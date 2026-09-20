#!/usr/bin/env python3
"""Leak matrix: every request form x every outcome must emit zero canary bytes.

Round 3 found a signed positional video URL written whole (signature and credential)
to stdout, to bench.jsonl and to every raw row, because the label came from
os.path.basename() and the sink's URL rule needed a scheme to match. The per-path
regression tests in test_bench.py could not have caught it: the leak was on the
SUCCESS path of an input shape no test used. So this file stops testing paths and
tests the property instead -- drive the whole cross product through bench.main()
against the in-process fake gateway, capture EVERYTHING the client emitted (stdout,
stderr, --out, the raw JSONL) and assert none of it contains the canary key, any
usable prefix of it, or a canary signature/credential query value.

No network (httpx.MockTransport), no built media (fake clip bytes), no corpus cache.

    python -m pytest models/marlin2b/tests/test_leaks.py -q
    python models/marlin2b/tests/test_leaks.py
"""
import contextlib, io, json, os, random, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]

import httpx

import bench
from fake_gateway import FakeGateway
from test_bench import make_clips, with_clips              # fake local clips, patched loader

# Canaries. The key carries '/', '+' and '=' because a real key may, and 8+ character
# prefixes of it are checked separately (a cut inside the key must leave nothing usable).
KEY = "sk-infrx-CANARYKEY-0123456789abcdef/+=q"
SIG = "CANARYSIGdeadbeefcafebabe0123"
CRED = "AKIACANARYCREDENTIAL"
QUERY = f"X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential={CRED}&X-Amz-Signature={SIG}"
VIDEO_URL = f"https://r3-bucket.invalid/private/clip.mp4?{QUERY}"     # documented positional input
UPLOAD_URL = f"https://upload-bucket.invalid/org1/up_0000?{QUERY}"    # signed PUT destination
REDACTED_VIDEO = "https://r3-bucket.invalid/private/clip.mp4?[redacted-query]"
CANARIES = (SIG, CRED, "X-Amz-Signature", "X-Amz-Credential")


# ------------------------------------------------------------------ harness


def raises(exc, text):
    def handler(request):
        raise exc(text, request=request)
    return handler


def body(status, payload, **kw):
    """A canned error response; `payload` is JSON unless it is a str (raw body)."""
    def handler(request):
        if isinstance(payload, str):
            return httpx.Response(status, text=payload, **kw)
        return httpx.Response(status, json=payload, **kw)
    return handler


# Every outcome a request can end in, as fake-gateway keyword arguments. The 4xx/5xx
# bodies echo the key and the signed URL the way a real gateway's error text does,
# including the shapes that used to slip past: an uppercase scheme, a scheme-less
# host, a \uXXXX-escaped key, a straddled truncation point, and non-dict error bodies.
OUTCOMES = {
    "200_stream": {},
    "200_truncated": {"truncate_stream": True},
    "401_echo_key_and_url": {"chat_override": body(
        401, {"error": {"code": f"bad_key:{KEY}", "type": "auth",
                        "message": f"key {KEY} may not read {VIDEO_URL}"}})},
    "401_echo_uppercase_and_schemeless_url": {"chat_override": body(
        401, {"error": {"code": f"upload-bucket.invalid/org1/o?{QUERY}",
                        "message": f"fetch failed HTTPS://R3-BUCKET.INVALID/private/clip.mp4?{QUERY}"}})},
    "401_escaped_key_straddling_the_cut": {"echo_key_in_json_message": KEY, "echo_pad": 180,
                                           "escape_key": True},
    "401_non_json_body_straddling_the_cut": {"echo_key_in_long_body": KEY},
    "402_string_error_body": {"chat_override": body(
        402, {"error": f"no credit for {KEY} fetching {VIDEO_URL}"})},
    "429_list_body": {"chat_override": body(
        429, [{"loc": "header", "msg": f"rate limit for {KEY}"}], headers={"retry-after": "0"})},
    "429_then_retry": {"statuses": {0: 429}, "retry_after": "0"},
    "500_echo_key_and_url": {"chat_override": body(
        500, f"upstream 500 while fetching {VIDEO_URL} with key {KEY}")},
    "307_redirect_to_signed_url": {"chat_override": body(
        307, f"moved to {UPLOAD_URL}", headers={"location": UPLOAD_URL})},
    "connect_error": {"chat_override": raises(
        httpx.ConnectError, f"[Errno 111] connecting to {VIDEO_URL} with Bearer {KEY}")},
    "read_timeout": {"chat_override": raises(
        httpx.ReadTimeout, f"read timed out on {VIDEO_URL} (Bearer {KEY})")},
    "upload_put_403": {"upload_url": UPLOAD_URL, "put_status": 403},
    "sse_error_event": {"stream_error": {"code": f"mid_stream:{KEY}",
                                         "message": f"aborted fetching {VIDEO_URL}"}},
}

