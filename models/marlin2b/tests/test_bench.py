#!/usr/bin/env python3
"""Regression tests for bench.py. No network: every request goes to the in-process
fake gateway. Run with pytest or as a plain script:

    python -m pytest models/marlin2b/tests -q
    python models/marlin2b/tests/test_bench.py
"""
import asyncio, contextlib, io, json, os, sys, tempfile

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
                      "duration_s": 2.0 + i, "width": 1920 - 640 * (i % 2),
                      "height": 1080 - 360 * (i % 2), "aspect": "16:9",
                      "codec": ["h264", "vp9"][i % 2], "fps": [30.0, 24.0][i % 2],
                      "frames": int((2.0 + i) * 30),
                      # distinct per clip: the item key folds in a content digest, and two
                      # clips sharing one would collide in it
                      "sha256": f"{i:064x}", "prompt_id": f"p{i % 4:02d}", "prompt": f"prompt {i % 4}",
                      "prompt_kind": ["caption", "find", "count", "motion"][i % 4]})
    return clips


@contextlib.contextmanager
def virtual_time(idle_rounds=8, tick=0.001):
    """Drive bench's drivers from a clock the test owns (E2 r1 review).

    The old assertions bounded scheduling lag in WALL-CLOCK seconds (`lag < 0.1`), which is a
    bound on how busy the host is, not on the driver: measured 0.234 s at load average 45 on
    16 cores, and it made `make check` flaky. With `bench.CLOCK`/`bench.SLEEP` injected, "the
    driver sent when the schedule said" becomes exact.

    The clock is EVENT-DRIVEN, not additive: a sleep registers a wake-up time and blocks, and
    the pump advances the clock to the EARLIEST pending wake-up once every runnable task has
    gone idle. An additive clock would be wrong here - a request's 1 s Retry-After wait would
    push the shared clock past every later arrival and manufacture the very lag these tests
    say cannot happen (measured while writing this: 1.156 s of phantom lag).
    """
    state = {"now": 0.0, "waiters": [], "pump": None}

    async def pump():
        idle = 0
        while True:
            await asyncio.sleep(tick)
            if not state["waiters"]:
                idle = 0
                continue
            # Only move time when nothing else can make progress, so a task that is about to
            # send is never overtaken by the clock. A request is not idle while it is still
            # preparing its media (`send_s` is stamped after that, on purpose - it is E1's
            # coordinated-omission measure), so the loop's own ready queue is the signal, with
            # the tick count as a fallback if a future CPython hides it.
            # This pump's own callback has already been popped to run us, so anything left in
            # the queue is another task that can still make progress.
            ready = getattr(asyncio.get_running_loop(), "_ready", None)
            if ready:
                idle = 0
                continue
            idle += 1
            if idle < idle_rounds:
                continue
            idle = 0
            earliest = min(wake for wake, _ in state["waiters"])
            state["now"] = max(state["now"], earliest)
            for waiter in [w for w in state["waiters"] if w[0] <= state["now"]]:
                state["waiters"].remove(waiter)
                waiter[1].set()

    async def sleep(seconds):
        if state["pump"] is None:
            state["pump"] = asyncio.get_running_loop().create_task(pump())
        wake = state["now"] + max(float(seconds), 0.0)
        if wake <= state["now"]:
            await asyncio.sleep(0)
            return
        event = asyncio.Event()
        state["waiters"].append([wake, event])
        await event.wait()

    old_clock, old_sleep = bench.CLOCK, bench.SLEEP
    bench.CLOCK, bench.SLEEP = (lambda: state["now"]), sleep
    try:
        yield state
    finally:
        bench.CLOCK, bench.SLEEP = old_clock, old_sleep


