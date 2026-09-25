#!/usr/bin/env python3
"""E2C: the three local verification gates, composed from the maintained runners.

    tests/integration/consumer-local.sh   [--out DIR] [--break-seam readiness|expiry]
                                          (last stage: E3C's backend/e3c/runner.py, when present)
    tests/integration/backend-certify.sh  [--out DIR] [--certify-profile P] [--validate-only]
                                          [-- <certify.py flags>]
    tests/integration/app-e2e.sh          [--out DIR]

Each writes `<out>/verdict.json` and exits 0 PASS, 1 FAIL, 3 BLOCKED or NOT RUN, 4 INVALID.
The gate is the WORST stage: FAIL > INVALID > BLOCKED > NOT RUN > PASS. A stage that could
not run (missing tool, image, busy namespace) is BLOCKED; a pytest stage that passed with
skips is BLOCKED too, because a required case that did not run cannot certify anything; and
so is one with strict xfails - they are the suite's quarantine (known gaps), and a quarantine
prevents claiming the gate passed. Both are listed with their reasons.
Nothing here provisions, pulls or deletes: the runners it calls own their own resources.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import preflight as pf                                   # noqa: E402

REPO = HERE.parents[1]
API = REPO / "apps" / "infrx-api"
PY = API / ".venv" / "bin" / "python"
PASS, FAIL, BLOCKED, INVALID, NOT_RUN = "PASS", "FAIL", "BLOCKED", "INVALID", "NOT RUN"
RANK = {PASS: 0, NOT_RUN: 1, BLOCKED: 2, INVALID: 3, FAIL: 4}
EXIT = {PASS: 0, FAIL: 1, BLOCKED: 3, NOT_RUN: 3, INVALID: 4}
# A deliberately broken seam: one existing single-edit mutant per control, run through the
# shared R83 runner (pristine baseline first). The gate must come out FAIL.
SEAMS = {
    "readiness": ("tests.g.mutants", "pilot_starts_unreachable"),
    "expiry": ("tests.contracts.mutants", "upload_expiry_ignored"),
}
# E3C's BACKEND-LOCAL runner (E3C WR-3): its own verdict.json, E2C's ranking and exit codes.
E3C_RUNNER = REPO / "tests" / "integration" / "backend" / "e3c" / "runner.py"
# The recorded E4B box invocation (certify.py's docstring; E4B box protocol).
BOX_FLAGS = ("--no-stack --box --target http://127.0.0.1:8001/v1 --engine-url "
             "http://127.0.0.1:8000 --metrics-url http://127.0.0.1:8001/metrics "
             "--worker-metrics-url http://127.0.0.1:8002/metrics --release-sha <full SHA> "
             "--inventory <inventory.sh output> --parity-baseline <W4 E0 parity.jsonl> "
             "--report <path>")


def worst(verdicts) -> str:
    return max(verdicts, key=RANK.__getitem__, default=PASS)


def junit_counts(path: Path) -> dict:
    """Counts from a pytest junit file. pytest writes an xfail as <skipped type=pytest.xfail>;
    it is counted apart from a real skip."""
    counts = collections.Counter()
    skips, xfails, failed = collections.Counter(), collections.Counter(), []
    for case in ET.parse(path).getroot().iter("testcase"):
        counts["tests"] += 1
        node = f"{case.get('classname')}::{case.get('name')}"
        outcome = next((child for child in case
                        if child.tag in ("failure", "error", "skipped")), None)
        if outcome is None:
            counts["passed"] += 1
        elif outcome.tag == "skipped" and outcome.get("type") == "pytest.xfail":
            counts["xfailed"] += 1
            xfails[outcome.get("message", "")[:200]] += 1
        elif outcome.tag == "skipped":
            counts["skipped"] += 1
            skips[outcome.get("message", "")[:200]] += 1
        else:
            counts["failed" if outcome.tag == "failure" else "errors"] += 1
            failed.append(node)
    return {**{k: counts[k] for k in ("tests", "passed", "failed", "errors", "skipped",
                                      "xfailed")},
            "skip_reasons": dict(skips.most_common()), "xfail_reasons": dict(xfails),
            "failed_ids": failed}


def run(name: str, argv: list[str], cwd: Path, out: Path, env: dict | None = None):
    log = out / f"{name}.log"
    started = time.monotonic()
    with log.open("w") as sink:
        done = subprocess.run(argv, cwd=cwd, stdout=sink, stderr=subprocess.STDOUT,
                              env={**os.environ, **(env or {})})
    return done.returncode, round(time.monotonic() - started, 1), log


def pytest_stage(name: str, argv: list[str], cwd: Path, out: Path,
                 env: dict | None = None) -> dict:
    junit = out / f"{name}.xml"
    # --basetemp: a private (0700, mkdtemp) directory instead of the shared
    # /tmp/pytest-of-<user> (GHSA-6w46-j5rx-g56g mitigation while pytest stays < 9.0.3).
    # Short, because some cases bind unix sockets under tmp_path. TMPDIR is left alone: the
    # D/Q harnesses take their host-wide flocks under it.
    basetemp = tempfile.mkdtemp(prefix=f"infrx-e2c-{name}-")
    argv = [*argv, f"--junitxml={junit}", f"--basetemp={basetemp}"]
    try:
        code, seconds, log = run(name, argv, cwd, out, env)
    finally:
        shutil.rmtree(basetemp, ignore_errors=True)
    row = {"stage": name, "command": " ".join(argv), "cwd": str(cwd), "exit": code,
           "duration_s": seconds, "log": str(log), **({"env": env} if env else {})}
    if not junit.exists():
        return {**row, "verdict": FAIL, "detail": f"pytest exit {code} and no junit report"}
    counts = junit_counts(junit)
    if code != 0 or counts["failed"] or counts["errors"]:
        verdict = FAIL
    elif counts["tests"] == 0:
        verdict, row["detail"] = BLOCKED, "no test ran"
    elif counts["skipped"] or counts["xfailed"]:
        verdict, row["detail"] = BLOCKED, (f"{counts['skipped']} required case(s) skipped, "
                                           f"{counts['xfailed']} quarantined (strict xfail)")
    else:
        verdict = PASS
    return {**row, "verdict": verdict, "counts": counts}


def runner_stage(name: str, argv: list[str], out: Path, report: Path | None = None) -> dict:
    """A runner with the 0 PASS / 1 FAIL / 3 PENDING convention (run.py, certify.py)."""
    code, seconds, log = run(name, argv, REPO, out)
    verdict = {0: PASS, 3: BLOCKED}.get(code, FAIL)
    return {"stage": name, "command": " ".join(argv), "exit": code, "duration_s": seconds,
            "verdict": verdict, "log": str(log),
            **({"report": str(report)} if report else {})}


def preflight_stage(profile: str, out: Path, certify_profile: Path | None = None) -> dict:
    result = pf.preflight(profile, certify_profile)
    (out / f"preflight-{profile}.json").write_text(json.dumps(result, indent=2) + "\n")
    return {"stage": f"preflight:{profile}", "verdict": result["verdict"],
            "exit": result["exit"], "not_ok": result["not_ok"],
            "checks": [c for c in result["checks"] if c["status"] != "ok"]}


def seam_stage(seam: str, out: Path) -> dict:
    """Break one control and run its cases. A kill means the suite noticed: the gate FAILS,
    as it must with a broken seam. A survivor would be a gate that passes a broken seam."""
    module, mutant = SEAMS[seam]
    probe = ("import json, importlib; m = importlib.import_module(%r); "
             "r = m.run_mutant(next(x for x in m.MUTANTS if x.name == %r)); "
             "print(json.dumps({'outcome': str(r.outcome), 'detail': r.detail[-400:]}))"
             % (module, mutant))
    code, seconds, log = run(f"seam-{seam}", [str(PY), "-c", probe], API, out)
    lines = log.read_text().strip().splitlines()
    try:
        result = json.loads(lines[-1])
    except (IndexError, ValueError):
        result = {"outcome": "broken_runner", "detail": f"exit {code}: no result"}
    verdict = {"killed": FAIL, "survived": PASS}.get(result["outcome"], INVALID)
    return {"stage": f"seam:{seam}", "mutant": f"{module}:{mutant}", "verdict": verdict,
            "exit": code, "duration_s": seconds, "log": str(log), **result,
            "detail_verdict": {FAIL: "broken seam detected: the gate fails",
                               PASS: "BROKEN SEAM NOT DETECTED: the gate would pass",
                               INVALID: "the runner proved nothing"}[verdict]}


def e3c_stage(out: Path, runner: Path = E3C_RUNNER) -> dict:
    """The corrective scenario matrix on real services. Its verdict.json decides, and it must
    agree with the runner's exit code: a runner that says PASS and exits 1 proved nothing."""
    name = "backend-local"
    if not runner.exists():
        return {"stage": name, "verdict": NOT_RUN,
                "detail": f"{runner} is not on this tree (E3C, codex/e3c-integration)"}
    target = out / "e3c"
    argv = [str(PY), str(runner), "--out", str(target)]
    code, seconds, log = run(name, argv, REPO, out)
    row = {"stage": name, "command": " ".join(argv), "exit": code, "duration_s": seconds,
           "log": str(log), "report": str(target / "verdict.json")}
    try:
        verdict = json.loads((target / "verdict.json").read_text())["verdict"]
    except (OSError, ValueError, KeyError, TypeError):
        return {**row, "verdict": FAIL, "detail": f"exit {code} and no readable verdict.json"}
    if verdict not in EXIT:
        return {**row, "verdict": INVALID, "detail": f"unknown verdict {verdict!r}"}
    if EXIT[verdict] != code:
        return {**row, "verdict": INVALID,
                "detail": f"verdict {verdict} but exit {code}: the runner contradicts itself"}
    return {**row, "verdict": verdict}


