#!/usr/bin/env python3
"""R32/R40/R83: every invariant `tests/g/jobs` claims is killed by a named single edit.

The runner is the shared one with G6B's stricter `require_every_case`; the self-tests pin
that the delegation kept its honesty: a no-op survives, a syntax error is `broken_runner`, a
blind named case is `misdeclared`, and a missing anchor is `misdeclared`.

    uv run --frozen pytest -q tests/g/jobs/test_jobs_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/jobs/test_jobs_mutants.py   # all
"""
from __future__ import annotations

import os

import pytest

from . import jobs_mutants as mutation_list

ALL = mutation_list.MUTANTS
CASES = mutation_list.case_names()
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
# One per item (the whole list runs with INFRX_MUTANTS=all).
SUBSET = ("replay_readmits", "handle_grammar_unchecked", "result_ttl_on_gateway_clock",
          "observer_disconnect_cancels", "delete_unshielded", "expired_job_served_as_running",
          "poll_ignores_retry_after", "second_jobs_route_tolerated")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)
Mutant, Outcome = mutation_list.Mutant, mutation_list.Outcome
J = mutation_list.J


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


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, f"{mutant.name} is {result.outcome}: {result.detail}"


ACCEPT = mutation_list.ACCEPT
SELF_TESTS = (
    (Outcome.survived, Mutant("self_no_op", "a comment changes nothing", J,
                              "POLL_AFTER_S = 2", "POLL_AFTER_S = 2  # no-op", (ACCEPT,))),
    (Outcome.broken_runner, Mutant("self_syntax", "a broken copy is not a kill", J,
                                   "POLL_AFTER_S = 2", "POLL_AFTER_S = = 2", (ACCEPT,))),
    (Outcome.misdeclared, Mutant("self_one_case_blind", "every named case must notice", J,
                                 "wire.HEADER_RETRY_AFTER: str(POLL_AFTER_S)})",
                                 "wire.HEADER_RETRY_AFTER: str(POLL_AFTER_S + 1)})",
                                 (ACCEPT, mutation_list.CHANGED))),
    (Outcome.misdeclared, Mutant("self_missing_anchor", "the list matches the code", J,
                                 "not in the module", "x", (ACCEPT,))),
)


@pytest.mark.parametrize("expected,mutant", SELF_TESTS, ids=[m.name for _, m in SELF_TESTS])
def test_the_runner_cannot_report_a_false_kill(expected, mutant):
    assert mutation_list.run_mutant(mutant).outcome == expected
