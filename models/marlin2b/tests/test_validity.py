#!/usr/bin/env python3
"""E1C / RV-08 / BENCH-VALIDITY: when is a cell's number capacity at all?

    apps/infrx-api/.venv/bin/python -m pytest -q models/marlin2b/tests/test_validity.py

Each case names its oracle: the broken behaviour it exists to catch.
"""
import contextlib, hashlib, io, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.dirname(HERE)]

import bench
from fake_gateway import FakeGateway
from test_bench import KEY, base_argv, make_clips, run_bench, with_clips

RESULTS = os.path.join(os.path.dirname(HERE), "results")


def argv(tmp, name, **kw):
    a = base_argv(tmp, **kw)
    a[a.index("--raw") + 1] = os.path.join(tmp, f"{name}.jsonl")
    return a


def test_an_unexpected_replay_invalidates_the_cell_and_never_counts_as_capacity():
    """Oracle (RV-08): the pre-E1C summary counted replayed answers as accepted throughput -
    a second cell reusing the first cell's dataset identity reported the store's answers as
    fresh latency and req/s. Now the replays are excluded and the cell is INVALID."""
    with tempfile.TemporaryDirectory() as tmp:
        with_clips(make_clips(4, tmp))
        gw = FakeGateway(idempotent=True)
        first, _, _, _ = run_bench(argv(tmp, "c1", requests=4, concurrency=1,
                                        dataset_version="cell"), gw, env={"MARLIN_API_KEY": KEY})
        assert first["validity"]["verdict"] == "VALID", first["validity"]
        second, raw, _, _ = run_bench(argv(tmp, "c2", requests=8, concurrency=1,
                                           dataset_version="cell"), gw,
                                      env={"MARLIN_API_KEY": KEY})
        v = second["validity"]
        assert v["verdict"] == "INVALID" and v["unexpected_replayed"] == 4, v
        assert any(r.startswith("unexpected replay") for r in v["reasons"])
        assert (second["accepted"], second["accepted_fresh"], second["accepted_replayed"]) == (8, 4, 4)
        # every capacity number is over the 4 fresh answers only
        assert second["percentiles"]["ttft_s"]["samples"] == 4
        assert second["denominators"]["latency_samples"] == 4
        assert second["video_seconds_accepted"] == sum(r["duration_s"] for r in raw
                                                       if r["served"] == "fresh")
        assert {r["served"] for r in raw} == {"fresh", "replay"}
        assert all(r["resend"] == "first" for r in raw)
        assert "INVALID" in " ".join(bench.cell_warnings(second))


def test_a_resume_labels_its_replays_and_they_do_not_invalidate_it():
    """Oracle: a validity rule that forbids every replay would make dataset resume (whose
    replays are the point) INVALID; one that allows every replay would pass RV-08's case."""
    with tempfile.TemporaryDirectory() as tmp:
        with_clips(make_clips(3, tmp))
        gw = FakeGateway(idempotent=True, lose_ack=(0, 1))
        first = os.path.join(tmp, "r1.jsonl")
        run_bench(argv(tmp, "r1", requests=3, concurrency=1, dataset_version="ds"), gw,
                  env={"MARLIN_API_KEY": KEY})
        a = argv(tmp, "r2", requests=3, concurrency=1, dataset_version="ds") + ["--resume", first]
        resumed, raw, _, _ = run_bench(a, gw, env={"MARLIN_API_KEY": KEY})
        v = resumed["validity"]
        assert v["verdict"] == "VALID" and v["intentional_resume"], v
        assert v["replayed"] == 2 and v["unexpected_replayed"] == 0
        assert [(r["resend"], r["served"]) for r in raw] == [("resume", "replay")] * 2
        assert resumed["accepted_fresh"] == 0 and resumed["req_per_s"] == 0
    # ...but only the keys the interrupted run sent: a replay on an item it never reached is
    # a collision with some other cell, and stays unexpected inside a resume too
    stray = rows(2)
    stray[1].update(idempotency_replayed=True, resend="first")
    assert check(stray, intentional_resume=True)["unexpected_replayed"] == 1


def rows(n, **over):
    """n accepted fresh final rows, one attempt each, open-loop shaped."""
    return [{"seq": i, "attempt": 0, "retries": 0, "outcome": "accepted",
             "idempotency_replayed": False, "schedule_lag_s": 0.01, "resend": "first",
             "served_model": "m@1", **over} for i in range(n)]


def check(rs, **kw):
    kw = {"scheduled": len({r["seq"] for r in rs}), "open_loop": True, "max_lag_s": 1.0, **kw}
    return bench.validity(rs, **kw)


def test_missing_attempts_and_an_interrupted_schedule_invalidate():
    """Oracle: an omitted request (a task that died, a cut schedule) must not leave a smaller
    but 'clean' cell - coordinated omission by absence."""
    assert check(rows(5))["verdict"] == "VALID"
    v = check(rows(4), scheduled=5)
    assert v["verdict"] == "INVALID" and v["missing_attempts"] == 1
    assert check(rows(5), interrupted=True)["verdict"] == "INVALID"


def test_driver_lag_above_the_declared_bound_invalidates_but_retry_waits_do_not():
    """Oracle: a driver that silently sent late offered less load than declared; but a
    retried attempt's lag includes Retry-After, which is not the driver's fault."""
    late = rows(3)
    late[1]["schedule_lag_s"] = 1.5
    v = check(late)
    assert v["verdict"] == "INVALID" and v["driver_lag_max_s"] == 1.5
    assert any("driver lag" in r for r in v["reasons"])
    assert check(late, max_lag_s=2.0)["verdict"] == "VALID"
    assert check(late, open_loop=False)["verdict"] == "VALID"      # closed loop has no schedule
    retried = rows(3) + [{**rows(1)[0], "seq": 1, "attempt": 1, "retries": 1,
                          "schedule_lag_s": 9.0, "resend": "transport_retry"}]
    retried[1]["outcome"] = "rejected"
    assert check(retried)["driver_lag_max_s"] == 0.01


