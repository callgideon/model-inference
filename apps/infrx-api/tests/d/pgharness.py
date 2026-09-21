"""A disposable task-local PostgreSQL for D1's Layer-2 tests (08 §8).

Container `infrx-d1-postgres` on host port 55432, image pinned by digest, removed at
interpreter exit. Nothing here touches a container this module did not create, and
nothing points at Supabase or AWS.

Why a plain `postgres` image plus `infrx/state/supabase_shim.sql` rather than a
`supabase/postgres` image: the shim is the whole of what `0001_init.sql` depends on
(the `auth` schema, `auth.uid()`, three roles and - crucially - Supabase's default
ALL-PRIVILEGES grants on `public`), it is a dozen lines, and it is auditable in the
diff. A `supabase/postgres` pull is multiple gigabytes this host does not have, and
it would still need `request.jwt.claims` set by hand to exercise RLS. The shim's
known differences from production are listed in its header; the one that matters is
covered, because without the default-privileges block the role matrix would pass
vacuously (contracts README, "four things the fakes cannot tell you", note 1).
"""
from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import time

from infrx.contracts.tasklocal import local_services

SERVICE = local_services("d1")["postgres"]
PORT = SERVICE.host_port                           # 55432
DATABASE = SERVICE.database                        # infrx_d1
PASSWORD = "infrx-d1-local"

# postgres:16.14-bookworm, the digest present on this host. Pinned so a rerun cannot
# silently move to another server version (08 §8: pin every service image by digest).
PLAIN_IMAGE = ("postgres@sha256:"
               "33f923b05f64ca54ac4401c01126a6b92afe839a0aa0a52bc5aeb5cc958e5f20")
# The real thing E2 provisions (tag 17.6.1.173), which stage review S2 will require:
# `INFRX_D1_IMAGE=supabase make api-test` runs this suite against it with NO shim, in
# D1's own container on D1's own port. The digest is E2's, read from its harness.
SUPABASE_IMAGE = ("supabase/postgres@sha256:"
                  "7768d0d1d377250b718a9ad07f4661d008ebe6c96ecbbc4c08f3c5e53553e8fd")

#: Which image this run uses, and therefore whether the shim is needed at all.
ON_SUPABASE = os.environ.get("INFRX_D1_IMAGE", "").lower() in ("supabase", "real")
IMAGE = SUPABASE_IMAGE if ON_SUPABASE else PLAIN_IMAGE
NEEDS_SHIM = not ON_SUPABASE
CONTAINER = f"{SERVICE.container}-supabase" if ON_SUPABASE else SERVICE.container

_started = False


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(("docker", *args), capture_output=True, text=True, check=check)


def unavailable() -> str | None:
    """Why this suite cannot run, or None. A missing Docker is a skip, not a pass."""
    if shutil.which("docker") is None:
        return "docker is not installed"
    probe = _docker("info", "--format", "{{.ServerVersion}}", check=False)
    if probe.returncode != 0:
        return f"docker is not usable: {probe.stderr.strip().splitlines()[-1:]}"
    try:
        import psycopg                              # noqa: F401
    except ImportError:                             # pragma: no cover - env without extras
        return "psycopg is not installed (uv sync --all-extras)"
    return None


def ensure() -> None:
    """Start the container if it is not already up, and remove it at exit."""
    global _started
    if _started:
        return
    state = _docker("inspect", "-f", "{{.State.Running}}", CONTAINER, check=False)
    if state.returncode != 0:
        # The Supabase image initialises its own roles (`supabase_admin` and the rest)
        # and refuses to start if `POSTGRES_USER`/`POSTGRES_DB` are forced, so it gets
        # the password alone - the same environment E2's compose file uses.
        env = ["-e", f"POSTGRES_PASSWORD={PASSWORD}"]
        if not ON_SUPABASE:
            env += ["-e", "POSTGRES_USER=postgres", "-e", "POSTGRES_DB=postgres"]
        run = _docker("run", "-d", "--name", CONTAINER, "-p", f"{PORT}:5432",
                      *env, IMAGE, check=False)
        if run.returncode != 0:
            raise RuntimeError(f"could not start {CONTAINER}: {run.stderr.strip()}")
    elif state.stdout.strip() != "true":
        _docker("start", CONTAINER)
    atexit.register(remove)
    _started = True
    _wait_ready()