def run_bench(argv, gateway, env=None):
    """Run bench.main() against a fake gateway, returning (summary, raw rows, stdout)."""
    bench.load_transport = lambda spec: gateway.transport()          # no network, no server
    old = {k: os.environ.get(k) for k in
           ("MARLIN_API_KEY", "INFRX_API_KEY", "MM_KWARGS", *(env or {}))}
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
            with virtual_time():
                summary, raw, _, _ = run_bench(base_argv(tmp, requests=12, rate=8, seed=11),
                                               FakeGateway(ttft=ttft), env={"MARLIN_API_KEY": KEY})
            rows[tag] = [(r["seq"], r["clip_id"], r["form"], r["scheduled_s"], r["cold"])
                         for r in sorted(raw, key=lambda r: r["seq"])]
            lag[tag] = summary["schedule_lag_s"]["max"]
            assert summary["accepted"] == 12
            # Open loop MEASURED, not just planned. Bounded by ONE arrival slot, not by a
            # wall-clock constant: `send_s` is stamped after the media is prepared (that is
            # E1's coordinated-omission measure), so on a virtual clock a send can land in the
            # next slot - but a driver that waited for completions would accumulate, and 12
            # requests at 0.25 s would be seconds late, not one slot.
            arrivals = sorted(r["scheduled_s"] for r in raw)
            widest_slot = max(b - a for a, b in zip(arrivals, arrivals[1:]))
            assert lag[tag] <= widest_slot + 1e-6, \
                f"{tag}: sends waited for completions (lag {lag[tag]}s > slot {widest_slot}s)"
            assert all(r["send_s"] >= r["scheduled_s"] for r in raw)
            assert all(r["latency_from_scheduled_s"] >= r["latency_s"] for r in raw)
            assert summary["percentiles"]["latency_from_scheduled_s"]["samples"] == 12
        assert rows["fast"] == rows["slow"], "arrival schedule must not depend on response latency"
        # The relative invariant the E2 review asked for, SIGNED and on the test's own clock:
        # a slower server must not make the driver later. (It is not an equality: on a virtual
        # clock `send_s` is stamped after media preparation, so which slot a send lands in
        # depends on the interleaving - bounded above by one arrival slot, asserted per tag.
        # A closed-loop driver would be seconds late, not one slot.)
        assert lag["slow"] - lag["fast"] <= 1e-6, \
            f"a slow server delayed the arrivals ({lag['fast']} -> {lag['slow']})"


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

        # An upstream exception that echoes the header contributes its TYPE only: round 4
        # dropped every verbatim server string, so no row has a message field to scrub.
        summary, raw, stdout, rc = run_bench(base_argv(tmp, requests=2, concurrency=1),
                                             FakeGateway(raise_with_key=KEY), env={"INFRX_API_KEY": KEY})
        assert summary["failed"] == 2 and summary["accepted"] == 0
        assert all(r["error_class"] == "RuntimeError" for r in raw)
        assert all("error_message" not in r for r in raw), "no row field may carry server text"
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


