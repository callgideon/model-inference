#!/usr/bin/env python3
"""dataset.py: interrupt -> resume -> one logical result per item, exact exports.

    apps/infrx-api/.venv/bin/python -m pytest -q models/marlin2b/tests/test_dataset.py

Every run goes to the in-process fake gateway with its idempotency store on: the fake
records one entry in `accepted_keys` per item it really created, so "no second billed
inference" is a count on the server side, not a claim of the client.
"""
import contextlib, io, json, os, signal, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]

import httpx

import bench
import dataset
from fake_gateway import FakeGateway

KEY = "sk-test-dataset-7f3e9a1b"


def manifest(tmp, n_video=4, n_text=2, name="items.jsonl"):
    os.makedirs(os.path.join(tmp, "media"), exist_ok=True)
    lines = []
    for i in range(n_video):
        path = os.path.join("media", f"ep{i}.mp4")
        with open(os.path.join(tmp, path), "wb") as f:
            f.write(b"\x00\x00\x00 ftypisom" + bytes([i]) * 256)
        lines.append({"id": f"ep{i}-seg0", "video": path, "prompt": f"steps {i}",
                      "max_tokens": 64, "start_s": 0, "end_s": 8})
    lines += [{"id": f"txt{i}", "video": None, "prompt": f"hello {i}"} for i in range(n_text)]
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(line) + "\n" for line in lines)
    return path


def argv(tmp, path, *extra, state="run.sqlite"):
    return ["run", "--manifest", path, "--state", os.path.join(tmp, state),
            "--dataset-version", "ds-test", "--base-url", "http://gw.test/v1",
            "--model", "nemostation/marlin-2b@2026-09-01", "--retain-output", "text",
            "--dry-run-transport", "fake", "--concurrency", "2", "--queue", "2", *extra]


def run(args, gw):
    """dataset.main against `gw`: (exit code, printed stats)."""
    bench.load_transport = lambda spec: gw.transport()
    old = os.environ.get("INFRX_API_KEY")
    os.environ["INFRX_API_KEY"] = KEY
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = dataset.main(args)
    finally:
        os.environ.pop("INFRX_API_KEY") if old is None else os.environ.__setitem__(
            "INFRX_API_KEY", old)
    return code, json.loads(out.getvalue().strip().splitlines()[-1])


def exported(tmp):
    res, fail = os.path.join(tmp, "results.jsonl"), os.path.join(tmp, "failures.jsonl")
    with contextlib.redirect_stdout(io.StringIO()):
        dataset.main(["export", "--state", os.path.join(tmp, "run.sqlite"),
                      "--results", res, "--failures", fail])
    read = lambda p: [json.loads(line) for line in open(p, encoding="utf-8") if line.strip()]
    return read(res), read(fail)


def test_interrupt_then_resume_leaves_exactly_one_logical_result_per_item():
    """Oracle: a client that re-keys per attempt, or sends before journalling its key, or
    treats the torn/interrupted item as new, makes the server create a second item for it
    (`accepted_keys` gains a duplicate) or leaves an item without a result."""
    with tempfile.TemporaryDirectory() as tmp:
        path = manifest(tmp)
        # chat #1's answer is lost after the server created the item; the fourth chat request
        # lands a SIGINT (the operator's Ctrl-C) while two requests are in flight.
        def interrupt(request):
            if len(gw.seen) == 4:
                signal.raise_signal(signal.SIGINT)
            return None
        gw = FakeGateway(idempotent=True, lose_ack=(1,), chat_override=interrupt)
        # A background job (`cmd &`, the mutant runner under setsid/nohup) starts with SIGINT
        # ignored, and asyncio.run only installs its Ctrl-C handler over the default one: give
        # this case the terminal's disposition, or the "Ctrl-C" never lands.
        previous = signal.signal(signal.SIGINT, signal.default_int_handler)
        try:
            code, first = run(argv(tmp, path, "--form", "upload"), gw)
        finally:
            signal.signal(signal.SIGINT, previous)
        assert code == 130 and first["interrupted"], first
        assert first["done"] < 6, "the interruption must land mid-run for this case to mean anything"

        gw.chat_override = None
        code, second = run(argv(tmp, path, "--form", "upload"), gw)
        assert code == 0, second
        results, failures = exported(tmp)
        assert sorted(r["item_id"] for r in results) == \
            ["ep0-seg0", "ep1-seg0", "ep2-seg0", "ep3-seg0", "txt0", "txt1"] and not failures
        # the server side: one created item per logical item, whatever was re-sent
        assert len(gw.accepted_keys) == 6 == len(set(gw.accepted_keys)), gw.accepted_keys
        assert {r["idempotency_key"] for r in results} == set(gw.accepted_keys)
        # a re-sent item that the server had already created is labelled a replay
        replays = [r for r in results if r["served"] == "replay"]
        assert replays and all(r["sends"] >= 2 for r in replays)
        assert second["skipped_terminal"] == first["done"]
        assert all(r["output"] and r["output_chars"] == len(r["output"]) for r in results)
        # a resumed item re-sends the handle it was sent with (no 409): one reference per key.
        # (A staging the SIGINT cut short may leave an orphan upload to expire; that is not a
        # second item, so the count of uploads is not the invariant - the key -> ref map is.)
        refs = {}
        for s in gw.seen:
            if isinstance(s["content"], list):
                refs.setdefault(s["idempotency_key"], set()).add(s["content"][0]["video_url"]["url"])
        assert len(refs) == 4 and all(len(r) == 1 for r in refs.values()), refs
        assert not gw.foreign


