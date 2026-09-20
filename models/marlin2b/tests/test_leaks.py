#!/usr/bin/env python3
"""Leak matrix: every request form x every outcome must emit zero canary bytes.

Rounds 1-3 scrubbed what the client emitted and lost three times: a signed URL whose
query contained a quote or a backslash, a userinfo longer than the pattern allowed, a
path longer than the pattern allowed, a scheme-less echo, and an echoed key BODY
(key[9:]) all walked past the regex. Round 4 stopped filtering and started allowlisting:
no server-controlled string is recorded verbatim, a URL label is rebuilt from parsed
parts, exceptions contribute only their class, library logging is muted, and an argv
value carrying the key refuses the run. This file tests that property from the outside.

    python -m pytest models/marlin2b/tests/test_leaks.py -q
    python models/marlin2b/tests/test_leaks.py

No network (httpx.MockTransport), no built media (fake clip bytes), no corpus cache.
"""
import contextlib, io, json, os, random, re, signal, string, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]

import httpx

import bench
from fake_gateway import FakeGateway
from test_bench import make_clips, with_clips              # fake local clips, patched loader

# Canaries. The key carries '/', '+' and '=' because a real key may. Every substring of
# it of 8 characters or more is checked, not just prefixes: an echoed key[9:] used to pass.
KEY = "sk-infrx-CANARYKEY-0123456789abcdef/+=q"
SIG = "CANARYSIGdeadbeefcafebabe0123"
CRED = "AKIACANARYCREDENTIAL"
TOKEN = "CANARYPATHTOKEN9876"                  # B5: a token embedded in the path itself
BASETOK = "CANARYBASENAMETOK5432"               # B6: the secret IS the file name
HOSTTOK = "canaryhosttok1234"                   # B6: the secret is a tunnel subdomain
QUERY = f"X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential={CRED}&X-Amz-Signature={SIG}"
VIDEO_URL = f"https://r3-bucket.invalid/private/clip.mp4?{QUERY}"     # documented positional input
UPLOAD_URL = f"https://upload-bucket.invalid/org1/up_0000?{QUERY}"    # signed PUT destination
# B1: a quote and a backslash inside the query ended the old pattern's match early.
QUOTED_URL = f"https://r3-bucket.invalid/private/clip.mp4?sig='{SIG}&x=\\{CRED}"
# B2: userinfo with a quote, and userinfo longer than the old 300-character bound.
USERINFO_URL = f"https://user'{SIG}:{CRED}@r3-bucket.invalid/private/clip.mp4"
LONG_USERINFO_URL = f"https://{'u' * 400}{SIG}@r3-bucket.invalid/private/clip.mp4"
# B3: a path longer than the old 2000-character bound, with the signature after it.
LONG_PATH_URL = f"https://r3-bucket.invalid/{'d/' * 1200}clip.mp4?sig={SIG}"
# B5: the token is in a path SEGMENT.
TOKEN_PATH_URL = f"https://r3-bucket.invalid/{TOKEN}/private/clip.mp4"
# B6: a capability URL whose secret is the basename, and one whose secret is the host.
BASENAME_TOKEN_URL = f"https://cdn.invalid/v/{BASETOK}.mp4"
HOSTNAME_TOKEN_URL = f"https://{HOSTTOK}.trycloudflare.invalid/clip.mp4"
CANARIES = (SIG, CRED, TOKEN, BASETOK, HOSTTOK, "X-Amz-Signature", "X-Amz-Credential",
            "r3-bucket.invalid", "cdn.invalid", "trycloudflare")


def label_of(url):
    """What the client must emit for a URL: a digest, plus an allowlisted extension."""
    return bench.video_label(url)


LABEL = label_of(VIDEO_URL)                    # url-<12 hex>.mp4, no text from the URL


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


body_response = body          # alias: some tests use `body` as a local variable


def escaped(text):
    """The key as \\uXXXX escapes: invisible in the undecoded body, real after json.loads."""
    return "".join("\\u%04x" % ord(c) for c in text)


