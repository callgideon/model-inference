#!/usr/bin/env python3
"""r1 R32: run track J's mutation list.

The list is slow by contract (one pytest process per mutant), so the default suite
runs the subset covering the five invariants the task brief names and the whole list
runs on demand:

    uv run --frozen pytest -q tests/j/test_mutants.py                     # subset
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/j/test_mutants.py   # all
"""
from __future__ import annotations

import os
import pathlib
import re

import pytest

from . import mutants as mutation_list

ALL = mutation_list.MUTANTS
FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
# The five invariants the brief names, one mutant each.
SUBSET = ("consent_from_the_snapshot_only", "stratum_bound_ignored", "score_range_unchecked",
          "dedupe_key_drops_the_rubric_version", "limited_flag_lost")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)

SUITE_DIR = pathlib.Path(__file__).resolve().parent
_DEF = re.compile(r"^def (test_\w+)", re.MULTILINE)
CASE_NAMES = {name for path in SUITE_DIR.glob("test_*.py") if path.name != "test_mutants.py"
              for name in _DEF.findall(path.read_text())}


def test_the_list_is_well_formed():
    """A typo in a case name would make a mutant unkillable by construction, and a
    mutant that names no case would pass by saying nothing."""
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    for mutant in ALL:
        assert mutant.cases, f"{mutant.name} names no case"
        assert mutant.invariant, f"{mutant.name} states no invariant"
        assert mutant.old != mutant.new, f"{mutant.name} changes nothing"
        for case in mutant.cases:
            assert case in CASE_NAMES, f"{mutant.name} names unknown case {case}"
    assert set(SUBSET) <= {m.name for m in ALL}


def test_the_required_invariants_each_have_a_mutant():
    """The brief's five: consent check, stratification bounds, score range, dedupe key,
    limited flag. Named here so a later edit cannot quietly drop one."""
    required = {
        "consent check": ("consent_check_skipped", "consent_from_the_snapshot_only"),
        "stratification bounds": ("stratum_bound_ignored", "design_collapses_to_one_bound"),
        "score range": ("score_range_unchecked", "boolean_score_accepted"),
        "dedupe key": ("dedupe_key_drops_the_rubric_version",),
        "limited flag": ("limited_flag_lost", "limited_result_not_marked",
                         "missing_criterion_counts_as_a_pass"),
    }
    declared = {m.name for m in ALL}
    for invariant, names in required.items():
        assert set(names) <= declared, invariant


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The cases {list(mutant.cases)} do not prove "
                           f"what they claim.")


# --- the runner's own honesty ------------------------------------------------------
# A runner that counted a syntax error as a kill would let the whole list pass while
# proving nothing, so each non-kill outcome is exercised deliberately.
SELF_TESTS = (
    ("a_no_op_edit_survives", mutation_list.Outcome.survived,
     mutation_list.Mutant(name="self_no_op", invariant="an edit that changes nothing survives",
                          file="judge/sampling.py", old="DEFAULT_DESIGN = CalibrationDesign()",
                          new="DEFAULT_DESIGN = CalibrationDesign()  # a comment",
                          cases=("test_the_design_is_25_uniform_15_failures_10_feedback",))),
    ("a_syntax_error_is_not_a_kill", mutation_list.Outcome.broken_runner,
     mutation_list.Mutant(name="self_syntax", invariant="the runner rejects a broken copy",
                          file="judge/sampling.py", old="def select(", new="def select(()) :::",
                          cases=("test_the_design_is_25_uniform_15_failures_10_feedback",))),
    # A real defect whose named case cannot see it is a *survivor*: the kill has to come
    # from the case that claims the invariant, not from anywhere in the suite.
    ("a_lethal_edit_under_the_wrong_case_name_is_not_a_kill", mutation_list.Outcome.survived,
     mutation_list.Mutant(name="self_wrong_case", invariant="a kill comes from the named case",
                          file="judge/rubric.py",
                          old="return (result.run_id, result.sample_id, result.rubric_version)",
                          new="return (result.run_id, result.sample_id)",
                          cases=("test_a_sample_without_media_is_marked_limited",))),
    ("a_missing_anchor_is_a_failure", mutation_list.Outcome.misdeclared,
     mutation_list.Mutant(name="self_missing_anchor", invariant="the list matches the code",
                          file="judge/cost.py", old="this text is not in the module",
                          new="nor is this",
                          cases=("test_the_shipped_rate_table_is_empty_and_says_why",))),
    ("a_mutant_with_no_case_is_a_failure", mutation_list.Outcome.misdeclared,
     mutation_list.Mutant(name="self_no_case", invariant="every mutant names a case",
                          file="judge/cost.py", old="MAX", new="MIN", cases=())),
)


@pytest.mark.parametrize("name,expected,mutant", SELF_TESTS, ids=[t[0] for t in SELF_TESTS])
def test_the_runner_cannot_report_a_false_kill(name, expected, mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.outcome is expected, f"{name}: got {result.outcome} - {result.detail}"
    assert result.killed is (expected is mutation_list.Outcome.killed)
