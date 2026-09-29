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
#: The compose project (`infrx-<PROJECT>`): `INFRX_E5L_PROJECT`, default the namespace.
PROJECT = os.environ.get("INFRX_E5L_PROJECT") or NAMESPACE
sys.path[:0] = [p for p in (str(E3C), str(INTEGRATION), str(INTEGRATION / "backend"))
                if p not in sys.path]


def load_harness() -> None:
    """E2's harness in namespace e5l under compose project `infrx-<PROJECT>`. Only the compose
    project and the names derived from it move (containers, volumes, network, bucket, state
    file); the tasklocal ports, the database and the key/object prefixes stay e5l's. It lets a
    run sidestep the foreign `infrx-e5l_*` volumes a finished clone left, which nothing here
    may touch. Unset, the harness loads untouched (E3C's `world.load_harness`)."""
    import re
    import types
    if not re.fullmatch(r"e5l[a-z0-9]{0,12}", PROJECT) \
            or os.environ.get("INFRX_E2_NAMESPACE", NAMESPACE) != NAMESPACE:
        raise ValueError(f"INFRX_E5L_PROJECT={PROJECT!r}: an e5l project name (e5l[a-z0-9]*) "
                         "under INFRX_E2_NAMESPACE=e5l")
    if PROJECT == NAMESPACE:
        return
    loaded = sys.modules.get("harness")
    if loaded is not None:
        if loaded.PROJECT != f"infrx-{PROJECT}":
            raise RuntimeError(f"harness already loaded as {loaded.PROJECT}, not infrx-{PROJECT}")
        return
    path = INTEGRATION / "harness.py"
    source = path.read_text()
    for old, new in (('PROJECT = f"infrx-{NAMESPACE}"', f'PROJECT = "infrx-{PROJECT}"'),
                     ('"INFRX_E2_PROJECT": f"infrx-{namespace}"', '"INFRX_E2_PROJECT": PROJECT')):
        if source.count(old) != 1:
            raise RuntimeError(f"harness.py moved: INFRX_E5L_PROJECT's anchor {old!r} is gone")
        source = source.replace(old, new)
    module = types.ModuleType("harness")
    module.__file__ = str(path)
    sys.modules["harness"] = module
    exec(compile(source, str(path), "exec"), module.__dict__)     # noqa: S102 - our own file


load_harness()
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
def trace_stores(trip, workdir: Path, prefix: str | None = None):
    """A ClickHouse database and an object prefix of the trip's own (or `prefix`: the gateway's
    own `infrx/`, which a box reading the bucket needs), and T2I's shipper."""
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
            prefix = prefix or f"{harness.OBJECT_PREFIX}{workdir.name[:40]}-{uuid.uuid4().hex[:6]}/"
            traces.shipper = ship.build_shipper(limits(traces), None, prefix=prefix,
                                                endpoint_url=harness.s3_endpoint())
            assert traces.shipper is not None, "build_shipper refused a complete setting set"
            yield traces
    finally:
        with contextlib.suppress(Exception):
            admin.command(f"DROP DATABASE IF EXISTS {database}")


@contextlib.contextmanager
def observe_trip(workdir: Path, start=("worker", "gateway"), trace_prefix: str | None = None,
                 **env: str):
    """E3C's composed clone (the box processes `start` names) with the Lab seeded and the
    trip's trace stores."""
    with world.composed(workdir, start=start, **env) as trip:
        seed_lab(trip)
        with trace_stores(trip, workdir, trace_prefix) as traces:
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
    for the shipper (the gateway builds no sink on this base; WR-C6-CAPTURE)."""
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
    """A well-formed MARLIN_VIDEO_V1 payload for these text-only samples (J1's `result()` with
    `groundedness=None`): no media, so no groundedness score and no pass (J1's no-media floor).
    A groundedness score here is rejected as `media_dependent_score_without_media`."""
    criterion = {"score": 4, "rationale": "frame 3 shows it"}
    return json.dumps({"relevance": criterion, "completeness": criterion, "format": criterion,
                       "refusal": criterion, "notes": "fine", "overall_pass": False})


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
def judge(trip, *, mode: str = "live", budget: str = "1.00", timeout_s: float = 5.0,
          pg: bool = False, retention=None):
    """J2's wiring on the trip: real access/projection/objects, the local judge fake, J2's
    in-memory D6J ledger with a PROVIDER_USD budget for A's named payer - or, `pg`, D6J's
    `PgJudgeLedger` on the clone (0036: the `lab_submission` flag on, the same budget put
    through `lab_put_budget`). `retention`: T3's reads on another clock (default the trip's)."""
    from infrx.contracts.limits import DEFAULTS
    from infrx.contracts.v2.money_units import ProviderUsd
    from infrx.judge.cost import ProviderRate, StaticRateTable
    from infrx.judge.submit import HttpJudgeProvider, JudgeWiring
    fakes = j2("fakes")
    fake = j2("judge_fake").JudgeFake(JUDGE_PORT)
    try:
        payer = fakes.payer(PROVIDER)
        ledger = fakes.FakeJudgeLedger({payer: ProviderUsd(budget)})
        if pg:
            from psycopg.types.json import Jsonb

            from infrx.state.jobstore import connector
            from infrx.state.lab_consent import PgJudgeLedger
            sql(trip, "insert into infrx.feature_flags (name, enabled, updated_by, reason) "
                      "values ('lab_submission', true, 'e5l', 'E5L o04') on conflict (name) do "
                      "update set enabled = true")
            sql(trip, "select infrx.lab_put_budget(%s)", Jsonb({
                "provider_org_id": PROVIDER, "payer_ref": payer, "limit": budget,
                "actor": "ops@e5l", "reason": "E5L o04"}))
            ledger = PgJudgeLedger(connector(harness.pg_dsn(trip.world.database)))
        rates = StaticRateTable((ProviderRate(**RATE_ROW, effective_at=fakes.T0.replace(
            month=1, day=1)),))
        settings = dataclasses.replace(DEFAULTS, judge_mode=mode,
                                       judge_live_budget_usd=Decimal(budget))
        wiring = JudgeWiring(access=access(trip), ledger=ledger,
                             provider=HttpJudgeProvider(fake.url, timeout_s=timeout_s),
                             retention=retention or trip.traces.retention, rates=rates,
                             settings=settings)
        yield Judge(wiring, ledger, fake, payer)
    finally:
        fake.close()


async def submit(judge: Judge, trip, request_ids, *, job=None):
    """J2's `submit` as the provider's developer: the run it leaves."""
    from infrx.judge.submit import submit as j2_submit
    return await j2_submit(job or judge.job(trip, request_ids), user_id=DEV, wiring=judge.wiring)


def new_id() -> str:
    return str(uuid.uuid4())


# ------------------------------------------------------------------ the Lab routes (LAB-API)

#: The session door in front of the box's PostgREST: e5l 57167 with spares (the block lies in
#: the kernel's ephemeral range, E2's documented limit).
SESSIONS_PORTS = tuple(harness.PORT_RANGE.start + n for n in (67, 68, 69))


def session(user: str) -> str:
    """A signed-in user's Supabase session token (what the Lab web forwards)."""
    return stack.jwt("authenticated", user, ttl_s=3600)


