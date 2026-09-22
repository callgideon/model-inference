#!/usr/bin/env python3
"""R32/R40: every invariant `tests/g/ops` claims is killed by a named single edit.

The runner's own honesty (a syntax error is not a kill, a no-op survives, every named
case must notice) is pinned by the self-tests below, since this runner is G6B's own.

    uv run --frozen pytest -q tests/g/ops/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/ops/test_mutants.py   # all
"""
from __future__ import annotations

import os

import pytest

from . import mutants as mutation_list

ALL = mutation_list.MUTANTS
CASES = mutation_list.case_names()
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
# One per mechanism a secret, an identity or money could leak through.
SUBSET = ("operator_audience_unchecked", "revoked_key_accepted", "secret_revealed_on_replay",
          "operation_id_not_deterministic", "wallet_binding_unchecked", "cli_prints_the_secret")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)
Mutant, Outcome = mutation_list.Mutant, mutation_list.Outcome
S = mutation_list.S


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


SELF_TESTS = (
    (Outcome.survived, Mutant("self_no_op", "a comment changes nothing", S,
                              "MAX_REASON = 500", "MAX_REASON = 500  # no-op",
                              ("test_api_ops__every_write_needs_a_reason_and_an_idempotency_key",))),
    (Outcome.broken_runner, Mutant("self_syntax", "a broken copy is not a kill", S,
                                   "MAX_REASON = 500", "MAX_REASON = = 500",
                                   ("test_api_ops__every_write_needs_a_reason_and_an_idempotency_key",))),
    (Outcome.misdeclared, Mutant("self_one_case_blind", "every named case must notice", S,
                                 "        if not reason.strip() or len(reason) > MAX_REASON:",
                                 "        if len(reason) > MAX_REASON:",
                                 ("test_api_ops__every_write_needs_a_reason_and_an_idempotency_key",
                                  "test_api_ops__the_secret_is_revealed_once"))),
    (Outcome.misdeclared, Mutant("self_missing_anchor", "the list matches the code", S,
                                 "not in the module", "x",
                                 ("test_api_ops__the_secret_is_revealed_once",))),
)


@pytest.mark.parametrize("expected,mutant", SELF_TESTS, ids=[m.name for _, m in SELF_TESTS])
def test_the_runner_cannot_report_a_false_kill(expected, mutant):
    assert mutation_list.run_mutant(mutant).outcome == expected
