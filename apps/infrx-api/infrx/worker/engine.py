"""W1: the vLLM OpenAI-compatible engine adapter (`ports.Engine`).

One HTTP client, injected; one clock, injected; no GPU and no network in any test
(`httpx.MockTransport` and `fakes.FakeUpstream` script the upstream instead). The
adapter answers the same exported conformance suite as the fake engine
(`infrx.contracts.conformance.run_engine_conformance`), so W2 can be written
against either.

What it is responsible for, and what it deliberately is not:

* **Translation, by allow-list (r1 R58).** `prepared_request(work, prompt_tokens)` turns
  the `Work` a lease holder loaded into the `PreparedRequest` the port takes;
  `VllmEngine.upstream_body` **rebuilds** vLLM's JSON from it - it never forwards a
  customer structure. A message becomes exactly `{role, content}`; a content part
  becomes exactly `{type: "text", text}` or `{type: "video_url", video_url: {url}}` where
  the url is *our* prepared reference. The pilot is text plus video only (`01`), so image,
  audio, file, embeds, untyped, differently-cased and unknown parts are refused, as are
  extra keys anywhere and any other role. A deny-list would have to enumerate every way a
  URL can be smuggled in; an allow-list enumerates the two ways it may legitimately appear.
* **Tenant namespacing.** Every request carries a `cache_salt` derived from the tenant,
  its source digests and the profile version (`01` "Privacy and retention"), so neither
  the prefix cache nor the multimodal cache is shared across tenants. With no tenant
  information at all the salt falls back to the request id: no sharing, rather than
  sharing with everybody. A customer-supplied `tenant_salt` is overwritten.
* **Canonical events (R58).** SSE becomes `EngineEvent`s: one `progress`, then `delta`s
  carrying `visible` (the customer's text, after the reasoning filter, *including the
  filter's final held tail*) and `raw` (the unfiltered model text, for trace capture
  only), then at most one `usage`. A single event's text is bounded, and so is the
  accumulated output.
* **Authoritative usage only, and only when the stream agrees with it (R58).** Usage is
  authoritative when it is the last usage object, arrives after the final content delta,
  and reports at least as many completion tokens as there were nonempty deltas. Anything
  else is *explicitly unknown*. Output chunks are never counted into a billable number -
  `02` forbids output chunks as a token estimator.
* **Honest failure classes.** A `DomainError` means the request was refused before
  anything ran (`unsupported_parameter`, `invalid_request`, `context_length_exceeded`); an
  `EngineFailure` means the external process failed, and each subclass carries the
  `TerminalCause` W2 settles. **Nothing untyped escapes `generate`.** A stall or a
  cancellation is neither: the iterator simply ends, and `EngineStream` says why.
* **Not** journalling, settling, leasing or retrying: those are W2 through the JobStore
  and StreamStore. The lease is only read here, for its phase deadlines and its generation.

The shape rules for one chunk, in one place because they decide whether a customer gets a
truncated answer:

| Received | Treated as |
|---|---|
| a line that is not `data:` | not an event (a blank separator or a `:` keepalive) |
| `data:` that is not JSON, or JSON that is not an object | counted `malformed_lines`, ignored: it carries nothing to act on |
| an object with `error` | `EngineError` (detail coerced to text and bounded) |
| `choices`, a choice, `delta` or `content` present with the wrong type | `EngineProtocolViolation`: these fields carry the answer, and skipping them would deliver a truncated answer as a whole one |
| `content` holding an unpaired surrogate | `EngineProtocolViolation`: the event must be persistable, and we do not rewrite the model's text |
| `usage` present but not a well-formed object of nonnegative integers that add up | usage **unknown**, never authoritative. The one field where a wrong shape is not a violation, because unknown usage already has an honest path (`02` reconciliation) while dropped content does not |
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, AsyncIterator

import httpx

from ..config import Settings
from ..contracts import errors
from ..contracts.limits import DEFAULTS, PilotSettings
from ..contracts.records import (ChunkEventType, EngineEvent, Lease, MediaRef, PreparedRequest,
                                 TerminalCause, Usage, Work)
from ..media.video import Media
from .reasoning import ReasoningFilter

# --- what a caller may ask for ------------------------------------------------
# An allow-list, not a deny-list: an unknown sampling parameter reaching a pinned
# engine is either ignored (the customer paid for something that did not happen) or
# honoured (we are serving a request nobody validated). Both are worse than a 400.
# The *ranges* (0 <= temperature <= 2 and friends) are G1's admission validation; the
# question here is only which knobs exist.
PASSTHROUGH_PARAMETERS = frozenset({
    "temperature", "top_p", "top_k", "min_p", "seed", "stop",
    "presence_penalty", "frequency_penalty", "repetition_penalty",
})
# Refused with a name the customer can act on. `n` is refused only above 1.
REFUSED_PARAMETERS = frozenset({
    "tools", "tool_choice", "functions", "function_call",          # 01: tools are unsupported
    "response_format", "guided_json", "guided_regex", "guided_choice",
    "guided_grammar", "structured_outputs",                        # 01: structured output too
    "logprobs", "top_logprobs", "echo", "best_of", "logit_bias",
    "price_snapshot",                                              # r1 R45: prices are never a request field
    "max_tokens", "max_completion_tokens",                         # the ceiling is the record's, not a parameter
    "stream", "stream_options", "model",                           # the transport and the engine are ours
})
# Consumed here and never forwarded: `prepared_request` puts the tenant in it.
INTERNAL_PARAMETERS = frozenset({"tenant_salt"})

# r1 R58: the pilot request is text plus video, and nothing else exists.
ALLOWED_ROLES = ("system", "user", "assistant")
TEXT_PART = "text"
VIDEO_PART = "video_url"
VIDEO_MIME_PREFIX = "video/"

# vLLM's field for the multimodal/prefix cache namespace. W3's capability probe
# confirms it against the pinned image; if the pinned engine names it differently,
# this constant is the only line that changes.
CACHE_SALT_FIELD = "cache_salt"
MM_UUIDS_FIELD = "mm_uuids"

DETAIL_MAX_CHARS = 500          # operator-only text, bounded so a log line stays a log line
ERROR_BODY_MAX_BYTES = 64 * 1024        # an engine's error body is read bounded, never whole
# r1 R58: one event must fit the journal (`JOURNAL_EVENT_MAX_BYTES`, 1 MiB). A delta
# carries its text up to three times (`visible`, `raw`, the transitional `content`), each
# character costing up to 4 bytes and JSON escaping up to 6, so an eighth of the journal
# ceiling *in characters* is a safe bound. A long delta is **split** across events rather
# than refused: the reasoning filter is boundary-independent, so splitting loses nothing,
# whereas refusing would throw away an answer the engine did produce.
EVENT_TEXT_DIVISOR = 8
# The accumulated output bound, in code points (a lower bound on bytes). 64 per output
# token is ~16x real text (`est.`, provisional like every limit in 08 §5); it exists so a
# runaway engine cannot grow `raw_text` without limit, not to police normal answers.
MAX_CODE_POINTS_PER_OUTPUT_TOKEN = 64
# Cancellation intents for leases that are never executed: bounded, because a cancel for
# work that never runs must not be a memory leak. The oldest is dropped first.
MAX_CANCEL_INTENTS = 1024
FINISHED_REASONS = ("stop", "length")


# --- failures of an external process -----------------------------------------
class EngineFailure(RuntimeError):
    """The engine is a separate process, so its failures are **not** `DomainError`s
    (the shared fake's `EngineExited` follows the same rule). `terminal_cause` is the
    `TerminalCause` W2 settles, so the distinction the adapter draws is machine
    readable rather than a sentence in a docstring.

    Also the class an unexpected exception is wrapped in at the `generate` boundary: an
    `OSError` from a socket, or a `TypeError` from our own parsing, must not reach W2
    untyped, because W2 has to settle *something* and an unknown exception is not a cause.
    """

    terminal_cause = TerminalCause.engine_error

    def __init__(self, detail: object = "", **facts: Any) -> None:
        # `str()` on purpose: `{"error": {"message": null}}` is a plausible payload, and
        # `None[:500]` is a `TypeError` escaping the port.
        self.detail = str(detail)[:DETAIL_MAX_CHARS]
        self.facts = facts
        super().__init__(f"{type(self).__name__}: {self.detail}" if self.detail else type(self).__name__)


class EngineTransportError(EngineFailure):
    """The engine could not be reached, or the connection died mid-stream (an abrupt
    exit looks exactly like this from here). A timeout *before* the response headers is
    also this: nothing was produced, so there is no stall to report."""


class EngineError(EngineFailure):
    """The engine answered with an error: a non-200 before the stream, or an error
    object inside it. `facts["stage"]` distinguishes the two, because a post-header
    error may already have published output that must never be regenerated."""


class EngineIncomplete(EngineFailure):
    """The stream ended without a completion marker and without a stall or a
    cancellation to explain it: the output is not the whole answer."""

    terminal_cause = TerminalCause.engine_incomplete


class EngineProtocolViolation(EngineFailure):
    """The engine broke the protocol: it exceeded the envelope we reserved, or sent a
    chunk whose answer-carrying fields we cannot read. `01`: a protocol violation beyond
    the envelope is a platform failure requiring reconciliation, never an unreserved
    customer debit."""

    terminal_cause = TerminalCause.platform_error


class EngineUnsupported(EngineFailure):
    """The pinned engine does not serve the model, or not at the pinned version."""


# --- translation --------------------------------------------------------------
def prepared_request(work: Work, prompt_tokens: int, *, profile_version: str | None = None) -> PreparedRequest:
    """The `PreparedRequest` for the `Work` a lease holder loaded (r1 R46).

    `prompt_tokens` is preparation's exact count; it is an argument because `Work` does
    not carry one, and guessing it here would be the estimator `02` forbids.

    Every ref must belong to the request's organization (R10 tenant coherence: the two
    tenant-bearing arguments are the request and its media), and `tenant_salt` is
    **overwritten**, never defaulted - a customer-supplied salt would let one tenant name
    another tenant's cache namespace.
    """
    request = work.request
    refs = work.prepared_refs or work.media_refs
    for ref in refs:
        if ref.org_id != request.org_id:
            raise errors.NotFound("prepared media must belong to the request's organization")
    parameters = dict(request.parameters or {})
    parameters["tenant_salt"] = request.org_id
    return PreparedRequest(
        request_id=request.request_id, model_revision=request.model_revision,
        messages=request.messages, parameters=parameters, media=refs,
        max_output_tokens=request.max_output_tokens, prompt_tokens=prompt_tokens,
        profile_version=profile_version or (refs[0].profile_version if refs else "v1"))


def cache_salt(prepared: PreparedRequest) -> str:
    """The per-tenant cache namespace (`01` "Privacy and retention").

    Tenant source digest **plus** profile version, so the same customer's second
    request hits the prefix and multimodal caches and another customer's never can.
    With neither a `tenant_salt` nor a media reference to identify the tenant the
    request id is used: caching nothing is a cost, sharing a cache across an unknown
    boundary is a leak.
    """
    parameters = prepared.parameters or {}
    tenant = str(parameters.get("tenant_salt") or "").strip()
    if not tenant:
        orgs = sorted({ref.org_id for ref in prepared.media})
        tenant = "|".join(orgs) if orgs else prepared.request_id
    parts = [tenant, prepared.profile_version, prepared.model_revision,
             *sorted(f"{ref.digest}@{ref.profile_version}" for ref in prepared.media)]
    return "salt_" + hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:32]


def media_uuid(ref: MediaRef, salt: str) -> str:
    """A deterministic identifier for one prepared media object, namespaced by the same
    salt as the prefix cache, and distinct per object: two sources in one tenant are two
    cache entries, not one."""
    return hashlib.sha256(f"{salt}\x1f{ref.digest}".encode()).hexdigest()[:32]


def _encodable(text: str) -> bool:
    """False for an unpaired surrogate: such a string cannot be serialised, so an event
    carrying it could not be journalled or relayed (it would break W2's `append`)."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _pieces(text: str, size: int) -> list[str]:
    return [text[at:at + size] for at in range(0, len(text), size)] or [text]


def _delta_payload(raw: str, visible: str) -> dict[str, str]:
    """r1 R58: `visible` is the customer's text, `raw` is for trace capture only.

    `content` is a **transitional alias of `raw`**: the exported engine conformance cases
    and the shared `FakeEngine` still read `payload["content"]` and assert the unfiltered
    text there, and both live in the coordinator-owned contracts package. It goes in the
    same change that updates them (integration request in the W1 evidence). No relay may
    use it - a relay that does leaks the reasoning block.
    """
    return {"visible": visible, "raw": raw, "content": raw}


def _parse_usage(raw: object) -> Usage | None:
    """vLLM's usage object, or `None` when it is not one.

    Strict on purpose: a non-object, a string, a float, a boolean, a negative or a
    `total_tokens` that does not add up all mean the engine did not tell us what it did,
    and a guessed number here becomes a customer's debit.
    """
    if not isinstance(raw, dict):
        return None
    prompt, completion = raw.get("prompt_tokens"), raw.get("completion_tokens")
    for value in (prompt, completion):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
    total = raw.get("total_tokens")
    if total is not None and (isinstance(total, bool) or not isinstance(total, int)
                             or total != prompt + completion):
        return None
    return Usage.of(prompt, completion)


class VllmEngine:
    """`ports.Engine` over vLLM's OpenAI-compatible server.

    `client` is injected (`httpx.AsyncClient`, base URL pointing at the engine), and
    so is `clock` - the adapter never reads the wall clock, so the stall timers are
    driven by the same injected clock a conformance case advances.
    """

    def __init__(self, client: httpx.AsyncClient, *, served_model: str, clock,
                 limits: PilotSettings = DEFAULTS, path: str = "/v1/chat/completions",
                 media_settings: Settings | None = None, require_version: str | None = None) -> None:
        self.client = client
        self.served_model = served_model
        self.clock = clock
        self.limits = limits
        self.path = path
        self.require_version = require_version
        self.drained = False
        # r1 R58: keyed by `(job_id, generation)`. Keyed by job alone, a stale intent for a
        # fenced generation 1 would cancel the live generation 2 of the same job. A bounded
        # insertion-ordered dict, so intents for work that never runs cannot grow without
        # limit; the oldest is dropped first.
        self.cancelled: dict[tuple[str, int], bool] = {}
        # r1: F1's video budget, called rather than reimplemented. `Media` reads only
        # `rt.settings`, so the whole runtime it needs is the settings object.
        self._media = Media(SimpleNamespace(settings=media_settings or Settings()))

    # --- request translation --------------------------------------------------
    def check_parameters(self, parameters: dict[str, Any] | None) -> dict[str, Any]:
        """The supported sampling parameters, or `unsupported_parameter` naming the
        one that is not (`01`: unsupported tools/structured-output options are
        refused explicitly, and r1 R45 refuses a client-supplied price)."""
        forwarded: dict[str, Any] = {}
        for name, value in (parameters or {}).items():
            if name in INTERNAL_PARAMETERS:
                continue
            if name == "n":
                if value != 1:
                    raise errors.UnsupportedParameter(f"n={value!r} is not supported", param="n")
                continue
            if name in REFUSED_PARAMETERS or name not in PASSTHROUGH_PARAMETERS:
                raise errors.UnsupportedParameter(f"{name} is not supported", param=name)
            forwarded[name] = value
        return forwarded

    def output_ceiling(self, prepared: PreparedRequest) -> int:
        """The engine-side output ceiling, validated after preparation.

        Enforced here as well as sent: `max_tokens` is what we ask for, and
        `EngineProtocolViolation` is what happens if the engine exceeds it anyway. Both
        bounds are inclusive - an answer of exactly the ceiling with
        `finish_reason=length` is the normal way a long generation ends, and a prompt that
        exactly fills the context minus the ceiling is a legal request.
        """
        ceiling = prepared.max_output_tokens
        if not 1 <= ceiling <= self.limits.max_output_tokens:
            raise errors.InvalidRequest(
                f"max output tokens must be in 1..{self.limits.max_output_tokens}",
                param="max_tokens")
        total = prepared.prompt_tokens + ceiling
        if total > self.limits.max_context_tokens:
            raise errors.ContextLengthExceeded(
                f"{total} tokens exceeds the {self.limits.max_context_tokens} token context")
        return ceiling

    def messages_for(self, prepared: PreparedRequest) -> list[dict]:
        """r1 R58: the messages **rebuilt** from an allow-list, media parts replaced by
        their prepared refs, in order, with the part kind matching the ref's MIME class.

        Nothing of the customer's structure survives except the text it is allowed to
        carry, so there is no shape - an untyped `{"image_url": …}`, a `VIDEO_URL`, an
        extra key beside `type`, a message-level `tool_calls`, a part that is not an
        object - in which a customer URL can reach the engine and be fetched from inside
        our network.
        """
        refs = list(prepared.media)
        messages, consumed = [], 0
        for message in prepared.messages:
            if not isinstance(message, dict):
                raise errors.InvalidRequest("a message is an object", param="messages")
            extra = sorted(set(message) - {"role", "content"})
            if extra:
                raise errors.InvalidRequest(f"a message carries role and content only, not {extra}",
                                            param="messages")
            role = message.get("role")
            if role not in ALLOWED_ROLES:
                raise errors.InvalidRequest(f"role must be one of {', '.join(ALLOWED_ROLES)}",
                                            param="messages")
            content = message.get("content")
            if isinstance(content, str):
                messages.append({"role": role, "content": content})
                continue
            if not isinstance(content, list):
                raise errors.InvalidRequest("content is text or a list of parts", param="messages")
            parts = []
            for part in content:
                rebuilt, consumed = self._part(part, refs, consumed)
                parts.append(rebuilt)
            messages.append({"role": role, "content": parts})
        if consumed != len(refs):
            raise errors.InvalidRequest(
                f"{consumed} media parts but {len(refs)} prepared references", param="messages")
        return messages

    def _part(self, part: object, refs: list[MediaRef], consumed: int) -> tuple[dict, int]:
        """One content part, rebuilt; returns it and how many refs are now consumed."""
        if not isinstance(part, dict):
            raise errors.InvalidRequest("a content part is an object", param="messages")
        kind = part.get("type")
        if kind == TEXT_PART:
            if set(part) != {"type", "text"} or not isinstance(part["text"], str):
                raise errors.InvalidRequest("a text part is exactly {type, text}", param="messages")
            return {"type": TEXT_PART, "text": part["text"]}, consumed
        if kind == VIDEO_PART:
            if set(part) != {"type", "video_url"}:
                raise errors.InvalidRequest("a video part is exactly {type, video_url}",
                                            param="messages")
            if consumed >= len(refs):
                raise errors.InvalidRequest("more media parts than prepared references",
                                            param="messages")
            ref = refs[consumed]
            if not ref.mime.startswith(VIDEO_MIME_PREFIX):
                raise errors.InvalidRequest(f"a video part cannot carry {ref.mime}",
                                           param="messages")
            # The customer's own `video_url` value is never read: the reference is ours.
            return {"type": VIDEO_PART, "video_url": {"url": ref.storage_ref}}, consumed + 1
        raise errors.UnsupportedParameter(
            f"{kind!r} content parts are not supported; the pilot is text and video",
            param="messages")

    def upstream_body(self, prepared: PreparedRequest) -> dict[str, Any]:
        """vLLM's request body. Raises a `DomainError` before anything is sent."""
        forwarded = self.check_parameters(prepared.parameters)
        ceiling = self.output_ceiling(prepared)
        if len({ref.org_id for ref in prepared.media}) > 1:
            # R10: one request, one tenant. Two tenants' objects in one prompt would share
            # a cache namespace and a token budget.
            raise errors.NotFound("prepared media must belong to one organization")
        videos = [ref for ref in prepared.media if ref.mime.startswith(VIDEO_MIME_PREFIX)]
        if len(videos) != len(prepared.media):
            raise errors.UnsupportedParameter("the pilot accepts video media only",
                                              param="messages")
        if len(videos) > 1:
            raise errors.UnsupportedParameter("one video per request", param="messages")
        messages = self.messages_for(prepared)
        salt = cache_salt(prepared)
        body: dict[str, Any] = {
            "model": self.served_model,          # the engine's served name, not the revision
            "messages": messages,
            "max_tokens": ceiling,
            "n": 1,
            "stream": True,
            "stream_options": {"include_usage": True},
            CACHE_SALT_FIELD: salt,
            **forwarded,
        }
        if videos:
            body["mm_processor_kwargs"] = self._media.budget_kwargs(videos[0].duration_s or 0.0)
            body[MM_UUIDS_FIELD] = [media_uuid(ref, salt) for ref in videos]
        return body

    # --- the port -------------------------------------------------------------
    def generate(self, lease: Lease, prepared: PreparedRequest) -> "EngineStream":
        """An async iterator of canonical events; not `async def`, per the port.

        The object it returns is also the report on how the stream ended - the raw text
        for trace capture, the authoritative usage or its absence, the stall or the
        cancellation - because a stall must not raise (the engine simply stops; W owns
        the policy) and W2 still has to know.
        """
        return EngineStream(self, lease, prepared)

    async def cancel(self, lease: Lease) -> bool:
        """Record the intent for **this generation** and stop the stream at the next
        event, which closes the upstream connection and so cancels the generation in vLLM.

        r1 R58: keyed by `(job_id, generation)`, so an intent recorded for an attempt that
        was then fenced cannot stop the attempt that replaced it.

        ponytail: a stream already blocked on `aiter_lines` notices at its next event. A
        wholly silent engine is bounded by the stall timers and the client read timeout;
        cancelling *instantly* means cancelling the asyncio task, which is W2's job because
        W2 owns the task.
        """
        self.cancelled[(lease.job_id, lease.generation)] = True
        while len(self.cancelled) > MAX_CANCEL_INTENTS:
            self.cancelled.pop(next(iter(self.cancelled)))
        return True

    async def health(self) -> dict[str, Any]:
        """Protected readiness: drained is not ready, and neither is an engine that
        does not answer."""
        if self.drained:
            return {"ready": False, "drained": True, "served_model": self.served_model}
        try:
            response = await self.client.get("/health")
            ready = response.status_code == 200
        except httpx.HTTPError as failure:
            return {"ready": False, "drained": False, "served_model": self.served_model,
                    "detail": type(failure).__name__}
        return {"ready": ready, "drained": False, "served_model": self.served_model,
                "max_num_seqs": self.limits.engine_max_num_seqs}

    async def drain(self) -> None:
        """Stop reporting ready. Waiting for in-flight work is W3's drain."""
        self.drained = True

    async def capabilities(self) -> dict[str, Any]:
        """The version/capability check hook (W3 pins the answers).

        Refuses an engine that does not serve the configured model, or serves it at a
        version other than the pinned one.
        """
        try:
            version = (await self.client.get("/version")).json().get("version")
            listed = (await self.client.get("/v1/models")).json().get("data", [])
        except (httpx.HTTPError, ValueError, AttributeError) as failure:
            raise EngineTransportError(f"capability probe failed: {type(failure).__name__}") from None
        models = [entry.get("id") for entry in listed if isinstance(entry, dict)]
        if self.served_model not in models:
            raise EngineUnsupported(f"{self.served_model} is not served", models=models)
        if self.require_version is not None and version != self.require_version:
            raise EngineUnsupported("engine version is not the pinned one",
                                    version=version, pinned=self.require_version)
        return {"version": version, "models": models, "served_model": self.served_model}

    # --- generation -----------------------------------------------------------
    def _timeout(self) -> httpx.Timeout:
        """A coarse backstop for an engine that says nothing at all; the per-phase
        bound is the clock check in `_generate`, which a conformance case can drive."""
        read = max(self.limits.ttft_timeout_s, self.limits.tpot_stall_s)
        return httpx.Timeout(read, connect=self.limits.media_fetch_connect_timeout_s,
                             write=read, pool=read)

    def event_text_limit(self) -> int:
        """How much text one event may carry, so a chunk fits `JOURNAL_EVENT_MAX_BYTES`."""
        return max(1, self.limits.journal_event_max_bytes // EVENT_TEXT_DIVISOR)

    def _overdue(self, stream: "EngineStream", now: datetime) -> str | None:
        """Which bound, if any, this stream has passed.

        The generation bound is checked first because it is a *different outcome* - the
        job's absolute deadline (`deadline_exceeded`), not a slow engine
        (`engine_incomplete`). The first-token instant is the store's (r1 R20: the worker
        compares against the persisted instant, it does not time a budget locally); the
        inter-event bound is a per-event budget, so it comes from the configuration.
        """
        lease = stream.lease
        if now >= lease.generation_deadline_at:
            return "generation"
        if stream.deltas == 0 and lease.first_token_deadline_at is not None \
                and now >= lease.first_token_deadline_at:
            return "first_token"
        if stream.last_event_at is not None and stream.deltas > 0 \
                and now >= stream.last_event_at + timedelta(seconds=self.limits.tpot_stall_s):
            return "inter_event"
        return None

    async def _read_bounded(self, response: httpx.Response) -> str:
        """An engine's error body, read bounded: it is a stack trace of unknown size and
        only `DETAIL_MAX_CHARS` of it is ever kept."""
        body = b""
        async for part in response.aiter_bytes():
            body += part
            if len(body) >= ERROR_BODY_MAX_BYTES:
                break
        return body[:ERROR_BODY_MAX_BYTES].decode("utf-8", "replace")

    async def _run(self, stream: "EngineStream") -> AsyncIterator[EngineEvent]:
        """The generate boundary: a refusal, an `EngineFailure`, or nothing at all."""
        inner = self._generate(stream)
        try:
            async for event in inner:
                yield event
        except (errors.DomainError, EngineFailure):
            raise
        except Exception as failure:
            # r1 R58: nothing untyped leaves the port. W2 has to settle a cause, and an
            # unknown exception is not one.
            raise EngineFailure(f"{type(failure).__name__}: {failure}", stage="adapter") from None
        finally:
            # Deterministic cleanup: a consumer that stops iterating (or closes this
            # generator) must close the upstream stream *now*, not whenever the event
            # loop finalises an abandoned async generator.
            await inner.aclose()

    async def _generate(self, stream: "EngineStream") -> AsyncIterator[EngineEvent]:
        lease, prepared = stream.lease, stream.prepared
        key = (lease.job_id, lease.generation)
        body = self.upstream_body(prepared)              # DomainError before anything runs
        ceiling = body["max_tokens"]
        if key in self.cancelled:
            # Cancelled before the request was sent: nothing ran, so zero tokens is the
            # authoritative answer rather than an estimate.
            self.cancelled.pop(key, None)
            stream.cancelled = True
            stream.usage = Usage.of(0, 0)
            yield stream.usage_event(stream.usage, None)
            return
        reasoning = ReasoningFilter()
        try:
            async with self.client.stream("POST", self.path, json=body,
                                          timeout=self._timeout()) as response:
                if response.status_code != 200:
                    raise EngineError(await self._read_bounded(response),
                                      stage="pre_headers", status=response.status_code)
                stream.started = True
                stream.last_event_at = self.clock.now()
                yield EngineEvent(type=ChunkEventType.progress, payload={"phase": "running"})
                async for line in response.aiter_lines():
                    now = self.clock.now()
                    stall = self._overdue(stream, now)
                    if stall is not None:
                        stream.stall = stall
                        break                            # leaving the block closes the stream
                    if key in self.cancelled:
                        stream.cancelled = True
                        break
                    for event in self._events(stream, line, reasoning, ceiling, now):
                        yield event
                    if stream.complete:
                        break
        except httpx.TimeoutException as failure:
            # Post-header silence is a stall, not a failed engine: the read timeout is the
            # same bound the clock check enforces, reached by a different route. Before the
            # headers nothing was produced at all, so it is a transport failure.
            if not stream.started:
                raise EngineTransportError(f"{type(failure).__name__}: {failure}",
                                           stage="pre_headers") from None
            stream.stall = self._overdue(stream, self.clock.now()) or "inter_event"
        except httpx.HTTPError as failure:
            raise EngineTransportError(f"{type(failure).__name__}: {failure}",
                                       stage="stream" if stream.started else "pre_headers") from None
        finally:
            self.cancelled.pop(key, None)
            # r1 R58: the filter's final held tail is part of the customer's text. It is
            # taken here so an abandoned generator cannot lose it from `visible_text`, and
            # emitted below as its own delta so a *streaming* consumer sees it too.
            stream.held_tail = reasoning.close()
            stream.visible_text += stream.held_tail
        # Only the paths that did not raise reach here.
        if stream.held_tail:
            stream.events += 1
            yield EngineEvent(type=ChunkEventType.delta,
                              payload=_delta_payload("", stream.held_tail))
        if stream.stall is None and not stream.complete and not stream.cancelled:
            stream.stall = self._overdue(stream, self.clock.now())
        for event in self._final_usage(stream, ceiling):
            yield event
        if not stream.complete and stream.stall is None and stream.finish_reason is None \
                and not stream.cancelled:
            raise EngineIncomplete("the stream ended without a completion marker",
                                   deltas=stream.deltas)

    def _events(self, stream: "EngineStream", line: str, reasoning: ReasoningFilter,
                ceiling: int, now: datetime) -> list[EngineEvent]:
        """One SSE line into canonical events, by the table in the module docstring."""
        if not line.startswith("data:"):
            return []                                    # blank separator, or a `:` keepalive
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            stream.complete = True
            return []
        try:
            obj = json.loads(payload)
        except ValueError:
            stream.malformed_lines += 1
            return []
        if not isinstance(obj, dict):
            stream.malformed_lines += 1
            return []
        if obj.get("error") is not None:
            error = obj["error"]
            raise EngineError(error.get("message") if isinstance(error, dict) else error,
                              stage="stream")
        stream.last_event_at = now
        events: list[EngineEvent] = []
        choices = obj.get("choices")
        if choices is not None:
            if not isinstance(choices, list):
                raise EngineProtocolViolation("choices is not a list", got=type(choices).__name__)
            for choice in choices:
                events.extend(self._choice(stream, choice, reasoning, ceiling))
        if obj.get("usage") is not None:
            self._note_usage(stream, obj["usage"])
        return events

    def _choice(self, stream: "EngineStream", choice: object, reasoning: ReasoningFilter,
                ceiling: int) -> list[EngineEvent]:
        if not isinstance(choice, dict):
            raise EngineProtocolViolation("a choice is not an object", got=type(choice).__name__)
        if choice.get("finish_reason"):
            stream.finish_reason = str(choice["finish_reason"])
        delta = choice.get("delta")
        if delta is None:
            return []
        if not isinstance(delta, dict):
            raise EngineProtocolViolation("a delta is not an object", got=type(delta).__name__)
        content = delta.get("content")
        if content is None or content == "":
            # vLLM's first chunk is `{"role": "assistant", "content": ""}`: a real event
            # (it is liveness) but not a token, so it is neither a delta nor counted.
            #
            # Only `content` is read, which is why the pinned engine must **not** run with
            # `--reasoning-parser`: that moves the reasoning into `reasoning_content`, which
            # this adapter would drop - and `raw` is what trace capture keeps, so the trace
            # would silently lose it. `reasoning.py` separates the block instead, and W3
            # owns the engine flags.
            return []
        if not isinstance(content, str):
            raise EngineProtocolViolation("delta content is not text", got=type(content).__name__)
        if not _encodable(content):
            raise EngineProtocolViolation("delta content is not serialisable (unpaired surrogate)")
        stream.deltas += 1
        if stream.deltas > ceiling:
            # Each nonempty delta carries at least one token, so more deltas than the
            # ceiling means more tokens than we reserved, whatever the usage says.
            raise EngineProtocolViolation(f"more than {ceiling} output tokens",
                                          deltas=stream.deltas)
        budget = ceiling * MAX_CODE_POINTS_PER_OUTPUT_TOKEN
        if len(stream.raw_text) + len(content) > budget:
            raise EngineProtocolViolation(f"output exceeds {budget} code points",
                                          produced=len(stream.raw_text))
        events = []
        for piece in _pieces(content, self.event_text_limit()):
            stream.raw_text += piece
            visible = reasoning.feed(piece)
            stream.visible_text += visible
            stream.events += 1
            events.append(EngineEvent(type=ChunkEventType.delta,
                                      payload=_delta_payload(piece, visible)))
        return events

    def _note_usage(self, stream: "EngineStream", raw: object) -> None:
        """Remember a usage object; the decision is made once the stream has ended."""
        stream.usage_objects += 1
        usage = _parse_usage(raw)
        if usage is None:
            stream.malformed_usage = True
            return
        stream.usage_candidates.add((usage.prompt_tokens, usage.completion_tokens))
        stream.usage_candidate = usage
        stream.usage_at_deltas = stream.deltas

    def _final_usage(self, stream: "EngineStream", ceiling: int) -> list[EngineEvent]:
        """r1 R58: at most one usage event, decided against the whole stream.

        Authoritative only when it is the last usage object, arrived after the final
        content delta, and reports at least as many completion tokens as there were
        nonempty deltas. Anything else is **unknown**, not a violation: an under-reported
        count settled as authoritative would under-bill *and* close the job, whereas
        unknown usage has a reconciliation path (`02`). The one thing that does raise is
        usage *beyond* the envelope, because that is a reservation breach (`01`).
        """
        if stream.usage_objects == 0:
            if stream.cancelled:
                # Truncated mid-stream: the engine sent no usage and the deltas are not a
                # token count, so the usage is unknown and D reconciles it.
                return [stream.usage_event(None, "cancelled")]
            return []
        reason = None
        if stream.malformed_usage or stream.usage_candidate is None:
            # The same fact twice: a usage object we could not read leaves no candidate.
            reason = "malformed"
        elif len(stream.usage_candidates) > 1:
            reason = "conflicting"
        elif stream.deltas > stream.usage_at_deltas:
            reason = "delta_after_usage"
        elif stream.usage_candidate.completion_tokens < stream.deltas:
            reason = "below_delta_count"
        if reason is not None:
            return [stream.usage_event(None, reason)]
        if stream.usage_candidate.completion_tokens > ceiling:
            raise EngineProtocolViolation(
                f"usage reports {stream.usage_candidate.completion_tokens} > {ceiling}",
                completion_tokens=stream.usage_candidate.completion_tokens)
        stream.usage = stream.usage_candidate
        return [stream.usage_event(stream.usage, None)]


class EngineStream:
    """The async iterator `generate` returns, and the report on how it ended.

    `raw_text` is the model's text exactly as it arrived, which is what canonical trace
    capture records; `visible_text` is the same text with the leading reasoning block
    removed, including the filter's final held tail. `usage is None` means unknown, always.
    """

    def __init__(self, engine: VllmEngine, lease: Lease, prepared: PreparedRequest) -> None:
        self.lease = lease
        self.prepared = prepared
        self.raw_text = ""
        self.visible_text = ""
        self.held_tail = ""
        self.usage: Usage | None = None
        self.usage_objects = 0                   # how many usage objects the stream carried
        self.usage_candidate: Usage | None = None
        self.usage_candidates: set[tuple[int, int]] = set()
        self.usage_at_deltas = 0
        self.usage_events = 0                    # at most one, asserted by the suite
        self.deltas = 0                          # nonempty upstream content deltas
        self.events = 0                          # delta events emitted (a long delta splits)
        self.finish_reason: str | None = None
        self.stall: str | None = None            # first_token | inter_event | generation
        self.cancelled = False
        self.complete = False                    # the engine sent its completion marker
        self.started = False                     # response headers arrived
        self.malformed_lines = 0
        self.malformed_usage = False
        self.last_event_at: datetime | None = None
        self._iterator = engine._run(self)

    def usage_event(self, usage: Usage | None, reason: str | None) -> EngineEvent:
        self.usage_events += 1
        payload: dict[str, Any] = {} if reason is None else {"reason": reason,
                                                            "certainty": "unknown"}
        return EngineEvent(type=ChunkEventType.usage, payload=payload, usage=usage)

    def __aiter__(self) -> "EngineStream":
        return self

    async def __anext__(self) -> EngineEvent:
        return await self._iterator.__anext__()

    async def aclose(self) -> None:
        await self._iterator.aclose()

    @property
    def terminal_cause(self) -> TerminalCause:
        """Advisory: the cause W2 settles if nothing else intervened; the store recomputes
        settlement (R21/R30).

        `completed` needs both halves: the engine said it finished (`stop` or `length`)
        **and** it told us what it used. A finished stream with unknown usage is
        `engine_incomplete`, which is what D does with a delivered success that has no
        authoritative usage.
        """
        if self.stall == "generation":
            return TerminalCause.deadline_exceeded
        if self.stall is not None:
            return TerminalCause.engine_incomplete
        if self.cancelled:
            return TerminalCause.client_cancelled
        if self.finish_reason in FINISHED_REASONS and self.usage is not None:
            return TerminalCause.completed
        return TerminalCause.engine_incomplete
