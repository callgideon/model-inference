#!/usr/bin/env python3
"""T3 / TRACE-TENANT + TRACE-RECOVER + OPS-RECOVER: retention and deletion over T2I and T2F.

Every scenario drives the real spool and shipper (T2I) and the feedback projector (T2F) and
runs twice: in memory (projections that read like `ReplacingMergeTree ... FINAL`), and on the
real stack - the ClickHouse image pinned in `tests/integration/compose.yaml` plus MinIO - on
the lane's tasklocal ports (t3: ClickHouse 57546, S3 57548). The stack half skips visibly
unless `INFRX_T3_STACK=1`:

    uv run --frozen pytest -q tests/t/retention
    INFRX_T3_STACK=1 uv run --frozen pytest -q tests/t/retention   # infrx-t3-* running
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from infrx.contracts import errors, tasklocal
from infrx.contracts.conformance import builders as b
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import TraceLossReason
from infrx.traces import feedback, retention, ship

from ..feedback.test_feedback import MemoryProjection as MemoryFeedback
from ..feedback.test_feedback import ScriptedOutbox, entry, event
from ..ship.test_ship import (ID_A, ID_B, ID_C, LostAck, MemoryProjection, Objects, Projection,
                              _dir, capture, die, durable, spool)

STARTED = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)     # builders.trace's started_at
SOON = STARTED + timedelta(days=1)
STACK = os.environ.get("INFRX_T3_STACK") == "1"
CH_USER, CH_PASSWORD = "infrx_t3", "infrx-t3-local"          # task-local literals
S3_KEY, S3_SECRET, S3_BUCKET = "infrxe2minio", "infrx-e2-local-secret", "infrx-t3"


def run(coroutine):
    return asyncio.run(coroutine)


class Store:
    """Either retention store, with a switch that fails the tombstone write."""

    def __init__(self, inner) -> None:
        self.inner, self.put_down = inner, False

    async def put(self, stones) -> None:
        if self.put_down:
            raise errors.DependencyUnavailable("clickhouse did not answer")
        await self.inner.put(stones)

    def __getattr__(self, name):
        return getattr(self.inner, name)


class MemoryStore:
    """`trace_deletions` read with FINAL (a cleaned tombstone supersedes its pending one),
    and the physical operations on the two in-memory projections."""

    def __init__(self, traces: MemoryProjection, fb: MemoryFeedback) -> None:
        self.traces, self.fb, self.stones = traces, fb, []

    async def put(self, stones) -> None:
        self.stones.extend(stones)

    def _final(self) -> dict:
        final: dict = {}
        for stone in self.stones:
            key = (stone.org_id, stone.request_id, stone.scope)
            if key not in final or (stone.cleaned_at is not None) >= \
                    (final[key].cleaned_at is not None):
                final[key] = stone
        return final

    async def get(self, pairs) -> dict:
        found: dict = {}
        for (org, request, scope), stone in self._final().items():
            if (org, request) in set(pairs):
                found.setdefault((org, request), {})[scope] = stone
        return found

    async def pending(self, limit: int):
        return sorted((s for s in self._final().values() if s.cleaned_at is None),
                      key=lambda s: (s.deleted_at, s.org_id, s.request_id))[:limit]

    async def expired_content(self, cutoff, limit: int):
        stoned = {(s.org_id, s.request_id) for s in self._final().values()}
        rows = {(r.org_id, r.request_id) for r in await self._rows()
                if r.content_stored and r.started_at <= cutoff}
        return sorted(rows - stoned)[:limit]

    async def _rows(self):
        final = {}
        for row in self.traces.inserted:
            key = (row.org_id, row.trace_id)
            if key not in final or row.content_stored >= final[key].content_stored:
                final[key] = row
        return list(final.values())

    async def purge(self, org_id: str, request_id: str) -> None:
        self.traces.inserted = [r for r in self.traces.inserted
                                if (r.org_id, r.request_id) != (org_id, request_id)]
        self.fb.inserted = [r for r in self.fb.inserted
                            if (r.org_id, r.request_id) != (org_id, request_id)]

    async def loss(self) -> dict:
        counts: dict = {}
        for row in await self._rows():
            eligible, lost, size = counts.get(row.mode, (0, 0, 0))
            counts[row.mode] = (eligible + 1, lost + (row.loss_reason != "none"),
                                size + row.content_bytes)
        return counts


def _stack():
    import botocore.session
    import clickhouse_connect
    from botocore.config import Config

    from infrx.media.s3 import S3ObjectStore

    services = tasklocal.local_services("t3")
    ch = dict(host="127.0.0.1", port=services["clickhouse"].host_port, username=CH_USER,
              password=CH_PASSWORD)
    admin = clickhouse_connect.get_client(**ch, database=services["clickhouse"].database)
    database = f"t3_{uuid.uuid4().hex}"
    admin.command(f"CREATE DATABASE {database}")
    client = clickhouse_connect.get_client(**ch, database=database)
    traces = ship.ClickHouseProjection(client)
    fb = feedback.ClickHouseFeedbackProjection(client)
    store = retention.ClickHouseRetentionStore(client)
    for projection in (traces, fb, store):
        run(projection.apply_schema())
    # A superseded tombstone stays physical, so only FINAL hides it (the projections keep
    # merging: their physical deletes are mutations).
    client.command(f"SYSTEM STOP MERGES {retention.policy.TABLE}")
    s3 = botocore.session.get_session().create_client(
        "s3", endpoint_url=f"http://127.0.0.1:{services['s3'].host_port}",
        aws_access_key_id=S3_KEY, aws_secret_access_key=S3_SECRET, region_name="us-east-1",
        config=Config(s3={"addressing_style": "path"}))
    try:
        s3.head_bucket(Bucket=S3_BUCKET)
    except Exception:                          # noqa: BLE001 - absent: this stack is ours
        s3.create_bucket(Bucket=S3_BUCKET)
    prefix = f"{services['s3'].object_prefix}{uuid.uuid4().hex}/"

    def cleanup() -> None:
        admin.command(f"DROP DATABASE IF EXISTS {database}")
        listed = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix).get("Contents", ())
        if listed:
            s3.delete_objects(Bucket=S3_BUCKET, Delete={
                "Objects": [{"Key": item["Key"]} for item in listed], "Quiet": True})
    return traces, fb, store, Objects(S3ObjectStore(s3, S3_BUCKET, prefix)), cleanup


@pytest.fixture(params=["memory", "stack"])
def w(request):
    if request.param == "memory":
        traces, fb = MemoryProjection(), MemoryFeedback()
        yield SimpleNamespace(traces=traces, fb=fb, store=Store(MemoryStore(traces, fb)),
                              objects=Objects())
        return
    if not STACK:
        pytest.skip("T3 (owner: T): no real stack - start infrx-t3-clickhouse/-s3 on the "
                    "tasklocal t3 ports and export INFRX_T3_STACK=1")
    traces, fb, store, objects, cleanup = _stack()
    try:
        yield SimpleNamespace(traces=traces, fb=fb, store=Store(store), objects=objects)
    finally:
        cleanup()


def policy(w, now=SOON, holds=None) -> retention.Retention:
    return retention.Retention(w.store, w.traces, w.fb, w.objects, holds=holds,
                               clock=lambda: now)


async def shipped(w, contents: dict, *, now=SOON, sink=None):
    """Capture, fsync, seal and ship one record per request (org A), through the retention
    policy at `now`; returns the spool."""
    sink = sink or spool()
    for request_id, content in contents.items():
        await capture(sink, request_id, content)
    await durable(sink)
    await ship.Shipper(sink, w.traces, w.objects, retention=policy(w, now)).ship()
    return sink


def key_of(row) -> str:
    return row.content_key


async def raw(w, request_id: str, org: str = b.ORG_A):
    """What is physically there, ignoring every retention rule."""
    rows = await w.traces.find(org, request_id)
    return rows, [await w.objects.get(r.content_key) for r in rows if r.content_key], \
        await w.fb.find(org, request_id)


async def project(w, *events, now=SOON):
    return await feedback.FeedbackProjector(ScriptedOutbox(*events), w.fb, worker_id="t3",
                                            retention=policy(w, now)).pump()


# ======================================================================================
# TRACE-TENANT: deletion removes logical access at once, for the deleting tenant only
# ======================================================================================
def test_a_deletion_removes_logical_access_at_once_while_the_bytes_remain(w):
    """Every read returns nothing the moment the tombstone lands, while rows, objects and
    feedback are still physically there; another tenant deleting the same request id
    hides nothing of this tenant's."""
    async def scenario():
        sink = await shipped(w, {ID_A: b"a's content", ID_B: b"b's content"})
        await project(w, event(1, entry(1, request=ID_A)), event(2, entry(2, request=ID_B)))
        keep = policy(w)
        receipt = await keep.delete(b.ORG_A, ID_A, "owner request")
        assert (receipt.scope, receipt.reason, receipt.deleted_at, receipt.cleaned_at) == \
            (retention.REQUEST, "owner request", SOON, None)
        assert await keep.find_traces(b.ORG_A, ID_A) == []
        assert await keep.find_feedback(b.ORG_A, ID_A) == []
        assert await keep.read_content(b.ORG_A, ID_A) is None
        rows, objects, fb = await raw(w, ID_A)
        assert len(rows) == 1 and objects == [b"a's content"] and len(fb) == 1, "still there"
        await keep.delete(b.ORG_B, ID_B, "another tenant's id")      # binds to org B only
        assert await keep.read_content(b.ORG_A, ID_B) == b"b's content"
        assert len(await keep.find_traces(b.ORG_A, ID_B)) == 1
        assert len(await keep.find_feedback(b.ORG_A, ID_B)) == 1
        assert await keep.receipt(b.ORG_A, ID_A) == (receipt,)
        await sink.close()
    run(scenario())


