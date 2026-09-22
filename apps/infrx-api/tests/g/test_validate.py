#!/usr/bin/env python3
"""F-BASE / MEDIA-SEC (shape half): the closed parameter set, the derived ceilings
and the deadline `JobStore.admit` will accept.

The last three cases are the ones that matter most: they hand the validator's own
`NormalizedRequest` to the executable specification of the durable store (the
contracts fake) and let *it* decide, so "the ceilings are derived so that admit
accepts them" is proven by admission rather than by a matching constant here.
"""
import asyncio
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from infrx.contracts.conformance import builders as b
from infrx.contracts.fakes import FACTORIES
from infrx.contracts.records import ExecutionMode
from infrx.gateway.routes import validate

from . import support


def client(**pilot):
    calls, accept = support.recorder()
    app, _ = support.cutover_app(support.settings(**pilot),
                                 ingress_deps=support.deps(accept=accept))
    return TestClient(app), calls


def post(body, headers=None, **pilot):
    tc, calls = client(**pilot)
    response = tc.post(support.CHAT_PATH, headers={**support.AUTH, **(headers or {})}, json=body)
    return response, calls


def message(**extra):
    return {"messages": [{"role": "user", "content": "hi"}], **extra}


UNSUPPORTED_BODIES = (
    ("tools", {"tools": [{"type": "function"}]}),
    ("tool_choice", {"tool_choice": "auto"}),
    ("functions", {"functions": []}),
    ("response_format", {"response_format": {"type": "json_schema"}}),
    ("logprobs", {"logprobs": True}),
    # r1 R45: prices come only from the store's own source, so a client that names
    # one is refused explicitly rather than having it quietly ignored.
    ("price_snapshot", {"price_snapshot": {"input_rate_per_million": "0.00"}}),
    ("unknown_field", {"unknown_field": 1}),
)


@pytest.mark.parametrize("param,extra", UNSUPPORTED_BODIES, ids=[n for n, _ in UNSUPPORTED_BODIES])
def test_f_base__an_unsupported_parameter_is_named_and_refused(param, extra):
    response, calls = post(message(**extra))
    assert response.status_code == 400, response.text
    error = support.error_of(response)
    assert (error["code"], error["param"]) == ("unsupported_parameter", param), error
    assert calls == []


def test_f_base__only_n_equals_one_is_supported():
    response, calls = post(message(n=2))
    error = support.error_of(response)
    assert (response.status_code, error["code"], error["param"]) == (400, "unsupported_parameter", "n")
    assert calls == []
    assert post(message(n=1))[0].status_code == 202


def parts(*content):
    return {"messages": [{"role": "user", "content": list(content)}]}


VIDEO = {"type": "video_url", "video_url": {"url": "https://cdn.test/a.mp4"}}

