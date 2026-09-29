#!/usr/bin/env python3
"""E5L: the LAB-OBSERVE integration gate runner - provider traces, review and the judge DRY
RUN through the real local services on one SHA, with faults.

    apps/infrx-api/.venv/bin/python tests/integration/lab_observe/runner.py \\
        [--out DIR] [--keep] [--reuse] [--only o01,o07] [-k EXPR]

`INFRX_E5L_PROJECT=e5l2` runs the same gate under compose project `infrx-e5l2` (its own
containers, volumes and network; same ports, namespace and evidence layout): see
`observe_world.load_harness`.

1. **stack**: E2's services in namespace `e5l` (tasklocal block 57100-57199, PostgreSQL 57132;
   run.py's own preflight/services/migrate stages, every migration incl. the Lab's). E3C's
   composed world supplies PostgREST per clone, the controlled engine and the gateway/worker
   processes; `observe_world` adds a ClickHouse database and an object prefix per trip, T2I's
   shipper, L2's access and J2's judge over the local judge fake. `--reuse` keeps a stack a
   previous `--keep` left. A stack that cannot provision makes every scenario BLOCKED.
2. **scenarios**: pytest over `scenarios_*.py` (never collected by `pytest tests/integration`),
   JUnit per case, mapped to the matrix below.
3. **verdict**: `<out>/verdict.json` - per scenario PASS / FAIL / BLOCKED / INVALID / NOT RUN,
   per manifest test id the worst of its scenarios, pinned SHAs and raw evidence. The gate is
   the worst of all (FAIL > INVALID > BLOCKED > NOT RUN > PASS); exit 0 / 1 / 3 / 3 / 4, as
   E2C's gates.py, E3C's and E3L's runners.
4. teardown of exactly what step 1 created, unless `--keep`.

A skip is never a pass: `NOT RUN[<lanes>]` names the unmerged lanes and the rerun command.

Label: orchestration with a controlled engine; the judge is a DRY RUN (J2's local judge fake,
J2's in-memory D6J ledger; no external provider, P-10 absent) and is never evidence of a
live-judge run, which is recorded separately if one is ever assigned. Not Marlin quality, not
GPU capacity, not hosted behaviour.
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
NAMESPACE = "e5l"
BASE = "9a48300c"            # the LW5 base this runner was built on (coordinator dispatch)
PASS, FAIL, BLOCKED, INVALID, NOT_RUN = "PASS", "FAIL", "BLOCKED", "INVALID", "NOT RUN"
RANK = {PASS: 0, NOT_RUN: 1, BLOCKED: 2, INVALID: 3, FAIL: 4}
EXIT = {PASS: 0, FAIL: 1, BLOCKED: 3, NOT_RUN: 3, INVALID: 4}
RUNNER = "tests/integration/lab_observe/runner.py"
PY = "apps/infrx-api/.venv/bin/python"
#: The manifest's E5L test_ids (tasks.json), in its order; test_e5l_runner holds them equal.
TEST_IDS = ("TRACE-BOUNDS", "TRACE-RECOVER", "TRACE-TENANT", "FEEDBACK-ACK", "JUDGE-BUDGET",
            "JUDGE-SCORES", "LAB-ACCESS", "CONSOLE-FLOWS")
JUDGE_LABEL = ("dry-run: J2's local judge fake on 127.0.0.1 and J2's in-memory D6J ledger; no "
               "external provider (P-10 absent); never a live-judge run")

# The matrix (09 E5L + the lane brief). `lanes`: what must merge before it can go green.
SCENARIOS = {
    "o01": {"title": "capture -> ship -> search: off by default; a captured request ships once "
                     "with its pins; only its org finds it; capture on via the switch",
            "test_ids": ["TRACE-BOUNDS", "TRACE-TENANT"], "lanes": []},
    "o02": {"title": "feedback acknowledged after commit, owned by its key, projected once",
            "test_ids": ["FEEDBACK-ACK"], "lanes": []},
    "o03": {"title": "Lab review: a provider reads a grantor's trace only under a current grant, "
                     "in-process and through the gateway's traces route",
            "test_ids": ["LAB-ACCESS"], "lanes": []},
    "o04": {"title": "judge dry run: default mode sends nothing; a consented run sends once, "
                     "scores once, charges no customer wallet; the D6J ledger",
            "test_ids": ["JUDGE-BUDGET", "JUDGE-SCORES"], "lanes": []},
    "o05": {"title": "a grant revoked mid-queue: nothing leaves, the hold is released",
            "test_ids": ["JUDGE-BUDGET", "LAB-ACCESS"], "lanes": []},
    "o06": {"title": "content expired or deleted: gone for every read, never sent to the judge",
            "test_ids": ["TRACE-TENANT", "JUDGE-BUDGET"], "lanes": []},
    "o07": {"title": "the projection dropped (ClickHouse killed): segment held, App serving, "
                     "shipped once after", "test_ids": ["TRACE-RECOVER"], "lanes": []},
    "o08": {"title": "a judge submit timed out and the worker restarted: quarantined, never "
                     "resent, reconciled", "test_ids": ["JUDGE-BUDGET"], "lanes": []},
    "o09": {"title": "a worker restarted: the feedback projector after its insert, the box "
                     "worker mid-traffic", "test_ids": ["TRACE-RECOVER", "FEEDBACK-ACK"],
            "lanes": []},
    "o10": {"title": "the Lab review panel renders the traces route's answer (browser flow)",
            "test_ids": ["CONSOLE-FLOWS"], "lanes": []},
}
REQUIRED = {
    "o01": ("test_o01_capture_is_off_by_default_and_a_served_request_leaves_no_trace",
            "test_o01_a_captured_request_ships_once_with_its_pins_and_only_its_org_finds_it",
            "test_o01_capture_turned_on_through_the_composition_switch"),
    "o02": ("test_o02_feedback_is_acknowledged_after_commit_owned_by_its_key_and_projected_once",),
    "o03": ("test_o03_a_provider_reads_a_grantors_trace_only_under_a_current_sharing_grant",
            "test_o03_the_lab_traces_route_through_the_real_gateway"),
    "o04": ("test_o04_the_default_dry_run_mode_sends_nothing",
            "test_o04_a_consented_run_sends_once_scores_once_and_charges_no_customer_wallet",
            "test_o04_the_judge_ledger_is_d6js_postgresql_ledger"),
    "o05": ("test_o05_a_grant_revoked_between_reservation_and_egress_sends_nothing",),
    "o06": ("test_o06_expired_or_deleted_content_is_gone_for_every_read_before_and_after_the_sweep",
            "test_o06_content_expired_or_deleted_before_egress_never_reaches_the_judge",
            "test_o06_after_the_sweep_nothing_is_left_to_send"),
    "o07": ("test_o07_clickhouse_down_holds_the_segment_serves_the_app_and_ships_once_after",),
    "o08": ("test_o08_a_timed_out_submit_is_quarantined_never_resent_and_reconciled",),
    "o09": ("test_o09_a_projector_killed_after_its_insert_redelivers_and_projects_once",
            "test_o09_a_box_worker_killed_mid_traffic_restarts_and_finishes_every_job_once"),
    "o10": ("test_o10_the_lab_review_panel_renders_the_routes_answer",),
}
#: R222 as amended by R234: the lanes whose NOT RUN is outside local scope, with their ruled
#: class (as lab_evaluate's runner). Both are kept only to judge recorded verdicts on their own
#: lanes: R234 (ii) `product WR` for df837faf's o01, which waited on WR-C6-CAPTURE (bound since
#: merge #54: no scenario declares it now); R234 (i) `lab-e2e UI` for bd13f72's o10, which waited
#: NOT RUN[LAB-E2E] (bound through apps/lab/tests/e2e/gate.py, R238).
OUT_OF_SCOPE = {"LAB-E2E": "lab-e2e UI", "WR-C6-CAPTURE": "product WR: WR-C6-CAPTURE"}
HARNESS = re.compile(r"^(?:[\w.]*\.)?(?:HarnessError|OperationalError)\b|address already in use")
CASE = re.compile(r"test_(?P<sid>o\d\d)_")
MARK = re.compile(r"\b(BLOCKED|INVALID)\[")
LOCK = Path("/tmp") / f"infrx-{NAMESPACE}.runner.lock"


def worst(statuses) -> str:
    return max(statuses, key=RANK.__getitem__, default=NOT_RUN)


def case_status(case) -> tuple[str, str]:
    """(status, reason) of one JUnit testcase: a failure is FAIL unless the harness broke
    (INVALID); a skip is NOT RUN unless it says BLOCKED/INVALID; an xfail is never a pass."""
    for tag in ("failure", "error"):
        node = case.find(tag)
        if node is not None:
            message = (node.get("message") or node.text or "")[:400]
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
            message[:400])


def classify(junit_xml: str, only: set[str] | None = None) -> dict:
    """Per scenario: status, cases and reasons, from pytest's JUnit XML."""
    result = {sid: {"status": NOT_RUN, "cases": {}, "reasons": []} for sid in SCENARIOS}
    for case in ET.fromstring(junit_xml).iter("testcase"):
        name = case.get("name", "")
        match = CASE.match(name)
        if not match or match.group("sid") not in SCENARIOS:
            continue
        status, reason = case_status(case)
        entry = result[match.group("sid")]
        entry["cases"][name] = status
        if reason:
            entry["reasons"].append(f"{name}: {reason}")
    for sid, entry in result.items():
        if only and sid not in only:
            entry["reasons"].append("not selected (--only)")
        entry["status"] = worst(entry["cases"].values()) if entry["cases"] else NOT_RUN
        absent = [name for name in REQUIRED[sid] if name not in entry["cases"]]
        if absent and entry["cases"]:
            entry["status"] = worst([entry["status"], NOT_RUN])
            entry["reasons"].append(f"required case absent (deselected or deleted): {absent}")
    return result


