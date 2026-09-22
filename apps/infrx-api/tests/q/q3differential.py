#!/usr/bin/env python3
"""Q3's differential: the reconciler over both adapters, one random stream, no daylight.

Two worlds with identical fake PostgreSQL (same clock, same id sequence) and D2 outbox,
one on the memory adapter and one on this lane's Valkey. One seeded stream of admissions,
drains (some with a lost acknowledgment), reconcile passes, rebuilds, preparation and
inference steps (some holding their lease), candidates acknowledged by a worker whose
claim never reached the store, completions, cancellations, partial and total index loss,
reaper sweeps and clock jumps past the lease TTLs drives both. After **every**
operation the returned value (or the exception type), Q2's published index state
(`differential.state`: tags, virtual times, kind tags, stats), the members, the outbox's
unacknowledged rows, every job's state and the reconciler's metrics must be equal.

On each world, after every operation: no job has held more inference leases than one
plus its requeues, and none more than one preparation; after every reconcile pass every
job PostgreSQL wants dispatched is in the index. Losing the index is a fresh adapter on
the memory side and deleting the namespace's keys on the Valkey side.

    uv run --frozen python -m tests.q.q3differential               # 20 seeds x 1000 ops
    uv run --frozen python -m tests.q.q3differential 3 --steps 200
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import random
import sys

from infrx.contracts import errors

from . import q3rig as rig
from . import differential, vkharness

SEEDS = tuple(range(1, 21))
STEPS = 1_000
OPS = (("admit", 6), ("drain", 5), ("reconcile", 3), ("rebuild", 1), ("prep", 5),
       ("infer", 4), ("hold", 2), ("complete", 2), ("cancel", 1), ("drop", 1),
       ("lose", 1), ("ack_fault", 1), ("recover", 2), ("advance", 2), ("transit", 1))
NAMES = tuple(name for name, weight in OPS for _ in range(weight))
JUMPS = (1.0, 5.0, 31.0, 121.0)          # past the preparation and inference leases


async def _lose(w: rig.World) -> None:
    if isinstance(w.index, rig.ValkeyScheduler):
        keys = [key async for key in w.index.client.scan_iter(f"{w.index.namespace}*")]
        if keys:
            await w.index.client.delete(*keys)
    else:
        w.index = w.rec.index = rig.make_index("memory", w.h.clock.now)


async def apply(w: rig.World, name: str, rng: random.Random, held: list):
    """One operation; its result, comparable across the two worlds."""
    live = [job for job in w.admitted if not w.jobs.jobs[job].terminal]
    if name == "admit":
        return await rig.admit(w, rng.choice((rig.ORG_A, rig.ORG_B)))
    if name == "drain":
        return await w.rec.drain()
    if name == "reconcile":
        report = await w.rec.reconcile()
        wanted = {event.event_id for event in await w.outbox.dispatch_snapshot()}
        missing = wanted - set(await w.index.members())
        assert not missing, f"a reconcile pass left wanted jobs unindexed: {missing}"
        return report
    if name == "rebuild":
        return await w.rec.rebuild()
    if name == "prep":
        candidate = await rig.prepare_one(w)
        return candidate and candidate.event_id
    if name == "infer":
        step = await rig.infer_one(w)
        return step and (step[0].event_id, step[1] and step[1].generation)
    if name == "hold":                           # W2's shape: the candidate stays in
        candidate = await w.index.claim_candidate("gpu-hold", kind=rig.INFER)
        if candidate is None:                    # flight while its attempt runs
            return None
        try:
            lease = await w.jobs.claim(candidate.job_id, "gpu-hold")
            w.leases[candidate.job_id] += 1
        except rig.REFUSED:
            lease = None
        held.append((candidate, lease))
        return candidate.event_id, lease and lease.generation
    if name == "complete":
        if not held:
            return None
        candidate, lease = held.pop(0)
        try:
            return lease and (await w.jobs.complete(
                lease, rig.b.outcome(lease.job_id, w.h))).state
        finally:
            await w.index.acknowledge(candidate)
    if name == "cancel":
        if not live:
            return None
        job = w.jobs.jobs[rng.choice(live)]
        return (await w.jobs.cancel(job.request.org_id, job.admission.job_handle)).state
    if name == "transit":                        # taken, the claim lost in transit, acked
        candidate = await w.index.claim_candidate("lost")
        if candidate is None:
            return None
        await w.index.acknowledge(candidate)
        return candidate.event_id
    if name == "drop":
        if not w.admitted:
            return None
        await w.index.remove(rng.choice(w.admitted))
        return "dropped"
    if name == "lose":
        await _lose(w)
        return "lost"
    if name == "ack_fault":
        w.outbox.ack_faults.append(rng.choice(("before", "after")))
        return "armed"
    if name == "recover":
        return len(await w.jobs.recover())
    if name == "advance":
        w.h.clock.advance(rng.choice(JUMPS))
        return "advanced"
    raise AssertionError(name)


async def observe(w: rig.World) -> dict:
    jobs = w.jobs.jobs
    for job_id in w.admitted:
        assert w.leases[job_id] <= 1 + jobs[job_id].attempts, \
            f"{job_id}: {w.leases[job_id]} leases for {jobs[job_id].attempts} requeues"
        assert w.prepared[job_id] <= 1, f"{job_id} was prepared twice"
    return {"index": await differential.state(w.index),
            "members": await w.index.members(),
            "unacknowledged": w.outbox.unacknowledged(),
            "jobs": {job_id: jobs[job_id].state for job_id in w.admitted},
            "metrics": dict(w.rec.metrics)}


async def run_stream(seed: int, steps: int = STEPS, counters=None, *, valkey=None) -> int:
    """One stream against both worlds; the number of compared states."""
    memory, other = rig.world("memory"), rig.world("valkey")
    if valkey is not None:                       # the canary swaps in a broken adapter
        other.index = other.rec.index = valkey(other)
    worlds = (memory, other)
    rngs = (random.Random(seed), random.Random(seed))
    helds: tuple[list, list] = ([], [])
    compared = 0
    for step in range(steps):
        name = rngs[0].choice(NAMES)
        assert rngs[1].choice(NAMES) == name
        outcomes = []
        for w, rng, held in zip(worlds, rngs, helds):
            try:
                outcomes.append(await apply(w, name, rng, held))
            except (errors.DomainError, ConnectionError) as refused:
                outcomes.append(f"raised:{type(refused).__name__}")
        if counters is not None:
            first = outcomes[0]
            counters[name] += 1
            if isinstance(first, str) and first.startswith("raised"):
                counters[f"{name}:{first}"] += 1
            if isinstance(first, dict):          # which paths a drain/pass took
                counters.update(f"{name}:{key}" for key in first)
        assert outcomes[0] == outcomes[1], \
            f"seed {seed} step {step} {name}: result diverged {outcomes}"
        mine, theirs = await observe(memory), await observe(other)
        for key in mine:
            assert mine[key] == theirs[key], \
                f"seed {seed} step {step} {name}: {key} diverged"
        compared += 1
    return compared


async def run_seeds(seeds=SEEDS, steps: int = STEPS, counters=None) -> int:
    return sum([await run_stream(seed, steps, counters) for seed in seeds])


def main() -> int:
    parser = argparse.ArgumentParser(description="Q3's reconciler differential")
    parser.add_argument("seeds", nargs="?", type=int, default=len(SEEDS))
    parser.add_argument("--steps", type=int, default=STEPS)
    args = parser.parse_args()
    if (reason := vkharness.unavailable()) is not None:
        print(f"task-local Valkey unavailable: {reason}")
        return 2
    counters: collections.Counter = collections.Counter()
    seeds = tuple(range(1, args.seeds + 1))
    compared = asyncio.run(run_seeds(seeds, args.steps, counters))
    print(f"{len(seeds)} seeds x {args.steps} operations = {compared} compared states; "
          f"no divergence\noutcomes: {dict(sorted(counters.items()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
