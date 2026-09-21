#!/usr/bin/env python3
"""MEDIA-SEC (structure half): what a body may contain inside the byte cap.

The finding these exist for: 96 MiB of JSON is 3.4 million empty messages, and one
authenticated key sending it stalled the whole process — 1.9 s in `json.loads` alone,
+1.1 GiB resident, and the refusal only afterwards, because the count caps run on the
parsed tree. Eight at once made that 13 s and 8 GiB.

So the order is the point, and three properties carry it, each proven by instrumenting
the code rather than by a stopwatch:

* the openers are counted in the raw bytes and the body refused **before**
  `json.loads` is called (a spy on the parser proves it was not),
* at most two large bodies are in flight per process, the rest refused 429 (the peak
  in-flight count is asserted),
* nothing per-byte in Python ever touches an inline media payload — not the URL
  hygiene regex, not `storable`, not `canonical_bytes` (spies on all three).

What remains is one `json.loads` of a legitimate large body, measured and reported as
a limit in the evidence. A thread would not have helped: it is one C call holding the
GIL.
"""
import asyncio
import json

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


def test_media_sec__the_parser_itself_refuses_json_that_is_not_json():
    """Directly at the boundary, not through the range checks that also catch these:
    `json` accepts `NaN`/`Infinity` by default, `jsonb` cannot store them, and
    `json.dumps` would then write a payload no parser accepts. So they never become a
    Python object in the first place.
    """
    from infrx.contracts import errors
    from infrx.gateway.routes import intake

    for raw in (b'{"x": NaN}', b'{"x": Infinity}', b'{"x": -Infinity}', b'{"x": [NaN]}'):
        with pytest.raises(errors.InvalidRequest) as raised:
            intake.parse_object(raw)
        assert raised.value.code == "invalid_request"
    assert intake.parse_object(b'{"x": 1.5}') == {"x": 1.5}




# --- review r2 B1: refuse before materialising, and bound how many arrive at once ---
def parse_spy():
    """A recorder around `intake.parse_object`, so "the parser was never called" is an
    assertion and not a hope. Restored by the caller."""
    from infrx.gateway.routes import intake

    calls = []
    real = intake.parse_object

    def spy(raw):
        calls.append(len(raw))
        return real(raw)

    intake.parse_object = spy
    return calls, (lambda: setattr(intake, "parse_object", real))


HOSTILE = (
    # the reviewer's attack9, shrunk to 8 MiB: the same shape, the same refusal
    ("3.4M empty messages", lambda n: b'{"messages":[' + b'{"role":"user","content":""},' * n
                                     + b'{"role":"user","content":""}]}'),
    ("nested arrays", lambda n: b'{"messages":' + b"[" * n + b"]" * n + b"}"),
)


@pytest.mark.parametrize("name,build", HOSTILE, ids=[row[0] for row in HOSTILE])
def test_media_sec__hostile_structure_is_refused_without_parsing_it(name, build):
    """B1a: the count caps run on the parsed tree, so they arrive after the damage.
    This is refused by counting openers in the raw bytes - `json.loads` is never
    called, which the spy proves."""
    from infrx.gateway.routes import validate as v

    raw = build(200_000)
    assert raw.count(b"{") + raw.count(b"[") > v.MAX_OPENERS, "the body is not over the cap"
    tc, accepted = client()
    calls, restore = parse_spy()
    try:
        response = tc.post(support.CHAT_PATH, headers=support.RAW, content=raw)
    finally:
        restore()
    assert response.status_code == 413, response.text[:200]
    assert support.error_of(response)["code"] == "request_too_large"
    assert calls == [], f"the parser ran on {calls} bytes"
    assert accepted == []


