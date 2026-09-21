#!/usr/bin/env python3
"""Every public error is the fixed envelope, and nothing else.

The leak check is deliberately blunt: the whole response - body and headers - is
searched for the things that must never be in it, for every failure the matrix can
produce. A message that "looks safe" is not the test; the absence of the secret is.
"""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from infrx.contracts import errors, wire

from . import support

# What must never appear in a public error, whatever went wrong upstream.
FORBIDDEN = ("supabase", "postgresql", "Traceback", "service-role", support.TOKEN,
             "fake.supabase.co", "/rest/v1", "Expecting", "usage.jsonl", "Error(")

MATRIX = (
    ("oversized", dict(content=b"x" * 64, pilot={"max_request_bytes": 8}), 413,
     "request_too_large"),
    ("malformed", dict(content=b"{oops"), 400, "invalid_request"),
    ("unsupported", dict(json={"messages": [{"role": "user", "content": "hi"}], "tools": []}),
     400, "unsupported_parameter"),
    ("unauthenticated", dict(json=support.BODY, auth=False), 401, "invalid_api_key"),
    ("identity source down", dict(json=support.BODY, down=True), 503, "dependency_unavailable"),
    ("no acceptor", dict(json=support.BODY, accept=False), 503, "dependency_unavailable"),
    ("acceptor explodes", dict(json=support.BODY, boom=True), 500, "internal_error"),
)


def call(content=None, json=None, *, auth=True, down=False, accept=True, boom=False, pilot=None):
    async def explode(*_a):
        raise RuntimeError("connection to postgresql://infrx:service-role@db/infrx failed")

    calls, recording = support.recorder()
    deps = support.deps(accept=explode if boom else (recording if accept else None))
    app, _ = support.cutover_app(support.settings(**(pilot or {})),
                                 sb=support.supabase(down=down), ingress_deps=deps)
    tc = TestClient(app)
    headers = (support.RAW if content is not None else support.AUTH) if auth else {}
    return tc.post(support.CHAT_PATH, headers=headers, content=content, json=json), calls


@pytest.mark.parametrize("name,kw,status,code", MATRIX, ids=[row[0] for row in MATRIX])
def test_f_base__every_failure_is_a_fixed_envelope_with_no_leak(name, kw, status, code):
    response, calls = call(**kw)
    assert response.status_code == status, response.text
    error = support.error_of(response)
    assert error["code"] == code
    assert error["type"] == errors.error_type(code)
    # The message is the table's fixed string, not a description of what happened.
    assert error["message"] == errors.MESSAGES[code]
    assert error["request_id"] == response.headers[wire.HEADER_INFERENCE_ID]
    body = response.text + repr(dict(response.headers))
    for secret in FORBIDDEN:
        assert secret not in body, (secret, body)
    assert calls == [] or name == "acceptor explodes"


def test_f_base__the_envelope_carries_only_the_contract_fields():
    response, _ = call(content=b"{oops")
    assert set(response.json()) == {"error"}
    assert set(support.error_of(response)) <= {"message", "type", "code", "param", "request_id",
                                              "infrx"}


def test_f_base__retry_guidance_rides_with_every_429_and_503():
    for kw in ({"json": support.BODY, "down": True}, {"json": support.BODY, "accept": False}):
        response, _ = call(**kw)
        assert response.status_code == 503
        assert int(response.headers[wire.HEADER_RETRY_AFTER]) > 0
        assert support.error_of(response)["infrx"]["retry_after_s"] > 0


def test_f_base__every_answer_carries_a_freshly_minted_inference_id():
    minted = []
    calls, accept = support.recorder()
    app, _ = support.cutover_app(ingress_deps=support.deps(
        accept=accept, new_request_id=lambda: minted.append(f"{len(minted):08x}-0000-4000-8000-"
                                                            f"{len(minted):012x}") or minted[-1]))
    tc = TestClient(app)
    first = tc.post(support.CHAT_PATH, headers=support.AUTH, content=b"{oops")
    second = tc.post(support.CHAT_PATH, headers=support.AUTH, json=support.BODY)
    assert first.headers[wire.HEADER_INFERENCE_ID] == minted[0]
    assert second.headers[wire.HEADER_INFERENCE_ID] == minted[1]
    assert calls[0][1].request_id == minted[1]          # the job identity is the same id


def test_f_base__an_unexpected_exception_never_reaches_the_client():
    """The one case that decides whether a leak is possible at all: the acceptor
    raises a message containing a DSN and a service-role key."""
    response, _ = call(json=support.BODY, boom=True)
    assert response.status_code == 500
    assert support.error_of(response)["message"] == errors.MESSAGES["internal_error"]
    assert "postgresql" not in response.text and "service-role" not in response.text


