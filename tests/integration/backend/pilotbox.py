"""E3B phase 3: the pilot box's two processes on this stack - the mounted gateway and the
worker - each its own OS process, so a journey calls the gateway over real HTTP and a
restart is a real restart (rc03).

    python tests/integration/backend/pilotbox.py gateway     # the environment: stack.pilot_env
    python -m infrx.worker                                   #   + INFRX_E3B_* below

**gateway** - `infrx.gateway.app.create_app()` from the environment, served by uvicorn: the
five mounted routers and the stores built from settings (`pilot.adapters_from_env`: D5's
PgCatalogDirectory, D4's PgStreamStore, D2's PgJobStore on one pool). What no setting can
compose yet is INJECTED, each named here and in the evidence:

* (the object store is NOT injected: M1-L2's S3ObjectStore from `S3_MEDIA_BUCKET`, on E2's
  MinIO under a prefix of the box's own; before M1-L2 merged it was the in-memory store)
* `sb`: the Supabase REST transport, pointed at this stack's PostgREST ROOT. Hosted Supabase
  serves the same API under `/rest/v1` behind its gateway; PostgREST itself serves `/`.
* `index`: Q2's ValkeyScheduler on this namespace's Valkey, in a namespace of its own under
  `harness.VALKEY_PREFIX` (removed afterwards, like every E2 key) instead of the pilot's.
* the customer's media host (`video_url`): M's fetcher resolves `MEDIA_HOST` to a public
  address (so M's SSRF rules pass unchanged) and its transport serves M's synthetic clip.
* test instrumentation (review J1): the relay's store is wrapped by `Counted`, and the media
  host counts its fetches - one line per lookup, admission and fetch in `INFRX_E3B_CALLS`, so
  a journey can prove a replay or a key conflict fetched, staged and admitted nothing.

Nothing prepares a job in this process (PREP-WORKER retired the emulation that did): the
gateway admits, attaches (M's `PgAttachments`, durably) and relays the `prepare_dispatch`.

**worker** - ALWAYS a process of its own (review J2; M's pilot-media merge made it
possible for video), and since I2B-R4 the product's: `python -m infrx.worker`, composed from
the same environment by the pilot's own composition root - W3's `WorkerService` over W2's
loop and runner, W's `VllmEngine` on `UPSTREAM` (E2's fake vLLM), D's stores on one pool,
a `MediaPreparation` over the object store built from settings and the shared
`PROCESSING_CACHE_DIR`, W request 5's `CreditWork`, and its loopback `/readyz` on
`WORKER_HEALTH_PORT`, which `start` waits for. Since PREP-WORKER it also PREPARES every
job: its second pool claims the preparation lease, prepares the media, counts the prompt
with the engine's own `/tokenize` (E2's fake vLLM answers the count it reports as usage)
and queues the job. Nothing of it is emulated here any more. The one thing the box
chooses for it is the index namespace: the worker reads the pilot's (`infrx:sched:{pilot}`,
Q2's default), so the gateway's index is put there too, and `close` removes it like every
E2 key.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import harness                                          # noqa: E402

if importlib.util.find_spec("infrx") is None:
    harness.api_on_path()

PORT_ENV, INDEX_ENV, CALLS_ENV = (
    "INFRX_E3B_GATEWAY_PORT", "INFRX_E3B_INDEX_NAMESPACE", "INFRX_E3B_CALLS")
# Where `python -m infrx.worker` looks for candidates: Q2's ValkeyScheduler default.
PILOT_NAMESPACE = "infrx:sched:{pilot}"
MEDIA_HOST = "media.e3b3.example"
PUBLIC_ADDRESS = "93.184.216.34"          # what MEDIA_HOST resolves to (G2's own choice)
log = logging.getLogger("e3b3.pilotbox")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def free_port() -> int:
    """A loopback port nobody holds now, for the worker's readiness listener."""
    import socket
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]



