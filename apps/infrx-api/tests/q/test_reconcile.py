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
import collections
import dataclasses
import os

import pytest

from infrx.contracts import errors
from infrx.contracts.limits import PilotSettings
from infrx.scheduling import MAX_INDEX_BYTES, MAX_INDEX_ITEMS

from . import q3differential as differential
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


def test_q3_drain__a_full_index_hands_the_row_back_and_the_next_drain_retries_it(adapter):
    """A full index stops the drain and hands the refused row back (D2's
    `release_dispatch`, review DUR-6): the next drain retries it at once, not after
    `redelivery_s`, and it takes the first slot that frees."""
    w = rig.world(adapter, max_items=2)
    w.rec.batch = 3                     # a full batch: only the refusal stops the scan

    async def body():
        jobs = await rig.admit_in_order(w, 3)
        assert await w.rec.drain() == {"read": 3, "indexed": 2, "deferred": 1,
                                       "acknowledged": 2}
        (deferred,) = w.outbox.unacknowledged()
        assert await w.rec.drain() == {"read": 1, "deferred": 1}     # still full
        await rig.prepare_one(w)                          # frees one slot
        assert await w.rec.drain() == {"read": 2, "indexed": 1, "acknowledged": 1,
                                       "deferred": 1}
        assert deferred not in w.outbox.unacknowledged()  # it took the slot
        await rig.finish(w)
        assert sorted(w.leases) == sorted(jobs)
        rig.settled(w)
    run(body)


def test_q3_drain__an_index_outage_acknowledges_only_what_was_indexed(adapter):
    """The index dies on the 2nd enqueue: the 1st is acknowledged, the 3rd - never
    tried - is handed back and indexed by the next drain, and the 2nd keeps its claim
    until `redelivery_s`, so a row the index keeps rejecting cannot block the rows
    behind it (review DUR-6)."""
    w = rig.world(adapter)
    w.rec.index = FlakyIndex(w.index, fail_on=2)

    async def body():
        jobs = await rig.admit_in_order(w, 3)
        with pytest.raises(ConnectionError):
            await w.rec.drain()
        assert list((await rig.members(w)).values()) == jobs[:1]
        assert len(w.outbox.unacknowledged()) == 2
        assert await w.rec.drain() == {"read": 1, "indexed": 1, "acknowledged": 1}
        assert sorted((await rig.members(w)).values()) == sorted([jobs[0], jobs[2]])
        w.h.clock.advance(w.rec.redelivery_s)
        assert await w.rec.drain() == {"read": 1, "indexed": 1, "acknowledged": 1}
        assert sorted((await rig.members(w)).values()) == sorted(jobs)
    run(body)


def test_q3_drain__a_superseded_row_is_acknowledged_by_the_store_and_uses_its_slot(
        adapter):
    """A job prepared before its `prepare_dispatch` row was drained: the row names work
    the job no longer wants, so the store acknowledges it as `superseded` and hands out
    nothing - and, as in SQL, it used up the read's `limit` (review FID-3)."""
    w = rig.world(adapter)
    w.rec.batch = 1

    async def body():
        job = await rig.admit(w)
        (prep_row,) = w.outbox.unacknowledged()
        w.h.clock.advance(1e-6)                    # the inference row sorts after it
        await w.jobs.prepared(await w.jobs.claim_preparation(job, "prep"), ())
        assert await w.rec.drain() == {}
        assert w.outbox.last_error.get(prep_row) == "superseded"
        assert await rig.members(w) == {}
        assert await w.rec.drain() == {"read": 1, "indexed": 1, "acknowledged": 1}
        assert list((await rig.members(w)).values()) == [job]
        await rig.finish(w)
        rig.settled(w)
    run(body)


class AfterEnqueue:
    """An index that runs `action` once, right after the first `enqueue` lands."""

    def __init__(self, inner, action) -> None:
        self.inner, self.action = inner, action

    async def enqueue(self, event):
        taken = await self.inner.enqueue(event)
        if self.action is not None:
            action, self.action = self.action, None
            await action()
        return taken

    def __getattr__(self, name):
        return getattr(self.inner, name)


