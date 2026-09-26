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
import json
from itertools import combinations
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError
from infrx.config import Settings
from infrx.contracts import errors, fixtures, ports
from infrx.contracts.codec import compact_bytes
from infrx.contracts.conformance import MissingHook, OPTIONAL_HOOKS, SUITES, run_cases
from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes.support import FakeClock, SequentialIds
from infrx.contracts.limits import DEFAULTS
from infrx.contracts.records import (Budgets, ChunkEventType, Lease, LeaseKind, MediaRef,
                                     PreparedRequest, TerminalCause, Work)
from infrx.media.video import Media
from infrx.worker import (EngineError, EngineFailure, EngineIncomplete, EngineProtocolViolation,
                          EngineTransportError, EngineUnsupported, VllmEngine, cache_salt,
                          prepared_request)
from infrx.worker.engine import (LOCAL_MEDIA_SCHEME, MAX_CANCEL_INTENTS,
                                 MIN_JOURNAL_EVENT_BYTES,
                                 PAYLOAD_OVERHEAD_BYTES, _delta_payload, check_storage_ref,
                                 media_uuid)
from infrx.worker.fakes import (ERROR_BODY_CHUNK, FAKE_MEDIA_ROOT, SERVED_MODEL, FakeUpstream, m2_local_uri,
                               engine_factory)
from infrx.worker.reasoning import filter_text

EVIL = "http://169.254.169.254/latest/meta-data/iam/security-credentials/"


class Box:
    """The two collaborators `conformance.builders` reads off a harness."""

    def __init__(self) -> None:
        self.clock, self.ids = FakeClock(), SequentialIds()


def lease(box: Box, **changes) -> Lease:
    now = box.clock.now()
    fields = dict(job_id=box.ids.uuid(), kind=LeaseKind.inference, generation=1,
                  worker_id="worker-a", acquired_at=now,
                  expires_at=box.clock.at(DEFAULTS.lease_ttl_s),
                  generation_deadline_at=box.clock.at(DEFAULTS.generation_timeout_s),
                  first_token_deadline_at=box.clock.at(DEFAULTS.ttft_timeout_s))
    fields.update(changes)
    return Lease(**fields)


def text_prepared(box: Box, **changes) -> PreparedRequest:
    fields = dict(request_id=box.ids.uuid(), model_revision=b.MODEL,
                  messages=({"role": "user", "content": "Describe this clip."},),
                  max_output_tokens=256, prompt_tokens=1200)
    fields.update(changes)
    return PreparedRequest(**fields)


def video_message(url: str = EVIL) -> dict:
    """The canonical shape G normalizes to: one text part and one video part, the video
    still naming whatever the customer asked for."""
    return {"role": "user", "content": [{"type": "text", "text": "Describe this clip."},
                                        {"type": "video_url", "video_url": {"url": url}}]}


def video_work(box: Box, *, org_id: str = b.ORG_A, refs: tuple[MediaRef, ...] | None = None,
               messages: tuple[dict, ...] | None = None) -> Work:
    """A `Work` as `JobStore.load_work` hands one over."""
    refs = refs if refs is not None else (b.media(org_id),)
    request = b.request(box, org_id=org_id, refs=refs, max_output_tokens=256)
    request = request.model_copy(update={"messages": messages or (video_message(),)})
    return Work(request=request, media_refs=refs, prepared_refs=refs,
                price_snapshot=b.DEFAULT_PRICE,
                budgets=Budgets.of(DEFAULTS, request.execution_mode))


def text_work(box: Box, *, org_id: str = b.ORG_A, parameters: dict | None = None) -> Work:
    request = b.request(box, org_id=org_id, max_output_tokens=256, parameters=parameters)
    return Work(request=request, price_snapshot=b.DEFAULT_PRICE,
                budgets=Budgets.of(DEFAULTS, request.execution_mode))


async def collect(stream) -> list:
    events = []
    async for event in stream:
        events.append(event)
    return events


def raws(events) -> list[str]:
    return [event.payload["raw"] for event in events if event.type is ChunkEventType.delta]


def visibles(events) -> list[str]:
    return [event.payload["visible"] for event in events if event.type is ChunkEventType.delta]


def usages(events) -> list:
    return [event for event in events if event.type is ChunkEventType.usage]


def drive(fault: str = "none", *, limits=DEFAULTS, prepared=None, **engine_kw):
    """(upstream, engine, lease, prepared) for one scripted generation."""
    box = Box()
    upstream = FakeUpstream(fault=fault, clock=box.clock, limits=limits)
    return upstream, upstream.engine(**engine_kw), lease(box), prepared or text_prepared(box)


def accepted(engine: VllmEngine, prepared: PreparedRequest):
    """The body, or the refusal code as text, so "this request is accepted" is an
    assertion rather than an exception escaping the case (and a mutant that starts
    refusing it is killed by that assertion, not by a stray traceback)."""
    try:
        return engine.upstream_body(prepared)
    except errors.DomainError as refused:
        return f"refused: {refused.code}"


def drained(stream):
    """(events, failure). A stall or a cancellation must *not* raise, so "no failure" is
    something a case can assert.

    An **adapter-side** crash is re-raised rather than returned: `_run` wraps any unexpected
    exception as `EngineFailure(stage="adapter")`, and swallowing that here let a mutant's
    `NameError` reach a case as a tidy "failure is not None" assertion - a false kill the
    round-2 review found with two probes.
    """
    try:
        return asyncio.run(collect(stream)), None
    except EngineFailure as failure:
        if failure.facts.get("stage") == "adapter":
            raise
        return [], failure


def outcome_of(engine: VllmEngine, held: Lease, prepared: PreparedRequest) -> str:
    """How a whole generation ended, as a comparable string: a refusal code, an engine
    failure's class, or "accepted". Comparing outcomes keeps a case honest - a *different*
    failure is not the refusal it asked for."""
    try:
        asyncio.run(collect(engine.generate(held, prepared)))
    except errors.DomainError as refused:
        return f"refused: {refused.code}"
    except EngineFailure as failure:
        return f"failed: {type(failure).__name__}"
    return "accepted"


def refusal(engine: VllmEngine, prepared: PreparedRequest) -> str:
    """The error code a refused body produces, or the absence of one. Comparing codes
    keeps a case honest: another refusal for another reason is not the same refusal."""
    outcome = accepted(engine, prepared)
    return outcome if isinstance(outcome, str) else "accepted"


def streamed(box: Box, script) -> VllmEngine:
    """An engine over an ad-hoc SSE script, for the shapes the fault list does not carry."""
    client = httpx.AsyncClient(base_url="http://engine.invalid", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=script())))
    return VllmEngine(client, served_model=SERVED_MODEL, clock=box.clock)


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


def test_f_contract__health_drain_and_the_capability_probe():
    """API-STREAM/OPS-RECOVER: drain is observable, an engine that answers badly or does
    not answer at all is not ready, and the version/capability hook refuses an engine that
    is not the pinned one (W3 supplies the pin)."""
    box = Box()
    upstream = FakeUpstream(clock=box.clock)
    engine = upstream.engine()
    assert asyncio.run(engine.health())["ready"] is True
    asyncio.run(engine.drain())
    health = asyncio.run(engine.health())
    assert health["ready"] is False and health["drained"] is True

    sick = FakeUpstream(clock=box.clock, health_status=503).engine()
    assert asyncio.run(sick.health())["ready"] is False
    gone = FakeUpstream(clock=box.clock, health_unreachable=True).engine()
    unreachable = asyncio.run(gone.health())
    assert unreachable["ready"] is False and unreachable["detail"] == "ConnectError"

    probe = asyncio.run(upstream.engine().capabilities())
    assert probe["version"] == upstream.version and SERVED_MODEL in probe["models"]
    with pytest.raises(EngineUnsupported):
        asyncio.run(upstream.engine(require_version="0.0.1").capabilities())
    other = FakeUpstream(clock=box.clock, served_model="something-else")
    mismatch = VllmEngine(other.client(), served_model=SERVED_MODEL, clock=box.clock)
    with pytest.raises(EngineUnsupported):
        asyncio.run(mismatch.capabilities())


# --- translation: the allow-list (r1 R58) ------------------------------------
def test_api_stream__the_body_carries_the_served_name_the_ceiling_and_the_salt():
    """API-STREAM: vLLM is asked for its **served** model name (F1's `marlin2b`), not
    the platform's model revision; the ceiling is the request's; usage is requested,
    because an unrequested usage block is an unknown settlement."""
    upstream, engine, _lease, _prepared = drive()
    prepared = prepared_request(video_work(Box()), prompt_tokens=1234)
    body = engine.upstream_body(prepared)
    assert body["model"] == SERVED_MODEL != prepared.model_revision
    assert body["max_tokens"] == 256 and body["n"] == 1
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert body["cache_salt"] == cache_salt(prepared)
    # the tenant travels as a digest, never as a field the engine could echo
    assert "tenant_salt" not in body and b.ORG_A not in body["cache_salt"]
    assert prepared.prompt_tokens == 1234


