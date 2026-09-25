# Consumer v1 progress tracker

Generated 2026-09-25 00:05Z UTC by `python3 research/plan/scripts/progress.py` from [tasks.json](../../tasks.json) (manifest v4) and [progress-state.json](progress-state.json) (overlay revision 18, updated 2026-09-25 00:05Z UTC). Generated file; never hand-edit. Program: [consumer-v1 (program 22)](../../22-consumer-v1-implementation.md). Full view: [progress.html](progress.html).

## Overview

- Integration branch `claude/consumer-v1` (head `bba27b02`), base `dff31efc`, main `dff31efc`.
- Deployed candidate `bda15866e5700f3856d7142580da842fba9bbd23` (third install; image infrx-runtime:bda1586 = sha256:cc2a80c9396f6ebec8cd151770a0b8f221a306a56364f2562f90afd82a1cbebb (S3 identity table); MAX_VIDEO_SECONDS=82, ENGINE_MAX_NUM_SEQS=8, WORKER_CONCURRENCY=8, LARGE_BODY_LIMIT=8; regime **legacy_usd**).
- Lowest open band: V0 current evidence; bands with active work: V0, V1, V2, V3, V4.
- Agent slots: 16 total, 11 active lanes, 2 reserved.
- Validation: 0 error(s), 10 warning(s).

### Actionable blockers

- P-01 open — Approved CREDIT rate card (unit, rounding, failed-execution disclosure) (owner operator; G8/D10/I8 inventory; blocks E4C)
- P-02 open — Re-opened by S3: inventory the pilot's USD 5.00 test grant and legacy_usd usage (W12, E1B, E4B) read-only before CREDIT activation; no conversion (owner operator; G8 inventory (with D10); blocks E4C)
- P-05 open — Verified signup email/callback/recovery and abuse bounds on the target; also a second verified hosted test tenant (E1B --tenant-keys, E4C two-tenant/fairness cells) per S3 (owner operator/coordinator; A2/I2A; blocks E1B, E4C, I2A, E4)
- P-06 open — Served-bytes digests of processor_config.json and preprocessor_config.json in the pinned serving record before E4C freezes the candidate (S3 finding 10) (owner I8 slice 5 + wiring request for serving-version.json; blocks E4C)
- P-17 open — Final operator decision accepting the backend candidate (then App before Lab) (owner coordinator/user; E4C; blocks E4C)
- P-18 open — Predeclared workload/SLO/error/recovery limits for E4C (owner workload owner; S3/E4C; blocks E4C)
- P-19 open — Sourced infrastructure price row or actual bill for cost-per-unit figures (owner pricing owner/user; E1C; blocks E4C)
- P-22 open — Canonical alias/legacy price identity decision (resolve before pricing vs per-string rows) (owner F2C-C/D10/G7 (D/G decision); blocks E4C)
- P-24 open — Versioned test profile with numeric request/byte/spend caps (target/window/stop rules exist, S3 §4.2); read-only inventory of the two pre-cutover consumer keys before the next E4B_WINDOW_OK=1 run (owner E1C/E4C; coordinator op (key inventory); blocks E4C)
- P-25 open — Operations/retention ownership: TTLs, alert destination (missing; I8 slice 4 BLOCKED), hosted backup/PITR ⚠️ unverified, and the box's newest install backup (27af05a) is not a known-good rollback bundle (owner I8/D10/M6; blocks E4C)
- GPU box (pilot, single L40S) held by E4B run3 (historical run on bda1586) until ≈2026-09-25 01:20Z
- SQL writer (migrations) held by D10
- runner dir tests/integration/backend held by E2C
- task-local services D10 held by D10
- task-local services M5 held by M5
- task-local services M6 held by M6
- task-local services W5 held by W5
- task-local services G7 held by G7
- task-local services G8 held by G8
- task-local services E2C held by E2C
- task-local services E1C held by E1C
- task-local services I8 held by I8
- task-local services E3C held by E3C
- E3C (BACKEND-LOCAL): no remaining-effort estimate for D10, E2C, G7, M5, W5
- E4C (BACKEND-READY): blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4C; no remaining-effort estimate for D10, E1B, E2C, E4C, G7, M5, W5
- E3A (APP-LOCAL): blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4C; no remaining-effort estimate for A2, A3, C0, C3A, D10, E1B, E2C, E3A, E4C, G7, M5, U1R, U2, U3, U4, W5
- E4 (APP-PILOT): blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4, E4C, I2A; no remaining-effort estimate for A2, A3, C0, C3A, D10, E1B, E2C, E3A, E4, E4C, G7, I2A, I3, M5, U1R, U2, U3, U4, W5

### Next ready work

- `E1B` Measure the end-to-end Marlin baseline and operating envelope (no lane assigned)

## Progress summaries

Task counts: manifest implemented/integrated over an explicit denominator. Cells: gate cells marked PASS. Neither implies launch readiness.

| Category | Implemented/integrated | Active | Acceptance cells PASS |
|---|---|---|---|
| Backend corrections | 2 / 14 | D10, E1C, E2C, E3C, F2C, G7, G8, I8, M5, M6, W5 | BACKEND-LOCAL 0/7; BACKEND-READY 0/6 |
| App completion | 0 / 12 | none | APP-LOCAL 0/17; APP-PILOT 0/5 |
| Deferred Lab / hosting / later | 0 / 57 | none | n/a |
| Reused baseline | 44 / 44 | none | n/a |
| Superseded | 0 / 6 | none | n/a |

## Milestones and ETA

Never a date while an open input or an unallocated GPU window sits on the remaining path.

| Milestone | Gate | Status | Forecast | Controlling constraint | Effort o/l/p | Wall-clock o/l/p | Confidence |
|---|---|---|---|---|---|---|---|
| `E3C` | BACKEND-LOCAL (PENDING) | unknown | unknown: no remaining-effort estimate for D10, E2C, G7, M5, W5 | no remaining-effort estimate for D10, E2C, G7, M5, W5 | — | — | unknown |
| `E4C` | BACKEND-READY (PENDING) | blocked | blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 | blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4C; no remaining-effort estimate for D10, E1B, E2C, E4C, G7, M5, W5 | — | — | unknown |
| `E3A` | APP-LOCAL (PENDING) | blocked | blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 | blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4C; no remaining-effort estimate for A2, A3, C0, C3A, D10, E1B, E2C, E3A, E4C, G7, M5, U1R, U2, U3, U4, W5 | — | — | unknown |
| `E4` | APP-PILOT (PENDING) | blocked | blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 | blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4, E4C, I2A; no remaining-effort estimate for A2, A3, C0, C3A, D10, E1B, E2C, E3A, E4, E4C, G7, I2A, I3, M5, U1R, U2, U3, U4, W5 | — | — | unknown |