def test_the_producer_never_reads_further_ahead_than_the_bounded_queue():
    """Oracle: loading the manifest whole (or an unbounded queue) reads all 40 items before
    the first finishes: read_ahead_max would be 40."""
    with tempfile.TemporaryDirectory() as tmp:
        path = manifest(tmp, n_video=0, n_text=40)
        code, stats = run(argv(tmp, path), FakeGateway(idempotent=True))
        assert code == 0 and stats["done"] == 40
        # queue 2 + 2 workers holding one each + the item the producer is blocked on
        assert stats["read_ahead_max"] <= 2 + 2 + 1, stats


def test_a_failure_export_retries_exactly_the_identified_subset():
    """Oracle: a rerun that re-sends the whole corpus (or loses the key) shows up as extra
    chat requests or a second accepted key."""
    with tempfile.TemporaryDirectory() as tmp:
        path = manifest(tmp, n_video=0, n_text=6)
        gw = FakeGateway(idempotent=True, statuses={1: 503, 4: 503}, retry_after="0")
        code, stats = run(argv(tmp, path, "--concurrency", "1"), gw)
        assert code == 1 and (stats["done"], stats["failed"]) == (4, 2)
        _, failures = exported(tmp)
        assert len(failures) == 2 and all(f["retryable"] for f in failures)
        # the operator retries ONE identified item now; the other stays failed
        subset = os.path.join(tmp, "retry-now.jsonl")
        with open(subset, "w", encoding="utf-8") as f:
            f.write(json.dumps(failures[0]) + "\n")
        before = len(gw.seen)
        code, stats = run(argv(tmp, path, "--only", subset), gw)
        assert code == 0 and stats["done"] == 1 and stats["sent"] == 1
        assert [s["idempotency_key"] for s in gw.seen[before:]] == \
            [failures[0]["idempotency_key"]], "only the identified subset may be re-sent"
        results, left = exported(tmp)
        assert len(results) == 5 and [f["item_id"] for f in left] == [failures[1]["item_id"]]
        code, stats = run(argv(tmp, path), gw)                  # a plain rerun takes the rest
        results, left = exported(tmp)
        assert len(results) == 6 and not left
        assert len(gw.accepted_keys) == 6 == len(set(gw.accepted_keys))


def test_an_expired_result_is_exported_and_never_rebilled():
    """Oracle: treating result_expired as retryable, or re-keying it, sends a second billed
    request for an item the gateway already processed."""
    with tempfile.TemporaryDirectory() as tmp:
        path = manifest(tmp, n_video=0, n_text=2)

        def expire(request):
            if json.loads(request.content)["messages"][0]["content"] == "hello 1":
                return httpx.Response(410, json={"error": {"code": "result_expired",
                                                           "type": "result_expired"}})
            return None
        gw = FakeGateway(idempotent=True, chat_override=expire)
        run(argv(tmp, path), gw)
        results, failures = exported(tmp)
        assert [r["item_id"] for r in results] == ["txt0"]
        assert [(f["item_id"], f["state"], f["error_code"], f["retryable"]) for f in failures] \
            == [("txt1", "expired", "result_expired", False)]
        _, stats = run(argv(tmp, path), FakeGateway(idempotent=True))
        assert stats["sent"] == 0 and stats["skipped_terminal"] == 2, "an expired item was re-sent"


