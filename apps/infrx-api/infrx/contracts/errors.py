"""Domain errors, their HTTP mapping and the only error body customers see.

Two rules the whole platform depends on:

* the envelope `message` is a fixed safe string looked up from `MESSAGES`, never
  an upstream exception, a storage key, a URL or anything else that could carry a
  secret. Detail for operators goes in `DomainError.detail`, which is logged and
  never serialized.
* internal domain errors (`stale_lease`, `already_terminal`, ...) have no HTTP
  status: a route that lets one escape has a mapping bug, so `http_status` raises
  instead of quietly answering 500.
"""
from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict

# code -> (HTTP status, OpenAI-style error type)
HTTP_ERRORS: dict[str, tuple[int, str]] = {
    "invalid_request": (400, "invalid_request_error"),
    "unsupported_parameter": (400, "invalid_request_error"),
    "unsupported_media": (400, "invalid_request_error"),
    "media_fetch_failed": (400, "invalid_request_error"),
    "context_length_exceeded": (400, "invalid_request_error"),
    "invalid_cursor": (400, "invalid_request_error"),
    "invalid_api_key": (401, "authentication_error"),
    "insufficient_credit": (402, "insufficient_quota"),
    "forbidden": (403, "permission_error"),
    "org_suspended": (403, "permission_error"),
    "model_not_entitled": (403, "permission_error"),
    "not_found": (404, "not_found_error"),
    "idempotency_conflict": (409, "conflict_error"),
    "result_pending": (409, "conflict_error"),
    "state_conflict": (409, "conflict_error"),
    "result_expired": (410, "gone_error"),
    "upload_expired": (410, "gone_error"),
    "journal_expired": (410, "gone_error"),
    "replay_gap": (410, "gone_error"),
    "idempotency_expired": (410, "gone_error"),
    "request_too_large": (413, "invalid_request_error"),
    "capacity_exhausted": (429, "rate_limit_error"),
    "journal_capacity_exhausted": (429, "rate_limit_error"),
    "rate_limited": (429, "rate_limit_error"),
    "internal_error": (500, "server_error"),
    "dependency_unavailable": (503, "server_error"),
    "deadline_exceeded": (504, "server_error"),
}

# In-stream terminal error events reuse the envelope with these codes. They are
# not HTTP statuses: the response already started with 200.
STREAM_CODES = frozenset({"stream_interrupted", "status_unknown"})

# Raised and handled inside the platform; a route must translate them first.
INTERNAL_CODES = frozenset({
    "stale_lease", "already_terminal", "not_claimable", "capacity_unavailable",
    "budget_exceeded", "consent_missing", "ambiguous_submission", "journal_write_failed",
})

# Codes whose HTTP response always carries Retry-After.
RETRY_AFTER_CODES = frozenset({"capacity_exhausted", "journal_capacity_exhausted", "rate_limited",
                               "dependency_unavailable"})

MESSAGES: dict[str, str] = {
    "invalid_request": "The request is not valid.",
    "unsupported_parameter": "A requested parameter is not supported.",
    "unsupported_media": "The media type is not supported.",
    "media_fetch_failed": "The media source could not be retrieved.",
    "context_length_exceeded": "The request exceeds the model context limit.",
    "invalid_cursor": "The event cursor is not valid.",
    "invalid_api_key": "The API key is missing, malformed or revoked.",
    "insufficient_credit": "The organization does not have enough available credit.",
    "forbidden": "This action is not permitted.",
    "org_suspended": "The organization is suspended.",
    "model_not_entitled": "The organization is not entitled to this model.",
    "not_found": "The requested resource was not found.",
    "idempotency_conflict": "This idempotency key was used with a different request payload.",
    "result_pending": "The result is not available yet.",
    "state_conflict": "The job is not in a state that allows this operation.",
    "result_expired": "The result is no longer available.",
    "upload_expired": "The upload window has expired.",
    "journal_expired": "The event journal for this job has expired.",
    "replay_gap": "The requested events are no longer available for replay.",
    "idempotency_expired": "This idempotency key has expired; submit a new request.",
    "request_too_large": "The request body is too large.",
    "capacity_exhausted": "Capacity is exhausted; retry later.",
    "journal_capacity_exhausted": "Output journal capacity is exhausted; retry later.",
    "rate_limited": "Too many requests; retry later.",
    "internal_error": "An internal error occurred.",
    "dependency_unavailable": "A required dependency is unavailable; retry later.",
    "deadline_exceeded": "The request exceeded its deadline.",
    "stream_interrupted": "The stream was interrupted.",
    "status_unknown": "The final status of this request is unknown; check the job status.",
    "stale_lease": "The execution lease is stale.",
    "already_terminal": "The job already reached a terminal state.",
    "not_claimable": "The job cannot be claimed.",
    "capacity_unavailable": "The requested capacity is unavailable.",
    "budget_exceeded": "The evaluation budget would be exceeded.",
    "consent_missing": "Current consent for this operation is missing.",
    "ambiguous_submission": "The external submission outcome is unknown.",
    "journal_write_failed": "The output journal write failed.",
}

ALL_CODES = frozenset(HTTP_ERRORS) | STREAM_CODES | INTERNAL_CODES


def http_status(code: str) -> int:
    """HTTP status for a public code. Internal and in-stream codes raise: they
    must be translated deliberately, not answered with an accidental 500."""
    try:
        return HTTP_ERRORS[code][0]
    except KeyError:
        raise LookupError(f"{code!r} has no HTTP status; translate it at the route boundary") from None


def error_type(code: str) -> str:
    if code in HTTP_ERRORS:
        return HTTP_ERRORS[code][1]
    if code in STREAM_CODES:
        return "server_error"
    raise LookupError(f"{code!r} has no public error type")


