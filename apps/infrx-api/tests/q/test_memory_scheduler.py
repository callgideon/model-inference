#!/usr/bin/env python3
"""Q1 F-CONTRACT / DUR-OUTBOX: the real memory scheduler under the shared suite.

The same exported cases the fake passes (`run_scheduler_conformance`) plus the
behaviour the fake does not have: weighted fair selection, per-kind streams, caps,
cancellation and rebuild semantics. Every test id names the invariant it claims, and
`tests/q/mutants.py` must be able to kill each one.

    uv run --frozen pytest -q tests/q
"""
from __future__ import annotations

import asyncio
import inspect

import pytest
from infrx.contracts import errors, ports
from infrx.contracts.conformance import (MissingHook, run_cases, run_scheduler_conformance,
                                          scheduler_cases)
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import DISPATCH_KINDS, OutboxKind

from . import support
from .support import (JOB_1, JOB_2, JOB_3, ORG_A, ORG_B, ORG_C, drain, event,
                      harness)

PREPARE, INFER = OutboxKind.prepare_dispatch, OutboxKind.inference_dispatch
CASES = scheduler_cases()


# ==========================================================================
# the shared contract, against the real adapter
# ==========================================================================
@pytest.mark.parametrize("case", CASES, ids=[case.__name__ for case in CASES])
def test_q1_contract__the_real_adapter_passes_the_exported_case(case):
    """F-CONTRACT: the adapter Q owns passes the same case the fake does, driven by
    the coordinator's harness and the shared fake JobStore."""
    try:
        asyncio.run(case(harness))
    except MissingHook as missing:      # pragma: no cover - the factory supplies them all
        pytest.skip(f"{case.__name__} needs the optional hook {missing.hook!r}")


def test_q1_contract__the_whole_suite_runs_and_skips_nothing():
    """R32: a case skipped for a missing hook is a skip, never a pass. The count and
    the (empty) skip list are printed so the evidence quotes output instead of prose."""
    skipped: list[MissingHook] = []
    ran = run_cases(CASES, harness, skipped=skipped)
    print(f"\nscheduler conformance: {ran} ran, {len(skipped)} skipped "
          f"{[(s.case, s.hook) for s in skipped]}")
    assert skipped == []
    assert ran == len(CASES)
    assert run_scheduler_conformance(harness) == len(CASES)


def test_q1_contract__the_adapter_satisfies_the_scheduler_protocol():
    port = harness().port
    assert isinstance(port, ports.Scheduler)
    for name in ports.Scheduler.__protocol_attrs__:
        assert inspect.iscoroutinefunction(getattr(port, name)), f"{name} is not async"


# ==========================================================================
# weighted fair selection
# ==========================================================================
def test_q1_fair__tenants_alternate_and_ties_break_on_arrival():
    """Fairness: with equal weights the dispatch order round-robins the tenants, and
    two flows with the same virtual time are separated by arrival order - never by
    dict order, an org id or anything else that would make the order unreproducible."""
    async def run():
        h = harness()
        port = h.port
        for _ in range(3):
            assert await port.enqueue(event(h, org_id=ORG_A))
        for _ in range(3):
            assert await port.enqueue(event(h, org_id=ORG_B))
        order = [candidate.org_id for candidate in await drain(port)]
        assert order == [ORG_A, ORG_B] * 3, order
    asyncio.run(run())


def test_q1_fair__a_noisy_tenant_cannot_delay_a_peer_past_one_round():
    """Q1 acceptance: a noisy tenant cannot starve bounded peers. A peer's wait is
    bounded in *dispatch slots* by the number of competing tenants, whatever the
    backlog of the noisy one - 30 queued requests do not buy 30 slots in a row."""
    async def run():
        h = harness()
        port = h.port
        for _ in range(30):
            assert await port.enqueue(event(h, org_id=ORG_A))
        for org in (ORG_B, ORG_C):
            for _ in range(3):
                assert await port.enqueue(event(h, org_id=org))
        order = [candidate.org_id for candidate in await drain(port)]
        tenants = 3
        for peer in (ORG_B, ORG_C):
            slots = [index for index, org in enumerate(order) if org == peer]
            assert len(slots) == 3
            assert slots[0] < tenants, f"{peer} waited {slots[0]} slots for its first"
            gaps = [second - first for first, second in zip(slots, slots[1:])]
            assert all(gap <= tenants for gap in gaps), f"{peer} waited {gaps} slots"
        assert order[:tenants] == [ORG_A, ORG_B, ORG_C]
    asyncio.run(run())


