#!/usr/bin/env python3
"""I2B-R4: `python -m infrx.worker` - the worker process the pilot's unit runs.

    uv run --frozen pytest -q tests/w/test_worker_main.py
    # + the PostgreSQL cases (the D harness's task, a Valkey and a MinIO of the lane's own;
    #   each skips visibly without them):
    INFRX_D_TASK=d4 INFRX_D2_VALKEY_PORT=55465 INFRX_D2_VALKEY_CONTAINER=infrx-worker-valkey \\
    INFRX_M_S3_ENDPOINT=http://127.0.0.1:55781 INFRX_M_S3_LOCAL_CREDS=1 \\
        uv run --frozen pytest -q tests/w/test_worker_main.py

Three layers. The composition from the environment, with no service (the stores are built
and never connected; the object store and the index are injected, as `create_app`'s cases
do). The listener in process over E2's fake vLLM as a real process. And the real
`python -m infrx.worker` as a subprocess: its refusal with nothing configured, and on
PostgreSQL a job the gateway admitted (`create_app` over the same database, in the test's
process) run by the worker process against the fake vLLM and read back through the gateway,
then its SIGTERM drain with a job in flight.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import pathlib
import signal
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

import httpx
import pytest

from infrx.config import RuntimeMisconfigured, from_env
from infrx.contracts.records import MediaKind, MediaRef, OutboxKind
from infrx.contracts.v2 import fixtures as v2fix
from infrx.media.fetch import digest_of
from infrx.media.prepare import ProcessingCache
from infrx.media.store import InMemoryObjectStore
from infrx.scheduling.memory import MemoryScheduler
from infrx.state.jobstore import PgJobStore
from infrx.state.journal import PgStreamStore
from infrx.worker import __main__ as worker_main

API = pathlib.Path(__file__).resolve().parents[2]
REPO = API.parents[1]
RELEASE, IMAGE = "c0ffee" + "0" * 34, "sha256:" + "b" * 64
CARD = v2fix.BUILDERS["rate_card_marlin.json"]().rate_card_version
DSN = "postgresql://infrx:do-not-print@127.0.0.1:9/infrx"      # nothing listens on 9
# E2's MinIO literals (tests/integration/harness.py): local test strings, no secret.
S3_KEY, S3_SECRET, BUCKET = "infrxe2minio", "infrx-e2-local-secret", "infrx-worker-main"
PROMPT_TOKENS = 1200               # what the fake vLLM counts every prompt as


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def environment(tmp_path, **changes) -> dict[str, str]:
    """A pilot environment by the 08 §5 names; `NAME=None` removes one."""
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    env = {"INFRX_MODE": "pilot", "DATABASE_URL": DSN, "VALKEY_URL": "redis://127.0.0.1:9/0",
           "PROCESSING_CACHE_DIR": str(cache), "S3_MEDIA_BUCKET": BUCKET,
           "UPSTREAM": "http://127.0.0.1:9", "SUPABASE_URL": "https://fake.supabase.invalid",
           "SUPABASE_SERVICE_ROLE_KEY": "service-role-key-for-tests",
           "INFRX_RELEASE_SHA": RELEASE, "INFRX_IMAGE": IMAGE, "WORKER_CONCURRENCY": "3",
           "WORKER_HEALTH_PORT": "18002"}
    env.update(changes)
    return {name: value for name, value in env.items() if value is not None}


def composed(env, **injected):
    """`compose` with the object store and the index injected - never the stores."""
    injected.setdefault("objects", InMemoryObjectStore())
    injected.setdefault("index", MemoryScheduler(utc_now))
    return worker_main.compose(from_env(env), **injected)


async def http_get(port: int, path: str) -> tuple[int, str]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {path} HTTP/1.1\r\nhost: probe\r\n\r\n".encode())
    await writer.drain()
    raw = await reader.read()
    writer.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    return int(head.split()[1]), body.decode()


async def answer(port: int, path: str, want: int, within_s: float = 20.0) -> tuple[int, str]:
    """Poll until `path` answers `want` (or the bound; at least once); the last answer, for
    the assert. A refused connection is an answer of 0."""
    deadline = time.monotonic() + within_s
    while True:
        try:
            last = await http_get(port, path)
        except OSError as refused:
            last = (0, type(refused).__name__)
        if last[0] == want or time.monotonic() >= deadline:
            return last
        await asyncio.sleep(0.1)


def pilotbox_module(monkeypatch):
    """E3B's pilot box (tests/integration/backend/pilotbox.py), as its own suite loads it."""
    backend = REPO / "tests" / "integration" / "backend"
    monkeypatch.syspath_prepend(str(backend))
    spec = importlib.util.spec_from_file_location("infrx_i2b_pilotbox", backend / "pilotbox.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_vllm_module():
    spec = importlib.util.spec_from_file_location(
        "infrx_i2b_fake_vllm", REPO / "tests" / "integration" / "fake_vllm.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ the composition

REFUSALS = {
    "INFRX_MODE": {"INFRX_MODE": None},
    "DATABASE_URL": {"INFRX_MODE": "dev", "DATABASE_URL": None},
    "VALKEY_URL": {"VALKEY_URL": None},
    "PROCESSING_CACHE_DIR": {"PROCESSING_CACHE_DIR": None},
    "PROCESSING_CACHE_DIR-absent": {"PROCESSING_CACHE_DIR": "/nonexistent/infrx-cache"},
    "PROCESSING_CACHE_DIR-relative": {"PROCESSING_CACHE_DIR": "cache"},
    "S3_MEDIA_BUCKET": {"S3_MEDIA_BUCKET": None},
    "INFRX_RELEASE_SHA": {"INFRX_RELEASE_SHA": None},
    "INFRX_IMAGE": {"INFRX_IMAGE": None},
    "SUPABASE_URL": {"SUPABASE_URL": None},
    "ACTIVE_RATE_CARD_VERSION": {"ACCOUNTING_REGIME": "credit"},
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_worker_main__each_missing_setting_refuses_startup_by_name(case, tmp_path):
    """Fail closed like the gateway, and on what this process needs in every mode: a store
    that outlives it (never an in-memory one), the index, the shared media root and the
    bucket. The refusal names the setting and never a value."""
    env = environment(tmp_path, **REFUSALS[case])
    objects = None if case == "S3_MEDIA_BUCKET" else InMemoryObjectStore()   # None: built
    with pytest.raises(RuntimeMisconfigured) as refused:
        composed(env, objects=objects)
    assert case.split("-")[0] in str(refused.value), str(refused.value)
    assert "do-not-print" not in str(refused.value)


def test_worker_main__the_composition_is_the_pilots_stores_and_settings(tmp_path):
    """The stores are D's on the pool `DATABASE_URL` builds; the engine is VllmEngine on
    `UPSTREAM` with the shared media root; the pool and the port are the settings'. The
    CREDIT regime runs W's runner through `load_work_credit`/`complete_credit`; the legacy
    one through the v1 doors."""
    port = free_port()
    service, pool = composed(environment(tmp_path, WORKER_HEALTH_PORT=str(port)))
    runner = service.loop.runner
    assert type(runner.jobs) is PgJobStore and runner.jobs is service.jobs
    assert type(runner.stream) is PgStreamStore
    assert runner.put_result.__self__ is runner.jobs and pool.closed
    assert (service.concurrency, service.health_port, service.health_host) == \
        (3, port, "127.0.0.1")
    assert str(service.engine.client.base_url) == "http://127.0.0.1:9"
    assert service.engine.local_media_root == str(tmp_path / "cache")

    credit, _ = composed(environment(tmp_path, ACCOUNTING_REGIME="credit",
                                     ACTIVE_RATE_CARD_VERSION=CARD))
    jobs = credit.loop.runner.jobs
    assert isinstance(jobs, worker_main.CreditWork) and type(jobs.store) is PgJobStore
    assert credit.jobs is jobs

    calls = []

    class Store:
        async def load_work_credit(self, lease):
            calls.append(("load_work_credit", lease))
            return type("W", (), {"request": type("R", (), {"request": "req"}),
                                  "media_refs": (), "prepared_refs": (), "budgets": None,
                                  "prompt_tokens": 7})()

        async def complete_credit(self, lease, outcome):
            calls.append(("complete_credit", lease))
            return "settled", "settlement"

    routed = worker_main.CreditWork(Store())
    work = asyncio.run(routed.load_work("lease"))
    assert (work.request, work.prompt_tokens) == ("req", 7)
    assert asyncio.run(routed.complete("lease", "outcome")) == "settled"
    assert calls == [("load_work_credit", "lease"), ("complete_credit", "lease")]


def test_worker_main__local_uri_finds_the_file_the_gateway_prepared(tmp_path):
    """M's pilot request: the engine resolves a prepared ref through the shared processing
    cache ON DISK - the gateway's process wrote it, this one never indexed it."""
    service, _ = composed(environment(tmp_path))
    data = b"\x00\x00\x00\x18ftypmp42" + os.urandom(64)
    digest = digest_of(data)
    org = "1a1a1a1a-0000-4000-8000-000000000001"
    path = ProcessingCache(str(tmp_path / "cache")).path_for(org, "v1", digest, "video/mp4")
    os.makedirs(os.path.dirname(path))
    pathlib.Path(path).write_bytes(data)
    ref = MediaRef(org_id=org, handle="med_1", kind=MediaKind.url, digest=digest,
                   bytes=len(data), mime="video/mp4", storage_ref="media/x", duration_s=10.0)
    assert service.engine.local_uri(ref) == "file://" + path


def test_worker_main__build_info_is_the_installed_settings_never_git(tmp_path):
    """E4B's served-build check on the worker: the gauge carries `INFRX_RELEASE_SHA` and
    `INFRX_IMAGE` as installed - here a revision that is not this checkout's HEAD."""
    service, _ = composed(environment(tmp_path))
    (line,) = [line for line in service.metrics.render().splitlines()
               if line.startswith("infrx_build_info{")]
    assert line == (f'infrx_build_info{{process="worker",revision="{RELEASE}",'
                    f'image="{IMAGE}"}} 1.0'), line


def test_worker_main__readyz_waits_for_the_engine_and_metrics_are_served(tmp_path):
    """The composed service's loopback listener: 503 `engine: down` while nothing answers
    on `UPSTREAM`, 200 once the engine's `/health` does; `/metrics` is the build gauge."""
    engine_port, port = free_port(), free_port()
    service, _ = composed(environment(tmp_path, UPSTREAM=f"http://127.0.0.1:{engine_port}",
                                      WORKER_HEALTH_PORT=str(port), INFRX_MODE="dev"))
    server = None

    async def case():
        nonlocal server
        await service.start()
        try:
            down = await http_get(port, "/readyz")
            server = fake_vllm_module().FakeVllmServer(engine_port).start()
            up = await answer(port, "/readyz", 200)
            metrics = await http_get(port, "/metrics")
        finally:
            await service.stop()
            await service.engine.client.aclose()
        return down, up, metrics

    try:
        down, up, metrics = asyncio.run(case())
    finally:
        if server is not None:
            server.stop()
    assert down[0] == 503 and json.loads(down[1])["engine"] == "down", down
    assert up[0] == 200 and json.loads(up[1])["engine"] == "up", up
    assert metrics[0] == 200 and f'revision="{RELEASE}"' in metrics[1], metrics


# ------------------------------------------------------------------ the process

def process_env(settings: dict[str, str]) -> dict[str, str]:
    """Only what the unit's env file would carry, plus the local S3 literals and the path
    to this tree's package: never the caller's AWS keys or anything else of the shell."""
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(API),
            "PYTHONUNBUFFERED": "1", "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
            "AWS_ACCESS_KEY_ID": S3_KEY, "AWS_SECRET_ACCESS_KEY": S3_SECRET,
            "AWS_DEFAULT_REGION": "us-east-1", "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_CONFIG_FILE": os.devnull, "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
            **settings}


def start_worker(settings: dict[str, str], log: pathlib.Path) -> subprocess.Popen:
    with open(log, "wb") as out:
        return subprocess.Popen([sys.executable, "-m", "infrx.worker"], cwd=str(API),
                                env=process_env(settings), stdout=out,
                                stderr=subprocess.STDOUT, start_new_session=True)


def stop_worker(process: subprocess.Popen, sig=signal.SIGTERM, within_s: float = 60.0):
    if process.poll() is None:
        process.send_signal(sig)
        try:
            process.wait(timeout=within_s)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
    return process.returncode


def test_worker_main__the_process_refuses_to_start_naming_the_setting(tmp_path):
    """`python -m infrx.worker` with a mode and nothing else: exit 2, the settings named, no
    traceback, no value; with no mode at all, the mode named."""
    for settings, named in (({"INFRX_MODE": "dev"}, "DATABASE_URL"), ({}, "INFRX_MODE")):
        log = tmp_path / f"refused-{named}.log"
        process = start_worker(settings, log)
        assert process.wait(timeout=60) == worker_main.REFUSED, log.read_text()
        text = log.read_text()
        assert f"refusing to start" in text and named in text and "Traceback" not in text, text


def test_worker_main__the_pilot_box_runs_the_real_entry_point(tmp_path, monkeypatch):
    """E3B's pilot box: its worker process is `python -m infrx.worker` (a mutation copy on
    PYTHONPATH first), on the fake engine as `UPSTREAM`, with a readiness port of its own
    that `start` waits on, and the index in the pilot's namespace (where the real worker
    reads it); the gateway is still the box's own composition."""
    pilotbox = pilotbox_module(monkeypatch)
    monkeypatch.setenv("PYTHONPATH", "/a-mutation-copy")
    box = pilotbox.PilotBox({"S3_MEDIA_PREFIX": "p/"}, "http://127.0.0.1:1", tmp_path, 1)
    assert box.command("worker") == ([sys.executable, "-m", "infrx.worker"],
                                      f"http://127.0.0.1:{box.worker_port}/readyz")
    assert box.command("gateway")[0][1:] == [
        str(REPO / "tests" / "integration" / "backend" / "pilotbox.py"), "gateway"]
    assert (box.env["UPSTREAM"], box.env["WORKER_HEALTH_PORT"]) == \
        ("http://127.0.0.1:1", str(box.worker_port))
    # E's mutation runner's copy (on PYTHONPATH) first, the checkout's package after it
    assert box.env["PYTHONPATH"] == os.pathsep.join(("/a-mutation-copy",
                                                     str(pilotbox.harness.API_ROOT)))
    assert box.namespace == box.env[pilotbox.INDEX_ENV] == "infrx:sched:{pilot}"


# --- on PostgreSQL: the D harness, a Valkey and a MinIO of the lane's own ------------

def s3_bucket():
    """(client, endpoint) of the lane's MinIO with the test bucket made, or a visible skip."""
    endpoint = os.environ.get("INFRX_M_S3_ENDPOINT", "")
    if not endpoint or os.environ.get("INFRX_M_S3_LOCAL_CREDS") != "1":
        pytest.skip("I2B-R4: no local S3 endpoint - start a MinIO and export "
                    "INFRX_M_S3_ENDPOINT with INFRX_M_S3_LOCAL_CREDS=1")
    import botocore.session
    from botocore.config import Config
    from botocore.exceptions import ClientError
    s3 = botocore.session.get_session().create_client(
        "s3", endpoint_url=endpoint, aws_access_key_id=S3_KEY, aws_secret_access_key=S3_SECRET,
        region_name="us-east-1", config=Config(s3={"addressing_style": "path"}))
    try:
        s3.create_bucket(Bucket=BUCKET)
    except ClientError as exists:
        if exists.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou",
                                                    "BucketAlreadyExists"):
            raise
    return s3, endpoint


def services():
    """(pgharness, vkstore, s3 client, endpoint) or a visible skip naming what is missing."""
    from ..d import pgharness, vkstore
    reason = vkstore.unavailable()          # pgharness's docker check included
    if reason:
        pytest.skip(f"I2B-R4: the D harness is unavailable: {reason}")
    s3, endpoint = s3_bucket()
    vkstore.ensure()
    return pgharness, vkstore, s3, endpoint


class Box:
    """One pilot box on this lane's services: a fresh CREDIT database (the operator's Marlin
    seed and the fixture's consumer), the Valkey index in its pilot namespace, a prefix of
    the MinIO bucket, E2's fake vLLM, the gateway composed in this process and the worker
    process. Torn down by `close`."""

    def __init__(self, tmp_path, monkeypatch) -> None:
        from infrx.state import migrations, pgtesting

        from ..d import pgstore
        pgharness, vkstore, self.s3, endpoint = services()
        harness = pgtesting.make_credit_jobstore_factory(
            pgstore.fresh_database, pgharness.dsn, migrations.SEED_MARLIN.read_text())()
        self.conn = harness.extra["conn"]
        # The conformance rig freezes the clone's clock; two processes agree on "now" only
        # as they do on the box.
        self.conn.execute("select infrx_test.unfreeze(), infrx_test.set_offset(0)")
        self.valkey_url = f"redis://127.0.0.1:{vkstore.PORT}/0"
        self.prefix = f"test/i2b-r4/{uuid.uuid4().hex}/"
        self.engine_port, self.port = free_port(), free_port()
        self.settings = environment(
            tmp_path, DATABASE_URL=pgharness.dsn(harness.extra["database"]),
            VALKEY_URL=self.valkey_url, S3_ENDPOINT_URL=endpoint, S3_MEDIA_PREFIX=self.prefix,
            UPSTREAM=f"http://127.0.0.1:{self.engine_port}", ACCOUNTING_REGIME="credit",
            ACTIVE_RATE_CARD_VERSION=CARD, WORKER_HEALTH_PORT=str(self.port),
            WORKER_CONCURRENCY="2", USAGE_LOG=str(tmp_path / "usage.jsonl"))
        for name, value in process_env({}).items():
            if name.startswith("AWS_"):
                monkeypatch.setenv(name, value)
        for name in ("AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_ENDPOINT_URL",
                     "AWS_ENDPOINT_URL_S3"):
            monkeypatch.delenv(name, raising=False)
        self.log = tmp_path / "worker.log"
        self.engine = None
        self.worker = None
        self._flush()

    def _flush(self) -> None:
        import valkey
        client = valkey.Valkey.from_url(self.valkey_url)
        leftovers = list(client.scan_iter(match="infrx:sched:{pilot}*"))
        if leftovers:
            client.delete(*leftovers)
        client.close()

    def start_engine(self, **control):
        self.engine = fake_vllm_module().FakeVllmServer(self.engine_port).start()
        if control:
            self.engine.control(**control)

    def start_worker(self) -> None:
        self.worker = start_worker(self.settings, self.log)

    def gateway(self):
        from infrx.gateway.app import create_app

        from ..g import relay_support as rs
        from ..g import support as gs
        return create_app(from_env(self.settings), client=gs.upstream(),
                          sb=gs.supabase(rows=(rs.CONSUMER_ROW,)))

    def close(self) -> None:
        if self.worker is not None:
            stop_worker(self.worker, signal.SIGKILL)
        if self.engine is not None:
            self.engine.stop()
        self._flush()
        listed = self.s3.list_objects_v2(Bucket=BUCKET, Prefix=self.prefix)
        for item in listed.get("Contents", []):
            self.s3.delete_object(Bucket=BUCKET, Key=item["Key"])

    # --- the job ---------------------------------------------------------------------
    async def admit(self, client) -> tuple[str, str]:
        """`POST /v1/jobs` through the gateway: 202, the handle and the request id."""
        accepted = await client.post("/v1/jobs", headers={"authorization": "Bearer sk-i2b"},
                                     json={"model": "nemostation/marlin-2b",
                                           "messages": [{"role": "user", "content": "hi"}]})
        assert accepted.status_code == 202, accepted.text
        return accepted.json()["job_handle"], accepted.json()["request_id"]

    async def prepare(self, request_id: str) -> None:
        """M's preparation worker, emulated as the E3B pilot box does (no product process
        claims a preparation lease yet): the text job is prepared with the count the fake
        engine reports, and its inference dispatch is relayed into the pilot index."""
        from valkey.asyncio import Valkey

        from infrx.scheduling.valkey import ValkeyScheduler
        from infrx.state.jobstore import connector
        pilot = from_env(self.settings).pilot
        store = PgJobStore(connector(pilot.database_url), limits=pilot)
        lease = await store.claim_preparation(request_id, "i2b-prep")
        await store.prepared(lease, (), prompt_tokens=PROMPT_TOKENS)
        index = ValkeyScheduler(Valkey.from_url(self.valkey_url), utc_now, limits=pilot)
        events = await store.dispatch_pending(worker_id="i2b-relay")
        for event in events:
            if event.kind is OutboxKind.inference_dispatch and event.job_id == request_id:
                assert await index.enqueue(event)
        await store.acknowledge_dispatch([event.event_id for event in events],
                                         worker_id="i2b-relay")
        await index.client.aclose()

    def row(self, request_id: str) -> tuple:
        return self.conn.execute(
            "select state, outcome_cause, settlement_state from infrx.jobs where request_id = %s",
            (request_id,)).fetchone()


async def until(predicate, within_s: float = 30.0):
    deadline = time.monotonic() + within_s
    while True:
        value = await predicate()
        if value or time.monotonic() > deadline:
            return value
        await asyncio.sleep(0.1)


@pytest.fixture
def box(tmp_path, monkeypatch):
    made = Box(tmp_path, monkeypatch)
    yield made
    made.close()


def test_worker_main_pg__a_job_the_gateway_admitted_runs_in_the_worker_process(box):
    """The round trip across two processes: the gateway admits a CREDIT job on PostgreSQL;
    preparation (emulated) queues it; the worker PROCESS claims it from the Valkey index,
    runs it on the fake vLLM, journals and settles it at the admitted card; the gateway
    reads it back succeeded with its result. Before that, the worker's `/readyz` is 503
    while the engine is down and 200 once it answers, and `/metrics` carries the build."""
    box.start_worker()

    async def case():
        before = await answer(box.port, "/readyz", 503, within_s=60)
        box.start_engine()
        ready = await answer(box.port, "/readyz", 200)
        metrics = await http_get(box.port, "/metrics")
        app = box.gateway()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://gw") as client:
            handle, request_id = await box.admit(client)
            await box.prepare(request_id)
            auth = {"authorization": "Bearer sk-i2b"}

            async def terminal():
                status = (await client.get(f"/v1/jobs/{handle}", headers=auth)).json()
                return status if status["state"] in ("succeeded", "failed", "cancelled") \
                    else None
            status = await until(terminal)
            result = await client.get(f"/v1/jobs/{handle}/result", headers=auth)
        return before, ready, metrics, request_id, status, result

    before, ready, metrics, request_id, status, result = asyncio.run(case())
    log = box.log.read_text()
    assert before[0] == 503 and json.loads(before[1])["engine"] == "down", (before, log)
    assert ready[0] == 200 and json.loads(ready[1])["loop"] in ("idle", "busy"), (ready, log)
    assert f'infrx_build_info{{process="worker",revision="{RELEASE}",image="{IMAGE}"}} 1.0' \
        in metrics[1], metrics
    assert status and (status["state"], status["cause"], status["result_available"]) == \
        ("succeeded", "completed", True), (status, log)
    assert result.status_code == 200, result.text
    assert result.json()["response"]["usage"] == status["usage"], result.text
    assert box.row(request_id) == ("succeeded", "completed", "settled")
    charged, regime, prompt = box.conn.execute(
        "select charged_credits, accounting_regime, prompt_tokens from public.usage_events "
        "where id = %s", (request_id,)).fetchone()
    assert (regime, prompt) == ("credit", PROMPT_TOKENS) and charged > 0
    chunks = box.conn.execute("select count(*) from infrx.stream_chunks where job_id = %s",
                              (request_id,)).fetchone()[0]
    assert chunks > 1                                    # deltas and the terminal event
    assert stop_worker(box.worker) == 0, box.log.read_text()


def test_worker_main_pg__sigterm_drains_the_in_flight_job_and_exits_0(box):
    """OPS-RECOVER on the process: SIGTERM while an attempt streams (the fake engine slowed
    to seconds) stops claiming, lets the attempt finish inside the generation budget - the
    job succeeds, its lease settled rather than abandoned - and the process exits 0."""
    box.start_engine(text="Two people unload boxes from a van onto a trolley. " * 3,
                     delta_gap_s=0.25)
    box.start_worker()

    async def case():
        ready = await answer(box.port, "/readyz", 200, within_s=60)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=box.gateway()),
                                     base_url="http://gw") as client:
            _, request_id = await box.admit(client)
            await box.prepare(request_id)

        async def busy():
            status, body = await http_get(box.port, "/readyz")
            return json.loads(body)["loop"] == "busy"
        streaming = await until(busy, within_s=30)
        box.worker.send_signal(signal.SIGTERM)
        return ready, request_id, streaming

    ready, request_id, streaming = asyncio.run(case())
    assert ready[0] == 200 and streaming, (ready, box.log.read_text())
    assert box.row(request_id)[0] == "running"           # the signal landed mid-attempt
    code = stop_worker(box.worker, within_s=120)
    log = box.log.read_text()
    assert code == 0, log
    assert box.row(request_id) == ("succeeded", "completed", "settled"), log
    # W3's drain record: both runners stopped inside the bound, nothing released, and the
    # one attempt in flight ended `completed`.
    assert f"drained: 2 finished, 0 released [], ended [('{request_id}', 'completed')]" \
        in log, log


