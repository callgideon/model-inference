# Consumer v1 progress tracker

Generated 2026-09-26 00:33Z UTC by `python3 research/plan/scripts/progress.py` from [tasks.json](../../tasks.json) (manifest v4) and [progress-state.json](progress-state.json) (overlay revision 61, updated 2026-09-26 00:33Z UTC). Generated file; never hand-edit. Program: [consumer-v1 (program 22)](../../22-consumer-v1-implementation.md). Full view: [progress.html](progress.html).

## Overview

- Integration branch `claude/consumer-v1` (head `8d9e5b5f`), base `dff31efc`, main `dff31efc`.
- Deployed candidate `bda15866e5700f3856d7142580da842fba9bbd23` (third install; image infrx-runtime:bda1586 = sha256:cc2a80c9396f6ebec8cd151770a0b8f221a306a56364f2562f90afd82a1cbebb (S3 identity table); MAX_VIDEO_SECONDS=82, ENGINE_MAX_NUM_SEQS=8, WORKER_CONCURRENCY=8, LARGE_BODY_LIMIT=8; regime **legacy_usd**).
- Lowest open band: V2 runtime repairs; bands with active work: V0, V1, V2, V3, V5, V6.
- Agent slots: 16 total, 9 active lanes, 2 reserved.
- Validation: 0 error(s), 16 warning(s).

### Actionable blockers

- P-01 open — DECIDED 2026-09-25, enactment pending (15-pending-inputs.md 'Decisions 2026-09-25'): Approved CREDIT rate card (unit, rounding, failed-execution disclosure) (owner coordinator (publish-card); blocks E4C)
- P-02 open — DECIDED 2026-09-25, enactment pending (15-pending-inputs.md 'Decisions 2026-09-25'): Re-opened by S3: inventory the pilot's USD 5.00 test grant and legacy_usd usage (W12, E1B, E4B) read-only before CREDIT activation; no conversion (owner coordinator (read-only dry-run); blocks E4C)
- P-05 open — DECIDED 2026-09-25, enactment pending (15-pending-inputs.md 'Decisions 2026-09-25'): Verified signup email/callback/recovery and abuse bounds on the target; also a second verified hosted test tenant (E1B --tenant-keys, E4C two-tenant/fairness cells) per S3 (owner operator-held (confirm 2nd user, verified-count read); then coordinator (grant/issue-key); A2/I2A public onboarding; blocks E1B, E4C, I2A, E4)
- P-06 open — DECIDED 2026-09-25, enactment pending (15-pending-inputs.md 'Decisions 2026-09-25'): Served-bytes digests of processor_config.json and preprocessor_config.json in the pinned serving record before E4C freezes the candidate (S3 finding 10) (owner coordinator (SSM inventory.sh); I8 (record + PINNED); blocks E4C)
- P-17 open — DECIDED 2026-09-25, enactment pending (15-pending-inputs.md 'Decisions 2026-09-25'): Final operator decision accepting the backend candidate (then App before Lab) (owner coordinator at E4C handback; blocks E4C)
- P-18 open — DECIDED 2026-09-25, enactment pending (15-pending-inputs.md 'Decisions 2026-09-25'): Predeclared workload/SLO/error/recovery limits for E4C (owner E4C lane (protocol §5 + certify.py thresholds before first qualifying run); blocks E4C)
- P-24 open — DECIDED 2026-09-25, enactment pending (15-pending-inputs.md 'Decisions 2026-09-25'): Versioned test profile with numeric request/byte/spend caps (target/window/stop rules exist, S3 §4.2); read-only inventory of the two pre-cutover consumer keys before the next E4B_WINDOW_OK=1 run (owner E4C lane (profile); E1C micro-lane (CREDIT spend schema); operator-held (+40,000 CREDIT adjust); blocks E4C)
- P-25 open — DECIDED 2026-09-25, enactment pending (15-pending-inputs.md 'Decisions 2026-09-25'): Operations/retention ownership: TTLs, alert destination (missing; I8 slice 4 BLOCKED), hosted backup/PITR ⚠️ unverified, and the box's newest install backup (27af05a) is not a known-good rollback bundle (owner M6/I8 (collector wiring + settings); I8 micro-lane (deliver.py SNS); operator-held (topic or webhook, 72/74, PITR read); blocks E4C)
- P-26 open — DECIDED 2026-09-25, enactment pending (15-pending-inputs.md 'Decisions 2026-09-25'): Key revocation during an identity-source outage: a key cached before the outage is served past KEY_TTL (60 s) until PostgREST answers again; decide bounded staleness (state it in the U2/A3 copy) vs fail-closed 503s (G7 verification / G8 0-G8-R3) (owner A3/U2 (copy only); blocks E4C)
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
- E4C (BACKEND-READY): blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-24, P-25, P-26; no GPU window allocated for E1B, E4C; no remaining-effort estimate for E1B, E4C
- E3A (APP-LOCAL): blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-24, P-25, P-26; no GPU window allocated for E1B, E4C; no remaining-effort estimate for E1B, E4C
- E4 (APP-PILOT): blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-24, P-25, P-26; no GPU window allocated for E1B, E4, E4C, I2A; no remaining-effort estimate for E1B, E4, E4C, I3

### Next ready work

- `E1B` Measure the end-to-end Marlin baseline and operating envelope (no lane assigned)

## Progress summaries

Task counts: manifest implemented/integrated over an explicit denominator. Cells: gate cells marked PASS. Neither implies launch readiness.

| Category | Implemented/integrated | Active | Acceptance cells PASS |
|---|---|---|---|
| Backend corrections | 10 / 14 | E2C, E3C, M6, W5 | BACKEND-LOCAL 0/7; BACKEND-READY 0/6 |
| App completion | 4 / 12 | C3A, I2A, U2, U3, U4 | APP-LOCAL 0/17; APP-PILOT 0/5 |
| Deferred Lab / hosting / later | 0 / 57 | none | n/a |
| Reused baseline | 44 / 44 | none | n/a |
| Superseded | 0 / 6 | none | n/a |

## Milestones and ETA

Never a date while an open input or an unallocated GPU window sits on the remaining path.

