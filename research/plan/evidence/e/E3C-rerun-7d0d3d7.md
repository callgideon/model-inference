# E3C interim rerun: harness re-pointed to G7, G8 lock bound, RUNTIME-LOGIN and M6 phase 2 (evidence)

Task E3C, phase 2, interim rerun. Lane `codex/e3c-rerun`, worktree `.claude/worktrees/codex-e3c-rerun`.

- **Base:** `19670a8c` (`claude/consumer-v1`: D10, M5, M6 phase 1, G7 and its wiring, the G8 lock-bound fix, and the E3C phase-2 harness).
- **Merged in during the lane (plain merges, as the coordinator asked):** `445a8bcd` (RUNTIME-LOGIN, which fixes F-1) as `623c3479`; `25826ab9` (M6 phase 2, which deletes `gc.py`) as `6b5d32b9`; `130e948b` (consumer-v1 tip: CERTIFY-WIRING, E1C profile flip) as `fb86c153`.
- **Heads:** run 1 (interim, G7 head) at `5b38ab08`. Run 2 (runtime login, merged tree) at `7d0d3d7b`. The evidence commit follows.
- **Status: BACKEND-LOCAL NOT passed.** Both runs end with gate **FAIL** (exit 1). Every red case below is classified. Run 2 has no harness reds and no BLOCKED cases.
- **Label:** orchestration on real PostgreSQL (migrations 0001–0021), PostgREST, Valkey, MinIO, real gateway, worker, collector and operator-CLI processes, and E2's controlled engine. The results say nothing about Marlin quality, GPU capacity or hosted behaviour. No hosted DB, pilot box, AWS or SSM was touched. Nothing was pushed.

## Changed paths (owned only: `tests/integration/backend/e3c/`)

| File | Change |
|---|---|
| `scenarios_surface.py` | s13 now reads G7's `/v1/models` as F2C-C `PublishedModel` entries. (a) The claims check requires a non-empty `data`, no `retention.zero_data_retention` or `compliance.zdr`, no `capacity`, and `video.max_seconds <= 82`. It then sends a real request for every name in `capability.parameters`: an advertised name must not be refused with 400. It also sends one for every name in `unsupported_parameters`: a name listed as refused must really be refused with 400. (b) The projection check was BLOCKED[G7] before. It now requires `pm.violations(entry, profile) == []` against two profiles: the approved release profile (`published_fixtures.deployed_profile()`, 82 s) and the profile of the box as it runs, built from the box's own environment (`config.from_env`) and the clone's catalog rows (`PgCatalogDirectory`). |
| `world.py` | (1) The `expiry-recompute` bypass targeted `Jobs.result_expiry`, which G7 removed, so it could no longer install. It now wraps `Relay._owned`, which status, result and same-key replay all go through, and rewrites `result_expires_at` from the current `RESULT_TTL_S`. (2) Wiring request 3 (amended): `collect_once` is M6's `RetentionCollector(PgLifecycle(...), pilot.object_store(settings)).sweep()`. Its DSN has `connect_timeout=5`, and its connector follows the pilot pool's rule: no `set role` on a dedicated login (`pilot.dedicated_login`, R127). `grace_passed()` returns `GRACE_S + 60`. |
| `scenarios_retention.py` | s06-live and s06-dark move the store clock past the persisted grace before the collectors run. s06-live then moves it back so the job stays inside its own deadlines. |
| `scenarios_outage.py` | s08 measures the answer time and records it even past the bound: the client waits up to 180 s and records `refusal`. The bound follows R130 per service: PostgreSQL 40 s; S3 (a video request whose stall lands in preparation) 60 s, R130's preparation-then-store clause. |
| `scenarios_accounting.py` | New s09 case for the G8 lock bound: an admission parked holding `require_feature('credit_admission')` FOR SHARE on its own connection. `credit-transition --to legacy_usd --drain-timeout-s 5` must refuse `state_conflict … open_transactions` within bound + 15 s and move no flag or money. The s11 scrub race was BLOCKED[M6] before. It now races `reconcile` against M6's collector process on a settled job past its expiry: content must end scrubbed, no money moves, and the job settles once. |
| `runner.py`, `test_e3c_runner.py` | New regression `test_s12_every_bypass_installs_on_this_tree[×4]`: each bypass is installed in a fresh process. The s09 lock case and the four s12 cases are added to `REQUIRED`. |

