#!/usr/bin/env python3
"""R32/R40/R83: every decision `tests/t/retention` claims is killed by a named single edit.

    uv run --frozen pytest -q tests/t/retention/test_mutants.py                     # subset
    INFRX_T3_STACK=1 INFRX_MUTANTS=all uv run --frozen pytest -q tests/t/retention/test_mutants.py

A mutant only the real ClickHouse can see skips, naming its owner, without INFRX_T3_STACK=1.
"""
from __future__ import annotations

import os

import pytest

from . import mutants as mutation_list

ALL = mutation_list.MUTANTS
CASES = mutation_list.case_names()
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
STACK = os.environ.get("INFRX_T3_STACK") == "1"
SUBSET = ("shipper_skips_the_policy", "sweep_follows_a_cross_org_key", "sweep_ignores_holds",
          "read_ignores_content_expiry")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)
Mutant, Outcome = mutation_list.Mutant, mutation_list.Outcome
needs_stack = pytest.mark.skipif(not STACK, reason="T3 (owner: T): a store-only mutant - "
                                 "start infrx-t3-* and export INFRX_T3_STACK=1")


def test_the_list_is_well_formed():
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    assert set(SUBSET) <= {m.name for m in ALL}
    assert mutation_list.NEEDS_STACK <= {m.name for m in ALL}
    for mutant in ALL:
        assert mutant.cases and mutant.invariant and mutant.new != mutant.old, mutant.name
        for case in mutant.cases:
            assert case in CASES, f"{mutant.name} names unknown case {case}"


def test_every_case_is_covered_by_a_mutant():
    covered = {case for mutant in ALL for case in mutant.cases}
    assert CASES - covered == set(), f"cases no mutant can break: {sorted(CASES - covered)}"


@pytest.mark.parametrize("mutant", [
    pytest.param(m, marks=needs_stack) if m.name in mutation_list.NEEDS_STACK else m
    for m in SELECTED], ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome}: {result.detail}"


ANCHOR = "PENDING, CLEANED = 1, 2"
SELF_TESTS = (
    (Outcome.survived, Mutant("self_no_op", "a comment changes nothing", mutation_list.R,
                              ANCHOR, ANCHOR + "  # no-op", (mutation_list.GAUGES,))),
    (Outcome.broken_runner, Mutant("self_syntax", "a broken copy is not a kill",
                                   mutation_list.R, ANCHOR, "PENDING = = 1",
                                   (mutation_list.GAUGES,))),
    (Outcome.misdeclared, Mutant("self_missing_anchor", "the list matches the code",
                                 mutation_list.R, "not in the module", "x",
                                 (mutation_list.GAUGES,))),
)


@pytest.mark.parametrize("expected,mutant", SELF_TESTS, ids=[m.name for _, m in SELF_TESTS])
def test_the_runner_cannot_report_a_false_kill(expected, mutant):
    assert mutation_list.run_mutant(mutant).outcome == expected