def test_api_stream__messages_are_rebuilt_from_an_allow_list():
    """r1 R58 / MEDIA-SEC at the engine boundary: the pilot is text plus video, and the
    body is **rebuilt**, never forwarded. Every other shape - an untyped part, an image,
    audio, file or embeds part, a differently-cased type, an extra key beside `type`, a
    message-level `tool_calls`, another role, a part that is not an object - is refused
    before anything is sent, so there is no shape in which a customer URL reaches the
    engine and is fetched from inside our network.

    A deny-list on part *types* is what made this false before: `{"image_url": {...}}`
    carries no `type` at all and was forwarded verbatim.
    """
    refused_parts = (
        {"image_url": {"url": EVIL}},                            # untyped: no `type` at all
        {"type": "image_url", "image_url": {"url": EVIL}},
        {"type": "audio_url", "audio_url": {"url": EVIL}},
        {"type": "file", "file": {"file_data": EVIL}},
        {"type": "image_embeds", "image_embeds": EVIL},
        {"type": "VIDEO_URL", "video_url": {"url": EVIL}},       # case matters
        {"type": "video_url", "video_url": {"url": EVIL}, "detail": "high"},
        {"type": "text", "text": "hi", "image_url": {"url": EVIL}},
        {"type": "text"},
        {"type": "text", "text": 5},
        {"type": None, "text": "hi"},
        "just a string",
        5,
        None,
    )
    for part in refused_parts:
        upstream, engine, held, _prepared = drive()
        prepared = text_prepared(Box(), messages=({"role": "user", "content": [part]},))
        with pytest.raises(errors.DomainError) as refused:
            asyncio.run(collect(engine.generate(held, prepared)))
        assert refused.value.code in ("invalid_request", "unsupported_parameter"), part
        assert upstream.requests == [], part

    refused_messages = (
        {"role": "user", "content": "hi", "tool_calls": [{"id": "1"}]},
        {"role": "user", "content": "hi", "mm_processor_kwargs": {"fps": 99}},
        {"role": "tool", "content": "hi"},
        {"role": "function", "content": "hi"},
        {"role": "user"},                                        # no content
        {"content": "hi"},                                       # no role
        {"role": "user", "content": 5},
        {"role": "user", "content": {"text": "hi"}},
    )
    for message in refused_messages:
        upstream, engine, held, _prepared = drive()
        prepared = text_prepared(Box(), messages=(message,))
        with pytest.raises(errors.DomainError):
            asyncio.run(collect(engine.generate(held, prepared)))
        assert upstream.requests == [], message
    # a message that is not an object at all cannot even be constructed: the record
    # refuses it, which is the same allow-list one layer earlier
    with pytest.raises(ValidationError):
        text_prepared(Box(), messages=("not a message",))

    # an extra key on a *matched* video part is refused for that reason alone: with no ref
    # to match, the count check would have refused it anyway and proved nothing
    upstream, engine, held, _prepared = drive()
    matched = prepared_request(video_work(Box()), prompt_tokens=1200)
    detailed = matched.model_copy(update={"messages": (
        {"role": "user", "content": [{"type": "video_url", "video_url": {"url": EVIL},
                                      "detail": "high"}]},)})
    assert refusal(engine, detailed) == "refused: invalid_request"

    # and the two shapes that are allowed survive, rebuilt to exactly {role, content}
    upstream, engine, held, _prepared = drive()
    prepared = prepared_request(video_work(Box()), prompt_tokens=1200)
    body = engine.upstream_body(prepared)
    assert [sorted(message) for message in body["messages"]] == [["content", "role"]]
    assert body["messages"][0]["content"] == [
        {"type": "text", "text": "Describe this clip."},
        # S2M §2/D3: the local file the prepared reference materialized to, under the
        # root the engine was started with - never the customer's own url
        {"type": "video_url",
         "video_url": {"url": m2_local_uri(FAKE_MEDIA_ROOT)(prepared.media[0])}}]
    for role in ("system", "user", "assistant"):
        engine.upstream_body(text_prepared(Box(), messages=({"role": role, "content": "hi"},)))


def test_api_stream__no_outbound_body_ever_carries_a_foreign_url():
    """MEDIA-SEC: the positive half of the allow-list. Whatever the customer put in the
    message, the only reference in the outbound JSON is the prepared one, as a `file://`
    path under the root the engine was started with (S2M §2/D3) - so the one scheme that
    can appear names a local file of this tenant's, and no network scheme ever does."""
    box = Box()
    for url in (EVIL, "https://videos.example.com/clip.mp4", "data:video/mp4;base64,AAAA",
                "file:///etc/passwd", "gopher://internal/"):
        upstream = FakeUpstream(clock=box.clock)
        engine = upstream.engine()
        work = video_work(box, messages=(video_message(url),))
        prepared = prepared_request(work, prompt_tokens=1200)
        events = asyncio.run(collect(engine.generate(lease(box), prepared)))
        assert events, url
        sent = json.dumps(upstream.requests[0])
        assert sent.count("://") == 1, (url, sent)          # exactly the one file:// we built
        assert m2_local_uri(FAKE_MEDIA_ROOT)(prepared.media[0]) in sent
        for scheme in ("http", "data:", "gopher", "ftp", "s3", "//169.254"):
            assert scheme not in sent.replace(LOCAL_MEDIA_SCHEME, ""), (url, scheme)


def test_api_stream__prepared_media_replaces_the_customers_url():
    """API-STREAM: the video part is replaced positionally by its prepared ref; a request
    whose parts and refs disagree in **count or order** is refused rather than guessed at,
    and a part kind that does not match its ref's MIME class is refused too."""
    upstream, engine, _lease, _prepared = drive()
    work = video_work(Box())
    prepared = prepared_request(work, prompt_tokens=1200)
    body = engine.upstream_body(prepared)
    assert body["messages"][0]["content"][1]["video_url"] == {
        "url": m2_local_uri(FAKE_MEDIA_ROOT)(work.prepared_refs[0])}
    assert EVIL not in json.dumps(body)

    # a prepared ref with no media part at all: only the count check can refuse this
    assert refusal(engine, prepared.model_copy(update={"messages": (
        {"role": "user", "content": "no video here"},)})) == "refused: invalid_request"

    # two video parts, one prepared ref: refused (the count)
    two_parts = prepared.model_copy(update={"messages": (
        {"role": "user", "content": [{"type": "video_url", "video_url": {"url": "a"}},
                                     {"type": "video_url", "video_url": {"url": "b"}}]},)})
    with pytest.raises(errors.InvalidRequest) as refused:
        engine.upstream_body(two_parts)
    assert refused.value.code == "invalid_request"
    # two parts *and* two refs: the counts agree and the kinds match, so the only rule
    # left to refuse it is "one video per request"
    two_videos = prepared.model_copy(update={
        "media": (b.media(), b.media(handle="upl_conformancefixture0000000000000000002")),
        "messages": two_parts.messages})
    assert refusal(engine, two_videos) == "refused: unsupported_parameter"
    # a video part paired with a ref that is not video: refused by kind, in order
    picture = b.media().model_copy(update={"mime": "image/png"})
    with pytest.raises(errors.InvalidRequest):
        engine.messages_for(prepared.model_copy(update={"media": (picture,)}))
    assert refusal(engine, prepared.model_copy(update={
        "media": (picture,)})) == "refused: unsupported_parameter"


