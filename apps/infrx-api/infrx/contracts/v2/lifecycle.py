"""F2C.a — durable lifecycle records and the three narrow ports behind them.

Closes the contract half of RV-02 (upload state lived in process dictionaries), RV-05
(a text-only attachment was indistinguishable from one never completed) and RV-03 (the
collector's liveness came from process memory). D10 implements the ports on PostgreSQL,
M5 the upload adapter over them, W5 the readiness barrier, G7 the routes; nothing here
is SQL and no port hands a browser an object key.

* `UploadRepository` — tickets: constraints, owner, window, the bytes that arrived and
  the immutable finalized source. Opaque `upl_` handles; another tenant's handle is the
  same `not_found` as an unknown one.
* `ReadinessStore` — `admit_ready` records the exact source manifest (possibly EMPTY) and
  the execution-ready marker in the admission transaction itself; `readiness` answers
  `None` for a job that never completed one, which is not the empty manifest; and
  `claim_preparation` refuses a job without the marker.
* `ContentLifecycle` — one durable row per content object with a generation; paginated
  candidates from persisted eligibility; leased, fenced claims; the reference recheck
  inside `tombstone`; an idempotent delete acknowledgement that can never retire a newer
  generation of the same key.

**Time and version authority** (02 §F2C). Every instant below is the store's database
transaction time (`infrx.now()`, R7) — never a caller argument, never a gateway clock:
`created_at`/`expires_at` (create; the window is the repository's configured length,
persisted once), `received_at` (put acknowledgement), `finalized_at` (complete),
`ready_at` (= the admission transaction's `admitted_at`), `registered_at`/`eligible_at`
(register; `eligible_at` = registration + the configured grace, persisted, so a restart
never resets it), `claimed_at`/`expires_at` of a claim (claim + the configured claim TTL),
`tombstoned_at`, `deleted_at`, and a reference's `retain_until` (set ONCE when its job
terminalizes, from the job's persisted facts, never recomputed from configuration).
`generation` is a per-object-key counter the store increments only when a deleted key is
registered again; `fence` is a per-generation claim counter the store increments on every
claim. An instant is live while `now < instant`: at equality it has passed.

**Refusals** travel as the existing typed errors (`REFUSAL_ERRORS`), so no public code or
HTTP status changes. The reason rides on the exception as `.refusal` and is never
serialized: a route renders the code only (`refusal_of(error)` reads it back).
"""
from __future__ import annotations

import enum
from datetime import timedelta
from typing import Protocol, runtime_checkable

from pydantic import Field, StrictFloat, StrictInt, ValidationError, model_validator

from .. import errors, ids
from ..records import (Admission, IdempotencyRef, JobState, Lease, MediaRef, NormalizedRequest,
                       SettlementState, TerminalOutcome, UploadState)
from .records import AccountingRegime, AdmissionV2, RecordV2, Sha256, Timestamp, UuidStr

# The one reference form a caller may name (R61(1)); media/uploads.py and
# gateway/routes/validate.py spell the same string today.
DESTINATION_SCHEME = "infrx-upload:"
CONSTRAINT_NAMES = frozenset({"max_bytes", "bytes", "accepted_mime", "digest"})
MAX_PAGE = 1000
# ponytail: one fixed retry hint for `content_retiring`; a per-sweep estimate if clients
# ever retry too hard. The window is normally one collector pass.
RETIRING_RETRY_AFTER_S = 5


# --- vocabulary --------------------------------------------------------------------------
class ReadinessState(enum.StrEnum):
    """`not_ready` is "no committed marker": never completed. It is NOT the empty
    manifest - a text-only job is `ready` with zero sources."""

    not_ready = "not_ready"
    ready = "ready"


class ContentKind(enum.StrEnum):
    upload_destination = "upload_destination"   # bytes awaiting completion
    source = "source"                            # finalized or staged source media
    prepared = "prepared"                        # preparation output
    payload = "payload"                          # the staged request envelope
    result = "result"                            # the result body


class ContentLocation(enum.StrEnum):
    object_store = "object_store"
    database = "database"                        # content-bearing rows count too (D10.b)