# Every outcome a request can end in, as fake-gateway keyword arguments. The 4xx/5xx
# bodies echo the key and the signed URL the way a real gateway's error text does,
# including every shape the reviewer got through: quotes and backslashes in the query,
# an oversized userinfo and path, an uppercase or scheme-less host, escape-encoded and
# percent-encoded keys, the key's BODY rather than its prefix, and non-dict bodies.
OUTCOMES = {
    "200_stream": {},
    "200_truncated": {"truncate_stream": True},
    "200_hostile_headers_and_finish_reason": {"hostile_fields": f"{KEY}/{SIG}"},
    "401_echo_key_and_url": {"chat_override": body(
        401, {"error": {"code": f"bad_key:{KEY}", "type": "auth",
                        "message": f"key {KEY} may not read {VIDEO_URL}"}})},
    "401_echo_uppercase_and_schemeless_url": {"chat_override": body(
        401, {"error": {"code": f"upload-bucket.invalid/org1/o?{QUERY}",
                        "message": f"fetch failed HTTPS://R3-BUCKET.INVALID/private/clip.mp4?{QUERY}"}})},
    "401_quote_and_backslash_in_query": {"chat_override": body(
        401, {"error": {"message": f"denied for {QUOTED_URL}", "code": "x"}})},
    "401_escaped_slash_url": {"chat_override": body(
        401, '{"error": {"message": "denied for https:\\/\\/r3-bucket.invalid\\/p\\/c.mp4?sig='
             + SIG + '", "code": "x"}}')},
    "401_oversized_userinfo_and_path": {"chat_override": body(
        401, {"error": {"message": f"{LONG_USERINFO_URL} then {LONG_PATH_URL}", "code": "y"}})},
    "401_key_body_and_suffix": {"chat_override": body(
        401, {"error": {"message": f"got {KEY[9:]} and {KEY[1:]} and {KEY[5:25]}", "code": "z"}})},
    "401_escaped_and_percent_encoded_key": {"chat_override": body(
        401, '{"error": {"message": "' + escaped(KEY) + " "
             + KEY.replace("/", "%2F").replace("+", "%2B").replace("=", "%3D")
             + '", "code": "w"}}')},
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
    "unretrieved_task_exception": {"chat_override": raises(
        httpx.RemoteProtocolError, f"peer closed while streaming {VIDEO_URL} key {KEY}")},
    "upload_put_403": {"upload_url": UPLOAD_URL, "put_status": 403},
    "sse_error_event": {"stream_error": {"code": f"mid_stream:{KEY}",
                                         "message": f"aborted fetching {VIDEO_URL}"}},
}

# Every input shape: the four documented forms over corpus clips, and the same forms over
# a signed positional http(s) URL. `upload` with a URL cannot read bytes, which is the
# point: the failure quotes the URL. The B1/B2/B3/B5 URL shapes get their own test below.
FORMS = [("text", False), ("video_b64", False), ("upload", False), ("video_url", False),
         ("text", True), ("video_b64", True), ("upload", True), ("video_url", True)]

# (accepted, rejected, failed, error class) each outcome really produces on the
# (video_b64, corpus) row. Without this the sweep could pass by emitting nothing at all.
# `429_list_body` is rejected, not failed: err.get() on a list body used to raise.
EXPECT = {
    "200_stream": (2, 0, 0, None),
    "200_truncated": (0, 0, 2, "truncated_stream"),
    "200_hostile_headers_and_finish_reason": (2, 0, 0, None),
    "401_echo_key_and_url": (0, 2, 0, "http_401"),
    "401_echo_uppercase_and_schemeless_url": (0, 2, 0, "http_401"),
    "401_quote_and_backslash_in_query": (0, 2, 0, "http_401"),
    "401_escaped_slash_url": (0, 2, 0, "http_401"),
    "401_oversized_userinfo_and_path": (0, 2, 0, "http_401"),
    "401_key_body_and_suffix": (0, 2, 0, "http_401"),
    "401_escaped_and_percent_encoded_key": (0, 2, 0, "http_401"),
    "401_escaped_key_straddling_the_cut": (0, 2, 0, "http_401"),
    "401_non_json_body_straddling_the_cut": (0, 2, 0, "http_401"),
    "402_string_error_body": (0, 2, 0, "http_402"),
    "429_list_body": (0, 2, 0, "http_429"),
    "429_then_retry": (2, 0, 0, None),
    "500_echo_key_and_url": (0, 0, 2, "http_500"),
    "307_redirect_to_signed_url": (0, 0, 2, "http_307"),
    "connect_error": (0, 0, 2, "ConnectError"),
    "read_timeout": (0, 0, 2, "ReadTimeout"),
    "unretrieved_task_exception": (0, 0, 2, "RemoteProtocolError"),
    "upload_put_403": (2, 0, 0, None),          # no PUT happens on the video_b64 row
    "sse_error_event": (0, 0, 2, "stream_error_event"),
}


