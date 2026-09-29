"""WR-C6-CAPTURE on the real services (t2f): consent from PostgreSQL, an async job's output
spooled by the worker's `JobCapture` and shipped by the gateway's `build` composition to real
ClickHouse and MinIO with the pins PostgreSQL admitted - found by its own organization only.
(The sync hook end to end is E5L o01's switch case on the e5l box.)

    INFRX_D_TASK=t2f INFRX_T2F_STACK=1 uv run --frozen pytest -q tests/t/capture/test_capture_stack.py

Outside the mutant runner (the T2I/N2 `_pg` pattern: the decisions are killed on the fake
cases of `test_capture.py`); this is the proof the pieces compose on the real stores.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest

from infrx.config import from_env
from infrx.contracts.conformance import builders as b
from infrx.contracts.records import ExecutionMode, TraceMode
from infrx.gateway import capture
from infrx.state.jobstore import connector
from infrx.traces import ship

from ...d import pgharness
from ...g.ops import pgworld
from ...w.test_worker_traces_pg import BUCKET, bucket, clickhouse  # noqa: F401 - fixtures
from ...w.test_worker_main import environment
from .test_capture_pg import consent, opt_in

_reason = ("the t2f key and its stack (INFRX_D_TASK=t2f INFRX_T2F_STACK=1)"
           if (os.environ.get("INFRX_D_TASK"), os.environ.get("INFRX_T2F_STACK")) != ("t2f", "1")
           else pgworld.UNAVAILABLE)
pytestmark = pytest.mark.skipif(_reason is not None, reason=f"WR-C6-CAPTURE needs {_reason}")
TEXT = "the van is red"


def run(coroutine):
    return asyncio.run(coroutine)


def test_capture_stack__consented_output_ships_once_with_its_pins_to_its_org_only(
        tmp_path, clickhouse, bucket):
    dsn, client = clickhouse
    endpoint, s3 = bucket
    w = pgworld.world("capture_stack")
    try:
        request, admission = run(pgworld.admit_credit(w, f"cap-{uuid.uuid4().hex[:8]}"))
        org, key = request.org_id, request.key_id
        consent(w, org, "full")
        opt_in(w, key, "full")
        env = environment(tmp_path, INFRX_MODE="dev", DATABASE_URL=pgharness.dsn(w.database),
                          TRACE_PUMPS="1", TRACE_SPOOL_DIR=str(tmp_path / "spool"),
                          CLICKHOUSE_URL=dsn, S3_TRACE_BUCKET=BUCKET, S3_ENDPOINT_URL=endpoint)
        gateway = capture.build(from_env(env), connector(pgharness.dsn(w.database)))
        policy = run(gateway.policy(SimpleNamespace(org_id=org, key_id=key), request.created_at))
        assert policy.trace_mode is TraceMode.full
        job = request.model_copy(update={"trace_policy": policy,
                                         "execution_mode": ExecutionMode.async_})
        worker = capture.JobCapture(None, None, tmp_path / "spool", capture.Wall)
        run(worker.spool(job, TEXT))
        [report] = [r for r in run(gateway.ship_once()) if r.rows]
        assert (report.shipped, report.rows, report.held) == (1, 1, {}), report
        [row] = run(ship.ClickHouseProjection(client).find(org, request.request_id))
        assert (row.serving_version_id, row.rate_card_version) == (
            admission.pins.serving_version_id, admission.pins.rate_card_version)
        assert row.mode == "full" and row.content_stored
        stored = s3.get_object(Bucket=BUCKET, Key=f"infrx/{row.content_key}")["Body"].read()
        assert stored == capture.request_line(job) + TEXT.encode()
        assert run(ship.ClickHouseProjection(client).find(b.ORG_B, request.request_id)) == []
        assert list((tmp_path / "spool" / capture.JOBS_DIR).iterdir()) == []
        assert all(r.rows == 0 for r in run(gateway.ship_once()))
        run(gateway.close())
    finally:
        w.owner.close()
