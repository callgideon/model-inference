"""Deterministic, consented selection of judge samples (J1, ruling R56).

Rules that exist because the obvious implementation is wrong:

* **Trace opt-in is not evaluation consent, and consent is not retroactive.** A
  `full`-mode trace says the customer agreed to *store* content;
  `ConsentSnapshot.allows_evaluation` says they agreed to send it to a third party.
  The gate is the **current** consent record (02, r1 R9), and only traces created
  inside that record's window are eligible (R56) - consent granted today does not
  authorize last month's traces.
* **The seed decides, not the scan order.** Selection ranks a candidate by a keyed
  hash of its id, so a run that dies half way re-selects the same traces instead of
  re-rolling the dice (`research/traces/06` §3.1 uses `cityHash64` for this).
* **The source is data, not authority.** A ClickHouse projection can return the same
  trace twice (which is why `06` §3.1 queries `FINAL`), rows for another tenant if a
  join grows, and feedback rows belonging to some other request. So candidates are
  deduplicated by `request_id` before stratifying, a *conflicting* duplicate is
  excluded rather than sampled twice (R56), the tenant is re-checked here, and a
  feedback row counts only when its organization **and** request match the
  candidate's.
* **Ordinary customer feedback is not a calibration label.** The feedback-bearing
  stratum is chosen from customer signal; calibration membership is only a
  `calibration_label` entry with `calibration_set` (r1 R43). A `by_operator` ordinary
  entry is neither (R56): the platform typed it, so it is not the customer telling us
  something.

Nothing in this module performs I/O. `CandidateSource` is the injected port.
"""
from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Protocol

from ..contracts import errors, limits
from ..contracts.records import AuthorRole, ConsentSnapshot, ContentState, Feedback, TraceMode

#: `research/traces/06` §3.1: a truncated answer is the failure stratum's first member.
FINISH_REASON_LENGTH = "length"
#: R56: a 5xx produced no answer to grade.
NO_OUTPUT_STATUS = 500


class Stratum(enum.StrEnum):
    """`06` §3.9's calibration design, in `06` §3.1's priority order (always ->
    feedback -> sample): a truncated or schema-invalid answer is the most informative
    thing an operator can label, so it claims its trace first."""

    failures = "failures"
    feedback = "feedback"
    uniform = "uniform"


class Exclusion(enum.StrEnum):
    """Why a candidate is not a sample. Every excluded row is reported, so "why was
    this not judged" is a lookup rather than a guess (`06` §3.1)."""

    not_owned = "not_owned"
    trace_mode_not_full = "trace_mode_not_full"
    outside_consent_window = "outside_consent_window"
    skipped_no_output = "skipped_no_output"
    content_lost = "content_lost"
    content_expired = "content_expired"
    content_missing = "content_missing"
    already_calibrated = "already_calibrated"
    malformed_row = "malformed_row"
    duplicate_row = "duplicate_row"
    conflicting_duplicate = "conflicting_duplicate"
    stratum_full = "stratum_full"


# Content availability (08 §9) -> why it cannot be evaluated. `lost`, `expired` and
# `missing` stay apart because an expired trace is a retention decision while a lost
# one is a capture failure, and the console shows the difference.
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
    """One candidate trace, as the source reports it: **raw facts only**.

    R56: the sampler derives the strata from `finish_reason`, `http_status` and
    `schema_valid` rather than trusting a `failure` flag the query computed, so the
    policy lives in one place and a query change cannot quietly redefine it.
    `feedback` carries the trace's feedback *rows* rather than two booleans, so the
    R43 distinction between a customer signal and an operator label is decided from
    the persisted shape - and only from rows that actually belong to this trace.
    """

    request_id: str
    org_id: str
    trace_mode: TraceMode
    content_state: ContentState
    started_at: datetime
    model_revision: str
    http_status: int = 200
    finish_reason: str | None = None
    schema_valid: bool = True
    media_available: bool = False
    feedback: tuple[Feedback, ...] = ()

    @property
    def own_feedback(self) -> tuple[Feedback, ...]:
        """R56: a row counts only when its organization **and** request match.

        Without both checks an org-B label attached to an org-A candidate excluded that
        candidate as already labelled, and an org-B thumb for another request moved an
        org-A trace into the customer-feedback stratum - a cross-tenant fact deciding a
        tenant's calibration set.
        """
        return tuple(entry for entry in self.feedback
                     if entry.org_id == self.org_id and entry.request_id == self.request_id)

    @property
    def calibration_labels(self) -> tuple[Feedback, ...]:
        """r1 R43: membership is the boolean on a `calibration_label` entry. The record
        already refuses any disagreement between name, flag and rubric version, so this
        one field is the whole test - and `by_operator` is *not* it (R56): the platform
        typing an ordinary comment is not an operator verdict against a rubric."""
        return tuple(entry for entry in self.own_feedback if entry.calibration_set)

    @property
    def has_customer_feedback(self) -> bool:
        """The `feedback` stratum: a *customer* told us something about this answer.
        A judge's own score is not customer feedback, a label is not, and neither is an
        ordinary entry the platform made on the customer's behalf (R56/R50)."""
        return any(entry.author_role is AuthorRole.customer and not entry.calibration_set
                   and not entry.by_operator for entry in self.own_feedback)

    @property
    def failed(self) -> bool:
        """R56 / `06` §3.1: the failure stratum is a truncated answer or an invalid
        structured output. Derived here, never taken from the source."""
        return self.finish_reason == FINISH_REASON_LENGTH or self.schema_valid is False

    @property
    def produced_no_output(self) -> bool:
        return self.http_status >= NO_OUTPUT_STATUS

    def labelled_against(self, rubric_version: int) -> bool:
        return any(entry.rubric_version == rubric_version for entry in self.calibration_labels)