def test_a_served_model_other_than_the_declared_one_invalidates():
    """Oracle: zero replay is not sufficient - a run against the wrong candidate is not a
    measurement of the declared one, however clean its counters."""
    assert check(rows(3), expect_model="m@1")["verdict"] == "VALID"
    v = check(rows(3), expect_model="m@2")
    assert v["verdict"] == "INVALID" and v["identity"] == {"declared": "m@2", "observed": ["m@1"]}
    unreported = check(rows(3, served_model=None), expect_model="m@1")
    assert unreported["verdict"] == "INVALID"
    assert any("unverified" in r for r in unreported["reasons"])
    # end to end: the fake streams the requested model back, which bench records
    with tempfile.TemporaryDirectory() as tmp:
        with_clips(make_clips(2, tmp))
        a = argv(tmp, "id", requests=2, concurrency=1) + ["--model", "pin@1",
                                                         "--expect-model", "pin@2"]
        summary, raw, _, _ = run_bench(a, FakeGateway(), env={"MARLIN_API_KEY": KEY})
        assert {r["served_model"] for r in raw} == {"pin@1"}
        assert summary["validity"]["verdict"] == "INVALID"


def test_counters_that_do_not_reconcile_invalidate():
    """Oracle: a lost attempt row (a retried request whose first attempt never reached the
    file) or an outcome outside the known set hides a refusal from every denominator."""
    lost = rows(2)
    lost[0]["retries"] = 1                          # says it was retried; the retry is gone
    v = check(lost)
    assert v["verdict"] == "INVALID" and not v["reconciliation"][
        "every attempt of a request is on file"]
    odd = rows(2)
    odd[1]["outcome"] = "mystery"
    assert check(odd)["verdict"] == "INVALID"


def test_stage_timestamps_are_monotonic_and_output_lengths_are_reported():
    """Oracle: a stage stamped out of order, a TTFT taken from something other than the first
    streamed content, or speed quoted without the output length it was measured at."""
    with tempfile.TemporaryDirectory() as tmp:
        with_clips(make_clips(2, tmp))
        summary, raw, _, _ = run_bench(argv(tmp, "st", requests=4, concurrency=1,
                                            forms="upload,text"),
                                       FakeGateway(), env={"MARLIN_API_KEY": KEY})
        up = [r for r in raw if r["form"] == "upload"]
        assert up and all(r["upload_start_s"] <= r["upload_end_s"] <= r["send_s"] for r in up)
        for r in raw:
            assert r["send_s"] <= r["first_byte_s"] <= r["first_token_s"] <= r["end_s"], r
            assert r["ttft_s"] == round(r["first_token_s"] - r["send_s"], 6)
        stages = summary["stages_s"]
        assert stages["upload"]["samples"] == 2 and stages["first_output"]["samples"] == 4
        assert stages["ttft_kind"] == "streamed first content delta"
        assert stages["not_measured"] == ["fetch", "reconciliation"]
        out = summary["output_lengths"]
        assert out["completion_tokens"]["samples"] == 4 and out["completion_tokens_total"] == 4 * 21
        assert summary["retry_policy"] == {"client_retries_429_503": 0, "http_library_retries": 0,
                                           "observed_transport_retries": 0}
        assert summary["parser_version"] == bench.PARSER_VERSION


def test_old_e1b_raw_files_stay_readable_and_are_never_reclassified():
    """Oracle: a parser change that reads committed E1B rows differently (their outcome counts
    no longer match the summary written at the time) or writes anything back. The derived
    verdict is labelled with its parser version; the replay-contaminated 4226315 cells come
    out INVALID, the distinct-key reruns and the bda1586 acceptance cells do not."""
    cells = {"E1B-box-bda1586": {"L2-r0.5": "VALID", "L3": "VALID"},
             "E1B-box-4226315": {"L2-r0.5": "INVALID", "L3": "INVALID"},
             os.path.join("E1B-box-4226315", "run2-distinct-keys"): {"L2-r1.0": None}}
    for cell_dir, files in cells.items():
        base = os.path.join(RESULTS, cell_dir)
        stored = {json.loads(line)["raw"]: json.loads(line)
                  for line in open(os.path.join(base, "bench.jsonl"), encoding="utf-8")}
        for name, verdict in files.items():
            path = os.path.join(base, "raw", f"{name}.jsonl")
            before = hashlib.sha256(open(path, "rb").read()).hexdigest()
            out = io.StringIO()
            code = bench.validate_raw(path, stream=out)
            got = json.loads(out.getvalue())
            summary = stored[f"raw/{name}.jsonl"]
            assert got["parser_version"] == bench.PARSER_VERSION and got["derived"]
            assert got["source_sha256"] == before
            assert hashlib.sha256(open(path, "rb").read()).hexdigest() == before
            for outcome in ("accepted", "rejected", "failed", "cancelled"):
                assert got["outcomes"][outcome] == summary[outcome], (path, outcome)
            assert got["replayed"] == summary["idempotency"]["replayed"], path
            if verdict:
                assert got["verdict"] == verdict and code == (verdict != "VALID"), (path, got)
            else:                  # run2 rows: no replay, whatever else the cell carries
                assert got["unexpected_replayed"] == 0, got