def run(tmp, form, positional, gw_kwargs, requests=2, video=None, extra=(), key=None):
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
    argv += [video or VIDEO_URL] if positional else ["--corpus", "unused"]
    if form == "video_url" and not positional:
        argv += ["--media-base-url", "https://media.invalid/clips"]
    argv += list(extra)

    gw = FakeGateway(ttft=0.001, token_gap=0.0, tokens=2, **gw_kwargs)
    bench.load_transport = lambda spec: gw.transport()
    os.environ["MARLIN_API_KEY"] = key or KEY
    sout, serr = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(sout), contextlib.redirect_stderr(serr):
            try:
                bench.main(argv)
            except SystemExit as e:                  # a sys.exit() string is output too
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


def key_substrings(key, least=8):
    """Every substring of the key of `least` characters or more — not just prefixes."""
    return [key[i:j] for i in range(len(key) - least + 1) for j in range(i + least, len(key) + 1)]


SUBSTRINGS = key_substrings(KEY)


def assert_clean(blobs, why):
    for where, blob in blobs.items():
        for canary in CANARIES:
            assert canary not in blob, f"{why}: {canary} leaked into {where}"
        for piece in SUBSTRINGS:
            assert piece not in blob, \
                f"{why}: {len(piece)} key characters ({piece[:4]}…) leaked into {where}"


def assert_parses(blobs, why):
    """Every emitted JSONL line must still be a JSON object: a redaction that mangles a
    line into invalid JSON would be a different kind of failure, not a success."""
    for where in ("out", "raw"):
        for line in blobs[where].splitlines():
            if line.strip():
                assert isinstance(json.loads(line), dict), f"{why}: unparseable {where} line"


# ------------------------------------------------------------------ tests


def test_no_form_and_no_outcome_can_emit_a_canary():
    """8 input shapes x 22 outcomes through the real client, checking all four sinks."""
    runs = 0
    with tempfile.TemporaryDirectory() as tmp:
        with_clips(make_clips(4, tmp))
        for form, positional in FORMS:
            for name, gw_kwargs in OUTCOMES.items():
                blobs, summary = run(tmp, form, positional, gw_kwargs)
                why = f"{form}{'(url)' if positional else ''} / {name}"
                assert blobs["stdout"].strip(), f"{why}: produced no summary at all"
                assert_clean(blobs, why)
                assert_parses(blobs, why)
                if positional:                   # the round-3 finding, on every outcome
                    assert summary["video"] == LABEL, why
                if (form, positional) == ("video_b64", False):     # the outcome really happened
                    got = (summary["accepted"], summary["rejected"], summary["failed"],
                           (sorted(summary["error_classes"]) or [None])[0])
                    assert got == EXPECT[name], (why, got, EXPECT[name])
                if form == "upload" and name == "upload_put_403" and not positional:
                    assert summary["error_classes"] == {"UploadFailed": 2}, why
                    assert '"upload_status": 403' in blobs["raw"], "the PUT status must survive"
                runs += 1
    assert runs == len(FORMS) * len(OUTCOMES) == 176, runs


def test_every_adversarial_url_shape_yields_a_rebuilt_label():
    """B1/B2/B3/B5: quotes and backslashes in the query, oversized userinfo and path, and a
    token in a path segment. The label is rebuilt from parsed parts, so none of them
    reaches any sink — no pattern has to match the URL for that to hold."""
    shapes = {"signed query": VIDEO_URL, "quote and backslash in query": QUOTED_URL,
              "userinfo with a quote": USERINFO_URL, "oversized userinfo": LONG_USERINFO_URL,
              "oversized path": LONG_PATH_URL, "token in a path segment": TOKEN_PATH_URL,
              "token IS the basename": BASENAME_TOKEN_URL,
              "token IS the hostname": HOSTNAME_TOKEN_URL}
    seen = set()
    with tempfile.TemporaryDirectory() as tmp:
        for why, url in shapes.items():
            want = label_of(url)
            assert re.fullmatch(r"url-[0-9a-f]{12}(\.mp4)?", want), (why, want)
            blobs, summary = run(tmp, "video_url", True, {}, video=url)
            assert summary["accepted"] == 2, why
            assert summary["video"] == want, (why, summary["video"])
            rows = [json.loads(l) for l in blobs["raw"].splitlines()]
            assert rows and all(r["clip_id"] == want for r in rows), why
            assert json.loads(blobs["out"].splitlines()[-1])["video"] == want, why
            assert_clean(blobs, why)
            assert_parses(blobs, why)
            seen.add(want)
    assert len(seen) == len(shapes), "distinctness for E4 comes from the digest"