class ContentOrigin(enum.StrEnum):
    written = "written"          # registered by the runtime BEFORE it wrote the object
    discovered = "discovered"    # an object found under the prefix with no row (pre-F2C)


class LifecycleState(enum.StrEnum):
    live = "live"
    tombstoned = "tombstoned"    # deletion committed: no new use until the delete is acked
    deleted = "deleted"          # external delete acknowledged; the key may be re-registered


class LifecycleRefusal(enum.StrEnum):
    not_found = "not_found"                        # unknown or another tenant's: one answer
    invalid_constraints = "invalid_constraints"
    upload_expired = "upload_expired"
    upload_not_open = "upload_not_open"            # finalized, aborted or expired already
    upload_not_finalized = "upload_not_finalized"  # R99(a)
    nothing_received = "nothing_received"
    bytes_changed = "bytes_changed"                # one handle names one set of bytes
    too_large = "too_large"
    size_mismatch = "size_mismatch"
    digest_mismatch = "digest_mismatch"
    mime_not_accepted = "mime_not_accepted"
    media_refused = "media_refused"                # M's probe refused the container
    invalid_manifest = "invalid_manifest"
    expectation_mismatch = "expectation_mismatch"  # the pinned card is not the runtime's
    not_ready = "not_ready"
    content_retiring = "content_retiring"          # tombstoned, delete not yet acknowledged
    not_eligible = "not_eligible"                  # inside its persisted grace
    reference_live = "reference_live"
    claim_held = "claim_held"
    claim_lost = "claim_lost"                      # superseded fence, expiry or generation


REFUSAL_ERRORS: dict[LifecycleRefusal, type[errors.DomainError]] = {
    LifecycleRefusal.not_found: errors.NotFound,
    LifecycleRefusal.invalid_constraints: errors.InvalidRequest,
    LifecycleRefusal.upload_expired: errors.UploadExpired,
    LifecycleRefusal.upload_not_open: errors.StateConflict,
    LifecycleRefusal.upload_not_finalized: errors.InvalidRequest,
    LifecycleRefusal.nothing_received: errors.InvalidRequest,
    LifecycleRefusal.bytes_changed: errors.StateConflict,
    LifecycleRefusal.too_large: errors.RequestTooLarge,
    LifecycleRefusal.size_mismatch: errors.InvalidRequest,
    LifecycleRefusal.digest_mismatch: errors.UnsupportedMedia,
    LifecycleRefusal.mime_not_accepted: errors.UnsupportedMedia,
    LifecycleRefusal.media_refused: errors.UnsupportedMedia,
    LifecycleRefusal.invalid_manifest: errors.InvalidRequest,
    LifecycleRefusal.expectation_mismatch: errors.InvalidRequest,
    LifecycleRefusal.not_ready: errors.NotClaimable,
    LifecycleRefusal.content_retiring: errors.DependencyUnavailable,
    LifecycleRefusal.not_eligible: errors.NotClaimable,
    LifecycleRefusal.reference_live: errors.NotClaimable,
    LifecycleRefusal.claim_held: errors.NotClaimable,
    LifecycleRefusal.claim_lost: errors.StaleLease,
}


# The only reasons an upload ticket may record as aborted: all public, all about the
# caller's own bytes, so a browser-safe ticket can never carry an internal refusal.
UPLOAD_ABORT_REASONS = frozenset({
    LifecycleRefusal.too_large, LifecycleRefusal.size_mismatch, LifecycleRefusal.digest_mismatch,
    LifecycleRefusal.mime_not_accepted, LifecycleRefusal.media_refused})


def refuse(reason: LifecycleRefusal, detail: str) -> errors.DomainError:
    """The typed error for `reason`, carrying it as `.refusal` (operator-side only)."""
    kind = REFUSAL_ERRORS[reason]
    error = kind(detail, retry_after_s=RETIRING_RETRY_AFTER_S) \
        if kind is errors.DependencyUnavailable else kind(detail)
    error.refusal = reason
    return error


def refusal_of(error: BaseException) -> LifecycleRefusal | None:
    return getattr(error, "refusal", None)


def refusal_table() -> list[dict]:
    """The cross-language table (`fixtures/v2/lifecycle_refusals.json`): reason -> code."""
    return [{"reason": reason.value, "code": REFUSAL_ERRORS[reason].code}
            for reason in LifecycleRefusal]


