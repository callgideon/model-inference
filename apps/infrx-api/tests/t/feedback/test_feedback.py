#!/usr/bin/env python3
"""T2F / FEEDBACK-ACK + TRACE-RECOVER: the D6F `feedback_projection` outbox, projected.

The projector's own decisions run against a scripted outbox (the events the relay hands
out) and two projections: an in-memory one that reads like `ReplacingMergeTree ... FINAL`,
and the ClickHouse image pinned in `tests/integration/compose.yaml` on the lane's tasklocal
port (t2f: 57543). The stack half skips visibly unless `INFRX_T2F_STACK=1`. The relay's SQL
(claim, redelivery, ack, the durable-row join) is `test_feedback_pg.py`'s.

    uv run --frozen pytest -q tests/t/feedback
    INFRX_T2F_STACK=1 uv run --frozen pytest -q tests/t/feedback   # infrx-t2f-clickhouse up
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from infrx.contracts import errors, tasklocal
from infrx.contracts.conformance import builders as b
from infrx.contracts.records import AuthorRole, Feedback, FeedbackChannel
from infrx.traces import feedback

REQ = "aaaaaaaa-0000-4000-8000-00000000000a"
OTHER_REQ = "bbbbbbbb-0000-4000-8000-00000000000b"
T0 = datetime(2026, 9, 27, 12, 0, tzinfo=timezone.utc)
STACK = os.environ.get("INFRX_T2F_STACK") == "1"
CH_USER, CH_PASSWORD = "infrx_t2f", "infrx-t2f-local"       # task-local literals


def run(coroutine):
    return asyncio.run(coroutine)


def entry(n: int, *, org: str = b.ORG_A, request: str = REQ, name: str = "rating",
          value=4, label: bool = False, **over) -> Feedback:
    fields = dict(feedback_id=f"fb_{n:026d}", request_id=request, org_id=org,
                  author_principal=b.KEY_A, author_role=AuthorRole.customer,
                  channel=FeedbackChannel.api, name=name, value=value,
                  created_at=T0 + timedelta(seconds=n))
    if label:
        fields.update(author_principal="ops@infrx", author_role=AuthorRole.operator,
                      channel=FeedbackChannel.console, name="calibration_label",
                      value="incorrect", calibration_set=True, rubric_version=2,
                      by_operator=True, comment="off by one")
    return Feedback(**{**fields, **over})


def event(n: int, row: Feedback | None) -> feedback.Event:
    return feedback.Event(event_id=f"00000000-0000-4000-8000-{n:012d}",
                          org_id=row.org_id if row else b.ORG_A, feedback=row)


class ScriptedOutbox:
    """What the relay hands out: every event not yet acknowledged, every pump - a lost
    acknowledgment is a redelivery. Records who acknowledged what."""

    def __init__(self, *events: feedback.Event) -> None:
        self.events, self.acked, self.acks = list(events), set(), []

    async def pending(self, *, worker_id: str, limit: int, redelivery_s: float):
        return [e for e in self.events if e.event_id not in self.acked][:limit]

    async def acknowledge(self, event_ids, *, worker_id: str) -> int:
        self.acks.append((tuple(event_ids), worker_id))
        fresh = set(event_ids) - self.acked
        self.acked |= fresh
        return len(fresh)


class LostAcks(ScriptedOutbox):
    async def acknowledge(self, event_ids, *, worker_id: str) -> int:
        raise errors.DependencyUnavailable("postgres went away before the ack")


class MemoryProjection:
    """`ReplacingMergeTree` read with FINAL: one row per (org, request, feedback id)."""

    def __init__(self) -> None:
        self.inserted: list[Feedback] = []

    async def insert(self, rows) -> None:
        self.inserted.extend(rows)

    async def find(self, org_id: str, request_id: str) -> list[Feedback]:
        final = {(r.org_id, r.request_id, r.feedback_id): r for r in self.inserted}
        return sorted((r for r in final.values()
                       if r.org_id == org_id and r.request_id == request_id),
                      key=lambda r: (r.created_at, r.feedback_id))


class Switch:
    """Either projection, with an outage switch."""

    def __init__(self, inner) -> None:
        self.inner, self.down = inner, False

    async def insert(self, rows) -> None:
        if self.down:
            raise errors.DependencyUnavailable("clickhouse did not answer")
        await self.inner.insert(rows)

    async def find(self, org_id, request_id):
        return await self.inner.find(org_id, request_id)


def _stack():
    import clickhouse_connect
    service = tasklocal.local_services("t2f")["clickhouse"]
    ch = dict(host="127.0.0.1", port=service.host_port, username=CH_USER, password=CH_PASSWORD)
    admin = clickhouse_connect.get_client(**ch, database=service.database)
    database = f"t2f_{uuid.uuid4().hex}"
    admin.command(f"CREATE DATABASE {database}")
    client = clickhouse_connect.get_client(**ch, database=database)
    projection = feedback.ClickHouseFeedbackProjection(client)
    run(projection.apply_schema())
    client.command(f"SYSTEM STOP MERGES {feedback.TABLE}")
    return projection, client, lambda: admin.command(f"DROP DATABASE IF EXISTS {database}")


@pytest.fixture(params=["memory", "stack"])
def projection(request):
    if request.param == "memory":
        yield Switch(MemoryProjection())
        return
    if not STACK:
        pytest.skip("T2F (owner: T): no real ClickHouse - start infrx-t2f-clickhouse on the "
                    "tasklocal t2f port and export INFRX_T2F_STACK=1")
    inner, _, cleanup = _stack()
    try:
        yield Switch(inner)
    finally:
        cleanup()


def projector(outbox, projection, **kw) -> feedback.FeedbackProjector:
    return feedback.FeedbackProjector(outbox, projection, worker_id="relay-t2f", **kw)


# ======================================================================================
# FEEDBACK-ACK: the projected row is the durable row, author and authority included
# ======================================================================================
def test_a_projected_row_is_the_durable_row_with_its_author_and_authority(projection):
    """Author, channel (the method), role, operator marker, calibration membership and
    rubric, and a value of each type arrive exactly as PostgreSQL stored them; nothing is
    added - no judge result, no default rating."""
    rows = [entry(1), entry(2, name="thumb", value=True),
            entry(3, name="correction", value="use 4 spaces", comment="style"),
            entry(4, label=True)]
    outbox = ScriptedOutbox(*(event(n, row) for n, row in enumerate(rows, 1)))
    report = run(projector(outbox, projection).pump())
    assert report == {"read": 4, "projected": 4, "acknowledged": 4, "orphaned": 0}, report
    assert run(projection.find(b.ORG_A, REQ)) == rows
    assert run(projection.find(b.ORG_A, OTHER_REQ)) == []
    assert outbox.acks == [(tuple(event(n, None).event_id for n in (1, 2, 3, 4)), "relay-t2f")]


# ======================================================================================
# TRACE-RECOVER: retry, loss, duplicates
# ======================================================================================
def test_a_projection_outage_acknowledges_nothing_and_the_retry_projects_once(projection):
    """ClickHouse down: the pump fails and no event is acknowledged (the relay's claim
    lapses and the events come back); the retry projects each exactly once."""
    outbox = ScriptedOutbox(event(1, entry(1)), event(2, entry(2)))
    projection.down = True
    with pytest.raises(errors.DependencyUnavailable):
        run(projector(outbox, projection).pump())
    assert outbox.acks == []
    projection.down = False
    assert run(projector(outbox, projection).pump())["acknowledged"] == 2
    assert run(projector(outbox, projection).pump())["read"] == 0
    assert [r.feedback_id for r in run(projection.find(b.ORG_A, REQ))] == \
        [entry(1).feedback_id, entry(2).feedback_id]


def test_a_redelivery_after_a_lost_ack_is_one_logical_row(projection):
    """The relay dies between the insert and the ack: the redelivered events insert the
    same rows again and the projection still holds one logical row each."""
    events = (event(1, entry(1)), event(2, entry(2, label=True)))
    with pytest.raises(errors.DependencyUnavailable):
        run(projector(LostAcks(*events), projection).pump())
    outbox = ScriptedOutbox(*events)
    assert run(projector(outbox, projection).pump())["projected"] == 2
    assert run(projection.find(b.ORG_A, REQ)) == [entry(1), entry(2, label=True)]
    if isinstance(projection.inner, MemoryProjection):
        assert len(projection.inner.inserted) == 4          # two physical copies each


def test_an_event_without_its_durable_row_is_never_acknowledged():
    """An event whose row its organization does not own (the relay's join found none) is
    not projected and not acknowledged - it stays visible as projection lag, never lost
    silently and never filled in."""
    outbox = ScriptedOutbox(event(1, None), event(2, entry(2)))
    store = MemoryProjection()
    report = run(projector(outbox, store).pump())
    assert report == {"read": 2, "projected": 1, "acknowledged": 1, "orphaned": 1}, report
    assert outbox.acked == {event(2, None).event_id}
    assert store.inserted == [entry(2)]


# ======================================================================================
# late feedback joins by durable request id
# ======================================================================================
def test_late_feedback_joins_its_trace_by_the_durable_request_id(projection):
    """Feedback needs no trace row: projected before its trace ships and after, it is found
    by the one (organization, request id) the durable job owns - the trace projection's own
    key (`ship.TraceRow.request_id`). Another organization asking with the same request id
    finds only its own."""
    early, late = entry(1), entry(3)
    run(projector(ScriptedOutbox(event(1, early)), projection).pump())
    run(projector(ScriptedOutbox(event(3, late), event(4, entry(4, org=b.ORG_B)),
                                 event(5, entry(5, request=OTHER_REQ))), projection).pump())
    assert run(projection.find(b.ORG_A, REQ)) == [early, late]
    assert run(projection.find(b.ORG_B, REQ)) == [entry(4, org=b.ORG_B)]
    assert run(projection.find(b.ORG_A, OTHER_REQ)) == [entry(5, request=OTHER_REQ)]
