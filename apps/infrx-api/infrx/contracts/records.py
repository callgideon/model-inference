"""The contracts-v1 records, frozen. Vocabulary strings are frozen too.

Every record is a frozen pydantic v2 model with `extra="forbid"` and
`schema_version: int = 1`: an unknown field is a rejected payload, not a silently
dropped one, and a producer that adds a field must bump the version. Money is a
`Decimal` that crosses JSON as a fixed-point string; datetimes are UTC-aware and
cross JSON as RFC 3339 with `Z`.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (BaseModel, ConfigDict, Field, PlainSerializer, StrictInt,
                      model_validator)
from pydantic.functional_validators import BeforeValidator

from . import errors, ids, limits, money

SCHEMA_VERSION = 1

# r1 R41: what a customer session reads in place of an operator's identity. It lives
# beside the records because the *port* projection needs it, not only the wire one.
PLATFORM_ACTOR = "platform"


# --- vocabulary (08 §3) ------------------------------------------------------
class JobState(enum.StrEnum):
    preparing = "preparing"
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    expired = "expired"


TERMINAL_STATES = frozenset({JobState.succeeded, JobState.failed, JobState.cancelled, JobState.expired})


class ExecutionMode(enum.StrEnum):
    sync = "sync"
    stream = "stream"
    async_ = "async"        # `async` is a keyword; the wire value is "async"


class TerminalCause(enum.StrEnum):
    completed = "completed"
    client_cancelled = "client_cancelled"
    client_disconnected = "client_disconnected"
    sync_deadline = "sync_deadline"
    queue_wait_expired = "queue_wait_expired"
    deadline_exceeded = "deadline_exceeded"
    invalid_media = "invalid_media"
    preparation_failed = "preparation_failed"
    engine_error = "engine_error"
    engine_incomplete = "engine_incomplete"
    lost_after_publication = "lost_after_publication"
    journal_write_failed = "journal_write_failed"
    retries_exhausted = "retries_exhausted"
    platform_error = "platform_error"


# r1 R21: exactly three causes may settle a debit, and only with authoritative
# usage. Every other cause is platform-absorbed, because 02 makes platform-caused
# failures free and the platform's own deadlines are platform-caused: a customer is
# not charged for our generation deadline (`deadline_exceeded`), our queue
# (`queue_wait_expired`) or a synchronous timeout we failed to answer within
# (`sync_deadline`). The two sets partition `TerminalCause`, which
# `test_fixtures.py` asserts, so a new cause cannot be silently billable.
BILLABLE_CAUSES = frozenset({
    TerminalCause.completed, TerminalCause.client_cancelled, TerminalCause.client_disconnected,
})
PLATFORM_FAILURE_CAUSES = frozenset(set(TerminalCause) - BILLABLE_CAUSES)

# R21 / G2: the causes a caller may give `JobStore.cancel`. The two client causes and
# the platform's synchronous deadline, and nothing else: every other cause is the
# store's or the worker's to record, never a canceller's.
CANCEL_CAUSES = frozenset({
    TerminalCause.client_cancelled, TerminalCause.client_disconnected, TerminalCause.sync_deadline,
})

# The (cause, state) pair is part of the contract, not two independent fields: a
# `succeeded` job whose cause is `engine_error` would be a free success, and a
# `failed` job whose cause is `completed` would lose a settled debit. Any cause
# not named here is a failure cause and may only carry `failed`.
CAUSE_STATES: dict[TerminalCause, frozenset] = {
    TerminalCause.completed: frozenset({JobState.succeeded}),
    TerminalCause.client_cancelled: frozenset({JobState.cancelled}),
    TerminalCause.client_disconnected: frozenset({JobState.failed, JobState.cancelled}),
    TerminalCause.sync_deadline: frozenset({JobState.failed, JobState.cancelled}),
    TerminalCause.queue_wait_expired: frozenset({JobState.expired}),
    TerminalCause.deadline_exceeded: frozenset({JobState.failed, JobState.expired}),
}
_FAILED_ONLY = frozenset({JobState.failed})


def states_for_cause(cause: TerminalCause) -> frozenset:
    return CAUSE_STATES.get(cause, _FAILED_ONLY)


class UsageCertainty(enum.StrEnum):
    authoritative = "authoritative"
    unknown = "unknown"


class SettlementState(enum.StrEnum):
    settled = "settled"
    released_free = "released_free"
    held_unknown = "held_unknown"
    released_platform_absorbed = "released_platform_absorbed"


class HoldState(enum.StrEnum):
    held = "held"
    settled = "settled"
    released = "released"
    unknown = "unknown"


class ReservationKind(enum.StrEnum):
    preparation = "preparation"
    inference = "inference"
    journal_bytes = "journal_bytes"


class LeaseKind(enum.StrEnum):
    """r1 R46: which phase a lease fences. Preparation and inference are separate
    attempts on separate counters, so a superseded preparation worker cannot pass
    its token off as an inference lease (or the reverse)."""

    preparation = "preparation"
    inference = "inference"


class ChunkEventType(enum.StrEnum):
    progress = "progress"
    delta = "delta"
    usage = "usage"
    error = "error"
    terminal = "terminal"


class OutboxKind(enum.StrEnum):
    prepare_dispatch = "prepare_dispatch"
    inference_dispatch = "inference_dispatch"
    usage_projection = "usage_projection"
    trace_projection = "trace_projection"
    feedback_projection = "feedback_projection"
    judge_projection = "judge_projection"
    callback_delivery = "callback_delivery"


class TraceMode(enum.StrEnum):
    off = "off"
    minimal = "minimal"
    full = "full"


class TraceLossReason(enum.StrEnum):
    none = "none"
    memory_budget = "memory_budget"
    metadata_budget = "metadata_budget"
    queue_full = "queue_full"
    disk_budget = "disk_budget"
    disk_error = "disk_error"
    shutdown = "shutdown"
    malformed = "malformed"
    abandoned = "abandoned"          # r1 R37: a capture the sink reaped or the caller dropped


class TraceOfferResult(enum.StrEnum):
    accepted_in_memory = "accepted_in_memory"
    # `dropped` means "nothing was stored", not "a loss was counted": an off-mode capture
    # answers `dropped` while counting neither a loss nor a drop (R42), so a caller must
    # not read this value as an error to report or retry.
    dropped = "dropped"


class FeedbackChannel(enum.StrEnum):
    api = "api"
    console = "console"


class AuthorRole(enum.StrEnum):
    customer = "customer"
    operator = "operator"
    judge = "judge"


class FeedbackName(enum.StrEnum):
    """Every name a *stored* feedback entry may carry (r1 R3, R43).

    The first four are the signals a client may submit and the name fixes the
    value's type (`research/traces/06` §2). `calibration_label` is a stored-only
    name: `FeedbackService.accept` refuses it as input, and only the operator path
    `label_calibration` creates one, which mirrors the console's
    `FEEDBACK_NAMES` / `FEEDBACK_ENTRY_NAMES` split (R43). Keeping the two lists
    apart is what makes "a client cannot author an operator label" a property of
    the vocabulary rather than one more check somebody has to remember.
    """

    thumb = "thumb"
    rating = "rating"
    correction = "correction"
    comment = "comment"
    calibration_label = "calibration_label"


# The submittable subset, in the console's order. `FeedbackName` is the entry set.
FEEDBACK_INPUT_NAMES: tuple[FeedbackName, ...] = (
    FeedbackName.thumb, FeedbackName.rating, FeedbackName.correction, FeedbackName.comment,
)


class CalibrationLabel(enum.StrEnum):
    """r1 R43: the verdict an operator records against a rubric version."""

    correct = "correct"
    partially_correct = "partially_correct"
    incorrect = "incorrect"
    unusable = "unusable"


class JudgeResolution(enum.StrEnum):
    """r1 R8: how an operator resolves an ambiguous judge run."""

    adopt_provider_evidence = "adopt_provider_evidence"
    release_reservation = "release_reservation"


class JudgeRunState(enum.StrEnum):
    dry_run = "dry_run"
    reserved = "reserved"
    submitting = "submitting"
    submitted = "submitted"
    ambiguous = "ambiguous"
    collecting = "collecting"
    settled = "settled"
    quarantined = "quarantined"
    cancelled = "cancelled"


class UploadState(enum.StrEnum):
    created = "created"
    finalized = "finalized"
    aborted = "aborted"
    expired = "expired"


class Role(enum.StrEnum):
    owner = "owner"
    member = "member"
    operator = "operator"
    service = "service"


class MediaKind(enum.StrEnum):
    """Refinement: how the source arrived. Uploads are the only durable form a
    JSON body may name; `inline` and `url` are staged into one before acceptance."""

    inline = "inline"
    url = "url"
    upload = "upload"


class ContentState(enum.StrEnum):
    """Trace content availability (08 §9), shared with the console."""

    available = "available"
    metadata_only = "metadata_only"
    pending = "pending"
    lost = "lost"
    expired = "expired"
    off = "off"


# --- annotated scalars -------------------------------------------------------
def _utc(value: object) -> object:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("timestamps must be timezone aware (UTC)")
        return value.astimezone(timezone.utc)
    return value


def _rfc3339(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


Timestamp = Annotated[datetime, BeforeValidator(_utc), PlainSerializer(_rfc3339, return_type=str)]
Money = Annotated[Decimal, BeforeValidator(money.parse),
                  PlainSerializer(money.format_money, return_type=str)]
UuidStr = Annotated[str, BeforeValidator(lambda v: ids.require_request_id(v) if isinstance(v, str) else v)]
JsonObject = dict[str, Any]


class Record(BaseModel):
    """Frozen, closed, versioned."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION


