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
from infrx.judge import (FINISH_REASON_LENGTH, ProviderRate, Rejected, Result, StaticRateTable,
                         TraceCandidate)

ORG_A, ORG_B = b.ORG_A, b.ORG_B
MODEL = b.MODEL
JUDGE_MODEL = "claude-opus-5"
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
SINCE = NOW - timedelta(days=7)
#: `builders.consent` is effective from this instant, so a candidate must be at or after
#: it to be inside the consent window (R56).
CONSENT_FROM = datetime(2026, 9, 1, tzinfo=timezone.utc)


def uuid(n: int) -> str:
    return f"{n:08x}-0000-4000-8000-{n:012x}"


def consent(org_id: str = ORG_A, **kw) -> ConsentSnapshot:
    return b.consent(org_id, **kw)


def feedback(request_id: str, *, org_id: str = ORG_A, name: FeedbackName = FeedbackName.rating,
             value: object = 4, role: AuthorRole = AuthorRole.customer,
             calibration_set: bool = False, rubric_version: int | None = None,
             by_operator: bool | None = None, principal: str = "member-1") -> Feedback:
    """One persisted feedback row. A calibration label is the *same* record with
    `name=calibration_label` (r1 R43), which is what the sampler must distinguish."""
    if by_operator is None:
        by_operator = role is AuthorRole.operator
    return Feedback(feedback_id="fb_" + request_id.replace("-", "")[:32], request_id=request_id,
                    org_id=org_id, author_principal=principal, author_role=role,
                    channel=FeedbackChannel.console, name=name, value=value,
                    calibration_set=calibration_set, rubric_version=rubric_version,
                    by_operator=by_operator, created_at=NOW)


def label(request_id: str, *, org_id: str = ORG_A, rubric_version: int = 1,
          verdict: CalibrationLabel = CalibrationLabel.correct) -> Feedback:
    return feedback(request_id, org_id=org_id, name=FeedbackName.calibration_label,
                    value=verdict.value, role=AuthorRole.operator, calibration_set=True,
                    rubric_version=rubric_version, principal="operator@infrx.test")


def candidate(n: int, *, org_id: str = ORG_A, mode: TraceMode = TraceMode.full,
              content: ContentState = ContentState.available, media: bool = True,
              entries: tuple[Feedback, ...] = (), started_at: datetime | None = None,
              http_status: int = 200, finish_reason: str | None = "stop",
              schema_valid: bool = True) -> TraceCandidate:
    """R56: the candidate carries raw facts; the sampler derives the strata."""
    return TraceCandidate(request_id=uuid(n), org_id=org_id, trace_mode=mode,
                          content_state=content, started_at=started_at or NOW,
                          model_revision=MODEL, http_status=http_status,
                          finish_reason=finish_reason, schema_valid=schema_valid,
                          media_available=media, feedback=entries)


def truncated(n: int, **kw) -> TraceCandidate:
    """A failure-stratum candidate: `06` §3.1's `finish_reason = length`."""
    return candidate(n, finish_reason=FINISH_REASON_LENGTH, **kw)


def population(*, uniform: int = 40, failures: int = 20, feedback_bearing: int = 20,
               org_id: str = ORG_A) -> tuple[TraceCandidate, ...]:
    """More candidates than the design draws in every stratum, so every bound bites."""
    out: list[TraceCandidate] = []
    n = 1
    for _ in range(uniform):
        out.append(candidate(n, org_id=org_id))
        n += 1
    for _ in range(failures):
        out.append(truncated(n, org_id=org_id))
        n += 1
    for _ in range(feedback_bearing):
        out.append(candidate(n, org_id=org_id,
                             entries=(feedback(uuid(n), org_id=org_id),)))
        n += 1
    return tuple(out)


class FakeCandidateSource:
    """`judge.CandidateSource`, honouring its contract: it truncates to `limit`.

    Records its arguments so a test can prove the port was called with the tenant and
    lookback it was given - and that it was **not** called at all when consent or
    ownership refuses.
    """

    def __init__(self, candidates: tuple[TraceCandidate, ...] = ()) -> None:
        self.rows = candidates
        self.calls: list[tuple[str, datetime, int]] = []

    async def candidates(self, org_id: str, *, since: datetime,
                         limit: int) -> tuple[TraceCandidate, ...]:
        self.calls.append((org_id, since, limit))
        return tuple(self.rows)[:limit]


