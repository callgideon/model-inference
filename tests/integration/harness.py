"""Task-local service lifecycle, readiness and fault injection for E2.

Everything here is scoped to one namespace: compose project `infrx-e2`, containers
`infrx-e2-<service>`, host ports inside E's 55500-55599 range, PostgreSQL database
`infrx_e2`, Valkey key prefix `infrx_e2:`, object prefix `test/e2/`. `INFRX_E2_NAMESPACE`
selects another namespace, which moves every one of those names and the whole port block
together (`NAMESPACES`); unset, every value is E2's own. Every destructive
helper refuses a container it did not create, because other sessions run containers on
this host and a teardown that guesses is a teardown that deletes someone else's work.

Nothing in this module reads a production credential or an AWS/Supabase endpoint; the
local test credentials are literals in `compose.yaml` and repeated here.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The override exists for one caller: `mutants.py`, which runs this suite from a temporary
# copy of the owned trees and still needs the real repository for the migrations and the
# contracts package. Nothing else sets it.
REPO_ROOT = Path(os.environ["INFRX_E2_REPO_ROOT"]) if os.environ.get("INFRX_E2_REPO_ROOT") \
    else HERE.parents[1]
COMPOSE_FILE = HERE / "compose.yaml"
API_ROOT = REPO_ROOT / "apps" / "infrx-api"
MIGRATIONS_DIR = REPO_ROOT / "apps" / "app" / "supabase" / "migrations"

# 08 §8 / R48: E2's layout inside E's compose range. `tasklocal.local_services(<ns>)` is
# the authority; test_harness.py asserts every namespace against it rather than trusting
# the copy.
E2_PORTS = {
    "postgres": 55532,
    "valkey": 55579,
    "clickhouse_http": 55523,
    "clickhouse_native": 55590,
    "s3": 55500,
    "fake_vllm": 55580,          # a host process, not a container (see fake_vllm.py)
}
# E3B phase 2: namespace -> offset of its block from E2's. A namespace moves the whole
# layout, so two checkouts can run the stack at once (e3b2: 56700-56799, E2's +1200; e4b: 56800-56899, +1300;
# e3c: 56900-56999, +1400 - tasklocal's `e3c` block, E3C WR-1).
NAMESPACES = {"e2": 0, "e3b2": 1200, "e4b": 1300, "e3c": 1400}
NAMESPACE = os.environ.get("INFRX_E2_NAMESPACE") or "e2"
if NAMESPACE not in NAMESPACES:
    raise ValueError(f"INFRX_E2_NAMESPACE={NAMESPACE!r}: expected one of {sorted(NAMESPACES)}")


def ports_for(namespace: str) -> dict[str, int]:
    return {service: port + NAMESPACES[namespace] for service, port in E2_PORTS.items()}


def range_for(namespace: str) -> range:
    return range(55500 + NAMESPACES[namespace], 55600 + NAMESPACES[namespace])


TASK = NAMESPACE
PROJECT = f"infrx-{NAMESPACE}"
PREFIX = f"{PROJECT}-"            # every container name starts with this
NETWORK = f"{PROJECT}_default"    # compose's default network for this project
PORT_RANGE = range_for(NAMESPACE)
PORTS = ports_for(NAMESPACE)

# Local test credentials. Fixed literals on purpose: an integration run must need no
# secret, so there is nothing to leak and nothing to forget to unset.
PG_USER, PG_PASSWORD = "postgres", "infrx-e2-local"
# r1 review R-a: the target database is `infrx_e2`, created with
# `CREATE DATABASE infrx_e2 TEMPLATE postgres OWNER postgres`, so it carries the image's
# `auth` schema, `auth.uid()` and the anon/authenticated/service_role roles AND matches
# D1's `current_database() like 'infrx\_%'` gate on the test clock - production is
# `postgres`, so that gate is what keeps a movable clock out of it. See
# `provision_database()` for the two things the copy needs.
PG_DATABASE = f"infrx_{NAMESPACE}"
PG_ADMIN_ROLE = "supabase_admin"     # the image's superuser; `postgres` is not one
PG_TEMPLATE_SOURCE = "postgres"
CH_USER, CH_PASSWORD, CH_DATABASE = "infrx_e2", "infrx-e2-local", "infrx_e2"
S3_ACCESS_KEY, S3_SECRET_KEY = "infrxe2minio", "infrx-e2-local-secret"
S3_BUCKET = PROJECT
OBJECT_PREFIX = f"test/{TASK}/"
VALKEY_PREFIX = f"infrx_{TASK}:"

SERVICES = ("postgres", "valkey", "clickhouse", "s3")


def pg_dsn(database: str = PG_DATABASE) -> str:
    return (f"postgresql://{PG_USER}:{PG_PASSWORD}@127.0.0.1:{PORTS['postgres']}/{database}"
            "?connect_timeout=5")


def valkey_url() -> str:
    return f"valkey://127.0.0.1:{PORTS['valkey']}/0"


def clickhouse_http() -> str:
    return f"http://127.0.0.1:{PORTS['clickhouse_http']}"


def s3_endpoint() -> str:
    return f"http://127.0.0.1:{PORTS['s3']}"


# Where `run.py` leaves what the layer-2 tests need to find the stack it provisioned:
# the seed and the fixture ids. Outside the repository on purpose - it is run state, not
# source, and it must never be committed or read by anything but this task.
# `INFRX_E2_STATE_FILE` is set by one caller, `mutants.py`, which gives each mutant's run a
# private TMPDIR (E3B phase 2) and still has to point it at the stack this run provisioned.
STATE_FILE = Path(os.environ["INFRX_E2_STATE_FILE"]) if os.environ.get("INFRX_E2_STATE_FILE") \
    else Path(os.environ.get("TMPDIR", "/tmp")) / f"{PROJECT}-state.json"


def save_state(state: dict) -> Path:
    import json
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str))
    return STATE_FILE


def load_state() -> dict | None:
    import json
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text())
    except json.JSONDecodeError:
        return None


def clear_state() -> None:
    STATE_FILE.unlink(missing_ok=True)


class HarnessError(RuntimeError):
    """A harness step failed. Never swallowed into a skip: a broken harness is a
    failure, not an absent test (04 §Test environments)."""


# --------------------------------------------------------------------- shelling out

def run(argv: list[str], *, check: bool = True, timeout: float = 300.0,
        capture: bool = True, env: dict | None = None, cwd: Path | None = None):
    """One place that shells out, so every command is visible in one traceback."""
    result = subprocess.run(argv, capture_output=capture, text=True, timeout=timeout,
                            env={**os.environ, **(env or {})} if env else None,
                            cwd=str(cwd) if cwd else None)
    if check and result.returncode != 0:
        raise HarnessError(f"{' '.join(argv)} exited {result.returncode}\n"
                           f"{(result.stdout or '')[-2000:]}\n{(result.stderr or '')[-2000:]}")
    return result


def docker_available() -> tuple[bool, str]:
    """(usable, why not). Pulls are a separate question: see `images_present`."""
    if shutil.which("docker") is None:
        return False, "docker is not on PATH"
    probe = run(["docker", "version", "--format", "{{.Server.Version}}"], check=False,
                timeout=30)
    if probe.returncode != 0:
        return False, f"docker daemon unreachable: {(probe.stderr or '').strip()[:200]}"
    return True, (probe.stdout or "").strip()


def compose_images() -> dict[str, str]:
    """service -> image reference, read out of compose.yaml with no yaml dependency.

    Only used by guards and by evidence; compose itself reads the file.
    """
    images: dict[str, str] = {}
    service = None
    for line in COMPOSE_FILE.read_text().splitlines():
        head = re.match(r"^  ([a-z0-9][a-z0-9_-]*):\s*$", line)
        if head:
            service = head.group(1)
            continue
        image = re.match(r"^    image:\s*(\S+)", line)
        if image and service:
            images[service] = image.group(1)
    return images


def images_present() -> list[str]:
    """The pinned references that are NOT in the local image store."""
    missing = []
    for reference in compose_images().values():
        if run(["docker", "image", "inspect", reference], check=False,
               timeout=60).returncode != 0:
            missing.append(reference)
    return missing


# --------------------------------------------------------------------- namespace guard
#
# r1 review B1: the compose PROJECT NAME is not ownership. Two checkouts of this repository
# run the same project name, so keying teardown on it alone means one run silently destroys
# another's live stack, and `down -v` happily deletes a hand-made volume that merely has the
# name compose would have used. Ownership here is the compose project label **plus**
# `ai.infrx.e2.checkout`, which compose.yaml stamps on every container, every volume and the
# network from `INFRX_E2_CHECKOUT` (this checkout's compose directory). Anything carrying one
# of our names, or our project label, without that label is FOREIGN: reported, never touched,
# and both provisioning and teardown refuse to start.

# Volumes compose creates for this project, and the network. Named here so a collision can
# be detected before `up` rather than discovered by `down -v`.
VOLUMES = ("postgres-data", "clickhouse-data", "s3-data")
PROJECT_VOLUMES = tuple(f"{PROJECT}_{name}" for name in VOLUMES)


# Our own label, set on every container, volume and the network by compose.yaml from
# `INFRX_E2_CHECKOUT`. Compose puts `project.working_dir` on containers but NOT on volumes,
# so ownership cannot be read off compose's own labels alone.
CHECKOUT_LABEL = "ai.infrx.e2.checkout"


def working_dir() -> str:
    """This checkout's identity: the compose file's directory. A second checkout of the same
    repository has a different one, which is the whole point (r1 B1).

    `INFRX_E2_CHECKOUT` overrides it for exactly one caller, `mutants.py`, whose temporary
    copy has to claim the identity of the checkout that provisioned the stack - otherwise the
    copy correctly sees that stack as foreign and every layer-2 mutant is skipped rather than
    killed. It is the same kind of seam as `INFRX_E2_REPO_ROOT`; nothing else sets it, and
    `compose_env()` round-trips whatever it returns, so the label and the check cannot drift.
    """
    return os.environ.get("INFRX_E2_CHECKOUT") or str(COMPOSE_FILE.parent)


def compose_env(namespace: str | None = None) -> dict[str, str]:
    """What every `docker compose` invocation must carry: the checkout label, and the
    namespace's project name and host ports. compose.yaml uses `:?` for each, so a missing
    value is a refusal rather than an unlabelled resource or a port of another namespace."""
    namespace = namespace or NAMESPACE
    return {"INFRX_E2_CHECKOUT": working_dir(), "INFRX_E2_PROJECT": f"infrx-{namespace}",
            **{f"INFRX_E2_PORT_{service.upper()}": str(port)
               for service, port in ports_for(namespace).items()}}


def _docker_ls(kind: str) -> list[str]:
    """Every container / volume / network name on the host."""
    argv = {"container": ["docker", "ps", "-a", "--format", "{{.Names}}"],
            "volume": ["docker", "volume", "ls", "--format", "{{.Name}}"],
            "network": ["docker", "network", "ls", "--format", "{{.Name}}"]}[kind]
    return [name for name in run(argv, timeout=60).stdout.split() if name]


def _labels(kind: str, name: str) -> dict[str, str]:
    probe = run(["docker", kind, "inspect", name, "--format", "{{json .Labels}}"]
                if kind != "container" else
                ["docker", "inspect", name, "--format", "{{json .Config.Labels}}"],
                check=False, timeout=60)
    if probe.returncode != 0:
        return {}
    import json
    try:
        return json.loads(probe.stdout.strip() or "null") or {}
    except json.JSONDecodeError:
        return {}


def _is_ours(labels: dict[str, str]) -> bool:
    """Ours = compose's project label AND this checkout's own label. Both, because the
    project label is every checkout's and the checkout label is what distinguishes them."""
    return (labels.get("com.docker.compose.project") == PROJECT
            and labels.get(CHECKOUT_LABEL) == working_dir())