def test_q3_drain__an_acknowledgment_behind_another_relays_rebuild_fence_is_refused(adapter):
    """D2's `OutboxRelay` in a second process (OB-1/OB-1b): it reads its fence and a
    snapshot, then - after this drain indexed a job admitted since, before its
    acknowledgment - rebuilds from that snapshot and reopens. The rebuild wiped the
    candidate and the reopen took this relay's claim, so the late acknowledgment is
    refused and the row is sent again: Q3's drain honours D2's fence."""
    w = rig.world(adapter)

    async def body():
        since = await w.outbox.db_now()
        stale = await w.outbox.dispatch_snapshot()              # before the job exists
        job = await rig.admit(w)

        async def the_other_relays_rebuild():
            await w.index.rebuild(stale)                        # wipes the new candidate
            assert await w.outbox.reopen_dispatch(since) == 1

        w.rec.index = AfterEnqueue(w.index, the_other_relays_rebuild)
        assert await w.rec.drain() == {"read": 1, "indexed": 1}     # the ack: refused
        assert await rig.members(w) == {}
        assert len(w.outbox.unacknowledged()) == 1
        w.rec.index = w.index
        assert await w.rec.drain() == {"read": 1, "indexed": 1, "acknowledged": 1}
        assert list((await rig.members(w)).values()) == [job]
        await rig.finish(w)
        rig.settled(w)
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
        assert await w.rec.drain() == {}
        assert w.rec.metrics["outbox_lag_s"] == 0.0      # caught up: no lag (DUR-3)
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
        jobs = [await rig.admit(w)]
        w.h.clock.advance(1.5)
        jobs += [await rig.admit(w) for _ in range(2)]
        await w.rec.drain()
        for job in jobs[:2]:
            await w.index.remove(job)
        w.h.clock.advance(4.0)
        assert await w.rec.reconcile() == {"missing": 2, "repaired": 2}
        assert w.rec.metrics["missing_index"] == 2
        assert w.rec.metrics["missing_lag_s"] == 5.5            # the oldest missing one
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


class RacedIndex:
    """An index another relay process writes to first: every `enqueue` from here finds
    the candidate already delivered (it indexes it, then answers "already indexed")."""

    def __init__(self, inner) -> None:
        self.inner = inner

    async def enqueue(self, event):
        await self.inner.enqueue(event)
        return False

    def __getattr__(self, name):
        return getattr(self.inner, name)


def test_q3_reconcile__a_candidate_delivered_concurrently_is_not_taken_for_an_acknowledged_one(
        adapter):
    """`enqueue` answering "already there" is ambiguous: acknowledged, or delivered by
    another relay since the pass read the index. Only a second read tells them apart,
    and only the acknowledged kind is worth a rebuild."""
    w = rig.world(adapter)

    async def body():
        job = await rig.admit(w)
        w.rec.index = RacedIndex(w.index)
        assert await w.rec.reconcile() == {"missing": 1}
        assert w.rec.metrics["rebuilds"] == 0
        assert list((await rig.members(w)).values()) == [job]
    run(body)


def test_q3_reconcile__a_job_redispatched_during_the_pass_keeps_its_new_candidate(adapter):
    """A running job's candidate is dead at the snapshot; before the pass removes it the
    lease expires, the reaper requeues the job and the drain delivers the new dispatch.
    `remove` takes both candidates of the job; the snapshot read after the removal puts
    the new one back."""
    w = rig.world(adapter)

    async def body():
        job = await rig.admit(w)
        await w.rec.drain()
        await rig.prepare_one(w)
        await w.rec.drain()
        taken = await w.index.claim_candidate("gpu", kind=rig.INFER)   # held in flight
        await w.jobs.claim(job, "gpu")

        async def requeued_and_delivered():
            w.h.clock.advance(w.jobs.limits.lease_ttl_s)
            await w.jobs.recover()
            assert await w.rec.drain() == {"read": 1, "indexed": 1, "acknowledged": 1}

        w.rec.store = Interleave(w.outbox, 1, requeued_and_delivered)
        assert await w.rec.reconcile() == {"dead": 1, "missing": 1, "repaired": 1}
        (fresh,) = (await rig.members(w)).items()
        assert fresh[1] == job and fresh[0] != taken.event_id
    run(body)


def test_q3_reconcile__a_rebuild_is_recovery_and_ignores_the_caps(adapter):
    w = rig.world(adapter, max_items=2)

    async def body():
        jobs = [await rig.admit(w) for _ in range(4)]
        assert await w.rec.rebuild() == 4
        assert sorted((await rig.members(w)).values()) == sorted(jobs)
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