def test_video_label_is_a_digest_with_no_text_from_the_url():
    """B6: host and basename are secrets in capability URLs, so neither is recorded."""
    label = bench.video_label
    for url in (VIDEO_URL, QUOTED_URL, USERINFO_URL, LONG_USERINFO_URL, LONG_PATH_URL,
                TOKEN_PATH_URL, BASENAME_TOKEN_URL, HOSTNAME_TOKEN_URL,
                "https://h.invalid/a/b/c.mp4", "https://h.invalid:8443/c.mp4",
                f"https://h.invalid/c.mp4#token={SIG}", "https://h.invalid/"):
        got = label(url)
        assert re.fullmatch(r"url-[0-9a-f]{12}(\.(mp4|m4v|webm|mov|mpeg|mpg))?", got), (url, got)
        for piece in (BASETOK, HOSTTOK, SIG, CRED, TOKEN, "h.invalid", "cdn.invalid"):
            assert piece not in got, (url, piece)
        assert label(url) == got, "the digest must be stable"
    # stable, per-URL distinct, and only an allowlisted extension may ride along
    assert label("https://h.invalid/x.mp4") != label("https://h.invalid/y.mp4")
    assert label("https://h.invalid/x.mp4").endswith(".mp4")
    assert label("https://h.invalid/x.MOV").endswith(".mov"), "case-folded extension"
    for hostile_ext in ("https://h.invalid/x.exe", "https://h.invalid/x.mp4.sh",
                        f"https://h.invalid/x.{SIG}", "https://h.invalid/x"):
        assert re.fullmatch(r"url-[0-9a-f]{12}", label(hostile_ext)), hostile_ext
    # the query and fragment change the digest (they are part of the URL identity)
    assert label("https://h.invalid/x.mp4") != label("https://h.invalid/x.mp4?v=2")
    assert label("/mnt/nvme/clips/c000.mp4") == "c000.mp4", "local files keep the basename"
    assert label("clip.mp4") == "clip.mp4"


def test_no_server_controlled_string_is_recorded_verbatim():
    """The allowlists themselves: a hostile finish_reason, Inference-Id, Retry-After,
    Server-Timing name and usage count are each replaced, not copied."""
    with tempfile.TemporaryDirectory() as tmp:
        with_clips(make_clips(2, tmp))
        blobs, summary = run(tmp, "video_b64", False, {"hostile_fields": f"{KEY}/{SIG}"})
        rows = [json.loads(l) for l in blobs["raw"].splitlines()]
        assert rows and summary["accepted"] == 2
        for r in rows:
            assert r["finish_reason"] == "unrecognized"      # not [a-z0-9_]{1,64}
            assert r["inference_id"] == "unrecognized"       # not [A-Za-z0-9-]{1,64}
            assert r["retry_after"] is None                  # not numeric
            assert r["prompt_tokens"] is None and r["completion_tokens"] is None   # not ints
            assert r["server_timing"] == {"queue": 2.5, "dropped_metrics": 1}
        assert_clean(blobs, "hostile header and stream fields")
        # and the well-formed forms still come through untouched
        blobs, summary = run(tmp, "video_b64", False, {})
        rows = [json.loads(l) for l in blobs["raw"].splitlines()]
        assert all(r["finish_reason"] == "stop" and len(r["inference_id"]) == 32 for r in rows)
        assert all(r["server_timing"] == {"queue": 12.5, "prep": 340.0, "gpu": 880.25} for r in rows)
        assert all(r["prompt_tokens"] == 2061 for r in rows)