# r1 R58: the allow-list, attacked from every side the reviews have named.
BAD_SHAPES = (
    ("no messages", {}, "invalid_request"),
    ("empty messages", {"messages": []}, "invalid_request"),
    ("message is not an object", {"messages": ["hi"]}, "invalid_request"),
    ("message without content", {"messages": [{"role": "user"}]}, "invalid_request"),
    ("message with an extra key", {"messages": [{"role": "user", "content": "hi", "name": "x"}]},
     "invalid_request"),
    ("message with tool calls", {"messages": [{"role": "user", "content": "hi",
                                               "tool_calls": []}]}, "invalid_request"),
    ("unknown role", {"messages": [{"role": "tool", "content": "x"}]}, "invalid_request"),
    ("empty content array", {"messages": [{"role": "user", "content": []}]}, "invalid_request"),
    ("part is not an object", parts("hi"), "unsupported_media"),
    ("text part without text", parts({"type": "text"}), "invalid_request"),
    ("text part with an extra key", parts({"type": "text", "text": "hi", "image_url": "x"}),
     "invalid_request"),
    ("image part", parts({"type": "image_url", "image_url": "x"}), "unsupported_media"),
    ("audio part", parts({"type": "input_audio", "input_audio": "x"}), "unsupported_media"),
    ("file part", parts({"type": "file", "file": "x"}), "unsupported_media"),
    ("untyped part", parts({"text": "hi"}), "unsupported_media"),
    ("differently cased type", parts({"type": "Text", "text": "hi"}), "unsupported_media"),
    ("the old input_video spelling", parts({"type": "input_video",
                                            "input_video": "https://cdn.test/a.mp4"}),
     "unsupported_media"),
    ("video without a url", parts({"type": "video_url"}), "invalid_request"),
    ("video part with an extra key", parts({**VIDEO, "detail": "high"}), "invalid_request"),
    ("video url as a bare string", parts({"type": "video_url",
                                          "video_url": "https://cdn.test/a.mp4"}),
     "invalid_request"),
    ("video ref with an extra key", parts({"type": "video_url",
                                           "video_url": {"url": "https://cdn.test/a.mp4",
                                                         "mime": "video/mp4"}}),
     "invalid_request"),
    ("file scheme", parts({"type": "video_url", "video_url": {"url": "file:///etc/passwd"}}),
     "unsupported_media"),
    ("two videos", parts(VIDEO, {"type": "video_url",
                                 "video_url": {"url": "https://cdn.test/b.mp4"}}),
     "unsupported_media"),
    ("stream is not a boolean", dict(message(stream="yes")), "invalid_request"),
    ("temperature out of range", dict(message(temperature=5)), "invalid_request"),
    ("temperature is a bool", dict(message(temperature=True)), "invalid_request"),
    ("top_p out of range", dict(message(top_p=1.5)), "invalid_request"),
    ("presence_penalty out of range", dict(message(presence_penalty=3)), "invalid_request"),
    ("frequency_penalty is a string", dict(message(frequency_penalty="1")), "invalid_request"),
    ("seed is not an integer", dict(message(seed="1")), "invalid_request"),
    ("five stop sequences", dict(message(stop=["a", "b", "c", "d", "e"])), "invalid_request"),
    ("an over-long stop sequence", dict(message(stop=["s" * 65])), "invalid_request"),
    ("an empty stop sequence", dict(message(stop=[""])), "invalid_request"),
    ("a non-string stop sequence", dict(message(stop=[1])), "invalid_request"),
    ("empty model", dict(message(model="")), "invalid_request"),
    # Survivors the first review's mutant corpus found: a boolean is not an integer,
    # `null` is not "absent", and a number JSON does not have is not a number.
    ("n is true", dict(message(n=True)), "invalid_request"),
    ("max_tokens is true", dict(message(max_tokens=True)), "invalid_request"),
    ("temperature is null", dict(message(temperature=None)), "invalid_request"),
    ("stop is null", dict(message(stop=None)), "invalid_request"),
    ("seed is negative", dict(message(seed=-1)), "invalid_request"),
    ("seed is 2**63", dict(message(seed=2 ** 63)), "invalid_request"),
    ("a text part's text is null", parts({"type": "text", "text": None}), "invalid_request"),
    ("content is a number", {"messages": [{"role": "user", "content": 5}]}, "invalid_request"),
    ("content is null", {"messages": [{"role": "user", "content": None}]}, "invalid_request"),
    ("a role that cannot be hashed",
     {"messages": [{"role": ["user"], "content": "hi"}]}, "invalid_request"),
    ("a video url of null", parts({"type": "video_url", "video_url": {"url": None}}),
     "invalid_request"),
    ("stop is a number", dict(message(stop=5)), "invalid_request"),
    ("a bare upload handle",
     parts({"type": "video_url", "video_url": {"url": "upl_" + "a" * 43}}), "unsupported_media"),
    ("an upload handle that is not one",
     parts({"type": "video_url", "video_url": {"url": "infrx-upload:upl_short"}}),
     "unsupported_media"),
    ("a data url that is not video base64",
     parts({"type": "video_url", "video_url": {"url": "data:text/plain,hello"}}),
     "unsupported_media"),
    ("a url with a newline in it",
     parts({"type": "video_url", "video_url": {"url": "https://cdn.test/a\nHost: x"}}),
     "unsupported_media"),
    ("an over-long model name", dict(message(model="m" * 129)), "invalid_request"),
    ("stream with respond-async", dict(message(stream=True)), "invalid_request"),
)


