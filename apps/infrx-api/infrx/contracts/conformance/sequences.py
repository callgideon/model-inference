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
              "finish_oversized_claim",
              # F2.1: an envelope assembled *past* the record validator, claiming content
              # in a mode that may not carry it. `TraceEnvelope` refuses to build one, so
              # the only way into the lattice is `model_construct` - and a sink that
              # trusted the envelope instead of the capture would store unconsented
              # content that no builder could have produced for the test.
              "finish_raw_content",
              # F2.1: the process dying mid-sequence. Without it "nothing is counted
              # under `none`" and "an off capture is silent" were never exercised across
              # a crash, which is the one route that zeroes the counters by hand.
              "crash",
              "abandon", "context_exit", "reap", "flush")
CLOSERS = ("abandon", "context_exit")
MODES = (TraceMode.off, TraceMode.minimal, TraceMode.full)


# The guarded invariants, i.e. the ones that only apply to some sequences. A guard that
# never opens is an assertion that never runs, and three of these never did: a `minimal`
# capture cannot accumulate, so "minimal never stores content" was structurally true, and a
# `full` capture with no deadline charged nothing, so "declared content that is missing is a
# marked, counted loss" had no declaration to notice. `Report.fired` counts them and the
# caller asserts each was reached, which is the difference between an invariant and a
# sentence.
GUARDED = ("minimal_stores_no_content", "lost_content_is_marked", "lost_content_is_counted",
           "closed_capture_stores_nothing", "closed_capture_holds_nothing",
           "crash_losses_are_bounded", "rows_belong_to_their_capture",
           "rows_carry_the_opened_mode")


