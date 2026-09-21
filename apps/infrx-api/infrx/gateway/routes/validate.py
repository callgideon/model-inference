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
budgets **minus a skew margin**, because `admit` measures that ceiling from the
database clock (R7) and a store clock a millisecond behind the gateway would
otherwise refuse every request. The margin is the whole tolerance for that skew:
past it, the store is right to refuse.

Size lives here too, not only in the byte cap. 96 MiB of JSON is 3.4 million empty
messages, and building records from them blocked the event loop for 21.8 s and grew
the process by 2.5 GiB in the first review. A byte bound is not a structure bound.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone

from ...contracts import errors, ids, wire
from ...contracts.codec import canonical_bytes
from ...contracts.limits import MAX_IDEMPOTENCY_KEY_CHARS
from ...contracts.records import (Budgets, ConsentSnapshot, ExecutionMode, IdempotencyRef,
                                  NormalizedRequest, TraceMode)

# Everything a pilot request may name. `messages` and `model` become record fields;
# the rest travel in `NormalizedRequest.parameters`.
SUPPORTED = frozenset({"model", "messages", "stream", "max_tokens", "max_completion_tokens",
                       "temperature", "top_p", "n", "stop", "seed",
                       # r1 R58 follow-up: the engine adapter forwards these, so an
                       # out-of-range value would come back as an engine 400 and be
                       # absorbed as `engine_error`. They are ranged here instead.
                       "presence_penalty", "frequency_penalty"})
# Named so the refusal is deliberate rather than a side effect of the allow-list:
# these are the capabilities callers most often assume, plus R45's price field.
UNSUPPORTED = frozenset({"tools", "tool_choice", "functions", "function_call", "response_format",
                         "logit_bias", "logprobs", "top_logprobs", "parallel_tool_calls",
                         "price_snapshot"})
# r1 R58: the allow-list for a normalized message. A message is *exactly*
# `{role, content}` and a part is *exactly* one of the two shapes below - equality of
# key sets, not "contains", so `tool_calls`, `name`, `mm_processor_kwargs` or an
# `image_url` beside a `text` cannot ride along. Types are compared case-sensitively
# with `==`: `"Text"` is not `"text"`.
ROLES = frozenset({"system", "user", "assistant"})
MESSAGE_KEYS = frozenset({"role", "content"})
TEXT_TYPE = "text"
VIDEO_TYPE = "video_url"
TEXT_PART_KEYS = frozenset({"type", "text"})
VIDEO_PART_KEYS = frozenset({"type", "video_url"})
VIDEO_REF_KEYS = frozenset({"url"})
# http(s) for a source M fetches during preparation, `data:` for the bounded inline
# base64 of DEC-02, `infrx-upload:upl_…` for an owned upload handle M resolves (the
# form the frozen `normalized_request` fixture carries - a bare `upl_…` is not it).
# Anything else - `file:`, `s3:`, a bare path - is a request to read something of ours.
HTTP_SCHEMES = ("http://", "https://")
UPLOAD_SCHEME = "infrx-upload:"
DATA_PREFIX = "data:"
# `data:<video mime>;base64,` only: a `data:` URL naming another type, or no base64
# marker, is not a video the pilot can decode.
DATA_URL = re.compile(r"data:(?P<mime>[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+);base64,")
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
MAX_STOP_SEQUENCES = 4
MAX_STOP_CHARS = 64

# --- structure bounds (review r1 item 3) -------------------------------------------
# A byte cap bounds the body; these bound what is *inside* it, and they are checked
# before anything is built. Provisional, module-local, and named in the integration
# request so the coordinator can promote them to `PilotSettings` names.
MAX_MESSAGES = 64
MAX_PARTS_PER_MESSAGE = 16
MAX_VIDEO_PARTS = 1
MAX_TEXT_CODEPOINTS = 131_072
MAX_URL_CHARS = 8_192
MAX_MODEL_CHARS = 128
MAX_SEED = 2 ** 63 - 1
# JSON that is not an inline media payload is small. `data:` URLs are the one thing a
# legitimate body can be megabytes of, so they are measured out of this bound.
MAX_NON_MEDIA_BYTES = 1_048_576
# Above this, parsing happens off the event loop: `json.loads` never yields.
PARSE_OFFLOAD_BYTES = 1_048_576
# r1 R7: `admit` derives its ceiling from the *database* clock. Without a margin a
# store clock a millisecond behind the gateway refuses every request, and the first
# review's tests could not see it because they pinned the fake's clock to
# `created_at` exactly. Two seconds of tolerance, spent from our own budget.
DEADLINE_SKEW_MARGIN_S = 2.0


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


