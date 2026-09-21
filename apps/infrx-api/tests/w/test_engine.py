#!/usr/bin/env python3
"""W1 / F-CONTRACT + API-STREAM: the real engine adapter, no network and no GPU.

    uv run --frozen pytest -q tests/w/test_engine.py
    uv run --frozen pytest -q tests/w -k api_stream

Every case drives `infrx.worker.VllmEngine` over `httpx.MockTransport`
(`infrx.worker.fakes.FakeUpstream`) on an injected clock: the engine's tokens, stalls
and failures are a script, so nothing here sleeps, waits on the wall clock or opens a
socket. The first case is the exported conformance suite, run against this adapter
exactly as `tests/contracts` runs it against the fake.
"""
from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import httpx
import pytest
from infrx.config import Settings
from infrx.contracts import errors, ports
from infrx.contracts.conformance import MissingHook, OPTIONAL_HOOKS, SUITES, run_cases
from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.support import FakeClock, SequentialIds
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import (Budgets, ChunkEventType, Lease, LeaseKind, MediaRef,
                                     PreparedRequest, TerminalCause, Work)
from infrx.media.video import Media
from infrx.worker import (EngineError, EngineIncomplete, EngineProtocolViolation,
                          EngineTransportError, EngineUnsupported, VllmEngine, cache_salt,
                          prepared_request)
from infrx.worker.fakes import SERVED_MODEL, FakeUpstream, engine_factory
from infrx.worker.reasoning import filter_text


class Box:
    """The two collaborators `conformance.builders` reads off a harness."""

    def __init__(self) -> None:
        self.clock, self.ids = FakeClock(), SequentialIds()


def lease(box: Box, **changes) -> Lease:
    now = box.clock.now()
    return Lease(job_id=box.ids.uuid(), kind=LeaseKind.inference, generation=1,
                 worker_id="worker-a", acquired_at=now,
                 expires_at=box.clock.at(DEFAULTS.lease_ttl_s),
                 generation_deadline_at=box.clock.at(DEFAULTS.generation_timeout_s),
                 first_token_deadline_at=box.clock.at(DEFAULTS.ttft_timeout_s), **changes)


def text_prepared(box: Box, **changes) -> PreparedRequest:
    fields = dict(request_id=box.ids.uuid(), model_revision=b.MODEL,
                  messages=({"role": "user", "content": "Describe this clip."},),
                  max_output_tokens=256, prompt_tokens=1200)
    fields.update(changes)
    return PreparedRequest(**fields)


CUSTOMER_URL = "https://videos.example.com/clip.mp4"


def video_work(box: Box, *, org_id: str = b.ORG_A, refs: tuple[MediaRef, ...] | None = None) -> Work:
    """A `Work` as `JobStore.load_work` hands one over: canonical messages with one
    media part still naming the customer's URL, plus the prepared refs."""
    refs = refs if refs is not None else (b.media(org_id),)
    request = b.request(box, org_id=org_id, refs=refs, max_output_tokens=256)
    request = request.model_copy(update={"messages": (
        {"role": "user", "content": [{"type": "text", "text": "Describe this clip."},
                                     {"type": "video_url", "video_url": {"url": CUSTOMER_URL}}]},)})
    return Work(request=request, media_refs=refs, prepared_refs=refs,
                price_snapshot=b.DEFAULT_PRICE,
                budgets=Budgets.of(DEFAULTS, request.execution_mode))


async def collect(stream) -> list:
    events = []
    async for event in stream:
        events.append(event)
    return events


def deltas(events) -> list[str]:
    return [event.payload["content"] for event in events if event.type is ChunkEventType.delta]


def usages(events) -> list:
    return [event for event in events if event.type is ChunkEventType.usage]


def drive(fault: str = "none", *, limits=DEFAULTS, prepared=None, **engine_kw):
    """(upstream, engine, lease, prepared) for one scripted generation."""
    box = Box()
    upstream = FakeUpstream(fault=fault, clock=box.clock, limits=limits)
    return upstream, upstream.engine(**engine_kw), lease(box), prepared or text_prepared(box)


# --- F-CONTRACT ---------------------------------------------------------------
def test_f_contract__the_real_adapter_passes_the_exported_engine_suite():
    """F-CONTRACT: the same cases the fake passes, against the httpx adapter, with no
    case skipped for a missing hook (r1 R32: a skip is never a pass)."""
    cases, runner = SUITES["engine"]
    skipped: list[MissingHook] = []
    ran = run_cases(cases(), engine_factory, skipped=skipped)
    print(f"\nengine conformance against VllmEngine: {ran} ran, {len(skipped)} skipped")
    assert skipped == [], [(missing.case, missing.hook) for missing in skipped]
    assert ran == len(cases()) == 8
    assert runner(engine_factory) == ran


