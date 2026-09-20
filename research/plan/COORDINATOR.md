# Coordinator handoff — Claude Opus 5 implementation

## Current State Summary

The user requested detailed implementation handoffs, documentation updates, commit and push; application implementation is delegated to later Claude Opus 5 sessions. This package contains foundation plus twelve module tracks and a machine-readable task dependency graph. All implementation tasks remain planned. Repository analysis baseline is commit `1db98e9`; use the current documentation commit as the initial implementation base after checking for newer changes.

## Important Context

The first release is a free single-GPU pilot using promotional holds/settlement, explicit async, secure URLs/uploads, opt-in traces, feedback and budgeted evaluation. Payments, second-owner commercial launch, OpenRouter and training remain later. User selected continued inference on trace loss and retention of 24h results/7d processing cache/90d optional content/13mo metadata. Production state and account access in root HANDOFF are historical and need read-only revalidation before deployment.

The complete implementation authority is [scope](00-decisions-and-scope.md), [contracts](01-contracts.md), [durable protocols](02-durable-protocols.md), then the assigned module brief. Historical research remains supporting context. Preserve research verification history; append new evidence. No secret values belong in handoffs, logs or commits.

## Immediate Next Steps

1. Read [execution rules](03-execution-protocol.md), [test gates](04-verification.md), [risks](05-risk-register.md) and [manifest](tasks.json).
2. Record a coordinator integration SHA and assign F1, E1 and I1 to distinct isolated worktrees; they are the only immediately independent starting tasks. I1 is read-only inventory; E1 is corpus/benchmark tooling, not a production load test.
3. Integrate F1 then F2, run shared contract/baseline tests and publish the new base. Only then dispatch module tasks whose start dependencies are met.
4. Review each handback with its integration dependencies and tests before merging; publish evidence and unblock successors. Twelve-track development does not mean twelve simultaneous live deployments.

## Architecture Overview

PostgreSQL owns durable jobs, holds, leases, stream journal, usage, feedback and evaluation coordination. Valkey/memory scheduling is an index. Media and result objects are immutable, tenant scoped and expiry controlled. Stream output is persisted before relay, terminal success after settlement. Trace capture is bounded and may drop with metrics; fsynced local spool is its durability boundary. ClickHouse is a deduplicated projection. Console services centralize tenant/role checks; UI work can run against shared fixtures.

## Critical Files

- [Root handoff](../../HANDOFF.md): historical live inventory and source map.
- [Project conventions](../../CLAUDE.md): source/research and implementation rules.
- [Package index](README.md): tracks and delivery sequence.
- [Task manifest](tasks.json): start and integration dependencies, owners, tests and planned statuses.
- [Evidence template](evidence/README.md): mandatory implementation handback content.

## Files Modified

This preparation changes only Markdown documentation and the documentation task manifest: the new plan package, root handoff/conventions/index, and relevant research/application spec supersession notices. No runtime code, migrations, dependencies, infrastructure resources or implementation worktrees were created. An old plaintext development-account password was removed from the current handoff; no credential value is copied into this package.

## Decisions Made

User scope decisions are recorded as DEC-01 through DEC-08. The review resolves durability in favor of PostgreSQL authority and makes async opt-in explicit. Worktree boundaries keep migrations with D, server actions with C, ClickHouse schema with T and integration tests with E. Foundation freezes interfaces first. Shared composition, dependency locks and navigation have one coordinator owner to reduce merge conflicts.

## Assumptions Made

Later sessions receive this repository and assigned task/environment, with credentials delivered separately. Local fake and real-service tests precede allocated cloud/GPU work. Current live inventory, cloud prices and provider capabilities may have changed; implementation owners verify them before relying on them. Performance limits in the plan are engineering defaults until measured.

## Potential Gotchas

Do not treat historical A0 fixes as new work: several already exist. Do not change to202 on ordinary chat overload, regenerate after committed output, settle unknown usage from chunk counts, or revert to an unmetered runtime. Do not use trace consent as judge consent or console origin as operator provenance. The DB region differs from the GPU region; journal latency is a pilot gate. Old research code examples are illustrative where this package supersedes them.

## Verification and next-session prompt

Planning observed 23 Python tests, 4 console tests and console lint passing before this documentation work; these are baseline observations only. Documentation validation is recorded separately in [the preparation report](evidence/documentation-review.md). Future tests, live fault drills and rollout gates remain unexecuted.

```text
Act as implementation coordinator for this repository. Read CLAUDE.md, HANDOFF.md and research/plan/README.md, then COORDINATOR.md, shared contracts, durable protocols, execution rules and tasks.json. Check for newer integrated evidence and record the starting SHA. Assign independent F1/E1/I1 work first, in separate worktrees, and freeze F2 contracts before broad parallel implementation. Keep a single owner for shared wiring and dependency files. Enforce task-specific test oracles and real integration dependencies; publish evidence per research/plan/evidence/README.md. Do not expand deferred scope or claim live verification from mocks. Coordinate only the work and environments explicitly assigned in this session.
```

## Verification log

- 2026-09-20: Completed from session-handoff scaffold and repository review for external implementation sessions; application work remains planned.
