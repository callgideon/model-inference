#!/usr/bin/env python3
"""Regression tests for bench.py. No network: every request goes to the in-process
fake gateway. Run with pytest or as a plain script:

    python -m pytest models/marlin2b/tests -q
    python models/marlin2b/tests/test_bench.py
"""
import contextlib, io, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]

import bench
from fake_gateway import FakeGateway

REAL_LOAD_CORPUS = bench.load_corpus            # with_clips() replaces it for the other tests
MANIFEST_PATH = os.path.join(os.path.dirname(HERE), "corpus", "manifest.json")

KEY = "sk-test-DO-NOT-LOG-4c2f9b1e"
# canary signed upload destination: the query string is what must never be written
SIGNED_URL = ("https://bucket.invalid/org1/up_0000?X-Amz-Algorithm=AWS4-HMAC-SHA256"
              "&X-Amz-Credential=CANARYCRED&X-Amz-Signature=CANARYSIGdeadbeefcafebabe")
SIGNED_CANARIES = ("CANARYSIGdeadbeefcafebabe", "X-Amz-Signature", "CANARYCRED")


def make_clips(n, tmp):
    """Fake local media: bench only needs bytes, a duration and an id."""
    clips = []
    for i in range(n):
        path = os.path.join(tmp, f"clip{i:03d}.mp4")
        with open(path, "wb") as f:
            f.write(b"\x00\x00\x00 ftypisom" + bytes([i]) * 512)
        clips.append({"id": f"clip{i:03d}", "path": path, "file": f"clips/clip{i:03d}.mp4",
                      "duration_s": 2.0 + i, "width": 1920, "height": 1080, "aspect": "16:9",
                      "sha256": "0" * 64, "prompt_id": f"p{i % 4:02d}", "prompt": f"prompt {i % 4}",
                      "prompt_kind": ["caption", "find", "count", "motion"][i % 4]})
    return clips


def run_bench(argv, gateway, env=None):
    """Run bench.main() against a fake gateway, returning (summary, raw rows, stdout)."""
    bench.load_transport = lambda spec: gateway.transport()          # no network, no server
    old = {k: os.environ.get(k) for k in ("MARLIN_API_KEY", "INFRX_API_KEY", "MM_KWARGS")}
    os.environ.update({k: v for k, v in (env or {}).items()})
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = bench.main(argv)
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    out = buf.getvalue()
    summary = json.loads(out[out.index("{"):])
    out_path = argv[argv.index("--out") + 1]
    raw = os.path.join(os.path.dirname(out_path), summary["raw"])
    with open(raw, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]
    return summary, rows, out, rc


def base_argv(tmp, **kw):
    argv = ["--corpus", "unused", "--out", os.path.join(tmp, "bench.jsonl"),
            "--raw", os.path.join(tmp, "raw.jsonl"), "--dry-run-transport", "fake_gateway:transport",
            "--base-url", "http://fake.invalid/v1", "--target", "gateway", "--prompt", "p"]
    for k, v in kw.items():
        argv += [f"--{k.replace('_', '-')}"] + ([] if v is True else [str(v)])
    return argv


def with_clips(monkeypatched_clips):
    bench.load_corpus = lambda path, subset="fast": (monkeypatched_clips, {}, {"corpus_version": "test"})


# ------------------------------------------------------------------ tests


