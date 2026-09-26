# WAVE4B-UNION: the integrated candidate, code head `74183655`

| | |
|---|---|
| Lane | WAVE4B-UNION (coordinator integration lane, program 22) |
| Branch / worktree | `codex/wave4b-union` / `.claude/worktrees/codex-wave4b-union` |
| Base | `e607b705` (= `claude/consumer-v1` at dispatch) |
| Code head | `74183655`; this evidence is committed on top of it |
| Isolation | `INFRX_D_TASK=union`: `infrx-union-postgres` (and `-supabase`) on 55458, `infrx-union-valkey` on 55454 (`INFRX_D2_VALKEY_CONTAINER`/`_PORT`), `infrx-union-s3` MinIO on 55455 (`pgsty/minio@sha256:b6bfe723…`, `--pull never`, E2's committed local literals via an env file, never argv). Deviations forced by the suites, both self-removing: `tests/q`'s harness is hard-wired to task `q3`, so `INFRX_Q_VALKEY_PORT=55456` (unreserved gap) gives it `infrx-q3-valkey-55456`; `make api-test`'s `tests/i` pooler harness is hard-wired to `i8` (`infrx-i8-postgres` 55450, `infrx-i8-pgbouncer` 55496, lock-guarded: acquired, so no i8 run was live). No hosted DB, pilot box, AWS or SSM. Nothing pushed; no rebase, reset, amend or stash. |

## Merges (first-parent order)

| # | Commit | What | Resolution |
|---|---|---|---|
| 1 | `f7a88900` | `merge --no-ff codex/d10-followup` at **9d4bd28f** (brief named 0d577c4e; the branch already carried fix round a0db994c + 9d4bd28f) | clean |
| 1w | `5b7eb5e8` | D10-FOLLOWUP wiring W1–W4: `git apply --3way research/plan/evidence/d/D10-followup-wiring.patch` (W1 transition.py → `infrx.set_feature_flag`; W2 F2C fake WR-7 + regenerated `fixtures/acceptance/lifecycle.json` in the same commit; W3 M6 strict xfail dropped; W4 pgstate FUNCTIONS/RUNTIME_FUNCTIONS + 0022 pin) | clean, one commit |
| 1d | `0e26f095` | `merge --no-ff codex/d10-followup` delta **c584f54a** (evidence/update only) | clean |
| 2 | `1bf18139` | `merge --no-ff codex/m6-wiring` at **28dd1af2** (code 3ed20d23; brief named 38876865) | clean |
| 2w | `21694661` | M6-WIRING WR-M6W-1: `DEPLOYMENT_EXPECTED` += the 4 settings, exactly as `M6-wiring-2ae944f.md` states | one commit |
| 3 | `9d61d1e1` | `merge --no-ff codex/w5-merge` at **f18c72f7** | **hand-resolved** `worker/service.py` (below); `__main__.py`, `prep_worker_mutants.py`, `Makefile` auto-merged and checked |
| 4 | `1b8d9d91` | `merge --no-ff codex/door-revoke` at **273990a0** (0023) | clean (its d10-followup parent is already in) |
| 4w | `a5a08519` | DOOR-REVOKE wiring W-DR1 v2: `git apply --check` then apply `DOOR-REVOKE-wiring-v2.patch` (pgstate RUNTIME_FUNCTIONS → 41, 0023 pin, e2m75 re-anchor) | clean, one commit |
| 5 | `9c4dcf9c` | `merge --no-ff codex/w5-merge` delta **50d85530** (code fba75f6e; tests/g only) | clean |
| 6 | `a51eeed9` | `merge --no-ff` `claude/consumer-v1` at **1a14c2b8** as instructed (ROLLOUT-FIXES, E4C-RUNBOOK, plan records). The branch has since moved to `fd40748c` (APP-UNION, G7-PROVISIONAL): NOT merged | clean |
| 7 | `74183655` | `merge --no-ff codex/known-good-proof` at **f5293057** | **hand-resolved** `infra/rollout/README.md` (below); 0022/0023 merged as no-ops |

Migrations: `git diff e607b705 HEAD` over 0001–0021 is empty. 0022 sha256
`1f44b4ae89506dc75cb1d9cbf26208935906058833a18fa518518085aea5e706` (278 lines, the fix-round
bytes D10-FOLLOWUP recorded); 0023 sha256
`0ea64faa5c15aa4aff0735b843530491d3c46beedaf4c23e24bfaab262b17f46`. No migration written here.

### Hand resolution 1: `apps/infrx-api/infrx/worker/service.py` (merge `9d61d1e1`)

The only textual conflict: both sides added a dataclass field at the same spot. Both kept, W5's
first (`field` was already imported: `from dataclasses import asdict, dataclass, field`).
`git show --cc 9d61d1e1`:

```diff
@@@ -74,7 -102,7 +106,8 @@@ class WorkerService
      preparation_concurrency: int = 1
+     reconciliation: object | None = None         # S3 F4: async () -> (drift, unknown holds)
 +    housekeeping: dict = field(default_factory=dict)   # name -> () -> coroutine, run forever
      reaped: int = 0
```

`worker/__main__.py` auto-merged to the union of both (checked by reading, and by both lanes'
mutant anchors below): one `from ..state.lifecycle import PgLifecycle`, W5's
`from .service import PgReconciliation, WorkerService`, W5's
`PreparationRunner(..., readiness=PgLifecycle(connect, limits=limits))`, and:

```diff
@@@ -166,10 -149,9 +167,11 @@@ def compose(settings, *, objects=None,
      service = WorkerService(loop=loop, jobs=jobs, engine=engine,
                              concurrency=limits.worker_concurrency,
                              health_port=deployment.worker_health_port,
+                             reconciliation=PgReconciliation(connect),
                              metrics=rt.metrics, pool=pool, preparation=preparation,
 -                            preparation_concurrency=limits.preparation_concurrency)
 +                            preparation_concurrency=limits.preparation_concurrency,
 +                            housekeeping=housekeeping(deployment, lifecycle, objects, media,
 +                                                      journal, rt.metrics))
```

So the worker starts M6's three housekeeping tasks (retention, cache_keeper, journal_expire; one
task each, cancelled after the drain) and W5's reconciliation pass (inside the reaper tick,
`_reconciled()` after each `recover`). `tests/w/prep_worker_mutants.py` auto-merged: M6's
re-anchored `main_preparation_concurrency_ignored` (`...preparation_concurrency,`) plus W5's
`main_readiness_not_wired` / `main_reconciliation_not_wired` and its `patches <= video` anchors.
`Makefile` `api-mutants` carries both `tests/m/test_retention_mutants.py` and
`tests/w/test_w5_mutants.py`. Composed proof: every anchor audit passed and all 152 W mutants
(prep 78, worker-main 39, w5 35) were killed on `9d61d1e1`, including M6's
`main_retention_not_scheduled`/`main_housekeeping_*` and W5's `main_reconciliation_not_wired`.