def test_q3_run__the_relay_reconciles_first_and_retries_a_failed_pass(adapter):
    """The loop's first tick is a reconcile, and a pass that fails stays due: with an
    hour between passes, the repair still lands on the tick after the failure."""
    w = rig.world(adapter)

    async def body():
        jobs = [await rig.admit(w) for _ in range(2)]
        await w.rec.drain()
        await w.index.remove(jobs[0])                  # lost while the relay was down

        async def outage():
            raise ConnectionError("store unreachable")

        w.rec.store = Interleave(w.outbox, 1, outage)
        stop = asyncio.Event()
        task = asyncio.create_task(w.rec.run(stop, drain_every_s=0.001,
                                             reconcile_every_s=3600))
        for _ in range(500):
            if len(await rig.members(w)) == 2:
                break
            await asyncio.sleep(0.002)
        stop.set()
        assert await asyncio.gather(task, return_exceptions=True) == [None]
        assert w.rec.metrics["errors"] == 1
        assert sorted((await rig.members(w)).values()) == sorted(jobs)
    run(body)


class SnapshotDown:
    """The store, except that `dispatch_snapshot` always fails (the unbounded snapshot
    timing out under a backlog) and the first `dispatch_pending` fails once."""

    def __init__(self, store) -> None:
        self.store, self.pending_failures = store, 1

    async def dispatch_snapshot(self):
        raise ConnectionError("snapshot timed out")

    async def dispatch_pending(self, **kw):
        if self.pending_failures:
            self.pending_failures -= 1
            raise ConnectionError("store unreachable")
        return await self.store.dispatch_pending(**kw)

    def __getattr__(self, name):
        return getattr(self.store, name)


def test_q3_run__a_pass_that_keeps_failing_never_stops_the_drain(adapter):
    """Review DUR-1: every reconcile pass fails, and the drain still delivers every
    dispatch; a drain that fails once is retried at the next tick."""
    w = rig.world(adapter)

    async def body():
        jobs = [await rig.admit(w) for _ in range(3)]
        w.rec.store = SnapshotDown(w.outbox)
        stop = asyncio.Event()
        task = asyncio.create_task(w.rec.run(stop, drain_every_s=0.001,
                                             reconcile_every_s=0.01))
        for _ in range(500):
            if len(await rig.members(w)) == 3:
                break
            await asyncio.sleep(0.002)
        stop.set()
        assert await asyncio.gather(task, return_exceptions=True) == [None]
        assert sorted((await rig.members(w)).values()) == sorted(jobs)
        assert w.outbox.unacknowledged() == []
        assert w.rec.metrics["errors"] >= 2          # the drain's one and the passes
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


def test_q3_switch__a_delivery_after_the_post_swap_snapshot_lands_in_the_new_index(adapter):
    """Review DUR-2: the top-up's snapshot is read AFTER the drain points at the new
    index, so a dispatch delivered between that read and the top-up goes straight into
    the new index. Read before the swap, it would land in the old one and the (stale)
    snapshot could not carry it over."""
    w = rig.world(adapter)

    async def body():
        await rig.admit(w)
        await w.rec.drain()
        late = []

        async def deliver():
            late.append(await rig.admit(w))
            assert await w.rec.drain() == {"read": 1, "indexed": 1, "acknowledged": 1}

        w.rec.store = Interleave(w.outbox, 3, deliver)
        fresh = rig.make_index("memory", w.h.clock.now)
        await w.rec.switch(fresh)
        assert late[0] in (await fresh.members()).values()
        w.index = fresh
        await rig.finish(w)
        rig.settled(w)
    run(body)


# --- (4) index loss ----------------------------------------------------------------

def _need_valkey():
    if _valkey_down:
        pytest.skip(f"task-local Valkey unavailable: {_valkey_down}")


async def _lost(w) -> None:
    """SIGKILL the server under the adapter; prove the index really went (a sentinel key
    written before is gone after), and drop the pool's dead connections."""
    sentinel = f"{w.index.namespace}:sentinel"
    await w.index.client.set(sentinel, "1")
    await asyncio.to_thread(vkharness.kill_and_restart)
    await w.index.client.connection_pool.disconnect()
    assert await w.index.client.exists(sentinel) == 0, "the index survived: not a loss"


def test_q3_drill__dr13_shape_the_rebuild_after_a_sigkill_comes_from_postgresql():
    """E3B dr13 with the snapshot read from the store (request 5): four accepted jobs
    queued, one run to completion, Valkey SIGKILLed; the rebuild from `dispatch_snapshot`
    dispatches the three still queued exactly once and the finished one never."""
    _need_valkey()
    w = rig.world("valkey")

    async def body():
        jobs = [await rig.admit(w) for _ in range(4)]
        await w.rec.drain()
        for _ in jobs:
            await rig.prepare_one(w)
        await w.rec.drain()
        (first, _lease) = await rig.infer_one(w)
        await _lost(w)
        assert await w.index.depth() == 0
        assert await w.rec.rebuild() == 3
        dispatched = []
        while (step := await rig.infer_one(w)) is not None:
            dispatched.append(step[0].job_id)
            assert len(dispatched) <= 3, dispatched
        assert sorted(dispatched) == sorted(set(jobs) - {first.job_id})
        assert w.outbox.unacknowledged() == []
        rig.settled(w)
    run(body)