## Commands

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `pytest -q tests/integration/backend/e3c` (no stack) | 0 | 34 passed |
| 2 | Same file, `-k bypass_installs`, with the phase-2 `world.py` put back | 1 | `1 failed, 3 passed`: `[expiry-recompute]` fails, so the new regression catches a bypass whose target is gone |
| 3 | `runner.py --only s07,s09,s13` (dev) | 1 | s07 PASS, s13 PASS 2/2, nc-result-expiry PASS; the new s09 case failed on the oracle's shape (the CLI answers a `state_conflict` envelope); fixed in `5b38ab08` |
| 4 | **Run 1 (interim, G7 head):** `setsid nohup /usr/bin/time -v make backend-local` (`E3C_OUT=$SC/run1`, log `$SC/e3c-rerun.log`), started 17:03:12Z at `5b38ab08` | 2 (runner 1) | 12:40 wall; **9 failed, 68 passed, 3 skipped**; gate FAIL; head clean at start and end; deviations `[]`; teardown PASS |
| 5 | `consumer-local.sh --break-seam readiness` (at `623c3479`) | 1 | `seam:readiness` **FAIL** as required (mutant `tests.g.mutants:pilot_starts_unreachable` killed, 23.3 s) |
| 6 | `consumer-local.sh --break-seam expiry` (at `623c3479`) | 1 | `seam:expiry` **FAIL** as required (mutant `tests.contracts.mutants:upload_expiry_ignored` killed, 7.0 s) |
| 7 | `INFRX_E3C_RUNTIME_LOGIN=1 runner.py --only s06,s11` (dev, after wiring 3) | 1 | The collector could not connect as `infrx_runtime` (`set role`); fixed by composing its connector the way the pilot does (`7d0d3d7b`) |
| 8 | Scratch mutant, not committed: `infrx.content_referenced` redefined to return NULL on the clone before the s06-live collectors; `--only s06` | 1 | s06-live **FAIL** ("a collector deleted a live job's media: media/…/source"), so the oracle catches a liveness check that is ignored; file restored with `git checkout` |
| 9 | **Run 2 (runtime login, merged tree):** `INFRX_E3C_RUNTIME_LOGIN=1 setsid nohup /usr/bin/time -v make backend-local` (`E3C_OUT=$SC/run2`, log `$SC/e3c-rerun-run2.log`), started 17:59:59Z at `7d0d3d7b` | 2 (runner 1) | 12:07 wall; **8 failed, 72 passed, 0 skipped**; gate FAIL; head clean at start and end; deviations `[]`; teardown PASS; `docker ps -a \| grep -c e3c` → 0 |

`$SC` is the session scratchpad. The verdict files stay there as raw evidence and are not committed. A concurrent coordinator run held the run lock once; that attempt returned BLOCKED[E2C], exit 3, and was retried.

## Matrix, run 1 (interim, G7 head `5b38ab08`, owner login)

