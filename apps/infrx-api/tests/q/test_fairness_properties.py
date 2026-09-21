#!/usr/bin/env python3
"""Q1 property tests: seeded workloads, a shadow model, no wall clock.

Four properties the acceptance criteria name, each over pseudo-random workloads built
from a fixed seed list (`random.Random(seed)`, stdlib, no plugin): determinism,
no starvation of a bounded peer, work conservation, and `rebuild` after `enqueue`
being the same index as `rebuild` alone.

The work-conservation and no-loss properties are checked against a **shadow model**
kept in the test - which events are indexed, which are in flight, and when a lost
worker's candidate becomes visible again - so the assertions do not simply ask the
implementation whether it agrees with itself.

    uv run --frozen pytest -q tests/q/test_fairness_properties.py
"""
from __future__ import annotations

import asyncio
import random
from datetime import timedelta

from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.support import DEFAULT_START
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import ExecutionMode, IndexEvent, OutboxKind

from .support import ORG_A, ORG_B, ORG_C, harness

SEEDS = (1, 2, 3, 5, 8, 13, 21, 34)
ORGS = (ORG_A, ORG_B, ORG_C)
KINDS = (OutboxKind.prepare_dispatch, OutboxKind.inference_dispatch)


def _visibility_s(kind: OutboxKind) -> float:
    return (DEFAULTS.preparation_lease_ttl_s if kind is OutboxKind.prepare_dispatch
            else DEFAULTS.lease_ttl_s)


def _event(n: int, org_id: str, kind: OutboxKind, delay_s: float) -> IndexEvent:
    """A prebuilt event, so two runs of one script index byte-identical candidates."""
    return IndexEvent(event_id=f"{n:08x}-0000-4000-8000-{n:012x}",
                      job_id=f"{n + 900000:08x}-0000-4000-8000-{n + 900000:012x}",
                      org_id=org_id, key_id=b.KEY_A, kind=kind,
                      execution_mode=ExecutionMode.async_,
                      available_at=DEFAULT_START + timedelta(seconds=delay_s))


def workload(seed: int, steps: int = 80) -> list[tuple]:
    """A deterministic script of port operations, built once and replayed as data."""
    rng = random.Random(seed)
    script: list[tuple] = []
    indexed: list[IndexEvent] = []
    for n in range(1, steps + 1):
        choice = rng.random()
        if choice < 0.45 or not indexed:
            candidate = _event(n, rng.choice(ORGS), rng.choice(KINDS),
                               rng.choice((0.0, 0.0, 0.0, 5.0, 45.0)))
            indexed.append(candidate)
            script.append(("enqueue", candidate))
        elif choice < 0.70:
            script.append(("claim", f"worker-{rng.randrange(3)}",
                           rng.choice((None, *KINDS))))
        elif choice < 0.80:
            script.append(("ack",))
        elif choice < 0.88:
            script.append(("remove", rng.choice(indexed).job_id))
        elif choice < 0.94:
            script.append(("replay", rng.choice(indexed)))      # at-least-once delivery
        else:
            script.append(("advance", rng.choice((1.0, 20.0, 31.0, 121.0))))
    return script


class Shadow:
    """What the index must be holding, tracked independently of the index."""

    def __init__(self) -> None:
        self.live: dict[str, IndexEvent] = {}
        self.claimed_at: dict[str, float] = {}
        self.gone: set[str] = set()                 # acknowledged: never indexed again
        self.elapsed = 0.0

    def available(self, kind: OutboxKind | None) -> list[IndexEvent]:
        out = []
        for event_id, event in self.live.items():
            if kind is not None and event.kind is not kind:
                continue
            held = self.claimed_at.get(event_id)
            if held is not None and self.elapsed < held + _visibility_s(event.kind):
                continue
            if (event.available_at - DEFAULT_START).total_seconds() <= self.elapsed:
                out.append(event)
        return out


async def _play(port, clock, script, shadow: Shadow) -> list[str]:
    """Run one script against one index, checking work conservation at every step."""
    order: list[str] = []
    held: list[IndexEvent] = []
    for step in script:
        if step[0] in ("enqueue", "replay"):
            event = step[1]
            known = event.event_id in shadow.live or event.event_id in shadow.gone
            assert await port.enqueue(event) is (not known), \
                f"enqueue({event.event_id}) disagreed with the shadow model"
            if not known:
                shadow.live[event.event_id] = event
        elif step[0] == "claim":
            _, worker, kind = step
            expected = shadow.available(kind)
            candidate = await port.claim_candidate(worker, kind=kind)
            # work conserving: a candidate exists <=> one is handed out
            assert (candidate is None) is (expected == []), \
                f"claim(kind={kind}) returned {candidate} with {len(expected)} available"
            if candidate is not None:
                assert candidate.event_id in {event.event_id for event in expected}
                shadow.claimed_at[candidate.event_id] = shadow.elapsed
                order.append(candidate.event_id)
                held.append(candidate)
        elif step[0] == "ack":
            if held:
                event = held.pop(0)
                await port.acknowledge(event)
                shadow.live.pop(event.event_id, None)
                shadow.claimed_at.pop(event.event_id, None)
                shadow.gone.add(event.event_id)
        elif step[0] == "remove":
            job_id = step[1]
            await port.remove(job_id)
            for event_id, event in list(shadow.live.items()):
                if event.job_id == job_id:
                    del shadow.live[event_id]
                    shadow.claimed_at.pop(event_id, None)
            held = [event for event in held if event.job_id != job_id]
        else:
            clock.advance(step[1])
            shadow.elapsed += step[1]
    return order