def _candidates(kind: str) -> list[str]:
    """Names that either look like ours or claim our project label - the set that must be
    classified before anything is created or removed."""
    names = _docker_ls(kind)
    looks_like = {"container": lambda n: n.startswith(PREFIX),
                  "volume": lambda n: n.startswith(f"{PROJECT}_"),
                  "network": lambda n: n == NETWORK or n.startswith(f"{PROJECT}_")}[kind]
    return sorted(name for name in names
                  if looks_like(name)
                  or _labels(kind, name).get("com.docker.compose.project") == PROJECT)


def owned(kind: str = "container") -> list[str]:
    """Resources of `kind` this checkout's project created, by label AND working_dir."""
    return [name for name in _candidates(kind) if _is_ours(_labels(kind, name))]


def owned_containers() -> list[str]:
    return owned("container")


def foreign(kind: str) -> list[dict]:
    """Resources carrying one of our names, or our project label, that this checkout did
    NOT create: another checkout's live stack, or a hand-made same-named volume.

    Reported with the reason, because "refuse" is only useful if it says what to do.
    """
    found = []
    for name in _candidates(kind):
        labels = _labels(kind, name)
        if _is_ours(labels):
            continue
        other = labels.get(CHECKOUT_LABEL)
        found.append({"kind": kind, "name": name,
                      "project": labels.get("com.docker.compose.project"),
                      "checkout": other,
                      "why": (f"another checkout's run ({other})" if other
                              else f"no {PROJECT} checkout label: not created by this harness")})
    return found


