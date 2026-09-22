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


# --- (2) the reconciler -------------------------------------------------------------

class Interleave:
    """The store, except that its `n`-th `dispatch_snapshot` is read, THEN `action` runs,
    THEN the (now stale) snapshot is returned: "something happened between the snapshot
    and what the reconciler did with it", made deterministic."""

    def __init__(self, store, n: int, action) -> None:
        self.store, self.n, self.action, self.calls = store, n, action, 0

    async def dispatch_snapshot(self):
        snapshot = await self.store.dispatch_snapshot()
        self.calls += 1
        if self.calls == self.n:
            await self.action()
        return snapshot

    def __getattr__(self, name):
        return getattr(self.store, name)


def test_q3_reconcile__a_consistent_index_is_left_alone(adapter):
    w = rig.world(adapter)

    async def body():
        for org in (rig.ORG_A, rig.ORG_B, rig.ORG_A):
            await rig.admit(w, org)
        await w.rec.drain()
        await rig.prepare_one(w)
        await w.rec.drain()
        before = await _fairness(w.index)
        assert await w.rec.reconcile() == {}
        assert await _fairness(w.index) == before
        assert w.rec.metrics["rebuilds"] == 0
    run(body)


def test_q3_reconcile__a_queued_job_missing_from_the_index_is_indexed_again(adapter):
    """A candidate lost from the index while its outbox row is acknowledged: nothing but
    the reconciler will ever bring it back."""
    w = rig.world(adapter)

    async def body():
        jobs = [await rig.admit(w) for _ in range(2)]
        await w.rec.drain()
        await w.index.remove(jobs[0])
        w.h.clock.advance(4.0)
        assert await w.rec.reconcile() == {"missing": 1, "repaired": 1}
        assert w.rec.metrics["missing_index"] == 1
        assert w.rec.metrics["missing_lag_s"] == 4.0
        assert sorted((await rig.members(w)).values()) == sorted(jobs)
        assert await w.rec.reconcile() == {}
        assert w.rec.metrics["missing_index"] == 0
        await rig.finish(w)
        rig.settled(w)
    run(body)


def test_q3_reconcile__a_dead_candidate_is_removed(adapter):
    """Cancelled while indexed, and running while its candidate is still in flight: both
    candidates are dead; the live job's candidate stays."""
    w = rig.world(adapter)

    async def body():
        cancelled, running, live = [await rig.admit(w) for _ in range(3)]
        await w.rec.drain()
        await w.jobs.cancel(rig.ORG_A, w.jobs.jobs[cancelled].admission.job_handle)
        held = await w.index.claim_candidate("prep", kind=rig.PREP)      # FIFO: cancelled
        held = await w.index.claim_candidate("prep", kind=rig.PREP)      # running's
        assert held.job_id == running
        lease = await w.jobs.claim_preparation(running, "prep")          # not acked yet
        assert await w.rec.reconcile() == {"dead": 2}
        assert w.rec.metrics["dead_candidates"] == 2
        assert list((await rig.members(w)).values()) == [live]
        await w.jobs.prepared(lease, ())
        await w.index.acknowledge(held)
        await rig.finish(w)
        rig.settled(w)
    run(body)


def test_q3_reconcile__an_acknowledged_candidate_postgresql_still_wants_forces_a_rebuild(
        adapter):
    """A worker took the candidate, its claim never reached the store, and it acknowledged
    the candidate anyway (the W2 loop always does). The outbox row is acknowledged and
    the index remembers the event as done: `enqueue` alone can never bring it back."""
    w = rig.world(adapter)

    async def body():
        job = await rig.admit(w)
        await w.rec.drain()
        taken = await w.index.claim_candidate("prep", kind=rig.PREP)
        await w.index.acknowledge(taken)                  # the claim failed in transit
        assert await w.index.enqueue(taken) is False      # the index will not re-take it
        assert await w.rec.reconcile() == {"missing": 1, "rebuilt": 1}
        assert w.rec.metrics["rebuilds"] == 1
        assert list((await rig.members(w)).values()) == [job]
        await rig.finish(w)
        rig.settled(w)
    run(body)


def test_q3_reconcile__the_rebuild_is_postgresql_truth(adapter):
    """Preparing and queued jobs are candidates; a job preparing under a live lease, a
    running one and a terminal one are not - whatever the index held before."""
    w = rig.world(adapter)

    async def body():
        waiting, preparing, queued, running, done = [await rig.admit(w) for _ in range(5)]
        await w.rec.drain()
        await w.jobs.claim_preparation(preparing, "prep")
        for job in (queued, running, done):
            await w.jobs.prepared(await w.jobs.claim_preparation(job, "prep"), ())
        await w.jobs.claim(running, "gpu")
        await w.jobs.complete(await w.jobs.claim(done, "gpu"), rig.b.outcome(done, w.h))
        assert await w.rec.rebuild() == 2
        assert sorted((await rig.members(w)).values()) == sorted([waiting, queued])
        assert w.rec.metrics["rebuilds"] == 1
    run(body)


