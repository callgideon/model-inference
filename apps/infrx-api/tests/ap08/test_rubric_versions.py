"""AP-08 (api-judge-2): versioned rubrics. A rubric version is immutable data with a digest; a
reviewed definition becomes a NEW stored version through `POST /lab/v1/judge/rubrics` (an
operator's act, SQL-checked in SR-AP08-1's door; the PostgreSQL half is
`test_rubric_store_pg.py`); the SOP rubric v2 is a reserved skeleton listed
`definition_pending` until the operator supplies the SOP definition (P-07), and no
configuration can pin it before then.
"""
from __future__ import annotations

import dataclasses

import pytest

from infrx.judge import rubric as r
from infrx.gateway.routes import lab_judge

from .test_lab_judge_routes import NEMO, Q, FakeDoors, client

STEPS = ("Open the cabinet", "Remove the filter", "Fit the new filter")


def sop_body(**over) -> dict:
    """The SOP v2 skeleton filled in: its criteria and evidence, thresholds and the steps."""
    criteria = [{"name": c.name, "min_score": 1, "max_score": 5, "pass_at": 4,
                 "evidence": "media" if c.requires_media else "text"}
                for c in r.SOP_VIDEO_V2.criteria]
    return {"rubric_id": r.SOP_VIDEO_V2.rubric_id, "version": 2, "criteria": criteria,
            "sop_steps": list(STEPS), "review_ref": "P-07 SOP sign-off 2026-10-02", **over}


def test_ap08_versions__a_definition_round_trips_and_its_digest_is_its_identity():
    """Failure oracle: a definition that loses a threshold or the evidence rule in storage,
    or a digest blind to a threshold (two different rubrics answering one identity)."""
    doc = r.definition(r.MARLIN_VIDEO_V1)
    assert r.from_definition(doc) == r.MARLIN_VIDEO_V1
    assert r.digest(r.MARLIN_VIDEO_V1) == r.digest(r.from_definition(doc))
    assert r.digest(r.MARLIN_VIDEO_V1).startswith("sha256:")
    looser = dataclasses.replace(r.MARLIN_VIDEO_V1, criteria=(
        dataclasses.replace(r.MARLIN_VIDEO_V1.criteria[0], pass_at=3),
        *r.MARLIN_VIDEO_V1.criteria[1:]))
    assert r.digest(looser) != r.digest(r.MARLIN_VIDEO_V1)


@pytest.mark.parametrize("edit", [
    lambda d: {**d, "extra": 1},
    lambda d: {k: v for k, v in d.items() if k != "criteria"},
    lambda d: {**d, "criteria": []},
    lambda d: {**d, "criteria": [{**d["criteria"][0], "evidence": "vibes"}]},
    lambda d: {**d, "criteria": [d["criteria"][0], d["criteria"][0]]},
    lambda d: {**d, "criteria": [{**d["criteria"][0], "colour": "red"}]},
    lambda d: {**d, "sop_steps": [""]},
    lambda d: {**d, "version": True},
])
def test_ap08_versions__a_malformed_definition_is_refused(edit):
    """Failure oracle: an unknown key, no criteria, an unknown evidence kind, a duplicate
    criterion, an empty SOP step or a boolean version stored as a rubric."""
    with pytest.raises(ValueError):
        r.from_definition(edit(r.definition(r.MARLIN_VIDEO_V1)))


def test_ap08_versions__the_sop_v2_skeleton_is_definition_pending():
    """v2 is reserved for the SOP rubric: its criteria and evidence are fixed (instruction
    following and output validity read the text; step evidence and task correctness need the
    video), its steps are not. A definition for v2 fills the skeleton with the SOP steps -
    it cannot rename it or drop a media criterion. Failure oracle: a v2 graded before the
    SOP exists, or a v2 that answers task correctness from the text alone."""
    assert 2 in r.PENDING and 2 not in r.RUBRICS
    skeleton = r.SOP_VIDEO_V2
    assert {c.name for c in skeleton.criteria if c.requires_media} == \
        {"step_evidence", "task_correctness"}
    assert {c.name for c in skeleton.criteria if not c.requires_media} == \
        {"instruction_following", "output_validity"}
    body = sop_body()
    review = body.pop("review_ref")
    assert review
    filled = r.from_definition({**body, "min_rationale_chars": 1, "max_rationale_chars": 300,
                                "max_notes_chars": 500})
    assert filled.sop_steps == STEPS and filled.requires_media
    base = {**body, "min_rationale_chars": 1, "max_rationale_chars": 300, "max_notes_chars": 500}
    with pytest.raises(ValueError, match="skeleton"):
        r.from_definition({**base, "rubric_id": "sop-other"})
    with pytest.raises(ValueError, match="skeleton"):
        r.from_definition({**base, "criteria": [{**c, "evidence": "text"}
                                                for c in base["criteria"]]})
    with pytest.raises(ValueError, match="SOP"):
        r.from_definition({**base, "sop_steps": []})


