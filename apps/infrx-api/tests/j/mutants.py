#!/usr/bin/env python3
"""r1 R32 for track J: every invariant `tests/j` claims must be killable.

Each entry below is a **single edit** to `infrx/judge/` that breaks one named invariant,
together with the cases that must fail because of it. The runner applies one mutant at a
time to a *copy* of the package in a temporary directory and runs the named cases there;
a survivor means the case asserting that invariant proves nothing, which is a failed
task, not a warning. Nothing is written inside the worktree.

A kill must also be **assertion-shaped**. A mutant that makes a test blow up with a
`TypeError` in the test body is a test that crashed, not a test that failed, and a runner
that counts it has stopped distinguishing "the suite noticed" from "the suite broke". So
an exception death is a `broken_runner` unless the mutant declares it in `dies_by` - which
two of them legitimately do, because the invariant they break *is* "this never raises".

    uv run --frozen pytest -q tests/j/test_mutants.py     # the whole list
    uv run --frozen python tests/j/mutants.py --list      # names only
    uv run --frozen python tests/j/mutants.py consent_from_the_snapshot_only
"""
from __future__ import annotations

import argparse
import enum
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

API_DIR = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = "infrx"
SUITE = "tests/j"
PYTEST_ALL_PASSED, PYTEST_TESTS_FAILED = 0, 1
#: How an honest kill dies: an assertion, or pytest's own `Failed` (a `pytest.raises`
#: block that did not raise). Anything else is an exception escaping into the test body.
ASSERTION_DEATHS = frozenset({"AssertionError", "Failed"})


class Outcome(enum.StrEnum):
    killed = "killed"
    survived = "survived"
    misdeclared = "misdeclared"
    broken_runner = "broken_runner"


@dataclass(frozen=True)
class Result:
    outcome: Outcome
    detail: str = ""

    @property
    def killed(self) -> bool:
        return self.outcome is Outcome.killed


@dataclass(frozen=True)
class Mutant:
    name: str
    invariant: str
    file: str
    old: str
    new: str
    cases: tuple[str, ...] = field(default_factory=tuple)
    #: Exception classes this mutant may legitimately die by, beyond an assertion.
    dies_by: tuple[str, ...] = ()

    @property
    def path(self) -> pathlib.Path:
        return pathlib.Path(PACKAGE) / self.file


def _m(name, invariant, file, old, new, *cases, dies_by=()) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases,
                  dies_by=tuple(dies_by))


S = "judge/sampling.py"
R = "judge/rubric.py"
C = "judge/cost.py"
D = "judge/dryrun.py"

