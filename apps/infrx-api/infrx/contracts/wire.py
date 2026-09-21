"""HTTP/SSE bodies. Refinement: 08 §2 keeps `records.py` to the contracts-v1
table, so the public wire shapes the fixtures pin live here.

These are what G returns and what the console/SDK parse. They are deliberately
thin: every one is built from records, and none of them carries a storage key, a
signed URL or another tenant's identifier.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .codec import compact_bytes
from . import errors
from .limits import MAX_FEEDBACK_TEXT_CHARS, MAX_IDEMPOTENCY_KEY_CHARS, MAX_PAGE_LIMIT
from .records import (FEEDBACK_INPUT_NAMES, AuthorRole, ChunkEventType, ContentState,
                      ExecutionMode, Feedback, FeedbackChannel, FeedbackName, JobState, JsonObject,
                      MediaRef, TerminalCause, Timestamp, TraceEnvelope, TraceLossReason,
                      TraceMode, UploadState, Usage, UuidStr, check_feedback_value, no_float_value)

DONE = "[DONE]"

# The header vocabulary of 08 §3, spelled once so G, W and the console never have
# to hand-type it. `X-Infrx-Accept-Async` is superseded by `Prefer: respond-async`.
HEADER_AUTHORIZATION = "Authorization"
HEADER_IDEMPOTENCY_KEY = "Idempotency-Key"
HEADER_PREFER = "Prefer"
HEADER_LAST_EVENT_ID = "Last-Event-ID"
HEADER_INFERENCE_ID = "Inference-Id"
HEADER_PREFERENCE_APPLIED = "Preference-Applied"
HEADER_RETRY_AFTER = "Retry-After"
HEADER_IDEMPOTENCY_REPLAYED = "Idempotency-Replayed"
HEADER_SERVER_TIMING = "Server-Timing"
PREFER_RESPOND_ASYNC = "respond-async"
IDEMPOTENCY_KEY_MAX_LEN = MAX_IDEMPOTENCY_KEY_CHARS

# What a customer session sees in place of an operator's identity (r1 R41). A tenant
# learns that the platform acted, never which person at the platform did it.
PLATFORM_ACTOR = "platform"


class WireModel(BaseModel):
    """Frozen and closed like a record, but not itself versioned: the
    `schema_version` stays on the records a body carries."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ChatUsage(WireModel):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)

    @classmethod
    def of(cls, usage: Usage) -> ChatUsage:
        return cls(prompt_tokens=usage.prompt_tokens, completion_tokens=usage.completion_tokens,
                   total_tokens=usage.total_tokens)


class ChatMessage(WireModel):
    role: str
    content: str | None = None
    reasoning_content: str | None = None


class ChatChoice(WireModel):
    index: int = Field(ge=0)
    message: ChatMessage
    finish_reason: str | None = None


class ChatCompletionResponse(WireModel):
    id: str                             # chatcmpl-<request_id>
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: tuple[ChatChoice, ...]
    usage: ChatUsage


class ChatChunkChoice(WireModel):
    index: int = Field(ge=0)
    delta: JsonObject = Field(default_factory=dict)
    finish_reason: str | None = None


class ChatCompletionChunk(WireModel):
    id: str
    object: Literal["chat.completion.chunk"] = "chat.completion.chunk"
    created: int
    model: str
    choices: tuple[ChatChunkChoice, ...] = ()
    usage: ChatUsage | None = None


class SseFrame(WireModel):
    """One SSE frame: a comment keepalive, or an id/data event."""

    comment: str | None = None
    id: str | None = None               # <generation>-<sequence>
    event: str | None = None
    data: JsonObject | str | None = None
    event_type: ChunkEventType | None = None    # which journal event this frame carries

    def render(self) -> str:
        if self.comment is not None:
            return f": {self.comment}\n\n"
        lines = []
        if self.id is not None:
            lines.append(f"id: {self.id}")
        if self.event is not None:
            lines.append(f"event: {self.event}")
        payload = self.data if isinstance(self.data, str) else compact_bytes(self.data).decode()
        lines.append(f"data: {payload}")
        return "\n".join(lines) + "\n\n"


class SseTranscript(WireModel):
    """The exact frame sequence a stream produces, keepalives included."""

    request_id: UuidStr
    frames: tuple[SseFrame, ...]

    def render(self) -> str:
        return "".join(frame.render() for frame in self.frames)


class JobAccepted(WireModel):
    """202 body. Only durable acceptance produces one."""

    job_handle: str
    request_id: UuidStr
    state: JobState
    execution_mode: ExecutionMode
    created_at: Timestamp
    deadline_at: Timestamp
    idempotency_replayed: bool = False


class JobStatus(WireModel):
    job_handle: str
    request_id: UuidStr
    state: JobState
    cause: TerminalCause | None = None
    created_at: Timestamp
    updated_at: Timestamp
    result_available: bool = False
    result_expires_at: Timestamp | None = None
    usage: ChatUsage | None = None
    usage_certainty: str | None = None


class JobResult(WireModel):
    job_handle: str
    request_id: UuidStr
    state: JobState
    cause: TerminalCause
    response: ChatCompletionResponse | None = None
    usage: ChatUsage | None = None
    completed_at: Timestamp


class UploadCreated(WireModel):
    """The destination is a constrained server-issued reference, never a raw key
    the caller can shape. Fixtures carry a placeholder, never a signed URL."""

    upload_handle: str
    destination_ref: str
    max_bytes: int = Field(ge=1)
    accepted_mime: tuple[str, ...]
    state: UploadState = UploadState.created
    expires_at: Timestamp