class DomainError(Exception):
    """Base class. `code` is the contract; `detail` is operator-only."""

    code: ClassVar[str] = "internal_error"

    def __init__(self, detail: str | None = None, *, param: str | None = None,
                 retry_after_s: int | None = None, code: str | None = None,
                 infrx: dict[str, Any] | None = None) -> None:
        if code is not None:
            if code not in ALL_CODES:
                raise ValueError(f"unknown error code: {code!r}")
            self.code = code
        self.param = param
        self.retry_after_s = retry_after_s
        self.detail = detail
        self.infrx = dict(infrx) if infrx else {}
        super().__init__(f"{self.code}: {detail or self.message}")

    @property
    def message(self) -> str:
        """The fixed safe string the customer sees."""
        return MESSAGES[self.code]


# --- 400 ---------------------------------------------------------------------
class InvalidRequest(DomainError):
    code = "invalid_request"


class UnsupportedParameter(InvalidRequest):
    code = "unsupported_parameter"


class UnsupportedMedia(InvalidRequest):
    code = "unsupported_media"


class MediaFetchFailed(InvalidRequest):
    code = "media_fetch_failed"


class ContextLengthExceeded(InvalidRequest):
    code = "context_length_exceeded"


class InvalidCursor(InvalidRequest):
    code = "invalid_cursor"


class RequestTooLarge(InvalidRequest):      # 413, same type
    code = "request_too_large"


# --- 401 / 402 / 403 / 404 ---------------------------------------------------
class InvalidApiKey(DomainError):
    code = "invalid_api_key"


class InsufficientCredit(DomainError):
    code = "insufficient_credit"


class Forbidden(DomainError):
    code = "forbidden"


class OrgSuspended(Forbidden):
    code = "org_suspended"


class ModelNotEntitled(Forbidden):
    code = "model_not_entitled"


class NotFound(DomainError):
    """Also every ownership failure: a cross-tenant lookup is a 404, not a 403."""

    code = "not_found"


# --- 409 / 410 ---------------------------------------------------------------
class Conflict(DomainError):
    code = "state_conflict"


class IdempotencyConflict(Conflict):
    code = "idempotency_conflict"


class ResultPending(Conflict):
    code = "result_pending"


class StateConflict(Conflict):
    code = "state_conflict"


class Gone(DomainError):
    code = "result_expired"


class ResultExpired(Gone):
    code = "result_expired"


class UploadExpired(Gone):
    """r1 R22: an upload window that closed is not an expired *result*."""

    code = "upload_expired"


class JournalExpired(Gone):
    code = "journal_expired"


class ReplayGap(Gone):
    code = "replay_gap"


class IdempotencyExpired(Gone):
    code = "idempotency_expired"


# --- 429 ---------------------------------------------------------------------
class RateLimitError(DomainError):
    code = "rate_limited"

    def __init__(self, detail: str | None = None, *, retry_after_s: int = 1, **kw: Any) -> None:
        super().__init__(detail, retry_after_s=retry_after_s, **kw)


class CapacityExhausted(RateLimitError):
    code = "capacity_exhausted"


class JournalCapacityExhausted(RateLimitError):
    code = "journal_capacity_exhausted"


class RateLimited(RateLimitError):
    code = "rate_limited"


# --- 5xx ---------------------------------------------------------------------
class ServerError(DomainError):
    code = "internal_error"


class InternalError(ServerError):
    code = "internal_error"


class DependencyUnavailable(ServerError):
    code = "dependency_unavailable"

    def __init__(self, detail: str | None = None, *, retry_after_s: int = 5, **kw: Any) -> None:
        super().__init__(detail, retry_after_s=retry_after_s, **kw)


class DeadlineExceeded(ServerError):
    code = "deadline_exceeded"


# --- in-stream terminal events ----------------------------------------------
class StreamError(DomainError):
    code = "stream_interrupted"


class StreamInterrupted(StreamError):
    code = "stream_interrupted"


class StatusUnknown(StreamError):
    """Never asserts a committed terminal state."""

    code = "status_unknown"


# --- internal (no HTTP status) -----------------------------------------------
class InternalDomainError(DomainError):
    code = "internal_error"


class StaleLease(InternalDomainError):
    code = "stale_lease"


class AlreadyTerminal(InternalDomainError):
    code = "already_terminal"


class NotClaimable(InternalDomainError):
    code = "not_claimable"


class CapacityUnavailable(InternalDomainError):
    code = "capacity_unavailable"


class BudgetExceeded(InternalDomainError):
    code = "budget_exceeded"


class ConsentMissing(InternalDomainError):
    code = "consent_missing"


class AmbiguousSubmission(InternalDomainError):
    code = "ambiguous_submission"


class JournalWriteFailed(InternalDomainError):
    code = "journal_write_failed"


class ErrorBody(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    message: str
    type: str
    code: str
    param: str | None = None
    request_id: str | None = None
    infrx: dict[str, Any] | None = None


class ErrorEnvelope(BaseModel):
    """Schema version 1. The only error shape any route or stream emits."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    error: ErrorBody


def envelope(error: DomainError, request_id: str | None = None) -> ErrorEnvelope:
    """Build the customer-visible envelope. Retry guidance for 429/503 is part of
    the contract, so a missing `retry_after_s` is a programming error here."""
    code = error.code
    infrx = dict(error.infrx)
    if error.retry_after_s is not None:
        infrx["retry_after_s"] = error.retry_after_s
    elif code in RETRY_AFTER_CODES:
        raise ValueError(f"{code} must carry retry_after_s")
    return ErrorEnvelope(error=ErrorBody(
        message=MESSAGES[code],
        type=error_type(code),
        code=code,
        param=error.param,
        request_id=request_id,
        infrx=infrx or None,
    ))
