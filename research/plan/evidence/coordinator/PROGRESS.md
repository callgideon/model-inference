# Backend-first progress tracker

Generated 2026-09-24T05:45:02Z from `tasks.json` (manifest v4) and `progress-state.json`. Integration branch `claude/backend-impl`, base `ec6c548`. Scope: the E4B backend closure (37 tasks, of which 7 foundations and wave-2 modules are already merged and reused: E1, F1, F2, I1, M1, Q1, W1).

**Backend packages: 28 done · 2 in progress · 0 remaining (of 30).**

| Band | Task | Title | Status | Manifest | Note |
|---|---|---|---|---|---|
| B0 Baseline & contracts | S1 | Reconcile pulled wave-2 baseline and publish product revision audit | **done** | implemented |  |
| B0 Baseline & contracts | F2R | Close remaining wave-2 contract and verification carryovers | **done** | implemented |  |
| B0 Baseline & contracts | I0 | Repair installer atomicity and fail-closed startup prerequisite | **done** | implemented |  |
| B0 Baseline & contracts | E2R | Repair service harness ownership, role matrix and shared test clock | **done** | implemented |  |
| B0 Baseline & contracts | S2M | Freeze Marlin SOP inference launch profile | **done** | implemented |  |
| B0 Baseline & contracts | F2P | Encode product-v2 CREDIT, identity, serving and permission contracts | **done** | implemented |  |
| B1 Durable endpoint | D1R | Add product-v2 schema without rewriting USD pilot migrations | **done** | implemented |  |
| B1 Durable endpoint | D2 | Atomic admission, durable preparation and dispatch outbox | **done** | implemented |  |
| B1 Durable endpoint | D3 | Fenced leases, recovery and cancellation | **done** | implemented |  |
| B1 Durable endpoint | D4 | Persistent stream journal and replay | **done** | implemented |  |
| B1 Durable endpoint | D5 | Terminal transaction, grants and reconciliation | **done** | implemented |  |
| B1 Durable endpoint | A1 | Verified individual signup entitlement and idempotent backfill | **done** | implemented |  |
| B1 Durable endpoint | M2 | Versioned preprocessing and tenant cache | **done** | implemented |  |
| B1 Durable endpoint | M3 | Owned uploads, expiry and orphan collection | **done** | implemented |  |
| B1 Durable endpoint | Q2 | Valkey adapter with atomic tested scripts | **done** | implemented |  |
| B1 Durable endpoint | Q3 | Outbox/reconciler integration and index loss recovery | **done** | implemented |  |
| B1 Durable endpoint | W2 | Lease-aware execution, cancellation and completion | **done** | implemented |  |
| B1 Durable endpoint | W3 | Drain, engine pin and measured concurrency | **done** | implemented |  |
| B1 Durable endpoint | G1R | Revise ingress for consumer and provider endpoint audiences | **done** | implemented |  |
| B1 Durable endpoint | G2 | Synchronous chat and persistent SSE relay | **done** | implemented |  |
| B1 Durable endpoint | G3 | Explicit jobs, status, cancellation and replay | **done** | implemented |  |
| B1 Durable endpoint | G4U | Owned upload HTTP adapter | **done** | implemented |  |
| B1 Durable endpoint | G6B | Headless endpoint provisioning and operations | **done** | implemented |  |
| B2 Integrate & deploy | E3B | Backend-only durability, security and protocol integration gate | **done** | implemented |  |
| B2 Integrate & deploy | I2B | Reproducible Marlin endpoint deployment independent of frontends | **done** | implemented | needs allocated GPU/staging (P-04) |
| B2 Integrate & deploy | I3B | Backend recovery, observability, restore and rollback proof | **done** | implemented | needs allocated GPU/staging (P-04) |
| B2 Integrate & deploy | E1B | Measure the end-to-end Marlin baseline and operating envelope | **in-progress** | planned | sop-synth-v1 generator, bench idempotency/resume, open-loop driver, predeclared protocol | resumed from WIP after restart |
| B3 Measured tuning | M4 | Optimize bounded video retrieval, decoding and preparation | **done** | implemented | needs allocated GPU/staging (P-04) |
| B3 Measured tuning | W4 | Tune Marlin GPU serving and scheduler admission from measured evidence | **done** | implemented | needs allocated GPU/staging (P-04) |
| B4 Endpoint gate | E4B | Certify the robust and measured Marlin endpoint release candidate | **in-progress** | implemented | namespace e2, d2/55466, Q 55493 |

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
| P-04-sweep | W3 concurrency sweep DONE (2026-09-23T05:04Z, sweep-20260923T050411Z): F(c) never 0 because the engine's 16384-token encoder cache rejects the four 112 s clips at every level; no level qualifies under the predeclared rule; c=16 would qualify with those set aside | ENGINE_MAX_NUM_SEQS / WORKER_CONCURRENCY (stay 8 ⚠️ / cutover keeps 32 explicitly); W4's first input | W4 (tuning) / W3 (record) |
| P-20 | The engine rejects videos above ~72 s (encoder cache budget 16384 tokens) while profile v1 admits up to 120 s: decide raise the budget (W4, re-measure) or cap admission (G2 validation) — until then the pilot admits what the engine cannot serve | E4B certification; G2's validation ceiling | W4 decides; G2 applies |
| P-21 | Host sysctl net.ipv4.ip_local_reserved_ports for the task-port blocks (55432-55499, 56700-56799) — a root change on this host (persist in /etc/sysctl.d); prevents ephemeral-port bind collisions between lanes | nothing (lanes retry); reduces HarnessBusy/bind noise | user (root approval); coordinator applies |