# --- review r1 items 2 and 4: the error path itself --------------------------------
def test_f_base__an_unrenderable_param_is_still_an_envelope():
    """A parameter name that cannot be encoded used to make the guard raise and the
    client got a bare text/plain 500 with no request id."""
    tc, calls = client_app()
    response = tc.post(support.CHAT_PATH, headers=support.RAW,
                       content=rb'{"messages":[{"role":"user","content":"hi"}],"\ud800":1}')
    assert response.status_code == 400, response.text[:200]
    assert response.headers["content-type"].startswith("application/json")
    error = support.error_of(response)
    assert error["code"] == "unsupported_parameter"
    assert "param" not in error, error          # unrenderable, so not echoed at all
    assert error["request_id"] == response.headers[wire.HEADER_INFERENCE_ID]
    assert calls == []


def test_f_base__a_caller_string_is_never_reflected_or_logged(caplog):
    """A 1 MiB parameter name produced a 1 MiB response and a forged log line. Now the
    name is dropped from the envelope and no caller text reaches the log."""
    name = "x" * 100_000 + "\nJan 01 00:00:00 infrx[1]: forged"
    tc, calls = client_app()
    with caplog.at_level("INFO", logger="infrx.gateway"):
        response = tc.post(support.CHAT_PATH, headers=support.AUTH,
                           json={"messages": [{"role": "user", "content": "hi"}], name: 1})
    assert response.status_code == 400
    assert len(response.content) < 1_024, len(response.content)
    assert "forged" not in response.text
    assert "forged" not in caplog.text and "xxxx" not in caplog.text
    assert calls == []


def test_f_base__a_parameter_name_that_is_a_name_is_still_echoed():
    tc, _ = client_app()
    response = tc.post(support.CHAT_PATH, headers=support.AUTH,
                       json={"messages": [{"role": "user", "content": "hi"}], "tools": []})
    assert support.error_of(response)["param"] == "tools"


def test_f_base__an_internal_only_code_escaping_a_route_is_a_500_envelope():
    """`stale_lease` has no HTTP status by design, so `http_status` raises. An
    acceptor leaking one must not turn the error path into a crash."""
    async def leak(*_a):
        raise errors.StaleLease("the lease is stale")

    calls, _accept = support.recorder()
    app, _ = support.cutover_app(ingress_deps=support.deps(accept=leak))
    response = TestClient(app).post(support.CHAT_PATH, headers=support.AUTH, json=support.BODY)
    assert response.status_code == 500, response.text
    error = support.error_of(response)
    assert error["code"] == "internal_error"
    assert error["request_id"] == response.headers[wire.HEADER_INFERENCE_ID]


def test_f_base__a_body_that_is_not_json_is_refused():
    tc, calls = client_app()
    response = tc.post(support.CHAT_PATH,
                       headers={**support.AUTH, "content-type": "text/plain"},
                       content=b'{"messages":[{"role":"user","content":"hi"}]}')
    error = support.error_of(response)
    assert (response.status_code, error["code"], error["param"]) == (400, "invalid_request",
                                                                     "Content-Type")
    assert calls == []
    # a charset parameter is still application/json
    assert tc.post(support.CHAT_PATH,
                   headers={**support.AUTH, "content-type": "application/json; charset=utf-8"},
                   content=b'{"messages":[{"role":"user","content":"hi"}]}').status_code == 202


def client_app():
    calls, accept = support.recorder()
    app, _ = support.cutover_app(ingress_deps=support.deps(accept=accept))
    return TestClient(app), calls


def test_f_base__the_envelope_of_last_resort():
    """The guard's guard. An error carrying something unserialisable used to raise out
    of the error path; now the client gets the constant envelope and a request id."""
    from infrx.gateway.routes import intake

    unserialisable = errors.InvalidRequest("x", infrx={"probe": object()})
    response = intake.response(unserialisable, support.REQUEST_ID)
    assert response.status_code == 500
    assert response.headers[wire.HEADER_INFERENCE_ID] == support.REQUEST_ID
    # rebuilt, so it names the request and carries no stale Retry-After
    assert json.loads(response.body) == {"error": {**intake.LAST_RESORT["error"],
                                                  "request_id": support.REQUEST_ID}}
    assert "retry-after" not in {key.lower() for key in response.headers}


def test_f_base__a_parameter_name_of_65_characters_is_not_echoed():
    """The echo bound is exact: 64 characters is a parameter name, 65 is a payload."""
    tc, calls = client_app()
    for length, echoed in ((64, True), (65, False)):
        name = "p" * length
        response = tc.post(support.CHAT_PATH, headers=support.AUTH,
                           json={"messages": [{"role": "user", "content": "hi"}], name: 1})
        assert response.status_code == 400
        error = support.error_of(response)
        assert ("param" in error) is echoed, (length, error)
        if echoed:
            assert error["param"] == name
    assert calls == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and not hasattr(fn, "pytestmark"):
            fn()
            print("ok", name)
