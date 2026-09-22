#!/usr/bin/env python3
"""Q2: the Valkey adapter against real Valkey.

The same exported scheduler conformance cases the fake and the memory adapter pass
(`run_scheduler_conformance`), the fourteen points the Q1 evidence says this adapter must
reproduce - each with its own case, because the differential run proves *agreement* and a
named case is what a mutant has to be killed by - plus the three things only a real server
can be asked: that the scripts are atomic under a connection killed mid-call, that the
caps are enforced inside the script rather than in one process's head, and that two
namespaces in one server cannot see each other.

A missing Docker, client or server is reported as a skip naming the reason, never a pass.

    uv run --frozen pytest -q tests/q/test_valkey_scheduler.py
    INFRX_Q2_DIFFERENTIAL=all uv run --frozen pytest -q tests/q/test_valkey_scheduler.py
"""
from __future__ import annotations

import asyncio
import inspect
import os

import pytest
from infrx.contracts import errors, ports
from infrx.contracts.codec import compact_bytes
from infrx.contracts.conformance import (MissingHook, run_cases, run_scheduler_conformance,
                                         scheduler_cases)
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import DISPATCH_KINDS, OutboxKind
from infrx.scheduling.valkey import _CLAIM, ValkeyScheduler

from . import differential, vkharness
from .support import JOB_1, JOB_2, ORG_A, ORG_B, ORG_C, drain, event

_reason = vkharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local Valkey unavailable: {_reason}")

PREPARE, INFER = OutboxKind.prepare_dispatch, OutboxKind.inference_dispatch
CASES = scheduler_cases()
#: The full differential run is 40 seeds x 2,500 operations (~100 s); the default suite
#: runs a four-seed slice, and a divergence is a failed suite either way.
FULL_DIFFERENTIAL = os.environ.get("INFRX_Q2_DIFFERENTIAL", "").lower() in ("all", "1", "true")


def harness(**kw):
    return vkharness.harness(**kw)


async def _unfiltered(port, count):
    """Claim `count` times with no kind filter, acknowledging as we go."""
    out = []
    for _ in range(count):
        candidate = await port.claim_candidate("worker-any")
        if candidate is None:
            return out
        out.append(candidate)
        await port.acknowledge(candidate)
    return out


def run(coro_fn):
    """Run one async body and close the adapter's connection afterwards."""
    clients: list = []

    def tracked(**kw):
        made = vkharness.harness(**kw)
        clients.append(made.port.client)
        return made

    async def main():
        try:
            await coro_fn(tracked)
        finally:
            for client in clients:
                await client.aclose()
    asyncio.run(main())


# ==========================================================================
# the shared contract, against the real adapter on a real server
# ==========================================================================
@pytest.mark.parametrize("case", CASES, ids=[case.__name__ for case in CASES])
def test_q2_contract__the_real_adapter_passes_the_exported_case(case):
    """F-CONTRACT: the Valkey adapter passes the same case the fake and the memory
    adapter do, driven by the coordinator's harness and the shared fake JobStore."""
    try:
        asyncio.run(case(harness))
    except MissingHook as missing:      # pragma: no cover - the factory supplies them all
        pytest.skip(f"{case.__name__} needs the optional hook {missing.hook!r}")


def test_q2_contract__the_whole_suite_runs_and_skips_nothing():
    """R32: a case skipped for a missing hook is a skip, never a pass."""
    skipped: list[MissingHook] = []
    ran = run_cases(CASES, harness, skipped=skipped)
    print(f"\nscheduler conformance (Valkey): {ran} ran, {len(skipped)} skipped "
          f"{[(s.case, s.hook) for s in skipped]}")
    assert skipped == []
    assert ran == len(CASES)
    assert run_scheduler_conformance(harness) == len(CASES)


def test_q2_contract__the_adapter_satisfies_the_scheduler_protocol():
    port = harness().port
    assert isinstance(port, ports.Scheduler)
    for name in ports.Scheduler.__protocol_attrs__:
        assert inspect.iscoroutinefunction(getattr(port, name)), f"{name} is not async"


# ==========================================================================
# the differential oracle (points 1-14, as agreement)
# ==========================================================================
def test_q2_differential__the_two_adapters_agree_on_every_operation():
    """The memory adapter is the specification; this asserts that no operation stream
    distinguishes the two. Every step compares the returned value (or the typed refusal)
    and then `tags()`, `virtual_times()`, `kind_tags()`, `top_virtual_time()` and
    `stats()` - so all fourteen points are covered by agreement, and the cases below
    exist so a single-edit defect has a *named* case to be killed by."""
    seeds = differential.SEEDS if FULL_DIFFERENTIAL else (1, 2, 3, 4)
    steps = differential.STEPS if FULL_DIFFERENTIAL else 250
    counters: dict = {}
    import collections
    counters = collections.Counter()
    compared = asyncio.run(differential.run_seeds(seeds, steps, counters))
    print(f"\ndifferential: {len(seeds)} seeds x {steps} operations = {compared} "
          f"compared states; outcomes {dict(sorted(counters.items()))}")
    assert compared == len(seeds) * steps
    # the stream must actually have exercised the paths it claims to
    assert counters["claim"] > 0 and counters["rebuild"] > 0
    assert counters["claim_unknown:invalid_request"] > 0
    assert counters["poison:internal_error"] > 0 and counters["poison:invalid_request"] > 0