| Milestone | Gate | Status | Forecast | Controlling constraint | Effort o/l/p | Wall-clock o/l/p | Confidence |
|---|---|---|---|---|---|---|---|
| `E3C` | BACKEND-LOCAL (PENDING) | forecast | 2026-09-26 04:09Z – 2026-09-26 11:57Z (likely 2026-09-26 06:45Z) | dependency path W5 → E3C | 3.6 / 6.2 / 11.4 h | 3.6 / 6.2 / 11.4 h | medium |
| `E4C` | BACKEND-READY (PENDING) | blocked | blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-24, P-25, P-26 | blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-24, P-25, P-26; no GPU window allocated for E1B, E4C; no remaining-effort estimate for E1B, E4C | — | — | unknown |
| `E3A` | APP-LOCAL (PENDING) | blocked | blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-24, P-25, P-26 | blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-24, P-25, P-26; no GPU window allocated for E1B, E4C; no remaining-effort estimate for E1B, E4C | — | — | unknown |
| `E4` | APP-PILOT (PENDING) | blocked | blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-24, P-25, P-26 | blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-24, P-25, P-26; no GPU window allocated for E1B, E4, E4C, I2A; no remaining-effort estimate for E1B, E4, E4C, I3 | — | — | unknown |

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
| RV-01 | open | F2C (complete), G7 (complete), A3 (complete) | dff31efc | [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md) |
| RV-02 | open | D10 (complete), M5 (complete), E1C (complete), E3C (review) | dff31efc | [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md) |
| RV-03 | open | D10 (complete), M6 (review), I8 (complete), E3C (review) | dff31efc | [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md) |
| RV-04 | open | S3 (complete), E4C (queued) | dff31efc | [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md) |
| RV-05 | open | F2C (complete), D10 (complete), W5 (review), G7 (complete), E3C (review) | dff31efc | [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md) |
| RV-06 | open | C0 (complete), C3A (review), A2 (complete), A3 (complete), U1R (complete), U2 (running), U3 (running), U4 (review), E3A (queued) | dff31efc | [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md) |
| RV-07 | open | E1C (complete), M5 (complete), G7 (complete) | dff31efc | [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md) |
| RV-08 | open | E1C (complete), E4C (queued) | dff31efc | [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md) |
| RV-09 | open | D10 (complete), I8 (complete), E4C (queued) | dff31efc | [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md) |
| RV-10 | open | I8 (complete), E4C (queued) | dff31efc | [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md) |
| RV-11 | open | F2C (complete), D10 (complete), G7 (complete), U4 (review), E3C (review) | dff31efc | [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md) |
| RV-12 | open | E2C (review), E3C (review) | dff31efc | [research/plan/evidence/coordinator/2026-09-24-S3-reconciliation.md](2026-09-24-S3-reconciliation.md) |

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
| soak | RUNNING | bounded 14,400+900 s at 0.25/s; start ≈20:46Z from 609 rows at 21:26Z (S3); ends ≈01:01–01:20Z box clock. Cannot PASS at bda1586: reconciled_at_end is always UNKNOWN because record_reconciliation has no runtime caller (S3 finding 4) | expected end 2026-09-25 01:01Z–2026-09-25 01:20Z passed at generation (27.8 h since start); verdict still RUNNING: verify |
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
| E2C | E2C L3-REBASE | review | codex/l3-rebase | 5bef53bc → 8a94a855 | ports postgres 55448, valkey 55474, s3 55475 (contracts/tasklocal.py, bfb3a8af), prefix infrx-e2c- | 2026-09-25 18:12Z | coordinator review + merge; D10-FOLLOWUP's 0022 re-baselines the migration pin and RUNTIME_FUNCTIONS; G7/I8 add panels for the four intake metric families (ob10) | 0–1 h remaining (likely 0.25 h), confidence high, estimated 2026-09-25 18:12Z — STALE; basis: layer-3 rls green at head; only 0022 will need a re-baseline |
| F2C-L | F2C fix-round | complete | codex/f2c-lifecycle | dff31efc → c4873027 | none | 2026-09-25 00:19Z | merged into claude/consumer-v1 at 78661bf0 (+ b413f253: R111-R116, SURFACE_VERSION contracts-v2.1, manifest F2C implemented) | 0–0 h remaining (likely 0 h), confidence high, estimated 2026-09-25 00:19Z; basis: merged after the verify-lane fix round (ACCEPT_WITH_FIXES at c4873027) |
| F2C-C | F2C c | complete | codex/f2c-catalog | dff31efc → b8de6171 | none | 2026-09-24 22:36Z | merged into claude/consumer-v1 at 2bd7f347 with the export wiring (179a1a0b); R109 numbered; P-22 decided | 0–0 h remaining (likely 0 h), confidence high, estimated 2026-09-24 22:36Z; basis: merged after the verify-lane fix round (ACCEPT_WITH_FIXES at b8de6171) |
| E1C | E1C E1C fix round (0-B1, 0-B2, 2-E1C-ACC-01..04, 0-M1..0-M4) | complete | codex/e1c-client | dff31efc → 6cb6c929 | ports postgres 55449 (contracts/tasklocal.py, bfb3a8af), prefix infrx-e1c- | 2026-09-25 19:59Z | UPLOAD-RESTART needs M5 durable uploads; hosted smoke is the coordinator's (amended command, --unprofiled smoke, in the evidence) | 0–0 h remaining (likely 0 h), confidence high, estimated 2026-09-25 00:19Z; basis: merged; start dependency F2C implemented |
| I8 | I8 fix round (review of 103d20a8/d2f90ce6): slices 1, 2, 3, 5, 6 | complete | codex/i8-operate | dff31efc → ce33c24c | ports postgres 55450, valkey 55476 (contracts/tasklocal.py, bfb3a8af), prefix infrx-i8- | 2026-09-25 23:50Z | P-24: canary spend bound + dedicated canary tenant (op 5 canary half BLOCKED; 72 enables the timer only with P24_APPROVED); P-25: alert destination/owner/escalation (slice 4 delivery proof BLOCKED); P-25: approved MIRROR_URL prefix for the model mirror (O9-O12); image store for a replacement-instance restore (engine image pinned by registry digest; docker load does not restore it) - 2-ACC-3 open, blocks the slice-5 acceptance; Supabase personal access token for supabase_policy.py (O8); D10: least-privilege runtime login + function list, read-only monitor login (O7, WR-I8-6); every live op after certification run3 (coordinator) | 0–0 h remaining (likely 0 h), confidence high, estimated 2026-09-25 01:38Z; basis: merged after the verify-lane fix round (ACCEPT_WITH_FIXES at ce33c24c) |
| D10 | D10 fix round (review of f9b6ce5c) | complete | codex/d10-durable | dff31efc → 483d9eca | ports postgres 55442, valkey 55469 (contracts/tasklocal.py, bfb3a8af), prefix infrx-d10- | 2026-09-25 22:42Z | DOOR-REVOKE verified ACCEPT_WITH_FIXES 273990a0 (0023 revokes admit/claim_preparation from infrx_runtime; RUNTIME_FUNCTIONS 41 by name; merge gate 'only with W5' satisfied on the union); the union lane merges it with wiring W-DR1 v2. | 0–0 h remaining (likely 0 h), confidence high, estimated 2026-09-25 08:19Z; basis: merged after the verify-lane fix round (ACCEPT_WITH_FIXES at 483d9eca); follow-ups are separate lanes |
| M5 | M5 fix round (verifier, one round): M5-R1 stack drill INFRX_D_TASK guard, M5-R1b R82 attach binding, M5-RS-1 WR-2 confirmation | complete | codex/m5-uploads | dff31efc → c0c4ce25 | ports postgres 55443, s3 55470 (contracts/tasklocal.py, bfb3a8af), prefix infrx-m5- | 2026-09-25 08:19Z | D10: 0019 lacks F2C's media_refused / UPLOAD_ABORT_REASONS (strict xfail test_stack__os_processes_a_probe_refusal_is_final_on_postgres); WR-1 needs D10's infrx/state/lifecycle.py on the integration branch | 0–0 h remaining (likely 0 h), confidence high, estimated 2026-09-25 08:19Z; basis: merged after the finish-verify fix round (ACCEPT_WITH_FIXES at c0c4ce25) |
| W5 | W5 fix round (review findings 0-W5-R1..R3, 1-W5-RULES-1/2, 2-W5-ACC-1..3) | review | codex/w5-readiness | dff31efc → 97381015 | ports postgres 55445, valkey 55472 (contracts/tasklocal.py, bfb3a8af), prefix infrx-w5- | 2026-09-25 23:50Z | merge gate: W5 only together with wiring 1 + D10 0019/PgLifecycle + G7 admit_ready; run the 7 MinIO PG round trips on the merged SHA; box warm-up/cold-cache run (coordinator) after run3 | 1–4 h remaining (likely 2 h), confidence medium, estimated 2026-09-25 21:06Z; basis: verification third lens + possible fix round, then the union merge and E3C final run |
| G7 | G7 fix round (CM-1..5, ACC-1..3) | complete | codex/g7-catalog | dff31efc → 2f7ed3ac | ports postgres 55446 (contracts/tasklocal.py, bfb3a8af), prefix infrx-g7- | 2026-09-25 23:50Z | merge gate: codex/d10-durable WR-3c (PgJobStore result_expires_at) must merge before or with G7, else PG successes read 410 and G8's tests/g/ops/test_acceptance_pg.py regresses | 0–0 h remaining (likely 0 h), confidence high, estimated 2026-09-25 08:29Z; basis: merged after the verify-lane fix round (ACCEPT_WITH_FIXES at 2f7ed3ac) |
| G8 | G8 fix round: 0-G8-R1/2-ACC-1 (pre-freeze admission straddling the transition), 0-G8-R2/1-G8-RULES-1/2-ACC-2 (contracts run on 55432; evidence correction), 0-G8-R3 (revoked-key read bound routed to G7) | complete | codex/g8-credit-ops | dff31efc → 09f4ab2a | ports postgres 55447, valkey 55473 (contracts/tasklocal.py, bfb3a8af), prefix infrx-g8- | 2026-09-25 00:44Z | P-01 approved launch rates (live activation only); coordinator box dry-run window (read-only) | 0–0 h remaining (likely 0 h), confidence high, estimated 2026-09-25 00:44Z; basis: merged after the verify-lane fix round (ACCEPT_WITH_FIXES at 09f4ab2a) |
| M6 | M6 phase 2 fix round: R1 prepared row-before-bytes proven; R3/R4/A2 pins hold on get, pin and evict; R2/A1 refetch race pinned as strict xfail (WR-7); A3 ruling, A4/A5 wiring | review | codex/m6-phase2 | bd556c5f → dea32507 | ports postgres 55444 (INFRX_D_TASK=m6), prefix infrx-m6-, db infrx_m6* | 2026-09-25 21:06Z | P-25 unresolved; WR-3 (E3C world.py + s06 clock) gates the merge; WR-7 (D10 register refresh) for the refetch race; R114 amendment | 1–5 h remaining (likely 2 h), confidence medium, estimated 2026-09-25 17:12Z — STALE; basis: lane code complete; remaining is WR-1/2/3/7 composition outside the lane, the R114 ruling, and P-25 |
| E3C | E3C phase 2 interim rerun: harness re-pointed to G7 (s13 PublishedModel, expiry bypass), R130 (s08 measured), G8 lock bound (s09), RUNTIME-LOGIN (whole matrix on infrx_runtime), M6 phase 2 (WR-3 collector + grace) | review | codex/e3c-rerun | 19670a8c → 7d0d3d7b | ports compose block 56900-56999 (postgres 56932), prefix infrx-e3c-, db infrx_e3c | 2026-09-25 22:42Z | W5 (claim_preparation_ready, fail_preparation): s04 video/permanent, s05 admission; F-3 relay.py:167 admits via admit_credit; no caller of PgLifecycle.admit_ready (unowned: G7 deferred, W5 gate): s04 text/late, s05 readiness; F-4 PgStreamStore.expire (journal.py:121) has no caller; stream_chunks deltas kept past JOURNAL_CHUNK_TTL_S: s06 scrub, s11 scrub race | 1–4 h remaining (likely 2 h), confidence medium, estimated 2026-09-25 22:42Z; basis: W5-F5 fix + union round 2 + one 13–25 min final matrix run |
| E4C | E4C  | queued | — | — → — | none | 2026-09-25 22:42Z | after E3C; needs an allocated GPU window | unknown (not estimated at baseline (lane has not inspected its slice yet)) |
| C0 | C0 fix round: 0-C0-V1 (consumerSession tested via consumerSessionFrom), 1-C0-V1 (consoleShell: operator bypass, route-gated redirects; WR-1 revised); 0-C0-V2/0-C0-V3 need coordinator WR-4/WR-1/WR-2 | complete | codex/app-c0 | 46776646 → ab0b5179 | ports app-c0 postgres 55451, prefix infrx-app-c0- | 2026-09-25 23:28Z | merged into claude/consumer-v1 via codex/app-union 7ebed92d at 68128815; manifest implemented (closure note lists the open wirings) | 0–0 h remaining (likely 0 h), confidence high, estimated 2026-09-25 23:28Z; basis: merged after the in-workflow verification (ACCEPT_WITH_FIXES) |
| U1R | U1R fix round: 0-U1R-V-01 (preview gate tested), 1-U1R-V01 (ledger page drops actor), 1-U1R-V02 lane side (sidebarCredits), 0-U1R-V-02 as exact D10 index WR | complete | codex/app-u1r | 46776646 → 7482c21a | ports postgres 55457 (INFRX_D_TASK=app-u1r), prefix infrx-app-u1r-, db infrx_app-u1r_credit, infrx_app-u1r_perf | 2026-09-25 23:28Z | merged into claude/consumer-v1 via codex/app-union 7ebed92d at 68128815; manifest implemented (closure note lists the open wirings) | 0–0 h remaining (likely 0 h), confidence high, estimated 2026-09-25 23:28Z; basis: merged after the in-workflow verification (ACCEPT_WITH_FIXES) |
| A2 | A2 fix round: 0-A2-CM-01..04, 1-A2-V-RS-01/02 — /welcome read, emailSettled, form wiring, grant RPC pin, sign-in claim + routing | complete | codex/app-a2 | 46776646 → cd4a688e | ports postgres 55460 (INFRX_D_TASK=app-a2), container removed at end, prefix infrx-app-a2-, db infrx_app-a2 | 2026-09-25 23:28Z | merged into claude/consumer-v1 via codex/app-union 7ebed92d at 68128815; manifest implemented (closure note lists the open wirings) | 0–0 h remaining (likely 0 h), confidence high, estimated 2026-09-25 23:28Z; basis: merged after the in-workflow verification (ACCEPT_WITH_FIXES) |
| A3 | A3 fix round: 0-A3-V1 page render guard (+18 page/copy mutants, refill/top-up/expiry bans); 1-A3-RSI-1 runner/list renamed to run-catalog-mutants.mjs / catalog-mutants.json (no A2 collision) | complete | codex/app-a3 | 46776646 → 7b0ea93f | ports fakes only, prefix infrx-app-a3- | 2026-09-25 23:28Z | merged into claude/consumer-v1 via codex/app-union 7ebed92d at 68128815; manifest implemented (closure note lists the open wirings) | 0–0 h remaining (likely 0 h), confidence high, estimated 2026-09-25 23:28Z; basis: merged after the in-workflow verification (ACCEPT_WITH_FIXES) |
| C3A | C3A C3A trusted consumer actions (keys, grant retry, settings, operator actions via narrow ports; idempotency/CSRF) | review | codex/app-c3a | 46776646 → 33b88daf | ports app-c3a postgres 55452, prefix infrx-app-c3a- | 2026-09-25 23:50Z | fix round done, rechecks in; merges with U4 in APP-UNION round 2 | 0.5–3 h remaining (likely 1.5 h), confidence medium, estimated 2026-09-25 23:28Z; basis: in-workflow verification + union round 2 |
| U4 | U4 U4 owned request detail and result lifecycle (bounded polling, expiry, two-tenant probes) | review | codex/app-u4 | 46776646 → d76f912f | ports app-u4 postgres 55456, prefix infrx-app-u4- | 2026-09-25 23:28Z | verified ACCEPT_WITH_FIXES (V-01/V-02 fixed; V-03 → WR-U4-2 D10 SQL in 0024); merges with C3A in APP-UNION round 2 | 0.5–3 h remaining (likely 1.5 h), confidence medium, estimated 2026-09-25 23:28Z; basis: in-workflow verification + union round 2 |
| U2 | U2 U2 keys and supported settings (one-time plaintext, rotation, truthful privacy copy) | running | codex/app-u2 | 46776646 → — | ports fakes only, prefix infrx-app-u2- | 2026-09-25 23:50Z | implementing (App wave wf_77b93903-38d) on top of U1R + C3A | 2–10 h remaining (likely 5 h), confidence low, estimated 2026-09-25 21:11Z; basis: brief-sized 2–8 h slices plus in-workflow verification; not yet inspected by the lane |
| U3 | U3 U3 minimal operator controls (rate publication, reasoned adjust/grant, suspension, audited idempotent ops) | running | codex/app-u3 | 46776646 → — | ports app-u3 postgres 55453, prefix infrx-app-u3- | 2026-09-25 23:50Z | implementing (App wave) on top of U1R + C3A | 2–10 h remaining (likely 5 h), confidence low, estimated 2026-09-25 21:11Z; basis: brief-sized 2–8 h slices plus in-workflow verification; not yet inspected by the lane |
| I2A | I2A I2A-PREP: hosting configuration as code (env matrix, preview credential isolation, callback allowlists, private no-store, release identity) + App deploy/rollback runbook; no live deploy | review | codex/i2a-prep | fd40748c → 8ca672d0 | ports none, prefix infrx-i2a-prep- | 2026-09-26 00:33Z | I2A-PREP merged a5ca1cd1 (+ wiring 416da075: /api/version public, console-built in check, trailing-dot refusal); R134 numbered. The live half (Vercel env, staging project, P-05 auth settings, deploy + smoke + known-good record) runs after BACKEND-READY with the operator inputs listed in i/I2A-prep-9fe9484.md | 2–12 h remaining (likely 5 h), confidence low, estimated 2026-09-26 00:33Z; basis: prep merged; the live deploy waits for the accepted backend and seven operator inputs |
| E3A | E3A E3A-PREP (coordinator): browser + real-adapter journey harness on the e4b compose block; gate not claimed | queued | codex/e3a-prep | fd40748c → — | ports e4b compose block 56800–56899, prefix infrx-e3a-prep- | 2026-09-25 23:50Z | E3A proper dispatches after BACKEND-LOCAL/READY; E3A-PREP (coordinator preparation lane, workflow wf_06c06a68-c32) builds the harness now without claiming the gate | 2–8 h remaining (likely 4 h), confidence low, estimated 2026-09-25 23:50Z; basis: preparation lane; not yet inspected |

