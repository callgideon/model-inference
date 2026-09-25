#!/usr/bin/env python3
"""E3C: the BACKEND-LOCAL gate runner - the corrective scenario matrix on real services.

    apps/infrx-api/.venv/bin/python tests/integration/backend/e3c/runner.py \\
        [--out DIR] [--keep] [--reuse] [--only s03,s04] [-k EXPR] [--control NC=TREE]

1. **stack**: E2's services in namespace `e3c` (PostgreSQL, Valkey, S3-compatible store;
   run.py's own `preflight`/`services`/`migrate` stages), PostgREST per scenario clone
   (`stack.journey_postgrest`), E2's controlled protocol engine. `--reuse` keeps a stack a
   previous `--keep` left. A stage that cannot provision makes EVERY scenario BLOCKED.
2. **scenarios**: pytest over `scenarios_*.py` (never collected by `pytest tests/integration`:
   they are named outside `test_*`), JUnit per case, mapped to the matrix below.
3. **verdict**: `<out>/verdict.json` - PASS / FAIL / BLOCKED / INVALID / NOT RUN per scenario
   and per negative control, reproduction commands, raw evidence refs (the JUnit file, the
   pytest log, each case's box logs). The gate is the WORST of them, E2C's ranking:
   FAIL > INVALID > BLOCKED > NOT RUN > PASS; exit 0 / 1 / 3 / 3 / 4 as E2C's gates.py.
4. teardown of exactly what step 1 created, unless `--keep`.

A skip is never a pass: `BLOCKED[<lanes>]` names the lanes whose merge it waits for,
`INVALID[...]` a case that could not establish its premise, any other skip is NOT RUN. A
scenario with no case in the report is NOT RUN. A negative control counts only when its
scenario passed in the same run (otherwise the control it removes is absent: NOT RUN).

Label: orchestration with a controlled engine - not Marlin quality, not GPU capacity.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import resource
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAMESPACE = "e3c"
PASS, FAIL, BLOCKED, INVALID, NOT_RUN = "PASS", "FAIL", "BLOCKED", "INVALID", "NOT RUN"
RANK = {PASS: 0, NOT_RUN: 1, BLOCKED: 2, INVALID: 3, FAIL: 4}
EXIT = {PASS: 0, FAIL: 1, BLOCKED: 3, NOT_RUN: 3, INVALID: 4}
RUNNER = "tests/integration/backend/e3c/runner.py"
PY = "apps/infrx-api/.venv/bin/python"

# The matrix (03 §E3C). `lanes`: whose merge it needs to go green; `seam`: the RV finding it
# regresses (a seam scenario must be red on a tree without its fix).
SCENARIOS = {
    "s01": {"title": "verified identity -> CLI grant -> CLI key -> text/video sync/SSE/async -> "
                     "result -> revoke", "row": 1,
            "test_ids": ["BACKEND-JOURNEY", "CREDIT-CUTOVER"], "lanes": ["G8", "E1C"],
            "seam": None},
    "s02": {"title": "two gateways, two tenants, repeated callback/idempotency/finalize",
            "row": 2, "test_ids": ["BACKEND-JOURNEY"], "lanes": [], "seam": None},
    "s03": {"title": "upload create/PUT/finalize/resolve across gateway processes and restarts",
            "row": 3, "test_ids": ["UPLOAD-RESTART"], "lanes": ["D10", "M5", "E1C"],
            "seam": "RV-02"},
    "s04": {"title": "empty manifest and late rejection: no execution before durable eligibility",
            "row": 4, "test_ids": ["ADMISSION-READY"], "lanes": ["F2C", "D10", "W5", "G7"],
            "seam": "RV-05"},
    "s05": {"title": "crash at upload/admission/readiness/attachment/prep/outbox/claim/output/"
                     "settle", "row": 3,
            "test_ids": ["UPLOAD-RESTART", "ADMISSION-READY", "BACKEND-JOURNEY"],
            "lanes": ["D10", "M5", "W5", "G7"], "seam": "RV-02/RV-05"},
    "s06": {"title": "two collectors with live jobs; expiry + policy change + replay; scrub "
                     "content, keep metadata", "row": 5,
            "test_ids": ["RETENTION-DURABLE", "RESULT-EXPIRY"], "lanes": ["D10", "M6", "I8"],
            "seam": "RV-03"},
    "s07": {"title": "persisted result expiry on every read across a policy change and restart",
            "row": 5, "test_ids": ["RESULT-EXPIRY"], "lanes": ["F2C", "D10", "G7"],
            "seam": "RV-11"},
    "s08": {"title": "DB / object store / Valkey unavailable; process replacement", "row": 6,
            "test_ids": ["BACKEND-JOURNEY"], "lanes": ["I8"], "seam": None},
    "s09": {"title": "CREDIT enablement while historical USD jobs exist", "row": 7,
            "test_ids": ["CREDIT-CUTOVER"], "lanes": ["D10", "G8"], "seam": None},
    "s10": {"title": "runtime DB role, Supabase browser role, operator role denials", "row": 8,
            "test_ids": ["CREDIT-CUTOVER"], "lanes": ["D10", "I8"], "seam": "RV-09"},
    "s11": {"title": "reconcile under active settlement / cancel / collector", "row": 9,
            "test_ids": ["CREDIT-CUTOVER", "RETENTION-DURABLE"], "lanes": ["D10", "G8", "M6"],
            "seam": None},
    "s13": {"title": "discovery publishes only what admission serves (coordinator update 1)",
            "row": None, "test_ids": ["CATALOG-TRUTH"], "lanes": ["F2C", "G7"],
            "seam": "RV-01"},
    "s12": {"title": "the verdict itself: missing service BLOCKED, skip never PASS, broken "
                     "seam FAIL", "row": None, "test_ids": ["VERIFY-REPRO"], "lanes": ["E2C"],
            "seam": None},
}

# Negative controls: one per corrective oracle. `bypass` runs in this run (world.BYPASSES,
# a DB defect or a process monkeypatch at the seam); `revert` needs the lane's fix commits
# reverted in a scratch tree (`--control NC=TREE`), so it is NOT RUN until that is done.
CONTROLS = {
    "nc-journey-revoke": {"oracle": "BACKEND-JOURNEY", "scenario": "s01",
                          "case": "test_s01_cli_identity",
                          "mechanism": "bypass revoke-ignored (gateway)"},
    "nc-journey-tenant": {"oracle": "BACKEND-JOURNEY", "scenario": "s02",
                          "case": "test_s02_two_gateways",
                          "mechanism": "bypass tenant-blind (gateway B)"},
    "nc-upload-restart": {"oracle": "UPLOAD-RESTART", "scenario": "s03",
                          "case": "test_s03_create_put_complete",
                          "mechanism": "bypass upload-local (both gateways)"},
    "nc-admission-ready": {"oracle": "ADMISSION-READY", "scenario": "s04",
                           "mechanism": "revert the F2C/D10/W5/G7 readiness-barrier commits",
                           "revert": True},
    "nc-retention-durable": {"oracle": "RETENTION-DURABLE", "scenario": "s06",
                             "mechanism": "revert the D10/M6 durable-liveness commits",
                             "revert": True},
    "nc-result-expiry": {"oracle": "RESULT-EXPIRY", "scenario": "s07",
                         "case": "test_s07_one_persisted_expiry",
                         "mechanism": "bypass expiry-recompute (gateway)"},
    "nc-roles-browser": {"oracle": "CREDIT-CUTOVER", "scenario": "s10",
                         "case": "test_s10_the_browser_roles",
                         "mechanism": "DB defect: INSERT on public.credit_ledger granted to "
                                      "authenticated"},
    "nc-credit-cutover": {"oracle": "CREDIT-CUTOVER", "scenario": "s09",
                          "case": "test_s09_concurrent_signup_callbacks",
                          "mechanism": "DB defect: signup grant uniqueness dropped (E3B db09)"},
    "nc-verify-repro": {"oracle": "VERIFY-REPRO", "scenario": "s12",
                        "mechanism": "classify(): a skipped / missing required case"},
}
# The required cases (0-MUT-2): a scenario missing any of these - deselected by `-k`/`--only`
# or deleted - is NOT RUN, never PASS over the cases that happen to be there. s12 checks the
# manifest equals what the modules define (a deletion fails there too).
REQUIRED = {
    "s01": ("test_s01_cli_identity_grant_key_modes_result_revoke",
            "test_s01_the_external_dataset_client_resumes_uploads_across_a_gateway_restart"),
    "s02": ("test_s02_two_gateways_two_tenants_one_logical_request",),
    "s03": ("test_s03_create_put_complete_resolve_each_on_another_gateway",
            *(f"test_s03_a_gateway_sigkill_after_each_step_loses_nothing[{p}]"
              for p in ("create", "put", "complete")),
            "test_s03_wrong_owner_and_wrong_digest_are_refused_on_every_process"),
    "s04": ("test_s04_nothing_executes_while_the_acceptance_is_incomplete[text]",
            "test_s04_nothing_executes_while_the_acceptance_is_incomplete[video]",
            "test_s04_late_rejection_refuses_before_any_execution",
            "test_s04_a_permanent_preparation_refusal_ends_the_job_on_its_first_refusal"),
    "s05": (*(f"test_s05_a_gateway_crash_at_each_step_recovers_once[{p}]"
              for p in ("upload", "admission", "readiness", "attachment", "outbox")),
            *(f"test_s05_a_worker_crash_at_each_step_recovers_once[{p}]"
              for p in ("prep", "claim", "output", "settle"))),
    "s06": ("test_s06_two_fresh_collectors_never_delete_a_live_jobs_media",
            "test_s06_a_collector_that_cannot_reach_the_database_deletes_nothing",
            "test_s06_past_expiry_content_is_scrubbed_and_metadata_kept"),
    "s07": ("test_s07_one_persisted_expiry_governs_every_read_across_a_policy_change",),
    "s08": ("test_s08_an_unavailable_store_is_a_bounded_refusal_then_one_job[postgres]",
            "test_s08_an_unavailable_store_is_a_bounded_refusal_then_one_job[s3]",
            "test_s08_a_lost_valkey_index_is_rebuilt_and_loses_no_accepted_job"),
    "s09": ("test_s09_concurrent_signup_callbacks_and_grants_grant_exactly_once",
            "test_s09_a_historical_usd_job_keeps_its_units_while_credit_serves",
            "test_s09_the_credit_transition_is_a_reported_dry_run_first",
            "test_s09_a_transition_meeting_a_parked_admission_refuses_within_its_bound"),
    "s10": ("test_s10_the_runtime_login_cannot_become_an_owner_or_rewrite_money",
            "test_s10_the_browser_roles_reach_nothing_outside_the_console_surface",
            "test_s10_operator_and_consumer_credentials_stay_in_their_lane"),
    "s11": ("test_s11_reconcile_of_a_live_job_is_typed_and_moves_no_money",
            "test_s11_cancel_racing_completion_and_reconcile_ends_once",
            "test_s11_reconcile_never_recreates_scrubbed_content"),
    "s13": ("test_s13_discovery_claims_nothing_serving_contradicts",
            "test_s13_discovery_matches_the_running_serving_profile"),
    "s12": ("test_s12_a_missing_or_skipped_case_is_never_a_pass",
            "test_s12_the_gate_is_the_worst_status_and_exits_as_e2c_does",
            "test_s12_a_control_that_is_not_detected_fails_the_gate",
            "test_s12_a_control_counts_over_the_case_it_guards_not_the_whole_scenario",
            "test_s12_the_matrix_covers_the_brief_and_the_task",
            "test_s12_scenario_modules_are_not_collected_by_the_default_suites",
            "test_s12_every_fault_point_and_bypass_names_code_that_exists",
            "test_s12_the_namespace_is_the_reserved_block",
            "test_s12_blocked_must_name_known_lanes[lanes0]",
            "test_s12_blocked_must_name_known_lanes[lanes1]",
            "test_s12_no_stack_blocks_every_scenario",
            "test_s12_a_scenario_missing_a_required_case_is_not_run",
            "test_s12_the_manifest_is_exactly_what_the_scenario_modules_define",
            *(f"test_s12_a_revert_control_passes_only_when_the_reverted_tree_is_red[{r}-{e}]"
              for r, e in (("FAIL", "PASS"), ("PASS", "FAIL"), ("BLOCKED", "BLOCKED"),
                           ("NOT RUN", "NOT RUN"), ("INVALID", "INVALID"))),
            *(f"test_s12_a_harness_error_is_invalid_not_a_product_fail[{m}-{e}]" for m, e in (
                ("harness.HarnessError: docker compose up failed", "INVALID"),
                ('psycopg.OperationalError: database infrx_e3c_1_2 does not exist',
                 "INVALID"),
                ("RuntimeError: [Errno 98] address already in use", "INVALID"),
                ("AssertionError: the gateway never reached its fault point in 60s",
                 "INVALID"),
                ("AssertionError: executed before durable eligibility", "FAIL"))),
            "test_s12_a_held_namespace_lock_blocks_the_run_and_touches_nothing",
            "test_s12_a_fake_only_box_is_invalid_before_any_process_starts",
            "test_s12_the_readiness_oracle_and_admission_point_follow_d10s_port",
            "test_s12_a_barrier_holds_on_whichever_candidate_the_process_calls",
            "test_s12_a_collector_that_cannot_run_is_blocked_not_passed",
            "test_s12_the_dedicated_runtime_login_is_a_real_box_database",
            "test_s12_a_revert_control_tree_claims_this_checkouts_stack",
            *(f"test_s12_every_bypass_installs_on_this_tree[{n}]" for n in (
                "upload-local", "expiry-recompute", "revoke-ignored", "tenant-blind"))),
}
# 2-ACC-2: infrastructure that broke under a case (never a product gap): INVALID[harness].
HARNESS = re.compile(r"^(?:[\w.]*\.)?(?:HarnessError|OperationalError|BypassTargetMissing)\b"
                     r"|address already in use|never reached its fault point")
# 0-MUT-1: one runner per namespace on this host (a second one's preflight would tear the
# live stack down). Host-wide, not $TMPDIR: two sessions may have different TMPDIRs.
LOCK = Path("/tmp") / f"infrx-{NAMESPACE}.runner.lock"
CASE = re.compile(r"test_(?:nc_(?P<nc>[a-z0-9_]+?)__)?(?P<sid>s\d\d)")
MARK = re.compile(r"\b(BLOCKED|INVALID)\[([^\]]*)\]")


def worst(statuses) -> str:
    return max(statuses, key=RANK.__getitem__, default=NOT_RUN)


def case_status(case) -> tuple[str, str]:
    """(status, reason) of one JUnit testcase."""
    for tag in ("failure", "error"):
        node = case.find(tag)
        if node is not None:
            message = (node.get("message") or node.text or "")[:400]
            if HARNESS.search(message):
                return INVALID, "INVALID[harness] " + message
            return FAIL, message
    node = case.find("skipped")
    if node is None:
        return PASS, ""
    message = (node.get("message") or "") + " " + (node.text or "")
    if node.get("type") == "pytest.xfail":
        return NOT_RUN, "xfail is not a pass here: " + message.strip()[:300]
    mark = MARK.search(message)
    return ({"BLOCKED": BLOCKED, "INVALID": INVALID}[mark.group(1)] if mark else NOT_RUN,
            message.strip()[:400])


def classify(junit_xml: str, only: set[str] | None = None,
             required: dict | None = None) -> dict:
    """Per scenario and per control: status, cases and reasons, from pytest's JUnit XML.
    `required` (default REQUIRED): cases whose absence makes their scenario NOT RUN."""
    required = REQUIRED if required is None else required
    scenarios = {sid: {"status": NOT_RUN, "cases": {}, "reasons": []} for sid in SCENARIOS}
    controls = {nc: {"status": NOT_RUN, "cases": {}, "reasons": []} for nc in CONTROLS}
    for case in ET.fromstring(junit_xml).iter("testcase"):
        name = case.get("name", "")
        match = CASE.search(name)
        if not match or match.group("sid") not in SCENARIOS:
            continue
        status, reason = case_status(case)
        properties = {prop.get("name"): prop.get("value") for prop in case.iter("property")}
        nc = match.group("nc")
        entry = controls.get(f"nc-{nc.replace('_', '-')}") if nc else scenarios[match["sid"]]
        if entry is None:
            continue
        entry["cases"][name] = status
        if properties:
            entry.setdefault("properties", {})[name] = properties
        if reason:
            entry["reasons"].append(f"{name}: {reason}")
    for sid, entry in scenarios.items():
        if only and sid not in only:
            entry["reasons"].append("not selected (--only)")
        entry["status"] = worst(entry["cases"].values()) if entry["cases"] else NOT_RUN
        absent = [name for name in required.get(sid, ()) if name not in entry["cases"]]
        if absent and entry["cases"]:
            entry["status"] = worst([entry["status"], NOT_RUN])
            entry["reasons"].append(f"required case absent (deselected or deleted): {absent}")
    for nc, entry in controls.items():
        control = CONTROLS[nc]
        scenario = scenarios[control["scenario"]]
        # The plain case(s) the control removes a check from; absent a `case`, the scenario.
        guarded = worst(status for name, status in scenario["cases"].items()
                        if name.startswith(control["case"])) if control.get("case") \
            else scenario["status"]
        detected = worst(entry["cases"].values()) if entry["cases"] else NOT_RUN
        if control.get("revert"):
            entry["status"] = NOT_RUN
            entry["reasons"].append("needs the lane's fix reverted: --control "
                                    f"{nc}=<scratch tree with the fix reverted>")
        elif guarded != PASS:
            # The control it removes is absent on this tree: nothing to detect yet.
            entry["status"] = NOT_RUN if detected in (PASS, FAIL) else detected
            entry["reasons"].append(f"{control.get('case', control['scenario'])} is {guarded}: "
                                    "the control is not present, so removing it proves nothing")
        else:
            entry["status"] = detected
    return {"scenarios": scenarios, "controls": controls}


def control_verdict(reverted: str) -> str:
    """A revert-type control: its scenario on the tree with the fix reverted must be red.
    FAIL there -> the control PASSES; PASS there -> it FAILS; anything else propagates."""
    return PASS if reverted == FAIL else FAIL if reverted == PASS else reverted


def blocked_all(result: dict, why: str) -> dict:
    """No usable stack: every scenario is BLOCKED (never PASS, never silently NOT RUN)."""
    for entry in result["scenarios"].values():
        entry["status"], entry["reasons"] = BLOCKED, [f"BLOCKED[E2C] stack: {why}"]
    return result


def gate(result: dict) -> str:
    return worst([entry["status"] for group in ("scenarios", "controls")
                  for entry in result[group].values()])


# ------------------------------------------------------------------ the run


def provision(report, reuse: bool, pull: bool) -> tuple[bool, str]:
    """E2's stages from run.py, in namespace e3c. (usable, why not)."""
    import run
    import world
    if reuse and world.stack.has_stack():
        return True, "reused a kept stack"
    if not run.preflight(report, want_services=True):
        return False, "preflight: " + json.dumps(report.stages[-1]["detail"], default=str)[:400]
    if not run.services(report, pull=pull):
        return False, "services: " + str(report.stages[-1]["detail"])[:400]
    if run.migrate(report, 20260924) is None:
        return False, "migrate: " + str(report.stages[-1]["detail"])[:400]
    return True, ""


