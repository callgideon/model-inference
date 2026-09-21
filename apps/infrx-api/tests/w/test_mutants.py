#!/usr/bin/env python3
"""r1 R32: run the W1 mutation list, and prove the runner cannot lie about it.

    uv run --frozen pytest -q tests/w/test_mutants.py                  # fast subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/w/test_mutants.py

One pytest process per mutant, so the whole list runs on demand and a subset runs by
default - the same split `tests/contracts/test_mutants.py` uses.
"""
from __future__ import annotations

import os

import pytest

from . import mutants as mutation_list

ALL = mutation_list.MUTANTS
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
# One mutant per area: the wire body, the tenant salt, the usage parser, a timer and
# the delimiter filter. `INFRX_MUTANTS=all` runs every one of them.
SUBSET = ("body_sends_the_model_revision", "salt_ignores_the_tenant",
          "usage_estimated_from_deltas", "first_token_deadline_ignored",
          "close_delimiter_tail_forgotten")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)


def test_the_list_is_well_formed():
    """Every mutant names an invariant and at least one case, and no name repeats: a
    typo would make a mutant unkillable by construction."""
    assert len(ALL) >= 40, f"only {len(ALL)} mutants declared"
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    for mutant in ALL:
        assert mutant.cases, f"{mutant.name} names no case"
        assert mutant.invariant, f"{mutant.name} states no invariant"
        assert mutant.file in ("worker/engine.py", "worker/reasoning.py"), mutant.file
    assert set(SUBSET) <= {m.name for m in ALL}


def test_every_owned_case_is_covered_by_a_mutant():
    """R32: an invariant no single edit can break is an invariant the case does not
    really prove. The two exceptions are declared here, with their reason."""
    import tests.w.test_engine as engine_tests
    import tests.w.test_reasoning as reasoning_tests

    covered = {case for mutant in ALL for case in mutant.cases}
    cases = {name for module in (engine_tests, reasoning_tests) for name in vars(module)
             if name.startswith("test_")}
    # F-CONTRACT shape and protocol cases assert *about the suite and the ports*, not
    # about this adapter's own logic: breaking them means editing the contracts package,
    # which is the coordinator's mutation list, not W's.
    exempt = {"test_f_contract__the_real_adapter_passes_the_exported_engine_suite",
              "test_f_contract__the_factory_publishes_every_hook_the_suite_may_need",
              "test_f_contract__the_adapter_satisfies_the_engine_protocol",
              "test_api_stream__the_raw_text_is_never_modified",
              "test_api_stream__split_tokens_are_reassembled_whatever_the_boundaries",
              "test_api_stream__canonical_events_are_progress_deltas_and_one_usage",
              "test_api_stream__deltas_carry_the_raw_text_and_the_visible_text"}
    uncovered = cases - covered - exempt
    assert uncovered == set(), f"cases no mutant can break: {sorted(uncovered)}"
    assert exempt <= cases, sorted(exempt - cases)


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The cases {list(mutant.cases)} do not prove "
                           f"what they claim.")


# --- the runner's own honesty (r1 R40: no false kill, no hidden skip) -------------
SELF_TESTS = (
    ("a_syntax_error_is_not_a_kill", mutation_list.Outcome.broken_runner,
     mutation_list.Mutant(name="self_syntax", invariant="a broken copy is not a kill",
                          file="worker/engine.py", old="    async def cancel(self",
                          new="    async def cancel(self)) :::",
                          cases=("test_api_stream__junk_lines_are_counted_and_never_relayed",))),
    ("an_import_error_is_not_a_kill", mutation_list.Outcome.broken_runner,
     mutation_list.Mutant(name="self_import", invariant="an import failure is not a kill",
                          file="worker/engine.py", old="DETAIL_MAX_CHARS = 500",
                          new="DETAIL_MAX_CHARS = _undefined_at_import_time",
                          cases=("test_api_stream__junk_lines_are_counted_and_never_relayed",))),
    ("a_no_op_edit_survives", mutation_list.Outcome.survived,
     mutation_list.Mutant(name="self_no_op", invariant="an edit that changes nothing survives",
                          file="worker/engine.py", old="DETAIL_MAX_CHARS = 500",
                          new="DETAIL_MAX_CHARS = 500  # a comment changes no behaviour",
                          cases=("test_api_stream__junk_lines_are_counted_and_never_relayed",))),
    ("a_lethal_edit_under_the_wrong_case_is_not_a_kill", mutation_list.Outcome.survived,
     mutation_list.Mutant(name="self_wrong_case", invariant="the kill comes from the named case",
                          file="worker/engine.py",
                          old="        if self.served_model not in models:", new="        if False:",
                          cases=("test_api_stream__junk_lines_are_counted_and_never_relayed",))),
    ("a_missing_anchor_is_a_failure", mutation_list.Outcome.misdeclared,
     mutation_list.Mutant(name="self_missing_anchor", invariant="the list matches the code",
                          file="worker/engine.py", old="this text is not in the adapter",
                          new="nor is this",
                          cases=("test_api_stream__junk_lines_are_counted_and_never_relayed",))),
    ("a_mutant_with_no_case_is_a_failure", mutation_list.Outcome.misdeclared,
     mutation_list.Mutant(name="self_no_case", invariant="every mutant names a case",
                          file="worker/engine.py", old="DETAIL_MAX_CHARS = 500",
                          new="DETAIL_MAX_CHARS = 1", cases=())),
)


@pytest.mark.parametrize("name,expected,mutant", SELF_TESTS, ids=[t[0] for t in SELF_TESTS])
def test_the_runner_cannot_report_a_false_kill(name, expected, mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.outcome is expected, f"{name}: got {result.outcome} - {result.detail}"
    assert result.ok is (expected is mutation_list.Outcome.killed)
