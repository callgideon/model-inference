# E3C early BACKEND-LOCAL on the wave-4b union (9d61d1e1): run, harness fixes, validation (evidence)

Task E3C, phase 2, early signal on the union. Lane `codex/e3c-rerun`. This is **not the final verdict**: W5-ADMIT-WIRING is in its fix round, and the final SHA comes later.

- **Union:** `9d61d1e1` (`codex/wave4b-union` = `e607b705` + d10-followup `c584f54a` (0022, W1–W4) + m6-wiring `28dd1af2` (WR-M6W-1) + w5-merge `f18c72f7`). Merged `--no-ff` into `codex/e3c-rerun` as `43178b58`. The merge was clean.
- **Earlier on this lane:** `e607b705` merged as `07995f39` (clean). Pre-staging: V-1 at `225b36ba`, `6f4a416f`, `5fadfdbe`.
- **Harness fixes after the early run:** `9107a5c6`. The validation run of the affected scenarios used this head. The evidence commit follows.
- Namespace e3c only (56900–56999). Every run used `INFRX_E3C_RUNTIME_LOGIN=1`, so the box runs on `infrx_runtime`. No hosted DB, box, AWS or SSM; nothing pushed.

## Absolute verdict paths (V-1 request)

All paths are under `SC=/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference--claude-worktrees-infrx-impl/7aae6bdd-47a8-4788-aef9-0b8e137e1f2b/scratchpad`.

| Run | Head | Verdict | Log |
|---|---|---|---|
| interim run 1 (owner login) | `5b38ab08` | `$SC/run1/verdict.json` | `$SC/e3c-rerun.log` |
| interim run 2 (runtime login) | `7d0d3d7b` | `$SC/run2/verdict.json` | `$SC/e3c-rerun-run2.log` |
| warm-up / preflight / s12 + control check | `5fadfdbe` | `$SC/warm/verdict.json` | `$SC/warm.log` |
| **early full run (union)** | `43178b58` | `$SC/final/verdict.json` | `$SC/e3c-final.log` |
| validation of the fixes (s04, s06, s11 + both controls) | `9107a5c6` | `$SC/v2/verdict.json` | `$SC/v2.log` |

V-1 (`225b36ba`): s08's bound is R130's one-stall 40 s plus `CLIENT_MARGIN_S = 2.0` for the client round trip, for both services. The s3 video path makes one stall, in the preparation source write. Measured in the early run: PostgreSQL 20.1 s, S3 40.1 s, both 503 `dependency_unavailable`. The bound holds.

## Early full run (`43178b58`), exactly as staged

Command: `$SC/final-run.sh 9d61d1e1`. It merges, runs `uv sync`, builds the control trees from the merged HEAD, then runs `INFRX_E3C_RUNTIME_LOGIN=1 make backend-local E3C_ARGS="--control nc-admission-ready=… --control nc-retention-durable=…"`, detached.
- Started 21:44:08Z; wall 25:35. The host load average was 44–64 on 16 cores from other lanes (the phase-2 runs took 12–13 min).
- Result: make exit 2 (runner exit 1), gate **FAIL**. Main session: `5 failed, 76 passed`. Control sessions: nc-admission-ready `4 failed`; nc-retention-durable `1 failed, 2 passed`.
- Head clean at start and end; deviations `[]`; teardown clean (0 `infrx-e3c*` containers).

| Scenario | Status | Pass / total | Non-passing → classification |
|---|---|---|---|
| s01 | PASS | 2/2 | |
| s02 | PASS | 1/1 | |
| s03 | PASS | 5/5 | |
| s04 | FAIL | 2/4 | late rejection → **F-5** (defect); permanent refusal → **harness** H-4 |
| s05 | **PASS** | 9/9 | (admission and readiness crash points: W5 + admit_ready) |
| s06 | FAIL | 2/3 | scrub: `jobs.request_record` kept → **harness** H-5 (result body and journal deltas scrubbed: F-4 closed) |
| s07 | PASS | 1/1 | |
| s08 | PASS | 3/3 | |
| s09 | PASS | 4/4 | |
| s10 | PASS | 3/3 | |
| s11 | FAIL | 1/3 | live reconcile "moved money" → **harness** H-6 (a loaded-host race); scrub race → **harness** H-5 |
| s12 | PASS | 34/34 | |
| s13 | PASS | 2/2 | |
| nc-admission-ready | PASS as run, **not for the right reason** | | The tree's worker died at start (`NameError: PgLifecycle`), so every s04 case "failed". **Harness** H-1/H-3. Re-run below: PASS for the right reason. |
| nc-retention-durable | PASS, for the right reason | | s06-live on the tree: "a collector deleted a live job's media" |
| the 7 bypass/DB-defect controls | PASS | | |

