#!/usr/bin/env python3
"""R32/R83: every decision L2 claims is killed by a named single edit (`mutants.py`).

    uv run --frozen pytest -q tests/l/access/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/l/access/test_mutants.py   # all
    INFRX_MUTANTS=all INFRX_D_TASK=l2 uv run --frozen pytest -q tests/l/access/test_mutants.py

Run the PostgreSQL list in a process that has not itself started the D harness (the copy
provisions its own container on the same port): its own line in `make api-mutants`
(WR-LW1I-6); after a tests/d list in the same process it skips visibly.
"""
from __future__ import annotations

import importlib.util
import os

import pytest

from tests.d import pgharness

from . import mutants as mutation_list

MEMORY, PG = mutation_list.MUTANTS, mutation_list.PG_MUTANTS
ALL = MEMORY + PG
CASES = mutation_list.case_names()
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
SUBSET = ("clock_is_the_process_clock",)
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


def test_each_list_names_only_the_cases_its_world_runs():
    """The fake list runs anywhere; the PostgreSQL list never names the fake-only case."""
    assert not any(set(m.cases) & set(mutation_list.PG_ONLY) for m in MEMORY)
    assert not any(set(m.cases) & set(mutation_list.FAKE_ONLY) for m in PG)


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome}: {result.detail}"


@pytest.mark.parametrize("mutant", SELECTED_PG, ids=[m.name for m in SELECTED_PG])
def test_pg_mutant_is_killed(mutant):
    """On the D harness's PostgreSQL (visible skip without Docker or before L2-SQL merges)."""
    if importlib.util.find_spec("infrx.state.lab_access") is None:
        pytest.skip("L2-SQL is not merged: infrx.state.lab_access (PgAccessStore) is absent")
    if pgharness._lock_fd is not None:
        # ponytail: skip, not share - the copy's harness cannot join this process's; the list's
        # own line in `make api-mutants` (WR-LW1I-6) is where it runs.
        pytest.skip("this process already holds the D harness lock (an earlier tests/d list "
                    "started it), so the copy's harness would be refused: run this list in its "
                    "own process")
    reason = pgharness.unavailable()
    if reason:
        pytest.skip(f"PostgreSQL harness unavailable: {reason}")
    result = mutation_list.run_mutant(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome}: {result.detail}"


def test_the_pg_list_skips_visibly_in_a_process_that_holds_the_d_harness(monkeypatch):
    """0-F1/1-LW1I-R1: an earlier list in this process (a tests/d list) holds the port lock, so
    the copy's own harness is refused (HarnessBusy) and the pristine baseline reads broken_runner.
    Oracle: a PG mutant in such a process is a visible skip naming the fix, never a failure."""
    monkeypatch.setattr(pgharness, "_lock_fd", -1)
    monkeypatch.setattr(mutation_list, "run_mutant",
                        lambda m: mutation_list.Result(Outcome.broken_runner, "HarnessBusy"))
    with pytest.raises(pytest.skip.Exception, match="its own process"):
        test_pg_mutant_is_killed(PG[0])


A = mutation_list.A
SELF_TESTS = (
    (Outcome.survived, Mutant("self_no_op", "a comment changes nothing", A,
                              "class LabAccess:", "class LabAccess:  # no-op",
                              (mutation_list.SEAM,))),
    (Outcome.broken_runner, Mutant("self_syntax", "a broken copy is not a kill", A,
                                   "class LabAccess:", "class LabAccess::",
                                   (mutation_list.SEAM,))),
    (Outcome.misdeclared, Mutant("self_missing_anchor", "the list matches the code", A,
                                 "not in the module", "x", (mutation_list.SEAM,))),
)


@pytest.mark.parametrize("expected,mutant", SELF_TESTS, ids=[m.name for _, m in SELF_TESTS])
def test_the_runner_cannot_report_a_false_kill(expected, mutant):
    assert mutation_list.run_mutant(mutant).outcome == expected