# --- identity and consent ----------------------------------------------------
class AuthContext(Record):
    org_id: UuidStr
    key_id: UuidStr
    principal: str                      # key id, user id or service name
    role: Role
    entitlement_version: int
    legacy_key: bool = False

    @property
    def is_operator(self) -> bool:
        return self.role is Role.operator


class OrgEntitlements(Record):
    """r1 R24: what an organization may run, stated so the default cannot be read
    as a denial.

    * `model_ids is None` - the platform default set; no per-org decision recorded.
    * `model_ids == ()` - **nothing entitled**: every admission is
      `model_not_entitled`. A deliberate fail-closed state, not an empty field.
    * a non-empty tuple - exactly those models, and nothing else.

    `limits` keys come from the closed `ENTITLEMENT_LIMIT_NAMES` set, so an unknown
    control is `invalid_request` rather than a silently ignored one.
    """

    org_id: UuidStr
    model_ids: tuple[str, ...] | None = None
    # r1 R52/R54: strict. `True` is not a limit of 1, and `"8"` is not eight.
    limits: dict[str, StrictInt] = Field(default_factory=dict)
    updated_at: Timestamp | None = None
    updated_by: str | None = None

    @model_validator(mode="after")
    def _limits_are_known_and_bounded(self) -> OrgEntitlements:
        unknown = sorted(set(self.limits) - set(limits.ENTITLEMENT_LIMIT_NAMES))
        if unknown:
            raise ValueError(f"unknown entitlement limits: {unknown}")
        for name, value in self.limits.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"entitlement limit {name} must be an integer")
            if not 0 <= value <= limits.MAX_ENTITLEMENT_LIMIT:
                raise ValueError(f"entitlement limit {name} must be in "
                                 f"0..{limits.MAX_ENTITLEMENT_LIMIT}")
        return self

    def allows(self, model_revision: str) -> bool | None:
        """True/False for a recorded decision, `None` for "use the platform default"."""
        if self.model_ids is None:
            return None
        return model_revision in self.model_ids