def blocked_all(result: dict, why: str) -> dict:
    for entry in result.values():
        entry["status"], entry["reasons"] = BLOCKED, [f"BLOCKED[stack] {why}"]
    return result


def cells(result: dict) -> dict:
    """Per manifest test id: the worst of the scenarios carrying it."""
    return {tid: worst(result[sid]["status"] for sid, spec in SCENARIOS.items()
                       if tid in spec["test_ids"]) for tid in TEST_IDS}


def gate(result: dict) -> str:
    return worst(entry["status"] for entry in result.values())


def r222(result: dict) -> dict:
    """R222/R235/R253: accepted locally with nothing but PASS, and NOT RUN whose every reason is
    the scenario's own wait on out-of-local-scope lanes and carries its rerun (`--only <sid>`,
    so the 400-char reason cut can never drop it silently). `open` = what keeps it from
    acceptance."""
    def excused(sid: str, entry: dict) -> bool:
        lanes = entry.get("lanes") or SCENARIOS[sid]["lanes"]   # a recorded verdict's own
        if entry["status"] != NOT_RUN:        # R234: an in-scope FAIL is never excused
            return False
        return set(lanes) <= set(OUT_OF_SCOPE) and bool(entry["cases"]) and \
            all(f"NOT RUN[{','.join(lanes)}]" in reason for reason in entry["reasons"]) and \
            all(f"--only {sid}" in reason for reason in entry["reasons"])   # R253: its rerun
    still = {sid: entry["status"] for sid, entry in result.items()
             if entry["status"] != PASS and not excused(sid, entry)}
    return {"accepted": not still, "open": still}