def test_q1_fair__weight_buys_a_proportional_share():
    """The weight is the fair-share knob (`03` §2.3: `weight[t]` defaults to 1 and is
    the dedicated-capacity knob). A tenant weighted 3 is dispatched three times as
    often as its peer while both are backlogged."""
    async def run():
        h = harness(weights={ORG_A: 3.0})
        port = h.port
        for _ in range(12):
            assert await port.enqueue(event(h, org_id=ORG_A))
            assert await port.enqueue(event(h, org_id=ORG_B))
        order = [candidate.org_id for candidate in await drain(port)]
        first_twelve = order[:12]
        assert first_twelve.count(ORG_A) == 9 and first_twelve.count(ORG_B) == 3, first_twelve
    asyncio.run(run())


def test_q1_fair__the_virtual_finish_time_advances_at_dispatch_only():
    """`03` §2.3's sketch advances the tag at *enqueue*; this index advances it at
    dispatch, so a tenant is charged for service it received, not for service it asked
    for. A tenant that enqueues a burst and cancels it carries no penalty."""
    async def run():
        h = harness()
        port = h.port
        for _ in range(5):
            assert await port.enqueue(event(h, org_id=ORG_A, job_id=JOB_1))
        assert port.tags() == {(INFER.value, ORG_A): 0.0}, "enqueue moved the tag"
        first = await port.claim_candidate("worker-a")
        assert port.tags() == {(INFER.value, ORG_A): 1.0}, "dispatch did not move the tag"
        await port.acknowledge(first)
        await port.remove(JOB_1)
        assert port.tags() == {}, "a cancelled burst left fairness state behind"
        # and the tenant that cancelled is not behind its peer afterwards
        assert await port.enqueue(event(h, org_id=ORG_A))
        assert await port.enqueue(event(h, org_id=ORG_B))
        assert [candidate.org_id for candidate in await drain(port)] == [ORG_A, ORG_B]
    asyncio.run(run())


def test_q1_fair__an_idle_tenant_accumulates_no_credit_and_a_backlogged_one_is_not_overtaken():
    """Both halves of the starvation rule. A tenant that is absent while another is
    served does not come back with a claim on every slot it missed (the arriving flow
    starts at the index's virtual time), and the tenant that was being served is not
    pushed behind for ever either (the served flow's tag only advances by what it got).
    """
    async def run():
        h = harness()
        port = h.port
        for _ in range(4):
            assert await port.enqueue(event(h, org_id=ORG_A))
        for _ in range(2):          # A alone is served twice
            await port.acknowledge(await port.claim_candidate("worker-a"))
        assert await port.enqueue(event(h, org_id=ORG_B))
        assert await port.enqueue(event(h, org_id=ORG_B))
        order = [candidate.org_id for candidate in await drain(port)]
        # B arrives level with A: one slot each, not both of B's before A's remainder.
        assert order == [ORG_B, ORG_A, ORG_B, ORG_A], order
    asyncio.run(run())


def test_q1_fair__a_candidate_that_waited_catches_up_once_and_cannot_hoard():
    """The other side of the same clamp. A candidate that was not yet available while
    its peer was served is dispatched as soon as it can be - one slot of catch-up, which
    is what makes a scheduled retry prompt - but its flow is then pulled up to the
    index's virtual time, so a tenant that waited cannot claim every slot it missed."""
    async def run():
        h = harness()
        port = h.port
        for _ in range(6):
            assert await port.enqueue(event(h, org_id=ORG_A))
        for _ in range(4):
            assert await port.enqueue(event(h, org_id=ORG_B, available_in_s=60))
        served = []
        for _ in range(5):
            candidate = await port.claim_candidate("worker-a")
            assert candidate is not None
            served.append(candidate.org_id)
        assert served == [ORG_A] * 5, served      # B is not available yet
        h.clock.advance(60)
        catching_up = [c.org_id for c in await drain(port)]
        assert catching_up[:3] == [ORG_B, ORG_A, ORG_B], catching_up
    asyncio.run(run())