def test_api_stream__media_belongs_to_the_requests_tenant():
    """R10/R55: the refs a worker executes are the request's own tenant's, checked where
    the request becomes engine work and again before the body is built."""
    box = Box()
    work = video_work(box)
    foreign = work.model_copy(update={"prepared_refs": (b.media(b.ORG_B),),
                                      "media_refs": (b.media(b.ORG_B),)})
    with pytest.raises(errors.NotFound):
        prepared_request(foreign, prompt_tokens=1200)
    prepared = prepared_request(work, prompt_tokens=1200)
    mixed = prepared.model_copy(update={"media": (b.media(b.ORG_A), b.media(b.ORG_B)),
                                       "messages": (
        {"role": "user", "content": [{"type": "video_url", "video_url": {"url": "a"}},
                                     {"type": "video_url", "video_url": {"url": "b"}}]},)})
    upstream, engine, _lease, _prepared = drive()
    assert refusal(engine, mixed) == "refused: not_found"
    # ... and with no salt to check them against, two tenants in one prompt are still
    # refused: they would share a cache namespace and a token budget
    assert refusal(engine, mixed.model_copy(update={"parameters": {}})) == "refused: not_found"


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
    not share a cache entry; the same tenant must, or the cache is pointless. A
    customer-supplied `tenant_salt` is overwritten, never honoured."""
    box = Box()
    a = prepared_request(video_work(box, org_id=b.ORG_A), prompt_tokens=1200)
    same = prepared_request(video_work(box, org_id=b.ORG_A), prompt_tokens=1200)
    other = prepared_request(video_work(box, org_id=b.ORG_B, refs=(b.media(b.ORG_B),)),
                             prompt_tokens=1200)
    assert cache_salt(a) == cache_salt(same)
    assert cache_salt(a) != cache_salt(other)
    assert cache_salt(a) != cache_salt(a.model_copy(update={"profile_version": "v2"}))
    assert cache_salt(a) != cache_salt(a.model_copy(update={"model_revision": "other@1"}))
    # the tenant's own source digest is part of the namespace
    second_clip = b.media(b.ORG_A, handle="upl_conformancefixture0000000000000000009")
    assert cache_salt(a) != cache_salt(prepared_request(
        video_work(box, org_id=b.ORG_A, refs=(second_clip,)), prompt_tokens=1200))

    # a text-only request has no media digest, so the salt comes from the tenant: two
    # requests from the same organization still share a namespace
    first = prepared_request(text_work(box, org_id=b.ORG_A), prompt_tokens=10)
    again = prepared_request(text_work(box, org_id=b.ORG_A), prompt_tokens=10)
    assert first.request_id != again.request_id
    assert cache_salt(first) == cache_salt(again)
    assert cache_salt(first) != cache_salt(prepared_request(text_work(box, org_id=b.ORG_B),
                                                           prompt_tokens=10))

    # a client cannot name another tenant's namespace
    forged = prepared_request(text_work(box, org_id=b.ORG_A,
                                        parameters={"tenant_salt": b.ORG_B}), prompt_tokens=10)
    assert forged.parameters["tenant_salt"] == b.ORG_A
    assert cache_salt(forged) == cache_salt(
        forged.model_copy(update={"parameters": {"tenant_salt": b.ORG_A}}))

    # with no tenant information at all, nothing is shared with anybody
    bare, bare_too = text_prepared(box), text_prepared(box)
    assert cache_salt(bare) != cache_salt(bare_too)

    # the multimodal uuid is namespaced by the same salt, and is per object
    salt, salt_b = cache_salt(a), cache_salt(other)
    assert media_uuid(a.media[0], salt) != media_uuid(a.media[0], salt_b)
    assert media_uuid(a.media[0], salt) != media_uuid(second_clip, salt)
    upstream, engine, _lease, _prepared = drive()
    assert engine.upstream_body(a)["mm_uuids"] == [media_uuid(a.media[0], salt)]


def test_api_stream__unsupported_options_are_refused_explicitly():
    """01: tools and structured output are refused explicitly, `n>1` too, r1 R45 refuses
    a client-supplied price, and an unknown parameter is refused rather than forwarded to
    a pinned engine that would ignore or honour it unvalidated. Nothing is sent."""
    for name, value in (("tools", [{"type": "function"}]), ("tool_choice", "auto"),
                        ("response_format", {"type": "json_object"}), ("guided_json", {}),
                        ("logprobs", True), ("n", 2), ("price_snapshot", {"price_version": "x"}),
                        ("stream_options", {}), ("who_knows", 1)):
        upstream, engine, held, _prepared = drive()
        prepared = text_prepared(Box(), parameters={name: value})
        with pytest.raises(errors.UnsupportedParameter) as refused:
            asyncio.run(collect(engine.generate(held, prepared)))
        assert refused.value.code == "unsupported_parameter"
        assert refused.value.param == name
        assert upstream.requests == [], name
    # and the supported ones are forwarded untouched (their ranges are G1's to validate)
    upstream, engine, held, _prepared = drive()
    body = engine.upstream_body(text_prepared(Box(), parameters={"temperature": 0.2, "seed": 7,
                                                                "n": 1}))
    assert body["temperature"] == 0.2 and body["seed"] == 7 and body["n"] == 1
    # and what the record consumed (G2 W-new) is neither refused nor forwarded as asked:
    # the ceiling is the record's 256 and the transport is ours
    body = accepted(engine, text_prepared(Box(), parameters={
        "stream": False, "max_tokens": 4096, "max_completion_tokens": 4096}))
    assert isinstance(body, dict), body
    assert body["stream"] is True and body["max_tokens"] == 256
    assert "max_completion_tokens" not in body


def test_api_stream__the_frozen_normalized_request_reaches_the_engine_capped():
    """G2 W-new: the frozen `normalized_request` fixture - what G1R's validator emits, with
    `stream` and `max_tokens` still in `parameters` - is accepted, and the engine is capped
    by the record's `max_output_tokens`, which the validator derived from that `max_tokens`
    (refusing it past `min(MAX_OUTPUT_TOKENS, the deployment's)`). Refusing these keys
    settled every validated streaming or capped request `platform_error`."""
    request = fixtures.model("normalized_request.json")
    assert request.parameters["stream"] is True
    assert request.parameters["max_tokens"] == request.max_output_tokens == 256
    # the upload handle as M's preparation hands it over: a stored, prepared ref
    prepared_refs = (b.media(request.org_id),)
    work = Work(request=request, media_refs=prepared_refs, prepared_refs=prepared_refs,
                price_snapshot=b.DEFAULT_PRICE,
                budgets=Budgets.of(DEFAULTS, request.execution_mode))
    _upstream, engine, _lease, _prepared = drive()
    body = accepted(engine, prepared_request(work, prompt_tokens=1200))
    assert isinstance(body, dict), body
    assert body["max_tokens"] == 256 and body["stream"] is True
    assert body["temperature"] == 0.2 and body["n"] == 1
    # a smaller record ceiling wins over the parameter it was derived from
    capped = request.model_copy(update={"max_output_tokens": 64})
    body = accepted(engine, prepared_request(work.model_copy(update={"request": capped}),
                                             prompt_tokens=1200))
    assert isinstance(body, dict) and body["max_tokens"] == 64, body


def test_api_stream__the_output_ceiling_is_validated_and_enforced():
    """01/R55: the ceiling is validated after preparation, sent to the engine, and
    enforced against it - an engine that streams past the envelope is a platform failure
    (`01`), never an unreserved customer debit. Both bounds are inclusive: an answer of
    exactly the ceiling, and a prompt that exactly fills the context, are legal."""
    upstream, engine, held, _prepared = drive()
    for ceiling in (0, DEFAULTS.max_output_tokens + 1):
        with pytest.raises(errors.InvalidRequest):
            engine.upstream_body(text_prepared(Box(), max_output_tokens=ceiling))
    assert engine.upstream_body(
        text_prepared(Box(), max_output_tokens=DEFAULTS.max_output_tokens,
                      prompt_tokens=0))["max_tokens"] == DEFAULTS.max_output_tokens
    with pytest.raises(errors.ContextLengthExceeded):
        engine.upstream_body(text_prepared(Box(), prompt_tokens=DEFAULTS.max_context_tokens,
                                           max_output_tokens=256))
    exactly_fits = text_prepared(Box(), max_output_tokens=256,
                                 prompt_tokens=DEFAULTS.max_context_tokens - 256)
    fits = accepted(engine, exactly_fits)
    assert isinstance(fits, dict) and fits["max_tokens"] == 256, fits

    # usage exactly at the ceiling is legitimate: it is how `finish_reason=length` ends
    upstream, engine, held, _prepared = drive()
    at_ceiling = text_prepared(Box(), max_output_tokens=len(upstream.deltas()))
    stream = engine.generate(held, at_ceiling)
    events, failure = drained(stream)
    assert failure is None, failure
    assert stream.usage is not None
    assert stream.usage.completion_tokens == at_ceiling.max_output_tokens

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
    assert "".join(raws(events)) == upstream.text == stream.raw_text
    assert len(usages(events)) == stream.usage_events == 1
    usage = usages(events)[0].usage
    assert usage.prompt_tokens == upstream.prompt_tokens
    assert usage.completion_tokens == len(upstream.deltas()) == stream.deltas
    assert usage.certainty.value == "authoritative"
    assert stream.complete and stream.finish_reason == "stop"
    assert stream.terminal_cause is TerminalCause.completed
    assert upstream.requests[0]["messages"] == [{"role": "user",
                                                 "content": "Describe this clip."}]


def test_api_stream__deltas_carry_the_visible_and_raw_text():
    """r1 R58: the delta payload keeps `visible` (what the customer reads) beside `raw`
    (the model's own text, which trace capture records, DEC-05), so the delimiter filter
    runs once, here, and not again in every consumer. Exactly those two keys: the
    transitional `content` alias is gone (F2R item 2, R80)."""
    upstream, engine, held, prepared = drive("split_reasoning_delimiters")
    stream = engine.generate(held, prepared)
    events = asyncio.run(collect(stream))
    raw, visible = "".join(raws(events)), "".join(visibles(events))
    assert raw == "".join(upstream.deltas()) == stream.raw_text
    assert raw.count("<think>") == 1 and "<think>" not in visible
    assert visible == stream.visible_text == filter_text(raw) == "Two people unload boxes."


def test_api_stream__the_filters_final_tail_reaches_the_event_stream():
    """r1 R58: the filter's held tail is the customer's text, so it must be *emitted*,
    not only left in `visible_text`. An answer of `<`, `<th`, ` <thin`, `  \\n` is text
    that merely looks like a delimiter; a streaming consumer that only relays events used
    to receive an empty answer."""
    box = Box()
    for answer, pieces in (("<", ("<",)), ("<th", ("<", "th")), (" <thin", (" <", "thin")),
                           ("  \n", ("  ", "\n")), ("<think>unclosed", ("<think>", "unclosed"))):
        def script(pieces=pieces):
            async def frames():
                for piece in pieces:
                    body = json.dumps({"choices": [{"index": 0, "delta": {"content": piece}}]})
                    yield f"data: {body}\n\n".encode()
                yield b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
                yield (b'data: {"choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2,'
                       b'"total_tokens":5}}\n\n')
                yield b"data: [DONE]\n\n"
            return frames()

        engine = streamed(box, script)
        stream = engine.generate(lease(box), text_prepared(box))
        events = asyncio.run(collect(stream))
        # the whole answer reaches the *events*, not only `visible_text`
        assert "".join(visibles(events)) == filter_text(answer), answer
        assert stream.visible_text == filter_text(answer), answer
        assert "".join(raws(events)) == answer == stream.raw_text, answer
        assert stream.usage is not None, answer
        assert (stream.held_tail != "") is (filter_text(answer) != ""), answer


def test_api_stream__every_chunk_split_reaches_the_customer_through_the_adapter():
    """r1 R58 / API-STREAM, at the *adapter* level: for every way the engine could split
    an answer into deltas, the concatenated `visible` of the **events** is the filtered
    answer and the concatenated `raw` is the answer itself - the held tail included, since
    a streaming consumer relays events and never reads `visible_text`.

    `test_reasoning.py` proves `filter_text` against an independent oracle over 140960
    splits; this composes that result with the event stream.
    """
    box = Box()
    corpus = ("<think>r</think>a", "  <think>r</think>\n a", "<think>unclosed", "<thi",
              "a<think>b</think>c", "  ", "<think></think>x")
    checked = 0
    for answer in corpus:
        # Every split into at most three pieces: a boundary inside each delimiter and on
        # either side of it. One event loop per split, so the exhaustive sweep over every
        # cut combination belongs to the filter's own test, which needs no loop at all.
        for cuts in (0, 1, 2):
            for at in combinations(range(1, len(answer)), cuts):
                bounds = (0, *at, len(answer))
                pieces = [answer[bounds[i]:bounds[i + 1]] for i in range(len(bounds) - 1)]

                def script(pieces=tuple(pieces)):
                    async def frames():
                        for piece in pieces:
                            body = json.dumps({"choices": [{"index": 0,
                                                            "delta": {"content": piece}}]})
                            yield f"data: {body}\n\n".encode()
                        yield (b'data: {"choices":[{"index":0,"delta":{},'
                               b'"finish_reason":"stop"}]}\n\n')
                        yield b"data: [DONE]\n\n"
                    return frames()

                stream = streamed(box, script).generate(lease(box), text_prepared(box))
                events = asyncio.run(collect(stream))
                checked += 1
                assert "".join(raws(events)) == answer, pieces
                assert "".join(visibles(events)) == filter_text(answer), pieces
                assert stream.visible_text == filter_text(answer), pieces
    print(f"\nadapter property: {checked} splits over {len(corpus)} answers")
    assert checked > 500, checked


def test_api_stream__split_tokens_are_reassembled_whatever_the_boundaries():
    """API-STREAM: one character per delta is still the same answer."""
    upstream, engine, held, prepared = drive("split_tokens")
    stream = engine.generate(held, prepared)
    events = asyncio.run(collect(stream))
    assert "".join(raws(events)) == upstream.text
    assert stream.visible_text == upstream.text


def test_api_stream__an_empty_first_delta_is_not_a_token():
    """API-STREAM: vLLM's real first chunk is `{"role": "assistant", "content": ""}`. It
    is liveness, not output: counting it as a delta would inflate the ceiling check and
    emit an empty event for the customer to render."""
    upstream, engine, held, prepared = drive("role_first")
    stream = engine.generate(held, prepared)
    events = asyncio.run(collect(stream))
    assert stream.deltas == len(upstream.deltas())
    assert "" not in raws(events) and "".join(raws(events)) == upstream.text
    assert stream.usage is not None and stream.terminal_cause is TerminalCause.completed


def test_api_stream__usage_is_authoritative_only_when_the_stream_agrees():
    """r1 R58 / DUR-SETTLE: `02` forbids output chunks as a token estimator, so a usage
    block we cannot trust leaves the usage unknown and D reconciles it. Authoritative
    means: the last usage object, arriving after the final content delta, reporting at
    least as many completion tokens as there were nonempty deltas."""
    upstream, engine, held, prepared = drive("missing_usage")
    stream = engine.generate(held, prepared)
    events = asyncio.run(collect(stream))
    assert usages(events) == [] and stream.usage is None and stream.complete
    assert stream.terminal_cause is TerminalCause.engine_incomplete   # no count, no success

    for fault, reason in (("malformed_usage", "malformed"), ("inconsistent_usage", "malformed"),
                          ("valid_then_malformed", "malformed"),
                          ("malformed_then_valid", "malformed"),
                          ("string_usage", "malformed"), ("negative_usage", "malformed"),
                          ("bool_usage", "malformed"), ("nondict_usage", "malformed"),
                          ("usage_then_delta", "delta_after_usage"),
                          ("usage_below_deltas", "below_delta_count"),
                          ("prompt_out_of_range", "out_of_range"),
                          ("conflicting_usage", "conflicting")):
        upstream, engine, held, prepared = drive(fault)
        stream = engine.generate(held, prepared)
        events = asyncio.run(collect(stream))
        assert [event.usage for event in events] == [None] * len(events), fault
        assert len(usages(events)) == stream.usage_events == 1, fault
        assert stream.usage is None, fault
        assert usages(events)[0].payload["certainty"] == "unknown", fault
        assert usages(events)[0].payload["reason"] == reason, fault
        assert stream.terminal_cause is TerminalCause.engine_incomplete, fault

    # the boundary: a reported prompt count of exactly the context limit is a count
    box = Box()
    upstream = FakeUpstream(clock=box.clock, prompt_tokens=DEFAULTS.max_context_tokens)
    stream = upstream.engine().generate(lease(box), text_prepared(box))
    events, failure = drained(stream)
    assert failure is None, failure
    assert stream.usage is not None
    assert stream.usage.prompt_tokens == DEFAULTS.max_context_tokens
    assert stream.terminal_cause is TerminalCause.completed

    # ... and the converse: the *same* usage object twice is one count, not a conflict, and
    # it yields exactly one usage event (r1 R58 as the round-2 review worded it)
    upstream, engine, held, prepared = drive("repeated_usage")
    stream = engine.generate(held, prepared)
    events, failure = drained(stream)
    assert failure is None, failure
    assert len(usages(events)) == stream.usage_events == 1
    assert stream.usage is not None and stream.usage.certainty.value == "authoritative"
    assert stream.usage.completion_tokens == len(upstream.deltas())
    assert stream.terminal_cause is TerminalCause.completed


def test_api_stream__a_finish_reason_outside_the_set_is_not_a_success():
    """R21/R58: `completed` needs **both** halves - a finish reason the engine is entitled to
    end on (`stop` or `length`) *and* authoritative usage. `abort` is neither a customer
    answer nor a length cut-off, so the outcome is `engine_incomplete` however good the
    count looks; only the usage half was asserted before."""
    upstream, engine, held, prepared = drive("abort_finish")
    stream = engine.generate(held, prepared)
    events, failure = drained(stream)
    assert failure is None, failure
    assert stream.finish_reason == "abort" and stream.complete
    assert stream.usage is not None                    # the count itself is fine
    assert stream.terminal_cause is TerminalCause.engine_incomplete
    # and the accepted reason really is accepted
    upstream, engine, held, prepared = drive()
    stream = engine.generate(held, prepared)
    drained(stream)
    assert stream.finish_reason == "stop"
    assert stream.terminal_cause is TerminalCause.completed


def test_api_stream__an_event_always_fits_the_journal_in_any_script():
    """r1 R58 by the **store's own rule** (`StreamStore.event_bytes` =
    `len(compact_bytes(payload))`): a code point costs up to 4 bytes in UTF-8 and up to 6 as
    a JSON escape, and a delta carries its text three times, so sizing by code points held
    only for ASCII - 131072 code points of CJK measured 1.18 MB against a 1 MiB ceiling and
    W2's `append` would have refused an ordinary answer.
    """
    for label, filler in (("ascii", "x"), ("cjk", "日"), ("emoji", "😀"),
                          ("control", "\x01"), ("mixed", "a日😀\x01")):
        for limit in (DEFAULTS.journal_event_max_bytes, 4096, 512):
            tuned = DEFAULTS.replace(journal_event_max_bytes=limit)
            box = Box()
            points = min(limit, 131_072)
            upstream = FakeUpstream(fault="huge_delta", clock=box.clock, limits=tuned,
                                   filler=filler, huge_delta_points=points)
            engine = upstream.engine()
            prepared = text_prepared(box, max_output_tokens=DEFAULTS.max_output_tokens,
                                     prompt_tokens=0)
            stream = engine.generate(lease(box), prepared)
            events, failure = drained(stream)
            assert failure is None, (label, limit, failure)
            sizes = [len(compact_bytes(event.payload)) for event in events]
            assert max(sizes) <= limit, (label, limit, max(sizes))
            assert "".join(raws(events)) == upstream.deltas()[0] == stream.raw_text
            assert "".join(visibles(events)) == stream.visible_text

    # the held tail is split too: it is the whitespace the filter was holding, and 100k
    # spaces is one event too many
    box = Box()
    tuned = DEFAULTS.replace(journal_event_max_bytes=4096)
    upstream = FakeUpstream(fault="held_tail_flood", clock=box.clock, limits=tuned,
                           huge_delta_points=100_000)
    stream = upstream.engine().generate(lease(box), text_prepared(
        box, max_output_tokens=DEFAULTS.max_output_tokens, prompt_tokens=0))
    events, failure = drained(stream)
    assert failure is None, failure
    assert stream.held_tail == " " * 100_000
    assert max(len(compact_bytes(event.payload)) for event in events) <= 4096
    assert "".join(visibles(events)) == " " * 100_000

    # the constant really does cover the payload's own keys and braces
    assert len(compact_bytes(_delta_payload("", ""))) <= PAYLOAD_OVERHEAD_BYTES

    # the smallest ceiling the adapter will accept still holds, with 4-byte code points,
    # and one below it is refused at construction rather than exceeded per delta
    box = Box()
    smallest = DEFAULTS.replace(journal_event_max_bytes=MIN_JOURNAL_EVENT_BYTES)
    upstream = FakeUpstream(fault="huge_delta", clock=box.clock, limits=smallest,
                           filler="😀", huge_delta_points=64)
    stream = upstream.engine().generate(lease(box), text_prepared(
        box, max_output_tokens=DEFAULTS.max_output_tokens, prompt_tokens=0))
    events, failure = drained(stream)
    assert failure is None, failure
    assert max(len(compact_bytes(event.payload)) for event in events) <= MIN_JOURNAL_EVENT_BYTES
    assert "".join(raws(events)) == "😀" * 64
    with pytest.raises(ValueError):
        FakeUpstream(clock=box.clock,
                     limits=DEFAULTS.replace(
                         journal_event_max_bytes=MIN_JOURNAL_EVENT_BYTES - 1)).engine()

    # and a `visible` longer than its own delta (the filter releases held whitespace with
    # the character that decided it) is split on its own account
    box = Box()

    async def held_then_one():
        body = json.dumps({"choices": [{"index": 0, "delta": {"content": " " * 400}}]})
        yield f"data: {body}\n\n".encode()
        body = json.dumps({"choices": [{"index": 0, "delta": {"content": "a"}}]})
        yield f"data: {body}\n\n".encode()
        yield b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        yield (b'data: {"choices":[],"usage":{"prompt_tokens":1,"completion_tokens":2,'
               b'"total_tokens":3}}\n\n')
        yield b"data: [DONE]\n\n"

    small = VllmEngine(httpx.AsyncClient(base_url="http://engine.invalid",
                                         transport=httpx.MockTransport(
                                             lambda request: httpx.Response(200,
                                                                            content=held_then_one()))),
                       served_model=SERVED_MODEL, clock=box.clock,
                       limits=DEFAULTS.replace(journal_event_max_bytes=128))
    stream = small.generate(lease(box), text_prepared(box))
    events, failure = drained(stream)
    assert failure is None, failure
    assert "".join(visibles(events)) == " " * 400 + "a"
    assert max(len(compact_bytes(event.payload)) for event in events) <= 128


def test_api_stream__one_event_and_the_whole_output_are_bounded():
    """r1 R58: one event must fit `JOURNAL_EVENT_MAX_BYTES`, so a huge delta is split
    across events (the filter is boundary-independent, so nothing is lost), and the whole
    accumulated output is bounded, so a runaway engine cannot grow the adapter's memory."""
    # a 4 KiB journal event, so the split is observable inside the output budget
    tuned = DEFAULTS.replace(journal_event_max_bytes=4096)
    box = Box()
    upstream = FakeUpstream(fault="huge_delta", clock=box.clock, limits=tuned,
                           huge_delta_points=4096)
    engine, held, prepared = upstream.engine(), lease(box), text_prepared(box)
    stream = engine.generate(held, prepared)
    events = asyncio.run(collect(stream))
    assert stream.deltas == 1 and len(raws(events)) > 1
    assert "".join(raws(events)) == upstream.deltas()[0] == stream.raw_text
    for event in events:
        assert len(compact_bytes(event.payload)) <= tuned.journal_event_max_bytes

    upstream, engine, held, prepared = drive("runaway_output")
    small = text_prepared(Box(), max_output_tokens=64)
    with pytest.raises(EngineProtocolViolation) as broke:
        asyncio.run(collect(engine.generate(held, small)))
    assert broke.value.terminal_cause is TerminalCause.platform_error