def test_the_sweep_reaches_the_projection_the_objects_and_the_feedback(w):
    """Rows, objects and feedback go; the receipt records when; a second sweep has nothing
    to do; and a row of another tenant naming this tenant's object key is never followed."""
    async def scenario():
        sink = await shipped(w, {ID_A: b"a's content", ID_B: b"b's content"})
        await project(w, event(1, entry(1, request=ID_A)))
        later = SOON + timedelta(hours=1)
        [a_row] = await w.traces.find(b.ORG_A, ID_A)
        await policy(w).delete(b.ORG_A, ID_A, "owner request")
        assert await policy(w, later).sweep() == {"cleaned": 1, "held": 0, "failed": 0}
        assert await raw(w, ID_A) == ([], [], [])
        assert await w.objects.get(a_row.content_key) is None
        [receipt] = await policy(w).receipt(b.ORG_A, ID_A)
        assert receipt.cleaned_at == later
        assert await policy(w, later).sweep() == {"cleaned": 0, "held": 0, "failed": 0}
        [b_row] = await w.traces.find(b.ORG_A, ID_B)
        forged = dataclasses.replace(b_row, org_id=b.ORG_B, trace_id="forged:0")
        await w.traces.insert([forged])                    # org B's row, org A's object key
        await policy(w).delete(b.ORG_B, ID_B, "owner request")
        assert (await policy(w, later).sweep())["cleaned"] == 1
        assert await policy(w).read_content(b.ORG_A, ID_B) == b"b's content", \
            "one tenant's deletion reached another tenant's object"
        await sink.close()
    run(scenario())


