#!/usr/bin/env python3
"""R32/R83: every decision I2L claims is killed by a named single edit (`mutants.py`).

    uv run --frozen pytest -q tests/i/lab/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/i/lab/test_mutants.py   # all
"""
from __future__ import annotations

import os

import pytest

from . import mutants as mutation_list

ALL = mutation_list.MUTANTS
CASES = mutation_list.case_names()
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
SUBSET = ("lab_unit_part_of_the_gateway", "control_public_bind")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)
Mutant, Outcome = mutation_list.Mutant, mutation_list.Outcome


def test_the_list_is_well_formed():
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    assert set(SUBSET) <= {m.name for m in ALL}
    for mutant in ALL:
        assert mutant.cases and mutant.invariant and mutant.new != mutant.old, mutant.name
        assert not mutant.dies_by, f"{mutant.name}: every death is an assertion"
        for case in mutant.cases:
            assert case in CASES, f"{mutant.name} names unknown case {case}"
        source = (mutation_list.API_DIR / mutant.file).read_text()
        assert source.count(mutant.old) == mutant.occurrences, mutant.name


def test_every_case_is_covered_by_a_mutant():
    covered = {case for mutant in ALL for case in mutant.cases}
    assert CASES - covered == set(), f"cases no mutant can break: {sorted(CASES - covered)}"


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome}: {result.detail}"


U = mutation_list.U
SELF_TESTS = (
    (Outcome.survived, Mutant("self_no_op", "a comment changes nothing", U,
                              "Restart=always\n", "Restart=always\n# no-op\n",
                              (mutation_list.DRAIN,))),
    (Outcome.misdeclared, Mutant("self_missing_anchor", "the list matches the files", U,
                                 "not in the unit", "x", (mutation_list.DRAIN,))),
)


@pytest.mark.parametrize("expected,mutant", SELF_TESTS, ids=[m.name for _, m in SELF_TESTS])
def test_the_runner_cannot_report_a_false_kill(expected, mutant):
    assert mutation_list.run_mutant(mutant).outcome == expected