class ConsentSnapshot(Record):
    org_id: UuidStr
    consent_version: int
    trace_mode: TraceMode
    # r1 R43: retention is 1..90 days wherever it is validated. Zero days is not a
    # retention policy - "keep nothing" is `TraceMode.off`.
    content_retention_days: int = Field(ge=limits.MIN_CONTENT_RETENTION_DAYS,
                                        le=limits.MAX_CONTENT_RETENTION_DAYS)
    evaluation_consent: bool
    effective_at: Timestamp
    revoked_at: Timestamp | None = None

    def is_current(self, now: datetime) -> bool:
        return self.effective_at <= now and (self.revoked_at is None or now < self.revoked_at)

    def allows_evaluation(self, now: datetime) -> bool:
        return self.evaluation_consent and self.trace_mode is TraceMode.full and self.is_current(now)


# --- money and pricing -------------------------------------------------------
class PriceSnapshot(Record):
    price_version: str
    currency: Literal["USD"] = "USD"
    model_revision: str
    # A negative rate would turn a debit into a credit at settlement.
    input_rate_per_million: Money = Field(ge=0)
    output_rate_per_million: Money = Field(ge=0)
    token_rules_version: str
    captured_at: Timestamp

    def debit(self, prompt_tokens: int, completion_tokens: int) -> Decimal:
        return money.debit(prompt_tokens, completion_tokens,
                           self.input_rate_per_million, self.output_rate_per_million)

    def maximum_hold(self, max_input_tokens: int, max_output_tokens: int) -> Decimal:
        return money.maximum_hold(max_input_tokens, max_output_tokens,
                                  self.input_rate_per_million, self.output_rate_per_million)


