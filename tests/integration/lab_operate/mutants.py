#!/usr/bin/env python3
"""R32/R83 for E3L: one single-edit defect per decision the gate claims, through the shared
runner (`apps/infrx-api/tests/contracts/mutants.py`, a private copy whose Python-only compile
check is relaxed for the SQL targets, as track I's).

* `MUTANTS` (layer 1, no stack): the runner's classification, cells, gate and exit codes, the
  NOT RUN vocabulary, and the unbound L3/L4 cases (never a pass once they stop skipping).
* `STACK_MUTANTS`: the product decisions the running scenarios guard (L2's service and store,
  the 0027/0030 doors, 0001's RLS, the catalog, the gateway) and the l11 drill's premises, killed
  on a kept e3l stack by the scenario cases, in a copy whose gateway and worker import the
  mutated package. Without a kept stack they skip visibly:

    apps/infrx-api/.venv/bin/python tests/integration/lab_operate/runner.py --keep --out <dir>
    INFRX_MUTANTS=all INFRX_E2_NAMESPACE=e3l apps/infrx-api/.venv/bin/python -m pytest -q \\
        -p no:cacheprovider tests/integration/lab_operate/test_mutants.py
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
    spec = importlib.util.spec_from_file_location("e3l_shared_mutants", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


shared = _shared()
Mutant, Outcome, Result, Runner, _m = (shared.Mutant, shared.Outcome, shared.Result,
                                       shared.Runner, shared._m)
shared.compile = lambda source, filename, mode, *a, **k: (
    compile(source, filename, mode, *a, **k) if str(filename).endswith(".py") else None)

R = "tests/integration/lab_operate/runner.py"
W = "tests/integration/lab_operate/lab_world.py"
P = "tests/integration/lab_operate/scenarios_publish.py"
LAYER1_FILES = ("tests/integration/lab_operate/test_e3l_runner.py", P)

MATRIX = "test_e3l_the_matrix_carries_the_manifest_test_ids_and_the_brief_cases"
REQUIRED = "test_e3l_the_required_cases_are_exactly_what_the_scenario_modules_define"
SKIPS = "test_e3l_a_skip_is_never_a_pass_and_names_its_kind"
MISSING = "test_e3l_a_scenario_missing_a_required_case_is_not_run"
HARNESS = "test_e3l_a_harness_error_is_invalid_not_a_product_fail"
GATE = "test_e3l_the_gate_and_the_cells_are_the_worst_status_and_exit_as_e2c_does"
NO_STACK = "test_e3l_no_stack_blocks_every_scenario"
NAMESPACE = "test_e3l_the_namespace_is_the_reserved_block"
RERUN = "test_e3l_a_not_run_case_names_its_lanes_and_the_exact_rerun"
#: the L3/L4 cases still waiting on a lane (their body calls `waits`); E3L-BIND binds the rest
UNBOUND = tuple(name for name, body in re.findall(r"^def (test_l\d\d_\w+)\((.*?)(?=^def |\Z)",
                                                  (REPO / P).read_text(), re.M | re.S)
                if "waits(" in body)

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
       'TEST_IDS = ("LAB-ACCESS", "LAB-PUBLISH", "SPLIT-CONTRACT")',
       'TEST_IDS = ("LAB-ACCESS", "LAB-PUBLISH")', MATRIX),
    _m("a_required_case_renamed", "the required cases are the modules' cases", R,
       '    "l12": ("test_l12_a_consumer_key_is_refused_by_every_control_operation",),',
       '    "l12": ("test_l12_a_consumer_key_is_refused",),', REQUIRED),
    _m("another_namespace", "e3l runs in its own reserved block", R,
       'NAMESPACE = "e3l"', 'NAMESPACE = "e3c"', NAMESPACE),
    _m("not_run_without_the_rerun", "a NOT RUN names the exact rerun", W,
       "rerun after the merge: {RERUN} --only {sid}", "rerun after the merge: {RERUN}", RERUN),
    _m("unbound_case_runs", "an L3/L4 case not bound to its port is never a pass", P,
       '    lab.not_run(sid, *lanes, why=f"{why}. Steps: {steps}")', "    return", *UNBOUND),
)

# ------------------------------------------------------------------ the stack list

A = "infrx/lab/access/__init__.py"
STORE = "infrx/state/lab_access.py"
RECORDS = "infrx/contracts/v2/records.py"
CATALOG = "infrx/state/catalog.py"
MODELS = "infrx/gateway/routes/models.py"
M = "../app/supabase/migrations/"
SPLIT = "../../tests/integration/lab_operate/scenarios_split.py"

L01_SESSION = "test_l01_each_provider_session_sees_only_its_own_workspace"
L01_OTHER = "test_l01_a_member_of_one_provider_is_refused_every_operation_on_the_other"
L01_REVOKED = "test_l01_a_revoked_membership_is_refused_on_its_next_call"
L02 = "test_l02_the_seeded_private_dev_deployment_is_not_discoverable_or_admissible"
L02_BOUND = "test_l02_a_provider_created_dev_revision_never_reaches_app_discovery"
L07_GATEWAY = "test_l07_a_consumer_key_reaches_no_provider_control_on_the_gateway"
L07_SESSION = "test_l07_a_consumer_key_is_no_lab_session"
L07_OWNER = "test_l07_a_consumer_owner_has_no_provider_workspace"
L08_GRANT = "test_l08_no_grant_no_content"
L08_PURPOSE = "test_l08_a_grant_is_purpose_bound_and_its_revocation_denies_the_next_call"
L08_DIRECT = "test_l08_a_provider_session_reads_no_consumer_rows_directly"
L03 = "test_l03_registry_validation_refuses_bad_artifacts_and_foreign_ownership"
L04 = "test_l04_publication_needs_operator_approval_and_snapshots_the_rate"
L05 = "test_l05_app_discovers_and_serves_the_published_revision"
L06 = "test_l06_rollback_during_a_queued_request_keeps_its_serving_and_rate_pins"
L3 = "infrx/lab/control/__init__.py"
M32 = M + "0032_lab_control.sql"
L11_DOWN = "test_l11_the_lab_down_mid_traffic_leaves_app_inference_serving"
L11_BAD = "test_l11_a_bad_lab_release_and_its_rollback_leave_every_accepted_job_finished_once"

STACK_MUTANTS: tuple[Mutant, ...] = (
    _m("st_membership_read_of_every_user", "the membership read is the user's own rows",
       M + "0027_lab_access.sql", "   where m.user_id = (p_args->>'user_id')::uuid\n",
       "   where true\n", L01_SESSION, L07_OWNER),
    _m("st_session_door_serves_revoked", "the session door answers current memberships only",
       M + "0030_lab_access_self.sql",
       "     and (r->>'revoked_at' is null or infrx.now() < (r->>'revoked_at')::timestamptz)\n",
       "", L01_REVOKED),
    _m("st_aggregates_unguarded", "another provider's health is refused",
       A, "        await self._member(user_id, provider_org_id, "
          "ProviderCapability.read_aggregate_health)\n", "", L01_OTHER),
    _m("st_private_resolves_for_consumers", "a private dev revision never resolves for a "
       "consumer credential", CATALOG,
       "if row is None and audience is CredentialAudience.provider_dev and endpoint_id:",
       "if row is None and endpoint_id:", L02, L02_BOUND),
    _m("st_control_path_on_the_consumer_origin", "the consumer origin mounts no provider-"
       "control path", MODELS, 'MODELS_PATH = "/v1/models"', 'MODELS_PATH = "/v1/providers"',
       L07_GATEWAY),
    _m("st_session_door_open_to_anon", "the session door is for signed-in users only",
       M + "0030_lab_access_self.sql",
       "grant execute on function public.lab_provider_memberships() to authenticated;",
       "grant execute on function public.lab_provider_memberships() to authenticated, anon;",
       L07_SESSION),
    _m("st_grant_of_any_grantor", "a grant is read for its own grantor only",
       M + "0027_lab_access.sql",
       "   where g.grantor_org_id = (p_args->>'grantor_org_id')::uuid\n     and",
       "   where true\n     and", L08_GRANT),
    _m("st_purpose_ignored", "each purpose is its own permission", RECORDS,
       "                and purpose in self.purposes)", "                and True)", L08_PURPOSE),
    _m("st_first_grant_version", "the current grant is the latest version (a revocation is "
       "one)", STORE, "        return history[-1] if history else None",
       "        return history[0] if history else None", L08_PURPOSE),
    _m("st_consumer_keys_readable", "a provider session reads no consumer rows",
       M + "0001_init.sql",
       "create policy api_keys_select on public.api_keys for select to authenticated\n"
       "  using (public.is_org_member(org_id) or public.is_operator());",
       "create policy api_keys_select on public.api_keys for select to authenticated\n"
       "  using (true);", L08_DIRECT),
    _m("st_lab_never_went_down", "the drill judges the App only with the Lab really down",
       SPLIT, "        web.kill()\n        assert web.answers() is None",
       "        web.kill\n        assert web.answers() is None", L11_DOWN),
    _m("st_rollback_to_the_bad_release", "the rollback is judged on the Lab answering again",
       SPLIT, "        web.start(LAB_DIR)                                # rollback",
       "        web.start(bad)                                    # rollback", L11_BAD),
)
STACK_MUTANTS += (
    # E3L-BIND: L3's control decisions on the real stores (LabControl over PgControlStore)
    _m("st_moving_tag_registers", "a runtime is pinned by digest, never a moving tag", L3,
       '@sha256:[0-9a-f]{64}$")', '[@:](sha256:[0-9a-f]{64}|latest)$")', L03),
    _m("st_any_runtime_registers", "only a supported runtime registers", L3,
       '    if runtime["repo"] not in SUPPORTED_RUNTIMES:', "    if False:", L03),
    _m("st_any_schema_registers", "only the gateway's schemas register", L3,
       "    if (capability.input_schema_ref, capability.output_schema_ref) not in "
       "SUPPORTED_SCHEMAS:", "    if False:", L03),
    _m("st_foreign_model_registers", "a provider registers only its own models", L3,
       "        if await self.store.model_provider(serving.model_id) != provider_org_id:",
       "        if False:", L03),
    _m("st_developer_proposes", "only an administrator proposes publication", L3,
       "        source = await self._dev(user_id, provider_org_id, deployment_revision_id,\n"
       "                                 ProviderCapability.propose_publication)",
       "        source = await self._dev(user_id, provider_org_id, deployment_revision_id,\n"
       "                                 ProviderCapability.manage_dev_deployment)", L04),
    _m("st_card_unattributed", "the rate snapshot names the approving operator", L3,
       "effective_at=await self.store.db_now(), approved_by=operator.principal)",
       "effective_at=await self.store.db_now(), approved_by=\"operator\")", L04),
    _m("st_publish_audit_actor", "the publish audit names its actor", M32,
       "perform infrx.lab_control_audit(d.provider_org_id, 'lab_publish', p_args->>'actor',",
       "perform infrx.lab_control_audit(d.provider_org_id, 'lab_publish', 'operator',", L04),
    _m("st_card_input_rate_misfiled", "the card is the approved rates, each in its place", M32,
       "      (c->>'serving_version_id')::uuid, (c->>'input_rate_per_million')::numeric,",
       "      (c->>'serving_version_id')::uuid, (c->>'output_rate_per_million')::numeric,",
       L04, L05),
    _m("st_unapproved_card_listed", "discovery lists only the card the runtime approved (R69)",
       MODELS, "    if regime == CREDIT and card.rate_card_version != "
               "settings.pilot.active_rate_card_version:", "    if False:", L05),
)
#: Cases whose FAIL is a recorded cross-lane finding (evidence E3L-BIND): kept out of the
#: stack list's pristine baseline until the owning lane fixes it (as E8L's KNOWN_FAIL).
KNOWN_FAIL = {"test_l05_discovery_reports_the_listing_version_it_serves"}
STACK_CASES = tuple(sorted({case for m in STACK_MUTANTS for case in m.cases}))


def case_names() -> set[str]:
    """The layer-1 cases: the runner's own, and the unbound L3/L4 cases."""
    return set(re.findall(r"^def (test_\w+)\(", (REPO / LAYER1_FILES[0]).read_text(), re.M)) \
        | set(UNBOUND)


