# Implementation handoffs — consumer App and provider Lab

**Current review (2026-09-24):** [Consumer v1 readiness audit and proposed priorities](21-v1-consumer-readiness-review-2026-09-24.md), code reviewed at `d7dc3690` and refreshed with main `726d004d` acceptance evidence. The durable backend is implemented and a metered pilot is reported deployed; final certification and consumer App completion remain open. Backend acceptance still precedes App release, then Lab.

**Current start:** [As-built handoff 20, §14](20-platform-handoff-2026-09-24.md), [operational tail](../../HANDOFF-20260924T2115Z.md), then [review 21](21-v1-consumer-readiness-review-2026-09-24.md). The [complete build plan](12-complete-build-plan.md), [task ledger](17-task-ledger.md) and [pending inputs](15-pending-inputs.md) retain the full App/Lab scope. The older [session prompt](16-fresh-session-handoff.md) is historical dispatch context; do not restart completed runtime tasks from it.

Status: **wave-3 backend work and later runtime repairs are on main; E1B/E4B final release evidence remains open.** CREDIT/public onboarding and real consumer console integration are pending. The [wave-2 audit](10-wave2-platform-audit.md) and its [verification](evidence/wave2-platform-audit.md) remain historical evidence, not the current implementation boundary.

## Start here

Read the current start documents above before the [complete build plan](12-complete-build-plan.md), [revision briefs](11-wave3-revision-handoffs.md), [product architecture](../platforms/README.md), [implementation impact](08-platform-split.md) and [amendment briefs](09-amendment-workstreams.md), then [accepted decisions](00-decisions-and-scope.md), [contracts](01-contracts.md) and [durable protocols](02-durable-protocols.md). Reconcile current work before selecting one active task from [manifest v4](tasks.json). Follow [worktree rules](03-execution-protocol.md); attach [verification evidence](04-verification.md). Six old mixed tasks are superseded; their IDs remain for mapping prior work. The sequence below describes the program, not a claim that its earlier stages are still unimplemented.

| Track | Handoff | Owns | Earliest useful work |
|---|---|---|---|
| F | [Foundation](handoffs/F-foundation.md) | Extraction, shared contracts, test discovery | Immediately |
| D | [Durable state](handoffs/D-durable-state.md) | PostgreSQL migrations, jobs, holds, journal, coordination | F2 |
| M | [Media](handoffs/M-media.md) | Secure ingestion, preprocessing, uploads | F2 |
| Q | [Scheduling](handoffs/Q-scheduling.md) | Queue indices, fairness, rebuilding | F2 |
| W | [Worker](handoffs/W-worker.md) | Engine lifecycle, execution fencing, cancellation | F2 |
| G | [Gateway](handoffs/G-gateway.md) | Authentication, public routes, SSE relay | F2 |
| T | [Traces](handoffs/T-traces.md) | Bounded capture, spool, projections, retention | F2 |
| J | [Judge](handoffs/J-judge.md) | Evaluation workflow, calibration, provider adapter | F2 |
| C | [Console services](handoffs/C-console-services.md) | Tenant-safe repositories and server actions | F2 |
| U | [Administration UI](handoffs/U-administration-ui.md) | Usage, balances, keys, settings, operator pages | F2 |
| V | [Trace UI](handoffs/V-trace-ui.md) | Provider trace/detail/review/judge UI in apps/lab | Amended F2; integrate Lab access/shell |
| I | [Infrastructure](handoffs/I-infrastructure.md) | Pilot deployment, observability, later fleet | Inventory immediately |
| E | [Verification](handoffs/E-verification.md) | Corpus, harness, adversarial integration, release evidence | Corpus immediately |
| S | [Reconciliation](09-amendment-workstreams.md) | Remote state, amended contracts, shared wiring | Immediately |
| A | [Consumer additions](09-amendment-workstreams.md) | Signup/onboarding, catalog/rates/docs; grant transaction D-owned | Amended F2; D1 for A1 |
| L | [Lab foundation](09-amendment-workstreams.md) | Provider shell/roles, model and endpoint workflows | Amended F2 |

F/S establish the revised common contracts. Independent tracks then develop against committed fixtures; tasks sharing owned paths remain serialized within their track. Development readiness does not imply merge or deployment readiness. Start dependencies require committed/reviewed code and fixtures; real integration dependencies require actual adapter evidence. New F2R/F2P/D1R gates must pass before their consumers start. Mock tests alone cannot satisfy integration dependencies.

## Delivery sequence

1. Review/commit S1 audit; retain F1/F2/D1 and all wave-2 history. F2R closes carryovers while I0, E2R and S2M can work independently.
2. F2P encodes the revised contract; D1R adds migrations after 0005. D/A1 and G6B supply shared headless identities/keys/rates/accounting; C0 browser reporting follows backend acceptance.
3. Parallel runtime D/M/Q/W/G tasks use revised fixtures; integrate real durable/runtime prerequisites before G2 mounts anything. I0 is mandatory. E3B proves the endpoint independently of App/Lab.
4. I2B/I3B/E1B → M4/W4 → E4B proves backend deployment, recovery and measured optimization. App follows using E3A/I2A/I3/E4 as frontend delta gates; later L1–L4/E3L/I2L and V/T/J/C prove provider operation/observation.
5. After the App candidate is accepted, activate Lab milestones: F3/D7/N/H/B/I5/E6L for evaluation, D8/N3/P/I6/E7L for improvement, D9/R/I7/E8L for rollout. [Detailed briefs](13-lab-improvement-handoffs.md) and [conditional expansion gates](14-expansion-gates.md) replace the earlier undecomposed roadmap. Speech remains deferred. No old wave-3 linear sequence overrides the manifest.

See [risks and review dispositions](05-risk-register.md) for the changes made after review. No elapsed-time promise substitutes for a passed gate.

The [contracts v1 encoding](08-contracts-v1-encoding.md) fixes the layout, vocabulary, configuration names, dependency set and test discovery that F2 implements. The [database map](06-database-map.md) defines persistence keys and role boundaries. The [requirement coverage map](07-requirement-coverage.md) connects the source specs to implementation tasks and explicitly deferred work.

## Verification log

- 2026-09-20: Derived from repository review and explicit user decisions. Created documentation-only implementation package; planned tests and live gates are not reported as passed.
- 2026-09-20: Implementation coordination started ([session 01](evidence/coordinator/2026-09-20-session-01.md)); added the contracts v1 encoding refinement for F2. No task is marked integrated by this entry.
- 2026-09-21: Status line updated; F1, F2, E1 and I1 are integrated on `claude/infrx-impl` (gate G0), nothing is deployed or live-verified.
- 2026-09-21: Wave 2 complete — all eleven module tasks (D1, M1, Q1, W1, G1, T1, J1, C1, U1, V1, E2) reviewed and merged on `claude/infrx-impl`; stage review S2 `pass`; D1 `integrated` (real PostgreSQL, both images), the other ten `implemented` behind fakes or a local real service. Nothing deployed, mounted or applied to a Supabase project. Next: F2.2 then wave 3 per the handoff.

- 2026-09-21: Amended for separate consumer App/provider Lab, individual signup credits and independent release gates; see the platform-split review. Implementation evidence on the other system remains unverified here.

- 2026-09-21: Imported `271add9`, audited wave 2 and reconciled product-v2 revisions in manifest v4; see `10-wave2-platform-audit.md`. Earlier remote-unverified statements are superseded for committed repository work only.
