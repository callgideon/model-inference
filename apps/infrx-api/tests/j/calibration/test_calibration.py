#!/usr/bin/env python3
"""J3 JUDGE-SCORES: operator calibration and the quality report (`infrx.judge.calibration`).

Truth is an operator `calibration_label` (C3F/R43 provenance) of the report's organization
and rubric version, nothing else; agreement is Cohen's kappa on `overall_pass` and Spearman
rho per trusted criterion (`research/traces/06` §3.9), each with a 95% interval and a
verdict that never turns too few, constant or limited evidence into a pass.

    uv run --frozen pytest -q tests/j/calibration
"""
from __future__ import annotations

import dataclasses
import math
import statistics

from infrx.contracts.records import AuthorRole, CalibrationLabel, FeedbackName
from infrx.judge import MARLIN_VIDEO_V1, validate_output
from infrx.judge.calibration import MIN_PAIRS, report, spearman

from tests.j import fakes

RUN = fakes.uuid(900)
C, P, BAD, U = (CalibrationLabel.correct, CalibrationLabel.partially_correct,
              CalibrationLabel.incorrect, CalibrationLabel.unusable)


def sid(n: int) -> str:
    return fakes.uuid(1000 + n)


def judged(n: int, *, passed: bool = True, score: int = 4, media: bool = True,
           rubric=MARLIN_VIDEO_V1):
    """A validated judge result: a pass scores `score` (>= 4) on relevance and groundedness,
    a fail 2 on both; without media there is no groundedness score at all."""
    graded = score if passed else 2
    payload = fakes.result(relevance=graded, groundedness=graded if media else None)
    return validate_output(rubric, payload, run_id=RUN, sample_id=sid(n), media_available=media)


def labelled(pairs):
    """[(n, judge pass, operator verdict)] -> (results, labels)."""
    return ([judged(n, passed=ok) for n, ok, _ in pairs],
            [fakes.label(sid(n), verdict=v) for n, _, v in pairs])


def agreeing(count: int, start: int = 0):
    return [(start + n, n % 2 == 0, C if n % 2 == 0 else BAD) for n in range(count)]


def run(results, labels, **kw):
    return report(results, labels, org_id=fakes.ORG_A, rubric_version=1, **kw)


def test_j3__customer_judge_and_operator_comments_are_never_calibration_truth():
    results, _ = labelled(agreeing(4))
    signals = [fakes.feedback(sid(0), value=5),                               # customer rating
               fakes.feedback(sid(1), name=FeedbackName.thumb, value=True),   # customer thumb
               fakes.feedback(sid(2), name=FeedbackName.comment, value="bad",
                              role=AuthorRole.judge),
               fakes.feedback(sid(3), name=FeedbackName.correction, value="x",
                              role=AuthorRole.operator)]                     # operator, no label
    out = run(results, signals)
    assert out.labels == 0 and out.kappa.n == 0 and out.state == "uncalibrated"
    assert out.excluded["not_a_calibration_label"] == 4
    assert out.excluded["unlabelled_result"] == 4


def test_j3__another_orgs_or_rubric_versions_labels_are_excluded_and_never_exemplars():
    results, labels = labelled(agreeing(4))
    foreign = [fakes.label(sid(0), org_id=fakes.ORG_B, verdict=BAD),
               fakes.label(sid(1), rubric_version=2, verdict=C)]
    v2 = judged(7, rubric=dataclasses.replace(MARLIN_VIDEO_V1, version=2))   # a v2 result
    out = run([*results, v2], [*foreign, *labels[2:], fakes.label(sid(7), verdict=C)])
    assert out.excluded["other_org"] == 1 and out.excluded["other_rubric_version"] == 2
    assert out.labels == 3 and out.kappa.n == 2 and out.excluded["no_judge_result"] == 1
    assert out.exemplars == ()                  # ORG_B's contrary verdict is nobody's exemplar


def test_j3__too_few_samples_are_insufficient_even_in_perfect_agreement():
    out = run(*labelled(agreeing(MIN_PAIRS - 1)))
    assert out.kappa.value == 1.0 and out.kappa.n == MIN_PAIRS - 1
    assert out.kappa.verdict == "insufficient" and out.state == "insufficient"
    enough = run(*labelled(agreeing(MIN_PAIRS)))
    assert enough.kappa.verdict == "met" and enough.state == "calibrated"


