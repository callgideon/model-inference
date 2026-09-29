"""T3: logical retention and deletion - logical at once, physical after, never resurrected.

**Logical.** A deletion writes a tombstone (`trace_deletions`, the receipt) and from that
moment every read here - `find_traces`, `find_feedback`, `read_content` - returns nothing for
the request, while its rows, objects and spool bytes may all still exist. Content is
unreadable from `started_at + content_days` (at the instant itself it has passed) and
metadata and feedback from 13 calendar months, whatever TTL has or has not run yet: ClickHouse
TTLs and object deletes are cleanup, never the authorization boundary.

**Physical.** `expire` records a `content` tombstone for each stored object past its bound;
`sweep` works the pending tombstones: the request's objects (only keys under its own
organization's prefix), then for a `request` tombstone its trace and feedback rows, then marks
the tombstone cleaned. Content a live grant or export references (`holds`) is kept and the
tombstone stays pending - still logically deleted - until the reference ends. A failure leaves
the tombstone pending for the next sweep; every step is idempotent. The sweep pages past held
and failing tombstones (a cursor), so they never starve the deletions queued behind them.

**No resurrection.** The spool is reached through the shipper: `verdicts` drops a deleted
request's record (and deletes the object a replay may have written, unless a live grant or
export holds it - `keeps`) and ships an expired one without content, so a replayed segment
brings nothing back; the feedback projector drops a deleted request's rows (`keep_feedback`)
and acknowledges them. Tombstones are never TTL'd.

`gauges` are the loss, lag and retention numbers and `RULES` the alarms over them, in
`infra/alerts` rule shape, for I2L-OBS to deploy (a wiring request).
"""
from __future__ import annotations

import asyncio
import calendar
import logging
import operator
from dataclasses import dataclass, fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ...contracts.limits import DEFAULTS
from .. import ship
from ..feedback import TABLE as FEEDBACK
from ..ship.shipper import TABLE as TRACES

log = logging.getLogger("infrx.traces.retention")
SCHEMA = Path(__file__).with_name("schema.sql")
TABLE = "trace_deletions"
REQUEST, CONTENT = "request", "content"
DROP, NO_CONTENT = "drop", "no_content"
PENDING, CLEANED = 1, 2
#: ponytail: the backlog gauge reads at most this many pending tombstones (oldest first, so
#: the age is exact); the count saturates here - a count query when backlogs grow that large.
BACKLOG_SCAN = 10_000


@dataclass(frozen=True)
class Tombstone:
    org_id: str
    request_id: str
    scope: str                          # REQUEST | CONTENT
    reason: str
    deleted_at: datetime
    cleaned_at: datetime | None = None