def stack_case_names() -> set[str]:
    return {name for path in HERE.glob("scenarios_*.py")
            for name in re.findall(r"^def (test_l\d\d_\w+)\(", path.read_text(), re.M)} \
        - set(UNBOUND) - KNOWN_FAIL


def _layer1(root: pathlib.Path) -> pathlib.Path:
    junk = shutil.ignore_patterns("__pycache__", ".venv", "node_modules", ".next")
    shutil.copytree(REPO / "tests" / "integration", root / "tests" / "integration", ignore=junk)
    shutil.copytree(API_DIR / "infrx", root / "apps" / "infrx-api" / "infrx", ignore=junk)
    (root / "research" / "plan").mkdir(parents=True)
    shutil.copy2(REPO / "research" / "plan" / "tasks.json", root / "research" / "plan" / "tasks.json")
    return root


def _stack(root: pathlib.Path) -> pathlib.Path:
    """The copy one level down as `apps/infrx-api` (PYTHONPATH: the box imports it), beside
    the scenarios, the migrations the template is built from, and the real Lab release."""
    api = _layer1(root) / "apps" / "infrx-api"
    migrations = pathlib.Path("apps", "app", "supabase", "migrations")
    shutil.copytree(REPO / migrations, root / migrations)
    # the box's media host serves M's synthetic clip (pilotbox.clip: tests/m/support.py)
    shutil.copytree(API_DIR / "tests", api / "tests",
                    ignore=shutil.ignore_patterns("__pycache__", "node_modules"))
    (root / "apps" / "lab").symlink_to(REPO / "apps" / "lab")
    return api