# Every input shape: the four documented forms over corpus clips, and the same forms
# over the positional http(s) video argument (the shape that leaked). `upload` with a
# URL cannot read bytes, which is the point: the failure text quotes the URL.
FORMS = [("text", False), ("video_b64", False), ("upload", False), ("video_url", False),
         ("text", True), ("video_b64", True), ("upload", True), ("video_url", True)]

# (accepted, rejected, failed, error class) each outcome really produces on the
# (video_b64, corpus) row. Without this the sweep could pass by emitting nothing at all.
# `429_list_body` is rejected, not failed: err.get() on a list body used to raise inside
# the ValueError guard and a rate limit was then miscounted as a failure.
EXPECT = {
    "200_stream": (2, 0, 0, None),
    "200_truncated": (0, 0, 2, "truncated_stream"),
    "401_echo_key_and_url": (0, 2, 0, "http_401"),
    "401_echo_uppercase_and_schemeless_url": (0, 2, 0, "http_401"),
    "401_escaped_key_straddling_the_cut": (0, 2, 0, "http_401"),
    "401_non_json_body_straddling_the_cut": (0, 2, 0, "http_401"),
    "402_string_error_body": (0, 2, 0, "http_402"),
    "429_list_body": (0, 2, 0, "http_429"),
    "429_then_retry": (2, 0, 0, None),
    "500_echo_key_and_url": (0, 0, 2, "http_500"),
    "307_redirect_to_signed_url": (0, 0, 2, "http_307"),
    "connect_error": (0, 0, 2, "ConnectError"),
    "read_timeout": (0, 0, 2, "ReadTimeout"),
    "upload_put_403": (2, 0, 0, None),          # no PUT happens on the video_b64 row
    "sse_error_event": (0, 0, 2, "stream_error_event"),
}


def run(tmp, form, positional, gw_kwargs, requests=2):
    """One bench.main() run. Returns (blobs, summary_or_None): blobs is every byte the
    client emitted -- stdout, stderr, --out and the raw JSONL."""
    out, raw = os.path.join(tmp, "bench.jsonl"), os.path.join(tmp, "raw.jsonl")
    for p in (out, raw):
        if os.path.exists(p):
            os.remove(p)
    argv = ["--out", out, "--raw", raw, "--dry-run-transport", "fake_gateway:transport",
            "--base-url", "http://fake.invalid/v1", "--target", "gateway", "--prompt", "p",
            "--forms", form, "--requests", str(requests), "--concurrency", "1", "--no-warmup",
            "--retries", "1"]       # the retry path is an emitting path too (429 outcomes)
    argv += [VIDEO_URL] if positional else ["--corpus", "unused"]
    if form == "video_url" and not positional:
        argv += ["--media-base-url", "https://media.invalid/clips"]

    gw = FakeGateway(ttft=0.001, token_gap=0.0, tokens=2, **gw_kwargs)
    bench.load_transport = lambda spec: gw.transport()
    os.environ["MARLIN_API_KEY"] = KEY
    sout, serr = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(sout), contextlib.redirect_stderr(serr):
            try:
                bench.main(argv)
            except SystemExit as e:                  # a redacted sys.exit() string is output too
                print(e.code, file=sys.stderr)
    finally:
        os.environ.pop("MARLIN_API_KEY", None)
    blobs = {"stdout": sout.getvalue(), "stderr": serr.getvalue()}
    for name, path in (("out", out), ("raw", raw)):
        blobs[name] = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    summary = None
    if "{" in blobs["stdout"]:
        summary = json.loads(blobs["stdout"][blobs["stdout"].index("{"):])
    return blobs, summary


def assert_clean(blobs, why):
    for where, blob in blobs.items():
        for canary in CANARIES:
            assert canary not in blob, f"{why}: {canary} leaked into {where}"
        for cut in range(8, len(KEY) + 1):
            assert KEY[:cut] not in blob, \
                f"{why}: {cut} of {len(KEY)} key characters leaked into {where}"


# ------------------------------------------------------------------ tests