def test_q2_differential__the_harness_notices_a_divergence():
    """R32 honesty: a differential run that could not fail would prove nothing. The same
    stream against an adapter whose Lua formats its scores the way Lua 5.1 formats numbers
    by default (`%.14g`, three digits short of a double) must fail, and fail on `tags`."""
    broken = _CLAIM.replace("string.format('%.17g', x)", "string.format('%.14g', x)")
    assert broken != _CLAIM

    class Rounded(ValkeyScheduler):
        def __init__(self, client, *a, **kw):
            super().__init__(client, *a, **kw)
            self._claim = client.register_script(broken)

    original, differential.vkharness.ValkeyScheduler = (
        differential.vkharness.ValkeyScheduler, Rounded)
    try:
        with pytest.raises(AssertionError) as caught:
            asyncio.run(differential.run_stream(1, 60))
    finally:
        differential.vkharness.ValkeyScheduler = original
    assert "tags diverged" in str(caught.value), caught.value


# ==========================================================================
# point 4, 2, 1: level-2 arithmetic, FIFO, the arrival sequence
# ==========================================================================
def test_q2_fair__tenants_alternate_and_ties_break_on_arrival():
    """Point 1/2/4: one flow per (kind, org), FIFO inside it by an integer arrival
    sequence shared across kinds, and two flows at the same tag separated by that
    sequence."""
    async def body(harness_):
        h = harness_()
        port = h.port
        first = [event(h, org_id=ORG_A) for _ in range(3)]
        second = [event(h, org_id=ORG_B) for _ in range(3)]
        for candidate in first + second:
            assert await port.enqueue(candidate)
        order = await drain(port, "worker-a")
        assert [c.org_id for c in order] == [ORG_A, ORG_B] * 3
        # FIFO inside each flow, by arrival, not by anything Valkey happens to store
        assert [c.event_id for c in order if c.org_id == ORG_A] == \
               [c.event_id for c in first]
    run(body)


def test_q2_fair__the_virtual_finish_time_advances_at_dispatch_only():
    """Point 4: `start = max(tag, V[kind])`, `V[kind] = start`, `tag = start + cost /
    weight`, in that float order, and nothing moves at enqueue."""
    async def body(harness_):
        h = harness_()
        port = h.port
        for _ in range(3):
            assert await port.enqueue(event(h, org_id=ORG_A))
        assert await port.tags() == {(INFER.value, ORG_A): 0.0}
        assert await port.virtual_times() == {PREPARE.value: 0.0, INFER.value: 0.0}
        first = await port.claim_candidate("worker-a", kind=INFER)
        assert first is not None
        assert await port.tags() == {(INFER.value, ORG_A): 1.0}
        assert (await port.virtual_times())[INFER.value] == 0.0     # the clamped start
        await port.acknowledge(first)
        await port.acknowledge(await port.claim_candidate("worker-a", kind=INFER))
        assert await port.tags() == {(INFER.value, ORG_A): 2.0}
        assert (await port.virtual_times())[INFER.value] == 1.0
    run(body)


def test_q2_fair__weight_buys_a_proportional_share():
    """Point 4: the tag advances by `cost / weight`, so a tenant weighted 3 is dispatched
    three times as often - a Lua that dropped the division would alternate."""
    async def body(harness_):
        h = harness_(weights={ORG_A: 3.0}, max_items=1000)
        port = h.port
        for _ in range(12):
            assert await port.enqueue(event(h, org_id=ORG_A))
        for _ in range(12):
            assert await port.enqueue(event(h, org_id=ORG_B))
        order = [c.org_id for c in await _unfiltered(port, 8)]
        assert order == [ORG_A, ORG_B, ORG_A, ORG_A, ORG_A, ORG_B, ORG_A, ORG_A], order
        # six advances of 1/3 land on 1.9999999999999998, not 2.0, and that is the point:
        # the Lua divides and adds in the memory adapter's order, so it drifts *the same
        # way*. An exact 2.0 here would mean the adapter had "corrected" the arithmetic.
        assert (await port.tags())[(INFER.value, ORG_A)] == 1.9999999999999998
        assert (await port.tags())[(INFER.value, ORG_B)] == 2.0
    run(body)


def test_q2_fair__a_candidate_that_waited_catches_up_once_and_cannot_hoard():
    """Point 4: the clamp. A flow whose candidates were not available yet gets one slot
    of catch-up and is then pulled up to its pool's virtual time; without the clamp it
    would claim every slot it missed, and the virtual time must be the *clamped* start
    or it walks backwards."""
    async def body(harness_):
        h = harness_(max_items=1000)
        port = h.port
        for _ in range(6):
            assert await port.enqueue(event(h, org_id=ORG_A))
        for _ in range(3):
            assert await port.enqueue(event(h, org_id=ORG_B, available_in_s=60))
        early = [c.org_id for c in await _unfiltered(port, 4)]
        assert early == [ORG_A] * 4, early
        assert (await port.virtual_times())[INFER.value] == 3.0
        h.clock.advance(60)
        late = [c.org_id for c in await _unfiltered(port, 4)]
        assert late == [ORG_B, ORG_A, ORG_B, ORG_A], late
        # the newcomer was clamped up to 3.0 once and then advanced in step, so the pool's
        # virtual time is the clamped start of the last dispatch, not the tag behind it
        assert (await port.virtual_times())[INFER.value] == 5.0
    run(body)


def test_q2_fair__a_newcomer_arrives_at_its_own_kinds_virtual_time():
    """Point 3: an arriving flow's tag is **its own kind's** virtual time - not zero (it
    would jump the queue) and not the highest of any kind (a busy preparation pool would
    bury a new inference tenant)."""
    async def body(harness_):
        h = harness_(max_items=1000)
        port = h.port
        for _ in range(50):
            assert await port.enqueue(event(h, org_id=ORG_C, kind=PREPARE))
        for _ in range(6):
            assert await port.enqueue(event(h, org_id=ORG_A, kind=INFER))
        for _ in range(50):
            await port.acknowledge(await port.claim_candidate("prep-a", kind=PREPARE))
        for _ in range(3):
            await port.acknowledge(await port.claim_candidate("worker-a", kind=INFER))
        assert (await port.virtual_times())[PREPARE.value] == 49.0
        assert (await port.virtual_times())[INFER.value] == 2.0
        for _ in range(6):
            assert await port.enqueue(event(h, org_id=ORG_B, kind=INFER))
        assert (await port.tags())[(INFER.value, ORG_B)] == 2.0, await port.tags()
        order = [c.org_id for c in await drain(port, "worker-a", kind=INFER)]
        assert order[:4] == [ORG_B, ORG_A, ORG_B, ORG_A], order
    run(body)


