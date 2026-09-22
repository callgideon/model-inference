#!/usr/bin/env python3
"""r1 R32: every invariant `test_trace_spool.py` claims must be killable.

`mutants.py` declares the single-edit defects; this runs them. One pytest process per
mutant, so the default suite runs a fast subset and the whole list runs on demand:

    uv run --frozen pytest -q tests/t/test_trace_mutants.py            # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/t/test_trace_mutants.py
"""
from __future__ import annotations

import os

import pytest

from . import mutants as mutation_list

ALL = mutation_list.MUTANTS
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
# One per area - the reader, the fsync boundary, the caps, the ack interface - so a broken
# runner or a vacuous case shows up in CI time; the rest run on demand.
SUBSET = ("checksum_not_verified", "a_failed_fsync_is_reported_durable",
          "the_active_segment_can_be_acked", "the_spool_cap_is_ignored",
          "understated_content_is_accepted")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)


def test_the_list_is_well_formed():
    """A typo in a case name would make a mutant unkillable by construction and pass
    silently, so the list is checked against the suite it names."""
    import inspect

    from . import test_trace_spool as suite

    names = {name for name, value in vars(suite).items()
             if name.startswith("test_") and inspect.isfunction(value)}
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    for mutant in ALL:
        assert mutant.cases, f"{mutant.name} names no case"
        assert mutant.invariant, f"{mutant.name} states no invariant"
        for case in mutant.cases:
            assert case in names, f"{mutant.name} names unknown case {case}"
    assert set(SUBSET) <= {m.name for m in ALL}


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The cases {list(mutant.cases)} do not prove "
                           f"what they claim.")


def test_the_runner_cannot_report_a_false_kill():
    """A runner that counted a syntax error as a kill would let the whole list pass while
    proving nothing, so each outcome is exercised deliberately."""
    case = mutation_list.ACK
    _m = mutation_list._m
    checks = (
        (mutation_list.Outcome.broken_runner,
         _m("self_syntax_error", "a broken copy is not a kill",
            "class SpoolIO:", "class SpoolIO:::", case)),
        (mutation_list.Outcome.survived,
         _m("self_no_op", "an edit that changes nothing survives",
            "SEGMENT_VERSION = 2", "SEGMENT_VERSION = 2  # a comment changes no behaviour", case)),
        (mutation_list.Outcome.misdeclared,
         _m("self_missing_anchor", "the list matches the code",
            "this text is not in the module", "nor is this", case)),
        (mutation_list.Outcome.misdeclared,
         _m("self_no_case", "every mutant names a case",
            "SEGMENT_VERSION = 2", "SEGMENT_VERSION = 3")),
    )
    for expected, mutant in checks:
        result = mutation_list.run_mutant(mutant)
        assert result.outcome is expected, f"{mutant.name}: {result.outcome} - {result.detail}"
        assert not result.killed