@pytest.mark.parametrize("name,body,code", BAD_SHAPES, ids=[n for n, _, _ in BAD_SHAPES])
def test_media_sec__a_malformed_shape_is_refused_with_a_stable_code(name, body, code):
    headers = {"Prefer": "respond-async"} if name == "stream with respond-async" else None
    response, calls = post(body, headers=headers)
    assert response.status_code == 400, response.text
    assert support.error_of(response)["code"] == code, support.error_of(response)
    assert calls == []


def test_media_sec__a_number_json_does_not_have_is_refused():
    """`1e400` parses as `inf`, which no range check catches by comparison and no
    HTTP client will even serialise; it travels as raw JSON text."""
    tc, calls = client()
    for raw in (b'{"messages":[{"role":"user","content":"hi"}],"temperature":1e400}',
                b'{"messages":[{"role":"user","content":"hi"}],"temperature":-1e400}'):
        response = tc.post(support.CHAT_PATH, headers=support.RAW, content=raw)
        assert response.status_code == 400, response.text
        assert support.error_of(response)["code"] == "invalid_request"
    assert calls == []


def test_media_sec__a_megabyte_model_name_never_reaches_the_store():
    """It is refused by the non-media byte bound before the length check even runs -
    which is the point: the first bound that catches it is enough."""
    tc, calls = client()
    response = tc.post(support.CHAT_PATH, headers=support.AUTH,
                       json=dict(message(model="m" * 1_048_576)))
    assert response.status_code in (400, 413), response.text
    assert calls == []


def test_f_base__an_unserved_model_is_refused_without_naming_what_is_served():
    """The public id is mapped to a revision; an unknown one is refused with the
    table's model code, and the message says nothing about which ids exist."""
    response, calls = post(message(model="someone/else"))
    error = support.error_of(response)
    assert (response.status_code, error["code"]) == (403, "model_not_entitled")
    assert error["message"] == "The organization is not entitled to this model."
    assert calls == []


def test_f_base__prefer_is_parsed_as_tokens_not_as_a_substring():
    """`x-no-respond-async` is not `respond-async`, and a parameter is not a token."""
    for prefer, mode in (("x-no-respond-async", ExecutionMode.sync),
                         ("wait=100", ExecutionMode.sync),
                         ("respond-async", ExecutionMode.async_),
                         ("handling=lenient, respond-async", ExecutionMode.async_)):
        tc, calls = client()
        response = tc.post(support.CHAT_PATH, headers={**support.AUTH, "Prefer": prefer},
                           json=message())
        assert response.status_code == 202, (prefer, response.text)
        assert calls[0][1].execution_mode is mode, (prefer, mode)


def test_media_sec__an_owned_upload_handle_and_a_public_url_are_both_accepted():
    for source in ("https://cdn.test/a.mp4", "infrx-upload:upl_" + "a" * 43,
                   "data:video/mp4;base64,AAA"):
        body = parts({"type": "text", "text": "describe"},
                     {"type": "video_url", "video_url": {"url": source}})
        assert post(body)[0].status_code == 202, source


def test_media_sec__accepted_messages_keep_their_parts_in_order():
    """r1 R58 pairs one media part with one staged ref, in order, so the normalized
    messages must preserve the order the caller sent - and carry nothing else."""
    body = parts({"type": "text", "text": "first"}, VIDEO, {"type": "text", "text": "last"})
    tc, calls = client()
    assert tc.post(support.CHAT_PATH, headers=support.AUTH, json=body).status_code == 202
    content = calls[0][1].messages[0]["content"]
    # The texts, not just the kinds: a reversed list of parts has the same kinds here.
    assert content == [{"type": "text", "text": "first"}, VIDEO,
                       {"type": "text", "text": "last"}], content
    assert set(calls[0][1].messages[0]) == {"role", "content"}


OUTPUT_CEILINGS = ((0, 400), (-1, 400), (2_049, 400), (2_048, 202), (1, 202))


@pytest.mark.parametrize("requested,status", OUTPUT_CEILINGS)
def test_f_base__the_output_ceiling_is_range_checked(requested, status):
    response, _ = post(message(max_tokens=requested))
    assert response.status_code == status, response.text
    if status == 400:
        assert support.error_of(response)["param"] == "max_tokens"


def test_f_base__max_tokens_and_max_completion_tokens_must_agree():
    assert post(message(max_tokens=10, max_completion_tokens=20))[0].status_code == 400
    assert post(message(max_tokens=10, max_completion_tokens=10))[0].status_code == 202


