# Requirement coverage and source reconciliation

**2026-09-24 closure:** [Program 22](22-consumer-v1-implementation.md) adds RV-01…RV-12 repair coverage and consumer result U4. Original requirements below remain preserved; E3C/E4C now supply repaired backend evidence for App. The validator checks every finding owner reaches the consumer release closure.

Read the source sections for background/detail; contracts and durable protocols override conflicts. This map ensures the combined serving/trace program is assigned rather than silently dropped. Named tasks are in [the manifest](tasks.json); required test oracles are in [verification](04-verification.md).

**2026-09-21 amendment:** the table below preserves the original requirement mapping. Mixed IDs resolve through [the complete 45-task mapping](08-platform-split.md). Product scope and current release gates are in [App requirements](../platforms/03-app-spec.md), [Lab requirements](../platforms/05-lab-spec.md) and manifest v4. App uses E3A/I2A/E4; Lab operations use E3L/I2L and observation uses E5L.

| Source requirement family | Implementation owners | Verification / disposition |
|---|---|---|
| Production requirements/traffic model; overload and fairness | D2/Q1–Q3/G1/E1 | DUR-CAP, API-MODES, PERF-PILOT; measured envelope replaces no-drop claim |
| Production architecture/queue schema/local mode | F2/D1–D5/Q1–Q3/I2 | DUR-ADMIT/OUTBOX/FENCE; PG authority even without Valkey |
| Production HTTP, jobs, idempotency, cancel/replay | G1–G4/D2–D5 | API-MODES/STREAM, DUR-OUTPUT; ordinary chat no automatic202 |
| Production optional webhooks | G5/D1/D5/M1 | API-CALLBACK; optional registered destinations, signed at-least-once delivery |
| Production media, URLs/uploads/cache/token budgets | M1–M3/W1/E1 | MEDIA-SEC/PARITY; area/context corrections, tenant cache identity |
| Production worker, heartbeat/reaper/drain | W1–W3/D3–D5 | DUR-FENCE/OUTPUT, OPS-RECOVER |
| Production autoscaling/cold start/AZ capacity | I1/I4/E4 | FLEET-GATE after pilot; purchases separately scoped |
| Production metrics/alerts/health/operations | Q3/W3/T3/I2/I3/E3 | OPS-RECOVER; public health sanitized, protected readiness |
| Production cost/unit economics/pricing | D1/D2/D5/U1/E4 | DUR-CAP/SETTLE; promotional consumption and internal costs, no cash-revenue claim |
| Production rollout/migration | F1/F2/D1/I2/I3/E3/E4 | G0–G3; preserve historical fixes, additive schema, no unmetered rollback |
| Traces requirements/metrics | T1–T3/C1/U1/V1 | TRACE-BOUNDS/RECOVER; off-mode denominator and loss accounting explicit |
| Traces architecture/data model/capture | T1–T3/D5 | Real CH DDL/dedup and fsync boundary; mandatory accounting separate from content |
| Traces content and retention | M3/T3/C2/V2 | TRACE-TENANT; logical access expiry independent of physical deletion |
| Traces feedback/API and console provenance | D6/G4/C3/V2 | FEEDBACK-ACK; customer console labels never implicitly operator labels |
| Traces judge/cost/consent/calibration | D6/J1–J3/C3/V3 | JUDGE-BUDGET/SCORES; dry-run first, live egress scoped/budgeted |
| Traces console/filters/details/settings | C1–C3/U2/V1–V3 | CONSOLE-FLOWS/TENANT; one server-action owner |
| Traces phase tests and cross-cutting H-series | E2/E3/E4 + module suites | Map legacy IDs with source namespace; actual fault/state assertions |
| App auth/keys/models/usage/admin baseline | F1/G1/C1/C3/U1–U3 | Existing behavior preserved; selected org model; protected role/limits |
| App pricing and credit features | D1R/D5/A1/A2/A3/C0/C3A/U1/U3 | One-time 10,000 CREDIT per individual, historical USD preserved separately, holds and exact settlement |
| Platform S1/S2 loop | Serving + traces + feedback + evaluation above | Pilot evidence; broader platform thesis remains context |
| Payments, commercial second-owner, OpenRouter | Deferred by accepted scope | No implementation task or paid-launch claim in this package |
| Consumer teams/pooling and org-switcher | Deferred from App | Personal-org consumer ownership retained; provider memberships separate |
| Provider dataset/experimentation/training loop | Lab milestones M2–M4 | F3/D7–D9/N/H/B/P/R/I5–I7/E6L–E8L detailed follow-on tasks; no consumer launch dependency |
| Provider roles, model versions, endpoint publication | L1–L4/A3/D1R | LAB-ACCESS/PUBLISH; customer access grants independent from model ownership |
| Consumer public verified signup and individual grant | A1/A2/D1R/D5 | CREDIT-GRANT/IDENTITY/UNITS and APP-JOURNEY |
| Response caching, speculative/quantized/kernel optimization | Deferred pending evidence and separate consent semantics | M2/W3 parity/capability probes only; do not activate from research estimates |