def test_no_form_and_no_outcome_can_emit_a_canary():
    """8 input shapes x 15 outcomes through the real client, checking all four sinks."""
    runs = 0
    with tempfile.TemporaryDirectory() as tmp:
        with_clips(make_clips(4, tmp))
        for form, positional in FORMS:
            for name, gw_kwargs in OUTCOMES.items():
                blobs, summary = run(tmp, form, positional, gw_kwargs)
                why = f"{form}{'(url)' if positional else ''} / {name}"
                assert blobs["stdout"].strip(), f"{why}: produced no summary at all"
                assert_clean(blobs, why)
                if positional:                   # the round-3 finding, on every outcome
                    assert summary["video"] == REDACTED_VIDEO, why
                if (form, positional) == ("video_b64", False):     # the outcome really happened
                    got = (summary["accepted"], summary["rejected"], summary["failed"],
                           (sorted(summary["error_classes"]) or [None])[0])
                    assert got == EXPECT[name], (why, got, EXPECT[name])
                if form == "upload" and name == "upload_put_403" and not positional:
                    assert summary["error_classes"] == {"RuntimeError": 2}, why
                    assert "HTTP 403" in blobs["raw"], "the PUT status must survive redaction"
                runs += 1
    assert runs == len(FORMS) * len(OUTCOMES) == 120, runs


def test_a_signed_positional_url_is_reduced_in_every_sink():
    """The round-3 blocking finding: basename() of a URL kept 'clip.mp4?X-Amz-Signature=…'
    and the sink's URL rule could no longer see it."""
    with tempfile.TemporaryDirectory() as tmp:
        blobs, summary = run(tmp, "video_url", True, {})
        assert summary["accepted"] == 2 and summary["video"] == REDACTED_VIDEO
        rows = [json.loads(l) for l in blobs["raw"].splitlines()]
        assert rows and all(r["clip_id"] == REDACTED_VIDEO for r in rows)
        assert json.loads(blobs["out"].splitlines()[-1])["video"] == REDACTED_VIDEO
        assert_clean(blobs, "signed positional URL on the success path")
        # and a local file still reports its basename, not its path
        assert bench.video_label("/mnt/nvme/clips/c000.mp4") == "c000.mp4"
        assert bench.video_label(VIDEO_URL) == VIDEO_URL


def test_redact_reduces_every_url_shape_and_leaves_no_key_prefix():
    r = lambda s: bench.redact(s, KEY)
    assert r(VIDEO_URL) == REDACTED_VIDEO
    assert r(f"HTTPS://HOST/p?{QUERY}") == "HTTPS://HOST/p?[redacted-query]"
    assert r(f"bucket.host/key?{QUERY}") == "bucket.host/key?[redacted-query]"
    assert r(f"https://u:{SIG}@host/p") == "https://[redacted-userinfo]@host/p"
    assert r(f"https://host/p#access_token={SIG}") == "https://host/p#[redacted-query]"
    assert r("https://host/plain/path") == "https://host/plain/path", "a clean URL survives"
    assert r(KEY) == "[redacted-key]" and r(KEY[:12]) == "[redacted-key]"
    assert r(f"a{KEY}b") == "a[redacted-key]b"
    assert r(r(VIDEO_URL + " " + KEY)) == r(VIDEO_URL + " " + KEY), "redact must be idempotent"
    assert r("") == "" and bench.redact(VIDEO_URL, "") == REDACTED_VIDEO, "no key, still no query"


def test_no_canary_survives_a_random_context_property_sweep():
    """Property sweep: the canaries embedded at random offsets in random surrounding text,
    JSON-encoded or not, must never survive -- and never survive a truncation either."""
    rng = random.Random(20260920)
    filler = ' x"\'\\{}[]:,=&?#/ \nabcDEF09'
    for i in range(400):
        pad = lambda: "".join(rng.choice(filler) for _ in range(rng.randrange(0, 40)))
        secret = rng.choice([KEY, KEY[:rng.randrange(8, len(KEY))], VIDEO_URL, UPLOAD_URL,
                             f"bucket.host/k?{QUERY}", f"HTTPS://H/p?{QUERY}",
                             f"https://u:{SIG}@h/p"])
        text = pad() + secret + pad()
        if i % 3 == 0:
            text = json.dumps({"m": text})              # the shape a row really takes
        out = bench.redact(text, KEY)
        for cut in (None, 200, 120, len(secret) // 2 + 1):
            piece = out if cut is None else out[:cut]
            for canary in CANARIES:
                assert canary not in piece, (canary, text, piece)
            for n in range(8, len(KEY) + 1):
                assert KEY[:n] not in piece, (n, text, piece)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok {t.__name__}")
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    main()
