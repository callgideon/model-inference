#!/usr/bin/env python3
"""r1 R32: run track J's mutation list.

The list is slow by contract (one pytest process per mutant), so the default suite runs
the subset covering the invariants the task brief and rulings R56/R57 name, and the whole
list runs on demand:

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
# One mutant per invariant the brief names, plus one each for R56's sampling policy and
# R57's money guard - the two rulings this task was corrected against.
SUBSET = ("consent_from_the_snapshot_only", "stratum_bound_ignored", "score_range_unchecked",
          "dedupe_key_drops_the_rubric_version", "limited_flag_lost", "duplicates_sampled_twice",
          "feedback_org_not_checked", "guard_trusts_the_estimates_total",
          "rate_lookup_takes_the_first_row", "scan_bound_not_enforced",
          "consent_checked_after_the_read",
          # round-3: one per blocking item the second review raised
          "recursion_error_escapes_the_parse_guard", "worst_case_uses_the_ambient_context",
          "describe_echoes_a_string", "superseded_price_version_accepted",
          "dedupe_runs_before_the_tenant_filter", "a_malformed_row_is_interpreted")
SELECTED = ALL if FULL_RUN else tuple(m for m in ALL if m.name in SUBSET)

SUITE_DIR = pathlib.Path(__file__).resolve().parent
_DEF = re.compile(r"^def (test_\w+)", re.MULTILINE)
CASE_NAMES = {name for path in SUITE_DIR.glob("test_*.py") if path.name != "test_mutants.py"
              for name in _DEF.findall(path.read_text())}


def test_the_list_is_well_formed():
    """A typo in a case name would make a mutant unkillable by construction, and a mutant
    that names no case would pass by saying nothing."""
    assert len({m.name for m in ALL}) == len(ALL), "duplicate mutant names"
    for mutant in ALL:
        assert mutant.cases, f"{mutant.name} names no case"
        assert mutant.invariant, f"{mutant.name} states no invariant"
        assert mutant.old != mutant.new, f"{mutant.name} changes nothing"
        for case in mutant.cases:
            assert case in CASE_NAMES, f"{mutant.name} names unknown case {case}"
    assert set(SUBSET) <= {m.name for m in ALL}


def test_the_required_invariants_each_have_a_mutant():
    """The brief's five, plus every invariant rulings R56 and R57 add. Named here so a
    later edit cannot quietly drop one, and so "every invariant has a mutant" is a check
    rather than a claim in an evidence report."""
    required = {
        # the brief's five
        "consent check": ("consent_check_skipped", "consent_from_the_snapshot_only"),
        "stratification bounds": ("stratum_bound_ignored", "design_collapses_to_one_bound"),
        "score range": ("score_range_unchecked", "boolean_score_accepted"),
        "dedupe key": ("dedupe_key_drops_the_rubric_version",),
        "limited flag": ("limited_flag_lost", "limited_result_not_marked",
                         "missing_criterion_counts_as_a_pass"),
        # R56
        "consent is not retroactive": ("consent_window_ignored", "consent_window_start_ignored",
                                       "consent_window_end_ignored"),
        "strata from raw facts": ("schema_invalid_is_not_a_failure", "truncation_is_not_a_failure",
                                  "no_output_is_graded_anyway"),
        "candidate deduplication": ("duplicates_sampled_twice", "conflicting_duplicate_is_kept",
                                    "identical_duplicates_dropped_silently"),
        "feedback belongs to the candidate": ("feedback_org_not_checked",
                                             "feedback_request_not_checked"),
        "by_operator is neither": ("calibration_keys_on_by_operator",
                                   "platform_entry_counts_as_customer_signal"),
        "no media, no pass, in code": ("media_criterion_passes_without_media",
                                       "scores_record_allows_a_no_media_pass"),
        "the scan bound": ("scan_bound_not_enforced", "scan_bound_range_unchecked",
                           "scan_bound_accepts_a_boolean"),
        # R57
        "the guard recomputes": ("guard_trusts_the_estimates_total", "stale_estimate_accepted",
                                 "priced_from_per_sample_only"),
        "the mode is exact": ("mode_compared_loosely",),
        "the budget boundary": ("over_budget_can_submit", "budget_boundary_exclusive"),
        "the effective rate": ("rate_lookup_takes_the_first_row", "rate_lookup_ignores_the_clock",
                               "duplicate_rate_rows_accepted", "a_zero_or_negative_rate_accepted"),
        # B5: dryrun.py had no mutant at all
        "the refusal ordering": ("consent_checked_after_the_read",),
        "the JudgeRun projection": ("judge_run_is_not_a_dry_run", "judge_run_reserves_money"),
        "no provider SDK": ("provider_sdk_imported_on_the_dry_run_path",),
        # round 3
        "R2-B1 the parse guard": ("recursion_error_escapes_the_parse_guard",
                                  "lone_surrogate_accepted", "payload_type_checked_loosely",
                                  "score_type_checked_loosely",
                                  "rationale_type_checked_loosely"),
        "R2-B2 the explicit context": ("worst_case_uses_the_ambient_context",
                                       "worst_case_takes_any_ceilings"),
        "R2-B3 no payload echo": ("describe_echoes_a_string", "describe_echoes_an_integer",
                                  "unexpected_key_echoed", "duplicate_key_echoed",
                                  "json_error_echoes_the_document", "detail_cap_overshoots"),
        "R2-B4 the five survivors": ("superseded_price_version_accepted",
                                     "priced_without_a_version",
                                     "consent_window_end_is_inclusive",
                                     "the_plan_prices_at_the_lookback_start",
                                     "the_guard_is_asked_at_the_lookback_start"),
        "R2 the tenant filter first": ("dedupe_runs_before_the_tenant_filter",
                                       "dedupe_on_the_raw_request_id",
                                       "canonical_id_keeps_the_case",
                                       "a_respelled_duplicate_is_a_conflict"),
        "R2 one bad row": ("a_malformed_row_is_interpreted", "a_future_row_is_a_candidate"),
        "R2 fail closed": ("the_predicate_reraises", "ledger_raises_on_an_unusable_sample_id"),
    }
    declared = {m.name for m in ALL}
    for invariant, names in required.items():
        assert set(names) <= declared, invariant


def test_every_judge_module_is_covered():
    """dryrun.py shipped with no mutant at all, so "every invariant has one" was false for
    a whole module. One file with no mutant is one file nothing proves."""
    modules = {path.name for path in (SUITE_DIR.parents[1] / "infrx" / "judge").glob("*.py")
               if path.name != "__init__.py"}
    covered = {pathlib.Path(m.file).name for m in ALL}
    assert modules <= covered, f"no mutant touches {sorted(modules - covered)}"


@pytest.mark.parametrize("mutant", SELECTED, ids=[m.name for m in SELECTED])
def test_mutant_is_killed(mutant):
    result = mutation_list.run_mutant(mutant)
    assert result.killed, (f"{mutant.name} is {result.outcome} ({mutant.invariant}): "
                           f"{result.detail}. The cases {list(mutant.cases)} do not prove "
                           f"what they claim.")


# --- the runner's own honesty ------------------------------------------------------
# A runner that counted a syntax error, or a test that crashed, as a kill would let the
# whole list pass while proving nothing, so each non-kill outcome is exercised here.
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
    # The new one: a test that *crashed* is not a test that noticed. This is the declared
    # `brief_lets_an_unprintable_value_raise` mutant with its declaration removed, so the
    # only difference between a kill and a runner error is the declaration.
    ("an_undeclared_exception_death_is_not_a_kill", mutation_list.Outcome.broken_runner,
     mutation_list.Mutant(name="self_crash", invariant="a kill is assertion-shaped",
                          file="judge/rubric.py",
                          old='        return "<undescribable>"',
                          new="        raise",
                          cases=("test_describe_reports_what_a_value_is_never_what_it_says",))),
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


def test_the_same_defect_is_a_kill_once_its_exception_is_declared():
    """The other half of the classification: an invariant whose honest kill *is* an
    exception (`validate_output` never raises) declares it, and then the *same edit* is a
    kill rather than a runner error - the self-test above runs it undeclared."""
    declared = next(m for m in ALL if m.name == "describe_lets_an_undescribable_value_raise")
    assert declared.dies_by == ("RuntimeError",)
    assert declared.old == SELF_TESTS[3][2].old and declared.new == SELF_TESTS[3][2].new
    assert mutation_list.run_mutant(declared).killed


def test_the_death_classifier_reads_each_shape():
    """Unit-level, because the classifier is what the honesty above rests on."""
    kinds = mutation_list._death_kinds(
        "/x/test_a.py:1: assert 1 == 2\n"
        "/x/test_b.py:2: TypeError: boom\n"
        "/x/test_c.py:5: Failed: DID NOT RAISE <class 'ValueError'>\n"
        "FAILED /x/test_a.py::test_a\n")
    assert kinds == ["AssertionError", "TypeError", "Failed"]