def test_api_stream__a_line_that_never_ends_is_bounded_and_still_checked():
    """r1 R58 / B7: `httpx.aiter_lines()` buffers a stream with no newline in it without
    bound and hands nothing back to the caller until a line completes, so a 200 MiB `data:`
    line was pulled whole (800 MiB peak) and neither the deadline nor the cancellation was
    looked at while it flowed - the client read timeout never fires, because bytes keep
    arriving. The adapter splits lines itself: the pending buffer is capped at one journal
    event, and the checks run once per network chunk."""
    box = Box()
    upstream = FakeUpstream(fault="no_newline_flood", clock=box.clock)
    engine = upstream.engine()
    with pytest.raises(EngineFailure) as broke:
        asyncio.run(collect(engine.generate(lease(box), text_prepared(box))))
    assert isinstance(broke.value, EngineProtocolViolation), type(broke.value)
    assert broke.value.terminal_cause is TerminalCause.platform_error
    # memory is asserted as chunks pulled, not as RSS: the fake had 24 MiB more to give
    assert upstream.chunks_sent <= 3, upstream.chunks_sent
    assert upstream.chunks_sent < upstream.flood_chunks

    # and the deadline is checked per chunk, so a slow flood stops on time
    box = Box()
    upstream = FakeUpstream(fault="slow_flood", clock=box.clock, flood_chunk_bytes=1024,
                           flood_clock_s=30.0)
    held = lease(box, first_token_deadline_at=box.clock.at(100),
                 generation_deadline_at=box.clock.at(100))
    stream = upstream.engine().generate(held, text_prepared(box))
    events, failure = drained(stream)
    assert failure is None, failure
    assert stream.stall == "generation" and stream.terminal_cause is TerminalCause.deadline_exceeded
    elapsed = (box.clock.now() - held.acquired_at).total_seconds()
    # The check runs when a chunk arrives, so the honest bound is one chunk interval past
    # the deadline (120 s here, not 100) - and not the 1,500 s the review measured before.
    assert 100 <= elapsed <= 100 + 2 * 30, elapsed
    assert upstream.chunks_sent <= 6 < upstream.flood_chunks


