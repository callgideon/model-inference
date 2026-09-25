# M6-WIRING: M6 wiring 1 + 2, WR-I8-M6-1, E3C F-4 (+ the phase-2 R6 add-on)

- Branch `codex/m6-wiring`, base `4959e10c`, code head `2ae944f5` (commits `67bb9e55` main lane, `165196bb` R6 add-on, `2ae944f5` mutant declaration). Not pushed.
- Isolation: `INFRX_D_TASK=m6` (infrx-m6-postgres :55444). No hosted DB, box, AWS or SSM. `docker ps -a` after the runs: no `infrx-m6-*` container left.

## What changed

| Deliverable | Change |
|---|---|
| (a) settings | `config.DeploymentSettings`: `processing_cache_max_bytes` 64 424 509 440 (60 GiB), `retention_interval_s` 300, `cache_sweep_interval_s` 300, `journal_expire_interval_s` 300, read by the one reader (`deployment_from_env`), refused at 0 by `DEPLOYMENT_MUST_BE_POSITIVE` (negative/non-finite by `_coerce`). All P-25 placeholders, `⚠️ TO BE VERIFIED` in the comment. The claim TTL stays `PgLifecycle`'s 300 s (> the 75 s delete timeout); no setting moves it. `deploy/preflight.py` TUNABLE declares the four names (the env schema test requires every name `from_env` reads). |
| (b) wiring 1 + F-4 | `worker/__main__.py` (blocks marked `M6-WIRING`): `PgLifecycle(connect)` is `media.content`; `ProcessingCache(..., max_bytes=PROCESSING_CACHE_MAX_BYTES, metrics=Registry)`; one `PgStreamStore` shared by the runner and the prune. `housekeeping()` returns three named loops, `WorkerService(housekeeping=)` starts one task each and cancels them after the drain: `retention` (`RetentionCollector.run(RETENTION_INTERVAL_S, metrics=)`), `cache_keeper` (`to_thread(cache.sweep)` every `CACHE_SWEEP_INTERVAL_S`), `journal_expire` (`expire_journal`: `PgStreamStore.expire()` until it returns 0, every `JOURNAL_EXPIRE_INTERVAL_S`). A failed step is logged and retried; none is a death. Gateways compose none of it. |
| (c) wiring 2 | `VllmEngine(pin=, reprepare=)`: `_attempt` first pins every video input `local_uri` returns (`ProcessingCache.pin`, an `ExitStack` on the stream), closed in `_generate`'s `finally` (every exit path: completion, refusal, failure, `aclose`). `NotFound` (a miss or a pin that lost its race) -> `reprepare(job_id, profile)` (`MediaPreparation.prepare`, then drop the per-job map entry) once; `NotFound` again propagates -> the runner's existing refusal path (`platform_error`, never charged). |
| (d) WR-I8-M6-1 | `observe/metrics.py` FAMILIES: the 12 families exactly as `dashboard.json` `pending.families` declared them (closed `reason`/`location` vocabularies). `retention.record()` from `run(metrics=)`: passes, deleted{location}, retained{reason} by count, delete_failed, ack_lost, pending_delete_seconds, aborted{reason} + consecutive streak (a pass that raised extends it), last_success on completion. `ProcessingCache(metrics=)`: evicted{high_water} in `_make_room`, evicted{expired} in `sweep`, refused before the raise. `dashboard.json`: the two pending rows moved into `rows`, `pending` dropped; `operations.json` `pending_producers` emptied (tests/i requires it once produced). `infrx_processing_cache_bytes` stays host-probe-produced (declared for OB-10). |
| (e) F-4 proof | `tests/m/test_retention.py::test_pg_one_journal_prune_pass_removes_only_chunks_past_their_ttl[d10]`. |
| add-on (coordinator) | R6 X6/X7/X11/X17/X19 pinned by five `tests/m/test_cache_bounds.py` cases + 6 mutants; R5 documented as a `ponytail:` ceiling on `store.Recent` (doc only). |

