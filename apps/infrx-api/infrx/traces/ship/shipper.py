"""T2I: the shipper from the T1 spool to the projection plus content objects.

One pass (`Shipper.ship`) over the spool's **sealed** segments, oldest first, each through
the spool's public interface only (`segments`, `read_segment`, `ack`):

1. every record's content goes to the object store first, at a key derived here from the
   envelope's organization and the record's stable id - never the envelope's own ref;
2. then one projection row per record, carrying the serving, rate-card and source-policy
   versions from the durable request record (`pins`, D5) and the envelope's loss state;
3. the segment is acked (unlinked whole) only when every row and every object landed.

Anything else holds the segment for the next pass, and a replay is harmless: the row key is
the spool's `(segment, position)` identity and the object write is write-once. The active
segment is never read (it may hold appended-but-unsynced records), and a segment with an
unreadable format or a poison frame is never acked: deleting bytes this reader cannot read
is not shipping them.

Shipping is OFF unless the spool, ClickHouse and the trace bucket are all configured
(`shipping_enabled`); `build_shipper` builds one only then, and scheduling it is the
composition root's.
"""
from __future__ import annotations

import asyncio
import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from ...contracts.limits import PilotSettings
from ...contracts.records import TraceEnvelope
from ...contracts.v2.records import AdmissionPins

SCHEMA = Path(__file__).with_name("schema.sql")
TABLE = "trace_envelopes"
CONTENT_PREFIX = "trace/"
CONTENT_TYPE = "application/json"
#: (org_id, request_id) -> the pins D5 persisted with the request, or None (legacy USD,
#: or no durable record). Raising means "ask again later", never "no pins".
PinsLookup = Callable[[str, str], Awaitable[AdmissionPins | None]]


def shipping_enabled(limits: PilotSettings) -> bool:
    """Defaults OFF: all three of the spool, the projection and the bucket must be set."""
    return all(value.strip() for value in (limits.trace_spool_dir, limits.clickhouse_url,
                                           limits.s3_trace_bucket))


def build_shipper(limits: PilotSettings, spool, *, prefix: str = "infrx/",
                  endpoint_url: str = "") -> Shipper | None:
    """T2I WR-3's factory, for the composition root: None unless `shipping_enabled(limits)`
    (flag OFF); otherwise the shipper over ClickHouse (`CLICKHOUSE_URL`), the trace bucket
    (`S3_TRACE_BUCKET`) and D5's pins on `DATABASE_URL`, consulting T3's retention over the
    same client and bucket (no resurrection). The root schedules
    `await spool.rotate(); await shipper.ship()` and `shipper.retention.expire()` /
    `.sweep()` (wiring requests)."""
    if not shipping_enabled(limits):
        return None
    import clickhouse_connect

    from ...media.s3 import S3ObjectStore
    from ...state.jobstore import connector
    from ..feedback import ClickHouseFeedbackProjection
    from ..retention import ClickHouseRetentionStore, Retention
    from .pins import PgPins
    client = clickhouse_connect.get_client(dsn=limits.clickhouse_url)
    traces = ClickHouseProjection(client)
    objects = S3ObjectStore.connect(limits.s3_trace_bucket, prefix, endpoint_url)
    retention = Retention(ClickHouseRetentionStore(client), traces,
                          ClickHouseFeedbackProjection(client), objects,
                          content_days=limits.trace_content_max_days,
                          metadata_months=limits.trace_metadata_months)
    return Shipper(spool, traces, objects, pins=PgPins(connector(limits.database_url)),
                   retention=retention)


def content_key(org_id: str, trace_id: str) -> str:
    return f"{CONTENT_PREFIX}{org_id}/{trace_id}"


@dataclass(frozen=True)
class TraceRow:
    org_id: str
    trace_id: str
    request_id: str
    key_id: str
    mode: str
    started_at: datetime
    completed_at: datetime | None
    loss_reason: str
    content_complete: bool
    content_bytes: int
    content_key: str | None
    content_stored: bool
    request_schema_version: int
    model_revision: str
    price_version: str
    serving_version_id: str | None
    rate_card_version: str | None
    policy_version: str | None


COLUMNS = tuple(f.name for f in dataclasses.fields(TraceRow))


@dataclass
class ShipReport:
    shipped: int = 0                       # segments acked
    rows: int = 0                          # rows inserted (a replay counts again)
    held: dict[str, str] = field(default_factory=dict)   # segment -> why it stays
    poison: int = 0
    unreadable: int = 0
    torn: int = 0                          # torn tails: the prefix ships, the tail is unpromised


