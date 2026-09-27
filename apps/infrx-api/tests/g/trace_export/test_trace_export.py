#!/usr/bin/env python3
"""G4T: `GET /v1/traces` over C2's `OwnedExport` - TRACE-TENANT and LAB-ACCESS, the route half.

    uv run --frozen pytest -q tests/g/trace_export

The export runs over T2I's projection and T3's tombstones in memory (`tests/content/world`),
on one clock; `ClickHouseTracePages` is checked for its tenant binding against a recording
client. The real-ClickHouse page runs at the coordinator's integration (no g4t ClickHouse).
"""
from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from infrx.content import EXPORT_MAX_LIMIT, ClickHouseTracePages, OwnedExport
from infrx.contracts import errors, wire
from infrx.contracts.records import ContentState
from infrx.gateway.routes import trace_export
from infrx.traces import ship
from infrx.traces.retention import Retention
from tests.content.world import Tombstones, Traces

from .. import support

ORG, OTHER = support.ORG, "9e9e9e9e-0000-4000-8000-000000000009"
PATH = trace_export.EXPORT_PATH
T0 = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)


class World:
    def __init__(self, now: datetime = T0 + timedelta(days=1)) -> None:
        self.now = now
        self.traces, self.tombstones = Traces(), Tombstones()
        self.retention = Retention(self.tombstones, self.traces, None, None,
                                   clock=lambda: self.now)
        self.export = OwnedExport(self.traces, self.retention)
        self.calls: list[str] = []
        page = self.export.page

        async def recorded(org_id, **kw):
            self.calls.append(org_id)
            return await page(org_id, **kw)
        self.export.page = recorded

    def row(self, org=ORG, n=0, *, content=True, stored=True, at=None) -> ship.TraceRow:
        request = f"4d4d4d4d-0000-4000-8000-{n:012d}"
        trace_id = f"seg-{n:04d}:0"
        row = ship.TraceRow(
            org_id=org, trace_id=trace_id, request_id=request, key_id=support.KEY,
            mode="full" if content else "minimal", started_at=at or T0 + timedelta(seconds=n),
            completed_at=None, loss_reason="none", content_complete=content,
            content_bytes=10 if content else 0,
            content_key=ship.content_key(org, trace_id) if content else None,
            content_stored=content and stored, request_schema_version=1,
            model_revision=support.MODEL_REVISION, price_version="pv_2026_09_01",
            serving_version_id=None, rate_card_version=None, policy_version=None)
        self.traces.rows[(org, trace_id)] = row
        return row


def mounted(export=None, *, on_runtime=False, rows=None):
    rt = support.runtime(support.settings(), sb=identities(rows or {support.TOKEN: support.ROW}))
    app = FastAPI()
    if on_runtime:
        rt.trace_export = export
        return app, trace_export.register(app, rt)
    return app, trace_export.register(app, rt, export=export)


def identities(rows):
    import hashlib
    by_hash = {hashlib.sha256(token.encode()).hexdigest(): row for token, row in rows.items()}

    def handler(request):
        key_hash = request.url.params.get("key_hash", "").removeprefix("eq.")
        return httpx.Response(200, json=[by_hash[key_hash]] if key_hash in by_hash else [])

    return httpx.AsyncClient(base_url="https://fake.supabase.co/rest/v1",
                             transport=httpx.MockTransport(handler))


def get(app, params=None, *, token=support.TOKEN, query: str | None = None):
    headers = {"authorization": f"Bearer {token}"} if token else {}
    url = PATH + (query or "")

    async def main():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://gateway.test") as client:
            return await client.get(url, params=params, headers=headers)

    return asyncio.run(main())


def code(response) -> str | None:
    return (response.json().get("error") or {}).get("code")


def requests(response) -> list[str]:
    return [row["request_id"] for row in response.json()["data"]]


@pytest.fixture
def w():
    return World()


# ------------------------------------------------------------------ enablement ---
def test_trace_export__no_export_mounts_no_route():
    """Off by default: nothing mounted, and an unknown path is the contract's 404."""
    app, export = mounted(None)
    assert export is None
    answer = get(app)
    assert answer.status_code == 404 and code(answer) == "not_found"