def foreign_resources() -> list[dict]:
    return [item for kind in ("container", "volume", "network") for item in foreign(kind)]


def foreign_containers() -> list[str]:
    """Kept as the container-only view the suite and the evidence use."""
    return [item["name"] for item in foreign("container")]


def busy_ports() -> dict[str, int]:
    """The task-local ports something else is already listening on.

    Checked before provisioning: a port in use is the one failure that otherwise shows up as
    an unexplained exit status from whatever tried to bind it.
    """
    import socket
    busy = {}
    for service, port in PORTS.items():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.5)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                busy[service] = port
    return busy


def assert_ours(container: str) -> str:
    """Refuse to touch anything outside the namespace. Three gates: the name must carry our
    prefix, docker must attribute it to our compose project, and the project's working_dir
    must be THIS checkout's (r1 B1 - the project name alone is another checkout's too)."""
    if not container.startswith(PREFIX):
        raise HarnessError(f"refusing to touch {container!r}: not in the {PREFIX!r} namespace")
    labels = _labels("container", container)
    if labels.get("com.docker.compose.project") != PROJECT:
        raise HarnessError(f"refusing to touch {container!r}: not created by project {PROJECT!r}")
    if not _is_ours(labels):
        raise HarnessError(
            f"refusing to touch {container!r}: it belongs to another checkout "
            f"({labels.get(CHECKOUT_LABEL)!r}, ours is {working_dir()!r})")
    return container