def test_q2_fair__the_virtual_time_of_a_kind_survives_an_empty_index():
    """Point 3: `V[kind]` is never reset when a flow or a kind empties - only `rebuild`
    resets it. Resetting it would hand the next arrival a credit for the whole epoch."""
    async def body(harness_):
        h = harness_()
        port = h.port
        for _ in range(3):
            assert await port.enqueue(event(h, org_id=ORG_A))
        await drain(port, "worker-a")
        assert await port.tags() == {}, "an empty index kept a flow"
        assert (await port.virtual_times())[INFER.value] == 2.0
        assert await port.enqueue(event(h, org_id=ORG_B))
        assert (await port.tags())[(INFER.value, ORG_B)] == 2.0
    run(body)


# ==========================================================================
# point 8: the estimator is validated before any write
# ==========================================================================
class _Estimator:
    """A cost estimator that can be switched to each of its failure modes."""

    def __init__(self) -> None:
        self.answer: float | None = 1.0
        self.raises: Exception | None = None

    def __call__(self, _event):
        if self.raises is not None:
            raise self.raises
        return self.answer


def test_q2_fair__a_bad_service_cost_is_a_typed_error_that_moves_nothing():
    """Point 8: a `0`, a negative, a NaN, an infinity or an estimator that raises is
    `internal_error` with the index byte-identical afterwards; a `DomainError` the
    estimator raises itself passes through unchanged. The adapter prices the candidate
    *between* the selecting script and the committing one for exactly this reason."""
    async def body(harness_):
        estimator = _Estimator()
        h = harness_(cost=estimator)
        port = h.port
        assert await port.enqueue(event(h, org_id=ORG_A))
        assert await port.enqueue(event(h, org_id=ORG_B, kind=PREPARE))
        before = await port.snapshot()
        for bad in (0.0, -1.0, float("nan"), float("inf")):
            estimator.answer = bad
            for kind in (None, INFER, PREPARE):
                with pytest.raises(errors.InternalError) as caught:
                    await port.claim_candidate("worker-a", kind=kind)
                assert caught.value.code == "internal_error"
                assert await port.snapshot() == before, f"{bad!r} moved the index"
        estimator.answer, estimator.raises = 1.0, RuntimeError("estimator bug")
        with pytest.raises(errors.InternalError):
            await port.claim_candidate("worker-a")
        assert await port.snapshot() == before
        estimator.raises = errors.InvalidRequest("the estimator refused")
        with pytest.raises(errors.InvalidRequest) as passed:
            await port.claim_candidate("worker-a")
        assert passed.value.code == "invalid_request", "a DomainError was re-wrapped"
        assert await port.snapshot() == before
        estimator.raises = None                   # and the index still works afterwards
        assert len(await drain(port, "worker-a")) == 2
    run(body)


def test_q2_config__a_weight_that_would_break_dispatch_is_refused_where_it_is_set():
    """A non-positive or non-finite weight is a division by zero (or a NaN that poisons
    every comparison) inside dispatch - in Lua, where the index is already moving. It is
    refused in the constructor instead."""
    for bad in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            ValkeyScheduler(vkharness.client(), lambda: None, weights={ORG_A: bad})


# ==========================================================================
# points 9, 2: visibility
# ==========================================================================
def test_q2_kind__visibility_expires_at_the_ttl_and_on_its_pools_lease():
    """Point 9: 30 s preparation, 120 s inference, measured from the claim, back at
    exactly `claimed_at + TTL` (`>=`), and evaluated lazily at the start of the next
    claim across **both** kinds - so `stats()["inflight"]` still counts an entry whose
    visibility has expired until that claim happens."""
    async def body(harness_):
        h = harness_()
        port = h.port
        assert await port.enqueue(event(h, org_id=ORG_A, kind=PREPARE))
        assert await port.enqueue(event(h, org_id=ORG_B, kind=INFER))
        prepared = await port.claim_candidate("prep-a", kind=PREPARE)
        inference = await port.claim_candidate("worker-a", kind=INFER)
        assert prepared is not None and inference is not None
        h.clock.advance(DEFAULTS.preparation_lease_ttl_s - 0.000_001)
        assert await port.claim_candidate("prep-b", kind=PREPARE) is None
        h.clock.advance(0.000_001)                # exactly the TTL
        assert (await port.stats())["inflight"] == 2, "expiry was not lazy"
        again = await port.claim_candidate("prep-b", kind=PREPARE)
        assert again is not None and again.event_id == prepared.event_id
        # the inference candidate is on the longer lease and is still held
        assert await port.claim_candidate("worker-b", kind=INFER) is None
        h.clock.advance(DEFAULTS.lease_ttl_s - DEFAULTS.preparation_lease_ttl_s)
        back = await port.claim_candidate("worker-b", kind=INFER)
        assert back is not None and back.event_id == inference.event_id
    run(body)


