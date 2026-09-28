#!/usr/bin/env python3
"""R32/R40/R83 for the Lab worker entry point (`infrx.lab.workers`, composition batch 2): one
single-edit defect per invariant `test_lab_workers.py` claims, through the shared runner.

    uv run --frozen pytest -q tests/w/test_lab_workers_mutants.py
    INFRX_MUTANTS=all uv run --frozen pytest -q tests/w/test_lab_workers_mutants.py
    uv run --frozen python -m tests.w.lab_workers_mutants --list
"""
from __future__ import annotations

import re

from ..contracts import mutants as shared
from ..contracts.mutants import Mutant, Result, Runner
from . import w3_mutants

API_DIR = shared.API_DIR
SUITE_FILE = "tests/w/test_lab_workers.py"
F = "lab/workers/__main__.py"
P = "gateway/pilot.py"
FILES = (F, P)
C = "test_lab_workers__"
SETTINGS = C + "each_role_refuses_to_start_naming_a_missing_setting"
PROCESS = C + "the_process_refuses_an_unknown_role_and_a_missing_setting"
EVAL = C + "eval_is_the_consumer_workers_one_composition"
TARGETS = C + "dev_targets_resolve_only_the_providers_private_dev_revision"
CKPT_REFUSE = C + "checkpoints_refuse_without_a_registry_and_a_deployer"
CKPT = C + "a_checkpoint_delivery_is_decided_by_b3_and_capacity_hands_it_back"
JUDGE = C + "the_judge_is_j2_on_its_ledger_dry_run_by_default"
SWEEP = C + "the_judge_pass_sweeps_silent_submissions"
DATASETS = C + "datasets_reconcile_every_providers_lineage_page_by_page"
ROLLOUT = C + "the_rollout_pass_refuses_until_its_inputs_exist"
STOP = C + "an_emergency_rollback_is_r2s_for_the_named_operator"
NO_PASS = C + "annotation_and_training_have_no_pass_and_refuse"
HEALTH = C + "readyz_is_the_database_and_every_pass_alive"
DEAD = C + "a_dead_pass_is_not_live_and_exits_non_zero"
EVERY = C + "the_pumps_are_every_step_forever"


def _m(name, invariant, old, new, *cases, file=F) -> Mutant:
    return Mutant(name=name, invariant=invariant, file=file, old=old, new=new, cases=cases)


