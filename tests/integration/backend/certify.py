#!/usr/bin/env python3
"""E4B: certify the Marlin endpoint release candidate - one command, one report.

    apps/infrx-api/.venv/bin/python tests/integration/backend/certify.py --report <path>
    # the box, inside the coordinator's maintenance window (E4B box protocol):
    E4B_WINDOW_OK=1 INFRX_API_KEY=... python tests/integration/backend/certify.py --no-stack \\
        --box --target http://127.0.0.1:8001/v1 --engine-url http://127.0.0.1:8000 \\
        --metrics-url http://127.0.0.1:8001/metrics --inventory <inventory.sh output> \\
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
  e4b.b.config-pin      the tree against W3/W4/M4's declared settings (`DECLARED`), the published
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
import hashlib
import json
import os
import signal
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
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
                   "host): the stack suite is bound to the SHA, not to the target"}

# models/marlin2b/results/E4B-protocol.md §5, the one place these numbers live in code.
CRITERIA = {
    "max_failure_rate": decide.MAX_FAILURE_RATE,
    "p95_min_accepted": decide.P95_MIN_ACCEPTED,
    "ttft_p95_short_s": 6.0,
    "short_clip_max_s": 30.0,
    "short_clip_max_edge_px": 1280,
    "e2e_p95_s_per_clip_minute": 45.0,
    "max_host_growth_mib": decide.MAX_HOST_GROWTH_MIB,
    "max_gpu_growth_mib": decide.MAX_GPU_GROWTH_MIB,
    "soak_latency_drift": 1.5,
    "applied_cap_s": 72,
}
# Refusals that are overload and carry retry guidance: `errors.RETRY_AFTER_CODES` minus the
# one about a dependency (protocol §5).
OVERLOAD_CODES = ("capacity_exhausted", "journal_capacity_exhausted", "rate_limited")
MATRIX = {
    "tiny": {"envelope": {"rates": (4.0,), "requests": 12},
             "soak": {"rate": 2.0, "seconds": 10, "sample_s": 1},
             "overload": {"burst": 32},
             "dataset": {"items": 12, "interrupt_after": 4, "rate": 4.0}},
    "box": {"envelope": {"rates": (0.5, 1.0, 2.0), "requests": 120},
            "soak": {"rate_fraction": 0.5, "seconds": 14400, "sample_s": 30},
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
            self.head_end = run.git_head()
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


def engine_ceiling_s(record: dict) -> int:
    """W4's P-20 arithmetic on the pinned flags: the encoder cache holds max(16384,
    --max-num-batched-tokens) tokens, and `decide.ceiling_s` is the longest clip that fits."""
    flags = served_flags(record)
    batched = [int(flags[i + 1]) for i, flag in enumerate(flags[:-1])
               if flag == "--max-num-batched-tokens"]
    return decide.ceiling_s(max([16384, *batched]))


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


def suite_check(report: Report, check_id: str, cases: dict, gate: list[dict]) -> None:
    """The gate's own verdict (`run.backend_verdict`) on one half of the backend suite, after
    the stages it needs: a stack that did not come up is a failed certification run."""
    down = [f"{entry['stage']}={entry['status']}" for entry in gate if entry["status"] != PASS]
    counts = {"passed": len(cases["passed"]), "failed": cases["failed"] or None,
              "not_run": cases["skipped"] or None,
              "pending_by_id": {task: len(names) for task, names in cases["pending"].items()}}
    if down:
        report.check(check_id, FAIL, {"stages_not_passed": down, **counts})
        return
    status = run.backend_verdict(cases, 0)
    report.check(check_id, status, counts, owners=cases["pending"] if status == PENDING else ())


def parity_check(report: Report, *, engine_url: str, workdir: Path, baseline: Path | None,
                 label: str, local: bool) -> None:
    """MARLIN-SOP: parity.py at c = 1 over W4's parity set, paired on the clips' bytes."""
    cache = corpus_cache()

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
    except (RuntimeError, OSError) as failed:
        report.check("e4b.a.sop-parity", FAIL, str(failed), label=label)
        return
    verdict, why = decide.parity_verdict(decide.jsonl(candidate), decide.jsonl(base))
    status = {decide.PASS: PASS, decide.FAIL: FAIL}.get(verdict, PENDING)
    report.check("e4b.a.sop-parity", status,
                 {"verdict": verdict, "why": why, "baseline": rel(base),
                  "candidate": rel(candidate), "candidate_sha256": sha256_file(candidate)},
                 owners=("BOX",) if status == PENDING else (), label=label)


def corpus_cache() -> Path:
    """The corpus cache the bench client reads ($CORPUS_CACHE, else the main checkout's)."""
    manifest = MARLIN / "corpus" / "manifest.json"
    return Path(bench.corpus_cache_root(json.loads(manifest.read_text()), str(manifest)))


# --- dataset resume: the client half ---------------------------------------------------

def resume_problems(first: list[dict], second: list[dict], *, items: int,
                    first_interrupted: bool) -> list[str]:
    """The client's side of MARLIN-SOP's resume (E1B L6): an interruption that happened,
    one key per item, nothing terminal re-sent, every item terminal at the end."""
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
    return problems


# --- dataset resume: the server half ---------------------------------------------------

def reconcile_problems(rows: list[dict], usage, holds, before, after) -> list[str]:
    """MARLIN-SOP's no-duplicate property on the tenant's own ledger (G6B `TenantSession`):
    one Inference-Id per accepted item across both runs, one CREDIT usage record and one
    hold (released) per accepted job, Σ charged = the ledger's fall, reserved restored."""
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
    if Decimal(str(after.reserved_total)) != Decimal(str(before.reserved_total)):
        problems.append(f"reserved {before.reserved_total} -> {after.reserved_total}")
    return problems


def tenant_ledger():
    """The provisioned client's own view (G6B `Operations.tenant(secret)`), or None while no
    PostgreSQL adapter is wired (`build_operations` refuses until D5)."""
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

def bench_argv(target: dict, workdir: Path, name: str, *, rate: float, requests: int,
               dataset_version: str, extra: tuple[str, ...] = ()) -> list[str]:
    """E1B's client, as the protocol shapes it: licensed corpus, frozen seed, output mix,
    no retries (a retry may not hide a refusal), inline media. Outputs in `workdir` only."""
    argv = [sys.executable, str(MARLIN / "bench.py"),
            "--corpus", str(MARLIN / "corpus" / "manifest.json"),
            "--subset", "full" if target["scale"] == "box" else "fast",
            "--base-url", target["base_url"], "--target", target["bench_target"],
            "--rate", str(rate), "--requests", str(requests), "--seed", str(SEED),
            "--dataset-version", dataset_version, "--forms", "video_b64",
            "--max-tokens", "128,512,1024", "--retries", "0",
            "--out", str(workdir / f"{name}.jsonl"), "--raw", str(workdir / f"{name}-raw.jsonl")]
    if target["bench_target"] == "gateway":
        argv += ["--model", target["model"]]
    return argv + list(extra)


def client(argv: list[str], env: dict | None = None) -> dict:
    """One client process (bench.py, parity.py) from the repository root, run.py's way."""
    return run.shell(argv, cwd=harness.REPO_ROOT, env=env, timeout=3600.0)


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


SETTLE_WAIT_S = 300.0      # a debit may land after the answer: the ledger is re-read until then


def dataset_check(report: Report, target: dict, workdir: Path, ledger=None,
                  settle_wait_s: float = SETTLE_WAIT_S) -> None:
    """E4B.a's large-dataset recipe: interruption, resume, and (on a metered target) the
    tenant's ledger reconciled item by item."""
    shape = MATRIX[target["scale"]]["dataset"]
    env = bench_env(target)
    version = f"e4b-{(report.head.get('sha') or 'nosha')[:7]}-" \
              f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    ledger = ledger if ledger is not None else (tenant_ledger() if target["metered"] else None)
    before = ledger()[0] if ledger else None
    argv = bench_argv(target, workdir, "dataset", rate=shape["rate"],
                      requests=shape["items"], dataset_version=version)
    first = interrupted_run(argv, workdir / "dataset-raw.jsonl",
                            after=shape["interrupt_after"], env=env, timeout_s=1800.0)
    resumed = client(bench_argv(target, workdir, "dataset-resume", rate=shape["rate"],
                                requests=shape["items"], dataset_version=version,
                                extra=("--resume", str(workdir / "dataset-raw.jsonl"))), env)
    rows_first = raw_rows(workdir / "dataset-raw.jsonl")
    rows_second = raw_rows(workdir / "dataset-resume-raw.jsonl")
    problems = resume_problems(rows_first, rows_second, items=shape["items"],
                               first_interrupted=first["signalled"] and first["exit"] == 130)
    if resumed["exit"] != 0:
        problems.append(f"the resumed run exited {resumed['exit']}: {resumed['tail'][-200:]}")
    measured = {"dataset_version": version, "items": shape["items"],
                "first_run": {**first, "attempts": len(rows_first),
                              "accepted": sum(r.get("outcome") == "accepted" for r in rows_first)},
                "resume": {"exit": resumed["exit"], "attempts": len(rows_second),
                           "replayed": sum(bool(r.get("idempotency_replayed"))
                                           for r in rows_second)},
                "client_problems": problems or None}
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
                     "(no PostgreSQL AccountView/Ledger adapter), so the tenant's ledger cannot "
                     "be read", owners=("D5",), measured=measured, label=target["label"])
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
    report.check("e4b.a.dataset-resume", FAIL if problems else PASS, problems or "reconciled",
                 measured=measured, label=target["label"])