def test_f_base__the_derived_ceilings_and_mode_reach_the_acceptor():
    """What `accept` is handed: the tenant from the key, the derived ceilings, the
    execution mode, the canonical payload digest and the idempotency scope."""
    tc, calls = client()
    response = tc.post(support.CHAT_PATH,
                       headers={**support.AUTH, "Idempotency-Key": "idem-1"},
                       json=message(model=support.PUBLIC_MODEL, max_tokens=512))
    assert response.status_code == 202, response.text
    auth, request, idem = calls[0]
    assert (auth.org_id, auth.key_id, auth.principal) == (support.ORG, support.KEY, support.KEY)
    assert (request.org_id, request.key_id) == (support.ORG, support.KEY)
    assert (request.max_output_tokens, request.max_input_tokens) == (512, 32_768 - 512)
    assert request.model_revision == support.MODEL_REVISION == b.MODEL
    assert request.model_revision != support.PUBLIC_MODEL
    assert request.execution_mode is ExecutionMode.sync
    assert request.payload_digest.startswith("sha256:")
    assert (idem.org_id, idem.operation, idem.key) == (support.ORG, "chat.completions", "idem-1")
    assert idem.payload_hash == request.payload_digest
    # r1 R45: nothing the client sent can become a price, and the ingress adds none.
    assert "price_snapshot" not in request.parameters


def test_f_base__the_execution_mode_follows_stream_and_prefer():
    for body, headers, mode in ((message(stream=True), {}, ExecutionMode.stream),
                                (message(), {"Prefer": "respond-async"}, ExecutionMode.async_),
                                (message(), {}, ExecutionMode.sync)):
        tc, calls = client()
        tc.post(support.CHAT_PATH, headers={**support.AUTH, **headers}, json=body)
        assert calls[0][1].execution_mode is mode, (headers, mode)


def test_f_base__an_over_long_idempotency_key_is_refused():
    response, calls = post(message(), headers={"Idempotency-Key": "k" * 256})
    error = support.error_of(response)
    assert (response.status_code, error["param"]) == (400, "Idempotency-Key")
    assert calls == []


# --- the store decides ------------------------------------------------------------
def admitted(body=None, **pilot):
    """Run one ingress request and admit its `NormalizedRequest` into the contracts
    fake, on the fake's clock so both halves agree on what "now" is."""
    harness = FACTORIES["jobstore"]()
    harness.extra["grant"](support.ORG, "1.00")
    tc, calls = client(**pilot)
    response = tc.post(support.CHAT_PATH, headers=support.AUTH,
                       json=body or message(model=support.PUBLIC_MODEL))
    assert response.status_code == 202, response.text
    _auth, request, idem = calls[0]
    # The gateway validated at its own instant; the store decides at the database
    # clock. Moving the fake's clock to the request's instant is what a real store's
    # `now()` would be a moment after admission.
    harness.clock.advance((request.created_at - harness.clock.now()).total_seconds())
    return harness, asyncio.run(harness.port.admit(request, idem))


def test_dur_admit__admit_accepts_the_ingress_ceilings_and_derives_the_hold():
    """r1 R53/R55: the store range-checks the ceilings and computes the hold itself.
    A ceiling pair the ingress derived wrongly is refused here, not here in a
    constant this file also owns."""
    harness, admission = admitted()
    assert admission.state.value == "preparing"
    assert admission.maximum_hold == b.DEFAULT_PRICE.maximum_hold(30_720, 2_048)
    assert harness.extra["balance"](support.ORG)["reserved"] == admission.maximum_hold


def test_dur_admit__the_ingress_deadline_is_one_the_store_can_keep():
    """r1 R29: `admit` clamps a deadline beyond preparation + queue + generation.
    The ingress derives exactly that sum, from the same `Budgets.of`."""
    _harness, admission = admitted()
    assert admission.budgets.queue_wait_s == 10.0            # sync: interactive budget
    assert (admission.deadline_at - admission.admitted_at).total_seconds() == (
        admission.budgets.preparation_s + admission.budgets.queue_wait_s
        + admission.budgets.generation_s)