### Queues and locks

- Review queue: W5, M6, E3C, E2C, A2, U1R, A3, C0.
- Integration queue: empty.
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

- warning: stale estimate: lane E2C estimated at 2026-09-25T18:12Z (older than 6 h)
- warning: stale estimate: lane M6 estimated at 2026-09-25T17:12:10Z (older than 6 h)
- warning: overlapping writers: E4C (queued) and E3A (queued) both own tests/integration/backend/ / tests/integration/
- warning: overlapping writers: C3A (review) and I2A (review) both own apps/app/app/actions.ts / apps/app/ (+2 more)
- warning: overlapping writers: C3A (review) and E3A (queued) both own apps/app/tests/c/ / apps/app/tests/
- warning: overlapping writers: U4 (review) and U2 (running) both own apps/app/tests/u/ / apps/app/tests/u/
- warning: overlapping writers: U4 (review) and U3 (running) both own apps/app/tests/u/ / apps/app/tests/u/
- warning: overlapping writers: U4 (review) and I2A (review) both own apps/app/app/(console)/usage/ / apps/app/ (+1 more)
- warning: overlapping writers: U4 (review) and E3A (queued) both own apps/app/tests/u/ / apps/app/tests/
- warning: overlapping writers: U2 (running) and U3 (running) both own apps/app/tests/u/ / apps/app/tests/u/
- warning: overlapping writers: U2 (running) and I2A (review) both own apps/app/app/(console)/keys/ / apps/app/ (+2 more)
- warning: overlapping writers: U2 (running) and E3A (queued) both own apps/app/tests/u/ / apps/app/tests/
- warning: overlapping writers: U3 (running) and I2A (review) both own apps/app/app/(console)/admin/ / apps/app/ (+1 more)
- warning: overlapping writers: U3 (running) and E3A (queued) both own apps/app/tests/u/ / apps/app/tests/
- warning: overlapping writers: I2A (review) and E3A (queued) both own apps/app/ / apps/app/tests/
- warning: 2 update file(s) not applied yet: I2A-20260926T0010Z.json, I2A-20260926T0022Z.json (run apply-updates)