class CandidateSource(Protocol):
    """J's read side over T's trace projection plus D's feedback rows. Injected.

    Contract for an adapter (the coordinator may move this and `TraceCandidate` into
    `contracts/ports.py`, so the shape stays minimal and tenant-scoped):

    * return rows for **`org_id` only**, `trace_mode = full`, started at or after
      `since`, at most `limit` of them;
    * return them **deduplicated by `request_id` (lower-case UUID text) and in a stable
      order** (`06` §3.1 queries `FINAL` for exactly this reason). The sampler enforces
      both anyway - it truncates to the bound and deduplicates on the canonical id - but
      only a stable order makes *which* rows fall inside the bound reproducible;
    * carry the **raw**, **typed** facts and let the sampler derive the strata:
      `http_status` an `int`, `schema_valid`/`media_available` a `bool`, `started_at` a
      UTC-aware `datetime` no later than the plan's `now`, `trace_mode`/`content_state`
      the enums, and `finish_reason` either `None` or the engine's token **exactly as
      lower-case text** (`"length"`, `"stop"`; the sampler does not fold case, so a
      differently-spelled value is simply not a truncation). A row that breaks any of
      this is excluded on its own as `malformed_row` rather than interpreted;
    * attach only feedback rows belonging to that organization and request.
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


def canonical_id(request_id: object) -> str:
    """The form two rows are compared by: trimmed and lower-case.

    A UUID's hex is case-insensitive, so `"AB…"` and `"ab…"` are one trace. Grouping by
    the raw string let the same trace through twice under two spellings, which is the
    duplicate R56 exists to stop.
    """
    return request_id.strip().lower() if isinstance(request_id, str) else repr(request_id)


def _reportable_id(candidate: TraceCandidate) -> str:
    """The id for an exclusion row, safe even on a malformed candidate."""
    request_id = candidate.request_id
    return request_id if isinstance(request_id, str) else f"<{type(request_id).__name__}>"


def rank(seed: str, request_id: str) -> int:
    """A keyed hash, so "seeded" means reproducible rather than merely shuffled."""
    digest = hashlib.blake2b(f"{seed}\x00{request_id}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def check_consent(org_id: str, consent: ConsentSnapshot, now: datetime) -> None:
    """The gate, callable **before** anything is read (r1 R10 / R9).

    Separated from `select` so a caller can refuse an unconsented or foreign request
    without querying the trace store at all: a refusal that reads the traces first has
    already touched the data it was refusing to touch.
    """
    if consent.org_id != org_id:
        raise errors.NotFound(f"consent for org {consent.org_id} does not match org {org_id}")
    if not consent.allows_evaluation(now):
        raise errors.ConsentMissing(f"org {org_id} has no current evaluation consent")


def _check_rubric_version(rubric_version: object) -> int:
    if isinstance(rubric_version, bool) or not isinstance(rubric_version, int):
        raise errors.InvalidRequest("a rubric version is an integer (r1 R43)")
    if not limits.MIN_RUBRIC_VERSION <= rubric_version <= limits.MAX_RUBRIC_VERSION:
        raise errors.InvalidRequest(f"a rubric version is in {limits.MIN_RUBRIC_VERSION}.."
                                    f"{limits.MAX_RUBRIC_VERSION}")
    return rubric_version


def deduplicate(candidates: tuple[TraceCandidate, ...]
                ) -> tuple[list[TraceCandidate], list[Excluded]]:
    """R56: one row per `request_id` before stratifying.

    A repeated *identical* row is a projection artefact: keep the first and report the
    copies. A repeated row that **disagrees** (one says the answer was truncated, the
    other does not) is excluded outright: we cannot tell which is true, and sampling
    both would put one trace in two strata and grade it twice.
    """
    order: list[str] = []
    groups: dict[str, list[TraceCandidate]] = {}
    for candidate in candidates:
        key = canonical_id(candidate.request_id)
        rows = groups.get(key)
        if rows is None:
            groups[key] = [candidate]
            order.append(key)
        else:
            rows.append(candidate)
    kept: list[TraceCandidate] = []
    excluded: list[Excluded] = []
    for key in order:
        rows = groups[key]
        request_id = rows[0].request_id
        if len(rows) == 1:
            kept.append(rows[0])
        elif all(_same_facts(row, rows[0]) for row in rows[1:]):
            kept.append(rows[0])
            excluded.extend(Excluded(request_id, Exclusion.duplicate_row) for _ in rows[1:])
        else:
            excluded.extend(Excluded(request_id, Exclusion.conflicting_duplicate) for _ in rows)
    return kept, excluded


def _same_facts(one: TraceCandidate, other: TraceCandidate) -> bool:
    """Whether two rows for the same trace agree, ignoring how the id was spelled.

    They are grouped by the canonical id, so two rows differing *only* in the case or
    padding of `request_id` are the same trace reported twice - a duplicate, not a
    conflict. Comparing the records whole made that spelling difference look like
    disagreement and excluded the trace outright.
    """
    blank = {"request_id": ""}
    return replace(one, **blank) == replace(other, **blank)


def _malformed(candidate: TraceCandidate, now: datetime) -> bool:
    """Whether this row's raw facts are usable at all.

    One bad row from the projection must not abort the whole selection, and it must not be
    *interpreted* either: a `"500"` status is not a 5xx to `>=`, `True` is not a status code,
    a naive `started_at` cannot be compared with the consent window, and a trace stamped in
    the future is a clock or a bug rather than a candidate. Each is excluded on its own with
    `malformed_row` and the rest of the draw goes on.
    """
    if not isinstance(candidate.request_id, str) or not candidate.request_id.strip():
        return True
    if not isinstance(candidate.org_id, str) or not candidate.org_id.strip():
        return True
    if not isinstance(candidate.trace_mode, TraceMode):
        return True
    if not isinstance(candidate.content_state, ContentState):
        return True
    if type(candidate.http_status) is not int:
        return True
    if candidate.finish_reason is not None and type(candidate.finish_reason) is not str:
        return True
    if type(candidate.schema_valid) is not bool or type(candidate.media_available) is not bool:
        return True
    if not isinstance(candidate.model_revision, str):
        return True
    started = candidate.started_at
    if not isinstance(started, datetime) or started.tzinfo is None:
        return True
    if not isinstance(candidate.feedback, tuple):
        return True
    return started > now


def select(org_id: str, consent: ConsentSnapshot, candidates: tuple[TraceCandidate, ...], *,
           rubric_version: int, seed: str, now: datetime,
           design: CalibrationDesign = DEFAULT_DESIGN) -> Selection:
    """The seeded, stratified, consented draw. Pure: no clock, no I/O, no network."""
    check_consent(org_id, consent, now)
    rubric_version = _check_rubric_version(rubric_version)

    # The tenant filter runs **before** deduplication: a foreign row carrying one of my
    # request ids would otherwise disagree with mine and exclude *my* trace as a
    # conflicting duplicate - another tenant deciding what I get calibrated on. Shape comes
    # next, because a row whose facts are unusable cannot be compared either.
    excluded: list[Excluded] = []
    owned: list[TraceCandidate] = []
    for candidate in candidates:
        if candidate.org_id != org_id:
            excluded.append(Excluded(_reportable_id(candidate), Exclusion.not_owned))
        elif _malformed(candidate, now):
            excluded.append(Excluded(_reportable_id(candidate), Exclusion.malformed_row))
        else:
            owned.append(candidate)

    unique, duplicates = deduplicate(tuple(owned))
    excluded.extend(duplicates)
    eligible: dict[Stratum, list[TraceCandidate]] = {stratum: [] for stratum in Stratum}
    for candidate in unique:
        reason = _ineligible(candidate, consent, rubric_version)
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


def _stratum_of(candidate: TraceCandidate) -> Stratum:
    if candidate.failed:
        return Stratum.failures
    if candidate.has_customer_feedback:
        return Stratum.feedback
    return Stratum.uniform


def within_consent_window(started_at: datetime, consent: ConsentSnapshot) -> bool:
    """R56: evaluation consent is not retroactive and does not outlive itself.

    Both bounds are half-open at the top: **at** `effective_at` is inside, **at**
    `revoked_at` is outside.

    The end bound cannot fire through `select` today, and that is a consequence of two
    rules meeting rather than dead code: a *current* consent has `now < revoked_at`, and a
    well-formed candidate has `started_at <= now`. It stays because R56 states it and
    because the next caller (J2 re-checking at submission, J3 replaying an old plan) will
    not have that guarantee - so it is unit-tested directly rather than only through the
    draw.
    """
    if started_at < consent.effective_at:
        return False
    return consent.revoked_at is None or started_at < consent.revoked_at


def _ineligible(candidate: TraceCandidate, consent: ConsentSnapshot,
                rubric_version: int) -> Exclusion | None:
    """Ownership and shape are already settled by `select` before this runs."""
    if candidate.trace_mode is not TraceMode.full:
        return Exclusion.trace_mode_not_full
    if not within_consent_window(candidate.started_at, consent):
        return Exclusion.outside_consent_window
    if candidate.produced_no_output:
        # R56 / `06` §3.1 step 3: a 5xx has no answer to grade.
        return Exclusion.skipped_no_output
    if candidate.content_state is not ContentState.available:
        return _CONTENT_EXCLUSION[candidate.content_state]
    if candidate.labelled_against(rubric_version):
        # Already labelled against *this* rubric version. A new version is a new
        # series (`06` §3.7 / J4), so the same trace is a candidate again there.
        return Exclusion.already_calibrated
    return None
