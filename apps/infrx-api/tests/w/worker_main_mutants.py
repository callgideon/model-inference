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
PILOT_BOX = "test_worker_main__the_pilot_box_runs_the_real_entry_point_on_request"
PILOT_BOX_PG = "test_worker_main_pg__the_pilot_box_starts_the_real_worker_and_waits_for_it"
PB = "../../../tests/integration/backend/pilotbox.py"      # E3B's pilot box, from `infrx/`

MUTANTS = (
    _m("main_validate_runtime_skipped", "the worker refuses what the gateway refuses (R44)",
       MAIN, "    mode = validate_runtime(settings)\n", "    mode = runtime_mode(settings)\n",
       REFUSALS),
    _m("main_database_url_not_required", "no DATABASE_URL, no worker, in every mode",
       MAIN, '(("DATABASE_URL", limits.database_url),\n'
             '                                        ("VALKEY_URL"', '(("VALKEY_URL"',
       REFUSALS),
    _m("main_cache_dir_unchecked", "PROCESSING_CACHE_DIR is an absolute readable directory",
       MAIN, "    if not (os.path.isabs(root) and os.path.isdir(root) and "
             "os.access(root, os.R_OK | os.X_OK)):",
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
       MAIN, "    media = MediaPreparation(objects, limits=limits,\n"
             "                             cache=ProcessingCache(root, "
             "ttl_s=limits.processing_cache_ttl_s))\n",
       "    media = MediaPreparation(objects, limits=limits)\n", LOCAL_URI),
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
    # item 3: E3B's pilot box can run the real entry point
    _m("pilotbox_real_worker_emulated", "real_worker runs python -m infrx.worker",
       PB, '        if role == "worker" and self.real_worker:\n'
           '            return [sys.executable, "-m", "infrx.worker"], harness.API_ROOT\n', "",
       PILOT_BOX),
    _m("pilotbox_real_worker_private_namespace", "the real worker and the gateway share the "
       "pilot's index namespace", PB, "        if real_worker:\n"
                                      "            namespace = PILOT_NAMESPACE\n", "", PILOT_BOX),
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
    _m("pilotbox_real_worker_not_awaited", "start returns once the real worker is ready",
       PB, "        elif self.real_worker:\n            self._wait_ready(role, "
           'f"http://127.0.0.1:{self.worker_port}/readyz", timeout)\n', "", PILOT_BOX_PG),
)


def case_names() -> set[str]:
    return set(re.findall(r"^def (test_\w+)\(", (API_DIR / SUITE_FILE).read_text(), re.M))


def _layout(root: pathlib.Path) -> pathlib.Path:
    """W3's copy (the package, the tests, fake_vllm.py beside them) plus E3B's integration
    tree, whose pilot box the item-3 cases load."""
    api = w3_mutants._layout(root)
    shutil.copytree(API_DIR.parents[1] / "tests" / "integration", root / "tests" / "integration",
                    dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
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