def uv() -> list[str]:
    return ["uv", "run", "--frozen", "pytest", "-q", "-p", "no:cacheprovider"]


def consumer_local(args, out: Path) -> list[dict]:
    if args.break_seam:                  # the seam's cases need no container
        stages = [preflight_stage("seam", out)]
        return stages if stages[0]["verdict"] != PASS else \
            [*stages, seam_stage(args.break_seam, out)]
    stages = [preflight_stage("consumer-local", out)]
    if stages[0]["verdict"] != PASS:
        return stages
    # The lane's own task-local services (tasklocal.py `e2c`), not D1's shared defaults.
    ports, containers = pf.namespace(pf.load()["namespaces"]["e2c"])
    own = {"INFRX_D_TASK": "e2c", "INFRX_D2_VALKEY_PORT": str(ports[1]),
           "INFRX_D2_VALKEY_CONTAINER": containers[1]}
    for suite in ("contracts", "d", "m", "w", "g"):
        stages.append(pytest_stage(f"api-{suite}", [*uv(), f"tests/{suite}"], API, out, own))
    stages.append(pytest_stage("bench-test", [str(PY), "-m", "pytest", "-q", "-p",
                                              "no:cacheprovider", "models/marlin2b/tests"],
                               REPO, out))
    report = out / "integration-l1.json"
    stages.append(runner_stage("integration-l1", [str(PY), "tests/integration/run.py",
                                                  "--layer", "1", "--only-suites",
                                                  "--no-mutants", "--report", str(report)],
                               out, report))
    ready = preflight_stage("integration-l2", out)
    if ready["verdict"] != PASS:
        stages.append({**ready, "stage": "integration-l2",
                       "detail": "not run: the layer-2 stack's prerequisites are missing"})
    else:
        report = out / "integration-l2.json"
        stages.append(runner_stage("integration-l2", [str(PY), "tests/integration/run.py",
                                                      "--layer", "2", "--no-mutants",
                                                      "--report", str(report)], out, report))
    stages.append(e3c_stage(out))
    return stages


