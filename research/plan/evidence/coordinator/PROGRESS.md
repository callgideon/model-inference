# Backend-first progress tracker

Generated 2026-09-23T04:00:07Z from `tasks.json` (manifest v4) and `progress-state.json`. Integration branch `claude/backend-impl`, base `ec6c548`. Scope: the E4B backend closure (37 tasks, of which 7 foundations and wave-2 modules are already merged and reused: E1, F1, F2, I1, M1, Q1, W1).

**Backend packages: 15 done · 8 in progress · 7 remaining (of 30).**

| Band | Task | Title | Status | Manifest | Note |
|---|---|---|---|---|---|
| B0 Baseline & contracts | S1 | Reconcile pulled wave-2 baseline and publish product revision audit | **done** | implemented |  |
| B0 Baseline & contracts | F2R | Close remaining wave-2 contract and verification carryovers | **done** | implemented |  |
| B0 Baseline & contracts | I0 | Repair installer atomicity and fail-closed startup prerequisite | **done** | implemented |  |
| B0 Baseline & contracts | E2R | Repair service harness ownership, role matrix and shared test clock | **done** | implemented |  |
| B0 Baseline & contracts | S2M | Freeze Marlin SOP inference launch profile | **done** | implemented |  |
| B0 Baseline & contracts | F2P | Encode product-v2 CREDIT, identity, serving and permission contracts | **in-progress** | planned | v2 CREDIT/USD units, audiences, admission pins, grant, provider grants — fixtures + map |
| B1 Durable endpoint | D1R | Add product-v2 schema without rewriting USD pilot migrations | **done** | implemented |  |
| B1 Durable endpoint | D2 | Atomic admission, durable preparation and dispatch outbox | **in-progress** | planned | real JobStore over PostgreSQL |
| B1 Durable endpoint | D3 | Fenced leases, recovery and cancellation | **in-progress** | planned | stacked on D2's head; leases, reaper, cancellation |
| B1 Durable endpoint | D4 | Persistent stream journal and replay | **remaining** | planned |  |
| B1 Durable endpoint | D5 | Terminal transaction, grants and reconciliation | **remaining** | planned |  |
| B1 Durable endpoint | A1 | Verified individual signup entitlement and idempotent backfill | **in-progress** | planned | new files only; migrations 0015+; D2 in flight |
| B1 Durable endpoint | M2 | Versioned preprocessing and tenant cache | **done** | implemented |  |
| B1 Durable endpoint | M3 | Owned uploads, expiry and orphan collection | **done** | implemented |  |
| B1 Durable endpoint | Q2 | Valkey adapter with atomic tested scripts | **done** | implemented |  |
| B1 Durable endpoint | Q3 | Outbox/reconciler integration and index loss recovery | **done** | implemented |  |
| B1 Durable endpoint | W2 | Lease-aware execution, cancellation and completion | **done** | implemented |  |
| B1 Durable endpoint | W3 | Drain, engine pin and measured concurrency | **in-progress** | planned | engine pin by digest, drain, readiness; measurements coordinator-run |
| B1 Durable endpoint | G1R | Revise ingress for consumer and provider endpoint audiences | **done** | implemented |  |
| B1 Durable endpoint | G2 | Synchronous chat and persistent SSE relay | **remaining** | planned |  |
| B1 Durable endpoint | G3 | Explicit jobs, status, cancellation and replay | **remaining** | planned |  |
| B1 Durable endpoint | G4U | Owned upload HTTP adapter | **remaining** | planned |  |
| B1 Durable endpoint | G6B | Headless endpoint provisioning and operations | **done** | implemented |  |
| B2 Integrate & deploy | E3B | Backend-only durability, security and protocol integration gate | **in-progress** | planned | gate not passable until G/D/W/Q lanes merge |
| B2 Integrate & deploy | I2B | Reproducible Marlin endpoint deployment independent of frontends | **in-progress** | planned | packaging, scripts, local rehearsal; box rollout coordinator-run |
| B2 Integrate & deploy | I3B | Backend recovery, observability, restore and rollback proof | **done** | implemented | needs allocated GPU/staging (P-04) |
| B2 Integrate & deploy | E1B | Measure the end-to-end Marlin baseline and operating envelope | **in-progress** | planned | sop-synth-v1 generator, bench idempotency/resume, open-loop driver, predeclared protocol | resumed from WIP after restart |
| B3 Measured tuning | M4 | Optimize bounded video retrieval, decoding and preparation | **done** | implemented | needs allocated GPU/staging (P-04) |
| B3 Measured tuning | W4 | Tune Marlin GPU serving and scheduler admission from measured evidence | **remaining** | planned | needs allocated GPU/staging (P-04) |
| B4 Endpoint gate | E4B | Certify the robust and measured Marlin endpoint release candidate | **remaining** | planned | needs allocated GPU/staging (P-04) |

