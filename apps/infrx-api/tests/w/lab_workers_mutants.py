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
T3 = "traces/retention/policy.py"
FILES = (F, P, T3)
C = "test_lab_workers__"
SETTINGS = C + "each_role_refuses_to_start_naming_a_missing_setting"
PROCESS = C + "the_process_refuses_an_unknown_role_and_a_missing_setting"
EVAL = C + "eval_is_the_consumer_workers_one_composition"
TARGETS = C + "dev_targets_resolve_only_the_providers_private_dev_revision"
CKPT_SOURCES = C + "checkpoints_compose_l3s_dev_deployer_and_the_lab_registry"
CKPT = C + "a_checkpoint_delivery_is_decided_by_b3_and_capacity_hands_it_back"
JUDGE = C + "the_judge_is_j2_on_its_ledger_dry_run_by_default"
SWEEP = C + "the_judge_pass_sweeps_silent_submissions"
JPASS = C + "the_judge_pass_reconciles_and_collects_every_providers_runs"
DATASETS = C + "datasets_reconcile_every_providers_lineage_page_by_page"
IMPORTS = C + "the_datasets_role_works_the_durable_import_job_queue"
ROLLOUT = C + "the_rollout_pass_steps_every_released_policy_on_its_stored_plan"
STOP = C + "an_emergency_rollback_is_r2s_for_the_named_operator"
DECIDE = C + "an_operator_decides_a_lab_proposal_through_d9s_cas"
LAUNCH = C + "a_release_is_launched_with_its_plan_stored_first"
B2 = C + "a_running_release_is_stepped_on_its_stored_b2_report"
NO_PASS = C + "training_has_no_pass_and_a_teacher_host_needs_its_approval"
ANNOT = C + "the_annotation_role_collects_teacher_batches_with_n2s_redaction"
COLLECT = C + "the_teacher_pass_collects_every_submitted_run_of_every_approved_batch"
HEALTH = C + "readyz_is_the_database_and_every_pass_alive"
DEAD = C + "a_dead_pass_is_not_live_and_exits_non_zero"
EVERY = C + "the_pumps_are_every_step_forever"
RETENTION = C + "trace_retention_is_t3s_over_the_shippers_bucket_and_bounds"
TEACHER = C + "the_teacher_wiring_is_p2_on_d8s_teacher_ledger"
PUSH = C + "a_trace_deletion_tombstones_every_providers_lineage_copies"
REPORT = C + "the_judge_report_job_publishes_each_configuration_on_its_ledger"


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
    _m("lw_checkpoints_bucket_optional", "the checkpoints role needs the Lab objects",
       '         "checkpoints": (BUCKET,), "judge"', '         "checkpoints": (), "judge"', SETTINGS),
    _m("lw_checkpoints_no_deployer", "the role composes L3's dev deployer by default",
       "    deployer = deployer or checkpoints.DevDeployer(PgControlStore(connect))",
       "    deployer = deployer", CKPT_SOURCES),
    _m("lw_checkpoints_deployer_off_the_pool", "L3's reads are on the role's database",
       "checkpoints.DevDeployer(PgControlStore(connect))",
       'checkpoints.DevDeployer(PgControlStore(connector("")))', CKPT_SOURCES),
    _m("lw_checkpoints_registry_other_provider", "each event gets its own provider's registry",
       "            registries=self.registries(provider), deployer=self.deployer,",
       "            registries=self.registries(None), deployer=self.deployer,", CKPT_SOURCES),
    Mutant(name="lw_checkpoints_registry_other_objects",
           invariant="the Lab registry reads the role's objects", file=F,
           old="        partial(checkpoints.lab_registry, objects)",
           new="        partial(checkpoints.lab_registry, None)", cases=(CKPT_SOURCES,),
           dies_by=("AttributeError",)),
    _m("lw_checkpoints_p3_decided_by_b3", "a checkpoint without B3's signed event is P3's: done",
       "        except errors.NotFound:\n            return True\n",
       "        except errors.NotFound:\n            pass\n", CKPT),
    _m("lw_checkpoints_signed_other_provider", "the signed event is looked up for its provider",
       "            await self.ledger.event(checkpoint_id, provider_org_id=provider)",
       "            await self.ledger.event(checkpoint_id, provider_org_id=None)", CKPT),
    _m("lw_checkpoints_claims_every_kind", "the checkpoints relay claims its own kind (R215)",
       '    relay = OutboxRelay(worker_main.Kinds(store, ("checkpoint_received",)),\n',
       "    relay = OutboxRelay(store,\n", CKPT),
    _m("lw_checkpoints_other_kind", "the checkpoints relay's kind is checkpoint_received",
       'worker_main.Kinds(store, ("checkpoint_received",))',
       'worker_main.Kinds(store, ("eval_run",))', CKPT),
    _m("lw_checkpoints_any_kind", "the checkpoints handler takes checkpoint_received only",
       '        if event.kind != "checkpoint_received":\n', "        if False:\n", CKPT),
    _m("lw_checkpoints_payload_provider", "a checkpoint is decided for the event's provider",
       'checkpoint_id, provider = event.payload["checkpoint_id"], event.provider_org_id',
       'checkpoint_id, provider = event.payload["checkpoint_id"], '
       'event.payload.get("provider_org_id")', CKPT),
    _m("lw_checkpoints_ledger_off_the_pool", "D8's checkpoint ledger is on the role's database",
       '    ledger = lab_sql(mode, "lab_pipeline", "PgCheckpointLedger")(connect)',
       '    ledger = lab_sql(mode, "lab_pipeline", "PgCheckpointLedger")(connector(""))', CKPT),
    _m("lw_checkpoints_cadence", "the checkpoints relay pumps at the Lab pump cadence",
       "every(worker_main.LAB_PUMP_S, relay.pump,", "every(LINEAGE_PASS_S, relay.pump,", CKPT),
    # WR-N4-3 (composition-5): the datasets role's import-job pass
    _m("lw_import_jobs_unscheduled", "the datasets role works the import-job queue",
       '            "import_jobs": lambda: every(IMPORT_PASS_S,',
       '            "import_jobs_off": lambda: every(IMPORT_PASS_S,', IMPORTS),
    _m("lw_import_jobs_cadence", "the queue is claimed every IMPORT_PASS_S",
       '"import_jobs": lambda: every(IMPORT_PASS_S,', '"import_jobs": lambda: every(LINEAGE_PASS_S,',
       IMPORTS),
    _m("lw_import_jobs_off_the_pool", "the job queue is on the role's database",
       "    jobs, store = PgLabImportJobs(connect), PgLabDataStore(connect)",
       '    jobs, store = PgLabImportJobs(connector("")), PgLabDataStore(connect)', IMPORTS),
    _m("lw_import_jobs_other_objects", "imports read and write the role's Lab objects",
       "                jobs, store, objects, worker_id=worker_id)",
       "                jobs, store, None, worker_id=worker_id)", IMPORTS),
    # --- judge ------------------------------------------------------------------------------------
    # WR-LSQ-C2A (composition-5): the collect/reconcile pass
    _m("lw_judge_pass_unscheduled", "the judge role runs its collect/reconcile pass",
       '"judge sweep"),\n            "judge_collect"', '"judge sweep"),\n            "judge_collect_off"',
       JUDGE),
    _m("lw_judge_pass_cadence", "the collect pass runs every JUDGE_PASS_S",
       '"judge_collect": lambda: every(JUDGE_PASS_S,', '"judge_collect": lambda: every(LINEAGE_PASS_S,',
       JPASS),
    _m("lw_judge_pass_providers_off_the_ledger", "the providers are read on the role's ledger",
       "wiring, partial(ledger.providers_in, JUDGE_WORK)),",
       'wiring, partial(PgJudgeLedger(connector("")).providers_in, JUDGE_WORK)),', JPASS),
    _m("lw_judge_pass_providers_other_states", "the providers are those with work the pass does",
       "wiring, partial(ledger.providers_in, JUDGE_WORK)),",
       'wiring, partial(ledger.providers_in, ("submitted",))),', JPASS),
    _m("lw_judge_pass_ambiguous_released", "an ambiguous run the provider lacks is never released",
       "                    elif (external := await wiring.provider.lookup(run.submit_key)) is None:\n"
       "                        done[\"waiting\"] += 1\n",
       "                    elif (external := await wiring.provider.lookup(run.submit_key)) is None:\n"
       "                        await ledger.record_submission(run.run_id, external)\n", JPASS),
    _m("lw_judge_pass_teachers_collected", "a teacher run is the annotation role's",
       '                if run.consent.grant_id.startswith("lab:"):\n                    continue\n',
       "", JPASS),
    _m("lw_judge_pass_submitted_only", "ambiguous runs are reconciled before collection",
       '        for state in JUDGE_WORK:', '        for state in ("submitted",):', JPASS),
    _m("lw_judge_pass_one_failure_stops_all", "one run's failure never stops the pass",
       '                    log.exception("judge pass failed for one run")\n'
       '                    done["failed"] += 1\n', "                    raise\n", JPASS),
    _m("lw_judge_pass_unbounded", "each listing is bounded to JUDGE_BATCH",
       "runs_in((state,), JUDGE_BATCH, provider_org_id=provider)",
       "runs_in((state,), 10_000, provider_org_id=provider)", JPASS),
    _m("lw_judge_pass_other_wiring", "a run is collected on the role's own wiring",
       "                        await submit.collect(run.run_id, wiring=wiring)",
       "                        await submit.collect(run.run_id, wiring=None)", JPASS),
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
    # --- WR-J3-D8-C: the judge report job ----------------------------------------------------
    _m("lw_report_not_composed", "the judge role carries J3's report job",
       '    if role == "judge":                       # WR-J3-D8-C: the sweep\'s ledger\n',
       "    if False:\n", REPORT),
    _m("lw_report_other_ledger", "the report job stores on the sweep's PgJudgeLedger",
       'worker.jobs["judge_report"] = JudgeReport(wiring.ledger)',
       'worker.jobs["judge_report"] = JudgeReport(None)', REPORT),
    _m("lw_report_grantor_is_the_provider", "the report is the grantor's, stored under it",
       'provider_org_id=c["provider_org_id"], org_id=c["org_id"],',
       'provider_org_id=c["provider_org_id"], org_id=c["provider_org_id"],', REPORT),
    _m("lw_report_one_failure_stops_all", "one configuration's failure does not skip the next",
       '                log.exception("judge report failed for one configuration")\n'
       '                done["failed"] += 1\n',
       '                log.exception("judge report failed for one configuration")\n'
       "                raise\n", REPORT),
    # --- datasets ----------------------------------------------------------------------------------
    _m("lw_lineage_every_prefix", "only providers with a lineage are reconciled",
       '                   if key.split("/")[2:3] == ["lineage"]})',
       "                   if True})", DATASETS),
    _m("lw_lineage_first_page_only", "every page of a provider is reconciled",
       "                    if after is None:\n                        break\n",
       "                    if True:\n                        break\n", DATASETS),
    _m("lw_lineage_one_failure_stops_all", "one provider's failure does not skip the others",
       '                report["failed"] += 1\n', "                raise\n", DATASETS),
    # --- WR-N3-2a: a trace deletion pushes N3's tombstones ---------------------------------
    _m("lw_push_not_composed", "the Lab's retention pushes tombstones on a deletion",
       "                     deleted=None if objects is None else lineage_push(objects, connect))",
       "                     deleted=None)", PUSH),
    _m("lw_push_datasets_without_objects", "the datasets role's retention has the Lab objects",
       "    _, retention = _traces(mode, env, objects, connect)\n",
       "    _, retention = _traces(mode, env)\n", DATASETS),
    _m("lw_push_first_page_only", "every page of a provider's copies is tombstoned",
       '**kw))["more"]:', '**kw))["more"] and False:', PUSH),
    _m("lw_push_whole_grantor", "only the deleted request's copies are tombstoned",
       "                    request_id=stone.request_id, reason=",
       "                    request_id=None, reason=", PUSH),
    _m("lw_push_reason_wrong", "a deletion's tombstone says deleted",
       'reason="deleted", at=stone.deleted_at,\n                    **kw))',
       'reason="grant_not_current", at=stone.deleted_at,\n                    **kw))', PUSH),
    _m("t3_hook_skipped", "T3 runs the deletion hook after a new tombstone",
       "        if self.deleted is not None:\n            try:\n",
       "        if False:\n            try:\n", PUSH, file=T3),
    _m("t3_hook_failure_loses_receipt", "a failed push never loses the receipt",
       '                log.exception("the deletion hook failed; the receipt stands")',
       "                raise", PUSH, file=T3),
    _m("t3_hook_on_repeat", "a repeated deletion is the first receipt and pushes nothing",
       "        if existing is not None:\n            return existing\n",
       "        if existing is not None:\n            await self.deleted(existing)\n"
       "            return existing\n", PUSH, file=T3),
    # --- rollout ------------------------------------------------------------------------------------
    # WR-R2-3 (composition-5): the pass loop
    _m("lw_rollout_operator_optional", "the controller's principal is required",
       '         "rollout": (BUCKET, "LAB_OPERATOR_ID"),', '         "rollout": (BUCKET,),',
       SETTINGS),
    _m("lw_rollout_cadence", "the controller pass runs every ROLLOUT_PASS_S",
       "every(ROLLOUT_PASS_S, lambda: rollout_pass(", "every(LINEAGE_PASS_S, lambda: rollout_pass(",
       ROLLOUT),
    _m("lw_rollout_other_actor", "R2's decisions are the named principal's",
       "control_serving(connect, operator), actor_id=operator)",
       'control_serving(connect, operator), actor_id="controller")', ROLLOUT),
    _m("lw_rollout_serving_off_the_pool", "L3's serving control is on the role's database",
       "control_serving(connect, operator), actor_id", 'control_serving(connector(""), operator), actor_id',
       ROLLOUT),
    _m("lw_rollout_every_state", "only running and rolled-back releases are stepped",
       'releases.releases_in(("running", "rolled_back"),', "releases.releases_in((),", ROLLOUT),
    _m("lw_rollout_providers_other_states", "the providers are D9's with a release the pass steps",
       "    for provider in await releases.providers_in(ROLLOUT_STATES):",
       '    for provider in await releases.providers_in(("running",)):', ROLLOUT),
    _m("lw_rollout_planless_stepped", "a release without its stored plan is held",
       "                if raw is None:\n", "                if False:\n", ROLLOUT),
    _m("lw_rollout_invented_live", "a running release is evaluated only on R1's aggregates",
       'current = await live(item) if item.release.state == "running" else None',
       "current = None", ROLLOUT),
    _m("lw_rollout_live_by_default", "without R1's aggregates a running release is held",
       "    store, live = PgLabDataStore(connect), live or NoLive()",
       "    store, live = PgLabDataStore(connect), live or (lambda item: asyncio.sleep(0, item))",
       ROLLOUT),
    _m("lw_rollout_held_is_failed", "an unreadable input holds, it is not a failure",
       "            except errors.DependencyUnavailable:\n                done[\"held\"] += 1\n", "",
       ROLLOUT),
    _m("lw_rollout_one_failure_stops_all", "one release's failure never stops the pass",
       '                log.exception("rollout pass failed for one release")\n'
       '                done["failed"] += 1\n',
       '                raise\n', ROLLOUT),
    _m("lw_rollout_policy_foreign", "the policy is D7's record of the release's provider",
       "store.resolve(item.policy_ref, provider_org_id=provider)",
       'store.resolve(item.policy_ref, provider_org_id="")', ROLLOUT),
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
    # --- WR-C5-REPORT (composition-6): a running release is stepped on its B2 report ----------
    _m("lw_report_never_read", "a running release is stepped on its stored B2 report",
       "                report, runs = await release_report(reads, store, provider, policy, plan) \\\n"
       "                    if current is not None else (None, None)\n",
       "                report, runs = None, None\n", B2),
    _m("lw_report_rolled_back_reads", "a rolled-back release reads no report (converge only)",
       "                    if current is not None else (None, None)\n",
       "                    if True else (None, None)\n", B2),
    _m("lw_report_any_protocol", "the report is under the plan's own protocol",
       '        if e["report"] is None or e["protocol_digest"] != protocol:\n',
       '        if e["report"] is None:\n', B2),
    _m("lw_report_unreported", "an experiment without a stored report is skipped",
       '        if e["report"] is None or e["protocol_digest"] != protocol:\n',
       '        if e["protocol_digest"] != protocol:\n', B2),
    _m("lw_report_other_servings", "the report compares the policy's baseline and a candidate",
       '        if runs[0]["serving_ref"] == policy.baseline_ref and \\\n', "        if True or \\\n",
       B2),
    _m("lw_report_oldest", "the newest matching experiment is the release's",
       "    for e in await reads.experiments(provider_org_id=provider):",
       "    for e in reversed(await reads.experiments(provider_org_id=provider)):", B2),
    _m("lw_report_run_refs_changed", "the runs are D7's records as stored (their refs)",
       '.model_dump(mode="json", by_alias=True, exclude_unset=True)   # its ref',
       '.model_dump(mode="json", by_alias=True)', B2),
    _m("lw_report_off_the_pool", "B4's experiments are read on the role's database",
       "    reads = PgLabReads(connect)", '    reads = PgLabReads(connector(""))', B2),
    # --- WR-C5-PLAN (composition-6): the release launcher stores the plan, then D9 starts ------
    _m("lw_launch_without_bucket", "the launcher needs the Lab bucket the plan is stored in",
       '    values = settings(mode, env, (DATABASE, BUCKET, "LAB_OPERATOR_ID"))',
       '    values = settings(mode, env, (DATABASE, "LAB_OPERATOR_ID"))', LAUNCH),
    _m("lw_launch_plan_unvalidated", "only R2's plan is launched",
       "            plan = Plan.model_validate_json(stored.read())",
       "            plan = Plan.model_construct(**json.loads(stored.read()))", LAUNCH),
    _m("lw_launch_plan_not_stored", "the plan is stored beside the release before D9 starts it",
       "        await write_once(lab_objects(mode, env), plan_key(provider, policy.policy_id),\n"
       "                         plan.model_dump_json().encode())\n", "", LAUNCH),
    _m("lw_launch_plan_elsewhere", "the plan is stored where the pass and the page read it",
       "plan_key(provider, policy.policy_id),", "plan_key(provider, policy_ref),", LAUNCH),
    _m("lw_launch_digest_other", "D9 freezes the stored plan's digest",
       "                                            plan_digest=plan_digest(plan),",
       '                                            plan_digest="sha256:" + "0" * 64,', LAUNCH),
    _m("lw_launch_other_actor", "the launch is the operator's decision",
       'decided_by=values["LAB_OPERATOR_ID"], reason=reason)',
       'decided_by="launcher", reason=reason)', LAUNCH),
    _m("lw_launch_refusal_escapes", "a refused launch is a non-zero exit",
       '        print(f"infrx.lab.workers: the release was not launched: {failed.code}: {failed}",\n'
       "              file=sys.stderr)\n        return 1\n", "        raise\n", LAUNCH),
    _m("lw_launch_unparsed", "launch names its plan",
       '    if args.command == "launch" and not args.plan:\n', "    if False:\n", LAUNCH),
    # --- WR-R4-2 (composition-6): the operator decides a Lab proposal through D9's CAS ---------
    _m("lw_decide_without_operator", "a decision names its operator",
       '    mode, needs = "lab-rollout", (DATABASE, "LAB_OPERATOR_ID")',
       '    mode, needs = "lab-rollout", (DATABASE,)', DECIDE),
    _m("lw_decide_other_release", "a proposal is decided only for the release the ref names",
       ' and p["policy_ref"] == policy_ref),', "),", DECIDE),
    _m("lw_decide_other_provider", "the proposals are the ref's provider's",
       "await proposals.proposals(provider_org_id=provider)",
       'await proposals.proposals(provider_org_id="")', DECIDE),
    _m("lw_decide_reject_approves", "a rejection moves nothing",
       "            await proposals.decide(proposal_id, approve=False, decided_by=operator)",
       "            await proposals.decide(proposal_id, approve=True, decided_by=operator)",
       DECIDE),
    _m("lw_decide_expand_without_live", "an expansion needs R2's verdict on R1's aggregates",
       '        if found["kind"] != "rollback":\n', "        if False:\n", DECIDE),
    _m("lw_decide_other_actor", "the decision is the operator's",
       "await proposals.decide(proposal_id, approve=True, decided_by=operator, decision={",
       'await proposals.decide(proposal_id, approve=True, decided_by="ops", decision={', DECIDE),
    _m("lw_decide_reasons_unnamed", "the decision names the operator's reason and the proposal",
       '            reasons=(f"operator:{reason}", f"proposal:{proposal_id}"))',
       "            reasons=())", DECIDE),
    _m("lw_decide_no_converge", "an approved rollback converges the alias (R2's stop)",
       "        await controller.emergency_rollback(operator, policy, policy_ref, now=now, "
       "reason=reason)\n", "", DECIDE),
    _m("lw_decide_refusal_escapes", "a refused CAS is a non-zero exit, not a crash",
       '        print(f"infrx.lab.workers: the proposal {outcome}: {failed.code}: {failed}",\n'
       "              file=sys.stderr)\n        return 1\n",
       '        raise\n', DECIDE),
    _m("lw_decide_outcome_unfollowed", "the refusal message follows the outcome (WR-C6-F2)",
       "        decided = True                        # committed: a later failure is "
       "converge-only\n", "", DECIDE),
    _m("lw_decide_unparsed", "decide names a proposal and approve or reject",
       '    if args.command == "decide" and not (args.proposal_id and (args.approve or '
       'args.reject)):\n', "    if False:\n", DECIDE),
    # --- annotation / training ------------------------------------------------------------------
    _m("lw_teacher_unapproved", "a teacher host is refused without P-10",
       '    if env.get("LAB_ANNOTATION_TEACHER", "dry-run") != "dry-run":\n', "    if False:\n",
       NO_PASS),
    _m("lw_connector_unapproved", "an automatic connector is refused without P-11",
       '    if env.get("LAB_TRAINING_CONNECTOR", MANUAL) != MANUAL:\n', "    if False:\n",
       NO_PASS),
    # --- WR-DS5-2 (composition-4): the reconcile pass's explicit 0041 port --------------------
    _m("lw_reconcile_restrictions_implicit", "the reconcile pass passes PgSampleRestrictions",
       "provider_org_id=provider, after=after,\n"
       "                                                     restrictions=restrictions))",
       "provider_org_id=provider, after=after))", DATASETS),
    _m("lw_reconcile_restrictions_off_the_login", "0041 is written on the directory's login",
       "directory, restrictions = PgAccessStore(connect), PgSampleRestrictions(connect)",
       'directory, restrictions = PgAccessStore(connect), PgSampleRestrictions(connector(""))',
       DATASETS),
    # --- WR-P4B-2 (composition-4): the annotation role's collect pass --------------------------
    _m("lw_annotation_teacher_url_optional", "the annotation role needs its teacher's URL",
       '"annotation": (BUCKET, "LAB_TEACHER_URL"),', '"annotation": (BUCKET,),', SETTINGS),
    _m("lw_annotation_unredacted", "the collected teacher saw only N2's redaction (WR-P2-4)",
       "settings=pilot, redact=redact_content)", "settings=pilot, redact=str)", ANNOT),
    _m("lw_annotation_live_by_default", "the role's judge mode is its environment's (dry run)",
       "settings=pilot, redact=redact_content)",
       'settings=pilot.replace(judge_mode="live"), redact=redact_content)', ANNOT),
    _m("lw_annotation_other_host_escapes", "another teacher host refuses by the setting's name",
       '    except errors.DomainError:            # names the setting, never its value\n'
       '        raise RuntimeMisconfigured(mode, detail="LAB_TEACHER_URL: teacher egress is the local "',
       '    except KeyError:\n'
       '        raise RuntimeMisconfigured(mode, detail="LAB_TEACHER_URL: teacher egress is the local "',
       NO_PASS),
    _m("lw_annotation_pass_idle", "the annotation role's pass is collect_teachers",
       "lambda: every(TEACHER_PASS_S, lambda: collect_teachers(wiring),",
       "lambda: every(TEACHER_PASS_S, lambda: asyncio.sleep(0),", ANNOT),
    _m("lw_collect_unapproved_batches", "only an approved batch is collected",
       'parts[-1] != "approval.json":', 'parts[-1] != "batch.json":', COLLECT),
    _m("lw_collect_every_state", "only a submitted run is collected",
       '"state", None) == "submitted"]', '"state", None) is not None]', COLLECT),
    _m("lw_collect_as_the_requester", "the batch is collected as its approver",
       '["approved_by"])', '["approved_by"] and stored["requested_by"])', COLLECT),
    _m("lw_collect_one_failure_stops_all", "one run's failure never skips the next",
       '                log.exception("teacher collect failed for one run")\n'
       '                done["failed"] += 1\n',
       '                log.exception("teacher collect failed for one run")\n'
       '                done["failed"] += 1\n                break\n', COLLECT),
    _m("lw_collect_uncounted", "each collected run is counted",
       '                await p2.collect(batch, run_id, wiring=wiring)\n'
       '                done["collected"] += 1\n',
       '                await p2.collect(batch, run_id, wiring=wiring)\n'
       "                pass\n", COLLECT),
    # --- WR-P2-D8-C: the teacher wiring ------------------------------------------------------
    _m("lw_teacher_plain_judge_ledger", "P2's ledger is D8's PgTeacherLedger (record_failures)",
       "ledger=PgTeacherLedger(connect),", "ledger=PgTeacherLedger.__mro__[1](connect),",
       TEACHER),
    _m("lw_teacher_log_off_the_pool", "P1's label log is on the role's database",
       "log=PgLabelLog(connect),", 'log=PgLabelLog(connector("")),', TEACHER),
    _m("lw_teacher_redaction_dropped", "the teacher sees content only through N2's redaction",
       "                         redact=redact)", "                         redact=str)",
       TEACHER),
    _m("lw_teacher_rates_unapproved", "a live teacher is priced by the approved rates only",
       "rates=APPROVED_RATES if rates is None else rates,", "rates=rates,", TEACHER),
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
    # --- the judge/datasets roles' trace retention ------------------------------------------
    _m("lw_trace_prefix_lost", "the trace bucket is read at the shipper's prefix",
       'limits.s3_trace_bucket, "infrx/", endpoint_url)', 'limits.s3_trace_bucket, "", '
       'endpoint_url)', RETENTION),
    _m("lw_trace_content_days_lost", "content is kept for the pilot's content bound",
       "content_days=limits.trace_content_max_days,", "content_days=30,", RETENTION),
    _m("lw_trace_metadata_months_lost", "metadata is kept for the pilot's metadata bound",
       "metadata_months=limits.trace_metadata_months,", "metadata_months=12,", RETENTION),
)


def case_names() -> set[str]:
    return set(re.findall(r"^def (test_\w+)\(", (API_DIR / SUITE_FILE).read_text(), re.M))


RUNNER = Runner(name="lab-workers", targets=(SUITE_FILE,), layout=w3_mutants._layout)


def run_mutant(mutant) -> Result:
    return shared.run_mutant(mutant, RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run the Lab worker entry point's mutants"))
