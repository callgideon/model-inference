"""Q3: outbox drain, reconciler, adapter switch and index-loss recovery (DUR-OUTBOX,
DUR-FENCE's scheduling half).

Every case runs on both adapters - the memory reference and Q2's Valkey adapter on this
lane's task-local server - so each one is also a differential: the same rows, the same
steps, the same assertions. A missing Docker or server is a skip naming the reason,
never a pass.

    uv run --frozen pytest -q tests/q/test_reconcile.py
"""
from __future__ import annotations

import asyncio

import pytest

from infrx.contracts import errors

from . import q3rig as rig
from . import vkharness
from .outboxfake import OutboxLost

_valkey_down = vkharness.unavailable()


@pytest.fixture(params=rig.ADAPTERS)
def adapter(request):
    if request.param == "valkey" and _valkey_down:
        pytest.skip(f"task-local Valkey unavailable: {_valkey_down}")
    return request.param


def run(body):
    return asyncio.run(body())


class FlakyIndex:
    """An index whose `enqueue` dies on the n-th call (the connection to Valkey drops)."""

    def __init__(self, inner, fail_on: int) -> None:
        self.inner, self.fail_on, self.calls = inner, fail_on, 0

    async def enqueue(self, event):
        self.calls += 1
        if self.calls == self.fail_on:
            raise ConnectionError("index connection lost")
        return await self.inner.enqueue(event)

    def __getattr__(self, name):
        return getattr(self.inner, name)


# --- (1) the drain ------------------------------------------------------------------

def test_q3_drain__every_dispatch_row_is_indexed_once_and_acknowledged(adapter):
    w = rig.world(adapter)

    async def body():
        jobs = [await rig.admit(w) for _ in range(3)]
        assert await w.rec.drain() == {"read": 3, "indexed": 3, "acknowledged": 3}
        assert w.outbox.unacknowledged() == []
        assert sorted((await rig.members(w)).values()) == sorted(jobs)
        assert await w.rec.drain() == {}
        for _ in jobs:
            await rig.prepare_one(w)
        assert await w.rec.drain() == {"read": 3, "indexed": 3, "acknowledged": 3}
        assert await rig.run_dry(w) == 3
        rig.settled(w)
    run(body)


@pytest.mark.parametrize("lost", ["before", "after"])
def test_q3_drain__a_lost_acknowledgment_is_a_redelivery_that_indexes_nothing_twice(
        adapter, lost):
    """Index first, acknowledge second. Lost before the commit: the rows come back after
    `redelivery_s` - not sooner - and `enqueue` answers "already indexed". Lost after
    it: nothing comes back. Either way the index holds each candidate once."""
    w = rig.world(adapter)

    async def body():
        jobs = [await rig.admit(w) for _ in range(3)]
        w.outbox.ack_faults.append(lost)
        with pytest.raises(OutboxLost):
            await w.rec.drain()
        assert sorted((await rig.members(w)).values()) == sorted(jobs)
        assert len(w.outbox.unacknowledged()) == (3 if lost == "before" else 0)
        assert await w.rec.drain() == {}          # claimed: not before redelivery
        w.h.clock.advance(w.rec.redelivery_s)
        expected = ({"read": 3, "acknowledged": 3} if lost == "before" else {})
        assert await w.rec.drain() == expected
        assert w.outbox.unacknowledged() == []
        assert len(await rig.members(w)) == 3
        assert await rig.run_dry(w) == 6
        rig.settled(w)
    run(body)


def test_q3_drain__a_duplicate_delivery_indexes_one_candidate(adapter):
    """Redelivered while pending, while in flight, and after the index acknowledged the
    candidate: one candidate, the in-flight one stays in flight, the consumed one is not
    re-indexed."""
    w = rig.world(adapter)
    w.rec.redelivery_s = 0.0

    async def body():
        jobs = [await rig.admit(w) for _ in range(3)]
        w.outbox.ack_faults.append("before")
        with pytest.raises(OutboxLost):
            await w.rec.drain()
        taken = await w.index.claim_candidate("prep", kind=rig.PREP)      # in flight
        consumed = await w.index.claim_candidate("prep", kind=rig.PREP)
        await w.index.acknowledge(consumed)                               # consumed
        assert await w.rec.drain() == {"read": 3, "acknowledged": 3}
        assert set((await rig.members(w)).values()) == set(jobs) - {consumed.job_id}
        stats = await _stats(w.index)
        assert (stats["pending"], stats["inflight"]) == (1, 1)
        assert w.outbox.deliveries == {event: 2 for event in w.outbox.deliveries}
        assert taken.job_id != consumed.job_id
    run(body)


def test_q3_drain__a_full_index_defers_the_row_and_the_redelivery_retries_it(adapter):
    w = rig.world(adapter, max_items=2)

    async def body():
        jobs = [await rig.admit(w) for _ in range(3)]
        assert await w.rec.drain() == {"read": 3, "indexed": 2, "deferred": 1,
                                       "acknowledged": 2}
        (deferred,) = w.outbox.unacknowledged()
        await rig.prepare_one(w)                          # frees one slot
        assert await w.rec.drain() == {"read": 1, "indexed": 1, "acknowledged": 1}
        assert w.outbox.unacknowledged() == [deferred]    # its redelivery is not due yet
        await rig.prepare_one(w)
        w.h.clock.advance(w.rec.redelivery_s)
        await w.rec.drain()
        assert deferred not in w.outbox.unacknowledged()  # redelivered and indexed
        await rig.finish(w)
        assert sorted(w.leases) == sorted(jobs)
        rig.settled(w)
    run(body)


def test_q3_drain__an_index_outage_acknowledges_only_what_was_indexed(adapter):
    w = rig.world(adapter)
    w.rec.index = FlakyIndex(w.index, fail_on=2)

    async def body():
        jobs = [await rig.admit(w) for _ in range(3)]
        with pytest.raises(ConnectionError):
            await w.rec.drain()
        assert list((await rig.members(w)).values()) == jobs[:1]
        assert len(w.outbox.unacknowledged()) == 2
        w.h.clock.advance(w.rec.redelivery_s)
        assert await w.rec.drain() == {"read": 2, "indexed": 2, "acknowledged": 2}
        assert sorted((await rig.members(w)).values()) == sorted(jobs)
    run(body)


def test_q3_drain__one_call_reads_a_bounded_number_of_rows_and_the_next_continues(adapter):
    w = rig.world(adapter)
    w.rec.batch, w.rec.max_batches = 2, 2

    async def body():
        for _ in range(7):
            await rig.admit(w)
        reads = [(await w.rec.drain()).get("read", 0) for _ in range(4)]
        assert reads == [4, 3, 0, 0]
        assert len(await rig.members(w)) == 7
    run(body)


def test_q3_metrics__the_outbox_lag_is_the_oldest_waiting_dispatch(adapter):
    w = rig.world(adapter)

    async def body():
        await rig.admit(w)
        w.h.clock.advance(2.5)
        await rig.admit(w)
        w.h.clock.advance(5.0)
        await w.rec.drain()
        assert w.rec.metrics["outbox_lag_s"] == 7.5
    run(body)


async def _stats(index):
    stats = index.stats()
    return await stats if asyncio.iscoroutine(stats) else stats
