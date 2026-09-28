"""J3: operator calibration and the judge quality report (JUDGE-SCORES).

**Truth is an operator verdict, nothing else.** A pair's truth is a `calibration_label`
feedback row (R43: `calibration_set`, operator-authored, `by_operator`; the C3F/R31 path is
the only way to write one) of the report's organization and rubric version. Customer
thumbs, ratings and corrections, judge-authored rows and an operator's ordinary comment are
evidence, never truth (P1: "App feedback is evidence, not privileged calibration truth").
`unusable` is not a verdict on quality; two different verdicts for one sample are a
conflict, not a coin toss. Every row left out is counted under its reason.

**Agreement** (`research/traces/06` §3.9): Cohen's kappa of `overall_pass` against the
binary verdicts (`correct` = pass, `incorrect` = fail; `partially_correct` is ordinal
evidence, not a binary one), and Spearman rho of each trusted criterion's 1-5 score against
the ordinal verdict (incorrect < partially_correct < correct). A limited (no-media) result
is not a full evaluation of a media rubric and never enters a pair (R56).

**Uncertainty.** Each statistic carries its pair count and a 95% interval (kappa: the
large-sample standard error; rho: Fisher z with the 1.06 Spearman factor). A target is
`met` only when the interval's lower bound clears it, `not_met` when the upper bound is
below it, `inconclusive` otherwise; fewer than `MIN_PAIRS` pairs is `insufficient` and a
side with no variation is `not_computed`. The configuration is `calibrated` only when every
statistic is `met` - too few, constant or limited evidence never reads as a pass.

**Exemplars** are the sample ids (never content) of this organization's kappa
disagreements, so a report cannot leak another tenant's traces.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

from ...contracts.records import CalibrationLabel, Feedback
from ..rubric import JudgeScores, Rejected

#: `06` §3.9's starting convention (⚠️ not sourced there), for kappa and each rho.
TARGET = 0.6
#: The design draws ~50 (`sampling.DEFAULT_DESIGN`); under 30 pairs the normal-approximation
#: intervals below are not evidence (⚠️ a convention, like the target).
MIN_PAIRS = 30
TRUSTED_CRITERIA = ("groundedness", "relevance")
Z95 = 1.959963984540054
ORDINAL = {CalibrationLabel.incorrect: 0, CalibrationLabel.partially_correct: 1,
           CalibrationLabel.correct: 2}
BINARY = {CalibrationLabel.correct: True, CalibrationLabel.incorrect: False}


@dataclass(frozen=True)
class Statistic:
    n: int
    value: float | None
    interval: tuple[float, float] | None
    verdict: str        # met | not_met | inconclusive | insufficient | not_computed


def _judge(n: int, value: float | None, interval: tuple[float, float] | None) -> Statistic:
    if n < MIN_PAIRS:
        verdict = "insufficient"
    elif value is None:
        verdict = "not_computed"
    elif interval[0] >= TARGET:
        verdict = "met"
    elif interval[1] < TARGET:
        verdict = "not_met"
    else:
        verdict = "inconclusive"
    return Statistic(n, value, interval, verdict)


def kappa(pairs: list[tuple[bool, bool]]) -> Statistic:
    n = len(pairs)
    if not n:
        return _judge(0, None, None)
    po = sum(a == b for a, b in pairs) / n
    pa, pb = sum(a for a, _ in pairs) / n, sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe == 1:
        return _judge(n, None, None)         # both sides constant: agreement is undefined
    value = (po - pe) / (1 - pe)
    half = Z95 * math.sqrt(po * (1 - po) / (n * (1 - pe) ** 2))
    return _judge(n, value, (max(-1.0, value - half), min(1.0, value + half)))


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1          # ties share their average rank
        i = j + 1
    return ranks


def spearman(x: list[float], y: list[float]) -> float | None:
    """Pearson correlation of average ranks; None when either side is constant."""
    rx, ry = _ranks(x), _ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sxx, syy = sum((a - mx) ** 2 for a in rx), sum((b - my) ** 2 for b in ry)
    if not sxx or not syy:
        return None
    return sxy / math.sqrt(sxx * syy)


def rho(pairs: list[tuple[int, int]]) -> Statistic:
    n = len(pairs)
    value = spearman([a for a, _ in pairs], [b for _, b in pairs]) if n > 3 else None
    if value is None:
        return _judge(n, None, None)
    z = math.atanh(max(-1 + 1e-12, min(1 - 1e-12, value)))
    half = Z95 * 1.06 / math.sqrt(n - 3)
    return _judge(n, value, (math.tanh(z - half), math.tanh(z + half)))


@dataclass(frozen=True)
class QualityReport:
    org_id: str
    rubric_version: int
    labels: int                                 # usable operator verdicts
    kappa: Statistic
    rho: dict[str, Statistic]
    exemplars: tuple[str, ...]
    excluded: Counter = field(default_factory=Counter)

    @property
    def state(self) -> str:
        if self.kappa.n == 0:
            return "uncalibrated"
        stats = [self.kappa, *self.rho.values()]
        return "calibrated" if all(s.verdict == "met" for s in stats) else "insufficient"

    def calibration(self) -> dict:
        """The V3 `Calibration` shape (apps/lab/components/traces/judge/port.ts)."""
        k = self.kappa
        return {"state": self.state, "labels": k.n, "required": MIN_PAIRS,
                "agreement": k.value, "interval": list(k.interval) if k.interval else None}


def report(results: Iterable[JudgeScores | Rejected], feedback: Iterable[Feedback], *,
           org_id: str, rubric_version: int) -> QualityReport:
    """One organization's judge results for one rubric version against its operators'
    verdicts. `results` are one run's (or one configuration's) collected results."""
    excluded: Counter = Counter()
    verdicts: dict[str, set[CalibrationLabel]] = {}
    for entry in feedback:
        if not isinstance(entry, Feedback) or not entry.calibration_set:
            excluded["not_a_calibration_label"] += 1
        elif entry.org_id != org_id:
            excluded["other_org"] += 1
        elif entry.rubric_version != rubric_version:
            excluded["other_rubric_version"] += 1
        elif entry.value == CalibrationLabel.unusable:
            excluded["unusable_label"] += 1
        else:
            verdicts.setdefault(entry.request_id, set()).add(CalibrationLabel(entry.value))
    truth = {}
    for sample_id, seen in verdicts.items():
        if len(seen) > 1:
            excluded["conflicting_labels"] += len(seen)
        else:
            truth[sample_id] = seen.pop()

    judged: dict[str, JudgeScores] = {}
    for result in results:
        if not isinstance(result, JudgeScores):
            excluded["rejected_result"] += 1
        elif result.rubric_version != rubric_version:
            excluded["other_rubric_version"] += 1
        elif result.limited:
            excluded["limited_result"] += 1
        elif result.sample_id in judged:
            excluded["duplicate_result"] += 1
        else:
            judged[result.sample_id] = result
    excluded["unlabelled_result"] += len(judged.keys() - truth.keys())
    excluded["no_judge_result"] += len(truth.keys() - judged.keys())

    binary, ordinal, exemplars = [], {c: [] for c in TRUSTED_CRITERIA}, []
    for sample_id in sorted(judged.keys() & truth.keys()):
        result, verdict = judged[sample_id], truth[sample_id]
        if verdict in BINARY:
            binary.append((result.overall_pass, BINARY[verdict]))
            if result.overall_pass != BINARY[verdict]:
                exemplars.append(sample_id)
        else:
            excluded["partial_not_binary"] += 1
        scores = result.by_name
        for criterion, pairs in ordinal.items():
            if criterion in scores:
                pairs.append((scores[criterion], ORDINAL[verdict]))
    return QualityReport(org_id=org_id, rubric_version=rubric_version, labels=len(truth),
                         kappa=kappa(binary), rho={c: rho(p) for c, p in ordinal.items()},
                         exemplars=tuple(exemplars), excluded=+excluded)