class Usage(Record):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    certainty: UsageCertainty = UsageCertainty.authoritative

    @model_validator(mode="after")
    def _total_adds_up(self) -> Usage:
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("total_tokens must equal prompt_tokens + completion_tokens")
        return self

    @classmethod
    def of(cls, prompt_tokens: int, completion_tokens: int,
           certainty: UsageCertainty = UsageCertainty.authoritative) -> Usage:
        return cls(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                   total_tokens=prompt_tokens + completion_tokens, certainty=certainty)


# --- media -------------------------------------------------------------------
class MediaRef(Record):
    """Tenant-scoped, immutable, content-addressed. No caller-chosen path ever."""

    org_id: UuidStr
    handle: str                         # upl_... or a staged object handle
    kind: MediaKind
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    bytes: int = Field(ge=0)
    mime: str
    storage_ref: str                    # server-built key; never returned to callers
    profile_version: str = "v1"
    duration_s: float | None = None
    # R61 (2), amended: the pilot's local serving form of a *prepared* ref, written by
    # preparation (M2) - `<PROCESSING_CACHE_DIR>/<org uuid>/<profile_version>/<digest16>/
    # source.<ext>`, mirroring the object key - never caller-derived, and never the
    # identity (`storage_ref` is). None for a source ref or with no local cache.
    local_path: str | None = None


# --- request and admission ---------------------------------------------------
class Budgets(Record):
    """r1 R4: the deadline budgets captured at admission, so a later
    configuration change never alters an accepted job. The store reads these,
    never its current settings, once the job exists."""

    preparation_s: float = Field(ge=0)
    queue_wait_s: float = Field(ge=0)
    generation_s: float = Field(ge=0)
    first_token_s: float = Field(ge=0)
    stall_s: float = Field(ge=0)

    @classmethod
    def of(cls, limits, execution_mode: ExecutionMode) -> Budgets:
        """From a `limits.PilotSettings`; the queue budget depends on the mode."""
        return cls(preparation_s=limits.preparation_timeout_s,
                   queue_wait_s=(limits.queue_wait_async_s
                                 if execution_mode is ExecutionMode.async_
                                 else limits.queue_wait_interactive_s),
                   generation_s=limits.generation_timeout_s,
                   first_token_s=limits.ttft_timeout_s, stall_s=limits.tpot_stall_s)


class NormalizedRequest(Record):
    request_id: UuidStr
    org_id: UuidStr
    key_id: UuidStr
    model_revision: str
    messages: tuple[JsonObject, ...]
    parameters: JsonObject = Field(default_factory=dict)
    payload_ref: str                    # durable immutable body
    payload_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    media: tuple[MediaRef, ...] = ()
    execution_mode: ExecutionMode
    max_input_tokens: int = Field(ge=0)
    max_output_tokens: int = Field(ge=0)
    created_at: Timestamp
    deadline_at: Timestamp
    trace_policy: ConsentSnapshot


class IdempotencyRef(Record):
    """Idempotency scope: org + operation + key. Payload hash decides replay vs 409."""

    org_id: UuidStr
    operation: str
    key: str | None = Field(default=None, max_length=limits.MAX_IDEMPOTENCY_KEY_CHARS)
    payload_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @property
    def scope(self) -> tuple[str, str, str | None]:
        return (self.org_id, self.operation, self.key)


class CapacityReservation(Record):
    request_id: UuidStr
    org_id: UuidStr
    key_id: UuidStr
    kind: ReservationKind
    amount: int = Field(ge=0)           # slots, or bytes for journal_bytes
    active: bool = True
    reserved_at: Timestamp


class OutboxEvent(Record):
    event_id: UuidStr
    aggregate_id: UuidStr
    kind: OutboxKind
    version: int = 1
    payload: JsonObject = Field(default_factory=dict)
    available_at: Timestamp


class Admission(Record):
    request_id: UuidStr
    job_handle: str
    org_id: UuidStr
    key_id: UuidStr
    operation: str
    idempotency_key: str | None = None
    payload_hash: str
    price_snapshot: PriceSnapshot
    maximum_hold: Money = Field(ge=0)
    reservations: tuple[CapacityReservation, ...]
    state: JobState
    outbox: tuple[OutboxEvent, ...]
    admitted_at: Timestamp              # r1 R4: this is the job's accepted_at
    deadline_at: Timestamp
    budgets: Budgets                    # r1 R4: snapshot, never re-read from config
    # r1 R20: phase instants the store derives from the database clock at the
    # transition into that phase, never beyond `deadline_at`. `preparation` exists
    # from admission; `queue` appears at the first durable `queued` transition and
    # is never reset by a prepublication requeue (R5). Workers and the reaper
    # compare against these, not against a budget plus a local clock.
    preparation_deadline_at: Timestamp
    queue_deadline_at: Timestamp | None = None
    # r1 R38: the queue budget is cumulative time *in* the `queued` state, so the
    # store persists how much of it has been spent. At every queued transition
    # `queue_deadline_at = min(now + budget - used, deadline_at)`; leaving `queued`
    # adds that interval to `used`. A requeue therefore keeps only the remainder,
    # and an interactive job can still retry within its absolute deadline.
    queue_wait_used_s: float = Field(default=0.0, ge=0)
    replayed: bool = False              # true when an idempotent replay returned it