def test_api_stream__the_splitter_handles_bytes_not_lines():
    """B7's splitter, at the byte level - everything `aiter_lines()` used to do for us:

    * a multi-byte character cut **between chunks** must survive (an incremental decoder;
      decoding each chunk on its own turns CJK and emoji into U+FFFD);
    * a final line with **no trailing newline** is still an event (real servers do this);
    * a legal line longer than 4 KiB straddling two chunks is **not** a cap breach (the cap
      is one journal event, not one buffer's worth);
    * many lines in one chunk cost **one** split pass, not one per line.
    """
    upstream, engine, held, prepared = drive("split_utf8")
    stream = engine.generate(held, prepared)
    events, failure = drained(stream)
    assert failure is None, failure
    assert "".join(raws(events)) == "日本語です😀" and "\ufffd" not in stream.raw_text
    assert stream.complete and stream.usage is not None

    upstream, engine, held, prepared = drive("no_trailing_newline")
    stream = engine.generate(held, prepared)
    events, failure = drained(stream)
    assert failure is None, failure
    assert stream.complete, "the last line was dropped because it had no newline"
    assert stream.terminal_cause is TerminalCause.completed

    upstream, engine, held, prepared = drive("long_legal_line")
    stream = engine.generate(held, prepared)
    events, failure = drained(stream)
    assert failure is None, failure          # 8 KiB in one line is legal: the cap is 1 MiB
    assert "".join(raws(events)) == "z" * 8192 and stream.complete

    upstream, engine, held, prepared = drive("bare_newlines")
    stream = engine.generate(held, prepared)
    events, failure = drained(stream)
    assert failure is None, failure
    assert stream.complete and stream.malformed_lines == 0
    # 50,000 lines arrived in one chunk; the splitter looked at the buffer once for it
    assert stream.split_passes <= upstream.frames_sent, stream.split_passes
    assert stream.split_passes <= 5, stream.split_passes


def test_api_stream__a_chunk_of_whole_frames_plus_a_partial_tail():
    """B12: the ordinary shape of a TCP read - one or two complete frames followed by the
    *beginning* of the next (`data: …\\n\\ndata: {"choi`). The previous case only cut inside a
    line with no newline before it in the same chunk, so a splitter that dropped the tail, or
    handed it on as a complete line, passed.
    """
    upstream, engine, held, prepared = drive("partial_tail")
    stream = engine.generate(held, prepared)
    events, failure = drained(stream)
    assert failure is None, failure
    assert "".join(raws(events)) == "alpha beta gamma", raws(events)
    assert stream.deltas == 3 and stream.malformed_lines == 0
    assert stream.complete and stream.usage is not None
    # one split pass per chunk that contained a newline, not one per line
    assert stream.split_passes <= upstream.frames_sent


