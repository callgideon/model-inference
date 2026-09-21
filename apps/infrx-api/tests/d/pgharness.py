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
import shutil
import subprocess
import time

from infrx.contracts.tasklocal import local_services

SERVICE = local_services("d1")["postgres"]
CONTAINER = SERVICE.container                       # infrx-d1-postgres
PORT = SERVICE.host_port                           # 55432
DATABASE = SERVICE.database                        # infrx_d1
PASSWORD = "infrx-d1-local"

# postgres:16.14-bookworm, the digest present on this host. Pinned so a rerun cannot
# silently move to another server version (08 §8: pin every service image by digest).
IMAGE = ("postgres@sha256:"
         "33f923b05f64ca54ac4401c01126a6b92afe839a0aa0a52bc5aeb5cc958e5f20")

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
        run = _docker("run", "-d", "--name", CONTAINER, "-p", f"{PORT}:5432",
                      "-e", f"POSTGRES_PASSWORD={PASSWORD}", "-e", "POSTGRES_USER=postgres",
                      "-e", "POSTGRES_DB=postgres", IMAGE, check=False)
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
    with connect("postgres") as admin:
        admin.execute(f'drop database if exists "{database}" with (force)')
        admin.execute(f'create database "{database}"')


def apply(database: str, files: tuple[tuple[str, str], ...]) -> None:
    """Run each (label, sql) as one implicit transaction, in order."""
    import psycopg
    with connect(database) as conn:
        for label, sql in files:
            try:
                conn.execute(sql)
            except psycopg.Error as failed:          # pragma: no cover - a broken migration
                raise AssertionError(f"{label} failed to apply: {failed}") from failed