## Gates

| Gate | Roots | Cells PASS | Not PASS | Decision | Note |
|---|---|---|---|---|---|
| BACKEND-LOCAL **PENDING** | E3C (planned) | 0 / 7 | BACKEND-JOURNEY NOT RUN, UPLOAD-RESTART NOT RUN, RETENTION-DURABLE NOT RUN, ADMISSION-READY NOT RUN, RESULT-EXPIRY NOT RUN, CREDIT-CUTOVER NOT RUN, VERIFY-REPRO NOT RUN | none | Not declared. E3B's harness is green at 95fbb90 (historical, predecessor root); E3C cells not run. |
| BACKEND-READY **PENDING** | E4C (planned) | 0 / 6 | BACKEND-JOURNEY NOT RUN, LOAD-CLOSEDLOOP NOT RUN, PERF-ENVELOPE NOT RUN, OPS-CONTINUOUS NOT RUN, CREDIT-CUTOVER NOT RUN, MARLIN-SOP NOT RUN | none | Pending. Open list: RV-01…RV-12, all OPEN at dff31efc (S3); see findings. |
| APP-LOCAL **PENDING** | E3A (planned) | 0 / 17 | DUR-ADMIT NOT RUN, DUR-CAP NOT RUN, DUR-FENCE NOT RUN, DUR-OUTPUT NOT RUN, DUR-SETTLE NOT RUN, DUR-OUTBOX NOT RUN, DUR-RLS NOT RUN, MEDIA-SEC NOT RUN, API-MODES NOT RUN, API-STREAM NOT RUN, CONSOLE-FLOWS NOT RUN, CREDIT-GRANT NOT RUN, CREDIT-IDENTITY NOT RUN, CREDIT-UNITS NOT RUN, CREDIT-RATE NOT RUN, CREDIT-SPEND NOT RUN, APP-JOURNEY NOT RUN | none | Pending; no decision recorded. |
| APP-PILOT **PENDING** | E4 (planned) | 0 / 5 | PERF-PILOT NOT RUN, OPS-RECOVER NOT RUN, MEDIA-PARITY NOT RUN, APP-JOURNEY NOT RUN, CREDIT-SPEND NOT RUN | none | Pending; no decision recorded. |

### Readiness findings (RV)

| Finding | Status | Corrective tasks | As of | Source |
|---|---|---|---|---|
| RV-01 | open | F2C (review), G7 (running), A3 (unassigned) | dff31efc | [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md) |
| RV-02 | open | D10 (running), M5 (running), E1C (integration), E3C (review) | dff31efc | [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md) |
| RV-03 | open | D10 (running), M6 (review), I8 (review), E3C (review) | dff31efc | [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md) |
| RV-04 | open | S3 (complete), E4C (queued) | dff31efc | [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md) |
| RV-05 | open | F2C (review), D10 (running), W5 (running), G7 (running), E3C (review) | dff31efc | [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md) |
| RV-06 | open | C0 (unassigned), C3A (unassigned), A2 (unassigned), A3 (unassigned), U1R (unassigned), U2 (unassigned), U3 (unassigned), U4 (unassigned), E3A (unassigned) | dff31efc | [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md) |
| RV-07 | open | E1C (integration), M5 (running), G7 (running) | dff31efc | [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md) |
| RV-08 | open | E1C (integration), E4C (queued) | dff31efc | [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md) |
| RV-09 | open | D10 (running), I8 (review), E4C (queued) | dff31efc | [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md) |
| RV-10 | open | I8 (review), E4C (queued) | dff31efc | [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md) |
| RV-11 | open | F2C (review), D10 (running), G7 (running), U4 (unassigned), E3C (review) | dff31efc | [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md) |
| RV-12 | open | E2C (running), E3C (review) | dff31efc | [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md) |

### Historical run E4B-run3 (running)

`E4B` on `bda15866e5700f3856d7142580da842fba9bbd23`. Historical E4B certification of bda1586 in legacy_usd; not E4C acceptance and not relabelled as such. [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md)

| Cell | Verdict | Note | Elapsed / remaining |
|---|---|---|---|
| preconditions | PASS | — | — |
| config-pin | PASS | — | — |
| served-build | PASS | — | — |
| sop-parity | PASS | 2 clips; over-cap refused 400 unsupported_media | — |
| recovery-box | PENDING | coordinator drills | — |
| protocol | NOT RUN | --no-stack | — |
| recovery | NOT RUN | --no-stack | — |
| dataset-resume | FAIL | S3: regime mismatch (legacy_usd vs the CREDIT ledger oracle), not a runtime defect; the client half passed (R106 holds live) | — |
| envelope | FAIL | supported 0.5/s; the 1.0 rung missed a provisional target; cause read from the run3 report (S3) | — |
| soak | RUNNING | bounded 14,400+900 s at 0.25/s; start ≈20:46Z from 609 rows at 21:26Z (S3); ends ≈01:01–01:20Z box clock. Cannot PASS at bda1586: reconciled_at_end is always UNKNOWN because record_reconciliation has no runtime caller (S3 finding 4) | elapsed 3.3 h since ≈2026-09-24 20:46Z; expected end 2026-09-25 01:01Z–2026-09-25 01:20Z (≤ 1.2 h remaining at generation) |
| overload | PENDING | runs after the soak; first live exercise of the intake drain (32-burst to 127.0.0.1:8001, bypassing Caddy) | — |

### Historical run E1B-acceptance-bda1586 (complete)

`E1B` on `bda15866e5700f3856d7142580da842fba9bbd23`. One tenant, mixed video_b64+text, 120 attempts per cell at LARGE_BODY_LIMIT=8; zero ReadError. Zero 429s, so the intake drain was not exercised (S3 finding 5). [models/marlin2b/results/E1B-box-bda1586/bench.jsonl](../../../../models/marlin2b/results/E1B-box-bda1586/bench.jsonl)

| Cell | Verdict | Note | Elapsed / remaining |
|---|---|---|---|
| L2 0.5/s | PASS | — | — |
| L2 1.0/s | PASS | — | — |
| L3 burst 8 | PASS | — | — |

