#!/usr/bin/env python3
"""D2 item 2: DUR-OUTBOX through the relay, independent of the queue backend.

The same five drills run against three `ports.Scheduler`s - the contract fake, Q1's
in-process `MemoryScheduler` and Q2's `ValkeyScheduler` on D2's own Valkey - with the REAL
PostgreSQL JobStore underneath (`pgstore.factory`, a fresh frozen database per case):

* both dispatch kinds reach the index once, and an acknowledged row is never re-sent;
* a lost acknowledgment (the relay dies between the index write and the ack) redelivers
  the SAME candidate after the window - one candidate, never two;
* a duplicate delivery (a replay, a second relay, a rebuild while a worker holds the
  candidate) never makes a second executable attempt: `claim_preparation` decides;
* losing the index (a Valkey restart) and restarting the dispatcher lose no accepted job;
* a full index defers rows, it never drops them.

    uv run --frozen pytest -q tests/d/test_outbox_relay.py
"""
from __future__ import annotations

import asyncio

import pytest
from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.scheduling import FakeScheduler
from infrx.contracts.records import OutboxKind
from infrx.scheduling.memory import MemoryScheduler
from infrx.scheduling.valkey import ValkeyScheduler
from infrx.state.outbox import OutboxRelay

from . import pgharness, pgstore, vkstore

_reason = pgharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local PostgreSQL unavailable: {_reason}")
BACKENDS = ("fake", "memory", "valkey")
REDELIVERY_S = 30.0


def index(backend: str, harness, *, max_items: int | None = None):
    """A fresh, empty index of this backend on the harness's (database) clock."""
    if backend == "fake":
        return FakeScheduler(harness.clock)
    if backend == "memory":
        return MemoryScheduler(harness.clock.now, **({"max_items": max_items}
                                                     if max_items else {}))
    why = vkstore.unavailable()
    if why:
        pytest.skip(f"Valkey unavailable: {why}")
    return ValkeyScheduler(vkstore.client(), harness.clock.now, namespace=vkstore.namespace(),
                           **({"max_items": max_items} if max_items else {}))


def run(body):
    asyncio.run(body())


async def admitted(h, n: int = 1, **kw):
    out = []
    for _ in range(n):
        request = b.request(h, **kw)
        out.append(await h.port.admit(request, b.idem(request, request.request_id)))
    return out


async def drain(scheduler, kind=None) -> list:
    got = []
    while (event := await scheduler.claim_candidate("drainer", kind=kind)) is not None:
        got.append(event)
        await scheduler.acknowledge(event)
    return got


@pytest.mark.parametrize("backend", BACKENDS)
def test_outbox__both_dispatch_kinds_are_indexed_once_and_acknowledged(backend) -> None:
    h = pgstore.factory()
    h.extra["grant"](b.ORG_A, "25.00")

    async def body():
        q = index(backend, h)
        relay = OutboxRelay(h.extra["store"], q, redelivery_s=REDELIVERY_S)
        a, c = await admitted(h, 2)
        first = await relay.pump()
        assert first == {"read": 2, "indexed": 2, "acknowledged": 2, "deferred": 0}, first
        prep = await drain(q, OutboxKind.prepare_dispatch)
        assert sorted(e.job_id for e in prep) == sorted([a.request_id, c.request_id])
        lease = await h.port.claim_preparation(a.request_id, "prep-a")
        await h.port.prepared(lease, ())
        h.clock.advance(REDELIVERY_S)
        second = await relay.pump()
        assert second["read"] == 1 and second["acknowledged"] == 1, second
        inference = await drain(q, OutboxKind.inference_dispatch)
        assert [(e.job_id, e.kind) for e in inference] == \
            [(a.request_id, OutboxKind.inference_dispatch)]
        h.clock.advance(REDELIVERY_S)
        assert (await relay.pump())["read"] == 0, "an acknowledged row was delivered again"
    run(body)


@pytest.mark.parametrize("backend", BACKENDS)
def test_outbox__a_lost_acknowledgment_redelivers_the_same_single_candidate(backend) -> None:
    h = pgstore.factory()
    h.extra["grant"](b.ORG_A, "25.00")

    async def body():
        q = index(backend, h)
        store = h.extra["store"]
        [a] = await admitted(h)
        # the relay reads and indexes, then dies before `acknowledge_dispatch`
        [event] = await store.dispatch_pending(redelivery_s=REDELIVERY_S)
        assert await q.enqueue(event) is True
        relay = OutboxRelay(store, q, redelivery_s=REDELIVERY_S)     # a restarted relay
        assert (await relay.pump())["read"] == 0, "redelivered inside the window"
        h.clock.advance(REDELIVERY_S)
        again = await relay.pump()
        assert again == {"read": 1, "indexed": 1, "acknowledged": 1, "deferred": 0}, again
        got = await drain(q)
        assert [(e.event_id, e.job_id) for e in got] == [(event.event_id, a.request_id)], \
            "a redelivery indexed a second candidate"
    run(body)