def test_f_contract__the_factory_publishes_every_hook_the_suite_may_need():
    """R32: the optional hooks of the engine suite are provided, so nothing silently
    asserts less than the fake's run does."""
    harness = engine_factory()
    provided = set(harness.extra) | ({"failures"} if harness.failures is not None else set())
    assert OPTIONAL_HOOKS["engine"] - provided == set()


def test_f_contract__the_adapter_satisfies_the_engine_protocol():
    """F-CONTRACT: `generate` returns the iterator (it is not a coroutine); everything
    else is async, as the ports table says."""
    engine = FakeUpstream().engine()
    assert isinstance(engine, ports.Engine)
    assert not inspect.iscoroutinefunction(engine.generate)
    for name in ("cancel", "health", "drain"):
        assert inspect.iscoroutinefunction(getattr(engine, name)), name
    stream = engine.generate(lease(Box()), text_prepared(Box()))
    assert hasattr(stream, "__aiter__") and hasattr(stream, "__anext__")
    asyncio.run(stream.aclose())


# --- translation --------------------------------------------------------------
def test_api_stream__the_body_carries_the_served_name_the_ceiling_and_the_salt():
    """API-STREAM: vLLM is asked for its **served** model name (F1's `marlin2b`), not
    the platform's model revision; the ceiling is the request's; usage is requested,
    because an unrequested usage block is an unknown settlement."""
    upstream, engine, _lease, _prepared = drive()
    work = video_work(Box())
    prepared = prepared_request(work, prompt_tokens=1234)
    body = engine.upstream_body(prepared)
    assert body["model"] == SERVED_MODEL != prepared.model_revision
    assert body["max_tokens"] == 256 and body["n"] == 1
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert body["cache_salt"] == cache_salt(prepared)
    assert "tenant_salt" not in body and "tenant_salt" not in str(body["messages"])
    assert prepared.prompt_tokens == 1234


def test_api_stream__prepared_media_replaces_the_customers_url():
    """MEDIA-SEC at the engine boundary: the engine must never be handed a URL the
    customer chose, or every fetch control would sit on the wrong side of the request.
    The video part is replaced by the prepared ref, positionally, and a request whose
    parts and prepared refs disagree is refused rather than guessed at."""
    upstream, engine, _lease, _prepared = drive()
    work = video_work(Box())
    prepared = prepared_request(work, prompt_tokens=1200)
    body = engine.upstream_body(prepared)
    part = body["messages"][0]["content"][1]
    assert part["video_url"]["url"] == work.prepared_refs[0].storage_ref
    assert CUSTOMER_URL not in str(body)
    # the multimodal cache is namespaced by the same salt as the prefix cache
    other = prepared_request(video_work(Box(), org_id=b.ORG_B, refs=(b.media(b.ORG_B),)),
                             prompt_tokens=1200)
    assert len(body["mm_uuids"]) == 1
    assert body["mm_uuids"] != engine.upstream_body(other)["mm_uuids"]
    # one prepared ref, two media parts: refused, with no request sent
    two_parts = prepared.model_copy(update={"messages": (
        {"role": "user", "content": [{"type": "video_url", "video_url": {"url": "a"}},
                                     {"type": "video_url", "video_url": {"url": "b"}}]},)})
    with pytest.raises(errors.InvalidRequest) as refused:
        engine.upstream_body(two_parts)
    assert refused.value.code == "invalid_request"
    two_videos = prepared.model_copy(update={
        "media": (b.media(), b.media(handle="upl_conformancefixture0000000000000000002")),
        "messages": two_parts.messages})
    with pytest.raises(errors.UnsupportedParameter):
        engine.upstream_body(two_videos)


def test_api_stream__the_video_token_budget_is_f1s_own_function():
    """MEDIA-PARITY: the frame/pixel budget is `Media.budget_kwargs` called, not a
    second copy of the arithmetic that can drift from the measured one."""
    upstream, engine, _lease, _prepared = drive()
    work = video_work(Box())
    prepared = prepared_request(work, prompt_tokens=1200)
    expected = Media(SimpleNamespace(settings=Settings())).budget_kwargs(
        work.prepared_refs[0].duration_s)
    assert engine.upstream_body(prepared)["mm_processor_kwargs"] == expected
    assert expected["size"]["longest_edge"] % 200704 == 0
    # no media, no multimodal keys at all
    body = engine.upstream_body(text_prepared(Box()))
    assert "mm_processor_kwargs" not in body and "mm_uuids" not in body