def test_j3__kappa_and_its_interval_match_the_hand_computation():
    # 18 pass/correct, 2 pass/incorrect, 2 fail/correct, 18 fail/incorrect:
    # po = 0.9, pe = 0.5, kappa = 0.8, se = sqrt(.9*.1 / (40*.25)) = sqrt(0.009)
    pairs = ([(n, True, C) for n in range(18)] + [(n, True, BAD) for n in range(18, 20)]
             + [(n, False, C) for n in range(20, 22)] + [(n, False, BAD) for n in range(22, 40)])
    out = run(*labelled(pairs))
    half = 1.959963984540054 * math.sqrt(0.009)
    assert out.kappa.n == 40 and math.isclose(out.kappa.value, 0.8)
    assert math.isclose(out.kappa.interval[0], 0.8 - half)
    assert math.isclose(out.kappa.interval[1], 0.8 + half)
    assert out.kappa.verdict == "met"
    assert out.exemplars == tuple(sid(n) for n in range(18, 22))       # the four disagreements


def test_j3__the_kappa_interval_is_clamped_to_its_range():
    # kappa = +-0.95 with a half-width of ~0.097: the interval stops at +-1
    high = run(*labelled([(n, True, C) for n in range(20)] + [(20, True, BAD)]
                         + [(n, False, BAD) for n in range(21, 40)]))
    low = run(*labelled([(n, True, BAD) for n in range(20)] + [(20, True, C)]
                        + [(n, False, C) for n in range(21, 40)]))
    assert math.isclose(high.kappa.value, 0.95) and high.kappa.interval[1] == 1.0
    assert math.isclose(low.kappa.value, -0.95) and low.kappa.interval[0] == -1.0


def test_j3__spearman_averages_tied_ranks():
    # x ranks 1..4, y ties in pairs -> ranks 1.5,1.5,3.5,3.5: rho = 4 / sqrt(5 * 4)
    assert math.isclose(spearman([1, 2, 3, 4], [0, 0, 2, 2]), 4 / math.sqrt(20))
    assert math.isclose(spearman([4, 3, 2, 1], [0, 0, 2, 2]), -4 / math.sqrt(20))
    assert spearman([3, 3, 3, 3], [0, 1, 2, 2]) is None                # a constant side
    # tie groups of 2, 1 and 3: average ranks 1.5, 1.5, 3, 5, 5, 5 (not 1, 1, 3, 4, 4, 4)
    assert math.isclose(spearman([1, 2, 3, 4, 5, 6], [0, 0, 1, 2, 2, 2]),
                        statistics.correlation([1, 2, 3, 4, 5, 6], [1.5, 1.5, 3, 5, 5, 5]))


def test_j3__rho_uses_partial_verdicts_kappa_does_not():
    pairs = [(0, True, C), (1, False, BAD), (2, False, P), (3, True, P)]
    out = run(*labelled(pairs))
    assert out.kappa.n == 2 and out.excluded["partial_not_binary"] == 2
    assert out.rho["relevance"].n == 4
    # relevance 4,2,2,4 vs verdict 2,0,1,1 -> ranks 3.5,1.5,1.5,3.5 vs 4,1,2.5,2.5
    value = spearman([4, 2, 2, 4], [2, 0, 1, 1])
    assert math.isclose(out.rho["relevance"].value, value)
    z, half = math.atanh(value), 1.959963984540054 * 1.06        # Fisher z, n - 3 = 1
    assert all(map(math.isclose, out.rho["relevance"].interval,
                   (math.tanh(z - half), math.tanh(z + half))))
    assert run(*labelled(pairs[:2] + [(4, True, C)])).rho["relevance"].value is None  # n = 3


def test_j3__constant_scores_are_not_computed_and_never_calibrated():
    out = run(*labelled([(n, True, C) for n in range(MIN_PAIRS + 5)]))
    assert out.kappa.value is None and out.kappa.interval is None
    assert out.kappa.verdict == "not_computed" and out.state == "insufficient"
    assert out.calibration()["agreement"] is None


