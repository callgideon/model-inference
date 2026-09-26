# D10-MERGE-2: migrations 0024 + 0025 onto the integration tip, code head `9e4e34ca`

| Field | Value |
|---|---|
| Lane / branch | D10-MERGE-2, `codex/d10-merge-2` (worktree `.claude/worktrees/codex-d10-merge-2`) |
| Base | integration tip `claude/consumer-v1` `34f0ed28` (code tip `04ae5e21`: backend union `d9e72c9c` + W5-F5B) |
| Merged | `codex/d10-app-sql` at verified head `8f453b98` (0024), then `codex/d10-0025` at verified head `b5fc2fbc` (0025) |
| Code head | `9e4e34ca`. This evidence and the update file are committed on top |
| Isolation | D harness `INFRX_D_TASK=revoke` only (PostgreSQL 55459, `infrx-revoke-postgres[-supabase]`); Valkey for the D relay drills as `infrx-revoke-valkey` on 55439 (`INFRX_D2_VALKEY_*`); `tests/q` forced onto `INFRX_Q_VALKEY_PORT=55440` (container `infrx-q3-valkey-55440`, created and removed by the run) so the running `infrx-q3-valkey` was not touched. Never 55432. The App stack targets ran as the Makefile defines them (see "Deviations"). No hosted DB, pilot box, AWS or SSM. Nothing pushed; no rebase, reset, amend or stash |

## Commits (first-parent, after `34f0ed28`)

| # | Commit | What | Resolution |
|---|---|---|---|
| 1 | `1b47fe3b` | `merge --no-ff codex/d10-app-sql` at **8f453b98** | **clean**: no conflicted path. The branch's base `273990a0` is an ancestor of the tip (the union carries 0022/0023), and the tip changed none of the branch's paths since `273990a0` (`git diff --stat 273990a0 HEAD -- <branch paths>` empty) |
| 2 | `8e6941a8` | **W-D10A-1 v2** | `git apply` of five files; the `tests/integration/test_harness.py` hunk re-applied by hand (below) |
| 3 | `49633c28` | `merge --no-ff codex/d10-0025` at **b5fc2fbc** | **clean**: no conflicted path (its base `8f453b98` is merge 1) |
| 4 | `97f0a99d` | **WR-D10B-2** + `git rm apps/app/tests/u/operator_rpc_proposed.sql` + the `operator-postgrest.test.ts` header line + **W-D10B-1 v2** | `git apply --check` clean for both patches, applied as printed |
| 5 | `e04a2edc` | W5-F5 wiring for 0024 (not in the brief: see "Unrequested but needed") | own commit |
| 6 | `9e4e34ca` | App flips: C3A case 9, U4-P08, and the C3A stack fixture 0024 needs | own commit |

Migrations after the merges:

- `0024_console_read_port.sql` sha256 `8e0bfd6288ad716342de932053b7f7aca8c9517b551458e1b86a1a84acc394e0` (= the D10-APP-SQL fix round's figure)
- `0025_operator_console.sql` sha256 `276af0e24ed09af91386b0e7848c24f1c4aa57df1827a1ecabfa5fa035c554eb` (= the D10-0025 figure)
- 0001-0023: unchanged (`git diff 34f0ed28 9e4e34ca --name-only -- apps/app/supabase/migrations` lists only 0024 and 0025)

## Wirings applied

Each patch was cut from the evidence exactly as printed (the lines between the code fences) and hashed.

| Wiring | Source (on the merged tree) | sha256 of the patch as printed | Applied |
|---|---|---|---|
| **W-D10A-1 v2** | `research/plan/evidence/d/D10-APP-SQL-b6c0156.md` lines 383-501 | `6c55c1c9d035446c79b19a26e4e4304a64e9c2a9adda056c8d419471e97c88db` (matches the evidence's `6c55c1c9…88db`) | `backend/e3c/scenarios_surface.py`, `backend/recovery/test_restore.py`, `harness.py`, `pgstate.py` (hunks 2/3 at offsets +2/+3), `test_run.py` by `git apply --exclude=tests/integration/test_harness.py`. `test_harness.py`: `git apply --check` fails there, because the context predates the union's 0022/0023 lines. So the two lines `# D10-APP-SQL: the console read port` / `"0024_console_read_port.sql",` were inserted after `"0023_runtime_unmarked_door_revoke.sql",`. That is the exact context W-D10B-1 v2 expects, and its hunk then applied cleanly. Applied result checked: `test_harness.py::…migration_set…` passes, and the diff of commit `8e6941a8` has sha256 `c378ad84…9416` |
| **WR-D10B-2** | `research/plan/evidence/d/D10-0025-47d252e.md` lines 115-146 | `b8595f1866d6c1e28d8a6acc7be6045badcd39f16ed9b8c3a043e5349a0826a4`. The evidence prints `30f50d58…f36a` for its scratch file; the text as printed hashes to `b8595f18…26a4` (the same kind of difference the evidence notes for W-D10B-1 v1). Identical to the coordinator's `rv-1f1/wrd10b2.patch` | applied. The docstring was then re-flowed: the patch leaves a short line `container and network, and signed JWTs for anon,`, and every line is now ≤ 100 columns. `git rm apps/app/tests/u/operator_rpc_proposed.sql` |
| `operator-postgrest.test.ts` header | brief step 2 | n/a | line 4: `every committed migration plus WR-U3-1's proposed operator SQL` → `every committed migration (0025 has WR-U3-1's operator RPCs)` |
| **W-D10B-1 v2** | `research/plan/evidence/d/D10-0025-47d252e.md` lines 288-384 | `aafded6099065c4df2c4073ad6cd6fda23ef477e30cf0c0bc3bf88853e339a9f` (matches the brief) | `git apply` clean. pgstate: `SIGNED_IN = ("authenticated",)`; the three `operator_*` RPC rows `SIGNED_IN`; `infrx.console_operator(text,text)` `NOBODY`; `operator_unknown_usage` / `operator_wallet_drift` in `RELATIONS` (BROWSER), `VIEWS` and the `SERVICE_WRITES` read-only set; e3c `SIGNED_IN_FUNCTIONS` plus the three RPCs; `test_harness.py` adds 0025 after 0024 |

### Unrequested but needed: W5-F5 wiring for 0024 (`e04a2edc`)

The D10-APP-SQL evidence's own §Wiring names this as the W5-F5 bullet: "the in-test grant and policy (its third step) become a plain assertion once 0024 is in the tree". The brief did not list it.

- **Fails on the merged tree without it.** `tests/w/test_worker_main.py::test_worker_main_pg__the_monitor_login_reads_what_the_runtime_login_may_not` gave exit 1: `assert service.reconciliation is None` failed. 0024 already gives the monitor login `credit_wallet_holds.state` and its policy, so the gauges no longer disable, and the in-test `create policy monitor_reads` would collide with 0024's.
- **The change** (W5-owned test, 5+/9-):
  - The in-test grant and policy are removed.
  - The refused-once phase now composes the **runtime** login as the reader. That is the login that may not read the views, so it still exercises "42501 disables the gauges once".
  - The monitor login then publishes the pass.
  - The mutant description in `worker_main_mutants.py` names the runtime login.
- **Proof on revoke:**
  - `pytest tests/w/test_worker_main.py -k "monitor_login or login_refused or reconciliation"` gave 4 passed.
  - Both PG mutants that name this case, run through `worker_main_mutants.PG_RUNNER`, were KILLED: `pg_monitor_login_sets_a_role` (1 failed) and `pg_privilege_refusal_every_tick` (1 failed).
  - The pytest wrapper for the PG list skips without a local S3 endpoint, so the runner was called directly.
- The coordinator may take this commit or ask the W5 lane for its own form.

## App flips (brief step 3)

Both flips were run on the merged tree (after), and on a scratch clone of `9e4e34ca` with 0024 and 0025 removed (fails-before for the flipped assertions, `scratchpad/…/d10-merge-2/clone-fb`).

| Case | Before (tip / flipped test without 0024) | After (merged tree) |
|---|---|---|
| **C3A case 9** (WR-C3A-4), `apps/app/tests/c/actions-postgrest.test.ts` | Brief: `ACCEPTED (gap)` on the tip (a diagnostic only). The flipped test on the clone without 0024: `not ok 9`, message `direct insert by an unverified individual: ACCEPTED`, `+ undefined - '42501'`; `make console-c3a-real` exit 2 (8 pass, 1 fail) | The test is now `"WR-C3A-4: an unverified individual's direct key insert is refused by the table policy (0024)"` with `assert.equal(error?.code, "42501", …)`, stronger than C3A's proposed `error !== null`. `make console-c3a-real` exit 0, **9 pass / 0 fail**. Before the flip, the merged tree already printed `refused 42501` |
| **U4-P08** (WR-U4-2), `apps/app/tests/u/request-pg.test.ts` | Brief: `not ok 8 # TODO` on the tip. On the merged tree before the flip: `ok 8 … # TODO WR-U4-2 (D10 SQL) not applied…` (the TODO case now passes). The flipped test on the clone without 0024: `not ok 8`, `consumer_job_result served an unknown-usage result` (`+ 'result of b2d2a33e-…' - null`); `make console-pg` exit 2 | `WR_U4_2` constant and `todo:` removed. `make console-pg` exit 0: credit_world **6/6**, request_world **8 pass / 0 fail / todo 0** |

**Fixture change needed for C3A:** `apps/app/tests/c/realdb/actions_stack.py`.

- **What failed without it.** On the merged tree, `make console-c3a-real` gave exit 2 with **4 failed** (cases 4, 6, 7, 8), for example `create: expected success, got forbidden`.
  - The stack seeds CONSUMER_1/2 and SHARED through `tests/d`'s admission fixture. Its grant seam takes evidence as an argument, so `auth.users.email_confirmed_at` stays null.
  - 0024's policy then refuses their browser key inserts, which is the behaviour change the D10-APP-SQL evidence records.
- **The fix.** The seed now confirms their email next to FRESH's.
  - A consumer wallet exists only through a verified claim, so the seeded state now matches production.
  - This is the same fix D10-APP-SQL made to `tests/d`'s `check_operator_seams` and `check_role_matrix`.
- The unverified individual stays unverified, so case 9 still tests the refusal.

## Checks on the merged tree

All `apps/infrx-api` commands ran with `INFRX_D_TASK=revoke`, except where a row names another harness. The D runs are serial on 55459, since its flock admits one run at a time.

| Command | Head | Exit | Counts |
|---|---|---|---|
| `make api-env` | 97f0a99d | 0 | pinned env |
| `pytest -q -rs tests/d` (plain PG 16; Valkey `infrx-revoke-valkey` 55439) | e04a2edc | 0 | **897 passed, 1 skipped, 8 xfailed** (850 s). The skip is PostgREST, which needs the Supabase image |
| `INFRX_D1_IMAGE=supabase pytest -q -rs tests/d/test_ready.py test_content.py test_reads.py test_lifecycle_conformance.py test_upgrade_d10.py test_postgrest_d10.py test_catalog_pg.py test_operator_d10.py test_credit_schema.py test_followup_d10.py test_port_d10.py` | e04a2edc | 0 | **173 passed, 1 skipped, 4 xfailed** (253 s). The skip is 0024's ledger-plan case (auto_explain, declared). PostgREST ran |
| `INFRX_MUTANTS=all pytest -q -rs tests/d/test_code_mutants*.py tests/d/test_migration_mutants.py -k "d10_ or operator or well_formed or superseded or api_keys_insert"` (plain) | e04a2edc | 0 | **128 passed**: 122 mutants KILLED (every D10 mutant, 0024's 12 and 0025's 19 among them, plus `api_keys_insert_is_table_wide` and the D2 `superseded` pair), the 5 well-formed lists, and the supersession guard. The brief's `tests/d/test_mutants*.py` matches no file; the four `test_code_mutants*.py` lists contribute only their well-formed case under this `-k` |
| same, `INFRX_D1_IMAGE=supabase` | e04a2edc | 0 | **127 passed, 1 skipped** (`d10_ledger_page_sorts`, declared plain-image only) |
| **Known failures on this base.** `test_composition_pg::test_f_base…dedicated_login` (W5 gate) and `test_lifecycle_conformance::test_the_versioned_acceptance_transcripts_replay_exactly` (retention_durable) | e04a2edc | — | **Neither persists.** Both pass in the plain `tests/d` run above, and `test_lifecycle_conformance.py` also passes on Supabase. Both lanes measured them on `273990a0`; this base carries the union (W5 included) |
| Layer 3 on the D harness, Supabase image (`scratchpad/…/d10-merge-2/l3-dm2.py`, after the D10-0025 verifier's `rv-1f1/l3.py`). It adds GoTrue's columns to the template the way `provision_database` does, then runs `pgstate.apply_migrations` (0001-0025), `install_test_clock`, `seed_fixtures(conn, 20260921)`, the whole `run_role_matrix` (read, EXECUTE, write and login rows), `catalog_objects` completeness, and E3C S10's `browser_surface` with `scenarios_surface`'s constants | 9e4e34ca | 0 | **916 rows, 0 failed** (198 write rows, 14 login rows; the 35 rows naming 0024/0025 objects, E2-RLS-30 and `L3-LOGIN-infrx_monitor-columns` all pass). Completeness: no relation or function without a row, no row without an object. S10: no extra function or write |
| `pytest -q ../../tests/integration/backend/e3c` | 9e4e34ca | 0 | **34 passed** (layer 1, `test_e3c_runner.py`; the scenario modules need the e3c stack, which is not this lane's) |
| `pytest -q ../../tests/integration/test_harness.py` (the brief's `backend/test_harness.py` does not exist; this is the file with the migration list and the needle guard) | 9e4e34ca | 1 | **26 passed, 1 failed**. The failure is `test_nothing_in_this_directory_points_at_production`, the production-needle guard, **user-held, not touched**. Offenders: `runner.py` (`SUPABASE_SERVICE_ROLE_KEY`), `test_e3a_runner.py` (`supabase.co`), `test_certify.py` (`callbill.ai`), `test_i3_operations.py` (`callbill.ai`). That is **four** files, not the three the brief names (`test_i3_operations.py` is the fourth), and the same set the D10-0025 evidence lists. `…migration_set…` passes with 0024 and 0025 |
| `pytest -q ../../tests/integration/test_run.py` (W-D10A-1 v2 touches its pinned `provision_database` statements) | 9e4e34ca | 0 | **53 passed** |
| `make api-test` (root; `INFRX_D_TASK=revoke`, D Valkey 55439, `INFRX_Q_VALKEY_PORT=55440`; log `scratchpad/…/d10-merge-2/d10-merge-2-api-test.log`) | 9e4e34ca | 0 | **4536 passed, 42 skipped, 9 xfailed, 2 warnings in 2232.01s (0:37:12)**, 0 failed. For comparison, the tip `34f0ed28` in the coordinator's `checks-04ae5e21` run gave 4473 passed, 54 skipped, 9 xfailed. The 42 skips, re-run with `-rs` per suite: **37 need a local S3 endpoint** (no MinIO is assigned to `revoke`, so none was started). Those are `tests/m/test_s3.py` 15, `test_upload_restart_stack.py` 9, `test_s3_mutants.py` 2, `tests/w` `test_prep_worker.py` 3 and `test_worker_main.py` 4, and `tests/g/test_relay_readiness_pg.py` 4. Another **4 are empty mutant-parameter placeholders**: `test_pilot_mutants`, `test_prep_worker_mutants`, `test_w5_mutants` and `test_worker_main_mutants`. The last **1 is PostgREST**, which needs the Supabase image. `tests/i`'s pooler cases ran and passed: `pytest -rs tests/m tests/i tests/t tests/j tests/q` gave exit 0, 1305 passed, 27 skipped, and every skip is in `tests/m` |
| `make console-test` | 9e4e34ca | 0 | **655 tests, 604 pass, 0 fail, 51 skipped, 0 todo** (the stack cases skip without their stack, by design) |
| `make console-lint` | 9e4e34ca | 0 | 0 errors, 2 warnings, both pre-existing in untouched files (`lib/contracts/conformance.ts:4294` `ids`, `lib/contracts/fake-services.ts:81` `AuditQuery`) |
| `make console-typecheck` | 9e4e34ca | 0 | `next typegen` + `tsc --noEmit` clean |
| `make console-pg` | 9e4e34ca | 0 | credit_world **6/6**; request_world **8/8, todo 0** (P08 flipped) |
| `make console-c3a-real` | 9e4e34ca | 0 | **9/9** (case 9 flipped); durable: signup_grant ledger entries for the fresh individual = 1 |
| `make console-u3-real` | 9e4e34ca | 0 | **9/9 pass, 0 skip** on the committed 0025 (WR-D10B-2); durable: CONSUMER_1 operator adjustments = 4 (4 distinct operations) |
| `make console-built` | 9e4e34ca | 0 | build compiled; `tests/i2a` **22/22** |
| `python3 research/plan/scripts/validate_plan.py` | 9e4e34ca | 0 | PASS (942 local links; 250 documents before this evidence, 251 with it); nothing flipped in the manifest |
| fails-before, scratch clone of `9e4e34ca` minus 0024/0025: `make console-c3a-real`, `make console-pg` | clone | 2, 2 | c3a 8 pass / 1 fail (case 9); request_world 7 pass / 1 fail (P08) (above) |

## Deviations (isolation)

- **App stack targets.** `make console-pg`, `console-c3a-real` and `console-u3-real` pin their own App tasks in the Makefile: `app-u1r` 55457, `app-u4` 55456, `app-c3a` 55452, `app-u3` 55453. The brief names these targets, so they ran as defined, not under `revoke`.
  - Before each run, `docker ps -a` showed no `infrx-app-*` container.
  - Each stack labels its containers with this checkout and removed them at exit.
  - The fails-before clone did the same, labelled with the clone's path.
- **`make api-test`.** `tests/i`'s pooler harness has fixed `infrx-i8-*` names (`tasklocal` task `i8`), as in every coordinator api-test. `infrx-i8-*` containers from another run (the coordinator's `clone-04ae5e21` checks) were up when this run started. The pooler harness flock refuses a second run and never touches a foreign container. The coordinator's run finished at 04:08Z, before this run reached `tests/i`. Every pooler case passed (see the api-test row).

## Rulings for the coordinator to number (texts cited, 08 not edited)

- **1-D10R-2 (0024).**
  - Base text: `research/plan/evidence/d/D10-APP-SQL-b6c0156.md` §"Proposed ruling (coordinator numbers it)", lines 298-301.
  - Its key-insert sentence is replaced by §"Proposed ruling (replaces the key-insert sentence above)", lines 514-517: "A browser `api_keys` INSERT requires, besides 0001's owner/creator check, a verified individual (…); a consumer wallet is not required …; an unverified owner's keys are issued through the service/operator seams."
  - Enforced by `0024_console_read_port.sql:226-239`.
- **R143 amendment (0025).**
  - Text: `research/plan/evidence/d/D10-0025-47d252e.md` line 240, §"R143 amendment (one line, for the coordinator)".
  - Named codes: 42501 `forbidden: …` from `infrx.console_operator`, `invalid_request`, `not_found`, and `replayed: true` for an already revoked key.
  - Same-key suspension and revocation serialize on `pg_advisory_xact_lock(hashtextextended(<namespaced key>, 0))`: 0025:109 and 0025:136.
- **The brief's 0-CM-1 clause** ("a no-op re-revoke does not use up its idempotency key").
  - The behaviour is in 0025:142-150: an already revoked key answers `replayed: true` before any audit row is written.
  - It is asserted by `checks_operator.check_operator_revoke`: a NEW key on the revoked credential answers `replayed: true`, with no second audit row and the first `revoked_at`.
  - That sentence is not in the lane's R143 text; the coordinator adds it.

## Unresolved / for the coordinator

- **S3-dependent cases.** 37 cases were not run because no task-local MinIO is assigned to `revoke`: M1-L2 S3, the M5 upload-restart drill, the W worker/prep PG-plus-S3 cases, and G's relay readiness on PG. None of them is new in 0024/0025. The union's round 3b ran them with its own MinIO.
- **Production-needle guard.** `tests/integration/test_harness.py::test_nothing_in_this_directory_points_at_production` is red on the tip and on this head, with four offender files. User-held; not touched.
- **W5-F5 wiring `e04a2edc`.** Unrequested, needed for a green `tests/w` on 0024; accept it or re-cut it.
- **C3A stack fixture (in `9e4e34ca`).** Seeded consumers are verified. Needed by 0024's policy.
- **Not done here (App adoption lanes, per D10-APP-SQL §Wiring):** C0 `creditLedger` → `rpc("consumer_credit_ledger")`, U1R's filters and P02 lines, and the E2 layer-3 run on the e2 stack (`tests/integration/run.py --layer 3`). The D-harness role matrix above is 916/916.
- **Hosted owners.** The hosted-owner check from D10-APP-SQL (owners with a null `email_confirmed_at` who created keys in the browser) is still open. This lane may not query the hosted database.

## Remaining effort

- **Coordinator:** review, merge `codex/d10-merge-2` into `claude/consumer-v1`, number the rulings, flip D10-APP-SQL/D10-0025 in the manifest. After that, the E2/E3C stack runs on the merged SHA.
- **Estimate:** optimistic 0.5 h, likely 1 h, pessimistic 2 h. Confidence medium-high.
- **Basis:** both merges are clean, every wiring is applied and composed, the App flips are live with a fails-before, and the D suites and mutants are green on both images.

## Verification log

- 2026-09-26: written at code head `9e4e34ca`. Every command above ran at the head it names; logs are in the session scratchpad `wave4b/d10-merge-2/`.
