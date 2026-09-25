# E3C phase 2 — BACKEND-LOCAL seams re-pointed to D10 + M5 (evidence)

Task E3C "Integrate corrective backend with real services and process faults", phase 2. Lane
`codex/e3c-phase2`, worktree `.claude/worktrees/codex-e3c-phase2`.

- **Base:** `ccf37b55` (`codex/m5-merge`: integration head + D10 merge `dfc4fa75` (0019–0021,
  `PgLifecycle`, persisted `result_expires_at`, `usd_price`, dedicated logins) + M5 merge
  `2c404760` / wiring `ccf37b55` (durable upload tickets composed in `pilot.py`)).
- **Code head (evidence run):** `60ab7575`. Evidence commit follows it.
- **Status: BACKEND-LOCAL is NOT passed** — gate **FAIL** (exit 1). Every remaining non-pass
  is classified below: waits for G7, W5 or M6, or a finding against merged code. No remaining
  red case is a harness defect.
- **Label:** orchestration on real PostgreSQL (every migration 0001–0021), PostgREST, Valkey,
  MinIO (`pgsty/minio@sha256:b6bfe723…`, now the compose pin — WR-2 landed, no `--s3-image`
  deviation), real gateway / worker / collector / operator-CLI / dataset-client processes,
  E2's controlled protocol engine. Nothing about Marlin quality, GPU capacity or hosted
  behaviour. No hosted DB, box, AWS or SSM touched.

## Changed paths (owned only)