Changed paths: `apps/infrx-api/{deploy/preflight.py, infrx/config.py, infrx/media/{prepare,retention,store}.py, infrx/observe/metrics.py, infrx/worker/{__main__,engine,service}.py, tests/i/{mutants,test_observe}.py, tests/m/{test_cache_bounds,test_retention,test_retention_mutants}.py, tests/w/{prep_worker_mutants,test_worker_main,worker_main_mutants}.py}`, `infra/alerts/{dashboard,operations}.json`.

Outside the listed paths, stated: `deploy/preflight.py` (the brief's "deploy env schema"), `infra/alerts/operations.json` (I8's item 5, required for tests/i green), `infrx/media/store.py` (comment only, coordinator add-on), `infrx/worker/service.py` (under `infrx/worker/`).

## Env schema

`deploy.preflight.schema_id()`: before `1:c947948d1106e4fc`, after `1:55563b285260ad78` (four TUNABLE names). `ENV_SCHEMA_VERSION` unchanged (no name changed meaning): an installed env file written for the old schema is refused by `envcheck` until `apply` rewrites it — the next install does.

## Regressions: failed first, then passed

- `tests/i/test_observe.py` with FAMILIES declared and the old dashboard/operations: **2 failed** (`a pending producer landed`, `declared now: move its panel into rows (OB-10)`); after the move and the rewritten M6 cases: 18 passed, 1 xfailed.
- Every new W/M case is killed by a single-edit mutant naming it (below): the composition (no collector, two tasks, not cancelled, wrong store/registry, no content, no high water, no prune, one-call prune), the pin (not taken, never released, not composed), re-prepare (never, forever, leaks the map entry), each metric increment.

## Commands (from `apps/infrx-api` unless noted)