# ======================================================================================
# TRACE-RECOVER: no resurrection through replay, and the spool is reached
# ======================================================================================
def test_a_replay_after_deletion_resurrects_nothing(w):
    """A segment whose ack was lost, replayed after the deletion and its sweep; a record
    deleted while still in the spool; a redelivered feedback event: none comes back, no
    object is rewritten, and the spool is emptied."""
    async def scenario():
        sink = spool(io=LostAck())
        await capture(sink, ID_A, b"a's content")
        await durable(sink)
        await ship.Shipper(sink, w.traces, w.objects, retention=policy(w)).ship()
        assert len(sink.segments()) == 1, "the ack was lost"
        await project(w, event(1, entry(1, request=ID_A)))
        await policy(w).delete(b.ORG_A, ID_A, "owner request")
        await policy(w).sweep()
        orphan = spool(_dir("t3-orphan"))
        await capture(orphan, ID_B, b"b's content")
        await durable(orphan)
        down = Projection(w.traces)
        down.down = True                      # the object lands, its row does not
        await ship.Shipper(orphan, down, w.objects, retention=policy(w)).ship()
        await policy(w).delete(b.ORG_A, ID_B, "owner request")
        await policy(w).sweep()               # no row: nothing physical found to delete
        assert len(await w.objects.keys(f"trace/{b.ORG_A}/")) == 1
        await ship.Shipper(orphan, w.traces, w.objects, retention=policy(w)).ship()
        assert orphan.segments() == ()
        await orphan.close()
        restarted = spool(die(sink))
        await capture(restarted, ID_C, b"c's content")          # still in the spool ...
        await policy(w).delete(b.ORG_A, ID_C, "owner request")  # ... when it is deleted
        await durable(restarted)
        report = await ship.Shipper(restarted, w.traces, w.objects, retention=policy(w)).ship()
        assert report.held == {} and restarted.segments() == (), report
        assert await project(w, event(1, entry(1, request=ID_A))) == \
            {"read": 1, "projected": 0, "acknowledged": 1, "orphaned": 0}
        for request_id in (ID_A, ID_B, ID_C):
            assert await raw(w, request_id) == ([], [], []), request_id
        assert await w.objects.keys(f"trace/{b.ORG_A}/") == [], "an object was rewritten"
        await restarted.close()
    run(scenario())


