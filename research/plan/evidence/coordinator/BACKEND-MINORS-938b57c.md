# BACKEND-MINORS: RV-D10F-3, the W5 mutant-runner lock, G8F2-M1/M2, CC-1, ASN-V1, RF-C1 (evidence)

| | |
|---|---|
| Lane | BACKEND-MINORS (support lane, after the freeze), branch `codex/backend-minors`, worktree `.claude/worktrees/codex-backend-minors` |
| Base / code head | `b967a033` / `938b57ca` (the E3C final run's head); this document and the update file are committed after it |
| Isolation | task-local docker only: w5 (55445), g8 (55447), the e3c block (56900-56999, one run at a time, torn down: 0 `e3c` containers after). Never e2c, i8 or 55432; no hosted DB, pilot box, AWS or SSM. No migration, no product code under `apps/infrx-api/infrx/`. |

## Changed paths (`git diff --stat b967a033..938b57ca`: 13 files, +152/-15)

| Path | Item |
|---|---|
| `apps/infrx-api/tests/w/test_loop.py` | RV-D10F-3: `World._put_result` answers as 0026 does - with a lease, another job's lease `invalid_request` and then the fake store's fence; **without one, 0014's unfenced write** (what production keeps for the rollback targets) instead of crashing on `_fence(None)` |
| `apps/infrx-api/tests/w/loop_mutants.py` | `result_written_without_the_lease` now names `RESULT_FENCE` (the R147 oracle) instead of HAPPY |
| `apps/infrx-api/tests/w/w5_mutants.py`, `test_w5_mutants.py` | the W5 PG runner's own layout: the copy's `pgharness.lock_path()` is rewritten to the directory THIS process's harness locks (TMPDIR-independent, one lock per port for every copy, as tests/i/pooler.py WR-KGP2-4); a refusal if the anchor moves; new case `test_a_pg_copy_takes_the_hosts_port_lock_whatever_its_tmpdir` |
| `apps/infrx-api/tests/g/ops/test_flag.py`, `mutants.py` | G8F2-M1: `test_flag__a_dry_run_with_a_direction_writes_nothing_even_for_a_valid_operator` (`cli.dispatch` with the operator's own credential); added to `flag_dry_run_writes`'s cases |
| `apps/infrx-api/tests/g/ops/test_flag_pg.py` | G8F2-M2: `--on --dry-run` on an already-on flag answers `changed: false` on PostgreSQL |
| `tests/integration/backend/e3c/scenarios_capacity.py` | CC-1: the capacity case bursts again (after its drain, worker stopped) through two more gateways at `MAX_ACTIVE_JOBS=16`: alpha's two keys and beta's first key, 4 requests each |
| `infra/observe/deliver.py` | ASN-V1: `status = int(...HTTPStatusCode...)` inside the existing try |
| `apps/infrx-api/tests/i/test_alert_sns.py`, `tests/i/mutants.py` | ASN-V1 fails-before cases (`HTTPStatusCode` None and "OK"); mutant `sns_status_not_a_number` (declared `TypeError`: main() compares the raw value) |
| `infra/rollout/ssm.sh` | RF-C1: the usage header shows 50-install with `"${INSTALL_ARGS[@]}"` and `MIGRATION_DIGEST=`, naming the three variables the step refuses without (comment only; `bash -n` clean) |
| `apps/infrx-api/tests/i/test_observe.py` | the dead-pooler DSN replaces `str(PORTS[BOUNCER])` (tasklocal i8 pgbouncer, 55496) instead of the literal |

## 1. RV-D10F-3

- **Before** (scratch copy of b967a033 with the mutant applied): the drops-the-lease runner made `World._put_result` call `_fence(None)` -> `AttributeError` swallowed by `_settle`'s `except Exception` -> `platform_error`; HAPPY failed on the outcome and RESULT_FENCE failed with "a refused result write went on to settle: [platform_error]" - an incidental crash, not the hazard.
- **After**: HAPPY passes under the mutant (a lease-less write is accepted, as 0014 accepts it); RESULT_FENCE fails with **"a refused result write went on to settle: [TerminalCause.completed]"** - the stale generation's result written and settled, R147's F-1 exactly. `python -m tests.w.loop_mutants result_written_without_the_lease`: killed (base: killed via HAPPY).
- The PG-backed runner case (the "and/or" alternative) was not added: the fake now reproduces the store's lease-less path, so the kill is an oracle assertion.

### The W5 mutant runner's lock

- Fails-before: `test_a_pg_copy_takes_the_hosts_port_lock_whatever_its_tmpdir` with the shared `prep_worker_mutants._pg_layout` -> FAILED (the copy's lock under its own `.tmp`); with the W5 layout -> passed.
- Concurrency probe (`INFRX_D_TASK=w5`): this process took `/tmp/infrx-w5-postgres-55445.lock` (`pgharness._acquire_lock()`), then ran `w5_reconciliation_ignores_the_usd_view`: **broken_runner** (the pristine baseline's PG cases refused), **0 `w5` containers** afterwards - a refusal, not a Created orphan.
- Not changed (not owned): `tests/d/pgharness.py` itself, so the worker_main / prep_worker PG runners keep the per-copy lock -> WR-BM-1.

## 2. G8F2-M1 / M2

- M1: `flag_dry_run_writes` with ONLY the new case: **killed** ("1 failed"); in the list (both named cases, `require_every_case`): **killed** ("2 failed"). First draft died by `KeyError` (row read before the write check) = broken_runner; the case now asserts no write/audit first.
- M2 (the PG suite is outside the runner, `--ignore-glob test_*_pg.py`): `git archive` copy of aabcd042 with `"changed": True` applied, `INFRX_D_TASK=g8 pytest tests/g/ops/test_flag_pg.py` -> **1 failed, 2 passed**: `AssertionError: {'changed': True, 'enabled': True, 'enabled_after': True, ...}` at the new `--on` line; unmutated: 3 passed. `flag_dry_run_change_misreported` in the fake list: killed.

## 3. CC-1 (s15)

Design: `CAP, PER_ORG, PER_KEY = 4, 3, 2` stay for the first burst; the second burst's total cap is 16 (> 2 x PER_ORG), so alpha (2 keys x 4) can only stop at PER_ORG (its keys allow 4) and beta (1 key x 4) only at PER_KEY (its org allows 3), in any lock order. Oracle: `(alpha, beta) == (3, 2)`, every key <= 2, every refusal 429 `capacity_exhausted` with a numeric Retry-After holding nothing, then drain + conservation + no deadlock across all four gateway logs. Gateways at `port_offset` 2/3 (56942/56943, free in E2's layout), their own log numbers (201/301).

Per-scope detection (dev runs, `runner.py --only s15` with the revert tree passed as `nc-dur-cap`; scratch trees built with `reverts.py` plus two scratch-only REVERTS entries, not committed):

| Tree under `nc-dur-cap` | Control | Reason (the scoped burst) |
|---|---|---|
| committed nc-dur-cap (all three comparisons) | detected | the first burst: "5 of 16 admitted at MAX_ACTIVE_JOBS 4" |
| per-organization comparison only (`>= ` -> `>` on `max_active_jobs_per_org`) | detected | "alpha 4 at PER_ORG 3, beta's one key 2 at PER_KEY 2, keys {alpha: 2, alpha-2: 2, beta: 2} at MAX_ACTIVE_JOBS 16" (the first burst passed there) |
| per-key comparison only | detected | "alpha 3 at PER_ORG 3, beta's one key 3 at PER_KEY 2, keys {alpha: 1, alpha-2: 2, beta: 3}" (the first burst passed there) |

In dev2/dev3 the first burst passed on both per-scope trees (the total cap bound first), so without the scoped burst those runs would not have seen either revert. The split into separate controls needs `control_trees.sh`, `runner.py` CONTROLS/REQUIRED and the cell test (exactly one control per oracle): not carried in-tree -> WR-BM-2.

### E3C final run at 938b57ca (all 16 scenarios, every control)

- Command (`/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/bm/e3c/final-run.sh`, detached): clean-tree check; `CTL="$(tests/integration/backend/e3c/control_trees.sh HEAD /tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/bm/e3c/ctl-final)"` (five trees); `INFRX_E3C_RUNTIME_LOGIN=1 E3C_OUT=/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad/bm/e3c/final /usr/bin/time -v make backend-local E3C_ARGS="$CTL"`.
- 18:47:39Z-19:02:37Z, **14:56.48 wall**, exit status **0**, **gate PASS**; preflight/services/migrate/teardown ok; afterwards 0 `e3c` containers.
- Sessions: main **99 passed in 726.88 s**; controls 3 failed/1 passed, 2 failed/1 passed, 1 failed/1 passed x 3 (each reverted tree red on its own case).

| Scenario / control | Status |
|---|---|
| s01-s11, s13 | PASS (12/12) |
| s14 DUR-FENCE | PASS |
| **s15 DUR-CAP** | **PASS** - capacity burst `{alpha: 2, beta: 2, alpha-2: 0, beta-2: 0}` admitted (12 x 429); **scoped burst `{alpha: 1, alpha-2: 2}` = 3 = PER_ORG, `{beta: 2}` = PER_KEY** (7 x 429: alpha 5, beta 2); balance burst alpha 1 + debit applied = 2, beta 4/4 |
| s16 CREDIT-RATE | PASS |
| s12 the verdict itself | PASS |
| nc-journey-revoke, nc-journey-tenant, nc-upload-restart, nc-result-expiry | PASS (detected) |
| nc-admission-ready, nc-retention-durable, nc-dur-fence, nc-credit-rate | PASS (detected) |
| nc-roles-browser, nc-credit-cutover, nc-verify-repro | PASS (detected) |
| **nc-dur-cap** | **PASS** - "5 of 16 admitted at MAX_ACTIVE_JOBS 4 (DUR-CAP)" |

## 4. ASN-V1, RF-C1, the 55496 literal

- ASN-V1 fails-before: `pytest tests/i/test_alert_sns.py` with the new answers on the unfixed deliver.py -> **1 failed, 5 passed** (TypeError from main()); fixed -> 6 passed. Mutant `sns_status_not_a_number` killed; `sns_missing_metadata_is_success` still killed (its anchor is inside the `int(`).
- RF-C1: `bash -n infra/rollout/ssm.sh` 0; `tests/i/test_rollout.py` 12 passed.
- test_observe: `PORTS[BOUNCER] == local_services("i8")["pgbouncer"].host_port == 55496` asserted in-process; the case itself needs the i8 stack and was **not run** (i8 not started, per the brief); the file collects (19) and its 18 other cases pass.

## Checks (at the head named; exit codes)

| Command | Head | Exit | Result |
|---|---|---|---|
| `INFRX_D_TASK=w5 pytest -q tests/w` | 1b7458e7 (tests/w unchanged after) | 0 | **331 passed, 10 skipped** |
| `INFRX_D_TASK=w5 INFRX_MUTANTS=all pytest -q` test_mutants, test_loop_mutants, test_w5_mutants, test_w3_mutants, test_w4_mutants, test_worker_main_mutants, test_prep_worker_mutants | 1b7458e7 | 0 | **599 passed, 15 skipped** (50:19); the 15 = worker_main 7 + prep_worker 8 PG mutants "no local S3 endpoint (INFRX_M_S3_ENDPOINT...)", pre-existing; W5's PG mutants ran and were killed (`-k pg -rs` rerun: 8 passed, 15 skipped) |
| `INFRX_D_TASK=g8 pytest -q tests/g/ops` | 938b57ca | 0 | **97 passed** |
| `INFRX_D_TASK=g8 INFRX_MUTANTS=all pytest -q tests/g/ops/test_mutants.py` | 0650d8d7 (mutant targets unchanged after) | 0 | **118 passed** |
| `pytest -q tests/integration/backend/e3c/test_e3c_runner.py` | 938b57ca | 0 | 46 passed |
| E3C final run (above) | 938b57ca | 0 | gate PASS, 16/16 scenarios, 12/12 controls |
| `pytest -q tests/integration/backend/recovery/test_observe.py` | cc82f88a | 0 | 16 passed |
| `pytest tests/i/test_observe.py test_alert_sns.py test_rollout.py -k "not durable_truth_reads_holds_backlog"` | cc82f88a | 0 | 35 passed, 1 deselected (i8), 1 xfailed |
| `pytest tests/i/test_mutants.py -k "not killed and not pristine"` | cc82f88a | 0 | 7 passed |
| `ruff check` on every edited Python file | 938b57ca | 0 | clean |
| `python3 research/plan/scripts/validate_plan.py` | 938b57ca | 0 | PASS |

## Wiring requests

- **WR-BM-1** `apps/infrx-api/tests/d/pgharness.py` `lock_path()`: `return Path("/tmp") / f"{SERVICE.container}-{PORT}.lock"` (and the same in `tests/d/vkstore.py:54`), so every runner's copy (worker_main, prep_worker, W5) serialises with the host run. Then `w5_mutants._pg_layout` must go back to `prep_worker_mutants._pg_layout` (its anchor guard raises once `gettempdir()` is gone); `test_a_pg_copy_takes_the_hosts_port_lock_whatever_its_tmpdir` keeps passing.
- **WR-BM-2** split nc-dur-cap into per-scope controls: `reverts.py` REVERTS gains `nc-dur-cap-org` / `nc-dur-cap-key` (anchor `if v_count >= (p_limits->>'max_active_jobs_per_<scope>')::int then`, `>=` -> `>`, 1 occurrence); `control_trees.sh` default `want` and the SQL loop gain both ids; `runner.py` CONTROLS gains both (`oracle DUR-CAP, scenario s15, revert True`) and REQUIRED s12's `test_s12_every_sql_revert_applies_to_this_tree[...]` list both; `test_e3c_runner.py`'s cell test allows the three DUR-CAP controls and the parametrize lists both. Proof: the dev runs above (each detected by the scoped burst alone).
- **WR-BM-3** `tests/integration/backend/e3c/README.md` DUR-CAP row: add "then a burst at MAX_ACTIVE_JOBS 16 where alpha stops at the org cap and beta's one key at the key cap".

## Open issues

- The W5 lock fix covers W5's runner only until WR-BM-1.
- The test_observe i8 case was not run here (i8 held by G2-FIX per the brief).

## Estimate

Remaining for this lane: 0 h (optimistic 0 / likely 0.3 / pessimistic 1 h for review fixes), confidence high; basis: every item done with its check green; only coordinator wirings remain.
