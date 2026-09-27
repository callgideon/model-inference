"""T2F: the feedback projection - D6F's outbox into ClickHouse, independently of traces.

One pump: the relay hands out a claimed batch (`pg.PgFeedbackOutbox`), each event with the
durable feedback row it names for its own organization; the rows are inserted; then exactly
the events whose row was inserted are acknowledged, as the claiming relay. An insert failure
acknowledges nothing (the claims lapse and the events come back); an event whose row the
organization does not own is neither projected nor acknowledged - it stays as lag, visible,
never filled in. A replay inserts the identical row, and FINAL reads keep one.

The projected row is the durable row: author, channel, role, operator marker, calibration
membership and rubric as PostgreSQL stored them. Nothing else is written - no judge result.
Nothing composes a projector yet (WR-T-2: the worker, behind `CLICKHOUSE_URL`).
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path

from ...contracts.records import Feedback

SCHEMA = Path(__file__).with_name("schema.sql")
TABLE = "feedback_events"
COLUMNS = ("org_id", "request_id", "feedback_id", "author_principal", "author_role", "channel",
           "name", "value", "comment", "calibration_set", "rubric_version", "by_operator",
           "created_at")


@dataclass(frozen=True)
class Event:
    event_id: str
    org_id: str
    feedback: Feedback | None        # None: the event names no row of its organization


class FeedbackProjector:
    def __init__(self, outbox, projection, *, worker_id: str | None = None,
                 batch: int = 100, redelivery_s: float = 30.0) -> None:
        self.outbox, self.projection = outbox, projection
        self.worker_id = worker_id or f"feedback-{uuid.uuid4().hex[:8]}"
        self.batch, self.redelivery_s = batch, redelivery_s

    async def pump(self) -> dict[str, int]:
        events = await self.outbox.pending(worker_id=self.worker_id, limit=self.batch,
                                           redelivery_s=self.redelivery_s)
        owned = [e for e in events if e.feedback is not None]
        if owned:
            await self.projection.insert([e.feedback for e in owned])
        acknowledged = await self.outbox.acknowledge(
            [e.event_id for e in owned], worker_id=self.worker_id) if owned else 0
        return {"read": len(events), "projected": len(owned), "acknowledged": acknowledged,
                "orphaned": len(events) - len(owned)}


class ClickHouseFeedbackProjection:
    """`feedback_events` over ClickHouse (`clickhouse-connect`); calls run in a thread."""

    def __init__(self, client) -> None:
        self.client = client

    async def apply_schema(self) -> None:
        await asyncio.to_thread(self.client.command, SCHEMA.read_text())

    async def insert(self, rows: list[Feedback]) -> None:
        data = [[_cell(row, name) for name in COLUMNS] for row in rows]
        await asyncio.to_thread(self.client.insert, TABLE, data, column_names=list(COLUMNS))

    async def find(self, org_id: str, request_id: str) -> list[Feedback]:
        result = await asyncio.to_thread(
            self.client.query,
            f"SELECT {', '.join(COLUMNS)} FROM {TABLE} FINAL "
            "WHERE org_id = {org:UUID} AND request_id = {request:UUID} "
            "ORDER BY created_at, feedback_id",
            parameters={"org": org_id, "request": request_id})
        return [_feedback(dict(zip(COLUMNS, values))) for values in result.result_rows]


def _cell(row: Feedback, name: str):
    value = getattr(row, name)
    return json.dumps(value) if name == "value" else getattr(value, "value", value)  # enums


def _feedback(row: dict) -> Feedback:
    return Feedback.model_validate({**row, "value": json.loads(row["value"]),
                                    "org_id": str(row["org_id"]),
                                    "request_id": str(row["request_id"]),
                                    # the column is UTC; the driver hands back naive values
                                    "created_at": row["created_at"].replace(tzinfo=timezone.utc)})