def add_months(at: datetime, months: int) -> datetime:
    """Calendar months, the day clamped to the target month's last."""
    index = at.month - 1 + months
    year, month = at.year + index // 12, index % 12 + 1
    return at.replace(year=year, month=month,
                      day=min(at.day, calendar.monthrange(year, month)[1]))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Retention:
    """The policy over one store (`ClickHouseRetentionStore`), the two projections and the
    trace object store. `holds(org_id, request_id) -> bool` answers whether a live grant or
    export references the request's content (C2/G4T/N2 provide it; None holds nothing).
    `deleted(stone)` runs once after a new deletion (WR-N3-2a: the Lab's lineage push); its
    failure never loses the receipt (logged; the Lab's hourly pull is the backstop)."""

    def __init__(self, store, traces, feedback, objects, *, holds=None, deleted=None,
                 content_days: int = DEFAULTS.trace_content_max_days,
                 metadata_months: int = DEFAULTS.trace_metadata_months,
                 clock=_utcnow) -> None:
        self.store, self.traces, self.feedback, self.objects = store, traces, feedback, objects
        self.holds, self.content_days, self.metadata_months = holds, content_days, metadata_months
        self.deleted, self.clock = deleted, clock

    def content_live(self, started_at: datetime, now: datetime) -> bool:
        return now < started_at + timedelta(days=self.content_days)

    def metadata_live(self, at: datetime, now: datetime) -> bool:
        return now < add_months(at, self.metadata_months)

    async def _scopes(self, org_id: str, request_id: str) -> dict[str, Tombstone]:
        return (await self.store.get({(org_id, request_id)})).get((org_id, request_id), {})

    # --- the deletion and its receipt -----------------------------------------------
    async def delete(self, org_id: str, request_id: str, reason: str) -> Tombstone:
        """Logical deletion of one request of the organization the server authenticated;
        the receipt. Repeating it returns the first receipt."""
        existing = (await self._scopes(org_id, request_id)).get(REQUEST)
        if existing is not None:
            return existing
        stone = Tombstone(org_id, request_id, REQUEST, reason, self.clock())
        await self.store.put([stone])
        if self.deleted is not None:
            try:
                await self.deleted(stone)
            except Exception:                 # noqa: BLE001 - the receipt stands
                log.exception("the deletion hook failed; the receipt stands")
        return stone

    async def receipt(self, org_id: str, request_id: str) -> tuple[Tombstone, ...]:
        return tuple((await self._scopes(org_id, request_id)).values())

    # --- the physical half --------------------------------------------------------------
    async def expire(self, limit: int = 1_000) -> int:
        """A `content` tombstone for each stored object past its bound, once."""
        now = self.clock()
        pairs = await self.store.expired_content(now - timedelta(days=self.content_days), limit)
        if pairs:
            await self.store.put([Tombstone(org, request, CONTENT, "retention", now)
                                  for org, request in pairs])
        return len(pairs)

    async def sweep(self, limit: int = 100) -> dict[str, int]:
        """Pages of `limit` pending tombstones, oldest first, until `limit` are cleaned or the
        queue ends: held and failing ones stay pending behind the cursor, never in the way.
        ponytail: each sweep asks `holds` again for every held tombstone ahead of the due
        ones; a held-until column when long-lived holds number in the millions."""
        report = {"cleaned": 0, "held": 0, "failed": 0}
        after = None
        while report["cleaned"] < limit:
            page = await self.store.pending(limit, after)
            await self._sweep(page, report)
            if len(page) < limit or page[-1] == after:     # the end, or a store not advancing
                break
            after = page[-1]
        return report

    async def _sweep(self, page, report) -> None:
        for stone in page:
            try:
                if self.holds is not None and await self.holds(stone.org_id, stone.request_id):
                    report["held"] += 1
                    continue
                own = ship.content_key(stone.org_id, "")
                for row in await self.traces.find(stone.org_id, stone.request_id):
                    if row.content_key and row.content_key.startswith(own):
                        await self.objects.delete(row.content_key)
                if stone.scope == REQUEST:
                    await self.store.purge(stone.org_id, stone.request_id)
                await self.store.put([replace(stone, cleaned_at=self.clock())])
                report["cleaned"] += 1
            except Exception:                   # noqa: BLE001 - pending, retried next sweep
                report["failed"] += 1

    # --- what the writers consult (no resurrection) -------------------------------------
    async def verdicts(self, envelopes) -> dict[tuple[str, str], str]:
        """For the shipper: (org, request) -> DROP (deleted, or metadata expired) or
        NO_CONTENT (content deleted or expired); absent ships as it is."""
        now = self.clock()
        stones = await self.store.get({(e.org_id, e.request_id) for e in envelopes})
        out = {}
        for e in envelopes:
            scopes = stones.get((e.org_id, e.request_id), {})
            if REQUEST in scopes or not self.metadata_live(e.started_at, now):
                out[(e.org_id, e.request_id)] = DROP
            elif CONTENT in scopes or not self.content_live(e.started_at, now):
                out[(e.org_id, e.request_id)] = NO_CONTENT
        return out

    async def keeps(self, org_id: str, request_id: str, key: str) -> bool:
        """For the shipper's replay: the object at `key` stays while a live grant or export
        holds the request and a shipped row names it. An object no row names was never
        readable, so nothing references it, and no sweep could ever find it."""
        return self.holds is not None and await self.holds(org_id, request_id) and any(
            row.content_key == key for row in await self.traces.find(org_id, request_id))

    async def keep_feedback(self, rows):
        """For the feedback projector: the rows of requests neither deleted nor expired."""
        now = self.clock()
        stones = await self.store.get({(r.org_id, r.request_id) for r in rows})
        return [r for r in rows if REQUEST not in stones.get((r.org_id, r.request_id), {})
                and self.metadata_live(r.created_at, now)]

    # --- the reads (the console / export paths use these, never the projections) ------
    async def find_traces(self, org_id: str, request_id: str):
        if REQUEST in await self._scopes(org_id, request_id):
            return []
        now = self.clock()
        return [r for r in await self.traces.find(org_id, request_id)
                if self.metadata_live(r.started_at, now)]

    async def find_feedback(self, org_id: str, request_id: str):
        if REQUEST in await self._scopes(org_id, request_id):
            return []
        now = self.clock()
        return [r for r in await self.feedback.find(org_id, request_id)
                if self.metadata_live(r.created_at, now)]

    async def read_content(self, org_id: str, request_id: str) -> bytes | None:
        """A deleted request has no rows here, and a `content` tombstone exists only past
        the content bound, which the rows' own clock check already refuses."""
        now = self.clock()
        rows = await self.find_traces(org_id, request_id)
        if not rows or not all(self.content_live(r.started_at, now) for r in rows):
            return None
        return await ship.read_content(self.traces, self.objects, org_id, request_id)