## Pending inputs

| Input | Status | What | Owner | Blocks |
|---|---|---|---|---|
| P-01 | open | DECIDED 2026-09-25, enactment pending (15-pending-inputs.md 'Decisions 2026-09-25'): Approved CREDIT rate card (unit, rounding, failed-execution disclosure) | coordinator (publish-card) | E4C |
| P-02 | open | DECIDED 2026-09-25, enactment pending (15-pending-inputs.md 'Decisions 2026-09-25'): Re-opened by S3: inventory the pilot's USD 5.00 test grant and legacy_usd usage (W12, E1B, E4B) read-only before CREDIT activation; no conversion | coordinator (read-only dry-run) | E4C |
| P-05 | open | DECIDED 2026-09-25, enactment pending (15-pending-inputs.md 'Decisions 2026-09-25'): Verified signup email/callback/recovery and abuse bounds on the target; also a second verified hosted test tenant (E1B --tenant-keys, E4C two-tenant/fairness cells) per S3 | operator-held (confirm 2nd user, verified-count read); then coordinator (grant/issue-key); A2/I2A public onboarding | E1B, E4C, I2A, E4 |
| P-06 | open | DECIDED 2026-09-25, enactment pending (15-pending-inputs.md 'Decisions 2026-09-25'): Served-bytes digests of processor_config.json and preprocessor_config.json in the pinned serving record before E4C freezes the candidate (S3 finding 10) | coordinator (SSM inventory.sh); I8 (record + PINNED) | E4C |
| P-17 | open | DECIDED 2026-09-25, enactment pending (15-pending-inputs.md 'Decisions 2026-09-25'): Final operator decision accepting the backend candidate (then App before Lab) | coordinator at E4C handback | E4C |
| P-18 | open | DECIDED 2026-09-25, enactment pending (15-pending-inputs.md 'Decisions 2026-09-25'): Predeclared workload/SLO/error/recovery limits for E4C | E4C lane (protocol §5 + certify.py thresholds before first qualifying run) | E4C |
| P-19 | resolved | RESOLVED 2026-09-25: g6e.2xlarge row added to cloud-pricing.md §3.1 (est. derivations) (15-pending-inputs.md 'Decisions 2026-09-25'): Sourced infrastructure price row or actual bill for cost-per-unit figures | research lane (cloud-pricing.md row) | E4C |
| P-22 | resolved | CLOSED: decided 2026-09-24 as R109 (resolve, then price); Canonical alias/legacy price identity decision (resolve before pricing vs per-string rows) | F2C-C/D10/G7 (D/G decision) | E4C |
| P-24 | open | DECIDED 2026-09-25, enactment pending (15-pending-inputs.md 'Decisions 2026-09-25'): Versioned test profile with numeric request/byte/spend caps (target/window/stop rules exist, S3 §4.2); read-only inventory of the two pre-cutover consumer keys before the next E4B_WINDOW_OK=1 run | E4C lane (profile); E1C micro-lane (CREDIT spend schema); operator-held (+40,000 CREDIT adjust) | E4C |
| P-25 | open | DECIDED 2026-09-25, enactment pending (15-pending-inputs.md 'Decisions 2026-09-25'): Operations/retention ownership: TTLs, alert destination (missing; I8 slice 4 BLOCKED), hosted backup/PITR ⚠️ unverified, and the box's newest install backup (27af05a) is not a known-good rollback bundle | M6/I8 (collector wiring + settings); I8 micro-lane (deliver.py SNS); operator-held (topic or webhook, 72/74, PITR read) | E4C |
| P-26 | open | DECIDED 2026-09-25, enactment pending (15-pending-inputs.md 'Decisions 2026-09-25'): Key revocation during an identity-source outage: a key cached before the outage is served past KEY_TTL (60 s) until PostgREST answers again; decide bounded staleness (state it in the U2/A3 copy) vs fail-closed 503s (G7 verification / G8 0-G8-R3) | A3/U2 (copy only) | E4C |