## Lanes

| Lane | Task / slice | Activity | Branch | Base → head | Isolation | Updated | Blocker / next | Estimate |
|---|---|---|---|---|---|---|---|---|
| S3 | S3 all | complete | codex/s3-reconcile | dff31efc → 289eef6e | none | 2026-09-24 21:58Z | — | 0–0 h remaining (likely 0 h), confidence high, estimated 2026-09-24 21:58Z; basis: merged (fff8416e; edits fdbbf87c) |
| TRACKER | support 06-progress-tracker (support) | complete | codex/tracker | dff31efc → 803f6cb1 | none | 2026-09-24 22:31Z | merged into claude/consumer-v1 at 2cae9a4c; the coordinator runs apply-updates at each handback | 0–0.5 h remaining (likely 0 h), confidence high, estimated 2026-09-24 22:31Z; basis: merged after the verify-lane fix round (ACCEPT_WITH_FIXES at 803f6cb1) |
| E2C | E2C all | running | codex/e2c-verify | dff31efc → — | ports postgres 55448, valkey 55474, s3 55475 (contracts/tasklocal.py, bfb3a8af), prefix infrx-e2c- | 2026-09-24 21:40Z | Linux environment manifest + preflight; RV-12 harness fixes; dependency triage; wrappers (Makefile lines as wiring requests). | unknown (not estimated at baseline (lane has not inspected its slice yet)) |
| F2C-L | F2C d | review | codex/f2c-lifecycle | dff31efc → 76d966cc | none | 2026-09-24 23:00Z | slice b: persisted result_expires_at through terminal/status DTOs, scrub shape, 0014 guard; slice d: fixtures, consumer matrix, rollout contract, fixture hash, README table | 0.5–3 h remaining (likely 1 h), confidence medium, estimated 2026-09-24 23:00Z; basis: a, b, d delivered and green; only review fixes remain |
| F2C-C | F2C c | complete | codex/f2c-catalog | dff31efc → b8de6171 | none | 2026-09-24 22:36Z | merged into claude/consumer-v1 at 2bd7f347 with the export wiring (179a1a0b); R109 numbered; P-22 decided | 0–0 h remaining (likely 0 h), confidence high, estimated 2026-09-24 22:36Z; basis: merged after the verify-lane fix round (ACCEPT_WITH_FIXES at b8de6171) |
| E1C | E1C E1C fix round (0-B1, 0-B2, 2-E1C-ACC-01..04, 0-M1..0-M4) | integration | codex/e1c-client | dff31efc → 6cb6c929 | ports postgres 55449 (contracts/tasklocal.py, bfb3a8af), prefix infrx-e1c- | 2026-09-24 23:05Z | UPLOAD-RESTART needs M5 durable uploads; hosted smoke is the coordinator's (amended command, --unprofiled smoke, in the evidence) | 0–2 h remaining (likely 0.5 h), confidence high, estimated 2026-09-24 23:05Z; basis: merged after the verify-lane fix round (ACCEPT_WITH_FIXES at 6cb6c929); residual = integration checks on the merged tree |
| I8 | I8 1-7 (code/config half) | review | codex/i8-operate | dff31efc → 103d20a8 | ports postgres 55450, valkey 55476 (contracts/tasklocal.py, bfb3a8af), prefix infrx-i8- | 2026-09-24 22:58Z | P-25: alert destination/owner/escalation (slice 4 delivery proof BLOCKED); P-25: approved MIRROR_URL prefix for the model mirror (O9-O12); Supabase personal access token for supabase_policy.py (O8); D10: least-privilege runtime login + function list, read-only monitor login (O7, WR-I8-6); every live op after certification run3 (coordinator) | 4–16 h remaining (likely 8 h), confidence medium, estimated 2026-09-24 22:58Z; basis: code half done and tested; left: coordinator ops O1-O14 (two ~1 h maintenance windows), I8 follow-ups after WR-I8-1..5 land (~1-2 h), inputs P-25/D10 outside the lane |
| D10 | D10 all (SQL writer) | running | codex/d10-durable | dff31efc → — | ports postgres 55442, valkey 55469 (contracts/tasklocal.py, bfb3a8af), prefix infrx-d10- | 2026-09-24 21:58Z | Wire 0010 media_uploads and jobs.result_expires_at (S3 F9); role-bound login or SET LOCAL (F6); P-02 USD inventory with G8. | unknown (not estimated at baseline (lane has not inspected its slice yet)) |
| M5 | M5 all | running | codex/m5-uploads | dff31efc → — | ports postgres 55443, s3 55470 (contracts/tasklocal.py, bfb3a8af), prefix infrx-m5- | 2026-09-24 21:58Z | Durable upload lifecycle across gateway replacement; wire media_uploads (S3 F9). | unknown (not estimated at baseline (lane has not inspected its slice yet)) |
| W5 | W5 all | running | codex/w5-readiness | dff31efc → — | ports postgres 55445, valkey 55472 (contracts/tasklocal.py, bfb3a8af), prefix infrx-w5- | 2026-09-24 21:58Z | Execution readiness; worker producer for the reconciliation gauges (S3 F4). | unknown (not estimated at baseline (lane has not inspected its slice yet)) |
| G7 | G7 all | running | codex/g7-catalog | dff31efc → — | ports postgres 55446 (contracts/tasklocal.py, bfb3a8af), prefix infrx-g7- | 2026-09-24 21:58Z | Public capability discovery, alias pricing, persisted result expiry; route-level proof for F5; provisional card ids mapped (F11). | unknown (not estimated at baseline (lane has not inspected its slice yet)) |
| G8 | G8 points 1-4 (trusted account ops; idempotent bounded transition; approved-card gate + dry-run; races/retries) + acceptance | review | codex/g8-credit-ops | dff31efc → 6a075c56 | ports postgres 55447, valkey 55473 (contracts/tasklocal.py, bfb3a8af), prefix infrx-g8- | 2026-09-24 23:14Z | P-01 approved launch rates (live activation only); coordinator box dry-run window (read-only) | 2–8 h remaining (likely 4 h), confidence medium, estimated 2026-09-24 23:14Z; basis: code + local real-PG/composed-app proofs done; remaining = merged-SHA rerun with D10 0019/0020 (ready marker in pgworld.settle), wiring request 1, review fixes, box dry-run support, post-P-01 activation window |
| M6 | M6 phase-1 (points 1, 2, 4; point 3 designed) | review | codex/m6-retention | f764e396 → 73760244 | ports 55444 postgres, 55471 s3, prefix infrx-m6-, db infrx_m6 | 2026-09-24 23:25Z | phase 2 waits for M5 to merge (store/uploads/prepare ownership); integration waits for D10's 0020 (PgLifecycle content functions); P-25 approved retention/grace/claim TTL/interval unresolved (parameterized) | 10–26 h remaining (likely 16 h), confidence medium, estimated 2026-09-24 23:25Z; basis: phase 2 (point 3 + writers on generation_key + gc.py reduction) 8/12/20 h over M5's rewritten files; d10 world once 0020 commits 2/3/5 h; review 0/1/1 h |
| E3C | E3C phase 1: red harness (runner + verdict, scenario matrix s01-s13, failure sensitivity) | review | codex/e3c-integration | f764e396 → 8406c798 | ports compose block 56900-56999 (postgres 56932), prefix infrx-e3c-, db infrx_e3c | 2026-09-25 00:03Z | WR-2: the pinned MinIO digest is unpullable (quay 401); runs use --s3-image as a recorded deviation; phase 2 needs D10, M5, M6, W5, G7, G8, I8, E1C, F2C merged | 3–12 h remaining (likely 6 h), confidence medium, estimated 2026-09-25 00:03Z; basis: phase 2 = rerun per merged SHA (~15 min) + re-pointing the named seams (POINTS candidates, collector entry, G8/G7 hooks, D10 lifecycle factory for F2C-L transcript replay) + the two revert-type controls |
| E4C | E4C  | queued | — | — → — | none | 2026-09-24 21:40Z | after E3C; needs an allocated GPU window | unknown (not estimated at baseline (lane has not inspected its slice yet)) |