### Hand resolution 2: `infra/rollout/README.md` (merge `74183655`)

Conflict only in the verification log. Kept ROLLOUT-FIXES' rollback table (its
"Rollback target after migrations 0019+" row) and KNOWN-GOOD-PROOF's §2 paragraph (auto-merged),
and all three 2026-09-25 log entries in time order: ROLLOUT-FIXES (21:25Z update), then
KNOWN-GOOD-PROOF and its fix round.

## Checks (from `apps/infrx-api` unless noted; env `INFRX_D_TASK=union INFRX_D2_VALKEY_CONTAINER=infrx-union-valkey INFRX_D2_VALKEY_PORT=55454`, plus `INFRX_Q_VALKEY_PORT=55456` and, from step 2 on, `INFRX_M_S3_ENDPOINT=http://127.0.0.1:55455 INFRX_M_S3_LOCAL_CREDS=1`; `P = uv run --frozen --no-sync pytest -q -p no:cacheprovider`)

| At | Command | Exit | Result |
|---|---|---|---|
| — | `make api-env` (root) | 0 | env built |
| 5b7eb5e8 | `P tests/d` | 0 | **845 passed, 1 skipped, 8 xfailed** (19m56s); incl. `test_lifecycle_conformance` transcripts (W2) and `test_composition_pg` 3/3 (`[legacy_usd]`, `[credit]`, dedicated login: PASSED) |
| 5b7eb5e8 | `P tests/contracts` | 0 | **1266 passed** |
| 5b7eb5e8 | `INFRX_M6_WORLDS=f2c,d10 P tests/m` | 0 | **541 passed, 27 skipped** (S3 suites, no MinIO yet), 0 xfailed |
| 5b7eb5e8 | `P -rA tests/m/test_retention.py -k fetched_again` | 0 | the former strict xfail **PASSED [f2c] and [d10]** |
| 5b7eb5e8 | `P tests/g/ops/test_transition.py tests/g/ops/test_transition_pg.py` | 0 | **14 passed** |
| 5b7eb5e8 | layer-3 `rls` stage: `INFRX_D1_IMAGE=supabase` scratch driver = `tests/integration/run.py:rls` (fresh `infrx_union` from the Supabase template → every migration → test clock → `seed_fixtures` → `pgstate.run_role_matrix`) + `test_services`' completeness diff | 0 | **886 cases, 0 failed** (880 + 6 rows for 0022's two functions); completeness: no relation/function without a row, no row without an object |
| 5b7eb5e8 | root: `.venv/bin/python -m pytest -q tests/integration/test_harness.py` | 0 | **27 passed** (0022 pin) |
| 1bf18139..21694661 | `P -rfEs tests/m` (MinIO up) | 0 | **577 passed, 1 skipped** (`test_pilot_mutants` placeholder) |
| 21694661 | `P -rfEs tests/w` | 0 | **279 passed, 2 skipped** (mutant-list placeholders); every PG/MinIO round trip ran |
| 21694661 | `P tests/contracts` | 0 | **1286 passed** (WR-M6W-1) |
| 21694661 | `P -rfEs tests/i/test_observe.py` | 0 | **18 passed, 1 xfailed** |
| 21694661 | `INFRX_MUTANTS=all P tests/w/test_worker_main_mutants.py` | 0 | **44 passed** (M6 fix-round delta) |
| 9d61d1e1 | `P -rfEs tests/w` | 0 | **322 passed, 3 skipped** (the 3 mutant-list placeholders), **0 failed**: W5's recorded `legacy_usd` failure is gone (D10-FOLLOWUP's seed) |
| 9d61d1e1 | `INFRX_MUTANTS=all P tests/m/test_retention_mutants.py` | 0 | **63 passed = 62/62 mutants killed** + well-formed (brief expected 43; M6-WIRING added 14 metric + 6 R6 mutants) |
| 9d61d1e1 | `INFRX_MUTANTS=all P tests/w/test_prep_worker_mutants.py tests/w/test_worker_main_mutants.py tests/w/test_w5_mutants.py` | 0 | **167 passed** (152 mutants killed: prep 70+8 PG, worker-main 35+4 PG, w5 28+7 PG; + 15 anchor/coverage/baseline cases) |
| 9d61d1e1 | root: `pytest -q tests/integration/backend/e3c/test_e3c_runner.py` | 0 | **34 passed** (brief said 30; E3C-rerun added s12 ×4 bypass cases) |
| 9d61d1e1 | `P tests/contracts` | 0 | **1286 passed** |
| 9d61d1e1 | `P tests/g/test_composition.py tests/g/test_relay_readiness.py` | 0 | **44 passed** |
| 9d61d1e1 | root: `make api-test` | 2 | **44 failed, 4419 passed, 5 skipped, 9 xfailed** (58m40s). All 44 in `tests/i`: 1 real + 43 cascades, see F1 |
| a5a08519 | `P tests/d/test_composition_pg.py tests/d/test_reads.py tests/d/test_ready.py tests/d/test_schema_postgres.py tests/g/test_composition.py` | 0 | **101 passed, 3 xfailed** (as DOOR-REVOKE measured) |
| a5a08519 | root: `pytest -q tests/integration/test_harness.py` | 0 | **27 passed** (0023 pin) |
| a5a08519 | layer-3 `rls` driver as above, 0001–0023 | 0 | **886 cases, 0 failed**; completeness clean; `L3-LOGIN` runtime row = 41 functions |
| a5a08519 | I8 `infra/runbooks/privilege_probe.py --role infrx_runtime --allow-functions …` as the REAL `infrx_runtime` login on a fresh 0001–0023 `infrx_union` (Supabase image; random in-memory password never printed; NOLOGIN restored). `tests/i/test_privilege_probe.py` itself needs I8's pooler stack (it runs inside `make api-test`) | 0 / 1 | `DOOR-REVOKE-runtime-functions.txt`: **PASS, 62 checks, 0 failed**. Old `D10-runtime-functions.txt`: FAIL on exactly `executes infrx.admit(jsonb)`, `executes infrx.claim_preparation(jsonb)` |
| 9c4dcf9c | `P tests/g/test_relay_readiness.py` | 0 | **22 passed, 0 xfailed** (lane: 20 + 2 xfailed). The 2 `fetched` cases are `xfail(not M6_PHASE2, strict=True)`; the union has `MediaStaging._register` (M6 phase 2), so they run and pass - the expected outcome on a tree with M6 phase 2 |
| 9c4dcf9c | `P tests/g/test_relay_readiness_pg.py` (union PG + union MinIO 55455) | 0 | **4 passed, 0 skipped** (lane: 2 + 2 xfailed; same reason). No MinIO on 55497 needed |
| 74183655 | `P tests/i/test_known_good_proof.py tests/i/test_rollout.py tests/i/test_ops_steps.py` | 0 | **25 passed** |
| 74183655 | `uv run --frozen --no-sync python tests/i/mutants.py known_good_proof_ignores_through known_good_record_unproven schema_proof_trusts_moved_statements` | 1 | 0/3 killed: all **broken_runner** (pristine baseline fails `test_observe`, F1). With F1's one-line fix applied temporarily (reverted): **3/3 killed** |
| 74183655 | root: `python3 infra/rollout/known-good.py bda15866e5700f3856d7142580da842fba9bbd23 --applied 0023 --set MAX_VIDEO_SECONDS=82 --set WORKER_CONCURRENCY=8` | 0 | **KNOWN-GOOD** (0019–0023 proven by `KNOWN-GOOD-PROOF-aab4b41.md`) |
| 74183655 | same with `--applied 0024` | 1 | **NOT-KNOWN-GOOD** (`0024` not the bytes its schema_proof ran on) |
| 74183655 | root: `python3 research/plan/scripts/validate_plan.py` | 0 | PASS (133 tasks; 933 links / 227 docs) |
| 74183655 | root: `make api-test` | 2 | **44 failed, 4437 passed, 5 skipped, 9 xfailed** (1h01m33s). Exactly F1 again: `test_observe` (1 assertion) + 43 `tests/i/test_mutants.py` `broken_runner` cascades; nothing else failed (tests/d incl. 0023, tests/w, tests/g, tests/m, tests/q, tests/contracts all green inside it) |
| 74183655 | `P -rfEs tests/i` with F1's one-line fix applied temporarily (reverted after; tree clean) | 0 | **236 passed, 1 xfailed** - so with WR-UNION-1 the whole `make api-test` is green on this head |

