#!/usr/bin/env python3
"""WR-V1M-2: `ClickHouseTraceRows`, the provider read's query on the real projection.

T2I's `trace_envelopes` schema on the ClickHouse image pinned in
`tests/integration/compose.yaml`, on the tasklocal t2i block (57540); a database of its own per
case with merges stopped, so a replayed row stays a physical duplicate and only FINAL hides it.
Skips visibly unless `INFRX_LAB_API_STACK=1`:

    INFRX_LAB_API_STACK=1 uv run --frozen pytest -q tests/g/lab_traces/test_lab_traces_stack.py
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from infrx.contracts import tasklocal
from infrx.gateway.routes import lab_traces as lt
from infrx.traces import ship

STACK = os.environ.get("INFRX_LAB_API_STACK") == "1"
CH_USER, CH_PASSWORD = "infrx_t2i", "infrx-t2i-local"          # task-local literals
MINE = "5e000000-0000-4000-8000-0000000000aa"
OTHER = "5e000000-0000-4000-8000-0000000000bb"
ORG = "c1000000-0000-4000-8000-000000000001"
NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def projection():
    if not STACK:
        pytest.skip("no ClickHouse: start infrx-t2i-clickhouse on the tasklocal t2i block "
                    "and export INFRX_LAB_API_STACK=1")
    import clickhouse_connect
    port = tasklocal.local_services("t2i")["clickhouse"].host_port
    ch = dict(host="127.0.0.1", port=port, username=CH_USER, password=CH_PASSWORD)
    admin = clickhouse_connect.get_client(**ch, database="infrx_t2i")
    database = f"lab_api_{uuid.uuid4().hex}"
    admin.command(f"CREATE DATABASE {database}")
    client = clickhouse_connect.get_client(**ch, database=database)
    try:
        writer = ship.ClickHouseProjection(client)
        run(writer.apply_schema())
        client.command("SYSTEM STOP MERGES trace_envelopes")
        yield writer, lt.ClickHouseTraceRows(client)
    finally:
        admin.command(f"DROP DATABASE IF EXISTS {database}")


def row(n: int, *, serving=MINE, stored=True) -> ship.TraceRow:
    return ship.TraceRow(
        org_id=ORG, trace_id=f"seg-{n:04d}:0", request_id=f"{n:08x}-0000-4000-8000-000000000001",
        key_id="3c3c3c3c-0000-4000-8000-000000000003", mode="full",
        started_at=NOW - timedelta(minutes=n), completed_at=None, loss_reason="none",
        content_complete=True, content_bytes=2 if stored else 0,
        content_key=ship.content_key(ORG, f"seg-{n:04d}:0") if stored else None,
        content_stored=stored, request_schema_version=1, model_revision="m@r1",
        price_version="pv1", serving_version_id=serving, rate_card_version="rc1",
        policy_version="dap1")


def test_lab_traces__the_clickhouse_read_is_final_scoped_and_paged(projection):
    """Oracle: only the given serving versions' rows, newest first, strictly before the cursor
    position, each replayed row once (FINAL, the stored version); a request read is scoped the
    same way."""
    writer, rows = projection
    run(writer.insert([row(1, stored=False), row(2), row(3), row(4, serving=OTHER),
                       row(5, serving=None)]))
    run(writer.insert([row(1)]))                   # the replay that stored the content
    first = run(rows.page((MINE,), None, 2))
    assert [(r.trace_id, r.content_stored) for r in first] == [("seg-0001:0", True),
                                                               ("seg-0002:0", True)]
    before = lt.parse_cursor(lt.mint_cursor(first[-1]))
    assert [r.trace_id for r in run(rows.page((MINE,), before, 2))] == ["seg-0003:0"]
    assert [r.trace_id for r in run(rows.page((MINE, OTHER), None, 10))] \
        == ["seg-0001:0", "seg-0002:0", "seg-0003:0", "seg-0004:0"]
    assert run(rows.request((MINE,), row(4).request_id)) == []
    found = run(rows.request((MINE,), row(1).request_id))
    assert found == [dataclasses.replace(row(1), completed_at=None)]