@pytest.mark.parametrize("backend", BACKENDS)
def test_outbox__a_duplicate_delivery_never_makes_a_second_attempt(backend) -> None:
    h = pgstore.factory()
    h.extra["grant"](b.ORG_A, "25.00")

    async def body():
        q = index(backend, h)
        store = h.extra["store"]
        [a] = await admitted(h)
        await OutboxRelay(store, q).pump()
        [event] = await store.dispatch_snapshot()
        assert await q.enqueue(event) is False, "a replayed event indexed a second candidate"
        mine = await q.claim_candidate("w1", kind=OutboxKind.prepare_dispatch)
        # the index loses w1's claim and is rebuilt from PostgreSQL: the job reappears
        await OutboxRelay(store, q).rebuild()
        theirs = await q.claim_candidate("w2", kind=OutboxKind.prepare_dispatch)
        assert mine.job_id == theirs.job_id == a.request_id
        results = await asyncio.gather(h.port.claim_preparation(a.request_id, "w1"),
                                       h.port.claim_preparation(a.request_id, "w2"),
                                       return_exceptions=True)
        leases = [r for r in results if not isinstance(r, BaseException)]
        refused = [r for r in results if isinstance(r, errors.NotClaimable)]
        assert len(leases) == 1 and len(refused) == 1, results
        loser = "w2" if leases[0].worker_id == "w1" else "w1"
        forged = leases[0].model_copy(update={"worker_id": loser})
        with pytest.raises(errors.StaleLease):
            await h.port.prepared(forged, ())
        queued = await h.port.prepared(leases[0], ())
        assert queued.state.value == "queued"
        kinds = h.extra["outbox_kinds"](a.request_id)
        assert kinds.count(OutboxKind.inference_dispatch) == 1, kinds
    run(body)


@pytest.mark.parametrize("backend", BACKENDS)
def test_outbox__losing_the_index_and_the_dispatcher_loses_no_accepted_job(backend) -> None:
    h = pgstore.factory()
    h.extra["grant"](b.ORG_A, "25.00")

    async def body():
        q = index(backend, h)
        store = h.extra["store"]
        jobs = await admitted(h, 4)
        await OutboxRelay(store, q).pump()
        await h.port.prepared(await h.port.claim_preparation(jobs[0].request_id, "p"), ())
        busy = await h.port.claim_preparation(jobs[1].request_id, "p")   # being prepared
        # the index loses everything (a Valkey restart) and the relay process restarts
        fresh = index(backend, h)
        rebuilt = await OutboxRelay(store, fresh).rebuild()
        got = {(e.job_id, e.kind) for e in await drain(fresh)}
        want = {(jobs[0].request_id, OutboxKind.inference_dispatch),
                (jobs[2].request_id, OutboxKind.prepare_dispatch),
                (jobs[3].request_id, OutboxKind.prepare_dispatch)}
        assert rebuilt == 3 and got == want, (rebuilt, got)
        # the job under a live preparation lease comes back once that lease is lost
        h.clock.advance(busy.expires_at.timestamp() - h.clock.now().timestamp())
        again = await OutboxRelay(store, fresh).rebuild()
        assert again == 4 and (jobs[1].request_id, OutboxKind.prepare_dispatch) in \
            {(e.job_id, e.kind) for e in await drain(fresh)}
    run(body)


@pytest.mark.parametrize("backend", ("memory", "valkey"))
def test_outbox__a_full_index_defers_rows_and_never_drops_them(backend) -> None:
    h = pgstore.factory()
    h.extra["grant"](b.ORG_A, "25.00")

    async def body():
        q = index(backend, h, max_items=1)
        store = h.extra["store"]
        jobs = await admitted(h, 3)
        relay = OutboxRelay(store, q, redelivery_s=REDELIVERY_S)
        first = await relay.pump()
        assert (first["indexed"], first["deferred"]) == (1, 2), first
        seen = set()
        for _ in range(3):
            seen |= {e.job_id for e in await drain(q)}
            h.clock.advance(REDELIVERY_S)
            await relay.pump()
        seen |= {e.job_id for e in await drain(q)}
        assert seen == {j.request_id for j in jobs}, "a deferred row was lost"
    run(body)


class _SnapshotThenRace:
    """The store as a rebuilding relay sees it, with the race of review OB-1 injected
    between its snapshot and the index rebuild: another relay pumps (indexes AND
    acknowledges) a job admitted after the snapshot began."""

    def __init__(self, store, interleave) -> None:
        self._store, self._interleave = store, interleave

    def __getattr__(self, name):
        return getattr(self._store, name)

    async def dispatch_snapshot(self):
        snapshot = await self._store.dispatch_snapshot()
        await self._interleave()
        return snapshot


@pytest.mark.parametrize("backend", BACKENDS)
def test_outbox__a_rebuild_racing_another_relays_pump_strands_no_job(backend) -> None:
    """OB-1: a acknowledged; the rebuild snapshots; c is admitted; a second relay pumps
    (indexes + acknowledges) c; the rebuild lands on the snapshot, which lacks c. Without
    the reopen fence c sat queued with its row "delivered" until its queue deadline."""
    h = pgstore.factory()
    h.extra["grant"](b.ORG_A, "25.00")

    async def body():
        q = index(backend, h)
        store = h.extra["store"]
        [a] = await admitted(h)
        await OutboxRelay(store, q).pump()                    # a indexed and acknowledged
        late = {}

        async def race():
            [late["c"]] = await admitted(h)
            report = await OutboxRelay(store, q, worker_id="relay-b").pump()
            assert report["acknowledged"] == 1, report        # c delivered and acked

        await OutboxRelay(_SnapshotThenRace(store, race), q).rebuild()
        got = {e.job_id for e in await drain(q, OutboxKind.prepare_dispatch)}
        assert got == {a.request_id, late["c"].request_id}, \
            f"the rebuild stranded a pumped job: dispatchable {got}"
    run(body)
