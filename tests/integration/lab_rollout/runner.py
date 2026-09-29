#!/usr/bin/env python3
"""E8L: the LAB-ROLLOUT integration gate runner - shadow, canary and A/B on D9's release rows,
the R2 controller's guardrails, decisions and rollback, and R3's variant evidence, on real
services.

    apps/infrx-api/.venv/bin/python tests/integration/lab_rollout/runner.py \\
        [--out DIR] [--keep] [--reuse] [--only k03,k05] [-k EXPR]

1. **stack**: E2's services in namespace `e8l` (tasklocal block 57400-57499, PostgreSQL 57432;
   run.py's own preflight/services/migrate stages, every migration incl. the Lab's 0027-0043).
   The scenarios take a database of their own on it (`lab_world`: every migration, the test
   clock, D8's seeded registry, grants and candidate serving versions), and the synthetic
   endpoints on `ENDPOINT_PORTS` inside the block. `--reuse` keeps a stack a previous
   `--keep` left. A stack that cannot provision makes every scenario BLOCKED.
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

Label: real D9/D7/L3 rows on PostgreSQL, the merged R1/R2/R3/B2 code, G2's relay on its
contract fakes (the consumer's money), owned case records and synthetic endpoints - no
real-model quality claim (P-07), no GPU parity (NOT RUN), not hosted behaviour, no public
shadow/canary (P-12: everything local).
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


NAMESPACE = "e8l"
BASE = "993d481c"            # the base of the E8L lane this runner last measured (merge #50)
PASS, FAIL, BLOCKED, INVALID, NOT_RUN = "PASS", "FAIL", "BLOCKED", "INVALID", "NOT RUN"
RANK = {PASS: 0, NOT_RUN: 1, BLOCKED: 2, INVALID: 3, FAIL: 4}
EXIT = {PASS: 0, FAIL: 1, BLOCKED: 3, NOT_RUN: 3, INVALID: 4}
RUNNER = "tests/integration/lab_rollout/runner.py"
PY = "apps/infrx-api/.venv/bin/python"
#: The manifest's E8L test_ids (tasks.json), in its order; test_e8l_runner holds them equal.
TEST_IDS = ("ROLLOUT-PIN", "ROLLOUT-RECOVER", "OPT-PARITY")
#: The synthetic endpoints (host processes of the scenarios), inside the e8l block and clear
#: of E2's service ports (57400 s3, 57423/57490 ClickHouse, 57432 PG, 57479 Valkey, 57480).
ENDPOINT_PORTS = {"candidate": 57460, "engine": 57461}
#: A port in the block nothing listens on: the release store's outage (R181).
DEAD_PORT = 57499
#: An endpoint that finds its port taken (the block is in the ephemeral range) binds the
#: first free spare, still inside the block.
SPARE_PORTS = tuple(range(57462, 57470))

# The matrix (tasks.json E8L slices + the lane brief). `lanes`: what must merge first.
SCENARIOS = {
    "k01": {"title": "R1 off: a live 100% canary on D9 changes nothing - the composed gateway "
                     "keeps the relay's own accept, D9 is never asked, no assignment row",
            "test_ids": ["ROLLOUT-PIN"], "lanes": []},
    "k02": {"title": "shadow on D9 through G2's relay: the user's answer, job, hold and "
                     "settlement are today's; the candidate duplicate runs bounded on the "
                     "provider-funded endpoint and is dropped", "test_ids": ["ROLLOUT-PIN"],
            "lanes": []},
    "k03": {"title": "canary and A/B on D9: stable arms across retries, bounded by weight, a "
                     "raised weight keeps promised subjects (R179), pins, ineligible and "
                     "session subjects never routed (R180), a store outage is 503 (R181)",
            "test_ids": ["ROLLOUT-PIN"], "lanes": []},
    "k04": {"title": "a guardrail breach rolls back exactly once through D9's CAS under two "
                     "controllers; future admissions only; a controller killed before L3's "
                     "alias CAS converges on restart with no second decision",
            "test_ids": ["ROLLOUT-RECOVER"], "lanes": []},
    "k05": {"title": "non-inferiority and cost (B2): an accepting report is approved once; "
                     "inconclusive blocks promotion; stale metrics or missing evidence hold; "
                     "a slice regression under an aggregate "
                     "gain rolls back; overspend rolls back and units never mix",
            "test_ids": ["ROLLOUT-RECOVER"], "lanes": []},
    "k06": {"title": "a promoted candidate's alias rolls back through L3's CAS: the policy R1 "
                     "routes is the one R2 converges", "test_ids": ["ROLLOUT-RECOVER"],
            "lanes": []},
    "k07": {"title": "R3: a variant registered through W3's probe over HTTP, compared on a B2 "
                     "report, stored through D7; incompatible variants and unmeasured load "
                     "claims refused", "test_ids": ["OPT-PARITY"], "lanes": []},
    "k08": {"title": "R3 parity measured on an allocated supported GPU target",
            "test_ids": ["OPT-PARITY"], "lanes": ["P-08"]},
    "k09": {"title": "the I7 controller process killed and restarted mid-rollout: the real "
                     "emergency-rollback subcommand twice; the bare rollout role converges a "
                     "rollback killed before the alias CAS on its first pass",
            "test_ids": ["ROLLOUT-RECOVER"], "lanes": []},
    "k10": {"title": "the Lab releases page over the composed route: verdict, proposal, the "
                     "operator's decision through D9 (`rollout decide`) and emergency rollback",
            "test_ids": ["ROLLOUT-PIN"], "lanes": []},
}
REQUIRED = {
    "k01": ("test_k01_routing_off_serves_todays_request_over_a_live_release",
            "test_k01_a_candidate_ref_resolves_through_0045_and_matches_l3s_own_computation"),
    "k02": ("test_k02_a_shadow_changes_nothing_the_user_sees_or_pays",),
    "k03": ("test_k03_a_subject_keeps_its_arm_and_each_admission_is_one_d9_row",
            "test_k03_candidate_traffic_is_bounded_and_a_raised_weight_keeps_its_subjects",
            "test_k03_an_ab_split_serves_each_candidate_within_its_weight",
            "test_k03_pins_ineligible_and_session_subjects_are_never_routed",
            "test_k03_a_release_store_outage_is_a_503_never_the_baseline"),
    "k04": ("test_k04_a_breach_rolls_back_once_under_two_controllers_for_future_admissions",
            "test_k04_a_controller_killed_before_the_alias_cas_converges_on_restart"),
    "k05": ("test_k05_an_accepting_report_after_the_horizon_is_approved_once",
            "test_k05_an_inconclusive_report_blocks_promotion",
            "test_k05_missing_or_stale_evidence_never_expands",
            "test_k05_a_slice_regression_under_an_aggregate_gain_rolls_back",
            "test_k05_overspend_rolls_back_and_units_never_mix"),
    "k06": ("test_k06_an_emergency_rollback_moves_a_promoted_alias_back",
            "test_k06_the_worlds_alias_read_is_the_real_control_store_on_its_login"),
    "k07": ("test_k07_a_variant_is_probed_compared_and_stored",
            "test_k07_incompatible_variants_and_unmeasured_claims_are_refused"),
    "k08": ("test_k08_parity_on_an_allocated_gpu_target",),
    "k09": ("test_k09_the_controller_process_restarted_mid_rollout",
            "test_k09_the_rollout_pass_process_converges_a_rollback_killed_before_the_cas"),
    "k10": ("test_k10_the_releases_ui_over_the_real_route",
            "test_k10_the_release_listing_reads_d9s_rows_and_r2s_latest_verdict",
            "test_k10_the_composed_releases_route_proposes_and_the_operator_decides"),
}
#: Halves of a scenario that exist in the matrix but are not bound yet (R222: recorded in
#: verdict.json, never prose-only). NOT RUN always; they do not lower their parent's status.
SUB_CELLS = {
    "k10-ui-composed": {
        "parent": "k10", "lanes": ["WR-C6-LIVE"],
        "title": "the releases page's proposal/approval journey over pilot.lab_releases' own "
                 "records and proposals",
        "note": "k10 PASS covers the port half (rollout launch|decide over the composed ports) "
                "and the page failing closed over the gateway's composition (E2E-R01, a 503 "
                "naming WR-C5-PLAN); E2E-R02..R05 run over the journey adapters until R1/R2 "
                "have a composed read (WR-C6-LIVE; WR-LR5-1)"},
}
#: R222 as amended by R234: the lanes whose NOT RUN is outside local scope, with their ruled
#: reason class - k08's GPU target (P-08) and product WRs. k09's breach half is bound over
#: 0054's Live (WR-LIVE-K09, R244): k09's own case, no sub-cell. k10 is in local scope since
#: WR-R4-2 is composed (merge #50): its UI half PASSes through LAB-E2E (R238) or stays open;
#: the UI journey over the composed ports waits on WR-C6-LIVE (sub-cell k10-ui-composed).
#: The gate is re-run when a dependency lands and the cell must then PASS.
OUT_OF_SCOPE = {"P-08": "GPU (P-08 staging target)", "WR-C6-LIVE": "product WR: WR-C6-LIVE"}
HARNESS = re.compile(r"^(?:[\w.]*\.)?(?:HarnessError|OperationalError)\b|address already in use")
CASE = re.compile(r"test_(?P<sid>k\d\d)_")
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
    """R222/R235: accepted locally with nothing but PASS, and NOT RUN (sub-cells included)
    whose every reason is the scenario's own wait on out-of-local-scope lanes. `open` = what
    keeps it from acceptance."""
    def excused(sid: str, entry: dict) -> bool:
        lanes = entry.get("lanes") or SCENARIOS[sid]["lanes"]   # a recorded verdict's own
        if entry["status"] != NOT_RUN:        # R234: an in-scope FAIL is never excused
            return False
        return set(lanes) <= set(OUT_OF_SCOPE) and bool(entry["cases"]) and \
            all(f"NOT RUN[{','.join(lanes)}]" in reason for reason in entry["reasons"])
    still = {sid: entry["status"] for sid, entry in result.items()
             if entry["status"] != PASS and not excused(sid, entry)}
    still.update({cell["id"]: cell["status"] for cell in sub_cells(result)
                  if not set(cell["lanes"]) <= set(OUT_OF_SCOPE)})
    return {"accepted": not still, "open": still}


def reproduce(sid: str | None = None) -> str:
    return f"{PY} {RUNNER} --out <dir>" + (f" --only {sid}" if sid else "")


def sub_cells(result: dict) -> list[dict]:
    return [{"id": cid, **spec, "parent_status": result[spec["parent"]]["status"],
             "status": NOT_RUN, "reason": f"NOT RUN[{','.join(spec['lanes'])}]",
             "reproduce": reproduce(spec["parent"])} for cid, spec in SUB_CELLS.items()]


# ------------------------------------------------------------------ the run


def provision(report, reuse: bool, pull: bool) -> tuple[bool, str]:
    """E2's stages from run.py, in namespace e8l. (usable, why not)."""
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
    env = {**os.environ, "INFRX_E2_NAMESPACE": NAMESPACE, "INFRX_E8L_OUT": str(out),
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
    parser.add_argument("--keep", action="store_true", help="leave the e8l stack up")
    parser.add_argument("--reuse", action="store_true", help="use a stack --keep left")
    parser.add_argument("--pull", action="store_true", help="pull the pinned digests first")
    parser.add_argument("--only", default="", help="comma-separated scenario ids")
    parser.add_argument("-k", dest="keyword", default=None, help="pytest -k expression")
    args = parser.parse_args(argv)
    started, clock = datetime.now(timezone.utc), time.monotonic()
    out = args.out or Path(os.environ.get("TMPDIR", "/tmp")) / f"infrx-e8l-{started:%Y%m%dT%H%M%SZ}"
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
        "task": "E8L", "gate": "LAB-ROLLOUT-LOCAL",
        "verdict": verdict, "exit": EXIT[verdict], "cells": cells(result), "r222": r222(result),
        "label": "real PostgreSQL (every migration: D9 0033/0039/0043, D7, L3 0032), the "
                 "merged R1/R2/R3/B2 code, G2's relay on its contract fakes, owned case records "
                 "and synthetic endpoints; no real-model quality claim (P-07), no GPU parity, "
                 "no public shadow/canary (P-12), not hosted behaviour",
        "pins": pins(harness), "namespace": harness.NAMESPACE, "ports": harness.PORTS,
        "started": started.isoformat(timespec="seconds"),
        "seconds": round(time.monotonic() - clock, 1),
        "stack": {"usable": usable, "why_not": why or None,
                  "stages": [{k: s[k] for k in ("stage", "status", "seconds")} for s in report.stages]},
        "scenarios": [{"id": sid, **SCENARIOS[sid], **entry, "reproduce": reproduce(sid),
                       "scope": {lane: OUT_OF_SCOPE.get(lane, "local (in scope)")
                                 for lane in SCENARIOS[sid]["lanes"]}}
                      for sid, entry in result.items()],
        "sub_cells": sub_cells(result),
        "lock": {"path": str(LOCK), "held": held}, "runs": runs,
        "evidence": {"junit": str(out / "scenarios.xml"), "log": str(out / "scenarios.log"),
                     "case_artifacts": str(out / "cases")},
        "reproduce": reproduce(),
    }
    (out / "verdict.json").write_text(json.dumps(payload, indent=2, default=str))
    for entry in payload["scenarios"]:
        print(f"{entry['status']:>8}  {entry['id']}  {entry['title']}")
    print(f"cells {payload['cells']}\nr222 {payload['r222']}\ngate {verdict} -> "
          f"{out / 'verdict.json'}")
    return EXIT[verdict]


if __name__ == "__main__":
    raise SystemExit(main())
