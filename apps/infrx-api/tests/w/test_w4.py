#!/usr/bin/env python3
"""W4 / ENGINE-OPT + PERF-ENVELOPE + MEDIA-PARITY + OPS-RECOVER: measured tuning, phase A.

    uv run --frozen pytest -q tests/w/test_w4.py

The scripts the coordinator runs on the box (`models/marlin2b/measure/{candidate.sh,
parity.py,concurrency.sh}`) run here against stub `docker`/`systemctl`/`curl` and a local
HTTP server; `decide.py` runs on the committed W3 sweep (`research/plan/evidence/w/box/
sweep-20260923T050411Z`). No container, GPU or network. Each case wraps a `check_*` taking
the repository root, so tests/w/w4_mutants.py can run a shell mutant's check on a mutated
copy in process (the shared runner compiles every mutated file as Python); Python mutants
go through the shared pytest runner on a copied tree.
"""
from __future__ import annotations

import asyncio
import hashlib
import http.server
import importlib.util
import io
import json
import pathlib
import re
import shutil
import subprocess
import sys
import threading
import types
from contextlib import redirect_stdout

from infrx.config import Settings
from infrx.contracts.records import JobState, SettlementState, TerminalCause
from infrx.media.video import Media
from tests.i import support
from tests.w import test_serving as serving
from tests.w.test_loop import World, adapter, queued

REPO = pathlib.Path(__file__).resolve().parents[4]
MEASURE = pathlib.Path("models/marlin2b/measure")
SWEEP = pathlib.Path("research/plan/evidence/w/box/sweep-20260923T050411Z")
SWEEP_LOG = "w3-L1-20260923T050411Z.concurrency.log"
SWEEP_RUN = "w3-L1-20260923T050411Z"
FOUR = ("c012", "c025", "c038", "c051")
PROFILE_CONSTANTS = {"FPS", "MIN_FRAMES", "MAX_FRAMES", "PX_PER_FRAME", "LEVELS"}


