"""Deterministic, consented selection of judge samples (J1).

Three rules do the work here, and each of them exists because the obvious
implementation is wrong:

* **Trace opt-in is not evaluation consent.** A `full`-mode trace says the customer
  agreed to *store* content; `ConsentSnapshot.allows_evaluation` says they agreed to
  send it to a third party. The gate is the **current** consent record, because the
  row a customer revokes is the live one (02, r1 R9).
* **The seed decides, not the scan order.** Selection ranks a candidate by a keyed
  hash of its id, so a run that dies half way through re-selects the same traces
  instead of re-rolling the dice (`research/traces/06` §3.1 uses `cityHash64` for the
  same reason).
* **Ordinary customer feedback is not a calibration label.** The feedback-bearing
  stratum is chosen *from* customer feedback; calibration membership is only a
  `calibration_label` entry with `calibration_set` (r1 R43). Conflating the two would
  let the console's thumbs-up count as an operator verdict and let the calibration
  report grade the judge against itself.

Nothing in this module performs I/O. `CandidateSource` is the injected port; T's
ClickHouse projection and D's feedback table implement it after integration.
"""
from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from ..contracts import errors
from ..contracts.records import AuthorRole, ConsentSnapshot, ContentState, Feedback, TraceMode


class Stratum(enum.StrEnum):
    """`research/traces/06` §3.9's calibration design, in priority order.

    `failures` before `feedback` before `uniform` is `06` §3.1's ordering
    (always -> feedback -> sample): a truncated or erroring answer is the most
    informative thing an operator can label, so it claims its trace first.
    """

    failures = "failures"
    feedback = "feedback"
    uniform = "uniform"


class Exclusion(enum.StrEnum):
    """Why a candidate is not a sample. Every excluded trace is reported, so
    "why was this not judged" is a lookup rather than a guess (`06` §3.1)."""

    not_owned = "not_owned"
    trace_mode_not_full = "trace_mode_not_full"
    content_lost = "content_lost"
    content_expired = "content_expired"
    content_missing = "content_missing"
    already_calibrated = "already_calibrated"
    stratum_full = "stratum_full"


# Content availability (08 §9) -> why it cannot be evaluated. Only `available`
# content can leave, and the three unavailable reasons are kept apart because an
# expired trace is a retention decision while a lost one is a capture failure.
_CONTENT_EXCLUSION: dict[ContentState, Exclusion] = {
    ContentState.lost: Exclusion.content_lost,
    ContentState.expired: Exclusion.content_expired,
    ContentState.pending: Exclusion.content_missing,
    ContentState.metadata_only: Exclusion.content_missing,
    ContentState.off: Exclusion.content_missing,
}


@dataclass(frozen=True)
class CalibrationDesign:
    """`06` §3.9: ~50 labels, 25 uniform / 15 failures / 10 feedback-bearing.

    A stratum that cannot be filled is **not** backfilled from another one: J3
    computes agreement per stratum, and a design silently rewritten into 50 uniform
    samples would report a calibration it never ran. The shortfall is reported.
    """

    uniform: int = 25
    failures: int = 15
    feedback: int = 10

    def __post_init__(self) -> None:
        for name in ("uniform", "failures", "feedback"):
            size = getattr(self, name)
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                raise ValueError(f"the {name} stratum size must be a nonnegative integer")

    @property
    def total(self) -> int:
        return self.uniform + self.failures + self.feedback

    def size_of(self, stratum: Stratum) -> int:
        return getattr(self, stratum.value)


DEFAULT_DESIGN = CalibrationDesign()


@dataclass(frozen=True)
class TraceCandidate:
    """One eligible-looking trace, as the candidate source reports it.

    `feedback` carries the trace's feedback *rows* rather than two booleans, so the
    "a customer signal is not a calibration label" rule is decided from the persisted
    shape (r1 R43) instead of from a flag whoever wrote the query chose.
    """

    request_id: str
    org_id: str
    trace_mode: TraceMode
    content_state: ContentState
    started_at: datetime
    model_revision: str
    failure: bool = False
    media_available: bool = False
    feedback: tuple[Feedback, ...] = ()

    @property
    def calibration_labels(self) -> tuple[Feedback, ...]:
        """r1 R43: membership is the boolean on a `calibration_label` entry. The
        record already refuses any disagreement between name, flag and rubric
        version, so this one field is the whole test."""
        return tuple(entry for entry in self.feedback if entry.calibration_set)

    @property
    def has_customer_feedback(self) -> bool:
        """The `feedback` stratum: a customer told us something about this answer.
        A judge's own score is not customer feedback, and a label is not either."""
        return any(entry.author_role is AuthorRole.customer and not entry.calibration_set
                   for entry in self.feedback)

    def labelled_against(self, rubric_version: int) -> bool:
        return any(entry.rubric_version == rubric_version
                   for entry in self.calibration_labels)


