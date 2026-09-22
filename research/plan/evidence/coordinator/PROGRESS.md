# Backend-first progress tracker

Generated 2026-09-22T16:30:52Z from `tasks.json` (manifest v4) and `progress-state.json`. Integration branch `claude/backend-impl`, base `ec6c548`. Scope: the E4B backend closure (37 tasks, of which 7 foundations and wave-2 modules are already merged and reused: E1, F1, F2, I1, M1, Q1, W1).

**Backend packages: 2 done · 8 in progress · 20 remaining (of 30).**

| Band | Task | Title | Status | Manifest | Note |
|---|---|---|---|---|---|
| B0 Baseline & contracts | S1 | Reconcile pulled wave-2 baseline and publish product revision audit | **done** | implemented |  |
| B0 Baseline & contracts | F2R | Close remaining wave-2 contract and verification carryovers | **in-progress** | planned | lane A: shared runner (item 9) then items 2–5; lane B (codex-f2r-b): TS console contracts (6) + config names (7) |
| B0 Baseline & contracts | I0 | Repair installer atomicity and fail-closed startup prerequisite | **in-progress** | planned | fail-closed installer; stubs only |
| B0 Baseline & contracts | E2R | Repair service harness ownership, role matrix and shared test clock | **in-progress** | planned | harness ownership + five RLS inversions on real services |
| B0 Baseline & contracts | S2M | Freeze Marlin SOP inference launch profile | **done** | implemented |  |
| B0 Baseline & contracts | F2P | Encode product-v2 CREDIT, identity, serving and permission contracts | **in-progress** | planned | v2 CREDIT/USD units, audiences, admission pins, grant, provider grants — fixtures + map |
| B1 Durable endpoint | D1R | Add product-v2 schema without rewriting USD pilot migrations | **remaining** | planned |  |
| B1 Durable endpoint | D2 | Atomic admission, durable preparation and dispatch outbox | **remaining** | planned |  |
| B1 Durable endpoint | D3 | Fenced leases, recovery and cancellation | **remaining** | planned |  |
| B1 Durable endpoint | D4 | Persistent stream journal and replay | **remaining** | planned |  |
| B1 Durable endpoint | D5 | Terminal transaction, grants and reconciliation | **remaining** | planned |  |
| B1 Durable endpoint | A1 | Verified individual signup entitlement and idempotent backfill | **remaining** | planned |  |
| B1 Durable endpoint | M2 | Versioned preprocessing and tenant cache | **in-progress** | planned | prepare(): probe real duration, url→ref rewrite, processing cache; rebases on F2R |
| B1 Durable endpoint | M3 | Owned uploads, expiry and orphan collection | **remaining** | planned |  |
| B1 Durable endpoint | Q2 | Valkey adapter with atomic tested scripts | **in-progress** | planned | Valkey adapter reproducing the 14-point list; differential vs memory |
| B1 Durable endpoint | Q3 | Outbox/reconciler integration and index loss recovery | **remaining** | planned |  |
| B1 Durable endpoint | W2 | Lease-aware execution, cancellation and completion | **in-progress** | planned | attempt loop: fenced claim/heartbeat/journal-before-relay/settle; rebases on F2R |
| B1 Durable endpoint | W3 | Drain, engine pin and measured concurrency | **remaining** | planned |  |
| B1 Durable endpoint | G1R | Revise ingress for consumer and provider endpoint audiences | **remaining** | planned |  |
| B1 Durable endpoint | G2 | Synchronous chat and persistent SSE relay | **remaining** | planned |  |
| B1 Durable endpoint | G3 | Explicit jobs, status, cancellation and replay | **remaining** | planned |  |
| B1 Durable endpoint | G4U | Owned upload HTTP adapter | **remaining** | planned |  |
| B1 Durable endpoint | G6B | Headless endpoint provisioning and operations | **remaining** | planned |  |
| B2 Integrate & deploy | E3B | Backend-only durability, security and protocol integration gate | **remaining** | planned |  |
| B2 Integrate & deploy | I2B | Reproducible Marlin endpoint deployment independent of frontends | **remaining** | planned | needs allocated GPU/staging (P-04) |
| B2 Integrate & deploy | I3B | Backend recovery, observability, restore and rollback proof | **remaining** | planned | needs allocated GPU/staging (P-04) |
| B2 Integrate & deploy | E1B | Measure the end-to-end Marlin baseline and operating envelope | **in-progress** | planned | sop-synth-v1 generator, bench idempotency/resume, open-loop driver, predeclared protocol |
| B3 Measured tuning | M4 | Optimize bounded video retrieval, decoding and preparation | **remaining** | planned | needs allocated GPU/staging (P-04) |
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