class OverReturningSource(FakeCandidateSource):
    """A source that **ignores `limit`** - a query without a `LIMIT`, a projection
    replaying a backlog, an adapter someone wrote in a hurry. The sampler's bound has to
    hold anyway, which is exactly what the well-behaved fake above cannot show."""

    async def candidates(self, org_id: str, *, since: datetime,
                         limit: int) -> tuple[TraceCandidate, ...]:
        self.calls.append((org_id, since, limit))
        return tuple(self.rows)


class UnboundedSource(FakeCandidateSource):
    """A source whose answer is a **generator** of effectively unlimited rows, and which
    fails loudly if anything consumes more than the bound.

    This is what proves the consumption is bounded rather than merely the result:
    `tuple(...)[:limit]` would drain every row here before slicing.
    """

    def __init__(self, template: TraceCandidate, *, total: int = 1_000_000) -> None:
        super().__init__(())
        self.template = template
        self.total = total
        self.consumed = 0

    async def candidates(self, org_id: str, *, since: datetime,
                         limit: int):
        self.calls.append((org_id, since, limit))

        def rows():
            for n in range(1, self.total + 1):
                self.consumed = n
                if n > limit + 1:
                    raise AssertionError(f"the plan consumed {n} rows for a bound of {limit}")
                yield candidate(n, org_id=self.template.org_id,
                                started_at=self.template.started_at)

        return rows()


#: An approved-looking rate row for the estimate tests. It is **not** a price from
#: `research/cross-cutting/cloud-pricing.md` (which has no provider token rows); it is a
#: test input, which is exactly why the shipped `APPROVED_RATES` table is empty.
TEST_RATE = ProviderRate(price_version="test-rates-v1", model=JUDGE_MODEL,
                         input_per_million=Decimal("5"), output_per_million=Decimal("25"),
                         source="tests/j/fakes.py - test input, not an approved price",
                         effective_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
#: A dearer row that takes effect later, so "the row effective at the clock" is
#: observable and reversing the row order cannot change the answer (R57).
NEWER_RATE = ProviderRate(price_version="test-rates-v2", model=JUDGE_MODEL,
                          input_per_million=Decimal("10"), output_per_million=Decimal("50"),
                          source="tests/j/fakes.py - test input, not an approved price",
                          effective_at=datetime(2026, 9, 15, tzinfo=timezone.utc))
#: The **same rates** under a different price version, effective later. The only thing it
#: changes is the version, which is what isolates "the estimate's version must still be
#: the effective one" from every amount check (R2-B4).
RENAMED_RATE = ProviderRate(price_version="test-rates-v1-renamed", model=JUDGE_MODEL,
                            input_per_million=Decimal("5"), output_per_million=Decimal("25"),
                            source="tests/j/fakes.py - test input, not an approved price",
                            effective_at=datetime(2026, 9, 16, tzinfo=timezone.utc))
TEST_RATES = StaticRateTable((TEST_RATE,))
#: A price history whose newer row takes effect **between** `SINCE` and `NOW`, so "the plan
#: prices at `now`, not at `since`" is observable (R2-B4).
RATE_HISTORY = StaticRateTable((TEST_RATE, NEWER_RATE))


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


def rejection(value: Result) -> Rejected:
    """Assert first, then read `.reason`.

    A test that reads `.reason` off whatever came back turns "the validator accepted
    something it should have refused" into an `AttributeError` in the test body, which is
    a test that crashed rather than a test that failed - and a mutation runner cannot
    tell that from an honest assertion.

    The message names the *type* rather than repr-ing the value, because these cases feed
    the validator hostile input: `repr` of an accepted result carrying a 5,000-digit score
    raises `ValueError`, which would turn this assertion into a crash of its own.
    """
    assert isinstance(value, Rejected), f"expected a rejection, got {type(value).__name__}"
    return value