def test_server_controlled_fields_are_allowlisted_not_scrubbed():
    """Rounds 1-3 tried to scrub these fields and lost three times. Round 4 stopped
    recording them: a code that is not [a-z0-9_]{1,64} becomes "unrecognized", a body
    becomes a sha256 and a length, and an exception becomes its class. There is nothing
    left to truncate, so the straddle shapes cannot exist any more — but the same hostile
    gateways must still produce clean, useful rows."""
    with tempfile.TemporaryDirectory() as tmp:
        clips = make_clips(4, tmp)
        with_clips(clips)

        # (a) the key echoed back in error.code: not allowlistable, so it never lands
        summary, raw, stdout, _ = run_bench(base_argv(tmp, requests=2, concurrency=1),
                                            FakeGateway(echo_key_in_code=KEY), env={"MARLIN_API_KEY": KEY})
        assert summary["rejected"] == 2 and summary["error_codes"] == {"unrecognized": 2}
        assert all(r["error_code"] == "unrecognized" and r["error_type"] == "auth" for r in raw)
        assert all(len(r["body_sha256"]) == 64 and r["body_bytes"] > 0 for r in raw), \
            "the body must still be correlatable by digest and length"
        for blob in all_output(tmp, summary, raw, stdout):
            assert KEY not in blob, "api key leaked through error.code"

        # (b) a non-JSON body carrying the key: recorded as a digest, never as text
        summary, raw, stdout, _ = run_bench(base_argv(tmp, requests=2, concurrency=1),
                                            FakeGateway(echo_key_in_long_body=KEY),
                                            env={"MARLIN_API_KEY": KEY})
        assert summary["rejected"] == 2
        assert all(r["error_code"] is None and r["body_bytes"] == 170 + 15 + len(KEY) + 5 for r in raw)
        assert_no_key_prefix(tmp, summary, raw, stdout, "non-JSON body echoing the key")

        # (b2) the same key \uXXXX-escaped inside error.message: the field is gone
        summary, raw, stdout, _ = run_bench(base_argv(tmp, requests=2, concurrency=1),
                                            FakeGateway(echo_key_in_json_message=KEY, echo_pad=180,
                                                        escape_key=True), env={"MARLIN_API_KEY": KEY})
        assert summary["rejected"] == 2
        assert all("error_message" not in r for r in raw)
        assert_no_key_prefix(tmp, summary, raw, stdout, "escaped key inside error.message")

        # (b3) and inside error.code, which used to be cut at 120 and become a summary key
        summary, raw, stdout, _ = run_bench(base_argv(tmp, requests=2, concurrency=1),
                                            FakeGateway(echo_key_in_code=KEY, echo_pad=102,
                                                        escape_key=True), env={"MARLIN_API_KEY": KEY})
        assert summary["rejected"] == 2
        assert all(r["error_code"] == "unrecognized" for r in raw), "no cut, no prefix, no echo"
        assert_no_key_prefix(tmp, summary, raw, stdout, "escaped key inside error.code")

        # (c) a rejected PUT to a signed destination: the status as a number, never the URL
        summary, raw, stdout, _ = run_bench(base_argv(tmp, requests=2, concurrency=1, forms="upload"),
                                            FakeGateway(upload_url=SIGNED_URL, put_status=403),
                                            env={"MARLIN_API_KEY": KEY})
        assert summary["failed"] == 2 and summary["accepted"] == 0
        assert all(r["upload_status"] == 403 and r["error_class"] == "UploadFailed" for r in raw)
        for blob in all_output(tmp, summary, raw, stdout):
            assert KEY not in blob
            for canary in SIGNED_CANARIES:
                assert canary not in blob, f"signed-URL canary {canary} leaked into output"
        # A well-formed code still survives intact: the allowlist must not flatten everything.
        summary, raw, _, _ = run_bench(base_argv(tmp, requests=2, concurrency=1),
                                       FakeGateway(statuses={0: 429, 1: 402}),
                                       env={"MARLIN_API_KEY": KEY})
        assert summary["error_codes"] == {"rate_limit_exceeded": 1, "insufficient_credit": 1}


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
        with virtual_time():
            summary, raw, _, _ = run_bench(base_argv(tmp, requests=4, concurrency=1, retries=1,
                                                     rate=20, seed=3),
                                           FakeGateway(statuses={0: 429}, retry_after="1",
                                                       ttft=0.01),
                                           env={"MARLIN_API_KEY": KEY})
        second = [r for r in raw if r["attempt"] == 1]
        assert second and second[0]["schedule_lag_s"] > 0.9, "the retry really waited"
        assert summary["schedule_lag_s"]["samples"] == 4, "one lag sample per request, not per attempt"
        # Exact on the test's own clock: the Retry-After wait is inside the request, so it
        # cannot appear as scheduling lag. The old `< 0.1` bound measured host load instead.
        assert summary["schedule_lag_s"]["max"] == 0.0, \
            "a retry wait must not count as schedule lag"


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
                                           "failed_excluded": 2, "cancelled_excluded": 0,
                                           "scheduled": 8, "skipped_terminal_on_resume": 0,
                                           "attempts": 8, "rejected_attempts": 2,
                                           "failed_attempts": 2, "cancelled_attempts": 0,
                                           "retried_requests": 0}
        assert summary["error_codes"]["rate_limit_exceeded"] == 1
        assert summary["error_codes"]["insufficient_credit"] == 1
        by_seq = {r["seq"]: r for r in raw}
        assert by_seq[0]["retry_after"] == 3.0 and by_seq[0]["outcome"] == "rejected"   # numeric only
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
        # R61(1): `infrx-upload:upl_…` is the only reference ingress accepts, and the handle
        # grammar is contracts/ids.py's. `upload://…` never passed validate.check_video_ref.
        assert any(r.startswith("infrx-upload:upl_") for r in refs), refs
        assert not any(r.startswith("upload://") for r in refs)
        assert any(r == "https://media.invalid/clips/clip000.mp4" or
                   r.startswith("https://media.invalid/clips/") for r in refs)