def test_media_sec__a_legitimate_body_at_the_opener_cap_is_still_parsed():
    """The cap is the structure the caps allow plus one opener per permitted code
    point of text, so a body full of braces *in text* is accepted."""
    # more braces than the structure the caps allow, so only the text allowance can
    # be what admits this
    braces = "{" * (validate.STRUCTURE_OPENERS * 4)
    body = {"messages": [{"role": "user", "content": braces}]}
    tc, accepted = client()
    calls, restore = parse_spy()
    try:
        response = tc.post(support.CHAT_PATH, headers=support.AUTH, json=body)
    finally:
        restore()
    assert response.status_code == 202, response.text[:200]
    assert calls and len(accepted) == 1


def test_media_sec__at_most_two_large_bodies_are_in_flight():
    """B1b: one parse of a legitimate large body stalls the loop; this bounds how many
    can do that at once. The excess is refused with retry guidance, never queued.

    The eight requests are held *inside the read* - each sends one chunk and waits -
    so they are genuinely concurrent at the moment the slot is claimed, with no sleep
    anywhere.
    """
    from infrx.gateway.routes import intake

    slots = intake.LargeBodies(limit=2, threshold=1024)
    calls, accept = support.recorder()
    app, mounted = support.cutover_app(
        ingress_deps=support.deps(accept=accept, large_bodies=slots))
    assert mounted.slots is slots
    head = b'{"messages":[{"role":"user","content":"' + b"x" * 4_000
    tail = b'"}]}'

    async def drive():
        everyone_arrived = asyncio.Event()
        arrived = 0

        async def one():
            nonlocal arrived
            sent = []
            chunks = [{"type": "http.request", "body": head, "more_body": True},
                      {"type": "http.request", "body": tail, "more_body": False}]

            async def receive():
                nonlocal arrived
                message = chunks.pop(0)
                if message["more_body"]:
                    arrived += 1
                    if arrived >= 8:
                        everyone_arrived.set()
                    return message
                # the holders finish only once every request has claimed or been refused
                await everyone_arrived.wait()
                return message

            async def send(message):
                sent.append(message)

            await app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                       "method": "POST", "path": support.CHAT_PATH,
                       "raw_path": support.CHAT_PATH.encode(), "query_string": b"",
                       "root_path": "", "scheme": "http", "client": ("127.0.0.1", 1),
                       "server": ("t", 80),
                       "headers": [(key.encode(), value.encode())
                                   for key, value in support.RAW.items()]},
                      receive, send)
            return sent[0]["status"], dict(
                (key.decode(), value.decode()) for key, value in sent[0]["headers"])

        return await asyncio.gather(*[one() for _ in range(8)])

    answers = asyncio.run(drive())
    statuses = [status for status, _headers in answers]
    assert statuses.count(202) == 2, statuses
    assert statuses.count(429) == 6, statuses
    assert slots.peak == 2, slots.peak
    assert slots.in_flight == 0, "a slot was not released"
    assert slots.refused == 6
    assert len(calls) == 2
    for status, headers in answers:
        if status == 429:
            assert headers["retry-after"] == "2", headers


def test_media_sec__a_large_body_slot_is_released_on_every_path():
    """Including the refusals: a slot the ingress keeps is a slot nobody gets again."""
    from infrx.gateway.routes import intake

    slots = intake.LargeBodies(limit=1, threshold=64)
    calls, accept = support.recorder()
    app, _ = support.cutover_app(ingress_deps=support.deps(accept=accept, large_bodies=slots))
    tc = TestClient(app)
    big = "x" * 4_000
    for status, body in ((202, json.dumps({"messages": [{"role": "user", "content": big}]})),
                         (400, json.dumps({"messages": [{"role": "user", "content": big},
                                                        {"role": "nope", "content": big}]})),
                         (400, "{" + big)):
        assert tc.post(support.CHAT_PATH, headers=support.RAW,
                       content=body.encode()).status_code == status, body[:40]
        assert slots.in_flight == 0, (status, slots.in_flight)
    assert len(calls) == 1


