#!/usr/bin/env python3
"""R32/R83: every decision L3 claims is killed by a named single edit (`mutants.py`).

    uv run --frozen pytest -q tests/l/control/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/l/control/test_mutants.py   # all
    INFRX_MUTANTS=all INFRX_D_TASK=l3 uv run --frozen pytest -q tests/l/control/test_mutants.py

Run the PostgreSQL list in a process that has not itself started the D harness (the copy
provisions its own container on the same port): its own line in `make api-mutants`, beside
L2's (WR-LW1I-6).
"""
from __future__ import annotations

import os

import pytest

from tests.d import pgharness

from . import mutants as mutation_list
from .worlds import seam_missing

MEMORY, PG = mutation_list.MUTANTS, mutation_list.PG_MUTANTS
ALL = MEMORY + PG
CASES = mutation_list.case_names()
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
SUBSET = ("dev_revision_of_any_provider",)
SELECTED = MEMORY if FULL_RUN else tuple(m for m in MEMORY if m.name in SUBSET)
SELECTED_PG = PG if FULL_RUN else ()
Mutant, Outcome = mutation_list.Mutant, mutation_list.Outcome


def test_the_list_is_well_formed():
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    assert set(SUBSET) <= {m.name for m in MEMORY}
    for mutant in ALL:
        assert mutant.cases and mutant.invariant and mutant.new != mutant.old, mutant.name
        assert not mutant.dies_by, f"{mutant.name}: every death is an assertion or a typed refusal"
        for case in mutant.cases:
            assert case in CASES, f"{mutant.name} names unknown case {case}"
        source = (mutation_list.API_DIR / "infrx" / mutant.file).read_text()
        assert source.count(mutant.old) == mutant.occurrences, mutant.name


def test_every_case_is_covered_by_a_mutant():
    covered = {case for mutant in ALL for case in mutant.cases}
    assert CASES - covered == set(), f"cases no mutant can break: {sorted(CASES - covered)}"


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome}: {result.detail}"


@pytest.mark.parametrize("mutant", SELECTED_PG, ids=[m.name for m in SELECTED_PG])
def test_pg_mutant_is_killed(mutant):
    """On the D harness's PostgreSQL (visible skip without Docker or before L3-SQL merges)."""
    reason = seam_missing()
    if reason:
        pytest.skip(reason)
    if pgharness._lock_fd is not None:
        pytest.skip("this process already holds the D harness lock (an earlier tests/d list "
                    "started it), so the copy's harness would be refused: run this list in its "
                    "own process")
    reason = pgharness.unavailable()
    if reason:
        pytest.skip(f"PostgreSQL harness unavailable: {reason}")
    result = mutation_list.run_mutant(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome}: {result.detail}"


A = mutation_list.C
SELF_TESTS = (
    (Outcome.survived, Mutant("self_no_op", "a comment changes nothing", A,
                              "class LabControl:", "class LabControl:  # no-op",
                              (mutation_list.SEAM,))),
    (Outcome.broken_runner, Mutant("self_syntax", "a broken copy is not a kill", A,
                                   "class LabControl:", "class LabControl::",
                                   (mutation_list.SEAM,))),
    (Outcome.misdeclared, Mutant("self_missing_anchor", "the list matches the code", A,
                                 "not in the module", "x", (mutation_list.SEAM,))),
)


@pytest.mark.parametrize("expected,mutant", SELF_TESTS, ids=[m.name for _, m in SELF_TESTS])
def test_the_runner_cannot_report_a_false_kill(expected, mutant):
    assert mutation_list.run_mutant(mutant).outcome == expected