def reproduce(sid: str | None = None) -> str:
    return f"{PY} {RUNNER} --out <dir>" + (f" --only {sid}" if sid else "")


# ------------------------------------------------------------------ the run


def provision(report, reuse: bool, pull: bool) -> tuple[bool, str]:
    """E2's stages from run.py, in namespace e5l. (usable, why not)."""
    import run
    import stack
    if reuse and stack.has_stack():
        return True, "reused a kept stack"
    if not run.preflight(report, want_services=True):
        return False, "preflight: " + json.dumps(report.stages[-1]["detail"], default=str)[:400]
    if not run.services(report, pull=pull):
        return False, "services: " + str(report.stages[-1]["detail"])[:400]
    if run.migrate(report, 20260927) is None:
        return False, "migrate: " + str(report.stages[-1]["detail"])[:400]
    return True, ""


def pytest_run(out: Path, keyword: str | None) -> tuple[dict, str]:
    junit, log = out / "scenarios.xml", out / "scenarios.log"
    files = sorted(str(path) for path in HERE.glob("scenarios_*.py"))
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rfEs",
            # WR-E7L-5's repo-root pytest.ini forces --import-mode=importlib, which does not
            # auto-insert a collected file's own directory into sys.path; these scenario
            # files rely on that (bare `import observe_world` after their own path.insert),
            # so this subprocess - run against the live checkout, never a scratch copy -
            # needs prepend mode back, overriding the root ini's addopts.
            "--import-mode=prepend",
            "-o", "junit_family=xunit1", f"--junitxml={junit}", *files,
            *(["-k", keyword] if keyword else [])]
    env = {**os.environ, "INFRX_E2_NAMESPACE": NAMESPACE, "INFRX_E5L_OUT": str(out),
           "COLUMNS": "400"}
    began = time.monotonic()
    with log.open("w") as sink:
        try:
            code = subprocess.run(argv, cwd=REPO, env=env, stdout=sink,
                                  stderr=subprocess.STDOUT, timeout=5400).returncode
        except subprocess.TimeoutExpired:
            code = 124
    text = log.read_text(errors="replace").strip()
    return ({"argv": " ".join(argv), "exit": code, "seconds": round(time.monotonic() - began, 1),
             "summary": text.splitlines()[-1] if text else "", "log": str(log)},
            junit.read_text() if junit.exists() else "")


