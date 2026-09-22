# Complete task ledger

Generated from [manifest v4](tasks.json) by `python3 research/plan/scripts/validate_plan.py --write-ledger`. Update the manifest only after evidence, then regenerate this file. Task status is separate from current dispatch priority.

**119 records; 113 active; 6 retired; 94 planned; 14 implemented; 5 integrated.** Original v1 statuses are preserved and do not establish product-v2 readiness. See [the audit](10-wave2-platform-audit.md).

**Current scope:** complete the robust and measured Marlin endpoint backend first. The E4B dependency closure is the immediate implementation set; App/browser work follows backend acceptance and Lab follows App. See [backend-first handoffs](18-marlin-backend-first.md), [the full plan](12-complete-build-plan.md), [pending inputs](15-pending-inputs.md) and [fresh-session prompt](16-fresh-session-handoff.md).

## Backend endpoint gate closure — current scope

| ID | Status / owner | Deliverable / brief | Start dependencies | Real integration dependencies |
|---|---|---|---|---|
| F1 | integrated / F | [Extract current gateway behind an application factory](handoffs/F-foundation.md) | — | — |
| F2 | integrated / F | [Freeze typed contracts, pins and executable fixtures](handoffs/F-foundation.md) | F1 | — |
| D2 | planned / D | [Atomic admission, durable preparation and dispatch outbox](handoffs/D-durable-state.md) | D1R | — |
| D3 | planned / D | [Fenced leases, recovery and cancellation](handoffs/D-durable-state.md) | D2, F2P | — |
| D4 | planned / D | [Persistent stream journal and replay](handoffs/D-durable-state.md) | D3, F2P | — |
| D5 | planned / D | [Terminal transaction, grants and reconciliation](handoffs/D-durable-state.md) | D4, F2P | — |
| M1 | implemented / M | [Bound and secure URL/base64 materialization](handoffs/M-media.md) | F2 | — |
| M2 | implemented / M | [Versioned preprocessing and tenant cache](handoffs/M-media.md) | F2P, M1 | D2, W1 |
| M3 | planned / M | [Owned uploads, expiry and orphan collection](handoffs/M-media.md) | F2P, M1 | D2 |
| Q1 | implemented / Q | [Deterministic memory scheduler and fairness model](handoffs/Q-scheduling.md) | F2 | — |
| Q2 | planned / Q | [Valkey adapter with atomic tested scripts](handoffs/Q-scheduling.md) | Q1, F2P | — |
| Q3 | planned / Q | [Outbox/reconciler integration and index loss recovery](handoffs/Q-scheduling.md) | Q2, F2P | D2, D3 |
| W1 | implemented / W | [Engine adapter and deterministic execution fakes](handoffs/W-worker.md) | F2 | — |
| W2 | planned / W | [Lease-aware execution, cancellation and completion](handoffs/W-worker.md) | W1, F2P | D5, M2, Q3 |
| W3 | planned / W | [Drain, engine pin and measured concurrency](handoffs/W-worker.md) | W2, F2P | E1, S2M |
| G2 | planned / G | [Synchronous chat and persistent SSE relay](handoffs/G-gateway.md) | G1R | D5, W2, M2, Q3, I0 |
| G3 | planned / G | [Explicit jobs, status, cancellation and replay](handoffs/G-gateway.md) | G1R | D5, W2, I0 |
| I1 | integrated / I | [Read-only inventory and deploy design](handoffs/I-infrastructure.md) | — | — |
| E1 | integrated / E | [Distinct corpus and authenticated benchmark client](handoffs/E-verification.md) | — | — |
| S1 | implemented / S | [Reconcile pulled wave-2 baseline and publish product revision audit](09-amendment-workstreams.md) | — | — |
| A1 | planned / D | [Verified individual signup entitlement and idempotent backfill](09-amendment-workstreams.md) | D1R | D5 |
| G4U | planned / G | [Owned upload HTTP adapter](09-amendment-workstreams.md) | G1R | M3 |
| F2R | planned / F | [Close remaining wave-2 contract and verification carryovers](11-wave3-revision-handoffs.md) | S1 | — |
| F2P | planned / F | [Encode product-v2 CREDIT, identity, serving and permission contracts](11-wave3-revision-handoffs.md) | F2R | — |
| D1R | planned / D | [Add product-v2 schema without rewriting USD pilot migrations](11-wave3-revision-handoffs.md) | F2P | E2R |
| I0 | implemented / I | [Repair installer atomicity and fail-closed startup prerequisite](11-wave3-revision-handoffs.md) | S1, I1 | — |
| E2R | planned / E | [Repair service harness ownership, role matrix and shared test clock](11-wave3-revision-handoffs.md) | S1 | — |
| G1R | planned / G | [Revise ingress for consumer and provider endpoint audiences](11-wave3-revision-handoffs.md) | F2P | D1R, D2 |
| S2M | implemented / S | [Freeze Marlin SOP inference launch profile](12-complete-build-plan.md) | S1 | — |
| G6B | planned / G | [Headless endpoint provisioning and operations](18-marlin-backend-first.md) | F2P | D1R, D5, A1, G1R |
| E3B | planned / E | [Backend-only durability, security and protocol integration gate](18-marlin-backend-first.md) | E2R, F2P | D1R, D5, G1R, G2, G3, G4U, G6B, M3, Q3, W3, I0, S2M |
| I2B | planned / I | [Reproducible Marlin endpoint deployment independent of frontends](18-marlin-backend-first.md) | I1, F2P | E3B, I0, W3, G6B |
| I3B | planned / I | [Backend recovery, observability, restore and rollback proof](18-marlin-backend-first.md) | I2B, F2P | E3B |
| E1B | planned / E | [Measure the end-to-end Marlin baseline and operating envelope](18-marlin-backend-first.md) | E1, S2M, F2P | E3B, I2B, W3 |
| M4 | planned / M | [Optimize bounded video retrieval, decoding and preparation](18-marlin-backend-first.md) | M2, M3, F2P | E1B, W2 |
| W4 | planned / W | [Tune Marlin GPU serving and scheduler admission from measured evidence](18-marlin-backend-first.md) | W3, F2P | E1B, M4, Q3 |
| E4B | planned / E | [Certify the robust and measured Marlin endpoint release candidate](18-marlin-backend-first.md) | E3B, F2P | I3B, E1B, M4, W4 |

