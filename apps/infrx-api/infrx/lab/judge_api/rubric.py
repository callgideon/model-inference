"""AP-08 08b/08e: the versioned rubric and the judge result as the wire shows them.

The rubric itself is J1's data (`infrx.judge.rubric`); this module only says what a stored
result MEANS to a reader, so no screen derives it:

* a criterion that needs video, on a result judged without it (J2 stores `limited`), is
  `abstained` with `no_media` - never a score, never omitted, and the result never passes
  (R56 is already enforced at the record; this keeps it visible);
* a malformed judge answer (J2's `Rejected`) is `quarantined` with its bounded reason code -
  its `detail` stays operator-only;
* calibration is `calibrated` only with at least the required reference labels and an
  agreement; fewer is `insufficient`, none `uncalibrated` (contracts.md §8).
"""
from __future__ import annotations

from typing import Any, Literal

from ...contracts.api import Wire
from pydantic import Field

from ...judge import rubric as j
from ...judge.rubric import NOTES, OVERALL_PASS, RUBRICS, Criterion, Rubric

Evidence = Literal["media", "text"]


class CriterionDoc(Wire):
    name: str
    min_score: int
    max_score: int
    pass_at: int | None
    evidence: Evidence


class RubricDoc(Wire):
    """A rubric version. `active`: immutable, `digest` its identity, gradable. A
    `definition_pending` version is a reserved skeleton (criteria and evidence only): no
    digest, no output schema, never configured (P-07)."""

    rubric_id: str
    version: int
    state: Literal["active", "definition_pending"] = "active"
    criteria: tuple[CriterionDoc, ...]
    sop_steps: tuple[str, ...] = ()
    max_rationale_chars: int
    max_notes_chars: int
    output_schema: dict[str, Any] | None = None
    digest: str | None = None
    review_ref: str | None = None
    pending_reason: str | None = None


class CriterionBody(Wire):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    min_score: int = Field(default=1, ge=-1000, le=1000)
    max_score: int = Field(default=5, ge=-1000, le=1000)
    pass_at: int | None = Field(default=None, ge=-1000, le=1000)
    evidence: Evidence


class RubricBody(Wire):
    """A reviewed rubric definition (`review_ref` names its review, e.g. the P-07 SOP
    sign-off) that becomes an immutable version."""

    rubric_id: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{0,63}$")
    version: int = Field(ge=1, le=1000)
    criteria: tuple[CriterionBody, ...] = Field(min_length=1, max_length=32)
    min_rationale_chars: int = Field(default=1, ge=1, le=4000)
    max_rationale_chars: int = Field(default=300, ge=1, le=4000)
    max_notes_chars: int = Field(default=500, ge=1, le=4000)
    sop_steps: tuple[str, ...] = Field(default=(), max_length=j.MAX_SOP_STEPS)
    review_ref: str = Field(min_length=1, max_length=400)

    def definition(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"review_ref"})


class CriterionResult(Wire):
    name: str
    state: Literal["scored", "abstained"]
    score: int | None = None
    max_score: int | None = None
    rationale: str | None = None
    evidence: Evidence
    abstain_reason: Literal["no_media"] | None = None


class SampleResult(Wire):
    label_id: str
    sample_id: str
    rubric_version: int
    state: Literal["scored", "quarantined"]
    overall_pass: bool | None = None
    limited: bool = False
    criteria: tuple[CriterionResult, ...] = ()
    quarantine_reason: str | None = None
    recorded_at: str


class CalibrationDoc(Wire):
    state: Literal["calibrated", "insufficient", "uncalibrated"]
    labels: int
    required: int
    agreement: float | None = None
    interval: tuple[float, float] | None = None


def _evidence(criterion: Criterion) -> Evidence:
    return "media" if criterion.requires_media else "text"