def test_api_stream__a_cancel_lands_mid_line():
    """B7 again: the cancel check runs once per **chunk**. Every other case sends chunks that
    contain a newline, which makes per-chunk and per-line indistinguishable - so here the
    cancel arrives while a single long legal line is still being received, and the stream must
    stop before that line ever completes."""
    box = Box()
    upstream = FakeUpstream(fault="slow_flood", clock=box.clock, flood_chunk_bytes=4096,
                           flood_chunks=200)
    engine = upstream.engine()
    held = lease(box)

    async def cancel_on_the_progress_event():
        stream = engine.generate(held, text_prepared(box))
        events = []
        try:
            async for event in stream:
                events.append(event)
                if event.type is ChunkEventType.progress:
                    assert await engine.cancel(held) is True
        except EngineFailure as failure:
            return stream, events, failure
        return stream, events, None

    stream, events, failure = asyncio.run(cancel_on_the_progress_event())
    assert failure is None, failure          # a cancellation is not an error
    assert [event.type for event in events] == [ChunkEventType.progress,
                                                ChunkEventType.usage]
    assert stream.cancelled and raws(events) == []          # no line ever completed
    assert upstream.chunks_sent <= 3 < upstream.flood_chunks, upstream.chunks_sent


def test_api_stream__junk_and_stray_payloads_are_survived_not_relayed():
    """API-STREAM: a line that is not JSON, or not an object, is the engine misbehaving
    and not content: it is counted and never relayed. A well-formed object that carries
    nothing we act on is simply not an event."""
    box = Box()

    async def junk():
        yield b"data: not json at all\n\n"
        yield b"data: [1, 2, 3]\n\n"
        yield b": keepalive\n\n"
        yield b'data: {"choices":[{"index":0,"delta":{"content":"ok"}}]}\n\n'
        yield b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        yield (b'data: {"choices":[],"usage":{"prompt_tokens":1,"completion_tokens":1,'
               b'"total_tokens":2}}\n\n')
        yield b"data: [DONE]\n\n"

    engine = streamed(box, junk)
    stream = engine.generate(lease(box), text_prepared(box))
    events = asyncio.run(collect(stream))
    assert raws(events) == ["ok"] and stream.malformed_lines == 2 and stream.complete

    upstream, engine, held, prepared = drive("stray_object")
    stream = engine.generate(held, prepared)
    events = asyncio.run(collect(stream))
    assert raws(events) == [upstream.deltas()[0]] and stream.complete

    # a byte-order mark before `data:` is a line we cannot place: counted, so it cannot end
    # the stream `completed` (it used to be dropped in silence)
    # the other SSE field names are legal, so they are not counted - only what we cannot
    # place at all is
    upstream, engine, held, prepared = drive("sse_fields")
    stream = engine.generate(held, prepared)
    events, failure = drained(stream)
    assert failure is None, failure
    assert stream.malformed_lines == 0 and raws(events) == [upstream.text[:5]]
    assert stream.terminal_cause is TerminalCause.completed

    upstream, engine, held, prepared = drive("bom_first")
    stream = engine.generate(held, prepared)
    events, failure = drained(stream)
    assert failure is None, failure
    assert raws(events) == [] and stream.malformed_lines == 1
    assert stream.complete and stream.terminal_cause is TerminalCause.engine_incomplete


def test_api_stream__a_dropped_line_is_never_a_billable_success():
    """R21 with the round-2 ruling: a `data:` line we could not read is content we dropped,
    so the stream did not deliver the whole answer and `completed` - the only cause that can
    charge, with authoritative usage - must not be the outcome. Both ways it happens: junk
    where a delta should have been, and one JSON object cut across two lines."""
    box = Box()

    async def junk_then_finish():
        yield b"data: {oops not json\n\n"
        yield b'data: {"choices":[{"index":0,"delta":{"content":"ok"}}]}\n\n'
        yield b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        yield (b'data: {"choices":[],"usage":{"prompt_tokens":1,"completion_tokens":1,'
               b'"total_tokens":2}}\n\n')
        yield b"data: [DONE]\n\n"

    stream = streamed(box, junk_then_finish).generate(lease(box), text_prepared(box))
    events, failure = drained(stream)
    assert failure is None and stream.complete and stream.usage is not None
    assert stream.malformed_lines == 1
    assert stream.terminal_cause is TerminalCause.engine_incomplete

    upstream, engine, held, prepared = drive("split_json")
    stream = engine.generate(held, prepared)
    events, failure = drained(stream)
    assert failure is None, failure
    assert stream.malformed_lines == 2 and raws(events) == []
    assert stream.complete and stream.usage is not None
    assert stream.terminal_cause is TerminalCause.engine_incomplete


def test_api_stream__a_second_choice_is_a_protocol_violation():
    """`n=1` is forced in the body, so a choice at `index: 1` is either another sample or
    another request's: merging it would interleave two answers in one journal."""
    upstream, engine, held, prepared = drive("second_choice")
    with pytest.raises(EngineFailure) as broke:
        asyncio.run(collect(engine.generate(held, prepared)))
    assert isinstance(broke.value, EngineProtocolViolation), type(broke.value)
    assert broke.value.facts["index"] == 1
    assert broke.value.terminal_cause is TerminalCause.platform_error


def test_api_stream__a_prepared_reference_must_be_one_the_store_could_have_made():
    """MEDIA-SEC at the last hop: a `PreparedRequest` can be hand-built, so the adapter
    checks the *shape* of `storage_ref` and its tenant, not just that the message parts were
    rebuilt. Without this, `http://169.254.169.254/…` in a ref reached the engine, which
    would have fetched it from inside our network."""
    upstream, engine, _lease, _prepared = drive()
    prepared = prepared_request(video_work(Box()), prompt_tokens=1200)
    good = prepared.media[0]
    assert refusal(engine, prepared) == "accepted"
    hostile = (
        EVIL,
        "https://videos.example.com/clip.mp4",
        "data:video/mp4;base64,AAAA",
        "file:///etc/passwd",
        "../../etc/passwd",
        "media/../../etc/passwd",
        f"media/{b.ORG_B}/v1/source",                  # another tenant's prefix
        f"media/{b.ORG_A}/../{b.ORG_B}/v1/source",     # traversal *inside* a valid prefix
        f"media/{b.ORG_A}/v1/./source",
        f"media/{b.ORG_A}/v1/.hidden",
        f"media/{b.ORG_A}/v1/source\n",                # `$` matched before a newline
        f"media/{b.ORG_A}/v1/source\nmedia/x/y/z",
        f"media/{good.org_id}",                        # no object under the tenant
        f"media/{good.org_id}/v1/source extra",        # a space is not a key
        "",
    )
    for storage_ref in hostile:
        bad = prepared.model_copy(update={"media": (good.model_copy(
            update={"storage_ref": storage_ref}),)})
        assert refusal(engine, bad) == "refused: not_found", storage_ref
        assert upstream.requests == [], storage_ref
    # and the salt's tenant must be the ref's: a hand-built request carrying org A's salt
    # with org B's ref and key is refused here, not only where the work is translated
    foreign = prepared.model_copy(update={"media": (b.media(b.ORG_B).model_copy(update={
        "storage_ref": f"media/{b.ORG_B}/v1/source"}),)})
    assert refusal(engine, foreign) == "refused: not_found"
    # R61's path check does not stand in for this link: a resolver that answers with the
    # request organization's path whatever the ref says passes the path check, and only
    # the salt/ref link refuses org B's object under org A's namespace
    lenient = m2_local_uri(FAKE_MEDIA_ROOT)
    blind = FakeUpstream(clock=Box().clock).engine(
        local_uri=lambda ref: lenient(ref.model_copy(update={"org_id": b.ORG_A})))
    assert refusal(blind, foreign) == "refused: not_found"
    assert refusal(blind, prepared) == "accepted"

    # the guard is also asserted on its own, because the local-path check (S2M D3) refuses
    # a foreign prefix too: with only the end-to-end assertions above, a `check_storage_ref`
    # that stopped comparing tenants would be masked by the second check.
    check_storage_ref(good)
    for storage_ref in (f"media/{b.ORG_B}/v1/source", "http://169.254.169.254/",
                        f"media/{b.ORG_A}/v1/source\n"):
        with pytest.raises(errors.NotFound):
            check_storage_ref(good.model_copy(update={"storage_ref": storage_ref}))

    # the store's own five-segment key is accepted too, not only the fixture's shorter one
    real = good.model_copy(update={
        "storage_ref": f"media/{good.org_id}/v1/{good.digest.split(':')[1][:16]}/source"})
    assert refusal(engine, prepared.model_copy(update={"media": (real,)})) == "accepted"


def test_api_stream__an_unmeasured_prompt_or_duration_is_refused():
    """A number nothing measured must not become a token budget: `prompt_tokens` outside the
    context is refused where the work is translated, and a prepared video with no duration is
    refused rather than silently given a four-frame budget (`or 0.0`)."""
    box = Box()
    work = video_work(box)
    for count in (10 ** 30, DEFAULTS.max_context_tokens + 1):
        with pytest.raises(errors.InvalidRequest) as refused:
            prepared_request(work, prompt_tokens=count)
        assert refused.value.param == "prompt_tokens", count
    with pytest.raises((errors.InvalidRequest, ValidationError)):
        prepared_request(work, prompt_tokens=-1)         # the record refuses this one too
    assert prepared_request(work, prompt_tokens=DEFAULTS.max_context_tokens).prompt_tokens

    upstream, engine, _lease, _prepared = drive()
    prepared = prepared_request(work, prompt_tokens=1200)
    for duration in (None, float("inf"), float("nan"), float("-inf"), -1.0, 0.0):
        timeless = prepared.model_copy(update={
            "media": (prepared.media[0].model_copy(update={"duration_s": duration}),)})
        assert refusal(engine, timeless) == "refused: unsupported_media", duration