## App launch additions — after backend acceptance

| ID | Status / owner | Deliverable / brief | Start dependencies | Real integration dependencies |
|---|---|---|---|---|
| U1 | implemented / U | [Usage and promotional balance views](handoffs/U-administration-ui.md) | F2 | C0, D5 |
| U2 | planned / U | [Keys and privacy/settings controls](handoffs/U-administration-ui.md) | U1R, F2P | C3A |
| U3 | planned / U | [Operator grants, suspension and pilot operations](handoffs/U-administration-ui.md) | U1R, F2P | C3A |
| I3 | planned / I | [Recovery, alarms and rollback runbooks](handoffs/I-infrastructure.md) | I2A, F2P | E3A, I3B |
| E2 | implemented / E | [Pinned integration services and fault harness](handoffs/E-verification.md) | E1, F2 | — |
| E4 | planned / E | [Single-GPU release evidence and launch decision](handoffs/E-verification.md) | E3A, F2P | I3, S2M, E4B |
| A2 | planned / A | [Consumer signup verification and credited onboarding](09-amendment-workstreams.md) | F2P | A1, C3A |
| A3 | planned / A | [Published catalog, credit rates and capability-matched examples](09-amendment-workstreams.md) | F2P | D1R, G1R, C0, S2M |
| C3A | planned / C | [Consumer key/privacy and platform operator actions](09-amendment-workstreams.md) | F2P, C0 | D5 |
| I2A | planned / I | [Reproducible App and single-GPU runtime deployment](09-amendment-workstreams.md) | I1, F2P | D5, G2, G3, G4U, W3, C3A, U2, U3, A2, A3, C0, I0, D1R, I2B |
| E3A | planned / E | [Consumer failure, security and signup-to-spend integration gate](09-amendment-workstreams.md) | E2, F2P, E3B | D5, A1, A2, A3, M3, Q3, W3, G2, G3, G4U, C3A, U2, U3, C0, E2R, I0, D1R |
| C0 | planned / C | [Wire the consumer database query port and real account context](11-wave3-revision-handoffs.md) | F2P | D1R |
| U1R | planned / U | [Adapt consumer usage and balance views to CREDIT and explicit legacy USD](11-wave3-revision-handoffs.md) | F2P, U1 | C0, D5 |