@dataclass
class Report:
    """What a run proved, so evidence can quote it instead of claiming it."""

    sequences: int = 0
    operations: int = 0
    by_length: dict[int, int] = field(default_factory=dict)
    sampled_lengths: tuple[int, ...] = ()
    fired: dict[str, int] = field(default_factory=lambda: {name: 0 for name in GUARDED})

    def unfired(self) -> tuple[str, ...]:
        return tuple(name for name in GUARDED if not self.fired.get(name))

    def line(self) -> str:
        lengths = ", ".join(f"len {n}: {count}" for n, count in sorted(self.by_length.items()))
        sampled = (f" (sampled at {self.sampled_lengths}, seed {SEED})"
                   if self.sampled_lengths else " (exhaustive)")
        fired = ", ".join(f"{name} {count}" for name, count in sorted(self.fired.items()))
        return (f"{self.sequences} sequences, {self.operations} operations"
                f"{sampled}; {lengths}; guarded invariants reached: {fired}")


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
                   operations: tuple[str, ...], fired: dict[str, int] | None = None) -> int:
    """Apply one sequence to one capture and assert every invariant afterwards."""
    harness = factory(limits=LIMITS)
    queued = hook(harness, "queued")
    reap = hook(harness, "reap")
    crash = hook(harness, "crash")
    budget = hook(harness, "content_budget")()
    request_id = harness.ids.uuid()
    deadline = harness.clock.at(600) if with_deadline else None
    capture = harness.port.open(request_id, b.ORG_A, mode, deadline)

    fired = {} if fired is None else fired
    charged = 0                 # bytes `add` actually accepted
    claims: list[int] = []      # content bytes an honest finish declared
    results: list[TraceOfferResult] = []
    closed_after: int | None = None       # index of the operation that closed it
    flushed = 0                 # rows a mid-sequence flush moved out of memory
    crashed = False             # the process died mid-sequence, so counters were zeroed

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
            # The honest finish: it claims exactly what `add` charged, which is what makes
            # "a row with no content must say why" a meaningful invariant below. Only the
            # **first** finish can queue a row, so only its claim is recorded - a later
            # finish is ignored by a closed capture, and counting its claim made the
            # loss-guard open against a row that finish never produced.
            claimed = charged if mode is TraceMode.full else 0
            if closed_after is None:
                claims.append(claimed)
            results.append(await capture.finish(envelope(claimed)))
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
            # Claims content it never charged; only valid to build for a full envelope. The
            # claim is recorded (it *is* a declaration of content), which is what opens
            # P10/P11 for a `full` capture that charged nothing - the no-deadline case.
            claim = charged + 4_096
            if mode is TraceMode.full and closed_after is None:
                claims.append(claim)
            results.append(await capture.finish(
                b.trace(request_id, mode=TraceMode.full, content_bytes=claim,
                        metadata_bytes=16, harness=harness)
                if mode is TraceMode.full else envelope()))
            closed_after = index if closed_after is None else closed_after
        elif operation == "finish_raw_content":
            # Assembled past the validator on purpose: `TraceEnvelope` refuses content in
            # `off`/`minimal`, so nothing a builder can produce tests the sink's own
            # refusal. The capture decides, never the envelope (R12).
            raw = envelope().model_construct(
                **{**envelope().model_dump(), "mode": mode, "content_bytes": 4_096,
                   "content_complete": True,
                   "content_ref": f"traces/{b.ORG_A}/{request_id}.json.zst"})
            # Deliberately not added to `claims`: this is a dishonest declaration (the
            # caller bug under test), so "content was declared and is missing, therefore a
            # loss must be marked" does not apply to it.
            results.append(await capture.finish(raw))
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
            before_flush = list(queued())
            await harness.port.flush(harness.clock.now())
            after = await harness.port.stats()
            # A flush moves what was in memory to `appended` and leaves nothing behind:
            # `appended` was read and never asserted, so a sink that dropped the queue
            # instead of appending it looked identical.
            assert after["in_memory"] == 0, f"a flush left rows in memory: {operations}"
            assert after["appended"] >= len(before_flush), \
                f"a flush lost {len(before_flush) - after['appended']} rows: {operations}"
            flushed += len(before_flush)
        elif operation == "crash":
            crash()
            crashed = True
        else:                                        # pragma: no cover - typo guard
            raise AssertionError(f"unknown operation {operation}")

    # --- the invariants ---------------------------------------------------------
    context = (mode.value, "deadline" if with_deadline else "no deadline", operations)
    stats = await harness.port.stats()
    rows = [row for row in queued() if row.request_id == request_id]
    # Rows a mid-sequence flush already moved out of memory count as stored too: reading
    # only `queued()` made "a closed capture queues no row" pass whenever a flush had
    # emptied the queue first.
    stored_rows = len(rows) + flushed

    assert stats["in_memory_content_bytes"] >= 0, f"negative bytes: {context}"
    assert stored_rows <= 1, f"more than one row for one capture: {context}"
    # R42 bounds the losses **this capture** contributes. A crash additionally loses the
    # appended-but-unsynced *rows* and counts one `shutdown` each, which 02 explicitly
    # allows ("crash may lose unsynced events") and is a different fact, so it is counted
    # separately rather than folded in - and bounded by what there was to lose.
    shutdown = stats["loss_reasons"].get("shutdown", 0)
    capture_losses = sum(stats["loss_reasons"].values()) - (shutdown if crashed else 0)
    assert capture_losses <= 1, \
        f"more than one loss counted for one capture: {stats['loss_reasons']} {context}"
    if crashed:
        assert shutdown <= stored_rows, \
            f"a crash counted {shutdown} lost rows out of {stored_rows}: {context}"
    else:
        assert shutdown == 0, f"a shutdown loss with no crash: {context}"
    assert stats["loss_reasons"].get("none", 0) == 0, f"a loss counted as `none`: {context}"

    if mode is TraceMode.off:
        assert not rows, f"an off-mode capture stored a row: {context}"
        assert stats["accepted"] == 0, f"off-mode stored: {context}"
        assert stats["appended"] == 0, f"off-mode appended a row: {context}"
        assert stats["dropped"] == 0, f"off-mode counted a drop: {context}"
        # A crash counts `shutdown` for records it lost; with nothing ever stored an
        # off-mode capture has none to lose, so the table stays empty even across one.
        assert stats["loss_reasons"] == {}, f"off-mode counted a loss: {context}"
    # P17: identity. `rows` is filtered by this request, so a capture that stored a row
    # under **another** identity was invisible to the lattice - the one place a wrong-id or
    # wrong-org finish could actually be observed. Every queued row is checked instead.
    everything = queued()
    foreign = [row for row in everything
               if row.request_id != request_id or row.org_id != b.ORG_A]
    if everything:
        fired["rows_belong_to_their_capture"] = fired.get("rows_belong_to_their_capture", 0) + 1
    assert not foreign, \
        f"a capture stored a row under another identity: {[r.request_id for r in foreign]} {context}"
    # P18: mode. Nothing asserted that a stored row carries the mode the capture was
    # **opened** with, so a live capture trusting the envelope's label went unnoticed.
    for row in rows:
        fired["rows_carry_the_opened_mode"] = fired.get("rows_carry_the_opened_mode", 0) + 1
        assert row.mode is mode, \
            f"a row opened {mode} was stored as {row.mode}: {context}"
    if mode is TraceMode.minimal and rows:
        # P08. It fires only once a `minimal` capture has a row at all - which the raw
        # envelope operation is what produces, since `add` on a minimal capture is a no-op.
        fired["minimal_stores_no_content"] = fired.get("minimal_stores_no_content", 0) + 1
        assert all(not row.carries_content for row in rows), \
            f"a minimal capture stored content: {context}"
    for row in rows:
        assert row.content_bytes <= charged, \
            f"stored {row.content_bytes} content bytes over {charged} charged: {context}"
        if row.content_bytes == 0 and any(claim > 0 for claim in claims):
            # P10/P11: content was declared and is not in the row, so it is a loss, and 02
            # wants it marked as well as counted. The `full` capture with no deadline is the
            # case this exists for - it charges nothing, so only a declaration it never
            # accumulated makes the guard open.
            fired["lost_content_is_marked"] = fired.get("lost_content_is_marked", 0) + 1
            assert row.loss_reason is not TraceLossReason.none, \
                f"lost content reported no loss reason: {context}"
            fired["lost_content_is_counted"] = fired.get("lost_content_is_counted", 0) + 1
            assert sum(stats["loss_reasons"].values()) == 1, \
                f"lost content was not counted: {stats['loss_reasons']} {context}"

    # a closed capture never queues a row afterwards, and repeats answer the first result
    if len(results) > 1:
        assert len(set(results)) == 1 or results[0] is results[-1], \
            f"a second finish answered differently: {results} {context}"
    if crashed:
        fired["crash_losses_are_bounded"] = fired.get("crash_losses_are_bounded", 0) + 1
    if closed_after is not None and operations[closed_after] in CLOSERS:
        fired["closed_capture_stores_nothing"] = \
            fired.get("closed_capture_stores_nothing", 0) + 1
        # The capture was abandoned (or fell out of its `with`) before any `finish`, so
        # every later finish must queue **nothing at all**. `<= 1` was vacuous here: one
        # row is what a working finish produces, so the old assertion passed either way.
        assert stored_rows == 0, \
            f"a capture closed by {operations[closed_after]} still stored a row: {context}"

    # Nothing is held that no row accounts for - checked **before** the flush below,
    # because a flush recomputes the byte total from the open captures and would hide a
    # leak. Content queued but not yet flushed is legitimately still charged, so the
    # invariant is equality against the rows in memory rather than zero.
    queued_bytes = sum(row.content_bytes for row in rows)
    assert stats["in_memory_content_bytes"] <= max(charged, queued_bytes), \
        (f"{stats['in_memory_content_bytes']} bytes held against {charged} charged and "
         f"{queued_bytes} queued: {context}")
    if closed_after is not None and not crashed:
        fired["closed_capture_holds_nothing"] = fired.get("closed_capture_holds_nothing", 0) + 1
        assert stats["open_captures"] == 0, f"a closed capture is still open: {context}"
        assert stats["in_memory_content_bytes"] == queued_bytes, \
            (f"{stats['in_memory_content_bytes']} bytes held by a closed capture against "
             f"{queued_bytes} in its queued row, before any flush: {context}")

    # nothing leaks: once everything is closed and flushed, no bytes are still held
    await capture.abandon(TraceLossReason.abandoned)
    await harness.port.flush(harness.clock.now())
    final = await harness.port.stats()
    assert final["in_memory_content_bytes"] == 0, \
        f"{final['in_memory_content_bytes']} bytes leaked after close+flush: {context}"
    final_capture_losses = sum(final["loss_reasons"].values()) - (
        final["loss_reasons"].get("shutdown", 0) if crashed else 0)
    assert final_capture_losses <= 1, \
        f"the closing abandon counted a second loss: {final['loss_reasons']} {context}"
    return len(operations)


async def _run_all(factory, sequences, report: Report) -> None:
    for operations in sequences:
        report.by_length[len(operations)] = report.by_length.get(len(operations), 0) + 1
        for mode in MODES:
            for with_deadline in (True, False):
                report.operations += await _run_one(factory, mode, with_deadline, operations,
                                                   report.fired)
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