def test_q2_kind__returned_candidates_keep_their_arrival_order():
    """Point 2: a candidate whose visibility timed out re-enters its flow at its
    **original** sequence, so FIFO inside a tenant survives a lost worker."""
    async def body(harness_):
        h = harness_()
        port = h.port
        first, second = event(h, org_id=ORG_A), event(h, org_id=ORG_A)
        assert await port.enqueue(first) and await port.enqueue(second)
        lost = await port.claim_candidate("worker-lost", kind=INFER)
        assert lost is not None and lost.event_id == first.event_id
        h.clock.advance(DEFAULTS.lease_ttl_s)
        order = [c.event_id for c in await drain(port, "worker-a")]
        assert order == [first.event_id, second.event_id], order
    run(body)


def test_q2_stats__a_candidate_is_not_offered_before_it_is_available():
    """Point 2: a not-yet-available older candidate does not block a newer available one
    in the same flow, and `available_at` bounds dispatch to the microsecond."""
    async def body(harness_):
        h = harness_()
        port = h.port
        later = event(h, org_id=ORG_A, available_in_s=30)
        now = event(h, org_id=ORG_A)
        assert await port.enqueue(later) and await port.enqueue(now)
        first = await port.claim_candidate("worker-a")
        assert first is not None and first.event_id == now.event_id
        await port.acknowledge(first)
        assert await port.claim_candidate("worker-a") is None
        h.clock.advance(29.999_999)
        assert await port.claim_candidate("worker-a") is None
        h.clock.advance(0.000_001)
        arrived = await port.claim_candidate("worker-a")
        assert arrived is not None and arrived.event_id == later.event_id
    run(body)


# ==========================================================================
# points 5, 6: R60, the two levels
# ==========================================================================
def test_q2_none__a_peer_in_another_kind_is_not_starved_by_a_noisy_backlog():
    """Point 5 (R60 level 1): the kinds are flows in their own right, so an unfiltered
    worker cannot serve one kind to exhaustion however deep the backlog and whatever
    weight the noisy tenant carries."""
    async def body(harness_):
        for depth, weights in ((5, None), (30, None), (400, None), (400, {ORG_A: 2.0})):
            h = harness_(max_items=1000, weights=weights)
            port = h.port
            for _ in range(depth):
                assert await port.enqueue(event(h, org_id=ORG_A, kind=INFER))
            for _ in range(3):
                assert await port.enqueue(event(h, org_id=ORG_B, kind=PREPARE))
            order = [c.kind for c in await _unfiltered(port, 8)]
            slots = [index for index, k in enumerate(order) if k is PREPARE]
            assert slots == [1, 3, 5], (depth, weights, slots)
    run(body)


def test_q2_none__the_kind_tag_advances_by_the_service_the_kind_received():
    """Point 5: level 1 charges the kind `start + cost` with **no weight**, so an
    unfiltered worker shares the machine by service time rather than by request count."""
    async def body(harness_):
        h = harness_(max_items=1000, weights={ORG_B: 4.0},
                     cost=lambda e: 11.0 if e.is_preparation else 1.0)
        port = h.port
        for _ in range(24):
            assert await port.enqueue(event(h, org_id=ORG_A, kind=INFER))
        for _ in range(4):
            assert await port.enqueue(event(h, org_id=ORG_B, kind=PREPARE))
        pattern = "".join("P" if c.is_preparation else "I"
                          for c in await _unfiltered(port, 26))
        assert pattern == "IPIIIIIIIIIIIPIIIIIIIIIIIP", pattern
        # the weight belongs to level 2 only: ORG_B is weighted 4 and the kind is still
        # charged the raw 11 s it received
        assert await port.kind_tags() == {PREPARE.value: 33.0, INFER.value: 23.0}
    run(body)


def test_q2_none__the_kind_tie_breaks_on_arrival_order():
    """Point 5: equal kind tags are separated by the arrival sequence of the candidate
    that kind would hand out - not by the order the kinds sit in a list, or their names."""
    async def body(harness_):
        for first, second in ((INFER, PREPARE), (PREPARE, INFER)):
            h = harness_()
            assert await h.port.enqueue(event(h, org_id=ORG_A, kind=first))
            assert await h.port.enqueue(event(h, org_id=ORG_B, kind=second))
            assert await h.port.kind_tags() == {INFER.value: 0.0, PREPARE.value: 0.0}
            order = [c.kind for c in await _unfiltered(h.port, 2)]
            assert order == [first, second], (first, order)
    run(body)


def test_q2_none__the_kind_is_ranked_by_the_candidate_it_would_actually_hand_out():
    """Point 5, as corrected on 2026-09-21: the level-1 tie-break is the sequence of the
    candidate the kind's own level-2 rule would hand out, **not** the oldest candidate
    the kind happens to hold. They differ as soon as a kind has two tenants and the one
    holding the oldest candidate has already been served."""
    async def body(harness_):
        h = harness_()
        port = h.port
        first = event(h, org_id=ORG_A, kind=PREPARE)          # seq 1
        second = event(h, org_id=ORG_A, kind=PREPARE)         # seq 2
        assert await port.enqueue(first) and await port.enqueue(second)
        served = await port.claim_candidate("prep-a", kind=PREPARE)
        assert served is not None and served.event_id == first.event_id
        await port.acknowledge(served)
        assert (await port.tags())[(PREPARE.value, ORG_A)] == 1.0
        assert await port.kind_tags() == {PREPARE.value: 0.0, INFER.value: 0.0}
        inference = event(h, org_id=ORG_C, kind=INFER)        # seq 3
        newcomer = event(h, org_id=ORG_B, kind=PREPARE)       # seq 4, arrives at tag 0
        assert await port.enqueue(inference) and await port.enqueue(newcomer)
        candidate = await port.claim_candidate("worker-any")
        assert candidate is not None and candidate.event_id == inference.event_id, \
            "level 1 ranked preparation by a candidate it would not have handed out"
    run(body)


