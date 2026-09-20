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

KEY = "sk-test-DO-NOT-LOG-4c2f9b1e"


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
        rows = {}
        for tag, ttft in (("fast", 0.001), ("slow", 0.12)):
            summary, raw, _, _ = run_bench(base_argv(tmp, requests=12, rate=8, seed=11),
                                           FakeGateway(ttft=ttft), env={"MARLIN_API_KEY": KEY})
            rows[tag] = [(r["seq"], r["clip_id"], r["form"], r["scheduled_s"], r["cold"])
                         for r in sorted(raw, key=lambda r: r["seq"])]
            assert summary["accepted"] == 12
        assert rows["fast"] == rows["slow"], "arrival schedule must not depend on response latency"


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
        assert all("[redacted]" in (r["error_message"] or "") for r in raw)
        for blob in (stdout, json.dumps(raw)):
            assert KEY not in blob


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
                                           "failed_excluded": 2, "scheduled": 8}
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