def test_media_sec__no_per_byte_python_work_touches_a_media_payload():
    """B1c: the expensive scans (`storable`, the URL hygiene regex) are for short
    references. A 4 MiB inline payload goes through the prefix check only, and the
    payload digest is the canonical non-media document plus the payload's hash."""
    from infrx.gateway.routes import validate as v

    payload = "data:video/mp4;base64," + "A" * (4 * 1024 * 1024)
    seen = []
    real_storable, real_search = v.storable, v.UNSAFE_IN_URL.search

    def spy_storable(text, param):
        seen.append(("storable", len(text)))
        return real_storable(text, param)

    class SpyRe:
        def search(self, text):
            seen.append(("regex", len(text)))
            return real_search(text)

    real_canonical = v.canonical_bytes

    def spy_canonical(value):
        out = real_canonical(value)
        seen.append(("canonical_bytes", len(out)))
        return out

    v.storable, v.UNSAFE_IN_URL, v.canonical_bytes = spy_storable, SpyRe(), spy_canonical
    try:
        tc, accepted = client()
        response = tc.post(support.CHAT_PATH, headers=support.AUTH, json={
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "describe"},
                {"type": "video_url", "video_url": {"url": payload}}]}]})
    finally:
        v.storable, v.canonical_bytes = real_storable, real_canonical
        v.UNSAFE_IN_URL = real_search.__self__
    assert response.status_code == 202, response.text[:200]
    assert len(accepted) == 1
    # nothing per-byte ever saw the payload
    assert all(size != len(payload) for _kind, size in seen), "a scan ran over the payload"
    # and the digest document is the non-media one: kilobytes, not megabytes
    serialised = [size for kind, size in seen if kind == "canonical_bytes"]
    assert serialised and max(serialised) < 4_096, serialised
    assert max(size for kind, size in seen if kind != "canonical_bytes") < 1_000, seen


def test_dur_admit__the_payload_digest_covers_the_media_payload_by_hash():
    """The exact construction: the canonical document with each inline payload replaced
    by `data-sha256:<sha256 of the url>`. Stable under reordering, and one byte of the
    payload changes it."""
    from infrx.gateway.routes import validate as v

    def digest_for(payload):
        body = {"messages": [{"role": "user", "content": [
            {"type": "video_url", "video_url": {"url": payload}}]}]}
        messages, media = v.check_messages(body, {"video/mp4"})
        return v.payload_digest(body, messages, media), media

    a, media = digest_for("data:video/mp4;base64," + "A" * 4096)
    b, _ = digest_for("data:video/mp4;base64," + "A" * 4095 + "B")
    assert a != b, "a changed payload did not change the digest"
    assert list(media.values())[0].startswith(v.MEDIA_TOKEN)
    again, _ = digest_for("data:video/mp4;base64," + "A" * 4096)
    assert a == again

    # and the rest of the body still counts: same key, same video, different sampling
    # is a different payload, so it must conflict rather than replay.
    payload = "data:video/mp4;base64," + "A" * 4096
    digests = set()
    for extra in ({}, {"temperature": 0.5}, {"temperature": 0.6}, {"max_tokens": 7},
                  {"model": support.PUBLIC_MODEL}):
        body = {"messages": [{"role": "user", "content": [
            {"type": "video_url", "video_url": {"url": payload}}]}], **extra}
        messages, media = v.check_messages(body, {"video/mp4"})
        digests.add(v.payload_digest(body, messages, media))
    assert len(digests) == 5, "a parameter beside the media did not change the digest"


