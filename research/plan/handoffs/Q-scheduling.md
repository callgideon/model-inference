# Q — Scheduling indices and fair dispatch

**Current assignment scope:** [complete plan](../12-complete-build-plan.md), [fresh-session handoff](../16-fresh-session-handoff.md) and manifest v4. Preserve these module algorithms subject to the audited revisions. Dispatch App launch work first; later Lab or conditional tasks require their milestone activation.

> **2026-09-21 amendment:** Read [platform split](../08-platform-split.md), [new task briefs](../09-amendment-workstreams.md) and [manifest v4](../tasks.json) before this brief. They supersede conflicting paths, signup/unit rules and dependencies below. Wave 2 is imported at `271add9`; [audit](../10-wave2-platform-audit.md) and [revision handoffs](../11-wave3-revision-handoffs.md) govern continuation; existing evidence is not reset.


## Current State Summary

Wave-2 baseline is audited in `10-wave2-platform-audit.md`; preserve its completed tasks and apply the new revision gates before pending work. This original brief supplies unchanged algorithms, not current progress claims. Provide bounded fair candidate selection while PostgreSQL owns admission and execution truth. Start from the coordinator-assigned committed base, inspect newer evidence, and claim exactly one task below.

## Important Context

Read the [accepted scope](../00-decisions-and-scope.md), [contracts](../01-contracts.md), [durable protocols](../02-durable-protocols.md), [worktree rules](../03-execution-protocol.md) and [test oracles](../04-verification.md) before editing. These override conflicting historical examples. Application implementation belongs to the new session; this handoff does not claim a deployed feature.

## Architecture Overview

Scheduler implements identical memory and Valkey interfaces. Takes durable outbox events; returns candidates for fenced JobStore.claim. Publish queue age/depth estimates without pretending index depth is admission capacity.

## Critical Files

- [research/production-api/03-request-handling-and-queueing.md](../../../research/production-api/03-request-handling-and-queueing.md)
- [research/production-api/10-implementation-spec.md](../../../research/production-api/10-implementation-spec.md)

## Files Modified

No application files were modified when this handoff was authored. Planned ownership for this track (paths may not exist until implementation):

- apps/infrx-api/infrx/scheduling/
- apps/infrx-api/tests/q/

Shared composition roots, global types/manifests/locks and navigation remain coordinator-owned after foundation. Feature tests belong to this track; root integration tests belong to E. Request shared changes with exact imports/config needed.

## Immediate Next Steps

1. Inspect the manifest and claim one eligible task with the coordinator; record base SHA and dependency evidence.
2. Create an isolated worktree per the execution protocol, then read the listed source files and relevant task below.
3. Write/run the specified failure regression, implement the smallest complete unit, and return evidence plus a reviewable commit.

## Decisions Made

Provide bounded fair candidate selection while PostgreSQL owns admission and execution truth. Use the shared contracts even when developing with fakes; no private replacement for another track’s state/service boundary.

## Assumptions Made

Start dependencies follow manifest v4: reviewed code/fixtures may enable development; new F2R/F2P/D1R gates require acceptance evidence. Integration dependencies may be replaced with contract fakes only during development. Environment defaults are provisional until measured; cloud/GPU/provider tests require an allocated environment and secrets supplied outside the repository.

## Potential Gotchas

Old Lua snippets are design sketches, not drop-in scripts: key namespaces, declared keys, byte caps and finish-time updates need tests. In-process scheduling still requires production PostgreSQL. Valkey asynchronous replication is not a no-loss durable queue.

## Task breakdown

### Q1 — Deterministic memory scheduler and fairness model

**Start after:** F2. **Integrate after:** none beyond the start/code gate.

**Imported baseline status:** `implemented` at wave 2; preserve completed work. Product revision/integration follow-up: `F2R`, `Q2`. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Implement per-org weighted fair selection, tie-breaking, finish-time advancement at dispatch, queued-item count/bytes and cancellation removal. Use injected clock and seeded workload fixtures. Outbox event replay replaces/indexes once; filter candidate eligibility through JobStore.

**Acceptance:** Identical input produces deterministic dispatch; noisy tenant cannot starve bounded peers; empty and cancelled queues do not retain stale fairness state.

**Required test oracles:** F-CONTRACT, DUR-OUTBOX.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### Q2 — Valkey adapter with atomic tested scripts

**Start after:** Q1, F2P. **Integrate after:** none beyond the start/code gate. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Port the tested model to namespaced keys/scripts with all accessed keys declared, complete byte/count accounting and atomic transitions. Pin supported server version and persistence expectations; verify script behavior with real Valkey and randomized differential tests versus Q1.

**Acceptance:** Both adapters satisfy the same contract and fairness properties; scripts survive repeated enqueue/dequeue/ack and isolated namespaces without corrupting capacity truth.

**Required test oracles:** F-CONTRACT, DUR-OUTBOX.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### Q3 — Outbox/reconciler integration and index loss recovery

**Start after:** Q2, F2P. **Integrate after:** D2, D3. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Implement outbox drain/ack with retry and bounded scan checkpoints; rebuild from PG nonterminal snapshots, clean dead candidates and expose lag/missing-index metrics. Reconcile concurrently with normal dispatch without duplicate execution. Switch adapters only through coordinator drain/rebuild protocol.

**Acceptance:** Flush/restart Valkey during queued/running traffic: every accepted job reaches a valid terminal or still-owned active state; stale candidates never acquire a second lease.

**Required test oracles:** DUR-OUTBOX, DUR-FENCE.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

## Closed-loop verification

Use the test IDs above as explicit pass/fail oracles, not checklist prose. Unit tests prove local behavior; shared contract fixtures prove interchangeability; real service tests prove integration. Exercise failure paths as well as success, retain a failing reproduction, fix in the owning module and rerun affected cases.

Planned focused suite: apps/infrx-api/tests/q/

After F2 establishes discovery, run the focused suite and relevant contract suite. Also run the existing Python baseline for gateway/runtime changes; console tests and lint for console changes; full console build for integrated UI/runtime changes. Record exact executable commands from the pinned environment in evidence. Unavailable external tests are pending, not skipped passes.

## Integration and rollback

Submit only owned paths. Coordinator handles shared wiring and migration order, then runs dependent suites on the merged SHA. Follow additive schema and drain/fence rollback rules; never bypass accounting or discard jobs as a shortcut. Feature-disable may stop new activity but must preserve existing durable state and retention obligations.

## Copyable Claude Opus 5 prompt

```text
Implement one eligible task from research/plan/handoffs/Q-scheduling.md in an isolated worktree. Read CLAUDE.md, HANDOFF.md and the handoff's shared-contract links first. Inspect the current checkout and newer coordinator evidence. Select the earliest unclaimed task whose start dependencies are integrated; report its ID and base SHA. Follow its owned paths and test oracles, use contract fakes only until integration dependencies exist, and do not modify shared wiring or another module's files independently. Deliver a reviewable commit and task evidence using research/plan/evidence/README.md. Do not claim mock-only work is integrated, run unauthorized live/paid operations, or implement deferred scope. Stop at this task's completed handback; leave later tasks explicit.
```

## Verification log

- 2026-09-20: Authored from reviewed repository/specs and accepted decisions. All tasks remain planned; test IDs are required future evidence.
