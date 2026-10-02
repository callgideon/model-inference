#!/usr/bin/env python3
"""AP-07b/07d on the lane's own stack (key ap7: PostgreSQL 57559, ClickHouse 57560/57561,
MinIO 57562): consent decided through the data-use API reaches the gateway's capture, sync,
SSE and async requests become one inspectable Lab trace each, capture off leaves none, and the
delivery faults (a duplicate delivery, a trace-store outage) still end in exactly one logical
projection while every answer reaches its caller.

    docker run -d --name infrx-ap7-clickhouse -p 127.0.0.1:57560:8123 -p 127.0.0.1:57561:9000 \\
        -e CLICKHOUSE_USER=infrx_ap7 -e CLICKHOUSE_PASSWORD=infrx-ap7-local \\
        clickhouse/clickhouse-server:25.8.33.6-alpine
    docker run -d --name infrx-ap7-s3 -p 127.0.0.1:57562:9000 -e MINIO_ROOT_USER=infrxap7minio \\
        -e MINIO_ROOT_PASSWORD=infrx-ap7-local-secret pgsty/minio:latest server /data
    INFRX_D_TASK=ap7 INFRX_AP7_STACK=1 uv run --frozen pytest -q tests/ap07/test_trace_stack.py

The pieces are the production ones: `routes.console_data_use` over `DataUse`, the gateway's
`capture.build` (ConsentSource on 0057, the spool, T2I's shipper to ClickHouse + the bucket),
the worker's `JobCapture`, and the Lab read as `lab.compose.lab_traces` builds it. Outside the
mutant runner (the `_stack` pattern): the decisions die on the cases of `test_data_use.py` and
`test_trace_reads.py`; this is the proof they compose. The engine is not part of it: each
answer is the relay's shape handed to the capture hook (`tests/t/capture`'s `Streamed`).
"""
from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from infrx.config import from_env
from infrx.contracts.conformance import builders as b
from infrx.contracts.records import ExecutionMode, TraceMode
from infrx.contracts.tasklocal import local_services
from infrx.gateway import capture
from infrx.gateway.routes import console_data_use as routes
from infrx.gateway.routes import lab_traces as lt
from infrx.lab import compose
from infrx.state.jobstore import PgJobStore, connector
from infrx.traces import ship

from ..d import checks_admission as ca
from ..d import pgharness
from ..g.lab_traces.test_lab_traces import Sessions, token
from ..l.access.conftest import CASE
from ..t.capture.test_capture import ANSWER, FRAMES, Streamed
from ..w.test_worker_main import environment
from .test_data_use import add_key, client, grant_body, put_capture, session

STACK = (os.environ.get("INFRX_D_TASK"), os.environ.get("INFRX_AP7_STACK")) == ("ap7", "1")
pytestmark = [pytest.mark.pg, pytest.mark.skipif(
    not STACK, reason="AP-07's stack needs INFRX_D_TASK=ap7 INFRX_AP7_STACK=1 and its containers")]
CH_USER, CH_PASSWORD = "infrx_ap7", "infrx-ap7-local"
S3_KEY, S3_SECRET, BUCKET = "infrxap7minio", "infrx-ap7-local-secret", "infrx-ap7-traces"
TEXT = "the van is red"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def clickhouse():
    """A database of this case's own on the ap7 ClickHouse, with the three trace schemas."""
    import clickhouse_connect

    from infrx.traces import feedback
    from infrx.traces.retention import policy
    port = local_services("ap7")["clickhouse"].host_port
    admin = clickhouse_connect.get_client(host="127.0.0.1", port=port, username=CH_USER,
                                          password=CH_PASSWORD)
    database = f"ap7_{uuid.uuid4().hex}"
    admin.command(f"CREATE DATABASE {database}")
    client_ = clickhouse_connect.get_client(host="127.0.0.1", port=port, username=CH_USER,
                                            password=CH_PASSWORD, database=database)
    for schema in (ship.shipper.SCHEMA, feedback.projector.SCHEMA, policy.SCHEMA):
        client_.command(schema.read_text())
    try:
        yield f"http://{CH_USER}:{CH_PASSWORD}@127.0.0.1:{port}/{database}", client_
    finally:
        admin.command(f"DROP DATABASE IF EXISTS {database}")


