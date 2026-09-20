"""HTTP/SSE bodies. Refinement: 08 §2 keeps `records.py` to the contracts-v1
table, so the public wire shapes the fixtures pin live here.

These are what G returns and what the console/SDK parse. They are deliberately
thin: every one is built from records, and none of them carries a storage key, a
signed URL or another tenant's identifier.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .codec import compact_bytes
from .records import (ChunkEventType, ContentState, ExecutionMode, Feedback, FeedbackChannel,
                      JobState, JsonObject, MediaRef, TerminalCause, Timestamp,
                      TraceEnvelope, UploadState, Usage, UuidStr)

DONE = "[DONE]"


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
    """What a client may send: no channel, no author role, no calibration flag."""

    request_id: UuidStr
    rating: int | None = Field(default=None, ge=-1, le=1)
    correction: str | None = None


class TraceExport(WireModel):
    envelope: TraceEnvelope
    content_state: ContentState
    content: JsonObject | None = None

    @classmethod
    def of(cls, envelope: TraceEnvelope, content_state: ContentState,
           content: dict[str, Any] | None = None) -> TraceExport:
        return cls(envelope=envelope, content_state=content_state, content=content)


class FeedbackList(WireModel):
    items: tuple[Feedback, ...] = ()
    next_cursor: str | None = None