# --- review r2 B2: the text cap counts code points, whatever the encoding ----------
# Every row is within `MAX_TEXT_CODEPOINTS` and must be ACCEPTED. The bound this
# replaced assumed one byte per code point and no JSON escaping, so each of these was
# a 413 against a documented 96 MiB cap.
WITHIN_THE_CAP = (
    ("35,000 CJK characters, escaped as an ordinary client sends them",
     json.dumps({"messages": [{"role": "user", "content": "漢" * 35_000}]},
                ensure_ascii=True).encode()),
    ("70,000 CJK characters as UTF-8",
     json.dumps({"messages": [{"role": "user", "content": "漢" * 70_000}]},
                ensure_ascii=False).encode()),
    ("110,000 newlines",
     json.dumps({"messages": [{"role": "user", "content": "\n" * 110_000}]}).encode()),
    ("131,072 astral code points",
     json.dumps({"messages": [{"role": "user", "content": "𝄞" * 131_072}]},
                ensure_ascii=False).encode()),
    ("300 KB of insignificant whitespace",
     b'{\n' + b' ' * 300_000 + b'"messages":[{"role":"user","content":"hi"}]}'),
    ("the cap exactly, in ASCII",
     json.dumps({"messages": [{"role": "user", "content": "a" * 131_072}]}).encode()),
)


@pytest.mark.parametrize("name,raw", WITHIN_THE_CAP, ids=[row[0] for row in WITHIN_THE_CAP])
def test_media_sec__text_within_the_code_point_cap_is_accepted_in_every_form(name, raw):
    tc, accepted = client()
    response = tc.post(support.CHAT_PATH, headers=support.RAW, content=raw)
    assert response.status_code == 202, (len(raw), response.text[:200])
    assert len(accepted) == 1


PAST_THE_CAP = (
    ("one code point past, in ASCII", "a" * (validate.MAX_TEXT_CODEPOINTS + 1)),
    ("one code point past, in CJK", "漢" * (validate.MAX_TEXT_CODEPOINTS + 1)),
    ("one code point past, astral", "𝄞" * (validate.MAX_TEXT_CODEPOINTS + 1)),
)


@pytest.mark.parametrize("name,content", PAST_THE_CAP, ids=[row[0] for row in PAST_THE_CAP])
def test_media_sec__one_code_point_past_the_cap_is_refused(name, content):
    tc, accepted = client()
    response = tc.post(support.CHAT_PATH, headers=support.RAW, content=json.dumps(
        {"messages": [{"role": "user", "content": content}]}, ensure_ascii=False).encode())
    assert response.status_code == 400, response.text[:200]
    assert support.error_of(response)["code"] == "invalid_request"
    assert accepted == []


def test_media_sec__the_text_cap_counts_across_parts_and_messages():
    """Not only string content: the cap is the whole request's text, so it cannot be
    evaded by splitting it over parts."""
    half = validate.MAX_TEXT_CODEPOINTS // 2
    tc, accepted = client()
    over = {"messages": [{"role": "user", "content": [
        {"type": "text", "text": "a" * half}, {"type": "text", "text": "b" * (half + 1)}]}]}
    assert tc.post(support.CHAT_PATH, headers=support.AUTH, json=over).status_code == 400
    across = {"messages": [{"role": "user", "content": [{"type": "text", "text": "a" * half}]},
                           {"role": "user", "content": "b" * (half + 1)}]}
    assert tc.post(support.CHAT_PATH, headers=support.AUTH, json=across).status_code == 400
    assert accepted == []
    under = {"messages": [{"role": "user", "content": [
        {"type": "text", "text": "a" * half}, {"type": "text", "text": "b" * half}]}]}
    assert tc.post(support.CHAT_PATH, headers=support.AUTH, json=under).status_code == 202


def test_media_sec__the_url_cap_is_exact():
    tc, accepted = client()
    for length, status in ((validate.MAX_URL_CHARS, 202), (validate.MAX_URL_CHARS + 1, 400)):
        url = "https://cdn.test/" + "a" * (length - len("https://cdn.test/"))
        assert len(url) == length
        response = tc.post(support.CHAT_PATH, headers=support.AUTH, json={
            "messages": [{"role": "user", "content": [
                {"type": "video_url", "video_url": {"url": url}}]}]})
        assert response.status_code == status, (length, response.text[:120])
    assert len(accepted) == 1


