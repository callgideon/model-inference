#!/usr/bin/env python3
"""R32/R83 for E8L: one single-edit defect per decision the gate claims, through the shared
runner (`apps/infrx-api/tests/contracts/mutants.py`, a private copy whose Python-only compile
check is relaxed for the SQL targets, as E3L's and E6L's).

* `MUTANTS` (layer 1, no stack): the runner's classification, cells, gate and exit codes, the
  NOT RUN vocabulary, and the unbound k08 case (never a pass once it stops skipping).
* `STACK_MUTANTS` (the failure oracles of tasks.json E8L): the product decisions the rollout
  scenarios guard - R1's pins, eligibility, cohorts, outage, shadow bound and suppression,
  assignment key; F3's cohort hash; 0043's routing reads; R2's guardrails, horizon, approval
  and CAS reread; B2's slice verdict, coverage and cost delta; R3's probe, tokenizer and
  measurement checks; the gateway's ROLLOUT_ROUTING switch - killed on a kept e8l stack by the
  scenario cases, in a copy that imports the mutated package and migrates its own Lab
  database from the mutated migrations. Without a kept stack they skip visibly:

    apps/infrx-api/.venv/bin/python tests/integration/lab_rollout/runner.py --keep --out <dir>
    INFRX_MUTANTS=all INFRX_E2_NAMESPACE=e8l apps/infrx-api/.venv/bin/python -m pytest -q \\
        -p no:cacheprovider tests/integration/lab_rollout/test_mutants.py
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


def _sibling(name: str):
    """Loaded under a name unique to this package's own directory: `lab_world` is also every
    sibling lab_*/'s module name (WR-E7L-5) - with two such packages in one process, a bare
    `import lab_world` resolves to whichever package's directory sorts first in sys.path, not
    to the importing file's own package."""
    key = f"{HERE.name}.{name}"
    cached = sys.modules.get(key)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(key, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def _shared():
    path = API_DIR / "tests" / "contracts" / "mutants.py"
    spec = importlib.util.spec_from_file_location("e8l_shared_mutants", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


shared = _shared()
Mutant, Outcome, Result, Runner, _m = (shared.Mutant, shared.Outcome, shared.Result,
                                       shared.Runner, shared._m)
shared.compile = lambda source, filename, mode, *a, **k: (
    compile(source, filename, mode, *a, **k) if str(filename).endswith(".py") else None)

R = "tests/integration/lab_rollout/runner.py"
W = "tests/integration/lab_rollout/lab_world.py"
P = "tests/integration/lab_rollout/scenarios_pending.py"
LAYER1_FILES = ("tests/integration/lab_rollout/test_e8l_runner.py", P)

MATRIX = "test_e8l_the_matrix_carries_the_manifest_test_ids_and_the_brief_cases"
REQUIRED = "test_e8l_the_required_cases_are_exactly_what_the_scenario_modules_define"
SKIPS = "test_e8l_a_skip_is_never_a_pass_and_names_its_kind"
MISSING = "test_e8l_a_scenario_missing_a_required_case_is_not_run"
HARNESS = "test_e8l_a_harness_error_is_invalid_not_a_product_fail"
GATE = "test_e8l_the_gate_and_the_cells_are_the_worst_status_and_exit_as_e2c_does"
NO_STACK = "test_e8l_no_stack_blocks_every_scenario"
NAMESPACE = "test_e8l_the_namespace_is_the_reserved_block"
RERUN = "test_e8l_a_not_run_case_names_its_lanes_and_the_exact_rerun"
SUB_CELL = "test_e8l_a_sub_cell_is_not_run_naming_its_lanes_and_its_parents_rerun"
K09_BOUND = "test_e8l_k09s_breach_half_is_bound_and_no_longer_a_sub_cell"
K10_UI = "test_e8l_k10s_composed_ui_journey_is_bound_and_no_longer_a_sub_cell"
R222 = "test_e8l_r222_accepts_only_a_not_run_out_of_local_scope"
PLAN_PATH = "test_e8l_k10s_plan_path_handed_to_the_worker_is_absolute"
GATE_OUT = "test_e8l_the_ui_suites_record_is_read_back_from_a_relative_out"
E2E = "apps/lab/tests/e2e/gate.py"
ENOENT = "test_e8l_an_environment_enoent_is_blocked_harness_not_a_product_fail"
UNBOUND = tuple(re.findall(r"^def (test_k\d\d_\w+)\(", (REPO / P).read_text(), re.M))

MUTANTS: tuple[Mutant, ...] = (
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
       'TEST_IDS = ("ROLLOUT-PIN", "ROLLOUT-RECOVER", "OPT-PARITY")',
       'TEST_IDS = ("ROLLOUT-PIN", "ROLLOUT-RECOVER")', MATRIX),
    _m("k10_claimed_waiting", "k10 runs for real since WR-R4-2 is composed (merge #50)", R,
       'emergency rollback",\n            "test_ids": ["ROLLOUT-PIN"], "lanes": []},',
       'emergency rollback",\n            "test_ids": ["ROLLOUT-PIN"], "lanes": ["WR-R4-2"]},',
       MATRIX),
    _m("k10_port_half_optional", "k10's port half (WR-C6-K10) is a required case", R,
       '            "test_k10_the_composed_releases_route_proposes_and_the_operator_decides"),',
       "            ),", MATRIX, REQUIRED),
    _m("a_required_case_renamed", "the required cases are the modules' cases", R,
       '    "k10": ("test_k10_the_releases_ui_over_the_real_route",\n',
       '    "k10": ("test_k10_the_releases_ui",\n', REQUIRED),
    _m("another_namespace", "e8l runs in its own reserved block", R,
       'NAMESPACE = "e8l"', 'NAMESPACE = "e6l"', NAMESPACE),
    _m("an_endpoint_on_a_service_port", "the synthetic endpoints avoid E2's service ports", R,
       '"engine": 57461}', '"engine": 57480}', NAMESPACE),
    _m("the_dead_port_is_an_endpoint", "the outage port is no endpoint's", R,
       "DEAD_PORT = 57499", "DEAD_PORT = 57460", NAMESPACE),
    _m("not_run_without_the_rerun", "a NOT RUN names the exact rerun", W,
       "rerun after the merge: {RERUN} --only {sid}", "rerun after the merge: {RERUN}", RERUN),
    _m("sub_cell_is_a_pass", "an unbound half of a scenario is NOT RUN, never a pass", R,
       '"parent_status": result[spec["parent"]]["status"],\n             "status": NOT_RUN,',
       '"parent_status": result[spec["parent"]]["status"],\n             "status": PASS,',
       SUB_CELL),
    _m("sub_cell_without_its_lane", "a sub-cell's reason names its lanes", R,
       '"reason": f"NOT RUN[{\',\'.join(spec[\'lanes\'])}]",', '"reason": "NOT RUN",', SUB_CELL),
    _m("k09_breach_still_a_sub_cell", "k09's breach half is bound over 0054's Live "
       "(WR-LIVE-K09): no NOT RUN sub-cell beside it", R,
       "SUB_CELLS: dict[str, dict] = {}",
       'SUB_CELLS: dict[str, dict] = {"k09-breach": {"parent": "k09", "lanes": ["WR-C6-LIVE"], '
       '"title": "", "note": ""}}', K09_BOUND),
    _m("k10_ui_still_a_sub_cell", "k10's UI journey is bound over the composed verdict and "
       "`rollout decide` (WR-LR6-VERDICT, WR-LIVE-DECIDE): no NOT RUN sub-cell beside it", R,
       "SUB_CELLS: dict[str, dict] = {}",
       'SUB_CELLS: dict[str, dict] = {"k10-ui-composed": {"parent": "k10", "lanes": '
       '["WR-LR6-VERDICT"], "title": "", "note": ""}}', K10_UI),
    # R222/R235: the runner's machine check (lab_evaluate's shape, with the sub-cells)
    _m("r222_in_scope_lane_excused", "a NOT RUN on a lane not ruled out of scope stays open", R,
       'return set(lanes) <= set(OUT_OF_SCOPE) and bool(entry["cases"]) and',
       'return bool(entry["cases"]) and', R222),
    _m("r222_never_run_excused", "a scenario with no case run is open", R,
       'set(lanes) <= set(OUT_OF_SCOPE) and bool(entry["cases"]) and',
       "set(lanes) <= set(OUT_OF_SCOPE) and", R222),
    _m("r222_any_reason_excuses", "every reason must be the scenario's own wait", R,
       "            all(f\"NOT RUN[{','.join(lanes)}]\" in reason",
       "            any(f\"NOT RUN[{','.join(lanes)}]\" in reason", R222),
    _m("r222_fail_excused_by_its_message", "an in-scope FAIL is never excused, whatever its "
       "message says (R234)", R,
       "        if entry[\"status\"] != NOT_RUN:        # R234", "        if False:        # R234",
       R222),
    _m("r222_pass_is_open", "a PASS never keeps the gate from acceptance", R,
       'if entry["status"] != PASS and not excused(sid, entry)}',
       "if not excused(sid, entry)}", R222),
    _m("r222_sub_cells_ignored", "a sub-cell's NOT RUN is judged like a scenario's", R,
       "                  if not set(cell[\"lanes\"]) <= set(OUT_OF_SCOPE)})",
       "                  if False})", R222),
    _m("r222_gpu_in_scope", "k08's GPU target is ruled out of local scope (R222)", R,
       'OUT_OF_SCOPE = {"P-08": "GPU (P-08 staging target)"}', "OUT_OF_SCOPE = {}", R222),
    _m("r222_landed_wr_still_excused", "a landed product WR (WR-LIVE-DECIDE, WR-LR6-VERDICT: "
       "lab-rollout-7) excuses nothing", R,
       'OUT_OF_SCOPE = {"P-08": "GPU (P-08 staging target)"}',
       'OUT_OF_SCOPE = {"P-08": "GPU (P-08 staging target)",\n'
       '                "WR-LR6-VERDICT": "product WR: WR-LR6-VERDICT"}', R222),
    _m("k10_plan_path_relative", "the plan path handed to `rollout launch` (cwd=API) is "
       "absolute (WR-LR5-RV2)", W, '    return workdir.resolve() / "plan.json"',
       '    return workdir / "plan.json"', PLAN_PATH),
    _m("gate_out_relative", "the suite's record directory reaches node absolute "
       "(WR-LR6-GATE-OUT)", E2E, '    out = Path(out).resolve() / f"e2e-{suite}"',
       '    out = Path(out) / f"e2e-{suite}"', GATE_OUT),
    # RV-4 (merge #62): an environment ENOENT is BLOCKED[harness], never a product FAIL
    _m("gate_runs_without_node_modules", "no suite runs without apps/lab/node_modules", E2E,
       '    if not (LAB / "node_modules").is_dir():', "    if False:", ENOENT),
    _m("gate_no_node_is_a_crash", "no node on PATH is EnvironmentBlocked", E2E,
       "    except FileNotFoundError as absent:", "    except NotADirectoryError as absent:",
       ENOENT, dies_by=("FileNotFoundError",)),
    _m("environment_enoent_is_a_fail", "an environment ENOENT is BLOCKED[harness]", R,
       "            if ENVIRONMENT.search(message):", "            if False:", ENOENT),
    _m("unbound_case_runs", "a case waiting on P-08 is never a pass",
       P, "    lw.not_run(sid, *lanes, why=why)", "    return", *UNBOUND),
)

# ------------------------------------------------------------------ the stack list

R1 = "infrx/rollouts/routing/__init__.py"
R2 = "infrx/rollouts/control/__init__.py"
R3 = "infrx/rollouts/optimization/__init__.py"
F3 = "infrx/contracts/lab/records.py"
B2 = "infrx/evaluation/reports/__init__.py"
G = "infrx/gateway/pilot.py"
D9 = "../app/supabase/migrations/0043_lab_reads_and_proposals.sql"
D44 = "../app/supabase/migrations/0044_lab_control_reads.sql"
D45 = "../app/supabase/migrations/0045_lab_serving_ref_identity.sql"
D48 = "../app/supabase/migrations/0048_lab_release_listing.sql"
L3S = "infrx/state/lab_control.py"
LW = "infrx/lab/workers/__main__.py"
WM = "infrx/worker/__main__.py"

K01 = "test_k01_routing_off_serves_todays_request_over_a_live_release"
K01_IDENTITY = "test_k01_a_candidate_ref_resolves_through_0045_and_matches_l3s_own_computation"
K02 = "test_k02_a_shadow_changes_nothing_the_user_sees_or_pays"
K03_ARMS = "test_k03_a_subject_keeps_its_arm_and_each_admission_is_one_d9_row"
K03_WEIGHT = "test_k03_candidate_traffic_is_bounded_and_a_raised_weight_keeps_its_subjects"
K03_AB = "test_k03_an_ab_split_serves_each_candidate_within_its_weight"
K03_NEVER = "test_k03_pins_ineligible_and_session_subjects_are_never_routed"
K03_OUTAGE = "test_k03_a_release_store_outage_is_a_503_never_the_baseline"
K04_ONCE = "test_k04_a_breach_rolls_back_once_under_two_controllers_for_future_admissions"
K04_RESTART = "test_k04_a_controller_killed_before_the_alias_cas_converges_on_restart"
K09_PROCESS = "test_k09_the_controller_process_restarted_mid_rollout"
K09_PASS = "test_k09_the_rollout_pass_process_converges_a_rollback_killed_before_the_cas"
K05_ACCEPT = "test_k05_an_accepting_report_after_the_horizon_is_approved_once"
K05_HOLD = "test_k05_an_inconclusive_report_blocks_promotion"
K05_SLICE = "test_k05_a_slice_regression_under_an_aggregate_gain_rolls_back"
K05_GAPS = "test_k05_missing_or_stale_evidence_never_expands"
K05_SPEND = "test_k05_overspend_rolls_back_and_units_never_mix"
K06_PROMOTED = "test_k06_an_emergency_rollback_moves_a_promoted_alias_back"
K06_READS = "test_k06_the_worlds_alias_read_is_the_real_control_store_on_its_login"
K10_LISTING = "test_k10_the_release_listing_reads_d9s_rows_and_r2s_latest_verdict"
K10_UI = "test_k10_the_releases_ui_over_the_real_route"                 # LAB-E2E
K10_PORT = "test_k10_the_composed_releases_route_proposes_and_the_operator_decides"
LR = "infrx/gateway/routes/lab_releases.py"
K07_STORED = "test_k07_a_variant_is_probed_compared_and_stored"
K07_REFUSED = "test_k07_incompatible_variants_and_unmeasured_claims_are_refused"

STACK_MUTANTS: tuple[Mutant, ...] = (
    _m("st_serving_ref_digest_drifts", "0045's digest is the same JCS field set L3's "
       "operations.serving_ref reads (R188/R208) - SQL and Python must agree byte-for-byte",
       D45, "|| '\"revision_label\":' || to_json(s.revision_label) || ','",
       "|| '\"revision_label\":' || to_json(s.runtime_image_ref) || ','", K01_IDENTITY),
    _m("st_routing_ignores_the_switch", "ROLLOUT_ROUTING off leaves the relay's own accept", G,
       "    if deployment.rollout_routing:\n        # R1", "    if rollouts is not None:\n        # R1",
       K01),
    _m("st_shadow_serves_the_candidate", "a shadow's user is admitted on the baseline (no shadow "
       "output leaks)", R1,
       "            return request, tuple((release, c.serving_ref) for c in policy.candidates), \\",
       "            return request.model_copy(update={\"model_revision\": release.revisions["
       "policy.candidates[0].serving_ref]}), tuple((release, c.serving_ref) for c in "
       "policy.candidates), \\", K02),
    _m("st_shadow_unbounded", "at shadow_limit 0 no duplicate runs (P-12)", R1,
       "            if self.inflight[policy_id] >= release.shadow_limit:",
       "            if self.inflight[policy_id] > release.shadow_limit:", K02),
    _m("st_assignment_by_request", "an assignment is keyed by the admitted job", R1,
       "wire.HEADER_INFERENCE_ID, request.request_id))",
       "wire.HEADER_INFERENCE_ID + \"-\", request.request_id))", K03_ARMS),
    _m("st_cohort_by_version", "a subject's bucket is per policy, not per version (R179)", F3,
       '    digest = hashlib.sha256(f"{policy.policy_id}\\n{subject_key}".encode()).hexdigest()',
       '    digest = hashlib.sha256(f"{policy.policy_id}{policy.version}\\n{subject_key}"'
       '.encode()).hexdigest()', K03_WEIGHT),
    _m("st_explicit_pin_routed", "an explicit <model>@<revision> is never routed", R1,
       '        if "@" in request.model_revision:', "        if False:", K03_NEVER),
    _m("st_session_cohort_routed", "a session cohort serves the baseline (R180)", R1,
       '            if policy.cohort != "account" or not await',
       '            if False and policy.cohort != "account" or not await', K03_NEVER),
    _m("st_revoked_subject_eligible", "a revoked grant ends eligibility at once", D9,
       "                          and (g.revoked_at is null or infrx.now() < g.revoked_at)",
       "                          and true", K03_NEVER),
    _m("st_outage_is_the_baseline", "a release-store outage is a 503 (R181)", R1,
       '            raise errors.DependencyUnavailable("the release store did not answer") from None',
       "            return request, (), None", K03_OUTAGE),
    _m("st_first_candidate_takes_all", "each candidate gets its own weight", F3,
       "            serving = candidate.serving_ref\n            break",
       "            serving = policy.candidates[0].serving_ref\n            break", K03_AB),
    _m("st_weight_ignored", "candidate traffic is bounded by the weight", F3,
       "        edge += candidate.weight_bp", "        edge += 5_000", K03_ARMS, K03_WEIGHT),
    # E8L-F3 (base drift, not a mutant-writing error): this anchor lived in 0043's
    # `release_active` body until lab-sql-lw5 merged 0045, which `create or replace
    # function`s the WHOLE body (D5 item 10b's pattern, already fixed the same way in
    # tests/d/code_mutants_d8.py's `q_active_*` list) - a mutation of 0043's copy of this
    # line now runs invisibly, since Postgres always executes 0045's redefinition. Moved to
    # 0045's own copy of the identical line (verified identical below).
    _m("st_rolled_back_still_routes", "only a running release routes", D45,
       "   where x.endpoint_id = v_endpoint and x.state = 'running';",
       "   where x.endpoint_id = v_endpoint and x.state in ('running', 'rolled_back') "
       "order by x.state limit 1;", K04_ONCE),
    _m("st_lost_race_raises", "a lost CAS race rereads and accepts the same rollback", R2,
       "            if (await self._store.release(policy_ref)).state != to:",
       "            if True:", K04_ONCE, K09_PROCESS),
    _m("st_process_reads_nothing", "the real emergency-rollback process reads the alias through "
       "the real PgControlStore (pilot.control_serving, WR-E8L-7)", G,
       "                   PgControlStore(connect),\n", "                   PgControlStore(None),\n",
       K09_PROCESS),
    # WR-C5-K09: the bare rollout role (WR-R2-3) converges a rollback killed before the CAS
    _m("st_pass_skips_the_rolled_back", "the pass steps rolled-back releases too (R216/R228: "
       "a rollback killed before the alias CAS is converged)", LW,
       'releases.releases_in(("running", "rolled_back"),', 'releases.releases_in(("running",),',
       K09_PASS),
    _m("st_pass_reads_another_plan", "the plan is the one stored beside the release "
       "(lab/<p>/releases/<policy_id>/plan.json, D9's digest)", LW,
       'return f"lab/{provider_org_id}/releases/{policy_id}/plan.json"',
       'return f"lab/{provider_org_id}/releases/{policy_id}/plan"', K09_PASS),
    _m("st_pass_rolled_back_needs_live", "a rolled-back release only converges: it needs no "
       "R1 aggregate (R228)", LW,
       'current = await live(item) if item.release.state == "running" else None',
       "current = await live(item)", K09_PASS),
    _m("st_step_leaves_the_rolled_back", "R2's step converges a rolled-back release (R216)", R2,
       "            await self._converge(policy, policy_ref)\n", "            pass\n", K09_PASS),
    _m("st_pass_waits_a_cadence_first", "the first pass runs at start, not a cadence later",
       WM, "    while True:\n        try:\n            await step()",
       "    while True:\n        await sleep(interval_s)\n        try:\n            await step()",
       K09_PASS),
    _m("st_pass_held_is_failed", "a running release with nothing assigned is held, not a "
       "failure (nothing assigned, R244)", LW,
       "            except errors.DependencyUnavailable:\n                done[\"held\"] += 1\n",
       "", K09_PASS),
    # WR-LIVE-K09: the pass evaluates a running release on D9's Live (0054, R244)
    _m("st_pass_never_reads_live", "the pass reads the running release's Live from D9 (0054): "
       "a failed terminal job assigned to its candidate is a breach rolled back once", LW,
       "    current = await releases.live(listing.policy_ref)\n", "    current = None\n",
       K09_PASS),
    _m("st_converge_by_full_ref", "R2 recognises a promoted candidate by serving identity "
       "(R216, E8L-F2): L3's promotion mints a fresh deployment revision", R2,
       "    return f\"{head.rpartition(':')[0]}@{digest}\"", "    return ref", K06_PROMOTED),
    # WR-E8L-3: the world's alias read is the real PgControlStore on 0044's control login
    _m("st_alias_answers_a_superseded_listing", "an endpoint's alias is the newest CURRENT "
       "listing (R207): an alias that moved off answers nothing there", L3S,
       '"and l.version = (select max(v.version) "', '"and l.version >= (select min(v.version) "',
       K06_READS),
    _m("st_control_login_lacks_the_listings_read", "the reads run on 0044's infrx_lab_control "
       "login, which holds the catalog listings read", D44,
       "grant select on infrx.catalog_listings to infrx_lab_control;", "select 1;", K06_READS),
    _m("st_error_rate_ignored", "an error-rate breach rolls back", R2,
       "    if cand.errors > plan.max_error_rate * cand.requests:", "    if False:", K04_ONCE),
    _m("st_latency_ignored", "a p99 breach rolls back", R2,
       "    if cand.p99_ms is not None and cand.p99_ms > plan.max_p99_ms:", "    if False:",
       K04_RESTART),
    _m("st_controller_acts_on_expand", "the controller never acts on an expand verdict", R2,
       '        if verdict.action == "rollback":', '        if verdict.action != "hold":',
       K05_ACCEPT),
    _m("st_peeking_wins", "nothing is approvable before the horizon", R2,
       "    if now < started_at + timedelta(seconds=plan.horizon_s):", "    if False:",
       K05_ACCEPT),
    _m("st_cost_delta_summed", "the cost delta is candidate minus baseline, per unit", B2,
       '    observed["cost_delta"] = {u: str(cand_costs[u] - base_costs[u])',
       '    observed["cost_delta"] = {u: str(cand_costs[u] + base_costs[u])', K05_ACCEPT),
    _m("st_approve_on_hold", "only an expand verdict is approvable", R2,
       '        if verdict.action != "expand":', '        if verdict.action == "rollback":',
       K05_HOLD, K05_ACCEPT, dies_by=("LabRejected",)),
    # (F3's decision contract backstops it: a hold carries no evidence and an expansion names
    # its evidence, so the approval dies as LabRejected at the store - declared)
    _m("st_inconclusive_accepts", "incomplete coverage is inconclusive, never accept", B2,
       '        {"outcome": "inconclusive", "reasons": unsure} if unsure else \\',
       '        {"outcome": "accept", "reasons": unsure} if unsure else \\', K05_HOLD),
    _m("st_slice_regression_hidden", "an inferior required slice rejects", B2,
       '    inferior = [f"{name} inferior" for name, v in verdicts if v == "inferior"]',
       '    inferior = [f"{name} inferior" for name, v in verdicts if v == "inferior" and '
       'name == "overall"]', K05_SLICE, K07_REFUSED),
    _m("st_quality_reject_ignored", "a rejecting report bound to the release rolls back", R2,
       '    if outcome and outcome["outcome"] == "reject":', "    if False:", K05_SLICE),
    _m("st_metrics_stale_ignored", "delayed metrics hold: no expansion on stale evidence", R2,
       "    if now - live.observed_until > timedelta(seconds=plan.max_lag_s):", "    if False:",
       K05_GAPS),
    _m("st_coverage_ignored", "thin quality coverage holds", R2,
       "    if live.quality_covered < plan.min_quality_coverage * cand.requests:",
       "    if False:", K05_GAPS),
    _m("st_min_requests_ignored", "too few candidate requests hold", R2,
       "    if cand.requests < plan.min_requests:", "    if False:", K05_GAPS),
    _m("st_cohort_skew_ignored", "a skewed cohort holds", R2,
       "    if abs(cand.requests * 10_000 - weight * total) > plan.max_skew_bp * total:",
       "    if False:", K05_GAPS),
    _m("st_budget_ignored", "spend past the budget rolls back", R2,
       "    if live.spent.amount > plan.budget.amount:", "    if False:", K05_SPEND),
    # (no `st_units_mix`: without R2's unit check, `lab.Amount` itself refuses to compare
    # CREDIT with PROVIDER_USD (TypeError) - the money type backstops it, equivalent here)
    _m("st_listing_ignores_the_state", "the release listing narrows to the asked states "
       "(0048's lab_releases_in)", D48,
       "          or o.state = any(array(select jsonb_array_elements_text(p_args->'states'))))",
       "          or true)", K10_LISTING),
    _m("st_listing_earliest_decision", "the listing's latest decision is the newest by fence "
       "(0048's lab_releases_in)", D48,
       "       order by e.fence desc limit 1) d on true",
       "       order by e.fence asc limit 1) d on true", K10_LISTING),
    # LAB-E2E: k10's UI half - the page shows a pending proposal from the route's records
    # (the route's own fence check is backstopped by 0043's, so the page cannot see it drop)
    _m("st_releases_route_drops_the_proposals", "the releases answer carries the provider's "
       "proposals (the page shows the pending one)", LR,
       '"proposals": list(await x.port("proposals").proposals(provider))}',
       '"proposals": []}', K10_UI),
    # WR-C6-K10: k10's port half over pilot.lab_releases and `rollout launch|decide` (R240/R241)
    _m("st_propose_any_role", "only an administrator proposes", LR,
       "    require(who, Cap.propose_publication)\n", "", K10_PORT),
    _m("st_records_verdict_dropped", "the page's verdict is D9's latest decision", G,
       '        if d is not None:\n            return {"action": d.decision,',
       '        if False:\n            return {"action": d.decision,', K10_PORT),
    _m("st_records_decisions_dropped", "the page lists 0053's decisions", G,
       "for d in await self.d9.decisions(provider_org_id=provider_org_id)]", "for d in []]",
       K10_PORT),
    _m("st_launch_without_the_plan", "the launcher stores the plan before D9 starts the release "
       "(R241): the page lists it with that plan", LW,
       "        await write_once(lab_objects(mode, env), plan_key(provider, policy.policy_id),\n"
       "                         plan.model_dump_json().encode())\n", "", K10_PORT),
    _m("st_decide_reasons_unnamed", "an approved proposal's decision names the operator's "
       "reason and the proposal", LW,
       'reasons=(f"operator:{reason}", f"proposal:{proposal_id}"))',
       'reasons=(f"operator:{reason}",))', K10_PORT),
    _m("st_decide_leaves_the_alias", "an approved rollback converges the alias through R2's stop "
       "(R240)", LW,
       "        await controller.emergency_rollback(operator, policy, policy_ref, now=now, "
       "reason=reason)\n", "", K10_PORT),
    # WR-LR5-RV3: `rollout decide --reject` moves nothing; an expansion's approval is refused
    # while nothing is assigned (WR-LIVE-DECIDE, R240/R248)
    _m("st_decide_reject_moves", "a rejection decides the proposal rejected and nothing else",
       LW, "        if not approve:\n", "        if False:\n", K10_PORT),
    _m("st_decide_expand_approved", "an expansion's approval is refused while nothing is assigned: no D9 "
       "decision, the alias unchanged", LW, '        if found["kind"] != "rollback":\n',
       "        if False:\n", K10_PORT),
    _m("st_claim_unmeasured", "an optimization is claimed only with measurements", R3,
       '"optimization_claimed": outcome == "equivalent" and performance is not None}',
       '"optimization_claimed": outcome == "equivalent"}', K07_STORED),
    _m("st_version_unprobed", "the variant's engine answers its declared version", R3,
       '    if probe.get("version") != variant.engine_version or \\', "    if False or \\",
       K07_REFUSED),
    _m("st_tokenizer_ignored", "a changed tokenizer is not equivalent", R3,
       "if base.tokenizer_digest != candidate.tokenizer_digest else []", "if False else []",
       K07_REFUSED),
    _m("st_any_load_source", "a load comes from an experiment's results at a commit", R3,
       '    source: Annotated[str, Field(pattern=f"^{RESULTS.pattern}$")]', "    source: str",
       K07_REFUSED),
    _m("st_hardware_unchecked", "a load was measured on the registered hardware", R3,
       "                (ref, ident.engine_version, ident.hardware):",
       "                (ref, ident.engine_version, load.hardware):", K07_REFUSED),
)
STACK_CASES = tuple(sorted({case for m in STACK_MUTANTS for case in m.cases}))
SCENARIO_FILES = ("scenarios_route.py", "scenarios_recover.py", "scenarios_parity.py")
#: FAIL on this base: none. k06 (E8L-F2) is fixed by ROLLOUT-IDENTITY (R216: R2's
#: `_converge` compares by serving identity when the rollback is decided) with its
#: world baseline (WR-E8L-9); `st_converge_by_full_ref` names it.
KNOWN_FAIL: set[str] = set()


def case_names() -> set[str]:
    """The layer-1 cases: the runner's own, and the unbound k08 case."""
    return set(re.findall(r"^def (test_\w+)\(", (REPO / LAYER1_FILES[0]).read_text(), re.M)) \
        | set(UNBOUND)


def stack_case_names() -> set[str]:
    """Every running scenario case, except the known FAIL."""
    return {name for file in SCENARIO_FILES
            for name in re.findall(r"^def (test_k\d\d_\w+)\(", (HERE / file).read_text(), re.M)} \
        - KNOWN_FAIL


def _layer1(root: pathlib.Path) -> pathlib.Path:
    junk = shutil.ignore_patterns("__pycache__", ".venv", "node_modules", ".next")
    shutil.copytree(REPO / "tests" / "integration", root / "tests" / "integration", ignore=junk)
    shutil.copytree(API_DIR / "infrx", root / "apps" / "infrx-api" / "infrx", ignore=junk)
    (root / "research" / "plan").mkdir(parents=True)
    shutil.copy2(REPO / "research" / "plan" / "tasks.json", root / "research" / "plan" / "tasks.json")
    (root / E2E).parent.mkdir(parents=True)             # LAB-E2E's gate half (WR-LR6-GATE-OUT)
    shutil.copy2(REPO / E2E, root / E2E)
    return root


def _stack(root: pathlib.Path) -> pathlib.Path:
    """The copy one level down as `apps/infrx-api` (PYTHONPATH: the scenarios import it),
    beside the scenarios, the D/G/R/B test worlds and the migrations the Lab database is built
    from."""
    api = _layer1(root) / "apps" / "infrx-api"
    migrations = pathlib.Path("apps", "app", "supabase", "migrations")
    shutil.copytree(REPO / migrations, root / migrations)
    shutil.copytree(API_DIR / "tests", api / "tests",
                    ignore=shutil.ignore_patterns("__pycache__", "node_modules"))
    return api


RUNNER = Runner(name="e8l", targets=LAYER1_FILES, package="", layout=_layer1)
#: the kept stack's identity, handed to the copy (harness.working_dir / STATE_FILE seams)
STACK_ENV = ("INFRX_E2_NAMESPACE", "INFRX_E2_CHECKOUT", "INFRX_E2_STATE_FILE", "INFRX_LAB_DIR")
STACK_RUNNER = Runner(name="e8l-stack", package="", layout=_stack, env=STACK_ENV,
                      timeout_s=1800,
                      targets=tuple(f"../../tests/integration/lab_rollout/{f}"
                                    for f in SCENARIO_FILES))


def claim_the_kept_stack() -> str | None:
    """Point the copies at the kept e8l stack; None if there is one, else why not."""
    os.environ["INFRX_E2_NAMESPACE"] = "e8l"
    sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parent / "backend")]
    lab_world = _sibling("lab_world")
    if lab_world.harness.NAMESPACE != "e8l":
        return (f"this process loaded E2's harness as {lab_world.harness.NAMESPACE!r}: run the "
                "stack list in its own process with INFRX_E2_NAMESPACE=e8l")
    if not lab_world.stack.has_stack():
        return f"no kept e8l stack: run {lab_world.RUNNER} --keep first"
    os.environ["INFRX_E2_CHECKOUT"] = lab_world.harness.working_dir()
    os.environ["INFRX_E2_STATE_FILE"] = str(lab_world.harness.STATE_FILE)
    os.environ["INFRX_LAB_DIR"] = str(REPO / "apps" / "lab")      # k10's UI suite (LAB-E2E)
    return None


def run_mutant(mutant) -> Result:
    if mutant not in STACK_MUTANTS:
        return shared.run_mutant(mutant, RUNNER)
    return shared.pristine(STACK_CASES, STACK_RUNNER) or shared.run_mutant(mutant, STACK_RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run E8L's layer-1 mutation list"))
