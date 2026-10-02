"""AP-08 08b/08e: what a stored judge result and a calibration say on the wire.

A result of a media-dependent rubric judged without video names each media criterion
`abstained` (`no_media`), never a score and never a pass; a malformed result (J2's `Rejected`)
is `quarantined` with its bounded reason only; calibration is `calibrated` only with enough
reference labels. Pure projection over `infrx.lab.judge_api.rubric` - no database.
"""
from __future__ import annotations

import dataclasses

from infrx.judge.rubric import MARLIN_VIDEO_V1, validate_output
from infrx.lab.judge_api import rubric

RUN, SAMPLE = "7a000000-0000-4000-8000-0000000000a1", "5a000000-0000-4000-8000-000000000001"


def stored(result) -> dict:
    """A row as 0064's `lab_judge_run_results` answers it (J2's record, stored whole)."""
    return {"label_id": "1abe1000-0000-4000-8000-000000000001", "sample_id": SAMPLE,
            "rubric_version": result.rubric_version, "accepted": result.accepted,
            "result": dataclasses.asdict(result), "recorded_at": "2026-10-01T00:00:00+00:00"}


def judged(media: bool) -> dict:
    payload = {c.name: {"score": 5, "rationale": "ok"}
               for c in MARLIN_VIDEO_V1.criteria_for(media=media)}
    payload.update(overall_pass=media, notes="")
    result = validate_output(MARLIN_VIDEO_V1, payload, run_id=RUN, sample_id=SAMPLE,
                             media_available=media)
    assert result.accepted, result
    return stored(result)


def test_ap08_rubric__missing_video_abstains_on_media_criteria_and_never_passes():
    """Failure oracle: a media criterion shown as scored/omitted without video, or a pass."""
    shown = rubric.project(judged(media=False))
    by_name = {c.name: c for c in shown.criteria}
    ground, relevance = by_name.get("groundedness"), by_name.get("relevance")
    assert ground is not None and ground.state == "abstained", shown.criteria
    assert ground.abstain_reason == "no_media"
    assert ground.score is None and ground.evidence == "media"
    assert relevance is not None and relevance.state == "scored" and relevance.score == 5
    assert (shown.state, shown.limited, shown.overall_pass) == ("scored", True, False)
    assert [c.name for c in shown.criteria] == [c.name for c in MARLIN_VIDEO_V1.criteria]


def test_ap08_rubric__a_full_result_scores_every_criterion_with_its_evidence():
    shown = rubric.project(judged(media=True))
    assert {c.state for c in shown.criteria} == {"scored"}
    assert shown.overall_pass is True and shown.limited is False
    assert {c.name: c.evidence for c in shown.criteria}["groundedness"] == "media"
    assert {c.name: c.rationale for c in shown.criteria}["relevance"] == "ok"


def test_ap08_rubric__malformed_judge_output_is_quarantined_without_its_detail():
    """Failure oracle: a rejected result rendered as scored, or its detail on the wire."""
    bad = validate_output(MARLIN_VIDEO_V1, {"relevance": 9}, run_id=RUN, sample_id=SAMPLE,
                          media_available=True)
    shown = rubric.project(stored(bad))
    assert (shown.state, shown.quarantine_reason) == ("quarantined", bad.reason)
    assert shown.criteria == () and shown.overall_pass is None
    assert "detail" not in shown.model_dump_json()


def test_ap08_rubric__calibration_needs_enough_reference_labels():
    """08e: `calibrated` only at/above the required labels with an agreement; below it is
    `insufficient`, none `uncalibrated`. Failure oracle: a stored or forged `calibrated` with
    too few labels displayed as calibrated."""
    def state(**c):
        base = {"state": "calibrated", "labels": 30, "required": 30, "agreement": 0.8,
                "interval": [0.7, 0.9]}
        return rubric.calibration({**base, **c}).state
    assert state() == "calibrated"
    assert state(labels=29) == "insufficient"
    assert state(state="insufficient") == "insufficient"
    assert state(agreement=None) == "insufficient"
    assert state(labels=0, state="uncalibrated", agreement=None) == "uncalibrated"


def test_ap08_rubric__the_registry_documents_evidence_and_the_output_schema():
    doc, pending = rubric.rubrics({})
    assert (pending.version, pending.state) == (2, "definition_pending")
    assert (doc.rubric_id, doc.version) == ("marlin-video-v1", 1)
    assert {c.name: c.evidence for c in doc.criteria}["groundedness"] == "media"
    assert set(doc.output_schema["required"]) >= {"overall_pass", "notes", "relevance"}
    assert rubric.RUBRICS[1] is MARLIN_VIDEO_V1