def test_trace_export__the_route_mounts_over_the_runtime_export(w):
    row = w.row()
    app, export = mounted(w.export, on_runtime=True)
    assert export is w.export
    answer = get(app)
    assert answer.status_code == 200 and requests(answer) == [row.request_id]
    assert answer.headers.get(wire.HEADER_INFERENCE_ID)


# ------------------------------------------------------------------ identity ---
def test_trace_export__identity_comes_before_any_read(w):
    """Oracle: no key, or an unknown key, is a 401 and the export is never asked."""
    app, _ = mounted(w.export)
    for token in (None, "sk-infrx-unknown"):
        answer = get(app, token=token)
        assert answer.status_code == 401 and code(answer) == "invalid_api_key"
    assert w.calls == []


def test_trace_export__an_operator_credential_owns_no_traces(w):
    app, _ = mounted(w.export, rows={support.TOKEN: support.OPERATOR_ROW})
    answer = get(app)
    assert answer.status_code == 403 and code(answer) == "forbidden" and w.calls == []


# ------------------------------------------------------------------ the tenant ---
def test_trace_export__an_org_exports_its_own_traces_only(w):
    """TRACE-TENANT: another org's rows never appear, and an org named in the query is a
    400 before any read (the organization is the key's)."""
    mine = [w.row(n=n).request_id for n in range(3)]
    w.row(OTHER, n=9)
    app, _ = mounted(w.export)
    answer = get(app)
    assert requests(answer) == mine and w.calls == [ORG]
    assert {row["org_id"] for row in answer.json()["data"]} == {ORG}
    for params in ({"org_id": OTHER}, {"limit": "5", "org": OTHER}):
        refused = get(app, params)
        assert refused.status_code == 400 and code(refused) == "invalid_request"
    assert w.calls == [ORG]


def test_trace_export__a_foreign_row_from_the_store_is_dropped(w):
    """Defence in depth: a page that returns another org's row does not export it."""
    mine = w.row(n=1)
    foreign = w.row(OTHER, n=2)
    real = w.traces.page

    async def leaky(org_id, **kw):
        return [*await real(org_id, **kw), foreign]
    w.traces.page = leaky
    assert requests(get(mounted(w.export)[0])) == [mine.request_id]


# ------------------------------------------------------------------ retention ---
def test_trace_export__deleted_and_expired_data_is_not_exported(w):
    """TRACE-TENANT + T3: a deleted request is gone, metadata past 13 months is gone, and
    content past its bound is `expired` (the row stays, its content does not)."""
    kept, deleted = w.row(n=1), w.row(n=2)
    old = w.row(n=3, at=w.now - timedelta(days=400))
    stale = w.row(n=4, at=w.now - timedelta(days=w.retention.content_days))
    asyncio.run(w.retention.delete(ORG, deleted.request_id, "customer"))
    data = get(mounted(w.export)[0]).json()["data"]
    assert [r["request_id"] for r in data] == [stale.request_id, kept.request_id]
    assert [r["content_state"] for r in data] == ["expired", "available"]
    assert old.request_id not in json.dumps(data)


def test_trace_export__rows_carry_no_storage_key_and_no_content(w):
    """R47: availability only - no key, no handle, no content; lost and metadata-only rows
    say so."""
    w.row(n=1)
    w.row(n=2, stored=False)
    w.row(n=3, content=False)
    answer = get(mounted(w.export)[0])
    data = answer.json()["data"]
    assert [r["content_state"] for r in data] == [ContentState.available, ContentState.lost,
                                                  ContentState.metadata_only]
    assert all(r["content_handle"] is None and r["content"] is None for r in data)
    assert ship.CONTENT_PREFIX not in answer.text and "content_key" not in answer.text
    for row in data:
        wire.TraceExport.model_validate(row)


# ------------------------------------------------------------------ pagination ---
def test_trace_export__the_cursor_walks_every_row_once_in_order(w):
    rows = [w.row(n=n).request_id for n in range(7)]
    app, _ = mounted(w.export)
    seen, cursor, pages = [], None, 0
    while True:
        answer = get(app, {"limit": "3", **({"cursor": cursor} if cursor else {})})
        seen += requests(answer)
        cursor, pages = answer.json()["next_cursor"], pages + 1
        if cursor is None:
            break
    assert seen == rows and pages == 3


