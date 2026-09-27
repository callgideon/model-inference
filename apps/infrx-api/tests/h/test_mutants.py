#!/usr/bin/env python3
"""R32/R83: every decision `tests/h` claims is killed by a named single edit.

    uv run --frozen pytest -q tests/h/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/h/test_mutants.py   # all
    INFRX_MUTANTS=all INFRX_D_TASK=l2 uv run --frozen pytest -q tests/h/test_mutants.py  # + pg

Run the PostgreSQL list in a process that has not itself started the D harness.
"""
from __future__ import annotations

import os

import pytest

from tests.d import pgharness

from . import mutants as mutation_list

MEMORY, PG = mutation_list.MUTANTS, mutation_list.PG_MUTANTS
ALL = MEMORY + PG
CASES = mutation_list.case_names()
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
SUBSET = ("h1_prod_endpoint_allowed", "h1_mutating_tool_replayed", "h1_bytes_unbounded",
          "h1_mid_run_edit_accepted", "h1_bind_skips_the_rights_port",
          "h1_replay_on_another_providers_deployment", "h1_bind_forwards_no_principal")
SELECTED = MEMORY if FULL_RUN else tuple(m for m in MEMORY if m.name in SUBSET)
SELECTED_PG = PG if FULL_RUN else ()
Mutant, Outcome = mutation_list.Mutant, mutation_list.Outcome


def test_the_list_is_well_formed():
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    assert set(SUBSET) <= {m.name for m in ALL}
    for mutant in ALL:
        assert mutant.cases and mutant.invariant and mutant.new != mutant.old, mutant.name
        for case in mutant.cases:
            assert case in CASES, f"{mutant.name} names unknown case {case}"


def test_every_case_is_covered_by_a_mutant():
    covered = {case for mutant in ALL for case in mutant.cases}
    assert CASES - covered == set(), f"cases no mutant can break: {sorted(CASES - covered)}"


def test_each_list_names_only_the_cases_its_world_runs():
    assert not any(mutation_list.REAL in m.cases for m in MEMORY)
    assert all(m.cases == (mutation_list.REAL,) for m in PG)


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome}: {result.detail}"


@pytest.mark.parametrize("mutant", SELECTED_PG, ids=[m.name for m in SELECTED_PG])
def test_pg_mutant_is_killed(mutant):
    """Through the real L2 port (visible skip without Docker)."""
    reason = pgharness.unavailable()
    if reason:
        pytest.skip(f"PostgreSQL harness unavailable: {reason}")
    result = mutation_list.run_mutant(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome}: {result.detail}"


P, DEV = mutation_list.P, mutation_list.DEV
SELF_TESTS = (
    (Outcome.survived, Mutant("self_no_op", "a comment changes nothing", P,
                              "_MISSING = object()", "_MISSING = object()  # no-op", (DEV,))),
    (Outcome.misdeclared, Mutant("self_missing_anchor", "the list matches the code", P,
                                 "not in the module", "x", (DEV,))),
    (Outcome.misdeclared, Mutant("self_one_case_blind", "every named case must notice", P,
                                 "if deployment.environment is not v2.Environment.dev:", "if False:",
                                 (DEV, mutation_list.COV))),
)


@pytest.mark.parametrize("expected,mutant", SELF_TESTS, ids=[m.name for _, m in SELF_TESTS])
def test_the_runner_cannot_report_a_false_kill(expected, mutant):
    assert mutation_list.run_mutant(mutant).outcome == expected