### Queues and locks

- Review queue: I8, F2C-L, G8, M6, E3C.
- Integration queue: E1C.
- GPU box (pilot, single L40S): E4B run3 (historical run on bda1586) until ≈2026-09-25 01:20Z. One window at a time; until = latest soak end (box clock); the overload cell follows the soak. I8 live steps serialize after run3. No window allocated for E1B or E4C.
- SQL writer (migrations): D10. D10 alone writes migrations (0001–0018 immutable).
- runner dir tests/integration/backend: E2C. One owner per runner directory; E2C → E3C → E4C.
- integration queue (claude/consumer-v1): unassigned. Serial coordinator merges; empty.
- task-local services D10: D10. postgres 55442, valkey 55469 reserved in apps/infrx-api/infrx/contracts/tasklocal.py (bfb3a8af)
- task-local services M5: M5. postgres 55443, s3 55470 reserved in apps/infrx-api/infrx/contracts/tasklocal.py (bfb3a8af)
- task-local services M6: M6. postgres 55444, s3 55471 reserved in apps/infrx-api/infrx/contracts/tasklocal.py (bfb3a8af)
- task-local services W5: W5. postgres 55445, valkey 55472 reserved in apps/infrx-api/infrx/contracts/tasklocal.py (bfb3a8af)
- task-local services G7: G7. postgres 55446 reserved in apps/infrx-api/infrx/contracts/tasklocal.py (bfb3a8af)
- task-local services G8: G8. postgres 55447, valkey 55473 reserved in apps/infrx-api/infrx/contracts/tasklocal.py (bfb3a8af)
- task-local services E2C: E2C. postgres 55448, valkey 55474, s3 55475 (dispatch range 55510–55519 replaced: 555xx is the E compose block) reserved in apps/infrx-api/infrx/contracts/tasklocal.py (bfb3a8af)
- task-local services E1C: E1C. postgres 55449 reserved in apps/infrx-api/infrx/contracts/tasklocal.py (bfb3a8af)
- task-local services I8: I8. postgres 55450, valkey 55476 reserved in apps/infrx-api/infrx/contracts/tasklocal.py (bfb3a8af)
- task-local services E3C: E3C. compose block 56900–56999 (postgres mirror 56932) reserved in apps/infrx-api/infrx/contracts/tasklocal.py (bfb3a8af)

## Validation

- warning: overlapping writers: E2C (running) and F2C-L (review) both own apps/infrx-api/tests/ / apps/infrx-api/tests/contracts/
- warning: overlapping writers: E2C (running) and I8 (review) both own apps/infrx-api/tests/ / apps/infrx-api/tests/i/
- warning: overlapping writers: E2C (running) and D10 (running) both own apps/infrx-api/tests/ / apps/infrx-api/tests/d/
- warning: overlapping writers: E2C (running) and M5 (running) both own apps/infrx-api/tests/ / apps/infrx-api/tests/m/
- warning: overlapping writers: E2C (running) and W5 (running) both own apps/infrx-api/tests/ / apps/infrx-api/tests/w/
- warning: overlapping writers: E2C (running) and G7 (running) both own apps/infrx-api/tests/ / apps/infrx-api/tests/g/
- warning: overlapping writers: E2C (running) and G8 (review) both own apps/infrx-api/tests/ / apps/infrx-api/tests/g/ops/
- warning: overlapping writers: E2C (running) and M6 (review) both own apps/infrx-api/tests/ / apps/infrx-api/tests/m/test_retention.py (+2 more)
- warning: overlapping writers: E1C (integration) and E4C (queued) both own research/plan/evidence/e/E1C-2531dc4.md / research/plan/evidence/e/
- warning: overlapping writers: G7 (running) and G8 (review) both own apps/infrx-api/tests/g/ / apps/infrx-api/tests/g/ops/

## Pending inputs