# --- (i) uploads -------------------------------------------------------------------------
class UploadConstraints(RecordV2):
    """What a create body may fix, and nothing else: no tenant, handle, window or path."""

    max_bytes: StrictInt = Field(ge=1)
    bytes: StrictInt | None = Field(default=None, ge=1)
    accepted_mime: tuple[str, ...] = Field(min_length=1)
    digest: Sha256 | None = None

    @model_validator(mode="after")
    def _bounded(self) -> UploadConstraints:
        if self.bytes is not None and self.bytes > self.max_bytes:
            raise ValueError("declared bytes exceed max_bytes")
        if len(set(self.accepted_mime)) != len(self.accepted_mime) \
                or not all(mime and mime == mime.strip().lower() for mime in self.accepted_mime):
            raise ValueError("accepted_mime is a list of distinct lowercase media types")
        return self

    @classmethod
    def parse(cls, body: object, *, max_media_bytes: int,
              allowed_mime: frozenset[str]) -> UploadConstraints:
        """The public create body -> constraints, the one trust boundary. Absent
        `max_bytes`/`accepted_mime` take the deployment's bounds; anything outside the four
        names (an `org_id`, `expires_at`, `upload_handle`, a path) is `invalid_constraints`,
        never ignored."""
        if not isinstance(body, dict):
            raise refuse(LifecycleRefusal.invalid_constraints, "constraints are an object")
        if set(body) - CONSTRAINT_NAMES:            # `schema_version` is a field, not a name
            raise refuse(LifecycleRefusal.invalid_constraints,
                         f"unknown constraints {sorted(map(str, set(body) - CONSTRAINT_NAMES))}")
        values = {"max_bytes": max_media_bytes, "accepted_mime": tuple(sorted(allowed_mime)),
                  **{name: value for name, value in body.items() if value is not None}}
        try:
            constraints = cls.model_validate(values)
        except ValidationError:
            raise refuse(LifecycleRefusal.invalid_constraints, "constraint values") from None
        if constraints.max_bytes > max_media_bytes \
                or not set(constraints.accepted_mime) <= set(allowed_mime):
            raise refuse(LifecycleRefusal.invalid_constraints,
                         "constraints exceed the deployment's media bounds")
        return constraints


class UploadReceipt(RecordV2):
    """What the server measured of the bytes that reached the destination."""

    bytes: StrictInt = Field(ge=0)
    digest: Sha256
    received_at: Timestamp


class FinalizedSource(RecordV2):
    """The immutable source a finalized ticket names: a content row and generation, plus
    the measured facts. No object key: M builds that from (org, profile, digest). Every
    fact is required, as 0010's `media_uploads_finalized_facts` requires of a finalized
    row: an upload whose duration was not measured is not finalized."""

    content_id: UuidStr
    generation: StrictInt = Field(ge=1)
    digest: Sha256
    bytes: StrictInt = Field(ge=0)
    mime: str = Field(min_length=1)
    profile_version: str = Field(min_length=1)
    duration_s: StrictFloat = Field(ge=0, allow_inf_nan=False)
    finalized_at: Timestamp


