#!/usr/bin/env python3
"""E3L: the LAB-OPERATE integration gate runner - provider access, publication and rollback,
and the App with the Lab unavailable, on real services.

    apps/infrx-api/.venv/bin/python tests/integration/lab_operate/runner.py \\
        [--out DIR] [--keep] [--reuse] [--only l01,l07] [-k EXPR]

1. **stack**: E2's services in namespace `e3l` (tasklocal block 57000-57099, PostgreSQL 57032;
   run.py's own preflight/services/migrate stages, every migration incl. the Lab's), then the
   Lab web's production build (`pnpm build` in apps/lab). E3C's composed world supplies
   PostgREST per clone, the controlled engine and the gateway/worker processes. `--reuse` keeps
   a stack a previous `--keep` left. A stack that cannot provision makes every scenario BLOCKED.
2. **scenarios**: pytest over `scenarios_*.py` (never collected by `pytest tests/integration`),
   JUnit per case, mapped to the matrix below.
3. **verdict**: `<out>/verdict.json` - per scenario PASS / FAIL / BLOCKED / INVALID / NOT RUN,
   per manifest test id the worst of its scenarios, pinned SHAs (base, head, image digests,
   the Lab build id) and raw evidence (JUnit, pytest log, each case's box logs). The gate is
   the worst of all (FAIL > INVALID > BLOCKED > NOT RUN > PASS); exit 0 / 1 / 3 / 3 / 4, as
   E2C's gates.py and E3C's runner.
4. teardown of exactly what step 1 created, unless `--keep`.

A skip is never a pass: `NOT RUN[<lanes>]` names the unmerged lanes and the rerun command,
`INVALID[...]` a case that could not establish its premise, `BLOCKED[...]` a missing stack. A
scenario missing a required case is NOT RUN.

Label: orchestration with a controlled engine and a local Lab build - not Marlin quality, not
GPU capacity, not hosted (Vercel/Supabase) behaviour.
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
NAMESPACE = "e3l"
BASE = "eb0734d7"            # the LW3 base this runner was built on (coordinator dispatch)
PASS, FAIL, BLOCKED, INVALID, NOT_RUN = "PASS", "FAIL", "BLOCKED", "INVALID", "NOT RUN"
RANK = {PASS: 0, NOT_RUN: 1, BLOCKED: 2, INVALID: 3, FAIL: 4}
EXIT = {PASS: 0, FAIL: 1, BLOCKED: 3, NOT_RUN: 3, INVALID: 4}
RUNNER = "tests/integration/lab_operate/runner.py"
PY = "apps/infrx-api/.venv/bin/python"
#: The manifest's E3L test_ids (tasks.json), in its order; test_e3l_runner holds them equal.
TEST_IDS = ("LAB-ACCESS", "LAB-PUBLISH", "SPLIT-CONTRACT")

# The matrix (09 E3L + the lane brief). `lanes`: what must merge before it can go green.
SCENARIOS = {
    "l01": {"title": "two providers: own workspace only; every cross-provider operation refused; "
                     "revoked membership refused next call", "test_ids": ["LAB-ACCESS"],
            "lanes": []},
    "l02": {"title": "private dev revisions excluded from App discovery and admission",
            "test_ids": ["LAB-PUBLISH"], "lanes": ["L3", "L4"]},
    "l03": {"title": "registry validation: artifact, schema, runtime, digest, ownership",
            "test_ids": ["LAB-PUBLISH"], "lanes": ["L3"]},
    "l04": {"title": "production publication: operator approval, audit, rate snapshot",
            "test_ids": ["LAB-PUBLISH"], "lanes": ["L3", "L4"]},
    "l05": {"title": "App discovery and a metered call on the published revision",
            "test_ids": ["LAB-PUBLISH", "SPLIT-CONTRACT"], "lanes": ["L3", "L4"]},
    "l06": {"title": "rollback during a queued request keeps its serving and rate pins",
            "test_ids": ["LAB-PUBLISH"], "lanes": ["L3"]},
    "l07": {"title": "consumer keys reach no provider control and are no Lab session",
            "test_ids": ["LAB-ACCESS", "SPLIT-CONTRACT"], "lanes": []},
    "l08": {"title": "consumer data denied without a current, purpose-matching grant",
            "test_ids": ["LAB-ACCESS"], "lanes": []},
    "l09": {"title": "fault injection on the publish/rollback CAS",
            "test_ids": ["LAB-PUBLISH"], "lanes": ["L3"]},
    "l10": {"title": "control-service restart mid-operation", "test_ids": ["LAB-PUBLISH"],
            "lanes": ["L3"]},
    "l11": {"title": "App inference with the Lab down, crash-looping and rolled back",
            "test_ids": ["SPLIT-CONTRACT"], "lanes": []},
    "l12": {"title": "consumer keys refused by every control-service operation",
            "test_ids": ["LAB-ACCESS"], "lanes": ["L3"]},
}
REQUIRED = {
    "l01": ("test_l01_each_provider_session_sees_only_its_own_workspace",
            "test_l01_a_member_of_one_provider_is_refused_every_operation_on_the_other",
            "test_l01_a_revoked_membership_is_refused_on_its_next_call"),
    "l02": ("test_l02_the_seeded_private_dev_deployment_is_not_discoverable_or_admissible",
            "test_l02_a_provider_created_dev_revision_never_reaches_app_discovery"),
    "l03": ("test_l03_registry_validation_refuses_bad_artifacts_and_foreign_ownership",),
    "l04": ("test_l04_publication_needs_operator_approval_and_snapshots_the_rate",),
    "l05": ("test_l05_app_discovers_and_serves_the_published_revision",),
    "l06": ("test_l06_rollback_during_a_queued_request_keeps_its_serving_and_rate_pins",),
    "l07": ("test_l07_a_consumer_key_reaches_no_provider_control_on_the_gateway",
            "test_l07_a_consumer_key_is_no_lab_session",
            "test_l07_a_consumer_owner_has_no_provider_workspace"),
    "l08": ("test_l08_no_grant_no_content",
            "test_l08_a_grant_is_purpose_bound_and_its_revocation_denies_the_next_call",
            "test_l08_a_provider_session_reads_no_consumer_rows_directly"),
    "l09": ("test_l09_publish_and_rollback_cas_under_injected_faults",),
    "l10": ("test_l10_a_control_service_restart_mid_operation_loses_nothing",),
    "l11": ("test_l11_the_lab_down_mid_traffic_leaves_app_inference_serving",
            "test_l11_a_bad_lab_release_and_its_rollback_leave_every_accepted_job_finished_once"),
    "l12": ("test_l12_a_consumer_key_is_refused_by_every_control_operation",),
}
HARNESS = re.compile(r"^(?:[\w.]*\.)?(?:HarnessError|OperationalError)\b|address already in use")
CASE = re.compile(r"test_(?P<sid>l\d\d)_")
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


def reproduce(sid: str | None = None) -> str:
    return f"{PY} {RUNNER} --out <dir>" + (f" --only {sid}" if sid else "")


# ------------------------------------------------------------------ the run


def provision(report, reuse: bool, pull: bool) -> tuple[bool, str]:
    """E2's stages from run.py, in namespace e3l. (usable, why not)."""
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