## ETA (provisional, cadence-based — not a commitment)

- Observed cadence: 11 tasks integrated in 15.7 h of wall clock (0.70 tasks/h at 4–6 concurrent lanes, each task 2–4 review rounds), incl. two rate-limit interruptions.
- Local software to BACKEND-LOCAL/E3B and the software half of the rest (22 packages): ~1.3 days at observed cadence, ~2.6 days if wave-3 packages run at half that rate (they are larger and the D lane is serial); the serial critical path alone (D1R→D2→D3→D4→D5→E3B) is at least ~22 h.
- GPU-gated packages (I2B, I3B, E1B, M4, W4, E4B): **no ETA until P-04 is allocated**; their software (harnesses, scripts, runbooks) proceeds inside the local estimate.
- Continuous coordinator time is assumed; interruptions (rate limits, restarts) extend wall clock, not work.

## In flight

- F2R: codex-f2r — implementing since 2026-09-22T15:51:21Z — lane A: shared runner (item 9) then items 2–5; lane B (codex-f2r-b): TS console contracts (6) + config names (7)
- I0: codex-i0 — implementing since 2026-09-22T15:51:21Z — fail-closed installer; stubs only
- E2R: codex-e2r — implementing (item 1 merged c23d804; items 2–4 in flight) since 2026-09-22T15:51:21Z — harness ownership + five RLS inversions on real services
- F2P: codex-f2p — additive design phase (new files only); wire-in after F2R since 2026-09-22T16:00:21Z — v2 CREDIT/USD units, audiences, admission pins, grant, provider grants — fixtures + map
- Q2: codex-q2 — implementing since 2026-09-22T16:00:21Z — Valkey adapter reproducing the 14-point list; differential vs memory
- M2: codex-m2 — implementing since 2026-09-22T16:00:21Z — prepare(): probe real duration, url→ref rewrite, processing cache; rebases on F2R
- W2: codex-w2 — implementing since 2026-09-22T16:00:21Z — attempt loop: fenced claim/heartbeat/journal-before-relay/settle; rebases on F2R
- E1B: codex-e1b — implementing (software slices; GPU slices wait for W3/I2B) since 2026-09-22T16:03:43Z — sop-synth-v1 generator, bench idempotency/resume, open-loop driver, predeclared protocol
- review S1: independent review of the audit's code at ec6c548 since 2026-09-22T15:51:21Z

## Checkpoints

- 2026-09-21T22:44Z: Wave 2 complete: 11 tasks merged, S2 pass, make check exit 0; main fast-forwarded to 271add9
- 2026-09-22T15:51:21Z: Wave 3 (backend-first) started from ec6c548; B0 lanes F2R/I0/E2R/S2M dispatched; audit-code review launched
- 2026-09-22T15:55:30Z: Baseline make check exit 0 on ec6c548 (api-test + all mutant lists, console 255, bench 40); main fast-forwarded to claude/backend-impl and pushed (first checkpoint under full authorization)
- 2026-09-22T16:00:21Z: Max-parallel decision: F2R split into two lanes; F2P additive phase started; Q2/M2/W2 started early against v1 fakes (rebase after F2R). Interim rule: only E2R runs tests/d until the harness namespace fix lands.
- 2026-09-22T16:07:02Z: Live-state inventory: P-02 resolved ($0 legacy USD), P-04 resolved (pilot box), hosted auth.uid() reads both claim forms; EBS snapshot snap-08732d3ac6376e850 taken and DeleteOnTermination disabled
- 2026-09-22T16:12:51Z: E2R item 1 (D harness ownership + port lock) merged as c23d804; tests/d 76 passed on merged tree; interim tests/d exclusion lifted
- 2026-09-22T16:30:52Z: S2M merged after independent review + fix round; R62 recorded; served-bytes digests measured on pilot box

## Authorizations

- authorized: merge to main at reviewed green checkpoints (user, 2026-09-22)
- authorized: FULL operational authorization (user, 2026-09-22): hosted Supabase migration apply, public cutover, paid provider calls, compute purchases, any operations — logged with cost and rollback before each irreversible step
- NOT authorized: nothing withheld by the user; coordinator rules: log-before-act, bounded spend, backup/restore before hosted migration, fail-closed installer before cutover