def test_q2_none__a_kind_that_waited_catches_up_once_and_cannot_hoard():
    """Point 5: level 1 obeys the same clamp as level 2 (`top_start = max(kind tag,
    V_top)`, `V_top = top_start`), so a kind that was quiet gets one slot of catch-up and
    the top-level virtual time never walks backwards."""
    async def body(harness_):
        h = harness_()
        port = h.port
        for _ in range(6):
            assert await port.enqueue(event(h, org_id=ORG_A, kind=INFER))
        for _ in range(3):
            assert await port.enqueue(event(h, org_id=ORG_B, kind=PREPARE,
                                            available_in_s=60))
        early = [c.kind for c in await _unfiltered(port, 4)]
        assert early == [INFER] * 4, early
        assert await port.top_virtual_time() == 3.0
        h.clock.advance(60)
        first_late = await port.claim_candidate("worker-any")
        assert first_late is not None and first_late.kind is PREPARE
        assert await port.top_virtual_time() == 3.0, "the clamped start walked backwards"
        await port.acknowledge(first_late)
        late = [first_late.kind] + [c.kind for c in await _unfiltered(port, 3)]
        assert late == [PREPARE, INFER, PREPARE, INFER], late
    run(body)


def test_q2_none__a_filtered_worker_never_moves_the_kind_state():
    """Point 6: a kind-filtered claim neither reads nor writes level-1 state, so a
    preparation peer never waits on a pool it is not competing in."""
    async def body(harness_):
        h = harness_(max_items=1000)
        port = h.port
        for _ in range(12):
            assert await port.enqueue(event(h, org_id=ORG_A, kind=INFER))
        for _ in range(6):
            assert await port.enqueue(event(h, org_id=ORG_B, kind=PREPARE))
        before = await port.kind_tags()
        filtered = await port.claim_candidate("worker-inf", kind=INFER)
        assert filtered is not None and filtered.kind is INFER
        await port.acknowledge(filtered)
        assert await port.kind_tags() == before, "a filtered claim moved level-1 state"
        assert await port.top_virtual_time() == 0.0
        unfiltered = []
        for _ in range(6):
            candidate = await port.claim_candidate("worker-any")
            assert candidate is not None
            unfiltered.append(candidate.kind)
            await port.acknowledge(candidate)
            more = await port.claim_candidate("worker-inf", kind=INFER)
            assert more is not None and more.kind is INFER
            await port.acknowledge(more)
        assert unfiltered == [INFER, PREPARE] * 3, unfiltered
    run(body)


def test_q2_kind__preparation_and_inference_are_separately_fair():
    """R52 / point 3: one flow per (kind, org) with its own tag and its own virtual time,
    so a tenant's transcodes never push it back in the GPU line."""
    async def body(harness_):
        h = harness_(max_items=1000)
        port = h.port
        for _ in range(6):
            assert await port.enqueue(event(h, org_id=ORG_A, kind=PREPARE))
        for _ in range(6):
            assert await port.enqueue(event(h, org_id=ORG_A, kind=INFER))
        assert set(await port.tags()) == {(PREPARE.value, ORG_A), (INFER.value, ORG_A)}
        for _ in range(4):
            await port.acknowledge(await port.claim_candidate("prep-a", kind=PREPARE))
        assert (await port.tags())[(PREPARE.value, ORG_A)] == 4.0
        assert (await port.tags())[(INFER.value, ORG_A)] == 0.0
        assert await port.virtual_times() == {PREPARE.value: 3.0, INFER.value: 0.0}
    run(body)


def test_q2_kind__an_unknown_kind_is_a_typed_refusal():
    """Point 13 (R55): an unknown kind is `invalid_request`, checked before anything
    else - answering `None` would report a caller bug as an empty index."""
    async def body(harness_):
        h = harness_()
        assert await h.port.enqueue(event(h, org_id=ORG_A))
        for unknown in (OutboxKind.usage_projection, OutboxKind.callback_delivery):
            with pytest.raises(errors.InvalidRequest) as caught:
                await h.port.claim_candidate("worker-a", kind=unknown)
            assert caught.value.code == "invalid_request"
        assert (await h.port.stats())["items"] == 1, "a refusal touched the index"
        assert await h.port.claim_candidate("worker-a") is not None   # None means anything
    run(body)


# ==========================================================================
# point 10, 4: bounded memory, enforced inside the script
# ==========================================================================
def test_q2_caps__a_full_index_refuses_with_a_typed_retryable_error():
    """Point 10: duplicate -> item cap -> byte cap, both caps before any write, refused
    with `capacity_exhausted` and `retry_after_s = 1`."""
    async def body(harness_):
        h = harness_(max_items=2)
        port = h.port
        assert await port.enqueue(event(h, org_id=ORG_A))
        assert await port.enqueue(event(h, org_id=ORG_A))
        full = await port.snapshot()
        with pytest.raises(errors.CapacityExhausted) as caught:
            await port.enqueue(event(h, org_id=ORG_B))
        assert errors.http_status(caught.value.code) == 429
        assert caught.value.retry_after_s == 1
        assert await port.snapshot() == full, "a refused enqueue wrote something"
        assert set(await port.tags()) == {(INFER.value, ORG_A)}
        await port.acknowledge(await port.claim_candidate("worker-a"))
        assert await port.enqueue(event(h, org_id=ORG_B))
        assert (await port.stats())["items"] == 2
    run(body)