# --- execution ---------------------------------------------------------------
class Lease(Record):
    job_id: UuidStr
    # r1 R46: which phase this token fences, and therefore which generation counter it
    # is compared against. A job has one preparation attempt sequence and one inference
    # attempt sequence; `generation` is the ordinal within this lease's own kind.
    kind: LeaseKind
    generation: int = Field(ge=1)
    worker_id: str
    acquired_at: Timestamp              # database clock
    expires_at: Timestamp
    # r1 R20: derived at claim from the database clock and the job's budgets, capped
    # by `deadline_at`. The worker enforces them: it stops working at
    # `generation_deadline_at` - which on a **preparation** lease is the job's
    # `preparation_deadline_at`, that phase having one deadline - and gives up on a
    # first token at `first_token_deadline_at` instead of timing a budget locally.
    generation_deadline_at: Timestamp
    first_token_deadline_at: Timestamp | None = None

    @model_validator(mode="after")
    def _phase_deadlines_are_ordered(self) -> Lease:
        if self.kind is LeaseKind.inference and self.first_token_deadline_at is None:
            raise ValueError("an inference lease carries a first-token deadline")
        if self.kind is LeaseKind.preparation and self.first_token_deadline_at is not None:
            # There is no first token to wait for before the job has even been queued.
            raise ValueError("a preparation lease has no first-token deadline")
        if self.first_token_deadline_at is not None \
                and self.first_token_deadline_at > self.generation_deadline_at:
            raise ValueError("the first-token deadline cannot outlast the generation deadline")
        return self


class Cursor(Record):
    generation: int = Field(ge=0)
    sequence: int = Field(ge=0)

    @property
    def token(self) -> str:
        return f"{self.generation}-{self.sequence}"

    @classmethod
    def parse(cls, token: str) -> Cursor:
        """`Last-Event-ID` / SSE `id`. A malformed cursor is a 400, not a guess."""
        if not isinstance(token, str):
            raise errors.InvalidCursor(f"cursor must be a string, not {type(token).__name__}")
        generation, _, sequence = token.partition("-")
        if not generation.isdigit() or not sequence.isdigit():
            raise errors.InvalidCursor(f"cursor must be <generation>-<sequence>: {token!r}")
        return cls(generation=int(generation), sequence=int(sequence))


class Chunk(Record):
    job_id: UuidStr
    generation: int = Field(ge=1)
    sequence: int = Field(ge=1)
    event_type: ChunkEventType
    payload: JsonObject
    bytes: int = Field(ge=0)
    persisted_at: Timestamp
    expires_at: Timestamp

    @property
    def cursor(self) -> Cursor:
        return Cursor(generation=self.generation, sequence=self.sequence)


class TerminalOutcome(Record):
    """One per job. `usage is None` is exactly "usage unknown": a present usage
    must be authoritative, because output chunks never bill.

    F2C.b: `result_expires_at` is the instant the settling transaction PERSISTED for a
    success's result (database clock; the store's configured TTL at settlement), carried
    unchanged on every committed read. The store decides it, like `settlement_state`: a
    proposal's value is ignored. It is absent on every other outcome, on a proposal, and
    on a record written before it was carried - and absent never means "recompute it":
    `v2.lifecycle.read_outcome` answers such a success `unavailable`.
    """

    job_id: UuidStr
    state: JobState
    cause: TerminalCause
    usage: Usage | None = None
    result_ref: str | None = None
    settlement_state: SettlementState
    debit: Money = Field(default=money.ZERO, ge=0)
    settled_at: Timestamp
    reconcile_after: Timestamp | None = None    # set when settlement_state is held_unknown
    result_expires_at: Timestamp | None = None  # F2C.b: persisted at settlement, successes only

    @model_validator(mode="after")
    def _consistent(self) -> TerminalOutcome:
        if self.state not in TERMINAL_STATES:
            raise ValueError(f"{self.state} is not a terminal state")
        if self.state not in states_for_cause(self.cause):
            raise ValueError(f"cause {self.cause} cannot carry state {self.state}; allowed: "
                             f"{sorted(s.value for s in states_for_cause(self.cause))}")
        if self.usage is not None and self.usage.certainty is not UsageCertainty.authoritative:
            raise ValueError("a present usage must be authoritative; unknown usage is usage=None")
        if self.debit != 0 and self.settlement_state is not SettlementState.settled:
            raise ValueError("only a settled outcome carries a nonzero debit")
        if (self.settlement_state is SettlementState.held_unknown) != (self.reconcile_after is not None):
            raise ValueError("held_unknown requires reconcile_after, and nothing else may set it")
        if self.result_expires_at is not None and (
                self.state is not JobState.succeeded or not self.result_ref
                or self.result_expires_at <= self.settled_at):
            raise ValueError("only a success with a result carries result_expires_at, "
                             "and it follows settled_at")
        return self