# ======================================================================================
# logical expiry: content at the retention bound, metadata at 13 calendar months
# ======================================================================================
def test_expired_content_is_unreadable_then_swept_while_its_metadata_stays(w):
    """At the content bound the content is unreadable before any cleanup ran; `expire`
    records it once, `sweep` removes the object and keeps the row; a record shipped after
    its bound carries no content; metadata and feedback end at 13 calendar months."""
    async def scenario():
        sink = await shipped(w, {ID_A: b"a's content"})
        bound = STARTED + timedelta(days=DEFAULTS.trace_content_max_days)
        assert await policy(w, bound - timedelta(seconds=1)).read_content(b.ORG_A, ID_A) == \
            b"a's content"
        assert await policy(w, bound).read_content(b.ORG_A, ID_A) is None   # at it: passed
        assert await policy(w, bound - timedelta(seconds=1)).expire() == 0
        assert await policy(w, bound).expire() == 1
        assert await policy(w, bound).expire() == 0, "an expiry is recorded once"
        assert await policy(w, bound).sweep() == {"cleaned": 1, "held": 0, "failed": 0}
        rows, objects, _ = await raw(w, ID_A)
        assert len(rows) == 1 and objects == [None], "the object went, the metadata stayed"
        assert len(await policy(w, bound).find_traces(b.ORG_A, ID_A)) == 1
        [receipt] = await policy(w).receipt(b.ORG_A, ID_A)
        assert (receipt.scope, receipt.reason) == (retention.CONTENT, "retention")
        late = await shipped(w, {ID_B: b"b's content"}, now=bound)   # shipped after expiry
        rows, objects, _ = await raw(w, ID_B)
        assert [r.content_stored for r in rows] == [False] and objects == []
        metadata_bound = datetime(2027, 10, 20, 12, tzinfo=timezone.utc)   # +13 months
        assert len(await policy(w, metadata_bound - timedelta(seconds=1))
                   .find_traces(b.ORG_A, ID_A)) == 1
        assert await policy(w, metadata_bound).find_traces(b.ORG_A, ID_A) == []
        await project(w, event(1, entry(1, request=ID_A)))
        assert len(await policy(w, metadata_bound - timedelta(days=7)).find_feedback(
            b.ORG_A, ID_A)) == 1
        assert await policy(w, datetime(2027, 10, 27, 12, 0, 1, tzinfo=timezone.utc)).find_feedback(
            b.ORG_A, ID_A) == []
        await sink.close()
        await late.close()
    run(scenario())


# ======================================================================================
# kept: content a live grant or export references
# ======================================================================================
def test_content_a_live_grant_or_export_references_is_kept(w):
    """A held deletion stays pending - logically gone, physically kept - until the
    reference ends; the next sweep then completes it."""
    async def scenario():
        sink = await shipped(w, {ID_A: b"a's content"})
        live = {(b.ORG_A, ID_A)}

        async def held(org_id, request_id):
            return (org_id, request_id) in live
        await policy(w).delete(b.ORG_A, ID_A, "owner request")
        assert await policy(w, holds=held).sweep() == {"cleaned": 0, "held": 1, "failed": 0}
        rows, objects, _ = await raw(w, ID_A)
        assert objects == [b"a's content"] and len(rows) == 1
        assert await policy(w, holds=held).read_content(b.ORG_A, ID_A) is None
        [receipt] = await policy(w).receipt(b.ORG_A, ID_A)
        assert receipt.cleaned_at is None
        live.clear()                                           # the grant ended
        assert await policy(w, holds=held).sweep() == {"cleaned": 1, "held": 0, "failed": 0}
        assert await raw(w, ID_A) == ([], [], [])
        await sink.close()
    run(scenario())