# ------------------------------------------------------------------------------ E4B.b

# The settings the earlier evidence was measured under. A tree (or a box) that differs has
# moved past that evidence: the check fails, naming it, until it is re-measured and
# re-declared here ("reject any optimization that invalidates earlier evidence").
PINNED_DIGEST = "sha256:3c4bbface108e019b55a71121e1f3aaa23268bc1d1bd100257b0e2c68c036147"
PINNED_IMAGE = ("vllm/vllm-openai@sha256:"
                "4cbfd34aac145fd1870381c030131c7f868fcad45448f401ecdb5fd4ed020b42")
DECLARED = {
    "engine_options_digest": (PINNED_DIGEST, "W3 serving-version.json; W4-ecacd50 phase A "
                                             "adopted no candidate, so E0 (the W3 pin) stands"),
    "runtime_image": (PINNED_IMAGE, "W3 serving-version.json runtime_image; W4's sweep image"),
    "engine_max_num_seqs": ("8", "W3 settings; no c* measured (W4-ecacd50 request 3)"),
    "contract_engine_max_num_seqs": (8, "contracts limits, equal to the W3 setting"),
    "encoder_budget_tokens": (16384, "W4 P-20 record: no --max-num-batched-tokens pinned"),
    "profile_version": ("v1", "S2M profile v1; M4-8179144's MEDIA-PARITY oracle"),
    "preparation_concurrency": (2, "M4-8179144 'Measured, not taken': stays 2"),
    "max_preparing_jobs": (8, "contracts limits, untouched by M4"),
    "max_video_seconds": (120.0, "contracts limits (profile v1); P-20 applies 72 as config"),
    "published_engine_options_digest": (PINNED_DIGEST, "R76/R78: the serving revision every "
                                                       "admission pins is the measured one"),
    "published_runtime_image": (PINNED_IMAGE, "R76/R78, as above"),
}


