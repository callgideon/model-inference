#!/usr/bin/env python3
"""r1 R42: the bounded-sequence property test for trace captures.

The one-off cases in the trace suite each check a path someone thought of. This checks
every sequence of capture operations up to a bounded length, against every opened mode and
both deadline states, and asserts the invariants that must hold however the sequence goes.

It exists because a guard moved in the r7 pass and two routes out of that lattice
immediately counted a loss twice. A per-route case would need writing for every new route;
this cannot miss one.

    uv run --frozen pytest -q tests/contracts/test_trace_sequences.py -s     # with stats
    INFRX_TRACE_SEQUENCES=sample uv run --frozen pytest -q tests/contracts/test_trace_sequences.py
"""
from __future__ import annotations

import os
import time

from infrx.contracts.conformance import run_tracesink_sequence_properties
from infrx.contracts.conformance.sequences import MODES, OPERATIONS
from infrx.contracts.fakes import FACTORIES

# Exhaustive by default (a few seconds); `INFRX_TRACE_SEQUENCES=sample` is the escape
# hatch for a slow machine, and reports the smaller number rather than hiding it.
SAMPLE = os.environ.get("INFRX_TRACE_SEQUENCES", "").lower() in ("sample", "1", "true")


def test_every_bounded_capture_sequence_holds_the_invariants():
    started = time.monotonic()
    report = run_tracesink_sequence_properties(FACTORIES["tracesink"], sample=SAMPLE)
    elapsed = time.monotonic() - started
    print(f"\ntrace sequence properties: {report.line()} in {elapsed:.1f}s")
    # the lattice must be the size it claims: every operation, every mode, both deadlines
    assert report.sequences >= 10_000, report.line()
    if not SAMPLE:
        assert report.sampled_lengths == (), "the default run must be exhaustive"
    # `by_length` counts operation sequences; each runs against 3 modes x 2 deadline states
    assert report.by_length[1] == len(OPERATIONS)
    assert set(report.by_length) == {1, 2, 3, 4}
    assert report.sequences == sum(report.by_length.values()) * len(MODES) * 2
    # Every **guarded** invariant was reached. Three of these never were: a `minimal`
    # capture cannot accumulate, so "minimal never stores content" was structurally true,
    # and a `full` capture with no deadline charges nothing, so "declared content that is
    # missing is a marked, counted loss" had no declaration to notice. A guard that never
    # opens is an assertion that never runs - which is the same as not having it.
    assert report.unfired() == (), f"guarded invariants never reached: {report.unfired()}"
    assert all(count > 0 for count in report.fired.values()), report.fired
    # No wall-clock assertion. A threshold on a shared machine fails for reasons that have
    # nothing to do with the contract - a busy CI box, a cold page cache - and a flaky
    # gate teaches people to rerun rather than to look. The timing is *printed* above, so
    # evidence quotes the measured number and a regression is visible without being a
    # false failure. `INFRX_TRACE_SEQUENCES=sample` is the escape hatch for a slow host.
    print(f"trace sequence properties: {report.operations / max(elapsed, 1e-9):,.0f} "
          f"operations/s over {elapsed:.1f}s")


def test_the_alphabet_covers_every_capture_operation():
    """A route missing from the alphabet is a route the lattice cannot reach, so the
    alphabet is pinned against the port rather than left to drift."""
    from infrx.contracts import ports

    capture_ops = {name for name in ports.TraceCapture.__protocol_attrs__}
    assert capture_ops == {"add", "finish", "abandon", "__enter__", "__exit__"}
    for expected in ("add_ok", "add_over_budget", "add_non_bytes", "finish_matching",
                     "finish_wrong_id", "finish_wrong_org", "finish_other_mode",
                     "finish_oversized_claim",
                     # F2.1: an envelope assembled past the record validator, and the
                     # process dying mid-sequence. Both are routes a caller or a host can
                     # take that no builder-produced envelope could reach.
                     "finish_raw_content", "crash",
                     "abandon", "context_exit", "reap", "flush"):
        assert expected in OPERATIONS
