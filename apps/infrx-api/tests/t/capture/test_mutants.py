#!/usr/bin/env python3
"""R32/R40/R83: every decision `tests/t/capture` claims is killed by a named single edit.

    uv run --frozen pytest -q tests/t/capture/test_mutants.py                          # subset
    INFRX_D_TASK=t2f INFRX_MUTANTS=all uv run --frozen pytest -q tests/t/capture/test_mutants.py
"""
from __future__ import annotations

import os

import pytest

from . import mutants as mutation_list

ALL, PG = mutation_list.MUTANTS, mutation_list.PG_MUTANTS
CASES = mutation_list.case_names()
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
SUBSET = ("consent_key_ignored", "consent_max_not_min", "ingress_ignores_the_capture_policy")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)
Mutant, Outcome = mutation_list.Mutant, mutation_list.Outcome


def test_the_list_is_well_formed():
    names = [m.name for m in ALL + PG]
    assert len(set(names)) == len(names), "duplicate mutant names"
    assert set(SUBSET) <= {m.name for m in ALL}
    checks = set(mutation_list.pg_checks())
    for mutant in ALL + PG:
        assert mutant.cases and mutant.invariant and mutant.new != mutant.old, mutant.name
        for case in mutant.cases:
            assert case in (checks if mutant in PG else CASES), \
                f"{mutant.name} names unknown case {case}"


def test_every_case_is_covered_by_a_mutant():
    covered = {case for mutant in ALL + PG for case in mutant.cases}
    every = CASES | set(mutation_list.pg_checks())
    assert every - covered == set(), f"cases no mutant can break: {sorted(every - covered)}"


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome}: {result.detail}"


@pytest.mark.parametrize("mutant", PG if FULL_RUN else (), ids=[m.name for m in PG] if FULL_RUN
                         else [])
def test_pg_mutant_is_killed_in_process(mutant):
    from tests.d import pgharness
    reason = pgharness.unavailable()
    if reason:
        pytest.skip(f"task-local PostgreSQL unavailable: {reason}")
    result = mutation_list.kill_in_process(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome}: {result.detail}"


ANCHOR = "CONSENT_TTL_S = 60.0"
SELF_TESTS = (
    (Outcome.survived, Mutant("self_no_op", "a comment changes nothing", mutation_list.C,
                              ANCHOR, ANCHOR + "  # no-op", (mutation_list.TTL,))),
    (Outcome.broken_runner, Mutant("self_syntax", "a broken copy is not a kill",
                                   mutation_list.C, ANCHOR, "CONSENT_TTL_S = = 1",
                                   (mutation_list.TTL,))),
    (Outcome.misdeclared, Mutant("self_missing_anchor", "the list matches the code",
                                 mutation_list.C, "not in the module", "x",
                                 (mutation_list.TTL,))),
)


@pytest.mark.parametrize("expected,mutant", SELF_TESTS, ids=[m.name for _, m in SELF_TESTS])
def test_the_runner_cannot_report_a_false_kill(expected, mutant):
    assert mutation_list.run_mutant(mutant).outcome == expected
