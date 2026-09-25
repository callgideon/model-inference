"""A disposable task-local PostgreSQL for D1's Layer-2 tests (08 §8).

Container `infrx-d1-postgres` on host port 55432, image pinned by digest, removed at
interpreter exit. Nothing here touches a container this module did not create, and
nothing points at Supabase or AWS.

**Ownership before adoption (E2R item 1, audit finding A11).** The container name and the
host port are fixed by 08 §8, so every checkout of this repository wants the same two. The
original code read `docker inspect` and, if something answered, used it - then removed it at
exit. Two checkouts running `tests/d` therefore shared, restarted and finally deleted each
other's database. Three things now stand in the way, in this order:

1. a host-wide **lock** on the shared port (`$TMPDIR/infrx-d1-postgres-<port>.lock`, an
   exclusive `flock`). A second run is refused and **alters nothing**: it does not start,
   stop, remove or connect to the first run's container. The kernel drops the lock when the
   holder dies, so a crashed run cannot wedge the next one;
2. an **ownership label** (`ai.infrx.d1.checkout`, this checkout's root) that `docker run`
   stamps on the container. A container without this checkout's label is FOREIGN: reported
   with the owner it claims, and never used, started, stopped, removed or `docker exec`-ed;
3. **only what this run created** is tracked. A leftover carrying our own label is a crashed
   run of this checkout, so it is replaced rather than adopted - which is also the cleanup
   after a crash, since nothing else would ever remove it.

Known limit, the same one E2's harness records: the label is a path, so a process that sets
`INFRX_D1_CHECKOUT` to another checkout's root is mis-classified as the owner. That stops the
accident it exists for; a per-run random id in a state file is the upgrade if forging ever
matters, and the lock refuses such a process anyway while the first run is alive.

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
import fcntl
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from infrx.contracts.tasklocal import local_services

# `INFRX_D_TASK=d2` runs the same harness on D2's own port (55433, `infrx-d2-postgres`,
# database `infrx_d2`) when another checkout holds D1's shared one (08 §8 / R48).
SERVICE = local_services(os.environ.get("INFRX_D_TASK", "d1"))["postgres"]
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
#: True only when THIS run's `ensure()` created the container. Nothing else is removed.
_created = False

#: The label `docker run` stamps on the container, and the value it must carry to be ours.
CHECKOUT_LABEL = "ai.infrx.d1.checkout"


class ForeignContainer(RuntimeError):
    """A container carrying this harness's name was created by somebody else. Reported with
    the owner it claims; never used, started, stopped, removed or `docker exec`-ed."""


class HarnessBusy(RuntimeError):
    """Another run holds the lock on this task's port. Refused, and nothing was altered -
    a skip here would hide a real collision, and stopping the first run would be worse."""


def checkout() -> str:
    """This checkout's identity: the repository root that holds this file.

    `INFRX_D1_CHECKOUT` overrides it for one purpose - the ownership tests, which need to act
    as a second checkout without being one. Nothing in the suite sets it, and setting it to
    another checkout's root is the documented limit in the module docstring.
    """
    return os.environ.get("INFRX_D1_CHECKOUT") or str(Path(__file__).resolve().parents[4])


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(("docker", *args), capture_output=True, text=True, check=check)


# --------------------------------------------------------------------- ownership

def labels(container: str | None = None) -> dict | None:
    """The container's labels, or None when there is no such container."""
    probe = _docker("inspect", "-f", "{{json .Config.Labels}}", container or CONTAINER,
                    check=False)
    if probe.returncode != 0:
        return None
    try:
        return json.loads(probe.stdout.strip() or "null") or {}
    except json.JSONDecodeError:                     # pragma: no cover - docker changed format
        return {}


def foreign(container: str | None = None) -> dict | None:
    """Why this container is not ours, or None when it is ours or does not exist."""
    name = container or CONTAINER
    found = labels(name)
    if found is None or found.get(CHECKOUT_LABEL) == checkout():
        return None
    other = found.get(CHECKOUT_LABEL)
    return {"container": name, "checkout": other, "ours": checkout(),
            "why": (f"another checkout's run ({other})" if other else
                    f"no {CHECKOUT_LABEL} label: not created by this harness")}


def assert_ours(action: str, container: str | None = None) -> str:
    """The gate in front of every `docker` verb that would change the container."""
    name = container or CONTAINER
    stranger = foreign(name)
    if stranger is not None:
        raise ForeignContainer(f"refusing to {action} {name}: {stranger['why']} - stop it "
                               f"yourself, nothing here will touch it")
    if labels(name) is None:
        raise ForeignContainer(f"refusing to {action} {name}: it does not exist "
                               f"(call ensure() first)")
    return name