def current_config() -> dict:
    """The same settings, read from the tree (and the release G6B publishes)."""
    from infrx.contracts.limits import DEFAULTS
    record, published = serving_record(), published_release()
    flags = served_flags(record)
    batched = [int(flags[i + 1]) for i, flag in enumerate(flags[:-1])
               if flag == "--max-num-batched-tokens"]
    return {"engine_options_digest": options_digest(flags),
            "runtime_image": record["runtime_image"]["ref"],
            "engine_max_num_seqs": record["settings"]["ENGINE_MAX_NUM_SEQS"],
            "contract_engine_max_num_seqs": DEFAULTS.engine_max_num_seqs,
            "encoder_budget_tokens": max([16384, *batched]),
            "profile_version": record["profile_version"],
            "preparation_concurrency": DEFAULTS.preparation_concurrency,
            "max_preparing_jobs": DEFAULTS.max_preparing_jobs,
            "max_video_seconds": DEFAULTS.max_video_seconds,
            "published_engine_options_digest": published["engine_options_digest"],
            "published_runtime_image": published["runtime_image_ref"]}


def config_problems(current: dict, declared: dict = DECLARED) -> list[str]:
    return [f"{name}: {current.get(name)!r} is not the declared {value!r} ({source}) - "
            f"re-measure and re-declare, or restore it"
            for name, (value, source) in declared.items() if current.get(name) != value]


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