| Scenario | Status | Pass / total | Non-passing cases → classification |
|---|---|---|---|
| s01, s02, s03 | PASS | 2/2, 1/1, 5/5 | — |
| s04 | FAIL | 0/4 | text → **F-3** (+W5); video → **W5**; late rejection → **F-3** (+W5); permanent refusal → **W5** |
| s05 | FAIL | 7/9 | admission → **W5**; readiness → **F-3** (+W5) |
| s06 | FAIL | 0/3 | ×3 → **M6** (collector not yet re-pointed; M6 phase 2 not merged at this head) |
| s07 | **PASS** | 1/1 | — (was G7) |
| s08 | FAIL | 2/3 | s3: 503 at 40.04 s against a 40 s oracle → **harness** (the oracle had no margin; the stall hit the 40 s preparation bound; now bounded by R130's preparation clause) |
| s09 | PASS | 4/4 | — (the new G8 lock-bound case passes) |
| s10 | FAIL | 2/3 | runtime → **F-1** (fixed by `445a8bcd`, green in run 2) |
| s11 | BLOCKED | 2/3 | scrub race → **M6** |
| s12 | PASS | 33/33 | — |
| s13 | **PASS** | 2/2 | — (was G7) |

Run 1 totals: 80 cases; 68 PASS, 9 FAIL, 3 BLOCKED. By classification: W5 3, M6 4, harness 1, defects in merged code 4 cases (F-3 3, F-1 1).

## Matrix, run 2 (runtime login `infrx_runtime`, merged tree `7d0d3d7b`)

| Scenario | Status | Pass / total | Non-passing cases → classification |
|---|---|---|---|
| s01 | PASS | 2/2 | — |
| s02 | PASS | 1/1 | — |
| s03 | PASS | 5/5 | — |
| s04 | FAIL | 0/4 | [text] executed and debited before readiness → **F-3** (+W5); [video] preparation claimed before readiness → **W5**; late rejection run and charged → **F-3** (+W5); permanent refusal still `preparing` after 45 s → **W5** (D-19 `fail_preparation`) |
| s05 | FAIL | 7/9 | [admission] `preparation_failed` after 3 attempts → **W5**; [readiness] prepared with no acceptance → **F-3** (+W5) |
| s06 | FAIL | 2/3 | live **PASS**, dark **PASS** (M6); scrub: `job_results.body` and `request_record` scrubbed, **5 `stream_chunks` deltas kept past the journal TTL** → **F-4** |
| s07 | PASS | 1/1 | — |
| s08 | **PASS** | 3/3 | — (F-2 bounded, below) |
| s09 | PASS | 4/4 | — |
| s10 | **PASS** | 3/3 | — (F-1 closed: the box serves on `infrx_runtime`; the whole matrix ran on it) |
| s11 | FAIL | 2/3 | scrub race: the reconcile answers typed, no money moves, settled once; the same 5 journal deltas remain → **F-4** |
| s12 | PASS | 33/33 | — |
| s13 | PASS | 2/2 | — |

Run 2 totals: 80 cases; 72 PASS, 8 FAIL, 0 skipped. By classification: **W5 3** (s04 video, s04 permanent, s05 admission); **M6 0**; **harness 0**; **defects in merged code 5 cases in 2 findings** (F-3: s04 text, s04 late, s05 readiness; F-4: s06 scrub, s11 scrub race).

Negative controls in run 2: nc-journey-revoke, nc-journey-tenant, nc-upload-restart, **nc-result-expiry** (now run, on the re-pointed bypass), nc-roles-browser, nc-credit-cutover and nc-verify-repro all **PASS**. nc-admission-ready and nc-retention-durable are NOT RUN because they are revert-type: s04 is still red, and s06 is red on its scrub case. E2C's two revert controls come out **FAIL** as required (commands 5 and 6).

### The six cases previously attributed to G7

| Case | Now | Reason |
|---|---|---|
| s04 text | FAIL → **F-3** (+W5) | G7 did not compose `admit_ready`. `infrx/gateway/routes/relay.py:167` still calls `self.jobs.admit_credit(prepared, idem)`; no code under `infrx/gateway` or `infrx/worker` calls `admit_ready` (grep). G7's own evidence defers it (`G7-2bbfe0e.md:103`). W5's merge gate names "G7 admit_ready", but W5 does not add it either (`codex/w5-merge` 09d83a97: no `admit_ready` under gateway/worker). No lane owns it. |
| s04 late rejection | FAIL → **F-3** (+W5) | Same cause. |
| s05 readiness | FAIL → **F-3** (+W5) | Same cause. |
| s07 | **PASS** | G7 reads the persisted `result_expires_at` on every read. nc-result-expiry detects the recompute bypass. |
| s13 claims | **PASS** | G7's projection: no ZDR, no concurrency; every advertised parameter accepted and every refused one refused. |
| s13 projection | **PASS** | `violations` is empty against the approved (82 s) profile and against the running box's profile. |

## F-2 re-measurement (POST /v1/jobs with the dependency paused; R130)

| Service | Run 1 (`5b38ab08`, owner login) | Run 2 (`7d0d3d7b`, `infrx_runtime`) | Bound (R130) | Holds |
|---|---|---|---|---|
| PostgreSQL | 503 `dependency_unavailable` in **20.0 s** | 503 `dependency_unavailable` in **20.0 s** | 40 s (one stalled call; `intake.DEPENDENCY_BOUND_S` = 20) | **yes** |
| Object store (video) | 503 `dependency_unavailable` in **40.0 s** (40.04) | 503 `dependency_unavailable` in **40.0 s** | 60 s ceiling for a video request (preparation bound = fetch 30 + probe + 10 = 40 s) | **yes** |

In both cases the same key recovers exactly one job after the fault is reverted; it succeeds and settles once. **F-2 is closed on this head.** Phase 2 had measured no answer within 60 s.

## Findings against merged code (not fixed here; not owned)

- **F-1: closed** by RUNTIME-LOGIN (`445a8bcd`). s10 passes on `infrx_runtime`, and the whole run 2 matrix ran on that login.
- **F-2: closed** by G7's R130 bounds (measured above).
- **F-3 (new, carried from G7): the gateway never admits through `admit_ready`.** `apps/infrx-api/infrx/gateway/routes/relay.py:167-168` admits with `admit_credit`/`admit`, so no readiness marker is written at acceptance. D10's `PgLifecycle.admit_ready` has no caller in `infrx/gateway` or `infrx/worker`. Consequence today: a text job runs and is debited while its acceptance is incomplete, and a late rejection is charged (RV-05 is still open).
  - **Consequence after W5 merges as-is:** W5's marker-gated claim would refuse every job the relay admits (W5's own 1-W5-RULES-2 hazard).
  - **Reproduce:** `runner.py --only s04 -k text` (fault point `admission`: the barrier holds after `PgJobStore.admit_credit`, and the worker prepares, runs and debits the job). Or run `grep -rn admit_ready apps/infrx-api/infrx/gateway apps/infrx-api/infrx/worker`, which prints nothing.
  - **Owner:** the coordinator must assign it. It is G7's deferred item and part of W5's merge gate.