# --------------------------------------------------------------------- the port lock

_lock_fd: int | None = None


def lock_path() -> Path:
    """One lock per shared PORT, not per container name: the `-supabase` variant answers to a
    different name but binds the same port, so it has to serialise with the plain one too."""
    return Path(tempfile.gettempdir()) / f"{SERVICE.container}-{PORT}.lock"


def _acquire_lock() -> None:
    global _lock_fd
    if _lock_fd is not None:
        return
    path = lock_path()
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        holder = os.read(fd, 300).decode(errors="replace").strip() or "an unknown run"
        os.close(fd)
        raise HarnessBusy(
            f"another run holds {path} ({holder}); refusing to touch port {PORT} or "
            f"{CONTAINER}. Nothing was altered - wait for it, or run the two suites in "
            f"sequence (08 §8 gives this task ONE port)") from None
    os.ftruncate(fd, 0)
    os.write(fd, f"pid {os.getpid()} checkout {checkout()}".encode())
    _lock_fd = fd                    # never closed: the kernel releases it when we die


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
    """Provision THIS run's container, and remove it at exit.

    The order matters and is the whole of E2R item 1: take the port lock first (so a
    concurrent run is refused before anything is inspected, let alone changed), refuse a
    container this checkout did not create, then replace one it did - a leftover carrying our
    label is a crashed run of ours, and adopting it would mean trusting a database whose state
    nobody knows. `_created` is what `remove()` acts on, so only this run's container is ever
    deleted.
    """
    global _started, _created
    if _started:
        return
    _acquire_lock()
    stranger = foreign()
    if stranger is not None:
        raise ForeignContainer(
            f"refusing to use {CONTAINER}: {stranger['why']} - this run will not start, stop "
            f"or delete it. Remove it yourself, or let its owner finish")
    if labels() is not None:
        # Ours, from a run of this checkout that never got to remove it (a crash, a SIGKILL).
        # Replaced, not adopted, and this is the only thing that ever cleans it up.
        _docker("rm", "-f", "-v", CONTAINER, check=False)
    # The Supabase image initialises its own roles (`supabase_admin` and the rest) and
    # refuses to start if `POSTGRES_USER`/`POSTGRES_DB` are forced, so it gets the password
    # alone - the same environment E2's compose file uses.
    env = ["-e", f"POSTGRES_PASSWORD={PASSWORD}"]
    if not ON_SUPABASE:
        env += ["-e", "POSTGRES_USER=postgres", "-e", "POSTGRES_DB=postgres"]
    # D10: every lane port lies in the kernel's ephemeral range (32768-60999), so another
    # process's OUTGOING connection can hold ours as its local port for a while (measured:
    # 127.0.0.1:55442 <-> another lane's 55444, then TIME-WAIT). That is a transient refusal,
    # not a foreign container: the half-created container is ours, so it is removed and the
    # bind retried for a bounded time. ponytail: a fixed back-off; lane ports below 32768
    # (or `ip_local_reserved_ports`) remove the cause.
    for attempt in range(12):
        run = _docker("run", "-d", "--name", CONTAINER,
                      "--label", f"{CHECKOUT_LABEL}={checkout()}",
                      "-p", f"127.0.0.1:{PORT}:5432", *env, IMAGE, check=False)
        if run.returncode == 0 or "address already in use" not in run.stderr \
                or attempt == 11:
            break
        _docker("rm", "-f", "-v", CONTAINER, check=False)
        time.sleep(10)
    if run.returncode != 0:
        raise RuntimeError(f"could not start {CONTAINER}: {run.stderr.strip()}")
    _created = True
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
    """Remove the container THIS run created, and nothing else.

    Registered with `atexit`, so it also runs after a failed suite. It refuses rather than
    guesses if the name has meanwhile been taken by somebody else - an exception from an
    atexit callback is printed, which is the outcome to want here.
    """
    global _created, _started
    if not _created:
        return
    assert_ours("remove")
    _docker("rm", "-f", "-v", CONTAINER, check=False)
    _created = _started = False


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

    E2R: whichever path is taken, the server on this port must be the container this run
    created. `DROP DATABASE ... WITH (FORCE)` on somebody else's PostgreSQL is exactly the
    damage finding A11 describes, and the TCP port alone cannot tell you whose it is.
    """
    assert_ours("recreate a database in")
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
    done = _docker("exec", assert_ours("exec into"), "psql", "-U", "supabase_admin",
                   "-d", database, "-v", "ON_ERROR_STOP=1", "-c", statement, check=False)
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