class UploadTicket(RecordV2):
    """One upload, as the repository persists it. Reloaded whole by any process."""

    upload_handle: str = Field(pattern=ids.UPLOAD_HANDLE_RE.pattern)
    org_id: UuidStr
    destination_ref: str
    constraints: UploadConstraints
    state: UploadState
    created_at: Timestamp
    expires_at: Timestamp
    received: UploadReceipt | None = None
    finalized: FinalizedSource | None = None
    refusal: LifecycleRefusal | None = None       # why it was aborted: UPLOAD_ABORT_REASONS

    @model_validator(mode="after")
    def _one_fact(self) -> UploadTicket:
        c, received, done = self.constraints, self.received, self.finalized
        if self.destination_ref != DESTINATION_SCHEME + self.upload_handle:
            raise ValueError("destination_ref is infrx-upload:<handle>, nothing else")
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must follow created_at")
        if (self.state is UploadState.finalized) != (done is not None):
            raise ValueError("finalized exactly when a finalized source is recorded")
        if (self.state is UploadState.aborted) != (self.refusal is not None):
            raise ValueError("aborted exactly when a refusal is recorded")
        if self.refusal is not None and self.refusal not in UPLOAD_ABORT_REASONS:
            raise ValueError("a ticket records only a public upload refusal")
        if received is not None and (received.bytes > c.max_bytes or not
                                     self.created_at <= received.received_at < self.expires_at):
            raise ValueError("a receipt is within the cap and the window")
        if done is not None:
            if received is None or (done.digest, done.bytes) != (received.digest, received.bytes):
                raise ValueError("a finalized source is exactly the bytes received")
            if done.mime not in c.accepted_mime \
                    or (c.bytes is not None and done.bytes != c.bytes) \
                    or (c.digest is not None and done.digest != c.digest):
                raise ValueError("a finalized source satisfies every constraint")
            if not received.received_at <= done.finalized_at < self.expires_at:
                raise ValueError("finalized inside the window, after the bytes arrived")
        return self


@runtime_checkable
class UploadRepository(Protocol):
    """D10 (M5 adapts it). The durable authority for tickets; process memory never is.

    `org_id` is the authenticated key's organization, never a body field. Every
    operation on a handle another organization owns is `not_found`, exactly as for an
    unknown handle. The window is `now < expires_at` on the store clock.
    """

    async def create(self, org_id: str, constraints: UploadConstraints) -> UploadTicket:
        """Mint an opaque handle, persist the constraints and a window of the store's own
        configured length. The caller names neither the handle nor the expiry."""

    async def acknowledge_put(self, org_id: str, upload_handle: str, *, bytes: int,
                              digest: str) -> UploadTicket:
        """Record what the server measured at the destination. Only an open, unexpired
        ticket (`upload_not_open`/`upload_expired`); over `max_bytes` is `too_large`. The
        same bytes again return the ticket unchanged; other bytes are `bytes_changed`."""

    async def complete(self, org_id: str, upload_handle: str, source: MediaRef) -> UploadTicket:
        """Finalize once. `source` is M's verified description of the bytes (re-read,
        probed, at the content-addressed source key, with its measured duration).
        Refusals: another tenant's or a
        relabelled ref `not_found`; nothing received `nothing_received`; digest/size unlike
        the receipt `bytes_changed`; a failed constraint (`size_mismatch`,
        `digest_mismatch`, `mime_not_accepted`) aborts the ticket in the same transaction -
        a failed check is final. It links or registers the source content row
        (`content_retiring` while a delete of that key is pending). A retry with the same
        source answers the finalized ticket; any other source is `bytes_changed`."""

    async def abort(self, org_id: str, upload_handle: str,
                    refusal: LifecycleRefusal) -> UploadTicket:
        """M's own final refusal, one of `UPLOAD_ABORT_REASONS` (else `invalid_request`).
        Idempotent: an aborted ticket answers as it stands; a finalized or expired one is
        `upload_not_open`."""

    async def resolve(self, org_id: str, upload_handle: str) -> UploadTicket:
        """The finalized ticket for use (R99): `not_found`, then `upload_expired` from
        `expires_at` on (any state), then `upload_not_finalized`, then `not_found` when the
        finalized source row is no longer live at the finalized generation."""

    async def expire(self, limit: int) -> int:
        """Mark at most `limit` open tickets past their window `expired` (store clock, no
        caller time). Returns how many. Idempotent; a finalized ticket stays finalized."""


# --- (ii) execution readiness ------------------------------------------------------------
class ManifestSource(RecordV2):
    """One source a job executes on: the store's ref, bound to a content row generation."""

    content_id: UuidStr
    generation: StrictInt = Field(ge=1)
    ref: MediaRef