class Work(Record):
    """r1 R46: everything a lease holder must execute, and nothing else.

    `JobStore.load_work(lease)` is the only way to get one, and it is fenced like a
    mutation: a stale, foreign or wrong-kind lease gets a typed refusal and **no data**.
    Before this existed a worker had to be handed the request out of band, which meant
    nothing stopped a fenced worker from still holding everything it needed to run.

    `media_refs` are the staged sources, `prepared_refs` what preparation produced (empty
    on a preparation lease, which is what fills them). The price is the admission's
    snapshot, never re-read at execution time, and `budgets` is the R4 snapshot.
    """

    request: NormalizedRequest
    media_refs: tuple[MediaRef, ...] = ()
    prepared_refs: tuple[MediaRef, ...] = ()
    price_snapshot: PriceSnapshot
    budgets: Budgets
    # Preparation's exact prompt token count, stored once with the prepared refs (D2's
    # `jobs.prepared_prompt_tokens`, filled by D3's `load_work`; W2's request). None until
    # preparation has counted, and never beyond the ceiling the hold was sized for.
    prompt_tokens: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _prompt_fits_the_admitted_ceiling(self) -> Work:
        if self.prompt_tokens is not None and self.prompt_tokens > self.request.max_input_tokens:
            raise ValueError("prompt_tokens exceeds the request's max_input_tokens, which the "
                             "hold was sized for")
        return self


class PreparedRequest(Record):
    """Refinement: what the engine port receives after preparation."""

    request_id: UuidStr
    model_revision: str
    messages: tuple[JsonObject, ...]
    parameters: JsonObject = Field(default_factory=dict)
    media: tuple[MediaRef, ...] = ()
    max_output_tokens: int = Field(ge=0)
    prompt_tokens: int = Field(ge=0)    # exact, from preparation
    profile_version: str = "v1"


class EngineEvent(Record):
    """Refinement: one canonical engine event, before it becomes a Chunk."""

    type: ChunkEventType
    payload: JsonObject = Field(default_factory=dict)
    usage: Usage | None = None


DISPATCH_KINDS = (OutboxKind.prepare_dispatch, OutboxKind.inference_dispatch)


class IndexEvent(Record):
    """Scheduling index membership. Never authorizes execution by itself.

    r1 R52: the event carries its **kind**, so a preparation worker can be fed from the
    index rather than from a side channel. Without it a candidate said only "this job
    wants something done", and Q had no way to hand it to the right pool - a preparation
    worker would claim an inference candidate, be refused by `claim`, and the job would
    sit there while the index looked busy.
    """

    event_id: UuidStr
    job_id: UuidStr
    org_id: UuidStr
    key_id: UuidStr
    kind: OutboxKind = OutboxKind.inference_dispatch
    execution_mode: ExecutionMode
    available_at: Timestamp
    attempt: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _is_a_dispatch(self) -> IndexEvent:
        if self.kind not in DISPATCH_KINDS:
            raise ValueError(f"{self.kind} is not a dispatch kind; the index carries "
                             f"{', '.join(k.value for k in DISPATCH_KINDS)}")
        return self

    @property
    def is_preparation(self) -> bool:
        return self.kind is OutboxKind.prepare_dispatch