- **F-4 (new): the stream journal is never pruned.** `PgStreamStore.expire` (`apps/infrx-api/infrx/state/journal.py:121`, which calls `infrx.expire_journal`, 0017) has no caller in any composed process. `grep -rn "\.expire(" infrx/worker infrx/gateway infrx/operations` prints nothing. M6's `RetentionCollector` scrubs `job_results` and `request_record` but not `stream_chunks`. SSE delta content therefore stays in PostgreSQL past `JOURNAL_CHUNK_TTL_S` (3600 s) for ever.
  - **Reproduce:** run2 s06 scrub (store clock +4000 s, one retention pass leaves `stream_chunks deltas: 5`), or `runner.py --only s06 -k scrub`.
  - **Owner:** worker reaper or retention composition (M6 wiring request 1 composes `RetentionCollector.run` in the worker; the journal pass belongs next to it).
  - **Proposed fix shape:** the same scheduler calls `PgStreamStore.expire()` each interval.

## Wiring requests

- WR-3 (amended, from M6): applied inside this lane (`world.py`, `scenarios_retention.py`) as the coordinator specified, plus the collector's connector following `pilot.dedicated_login`. The s06-live oracle was proven by a scratch mutant (command 8).
- None new outside the lane. F-3 and F-4 are product changes outside E3C's paths.

## Unresolved / next rerun

1. F-3 (relay → `admit_ready`) and W5 (`claim_preparation_ready`, `fail_preparation`) merge together. Expected result: s04 ×4 and s05 ×2 green. Then run `--control nc-admission-ready=<scratch tree with the readiness commits reverted>`.
2. F-4 fixed (journal pruning composed): s06 scrub and s11 scrub race green. Then run `--control nc-retention-durable=<scratch tree with D10/M6 liveness reverted>`.
3. M6's production scheduling of `RetentionCollector` (its WR-1/WR-2) is not yet composed. E3C runs the collector as its own process, which is the RV-03 question as posed. The final run should use M6's worker entry once it exists.
4. `scenarios_crash.py:23` unused `stack` import (ruff F401) is pre-existing; left alone.

## Resource usage

Run 1: 12:40 wall, 231 s user + 37 s sys, max RSS 121,060 kB. Run 2: 12:07 wall. Stacks were torn down after every run: 0 `infrx-e3c*` containers.

## Remaining effort

| Optimistic | Likely | Pessimistic | Confidence | Basis |
|---|---|---|---|---|
| 1 h | 2 h | 5 h | medium | One ~13-minute final rerun with `INFRX_E3C_RUNTIME_LOGIN=1` once F-3 + W5 and F-4 land, plus two revert-type controls (`nc-admission-ready`, `nc-retention-durable`), each needing a reverted scratch tree and one scenario run. Pessimistic if W5's claim path moves the s04/s05 fault points or F-4's fix changes what s06 reads. |

## Verification log

- 2026-09-25 (E3C interim rerun): harness re-pointed to G7 (s13 on `PublishedModel`, expiry bypass on `Relay._owned`), R130 (s08 measured), the G8 lock bound (new s09 case), RUNTIME-LOGIN and M6 phase 2 (WR-3). Run 1 at `5b38ab08`: gate FAIL, 9 failed / 68 passed / 3 skipped. Run 2 on `infrx_runtime` at `7d0d3d7b`: gate FAIL, 8 failed / 72 passed / 0 skipped, with W5 3 and defects 5 (F-3 ×3, F-4 ×2); harness 0 and M6 0. F-1 and F-2 closed. Both `--break-seam` controls FAIL as required. No hosted DB, box, AWS or paid operation; stacks torn down; nothing pushed.