class ExecutionReadiness(RecordV2):
    """The execution-ready marker and the exact normalized source manifest, written in
    the admission transaction and never changed. `sources` is REQUIRED: `[]` is a
    completed empty manifest (text-only), and an absent marker is `readiness() -> None`.
    The two are never confused, in a record or on the wire."""

    job_id: UuidStr
    org_id: UuidStr
    sources: tuple[ManifestSource, ...]
    ready_at: Timestamp

    @model_validator(mode="after")
    def _own_and_distinct(self) -> ExecutionReadiness:
        if any(source.ref.org_id != self.org_id for source in self.sources):
            raise ValueError("every manifest source belongs to the job's organization")
        if len({s.ref.handle for s in self.sources}) != len(self.sources) \
                or len({s.content_id for s in self.sources}) != len(self.sources):
            raise ValueError("a manifest names each source once")
        return self

    def view(self) -> ReadinessView:
        return ReadinessView(request_id=self.job_id, state=ReadinessState.ready,
                             source_count=len(self.sources), ready_at=self.ready_at)


class ReadinessView(RecordV2):
    """The wire-safe projection (no refs, no keys). `ready` carries a count - possibly
    0 - and the instant; `not_ready` carries neither."""

    request_id: UuidStr
    state: ReadinessState
    source_count: StrictInt | None = Field(default=None, ge=0)
    ready_at: Timestamp | None = None

    @model_validator(mode="after")
    def _ready_is_a_fact(self) -> ReadinessView:
        ready = self.state is ReadinessState.ready
        if ready != (self.source_count is not None) or ready != (self.ready_at is not None):
            raise ValueError("ready carries source_count and ready_at; not_ready neither")
        return self


def readiness_view(job_id: str, readiness: ExecutionReadiness | None) -> ReadinessView:
    return readiness.view() if readiness is not None else \
        ReadinessView(request_id=job_id, state=ReadinessState.not_ready)


class AdmissionExpectation(RecordV2):
    """What the RUNTIME requires of an admission: composition-root configuration, never a
    request field. CREDIT: the card this deployment approved (R69); legacy: none."""

    accounting_regime: AccountingRegime
    rate_card_version: str | None = None

    @model_validator(mode="after")
    def _regime_card(self) -> AdmissionExpectation:
        card = self.rate_card_version
        if (self.accounting_regime is AccountingRegime.credit) != (card is not None):
            raise ValueError("a CREDIT expectation names its card; a legacy one names none")
        if card is not None and (not card or card != card.strip()):
            raise ValueError("the card version is exact text")
        return self


@runtime_checkable
class ReadinessStore(Protocol):
    """D10 (W5 consumes it; G7 calls `admit_ready`). One-phase readiness (D1)."""

    async def admit_ready(self, request: NormalizedRequest, idem: IdempotencyRef,
                          expectation: AdmissionExpectation
                          ) -> tuple[Admission | AdmissionV2, ExecutionReadiness | None]:
        """ONE transaction: everything `admit`/`admit_credit` does, plus (a) the
        expectation against the pins (`expectation_mismatch`, R69) and the capability of
        the PINNED serving revision (video sources need video input, stream mode needs
        stream output), (b) each `request.media` ref resolved by (org, source key) to a live
        content row of the request's organization with the same digest - an upload ref also
        to a finalized, unexpired ticket naming that row (`not_found`, `upload_expired`,
        `content_retiring`, `invalid_manifest` for a repeat) - and (c) the manifest, its
        references and the marker. A refusal admits nothing: no job, hold or outbox. A
        replay answers the recorded pair; a job the previous runtime admitted replays with
        `None` (it has no marker). Both regimes: a legacy expectation names no card and has
        no pinned revision to recheck. `JobStore.admit` and `CreditJobStore.admit_credit`
        NEVER write a marker - a job they admit is `not_ready` until its preparation
        deadline ends it (the cutover rule in 02)."""

    async def readiness(self, job_id: str) -> ExecutionReadiness | None:
        """The committed marker, or None when none was ever completed."""

    async def claim_preparation(self, job_id: str, worker_id: str) -> Lease:
        """`JobStore.claim_preparation`, refusing `not_ready` for a job with no marker."""