def test_ap08_routes__rubrics_list_their_state_and_a_pending_one_is_never_configured():
    """GET lists the code version active with its digest and v2 `definition_pending` with its
    reason; a configuration pinning v2 is 409 and never reaches the door.
    Failure oracle: a pending rubric offered as gradable, or a configuration of it queued."""
    doors = FakeDoors()
    c = client(doors)
    listed = {d["version"]: d for d in c.get(f"{lab_judge.PREFIX}/rubrics", params=Q)
              .json()["data"]}
    assert (listed[1]["state"], listed[1]["digest"]) == ("active", r.digest(r.MARLIN_VIDEO_V1))
    assert listed[2]["state"] == "definition_pending" and "P-07" in listed[2]["pending_reason"]
    assert listed[2]["digest"] is None and listed[2]["output_schema"] is None
    assert {x["name"]: x["evidence"] for x in listed[2]["criteria"]}["task_correctness"] == \
        "media"
    pinned = c.post(f"{lab_judge.PREFIX}/configs", params=Q, headers={"Idempotency-Key": "c"},
                    json={"grantor_org_id": NEMO, "model_id": NEMO, "judge_model": "judge-1",
                          "rubric_version": 2, "sample_size": 5})
    assert pinned.status_code == 409 and pinned.json()["error"]["code"] == "state_conflict"
    assert not any(d == "lab_judge_configure_keyed" for _, d, *_ in doors.calls)


def test_ap08_routes__a_reviewed_definition_becomes_one_immutable_version():
    """POST stores the definition once under its version with its digest and review record;
    the same definition again answers the stored version; another definition for it is 409;
    a code version is immutable (409); a malformed one 422; once stored, v2 is active and a
    configuration may pin it. Failure oracle: a version overwritten, a code version shadowed,
    an unreviewed definition stored, a stored version still refused."""
    doors = FakeDoors()
    c = client(doors)

    def post(key: str = "rb-1", **body):
        return c.post(f"{lab_judge.PREFIX}/rubrics", params=Q,
                      headers={"Idempotency-Key": key}, json=sop_body(**body))

    first = post()
    assert first.status_code == 201, first.text
    doc = first.json()
    assert (doc["version"], doc["state"], doc["sop_steps"]) == (2, "active", list(STEPS))
    assert doc["review_ref"] == "P-07 SOP sign-off 2026-10-02" and doc["digest"]
    (_, _, sent), = [(u, d, *a) for u, d, *a in doors.calls if d == "lab_judge_rubric_create"]
    assert sent["digest"] == doc["digest"] and "review_ref" not in sent["definition"]
    assert post("rb-2").json()["digest"] == doc["digest"], "a replay is the stored version"
    moved = post("rb-3", sop_steps=["Open the cabinet"])
    assert moved.status_code == 409 and moved.json()["error"]["code"] == "idempotency_conflict"
    code_version = c.post(f"{lab_judge.PREFIX}/rubrics", params=Q,
                          headers={"Idempotency-Key": "rb-4"},
                          json={**r.definition(r.MARLIN_VIDEO_V1), "review_ref": "x"})
    assert code_version.status_code == 409, code_version.text
    assert post("rb-5", review_ref="").status_code == 422
    assert post("rb-6", rubric_id="sop-other").status_code == 422
    listed = {d["version"]: d for d in c.get(f"{lab_judge.PREFIX}/rubrics", params=Q)
              .json()["data"]}
    assert listed[2]["state"] == "active" and listed[2]["digest"] == doc["digest"]
    pinned = c.post(f"{lab_judge.PREFIX}/configs", params=Q, headers={"Idempotency-Key": "c"},
                    json={"grantor_org_id": NEMO, "model_id": NEMO, "judge_model": "judge-1",
                          "rubric_version": 2, "sample_size": 5})
    assert pinned.status_code == 201, pinned.text


def test_ap08_routes__a_stored_versions_results_abstain_on_its_media_criteria():
    """A result of a stored version (SOP v2) judged without video names that version's media
    criteria `abstained` - the projection reads the stored rubric, not the code registry.
    Failure oracle: step evidence and task correctness silently omitted from a no-media
    result of the SOP rubric."""
    import json

    from infrx.judge.rubric import validate_json

    doors = FakeDoors()
    c = client(doors)
    assert c.post(f"{lab_judge.PREFIX}/rubrics", params=Q, headers={"Idempotency-Key": "rb"},
                  json=sop_body()).status_code == 201
    sop = r.from_definition(doors.rubrics[2]["definition"])
    payload = {x.name: {"score": 4, "rationale": "ok"} for x in sop.criteria_for(media=False)}
    payload.update(overall_pass=False, notes="")
    result = validate_json(sop, json.dumps(payload), run_id="7a000000-0000-4000-8000-0000000000a1",
                           sample_id="5a000000-0000-4000-8000-000000000001",
                           media_available=False)
    doors.results = [{"label_id": "1abe1000-0000-4000-8000-000000000001",
                      "sample_id": result.sample_id, "rubric_version": 2,
                      "accepted": True, "result": dataclasses.asdict(result),
                      "recorded_at": "2026-10-02T00:00:00+00:00"}]
    shown = c.get(f"{lab_judge.PREFIX}/runs/7a000000-0000-4000-8000-0000000000a1/results",
                  params=Q).json()["data"][0]
    states = {x["name"]: x["state"] for x in shown["criteria"]}
    assert states == {"instruction_following": "scored", "output_validity": "scored",
                      "step_evidence": "abstained", "task_correctness": "abstained"}
