# G-GATES at 9b21339 — the six go/no-go gates of `infra/rollout/README.md` §0, run at the tip

Support lane G-GATES (evidence only; no manifest task), branch `codex/g-gates`, worktree
`.claude/worktrees/codex-g-gates`, checkout of the integration tip
`9b21339a0ec15a14aa019d969a88a118cb418c70` (RELEASE candidate for this record). Runner: Opus.
Run window 2026-09-26T07:01Z–11:21Z on the development host (not the pilot box). Nothing
in the tree was changed: a failing gate is a finding here, never a fix. No AWS, SSM, hosted
Supabase or box access; no secrets in any command (G3's env values are the README's placeholders).
Docker isolation: `INFRX_D_TASK=e2c` (postgres 55448, valkey 55493) for the docker-gated
suites, the i8 harness only after polling it free, and the rehearsal on its own namespace.

## Verdict table

| Gate | Command (as run, repo root unless noted) | Exit | Wall | Verdict | Note |
|---|---|---|---|---|---|
| G1 | `make api-env` (tree venv, 0 s, exit 0), then `git rev-parse HEAD; git status --porcelain` | 0 | 0.02 s | **PASS** | `9b21339a0ec15a14aa019d969a88a118cb418c70`, porcelain empty (the venv built in the tree; `apps/app/node_modules` from `pnpm install --frozen-lockfile`, both ignored) |
| G2a | `INFRX_D_TASK=e2c INFRX_D2_VALKEY_PORT=55493 INFRX_D2_VALKEY_CONTAINER=infrx-e2c-valkey INFRX_Q_VALKEY_PORT=55493 PYTEST_ADDOPTS=-rsxX make check` | 2 | 2,595 s | **FAIL** | stopped in `api-test`: `2 failed, 4544 passed, 42 skipped, 9 xfailed` (43:09). make does not continue past a failed prerequisite, so the other seven targets were run as a supplement (next row) |
| G2a' (supplement) | same env, `make -k api-mutants console-test console-lint console-typecheck console-mutants console-built bench-test` | 2 | 9,674 s | **FAIL** | `api-mutants` line 1: `22 failed, 3689 passed, 33 skipped` (2:32:56); every console target and `bench-test` green (below); `api-mutants` line 2 (E4B list) not reached by make, run by hand: `255 passed` (562 s, exit 0) |
| G2b | `cd apps/infrx-api && INFRX_D_TASK=i8 uv run --frozen --no-sync pytest -q tests/i` (the brief's form; i8 polled free first) | 1 | 491 s / 216 s | **FAIL** (two runs) | 07:58Z: `3 failed, 231 passed, 1 xfailed, 4 errors`; 10:58Z: `44 failed, 193 passed, 1 xfailed, 1 error`. Both are the i8 PostgreSQL/PgBouncer stand-in not coming up (`not ready: connection timeout expired`, `127.0.0.1:55450 ... Connection refused`) - see F-5 |
| G2b' (control) | `cd apps/infrx-api && INFRX_D_TASK=e2c uv run --frozen --no-sync pytest -q tests/i` (i8 polled free) | 0 | 236 s | PASS (control only) | `238 passed, 1 xfailed`; the same cases also passed inside `api-test` above. The gate row stays FAIL: its command as given did not exit 0 |
| G3 | `docker build --provenance=false -q -f apps/infrx-api/deploy/Dockerfile -t infrx-runtime:9b21339a0ec15a14aa019d969a88a118cb418c70 apps/infrx-api`, then the README's placeholder `printf ... \| docker run --rm -i --network none infrx-runtime:<sha> python /app/deploy/preflight.py probe --mode pilot --env-file /dev/stdin` | 0 / 0 | 13 s / 7 s | **PASS** | image `sha256:9baee3d31041…797d`; `{"ok": true, "python": "3.12.14", "mode": "pilot", "validated_mode": "pilot", "problems": [], "warnings": []}`; `--rm` left no container |
| G4 | `cd apps/infrx-api && uv run --frozen python -c '...preflight.engine_problems(pathlib.Path("../../models/marlin2b/serve.sh"), "pilot")'` | 0 | 0.12 s | **PASS** | prints `[]`. The check covers flags, the image digest pin, loopback publishing and `serving-version.json` naming the digest; it does **not** read `processor_config_digest` / `preprocessor_config_digest`, which are still `null` (P-06, `processor_digests_todo`). So G4 as written passes while P-06 is open; P-06 is listed below as a RELEASE blocker in its own right |
| G5 | `REHEARSAL_NS=infrx-ggates apps/infrx-api/deploy/rehearse.sh`, then `docker ps -a --filter label=ai.infrx.rehearsal=infrx-ggates` plus name/volume/network scans for `infrx-ggates*` | 0 | 137 s | **PASS** | `REHEARSAL PASSED`, 48 `PASS` checks, 0 `FAIL`; `teardown: nothing infrx-ggates-* left`; the scans found no container, volume or network. Read first: the box is `--network none`, every unit joins it (`container:`), the migrate drill uses its own bridge `$NS-net`; no `-p` anywhere, so no host port |
| G6 | from the documents only (no AWS or hosted read) | - | - | **NOT RUN[operator]** | see §G6 |

Supplement results (G2a'), every one exit 0 unless named: `console-test` `# tests 663 / pass 607 / fail 0 / skipped 56`; `console-lint` `0 errors, 2 warnings`; `console-typecheck` types generated, `tsc --noEmit` clean; `console-mutants` contracts 212/212, v 40/40, u 212/212, c 185/185, a all killed, catalog 46/46; `console-built` `next build` then `# tests 22 / pass 22`; `bench-test` `116 passed`.

## G2 failing ids

**api-test (G2a), 2 failed** — `tests/q/test_reconcile.py`:
- `test_q3_drill__dr13_shape_the_rebuild_after_a_sigkill_comes_from_postgresql`
- `test_q3_drill__valkey_sigkilled_under_queued_and_running_traffic_loses_no_job`

Both: `RuntimeError: docker kill infrx-q3-valkey-55493: ... No such container` (`tests/q/vkharness.py:125`). F-1 below: an environment-composition failure plus a harness defect, not a product regression. Control: `INFRX_D_TASK=e2c INFRX_Q_VALKEY_PORT=55493 uv run --frozen pytest -q tests/q/test_reconcile.py` alone: `73 passed` (7 s, exit 0).

**api-mutants (G2a'), 22 failed.** A rerun of `INFRX_MUTANTS=all pytest -q tests/i/test_mutants.py` alone on e2c with i8 free (11:08Z–11:21Z, `3 failed, 358 passed`, 780 s) separates them:
- Deterministic, reproduced (4):
  - `tests/contracts/test_mutants.py::test_mutant_is_killed[lc_reregistration_resets_the_grace]` misdeclared: its anchor in `infrx/contracts/fakes/lifecycle.py` (`...raise refuse(R.bytes_changed, "the key already names other bytes")\n        return row`) no longer exists; the `raise` is now followed by an `if identity.origin is ContentOrigin.written ...` block (`tests/contracts/mutants.py:2351`). This one was not rerun alone; it is anchor text, so it is independent of the environment.
  - `tests/i/test_mutants.py::test_mutant_is_killed[pilot_key_not_forbidden]` misdeclared: the anchor `forbidden_in=("pilot",)),\n)` in `deploy/preflight.py` is gone because W5-F5's `infrx_monitor` entry now follows it (`preflight.py:163-164`).
  - `tests/i/test_mutants.py::test_mutant_is_killed[install_args_pool_pin_dropped]` misdeclared: the anchor ` DATABASE_POOL_MAX_SIZE=6")` in `infra/runbooks/rollout.md` is gone because E4C-RUNBOOK-2 (`e9b21287`/`13a30bb5`) appended `ACCOUNTING_REGIME=credit ACTIVE_RATE_CARD_VERSION=...` to that `INFRX_SET` line (`rollout.md:57`).
  - `tests/i/test_mutants.py::test_mutant_is_killed[durable_forgets_unknown_holds]` **survived**: `test_ops_continuous__the_alert_rules_without_a_producer_are_exactly_the_known_ones` passes with the mutant applied (`1 passed, 18 deselected`). The declared case does not prove "the durable exporter feeds the reconcile rules".
- Environmental, did not reproduce alone (18, all `broken_runner` from i8 cases erroring in the copies while other lanes' `infrx-i8-*` containers (`infrx-i8-postgres-supabase`, `infrx-i8-migrated-supabase`) were live on the host): `budget_forgets_the_worker_pool`, `budget_session_limit_raised`, `stand_in_pooler_in_session_mode`, `stand_in_pooler_replays_prepares`, `pool_prepares_again`, `pool_sets_session_state_on_6543`, `connector_sets_role_on_6543`, `probe_passes_an_allowed_operation`, `probe_ignores_role_membership`, `probe_passes_without_function_list`, `durable_backlog_counts_the_future`, `durable_hides_drift`, `probe_attrs_blind`, `probe_timeout_identity_blind`, `probe_identity_reads_its_own_timeout`, `drift_credit_charge_unchecked`, `drift_usd_only`, `drift_ignores_wallet_drift`.

**tests/i with `INFRX_D_TASK=i8` (G2b).** Run 1 (07:58Z): errors at setup of `test_pooler.py::test_ops_continuous__session_state_is_lost_and_leaked_on_the_transaction_pooler`, `...auto_prepared_statements_break_on_the_transaction_pooler`, `...transaction_scoped_patterns_survive_the_transaction_pooler` and `...the_composed_runtime_pool_holds_on_the_transaction_pooler`. It also failed `test_pooler.py::test_ops_continuous__the_computed_budget_is_what_the_session_pooler_admits`, `test_privilege_probe.py::test_ops_continuous__the_least_privilege_login_passes_and_privileged_ones_fail` and `test_rollback_drill.py::test_ops_recover__the_settlement_check_passes_either_regime_and_fails_the_unsettled`. Run 2 (10:58Z): 1 setup error (the first case above) and 44 failed. Those 44 are the budget case plus 43 `tests/i/test_mutants.py::test_mutant_is_killed[...]` cases (`denied_read_tolerated` ... `revert_runtime_before_tree`), each `broken_runner: pristine baseline: the unmutated tree fails the list's own cases`.

## Skips (by name and reason)

api-test (42):
- `tests/d/test_postgrest_d10.py:123` (1): needs `INFRX_D1_IMAGE=supabase`.
- S3 not started (the README's G2 does not start MinIO; `consumer-local.sh` does):
  - `tests/g/test_relay_readiness_pg.py:55,93` (4)
  - `tests/m/test_s3.py` lines 236/248/258/264/274/286/301/323/354/370/383/412/443/582/700 (15)
  - `tests/m/test_s3_mutants.py:38` (2)
  - `tests/m/test_upload_restart_stack.py:100,206,219,245×4,274,307` (9)
  - `tests/w/test_prep_worker.py:895,929,968` (3)
  - `tests/w/test_worker_main.py:745,900,950,1013` (4)
  - All give the reason "no S3-compatible endpoint / INFRX_M_S3_ENDPOINT".
- Empty parameter sets (not a case that did not run): `tests/m/test_pilot_mutants.py:72`, `tests/w/test_prep_worker_mutants.py:77`, `tests/w/test_w5_mutants.py:79`, `tests/w/test_worker_main_mutants.py:77` (4).

api-mutants (33): `tests/m/test_s3_mutants.py:38` (18), `tests/w/test_worker_main_mutants.py:86` (7), `tests/w/test_prep_worker_mutants.py:86` (8), all "no local S3 endpoint".

console-test (56), each "needs the task-local stack / DSN not set", as `make check` runs them:
- `tests/c/realdb/stack.py` C0 cases 124-138 (15)
- `tests/c/realdb/actions_stack.py` cases 89-97 (9)
- `tests/u/operator_stack.py` U3-DB01..09 (9)
- `tests/u/credit_world.py` U1R-P01..08 (8)
- `tests/u/request_world.py` U4-P01..08 (8)
- `tests/a/pg_up.py` A2-PG-01..05 (5)
- I2A-BUILT-01/02 (2): "no .next build" at `console-test` time; they run and pass in `console-built`.

xfail (9 api-test, strict, reasons printed): `test_catalog_pg` 1, `test_credit_jobstore_conformance` 3, `test_jobstore_conformance` 1, `test_reads` 3, `tests/i/test_observe.py::...every_alert_rule_names_a_metric_something_produces` 1 (F4).

## Findings

- **F-1 (G2a, harness + gate definition).** `make check` runs every API suite in **one** pytest process. On e2c's single Valkey port, D2's `vkstore` (`infrx-e2c-valkey`) and Q's `vkharness` (derived name `infrx-q3-valkey-55493`) share 55493. `vkharness.ensure()` adopts any server already listening on its port (`if _listening(): ... _started = True`), and `kill_and_restart()` then targets a container it never created. That contradicts its own R63 rule ("Only ever this lane's own container"): with the defaults the same code would SIGKILL another lane's `infrx-q3-valkey` on 55462, which is why the defaults were not used. `consumer-local.sh` avoids it only by running suites in separate processes. Needed: either `vkharness` refuses a listener it does not own, or the G2 row names a composition that gives Q its own port.
- **F-2 (G2a', deterministic).** Three stale mutant anchors (`lc_reregistration_resets_the_grace`, `pilot_key_not_forbidden`, `install_args_pool_pin_dropped`) left by the wave-4b union, W5-F5 and E4C-RUNBOOK-2 edits. Each needs its anchor re-declared by the owning lane (contracts/P25, I8, E4C-RUNBOOK).
- **F-3 (G2a', deterministic).** `durable_forgets_unknown_holds` survives: the declared case does not kill it. The case is weak or the mutant needs another declared case (I8/M6 owner).
- **F-4 (host contention).** The 18 i8 `broken_runner` mutants in the long run did not reproduce alone. The i8 harness is shared by name (`infrx-i8-*`, one flock on 55450), and other lanes held or recycled it during the run. A G2 on this host is only meaningful with i8 free for its full duration (about 3 h here).
- **F-5 (G2b command form).** `tests/i` with `INFRX_D_TASK=i8` failed twice. The same tree with `INFRX_D_TASK=e2c` passed (238/1 xfail), both standalone and inside `api-test`. Docker events during run 2 show `infrx-i8-postgres` killed and recreated within seconds, repeatedly, while this run alone held i8 (10:59:23–10:59:48Z). That is consistent with two i8-named PostgreSQL owners in one run: `pgharness` under task i8 resolves to `infrx-i8-postgres:55450`, the same container `tests/i/pooler.py` owns. This was not fully proven; the file that loads `pgharness` under tests/i was not traced. The README's own row has no `INFRX_D_TASK`, which would default the D harness to d1's 55432, which lane rules forbid. The G2 row should name `INFRX_D_TASK=e2c` (or another non-i8 task) for `tests/i`.
- **F-6 (G4 scope).** `engine_problems` passes with P-06's processor digests `null`. G4 does not gate P-06; whoever accepts RELEASE must check P-06 separately.

## G6 (documents only; NOT RUN[operator])

| Input | Documents say | Status |
|---|---|---|
| `/model-inference/pg_journal_url` exists | `infra/runbooks/rollout.md` §1 `DATABASE_URL` row and its log (line 300): created 2026-09-24T01:03Z, v1 SecureString, by the coordinator; after W10b the env file uses `infrx_runtime`/`infrx_monitor` and `pg_journal_url` stays the owner login | recorded in documents; existence not re-read (an SSM read is an AWS call) - NOT RUN[operator] |
| Hosted backup rehearsed by I3B before any hosted migration | `15-pending-inputs.md` P-25 (lines 115, 169): the only hosted backup on record is the coordinator-host dump `hosted-20260924T050746Z`; PITR/backup policy ⚠️ TO BE VERIFIED; rule = a verified logical dump before every migration/rollout (restore.md:30-40, 286-313). I3B evidence (`evidence/i/I3B-followup-0737ebe.md:9,228,288`) is local only; the hosted rehearsal is a coordinator operation not yet recorded | **not met** on record - NOT RUN[operator] |
| G6B can issue a scoped key and revoke one | G6B is a reused-baseline task (`progress-state.json` `reused_baseline`); `evidence/g/G6B-db03f0b.md` proves issue/rotate/revoke locally (tests + 55 mutants). On hosted: P-24 records two pre-cutover consumer keys active (15:114), and their revocation is operator-held (E4C-readiness §3, RB:41). No hosted issue+revoke is recorded | local PASS on record; hosted NOT RUN[operator] |

## Host load during the run

142 one-minute samples of `/proc/loadavg` (1-min) on a 16-core host, 07:15Z–10:47Z: min 0.51, median 3.90, max 38.23 (about 07:20Z, while other lanes' E3C/i8/g8 stacks were running: `infrx-e3c-*`, `infrx-e3crestj-postgrest`, `infrx-g8-postgres`, `infrx-i8-*`, `infrx-q3-valkey`, `infrx-m5-s3`, `infrx-d2-postgres`). 61 GiB RAM, 51 GiB available at start.

## Output tails

G1:
```
9b21339a0ec15a14aa019d969a88a118cb418c70
(git status --porcelain: empty)
```
G2a (`make check`, last 40 lines up to the exit marker; long lines cut at 240 chars):
```
SKIPPED [1] tests/m/test_s3.py:274: M1-L2 (owner: M): no S3-compatible endpoint - start the E2 stack's s3 service and export INFRX_M_S3_ENDPOINT (and INFRX_M_S3_LOCAL_CREDS=1 for MinIO)
SKIPPED [1] tests/m/test_s3.py:286: M1-L2 (owner: M): no S3-compatible endpoint - start the E2 stack's s3 service and export INFRX_M_S3_ENDPOINT (and INFRX_M_S3_LOCAL_CREDS=1 for MinIO)
SKIPPED [1] tests/m/test_s3.py:301: M1-L2 (owner: M): no S3-compatible endpoint - start the E2 stack's s3 service and export INFRX_M_S3_ENDPOINT (and INFRX_M_S3_LOCAL_CREDS=1 for MinIO)
SKIPPED [1] tests/m/test_s3.py:323: M1-L2 (owner: M): no S3-compatible endpoint - start the E2 stack's s3 service and export INFRX_M_S3_ENDPOINT (and INFRX_M_S3_LOCAL_CREDS=1 for MinIO)
SKIPPED [1] tests/m/test_s3.py:354: M1-L2 (owner: M): no S3-compatible endpoint - start the E2 stack's s3 service and export INFRX_M_S3_ENDPOINT (and INFRX_M_S3_LOCAL_CREDS=1 for MinIO)
SKIPPED [1] tests/m/test_s3.py:370: M1-L2 (owner: M): no S3-compatible endpoint - start the E2 stack's s3 service and export INFRX_M_S3_ENDPOINT (and INFRX_M_S3_LOCAL_CREDS=1 for MinIO)
SKIPPED [1] tests/m/test_s3.py:383: M1-L2 (owner: M): no S3-compatible endpoint - start the E2 stack's s3 service and export INFRX_M_S3_ENDPOINT (and INFRX_M_S3_LOCAL_CREDS=1 for MinIO)
SKIPPED [1] tests/m/test_s3.py:412: M1-L2 (owner: M): no S3-compatible endpoint - start the E2 stack's s3 service and export INFRX_M_S3_ENDPOINT (and INFRX_M_S3_LOCAL_CREDS=1 for MinIO)
SKIPPED [1] tests/m/test_s3.py:443: M1-L2 (owner: M): no S3-compatible endpoint - start the E2 stack's s3 service and export INFRX_M_S3_ENDPOINT (and INFRX_M_S3_LOCAL_CREDS=1 for MinIO)
SKIPPED [1] tests/m/test_s3.py:582: M1-L2 (owner: M): no S3-compatible endpoint - start the E2 stack's s3 service and export INFRX_M_S3_ENDPOINT (and INFRX_M_S3_LOCAL_CREDS=1 for MinIO)
SKIPPED [1] tests/m/test_s3.py:700: M1-L2 (owner: M): no S3-compatible endpoint - start the E2 stack's s3 service and export INFRX_M_S3_ENDPOINT (and INFRX_M_S3_LOCAL_CREDS=1 for MinIO)
SKIPPED [2] tests/m/test_s3_mutants.py:38: M1-L2 (owner: M): no S3-compatible endpoint - start the E2 stack's s3 service and export INFRX_M_S3_ENDPOINT (and INFRX_M_S3_LOCAL_CREDS=1 for MinIO)
SKIPPED [1] tests/m/test_upload_restart_stack.py:100: M5 (owner: M): no S3-compatible endpoint - start the task-local MinIO (infrx-m5-s3) and export INFRX_M_S3_ENDPOINT, INFRX_M_S3_LOCAL_CREDS=1
SKIPPED [1] tests/m/test_upload_restart_stack.py:206: M5 (owner: M): no S3-compatible endpoint - start the task-local MinIO (infrx-m5-s3) and export INFRX_M_S3_ENDPOINT, INFRX_M_S3_LOCAL_CREDS=1
SKIPPED [1] tests/m/test_upload_restart_stack.py:219: M5 stack drill needs MinIO, the task-local PostgreSQL and D10: no INFRX_M_S3_ENDPOINT
SKIPPED [4] tests/m/test_upload_restart_stack.py:245: M5 stack drill needs MinIO, the task-local PostgreSQL and D10: no INFRX_M_S3_ENDPOINT
SKIPPED [1] tests/m/test_upload_restart_stack.py:274: M5 stack drill needs MinIO, the task-local PostgreSQL and D10: no INFRX_M_S3_ENDPOINT
SKIPPED [1] tests/m/test_upload_restart_stack.py:307: M5 stack drill needs MinIO, the task-local PostgreSQL and D10: no INFRX_M_S3_ENDPOINT
SKIPPED [1] tests/w/test_prep_worker.py:895: I2B-R4: no local S3 endpoint - start a MinIO and export INFRX_M_S3_ENDPOINT with INFRX_M_S3_LOCAL_CREDS=1
SKIPPED [1] tests/w/test_prep_worker.py:929: I2B-R4: no local S3 endpoint - start a MinIO and export INFRX_M_S3_ENDPOINT with INFRX_M_S3_LOCAL_CREDS=1
SKIPPED [1] tests/w/test_prep_worker.py:968: I2B-R4: no local S3 endpoint - start a MinIO and export INFRX_M_S3_ENDPOINT with INFRX_M_S3_LOCAL_CREDS=1
SKIPPED [1] tests/w/test_prep_worker_mutants.py:77: got empty parameter set for (mutant)
SKIPPED [1] tests/w/test_w5_mutants.py:79: got empty parameter set for (mutant)
SKIPPED [1] tests/w/test_worker_main.py:900: I2B-R4: no local S3 endpoint - start a MinIO and export INFRX_M_S3_ENDPOINT with INFRX_M_S3_LOCAL_CREDS=1
SKIPPED [1] tests/w/test_worker_main.py:950: I2B-R4: no local S3 endpoint - start a MinIO and export INFRX_M_S3_ENDPOINT with INFRX_M_S3_LOCAL_CREDS=1
SKIPPED [1] tests/w/test_worker_main.py:745: I2B-R4: no local S3 endpoint - start a MinIO and export INFRX_M_S3_ENDPOINT with INFRX_M_S3_LOCAL_CREDS=1
SKIPPED [1] tests/w/test_worker_main.py:1013: I2B-R4: no local S3 endpoint - start a MinIO and export INFRX_M_S3_ENDPOINT with INFRX_M_S3_LOCAL_CREDS=1
SKIPPED [1] tests/w/test_worker_main_mutants.py:77: got empty parameter set for (mutant)
XFAIL tests/d/test_catalog_pg.py::test_v2_case_on_the_postgres_catalog[credit_rate__an_alias_moved_after_acceptance_does_not_move_the_job] - 0007 lists PUBLIC deployments only: the case moves the public alias onto the private dev deployment
XFAIL tests/d/test_credit_jobstore_conformance.py::test_credit_jobstore_conformance_on_postgres[credit_admit__refusals_leave_no_job_and_no_hold] - G1R request 2(c), unassigned: PostgreSQL admission of a provider_dev credential on its own de
XFAIL tests/d/test_credit_jobstore_conformance.py::test_credit_jobstore_conformance_on_postgres[credit_admit__a_replay_is_pinned_and_never_crosses_regimes] - 0011 (D2 review M7) answers a key of the other regime `state_conflict`; the fake a
XFAIL tests/d/test_credit_jobstore_conformance.py::test_credit_jobstore_conformance_on_postgres[credit_settle__an_unknown_usage_hold_is_reconciled_on_the_credit_wallet] - G1R request 2(c), unassigned: PostgreSQL admission of a provider_dev 
XFAIL tests/d/test_jobstore_conformance.py::test_jobstore_conformance_on_postgres[dur_cap__total_org_and_key_limits_reject_with_retry_guidance] - F2 conformance: the case reuses one key across two organizations
XFAIL tests/d/test_reads.py::test_runtime_role_runs_the_credit_jobstore[credit_admit__refusals_leave_no_job_and_no_hold] - G1R request 2(c), unassigned: PostgreSQL admission of a provider_dev credential on its own dev endpoint is not built 
XFAIL tests/d/test_reads.py::test_runtime_role_runs_the_credit_jobstore[credit_admit__a_replay_is_pinned_and_never_crosses_regimes] - 0011 (D2 review M7) answers a key of the other regime `state_conflict`; the fake answers `idempotency_conf
XFAIL tests/d/test_reads.py::test_runtime_role_runs_the_credit_jobstore[credit_settle__an_unknown_usage_hold_is_reconciled_on_the_credit_wallet] - G1R request 2(c), unassigned: PostgreSQL admission of a provider_dev credential on its own de
XFAIL tests/i/test_observe.py::test_ops_continuous__every_alert_rule_names_a_metric_something_produces - F4: W5/G own the runtime producers of ComponentDown, LeaseLost, PlatformFailureRate, QueueSaturated, QueueStalled, ReaperTerminalized, 
2 failed, 4544 passed, 42 skipped, 9 xfailed, 2 warnings in 2589.23s (0:43:09)
make: *** [Makefile:13: api-test] Error 1
check_exit=2 check_wall=2595s check_end=2026-09-26T07:58:24Z
```
G2b run 2 (tail):
```
    _PortalFactoryType = Callable[[], AbstractContextManager[anyio.abc.BlockingPortal]]

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
XFAIL tests/i/test_observe.py::test_ops_continuous__every_alert_rule_names_a_metric_something_produces - F4: W5/G own the runtime producers of ComponentDown, LeaseLost, PlatformFailureRate, QueueSaturated, QueueStalled, ReaperTerminalized, 
44 failed, 193 passed, 1 xfailed, 2 warnings, 1 error in 214.80s (0:03:34)
```
G2b control (`INFRX_D_TASK=e2c`, tail):
```
=========================== short test summary info ============================
XFAIL tests/i/test_observe.py::test_ops_continuous__every_alert_rule_names_a_metric_something_produces - F4: W5/G own the runtime producers of ComponentDown, LeaseLost, PlatformFailureRate, QueueSaturated, QueueStalled, ReaperTerminalized, 
238 passed, 1 xfailed, 2 warnings in 234.86s (0:03:54)
```
G3:
```
sha256:9baee3d31041ee64495984985bd7a55d3d906a923fa77a476fd869f8b937797d
build_exit=0 build_wall=13s
{"ok": true, "python": "3.12.14", "mode": "pilot", "validated_mode": "pilot", "problems": [], "warnings": []}
probe_exit=0 probe_wall=7s
```
G4:
```
[]
```
G5:
```
0026_commits.sql ended the transaction (a COMMIT or ROLLBACK inside a migration): what ran before it may be committed without its history rows, and statements after the COMMIT in this file ran too; no
PASS a migration with its own COMMIT stops the plan: exit 4 (got 4), history still 0001,0002,0003,0004,0005,0006,0007,0008,0009,0010,0011,0012,0013,0014,0015,0016,0017,0018,0019,0020,0021,0022,0023,00

=== result
REHEARSAL PASSED
teardown: nothing infrx-ggates-* left
rehearse_exit=0 wall=137s
leftover_containers=[]
leftover_named=[]
leftover_volumes=[]
leftover_networks=[]
end 2026-09-26T07:05:31Z
```

## What blocks RELEASE today

1. **G2 red at 9b21339.** Four deterministic mutant defects (F-2, F-3) need the owning lanes to re-declare three anchors and strengthen or redeclare one case. Two gate-definition issues (F-1 Valkey port sharing plus the `vkharness` adopt-a-foreign-listener defect; F-5 the `tests/i` task value) must be settled so that G2 can exit 0 on this host without touching another lane's namespace. G2 then needs a full rerun with i8 held free for about 3 h (F-4).
2. **P-06 open** (F-6): `processor_config_digest` / `preprocessor_config_digest` null in `serving-version.json`, and the PINNED list in `infra/runbooks/artifacts.py` is not extended. G4 does not catch this.
3. **G6 not provable from here:** the hosted backup rehearsal (only one coordinator-host dump on record; PITR unknown; P-25 dump rule) and a hosted G6B issue+revoke (P-24's two pre-cutover keys) are operator/coordinator reads. The `pg_journal_url` existence is recorded but was not re-read.
4. Carried from `E4C-readiness-2026-09-26.md` §2b/§3 (not re-verified here): the known-good schema proof stops at 0023 (0024/0025 on the tip, and this rehearsal applied through 0026); the hosted apply of 0019+ is pending; P-01/P-02/P-05/P-24/P-25 enactments are operator-held; `MEDIA_BASE_URL`; the `weights_sha256` ratification.

G3 (runtime image, pilot probe `"ok": true`) and G5 (the full local rehearsal, clean teardown) do not block.

## Deviations

- G2a's env adds `INFRX_D2_VALKEY_PORT/_CONTAINER` and `INFRX_Q_VALKEY_PORT` = e2c's 55493 (what `tests/integration/gates.py:295` sets). Without them the Q harness defaults to Q3's live `infrx-q3-valkey` on 55462 (another lane). It also adds `PYTEST_ADDOPTS=-rsxX` so that skips are printed by name.
- G2a' (`make -k` of the remaining targets, plus the E4B mutant line by hand) and the controls (`tests/q/test_reconcile.py` alone, `tests/i` on e2c, `tests/i/test_mutants.py` alone) are supplements. None changes a gate verdict.
- G5 ran with `REHEARSAL_NS=infrx-ggates` (the script's own knob) instead of the default `infrx-i2b`, so that its label-scoped teardown cannot touch another lane's rehearsal.
- Left on the host: the images `infrx-runtime:9b21339a…` and `infrx-ggates-runtime:9b21339a…` (tags; no containers, volumes or networks). The rehearsal's rebuild retagged `infrx-runtime:<sha>` to image `0dec1dbb47c2`; G3 probed `9baee3d3…`.

## Verification log

- 2026-09-26T11:21Z (G-GATES, Opus runner): created; gates run at 9b21339a between 07:01Z and 11:21Z.