## Rejected updates

- `S3-20260924T2150Z.json`: stale: at 2026-09-24T21:50:00Z is not newer than lane S3 state 2026-09-24T21:58:00Z
- `F2C-L-20260924T2234Z.json`: stale: at 2026-09-24T22:34:29Z is not newer than lane F2C-L state 2026-09-24T23:00:20Z
- `E2C-20260925T0154Z.json`: malformed: estimate at: Invalid isoformat string: '2026-09-25T01:54+00:00:00+00:00'
- `I8-panels-20260925T0400Z.json`: impossible transition complete → review: complete is terminal (the coordinator reopens by editing the overlay)
- `D10-20260925T081832Z.json`: lane D10 belongs to D10, not I8
- `G8-20260925T0808Z.json`: impossible transition complete → review: complete is terminal (the coordinator reopens by editing the overlay)
- `G8-20260925T0900Z.json`: future-dated: at 2026-09-25T09:00:00Z is after host UTC now 2026-09-25T08:40:45Z (+15 min skew allowed); check the clock
- `I8-20260925T1643Z.json`: unknown task ID 'RUNTIME-LOGIN'
- `I8-20260925T0530Z.json`: unknown task ID 'I8-M6-WIRING'
- `I8-20260925T1704Z.json`: unknown task ID 'I8-M6-WIRING'
- `E1C-20260925T1713Z.json`: impossible transition complete → review: complete is terminal (the coordinator reopens by editing the overlay)
- `OPS-CLI-DSN-20260925T1734Z.json`: impossible transition complete → review: complete is terminal (the coordinator reopens by editing the overlay)
- `I8-20260925T1744Z.json`: impossible transition complete → review: complete is terminal (the coordinator reopens by editing the overlay)
- `E1C-PROFILE-20260925T1910Z.json`: impossible transition complete → review: complete is terminal (the coordinator reopens by editing the overlay)
- `I8-ALERT-20260925T1910Z.json`: impossible transition complete → review: complete is terminal (the coordinator reopens by editing the overlay)
- `I8-ALERT-20260925T1932Z.json`: impossible transition complete → review: complete is terminal (the coordinator reopens by editing the overlay)
- `ROLLOUT-FIXES-20260925T2125Z.json`: unknown task ID 'ROLLOUT-FIXES'
- `APP-UNION-20260925T2325Z.json`: unknown task ID 'APP-UNION'
- `G7-20260925T2326Z.json`: impossible transition complete → review: complete is terminal (the coordinator reopens by editing the overlay)

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
| `U2` | Keys and privacy/settings controls | App completion | planned | running | active: lane active |
| `U3` | Operator grants, suspension and pilot operations | App completion | planned | running | active: lane active |
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
| `A2` | Consumer signup verification and credited onboarding | App completion | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `A3` | Published catalog, credit rates and capability-matched examples | App completion | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `D6F` | Durable feedback and immutable author provenance | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `D6J` | Lab consent, USD budget and external submission coordination | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `G4U` | Owned upload HTTP adapter | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `G4F` | Owned feedback HTTP adapter | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `G4T` | Owned trace export HTTP adapter | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `T2I` | Inference analytics and content projection | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `T2F` | Feedback analytics projection | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `C3A` | Consumer key/privacy and platform operator actions | App completion | planned | review | active: lane active |
| `C3F` | Authorized feedback and review actions | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `C3L` | Lab judge and calibration control actions | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `L1` | Provider app shell and separate build/auth boundary | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `L2` | Provider role and purpose-specific data-access services | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `L3` | Assisted model registration and dev/prod revision services | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `L4` | Model/deployment/publication UI and aggregate health | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `I2A` | Reproducible App and single-GPU runtime deployment | App completion | planned | review | active: lane active |
| `I2L` | Independent Lab app and control-service deployment | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `E3A` | Consumer failure, security and signup-to-spend integration gate | App completion | planned | queued | blocked: gated: dispatch only after BACKEND-READY is accepted |
| `E3L` | Provider access, publication and rollback integration gate | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `E5L` | Provider traces, review and evaluation integration gate | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `F2R` | Close remaining wave-2 contract and verification carryovers | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `F2P` | Encode product-v2 CREDIT, identity, serving and permission contracts | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `D1R` | Add product-v2 schema without rewriting USD pilot migrations | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `C0` | Wire the consumer database query port and real account context | App completion | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `I0` | Repair installer atomicity and fail-closed startup prerequisite | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `E2R` | Repair service harness ownership, role matrix and shared test clock | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `G1R` | Revise ingress for consumer and provider endpoint audiences | Reused baseline | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `V1M` | Move the implemented trace explorer into the authorized Lab shell | Deferred Lab / hosting / later | planned | unassigned | blocked: deferred: Lab, hosting and later work follow App acceptance and their activation gates |
| `U1R` | Adapt consumer usage and balance views to CREDIT and explicit legacy USD | App completion | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
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
| `F2C` | Freeze durable lifecycle, expiry and public capability corrections | Backend corrections | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `E2C` | Make corrective verification reproducible on supported Linux | Backend corrections | implemented | review | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `D10` | Persist uploads, execution eligibility, safe cleanup and result read authority | Backend corrections | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `M5` | Persist upload lifecycle across gateway replacement | Backend corrections | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `M6` | Implement restart-safe content cleanup and bounded caches | Backend corrections | implemented | review | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `W5` | Enforce execution readiness and bounded worker recovery | Backend corrections | planned | review | active: lane active |
| `G7` | Align public capability discovery, alias pricing and persisted result expiry | Backend corrections | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `G8` | Prove headless consumer CREDIT operations and safe activation | Backend corrections | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `E1C` | Repair upload client and deliver valid resumable dataset/load measurement | Backend corrections | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `I8` | Operate continuously with bounded DB pools, durable artifacts and real rollback | Backend corrections | implemented | complete | done: implemented/integrated in the manifest (evidence-backed status, not release acceptance) |
| `E3C` | Integrate corrective backend with real services and process faults | Backend corrections | planned | review | active: lane active |
| `E4C` | Certify repaired CREDIT backend on final Marlin deployment | Backend corrections | planned | queued | blocked: waiting on start dependencies: E3C |
| `U4` | Expose owned consumer request detail and result lifecycle | App completion | planned | review | active: lane active |

## Activity log (newest first)

