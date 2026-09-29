#!/usr/bin/env python3
"""R32/R83 for I2B-R4: every invariant `test_worker_main.py` claims is killable by a named
case, through the shared runner (`tests/contracts/mutants.py`).

Two lists. `MUTANTS` names only the cases that need no service (the composition, the
listener over E2's fake vLLM, the refusing process), so it runs anywhere. `PG_MUTANTS` names
the `_pg__` cases: the copy inherits the lane's harness settings (`INFRX_D_TASK`, its Valkey,
its MinIO), so it provisions that task's own containers, never the default ones.

    uv run --frozen pytest -q tests/w/test_worker_main_mutants.py
    INFRX_MUTANTS=all INFRX_D_TASK=d4 INFRX_D2_VALKEY_PORT=55465 \\
      INFRX_D2_VALKEY_CONTAINER=infrx-worker-valkey INFRX_M_S3_ENDPOINT=http://127.0.0.1:55781 \\
      INFRX_M_S3_LOCAL_CREDS=1 uv run --frozen pytest -q tests/w/test_worker_main_mutants.py
    uv run --frozen python -m tests.w.worker_main_mutants --list
"""
from __future__ import annotations

import pathlib
import re
import shutil

from ..contracts import mutants as shared
from ..contracts.mutants import Result, Runner, _m
from . import w3_mutants

API_DIR = shared.API_DIR
SUITE_FILE = "tests/w/test_worker_main.py"
MAIN = "worker/__main__.py"
SERVICE = "worker/service.py"
PILOT = "gateway/pilot.py"

REFUSALS = "test_worker_main__each_missing_setting_refuses_startup_by_name"
COMPOSITION = "test_worker_main__the_composition_is_the_pilots_stores_and_settings"
LOCAL_URI = "test_worker_main__local_uri_finds_the_file_the_gateway_prepared"
BUILD_INFO = "test_worker_main__build_info_is_the_installed_settings_never_git"
READYZ = "test_worker_main__readyz_waits_for_the_engine_and_metrics_are_served"
PROCESS = "test_worker_main__the_process_refuses_to_start_naming_the_setting"
ROUND_TRIP = "test_worker_main_pg__a_job_the_gateway_admitted_runs_in_the_worker_process"
DRAIN = "test_worker_main_pg__sigterm_drains_the_in_flight_job_and_exits_0"
UNREACHABLE = "test_worker_main_pg__an_unreachable_database_refuses_before_readiness"
PILOT_BOX = "test_worker_main__the_pilot_box_runs_the_real_entry_point"
PILOT_BOX_PG = "test_worker_main_pg__the_pilot_box_starts_the_real_worker_and_waits_for_it"
PB = "../../../tests/integration/backend/pilotbox.py"      # E3B's pilot box, from `infrx/`
ENGINE = "worker/engine.py"
OWNER = "test_worker_main__the_worker_is_the_one_owner_of_housekeeping"
KEEPER = "test_worker_main__the_keeper_never_removes_an_input_the_engine_is_reading"
GONE = "test_worker_main__a_gone_input_is_prepared_again_once_then_refused"
EXITS = "test_worker_main__every_exit_path_releases_every_pin"
EVERY = "test_worker_main__a_housekeeping_loop_outlives_a_failed_step"
GRACE = "test_worker_main__the_lifecycle_grace_is_the_deployments"
P25_CACHE = "test_worker_main__the_cache_high_water_and_its_alert_are_p25s"
CONFIG = "config.py"
OPS = "../../../infra/alerts/operations.json"                # from `infrx/`
GATEWAY_GRACE = "test_worker_main__the_gateways_content_grace_is_the_deployments"
GATEWAY_GRACE_PG = ("test_worker_main_pg__a_source_the_gateway_registers_is_eligible_"
                    "after_p25s_grace")