## Review precedence examples

The old serving implementation spec's §§3–5, 13–14 describe queue state, HTTP modes and rollout before these corrections; use the new durable protocol and task graph. Its Lua/DDL/pseudocode remain explanatory. The old traces capture/data-model examples omit active-byte budgeting, durable feedback coordination or all deduplicating reads; use T/D/C tasks. Historical “all requests have a trace” assertions are now conditional on trace eligibility and capture outcome. Feedback and judge acceptance require durable evidence, not successful enqueue into a volatile process queue.

## Verification log

- 2026-09-20: Assigned source requirement families to tasks/tests and explicitly identified deferred scope. Detailed source test-ID mapping is E2's implementation deliverable.

- 2026-09-21: Amended for separate consumer App/provider Lab, individual signup credits and independent release gates; see the platform-split review. Implementation evidence on the other system remains unverified here.

- 2026-09-21 wave-2 audit: manifest v4 adds F2R/F2P/D1R/C0/I0/E2R/G1R/V1M/U1R. Imported baseline completions are retained; the audit and revision briefs map current repair/integration gates. Earlier unverified-remote statements apply only to the pre-pull review.

## Complete product coverage (manifest v4)

The original source map above is historical where it says later Lab work is undecomposed. This mapping gives every current product requirement an implementation owner and an actual gate. Current dispatch remains App first.

| Requirement | Task coverage | Gate / oracle |
|---|---|---|
| APP-01 signup/recovery | A2/C0/C3A/D1R | E3A; APP-JOURNEY/CONSOLE-TENANT |
| APP-02 individual grant | A1/D1R/D5/F2P | E3A; CREDIT-GRANT/IDENTITY |
| APP-03 catalog/rates/capabilities | A3/G1R/S2M | E3A/E4; CREDIT-RATE/MARLIN-SOP |
| APP-04 keys | U2/C3A/G1R | E3A; API-AUTH |
| APP-05 inference/media | G2/G4U/M2/M3/W2/W3 | E3A/E4; MEDIA-SEC/PARITY/API-STREAM |
| APP-06 explicit async/replay | G3/D2–D5/Q2/Q3 | E3A; API-MODES/DUR-OUTPUT |
| APP-07 credits/history | U1R/C0/C3A/D5 | E3A; CREDIT-UNITS/SPEND |
| APP-08 own usage | U1R/C0/C3A/D5 | E3A; CONSOLE-TENANT |
| APP-09 working docs | A3/S2M | E3A/E4; APP-JOURNEY/MARLIN-SOP |
| APP-10 settings/data consent | U2/C3A, T3 only when capture enabled | E3A and enabled-feature TRACE-TENANT |
| APP-11 operator controls | U3/C3A/A3 | E3A; CREDIT-RATE/CONSOLE-TENANT |
| APP-12 bounds/exhaustion | G1R/D2/A2 | E3A/E4; DUR-CAP/CREDIT-SPEND |
| APP-13 Marlin SOP launch profile | S2M/A3/W3/E4 | MARLIN-SOP; real-model smoke separate from SOP-quality certification |
| LAB-01 access | L1/L2/D1R | E3L; LAB-ACCESS |
| LAB-02 registry | L2/F2P/D1R | E3L; LAB-PUBLISH |
| LAB-03 dev/prod/rollback | L3/L4/G1R/W2 | E3L; LAB-PUBLISH |
| LAB-04 publication/rates | L4/A3/D1R | E3L; CREDIT-RATE/LAB-PUBLISH |
| LAB-05 aggregates | L4/C0/T2I/T3 | E3L/E5L; LAB-ACCESS/TRACE-BOUNDS |
| LAB-06 traces | V1M/C2/T2I/T3/G4T | E5L; TRACE-TENANT/RECOVER |
| LAB-07 review/feedback | V2/C3F/D6F/G4F | E5L; FEEDBACK-ACK |
| LAB-08 dataset lifecycle | F3/D7/N1–N4 | E6L/E7L; DATA-IMPORT/SPLIT/RIGHTS/LINEAGE |
| LAB-09 evaluation/compare | B1/B2/B4/D7/H1 | E6L; EVAL-DURABLE/REPRO/COMPARE |
| LAB-10 checkpoints | B3/L2/D7 | E6L; CHECKPOINT-IDEM |
| LAB-11 prompt/harness | H1/F3/B4 | E6L; HARNESS-SAFE/EVAL-REPRO |
| LAB-12 teacher/judge | J2/J3/V3/C3L/D6J and P2/D8 | E5L/E7L; JUDGE-BUDGET/SCORES/PIPELINE-BUDGET |
| LAB-13 training integration | P1/P3/P4/D8/N3 | E7L; TRAIN-RECOVER/PIPELINE-LINEAGE |
| LAB-14 controlled release | D9/R1/R2/R4 | E8L; ROLLOUT-PIN/RECOVER |
| LAB-15 optimization evidence | R3/R4/W3; conditional X5/X6 backend | E8L; OPT-PARITY; actual hardware gate separate |
| LAB-16 specialized modalities | X1/X2 video, X3/X4 robotics | Approved contract then VIDEO-CAUSAL/ROBOT-REPLAY; conditional, not App launch |

