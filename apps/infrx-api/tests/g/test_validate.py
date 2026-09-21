#!/usr/bin/env python3
"""F-BASE / MEDIA-SEC (shape half): the closed parameter set, the derived ceilings
and the deadline `JobStore.admit` will accept.

The last three cases are the ones that matter most: they hand the validator's own
`NormalizedRequest` to the executable specification of the durable store (the
contracts fake) and let *it* decide, so "the ceilings are derived so that admit
accepts them" is proven by admission rather than by a matching constant here.
"""
import asyncio

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
)


@pytest.mark.parametrize("name,body,code", BAD_SHAPES, ids=[n for n, _, _ in BAD_SHAPES])
def test_media_sec__a_malformed_shape_is_refused_with_a_stable_code(name, body, code):
    response, calls = post(body)
    assert response.status_code == 400, response.text
    assert support.error_of(response)["code"] == code, support.error_of(response)
    assert calls == []


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
    """r1 R29: `admit` refuses a deadline beyond preparation + queue + generation.
    The ingress derives exactly that sum, from the same `Budgets.of`."""
    _harness, admission = admitted()
    assert admission.budgets.queue_wait_s == 10.0            # sync: interactive budget
    assert (admission.deadline_at - admission.admitted_at).total_seconds() == (
        admission.budgets.preparation_s + admission.budgets.queue_wait_s
        + admission.budgets.generation_s - validate.DEADLINE_SKEW_MARGIN_S)


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
        1_020.0 - validate.DEADLINE_SKEW_MARGIN_S)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and not hasattr(fn, "pytestmark"):
            fn()
            print("ok", name)