| Path | Change |
|---|---|
| `tests/integration/backend/e3c/world.py` | (1) fault points hold on EVERY candidate the tree has, first call wins, once (`point_targets`, `_hold_on`): D10's `PgLifecycle.admit_ready` exists but the gateway still admits through `PgJobStore.admit_credit` until G7 composes it — holding only the first candidate would wait on a step never taken. (2) `collect_once` composes the media store as `pilot.py` does (M5: `PgLifecycle` as `uploads=` and `content=`); over the durable ticket authority the base `MediaCollector` has no process-local tickets and stops before any delete, so the process answers `{"blocked": "M6"}` and `collector_blocked` turns that into `BLOCKED[M6]` (never a vacuous "deleted nothing"). (3) WR-4: `runtime_dsn` gives 0021's `infrx_runtime` LOGIN and a fresh random password on the e3c cluster (as owner) and `composed(runtime_login=True)` passes that DSN as the box's `DATABASE_URL` (password never printed; redacted from failure text). `INFRX_E3C_RUNTIME_LOGIN=1` flips the whole matrix to it once F-1 is fixed. `fake_only` accepts any login on the namespace's host:port. (4) `cli()` returns stdout when a non-zero exit printed no stderr (a dry run's blockers). |
| `scenarios_surface.py` | s10 runtime case on the dedicated login: probes run as the login itself AND after `set role service_role` if it can; then the box must SERVE on that login (gateway + worker start, one text job succeeds and settles once). s10 browser allowlist: 0021's `public.consumer_jobs` / `public.consumer_job_result` (C0/U4, `auth.uid()`-scoped, granted to `authenticated` only) added for `authenticated` only; `anon` executing them still fails. |
| `scenarios_retention.py` | s06 live / fail-closed: deletion asserted first (a deletion is FAIL), then `collector_blocked` (BLOCKED[M6]). |
| `scenarios_journey.py` | s01 dataset-client oracle aligned with **R106**: items the SIGINT'd client had in flight were cancelled by its disconnect (`cancelled / client_disconnected`); their same-key replay is `state_conflict`, terminal, quarantined. Every item appears exactly once (results + failures), one job per item, at least one resumed success, ≤ 2 quarantined (concurrency 2), every job settled once, wallet conserved. |
| `scenarios_accounting.py` | s09 transition wired to G8's `credit-transition`: with a historical USD job in flight, the dry run reports the `legacy_usd` in-flight job and `card_unapproved` for the provisional seed card, exit 1, writes nothing; applying it is refused (exit 1) and no flag, wallet, CREDIT ledger sum or USD ledger row moves; the USD job keeps `legacy_usd`. s11 scrub race now `BLOCKED[M6]` only (D10 merged). |
| `test_e3c_runner.py`, `runner.py` | three new s12 regressions + their `REQUIRED` entries (below). |

Commits: `42c831ec` (WIP: barrier candidates, collector composition, WR-4, allowlist), `60ab7575`
(WIP: R106 oracle, G8 wiring, s11 label), plus the evidence commit.

## Commands (host Linux 7.0.0-1010-aws, 16 cores, other lanes running)

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `make api-env` | 0 | pinned env |
| 2 | `runner.py --keep --only s12 --out $SC/boot` (at `ccf37b55`) | 3 | stack PASS (preflight/services/migrate); s12 PASS; others NOT RUN (deselected) |
| 3 | `runner.py --keep --reuse --only s03,s06,s07,s10 --out $SC/pre1` (phase-1 harness, `ccf37b55`) | 1 | `6 failed, 9 passed`: s03 **PASS** (M5), nc-upload-restart PASS; s06 ×3 FAIL (the phase-1 collector was composed process-local — it deleted live media: the fake seam this phase removes); s07 FAIL; s10 runtime FAIL (probe ran as `postgres`); s10 browser FAIL (0021's two consumer reads not in the allowlist) |
| 4 | `pytest -q tests/integration/backend/e3c` (no stack) after the s12 additions | 0 | `30 passed` |
| 5 | mutants on the new s12 cases (a scratch copy of `world.py`): first-candidate-only barrier / phase-1 `world.py` / `collector_blocked` inert / port unchecked in `fake_only` | 1 each | `1 / 3 / 1 / 1 failed` of the 3 new cases (each kills its target) |
| 6 | **run 1** `make backend-local` (`E3C_OUT=$SC/run1`) at `42c831ec`, detached | 1 (make 2) | 12:34 wall; `14 failed, 57 passed, 4 skipped`; new reds were harness: s01 dataset (oracle predates R106), s09 transition (`pytest.fail` placeholder once G8's command exists) |
| 7 | `runner.py --keep --only s01,s09 --out $SC/dev2` at `60ab7575` (pre-commit tree = `60ab7575`) | 3 (NOT RUN: s12 deselected) | `7 passed`: s01 ×2 (dataset: 2 results, 2 quarantined per R106), s09 ×3, nc-journey-revoke, nc-credit-cutover |
| 8 | `tests/integration/consumer-local.sh --break-seam readiness --out $SC/seam-readiness` | 1 | preflight:seam PASS; `seam:readiness` **FAIL** (`tests.g.mutants:pilot_starts_unreachable` killed, 16.4 s) — the broken seam is detected |
| 9 | `tests/integration/consumer-local.sh --break-seam expiry --out $SC/seam-expiry` | 1 | `seam:expiry` **FAIL** (`tests.contracts.mutants:upload_expiry_ignored` killed, 7.3 s) |
| 10 | `pytest -q tests/integration/backend` (layer 0, no stack) at `60ab7575` | 1 | `1 failed, 147 passed, 79 skipped`; the one failure is `recovery/test_observe.py::test_i3b_ob10_every_rule_and_panel_names_a_declared_metric` ("families with no panel": `infrx_db_pool_*`) — a base-tree fact, fixed by `i8-panels` (`463d313d`) on `claude/consumer-v1`, which `codex/m5-merge` predates; nothing here touches it |
| 11 | **run 2 (the evidence run)** `setsid nohup /usr/bin/time -v make backend-local` (`E3C_OUT=$SC/run2`, log `$SC/e3c-runner-run2.log`), started 07:33:10Z at `60ab7575` | 1 (make 2) | 12:52 wall; **`12 failed, 59 passed, 4 skipped in 752.36s`**; gate **FAIL**; head at start = end `60ab7575`, clean; deviations `[]`; teardown PASS, `docker ps -a \| grep -c e3c` → 0 |

`$SC` = the session scratchpad; verdict files stay there (raw evidence, not committed).
Run-1 driver PID: the waiter matched a transient `setsid` parent; the runner itself was PID
1265989 (log `$SC/e3c-runner.log`). Run 2: log `$SC/e3c-runner-run2.log`.

## Matrix (run 2 at `60ab7575`)

| Scenario | Status | Cases pass / total | Non-passing cases → classification |
|---|---|---|---|
| s01 identity → CLI grant → key → modes → revoke; E1C dataset resume | **PASS** | 2 / 2 | — (E1C's `dataset.py` now in the tree; was BLOCKED[E1C]) |
| s02 two gateways, two tenants | PASS | 1 / 1 | — |
| s03 upload across gateway processes and SIGKILLs | **PASS** | 5 / 5 | — (RV-02 closed by M5 + D10; was 0 / 5) |
| s04 admission readiness | FAIL | 0 / 4 | [text] executed + debited before a readiness marker → **G7** (admit through `admit_ready`) + W5; [video] preparation claimed before readiness → **W5** (`claim_preparation_ready`) + G7; late rejection run and charged → **G7** + W5; permanent refusal still `preparing` after 45 s → **W5** (D-19 `fail_preparation`) |
| s05 crash at each step | FAIL | 7 / 9 | [admission] `failed / preparation_failed` after 3 preparation attempts → **W5** (D-19; `_resume` "staged by another process"); [readiness] prepared with no acceptance → **G7** + W5. upload (M5), attachment, outbox, prep, claim, output, settle PASS |
| s06 collectors / scrub | FAIL | 0 / 3 | scrub: content rows kept past the persisted expiry → **M6**; live, fail-closed: BLOCKED[M6] (no durable retention pass; the base collector stops before any delete on the durable authority) → **M6** |
| s07 persisted expiry on every read | FAIL | 0 / 1 | the route reports settled + 86400 after a restart at 86400 while `jobs.result_expires_at` persisted settled + 600 → **G7** (`infrx/gateway/routes/jobs.py:145-157` still recomputes `settled_at + limits.result_ttl_s`; its own ponytail note says "until D5 stores `jobs.result_expires_at`; then read the store's") |
| s08 dependency outages | FAIL | 1 / 3 | [postgres], [s3]: no answer within 60 s → **finding F-2** (merged code; I8 merged without it) |
| s09 CREDIT transition with USD history | **PASS** | 3 / 3 | — (G8 wired; was BLOCKED[G8]) |
| s10 runtime / browser / operator roles | FAIL | 2 / 3 | runtime: the dedicated login is correctly locked down (no owner, no money rewrite, no DDL, cannot `set role service_role`), but the box cannot SERVE on it → **finding F-1** |
| s11 reconcile races | BLOCKED | 2 / 3 | scrub race BLOCKED[M6] → **M6** |
| s12 the verdict (no stack) | PASS | 30 / 30 | — |
| s13 discovery vs admission | FAIL | 0 / 2 | claims (`compliance.zdr`, concurrency 16, `tools` refused) → **G7**; projection check BLOCKED[G7] → **G7** |

**Totals (run 2):** 75 cases = 59 passed, 12 failed, 4 skipped. Non-passing 16 classified:
**G7 6** (s04 text, s04 late, s05 readiness, s07, s13 ×2), **W5 3** (s04 video, s04 permanent,
s05 admission), **M6 4** (s06 ×3, s11 scrub race), **harness 0**, **real defects in merged
code 3 cases / 2 findings** (s08 ×2 → F-2; s10 runtime → F-1). The s04/s05 G7 and W5 rows are
co-dependent (G7 writes the marker, W5 refuses to prepare without it); each is listed under the
lane whose change the observed symptom names first.

Negative controls: nc-journey-revoke, nc-journey-tenant, **nc-upload-restart** (now counted:
s03 green), nc-roles-browser, nc-credit-cutover, nc-verify-repro **PASS**; nc-admission-ready,
nc-retention-durable NOT RUN (their scenarios are red until G7/W5/M6 — a revert over a red
scenario proves nothing); nc-result-expiry NOT RUN (s07 red until G7). E2C's two revert controls
(`--break-seam readiness|expiry`) come out **FAIL** as required (commands 8–9).

Phase 1 → phase 2 (same scenarios): s01 BLOCKED → PASS, s03 FAIL → PASS, s09 BLOCKED → PASS;
s05 6/9 → 7/9 (upload); s10 runtime moved from "the `postgres` login can do anything" to
"the dedicated login is right but unusable" (F-1).

## New s12 regressions (failure oracles)

| Test | Catches | Proven |
|---|---|---|
| `test_s12_a_barrier_holds_on_whichever_candidate_the_process_calls` | a fault point that holds only its first candidate (waits for ever on `admit_ready` while the gateway calls `admit_credit`) or holds twice | fails on first-candidate-only and on phase-1 `world.py` (cmd 5) |
| `test_s12_a_collector_that_cannot_run_is_blocked_not_passed` | a collector that could not run read as "deleted nothing" (vacuous green) | fails with `collector_blocked` inert |
| `test_s12_the_dedicated_runtime_login_is_a_real_box_database` | WR-4 DSN refused as fake-only, or the same login on a foreign port accepted | fails with the port unchecked |

## Findings against merged code (not fixed here; not owned)

- **F-1 — the pilot pool cannot run on the dedicated runtime login.**
  `apps/infrx-api/infrx/gateway/pilot.py:129-139` (`configure_connection`) runs
  `set role service_role` on every pooled connection whenever the DSN is off port 6543
  (`session_state_allowed`), and `connection_pool` (`:155-183`) always installs it; the worker
  uses the same pool (`infrx/worker/__main__.py:126`). 0021 makes `infrx_runtime` "a member of
  no role" by design, so every connection fails `permission denied to set role
  "service_role"` and the worker exits 2 (`DATABASE_URL did not answer (PoolTimeout)`).
  `jobstore.connector(set_role=False)` exists (D10) but the pilot pool does not use it.
  **Reproduce:** `INFRX_E2_NAMESPACE=e3c` stack up; `alter role infrx_runtime login password
  '<random>'`; connect as it and run `set role service_role` → `InsufficientPrivilege`
  (measured directly), or run2 case `test_s10_the_runtime_login_cannot_become_an_owner_or_rewrite_money`
  (worker log: 3× the error, then exit 2). Owner: pilot composition (I8 WR-I8-6's runtime side
  / coordinator wiring). Suggested fix shape: no `set role` when the login is not a member of
  `service_role` (or an explicit setting), keeping the statement timeout from the role default.
  Once fixed, rerun with `INFRX_E3C_RUNTIME_LOGIN=1` so the whole matrix runs on that login.
- **F-2 — an acceptance on a stalled PostgreSQL or object store is not bounded.** With the
  container paused, `POST /v1/jobs` gives no answer within 60 s (both runs). PostgreSQL:
  the pool (`pilot.py:165`) has a connect timeout but no client-side query timeout; the
  server-side `statement_timeout` cannot fire on a stalled server. Object store:
  `infrx/media/s3.py:80` `read_timeout=30` × 2 attempts is already ≥ 60 s per call. I8 is
  merged (`63011a31`) and did not bound these. The 45 s bound itself is E3C's proposed ruling
  (phase 1), not yet numbered — the coordinator should rule the bound, then assign the fix.
  **Reproduce:** `runner.py --only s08 -k "postgres or s3"`.

## Wiring requests

- WR-1, WR-2, WR-3 landed (compose pin, namespace row, `make backend-local`). WR-4 applied
  inside this lane (no file outside it), blocked on F-1 for the running box.
- No new wiring request. F-1 needs a product change in `pilot.py` (not this lane's path).

## Unresolved / next reruns

1. After **G7** merges (admit_ready composed, route reads persisted expiry, discovery): rerun;
   expect s04 text/late, s05 readiness, s07, s13 green; then run nc-result-expiry.
2. After **W5** (claim_preparation_ready, fail_preparation): s04 video/permanent, s05
   admission; then `--control nc-admission-ready=<tree with the G7/W5/D10 readiness commits
   reverted>`.
3. After **M6** (durable retention pass): re-point `collect_once` to M6's entry (the
   `blocked` answer tells where), unblock s06 ×2 and s11; then `--control
   nc-retention-durable=<tree>`.
4. F-1 fix → `INFRX_E3C_RUNTIME_LOGIN=1` full rerun; F-2 needs a ruling + owner.
5. The layer-0 `test_observe` ob10 failure disappears on a base that includes `463d313d`.

## Resource usage (run 2)

`/usr/bin/time -v make backend-local`: 12:52.33 wall, 204 s user + 37 s sys, max RSS
118,688 kB (runner and waited children; box processes run in their own sessions). Stack torn
down at the end; 0 `infrx-e3c*` containers.

## Remaining effort

| Optimistic | Likely | Pessimistic | Confidence | Basis |
|---|---|---|---|---|
| 2 h | 4 h | 8 h | medium | Three 13-minute reruns (after G7, W5, M6 land) plus re-pointing `collect_once` to M6's entry and the two revert controls (`nc-admission-ready`, `nc-retention-durable`) each needing a reverted scratch tree; pessimistic if G7/W5's admission surface moves the fault points, or F-1/F-2 need re-declared oracles. |

## Verification log

- 2026-09-25 (E3C phase 2): seams re-pointed to D10 + M5 on `ccf37b55`; evidence run at
  `60ab7575` gate FAIL (12 failed / 59 passed / 4 skipped; 16 non-passes classified: G7 6,
  W5 3, M6 4, defects 3 cases in 2 findings, harness 0); both `--break-seam` controls FAIL as
  required; no hosted DB, box, AWS or paid operation; stack torn down.