def test_q1_config__a_weight_that_would_break_dispatch_is_refused_where_it_is_set():
    """A zero, negative, NaN or infinite weight is a division by zero or a poisoned
    comparison inside dispatch. It is refused at construction, not discovered later."""
    for bad in (0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            harness(weights={ORG_A: bad})


# ==========================================================================
# separate streams per dispatch kind (R52)
# ==========================================================================
def test_q1_kind__preparation_and_inference_are_separately_fair():
    """R52: the two kinds are different worker pools, so they are different flows. A
    tenant's transcodes must not push it back in the inference line, and a preparation
    pool asking for work never sees an inference candidate."""
    async def run():
        h = harness()
        port = h.port
        for _ in range(3):
            assert await port.enqueue(event(h, org_id=ORG_A, kind=PREPARE))
        assert await port.enqueue(event(h, org_id=ORG_A, kind=INFER))
        assert await port.enqueue(event(h, org_id=ORG_B, kind=INFER))
        # three preparation dispatches for A do not cost A its turn on the GPU pool
        prepared = [c.kind for c in await drain(port, "prep-a", kind=PREPARE)]
        assert prepared == [PREPARE] * 3
        inferred = [c.org_id for c in await drain(port, "worker-a", kind=INFER)]
        assert inferred == [ORG_A, ORG_B], inferred
    asyncio.run(run())


def test_q1_kind__a_lost_preparation_worker_returns_its_candidate_on_the_preparation_lease():
    """R52: preparation gets the shorter lease (30 s), because a lost transcode worker
    must be noticed early enough for R46's bounded retries to happen inside the
    preparation budget. Visibility follows the lease of the pool that was fed."""
    async def run():
        h = harness()
        port = h.port
        assert await port.enqueue(event(h, org_id=ORG_A, kind=PREPARE))
        assert await port.enqueue(event(h, org_id=ORG_A, kind=INFER))
        prepare = await port.claim_candidate("prep-a", kind=PREPARE)
        infer = await port.claim_candidate("worker-a", kind=INFER)
        assert prepare is not None and infer is not None
        h.clock.advance(DEFAULTS.preparation_lease_ttl_s + 1)
        assert DEFAULTS.preparation_lease_ttl_s + 1 < DEFAULTS.lease_ttl_s
        back = await port.claim_candidate("prep-b", kind=PREPARE)
        assert back is not None and back.event_id == prepare.event_id
        assert await port.claim_candidate("worker-b", kind=INFER) is None, \
            "an inference candidate came back on the preparation lease"
        h.clock.advance(DEFAULTS.lease_ttl_s + 1)
        again = await port.claim_candidate("worker-b", kind=INFER)
        assert again is not None and again.event_id == infer.event_id
    asyncio.run(run())


def test_q1_kind__returned_candidates_keep_their_arrival_order():
    """FIFO inside a tenant survives a lost worker: the candidate that comes back is
    older than everything queued behind it, so it is re-offered *first*, not appended
    behind the two that arrived while the worker was dying."""
    async def run():
        h = harness()
        port = h.port
        first, second, third = (event(h, org_id=ORG_A) for _ in range(3))
        for candidate in (first, second, third):
            assert await port.enqueue(candidate)
        assert (await port.claim_candidate("worker-a")).event_id == first.event_id
        h.clock.advance(DEFAULTS.lease_ttl_s + 1)
        order = [candidate.event_id for candidate in await drain(port, "worker-b")]
        assert order == [first.event_id, second.event_id, third.event_id], order
    asyncio.run(run())


# ==========================================================================
# bounded memory (03 §2.5)
# ==========================================================================
def test_q1_caps__a_full_index_refuses_with_a_typed_retryable_error():
    """`03` §2.5: item and byte caps bound host memory. A full index answers
    `capacity_exhausted` with `Retry-After` so the outbox drain can tell "not indexed,
    come back" from "already indexed" - and the refusal costs throughput, never a job,
    because PostgreSQL still holds it and `rebuild` brings it back."""
    async def run():
        h = harness(max_items=2)
        port = h.port
        assert await port.enqueue(event(h, org_id=ORG_A))
        assert await port.enqueue(event(h, org_id=ORG_A))
        with pytest.raises(errors.CapacityExhausted) as caught:
            await port.enqueue(event(h, org_id=ORG_B))
        assert errors.http_status(caught.value.code) == 429
        assert caught.value.retry_after_s
        assert port.stats()["items"] == 2
        # an acknowledgment frees the slot again
        await port.acknowledge(await port.claim_candidate("worker-a"))
        assert await port.enqueue(event(h, org_id=ORG_B))
    asyncio.run(run())


def test_q1_caps__queued_bytes_are_counted_per_candidate_and_returned():
    """Byte accounting is per candidate and symmetric: what enqueue charges, an
    acknowledgment and a cancellation give back. An index that only counted up would
    refuse for ever after one burst."""
    async def run():
        h = harness()
        port = h.port
        one = event(h, org_id=ORG_A)
        assert await port.enqueue(one)
        charged = port.stats()["bytes"]
        assert charged > 0
        assert await port.enqueue(event(h, org_id=ORG_B, job_id=JOB_2))
        assert port.stats()["bytes"] > charged
        await port.remove(JOB_2)
        assert port.stats()["bytes"] == charged, "cancellation did not return the bytes"
        await port.acknowledge(await port.claim_candidate("worker-a"))
        assert port.stats()["bytes"] == 0
        # and the byte cap binds independently of the item cap
        tiny = harness(max_bytes=charged)
        assert await tiny.port.enqueue(event(tiny, org_id=ORG_A))
        with pytest.raises(errors.CapacityExhausted):
            await tiny.port.enqueue(event(tiny, org_id=ORG_A))
    asyncio.run(run())


# ==========================================================================
# cancellation and stale fairness state
# ==========================================================================
def test_q1_cancel__removal_drops_pending_and_in_flight_candidates_and_their_flow():
    """Q1 acceptance: empty and cancelled tenants retain no fairness state. A
    cancelled job's candidates go whether they are pending or in flight, and when the
    tenant's last candidate goes so does its flow - a tenant that comes back tomorrow
    must not inherit yesterday's virtual time."""
    async def run():
        h = harness()
        port = h.port
        assert await port.enqueue(event(h, org_id=ORG_A, job_id=JOB_1))
        assert await port.enqueue(event(h, org_id=ORG_A, job_id=JOB_1, kind=PREPARE))
        assert await port.enqueue(event(h, org_id=ORG_A, job_id=JOB_2))
        assert await port.enqueue(event(h, org_id=ORG_B, job_id=JOB_3))
        in_flight = await port.claim_candidate("worker-a", kind=INFER)
        assert in_flight is not None and in_flight.job_id == JOB_1
        await port.remove(JOB_1)
        assert port.stats()["items"] == 2 and port.stats()["inflight"] == 0
        assert set(port.tags()) == {(INFER.value, ORG_A), (INFER.value, ORG_B)}, \
            "the cancelled preparation flow was left behind"
        # the in-flight candidate does not come back after its visibility expires
        h.clock.advance(DEFAULTS.lease_ttl_s * 2 + 1)
        remaining = {candidate.job_id for candidate in await drain(port)}
        assert remaining == {JOB_2, JOB_3}, remaining
        assert port.tags() == {} and port.stats()["flows"] == 0
    asyncio.run(run())


# ==========================================================================
# the index is an index (DUR-OUTBOX)
# ==========================================================================
def test_q1_index__a_stale_candidate_never_executes():
    """DUR-OUTBOX, the invariant the whole track exists for: a candidate is a hint. A
    job cancelled after its candidate was handed out is refused by `JobStore.claim`, so
    a stale candidate costs one wasted claim and nothing else."""
    async def run():
        from dataclasses import replace

        from infrx.contracts.conformance.jobs import _admit, _prepare
        h = harness()
        jobs = h.extra["jobs"]
        request, admission = await _admit(replace(h, port=jobs))
        await _prepare(jobs, admission.request_id)
        dispatch = event(h, org_id=request.org_id, job_id=request.request_id)
        assert await h.port.enqueue(dispatch)
        candidate = await h.port.claim_candidate("worker-a")
        assert candidate is not None and candidate.job_id == request.request_id
        # the customer cancels while the candidate is in flight
        await jobs.cancel(request.org_id, admission.job_handle)
        with pytest.raises(errors.DomainError) as caught:
            await jobs.claim(request.request_id, "worker-a")
        assert caught.value.code in ("already_terminal", "not_claimable"), caught.value.code
        # The *same* dispatch event, delivered again after the cancellation removed it,
        # is indexed again: the index is not the authority on cancellation, and
        # remembering the removal would make it one. It still cannot execute.
        await h.port.remove(request.request_id)
        assert await h.port.enqueue(dispatch) is True
        assert (await h.port.claim_candidate("worker-b")).event_id == dispatch.event_id
        with pytest.raises(errors.DomainError):
            await jobs.claim(request.request_id, "worker-b")
    asyncio.run(run())


# ==========================================================================
# rebuild
# ==========================================================================
def test_q1_rebuild__clears_in_flight_and_acknowledged_and_keeps_no_stale_tenant():
    """The semantics this adapter pins (Q1 evidence records them): pending becomes
    exactly the snapshot, in-flight entries are dropped (their holder's right to
    execute came from `JobStore.claim`, which fences a second attempt), acknowledged
    ids are cleared (keeping them would drop a requeued job PostgreSQL still reports as
    queued), and fairness state restarts with only the tenants in the snapshot."""
    async def run():
        h = harness()
        port = h.port
        acked = event(h, org_id=ORG_A)
        gone = event(h, org_id=ORG_C)
        keep = event(h, org_id=ORG_B)
        for candidate in (acked, gone, keep):
            assert await port.enqueue(candidate)
        await port.acknowledge(await port.claim_candidate("worker-a"))       # acked
        assert await port.claim_candidate("worker-b") is not None            # gone: in flight
        assert await port.rebuild((keep, acked)) == 2
        assert set(port.tags()) == {(INFER.value, ORG_B), (INFER.value, ORG_A)}, \
            "a tenant absent from the snapshot kept its fairness state"
        assert port.stats()["inflight"] == 0 and port.stats()["acknowledged"] == 0
        # the acknowledged candidate is indexable again, because PostgreSQL says it is
        # queued; the pre-rebuild in-flight one is simply gone
        h.clock.advance(DEFAULTS.lease_ttl_s * 2 + 1)
        order = [candidate.event_id for candidate in await drain(port)]
        assert sorted(order) == sorted([keep.event_id, acked.event_id]), order
        # a duplicate inside the snapshot indexes once
        assert await port.rebuild((keep, keep, acked)) == 2
    asyncio.run(run())


def test_q1_rebuild__recovery_is_not_bounded_by_the_index_caps():
    """A rebuild is recovery from PostgreSQL truth, so it takes the whole snapshot: a
    cap refusal there would turn a brief loss of throughput into a permanent one."""
    async def run():
        h = harness(max_items=1)
        snapshot = tuple(event(h, org_id=ORG_A) for _ in range(4))
        assert await h.port.rebuild(snapshot) == 4
        assert len(await drain(h.port)) == 4
    asyncio.run(run())


# ==========================================================================
# observability
# ==========================================================================
def test_q1_stats__depth_and_waiting_age_are_reported_without_claiming_capacity():
    """The handoff's rule: publish queue age and depth without pretending index depth
    is admission capacity. Depth counts what this index holds, per kind; the waiting
    age only counts candidates that are actually available."""
    async def run():
        h = harness()
        port = h.port
        assert await port.enqueue(event(h, org_id=ORG_A, kind=PREPARE))
        assert await port.enqueue(event(h, org_id=ORG_A, available_in_s=60))
        h.clock.advance(30)
        stats = port.stats()
        assert stats["depth_by_kind"] == {PREPARE.value: 1, INFER.value: 1}
        assert stats["pending"] == 2 and stats["inflight"] == 0
        assert stats["oldest_wait_s"] == 30.0, stats        # the future one is not waiting
        assert port.depth() == 2
        claimed = await port.claim_candidate("prep-a")
        assert claimed is not None and claimed.kind is PREPARE
        assert port.stats()["inflight"] == 1
        assert port.stats()["oldest_wait_s"] == 0.0         # nothing available is waiting
        assert set(DISPATCH_KINDS) == {PREPARE, INFER}      # the index carries these only
    asyncio.run(run())


def test_q1_stats__a_candidate_is_not_offered_before_it_is_available():
    """`available_at` is honoured: a retry scheduled for later is not dispatched early,
    and it does not hide the tenant's other work either (work conserving)."""
    async def run():
        h = harness()
        port = h.port
        later = event(h, org_id=ORG_A, available_in_s=60, attempt=1)
        now = event(h, org_id=ORG_A)
        assert await port.enqueue(later) and await port.enqueue(now)
        first = await port.claim_candidate("worker-a")
        assert first is not None and first.event_id == now.event_id
        await port.acknowledge(first)
        assert await port.claim_candidate("worker-a") is None
        h.clock.advance(60)
        assert (await port.claim_candidate("worker-a")).event_id == later.event_id
    asyncio.run(run())


def test_q1_drill__losing_the_index_mid_flight_loses_no_job_and_executes_none_twice():
    """The failure drill for the evidence report: the index is destroyed while
    candidates are in flight, a new one is rebuilt from the JobStore's queued jobs, and
    the worker holding a pre-loss candidate comes back.

    Durable state is untouched throughout (the jobs stay `queued`), every job is
    claimable exactly once afterwards, and the worker with the stale candidate is
    refused by `JobStore.claim` rather than being allowed a second generation.
    """
    async def run():
        from dataclasses import replace

        from infrx.contracts.conformance.jobs import _admit, _prepare
        h = harness()
        jobs = h.extra["jobs"]
        inner = replace(h, port=jobs)
        admitted = []
        for n in range(4):
            request, admission = await _admit(inner, key=f"idem-{n}")
            await _prepare(jobs, admission.request_id)
            admitted.append((request, admission))
            assert await h.port.enqueue(event(h, org_id=request.org_id,
                                              job_id=request.request_id))
        stale = await h.port.claim_candidate("worker-lost")
        assert stale is not None
        before = [(await jobs.get_owned(request.org_id, admission.job_handle))[0].state.value
                  for request, admission in admitted]
        assert before == ["queued"] * 4, before

        # the index is gone: a fresh adapter, rebuilt from PostgreSQL's queued jobs
        after_loss = harness()
        snapshot = tuple(event(after_loss, org_id=request.org_id, job_id=request.request_id)
                         for request, _ in admitted)
        assert await after_loss.port.rebuild(snapshot) == 4
        claimed = []
        for candidate in await drain(after_loss.port):
            lease = await jobs.claim(candidate.job_id, "worker-new")
            assert lease.generation == 1, "a rebuilt candidate executed twice"
            claimed.append(candidate.job_id)
        assert sorted(claimed) == sorted(request.request_id for request, _ in admitted)
        # the worker holding the pre-loss candidate comes back and is refused
        with pytest.raises(errors.DomainError) as caught:
            await jobs.claim(stale.job_id, "worker-lost")
        assert caught.value.code == "not_claimable", caught.value.code
        after = [(await jobs.get_owned(request.org_id, admission.job_handle))[0].state.value
                 for request, admission in admitted]
        assert after == ["running"] * 4, after
    asyncio.run(run())


def test_q1_support__the_harness_supplies_the_hooks_the_suite_documents():
    """The factory is part of the deliverable: it must hand the cases the shared fake
    JobStore and the wallet hooks, or the suite would be silently skipping."""
    h = harness()
    assert "jobs" in h.extra and "grant" in h.extra and h.failures is not None
    assert isinstance(h.port, support.MemoryScheduler), "the suite ran against the fake"