RELEASE = "                stream.pins.close()\n                # Every exit path"
RECON_OFF = "test_worker_main__without_a_monitor_login_the_reconciliation_gauges_are_off"
RECON_REFUSED = "test_worker_main__a_login_refused_the_views_disables_the_gauges_once"
RECON_DOWN = "test_worker_main__a_database_that_is_down_is_still_retried_every_tick"
RECON_MONITOR = "test_worker_main__the_reconciliation_gauges_are_read_on_the_monitor_login"
RECON_PG = "test_worker_main_pg__the_monitor_login_reads_what_the_runtime_login_may_not"
# WR-T-4 / WR-B-5 (composition lane, LW2)
SWITCHES_OFF = "test_worker_main__every_trace_and_lab_switch_is_off_and_composes_nothing"
TRACE_REFUSE = "test_worker_main__trace_pumps_refuse_to_start_without_their_settings"
TRACE_ON = "test_worker_main__trace_pumps_ship_retain_and_project_on_the_workers_stores"
TRACE_HOLDS = "test_worker_main__trace_pumps_refuse_without_c2s_content_refs"
SHIPPER_HOLDS = "test_worker_main__the_shippers_retention_holds_what_it_was_given"
SHIPPER = "traces/ship/shipper.py"
LAB_REFUSE = "test_worker_main__the_lab_eval_worker_refuses_to_start_without_its_sources"
LAB_ON = "test_worker_main__the_lab_eval_worker_pumps_d7s_outbox_and_recovers"
RESUME = "test_worker_main__an_eval_run_delivery_resumes_the_created_run_never_freezes"
PENDING = "test_worker_main__a_delivery_the_handler_cannot_finish_stays_pending"
UNFINISHED = "test_worker_main__a_run_left_unfinished_is_not_acknowledged"
FOREIGN_REF = ("test_worker_main__a_run_naming_another_providers_ref_is_not_found_before_any_"
               "source")