def _wait_ready(timeout_s: float = 60.0) -> None:
    """Connect from the host, not `pg_isready` in the container: the entrypoint runs a
    temporary server on the unix socket while it initialises the cluster, so
    `pg_isready` says yes and the next TCP connection is closed under us."""
    import psycopg
    deadline = time.monotonic() + timeout_s
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(dsn("postgres"), connect_timeout=3) as probe:
                probe.execute("select 1")
            return
        except psycopg.Error as not_yet:
            last = not_yet
            time.sleep(0.5)
    raise RuntimeError(f"{CONTAINER} did not become ready within {timeout_s}s: {last}")


def remove() -> None:
    _docker("rm", "-f", "-v", CONTAINER, check=False)


def dsn(database: str = DATABASE) -> str:
    return (f"postgresql://postgres:{PASSWORD}@127.0.0.1:{PORT}/{database}"
            "?connect_timeout=10")


def connect(database: str = DATABASE, *, autocommit: bool = True):
    import psycopg
    return psycopg.connect(dsn(database), autocommit=autocommit)


def recreate(database: str) -> None:
    """A fresh database for this task.

    On the real Supabase image it is created FROM TEMPLATE postgres, because that is the
    database whose entrypoint created `auth.users`, `auth.uid()` and the three roles;
    a virgin database would need the shim after all. The name still has to match the
    clock gate (`infrx_<task>`), which is why E2 does the same thing for `infrx_e2`.
    """
    if not ON_SUPABASE:
        with connect("postgres") as admin:
            admin.execute(f'drop database if exists "{database}" with (force)')
            admin.execute(f'create database "{database}"')
        return
    # On the real image the task database is a COPY of `postgres`, because that is where
    # the entrypoint created `auth.users`, `auth.uid()` and - the part that matters most -
    # the `alter default privileges` entries that hand `anon`/`authenticated` ALL on
    # everything in `public`. A virgin database would silently lack them, which is the
    # one difference that would make the whole role matrix vacuous.
    #
    # Two things make it awkward, and both are measured facts about this image rather
    # than choices: `create database … template postgres` refuses while anything is
    # connected to the template, and Supabase's `postgres` role is NOT a superuser, so it
    # cannot close pg_cron's launcher (which runs as `supabase_admin`). The work is
    # therefore done through the container's own unix socket as `supabase_admin`, which
    # needs no password locally. It is this task's own container.
    # One `-c` per statement: psql wraps a multi-statement `-c` in a transaction block,
    # and CREATE/DROP DATABASE cannot run in one. The terminate-and-create pair is
    # retried, because pg_cron and pg_net reconnect to `postgres` within milliseconds and
    # the copy refuses while they are attached.
    _sb("template1", f'drop database if exists "{database}" with (force)')
    last = ""
    for _attempt in range(5):
        _sb("template1", "select pg_terminate_backend(pid) from pg_stat_activity "
                         "where datname = 'postgres' and pid <> pg_backend_pid()")
        done = _sb("template1",
                   f'create database "{database}" template postgres owner postgres',
                   check=False)
        if done.returncode == 0:
            return
        last = done.stderr.strip()[-300:]
        time.sleep(0.3)
    raise AssertionError(f"could not copy the postgres template into {database}: {last}")


def _sb(database: str, statement: str, *, check: bool = True):
    """A statement as `supabase_admin` over the container's unix socket.

    Supabase's `postgres` role is not a superuser on this image (measured), so the
    database copy has to be done by one - and locally it needs no password.
    """
    done = _docker("exec", CONTAINER, "psql", "-U", "supabase_admin", "-d", database,
                   "-v", "ON_ERROR_STOP=1", "-c", statement, check=False)
    if check and done.returncode != 0:
        raise AssertionError(f"supabase_admin could not run `{statement[:50]}…`: "
                             f"{done.stderr.strip()[-300:]}")
    return done


def apply(database: str, files: tuple[tuple[str, str], ...]) -> None:
    """Run each (label, sql) as one implicit transaction, in order."""
    import psycopg
    with connect(database) as conn:
        for label, sql in files:
            try:
                conn.execute(sql)
            except psycopg.Error as failed:          # pragma: no cover - a broken migration
                raise AssertionError(f"{label} failed to apply: {failed}") from failed