class CandidateSource(Protocol):
    """J's read side over T's trace projection. Injected; faked in tests.

    It is a *port*, not a query helper: the adapter owns the tenant filter, the
    `full`-mode filter and the lookback, and this module re-checks every one of them
    anyway, because a selection that trusts its query is a selection that ships one
    org's traces the day the query grows a join.
    """

    async def candidates(self, org_id: str, *, since: datetime,
                         limit: int) -> tuple[TraceCandidate, ...]:
        ...


@dataclass(frozen=True)
class Sample:
    sample_id: str
    request_id: str
    stratum: Stratum
    model_revision: str
    # No media means no groundedness, so the evaluation is `limited` and can never be
    # a pass (02). Carried on the sample so the prompt, the validator and the report
    # all read one fact.
    limited: bool = False


@dataclass(frozen=True)
class Excluded:
    request_id: str
    reason: Exclusion


@dataclass(frozen=True)
class Selection:
    samples: tuple[Sample, ...] = ()
    excluded: tuple[Excluded, ...] = ()
    counts: dict[Stratum, int] = field(default_factory=dict)
    shortfall: dict[Stratum, int] = field(default_factory=dict)

    @property
    def sample_ids(self) -> tuple[str, ...]:
        return tuple(sample.sample_id for sample in self.samples)

    @property
    def limited(self) -> tuple[Sample, ...]:
        return tuple(sample for sample in self.samples if sample.limited)


def rank(seed: str, request_id: str) -> int:
    """A keyed hash, so "seeded" means reproducible rather than merely shuffled.

    Ordering by this instead of by arrival makes selection independent of the scan
    order: the same seed, rubric version and candidate set always pick the same
    traces, which is what lets a crashed run resume without re-rolling the dice.
    """
    digest = hashlib.blake2b(f"{seed}\x00{request_id}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def _stratum_of(candidate: TraceCandidate) -> Stratum:
    if candidate.failure:
        return Stratum.failures
    if candidate.has_customer_feedback:
        return Stratum.feedback
    return Stratum.uniform


def select(org_id: str, consent: ConsentSnapshot, candidates: tuple[TraceCandidate, ...], *,
           rubric_version: int, seed: str, now: datetime,
           design: CalibrationDesign = DEFAULT_DESIGN) -> Selection:
    """The seeded, stratified, consented draw. Pure: no clock, no I/O, no network."""
    if consent.org_id != org_id:
        # r1 R10: one org's consent never authorizes another org's traces leaving.
        raise errors.NotFound(f"consent for org {consent.org_id} does not match org {org_id}")
    if not consent.allows_evaluation(now):
        # The whole point of the gate: `full` mode alone is storage consent, and a
        # revoked or not-yet-effective record is not consent at all.
        raise errors.ConsentMissing(f"org {org_id} has no current evaluation consent")

    eligible: dict[Stratum, list[TraceCandidate]] = {stratum: [] for stratum in Stratum}
    excluded: list[Excluded] = []
    for candidate in candidates:
        reason = _ineligible(candidate, org_id, rubric_version)
        if reason is not None:
            excluded.append(Excluded(candidate.request_id, reason))
            continue
        eligible[_stratum_of(candidate)].append(candidate)

    samples: list[Sample] = []
    counts: dict[Stratum, int] = {}
    shortfall: dict[Stratum, int] = {}
    for stratum in Stratum:
        size = design.size_of(stratum)
        ordered = sorted(eligible[stratum], key=lambda c: (rank(seed, c.request_id), c.request_id))
        chosen, overflow = ordered[:size], ordered[size:]
        samples.extend(Sample(sample_id=c.request_id, request_id=c.request_id, stratum=stratum,
                              model_revision=c.model_revision, limited=not c.media_available)
                       for c in chosen)
        excluded.extend(Excluded(c.request_id, Exclusion.stratum_full) for c in overflow)
        counts[stratum] = len(chosen)
        shortfall[stratum] = size - len(chosen)
    return Selection(samples=tuple(samples), excluded=tuple(excluded),
                     counts=counts, shortfall=shortfall)


def _ineligible(candidate: TraceCandidate, org_id: str, rubric_version: int) -> Exclusion | None:
    if candidate.org_id != org_id:
        return Exclusion.not_owned
    if candidate.trace_mode is not TraceMode.full:
        return Exclusion.trace_mode_not_full
    if candidate.content_state is not ContentState.available:
        return _CONTENT_EXCLUSION[candidate.content_state]
    if candidate.labelled_against(rubric_version):
        # Already labelled against *this* rubric version. A new version is a new
        # series (`06` §3.7 / J4), so the same trace is a candidate again there.
        return Exclusion.already_calibrated
    return None