def test_q1_property__identical_input_gives_an_identical_dispatch_order():
    """Q1 acceptance: identical input produces a deterministic dispatch order. Two
    fresh indices fed the same script must agree on every dispatch, on the fairness
    tags they end with and on their accounting - nothing may depend on dict order,
    hashing or a wall clock."""
    async def run():
        for seed in SEEDS:
            script = workload(seed)
            runs = []
            for _ in range(2):
                h = harness()
                order = await _play(h.port, h.clock, script, Shadow())
                runs.append((order, h.port.tags(), h.port.stats()))
            assert runs[0][0] == runs[1][0], f"seed {seed}: dispatch order differed"
            assert runs[0][1] == runs[1][1], f"seed {seed}: fairness state differed"
            assert runs[0][2] == runs[1][2], f"seed {seed}: accounting differed"
            assert runs[0][0], f"seed {seed} dispatched nothing"
    asyncio.run(run())


def test_q1_property__the_index_is_work_conserving_and_loses_nothing():
    """Work conservation (checked at every step against the shadow model) plus the
    DUR-OUTBOX half: after the script, draining the index yields every live candidate
    exactly once - never twice, never one belonging to a cancelled job."""
    async def run():
        for seed in SEEDS:
            h = harness()
            shadow = Shadow()
            await _play(h.port, h.clock, workload(seed), shadow)
            # every candidate still in flight comes back, so the drain sees all of them
            h.clock.advance(DEFAULTS.lease_ttl_s + 1)
            shadow.elapsed += DEFAULTS.lease_ttl_s + 1
            drained: list[str] = []
            while (candidate := await h.port.claim_candidate("drain")) is not None:
                assert candidate.event_id not in drained, f"seed {seed}: dispatched twice"
                drained.append(candidate.event_id)
                await h.port.acknowledge(candidate)
            assert sorted(drained) == sorted(shadow.live), f"seed {seed}: index lost work"
            assert h.port.stats()["items"] == 0 and h.port.tags() == {}, \
                f"seed {seed}: an empty index kept state"
    asyncio.run(run())


def test_q1_property__a_noisy_tenant_cannot_starve_a_bounded_peer():
    """Q1 acceptance, as a bound rather than a vibe: while a tenant is backlogged, the
    gap between two of its dispatches is at most the number of competing tenants,
    whatever backlog the noisy tenant has. With N tenants a peer therefore waits at
    most N-1 dispatch slots, so its wait is bounded by concurrency, not by the queue."""
    async def run():
        for seed in SEEDS:
            rng = random.Random(seed)
            noisy_depth = rng.randrange(20, 60)
            peers = {org: rng.randrange(2, 6) for org in ORGS[1:]}
            h = harness()
            n = 0
            for _ in range(noisy_depth):
                n += 1
                assert await h.port.enqueue(_event(n, ORG_A, KINDS[1], 0.0))
            for org, depth in peers.items():
                for _ in range(depth):
                    n += 1
                    assert await h.port.enqueue(_event(n, org, KINDS[1], 0.0))
            order = []
            while (candidate := await h.port.claim_candidate("worker-a")) is not None:
                order.append(candidate.org_id)
                await h.port.acknowledge(candidate)
            tenants = 1 + len(peers)
            for peer, depth in peers.items():
                slots = [index for index, org in enumerate(order) if org == peer]
                assert len(slots) == depth, f"seed {seed}: {peer} lost work"
                assert slots[0] < tenants, f"seed {seed}: {peer} waited {slots[0]} slots"
                gaps = [second - first for first, second in zip(slots, slots[1:])]
                assert all(gap <= tenants for gap in gaps), \
                    f"seed {seed}: {peer} waited {gaps} slots behind {noisy_depth} queued"
            assert order.count(ORG_A) == noisy_depth      # and the noisy tenant is served
    asyncio.run(run())


def test_q1_property__rebuild_after_enqueue_is_the_same_index_as_rebuild_alone():
    """Q1 acceptance: `rebuild` is idempotent over `enqueue`. An index that has been
    running, and one that has just been rebuilt from the same PostgreSQL snapshot, are
    the same index: same dispatch order, same fairness state, same accounting. That is
    what makes a Valkey flush or an adapter switch a throughput event, not a data
    event."""
    async def run():
        for seed in SEEDS:
            script = workload(seed)
            snapshot = tuple(step[1] for step in script if step[0] == "enqueue")
            elapsed = sum(step[1] for step in script if step[0] == "advance")
            hot = harness()
            await _play(hot.port, hot.clock, script, Shadow())
            assert await hot.port.rebuild(snapshot) == len(snapshot)
            cold = harness()
            cold.clock.advance(elapsed)                   # same instant, same availability
            assert await cold.port.rebuild(snapshot) == len(snapshot)
            assert await cold.port.rebuild(snapshot) == len(snapshot)     # and again
            assert hot.port.tags() == cold.port.tags(), f"seed {seed}: fairness differed"
            assert hot.port.stats() == cold.port.stats(), f"seed {seed}: accounting differed"
            hot_order = [c.event_id for c in await _drain(hot.port, hot.clock)]
            cold_order = [c.event_id for c in await _drain(cold.port, cold.clock)]
            assert hot_order == cold_order, f"seed {seed}: dispatch order differed"
            assert sorted(hot_order) == sorted({e.event_id for e in snapshot}), \
                f"seed {seed}: the rebuilt index lost or duplicated a candidate"
    asyncio.run(run())


async def _drain(port, clock):
    """Drain what is available, then advance past the longest delay and lease and drain
    again, so candidates waiting on `available_at` are included exactly once."""
    out = []
    for _ in range(2):
        while (candidate := await port.claim_candidate("drain")) is not None:
            out.append(candidate)
            await port.acknowledge(candidate)
        clock.advance(DEFAULTS.lease_ttl_s + 60.0)
    return out
