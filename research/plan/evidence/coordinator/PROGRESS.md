# Backend-first progress tracker

Generated 2026-09-23T12:44:00Z from `tasks.json` (manifest v4) and `progress-state.json`. Integration branch `claude/backend-impl`, base `ec6c548`. Scope: the E4B backend closure (37 tasks, of which 7 foundations and wave-2 modules are already merged and reused: E1, F1, F2, I1, M1, Q1, W1).

**Backend packages: 22 done · 6 in progress · 2 remaining (of 30).**

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
| B1 Durable endpoint | D4 | Persistent stream journal and replay | **in-progress** | planned | 0017 stream journal: fenced append, global budget without a global lock, terminal event via AFTER UPDATE OF settled_at trigger, read_owned replay (typed gaps/expiry/cursor), pruning + usage, races, E3B drills dr05/06/08/10, CREDIT journal + privileges; sweeps 788 both images; 44 migration + 9 code mutants; requests: harness 0017 line, Makefile += tests/d/test_code_mutants_d4.py, G2 compose PgStreamStore, W3 expire() in reap_once, I3B gauge, pgstate additions, E3B2 rig hook |
| B1 Durable endpoint | D5 | Terminal transaction, grants and reconciliation | **remaining** | planned |  |
| B1 Durable endpoint | A1 | Verified individual signup entitlement and idempotent backfill | **done** | implemented |  |
| B1 Durable endpoint | M2 | Versioned preprocessing and tenant cache | **done** | implemented |  |
| B1 Durable endpoint | M3 | Owned uploads, expiry and orphan collection | **done** | implemented |  |
| B1 Durable endpoint | Q2 | Valkey adapter with atomic tested scripts | **done** | implemented |  |
| B1 Durable endpoint | Q3 | Outbox/reconciler integration and index loss recovery | **done** | implemented |  |
| B1 Durable endpoint | W2 | Lease-aware execution, cancellation and completion | **done** | implemented |  |
| B1 Durable endpoint | W3 | Drain, engine pin and measured concurrency | **done** | implemented |  |
| B1 Durable endpoint | G1R | Revise ingress for consumer and provider endpoint audiences | **done** | implemented |  |
| B1 Durable endpoint | G2 | Synchronous chat and persistent SSE relay | **in-progress** | planned | relay.py (accept/sync/SSE/cancel causes), pilot.py (fail-closed build_ingress_deps, lifespan), ingress readyz/route table; G suite 385, G list 264 (257 mutants); W-new blocking defect (engine refuses stream/max_tokens in parameters) → fix lane codex/w-consumed-parameters |
| B1 Durable endpoint | G3 | Explicit jobs, status, cancellation and replay | **in-progress** | planned | routes/jobs.py (POST /v1/jobs, status, result, events, DELETE) + additive Relay.admit/on_async/pump(cursor, cancel_on_gone) + route table + client example; 57 mutants over 33 cases; tests/g 422; G list 264; finding: a crash between the admission commit and the 202 leaves a replayed job's media never attached (G2 request b) |
| B1 Durable endpoint | G4U | Owned upload HTTP adapter | **done** | implemented |  |
| B1 Durable endpoint | G6B | Headless endpoint provisioning and operations | **done** | implemented |  |
| B2 Integrate & deploy | E3B | Backend-only durability, security and protocol integration gate | **in-progress** | planned | namespace e3b2; real JobStore/CREDIT admission/queue rebuild/reaper/tenants/RLS completeness (490 cases) on real stores; 25 e3bm mutants killed; backend 87 pass / 23 pending / 11 fail (all I3B); requests: tasklocal TASK_BLOCKS, I3B fixes, fake requeue event id, host reserved ports, PENDING/RESIDUAL updates at D4/G2/G4U merges |
| B2 Integrate & deploy | I2B | Reproducible Marlin endpoint deployment independent of frontends | **done** | implemented | needs allocated GPU/staging (P-04) |
| B2 Integrate & deploy | I3B | Backend recovery, observability, restore and rollback proof | **done** | implemented | needs allocated GPU/staging (P-04) |
| B2 Integrate & deploy | E1B | Measure the end-to-end Marlin baseline and operating envelope | **in-progress** | planned | sop-synth-v1 generator, bench idempotency/resume, open-loop driver, predeclared protocol | resumed from WIP after restart |
| B3 Measured tuning | M4 | Optimize bounded video retrieval, decoding and preparation | **done** | implemented | needs allocated GPU/staging (P-04) |
| B3 Measured tuning | W4 | Tune Marlin GPU serving and scheduler admission from measured evidence | **in-progress** | planned | protocol + candidate.sh + decide.py + parity.py + P-20 record; 52 mutants killed; interim ceiling 82 s (72 also safe); Makefile += tests/w/test_w4_mutants.py at merge |
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
| P-04-sweep | W3 concurrency sweep DONE (2026-09-23T05:04Z, sweep-20260923T050411Z): F(c) never 0 because the engine's 16384-token encoder cache rejects the four 112 s clips at every level; no level qualifies under the predeclared rule; c=16 would qualify with those set aside | ENGINE_MAX_NUM_SEQS / WORKER_CONCURRENCY (stay 8 ⚠️ / cutover keeps 32 explicitly); W4's first input | W4 (tuning) / W3 (record) |
| P-20 | The engine rejects videos above ~72 s (encoder cache budget 16384 tokens) while profile v1 admits up to 120 s: decide raise the budget (W4, re-measure) or cap admission (G2 validation) — until then the pilot admits what the engine cannot serve | E4B certification; G2's validation ceiling | W4 decides; G2 applies |
| P-21 | Host sysctl net.ipv4.ip_local_reserved_ports for the task-port blocks (55432-55499, 56700-56799) — a root change on this host (persist in /etc/sysctl.d); prevents ephemeral-port bind collisions between lanes | nothing (lanes retry); reduces HarnessBusy/bind noise | user (root approval); coordinator applies |

