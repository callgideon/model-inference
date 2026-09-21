#!/usr/bin/env python3
"""MEDIA-SEC (intake half): the body is bounded and deadlined before it is parsed.

No sleeps and no wall clock: the intake deadline is exercised with
`INTAKE_TIMEOUT_S=0`, which is already past when the read starts, so the 504 is
deterministic rather than timed.
"""
from fastapi.testclient import TestClient

from infrx.contracts.limits import DEFAULTS

from . import support


def client(clock=None, **pilot):
    calls, accept = support.recorder()
    app, _ = support.cutover_app(support.settings(**pilot), clock=clock,
                                 ingress_deps=support.deps(accept=accept))
    return TestClient(app), calls


def test_media_sec__an_oversized_body_is_refused_before_it_is_parsed():
    """413 `request_too_large`, and *not* 400: a body over the cap is never parsed,
    so an oversized body that is also invalid JSON still answers 413."""
    tc, calls = client(max_request_bytes=32)
    response = tc.post(support.CHAT_PATH, headers=support.AUTH,
                       content=b"{not json at all" + b"x" * 64)
    assert response.status_code == 413, response.text
    assert support.error_of(response)["code"] == "request_too_large"
    assert calls == []


def test_media_sec__a_chunked_body_is_bounded_by_the_running_total():
    """A body that declares no length is bounded by what actually arrives."""
    tc, calls = client(max_request_bytes=16)

    def drip():
        for _ in range(8):
            yield b"0123456789"

    response = tc.post(support.CHAT_PATH, headers=support.AUTH, content=drip())
    assert response.status_code == 413, response.text
    assert calls == []


def test_media_sec__a_body_within_the_cap_is_read_whole():
    tc, calls = client(max_request_bytes=DEFAULTS.max_request_bytes)
    assert tc.post(support.CHAT_PATH, headers=support.AUTH, json=support.BODY).status_code == 202
    assert len(calls) == 1


def test_media_sec__a_slow_body_hits_the_intake_deadline():
    """Time moves because the test moves it: the drip advances the injected clock
    past `INTAKE_TIMEOUT_S`, and the read refuses the chunk that arrives too late."""
    now = [1_790_000_000.0]
    tc, calls = client(clock=lambda: now[0], intake_timeout_s=30)

    def drip():
        for _ in range(4):
            now[0] += 20            # 20s per chunk: the second one is past 30s
            yield b'{"messages":[]}'

    response = tc.post(support.CHAT_PATH, headers=support.AUTH, content=drip())
    assert response.status_code == 504, response.text
    assert support.error_of(response)["code"] == "deadline_exceeded"
    assert calls == []


def test_media_sec__a_peer_that_sends_nothing_hits_the_intake_deadline():
    """The other half of the deadline: no chunk ever arrives, so only the read's own
    timeout can end it. Driven as raw ASGI because a test client always has a body."""
    import asyncio

    calls, accept = support.recorder()
    app, _ = support.cutover_app(support.settings(intake_timeout_s=0),
                                 ingress_deps=support.deps(accept=accept))
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
             "path": support.CHAT_PATH, "raw_path": support.CHAT_PATH.encode(), "query_string": b"",
             "root_path": "", "scheme": "http", "client": ("127.0.0.1", 1), "server": ("t", 80),
             "headers": [(b"authorization", f"Bearer {support.TOKEN}".encode()),
                         (b"content-type", b"application/json")]}
    sent = []

    async def receive():
        await asyncio.Event().wait()            # a peer that never sends its body

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    assert sent[0]["status"] == 504, sent
    assert b"deadline_exceeded" in sent[1]["body"]
    assert calls == []


def test_media_sec__malformed_json_is_a_400_with_no_parser_text():
    tc, _ = client()
    response = tc.post(support.CHAT_PATH, headers=support.AUTH, content=b'{"messages": [}')
    assert response.status_code == 400, response.text
    error = support.error_of(response)
    assert error["code"] == "invalid_request"
    assert error["message"] == "The request is not valid."


def test_media_sec__a_non_object_body_is_refused():
    tc, _ = client()
    response = tc.post(support.CHAT_PATH, headers=support.AUTH, content=b"[1, 2, 3]")
    assert response.status_code == 400
    assert support.error_of(response)["code"] == "invalid_request"


def test_dur_rls__identity_is_checked_before_the_body_is_parsed():
    """Malformed JSON with no key answers 401, not 400: an unauthenticated caller
    cannot use the parser as an oracle, and the parse happens for a known tenant."""
    tc, _ = client()
    response = tc.post(support.CHAT_PATH, content=b"{not json")
    assert response.status_code == 401, response.text
    assert support.error_of(response)["code"] == "invalid_api_key"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