| Input | Status | What | Owner | Blocks |
|---|---|---|---|---|
| P-01 | open | Approved CREDIT rate card (unit, rounding, failed-execution disclosure) | operator; G8/D10/I8 inventory | E4C |
| P-02 | open | Re-opened by S3: inventory the pilot's USD 5.00 test grant and legacy_usd usage (W12, E1B, E4B) read-only before CREDIT activation; no conversion | operator; G8 inventory (with D10) | E4C |
| P-05 | open | Verified signup email/callback/recovery and abuse bounds on the target; also a second verified hosted test tenant (E1B --tenant-keys, E4C two-tenant/fairness cells) per S3 | operator/coordinator; A2/I2A | E1B, E4C, I2A, E4 |
| P-06 | open | Served-bytes digests of processor_config.json and preprocessor_config.json in the pinned serving record before E4C freezes the candidate (S3 finding 10) | I8 slice 5 + wiring request for serving-version.json | E4C |
| P-17 | open | Final operator decision accepting the backend candidate (then App before Lab) | coordinator/user; E4C | E4C |
| P-18 | open | Predeclared workload/SLO/error/recovery limits for E4C | workload owner; S3/E4C | E4C |
| P-19 | open | Sourced infrastructure price row or actual bill for cost-per-unit figures | pricing owner/user; E1C | E4C |
| P-22 | open | Canonical alias/legacy price identity decision (resolve before pricing vs per-string rows) | F2C-C/D10/G7 (D/G decision) | E4C |
| P-24 | open | Versioned test profile with numeric request/byte/spend caps (target/window/stop rules exist, S3 §4.2); read-only inventory of the two pre-cutover consumer keys before the next E4B_WINDOW_OK=1 run | E1C/E4C; coordinator op (key inventory) | E4C |
| P-25 | open | Operations/retention ownership: TTLs, alert destination (missing; I8 slice 4 BLOCKED), hosted backup/PITR ⚠️ unverified, and the box's newest install backup (27af05a) is not a known-good rollback bundle | I8/D10/M6 | E4C |

## Rejected updates

- `S3-20260924T2150Z.json`: stale: at 2026-09-24T21:50:00Z is not newer than lane S3 state 2026-09-24T21:58:00Z

## All manifest tasks

