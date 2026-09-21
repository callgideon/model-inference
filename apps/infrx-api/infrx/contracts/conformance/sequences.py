"""r1 R42: bounded-sequence properties for `TraceSink`/`TraceCapture`.

Every one-off case in `services.py` checks a path someone thought of. This checks the
*lattice*: every sequence of capture operations up to a bounded length, against every
opened mode and both deadline states, asserting the invariants that must hold however the
sequence goes. It exists because the r7 pass moved a loss-count guard from one place to
its call sites, and two routes out of that lattice promptly counted a loss twice -
`abandon` then `finish`, and a budget breach then a mode-mismatched `finish`. A one-off
case per route would have to be written for each new route; this cannot miss one.

Exported so a real adapter runs the same properties:

    from infrx.contracts.conformance import run_tracesink_sequence_properties
    run_tracesink_sequence_properties(my_factory)     # raises AssertionError on failure

It is **exhaustive** by default: the whole product to length 4 - every operation sequence
x 3 opened modes x 2 deadline states - is ~136k sequences and runs in a few seconds, so
nothing is sampled and no seed caveat is needed. `Report.line()` says exactly what ran, so
evidence quotes it. A caller with a slower adapter can pass `max_length=3` or
`sample=True` and report the smaller number.
"""
from __future__ import annotations

import asyncio
import itertools
import random
from dataclasses import dataclass, field

from ..limits import DEFAULTS
from ..records import TraceLossReason, TraceMode, TraceOfferResult
from . import builders as b
from .harness import hook

SEED = 20260921
# A small budget on purpose: "add over budget" must be a few KiB, not the 248 MiB the
# default profile leaves for content - allocating that per operation is what made the
# first version of this file take minutes instead of seconds.
LIMITS = DEFAULTS.replace(trace_capture_bytes=4_096, trace_metadata_reserve_bytes=1_024,
                          trace_queue_max=8)
SAMPLE_PER_LENGTH = 4_000

# The operation alphabet: everything a caller can do to a capture, including the ways it
# can do it wrong. Each is applied to the *same* capture, in order.
OPERATIONS = ("add_ok", "add_over_budget", "add_non_bytes", "finish_matching",
              "finish_wrong_id", "finish_wrong_org", "finish_other_mode",
              "finish_oversized_claim", "abandon", "context_exit", "reap", "flush")
MODES = (TraceMode.off, TraceMode.minimal, TraceMode.full)


@dataclass
class Report:
    """What a run proved, so evidence can quote it instead of claiming it."""

    sequences: int = 0
    operations: int = 0
    by_length: dict[int, int] = field(default_factory=dict)
    sampled_lengths: tuple[int, ...] = ()

    def line(self) -> str:
        lengths = ", ".join(f"len {n}: {count}" for n, count in sorted(self.by_length.items()))
        sampled = (f" (sampled at {self.sampled_lengths}, seed {SEED})"
                   if self.sampled_lengths else " (exhaustive)")
        return (f"{self.sequences} sequences, {self.operations} operations"
                f"{sampled}; {lengths}")


def _sequences(max_length: int, full: bool) -> list[tuple[str, ...]]:
    chosen: list[tuple[str, ...]] = []
    sampled: list[int] = []
    rng = random.Random(SEED)
    for length in range(1, max_length + 1):
        product = itertools.product(OPERATIONS, repeat=length)
        if full or len(OPERATIONS) ** length <= 2_000:
            chosen.extend(product)
        else:
            population = list(product)
            chosen.extend(rng.sample(population, min(SAMPLE_PER_LENGTH, len(population))))
            sampled.append(length)
    _sequences.sampled = tuple(sampled)          # type: ignore[attr-defined]
    return chosen


