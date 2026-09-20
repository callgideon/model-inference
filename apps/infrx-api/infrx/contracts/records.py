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

from pydantic import (BaseModel, ConfigDict, Field, PlainSerializer, model_validator)
from pydantic.functional_validators import BeforeValidator

from . import errors, ids, money

SCHEMA_VERSION = 1


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


class TraceOfferResult(enum.StrEnum):
    accepted_in_memory = "accepted_in_memory"
    dropped = "dropped"


class FeedbackChannel(enum.StrEnum):
    api = "api"
    console = "console"


class AuthorRole(enum.StrEnum):
    customer = "customer"
    operator = "operator"
    judge = "judge"


class FeedbackName(enum.StrEnum):
    """r1 R3, from `research/traces/06` §2: the name fixes the value's type."""

    thumb = "thumb"
    rating = "rating"
    correction = "correction"
    comment = "comment"


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


class ConsentSnapshot(Record):
    org_id: UuidStr
    consent_version: int
    trace_mode: TraceMode
    content_retention_days: int = Field(ge=0, le=90)
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
    key: str | None = Field(default=None, max_length=255)
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
    replayed: bool = False              # true when an idempotent replay returned it


# --- execution ---------------------------------------------------------------
class Lease(Record):
    job_id: UuidStr
    generation: int = Field(ge=1)
    worker_id: str
    acquired_at: Timestamp              # database clock
    expires_at: Timestamp
    # r1 R20: derived at claim from the database clock and the job's budgets, capped
    # by `deadline_at`. The worker enforces them: it stops generating at
    # `generation_deadline_at` and gives up on a first token at
    # `first_token_deadline_at` instead of timing a budget locally.
    generation_deadline_at: Timestamp
    first_token_deadline_at: Timestamp

    @model_validator(mode="after")
    def _phase_deadlines_are_ordered(self) -> Lease:
        if self.first_token_deadline_at > self.generation_deadline_at:
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
    must be authoritative, because output chunks never bill."""

    job_id: UuidStr
    state: JobState
    cause: TerminalCause
    usage: Usage | None = None
    result_ref: str | None = None
    settlement_state: SettlementState
    debit: Money = Field(default=money.ZERO, ge=0)
    settled_at: Timestamp
    reconcile_after: Timestamp | None = None    # set when settlement_state is held_unknown

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


class IndexEvent(Record):
    """Scheduling index membership. Never authorizes execution by itself."""

    event_id: UuidStr
    job_id: UuidStr
    org_id: UuidStr
    key_id: UuidStr
    execution_mode: ExecutionMode
    available_at: Timestamp
    attempt: int = Field(default=0, ge=0)


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
    else:                                # correction, comment
        if isinstance(value, bool) or not isinstance(value, str) or not value.strip():
            raise ValueError(f"a {name} value is nonempty text")


class Feedback(Record):
    """r1 R3: one signal per record — `name` fixes the type of `value`."""

    feedback_id: str
    request_id: UuidStr
    org_id: UuidStr
    author_principal: str
    author_role: AuthorRole             # server-set
    channel: FeedbackChannel            # server-set
    name: FeedbackName
    value: bool | int | str
    comment: str | None = None
    calibration_set: str | None = None  # operator authorization required
    created_at: Timestamp

    @model_validator(mode="before")
    @classmethod
    def _value_is_never_a_float(cls, data: object) -> object:
        return no_float_value(data)

    @model_validator(mode="after")
    def _value_matches_the_name(self) -> Feedback:
        check_feedback_value(self.name, self.value)
        return self


class JudgeRun(Record):
    run_id: UuidStr
    org_id: UuidStr
    sample_ids: tuple[str, ...] = ()
    consent: ConsentSnapshot
    rubric_version: str
    model_revision: str
    reserved_cost: Money = Field(default=money.ZERO, ge=0)
    actual_cost: Money | None = None
    submit_intent: UuidStr | None = None
    external_batch_id: str | None = None
    state: JudgeRunState
    created_at: Timestamp
    reconciled_at: Timestamp | None = None