def storable(text: str, param: str) -> str:
    """Text PostgreSQL `jsonb` and UTF-8 can actually hold.

    A lone surrogate (`"\\ud800"`) is valid JSON and is not encodable UTF-8: it used
    to reach `canonical_bytes` and raise a 500 out of the digest. `NUL` cannot be
    stored in a PostgreSQL text or `jsonb` value at all. Both are refused here, where
    the answer is still a 400, rather than three layers down where it is a 500.
    """
    try:
        text.encode()
    except UnicodeEncodeError:
        raise errors.InvalidRequest("text contains unpaired surrogates", param=param) from None
    if "\x00" in text:
        raise errors.InvalidRequest("text contains a NUL character", param=param)
    return text


def check_stop(stop: object) -> None:
    """A string or up to four strings, each bounded. Counted in code points (R54)."""
    if stop is None:
        return
    items = [stop] if isinstance(stop, str) else stop
    if not isinstance(items, list) or len(items) > MAX_STOP_SEQUENCES:
        raise errors.InvalidRequest(
            f"stop must be a string or up to {MAX_STOP_SEQUENCES} strings", param="stop")
    for item in items:
        if not isinstance(item, str) or not item or len(item) > MAX_STOP_CHARS:
            raise errors.InvalidRequest(
                f"each stop sequence is 1..{MAX_STOP_CHARS} characters", param="stop")
        storable(item, "stop")


def check_video_ref(ref: object) -> tuple[str, int]:
    """r1 R58: a video part carries exactly `{url}`, and the url is a reference we
    are willing to resolve later — never anything that would make the engine fetch.

    Returns the source and how many of its characters are inline media, so the
    caller can measure the *non-media* size of the body (review r1 item 3c).
    """
    if not isinstance(ref, dict) or set(ref) != VIDEO_REF_KEYS:
        raise errors.InvalidRequest("a video part carries exactly {url}", param="messages")
    source = ref["url"]
    if not isinstance(source, str) or not source:
        raise errors.InvalidRequest("a video url must be a non-empty string", param="messages")
    storable(source, "messages")
    if CONTROL_CHARS.search(source):
        # A CR/LF in a URL is a request smuggling primitive the moment anything
        # downstream builds a header or a log line out of it.
        raise errors.UnsupportedMedia("a video url contains control characters",
                                      param="messages")
    if source.startswith(UPLOAD_SCHEME):
        # The frozen `normalized_request` fixture's form. A bare `upl_…` is not a
        # reference: it is an identifier with no namespace.
        handle = source[len(UPLOAD_SCHEME):]
        if not ids.UPLOAD_HANDLE_RE.fullmatch(handle):
            raise errors.UnsupportedMedia("an upload reference is infrx-upload:upl_…",
                                          param="messages")
        return source, 0
    if source.lower().startswith(DATA_PREFIX):
        # Inline base64 is the one legitimately huge string in a body; it is bounded
        # by the intake cap and by `MAX_MEDIA_BYTES` once M decodes it, not by the
        # URL length bound, so it is measured separately.
        if not DATA_URL.match(source):
            raise errors.UnsupportedMedia("an inline video is data:<mime>;base64,",
                                          param="messages")
        return source, len(source)
    if not source.lower().startswith(HTTP_SCHEMES):
        # The reason class, not the value: the value is the caller's URL and goes
        # nowhere near the envelope's fixed message anyway.
        raise errors.UnsupportedMedia("a video source must be http(s), data: or an upload handle",
                                      param="messages")
    if len(source) > MAX_URL_CHARS:
        raise errors.UnsupportedMedia(f"a video url is at most {MAX_URL_CHARS} characters",
                                      param="messages")
    return source, 0