MUTANTS = (
    _m("main_validate_runtime_skipped", "the worker refuses what the gateway refuses (R44)",
       MAIN, "    mode = validate_runtime(settings)\n", "    mode = runtime_mode(settings)\n",
       REFUSALS),
    _m("main_database_url_not_required", "no DATABASE_URL, no worker, in every mode",
       MAIN, '(("DATABASE_URL", limits.database_url),\n'
             '                                        ("VALKEY_URL"', '(("VALKEY_URL"',
       REFUSALS),
    _m("main_cache_dir_unchecked", "PROCESSING_CACHE_DIR is an absolute writable directory",
       MAIN, "    if not (os.path.isabs(root) and os.path.isdir(root)\n"
             "            and os.access(root, os.R_OK | os.W_OK | os.X_OK)):",
       "    if False:", REFUSALS),
    _m("main_store_defaulted_to_in_memory",
       "no S3_MEDIA_BUCKET, no worker: the object store is never process memory (M1-L2)",
       MAIN, "    objects = objects if objects is not None else pilot.object_store(settings)\n",
       "    objects = objects if objects is not None else __import__(\n"
       '        "infrx.media.store", fromlist=["x"]).InMemoryObjectStore()\n', REFUSALS),
    _m("main_credit_work_absent", "a CREDIT deployment runs W's runner through the CREDIT doors",
       MAIN, "    jobs = CreditWork(store) if deployment.accounting_regime == CREDIT_REGIME "
             "else store\n", "    jobs = store\n", COMPOSITION),
    _m("main_local_uri_without_the_shared_cache",
       "local_uri resolves through the shared processing cache (M's pilot request)",
       MAIN, "                             cache=ProcessingCache(root, "
             "ttl_s=limits.processing_cache_ttl_s,\n",
       "                             cache=ProcessingCache(__import__('tempfile').mkdtemp(), "
       "ttl_s=limits.processing_cache_ttl_s,\n", LOCAL_URI),
    _m("main_build_info_from_git", "the build gauge is the installed setting, never git",
       PILOT, 'rt.metrics.set("infrx_build_info", 1, revision=deployment.infrx_release_sha,',
       'rt.metrics.set("infrx_build_info", 1, revision=__import__("subprocess").run('
       '["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),',
       BUILD_INFO),
    _m("main_readiness_before_the_engine_check", "/readyz is 200 only while the engine answers",
       SERVICE, '        return {"ready": live and engine_up and state in ("idle", "busy"),',
       '        return {"ready": live and state in ("idle", "busy"),', READYZ),
    _m("main_metrics_not_served", "the worker's listener serves its build gauge",
       SERVICE, "            if method == \"GET\" and path == METRICS_PATH and self.metrics is "
                "not None:", "            if False:", READYZ),
    _m("main_refusal_exits_zero", "a startup refusal is a non-zero exit (2)",
       MAIN, '        print(f"infrx.worker: refusing to start: {refused}", file=sys.stderr)\n'
             "        return REFUSED\n",
       '        print(f"infrx.worker: refusing to start: {refused}", file=sys.stderr)\n'
       "        return 0\n", PROCESS),
    # --- M6-WIRING: the one owner of housekeeping (wiring 1 + E3C F-4) -------------------
    _m("main_retention_not_scheduled", "the worker runs the retention collector",
       MAIN, '        "retention": lambda: collector.run(deployment.retention_interval_s, '
             'metrics=metrics),\n', "", OWNER),
    _m("main_retention_unrecorded", "the collector records its passes on the worker registry",
       MAIN, "collector.run(deployment.retention_interval_s, metrics=metrics)",
       "collector.run(deployment.retention_interval_s)", OWNER),
    _m("main_journal_never_pruned", "the worker prunes the stream journal (F-4)",
       MAIN, '        "journal_expire": lambda: every(', '        "journal_expire_": lambda: every(',
       OWNER),
    _m("main_journal_prune_one_call", "one prune pass drains everything past its TTL",
       MAIN, "    while found := await journal.expire():", "    if found := await journal.expire():",
       OWNER),
    _m("main_content_unregistered", "prepared artifacts register with the worker's lifecycle",
       MAIN, "media = MediaPreparation(objects, limits=limits, content=lifecycle,",
       "media = MediaPreparation(objects, limits=limits,", OWNER),
    _m("main_cache_unbounded", "the cache's high water is PROCESSING_CACHE_MAX_BYTES",
       MAIN, "max_bytes=deployment.processing_cache_max_bytes,", "max_bytes=None,", OWNER),
    # --- P25-ENACT (P-25, decided 2026-09-25) ----------------------------------------------
    _m("main_retention_grace_dropped", "the worker's lifecycle carries the deployment's grace",
       MAIN, "    lifecycle = PgLifecycle(connect, limits=limits,       # claim TTL 300 s > the 75 s delete\n"
             "                            grace_s=deployment.retention_grace_s)     # P-25: 3,600 s\n",
       "    lifecycle = PgLifecycle(connect, limits=limits)\n", GRACE),
    _m("main_retention_grace_fixed", "the grace is read from RETENTION_GRACE_S",
       MAIN, "grace_s=deployment.retention_grace_s)", "grace_s=3600.0)", GRACE),
    _m("config_retention_grace_a_week", "the deployed grace is P-25's 3,600 s",
       CONFIG, "    retention_grace_s: float = 3600.0\n",
       "    retention_grace_s: float = 604_800.0\n", GRACE),
    _m("config_cache_high_water_60gib", "the cache high water is P-25's 50 GiB",
       CONFIG, "    processing_cache_max_bytes: int = 53_687_091_200\n",
       "    processing_cache_max_bytes: int = 64_424_509_440\n", P25_CACHE),
    _m("alert_cache_threshold_drifts", "ProcessingCacheLarge fires above the same high water",
       OPS, '"threshold": 53687091200,', '"threshold": 64424509440,', P25_CACHE),
    # --- P25-ENACT fix round (0-P25R-1/1-P25R-1); the runbook cases: tests/w/test_p25_runbooks.py
    # WR-P25-1 (coordinator wiring): the gateway's lifecycle takes the deployment's grace
    _m("gateway_grace_dropped", "the gateway's content lifecycle stamps RETENTION_GRACE_S",
       PILOT, "    return PgLifecycle(connect, limits=settings.pilot,\n"
              "                       grace_s=settings.deployment.retention_grace_s)\n",
       "    return PgLifecycle(connect, limits=settings.pilot)\n", GATEWAY_GRACE),
    _m("main_housekeeping_started_twice", "exactly one task per housekeeping loop",
       SERVICE, "for name, loop in self.housekeeping.items()]",
       "for name, loop in [*self.housekeeping.items()] * 2]", OWNER),
    _m("main_housekeeping_outlives_the_drain", "housekeeping is cancelled with the service",
       SERVICE, "        for task in (self._reaper, *self._housekeeping):",
       "        for task in (self._reaper,):", OWNER),
    # --- M6-WIRING: the engine holds its inputs (wiring 2) ---------------------------------
    _m("engine_media_not_pinned", "the engine pins every input from submit to terminal",
       ENGINE, "        await self._hold_media(stream)                   # released by `_generate`\n",
       "", KEEPER, GONE),
    _m("engine_pin_never_released", "a terminal attempt releases its pins",
       ENGINE, RELEASE, "                # Every exit path", KEEPER, EXITS),
    # --- M6-WIRING fix round (0-M6W-C1..C6) ------------------------------------------------
    _m("engine_pins_released_on_success_only", "every exit path releases the pins (C1)",
       ENGINE, RELEASE, "                stream.pins.close() if stream.complete else None\n"
                        "                # Every exit path", EXITS),
    _m("engine_close_raising_leaks_the_pins", "a close that raises still releases (C3)",
       ENGINE, "            try:\n                await inner.aclose()\n            finally:\n",
       "            await inner.aclose()\n            if True:\n", EXITS),
    _m("engine_pins_released_before_the_upstream_close",
       "the pins outlive the upstream response (C3)",
       ENGINE, "            try:\n                await inner.aclose()\n",
       "            stream.pins.close()\n            try:\n                await inner.aclose()\n",
       EXITS),
    _m("engine_only_the_first_input_pinned", "every file:// input of a request is pinned (C5)",
       ENGINE, "                for ref in videos:\n                    uri = str(self.local_uri(ref))",
       "                for ref in videos[:1]:\n                    uri = str(self.local_uri(ref))",
       EXITS),
    _m("engine_reprepare_for_another_job", "a gone input is prepared for the attempt's job (C4)",
       ENGINE, "await self.reprepare(stream.lease.job_id, videos[0].profile_version)",
       "await self.reprepare(str(__import__('uuid').uuid4()), videos[0].profile_version)",
       GONE),
    _m("main_housekeeping_loop_dies_on_a_failed_step",
       "a failed step does not end a housekeeping loop (C2)",
       MAIN, "        try:\n            await step()\n        except Exception:\n"
             '            log.exception("%s failed", what)\n', "        await step()\n", EVERY),
    _m("main_housekeeping_loop_stops_after_a_failure",
       "a housekeeping loop runs on after a failed step (C2)",
       MAIN, '            log.exception("%s failed", what)\n',
       '            log.exception("%s failed", what)\n            return\n', EVERY),
    _m("main_housekeeping_cancelled_before_the_drain",
       "housekeeping runs until the in-flight attempts have drained (C6)",
       SERVICE, "        report = await self.loop.drain(bound)\n",
       "        for task in self._housekeeping:\n            task.cancel()\n"
       "        report = await self.loop.drain(bound)\n", OWNER),
    # E1B WREQ-1: the inference loop publishes each attempt's phase timings; preparation none
    _m("main_loop_phases_unobserved", "the inference loop observes phases on the worker Registry",
       MAIN, "limits=limits,\n                      metrics=rt.metrics)", "limits=limits)",
       COMPOSITION),
    _m("main_preparation_phases_observed", "the preparation loop holds no Registry",
       MAIN, "                                 limits=limits, readiness=lifecycle),\n        limits=limits)",
       "                                 limits=limits, readiness=lifecycle),\n"
       "        limits=limits, metrics=rt.metrics)", COMPOSITION),
    _m("main_engine_unpinned", "the composed engine pins through the composed cache",
       MAIN, "pin=media.cache.pin, reprepare=", "pin=None, reprepare=", KEEPER, OWNER),
    _m("engine_gone_input_refused_at_once", "a gone input is prepared again once",
       ENGINE, "                if again or self.reprepare is None:", "                if True:",
       GONE),
    _m("engine_gone_input_prepared_forever", "a gone input is prepared again only once",
       ENGINE, "        for again in (False, True):", "        for again in (False, False, True):",
       GONE),
    _m("main_reprepare_keeps_the_job_map", "re-preparation leaves no per-job entry behind",
       MAIN, "        media.prepared_by_job.pop(job_id, None)   # nothing in this process reads it (F3)\n",
       "", GONE),
    # item 3: E3B's pilot box runs the real entry point
    _m("pilotbox_worker_emulated", "the pilot box's worker process is python -m infrx.worker",
       PB, '        if role == "worker":\n', "        if False:\n", PILOT_BOX),
    _m("pilotbox_worker_imports_the_checkout", "a mutation copy on PYTHONPATH is what the "
       "box's processes import", PB,
       '(inherited.get("PYTHONPATH"), str(harness.API_ROOT))',
       '(str(harness.API_ROOT), inherited.get("PYTHONPATH"))', PILOT_BOX),
    _m("pilotbox_worker_private_namespace", "the worker and the gateway share the pilot's "
       "index namespace", PB, "port: int, namespace: str = PILOT_NAMESPACE) -> None:",
       'port: int, namespace: str = "infrx_e2:{e3b3}") -> None:', PILOT_BOX),
    # W5-F5 (E3C F-6): the reconciliation gauges on D10's monitor login, never per-tick errors
    _m("main_reconciliation_on_the_runtime_pool",
       "the reconciliation reader is never composed on the runtime pool (0021:550)",
       MAIN, "reconciliation=reconciliation_reader(deployment),",
       "reconciliation=PgReconciliation(connect),", RECON_OFF, RECON_MONITOR),
    _m("main_reconciliation_off_unsaid", "gauges with no monitor login are said off, once",
       MAIN, '        log.info("reconciliation gauges disabled: no monitor login")\n', "",
       RECON_OFF),
    _m("main_monitor_login_sets_a_role", "a dedicated monitor login sets no role (R127)",
       MAIN, "connector(dsn, set_role=not pilot.dedicated_login(dsn))",
       "connector(dsn, set_role=True)", RECON_MONITOR),
    _m("service_privilege_refusal_every_tick",
       "a login refused the views disables the gauges once, never an error per tick",
       SERVICE, '            if getattr(failure, "sqlstate", None) == "42501":',
       "            if False:", RECON_REFUSED),
    _m("service_any_failure_disables", "a database that is down is retried every tick",
       SERVICE, '            if getattr(failure, "sqlstate", None) == "42501":',
       "            if True:", RECON_DOWN),
    # --- WR-T-4 / WR-B-5 (composition lane, LW2): each switch off, and what on composes ---
    _m("main_trace_pumps_on_by_default", "TRACE_PUMPS is off unless the deployment sets it",
       CONFIG, "    trace_pumps: bool = False\n", "    trace_pumps: bool = True\n", SWITCHES_OFF),
    _m("main_lab_eval_on_by_default", "LAB_EVAL_WORKER is off unless the deployment sets it",
       CONFIG, "    lab_eval_worker: bool = False\n", "    lab_eval_worker: bool = True\n",
       SWITCHES_OFF),
    _m("main_trace_switch_ignored", "TRACE_PUMPS off composes no spool, client or pump",
       MAIN, "    if deployment.trace_pumps:  ", "    if True:  ", SWITCHES_OFF),
    _m("main_lab_switch_ignored", "LAB_EVAL_WORKER off composes no Lab store or pump",
       MAIN, "    if deployment.lab_eval_worker:  ", "    if True:  ", SWITCHES_OFF),
    _m("main_trace_switch_composes_nothing", "TRACE_PUMPS on runs the three trace pumps",
       MAIN, "        chores |= trace_pumps(settings, mode, connect)\n", "        pass\n",
       TRACE_ON),
    _m("main_lab_switch_composes_nothing", "LAB_EVAL_WORKER on runs the Lab pump and recover",
       MAIN, "        chores |= lab_eval(mode, connect, objects, evaluators, targets, "
             "worker_id)\n", "        pass\n", LAB_ON),
    # R215 / WR-LSQ-C2B (composition-5): the eval relay claims its own kind only
    _m("main_lab_eval_claims_every_kind", "the eval relay claims eval_run events only",
       MAIN, '    relay = OutboxRelay(Kinds(store, ("eval_run",)),\n',
       "    relay = OutboxRelay(store,\n", LAB_ON),
    _m("main_lab_eval_other_kind", "the eval relay's kind is eval_run",
       MAIN, 'Kinds(store, ("eval_run",))', 'Kinds(store, ("checkpoint_received",))', LAB_ON),
    _m("main_kinds_unfiltered", "a role's relay passes its kinds to 0050's claim",
       MAIN, "        return await self.store.dispatch_pending(**kw, kinds=self.kinds)",
       "        return await self.store.dispatch_pending(**kw)", LAB_ON),
    _m("main_trace_settings_not_required", "TRACE_PUMPS on refuses without its three settings",
       MAIN, "    if missing:\n        raise RuntimeMisconfigured(mode, missing)\n    holds = ",
       "    holds = ", TRACE_REFUSE),
    _m("main_trace_ship_without_rotate", "each ship pass seals the spool's tail first",
       MAIN, "        await spool.rotate()\n        return await shipper.ship()\n",
       "        return await shipper.ship()\n", TRACE_ON),
    _m("main_trace_sweep_without_expire", "each retention pass expires, then sweeps",
       MAIN, "        await retention.expire()\n        return await retention.sweep()\n",
       "        return await retention.sweep()\n", TRACE_ON),
    _m("main_trace_shipper_endpoint_ignored", "the trace bucket is reached at the deployment's "
       "S3 endpoint", MAIN,
       "                                     endpoint_url=settings.deployment.s3_endpoint_url,\n",
       "", TRACE_ON),
    _m("main_feedback_projection_off_the_pool", "the feedback relay runs on the worker's pool",
       MAIN, "    projector = FeedbackProjector(PgFeedbackOutbox(connect), retention.feedback,",
       "    projector = FeedbackProjector(PgFeedbackOutbox(connector(limits.database_url)), "
       "retention.feedback,", TRACE_ON),
    _m("main_feedback_projection_ignores_retention", "a deleted request's feedback is not "
       "projected again (T3's keep_feedback)", MAIN,
       "                                  retention=retention)\n",
       "                                  retention=None)\n", TRACE_ON),
    _m("main_lab_sources_not_required", "LAB_EVAL_WORKER refuses without its two sources",
       MAIN, "    if evaluators is None or targets is None:\n", "    if False:\n", LAB_REFUSE),
    _m("main_lab_store_off_the_pool", "D7's store runs on the worker's pool",
       MAIN, "    store = PgLabDataStore(connect)\n",
       '    store = PgLabDataStore(connector(""))\n', LAB_ON),
    _m("main_lab_recover_not_scheduled", "expired Lab leases are recovered on a timer",
       MAIN, ',\n            "lab_recover": lambda: every(LAB_RECOVER_S, store.recover, '
             '"lab recover")}\n', "}\n", LAB_ON),
    _m("main_redelivery_freezes_again", "a delivery resumes the created run, never freezes it",
       MAIN, "        frozen = await evaluation.resume(self.store, run_id,\n",
       "        frozen = await evaluation.freeze(self.store, run_id,\n", RESUME),
    _m("main_eval_provider_from_the_payload", "the run is read for the event's own provider",
       MAIN, '        provider, run_id = event.provider_org_id, event.payload["run_id"]\n',
       '        provider, run_id = event.payload.get("provider_org_id"), '
       'event.payload["run_id"]\n', RESUME),
    _m("main_evaluator_by_the_serving_ref", "the evaluator is the one the run record names",
       MAIN, "evaluator=await self.evaluators(record.evaluator_ref),",
       "evaluator=await self.evaluators(record.serving_ref),", RESUME),
    _m("main_wallet_stop_acknowledged", "a wallet stop stays pending for redelivery",
       MAIN, '        if report["stopped"] == "wallet_exhausted":\n', "        if False:\n",
       PENDING),
    _m("main_every_stop_pending", "a budget stop is final and acknowledged",
       MAIN, '        if report["stopped"] == "wallet_exhausted":\n',
       '        if report["stopped"] is not None:\n', PENDING),
    _m("main_unfinished_run_acknowledged", "a run still unfinished stays pending (1-F1)",
       MAIN, '        if report["stopped"] == "budget_exhausted" or report["state"] in RUN_FINAL:\n',
       "        if True:\n", UNFINISHED),
    _m("main_budget_stop_pending", "a budget stop is final and acknowledged",
       MAIN, '        if report["stopped"] == "budget_exhausted" or report["state"] in RUN_FINAL:\n',
       '        if report["state"] in RUN_FINAL:\n', PENDING),
    _m("main_other_kinds_taken", "another kind's Lab event is not the eval handler's",
       MAIN, '        if event.kind != "eval_run":\n', "        if False:\n", PENDING),
    _m("main_eval_foreign_ref_resolved", "a run's refs resolve for its own provider (R167)",
       MAIN, "        if any(ref.split(\":\")[2:3] != [provider]",
       "        if any(ref.split(\":\")[2:3] == [None]",
       FOREIGN_REF),
    _m("main_eval_foreign_serving_ref", "the serving ref too, not only the evaluator's (R167)",
       MAIN, "for ref in (record.evaluator_ref, record.serving_ref)):",
       "for ref in (record.evaluator_ref,)):", FOREIGN_REF),
    # WR-C2-2 (composition batch 2): the trace sweep keeps what a live C2 content ref holds
    _m("main_trace_sweep_unheld", "the worker's trace sweep and replay ask C2's holds",
       MAIN, "                                     holds=holds)                       # WR-C2-2b\n",
       "                                     holds=None)\n", TRACE_ON),
    _m("main_trace_holds_off_the_pool", "C2's content refs are read on the worker's pool",
       MAIN, "    return ContentAccess(PgContentRefs(connect), None).holds",
       "    return ContentAccess(PgContentRefs(None), None).holds", TRACE_ON),
    # WR-C2-2b: T3's build_shipper hands the holds to the Retention under the shipper
    _m("shipper_holds_dropped", "the shipper's retention holds what it was given",
       SHIPPER, "ClickHouseFeedbackProjection(client), objects, holds=holds,",
       "ClickHouseFeedbackProjection(client), objects,", SHIPPER_HOLDS),
    _m("main_trace_holds_optional", "without C2's refs in the build the pumps refuse",
       MAIN, "    except ImportError:\n        raise RuntimeMisconfigured(mode, detail=\"TRACE_PUMPS",
       "    except ImportError:\n        return None\n        raise RuntimeMisconfigured(mode, "
       "detail=\"TRACE_PUMPS", TRACE_HOLDS),
)

