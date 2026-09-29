#!/usr/bin/env python3
"""R32/R83 for LAB-LOCAL (E4-ON): one single-edit defect per decision the gate claims, through
the shared runner (`apps/infrx-api/tests/contracts/mutants.py`, a private copy whose
Python-only compile check is relaxed, as E3L's).

* `MUTANTS` (layer 1, no stack): the runner's classification, gate and exit codes, the
  switch list, the pinned-journey rule, the tasklocal key and the Lab web's names.
* `STACK_MUTANTS`: the decisions the scenarios guard with every switch ON, killed on a kept
  e3l stack (runner.py --keep) by the scenario cases, in a copy whose processes import the
  mutated package. Without a kept stack they skip visibly:

    tests/integration/lab-local.sh --keep --only scenarios --out <dir>
    INFRX_MUTANTS=all INFRX_E2_NAMESPACE=e3l apps/infrx-api/.venv/bin/python -m pytest -q \\
        -p no:cacheprovider tests/integration/lab_local/test_mutants.py
"""
from __future__ import annotations

import importlib.util
import pathlib
import re
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
API_DIR = REPO / "apps" / "infrx-api"


def _load(key: str, path: pathlib.Path):
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


shared = _load("lab_local_shared_mutants", API_DIR / "tests" / "contracts" / "mutants.py")
Mutant, Outcome, Result, Runner, _m = (shared.Mutant, shared.Outcome, shared.Result,
                                       shared.Runner, shared._m)
shared.compile = lambda source, filename, mode, *a, **k: (
    compile(source, filename, mode, *a, **k) if str(filename).endswith(".py") else None)
# E3L's copy layouts and kept-stack claim: the same e3l block, the same seams.
operate = _load("lab_operate.mutants", HERE.parent / "lab_operate" / "mutants.py")

R = "tests/integration/lab_local/runner.py"
W = "tests/integration/lab_local/lab_world.py"
TASKLOCAL = "apps/infrx-api/infrx/contracts/tasklocal.py"
LAYER1_FILES = ("tests/integration/lab_local/test_lab_local_runner.py",)

SWITCHES = "test_lab_local_every_deployment_switch_is_on_in_the_composition"
SKIPS = "test_lab_local_a_skip_is_never_a_pass_and_names_its_kind"
MISSING = "test_lab_local_a_scenario_missing_a_required_case_is_not_run"
HARNESS = "test_lab_local_a_harness_error_is_invalid_not_a_product_fail"
GATE = "test_lab_local_the_gate_is_the_worst_stage_and_exits_as_e2c_does"
NO_STACK = "test_lab_local_no_stack_blocks_every_stage"
PYTEST_STAGE = "test_lab_local_a_pytest_stage_with_a_skip_or_no_case_is_blocked_not_passed"
E4 = "test_lab_local_the_e4_subset_is_the_composition_lanes_regression"
PINNED = "test_lab_local_a_key_pinned_journey_is_not_run_with_its_owners_exact_rerun"
REQUIRED = "test_lab_local_the_required_cases_are_what_the_scenario_module_defines"
KEY = "test_lab_local_the_key_is_tasklocal_in_the_lab_band_and_the_block_is_e3ls_lock"
WEB = "test_lab_local_the_lab_web_gets_lab_jsons_names_and_no_service_key"
PENDING = "test_lab_local_pending_roles_are_proven_to_refuse_by_name"
JOURNEY = "test_lab_local_a_journey_passes_only_when_every_case_ran_and_passed"
CONTROL_LOGIN = "test_lab_local_the_control_factory_runs_on_its_own_login_never_the_owner"
R222_RECORDED = "test_lab_local_r222_the_recorded_28c9c2cc_verdict_is_not_accepted"
R222_CLASSES = "test_lab_local_r222_excuses_only_the_ruled_classes"
R222_E4 = "test_lab_local_r222_the_e4_stage_is_excused_only_for_its_by_design_case"
R222_FD = "test_lab_local_r222_the_fd0aba04_verdict_stays_open_after_the_journeys_land"
AS_OWNER = "test_lab_local_the_control_login_answers_as_the_owner_login"
TLS_READY = "test_lab_local_the_lab_web_is_ready_only_once_its_tls_origin_answers"
PIN_CLEAN = "test_lab_local_the_evidence_it_writes_never_makes_the_pin_dirty"

