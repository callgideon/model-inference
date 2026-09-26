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
RELEASE = "                stream.pins.close()\n                # Every exit path"
RECON_OFF = "test_worker_main__without_a_monitor_login_the_reconciliation_gauges_are_off"
RECON_REFUSED = "test_worker_main__a_login_refused_the_views_disables_the_gauges_once"
RECON_DOWN = "test_worker_main__a_database_that_is_down_is_still_retried_every_tick"
RECON_MONITOR = "test_worker_main__the_reconciliation_gauges_are_read_on_the_monitor_login"
RECON_PG = "test_worker_main_pg__the_monitor_login_reads_what_the_runtime_login_may_not"

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
    _m("gateway_grace_wired_unrecorded", "the gateway-grace gap stays recorded until "
       "WR-P25-1's patch removes the strict mark (then: a mutant that drops its grace_s)",
       PILOT, "        lifecycle = _pg_lifecycle(connect, settings.pilot)\n",
       "        lifecycle = _pg_lifecycle(connect, settings.pilot)\n"
       "        lifecycle.grace_s = settings.deployment.retention_grace_s\n", GATEWAY_GRACE),
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
       "on PostgreSQL 0021's monitor login is refused once and disabled, not every tick",
       SERVICE, '            if getattr(failure, "sqlstate", None) == "42501":',
       "            if False:", RECON_PG),
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
