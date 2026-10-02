"""AP-08 (api-judge-2) 08e: the gold set - a human-reviewed reference set as J3's truth.

The operator supplies (P-07) a reviewed reference set for ONE judge configuration: who reviewed
it, the review record, the configuration's key (provider, grantor, judge model, rubric
version) and one verdict per sample id - ids only, never content. `labels` makes those
verdicts J3's operator `calibration_label` rows in memory (human ground truth; never teacher or
judge output, never written as feedback), and `calibrate` grades that configuration's stored
judge results (SR-AP08-1's `lab_judge_results_of`: completed runs, grant still current) with
J3's `report` and publishes it through D8's store: `calibrated` only when every statistic is
`met` at >= MIN_PAIRS pairs, `insufficient` below. It replaces nothing: J3's report and D8's
calibration store are reused as they are; this is only the reference set's path into them.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from collections.abc import Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...contracts.records import (AuthorRole, CalibrationLabel, Feedback, FeedbackChannel,
                                  FeedbackName)
from ..rubric import JudgeScores, Rejected, Score
from .report import QualityReport, publish

UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
#: Fixed forever: the namespace a gold label's feedback id is derived in.
NAMESPACE = uuid.UUID("0b8e3d7a-58a1-5a8e-9f0e-000000000a09")
MAX_LABELS = 1000


class GoldLabel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    sample_id: str = Field(pattern=UUID)
    verdict: CalibrationLabel


class GoldSet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    provider_org_id: str = Field(pattern=UUID)
    org_id: str = Field(pattern=UUID)                 # the grantor whose traces were judged
    judge_model: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]{0,63}$")
    rubric_version: int = Field(ge=1, le=1000)
    reviewed_by: str = Field(min_length=1, max_length=200)
    review_ref: str = Field(min_length=1, max_length=400)
    labels: tuple[GoldLabel, ...] = Field(min_length=1, max_length=MAX_LABELS)

    @model_validator(mode="after")
    def _one_verdict_per_sample(self) -> GoldSet:
        if len({label.sample_id for label in self.labels}) != len(self.labels):
            raise ValueError("a gold set labels each sample once")
        return self


ResultsOf = Callable[[GoldSet], Awaitable[list[dict[str, Any]]]]


def load(path: str) -> GoldSet:
    with open(path, "rb") as stored:
        return GoldSet.model_validate(json.loads(stored.read()))


def labels(gold: GoldSet, at: datetime | None = None) -> list[Feedback]:
    """The reviewed verdicts as J3's operator calibration labels of the configuration."""
    at = at or datetime.now(UTC)
    return [Feedback(feedback_id=str(uuid.uuid5(NAMESPACE, f"{gold.review_ref}\n{g.sample_id}")),
                     request_id=g.sample_id, org_id=gold.org_id,
                     author_principal=gold.reviewed_by, author_role=AuthorRole.operator,
                     channel=FeedbackChannel.console, name=FeedbackName.calibration_label,
                     value=g.verdict.value, calibration_set=True,
                     rubric_version=gold.rubric_version, by_operator=True, created_at=at)
            for g in gold.labels]


def stored_result(row: dict[str, Any]) -> JudgeScores | Rejected:
    """A stored result (J2's record, kept whole by D6J) as the record it was."""
    result = dict(row["result"])
    if not row["accepted"]:
        return Rejected(**result)
    result["scores"] = tuple(Score(**s) for s in result["scores"])
    return JudgeScores(**result)


async def calibrate(ledger, results_of: ResultsOf, gold: GoldSet) -> QualityReport:
    """The configuration's stored results against the gold set, published (J3/D8)."""
    rows = await results_of(gold)
    return await publish(ledger, [stored_result(r) for r in rows], labels(gold),
                         provider_org_id=gold.provider_org_id, org_id=gold.org_id,
                         judge_model=gold.judge_model, rubric_version=gold.rubric_version)


def pg_results_of(connect) -> ResultsOf:
    """SR-AP08-1's `infrx.lab_judge_results_of` on the judge worker's login."""
    from ...state import rpc
    from ...state.jobstore import domain_error

    async def results_of(gold: GoldSet) -> list[dict[str, Any]]:
        return await rpc.call(connect, "lab_judge_results_of", {
            "provider_org_id": gold.provider_org_id, "grantor_org_id": gold.org_id,
            "judge_model": gold.judge_model, "rubric_version": gold.rubric_version,
            "limit": MAX_LABELS}, error=domain_error)
    return results_of