MUTANTS: tuple[Mutant, ...] = (
    # --- settings and the process ------------------------------------------------------------
    _m("lw_setting_not_required", "a role refuses to start without a setting it reads",
       "    if missing:\n        raise RuntimeMisconfigured(mode, missing)\n    if PORT",
       "    if False:\n        raise RuntimeMisconfigured(mode, missing)\n    if PORT", SETTINGS),
    _m("lw_eval_key_optional", "the eval role needs its dev endpoint credential",
       '"LAB_EVAL_ENDPOINT_URL", "LAB_EVAL_ENDPOINT_KEY"),', '"LAB_EVAL_ENDPOINT_URL"),',
       SETTINGS),
    _m("lw_traces_optional", "the judge and datasets roles need the trace stack",
       'TRACES = ("CLICKHOUSE_URL", "S3_TRACE_BUCKET")', "TRACES = ()", SETTINGS),
    _m("lw_refusal_exit_code", "a refusal exits 2 (the unit's contract)", "REFUSED = 2",
       "REFUSED = 1", PROCESS),
    _m("lw_unknown_role_composed", "an unknown role is refused by name",
       "    if role not in BUILD:\n", "    if False:\n", PROCESS),
    # --- eval: one composition, its two sources --------------------------------------------------
    _m("lw_eval_mode_lost", "the eval role runs the consumer worker's lab_eval as lab-eval",
       "        mode, connect, objects,\n", '        "lab", connect, objects,\n', EVAL),
    _m("lw_eval_objects_dropped", "the eval role's runs read the Lab objects",
       "        mode, connect, objects,\n", "        mode, connect, None,\n", EVAL),
    _m("lw_evaluator_provider_wrong", "an evaluator is read for the provider its ref names",
       'provider_org_id=ref.split(":")[2])', 'provider_org_id=ref.split(":")[3])', EVAL),
    _m("lw_targets_off_the_pool", "L3's rows are read on the role's database",
       "    targets = DevTargets(PgControlStore(connect),",
       '    targets = DevTargets(PgControlStore(connector("")),', EVAL),
    _m("lw_targets_key_ignored", "the dev endpoint is called with the configured credential",
       'key=env["LAB_EVAL_ENDPOINT_KEY"])', 'key="")', EVAL),
    _m("lw_targets_any_environment", "only a dev revision is an evaluation target",
       "        if (not serving or found.environment is not Environment.dev\n",
       "        if (not serving\n", TARGETS),
    _m("lw_targets_public_ok", "only a private revision is an evaluation target",
       "                or found.visibility is not Visibility.private\n", "", TARGETS),
    _m("lw_targets_unpinned", "the deployment must serve the revision the ref pins",
       "                or serving_ref(found, serving) != ref):\n", "                ):\n",
       TARGETS),
    _m("lw_targets_unpriced", "an unpriced dev revision waits (503), never runs free",
       "        if card is None:\n            raise errors.DependencyUnavailable",
       "        if False:\n            raise errors.DependencyUnavailable", TARGETS),
    _m("lw_targets_client_per_run", "one HTTP client serves every run",
       "                               client=self.client), found",
       "                               client=None), found", TARGETS),
    _m("lw_targets_model_wrong", "the call names the serving revision's model",
       "                               model=serving.public_model_id, rate_card=card,",
       "                               model=serving.serving_version_id, rate_card=card,",
       TARGETS),
    # --- checkpoints -------------------------------------------------------------------------------
    _m("lw_checkpoints_without_deployer", "no checkpoint is decided without both sources",
       "    if not registries or deployer is None:\n", "    if deployer is None:\n",
       CKPT_REFUSE),
    _m("lw_checkpoints_any_kind", "the checkpoints handler takes checkpoint_received only",
       '        if event.kind != "checkpoint_received":\n', "        if False:\n", CKPT),
    _m("lw_checkpoints_payload_provider", "a checkpoint is decided for the event's provider",
       "            event.payload[\"checkpoint_id\"], provider_org_id=event.provider_org_id,",
       "            event.payload[\"checkpoint_id\"], "
       "provider_org_id=event.payload.get(\"provider_org_id\"),", CKPT),
    _m("lw_checkpoints_ledger_off_the_pool", "D8's checkpoint ledger is on the role's database",
       '    ledger = lab_sql(mode, "lab_pipeline", "PgCheckpointLedger")(connect)',
       '    ledger = lab_sql(mode, "lab_pipeline", "PgCheckpointLedger")(connector(""))', CKPT),
    _m("lw_checkpoints_cadence", "the checkpoints relay pumps at the Lab pump cadence",
       "every(worker_main.LAB_PUMP_S, relay.pump,", "every(LINEAGE_PASS_S, relay.pump,", CKPT),
    # --- judge ------------------------------------------------------------------------------------
    _m("lw_judge_live_by_default", "the judge is dry_run unless JUDGE_MODE=live",
       "        limits = validate_pilot(pilot_from_env(env))",
       '        limits = validate_pilot(pilot_from_env({"JUDGE_MODE": "live", '
       '"JUDGE_LIVE_BUDGET_USD": "1", **env}))', JUDGE),
    _m("lw_judge_remote_provider", "judge egress past the local fake refuses to start",
       "    except errors.DomainError:\n        raise RuntimeMisconfigured(mode, detail=\"JUDGE",
       "    except KeyError:\n        raise RuntimeMisconfigured(mode, detail=\"JUDGE", JUDGE),
    _m("lw_judge_ledger_off_the_pool", "D6J's ledger is on the role's database",
       "    ledger = PgJudgeLedger(connect)\n", '    ledger = PgJudgeLedger(connector(""))\n',
       JUDGE),
    _m("lw_judge_sweep_threshold", "only a silent submission is made ambiguous",
       "lambda: ledger.sweep(JUDGE_SILENT_S)", "lambda: ledger.sweep(0)", SWEEP),
    # --- datasets ----------------------------------------------------------------------------------
    _m("lw_lineage_every_prefix", "only providers with a lineage are reconciled",
       '                   if key.split("/")[2:3] == ["lineage"]})',
       "                   if True})", DATASETS),
    _m("lw_lineage_first_page_only", "every page of a provider is reconciled",
       "                    if after is None:\n                        break\n",
       "                    if True:\n                        break\n", DATASETS),
    _m("lw_lineage_one_failure_stops_all", "one provider's failure does not skip the others",
       '                report["failed"] += 1\n', "                raise\n", DATASETS),
    # --- rollout ------------------------------------------------------------------------------------
    _m("lw_rollout_pass_idle", "the rollout pass refuses rather than idle",
       '    raise RuntimeMisconfigured(mode, detail="the rollout pass needs every running or "',
       '    return {}, None\n    raise RuntimeMisconfigured(mode, detail="the rollout pass needs '
       'every running or "', ROLLOUT),
    _m("lw_no_reads_answer_nothing", "an unwired L3 read is a typed 503, never an empty answer",
       '        raise errors.DependencyUnavailable("L3\'s control reads are not wired (WR-LSQ-9)")',
       "        return None", ROLLOUT, file=P),
    _m("lw_rollback_without_operator", "an emergency rollback names its operator",
       '    values = settings(mode, env, (DATABASE, "LAB_OPERATOR_ID"))',
       "    values = settings(mode, env, (DATABASE,))", STOP),
    _m("lw_rollback_policy_provider", "the policy is read for the provider its ref names",
       "provider_org_id=match.group(2))", "provider_org_id=match.group(3))", STOP),
    _m("lw_rollback_failure_is_success", "an unfinished rollback exits non-zero",
       "              f\"{failed}\", file=sys.stderr)\n        return 1\n",
       "              f\"{failed}\", file=sys.stderr)\n        return 0\n", STOP),
    _m("lw_rollback_actor_constant", "the controller acts as the operator",
       "                                actor_id=operator)", '                                '
       'actor_id="rollout:controller")', STOP),
    _m("lw_serving_principal_constant", "the alias CAS is audited under the operator",
       "                   OperatorSession(ops=None, principal=principal))",
       '                   OperatorSession(ops=None, principal="rollout:controller"))', STOP,
       file=P),
    # --- annotation / training ------------------------------------------------------------------
    _m("lw_teacher_unapproved", "a teacher host is refused without P-10",
       '    if env.get("LAB_ANNOTATION_TEACHER", "dry-run") != "dry-run":\n', "    if False:\n",
       NO_PASS),
    _m("lw_connector_unapproved", "an automatic connector is refused without P-11",
       '    if env.get("LAB_TRAINING_CONNECTOR", MANUAL) != MANUAL:\n', "    if False:\n",
       NO_PASS),
    # --- the process: health and the drain ---------------------------------------------------------
    _m("lw_ready_without_the_database", "/readyz is down while the database is",
       '            up = live and (path == "/livez" or await _answers(worker.ready))\n',
       "            up = live\n", HEALTH),
    _m("lw_metrics_role_lost", "/metrics names the role",
       "f'infrx_lab_worker_up{{role=\"{worker.role}\"}} {int(live)}\\n'",
       "f'infrx_lab_worker_up{{role=\"lab\"}} {int(live)}\\n'", HEALTH),
    _m("lw_sigterm_is_a_failure", "a stop is a clean exit", "    return 1 if died else 0\n",
       "    return 1\n", HEALTH),
    _m("lw_dead_pass_is_success", "a pass that ended is a failed process",
       "    return 1 if died else 0\n", "    return 0\n", DEAD),
    _m("lw_own_every", "the Lab passes use the consumer worker's `every`",
       "from ...worker.__main__ import every\n",
       "\n\nasync def every(interval_s, step, what, *, sleep=asyncio.sleep):\n"
       "    while True:\n        await step()\n        await sleep(interval_s)\n", EVERY),
)


def case_names() -> set[str]:
    return set(re.findall(r"^def (test_\w+)\(", (API_DIR / SUITE_FILE).read_text(), re.M))


RUNNER = Runner(name="lab-workers", targets=(SUITE_FILE,), layout=w3_mutants._layout)


def run_mutant(mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run the Lab worker entry point's mutants"))