def test_api_stream__the_cache_salt_is_per_tenant_and_per_profile():
    """01 "Privacy and retention": tenant source digest **plus** profile version
    namespace the prefix and multimodal caches. Two tenants sending the same bytes must
    not share a cache entry; the same tenant must, or the cache is pointless."""
    box = Box()
    a = prepared_request(video_work(box, org_id=b.ORG_A), prompt_tokens=1200)
    same = prepared_request(video_work(box, org_id=b.ORG_A), prompt_tokens=1200)
    other = prepared_request(video_work(box, org_id=b.ORG_B,
                                        refs=(b.media(b.ORG_B),)), prompt_tokens=1200)
    assert cache_salt(a) == cache_salt(same)
    assert cache_salt(a) != cache_salt(other)
    assert cache_salt(a) != cache_salt(a.model_copy(update={"profile_version": "v2"}))
    assert cache_salt(a) != cache_salt(a.model_copy(update={"model_revision": "other@1"}))
    # no tenant and no media: the request id, so nothing is shared with anybody
    bare, bare_too = text_prepared(box), text_prepared(box)
    assert cache_salt(bare) != cache_salt(bare_too)
    assert cache_salt(bare) == cache_salt(bare.model_copy(update={"messages": bare.messages}))
    # media alone identifies the tenant when no salt was passed
    media_only = a.model_copy(update={"parameters": {}})
    assert cache_salt(media_only) != cache_salt(other.model_copy(update={"parameters": {}}))


def test_api_stream__unsupported_options_are_refused_explicitly():
    """01: tools and structured output are refused explicitly, `n>1` too, r1 R45 refuses
    a client-supplied price, and an unknown parameter is refused rather than forwarded to
    a pinned engine that would ignore or honour it unvalidated. Nothing is sent."""
    for name, value in (("tools", [{"type": "function"}]), ("tool_choice", "auto"),
                        ("response_format", {"type": "json_object"}), ("guided_json", {}),
                        ("logprobs", True), ("n", 2), ("price_snapshot", {"price_version": "x"}),
                        ("max_tokens", 4096), ("stream", False), ("who_knows", 1)):
        upstream, engine, held, _prepared = drive()
        prepared = text_prepared(Box(), parameters={name: value})
        with pytest.raises(errors.UnsupportedParameter) as refused:
            asyncio.run(collect(engine.generate(held, prepared)))
        assert refused.value.code == "unsupported_parameter"
        assert refused.value.param == (name if name != "max_tokens" else "max_tokens")
        assert upstream.requests == [], name
    # and the supported ones are forwarded untouched
    upstream, engine, held, _prepared = drive()
    body = engine.upstream_body(text_prepared(Box(), parameters={"temperature": 0.2, "seed": 7,
                                                                "n": 1}))
    assert body["temperature"] == 0.2 and body["seed"] == 7 and body["n"] == 1


def test_api_stream__the_output_ceiling_is_validated_and_enforced():
    """01/R55: the ceiling is validated after preparation, sent to the engine, and
    enforced against it - an engine that streams past the envelope is a platform
    failure (`01`), never an unreserved customer debit."""
    upstream, engine, held, _prepared = drive()
    for ceiling in (0, DEFAULTS.max_output_tokens + 1):
        with pytest.raises(errors.InvalidRequest):
            engine.upstream_body(text_prepared(Box(), max_output_tokens=ceiling))
    with pytest.raises(errors.ContextLengthExceeded):
        engine.upstream_body(text_prepared(Box(), prompt_tokens=DEFAULTS.max_context_tokens,
                                           max_output_tokens=256))
    # the engine claims more tokens than it was allowed
    upstream, engine, held, _prepared = drive("over_ceiling")
    with pytest.raises(EngineProtocolViolation) as broke:
        asyncio.run(collect(engine.generate(held, _prepared)))
    assert broke.value.terminal_cause is TerminalCause.platform_error
    # ... and even when it reports no usage at all, more nonempty deltas than the
    # ceiling is proof on its own: every delta carries at least one token.
    upstream, engine, held, _prepared = drive("missing_usage")
    tiny = text_prepared(Box(), max_output_tokens=2)
    with pytest.raises(EngineProtocolViolation) as broke:
        asyncio.run(collect(engine.generate(held, tiny)))
    assert broke.value.facts["deltas"] == 3 and upstream.requests[0]["max_tokens"] == 2