| ID | Title | Category | Manifest | Activity | State |
|---|---|---|---|---|---|
| `F1` | Extract current gateway behind an application factory | Reused baseline | integrated | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `F2` | Freeze typed contracts, pins and executable fixtures | Reused baseline | integrated | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `D1` | Migrate durable schema, wallet summaries and permissions | Reused baseline | integrated | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `D2` | Atomic admission, durable preparation and dispatch outbox | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `D3` | Fenced leases, recovery and cancellation | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `D4` | Persistent stream journal and replay | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `D5` | Terminal transaction, grants and reconciliation | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `D6` | Durable feedback and judge coordination | Superseded | superseded-for-scheduling | unassigned | superseded: never scheduled; replaced by D6F, D6J |
| `M1` | Bound and secure URL/base64 materialization | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `M2` | Versioned preprocessing and tenant cache | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `M3` | Owned uploads, expiry and orphan collection | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `Q1` | Deterministic memory scheduler and fairness model | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `Q2` | Valkey adapter with atomic tested scripts | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `Q3` | Outbox/reconciler integration and index loss recovery | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `W1` | Engine adapter and deterministic execution fakes | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `W2` | Lease-aware execution, cancellation and completion | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `W3` | Drain, engine pin and measured concurrency | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `G1` | Ingress, auth and capability validation | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `G2` | Synchronous chat and persistent SSE relay | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `G3` | Explicit jobs, status, cancellation and replay | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `G4` | Uploads, feedback and trace export adapters | Superseded | superseded-for-scheduling | unassigned | superseded: never scheduled; replaced by G4U, G4F, G4T |
| `G5` | Signed async completion callbacks | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `T1` | Byte-budgeted capture and persistent spool | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `T2` | Idempotent analytics and content shipping | Superseded | superseded-for-scheduling | unassigned | superseded: never scheduled; replaced by T2I, T2F |
| `T3` | Logical retention, deletion and observability | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `J1` | Dry-run sampler, rubric and score validation | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `J2` | Consent/budget coordinated submission and collection | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `J3` | Operator calibration and quality report | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `C1` | Typed repositories, pagination and tenant query boundary | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `C2` | Lab content access, expiry and safe signed references | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `C3` | Shared key/settings/grant/feedback/judge actions | Superseded | superseded-for-scheduling | unassigned | superseded: never scheduled; replaced by C3A, C3F, C3L |
| `U1` | Usage and promotional balance views | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `U2` | Keys and privacy/settings controls | App completion | planned | unassigned | blocked: gated: dispatch only after BACKEND-READY is accepted |
| `U3` | Operator grants, suspension and pilot operations | App completion | planned | unassigned | blocked: gated: dispatch only after BACKEND-READY is accepted |
| `V1` | Paginated trace list and filters | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `V2` | Trace detail, content and feedback | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `V3` | Judge score and calibration presentation | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `I1` | Read-only inventory and deploy design | Reused baseline | integrated | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `I2` | Reproducible single-GPU deployment | Superseded | superseded-for-scheduling | unassigned | superseded: never scheduled; replaced by I2A, I2L |
| `I3` | Recovery, alarms and rollback runbooks | App completion | planned | unassigned | blocked: gated: dispatch only after BACKEND-READY is accepted |
| `I4` | Separately gated fleet deployment | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `E1` | Distinct corpus and authenticated benchmark client | Reused baseline | integrated | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `E2` | Pinned integration services and fault harness | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `E3` | Cross-module failures and security gate | Superseded | superseded-for-scheduling | unassigned | superseded: never scheduled; replaced by E3A, E3L, E5L |
| `E4` | Single-GPU release evidence and launch decision | App completion | planned | unassigned | blocked: gated: dispatch only after BACKEND-READY is accepted |
| `S1` | Reconcile pulled wave-2 baseline and publish product revision audit | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `A1` | Verified individual signup entitlement and idempotent backfill | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `A2` | Consumer signup verification and credited onboarding | App completion | planned | unassigned | blocked: gated: dispatch only after BACKEND-READY is accepted |
| `A3` | Published catalog, credit rates and capability-matched examples | App completion | planned | unassigned | blocked: gated: dispatch only after BACKEND-READY is accepted |
| `D6F` | Durable feedback and immutable author provenance | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `D6J` | Lab consent, USD budget and external submission coordination | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `G4U` | Owned upload HTTP adapter | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `G4F` | Owned feedback HTTP adapter | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `G4T` | Owned trace export HTTP adapter | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `T2I` | Inference analytics and content projection | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `T2F` | Feedback analytics projection | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `C3A` | Consumer key/privacy and platform operator actions | App completion | planned | unassigned | blocked: gated: dispatch only after BACKEND-READY is accepted |
| `C3F` | Authorized feedback and review actions | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `C3L` | Lab judge and calibration control actions | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `L1` | Provider app shell and separate build/auth boundary | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `L2` | Provider role and purpose-specific data-access services | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `L3` | Assisted model registration and dev/prod revision services | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `L4` | Model/deployment/publication UI and aggregate health | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `I2A` | Reproducible App and single-GPU runtime deployment | App completion | planned | unassigned | blocked: gated: dispatch only after BACKEND-READY is accepted |
| `I2L` | Independent Lab app and control-service deployment | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `E3A` | Consumer failure, security and signup-to-spend integration gate | App completion | planned | unassigned | blocked: gated: dispatch only after BACKEND-READY is accepted |
| `E3L` | Provider access, publication and rollback integration gate | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `E5L` | Provider traces, review and evaluation integration gate | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `F2R` | Close remaining wave-2 contract and verification carryovers | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `F2P` | Encode product-v2 CREDIT, identity, serving and permission contracts | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `D1R` | Add product-v2 schema without rewriting USD pilot migrations | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `C0` | Wire the consumer database query port and real account context | App completion | planned | unassigned | blocked: gated: dispatch only after BACKEND-READY is accepted |
| `I0` | Repair installer atomicity and fail-closed startup prerequisite | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `E2R` | Repair service harness ownership, role matrix and shared test clock | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `G1R` | Revise ingress for consumer and provider endpoint audiences | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `V1M` | Move the implemented trace explorer into the authorized Lab shell | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `U1R` | Adapt consumer usage and balance views to CREDIT and explicit legacy USD | App completion | planned | unassigned | blocked: gated: dispatch only after BACKEND-READY is accepted |
| `F3` | Freeze dataset, evaluation, training and rollout contracts | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `D7` | Persist datasets, harnesses and evaluation coordination | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `N1` | Import benchmark data and existing annotation outputs | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `N2` | Version, split and export reproducible datasets | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `N3` | Derive datasets from permitted traces and propagate revocation | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `N4` | Build dataset import, version and split workflows in Lab | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `H1` | Version prompts and bounded replay harnesses | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `B1` | Execute durable offline evaluation runs | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `B2` | Compare quality, costs and latency with honest uncertainty | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `B3` | Benchmark externally produced checkpoints continuously | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `B4` | Build evaluations and experiment comparisons in Lab | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `D8` | Persist annotations and external training lifecycle | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `P1` | Import, review and export annotation records | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `P2` | Run bounded teacher annotation batches | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `P3` | Integrate external training and import candidates | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `P4` | Build annotation and training workflows in Lab | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `D9` | Persist release policies and stable experiment assignment | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `R1` | Route bounded shadow, canary and A/B experiments | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `R2` | Evaluate guardrails and roll back controlled releases | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `R3` | Register and compare optimized serving variants | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `R4` | Build release experiments and optimization comparison UI | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `I5` | Package dataset and evaluation workers for independent deployment | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `I6` | Package annotation and training integration workers | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `I7` | Package release controls and optimization evidence operations | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `E6L` | Prove imported benchmark to candidate decision end to end | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `E7L` | Prove the second authorized improvement iteration | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `E8L` | Prove controlled rollout and optimization evidence | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `X1` | Specify a bounded live-video workload and trial contract | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `X2` | Implement and verify the approved video session adapter | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `X3` | Specify a robot/task and inference freshness contract | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `X4` | Implement the bounded policy/ROS2 observation adapter | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `X5` | Qualify a non-NVIDIA backend investment | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `X6` | Implement and validate one qualified inference backend | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `S2M` | Freeze Marlin SOP inference launch profile | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `G6B` | Headless endpoint provisioning and operations | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `E3B` | Backend-only durability, security and protocol integration gate | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `I2B` | Reproducible Marlin endpoint deployment independent of frontends | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `I3B` | Backend recovery, observability, restore and rollback proof | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `E1B` | Measure the end-to-end Marlin baseline and operating envelope | Backend corrections | planned | unassigned | ready: start dependencies met |
| `M4` | Optimize bounded video retrieval, decoding and preparation | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `W4` | Tune Marlin GPU serving and scheduler admission from measured evidence | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `E4B` | Certify the robust and measured Marlin endpoint release candidate | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `S3` | Reconcile post-wave implementation, release evidence and RV findings | Backend corrections | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `F2C` | Freeze durable lifecycle, expiry and public capability corrections | Backend corrections | planned | review | active: lane active |
| `E2C` | Make corrective verification reproducible on supported Linux | Backend corrections | planned | running | active: lane active |
| `D10` | Persist uploads, execution eligibility, safe cleanup and result read authority | Backend corrections | planned | running | active: started before F2C (see lane deviation) |
| `M5` | Persist upload lifecycle across gateway replacement | Backend corrections | planned | running | active: started before F2C (see lane deviation) |
| `M6` | Implement restart-safe content cleanup and bounded caches | Backend corrections | planned | review | active: started before F2C, M5 (see lane deviation) |
| `W5` | Enforce execution readiness and bounded worker recovery | Backend corrections | planned | running | active: started before F2C (see lane deviation) |
| `G7` | Align public capability discovery, alias pricing and persisted result expiry | Backend corrections | planned | running | active: started before F2C (see lane deviation) |
| `G8` | Prove headless consumer CREDIT operations and safe activation | Backend corrections | planned | review | active: started before F2C (see lane deviation) |
| `E1C` | Repair upload client and deliver valid resumable dataset/load measurement | Backend corrections | implemented | integration | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `I8` | Operate continuously with bounded DB pools, durable artifacts and real rollback | Backend corrections | planned | review | active: started before F2C (see lane deviation) |
| `E3C` | Integrate corrective backend with real services and process faults | Backend corrections | planned | review | active: started before E2C, F2C (see lane deviation) |
| `E4C` | Certify repaired CREDIT backend on final Marlin deployment | Backend corrections | planned | queued | blocked: waiting on start dependencies: E3C |
| `U4` | Expose owned consumer request detail and result lifecycle | App completion | planned | unassigned | blocked: gated: dispatch only after BACKEND-READY is accepted |

## Activity log (newest first)