MUTANTS: tuple[Mutant, ...] = (
    # --- consent: the gate, its currency, its tenancy and its window (R56) ----------
    _m("consent_check_skipped", "evaluation consent gates selection",
       S, '        raise errors.ConsentMissing(f"org {org_id} has no current evaluation consent")',
       "        pass",
       "test_trace_opt_in_is_not_evaluation_consent"),
    _m("consent_from_the_snapshot_only",
       "a trace opt-in is not evaluation consent, and the record must be current",
       S, "if not consent.allows_evaluation(now):", "if not consent.evaluation_consent:",
       "test_trace_opt_in_is_not_evaluation_consent"),
    _m("consent_of_any_org_accepted", "one org's consent never authorizes another's traces",
       S, '        raise errors.NotFound(f"consent for org {consent.org_id} does not match org {org_id}")',
       "        pass",
       "test_one_orgs_consent_never_authorizes_another_orgs_traces"),
    _m("foreign_trace_selected", "a candidate is checked against the caller's org",
       S, "    if candidate.org_id != org_id:\n        return Exclusion.not_owned",
       "    if False:\n        return Exclusion.not_owned",
       "test_another_tenants_trace_is_never_a_sample"),
    _m("consent_window_ignored", "consent is not retroactive (R56)",
       S, "    if not _within_consent_window(candidate, consent):",
       "    if False:",
       "test_evaluation_consent_is_not_retroactive"),
    _m("consent_window_start_ignored", "a trace older than the consent is not eligible",
       S, "    if candidate.started_at < consent.effective_at:\n        return False",
       "    if False:\n        return False",
       "test_evaluation_consent_is_not_retroactive"),
    _m("consent_window_end_ignored", "a trace after the revocation instant is not eligible",
       S, "    return consent.revoked_at is None or candidate.started_at < consent.revoked_at",
       "    return True",
       "test_evaluation_consent_is_not_retroactive"),

    # --- stratification bounds -------------------------------------------------------
    _m("stratum_bound_ignored", "each stratum is cut to its designed size",
       S, "chosen, overflow = ordered[:size], ordered[size:]",
       "chosen, overflow = ordered, []",
       "test_the_design_is_25_uniform_15_failures_10_feedback"),
    _m("design_collapses_to_one_bound", "the design is 25/15/10, not 50 of whatever came first",
       S, "size = design.size_of(stratum)", "size = design.total",
       "test_the_design_is_25_uniform_15_failures_10_feedback",
       "test_a_stratum_that_cannot_be_filled_is_reported_not_backfilled"),
    _m("failures_lose_their_priority", "a failing trace claims the failures stratum first",
       S, "    if candidate.failed:", "    if False:",
       "test_a_failing_trace_claims_the_failures_stratum_before_feedback",
       "test_the_design_is_25_uniform_15_failures_10_feedback",
       "test_the_failure_stratum_is_derived_from_the_raw_facts"),
    _m("shortfall_reported_as_filled", "an unfillable stratum is reported, never papered over",
       S, "shortfall[stratum] = size - len(chosen)", "shortfall[stratum] = 0",
       "test_a_stratum_that_cannot_be_filled_is_reported_not_backfilled"),

    # --- the failure stratum is derived from raw facts (R56) -------------------------
    _m("schema_invalid_is_not_a_failure", "an invalid structured output is a failure sample",
       S, "return self.finish_reason == FINISH_REASON_LENGTH or self.schema_valid is False",
       "return self.finish_reason == FINISH_REASON_LENGTH",
       "test_the_failure_stratum_is_derived_from_the_raw_facts"),
    _m("truncation_is_not_a_failure", "a truncated answer is a failure sample",
       S, "return self.finish_reason == FINISH_REASON_LENGTH or self.schema_valid is False",
       "return self.schema_valid is False",
       "test_the_failure_stratum_is_derived_from_the_raw_facts",
       "test_the_design_is_25_uniform_15_failures_10_feedback",
       "test_a_failing_trace_claims_the_failures_stratum_before_feedback"),
    _m("no_output_is_graded_anyway", "a 5xx has no answer to grade (R56)",
       S, "    if candidate.produced_no_output:", "    if False:",
       "test_a_request_that_produced_no_output_is_skipped",
       "test_unreadable_content_never_reaches_the_plan"),

    # --- determinism ----------------------------------------------------------------
    _m("selection_follows_the_scan_order", "the seed decides, not the scan order",
       S, "ordered = sorted(eligible[stratum], key=lambda c: (rank(seed, c.request_id), c.request_id))",
       "ordered = list(eligible[stratum])",
       "test_the_same_seed_picks_the_same_samples_whatever_the_scan_order"),
    _m("seed_ignored", "the seed is actually read",
       S, 'digest = hashlib.blake2b(f"{seed}\\x00{request_id}".encode(), digest_size=8).digest()',
       'digest = hashlib.blake2b(f"{request_id}".encode(), digest_size=8).digest()',
       "test_the_same_seed_picks_the_same_samples_whatever_the_scan_order"),

    # --- duplicates from the projection (R56) ----------------------------------------
    _m("duplicates_sampled_twice", "one row per request_id before stratifying (R56)",
       S, "unique, excluded = deduplicate(tuple(candidates))",
       "unique, excluded = list(candidates), []",
       "test_a_repeated_row_is_sampled_once",
       "test_a_conflicting_duplicate_is_excluded_rather_than_sampled_twice",
       "test_deduplication_accounts_for_every_row",
       "test_duplicate_rows_from_the_source_never_become_duplicate_samples"),
    _m("conflicting_duplicate_is_kept", "a conflicting duplicate is excluded, never sampled",
       S, "            excluded.extend(Excluded(request_id, Exclusion.conflicting_duplicate) for _ in rows)",
       "            kept.append(rows[0])",
       "test_a_conflicting_duplicate_is_excluded_rather_than_sampled_twice",
       "test_deduplication_accounts_for_every_row"),
    _m("identical_duplicates_dropped_silently", "every input row is accounted for",
       S, "            excluded.extend(Excluded(request_id, Exclusion.duplicate_row) for _ in rows[1:])",
       "            pass",
       "test_a_repeated_row_is_sampled_once", "test_deduplication_accounts_for_every_row"),

    # --- r1 R43 / R56: what counts as feedback, and what counts as calibration -------
    _m("any_feedback_counts_as_calibration",
       "only a calibration_label entry is calibration membership (R43)",
       S, "if candidate.labelled_against(rubric_version):", "if candidate.feedback:",
       "test_ordinary_customer_feedback_is_a_stratum_not_a_calibration_label"),
    _m("a_label_at_any_version_excludes", "a new rubric version is a new series (J4)",
       S, "return any(entry.rubric_version == rubric_version for entry in self.calibration_labels)",
       "return bool(self.calibration_labels)",
       "test_a_trace_already_labelled_at_this_rubric_version_is_excluded"),
    _m("judge_scores_count_as_customer_feedback",
       "the feedback stratum is customer signal, not the judge's own output",
       S, "return any(entry.author_role is AuthorRole.customer and not entry.calibration_set",
       "return any(not entry.calibration_set and (True",
       "test_a_judges_own_score_is_not_customer_feedback"),
    _m("calibration_keys_on_by_operator",
       "calibration membership is calibration_set, not the by_operator marker (R56)",
       S, "return tuple(entry for entry in self.own_feedback if entry.calibration_set)",
       "return tuple(entry for entry in self.own_feedback if entry.by_operator)",
       "test_an_ordinary_entry_the_platform_made_is_neither_signal_nor_label"),
    _m("platform_entry_counts_as_customer_signal",
       "a by_operator ordinary entry is not customer signal (R56/R50)",
       S, "                   and not entry.by_operator for entry in self.own_feedback)",
       "                   for entry in self.own_feedback)",
       "test_an_ordinary_entry_the_platform_made_is_neither_signal_nor_label"),
    _m("feedback_org_not_checked", "a feedback row counts only for its own organization (R56)",
       S, "                     if entry.org_id == self.org_id and entry.request_id == self.request_id)",
       "                     if entry.request_id == self.request_id)",
       "test_another_orgs_label_cannot_exclude_this_orgs_trace"),
    _m("feedback_request_not_checked", "a feedback row counts only for its own request (R56)",
       S, "                     if entry.org_id == self.org_id and entry.request_id == self.request_id)",
       "                     if entry.org_id == self.org_id)",
       "test_a_row_for_another_request_does_not_move_a_trace_into_the_feedback_stratum"),

    # --- other exclusions and inputs --------------------------------------------------
    _m("unreadable_content_selected", "missing, lost or expired content is never a sample",
       S, "if candidate.content_state is not ContentState.available:", "if False:",
       "test_content_that_cannot_be_read_is_excluded_with_its_reason",
       "test_unreadable_content_never_reaches_the_plan"),
    _m("non_full_trace_selected", "only a full-mode trace may be evaluated",
       S, "if candidate.trace_mode is not TraceMode.full:", "if False:",
       "test_a_non_full_trace_is_excluded_even_with_consent"),
    _m("rubric_version_unchecked_in_select", "selection validates the rubric version",
       S, "    rubric_version = _check_rubric_version(rubric_version)",
       "    rubric_version = rubric_version",
       "test_the_rubric_version_is_validated"),

    # --- the limited flag -------------------------------------------------------------
    _m("limited_flag_lost", "a sample with no media is marked limited",
       S, "limited=not c.media_available)", "limited=False)",
       "test_a_sample_without_media_is_marked_limited"),
    _m("limited_result_not_marked", "a limited evaluation says so on the stored result",
       R, "limited = not media_available", "limited = False",
       "test_a_limited_evaluation_is_accepted_and_can_never_pass",
       "test_without_media_groundedness_is_not_scored_at_all",
       "test_a_well_formed_result_is_accepted_with_its_rubric_version",
       "test_no_media_no_pass_does_not_depend_on_a_threshold"),
    _m("missing_criterion_counts_as_a_pass",
       "an unscored criterion in the pass rule fails it",
       R, "if score is None or score < criterion.pass_at:",
       "if score is not None and score < criterion.pass_at:",
       "test_the_pass_rule_belongs_to_the_rubric_version"),
    _m("media_criterion_passes_without_media",
       "no media, no pass - in code, not from the thresholds (R56)",
       R, "        if not media and self.requires_media:", "        if False:",
       "test_the_pass_rule_belongs_to_the_rubric_version",
       "test_no_media_no_pass_does_not_depend_on_a_threshold"),
    _m("media_criteria_scored_without_media",
       "a media-dependent criterion is not scored from text alone",
       R, "return tuple(c for c in self.criteria if media or not c.requires_media)",
       "return self.criteria",
       "test_a_limited_evaluation_is_accepted_and_can_never_pass",
       "test_no_media_no_pass_does_not_depend_on_a_threshold"),
    _m("scores_record_allows_a_no_media_pass",
       "the scores record itself refuses a no-media pass (R56)",
       R, "        if self.limited and self.overall_pass and self.media_required:",
       "        if False:",
       "test_the_scores_record_refuses_a_no_media_pass"),

    # --- score validation ------------------------------------------------------------
    _m("score_range_unchecked", "a score outside the rubric's range is rejected",
       R, "if not criterion.min_score <= score <= criterion.max_score:", "if not True:",
       "test_a_score_outside_the_allowed_range_is_rejected",
       "test_a_hostile_payload_is_rejected_rather_than_raised"),
    _m("boolean_score_accepted", "True is not 1 and 4.0 is not 4",
       R, "if isinstance(score, bool) or not isinstance(score, int):",
       "if not isinstance(score, int):",
       "test_a_score_that_is_not_an_integer_is_rejected"),
    _m("closed_key_set_opened", "a judge result has exactly the rubric's keys",
       R, "    unexpected = sorted(keys - required)", "    unexpected = []",
       "test_a_missing_or_extra_field_is_rejected_by_name",
       "test_a_hostile_payload_is_rejected_rather_than_raised"),
    _m("rationale_bound_ignored", "a rationale is bounded text",
       R, "if not rubric.min_rationale_chars <= len(rationale) <= rubric.max_rationale_chars:",
       "if not True:",
       "test_a_rationale_is_required_and_bounded", "test_a_rationale_bound_counts_code_points"),
    _m("overall_pass_claim_trusted", "the pass rule belongs to the rubric version",
       R, "if claimed != expected:", "if claimed is None:",
       "test_overall_pass_must_be_a_boolean_and_must_match_the_rubric",
       "test_a_limited_evaluation_is_accepted_and_can_never_pass",
       "test_no_media_no_pass_does_not_depend_on_a_threshold"),
    _m("bounded_int_accepts_a_boolean", "a rubric integer is an integer, not a boolean (R43/R54)",
       R, "    if isinstance(value, bool) or not isinstance(value, int):",
       "    if not isinstance(value, int):",
       "test_a_rubric_version_is_an_integer_in_range", "test_a_criterion_is_validated",
       "test_the_text_bounds_are_validated"),
    _m("bounded_int_ignores_the_range", "a rubric integer is range-checked",
       R, "    if not low <= value <= high:", "    if False:",
       "test_a_rubric_version_is_an_integer_in_range", "test_a_criterion_is_validated",
       "test_the_text_bounds_are_validated"),
    _m("reserved_criterion_name_allowed", "a criterion may not be called overall_pass or notes",
       R, "        if self.name in RESERVED_NAMES:", "        if False:",
       "test_a_criterion_is_validated"),

    # --- the validator never raises ---------------------------------------------------
    _m("non_string_key_reaches_the_key_arithmetic",
       "a non-string key is rejected, not raised (validate_output never raises)",
       R, "    unnamed = [key for key in payload if not isinstance(key, str)]", "    unnamed = []",
       "test_a_hostile_payload_is_rejected_rather_than_raised", dies_by=("TypeError",)),
    _m("brief_lets_an_unprintable_value_raise",
       "an untrusted value is rendered without raising (a 5,000-digit int has no repr)",
       R, '        return f"<unprintable {type(value).__name__}>"', "        raise",
       "test_a_hostile_payload_is_rejected_rather_than_raised", dies_by=("ValueError",)),
    _m("detail_unbounded", "a rejection detail is a bounded log field",
       R, "        if len(self.detail) > MAX_DETAIL_CHARS:", "        if False:",
       "test_every_rejection_detail_is_bounded"),
    _m("duplicate_json_keys_accepted", "a duplicate JSON key is refused, not resolved silently",
       R, "    return json.loads(text, object_pairs_hook=_no_duplicate_keys)",
       "    return json.loads(text)",
       "test_parsing_refuses_a_duplicate_key"),

    # --- duplicate delivery and the ledger's bounds -----------------------------------
    _m("dedupe_key_drops_the_rubric_version",
       "late results dedupe by run, sample AND rubric version",
       R, "return (result.run_id, result.sample_id, result.rubric_version)",
       "return (result.run_id, result.sample_id)",
       "test_results_deduplicate_by_run_sample_and_rubric_version"),
    _m("duplicate_overwrites_the_first_outcome", "the first outcome for a key wins",
       R, "        stored = self._by_key.get(key)\n        if stored is not None:\n            return False, stored",
       "        stored = self._by_key.get(key)\n        if stored is not None:\n            self._by_key[key] = result",
       "test_results_deduplicate_by_run_sample_and_rubric_version",
       "test_a_rejection_is_an_outcome_and_dedupes_like_one"),
    _m("ledger_accepts_any_run", "a ledger belongs to one run",
       R, "        if result.run_id != self.run_id:", "        if False:",
       "test_a_ledger_belongs_to_one_run_and_its_planned_samples"),
    _m("ledger_accepts_any_sample", "a ledger is bounded by the run's planned samples",
       R, "        if result.sample_id not in self.sample_ids:", "        if False:",
       "test_a_ledger_belongs_to_one_run_and_its_planned_samples"),
    _m("ledger_accepts_any_rubric_version", "a ledger holds one rubric version's series",
       R, "        if result.rubric_version != self.rubric_version:", "        if False:",
       "test_results_deduplicate_by_run_sample_and_rubric_version"),

    # --- the money guard (R57) ---------------------------------------------------------
    _m("dry_run_can_submit", "only the exact mode live authorizes a submission",
       C, '        raise errors.BudgetExceeded(f"judge mode {settings.judge_mode!r} is not "\n'
          '                                    f"{JUDGE_MODE_LIVE!r} and cannot authorize a live submission")',
       "        pass",
       "test_the_defaults_can_never_authorize_a_submission",
       "test_only_the_exact_mode_live_authorizes"),
    _m("mode_compared_loosely", "the mode is matched exactly, not against dry_run (R57)",
       C, "    if settings.judge_mode != JUDGE_MODE_LIVE:",
       '    if settings.judge_mode == "dry_run":',
       "test_only_the_exact_mode_live_authorizes"),
    _m("zero_budget_can_submit", "a live submission needs a positive budget",
       C, '        raise errors.BudgetExceeded("a live submission needs a positive JUDGE_LIVE_BUDGET_USD")',
       "        pass",
       "test_the_defaults_can_never_authorize_a_submission",
       "test_a_budget_that_is_not_positive_never_authorizes"),
    _m("unpriced_estimate_can_submit",
       "a pricing estimate without a hard maximum cannot authorize a submission",
       C, '        raise errors.BudgetExceeded("an unpriced estimate cannot authorize a live submission")',
       "        pass",
       "test_a_pricing_estimate_without_a_hard_maximum_cannot_authorize_a_submission",
       "test_the_guard_recomputes_the_total_and_never_trusts_the_estimate",
       "test_a_zero_or_negative_sample_count_never_authorizes"),
    _m("guard_trusts_the_estimates_total",
       "the guard recomputes ceiling(per_sample x samples) (R57)",
       C, "    total = worst_case(rate, estimate.ceilings, samples)",
       "    total = estimate.worst_case_total or money.ZERO",
       "test_the_guard_recomputes_the_total_and_never_trusts_the_estimate"),
    _m("stale_estimate_accepted", "an estimate that disagrees with the recomputation is refused",
       C, "    if estimate.worst_case_total is not None and estimate.worst_case_total != total:",
       "    if False:",
       "test_the_guard_recomputes_the_total_and_never_trusts_the_estimate"),
    _m("priced_from_per_sample_only", "a half-filled estimate is not a priced one",
       C, "                and _positive_money(self.worst_case_total) is not None)",
       "                and True)",
       "test_the_guard_recomputes_the_total_and_never_trusts_the_estimate"),
    _m("over_budget_can_submit", "the worst case must fit the budget",
       C, "    if total > budget:", "    if False:",
       "test_a_worst_case_over_the_budget_is_refused_and_the_boundary_is_inclusive"),
    _m("budget_boundary_exclusive", "a worst case exactly at the budget is affordable (R57)",
       C, "    if total > budget:", "    if total >= budget:",
       "test_a_worst_case_over_the_budget_is_refused_and_the_boundary_is_inclusive"),
    _m("reasoning_tokens_not_reserved", "reasoning tokens are billed as output and reserved",
       C, "return self.output_tokens + self.reasoning_tokens", "return self.output_tokens",
       "test_reasoning_tokens_are_billed_as_output_and_included"),
    _m("zero_output_ceiling_accepted", "a ceiling that would reserve nothing is refused",
       C, '            raise ValueError("output_tokens must be positive")', "            pass",
       "test_a_ceiling_that_would_reserve_nothing_is_refused"),
    _m("worst_case_escapes_the_money_domain",
       "a worst case outside numeric(20, 8) is refused, not returned",
       C, "        return money.parse(money.ceiling(per_sample * samples))",
       "        return money.ceiling(per_sample * samples)",
       "test_an_unrepresentable_worst_case_refuses_rather_than_raising_arithmetic"),

    # --- the rate table (R57) ----------------------------------------------------------
    _m("a_shipped_rate_appears_from_nowhere",
       "the shipped rate table is empty until an approved price row exists",
       C, "APPROVED_RATES = StaticRateTable()",
       'APPROVED_RATES = StaticRateTable((ProviderRate(price_version="guess", model="claude-opus-5",\n'
       '                                              input_per_million="5", output_per_million="25",\n'
       '                                              source="guessed",\n'
       '                                              effective_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),))',
       "test_the_shipped_rate_table_is_empty_and_says_why",
       "test_a_dry_run_reports_samples_costs_and_why_it_will_not_submit",
       dies_by=("NameError",)),
    _m("rate_lookup_takes_the_first_row", "the row effective at the clock is used (R57)",
       C, "        return max(effective, key=lambda row: row.effective_at)",
       "        return effective[0]",
       "test_the_rate_effective_at_the_clock_is_used_whatever_the_row_order"),
    _m("rate_lookup_ignores_the_clock", "a rate not yet effective is not a rate (R57)",
       C, "        effective = [row for row in self.rows if row.model == model and row.effective_at <= at]",
       "        effective = [row for row in self.rows if row.model == model]",
       "test_the_rate_effective_at_the_clock_is_used_whatever_the_row_order"),
    _m("duplicate_rate_rows_accepted", "duplicate rows for a model and instant are refused (R57)",
       C, "        if len(set(keys)) != len(keys):", "        if False:",
       "test_the_rate_table_refuses_duplicate_rows_for_a_model"),
    _m("a_zero_or_negative_rate_accepted", "every rate is a finite positive amount (R57)",
       C, "            rate = _positive_money(getattr(self, name))",
       "            rate = money.parse(getattr(self, name))",
       "test_a_rate_row_is_validated_at_the_boundary"),
    _m("naive_effective_at_accepted", "an instant that cannot be compared is refused",
       C, "        if not isinstance(self.effective_at, datetime) or self.effective_at.tzinfo is None:",
       "        if False:",
       "test_a_rate_row_is_validated_at_the_boundary"),

    # --- the dry run: its bound, its ordering, its projection, its import hygiene ------
    _m("scan_bound_not_enforced", "the scan bound holds whatever the source returns (B1/R56)",
       D, "    candidates = tuple(await source.candidates(org_id, since=since, limit=limit))[:limit]",
       "    candidates = tuple(await source.candidates(org_id, since=since, limit=limit))",
       "test_the_scan_bound_holds_against_a_source_that_ignores_it"),
    _m("scan_bound_range_unchecked", "the scan bound is in 1..MAX_CANDIDATES",
       D, "    if not 1 <= limit <= MAX_CANDIDATES:", "    if False:",
       "test_a_scan_bound_that_is_not_one_is_refused"),
    _m("scan_bound_accepts_a_boolean", "True is not a scan bound of 1",
       D, "    if isinstance(limit, bool) or not isinstance(limit, int):",
       "    if not isinstance(limit, int):",
       "test_a_scan_bound_that_is_not_one_is_refused"),
    _m("consent_checked_after_the_read", "a refusal never reads the traces (B5)",
       D, "    check_consent(org_id, consent, now)", "    pass",
       "test_a_refusal_never_reads_the_traces"),
    _m("judge_run_is_not_a_dry_run", "a planned run is a dry run with no reservation",
       D, "                        state=JudgeRunState.dry_run, created_at=created_at)",
       "                        state=JudgeRunState.reserved, created_at=created_at)",
       "test_the_plan_projects_into_the_frozen_judge_run_record"),
    _m("judge_run_reserves_money", "a dry-run projection reserves nothing",
       D, "reserved_cost=money.ZERO,", 'reserved_cost=money.parse("1"),',
       "test_the_plan_projects_into_the_frozen_judge_run_record"),
    _m("provider_sdk_imported_on_the_dry_run_path",
       "the dry-run path imports no provider SDK",
       D, "from dataclasses import dataclass", "import anthropic\nfrom dataclasses import dataclass",
       "test_the_judge_package_never_imports_the_provider_sdk",
       "test_importing_the_dry_run_path_loads_no_provider_sdk"),
)