# --- events, usage, text ------------------------------------------------------
def test_api_stream__canonical_events_are_progress_deltas_and_one_usage():
    """API-STREAM: one progress event when the engine accepts the request, the model's
    text as deltas, exactly one usage event, and the authoritative counts are the
    engine's numbers - not the number of chunks we happened to receive."""
    upstream, engine, held, prepared = drive()
    stream = engine.generate(held, prepared)
    events = asyncio.run(collect(stream))
    assert events[0].type is ChunkEventType.progress
    assert "".join(deltas(events)) == upstream.text == stream.raw_text
    assert len(usages(events)) == 1
    usage = usages(events)[0].usage
    assert usage.prompt_tokens == upstream.prompt_tokens
    assert usage.completion_tokens == len(upstream.deltas()) == stream.deltas
    assert usage.certainty.value == "authoritative"
    assert stream.complete and stream.finish_reason == "stop"
    assert stream.terminal_cause is TerminalCause.completed
    assert upstream.requests[0]["messages"] == [dict(message) for message in prepared.messages]


def test_api_stream__deltas_carry_the_raw_text_and_the_visible_text():
    """API-STREAM: the delta payload keeps the model's own text (what trace capture
    records, DEC-05) next to the filtered text (what the customer reads), so the
    delimiter filter runs once, here, and not again in every consumer."""
    upstream, engine, held, prepared = drive("split_reasoning_delimiters")
    stream = engine.generate(held, prepared)
    events = asyncio.run(collect(stream))
    raw = "".join(deltas(events))
    visible = "".join(event.payload["visible"] for event in events
                      if event.type is ChunkEventType.delta)
    assert raw == "".join(upstream.deltas()) == stream.raw_text
    assert raw.count("<think>") == 1 and "<think>" not in visible
    assert visible == stream.visible_text == filter_text(raw) == "Two people unload boxes."


def test_api_stream__split_tokens_are_reassembled_whatever_the_boundaries():
    """API-STREAM: one character per delta is still the same answer."""
    upstream, engine, held, prepared = drive("split_tokens")
    stream = engine.generate(held, prepared)
    events = asyncio.run(collect(stream))
    assert "".join(deltas(events)) == upstream.text
    assert stream.visible_text == upstream.text


def test_api_stream__missing_or_malformed_usage_is_explicitly_unknown():
    """DUR-SETTLE feeds on this: `02` forbids output chunks as a token estimator, so a
    usage block we cannot trust leaves the usage unknown and D reconciles it."""
    upstream, engine, held, prepared = drive("missing_usage")
    stream = engine.generate(held, prepared)
    events = asyncio.run(collect(stream))
    assert usages(events) == [] and stream.usage is None and stream.complete
    for fault in ("malformed_usage", "inconsistent_usage", "string_usage"):
        upstream, engine, held, prepared = drive(fault)
        stream = engine.generate(held, prepared)
        events = asyncio.run(collect(stream))
        assert [event.usage for event in events] == [None] * len(events), fault
        assert len(usages(events)) == 1 and stream.usage is None and stream.malformed_usage
        assert usages(events)[0].payload["certainty"] == "unknown"


def test_api_stream__at_most_one_usage_event_per_stream():
    """DUR-SETTLE: two usage objects in one stream must not become two settlements."""
    box = Box()
    upstream = FakeUpstream(clock=box.clock)
    usage = {"prompt_tokens": 7, "completion_tokens": 1, "total_tokens": 8}

    async def twice():
        yield b'data: {"choices":[{"index":0,"delta":{"content":"hi"}}]}\n\n'
        yield f'data: {{"choices":[],"usage":{usage}}}\n\n'.replace("'", '"').encode()
        yield f'data: {{"choices":[],"usage":{usage}}}\n\n'.replace("'", '"').encode()
        yield b"data: [DONE]\n\n"

    client = httpx.AsyncClient(base_url="http://engine.invalid", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=twice())))
    engine = VllmEngine(client, served_model=SERVED_MODEL, clock=box.clock)
    events = asyncio.run(collect(engine.generate(lease(box), text_prepared(box))))
    assert len(usages(events)) == 1 and usages(events)[0].usage.total_tokens == 8