def test_an_allowlisted_field_carrying_the_key_is_still_refused():
    """n1: a lower-cased key body fullmatches [a-z0-9_]{1,64}, so the pattern alone would
    have recorded it. allow() runs the same 8-gram key check the argv gate uses."""
    body = bench.fold(KEY[9:])                    # the secret body, slug-folded, no punctuation
    assert bench.CODE_OK.fullmatch(body.replace("-", "_")), "the shape really is allowlistable"
    with tempfile.TemporaryDirectory() as tmp:
        with_clips(make_clips(2, tmp))
        for why, gw, field in (
                ("error.code", {"chat_override": body_response(
                    401, {"error": {"code": body.replace("-", "_"), "type": "auth"}})}, "error_code"),
                ("error.type", {"chat_override": body_response(
                    401, {"error": {"code": "auth", "type": body.replace("-", "_")}})}, "error_type"),
                ("finish_reason / headers", {"hostile_fields": body.replace("-", "_")}, "finish_reason"),
                ("uppercased key body", {"chat_override": body_response(
                    401, {"error": {"code": KEY[9:].lower().replace("/", "_").replace("+", "_")
                                    .replace("=", "_"), "type": "auth"}})}, "error_code")):
            blobs, summary = run(tmp, "video_b64", False, gw)
            rows = [json.loads(l) for l in blobs["raw"].splitlines()]
            assert rows, why
            assert all(r[field] == "unrecognized" for r in rows), (why, rows[0][field])
            assert_clean(blobs, why)
        # an Inference-Id and a Server-Timing metric name carrying the body go the same way
        blobs, summary = run(tmp, "video_b64", False,
                             {"hostile_fields": "x" * 0 + body.replace("-", "")})
        rows = [json.loads(l) for l in blobs["raw"].splitlines()]
        assert all(r["inference_id"] == "unrecognized" for r in rows)
        assert all("dropped_metrics" in (r["server_timing"] or {}) for r in rows)
        assert_clean(blobs, "hostile id and timing name")


def test_the_argv_gate_ignores_the_public_key_prefix():
    """n3: `sk-infrx-` is public boilerplate; `apps/infrx-api/...` must not refuse a run,
    while 6+ characters of the secret body still must."""
    key = "sk-infrx-" + "SECRETBODY0123456789"
    for public in ("apps/infrx-api/tests", "out/infrx-run.jsonl", "sk-infrx-", "infrx-api",
                   "/home/x/.claude/worktrees/infrx-impl/out.jsonl"):
        assert not bench.carries_key(public, key), public
    for secret in ("run-SECRETBO", "x/secretbody0123", key, key[9:], key[:17],
                   "out/bench-" + key[12:22] + ".jsonl"):
        assert bench.carries_key(secret, key), secret
    # fail-closed for a key with no known prefix: every window counts
    odd = "zz-custom-ABCDEFGH1234"
    assert bench.carries_key("zz-custom-ABCDEFGH", odd) and bench.carries_key(odd[:9], odd)
    with tempfile.TemporaryDirectory() as tmp:
        with_clips(make_clips(2, tmp))
        os.environ["MARLIN_API_KEY"] = key
        try:
            blobs, summary = run(tmp, "video_b64", False, {}, extra=["--label", "infrx api run"],
                                 key=key)
            assert summary is not None and summary["label"] == "infrx-api-run"
            blobs, summary = run(tmp, "video_b64", False, {},
                                 extra=["--label", f"run {key[9:20]}"], key=key)
            assert summary is None and "refusing to run" in blobs["stderr"]
        finally:
            os.environ.pop("MARLIN_API_KEY", None)


def test_an_argv_value_carrying_the_key_refuses_to_run():
    """N2/B4: --label and --out become file names and summary fields, and any argv value
    can be echoed by argparse. A value carrying 8+ key characters — literally, folded to a
    slug, or in a different case — exits 2 naming only the flag."""
    with tempfile.TemporaryDirectory() as tmp:
        for why, extra, flag in (
                ("label holds the key", ["--label", f"run {KEY}"], "--label"),
                ("label holds a slugged key", ["--label", f"run-{bench.fold(KEY)}"], "--label"),
                ("label holds the key's body", ["--label", KEY[9:]], "--label"),
                ("label differs in case", ["--label", f"run-{KEY.upper()}"], "--label"),
                ("media base url holds it", ["--media-base-url", f"https://m.invalid/{KEY[4:20]}"],
                 "--media-base-url")):
            blobs, summary = run(tmp, "text", True, {}, extra=extra)
            assert summary is None, f"{why}: the run must not start"
            assert "refusing to run" in blobs["stderr"] and flag in blobs["stderr"], why
            assert_clean(blobs, why)
        # an --out path carrying the key is refused before the file is created
        out = os.path.join(tmp, f"bench-{KEY}.jsonl")
        blobs, _ = run(tmp, "text", True, {}, extra=["--out", out])
        assert "refusing to run" in blobs["stderr"] and "--out" in blobs["stderr"]
        assert not os.path.exists(out), "no file may be created from a key-bearing path"
        assert_clean(blobs, "--out path carrying the key")
        # a clean label still runs, and is slugged into the summary and the raw file name
        blobs, summary = run(tmp, "text", True, {}, extra=["--label", "L40S run 3"])
        assert summary["label"] == "l40s-run-3"


