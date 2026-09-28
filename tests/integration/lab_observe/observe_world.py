"""E5L's world: E3C's composed App box (`backend/e3c/world.py`) in namespace `e5l`, plus the
trace, feedback, retention and judge stores the scenarios judge - on the same fresh clone,
through the merged code only.

* **traces**: a ClickHouse database of the trip's own on the e5l ClickHouse (the three trace
  schemas), the e5l MinIO bucket under the trip's own object prefix, and T2I's real factory
  `ship.build_shipper` over them with D5's pins on the clone (`PgPins`). Capture is OFF in the
  box (no sink is built by the gateway on this base, R140); a trace is written the way the
  process that owns the spool would, with T1's real `SpoolTraceSink` on the trip's directory.
* **access**: provider A is the seed's NemoStation (it owns the served Marlin model); DEV is a
  developer of A; alpha's organization grants A `provider_sharing` + `external_judging` on the
  request and response content of that model (L2-SQL `lab_put_access_grant`, read through
  L2's `LabAccess` over `PgAccessStore`). beta grants nobody.
* **judge**: J2's `submit`/`reconcile`/`collect` over the real access, projection and objects,
  J2's local judge fake (HTTP, loopback, e5l port 57165) and J2's in-memory D6J ledger
  (`tests/j/submit/fakes.py`: D6J is not merged - every judge verdict says so). It is a DRY RUN
  in the P-10 sense: no external provider is reachable; nothing here is a live-judge run.

`NOT_RUN` is the one vocabulary for a case that waits on an unmerged lane: the runner maps it
to NOT RUN, never a pass, and the reason carries the exact rerun command.
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import importlib.util
import json
import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
INTEGRATION = HERE.parent
E3C = INTEGRATION / "backend" / "e3c"
NAMESPACE = "e5l"
sys.path[:0] = [p for p in (str(E3C), str(INTEGRATION), str(INTEGRATION / "backend"))
                if p not in sys.path]
import world                                            # noqa: E402 - E3C's composed box

import stack                                            # noqa: E402

harness = world.harness
API = harness.REPO_ROOT / "apps" / "infrx-api"
RUNNER = "tests/integration/lab_observe/runner.py"
RERUN = f"apps/infrx-api/.venv/bin/python {RUNNER} --out <dir>"
PROVIDER, MODEL = stack.SEED_PROVIDER_ORG, stack.SEED_MODEL
DEV = "d1000000-0000-4000-8000-00000000005a"
JUDGE_PORT = harness.PORT_RANGE.start + 65               # e5l: 57165, loopback only
JUDGE_MODEL = "judge-e5l"
#: A test input, not an approved price (J1's TEST_RATE shape): the shipped table is empty.
RATE_ROW = dict(price_version="e5l-test-rates", model=JUDGE_MODEL,
                input_per_million=Decimal("5"), output_per_million=Decimal("25"),
                source="tests/integration/lab_observe - test input, not an approved price")


def not_run(sid: str, *lanes: str, why: str):
    """Skip as NOT RUN on unmerged lanes, naming the exact rerun (never a pass)."""
    import pytest
    pytest.skip(f"NOT RUN[{','.join(lanes)}] {why}; rerun after the merge: {RERUN} --only {sid}")


def load(name: str, path: Path):
    """A module by path under a unique name (J2's fakes live under apps/infrx-api/tests)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def run(coroutine):
    return asyncio.run(coroutine)


# ------------------------------------------------------------------ the trip

@dataclasses.dataclass
class Traces:
    """The trip's trace stores: the shipper T2I's factory built, and what it consults."""

    database: str
    client: object
    shipper: object
    spool_dir: Path
    dsn: str

    @property
    def projection(self):
        return self.shipper.projection

    @property
    def objects(self):
        return self.shipper.objects

    @property
    def retention(self):
        return self.shipper.retention

    def sink(self):
        """T1's durable sink on the trip's spool directory, on the wall clock (one writer)."""
        from infrx.traces.spool import SpoolTraceSink
        return SpoolTraceSink(Wall, limits=limits(self), spool_dir=self.spool_dir)

    def rows(self, table: str, request_id: str) -> int:
        return self.client.query(f"select count() from {table} final where request_id = "
                                 "{r:UUID}", parameters={"r": request_id}).result_rows[0][0]


class Wall:
    @staticmethod
    def now():
        from datetime import datetime, timezone
        return datetime.now(timezone.utc)


def limits(traces: Traces):
    from infrx.contracts.limits import DEFAULTS
    return dataclasses.replace(
        DEFAULTS, trace_spool_dir=str(traces.spool_dir),
        clickhouse_url=(f"http://{harness.CH_USER}:{harness.CH_PASSWORD}@127.0.0.1:"
                        f"{harness.PORTS['clickhouse_http']}/{traces.database}"),
        s3_trace_bucket=harness.S3_BUCKET, database_url=traces.dsn)


@contextlib.contextmanager
def s3_credentials():
    """E2's MinIO literals as this process's botocore credentials, restored after."""
    saved = {name: os.environ.get(name) for name in stack.s3_env()}
    os.environ.update(stack.s3_env())
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@contextlib.contextmanager
def trace_stores(trip, workdir: Path):
    """A ClickHouse database and an object prefix of the trip's own, and T2I's shipper."""
    from infrx.traces import ship
    database = f"e5l_{uuid.uuid4().hex[:12]}"
    admin = harness.clickhouse_client()
    admin.command(f"CREATE DATABASE {database}")
    import clickhouse_connect
    client = clickhouse_connect.get_client(
        host="127.0.0.1", port=harness.PORTS["clickhouse_http"], username=harness.CH_USER,
        password=harness.CH_PASSWORD, database=database)
    for path in harness.TRACE_SCHEMAS:
        for statement in (p.strip() for p in path.read_text().split(";")):
            if statement and not all(line.strip().startswith("--") or not line.strip()
                                     for line in statement.splitlines()):
                client.command(statement)
    spool_dir = workdir / "spool"
    spool_dir.mkdir(parents=True, exist_ok=True)
    traces = Traces(database, client, None, spool_dir, harness.pg_dsn(trip.world.database))
    try:
        with s3_credentials():
            stack.media_bucket()
            prefix = f"{harness.OBJECT_PREFIX}{workdir.name[:40]}-{uuid.uuid4().hex[:6]}/"
            traces.shipper = ship.build_shipper(limits(traces), None, prefix=prefix,
                                                endpoint_url=harness.s3_endpoint())
            assert traces.shipper is not None, "build_shipper refused a complete setting set"
            yield traces
    finally:
        with contextlib.suppress(Exception):
            admin.command(f"DROP DATABASE IF EXISTS {database}")


@contextlib.contextmanager
def observe_trip(workdir: Path, start=("worker", "gateway"), **env: str):
    """E3C's composed clone (the box processes `start` names) with the Lab seeded and the
    trip's trace stores."""
    with world.composed(workdir, start=start, **env) as trip:
        seed_lab(trip)
        with trace_stores(trip, workdir) as traces:
            trip.traces = traces
            yield trip


def seed_lab(trip) -> None:
    """DEV (a developer of A) and alpha's grant to A (see the module docstring)."""
    sql(trip, "insert into auth.users (id, email, email_confirmed_at) values "
              "(%s, 'dev@e5l.invalid', infrx.now())", DEV)
    sql(trip, "insert into infrx.provider_memberships (provider_org_id, user_id, role, "
              "granted_by, granted_at) values (%s, %s, 'developer', 'e5l', "
              "infrx.now() - interval '1 day')", PROVIDER, DEV)
    grant(trip, trip.world.alpha, "provider_sharing", "external_judging")


def sql(trip, statement: str, *args) -> None:
    import psycopg
    with psycopg.connect(harness.pg_dsn(trip.world.database), autocommit=True) as conn:
        conn.execute(statement, args)


def grant(trip, tenant, *purposes: str) -> None:
    from psycopg.types.json import Jsonb
    sql(trip, "select infrx.lab_put_access_grant(%s)", Jsonb({
        "actor_user_id": tenant.user_id, "grantor_org_id": tenant.org_id,
        "recipient_provider_org_id": PROVIDER, "model_ids": [MODEL],
        "categories": ["request_content", "response_content"], "purposes": list(purposes),
        "retention_days": 30}))


def revoke(trip, tenant) -> None:
    from psycopg.types.json import Jsonb
    sql(trip, "select infrx.lab_revoke_access_grant(%s)", Jsonb({
        "actor_user_id": tenant.user_id, "grantor_org_id": tenant.org_id,
        "recipient_provider_org_id": PROVIDER}))


def access(trip):
    """L2's `LabAccess` over L2-SQL's `PgAccessStore` on the scenario's clone."""
    from infrx.lab.access import LabAccess
    from infrx.state.jobstore import connector
    from infrx.state.lab_access import PgAccessStore
    return LabAccess(PgAccessStore(connector(harness.pg_dsn(trip.world.database))))


# ------------------------------------------------------------------ App traffic and traces

def served(trip, tenant, tag: str) -> str:
    """One async App request, finished and settled once: its durable request id."""
    accepted = trip.send(tenant, "async", world.TEXT, f"e5l-{tag}-{uuid.uuid4().hex[:6]}")
    assert accepted.status_code == 202, f"{tag}: {accepted.status_code} {accepted.text[:200]}"
    request_id = accepted.json()["request_id"]
    assert world.terminal(trip, request_id, timeout=90.0) == "succeeded", request_id
    world.settled_once(trip, request_id)
    return request_id


def app_serves(trip, tenant, tag: str) -> str:
    """Sync and SSE answered in full and one async job finished once (no App outage)."""
    sync = trip.send(tenant, "sync", world.TEXT, None)
    assert sync.status_code == 200, f"{tag}: sync {sync.status_code} {sync.text[:200]}"
    sse = trip.send(tenant, "sse", world.TEXT, None)
    assert sse.status_code == 200 and "data: [DONE]" in sse.text, f"{tag}: sse {sse.text[-200:]}"
    return served(trip, tenant, tag)


def captured(trip, tenant, request_ids, content: bytes = b'{"q":"Describe the van."}') -> None:
    """Each request's trace, written durably by the spool's one writer and its segment sealed
    for the shipper (the gateway builds no sink on this base; WR-COMP-4)."""
    from datetime import timedelta

    from infrx.contracts.records import TraceEnvelope, TraceMode

    async def write():
        sink = trip.traces.sink()
        try:
            for request_id in request_ids:
                now = Wall.now()
                with sink.open(request_id, tenant.org_id, TraceMode.full,
                               now + timedelta(minutes=10)) as capture:
                    assert capture.add(content) is True
                    await capture.finish(TraceEnvelope(
                        request_id=request_id, org_id=tenant.org_id, key_id=tenant.key_id,
                        mode=TraceMode.full, started_at=now, content_complete=True,
                        content_ref=f"traces/{tenant.org_id}/{request_id}.json",
                        content_bytes=len(content), metadata_bytes=256,
                        model_revision="marlin-e5l", price_version=stack.SEED_CARD))
            await sink.flush(Wall.now() + timedelta(seconds=5))
            await sink.rotate()
        finally:
            await sink.close(drop_queued=False)
    run(write())


def shipped(trip):
    """One ship pass over the trip's spool (a fresh reader of the directory, as a restarted
    worker is): the report."""
    async def once():
        sink = trip.traces.sink()
        try:
            trip.traces.shipper.spool = sink
            return await trip.traces.shipper.ship()
        finally:
            await sink.close(drop_queued=False)
    return run(once())


# ------------------------------------------------------------------ the judge

def j2(name: str):
    return load(f"e5l_j2_{name}", API / "tests" / "j" / "submit" / f"{name}.py")


def judge_result() -> str:
    """A well-formed MARLIN_VIDEO_V1 payload (J1's `result()` shape)."""
    criterion = {"score": 4, "rationale": "frame 3 shows it"}
    return json.dumps({"relevance": criterion, "completeness": criterion, "format": criterion,
                       "refusal": criterion, "groundedness": criterion, "notes": "fine",
                       "overall_pass": True})


@dataclasses.dataclass
class Judge:
    wiring: object
    ledger: object
    fake: object
    payer: str

    def job(self, trip, request_ids, run_id: str | None = None):
        from infrx.judge.submit import JudgeJob
        return JudgeJob(run_id=run_id or str(uuid.uuid4()), provider_org_id=PROVIDER,
                        grantor_org_id=trip.world.alpha.org_id, model_id=MODEL,
                        judge_model=JUDGE_MODEL, payer_ref=self.payer,
                        request_ids=tuple(request_ids))

    def posts(self) -> list[dict]:
        return list(self.fake.posts)


@contextlib.contextmanager
def judge(trip, *, mode: str = "live", budget: str = "1.00", timeout_s: float = 5.0):
    """J2's wiring on the trip: real access/projection/objects, the local judge fake, J2's
    in-memory D6J ledger with a PROVIDER_USD budget for A's named payer."""
    from infrx.contracts.limits import DEFAULTS
    from infrx.contracts.v2.money_units import ProviderUsd
    from infrx.judge.cost import ProviderRate, StaticRateTable
    from infrx.judge.submit import HttpJudgeProvider, JudgeWiring
    fakes = j2("fakes")
    fake = j2("judge_fake").JudgeFake(JUDGE_PORT)
    try:
        payer = fakes.payer(PROVIDER)
        ledger = fakes.FakeJudgeLedger({payer: ProviderUsd(budget)})
        rates = StaticRateTable((ProviderRate(**RATE_ROW, effective_at=fakes.T0.replace(
            month=1, day=1)),))
        settings = dataclasses.replace(DEFAULTS, judge_mode=mode,
                                       judge_live_budget_usd=Decimal(budget))
        wiring = JudgeWiring(access=access(trip), ledger=ledger,
                             provider=HttpJudgeProvider(fake.url, timeout_s=timeout_s),
                             retention=trip.traces.retention, rates=rates, settings=settings)
        yield Judge(wiring, ledger, fake, payer)
    finally:
        fake.close()


async def submit(judge: Judge, trip, request_ids, *, job=None):
    """J2's `submit` as the provider's developer: the run it leaves."""
    from infrx.judge.submit import submit as j2_submit
    return await j2_submit(job or judge.job(trip, request_ids), user_id=DEV, wiring=judge.wiring)


def new_id() -> str:
    return str(uuid.uuid4())