## Harness defects found and fixed (`9107a5c6`, owned paths only)

- **H-1 (runner): a reverted tree that does not start was read as a detection.** `reverted_status()` makes a control INVALID[harness] when any case failed with pilotbox's `RuntimeError: the worker|gateway exited|was not ready`. A product assertion that merely quotes a dead worker (s10's F-1 text) stays FAIL. Regression: `test_s12_a_reverted_tree_that_does_not_start_is_invalid_not_a_detection`.
- **H-2 (runner): control cases overwrote the main run's evidence.** The control re-runs cases under the same names into the same `cases/` dirs. `case_out()` now writes control cases to `<out>/<control>/cases`. Regression: `test_s12_a_control_writes_its_cases_apart_from_the_main_run`.
- **H-3 (`control_trees.sh`): the admission revert killed the worker.** Reverting W5 wiring 1 (`fa446ae7`) whole also removed the `PgLifecycle` import that M6-WIRING now shares. The builder now reverse-applies f5784d0c, d58139f3, 16ff5771, 245dcb75 and 53dc95ae, strips only the worker's `readiness=PgLifecycle(connect, limits=limits)` argument (asserted to appear exactly once), refuses a tree that still composes the barrier, and fails on any undefined name (`ruff --select F821`).
- **H-4 (s04 permanent refusal): the premise no longer named a permanent refusal.**
  - The old premise was a deleted staged object. W5 item 3 (`PERMANENT`, `infrx/worker/preparation.py:113`) classifies a vanished object as `not_found`, which is temporary and retried within `preparation_deadline_at`.
  - The case now uses W5's permanent class: the engine counts the prompt past the context (`tokenize_count` 1,000,000, so `context_length_exceeded`).
  - It requires `failed`/`preparation_failed` after exactly 1 preparation attempt, 0 inference attempts, settled once.
- **H-5 (s06 scrub, s11 race): the oracle expected the request record gone at the result's expiry.**
  - 0020's `content_referenced` keeps a job's request record (like its sources) until `settled_at + job_readiness.retention_s`. That is the admission's persisted content retention: about 604,800 s measured here, longer than the 600 s result TTL.
  - Before the union no marker row existed, so the retention coalesced to 0.
  - s06 is now judged per row, on the worker's own housekeeping (M6's entry at a 2 s cadence):
    1. at +4000 s the result body and journal deltas are gone, and the request record is still present (not deleted early);
    2. 410 and the replay are checked;
    3. past the job's retention (+60 s) the request record is gone.
  - s11 checks the rows the race scrubs (result and journal).
- **H-6 (s11 live reconcile): the "live" premise was not guaranteed.**
  - At 0.2 s per delta a loaded host let the job settle before the CLI returned, so the wallet moved by the job's own debit (10000 → 9999.4592), not the reconcile's.
  - Now `LIVE_GAP_S` = 3.0 s per delta.
  - A job that settled before the wallet check raises `HarnessError` (INVALID, premise), never a product FAIL.

`pytest tests/integration/backend/e3c`: 37 passed.

## Validation of the fixes (`9107a5c6`)

Command: `INFRX_E3C_RUNTIME_LOGIN=1 runner.py --only s04,s06,s11 $(control_trees.sh HEAD …) --out $SC/v2`. Main session: 1 failed, 9 passed in 134 s. Control sessions: nc-admission-ready 3 failed / 1 passed; nc-retention-durable 2 failed / 1 passed.

| Check | Result |
|---|---|
| s04 | 3/4. text PASS, video PASS, permanent refusal **PASS** (H-4: `preparation_failed` at the first attempt through `fail_preparation`); late rejection **FAIL** → F-5 |
| s06 | **PASS 3/3.** Live and dark PASS. Scrub PASS: result and journal gone at +4000 s, request record kept until its retention, then gone. **F-4 closed** by M6-WIRING's journal prune. |
| s11 | **PASS 3/3** |
| nc-admission-ready | **PASS for the right reason.** On the reverted tree (the worker starts), s04 text is "executed before durable eligibility (RV-05): preparation 1, prepared 1, inference 1, debits 1"; video: preparation claimed; late rejection: executed and debited |
| nc-retention-durable | **PASS for the right reason.** On the tree, s06-live: "a collector deleted a live job's media"; s06-scrub: "the request record went 600799 s before its persisted retention" |