def clip() -> bytes:
    """M's synthetic probe-valid container (tests/m/support.py), 10 s."""
    spec = importlib.util.spec_from_file_location(
        "e3b3_m_support", harness.API_ROOT / "tests" / "m" / "support.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.mp4(seconds=10.0)


def index(pilot):
    from valkey.asyncio import Valkey

    from infrx.scheduling.valkey import ValkeyScheduler
    return ValkeyScheduler(Valkey.from_url(pilot.valkey_url), utc_now, limits=pilot,
                           namespace=os.environ[INDEX_ENV])


# ------------------------------------------------------------------ the gateway process

def mark(kind: str) -> None:
    """One line per counted call (O_APPEND: one write per line, whole)."""
    with open(os.environ[CALLS_ENV], "a", encoding="utf-8") as calls:
        calls.write(kind + "\n")


class Counted:
    """The relay's store with its R91 lookups and its admissions counted (test instrumentation,
    review J1); every call is the store's own."""

    def __init__(self, store) -> None:
        self.store = store

    def __getattr__(self, name):
        return getattr(self.store, name)

    async def lookup(self, *args, **kw):
        mark("lookup")
        return await self.store.lookup(*args, **kw)

    async def admit_credit(self, *args, **kw):
        mark("admit")
        return await self.store.admit_credit(*args, **kw)

    async def admit(self, *args, **kw):
        mark("admit")
        return await self.store.admit(*args, **kw)


async def gateway() -> None:
    import httpx
    import uvicorn

    from infrx.config import from_env
    from infrx.gateway.app import create_app
    from infrx.media import fetch
    settings = from_env()
    token = settings.supabase_key
    sb = httpx.AsyncClient(base_url=settings.supabase_url, timeout=httpx.Timeout(5, connect=2),
                           headers={"apikey": token, "Authorization": f"Bearer {token}",
                                    "Content-Type": "application/json"})
    queue = index(settings.pilot)
    app = create_app(settings, sb=sb, index=queue)
    rt = app.state.runtime
    rt.relay.jobs = Counted(rt.relay.jobs)
    body = clip()

    def serve(request):
        mark("fetch")
        return httpx.Response(200, headers={"content-type": "video/mp4"},
                              stream=httpx.ByteStream(body))

    async def resolve(host):
        return [PUBLIC_ADDRESS] if host == MEDIA_HOST else []

    rt.media_store.fetcher = fetch.MediaFetcher(
        settings.pilot, allowed_mime=settings.allowed_video_mime, resolve=resolve,
        transport=httpx.MockTransport(serve))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=int(os.environ[PORT_ENV]),
                                           log_level="warning"))
    await server.serve()


# ------------------------------------------------------------------ the test's side

