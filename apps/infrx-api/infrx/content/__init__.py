"""C2: Lab content access - short-lived signed refs to one request's trace content, bound to
grant, recipient and expiry, redeemed over T3 retention and the M3 object store; and the
owned trace export page G4T serves.

**The ref.** `issue` mints an opaque `tc_` handle (R54) and hands only its SHA-256 to
lab-sql's content-access RPC (`ContentRefs`, C2-RPC), which holds every grant, expiry and
retention rule on the database clock (R7): the named grant ref must be a version of a grant TO
this provider (else `NotFound`, the answer for a forged or foreign ref too); the request must
be the grantor's own job, which also names the served model (never the caller); the user must
be a current developer+ member; the grant's CURRENT version must permit that model, both
content categories and the purpose; the ref expires at the earliest of its TTL, the grant's
expiry and the job's age plus the grant's `retention_days`. `redeem` re-checks all of that at
every read, so an expired, revoked or out-of-retention ref fails closed however long it has
existed, and a ref is redeemable only by the provider and user it was issued to.

**The read.** A handle that is not a `tc_` handle (a storage key, a path) is `NotFound` before
any lookup: this service never takes a storage path. The object key comes from the grantor's
own projection row under the grantor's prefix (`retention.read_content`, TRACE-TENANT), after
T3 said the request is neither deleted nor past its content bound. What is not readable is a
state, never a crash: `pending` (not projected yet), `metadata_only`, `lost` (never stored,
gone, or not a renderable `TraceContentBody`), `expired` (deleted or past its bound).

`holds` is T3's `Retention(holds=...)`: a live ref keeps the physical object until it ends
(logical deletion is immediate either way).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from datetime import datetime, timezone
from typing import Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..contracts import errors, ids, wire
from ..contracts.records import ContentState
from ..contracts.v2.records import DataCategory, DataPurpose
from ..traces.retention import DROP, NO_CONTENT

DEFAULT_TTL_S = 300
MAX_TTL_S = 900
#: A trace content object is `{v, request, response}` (R47): reading it needs both.
CATEGORIES = (DataCategory.request_content, DataCategory.response_content)
EXPORT_DEFAULT_LIMIT, EXPORT_MAX_LIMIT = 100, 1000


class RefBinding(BaseModel):
    """What a ref is bound to, as the RPC answers it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    grant_id: str
    grant_version: int = Field(ge=1)
    grantor_org_id: str
    request_id: str
    purpose: DataPurpose
    expires_at: datetime


class ContentRefs(Protocol):
    """C2-RPC (lab-sql; the schema request in the C2 evidence). Refusals are typed:
    `NotFound`, `Forbidden`, `Gone`, `InvalidRequest`, `Conflict`."""

    async def issue(self, *, handle_sha256: str, user_id: str, provider_org_id: str,
                    grant_ref: str, request_id: str, purpose: DataPurpose,
                    categories: Sequence[DataCategory], ttl_s: int) -> RefBinding: ...

    async def redeem(self, *, handle_sha256: str, user_id: str,
                     provider_org_id: str) -> RefBinding: ...

    async def held(self, org_id: str, request_id: str) -> bool: ...


