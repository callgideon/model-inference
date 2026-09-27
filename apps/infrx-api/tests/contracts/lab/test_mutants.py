#!/usr/bin/env python3
"""R32/R83: every decision `tests/contracts/lab` claims is killed by a named single edit,
in the Python half and in the TypeScript half.

    uv run --frozen pytest -q tests/contracts/lab/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/contracts/lab/test_mutants.py   # all
"""
from __future__ import annotations

import os

import pytest

from . import mutants as mutation_list

ALL, TS_ALL = mutation_list.MUTANTS, mutation_list.TS_MUTANTS
CASES = mutation_list.case_names()
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
SUBSET = ("lab_provider_segment_ignored", "lab_units_side_by_side_unchecked",
          "lab_ambiguous_resubmits", "lab_gate_open", "lab_frozen_contract_edited",
          "ts_provider_segment_ignored")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)
TS_SELECTED = TS_ALL if FULL_RUN else tuple(m for m in TS_ALL if m.name in SUBSET)
Mutant, Outcome = mutation_list.Mutant, mutation_list.Outcome


def test_the_list_is_well_formed():
    names = [m.name for m in ALL + TS_ALL]
    assert len(set(names)) == len(names), "duplicate mutant names"
    assert set(SUBSET) <= set(names)
    for mutant in ALL + TS_ALL:
        assert mutant.cases and mutant.invariant and mutant.new != mutant.old, mutant.name
        for case in mutant.cases:
            assert case in CASES, f"{mutant.name} names unknown case {case}"
    assert all(m.file.endswith(".ts") for m in TS_ALL) and not any(m.file.endswith(".ts") for m in ALL)


def test_every_case_is_covered_by_a_mutant():
    covered = {case for mutant in ALL + TS_ALL for case in mutant.cases}
    assert CASES - covered == set(), f"cases no mutant can break: {sorted(CASES - covered)}"


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome}: {result.detail}"


@pytest.mark.parametrize("mutant", TS_SELECTED, ids=[m.name for m in TS_SELECTED])
def test_ts_mutant_is_killed(mutant):
    result = mutation_list.run_ts_mutant(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome}: {result.detail}"


TI, REJ, TS = mutation_list.TI, mutation_list.REJ, mutation_list.TS
SELF_TESTS = (
    (Outcome.survived, Mutant("self_no_op", "a comment changes nothing", mutation_list.R,
                              'SURFACE_VERSION = "contracts-lab.1"',
                              'SURFACE_VERSION = "contracts-lab.1"  # no-op', (REJ,))),
    (Outcome.misdeclared, Mutant("self_missing_anchor", "the list matches the code",
                                 mutation_list.R, "not in the module", "x", (REJ,))),
)
TS_SELF_TESTS = (
    (Outcome.survived, Mutant("self_ts_no_op", "a comment changes nothing", TI,
                              'export const MAX_VIDEO_MS = 82_000;',
                              'export const MAX_VIDEO_MS = 82_000; // no-op', (TS,))),
    (Outcome.misdeclared, Mutant("self_ts_missing_anchor", "the list matches the code", TI,
                                 "not in the module", "x", (TS,))),
    (Outcome.misdeclared, Mutant("self_ts_one_case_blind", "every named case must notice", TI,
                                 'if (mixed(p)) return "mixed_units";',
                                 'if (false) return "mixed_units";', (TS, mutation_list.VOCAB))),
)


@pytest.mark.parametrize("expected,mutant", SELF_TESTS, ids=[m.name for _, m in SELF_TESTS])
def test_the_runner_cannot_report_a_false_kill(expected, mutant):
    assert mutation_list.run_mutant(mutant).outcome == expected


@pytest.mark.parametrize("expected,mutant", TS_SELF_TESTS, ids=[m.name for _, m in TS_SELF_TESTS])
def test_the_ts_runner_cannot_report_a_false_kill(expected, mutant):
    assert mutation_list.run_ts_mutant(mutant).outcome == expected