def test_j3__a_limited_no_media_result_never_counts_toward_calibration():
    results, labels = labelled(agreeing(MIN_PAIRS))
    blind = [judged(100 + n, passed=False, media=False) for n in range(20)]
    out = run([*results, *blind], [*labels, *(fakes.label(sid(100 + n), verdict=BAD)
                                             for n in range(20))])
    assert out.excluded["limited_result"] == 20 and out.excluded["no_judge_result"] == 20
    assert out.kappa.n == MIN_PAIRS and out.rho["groundedness"].n == MIN_PAIRS


def test_j3__rejected_duplicate_and_unusable_rows_are_counted_not_paired():
    results, labels = labelled(agreeing(4))
    bad = validate_output(MARLIN_VIDEO_V1, {"nope": 1}, run_id=RUN, sample_id=sid(5),
                          media_available=True)
    twice = judged(0, passed=False)                       # a second result for sample 0
    unusable = fakes.label(sid(6), verdict=U)
    clash = fakes.label(sid(1), verdict=C)                # sample 1 is also labelled BAD
    out = run([*results, bad, twice], [*labels, unusable, clash])
    assert out.excluded["rejected_result"] == 1 and out.excluded["duplicate_result"] == 1
    assert out.excluded["unusable_label"] == 1 and out.excluded["conflicting_labels"] == 2
    assert out.kappa.n == 3                               # samples 0, 2, 3; 0 kept its first
    assert out.exemplars == ()


def test_j3__an_interval_crossing_the_target_is_inconclusive():
    # 40 pairs, 34 agree: po = .85, pe = .5, kappa = .7, se = sqrt(.85*.15/10) ~ .113
    pairs = ([(n, True, C) for n in range(17)] + [(n, True, BAD) for n in range(17, 20)]
             + [(n, False, C) for n in range(20, 23)] + [(n, False, BAD) for n in range(23, 40)])
    out = run(*labelled(pairs))
    assert math.isclose(out.kappa.value, 0.7)
    assert out.kappa.interval[0] < 0.6 < out.kappa.interval[1]
    assert out.kappa.verdict == "inconclusive" and out.state == "insufficient"
    # 30 of 40 agree: kappa = .5 below the target, yet its upper bound (~.77) is above it
    half = run(*labelled([(n, True, C) for n in range(15)] + [(n, True, BAD) for n in range(15, 20)]
                         + [(n, False, C) for n in range(20, 25)]
                         + [(n, False, BAD) for n in range(25, 40)]))
    assert math.isclose(half.kappa.value, 0.5) and half.kappa.verdict == "inconclusive"
    worse = run(*labelled([(n, n % 2 == 0, C if n % 4 < 2 else BAD) for n in range(40)]))
    assert worse.kappa.verdict == "not_met"


def test_j3__kappa_alone_never_calibrates_a_configuration():
    # relevance tracks the verdicts, groundedness is always 4: kappa and one rho are met,
    # the groundedness rho cannot be computed, so the configuration is not calibrated
    results = [validate_output(MARLIN_VIDEO_V1, fakes.result(relevance=4 if n % 2 == 0 else 2),
                               run_id=RUN, sample_id=sid(n), media_available=True)
               for n in range(MIN_PAIRS)]
    out = run(results, [fakes.label(sid(n), verdict=C if n % 2 == 0 else BAD)
                        for n in range(MIN_PAIRS)])
    assert out.kappa.verdict == "met" and out.rho["relevance"].verdict == "met"
    assert out.rho["groundedness"].verdict == "not_computed" and out.state == "insufficient"


def test_j3__the_report_projects_into_the_v3_calibration_shape():
    out = run(*labelled(agreeing(MIN_PAIRS)))
    shape = out.calibration()
    assert shape == {"state": "calibrated", "labels": MIN_PAIRS, "required": MIN_PAIRS,
                     "agreement": 1.0, "interval": [1.0, 1.0]}
    empty = run([], []).calibration()
    assert empty == {"state": "uncalibrated", "labels": 0, "required": MIN_PAIRS,
                     "agreement": None, "interval": None}
