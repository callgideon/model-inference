"""Public identifiers. Handles are random, never derived from the request UUID.

`request_id` is the one internal identity (job, usage, trace, `Inference-Id`).
Anything a customer can put in a URL (`job_`, `upl_`, `fb_`) is an opaque random
handle, so possession of one leaks nothing and every lookup still checks the org.
"""
from __future__ import annotations

import re
import secrets
import uuid

HANDLE_BYTES = 32                       # secrets.token_urlsafe(32) -> 43 chars
JOB_PREFIX = "job_"
UPLOAD_PREFIX = "upl_"
FEEDBACK_PREFIX = "fb_"
CHAT_PREFIX = "chatcmpl-"

# Matched with `fullmatch` everywhere: `$` alone would accept a trailing newline.
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
_HANDLE_BODY = r"[A-Za-z0-9_-]{22,64}"
JOB_HANDLE_RE = re.compile(JOB_PREFIX + _HANDLE_BODY)
UPLOAD_HANDLE_RE = re.compile(UPLOAD_PREFIX + _HANDLE_BODY)
FEEDBACK_ID_RE = re.compile(FEEDBACK_PREFIX + _HANDLE_BODY)


def new_request_id() -> str:
    return str(uuid.uuid4())


def new_event_id() -> str:
    """Stable outbox/index event identity; at-least-once delivery dedupes on it."""
    return str(uuid.uuid4())


def _handle(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(HANDLE_BYTES)


def new_job_handle() -> str:
    return _handle(JOB_PREFIX)


def new_upload_handle() -> str:
    return _handle(UPLOAD_PREFIX)


def new_feedback_id() -> str:
    return _handle(FEEDBACK_PREFIX)


def chat_completion_id(request_id: str) -> str:
    return CHAT_PREFIX + require_request_id(request_id)


def is_request_id(value: object) -> bool:
    return isinstance(value, str) and bool(UUID_RE.fullmatch(value))


def require_request_id(value: str) -> str:
    if not is_request_id(value):
        raise ValueError(f"not a lowercase UUIDv4 request id: {value!r}")
    return value


def require_handle(value: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"not a valid opaque handle for {pattern.pattern!r}: {value!r}")
    return value