def teardown(report) -> None:
    import run
    run.backend_teardown(report)
    run.teardown(report)


def scenario_files(tree: Path | None = None) -> list[str]:
    root = (tree / "tests/integration/backend/e3c") if tree else HERE
    return sorted(str(path) for path in root.glob("scenarios_*.py")) + \
        [str(root / "test_e3c_runner.py")]


def run_env(out: Path, tree: Path | None = None) -> dict:
    """The scenarios' environment. A scratch tree (a revert-type control) brings its package,
    its migrations and its harness copy, but must claim THIS checkout's stack: the copy's
    compose directory is another checkout's identity (B1's ownership label), so without
    `INFRX_E2_CHECKOUT` and the state file every owned container reads foreign and the
    control's scenario is skipped, not judged (the same seam `mutants.py` uses)."""
    import world
    env = {**os.environ, "INFRX_E2_NAMESPACE": NAMESPACE, "INFRX_E3C_OUT": str(out),
           "COLUMNS": "400"}
    if tree is not None:
        env.update(PYTHONPATH=str(tree / "apps/infrx-api"), INFRX_E2_REPO_ROOT=str(tree),
                   INFRX_E2_CHECKOUT=world.harness.working_dir(),
                   INFRX_E2_STATE_FILE=str(world.harness.STATE_FILE))
    return env


