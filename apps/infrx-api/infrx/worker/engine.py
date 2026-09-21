"""W1: the vLLM OpenAI-compatible engine adapter (`ports.Engine`).

One HTTP client, injected; one clock, injected; no GPU and no network in any test
(`httpx.MockTransport` and `fakes.FakeUpstream` script the upstream instead). The
adapter answers the same exported conformance suite as the fake engine
(`infrx.contracts.conformance.run_engine_conformance`), so W2 can be written
against either.

What it is responsible for, and what it deliberately is not:

* **Translation.** `prepared_request(work, prompt_tokens)` turns the `Work` a lease
  holder loaded into the `PreparedRequest` the port takes; `VllmEngine.upstream_body`
  turns that into vLLM's JSON. The served model name is *this engine's*
  configuration, never the platform's model revision, and the video token budget is
  F1's `Media.budget_kwargs` called, not copied.
* **Tenant namespacing.** Every request carries a `cache_salt` derived from the
  tenant's source digests and the profile version (`01` "Privacy and retention"), so
  neither the prefix cache nor the multimodal cache is shared across tenants. With
  no tenant information at all the salt falls back to the request id: no sharing,
  rather than sharing with everybody.
* **Canonical events.** SSE becomes `EngineEvent`s: one `progress`, then `delta`s
  carrying both the raw model text and the visible text (`reasoning.py` filters the
  leading `<think>` block across arbitrary chunk boundaries), then at most one
  `usage`.
* **Authoritative usage only.** Missing or malformed usage is *explicitly unknown*
  (a `usage` event with `usage=None`, or none at all). Output chunks are never
  counted into a billable number - `02` forbids output chunks as a token estimator.
* **Honest failure classes.** A `DomainError` means the request was refused before
  anything ran (`unsupported_parameter`, `context_length_exceeded`); an
  `EngineFailure` means the external process failed, and each subclass carries the
  `TerminalCause` W2 settles. A stall or a cancellation is neither: the iterator
  simply ends, and `EngineStream` says why.
* **Not** journalling, settling, leasing or retrying: those are W2 through the
  JobStore and StreamStore. The lease is only read here, for its phase deadlines.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, AsyncIterator, Iterable

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

VIDEO_PART_TYPES = ("video_url", "input_video")
IMAGE_PART_TYPES = ("image_url", "input_image")
MEDIA_PART_TYPES = VIDEO_PART_TYPES + IMAGE_PART_TYPES

# vLLM's field for the multimodal/prefix cache namespace. W3's capability probe
# confirms it against the pinned image; if the pinned engine names it differently,
# this constant is the only line that changes.
CACHE_SALT_FIELD = "cache_salt"
MM_UUIDS_FIELD = "mm_uuids"
DETAIL_MAX_CHARS = 500          # operator-only text, bounded so a log line stays a log line


# --- failures of an external process -----------------------------------------
class EngineFailure(RuntimeError):
    """The engine is a separate process, so its failures are **not** `DomainError`s
    (the shared fake's `EngineExited` follows the same rule). `terminal_cause` is the
    `TerminalCause` W2 settles, so the distinction the adapter draws is machine
    readable rather than a sentence in a docstring."""

    terminal_cause = TerminalCause.engine_error

    def __init__(self, detail: str = "", **facts: Any) -> None:
        self.detail = detail[:DETAIL_MAX_CHARS]
        self.facts = facts
        super().__init__(f"{type(self).__name__}: {self.detail}" if self.detail else type(self).__name__)


class EngineTransportError(EngineFailure):
    """The engine could not be reached, or the connection died mid-stream (an abrupt
    exit looks exactly like this from here)."""


class EngineError(EngineFailure):
    """The engine answered with an error: a non-200 before the stream, or an error
    object inside it. `facts["stage"]` distinguishes the two, because a post-header
    error may already have published output that must never be regenerated."""


class EngineIncomplete(EngineFailure):
    """The stream ended without a completion marker and without a stall or a
    cancellation to explain it: the output is not the whole answer."""

    terminal_cause = TerminalCause.engine_incomplete


class EngineProtocolViolation(EngineFailure):
    """The engine exceeded the envelope we reserved (more completion tokens than the
    ceiling it was given). `01`: a protocol violation beyond the envelope is a
    platform failure requiring reconciliation, never an unreserved customer debit."""

    terminal_cause = TerminalCause.platform_error


class EngineUnsupported(EngineFailure):
    """The pinned engine does not serve the model, or not at the pinned version."""


# --- translation --------------------------------------------------------------
def prepared_request(work: Work, prompt_tokens: int, *, profile_version: str | None = None) -> PreparedRequest:
    """The `PreparedRequest` for the `Work` a lease holder loaded (r1 R46).

    `prompt_tokens` is preparation's exact count; it is an argument because `Work`
    does not carry one, and guessing it here would be the estimator `02` forbids.
    The media are what preparation produced, falling back to the staged sources for
    a request that needed no preparation, and `tenant_salt` carries the tenant into
    `cache_salt` for a text-only request, which has no media digest to speak for it.
    """
    request = work.request
    refs = work.prepared_refs or work.media_refs
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
    """A deterministic per-tenant identifier for one prepared media object, so the
    engine's multimodal cache is namespaced by the same salt as the prefix cache."""
    return hashlib.sha256(f"{salt}\x1f{ref.digest}".encode()).hexdigest()[:32]


def _media_parts(messages: Iterable[dict]) -> list[dict]:
    """Every media part of the canonical messages, in order."""
    parts = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") in MEDIA_PART_TYPES:
                parts.append(part)
    return parts


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
        # The engine's own cancellation intents. `generate` clears its own entry, so
        # this only grows for a lease that was cancelled and never executed.
        # ponytail: a plain set; if W2 ever cancels leases it never runs in bulk, give
        # it a bounded LRU.
        self.cancelled: set[str] = set()
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
        `EngineProtocolViolation` is what happens if the engine exceeds it anyway.
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
        """The canonical messages with every media part replaced by its prepared ref.

        Positional, and the counts must match: the engine must never be handed a
        customer-supplied URL, because then *it* would fetch it and every SSRF control
        M1 owns would sit on the wrong side of the request.
        """
        messages = copy.deepcopy(list(prepared.messages))
        parts = _media_parts(messages)
        if len(parts) != len(prepared.media):
            raise errors.InvalidRequest(
                f"{len(parts)} media parts but {len(prepared.media)} prepared references",
                param="messages")
        videos = [ref for ref in prepared.media if ref.mime.startswith("video/")]
        if len(videos) > 1:
            raise errors.UnsupportedParameter("one video per request", param="messages")
        for part, ref in zip(parts, prepared.media):
            reference = {"url": ref.storage_ref}
            if part["type"] in VIDEO_PART_TYPES:
                part.clear()
                part.update({"type": "video_url", "video_url": reference})
            else:
                part.clear()
                part.update({"type": "image_url", "image_url": reference})
        return messages

    def upstream_body(self, prepared: PreparedRequest) -> dict[str, Any]:
        """vLLM's request body. Raises a `DomainError` before anything is sent."""
        forwarded = self.check_parameters(prepared.parameters)
        ceiling = self.output_ceiling(prepared)
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
        videos = [ref for ref in prepared.media if ref.mime.startswith("video/")]
        if videos:
            seconds = videos[0].duration_s or 0.0
            body["mm_processor_kwargs"] = self._media.budget_kwargs(seconds)
        if prepared.media:
            body[MM_UUIDS_FIELD] = [media_uuid(ref, salt) for ref in prepared.media]
        return body

    # --- the port -------------------------------------------------------------
    def generate(self, lease: Lease, prepared: PreparedRequest) -> "EngineStream":
        """An async iterator of canonical events; not `async def`, per the port.

        The object it returns is also the report on how the stream ended - the raw
        text for trace capture, the authoritative usage or its absence, the stall or
        the cancellation - because a stall must not raise (the engine simply stops;
        W owns the policy) and W2 still has to know.
        """
        return EngineStream(self, lease, prepared)

    async def cancel(self, lease: Lease) -> bool:
        """Record the intent and stop the stream at the next event, which closes the
        upstream connection and so cancels the generation in vLLM.

        ponytail: a stream already blocked on `aiter_lines` notices at its next event.
        A wholly silent engine is bounded by the stall timers and the client read
        timeout; cancelling *instantly* means cancelling the asyncio task, which is
        W2's job because W2 owns the task.
        """
        self.cancelled.add(lease.job_id)
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
        except (httpx.HTTPError, json.JSONDecodeError, ValueError) as failure:
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
        bound is the clock check in `_run`, which a conformance case can drive."""
        read = max(self.limits.ttft_timeout_s, self.limits.tpot_stall_s)
        return httpx.Timeout(read, connect=self.limits.media_fetch_connect_timeout_s,
                             write=read, pool=read)

    def _overdue(self, stream: "EngineStream", now: datetime) -> str | None:
        """Which bound, if any, this stream has passed. The first-token instant is the
        store's (r1 R20: the worker compares against the persisted instant, it does not
        time a budget locally); the inter-event bound is a per-event budget, so it comes
        from the configuration."""
        lease = stream.lease
        if stream.deltas == 0 and lease.first_token_deadline_at is not None \
                and now >= lease.first_token_deadline_at:
            return "first_token"
        if stream.last_event_at is not None and stream.deltas > 0 \
                and now >= stream.last_event_at + timedelta(seconds=self.limits.tpot_stall_s):
            return "inter_event"
        if now >= lease.generation_deadline_at:
            return "generation"
        return None

    async def _run(self, stream: "EngineStream") -> AsyncIterator[EngineEvent]:
        lease, prepared = stream.lease, stream.prepared
        body = self.upstream_body(prepared)              # DomainError before anything runs
        ceiling = body["max_tokens"]
        if lease.job_id in self.cancelled:
            # Cancelled before the request was sent: nothing ran, so zero tokens is the
            # authoritative answer rather than an estimate.
            self.cancelled.discard(lease.job_id)
            stream.cancelled = True
            stream.usage = Usage.of(0, 0)
            yield EngineEvent(type=ChunkEventType.usage, payload={"reason": "cancelled_before_start"},
                              usage=stream.usage)
            return
        reasoning = ReasoningFilter()
        try:
            async with self.client.stream("POST", self.path, json=body,
                                          timeout=self._timeout()) as response:
                if response.status_code != 200:
                    detail = (await response.aread()).decode("utf-8", "replace")
                    raise EngineError(detail, stage="pre_headers", status=response.status_code)
                stream.started = True
                stream.last_event_at = self.clock.now()
                yield EngineEvent(type=ChunkEventType.progress, payload={"phase": "running"})
                async for line in response.aiter_lines():
                    now = self.clock.now()
                    stall = self._overdue(stream, now)
                    if stall is not None:
                        stream.stall = stall
                        break                            # leaving the block closes the stream
                    if lease.job_id in self.cancelled:
                        stream.cancelled = True
                        break
                    for event in self._events(stream, line, reasoning, ceiling, now):
                        yield event
                    if stream.complete:
                        break
        except httpx.TimeoutException as failure:
            # Post-headers silence is a stall, not a failed engine: the read timeout is
            # the same bound the clock check enforces, reached by a different route.
            if not stream.started:
                raise EngineTransportError(f"{type(failure).__name__}: {failure}",
                                           stage="pre_headers") from None
            stream.stall = self._overdue(stream, self.clock.now()) or "inter_event"
        except httpx.HTTPError as failure:
            raise EngineTransportError(f"{type(failure).__name__}: {failure}",
                                       stage="stream" if stream.started else "pre_headers") from None
        finally:
            self.cancelled.discard(lease.job_id)
            stream.visible_text += reasoning.close()
        # Only the paths that did not raise reach here.
        if stream.stall is None and not stream.complete and not stream.cancelled:
            stream.stall = self._overdue(stream, self.clock.now())
        if stream.cancelled and not stream.usage_seen:
            # Truncated mid-stream: the engine sent no usage and the deltas are not a
            # token count, so the usage is unknown and D reconciles it.
            stream.usage_seen = True
            yield EngineEvent(type=ChunkEventType.usage,
                              payload={"reason": "cancelled", "certainty": "unknown"})
            return
        if not stream.complete and stream.stall is None and stream.finish_reason is None:
            raise EngineIncomplete("the stream ended without a completion marker",
                                   deltas=stream.deltas)

    def _events(self, stream: "EngineStream", line: str, reasoning: ReasoningFilter,
                ceiling: int, now: datetime) -> list[EngineEvent]:
        """One SSE line into canonical events. A line that is not an event, or is not
        JSON, produces none: junk is counted, never relayed."""
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
            detail = obj["error"].get("message", "") if isinstance(obj["error"], dict) else str(obj["error"])
            raise EngineError(detail, stage="stream")
        stream.last_event_at = now
        events: list[EngineEvent] = []
        for choice in obj.get("choices") or ():
            if not isinstance(choice, dict):
                continue
            if choice.get("finish_reason"):
                stream.finish_reason = str(choice["finish_reason"])
            content = (choice.get("delta") or {}).get("content")
            if not isinstance(content, str) or content == "":
                continue
            stream.deltas += 1
            if stream.deltas > ceiling:
                # Each nonempty delta carries at least one token, so more deltas than the
                # ceiling means more tokens than we reserved, whatever the usage says.
                raise EngineProtocolViolation(f"more than {ceiling} output tokens",
                                              deltas=stream.deltas)
            stream.raw_text += content
            visible = reasoning.feed(content)
            stream.visible_text += visible
            events.append(EngineEvent(type=ChunkEventType.delta,
                                      payload={"content": content, "visible": visible}))
        if obj.get("usage") is not None and not stream.usage_seen:
            # At most one usage event per stream, so a repeated or a second, different
            # usage object cannot turn one request into two settlements.
            events.append(self._usage_event(stream, obj["usage"], ceiling))
        return events

    def _usage_event(self, stream: "EngineStream", raw: object, ceiling: int) -> EngineEvent:
        """Authoritative usage, or an explicitly unknown one. Never a count of deltas."""
        usage = _parse_usage(raw)
        stream.usage_seen = True
        if usage is None:
            stream.malformed_usage = True
            return EngineEvent(type=ChunkEventType.usage,
                               payload={"reason": "malformed", "certainty": "unknown"})
        if usage.completion_tokens > ceiling:
            raise EngineProtocolViolation(f"usage reports {usage.completion_tokens} > {ceiling}",
                                          completion_tokens=usage.completion_tokens)
        stream.usage = usage
        return EngineEvent(type=ChunkEventType.usage, payload={}, usage=usage)


def _parse_usage(raw: object) -> Usage | None:
    """vLLM's usage object, or `None` when it is not one.

    Strict on purpose: a string, a float, a boolean, a negative or a `total_tokens`
    that does not add up all mean the engine did not tell us what it did, and a
    guessed number here becomes a customer's debit.
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