def lab_build(out: Path) -> dict:
    """The Lab web's production build (the release l11 starts), logged beside the verdict."""
    log = out / "lab-build.log"
    began = time.monotonic()
    with log.open("w") as sink:
        code = subprocess.run(["pnpm", "build"], cwd=REPO / "apps" / "lab", stdout=sink,
                              stderr=subprocess.STDOUT, timeout=900,
                              env={**os.environ, "NEXT_TELEMETRY_DISABLED": "1"}).returncode
    build = REPO / "apps" / "lab" / ".next" / "BUILD_ID"
    return {"exit": code, "seconds": round(time.monotonic() - began, 1), "log": str(log),
            "build_id": build.read_text().strip() if code == 0 and build.exists() else None}


def pytest_run(out: Path, keyword: str | None) -> tuple[dict, str]:
    junit, log = out / "scenarios.xml", out / "scenarios.log"
    files = sorted(str(path) for path in HERE.glob("scenarios_*.py"))
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rfEs",
            "-o", "junit_family=xunit1", f"--junitxml={junit}", *files,
            *(["-k", keyword] if keyword else [])]
    env = {**os.environ, "INFRX_E2_NAMESPACE": NAMESPACE, "INFRX_E3L_OUT": str(out),
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
    return {"base": BASE, "head": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain")),
            "images": images}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=None, help="verdict directory")
    parser.add_argument("--keep", action="store_true", help="leave the e3l stack up")
    parser.add_argument("--reuse", action="store_true", help="use a stack --keep left")
    parser.add_argument("--pull", action="store_true", help="pull the pinned digests first")
    parser.add_argument("--only", default="", help="comma-separated scenario ids")
    parser.add_argument("-k", dest="keyword", default=None, help="pytest -k expression")
    args = parser.parse_args(argv)
    started, clock = datetime.now(timezone.utc), time.monotonic()
    out = args.out or Path(os.environ.get("TMPDIR", "/tmp")) / f"infrx-e3l-{started:%Y%m%dT%H%M%SZ}"
    out.mkdir(parents=True, exist_ok=True)
    os.environ["INFRX_E2_NAMESPACE"] = NAMESPACE          # before E2's harness is imported
    sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parent / "backend")]
    import lab_world
    import run
    harness = lab_world.harness
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
                runs["lab_build"] = lab_build(out)
                done, junit = pytest_run(out, keyword)
                runs["scenarios"] = done
                result = classify(junit or "<testsuites/>", only)
            else:
                blocked_all(result, why)
    except run.Interrupted as stop:
        why = f"interrupted by signal {stop.signum}: unfinished scenarios are NOT RUN"
    finally:
        if usable and not args.keep:
            run.backend_teardown(report)
            run.teardown(report)
        lock.close()
    verdict = gate(result)
    payload = {
        "task": "E3L", "gate": "LAB-OPERATE (local half; I2L staging needs P-08)",
        "verdict": verdict, "exit": EXIT[verdict], "cells": cells(result),
        "label": "real PostgreSQL/PostgREST/Valkey/S3-compatible services, the merged L1/L2 code, "
                 "a controlled protocol engine and a local Lab build; not Marlin quality, not GPU "
                 "capacity, not hosted behaviour",
        "pins": pins(harness), "namespace": harness.NAMESPACE, "ports": harness.PORTS,
        "started": started.isoformat(timespec="seconds"),
        "seconds": round(time.monotonic() - clock, 1),
        "stack": {"usable": usable, "why_not": why or None,
                  "stages": [{k: s[k] for k in ("stage", "status", "seconds")} for s in report.stages]},
        "scenarios": [{"id": sid, **SCENARIOS[sid], **entry, "reproduce": reproduce(sid)}
                      for sid, entry in result.items()],
        "lock": {"path": str(LOCK), "held": held}, "runs": runs,
        "evidence": {"junit": str(out / "scenarios.xml"), "log": str(out / "scenarios.log"),
                     "box_logs": str(out / "cases")},
        "reproduce": reproduce(),
    }
    (out / "verdict.json").write_text(json.dumps(payload, indent=2, default=str))
    for entry in payload["scenarios"]:
        print(f"{entry['status']:>8}  {entry['id']}  {entry['title']}")
    print(f"cells {payload['cells']}\ngate {verdict} -> {out / 'verdict.json'}")
    return EXIT[verdict]


if __name__ == "__main__":
    raise SystemExit(main())