def test_dur_admit__an_async_request_gets_the_async_queue_budget():
    _harness, admission = admitted({"messages": [{"role": "user", "content": "hi"}],
                                    "model": support.PUBLIC_MODEL, "stream": False})
    assert admission.budgets.queue_wait_s == 10.0
    harness = FACTORIES["jobstore"]()
    harness.extra["grant"](support.ORG, "1.00")
    tc, calls = client()
    tc.post(support.CHAT_PATH, headers={**support.AUTH, "Prefer": "respond-async"},
            json=message(model=support.PUBLIC_MODEL))
    _auth, request, idem = calls[0]
    harness.clock.advance((request.created_at - harness.clock.now()).total_seconds())
    admission = asyncio.run(harness.port.admit(request, idem))
    assert admission.budgets.queue_wait_s == 600.0
    assert (admission.deadline_at - admission.admitted_at).total_seconds() == (
        1_020.0)


# --- survivors the first review named, each now killable ---------------------------
def test_dur_admit__the_payload_digest_is_canonical():
    """The digest is over the canonical re-serialisation, so key order and whitespace
    do not change it and a value does. Idempotent replay is decided by this hash."""
    tc, calls = client()
    reordered = ('{"messages":[{"content":"hi","role":"user"}],"model":"%s","temperature":0.2}'
                 % support.PUBLIC_MODEL).encode()
    spaced = ('{ "model" : "%s" ,\n "temperature" : 0.2 , "messages" : '
              '[ { "role" : "user" , "content" : "hi" } ] }' % support.PUBLIC_MODEL).encode()
    changed = ('{"messages":[{"content":"HI","role":"user"}],"model":"%s","temperature":0.2}'
               % support.PUBLIC_MODEL).encode()
    for raw in (reordered, spaced, changed):
        assert tc.post(support.CHAT_PATH, headers=support.RAW, content=raw).status_code == 202
    first, second, third = [call[1].payload_digest for call in calls]
    assert first == second, "key order or whitespace changed the digest"
    assert first != third, "a changed value did not change the digest"


def test_trace_tenant__the_trace_policy_defaults_to_off():
    """Privacy-critical and previously untested: with no consent source wired, an
    accepted request captures nothing - no trace row, no content, no evaluation."""
    tc, calls = client()
    assert tc.post(support.CHAT_PATH, headers=support.AUTH, json=message()).status_code == 202
    policy = calls[0][1].trace_policy
    assert policy.trace_mode.value == "off"
    assert policy.evaluation_consent is False
    assert policy.org_id == support.ORG


def test_trace_tenant__an_injected_consent_source_is_used_as_given():
    from infrx.contracts.records import ConsentSnapshot, TraceMode

    def consent_for(org_id, now):
        return ConsentSnapshot(org_id=org_id, consent_version=3, trace_mode=TraceMode.full,
                               content_retention_days=30, evaluation_consent=True,
                               effective_at=now)

    calls, accept = support.recorder()
    app, _ = support.cutover_app(ingress_deps=support.deps(accept=accept,
                                                          consent_for=consent_for))
    assert TestClient(app).post(support.CHAT_PATH, headers=support.AUTH,
                                json=message()).status_code == 202
    assert calls[0][1].trace_policy.trace_mode.value == "full"


def test_f_base__there_is_no_second_clock():
    """`created_at` comes from the app's injected clock, so a test can place a request
    anywhere in time and nothing reads the wall clock behind its back."""
    fixed = 1_700_000_000.0
    calls, accept = support.recorder()
    app, _ = support.cutover_app(clock=lambda: fixed,
                                 ingress_deps=support.deps(accept=accept))
    assert TestClient(app).post(support.CHAT_PATH, headers=support.AUTH,
                                json=message()).status_code == 202
    request = calls[0][1]
    assert request.created_at.timestamp() == fixed
    assert request.created_at.tzinfo is not None
    assert (request.deadline_at - request.created_at).total_seconds() == (
        120.0 + 10.0 + 300.0)


SKEWS = (0.0, 0.001, 1.999, 3.0, -5.0)