def test_q2_caps__queued_bytes_are_counted_per_candidate_and_returned():
    """Point 10: the byte cap binds independently of the item count, the charge is the
    candidate's compact bytes, and what an enqueue charges an acknowledgment and a
    cancellation give back."""
    async def body(harness_):
        h = harness_()
        port = h.port
        one = event(h, org_id=ORG_A)
        assert await port.enqueue(one)
        charged = (await port.stats())["bytes"]
        assert charged == len(compact_bytes(one))
        assert await port.enqueue(event(h, org_id=ORG_B, job_id=JOB_2))
        assert (await port.stats())["bytes"] > charged
        await port.remove(JOB_2)
        assert (await port.stats())["bytes"] == charged
        await port.acknowledge(await port.claim_candidate("worker-a"))
        assert (await port.stats())["bytes"] == 0
        tiny = harness_(max_bytes=charged)
        assert await tiny.port.enqueue(event(tiny, org_id=ORG_A))
        with pytest.raises(errors.CapacityExhausted):
            await tiny.port.enqueue(event(tiny, org_id=ORG_A))
    run(body)


def test_q2_caps__the_cap_is_enforced_inside_the_script_over_the_shared_index():
    """Point 4 of the Q2 brief: the caps are enforced **in the script**, against the
    server's own count and byte total. Two adapter instances on one namespace - two
    gateway processes - therefore share one bound; a cap checked in process memory would
    let each of them index its own capful."""
    async def body(harness_):
        namespace = vkharness.namespace()
        one = harness_(max_items=2, namespace_=namespace)
        two = harness_(max_items=2, namespace_=namespace)
        # one source of event ids, two adapters: the second process must see the first
        # one's candidates, not a private copy of the count
        candidates = [event(one, org_id=org) for org in (ORG_A, ORG_B, ORG_C, ORG_A)]
        assert await one.port.enqueue(candidates[0])
        assert await two.port.enqueue(candidates[1])
        with pytest.raises(errors.CapacityExhausted):
            await two.port.enqueue(candidates[2])
        with pytest.raises(errors.CapacityExhausted):
            await one.port.enqueue(candidates[3])
        assert (await one.port.stats())["items"] == 2 == (await two.port.stats())["items"]
    run(body)


def test_q2_bounds__the_adapter_keeps_no_per_candidate_state_in_the_process():
    """Point 4 of the Q2 brief, the other half: the adapter's own memory is bounded by
    its configuration, not by the index. Nothing it holds grows with the number of
    candidates - the index lives in Valkey, which is the whole point of the port."""
    async def body(harness_):
        h = harness_(max_items=1000, weights={ORG_A: 2.0})
        port = h.port
        containers = {name: value for name, value in vars(port).items()
                      if isinstance(value, (dict, list, set, tuple))}
        sizes = {name: len(value) for name, value in containers.items()}
        for _ in range(200):
            assert await port.enqueue(event(h, org_id=ORG_A))
        for _ in range(100):
            await port.claim_candidate("worker-a")
        await port.remove(JOB_1)
        assert (await port.stats())["items"] == 200
        after = {name: len(value) for name, value in vars(port).items()
                 if isinstance(value, (dict, list, set, tuple))}
        assert after == sizes, f"the adapter grew with the index: {sizes} -> {after}"
    run(body)


def test_q2_isolation__two_namespaces_do_not_see_each_other():
    """Q2 acceptance: isolated namespaces never corrupt each other's capacity truth. One
    server, two indices (a test beside a pilot, or a drain-and-switch migration)."""
    async def body(harness_):
        left, right = harness_(), harness_()
        assert await left.port.enqueue(event(left, org_id=ORG_A))
        assert (await left.port.stats())["items"] == 1
        assert (await right.port.stats())["items"] == 0
        assert await right.port.claim_candidate("worker-a") is None
        assert await right.port.tags() == {}
        assert await left.port.claim_candidate("worker-a") is not None
    run(body)


# ==========================================================================
# points 11, 12: cancellation and rebuild
# ==========================================================================
def test_q2_cancel__removal_drops_pending_and_in_flight_candidates_and_their_flow():
    """Point 7/11: `remove` drops every candidate of the job, pending or in flight, and
    the flow disappears exactly when its pending **and** in-flight count reaches zero."""
    async def body(harness_):
        h = harness_()
        port = h.port
        pending = event(h, org_id=ORG_A, job_id=JOB_1)
        inflight = event(h, org_id=ORG_A, job_id=JOB_1)
        other = event(h, org_id=ORG_B, job_id=JOB_2)
        for candidate in (inflight, pending, other):
            assert await port.enqueue(candidate)
        claimed = await port.claim_candidate("worker-a")
        assert claimed is not None and claimed.event_id == inflight.event_id
        assert len(await port.tags()) == 2
        await port.remove(JOB_1)
        assert set(await port.tags()) == {(INFER.value, ORG_B)}, await port.tags()
        stats = await port.stats()
        assert (stats["items"], stats["inflight"], stats["flows"]) == (1, 0, 1)
        assert stats["bytes"] == len(compact_bytes(other))
    run(body)


def test_q2_cancel__a_cancelled_candidate_may_be_re_indexed():
    """Point 11: cancellation is **not** remembered. A replayed dispatch event for a
    cancelled job may be re-indexed; `JobStore.claim` refuses it. Remembering it here
    would make the index authoritative about cancellation."""
    async def body(harness_):
        h = harness_()
        port = h.port
        candidate = event(h, org_id=ORG_A, job_id=JOB_1)
        assert await port.enqueue(candidate)
        await port.remove(JOB_1)
        assert (await port.stats())["acknowledged"] == 0
        assert await port.enqueue(candidate) is True, "the index remembered a cancellation"
    run(body)