def pins(harness) -> dict:
    def git(*args: str) -> str:
        done = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True)
        return done.stdout.strip()
    try:
        images = harness.compose_images()
    except Exception as exc:                                   # noqa: BLE001 - recorded
        images = {"error": f"{type(exc).__name__}: {exc}"[:200]}
    return {"base": BASE, "head": git("rev-parse", "HEAD"),
            "dirty": bool(git("status", "--porcelain")), "images": images}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=None, help="verdict directory")
    parser.add_argument("--keep", action="store_true", help="leave the e5l stack up")
    parser.add_argument("--reuse", action="store_true", help="use a stack --keep left")
    parser.add_argument("--pull", action="store_true", help="pull the pinned digests first")
    parser.add_argument("--only", default="", help="comma-separated scenario ids")
    parser.add_argument("-k", dest="keyword", default=None, help="pytest -k expression")
    args = parser.parse_args(argv)
    started, clock = datetime.now(timezone.utc), time.monotonic()
    out = (args.out or Path(os.environ.get("TMPDIR", "/tmp")) / f"infrx-e5l-{started:%Y%m%dT%H%M%SZ}").resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.environ["INFRX_E2_NAMESPACE"] = NAMESPACE          # before E2's harness is imported
    sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parent / "backend")]
    import observe_world
    import run
    harness = observe_world.harness
    only = {sid.strip() for sid in args.only.split(",") if sid.strip()}
    keyword = args.keyword or (" or ".join(f"test_{sid}_" for sid in sorted(only)) if only else None)
    report, runs, usable, why = run.Report(), {}, False, ""
    result = classify("<testsuites/>", only)
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
            if usable:
                done, junit = pytest_run(out, keyword)
                runs["scenarios"] = done
                result = classify(junit or "<testsuites/>", only)
            else:
                blocked_all(result, why)
    except run.Interrupted as stop:
        why = f"interrupted by signal {stop.signum}: unfinished scenarios are NOT RUN"
    finally:
        # A stack that did not provision is torn down too (never left half-up), but only by
        # the run holding the namespace lock.
        if held and not (usable and args.keep):
            run.backend_teardown(report)
            run.teardown(report)
        lock.close()
    verdict = gate(result)
    payload = {
        "task": "E5L", "gate": "LAB-OBSERVE (local; I2L-OBS staging needs P-08)",
        "verdict": verdict, "exit": EXIT[verdict], "cells": cells(result), "r222": r222(result),
        "label": "real PostgreSQL/PostgREST/Valkey/ClickHouse/S3-compatible services, the merged "
                 "T2I/T2F/T3/G4F/L2/J2 code and a controlled protocol engine; not Marlin quality, "
                 "not GPU capacity, not hosted behaviour",
        "judge": JUDGE_LABEL,
        "pins": pins(harness), "namespace": harness.NAMESPACE, "project": harness.PROJECT,
        "ports": harness.PORTS,
        "started": started.isoformat(timespec="seconds"),
        "seconds": round(time.monotonic() - clock, 1),
        "stack": {"usable": usable, "why_not": why or None,
                  "stages": [{k: s[k] for k in ("stage", "status", "seconds")} for s in report.stages]},
        "scenarios": [{"id": sid, **SCENARIOS[sid], **entry, "reproduce": reproduce(sid),
                       "scope": {lane: OUT_OF_SCOPE.get(lane, "local (in scope)")
                                 for lane in SCENARIOS[sid]["lanes"]}}
                      for sid, entry in result.items()],
        "lock": {"path": str(LOCK), "held": held}, "runs": runs,
        "evidence": {"junit": str(out / "scenarios.xml"), "log": str(out / "scenarios.log"),
                     "box_logs": str(out / "cases")},
        "reproduce": reproduce(),
    }
    (out / "verdict.json").write_text(json.dumps(payload, indent=2, default=str))
    for entry in payload["scenarios"]:
        print(f"{entry['status']:>8}  {entry['id']}  {entry['title']}")
    print(f"cells {payload['cells']}\nr222 {payload['r222']}\ngate {verdict} -> {out / 'verdict.json'}")
    return EXIT[verdict]


if __name__ == "__main__":
    raise SystemExit(main())