def test_trace_export__the_page_is_bounded(w):
    """Oracle: the default is 100, the cap 1000; 0, 1001, a word and a repeat are a 400."""
    for n in range(101):
        w.row(n=n)
    app, _ = mounted(w.export)
    first = get(app)
    assert len(requests(first)) == 100 and first.json()["next_cursor"] is not None
    assert len(requests(get(app, {"limit": str(EXPORT_MAX_LIMIT)}))) == 101
    for query in ("?limit=0", f"?limit={EXPORT_MAX_LIMIT + 1}", "?limit=ten", "?limit=-1",
                  "?limit=5&limit=6"):
        refused = get(app, query=query)
        assert refused.status_code == 400 and code(refused) == "invalid_request", query


def test_trace_export__a_bad_cursor_is_a_400(w):
    app, _ = mounted(w.export)
    naive = base64.urlsafe_b64encode(json.dumps(["2026-09-20T12:00:00", "x"]).encode()).decode()
    for cursor in ("not-a-cursor", naive, base64.urlsafe_b64encode(b"[1, 2]").decode()):
        refused = get(app, {"cursor": cursor})
        assert refused.status_code == 400 and code(refused) == "invalid_cursor", cursor


def test_trace_export__the_window_filters_by_start(w):
    rows = [w.row(n=n) for n in range(5)]
    app, _ = mounted(w.export)
    answer = get(app, {"since": rows[1].started_at.isoformat(),
                       "until": rows[3].started_at.isoformat().replace("+00:00", "Z")})
    assert requests(answer) == [rows[1].request_id, rows[2].request_id]
    for params in ({"since": "yesterday"}, {"until": "2026-09-20T12:00:00"}):
        refused = get(app, params)
        assert refused.status_code == 400 and code(refused) == "invalid_request"


def test_trace_export__a_hanging_store_is_a_503_within_the_bound(w, monkeypatch):
    """E3C s08: the store call is bounded by DEPENDENCY_BOUND_S, never a hang."""
    from infrx.gateway.routes import intake
    monkeypatch.setattr(intake, "DEPENDENCY_BOUND_S", 0.05)

    async def hang(org_id, **kw):
        await asyncio.sleep(3600)
    w.traces.page = hang
    app, _ = mounted(w.export)

    async def main():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://gateway.test") as client:
            return await client.get(PATH, headers=support.AUTH)
    try:
        answer = asyncio.run(asyncio.wait_for(main(), 5))
    except TimeoutError:
        answer = None
    assert answer is not None and answer.status_code == 503
    assert code(answer) == "dependency_unavailable"


def test_trace_export__a_store_outage_is_a_503(w):
    """Oracle: a projection or a tombstone outage is a 503 - never a page that skipped T3."""
    w.row()

    async def down(*args, **kw):
        raise errors.DependencyUnavailable("clickhouse did not answer")
    for store, method in ((w.traces, "page"), (w.tombstones, "get")):
        original = getattr(store, method)
        setattr(store, method, down)
        answer = get(mounted(w.export)[0])
        assert answer.status_code == 503 and code(answer) == "dependency_unavailable", method
        setattr(store, method, original)


# ------------------------------------------------------------------ ClickHouse ---
class Recording:
    def __init__(self) -> None:
        self.queries: list[tuple[str, dict]] = []

    def query(self, sql, parameters):
        self.queries.append((sql, parameters))
        return SimpleNamespace(result_rows=[])


def test_trace_export__the_clickhouse_page_binds_the_org_and_every_bound_as_parameters():
    """TRACE-TENANT: the org, the cursor, the window and the limit are query parameters,
    never text; the order is the cursor's."""
    client = Recording()
    after = (T0, "seg-0001:0")
    asyncio.run(ClickHouseTracePages(client).page(ORG, after=after, since=T0, until=T0,
                                                  limit=7))
    sql, parameters = client.queries[0]
    assert "FINAL WHERE org_id = {org:UUID} AND (started_at, trace_id) > " in sql
    assert "started_at >= toDateTime64({since:String}" in sql
    assert "started_at < toDateTime64({until:String}" in sql
    assert sql.endswith("ORDER BY started_at, trace_id LIMIT {limit:UInt32}")
    assert parameters["org"] == ORG and parameters["limit"] == 7
    assert parameters["trace"] == "seg-0001:0" and ORG not in sql and "seg-0001" not in sql