class Shipper:
    def __init__(self, spool, projection, objects, *, pins: PinsLookup | None = None,
                 retention=None) -> None:
        self.spool, self.projection, self.objects, self.pins = spool, projection, objects, pins
        self.retention = retention           # T3: `retention.Retention`, consulted per segment

    async def ship(self) -> ShipReport:
        report = ShipReport()
        for view in self.spool.segments():
            if not view.sealed:
                continue
            try:
                reason = await self._segment(view.name, report)
                if reason is None and not await self.spool.ack(view.name):
                    reason = "not acked"
            except Exception as failure:          # noqa: BLE001 - held, retried next pass
                reason = type(failure).__name__
            if reason is None:
                report.shipped += 1
            else:
                report.held[view.name] = reason
        return report

    async def _segment(self, name: str, report: ShipReport) -> str | None:
        scan = await self.spool.read_segment(name)
        report.poison += scan.poison
        report.unreadable += scan.unreadable
        report.torn += scan.torn
        if scan.unreadable or scan.poison:
            return "unreadable" if scan.unreadable else "poison"
        verdicts = await self.retention.verdicts(scan.records) if self.retention else {}
        rows = []
        for envelope, content, (segment, position) in zip(scan.records, scan.contents, scan.ids):
            trace_id = f"{segment}:{position}"
            verdict = verdicts.get((envelope.org_id, envelope.request_id))
            if verdict is not None:
                # T3: deleted or expired - never (re)written, and the object an earlier
                # attempt of this replay may have put goes too
                await self.objects.delete(content_key(envelope.org_id, trace_id))
                if verdict == "drop":
                    continue
                content = b""
            rows.append(await self._row(envelope, content, trace_id))
        if rows:
            await self.projection.insert(rows)
            report.rows += len(rows)
        return None if all(row.content_stored or row.content_key is None for row in rows) \
            else "content not stored"

    async def _row(self, envelope: TraceEnvelope, content: bytes, trace_id: str) -> TraceRow:
        key, stored = None, False
        if content:
            key = content_key(envelope.org_id, trace_id)
            try:
                # write-once: a replay finds the object there (False) and it is still stored
                await self.objects.put_if_absent(key, content, CONTENT_TYPE)
                stored = True
            except Exception:                    # noqa: BLE001 - metadata ships regardless
                stored = False
        pins = await self.pins(envelope.org_id, envelope.request_id) if self.pins else None
        return TraceRow(
            org_id=envelope.org_id, trace_id=trace_id, request_id=envelope.request_id,
            key_id=envelope.key_id, mode=envelope.mode.value, started_at=envelope.started_at,
            completed_at=envelope.completed_at, loss_reason=envelope.loss_reason.value,
            content_complete=envelope.content_complete, content_bytes=len(content),
            content_key=key, content_stored=stored,
            request_schema_version=envelope.request_schema_version,
            model_revision=envelope.model_revision, price_version=envelope.price_version,
            serving_version_id=pins.serving_version_id if pins else None,
            rate_card_version=pins.rate_card_version if pins else None,
            policy_version=pins.policy_version if pins else None)


async def read_content(projection, objects, org_id: str, request_id: str) -> bytes | None:
    """TRACE-TENANT: a request's stored content, for the organization the *server*
    authenticated. The row is found with the organization bound as a parameter, and the
    object key comes from that row and must sit under that organization's prefix - a caller
    never names a key, and a planted cross-org ref is never followed."""
    for row in await projection.find(org_id, request_id):
        if row.content_stored and row.content_key \
                and row.content_key.startswith(content_key(org_id, "")):
            return await objects.get(row.content_key)
    return None


class ClickHouseProjection:
    """The projection over ClickHouse (`clickhouse-connect`, the `traces` extra). The
    client blocks, so each call runs in a worker thread."""

    def __init__(self, client) -> None:
        self.client = client

    async def apply_schema(self) -> None:
        await asyncio.to_thread(self.client.command, SCHEMA.read_text())

    async def insert(self, rows: list[TraceRow]) -> None:
        data = [[int(v) if name == "content_stored" else v
                 for name, v in zip(COLUMNS, dataclasses.astuple(row))] for row in rows]
        await asyncio.to_thread(self.client.insert, TABLE, data, column_names=list(COLUMNS))

    async def find(self, org_id: str, request_id: str) -> list[TraceRow]:
        # ponytail: (org_id, trace_id) is the key, so this scans one org's rows; add a
        # request_id skip index when an org's projection outgrows that.
        result = await asyncio.to_thread(
            self.client.query,
            f"SELECT {', '.join(COLUMNS)} FROM {TABLE} FINAL "
            "WHERE org_id = {org:UUID} AND request_id = {request:UUID} ORDER BY trace_id",
            parameters={"org": org_id, "request": request_id})
        return [_row(values) for values in result.result_rows]


def _row(values) -> TraceRow:
    row = dict(zip(COLUMNS, values))
    for name in ("org_id", "request_id", "key_id", "serving_version_id"):
        row[name] = None if row[name] is None else str(row[name])
    row["content_stored"] = bool(row["content_stored"])
    for name in ("started_at", "completed_at"):         # UTC columns, naive from the driver
        row[name] = row[name] and row[name].replace(tzinfo=timezone.utc)
    return TraceRow(**row)