def test_schedule_is_deterministic_and_independent_of_latency():
    with tempfile.TemporaryDirectory() as tmp:
        clips = make_clips(8, tmp)
        key = lambda s: [(r["seq"], r["clip_id"], r["arrival_s"], r["form"], r["cold"], r["prompt"]) for r in s]
        a = bench.build_schedule(24, clips, ["video_b64", "text"], rate=5.0, seed=42)
        b = bench.build_schedule(24, clips, ["video_b64", "text"], rate=5.0, seed=42)
        assert key(a) == key(b), "same seed must give the same schedule"
        assert key(a) != key(bench.build_schedule(24, clips, ["video_b64", "text"], rate=5.0, seed=43))
        assert [r["arrival_s"] for r in a] == sorted(r["arrival_s"] for r in a)
        assert a[0]["cold"] is True and any(r["cold"] is False for r in a)

        with_clips(clips)
        rows, lag = {}, {}
        for tag, ttft in (("fast", 0.001), ("slow", 0.25)):
            summary, raw, _, _ = run_bench(base_argv(tmp, requests=12, rate=8, seed=11),
                                           FakeGateway(ttft=ttft), env={"MARLIN_API_KEY": KEY})
            rows[tag] = [(r["seq"], r["clip_id"], r["form"], r["scheduled_s"], r["cold"])
                         for r in sorted(raw, key=lambda r: r["seq"])]
            lag[tag] = summary["schedule_lag_s"]["max"]
            assert summary["accepted"] == 12
            # open loop measured, not just planned: sends track the schedule even when the
            # server is slow, and latency is also reported from the scheduled arrival.
            assert lag[tag] < 0.1, f"{tag}: sends waited for completions (lag {lag[tag]}s)"
            assert all(r["send_s"] >= r["scheduled_s"] for r in raw)
            assert all(r["latency_from_scheduled_s"] >= r["latency_s"] for r in raw)
            assert summary["percentiles"]["latency_from_scheduled_s"]["samples"] == 12
        assert rows["fast"] == rows["slow"], "arrival schedule must not depend on response latency"
        assert lag["slow"] < 0.25, "a 0.25 s server must not delay the next arrival"


def test_api_key_never_appears_in_any_output():
    with tempfile.TemporaryDirectory() as tmp:
        clips = make_clips(4, tmp)
        with_clips(clips)
        gw = FakeGateway(require_bearer=KEY)
        summary, raw, stdout, rc = run_bench(base_argv(tmp, requests=6, concurrency=2),
                                             gw, env={"MARLIN_API_KEY": KEY})
        assert summary["accepted"] == 6 and rc == 0
        assert all(s["authorization_present"] for s in gw.seen), "key must be sent as a Bearer header"
        blobs = [stdout, json.dumps(summary), json.dumps(raw),
                 open(os.path.join(tmp, "bench.jsonl"), encoding="utf-8").read(),
                 open(os.path.join(tmp, "raw.jsonl"), encoding="utf-8").read()]
        for blob in blobs:
            assert KEY not in blob, "api key leaked into client output"

        # an upstream exception that echoes the header must be scrubbed too
        summary, raw, stdout, rc = run_bench(base_argv(tmp, requests=2, concurrency=1),
                                             FakeGateway(raise_with_key=KEY), env={"INFRX_API_KEY": KEY})
        assert summary["failed"] == 2 and summary["accepted"] == 0
        assert all("[redacted-key]" in (r["error_message"] or "") for r in raw)
        for blob in (stdout, json.dumps(raw)):
            assert KEY not in blob


def all_output(tmp, summary, raw, stdout):
    return [stdout, json.dumps(summary), json.dumps(raw),
            open(os.path.join(tmp, "bench.jsonl"), encoding="utf-8").read(),
            open(os.path.join(tmp, "raw.jsonl"), encoding="utf-8").read()]


def assert_no_key_prefix(tmp, summary, raw, stdout, why):
    """Not even a usable prefix: a truncate-then-redact client leaves one behind."""
    for blob in all_output(tmp, summary, raw, stdout):
        for cut in range(8, len(KEY) + 1):
            assert KEY[:cut] not in blob, f"{why}: leaked {cut} of {len(KEY)} key characters"