class UploadCompleted(WireModel):
    upload_handle: str
    state: UploadState
    media: MediaRef


class FeedbackAccepted(WireModel):
    """201 means PostgreSQL accepted it; provenance is server-set."""

    feedback_id: str
    request_id: UuidStr
    channel: FeedbackChannel
    author_role: str
    created_at: Timestamp
    replayed: bool = False


class FeedbackSubmission(WireModel):
    """What a client may send: no channel, no author role, no calibration flag.

    r1 R3: `name` and `value` are both required, so an empty body is a 400 rather
    than a row that says nothing; `comment` is the only optional field.

    r1 R43: `calibration_label` is a stored-entry name, not an input one. It is in
    `FeedbackName` because a row carries it, and refused here because only
    `label_calibration` may author one - the same `FEEDBACK_NAMES` versus
    `FEEDBACK_ENTRY_NAMES` split the console makes.
    """

    request_id: UuidStr
    name: FeedbackName
    value: bool | int | str
    comment: str | None = Field(default=None, max_length=MAX_FEEDBACK_TEXT_CHARS)

    @model_validator(mode="before")
    @classmethod
    def _value_is_never_a_float(cls, data: object) -> object:
        return no_float_value(data)

    @model_validator(mode="after")
    def _value_matches_the_name(self) -> FeedbackSubmission:
        if self.name not in FEEDBACK_INPUT_NAMES:
            raise ValueError(f"{self.name} is set by the server, not submitted by a client")
        check_feedback_value(self.name, self.value)
        return self


class TraceContentRequest(WireModel):
    model: str
    messages: tuple[JsonObject, ...] = ()
    params: JsonObject = Field(default_factory=dict)


class TraceContentError(WireModel):
    type: str
    code: str
    message: str

    @model_validator(mode="after")
    def _code_is_a_known_code(self) -> TraceContentError:
        if self.code not in errors.ALL_CODES:
            raise ValueError(f"{self.code} is not a contracts-v1 error code")
        return self


class TraceContentChoice(WireModel):
    index: int = Field(ge=0)
    finish_reason: str | None = None
    content: str | None = None
    reasoning_content: str | None = None


class TraceContentResponse(WireModel):
    status: int = Field(ge=100, le=599)
    choices: tuple[TraceContentChoice, ...] = ()
    error: TraceContentError | None = None


class TraceContentBody(WireModel):
    """r1 R47: the content object is `{v, request, response}`, exactly the shape
    `research/traces/04` §3.1 stores and the console's `TraceContentBody` renders.
    Media parts inside `request.messages` are references, never bytes."""

    v: Literal[1] = 1
    request: TraceContentRequest
    response: TraceContentResponse


class TraceExport(WireModel):
    """r1 R47: the public export of one trace.

    It carries **no storage key**. `content_ref` on the internal `TraceEnvelope` is
    an object key, so the export replaces it with `content_state` (availability) plus
    an opaque `content_handle` the server resolves after ownership and logical expiry
    - which is what makes wire.py's "none of them carries a storage key" true rather
    than aspirational. `test_fixtures.py` greps every wire fixture for a
    storage-key-shaped field.
    """

    request_id: UuidStr
    org_id: UuidStr
    key_id: UuidStr
    mode: TraceMode
    started_at: Timestamp
    completed_at: Timestamp | None = None
    content_complete: bool = False
    content_state: ContentState
    content_handle: str | None = None       # opaque; resolved server-side, never a key
    content_bytes: int = Field(default=0, ge=0)
    metadata_bytes: int = Field(default=0, ge=0)
    loss_reason: TraceLossReason = TraceLossReason.none
    model_revision: str
    price_version: str
    trace_schema_version: int = 1
    content: TraceContentBody | None = None

    @classmethod
    def of(cls, envelope: TraceEnvelope, content_state: ContentState,
           content: TraceContentBody | dict[str, Any] | None = None,
           content_handle: str | None = None) -> TraceExport:
        """Project an envelope. `envelope.content_ref` is deliberately dropped."""
        return cls(request_id=envelope.request_id, org_id=envelope.org_id,
                   key_id=envelope.key_id, mode=envelope.mode,
                   started_at=envelope.started_at, completed_at=envelope.completed_at,
                   content_complete=envelope.content_complete, content_state=content_state,
                   content_handle=content_handle, content_bytes=envelope.content_bytes,
                   metadata_bytes=envelope.metadata_bytes, loss_reason=envelope.loss_reason,
                   model_revision=envelope.model_revision, price_version=envelope.price_version,
                   trace_schema_version=envelope.request_schema_version, content=content)


class FeedbackList(WireModel):
    items: tuple[Feedback, ...] = ()
    next_cursor: str | None = None

    @classmethod
    def for_viewer(cls, items: tuple[Feedback, ...], *, operator: bool,
                   next_cursor: str | None = None) -> FeedbackList:
        """r1 R35/R41: what a viewer may be shown.

        A non-operator viewer gets no calibration labels at all (they are operator
        data) and never an operator principal: an operator-authored row reads
        `platform`. An operator viewer gets the rows as stored. Publishing the port's
        tuple straight into a body is how a customer ends up reading the name of the
        person who labelled their trace, so the projection lives here, once.
        """
        if operator:
            return cls(items=tuple(items), next_cursor=next_cursor)
        visible = tuple(
            item if item.author_role is not AuthorRole.operator
            else item.model_copy(update={"author_principal": PLATFORM_ACTOR})
            for item in items if not item.calibration_set)
        return cls(items=visible, next_cursor=next_cursor)