**Projected gate on the union with these fixes:** the early run's other scenarios (s01–s03, s05, s07–s10, s12, s13) were PASS on the same product code. The one remaining red is s04 late rejection (F-5), so the gate stays FAIL on F-5 alone. The two harness regressions add 2 s12 cases, which pass. The final run on the final SHA confirms this.

## Findings against merged union code (not fixed here)

- **F-3: closed on the union.** The relay admits through `ReadinessStore.admit_ready` (`relay.py:185-190`); s04 text/video and s05 readiness are green.
- **F-4: closed on the union.** The worker's journal prune (`worker/__main__.py` `housekeeping`); s06 scrub and the s11 race are green.
- **F-5 (new): after `admit_ready`, the relay still runs the legacy post-admission capability recheck. When it refuses, the client gets a 4xx for a job that already ran and was charged.**
  - Where: `apps/infrx-api/infrx/gateway/routes/relay.py:216` calls `_admitted` (`:268-300`) after `admit_ready` has already made the job ready and claimable. `_admitted` re-checks the pinned card and capability. A refusal calls `cancel`; if the job already finished, `cancel` returns the committed outcome and the refusal is raised to the client anyway.
  - Measured (validation run, request `f61b82d3`): the worker prepared it at 22:17:58 (inside the hold, legitimately ready). The gateway answered `unsupported_media` at 22:18:06. The job ended `succeeded`, and a succeeded CREDIT job settles its debit.
  - The customer is told "refused" and is billed. R110 (one-phase acceptance) checks capability inside the admission transaction, so the recheck is redundant on the `admit_ready` path. Suggested fix: skip it there, or answer the committed outcome when `cancel` finds the job already finished.
  - Reproduce: `INFRX_E3C_RUNTIME_LOGIN=1 runner.py --only s04 -k late`. The barrier holds before `Relay._admitted`, the test withdraws text input from the pinned serving revision, then releases.
  - Owner: W5-ADMIT-WIRING (its fix round).
- **F-6 (new, no scenario fails on it): the worker's reconciliation gauges cannot run on the dedicated login.**
  - Where: `apps/infrx-api/infrx/worker/__main__.py` composes `WorkerService(reconciliation=PgReconciliation(connect))` on the worker pool. `RECONCILIATION_SQL` (`worker/service.py:70-76`) reads `infrx.wallet_reconciliation` and `infrx.credit_wallet_reconciliation`, which 0021 grants to `infrx_monitor` only (`0021_read_authority.sql:550`).
  - Effect: on `infrx_runtime` every reap tick logs `reconciliation read failed: InsufficientPrivilege` (39 of 46 worker logs in the early run), and the S3 F4 reconciliation gauges are never published.
  - Reproduce: any `INFRX_E3C_RUNTIME_LOGIN=1` case's worker log.
  - Owner: W5 wiring 1 / D10. Options: read through the monitor login, grant the two views to the runtime role, or leave the gauges to I8's durable monitor (which already reads them as `infrx_monitor`; W5's evidence noted the double producer).

## Unresolved

1. F-5 must be fixed in W5-ADMIT-WIRING's fix round. Then the final run on the final SHA: `$SC/final-run.sh <SHA>`. Its control trees are built from the merged HEAD, which now carries H-1 to H-6.
2. F-6 needs an owner and a decision (not a gate case).
3. The pre-existing ruff F401 at `scenarios_crash.py:23` is unchanged.

## Remaining effort

| Optimistic | Likely | Pessimistic | Confidence | Basis |
|---|---|---|---|---|
| 0.5 h | 1 h | 2 h | medium | One final run: 13 min on a quiet host, 25 min at load 50, plus the evidence write-up. Pessimistic if the W5-ADMIT-WIRING delta moves the relay's admission steps (fault points) or the control revert list. |

## Verification log

- 2026-09-25 (E3C early run on union 9d61d1e1): merged as 43178b58. Early full run gate FAIL, 5 failed / 76 passed (25:35 at load 44–64). Six harness defects found and fixed at 9107a5c6: control trees, control INVALID on no-start, control case dirs, s04 permanent premise per W5, retention-aware scrub, s11 live premise. Validation of s04/s06/s11 with both controls: s06 and s11 green, s04 3/4, both revert controls detected for the right reason. F-3 and F-4 closed on the union; F-5 and F-6 new. No hosted DB, box, AWS or paid operation; nothing pushed.