# --- (iii) content lifecycle -------------------------------------------------------------
class ContentIdentity(RecordV2):
    """What an object IS. `object_key` is internal (a server-built object key, or
    `<table>/<row id>` for database content); no port returns it to a browser."""

    org_id: UuidStr
    kind: ContentKind
    location: ContentLocation
    object_key: str = Field(min_length=1, max_length=1024)
    digest: Sha256 | None = None
    bytes: StrictInt | None = Field(default=None, ge=0)
    job_id: UuidStr | None = None          # payload/prepared/result: the one job it serves
    upload_handle: str | None = Field(default=None, pattern=ids.UPLOAD_HANDLE_RE.pattern)
    origin: ContentOrigin

    @model_validator(mode="after")
    def _owner(self) -> ContentIdentity:
        per_job = self.kind in (ContentKind.payload, ContentKind.prepared, ContentKind.result)
        if per_job != (self.job_id is not None):
            raise ValueError("payload, prepared and result content name their job; "
                             "sources and destinations name none")
        if (self.kind is ContentKind.upload_destination) != (self.upload_handle is not None):
            raise ValueError("an upload destination names its ticket, and only it")
        if self.origin is ContentOrigin.written and (self.digest is None or self.bytes is None):
            raise ValueError("written content is registered with its digest and size")
        return self


class DeletionClaim(RecordV2):
    """A leased, fenced right to delete ONE generation of one object."""

    content_id: UuidStr
    generation: StrictInt = Field(ge=1)
    fence: StrictInt = Field(ge=1)
    holder: str = Field(min_length=1, max_length=200)
    claimed_at: Timestamp
    expires_at: Timestamp

    @model_validator(mode="after")
    def _leased(self) -> DeletionClaim:
        if self.expires_at <= self.claimed_at:
            raise ValueError("a claim expires after it was taken")
        return self


class ContentObject(RecordV2):
    """The durable reference row. `content_id` is stable for an object key; each
    re-creation after a delete is a new `generation` of it."""

    content_id: UuidStr
    generation: StrictInt = Field(ge=1)
    identity: ContentIdentity
    state: LifecycleState
    registered_at: Timestamp
    eligible_at: Timestamp
    claim: DeletionClaim | None = None
    tombstoned_at: Timestamp | None = None
    deleted_at: Timestamp | None = None

    @model_validator(mode="after")
    def _states(self) -> ContentObject:
        if self.eligible_at < self.registered_at:
            raise ValueError("eligibility never precedes registration")
        live = self.state is LifecycleState.live
        if live != (self.tombstoned_at is None):
            raise ValueError("tombstoned_at is set exactly once the object leaves live")
        if (self.state is LifecycleState.deleted) != (self.deleted_at is not None):
            raise ValueError("deleted_at is set exactly when the delete was acknowledged")
        if self.claim is not None and (self.claim.content_id, self.claim.generation) != (
                self.content_id, self.generation):
            raise ValueError("a claim is on this generation of this object")
        return self


class ContentReference(RecordV2):
    """Why an object must stay: a job it serves. Live while the job is non-terminal, then
    until `retain_until`, which is set once at terminalization and never recomputed."""

    content_id: UuidStr
    generation: StrictInt = Field(ge=1)
    job_id: UuidStr
    org_id: UuidStr
    referenced_at: Timestamp
    retain_until: Timestamp | None = None


class Tombstone(RecordV2):
    """Committed: this generation is being deleted. Only now may the external delete run."""

    content_id: UuidStr
    generation: StrictInt = Field(ge=1)
    fence: StrictInt = Field(ge=1)
    location: ContentLocation
    object_key: str = Field(min_length=1, max_length=1024)
    tombstoned_at: Timestamp


class ContentPage(RecordV2):
    items: tuple[ContentObject, ...]
    next_cursor: str | None = None