def test_server_controlled_fields_cannot_leak_the_key_or_a_signed_url():
    """The paths that escaped per-field scrubbing: error.code, an error body or message
    with the key STRADDLING the truncation point (cut before redacting leaves a prefix,
    and a \\uXXXX-escaped key survives a scrub of the undecoded body), and httpx
    exception text quoting the signed upload URL."""
    with tempfile.TemporaryDirectory() as tmp:
        clips = make_clips(4, tmp)
        with_clips(clips)

        # (a) the key echoed back in error.code, which reaches summary["error_codes"]
        summary, raw, stdout, _ = run_bench(base_argv(tmp, requests=2, concurrency=1),
                                            FakeGateway(echo_key_in_code=KEY), env={"MARLIN_API_KEY": KEY})
        assert summary["rejected"] == 2 and "bad_key:[redacted-key]" in summary["error_codes"]
        for blob in all_output(tmp, summary, raw, stdout):
            assert KEY not in blob, "api key leaked through error.code"

        # (b) a non-JSON body with the key straddling the 200-character cut: truncating
        # before redacting leaks the first 15 characters, so no prefix may survive.
        summary, raw, stdout, _ = run_bench(base_argv(tmp, requests=2, concurrency=1),
                                            FakeGateway(echo_key_in_long_body=KEY),
                                            env={"MARLIN_API_KEY": KEY})
        assert summary["rejected"] == 2
        assert all(len(r["error_message"]) == 200 for r in raw), "the body must really be cut at 200"
        assert_no_key_prefix(tmp, summary, raw, stdout, "non-JSON body straddling the 200-char cut")

        # (b2) the same straddle inside error.message, with the key \uXXXX-escaped on the
        # wire so redacting the undecoded body cannot see it: json.loads hands back the
        # real key, so the parsed string must be redacted again before it is cut.
        summary, raw, stdout, _ = run_bench(base_argv(tmp, requests=2, concurrency=1),
                                            FakeGateway(echo_key_in_json_message=KEY, echo_pad=180,
                                                        escape_key=True), env={"MARLIN_API_KEY": KEY})
        assert summary["rejected"] == 2
        # pad 180 + the 14-char marker: the key began before the 200-char cut and was
        # replaced rather than sliced, so the field is 194 characters, not 200.
        assert all(r["error_message"] == "x" * 180 + "[redacted-key]" for r in raw)
        assert_no_key_prefix(tmp, summary, raw, stdout, "escaped key straddling the 200-char message cut")

        # (b3) and inside error.code, which is cut at 120 and becomes a summary key
        summary, raw, stdout, _ = run_bench(base_argv(tmp, requests=2, concurrency=1),
                                            FakeGateway(echo_key_in_code=KEY, echo_pad=102,
                                                        escape_key=True), env={"MARLIN_API_KEY": KEY})
        assert summary["rejected"] == 2
        assert all(len(r["error_code"]) == 120 for r in raw), "the code must really be cut at 120"
        assert_no_key_prefix(tmp, summary, raw, stdout, "escaped key straddling the 120-char code cut")

        # (c) a rejected PUT to a signed destination: status only, never the URL
        summary, raw, stdout, _ = run_bench(base_argv(tmp, requests=2, concurrency=1, forms="upload"),
                                            FakeGateway(upload_url=SIGNED_URL, put_status=403),
                                            env={"MARLIN_API_KEY": KEY})
        assert summary["failed"] == 2 and summary["accepted"] == 0
        assert all("HTTP 403" in (r["error_message"] or "") for r in raw)
        for blob in all_output(tmp, summary, raw, stdout):
            assert KEY not in blob
            for canary in SIGNED_CANARIES:
                assert canary not in blob, f"signed-URL canary {canary} leaked into output"
        # a raw apostrophe in the path must not let the query string through
        assert "CANARYSIG" not in bench.redact(
            "Client error for url 'https://s3.invalid/b/o'x/up_1?X-Amz-Signature=CANARYSIGaa'", KEY)


def test_distinct_clips_counts_only_clips_whose_media_was_sent():
    with tempfile.TemporaryDirectory() as tmp:
        clips = make_clips(4, tmp)
        with_clips(clips)
        summary, raw, _, _ = run_bench(base_argv(tmp, requests=8, concurrency=1,
                                                 forms="video_b64,text"), FakeGateway(),
                                       env={"MARLIN_API_KEY": KEY})
        text_rows = [r for r in raw if r["form"] == "text"]
        media_rows = [r for r in raw if r["form"] != "text"]
        assert text_rows and media_rows
        assert all(r["media_sent"] is False for r in text_rows), "a text slot sends no media"
        assert all(r["media_sent"] is True for r in media_rows)
        assert summary["distinct_clips"] == len({r["clip_id"] for r in media_rows})
        assert summary["distinct_clips"] < summary["distinct_clips_scheduled"], \
            "text slots must not inflate the distinct-media count"
        assert summary["distinct_clips"] == summary["cold_requests"], \
            "every distinct clip whose media was sent is exactly one cold request"


