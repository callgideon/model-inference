#!/usr/bin/env python3
"""E4B: certify the Marlin endpoint release candidate - one command, one report.

    apps/infrx-api/.venv/bin/python tests/integration/backend/certify.py --report <path>
    # the box, inside the coordinator's maintenance window (E4B box protocol):
    E4B_WINDOW_OK=1 INFRX_API_KEY=... python tests/integration/backend/certify.py --no-stack \\
        --box --target http://127.0.0.1:8001/v1 --engine-url http://127.0.0.1:8000 \\
        --metrics-url http://127.0.0.1:8001/metrics --worker-metrics-url http://127.0.0.1:8002/metrics \\
        --release-sha <the full release SHA> --inventory <inventory.sh output> \\
        --parity-baseline <W4 E0 parity.jsonl> --report <path>

The protocol - checks, cells, criteria, shapes - is predeclared in
`models/marlin2b/results/E4B-protocol.md`; `CRITERIA` and `MATRIX` below are its numbers
(test_certify holds the two equal). Checks, each a named report entry:

  e4b.a.protocol        the phase-2 gate's stages on the E2 stack (run.py's own functions:
                        preflight, services, migrate, rls, backend), minus `recovery/`
  e4b.a.sop-parity      MARLIN-SOP: W4's parity.py against the engine, `decide.parity_verdict`
  e4b.a.dataset-resume  E1B's bench.py interrupted by SIGINT, then `--resume`; the client's
                        invariants, and the tenant's ledger reconciled through G6B's Operations
  e4b.b.preconditions   App/Lab stopped; box: window consent, engine idle, parity clips
  e4b.b.config-pin      the tree against W3/W4/M4's declared settings (`declared()`), the published
                        release record, and on the box the deployed engine (`--inventory`)
  e4b.b.envelope        bench.py open loop per rate of the ladder: failures, refusals, tails,
                        and the P-20 duration cap at admission
  e4b.b.soak            one open-loop run at half the supported rate, /metrics sampled
  e4b.b.overload        one burst from one key: refusals are 429 + Retry-After, never 5xx
  e4b.b.recovery        I3B's rc*/bk* drills (the backend suite's `recovery/` half)

Every check is PASS, FAIL, or PENDING/SKIP naming owners from `OWNERS` - an untyped skip is
recorded as a FAIL. Exit 0 = every entry PASS, 1 = any FAIL, 3 = otherwise. Nothing here
decides BACKEND-READY: that needs the box half and the coordinator's recorded decision.
Target `local` (no --target) is the E2 stack plus this runner's own fake vLLM, and every
number from it is labelled `fake-engine, not a measurement`.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import hashlib
import json
import os
import signal
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _path in (HERE / "recovery", HERE, HERE.parent):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import harness                                          # noqa: E402
import run                                              # noqa: E402
import recoverykit                                      # noqa: E402  (imports stack; infrx on path)

MARLIN = harness.REPO_ROOT / "models" / "marlin2b"
for _path in (MARLIN / "measure", MARLIN):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import bench                                            # noqa: E402
import decide                                           # noqa: E402
import parity                                           # noqa: E402

PASS, FAIL, PENDING, SKIP = run.PASS, run.FAIL, run.PENDING, run.SKIP
FAKE, MEAS = "fake-engine, not a measurement", "meas."
UNVERIFIED = "unverified target, not a measurement"      # review F5: off the box, or unready
PROTOCOL = MARLIN / "results" / "E4B-protocol.md"
SEED = 20260922

# The only owners a PENDING or SKIP may name: the backend suite's own vocabulary (E3B's
# `stack.PENDING`, I3B's `recoverykit.OWNERS`), plus the two this runner adds. A check the
# local target (an engine: no admission, no ledger) can never judge names BOX, not a task:
# the task's id leaves the vocabulary the day it merges, and the check still needs the box.
OWNERS = {**recoverykit.PENDING,
          "BOX": "the coordinator's maintenance window on the pilot box (P-04 target): this "
                 "runner with --target/--engine-url/--inventory --box, the real engine, "
                 "gateway and stores (research/plan/evidence/e/E4B box protocol)",
          "STACK": "this runner at the same SHA on a host with the E2 compose stack (the dev "
                   "host): the stack suite is bound to the SHA, not to the target",
          "PROFILE": "the coordinator's run profile (infrx.run-profile/1) and sanitized key "
                     "inventory for a remote target: --run-profile and --key-inventory "
                     "(consumer-v1/05 section 1, E1C)"}

# models/marlin2b/results/E4B-protocol.md §5, the one place these numbers live in code.
CRITERIA = {
    "max_failure_rate": decide.MAX_FAILURE_RATE,
    "p95_min_accepted": decide.P95_MIN_ACCEPTED,
    "ttft_p95_short_s": 6.0,
    "short_clip_max_s": 30.0,
    "short_clip_max_edge_px": 1280,
    "e2e_p95_s_per_clip_minute": 90.0,
    "latency_p95_s": 9.0,
    "declared_rate_per_s": 0.5,        # box: the only rate the certificate supports (P-18)
    "max_host_growth_mib": decide.MAX_HOST_GROWTH_MIB,
    "max_gpu_growth_mib": decide.MAX_GPU_GROWTH_MIB,
    "soak_latency_drift": 1.5,
}
# P-20: the deployed cap (`deployed_cap_s`) replaces §5's interim `applied_cap_s` (72;
# amendment 5(c)). A clip over it must get the M layer's typed refusal - `MediaProfile.check` raises
# `UnsupportedMedia(param="messages")`, a 400 (test_certify ties the two). bench.py records
# the status and the code; its allowlist does not keep the param.
OVER_CAP = {"http_status": 400, "code": "unsupported_media", "param": "messages"}
# Refusals that are overload and carry retry guidance: `errors.RETRY_AFTER_CODES` minus the
# one about a dependency (protocol §5).
OVERLOAD_CODES = ("capacity_exhausted", "journal_capacity_exhausted", "rate_limited")
MATRIX = {
    "tiny": {"envelope": {"rates": (4.0,), "requests": 12},
             "soak": {"rate": 2.0, "seconds": 10, "sample_s": 1},
             "overload": {"burst": 32},
             "dataset": {"items": 12, "interrupt_after": 4, "rate": 4.0}},
    "box": {"envelope": {"rates": (0.5, 1.0, 2.0), "requests": 120},
            "soak": {"rate": 0.25, "seconds": 14400, "sample_s": 30},       # P-18, fixed
            "overload": {"burst": 32},
            "dataset": {"items": 24, "interrupt_after": 8, "rate": 1.0}},
}


# ------------------------------------------------------------------------------ report

class Report(run.Report):
    """run.py's report (stages, per-stage seconds, `git_head` at start and end), plus the
    target, the release hashes and the rule that a skip names its owner."""

    def __init__(self, target: dict, release_sha: str | None = None) -> None:
        super().__init__()
        self.target, self.hashes, self.head_end = target, {}, None
        self.release_sha = release_sha
        self.head = release_head(release_sha) or self.head

    def check(self, check_id: str, status: str, detail, *, owners=(), measured=None,
              label: str | None = None) -> dict:
        owners = tuple(sorted(set(owners)))
        if status in (PENDING, SKIP) and (not owners or set(owners) - set(OWNERS)):
            detail = {"untyped": status, "owners": list(owners), "detail": detail}
            status = FAIL
        return self.add(check_id, status, detail, owners=list(owners) or None,
                        measured=measured, label=label)

    @property
    def exit_code(self) -> int:
        statuses = {entry["status"] for entry in self.stages}
        if FAIL in statuses:
            return 1
        return 3 if statuses - {PASS} else 0

    def as_json(self) -> str:
        # Review F1: a certification counts for one clean, known tree - judged once, from the
        # same end sample the report records, so the exit code carries it.
        if self.head_end is None:
            self.head_end = release_head(self.release_sha) or run.git_head()
            problems = identity_problems(self.head, self.head_end, self.release_sha)
            self.check("release-identity", FAIL if problems else PASS,
                       problems or f"one clean tree: {self.head['sha']}")
        doc = json.loads(super().as_json())
        doc["git_head_end"] = self.head_end
        return json.dumps({"runner": "e4b-certify", "protocol": rel(PROTOCOL),
                           "target": self.target, "hashes": self.hashes,
                           "backend_ready": "not decided by this runner: BACKEND-READY needs "
                                            "the box half and the coordinator's recorded "
                                            "decision (E4B-release-decision.md)",
                           **doc}, indent=2, default=str, ensure_ascii=False)


def release_head(release_sha: str | None) -> dict | None:
    """The tree under test where git cannot exist - no git binary (the runtime image) or a
    checkout with no .git: the release the operator names, and the report says so (`source`).
    Its state stays unknown, so `release-identity` still fails it (R97: one clean SHA); git
    that runs is never overridden - its answer, or its silence, stands (None here)."""
    missing = ("no git" if shutil.which("git") is None
               else "no .git" if not (harness.REPO_ROOT / ".git").exists() else None)
    if release_sha and missing:
        return {"sha": release_sha, "dirty": None, "source": f"--release-sha ({missing})"}
    return None


def identity_problems(start: dict, end: dict, release_sha: str | None = None) -> list[str]:
    """Protocol §6.3 as code: a SHA at both ends, a clean tree at both ends (an unknown state
    is not clean), the same SHA, and - on the box - the release the operator names; or the
    report is evidence for no release."""
    problems = []
    for when, head in (("start", start), ("end", end)):
        if not head.get("sha"):
            problems.append(f"no git SHA at the {when} of the run")
        if head.get("dirty") is not False:
            problems.append(f"the tree at the {when} is "
                            f"{'dirty' if head.get('dirty') else 'of unknown state'}")
    if start.get("sha") and end.get("sha") and start["sha"] != end["sha"]:
        problems.append(f"the tree moved during the run: {start['sha']} -> {end['sha']}")
    if release_sha is not None and start.get("sha") != release_sha:
        problems.append(f"the tree is {start.get('sha')}, not the release {release_sha}")
    return problems


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(harness.REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


# ------------------------------------------------------------------------------ hashes

def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_tree(root: Path, pattern: str = "*") -> str:
    """One digest over a tree: every file's relative path and content digest, in order."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob(pattern)
                       if p.is_file() and "__pycache__" not in p.parts):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0"
                      + hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def serving_record() -> dict:
    return json.loads((MARLIN / "serving-version.json").read_text())


