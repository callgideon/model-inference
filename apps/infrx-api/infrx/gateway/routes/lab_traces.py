"""WR-V1M-2: `/lab/v1/traces`, a provider's read of the requests on its own deployments.

    GET /lab/v1/traces?provider_org_id=&limit=&cursor=     -> {data: [item], next_cursor}
    GET /lab/v1/traces/{request_id}?provider_org_id=       -> item (+ content when granted)

In this order, on every call and with nothing cached:

1. the session and a current developer+ membership of the provider (`lab_auth`; a viewer
   holds aggregate health only, so per-request rows are a 403);
2. the provider's own serving versions (`ProviderServing`, the registry): only rows whose
   `serving_version_id` is one of them are ever read - another provider's requests are
   unreachable, not filtered;
3. T3 first: a deleted request (a `request` tombstone) or one past its metadata bound is not
   there; content past its bound, or under a `content` tombstone, is unavailable;
4. per grantor organization and model, `LabAccess.authorize_content` for BOTH content
   categories (the stored object holds the request and the response) and `provider_sharing`.
   Without it the row is **metadata only**: no organization, key, size or content.

Rows come from T2I's projection (`TraceRows`; `ClickHouseTraceRows` over `trace_envelopes`
FINAL) in bounded pages, newest first, behind a cursor the server mints. Mounted only when the
composition put a `LabTraces` on `rt.lab_traces` (LAB_TRACES, off).
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Protocol, Sequence

from fastapi import Request

from ...contracts import errors
from ...contracts.v2.records import DataCategory, DataPurpose
from ...contracts.v2.records import ProviderCapability as Cap
from ...traces.retention import CONTENT, REQUEST
from ...state.lab_data import grant_ref
from ...traces.ship.shipper import COLUMNS, TABLE, TraceRow, _row
from .. import lab_auth

TRACES_PATH = "/lab/v1/traces"
DEFAULT_LIMIT, MAX_LIMIT = 50, 200
CATEGORIES = (DataCategory.request_content, DataCategory.response_content)
#: What any member developer sees of a request on its deployment.
METADATA = ("request_id", "started_at", "completed_at", "mode", "loss_reason",
            "serving_version_id", "model_revision", "rate_card_version", "policy_version")
#: What a current grant from the request's organization adds; `grant_ref` names the grant version
#: the row was read under (`lab_data.grant_ref`), so a C2 content ref can be bound to it (WR-V2-1).
GRANTED = ("grantor_org_id", "grant_ref", "content_complete", "content_bytes", "content_available")


class ProviderServing(Protocol):
    async def serving(self, provider_org_id: str) -> Mapping[str, str]:
        """The provider's serving versions -> each one's model id (the grant's model)."""


class TraceRows(Protocol):
    """T2I's projection, read FINAL, restricted to the given serving versions."""

    async def page(self, serving_version_ids: Sequence[str],
                   before: tuple[datetime, str] | None, limit: int) -> Sequence[TraceRow]:
        """Newest first by (started_at, trace_id), strictly before `before`."""

    async def request(self, serving_version_ids: Sequence[str],
                      request_id: str) -> Sequence[TraceRow]: ...


@dataclass(frozen=True)
class LabTraces:
    sessions: lab_auth.Sessions
    access: object                    # infrx.lab.access.LabAccess
    serving: ProviderServing
    rows: TraceRows
    retention: object                 # infrx.traces.retention.Retention (T3)


class PgServing:
    """The registry's serving versions of one provider (0007; read as the platform role).
    ponytail: a direct read like T2I's `PgPins`; a lab-sql RPC if the registry closes it."""

    SQL = ("select serving_version_id::text, model_id::text from infrx.serving_versions "
           "where provider_org_id = %s")

    def __init__(self, connect) -> None:
        self.connect = connect

    async def serving(self, provider_org_id: str) -> dict[str, str]:
        from ...traces.ship.pins import pg_rows
        return dict(await pg_rows(self.connect, self.SQL, (provider_org_id,)))


class ClickHouseTraceRows:
    """`trace_envelopes` FINAL (T2I's schema), serving versions and ids bound as parameters.
    ponytail: the table's key leads with org_id, so this scans; a serving_version_id skip
    index when a provider's reads outgrow that."""

    def __init__(self, client) -> None:
        self.client = client

    async def _select(self, where: str, serving_version_ids, limit: int, **parameters):
        result = await asyncio.to_thread(
            self.client.query,
            f"SELECT {', '.join(COLUMNS)} FROM {TABLE} FINAL "
            "WHERE serving_version_id IN {serving:Array(UUID)} AND " + where
            + " ORDER BY started_at DESC, trace_id DESC LIMIT {limit:UInt32}",
            parameters={"serving": list(serving_version_ids), "limit": limit, **parameters})
        return [_row(values) for values in result.result_rows]

    async def page(self, serving_version_ids, before, limit):
        if before is None:
            return await self._select("1", serving_version_ids, limit)
        at, trace_id = before
        return await self._select(
            "(started_at, trace_id) < (toDateTime64({at:String}, 6, 'UTC'), {trace:String})",
            serving_version_ids, limit, at=at.strftime("%Y-%m-%d %H:%M:%S.%f"), trace=trace_id)

    async def request(self, serving_version_ids, request_id):
        return await self._select("request_id = {request:UUID}", serving_version_ids, 100,
                                  request=request_id)


def mint_cursor(row: TraceRow) -> str:
    """Opaque to the Lab (R36): the last row's position, never its organization.
    ponytail: two rows with the same instant AND trace id in different organizations would
    share a position; trace ids are unique per spool segment, so none do today."""
    raw = json.dumps([row.started_at.isoformat(), row.trace_id]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def parse_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        at, trace_id = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        position = datetime.fromisoformat(at), trace_id
        if position[0].tzinfo is None or not isinstance(trace_id, str):
            raise ValueError
        return position
    except (ValueError, TypeError, binascii.Error):
        raise errors.InvalidCursor("not a cursor this list issued") from None


def parse_limit(value: str | None) -> int:
    try:
        limit = DEFAULT_LIMIT if value is None else int(value)
    except ValueError:
        raise errors.InvalidRequest("limit is an integer") from None
    if not 1 <= limit <= MAX_LIMIT:
        raise errors.InvalidRequest(f"limit is 1 to {MAX_LIMIT}")
    return limit


def register(app, rt, traces: LabTraces | None = None):
    """Mount the trace routes over `traces` (default `rt.lab_traces`); without one nothing
    is mounted and `None` is returned."""
    traces = traces if traces is not None else getattr(rt, "lab_traces", None)
    if traces is None:
        return None

    async def reader(request: Request) -> tuple[str, str, Mapping[str, str]]:
        user_id = await lab_auth.authenticate(request, traces.sessions)
        provider = request.query_params.get("provider_org_id", "")
        await lab_auth.member(traces.access, user_id, provider, Cap.manage_dev_deployment)
        return user_id, provider, await traces.serving.serving(provider)

    async def granted(user_id, provider, org_id, model_id) -> str | None:
        """The ref of the current grant allowing both categories, or None."""
        try:
            for category in CATEGORIES:
                grant = await traces.access.authorize_content(
                    user_id=user_id, provider_org_id=provider, grantor_org_id=org_id,
                    model_id=model_id, category=category, purpose=DataPurpose.provider_sharing)
        except errors.Forbidden:
            return None
        return grant_ref(grant)

    async def visible(user_id, provider, serving, rows) -> list[tuple[TraceRow, dict]]:
        t3 = traces.retention
        stones = await t3.store.get({(r.org_id, r.request_id) for r in rows})
        now = t3.clock()
        grants: dict[tuple[str, str], str | None] = {}
        out = []
        for row in rows:
            scopes = stones.get((row.org_id, row.request_id), {})
            if REQUEST in scopes or not t3.metadata_live(row.started_at, now):
                continue
            model_id = serving[row.serving_version_id]
            key = (row.org_id, model_id)
            if key not in grants:
                grants[key] = await granted(user_id, provider, row.org_id, model_id)
            item = {name: getattr(row, name) for name in METADATA}
            item |= {"model_id": model_id, "access": "metadata"}
            if grants[key] is not None:
                item |= {"access": "content", "grantor_org_id": row.org_id, "grant_ref": grants[key],
                         "content_complete": row.content_complete,
                         "content_bytes": row.content_bytes,
                         "content_available": row.content_stored and CONTENT not in scopes
                         and t3.content_live(row.started_at, now)}
            out.append((row, _json(item)))
        return out

    @app.get(TRACES_PATH)
    @lab_auth.guarded
    async def list_traces(request: Request):
        user_id, provider, serving = await reader(request)
        limit = parse_limit(request.query_params.get("limit"))
        cursor = request.query_params.get("cursor")
        before = parse_cursor(cursor) if cursor is not None else None
        rows = await traces.rows.page(tuple(serving), before, limit) if serving else []
        items = await visible(user_id, provider, serving, rows)
        return lab_auth.ok({"data": [item for _, item in items],
                            "next_cursor": mint_cursor(rows[-1]) if len(rows) == limit else None})

    @app.get(TRACES_PATH + "/{request_id}")
    @lab_auth.guarded
    async def read_trace(request: Request):
        user_id, provider, serving = await reader(request)
        request_id = request.path_params["request_id"]
        try:
            uuid.UUID(request_id)
        except ValueError:
            raise errors.NotFound("no such request") from None
        rows = await traces.rows.request(tuple(serving), request_id) if serving else []
        items = await visible(user_id, provider, serving, rows)
        if not items:
            raise errors.NotFound("no such request")
        row, item = items[0]
        if item["access"] == "content":
            content = await traces.retention.read_content(row.org_id, row.request_id) \
                if item["content_available"] else None
            item["content"] = None if content is None else content.decode("utf-8", "replace")
        return lab_auth.ok(item)

    return traces


def _json(item: dict) -> dict:
    return {k: v.isoformat() if isinstance(v, datetime) else v for k, v in item.items()}