@pytest.fixture
def bucket(monkeypatch):
    import botocore.session
    endpoint = f"http://127.0.0.1:{local_services('ap7')['s3'].host_port}"
    for name, value in (("AWS_ACCESS_KEY_ID", S3_KEY), ("AWS_SECRET_ACCESS_KEY", S3_SECRET),
                        ("AWS_DEFAULT_REGION", "us-east-1")):
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    s3 = botocore.session.get_session().create_client("s3", endpoint_url=endpoint)
    try:
        s3.create_bucket(Bucket=BUCKET)
    except s3.exceptions.BucketAlreadyOwnedByYou:
        pass
    return endpoint


@pytest.fixture
def stack(pg_world, clickhouse, bucket, tmp_path):
    """The settings every piece reads, the gateway's capture over them, and the world."""
    dsn, ch = clickhouse
    env = environment(tmp_path, INFRX_MODE="dev", DATABASE_URL=pgharness.dsn(CASE),
                      TRACE_PUMPS="1", TRACE_SPOOL_DIR=str(tmp_path / "spool"),
                      CLICKHOUSE_URL=dsn, S3_TRACE_BUCKET=BUCKET, S3_ENDPOINT_URL=bucket)
    settings = from_env(env)
    gateway = capture.build(settings, connector(pgharness.dsn(CASE)))
    try:
        yield SimpleNamespace(w=pg_world, settings=settings, gateway=gateway, ch=ch,
                              spool=tmp_path / "spool")
    finally:
        run(gateway.close())


def consented(s) -> tuple[str, str]:
    """C1's owner turns capture on for one key (the other stays off) and grants provider A
    both content categories for provider_sharing - through the data-use routes only."""
    w = s.w
    owner = client(session(w.BOTH, w.C1))
    off_key = add_key(w, w.C1, w.BOTH)
    assert put_capture(owner, ca.C1_KEY, "full", 0).status_code == 200
    granted = owner.post(routes.GRANTS_PATH, json=grant_body(
        w, purposes=["provider_sharing"], categories=["request_content", "response_content"]))
    assert granted.status_code == 201, granted.text
    return ca.C1_KEY, off_key


def admitted(s, key: str, tag: str, mode: ExecutionMode = ExecutionMode.sync):
    """A request admitted in PostgreSQL (its pins are the shipper's), under the policy the
    gateway reads for its key now."""
    w = s.w
    request = ca.credit_request(ca.World(w.conn), key, w.C1)
    run(PgJobStore(connector(pgharness.dsn(CASE))).admit_credit(request, b.idem(request, tag)))
    policy = run(s.gateway.policy(SimpleNamespace(org_id=w.C1, key_id=key), w.now()))
    return request.model_copy(update={"trace_policy": policy, "execution_mode": mode})


def answered(s, request, response) -> bytes:
    """The answer through the gateway's capture hook, as the caller receives it."""
    sent: list[dict] = []

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)
    run(s.gateway.response(response, request, {})({"type": "http"}, receive, send))
    return b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


def lab(s) -> TestClient:
    w = s.w
    traces = compose.lab_traces(s.settings, connector(pgharness.dsn(CASE)), Sessions(w), w.access)
    app = FastAPI()
    lt.register(app, SimpleNamespace(), traces)
    return TestClient(app, raise_server_exceptions=False)


def lab_get(c, w, path=""):
    answer = c.get(lt.TRACES_PATH + path, params={"provider_org_id": w.A},
                   headers={"authorization": f"Bearer {token(w.DEV_A)}"})
    assert answer.status_code == 200, answer.text
    return answer.json()