@runtime_checkable
class ContentLifecycle(Protocol):
    """D10 (M6's collector drives it). PostgreSQL decides what may be deleted; an object
    listing, its age or an empty process dictionary never does. An unavailable store
    means RETAIN and report: every refusal here keeps the object.

    An object is REFERENCED while any of: a manifest reference of a non-terminal job, or of
    a terminal one before its `retain_until`; its own `job_id`'s job is non-terminal (or
    before that job's `retain_until`); it is an upload destination whose ticket is open and
    unexpired; it is the source a finalized, unexpired ticket names.

    `retain_until` per kind (F2C.b), set once at terminalization: a `result` lasts exactly
    until the outcome's persisted `result_expires_at`; a SETTLED job with none (every
    non-success, and a success settled before 0018 persisted it, which reads
    `unavailable`) keeps no result, so its body is scrubbable from settlement. Every other
    kind lasts until settlement + the configured serving retention for it (P-25).

    Deleting `database` content is a SCRUB (D3): the tombstoned row's body is emptied in
    one transaction and the acknowledgement records it; the job row, idempotency tombstone,
    terminal outcome, usage, settlement and ledger rows and the content's digest and size
    stay. A scrubbed result reads `result_expired` (never empty text, never regenerated),
    and because a result is eligible only from its persisted expiry, `read_outcome` already
    answers `expired` for it. D10's guard on `infrx.job_results`, replacing 0014's
    `job_results_immutable` trigger and `job_results_bytes_exact` check (02 §F2C "Scrub
    guard" is the column-by-column text): an UPDATE may change only `body` -> '' and
    `scrubbed_at` null -> `infrx.now()`, only when the job has `settled_at is not null and
    (result_expires_at is null or infrx.now() >= result_expires_at)`; `bytes` keeps the
    original size (`check (scrubbed_at is not null or bytes = octet_length(body))`);
    DELETE and TRUNCATE stay forbidden.
    """

    async def register(self, identity: ContentIdentity) -> ContentObject:
        """Before the runtime writes an object (origin `written`) or when a collector finds
        one with no row (`discovered`): the row, with `eligible_at` = now + grace,
        persisted. Idempotent on (location, object_key): the first registration wins and
        its eligibility is never reset. A `tombstoned` key is `content_retiring` (the write
        must wait for the acknowledgement); a `deleted` key becomes generation + 1. Other
        bytes at a live key are `bytes_changed`."""

    async def references(self, content_id: str) -> tuple[ContentReference, ...]:
        """Every manifest reference of the object, for a retain-and-report answer."""

    async def candidates(self, *, after: str | None, limit: int) -> ContentPage:
        """Live objects past `eligible_at` with no reference and no unexpired claim, plus
        tombstoned ones whose claim lapsed (an unfinished delete to reconcile), ordered by
        `(eligible_at, content_id)`. `after` is the previous page's opaque cursor; `limit`
        is 1..MAX_PAGE (larger is clamped, smaller is `invalid_request`)."""

    async def claim(self, content_id: str, generation: int, holder: str) -> DeletionClaim:
        """A leased claim with the next fence. Refusals keep the object: `not_eligible`,
        `reference_live`, `claim_held` (an unexpired claim), `claim_lost` (another
        generation). A tombstoned object may be re-claimed to finish its delete."""

    async def tombstone(self, claim: DeletionClaim) -> Tombstone:
        """In one transaction, under the row lock admission and `complete` also take: the
        claim is still the current, unexpired fence (`claim_lost`), the reference and grace
        rechecks pass (`reference_live`, `not_eligible`), then `tombstoned`. The recheck is
        not a separate operation: a check before this transaction is a race."""

    async def acknowledge_delete(self, tombstone: Tombstone) -> ContentObject:
        """The external delete finished: `deleted`. Idempotent. A tombstone of an OLDER
        generation changes nothing (the recreated object is not this delete's); a fence a
        newer claim superseded is `claim_lost`."""


# --- F2C.b: terminal/read consistency ----------------------------------------------------
class ReadOutcome(enum.StrEnum):
    """Every answer a committed job can give a reader (status, result, replay, console).

    | read | when | result route | status `result_available` / `result_expires_at` |
    |---|---|---|---|
    | `pending` | not terminal | 409 `result_pending` | false / absent |
    | `available` | success with a result and authoritative usage, `now < result_expires_at` | 200 with the response | true / the persisted instant |
    | `no_result` | failed, cancelled or expired job; a success the store rewrote (R30) | 200, no response | false / absent |
    | `held_unknown` | usage unknown, reservation held for reconciliation | 200, no response | false / absent |
    | `expired` | success past its PERSISTED expiry (scrubbed or not) | 410 `result_expired` | false / absent |
    | `unavailable` | success with no persisted expiry (a record from before F2C.b) | 410 `result_expired` | false / absent |

    `unavailable` is fail-closed: an expiry is never recomputed from configuration.
    Metadata (state, cause, usage, settlement) stays readable in every row.
    """

    pending = "pending"
    available = "available"
    no_result = "no_result"
    held_unknown = "held_unknown"
    expired = "expired"
    unavailable = "unavailable"


