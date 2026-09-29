#!/usr/bin/env python3
"""R32 for J3: one single-edit defect per decision `tests/j/calibration` claims, through the
shared runner (`tests/contracts/mutants.py`).

    INFRX_MUTANTS=all uv run --frozen pytest -q tests/j/calibration/test_mutants.py

`tests/j/mutants.py` (J1's list) carries the one J3 mutant its module-coverage case needs.
"""
from __future__ import annotations

import pathlib
import sys

API_DIR = pathlib.Path(__file__).resolve().parents[3]
if str(API_DIR) not in sys.path:        # `python tests/j/calibration/mutants.py`
    sys.path.insert(0, str(API_DIR))

from tests.contracts import mutants as shared  # noqa: E402
from tests.contracts.mutants import Mutant, Outcome, Result, Runner, _m  # noqa: E402,F401

SUITE = "tests/j/calibration"
R = "judge/calibration/report.py"

TRUTH = "test_j3__customer_judge_and_operator_comments_are_never_calibration_truth"
TENANT = "test_j3__another_orgs_or_rubric_versions_labels_are_excluded_and_never_exemplars"
FEW = "test_j3__too_few_samples_are_insufficient_even_in_perfect_agreement"
KAPPA = "test_j3__kappa_and_its_interval_match_the_hand_computation"
CLAMP = "test_j3__the_kappa_interval_is_clamped_to_its_range"
TIES = "test_j3__spearman_averages_tied_ranks"
PARTIAL = "test_j3__rho_uses_partial_verdicts_kappa_does_not"
CONSTANT = "test_j3__constant_scores_are_not_computed_and_never_calibrated"
LIMITED = "test_j3__a_limited_no_media_result_never_counts_toward_calibration"
ROWS = "test_j3__rejected_duplicate_and_unusable_rows_are_counted_not_paired"
CROSS = "test_j3__an_interval_crossing_the_target_is_inconclusive"
ALONE = "test_j3__kappa_alone_never_calibrates_a_configuration"
SHAPE = "test_j3__the_report_projects_into_the_v3_calibration_shape"
JOB = "test_j3__the_report_job_stores_its_configurations_calibration"

