#!/usr/bin/env python3
"""R32: run the Q mutation list. A surviving mutant is a failed suite.

One pytest process per mutant, so the whole list is slow by contract (it was 21 s of a
50 s `make api-test`). Gated exactly like the coordinator's list: the default suite runs
a three-mutant subset plus the runner's own self-tests, and the full list runs on demand.
A surviving mutant is a failed suite either way.

    uv run --frozen pytest -q tests/q/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/q/test_mutants.py   # all of them
    uv run --frozen python tests/q/mutants.py                             # the same list
"""
from __future__ import annotations

import os

import pytest

from . import mutants as mutation_list

ALL = mutation_list.MUTANTS
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
# One mutant per mechanism most likely to regress: the kind filter (R52, killed through
# the exported suite), the dispatch clamp, and the arrival tag whose mutant the r2 review
# found surviving. The whole list is one environment variable away.
SUBSET = ("the_kind_filter_is_inverted",
          "the_dispatch_start_is_not_clamped_to_the_virtual_time",
          "an_arriving_tenant_starts_at_zero")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)


def test_the_list_is_well_formed():
    """Every mutant names an invariant and at least one case, and every anchor exists
    exactly once. A duplicated anchor would mutate the wrong line and prove nothing."""
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    assert set(SUBSET) <= {m.name for m in ALL}, "the default subset names a missing mutant"
    print(f"\nQ mutants: {len(ALL)} declared, {len(SELECTED)} selected "
          f"({'INFRX_MUTANTS=all' if FULL_RUN else 'default subset'})")
    source = (mutation_list.API_DIR / "infrx" / mutation_list.Q).read_text()
    for mutant in ALL:
        assert mutant.cases, f"{mutant.name} names no case"
        assert mutant.invariant, f"{mutant.name} states no invariant"
        assert source.count(mutant.old) == 1, \
            f"{mutant.name}: anchor appears {source.count(mutant.old)} times"
        assert mutant.new != mutant.old, f"{mutant.name} changes nothing"


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The cases {list(mutant.cases)} do not prove "
                           f"what they claim.")


# --- the runner's own honesty -------------------------------------------------------
# A runner that counted a syntax error, or a change with no effect, as a kill would let
# every mutant above pass while proving nothing.
SELF_TESTS = (
    ("a_no_op_edit_survives", mutation_list.Outcome.survived,
     mutation_list.Mutant(name="self_no_op", invariant="an edit that changes nothing survives",
                          file=mutation_list.Q, old="MAX_INDEX_ITEMS = 500",
                          new="MAX_INDEX_ITEMS = 500  # a comment changes no behaviour",
                          cases=("test_q1_contract__the_adapter_satisfies_the_scheduler_protocol",))),
    ("a_syntax_error_is_not_a_kill", mutation_list.Outcome.broken_runner,
     mutation_list.Mutant(name="self_syntax_error", invariant="the runner rejects a broken copy",
                          file=mutation_list.Q, old="    async def enqueue(self",
                          new="    async def enqueue(self)) :::",
                          cases=("dur_outbox__enqueue_is_replay_safe",))),
    # A real defect (the byte cap stops binding) named against a case that never
    # enqueues anything: the kill has to come from the case that claims the invariant,
    # not from anywhere in the suite.
    ("a_defect_the_named_case_cannot_see_is_not_a_kill", mutation_list.Outcome.survived,
     mutation_list.Mutant(name="self_wrong_case", invariant="the kill comes from the named case",
                          file=mutation_list.Q,
                          old="        if self._bytes + size > self._max_bytes:",
                          new="        if False and self._bytes + size > self._max_bytes:",
                          cases=("test_q1_contract__the_adapter_satisfies_the_scheduler_protocol",))),
    # r2: a mutant may not claim coverage from a case that cannot see it. The item cap is
    # really broken here, the caps case notices and the protocol-shape case cannot, so the
    # declaration is wrong even though something failed.
    ("a_named_case_that_does_not_notice_is_a_failure", mutation_list.Outcome.misdeclared,
     mutation_list.Mutant(name="self_unproven_case",
                          invariant="every named case must notice the defect",
                          file=mutation_list.Q,
                          old="        if len(self._entries) + 1 > self._max_items:",
                          new="        if False and len(self._entries) + 1 > self._max_items:",
                          cases=("test_q1_caps__a_full_index_refuses_with_a_typed_retryable_error",
                                 "test_q1_contract__the_adapter_satisfies_the_scheduler_protocol"))),
    ("a_missing_anchor_is_a_failure", mutation_list.Outcome.misdeclared,
     mutation_list.Mutant(name="self_missing_anchor", invariant="the list matches the code",
                          file=mutation_list.Q, old="this text is not in the scheduler",
                          new="nor is this", cases=("dur_outbox__enqueue_is_replay_safe",))),
    ("a_mutant_with_no_case_is_a_failure", mutation_list.Outcome.misdeclared,
     mutation_list.Mutant(name="self_no_case", invariant="every mutant names a case",
                          file=mutation_list.Q, old="MAX_INDEX_ITEMS = 500",
                          new="MAX_INDEX_ITEMS = 1", cases=())),
)


@pytest.mark.parametrize("name,expected,mutant", SELF_TESTS, ids=[t[0] for t in SELF_TESTS])
def test_the_runner_cannot_report_a_false_kill(name, expected, mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.outcome is expected, f"{name}: got {result.outcome} - {result.detail}"
    assert result.ok is (expected is mutation_list.Outcome.killed)
