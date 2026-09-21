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

from ...contracts import errors, ids, wire

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
# Refusals that happen while a body may still be arriving. Keeping the socket open
# invites the caller to go on sending an endless chunked body until a proxy gives up.
CLOSE_CODES = frozenset({"invalid_api_key", "request_too_large", "capacity_exhausted",
                         "deadline_exceeded"})
# The minted id is ours, but it reaches a response header, so it is checked like
# anything else that does: a CRLF in a header value splits the response.
REQUEST_ID_RE = re.compile(r"[0-9a-fA-F-]{36}")


def check_content_type(request) -> None:
    """`application/json` (a charset parameter is fine), or a 400 before the read."""
    declared = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if declared != JSON_MEDIA_TYPE:
        raise errors.InvalidRequest("the request body must be application/json",
                                    param="Content-Type")


async def read_body(request, *, max_bytes: int, timeout_s: float, clock, large=None) -> bytes:
    """The request body, or `request_too_large` / `deadline_exceeded`.

    The running total is the only size bound, deliberately: `Content-Length` is a
    hint the caller chooses, a chunked body carries none, and refusing on the total
    never buffers more than one chunk past the limit. A second check against the
    declared length would only be a check the attacker controls. The declared length
    *is* used for one thing - claiming a large-body slot early, before the bytes
    arrive - because being wrong there only costs the caller its own slot.

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
    if large is not None:
        declared = request.headers.get("content-length") or ""
        # `isdigit()` is true for "²" and for a 5,000-digit number; the first raises out
        # of `int()` on some inputs and the second is a pointless big-int conversion.
        # Anything else is simply not used - the running total is the real bound.
        if declared.isascii() and declared.isdigit() and len(declared) <= 19:
            large.account(int(declared))
    try:
        async with asyncio.timeout(timeout_s):
            async for chunk in request.stream():
                total += len(chunk)
                if total > max_bytes:
                    raise errors.RequestTooLarge(f"body exceeded the {max_bytes} byte limit")
                if clock() > deadline:
                    raise errors.DeadlineExceeded(f"the {timeout_s}s intake deadline passed")
                if large is not None:
                    large.account(total)
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


# No value this API accepts needs more digits than this: the largest is `seed`, at
# 2**63-1, which is 19. Nothing here is a limit on what JSON can express; it is a limit
# on what a *request* may contain, and it is what stops the parser doing quadratic work.
MAX_NUMBER_DIGITS = 20
# A float literal carries a point and possibly an exponent, and the longest a real
# double needs is about 24 characters (`1.7976931348623157e+308`), so it gets its own,
# wider allowance: wide enough that no value a client can legitimately send is refused,
# narrow enough that a 700-character number is never converted.
MAX_NUMBER_CHARS = 32


def _bounded_int(literal: str) -> int:
    """`int(str)` is quadratic in CPython up to its own 4,300-digit threshold, and
    `json.loads` calls it once per integer literal. A 94 MiB body of 23,000
    4,300-digit integers passes both structural counts - two openers, 23,000 commas -
    and cost 2.67 s of blocked event loop and +283 MiB. Refusing the literal by its
    length costs nothing: the parser raises before converting anything.
    """
    if len(literal.lstrip("-")) > MAX_NUMBER_DIGITS:
        raise ValueError(f"a number of more than {MAX_NUMBER_DIGITS} digits")
    return int(literal)


def _bounded_float(literal: str) -> float:
    """The same bound, for the same reason. `float(str)` is linear, so this is the
    cheaper half of the problem - 700-character floats measured 0.38 s against the
    integers' 2.67 s - but no accepted parameter is a 700-character number either, so
    there is no reason to convert one.
    """
    if len(literal) > MAX_NUMBER_CHARS:
        raise ValueError(f"a number of more than {MAX_NUMBER_CHARS} characters")
    return float(literal)


OPENERS = ("{", "[")
SEPARATOR = ","
BOM = "﻿"


def decode_utf8(raw: bytes) -> str:
    """The body is UTF-8 (RFC 8259), decoded once, strictly, by us.

    `json.loads` on **bytes** sniffs the encoding and will happily decode UTF-16 and
    UTF-32 - outside the JSON interchange rule, and a way to make every byte-level
    count measure something other than what will be parsed. Decoding here and handing
    `json.loads` a `str` removes that detection entirely, and a BOM, which RFC 8259
    forbids, is refused rather than silently skipped.
    """
    try:
        text = raw.decode()
    except UnicodeDecodeError:
        raise errors.InvalidRequest("the request body must be UTF-8") from None
    if text.startswith(BOM):
        raise errors.InvalidRequest("the request body must not start with a byte order mark")
    return text


def check_structure(text: str, max_openers: int, max_separators: int) -> None:
    """Refuse hostile structure **before** `json.loads` materialises it.

    The count caps in `validate` run on the parsed tree, so a 95 MiB body was parsed in
    full - seconds of stalled event loop, more than a gigabyte resident - and only then
    refused. Two C-speed counts stop that, with no Python loop and no allocation:

    * **openers.** `{` and `[` bound how many collections a body can open.
    * **separators.** Counting openers alone is evadable, because one collection needs
      exactly one opener however many elements it holds: `{"<hex>":1, …}` with nine
      million keys, `[1.5, 1.5, …]` and `["ab", "ab", …]` each have one, and each of
      them cost seconds of parsing and more than a gigabyte. Every element needs a
      comma, so this is the count that bounds the *size* of the tree rather than its
      shape. Base64 contains no comma (a `data:` URL's prefix has exactly one), so an
      inline media payload still costs nothing here.

    Both caps allow one per permitted code point of text, because a brace or a comma
    inside a string is legitimate text.
    """
    if sum(text.count(opener) for opener in OPENERS) > max_openers:
        raise errors.RequestTooLarge(f"the body opens more than {max_openers} collections")
    if text.count(SEPARATOR) > max_separators:
        raise errors.RequestTooLarge(f"the body has more than {max_separators} separators")


def parse_object(text: str) -> dict:
    """JSON parsing, after the bounds. The parser's own message never leaves.

    `RecursionError` is caught with `ValueError` because that is what deep nesting
    raises (`[`×100k), and it is a malformed request, not a server fault.

    Takes the already-decoded `str`, so no encoding detection happens here, and is
    deliberately *not* run in a thread: `json.loads` is one C call holding the GIL, so
    `asyncio.to_thread` moves the stall without shortening it (measured: 1.91 s of
    parsing, 1.83 s of it with the loop blocked). What keeps the loop live is refusing
    hostile bodies before this runs (`check_structure`) and bounding how many large
    ones are in flight at once (`LargeBodies`).
    """
    try:
        body = json.loads(text, parse_constant=_no_constants,
                          parse_int=_bounded_int, parse_float=_bounded_float)
    except (ValueError, RecursionError):
        raise errors.InvalidRequest("the request body is not valid JSON") from None
    if not isinstance(body, dict):
        raise errors.InvalidRequest("the request body must be a JSON object")
    return body


class LargeBodies:
    """At most `limit` bodies over `threshold` bytes in flight per process.

    One parse of a legitimate large body stalls the loop for as long as it takes; this
    is what stops that being multiplied by however many clients ask at once. The deploy
    unit runs one worker and one loop, and eight concurrent 95 MiB bodies measured a
    30 s stall and several GiB resident. Excess is refused, never queued: a queue is
    the same stall with a longer fuse.
    """

    def __init__(self, limit: int = 2, threshold: int = 1_048_576) -> None:
        self.limit = limit
        self.threshold = threshold
        self.in_flight = 0
        self.peak = 0
        self.refused = 0

    def slot(self) -> "LargeBody":
        return LargeBody(self)


class LargeBody:
    """One request's claim on a large-body slot: claimed once, released once."""

    def __init__(self, slots: LargeBodies) -> None:
        self.slots = slots
        self.held = False

    def account(self, total: int) -> None:
        """Claim a slot the first time this body is known to be large."""
        if self.held or total <= self.slots.threshold:
            return
        if self.slots.in_flight >= self.slots.limit:
            self.slots.refused += 1
            raise errors.CapacityExhausted(
                f"{self.slots.limit} large bodies are already in flight", retry_after_s=2)
        self.slots.in_flight += 1
        self.slots.peak = max(self.slots.peak, self.slots.in_flight)
        self.held = True

    def release(self) -> None:
        if self.held:
            self.slots.in_flight -= 1
            self.held = False


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
        if public.code in CLOSE_CODES:
            headers["Connection"] = "close"
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
        # Rebuilt, not reused: `headers` may already carry a Retry-After for a code
        # this envelope no longer reports.
        body = {"error": {**LAST_RESORT["error"], "request_id": request_id}}
        return JSONResponse(body, status_code=500,
                            headers={wire.HEADER_INFERENCE_ID: request_id})


def _retry_hint(error: errors.DomainError) -> dict | None:
    infrx = dict(error.infrx)
    if error.retry_after_s is not None:
        infrx["retry_after_s"] = error.retry_after_s
    elif error.code in errors.RETRY_AFTER_CODES:
        # The contract says these always carry retry guidance; supply it rather than
        # let `envelope()` raise inside the error path.
        infrx["retry_after_s"] = 5
    return infrx or None


def mint(mint_request_id) -> str:
    """A request id that is certainly usable in a header.

    The source is ours, but its answer lands in `Inference-Id`, so an injected source
    that raises, returns a non-string or returns something with a CRLF in it must not
    be able to split a response or leave a bare 500 in place of an envelope.
    """
    try:
        minted = mint_request_id()
        if isinstance(minted, str) and REQUEST_ID_RE.fullmatch(minted):
            return minted
        log.error("the request-id source returned something unusable")
    except Exception:
        log.exception("the request-id source raised")
    return ids.new_request_id()


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
            request_id = mint(mint_request_id)
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