def test_library_logging_cannot_print_a_message_or_a_url():
    """N1: asyncio's 'Task exception was never retrieved' handler prints the exception
    MESSAGE, and httpx logs request URLs. Muted to name and level only."""
    bench.mute_library_logging()
    import logging
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        for name in ("asyncio", "httpx", "httpcore"):
            log = logging.getLogger(name)
            assert log.propagate is False and log.level == logging.WARNING, name
            log.error("connecting to %s with Bearer %s", VIDEO_URL, KEY)
    printed = buf.getvalue()
    assert printed.strip(), "the logger must still say that it spoke"
    assert_clean({"stderr": printed}, "library logging")
    assert "ERROR" in printed and "asyncio" in printed


def test_a_toplevel_failure_prints_frames_without_a_message():
    """Exception text may quote the request URL; only the class and the frames may print."""
    with tempfile.TemporaryDirectory() as tmp:
        def boom(*a, **kw):
            raise RuntimeError(f"cannot schedule {VIDEO_URL} with {KEY}")
        saved, bench.build_schedule = bench.build_schedule, boom
        try:
            blobs, summary = run(tmp, "text", True, {})
        finally:
            bench.build_schedule = saved
        assert summary is None
        assert "bench failed: RuntimeError" in blobs["stderr"]
        assert "bench.py:" in blobs["stderr"], "frames as file:line:function"
        assert "cannot schedule" not in blobs["stderr"], "no exception message"
        assert_clean(blobs, "top-level failure")


def test_rows_are_on_disk_as_they_finish_and_an_interrupt_is_marked():
    """N3: rows used to be written only after the last request, so a Ctrl-C lost them all."""
    with tempfile.TemporaryDirectory() as tmp:
        with_clips(make_clips(4, tmp))
        seen = []

        def peek(request):
            # Read the raw file from inside the run: earlier rows must already be there.
            path = os.path.join(tmp, "raw.jsonl")
            seen.append(len(open(path, encoding="utf-8").read().splitlines())
                        if os.path.exists(path) else 0)
            return None

        blobs, summary = run(tmp, "video_b64", False, {"chat_override": peek}, requests=4)
        assert summary["accepted"] == 4 and summary["interrupted"] is False
        assert seen == [0, 1, 2, 3], f"rows must be flushed as they complete, saw {seen}"

        # A KeyboardInterrupt mid-run keeps the completed rows and marks the summary.
        def interrupt(request):
            if len(seen) > 4:
                raise KeyboardInterrupt()
            seen.append("x")
            return None

        blobs, summary = run(tmp, "video_b64", False, {"chat_override": interrupt}, requests=6)
        assert summary["interrupted"] is True, "a partial run must not read as a complete one"
        assert summary["requests"] < 6 and summary["accepted"] >= 1
        rows = [json.loads(l) for l in blobs["raw"].splitlines()]
        assert len(rows) == summary["attempts"] >= 1, "the completed rows survived the interrupt"
        assert json.loads(blobs["out"].splitlines()[-1])["interrupted"] is True
        assert_clean(blobs, "interrupted run")