class PilotBox:
    """Both processes, started and stopped from a test. Each is its own process group, so a
    stop takes everything it spawned; a crashed run leaves nothing on a task-local port."""

    def __init__(self, env: dict[str, str], engine_url: str, workdir: Path,
                 port: int, namespace: str = PILOT_NAMESPACE) -> None:
        import stack
        inherited = {name: value for name, value in os.environ.items()
                     if name not in stack.AWS_UNSET}
        self.worker_port = free_port()
        # Both processes resolve `infrx` as the gateway always did (`api_on_path` only when
        # nothing else provides it): E's mutation runner's copy on PYTHONPATH first, the
        # checkout's package after it - never this process's module, which fake_vllm.py
        # may have imported from the real checkout, and never a package beside their cwd.
        path = os.pathsep.join(filter(None, (inherited.get("PYTHONPATH"), str(harness.API_ROOT))))
        self.env = {**inherited, **env, PORT_ENV: str(port), INDEX_ENV: namespace,
                    CALLS_ENV: str(workdir / "calls.log"), "UPSTREAM": engine_url,
                    "WORKER_HEALTH_PORT": str(self.worker_port), "PYTHONPATH": path,
                    "PYTHONUNBUFFERED": "1"}
        self.workdir, self.port, self.namespace = workdir, port, namespace
        self.processes: dict[str, subprocess.Popen] = {}
        self.starts = {"gateway": 0, "worker": 0}

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def command(self, role: str) -> tuple[list[str], str]:
        """What runs `role` (from the repository root, `infrx` on PYTHONPATH) and the
        readiness it answers on: the worker is I2B-R4's entry point."""
        if role == "worker":
            return ([sys.executable, "-m", "infrx.worker"],
                    f"http://127.0.0.1:{self.worker_port}/readyz")
        return [sys.executable, str(Path(__file__).resolve()), role], self.url + "/readyz"

    def start(self, role: str, timeout: float = 60.0) -> None:
        self.starts[role] += 1
        argv, ready = self.command(role)
        logfile = open(self.workdir / f"{role}-{self.starts[role]}.log", "wb")
        self.processes[role] = subprocess.Popen(
            argv, env=self.env, stdout=logfile, stderr=subprocess.STDOUT,
            start_new_session=True, cwd=str(harness.REPO_ROOT))
        logfile.close()
        self._wait_ready(role, ready, timeout)

    def _wait_ready(self, role: str, url: str, timeout: float) -> None:
        import httpx
        end, last = time.monotonic() + timeout, ""
        while time.monotonic() < end:
            if self.processes[role].poll() is not None:
                raise RuntimeError(f"the {role} exited {self.processes[role].returncode}: "
                                   f"{self.tail(role)}")
            try:
                answer = httpx.get(url, timeout=2.0)
                if answer.status_code == 200:
                    return
                last = f"{answer.status_code} {answer.text[:200]}"
            except httpx.HTTPError as exc:
                last = type(exc).__name__
            time.sleep(0.1)
        raise RuntimeError(f"the {role} was not ready within {timeout}s: {last} "
                           f"{self.tail(role)}")

    def tail(self, role: str, lines: int = 12) -> str:
        path = self.workdir / f"{role}-{self.starts[role]}.log"
        text = path.read_text(errors="replace") if path.exists() else ""
        return " | ".join(text.strip().splitlines()[-lines:])

    def stop(self, role: str, sig: int = signal.SIGTERM, timeout: float = 30.0) -> int | None:
        process = self.processes.pop(role, None)
        if process is None:
            return None
        for signum in (sig, signal.SIGKILL):
            if process.poll() is not None:
                break
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(os.getpgid(process.pid), signum)
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                continue
        return process.returncode

    def close(self) -> None:
        for role in list(self.processes):
            self.stop(role)
        client = harness.valkey_client()
        leftovers = list(client.scan_iter(f"{self.namespace}*"))
        if leftovers:
            client.delete(*leftovers)
        s3, prefix = harness.s3_client(), self.env["S3_MEDIA_PREFIX"]
        for item in s3.list_objects_v2(Bucket=harness.S3_BUCKET,
                                       Prefix=prefix).get("Contents", []):
            s3.delete_object(Bucket=harness.S3_BUCKET, Key=item["Key"])


@contextlib.contextmanager
def pilot_box(database: str, workdir: Path, rest_url: str, engine_url: str):
    """The gateway and the worker over `database`, until the block ends. The clone's test
    clock (frozen by the conformance rig) is handed back to the wall first: two processes
    and a database agree on "now" only as they do on the pilot box."""
    import psycopg

    import stack
    with psycopg.connect(harness.pg_dsn(database), autocommit=True) as conn:
        conn.execute("select infrx_test.unfreeze(), infrx_test.set_offset(0)")
    env = stack.pilot_env(database, workdir, rest_url)
    box = PilotBox(env, engine_url, workdir, stack.GATEWAY_PORT)
    try:
        box.start("worker")
        box.start("gateway")
        yield box
    finally:
        box.close()


# ------------------------------------------------------------------ a journey's reads

MODES = ("sync", "sse", "async")
FINISHED = ("succeeded", "failed", "cancelled")