class EngineStream:
    """The async iterator `generate` returns, and the report on how it ended.

    `raw_text` is the model's text exactly as it arrived, which is what canonical
    trace capture records; `visible_text` is the same text with the leading reasoning
    block removed. `usage is None` means unknown, always.
    """

    def __init__(self, engine: VllmEngine, lease: Lease, prepared: PreparedRequest) -> None:
        self.lease = lease
        self.prepared = prepared
        self.raw_text = ""
        self.visible_text = ""
        self.usage: Usage | None = None
        self.usage_seen = False                  # at most one usage event per stream
        self.deltas = 0
        self.finish_reason: str | None = None
        self.stall: str | None = None            # first_token | inter_event | generation
        self.cancelled = False
        self.complete = False                    # the engine sent its completion marker
        self.started = False                     # response headers arrived
        self.malformed_lines = 0
        self.malformed_usage = False
        self.last_event_at: datetime | None = None
        self._iterator = engine._run(self)

    def __aiter__(self) -> "EngineStream":
        return self

    async def __anext__(self) -> EngineEvent:
        return await self._iterator.__anext__()

    async def aclose(self) -> None:
        await self._iterator.aclose()

    @property
    def terminal_cause(self) -> TerminalCause:
        """Advisory: the cause W2 settles if nothing else intervened. W2 still decides
        (a `completed` cause with unknown usage is `engine_incomplete` at the store,
        which is D's rule, not the engine's)."""
        if self.stall == "generation":
            return TerminalCause.deadline_exceeded
        if self.stall is not None:
            return TerminalCause.engine_incomplete
        if self.cancelled:
            return TerminalCause.client_cancelled
        if self.complete or self.finish_reason is not None:
            return TerminalCause.completed
        return TerminalCause.engine_incomplete
