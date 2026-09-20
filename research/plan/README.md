# Implementation handoffs — free pilot and traces

Status: **documentation ready; implementation not started**. Prepared 2026-09-20 for independent Claude Opus 5 sessions. This package supersedes conflicting implementation instructions in the original production API and traces research. Historical measurements remain historical measurements; this plan does not certify the live system.

## Start here

Read [the coordinator handoff](COORDINATOR.md), [accepted decisions](00-decisions-and-scope.md), [contracts](01-contracts.md), and [durable protocols](02-durable-protocols.md). Then select exactly one task from [the task manifest](tasks.json) and its module handoff. Follow [worktree and integration rules](03-execution-protocol.md); attach [verification evidence](04-verification.md) before handing it back.

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
| V | [Trace UI](handoffs/V-trace-ui.md) | Trace list/detail and feedback/judge presentation | F2 |
| I | [Infrastructure](handoffs/I-infrastructure.md) | Pilot deployment, observability, later fleet | Inventory immediately |
| E | [Verification](handoffs/E-verification.md) | Corpus, harness, adversarial integration, release evidence | Corpus immediately |

F is a short prerequisite track; the other twelve can then develop against the same fakes and interfaces. Development readiness does not imply merge or deployment readiness. Dependencies in the manifest are **implementation-start dependencies**; each task also lists **integration dependencies** that must pass before integrated status. Mock tests alone cannot satisfy those dependencies.

## Delivery sequence

1. F1 extraction, E1 benchmark/corpus preparation, I1 read-only inventory run independently.
2. Merge F2 contracts and test discovery. Start twelve isolated module tracks; D is the critical path.
3. Integrate real admission, media, scheduling, worker, journal and gateway. Integrate traces and console independently, then feedback/judge coordination.
4. E3 verifies cross-module failure boundaries; I2/I3 and E4 produce single-GPU pilot evidence.
5. I4 fleet rollout is separately gated after pilot acceptance. Payments, second-owner workflow, OpenRouter listing and distillation remain later work.

See [risks and review dispositions](05-risk-register.md) for the changes made after review. No elapsed-time promise substitutes for a passed gate.

The [contracts v1 encoding](08-contracts-v1-encoding.md) fixes the layout, vocabulary, configuration names, dependency set and test discovery that F2 implements. The [database map](06-database-map.md) defines persistence keys and role boundaries. The [requirement coverage map](07-requirement-coverage.md) connects the source specs to implementation tasks and explicitly deferred work.

## Verification log

- 2026-09-20: Derived from repository review and explicit user decisions. Created documentation-only implementation package; planned tests and live gates are not reported as passed.
- 2026-09-20: Implementation coordination started ([session 01](evidence/coordinator/2026-09-20-session-01.md)); added the contracts v1 encoding refinement for F2. No task is marked integrated by this entry.