def config_pin_check(report: Report, inventory: Path | None) -> None:
    problems = config_problems(current_config())
    measured = {"current": current_config()}
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


def repo_roots() -> tuple[str, ...]:
    """This checkout and the main checkout its worktrees hang off."""
    main = bench.shared_repo_root(str(harness.REPO_ROOT))
    return tuple(sorted({str(harness.REPO_ROOT), *([main] if main else [])}))


def next_servers(proc: Path = Path("/proc"), roots: tuple[str, ...] | None = None) -> list[dict]:
    """Next.js servers (the App or the Lab) of this repository running on this host."""
    roots = roots if roots is not None else repo_roots()
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            argv = [part.decode(errors="replace")
                    for part in (entry / "cmdline").read_bytes().split(b"\0") if part]
            cwd = os.readlink(entry / "cwd")
        except OSError:
            continue
        head = os.path.basename(argv[0]).split()[0] if argv else ""
        is_next = head == "next-server" or (
            head in ("node", "next") and {"start", "dev"} & set(argv[1:])
            and any("next" in part for part in argv[:3]))
        if is_next and any(cwd == root or cwd.startswith(root + os.sep) for root in roots):
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
    return {"rss_mib": mib(total("infrx_process_resident_bytes")),
            "gpu_used_mib": mib(total("infrx_gpu_memory_bytes", state="used")),
            "drift": total("infrx_reconciliation_drift"),
            "unsettleable": total("infrx_unsettleable_jobs"),
            "running": total("vllm:num_requests_running"),
            "waiting": total("vllm:num_requests_waiting"),
            "revision": next((dict(labels).get("revision") for (series, labels), value
                              in samples.items() if series == "infrx_build_info" and value == 1),
                             None)}


def served_build_problems(scraped: dict | None, head_sha: str | None,
                          gateway_image: str | None, release_image: str | None) -> list[str]:
    """Review F3: the report's hashes are the release the box serves. The gateway names its
    revision (`infrx_build_info`), which must be the report's tree, and it runs the image
    install.sh built and tagged for that release (`infrx-runtime:<release>`)."""
    problems = []
    revision = (scraped or {}).get("revision")
    if scraped is None:
        problems.append("the gateway's /metrics is unreadable: its build is unknown")
    elif revision is None:
        problems.append("the gateway publishes no infrx_build_info{revision} (I3B request: "
                        "set it at startup), so the build it serves is unknown")
    elif not (head_sha and len(str(revision)) >= 7 and head_sha.startswith(str(revision))):
        problems.append(f"the gateway serves {revision}, the report's tree is {head_sha}")
    if not gateway_image:
        problems.append("INFRX_CERTIFY_GATEWAY_IMAGE is unset: the serving image is unrecorded")
    if not release_image:
        problems.append("INFRX_CERTIFY_RELEASE_IMAGE is unset: the release image is unrecorded")
    elif gateway_image and gateway_image != release_image:
        problems.append(f"the gateway runs {gateway_image}, not the release image {release_image}")
    return problems


def served_build_check(report: Report, metrics_url: str) -> None:
    problems = served_build_problems(scrape(metrics_url), report.head.get("sha"),
                                     os.environ.get("INFRX_CERTIFY_GATEWAY_IMAGE"),
                                     os.environ.get("INFRX_CERTIFY_RELEASE_IMAGE"))
    report.check("e4b.b.served-build", FAIL if problems else PASS,
                 problems or "the gateway serves the report's tree, from the release image")


