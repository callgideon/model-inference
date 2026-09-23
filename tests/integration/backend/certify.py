#!/usr/bin/env python3
"""E4B: certify the Marlin endpoint release candidate - one command, one report.

    apps/infrx-api/.venv/bin/python tests/integration/backend/certify.py --report <path>
    # the box, inside the coordinator's maintenance window (E4B box protocol):
    E4B_WINDOW_OK=1 INFRX_API_KEY=... python tests/integration/backend/certify.py --no-stack \\
        --target http://127.0.0.1:8001/v1 --engine-url http://127.0.0.1:8000 \\
        --parity-baseline <W4 E0 parity.jsonl> --report <path>

The protocol - checks, cells, criteria, shapes - is predeclared in
`models/marlin2b/results/E4B-protocol.md`; `CRITERIA` and `MATRIX` below are its numbers
(test_certify holds the two equal). Checks, each a named report entry:

  e4b.a.protocol        the phase-2 gate's stages on the E2 stack (run.py's own functions:
                        preflight, services, migrate, rls, backend), minus `recovery/`
  e4b.a.sop-parity      MARLIN-SOP: W4's parity.py against the engine, `decide.parity_verdict`
  e4b.a.dataset-resume  E1B's bench.py interrupted by SIGINT, then `--resume`; the client's
                        invariants, and the tenant's ledger reconciled through G6B's Operations

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
import subprocess
import sys
import tempfile
import time
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

PASS, FAIL, PENDING, SKIP = run.PASS, run.FAIL, run.PENDING, run.SKIP
FAKE, MEAS = "fake-engine, not a measurement", "meas."
PROTOCOL = MARLIN / "results" / "E4B-protocol.md"
SEED = 20260922

# The only owners a PENDING or SKIP may name: the backend suite's own vocabulary (E3B's
# `stack.PENDING`, I3B's `recoverykit.OWNERS`), plus the two this runner adds.
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

    def __init__(self, target: dict) -> None:
        super().__init__()
        self.target, self.hashes = target, {}

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
        doc = json.loads(super().as_json())
        return json.dumps({"runner": "e4b-certify", "protocol": rel(PROTOCOL),
                           "target": self.target, "hashes": self.hashes,
                           "backend_ready": "not decided by this runner: BACKEND-READY needs "
                                            "the box half and the coordinator's recorded "
                                            "decision (E4B-release-decision.md)",
                           **doc}, indent=2, default=str, ensure_ascii=False)


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
        "gateway_image": os.environ.get("INFRX_CERTIFY_GATEWAY_IMAGE")
        or "⚠️ TO BE MEASURED on the box: `docker image inspect` of the image install.sh built",
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


def dataset_check(report: Report, target: dict, workdir: Path, ledger=None) -> None:
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
                     owners=("G2-R1",), measured=measured, label=target["label"])
        return
    if ledger is None:
        report.check("e4b.a.dataset-resume", PENDING,
                     "client invariants hold; `infrx.operations.cli.build_operations` refuses "
                     "(no PostgreSQL AccountView/Ledger adapter), so the tenant's ledger cannot "
                     "be read", owners=("D5",), measured=measured, label=target["label"])
        return
    balance, usage, holds = ledger()
    problems = reconcile_problems(rows_first + rows_second, usage, holds, before, balance)
    measured["ledger"] = {"before": str(before.ledger_total), "after": str(balance.ledger_total),
                          "reserved_after": str(balance.reserved_total)}
    report.check("e4b.a.dataset-resume", FAIL if problems else PASS, problems or "reconciled",
                 measured=measured, label=target["label"])


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
    workdir = args.workdir or Path(tempfile.mkdtemp(prefix=f"{harness.PROJECT}-e4b-"))
    workdir.mkdir(parents=True, exist_ok=True)
    target = remote_target(args) if args.target else \
        local_target(args.scale or "tiny", harness.PORTS["fake_vllm"])
    report = Report({**target, "workdir": str(workdir)})
    with run.signals_handled():
        try:
            report.hashes = release_hashes()
            if args.no_stack:
                for check_id in ("e4b.a.protocol", "e4b.b.recovery"):
                    report.check(check_id, SKIP, "--no-stack", owners=("STACK",))
            else:
                stack_checks(report, args.keep)
            engine_checks(report, target, workdir, args)
        except run.Interrupted as stop:
            report.add("interrupted", FAIL, f"signal {stop.signum}")
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
    finally:
        if server is not None:
            server.stop()
            if server in run.LIVE_SERVERS:
                run.LIVE_SERVERS.remove(server)


if __name__ == "__main__":
    raise SystemExit(main())
