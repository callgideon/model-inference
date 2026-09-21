"""Builders and the fake candidate source for the J suite.

Relative-import helper module (r1 R48): the track tests import it as `from . import
fakes`, never by a top-level name, because `--import-mode=importlib` collects track
directories before the legacy top-level files.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from infrx.contracts.conformance import builders as b
from infrx.contracts.records import (AuthorRole, CalibrationLabel, ConsentSnapshot, ContentState,
                                     Feedback, FeedbackChannel, FeedbackName, TraceMode)
from infrx.judge import ProviderRate, StaticRateTable, TraceCandidate

ORG_A, ORG_B = b.ORG_A, b.ORG_B
MODEL = b.MODEL
JUDGE_MODEL = "claude-opus-5"
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
SINCE = NOW - timedelta(days=7)


def uuid(n: int) -> str:
    return f"{n:08x}-0000-4000-8000-{n:012x}"


def consent(org_id: str = ORG_A, **kw) -> ConsentSnapshot:
    return b.consent(org_id, **kw)


def feedback(request_id: str, *, org_id: str = ORG_A, name: FeedbackName = FeedbackName.rating,
             value: object = 4, role: AuthorRole = AuthorRole.customer,
             calibration_set: bool = False, rubric_version: int | None = None,
             principal: str = "member-1") -> Feedback:
    """One persisted feedback row. A calibration label is the *same* record with
    `name=calibration_label` (r1 R43), which is what the sampler must distinguish."""
    return Feedback(feedback_id="fb_" + request_id.replace("-", "")[:32], request_id=request_id,
                    org_id=org_id, author_principal=principal, author_role=role,
                    channel=FeedbackChannel.console, name=name, value=value,
                    calibration_set=calibration_set, rubric_version=rubric_version,
                    by_operator=role is AuthorRole.operator, created_at=NOW)


def label(request_id: str, *, org_id: str = ORG_A, rubric_version: int = 1,
          verdict: CalibrationLabel = CalibrationLabel.correct) -> Feedback:
    return feedback(request_id, org_id=org_id, name=FeedbackName.calibration_label,
                    value=verdict.value, role=AuthorRole.operator, calibration_set=True,
                    rubric_version=rubric_version, principal="operator@infrx.test")


def candidate(n: int, *, org_id: str = ORG_A, mode: TraceMode = TraceMode.full,
              content: ContentState = ContentState.available, failure: bool = False,
              media: bool = True, entries: tuple[Feedback, ...] = (),
              started_at: datetime | None = None) -> TraceCandidate:
    return TraceCandidate(request_id=uuid(n), org_id=org_id, trace_mode=mode,
                          content_state=content, started_at=started_at or NOW,
                          model_revision=MODEL, failure=failure, media_available=media,
                          feedback=entries)


def population(*, uniform: int = 40, failures: int = 20, feedback_bearing: int = 20,
               org_id: str = ORG_A) -> tuple[TraceCandidate, ...]:
    """More candidates than the design draws in every stratum, so every bound bites."""
    out: list[TraceCandidate] = []
    n = 1
    for _ in range(uniform):
        out.append(candidate(n, org_id=org_id)); n += 1
    for _ in range(failures):
        out.append(candidate(n, org_id=org_id, failure=True)); n += 1
    for _ in range(feedback_bearing):
        out.append(candidate(n, org_id=org_id,
                             entries=(feedback(uuid(n), org_id=org_id),))); n += 1
    return tuple(out)


class FakeCandidateSource:
    """`judge.CandidateSource`. Records its arguments so a test can prove the port was
    called with the tenant and lookback it was given, not with something wider."""

    def __init__(self, candidates: tuple[TraceCandidate, ...] = ()) -> None:
        self.rows = candidates
        self.calls: list[tuple[str, datetime, int]] = []

    async def candidates(self, org_id: str, *, since: datetime,
                         limit: int) -> tuple[TraceCandidate, ...]:
        self.calls.append((org_id, since, limit))
        return tuple(self.rows)[:limit]


#: An approved-looking rate row for the estimate tests. It is **not** a price from
#: `research/cross-cutting/cloud-pricing.md` (which has no provider token rows); it is
#: a test input, which is exactly why the shipped `APPROVED_RATES` table is empty.
TEST_RATE = ProviderRate(price_version="test-rates-v1", model=JUDGE_MODEL,
                         input_per_million=Decimal("5"), output_per_million=Decimal("25"),
                         source="tests/j/fakes.py - test input, not an approved price")
TEST_RATES = StaticRateTable((TEST_RATE,))


def result(*, groundedness: int | None = 4, relevance: int = 4, completeness: int = 4,
           fmt: int = 4, refusal: int = 4, overall_pass: bool | None = None,
           notes: str = "fine") -> dict:
    """A well-formed judge payload for `MARLIN_VIDEO_V1`, tweakable per test."""
    def criterion(score: int) -> dict:
        return {"score": score, "rationale": "frame 3 shows it"}

    payload: dict = {"relevance": criterion(relevance), "completeness": criterion(completeness),
                     "format": criterion(fmt), "refusal": criterion(refusal), "notes": notes}
    if groundedness is not None:
        payload["groundedness"] = criterion(groundedness)
    if overall_pass is None:
        overall_pass = (groundedness is not None and relevance >= 4 and groundedness >= 4
                        and fmt >= 3 and refusal >= 3)
    payload["overall_pass"] = overall_pass
    return payload