def test_item_key_is_the_sop_recipe_and_a_function_of_the_payload_only():
    """marlin-sop.md §3.1. Every field must change the key, and nothing else may."""
    fields = dict(dataset_version="ds1", source_id="src", episode_id="ep", segment_index=0,
                  start_s=0, end_s=30, prompt_version="p00", profile_version="v1")
    key = bench.item_key(**fields)
    assert len(key) == 32 and key == bench.item_key(**fields), "the key must be stable"
    for name, other in (("dataset_version", "ds2"), ("source_id", "src2"), ("episode_id", "ep2"),
                        ("segment_index", 1), ("end_s", 31), ("prompt_version", "p01"),
                        ("profile_version", "v2")):
        assert bench.item_key(**{**fields, name: other}) != key, f"{name} must change the key"
    # The separator is a unit separator on purpose: without it "a"+"bc" and "ab"+"c" collide.
    assert bench.item_key("a", "bc", "e", 0, 0, 1, "p", "v") != \
        bench.item_key("ab", "c", "e", 0, 0, 1, "p", "v")


def test_every_scheduled_item_carries_a_distinct_idempotency_key():
    with tempfile.TemporaryDirectory() as tmp:
        clips = make_clips(4, tmp)
        # 12 items over 4 clips: each clip is scheduled three times, and the three copies
        # must be three items (distinct segment_index), not one item replayed twice.
        schedule = bench.build_schedule(12, clips, ["video_b64", "text"], dataset_version="ds",
                                        seed=5)
        keys = [i["idempotency_key"] for i in schedule]
        assert len(set(keys)) == 12, "a reused key would replay an earlier answer"
        assert all(k.startswith("sop1.") and len(k) == len("sop1.") + 32 for k in keys)
        assert max(i["segment_index"] for i in schedule) == 2
        again = bench.build_schedule(12, clips, ["video_b64", "text"], dataset_version="ds", seed=5)
        assert [i["idempotency_key"] for i in again] == keys, "resume re-derives, never remembers"
        other = bench.build_schedule(12, clips, ["video_b64", "text"], dataset_version="ds",
                                     seed=5, profile_version="v2")
        assert not set(keys) & {i["idempotency_key"] for i in other}, \
            "a different preprocessing profile is a different question about the same clip"

        with_clips(clips)
        summary, raw, _, _ = run_bench(base_argv(tmp, requests=6, concurrency=2,
                                                dataset_version="ds"),
                                       gw := FakeGateway(), env={"MARLIN_API_KEY": KEY})
        assert summary["accepted"] == 6
        sent = [s["idempotency_key"] for s in gw.seen]
        assert len(set(sent)) == 6 and all(k for k in sent), "every request carried its own key"
        assert {r["idempotency_key"] for r in raw} == set(sent)
        assert summary["idempotency"]["distinct_item_keys"] == 6


def test_resume_after_a_lost_ack_creates_no_second_accepted_item():
    """The MARLIN-SOP invariant: interruption and resume create no second accepted job.

    The gateway accepts items 3-5 and then tears the response, so the client records them
    as failed while the server holds them. The resumed run re-sends the SAME keys and the
    SAME payloads; the server replays and creates nothing.
    """
    with tempfile.TemporaryDirectory() as tmp:
        clips = make_clips(3, tmp)
        with_clips(clips)
        gw = FakeGateway(idempotent=True, lose_ack=(3, 4, 5))
        first = os.path.join(tmp, "raw1.jsonl")
        argv = base_argv(tmp, requests=6, concurrency=1, dataset_version="ds")
        argv[argv.index("--raw") + 1] = first
        summary, raw, _, rc = run_bench(argv, gw, env={"MARLIN_API_KEY": KEY})
        assert (summary["accepted"], summary["failed"]) == (3, 3), summary["error_classes"]
        assert len(gw.accepted_keys) == 6, "the server really accepted all six items"

        argv2 = base_argv(tmp, requests=6, concurrency=1, dataset_version="ds")
        argv2[argv2.index("--raw") + 1] = os.path.join(tmp, "raw2.jsonl")
        argv2 += ["--resume", first]
        resumed, raw2, _, rc2 = run_bench(argv2, gw, env={"MARLIN_API_KEY": KEY})
        assert rc2 == 0
        assert resumed["denominators"]["skipped_terminal_on_resume"] == 3, "accepted items are done"
        assert resumed["accepted"] == 3 and resumed["idempotency"]["replayed"] == 3
        assert len(gw.accepted_keys) == 6, \
            f"resume created {len(gw.accepted_keys) - 6} duplicate accepted item(s)"
        assert len(set(gw.accepted_keys)) == 6
        assert resumed["idempotency"]["conflicts"] == 0, "the resumed payload must be identical"
        assert resumed["idempotency"]["resumed_from"] == "raw1.jsonl"


