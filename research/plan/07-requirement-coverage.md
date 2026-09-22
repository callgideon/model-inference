# Requirement coverage and source reconciliation

Read the source sections for background/detail; contracts and durable protocols override conflicts. This map ensures the combined serving/trace program is assigned rather than silently dropped. Named tasks are in [the manifest](tasks.json); required test oracles are in [verification](04-verification.md).

**2026-09-21 amendment:** the table below preserves the original requirement mapping. Mixed IDs resolve through [the complete 45-task mapping](08-platform-split.md). Product scope and current release gates are in [App requirements](../platforms/03-app-spec.md), [Lab requirements](../platforms/05-lab-spec.md) and manifest v3. App uses E3A/I2A/E4; Lab operations use E3L/I2L and observation uses E5L.

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
| Provider dataset/experimentation/training loop | Lab milestones M2–M4 | Explicit roadmap; not a consumer launch dependency or an already decomposed coding task |
| Provider roles, model versions, endpoint publication | L1–L4/A3/D1R | LAB-ACCESS/PUBLISH; customer access grants independent from model ownership |
| Consumer public verified signup and individual grant | A1/A2/D1R/D5 | CREDIT-GRANT/IDENTITY/UNITS and APP-JOURNEY |
| Response caching, speculative/quantized/kernel optimization | Deferred pending evidence and separate consent semantics | M2/W3 parity/capability probes only; do not activate from research estimates |

## Review precedence examples

The old serving implementation spec's §§3–5, 13–14 describe queue state, HTTP modes and rollout before these corrections; use the new durable protocol and task graph. Its Lua/DDL/pseudocode remain explanatory. The old traces capture/data-model examples omit active-byte budgeting, durable feedback coordination or all deduplicating reads; use T/D/C tasks. Historical “all requests have a trace” assertions are now conditional on trace eligibility and capture outcome. Feedback and judge acceptance require durable evidence, not successful enqueue into a volatile process queue.

## Verification log

- 2026-09-20: Assigned source requirement families to tasks/tests and explicitly identified deferred scope. Detailed source test-ID mapping is E2's implementation deliverable.

- 2026-09-21: Amended for separate consumer App/provider Lab, individual signup credits and independent release gates; see the platform-split review. Implementation evidence on the other system remains unverified here.

- 2026-09-21 wave-2 audit: manifest v3 adds F2R/F2P/D1R/C0/I0/E2R/G1R/V1M/U1R. Imported baseline completions are retained; the audit and revision briefs map current repair/integration gates. Earlier unverified-remote statements apply only to the pre-pull review.