def served_flags(record: dict, **settings: str) -> list[str]:
    """W3's formula (`tests/w/test_serving.py`): the record's flags, `${NAME}` substituted."""
    values = {**record["settings"], **settings}
    flags = []
    for flag in record["flags"]:
        for name, value in values.items():
            flag = flag.replace("${" + name + "}", str(value))
        flags.append(flag)
    return flags


def options_digest(flags: list[str]) -> str:
    compact = json.dumps(flags, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(compact.encode()).hexdigest()


def encoder_budget(flags: list[str]) -> int:
    """W4's P-20 finding: the encoder cache holds max(16384, --max-num-batched-tokens)."""
    batched = [int(flags[i + 1]) for i, flag in enumerate(flags[:-1])
               if flag == "--max-num-batched-tokens"]
    return max([16384, *batched])


def engine_ceiling_s(record: dict) -> int:
    """W4's P-20 arithmetic on the pinned flags: `decide.ceiling_s` of the encoder budget is
    the longest clip that fits."""
    return decide.ceiling_s(encoder_budget(served_flags(record)))


def published_release() -> dict:
    """What G6B's `marlin_release` publishes: the serving revision every admission pins."""
    from infrx.operations import service
    moment = datetime(2026, 9, 1, tzinfo=timezone.utc)
    serving, _deployment, card, requested = service.marlin_release(
        provider_org_id="00000000-0000-4000-8000-0000000000e4", created_at=moment,
        effective_at=moment)
    return {"requested_model": requested,
            "engine_options_digest": serving.engine_options_digest,
            "runtime_image_ref": serving.runtime_image_ref,
            "rate_card_version": card.rate_card_version}


def release_hashes() -> dict:
    """SHA/image/artifact/config hashes of the release candidate, from the tree."""
    record = serving_record()
    api = harness.API_ROOT
    return {
        "git": run.git_head(),
        "serving_version_json": sha256_file(MARLIN / "serving-version.json"),
        "serve_sh": sha256_file(MARLIN / "serve.sh"),
        "engine_options_digest": record["engine_options_digest"],
        "engine_options_digest_recomputed": options_digest(served_flags(record)),
        "runtime_image": record["runtime_image"]["ref"],
        "model_commit": record["model"]["commit"],
        "model_digests": {"weight_shards": record["model"]["weight_shard_digests"],
                          "tokenizer": record["model"]["tokenizer_digest"],
                          "chat_template": record["model"]["chat_template_digest"]},
        "contract_limits": _limits_digest(),
        "migrations": sha256_tree(harness.MIGRATIONS_DIR, "*.sql"),
        "deploy_tree": sha256_tree(api / "deploy"),
        "rollout_tree": sha256_tree(harness.REPO_ROOT / "infra" / "rollout"),
        "alert_rules": sha256_file(harness.REPO_ROOT / "infra" / "alerts" / "alerts.json"),
        "uv_lock": sha256_file(api / "uv.lock"),
        "infrx_package": sha256_tree(api / "infrx", "*.py"),
        "published_release": published_release(),
        # the box step reads both with `docker image inspect`; the served-build check
        # (`--box`) compares them, and a run off the box records what it was given
        "gateway_image": os.environ.get("INFRX_CERTIFY_GATEWAY_IMAGE"),
        "release_image": os.environ.get("INFRX_CERTIFY_RELEASE_IMAGE"),
    }


def _limits_digest() -> str:
    import dataclasses

    from infrx.contracts.limits import DEFAULTS
    doc = json.dumps({field.name: getattr(DEFAULTS, field.name)
                      for field in dataclasses.fields(DEFAULTS)}, sort_keys=True, default=str)
    return hashlib.sha256(doc.encode()).hexdigest()


# ------------------------------------------------------------------------------ E4B.a

GATE_STAGES = ("preflight", "services", "migrate", "rls")


def split_backend(cases: dict) -> dict[str, dict]:
    """The backend stage's classified cases, split into `protocol` (everything outside
    `recovery/`) and `recovery` (I3B's rc*/bk* drills), each in `run.classify`'s shape."""
    halves = {half: {"passed": [], "failed": [], "pending": {}, "skipped": []}
              for half in ("protocol", "recovery")}
    for bucket in ("passed", "failed", "skipped"):
        for name in cases[bucket]:
            halves["recovery" if run._is_recovery(name) else "protocol"][bucket].append(name)
    for task, names in cases["pending"].items():
        for name in names:
            half = halves["recovery" if run._is_recovery(name) else "protocol"]
            half["pending"].setdefault(task, []).append(name)
    return halves


def attributed_exit(backend: dict | None, cases: dict) -> int | None:
    """pytest's own exit code for the backend run (review N1), None if it never ran. An exit
    of 1 is explained when a case failed - the split attributes that case to its half - and
    any other code stays with both halves."""
    runs = (backend or {}).get("runs") or []
    if not runs:
        return None
    code = runs[0]["exit"]
    return 0 if code == 1 and cases["failed"] else code


def suite_check(report: Report, check_id: str, cases: dict, gate: list[dict],
                exit_code: int | None) -> None:
    """The gate's own verdict (`run.backend_verdict`, with pytest's exit code) on one half of
    the backend suite, after the stages it needs: a stack that did not come up, or a backend
    suite that never ran, is a failed certification run."""
    down = [f"{entry['stage']}={entry['status']}" for entry in gate if entry["status"] != PASS]
    if exit_code is None:
        down.append("backend=not run")
    counts = {"passed": len(cases["passed"]), "failed": cases["failed"] or None,
              "not_run": cases["skipped"] or None,
              "pending_by_id": {task: len(names) for task, names in cases["pending"].items()}}
    if down:
        report.check(check_id, FAIL, {"stages_not_passed": down, **counts})
        return
    status = run.backend_verdict(cases, exit_code)
    report.check(check_id, status, counts, owners=cases["pending"] if status == PENDING else ())


def deployed_cap_s() -> float:
    """The deployed MAX_VIDEO_SECONDS: the gateway's environment (the box step's --env-file),
    parsed by the gateway's own `pilot_from_env`; unset, the tree's default."""
    from infrx.config import pilot_from_env
    return pilot_from_env().max_video_seconds


def refused_over_cap(row: dict) -> bool:
    """A bench attempt answered with the typed over-cap refusal (status and code)."""
    return (row.get("outcome"), row.get("http_status"), row.get("error_code")) == (
        "rejected", OVER_CAP["http_status"], OVER_CAP["code"])


def admission_answer(target: dict, clip: dict, path: Path) -> dict:
    """One clip sent to the gateway as bench.py sends it (inline, the release's model, the
    client's key): the status and the error's code and param - allowlisted, nothing else.
    A 429 (capacity, checked before the media) is asked once more after its Retry-After."""
    body = {"model": target["model"], "max_tokens": 16,
            "messages": bench.messages_for({"form": "video_b64", "prompt": clip["prompt"]},
                                           bench.data_url(str(path)))}
    key = bench.api_key()
    request = urllib.request.Request(f"{target['base_url']}/chat/completions",
                                     json.dumps(body).encode(),
                                     {"content-type": "application/json",
                                      "authorization": f"Bearer {key}"})
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(request, timeout=600) as answer:
                return {"http_status": answer.status, "code": None, "param": None}
        except urllib.error.HTTPError as refused:
            if refused.code == 429 and attempt == 1:
                wait = bench.as_float(refused.headers.get("retry-after"))
                time.sleep(min(max(1.0 if wait is None else wait, 0.0), 60.0))
                continue
            try:
                error = json.loads(refused.read()).get("error")
            except (ValueError, AttributeError):
                error = None
            error = error if isinstance(error, dict) else {}
            return {"http_status": refused.code,
                    "code": bench.allow(error.get("code"), bench.CODE_OK, key),
                    "param": bench.allow(error.get("param"), bench.CODE_OK, key)}


def parity_check(report: Report, *, engine_url: str, workdir: Path, baseline: Path | None,
                 label: str, local: bool, cap_s: float, gateway: dict | None = None) -> None:
    """MARLIN-SOP: parity.py at c = 1 over W4's parity set, paired on the clips' bytes - the
    clips within the deployed cap. A clip over it never reaches the engine through the
    product: it is an expected refusal, asked of the gateway (`gateway`: the target, when it
    has admission) and judged as the M layer's typed refusal; an engine target pends it."""
    cache, clips = corpus_cache(), parity.clips()
    over = sorted(clip for clip in parity.PARITY_SET if clips[clip]["duration_s"] > cap_s)

    def parity_run(name: str) -> Path:
        out = workdir / f"parity-{name}.jsonl"
        out.unlink(missing_ok=True)
        done = client([sys.executable, str(MARLIN / "measure" / "parity.py"),
                       "--engine", engine_url, "--cache", str(cache), "--out", str(out)])
        if done["exit"] != 0:
            raise RuntimeError(f"parity.py exit {done['exit']}: {done['tail'][-300:]}")
        return out

    if baseline is None and not local:
        report.check("e4b.a.sop-parity", PENDING,
                     "no --parity-baseline: pair the release engine with W4's E0 parity.jsonl "
                     "(or the last certified release's)", owners=("BOX",))
        return
    try:
        base = baseline or parity_run("baseline")
        candidate = parity_run("candidate")
        answers = {clip: admission_answer(gateway, clips[clip], cache / clips[clip]["file"])
                   for clip in over} if gateway else {}
    except (RuntimeError, OSError) as failed:
        report.check("e4b.a.sop-parity", FAIL, str(failed), label=label)
        return
    within = (lambda rows: [row for row in rows if row["clip_id"] not in over])
    verdict, why = decide.parity_verdict(within(decide.jsonl(candidate)),
                                         within(decide.jsonl(base)))
    status = {decide.PASS: PASS, decide.FAIL: FAIL}.get(verdict, PENDING)
    # a clip refused for capacity twice was never judged by the cap: listed apart, and the
    # cell still FAILs - parity must observe the typed refusal itself
    busy = sorted(clip for clip, answer in answers.items() if answer["http_status"] == 429)
    wrong = sorted(clip for clip, answer in answers.items()
                   if answer != OVER_CAP and answer["http_status"] != 429)
    if wrong or busy:
        status = FAIL
    elif over and not gateway and status == PASS:
        status = PENDING                  # an engine target has no admission to refuse them
    report.check("e4b.a.sop-parity", status,
                 {"verdict": verdict, "why": why, "cap_s": cap_s,
                  "expected refusal (over MAX_VIDEO_SECONDS)": {
                      clip: answers.get(clip, "unasked: an engine target has no admission")
                      for clip in over},
                  "not refused as over the cap": wrong, "capacity twice, unjudged": busy,
                  "baseline": rel(base),
                  "candidate": rel(candidate), "candidate_sha256": sha256_file(candidate)},
                 owners=("BOX",) if status == PENDING else (), label=label)


def corpus_cache() -> Path:
    """The corpus cache the bench client reads ($CORPUS_CACHE, else the main checkout's)."""
    manifest = MARLIN / "corpus" / "manifest.json"
    return Path(bench.corpus_cache_root(json.loads(manifest.read_text()), str(manifest)))


# --- dataset resume: the client half ---------------------------------------------------

def torn_by_the_client(row: dict) -> bool:
    """A first-run attempt the client itself ended: a transport error, which bench records
    by its exception type - never a server's answer (`http_NNN`) or a platform failure
    (`stream_error_event` and the other platform classes)."""
    kind = row.get("error_class") or ""
    return bool(kind) and not kind.startswith("http_") and kind not in bench.PLATFORM_ERROR_CLASSES


def cancelled_replays(first: list[dict], second: list[dict]) -> tuple[list[str], list[str]]:
    """R106: the items whose replay answered that their job was cancelled, split by what
    ended the item's first-run attempt - the client's own tear (cancelled by the
    interruption) or anything else (cancelled by the platform, which fails the drill)."""
    firsts = {row["item_key"]: row for row in first}
    cancelled = sorted({row["item_key"] for row in first + second
                        if row.get("outcome") == bench.CANCELLED_REPLAY})
    # an item in flight at the SIGINT has no first-run row at all (bench's attempt() never
    # reaches its write when the task is cancelled): its key replayed, so it was sent, and
    # the client's own exit cut it - the interruption's tear (verifier B1, box run2's 643711ed)
    torn = [item for item in cancelled if item not in firsts or torn_by_the_client(firsts[item])]
    return torn, [item for item in cancelled if item not in torn]


def sop_property(first: list[dict], second: list[dict]) -> str:
    """The MARLIN-SOP property a drill with no client problem has proved, with the actual
    counts (a FAIL that lists a platform cancel says so here too)."""
    torn, platform = cancelled_replays(first, second)
    return (f"MARLIN-SOP: no second accepted item, nothing re-sent after it was terminal, "
            f"and {len(torn)} item(s) cancelled by the interruption (the client's own tear), "
            f"each terminal after exactly one replay of its key; {len(platform)} cancelled by "
            f"the platform (R106)")


def resume_problems(first: list[dict], second: list[dict], *, items: int,
                    first_interrupted: bool) -> list[str]:
    """The client's side of MARLIN-SOP's resume (E1B L6): an interruption that happened,
    one key per item, nothing terminal re-sent, no item accepted twice, every item
    terminal at the end - an item the interruption cancelled after exactly one replay of
    its key (R106: a cancelled job's replay is terminal for that key), and no item the
    platform cancelled (a replay cancelled whose first attempt was not the client's tear)."""
    problems = []
    accepted = {row["item_key"] for row in first if row.get("outcome") == "accepted"}
    if not first_interrupted or not 0 < len(accepted) < items:
        problems.append(f"not interrupted mid-run (interrupted={first_interrupted}, "
                        f"{len(accepted)} of {items} items accepted): the drill proved nothing")
    keys: dict[str, set] = {}
    for row in first + second:
        keys.setdefault(row["item_key"], set()).add(row.get("idempotency_key"))
    split = sorted(item for item, seen in keys.items() if len(seen) != 1)
    if split:
        problems.append(f"items sent under more than one key: {split}")
    terminal = {row["item_key"] for row in first if bench.is_terminal(row)}
    resent = sorted(terminal & {row["item_key"] for row in second})
    if resent:
        problems.append(f"terminal items re-sent by the resume: {resent}")
    last = {row["item_key"]: row for row in first + second}
    if len(last) != items:
        problems.append(f"{len(last)} distinct items across both runs, {items} scheduled")
    open_items = sorted(item for item, row in last.items() if not bench.is_terminal(row))
    if open_items:
        problems.append(f"items not terminal after the resume: {open_items}")
    acceptances = collections.Counter(row["item_key"] for row in first + second
                                      if row.get("outcome") == "accepted")
    twice = sorted(item for item, count in acceptances.items() if count > 1)
    if twice:
        problems.append(f"items accepted more than once: {twice}")
    replays = collections.Counter(row["item_key"] for row in second)
    torn, platform = cancelled_replays(first, second)
    if platform:
        problems.append(f"items cancelled by the platform (their first attempt was not the "
                        f"client's tear): {platform}")
    unreplayed = [item for item in torn + platform if replays[item] != 1]
    if unreplayed:
        problems.append(f"cancelled items not terminal after exactly one replay: {unreplayed}")
    return problems


# --- dataset resume: the server half ---------------------------------------------------

def reconcile_problems(rows: list[dict], usage, holds, before, after) -> list[str]:
    """MARLIN-SOP's no-duplicate property on the tenant's own ledger (G6B `TenantSession`):
    one Inference-Id per accepted item across both runs, one CREDIT usage record and one
    hold (released) per accepted job, Σ charged = the ledger's fall, reserved restored -
    but for the holds of the jobs the interruption cancelled (R106) that are still held: a
    cancel carries no usage, and after output its hold stays held until the platform
    releases it (R21, `held_unknown`)."""
    problems = []
    ids: dict[str, set] = {}
    for row in rows:
        if row.get("outcome") == "accepted":
            ids.setdefault(row["item_key"], set()).add(row.get("inference_id"))
    duplicated = sorted(item for item, seen in ids.items() if len(seen) != 1 or None in seen)
    if duplicated:
        problems.append(f"items accepted as more than one job (or with no Inference-Id): "
                        f"{duplicated}")
    jobs = {job for seen in ids.values() for job in seen if job}
    records = [entry for entry in usage if entry.request_id in jobs]
    per_job = {job: sum(1 for entry in records if entry.request_id == job) for job in jobs}
    wrong = sorted(job for job, count in per_job.items() if count != 1)
    if wrong:
        problems.append(f"jobs without exactly one usage record: "
                        f"{ {job: per_job[job] for job in wrong} }")
    usd = [entry.request_id for entry in records if entry.unit != "CREDIT"]
    if usd:
        problems.append(f"usage recorded outside CREDIT: {usd}")
    held = [hold.request_id for hold in holds
            if hold.request_id in jobs and str(hold.state) == "held"]
    if held:
        problems.append(f"holds still held after every item is terminal: {held}")
    hold_count = {job: sum(1 for hold in holds if hold.request_id == job) for job in jobs}
    if any(count != 1 for count in hold_count.values()):
        problems.append(f"jobs without exactly one hold: {hold_count}")
    charged = sum((Decimal(entry.charged_amount) for entry in records), Decimal(0))
    fell = Decimal(str(before.ledger_total)) - Decimal(str(after.ledger_total))
    if charged != fell:
        problems.append(f"Σ charged {charged} != ledger fall {fell}")
    cancelled = {row.get("inference_id") for row in rows
                 if row.get("outcome") == bench.CANCELLED_REPLAY} - {None}
    kept = sum((Decimal(str(hold.amount)) for hold in holds
                if hold.request_id in cancelled and str(hold.state) == "held"), Decimal(0))
    if Decimal(str(after.reserved_total)) != Decimal(str(before.reserved_total)) + kept:
        problems.append(f"reserved {before.reserved_total} -> {after.reserved_total}"
                        + (f" ({kept} held for the interruption's cancels)" if kept else ""))
    return problems


def tenant_ledger():
    """The provisioned client's own view (G6B `Operations.tenant(secret)`), or None where
    `build_operations` refuses: without the deployment's DATABASE_URL (off the box)."""
    from infrx.operations import cli
    try:
        ops = cli.build_operations()
    except SystemExit:
        return None
    secret = next((os.environ[name] for name in bench.KEY_ENV if os.environ.get(name)), None)

    async def view():
        tenant = await ops.tenant(secret)
        return await tenant.balance(), (await tenant.usage()).entries, await tenant.holds()
    return lambda: asyncio.run(view())


# --- the bench client ------------------------------------------------------------------

class Blocked(Exception):
    """A remote bench cell without its run profile or key inventory: it never starts."""


def profile_blocked(target: dict) -> str | None:
    """Why a remote target's bench cells may not start, or None (a local target needs no
    profile). E1C: no paid cell runs unbounded, and an unprofiled one is INVALID anyway."""
    if target["kind"] == "local":
        return None
    paths = {"--run-profile": target.get("run_profile"),
             "--key-inventory": target.get("key_inventory")}
    absent = [flag for flag, path in paths.items() if not path or not Path(path).is_file()]
    if absent:
        return f"BLOCKED: a remote bench cell needs {' and '.join(absent)} (a readable file)"
    try:
        schema = json.loads(Path(target["run_profile"]).read_text()).get("schema")
    except (OSError, ValueError, AttributeError) as e:
        return f"BLOCKED: --run-profile unreadable: {type(e).__name__}"
    if schema != RUN_PROFILE_SCHEMA:
        return f"BLOCKED: --run-profile schema is {schema!r}, not {RUN_PROFILE_SCHEMA}"
    return None


RUN_PROFILE_SCHEMA = "infrx.run-profile/1"


def edge_host(path) -> str | None:
    """The first `target.allowlist` host of a readable --overload-profile, or None when it
    has no target block or no host there (E4P-V7: never a KeyError in the load cells)."""
    target = json.loads(Path(path).read_text()).get("target")
    hosts = target.get("allowlist") if isinstance(target, dict) else None
    return hosts[0] if isinstance(hosts, list) and hosts and isinstance(hosts[0], str) \
        and hosts[0] else None
BENCH_FORMS, BENCH_MAX_TOKENS = "video_b64", "128,512,1024"


def cell_profile(target: dict, workdir: Path, name: str, *, rate: float,
                 dataset_version: str, corpus: Path) -> Path:
    """The coordinator's base profile stamped with this cell's run shape - run id, dataset
    version, arrival, the manifest this runner hands bench, seed, forms and output mix; the
    overload cell's profile class is P4.
    Identity, target, bounds, spend and item ids stay the coordinator's: bench refuses the
    cell (exit 2, a FAIL) when they do not cover it."""
    profile = json.loads(Path(target["run_profile"]).read_text())
    profile["identity"]["run_id"] = f"{profile['identity']['run_id']}-{name}"[:64]
    profile["workload"].update(dataset_version=dataset_version, seed=SEED,
                               manifest_sha256=sha256_file(corpus),
                               forms=[BENCH_FORMS],
                               max_tokens_mix=[int(t) for t in BENCH_MAX_TOKENS.split(",")])
    profile["measurement"].update(arrival="open-loop", rate_per_s=rate)
    if name == "overload":                 # S3 F5: bench refuses a P4 cell off the public edge
        profile["measurement"]["profile_class"] = "P4"
    path = workdir / f"{name}-profile.json"
    path.write_text(json.dumps(profile, indent=2))
    return path


def bench_argv(target: dict, workdir: Path, name: str, *, rate: float, requests: int,
               dataset_version: str, extra: tuple[str, ...] = (),
               corpus: Path = MARLIN / "corpus" / "manifest.json") -> list[str]:
    """E1B's client, as the protocol shapes it: licensed corpus, frozen seed, output mix,
    no retries (a retry may not hide a refusal), inline media. Outputs in `workdir` only.
    A remote cell runs under its own stamped profile and the key inventory, or not at all."""
    if why := profile_blocked(target):
        raise Blocked(why)
    # bench appends to --out and may refuse (exit 2) before opening either file: a reused
    # workdir's previous summary and rows must never be read as this run's (CW-V1)
    for stale in (workdir / f"{name}.jsonl", workdir / f"{name}-raw.jsonl"):
        stale.unlink(missing_ok=True)
    argv = [sys.executable, str(MARLIN / "bench.py"),
            "--corpus", str(corpus),
            "--subset", "full" if target["scale"] == "box" else "fast",
            "--base-url", target["base_url"], "--target", target["bench_target"],
            "--rate", str(rate), "--requests", str(requests), "--seed", str(SEED),
            "--dataset-version", dataset_version, "--forms", BENCH_FORMS,
            "--max-tokens", BENCH_MAX_TOKENS, "--retries", "0",
            "--out", str(workdir / f"{name}.jsonl"), "--raw", str(workdir / f"{name}-raw.jsonl")]
    if target["bench_target"] == "gateway":
        argv += ["--model", target["model"]]
    if target["kind"] != "local":
        argv += ["--profile", str(cell_profile(target, workdir, name, rate=rate,
                                               dataset_version=dataset_version, corpus=corpus)),
                 "--key-inventory", str(target["key_inventory"])]
    return argv + list(extra)


# N12 (box run2): a flat hour cut the 4 h soak (exit 124). A bench run gets its own
# schedule plus this margin: bench's per-request timeout (600 s) and the tail after the
# last arrival. A client with no schedule (parity.py) keeps the hour.
CLIENT_MARGIN_S = 900.0
CLIENT_TIMEOUT_S = 3600.0


def client_timeout_s(argv: list[str]) -> float:
    """A bench run's bound: its requests over its open-loop rate, plus CLIENT_MARGIN_S."""
    if "--rate" not in argv:
        return CLIENT_TIMEOUT_S
    rate, requests = float(argv[argv.index("--rate") + 1]), int(argv[argv.index("--requests") + 1])
    return requests / rate + CLIENT_MARGIN_S


def client(argv: list[str], env: dict | None = None) -> dict:
    """One client process (bench.py, parity.py) from the repository root, run.py's way."""
    return run.shell(argv, cwd=harness.REPO_ROOT, env=env, timeout=client_timeout_s(argv))


def bench_env(target: dict) -> dict:
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    if target["kind"] == "local":
        env["MARLIN_API_KEY"] = "e4b-local-fake-engine-key"     # the fake engine reads none
    return env


def raw_rows(path: Path) -> list[dict]:
    return bench.read_attempts(str(path)) if path.exists() else []


def interrupted_run(argv: list[str], raw: Path, *, after: int, env: dict,
                    timeout_s: float) -> dict:
    """Run the client and SIGINT it once `after` items are accepted - the dataset client's
    crash, mid-run. bench.py writes each finished attempt as it lands and exits 130."""
    process = subprocess.Popen(argv, cwd=harness.REPO_ROOT, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, start_new_session=True)
    signalled, end = False, time.monotonic() + timeout_s
    while process.poll() is None and time.monotonic() < end:
        if sum(row.get("outcome") == "accepted" for row in raw_rows(raw)) >= after:
            process.send_signal(signal.SIGINT)
            signalled = True
            break
        time.sleep(0.02)
    try:
        process.communicate(timeout=max(end - time.monotonic(), 30.0))
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
    return {"exit": process.returncode, "signalled": signalled}


def within_cap_corpus(workdir: Path, cap_s: float) -> Path:
    """The licensed corpus less the clips the deployed cap refuses, cached where the corpus
    is: the dataset drill schedules only items the gateway may accept."""
    data = json.loads((MARLIN / "corpus" / "manifest.json").read_text())
    data["clips"] = [clip for clip in data["clips"] if clip["derived"]["duration_s"] <= cap_s]
    data["cache_root_default"] = str(corpus_cache())
    path = workdir / "corpus-within-cap.json"
    path.write_text(json.dumps(data))
    return path


SETTLE_WAIT_S = 300.0      # a debit may land after the answer: the ledger is re-read until then


def dataset_check(report: Report, target: dict, workdir: Path, cap_s: float, ledger=None,
                  settle_wait_s: float = SETTLE_WAIT_S) -> None:
    """E4B.a's large-dataset recipe over the clips within the deployed cap: interruption,
    resume, and (on a metered target) the tenant's ledger reconciled item by item."""
    shape = MATRIX[target["scale"]]["dataset"]
    env, corpus = bench_env(target), within_cap_corpus(workdir, cap_s)
    version = f"e4b-{(report.head.get('sha') or 'nosha')[:7]}-" \
              f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    ledger = ledger if ledger is not None else (tenant_ledger() if target["metered"] else None)
    before = ledger()[0] if ledger else None
    argv = bench_argv(target, workdir, "dataset", rate=shape["rate"],
                      requests=shape["items"], dataset_version=version, corpus=corpus)
    first = interrupted_run(argv, workdir / "dataset-raw.jsonl",
                            after=shape["interrupt_after"], env=env, timeout_s=1800.0)
    resumed = client(bench_argv(target, workdir, "dataset-resume", rate=shape["rate"],
                                requests=shape["items"], dataset_version=version,
                                extra=("--resume", str(workdir / "dataset-raw.jsonl")),
                                corpus=corpus), env)
    rows_first = raw_rows(workdir / "dataset-raw.jsonl")
    rows_second = raw_rows(workdir / "dataset-resume-raw.jsonl")
    problems = resume_problems(rows_first, rows_second, items=shape["items"],
                               first_interrupted=first["signalled"] and first["exit"] == 130)
    if resumed["exit"] != 0:
        problems.append(f"the resumed run exited {resumed['exit']}: {resumed['tail'][-200:]}")
    capped = sorted({row["item_key"] for row in rows_first + rows_second if refused_over_cap(row)})
    if capped:
        problems.append(f"items within the runner's cap ({cap_s:g} s) refused as over "
                        f"MAX_VIDEO_SECONDS - the gateway's cap is another: {capped}")
    measured = {"dataset_version": version, "items": shape["items"], "cap_s": cap_s,
                "first_run": {**first, "attempts": len(rows_first),
                              "accepted": sum(r.get("outcome") == "accepted" for r in rows_first)},
                "resume": {"exit": resumed["exit"], "attempts": len(rows_second),
                           "replayed": sum(bool(r.get("idempotency_replayed"))
                                           for r in rows_second)},
                "client_problems": problems or None,
                "cancelled_by_interruption": cancelled_replays(rows_first, rows_second)[0],
                "cancelled_by_the_platform": cancelled_replays(rows_first, rows_second)[1],
                "sop": sop_property(rows_first, rows_second)}
    if problems:
        report.check("e4b.a.dataset-resume", FAIL, problems, measured=measured,
                     label=target["label"])
        return
    if not target["metered"]:
        report.check("e4b.a.dataset-resume", PENDING,
                     "client invariants hold; the ledger half needs a metered endpoint (the "
                     "local target is the engine: no admission, no idempotency, no ledger)",
                     owners=("BOX",), measured=measured, label=target["label"])
        return
    if ledger is None:
        report.check("e4b.a.dataset-resume", PENDING,
                     "client invariants hold; `infrx.operations.cli.build_operations` refuses "
                     "without the deployment's DATABASE_URL, so the tenant's ledger cannot be "
                     "read here", owners=("BOX",), measured=measured, label=target["label"])
        return
    end = time.monotonic() + settle_wait_s
    while True:
        balance, usage, holds = ledger()
        problems = reconcile_problems(rows_first + rows_second, usage, holds, before, balance)
        if not problems or time.monotonic() >= end:
            break
        time.sleep(5)
    measured["ledger"] = {"before": str(before.ledger_total), "after": str(balance.ledger_total),
                          "reserved_after": str(balance.reserved_total)}
    report.check("e4b.a.dataset-resume", FAIL if problems else PASS,
                 problems or f"reconciled; {measured['sop']}",
                 measured=measured, label=target["label"])


# ------------------------------------------------------------------------------ E4B.b

# The settings the earlier evidence was measured under. A tree (or a box) that differs has
# moved past that evidence: the check fails, naming it, until it is re-measured and
# re-declared here ("reject any optimization that invalidates earlier evidence").
def declared() -> dict:
    """What the earlier evidence was measured under - read, never typed (review F6): W3's
    serving record, which W4 phase B re-declares by changing it, and the settings M4 and the
    contract fixed. The published release must be that record."""
    record = serving_record()
    digest, image = record["engine_options_digest"], record["runtime_image"]["ref"]
    seqs = record["settings"]["ENGINE_MAX_NUM_SEQS"]
    return {
        "engine_options_digest": (digest, "W3 serving-version.json (W4-ecacd50 phase A adopted "
                                          "no candidate, so E0 - the W3 pin - stands)"),
        "runtime_image": (image, "W3 serving-version.json runtime_image.ref"),
        "engine_max_num_seqs": (seqs, "W3 serving-version.json settings"),
        "contract_engine_max_num_seqs": (int(seqs), "contracts limits = the W3 setting"),
        "encoder_budget_tokens": (encoder_budget(served_flags(record)),
                                  "W4 P-20: max(16384, --max-num-batched-tokens) of the record"),
        "profile_version": ("v1", "S2M profile v1; M4-8179144's MEDIA-PARITY oracle"),
        "preparation_concurrency": (2, "M4-8179144 'Measured, not taken': stays 2"),
        "max_preparing_jobs": (8, "contracts limits, untouched by M4"),
        "max_video_seconds": (82.0, "contracts limits: the approved release ceiling (P-20 "
                                    "decision B; the code default since G7 WR-1)"),
        "published_engine_options_digest": (digest, "R76/R78: the serving revision every "
                                                    "admission pins is the measured one"),
        "published_runtime_image": (image, "R76/R78, as above"),
    }


def serve_sh_pins() -> dict:
    """The launch script's own pins - the second source the record is held against."""
    text = (MARLIN / "serve.sh").read_text()
    image = re.search(r"^IMAGE=\$\{IMAGE:-(\S+)\}$", text, re.M)
    seqs = re.search(r"^ENGINE_MAX_NUM_SEQS=\$\{ENGINE_MAX_NUM_SEQS:-(\d+)\}$", text, re.M)
    # Review V2: every mention outside a comment must be a literal integer - a variable
    # (`"$BATCHED"`) is a budget this read cannot know, so the pin fails on None.
    batched = [match.group(1) for line in text.splitlines()
               if not line.lstrip().startswith("#")
               for match in re.finditer(r"--max-num-batched-tokens(?:[ =]+(\S+))?", line)]
    literal = [re.fullmatch(r'"?(\d+)"?', value or "") for value in batched]
    budget = None if None in literal else max([16384, *(int(m.group(1)) for m in literal)])
    return {"image": image and image.group(1), "seqs": seqs and seqs.group(1),
            "encoder_budget": budget}


def current_config() -> dict:
    """The same settings, read from the tree's other sources: the flags the record's digest
    is recomputed from, serve.sh, the contract limits and the release G6B publishes."""
    from infrx.contracts.limits import DEFAULTS
    record, published, pins = serving_record(), published_release(), serve_sh_pins()
    return {"engine_options_digest": options_digest(served_flags(record)),
            "runtime_image": pins["image"],
            "engine_max_num_seqs": pins["seqs"],
            "contract_engine_max_num_seqs": DEFAULTS.engine_max_num_seqs,
            "encoder_budget_tokens": pins["encoder_budget"],
            "profile_version": record["profile_version"],
            "preparation_concurrency": DEFAULTS.preparation_concurrency,
            "max_preparing_jobs": DEFAULTS.max_preparing_jobs,
            "max_video_seconds": DEFAULTS.max_video_seconds,
            "published_engine_options_digest": published["engine_options_digest"],
            "published_runtime_image": published["runtime_image_ref"]}


def config_problems(current: dict, pinned: dict | None = None) -> list[str]:
    pinned = declared() if pinned is None else pinned
    return [f"{name}: {current.get(name)!r} is not the declared {value!r} ({source}) - "
            f"re-measure and re-declare, or restore it"
            for name, (value, source) in pinned.items() if current.get(name) != value]


def _pairs(flags: list[str]) -> set[tuple[str, str | None]]:
    return {(flag, flags[i + 1] if i + 1 < len(flags) and not flags[i + 1].startswith("--")
             else None) for i, flag in enumerate(flags) if flag.startswith("--")}


def inventory_problems(text: str, record: dict) -> list[str]:
    """The deployed engine (W3's `measure/inventory.sh` output) against the pinned launch:
    the image is the pin, and its flags are exactly the served flags, nothing more."""
    lines = dict(line.split("=", 1) for line in text.splitlines()
                 if re.match(r"^[a-z_]+=", line))
    problems = []
    if lines.get("image_equals_pin") != "yes":
        problems.append(f"image_equals_pin={lines.get('image_equals_pin')}")
    try:
        args = json.loads(lines["args"])
    except (KeyError, ValueError):
        return problems + ["no readable args= line"]
    expected = served_flags(record)
    missing, extra = sorted(_pairs(expected) - _pairs(args)), sorted(_pairs(args) - _pairs(expected))
    if missing:
        problems.append(f"pinned flags the engine does not run: {missing}")
    if extra:
        problems.append(f"flags the engine runs beyond the pin: {extra}")
    return problems


def config_pin_check(report: Report, inventory: Path | None, cap_s: float) -> None:
    problems = config_problems(current_config())
    measured = {"current": current_config(), "deployed_max_video_seconds": cap_s}
    if inventory is not None:
        problems += [f"deployed: {p}" for p in inventory_problems(inventory.read_text(),
                                                                  serving_record())]
        measured["inventory_sha256"] = sha256_file(inventory)
    if problems:
        report.check("e4b.b.config-pin", FAIL, problems, measured=measured)
    elif inventory is None:
        report.check("e4b.b.config-pin", PENDING, "the tree matches the declared settings; the "
                     "deployed engine is unread (pass --inventory with inventory.sh's output)",
                     owners=("BOX",), measured=measured)
    else:
        report.check("e4b.b.config-pin", PASS, "tree and deployed engine match", measured=measured)


# An App or Lab package directory of any checkout (review F7: not only this checkout's).
APP_OR_LAB = re.compile(r"(?:^|/)apps/(?:app|lab)(?:/|$)")


def next_servers(proc: Path = Path("/proc")) -> list[dict]:
    """Next.js servers of the App or the Lab on this host, whichever checkout they run from:
    a Next.js process whose working directory or command line lies in an `apps/app` or
    `apps/lab` package. One whose working directory cannot be read is counted too - an
    unknown is not a stopped App (review F7; inside a container that is the usual case)."""
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            argv = [part.decode(errors="replace")
                    for part in (entry / "cmdline").read_bytes().split(b"\0") if part]
        except OSError:
            continue                    # gone, or no command line to tell what it runs
        head = os.path.basename(argv[0]).split()[0] if argv else ""
        is_next = head == "next-server" or (
            head in ("node", "next") and {"start", "dev"} & set(argv[1:])
            and any("next" in part for part in argv[:3]))
        if not is_next:
            continue
        try:
            cwd = os.readlink(entry / "cwd")
        except OSError:
            cwd = None
        if cwd is None or APP_OR_LAB.search(cwd) or any(APP_OR_LAB.search(part) for part in argv):
            found.append({"pid": int(entry.name), "cwd": cwd, "command": " ".join(argv)[:80]})
    return sorted(found, key=lambda server: server["pid"])


def scrape(url: str) -> dict | None:
    """One read of a Prometheus endpoint, the series this runner judges (MiB for memory)."""
    from infrx.observe import alerts
    try:
        with urllib.request.urlopen(url, timeout=5) as answer:
            samples = alerts.parse(answer.read().decode(errors="replace"))
    except (OSError, ValueError):
        return None

    def total(name, **labels):
        values = [value for (series, have), value in samples.items()
                  if series == name and set(labels.items()) <= set(have)]
        return sum(values) if values else None
    mib = (lambda value: None if value is None else round(value / 2 ** 20, 1))
    build = next((dict(labels) for (series, labels), value
                  in samples.items() if series == "infrx_build_info" and value == 1), {})
    return {"rss_mib": mib(total("infrx_process_resident_bytes")),
            "gpu_used_mib": mib(total("infrx_gpu_memory_bytes", state="used")),
            "drift": total("infrx_reconciliation_drift"),
            "unsettleable": total("infrx_unsettleable_jobs"),
            "running": total("vllm:num_requests_running"),
            "waiting": total("vllm:num_requests_waiting"),
            "revision": build.get("revision"), "process": build.get("process")}


def served_build_problems(gateway: dict | None, head_sha: str | None, gateway_image: str | None,
                          release_image: str | None, worker: dict | None) -> list[str]:
    """Review F3: the report's hashes are the release the box serves. The gateway and the
    worker (I2B-R4) each name their revision on their own /metrics (`infrx_build_info`, its
    `process` label saying whose page it is), which must be the report's tree, and the
    gateway runs the image install.sh built and tagged for that release."""
    problems = []
    for process, scraped in (("gateway", gateway), ("worker", worker)):
        revision = (scraped or {}).get("revision")
        if scraped is None:
            problems.append(f"the {process}'s /metrics is unreadable: its build is unknown")
        elif revision is None:
            problems.append(f"the {process} publishes no infrx_build_info{{revision}}, so the "
                            "build it serves is unknown")
        elif scraped.get("process") != process:
            problems.append(f"the {process}'s /metrics is the {scraped.get('process')}'s page: "
                            "its build is unread")
        elif not (head_sha and len(str(revision)) >= 7 and head_sha.startswith(str(revision))):
            problems.append(f"the {process} serves {revision}, the report's tree is {head_sha}")
    if not gateway_image:
        problems.append("INFRX_CERTIFY_GATEWAY_IMAGE is unset: the serving image is unrecorded")
    if not release_image:
        problems.append("INFRX_CERTIFY_RELEASE_IMAGE is unset: the release image is unrecorded")
    elif gateway_image and gateway_image != release_image:
        problems.append(f"the gateway runs {gateway_image}, not the release image {release_image}")
    return problems


def served_build_check(report: Report, metrics_url: str, worker_metrics_url: str) -> dict:
    tree = report.head.get("sha")
    problems = served_build_problems(scrape(metrics_url), tree,
                                     os.environ.get("INFRX_CERTIFY_GATEWAY_IMAGE"),
                                     os.environ.get("INFRX_CERTIFY_RELEASE_IMAGE"),
                                     scrape(worker_metrics_url))
    return report.check("e4b.b.served-build", FAIL if problems else PASS,
                        problems or f"the gateway and the worker serve the report's tree {tree} "
                                    f"({report.head.get('source', 'git')}), the gateway from "
                                    "the release image")


def preconditions_check(report: Report, target: dict, box: bool) -> dict:
    """Protocol §2: App/Lab stopped everywhere; on the box also the window consent, an idle
    engine and every parity clip in the cache."""
    servers = next_servers()
    problems = [f"App/Lab running: pid {s['pid']} in "
                f"{s['cwd'] or 'an unreadable directory (unknown is not stopped)'} ({s['command']})"
                for s in servers]
    if box:
        if os.environ.get("E4B_WINDOW_OK") != "1":
            problems.append("E4B_WINDOW_OK=1 (a logged maintenance window) is not set")
        engine = scrape(f"{target['engine_url'].rstrip('/')}/metrics")
        busy = None if engine is None or engine["running"] is None \
            else (engine["running"] or 0) + (engine["waiting"] or 0)
        if busy != 0:
            problems.append(f"the engine is not idle (running+waiting = {busy})")
        clips = client([sys.executable, str(MARLIN / "measure" / "parity.py"), "--check",
                        "--cache", str(corpus_cache())])
        if clips["exit"] != 0:
            problems.append(f"parity clips missing: {clips['tail'][-200:]}")
    return report.check("e4b.b.preconditions", FAIL if problems else PASS,
                 problems or "App and Lab stopped" + (", window open, engine idle, clips present"
                                                      if box else ""),
                 measured={"next_servers": servers})


# --- the bench cells -------------------------------------------------------------------

UNKNOWN = decide.UNKNOWN


def summarise(verdicts: list[tuple]) -> tuple[str, tuple[str, ...]]:
    """(status, owners) of a cell from its (criterion, verdict, detail, owner) rows."""
    states = {verdict for _, verdict, _, _ in verdicts}
    if decide.FAIL in states:
        return FAIL, ()
    if UNKNOWN in states:
        return PENDING, tuple(sorted({owner for _, v, _, owner in verdicts if v == UNKNOWN}))
    return PASS, ()


def _duration(row: dict, clips: dict) -> float:
    return clips.get(row.get("clip_id"), {}).get("duration_s", 0.0)


def judged(rows: list[dict], clips: dict, cap_s: float) -> list[dict]:
    """The attempts a cell judges: clips within the deployed cap (the rest are the cap's,
    P-20; a clip at the cap is within it - the M layer refuses only a longer one)."""
    return [row for row in rows if _duration(row, clips) <= cap_s]


def failures(rows: list[dict]) -> tuple:
    """Review F4: every attempt that got no answer counts - the platform-caused ones (a 5xx,
    a broken stream) and the transport ones (a timeout, a reset) alike, because a client that
    got nothing was not served within the envelope; a refusal or a client cancel is not a
    failure. The platform-caused share is kept in the detail (R21 makes those free)."""
    failed = [r for r in rows if r.get("outcome") == "failed"]
    platform = sum(bench.is_platform_failure(r) for r in failed)
    rate = len(failed) / len(rows) if rows else None
    return ("failure_rate", UNKNOWN if rate is None else
            decide.PASS if rate < CRITERIA["max_failure_rate"] else decide.FAIL,
            f"{len(failed)}/{len(rows)} ({platform} platform-caused)", "BOX")


def answered(rows: list[dict]) -> tuple:
    """A cell that accepted nothing supports nothing, whatever else it measured."""
    accepted = sum(r.get("outcome") == "accepted" for r in rows)
    return ("answered", decide.PASS if accepted else decide.FAIL, f"{accepted} accepted", "BOX")


def short_clip(clip: dict) -> bool:
    """01 §2.3's TTFT class: a clip of at most 30 s at no more than 720p (the long edge)."""
    return (clip.get("duration_s", 0.0) <= CRITERIA["short_clip_max_s"]
            and max(clip.get("width", 0), clip.get("height", 0))
            <= CRITERIA["short_clip_max_edge_px"])


def rung_requests(declared: int, subset: str) -> int:
    """N14 (box run2: 54/52/40 short-clip samples of 120): a rung sends its declared
    requests, or more until bench's own schedule - its shuffled cycle through the subset,
    this runner's seed - holds `p95_min_accepted` short clips, so its TTFT p95 can be judged
    at all. A subset with no short clip keeps the declared count."""
    clips = bench.load_corpus(str(MARLIN / "corpus" / "manifest.json"), subset)[0]
    if not any(map(short_clip, clips)):
        return declared
    n = declared
    while sum(short_clip(item["clip"]) for item in bench.build_schedule(
            n, clips, ["video_b64"], seed=SEED)) < CRITERIA["p95_min_accepted"]:
        n += 1
    return n


def overloaded(row: dict) -> bool:
    """Refused for capacity - a 429 with an overload code. Admission checks capacity before
    the media's length (the cheap refusal first), so such an answer says nothing of the cap."""
    return row.get("http_status") == 429 and row.get("error_code") in OVERLOAD_CODES


def cap_verdict(rows: list[dict], counted: list[dict], cap_s: float) -> tuple:
    """The duration cap at admission (P-20): every attempt over the cap got the typed
    refusal - one refused for capacity first is not judged (N15) - and none within it did;
    nothing judged over the cap judges nothing."""
    over = [r for r in rows if r not in counted]
    judged_over = [r for r in over if not overloaded(r)]
    admitted = sorted({r["clip_id"] for r in judged_over if not refused_over_cap(r)})
    refused = sorted({r["clip_id"] for r in counted if refused_over_cap(r)})
    verdict = decide.FAIL if admitted or refused else (decide.PASS if judged_over else UNKNOWN)
    return ("duration_cap", verdict, {
        "over_cap_not_refused": admitted, "within_cap_refused": refused,
        "over_cap_refused_for_capacity": sorted({r["clip_id"] for r in over if overloaded(r)}),
        "cap_s": cap_s}, "BOX")


def bench_summary(path: Path) -> dict | None:
    """A cell's bench summary: the last line bench appended to its --out."""
    lines = path.read_text().splitlines() if path.exists() else []
    return json.loads(lines[-1]) if lines else None


def bench_validity(summary: dict | None, local: bool) -> tuple:
    """E1C BENCH-VALIDITY: a cell whose summary is not VALID supports nothing. A local
    (fake-engine) cell is unprofiled by design: that reason alone leaves it unjudged."""
    validity = (summary or {}).get("validity") or {}
    if validity.get("verdict") == "VALID":
        return ("bench_validity", decide.PASS, "VALID", "BOX")
    stated = validity.get("reasons") or []
    reasons = [r for r in stated if not (local and r == bench.UNPROFILED)]
    if reasons or not local or (validity and not stated):
        return ("bench_validity", decide.FAIL, reasons or (
            f"verdict {validity.get('verdict')!r} with no reason" if validity
            else "no bench summary: the cell's validity is unknown"), "BOX")
    return ("bench_validity", UNKNOWN, "local fake engine: unprofiled, not a measurement", "BOX")


def rung_verdicts(rows: list[dict], clips: dict, *, gateway: bool, cap_s: float,
                  summary: dict | None, local: bool) -> list[tuple]:
    """Protocol §4 envelope criteria for one rate. Attempts on clips over the deployed cap
    are the cap's: each must get the typed over-cap refusal at admission, and they are no
    one's failures; a clip within the cap is never refused as over it. A cell bench did not
    call VALID fails (E1C)."""
    counted = judged(rows, clips, cap_s)
    out = [cap_verdict(rows, counted, cap_s) if gateway else
           ("duration_cap", UNKNOWN, "an engine target has no admission", "BOX")]
    out += [bench_validity(summary, local), failures(counted), answered(counted)]
    refusals = [r for r in counted if r.get("outcome") == "rejected"]
    out.append(("rejections", decide.FAIL if refusals else decide.PASS,
                f"{len(refusals)} refused within the cap", "BOX"))
    accepted = [r for r in counted if r.get("outcome") == "accepted"]
    short = [r["ttft_s"] for r in accepted if r.get("ttft_s") is not None
             and short_clip(clips.get(r.get("clip_id"), {}))]
    per_minute = [r["latency_s"] / (_duration(r, clips) / 60) for r in accepted
                  if r.get("latency_s") is not None and _duration(r, clips) > 0]
    latency = [r["latency_s"] for r in accepted if r.get("latency_s") is not None]
    for name, values, limit in (("ttft_p95_short", short, CRITERIA["ttft_p95_short_s"]),
                                ("latency_p95", latency, CRITERIA["latency_p95_s"]),
                                ("e2e_p95_per_clip_minute", per_minute,
                                 CRITERIA["e2e_p95_s_per_clip_minute"])):
        tail = decide.p95(values)
        out.append((name, UNKNOWN if tail is None else
                    decide.PASS if tail <= limit else decide.FAIL,
                    f"p95 {tail}, p50 {round(statistics.median(values), 3) if values else None} "
                    f"over {len(values)} accepted samples (needs {decide.P95_MIN_ACCEPTED})",
                    "BOX"))
    return out


def envelope_summary(rungs: list[tuple[float, list[tuple]]],
                     declared: float | None = None) -> tuple[str, tuple, float | None]:
    """The supported rate is the highest rung, climbing from the lowest, whose failures and
    refusals pass; the check is that rung's verdicts plus every rung's duration cap. With a
    declared rate (the box, P-18) the climb stops there: the declared rung is supported and
    judged when it and every rung below pass, and a rung above it is measured, never
    supported; when it fails, nothing is supported."""
    supported, chosen = None, None
    for rate, verdicts in sorted(rungs):
        if declared is not None and rate > declared:
            break
        core = [v for name, v, _, _ in verdicts
                if name in ("failure_rate", "answered", "rejections", "client_exit")]
        if any(v != decide.PASS for v in core):
            break
        supported, chosen = rate, verdicts
    if chosen is None or (declared is not None and supported != declared):
        return FAIL, (), None
    caps = [row for _, verdicts in rungs for row in verdicts
            if row[0] in ("duration_cap", "bench_validity")]
    status, owners = summarise([row for row in chosen
                                if row[0] not in ("duration_cap", "bench_validity")] + caps)
    return status, owners, supported


def soak_verdicts(rows: list[dict], samples: list[dict], clips: dict,
                  cap_s: float, gateway: bool = False) -> list[tuple]:
    """Protocol §4 soak criteria: failures, memory growth from /metrics, the reconciler's
    drift at the end, and the latency of the last third against the first - over the clips
    within the cap. The envelope judges the cap; on a gateway the soak reports its breach
    (an over-cap clip admitted, or a within-cap clip refused as over it), never a pass."""
    counted = judged(rows, clips, cap_s)
    out = [failures(counted), answered(counted)]
    cap = cap_verdict(rows, counted, cap_s)
    if gateway and cap[1] == decide.FAIL:
        out.append(cap)
    for name, key, limit in (("host_growth_mib", "rss_mib", CRITERIA["max_host_growth_mib"]),
                             ("gpu_growth_mib", "gpu_used_mib", CRITERIA["max_gpu_growth_mib"])):
        grew = decide.growth([sample.get(key) for sample in samples])
        out.append((name, UNKNOWN if grew is None else
                    decide.PASS if grew <= limit else decide.FAIL,
                    f"+{grew} MiB over {len(samples)} samples", "BOX"))
    last = samples[-1] if samples else {}
    ends = [last.get("drift"), last.get("unsettleable")]
    out.append(("reconciled_at_end", UNKNOWN if None in ends else
                decide.PASS if ends == [0, 0] else decide.FAIL,
                f"drift {ends[0]}, unsettleable {ends[1]}", "BOX"))
    ordered = [r["latency_s"] for r in sorted(counted, key=lambda r: r.get("send_s") or 0)
               if r.get("outcome") == "accepted" and r.get("latency_s") is not None]
    third = len(ordered) // 3
    if third < 6:
        out.append(("latency_drift", UNKNOWN, f"{len(ordered)} accepted: a p50 per third "
                                              f"needs 6", "BOX"))
    else:
        early, late = statistics.median(ordered[:third]), statistics.median(ordered[-third:])
        out.append(("latency_drift", decide.PASS if late <= CRITERIA["soak_latency_drift"] * early
                    else decide.FAIL, f"p50 {early} -> {late}", "BOX"))
    return out


def overload_problems(rows: list[dict], clips: dict, cap_s: float) -> list[str]:
    """Protocol §4 overload: admission refuses honestly - 429, a Retry-After, an overload
    code - and nothing is a 5xx or a broken stream. Clips over the deployed cap are the
    duration cap's answer, not overload's."""
    rows = judged(rows, clips, cap_s)
    accepted = [r for r in rows if r.get("outcome") == "accepted"]
    refused = [r for r in rows if r.get("outcome") == "rejected"]
    problems = []
    if not accepted:
        problems.append("nothing was accepted under the burst")
    if not refused:
        problems.append("nothing was refused: admission never reached its limit")
    wrong = [(r.get("http_status"), r.get("retry_after"), r.get("error_code")) for r in refused
             if r.get("http_status") != 429 or not (r.get("retry_after") or 0) > 0
             or r.get("error_code") not in OVERLOAD_CODES]
    if wrong:
        problems.append(f"refusals without 429 + Retry-After + an overload code: {wrong[:5]}")
    broken = [(r.get("http_status"), r.get("error_class")) for r in rows
              if (r.get("http_status") or 0) >= 500 or r.get("outcome") == "failed"]
    if broken:
        problems.append(f"5xx or platform-caused failures under overload: {broken[:5]}")
    return problems


def client_exit(code: int) -> tuple:
    """A client run that did not finish cleanly is a failed cell, never an unjudged one."""
    return ("client_exit", decide.PASS if code == 0 else decide.FAIL, f"exit {code}", "BOX")


def sampled_run(argv: list[str], env: dict, metrics_url: str | None,
                every_s: float) -> tuple[dict, list[dict]]:
    """A client run with the target's /metrics read every `every_s` seconds alongside."""
    samples, stop = [], threading.Event()

    def sample():
        while True:
            answer = scrape(metrics_url)
            if answer is not None:
                samples.append(answer)
            if stop.wait(every_s):
                return
    worker = threading.Thread(target=sample, daemon=True) if metrics_url else None
    if worker:
        worker.start()
    try:
        done = client(argv, env)
    finally:
        stop.set()
        if worker:
            worker.join(timeout=every_s + 10)
    if metrics_url and (answer := scrape(metrics_url)) is not None:
        samples.append(answer)                  # the end state, after the last request
    return done, samples


def load_cells(report: Report, target: dict, workdir: Path, metrics_url: str | None,
               cap_s: float) -> None:
    """E4B.b's envelope, soak and overload cells (protocol §4, shapes §5)."""
    shape, clips, env = MATRIX[target["scale"]], parity.clips(), bench_env(target)
    gateway, local = target["bench_target"] == "gateway", target["kind"] == "local"
    version = f"e4b-{(report.head.get('sha') or 'nosha')[:7]}-" \
              f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    rungs, runs = [], {}
    # the box rungs carry the p95s; the tiny scale's latency rows are unknown by design (§5)
    requests = rung_requests(shape["envelope"]["requests"], "full") if target["scale"] == "box" \
        else shape["envelope"]["requests"]
    for rate in shape["envelope"]["rates"]:
        name = f"envelope-r{rate}"
        runs[name] = client(bench_argv(target, workdir, name, rate=rate, requests=requests,
                                       dataset_version=f"{version}-{name}"), env)["exit"]
        rungs.append((rate, rung_verdicts(raw_rows(workdir / f"{name}-raw.jsonl"), clips,
                                          gateway=gateway, cap_s=cap_s, local=local,
                                          summary=bench_summary(workdir / f"{name}.jsonl"))
                      + [client_exit(runs[name])]))
    declared = CRITERIA["declared_rate_per_s"] if target["scale"] == "box" else None
    status, owners, supported = envelope_summary(rungs, declared)
    report.check("e4b.b.envelope", status,
                 {"supported_rate_per_s": supported,
                  "measured_passing_rate_per_s": envelope_summary(rungs)[2],
                  "client_exits": runs,
                  "rungs": {str(rate): verdicts for rate, verdicts in rungs}},
                 owners=owners, label=target["label"])
    soak = shape["soak"]
    # the box soaks at P-18's fixed rate, and only once the declared rung is supported
    rate = None if declared is not None and supported is None else soak["rate"]
    if rate is None:
        report.check("e4b.b.soak", FAIL, "no supported envelope rate to soak at",
                     label=target["label"])
    else:
        done, samples = sampled_run(
            bench_argv(target, workdir, "soak", rate=rate,
                       requests=max(1, round(rate * soak["seconds"])),
                       dataset_version=f"{version}-soak"), env, metrics_url, soak["sample_s"])
        verdicts = soak_verdicts(raw_rows(workdir / "soak-raw.jsonl"), samples, clips,
                                 cap_s, gateway) + [client_exit(done["exit"]), bench_validity(
                                     bench_summary(workdir / "soak.jsonl"), local)]
        status, owners = summarise(verdicts)
        report.check("e4b.b.soak", status, {"rate_per_s": rate, "seconds": soak["seconds"],
                                            "client_exit": done["exit"], "verdicts": verdicts,
                                            "samples": len(samples)},
                     owners=owners, label=target["label"])
    if not gateway:
        report.check("e4b.b.overload", PENDING, "overload is admission's answer, and the local "
                     "target is the engine: it has no admission to refuse with",
                     owners=("BOX",), label=target["label"])
        return
    burst, edge = shape["overload"]["burst"], target.get("overload_profile")
    if not local:
        # S3 F5 / E1B-protocol rule 11: the burst is P4 (cell_profile stamps it) and enters
        # through the public edge under its own profile; no remote target (the box or any
        # other, E4P-V8) runs it as a P1 cell
        why = profile_blocked({**target, "run_profile": edge}) if edge else (
            "BLOCKED: a remote overload burst is P4 and must enter through the public edge "
            "(S3 F5, E1B-protocol rule 11): pass --overload-profile <a public-edge profile>")
        host = None if why else edge_host(edge)
        if not host:
            report.check("e4b.b.overload", PENDING, why or (
                "BLOCKED: --overload-profile names no public-edge host: it needs a target "
                "block whose target.allowlist is a non-empty list of hosts (E4P-V7)"),
                owners=("PROFILE",), label=target["label"])
            return
        target = {**target, "run_profile": edge, "base_url": f"https://{host}/v1"}
    done = client(bench_argv(target, workdir, "overload", rate=1000.0, requests=burst,
                             dataset_version=f"{version}-overload",
                             extra=("--burst", str(burst))), env)
    rows = raw_rows(workdir / "overload-raw.jsonl")
    problems = overload_problems(rows, clips, cap_s)
    if client_exit(done["exit"])[1] == decide.FAIL:
        problems.append(f"the bench client exited {done['exit']}")
    validity = bench_validity(bench_summary(workdir / "overload.jsonl"), local)
    if validity[1] == decide.FAIL:
        problems.append(f"the bench cell is not VALID: {validity[2]}")
    report.check("e4b.b.overload", FAIL if problems else PASS, problems or "honest refusals",
                 measured={"attempts": len(rows),
                           "accepted": sum(r.get("outcome") == "accepted" for r in rows),
                           "refused": sum(r.get("outcome") == "rejected" for r in rows)},
                 label=target["label"])


# ------------------------------------------------------------------------------ the run

def local_target(scale: str, engine_port: int) -> dict:
    url = f"http://127.0.0.1:{engine_port}"
    return {"kind": "local", "base_url": f"{url}/v1", "engine_url": url, "metered": False,
            "bench_target": "direct", "model": "marlin2b", "scale": scale, "label": FAKE,
            "namespace": harness.NAMESPACE}


def remote_target(args) -> dict:
    return {"kind": "remote", "base_url": args.target.rstrip("/"), "engine_url": args.engine_url,
            "metered": True, "bench_target": "gateway",
            "model": published_release()["requested_model"], "scale": args.scale or "box",
            "label": UNVERIFIED, "namespace": None,
            "run_profile": getattr(args, "run_profile", None),
            "key_inventory": getattr(args, "key_inventory", None),
            "overload_profile": getattr(args, "overload_profile", None)}


def target_label(target: dict, box: bool, preconditions: str) -> str:
    """Review F5: a number is a measurement only from the box with every precondition met;
    the local target is the fake engine, and any other target is unverified."""
    if target["kind"] == "local":
        return FAKE
    return MEAS if box and preconditions == PASS else UNVERIFIED


def stack_checks(report: Report, keep: bool) -> None:
    """The phase-2 gate's stages, by run.py's own functions, then the two halves."""
    have = run.preflight(report, want_services=True)
    try:
        if have and run.services(report, pull=False):
            fixtures = run.migrate(report, SEED)
            if fixtures is not None:
                run.rls(report, fixtures)
                run.backend(report)
    finally:
        gate = [entry for entry in report.stages if entry["stage"] in GATE_STAGES]
        backend = next((e for e in report.stages if e["stage"] == "backend"), None)
        cases = (backend or {}).get("cases") or {"passed": [], "failed": ["<no backend run>"],
                                                  "pending": {}, "skipped": []}
        halves, code = split_backend(cases), attributed_exit(backend, cases)
        suite_check(report, "e4b.a.protocol", halves["protocol"], gate, code)
        suite_check(report, "e4b.b.recovery", halves["recovery"], gate, code)
        if have and not keep:
            run.teardown(report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", help="the gateway's /v1 URL (default: the local fake engine)")
    parser.add_argument("--engine-url", help="the engine, for parity (box: http://127.0.0.1:8000)")
    parser.add_argument("--parity-baseline", type=Path,
                        help="parity.jsonl to pair with (box: W4's E0 run)")
    parser.add_argument("--metrics-url", help="the gateway's /metrics, read during the soak "
                                               "(box: http://127.0.0.1:8001/metrics)")
    parser.add_argument("--worker-metrics-url", help="the worker's /metrics, where its build "
                                                      "is read (box: http://127.0.0.1:8002/metrics)")
    parser.add_argument("--inventory", type=Path,
                        help="measure/inventory.sh's output from the box (the deployed engine)")
    parser.add_argument("--run-profile", type=Path,
                        help="remote target: the base infrx.run-profile/1 JSON each bench cell "
                             "is stamped from (models/marlin2b/profiles); without it and "
                             "--key-inventory the bench cells are BLOCKED")
    parser.add_argument("--key-inventory", type=Path,
                        help="remote target: the sanitized active key-id prefixes (the "
                             "coordinator's read-only op), passed to every bench cell")
    parser.add_argument("--overload-profile", type=Path,
                        help="remote target: the public-edge infrx.run-profile/1 JSON the "
                             "overload burst runs under, stamped P4, at https://<its first "
                             "allowlist host>/v1; without it the box overload cell is BLOCKED "
                             "(S3 F5, E1B-protocol rule 11)")
    parser.add_argument("--box", action="store_true",
                        help="the maintenance-window preconditions (E4B_WINDOW_OK=1 etc.)")
    parser.add_argument("--release-sha", help="the release under test, the FULL 40-character "
                                               "commit id (required with --box): the checkout's "
                                               "own SHA must be it; a prefix is not the release")
    parser.add_argument("--scale", choices=sorted(MATRIX), help="tiny (local) or box")
    parser.add_argument("--no-stack", action="store_true",
                        help="skip the E2-stack suite (a box run; the dev host runs it)")
    parser.add_argument("--keep", action="store_true", help="leave the E2 stack up")
    parser.add_argument("--workdir", type=Path, help="where client/parity outputs go")
    parser.add_argument("--report", type=Path, help="write the JSON report here")
    parser.add_argument("--hashes", action="store_true", help="print the release hashes and exit")
    args = parser.parse_args(argv)
    if args.hashes:
        print(json.dumps(release_hashes(), indent=2, ensure_ascii=False))
        return 0
    if args.target and not args.engine_url:
        parser.error("--target needs --engine-url (parity runs against the engine itself)")
    if args.box and not args.target:
        parser.error("--box certifies a deployed endpoint: give --target and --engine-url")
    if args.box and not args.metrics_url:
        parser.error("--box needs --metrics-url: the gateway's build is read from it (F3)")
    if args.box and not args.worker_metrics_url:
        parser.error("--box needs --worker-metrics-url: the worker's build is read from it")
    if args.box and not args.release_sha:
        parser.error("--box needs --release-sha: the release the box serves, compared with "
                     "this checkout's own SHA (review F2)")
    workdir = args.workdir or Path(tempfile.mkdtemp(prefix=f"{harness.PROJECT}-e4b-"))
    workdir.mkdir(parents=True, exist_ok=True)
    target = remote_target(args) if args.target else \
        local_target(args.scale or "tiny", harness.PORTS["fake_vllm"])
    report = Report({**target, "workdir": str(workdir), "box": args.box,
                     "release_sha": args.release_sha,
                     "inventory": args.inventory and rel(args.inventory),
                     "parity_baseline": args.parity_baseline and rel(args.parity_baseline)},
                    release_sha=args.release_sha)
    with run.signals_handled():
        try:
            report.hashes = release_hashes()
            cap_s = report.target["max_video_seconds"] = deployed_cap_s()
            ready = preconditions_check(report, target, args.box)["status"]
            config_pin_check(report, args.inventory, cap_s)
            if args.box:
                # review V4: a box whose served build is not the release measures nothing
                served = served_build_check(report, args.metrics_url,
                                            args.worker_metrics_url)["status"]
                ready = ready if served == PASS else FAIL
            report.target["label"] = target["label"] = target_label(target, args.box, ready)
            if args.box:
                report.check("e4b.b.recovery-box", PENDING,
                             "I3B's runbook drills on the box (infra/runbooks: restart, "
                             "restore, rollback, index-loss, disk, reconcile) are the "
                             "coordinator's, recorded in E4B-release-decision.md",
                             owners=("BOX",))
            if args.no_stack:
                for check_id in ("e4b.a.protocol", "e4b.b.recovery"):
                    report.check(check_id, SKIP, "--no-stack", owners=("STACK",))
            else:
                stack_checks(report, args.keep)
            engine_checks(report, target, workdir, args, cap_s)
        except run.Interrupted as stop:
            report.add("interrupted", FAIL, f"signal {stop.signum}")
        except Exception as crashed:                  # noqa: BLE001 - recorded, and the
            # report is still written: a run that dies before its JSON is no evidence (E3B)
            report.add("runner-error", FAIL, f"{type(crashed).__name__}: {crashed}")
    payload = report.as_json()
    if args.report:
        args.report.write_text(payload)
    print(payload)
    print(f"\nexit {report.exit_code}")
    return report.exit_code


def engine_checks(report: Report, target: dict, workdir: Path, args, cap_s: float) -> None:
    """The checks that drive an engine or an endpoint: the runner's own fake vLLM for a
    local run (started here, stopped whatever happens), the given URLs otherwise."""
    server = None
    if target["kind"] == "local":
        import fake_vllm
        server = fake_vllm.FakeVllmServer(harness.PORTS["fake_vllm"])
        run.LIVE_SERVERS.append(server)
        server.start()
    try:
        parity_check(report, engine_url=target["engine_url"], workdir=workdir,
                     baseline=args.parity_baseline, label=target["label"],
                     local=target["kind"] == "local", cap_s=cap_s,
                     gateway=target if target["bench_target"] == "gateway" else None)
        if why := profile_blocked(target):
            for check_id in ("e4b.a.dataset-resume", "e4b.b.envelope", "e4b.b.soak",
                             "e4b.b.overload"):
                report.check(check_id, PENDING, why, owners=("PROFILE",), label=target["label"])
            return
        dataset_check(report, target, workdir, cap_s)
        load_cells(report, target, workdir, args.metrics_url, cap_s)
    finally:
        if server is not None:
            server.stop()
            if server in run.LIVE_SERVERS:
                run.LIVE_SERVERS.remove(server)


if __name__ == "__main__":
    raise SystemExit(main())