## ETA (provisional, cadence-based — not a commitment)

- Observed cadence: 11 tasks integrated in 15.7 h of wall clock (0.70 tasks/h at 4–6 concurrent lanes, each task 2–4 review rounds), incl. two rate-limit interruptions.
- Local software to BACKEND-LOCAL/E3B and the software half of the rest (5 packages): ~7 h at observed cadence, ~14 h if wave-3 packages run at half that rate (they are larger and the D lane is serial); the serial critical path alone (D1R→D2→D3→D4→D5→E3B) is at least ~22 h.
- GPU-gated packages (E1B, W4, E4B): **no ETA until P-04 is allocated**; their software (harnesses, scripts, runbooks) proceeds inside the local estimate.
- Continuous coordinator time is assumed; interruptions (rate limits, restarts) extend wall clock, not work.

## In flight

- E1B: codex-e1b — software slices MERGED (164e43e); GPU measurement slices pending W3 → I2B since 2026-09-22T16:03:43Z — sop-synth-v1 generator, bench idempotency/resume, open-loop driver, predeclared protocol | resumed from WIP after restart
- E3B: codex-e3b / codex/e3b-backend-gate — phase 2 review fix_required at 80cef22 (4 blocking: dr13 premise unpinned, baseline-red mutants counted killed, I2B pending vs RESIDUAL, e3bm22 dies by import; 1 refuted; 17 nonblocking; rulings R92/R93, P-21) → fix round running since 2026-09-22T18:46:49Z — namespace e3b2; real JobStore/CREDIT admission/queue rebuild/reaper/tenants/RLS completeness (490 cases) on real stores; 25 e3bm mutants killed; backend 87 pass / 23 pending / 11 fail (all I3B); requests: tasklocal TASK_BLOCKS, I3B fixes, fake requeue event id, host reserved ports, PENDING/RESIDUAL updates at D4/G2/G4U merges
- D4: codex-d4 / codex/d4-stream-journal — confirmation at 7d3ef59: journal lens PASS; adapter lens one test gap (M1 any-position refusal unkilled) + 6 small items → round 3 (tests + surrogate guard) running; then a single-verifier check, MERGE, D5 dispatch since 2026-09-23T05:12:27Z — 0017 stream journal: fenced append, global budget without a global lock, terminal event via AFTER UPDATE OF settled_at trigger, read_owned replay (typed gaps/expiry/cursor), pruning + usage, races, E3B drills dr05/06/08/10, CREDIT journal + privileges; sweeps 788 both images; 44 migration + 9 code mutants; requests: harness 0017 line, Makefile += tests/d/test_code_mutants_d4.py, G2 compose PgStreamStore, W3 expire() in reap_once, I3B gauge, pgstate additions, E3B2 rig hook
- G2: codex-g2 / codex/g2-chat-relay — confirmation at a786af7 fix_required (5 blocking: an in-flight mapped job still re-prepares media; an in-flight replay re-runs the rechecks and can cancel a running CREDIT job; drain-before-close unpinned; identity-send half of the named rule unpinned; 9 nonblocking) → round 3 running since 2026-09-23T05:12:27Z — relay.py (accept/sync/SSE/cancel causes), pilot.py (fail-closed build_ingress_deps, lifespan), ingress readyz/route table; G suite 385, G list 264 (257 mutants); W-new blocking defect (engine refuses stream/max_tokens in parameters) → fix lane codex/w-consumed-parameters
- W4: codex-w4 / codex/w4-measured-tuning — re-confirmation at ba95e42: 1 clause-level coverage gap (candidate half of the paired-levels check) + 4 tiny nonblocking → round 4 (tests only) running; then a single-verifier check and merge since 2026-09-23T05:40:38Z — protocol + candidate.sh + decide.py + parity.py + P-20 record; 52 mutants killed; interim ceiling 82 s (72 also safe); Makefile += tests/w/test_w4_mutants.py at merge
- G3: codex-g3 / codex/g3-jobs — review fix_required at a100aee (4 blocking: a mode switch under one key attaches a cancelling sync wait to an async job → R94; events observer error/cancel paths unpinned; DELETE with a failing cancel must be non-200 retryable; 15 nonblocking) → fix round on the merged head (G2 a786af7) running since 2026-09-23T10:09:50Z — routes/jobs.py (POST /v1/jobs, status, result, events, DELETE) + additive Relay.admit/on_async/pump(cursor, cancel_on_gone) + route table + client example; 57 mutants over 33 cases; tests/g 422; G list 264; finding: a crash between the admission commit and the 202 leaves a replayed job's media never attached (G2 request b)
- I3B-followup: codex-i3bf / codex/i3b-followup — HANDED BACK d99da09 (impl 0737ebe); Opus review running; MERGE AFTER E3B2 (depends on stack.stubbed() + OWNED_TREES += infra) since 2026-09-23T11:22:39Z — R92 acldefault comparison (bk01_a + 8 bk01f green on Supabase; plain image unsupported by the fixture: 17.6 client vs 16.14 server); i3bm57 re-anchored; rc10 = runbook composition with PATH stubs for systemctl/docker/curl; pending honesty (rc03→G2, rc04 stubbed, rc05b→M1-L2, rc08b→I2B-R4); 3 crash-kills fixed; I3B list 86: 84 killed, 1 control, i3bm33 no-cases (needs E2 Valkey)
- review W4: re-confirmation fix_required at ba95e42 (wf_884c29c8-5ee; JSON evidence/w/W4-reconfirm-ba95e42.json) → round 4 since 2026-09-23T12:12:43Z
- review G4U: MERGED 7d21fa7 after confirmation pass at 962b2b1 since 2026-09-23T10:33:10Z
- review G2: confirmation fix_required at a786af7 (wf_575b11a2-ce2; 13 agents; JSON evidence/g/G2-confirm-a786af7.json) → round 3 since 2026-09-23T12:44:00Z
- review E3B2: fix_required at 80cef22 (wf_345bc3f0-8ec; 13 agents; JSON evidence/e/E3B2-review-80cef22.json) → fix round; confirmation next since 2026-09-23T11:20:35Z
- review D4: confirmation fix_required at 7d3ef59 on one test gap (wf_92a6c092-b89; JSON evidence/d/D4-confirm-7d3ef59.json) → round 3 since 2026-09-23T12:39:54Z
- review G3: fix_required at a100aee (wf_bc7d6d9f-ded; 12 agents; JSON evidence/g/G3-review-a100aee.json) → fix round; confirmation next since 2026-09-23T12:25:20Z
- review I3B-followup: review at d99da09 running (2 lenses on the d3 harness over a scratch merge with E3B2's head) since 2026-09-23T12:44:00Z

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

## Authorizations

- authorized: merge to main at reviewed green checkpoints (user, 2026-09-22)
- authorized: FULL operational authorization (user, 2026-09-22): hosted Supabase migration apply, public cutover, paid provider calls, compute purchases, any operations — logged with cost and rollback before each irreversible step
- authorized: 2026-09-23: finish in-flight lanes only; no new task dispatch (user instruction, session limits)
- NOT authorized: nothing withheld by the user; coordinator rules: log-before-act, bounded spend, backup/restore before hosted migration, fail-closed installer before cutover
