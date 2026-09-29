#!/usr/bin/env python3
"""LAB-LOCAL: the E4-ON regression - the whole Lab composed locally with EVERY switch ON.

    tests/integration/lab-local.sh [--out DIR] [--keep] [--reuse] [--only STAGES] [-k EXPR]
    (make lab-local: verdict.json under research/plan/evidence/e/E4ON-raw-<head7>)

1. **stack**: E2's services in the e3l block (E3L's runner lock: no 100-port block of the
   57500-57599 band is free, so the finished E3L gate's block is borrowed while its lock is
   held; `lab-on` is this gate's tasklocal key for the D harness), every migration (0001-0051)
   through run.py's preflight/services/migrate. `--reuse` keeps a stack `--keep` left.
2. **lab-build**: `pnpm build` in apps/lab with the composition's public names inlined.
3. **e4-on**: the consumer E4 subset (tests/g tests/w tests/contracts tests/i/test_packaging.py)
   with every switch ON in its environment, the D harness on `lab-on`, MinIO the stack's.
4. **scenarios**: `scenarios_on.py` over the composition (`lab_world.composition`): the
   gateway + consumer worker + every Lab worker role + the control factory + the Lab web, every
   switch ON; each route family smoked with a real Lab session; the consumer path still serves.
5. **lab-journeys**: the Lab app's real-route journeys that accept this key (tests/n's journey,
   `LAB_N_REAL=1 INFRX_D_TASK=lab-on`); the key-pinned stack tests (r2, p1, b3, lab-v1m) are
   NOT RUN, named with the exact rerun (their backends refuse any other key: WR-LDP-1).

Every stage is PASS / FAIL / BLOCKED / INVALID / NOT RUN; the gate is the worst (FAIL > INVALID
> BLOCKED > NOT RUN > PASS), exit 0 / 1 / 3 / 3 / 4, as E2C's gates.py. A skip is never a pass.
Label: a controlled engine, fake teacher/connectors, a local Lab build and a Supabase stand-in;
not Marlin quality, not GPU capacity, not hosted (Vercel/Supabase) behaviour.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
API = REPO / "apps" / "infrx-api"
PY = API / ".venv" / "bin" / "python"
NAMESPACE = "e3l"
KEY = "lab-on"
PASS, FAIL, BLOCKED, INVALID, NOT_RUN = "PASS", "FAIL", "BLOCKED", "INVALID", "NOT RUN"
RANK = {PASS: 0, NOT_RUN: 1, BLOCKED: 2, INVALID: 3, FAIL: 4}
EXIT = {PASS: 0, FAIL: 1, BLOCKED: 3, NOT_RUN: 3, INVALID: 4}
LOCK = Path("/tmp") / f"infrx-{NAMESPACE}.runner.lock"     # E3L's: one user of the block
RUNNER = "tests/integration/lab_local/runner.py"
STAGES = ("e4-on", "scenarios", "lab-journeys")
#: The consumer E4 subset (the composition lanes' E4 regression), run with the switches ON.
E4_SUITES = ("tests/g", "tests/w", "tests/contracts", "tests/i/test_packaging.py")

SCENARIOS = {
    "o01": "the gateway starts with every gateway switch ON on the dedicated runtime login",
    "o02": "the consumer worker starts with TRACE_PUMPS and LAB_EVAL_WORKER ON",
    "o03": "every Lab worker role starts from its own env (or refuses by name)",
    "o04": "the Lab control factory serves a real Lab session",
    "o05": "every Lab route family answers a real Lab session and refuses a consumer key",
    "o06": "the consumer App path serves and settles once with every switch ON",
    "o07": "the Lab web (production build, https origin) renders each page family signed in",
}
REQUIRED = {
    "o01": ("test_o01_the_gateway_is_ready_with_every_switch_on",),
    "o02": ("test_o02_the_consumer_worker_is_ready_with_its_switches_on",
            "test_o02_the_consumer_workers_lab_seam_refuses_by_name"),
    "o03": tuple(f"test_o03_the_{role}_role_starts" for role in
                 ("eval", "checkpoints", "judge", "annotation", "training", "rollout",
                  "datasets")),
    "o04": ("test_o04_the_control_factory_serves_a_lab_session",),
    "o05": ("test_o05_every_lab_route_family_answers_a_lab_session",
            "test_o05_the_lab_routes_gateway_serves_every_family",
            "test_o05_a_consumer_key_is_no_lab_session_on_any_family"),
    "o06": ("test_o06_the_consumer_path_serves_and_settles_once",),
    "o07": ("test_o07_the_lab_web_renders_every_page_family_signed_in",),
}
#: The Lab app's real-route journeys. `pinned`: the backend refuses any key but its own, so
#: this gate cannot run it on `lab-on` (WR-LDP-1) - NOT RUN with the owner's exact rerun.
JOURNEYS = {
    "datasets": {"file": "tests/n/journey.test.ts", "flag": "LAB_N_REAL", "pinned": None},
    "releases": {"file": "tests/r/stack.test.ts", "flag": "LAB_R4_REAL", "pinned": "r2"},
    "pipelines": {"file": "tests/p/stack.test.ts", "flag": "LAB_P4_REAL", "pinned": "p1"},
    "evaluations": {"file": "tests/b/stack.test.ts", "flag": "LAB_B4_REAL", "pinned": "b3"},
    "traces": {"file": "tests/v/list/stack.test.ts", "flag": "LAB_V1M_REAL",
               "pinned": "lab-v1m"},
}
HARNESS = re.compile(r"^(?:[\w.]*\.)?(?:HarnessError|OperationalError)\b|address already in use")
CASE = re.compile(r"test_(?P<sid>o\d\d)_")
MARK = re.compile(r"\b(BLOCKED|INVALID)\[")


def worst(statuses) -> str:
    return max(statuses, key=RANK.__getitem__, default=NOT_RUN)


def case_status(case) -> tuple[str, str]:
    """(status, reason) of one JUnit testcase: a failure is FAIL unless the harness broke
    (INVALID); a skip is NOT RUN unless it says BLOCKED/INVALID; an xfail is never a pass."""
    for tag in ("failure", "error"):
        node = case.find(tag)
        if node is not None:
            message = (node.get("message") or node.text or "")[:600]
            return (INVALID, "INVALID[harness] " + message) if HARNESS.search(message) \
                else (FAIL, message)
    node = case.find("skipped")
    if node is None:
        return PASS, ""
    message = ((node.get("message") or "") + " " + (node.text or "")).strip()
    if node.get("type") == "pytest.xfail":
        return NOT_RUN, "xfail is not a pass here: " + message[:300]
    mark = MARK.search(message)
    return ({"BLOCKED": BLOCKED, "INVALID": INVALID}[mark.group(1)] if mark else NOT_RUN,
            message[:600])


def classify(junit_xml: str) -> dict:
    """Per scenario: status, cases and reasons; a scenario missing a required case is NOT RUN."""
    result = {sid: {"status": NOT_RUN, "cases": {}, "reasons": []} for sid in SCENARIOS}
    for case in ET.fromstring(junit_xml).iter("testcase"):
        match = CASE.match(case.get("name", ""))
        if not match or match.group("sid") not in SCENARIOS:
            continue
        status, reason = case_status(case)
        entry = result[match.group("sid")]
        entry["cases"][case.get("name")] = status
        if reason:
            entry["reasons"].append(f"{case.get('name')}: {reason}")
    for sid, entry in result.items():
        entry["status"] = worst(entry["cases"].values()) if entry["cases"] else NOT_RUN
        absent = [name for name in REQUIRED[sid] if name not in entry["cases"]]
        if absent:
            entry["status"] = worst([entry["status"], NOT_RUN])
            entry["reasons"].append(f"required case absent (deselected or deleted): {absent}")
    return result


def pytest_verdict(code: int, counts: dict) -> tuple[str, str]:
    """A pytest stage (E2C's rule): a failure FAILs; no case, a skip or an xfail BLOCKS."""
    if code != 0 or counts["failed"] or counts["errors"]:
        return FAIL, f"exit {code}: {counts['failed']} failed, {counts['errors']} errors"
    if counts["tests"] == 0:
        return BLOCKED, "no test ran"
    if counts["skipped"] or counts["xfailed"]:
        return BLOCKED, (f"{counts['skipped']} required case(s) skipped, "
                         f"{counts['xfailed']} quarantined (strict xfail)")
    return PASS, ""


def journey_row(name: str, spec: dict) -> dict:
    """A key-pinned journey is NOT RUN with the owner's exact rerun; never a pass."""
    rerun = (f"cd apps/lab && {spec['flag']}=1 INFRX_D_TASK={spec['pinned'] or KEY} "
             f"node --test {spec['file']}")
    if spec["pinned"]:
        return {"stage": f"journey:{name}", "status": NOT_RUN, "rerun": rerun,
                "reason": f"NOT RUN[WR-LDP-1] its backend refuses every key but "
                          f"{spec['pinned']}'s; this gate runs only on {KEY}"}
    return {"stage": f"journey:{name}", "status": None, "rerun": rerun}


def gate(stages: list[dict]) -> str:
    return worst(stage["status"] for stage in stages)


def blocked_all(why: str) -> list[dict]:
    return [{"stage": name, "status": BLOCKED, "reason": f"BLOCKED[stack] {why}"}
            for name in STAGES]


# ------------------------------------------------------------------ the run


def _counts(junit: Path) -> dict:
    sys.path.insert(0, str(HERE.parent))
    import gates                                           # E2C's JUnit counter
    return gates.junit_counts(junit)


def provision(report, reuse: bool, pull: bool) -> tuple[bool, str]:
    """E2's stages from run.py in the borrowed e3l block. (usable, why not)."""
    import run
    import stack
    if reuse and stack.has_stack():
        return True, "reused a kept stack"
    if not run.preflight(report, want_services=True):
        return False, "preflight: " + json.dumps(report.stages[-1]["detail"], default=str)[:400]
    if not run.services(report, pull=pull):
        return False, "services: " + str(report.stages[-1]["detail"])[:400]
    if run.migrate(report, 20260929) is None:
        return False, "migrate: " + str(report.stages[-1]["detail"])[:400]
    return True, ""


def logged(name: str, argv: list[str], out: Path, cwd: Path, env: dict,
           timeout: float) -> tuple[int, float, Path]:
    log = out / f"{name}.log"
    began = time.monotonic()
    with log.open("w") as sink:
        try:
            code = subprocess.run(argv, cwd=cwd, env=env, stdout=sink,
                                  stderr=subprocess.STDOUT, timeout=timeout).returncode
        except subprocess.TimeoutExpired:
            code = 124
    return code, round(time.monotonic() - began, 1), log


def lab_build(out: Path, lab_world) -> dict:
    """The Lab web's production build with the composition's public names inlined."""
    env = {**os.environ, **lab_world.lab_web_env(lab_world.GATEWAY_URL, lab_world.STANDIN_URL)}
    code, seconds, log = logged("lab-build", ["pnpm", "build"], out, REPO / "apps" / "lab",
                                env, 1200)
    build = REPO / "apps" / "lab" / ".next" / "BUILD_ID"
    return {"stage": "lab-build", "status": PASS if code == 0 and build.exists() else FAIL,
            "exit": code, "seconds": seconds, "log": str(log),
            "build_id": build.read_text().strip() if code == 0 and build.exists() else None}


def e4_on(out: Path, lab_world) -> dict:
    """The E4 subset with every switch ON (and what each needs on this stack) in its env."""
    junit = out / "e4-on.xml"
    spool = out / "e4-on-spool"
    spool.mkdir(exist_ok=True)
    from infrx.contracts.tasklocal import local_services
    own = local_services(KEY)
    env = {**os.environ, **lab_world.switch_env(spool), **lab_world.stack.s3_env(),
           "INFRX_D_TASK": KEY, "INFRX_D2_VALKEY_PORT": str(own["valkey"].host_port),
           "INFRX_D2_VALKEY_CONTAINER": own["valkey"].container,
           "INFRX_M_S3_ENDPOINT": lab_world.harness.s3_endpoint(), "INFRX_M_S3_LOCAL_CREDS": "1"}
    env.pop("INFRX_E2_NAMESPACE", None)          # the suites use their own harnesses
    argv = [str(PY), "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider", *E4_SUITES,
            f"--junitxml={junit}"]
    code, seconds, log = logged("e4-on", argv, out, API, env, 7200)
    if not junit.exists():
        return {"stage": "e4-on", "status": FAIL, "exit": code, "seconds": seconds,
                "log": str(log), "reason": "no junit report"}
    counts = _counts(junit)
    status, reason = pytest_verdict(code, counts)
    return {"stage": "e4-on", "status": status, "exit": code, "seconds": seconds,
            "log": str(log), "counts": counts, "reason": reason,
            "switches_on": [*lab_world.GATEWAY_SWITCHES, *lab_world.WORKER_SWITCHES],
            "pending_switches": lab_world.PENDING_SWITCHES}


def scenarios(out: Path, keyword: str | None) -> tuple[dict, dict]:
    junit, log = out / "scenarios.xml", out / "scenarios.log"
    argv = [str(PY), "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rfEs",
            "--import-mode=prepend", "-o", "junit_family=xunit1", f"--junitxml={junit}",
            str(HERE / "scenarios_on.py"), *(["-k", keyword] if keyword else [])]
    env = {**os.environ, "INFRX_E2_NAMESPACE": NAMESPACE, "INFRX_LAB_LOCAL_OUT": str(out),
           "COLUMNS": "400"}
    code, seconds, _ = logged("scenarios", argv, out, REPO, env, 5400)
    result = classify(junit.read_text() if junit.exists() else "<testsuites/>")
    row = {"stage": "scenarios", "status": worst(e["status"] for e in result.values()),
           "exit": code, "seconds": seconds, "log": str(log), "junit": str(junit)}
    return row, result


def journeys(out: Path) -> list[dict]:
    rows = []
    for name, spec in JOURNEYS.items():
        row = journey_row(name, spec)
        if row["status"] is None:
            env = {**os.environ, spec["flag"]: "1", "INFRX_D_TASK": KEY}
            code, seconds, log = logged(f"journey-{name}", ["node", "--test", spec["file"]],
                                        out, REPO / "apps" / "lab", env, 1800)
            text = log.read_text(errors="replace")
            skipped = re.search(r"^# skipped (\d+)", text, re.M)
            row.update(exit=code, seconds=seconds, log=str(log),
                       status=FAIL if code else (BLOCKED if skipped and skipped.group(1) != "0"
                                                 else PASS))
        rows.append(row)
    return rows


def pins() -> dict:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                              text=True).stdout.strip()
    return {"head": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain"))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=None, help="verdict directory")
    parser.add_argument("--keep", action="store_true", help="leave the stack up")
    parser.add_argument("--reuse", action="store_true", help="use a stack --keep left")
    parser.add_argument("--pull", action="store_true", help="pull the pinned digests first")
    parser.add_argument("--only", default=",".join(STAGES), help="comma-separated stages")
    parser.add_argument("-k", dest="keyword", default=None, help="scenarios: pytest -k")
    args = parser.parse_args(argv)
    started, clock = datetime.now(timezone.utc), time.monotonic()
    out = args.out or Path(os.environ.get("TMPDIR", "/tmp")) / f"infrx-lab-local-{started:%Y%m%dT%H%M%SZ}"
    out.mkdir(parents=True, exist_ok=True)
    only = [s for s in (x.strip() for x in args.only.split(",")) if s]
    os.environ["INFRX_E2_NAMESPACE"] = NAMESPACE          # before E2's harness is imported
    sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parent / "backend"), str(API)]
    import lab_world
    import run
    report, stages, result, usable, why = run.Report(), [], None, False, ""
    lock = LOCK.open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held = True
    except BlockingIOError:
        held = False
    try:
        with run.signals_handled():
            if not held:
                why = f"namespace {NAMESPACE} in use by another run (lock {LOCK})"
            else:
                usable, why = provision(report, args.reuse, args.pull)
            if not usable:
                stages = blocked_all(why)
            else:
                stages.append(lab_build(out, lab_world))
                if "e4-on" in only:
                    stages.append(e4_on(out, lab_world))
                if "scenarios" in only:
                    row, result = scenarios(out, args.keyword)
                    stages.append(row)
                if "lab-journeys" in only:
                    stages += journeys(out)
                stages += [{"stage": name, "status": NOT_RUN, "reason": "not selected (--only)"}
                           for name in STAGES if name not in only]
    except run.Interrupted as stop:
        stages.append({"stage": "interrupted", "status": NOT_RUN,
                       "reason": f"signal {stop.signum}: unfinished stages are NOT RUN"})
    finally:
        if held and not (usable and args.keep):
            run.backend_teardown(report)
            run.teardown(report)
        lock.close()
    verdict = gate(stages)
    payload = {
        "task": "LAB-DEPLOY-PREP", "gate": "E4-ON (LAB-LOCAL: every switch ON)",
        "verdict": verdict, "exit": EXIT[verdict],
        "label": "real PostgreSQL/PostgREST/Valkey/ClickHouse/S3-compatible services (e3l block), "
                 "the merged code with every switch ON, a controlled engine, a judge fake as "
                 "the teacher, the manual-bundle training connector, a local Lab build behind "
                 "local TLS and a Supabase stand-in; not Marlin quality, not GPU capacity, not "
                 "hosted behaviour",
        "pins": pins(), "namespace": NAMESPACE, "key": KEY,
        "started": started.isoformat(timespec="seconds"),
        "seconds": round(time.monotonic() - clock, 1),
        "stack": {"usable": usable, "why_not": why or None,
                  "stages": [{k: s.get(k) for k in ("stage", "status", "seconds")}
                             for s in report.stages]},
        "stages": stages,
        "scenarios": [{"id": sid, "title": SCENARIOS[sid], **entry}
                      for sid, entry in (result or {}).items()],
        "lock": {"path": str(LOCK), "held": held},
        "reproduce": f"{PY.relative_to(REPO)} {RUNNER} --out <dir>",
    }
    (out / "verdict.json").write_text(json.dumps(payload, indent=2, default=str))
    for stage in stages:
        print(f"{stage['status']:>8}  {stage['stage']}")
    for entry in payload["scenarios"]:
        print(f"{entry['status']:>8}  {entry['id']}  {entry['title']}")
    print(f"gate {verdict} -> {out / 'verdict.json'}")
    return EXIT[verdict]


if __name__ == "__main__":
    raise SystemExit(main())
