"""AP-08 (api-judge-2) 08e: the gold set - a human-reviewed reference set (P-07's reviewed labels)
as J3's calibration truth for ONE configuration, graded against that configuration's stored
judge results (SR-AP08-1's read) and published through D8's calibration store. No database:
the results read and the ledger are in memory; the PostgreSQL proof is the worker world.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json

import pytest

from infrx.contracts.records import AuthorRole, CalibrationLabel, FeedbackName
from infrx.judge.calibration import MIN_PAIRS, goldset
from infrx.judge.rubric import MARLIN_VIDEO_V1, validate_json

PROVIDER = "b0000001-0000-4000-8000-000000000001"
GRANTOR = "0a000000-0000-4000-8000-0000000000a1"
RUN = "7a000000-0000-4000-8000-0000000000a1"


def sid(n: int) -> str:
    return f"5a000000-0000-4000-8000-{n:012x}"


def gold_doc(verdicts, **over) -> dict:
    return {"provider_org_id": PROVIDER, "org_id": GRANTOR, "judge_model": "judge-1",
            "rubric_version": 1, "reviewed_by": "operator@infrx.test",
            "review_ref": "P-07 gold set review 2026-10-02",
            "labels": [{"sample_id": sid(n), "verdict": v} for n, v in verdicts], **over}


def stored(n: int, passed: bool, *, media: bool = True) -> dict:
    """A row as SR-AP08-1's results read answers it: J2's record, stored whole."""
    score = 5 if passed else 2
    payload = {c.name: {"score": score, "rationale": "r"}
               for c in MARLIN_VIDEO_V1.criteria_for(media=media)}
    payload.update(overall_pass=passed and media, notes="")
    result = validate_json(MARLIN_VIDEO_V1, json.dumps(payload), run_id=RUN, sample_id=sid(n),
                           media_available=media)
    return {"run_id": RUN, "sample_id": sid(n), "rubric_version": 1,
            "accepted": result.accepted, "result": dataclasses.asdict(result)}


class Ledger:
    def __init__(self) -> None:
        self.put: list[tuple[dict, dict]] = []

    async def put_calibration(self, calibration, **key):
        self.put.append((calibration, key))
        return len(self.put)


def calibrate(rows, gold):
    ledger, asked = Ledger(), []

    async def results_of(g):
        asked.append(g)
        return rows

    quality = asyncio.run(goldset.calibrate(ledger, results_of, gold))
    return quality, ledger, asked


def test_ap08_goldset__a_reviewed_set_is_operator_truth_of_one_configuration():
    """Each label is J3's operator `calibration_label` of the configuration's grantor and
    rubric version - human ground truth, never a judge or teacher row. Failure oracle: a
    gold label read as customer/judge evidence, or one sample labelled twice."""
    gold = goldset.GoldSet.model_validate(gold_doc([(1, "correct"), (2, "incorrect")]))
    rows = goldset.labels(gold)
    assert [(f.request_id, f.value) for f in rows] == [(sid(1), "correct"), (sid(2), "incorrect")]
    assert {(f.name, f.author_role, f.calibration_set, f.by_operator, f.org_id, f.rubric_version)
            for f in rows} == {(FeedbackName.calibration_label, AuthorRole.operator, True, True,
                                GRANTOR, 1)}
    assert {f.author_principal for f in rows} == {"operator@infrx.test"}
    for bad in (gold_doc([(1, "correct"), (1, "incorrect")]), gold_doc([(1, "great")]),
                gold_doc([]), gold_doc([(1, "correct")], review_ref=""),
                gold_doc([(1, "correct")], extra=1)):
        with pytest.raises(ValueError):
            goldset.GoldSet.model_validate(bad)


def test_ap08_goldset__a_stored_row_is_the_j2_record_it_was():
    """Failure oracle: a limited (no-media) result read back as a full one (it would enter a
    pair, R56), or a quarantined result read back as scored."""
    full, limited = goldset.stored_result(stored(1, True)), goldset.stored_result(
        stored(2, True, media=False))
    assert (full.accepted, full.overall_pass, full.limited) == (True, True, False)
    assert (limited.accepted, limited.limited) == (True, True)
    assert full.by_name["groundedness"] == 5
    bad = {**stored(3, True), "accepted": False,
           "result": {"run_id": RUN, "sample_id": sid(3), "rubric_version": 1,
                      "reason": "malformed_json", "detail": "x"}}
    assert goldset.stored_result(bad).accepted is False


def test_ap08_goldset__below_the_reference_threshold_it_is_insufficient():
    """Perfect agreement on fewer than MIN_PAIRS gold labels is `insufficient`, never
    `calibrated`; MIN_PAIRS agreeing labels calibrate; the calibration is published under the
    gold set's configuration. Failure oracle: a handful of labels displayed as calibrated, or
    a calibration stored under another configuration."""
    few = [(n, n % 2 == 0) for n in range(5)]
    gold = goldset.GoldSet.model_validate(gold_doc(
        [(n, "correct" if ok else "incorrect") for n, ok in few]))
    quality, ledger, asked = calibrate([stored(n, ok) for n, ok in few], gold)
    assert asked == [gold]
    (shown, key), = ledger.put
    assert (shown["state"], shown["labels"], shown["required"]) == ("insufficient", 5, MIN_PAIRS)
    assert key == {"provider_org_id": PROVIDER, "grantor_org_id": GRANTOR,
                   "judge_model": "judge-1", "rubric_version": 1}
    many = [(n, n % 2 == 0) for n in range(MIN_PAIRS)]
    gold = goldset.GoldSet.model_validate(gold_doc(
        [(n, "correct" if ok else "incorrect") for n, ok in many]))
    quality, ledger, _ = calibrate([stored(n, ok) for n, ok in many], gold)
    assert ledger.put[0][0]["state"] == "calibrated" == quality.state
    assert CalibrationLabel.correct.value == "correct"