MUTANTS: tuple[Mutant, ...] = (
    _m("a_switch_left_off", "EVERY switch is ON in the composition", W,
       '"LAB_DATASETS", "LAB_TEACHERS")', '"LAB_DATASETS")', SWITCHES),
    _m("a_switch_on_as_false", "a switch ON is `true`", W,
       '{**{name: "true" for name in', '{**{name: "false" for name in', SWITCHES),
    _m("xfail_is_a_pass", "an xfail is never a pass", R,
       '        return NOT_RUN, "xfail is not a pass here: " + message[:300]',
       '        return PASS, ""', SKIPS),
    _m("not_run_is_a_pass", "a skip without a mark is NOT RUN", R,
       "[mark.group(1)] if mark else NOT_RUN,", "[mark.group(1)] if mark else PASS,", SKIPS),
    _m("required_cases_ignored", "a scenario missing a required case is NOT RUN", R,
       "        if absent:\n", "        if False:\n", MISSING),
    _m("harness_error_is_a_product_fail", "a broken harness is INVALID, never FAIL", R,
       "if HARNESS.search(message)", "if False and HARNESS.search(message)", HARNESS),
    _m("gate_is_the_best_status", "the gate is the WORST status", R,
       "    return max(statuses, key=RANK.__getitem__, default=NOT_RUN)",
       "    return min(statuses, key=RANK.__getitem__, default=NOT_RUN)", GATE),
    _m("not_run_exits_zero", "NOT RUN exits 3 (E2C), never 0", R,
       "EXIT = {PASS: 0, FAIL: 1, BLOCKED: 3, NOT_RUN: 3, INVALID: 4}",
       "EXIT = {PASS: 0, FAIL: 1, BLOCKED: 3, NOT_RUN: 0, INVALID: 4}", GATE),
    _m("no_stack_passes", "no stack blocks every stage", R,
       '[{"stage": name, "status": BLOCKED, "reason": f"BLOCKED[stack] {why}"}',
       '[{"stage": name, "status": PASS, "reason": f"BLOCKED[stack] {why}"}', NO_STACK),
    _m("skipped_stage_passes", "a pytest stage with a skipped case is BLOCKED", R,
       '    if counts["skipped"] or counts["xfailed"]:', '    if counts["xfailed"]:',
       PYTEST_STAGE),
    _m("empty_stage_passes", "a pytest stage that ran nothing is BLOCKED", R,
       '    if counts["tests"] == 0:', '    if counts["tests"] < 0:', PYTEST_STAGE),
    _m("an_e4_suite_dropped", "the E4 subset is the composition lanes' regression", R,
       '"tests/g", "tests/w", "tests/contracts",', '"tests/g", "tests/contracts",', E4),
    _m("pinned_journey_runs", "a journey on another key's resource is NOT RUN, never run", R,
       '    if spec["foreign"]:\n        wr, why', '    if False:\n        wr, why', PINNED),
    _m("a_runnable_journey_skipped", "every journey whose backend accepts lab-on runs (WR-LDP-1)",
       R, '"key": "r2",\n                 "foreign": None}',
       '"key": "r2",\n                 "foreign": ("WR-X", "x")}', PINNED),
    _m("r222_fail_excused", "an in-scope FAIL is never excused (R234)", R,
       "            return status == PASS or (status == NOT_RUN",
       "            return status != NOT_RUN or (status == NOT_RUN", R222_CLASSES, R222_RECORDED),
    _m("r222_any_not_run_excused", "a NOT RUN is excused only when every lane it names is "
       "out of scope", R,
       '    return bool(found) and set(found.group(1).split(",")) <= set(OUT_OF_SCOPE)',
       "    return bool(found)", R222_CLASSES, R222_RECORDED),
    _m("r222_unmarked_skip_excused", "a NOT RUN without a named class stays open", R,
       "(status == NOT_RUN and bool(mine)", "(status == NOT_RUN", R222_CLASSES),
    _m("r222_absent_required_ignored", "a scenario missing a required case stays open", R,
       " or \\\n                set(REQUIRED[sid]) - set(cases):", ":", R222_CLASSES, R222_RECORDED),
    _m("r222_e4_any_failure_excused", "only the by-design e4-on failure is reported as such", R,
       "and set(failed) <= set(BY_DESIGN)", "", R222_E4),
    _m("r222_e4_skips_ignored", "an e4-on stage with skipped cases is never reported as only "
       "its by-design FAIL (0-LL2C-1)", R,
       "                and not (counts.get(\"skipped\") or counts.get(\"xfailed\")):",
       "                and True:", R222_E4),
    _m("r222_by_design_accepted", "a by-design FAIL stays open until ruled (R222, 0-LL2C-2)",
       R, "                excused[name] = BY_DESIGN[name]\n",
       "                excused[name] = BY_DESIGN[name]\n                return True\n",
       R222_CLASSES, R222_FD),
    _m("r222_e4_by_design_accepted", "the e4-on by-design FAIL stays open until ruled "
       "(0-LL2C-2)", R, "            excused.update({case: why for case, why in "
       "BY_DESIGN.items() if case in failed})\n",
       "            excused.update({case: why for case, why in BY_DESIGN.items() if case in "
       "failed})\n            continue\n", R222_E4, R222_FD),
    _m("r222_stages_ignored", "a stage that did not pass keeps the gate open", R,
       "        still[name] = status\n", "        pass\n", R222_E4, R222_RECORDED),
    _m("r222_always_accepted", "accepted is computed, never asserted", R,
       '{"accepted": not still,', '{"accepted": True,', R222_CLASSES, R222_RECORDED),
    _m("login_judged_alone", "the Lab login answers as the owner login (R251)", W,
       "for f in set(login) | set(owner) if login.get(f) != owner.get(f)}",
       "for f in set(login) | set(owner) if False}", AS_OWNER),
    _m("login_any_typed_family_pending", "only a pending family's typed 503 is NOT RUN", W,
       "if f not in wrong and f in pending and v.startswith(UNAVAILABLE)}",
       "if f not in wrong and v.startswith(UNAVAILABLE)}", AS_OWNER),
    _m("login_any_503_pending", "a store fault's 503 is no typed unavailability", W,
       "and f in pending and v.startswith(UNAVAILABLE)}",
       'and f in pending and v.startswith("503")}', AS_OWNER),
    _m("a_required_case_renamed", "the required cases are the module's cases", R,
       '    "o04": ("test_o04_the_control_factory_serves_a_lab_session",\n',
       '    "o04": ("test_o04_the_control_factory_serves",\n', REQUIRED),
    _m("another_lanes_block", "the block is borrowed only under E3L's own lock", R,
       'LOCK = Path("/tmp") / f"infrx-{NAMESPACE}.runner.lock"',
       'LOCK = Path("/tmp") / "infrx-lab-local.runner.lock"', KEY),
    _m("key_outside_the_band", "lab-on sits in the Lab band", TASKLOCAL,
       '"lab-on": {"postgres": 57537, "valkey": 57538, "valkey-q": 57539}',
       '"lab-on": {"postgres": 57437, "valkey": 57538, "valkey-q": 57539}', KEY),
    _m("lab_web_holds_the_service_key", "the Lab web holds no service-role key", W,
       '            "NEXT_PUBLIC_SUPABASE_ANON_KEY": stack.jwt("anon", ttl_s=12 * 3600),',
       '            "SUPABASE_SERVICE' '_ROLE_KEY": stack.jwt("service_role"),', WEB),  # split: test_harness' needle
    _m("lab_web_on_http", "the Lab origin is https (production refuses http)", W,
       'LAB_ORIGIN = f"https://localhost:{LAB_TLS_PORT}"',
       'LAB_ORIGIN = f"http://localhost:{LAB_TLS_PORT}"', WEB),
    _m("journey_always_passes", "a journey passes only when it ran and passed", R,
       "                       status=journey_status(code, log.read_text(errors=\"replace\")))",
       "                       status=PASS)", JOURNEY),
    _m("journey_skips_pass", "a skipped journey case is BLOCKED", R,
       'return PASS if count("tests") and count("pass") == count("tests") else BLOCKED',
       'return PASS if count("tests") and count("fail") == 0 else BLOCKED', JOURNEY),
    _m("journey_empty_passes", "a journey that ran no case is BLOCKED", R,
       'return PASS if count("tests") and count("pass")', 'return PASS if count("pass")', JOURNEY),
    _m("control_on_the_owner", "the control factory runs on infrx_lab_control (LDP-R4)", W,
       'CONTROL_LOGIN = "infrx_lab_control"', 'CONTROL_LOGIN = "postgres"', CONTROL_LOGIN),
    _m("teacher_url_unset", "LAB_TEACHER_URL is the local teacher fake", W,
       '            "LAB_TEACHER_URL": f"http://127.0.0.1:{TEACHER_PORT}",\n            "LAB_CHECKPOINT_KEYS"',
       '            "LAB_CHECKPOINT_KEYS"', SWITCHES),
    _m("lab_web_without_control", "the Lab web reaches the control factory", W,
       'return {"LAB_CONTROL_URL": control or control_url(), ', 'return {', WEB),
    _m("lab_web_ready_before_its_origin", "the Lab web is ready once its https origin "
       "answers (o07's race)", W, ' or \\\n                wait_ready(proc, f"{origin}/", 30.0, verify=False)',
       "", TLS_READY),
    _m("evidence_is_dirt", "the run's own evidence never makes the pin dirty", R,
       '"--porcelain", "--", ".", ":!research/plan/evidence"))}', '"--porcelain"))}', PIN_CLEAN),
    _m("anything_is_clean", "a stray file outside the evidence is dirty", R,
       '"dirty": bool(git("status", "--porcelain",', '"dirty": False and bool(git("status", "--porcelain",',
       PIN_CLEAN),
    _m("a_served_role_pending", "eval, judge and datasets must start; only named lanes "
       "pend", W, '    "rollout": ("WR-LSQ-9", "the rollout pass needs"),\n',
       '    "rollout": ("WR-LSQ-9", "the rollout pass needs"),\n'
       '    "eval": ("WR-X", "x"),\n', PENDING),
)