def test_a_changed_item_or_a_changed_run_identity_is_refused_not_rekeyed():
    """Oracle: re-keying an already-sent item bills it twice; resuming against another
    model/gateway/dataset version sends every item under keys that no longer match."""
    with tempfile.TemporaryDirectory() as tmp:
        path = manifest(tmp, n_video=0, n_text=2)
        gw = FakeGateway(idempotent=True, lose_ack=(0,))
        run(argv(tmp, path, "--concurrency", "1"), gw)             # txt0 sent, answer lost
        lines = [json.loads(line) for line in open(path, encoding="utf-8")]
        lines[0]["prompt"] = "a different question"
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(line) + "\n" for line in lines)
        code, stats = run(argv(tmp, path), gw)
        assert code == 1 and stats["changed"] == 1 and stats["sent"] == 0
        _, failures = exported(tmp)
        assert [(f["item_id"], f["error_class"], f["retryable"]) for f in failures] == \
            [("txt0", "item_changed_after_send", False)]
        other = argv(tmp, path)
        other[other.index("--model") + 1] = "nemostation/marlin-2b@2026-10-01"
        try:
            run(other, gw)
        except SystemExit as e:
            assert "model was" in str(e)
        else:
            raise AssertionError("a resume under another model was not refused")


def test_digest_retention_keeps_no_output_text():
    """Oracle: `--retain-output digest` that still stores the answer retains content the
    operator declared must not be kept."""
    with tempfile.TemporaryDirectory() as tmp:
        path = manifest(tmp, n_video=0, n_text=1)
        args = argv(tmp, path)
        args[args.index("--retain-output") + 1] = "digest"
        run(args, FakeGateway(idempotent=True))
        results, _ = exported(tmp)
        assert results[0]["output"] is None and results[0]["output_chars"] > 0
        assert len(results[0]["output_sha256"]) == 64


# ------------------------------------------------------------------ E1C fix round


def test_a_resume_under_any_other_run_identity_is_refused_before_any_request():
    """Oracle (fix round 0-M2 / 2-E1C-ACC-04): the fingerprint pinning only the model. A
    resume against another gateway (base URL) is the double-billing case - that gateway's
    idempotency store does not know the keys - and another dataset version, form or profile
    version re-keys or re-shapes every item."""
    changes = {"--model": "nemostation/marlin-2b@2026-10-01", "--base-url": "http://gw2.test/v1",
               "--dataset-version": "ds-other", "--form": "video_b64",
               "--profile-version": "v2"}
    with tempfile.TemporaryDirectory() as tmp:
        path = manifest(tmp, n_video=1, n_text=1)
        gw = FakeGateway(idempotent=True, lose_ack=(0,))
        run(argv(tmp, path, "--concurrency", "1"), gw)
        for flag, value in changes.items():
            other = argv(tmp, path) + [flag, value]              # argparse: the last one wins
            before = (len(gw.seen), len(gw.uploads))
            try:
                run(other, gw)
            except SystemExit as e:
                field = flag[2:].replace("-", "_")
                assert f"{field} was" in str(e), (flag, str(e))
            else:
                raise AssertionError(f"a resume under another {flag} was not refused")
            assert (len(gw.seen), len(gw.uploads)) == before, flag


def test_an_edited_video_after_a_lost_ack_is_refused_not_resent_under_its_old_key():
    """Oracle (fix round d09): a key without the media digest re-sends the edited clip under the
    key the server already holds for the old bytes."""
    with tempfile.TemporaryDirectory() as tmp:
        path = manifest(tmp, n_video=1, n_text=0)
        gw = FakeGateway(idempotent=True, lose_ack=(0,))
        run(argv(tmp, path, "--form", "video_b64"), gw)            # ep0 sent, answer lost
        with open(os.path.join(tmp, "media", "ep0.mp4"), "ab") as f:
            f.write(b"re-cut")
        before = len(gw.seen)
        code, stats = run(argv(tmp, path, "--form", "video_b64"), gw)
        assert code == 1 and stats["changed"] == 1 and stats["sent"] == 0, stats
        assert len(gw.seen) == before


def test_output_retention_must_be_declared():
    """Oracle (fix round d04): a default retention keeps answers the operator never agreed to
    keep."""
    args = argv("/nonexistent", "items.jsonl")
    i = args.index("--retain-output")
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            dataset.parse_args(args[:i] + args[i + 2:])
        except SystemExit as e:
            assert e.code == 2
        else:
            raise AssertionError("run without --retain-output was accepted")