_NODE = re.compile(r"^(FAILED|ERROR)\s+(\S+)")
# `--tb=line` prints one `path:lineno: <message>` line per failure. An assertion reads
# `assert ...`, a `pytest.raises` miss reads `Failed: DID NOT RAISE`, and anything else
# leads with its exception class - which is what we have to be able to tell apart.
_DEATH = re.compile(r"^\S*?:\d+: (?P<message>.+)$")
_CLASS = re.compile(r"^(?P<cls>[A-Za-z_][A-Za-z0-9_.]*)(?::|$)")


def _failing_ids(stdout: str) -> tuple[list[str], list[str]]:
    failed, errored = [], []
    for line in stdout.splitlines():
        match = _NODE.match(line.strip())
        if match:
            (failed if match.group(1) == "FAILED" else errored).append(match.group(2))
    return failed, errored


def _death_kinds(stdout: str) -> list[str]:
    """How each failure died: `AssertionError`, `Failed`, or an exception class."""
    kinds: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith(("FAILED", "ERROR", "E ")):
            continue
        match = _DEATH.match(line)
        if not match:
            continue
        message = match.group("message")
        if message.startswith("assert"):
            kinds.append("AssertionError")
            continue
        named = _CLASS.match(message)
        kinds.append(named.group("cls") if named else "unknown")
    return kinds