def test_q3_drill__an_acknowledgment_after_a_rebuild_is_repaired_by_the_next_pass(adapter):
    """The other half of Q2's hole. A worker holds a candidate across a rebuild that
    re-indexes it, its claim never reaches the store, and it acknowledges the candidate:
    the re-indexed copy is gone and the event is remembered as done. The next pass sees
    a job PostgreSQL wants and the index will not take, and rebuilds."""
    w = rig.world(adapter)

    async def body():
        job = await rig.admit(w)
        await w.rec.drain()
        held = await w.index.claim_candidate("prep", kind=rig.PREP)
        assert await w.rec.rebuild() == 1                     # re-indexed: still wanted
        await w.index.acknowledge(held)                       # the claim never landed
        assert await rig.members(w) == {}
        assert await w.rec.reconcile() == {"missing": 1, "rebuilt": 1}
        assert list((await rig.members(w)).values()) == [job]
        await rig.finish(w)
        rig.settled(w)
    run(body)


def test_q3_reconcile__an_acknowledgment_inside_the_rebuild_is_repaired_by_the_rebuild(
        adapter):
    """Review DUR-4: the worker's acknowledgment lands between the rebuild and its
    top-up. The rebuild runs once more, so the job is indexed when `rebuild()` returns,
    not a whole reconcile period later."""
    w = rig.world(adapter)

    async def body():
        job = await rig.admit(w)
        await w.rec.drain()
        held = await w.index.claim_candidate("prep", kind=rig.PREP)

        async def acknowledged():
            await w.index.acknowledge(held)                   # the claim never landed

        w.rec.store = Interleave(w.outbox, 2, acknowledged)
        await w.rec.rebuild()
        assert list((await rig.members(w)).values()) == [job]
        assert w.rec.metrics["rebuilds"] == 2
        await rig.finish(w)
        rig.settled(w)
    run(body)


def test_q3_drill__valkey_sigkilled_under_queued_and_running_traffic_loses_no_job():
    """The acceptance. A drain loop, a reconcile loop, two preparation and two inference
    workers run concurrently over real Valkey while jobs keep arriving; one inference
    worker holds its lease across the kill. Valkey is SIGKILLed mid-run and comes back
    empty. Every loop that touches the server during the outage feels it and retries
    (the printout lists every worker; the lease-holding one waits on the kill and may
    feel nothing), and every loop outlives it. At the end every accepted job is
    terminal, no job ever held a second lease, and every dispatch row is acknowledged."""
    _need_valkey()
    import valkey.exceptions

    w = rig.world("valkey")
    transient = (ConnectionError, OSError, valkey.exceptions.ValkeyError,
                 errors.InternalError)            # Q2: claim-attempt exhaustion retries

    async def body():
        stop, killed = asyncio.Event(), asyncio.Event()
        felt = dict.fromkeys(("prep-1", "prep-2", "gpu-1", "gpu-slow"), 0)   # HON-1

        async def forever(name, step):
            while not stop.is_set():
                try:
                    if not await step():
                        await asyncio.sleep(0.002)
                except transient:
                    felt[name] += 1
                    await asyncio.sleep(0.01)

        async def slow_gpu():
            step = await rig.infer_one(w, "gpu-slow", finish=False)
            if step is None:
                return False
            candidate, lease = step
            if lease is not None:
                await killed.wait()                  # holds the lease across the kill
                await w.jobs.complete(lease, rig.b.outcome(candidate.job_id, w.h))
            return True

        loops = [asyncio.create_task(w.rec.run(stop, drain_every_s=0.002,
                                               reconcile_every_s=0.02))]
        loops += [asyncio.create_task(forever(name, step)) for name, step in (
            ("prep-1", lambda: rig.prepare_one(w, "prep-1")),
            ("prep-2", lambda: rig.prepare_one(w, "prep-2")),
            ("gpu-1", lambda: rig.infer_one(w, "gpu-1")),
            ("gpu-slow", slow_gpu))]
        for wave in range(2):
            for index in range(12):
                await rig.admit(w, (rig.ORG_A, rig.ORG_B)[index % 2])
                await asyncio.sleep(0.003)
            if wave == 0:
                while not w.leases:                  # something is running
                    await asyncio.sleep(0.005)
                await _lost(w)
                killed.set()
        for _ in range(3000):
            if all(w.jobs.jobs[job].terminal for job in w.admitted):
                break
            await asyncio.sleep(0.01)
        stop.set()
        ended = await asyncio.gather(*loops, return_exceptions=True)
        print(f"\nworkers that felt the outage: {felt}; relay errors "
              f"{w.rec.metrics['errors']}, rebuilds {w.rec.metrics['rebuilds']}; "
              f"leases {sum(w.leases.values())}")
        assert any(felt.values()) and w.rec.metrics["errors"], \
            "the kill did not happen under traffic"
        rig.settled(w)
        assert sorted(w.leases) == sorted(w.admitted)
        assert all(w.jobs.jobs[job].state is rig.JobState.succeeded for job in w.admitted)
        assert w.outbox.unacknowledged() == []
        assert [e for e in ended if isinstance(e, BaseException)] == [], ended
    run(body)


