#!/usr/bin/env python3
"""r1 R32 for track J: every invariant `tests/j` claims must be killable.

Each entry below is a **single edit** to `infrx/judge/` that breaks one named
invariant, together with the cases that must fail because of it. The runner applies
one mutant at a time to a *copy* of the package in a temporary directory and runs the
named cases there; a survivor means the case asserting that invariant proves nothing,
which is a failed task, not a warning. Nothing is written inside the worktree.

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

    @property
    def path(self) -> pathlib.Path:
        return pathlib.Path(PACKAGE) / self.file


def _m(name, invariant, file, old, new, *cases) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases)


S = "judge/sampling.py"
R = "judge/rubric.py"
C = "judge/cost.py"

MUTANTS: tuple[Mutant, ...] = (
    # --- consent (the required mutant, twice: the gate and the *currency* of it) ----
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

    # --- stratification bounds (required) -------------------------------------------
    _m("stratum_bound_ignored", "each stratum is cut to its designed size",
       S, "chosen, overflow = ordered[:size], ordered[size:]",
       "chosen, overflow = ordered, []",
       "test_the_design_is_25_uniform_15_failures_10_feedback"),
    _m("design_collapses_to_one_bound", "the design is 25/15/10, not 50 of whatever came first",
       S, "size = design.size_of(stratum)", "size = design.total",
       "test_the_design_is_25_uniform_15_failures_10_feedback",
       "test_a_stratum_that_cannot_be_filled_is_reported_not_backfilled"),
    _m("failures_lose_their_priority", "a failing trace claims the failures stratum first",
       S, "    if candidate.failure:", "    if False:",
       "test_a_failing_trace_claims_the_failures_stratum_before_feedback",
       "test_the_design_is_25_uniform_15_failures_10_feedback"),
    _m("shortfall_reported_as_filled", "an unfillable stratum is reported, never papered over",
       S, "shortfall[stratum] = size - len(chosen)", "shortfall[stratum] = 0",
       "test_a_stratum_that_cannot_be_filled_is_reported_not_backfilled"),

    # --- determinism ----------------------------------------------------------------
    _m("selection_follows_the_scan_order", "the seed decides, not the scan order",
       S, "ordered = sorted(eligible[stratum], key=lambda c: (rank(seed, c.request_id), c.request_id))",
       "ordered = list(eligible[stratum])",
       "test_the_same_seed_picks_the_same_samples_whatever_the_scan_order"),
    _m("seed_ignored", "the seed is actually read",
       S, 'digest = hashlib.blake2b(f"{seed}\\x00{request_id}".encode(), digest_size=8).digest()',
       'digest = hashlib.blake2b(f"{request_id}".encode(), digest_size=8).digest()',
       "test_the_same_seed_picks_the_same_samples_whatever_the_scan_order"),

    # --- r1 R43: customer feedback is not a calibration label -----------------------
    _m("any_feedback_counts_as_calibration",
       "only a calibration_label entry is calibration membership (R43)",
       S, "if candidate.labelled_against(rubric_version):", "if candidate.feedback:",
       "test_ordinary_customer_feedback_is_a_stratum_not_a_calibration_label"),
    _m("a_label_at_any_version_excludes", "a new rubric version is a new series (J4)",
       S, "return any(entry.rubric_version == rubric_version\n                   for entry in self.calibration_labels)",
       "return bool(self.calibration_labels)",
       "test_a_trace_already_labelled_at_this_rubric_version_is_excluded"),
    _m("judge_scores_count_as_customer_feedback",
       "the feedback stratum is customer signal, not the judge's own output",
       S, "return any(entry.author_role is AuthorRole.customer and not entry.calibration_set\n"
          "                   for entry in self.feedback)",
       "return any(not entry.calibration_set for entry in self.feedback)",
       "test_a_judges_own_score_is_not_customer_feedback"),

    # --- exclusions -----------------------------------------------------------------
    _m("unreadable_content_selected", "missing, lost or expired content is never a sample",
       S, "if candidate.content_state is not ContentState.available:", "if False:",
       "test_content_that_cannot_be_read_is_excluded_with_its_reason",
       "test_unreadable_content_never_reaches_the_plan"),
    _m("non_full_trace_selected", "only a full-mode trace may be evaluated",
       S, "if candidate.trace_mode is not TraceMode.full:", "if False:",
       "test_a_non_full_trace_is_excluded_even_with_consent"),

    # --- the limited flag (required) -------------------------------------------------
    _m("limited_flag_lost", "a sample with no media is marked limited",
       S, "limited=not c.media_available)", "limited=False)",
       "test_a_sample_without_media_is_marked_limited"),
    _m("limited_result_not_marked", "a limited evaluation says so on the stored result",
       R, "limited = not media_available", "limited = False",
       "test_a_limited_evaluation_is_accepted_and_can_never_pass",
       "test_without_media_groundedness_is_not_scored_at_all"),
    _m("missing_criterion_counts_as_a_pass",
       "an unscored criterion in the pass rule fails it (the general no-media rule)",
       R, "if score is None or score < criterion.pass_at:",
       "if score is not None and score < criterion.pass_at:",
       "test_the_pass_rule_belongs_to_the_rubric_version"),
    _m("media_criterion_passes_without_media",
       "a media-dependent criterion in the pass rule can never pass without media",
       R, "            if criterion.requires_media and not media:", "            if False:",
       "test_the_pass_rule_belongs_to_the_rubric_version"),
    _m("media_criteria_scored_without_media",
       "a media-dependent criterion is not scored from text alone",
       R, "return tuple(c for c in self.criteria if media or not c.requires_media)",
       "return self.criteria",
       "test_a_limited_evaluation_is_accepted_and_can_never_pass"),

    # --- score validation (required: score range) -----------------------------------
    _m("score_range_unchecked", "a score outside the rubric's range is rejected",
       R, "if not criterion.min_score <= score <= criterion.max_score:", "if not True:",
       "test_a_score_outside_the_allowed_range_is_rejected"),
    _m("boolean_score_accepted", "True is not 1 and 4.0 is not 4",
       R, "if isinstance(score, bool) or not isinstance(score, int):",
       "if not isinstance(score, int):",
       "test_a_score_that_is_not_an_integer_is_rejected"),
    _m("closed_key_set_opened", "a judge result has exactly the rubric's keys",
       R, "    unexpected = sorted(keys - required)", "    unexpected = []",
       "test_a_missing_or_extra_field_is_rejected_by_name"),
    _m("rationale_bound_ignored", "a rationale is bounded text",
       R, "if not rubric.min_rationale_chars <= len(rationale) <= rubric.max_rationale_chars:",
       "if not True:",
       "test_a_rationale_is_required_and_bounded", "test_a_rationale_bound_counts_code_points"),
    _m("overall_pass_claim_trusted", "the pass rule belongs to the rubric version",
       R, "if claimed != expected:", "if claimed is None:",
       "test_overall_pass_must_be_a_boolean_and_must_match_the_rubric",
       "test_a_limited_evaluation_is_accepted_and_can_never_pass"),
    _m("rubric_version_is_not_an_integer", "a rubric version is an integer 1..1000 (R43)",
       R, "if isinstance(self.version, bool) or not isinstance(self.version, int):",
       "if False:",
       "test_a_rubric_version_is_an_integer_in_range"),

    # --- duplicate delivery (required: dedupe key) ----------------------------------
    _m("dedupe_key_drops_the_rubric_version",
       "late results dedupe by run, sample AND rubric version",
       R, "return (result.run_id, result.sample_id, result.rubric_version)",
       "return (result.run_id, result.sample_id)",
       "test_results_deduplicate_by_run_sample_and_rubric_version"),
    _m("duplicate_overwrites_the_first_outcome", "the first outcome for a key wins",
       R, "        if stored is not None:\n            return False, stored",
       "        if stored is not None:\n            self._by_key[key] = result",
       "test_results_deduplicate_by_run_sample_and_rubric_version",
       "test_a_rejection_is_an_outcome_and_dedupes_like_one"),

    # --- the budget guard -----------------------------------------------------------
    _m("dry_run_can_submit", "dry-run mode cannot authorize a live submission",
       C, '        raise errors.BudgetExceeded(f"judge mode {settings.judge_mode} cannot authorize a "\n'
          '                                    "live submission")',
       "        pass",
       "test_the_defaults_can_never_authorize_a_submission"),
    _m("zero_budget_can_submit", "a live submission needs a positive budget",
       C, '        raise errors.BudgetExceeded("a live submission needs a positive JUDGE_LIVE_BUDGET_USD")',
       "        pass",
       "test_the_defaults_can_never_authorize_a_submission"),
    _m("unpriced_estimate_can_submit",
       "a pricing estimate without a hard maximum cannot authorize a submission",
       C, '        raise errors.BudgetExceeded("an unpriced estimate cannot authorize a live submission")',
       "        pass",
       "test_a_pricing_estimate_without_a_hard_maximum_cannot_authorize_a_submission"),
    _m("over_budget_can_submit", "the worst case must fit the budget",
       C, "    if total > budget:", "    if False:",
       "test_a_worst_case_over_the_budget_is_refused"),
    _m("reasoning_tokens_not_reserved", "reasoning tokens are billed as output and reserved",
       C, "return self.output_tokens + self.reasoning_tokens", "return self.output_tokens",
       "test_reasoning_tokens_are_billed_as_output_and_included"),
    _m("zero_output_ceiling_accepted", "a ceiling that would reserve nothing is refused",
       C, '            raise ValueError("output_tokens must be positive")', "            pass",
       "test_a_ceiling_that_would_reserve_nothing_is_refused"),
    _m("a_shipped_rate_appears_from_nowhere",
       "the shipped rate table is empty until an approved price row exists",
       C, "APPROVED_RATES = StaticRateTable()",
       'APPROVED_RATES = StaticRateTable((ProviderRate(price_version="guess", model="claude-opus-5",\n'
       '                                              input_per_million="5", output_per_million="25",\n'
       '                                              source="guessed"),))',
       "test_the_shipped_rate_table_is_empty_and_says_why",
       "test_a_dry_run_reports_samples_costs_and_why_it_will_not_submit"),
)


_NODE = re.compile(r"^(FAILED|ERROR)\s+(\S+)")


def _failing_ids(stdout: str) -> tuple[list[str], list[str]]:
    failed, errored = [], []
    for line in stdout.splitlines():
        match = _NODE.match(line.strip())
        if match:
            (failed if match.group(1) == "FAILED" else errored).append(match.group(2))
    return failed, errored


def _names(test_id: str) -> str:
    """The function name out of a node id, parametrization and all: a mutant names a
    case, and `case[param]` is that case."""
    tail = test_id.rsplit("::", 1)[-1]
    return tail.split("[", 1)[0]


def run_mutant(mutant: Mutant) -> Result:
    """Apply one mutant to a throwaway copy and run its cases there.

    A kill requires pytest exit 1, at least one failure, and every failing test naming
    one of the mutant's own cases - so a syntax error, an import-time failure or a
    defect with wider reach than the declaration is `broken_runner`, which fails the
    run exactly as a survivor does.
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
             "-rf", "--tb=no", SUITE, f"--ignore={SUITE}/test_mutants.py",
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
        return Result(Outcome.killed, summary)


def main() -> int:
    parser = argparse.ArgumentParser(description="run track J's mutation list")
    parser.add_argument("names", nargs="*")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    if args.list:
        for mutant in MUTANTS:
            print(f"{mutant.name:42s} {mutant.invariant}")
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
