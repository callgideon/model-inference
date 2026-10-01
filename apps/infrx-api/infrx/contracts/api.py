"""R270 (wave 7): the wire contracts every NEW control route shares, framework-free.

Audiences: `/auth/v1/*`, `/console/v1/*`, `/operator/v1/*` and the new `/lab/v1/*`
families (workspaces, model projects, artifacts, deployments, judge, reviews). The
existing families keep their envelopes: OpenAI-style errors on `/v1/*`, `{refusal:
reason}` on the pre-wave-7 `/lab/v1/*` routes (migrated by AP-00 behind an adapter).

What is fixed here (research/plan/api-lifecycle/contracts.md §2):
- the error envelope `{error: {code, message, request_id, retryable, field_errors}}`;
- list pages `{data, next_cursor}`, page size default 25 / max 100, cursors opaque;
- the operation document and its state enum (domain state is a separate field);
- feature availability (configured / disabled / unavailable / unknown);
- money crosses the wire as an exact decimal string with a unit, never a float;
- the actor record: always derived from a verified session or a scoped credential,
  never read from a request body.

The HTTP rendering (status codes, no-store headers) lives in `infrx.gateway.control`.
"""
from __future__ import annotations

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from . import errors

DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100


class Wire(BaseModel):
    """Frozen, extra fields refused: a caller cannot smuggle authority in an unknown field."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class FieldError(Wire):
    field: str
    code: str
    message: str


class ErrorBody(Wire):
    code: str
    message: str
    request_id: str
    retryable: bool
    field_errors: tuple[FieldError, ...] = ()
    operation_id: str | None = None
    resource_id: str | None = None


class ErrorEnvelope(Wire):
    error: ErrorBody


T = TypeVar("T")


class ListPage(Wire, Generic[T]):
    data: tuple[T, ...]
    next_cursor: str | None = None


OperationState = Literal["queued", "running", "succeeded", "failed", "cancel_requested", "cancelled"]
TERMINAL_STATES: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})


class OperationDoc(Wire):
    """A long operation: 202 + Location points at one of these. `phase` describes observed
    work; percentage and ETA are deliberately absent (a lane may add nullable ones)."""

    operation_id: str
    kind: str
    state: OperationState
    phase: str | None = None
    resource_id: str | None = None
    created_at: str
    updated_at: str
    retry_after_s: int | None = None
    error: ErrorBody | None = None


AvailabilityState = Literal["configured", "disabled", "unavailable", "unknown"]


class Availability(Wire):
    """A feature's availability as FastAPI states it. A failed fetch is `unknown` with a
    reason; it is never rendered as a zero count or a disabled feature."""

    state: AvailabilityState
    reason: str | None = None
    verified_at: str | None = None


MoneyUnit = Literal["CREDIT", "USD", "PROVIDER_USD"]


class Money(Wire):
    amount: str = Field(pattern=r"^-?\d+(\.\d+)?$")
    unit: MoneyUnit


Audience = Literal["consumer", "operator", "provider_dev", "session"]


class Actor(Wire):
    """Who is acting, as the server established it. `session` = a verified Supabase session
    (the web apps' transport); the key audiences come from `infrx.auth`."""

    audience: Audience
    user_id: str | None = None
    org_id: str | None = None
    provider_org_id: str | None = None
    role: str | None = None
    operator: bool = False


# (exception class, status, code, retryable) - first match wins, so subclasses come first.
STATUSES: tuple[tuple[type[Exception], int, str, bool], ...] = (
    (errors.InvalidApiKey, 401, "unauthenticated", False),
    (errors.OrgSuspended, 403, "suspended", False),
    (errors.Forbidden, 403, "forbidden", False),
    (errors.NotFound, 404, "not_found", False),
    (errors.IdempotencyConflict, 409, "idempotency_conflict", False),
    (errors.Conflict, 409, "conflict", False),
    (errors.Gone, 410, "gone", False),
    (errors.RequestTooLarge, 413, "too_large", False),
    (errors.InvalidRequest, 422, "invalid", False),
    (errors.RateLimitError, 429, "limited", True),
    (errors.DependencyUnavailable, 503, "unavailable", True),
    (errors.DeadlineExceeded, 503, "unavailable", True),
)


def status_of(exc: BaseException) -> tuple[int, str, bool]:
    """(status, code, retryable) for a domain error; anything else is an internal 500."""
    for kind, status, code, retryable in STATUSES:
        if isinstance(exc, kind):
            return status, code, retryable
    return 500, "internal", False


def envelope(exc: BaseException, request_id: str, *, operation_id: str | None = None,
             resource_id: str | None = None) -> ErrorEnvelope:
    """The sanitized body: a domain error's own message, a bug's type name only."""
    _, code, retryable = status_of(exc)
    if isinstance(exc, errors.DomainError):      # its own contract code and fixed safe message;
        code, message = exc.code, exc.message    # `detail` is operator-only and never leaves
    else:
        message = type(exc).__name__
    return ErrorEnvelope(error=ErrorBody(code=code, message=message, request_id=request_id,
                                        retryable=retryable, operation_id=operation_id,
                                        resource_id=resource_id))