def test_media_sec__a_declared_large_body_claims_its_slot_before_it_is_read():
    """The declared length is not trusted as a bound - the running total is - but it is
    used to claim a slot early, so a request that will be refused never gets buffered
    at all. Removing that costs one wasted 96 MiB read per refusal."""
    from infrx.gateway.routes import intake

    slots = intake.LargeBodies(limit=1, threshold=1024)
    slots.in_flight = 1                       # somebody else is already parsing
    calls, accept = support.recorder()
    app, _ = support.cutover_app(ingress_deps=support.deps(accept=accept, large_bodies=slots))
    read, sent = [], []

    async def receive():
        read.append(1)
        return {"type": "http.request", "body": b"x" * 8_000, "more_body": True}

    async def send(message):
        sent.append(message)

    asyncio.run(app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                     "method": "POST", "path": support.CHAT_PATH,
                     "raw_path": support.CHAT_PATH.encode(), "query_string": b"", "root_path": "",
                     "scheme": "http", "client": ("127.0.0.1", 1), "server": ("t", 80),
                     "headers": [(b"authorization", f"Bearer {support.TOKEN}".encode()),
                                 (b"content-type", b"application/json"),
                                 (b"content-length", b"9000")]},
                    receive, send))
    assert sent[0]["status"] == 429, sent[0]
    assert read == [], "the body was read for a request that had no slot"
    assert calls == []


# --- review r3 B1: one opener is enough, so count the separators too ---------------
# Every one of these has ONE opener and cost seconds of parsing and more than a
# gigabyte before the separator count existed. `n` is sized so the body is ~4 MiB.
OPENER_FREE = (
    ("nine million distinct keys",
     lambda n: b'{"' + b'":1,"'.join(b"%08x" % i for i in range(n)) + b'":1}'),
    ("a flat array of floats", lambda n: b'{"messages":[' + b"1.5," * (n - 1) + b"1.5]}"),
    ("a flat array of short strings", lambda n: b'["ab"' + b',"ab"' * (n - 1) + b"]"),
    ("a flat array of integers", lambda n: b'{"messages":[' + b"1," * (n - 1) + b"1]}"),
)


@pytest.mark.parametrize("name,build", OPENER_FREE, ids=[row[0] for row in OPENER_FREE])
def test_media_sec__an_opener_free_body_is_refused_without_parsing_it(name, build):
    raw = build(400_000)
    assert raw.count(b"{") + raw.count(b"[") <= 2, "this body is supposed to evade the opener cap"
    tc, accepted = client()
    calls, restore = parse_spy()
    try:
        response = tc.post(support.CHAT_PATH, headers=support.RAW, content=raw)
    finally:
        restore()
    assert response.status_code == 413, response.text[:200]
    assert support.error_of(response)["code"] == "request_too_large"
    assert calls == [], f"the parser ran on {calls} bytes"
    assert accepted == []


def test_media_sec__the_separator_cap_is_exact():
    """At the cap the body is parsed (and then refused on its shape); one separator
    past it, it is refused *before* the parse. The spy is what makes the two
    distinguishable - both are a refusal to the caller, and only one of them is cheap.
    """
    tc, accepted = client()
    at_the_cap = b'{"x":[' + b"1," * validate.MAX_SEPARATORS + b"1]}"
    assert at_the_cap.decode().count(",") == validate.MAX_SEPARATORS

    calls, restore = parse_spy()
    try:
        inside = tc.post(support.CHAT_PATH, headers=support.RAW, content=at_the_cap)
        past = tc.post(support.CHAT_PATH, headers=support.RAW,
                       content=b'{"x":[' + b"1," * (validate.MAX_SEPARATORS + 1) + b"1]}")
    finally:
        restore()
    assert inside.status_code == 400, inside.text[:160]        # parsed, then unsupported
    assert support.error_of(inside)["code"] == "unsupported_parameter"
    assert past.status_code == 413, past.text[:160]            # refused before the parse
    assert len(calls) == 1, "the body one separator past the cap reached the parser"
    assert accepted == []