def test_worker_main_pg__an_unreachable_database_refuses_before_readiness(tmp_path):
    """The pool is opened before the listener is bound: with the bucket answering and the
    database not, the process exits 2 naming DATABASE_URL and `/readyz` never answered."""
    _, endpoint = s3_bucket()
    port = free_port()
    settings = environment(tmp_path, S3_ENDPOINT_URL=endpoint, WORKER_HEALTH_PORT=str(port),
                           DATABASE_POOL_CONNECT_TIMEOUT_S="2")
    log = tmp_path / "unreachable.log"
    process = start_worker(settings, log)

    async def watch():
        answered, deadline = [], time.monotonic() + 60
        while process.poll() is None and time.monotonic() < deadline:
            try:
                answered.append((await http_get(port, "/readyz"))[0])
            except OSError:
                pass
            await asyncio.sleep(0.05)
        return answered

    try:
        answered = asyncio.run(watch())
    finally:
        code = stop_worker(process, signal.SIGKILL)
    text = log.read_text()
    assert code == worker_main.REFUSED and "DATABASE_URL did not answer" in text, (code, text)
    assert answered == [] and "do-not-print" not in text, (answered, text)


def test_worker_main_pg__the_pilot_box_starts_the_real_worker_and_waits_for_it(box, monkeypatch):
    """E3B's pilot box on this lane's services: `start("worker")` returns once `python -m
    infrx.worker` answers `/readyz` 200, and `stop` drains it to exit 0."""
    pilotbox = pilotbox_module(monkeypatch)
    box.start_engine()
    pilot = pilotbox.PilotBox(dict(box.settings), f"http://127.0.0.1:{box.engine_port}",
                              box.log.parent, 1)
    try:
        pilot.start("worker", timeout=60)
        ready = asyncio.run(answer(pilot.worker_port, "/readyz", 200, within_s=0))  # once
    finally:
        code = pilot.stop("worker")
    assert ready[0] == 200 and '"engine": "up"' in ready[1], (ready, pilot.tail("worker"))
    assert code == 0, pilot.tail("worker")