def pytest_run(out: Path, name: str, files: list[str], keyword: str | None,
               tree: Path | None = None) -> tuple[dict, str]:
    """pytest in its own session, the whole output to `<out>/<name>.log`, JUnit beside it."""
    import subprocess
    junit, log = out / f"{name}.xml", out / f"{name}.log"
    import world
    env = run_env(out, tree)
    argv = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rfEs",
            "-o", "junit_family=xunit1", f"--junitxml={junit}", *files,
            *(["-k", keyword] if keyword else [])]
    began = time.monotonic()
    with log.open("w") as sink:
        try:
            code = subprocess.run(argv, cwd=world.harness.REPO_ROOT, env=env, stdout=sink,
                                  stderr=subprocess.STDOUT, timeout=5400).returncode
        except subprocess.TimeoutExpired:
            code = 124
    text = log.read_text(errors="replace")
    summary = text.strip().splitlines()[-1] if text.strip() else ""
    return ({"argv": " ".join(argv), "exit": code, "seconds": round(time.monotonic() - began, 1),
             "summary": summary, "log": str(log)},
            junit.read_text() if junit.exists() else "")


def substitute_s3_image(image: str, out: Path) -> dict:
    """E2's compose file with ONLY the s3 service's image replaced, written to `out`, used by
    this process and every child (world.COMPOSE_ENV) under this checkout's ownership label."""
    source = HERE.parents[1] / "compose.yaml"
    lines, service, swapped = source.read_text().splitlines(keepends=True), None, None
    for index, line in enumerate(lines):
        head = re.match(r"^  ([a-z0-9_-]+):\s*$", line)
        service = head.group(1) if head else service
        if service == "s3" and re.match(r"^    image:", line):
            swapped, lines[index] = line.split("image:", 1)[1].strip(), f"    image: {image}\n"
    if swapped is None or "@sha256:" not in image:
        raise SystemExit("--s3-image needs a digest-pinned reference and E2's s3 service")
    copy = out / "compose.yaml"
    copy.write_text("".join(lines))
    os.environ["INFRX_E3C_COMPOSE_FILE"] = str(copy)
    os.environ["INFRX_E2_CHECKOUT"] = str(source.parent)
    return {"what": "S3 image substituted (WR-2)", "pinned": swapped, "used": image,
            "compose": str(copy)}