def profile(tmp, path, n, **over):
    """A dataset run profile for `path` (test_profile's builder, retargeted at a manifest)."""
    import runprofile
    from test_profile import profile_for
    ids = [json.loads(line)["id"] for line in open(path, encoding="utf-8") if line.strip()]
    p = profile_for([{"id": i} for i in ids], path, **{
        "target.allowlist": ["gw.test"], "workload.dataset_version": "ds-test",
        "workload.forms": ["upload"], "workload.max_tokens_mix": [64, 512],
        "measurement.concurrency": 2, "bounds.max_requests": n, **over})
    p["workload"]["manifest_sha256"] = runprofile.file_sha256(path)
    out = os.path.join(tmp, "profile.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(p, f)
    return out


def test_a_dataset_run_is_bounded_by_its_profile_and_a_paid_one_needs_it():
    """Oracle (fix round 2-E1C-ACC-01): dataset.py had no profile, no host allowlist and no
    request, token, spend or duration bound, so `run --base-url https://<any>/v1` over a
    manifest of any size started. A non-local target now needs a runnable profile; a profile
    that does not bound the manifest refuses the run; neither case sends anything."""
    with tempfile.TemporaryDirectory() as tmp:
        path = manifest(tmp, n_video=2, n_text=2)
        gw = FakeGateway(idempotent=True)
        paid = argv(tmp, path)
        i = paid.index("--dry-run-transport")
        paid = paid[:i] + paid[i + 2:]
        paid[paid.index("--base-url") + 1] = "https://gw.test/v1"
        from test_profile import routed_to     # the paid host's requests would reach `gw`
        with contextlib.redirect_stderr(io.StringIO()), routed_to(gw):
            for extra in ((), ("--profile", profile(tmp, path, 4))):   # no inventory: blocked
                code, stats = run(paid + list(extra), gw)
                assert code == 2 and stats.get("refused") and not gw.seen and not gw.uploads, \
                    (extra, stats)
            for over, needle in (({"bounds.max_requests": 3}, "bounds.max_requests"),
                                 ({"bounds.max_output_tokens": 100}, "output-token ceiling"),
                                 ({"bounds.spend.max_usd": 0.00001}, "spend"),
                                 ({"target.allowlist": ["other.test"]}, "allowlist")):
                p = profile(tmp, path, 4, **over)
                code, stats = run(argv(tmp, path, "--profile", p), gw)
                assert code == 2 and any(needle in e for e in stats["refused"]), (over, stats)
                assert not gw.seen and not gw.uploads, over
        assert not os.path.exists(os.path.join(tmp, "run.sqlite")), "a refused run wrote state"
        code, stats = run(argv(tmp, path, "--profile", profile(tmp, path, 4)), gw)
        assert code == 0 and stats["done"] == 4, stats


def test_a_dataset_run_stops_taking_items_at_its_declared_duration():
    """Oracle (fix round 2-E1C-ACC-01): max_duration_s declared but never enforced - a slow
    target lets the dataset run on for as long as the manifest lasts. What was not sent stays
    resumable under its key."""
    with tempfile.TemporaryDirectory() as tmp:
        path = manifest(tmp, n_video=0, n_text=6)
        p = profile(tmp, path, 6, **{"bounds.max_duration_s": 0.05, "bounds.max_drain_s": 5,
                                     "workload.max_tokens_mix": [512]})
        gw = FakeGateway(idempotent=True, ttft=0.04)
        code, stats = run(argv(tmp, path, "--profile", p), gw)
        assert stats["stopped_by"] == "bounds.max_duration_s" and 0 < stats["sent"] < 6, stats
        code, stats = run(argv(tmp, path), gw)                  # no profile: local, resumes
        _, left = exported(tmp)
        assert not left and len(gw.accepted_keys) == 6 == len(set(gw.accepted_keys))
    with tempfile.TemporaryDirectory() as tmp:          # what is in flight gets max_drain_s
        path = manifest(tmp, n_video=0, n_text=4)
        p = profile(tmp, path, 4, **{"bounds.max_duration_s": 0.05, "bounds.max_drain_s": 0.1,
                                     "workload.max_tokens_mix": [512]})
        gw = FakeGateway(idempotent=True, ttft=1.5)
        code, stats = run(argv(tmp, path, "--profile", p), gw)
        assert stats["stopped_by"] == "bounds.max_drain_s" and stats["wall_s"] < 1.0, stats
        assert stats["done"] == 0 and stats["sent"] == 2, stats
        gw.ttft = 0.01
        run(argv(tmp, path), gw)                          # the cancelled two replay, once
        results, left = exported(tmp)
        assert len(results) == 4 and not left
        assert len(gw.accepted_keys) == 4 == len(set(gw.accepted_keys))
