#!/usr/bin/env python3
"""Q2's differential oracle: one random operation stream, two adapters, no daylight.

The memory adapter is the specification (Q1 evidence, "What Q2 must reproduce"), so the
strongest statement Q2 can make is not "the Valkey adapter passes some tests" but "no
sequence of operations distinguishes it from the reference". This module builds the
streams and runs them against both adapters at once, comparing, **after every single
operation**, the result the operation returned *and* the whole published state:
`tags()`, `virtual_times()`, `kind_tags()`, `top_virtual_time()` and `stats()`.

The mix is the reviewer's reference-model fuzz style, and it deliberately contains the
three things the r2/r3 heads had no test for:

* unfiltered claims **while both kinds hold work** (R60 level 1 only moves then),
* **non-unit costs** and non-unit weights, so a float that is merely *close* diverges
  (tags are IEEE doubles in both adapters; the Lua writes them with `%.17g` and does the
  arithmetic in the memory adapter's order, so equality here is bit equality),
* a **poisoned estimator** on a live index: both adapters must refuse identically and
  leave the index byte-identical.

    uv run --frozen python tests/q/differential.py            # 40 seeds x 2500 ops
    uv run --frozen python tests/q/differential.py 3 --steps 200
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import random
import sys
import time
from datetime import timedelta

from infrx.contracts import errors
from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.support import DEFAULT_START
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import ExecutionMode, IndexEvent, OutboxKind

from . import support, vkharness

SEEDS = tuple(range(1, 41))                 # 40 seeds
STEPS = 2_500                               # x 2,500 operations
ORGS = (support.ORG_A, support.ORG_B, support.ORG_C)
KINDS = (OutboxKind.prepare_dispatch, OutboxKind.inference_dispatch)
# Non-unit weights: with equal weights a tag advance of `cost / weight` cannot be told
# from `cost`, and the Lua would pass while ignoring the weight. Neither weight is a
# power of two, so the division is inexact and the tags drift - which is the point: an
# adapter that formats its scores with Lua's default %.14g agrees with the reference
# until a tag needs its fifteenth significant digit, and then stops agreeing.
WEIGHTS = {support.ORG_B: 3.0, support.ORG_C: 0.4}
# Bounded index, small enough that a cap binds inside a 2,500-operation stream (so the
# refusal path is differential too) and that a full scan stays cheap. An `IndexEvent` is
# ~317 compact bytes whatever it carries, so one configuration can only ever bind one of
# the two caps: odd seeds run against the item cap, even seeds against the byte cap.
TIGHT_ITEMS = dict(max_items=40, max_bytes=8_000_000)
TIGHT_BYTES = dict(max_items=100_000, max_bytes=11_000)


def caps(seed: int) -> dict:
    return TIGHT_ITEMS if seed % 2 else TIGHT_BYTES


#: The four ways the injected estimator can go wrong (Q1 evidence, point 8). The first
#: three are ours and must surface as `internal_error`; a `DomainError` the estimator
#: raises itself must pass through **unchanged**, which is a different code.
FAULTS = ("zero", "nan", "raise", "domain")


class Cost:
    """The injected service-time estimator, shared by both adapters.

    Non-unit and deterministic per event (`03` §2.3 wants predicted service seconds, and
    Marlin prompts vary ~11x), with a switch the stream flips for exactly one claim: a
    bad answer must be refused identically by both adapters, with the index untouched.
    """

    def __init__(self) -> None:
        self.fault: tuple[OutboxKind, str] | None = None

    def __call__(self, event: IndexEvent) -> float:
        if self.fault is not None and event.kind is self.fault[0]:
            mode = self.fault[1]
            if mode == "zero":
                return 0.0
            if mode == "nan":
                return float("nan")
            if mode == "raise":
                raise RuntimeError("the estimator has a bug")
            raise errors.InvalidRequest("the estimator refused this candidate")
        return 0.37 + (int(event.event_id[:4], 16) % 11) * 0.13


def _event(n: int, org_id: str, kind: OutboxKind, delay_s: float) -> IndexEvent:
    """A prebuilt event, so both adapters index byte-identical candidates."""
    return IndexEvent(event_id=f"{n:08x}-0000-4000-8000-{n:012x}",
                      job_id=f"{n + 700000:08x}-0000-4000-8000-{n + 700000:012x}",
                      org_id=org_id, key_id=b.KEY_A, kind=kind,
                      execution_mode=ExecutionMode.async_,
                      available_at=DEFAULT_START + timedelta(seconds=delay_s))


def workload(seed: int, steps: int = STEPS) -> list[tuple]:
    """A deterministic script of port operations, built once and replayed as data."""
    rng = random.Random(seed)
    script: list[tuple] = []
    indexed: list[IndexEvent] = []
    for n in range(1, steps + 1):
        roll = rng.random()
        if roll < 0.42 or not indexed:
            candidate = _event(n, rng.choice(ORGS), rng.choice(KINDS),
                               rng.choice((0.0, 0.0, 0.0, 3.0, 40.0)))
            indexed.append(candidate)
            script.append(("enqueue", candidate))
        elif roll < 0.48:
            script.append(("enqueue", rng.choice(indexed)))     # at-least-once delivery
        elif roll < 0.62:
            script.append(("claim", f"worker-{rng.randrange(3)}", None))
        elif roll < 0.72:
            script.append(("claim", f"worker-{rng.randrange(3)}", rng.choice(KINDS)))
        elif roll < 0.74:
            script.append(("claim_unknown", OutboxKind.usage_projection))
        elif roll < 0.84:
            script.append(("ack", rng.randrange(4)))
        elif roll < 0.89:
            script.append(("remove", rng.choice(indexed).job_id))
        elif roll < 0.93:
            # a whole kind is mispriced, so the claim really does hit the bad answer
            script.append(("poison", rng.choice(KINDS), rng.choice(FAULTS),
                           rng.choice((None, *KINDS))))
        elif roll < 0.97:
            script.append(("advance", rng.choice((0.5, 4.0, 31.0, 121.0))))
        else:
            script.append(("rebuild", tuple(rng.sample(indexed, min(len(indexed), 9)))))
    return script


async def _call(value):
    """The memory adapter publishes its state synchronously, the Valkey one over the
    wire; the comparison does not care which."""
    return await value if asyncio.iscoroutine(value) else value


async def state(port) -> dict:
    """Everything the two adapters must agree on, in one comparable dict."""
    if hasattr(port, "snapshot"):
        return await port.snapshot()
    return {"tags": port.tags(), "virtual_times": port.virtual_times(),
            "kind_tags": port.kind_tags(), "top_virtual_time": port.top_virtual_time(),
            "stats": port.stats()}


async def apply(port, clock, step, held: list[str], cost: Cost):
    """One operation, as a result the two adapters can be compared on.

    Every refusal is compared by its typed code, not by chance: a `capacity_exhausted`
    from one adapter and an `internal_error` from the other would be a divergence even
    though both "raised".
    """
    name = step[0]
    try:
        if name == "enqueue":
            return ("enqueue", await port.enqueue(step[1]))
        if name == "claim":
            candidate = await port.claim_candidate(step[1], kind=step[2])
            if candidate is not None:
                held.append(candidate)
            return ("claim", None if candidate is None else candidate.event_id)
        if name == "claim_unknown":
            await port.claim_candidate("worker-x", kind=step[1])
            return ("claim_unknown", "no refusal")
        if name == "ack":
            if not held:
                return ("ack", "nothing held")
            event = held.pop(step[1] % len(held))
            await port.acknowledge(event)
            return ("ack", event.event_id)
        if name == "remove":
            await port.remove(step[1])
            held[:] = [event for event in held if event.job_id != step[1]]
            return ("remove", step[1])
        if name == "poison":
            cost.fault = (step[1], step[2])
            try:
                candidate = await port.claim_candidate("worker-poison", kind=step[3])
                if candidate is not None:
                    held.append(candidate)
                return ("poison", None if candidate is None else candidate.event_id)
            finally:
                cost.fault = None
        if name == "advance":
            clock.advance(step[1])
            return ("advance", step[1])
        if name == "rebuild":
            held.clear()
            return ("rebuild", await port.rebuild(step[1]))
    except errors.DomainError as refused:
        return (name, "refused", type(refused).__name__, refused.code)
    raise AssertionError(f"unknown step {name!r}")


async def run_stream(seed: int, steps: int = STEPS, *, connection=None,
                     counters=None) -> int:
    """Play one stream against both adapters, comparing after every operation."""
    counters = collections.Counter() if counters is None else counters
    cost = Cost()
    common = dict(weights=WEIGHTS, cost=cost, **caps(seed))
    reference = support.harness(**common)
    subject = vkharness.harness(connection=connection, **common)
    ref_held: list[IndexEvent] = []
    sub_held: list[IndexEvent] = []
    script = workload(seed, steps)
    previous = await state(subject.port)
    for number, step in enumerate(script, start=1):
        where = f"seed {seed} step {number} {step[0]}"
        expected = await apply(reference.port, reference.clock, step, ref_held, cost)
        actual = await apply(subject.port, subject.clock, step, sub_held, cost)
        assert expected == actual, f"{where}: memory returned {expected}, valkey {actual}"
        assert [e.event_id for e in ref_held] == [e.event_id for e in sub_held], where
        want, got = await state(reference.port), await state(subject.port)
        for field in ("tags", "virtual_times", "kind_tags", "top_virtual_time", "stats"):
            assert want[field] == got[field], \
                f"{where}: {field} diverged\n  memory: {want[field]}\n  valkey: {got[field]}"
        if len(actual) > 2 and step[0] == "enqueue":
            # a refused enqueue writes nothing at all: not a byte, not a tag, not a flow
            # for the tenant it refused (a half-inserted candidate would hold a cap slot
            # for ever). A refused *claim* is not in this assertion on purpose: both
            # adapters expire visibilities before they price the candidate, so a claim
            # that then refuses has legitimately returned lost workers' candidates -
            # `test_valkey_scheduler` pins the untouched-index half of point 8 directly.
            assert got == previous, f"{where}: a refused enqueue moved the index"
        previous = got
        counters[expected[0] if len(expected) < 3 else f"{expected[0]}:{expected[3]}"] += 1
    if connection is None:
        await subject.port.client.aclose()
    return len(script)


async def run_seeds(seeds=SEEDS, steps: int = STEPS, counters=None) -> int:
    """Every seed over one connection, so the run measures the index and not TCP."""
    connection = vkharness.client()
    counters = collections.Counter() if counters is None else counters
    total = 0
    try:
        for seed in seeds:
            started = time.monotonic()
            total += await run_stream(seed, steps, connection=connection,
                                      counters=counters)
            print(f"seed {seed:3d}: {steps} operations agreed "
                  f"({time.monotonic() - started:.1f}s)", flush=True)
    finally:
        await connection.aclose()
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="Q2 memory-vs-Valkey differential")
    parser.add_argument("seeds", nargs="*", type=int, default=list(SEEDS))
    parser.add_argument("--steps", type=int, default=STEPS)
    args = parser.parse_args()
    reason = vkharness.unavailable()
    if reason:
        print(f"PENDING: {reason}")
        return 3
    counters = collections.Counter()
    total = asyncio.run(run_seeds(tuple(args.seeds), args.steps, counters))
    print(f"\n{len(args.seeds)} seeds x {args.steps} operations = {total} compared "
          f"states; no divergence")
    for outcome, count in sorted(counters.items()):
        print(f"{count:7d}  {outcome}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