def preconditions_check(report: Report, target: dict, box: bool) -> None:
    """Protocol §2: App/Lab stopped everywhere; on the box also the window consent, an idle
    engine and every parity clip in the cache."""
    servers = next_servers()
    problems = [f"App/Lab running: pid {s['pid']} in {s['cwd']} ({s['command']})"
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
    report.check("e4b.b.preconditions", FAIL if problems else PASS,
                 problems or "App and Lab stopped" + (", window open, engine idle, clips present"
                                                      if box else ""),
                 measured={"next_servers": servers, "roots": list(repo_roots())})


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


def judged(rows: list[dict], clips: dict) -> list[dict]:
    """The attempts a cell judges: clips the engine can hold at all (the rest are the
    duration cap's, P-20)."""
    ceiling = engine_ceiling_s(serving_record())
    return [row for row in rows if _duration(row, clips) <= ceiling]


def rung_verdicts(rows: list[dict], clips: dict, *, gateway: bool) -> list[tuple]:
    """Protocol §4 envelope criteria for one rate. Attempts on clips beyond the engine's
    ceiling are the duration cap's: they must be refused at admission and are no one's
    failures; a clip between the applied cap and the ceiling may be either."""
    ceiling, cap = engine_ceiling_s(serving_record()), CRITERIA["applied_cap_s"]
    counted = judged(rows, clips)
    over = [r for r in rows if r not in counted]
    out = []
    if not gateway:
        out.append(("duration_cap", UNKNOWN, "an engine target has no admission", "BOX"))
    else:
        admitted = sorted({r["clip_id"] for r in over if not (
            r.get("outcome") == "rejected" and 400 <= (r.get("http_status") or 0) < 500)})
        refused = sorted({r["clip_id"] for r in counted if _duration(r, clips) <= cap
                          and r.get("outcome") == "rejected"
                          and r.get("http_status") in (400, 413, 422)})
        verdict = decide.FAIL if admitted or refused else (decide.PASS if over else UNKNOWN)
        out.append(("duration_cap", verdict, {"over_ceiling_not_refused": admitted,
                                              "within_cap_refused": refused,
                                              "ceiling_s": ceiling, "cap_s": cap}, "BOX"))
    platform = [r for r in counted if bench.is_platform_failure(r)]
    rate = len(platform) / len(counted) if counted else None
    out.append(("failure_rate", UNKNOWN if rate is None else
                decide.PASS if rate < CRITERIA["max_failure_rate"] else decide.FAIL,
                f"{len(platform)}/{len(counted)}", "BOX"))
    refusals = [r for r in counted if r.get("outcome") == "rejected"
                and _duration(r, clips) <= cap]
    out.append(("rejections", decide.FAIL if refusals else decide.PASS,
                f"{len(refusals)} refused within the cap", "BOX"))
    accepted = [r for r in counted if r.get("outcome") == "accepted"]
    short = [r["ttft_s"] for r in accepted if r.get("ttft_s") is not None
             and _duration(r, clips) <= CRITERIA["short_clip_max_s"]
             and max(clips.get(r.get("clip_id"), {}).get("width", 0),
                     clips.get(r.get("clip_id"), {}).get("height", 0))
             <= CRITERIA["short_clip_max_edge_px"]]
    per_minute = [r["latency_s"] / (_duration(r, clips) / 60) for r in accepted
                  if r.get("latency_s") is not None and _duration(r, clips) > 0]
    for name, values, limit in (("ttft_p95_short", short, CRITERIA["ttft_p95_short_s"]),
                                ("e2e_p95_per_clip_minute", per_minute,
                                 CRITERIA["e2e_p95_s_per_clip_minute"])):
        tail = decide.p95(values)
        out.append((name, UNKNOWN if tail is None else
                    decide.PASS if tail <= limit else decide.FAIL,
                    f"p95 {tail} over {len(values)} samples (needs {decide.P95_MIN_ACCEPTED})",
                    "BOX"))
    return out


def envelope_summary(rungs: list[tuple[float, list[tuple]]]) -> tuple[str, tuple, float | None]:
    """The supported rate is the highest rung, climbing from the lowest, whose failures and
    refusals pass; the check is that rung's verdicts plus every rung's duration cap."""
    supported, chosen = None, None
    for rate, verdicts in sorted(rungs):
        core = [v for name, v, _, _ in verdicts
                if name in ("failure_rate", "rejections", "client_exit")]
        if any(v != decide.PASS for v in core):
            break
        supported, chosen = rate, verdicts
    if chosen is None:
        return FAIL, (), None
    caps = [row for _, verdicts in rungs for row in verdicts if row[0] == "duration_cap"]
    status, owners = summarise([row for row in chosen if row[0] != "duration_cap"] + caps)
    return status, owners, supported


def soak_verdicts(rows: list[dict], samples: list[dict], clips: dict) -> list[tuple]:
    """Protocol §4 soak criteria: failures, memory growth from /metrics, the reconciler's
    drift at the end, and the latency of the last third against the first."""
    counted = judged(rows, clips)
    platform = [r for r in counted if bench.is_platform_failure(r)]
    out = [("failure_rate", UNKNOWN if not counted else
            decide.PASS if len(platform) / len(counted) < CRITERIA["max_failure_rate"]
            else decide.FAIL, f"{len(platform)}/{len(counted)}", "BOX")]
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


def overload_problems(rows: list[dict], clips: dict) -> list[str]:
    """Protocol §4 overload: admission refuses honestly - 429, a Retry-After, an overload
    code - and nothing is a 5xx or a broken stream. Clips over the applied cap are the
    duration cap's answer, not overload's."""
    rows = [r for r in rows if _duration(r, clips) <= CRITERIA["applied_cap_s"]]
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
              if (r.get("http_status") or 0) >= 500 or bench.is_platform_failure(r)]
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


