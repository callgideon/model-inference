#!/usr/bin/env python3
"""E6L: the LAB-EVALUATE integration gate runner - an imported benchmark to a candidate
decision, end to end, on real services.

    apps/infrx-api/.venv/bin/python tests/integration/lab_evaluate/runner.py \\
        [--out DIR] [--keep] [--reuse] [--only j01,j07] [-k EXPR]

1. **stack**: E2's services in namespace `e6l` (tasklocal block 57200-57299, PostgreSQL 57232;
   run.py's own preflight/services/migrate stages, every migration incl. the Lab's 0027-0030).
   The scenarios take a database of their own on it (`lab_world`: every migration, the test
   clock, D7's seeded grant and source), E2's MinIO through M1-L2's `S3ObjectStore`, and the
   synthetic dev endpoints on `ENDPOINT_PORTS` inside the block. `--reuse` keeps a stack a
   previous `--keep` left. A stack that cannot provision makes every scenario BLOCKED.
2. **scenarios**: pytest over `scenarios_*.py` (never collected by `pytest tests/integration`),
   JUnit per case, mapped to the matrix below.
3. **verdict**: `<out>/verdict.json` - per scenario PASS / FAIL / BLOCKED / INVALID / NOT RUN,
   per manifest test id the worst of its scenarios, pinned SHAs (base, head, image digests)
   and raw evidence (JUnit, pytest log, each case's artifacts and hashes). The gate is the
   worst of all (FAIL > INVALID > BLOCKED > NOT RUN > PASS); exit 0 / 1 / 3 / 3 / 4, as E2C's
   gates.py and E3C's/E3L's runners.
4. teardown of exactly what step 1 created, unless `--keep`.

A skip is never a pass: `NOT RUN[<lanes>]` names the unmerged lanes and the rerun command,
`INVALID[...]` a case that could not establish its premise, `BLOCKED[...]` a missing stack. A
scenario missing a required case is NOT RUN.

Label: orchestration over owned synthetic fixtures, a deterministic evaluator and synthetic
dev endpoints with known wins and regressions - no real-model quality claim (P-07), not GPU
capacity, not hosted (Vercel/Supabase) behaviour.
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


def _sibling(name: str):
    """Loaded under a name unique to this package's own directory: `lab_world` is also every
    sibling lab_*/'s module name (WR-E7L-5) - with two such packages in one process, a bare
    `import lab_world` resolves to whichever package's directory sorts first in sys.path, not
    to the importing file's own package."""
    import importlib.util
    key = f"{HERE.name}.{name}"
    cached = sys.modules.get(key)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(key, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


NAMESPACE = "e6l"
BASE = "9a48300c"            # the LW5 base this runner was built on (coordinator dispatch)
PASS, FAIL, BLOCKED, INVALID, NOT_RUN = "PASS", "FAIL", "BLOCKED", "INVALID", "NOT RUN"
RANK = {PASS: 0, NOT_RUN: 1, BLOCKED: 2, INVALID: 3, FAIL: 4}
EXIT = {PASS: 0, FAIL: 1, BLOCKED: 3, NOT_RUN: 3, INVALID: 4}
RUNNER = "tests/integration/lab_evaluate/runner.py"
PY = "apps/infrx-api/.venv/bin/python"
#: The manifest's E6L test_ids (tasks.json), in its order; test_e6l_runner holds them equal.
TEST_IDS = ("DATA-IMPORT", "DATA-SPLIT", "DATA-RIGHTS", "EVAL-DURABLE", "EVAL-REPRO",
            "EVAL-COMPARE", "CHECKPOINT-IDEM")
#: The synthetic dev endpoints (host processes of the scenarios), inside the e6l block and
#: clear of E2's service ports (57200 s3, 57223 ClickHouse, 57230 PostgREST, 57232 PG, ...).
ENDPOINT_PORTS = {"baseline": 57260, "regressing": 57261, "improving": 57262,
                  "baseline_v2": 57263, "missing": 57264, "tools": 57265, "video": 57266,
                  "checkpoint": 57267}
#: The block sits inside Linux's ephemeral range: a client socket of any process can hold an
#: endpoint's port for a while (measured: 57264 in TIME-WAIT towards e5l's PostgreSQL), so an
#: endpoint that finds its port taken binds the first free spare, still inside the block.
SPARE_PORTS = tuple(range(57268, 57279))

# The matrix (13:491-509 + the lane brief). `lanes`: what must merge before it can go green.
SCENARIOS = {
    "j01": {"title": "owned text/tool benchmark imported through N1 onto D7 + MinIO: bad rows "
                     "quarantined and refused until accepted; a replay is the same dataset, a "
                     "changed upload conflicts", "test_ids": ["DATA-IMPORT"], "lanes": []},
    "j02": {"title": "owned finite-video benchmark: clips in the import bundle, the 82 s cap, "
                     "missing and non-video clips refused", "test_ids": ["DATA-IMPORT"],
            "lanes": []},
    "j03": {"title": "a foreign dataset is refused at import, resolve and schedule; a revoked "
                     "grant stops derivation and scheduling", "test_ids": ["DATA-RIGHTS"],
            "lanes": []},
    "j04": {"title": "split traps: related sources placed whole and deterministically; a "
                     "leaking base refused; the frozen holdout admits no relative",
            "test_ids": ["DATA-SPLIT"], "lanes": []},
    "j05": {"title": "B1 on real D7 through HttpDevEndpoint: a worker killed mid-attempt, a "
                     "duplicate delivery, a cancel mid-run", "test_ids": ["EVAL-DURABLE"],
            "lanes": []},
    "j06": {"title": "two serving versions reproduce the frozen benchmark; the same run "
                     "payload is the same run", "test_ids": ["EVAL-REPRO"], "lanes": []},
    "j07": {"title": "compare -> decide: a required-slice regression rejects despite an "
                     "aggregate gain, a clean win is accepted, a missing output is counted, "
                     "tool cases are visible and not comparable",
            "test_ids": ["EVAL-COMPARE"], "lanes": []},
    "j08": {"title": "a checkpoint delivered twice is one receipt and one run; a crash before "
                     "the decision is one run on redelivery", "test_ids": ["CHECKPOINT-IDEM"],
            "lanes": []},
    "j09": {"title": "the I5 eval and checkpoint worker processes: a worker killed mid-run, the "
                     "outbox drained once", "test_ids": ["EVAL-DURABLE", "CHECKPOINT-IDEM"],
            "lanes": ["L3"]},
    "j10": {"title": "the provider UI: launch, progress, cancel, compare with slices and "
                     "uncertainty", "test_ids": ["EVAL-COMPARE"], "lanes": ["B4", "lab-api-2"]},
    "j11": {"title": "a finite-video case reaches the dev endpoint through H1/B1",
            "test_ids": ["EVAL-COMPARE"], "lanes": ["L3"]},
}
REQUIRED = {
    "j01": ("test_j01_a_benchmark_with_bad_rows_is_refused_until_its_rejects_are_accepted",
            "test_j01_a_replayed_upload_is_the_same_dataset_and_a_changed_one_conflicts"),
    "j02": ("test_j02_finite_video_imports_with_its_cap_and_bundle_checks",),
    "j03": ("test_j03_a_foreign_dataset_is_refused_at_import_resolve_and_schedule",
            "test_j03_a_revoked_grant_stops_derivation_and_scheduling"),
    "j04": ("test_j04_related_sources_are_placed_whole_and_deterministically",
            "test_j04_a_leaking_base_is_refused_and_the_frozen_holdout_admits_no_relative"),
    "j05": ("test_j05_a_worker_killed_mid_attempt_is_recovered_and_each_case_scored_once",
            "test_j05_duplicate_delivery_scores_each_case_once",
            "test_j05_a_cancel_mid_run_stops_spending"),
    "j06": ("test_j06_two_serving_versions_reproduce_the_frozen_benchmark",),
    "j07": ("test_j07_a_required_slice_regression_rejects_despite_an_aggregate_gain",
            "test_j07_a_clean_win_is_accepted_and_claimed_improved",
            "test_j07_a_missing_candidate_output_is_counted_not_dropped",
            "test_j07_an_unfinished_candidate_counts_its_missing_cases",
            "test_j07_tool_cases_are_recorded_and_not_comparable"),
    "j08": ("test_j08_a_checkpoint_delivered_twice_is_one_receipt_and_one_run",
            "test_j08_a_crash_before_the_decision_is_one_run_on_redelivery"),
    "j09": ("test_j09_the_eval_worker_process_killed_mid_run_loses_nothing",
            "test_j09_the_checkpoint_worker_drains_the_outbox_once"),
    "j10": ("test_j10_the_provider_ui_launches_compares_and_cancels",),
    "j11": ("test_j11_a_finite_video_case_reaches_the_dev_endpoint",),
}
HARNESS = re.compile(r"^(?:[\w.]*\.)?(?:HarnessError|OperationalError)\b|address already in use")
CASE = re.compile(r"test_(?P<sid>j\d\d)_")
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
    """E2's stages from run.py, in namespace e6l. (usable, why not)."""
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
            # files rely on that (bare `import lab_world` after their own path.insert), so
            # this subprocess - run against the live checkout, never a scratch copy - needs
            # prepend mode back, overriding the root ini's addopts.
            "--import-mode=prepend",
            "-o", "junit_family=xunit1", f"--junitxml={junit}", *files,
            *(["-k", keyword] if keyword else [])]
    env = {**os.environ, "INFRX_E2_NAMESPACE": NAMESPACE, "INFRX_E6L_OUT": str(out),
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
    lab_world = _sibling("lab_world")
    return {"base": BASE, "head": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain")),
            "images": images, "fixtures_sha256": lab_world.fixture_hashes()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=None, help="verdict directory")
    parser.add_argument("--keep", action="store_true", help="leave the e6l stack up")
    parser.add_argument("--reuse", action="store_true", help="use a stack --keep left")
    parser.add_argument("--pull", action="store_true", help="pull the pinned digests first")
    parser.add_argument("--only", default="", help="comma-separated scenario ids")
    parser.add_argument("-k", dest="keyword", default=None, help="pytest -k expression")
    args = parser.parse_args(argv)
    started, clock = datetime.now(timezone.utc), time.monotonic()
    out = args.out or Path(os.environ.get("TMPDIR", "/tmp")) / f"infrx-e6l-{started:%Y%m%dT%H%M%SZ}"
    out.mkdir(parents=True, exist_ok=True)
    os.environ["INFRX_E2_NAMESPACE"] = NAMESPACE          # before E2's harness is imported
    sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parent / "backend")]
    lab_world = _sibling("lab_world")
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
        "task": "E6L", "gate": "LAB-EVALUATE-LOCAL",
        "verdict": verdict, "exit": EXIT[verdict], "cells": cells(result),
        "label": "real PostgreSQL (every migration, D7 0029) and S3-compatible MinIO, the merged "
                 "N1/N2/H1/B1/B2/B3 code, owned synthetic fixtures, a deterministic evaluator and "
                 "synthetic dev endpoints; no real-model quality claim (P-07), not GPU capacity, "
                 "not hosted behaviour",
        "pins": pins(harness), "namespace": harness.NAMESPACE, "ports": harness.PORTS,
        "started": started.isoformat(timespec="seconds"),
        "seconds": round(time.monotonic() - clock, 1),
        "stack": {"usable": usable, "why_not": why or None,
                  "stages": [{k: s[k] for k in ("stage", "status", "seconds")} for s in report.stages]},
        "scenarios": [{"id": sid, **SCENARIOS[sid], **entry, "reproduce": reproduce(sid)}
                      for sid, entry in result.items()],
        "lock": {"path": str(LOCK), "held": held}, "runs": runs,
        "evidence": {"junit": str(out / "scenarios.xml"), "log": str(out / "scenarios.log"),
                     "case_artifacts": str(out / "cases")},
        "reproduce": reproduce(),
    }
    (out / "verdict.json").write_text(json.dumps(payload, indent=2, default=str))
    for entry in payload["scenarios"]:
        print(f"{entry['status']:>8}  {entry['id']}  {entry['title']}")
    print(f"cells {payload['cells']}\ngate {verdict} -> {out / 'verdict.json'}")
    return EXIT[verdict]


if __name__ == "__main__":
    raise SystemExit(main())