def test_a_resumed_upload_item_reuses_its_handle_instead_of_conflicting():
    """Staging again would change the payload under the same key: 409, not a retry (§3.3)."""
    with tempfile.TemporaryDirectory() as tmp:
        clips = make_clips(2, tmp)
        with_clips(clips)
        gw = FakeGateway(idempotent=True, lose_ack=(0, 1))
        first = os.path.join(tmp, "raw1.jsonl")
        argv = base_argv(tmp, requests=2, concurrency=1, forms="upload", dataset_version="ds")
        argv[argv.index("--raw") + 1] = first
        summary, raw, _, _ = run_bench(argv, gw, env={"MARLIN_API_KEY": KEY})
        assert summary["failed"] == 2 and len(gw.uploads) == 2
        handles = [r["upload_handle"] for r in raw]
        assert all(h and h.startswith("upl_") for h in handles)

        argv2 = base_argv(tmp, requests=2, concurrency=1, forms="upload", dataset_version="ds")
        argv2[argv2.index("--raw") + 1] = os.path.join(tmp, "raw2.jsonl")
        argv2 += ["--resume", first]
        resumed, raw2, _, _ = run_bench(argv2, gw, env={"MARLIN_API_KEY": KEY})
        assert resumed["accepted"] == 2 and resumed["idempotency"]["replayed"] == 2
        assert resumed["idempotency"]["conflicts"] == 0
        assert len(gw.uploads) == 2, "the resumed run staged the object a second time"
        assert [r["upload_handle"] for r in raw2] == handles
        assert all(r["upload_s"] is None for r in raw2), "no second staging means no upload time"


def test_an_upload_handle_that_is_not_a_contract_handle_is_never_sent_back():
    with tempfile.TemporaryDirectory() as tmp:
        clips = make_clips(2, tmp)
        with_clips(clips)
        gw = FakeGateway(bad_handle=True)
        summary, raw, _, _ = run_bench(base_argv(tmp, requests=2, concurrency=1, forms="upload"),
                                       gw, env={"MARLIN_API_KEY": KEY})
        assert summary["accepted"] == 0 and summary["failed"] == 2
        assert all(r["error_class"] == "UploadFailed" for r in raw)
        assert not gw.seen, "no chat request may carry a reference we cannot vouch for"


def test_two_tenants_may_hold_identical_item_keys():
    """The idempotency scope is org + operation + key, so two tenants never collide."""
    with tempfile.TemporaryDirectory() as tmp:
        clips = make_clips(2, tmp)
        with_clips(clips)
        gw = FakeGateway(idempotent=True)
        env = {"MARLIN_API_KEY": KEY, "T_ONE": KEY, "T_TWO": "sk-test-SECOND-TENANT-8a7b6c5d"}
        summary, raw, stdout, _ = run_bench(
            base_argv(tmp, requests=4, concurrency=1, tenant_keys="T_ONE,T_TWO",
                      dataset_version="ds"), gw, env=env)
        assert summary["accepted"] == 4 and summary["profile"]["tenants"] == 2
        assert summary["idempotency"]["replayed"] == 0, "a cross-tenant replay would be a leak"
        bearers = {s["authorization"] for s in gw.seen}
        assert len(bearers) == 2, "each tenant sent its own key"
        assert {r["tenant"] for r in raw} == {0, 1}
        for blob in all_output(tmp, summary, raw, stdout):
            for secret in (KEY, env["T_TWO"]):
                assert secret not in blob, "a tenant key leaked into client output"


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


def test_bursty_arrivals_are_bursts_and_keep_the_mean_rate():
    with tempfile.TemporaryDirectory() as tmp:
        clips = make_clips(4, tmp)
        plain = bench.build_schedule(64, clips, ["text"], rate=8.0, seed=9)
        bursty = bench.build_schedule(64, clips, ["text"], rate=8.0, seed=9, burst=4)
        groups = {}
        for item in bursty:
            groups.setdefault(item["arrival_s"], []).append(item["seq"])
        assert len(groups) == 16 and all(len(g) == 4 for g in groups.values()), \
            "arrivals must land in bursts of four instants, not 64 of them"
        assert len(groups) < len({i["arrival_s"] for i in plain})
        # Same mean rate: the gap before a burst is drawn at rate/burst, so the total span is
        # the same stream, only lumpier. Exact per seed, so this is not a statistical hope.
        assert abs(bursty[-1]["arrival_s"] - plain[-1]["arrival_s"]) < 0.5 * plain[-1]["arrival_s"]
        assert [i["arrival_s"] for i in bursty] == sorted(i["arrival_s"] for i in bursty)

        with_clips(clips)
        with virtual_time():
            summary, raw, _, _ = run_bench(base_argv(tmp, requests=16, rate=20, burst=4, seed=9),
                                           FakeGateway(ttft=0.02), env={"MARLIN_API_KEY": KEY})
        assert summary["accepted"] == 16 and summary["profile"]["arrival"]["burst"] == 4
        assert len({r["scheduled_s"] for r in raw}) == 4, "the driver flattened the bursts"