## Findings

- **F1 (gate, pre-existing on `codex/w5-merge`, not a union artifact): `tests/i/test_observe.py::test_ops_continuous__the_alert_rules_without_a_producer_are_exactly_the_known_ones` fails.** W5 (S3 F4) now calls `record_reconciliation` from `worker/service.py`, which produces `infrx_unsettleable_jobs`, so alert `UnsettleableJobs` has a producer; I8's `KNOWN_UNPRODUCED` pin still lists it ("Extra items in the right set: 'UnsettleableJobs'"). Reproduced on a `git archive` of `f18c72f7` alone (same assertion); passes on `28dd1af2` and `e607b705`. Every `tests/i` mutant then reports `broken_runner` (43 in `make api-test`'s default list; the 3 KNOWN-GOOD-PROOF mutants above). **Wiring request WR-UNION-1** (tests/i, I8/W5 owner):
  ```diff
   KNOWN_UNPRODUCED = {"ComponentDown", "QueueStalled", "QueueSaturated", "RejectionsHigh",
  -                    "PlatformFailureRate", "LeaseLost", "ReaperTerminalized", "UnsettleableJobs"}
  +                    "PlatformFailureRate", "LeaseLost", "ReaperTerminalized"}
  ```
  Proof (applied temporarily, reverted): at `9d61d1e1` `tests/i/test_observe.py` 18 passed, 1 xfailed and `tests/i/test_mutants.py` 50 passed; at `74183655` all of `tests/i` 236 passed, 1 xfailed. Not applied here (lane-owned test; the brief forbids improvising). The coordinator's queued round-3 WR-P25-4 is this same edit.