- 2026-09-25 23:50Z UTC, coordinator: G7-PROVISIONAL merged 2b112a05; App union checks green; user override extended to I2A; I2A-PREP and E3A-PREP dispatched; union round 2 at 74183655; U2/U3 implementing
- 2026-09-25 23:50Z UTC, tracker: rejected update: impossible transition complete → review: complete is terminal (the coordinator reopens by editing the overlay)
- 2026-09-25 23:28Z UTC, coordinator: APP-UNION merged 68128815: C0, U1R, A2, A3 implemented (verified lanes + coordinator wirings); U4 verified, C3A in its fix round; D10-APP-SQL 0024 takes WR-U4-2 and WR-C3A-4
- 2026-09-25 23:28Z UTC, tracker: forecast E3C: 2026-09-26 02:18Z – 2026-09-26 11:24Z (likely 2026-09-26 04:54Z) → 2026-09-26 03:04Z – 2026-09-26 10:52Z (likely 2026-09-26 05:40Z) (because: dependency path W5 → E3C)
- 2026-09-25 23:28Z UTC, tracker: rejected update: unknown task ID 'APP-UNION'
- 2026-09-25 22:38Z UTC, C0: review → review; head ffd5ba7c; estimate likely 3 → 2 h (code, decision and guards done; remaining = coordinator WR-1 (full file in evidence) + WR-4 gate, U1R/U4 WR-2, rerun console-c0-real and App suites on merged SHA with 0022)
- 2026-09-25 22:30Z UTC, A3: review → review; head fdd9e1f5; estimate likely 1.5 → 1 h (both findings closed and green; remaining = coordinator WR-1..WR-4, C0 merge, re-review)
- 2026-09-25 22:22Z UTC, U1R: review → review; head d199fffe; estimate likely 2 → 1 h (lane-side fixes and regressions green; remaining is coordinator WR-1+2 (exact diff + test) and D10 WR-3(b) (one index + test lines); WR-3(c) optional)
- 2026-09-25 22:10Z UTC, A3: running → review; head 19f84679; estimate likely 5 → 1.5 h (A3 code/tests complete and green; remaining = coordinator WR-1..WR-4, merge with C0 session context, one review round, hosted E4 check of INFRX_API_BASE_URL)
- 2026-09-25 22:03Z UTC, C0: running → review; head acfd9a51; estimate likely 5 → 3 h (code+tests done; remaining = coordinator WR-1..WR-4 patches, rerun of real stack and App suites on the merged SHA with 0022, review fixes; page rewrites belong to U1R/U4)
- 2026-09-25 21:54Z UTC, A2: review → review; head ed6a282d; estimate likely 3 → 2 h (lane code and tests green after the fix round; remaining WR-A2-1/2/3 application and the P-05 hosted auth configuration + staging fresh-email run)
- 2026-09-25 21:46Z UTC, U1R: running → review; head ae8c388a; estimate likely 5 → 2 h (lane implementation, tests, real-DB oracle and mutants complete; remaining is one review/fix round; wiring (nav label, sidebar CREDIT balance, D10 filters/totals, U4 route) is outside the lane)
- 2026-09-25 21:27Z UTC, A2: running → review; head 16fe503f; estimate likely 5 → 3 h (lane code complete and green; remaining WR-A2-1/2, review fixes, C3A reuse of the port, P-05 hosted auth config + staging fresh-email run)
- 2026-09-25 21:15Z UTC, U1R: running → running; head 46776646
- 2026-09-25 22:42Z UTC, coordinator: ROLLOUT-FIXES (a1e06236) and E4C-RUNBOOK (2f2ca075) merged after self-verifying workflows; DOOR-REVOKE verified 273990a0 (0023) → union lane; E3C early run on union 9d61d1e1: F-3/F-4 closed, F-5/F-6 new → W5-F5 lane
- 2026-09-25 22:42Z UTC, tracker: forecast E4: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-24, P-25, P-26 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-24, P-25, P-26 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-24, P-25, P-26; no GPU window allocated for E1B, E4, E4C, I2A; no remaining-effort estimate for E1B, E3A, E4, E4C, I2A, I3)
- 2026-09-25 22:42Z UTC, tracker: forecast E3A: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-24, P-25, P-26 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-24, P-25, P-26 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-24, P-25, P-26; no GPU window allocated for E1B, E4C; no remaining-effort estimate for E1B, E3A, E4C)
- 2026-09-25 22:42Z UTC, tracker: forecast E3C: 2026-09-26 02:11Z – 2026-09-26 19:05Z (likely 2026-09-26 07:23Z) → 2026-09-26 02:18Z – 2026-09-26 11:24Z (likely 2026-09-26 04:54Z) (because: dependency path W5 → E3C)
- 2026-09-25 22:42Z UTC, tracker: rejected update: unknown task ID 'ROLLOUT-FIXES'
- 2026-09-25 21:11Z UTC, coordinator: User decision: App completion tasks dispatched ahead of BACKEND-READY (dispatch only; gates unchanged). App wave workflow wf_77b93903-38d: C0/U1R/A2/A3 running, C3A/U4/U2/U3 queued behind their predecessors
- 2026-09-25 21:06Z UTC, coordinator: Rollout gates rehearsal at e607b705 (G1/G3/G4 PASS; G5 harness defect; no rollback target once 0019+ applied); ROLLOUT-FIXES, KNOWN-GOOD-PROOF, DOOR-REVOKE, E4C-RUNBOOK lanes running as self-verifying workflows; E3C final run pre-staged
- 2026-09-25 21:06Z UTC, coordinator: M6-WIRING (28dd1af2) and D10-FOLLOWUP (c584f54a) verified ACCEPT_WITH_FIXES with all rechecks fixed; union lane merging both with W5
- 2026-09-25 21:06Z UTC, coordinator: E4C-PREP merged dafd4030 after fix round + ACCEPT; R133 numbered
- 2026-09-25 19:59Z UTC, coordinator: E4C-PREP dispatched (base 56c284cf) and handed back at 1acf85b4; one-lens recheck running. D10-FOLLOWUP / M6-WIRING / W5-ADMIT-WIRING verify workflows running; union merge lane brief ready (trial merges: one trivial worker/service.py field conflict)
- 2026-09-25 19:59Z UTC, coordinator: ALERT-SNS merged c1b74038 after its fix round + one-lens ACCEPT; post-merge tests/i 32/1 xfail, mutants 50, rollout 8
- 2026-09-25 19:59Z UTC, coordinator: PROFILE-CREDIT-CAP merged 93fdab4e after a one-lens ACCEPT; ruling R132 numbered (W5 moves to R133–R136)
- 2026-09-25 19:59Z UTC, tracker: forecast E4: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25, P-26 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-24, P-25, P-26 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-24, P-25, P-26; no GPU window allocated for E1B, E4, E4C, I2A; no remaining-effort estimate for A2, A3, C0, C3A, E1B, E3A, E4, E4C, I2A, I3, U1R, U2, U3, U4)
- 2026-09-25 19:59Z UTC, tracker: forecast E3A: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25, P-26 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-24, P-25, P-26 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-24, P-25, P-26; no GPU window allocated for E1B, E4C; no remaining-effort estimate for A2, A3, C0, C3A, E1B, E3A, E4C, U1R, U2, U3, U4)
- 2026-09-25 19:59Z UTC, tracker: forecast E4C: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25, P-26 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-24, P-25, P-26 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-24, P-25, P-26; no GPU window allocated for E1B, E4C; no remaining-effort estimate for E1B, E4C)
- 2026-09-25 19:59Z UTC, tracker: rejected update: impossible transition complete → review: complete is terminal (the coordinator reopens by editing the overlay)
- 2026-09-25 19:59Z UTC, tracker: rejected update: impossible transition complete → review: complete is terminal (the coordinator reopens by editing the overlay)
- 2026-09-25 19:59Z UTC, tracker: rejected update: impossible transition complete → review: complete is terminal (the coordinator reopens by editing the overlay)
- 2026-09-25 19:00Z UTC, coordinator: inputs P-01..P-26 decided under the operator's authorization; P-22 closed; the rest decided with enactment pending
- 2026-09-25 18:12Z UTC, E2C: review → review; head 8a94a855; estimate likely 2 → 0.25 h (layer-3 rls green at head; only 0022 will need a re-baseline)
- 2026-09-25 18:22Z UTC, tracker: forecast E3C: 2026-09-26 01:22Z – 2026-09-26 20:52Z (likely 2026-09-26 07:52Z) → 2026-09-26 00:34Z – 2026-09-26 17:28Z (likely 2026-09-26 05:46Z) (because: dependency path W5 → E3C)
- 2026-09-25 18:20Z UTC, E3C: review → review; head 7d0d3d7b; estimate likely 4 → 2 h (one ~13-min final INFRX_E3C_RUNTIME_LOGIN=1 rerun after F-3 (relay -> admit_ready) + W5 and F-4 (journal pruning composed) land, plus nc-admission-ready and nc-retention-durable revert controls on scratch trees)
- 2026-09-25 17:52Z UTC, tracker: rejected update: impossible transition complete → review: complete is terminal (the coordinator reopens by editing the overlay)
- 2026-09-25 17:52Z UTC, tracker: rejected update: impossible transition complete → review: complete is terminal (the coordinator reopens by editing the overlay)
- 2026-09-25 17:34Z UTC, tracker: forecast E3C: 2026-09-26 09:31Z – 2026-09-27 11:31Z (likely 2026-09-26 18:37Z) → 2026-09-26 01:04Z – 2026-09-26 20:34Z (likely 2026-09-26 07:34Z) (because: dependency path W5 → E3C)
- 2026-09-25 17:34Z UTC, tracker: rejected update: impossible transition complete → review: complete is terminal (the coordinator reopens by editing the overlay)
- 2026-09-25 17:12Z UTC, M6: review → review; head dea32507; estimate likely 3 → 2 h (lane code complete; remaining is WR-1/2/3/7 composition outside the lane, the R114 ruling, and P-25)
- 2026-09-25 17:34Z UTC, tracker: rejected update: unknown task ID 'I8-M6-WIRING'
- 2026-09-25 07:59Z UTC, M6: review → review; head 5a027914; estimate likely 15 → 3 h (phases 1+2 implemented and green on D10 PostgreSQL; remaining = review round 1/2/3 h, composition proof after WR-1..3 0/1/2 h, coordinator box smoke 0/0/1 h)
- 2026-09-25 17:34Z UTC, tracker: rejected update: unknown task ID 'I8-M6-WIRING'
- 2026-09-25 16:55Z UTC, tracker: rejected update: unknown task ID 'RUNTIME-LOGIN'
- 2026-09-25 08:40Z UTC, tracker: rejected update: future-dated: at 2026-09-25T09:00:00Z is after host UTC now 2026-09-25T08:40:45Z (+15 min skew allowed); check the clock
- 2026-09-25 08:40Z UTC, tracker: rejected update: impossible transition complete → review: complete is terminal (the coordinator reopens by editing the overlay)
- 2026-09-25 08:30Z UTC, tracker: forecast E3C: 2026-09-26 02:23Z – 2026-09-27 08:17Z (likely 2026-09-26 12:47Z) → 2026-09-26 01:06Z – 2026-09-27 03:06Z (likely 2026-09-26 10:12Z) (because: dependency path M6 → E3C)
- 2026-09-25 07:46Z UTC, E3C: review → review; head 60ab7575; estimate likely 6 → 4 h (three ~13-min reruns after G7, W5 and M6 merge, re-point collect_once to M6's entry, run nc-admission-ready / nc-retention-durable revert controls; F-1 fix then INFRX_E3C_RUNTIME_LOGIN=1 rerun; F-2 needs a ruling)
- 2026-09-25 08:29Z UTC, G7: review → complete; head 2f7ed3ac; estimate likely 2 → 0 h (merged after the verify-lane fix round (ACCEPT_WITH_FIXES at 2f7ed3ac))
- 2026-09-25 08:19Z UTC, D10: review → complete; head 483d9eca; estimate likely 2 → 0 h (merged after the verify-lane fix round (ACCEPT_WITH_FIXES at 483d9eca); follow-ups are separate lanes)
- 2026-09-25 08:19Z UTC, tracker: forecast E3C: 2026-09-26 03:24Z – 2026-09-27 13:48Z (likely 2026-09-26 15:42Z) → 2026-09-26 02:13Z – 2026-09-27 08:07Z (likely 2026-09-26 12:37Z) (because: dependency path M6 → E3C)
- 2026-09-25 08:19Z UTC, M5: review → complete; head c0c4ce25; estimate likely 2 → 0 h (merged after the finish-verify fix round (ACCEPT_WITH_FIXES at c0c4ce25))
- 2026-09-25 08:18Z UTC, tracker: rejected update: lane D10 belongs to D10, not I8
- 2026-09-25 08:18Z UTC, tracker: forecast E3C: 2026-09-26 03:11Z – 2026-09-27 20:05Z (likely 2026-09-26 18:05Z) → 2026-09-26 03:24Z – 2026-09-27 13:48Z (likely 2026-09-26 15:42Z) (because: dependency path M5 → M6 → E3C)
- 2026-09-25 05:50Z UTC, D10: review → review; head ff62913d; estimate likely 3 → 2 h (all D lists green; left: coordinator merge, W5/G7-gated revoke migration, W5 fail_preparation)
- 2026-09-25 06:17Z UTC, tracker: forecast E4: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25, P-26 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25, P-26; no GPU window allocated for E1B, E4, E4C, I2A; no remaining-effort estimate for A2, A3, C0, C3A, E1B, E3A, E4, E4C, I2A, I3, U1R, U2, U3, U4)
- 2026-09-25 06:17Z UTC, tracker: forecast E3A: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25, P-26 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25, P-26; no GPU window allocated for E1B, E4C; no remaining-effort estimate for A2, A3, C0, C3A, E1B, E3A, E4C, U1R, U2, U3, U4)
- 2026-09-25 06:17Z UTC, tracker: forecast E4C: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25, P-26 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25, P-26; no GPU window allocated for E1B, E4C; no remaining-effort estimate for E1B, E4C)
- 2026-09-25 06:17Z UTC, tracker: rejected update: impossible transition complete → review: complete is terminal (the coordinator reopens by editing the overlay)
- 2026-09-25 04:40Z UTC, G7: review → review; head 8906eb26; estimate likely 3 → 2 h (code findings closed; merge gates (D10 WR-3c before G7; WR-1; WR-2 after WR-3a) and merged-tree full suite remain)
- 2026-09-25 03:03Z UTC, W5: review → review; head 97381015
- 2026-09-25 03:25Z UTC, E3C: review → review; head fecf91a6
- 2026-09-25 03:02Z UTC, E2C: review → review; head 26ef3fc3; estimate likely 3 → 2 h (one consumer-local rerun on this head (~65 min) + E2C-FR-1 + make api-test/api-mutants on pytest 9)
- 2026-09-25 03:04Z UTC, tracker: forecast E3C: 2026-09-26 02:56Z – 2026-09-28 03:38Z (likely 2026-09-26 20:26Z) → 2026-09-25 23:58Z – 2026-09-27 16:52Z (likely 2026-09-26 14:52Z) (because: dependency path D10 → M5 → M6 → E3C)
- 2026-09-25 03:02Z UTC, D10: running → review; head 1f12cb6a; estimate likely 4 → 3 h (all D lists, full mutant list and Supabase-image runs green; left: review fixes and W5 fail_preparation SQL once F2C/R104 rule the port)
- 2026-09-25 02:56Z UTC, tracker: forecast E3C: 2026-09-26 02:14Z – 2026-09-28 04:56Z (likely 2026-09-26 20:26Z) → 2026-09-26 02:56Z – 2026-09-28 03:38Z (likely 2026-09-26 20:26Z) (because: dependency path E2C → D10 → M5 → M6 → E3C)
- 2026-09-25 02:56Z UTC, tracker: rejected update: malformed: estimate at: Invalid isoformat string: '2026-09-25T01:54+00:00:00+00:00'
- 2026-09-25 01:50Z UTC, M5: review → review; head dfe0533a; estimate likely 3 → 2 h (both major code findings closed by dfe0533a with fails-before/passes-after and killed mutants; the lane's suites are green on the m5 harness except the one WR-2 G test; remaining: coordinator applies WR-1/WR-2 at the merge, resolves tasklocal.py/v2 exports, D10 media_refused alignment flips one xfail, minor findings for a later pass, one box smoke after composition)
- 2026-09-25 01:38Z UTC, tracker: forecast E3C: 2026-09-26 01:47Z – 2026-09-28 15:23Z (likely 2026-09-26 23:05Z) → 2026-09-26 02:14Z – 2026-09-28 04:56Z (likely 2026-09-26 20:26Z) (because: dependency path E2C → D10 → M5 → M6 → E3C)
- 2026-09-25 01:38Z UTC, I8: review → complete; head ce33c24c; estimate likely 11 → 0 h (merged after the verify-lane fix round (ACCEPT_WITH_FIXES at ce33c24c))
- 2026-09-25 01:01Z UTC, M6: review → review; head 188a7f01; estimate likely 16 → 15 h (phase 2 8/12/20 h unchanged; d10 integration after D10 merges 1/2/3 h (world exists, 27/27 port cases pass in a scratch merge); review 1/1/1 h)
- 2026-09-25 00:56Z UTC, tracker: forecast E4: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4, E4C, I2A; no remaining-effort estimate for A2, A3, C0, C3A, E1B, E3A, E4, E4C, I2A, I3, U1R, U2, U3, U4)
- 2026-09-25 00:56Z UTC, tracker: forecast E3A: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4C; no remaining-effort estimate for A2, A3, C0, C3A, E1B, E3A, E4C, U1R, U2, U3, U4)
- 2026-09-25 00:56Z UTC, tracker: forecast E4C: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4C; no remaining-effort estimate for E1B, E4C)
- 2026-09-25 00:56Z UTC, tracker: forecast E3C: unknown: no remaining-effort estimate for D10 → 2026-09-26 01:32Z – 2026-09-28 15:08Z (likely 2026-09-26 22:50Z) (because: dependency path E2C → D10 → M5 → W5 → I8 → E3C)
- 2026-09-25 00:53Z UTC, D10: running → running; head 31c2112c; estimate likely unknown → 4 h (code/SQL committed and green on plain PG; remaining: full mutant list, Supabase-image runs, probe on that image)
- 2026-09-25 00:02Z UTC, I8: review → review; head b55b3c5e; estimate likely 8 → 11 h (earlier 4/8/16 plus the replacement-instance restore path (image store decision, ECR copy, timed restore on a second instance, coordinator-run) and the P-24 canary decision)
- 2026-09-25 00:48Z UTC, G7: review → review; head a0e0928a
- 2026-09-25 00:46Z UTC, tracker: forecast E4: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4, E4C, I2A; no remaining-effort estimate for A2, A3, C0, C3A, D10, E1B, E3A, E4, E4C, I2A, I3, U1R, U2, U3, U4)
- 2026-09-25 00:46Z UTC, tracker: forecast E3A: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4C; no remaining-effort estimate for A2, A3, C0, C3A, D10, E1B, E3A, E4C, U1R, U2, U3, U4)
- 2026-09-25 00:46Z UTC, tracker: forecast E4C: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4C; no remaining-effort estimate for D10, E1B, E4C)
- 2026-09-25 00:46Z UTC, tracker: forecast E3C: unknown: no remaining-effort estimate for D10, W5 → unknown: no remaining-effort estimate for D10 (because: no remaining-effort estimate for D10)
- 2026-09-25 00:35Z UTC, W5: running → review; head 27afab76; estimate likely unknown → 6 h (lane code and local/PG proof done; left: apply wiring 1-2, F2C/D10 fail_preparation port+adapter, merged-tree PG round trip with G7 admit_ready, one box run of W5-box-warmup.sh)
- 2026-09-25 00:44Z UTC, G8: review → complete; head 09f4ab2a; estimate likely 4 → 0 h (merged after the verify-lane fix round (ACCEPT_WITH_FIXES at 09f4ab2a))
- 2026-09-25 00:17Z UTC, G8: review → review; head 44ea4770
- 2026-09-25 00:19Z UTC, E1C: integration → complete; head 6cb6c929; estimate likely 0.5 → 0 h (merged; start dependency F2C implemented)
- 2026-09-25 00:19Z UTC, F2C-L: review → complete; head c4873027; estimate likely 0.5 → 0 h (merged after the verify-lane fix round (ACCEPT_WITH_FIXES at c4873027))
- 2026-09-24 23:54Z UTC, F2C-L: review → review; head 16ce171a; estimate likely 1 → 0.5 h (every verification finding closed with a killed mutant or a column-level spec)
- 2026-09-25 00:19Z UTC, tracker: rejected update: stale: at 2026-09-24T22:34:29Z is not newer than lane F2C-L state 2026-09-24T23:00:20Z
- 2026-09-25 00:10Z UTC, E2C: review → review; head f61d2f0b
- 2026-09-25 00:08Z UTC, tracker: forecast E4: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4, E4C, I2A; no remaining-effort estimate for A2, A3, C0, C3A, D10, E1B, E3A, E4, E4C, I2A, I3, U1R, U2, U3, U4, W5)
- 2026-09-25 00:08Z UTC, tracker: forecast E3A: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4C; no remaining-effort estimate for A2, A3, C0, C3A, D10, E1B, E3A, E4C, U1R, U2, U3, U4, W5)
- 2026-09-25 00:08Z UTC, tracker: forecast E4C: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4C; no remaining-effort estimate for D10, E1B, E4C, W5)
- 2026-09-25 00:08Z UTC, tracker: forecast E3C: unknown: no remaining-effort estimate for D10, M5, W5 → unknown: no remaining-effort estimate for D10, W5 (because: no remaining-effort estimate for D10, W5)
- 2026-09-24 23:44Z UTC, M5: running → review; head 35c401bd; estimate likely unknown → 3 h (all four points green on D10's real adapter, MinIO and separate OS processes; remaining: two wiring patches, D10 media_refused alignment (one xfail flips), review fixes, one box smoke)
- 2026-09-25 00:07Z UTC, tracker: forecast E4: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4, E4C, I2A; no remaining-effort estimate for A2, A3, C0, C3A, D10, E1B, E3A, E4, E4C, I2A, I3, M5, U1R, U2, U3, U4, W5)
- 2026-09-25 00:07Z UTC, tracker: forecast E3A: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4C; no remaining-effort estimate for A2, A3, C0, C3A, D10, E1B, E3A, E4C, M5, U1R, U2, U3, U4, W5)
- 2026-09-25 00:07Z UTC, tracker: forecast E4C: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 → blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25 (because: blocked pending P-01, P-02, P-05, P-06, P-17, P-18, P-19, P-22, P-24, P-25; no GPU window allocated for E1B, E4C; no remaining-effort estimate for D10, E1B, E4C, M5, W5)
- 2026-09-25 00:07Z UTC, tracker: forecast E3C: unknown: no remaining-effort estimate for D10, E2C, G7, M5, W5 → unknown: no remaining-effort estimate for D10, M5, W5 (because: no remaining-effort estimate for D10, M5, W5)
- 2026-09-25 00:05Z UTC, G7: running → review; head 2bbfe0e3; estimate likely unknown → 3 h (all points implemented and green; left: review fixes, rerun on M5/D10 adapters at merge, WR-1 default decision fallout)
- 2026-09-25 00:04Z UTC, E2C: running → review; head 3a113dc9; estimate likely unknown → 3 h (after coordinator wiring 1-4 (Makefile, tasklocal decoy fix, pytest bump, port-reservation sysctl), rerun make consumer-local with S3 + E2 layer 2 provided to reach PASS or name the next blocker)
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
- 2026-09-26 00:33Z UTC, coordinator: I2A-PREP merged a5ca1cd1 + wiring 416da075 (ACCEPT_WITH_FIXES; I2A-R1..R3/F1 fixed, minors carried; coordinator ratified instrumentation.ts, app/api/version/route.ts and .env.example as I2A scope); R134 numbered; W5 rulings move to R135–R138

## History

The v46 backend-first tracker is preserved at [research/plan/evidence/coordinator/tracker-v46/README.md](tracker-v46/README.md) (commit `dff31efc`): E4B closure bands B0–B4: 28 done · 2 in progress (E1B, E4B) · 0 remaining of 30 packages; 33 checkpoints 2026-09-21T22:44Z → 2026-09-24T21:00Z.