def test_q3_reconcile__a_delivery_behind_a_stale_snapshot_survives_the_rebuild(adapter):
    """Q2's hole: snapshot read, then a job prepared and its dispatch delivered and
    acknowledged, then the rebuild. The rebuild wipes the delivery and the outbox will
    never repeat it; the snapshot read after the rebuild puts it back."""
    w = rig.world(adapter)

    async def body():
        first = await rig.admit(w)
        second = []

        async def deliver():
            second.append(await rig.admit(w))
            assert await w.rec.drain() == {"read": 2, "indexed": 2, "acknowledged": 2}

        w.rec.store = Interleave(w.outbox, 1, deliver)
        assert await w.rec.rebuild() == 2
        (second,) = second
        assert sorted((await rig.members(w)).values()) == sorted([first, second])
        assert w.outbox.unacknowledged() == []
        await rig.finish(w)
        rig.settled(w)
    run(body)


def test_q3_fence__a_stale_candidate_never_acquires_a_second_lease(adapter):
    """A rebuild from a snapshot read before a claim re-indexes a job that is already
    running. The candidate is offered again; the store refuses it; one lease."""
    w = rig.world(adapter)

    async def body():
        job = await rig.admit(w)
        await w.rec.drain()
        await rig.prepare_one(w)                             # prepared, queued
        await w.rec.drain()
        first = []

        async def claim():
            first.append(await rig.infer_one(w, "gpu-1", finish=False))

        w.rec.store = Interleave(w.outbox, 1, claim)
        await w.rec.rebuild()
        ((_candidate, lease),) = first
        assert lease is not None and rig.state(w, job) is rig.JobState.running
        again, refused = await rig.infer_one(w, "gpu-2")
        assert again.job_id == job and refused is None
        assert w.leases[job] == 1
        await w.jobs.complete(lease, rig.b.outcome(job, w.h))
        rig.settled(w)
    run(body)


# --- (3) switching adapters ---------------------------------------------------------

@pytest.mark.parametrize("old,new", [("memory", "valkey"), ("valkey", "memory")])
def test_q3_switch__a_live_pipeline_moves_to_the_other_adapter_and_loses_no_job(old, new):
    """Mid-run: candidates pending, one in flight with a preparation worker, one job
    running, one row not yet delivered. After the switch the workers claim from the new
    index; the one still holding an old candidate finishes and acknowledges it there."""
    if _valkey_down:
        pytest.skip(f"task-local Valkey unavailable: {_valkey_down}")
    w = rig.world(old)

    async def body():
        for org in (rig.ORG_A, rig.ORG_B) * 3:
            await rig.admit(w, org)
        await w.rec.drain()
        for _ in range(3):
            await rig.prepare_one(w)
        await w.rec.drain()
        _candidate, running = await rig.infer_one(w, finish=False)
        held = await w.index.claim_candidate("prep", kind=rig.PREP)
        await rig.admit(w)                                   # not delivered yet
        old_index = w.index
        fresh = rig.make_index(new, w.h.clock.now)
        # PostgreSQL's six: three preparing (the held one too), two queued, and the one
        # whose row the drain has not delivered - the running one is not a candidate
        assert await w.rec.switch(fresh) == 6
        w.index = fresh                                      # the workers, re-pointed
        await w.jobs.prepared(await w.jobs.claim_preparation(held.job_id, "prep"), ())
        await old_index.acknowledge(held)
        await w.jobs.complete(running, rig.b.outcome(running.job_id, w.h))
        await rig.finish(w)
        assert sorted(w.leases) == sorted(w.admitted)
        assert w.outbox.unacknowledged() == []
        rig.settled(w)
    run(body)


def test_q3_switch__a_delivery_into_the_old_index_during_the_switch_reaches_the_new_one(
        adapter):
    """The drain still points at the old index while the new one is being rebuilt. A
    dispatch it delivers there - after the rebuild's snapshots, before the swap - is
    acknowledged in the outbox, so only the snapshot read after the swap can carry it
    over."""
    w = rig.world(adapter)

    async def body():
        await rig.admit(w)
        await w.rec.drain()
        late = []

        async def deliver_to_the_old_index():
            late.append(await rig.admit(w))
            assert await w.rec.drain() == {"read": 1, "indexed": 1, "acknowledged": 1}

        w.rec.store = Interleave(w.outbox, 2, deliver_to_the_old_index)
        fresh = rig.make_index("memory", w.h.clock.now)
        await w.rec.switch(fresh)
        assert late[0] in (await fresh.members()).values()
        w.index = fresh
        await rig.finish(w)
        rig.settled(w)
    run(body)


async def _fairness(index):
    if isinstance(index, rig.ValkeyScheduler):
        snap = await index.snapshot()
        return snap["tags"], snap["virtual_times"], snap["stats"]
    return index.tags(), index.virtual_times(), index.stats()


async def _stats(index):
    stats = index.stats()
    return await stats if asyncio.iscoroutine(stats) else stats
