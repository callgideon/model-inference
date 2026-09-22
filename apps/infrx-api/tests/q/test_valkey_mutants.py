#!/usr/bin/env python3
"""R32: run the Q2 mutation list. A surviving mutant is a failed suite.

Gated exactly like Q1's list: the default suite runs a small subset plus the two
self-tests that the *Q2 wiring* cannot report a false kill, and the full list runs on
demand. The runner's own honesty (a syntax error is not a kill, every named case must
notice) is pinned once, by `tests/q/test_mutants.py`, because this module imports the
same `run_mutant`.

    uv run --frozen pytest -q tests/q/test_valkey_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/q/test_valkey_mutants.py   # all
    uv run --frozen python tests/q/valkey_mutants.py                             # the same
"""
from __future__ import annotations

import os

import pytest

from . import valkey_mutants as mutation_list
from . import vkharness

_reason = vkharness.unavailable()
pytestmark = pytest.mark.skipif(_reason is not None,
                                reason=f"task-local Valkey unavailable: {_reason}")

ALL = mutation_list.MUTANTS
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
# One mutant per mechanism this port could plausibly regress: the score precision that
# makes the two adapters bit-identical, the R60 level-1 charge, and the atomic claim's
# rule that nothing moves before the candidate is priced.
SUBSET = ("the_scores_are_formatted_with_lua_default_precision",
          "the_kind_tag_does_not_advance",
          "the_probe_commits_before_the_cost_is_validated")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)


def test_the_list_is_well_formed():
    """Every mutant names an invariant and at least one case, and every anchor exists
    exactly once - two identical lines would mutate the wrong one and prove nothing."""
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    assert set(SUBSET) <= {m.name for m in ALL}, "the default subset names a missing mutant"
    assert len(ALL) >= 30, f"the Q2 brief asks for at least 30 mutants, not {len(ALL)}"
    print(f"\nQ2 mutants: {len(ALL)} declared, {len(SELECTED)} selected "
          f"({'INFRX_MUTANTS=all' if FULL_RUN else 'default subset'})")
    source = (mutation_list.API_DIR / "infrx" / mutation_list.VALKEY).read_text()
    for mutant in ALL:
        assert mutant.cases, f"{mutant.name} names no case"
        assert mutant.invariant, f"{mutant.name} states no invariant"
        assert source.count(mutant.old) == 1, \
            f"{mutant.name}: anchor appears {source.count(mutant.old)} times"
        assert mutant.new != mutant.old, f"{mutant.name} changes nothing"


def test_every_r60_clause_carries_a_mutant():
    """R60 has five clauses with state behind them (the kind tag's charge, the clamp, the
    top-level virtual time, the tie-break on the pick's sequence, and level-1 being
    untouched by a filtered claim) plus the withdrawn cross-kind comparison. Each one is
    named here, so a list that quietly lost one fails instead of shrinking."""
    required = {"the_kind_tag_does_not_advance",
                "the_kind_tag_advances_by_one_not_by_the_cost",
                "the_kind_tag_is_weighted",
                "the_kind_choice_takes_the_largest_kind_tag",
                "the_kinds_are_ranked_by_their_tenants_tags",
                "the_kind_tie_breaks_on_list_order",
                "the_kind_tie_breaks_on_the_kind_name",
                "the_kind_is_ranked_by_its_oldest_candidate",
                "a_kind_with_no_eligible_pick_is_charged",
                "the_kind_start_is_not_clamped_to_the_top_virtual_time",
                "the_top_virtual_time_is_the_unclamped_kind_tag",
                "the_top_virtual_time_never_advances",
                "a_filtered_claim_moves_the_kind_state",
                "an_unfiltered_claim_serves_one_kind_only",
                "rebuild_keeps_the_fairness_epoch"}
    missing = required - {m.name for m in ALL}
    assert missing == set(), f"R60 clauses with no mutant: {sorted(missing)}"


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant, paths=mutation_list.PATHS)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The cases {list(mutant.cases)} do not prove "
                           f"what they claim.")


# --- the Q2 wiring's own honesty ----------------------------------------------------
SELF_TESTS = (
    ("a_no_op_edit_survives", mutation_list.Outcome.survived,
     mutation_list.Mutant(name="self_no_op", invariant="an edit that changes nothing survives",
                          file=mutation_list.VALKEY, old="_CLAIM_ATTEMPTS = 16",
                          new="_CLAIM_ATTEMPTS = 16  # a comment changes no behaviour",
                          cases=("test_q2_contract__the_adapter_satisfies_the_scheduler_protocol",))),
    # A real defect in the Lua (the byte cap stops binding) named against a case that
    # never fills an index: the kill has to come from the case that claims the invariant.
    ("a_defect_the_named_case_cannot_see_is_not_a_kill", mutation_list.Outcome.survived,
     mutation_list.Mutant(name="self_wrong_case", invariant="the kill comes from the named case",
                          file=mutation_list.VALKEY,
                          old="if bytes + size > max_bytes then return -2 end",
                          new="if false then return -2 end",
                          cases=("test_q2_contract__the_adapter_satisfies_the_scheduler_protocol",))),
)


@pytest.mark.parametrize("name,expected,mutant", SELF_TESTS, ids=[t[0] for t in SELF_TESTS])
def test_the_runner_cannot_report_a_false_kill(name, expected, mutant):
    result = mutation_list.run_mutant(mutant, paths=mutation_list.PATHS)
    assert result.outcome is expected, f"{name}: got {result.outcome} - {result.detail}"
    assert result.ok is (expected is mutation_list.Outcome.killed)
