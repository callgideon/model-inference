#!/usr/bin/env python3
"""W4: apply the predeclared rule (measure/W4-protocol.md) to measured run directories.

    python3 decide.py RUN_DIR [--baseline RUN_DIR] [--set-aside c012,c025,...]
    python3 decide.py --ceiling 16384          # P-20: longest duration whose worst case fits
    python3 decide.py --overheads RUN_DIR      # text+timestamp tokens per group (calibration)

A RUN_DIR is `candidate.sh`'s output or a W3 sweep directory: every `*.concurrency.log` under
it names its run (`run=`), whose directory holds `bench.jsonl`, `raw/c<c>.jsonl` and
`samples.tsv`. Beside them, a candidate run has `candidate.log`, `capability.txt`,
`parity.jsonl`, `engine-errors-<cell>.log` and `host-mem-c<c>.tsv`.

Fails closed: a blank, missing or unparseable sample is `unknown`, never 0, and a verdict
with any `unknown` criterion adopts nothing. Stdlib only. The constants are the protocol's
§5 table; tests/w/test_w4.py holds the two equal.
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import pathlib
import re
import statistics
import sys

T_FRACTION = 0.9
MAX_FAILURE_RATE = 0.01
P95_MIN_ACCEPTED = 60
MAX_TTFT_P95_S = 30.0
MAX_LATENCY_P95_S = 150.0
SHORT_CLASS_MAX_S = 9.0
SHORT_CLASS_LEVELS = (8, 16, 32)
STARVATION_RATIO = 1.5
STARVATION_SLACK_S = 1.0
MAX_GPU_GROWTH_MIB = 256
MAX_HOST_GROWTH_MIB = 512
MIN_GROWTH_SAMPLES = 4
OVERHEAD_PER_GROUP_MIN = 8
OVERHEAD_PER_GROUP_MAX = 12
E3_MIN_LEVEL = 8
E3_MAX_GPU_UTIL = 90

# the protocol's identity (§1, §4): the six levels, and the predeclared pairs (§2)
LEVELS = (1, 2, 4, 8, 16, 32)
PAIRS = {("e1", "e0"), ("e3", "e1")}

# profile v1 (marlin-sop.md §1.5) and the engine's refusal text (input_processor.py:512-519)
FPS, MIN_FRAMES, MAX_FRAMES, PX_PER_FRAME = 2.0, 4, 240, 200_704
REFUSAL = re.compile(r"item with (\d+) embedding tokens")
X_LINE = re.compile(r"Maximum concurrency for [\d,]+ tokens per request: ([\d.]+)x")
EVENT = re.compile(r"<\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*>")
PASS, FAIL, UNKNOWN = "pass", "fail", "unknown"
HERE = pathlib.Path(__file__).resolve().parent


# ---------------------------------------------------------------- the pinned processor
def frames(duration_s: float) -> int:
    """Profile v1's frame budget, as the worker's `budget_kwargs` computes it."""
    count = int(min(MAX_FRAMES, max(MIN_FRAMES, round(duration_s * FPS))))
    return count + count % 2


def smart_resize(n, height, width, max_pixels, min_pixels=4096, factor=32, temporal=2):
    """transformers' Qwen3-VL video `smart_resize` (vLLM imports it, qwen3_vl.py:46-47), for
    frames whose sides are both >= 32 px: the port has no branch for a side under 32 px.
    Reproduced here, not read from the pinned image: checked against the box instead (the
    engine's refusal counts and the accepted clips' prompt_tokens, tests/w/test_w4.py)."""
    h_bar, w_bar = round(height / factor) * factor, round(width / factor) * factor
    if round(n / temporal) * temporal * h_bar * w_bar > max_pixels:
        beta = math.sqrt(n * height * width / max_pixels)
        h_bar = max(factor, math.floor(height / beta / factor) * factor)
        w_bar = max(factor, math.floor(width / beta / factor) * factor)
    elif round(n / temporal) * temporal * h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (n * height * width))
        h_bar = math.ceil(height * beta / factor) * factor
        w_bar = math.ceil(width * beta / factor) * factor
    return h_bar, w_bar


def video_tokens(duration_s: float, width: int, height: int, sampled: int | None = None) -> int:
    """One video item's embedding tokens as vLLM counts them (qwen3_vl.py:936-998):
    grid_t x grid_h x grid_w / merge^2, with patch 16, merge 2, temporal patch 2, for the
    `sampled` frames the processor took (default: the budget's own count)."""
    count = frames(duration_s)
    sampled = sampled or count
    h_bar, w_bar = smart_resize(sampled, height, width, count * PX_PER_FRAME)
    return math.ceil(sampled / 2) * (h_bar // 16) * (w_bar // 16) // 4


def worst_tokens(duration_s: float) -> int:
    """Upper bound over every geometry with both sides >= 32 px and an aspect ratio the
    processor accepts: per-frame pixels are at most the clip budget over the frames the
    processor samples, which may be up to two fewer than the budget's count - so it holds
    only for a source with at least F - 2 frames (about 2 fps or more)."""
    budget = frames(duration_s)
    worst = 0
    for sampled in range(max(MIN_FRAMES, budget - 2), budget + 1):
        padded = round(sampled / 2) * 2
        per_group = budget * PX_PER_FRAME // (min(padded, sampled) * 1024)
        worst = max(worst, math.ceil(sampled / 2) * per_group)
    return worst


def ceiling_s(budget_tokens: int, longest_s: int = 120) -> int:
    """The largest whole-second duration up to which every item fits the encoder budget."""
    fits = 0
    for duration in range(1, longest_s + 1):
        if worst_tokens(duration) > budget_tokens:
            break
        fits = duration
    return fits


# ---------------------------------------------------------------- reading a run
def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def jsonl(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def p95(values) -> float | None:
    values = [v for v in values if v is not None]
    if len(values) < P95_MIN_ACCEPTED:
        return None
    return round(statistics.quantiles(sorted(values), n=100, method="inclusive")[94], 4)


def growth(values) -> float | None:
    """Maximum of the second half over the first; None when a half is short or blank."""
    half = len(values) // 2
    first, second = values[:half], values[half:]
    if min(len(first), len(second)) < MIN_GROWTH_SAMPLES or None in first + second:
        return None
    return max(second) - max(first)


def mib(text: str):
    """`docker stats` MemUsage (`1.5GiB / 62GiB`) -> MiB of the first figure."""
    match = re.match(r"\s*([\d.]+)\s*([KMG]i?B|B)", text or "")
    if not match:
        return None
    scale = {"B": 2 ** -20, "KiB": 2 ** -10, "KB": 2 ** -10, "MiB": 1, "MB": 1, "GiB": 1024,
             "GB": 1024}[match.group(2)]
    return float(match.group(1)) * scale


class Level:
    def __init__(self, c, row, run_dir, exact_waiting, root, set_aside):
        self.c, self.profile = c, row.get("profile") or {}
        rows = [r for r in jsonl(run_dir / "raw" / f"c{c}.jsonl") if "outcome" in r]
        self.rows = [r for r in rows if not aside(r.get("clip_id"), set_aside)]
        # the raw rows must be the bench row's attempts, every one: a missing row is not a
        # smaller denominator (protocol §5: accepted + rejected + failed + cancelled = scheduled)
        counted = {k: sum(r["outcome"] == k for r in rows) for k in ("accepted", "failed")}
        tally = sum(row.get(k) or 0 for k in ("accepted", "rejected", "failed", "cancelled"))
        self.reconciled = (len(rows) == row.get("attempts") and tally == row.get("requests")
                           and counted["accepted"] == row.get("accepted")
                           and counted["failed"] == row.get("failed"))
        self.retries_known = all("retries" in r for r in rows) and \
            (row.get("denominators") or {}).get("retried_requests") is not None
        self.accepted = [r for r in self.rows if r["outcome"] == "accepted"]
        self.failures = collections.Counter(
            r.get("error_class") or "unclassified" for r in self.rows if r["outcome"] == "failed")
        self.F = sum(self.failures.values())
        self.failed_clips = sorted({r["clip_id"] for r in self.rows if r["outcome"] == "failed"})
        self.T = (round(row["req_per_s"] * len(self.accepted) / row["accepted"], 3)
                  if row.get("accepted") else 0.0)
        self.retried = sum(r.get("retries") or 0 for r in rows) + \
            ((row.get("denominators") or {}).get("retried_requests") or 0)
        self.accounted = all(r["outcome"] in ("accepted", "rejected", "failed", "cancelled")
                             for r in self.rows)
        self.repeats = len(self.rows) - len({r.get("clip_id") for r in self.rows})
        samples = [(line.split("\t") + [""] * 7)[:7]
                   for line in (run_dir / "samples.tsv").read_text().splitlines()[1:]
                   if line.split("\t")[0] == str(c)] if (run_dir / "samples.tsv").exists() else []
        waiting = [num(s[3]) for s in samples]
        if not waiting or None in waiting:
            self.W = None
        else:
            # the pre-82a7dbf sampler also summed num_requests_waiting_by_reason: only a
            # zero is exact there
            self.W = max(waiting) if exact_waiting or max(waiting) == 0 else None
        running = [num(s[2]) for s in samples if num(s[2]) is not None]
        self.peak_running = max(running) if running else None
        kv = [num(s[4]) for s in samples if num(s[4]) is not None]
        self.peak_kv = max(kv) if kv else None
        self.gpu = [num(s[5]) for s in samples]
        util = [num(s[6]) for s in samples if num(s[6]) is not None]
        self.util_median = statistics.median(util) if util else None
        host = root / f"host-mem-c{c}.tsv"
        self.host = ([mib(line.split("\t", 1)[-1]) for line in host.read_text().splitlines()]
                     if host.exists() else [])
        self.ttft_p95 = p95(r.get("ttft_s") for r in self.accepted)
        self.latency_p95 = p95(r.get("latency_s") for r in self.accepted)


class Run:
    def __init__(self, root, set_aside=()):
        self.root, self.set_aside = pathlib.Path(root), tuple(set_aside)
        self.levels: dict[int, Level] = {}
        xs = []
        for log in sorted(self.root.rglob("*.concurrency.log")) + sorted(self.root.rglob("startup-*.log")):
            text = log.read_text(errors="replace")
            xs += [float(v) for v in X_LINE.findall(text)]
            run = re.search(r"^run=(\S+)", text, re.M)
            run_dir = next((p for p in self.root.rglob(run.group(1)) if p.is_dir()), None) \
                if run else None
            if run_dir is None:
                continue
            exact = re.search(r"^corpus=", text, re.M) is not None
            for row in jsonl(run_dir / "bench.jsonl"):
                self.levels[row["concurrency"]] = Level(row["concurrency"], row, run_dir, exact,
                                                        self.root, self.set_aside)
        self.X = min(xs) if xs else None
        log = self.root / "candidate.log"
        self.log = log.read_text(errors="replace") if log.exists() else None
        errors = sorted(self.root.glob("engine-errors-*.log"))
        self.engine_errors = {p.stem[len("engine-errors-"):]: p.read_text(errors="replace")
                              for p in errors}
        capability = self.root / "capability.txt"
        found = re.search(r"probe=cancellation result=(\w+)",
                          capability.read_text(errors="replace")) if capability.exists() else None
        self.cancellation = found.group(1) if found else None
        self.parity = [r for r in jsonl(self.root / "parity.jsonl")
                       if not aside(r.get("clip_id"), self.set_aside)]


def aside(clip_id, set_aside) -> bool:
    """`c012` names `c012-bbb1080p30-1024x768-4x3`; a full id names itself."""
    return any(clip_id == name or (clip_id or "").startswith(name + "-") for name in set_aside)


def load(root, set_aside=()) -> Run:
    return Run(root, set_aside)


# ---------------------------------------------------------------- the rule and the criteria
def c_star(levels: dict[int, Level]):
    """W3's rule: the smallest c with F = 0, W = 0 and T >= 0.9 x max T."""
    known = [level.T for level in levels.values() if level.T is not None]
    if not known:
        return None, None
    threshold = round(T_FRACTION * max(known), 4)
    for c in sorted(levels):
        level = levels[c]
        if level.F == 0 and level.W == 0 and level.T is not None and level.T >= threshold:
            return c, threshold
    return None, threshold


def setting(c, x):
    return min(c, math.floor(x)) if c is not None and x is not None else None


def parity_verdict(candidate: list[dict], baseline: list[dict]):
    """Output/usage drift on the parity set (protocol §4, §5)."""
    if not candidate or not baseline:
        return UNKNOWN, "no parity rows on both sides"
    base = {(r["clip_id"], r.get("sha256")): r for r in baseline}
    problems, unknown = [], []
    for row in candidate:
        clip, other = row["clip_id"], base.get((row["clip_id"], row.get("sha256")))
        if other is None or "missing" in (row["outcome"], other["outcome"]):
            unknown.append(f"{clip}: no paired row on the same bytes")
        elif row["outcome"] == "accepted" and other["outcome"] == "accepted":
            if row.get("prompt_tokens") is None or other.get("prompt_tokens") is None:
                unknown.append(f"{clip}: accepted without usage")
            elif row.get("prompt_tokens") != other.get("prompt_tokens"):
                problems.append(f"{clip}: prompt_tokens {row.get('prompt_tokens')} != "
                                f"{other.get('prompt_tokens')}")
            elif row.get("content_sha256") != other.get("content_sha256") and not (
                    row.get("events") and row.get("events") == other.get("events")):
                problems.append(f"{clip}: content and caption events differ")
        elif row["outcome"] == "accepted":             # only the candidate answers it
            refused = REFUSAL.search(other.get("error_message") or "")
            expected = video_tokens(row["duration_s"], row["width"], row["height"])
            groups = math.ceil(frames(row["duration_s"]) / 2)
            if refused is None:
                unknown.append(f"{clip}: the baseline refusal names no embedding count")
            elif int(refused.group(1)) != expected:
                problems.append(f"{clip}: the baseline counted {refused.group(1)} embedding "
                                f"tokens, the pinned processor {expected}")
            elif not (OVERHEAD_PER_GROUP_MIN
                      <= (row["prompt_tokens"] - expected) / groups <= OVERHEAD_PER_GROUP_MAX):
                problems.append(f"{clip}: prompt_tokens {row['prompt_tokens']} is not "
                                f"{expected} video tokens plus the text of {groups} groups")
        else:
            problems.append(f"{clip}: refused by the candidate")
    for clip in sorted({r["clip_id"] for r in baseline} - {r["clip_id"] for r in candidate}):
        unknown.append(f"{clip}: no candidate row")
    if problems:
        return FAIL, "; ".join(problems)
    return (UNKNOWN, "; ".join(unknown)) if unknown else (PASS, f"{len(candidate)} clips")


def usage_verdict(run: Run, base: Run, levels):
    def counts(r: Run):
        seen = collections.defaultdict(set)
        for c in levels:
            for row in r.levels[c].accepted:
                seen[row["clip_id"]].add(row.get("prompt_tokens"))
        return seen
    mine, theirs = counts(run), counts(base)
    common = sorted(set(mine) & set(theirs))
    if not common:
        return UNKNOWN, "no clip accepted by both"
    blank = [clip for clip in common if None in mine[clip] | theirs[clip]]
    if blank:
        return UNKNOWN, f"accepted without usage: {blank[:5]}"
    drift = [clip for clip in common if len(mine[clip] | theirs[clip]) != 1]
    return (FAIL, f"prompt_tokens differ for {drift}") if drift else (PASS, f"{len(common)} clips")


def short_p95(run: Run, levels):
    return p95(row.get("ttft_s") for c in SHORT_CLASS_LEVELS if c in levels
               for row in run.levels[c].accepted
               if (row.get("duration_s") or math.inf) <= SHORT_CLASS_MAX_S)


def starvation_verdict(run: Run, base: Run, levels):
    mine, theirs = short_p95(run, levels), short_p95(base, levels)
    if mine is None or theirs is None:
        return UNKNOWN, f"short-class TTFT p95 unsupported (candidate {mine}, baseline {theirs})"
    bound = round(STARVATION_RATIO * theirs + STARVATION_SLACK_S, 4)
    return (PASS if mine <= bound else FAIL), f"{mine} s against a bound of {bound} s"


def tail_verdict(run: Run, cstar):
    if cstar is None:
        return UNKNOWN, "no c*"
    for c in sorted(level for level in run.levels if level <= cstar):
        level = run.levels[c]
        if level.ttft_p95 is None or level.latency_p95 is None:
            return UNKNOWN, f"c={c}: p95 needs >= {P95_MIN_ACCEPTED} accepted"
        if level.ttft_p95 > MAX_TTFT_P95_S or level.latency_p95 > MAX_LATENCY_P95_S:
            return FAIL, f"c={c}: TTFT p95 {level.ttft_p95} s, latency p95 {level.latency_p95} s"
    return PASS, f"levels <= {cstar}"


def oom_verdict(run: Run):
    if run.log is None:
        return UNKNOWN, "no candidate.log (engine exits not recorded)"
    exited = re.findall(r"^cell=(\S+) engine_running=no", run.log, re.M)
    oom = sorted(cell for cell, text in run.engine_errors.items()
                 if re.search(r"out of memory", text, re.I))
    if exited or oom:
        return FAIL, f"engine stopped in {exited}; out of memory in {oom}"
    return PASS, "no exit, no out-of-memory line"


def memory_verdict(run: Run):
    level = run.levels.get(32)
    if level is None:
        return UNKNOWN, "no c=32 cell"
    gpu, host = growth(level.gpu), growth(level.host)
    if gpu is None or host is None:
        return UNKNOWN, f"c=32 growth unsupported (gpu {gpu}, host {host})"
    ok = gpu <= MAX_GPU_GROWTH_MIB and host <= MAX_HOST_GROWTH_MIB
    return (PASS if ok else FAIL), f"c=32 second pass over first: gpu +{gpu} MiB, host +{round(host, 1)} MiB"


def cancellation_verdict(run: Run):
    if run.cancellation is None:
        return UNKNOWN, "no capability.sh cancellation line"
    return (PASS if run.cancellation == "pass" else FAIL), f"result={run.cancellation}"


def error_verdict(run: Run):
    loose = [c for c, level in sorted(run.levels.items()) if not level.reconciled]
    if loose:
        return UNKNOWN, f"raw rows do not reconcile with bench.jsonl at c={loose}"
    scheduled = sum(len(level.rows) for level in run.levels.values())
    failed = sum(level.F for level in run.levels.values())
    if not scheduled:
        return UNKNOWN, "no attempts"
    rate = failed / scheduled
    return (PASS if rate < MAX_FAILURE_RATE else FAIL), f"{failed}/{scheduled} = {round(rate, 4)}"


def masking_verdict(run: Run):
    bad = [c for c, level in sorted(run.levels.items())
           if level.retried or not level.accounted or not level.reconciled]
    if bad:
        return FAIL, f"retries, or attempts the raw rows do not account for, at c={bad}"
    blind = [c for c, level in sorted(run.levels.items()) if not level.retries_known]
    if blind:
        return UNKNOWN, f"retries not recorded at c={blind}"
    return PASS, "--retries 0, every attempt accounted"


def candidate_name(run: Run):
    found = re.search(r"^candidate=(\S+)", run.log or "", re.M)
    return found.group(1) if found else None


def criteria(run: Run, base: Run | None):
    cstar, threshold = c_star(run.levels)
    # protocol §4/§7: every compared cell ran on a freshly started engine, and c* is taken
    # over the six predeclared levels - a warm or missing level is not compared
    stale = [c for c, level in sorted(run.levels.items())
             if level.profile.get("engine_state") != "restarted"]
    missing = [c for c in LEVELS if c not in run.levels]
    if missing or stale:
        v = {"w3_rule": (UNKNOWN, f"c*={cstar} is not taken: levels missing {missing}, "
                                  f"not restarted {stale}")}
    else:
        v = {"w3_rule": (PASS, f"c*={cstar}") if cstar else
             (FAIL, f"no level has F=0, W=0 and T>={threshold}")}
    v["error_rate"] = error_verdict(run)
    v["overload_masking"] = masking_verdict(run)
    v["severe_tail"] = (UNKNOWN, f"not restarted at c={stale}") if stale else tail_verdict(run, cstar)
    v["oom"] = oom_verdict(run)
    v["memory_growth"] = memory_verdict(run)
    v["cancellation"] = cancellation_verdict(run)
    if base is None:
        for name in ("short_job_starvation", "usage_drift", "output_drift"):
            v[name] = (UNKNOWN, "no paired baseline")
        return v, cstar, threshold
    common = sorted(set(run.levels) & set(base.levels))
    comparable = [c for c in common if run.levels[c].profile == base.levels[c].profile]
    pair = (candidate_name(run), candidate_name(base))
    why = None
    if base.root.resolve() == run.root.resolve():
        why = "the baseline is the candidate's own run"
    elif pair not in PAIRS:
        why = f"not a predeclared (candidate, baseline) pair: {pair}"
    elif not common or comparable != common:
        why = (f"cells at different states or profiles, not compared: "
               f"c={[c for c in common if c not in comparable]}")
    elif stale:                       # equal profiles, so the baseline's are stale too
        why = f"cells not on a freshly started engine: c={stale}"
    if why:
        for name in ("short_job_starvation", "usage_drift", "output_drift"):
            v[name] = (UNKNOWN, why)
        return v, cstar, threshold
    v["short_job_starvation"] = starvation_verdict(run, base, comparable)
    v["usage_drift"] = usage_verdict(run, base, comparable)
    v["output_drift"] = parity_verdict(run.parity, base.parity)
    return v, cstar, threshold


def e3_trigger(run: Run):
    return [c for c, level in sorted(run.levels.items())
            if c >= E3_MIN_LEVEL and level.peak_running is not None and level.peak_running < c
            and level.util_median is not None and level.util_median < E3_MAX_GPU_UTIL]


def report(run: Run, base: Run | None = None) -> dict:
    verdicts, cstar, threshold = criteria(run, base)
    chosen = setting(cstar, run.X)
    failed = [name for name, (state, _) in verdicts.items() if state == FAIL]
    unknown = [name for name, (state, _) in verdicts.items() if state == UNKNOWN]
    if not failed and not unknown and chosen is not None and chosen >= 1:
        verdict = f"adopt ENGINE_MAX_NUM_SEQS={chosen} (WORKER_CONCURRENCY={chosen})"
    else:
        verdict = (f"no setting adopted - failed: {failed}; unknown: {unknown}"
                   + ("; X unknown" if run.X is None else "")
                   + ("; floor(X) < 1" if chosen is not None and chosen < 1 else ""))
    return {"run": str(run.root), "baseline": str(base.root) if base else None,
            "set_aside": list(run.set_aside), "X": run.X, "c_star": cstar,
            "threshold": threshold, "setting": chosen, "e3_trigger": e3_trigger(run),
            "start_to_ready_s": [float(s) for s in re.findall(r"start_to_ready_s=([\d.]+)",
                                                              run.log or "")],
            "criteria": {name: {"state": s, "detail": d} for name, (s, d) in verdicts.items()},
            "verdict": verdict}


def print_report(run: Run, rep: dict) -> None:
    print(f"run={rep['run']} baseline={rep['baseline']} "
          f"set_aside={','.join(rep['set_aside']) or 'none'} X={rep['X']} "
          f"start_to_ready_s={rep['start_to_ready_s']}")
    for c, level in sorted(run.levels.items()):
        print(f"level c={c} state={level.profile.get('engine_state')} attempts={len(level.rows)} "
              f"accepted={len(level.accepted)} T={level.T} F={level.F} {dict(level.failures)} "
              f"failed_clips={','.join(level.failed_clips) or '-'} "
              f"W={'unknown' if level.W is None else level.W} peak_running={level.peak_running} "
              f"peak_kv={level.peak_kv} peak_gpu_mib={max((g for g in level.gpu if g is not None), default=None)} "
              f"util_median={level.util_median} ttft_p95={level.ttft_p95} "
              f"latency_p95={level.latency_p95} repeats={level.repeats}")
    print(f"w3_rule c*={rep['c_star']} threshold={rep['threshold']} setting={rep['setting']}")
    for name, item in rep["criteria"].items():
        print(f"criterion {name}={item['state']} ({item['detail']})")
    print(f"e3_trigger={'yes c=' + str(rep['e3_trigger']) if rep['e3_trigger'] else 'no'}")
    print(f"verdict: {rep['verdict']}")


def overheads(run: Run, manifest: pathlib.Path, min_groups: int = 24):
    """(prompt_tokens - video tokens) / groups for accepted rows of >= min_groups groups."""
    clips = {c["id"]: c["derived"] for c in json.loads(manifest.read_text())["clips"]}
    out = []
    for level in run.levels.values():
        for row in level.accepted:
            geo, groups = clips[row["clip_id"]], math.ceil(frames(row["duration_s"]) / 2)
            if groups >= min_groups and row.get("prompt_tokens"):
                tokens = video_tokens(row["duration_s"], geo["width"], geo["height"])
                out.append(round((row["prompt_tokens"] - tokens) / groups, 3))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("run", nargs="?")
    ap.add_argument("--baseline")
    ap.add_argument("--set-aside", default="", help="comma list of clip ids excluded from "
                    "every count, printed with the verdict (never a default)")
    ap.add_argument("--ceiling", type=int, metavar="BUDGET_TOKENS")
    ap.add_argument("--overheads", action="store_true")
    ap.add_argument("--manifest", default=str(HERE.parent / "corpus" / "manifest.json"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.ceiling:
        fits = ceiling_s(a.ceiling)
        for duration in range(max(1, fits - 2), min(120, fits + 2) + 1):
            print(f"duration_s={duration} frames={frames(duration)} "
                  f"worst_video_tokens={worst_tokens(duration)}")
        print(f"ceiling_s={fits} budget_tokens={a.ceiling}")
        return 0
    if not a.run:
        ap.error("a run directory is required")
    set_aside = tuple(filter(None, a.set_aside.split(",")))
    run = load(a.run, set_aside)
    if a.overheads:
        values = overheads(run, pathlib.Path(a.manifest))
        print(f"clips={len(values)} per_group_min={min(values, default=None)} "
              f"per_group_max={max(values, default=None)}")
        return 0
    base = load(a.baseline, set_aside) if a.baseline else None
    rep = report(run, base)
    if a.json:
        print(json.dumps(rep, indent=2))
    else:
        print_report(run, rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