def backend_certify(args, out: Path) -> list[dict]:
    remote = any(flag in args.certify_args for flag in ("--box", "--target"))
    if remote and args.certify_profile is None:
        return [{"stage": "certify-profile", "verdict": INVALID,
                 "detail": "a --box/--target run needs --certify-profile (E1C's load/cert "
                           "profile); none given, nothing started"}]
    stages = [preflight_stage("backend-certify", out, args.certify_profile)]
    if stages[0]["verdict"] == INVALID or args.validate_only:
        return stages
    if remote:
        # ponytail: validate-only until E1C's --validate-only/profile executor merges; a live
        # or paid run is started by the coordinator with the recorded flags, never from here.
        return [*stages, {"stage": "certify", "verdict": NOT_RUN,
                          "detail": "live certification is not started by this wrapper",
                          "would_run": f"{PY} tests/integration/backend/certify.py {BOX_FLAGS}"}]
    if stages[0]["verdict"] != PASS:
        return stages
    report = out / "certify.json"
    return [*stages, runner_stage("certify", [str(PY), "tests/integration/backend/certify.py",
                                              *args.certify_args, "--report", str(report)],
                                  out, report)]


def app_e2e(args, out: Path) -> list[dict]:
    stages = [preflight_stage("app-e2e", out)]
    if stages[0]["verdict"] != PASS:
        return stages
    if not (REPO / "apps" / "app" / "node_modules").is_dir():
        return [*stages, {"stage": "console-install", "verdict": BLOCKED,
                          "detail": "apps/app/node_modules missing: "
                                    "cd apps/app && pnpm install --frozen-lockfile"}]
    for target in ("console-test", "console-lint", "console-typecheck"):
        code, seconds, log = run(target, ["make", target], REPO, out)
        stages.append({"stage": target, "command": f"make {target}", "exit": code,
                       "duration_s": seconds, "verdict": PASS if code == 0 else FAIL,
                       "log": str(log), "tail": log.read_text().strip().splitlines()[-6:]})
    stages.append({"stage": "browser-journey", "verdict": NOT_RUN,
                   "detail": "E3A owns the browser journey (apps/app/tests/e2e); not built yet"})
    return stages


