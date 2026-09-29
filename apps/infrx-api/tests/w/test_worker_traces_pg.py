"""WR-T-4 on the real services: the worker composed with `TRACE_PUMPS` on runs T3's retention
and T2F's feedback projection, and the gateway's capture (WR-C6-CAPTURE (c): only the gateway
ships, `gateway.capture.build` on the same settings) ships T2I's spool, against PostgreSQL (the t2f D harness: every
migration, the admission seed, the `feedback` flag on), ClickHouse and MinIO (the t2f
block: 57543 / 57545). The composition is `worker_main.compose` from the environment; each
pump's step runs once, as `every` would run it.

    INFRX_D_TASK=t2f INFRX_T2F_STACK=1 uv run --frozen pytest -q tests/w/test_worker_traces_pg.py

Oracles: a segment the worker never ships (or ships without the versions PostgreSQL admitted),
feedback accepted in PostgreSQL that never reaches ClickHouse or stays unacknowledged, or a
retention pass that cannot run on the composed stores. Outside the mutant runner (T2I/N2's
`_pg` pattern); the decisions are killed by `worker_main_mutants.py` on the unit cases.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from infrx.config import from_env
from infrx.contracts.conformance import builders as b
from infrx.contracts.tasklocal import local_services
from infrx.media.store import InMemoryObjectStore
from infrx.scheduling.memory import MemoryScheduler
from infrx.traces import feedback, ship
from infrx.traces.retention import policy
from infrx.traces.spool import segment_names
from infrx.gateway import capture as gateway_capture
from infrx.state.jobstore import connector
from infrx.worker import __main__ as worker_main

from ..d import pgharness
from ..g.ops import pgworld
from ..t.feedback.test_feedback_pg import accept, job, pending_events
from ..t.feedback.test_feedback_pg import world as feedback_world
from ..t.ship.test_ship import capture, durable, spool
from .test_worker_main import environment, utc_now

_reason = ("the t2f key and its stack (INFRX_D_TASK=t2f INFRX_T2F_STACK=1)"
           if (os.environ.get("INFRX_D_TASK"), os.environ.get("INFRX_T2F_STACK")) != ("t2f", "1")
           else pgworld.UNAVAILABLE)
pytestmark = pytest.mark.skipif(_reason is not None, reason=f"WR-T-4 proof needs {_reason}")
# The t2f block's local literals (task-local containers; nothing here is a secret).
CH_USER, CH_PASSWORD = "infrx_t2f", "infrx-t2f-local"
S3_KEY, S3_SECRET, BUCKET = "infrxe2minio", "infrx-e2-local-secret", "infrx-t2f"


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.fixture
def clickhouse():
    """A database of this case's own on the t2f ClickHouse, with the three trace schemas."""
    import clickhouse_connect
    port = local_services("t2f")["clickhouse"].host_port
    admin = clickhouse_connect.get_client(host="127.0.0.1", port=port, username=CH_USER,
                                          password=CH_PASSWORD)
    database = f"wrt4_{uuid.uuid4().hex}"
    admin.command(f"CREATE DATABASE {database}")
    client = clickhouse_connect.get_client(host="127.0.0.1", port=port, username=CH_USER,
                                           password=CH_PASSWORD, database=database)
    for schema in (ship.shipper.SCHEMA, feedback.projector.SCHEMA, policy.SCHEMA):
        client.command(schema.read_text())
    try:
        yield f"http://{CH_USER}:{CH_PASSWORD}@127.0.0.1:{port}/{database}", client
    finally:
        admin.command(f"DROP DATABASE IF EXISTS {database}")


@pytest.fixture
def bucket(monkeypatch):
    """The t2f MinIO, its bucket, and the E2 literals as this process's S3 credentials."""
    import botocore.session
    endpoint = f"http://127.0.0.1:{local_services('t2f')['s3'].host_port}"
    for name, value in (("AWS_ACCESS_KEY_ID", S3_KEY), ("AWS_SECRET_ACCESS_KEY", S3_SECRET),
                        ("AWS_DEFAULT_REGION", "us-east-1")):
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    client = botocore.session.get_session().create_client("s3", endpoint_url=endpoint)
    try:
        client.create_bucket(Bucket=BUCKET)
    except client.exceptions.BucketAlreadyOwnedByYou:
        pass
    return endpoint, client


def test_worker_traces_pg__the_composed_pumps_ship_project_and_retain(tmp_path, monkeypatch,
                                                                      clickhouse, bucket):
    dsn, client = clickhouse
    endpoint, s3 = bucket
    w = feedback_world()
    try:
        credit, admission = run(pgworld.admit_credit(w, f"wrt4-credit-{uuid.uuid4().hex[:8]}"))
        legacy = job(w, f"wrt4-usd-{uuid.uuid4().hex[:8]}")
        feedback_id = accept(w, legacy, f"wrt4-fb-{uuid.uuid4().hex[:8]}")
        # A segment a previous process left sealed in the spool (capture itself stays off).
        directory = tmp_path / "spool"

        async def leave_a_segment():
            sink = spool(directory)
            await capture(sink, credit.request_id, b"wr-t-4 content", org_id=credit.org_id)
            await durable(sink)
            await sink.close(drop_queued=False)
        run(leave_a_segment())
        left = segment_names(directory)
        assert len(left) == 1

        steps = {}

        def every(interval_s, step, what):
            steps[what] = step
            return asyncio.sleep(0)
        monkeypatch.setattr(worker_main, "every", every)
        env = environment(tmp_path, INFRX_MODE="dev", DATABASE_URL=pgharness.dsn(w.database),
                          TRACE_PUMPS="1", TRACE_SPOOL_DIR=str(directory), CLICKHOUSE_URL=dsn,
                          S3_TRACE_BUCKET=BUCKET, S3_ENDPOINT_URL=endpoint)
        service, _ = worker_main.compose(from_env(env), objects=InMemoryObjectStore(),
                                         index=MemoryScheduler(utc_now))
        for name in ("trace_retention", "feedback_projection"):
            run(service.housekeeping[name]())
        assert set(steps) == {"trace retention", "feedback projection"}
        gateway = gateway_capture.build(from_env(env), connector(pgharness.dsn(w.database)))

        report = run(gateway.ship_once())[0]
        assert (report.shipped, report.rows, report.held) == (1, 1, {}), report
        [row] = run(ship.ClickHouseProjection(client).find(credit.org_id, credit.request_id))
        assert (row.serving_version_id, row.rate_card_version, row.policy_version) == (
            admission.pins.serving_version_id, admission.pins.rate_card_version,
            admission.pins.policy_version)
        assert row.content_stored and row.content_key.startswith(f"trace/{credit.org_id}/")
        stored = s3.get_object(Bucket=BUCKET, Key=f"infrx/{row.content_key}")["Body"].read()
        assert stored == b"wr-t-4 content"
        assert not set(left) & set(segment_names(directory))    # acked: unlinked whole

        assert pending_events(w) == 1
        assert run(steps["feedback projection"]()) == {"read": 1, "projected": 1,
                                                        "acknowledged": 1, "orphaned": 0}
        [projected] = run(feedback.ClickHouseFeedbackProjection(client).find(b.ORG_A, legacy))
        assert projected.feedback_id == feedback_id and pending_events(w) == 0

        assert run(steps["trace retention"]()) == {"cleaned": 0, "held": 0, "failed": 0}
        assert run(gateway.ship_once())[0].rows == 0         # nothing left to ship
        run(gateway.close())
    finally:
        w.owner.close()