| Command | Exit | Result |
|---|---|---|
| `make api-env` (repo root) | 0 | env built |
| `INFRX_D_TASK=m6 INFRX_M6_WORLDS=f2c,d10 uv run --frozen pytest -q -p no:cacheprovider tests/w tests/m` | 0 | **814 passed, 36 skipped, 2 xfailed** (skips: `_pg__` W cases without a lane MinIO/Valkey; the 2 xfailed are M6's strict WR-7 race) — at `165196bb`; `2ae944f5` changes only a mutant declaration |
| `INFRX_D_TASK=m6 uv run --frozen pytest -q tests/i/test_observe.py` | 0 | 18 passed, 1 xfailed |
| `.venv/bin/python -m pytest -q tests/integration/backend/recovery/test_observe.py` | 0 | 16 passed (OB-01, OB-10) |
| `INFRX_D_TASK=m6 INFRX_MUTANTS=all uv run --frozen pytest -q tests/m/test_retention_mutants.py` | 1 -> 0 | 61/62 at `165196bb` (`m6_x19_prepared_row_no_job` died by `ValidationError`, undeclared); declared at `2ae944f5`, rerun of it + well-formed: 2 passed. **62/62 killed** (42 prior + 14 metrics + 6 R6) |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/w/test_worker_main_mutants.py` | 0 | 32 passed, 4 skipped (PG list, no lane MinIO): **27/27 service-free killed** (14 new) |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/w/test_prep_worker_mutants.py` | 0 | 73 passed, 8 skipped (one anchor re-pointed: `main_preparation_concurrency_ignored`, killed) |
| `INFRX_D_TASK=m6 uv run --frozen python tests/i/mutants.py m6_panel_missing m6_pending_section_left m6_family_undeclared m6_abort_rule_blunted` | 0 | 4/4 killed (2 new) |
| `INFRX_D_TASK=m6 uv run --frozen pytest -q tests/i/test_mutants.py tests/i/test_packaging.py tests/i/test_envcheck.py tests/i/test_prereqs.py` | 0 | 102 passed |
| `uv run --frozen pytest -q tests/contracts/test_config_and_imports.py` | 1 | 1 failed (`DEPLOYMENT_EXPECTED`, not this lane's): WR-M6W-1; with the patch applied locally 271 passed, reverted |
| `python3 research/plan/scripts/validate_plan.py` (root) | 0 | PASS |

New mutants: W `main_retention_not_scheduled`, `main_retention_unrecorded`, `main_journal_never_pruned`, `main_journal_prune_one_call`, `main_content_unregistered`, `main_cache_unbounded`, `main_housekeeping_started_twice`, `main_housekeeping_outlives_the_drain`, `engine_media_not_pinned`, `engine_pin_never_released`, `main_engine_unpinned`, `engine_gone_input_refused_at_once`, `engine_gone_input_prepared_forever`, `main_reprepare_keeps_the_job_map`; M `m6_metrics_{pass_uncounted,location_fixed,retained_once,failed_once,ack_lost_uncounted,pending_age_dropped,abort_uncounted,success_on_abort,streak_never_resets,crash_not_aborted,registry_ignored,expiry_uncounted,high_water_uncounted,refusal_uncounted}`, `m6_x6_part_grace_zero`, `m6_x7_no_clock_step_slack`, `m6_x11_keep_path_counted`, `m6_x17_rewrite_not_newest`, `m6_x19_prepared_row_wrong_kind`, `m6_x19_prepared_row_no_job`; I `m6_pending_section_left`, `m6_family_undeclared`. Re-anchored: W `main_local_uri_without_the_shared_cache`, `main_preparation_concurrency_ignored`; M `m6_full_cache_overfills`; I `m6_panel_missing`.

## Wiring requests

- **WR-M6W-1 (contracts owner):** `apps/infrx-api/tests/contracts/test_config_and_imports.py` `DEPLOYMENT_EXPECTED` pins every DeploymentSettings name. Patch (verified: 271 passed):
  ```diff
       "WORKER_HEALTH_PORT": 8002,
  +    # M6 wiring 1 + E3C F-4: the worker's cache high water and housekeeping cadences (P-25)
  +    "PROCESSING_CACHE_MAX_BYTES": 64424509440, "RETENTION_INTERVAL_S": 300.0,
  +    "CACHE_SWEEP_INTERVAL_S": 300.0, "JOURNAL_EXPIRE_INTERVAL_S": 300.0,
   }
  ```
- **WR-M6W-2 (merge lane, W5):** `codex/w5-merge` also edits `worker/__main__.py` `compose`. This lane's edits are the `M6-WIRING` blocks plus three keyword arguments (`VllmEngine(pin=, reprepare=)`, `AttemptRunner(stream=journal)`, `WorkerService(housekeeping=)`). The W mutant anchors on `preparation_concurrency=limits.preparation_concurrency,` and `pin=media.cache.pin, reprepare=` must survive the union; `tests/w/test_worker_main_mutants.py::test_every_anchor_is_in_the_source_as_often_as_declared` fails on a lost one.
- **WR-M6W-3 (E3C):** the final E3C run should start `python -m infrx.worker` (it now schedules retention and the prune) for nc-retention-durable and F-4's s06/s11 scrub cases; the old WR-3 stand-in collector remains valid for a direct pass.
- **WR-M6W-4 (I8, optional):** `infrx_processing_cache_bytes` is still host-probe only; the worker could `set` it after each keeper pass if a per-process figure is wanted.

## Open

1. P-25: every default above is a placeholder (retention interval, cache sweep, prune interval, 60 GiB high water). The I8 thresholds (pending delete > 900 s, stale > 1200 s) assume 300 s.
2. Preparation's own `/tokenize` call sends the freshly put file to vLLM without a pin; a high-water eviction by a concurrent `put` in that window would fail the count as `dependency_unavailable` (retried by preparation). Pinning there is a one-line `with media.cache.pin(...)` in `preparation.py` if measured.
3. M6 WR-7 (refetch race, D10 SQL) and WR-8 are unchanged; the strict xfail stays.
4. The composed `reprepare` runs `MediaPreparation.prepare` under the inference lease; it is idempotent (write-once, content-addressed) and registers the same prepared row, but a source already collected makes it `not_found` -> refusal (correct: the media is gone).

## Remaining effort

Optimistic 0.5 h, likely 1.5 h, pessimistic 3 h; confidence medium. Basis: code and proofs complete on this branch; remaining is the union with W5's compose (WR-M6W-2), WR-M6W-1, a review round, and the E3C rerun on the composed worker.