def reproduce(sid: str | None = None, keyword: str | None = None) -> str:
    base = f"{PY} {RUNNER} --out <dir>"
    if sid:
        base += f" --only {sid}"
    return base + (f" -k '{keyword}'" if keyword else "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=None, help="verdict directory")
    parser.add_argument("--keep", action="store_true", help="leave the e3c stack up")
    parser.add_argument("--reuse", action="store_true", help="use a stack --keep left")
    parser.add_argument("--pull", action="store_true", help="pull the pinned digests first")
    parser.add_argument("--only", default="", help="comma-separated scenario ids")
    parser.add_argument("-k", dest="keyword", default=None, help="pytest -k expression")
    parser.add_argument("--control", action="append", default=[], metavar="NC=TREE",
                        help="run a revert-type negative control from a scratch tree")
    parser.add_argument("--s3-image", default=None, metavar="REF@sha256:...",
                        help="substitute E2's S3 image (WR-2: the pinned quay digest answers "
                             "401); recorded in the verdict as a deviation")
    args = parser.parse_args(argv)
    started, clock = datetime.now(timezone.utc), time.monotonic()
    out = args.out or Path(os.environ.get("TMPDIR", "/tmp")) / \
        f"infrx-e3c-{started:%Y%m%dT%H%M%SZ}"
    out.mkdir(parents=True, exist_ok=True)
    # The lane's namespace, set before E2's harness is first imported (world, then run.py).
    os.environ["INFRX_E2_NAMESPACE"] = NAMESPACE
    deviations = []
    if args.s3_image:
        deviations.append(substitute_s3_image(args.s3_image, out))
    sys.path.insert(0, str(HERE))
    import world
    import run
    harness = world.harness
    only = {sid.strip() for sid in args.only.split(",") if sid.strip()}
    keyword = args.keyword or (" or ".join(sorted(only)) if only else None)
    report = run.Report()
    usable, why = False, ""
    runs: dict = {}
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
                blocked_all(result, why)
                for entry in result["scenarios"].values():
                    entry["reasons"] = [f"BLOCKED[E2C] another run holds namespace {NAMESPACE}"]
            else:
                usable, why = provision(report, args.reuse, args.pull)
            if usable:
                done, junit = pytest_run(out, "scenarios", scenario_files(), keyword)
                runs["scenarios"] = done
                result = classify(junit or "<testsuites/>", only)
                for spec in args.control:
                    nc, _, tree = spec.partition("=")
                    control = CONTROLS[nc]
                    done, junit = pytest_run(out, nc, scenario_files(Path(tree)),
                                             control["scenario"], Path(tree))
                    runs[nc] = done
                    scenario = classify(junit or "<testsuites/>")["scenarios"][
                        control["scenario"]]["status"]
                    result["controls"][nc].update(
                        status=control_verdict(scenario),
                        reasons=[f"{control['scenario']} on {tree} (fix reverted): {scenario}"])
            elif held:
                blocked_all(result, why)
    except run.Interrupted as stop:
        why = f"interrupted by signal {stop.signum}: unfinished scenarios are NOT RUN"
    finally:
        if usable and not args.keep:
            teardown(report)
        lock.close()                      # releases the flock, after teardown
    verdict = gate(result)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    payload = {
        "task": "E3C", "gate": "BACKEND-LOCAL", "verdict": verdict, "exit": EXIT[verdict],
        "label": "orchestration on real PostgreSQL/PostgREST/Valkey/S3-compatible services "
                 "with a controlled protocol engine (tests/integration/fake_vllm.py); not "
                 "Marlin quality, not GPU capacity, not hosted behaviour",
        "head": report.head, "head_end": run.git_head(), "namespace": harness.NAMESPACE, "project": harness.PROJECT,
        "ports": harness.PORTS, "started": started.isoformat(timespec="seconds"),
        "seconds": round(time.monotonic() - clock, 1), "stack": {
            "usable": usable, "why_not": why or None,
            "stages": [{k: s[k] for k in ("stage", "status", "seconds")} for s in report.stages]},
        "scenarios": [{"id": sid, **SCENARIOS[sid], **entry,
                       "reproduce": reproduce(sid)} for sid, entry in result["scenarios"].items()],
        "negative_controls": [{"id": nc, **CONTROLS[nc], **entry,
                               "reproduce": reproduce(CONTROLS[nc]["scenario"], nc.replace(
                                   "nc-", "nc_").replace("-", "_"))}
                              for nc, entry in result["controls"].items()],
        "lock": {"path": str(LOCK), "held": held},
        "runs": runs, "deviations": deviations,
        "evidence": {"junit": str(out / "scenarios.xml"), "log": str(out / "scenarios.log"),
                     "box_logs": str(out / "cases")},
        "resources": {"children_max_rss_kb": children.ru_maxrss,
                      "children_cpu_s": round(children.ru_utime + children.ru_stime, 1)},
        "reproduce": f"{PY} {RUNNER} --out <dir>",
    }
    (out / "verdict.json").write_text(json.dumps(payload, indent=2, default=str))
    for entry in payload["scenarios"]:
        print(f"{entry['status']:>8}  {entry['id']}  {entry['title']}")
    for entry in payload["negative_controls"]:
        print(f"{entry['status']:>8}  {entry['id']}  ({entry['mechanism']})")
    print(f"gate {verdict} -> {out / 'verdict.json'}")
    return EXIT[verdict]


if __name__ == "__main__":
    raise SystemExit(main())