@contextlib.contextmanager
def supabase_door(rest_url: str):
    """The box's `SUPABASE_URL` for the Lab routes. The gateway uses one URL for two servers:
    PostgREST (the REST transport) and GoTrue (`GoTrueSessions`: `GET /auth/v1/user`). This
    stack runs no GoTrue, so the door answers that one path for a live HS256 token of this
    stack's PostgREST secret (E3L's `lab_world.sessions` rule: `aud`/`role` from the token, 401
    otherwise) and forwards every other call unchanged to `rest_url` (the box's own PostgREST,
    over the trip's clone). Yields its URL and `.seen`, the session tokens it judged."""
    import base64
    import errno
    import hashlib
    import hmac
    import threading
    import time
    import types
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import httpx
    upstream = httpx.Client(base_url=rest_url, timeout=30.0)
    seen = []

    def claims(token: str):
        try:
            head, body, sig = token.split(".")
            mac = hmac.new(stack.JWT_SECRET.encode(), f"{head}.{body}".encode(),
                           hashlib.sha256).digest()
            if not hmac.compare_digest(base64.urlsafe_b64encode(mac).rstrip(b"=").decode(), sig):
                return None
            found = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        except (ValueError, TypeError):
            return None
        return found if found.get("exp", 0) > time.time() and found.get("sub") else None

    class Door(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def answer(self, status: int, headers, body: bytes) -> None:
            self.send_response(status)
            for name, value in headers:
                if name.lower() not in ("content-length", "transfer-encoding", "connection",
                                        "content-encoding"):
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def forward(self):
            if self.path == "/auth/v1/user":
                found = claims((self.headers.get("authorization") or "").removeprefix("Bearer "))
                seen.append(bool(found))
                body = json.dumps({"id": found["sub"], "aud": found["role"], "role": found["role"]}
                                  if found else {"msg": "invalid"}).encode()
                return self.answer(200 if found else 401,
                                   [("Content-Type", "application/json")], body)
            length = int(self.headers.get("content-length") or 0)
            got = upstream.request(self.command, self.path, content=self.rfile.read(length),
                                   headers={k: v for k, v in self.headers.items()
                                            if k.lower() not in ("host", "content-length",
                                                                 "connection")})
            self.answer(got.status_code, got.headers.items(), got.content)

        do_GET = do_POST = do_PATCH = do_PUT = do_DELETE = do_HEAD = forward

    for port in SESSIONS_PORTS:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), Door)
            break
        except OSError as busy:
            if busy.errno != errno.EADDRINUSE:
                raise
    else:
        raise RuntimeError(f"address already in use: every one of {SESSIONS_PORTS}")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield types.SimpleNamespace(url=f"http://127.0.0.1:{server.server_address[1]}", seen=seen)
    finally:
        server.shutdown()
        server.server_close()
        upstream.close()


def lab_traces_env(trip, door) -> dict[str, str]:
    """The gateway's LAB_TRACES composition (WR-LAB-API-1) on the trip: the switch, T2I's
    projection and the trace bucket the shipper wrote, and the door as its SUPABASE_URL."""
    return {"LAB_TRACES": "1", "SUPABASE_URL": door.url,
            "CLICKHOUSE_URL": limits(trip.traces).clickhouse_url,
            "S3_TRACE_BUCKET": harness.S3_BUCKET}