# --- (5) the index caps come from the settings ---------------------------------------

@dataclasses.dataclass(frozen=True)
class _PilotWithCaps(PilotSettings):
    """`PilotSettings` as F2P's wire-in extends it (the two names 08 §5 gives the caps)."""
    max_index_items: int = 2
    max_index_bytes: int = 268_435_456


def test_q3_caps__both_adapters_take_the_caps_from_the_settings(adapter):
    """With the fields present the settings bind; without them Q1's constants do; an
    explicit argument wins over both."""
    async def body():
        now = rig.jobstore_factory().clock.now
        configured = rig.make_index(adapter, now, limits=_PilotWithCaps())
        assert (configured._max_items, configured._max_bytes) == (2, 268_435_456)
        plain = rig.make_index(adapter, now)
        assert (plain._max_items, plain._max_bytes) == (MAX_INDEX_ITEMS, MAX_INDEX_BYTES)
        explicit = rig.make_index(adapter, now, limits=_PilotWithCaps(), max_items=7)
        assert explicit._max_items == 7
        w = rig.world(adapter, limits=_PilotWithCaps(max_index_bytes=10**6))
        for _ in range(3):
            await rig.admit(w)
        assert await w.rec.drain() == {"read": 3, "indexed": 2, "deferred": 1,
                                       "acknowledged": 2}
    run(body)


# --- the differential ---------------------------------------------------------------
FULL_DIFFERENTIAL = os.environ.get("INFRX_Q3_DIFFERENTIAL", "").lower() in ("all", "1")


def test_q3_differential__the_reconciler_agrees_on_both_adapters():
    """No stream of drains, passes, rebuilds, worker steps, losses, faults and clock jumps
    tells the two adapters apart, and on each one no job ever holds a lease it should not
    and no pass leaves a wanted job unindexed. `INFRX_Q3_DIFFERENTIAL=all`: 20 x 1,000."""
    _need_valkey()
    seeds = differential.SEEDS if FULL_DIFFERENTIAL else (1, 2, 3, 4)
    steps = differential.STEPS if FULL_DIFFERENTIAL else 250
    counters: collections.Counter = collections.Counter()
    compared = asyncio.run(differential.run_seeds(seeds, steps, counters))
    print(f"\nq3 differential: {len(seeds)} seeds x {steps} operations = {compared} "
          f"compared states; outcomes {dict(sorted(counters.items()))}")
    assert compared == len(seeds) * steps
    for path in ("reconcile:missing", "reconcile:dead", "reconcile:rebuilt",
                 "drain:raised:OutboxLost", "lose", "transit"):
        assert counters[path] > 0, f"the stream never took {path}"


def test_q3_differential__the_harness_notices_a_divergence():
    """R32 honesty: an adapter whose `members()` forgets in-flight candidates makes the
    pass think they are missing, and the run must fail on it."""
    _need_valkey()

    class ForgetsInFlight(rig.ValkeyScheduler):
        async def members(self):
            held = {v.decode() if isinstance(v, bytes) else v
                    for v in await self.client.zrange(self._keys[3], 0, -1)}
            return {k: v for k, v in (await super().members()).items() if k not in held}

    def broken(w):
        return ForgetsInFlight(vkharness.client(), w.h.clock.now,
                               namespace=vkharness.namespace())

    with pytest.raises(AssertionError) as caught:
        asyncio.run(differential.run_stream(1, 250, valkey=broken))
    assert "diverged" in str(caught.value), caught.value


async def _fairness(index):
    if isinstance(index, rig.ValkeyScheduler):
        snap = await index.snapshot()
        return snap["tags"], snap["virtual_times"], snap["stats"]
    return index.tags(), index.virtual_times(), index.stats()


async def _stats(index):
    stats = index.stats()
    return await stats if asyncio.iscoroutine(stats) else stats