PG_MUTANTS = (
    _m("main_drain_skipped_on_sigterm", "SIGTERM drains the in-flight attempt, then exit 0",
       MAIN, "        await service.serve()\n",
       "        await service.start()\n        await asyncio.Event().wait()\n", DRAIN),
    _m("main_credit_work_absent_on_postgresql",
       "a CREDIT job admitted by the gateway runs to settlement in the worker process",
       MAIN, "    jobs = CreditWork(store) if deployment.accounting_regime == CREDIT_REGIME "
             "else store\n", "    jobs = store\n", ROUND_TRIP),
    _m("main_pool_not_opened", "the pool answers before the listener is bound",
       MAIN, "        await pool.open(wait=True, "
             "timeout=settings.deployment.database_pool_connect_timeout_s)\n",
       "        pass\n", UNREACHABLE),
    _m("pilotbox_worker_not_awaited", "start returns once the worker process is ready",
       PB, "        self._wait_ready(role, ready, timeout)\n", "", PILOT_BOX_PG),
    _m("pg_monitor_login_sets_a_role",
       "on PostgreSQL the monitor login (member of no role) publishes the pass",
       MAIN, "connector(dsn, set_role=not pilot.dedicated_login(dsn))",
       "connector(dsn, set_role=True)", RECON_PG),
    _m("pg_privilege_refusal_every_tick",
       "on PostgreSQL a login refused the views (the runtime's) is disabled once, not every tick",
       SERVICE, '            if getattr(failure, "sqlstate", None) == "42501":',
       "            if False:", RECON_PG),
    _m("pg_gateway_grace_dropped",
       "on PostgreSQL a source the gateway registers is eligible after RETENTION_GRACE_S",
       PILOT, "    return PgLifecycle(connect, limits=settings.pilot,\n"
              "                       grace_s=settings.deployment.retention_grace_s)\n",
       "    return PgLifecycle(connect, limits=settings.pilot)\n", GATEWAY_GRACE_PG),
)