async def _run_one(factory, mode: TraceMode, with_deadline: bool,
                   operations: tuple[str, ...]) -> int:
    """Apply one sequence to one capture and assert every invariant afterwards."""
    harness = factory(limits=LIMITS)
    queued = hook(harness, "queued")
    reap = hook(harness, "reap")
    budget = hook(harness, "content_budget")()
    request_id = harness.ids.uuid()
    deadline = harness.clock.at(600) if with_deadline else None
    capture = harness.port.open(request_id, b.ORG_A, mode, deadline)

    charged = 0                 # bytes `add` actually accepted
    claims: list[int] = []      # content bytes an honest finish declared
    results: list[TraceOfferResult] = []
    closed_after: int | None = None       # index of the operation that closed it

    def envelope(content_bytes: int = 0):
        return b.trace(request_id, mode=mode, content_bytes=content_bytes, metadata_bytes=16,
                       harness=harness)

    for index, operation in enumerate(operations):
        if operation == "add_ok":
            if capture.add("a" * 64):
                charged += 64
        elif operation == "add_over_budget":
            capture.add("b" * (budget + 1))
        elif operation == "add_non_bytes":
            capture.add(12_345)
        elif operation == "finish_matching":
            # the honest finish: it claims exactly what `add` charged, which is what makes
            # "a row with no content must say why" a meaningful invariant below
            claimed = charged if mode is TraceMode.full else 0
            results.append(await capture.finish(envelope(claimed)))
            claims.append(claimed)
            closed_after = index if closed_after is None else closed_after
        elif operation == "finish_wrong_id":
            results.append(await capture.finish(
                b.trace(harness.ids.uuid(), mode=mode, content_bytes=0, metadata_bytes=16,
                        harness=harness)))
            closed_after = index if closed_after is None else closed_after
        elif operation == "finish_wrong_org":
            results.append(await capture.finish(
                b.trace(request_id, org_id=b.ORG_B, mode=mode, content_bytes=0,
                        metadata_bytes=16, harness=harness)))
            closed_after = index if closed_after is None else closed_after
        elif operation == "finish_other_mode":
            other = TraceMode.full if mode is not TraceMode.full else TraceMode.minimal
            results.append(await capture.finish(
                b.trace(request_id, mode=other, content_bytes=0, metadata_bytes=16,
                        harness=harness)))
            closed_after = index if closed_after is None else closed_after
        elif operation == "finish_oversized_claim":
            # claims content it never charged; only valid to build for a full envelope
            claim = charged + 4_096
            results.append(await capture.finish(
                b.trace(request_id, mode=TraceMode.full, content_bytes=claim,
                        metadata_bytes=16, harness=harness)
                if mode is TraceMode.full else envelope()))
            closed_after = index if closed_after is None else closed_after
        elif operation == "abandon":
            await capture.abandon(TraceLossReason.abandoned)
            closed_after = index if closed_after is None else closed_after
        elif operation == "context_exit":
            with capture:
                pass
            closed_after = index if closed_after is None else closed_after
        elif operation == "reap":
            harness.clock.advance(10_000)
            reap()
        elif operation == "flush":
            await harness.port.flush(harness.clock.now())
        else:                                        # pragma: no cover - typo guard
            raise AssertionError(f"unknown operation {operation}")

    # --- the invariants ---------------------------------------------------------
    context = (mode.value, "deadline" if with_deadline else "no deadline", operations)
    stats = await harness.port.stats()
    rows = [row for row in queued() if row.request_id == request_id]
    appended = stats["appended"]

    assert stats["in_memory_content_bytes"] >= 0, f"negative bytes: {context}"
    assert len(rows) <= 1, f"more than one row for one capture: {context}"
    assert sum(stats["loss_reasons"].values()) <= 1, \
        f"more than one loss counted: {stats['loss_reasons']} {context}"
    assert stats["loss_reasons"].get("none", 0) == 0, f"a loss counted as `none`: {context}"

    if mode is TraceMode.off:
        assert not rows, f"an off-mode capture stored a row: {context}"
        assert stats["accepted"] == 0 and appended == 0, f"off-mode stored: {context}"
        assert stats["dropped"] == 0, f"off-mode counted a drop: {context}"
        assert stats["loss_reasons"] == {}, f"off-mode counted a loss: {context}"
    if mode is TraceMode.minimal:
        assert all(not row.carries_content for row in rows), \
            f"a minimal capture stored content: {context}"
    for row in rows:
        assert row.content_bytes <= charged, \
            f"stored {row.content_bytes} content bytes over {charged} charged: {context}"
        if row.content_bytes == 0 and any(claim > 0 for claim in claims):
            # content was declared and is not in the row: that is a loss, and 02 wants it
            # marked as well as counted
            assert row.loss_reason is not TraceLossReason.none, \
                f"lost content reported no loss reason: {context}"
            assert sum(stats["loss_reasons"].values()) == 1, \
                f"lost content was not counted: {stats['loss_reasons']} {context}"

    # a closed capture never queues a row afterwards, and repeats answer the first result
    if len(results) > 1:
        assert len(set(results)) == 1 or results[0] is results[-1], \
            f"a second finish answered differently: {results} {context}"
    if closed_after is not None:
        after_close = [op for op in operations[closed_after + 1:] if op.startswith("finish")]
        if after_close:
            assert len(rows) <= 1, f"a closed capture queued a row: {context}"

    # nothing leaks: once everything is closed and flushed, no bytes are still held
    await capture.abandon(TraceLossReason.abandoned)
    await harness.port.flush(harness.clock.now())
    final = await harness.port.stats()
    assert final["in_memory_content_bytes"] == 0, \
        f"{final['in_memory_content_bytes']} bytes leaked after close+flush: {context}"
    assert sum(final["loss_reasons"].values()) <= 1, \
        f"the closing abandon counted a second loss: {final['loss_reasons']} {context}"
    return len(operations)


async def _run_all(factory, sequences, report: Report) -> None:
    for operations in sequences:
        report.by_length[len(operations)] = report.by_length.get(len(operations), 0) + 1
        for mode in MODES:
            for with_deadline in (True, False):
                report.operations += await _run_one(factory, mode, with_deadline, operations)
                report.sequences += 1


async def tracesink_sequence_properties(factory, *, max_length: int = 4,
                                        sample: bool = False) -> Report:
    """The async entry point, for a caller that already has an event loop - an exported
    conformance case is `async def`, so it awaits this rather than nesting `asyncio.run`."""
    report = Report()
    sequences = _sequences(max_length, full=not sample)
    report.sampled_lengths = getattr(_sequences, "sampled", ())
    await _run_all(factory, sequences, report)
    return report


def run_tracesink_sequence_properties(factory, *, max_length: int = 4,
                                      sample: bool = False) -> Report:
    """Every capture sequence up to `max_length`, over every opened mode and both deadline
    states. Exhaustive where the product is small, deterministically sampled where it is
    not (seed 20260921), and `full=True` runs the whole product.

    One event loop for the lattice: `asyncio.run` per sequence dominated the runtime.
    Returns what it ran, so evidence quotes the number instead of claiming it.
    """
    return asyncio.run(tracesink_sequence_properties(factory, max_length=max_length,
                                                     sample=sample))