RUNNER = Runner(name="e3l", targets=LAYER1_FILES, package="", layout=_layer1)
#: the kept stack's identity, handed to the copy (harness.working_dir / STATE_FILE seams)
STACK_ENV = ("INFRX_E2_NAMESPACE", "INFRX_E2_CHECKOUT", "INFRX_E2_STATE_FILE")
STACK_RUNNER = Runner(name="e3l-stack", package="", layout=_stack, env=STACK_ENV,
                      timeout_s=1800,
                      targets=tuple(f"../../tests/integration/lab_operate/{f}" for f in (
                          "scenarios_access.py", "scenarios_publish.py", "scenarios_split.py")))


def claim_the_kept_stack() -> str | None:
    """Point the copies at the kept e3l stack; None if there is one, else why not."""
    os.environ["INFRX_E2_NAMESPACE"] = "e3l"
    sys.path[:0] = [str(HERE), str(HERE.parent), str(HERE.parent / "backend")]
    import lab_world
    if lab_world.harness.NAMESPACE != "e3l":
        return (f"this process loaded E2's harness as {lab_world.harness.NAMESPACE!r}: run the "
                "stack list in its own process with INFRX_E2_NAMESPACE=e3l")
    if not lab_world.stack.has_stack():
        return f"no kept e3l stack: run {lab_world.RUNNER} --keep first"
    os.environ["INFRX_E2_CHECKOUT"] = lab_world.harness.working_dir()
    os.environ["INFRX_E2_STATE_FILE"] = str(lab_world.harness.STATE_FILE)
    return None


def run_mutant(mutant) -> Result:
    if mutant not in STACK_MUTANTS:
        return shared.run_mutant(mutant, RUNNER)
    return shared.pristine(STACK_CASES, STACK_RUNNER) or shared.run_mutant(mutant, STACK_RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run E3L's layer-1 mutation list"))