## Later core platform work and preserved module baselines

| ID | Status / owner | Deliverable / brief | Start dependencies | Real integration dependencies |
|---|---|---|---|---|
| D1 | integrated / D | [Migrate durable schema, wallet summaries and permissions](handoffs/D-durable-state.md) | F2 | — |
| G1 | implemented / G | [Ingress, auth and capability validation](handoffs/G-gateway.md) | F2 | D2 |
| T1 | implemented / T | [Byte-budgeted capture and persistent spool](handoffs/T-traces.md) | F2 | — |
| T3 | planned / T | [Logical retention, deletion and observability](handoffs/T-traces.md) | T2I, F2P | T2F |
| J1 | implemented / J | [Dry-run sampler, rubric and score validation](handoffs/J-judge.md) | F2 | — |
| J2 | planned / J | [Consent/budget coordinated submission and collection](handoffs/J-judge.md) | J1, F2P | D6J, T2I, L2 |
| J3 | planned / J | [Operator calibration and quality report](handoffs/J-judge.md) | J2, F2P | C3L, V2 |
| C1 | implemented / C | [Typed repositories, pagination and tenant query boundary](handoffs/C-console-services.md) | F2 | D1, C0 |
| C2 | planned / C | [Lab content access, expiry and safe signed references](handoffs/C-console-services.md) | F2P, C0 | M3, T3, L2 |
| V1 | implemented / V | [Paginated trace list and filters](handoffs/V-trace-ui.md) | F2 | V1M |
| V2 | planned / V | [Trace detail, content and feedback](handoffs/V-trace-ui.md) | V1M | C2, C3F |
| V3 | planned / V | [Judge score and calibration presentation](handoffs/V-trace-ui.md) | V2, F2P | J2 |
| D6F | planned / D | [Durable feedback and immutable author provenance](09-amendment-workstreams.md) | D5, F2P | — |
| D6J | planned / D | [Lab consent, USD budget and external submission coordination](09-amendment-workstreams.md) | D5, F2P | L2 |
| G4F | planned / G | [Owned feedback HTTP adapter](09-amendment-workstreams.md) | G1R | D6F |
| G4T | planned / G | [Owned trace export HTTP adapter](09-amendment-workstreams.md) | G1R | T2I, T3, C2 |
| T2I | planned / T | [Inference analytics and content projection](09-amendment-workstreams.md) | T1, F2P | D5 |
| T2F | planned / T | [Feedback analytics projection](09-amendment-workstreams.md) | T2I, F2P | D6F |
| C3F | planned / C | [Authorized feedback and review actions](09-amendment-workstreams.md) | F2P, C0 | D6F, L2 |
| C3L | planned / C | [Lab judge and calibration control actions](09-amendment-workstreams.md) | F2P, C0 | D6J, J2, L2 |
| L1 | planned / L | [Provider app shell and separate build/auth boundary](09-amendment-workstreams.md) | F2P | L2 |
| L2 | planned / L | [Provider role and purpose-specific data-access services](09-amendment-workstreams.md) | F2P | D1R |
| L3 | planned / L | [Assisted model registration and dev/prod revision services](09-amendment-workstreams.md) | L2, F2P | G1R, W2, A3 |
| L4 | planned / L | [Model/deployment/publication UI and aggregate health](09-amendment-workstreams.md) | L1, F2P | L3 |
| I2L | planned / I | [Independent Lab app and control-service deployment](09-amendment-workstreams.md) | I1, F2P | L1, L2, L3, L4 |
| E3L | planned / E | [Provider access, publication and rollback integration gate](09-amendment-workstreams.md) | E2, F2P | L1, L2, L3, L4, E2R, D1R |
| E5L | planned / E | [Provider traces, review and evaluation integration gate](09-amendment-workstreams.md) | E3L, F2P | D6F, D6J, G4F, G4T, T3, J2, J3, C3F, C3L, V3 |
| V1M | planned / V | [Move the implemented trace explorer into the authorized Lab shell](11-wave3-revision-handoffs.md) | F2P, L1 | L2, C0, T2I |
| F3 | planned / F | [Freeze dataset, evaluation, training and rollout contracts](13-lab-improvement-handoffs.md) | F2P | — |
| D7 | planned / D | [Persist datasets, harnesses and evaluation coordination](13-lab-improvement-handoffs.md) | F3, D1R | — |
| N1 | planned / N | [Import benchmark data and existing annotation outputs](13-lab-improvement-handoffs.md) | F3 | D7, M3, L2 |
| N2 | planned / N | [Version, split and export reproducible datasets](13-lab-improvement-handoffs.md) | N1 | D7 |
| N3 | planned / N | [Derive datasets from permitted traces and propagate revocation](13-lab-improvement-handoffs.md) | N2 | T3, C2, D6F |
| N4 | planned / N | [Build dataset import, version and split workflows in Lab](13-lab-improvement-handoffs.md) | F3, L1 | N2 |
| H1 | planned / H | [Version prompts and bounded replay harnesses](13-lab-improvement-handoffs.md) | F3 | L2 |
| B1 | planned / B | [Execute durable offline evaluation runs](13-lab-improvement-handoffs.md) | F3 | D7, N2, H1, L3, W2, D5 |
| B2 | planned / B | [Compare quality, costs and latency with honest uncertainty](13-lab-improvement-handoffs.md) | F3 | B1 |
| B3 | planned / B | [Benchmark externally produced checkpoints continuously](13-lab-improvement-handoffs.md) | F3 | B1, L2 |
| B4 | planned / B | [Build evaluations and experiment comparisons in Lab](13-lab-improvement-handoffs.md) | F3, L1 | B2, B3, H1 |
| D8 | planned / D | [Persist annotations and external training lifecycle](13-lab-improvement-handoffs.md) | F3, D7 | D6J |
| P1 | planned / P | [Import, review and export annotation records](13-lab-improvement-handoffs.md) | F3 | D8, N2 |
| P2 | planned / P | [Run bounded teacher annotation batches](13-lab-improvement-handoffs.md) | F3 | P1, D8, N2, J2, J3 |
| P3 | planned / P | [Integrate external training and import candidates](13-lab-improvement-handoffs.md) | F3 | D8, N2, B3, L2 |
| P4 | planned / P | [Build annotation and training workflows in Lab](13-lab-improvement-handoffs.md) | F3, L1 | P1, P2, P3, B2 |
| D9 | planned / D | [Persist release policies and stable experiment assignment](13-lab-improvement-handoffs.md) | F3, D7 | — |
| R1 | planned / R | [Route bounded shadow, canary and A/B experiments](13-lab-improvement-handoffs.md) | F3 | D9, L3, G2, G3 |
| R2 | planned / R | [Evaluate guardrails and roll back controlled releases](13-lab-improvement-handoffs.md) | R1 | B2, L4 |
| R3 | planned / R | [Register and compare optimized serving variants](13-lab-improvement-handoffs.md) | F3 | H1, B2, L2, W3 |
| R4 | planned / R | [Build release experiments and optimization comparison UI](13-lab-improvement-handoffs.md) | F3, L1 | R2, R3, B2 |
| I5 | planned / I | [Package dataset and evaluation workers for independent deployment](13-lab-improvement-handoffs.md) | F3, I1 | N2, B1, B3 |
| I6 | planned / I | [Package annotation and training integration workers](13-lab-improvement-handoffs.md) | I5 | P2, P3 |
| I7 | planned / I | [Package release controls and optimization evidence operations](13-lab-improvement-handoffs.md) | I5 | R2, R3 |
| E6L | planned / E | [Prove imported benchmark to candidate decision end to end](13-lab-improvement-handoffs.md) | F3, E2R | E3L, N2, N4, H1, B1, B2, B3, B4, I5 |
| E7L | planned / E | [Prove the second authorized improvement iteration](13-lab-improvement-handoffs.md) | E6L | E5L, N3, P1, P2, P3, P4, I6 |
| E8L | planned / E | [Prove controlled rollout and optimization evidence](13-lab-improvement-handoffs.md) | E6L | R1, R2, R3, R4, I7 |