## ETA (provisional, cadence-based — not a commitment)

- Observed cadence: 11 tasks integrated in 15.7 h of wall clock (0.70 tasks/h at 4–6 concurrent lanes, each task 2–4 review rounds), incl. two rate-limit interruptions.
- Local software to BACKEND-LOCAL/E3B and the software half of the rest (0 packages): ~0 h at observed cadence, ~0 h if wave-3 packages run at half that rate (they are larger and the D lane is serial); the serial critical path alone (D1R→D2→D3→D4→D5→E3B) is at least ~22 h.
- GPU-gated packages (E1B, E4B): **no ETA until P-04 is allocated**; their software (harnesses, scripts, runbooks) proceeds inside the local estimate.
- Continuous coordinator time is assumed; interruptions (rate limits, restarts) extend wall clock, not work.

## In flight

- E1B: codex-e1b — engine cells L0/L1/L8 MEASURED on the box (merged 0117d48); L2–L7 after the rollout (phase 2) since 2026-09-22T16:03:43Z — sop-synth-v1 generator, bench idempotency/resume, open-loop driver, predeclared protocol | resumed from WIP after restart
- E4B: codex-e4b / codex/e4b-certify — software half MERGED; PILOT RELEASE 27af05a DEPLOYED on the box 05:43Z (metered gateway live behind the edge; hosted 0003-0018 applied; operator seed applied); box half (smoke, E1B L2-L7, certify --box) waits for the preparation loop since 2026-09-23T19:29:02Z — namespace e2, d2/55466, Q 55493
- PREP-WORKER: codex-prepw / codex/prep-worker — early review PASS at c035ee4 (wf_6bad9e38-f8b; all nine journeys on the real worker, backend 188/0/0; 10 nonblocking/downgraded items sent to the lane to fold in) → handback → targeted verifier → merge + focused gate since 2026-09-24T02:13:25Z — BLOCKS THE PILOT: no product process prepares a job today; ports d5/55498, MinIO 55781, namespace e4b
- review W4: MERGED f36c17c after the round-4 verifier pass; merged-tree tests/w 180 passed (w4-merged-f36c17c.log) since 2026-09-23T14:18:55Z
- review G4U: MERGED 7d21fa7 after confirmation pass at 962b2b1 since 2026-09-23T10:33:10Z
- review G2: MERGED 2391d4d; merged-tree checks green on d4: contracts 1053, D conformance (after the RAISES fix f52308a) 47/26xf/1xp, tests/g+m 859, G list 306 since 2026-09-23T15:38:21Z
- review E3B2: MERGED d1d4a68 (+ d588c3e IR2-1, 0a642ad IR2-3); merged-tree checks: test_run+test_stage 61, layer 0 146 passed with only I3B's two reds (closed by the I3B merge), test_harness+contracts 1036, dr03/dr04 fake 2 since 2026-09-23T18:16:49Z
- review D4: MERGED 93ba108 after the round-3 verifier pass (evidence/d/D4-verify-90efcb0.json) since 2026-09-23T12:47:20Z
- review G3: MERGED b560b51 (+ Makefile 31bfd05); merged-tree checks green: contracts 1014, tests/g 559, G3 list 85, G list 306 since 2026-09-23T16:04:40Z
- review I3B-followup: MERGED 90b81c7 (+ 08725c0 IR2-2); merged-tree checks green: layer 0 exit 0, recovery 62/9, I3B list 109/107, tests/i 143 since 2026-09-23T18:25:08Z
- review F-fakes-followup: MERGED a2779d1 (+ G3 seam fix 51f6c6e); merged-tree checks green on both images: contracts 1056, D conformance 69/31/1 ×2, code_mutants_d4 17/17, tests/g 500 + G3 list 85, tests/w 180, contracts list 445 since 2026-09-23T16:27:29Z
- review checkpoint-2-interim: whole-tree gate running on 602b1e0 (all merges so far, D5 out): api-test, layer 0, bench, console ×4, api-mutants on d4/Q 55489 (.claude-logs/gate-interim-602b1e0.log) since 2026-09-23T18:41:52Z
- review D5: MERGED 5b7fe69 (f142074) in the merge unit at 92ea6c0 since 2026-09-24T00:59:03Z
- review M1-L2: MERGED 21fe8fc (ba26ca4); R95 since 2026-09-24T00:59:03Z
- review E4B: software half MERGED 66eed31 (4d9360c) + 4 coordinator edits; R96/R97; box half + release decision after the rollout since 2026-09-24T00:59:03Z
- review W4-phaseB: MEASURED on the box: E0/E1/E3 run and restored; decide.py: no setting adopted → B at 82 s; P-20 decided; evidence/w/W4-phaseB-20260923T2155Z.md since 2026-09-23T22:02:17Z
- review E3B-phase3: MERGED b657a30 (b9529d1) with D5; IR3F-2(b) applied 7b100f2 since 2026-09-24T00:59:03Z
- review merge-plan: MERGED 58e6fdb: scripts 1–9 + replay; gating unit D5→phase 3→cutover→M1-L2 on all four verdicts; then M pilot → E4B → checkpoint 2 (api-test whole, layer 0, bench, affected lists, one layer-3 run) → ff main since 2026-09-23T22:22:23Z
- review M-pilot-media: MERGED 4fd4cdc (e5c02f7); R99 since 2026-09-24T00:59:03Z
- review CUTOVER: MERGED: code daeff13 + fix round 865d352 (verifier pass at 141e169; JSON evidence/g/CUTOVER-verify-141e169.json) since 2026-09-24T01:29:18Z
- review ROLLOUT-PREP: MERGED 22761a4 (f0a1a82) since 2026-09-24T00:59:03Z
- review WORKER: lane running (I2B-R4 composition root; item 3 real_worker 41a7480); told to merge phase-3 b9529d1 before handback since 2026-09-24T00:20:08Z
- review I2B-R4-worker: MERGED 405f633 (verifier pass at e540963; JSON evidence/i/I2B-R4-verify-e540963.json) since 2026-09-24T02:51:40Z
- review PREP-WORKER: early review pass at c035ee4 (JSON evidence/w/PREP-WORKER-review-c035ee4.json); lane folding L1-L8, J-F2-J-F4 since 2026-09-24T04:28:30Z

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
- 2026-09-23T10:43:20Z: Checkpoint at 90aadcf (code) / 01a7dfc (docs-only tail): api-test 2993 green on private ports; api-mutants 2349/2350 (the one red is the load-sensitive W3 sigint_not_handled runner timeout, killed in isolation); console 289 + typecheck + lint + 104 console mutants; bench 67 → main fast-forwarded f9ba5d2 → 01a7dfc
- 2026-09-23T14:10:54Z: SESSION LIMIT (Opus 429 until 14:00 UTC): all lanes/workflows died; coordinator saved everything (E3B2 WIP c587ddc; all lane branches pushed; partial review JSONs saved); main stays at 01a7dfc; handoff HANDOFF-20260923T1330Z.md
- 2026-09-24T05:33:05Z: checkpoint 2: main 8cabe20 (from 01a7dfc) - api-test 3639 (+2 P-21 drills green on d6), layer 0 202 no pending, lists 2185/2186 + 3 fixed (7a22064, 2b86ae5), E4B 140, layer 3 exit 0, bench 67

## Authorizations

- authorized: merge to main at reviewed green checkpoints (user, 2026-09-22)
- authorized: FULL operational authorization (user, 2026-09-22): hosted Supabase migration apply, public cutover, paid provider calls, compute purchases, any operations — logged with cost and rollback before each irreversible step
- authorized: 2026-09-23: finish in-flight lanes only; no new task dispatch (user instruction, session limits)
- NOT authorized: nothing withheld by the user; coordinator rules: log-before-act, bounded spend, backup/restore before hosted migration, fail-closed installer before cutover