def read_outcome(outcome: TerminalOutcome | None, now) -> ReadOutcome:
    """The one classification every read path applies, `now` being the STORE clock
    (`db_now`). Content is served only for `available`; scrubbing happens only after the
    persisted expiry, so a scrubbed body is always `expired` here."""
    if outcome is None:
        return ReadOutcome.pending
    if outcome.settlement_state is SettlementState.held_unknown:
        return ReadOutcome.held_unknown
    if outcome.state is not JobState.succeeded or not outcome.result_ref \
            or outcome.usage is None:
        return ReadOutcome.no_result
    if outcome.result_expires_at is None:
        return ReadOutcome.unavailable
    return ReadOutcome.available if now < outcome.result_expires_at else ReadOutcome.expired


def result_case_table() -> list[dict]:
    """`fixtures/v2/result_read_cases.json`: the table both languages must classify
    identically, built from the v1 terminal fixtures - `terminal_success.json` is a record
    written before F2C.b (no expiry) and `terminal_success_expiring.json` the same success
    as the store now commits it."""
    from ..fixtures import model as v1_model
    tick = timedelta(microseconds=1)
    old = v1_model("terminal_success.json")
    new = v1_model("terminal_success_expiring.json")
    expires = new.result_expires_at
    rows = [("not terminal", None, new.settled_at),
            ("success at settlement", new, new.settled_at),
            ("success one microsecond before its expiry", new, expires - tick),
            ("success AT its expiry: equality has passed", new, expires),
            ("success after its expiry", new, expires + tick),
            ("pre-F2C.b success at settlement: no expiry is invented", old, old.settled_at),
            ("pre-F2C.b success before the old TTL would end", old, expires - tick)]
    rows += [(name.removesuffix(".json"), v1_model(name), new.settled_at)
             for name in ("terminal_cancelled.json", "terminal_platform_error.json",
                          "terminal_expired.json", "terminal_unknown_usage.json")]

    def variant(outcome: TerminalOutcome, **changes) -> TerminalOutcome:
        return TerminalOutcome.model_validate({**outcome.model_dump(), **changes})
    # Each `no_result` condition alone (valid records a store could hold): a ref on a job
    # that did not succeed is never served, a success needs its ref, and a result is only
    # served with authoritative usage - even when an expiry was persisted.
    ref, zero = new.result_ref, v1_model("terminal_platform_error.json").debit
    rows += [("cancelled with a result_ref", variant(v1_model("terminal_cancelled.json"),
                                                     result_ref=ref), new.settled_at),
             ("failed with a result_ref", variant(v1_model("terminal_platform_error.json"),
                                                  result_ref=ref), new.settled_at),
             ("success without a result_ref", variant(old, result_ref=None), old.settled_at),
             ("success released free without usage, with an expiry",
              variant(new, usage=None, settlement_state=SettlementState.released_free,
                      debit=zero), new.settled_at)]
    return [{"name": name, "now": now.isoformat().replace("+00:00", "Z"),
             "expected": read_outcome(outcome, now).value,
             **({"outcome": outcome.model_dump(mode="json", exclude_none=True)}
                if outcome is not None else {})}
            for name, outcome, now in rows]


__all__ = [
    "AdmissionExpectation", "ContentIdentity", "ContentKind",
    "ContentLifecycle", "ContentLocation", "ContentObject", "ContentOrigin", "ContentPage",
    "ContentReference", "DESTINATION_SCHEME", "DeletionClaim", "ExecutionReadiness",
    "FinalizedSource", "LifecycleRefusal", "LifecycleState", "MAX_PAGE", "ManifestSource",
    "REFUSAL_ERRORS", "ReadinessState", "UPLOAD_ABORT_REASONS", "CONSTRAINT_NAMES", "ReadinessStore", "ReadinessView", "Tombstone",
    "ReadOutcome", "UploadConstraints", "UploadReceipt", "UploadRepository", "UploadTicket",
    "read_outcome", "readiness_view", "refusal_of", "refusal_table", "refuse",
    "result_case_table",
]