# --- observability -----------------------------------------------------------
class TraceEnvelope(Record):
    request_id: UuidStr
    org_id: UuidStr
    key_id: UuidStr
    mode: TraceMode
    started_at: Timestamp
    completed_at: Timestamp | None = None
    content_complete: bool = False
    content_ref: str | None = None
    content_bytes: int = Field(default=0, ge=0)
    metadata_bytes: int = Field(default=0, ge=0)
    loss_reason: TraceLossReason = TraceLossReason.none
    request_schema_version: int = SCHEMA_VERSION
    model_revision: str
    price_version: str

    @model_validator(mode="after")
    def _content_honest(self) -> TraceEnvelope:
        if self.content_complete and self.loss_reason is not TraceLossReason.none:
            raise ValueError("a lossy capture is never content_complete")
        if self.content_complete and self.content_ref is None:
            raise ValueError("content_complete requires a content_ref")
        if self.mode is not TraceMode.full and (self.content_ref is not None
                                                or self.content_bytes > 0
                                                or self.content_complete):
            # r1 R12 (and 01's privacy rule): `minimal` is metadata only and `off`
            # produces no row at all. Tying content to the mode here is what stops
            # a minimal envelope from carrying uncharged, unconsented content.
            raise ValueError(f"{self.mode} mode never carries content")
        return self

    @property
    def carries_content(self) -> bool:
        return self.content_bytes > 0 or self.content_ref is not None


def no_float_value(data: object) -> object:
    """A JSON `1.0` is not a rating. Pydantic's lax mode would coerce it to 1, so
    the float is refused before any member of the union sees it (r1 R3)."""
    if isinstance(data, dict) and isinstance(data.get("value"), float):
        raise ValueError("value must be a boolean, an integer or text, never a float")
    return data


def check_feedback_value(name: FeedbackName, value: object) -> None:
    """r1 R3 / `research/traces/06` §2: the name fixes the value's type and range."""
    if name is FeedbackName.thumb:
        if not isinstance(value, bool):
            raise ValueError("a thumb value is a boolean")
    elif name is FeedbackName.rating:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("a rating value is an integer")
        if not 1 <= value <= 5:
            raise ValueError("a rating value is between 1 and 5")
    elif name is FeedbackName.calibration_label:
        # r1 R43: a label's value is one of a closed vocabulary, not free text.
        if isinstance(value, bool) or value not in tuple(CalibrationLabel):
            raise ValueError("a calibration_label value is one of "
                             f"{', '.join(label.value for label in CalibrationLabel)}")
    else:                                # correction, comment
        if isinstance(value, bool) or not isinstance(value, str) or not value.strip():
            raise ValueError(f"a {name} value is nonempty text")
    if isinstance(value, str) and len(value) > limits.MAX_FEEDBACK_TEXT_CHARS:
        # r1 R43: a comment is a note, not an upload channel.
        raise ValueError(f"a feedback value is at most {limits.MAX_FEEDBACK_TEXT_CHARS} characters")


class Feedback(Record):
    """r1 R3/R43: one signal per record — `name` fixes the type of `value`.

    A calibration label is *this* record with `name=calibration_label`,
    `calibration_set=True` and an integer `rubric_version`; there is no second
    shape and no free-text calibration set. `calibration_set` is a boolean because
    membership is one fact, and the rubric it was labelled against is the integer
    `research/traces/04` stores as a `UInt16`.
    """

    feedback_id: str
    request_id: UuidStr
    org_id: UuidStr
    author_principal: str
    author_role: AuthorRole             # server-set
    channel: FeedbackChannel            # server-set
    name: FeedbackName
    value: bool | int | str
    comment: str | None = Field(default=None, max_length=limits.MAX_FEEDBACK_TEXT_CHARS)
    calibration_set: bool = False       # operator authorization required
    # r1 R54: **strict**. Under pydantic's lax mode `True` validated as 1, so a boolean
    # was a rubric version and `"3"` was three - the record was looser than the port.
    rubric_version: StrictInt | None = Field(default=None, ge=limits.MIN_RUBRIC_VERSION,
                                             le=limits.MAX_RUBRIC_VERSION)
    # r1 R50: server-set, never client-settable, and it **never leaves the service** -
    # the public projection is `wire.FeedbackEntry`, which has no such field. It records
    # that a *platform operator* made this entry, which is what R41's masking keys on;
    # `author_role` cannot, because `accept` always stores `customer` (R31), so a branch
    # on the role was dead code and a customer read the operator's address.
    by_operator: bool = False
    created_at: Timestamp

    @model_validator(mode="before")
    @classmethod
    def _value_is_never_a_float(cls, data: object) -> object:
        return no_float_value(data)

    @model_validator(mode="after")
    def _value_matches_the_name(self) -> Feedback:
        check_feedback_value(self.name, self.value)
        # r1 R43: the three calibration fields are one fact, so they cannot disagree.
        # A `calibration_set` customer signal would be an unauthorized label, and a
        # label with no rubric version is a verdict against nothing.
        label = self.name is FeedbackName.calibration_label
        if label != self.calibration_set:
            raise ValueError("calibration_set is true exactly on a calibration_label entry")
        if label != (self.rubric_version is not None):
            raise ValueError("rubric_version is required on a calibration_label entry "
                             "and null on every other entry")
        # r1 R54: a label is an operator's verdict. A `calibration_label` row attributed
        # to a customer would be operator data with a customer's provenance, which is
        # exactly the forgery R31 exists to prevent.
        if label and self.author_role is not AuthorRole.operator:
            raise ValueError("a calibration_label entry is authored by an operator")
        if label and not self.by_operator:
            raise ValueError("a calibration_label entry is made by an operator (R50)")
        # r1 R55: and the converse, for *any* row. An `operator` author role without the
        # marker was constructible, and the marker is what R41's masking keys on - so such
        # a row would be operator-authored and read to a customer with the operator's
        # principal intact, which is the leak R50 exists to close.
        if self.author_role is AuthorRole.operator and not self.by_operator:
            raise ValueError("an operator-authored entry is marked by_operator (R55)")
        return self