def test_truncated_200_stream_is_failed_not_accepted():
    with tempfile.TemporaryDirectory() as tmp:
        clips = make_clips(2, tmp)
        with_clips(clips)
        gw = FakeGateway(truncate_stream=True, usage=True)   # content, then silence
        summary, raw, _, rc = run_bench(base_argv(tmp, requests=2, concurrency=1), gw,
                                        env={"MARLIN_API_KEY": KEY})
        assert summary["accepted"] == 0 and summary["failed"] == 2 and rc == 1
        assert summary["error_classes"] == {"truncated_stream": 2}
        for r in raw:
            assert r["http_status"] == 200 and r["content_chars"] > 0
            assert r["stream_complete"] is False and r["finish_reason"] is None
        # a finish_reason alone (no [DONE], no usage) is a complete stream
        summary, raw, _, _ = run_bench(base_argv(tmp, requests=2, concurrency=1),
                                       FakeGateway(usage=False), env={"MARLIN_API_KEY": KEY})
        assert summary["accepted"] == 2 and all(r["finish_reason"] == "stop" for r in raw)


def test_retried_rejections_stay_visible_and_latency_covers_every_attempt():
    with tempfile.TemporaryDirectory() as tmp:
        clips = make_clips(2, tmp)
        with_clips(clips)
        gw = FakeGateway(statuses={0: 429}, retry_after="0", ttft=0.03)
        summary, raw, _, _ = run_bench(base_argv(tmp, requests=2, concurrency=1, retries=1), gw,
                                       env={"MARLIN_API_KEY": KEY})
        assert summary["accepted"] == 2 and summary["rejected"] == 0     # the retry succeeded
        assert summary["attempts"] == 3 and summary["attempt_status_counts"] == {"429": 1, "200": 2}
        assert summary["attempt_outcomes"] == {"rejected": 1, "accepted": 2}
        d = summary["denominators"]
        assert d["rejected_attempts"] == 1 and d["attempts"] == 3 and d["retried_requests"] == 1, d
        retried = [r for r in raw if r["retries"] == 1][0]
        assert retried["request_send_s"] < retried["send_s"], "request starts at the first attempt"
        assert retried["request_latency_s"] > retried["latency_s"], "retry wait must be in the latency"
        assert summary["percentiles"]["latency_s"]["samples"] == 2

        # Open loop: the Retry-After wait sits between a retry's scheduled_s and its
        # send_s, but it is not driver lag, so the summary block counts first attempts
        # only (E4 uses schedule_lag_s.max to validate an open-loop cell).
        summary, raw, _, _ = run_bench(base_argv(tmp, requests=4, concurrency=1, retries=1,
                                                rate=20, seed=3),
                                       FakeGateway(statuses={0: 429}, retry_after="1", ttft=0.01),
                                       env={"MARLIN_API_KEY": KEY})
        second = [r for r in raw if r["attempt"] == 1]
        assert second and second[0]["schedule_lag_s"] > 0.9, "the retry really waited"
        assert summary["schedule_lag_s"]["samples"] == 4, "one lag sample per request, not per attempt"
        assert summary["schedule_lag_s"]["max"] < 0.1, "a retry wait must not count as schedule lag"