I5/I6/I7 package later workers and per-enabled-feature staging evidence. E6L does not depend on E5L/customer capture; E8L does not depend on training. Core local completeness covers App plus Lab M0–M4. Speech/commercial/fleet/new chip or transport claims require their own scope/activation; nothing is silently counted complete.

## Backend-first coverage (2026-09-22)

These requirements define current completion independently of the App/Lab presentation layers. The individual-credit and later product mappings above remain valid.

| Requirement | Owners | Gate / evidence |
|---|---|---|
| BACKEND-01 supported Marlin protocol and immutable artifact/processor | S2M/F2P/W3/G1R | MARLIN-SOP/F-CONTRACT; actual model smoke |
| BACKEND-02 headless identities, keys, limits and operator actions | D1R/A1/D5/G6B/G1R | API-OPS/API-AUTH; two-tenant denial and revocation |
| BACKEND-03 secure bounded finite-video upload/retrieval/preparation | M1–M3/G4U | MEDIA-SEC/PARITY |
| BACKEND-04 durable acceptance, scheduling, leases and exact accounting | D2–D5/Q2/Q3/W2 | DUR-ADMIT/CAP/FENCE/SETTLE/OUTBOX |
| BACKEND-05 sync/SSE/explicit async/replay/cancel and dataset resume | G2/G3/G6B/W2 | API-MODES/STREAM/BACKEND-JOURNEY |
| BACKEND-06 independent reproducible runtime deployment | I0/I2B/W3 | BACKEND-DEPLOY/DEPLOY-FAILCLOSED |
| BACKEND-07 metrics, recovery, restore and rollback | I3B | BACKEND-OBSERVE/OPS-RECOVER |
| BACKEND-08 representative real-GPU workload baseline/envelope | E1/E1B | PERF-ENVELOPE/MARLIN-SOP |
| BACKEND-09 measured preparation and engine optimization | M4/W4/Q3 | MEDIA-OPT/ENGINE-OPT/MEDIA-PARITY |
| BACKEND-10 combined final endpoint certification | E3B/E4B | Actual-service faults, final GPU soak/load/quality/accounting with App/Lab absent |

E3A adds browser/onboarding evidence on top of E3B; I2A reuses I2B, I3 reuses I3B, E4 reuses E4B and verifies the consumer release delta. I4 can be activated from backend evidence without waiting for UI. These dependencies prevent both premature UI gating and duplicate runtime implementations.

## Post-wave consumer launch corrections

| Review finding | Implement / integrate | Product outcome / oracle |
|---|---|---|
| RV-01 | F2C/G7/A3 | Truthful model, limits, rates, retention: CATALOG-TRUTH |
| RV-02 | D10/M5/E1C/E3C | Usable uploads across process replacement: UPLOAD-RESTART |
| RV-03 | D10/M6/I8/E3C | Safe durable content lifecycle: RETENTION-DURABLE |
| RV-04 | S3/E4C | Accepted final backend certificate: LOAD-CLOSEDLOOP |
| RV-05 | F2C/D10/W5/G7/E3C | Durable readiness including text: ADMISSION-READY |
| RV-06 | C0/C3A/A2/A3/U1R/U2/U3/U4/E3A | Actual verified signup-to-result consumer journey: APP-JOURNEY/USER-RESULTS |
| RV-07 | E1C/M5/G7 | Actual upload wire protocol: UPLOAD-RESTART |
| RV-08 | E1C/E4C | Fresh-generation capacity evidence: BENCH-VALIDITY |
| RV-09 | D10/I8/E4C | Operated recoverable service: OPS-CONTINUOUS |
| RV-10 | I8/E4C | Rollback serves and settles a real request: OPS-RECOVER |
| RV-11 | F2C/D10/G7/U4/E3C | Persisted result lifetime across all paths: RESULT-EXPIRY |
| RV-12 | E2C/E3C | Reproducible supported environment: VERIFY-REPRO |

The reviewed code also needs moderate dependency-alert triage in E2C. Fleet/custom model/Modal comparison stays in [proposal 23](23-inference-hosting-roadmap.md), conditional I4 and later specialization gates; no new consumer launch dependency is introduced.