# ------------------------------------------------------------------ the stack list

PILOT = "infrx/gateway/pilot.py"
WORKERS = "infrx/lab/workers/__main__.py"
CONTROL = "infrx/lab/control/app.py"
LAB_AUTH = "infrx/gateway/lab_auth.py"
WORKER = "infrx/worker/__main__.py"
ROUTING = "infrx/rollouts/routing/__init__.py"
SW = "../../tests/integration/lab_local/lab_world.py"

O01 = "test_o01_the_gateway_is_ready_with_every_switch_on"
O02 = "test_o02_the_consumer_worker_is_ready_with_its_switches_on"
O02_SEAM = "test_o02_the_consumer_workers_lab_seam_refuses_by_name"
O03 = {role: f"test_o03_the_{role}_role_starts" for role in
       ("eval", "checkpoints", "judge", "annotation", "training", "rollout", "datasets")}
O04 = "test_o04_the_control_factory_serves_a_lab_session"
#: LDP-F7 (fixed by lab-control-routes): the factory is ready on infrx_lab_control off the pooler.
O04_LOGIN = "test_o04_the_control_factory_is_ready_on_its_own_login"
O05_ALL = "test_o05_every_lab_route_family_answers_a_lab_session"
O05_LAB = "test_o05_the_lab_routes_gateway_serves_every_family"
O05_KEY = "test_o05_a_consumer_key_is_no_lab_session_on_any_family"
O05_CONTROL = "test_o05_the_control_factory_serves_every_family_on_its_own_login"
O06 = "test_o06_the_consumer_path_serves_and_settles_once"
O07 = "test_o07_the_lab_web_renders_every_page_family_signed_in"

