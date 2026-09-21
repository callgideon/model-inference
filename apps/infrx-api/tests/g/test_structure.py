#!/usr/bin/env python3
"""MEDIA-SEC (structure half): what a body may contain inside the byte cap.

The first review's finding, in one sentence: 96 MiB of JSON is 3.4 million empty
messages, and building records from them blocked the event loop for 21.8 s and grew
the process by 2.5 GiB — one authenticated free key stalling every other request in
the process. These cases are the bounds that make that impossible, plus the two
liveness properties that make a big body cheap: the caps are checked before anything
is built, and a large body is parsed off the loop.
"""
import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from infrx.gateway.routes import validate

from . import support


def client(**pilot):
    calls, accept = support.recorder()
    app, _ = support.cutover_app(support.settings(**pilot),
                                 ingress_deps=support.deps(accept=accept))
    return TestClient(app), calls


def messages(count, content="hi"):
    return {"messages": [{"role": "user", "content": content} for _ in range(count)]}


def test_media_sec__the_message_count_is_bounded():
    tc, calls = client()
    assert tc.post(support.CHAT_PATH, headers=support.AUTH,
                   json=messages(validate.MAX_MESSAGES)).status_code == 202
    response = tc.post(support.CHAT_PATH, headers=support.AUTH,
                       json=messages(validate.MAX_MESSAGES + 1))
    assert response.status_code == 400, response.text
    assert support.error_of(response)["code"] == "invalid_request"
    assert len(calls) == 1


def test_media_sec__the_parts_per_message_are_bounded():
    part = {"type": "text", "text": "hi"}
    tc, calls = client()
    ok = {"messages": [{"role": "user", "content": [part] * validate.MAX_PARTS_PER_MESSAGE}]}
    too_many = {"messages": [{"role": "user",
                              "content": [part] * (validate.MAX_PARTS_PER_MESSAGE + 1)}]}
    assert tc.post(support.CHAT_PATH, headers=support.AUTH, json=ok).status_code == 202
    assert tc.post(support.CHAT_PATH, headers=support.AUTH, json=too_many).status_code == 400
    assert len(calls) == 1