# --- failure classes ----------------------------------------------------------
def test_api_stream__transport_engine_and_incomplete_failures_are_distinct():
    """W1 acceptance: the adapter distinguishes a transport failure, an engine error
    before and after headers, and output that simply stopped. None of them is a
    `DomainError`, so no route can echo an engine's text at a customer."""
    upstream, engine, held, prepared = drive("transport_error")
    with pytest.raises(EngineTransportError) as failed:
        asyncio.run(collect(engine.generate(held, prepared)))
    assert failed.value.facts["stage"] == "pre_headers"
    assert not isinstance(failed.value, errors.DomainError)
    assert failed.value.terminal_cause is TerminalCause.engine_error

    upstream, engine, held, prepared = drive("engine_error_pre_headers")
    stream = engine.generate(held, prepared)
    with pytest.raises(EngineError) as failed:
        asyncio.run(collect(stream))
    assert failed.value.facts == {"stage": "pre_headers", "status": 500}
    assert not stream.started and stream.deltas == 0
    # the engine's body is a stack trace with internal paths in it: operator-only, and
    # bounded, because a failure detail is not a log sink
    assert len(failed.value.detail) == 500 < len(upstream.handle(
        httpx.Request("POST", "http://engine.invalid/v1/chat/completions", json={})).content)

    upstream, engine, held, prepared = drive("engine_error_post_headers")
    stream = engine.generate(held, prepared)
    with pytest.raises(EngineError) as failed:
        asyncio.run(collect(stream))
    assert failed.value.facts == {"stage": "stream"} and stream.started and stream.deltas == 1

    upstream, engine, held, prepared = drive("abrupt_exit")
    stream = engine.generate(held, prepared)
    with pytest.raises(EngineTransportError) as failed:
        asyncio.run(collect(stream))
    assert failed.value.facts["stage"] == "stream" and stream.deltas == 1

    upstream, engine, held, prepared = drive("truncated")
    stream = engine.generate(held, prepared)
    with pytest.raises(EngineIncomplete) as failed:
        asyncio.run(collect(stream))
    assert failed.value.terminal_cause is TerminalCause.engine_incomplete
    assert not stream.complete and stream.usage is None


def test_api_stream__a_stall_ends_the_stream_and_says_which_bound_it_passed():
    """API-STREAM: the engine simply stops, so the iterator ends rather than raising -
    and the adapter stops **waiting** at the store's first-token instant (r1 R20) and at
    the inter-event budget. The keepalives prove the timer fired: the scripted upstream
    would have gone on sending them for another five minutes."""
    upstream, engine, held, prepared = drive("prefill_stall")
    started = upstream.clock.now()
    stream = engine.generate(held, prepared)
    events = asyncio.run(collect(stream))
    assert [event.type for event in events] == [ChunkEventType.progress]
    assert stream.stall == "first_token" and stream.usage is None and not stream.complete
    assert 0 < upstream.pings < upstream.max_keepalives
    elapsed = (upstream.clock.now() - started).total_seconds()
    assert DEFAULTS.ttft_timeout_s < elapsed < DEFAULTS.generation_timeout_s
    assert stream.terminal_cause is TerminalCause.engine_incomplete

    upstream, engine, held, prepared = drive("midstream_stall")
    started = upstream.clock.now()
    stream = engine.generate(held, prepared)
    events = asyncio.run(collect(stream))
    assert deltas(events) and usages(events) == []
    assert stream.stall == "inter_event" and 0 < upstream.pings < upstream.max_keepalives
    assert (upstream.clock.now() - started).total_seconds() > DEFAULTS.tpot_stall_s

    # a read timeout after the headers is the same stall by another route
    upstream, engine, held, prepared = drive("read_timeout")
    stream = engine.generate(held, prepared)
    events = asyncio.run(collect(stream))
    assert stream.stall == "inter_event" and deltas(events) and usages(events) == []


def test_api_stream__a_generation_deadline_ends_the_attempt():
    """R20: the worker compares against the persisted instants, so a lease whose
    generation deadline has passed stops at the next event."""
    box = Box()
    upstream = FakeUpstream(clock=box.clock)
    engine = upstream.engine()
    held = lease(box)
    expired = held.model_copy(update={"generation_deadline_at": box.clock.at(-1),
                                      "first_token_deadline_at": box.clock.at(-1)})
    stream = engine.generate(expired, text_prepared(box))
    events = asyncio.run(collect(stream))
    assert [event.type for event in events] == [ChunkEventType.progress]
    assert stream.stall == "first_token"          # the earlier bound is reported first
    later = held.model_copy(update={"first_token_deadline_at": box.clock.at(5),
                                    "generation_deadline_at": box.clock.at(5)})
    upstream = FakeUpstream(clock=box.clock, fault="midstream_stall")
    stream = upstream.engine().generate(later, text_prepared(box))
    asyncio.run(collect(stream))
    assert stream.stall in ("inter_event", "generation")


