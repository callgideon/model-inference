"""Bounded intake and the one public error body.

Order matters here more than anything else in the ingress: the body is bounded
**and** deadlined before it is parsed, because `await request.json()` on an
unbounded stream is how a single client fills the process's memory, and because a
96 MiB body that arrives one byte a second costs a connection for a day. So this
module reads the stream itself, counting bytes and watching the clock, and only
then hands `json.loads` something it already knows the size of.

Every failure leaves through `response()`: the fixed-message envelope of 08 §3
with the request id, and nothing else. No upstream exception text, no URL, no
storage key, no setting value. `guard()` is the belt: an unexpected exception
inside a handler becomes `internal_error` with its detail logged, never returned.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from ...contracts import errors, wire

log = logging.getLogger("infrx.gateway")


async def read_body(request, *, max_bytes: int, timeout_s: float, clock) -> bytes:
    """The request body, or `request_too_large` / `deadline_exceeded`.

    The running total is the only size bound, deliberately: `Content-Length` is a
    hint the caller chooses, a chunked body carries none, and refusing on the total
    never buffers more than one chunk past the limit. A second check against the
    declared length would only be a check the attacker controls.

    The deadline is enforced twice because there are two ways to outlast it, and
    neither mechanism catches the other. A body that arrives slowly is caught per
    chunk against the app's injected `clock` - which is also what lets a test move
    time instead of spending it. A peer that opens a request and then sends nothing
    never produces a chunk to check, so the whole read also runs under
    `asyncio.timeout`.
    """
    chunks: list[bytes] = []
    total = 0
    deadline = clock() + timeout_s
    try:
        async with asyncio.timeout(timeout_s):
            async for chunk in request.stream():
                total += len(chunk)
                if total > max_bytes:
                    raise errors.RequestTooLarge(f"body exceeded the {max_bytes} byte limit")
                if clock() > deadline:
                    raise errors.DeadlineExceeded(f"the {timeout_s}s intake deadline passed")
                chunks.append(chunk)
    except TimeoutError:
        raise errors.DeadlineExceeded(f"the {timeout_s}s intake deadline passed") from None
    return b"".join(chunks)


def parse_object(raw: bytes) -> dict:
    """JSON parsing, after the bounds. The parser's own message never leaves."""
    try:
        body = json.loads(raw)
    except ValueError:
        raise errors.InvalidRequest("the request body is not valid JSON") from None
    if not isinstance(body, dict):
        raise errors.InvalidRequest("the request body must be a JSON object")
    return body


def response(error: errors.DomainError, request_id: str) -> JSONResponse:
    """The customer-visible error: fixed message, stable code, request id."""
    envelope = errors.envelope(error, request_id)
    headers = {wire.HEADER_INFERENCE_ID: request_id}
    if error.retry_after_s is not None:
        headers[wire.HEADER_RETRY_AFTER] = str(int(error.retry_after_s))
    return JSONResponse(envelope.model_dump(mode="json", exclude_none=True),
                        status_code=errors.http_status(error.code), headers=headers)


def guard(mint_request_id):
    """Decorator factory: wrap a handler so nothing but the envelope ever leaves.

    `mint_request_id` is the injected id source, so the ingress mints the id (01:
    "UUID minted at ingress") before anything can fail and every answer - including
    a 413 for a body that was never read - carries one. The handler receives it as
    its second argument. A `DomainError` is translated; anything else is logged and
    answered `internal_error`, because an unexpected exception's text is the string
    most likely to carry a DSN, a token or a file path.
    """

    def decorate(handler):
        # Deliberately not `functools.wraps`: it sets `__wrapped__`, FastAPI follows
        # that when it reads the signature, and `request_id` would become a required
        # query parameter on every route.
        async def wrapped(request: Request):
            request_id = mint_request_id()
            try:
                return await handler(request, request_id)
            except errors.DomainError as error:
                if error.detail:
                    log.info("%s %s: %s (%s)", request.method, request.url.path, error.code,
                             error.detail)
                return response(error, request_id)
            except Exception:
                log.exception("%s %s: unhandled error on request %s", request.method,
                              request.url.path, request_id)
                return response(errors.InternalError(), request_id)

        wrapped.__name__ = handler.__name__
        wrapped.__doc__ = handler.__doc__
        return wrapped

    return decorate