def _names(test_id: str) -> str:
    """The function name out of a node id, parametrization and all: a mutant names a
    case, and `case[param]` is that case."""
    tail = test_id.rsplit("::", 1)[-1]
    return tail.split("[", 1)[0]


def run_mutant(mutant: Mutant) -> Result:
    """Apply one mutant to a throwaway copy and run its cases there.

    A kill requires pytest exit 1, at least one failure, every failing test naming one of
    the mutant's own cases, and every death being assertion-shaped or declared in
    `dies_by`. So a syntax error, an import-time failure, a defect with wider reach than
    the declaration, or a test that crashed instead of failing is `broken_runner`, which
    fails the run exactly as a survivor does.
    """
    if not mutant.cases:
        return Result(Outcome.misdeclared, "declares no case")
    with tempfile.TemporaryDirectory(prefix=f"j1-mutant-{mutant.name}-") as tmp:
        root = pathlib.Path(tmp)
        ignore = shutil.ignore_patterns("__pycache__")
        shutil.copytree(API_DIR / PACKAGE, root / PACKAGE, ignore=ignore)
        shutil.copytree(API_DIR / "tests", root / "tests", ignore=ignore)
        # the pytest configuration too (importlib import mode, pythonpath), so the copy
        # collects the suite exactly as the worktree does (R48)
        shutil.copy2(API_DIR / "pyproject.toml", root / "pyproject.toml")
        target = root / mutant.path
        source = target.read_text()
        if mutant.old not in source:
            return Result(Outcome.misdeclared,
                          f"anchor not found in {mutant.file}: {mutant.old[:60]!r}")
        target.write_text(source.replace(mutant.old, mutant.new, 1))
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider",
             "-rf", "--tb=line", SUITE, f"--ignore={SUITE}/test_mutants.py",
             "-k", " or ".join(mutant.cases)],
            cwd=root, capture_output=True, text=True,
            env={"PYTHONPATH": str(root), "PATH": "/usr/bin:/bin"})
        stdout = done.stdout or ""
        lines = (stdout or done.stderr).strip().splitlines()
        summary = lines[-1] if lines else "no output"
        if done.returncode not in (PYTEST_ALL_PASSED, PYTEST_TESTS_FAILED):
            return Result(Outcome.broken_runner, f"pytest exit {done.returncode}: {summary}")
        if not re.search(r"(\d+) (?:passed|failed|skipped)", summary) or "no tests ran" in summary:
            return Result(Outcome.misdeclared, f"no case matched: {summary}")
        failed, errored = _failing_ids(stdout)
        if errored:
            return Result(Outcome.broken_runner, f"collection errors: {errored[:3]}")
        if done.returncode == PYTEST_ALL_PASSED or not failed:
            return Result(Outcome.survived, summary)
        stray = [test_id for test_id in failed if _names(test_id) not in mutant.cases]
        if stray:
            return Result(Outcome.broken_runner, f"failures outside the named cases: {stray[:3]}")
        allowed = ASSERTION_DEATHS | set(mutant.dies_by)
        crashed = [kind for kind in _death_kinds(stdout) if kind not in allowed]
        if crashed:
            # The case blew up rather than asserting. It may well have noticed the defect,
            # but a crash is not a proof: declare the exception in `dies_by` if the
            # invariant really is "this never raises".
            return Result(Outcome.broken_runner,
                          f"undeclared exception deaths {sorted(set(crashed))}: {summary}")
        return Result(Outcome.killed, summary)


def main() -> int:
    parser = argparse.ArgumentParser(description="run track J's mutation list")
    parser.add_argument("names", nargs="*")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        for mutant in MUTANTS:
            print(f"{mutant.name:44s} {mutant.invariant}")
        print(f"\n{len(MUTANTS)} mutants over "
              f"{len({case for m in MUTANTS for case in m.cases})} named cases")
        return 0
    chosen = [m for m in MUTANTS if not args.names or m.name in args.names]
    bad: dict[str, list[str]] = {}
    for mutant in chosen:
        result = run_mutant(mutant)
        print(f"[{result.outcome:13s}] {mutant.name}: {result.detail}")
        if not result.killed:
            bad.setdefault(result.outcome.value, []).append(mutant.name)
    failures = sum(len(names) for names in bad.values())
    print(f"\n{len(chosen) - failures}/{len(chosen)} killed"
          + "".join(f"; {outcome}: {names}" for outcome, names in sorted(bad.items())))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