- **F2 (doc nit, kept by instruction):** `infra/rollout/README.md`'s ROLLOUT-FIXES table row still says `bda1586`/`4226315` "are recorded without" a `schema_proof` and the known-good rollback "has no target", while KNOWN-GOOD-PROOF's paragraph right below records both with `through: 0023`. The row wants a one-sentence update by the rollout owner.
- **F3 (cleanup candidate, not a defect):** the composed worker builds two `PgLifecycle(connect, limits=limits)` objects over one `connect`: M6's `lifecycle` (content registration, retention) and W5's `readiness=` for `PreparationRunner`. Both are stateless adapters over the same database. Reusing `lifecycle` would need W5's `main_readiness_not_wired` anchor re-pointed; left as merged.
- Branch tips moved during the lane: `codex/d10-followup` 0d577c4e → c584f54a, `codex/m6-wiring` 38876865 → 28dd1af2, `codex/w5-merge` f18c72f7 → 50d85530, `claude/consumer-v1` → fd40748c (not merged beyond 1a14c2b8). `codex/w5-f5` exists (39f48b37) and is NOT merged (pending its verification, round 3). `codex/e3c-rerun` not merged, as instructed.

## Remaining effort

WR-UNION-1 (one line + rerun `tests/i`, ~10 min), round 3 (`codex/w5-f5` + its checks), then the
coordinator's merge of this branch into `claude/consumer-v1` (now at fd40748c: APP-UNION and
G7-PROVISIONAL touch `apps/app` and `gateway/routes/models`, not the files hand-resolved here).
Optimistic 0.5 h, likely 1.5 h, pessimistic 3 h; confidence medium; basis: every D/W/M/G/contracts
list green on the union, the one red is F1 with a proven fix.

## Verification log

- 2026-09-25: written at code head `74183655` by the WAVE4B-UNION lane; logs in the session scratchpad (`union/results.txt`, `s*-*.log`, `rls-s*.log`).