def test_q2_replay__enqueue_is_replay_safe_across_pending_inflight_and_acknowledged():
    """Point 10's duplicate clause, all three states: the same stable event id indexes
    exactly one candidate whether the first copy is pending, in flight or acknowledged."""
    async def body(harness_):
        h = harness_()
        port = h.port
        candidate = event(h, org_id=ORG_A)
        assert await port.enqueue(candidate) is True
        assert await port.enqueue(candidate) is False
        claimed = await port.claim_candidate("worker-a")
        assert claimed is not None
        assert await port.enqueue(candidate) is False, "a claimed event was re-indexed"
        assert await port.claim_candidate("worker-b") is None
        await port.acknowledge(claimed)
        assert await port.enqueue(candidate) is False, "an acknowledged event came back"
        assert (await port.stats())["items"] == 0
        assert (await port.stats())["acknowledged"] == 1
    run(body)


def test_q2_rebuild__from_a_postgresql_snapshot_with_duplicates():
    """Point 12, deliverable 3: the index is rebuilt from a snapshot of accepted jobs (the
    shape `JobStore` will hand over; the real PostgreSQL source is Q3). `pending` becomes
    the de-duplicated snapshot in snapshot order, the caps are **not** applied, and the
    bytes are the sum of the snapshot's compact bytes."""
    async def body(harness_):
        h = harness_(max_items=3)
        port = h.port
        snapshot = tuple(event(h, org_id=org, kind=kind)
                         for org in (ORG_A, ORG_B) for kind in (INFER, PREPARE))
        with_duplicates = snapshot + snapshot[:2] + (snapshot[1],)
        assert await port.rebuild(with_duplicates) == len(snapshot), \
            "a duplicate in the snapshot was indexed twice"
        stats = await port.stats()
        assert stats["items"] == 4 > 3, "the caps were applied to a recovery"
        assert stats["bytes"] == sum(len(compact_bytes(e)) for e in snapshot)
        assert stats["depth_by_kind"] == {PREPARE.value: 2, INFER.value: 2}
        # snapshot order is the arrival order, so the drain follows it inside each flow
        drained = [c.event_id for c in await drain(port, "worker-a")]
        assert sorted(drained) == sorted(e.event_id for e in snapshot)
        assert await port.rebuild(()) == 0 and (await port.stats())["items"] == 0
    run(body)


def test_q2_rebuild__clears_in_flight_acknowledged_and_both_fairness_levels():
    """Point 12: in-flight entries are dropped, acknowledged ids are cleared, **both**
    fairness levels and the arrival sequence restart, and no tenant absent from the
    snapshot keeps state."""
    async def body(harness_):
        h = harness_()
        port = h.port
        for _ in range(4):
            assert await port.enqueue(event(h, org_id=ORG_A, kind=INFER))
        for _ in range(2):
            assert await port.enqueue(event(h, org_id=ORG_B, kind=PREPARE))
        await port.acknowledge(await port.claim_candidate("worker-any"))
        await port.acknowledge(await port.claim_candidate("worker-any"))
        inflight = await port.claim_candidate("worker-any")
        assert inflight is not None
        assert await port.top_virtual_time() > 0.0
        assert any(tag > 0.0 for tag in (await port.kind_tags()).values())
        fresh = event(h, org_id=ORG_C, kind=INFER)
        assert await port.rebuild((fresh,)) == 1
        assert await port.tags() == {(INFER.value, ORG_C): 0.0}
        assert await port.virtual_times() == {PREPARE.value: 0.0, INFER.value: 0.0}
        assert await port.kind_tags() == {PREPARE.value: 0.0, INFER.value: 0.0}
        assert await port.top_virtual_time() == 0.0
        stats = await port.stats()
        assert (stats["inflight"], stats["acknowledged"], stats["flows"]) == (0, 0, 1)
        # the acknowledged set is cleared, so a requeued job is indexable again
        assert await port.enqueue(inflight) is True
    run(body)


# ==========================================================================
# stats
# ==========================================================================
def test_q2_stats__depth_and_waiting_age_are_reported_without_claiming_capacity():
    """Index depth, bytes and waiting age, per kind, with the waiting age measured over
    **available pending** candidates only - an in-flight candidate is not waiting and a
    candidate that is not available yet has not started waiting."""
    async def body(harness_):
        h = harness_()
        port = h.port
        old = event(h, org_id=ORG_A, kind=INFER)
        future = event(h, org_id=ORG_A, kind=PREPARE, available_in_s=100)
        assert await port.enqueue(old) and await port.enqueue(future)
        assert (await port.stats())["oldest_wait_s"] == 0.0
        h.clock.advance(9.5)
        stats = await port.stats()
        assert stats["oldest_wait_s"] == 9.5
        assert stats["depth_by_kind"] == {INFER.value: 1, PREPARE.value: 1}
        assert (stats["pending"], stats["inflight"]) == (2, 0)
        claimed = await port.claim_candidate("worker-a", kind=INFER)
        assert claimed is not None
        after = await port.stats()
        assert after["oldest_wait_s"] == 0.0, "an in-flight candidate is not waiting"
        assert (after["pending"], after["inflight"]) == (1, 1)
        h.clock.advance(100)
        # the preparation candidate became available at +100 and the clock is at +109.5;
        # the inference candidate is still in flight, so it is still not waiting
        assert (await port.stats())["oldest_wait_s"] == 9.5
        assert (await port.stats())["inflight"] == 1
    run(body)


# ==========================================================================
# crash safety: the scripts are atomic
# ==========================================================================
def _resp(*parts: bytes) -> bytes:
    """One RESP array, so the fault injector owns the bytes on the wire."""
    out = [b"*%d\r\n" % len(parts)]
    for part in parts:
        out.append(b"$%d\r\n%s\r\n" % (len(part), part))
    return b"".join(out)