# ======================================================================================
# the ClickHouse store
# ======================================================================================
COLUMNS = tuple(f.name for f in fields(Tombstone))


def _utc(value: datetime | None) -> datetime | None:
    return None if value is None else value.replace(tzinfo=timezone.utc)   # naive from the driver


def _at(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


class ClickHouseRetentionStore:
    """`trace_deletions` plus the physical operations on `trace_envelopes` (T2I) and
    `feedback_events` (T2F) of the same database; calls run in a thread."""

    def __init__(self, client) -> None:
        self.client = client

    async def _query(self, sql: str, **parameters):
        result = await asyncio.to_thread(self.client.query, sql, parameters=parameters)
        return result.result_rows

    async def apply_schema(self) -> None:
        await asyncio.to_thread(self.client.command, SCHEMA.read_text())

    async def put(self, stones) -> None:
        data = [[getattr(s, name) for name in COLUMNS]
                + [CLEANED if s.cleaned_at is not None else PENDING] for s in stones]
        await asyncio.to_thread(self.client.insert, TABLE, data,
                                column_names=[*COLUMNS, "state"])

    def _stone(self, values) -> Tombstone:
        row = dict(zip(COLUMNS, values))
        return Tombstone(**{**row, "org_id": str(row["org_id"]),
                            "request_id": str(row["request_id"]),
                            "deleted_at": _utc(row["deleted_at"]),
                            "cleaned_at": _utc(row["cleaned_at"])})

    async def get(self, pairs) -> dict:
        pairs = set(pairs)
        if not pairs:
            return {}
        found: dict = {}
        for values in await self._query(
                f"SELECT {', '.join(COLUMNS)} FROM {TABLE} FINAL "
                "WHERE request_id IN {requests:Array(UUID)}",
                requests=sorted({request for _, request in pairs})):
            stone = self._stone(values)        # callers look up their exact (org, request)
            found.setdefault((stone.org_id, stone.request_id), {})[stone.scope] = stone
        return found

    async def pending(self, limit: int, after: Tombstone | None = None) -> list[Tombstone]:
        """In (deleted_at, org, request, scope) order, past `after` when given."""
        cursor, parameters = "", {"limit": limit}
        if after is not None:
            cursor = ("AND (deleted_at, org_id, request_id, scope) > (toDateTime64({at:String}, "
                      "6, 'UTC'), {org:UUID}, {request:UUID}, {scope:String}) ")
            parameters |= {"at": _at(after.deleted_at), "org": after.org_id,
                           "request": after.request_id, "scope": after.scope}
        return [self._stone(values) for values in await self._query(
            f"SELECT {', '.join(COLUMNS)} FROM {TABLE} FINAL WHERE state = {PENDING} " + cursor
            + "ORDER BY deleted_at, org_id, request_id, scope LIMIT {limit:UInt32}",
            **parameters)]

    async def expired_content(self, cutoff: datetime, limit: int) -> list[tuple[str, str]]:
        return [(str(org), str(request)) for org, request in await self._query(
            f"SELECT DISTINCT org_id, request_id FROM {TRACES} FINAL "
            "WHERE content_stored = 1 "
            "AND started_at <= toDateTime64({cutoff:String}, 6, 'UTC') "
            f"AND (org_id, request_id) NOT IN (SELECT org_id, request_id FROM {TABLE} FINAL) "
            "ORDER BY org_id, request_id LIMIT {limit:UInt32}", cutoff=_at(cutoff), limit=limit)]

    async def purge(self, org_id: str, request_id: str) -> None:
        for table in (TRACES, FEEDBACK):
            await asyncio.to_thread(
                self.client.command,
                f"DELETE FROM {table} WHERE org_id = {{org:UUID}} AND request_id = {{request:UUID}}",
                parameters={"org": org_id, "request": request_id})

    async def loss(self) -> dict[str, tuple[int, int, int]]:
        """mode -> (eligible rows, lossy rows, content bytes). `off` has no rows (T1), so it
        is in no denominator."""
        return {mode: (int(eligible), int(lost), int(size)) for mode, eligible, lost, size
                in await self._query(
                    "SELECT mode, count(), countIf(loss_reason != 'none'), sum(content_bytes) "
                    f"FROM {TRACES} FINAL GROUP BY mode")}


# ======================================================================================
# gauges and alarms
# ======================================================================================
async def gauges(retention: Retention, *, spool=None, outbox=None) -> dict[str, float]:
    """Loss (per capture mode, with its denominator and bytes), deletion backlog, spool and
    feedback-projection lag. `spool` is the T1 sink, `outbox` the T2F relay."""
    now, out = retention.clock(), {}
    counts = await retention.store.loss()
    for mode in ("full", "minimal"):
        eligible, lost, size = counts.get(mode, (0, 0, 0))
        out[f"infrx_trace_eligible_{mode}"] = eligible
        out[f"infrx_trace_lost_{mode}"] = lost
        out[f"infrx_trace_content_bytes_{mode}"] = size
    eligible = sum(c[0] for c in counts.values())
    out["infrx_trace_loss_ratio"] = sum(c[1] for c in counts.values()) / eligible if eligible \
        else 0.0
    pending = await retention.store.pending(BACKLOG_SCAN)
    held = [s for s in pending
            if retention.holds is not None and await retention.holds(s.org_id, s.request_id)]
    due = [s for s in pending if s not in held]
    out["infrx_trace_deletion_backlog"] = len(due)
    out["infrx_trace_deletion_held"] = len(held)
    out["infrx_trace_deletion_backlog_seconds"] = max(
        ((now - s.deleted_at).total_seconds() for s in due), default=0.0)
    if spool is not None:
        stats = await spool.stats()
        out["infrx_trace_spool_bytes"] = stats["spool_bytes"]
        out["infrx_trace_spool_unacked_records"] = stats["spool_unacked_records"]
        out["infrx_trace_capture_dropped"] = stats["dropped"]
    if outbox is not None:
        lag = await outbox.lag()
        out["infrx_feedback_projection_backlog"] = lag["pending"]
        out["infrx_feedback_projection_lag_seconds"] = lag["oldest_s"]
    return out


RULES: tuple[dict, ...] = (
    {"name": "TraceDeletionBacklogOld", "severity": "ticket",
     "summary": "A trace deletion or content expiry that nothing holds has waited more than "
                "24 h for its physical cleanup (logical access is already gone).",
     "metric": "infrx_trace_deletion_backlog_seconds", "op": ">", "threshold": 86_400,
     "threshold_status": "derived: O3, a deletion completes within 24 h "
                         "(research/traces/01-requirements.md)"},
    {"name": "FeedbackProjectionLagging", "severity": "ticket",
     "summary": "Accepted feedback has waited more than 15 min to reach the projection "
                "(acceptance is PostgreSQL's and is unaffected).",
     "metric": "infrx_feedback_projection_lag_seconds", "op": ">", "threshold": 900,
     "threshold_status": "est.: 30 redelivery windows of 30 s; ⚠️ TO BE VERIFIED against the "
                         "measured pump interval"},
    {"name": "TraceLossHigh", "severity": "ticket",
     "summary": "More than 1% of eligible traces were captured with a loss reason.",
     "metric": "infrx_trace_loss_ratio", "op": ">", "threshold": 0.01,
     "threshold_status": "est.: product default; ⚠️ TO BE VERIFIED against measured loss"},
    {"name": "TraceSpoolFilling", "severity": "page",
     "summary": "The trace spool holds more than 80% of its byte cap: capture will pause.",
     "metric": "infrx_trace_spool_bytes", "op": ">",
     "threshold": int(0.8 * DEFAULTS.trace_spool_max_bytes),
     "threshold_status": "derived: 80% of TRACE_SPOOL_MAX_BYTES (contracts limits)"},
)
_OPS = {">": operator.gt, "<": operator.lt}


def firing(values: dict, rules=RULES) -> list[str]:
    return [rule["name"] for rule in rules
            if rule["metric"] in values and _OPS[rule["op"]](values[rule["metric"]],
                                                             rule["threshold"])]