def test_cancellation_is_its_own_outcome_and_stays_in_the_denominators():
    with tempfile.TemporaryDirectory() as tmp:
        clips = make_clips(4, tmp)
        with_clips(clips)
        with virtual_time():
            summary, raw, _, rc = run_bench(
                base_argv(tmp, requests=8, concurrency=1, cancel_after=0.05,
                          cancel_fraction=1.0), FakeGateway(ttft=0.01, token_gap=0.1, tokens=6),
                env={"MARLIN_API_KEY": KEY})
        assert summary["cancelled"] == 8 and summary["accepted"] == 0 and summary["failed"] == 0
        d = summary["denominators"]
        assert d["cancelled_excluded"] == 8 and d["cancelled_attempts"] == 8 and d["scheduled"] == 8
        assert summary["percentiles"]["ttft_s"]["samples"] == 0, \
            "a cancelled request is not a latency sample"
        for r in raw:
            assert r["outcome"] == "cancelled" and r["error_class"] == "client_cancelled"
            assert r["http_status"] == 200 and r["content_chars"] > 0, "it really started streaming"
            assert r["end_s"] - r["send_s"] >= 0.05 and r["stream_complete"] is None
        # Nothing is cancelled when the fraction is zero, on the same clock and gateway.
        with virtual_time():
            summary, _, _, _ = run_bench(base_argv(tmp, requests=4, concurrency=1),
                                         FakeGateway(ttft=0.01, token_gap=0.1, tokens=6),
                                         env={"MARLIN_API_KEY": KEY})
        assert summary["cancelled"] == 0 and summary["accepted"] == 4


def test_phase_timings_come_from_server_timing_and_absent_phases_are_named():
    with tempfile.TemporaryDirectory() as tmp:
        clips = make_clips(2, tmp)
        with_clips(clips)
        gw = FakeGateway(extra_headers={"server-timing":
                                        "retrieval;dur=120, decode;dur=310.5, queue;dur=12.5, "
                                        "prefill;dur=700, settle;dur=4, wibble;dur=1"})
        summary, raw, _, _ = run_bench(base_argv(tmp, requests=6, concurrency=1), gw,
                                       env={"MARLIN_API_KEY": KEY})
        phases = summary["phases"]
        assert phases["unit"] == "ms" and phases["source"].startswith("Server-Timing")
        assert phases["observed"]["retrieval"]["p50"] == 120.0
        assert phases["observed"]["prefill"]["samples"] == 6
        assert phases["observed"]["prefill"]["p95"] is None, "6 samples cannot support a p95"
        # The phases E1B.b asks for and this target does not publish, named rather than guessed.
        assert phases["declared_missing"] == ["prepare", "generate", "journal", "persist"]
        assert phases["undeclared_observed"] == ["wibble"]
        assert all(r["server_timing"]["queue"] == 12.5 for r in raw)


def test_resource_samples_are_written_to_the_raw_file_and_summarised():
    with tempfile.TemporaryDirectory() as tmp:
        clips = make_clips(2, tmp)
        with_clips(clips)
        with virtual_time():
            summary, rows, _, _ = run_bench(
                base_argv(tmp, requests=6, concurrency=1, sample_interval=0.01),
                FakeGateway(ttft=0.02, token_gap=0.01), env={"MARLIN_API_KEY": KEY})
        raw_path = os.path.join(tmp, "raw.jsonl")
        lines = [json.loads(line) for line in open(raw_path, encoding="utf-8")]
        samples = [line for line in lines if line.get("kind") == "resource_sample"]
        attempts = [line for line in lines if line.get("kind") is None]
        assert len(attempts) == 6 and len(samples) >= 2, f"{len(samples)} samples"
        assert samples[-1].get("final") is True, "the last sample is taken at the end of the run"
        assert all("t_s" in s for s in samples)
        series = summary["resources"]["series"]
        assert summary["resources"]["samples"] == len(samples)
        assert "client_cpu_user_s" in series and series["client_cpu_user_s"]["growth"] >= 0
        assert set(series["client_cpu_user_s"]) == {"first", "last", "min", "max", "growth"}
        # A sample line must not be read back as an attempt by the resume reader.
        assert len(bench.read_attempts(raw_path)) == 6
        # Sampling off by default: no sample lines at all, so the raw file stays attempt-only.
        summary2, _, _, _ = run_bench(base_argv(tmp, requests=2, concurrency=1), FakeGateway(),
                                      env={"MARLIN_API_KEY": KEY})
        assert summary2["resources"]["samples"] == 0


