"""Bounded intake and the one public error body.

Order matters here more than anything else in the ingress: the body is bounded
**and** deadlined before it is parsed, because `await request.json()` on an
unbounded stream is how a single client fills the process's memory, and because a
96 MiB body that arrives one byte a second costs a connection for a day. So this
module reads the stream itself, counting bytes and watching the clock, and only
then hands `json.loads` something it already knows the size of.

Every failure leaves through `response()`: the fixed-message envelope of 08 §3
with the request id, and nothing else. Three rules keep that true under attack:

* **Rendering cannot fail.** `response()` builds the envelope inside a try and
  falls back to a constant one, because the first review found two ways to make
  the error path raise — an unencodable `param` echoed from the caller, and an
  internal-only code whose HTTP status lookup raises on purpose. A guard that can
  raise is a guard that returns a bare `text/plain` 500 with no request id.
* **Nothing the caller wrote is echoed or logged unfiltered.** `param` is echoed
  only when it looks like a parameter name; `detail` is ours and the log line
  carries the code and the request id, never caller text (a newline in a
  parameter name is a forged log line).
* **The parser's own failures are ours to classify.** `json.loads` raises
  `RecursionError` on deep nesting, not `ValueError`, and that used to be a 500.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

from ...contracts import errors, wire

log = logging.getLogger("infrx.gateway")

# Only `application/json`; a charset parameter is fine.
JSON_MEDIA_TYPE = "application/json"
# What may be echoed as `error.param`. Callers choose parameter names, so an
# unfiltered echo reflects up to the whole body back, and a newline in one forges a
# log line. Every name this package uses matches; anything else is dropped.
SAFE_PARAM = re.compile(r"[A-Za-z0-9_.-]{1,64}")
# Pre-built, so producing it cannot fail the way building one from a `DomainError`
# can. This is what a client gets if even the envelope could not be rendered.
LAST_RESORT = {"error": {"message": errors.MESSAGES["internal_error"], "type": "server_error",
                         "code": "internal_error"}}


def check_content_type(request) -> None:
    """`application/json` (a charset parameter is fine), or a 400 before the read."""
    declared = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if declared != JSON_MEDIA_TYPE:
        raise errors.InvalidRequest("the request body must be application/json",
                                    param="Content-Type")


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
    except ClientDisconnect:
        # The caller hung up mid-body. Nothing is wrong with the server, nobody is
        # listening, and a stack trace in the log would say otherwise.
        raise errors.InvalidRequest("the client disconnected before the body arrived") from None
    return b"".join(chunks)


def _no_constants(name: str) -> None:
    """`json` accepts `NaN`, `Infinity` and `-Infinity` by default; JSON does not,
    PostgreSQL `jsonb` cannot store them, and they defeat every range check that
    compares with `<=`."""
    raise ValueError(f"{name} is not valid JSON")


def parse_object(raw: bytes) -> dict:
    """JSON parsing, after the bounds. The parser's own message never leaves.

    `RecursionError` is caught with `ValueError` because that is what deep nesting
    raises (`[`×100k), and it is a malformed request, not a server fault.
    """
    try:
        body = json.loads(raw, parse_constant=_no_constants)
    except (ValueError, RecursionError):
        raise errors.InvalidRequest("the request body is not valid JSON") from None
    if not isinstance(body, dict):
        raise errors.InvalidRequest("the request body must be a JSON object")
    return body


async def parse_body(raw: bytes, *, offload_over_bytes: int) -> dict:
    """Parse, off the event loop when the body is big enough to block it.

    `json.loads` does not yield, so parsing megabytes inline stalls every other
    request in the process - health checks included - for as long as it takes.
    """
    if len(raw) > offload_over_bytes:
        return await asyncio.to_thread(parse_object, raw)
    return parse_object(raw)


def safe_param(param: object) -> str | None:
    return param if isinstance(param, str) and SAFE_PARAM.fullmatch(param) else None


def response(error: errors.DomainError, request_id: str) -> JSONResponse:
    """The customer-visible error: fixed message, stable code, request id.

    Never raises. An internal-only code (`stale_lease`, …) has no HTTP status by
    design, so one escaping a route is a mapping bug: it is logged and answered as
    `internal_error` rather than allowed to raise out of the error path.
    """
    headers = {wire.HEADER_INFERENCE_ID: request_id}
    try:
        public = error if error.code in errors.HTTP_ERRORS else errors.InternalError()
        if public is not error:
            log.error("internal-only error code %s escaped a route on request %s",
                      error.code, request_id)
        body = errors.ErrorEnvelope(error=errors.ErrorBody(
            message=errors.MESSAGES[public.code],
            type=errors.error_type(public.code),
            code=public.code,
            param=safe_param(public.param),
            request_id=request_id,
            infrx=_retry_hint(public)))
        if public.retry_after_s is not None:
            headers[wire.HEADER_RETRY_AFTER] = str(int(public.retry_after_s))
        return JSONResponse(body.model_dump(mode="json", exclude_none=True),
                            status_code=errors.http_status(public.code), headers=headers)
    except Exception:
        log.exception("the error envelope could not be rendered for request %s", request_id)
        return JSONResponse(LAST_RESORT, status_code=500, headers=headers)


def _retry_hint(error: errors.DomainError) -> dict | None:
    infrx = dict(error.infrx)
    if error.retry_after_s is not None:
        infrx["retry_after_s"] = error.retry_after_s
    elif error.code in errors.RETRY_AFTER_CODES:
        # The contract says these always carry retry guidance; supply it rather than
        # let `envelope()` raise inside the error path.
        infrx["retry_after_s"] = 5
    return infrx or None


def guard(mint_request_id):
    """Decorator factory: wrap a handler so nothing but the envelope ever leaves.

    `mint_request_id` is the injected id source, so the ingress mints the id (01:
    "UUID minted at ingress") before anything can fail and every answer - including
    a 413 for a body that was never read - carries one. The handler receives it as
    its second argument. A `DomainError` is translated; anything else is logged and
    answered `internal_error`, because an unexpected exception's text is the string
    most likely to carry a DSN, a token or a file path.

    The log line names the code and the request id only. `error.detail` is ours, but
    a caller-supplied string reaching it once is all a log-forging attack needs.
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
                log.info("%s: %s on request %s", request.url.path, error.code, request_id)
                return response(error, request_id)
            except Exception:
                log.exception("%s: unhandled error on request %s", request.url.path, request_id)
                return response(errors.InternalError(), request_id)

        wrapped.__name__ = handler.__name__
        wrapped.__doc__ = handler.__doc__
        return wrapped

    return decorate