## Gates

- **BACKEND-LOCAL** (requires E3B): not started
- **BACKEND-READY** (requires E4B): not started — blocked by: P-18 workload targets (provisional criteria allowed)

## Inputs that block specific gates (not the coding)

| Input | What | Blocks | Owner |
|---|---|---|---|
| P-04 | GPU target RESOLVED: pilot box i-0e8449a4ffca29bab (L40S), snapshot taken; I2B redeploys the refactored app onto it | nothing | coordinator |
| P-06 | Marlin artifact/capabilities — RESOLVED AS A PROFILE (S2M): fd111fca, Qwen3-VL processor, profile v1; 4 digests ⚠️ need HF_TOKEN + pinned image | publishing externally until the three fail-stops (D2/D3/D12) close | M2/W/G2 (fixes); W3 (digests) |
| P-07 | SOP rubric, ground truth, dataset rights — RECORDED (10 missing inputs; no accuracy baseline exists) | SOP accuracy claims only | user/product |
| P-18 | Workload + criteria — PROVISIONAL criteria predeclared by S2M (labelled); latency/availability targets deliberately absent | E4B certification; provisional criteria allowed | workload owner |
| P-01 | Approved CREDIT rate card (P-02 legacy transition RESOLVED: $0 legacy USD, nothing to migrate) | public metered publication only; a provisional fixture rate is used meanwhile | user/operator |
| P-19 (new) | Sourced AWS g6e.2xlarge / single-L40S price row in research/cross-cutting/cloud-pricing.md | publishing any cost-per-video-hour figure (S2M §5.2, E1B protocol) | pricing owner / user |
| P-04-sweep | W3 concurrency sweep on the box (E1B cell L1): needs a checkout with bench.py + a ~12 min corpus build on the box; held per user instruction (no new work). ENGINE_MAX_NUM_SEQS stays 32 (the box's measured value) until then. | W3 measured ENGINE_MAX_NUM_SEQS; serving-version.json settings_status | coordinator (box op) |

## ETA (provisional, cadence-based — not a commitment)

- Observed cadence: 11 tasks integrated in 15.7 h of wall clock (0.70 tasks/h at 4–6 concurrent lanes, each task 2–4 review rounds), incl. two rate-limit interruptions.
- Local software to BACKEND-LOCAL/E3B and the software half of the rest (11 packages): ~16 h at observed cadence, ~1.3 days if wave-3 packages run at half that rate (they are larger and the D lane is serial); the serial critical path alone (D1R→D2→D3→D4→D5→E3B) is at least ~22 h.
- GPU-gated packages (I2B, E1B, W4, E4B): **no ETA until P-04 is allocated**; their software (harnesses, scripts, runbooks) proceeds inside the local estimate.
- Continuous coordinator time is assumed; interruptions (rate limits, restarts) extend wall clock, not work.

## In flight

- F2P: codex-f2p — wire-in fix round IN PROGRESS when the session ended (agent killed; worktree WIP-snapshotted); re-dispatch from evidence/f/F2P-wirein-review-e307084.json since 2026-09-22T16:00:21Z — v2 CREDIT/USD units, audiences, admission pins, grant, provider grants — fixtures + map
- E1B: codex-e1b — software slices MERGED (164e43e); GPU measurement slices pending W3 → I2B since 2026-09-22T16:03:43Z — sop-synth-v1 generator, bench idempotency/resume, open-loop driver, predeclared protocol | resumed from WIP after restart
- E3B: codex-e3b / codex/e3b-backend-gate — phase 1 MERGED (c7d715f); gate exit 3 with 22 pending; phase 2 after G/D/W/Q lanes since 2026-09-22T18:46:49Z — gate not passable until G/D/W/Q lanes merge
- D2: codex-d2 / codex/d2-admission-outbox — round 3 handed back 2e386f0 (all four items; sweeps plain green, Supabase failures = shared decoy port 55598 self-tests); quick check then MERGE next session since 2026-09-22T20:55:35Z — real JobStore over PostgreSQL
- A1: codex-a1 / codex/a1-signup-grant — round 4 needed: RV3-1 delete/move race case + mutant (or narrow docstring); RV3-2/RV3-3 wording — then merge after D2 (JSON evidence/a/A1-confirm-dc3cb9c.json) since 2026-09-22T22:35:57Z — new files only; migrations 0015+; D2 in flight
- W3: codex-w3 / codex/w3-drain-pin — merge-ready at c81ef36 (measured box values written); merges with I2B's deploy/ after I2B round 2 since 2026-09-22T22:37:58Z — engine pin by digest, drain, readiness; measurements coordinator-run
- I2B: codex-i2b / codex/i2b-deployment — round 2 handed back d620f14 (RB-1/RB-2 closed); needs an Opus confirmation, then merge with W3 since 2026-09-22T22:37:58Z — packaging, scripts, local rehearsal; box rollout coordinator-run
- D3: codex-d3 / codex/d3-fenced-leases — fix round (FE-1/MY-1 CREDIT held_unknown path untested) + merge of D2's head after review of b4b1ec7 since 2026-09-22T22:40:37Z — stacked on D2's head; leases, reaper, cancellation

## Checkpoints

- 2026-09-21T22:44Z: Wave 2 complete: 11 tasks merged, S2 pass, make check exit 0; main fast-forwarded to 271add9
- 2026-09-22T15:51:21Z: Wave 3 (backend-first) started from ec6c548; B0 lanes F2R/I0/E2R/S2M dispatched; audit-code review launched
- 2026-09-22T15:55:30Z: Baseline make check exit 0 on ec6c548 (api-test + all mutant lists, console 255, bench 40); main fast-forwarded to claude/backend-impl and pushed (first checkpoint under full authorization)
- 2026-09-22T16:00:21Z: Max-parallel decision: F2R split into two lanes; F2P additive phase started; Q2/M2/W2 started early against v1 fakes (rebase after F2R). Interim rule: only E2R runs tests/d until the harness namespace fix lands.
- 2026-09-22T16:07:02Z: Live-state inventory: P-02 resolved ($0 legacy USD), P-04 resolved (pilot box), hosted auth.uid() reads both claim forms; EBS snapshot snap-08732d3ac6376e850 taken and DeleteOnTermination disabled
- 2026-09-22T16:12:51Z: E2R item 1 (D harness ownership + port lock) merged as c23d804; tests/d 76 passed on merged tree; interim tests/d exclusion lifted
- 2026-09-22T16:30:52Z: S2M merged after independent review + fix round; R62 recorded; served-bytes digests measured on pilot box
- 2026-09-22T16:36:52Z: S1 independent review: API/contract side pass; console B1/B2 fix lane dispatched; integration layer 2 recorded RED (E2R owns)
- 2026-09-22T17:29:34Z: S1-fix merged (6669e1f): console balance unavailable state, build-time preview gate; console targets green on merged tree
- 2026-09-22T17:45:34Z: Restart on rey account; M2 (8156f78) and F2R-B (2bfb0c4) merged; six implementer lanes resumed from WIP; F2P review resumed
- 2026-09-22T17:51:45Z: E1B software slices merged (164e43e): bench client, sop-synth-v1 (120 s worst case), predeclared protocol
- 2026-09-22T17:57:28Z: I0 merged (0ac073e): fail-closed installer; Makefile mutant list extended
- 2026-09-22T18:05:08Z: F2P additive phase merged (9faaa57); rulings R64–R78; D1R dispatched
- 2026-09-22T18:06:47Z: Q2 merged (e86b1c0): Valkey scheduler adapter with race-fence test
- 2026-09-22T18:13:15Z: Checkpoint 2: main ff to 2bfb0c4 after a full green check (api-mutants 1103, api 1729+76, console 266)
- 2026-09-22T18:45:43Z: E2R merged (9cc3be1): role matrix on real services, shared clock, layer-2 green with canary
- 2026-09-22T18:49:10Z: W2 merged (32cc0cb): worker attempt loop + vLLM adapter with pilot media form
- 2026-09-22T19:04:25Z: Checkpoint 3: main ff to the integration head (E2R, W2 merged); composed evidence recorded
- 2026-09-22T19:16:15Z: M3 merged (e2188f3): owned uploads, collector, consented reuse; M→W seam tests wired to the resolver
- 2026-09-22T19:36:54Z: G6B merged (ed6da07): operator adapter/CLI, protected publication, headless client
- 2026-09-22T20:55:35Z: D1R merged: migrations 0006–0009 (CREDIT, registry, read surface, operator seams); D2 dispatched
- 2026-09-22T21:18:49Z: E3B phase 1 merged (c7d715f); integration engine client on {visible, raw}; migration set 0001–0009
- 2026-09-22T22:14:13Z: F2R-A follow-ups merged (5f7ca02): tree collects (2485 tests); F2R implemented; session paused at the 85% window mark
- 2026-09-23T00:46:03Z: Resumed on sofia; ten lanes resumed from pushed heads (transcripts intact)

## Authorizations

- authorized: merge to main at reviewed green checkpoints (user, 2026-09-22)
- authorized: FULL operational authorization (user, 2026-09-22): hosted Supabase migration apply, public cutover, paid provider calls, compute purchases, any operations — logged with cost and rollback before each irreversible step
- authorized: 2026-09-23: finish in-flight lanes only; no new task dispatch (user instruction, session limits)
- NOT authorized: nothing withheld by the user; coordinator rules: log-before-act, bounded spend, backup/restore before hosted migration, fail-closed installer before cutover