def test_api_stream__the_roles_and_the_timer_boundaries_are_pinned():
    """The two vocabularies a mutant can widen without any case noticing: the allowed roles
    (R58) and the three timer comparisons, which are `>=` - a deadline is reached *at* its
    instant, not a second later."""
    from infrx.worker.engine import ALLOWED_ROLES
    assert ALLOWED_ROLES == ("system", "user", "assistant")

    box = Box()
    engine = FakeUpstream(clock=box.clock).engine()
    held = lease(box, first_token_deadline_at=box.clock.at(60),
                 generation_deadline_at=box.clock.at(300))
    stream = engine.generate(held, text_prepared(box))
    stream.last_event_at = box.clock.now()
    assert engine._overdue(stream, box.clock.now()) is None
    # exactly at the first-token instant, with no delta yet
    assert engine._overdue(stream, box.clock.at(60)) == "first_token"
    assert engine._overdue(stream, box.clock.at(59.999)) is None
    # exactly at the inter-event budget, once a delta has arrived
    stream.deltas = 1
    assert engine._overdue(stream, box.clock.at(DEFAULTS.tpot_stall_s)) == "inter_event"
    assert engine._overdue(stream, box.clock.at(DEFAULTS.tpot_stall_s - 0.001)) is None
    # exactly at the generation instant, which is reported ahead of the others
    assert engine._overdue(stream, box.clock.at(300)) == "generation"
    asyncio.run(stream.aclose())


# --- failure classes ----------------------------------------------------------
def test_api_stream__transport_engine_and_incomplete_failures_are_distinct():
    """W1 acceptance: the adapter distinguishes a transport failure, an engine error
    before and after headers, and output that simply stopped. None of them is a
    `DomainError`, so no route can echo an engine's text at a customer, and the error body
    is read *bounded*: it is a stack trace of unknown size."""
    for fault in ("transport_error", "connect_timeout"):
        upstream, engine, held, prepared = drive(fault)
        with pytest.raises(EngineTransportError) as failed:
            asyncio.run(collect(engine.generate(held, prepared)))
        assert failed.value.facts["stage"] == "pre_headers", fault
        assert not isinstance(failed.value, errors.DomainError)
        assert failed.value.terminal_cause is TerminalCause.engine_error

    upstream, engine, held, prepared = drive("engine_error_pre_headers")
    stream = engine.generate(held, prepared)
    with pytest.raises(EngineFailure) as failed:
        asyncio.run(collect(stream))
    assert isinstance(failed.value, EngineError), type(failed.value)
    assert failed.value.facts == {"stage": "pre_headers", "status": 500}
    assert not stream.started and stream.deltas == 0
    assert len(failed.value.detail) == 500
    assert 0 < upstream.error_bytes <= 2 * ERROR_BODY_CHUNK, "the error body was read whole"

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


def test_api_stream__every_engine_failure_is_typed():
    """r1 R58: nothing untyped escapes `generate`. A null error message, an
    answer-carrying field of the wrong type, content that cannot be serialised and a
    non-httpx exception mid-stream are each an `EngineFailure` with a `TerminalCause` -
    not a `TypeError`, an `AttributeError` or an `OSError` W2 cannot settle."""
    expected = {
        "null_error_message": (EngineError, TerminalCause.engine_error),
        "bad_choices": (EngineProtocolViolation, TerminalCause.platform_error),
        "bad_choice": (EngineProtocolViolation, TerminalCause.platform_error),
        "bad_delta": (EngineProtocolViolation, TerminalCause.platform_error),
        "bad_content": (EngineProtocolViolation, TerminalCause.platform_error),
        "surrogate_delta": (EngineProtocolViolation, TerminalCause.platform_error),
        "os_error": (EngineFailure, TerminalCause.engine_error),
    }
    for fault, (kind, cause) in expected.items():
        upstream, engine, held, prepared = drive(fault)
        with pytest.raises(EngineFailure) as failed:
            asyncio.run(collect(engine.generate(held, prepared)))
        assert isinstance(failed.value, kind), (fault, type(failed.value))
        assert failed.value.terminal_cause is cause, fault
        assert not isinstance(failed.value, errors.DomainError), fault
        assert isinstance(failed.value.detail, str), fault
    # every emitted event serialises, which is what W2's `append` needs
    upstream, engine, held, prepared = drive()
    for event in asyncio.run(collect(engine.generate(held, prepared))):
        assert event.model_dump_json()


# --- timers -------------------------------------------------------------------
def test_api_stream__a_stall_ends_the_stream_and_says_which_bound_it_passed():
    """API-STREAM: the engine simply stops, so the iterator ends rather than raising -
    and the adapter stops **waiting** at the store's first-token instant (r1 R20) and at
    the inter-event budget. The keepalives prove the timer fired: the scripted upstream
    would have gone on sending them for another five minutes."""
    upstream, engine, held, prepared = drive("prefill_stall")
    started = upstream.clock.now()
    stream = engine.generate(held, prepared)
    events, failure = drained(stream)
    assert failure is None, failure          # the engine stopped; that is not an error
    assert [event.type for event in events] == [ChunkEventType.progress]
    assert stream.stall == "first_token" and stream.usage is None and not stream.complete
    assert 0 < upstream.pings < upstream.max_keepalives
    elapsed = (upstream.clock.now() - started).total_seconds()
    assert DEFAULTS.ttft_timeout_s < elapsed < DEFAULTS.generation_timeout_s
    assert stream.terminal_cause is TerminalCause.engine_incomplete

    upstream, engine, held, prepared = drive("midstream_stall")
    started = upstream.clock.now()
    stream = engine.generate(held, prepared)
    events, failure = drained(stream)
    assert failure is None, failure
    assert raws(events) and usages(events) == []
    assert stream.stall == "inter_event" and 0 < upstream.pings < upstream.max_keepalives
    assert (upstream.clock.now() - started).total_seconds() > DEFAULTS.tpot_stall_s

    # a read timeout after the headers is the same stall by another route
    upstream, engine, held, prepared = drive("read_timeout")
    stream = engine.generate(held, prepared)
    events, failure = drained(stream)
    assert failure is None, failure
    assert stream.stall == "inter_event" and raws(events) and usages(events) == []


def test_api_stream__the_generation_deadline_is_its_own_outcome():
    """R20/R21: passing the attempt's absolute deadline is `deadline_exceeded` - the
    platform's own deadline, and a different outcome from a slow engine. It is reported
    ahead of the per-event bound, and a lease already past its instants stops at once."""
    box = Box()
    upstream = FakeUpstream(fault="midstream_stall", clock=box.clock)
    engine = upstream.engine()
    # one delta at t=0, then keepalives every 7 s: the generation bound at 10 s fires
    # before the 20 s inter-event budget
    held = lease(box, first_token_deadline_at=box.clock.at(5),
                 generation_deadline_at=box.clock.at(10))
    stream = engine.generate(held, text_prepared(box))
    events, failure = drained(stream)
    assert failure is None, failure
    assert len(raws(events)) == 1 and stream.stall == "generation"
    assert stream.terminal_cause is TerminalCause.deadline_exceeded
    assert 0 < upstream.pings < upstream.max_keepalives

    box = Box()
    upstream = FakeUpstream(clock=box.clock)
    expired = lease(box, first_token_deadline_at=box.clock.at(-1),
                    generation_deadline_at=box.clock.at(-1))
    stream = upstream.engine().generate(expired, text_prepared(box))
    events = asyncio.run(collect(stream))
    assert [event.type for event in events] == [ChunkEventType.progress]
    assert stream.stall == "generation"
    assert stream.terminal_cause is TerminalCause.deadline_exceeded


def test_api_stream__a_slow_but_steady_stream_is_not_a_stall():
    """API-STREAM: the inter-event budget is measured from the **last event**, so an
    engine that keeps producing under the budget runs to completion however long the whole
    answer takes. Measuring from the start of the stream would cut off every long answer."""
    upstream, engine, held, prepared = drive("slow_deltas")
    started = upstream.clock.now()
    stream = engine.generate(held, prepared)
    events, failure = drained(stream)
    assert failure is None, failure
    assert "".join(raws(events)) == upstream.text
    assert stream.stall is None and stream.complete and stream.usage is not None
    elapsed = (upstream.clock.now() - started).total_seconds()
    assert DEFAULTS.tpot_stall_s < elapsed < DEFAULTS.generation_timeout_s
    assert stream.terminal_cause is TerminalCause.completed


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
            if not cancelled and len(raws(events)) == 2:
                assert await engine.cancel(held) is True
                cancelled = True
        return events

    events = asyncio.run(cancel_after_two())
    assert len(raws(events)) < upstream.long_stream_deltas
    # Two independent measures: the script had 200 deltas to give and the adapter pulled a
    # handful, and httpx called `aclose` on the body when the adapter left its `async with`
    # (the body is a real `AsyncByteStream`, so that call is observable).
    assert upstream.frames_sent < upstream.long_stream_deltas // 4, upstream.frames_sent
    assert upstream.closed and not upstream.completed and stream.upstream_closed
    assert stream.cancelled and len(usages(events)) == 1
    assert usages(events)[0].usage is None and stream.usage is None
    assert usages(events)[0].payload["certainty"] == "unknown"
    assert stream.terminal_cause is TerminalCause.client_cancelled
    assert engine.cancelled == {}                  # the intent does not outlive the stream


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
    assert len(usages(events)) == 1 and raws(events) == []
    assert usages(events)[0].usage.total_tokens == 0
    assert usages(events)[0].usage.certainty.value == "authoritative"
    assert stream.cancelled and engine.cancelled == {}


