#!/usr/bin/env python3
"""R32/R83 for I2L-OBS and E5L: one single-edit defect per decision the lane claims, through
the shared runner (`apps/infrx-api/tests/contracts/mutants.py`, a private copy whose Python-only
compile check is relaxed for the unit/JSON/SQL targets, as track I's and E3L's).

* `MUTANTS` (layer 1, no stack): the I2L-OBS packaging (units, names, grants, egress, alarms,
  exporter) and the E5L runner's classification, cells, gate, exit codes and NOT RUN vocabulary.
* `STACK_MUTANTS`: the product decisions the running scenarios guard (T2I's shipper, T3's
  retention, T2F's outbox, L2's store, J2's submit, G4F's route) and two drill premises, killed
  on a kept e5l stack by the scenario cases, in a copy whose gateway and worker import the
  mutated package. Without a kept stack they skip visibly:

    apps/infrx-api/.venv/bin/python tests/integration/lab_observe/runner.py --keep --out <dir>
    INFRX_MUTANTS=all INFRX_E2_NAMESPACE=e5l apps/infrx-api/.venv/bin/python -m pytest -q \\
        -p no:cacheprovider tests/integration/lab_observe/test_mutants.py
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
API_DIR = REPO / "apps" / "infrx-api"


def _shared():
    path = API_DIR / "tests" / "contracts" / "mutants.py"
    spec = importlib.util.spec_from_file_location("e5l_shared_mutants", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


shared = _shared()
Mutant, Outcome, Result, Runner, _m = (shared.Mutant, shared.Outcome, shared.Result,
                                       shared.Runner, shared._m)
shared.compile = lambda source, filename, mode, *a, **k: (
    compile(source, filename, mode, *a, **k) if str(filename).endswith(".py") else None)

L = "tests/integration/lab_observe/"
R, W = L + "runner.py", L + "observe_world.py"
OBS, UNITS = "infra/lab/observe/", "apps/infrx-api/deploy/lab/observe/"
JUDGE_UNIT, GAUGES_UNIT = UNITS + "infrx-lab-judge.service", UNITS + "infrx-lab-trace-gauges.service"
TIMER, MANIFEST = UNITS + "infrx-lab-trace-gauges.timer", OBS + "observe.json"
LAYER1_FILES = (L + "test_e5l_runner.py", L + "test_i2l_obs.py")

# the I2L-OBS cases
OFF = "test_i2l_obs__every_observe_unit_is_off_until_its_role_env_file_exists"
COUPLED = "test_i2l_obs__no_app_unit_links_to_an_observe_unit_or_the_reverse"
BOUNDED = "test_i2l_obs__each_unit_is_bounded_least_privilege_and_its_own"
DRAINS = "test_i2l_obs__the_judge_worker_runs_its_role_and_drains"
TIMED = "test_i2l_obs__the_trace_gauges_run_on_a_timer_from_the_shipped_exporter"
NAMES = "test_i2l_obs__secret_names_only_and_every_name_a_unit_reads_is_declared"
GRANTS = "test_i2l_obs__storage_grants_are_object_prefixes_under_the_trace_root_never_a_bucket"
EGRESS = "test_i2l_obs__judge_egress_is_the_local_fake_and_a_zero_budget_until_p10"
ALARMS = "test_i2l_obs__the_alarms_are_t3s_rules_with_runbook_anchors_and_no_name_clash"
EXPORTER = "test_i2l_obs__the_exporter_writes_t3s_gauges_and_a_failure_is_up_0_never_silence"
UP0 = "test_i2l_obs__an_exporter_that_wrote_up_0_fires_an_alarm_through_the_evaluator"
PORTS = "test_i2l_obs__no_two_lab_worker_units_share_a_health_port"
PINNED = "test_i2l_obs__the_pinned_monitor_copy_holds_the_lab_files_and_an_old_pin_keeps_app_alerts"
WIRING = "research/plan/evidence/e/E5L-wiring/"
# the E5L runner cases
MATRIX = "test_e5l_the_matrix_carries_the_manifest_test_ids_and_the_brief_cases"
REQUIRED = "test_e5l_the_required_cases_are_exactly_what_the_scenario_modules_define"
LANES = "test_e5l_a_scenarios_declared_lanes_are_the_lanes_its_not_run_case_names"
UNBOUND = "test_e5l_every_unbound_case_is_not_run_without_touching_a_stack"
SKIPS = "test_e5l_a_skip_is_never_a_pass_and_names_its_kind"
MISSING = "test_e5l_a_scenario_missing_a_required_case_is_not_run"
HARNESS = "test_e5l_a_harness_error_is_invalid_not_a_product_fail"
GATE = "test_e5l_the_gate_and_the_cells_are_the_worst_status_and_exit_as_e2c_does"
NO_STACK = "test_e5l_no_stack_blocks_every_scenario"
NAMESPACE = "test_e5l_the_namespace_is_the_reserved_block_and_the_judge_fake_port_is_free_in_it"
RERUN = "test_e5l_a_not_run_case_names_its_lanes_and_the_exact_rerun"
DRY_RUN = "test_e5l_the_judge_is_labelled_a_dry_run_never_a_live_run"
PROJECT = "test_e5l_a_compose_project_override_moves_only_the_compose_names"

MUTANTS: tuple[Mutant, ...] = (
    # --- I2L-OBS packaging
    _m("judge_unit_runs_without_its_env_file", "a role is off until its env file exists",
       JUDGE_UNIT, "ConditionPathExists=/etc/infrx-lab/judge.env\n",
       "ConditionPathExists=/etc/infrx-lab/enabled\n", OFF),
    _m("gauges_timer_unconditional", "the timer is off until the env file exists", TIMER,
       "ConditionPathExists=/etc/infrx-lab/traces.env\n", "", OFF),
    _m("manifest_enabled", "the observe roles ship disabled", MANIFEST,
       '"enabled": false,', '"enabled": true,', OFF),
    _m("judge_ordered_after_the_worker", "no ordering or lifecycle link to an App unit",
       JUDGE_UNIT, "After=docker.service network-online.target",
       "After=docker.service network-online.target infrx-worker.service", COUPLED),
    _m("judge_full_cpu_weight", "a Lab unit yields CPU to inference", JUDGE_UNIT,
       "--cpus 0.5 --cpu-shares 256 ", "--cpus 0.5 ", BOUNDED),
    _m("judge_in_the_runtime_uid", "a Lab unit runs outside the runtime's uid", JUDGE_UNIT,
       "--user 10004:10000", "--user 10000:10000", BOUNDED),
    _m("gauges_writable_exporter_mount", "the exporter mounts its code read-only", GAUGES_UNIT,
       "infra/lab/observe:/observe:ro", "infra/lab/observe:/observe", BOUNDED, TIMED),
    _m("gauges_reads_the_gateway_env", "a Lab unit reads its own env file only", GAUGES_UNIT,
       "  --env-file /etc/infrx-lab/traces.env \\\n",
       "  --env-file /etc/marlin2b-gateway.env \\\n", BOUNDED),
    _m("judge_runs_another_role", "the judge unit runs the judge role", JUDGE_UNIT,
       "python -m infrx.lab.workers judge", "python -m infrx.lab.workers eval", DRAINS),
    _m("judge_stop_shorter_than_the_drain", "systemd waits longer than docker's drain",
       JUDGE_UNIT, "TimeoutStopSec=90", "TimeoutStopSec=30", DRAINS),
    _m("gauges_write_another_textfile", "the exporter writes the textfile the cycle reads",
       GAUGES_UNIT, "--out /m/lab-traces.prom", "--out /m/traces.prom", TIMED),
    _m("spool_dir_undeclared", "every name a unit reads is declared", MANIFEST,
       '      {"name": "TRACE_SPOOL_DIR", "exposure": "server", "purpose": "the worker\'s spool '
       'directory, mounted read-only for its size"},\n', "", NAMES),
    _m("judge_bucket_wide", "a grant is an object prefix, never a bucket", MANIFEST,
       '"prefix": "${OBJECT_PREFIX}trace/",\n     "actions": ["s3:GetObject"],',
       '"prefix": "${OBJECT_PREFIX}",\n     "actions": ["s3:GetObject"],', GRANTS),
    _m("judge_may_write", "the judge reads only", MANIFEST, '"actions": ["s3:GetObject"],',
       '"actions": ["s3:GetObject", "s3:PutObject"],', GRANTS),
    _m("judge_egress_to_another_host", "the judge reaches the local fake only", MANIFEST,
       '"hosts": ["127.0.0.1", "::1", "localhost"]',
       '"hosts": ["127.0.0.1", "::1", "localhost", "api.example.com"]', EGRESS),
    _m("judge_live_budget", "the live budget is 0 until P-10", MANIFEST,
       '"JUDGE_LIVE_BUDGET_USD": "0"', '"JUDGE_LIVE_BUDGET_USD": "5"', EGRESS),
    _m("alarm_threshold_drift", "the alarms are T3's rules exactly", OBS + "alerts.json",
       '"threshold": 86400,', '"threshold": 172800,', ALARMS),
    _m("alarm_without_its_runbook", "every alarm's runbook anchor exists", OBS + "alerts.json",
       "README.md#tracelosshigh", "README.md#loss", ALARMS),
    _m("exporter_silent_on_failure", "a failed exporter writes up 0", OBS + "trace_gauges.py",
       "        return write(out, None)", "        return 0", EXPORTER),
    _m("exporter_prints_the_error", "the exporter prints the error's type only",
       OBS + "trace_gauges.py", "{type(failure).__name__}", "{failure}", EXPORTER),
    _m("exporter_counts_every_file", "spool bytes are the sealed segments'",
       OBS + "trace_gauges.py", "for name in segment_names(spool_dir))",
       "for name in os.listdir(spool_dir))", EXPORTER),
    _m("exporter_up_always_1", "up is 0 when nothing was read", OBS + "trace_gauges.py",
       "{1 if values is not None else 0}", "1", EXPORTER),
    # --- the fix round (0-F3, 1-LO-INT-1, 0-F4 / 1-LO-SCOPE-1)
    _m("gauges_down_never_fires", "an exporter that wrote up 0 pages", OBS + "alerts.json",
       '"metric": "infrx_trace_gauges_up",\n      "op": "<",',
       '"metric": "infrx_trace_gauges_up",\n      "op": ">",', UP0),
    _m("judge_on_the_eval_port", "each Lab worker unit has its own health port", JUDGE_UNIT,
       "-e LAB_WORKER_HEALTH_PORT=8017", "-e LAB_WORKER_HEALTH_PORT=8012", PORTS, DRAINS),
    _m("pin_without_the_lab_files", "step 72 pins infra/lab/observe where the units read it",
       WIRING + "WR-OBS-5.diff", '+cp -r "$repo/infra/lab/observe" "$next/infra/lab/"',
       '+cp -r "$repo/infra/lab/observe" "$next/infra/lab/observe.new"', PINNED),
    _m("lab_rules_merged_unguarded", "a pin without the Lab rules keeps the App's rules",
       WIRING + "WR-OBS-2.diff",
       '+  [[ -f $repo/infra/lab/observe/alerts.json ]] && lab_rules=("$repo/infra/lab/observe/alerts.json")',
       '+  lab_rules=("$repo/infra/lab/observe/alerts.json")', PINNED),
    # --- the E5L runner
    _m("xfail_is_a_pass", "an xfail is never a pass", R,
       '        return NOT_RUN, "xfail is not a pass here: " + message[:300]',
       '        return PASS, ""', SKIPS),
    _m("not_run_is_a_pass", "a skip without a mark is NOT RUN", R,
       "[mark.group(1)] if mark else NOT_RUN,", "[mark.group(1)] if mark else PASS,", SKIPS),
    _m("required_cases_ignored", "a scenario missing a required case is NOT RUN", R,
       '        if absent and entry["cases"]:', "        if False:", MISSING),
    _m("harness_error_is_a_product_fail", "a broken harness is INVALID, never FAIL", R,
       "if HARNESS.search(message)", "if False and HARNESS.search(message)", HARNESS),
    _m("gate_is_the_best_status", "the gate is the WORST status", R,
       "    return max(statuses, key=RANK.__getitem__, default=NOT_RUN)",
       "    return min(statuses, key=RANK.__getitem__, default=NOT_RUN)", GATE),
    _m("a_cell_over_every_scenario", "a cell is judged on the scenarios carrying its test id",
       R, 'if tid in spec["test_ids"]) for tid in TEST_IDS}', ") for tid in TEST_IDS}", GATE),
    _m("not_run_exits_zero", "NOT RUN exits 3 (E2C), never 0", R,
       "EXIT = {PASS: 0, FAIL: 1, BLOCKED: 3, NOT_RUN: 3, INVALID: 4}",
       "EXIT = {PASS: 0, FAIL: 1, BLOCKED: 3, NOT_RUN: 0, INVALID: 4}", GATE),
    _m("no_stack_passes", "no stack blocks every scenario", R,
       'entry["status"], entry["reasons"] = BLOCKED, [f"BLOCKED[stack] {why}"]',
       'entry["status"], entry["reasons"] = PASS, [f"BLOCKED[stack] {why}"]', NO_STACK),
    _m("a_manifest_test_id_dropped", "the cells are the manifest's test ids", R,
       '"JUDGE-SCORES", "LAB-ACCESS", "CONSOLE-FLOWS")', '"JUDGE-SCORES", "LAB-ACCESS")',
       MATRIX),
    _m("a_required_case_renamed", "the required cases are the modules' cases", R,
       '    "o08": ("test_o08_a_timed_out_submit_is_quarantined_never_resent_and_reconciled",),',
       '    "o08": ("test_o08_a_timed_out_submit",),', REQUIRED),
    _m("a_lane_undeclared", "a scenario waiting on a lane declares it", R,
       '"lanes": ["LAB-E2E"]}', '"lanes": []}', LANES),
    _m("another_namespace", "e5l runs in its own reserved block", R,
       'NAMESPACE = "e5l"', 'NAMESPACE = "e3l"', NAMESPACE),
    _m("judge_fake_on_the_gateway_port", "the judge fake has a port of its own", W,
       "JUDGE_PORT = harness.PORT_RANGE.start + 65", "JUDGE_PORT = harness.PORT_RANGE.start + 40",
       NAMESPACE, DRY_RUN),
    _m("not_run_without_the_rerun", "a NOT RUN names the exact rerun", W,
       "rerun after the merge: {RERUN} --only {sid}", "rerun after the merge: {RERUN}", RERUN,
       UNBOUND),
    _m("unbound_case_runs", "a case waiting on a lane is never a pass", W,
       '    pytest.skip(f"NOT RUN[', '    return (f"NOT RUN[', RERUN, UNBOUND),
    _m("judge_label_claims_live", "dry-run evidence never claims a live run", R,
       'external provider (P-10 absent); never a live-judge run")',
       'external provider (P-10 absent)")', DRY_RUN),
    # --- lab-observe-2: the compose project override (INFRX_E5L_PROJECT)
    _m("project_override_ignored", "INFRX_E5L_PROJECT names the compose project", W,
       'PROJECT = os.environ.get("INFRX_E5L_PROJECT") or NAMESPACE', "PROJECT = NAMESPACE",
       PROJECT),
    _m("compose_run_under_the_namespace", "compose itself runs under the override", W,
       """'"INFRX_E2_PROJECT": PROJECT')""", """'"INFRX_E2_PROJECT": f"infrx-{namespace}"')""",
       PROJECT),
    _m("any_project_name", "only an e5l project name is accepted", W,
       'r"e5l[a-z0-9]{0,12}"', 'r"[a-z0-9]{1,15}"', PROJECT),
)

# ------------------------------------------------------------------ the stack list

SHIP, RET = "infrx/traces/ship/shipper.py", "infrx/traces/retention/policy.py"
OUTBOX, SUBMIT = "infrx/traces/feedback/pg.py", "infrx/judge/submit.py"
STORE, FEEDBACK = "infrx/state/lab_access.py", "infrx/gateway/routes/feedback.py"
TRACE = "../../tests/integration/lab_observe/scenarios_trace.py"
OWORLD = "../../tests/integration/lab_observe/observe_world.py"

O01_OFF = "test_o01_capture_is_off_by_default_and_a_served_request_leaves_no_trace"
O01_SHIP = "test_o01_a_captured_request_ships_once_with_its_pins_and_only_its_org_finds_it"
O02 = "test_o02_feedback_is_acknowledged_after_commit_owned_by_its_key_and_projected_once"
O03 = "test_o03_a_provider_reads_a_grantors_trace_only_under_a_current_sharing_grant"
O04_DEFAULT = "test_o04_the_default_dry_run_mode_sends_nothing"
O04_RUN = "test_o04_a_consented_run_sends_once_scores_once_and_charges_no_customer_wallet"
O05 = "test_o05_a_grant_revoked_between_reservation_and_egress_sends_nothing"
O06_READS = "test_o06_expired_or_deleted_content_is_gone_for_every_read_before_and_after_the_sweep"
O06_JUDGE = "test_o06_content_expired_or_deleted_before_egress_never_reaches_the_judge"
O06_SWEPT = "test_o06_after_the_sweep_nothing_is_left_to_send"
O07 = "test_o07_clickhouse_down_holds_the_segment_serves_the_app_and_ships_once_after"
O08 = "test_o08_a_timed_out_submit_is_quarantined_never_resent_and_reconciled"
O09_PROJECTOR = "test_o09_a_projector_killed_after_its_insert_redelivers_and_projects_once"
O09_WORKER = "test_o09_a_box_worker_killed_mid_traffic_restarts_and_finishes_every_job_once"
O03_ROUTE = "test_o03_the_lab_traces_route_through_the_real_gateway"
O04_PG = "test_o04_the_judge_ledger_is_d6js_postgresql_ledger"
ROUTE, CONSENT = "infrx/gateway/routes/lab_traces.py", "infrx/state/lab_consent.py"
J3 = "infrx/judge/calibration/report.py"
#: Cases that FAIL on this base (a product finding, recorded in the evidence): no mutant can
#: name them (the pristine baseline refuses a failing case), so coverage lists them here and
#: `test_every_case_is_covered_by_a_mutant` holds the list to exactly the failing ones.
KNOWN_FAIL: dict[str, str] = {}   # E5L-F1 fixed by WR-OBS-3 (J2 reads through Retention)

STACK_MUTANTS: tuple[Mutant, ...] = (
    _m("st_capture_on_in_the_box", "the drill judges capture off with the box at its defaults",
       OWORLD, "    with world.composed(workdir, start=start, **env) as trip:",
       "    with world.composed(workdir, start=start, **{'TRACE_PUMPS': '1', **env}) as trip:",
       O01_OFF),
    _m("st_ship_without_pins", "a shipped row carries the pins PostgreSQL admitted", SHIP,
       "serving_version_id=pins.serving_version_id if pins else None,",
       "serving_version_id=None,", O01_SHIP),
    _m("st_find_of_any_org", "a projection read is bound to the server's org", SHIP,
       '"WHERE org_id = {org:UUID} AND request_id = {request:UUID} ORDER BY trace_id"',
       '"WHERE request_id = {request:UUID} ORDER BY trace_id"', O01_SHIP),
    _m("st_feedback_ack_before_commit_status", "the ack is a 201 rendered from the committed "
       "row", FEEDBACK, "status_code=201,", "status_code=200,", O02, O09_PROJECTOR),
    _m("st_first_grant_version", "the current grant is the latest version (a revocation is "
       "one)", STORE, "        return history[-1] if history else None",
       "        return history[0] if history else None", O03, O05),
    _m("st_judge_in_any_mode", "only live mode submits", SUBMIT,
       "    if wiring.settings.judge_mode != JUDGE_MODE_LIVE:", "    if False:", O04_DEFAULT),
    _m("st_judge_sends_one_sample", "every consented sample with content is sent", SUBMIT,
       "    for request_id in run.sample_ids:", "    for request_id in run.sample_ids[:1]:",
       O04_RUN),
    _m("st_no_recheck_before_egress", "the permission is checked again just before egress",
       SUBMIT, "        await recheck()\n    except errors.DomainError:",
       "        pass\n    except errors.DomainError:", O05),
    _m("st_judge_reads_the_raw_projection", "the judge reads content through T3's Retention "
       "(E5L-F1)", SUBMIT,
       "        body = await wiring.retention.read_content(job.grantor_org_id, request_id)",
       "        body = await __import__(\"infrx.traces.ship.shipper\", fromlist=[\"x\"]).read_content(\n"
       "            wiring.retention.traces, wiring.retention.objects, job.grantor_org_id, request_id)",
       O06_JUDGE),
    _m("st_deleted_request_found", "a deleted request has no rows for any read", RET,
       "        if REQUEST in await self._scopes(org_id, request_id):\n            return []\n"
       "        now = self.clock()\n        return [r for r in await self.traces.find",
       "        if False:\n            return []\n"
       "        now = self.clock()\n        return [r for r in await self.traces.find",
       O06_READS),
    _m("st_sweep_keeps_objects", "the sweep deletes the content it cleans", RET,
       "                        await self.objects.delete(row.content_key)",
       "                        pass", O06_READS, O06_SWEPT),
    _m("st_failed_insert_acked", "a segment whose insert failed is held, never acked", SHIP,
       "                reason = type(failure).__name__", "                reason = None", O07),
    _m("st_timeout_is_a_failure", "an unknown submit outcome is quarantined, never released",
       SUBMIT, '        return await ledger.quarantine(run.run_id, f"unknown submit outcome: "',
       '        return await ledger.release(run.run_id, "failed", f"unknown submit outcome: "',
       O08),
    _m("st_claim_window_ignored", "a claimed event is not redelivered inside its window",
       OUTBOX, "           or x.claimed_at <= infrx.now() - make_interval(secs => %(redelivery)s))",
       "           or true)", O09_PROJECTOR),
    _m("st_worker_never_restarted", "the drill judges the job after the worker came back",
       TRACE, "        time.sleep(1.0)\n        trip.box.start(\"worker\")",
       "        time.sleep(1.0)", O09_WORKER),
    # --- lab-observe-2: the cells the tip now supports (LAB-API route, D6J ledger, J3 report)
    _m("st_route_content_without_a_grant", "the route shows content only under a current grant",
       ROUTE, "            if grants[key] is not None:", "            if True:", O03_ROUTE),
    _m("st_route_shows_a_deleted_request", "an owner-deleted request is not there for the Lab",
       ROUTE, "            if REQUEST in scopes or not t3.metadata_live(row.started_at, now):",
       "            if not t3.metadata_live(row.started_at, now):", O03_ROUTE),
    _m("st_pg_reserve_another_payer", "the reservation holds the named payer's budget", CONSENT,
       '"run_id": run_id, "provider_org_id": provider_org_id, "payer_ref": payer_ref,',
       '"run_id": run_id, "provider_org_id": provider_org_id, "payer_ref": payer_ref + "-x",',
       O04_PG),
    _m("st_j3_limited_results_calibrate", "a limited result never enters the calibration",
       J3, "        elif result.limited:", "        elif False:", O04_PG),
)
STACK_CASES = tuple(sorted({case for m in STACK_MUTANTS for case in m.cases}))


def unbound_cases() -> set[str]:
    """The scenario cases that skip NOT RUN (named through the layer-1 `UNBOUND` case)."""
    found = set()
    for path in HERE.glob("scenarios_*.py"):
        for name, body in re.findall(r"^def (test_o\d\d_\w+)\(.*?\n((?:    .*\n|\n)*)",
                                     path.read_text(), re.M):
            if "not_run(" in body:
                found.add(name)
    return found


def case_names() -> set[str]:
    """The layer-1 cases: the runner's and the packaging's."""
    return {name for file in LAYER1_FILES
            for name in re.findall(r"^def (test_\w+)\(", (REPO / file).read_text(), re.M)}


def stack_case_names() -> set[str]:
    return {name for path in HERE.glob("scenarios_*.py")
            for name in re.findall(r"^def (test_o\d\d_\w+)\(", path.read_text(), re.M)} \
        - unbound_cases() - set(KNOWN_FAIL)


def _layer1(root: pathlib.Path) -> pathlib.Path:
    junk = shutil.ignore_patterns("__pycache__", ".venv", "node_modules", ".next")
    shutil.copytree(REPO / "tests" / "integration", root / "tests" / "integration", ignore=junk)
    for part in ("infrx", "deploy"):
        shutil.copytree(API_DIR / part, root / "apps" / "infrx-api" / part, ignore=junk)
    for part in ("lab/observe", "alerts", "observe", "rollout/steps"):
        shutil.copytree(REPO / "infra" / part, root / "infra" / part, ignore=junk)
    wiring = pathlib.Path("research", "plan", "evidence", "e", "E5L-wiring")
    shutil.copytree(REPO / wiring, root / wiring)
    (root / "research" / "plan").mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO / "research" / "plan" / "tasks.json", root / "research" / "plan" / "tasks.json")
    return root


def _stack(root: pathlib.Path) -> pathlib.Path:
    """The copy one level down as `apps/infrx-api` (PYTHONPATH: the box imports it), beside
    the scenarios and the migrations the template is built from."""
    api = _layer1(root) / "apps" / "infrx-api"
    migrations = pathlib.Path("apps", "app", "supabase", "migrations")
    shutil.copytree(REPO / migrations, root / migrations)
    # the box's media host serves M's synthetic clip; J2's fakes live under tests/j
    shutil.copytree(API_DIR / "tests", api / "tests",
                    ignore=shutil.ignore_patterns("__pycache__", "node_modules"))
    return api


RUNNER = Runner(name="e5l", targets=LAYER1_FILES, package="", layout=_layer1)
#: the kept stack's identity, handed to the copy (harness.working_dir / STATE_FILE seams)
STACK_ENV = ("INFRX_E2_NAMESPACE", "INFRX_E2_CHECKOUT", "INFRX_E2_STATE_FILE", "INFRX_E5L_PROJECT")
STACK_RUNNER = Runner(name="e5l-stack", package="", layout=_stack, env=STACK_ENV,
                      timeout_s=1800,
                      targets=tuple(f"../../tests/integration/lab_observe/{f}" for f in (
                          "scenarios_trace.py", "scenarios_judge.py")))


def claim_the_kept_stack() -> str | None:
    """Point the copies at the kept e5l stack; None if there is one, else why not."""
    os.environ["INFRX_E2_NAMESPACE"] = "e5l"
    sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parent / "backend")]
    import observe_world
    if observe_world.harness.NAMESPACE != "e5l":
        return (f"this process loaded E2's harness as {observe_world.harness.NAMESPACE!r}: run "
                "the stack list in its own process with INFRX_E2_NAMESPACE=e5l")
    if not observe_world.stack.has_stack():
        return (f"no kept {observe_world.harness.PROJECT} stack: run {observe_world.RUNNER} "
                "--keep first (the same INFRX_E5L_PROJECT)")
    os.environ["INFRX_E2_CHECKOUT"] = observe_world.harness.working_dir()
    os.environ["INFRX_E2_STATE_FILE"] = str(observe_world.harness.STATE_FILE)
    return None


def run_mutant(mutant) -> Result:
    if mutant not in STACK_MUTANTS:
        return shared.run_mutant(mutant, RUNNER)
    return shared.pristine(STACK_CASES, STACK_RUNNER) or shared.run_mutant(mutant, STACK_RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run E5L/I2L-OBS's layer-1 mutation list"))