MUTANTS: tuple[Mutant, ...] = (
    # --- truth: C3F/R43 operator verdicts of this org and rubric version only ------------------
    _m("customer_signal_is_truth", "only a calibration_label row is calibration truth",
       R, "if not isinstance(entry, Feedback) or not entry.calibration_set:",
       "if not isinstance(entry, Feedback):", TRUTH, dies_by=("ValueError",)),
    _m("another_orgs_label_counts", "another organization's verdict is never this org's truth",
       R, "        elif entry.org_id != org_id:", "        elif False:", TENANT),
    _m("another_rubric_versions_label_counts", "a label is truth only for its rubric version",
       R, "        elif entry.rubric_version != rubric_version:\n            excluded",
       "        elif False:\n            excluded", TENANT),
    _m("unusable_is_a_verdict", "`unusable` is not a verdict on quality",
       R, "        elif entry.value == CalibrationLabel.unusable:", "        elif False:", ROWS,
       dies_by=("KeyError",)),
    _m("conflicting_labels_pick_one", "two verdicts for one sample are a conflict, not a pick",
       R, "        if len(seen) > 1:", "        if False:", ROWS),
    # --- results: accepted, this version, full evaluations, one per sample ---------------------
    _m("rejected_result_paired", "a rejected result is counted, never paired",
       R, "        if not isinstance(result, JudgeScores):", "        if False:", ROWS,
       dies_by=("AttributeError",)),
    _m("another_rubric_versions_result_counts", "a result pairs only under its rubric version",
       R, "        elif result.rubric_version != rubric_version:\n            excluded",
       "        elif False:\n            excluded", TENANT),
    _m("limited_result_paired", "a limited (no-media) result never enters calibration (R56)",
       R, "        elif result.limited:", "        elif False:", LIMITED),
    _m("duplicate_result_overwrites", "the first result for a sample is the one paired",
       R, "        elif result.sample_id in judged:", "        elif False:", ROWS),
    _m("unlabelled_results_uncounted", "a result with no verdict is counted",
       R, 'excluded["unlabelled_result"] += len(judged.keys() - truth.keys())',
       'excluded["unlabelled_result"] += 0', TRUTH),
    _m("unjudged_labels_uncounted", "a verdict with no usable result is counted",
       R, 'excluded["no_judge_result"] += len(truth.keys() - judged.keys())',
       'excluded["no_judge_result"] += 0', LIMITED, TENANT),
    # --- pairing ----------------------------------------------------------------------------------
    _m("partial_is_a_fail_for_kappa", "partially_correct is ordinal evidence, not binary",
       R, "BINARY = {CalibrationLabel.correct: True, CalibrationLabel.incorrect: False}",
       "BINARY = {CalibrationLabel.correct: True, CalibrationLabel.incorrect: False, "
       "CalibrationLabel.partially_correct: False}", PARTIAL),
    _m("partial_ranks_as_correct", "the ordinal is incorrect < partially_correct < correct",
       R, "CalibrationLabel.partially_correct: 1,", "CalibrationLabel.partially_correct: 2,",
       PARTIAL),
    _m("every_pair_is_an_exemplar", "an exemplar is a disagreement",
       R, "            if result.overall_pass != BINARY[verdict]:", "            if True:", KAPPA),
    # --- statistics --------------------------------------------------------------------------------
    _m("kappa_is_raw_agreement", "kappa corrects for chance agreement",
       R, "    value = (po - pe) / (1 - pe)", "    value = po", KAPPA),
    _m("kappa_se_misses_a_square", "the kappa standard error divides by n (1 - pe)^2",
       R, "(n * (1 - pe) ** 2)", "(n * (1 - pe))", KAPPA),
    _m("interval_is_90_percent", "the interval is 95%",
       R, "Z95 = 1.959963984540054", "Z95 = 1.6448536269514722", KAPPA),
    _m("kappa_upper_unclamped", "kappa's interval stops at 1",
       R, "min(1.0, value + half)", "value + half", CLAMP),
    _m("kappa_lower_unclamped", "kappa's interval stops at -1",
       R, "max(-1.0, value - half)", "value - half", CLAMP),
    _m("constant_kappa_divides", "constant sides make kappa undefined, not an error",
       R, "    if pe == 1:", "    if False:", CONSTANT, dies_by=("ZeroDivisionError",)),
    _m("ties_take_the_first_rank", "tied values share their average rank",
       R, "ranks[order[k]] = (i + j) / 2 + 1", "ranks[order[k]] = i + 1", TIES),
    _m("constant_rank_divides", "a constant side has no rank correlation",
       R, "    if not sxx or not syy:", "    if False:", TIES,
       dies_by=("ZeroDivisionError",)),
    _m("rho_from_three_pairs", "rho needs n > 3 for its interval",
       R, "if n > 3 else None", "if n > 0 else None", PARTIAL,
       dies_by=("ZeroDivisionError",)),
    _m("fisher_without_the_spearman_factor", "the Fisher z standard error is 1.06 / sqrt(n - 3)",
       R, "half = Z95 * 1.06 / math.sqrt(n - 3)", "half = Z95 / math.sqrt(n - 3)", PARTIAL),
    _m("perfect_rho_unclamped", "a perfect rho still has an interval",
       R, "math.atanh(max(-1 + 1e-12, min(1 - 1e-12, value)))", "math.atanh(value)", FEW,
       dies_by=("ValueError",)),
    # --- verdicts and state -----------------------------------------------------------------------
    _m("too_few_pairs_judged", "under MIN_PAIRS pairs is insufficient evidence",
       R, "    if n < MIN_PAIRS:", "    if False:", FEW),
    _m("met_on_the_point_estimate", "met needs the lower bound over the target",
       R, "    elif interval[0] >= TARGET:", "    elif value >= TARGET:", CROSS),
    _m("not_met_on_the_point_estimate", "not_met needs the upper bound under the target",
       R, "    elif interval[1] < TARGET:", "    elif value < TARGET:", CROSS),
    _m("kappa_alone_calibrates", "every statistic must be met, not kappa alone",
       R, "        stats = [self.kappa, *self.rho.values()]", "        stats = [self.kappa]",
       ALONE),
    _m("no_labels_reads_insufficient", "no pair at all is `uncalibrated`",
       R, "        if self.kappa.n == 0:", "        if False:", TRUTH),
    _m("port_agreement_dropped", "the V3 shape carries kappa as the agreement",
       R, '"agreement": k.value,', '"agreement": None,', SHAPE),
    _m("port_state_literal", "the V3 shape carries the computed state",
       R, '{"state": self.state,', '{"state": "calibrated",', SHAPE),
    # --- the report job (WR-J3-D8): D8's put_calibration, keyed by the configuration -----------
    _m("job_stores_nothing", "the job stores the report's calibration",
       R, "    await ledger.put_calibration(quality.calibration(),",
       "    ledger.put_calibration(quality.calibration(),", JOB),
    _m("job_grantor_is_the_provider", "the calibration is the grantor's, not the provider's",
       R, "grantor_org_id=org_id, judge_model", "grantor_org_id=provider_org_id, judge_model",
       JOB),
    _m("job_reports_every_org", "the job reports the grantor's own labels only",
       R, "    quality = report(results, feedback, org_id=org_id, rubric_version=rubric_version)",
       "    quality = report(results, feedback, org_id=provider_org_id, "
       "rubric_version=rubric_version)", JOB),
)

#: the real-store half (WR-J3-D8) is outside the runner (T2I/G8's pattern): the fake cases'
#: mutants are its oracles.
OUTSIDE = ("test_mutants.py", "test_calibration_pg.py", "test_calibration_composition_pg.py")
RUNNER = Runner(name="j3", targets=(SUITE,),
                extra_args=tuple(f"--ignore={SUITE}/{name}" for name in OUTSIDE))


def case_names() -> set[str]:
    import re
    pattern = re.compile(r"^def (test_\w+)", re.MULTILINE)
    return {name for path in (API_DIR / SUITE).glob("test_*.py") if path.name not in OUTSIDE
            for name in pattern.findall(path.read_text())}


def run_mutant(mutant: Mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    sys.exit(shared.main(MUTANTS, RUNNER, "run J3's mutation list"))