def test_rejections_and_failures_are_counted_apart_from_accepted():
    with tempfile.TemporaryDirectory() as tmp:
        clips = make_clips(4, tmp)
        with_clips(clips)
        gw = FakeGateway(statuses={0: 429, 1: 402, 2: 500, 3: 503})
        summary, raw, _, _ = run_bench(base_argv(tmp, requests=8, concurrency=1), gw,
                                       env={"MARLIN_API_KEY": KEY})
        assert summary["accepted"] == 4, summary["status_counts"]
        assert summary["rejected"] == 2 and summary["failed"] == 2      # 429/402 rejected, 500/503 failed
        assert summary["status_counts"] == {"429": 1, "402": 1, "500": 1, "503": 1, "200": 4}
        assert summary["denominators"] == {"latency_samples": 4, "rejected_excluded": 2,
                                           "failed_excluded": 2, "scheduled": 8, "attempts": 8,
                                           "rejected_attempts": 2, "failed_attempts": 2,
                                           "retried_requests": 0}
        assert summary["error_codes"]["rate_limit_exceeded"] == 1
        assert summary["error_codes"]["insufficient_credit"] == 1
        by_seq = {r["seq"]: r for r in raw}
        assert by_seq[0]["retry_after"] == "3" and by_seq[0]["outcome"] == "rejected"
        assert by_seq[0]["ttft_s"] is None and by_seq[0]["completion_tokens"] is None
        assert summary["percentiles"]["ttft_s"]["samples"] == 4


def test_ttft_comes_from_first_content_delta_and_tokens_from_usage():
    with tempfile.TemporaryDirectory() as tmp:
        clips = make_clips(2, tmp)
        with_clips(clips)
        gw = FakeGateway(ttft=0.2, tokens=3, completion_tokens=77)
        summary, raw, _, _ = run_bench(base_argv(tmp, requests=2, concurrency=1), gw,
                                       env={"MARLIN_API_KEY": KEY})
        for r in raw:
            assert r["first_byte_s"] < r["first_token_s"] - 0.15, "TTFT must not use headers/role chunk"
            assert r["ttft_s"] >= 0.19
            assert r["completion_tokens"] == 77 and r["prompt_tokens"] == 2061   # usage, not chunk count
            assert r["content_chars"] == len("tok0 tok1 tok2 ")
            assert r["usage_missing"] is False
            assert r["server_timing"] == {"queue": 12.5, "prep": 340.0, "gpu": 880.25}
            assert r["inference_id"] and len(r["inference_id"]) == 32
        assert not any(s["has_mm_processor_kwargs"] for s in gw.seen), \
            "gateway target must not send mm_processor_kwargs"

        missing = FakeGateway(usage=False)
        summary, raw, _, _ = run_bench(base_argv(tmp, requests=2, concurrency=1), missing,
                                       env={"MARLIN_API_KEY": KEY})
        assert summary["accepted_without_usage"] == 2
        assert all(r["completion_tokens"] is None for r in raw)
        assert summary["out_tok_per_s"] == 0.0, "no usage means no counted tokens, never an estimate"


def test_percentiles_are_suppressed_when_samples_cannot_support_them():
    assert bench.min_samples(50) == 6 and bench.min_samples(95) == 60 and bench.min_samples(99) == 300
    v32 = [0.1 * i for i in range(32)]
    val, n, why = bench.percentile(v32, 50)
    assert val is not None and n == 32 and why is None
    for q in (95, 99):
        val, n, why = bench.percentile(v32, q)
        assert val is None and n == 32 and f"p{q} needs" in why
    with tempfile.TemporaryDirectory() as tmp:
        clips = make_clips(8, tmp)
        with_clips(clips)
        summary, _, _, _ = run_bench(base_argv(tmp, requests=32, concurrency=8),
                                     FakeGateway(), env={"MARLIN_API_KEY": KEY})
        assert summary["accepted"] == 32
        assert summary["ttft_p50"] is not None
        assert summary["ttft_p95"] is None and summary["latency_p95"] is None
        assert summary["percentiles"]["ttft_s"]["p99"] is None
        assert any("ttft_s p99 needs >= 300" in s for s in summary["suppressed_percentiles"])
        assert any("ttft_s p95 needs >= 60" in s for s in summary["suppressed_percentiles"])