def load_cells(report: Report, target: dict, workdir: Path, metrics_url: str | None) -> None:
    """E4B.b's envelope, soak and overload cells (protocol §4, shapes §5)."""
    shape, clips, env = MATRIX[target["scale"]], parity.clips(), bench_env(target)
    gateway = target["bench_target"] == "gateway"
    version = f"e4b-{(report.head.get('sha') or 'nosha')[:7]}-" \
              f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    rungs, runs = [], {}
    for rate in shape["envelope"]["rates"]:
        name = f"envelope-r{rate}"
        runs[name] = client(bench_argv(target, workdir, name, rate=rate,
                                       requests=shape["envelope"]["requests"],
                                       dataset_version=f"{version}-{name}"), env)["exit"]
        rungs.append((rate, rung_verdicts(raw_rows(workdir / f"{name}-raw.jsonl"), clips,
                                          gateway=gateway) + [client_exit(runs[name])]))
    status, owners, supported = envelope_summary(rungs)
    report.check("e4b.b.envelope", status,
                 {"supported_rate_per_s": supported, "client_exits": runs,
                  "rungs": {str(rate): verdicts for rate, verdicts in rungs}},
                 owners=owners, label=target["label"])
    soak = shape["soak"]
    rate = soak.get("rate") or (supported * soak["rate_fraction"] if supported else None)
    if rate is None:
        report.check("e4b.b.soak", FAIL, "no supported envelope rate to soak at",
                     label=target["label"])
    else:
        done, samples = sampled_run(
            bench_argv(target, workdir, "soak", rate=rate,
                       requests=max(1, round(rate * soak["seconds"])),
                       dataset_version=f"{version}-soak"), env, metrics_url, soak["sample_s"])
        verdicts = soak_verdicts(raw_rows(workdir / "soak-raw.jsonl"), samples, clips) + [
            client_exit(done["exit"])]
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
    burst = shape["overload"]["burst"]
    client(bench_argv(target, workdir, "overload", rate=1000.0, requests=burst,
                      dataset_version=f"{version}-overload", extra=("--burst", str(burst))), env)
    rows = raw_rows(workdir / "overload-raw.jsonl")
    problems = overload_problems(rows, clips)
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
            "label": MEAS, "namespace": None}


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
        halves = split_backend(cases)
        suite_check(report, "e4b.a.protocol", halves["protocol"], gate)
        suite_check(report, "e4b.b.recovery", halves["recovery"], gate)
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
    parser.add_argument("--inventory", type=Path,
                        help="measure/inventory.sh's output from the box (the deployed engine)")
    parser.add_argument("--box", action="store_true",
                        help="the maintenance-window preconditions (E4B_WINDOW_OK=1 etc.)")
    parser.add_argument("--release-sha", help="the release under test (required with --box): "
                                               "the checkout's own SHA must be it")
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
            preconditions_check(report, target, args.box)
            config_pin_check(report, args.inventory)
            if args.box:
                served_build_check(report, args.metrics_url)
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
            engine_checks(report, target, workdir, args)
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


def engine_checks(report: Report, target: dict, workdir: Path, args) -> None:
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
                     local=target["kind"] == "local")
        dataset_check(report, target, workdir)
        load_cells(report, target, workdir, args.metrics_url)
    finally:
        if server is not None:
            server.stop()
            if server in run.LIVE_SERVERS:
                run.LIVE_SERVERS.remove(server)


if __name__ == "__main__":
    raise SystemExit(main())