def test_a_second_ctrl_c_cannot_lose_the_summary():
    """n5: SIGINT is held for the length of one row write and of the final summary write,
    so an impatient second Ctrl-C cannot truncate either. Exit is still 130."""
    import signal as sig
    state = {"interrupted": False}
    with bench.sigint_deferred(state):
        os.kill(os.getpid(), sig.SIGINT)         # would raise KeyboardInterrupt unprotected
        os.kill(os.getpid(), sig.SIGINT)         # the impatient second one
        landed = "still running"
    assert landed == "still running", "the write must complete"
    assert state["interrupted"] is True, "and the interrupt must not be swallowed"
    assert sig.getsignal(sig.SIGINT) is sig.default_int_handler, "handler restored"

    # end to end, in a real process: two signals, summary and rows still written
    with tempfile.TemporaryDirectory() as tmp:
        clip = os.path.join(tmp, "c.mp4")
        with open(clip, "wb") as f:
            f.write(b"\x00\x00\x00 ftypisom" + b"\x01" * 4096)
        out, raw = os.path.join(tmp, "bench.jsonl"), os.path.join(tmp, "raw.jsonl")
        env = dict(os.environ, MARLIN_API_KEY=KEY, PYTHONDONTWRITEBYTECODE="1")
        # 400 requests at ~20/s cannot finish, and we wait for real rows instead of
        # sleeping a guessed interval: no race either way.
        proc = subprocess.Popen(
            [sys.executable, os.path.join(os.path.dirname(HERE), "bench.py"), clip,
             "-c", "1", "-n", "400", "--prompt", "p", "--no-warmup", "--target", "gateway",
             "--forms", "video_b64", "--out", out, "--raw", raw,
             "--dry-run-transport", "fake_gateway:transport"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            rows_now = (len(open(raw, encoding="utf-8").read().splitlines())
                        if os.path.exists(raw) else 0)
            if rows_now >= 2:
                break
            assert proc.poll() is None, f"the run ended before we could signal it ({proc.poll()})"
            time.sleep(0.02)
        else:
            proc.kill()
            raise AssertionError("no raw row appeared within 30 s")
        proc.send_signal(signal.SIGINT)
        time.sleep(0.02)
        proc.send_signal(signal.SIGINT)          # the second one, during the write window
        stdout, stderr = proc.communicate(timeout=30)
        assert proc.returncode == 130, (proc.returncode, stderr[-300:])
        assert os.path.exists(out), "the summary line must exist"
        summary = json.loads(open(out, encoding="utf-8").read().splitlines()[-1])
        assert summary["interrupted"] is True and summary["attempts"] >= 1
        rows = [json.loads(l) for l in open(raw, encoding="utf-8").read().splitlines()]
        assert len(rows) == summary["attempts"], "every completed row survived"
        assert_clean({"stdout": stdout, "stderr": stderr,
                      "out": open(out, encoding="utf-8").read(),
                      "raw": open(raw, encoding="utf-8").read()}, "double Ctrl-C")


def test_redact_is_defense_in_depth_over_any_key_substring():
    """redact() is no longer the guarantee, but it must still catch a key substring
    anywhere (not just a prefix) in a line some future field forgot to allowlist."""
    r = lambda s: bench.redact(s, KEY)
    assert r(KEY) == "[redacted-key]"
    for piece in (KEY[:8], KEY[9:], KEY[1:], KEY[5:25], KEY[-8:]):
        out = r(f"echo {piece} back")
        assert piece not in out, piece
        for sub in key_substrings(piece):        # and no 8+ run of it either
            assert sub not in out, (piece, sub)
    assert r(KEY[:7]) == KEY[:7], "shorter than 8 characters is not a key"
    assert r(f"see {VIDEO_URL}").endswith("?[redacted-query]")
    assert r(r(f"{KEY} {VIDEO_URL}")) == r(f"{KEY} {VIDEO_URL}"), "must be idempotent"
    assert bench.redact("", KEY) == "" and bench.redact("plain", "") == "plain"


def test_generative_urls_and_bodies_never_leak_and_always_parse():
    """Random adversarial URLs and server bodies through the real client: quotes,
    backslashes, unicode, very long userinfo/path/query, nested URLs inside the query.
    Zero canary bytes in any sink, and every emitted JSONL line still parses."""
    rng = random.Random(20260921)
    alphabet = "'\"\\ \t{}[]:,=&?#/@%<>|^`~;" + string.ascii_letters + "0123456789" + "é漢🙂"
    noise = lambda n: "".join(rng.choice(alphabet) for _ in range(n))
    with tempfile.TemporaryDirectory() as tmp:
        with_clips(make_clips(2, tmp))
        for i in range(24):
            # the URL is built from non-key canaries (a key in argv is refused outright,
            # tested separately); the server body carries the key itself
            canary = rng.choice([SIG, CRED, TOKEN])
            body_canary = rng.choice([KEY, KEY[rng.randrange(1, 9):], SIG, CRED, TOKEN])
            host = rng.choice(["r3-bucket.invalid", "R3-BUCKET.INVALID", "h.invalid:8443",
                               f"{HOSTTOK}.trycloudflare.invalid",       # B6: secret host
                               f"{'h' * rng.randrange(1, 40)}.invalid"])
            userinfo = rng.choice(["", f"u'{canary}@", f"{'u' * rng.randrange(1, 500)}{canary}@"])
            path = rng.choice(["/private/clip.mp4", f"/{canary}/clip.mp4",
                               f"/v/{BASETOK}.mp4",                      # B6: secret basename
                               f"/{'d/' * rng.randrange(1, 1500)}clip.mp4",
                               f"/clip{noise(3)}.mp4"])
            query = rng.choice(["", f"?sig={canary}", f"?a='{canary}&b=\\{canary}",
                                f"?next=https%3A%2F%2Fx.invalid%2F{canary}", f"?{noise(40)}={canary}"])
            url = f"https://{userinfo}{host}{path}{query}"
            shape = rng.randrange(0, 4)
            if shape == 0:
                gw = {"chat_override": body(401, {"error": {"code": noise(30), "message":
                                                            f"{noise(20)} {url} {body_canary} {noise(20)}"}})}
            elif shape == 1:
                gw = {"chat_override": body(500, f"{noise(50)}{url}{escaped(body_canary)}{noise(10)}")}
            elif shape == 2:
                gw = {"chat_override": raises(httpx.ConnectError, f"connect {url} {body_canary}")}
            else:
                # hostile_fields plants the value in Inference-Id / finish_reason / usage /
                # Server-Timing. The ruling lets an Inference-Id be any opaque
                # [A-Za-z0-9-]{1,64}, so a KEY-derived value is what must be suppressed
                # here (allow() checks the key); see the documented residual in
                # test_an_opaque_server_id_is_a_documented_residual.
                gw = {"hostile_fields": f"{rng.choice([KEY, KEY[9:], KEY[1:]])}{noise(5)}"}
            positional = bool(i % 2)
            blobs, summary = run(tmp, "video_url" if positional else "video_b64", positional, gw,
                                 video=url if positional else None)
            why = f"generative #{i} shape {shape}"
            assert_clean(blobs, why)
            assert_parses(blobs, why)
            assert summary is not None, (why, blobs["stderr"][:200])
            if positional:
                # structurally: a digest of the whole URL, plus an allowlisted extension
                v = summary["video"]
                assert re.fullmatch(r"url-[0-9a-f]{12}(\.(mp4|m4v|webm|mov|mpeg|mpg))?", v), (why, v)
                assert v == bench.video_label(url), (why, v)


def test_an_opaque_server_id_is_a_documented_residual():
    """The known boundary of the ruling's allowlist, asserted rather than left implicit.

    `Inference-Id` may be any opaque [A-Za-z0-9-]{1,64}, and a request id is
    indistinguishable from a signature-shaped token, so a gateway that echoes one there
    HAS it recorded. A key-derived value is still suppressed, and anything outside the
    pattern still becomes "unrecognized". Recorded in the evidence report under Limits."""
    with tempfile.TemporaryDirectory() as tmp:
        with_clips(make_clips(2, tmp))
        blobs, _ = run(tmp, "video_b64", False, {"extra_headers": {"inference-id": SIG}})
        rows = [json.loads(l) for l in blobs["raw"].splitlines()]
        assert all(r["inference_id"] == SIG for r in rows), "documented residual, not a surprise"
        # but the same value one character outside the pattern, or carrying the key, does not
        blobs, _ = run(tmp, "video_b64", False, {"extra_headers": {"inference-id": SIG + "/x"}})
        rows = [json.loads(l) for l in blobs["raw"].splitlines()]
        assert all(r["inference_id"] == "unrecognized" for r in rows)
        blobs, _ = run(tmp, "video_b64", False, {"extra_headers": {"inference-id": KEY[9:25]}})
        rows = [json.loads(l) for l in blobs["raw"].splitlines()]
        assert all(r["inference_id"] == "unrecognized" for r in rows)
        assert_clean(blobs, "key body echoed as an Inference-Id")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok {t.__name__}")
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    main()