def check_messages(body: dict) -> tuple[tuple[dict, ...], int]:
    """r1 R58: the allow-list for a pilot request's messages, and its bounds.

    An allow-list, never a deny-list: the key set of a message and of a part must
    *equal* the allowed one, so a field nobody thought of — `tool_calls`, `name`,
    `mm_processor_kwargs`, an `image_url` smuggled beside a `text` — is refused rather
    than ignored. Part types are compared with `==` against a literal, so casing and
    unknown types are refused too, and the comparison can never reach an inherited
    attribute of some container.

    Counting happens in the same pass and the caps are checked as they are reached,
    so a body of three million empty messages is refused at message 65 instead of
    after the loop has already run (review r1 item 3).

    Returns the messages and the number of inline-media characters in them.
    """
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise errors.InvalidRequest("messages must be a non-empty array", param="messages")
    if len(messages) > MAX_MESSAGES:
        raise errors.InvalidRequest(f"at most {MAX_MESSAGES} messages", param="messages")
    videos = 0
    text_chars = 0
    media_chars = 0
    for message in messages:
        if not isinstance(message, dict) or set(message) != MESSAGE_KEYS:
            raise errors.InvalidRequest("a message is exactly {role, content}", param="messages")
        role = message["role"]
        # `in` on a set with an unhashable value raises `TypeError`, so the type check
        # comes first: a list role used to be a 500.
        if not isinstance(role, str) or role not in ROLES:
            raise errors.InvalidRequest("a message role is system, user or assistant",
                                        param="messages")
        content = message["content"]
        if isinstance(content, str):
            text_chars += len(storable(content, "messages"))
            if text_chars > MAX_TEXT_CODEPOINTS:
                raise errors.InvalidRequest(f"at most {MAX_TEXT_CODEPOINTS} characters of text",
                                            param="messages")
            continue
        if not isinstance(content, list) or not content:
            raise errors.InvalidRequest("message content must be a string or a non-empty array",
                                        param="messages")
        if len(content) > MAX_PARTS_PER_MESSAGE:
            raise errors.InvalidRequest(f"at most {MAX_PARTS_PER_MESSAGE} parts per message",
                                        param="messages")
        for part in content:
            if not isinstance(part, dict):
                raise errors.UnsupportedMedia("a content part is an object with a type",
                                              param="messages")
            kind = part.get("type")
            if kind == TEXT_TYPE:
                if set(part) != TEXT_PART_KEYS or not isinstance(part["text"], str):
                    raise errors.InvalidRequest("a text part is exactly {type, text}",
                                                param="messages")
                text_chars += len(storable(part["text"], "messages"))
                if text_chars > MAX_TEXT_CODEPOINTS:
                    raise errors.InvalidRequest(
                        f"at most {MAX_TEXT_CODEPOINTS} characters of text", param="messages")
            elif kind == VIDEO_TYPE:
                if set(part) != VIDEO_PART_KEYS:
                    raise errors.InvalidRequest("a video part is exactly {type, video_url}",
                                                param="messages")
                videos += 1
                if videos > MAX_VIDEO_PARTS:
                    # The engine profile budgets one clip per request
                    # (models/marlin2b/README.md), and R58 pairs one media part with
                    # one staged ref.
                    raise errors.UnsupportedMedia("one video per request", param="messages")
                media_chars += check_video_ref(part[VIDEO_TYPE])[1]
            else:
                raise errors.UnsupportedMedia(f"content parts are {TEXT_TYPE} and {VIDEO_TYPE}",
                                              param="messages")
    return tuple(messages), media_chars