- 2026-09-25 00:05Z UTC, tracker: forecast E4: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4, E4C, I2A; no remaining-effort estimate for A2, A3, C0, C3A, D10, E1B, E2C, E3A, E4, E4C, G7, I2A, I3, M5, U1R, U2, U3, U4, W5)
- 2026-09-25 00:05Z UTC, tracker: forecast E3A: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4C; no remaining-effort estimate for A2, A3, C0, C3A, D10, E1B, E2C, E3A, E4C, G7, M5, U1R, U2, U3, U4, W5)
- 2026-09-25 00:05Z UTC, tracker: forecast E4C: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4C; no remaining-effort estimate for D10, E1B, E2C, E4C, G7, M5, W5)
- 2026-09-25 00:05Z UTC, tracker: forecast E3C: unknown: no remaining-effort estimate for D10, E2C, E3C, G7, M5, W5 → unknown: no remaining-effort estimate for D10, E2C, G7, M5, W5 (because: no remaining-effort estimate for D10, E2C, G7, M5, W5)
- 2026-09-25 00:03Z UTC, E3C: running → review; head 8406c798; estimate likely unknown → 6 h (phase 2 = rerun per merged SHA (~15 min) + re-pointing the named seams (POINTS candidates, collector entry, G8/G7 hooks, D10 lifecycle factory for F2C-L transcript replay) + the two revert-type controls)
- 2026-09-24 23:24Z UTC, tracker: forecast E4: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4, E4C, I2A; no remaining-effort estimate for A2, A3, C0, C3A, D10, E1B, E2C, E3A, E3C, E4, E4C, G7, I2A, I3, M5, U1R, U2, U3, U4, W5)
- 2026-09-24 23:24Z UTC, tracker: forecast E3A: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4C; no remaining-effort estimate for A2, A3, C0, C3A, D10, E1B, E2C, E3A, E3C, E4C, G7, M5, U1R, U2, U3, U4, W5)
- 2026-09-24 23:24Z UTC, tracker: forecast E4C: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4C; no remaining-effort estimate for D10, E1B, E2C, E3C, E4C, G7, M5, W5)
- 2026-09-24 23:24Z UTC, tracker: forecast E3C: unknown: no remaining-effort estimate for D10, E2C, E3C, G7, M5, M6, W5 → unknown: no remaining-effort estimate for D10, E2C, E3C, G7, M5, W5 (because: no remaining-effort estimate for D10, E2C, E3C, G7, M5, W5)
- 2026-09-24 23:25Z UTC, M6: running → review; head 73760244; estimate likely unknown → 16 h (phase 2 (point 3 + writers on generation_key + gc.py reduction) 8/12/20 h over M5's rewritten files; d10 world once 0020 commits 2/3/5 h; review 0/1/1 h)
- 2026-09-24 23:17Z UTC, tracker: forecast E4: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4, E4C, I2A; no remaining-effort estimate for A2, A3, C0, C3A, D10, E1B, E2C, E3A, E3C, E4, E4C, G7, I2A, I3, M5, M6, U1R, U2, U3, U4, W5)
- 2026-09-24 23:17Z UTC, tracker: forecast E3A: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4C; no remaining-effort estimate for A2, A3, C0, C3A, D10, E1B, E2C, E3A, E3C, E4C, G7, M5, M6, U1R, U2, U3, U4, W5)
- 2026-09-24 23:17Z UTC, tracker: forecast E4C: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4C; no remaining-effort estimate for D10, E1B, E2C, E3C, E4C, G7, M5, M6, W5)
- 2026-09-24 23:17Z UTC, tracker: forecast E3C: unknown: no remaining-effort estimate for D10, E2C, E3C, G7, G8, M5, M6, W5 → unknown: no remaining-effort estimate for D10, E2C, E3C, G7, M5, M6, W5 (because: no remaining-effort estimate for D10, E2C, E3C, G7, M5, M6, W5)
- 2026-09-24 23:14Z UTC, G8: running → review; head 6a075c56; estimate likely unknown → 4 h (code + local real-PG/composed-app proofs done; remaining = merged-SHA rerun with D10 0019/0020 (ready marker in pgworld.settle), wiring request 1, review fixes, box dry-run support, post-P-01 activation window)
- 2026-09-24 23:05Z UTC, E1C: review → integration; head 6cb6c929; estimate likely 4 → 0.5 h (merged after the verify-lane fix round (ACCEPT_WITH_FIXES at 6cb6c929); residual = integration checks on the merged tree)
- 2026-09-24 23:04Z UTC, tracker: rejected update: impossible transition: E1C cannot be complete before start dependencies F2C
- 2026-09-24 23:00Z UTC, E1C: review → review; head f928103a
- 2026-09-24 23:00Z UTC, F2C-L: running → review; head 76d966cc; estimate likely 8 → 1 h (a, b, d delivered and green; only review fixes remain)
- 2026-09-24 23:00Z UTC, tracker: forecast E4: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4, E4C, I2A; no remaining-effort estimate for A2, A3, C0, C3A, D10, E1B, E2C, E3A, E3C, E4, E4C, G7, G8, I2A, I3, M5, M6, U1R, U2, U3, U4, W5)
- 2026-09-24 23:00Z UTC, tracker: forecast E3A: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4C; no remaining-effort estimate for A2, A3, C0, C3A, D10, E1B, E2C, E3A, E3C, E4C, G7, G8, M5, M6, U1R, U2, U3, U4, W5)
- 2026-09-24 23:00Z UTC, tracker: forecast E4C: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4C; no remaining-effort estimate for D10, E1B, E2C, E3C, E4C, G7, G8, M5, M6, W5)
- 2026-09-24 23:00Z UTC, tracker: forecast E3C: unknown: no remaining-effort estimate for D10, E2C, E3C, G7, G8, I8, M5, M6, W5 → unknown: no remaining-effort estimate for D10, E2C, E3C, G7, G8, M5, M6, W5 (because: no remaining-effort estimate for D10, E2C, E3C, G7, G8, M5, M6, W5)
- 2026-09-24 22:58Z UTC, I8: running → review; head 103d20a8; estimate likely unknown → 8 h (code half done and tested; left: coordinator ops O1-O14 (two ~1 h maintenance windows), I8 follow-ups after WR-I8-1..5 land (~1-2 h), inputs P-25/D10 outside the lane)
- 2026-09-24 22:36Z UTC, F2C-C: review → complete; head b8de6171; estimate likely 1 → 0 h (merged after the verify-lane fix round (ACCEPT_WITH_FIXES at b8de6171))
- 2026-09-24 22:28Z UTC, F2C-C: review → review; head 35d1e32a; estimate likely 1.5 → 1 h (0-F1, 0-F2, 2-F2C-C-R1, 2-F2C-C-R2 closed with regressions; left: wiring requests, R109/P-22 application)
- 2026-09-24 22:31Z UTC, tracker: forecast E4: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4, E4C, I2A; no remaining-effort estimate for A2, A3, C0, C3A, D10, E1B, E2C, E3A, E3C, E4, E4C, G7, G8, I2A, I3, I8, M5, M6, U1R, U2, U3, U4, W5)
- 2026-09-24 22:31Z UTC, tracker: forecast E3A: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4C; no remaining-effort estimate for A2, A3, C0, C3A, D10, E1B, E2C, E3A, E3C, E4C, G7, G8, I8, M5, M6, U1R, U2, U3, U4, W5)
- 2026-09-24 22:31Z UTC, tracker: forecast E4C: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4C; no remaining-effort estimate for D10, E1B, E2C, E3C, E4C, G7, G8, I8, M5, M6, W5)
- 2026-09-24 22:31Z UTC, tracker: forecast E3C: unknown: no remaining-effort estimate for D10, E1C, E2C, E3C, F2C, G7, G8, I8, M5, M6, W5 → unknown: no remaining-effort estimate for D10, E2C, E3C, G7, G8, I8, M5, M6, W5 (because: no remaining-effort estimate for D10, E2C, E3C, G7, G8, I8, M5, M6, W5)
- 2026-09-24 22:31Z UTC, F2C-L: review → running; head d566c83b
- 2026-09-24 22:31Z UTC, TRACKER: complete → complete; head 803f6cb1
- 2026-09-24 22:15Z UTC, F2C-C: running → review; head d414607d; estimate likely unknown → 1.5 h (contract, fixtures and both suites done; left: review fixes and applying wiring requests)
- 2026-09-24 22:15Z UTC, E1C: running → review; head 2531dc49; estimate likely unknown → 4 h (UPLOAD-RESTART over M5 store once it lands; hosted smoke analysis; review rounds)
- 2026-09-24 22:13Z UTC, F2C-L: running → review; head 2d5e4743; estimate likely unknown → 8 h (slices b and d remain; b changes the v1 TerminalOutcome consumed by every JobStore suite)
- 2026-09-24 22:31Z UTC, tracker: rejected update: stale: at 2026-09-24T21:50:00Z is not newer than lane S3 state 2026-09-24T21:58:00Z
- 2026-09-24 22:30Z UTC, tracker: rejected update: malformed: estimate confidence 'med' not in ['unknown', 'low', 'medium', 'high']
- 2026-09-24 22:30Z UTC, tracker: rejected update: malformed: estimate confidence 'med' not in ['unknown', 'low', 'medium', 'high']
- 2026-09-24 22:30Z UTC, tracker: rejected update: malformed: estimate confidence 'med' not in ['unknown', 'low', 'medium', 'high']
- 2026-09-24 21:59Z UTC, E3C: queued → running
- 2026-09-24 21:59Z UTC, M6: queued → running
- 2026-09-24 22:30Z UTC, tracker: rejected update: malformed: estimate confidence 'med' not in ['unknown', 'low', 'medium', 'high']
- 2026-09-24 22:24Z UTC, TRACKER: review → review; head 32bbe9a8; estimate likely 1.5 → 0.5 h (fix round done; remaining is re-verification follow-up only)
- 2026-09-24 22:04Z UTC, TRACKER: running → review; head 7fa0e76f; estimate likely unknown → 1.5 h (one review/fix round plus schema tweaks when real lane update files arrive)
- 2026-09-24 22:02Z UTC, tracker: forecast E4: none → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4, E4C, I2A; no remaining-effort estimate for A2, A3, C0, C3A, D10, E1B, E1C, E2C, E3A, E3C, E4, E4C, F2C, G7, G8, I2A, I3, I8, M5, M6, U1R, U2, U3, U4, W5)
- 2026-09-24 22:02Z UTC, tracker: forecast E3A: none → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4C; no remaining-effort estimate for A2, A3, C0, C3A, D10, E1B, E1C, E2C, E3A, E3C, E4C, F2C, G7, G8, I8, M5, M6, U1R, U2, U3, U4, W5)
- 2026-09-24 22:02Z UTC, tracker: forecast E4C: none → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4C; no remaining-effort estimate for D10, E1B, E1C, E2C, E3C, E4C, F2C, G7, G8, I8, M5, M6, W5)
- 2026-09-24 22:02Z UTC, tracker: forecast E3C: none → unknown: no remaining-effort estimate for D10, E1C, E2C, E3C, F2C, G7, G8, I8, M5, M6, W5 (because: no remaining-effort estimate for D10, E1C, E2C, E3C, F2C, G7, G8, I8, M5, M6, W5)
- 2026-09-24 21:58Z UTC, coordinator: Per-lane task-local service ports reserved in contracts/tasklocal.py (bfb3a8af); dispatch port ranges 55510–55539 replaced; integration head f764e396.
- 2026-09-24 21:58Z UTC, coordinator: Coordinator update 1 (labelled 22:30Z; recorded f764e396 at 21:58Z host clock). S3 complete and merged (fff8416e; manifest S3 implemented at fdbbf87c): all twelve RV findings OPEN at dff31efc; run3 dataset-resume FAIL = regime mismatch (legacy_usd vs CREDIT oracle), not a runtime defect; envelope FAIL = supported 0.5/s; run3 launch ≈20:29Z box clock (not 21:29Z); soak ends ≈01:01–01:20Z. Inputs P-02 re-opened, P-05 second hosted tenant, P-06 processor digests, P-24 key inventory + numeric caps, P-25 backup/PITR unverified.
- 2026-09-24 21:55Z UTC, coordinator: Dispatch 2: D10 (SQL writer), M5, W5, G7, G8 running from dff31efc; 12 lanes active; agent slots 14 (2 reserved for review).
- 2026-09-24 21:40Z UTC, coordinator: Dispatch 1: seven lanes (S3, TRACKER, E2C, F2C-L, F2C-C, E1C, I8) running from dff31efc; D10, M5, W5, G7, G8, M6, E3C, E4C queued. All lane estimates unknown at baseline.
- 2026-09-24 21:30Z UTC, coordinator: Session 03 baseline: main dff31efc; integration branch claude/consumer-v1 created; validator PASS; run3 on bda1586 still running.

## History

The v46 backend-first tracker is preserved at [research/plan/evidence/coordinator/tracker-v46/README.md](tracker-v46/README.md) (commit `dff31efc`): E4B closure bands B0–B4: 28 done · 2 in progress (E1B, E4B) · 0 remaining of 30 packages; 33 checkpoints 2026-09-21T22:44Z → 2026-09-24T21:00Z.