@pytest.mark.parametrize("behind_s", SKEWS)
def test_dur_admit__the_deadline_survives_clock_skew(behind_s):
    """R-3: a future request is accepted, bounded by the database clock and caller."""
    harness = FACTORIES["jobstore"]()
    harness.extra["grant"](support.ORG, "1.00")
    tc, calls = client()
    assert tc.post(support.CHAT_PATH, headers=support.AUTH,
                   json=message(model=support.PUBLIC_MODEL)).status_code == 202
    request = calls[0][1]
    harness.clock.advance((request.created_at - harness.clock.now()).total_seconds() - behind_s)
    admission = asyncio.run(harness.port.admit(request, calls[0][2]))
    assert admission.state.value == "preparing"
    assert admission.deadline_at == min(
        request.deadline_at,
        harness.clock.now() + timedelta(seconds=b.default_deadline_s(request.execution_mode)),
    )



def test_media_sec__a_backslash_alone_is_enough_to_refuse_a_url():
    """Without a userinfo `@`: a backslash is a path separator to some clients and not
    to others, which is how a validated host stops being the host that is fetched."""
    response, calls = post(parts({"type": "video_url",
                                  "video_url": {"url": "https://cdn.test" + chr(92) + "evil/a"}}))
    error = support.error_of(response)
    assert (response.status_code, error["code"]) == (400, "unsupported_media")
    assert calls == []


def test_f_base__parameters_carry_the_closed_set_and_nothing_else():
    """`messages` and `model` are record fields, not parameters; everything else a
    caller may send travels in `parameters`, and nothing else does."""
    tc, calls = client()
    body = message(model=support.PUBLIC_MODEL, temperature=0.4, stream=False, n=1, seed=7)
    assert tc.post(support.CHAT_PATH, headers=support.AUTH, json=body).status_code == 202
    parameters = calls[0][1].parameters
    assert set(parameters) == {"temperature", "stream", "n", "seed"}, parameters
    assert "messages" not in parameters and "model" not in parameters
    assert set(parameters) <= validate.SUPPORTED


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and not hasattr(fn, "pytestmark"):
            fn()
            print("ok", name)


# --- review r2 B3: claims the round-2 evidence made that no case covered ------------
def test_f_base__max_completion_tokens_alone_is_the_output_ceiling():
    """The round-2 evidence claimed this was covered; no test sent it alone, and the
    mutant that ignores it survived."""
    tc, calls = client()
    assert tc.post(support.CHAT_PATH, headers=support.AUTH,
                   json=message(max_completion_tokens=512)).status_code == 202
    request = calls[0][1]
    assert (request.max_output_tokens, request.max_input_tokens) == (512, 32_768 - 512)
    for bad in (0, 2_049, -1):
        response = tc.post(support.CHAT_PATH, headers=support.AUTH,
                           json=message(max_completion_tokens=bad))
        assert response.status_code == 400, (bad, response.text[:120])
    assert len(calls) == 1


def test_f_base__an_omitted_model_goes_through_the_served_map():
    """The default is mapped like any other public id, not copied through."""
    tc, calls = client()
    assert tc.post(support.CHAT_PATH, headers=support.AUTH,
                   json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 202
    assert calls[0][1].model_revision == support.MODEL_REVISION
    assert calls[0][1].model_revision != support.settings().model_id


def test_media_sec__an_idempotency_key_is_storable_text():
    """It is persisted, so it is held to the same rule as any other stored text."""
    tc, calls = client()
    # A NUL is what an HTTP header can carry and PostgreSQL cannot store; a lone
    # surrogate cannot survive a header at all, so the NUL is the reachable case.
    response = tc.post(support.CHAT_PATH,
                       headers={**support.AUTH, "Idempotency-Key": "k\x00ey"},
                       json=message())
    assert response.status_code == 400, response.text[:160]
    assert support.error_of(response)["param"] == "Idempotency-Key"
    assert calls == []


def test_f_base__the_content_type_and_prefer_checks_are_case_insensitive():
    tc, calls = client()
    assert tc.post(support.CHAT_PATH,
                   headers={**support.AUTH, "content-type": "APPLICATION/JSON"},
                   content=json.dumps(message()).encode()).status_code == 202
    assert tc.post(support.CHAT_PATH, headers={**support.AUTH, "Prefer": "RESPOND-ASYNC"},
                   json=message()).status_code == 202
    assert calls[1][1].execution_mode is ExecutionMode.async_
    # and a prefix match is not a media type
    assert tc.post(support.CHAT_PATH, headers={**support.AUTH, "content-type": "application/jsonx"},
                   content=json.dumps(message()).encode()).status_code == 400


def test_f_base__the_content_type_is_checked_before_the_body_is_read():
    """Cheapest refusal first: a wrong content type costs no buffering."""
    calls, accept = support.recorder()
    app, _ = support.cutover_app(ingress_deps=support.deps(accept=accept))
    read = []

    async def receive():
        read.append(1)
        return {"type": "http.request", "body": b"{}", "more_body": False}

    sent = []

    asyncio.run(app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                     "method": "POST", "path": support.CHAT_PATH,
                     "raw_path": support.CHAT_PATH.encode(), "query_string": b"", "root_path": "",
                     "scheme": "http", "client": ("127.0.0.1", 1), "server": ("t", 80),
                     "headers": [(b"authorization", f"Bearer {support.TOKEN}".encode()),
                                 (b"content-type", b"text/plain")]},
                    receive, lambda message: sent.append(message) or asyncio.sleep(0)))
    assert sent[0]["status"] == 400, sent[0]
    assert read == [], "the body was read for a body we were never going to parse"