STACK_MUTANTS: tuple[Mutant, ...] = (
    _m("st_all_switches_on_the_owner_login", "ROLLOUT_ROUTING refuses any login but "
       "infrx_runtime: the all-switches gateway runs on it", SW,
       "world.composed(workdir, start=(), runtime_login=True,",
       "world.composed(workdir, start=(), runtime_login=False,", O01),
    _m("st_trace_pumps_without_a_spool", "TRACE_PUMPS ON has the spool it needs", SW,
       '            "TRACE_SPOOL_DIR": str(spool),\n', "", O02),
    _m("st_consumer_worker_runs_lab_eval", "the consumer worker's Lab seam refuses (R198)",
       WORKER, "    if evaluators is None or targets is None:", "    if False:", O02_SEAM),
    _m("st_refusal_restartable", "a role's refusal is exit 2, never restarted (R211)", WORKERS,
       "REFUSED = 2", "REFUSED = 1", O03["checkpoints"], O03["annotation"], O03["training"],
       O03["rollout"]),
    _m("st_role_never_ready", "a role that serves answers /readyz", WORKERS,
       'up = live and (path == "/livez" or await _answers(worker.ready))',
       'up = live and path == "/livez"', O03["eval"], O03["judge"], O03["datasets"]),
    _m("st_control_never_ready", "the control factory answers ready on its database", CONTROL,
       '        return {"status": "ready"}',
       '        return JSONResponse({"status": "ready"}, status_code=503)', O04),
    _m("st_control_login_sets_role", "the control factory never sets a role on its own login "
       "(LDP-F7)", CONTROL, "connector(os.environ[DATABASE_URL], set_role=False)",
       "connector(os.environ[DATABASE_URL])", O04_LOGIN),
    _m("st_control_families_set_role", "the families on the Lab login never set a role "
       "(R245): they answer as on the owner login", CONTROL,
       "connector(lab[DATABASE_URL], set_role=False)", "connector(lab[DATABASE_URL])",
       O05_CONTROL),
    _m("st_control_families_unmounted", "the control factory serves every Lab family (WR-LDP-2)",
       CONTROL, "        family.register(app, rt)\n", "        pass\n", O05_CONTROL),
    _m("st_datasets_unmounted", "LAB_DATASETS ON mounts the datasets family", PILOT,
       "    rt.lab_datasets = lab_datasets if deployment.lab_datasets else None",
       "    rt.lab_datasets = None", O05_LAB, O07),
    _m("st_consumer_key_answered", "a bearer that is no Lab session is refused (401)", LAB_AUTH,
       'REFUSALS = ((errors.InvalidApiKey, 401, "unauthenticated"),',
       'REFUSALS = ((errors.InvalidApiKey, 200, "unauthenticated"),', O05_KEY),
    _m("st_routed_accept_drops_the_answer", "with ROLLOUT_ROUTING ON the routed accept "
       "answers the relay's admission", ROUTING,
       "                router.shadow(pending, request)\n        return answer",
       "                router.shadow(pending, request)\n        return None", O06),
)
#: E4-ON's standing FAIL: the all-switches App gateway on infrx_runtime is R237's
#: never-on-the-box configuration (LDP-F1 resolved by design, option (b): that login holds no
#: lab_* grant, so every Lab route refuses typed; LDP-F3 fixed, no 500). Kept out of the stack
#: list's pristine baseline (E8L's KNOWN_FAIL rule); the box's Lab server is O05_CONTROL.
KNOWN_FAIL: set[str] = {O05_ALL}
STACK_CASES = tuple(sorted({case for m in STACK_MUTANTS for case in m.cases}))
SCENARIOS = "../../tests/integration/lab_local/scenarios_on.py"