# ======================================================================================
# OPS-RECOVER: a sweep that dies midway finishes on the next one
# ======================================================================================
def test_a_sweep_that_fails_midway_finishes_on_the_next_one(w):
    """The deletes land and the cleaned mark does not: both tombstones stay pending, the
    sweep goes on past each failure, and the next sweep finishes them (idempotently)."""
    async def scenario():
        sink = await shipped(w, {ID_A: b"a's content", ID_B: b"b's content"})
        await policy(w).delete(b.ORG_A, ID_A, "owner request")
        await policy(w).delete(b.ORG_A, ID_B, "owner request")
        w.store.put_down = True               # the physical deletes land, the mark does not
        assert await policy(w).sweep() == {"cleaned": 0, "held": 0, "failed": 2}
        assert [s.cleaned_at for s in await w.store.pending(10)] == [None, None]
        w.store.put_down = False
        assert await policy(w).sweep() == {"cleaned": 2, "held": 0, "failed": 0}
        for request_id in (ID_A, ID_B):
            assert await raw(w, request_id) == ([], [], [])
        await sink.close()
    run(scenario())


# ======================================================================================
# loss, lag and retention gauges, and the alarms I2L-OBS deploys
# ======================================================================================
class Lag:
    def __init__(self, oldest_s: float) -> None:
        self.oldest_s = oldest_s

    async def lag(self):
        return {"pending": 3, "oldest_s": self.oldest_s}


def test_loss_lag_and_retention_gauges_fire_their_alarms(w):
    """Loss with its per-mode denominator and bytes, the deletion backlog's age, spool and
    feedback lag; each alarm fires on its gauge and is quiet when it is healthy."""
    async def scenario():
        sink = spool()
        await capture(sink, ID_A, b"a's content")
        await sink.offer(b.trace(ID_B, content_bytes=0).model_copy(
            update={"loss_reason": TraceLossReason.memory_budget, "content_complete": False,
                    "content_ref": None}))
        await durable(sink)
        await ship.Shipper(sink, w.traces, w.objects).ship()
        await policy(w, SOON).delete(b.ORG_A, ID_A, "owner request")
        await policy(w, SOON - timedelta(days=5)).delete(b.ORG_A, ID_C, "held by an export")

        async def held(org_id, request_id):
            return request_id == ID_C
        later = SOON + timedelta(days=2)
        gauges = await retention.gauges(policy(w, later, holds=held), spool=sink,
                                        outbox=Lag(1_000))
        assert gauges["infrx_trace_deletion_held"] == 1
        assert gauges["infrx_trace_eligible_full"] == 2 and gauges["infrx_trace_lost_full"] == 1
        assert gauges["infrx_trace_content_bytes_full"] == len(b"a's content")
        assert gauges["infrx_trace_loss_ratio"] == 0.5
        assert gauges["infrx_trace_deletion_backlog"] == 1
        assert gauges["infrx_trace_deletion_backlog_seconds"] == 2 * 86_400
        assert gauges["infrx_feedback_projection_lag_seconds"] == 1_000
        assert gauges["infrx_feedback_projection_backlog"] == 3
        assert gauges["infrx_trace_spool_unacked_records"] == 0
        assert retention.firing(gauges) == ["TraceDeletionBacklogOld",
                                            "FeedbackProjectionLagging", "TraceLossHigh"]
        await policy(w, later, holds=held).sweep()
        calm = await retention.gauges(policy(w, later, holds=held), spool=sink, outbox=Lag(10))
        assert calm["infrx_trace_deletion_backlog_seconds"] == 0
        assert retention.firing({**calm, "infrx_trace_loss_ratio": 0.0}) == []
        assert retention.firing({"infrx_trace_spool_bytes": DEFAULTS.trace_spool_max_bytes}) \
            == ["TraceSpoolFilling"]
        await sink.close()
    run(scenario())