def test_the_report_refuses_unsupported_tails_and_names_every_cell_limit():
    with tempfile.TemporaryDirectory() as tmp:
        clips = make_clips(4, tmp)
        with_clips(clips)
        out = os.path.join(tmp, "bench.jsonl")
        run_bench(base_argv(tmp, requests=8, concurrency=2, label="conc-2"), FakeGateway(),
                  env={"MARLIN_API_KEY": KEY})
        argv = base_argv(tmp, requests=8, concurrency=2, label="failing", seed=3)
        argv[argv.index("--raw") + 1] = os.path.join(tmp, "raw2.jsonl")
        run_bench(argv, FakeGateway(statuses={i: 500 for i in range(8)}),
                  env={"MARLIN_API_KEY": KEY})
        buf = io.StringIO()
        assert bench.report(out, stream=buf) == 0
        text = buf.getvalue()
        assert "2 cell(s)" in text and "| conc-2 |" in text and "| failing |" in text
        # p50 is supported at 8 samples, p95 is not, and an unsupported tail is a dash.
        row = [line for line in text.splitlines() if line.startswith("| conc-2 ")][0]
        cells = [c.strip() for c in row.strip("|").split("|")]
        assert cells[-1] == "—" and cells[-3] == "—", row       # both p95 columns refused
        assert float(cells[-2]) > 0 and float(cells[-4]) > 0, row   # both p50 columns reported
        assert row.count("—") == 2, row
        assert "ttft_s p95 needs >= 60 samples, have 8" in text
        assert "exceed the provisional 1 % criterion (P-18, provisional)" in text
        assert "no accepted request: nothing in this row is a measurement" in text
        assert "a cold claim needs a restarted engine" in text
        assert "phases not published by the target: " in text
        assert "distinct workload profile" in text
        empty = os.path.join(tmp, "empty.jsonl")
        open(empty, "w").close()
        buf = io.StringIO()
        assert bench.report(empty, stream=buf) == 1 and "no summaries" in buf.getvalue()


def test_the_declared_profile_carries_every_axis_a_throughput_number_needs():
    with tempfile.TemporaryDirectory() as tmp:
        clips = make_clips(4, tmp)
        with_clips(clips)
        summary, raw, _, _ = run_bench(
            base_argv(tmp, requests=8, concurrency=2, forms="video_b64,text",
                      max_tokens="128,512", dataset_version="ds-1", engine_state="restarted"),
            FakeGateway(), env={"MARLIN_API_KEY": KEY})
        p = summary["profile"]
        assert p["dataset_version"] == "ds-1" and p["profile_version"] == "v1"
        assert p["engine_state"] == "restarted" and p["max_tokens_mix"] == [128, 512]
        assert p["clip_duration_s"] == {"distinct": 4, "min": 2.0, "max": 5.0}
        assert p["clip_frames_profile_v1"] == {"distinct": 4, "min": 4, "max": 10}
        assert p["clip_resolutions"] == ["1280x720", "1920x1080"]
        assert p["clip_codecs"] == ["h264", "vp9"] and p["clip_source_fps"]["distinct"] == 2
        assert p["arrival"] == {"mode": "closed-loop", "rate_per_s": None, "burst": 1,
                                "concurrency": 2}
        # The output-length distribution is really sent, not just declared.
        assert sorted({r["max_tokens"] for r in raw}) == [128, 512]
        # Successful work in video-seconds, with the accepted count it came from.
        assert summary["video_seconds_accepted"] == sum(r["duration_s"] for r in raw
                                                       if r["media_sent"])
        assert summary["video_s_per_s"] > 0


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