def shipped_rows(s) -> int:
    return sum(report.rows for report in run(s.gateway.ship_once()))


def test_trace_stack__captured_requests_reach_the_lab_once_and_capture_off_leaves_nothing(stack):
    """Oracle (07b/07d): sync, SSE and async requests on the consenting key are one Lab row
    each with the content the caller received; the off key's request is answered and leaves
    no trace at all; a duplicate delivery of the same rows still lists one; revoking the
    grant between the list and the detail withholds the content."""
    s, w = stack, stack.w
    on, off = consented(s)
    sync, sse = admitted(s, on, "ap7-sync"), admitted(s, on, "ap7-sse")
    later = admitted(s, on, "ap7-async", ExecutionMode.async_)
    quiet = admitted(s, off, "ap7-off")
    assert (sync.trace_policy.trace_mode, quiet.trace_policy.trace_mode) \
        == (TraceMode.full, TraceMode.off)
    assert b"the van is red" in answered(s, sync, JSONResponse(ANSWER))
    assert answered(s, sse, Streamed()) == b"".join(FRAMES)
    assert b"the van is red" in answered(s, quiet, JSONResponse(ANSWER))
    worker = capture.JobCapture(None, None, s.spool, capture.Wall)
    run(worker.spool(later, TEXT))
    assert shipped_rows(s) == 3
    c = lab(s)
    items = {item["request_id"]: item for item in lab_get(c, w)["data"]}
    assert set(items) == {sync.request_id, sse.request_id, later.request_id}
    assert {item["access_state"] for item in items.values()} == {"content"}
    assert all(item["elapsed_ms"] is None or item["elapsed_ms"] >= 0 for item in items.values())
    contents = {r: lab_get(c, w, "/" + r)["content"] for r in items}
    assert '"the van is red"' in contents[sync.request_id] or "the van is red" in \
        contents[sync.request_id]
    assert contents[sse.request_id].endswith(b"".join(FRAMES).decode())
    assert contents[later.request_id].endswith(TEXT)
    rows = [row for r in items for row in run(ship.ClickHouseProjection(s.ch).find(w.C1, r))]
    run(ship.ClickHouseProjection(s.ch).insert(rows))          # the same rows delivered again
    assert sorted(i["request_id"] for i in lab_get(c, w)["data"]) == sorted(items)
    owner = client(session(w.BOTH, w.C1))
    [grant] = [g for g in owner.get(routes.GRANTS_PATH).json()["data"] if g["provider_org_id"] == w.A]
    assert owner.delete(f"{routes.GRANTS_PATH}/{grant['grant_id']}").status_code == 200
    detail = lab_get(c, w, "/" + sync.request_id)
    assert detail["access_state"] == "revoked" and "content" not in detail


def test_trace_stack__a_trace_store_outage_holds_the_record_and_ships_it_once_after(stack):
    """Oracle (07d): with the projection unwritable the caller's answer is whole, the record
    is held (never dropped, never half-listed), and once the store is back it ships once."""
    s, w = stack, stack.w
    on, _ = consented(s)
    sync = admitted(s, on, "ap7-outage")
    s.ch.command("RENAME TABLE trace_envelopes TO trace_envelopes_away")
    try:
        assert b"the van is red" in answered(s, sync, JSONResponse(ANSWER))
        reports = run(s.gateway.ship_once())
        assert sum(r.rows for r in reports) == 0 and any(r.held for r in reports), reports
    finally:
        s.ch.command("RENAME TABLE trace_envelopes_away TO trace_envelopes")
    assert shipped_rows(s) == 1
    assert shipped_rows(s) == 0, "an acknowledged segment shipped again"
    [item] = lab_get(lab(s), w)["data"]
    assert (item["request_id"], item["access_state"]) == (sync.request_id, "content")