def test_media_sec__an_upload_handle_is_anchored_and_exact():
    """`fullmatch`, not a prefix: a handle with anything after it is not the handle."""
    tc, calls = client()
    good = "infrx-upload:upl_" + "a" * 43
    assert tc.post(support.CHAT_PATH, headers=support.AUTH, json=parts(
        {"type": "video_url", "video_url": {"url": good}})).status_code == 202
    for bad in (good + "/../other", good + "?x=1", "infrx-upload:", "infrx-upload:upl_"):
        response = tc.post(support.CHAT_PATH, headers=support.AUTH, json=parts(
            {"type": "video_url", "video_url": {"url": bad}}))
        assert response.status_code == 400, (bad, response.text[:120])
    assert len(calls) == 1


def test_media_sec__an_inline_video_must_be_a_supported_video_type():
    tc, calls = client()
    assert tc.post(support.CHAT_PATH, headers=support.AUTH, json=parts(
        {"type": "video_url", "video_url": {"url": "data:VIDEO/MP4;base64,AAAA"}}
    )).status_code == 202, "the media type is compared case-insensitively"
    for bad in ("data:text/html;base64,AAAA", "data:image/png;base64,AAAA",
                "data:video/mp4;base64,", "data:video/mp4,AAAA"):
        response = tc.post(support.CHAT_PATH, headers=support.AUTH, json=parts(
            {"type": "video_url", "video_url": {"url": bad}}))
        error = support.error_of(response)
        assert (response.status_code, error["code"]) == (400, "unsupported_media"), bad
    assert len(calls) == 1


UNSAFE_URLS = ("https://cdn.test/a\rHost: x", "https://cdn.test/a\x85b",
               "https://cdn.test/a b", "https://cdn.test/a b",
               "https://user:pass@cdn.test/a", "https://cdn.test\\@evil.test/a")


@pytest.mark.parametrize("url", UNSAFE_URLS)
def test_media_sec__a_reference_url_carries_no_smuggling_characters(url):
    response, calls = post(parts({"type": "video_url", "video_url": {"url": url}}))
    error = support.error_of(response)
    assert (response.status_code, error["code"]) == (400, "unsupported_media"), url
    assert calls == []


def test_f_base__an_empty_idempotency_key_is_refused_and_absence_is_not():
    tc, calls = client()
    assert tc.post(support.CHAT_PATH, headers={**support.AUTH, "Idempotency-Key": ""},
                   json=message()).status_code == 400
    assert tc.post(support.CHAT_PATH, headers=support.AUTH, json=message()).status_code == 202
    assert calls[0][2].key is None


def test_f_base__top_p_and_the_closed_set_are_exact_at_the_edges():
    tc, calls = client()
    for value, status in ((0.0, 202), (1.0, 202), (1.0000001, 400)):
        assert tc.post(support.CHAT_PATH, headers=support.AUTH,
                       json=message(top_p=value)).status_code == status, value
    # and a name one character away from a supported one is still unsupported
    assert tc.post(support.CHAT_PATH, headers=support.AUTH,
                   json=message(temperatures=0.5)).status_code == 400
    assert len(calls) == 2