def test_media_sec__commas_inside_text_are_text():
    """A comma in a string is text, not structure: 131,072 of them in one message is a
    legitimate request. (Sized from the *text* cap, not from the separator cap, so the
    case cannot move with the bound it is checking.)"""
    tc, accepted = client()
    prose = "," * validate.MAX_TEXT_CODEPOINTS
    response = tc.post(support.CHAT_PATH, headers=support.AUTH, json={
        "messages": [{"role": "user", "content": prose}]})
    assert response.status_code == 202, response.text[:200]
    assert len(accepted) == 1


def test_media_sec__an_inline_video_costs_one_separator():
    """The count must not price a legitimate media body out: base64 has no comma, and
    the `data:` prefix has exactly one."""
    payload = "data:video/mp4;base64," + "A" * (2 * 1024 * 1024)
    assert payload.count(",") == 1
    tc, accepted = client()
    assert tc.post(support.CHAT_PATH, headers=support.AUTH, json=parts_body(
        {"type": "video_url", "video_url": {"url": payload}})).status_code == 202
    assert len(accepted) == 1


NOT_UTF8 = (
    ("UTF-16 with a BOM", lambda: '{"messages":[{"role":"user","content":"孛"}]}'.encode("utf-16")),
    ("UTF-32", lambda: '{"messages":[{"role":"user","content":"hi"}]}'.encode("utf-32")),
    ("a UTF-8 BOM", lambda: b"\xef\xbb\xbf" + b'{"messages":[{"role":"user","content":"hi"}]}'),
    ("latin-1 bytes", lambda: b'{"messages":[{"role":"user","content":"caf\xe9"}]}'),
    ("a truncated code point", lambda: b'{"messages":[{"role":"user","content":"\xe5\xad"}]}'),
)


@pytest.mark.parametrize("name,build", NOT_UTF8, ids=[row[0] for row in NOT_UTF8])
def test_media_sec__a_body_that_is_not_utf8_is_refused(name, build):
    """RFC 8259: the body is UTF-8. `json.loads` on bytes would sniff UTF-16 and
    UTF-32 instead, which also makes every byte-level count measure the wrong thing."""
    tc, accepted = client()
    calls, restore = parse_spy()
    try:
        response = tc.post(support.CHAT_PATH, headers=support.RAW, content=build())
    finally:
        restore()
    assert response.status_code == 400, response.text[:200]
    assert support.error_of(response)["code"] == "invalid_request"
    assert calls == [], "a non-UTF-8 body reached the parser"
    assert accepted == []


def test_media_sec__utf16_text_is_not_a_413_it_is_a_400():
    """The byte count would call 100,000 CJK characters in UTF-16 oversized; they are
    simply not UTF-8, and the answer says so."""
    raw = ("{" + '"messages":[{"role":"user","content":"' + "孛" * 100_000 + '"}]}').encode("utf-16")
    tc, _accepted = client()
    response = tc.post(support.CHAT_PATH, headers=support.RAW, content=raw)
    assert response.status_code == 400, response.text[:160]


# --- review r3 B2: the payload skips `storable`, so it needs its own checks --------
BAD_PAYLOADS = (
    ("a lone surrogate", "data:video/mp4;base64,AAAA\\ud800"),
    ("a NUL", "data:video/mp4;base64,AA\\u0000AA"),
    ("CJK", "data:video/mp4;base64,AAAA\\u5b5b"),
    ("a C1 control", "data:video/mp4;base64,AAAA\\u0085"),
)