## Conditional work — activation required

| ID | Status / owner | Deliverable / brief | Start dependencies | Real integration dependencies |
|---|---|---|---|---|
| G5 | planned / G | [Signed async completion callbacks](handoffs/G-gateway.md) | G3, F2P | D5, M1 |
| I4 | planned / I | [Separately gated fleet deployment](handoffs/I-infrastructure.md) | I3B, E4B, F2P | — |
| X1 | planned / X | [Specify a bounded live-video workload and trial contract](14-expansion-gates.md) | F2P | — |
| X2 | planned / X | [Implement and verify the approved video session adapter](14-expansion-gates.md) | X1 | W3, M3, E6L |
| X3 | planned / X | [Specify a robot/task and inference freshness contract](14-expansion-gates.md) | F2P | — |
| X4 | planned / X | [Implement the bounded policy/ROS2 observation adapter](14-expansion-gates.md) | X3 | W3, E6L |
| X5 | planned / X | [Qualify a non-NVIDIA backend investment](14-expansion-gates.md) | F2P | — |
| X6 | planned / X | [Implement and validate one qualified inference backend](14-expansion-gates.md) | X5 | R3, W3 |

## Retired mixed tasks — never dispatch

| ID | Status / owner | Deliverable / brief | Start dependencies | Real integration dependencies |
|---|---|---|---|---|
| D6 | superseded-for-scheduling / D | [Durable feedback and judge coordination](handoffs/D-durable-state.md) | — | — |
| G4 | superseded-for-scheduling / G | [Uploads, feedback and trace export adapters](handoffs/G-gateway.md) | — | — |
| T2 | superseded-for-scheduling / T | [Idempotent analytics and content shipping](handoffs/T-traces.md) | — | — |
| C3 | superseded-for-scheduling / C | [Shared key/settings/grant/feedback/judge actions](handoffs/C-console-services.md) | — | — |
| I2 | superseded-for-scheduling / I | [Reproducible single-GPU deployment](handoffs/I-infrastructure.md) | — | — |
| E3 | superseded-for-scheduling / E | [Cross-module failures and security gate](handoffs/E-verification.md) | — | — |

## Scheduling rules

Start edges require committed reviewed interfaces/fixtures; F2R/F2P/D1R additionally require their acceptance evidence. Integration edges require actual adapters and tests before integration status. Same-path tasks stay serial even if graph edges permit parallel development. D owns every migration; the coordinator owns common wiring and this manifest. New package slices are in their briefs; split oversized units before dispatch, preserving parent acceptance.

Conditional task activation is specified in the manifest and [expansion gates](14-expansion-gates.md). G5 callbacks and I4 fleet are not baseline launch dependencies. LAB M2–M4 local gates differ from hosted/model-quality certification. Dates, reviewer identity, branch assignments and live results belong in appended coordinator evidence, not invented here.
