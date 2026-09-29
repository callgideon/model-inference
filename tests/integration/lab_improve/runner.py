#!/usr/bin/env python3
"""E7L: the LAB-IMPROVE integration gate runner - two authorized improvement iterations, end
to end, on real services.

    apps/infrx-api/.venv/bin/python tests/integration/lab_improve/runner.py \\
        [--out DIR] [--keep] [--reuse] [--only i01,i04] [-k EXPR]

1. **stack**: E2's services in namespace `e7l` (tasklocal block 57300-57399, PostgreSQL 57332;
   run.py's own preflight/services/migrate stages, every migration incl. the Lab's 0027-0043).
   The scenarios take a database of their own on it (`lab_world`: every migration, the test
   clock, D7's seed), E2's MinIO through `S3ObjectStore`, and host processes inside the block:
   the synthetic serving endpoints (`ENDPOINT_PORTS`), J2's local teacher fake and P3's
   automatic-connector protocol server (`SERVICE_PORTS`). `--reuse` keeps a stack a previous
   `--keep` left. A stack that cannot provision makes every scenario BLOCKED.
2. **scenarios**: pytest over `scenarios_*.py` (never collected by `pytest tests/integration`),
   JUnit per case, mapped to the matrix below.
3. **verdict**: `<out>/verdict.json` - per scenario PASS / FAIL / BLOCKED / INVALID / NOT RUN,
   per manifest test id the worst of its scenarios, pinned SHAs (base, head, image digests)
   and raw evidence (JUnit, pytest log, each case's artifacts and hashes). The gate is the
   worst of all (FAIL > INVALID > BLOCKED > NOT RUN > PASS); exit 0 / 1 / 3 / 3 / 4, as E2C's
   gates.py and E3L's/E6L's runners.
4. teardown of exactly what step 1 created, unless `--keep`.

A skip is never a pass: `NOT RUN[<lanes>]` names the unmerged lanes and the rerun command,
`INVALID[...]` a case that could not establish its premise, `BLOCKED[...]` a missing stack. A
scenario missing a required case is NOT RUN.

Label: orchestration over owned synthetic fixtures, a deterministic evaluator, synthetic
serving endpoints with declared wins and regressions, the local teacher fake and the
manual/protocol training connectors - no real-model improvement claim (P-07), no live teacher
(P-10) or managed training (P-11) evidence, not GPU capacity, not hosted behaviour.
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


NAMESPACE = "e7l"
BASE = "b2906856"            # the LW6 base this runner was built on (coordinator dispatch)
PASS, FAIL, BLOCKED, INVALID, NOT_RUN = "PASS", "FAIL", "BLOCKED", "INVALID", "NOT RUN"
RANK = {PASS: 0, NOT_RUN: 1, BLOCKED: 2, INVALID: 3, FAIL: 4}
EXIT = {PASS: 0, FAIL: 1, BLOCKED: 3, NOT_RUN: 3, INVALID: 4}
RUNNER = "tests/integration/lab_improve/runner.py"
PY = "apps/infrx-api/.venv/bin/python"
#: The manifest's E7L test_ids (tasks.json), in its order; test_e7l_runner holds them equal.
TEST_IDS = ("DATA-LINEAGE", "PIPELINE-LINEAGE", "PIPELINE-BUDGET", "TRAIN-RECOVER")
#: The synthetic serving endpoints (host processes of the scenarios), inside the e7l block
#: and clear of E2's service ports (57300 s3, 57323/57390 ClickHouse, 57332 PG, 57379 Valkey,
#: 57380 fake vLLM): the deployed baseline, iteration 1's candidate, the seeded bad
#: checkpoint and iteration 2's candidate.
ENDPOINT_PORTS = {"base": 57360, "cand1": 57361, "bad": 57362, "cand2": 57363}
#: J2's local teacher fake (P2's egress) and P3's automatic-connector protocol server.
SERVICE_PORTS = {"teacher": 57364, "protocol": 57365}
#: An endpoint that finds its port taken (the block is inside Linux's ephemeral range) binds
#: the first free spare, still inside the block (E6L's measured TIME-WAIT case).
SPARE_PORTS = tuple(range(57366, 57379))

# The matrix (13:511-529 + the lane brief). `lanes`: what must merge before it can go green.
SCENARIOS = {
    "i01": {"title": "iteration 1 labels: human rows imported through P1 and a P2 teacher batch "
                     "over J2's egress to the local teacher fake, published synthetic; review "
                     "and correction; forged ground truth refused; the holdout never labelled "
                     "by the teacher nor exported", "test_ids": ["PIPELINE-LINEAGE"],
            "lanes": []},
    "i02": {"title": "iteration 1 training: the train-only label export in a manual bundle "
                     "that pins (never carries) the frozen holdout; checkpoints through P3/D7 "
                     "receipts evaluated by B1/B2 on that holdout; the seeded bad checkpoint "
                     "with the better training loss is rejected; no eligibility without the "
                     "holdout run; missing and late checkpoints rejected",
            "test_ids": ["TRAIN-RECOVER", "PIPELINE-LINEAGE"], "lanes": []},
    "i03": {"title": "iteration 2: the grantor's traces of candidate 1's serving selected "
                     "through N3 under its grant (D6F corrections kept apart), derived over the "
                     "frozen holdout, corrected, exported with both iterations' lineage, "
                     "trained, evaluated on the same holdout; budgets reconcile per unit",
            "test_ids": ["DATA-LINEAGE", "PIPELINE-LINEAGE", "PIPELINE-BUDGET"], "lanes": []},
    "i04": {"title": "a revocation mid-iteration: the annotation queue, the training submit and "
                     "the export reads stop; reconcile tombstones; nothing is recalled or "
                     "unlearned; a re-grant resurrects nothing", "test_ids": ["DATA-LINEAGE"],
            "lanes": []},
    "i05": {"title": "a duplicate teacher submit is one paid job; an ambiguous submit holds its "
                     "reservation, is never resubmitted and reconciles from the teacher's "
                     "record; the budget stops a batch before the chunk it cannot cover",
            "test_ids": ["PIPELINE-BUDGET"], "lanes": []},
    "i06": {"title": "the automatic training connector over TCP: a timeout after accept and a "
                     "lost poll never create a second paid job (R184); the reported cost "
                     "settles once; an ambiguous run ends only on an operator's written "
                     "confirmation, releasing its hold in the same move (WR-P3-R184/R192)",
            "test_ids": ["TRAIN-RECOVER", "PIPELINE-BUDGET"], "lanes": []},
    "i07": {"title": "the I6 annotation and training worker processes: a batch and a training "
                     "run driven by `python -m infrx.lab.workers <role>`, killed and restarted",
            "test_ids": ["PIPELINE-BUDGET", "TRAIN-RECOVER"],
            "lanes": ["composition-2", "WR-P2-4"]},
    "i08": {"title": "the provider UI (P4): labels, review, export, bundle, checkpoint and "
                     "eligibility over /lab/v1/pipelines", "test_ids": ["PIPELINE-LINEAGE"],
            "lanes": ["LAB_PIPELINES", "P3-evaluations"]},
    "i09": {"title": "the candidate proposed and approved through L3/L4 into a private or "
                     "allocated staging deployment, its traces pinned to that serving version",
            "test_ids": ["DATA-LINEAGE"], "lanes": ["staging-target"]},
}
REQUIRED = {
    "i01": ("test_i01_labels_keep_their_method_evidence_and_review",
            "test_i01_the_teacher_never_sees_the_holdout_and_its_labels_stay_synthetic"),
    "i02": ("test_i02_the_bundle_trains_on_the_train_export_and_pins_the_holdout",
            "test_i02_a_candidate_is_eligible_only_after_its_holdout_evaluation",
            "test_i02_an_evaluation_on_another_holdout_never_makes_a_candidate_eligible",
            "test_i02_the_bad_checkpoint_with_the_better_training_loss_is_rejected",
            "test_i02_missing_and_late_checkpoints_are_rejected_and_never_evaluated"),
    "i03": ("test_i03_permitted_traces_become_a_derived_version_over_the_frozen_holdout",
            "test_i03_the_second_iteration_improves_on_the_same_holdout_with_its_lineage",
            "test_i03_budgets_reconcile_per_unit_across_both_iterations"),
    "i04": ("test_i04_a_revocation_stops_the_queue_the_submit_and_the_export_reads",
            "test_i04_a_regrant_leaves_the_n3_gate_closed",
            "test_i04_a_regrant_resurrects_no_tombstoned_sample_into_training"),
    "i05": ("test_i05_a_duplicate_teacher_submit_is_one_paid_job",
            "test_i05_an_ambiguous_teacher_submit_is_held_and_never_resubmitted",
            "test_i05_the_budget_stops_the_batch_before_the_chunk_it_cannot_cover"),
    "i06": ("test_i06_a_timeout_after_accept_and_a_lost_poll_are_one_paid_job",
            "test_i06_an_ambiguous_run_ends_only_on_an_operators_written_confirmation"),
    "i07": ("test_i07_the_annotation_worker_process_resumes_a_batch_once",
            "test_i07_the_training_worker_process_never_resubmits"),
    "i08": ("test_i08_the_provider_ui_drives_labels_to_an_eligible_candidate",),
    "i09": ("test_i09_the_candidate_is_promoted_through_l3_l4_staging",),
}
HARNESS = re.compile(r"^(?:[\w.]*\.)?(?:HarnessError|OperationalError)\b|address already in use")
CASE = re.compile(r"test_(?P<sid>i\d\d)_")
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
    """E2's stages from run.py, in namespace e7l. (usable, why not)."""
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
    env = {**os.environ, "INFRX_E2_NAMESPACE": NAMESPACE, "INFRX_E7L_OUT": str(out),
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
    parser.add_argument("--keep", action="store_true", help="leave the e7l stack up")
    parser.add_argument("--reuse", action="store_true", help="use a stack --keep left")
    parser.add_argument("--pull", action="store_true", help="pull the pinned digests first")
    parser.add_argument("--only", default="", help="comma-separated scenario ids")
    parser.add_argument("-k", dest="keyword", default=None, help="pytest -k expression")
    args = parser.parse_args(argv)
    started, clock = datetime.now(timezone.utc), time.monotonic()
    out = args.out or Path(os.environ.get("TMPDIR", "/tmp")) / f"infrx-e7l-{started:%Y%m%dT%H%M%SZ}"
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
        "task": "E7L", "gate": "LAB-IMPROVE-LOCAL",
        "verdict": verdict, "exit": EXIT[verdict], "cells": cells(result),
        "label": "real PostgreSQL (every migration incl. 0027-0043) and S3-compatible MinIO, the "
                 "merged N1/N2/N3/P1/P2/P3/H1/B1/B2 code, D7 receipts, L2 grants and D6F "
                 "feedback; D8's ledgers and B3's P3 evaluation port as test-side stand-ins "
                 "(see lab_world); owned synthetic fixtures, a deterministic evaluator, "
                 "synthetic serving endpoints, the local teacher fake and the manual/protocol "
                 "training connectors; no real-model improvement claim (P-07), no live teacher "
                 "(P-10) or managed training (P-11) evidence, not GPU capacity, not hosted",
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