class Journey:
    """Two provisioned tenants, their pilot box and E2's fake engine, with what a journey
    reads: HTTP through the mounted gateway, and the clone's rows as its owner (the money
    reads never go through the gateway they check)."""

    def __init__(self, world, box: PilotBox, engine) -> None:
        import httpx
        self.world, self.box, self.engine = world, box, engine
        self.http = httpx.Client(base_url=box.url, timeout=120.0)

    # --- the database ----------------------------------------------------------
    def db(self, sql: str, *args) -> list[tuple]:
        import psycopg
        with psycopg.connect(harness.pg_dsn(self.world.database), autocommit=True) as conn:
            return conn.execute(sql, args).fetchall()

    def one(self, sql: str, *args):
        rows = self.db(sql, *args)
        assert len(rows) == 1, (sql, rows)
        return rows[0]

    def wallet(self, tenant) -> tuple:
        """(ledger, reserved) of the tenant's CREDIT wallet."""
        return self.one("select ledger_total, reserved_total from infrx.credit_wallets "
                        "where wallet_id = %s", tenant.wallet.wallet_id)

    def usd(self, tenant) -> tuple:
        """The organization's legacy USD books: its wallet row, holds and ledger rows."""
        return (self.db("select ledger_total, reserved_total from infrx.wallets where "
                        "org_id = %s", tenant.org_id),
                self.one("select count(*) from infrx.credit_holds where org_id = %s",
                         tenant.org_id),
                self.one("select count(*) from public.credit_ledger where org_id = %s",
                         tenant.org_id))

    def footprint(self) -> tuple:
        """Everything an acceptance may write or do: the rows (jobs, holds of both units,
        outbox, mappings), the objects staged under the box's prefix, the media fetched and
        the admissions tried (review J1: R91's "nothing prepared or staged")."""
        rows = self.one("select (select count(*) from infrx.jobs), "
                        "(select count(*) from infrx.credit_wallet_holds), "
                        "(select count(*) from infrx.credit_holds), "
                        "(select count(*) from infrx.outbox), "
                        "(select count(*) from infrx.idempotency)")
        staged = harness.s3_client().list_objects_v2(
            Bucket=harness.S3_BUCKET, Prefix=self.box.env["S3_MEDIA_PREFIX"]).get("KeyCount", 0)
        return (*rows, staged, self.calls("fetch"), self.calls("admit"))

    def calls(self, kind: str) -> int:
        path = Path(self.box.env[CALLS_ENV])
        return path.read_text().splitlines().count(kind) if path.exists() else 0

    def untouched(self) -> tuple:
        """(lookups, footprint): a replay or a key conflict answered by ONE lookup and nothing
        else leaves the second as it was and moves the first by one (G3's MODES_409 shape)."""
        return self.calls("lookup"), self.footprint()

    def handle_of(self, request_id: str) -> str:
        return self.one("select job_handle from infrx.jobs where request_id = %s",
                        request_id)[0]

    def conserved(self, tenant) -> None:
        """Per CREDIT wallet: every settled charge (a CREDIT usage row) = its job's ADMITTED
        card x its usage, half up (review J4: `RateCardSnapshot.debit`, never the ledger
        compared with what the same settlement wrote), ledger = the one grant - those charges,
        reserved = its holds still held or unknown, available never negative."""
        from infrx.contracts.v2 import records as v2
        ledger, reserved, available = self.one(
            "select ledger_total, reserved_total, available from infrx.credit_wallets "
            "where wallet_id = %s", tenant.wallet.wallet_id)
        charged = 0
        for request_id, card, prompt, completion, amount in self.db(
                "select j.request_id, infrx.job_admission(j.request_id)->'rate_card', "
                "u.prompt_tokens, u.completion_tokens, u.charged_credits from infrx.jobs j "
                "join public.usage_events u on u.id = j.request_id "
                "where j.wallet_id = %s and u.accounting_regime = 'credit'",
                tenant.wallet.wallet_id):
            due = v2.RateCardSnapshot.model_validate(card).debit(prompt, completion).raw("CREDIT")
            assert amount == due, f"{tenant.name} {request_id}: charged {amount}, " \
                                  f"card x usage {due}"
            charged += due
        held, = self.one("select coalesce(sum(amount), 0) from infrx.credit_wallet_holds "
                         "where wallet_id = %s and state in ('held', 'unknown')",
                         tenant.wallet.wallet_id)
        from decimal import Decimal
        assert ledger == Decimal("10000") - charged, (tenant.name, ledger, charged)
        assert reserved == held, (tenant.name, reserved, held)
        assert available >= 0, (tenant.name, available)

    # --- HTTP -----------------------------------------------------------------
    @staticmethod
    def headers(tenant, key: str | None = None, **extra) -> dict:
        found = {"Authorization": f"Bearer {tenant.secret}", **extra}
        if key is not None:
            found["Idempotency-Key"] = key
        return found

    def send(self, tenant, mode: str, messages, key: str | None, **extra):
        """One request in `mode`: sync and SSE are chat completions, async is `/v1/jobs`."""
        import stack
        body = {"model": stack.CREDIT_ALIAS, "messages": messages, **extra}
        if mode == "sse":
            body["stream"] = True
        path = "/v1/jobs" if mode == "async" else "/v1/chat/completions"
        return self.http.post(path, json=body, headers=self.headers(tenant, key))

    def until_terminal(self, tenant, handle: str, timeout: float = 60.0) -> dict:
        end = time.monotonic() + timeout
        while True:
            status = self.http.get(f"/v1/jobs/{handle}", headers=self.headers(tenant))
            assert status.status_code == 200, status.text
            if status.json()["state"] in FINISHED or time.monotonic() > end:
                return status.json()
            time.sleep(0.05)

    def upload(self, tenant, data: bytes) -> str:
        """G4U's handshake: create, PUT the bytes, complete; the destination ref."""
        import hashlib
        ticket = self.http.post("/v1/uploads", headers=self.headers(tenant), json={
            "bytes": len(data), "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
            "accepted_mime": ["video/mp4"]})
        assert ticket.status_code == 201, ticket.text
        handle = ticket.json()["upload_handle"]
        put = self.http.put(f"/v1/uploads/{handle}", content=data,
                            headers=self.headers(tenant, **{"content-type": "video/mp4"}))
        done = self.http.post(f"/v1/uploads/{handle}/complete", headers=self.headers(tenant))
        assert (put.status_code, done.status_code) == (204, 200), (put.text, done.text)
        return ticket.json()["destination_ref"]


def frames(text: str) -> list[str]:
    """An SSE body as its frames, each exactly as sent (without the blank line)."""
    return [frame for frame in text.split("\n\n") if frame.strip()]


def frame_id(frame: str) -> str | None:
    return next((line[4:] for line in frame.splitlines() if line.startswith("id: ")), None)


def frame_data(frame: str):
    import json
    data = next((line[6:] for line in frame.splitlines() if line.startswith("data: ")), None)
    return data if data in (None, "[DONE]") else json.loads(data)


@contextlib.contextmanager
def journey(workdir: Path):
    """Two tenants provisioned on a fresh clone, PostgREST over it, E2's fake engine and the
    pilot box - the whole stack a journey calls, torn down afterwards."""
    import fake_vllm
    import stack
    world = stack.provision_two_tenants()
    engine = fake_vllm.FakeVllmServer(harness.PORTS["fake_vllm"])
    with stack.journey_postgrest(world.database) as rest, engine:
        with pilot_box(world.database, workdir, rest, engine.base_url) as box:
            yield Journey(world, box, engine)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if argv[1:] != ["gateway"]:
        raise SystemExit("usage: pilotbox.py gateway (the worker is python -m infrx.worker)")
    asyncio.run(gateway())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