def case_names() -> set[str]:
    return set(re.findall(r"^def (test_\w+)\(", (REPO / LAYER1_FILES[0]).read_text(), re.M))


def stack_case_names() -> set[str]:
    return set(re.findall(r"^def (test_o\d\d_\w+)\(", (HERE / "scenarios_on.py").read_text(),
                          re.M)) - KNOWN_FAIL


def _layer1(root: pathlib.Path) -> pathlib.Path:
    """E3L's layer-1 copy plus the Lab's names manifest the Lab web case reads."""
    operate._layer1(root)
    (root / "infra" / "lab" / "app").mkdir(parents=True)
    shutil.copy2(REPO / "infra" / "lab" / "app" / "lab.json", root / "infra/lab/app/lab.json")
    for head in ("28c9c2cc", "fd0aba04"):                         # R222_RECORDED, R222_FD
        recorded = f"research/plan/evidence/e/E4ON-raw-{head}/verdict.json"
        (root / recorded).parent.mkdir(parents=True)
        shutil.copy2(REPO / recorded, root / recorded)
    return root


RUNNER = Runner(name="lab-local", targets=LAYER1_FILES, package="", layout=_layer1)
def _stack(root: pathlib.Path) -> pathlib.Path:
    """E3L's stack copy plus the release's deploy dir (the Lab origin's TLS terminator is the
    pinned Caddy that deploy/lib.sh names)."""
    api = operate._stack(root)
    shutil.copytree(API_DIR / "deploy", api / "deploy")
    return api


STACK_RUNNER = Runner(name="lab-local-stack", package="", layout=_stack,
                      env=operate.STACK_ENV, timeout_s=1800, targets=(SCENARIOS,))


def claim_the_kept_stack() -> str | None:
    """E3L's claim: the copies point at the kept e3l stack (runner.py --keep)."""
    return operate.claim_the_kept_stack()


def run_mutant(mutant) -> Result:
    if mutant not in STACK_MUTANTS:
        return shared.run_mutant(mutant, RUNNER)
    return shared.pristine(STACK_CASES, STACK_RUNNER) or shared.run_mutant(mutant, STACK_RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run LAB-LOCAL's layer-1 mutation list"))