def execution_mode(body: dict, headers) -> ExecutionMode:
    """`stream` -> stream, `Prefer: respond-async` -> async. G3 owns the 202 itself;
    the mode is needed here because the queue budget depends on it.

    `Prefer` is parsed into tokens: a substring match makes
    `Prefer: x-no-respond-async` an async request, and `wait=respond-async` too.
    """
    stream = body.get("stream", False)
    if not isinstance(stream, bool):
        raise errors.InvalidRequest("stream must be a boolean", param="stream")
    tokens = {token.strip().lower().split("=")[0]
              for token in (headers.get("prefer") or "").split(",")}
    if wire.PREFER_RESPOND_ASYNC in tokens:
        if stream:
            # One response shape per request: a stream is not a job acknowledgement,
            # and silently preferring one over the other is how a client gets neither.
            raise errors.InvalidRequest("stream and respond-async are mutually exclusive",
                                        param="Prefer")
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

    def __init__(self, rt, *, consent_for=None, served_models=None) -> None:
        self.rt = rt
        self.consent_for = consent_for or off_mode_policy
        # Public model id -> model revision. The public id is what a caller names and
        # the revision is what the store prices, entitles and pins; copying one into
        # the other made every unknown model the platform's problem at admission and
        # let a 1 MiB string through as a revision. `None` means "the served model
        # answers to its own name", which is the F1 behaviour.
        self.served_models = (dict(served_models) if served_models is not None
                              else {rt.settings.model_id: rt.settings.model_id})

    def model_revision(self, body: dict) -> str:
        """The revision for the requested public id, or a refusal (never an echo)."""
        model = body.get("model")
        if model is None:
            default = self.rt.settings.model_id
            return self.served_models.get(default, default)
        if not isinstance(model, str) or not model:
            raise errors.InvalidRequest("model must be a non-empty string", param="model")
        if len(model) > MAX_MODEL_CHARS:
            raise errors.InvalidRequest(f"model is at most {MAX_MODEL_CHARS} characters",
                                        param="model")
        revision = self.served_models.get(model)
        if revision is None:
            # The table's model code: this organization cannot run that model here.
            # Never "not found", which would confirm which model ids exist.
            raise errors.ModelNotEntitled("the requested model is not served", param="model")
        return revision

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

    def normalize(self, body: dict, auth, request_id: str, headers,
                  body_bytes: int = 0) -> NormalizedRequest:
        for name in body:
            # The name is the caller's, so it is never formatted into a message; the
            # envelope echoes it only if it looks like a parameter name (intake).
            if name in UNSUPPORTED or name not in SUPPORTED:
                raise errors.UnsupportedParameter("unsupported parameter", param=name)
            if body[name] is None:
                # `null` is not "absent": accepting it silently makes
                # `{"temperature": null}` mean something different from every other
                # null-typed field in the request.
                raise errors.InvalidRequest("a parameter must not be null", param=name)
        messages, media_chars = check_messages(body)
        if body_bytes - media_chars > MAX_NON_MEDIA_BYTES:
            # Everything but an inline media payload is small. This is what stops
            # megabytes of structure that each individual cap allows.
            raise errors.RequestTooLarge(
                f"non-media JSON exceeds {MAX_NON_MEDIA_BYTES} bytes")
        count = _int(body, "n")
        if count is not None and count != 1:
            raise errors.UnsupportedParameter("only n=1 is supported", param="n")
        # Types *and* ranges, because the engine adapter forwards these: a value the
        # engine refuses would come back as an engine 400 and be absorbed as
        # `engine_error` (R21), i.e. the platform paying for a bad request.
        _number(body, "temperature", 0.0, 2.0)
        _number(body, "top_p", 0.0, 1.0)
        _number(body, "presence_penalty", -2.0, 2.0)
        _number(body, "frequency_penalty", -2.0, 2.0)
        seed = _int(body, "seed")
        if seed is not None and not 0 <= seed <= MAX_SEED:
            raise errors.InvalidRequest(f"seed must be in 0..{MAX_SEED}", param="seed")
        check_stop(body.get("stop"))
        revision = self.model_revision(body)
        mode = execution_mode(body, headers)
        max_input, max_output = self.ceilings(body)
        now = self.now()
        budgets = Budgets.of(self.limits, mode)
        return NormalizedRequest(
            request_id=request_id, org_id=auth.org_id, key_id=auth.key_id,
            model_revision=revision,
            messages=messages,
            parameters={name: body[name] for name in sorted(body)
                        if name in SUPPORTED - {"model", "messages"}},
            # 02 step 1 stages the canonical payload durably before acceptance; the
            # digest is computed here so the ref and the bytes cannot disagree. M1/G2
            # replace the ref with the staged object's.
            payload_ref=f"payloads/{auth.org_id}/{request_id}.json",
            payload_digest="sha256:" + hashlib.sha256(canonical_bytes(body)).hexdigest(),
            # Media is resolved by M during preparation (public URLs) or from an owned
            # upload handle; the ingress validates the reference and stages nothing.
            media=(), execution_mode=mode,
            max_input_tokens=max_input, max_output_tokens=max_output, created_at=now,
            # The skew margin is subtracted, not added: `admit` measures this ceiling
            # from the database clock, and a store a moment behind us must still find
            # the deadline acceptable.
            deadline_at=now + timedelta(seconds=(budgets.preparation_s + budgets.queue_wait_s
                                                 + budgets.generation_s
                                                 - DEADLINE_SKEW_MARGIN_S)),
            trace_policy=self.consent_for(auth.org_id, now))