class ContentRead(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: ContentState
    content: wire.TraceContentBody | None = None
    expires_at: datetime


def handle_digest(handle: str) -> str:
    """The only form of a handle the database ever sees."""
    return hashlib.sha256(handle.encode()).hexdigest()


def row_state(row, expired: bool) -> ContentState:
    """One projection row's content availability; `expired` is T3's word on its content."""
    if not row.content_key:
        return ContentState.metadata_only
    if expired:
        return ContentState.expired
    return ContentState.available if row.content_stored else ContentState.lost


class ContentAccess:
    def __init__(self, refs: ContentRefs, retention, *, ttl_s: int = DEFAULT_TTL_S) -> None:
        if not 1 <= ttl_s <= MAX_TTL_S:
            raise ValueError(f"a content ref lives 1..{MAX_TTL_S} s")
        self.refs, self.retention, self.ttl_s = refs, retention, ttl_s

    async def issue(self, *, user_id: str, provider_org_id: str, grant_ref: str,
                    request_id: str, purpose: DataPurpose) -> tuple[str, RefBinding]:
        """A new handle to `request_id`'s content under the grant `grant_ref` names, for the
        server-verified `user_id` of `provider_org_id`."""
        handle = ids.new_trace_content_handle()
        binding = await self.refs.issue(
            handle_sha256=handle_digest(handle), user_id=user_id,
            provider_org_id=provider_org_id, grant_ref=grant_ref, request_id=request_id,
            purpose=DataPurpose(purpose), categories=CATEGORIES, ttl_s=self.ttl_s)
        return handle, binding

    async def read(self, *, handle: str, user_id: str, provider_org_id: str) -> ContentRead:
        if not isinstance(handle, str) or not ids.TRACE_CONTENT_HANDLE_RE.fullmatch(handle):
            raise errors.NotFound("no such content reference")
        binding = await self.refs.redeem(handle_sha256=handle_digest(handle), user_id=user_id,
                                         provider_org_id=provider_org_id)
        org, request = binding.grantor_org_id, binding.request_id
        rows = await self.retention.find_traces(org, request)
        if not rows:
            deleted = bool(await self.retention.receipt(org, request))
            return ContentRead(state=ContentState.expired if deleted else ContentState.pending,
                               expires_at=binding.expires_at)
        now = self.retention.clock()
        if not any(r.content_key for r in rows):
            state, content = ContentState.metadata_only, None
        elif not all(self.retention.content_live(r.started_at, now) for r in rows):
            state, content = ContentState.expired, None
        else:
            data = await self.retention.read_content(org, request)
            try:
                content = wire.TraceContentBody.model_validate_json(data) if data else None
            except ValidationError:
                content = None
            state = ContentState.lost if content is None else ContentState.available
        return ContentRead(state=state, content=content, expires_at=binding.expires_at)

    async def holds(self, org_id: str, request_id: str) -> bool:
        return await self.refs.held(org_id, request_id)


# ======================================================================================
# G4T: the owner's export
# ======================================================================================
class TracePages(Protocol):
    """The organization's projection rows in `(started_at, trace_id)` order, strictly after
    `after`, within [since, until), at most `limit`."""

    async def page(self, org_id: str, *, after: tuple[datetime, str] | None,
                   since: datetime | None, until: datetime | None, limit: int) -> list: ...


def encode_cursor(row) -> str:
    raw = json.dumps([row.started_at.isoformat(), row.trace_id]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    """A position in the caller's own organization's list; anything else is a 400."""
    try:
        at, trace_id = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        at = datetime.fromisoformat(at)
        if at.tzinfo is None or not isinstance(trace_id, str):
            raise ValueError
        return at.astimezone(timezone.utc), trace_id
    except (ValueError, TypeError, binascii.Error):
        raise errors.InvalidCursor("not a trace export cursor", param="cursor") from None


def export_row(row, state: ContentState) -> wire.TraceExport:
    """R47: the public shape - no storage key, no content, no handle."""
    return wire.TraceExport(
        request_id=row.request_id, org_id=row.org_id, key_id=row.key_id, mode=row.mode,
        started_at=row.started_at, completed_at=row.completed_at,
        content_complete=row.content_complete, content_state=state,
        content_bytes=row.content_bytes, loss_reason=row.loss_reason,
        model_revision=row.model_revision, price_version=row.price_version,
        trace_schema_version=row.request_schema_version)


class OwnedExport:
    """G4T's service: pages of the caller's OWN traces over the projection (`pages`) and T3's
    `retention.verdicts` (deleted or metadata-expired: dropped; content deleted or past its
    bound: `expired`)."""

    def __init__(self, pages: TracePages, retention) -> None:
        self.pages, self.retention = pages, retention

    async def page(self, org_id: str, *, cursor: str | None, since: datetime | None,
                   until: datetime | None,
                   limit: int) -> tuple[list[wire.TraceExport], str | None]:
        """One page of `org_id`'s traces, minus what T3 deleted or expired. The cursor
        advances past dropped rows too, so a page may be short; `None` is the end."""
        if not 1 <= limit <= EXPORT_MAX_LIMIT:
            raise errors.InvalidRequest(f"limit must be 1..{EXPORT_MAX_LIMIT}", param="limit")
        after = decode_cursor(cursor) if cursor else None
        rows = [r for r in await self.pages.page(org_id, after=after, since=since, until=until,
                                                 limit=limit) if r.org_id == org_id]
        verdicts = await self.retention.verdicts(rows)       # T3's bounds and tombstones
        out = []
        for row in rows:
            verdict = verdicts.get((row.org_id, row.request_id))
            if verdict != DROP:
                out.append(export_row(row, row_state(row, verdict == NO_CONTENT)))
        return out, encode_cursor(rows[-1]) if len(rows) == limit else None


def build_export(limits) -> OwnedExport | None:
    """G4T's factory for the composition root: None unless ClickHouse is configured; the
    export reads the T2I projection and T3's tombstones there, on T3's bounds."""
    if not limits.clickhouse_url.strip():
        return None
    import clickhouse_connect

    from ..traces.retention import ClickHouseRetentionStore, Retention
    from ..traces.ship import ClickHouseProjection
    client = clickhouse_connect.get_client(dsn=limits.clickhouse_url)
    retention = Retention(ClickHouseRetentionStore(client), ClickHouseProjection(client), None,
                          None, content_days=limits.trace_content_max_days,
                          metadata_months=limits.trace_metadata_months)
    return OwnedExport(ClickHouseTracePages(client), retention)


class ClickHouseTracePages:
    """`TracePages` over T2I's `trace_envelopes` (R165), the organization bound as a query
    parameter like every read of it. ponytail: the `(org_id, trace_id)` key makes this an
    org scan sorted by time; a `(org_id, started_at)` projection when an org outgrows it."""

    def __init__(self, client) -> None:
        self.client = client

    async def page(self, org_id, *, after, since, until, limit):
        import asyncio

        from ..traces.ship.shipper import COLUMNS, TABLE, _row
        from ..traces.retention.policy import _at
        where, parameters = ["org_id = {org:UUID}"], {"org": org_id, "limit": limit}
        if after is not None:
            where.append("(started_at, trace_id) > "
                         "(toDateTime64({at:String}, 6, 'UTC'), {trace:String})")
            parameters |= {"at": _at(after[0]), "trace": after[1]}
        if since is not None:
            where.append("started_at >= toDateTime64({since:String}, 6, 'UTC')")
            parameters["since"] = _at(since)
        if until is not None:
            where.append("started_at < toDateTime64({until:String}, 6, 'UTC')")
            parameters["until"] = _at(until)
        result = await asyncio.to_thread(
            self.client.query,
            f"SELECT {', '.join(COLUMNS)} FROM {TABLE} FINAL WHERE {' AND '.join(where)} "
            "ORDER BY started_at, trace_id LIMIT {limit:UInt32}", parameters=parameters)
        return [_row(values) for values in result.result_rows]