def test_request_forms_and_upload_flow():
    with tempfile.TemporaryDirectory() as tmp:
        clips = make_clips(4, tmp)
        with_clips(clips)
        gw = FakeGateway()
        summary, raw, _, _ = run_bench(base_argv(tmp, requests=8, concurrency=1,
                                                 forms="text,video_b64,upload,video_url",
                                                 media_base_url="https://media.invalid/clips"), gw,
                                       env={"MARLIN_API_KEY": KEY})
        forms = {r["form"]: r for r in raw}
        assert set(forms) == {"text", "video_b64", "upload", "video_url"} and summary["accepted"] == 8
        assert forms["text"]["cold"] is None
        assert forms["upload"]["upload_s"] is not None and len(gw.uploads) == 2
        sent = {s["index"]: s["content"] for s in gw.seen}
        assert isinstance(sent[0], str), "text form sends a plain string content"
        refs = [p["video_url"]["url"] for parts in sent.values() if isinstance(parts, list)
                for p in parts if p["type"] == "video_url"]
        assert any(r.startswith("data:video/mp4;base64,") for r in refs)
        assert any(r.startswith("upload://up_") for r in refs)
        assert any(r == "https://media.invalid/clips/clip000.mp4" or
                   r.startswith("https://media.invalid/clips/") for r in refs)


def test_real_load_corpus_reads_the_committed_manifest():
    """Every other test monkeypatches load_corpus, so the real loader, the $CORPUS_CACHE
    resolution and the manifest's own field names are only exercised here."""
    saved, bench.load_corpus = bench.load_corpus, REAL_LOAD_CORPUS
    old_cache = os.environ.get("CORPUS_CACHE")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["CORPUS_CACHE"] = tmp
            clips, prompts, manifest = bench.load_corpus(MANIFEST_PATH, "fast")
            assert len(clips) >= 32 and len(prompts) == 16 and manifest["corpus_version"]
            assert len({c["id"] for c in clips}) == len(clips)
            assert all(c["path"].startswith(tmp) and c["prompt"] and c["duration_s"] > 0 for c in clips)
            geometries = {(c["width"], c["height"]) for c in clips}
            assert (1080, 1920) in geometries and (480, 1920) in geometries, \
                "the fast subset must carry the orientation extremes to the client"
            assert len(REAL_LOAD_CORPUS(MANIFEST_PATH, "full")[0]) >= 64
    finally:
        bench.load_corpus = saved
        os.environ.pop("CORPUS_CACHE", None) if old_cache is None else \
            os.environ.__setitem__("CORPUS_CACHE", old_cache)


def test_historical_cli_still_parses_and_refuses_command_line_keys():
    a = bench.parse_args(["video.mp4", "-c", "8", "-n", "32", "--max-tokens", "256", "--label", "L40S run"])
    assert (a.video, a.concurrency, a.requests, a.max_tokens, a.label) == ("video.mp4", 8, 32, 256, "L40S run")
    assert a.out == bench.DEFAULT_OUT
    assert a.out.endswith(os.path.join("models", "marlin2b", "results", "bench.jsonl"))
    assert a.mm_kwargs == "auto" and a.target == "direct" and a.rate is None
    os.environ["MM_KWARGS"] = '{"fps": 1.0}'
    try:
        assert bench.parse_args(["v.mp4"]).mm_kwargs == '{"fps": 1.0}'
    finally:
        del os.environ["MM_KWARGS"]
    for bad in (["v.mp4", "--api-key", "sk-x"], ["v.mp4", "--token=abc"], ["sk-live-abc"]):
        try:
            bench.parse_args(bad)
            raise AssertionError(f"accepted a key on argv: {bad}")
        except SystemExit:
            pass
    # direct target keeps the mm_processor_kwargs budget, gateway target drops it
    direct = bench.parse_args(["v.mp4", "--target", "direct", "--mm-kwargs", '{"fps": 2.0}'])
    assert bench.resolve_mm_kwargs(direct) == {"fps": 2.0}
    gateway = bench.parse_args(["v.mp4", "--target", "gateway", "--mm-kwargs", '{"fps": 2.0}'])
    assert bench.resolve_mm_kwargs(gateway) is None
    budget = bench.training_budget_kwargs(duration=10.0)
    assert budget["size"]["longest_edge"] == 20 * 200704 and budget["fps"] == 2.0


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok {t.__name__}")
    print(f"{len(tests)} passed")


if __name__ == "__main__":
    main()