GATES = {"consumer-local": consumer_local, "backend-certify": backend_certify,
         "app-e2e": app_e2e}


def head() -> dict:
    sha = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True,
                         text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"],
                           capture_output=True, text=True).stdout.strip() != ""
    return {"sha": sha, "dirty": dirty}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("gate", choices=sorted(GATES))
    parser.add_argument("--out", type=Path, help="verdict directory (default: a new temp dir)")
    parser.add_argument("--break-seam", choices=sorted(SEAMS),
                        help="consumer-local: break one control; the gate must FAIL")
    parser.add_argument("--certify-profile", type=Path)
    parser.add_argument("--validate-only", action="store_true",
                        help="backend-certify: preflight and profile validation only")
    argv = sys.argv[1:] if argv is None else list(argv)
    cut = argv.index("--") if "--" in argv else len(argv)
    args = parser.parse_args(argv[:cut])
    args.certify_args = argv[cut + 1:]                   # after --: flags for certify.py
    out = args.out or Path(tempfile.mkdtemp(prefix=f"infrx-e2c-{args.gate}-"))
    out.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    source = head()                                # the tree the stages start from
    stages = GATES[args.gate](args, out)
    verdict = worst(stage["verdict"] for stage in stages)
    result = {"schema": "infrx.e2c.verdict/1", "gate": args.gate, "verdict": verdict,
              "exit": EXIT[verdict], "head": source, "started": started,
              "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "host": {"platform": platform.platform(), "python": platform.python_version()},
              "argv": argv, "stages": stages}
    (out / "verdict.json").write_text(json.dumps(result, indent=2) + "\n")
    for stage in stages:
        print(f"{stage['verdict']:8} {stage['stage']}")
    print(f"{args.gate}: {verdict} (exit {EXIT[verdict]}) - {out / 'verdict.json'}")
    return EXIT[verdict]


if __name__ == "__main__":
    raise SystemExit(main())
