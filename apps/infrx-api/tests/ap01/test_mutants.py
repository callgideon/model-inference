#!/usr/bin/env python3
"""R32/R83: every decision AP-01 claims is killed by a named single edit (`mutants.py`).

    uv run --frozen pytest -q tests/ap01/test_mutants.py                                  # subset
    INFRX_MUTANTS=all INFRX_D_TASK=ap1 uv run --frozen pytest -q tests/ap01/test_mutants.py  # all

Run it in a process that has not itself started the D harness (the PostgreSQL copy provisions
its own container on the key's port); after a tests/d list in the same process the PostgreSQL
half skips visibly, as L2's does.
"""
from __future__ import annotations

import os

import pytest

from tests.ap01 import mutants as mutation_list
from tests.d import pgharness

MEMORY, PG = mutation_list.MUTANTS, mutation_list.PG_MUTANTS
ALL = MEMORY + PG
CASES = mutation_list.case_names()
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
SUBSET = ("origin_unchecked",)
SELECTED = MEMORY if FULL_RUN else tuple(m for m in MEMORY if m.name in SUBSET)
SELECTED_PG = PG if FULL_RUN else ()
Mutant, Outcome = mutation_list.Mutant, mutation_list.Outcome


def test_the_list_is_well_formed():
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    assert set(SUBSET) <= {m.name for m in MEMORY}
    for mutant in ALL:
        assert mutant.cases and mutant.invariant and mutant.new != mutant.old, mutant.name
        assert not mutant.dies_by, f"{mutant.name}: every death is an assertion"
        for case in mutant.cases:
            assert case in CASES, f"{mutant.name} names unknown case {case}"
        source = (mutation_list.API_DIR / "infrx" / mutant.file).read_text()
        assert source.count(mutant.old) == mutant.occurrences, mutant.name


def test_every_case_is_covered_by_a_mutant():
    covered = {case for mutant in ALL for case in mutant.cases}
    assert CASES - covered == set(), f"cases no mutant can break: {sorted(CASES - covered)}"


def test_each_list_names_only_the_cases_its_world_runs():
    """The in-memory list never names the PostgreSQL-only case, and the PostgreSQL list never
    names a case that runs in memory only."""
    assert not any(set(m.cases) & set(mutation_list.PG_ONLY) for m in MEMORY)
    assert not any(set(m.cases) & set(mutation_list.FAKE_ONLY) for m in PG)


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome}: {result.detail}"


@pytest.mark.parametrize("mutant", SELECTED_PG, ids=[m.name for m in SELECTED_PG])
def test_pg_mutant_is_killed(mutant):
    """On the D harness's PostgreSQL of `INFRX_D_TASK` (visible skip without Docker)."""
    if pgharness._lock_fd is not None:
        # ponytail: skip, not share - the copy's harness cannot join this process's.
        pytest.skip("this process already holds the D harness lock (an earlier tests/d list "
                    "started it), so the copy's harness would be refused: run this list in its "
                    "own process")
    reason = pgharness.unavailable()
    if reason:
        pytest.skip(f"PostgreSQL harness unavailable: {reason}")
    result = mutation_list.run_mutant(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome}: {result.detail}"


A = "console/__init__.py"
SELF_TESTS = (
    (Outcome.survived, Mutant("self_no_op", "a comment changes nothing", A,
                              "class EnvelopeRoute(APIRoute):",
                              "class EnvelopeRoute(APIRoute):  # no-op",
                              (mutation_list.CSRF,))),
    (Outcome.broken_runner, Mutant("self_syntax", "a broken copy is not a kill", A,
                                   "class EnvelopeRoute(APIRoute):",
                                   "class EnvelopeRoute(APIRoute)::", (mutation_list.CSRF,))),
    (Outcome.misdeclared, Mutant("self_missing_anchor", "the list matches the code", A,
                                 "not in the module", "x", (mutation_list.CSRF,))),
)


@pytest.mark.parametrize("expected,mutant", SELF_TESTS, ids=[m.name for _, m in SELF_TESTS])
def test_the_runner_cannot_report_a_false_kill(expected, mutant):
    assert mutation_list.run_mutant(mutant).outcome == expected