def container_of(service: str) -> str:
    if service not in SERVICES:
        raise HarnessError(f"unknown service {service!r}; expected one of {SERVICES}")
    return f"{PREFIX}{service}"


# --------------------------------------------------------------------- compose lifecycle

def compose(*args: str, check: bool = True, timeout: float = 600.0):
    return run(["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE_FILE), *args],
               check=check, timeout=timeout, env=compose_env())


def assert_nothing_foreign() -> None:
    """The gate in front of every create and every remove."""
    found = foreign_resources()
    if found:
        raise HarnessError(
            "refusing to provision or tear down: these carry this project's names or label "
            "but were not created by this checkout - stop them yourself, nothing here will "
            f"touch them: {found}")


def up(*, pull: bool = False, retry_bind: bool = True) -> None:
    """A FRESH stack: whatever THIS checkout left is removed first, volumes included.

    r1 review R-c: 55500-55599 is inside the kernel ephemeral range (32768-60999), so a
    transient bind failure is possible. One retry after a short wait, then the caller reports
    PENDING - never a pass.
    """
    assert_nothing_foreign()
    down()
    if pull:
        compose("pull", "--quiet", timeout=1800.0)
    try:
        compose("up", "-d", "--no-build", timeout=900.0)
    except HarnessError as first:
        if not retry_bind or not _looks_like_a_bind_failure(str(first)):
            raise
        compose("down", "-v", "--remove-orphans", check=False, timeout=300.0)
        time.sleep(5.0)
        try:
            compose("up", "-d", "--no-build", timeout=900.0)
        except HarnessError as second:
            raise HarnessError(
                f"port bind failed twice (ephemeral-range collision, R-c): {second}") from first


def _looks_like_a_bind_failure(message: str) -> bool:
    lowered = message.lower()
    return any(needle in lowered for needle in
               ("address already in use", "bind: ", "port is already allocated",
                "failed to set up container networking"))


def down() -> list[str]:
    """Remove exactly what THIS checkout created, and prove it afterwards by id.

    Every id is recorded before the removal and re-checked after, so "removes only what it
    created" is a measurement rather than a property of the command line.
    """
    assert_nothing_foreign()
    recorded = {kind: {name: _resource_id(kind, name) for name in owned(kind)}
                for kind in ("container", "volume", "network")}
    compose("down", "-v", "--remove-orphans", check=False, timeout=300.0)
    survivors = {kind: owned(kind) for kind in ("container", "volume", "network")}
    still = {kind: names for kind, names in survivors.items() if names}
    if still:
        raise HarnessError(f"teardown left {still} behind - disposable means gone")
    return sorted(recorded["container"])


def _resource_id(kind: str, name: str) -> str:
    probe = run(["docker", kind, "inspect", name, "--format", "{{.Id}}"]
                if kind != "container" else
                ["docker", "inspect", name, "--format", "{{.Id}}"],
                check=False, timeout=60)
    return probe.stdout.strip() if probe.returncode == 0 else ""


# --------------------------------------------------------------------- database (R-a)

def provision_database(database: str = PG_DATABASE) -> dict:
    """`CREATE DATABASE infrx_e2 TEMPLATE postgres OWNER postgres` (r1 review R-a).

    Two things the copy needs, both measured on the pinned image:

    * the template's background workers must be gone first - `pg_net 0.20.4` and the
      `pg_cron scheduler` hold permanent sessions on `postgres`, and PostgreSQL refuses to
      copy a database anything else is connected to;
    * `pg_terminate_backend` on those needs a real superuser, and in this image `postgres`
      is **not** one (`usesuper` false); `supabase_admin` is. It has no TCP password here, so
      the statement goes through `docker exec`, which is a local socket connection.

    `OWNER postgres` matters: `public` is owned by `pg_database_owner`, so without it the
    copy's owner would be `supabase_admin` and `postgres` could not create the console's
    tables - the migration would fail with `permission denied for schema public`.

    Each statement is its own `-c`: `DROP DATABASE` and `CREATE DATABASE` cannot run inside a
    transaction block, and psql wraps a multi-statement `-c` in one. The workers reconnect
    within seconds, so a lost race is retried rather than reported as a refusal.

    `database` defaults to E2's; E3B phase 2 builds its JobStore template the same way.
    """
    container = assert_ours(container_of("postgres"))
    terminate = (f"select pg_terminate_backend(pid) from pg_stat_activity "
                 f"where datname = '{PG_TEMPLATE_SOURCE}' and pid <> pg_backend_pid()")
    attempts = []
    for attempt in range(4):
        result = run(["docker", "exec", "-i", container, "psql", "-U", PG_ADMIN_ROLE,
                      "-d", "template1", "-v", "ON_ERROR_STOP=1",
                      "-c", f"drop database if exists {database}",
                      "-c", terminate,
                      "-c", (f"create database {database} "
                             f"template {PG_TEMPLATE_SOURCE} owner {PG_USER}")],
                     check=False, timeout=300.0)
        if result.returncode == 0:
            return {"database": database, "template": PG_TEMPLATE_SOURCE,
                    "created_by": PG_ADMIN_ROLE, "owner": PG_USER, "attempts": attempt + 1,
                    "statements": ["drop database if exists", "pg_terminate_backend",
                                   "create database … template … owner"]}
        attempts.append(((result.stderr or result.stdout or "").strip().splitlines() or [""])[-1])
        time.sleep(1.0)
    raise HarnessError(
        "the pinned image refused the template copy R-a requires; not working around it "
        f"by weakening D1's clock gate. Attempts: {attempts}")


# --------------------------------------------------------------------- readiness

def wait_postgres(timeout: float = 180.0, database: str | None = None) -> str:
    """Readiness is checked on the TEMPLATE database by default: `infrx_e2` does not exist
    until `provision_database()` has run, and waiting for a database nobody has created yet
    is a 180-second way to say "not ready"."""
    import psycopg
    last = ""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with psycopg.connect(pg_dsn(database or PG_TEMPLATE_SOURCE), connect_timeout=3) as conn:
                return conn.execute("select version()").fetchone()[0]
        except Exception as exc:                   # noqa: BLE001 - any client error retries
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(1.0)
    raise HarnessError(f"postgres not ready within {timeout}s: {last}")


def wait_valkey(timeout: float = 60.0) -> str:
    import valkey
    last = ""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            client = valkey.Valkey.from_url(valkey_url(), socket_timeout=2)
            client.ping()
            return str(client.info("server").get("valkey_version")
                       or client.info("server").get("redis_version"))
        except Exception as exc:                   # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
            time.sleep(0.5)
    raise HarnessError(f"valkey not ready within {timeout}s: {last}")


def wait_clickhouse(timeout: float = 120.0) -> str:
    import httpx
    last = ""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            ping = httpx.get(f"{clickhouse_http()}/ping", timeout=3.0)
            if ping.status_code == 200:
                version = httpx.post(clickhouse_http(), timeout=5.0,
                                     content="select version()",
                                     auth=(CH_USER, CH_PASSWORD))
                if version.status_code == 200:
                    return version.text.strip()
                last = f"query {version.status_code}: {version.text[:120]}"
            else:
                last = f"ping {ping.status_code}"
        except Exception as exc:                   # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(0.5)
    raise HarnessError(f"clickhouse not ready within {timeout}s: {last}")


def wait_s3(timeout: float = 90.0) -> str:
    import httpx
    last = ""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            live = httpx.get(f"{s3_endpoint()}/minio/health/live", timeout=3.0)
            if live.status_code == 200:
                return live.headers.get("server", "minio")
        except Exception as exc:                   # noqa: BLE001
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(0.5)
    raise HarnessError(f"s3 not ready within {timeout}s: {last}")


def wait_all(timeout: float = 240.0) -> dict[str, str]:
    """Versions, so the evidence records what actually answered rather than what was
    requested. PostgreSQL is first and slowest: the Supabase image runs its own
    init-scripts and migrations before it accepts a connection."""
    return {"postgres": wait_postgres(timeout), "valkey": wait_valkey(),
            "clickhouse": wait_clickhouse(), "s3": wait_s3()}


# --------------------------------------------------------------------- clients

def s3_client():
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3", endpoint_url=s3_endpoint(), aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY, region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                      retries={"max_attempts": 1}, connect_timeout=5, read_timeout=10))


def valkey_client(**kw):
    import valkey
    return valkey.Valkey.from_url(valkey_url(), socket_timeout=kw.pop("socket_timeout", 5), **kw)


def clickhouse_client():
    import clickhouse_connect
    return clickhouse_connect.get_client(
        host="127.0.0.1", port=PORTS["clickhouse_http"], username=CH_USER,
        password=CH_PASSWORD, database=CH_DATABASE, connect_timeout=5, send_receive_timeout=30)


# --------------------------------------------------------------------- fault injection
#
# 04 layer 2 needs three kinds of fault and they are genuinely different: a process that
# dies (the peer is gone), a network that stops carrying packets (the peer is silent, which
# is the one that produces hangs rather than errors), and time moving (expiry and lease
# decisions). Each helper is namespace-checked and each has an undo.

@dataclass
class Fault:
    """What was injected and how to undo it, so cleanup is data rather than discipline."""

    what: str
    undo: object | None = None          # callable or None when the fault is one-way

    def revert(self) -> None:
        if callable(self.undo):
            self.undo()


def signal_container(service: str, sig: str = "SIGKILL") -> Fault:
    """Process loss inside a container. `SIGKILL` is the one OPS-RECOVER cares about:
    no flush, no graceful shutdown, no chance to write a summary."""
    name = assert_ours(container_of(service))
    run(["docker", "kill", "-s", sig, name], timeout=60)
    return Fault(f"{sig} {name}", undo=lambda: compose("up", "-d", "--no-build", timeout=300.0))


def pause_container(service: str) -> Fault:
    """A network drop that HANGS rather than refuses: the container is frozen, so the
    kernel still accepts the connection and nothing ever answers. This is the shape that
    finds missing timeouts; `disconnect_container` finds missing error handling."""
    name = assert_ours(container_of(service))
    run(["docker", "pause", name], timeout=60)
    return Fault(f"pause {name}", undo=lambda: run(["docker", "unpause", name],
                                                   check=False, timeout=60))


def disconnect_container(service: str) -> Fault:
    """A hard network partition: the endpoint leaves the project network."""
    name = assert_ours(container_of(service))
    run(["docker", "network", "disconnect", NETWORK, name], timeout=60)
    return Fault(f"disconnect {name}",
                 undo=lambda: run(["docker", "network", "connect", NETWORK, name],
                                  check=False, timeout=60))


class Faults:
    """Context manager that reverts everything it injected, in reverse order, even when
    the test body raises. Cleanup scoped to the namespace: every helper above checks."""

    def __init__(self) -> None:
        self.injected: list[Fault] = []

    def __enter__(self) -> "Faults":
        return self

    def add(self, fault: Fault) -> Fault:
        self.injected.append(fault)
        return fault

    def kill_container(self, service: str, sig: str = "SIGKILL") -> Fault:
        return self.add(signal_container(service, sig))

    def pause(self, service: str) -> Fault:
        return self.add(pause_container(service))

    def disconnect(self, service: str) -> Fault:
        return self.add(disconnect_container(service))

    def __exit__(self, *exc) -> None:
        errors = []
        for fault in reversed(self.injected):
            try:
                fault.revert()
            except Exception as problem:            # noqa: BLE001 - report all, raise once
                errors.append(f"{fault.what}: {problem}")
        self.injected.clear()
        if errors:
            raise HarnessError("fault cleanup failed: " + "; ".join(errors))


# --------------------------------------------------------------------- python path

def api_on_path() -> None:
    """`infrx.contracts` is the shared contract package; the integration suite consumes
    it exactly as a track does, from the pinned environment."""
    if str(API_ROOT) not in sys.path:
        sys.path.insert(0, str(API_ROOT))
