"""Task-local service lifecycle, readiness and fault injection for E2.

Everything here is scoped to one namespace: compose project `infrx-e2`, containers
`infrx-e2-<service>`, host ports inside E's 55500-55599 range, ClickHouse database
`infrx_e2`, Valkey key prefix `infrx_e2:`, object prefix `test/e2/`. Every destructive
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

TASK = "e2"
PROJECT = "infrx-e2"
PREFIX = f"{PROJECT}-"            # every container name starts with this
NETWORK = f"{PROJECT}_default"    # compose's default network for this project

# 08 §8 / R48: E's compose range. `tasklocal.local_services("e2")` is the authority;
# test_harness.py asserts these against it rather than trusting the copy.
PORT_RANGE = range(55500, 55600)
PORTS = {
    "postgres": 55532,
    "valkey": 55579,
    "clickhouse_http": 55523,
    "clickhouse_native": 55590,
    "s3": 55500,
    "fake_vllm": 55580,          # a host process, not a container (see fake_vllm.py)
}

# Local test credentials. Fixed literals on purpose: an integration run must need no
# secret, so there is nothing to leak and nothing to forget to unset.
PG_USER, PG_PASSWORD = "postgres", "infrx-e2-local"
# The Supabase image builds its `auth` schema, `auth.uid()` and the anon/authenticated/
# service_role roles in the `postgres` database only; see README.md "Why the database is
# `postgres`". Isolation is the container, the port and the disposable volume.
PG_DATABASE = "postgres"
CH_USER, CH_PASSWORD, CH_DATABASE = "infrx_e2", "infrx-e2-local", "infrx_e2"
S3_ACCESS_KEY, S3_SECRET_KEY = "infrxe2minio", "infrx-e2-local-secret"
S3_BUCKET = "infrx-e2"
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
STATE_FILE = Path(os.environ.get("TMPDIR", "/tmp")) / f"{PROJECT}-state.json"


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

def owned_containers() -> list[str]:
    """Containers docker attributes to THIS compose project, nothing else."""
    result = run(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={PROJECT}",
                  "--format", "{{.Names}}"], timeout=60)
    return [name for name in result.stdout.split() if name]


def foreign_containers() -> list[str]:
    """Containers whose name starts with our prefix but which the project does not own.

    A leftover from an interrupted run of *this* task still carries the project label, so
    anything here is someone else's naming collision and must be reported, never removed.
    """
    result = run(["docker", "ps", "-a", "--format", "{{.Names}}"], timeout=60)
    ours = set(owned_containers())
    return sorted(name for name in result.stdout.split()
                  if name.startswith(PREFIX) and name not in ours)


def assert_ours(container: str) -> str:
    """Refuse to touch anything outside the namespace. Two gates, not one: the name
    must carry our prefix AND docker must attribute it to our compose project."""
    if not container.startswith(PREFIX):
        raise HarnessError(f"refusing to touch {container!r}: not in the {PREFIX!r} namespace")
    if container not in owned_containers():
        raise HarnessError(f"refusing to touch {container!r}: not created by project {PROJECT!r}")
    return container


def container_of(service: str) -> str:
    if service not in SERVICES:
        raise HarnessError(f"unknown service {service!r}; expected one of {SERVICES}")
    return f"{PREFIX}{service}"


# --------------------------------------------------------------------- compose lifecycle

def compose(*args: str, check: bool = True, timeout: float = 600.0):
    return run(["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE_FILE), *args],
               check=check, timeout=timeout)


def up(*, pull: bool = False) -> None:
    """A FRESH stack: whatever a previous run left is removed first, volumes included."""
    down()
    if pull:
        compose("pull", "--quiet", timeout=1800.0)
    compose("up", "-d", "--no-build", timeout=900.0)


def down() -> list[str]:
    """Remove exactly what this project created. Returns the names it removed.

    `docker compose down` is already scoped by the project label; the explicit check
    afterwards is what turns "scoped by convention" into evidence.
    """
    before = owned_containers()
    compose("down", "-v", "--remove-orphans", check=False, timeout=300.0)
    left = owned_containers()
    if left:
        raise HarnessError(f"teardown left {left} behind")
    volumes = run(["docker", "volume", "ls", "--filter",
                   f"label=com.docker.compose.project={PROJECT}", "--format", "{{.Name}}"],
                  timeout=60).stdout.split()
    if volumes:
        raise HarnessError(f"teardown left volumes behind: {volumes} - disposable means gone")
    return before


# --------------------------------------------------------------------- readiness

def wait_postgres(timeout: float = 180.0) -> str:
    import psycopg
    last = ""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            with psycopg.connect(pg_dsn(), connect_timeout=3) as conn:
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