async def _kill_mid_call(port, script_source: str, args, *, truncate: int | None = None):
    """Send one script on a raw socket and lose the connection before the reply.

    Two deaths, because they prove the two halves of atomicity and each one is
    deterministic rather than a race:

    * the **whole** command, then a close with the reply never read (FIN after the bytes,
      so the server has them): the client learns nothing, and the operation must have been
      applied *completely*;
    * a command **truncated mid-write**, then an abort (RST): the server is left holding an
      incomplete command it can never run, and the operation must not have happened at all.
    """
    command = _resp(b"EVAL", script_source.encode(), str(len(port._keys)).encode(),
                    *[key.encode() for key in port._keys],
                    *[str(arg).encode() for arg in args])
    reader, writer = await asyncio.open_connection("127.0.0.1", vkharness.PORT)
    writer.write(command if truncate is None else command[:truncate])
    await writer.drain()
    if truncate is None:
        writer.close()                            # FIN: the bytes are delivered, the
        await writer.wait_closed()                # reply is never read
    else:
        writer.transport.abort()                  # RST: the half-command is discarded
    del reader


async def _settled(port, expected: int, *, timeout_s: float = 5.0) -> int:
    """The item count once the server has caught up, or after the deadline.

    Valkey gives no ordering between two connections, so "did the killed command run"
    is polled rather than assumed - and a poll that times out returns the count it saw,
    which is what makes the assertion above it a real one.
    """
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        items = (await port.stats())["items"]
        if items == expected or asyncio.get_running_loop().time() > deadline:
            return items
        await asyncio.sleep(0.01)


async def _consistent(port) -> dict:
    """Every accounting invariant the index must satisfy at rest, read back raw.

    A "half-indexed candidate" is precisely a violation of one of these: an entry with no
    payload, a byte total that is not the sum of the entries, an event that is neither
    pending nor in flight (wedged: never re-offered, never released, still charged), one
    that is both, or a flow whose reference count does not match what it holds.
    """
    client = port.client
    ns = port.namespace
    meta = {k.decode(): v.decode() for k, v in (await client.hgetall(f"{ns}:meta")).items()}
    payloads = await client.hgetall(f"{ns}:payload")
    inflight = {m.decode() for m in await client.zrange(f"{ns}:inflight", 0, -1)}
    refs = {k.decode(): int(v) for k, v in (await client.hgetall(f"{ns}:refs")).items()}
    flows = {m.decode() for m in await client.zrange(f"{ns}:tags", 0, -1)}
    charged = int(await client.hget(f"{ns}:counters", "bytes") or 0)
    fields = {event_id: packed.split("\t") for event_id, packed in meta.items()}
    assert set(payloads) == {k.encode() for k in meta}, "an entry lost its payload"
    assert charged == sum(int(f[6]) for f in fields.values()), "the byte total drifted"
    pending: dict[str, set[str]] = {}
    for flow in flows:
        pending[flow] = {m.decode() for m in await client.zrange(f"{ns}:pending:{flow}", 0, -1)}
    queued = {event_id for members in pending.values() for event_id in members}
    assert queued & inflight == set(), "a candidate was pending and in flight at once"
    assert queued | inflight == set(meta), "a candidate was neither pending nor in flight"
    assert flows == set(refs), "a flow lost its tag or its reference count"
    for flow, count in refs.items():
        held = {event_id for event_id, f in fields.items() if f"{f[3]}|{f[4]}" == flow}
        assert count == len(held) > 0, f"{flow} counts {count} of {len(held)}"
    return {"items": len(meta), "bytes": charged, "inflight": len(inflight)}


def test_q2_crash__an_interrupted_script_leaves_no_half_indexed_candidate():
    """Deliverable 3: an operation interrupted between its steps cannot leave a
    half-indexed candidate. The scripts are atomic, so the proof is a client that dies
    mid-call: the command is written on a raw socket and the connection is destroyed
    before the reply is read (and, in half the injections, mid-write). After each
    injection the index satisfies every accounting invariant and holds either the state
    before the operation or the state after it - never something in between.
    """
    from infrx.scheduling.valkey import _ENQUEUE, _us

    async def body(harness_):
        h = harness_()
        port = h.port
        outcomes = {"applied": 0, "discarded": 0}
        for number in range(12):
            candidate = event(h, org_id=(ORG_A, ORG_B)[number % 2],
                              kind=(INFER, PREPARE)[number % 2])
            before = await _consistent(port)
            enqueue_args = list(port._event_args(candidate)) + [port._max_items,
                                                               port._max_bytes]
            truncated = 40 if number % 3 == 0 else None
            await _kill_mid_call(port, _ENQUEUE, enqueue_args, truncate=truncated)
            expected = before["items"] + (0 if truncated else 1)
            assert await _settled(port, expected) == expected, \
                ("a whole command was not applied" if truncated is None
                 else "a truncated command was applied")
            after = await _consistent(port)
            if truncated:
                outcomes["discarded"] += 1
                assert after["bytes"] == before["bytes"]
                assert await port.enqueue(candidate) is True     # not indexed: retryable
            else:
                outcomes["applied"] += 1
                assert after["bytes"] > before["bytes"]
                assert await port.enqueue(candidate) is False    # indexed exactly once
            # and a claim killed mid-call leaves no candidate owned by nobody
            claim_args = [_us(h.clock.now()), "", "", "1.0",
                          ",".join(k.value for k in DISPATCH_KINDS)]
            await _kill_mid_call(port, _CLAIM, claim_args)
            await _consistent(port)
        # both halves of the injection actually happened: a command the server received
        # whole was applied whole, and a truncated one was discarded whole
        print(f"\nfault injection: {outcomes}")
        assert outcomes["applied"] == 8 and outcomes["discarded"] == 4, outcomes
        # the index is still usable, and every candidate is dispatched exactly once
        h.clock.advance(DEFAULTS.lease_ttl_s + 1)
        drained = [c.event_id for c in await drain(port, "worker-a")]
        assert len(drained) == len(set(drained)) == 12, drained
    run(body)