def test_api_stream__a_cancel_intent_is_never_immortal_and_never_refuses_a_running_lease():
    """The round-3 ruling, clause by clause. The old rule - "only a finished generation may
    be evicted, otherwise False" - made never-consumed intents immortal, so 1,024 of them
    (an ordinary late-cancel race is enough) refused **every** later lease, including a
    generation running at that moment: a regression for the one case cancellation exists to
    serve."""
    box = Box()
    upstream = FakeUpstream(clock=box.clock)
    engine = upstream.engine()

    def leases(count, at=300.0, job=1):
        return [lease(box, job_id=f"{job:08x}-0000-4000-8000-{ordinal:012x}",
                      first_token_deadline_at=box.clock.at(min(at, 60.0)),
                      generation_deadline_at=box.clock.at(at))
                for ordinal in range(count)]

    # (1) a cancel for a generation that has finished is a no-op that stores nothing
    done = lease(box)
    asyncio.run(collect(engine.generate(done, text_prepared(box))))
    assert (done.job_id, done.generation) in engine.finished
    assert asyncio.run(engine.cancel(done)) is True
    assert (done.job_id, done.generation) not in engine.cancelled

    # (2) closing retires the intent even when the generator never started - the reviewer's
    # route: cancel, generate, aclose, over and over, leaves nothing behind
    async def cancel_generate_close(count):
        for held in leases(count, job=2):
            assert await engine.cancel(held) is True
            stream = engine.generate(held, text_prepared(box))
            await stream.aclose()

    asyncio.run(cancel_generate_close(MAX_CANCEL_INTENTS + 200))
    assert engine.cancelled == {}, len(engine.cancelled)
    assert asyncio.run(engine.cancel(lease(box))) is True

    # (3) a full map never refuses a **running** generation, and the cancel still stops it
    box = Box()
    upstream = FakeUpstream(fault="cancellation_race", clock=box.clock)
    engine = upstream.engine()
    for held in leases(MAX_CANCEL_INTENTS, job=3):
        assert asyncio.run(engine.cancel(held)) is True
    assert len(engine.cancelled) == MAX_CANCEL_INTENTS
    assert asyncio.run(engine.cancel(leases(1, job=9)[0])) is False      # (5) full and live

    running = lease(box)

    async def cancel_the_running_one():
        stream = engine.generate(running, text_prepared(box))
        events = []
        async for event in stream:
            events.append(event)
            if len(raws(events)) == 1:
                assert await engine.cancel(running) is True, "a running lease was refused"
        return stream, events

    stream, events = asyncio.run(cancel_the_running_one())
    assert stream.cancelled and len(raws(events)) < upstream.long_stream_deltas
    assert (running.job_id, running.generation) not in engine.cancelled

    # (4) a pre-start intent expires with its lease's generation deadline, so a map full of
    # intents for work that can no longer run is not a full map
    box = Box()
    engine = FakeUpstream(clock=box.clock).engine()
    for held in leases(MAX_CANCEL_INTENTS, at=10.0, job=4):
        assert asyncio.run(engine.cancel(held)) is True
    fresh = leases(1, job=5)[0]
    assert asyncio.run(engine.cancel(fresh)) is False       # still live: refused
    box.clock.advance(10)                                   # exactly at their deadline
    assert asyncio.run(engine.cancel(fresh)) is True, "expiry is not reached at its instant"
    engine.cancelled.clear()
    for held in leases(MAX_CANCEL_INTENTS, at=10.0, job=41):   # deadlines from *now*
        assert asyncio.run(engine.cancel(held)) is True
    assert asyncio.run(engine.cancel(leases(1, job=42)[0])) is False
    box.clock.advance(11)
    assert asyncio.run(engine.cancel(fresh)) is True        # past their deadlines: evictable
    assert len(engine.cancelled) < MAX_CANCEL_INTENTS

    # (a round-4 case built a "finished and running" key to prove the eviction guard; B11
    # makes that state unreachable - a generation runs once - so the guard went with it, and
    # what protects a live intent is the expiry above.)

    # the reviewer's second route: late cancels, an ordinary race, are not immortal
    box = Box()
    upstream = FakeUpstream(clock=box.clock)
    engine = upstream.engine()

    async def finish_then_cancel(count):
        for held in leases(count, job=6):
            await collect(engine.generate(held, text_prepared(box)))
            assert await engine.cancel(held) is True
    asyncio.run(finish_then_cancel(MAX_CANCEL_INTENTS + 1))
    assert engine.cancelled == {}
    assert len(engine.finished) <= MAX_CANCEL_INTENTS, len(engine.finished)
    assert asyncio.run(engine.cancel(lease(box))) is True


def test_api_stream__a_generation_runs_only_once():
    """B11 / r1 R46: one generation is one attempt, so a lease is never executed twice - and
    the adapter refuses to, because the intent map is retired per generation. Both sequences
    the review found lost a cancellation in silence: a cancel between two runs of the same key
    was answered `True` as a no-op (it is over, says clause 1) and the second run then ignored
    it. `state_conflict`, and nothing is sent.
    """
    # sequence 1: attempt 1 dies mid-stream, the job is cancelled, attempt 2 reuses the lease
    box = Box()
    upstream = FakeUpstream(fault="abrupt_exit", clock=box.clock)
    engine = upstream.engine()
    held = lease(box)
    with pytest.raises(EngineTransportError):
        asyncio.run(collect(engine.generate(held, text_prepared(box))))
    assert asyncio.run(engine.cancel(held)) is True
    again = FakeUpstream(clock=box.clock)
    second = VllmEngine(again.client(), served_model=SERVED_MODEL, clock=box.clock)
    # a *different* engine instance has no memory, so the refusal is this engine's business:
    assert asyncio.run(collect(second.generate(held, text_prepared(box))))
    assert outcome_of(engine, held, text_prepared(box)) == "refused: state_conflict"
    assert len(upstream.requests) == 1, "the second attempt was sent"

    # sequence 2: generate, close before the first `__anext__`, generate again, cancel
    box = Box()
    upstream = FakeUpstream(clock=box.clock)
    engine = upstream.engine()
    held = lease(box)

    async def close_then_cancel():
        first = engine.generate(held, text_prepared(box))
        await first.aclose()
        assert await engine.cancel(held) is True

    asyncio.run(close_then_cancel())
    assert outcome_of(engine, held, text_prepared(box)) == "refused: state_conflict"
    assert upstream.requests == []


def test_api_stream__a_cancellation_is_scoped_to_its_generation():
    """r1 R58: intents are keyed by `(job_id, generation)`. Keyed by job alone, a stale
    intent for a fenced attempt would kill the attempt that replaced it - and the set of
    intents for work that never runs is bounded, so a cancel storm is not a leak."""
    box = Box()
    upstream = FakeUpstream(clock=box.clock)
    engine = upstream.engine()
    first = lease(box)
    second = first.model_copy(update={"generation": 2})
    assert asyncio.run(engine.cancel(first)) is True
    stream = engine.generate(second, text_prepared(box))
    asyncio.run(collect(stream))
    assert upstream.requests, "generation 2 was cancelled by generation 1's intent"
    assert not stream.cancelled and stream.complete and stream.usage is not None
    assert set(engine.cancelled) == {(first.job_id, 1)}    # the other intent is untouched

    # the rest of the lifecycle is `…a_cancel_intent_is_never_immortal…` below

    # a consumer that stops reading closes the upstream stream deterministically
    box = Box()
    upstream = FakeUpstream(fault="cancellation_race", clock=box.clock)
    engine = upstream.engine()

    async def stop_after_one():
        stream = engine.generate(lease(box), text_prepared(box))
        async for event in stream:
            if event.type is ChunkEventType.delta:
                break
        await stream.aclose()
        # asserted *inside* the coroutine: the loop finalises abandoned async generators at
        # shutdown, so anything checked after `asyncio.run` returned proves nothing about
        # who stopped the read. `frames_sent` and `upstream_closed` are what do.
        pulled = upstream.frames_sent
        assert stream.upstream_closed, "aclose did not close the response"
        assert upstream.closed, "httpx never closed the body"
        assert not upstream.completed and pulled <= 3, pulled
        with pytest.raises(StopAsyncIteration):
            await stream.__anext__()
        assert upstream.frames_sent == pulled, "the engine was still being read"
        return stream

    closed = asyncio.run(stop_after_one())
    assert closed.cancelled is False and engine.cancelled == {}

    # a generator closed before its first `__anext__` leaves nothing behind either
    box = Box()
    upstream = FakeUpstream(clock=box.clock)
    engine = upstream.engine()
    never = engine.generate(lease(box), text_prepared(box))
    asyncio.run(never.aclose())
    assert upstream.requests == [] and engine.cancelled == {}
