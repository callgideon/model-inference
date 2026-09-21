"""Request body -> `NormalizedRequest`. Strict, closed and explicit.

The pilot serves one shape of request (01: text/video, `n=1`, output ≤ 2,048,
total context ≤ 32,768) and this is where that is decided. Three choices worth
stating, because each one is a rule rather than a taste:

* **The parameter set is closed.** A name that is not in `SUPPORTED` is
  `unsupported_parameter` naming itself, not a silently ignored field. Silently
  ignoring `tools` is how a caller believes it got tool calling; silently ignoring
  `price_snapshot` (r1 R45) is how it believes it named its own rates.
* **The ceilings are derived, not echoed.** `max_output_tokens` is the validated
  request ceiling and `max_input_tokens` is `MAX_CONTEXT_TOKENS` minus it (01: "the
  conservative input ceiling (context limit minus requested maximum output)"), so
  the pair always satisfies the range check `admit` repeats inside its transaction
  (r1 R55: `1 ≤ out ≤ MAX_OUTPUT_TOKENS`, `in ≥ 1`, `in + out ≤ MAX_CONTEXT_TOKENS`)
  and the hold the store derives from it (R53) covers the whole envelope.
* **No money and no price.** Nothing here reads or writes a rate. The price is the
  store's fact, taken in the admitting transaction from its own source (R45).

`deadline_at` is `created_at` plus the mode's preparation + queue + generation
budgets, which is exactly the ceiling `admit` allows (R29). The store computes its
ceiling from a later instant than this one, so a request validated here is never
refused for a deadline that is too far out.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from ...contracts import errors
from ...contracts.codec import canonical_bytes
from ...contracts.limits import MAX_IDEMPOTENCY_KEY_CHARS
from ...contracts.records import (Budgets, ConsentSnapshot, ExecutionMode, IdempotencyRef,
                                  NormalizedRequest, TraceMode)

# Everything a pilot request may name. `messages` and `model` become record fields;
# the rest travel in `NormalizedRequest.parameters`.
SUPPORTED = frozenset({"model", "messages", "stream", "max_tokens", "max_completion_tokens",
                       "temperature", "top_p", "n", "stop", "seed"})
# Named so the refusal is deliberate rather than a side effect of the allow-list:
# these are the capabilities callers most often assume, plus R45's price field.
UNSUPPORTED = frozenset({"tools", "tool_choice", "functions", "function_call", "response_format",
                         "logit_bias", "logprobs", "top_logprobs", "parallel_tool_calls",
                         "price_snapshot"})
ROLES = frozenset({"system", "user", "assistant"})
TEXT_PARTS = frozenset({"text"})
VIDEO_PARTS = frozenset({"video_url", "input_video"})
# http(s) for a source M fetches during preparation, `data:` for the bounded inline
# base64 of DEC-02, `upl_` for an owned upload handle M resolves. Anything else -
# `file:`, `s3:`, a bare path - is a request to read something of ours.
MEDIA_SCHEMES = ("http://", "https://", "data:")
UPLOAD_PREFIX = "upl_"
MAX_STOP_SEQUENCES = 4


def _int(body: dict, name: str) -> int | None:
    value = body.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise errors.InvalidRequest(f"{name} must be an integer", param=name)
    return value


def _number(body: dict, name: str, low: float, high: float) -> float | None:
    value = body.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise errors.InvalidRequest(f"{name} must be a number", param=name)
    value = float(value)
    if not low <= value <= high:
        raise errors.InvalidRequest(f"{name} must be in {low}..{high}", param=name)
    return value


def _video_source(part: dict) -> str:
    """The one string a video part carries, whatever spelling it used."""
    source = part.get("video_url", part.get("input_video"))
    if isinstance(source, dict):
        source = source.get("url")
    if not isinstance(source, str) or not source:
        raise errors.InvalidRequest("a video part must carry a url", param="messages")
    if source.startswith(UPLOAD_PREFIX):
        return source
    if not source.lower().startswith(MEDIA_SCHEMES):
        # The reason class, not the value: the value is the caller's URL and goes
        # nowhere near the envelope's fixed message anyway.
        raise errors.UnsupportedMedia("a video source must be http(s), data: or an upload handle",
                                      param="messages")
    return source


def check_messages(body: dict) -> tuple[dict, ...]:
    """Shape only: canonical messages, at most one video, no foreign part type."""
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise errors.InvalidRequest("messages must be a non-empty array", param="messages")
    videos = 0
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in ROLES:
            raise errors.InvalidRequest(f"each message needs a role in {sorted(ROLES)}",
                                        param="messages")
        content = message.get("content")
        if isinstance(content, str):
            continue
        if not isinstance(content, list) or not content:
            raise errors.InvalidRequest("message content must be a string or a non-empty array",
                                        param="messages")
        for part in content:
            kind = part.get("type") if isinstance(part, dict) else None
            if kind in TEXT_PARTS:
                if not isinstance(part.get("text"), str):
                    raise errors.InvalidRequest("a text part must carry text", param="messages")
            elif kind in VIDEO_PARTS:
                videos += 1
                _video_source(part)
            else:
                raise errors.UnsupportedMedia(
                    f"content parts are {sorted(TEXT_PARTS | VIDEO_PARTS)}", param="messages")
    if videos > 1:
        # The engine profile budgets one clip per request (models/marlin2b/README.md).
        raise errors.UnsupportedMedia("one video per request", param="messages")
    return tuple(messages)


def execution_mode(body: dict, headers) -> ExecutionMode:
    """`stream` -> stream, `Prefer: respond-async` -> async. G3 owns the 202 itself;
    the mode is needed here because the queue budget depends on it."""
    stream = body.get("stream", False)
    if not isinstance(stream, bool):
        raise errors.InvalidRequest("stream must be a boolean", param="stream")
    prefer = (headers.get("prefer") or "").lower()
    if "respond-async" in prefer:
        return ExecutionMode.async_
    return ExecutionMode.stream if stream else ExecutionMode.sync


def idempotency(auth, headers, payload_hash: str, operation: str) -> IdempotencyRef:
    key = headers.get("idempotency-key")
    if key is not None and (not key or len(key) > MAX_IDEMPOTENCY_KEY_CHARS):
        raise errors.InvalidRequest(
            f"Idempotency-Key must be 1..{MAX_IDEMPOTENCY_KEY_CHARS} characters",
            param="Idempotency-Key")
    return IdempotencyRef(org_id=auth.org_id, operation=operation, key=key,
                          payload_hash=payload_hash)


def off_mode_policy(org_id: str, now: datetime) -> ConsentSnapshot:
    """The trace policy for an organization whose consent record has not been read.

    `off` produces no trace row and no content (01 privacy section), which is the
    only safe default: capturing content the owner never opted into is not
    recoverable by deleting it afterwards. T2/C1 supply the real source.
    """
    return ConsentSnapshot(org_id=org_id, consent_version=0, trace_mode=TraceMode.off,
                           content_retention_days=1, evaluation_consent=False, effective_at=now)


class Validator:
    """One per app. Holds the pilot limits; reads nothing else."""

    def __init__(self, rt, *, consent_for=None) -> None:
        self.rt = rt
        self.consent_for = consent_for or off_mode_policy

    @property
    def limits(self):
        return self.rt.settings.pilot

    def now(self) -> datetime:
        """UTC from the app's injected clock - never a second clock of its own."""
        return datetime.fromtimestamp(self.rt.clock(), timezone.utc)

    def ceilings(self, body: dict) -> tuple[int, int]:
        """(max_input_tokens, max_output_tokens), already range-checked."""
        requested = _int(body, "max_tokens")
        alternative = _int(body, "max_completion_tokens")
        if requested is not None and alternative is not None and requested != alternative:
            raise errors.InvalidRequest("max_tokens and max_completion_tokens disagree",
                                        param="max_tokens")
        ceiling = self.limits.max_output_tokens
        output = requested if requested is not None else alternative
        output = ceiling if output is None else output
        if not 1 <= output <= ceiling:
            raise errors.InvalidRequest(f"max_tokens must be in 1..{ceiling}", param="max_tokens")
        # 01: reserve the context limit minus the requested output as the input
        # ceiling. The sum is then exactly MAX_CONTEXT_TOKENS, which R55 allows.
        return self.limits.max_context_tokens - output, output

    def normalize(self, body: dict, auth, request_id: str, headers) -> NormalizedRequest:
        for name in body:
            if name in UNSUPPORTED or name not in SUPPORTED:
                raise errors.UnsupportedParameter(f"{name} is not supported", param=name)
        messages = check_messages(body)
        count = _int(body, "n")
        if count is not None and count != 1:
            raise errors.UnsupportedParameter("only n=1 is supported", param="n")
        _number(body, "temperature", 0.0, 2.0)
        _number(body, "top_p", 0.0, 1.0)
        _int(body, "seed")
        stop = body.get("stop")
        if stop is not None and not (isinstance(stop, str) or (
                isinstance(stop, list) and len(stop) <= MAX_STOP_SEQUENCES
                and all(isinstance(item, str) for item in stop))):
            raise errors.InvalidRequest(
                f"stop must be a string or up to {MAX_STOP_SEQUENCES} strings", param="stop")
        model = body.get("model")
        if model is not None and (not isinstance(model, str) or not model):
            raise errors.InvalidRequest("model must be a non-empty string", param="model")
        mode = execution_mode(body, headers)
        max_input, max_output = self.ceilings(body)
        now = self.now()
        budgets = Budgets.of(self.limits, mode)
        return NormalizedRequest(
            request_id=request_id, org_id=auth.org_id, key_id=auth.key_id,
            model_revision=model or self.rt.settings.model_id,
            messages=messages,
            parameters={name: body[name] for name in sorted(body)
                        if name in SUPPORTED - {"model", "messages", "stream"}},
            # 02 step 1 stages the canonical payload durably before acceptance; the
            # digest is computed here so the ref and the bytes cannot disagree. M1/G2
            # replace the ref with the staged object's.
            payload_ref=f"payloads/{auth.org_id}/{request_id}.json",
            payload_digest="sha256:" + hashlib.sha256(canonical_bytes(body)).hexdigest(),
            # Media is resolved by M during preparation (public URLs) or from an owned
            # upload handle; the ingress validates the reference and stages nothing.
            media=(), execution_mode=mode,
            max_input_tokens=max_input, max_output_tokens=max_output, created_at=now,
            deadline_at=now + timedelta(seconds=(budgets.preparation_s + budgets.queue_wait_s
                                                 + budgets.generation_s)),
            trace_policy=self.consent_for(auth.org_id, now))