def visible_feedback(items: tuple[Feedback, ...], *, operator: bool) -> tuple[Feedback, ...]:
    """The one feedback visibility rule, applied at the port and again at the wire.

    * **R35/R49: a feedback list never carries a calibration label, for anyone.** Labels
      are operator data read only through `list_calibration`/`calibration.list`. An
      operator listing a request's feedback sees its ordinary entries, with their real
      principals; it does not see labels there, so no viewer has to remember which list
      it is reading.
    * **R41/R50: a non-operator viewer reads `platform` as the principal of any
      `by_operator` entry.** A tenant learns that the platform acted, never who at the
      platform did it.

    One function because two copies of a visibility rule is one copy that gets fixed.
    """
    visible = []
    for item in items:
        if item.calibration_set:
            continue
        if not operator and item.by_operator:
            item = item.model_copy(update={"author_principal": PLATFORM_ACTOR})
        visible.append(item)
    return tuple(visible)


class JudgeRun(Record):
    run_id: UuidStr
    org_id: UuidStr
    # F2R item 5: each id is the frozen lower-case UUIDv4 form, and unique within the run
    # (a duplicate would be graded, reserved for and billed twice).
    sample_ids: tuple[UuidStr, ...] = ()
    consent: ConsentSnapshot
    # r1 R43: an integer everywhere - runs, samples, scores and calibration labels -
    # matching `research/traces/04`'s `UInt16` and the console's `rubric_version`.
    rubric_version: StrictInt = Field(ge=limits.MIN_RUBRIC_VERSION,
                                      le=limits.MAX_RUBRIC_VERSION)
    model_revision: str
    reserved_cost: Money = Field(default=money.ZERO, ge=0)
    actual_cost: Money | None = None
    submit_intent: UuidStr | None = None
    external_batch_id: str | None = None
    state: JudgeRunState
    created_at: Timestamp
    reconciled_at: Timestamp | None = None

    @model_validator(mode="after")
    def _unique_samples(self) -> JudgeRun:
        if len(set(self.sample_ids)) != len(self.sample_ids):
            raise ValueError("a judge run's sample ids are unique")
        return self


class JudgeSample(Record):
    """F2R item 5 / IR-7: one evaluated sample, spelled exactly as the console's
    `JudgeSample` and D1's `console_judge_runs` view emit it. `rubric_version` is the
    sample's own; `request_id` is null once the scored trace is deleted; `scores` is
    empty until something is collected (their shape is the console's `JudgeScore`)."""

    sample_id: UuidStr
    rubric_version: StrictInt = Field(ge=limits.MIN_RUBRIC_VERSION,
                                      le=limits.MAX_RUBRIC_VERSION)
    request_id: UuidStr | None
    scores: tuple[JsonObject, ...] = ()


class AccountingRegime(enum.StrEnum):
    """IR-7: the console's `ACCOUNTING_REGIMES`, the name for D1's
    `usage_events.settlement_regime` (`legacy` -> `legacy_usd`, `pilot` -> `pilot`).
    A `legacy_usd` row is historical USD: it keeps NULL execution mode, job state, usage
    certainty and trace mode, and is never replayed into a debit."""

    legacy_usd = "legacy_usd"
    pilot = "pilot"
