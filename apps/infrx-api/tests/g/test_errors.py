#!/usr/bin/env python3
"""Every public error is the fixed envelope, and nothing else.

The leak check is deliberately blunt: the whole response - body and headers - is
searched for the things that must never be in it, for every failure the matrix can
produce. A message that "looks safe" is not the test; the absence of the secret is.
"""
import asyncio

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
    return tc.post(support.CHAT_PATH, headers=support.AUTH if auth else {},
                   content=content, json=json), calls


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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and not hasattr(fn, "pytestmark"):
            fn()
            print("ok", name)