def case_names() -> set[str]:
    return set(re.findall(r"^def (test_\w+)\(", (API_DIR / SUITE_FILE).read_text(), re.M))


def _layout(root: pathlib.Path) -> pathlib.Path:
    """W3's copy (the package, the tests, fake_vllm.py beside them) plus E3B's integration
    tree, whose pilot box the item-3 cases load."""
    api = w3_mutants._layout(root)
    shutil.copytree(API_DIR.parents[1] / "tests" / "integration", root / "tests" / "integration",
                    dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    # P25-ENACT: the cache alert and R's disk budget the P-25 case reads
    for name in ("apps/infrx-api/deploy/preflight.py", "infra/alerts/operations.json"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(API_DIR.parents[1] / name, root / name)
    return api


RUNNER = Runner(name="worker-main", targets=(SUITE_FILE,), layout=_layout)


def _pg_layout(root: pathlib.Path) -> pathlib.Path:
    """The copy above plus the migrations `infrx.state.migrations` reads (the D harness
    builds its template database from them)."""
    api = _layout(root)
    migrations = pathlib.Path("apps", "app", "supabase", "migrations")
    shutil.copytree(API_DIR.parents[1] / migrations, root / migrations)
    return api


PG_RUNNER = Runner(name="worker-main-pg", targets=(SUITE_FILE,), layout=_pg_layout,
                   env=("INFRX_D_TASK", "INFRX_D2_VALKEY_PORT", "INFRX_D2_VALKEY_CONTAINER",
                        "INFRX_M_S3_ENDPOINT", "INFRX_M_S3_LOCAL_CREDS"))


def run_mutant(mutant) -> Result:
    """The PostgreSQL list is not a module's `MUTANTS`, so the shared runner takes no
    baseline for it: its cases run unmutated first, once per process (R83 (b))."""
    if mutant not in PG_MUTANTS:
        return shared.run_mutant(mutant, RUNNER)
    cases = tuple(sorted({case for m in PG_MUTANTS for case in m.cases}))
    return shared.pristine(cases, PG_RUNNER) or shared.run_mutant(mutant, PG_RUNNER)


if __name__ == "__main__":
    raise SystemExit(shared.main(MUTANTS, RUNNER, "run I2B-R4's service-free mutation list"))