def test_media_sec__the_total_text_is_bounded():
    tc, calls = client()
    body = messages(2, content="a" * (validate.MAX_TEXT_CODEPOINTS // 2 + 1))
    response = tc.post(support.CHAT_PATH, headers=support.AUTH, json=body)
    assert response.status_code == 400, response.text
    assert calls == []


def test_media_sec__a_public_url_is_bounded():
    tc, calls = client()
    body = {"messages": [{"role": "user", "content": [
        {"type": "video_url",
         "video_url": {"url": "https://cdn.test/" + "a" * validate.MAX_URL_CHARS}}]}]}
    response = tc.post(support.CHAT_PATH, headers=support.AUTH, json=body)
    assert (response.status_code, support.error_of(response)["code"]) == (400, "unsupported_media")
    assert calls == []


def test_media_sec__non_media_json_is_bounded_but_inline_media_is_not():
    """The bound that catches structure every individual cap allows. An inline
    `data:` video is the one thing a legitimate body is megabytes of, so its
    characters are measured out of it."""
    tc, calls = client()
    padding = "x" * (validate.MAX_NON_MEDIA_BYTES // 60)
    fat = {"messages": [{"role": "user", "content": [{"type": "text", "text": padding}]}
                        for _ in range(60)]}
    response = tc.post(support.CHAT_PATH, headers=support.AUTH, json=fat)
    assert response.status_code in (400, 413), response.text
    assert calls == []

    inline = "data:video/mp4;base64," + "A" * (validate.MAX_NON_MEDIA_BYTES + 4096)
    body = {"messages": [{"role": "user", "content": [
        {"type": "video_url", "video_url": {"url": inline}}]}]}
    assert tc.post(support.CHAT_PATH, headers=support.AUTH, json=body).status_code == 202
    assert len(calls) == 1


def test_media_sec__a_huge_body_is_refused_without_stalling_the_event_loop():
    """The reviewer's repro, shrunk to 8 MiB: refused, nothing accepted, and the
    loop keeps running while it happens.

    The gap bound is wall clock on purpose - it is a liveness property, not a logical
    one - and it is loose (0.5 s) because the machine is shared; the behaviour it
    rules out took 1.3 s for this body and 21.8 s for the reviewer's 95 MiB one.
    """
    count = 8 * 1024 * 1024 // 40
    raw = json.dumps({"messages": [{"role": "user", "content": ""}] * count}).encode()
    calls, accept = support.recorder()
    app, _ = support.cutover_app(ingress_deps=support.deps(accept=accept))
    gaps = []

    async def drive():
        async def watchdog():
            last = time.monotonic()
            while True:
                await asyncio.sleep(0)
                now = time.monotonic()
                gaps.append(now - last)
                last = now

        sent = []

        async def receive():
            return {"type": "http.request", "body": raw, "more_body": False}

        async def send(message):
            sent.append(message)

        watch = asyncio.create_task(watchdog())
        await asyncio.sleep(0)
        await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                   "method": "POST", "path": support.CHAT_PATH,
                   "raw_path": support.CHAT_PATH.encode(), "query_string": b"", "root_path": "",
                   "scheme": "http", "client": ("127.0.0.1", 1), "server": ("t", 80),
                   "headers": [(key.encode(), value.encode())
                               for key, value in support.RAW.items()]}, receive, send)
        watch.cancel()
        return sent

    sent = asyncio.run(drive())
    assert sent[0]["status"] in (400, 413), sent[0]
    assert calls == []
    assert max(gaps) < 0.5, f"the event loop was blocked for {max(gaps):.2f}s"


def test_media_sec__a_large_body_is_parsed_off_the_event_loop():
    """Directly: `parse_body` above the threshold runs in another thread, below it
    on the caller's."""
    import threading

    raw = b'{"messages": [' + b'{"role":"user","content":"x"},' * 40_000 + b'{"role":"user","content":"x"}]}'
    assert len(raw) > validate.PARSE_OFFLOAD_BYTES

    async def where(body, threshold):
        from infrx.gateway.routes import intake

        seen = {}
        real = intake.parse_object

        def spy(data):
            seen["thread"] = threading.get_ident()
            return real(data)

        intake.parse_object = spy
        try:
            await intake.parse_body(body, offload_over_bytes=threshold)
        finally:
            intake.parse_object = real
        return seen["thread"], threading.get_ident()

    parsed_on, loop_thread = asyncio.run(where(raw, validate.PARSE_OFFLOAD_BYTES))
    assert parsed_on != loop_thread, "a large body was parsed on the event loop"
    small_on, loop_thread = asyncio.run(where(b'{"a": 1}', validate.PARSE_OFFLOAD_BYTES))
    assert small_on == loop_thread, "a small body paid for a thread"


# The surrogate travels as the JSON escape it legally is; no HTTP client can encode
# the character itself, which is the whole point.
UNSTORABLE = (
    ("a lone surrogate in content",
     rb'{"messages":[{"role":"user","content":"\ud800"}]}'),
    ("a lone surrogate in a text part",
     rb'{"messages":[{"role":"user","content":[{"type":"text","text":"\ud800"}]}]}'),
    ("a NUL in content", rb'{"messages":[{"role":"user","content":"a\u0000b"}]}'),
    ("a lone surrogate in a stop sequence",
     rb'{"messages":[{"role":"user","content":"hi"}],"stop":["\ud800"]}'),
    ("a lone surrogate in a video url",
     rb'{"messages":[{"role":"user","content":[{"type":"video_url",'
     rb'"video_url":{"url":"https://cdn.test/\ud800"}}]}]}'),
)


@pytest.mark.parametrize("name,raw", UNSTORABLE, ids=[row[0] for row in UNSTORABLE])
def test_media_sec__text_the_database_cannot_store_is_refused(name, raw):
    """Valid JSON, unencodable UTF-8 (or unstorable in `jsonb`): a 400 here, not a
    500 three layers down when the digest is computed."""
    tc, calls = client()
    response = tc.post(support.CHAT_PATH, headers=support.RAW, content=raw)
    assert response.status_code == 400, response.text
    assert support.error_of(response)["code"] == "invalid_request"
    assert calls == []


MALFORMED = (
    ("100k deep nesting", b'{"messages":' + b"[" * 100_000 + b"]" * 100_000 + b"}"),
    ("100k unterminated objects", b'{"a":' * 100_000),
    ("deep nesting inside content",
     b'{"messages":[{"role":"user","content":' + b"[" * 100_000 + b"]" * 100_000 + b"}]}"),
    ("NaN", b'{"messages":[{"role":"user","content":"hi"}],"temperature":NaN}'),
    ("Infinity", b'{"messages":[{"role":"user","content":"hi"}],"temperature":Infinity}'),
)


@pytest.mark.parametrize("name,raw", MALFORMED, ids=[row[0] for row in MALFORMED])
def test_media_sec__a_parser_hostile_body_is_a_400_not_a_500(name, raw):
    tc, calls = client()
    response = tc.post(support.CHAT_PATH, headers=support.RAW, content=raw)
    assert response.status_code == 400, response.text[:200]
    assert support.error_of(response)["code"] == "invalid_request"
    assert calls == []


def test_dur_rls__an_unauthenticated_caller_never_makes_us_buffer():
    """Identity is resolved from headers first, so an anonymous 96 MiB upload is a
    401 before a byte of it is read."""
    calls, accept = support.recorder()
    app, _ = support.cutover_app(ingress_deps=support.deps(accept=accept))
    read = []

    async def receive():
        read.append(1)
        return {"type": "http.request", "body": b"x" * 1024, "more_body": True}

    sent = []

    async def send(message):
        sent.append(message)

    asyncio.run(app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                     "method": "POST", "path": support.CHAT_PATH,
                     "raw_path": support.CHAT_PATH.encode(), "query_string": b"", "root_path": "",
                     "scheme": "http", "client": ("127.0.0.1", 1), "server": ("t", 80),
                     "headers": [(b"content-type", b"application/json")]}, receive, send))
    assert sent[0]["status"] == 401, sent[0]
    assert read == [], "the body was read for a caller with no identity"
    assert calls == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and not hasattr(fn, "pytestmark"):
            fn()
            print("ok", name)