def _schema(r: Rubric) -> dict[str, Any]:
    """The judge's output contract J1's validator enforces (closed key set)."""
    item = {"type": "object", "additionalProperties": False, "required": ["score", "rationale"],
            "properties": {"score": {"type": "integer"},
                           "rationale": {"type": "string", "minLength": r.min_rationale_chars,
                                         "maxLength": r.max_rationale_chars}}}
    props: dict[str, Any] = {c.name: {**item, "properties": {
        **item["properties"], "score": {"type": "integer", "minimum": c.min_score,
                                        "maximum": c.max_score}}} for c in r.criteria}
    props[OVERALL_PASS] = {"type": "boolean"}
    props[NOTES] = {"type": "string", "maxLength": r.max_notes_chars}
    return {"type": "object", "additionalProperties": False, "properties": props,
            "required": [*(c.name for c in r.criteria), OVERALL_PASS, NOTES]}


def rubric_doc(r: Rubric, review_ref: str | None = None,
               pending: str | None = None) -> RubricDoc:
    return RubricDoc(rubric_id=r.rubric_id, version=r.version, criteria=tuple(
        CriterionDoc(name=c.name, min_score=c.min_score, max_score=c.max_score,
                     pass_at=c.pass_at, evidence=_evidence(c)) for c in r.criteria),
        sop_steps=r.sop_steps, max_rationale_chars=r.max_rationale_chars,
        max_notes_chars=r.max_notes_chars, review_ref=review_ref,
        **({"state": "definition_pending", "pending_reason": pending} if pending else
           {"output_schema": _schema(r), "digest": j.digest(r)}))


def stored_doc(row: dict[str, Any]) -> RubricDoc:
    """SR-AP08-1's stored version (its definition re-validated on the way out)."""
    return rubric_doc(j.from_definition(row["definition"]), review_ref=row["review_ref"])


def rubrics(stored: dict[int, dict[str, Any]]) -> tuple[RubricDoc, ...]:
    """The code versions, the stored ones and the still-pending skeletons, by version."""
    docs = {v: rubric_doc(r) for v, r in RUBRICS.items()}
    docs.update({v: rubric_doc(skeleton, pending=why)
                 for v, (skeleton, why) in j.PENDING.items() if v not in stored})
    docs.update({v: stored_doc(row) for v, row in stored.items()})
    return tuple(docs[v] for v in sorted(docs))


def project(row: dict[str, Any], known: dict[int, Rubric] = RUBRICS) -> SampleResult:
    """One stored result (0064 `lab_judge_run_results`) as the wire shows it."""
    base = {"label_id": str(row["label_id"]), "sample_id": str(row["sample_id"]),
            "rubric_version": row["rubric_version"], "recorded_at": str(row["recorded_at"])}
    result = row["result"]
    if not row["accepted"]:
        return SampleResult(**base, state="quarantined", quarantine_reason=result["reason"])
    limited = bool(result["limited"])
    scored = {s["name"]: s for s in result["scores"]}
    r = known.get(row["rubric_version"])
    criteria = r.criteria if r is not None else tuple(Criterion(n) for n in scored)
    shown = []
    for c in criteria:
        s = scored.get(c.name)
        if s is None:
            if not (limited and c.requires_media):
                raise ValueError(f"a stored result lacks {c.name}")   # J1 never stores this
            shown.append(CriterionResult(name=c.name, state="abstained", evidence="media",
                                         abstain_reason="no_media"))
        else:
            shown.append(CriterionResult(name=c.name, state="scored", score=s["score"],
                                         max_score=c.max_score, rationale=s["rationale"],
                                         evidence=_evidence(c)))
    return SampleResult(**base, state="scored", overall_pass=bool(result["overall_pass"]),
                        limited=limited, criteria=tuple(shown))


def calibration(c: dict[str, Any]) -> CalibrationDoc:
    """J3's stored calibration (0043) re-checked: never `calibrated` below the threshold."""
    labels, required, agreement = int(c["labels"]), int(c["required"]), c.get("agreement")
    if c.get("state") == "calibrated" and labels >= required and agreement is not None:
        state = "calibrated"
    elif labels > 0:
        state = "insufficient"
    else:
        state = "uncalibrated"
    interval = c.get("interval")
    return CalibrationDoc(state=state, labels=labels, required=required, agreement=agreement,
                          interval=tuple(interval) if interval else None)