def module(repo: pathlib.Path, name: str):
    """A fresh import of measure/<name>.py from `repo` (a mutated copy imports its own)."""
    path = repo / MEASURE / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"w4_{name}_{abs(hash(str(path)))}", path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def lines(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_lines(path: pathlib.Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def edit_raw(run: pathlib.Path, c: int, change) -> None:
    """Apply `change(row)` to every attempt row of level c in a copied sweep."""
    path = run / SWEEP_RUN / "raw" / f"c{c}.jsonl"
    rows = lines(path)
    for row in rows:
        if "outcome" in row:
            change(row)
    write_lines(path, rows)


# --------------------------------------------------------------------------
# item 1: the protocol states what decide.py applies
# --------------------------------------------------------------------------
def check_protocol_matches(repo: pathlib.Path) -> None:
    decide, parity = module(repo, "decide"), module(repo, "parity")
    text = (repo / MEASURE / "W4-protocol.md").read_text()
    rows = re.findall(r"^\| [^|]+ \| `([A-Z][A-Z0-9_]+)` \| ([^|]+?) \|", text, re.M)
    assert rows, "the protocol has no criteria table"
    for name, stated in rows:
        actual = getattr(decide, name)
        values = tuple(float(v) for v in stated.split(","))
        assert tuple(map(float, actual if isinstance(actual, tuple) else (actual,))) == values, (
            f"{name}: decide.py has {actual}, the protocol states {stated}")
    numeric = {name for name, value in vars(decide).items()
               if re.fullmatch(r"[A-Z][A-Z0-9_]+", name) and isinstance(value, (int, float, tuple))}
    assert numeric - PROFILE_CONSTANTS == {name for name, _ in rows}, (
        numeric - PROFILE_CONSTANTS ^ {name for name, _ in rows})
    levels = ", ".join(map(str, decide.LEVELS))
    assert f"closed loop at c = {levels} " in text, decide.LEVELS
    listed = re.findall(r"`((?:c\d{3}|sop\d\d)-[^`]+)`", text.split("**Parity set**")[1]
                        .split("## 5.")[0])
    assert tuple(listed) == parity.PARITY_SET, listed
    assert f"`max_tokens` {parity.MAX_TOKENS}" in text
    script = (repo / MEASURE / "candidate.sh").read_text()
    assert f'LEVELS="{" ".join(map(str, decide.LEVELS))}"' in script
    table = dict(re.findall(r"^  (e\d)\) flags=\(([^)]*)\) ;;$", script, re.M))
    for name, flags in table.items():
        stated = re.search(rf"^\| \*\*{name.upper()}\*\* \| ([^|]+) \|", text, re.M).group(1)
        stated = stated.replace("`", "").replace(" — today's pinned flags", "").strip()
        assert (flags or "none") == stated, (name, flags, stated)
    assert sorted(table) == ["e0", "e1", "e3"], table
    assert {pair for pair in decide.PAIRS} == {("e1", "e0"), ("e3", "e1")}
    for candidate, baseline in decide.PAIRS:
        assert re.search(rf"^\| \*\*{candidate.upper()}\*\* \| [^|]+ \| {baseline.upper()} \|",
                         text, re.M), (candidate, baseline)


def test_engine_opt__the_protocol_states_the_criteria_decide_applies():
    check_protocol_matches(REPO)


# --------------------------------------------------------------------------
# item 4: decide.py on the committed W3 sweep
# --------------------------------------------------------------------------
def edit_bench(run: pathlib.Path, change) -> None:
    """Apply `change(row)` to every bench.jsonl row of a copied sweep."""
    path = run / SWEEP_RUN / "bench.jsonl"
    rows = lines(path)
    for row in rows:
        change(row)
    write_lines(path, rows)


def restarted(run: pathlib.Path, name: str | None = None) -> pathlib.Path:
    """Label every level of a copied sweep as run on a freshly started engine (what
    candidate.sh produces), and give it the candidate name candidate.sh logs."""
    edit_bench(run, lambda row: row["profile"].update(engine_state="restarted"))
    if name:
        log = run / "candidate.log"
        previous = log.read_text() if log.exists() else ""
        log.write_text(f"candidate={name} flags=- out={run}\n" + previous)
    return run


def test_engine_opt__the_050411Z_sweep_has_no_qualifying_level():
    """F(c) is never 0: the same four 112 s clips fail at every level (4 per level, 8 at
    c = 32). W(32) was scraped by the sampler before 82a7dbf, which also summed the
    by-reason series, so its nonzero peak is unknown - never 2, never 0. Every level of that
    sweep ran warm, so the rule is not even taken (protocol §7)."""
    decide = module(REPO, "decide")
    run = decide.load(REPO / SWEEP)
    assert sorted(run.levels) == [1, 2, 4, 8, 16, 32]
    for c, level in run.levels.items():
        assert level.F == (8 if c == 32 else 4), (c, level.F)
        assert {clip.split("-")[0] for clip in level.failed_clips} == set(FOUR), level.failed_clips
        assert dict(level.failures) == {"stream_error_event": level.F}
        assert level.reconciled and level.retries_known, c
    assert run.levels[16].W == 0 and run.levels[32].W is None
    assert run.X == 80.7
    assert decide.c_star(run.levels)[0] is None
    rep = decide.report(run)
    assert rep["setting"] is None and rep["verdict"].startswith("no setting adopted")
    assert rep["criteria"]["w3_rule"]["state"] == "unknown"
    assert "not restarted [1, 2, 4, 8, 16, 32]" in rep["criteria"]["w3_rule"]["detail"]
    assert rep["criteria"]["error_rate"]["state"] == "fail"
    assert rep["criteria"]["overload_masking"]["state"] == "pass"


def check_set_aside(repo: pathlib.Path, tmp: pathlib.Path) -> None:
    decide = module(repo, "decide")
    run = decide.load(repo / SWEEP, set_aside=FOUR)
    top = max(level.T for level in run.levels.values())
    assert decide.c_star(run.levels) == (16, round(0.9 * top, 4)), decide.c_star(run.levels)
    rep = decide.report(run)
    assert rep["setting"] == 16 and rep["set_aside"] == list(FOUR), rep
    # the rule's value alone adopts nothing: this run has no engine logs, parity or baseline
    assert rep["verdict"].startswith("no setting adopted"), rep["verdict"]
    printed = io.StringIO()
    with redirect_stdout(printed):
        decide.main([str(repo / SWEEP), "--set-aside", ",".join(FOUR)])
    assert "set_aside=c012,c025,c038,c051" in printed.getvalue()
    # the warm W3 sweep: the value is computed, but the rule was not taken
    assert "setting=16 (not taken)" in printed.getvalue(), printed.getvalue()[-900:]
    # never the default: unnamed, nothing is set aside and nothing qualifies
    assert decide.c_star(decide.load(repo / SWEEP).levels)[0] is None
    printed = io.StringIO()
    with redirect_stdout(printed):
        decide.main([str(repo / SWEEP)])
    assert "set_aside=none" in printed.getvalue() and "setting=None" in printed.getvalue()
    # floor(X) binds when the engine's capacity is below c*, and floors (12.6 -> 12)
    copy = tmp / "low-x"
    shutil.copytree(repo / SWEEP, copy)
    log = copy / SWEEP_LOG
    log.write_text(log.read_text().replace(": 80.70x", ": 12.60x"))
    assert decide.report(decide.load(copy, FOUR))["setting"] == 12


def test_engine_opt__the_set_aside_reproduces_the_recorded_c16_only_when_named(tmp_path):
    check_set_aside(REPO, tmp_path)


def test_perf_envelope__an_unsupported_tail_or_blank_sample_is_unknown(tmp_path):
    decide = module(REPO, "decide")
    assert decide.p95([1.0] * 59) is None and decide.p95([1.0] * 60) == 1.0
    blank = tmp_path / "blank"
    shutil.copytree(REPO / SWEEP, blank)
    samples = blank / SWEEP_RUN / "samples.tsv"
    rows = samples.read_text().splitlines()
    at = next(i for i, row in enumerate(rows) if row.split("\t")[0] == "16")
    cols = rows[at].split("\t")
    cols[3] = ""                                    # one scrape that returned nothing
    rows[at] = "\t".join(cols)
    samples.write_text("\n".join(rows) + "\n")
    run = decide.load(blank, FOUR)
    assert run.levels[16].W is None, run.levels[16].W
    assert decide.c_star(run.levels)[0] is None     # c=16 no longer proves W = 0
    short = restarted(pathlib.Path(shutil.copytree(REPO / SWEEP, tmp_path / "short")))
    kept = [0]

    def drop_one(row):
        if row["outcome"] == "accepted" and not kept[0]:
            kept[0] = 1
            row["outcome"] = "cancelled"            # 59 accepted remain at c = 1
    edit_raw(short, 1, drop_one)
    edit_bench(short, lambda row: row.update(accepted=59, cancelled=1)
               if row["concurrency"] == 1 else None)
    run = decide.load(short, FOUR)
    assert run.levels[1].ttft_p95 is None and run.levels[2].ttft_p95 is not None
    tail = decide.criteria(run, None)[0]["severe_tail"]
    assert tail[0] == "unknown" and "p95 needs" in tail[1], tail


def test_perf_envelope__cells_at_different_cache_states_are_not_compared(tmp_path):
    """Protocol §4/§7: every compared cell ran on a freshly started engine. A candidate and a
    baseline at different states are not compared; nor is a warm level inside one run, for
    the rule or against a baseline."""
    decide = module(REPO, "decide")
    fresh = restarted(pathlib.Path(shutil.copytree(REPO / SWEEP, tmp_path / "fresh")), "e1")
    warm = pathlib.Path(shutil.copytree(REPO / SWEEP, tmp_path / "warm"))
    (warm / "candidate.log").write_text("candidate=e0 flags=none\n")
    verdicts = decide.criteria(decide.load(fresh, FOUR), decide.load(warm, FOUR))[0]
    for name in ("usage_drift", "short_job_starvation", "output_drift"):
        assert verdicts[name][0] == "unknown" and "different states" in verdicts[name][1], verdicts
    base = restarted(pathlib.Path(shutil.copytree(REPO / SWEEP, tmp_path / "base")), "e0")
    same = decide.criteria(decide.load(fresh, FOUR), decide.load(base, FOUR))[0]
    assert same["usage_drift"][0] == "pass" and same["short_job_starvation"][0] == "pass"
    assert same["w3_rule"] == ("pass", "c*=16"), same["w3_rule"]
    # one warm level on both sides: the profiles still match, but that level is not compared
    for side in (fresh, base):
        edit_bench(side, lambda row: row["profile"].update(engine_state="warm")
                   if row["concurrency"] == 4 else None)
    mixed = decide.criteria(decide.load(fresh, FOUR), decide.load(base, FOUR))[0]
    assert mixed["w3_rule"][0] == "unknown" and "not restarted [4]" in mixed["w3_rule"][1]
    assert mixed["severe_tail"][0] == "unknown"
    for name in ("usage_drift", "short_job_starvation", "output_drift"):
        assert mixed[name][0] == "unknown" and "freshly started" in mixed[name][1], mixed[name]


# --------------------------------------------------------------------------
# a passing candidate, and each disqualifier on its own
# --------------------------------------------------------------------------
PARITY_ROW = {"clip_id": "c039-bbb1080p30-1080-square", "sha256": "bytes", "duration_s": 2.0,
              "width": 1080, "height": 1080, "outcome": "accepted", "prompt_tokens": 431,
              "content_sha256": "answer", "events": [["0.0", "1.5"]]}


def passing_pair(tmp: pathlib.Path):
    """The W3 sweep with the four clips set aside, relabelled as candidate.sh's cells
    (every level restarted), as E1 with clean logs against an E0 with the same cells:
    every criterion passes and c* = 16."""
    cand, base = tmp / "cand", tmp / "base"
    shutil.copytree(REPO / SWEEP, cand)
    shutil.copytree(REPO / SWEEP, base)
    (cand / "candidate.log").write_text("".join(
        f"start=c{c} start_to_ready_s=95\ncell=c{c} engine_running=yes error_lines=0\n"
        for c in (1, 2, 4, 8, 16, 32)))
    for c in (1, 2, 4, 8, 16, 32):
        (cand / f"engine-errors-c{c}.log").write_text("")
    (cand / "capability.txt").write_text("probe=cancellation result=pass chunks_read=5\n")
    (cand / "host-mem-c32.tsv").write_text("".join(f"{1790139852 + 2 * i}\t1.5GiB / 62GiB\n"
                                                  for i in range(26)))
    for side, name in ((cand, "e1"), (base, "e0")):
        write_lines(side / "parity.jsonl", [PARITY_ROW])
        restarted(side, name)
    return cand, base


def verdict_of(decide, cand, base) -> dict:
    return decide.report(decide.load(cand, FOUR), decide.load(base, FOUR))


def test_engine_opt__a_passing_candidate_is_adopted_and_each_disqualifier_blocks_it(tmp_path):
    decide = module(REPO, "decide")
    cand, base = passing_pair(tmp_path / "ok")
    rep = verdict_of(decide, cand, base)
    assert rep["verdict"] == "adopt ENGINE_MAX_NUM_SEQS=16 (WORKER_CONCURRENCY=16)", rep
    assert rep["start_to_ready_s"] == [95.0] * 6
    printed = io.StringIO()
    with redirect_stdout(printed):
        decide.main([str(cand), "--baseline", str(base), "--set-aside", ",".join(FOUR)])
    assert re.search(r"^w3_rule c\*=16 threshold=\S+ setting=16$", printed.getvalue(), re.M), \
        printed.getvalue()[-600:]

    def blocks(name, damage, state="fail", criterion=None):
        cand, base = passing_pair(tmp_path / name)
        damage(cand, base)
        rep = verdict_of(decide, cand, base)
        got = rep["criteria"][criterion or name]
        assert got["state"] == state, (name, got)
        assert rep["verdict"].startswith("no setting adopted"), (name, rep["verdict"])
        return rep

    def append(path, text):
        path.write_text((path.read_text() if path.exists() else "") + text)

    blocks("oom", lambda c, b: append(c / "candidate.log",
                                      "cell=c16 engine_running=no error_lines=3\n"))
    blocks("oom_line", lambda c, b: append(c / "engine-errors-c16.log",
                                           "ERROR torch.OutOfMemoryError: CUDA out of memory.\n"),
           criterion="oom")
    blocks("memory_growth", lambda c, b: (c / "host-mem-c32.tsv").write_text("".join(
        f"{i}\t{'1.5' if i < 13 else '2.5'}GiB / 62GiB\n" for i in range(26))))

    def gpu_grows_at_32(c, b):
        samples = c / SWEEP_RUN / "samples.tsv"
        rows = samples.read_text().splitlines()
        at32 = [i for i, line in enumerate(rows) if line.split("\t")[0] == "32"]
        for i in at32[len(at32) // 2:]:
            cols = rows[i].split("\t")
            cols[5] = str(int(float(cols[5])) + 300)
            rows[i] = "\t".join(cols)
        samples.write_text("\n".join(rows) + "\n")
    blocks("gpu_growth", gpu_grows_at_32, criterion="memory_growth")
    blocks("cancellation", lambda c, b: (c / "capability.txt").write_text(
        "probe=cancellation result=fail\n"))
    blocks("output_drift", lambda c, b: write_lines(c / "parity.jsonl", [
        {**PARITY_ROW, "content_sha256": "other", "events": [["0.0", "9.5"]]}]))

    def slower_short_jobs(c, b):
        for level in (8, 16, 32):
            edit_raw(c, level, lambda row: row.update(ttft_s=row["ttft_s"] + 10)
                     if row["outcome"] == "accepted" and row["duration_s"] <= 9 else None)
    blocks("short_job_starvation", slower_short_jobs)

    def one_more_prompt_token(c, b):
        done = [0]

        def change(row):
            if row["outcome"] == "accepted" and not done[0]:
                done[0], row["prompt_tokens"] = 1, row["prompt_tokens"] + 1
        edit_raw(c, 4, change)
    blocks("usage_drift", one_more_prompt_token)

    def no_usage_anywhere(c, b):                    # D4: None on both sides is not equal
        for side in (c, b):
            edit_raw(side, 4, lambda row: row.update(prompt_tokens=None))
    blocks("usage_blank", no_usage_anywhere, "unknown", "usage_drift")
    blocks("severe_tail", lambda c, b: edit_raw(
        c, 1, lambda row: row.update(ttft_s=40.0) if row["outcome"] == "accepted" else None))
    blocks("overload_masking", lambda c, b: edit_raw(c, 2, lambda row: row.update(retries=1)))
    blocks("retries_unrecorded", lambda c, b: edit_raw(c, 2, lambda row: row.pop("retries")),
           "unknown", "overload_masking")

    def six_rows_lost_at_32(c, b):                  # D2: rows missing are not a smaller count
        path = c / SWEEP_RUN / "raw" / "c32.jsonl"
        rows, dropped = lines(path), [0]
        for row in rows:
            if row.get("outcome") == "accepted" and dropped[0] < 6:
                dropped[0], row["drop"] = dropped[0] + 1, True
        write_lines(path, [row for row in rows if not row.get("drop")])
    rep = blocks("rows_lost", six_rows_lost_at_32, criterion="overload_masking")
    assert rep["criteria"]["error_rate"]["state"] == "unknown", rep["criteria"]["error_rate"]

    def six_failures_at_32(c, b):
        count = [0]

        def change(row):
            if row["outcome"] == "accepted" and count[0] < 6:
                count[0] += 1
                row.update(outcome="failed", error_class="stream_error_event")
        edit_raw(c, 32, change)
        edit_bench(c, lambda row: row.update(accepted=row["accepted"] - 6,
                                             failed=row["failed"] + 6)
                   if row["concurrency"] == 32 else None)
    blocks("error_rate", six_failures_at_32)

    # DEC-R2-1: each reconciliation clause on its own (raw rows against the bench row)
    def at32(c, change):
        edit_bench(c, lambda row: change(row) if row["concurrency"] == 32 else None)

    def failures_hidden_as_cancellations(c, b):     # bench says failed, raw says cancelled
        count = [0]

        def change(row):
            if row["outcome"] == "accepted" and count[0] < 6:
                count[0] += 1
                row["outcome"] = "cancelled"
        edit_raw(c, 32, change)
        at32(c, lambda row: row.update(accepted=row["accepted"] - 6, failed=row["failed"] + 6))
    for name, damage in (
            ("failed_miscounted", failures_hidden_as_cancellations),
            ("accepted_miscounted", lambda c, b: at32(c, lambda row: row.update(
                accepted=row["accepted"] - 1, cancelled=row["cancelled"] + 1))),
            ("requests_miscounted", lambda c, b: at32(c, lambda row: row.update(rejected=1)))):
        rep = blocks(name, damage, criterion="overload_masking")
        assert rep["criteria"]["error_rate"]["state"] == "unknown", (name, rep["criteria"])
    def one_attempt_too_many(c, b):                 # --retries 0: attempts must be requests
        path = c / SWEEP_RUN / "raw" / "c32.jsonl"
        rows = lines(path)
        extra = next(dict(row) for row in rows if row.get("outcome") == "accepted")
        write_lines(path, rows + [{**extra, "outcome": "rejected", "seq": 999}])
        at32(c, lambda row: row.update(attempts=row["attempts"] + 1, rejected=1))
    blocks("attempts_over_requests", one_attempt_too_many, criterion="overload_masking")
    # DEC-R2-2: the bench row's own retry count
    at2 = lambda change: lambda c, b: edit_bench(          # noqa: E731
        c, lambda row: change(row["denominators"]) if row["concurrency"] == 2 else None)
    blocks("bench_retried", at2(lambda d: d.update(retried_requests=1)),
           criterion="overload_masking")
    blocks("bench_retries_unrecorded", at2(lambda d: d.pop("retried_requests")), "unknown",
           "overload_masking")
    # DEC-R2-4: a baseline without all six levels is not a baseline
    blocks("baseline_level_missing", lambda c, b: edit_bench(
        b, lambda row: row.update(concurrency=3) if row["concurrency"] == 32 else None),
        "unknown", "usage_drift")
    # D7: the rule is taken over the six predeclared levels
    rep = blocks("level_missing", lambda c, b: edit_bench(c, lambda row: row.update(concurrency=3)
                                                          if row["concurrency"] == 1 else None),
                 "unknown", "w3_rule")
    paired = rep["criteria"]["usage_drift"]           # the candidate short of a level: not paired
    assert paired["state"] == "unknown" and "not all six levels" in paired["detail"], paired
    # D6: the baseline is the predeclared one - not the candidate itself, not another pair
    blocks("pair", lambda c, b: (b / "candidate.log").write_text("candidate=e3 flags=-\n"),
           "unknown", "usage_drift")
    rep = decide.report(decide.load(cand, FOUR), decide.load(cand, FOUR))
    assert rep["criteria"]["output_drift"]["state"] == "unknown", rep["criteria"]["output_drift"]
    assert "own run" in rep["criteria"]["output_drift"]["detail"]
    # D5: an engine whose capacity floors below one sequence adopts nothing
    low = passing_pair(tmp_path / "x-below-one")
    log = low[0] / SWEEP_LOG
    log.write_text(log.read_text().replace(": 80.70x", ": 0.70x"))
    rep = verdict_of(decide, *low)
    assert rep["setting"] == 0 and "floor(X) < 1" in rep["verdict"], rep["verdict"]
    assert not rep["verdict"].startswith("adopt")


def test_engine_opt__the_rule_holds_at_its_boundaries(tmp_path):
    """The operators at the edges: the smallest qualifying c, T exactly at 0.9 x max, a
    failure rate of exactly 1 %, a tail breach at c* itself, the smallest X of two start-ups,
    a set-aside that names a whole clip id, the starvation slack, and parity pairing by the
    clip's bytes."""
    decide = module(REPO, "decide")
    level = types.SimpleNamespace

    def ok(t):
        return level(T=t, F=0, W=0)
    # two qualifying levels (8 and 32) plus 16: the smallest is taken; 1.8 = 0.9 x 2.0 counts
    assert decide.c_star({1: ok(1.0), 8: ok(1.8), 16: ok(2.0), 32: ok(1.9)}) == (8, 1.8)
    assert decide.c_star({1: ok(1.0), 8: ok(1.799), 16: ok(2.0)}) == (16, 1.8)
    run = level(levels={1: level(rows=[{}] * 100, F=1, reconciled=True)})
    assert decide.error_verdict(run)[0] == "fail"          # 1/100 is not < 1 %
    run = level(levels={8: level(ttft_p95=1.0, latency_p95=1.0),
                        16: level(ttft_p95=decide.MAX_TTFT_P95_S + 1, latency_p95=1.0)})
    assert decide.tail_verdict(run, 16)[0] == "fail"
    two = pathlib.Path(shutil.copytree(REPO / SWEEP, tmp_path / "two-x"))
    (two / "startup-c1.log").write_text(
        "GPU KV cache size: 1,970,000 tokens, Maximum concurrency for 32,768 tokens per "
        "request: 60.10x\n")
    assert decide.load(two).X == 60.1
    assert not decide.aside("c012-bbb1080p30-1024x768-4x3", ("c01",))
    assert decide.aside("c012-bbb1080p30-1024x768-4x3", ("c012",))
    short = [{"ttft_s": 2.0, "duration_s": 2.0}] * 60
    base = level(levels={8: level(accepted=short)})
    slow = level(levels={8: level(accepted=[{"ttft_s": 3.5, "duration_s": 2.0}] * 60)})
    assert decide.starvation_verdict(slow, base, [8])[0] == "pass"   # 3.5 <= 1.5 x 2 + 1
    other_bytes = [{**PARITY_ROW, "sha256": "other"}]
    assert decide.parity_verdict(other_bytes, [PARITY_ROW])[0] == "unknown"


# --------------------------------------------------------------------------
# item 5: parity - the comparison and the client
# --------------------------------------------------------------------------
REFUSED_112 = ("The decoder prompt contains a(n) video item with {n} embedding tokens, which "
               "exceeds the pre-allocated encoder cache size 16384.")


def row(clip, outcome="accepted", **fields):
    return {"clip_id": clip, "sha256": f"bytes-{clip}", "duration_s": 2.0, "width": 1080,
            "height": 1080, "outcome": outcome, "prompt_tokens": 431, "content_sha256": "a",
            "events": [["0.0", "1.5"]], "error_message": None, **fields}


def test_media_parity__token_or_content_drift_disqualifies():
    decide = module(REPO, "decide")
    verdict = decide.parity_verdict
    same = [row("c039")]
    assert verdict(same, same)[0] == "pass"
    # the hashes differ but the caption events agree: the declared fallback (marlin-sop §5.2)
    assert verdict([row("c039", content_sha256="b")], same)[0] == "pass"
    assert verdict([row("c039", content_sha256="b", events=[["0.0", "2.0"]])], same)[0] == "fail"
    assert verdict([row("c039", content_sha256="b", events=[])],
                   [row("c039", content_sha256="a", events=[])])[0] == "fail"
    assert verdict([row("c039", prompt_tokens=432)], same)[0] == "fail"
    blank = [row("c039", prompt_tokens=None)]
    assert verdict(blank, blank)[0] == "unknown"                    # no usage is not equal
    # a clip only the candidate answers: the baseline's own refusal count must be the pinned
    # processor's, and the candidate's prompt that count plus the text of its 112 groups
    long = dict(duration_s=112.0, width=1024, height=768)
    base = [row("c012", "failed", error_message=REFUSED_112.format(n=21504), **long)]
    assert verdict([row("c012", prompt_tokens=21504 + 112 * 9 + 40, **long)], base)[0] == "pass"
    assert verdict([row("c012", prompt_tokens=21504 + 112 * 5, **long)], base)[0] == "fail"
    # a baseline that counted something else fails even when the prompt looks right
    assert verdict([row("c012", prompt_tokens=21504 + 112 * 9 + 40, **long)],
                   [row("c012", "failed", error_message=REFUSED_112.format(n=21000), **long)]
                   )[0] == "fail"
    assert verdict([row("c012", prompt_tokens=21504 + 112 * 9, **long)],
                   [row("c012", "failed", error_message="EngineError", **long)])[0] == "unknown"
    assert verdict([row("c012", "failed", **long)], base)[0] == "fail"
    assert verdict(same, same + [row("c024")])[0] == "unknown"          # a clip never asked
    # the arithmetic is the engine's: the three counts the box refused (the engine's text,
    # quoted in serving-version.json) for the four 112 s clips, from their manifest geometry
    record = json.loads((REPO / "models/marlin2b/serving-version.json").read_text())
    refused = re.search(r"(\d+)\|(\d+)\|(\d+) embedding tokens",
                        record["engine_limits"]["measured"]).groups()
    clips = {c["id"].split("-")[0]: c["derived"] for c in json.loads(
        (REPO / "models/marlin2b/corpus/manifest.json").read_text())["clips"]}
    assert {decide.video_tokens(clips[c]["duration_s"], clips[c]["width"], clips[c]["height"])
            for c in FOUR} == {int(n) for n in refused}
    # and the band the check uses holds on every accepted clip of the sweep
    band = decide.overheads(decide.load(REPO / SWEEP), REPO / "models/marlin2b/corpus/manifest.json")
    assert band and decide.OVERHEAD_PER_GROUP_MIN <= min(band) <= max(band) \
        <= decide.OVERHEAD_PER_GROUP_MAX, (min(band), max(band))


class _Engine(http.server.BaseHTTPRequestHandler):
    """A scripted vLLM: a 224-frame item is refused after the headers (the box's
    presentation), the 72 s clip's stream ends without [DONE], the 120 s one finishes with
    [DONE] but no usage, the rest answer."""
    bodies: list = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        type(self).bodies.append(body)
        frames = body["mm_processor_kwargs"]["size"]["longest_edge"] // 200704
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()

        def send(obj):
            self.wfile.write(b"data: " + (obj if isinstance(obj, bytes) else
                                          json.dumps(obj).encode()) + b"\n\n")
        send({"choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}}]})
        if frames == 224:
            send({"error": {"message": REFUSED_112.format(n=21504), "code": 400}})
            return
        send({"choices": [{"index": 0, "delta": {"content": "<0.0-1.5> a door opens"}}]})
        send({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        if frames != 240:
            send({"choices": [], "usage": {"prompt_tokens": 431 + frames,
                                           "completion_tokens": 6}})
        if frames != 144:
            send(b"[DONE]")


def test_media_parity__the_parity_client_records_usage_content_and_the_engines_refusal(tmp_path):
    parity = module(REPO, "parity")
    worker = Media(types.SimpleNamespace(settings=Settings()))
    for duration in (2.0, 2.3, 71.7, 112.0, 130.0):
        assert parity.budget_kwargs(duration) == worker.budget_kwargs(duration), duration
    known = parity.clips()
    cache = tmp_path / "cache"
    for clip in ("c039-bbb1080p30-1080-square", "c024-bbb1080p30-512-square",
                 "c012-bbb1080p30-1024x768-4x3", "sop09-120s-640x360"):
        path = cache / known[clip]["file"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(clip.encode())
    # --check: candidate.sh refuses before the stop unless every parity clip is there
    printed = io.StringIO()
    with redirect_stdout(printed):
        assert parity.main(["--check", "--cache", str(cache)]) == 1
    assert "present=4 " in printed.getvalue() and "sop11-120s-1280x720" in printed.getvalue()
    _Engine.bodies = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Engine)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        out = tmp_path / "parity.jsonl"
        with redirect_stdout(io.StringIO()):
            assert parity.main(["--engine", f"http://127.0.0.1:{server.server_port}",
                                "--cache", str(tmp_path / "cache"), "--out", str(out)]) == 0
    finally:
        server.shutdown()
    rows = {r["clip_id"]: r for r in lines(out)}
    assert list(rows) == list(parity.PARITY_SET)
    short = rows["c039-bbb1080p30-1080-square"]
    assert short["outcome"] == "accepted" and short["prompt_tokens"] == 435
    assert short["content_sha256"] == hashlib.sha256(b"<0.0-1.5> a door opens").hexdigest()
    assert short["events"] == [["0.0", "1.5"]] and short["finish_reason"] == "stop"
    assert short["sha256"] == hashlib.sha256(b"c039-bbb1080p30-1080-square").hexdigest()
    refused = rows["c012-bbb1080p30-1024x768-4x3"]
    assert refused["outcome"] == "failed" and "21504 embedding tokens" in refused["error_message"]
    assert rows["c024-bbb1080p30-512-square"]["outcome"] == "failed", "no [DONE] is not accepted"
    assert rows["sop09-120s-640x360"]["outcome"] == "failed", "no usage is not accepted"
    assert rows["sop10-120s-854x480-step_spans_segment_boundary"]["outcome"] == "missing"
    for clip in parity.PARITY_SET:
        path = cache / known[clip]["file"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(clip.encode())
    with redirect_stdout(io.StringIO()):
        assert parity.main(["--check", "--cache", str(cache)]) == 0
    body = _Engine.bodies[0]
    assert body["temperature"] == 0 and body["max_tokens"] == parity.MAX_TOKENS
    assert body["stop_token_ids"] == [248044, 248046] and body["stream"] is True


# --------------------------------------------------------------------------
# P-20: the interim ceiling and E1's budget
# --------------------------------------------------------------------------
def check_ceiling(repo: pathlib.Path) -> None:
    decide = module(repo, "decide")
    ceiling = decide.ceiling_s(16384)
    geometries = {(c["derived"]["width"], c["derived"]["height"])
                  for name in ("corpus", "corpus-synth")
                  for c in json.loads((repo / "models/marlin2b" / name / "manifest.json")
                                      .read_text())["clips"]}
    # plus a 2.44:1 frame that fills 198 tokens per group when one frame fewer is sampled
    geometries |= {(704, 288), (288, 704)}
    for duration in range(1, ceiling + 1):
        budget = decide.frames(duration)
        for width, height in geometries:
            for sampled in range(max(4, budget - 2), budget + 1):
                tokens = decide.video_tokens(duration, width, height, sampled)
                assert tokens <= 16384, (duration, width, height, sampled, tokens)
    assert decide.worst_tokens(ceiling + 1) > 16384
    assert decide.ceiling_s(32768) == 120, "E1's budget covers profile v1's 120 s"


def test_engine_opt__the_interim_ceiling_fits_every_geometry_and_e1_covers_120_s():
    check_ceiling(REPO)


# --------------------------------------------------------------------------
# concurrency.sh: the state label it is given
# --------------------------------------------------------------------------
def check_the_sweep_labels_its_state(repo: pathlib.Path, tmp: pathlib.Path) -> None:
    stubs = {
        "docker": 'case "$*" in *Args*) echo \'["/model","--max-num-seqs","32"]\' ;; '
                  "*Image*) echo sha256:0 ;; esac\n",
        "curl": "printf 'vllm:num_requests_running 0\\nvllm:num_requests_waiting 0\\n'\n",
        "nvidia-smi": 'echo "1000, 0"\n',
        "bench": 'case "$*" in *verify*) echo "verified 72 file(s), 0 missing, 0 error(s)" ;; '
                 '*--report*) echo "report rows" ;; *) echo "$*" >> "$BENCH_ARGV" ;; esac\n'}
    for env, expected in (({}, "warm"), ({"ENGINE_STATE": "restarted"}, "restarted")):
        sub = tmp / expected
        sub.mkdir()
        done = serving.run_script(repo / "models", sub, "concurrency.sh", stubs,
                                  **{**serving.sweep_inputs(sub), "LEVELS": "1"},
                                  BENCH_ARGV=str(sub / "argv"), **env)
        assert done.returncode == 0, done.stderr[-400:]
        argv = (sub / "argv").read_text()
        assert f"--engine-state {expected} " in argv, argv


def test_perf_envelope__the_sweep_labels_the_engine_state_it_is_given(tmp_path):
    check_the_sweep_labels_its_state(REPO, tmp_path)


# --------------------------------------------------------------------------
# item 3: candidate.sh on a box made of stubs
# --------------------------------------------------------------------------
UNIT_ARGS = json.dumps(["serve", "/model", "--served-model-name", "marlin2b",
                        "--max-model-len", "32768", "--max-num-seqs", "32"])
PY_HEAD = f"#!{sys.executable}\nimport json, os, pathlib, sys\n" \
          "st = pathlib.Path(os.environ['STATE']); a = sys.argv[1:]\n" \
          "open(st / 'calls', 'a').write(pathlib.Path(sys.argv[0]).name + ' ' + ' '.join(a) + '\\n')\n"
# docker: one container name, like the real daemon - a second `run` on a name in use fails
DOCKER = PY_HEAD + r'''
running = st / "running"
if a[0] == "inspect":
    if not running.exists():
        print("Error: No such object", file=sys.stderr); sys.exit(1)
    fmt = a[a.index("--format") + 1] if "--format" in a else ""
    print({"{{json .Args}}": (st / "args").read_text(), "{{.Image}}": (st / "image").read_text(),
           "{{.Config.Image}}": "vllm/vllm-openai:nightly",
           "{{.State.Running}}": "true"}.get(fmt, "[{}]"))
elif a[0] == "run":
    open(st / "run-fds", "a").write(" ".join(sorted(os.listdir("/proc/self/fd"))) + "\n")
    if running.exists():
        print("docker: Error response from daemon: Conflict. The container name is already "
              "in use", file=sys.stderr); sys.exit(125)
    at = next(i for i, x in enumerate(a) if x.startswith("vllm/vllm-openai@"))
    (st / "args").write_text(json.dumps(a[at + 1:]))
    (st / "image").write_text("sha256:4cbf")
    open(st / "runs", "a").write(json.dumps(a) + "\n")
    running.touch()
    print("INFO GPU KV cache size: 2,400,000 tokens")
    print("INFO Maximum concurrency for 32,768 tokens per request: 73.24x")
    print("INFO Encoder cache will be initialized with a budget of 32768 tokens")
elif a[0] == "logs":
    if os.environ.get("ENGINE_ERRORS"):
        print("ERROR VLLMValidationError: The decoder prompt contains a(n) video item with 20160 "
              "embedding tokens, which exceeds the pre-allocated encoder cache size 16384.")
    print("INFO a quiet line")
elif a[0] in ("stop", "rm"):
    running.unlink(missing_ok=True)
elif a[0] == "stats":
    open(st / "stats-fds", "a").write(" ".join(sorted(os.listdir("/proc/self/fd"))) + "\n")
    print("1.50GiB / 62.0GiB")
'''
# systemctl: the unit, its PartOf worker, and how the restore may go wrong
SYSTEMCTL = PY_HEAD + r'''
script = os.environ.get("UNIT_SCRIPT", "/home/ubuntu/model-inference/models/marlin2b/serve.sh")
if a[0] == "show":
    prop = a[a.index("-p") + 1]
    if prop == "ExecStart" and not os.environ.get("UNIT_EXEC_EMPTY"):
        print("{ path=%s ; argv[]=%s --max-num-seqs 32 ; ignore_errors=no }" % (script, script))
    elif prop == "ExecStart":
        print("")                              # what `show` prints for a unit that is not there
    elif prop == "ConsistsOf":
        print("infrx-worker.service")
elif a[0] == "is-active":
    up = (st / ("active-" + a[1])).exists()
    print("active" if up else "inactive"); sys.exit(0 if up else 3)
elif a[0] == "stop":
    for unit in a[1:]:
        (st / ("active-" + unit)).unlink(missing_ok=True)
        if unit == "marlin2b-vllm":           # its container, and the PartOf worker
            (st / "running").unlink(missing_ok=True)
            (st / "active-infrx-worker.service").unlink(missing_ok=True)
elif a[0] == "start":
    if os.environ.get("RESTORE_FAILS"):
        print("Job for marlin2b-vllm.service failed.", file=sys.stderr); sys.exit(1)
    for unit in a[1:]:
        (st / ("active-" + unit)).touch()
        if unit == "marlin2b-vllm":
            (st / "args").write_text(os.environ.get("RESTORE_ARGS") or (st / "unit-args").read_text())
            (st / "image").write_text(os.environ.get("RESTORE_IMAGE") or "sha256:4cbf")
            (st / "running").touch()
            if os.environ.get("RESTORE_UNHEALTHY"):
                (st / "unhealthy").touch()
'''
CURL = PY_HEAD + r'''
url = next(x for x in a if x.startswith("http"))
if not url.startswith("http://127.0.0.1:8000/"):
    sys.exit(7)                                # nothing else listens here
if not (st / "running").exists() or (url.endswith("/metrics") and os.environ.get("METRICS_DOWN")):
    sys.exit(7)
if url.endswith("/health") and (st / "unhealthy").exists():
    sys.exit(22)
if url.endswith("/metrics"):
    print('vllm:num_requests_running{engine="0"} %s' % os.environ.get("INFLIGHT", "0.0"))
    print('vllm:num_requests_waiting{engine="0"} %s' % os.environ.get("WAITING", "0.0"))
    print('vllm:num_requests_waiting_by_reason{engine="0",reason="capacity"} 0.0')
    print('vllm:cache_config_info{block_size="544",num_gpu_blocks="5000"} 1.0')
'''
CHECKOUT_STUBS = {
    "bench.py": "",
    "corpus/manifest.json": "{}",
    "corpus/build.py": "import os, sys\nif os.environ.get('CORPUS_BAD'):\n"
                       "    print('verified 71 file(s), 0 missing, 1 error(s)'); sys.exit(1)\n"
                       "print('verified 72 file(s), 0 missing, 0 error(s)')\n",
    "corpus-synth/manifest.json": "{}",
    "corpus-synth/synth.py": "print('verified 12 file(s), 0 missing, 0 error(s)')\n",
    "measure/capability.sh": 'echo "probe=cancellation result=pass chunks_read=5"\n',
    "measure/concurrency.sh": 'echo "run=w3-L1-stub-c$LEVELS"\n'
                              'for _ in $(seq 100); do [ -e "$STATE/stats-fds" ] && break; '
                              'sleep 0.05; done\n'
                              'echo "levels=$LEVELS state=$ENGINE_STATE" >> "$STATE/sweeps"\n'
                              'echo "level=$LEVELS peak_running=1 peak_waiting=0"\n'
                              'exit "${SWEEP_EXIT:-0}"\n',
    "measure/parity.py": "import os, sys\n"
                         "if '--check' in sys.argv:\n"
                         "    print('parity_check present=9 missing=[]')\n"
                         "    sys.exit(1 if os.environ.get('PARITY_MISSING') else 0)\n"
                         "out = sys.argv[sys.argv.index('--out') + 1]\n"
                         "line = {'clip_id': 'stub', 'bytecode': os.environ.get('PYTHONDONTWRITEBYTECODE')}\n"
                         "import json; open(out, 'a').write(json.dumps(line) + '\\n')\n",
}
LOCK = "w4-candidate.lock"


def candidate_box(repo: pathlib.Path, tmp: pathlib.Path, *args: str, damage=None, **env: str):
    """A pilot box in a directory: the engine it found (running, the unit and its PartOf
    worker active), stub docker/systemctl/curl, the weights at $NVME/marlin2b, a measurement
    checkout at $NVME/w3-checkout (serve.sh real, the measurement scripts stubbed), and
    candidate.sh with NVME there. `damage(tmp)` changes the box before the run."""
    tmp.mkdir(parents=True, exist_ok=True)
    nvme, state, bin_dir = tmp / "nvme", tmp / "state", tmp / "bin"
    for directory in (state, bin_dir, nvme / "marlin2b"):
        directory.mkdir(parents=True, exist_ok=True)
    (nvme / "marlin2b" / "config.json").write_text("{}")
    models = nvme / "w3-checkout" / "models"
    for name in ("common/env.sh", "marlin2b/model.env", "marlin2b/serve.sh"):
        (models / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / "models" / name, models / name)
    for name, body in CHECKOUT_STUBS.items():
        (models / "marlin2b" / name).parent.mkdir(parents=True, exist_ok=True)
        (models / "marlin2b" / name).write_text(body)
    for name, body in (("docker", DOCKER), ("systemctl", SYSTEMCTL), ("curl", CURL)):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    for name in ("running", "active-marlin2b-vllm", "active-infrx-worker.service"):
        (state / name).touch()
    (state / "args").write_text(UNIT_ARGS)
    (state / "unit-args").write_text(UNIT_ARGS)
    (state / "image").write_text("sha256:4cbf")
    source = (repo / MEASURE / "candidate.sh").read_text()
    assert source.count("NVME=/opt/dlami/nvme\n") == 1
    script = tmp / "candidate.sh"
    script.write_text(source.replace("NVME=/opt/dlami/nvme\n", f"NVME={nvme}\n"))
    if damage is not None:
        damage(tmp)
    done = subprocess.run(
        ["bash", str(script), *args], capture_output=True, text=True, timeout=180,
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp), "STATE": str(state),
             "PY": sys.executable, "READY_S": "10", "CANDIDATE": "e1",
             "W4_ENGINE_RESTART_OK": "1", **env})
    calls = (state / "calls").read_text().splitlines() if (state / "calls").exists() else []
    return done, state, nvme, calls


def check_candidate_restores(repo: pathlib.Path, tmp: pathlib.Path) -> None:
    # the operator's environment cannot move the engine: port, GPU, weights, log level, media
    elsewhere = tmp / "elsewhere-media"
    elsewhere.mkdir(parents=True)
    done, state, nvme, calls = candidate_box(
        repo, tmp / "ok", PORT="8001", GPU="1", WEIGHTS=str(tmp / "other-weights"),
        VLLM_LOGGING_LEVEL="DEBUG", PROCESSING_CACHE_DIR=str(elsewhere), ENGINE_MAX_NUM_SEQS="8")
    assert done.returncode == 0, (done.returncode, done.stdout[-800:], done.stderr[-400:])
    assert "restored=yes" in done.stdout, done.stdout[-800:]
    inherited = [fds for fds in (state / "run-fds").read_text().splitlines() if "9" in fds.split()]
    assert not inherited, f"the candidate engine inherited the lock's fd 9: {inherited}"
    sampled = (state / "stats-fds").read_text().splitlines()
    assert sampled and not [fds for fds in sampled if "9" in fds.split()], \
        f"the host sampler inherited the lock's fd 9: {sampled[:3]}"
    stop = calls.index("systemctl stop marlin2b-vllm")
    attempts = [i for i, call in enumerate(calls) if call.startswith("docker run")]
    assert min(attempts) > stop
    runs = [json.loads(line) for line in (state / "runs").read_text().splitlines()]
    assert len(runs) == 7 == len(attempts), "a fresh engine per level plus the gate, each started"
    assert "systemctl start marlin2b-vllm infrx-worker.service" in calls[max(attempts):], calls[-6:]
    assert (state / "args").read_text() == UNIT_ARGS and (state / "active-infrx-worker.service").exists()
    for argv in runs:
        after = argv[argv.index(next(x for x in argv if x.startswith("vllm/vllm-openai@"))) + 1:]
        assert after[after.index("--max-num-seqs") + 1] == "32", after
        assert after[-2:] == ["--max-num-batched-tokens", "32768"], after
        assert argv[argv.index("-p") + 1] == "127.0.0.1:8000:8000", argv
        assert argv[argv.index("--gpus") + 1] == '"device=0"', argv
        assert f"{nvme}/marlin2b:/model:ro" in argv and "VLLM_LOGGING_LEVEL=INFO" in argv, argv
        assert "--allowed-local-media-path" not in argv, argv
    sweeps = (state / "sweeps").read_text().splitlines()
    assert sweeps == [f"levels={c} state=restarted" for c in (1, 2, 4, 8, 16, 32)], sweeps
    # the lock is released at exit: a second run on the same box goes through
    done, state, nvme, calls = candidate_box(repo, tmp / "ok")
    assert done.returncode == 0 and "restored=yes" in done.stdout, (done.stdout[-600:],
                                                                    done.stderr[-300:])
    # a failing sweep still restores the engine it found, and says the run failed
    done, state, nvme, calls = candidate_box(repo, tmp / "failing", SWEEP_EXIT="1")
    assert done.returncode == 3 and "level=1 sweep_exit=1" in done.stdout, done.stdout[-800:]
    assert "restored=yes" in done.stdout and (state / "args").read_text() == UNIT_ARGS
    assert "systemctl start marlin2b-vllm infrx-worker.service" in calls
    # restored means the same args, the same image and a healthy engine - each checked
    for name, env, diff in (("args", {"RESTORE_ARGS": json.dumps(["serve", "/other"])},
                             "args_diff: before="),
                            ("image", {"RESTORE_IMAGE": "sha256:other"}, "image_diff: before="),
                            ("unhealthy", {"RESTORE_UNHEALTHY": "1", "READY_S": "3"},
                             "restored=no healthy=no"),
                            ("start-fails", {"RESTORE_FAILS": "1", "READY_S": "3"},
                             "restored=no healthy=no")):
        done, state, nvme, calls = candidate_box(repo, tmp / name, **env)
        assert done.returncode == 4 and "restored=no" in done.stdout, (name, done.stdout[-600:])
        assert diff in done.stdout, (name, done.stdout[-600:])


@support.LINUX_USERLAND
def test_ops_recover__the_candidate_run_restores_the_engine_it_found(tmp_path):
    check_candidate_restores(REPO, tmp_path)


def check_candidate_refuses(repo: pathlib.Path, tmp: pathlib.Path) -> None:
    checkout = tmp / "unit-tree" / "nvme" / "w3-checkout" / "models" / "marlin2b" / "serve.sh"
    held = []

    def hold_the_lock(box):                             # a first run still going
        import fcntl
        handle = open(box / "nvme" / LOCK, "w")
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held.append(handle)

    def unlink(*parts):
        return lambda box: box.joinpath(*parts).unlink()
    cases = {
        "unlisted": ((), {"CANDIDATE": "e9"}, None),
        "free-form": (("--max-num-seqs", "64"), {}, None),
        "no-consent": ((), {"W4_ENGINE_RESTART_OK": ""}, None),
        "outside": ((), {"OUT": str(tmp / "elsewhere" / "w4-e1")}, None),
        "not-canonical": ((), {"OUT": str(tmp / "not-canonical" / "nvme" / "w4-x" / ".." / ".."
                                         / "escape")}, None),
        "ready-s": ((), {"READY_S": "15m"}, None),
        "ready-s-zero": ((), {"READY_S": "0"}, None),
        "ready-s-zeros": ((), {"READY_S": "00"}, None),
        "second-run": ((), {}, hold_the_lock),
        "unit-inactive": ((), {}, unlink("state", "active-marlin2b-vllm")),
        "no-container": ((), {}, unlink("state", "running")),
        "no-execstart": ((), {"UNIT_EXEC_EMPTY": "1"}, None),
        "unit-tree": ((), {"UNIT_SCRIPT": str(checkout)}, None),
        "no-checkout": ((), {}, unlink("nvme", "w3-checkout", "models", "marlin2b", "measure",
                                       "concurrency.sh")),
        "unverified": ((), {"CORPUS_BAD": "1"}, None),
        "parity-missing": ((), {"PARITY_MISSING": "1"}, None),
        "in-flight": ((), {"INFLIGHT": "2.0"}, None),
        "waiting": ((), {"WAITING": "3.0"}, None),
        "metrics-down": ((), {"METRICS_DOWN": "1"}, None),
    }
    for name, (args, env, damage) in cases.items():
        done, state, nvme, calls = candidate_box(repo, tmp / name, *args, damage=damage, **env)
        assert done.returncode == 2 and "refused:" in done.stderr, (name, done.returncode,
                                                                    done.stderr[-300:])
        touched = [call for call in calls
                   if call.startswith(("systemctl stop", "systemctl start", "docker run",
                                       "docker stop", "docker rm"))]
        assert not touched, (name, touched)
        written = [p.name for p in nvme.glob("w4-*") if p.name != LOCK]
        assert not written and not (tmp / name / "escape").exists(), (name, written)
        assert not (tmp / "elsewhere").exists(), name
    for handle in held:
        handle.close()


def test_ops_recover__the_candidate_run_refuses_in_flight_work_and_unlisted_flags(tmp_path):
    check_candidate_refuses(REPO, tmp_path)


def check_candidate_records(repo: pathlib.Path, tmp: pathlib.Path) -> None:
    done, state, nvme, calls = candidate_box(repo, tmp, ENGINE_ERRORS="1")
    assert done.returncode == 0, (done.stdout[-600:], done.stderr[-300:])
    (out,) = nvme.glob("w4-e1-*")
    log = (out / "candidate.log").read_text()
    labels = re.findall(r"^start=(\S+) start_to_ready_s=\d+$", log, re.M)
    assert labels == ["gate"] + [f"c{c}" for c in (1, 2, 4, 8, 16, 32)], labels
    assert f"pre_args={UNIT_ARGS}" in log and "pre_image=sha256:4cbf" in log
    assert "partof_active=infrx-worker.service" in log
    assert "parity_set=parity_check present=9 missing=[] synth=verified 12 file(s)" in log, log[:600]
    for cell in ("capability", "parity", "c1", "c32"):
        assert re.search(rf"^cell={cell} engine_running=yes error_lines=1$", log, re.M), cell
        errors = (out / f"engine-errors-{cell}.log").read_text()
        assert "exceeds the pre-allocated encoder cache size 16384" in errors, (cell, errors)
    startup = (out / "startup-c1.log").read_text()
    assert "Maximum concurrency for 32,768 tokens per request: 73.24x" in startup
    assert "Encoder cache will be initialized" in startup and "vllm:cache_config_info" in startup
    recorded = re.search(r"^args=(.*)$", startup, re.M)
    assert recorded, f"no args= line in startup-c1.log: {startup}"
    served = json.loads(recorded.group(1))
    assert served[0] == "/model" and served[-2:] == ["--max-num-batched-tokens", "32768"], served
    assert (out / "host-mem-c1.tsv").exists()
    assert [json.loads(line)["bytecode"] for line in (out / "parity.jsonl").read_text()
            .splitlines()] == ["1"], "parity ran without PYTHONDONTWRITEBYTECODE"
    assert "probe=cancellation result=pass" in (out / "capability.txt").read_text()
    sixteen = (out / "c16.concurrency.log").read_text()
    assert sixteen.startswith("run=w3-L1-stub-c16"), sixteen[:300]


@support.LINUX_USERLAND
def test_ops_recover__the_candidate_run_records_start_to_ready_and_engine_errors(tmp_path):
    check_candidate_records(REPO, tmp_path)


# --------------------------------------------------------------------------
# item 6: the worker's side of a deterministic engine refusal
# --------------------------------------------------------------------------
def test_engine_opt__a_deterministic_engine_refusal_settles_once_and_free():
    """The box's presentation of the encoder-cache refusal: HTTP 200, the role chunk, then
    an SSE error and no visible text. The attempt is a typed platform failure - engine_error,
    free to the customer and absorbed by the platform (R21) - settled once, with one request
    to the engine and nothing relayed."""
    async def case():
        world = World()
        request, _ = await queued(world)
        upstream, engine = adapter(world, "engine_error_before_content")
        result = await world.runner(engine).run(request.request_id)
        outcome = result.outcome
        assert result.proposed_cause is TerminalCause.engine_error, result.proposed_cause
        assert outcome.state is JobState.failed and outcome.debit == 0
        assert outcome.settlement_state is SettlementState.released_platform_absorbed
        assert world.balance()["reserved"] == 0
        assert len(upstream.requests) == 1 and world.relayed == []
        assert world.visible(request.request_id) == ""
        # terminal: a second pass over the same job runs nothing
        again = await world.runner(engine).run(request.request_id)
        assert len(upstream.requests) == 1, "a settled refusal was sent to the engine again"
        assert world.outcome(request.request_id).state is JobState.failed, again
    asyncio.run(case())