# --- cancellation -------------------------------------------------------------
def test_api_stream__cancellation_closes_the_upstream_stream():
    """API-STREAM: cancelling reaches the engine, because closing the response is what
    tells vLLM to stop generating. The usage is then unknown - the deltas we happened to
    see are not a token count."""
    box = Box()
    upstream = FakeUpstream(fault="cancellation_race", clock=box.clock)
    engine = upstream.engine()
    held = lease(box)
    stream = engine.generate(held, text_prepared(box))

    async def cancel_after_two():
        events, cancelled = [], False
        async for event in stream:
            events.append(event)
            if not cancelled and len(deltas(events)) == 2:
                assert await engine.cancel(held) is True
                cancelled = True
        return events

    events = asyncio.run(cancel_after_two())
    assert len(deltas(events)) < upstream.long_stream_deltas
    assert upstream.closed and not upstream.completed, "the adapter left the engine running"
    assert stream.cancelled and len(usages(events)) == 1
    assert usages(events)[0].usage is None and stream.usage is None
    assert usages(events)[0].payload["certainty"] == "unknown"
    assert stream.terminal_cause is TerminalCause.client_cancelled
    assert engine.cancelled == set()               # the intent does not outlive the stream


def test_api_stream__a_request_cancelled_before_it_starts_is_never_sent():
    """API-STREAM: nothing ran, so zero tokens is the authoritative answer and the
    engine never sees the request at all."""
    box = Box()
    upstream = FakeUpstream(clock=box.clock)
    engine = upstream.engine()
    held = lease(box)
    assert asyncio.run(engine.cancel(held)) is True
    stream = engine.generate(held, text_prepared(box))
    events = asyncio.run(collect(stream))
    assert upstream.requests == []
    assert len(usages(events)) == 1 and deltas(events) == []
    assert usages(events)[0].usage.total_tokens == 0
    assert usages(events)[0].usage.certainty.value == "authoritative"
    assert stream.cancelled and engine.cancelled == set()


# --- readiness and the version pin -------------------------------------------
def test_f_contract__health_drain_and_the_capability_probe():
    """API-STREAM/OPS-RECOVER: drain is observable, an engine that does not answer is
    not ready, and the version/capability hook refuses an engine that is not the pinned
    one (W3 supplies the pin)."""
    box = Box()
    upstream = FakeUpstream(clock=box.clock)
    engine = upstream.engine()
    assert asyncio.run(engine.health())["ready"] is True
    asyncio.run(engine.drain())
    health = asyncio.run(engine.health())
    assert health["ready"] is False and health["drained"] is True

    unhealthy = FakeUpstream(clock=box.clock, health_status=503).engine()
    assert asyncio.run(unhealthy.health())["ready"] is False

    probe = asyncio.run(upstream.engine().capabilities())
    assert probe["version"] == upstream.version and SERVED_MODEL in probe["models"]
    pinned = upstream.engine(require_version="0.0.1")
    with pytest.raises(EngineUnsupported):
        asyncio.run(pinned.capabilities())
    other = FakeUpstream(clock=box.clock, served_model="something-else")
    mismatch = VllmEngine(other.client(), served_model=SERVED_MODEL, clock=box.clock)
    with pytest.raises(EngineUnsupported):
        asyncio.run(mismatch.capabilities())


def test_api_stream__junk_lines_are_counted_and_never_relayed():
    """API-STREAM: a line that is not JSON is the engine misbehaving, not content; it is
    counted, and the customer never sees it."""
    box = Box()

    async def junk():
        yield b"data: not json at all\n\n"
        yield b": keepalive\n\n"
        yield b'data: {"choices":[{"index":0,"delta":{"content":"ok"}}]}\n\n'
        yield b"data: [DONE]\n\n"

    client = httpx.AsyncClient(base_url="http://engine.invalid", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=junk())))
    engine = VllmEngine(client, served_model=SERVED_MODEL, clock=box.clock)
    stream = engine.generate(lease(box), text_prepared(box))
    events = asyncio.run(collect(stream))
    assert deltas(events) == ["ok"] and stream.malformed_lines == 1 and stream.complete