@pytest.mark.parametrize("name,payload", BAD_PAYLOADS, ids=[row[0] for row in BAD_PAYLOADS])
def test_media_sec__an_inline_payload_that_is_not_base64_ascii_is_refused(name, payload):
    """118 bytes used to be a 500 with a logged stack: the payload deliberately skips
    `storable`, so `sha256(source.encode())` raised `UnicodeEncodeError`."""
    raw = ('{"messages":[{"role":"user","content":[{"type":"video_url",'
           '"video_url":{"url":"%s"}}]}]}' % payload).encode()
    tc, accepted = client()
    response = tc.post(support.CHAT_PATH, headers=support.RAW, content=raw)
    assert response.status_code == 400, response.text[:200]
    assert support.error_of(response)["code"] == "unsupported_media"
    assert accepted == []


def parts_body(*content):
    return {"messages": [{"role": "user", "content": list(content)}]}


# --- review r3 same pass: the boundaries, exactly ----------------------------------
def test_media_sec__the_opener_cap_is_exact_at_its_real_boundary():
    """At exactly `MAX_OPENERS` the body is parsed; at one more it is refused before
    the parse. Both answer the caller with a refusal, so the spy is the difference."""
    tc, accepted = client()
    body = b'{"x":"' + b"{" * (validate.MAX_OPENERS - 1) + b'"}'
    assert body.decode().count("{") == validate.MAX_OPENERS

    calls, restore = parse_spy()
    try:
        inside = tc.post(support.CHAT_PATH, headers=support.RAW, content=body)
        past = tc.post(support.CHAT_PATH, headers=support.RAW,
                       content=b'{"x":"' + b"{" * validate.MAX_OPENERS + b'"}')
    finally:
        restore()
    assert inside.status_code == 400, inside.text[:160]
    assert support.error_of(inside)["code"] == "unsupported_parameter"
    assert past.status_code == 413, past.text[:160]
    assert len(calls) == 1, "the body one opener past the cap reached the parser"
    assert accepted == []


def test_media_sec__the_large_body_threshold_is_exclusive():
    """A body *at* the threshold is not a large body; one byte more is."""
    from infrx.gateway.routes import intake

    slots = intake.LargeBodies(limit=1, threshold=1024)
    slot = slots.slot()
    slot.account(1024)
    assert slots.in_flight == 0 and not slot.held
    slot.account(1025)
    assert slots.in_flight == 1 and slot.held


def test_media_sec__a_slot_is_released_once_however_often_release_is_called():
    from infrx.gateway.routes import intake

    slots = intake.LargeBodies(limit=1, threshold=8)
    slot = slots.slot()
    slot.account(64)
    assert slots.in_flight == 1
    slot.release()
    slot.release()
    slot.release()
    assert slots.in_flight == 0, "releasing twice freed a slot nobody held"
    other = slots.slot()
    other.account(64)                      # the slot is available exactly once
    assert slots.in_flight == 1


def test_media_sec__a_declared_length_that_is_not_a_number_is_ignored():
    """`"²".isdigit()` is true and a 5,000-digit length is a pointless big int: neither
    is a content length, and neither may reach `int()` on the request path. The running
    total is the real bound either way."""
    from infrx.gateway.routes import intake

    slots = intake.LargeBodies(limit=1, threshold=8)
    calls, accept = support.recorder()
    app, _ = support.cutover_app(ingress_deps=support.deps(accept=accept, large_bodies=slots))
    body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()

    for declared in (b"\xc2\xb2", b"9" * 5_000, b"", b"-1", b"12abc"):
        sent = []

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        asyncio.run(app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                         "method": "POST", "path": support.CHAT_PATH,
                         "raw_path": support.CHAT_PATH.encode(), "query_string": b"",
                         "root_path": "", "scheme": "http", "client": ("127.0.0.1", 1),
                         "server": ("t", 80),
                         "headers": [(b"authorization", f"Bearer {support.TOKEN}".encode()),
                                     (b"content-type", b"application/json"),
                                     (b"content-length", declared)]},
                        receive, lambda message: sent.append(message) or asyncio.sleep(0)))
        assert sent[0]["status"] == 202, (declared[:20], sent[0])
    assert len(calls) == 5
    assert slots.in_flight == 0
