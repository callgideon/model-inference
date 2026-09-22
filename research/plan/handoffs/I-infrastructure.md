# I — Infrastructure and operations

**Current assignment scope:** [complete plan](../12-complete-build-plan.md), [fresh-session handoff](../16-fresh-session-handoff.md) and manifest v4. Preserve these module algorithms subject to the audited revisions. Dispatch App launch work first; later Lab or conditional tasks require their milestone activation.

> **2026-09-21 amendment:** Read [platform split](../08-platform-split.md), [new task briefs](../09-amendment-workstreams.md) and [manifest v4](../tasks.json) before this brief. They supersede conflicting paths, signup/unit rules and dependencies below. Wave 2 is imported at `271add9`; [audit](../10-wave2-platform-audit.md) and [revision handoffs](../11-wave3-revision-handoffs.md) govern continuation; existing evidence is not reset. I2 is split into I2A/I2L; use independent App/Lab gates.


## Current State Summary

Wave-2 baseline is audited in `10-wave2-platform-audit.md`; preserve its completed tasks and apply the new revision gates before pending work. This original brief supplies unchanged algorithms, not current progress claims. Deploy the integrated pilot reproducibly, prove recovery, and keep fleet rollout behind its own evidence gate. Start from the coordinator-assigned committed base, inspect newer evidence, and claim exactly one task below.

## Important Context

Read the [accepted scope](../00-decisions-and-scope.md), [contracts](../01-contracts.md), [durable protocols](../02-durable-protocols.md), [worktree rules](../03-execution-protocol.md) and [test oracles](../04-verification.md) before editing. These override conflicting historical examples. Application implementation belongs to the new session; this handoff does not claim a deployed feature.

## Architecture Overview

Deploy pinned artifacts and coordinator-approved environment configs; D owns migration code, T owns ClickHouse schema, W owns model serve script, E owns local test compose. No concurrent production mutation by individual modules.

## Critical Files

- [HANDOFF.md](../../../HANDOFF.md)
- [apps/infrx-api/deploy/install.sh](../../../apps/infrx-api/deploy/install.sh)
- [research/production-api/02-aws-architecture-options.md](../../../research/production-api/02-aws-architecture-options.md)
- [research/production-api/07-reliability-observability-operations.md](../../../research/production-api/07-reliability-observability-operations.md)
- [research/cross-cutting/cloud-pricing.md](../../../research/cross-cutting/cloud-pricing.md)

## Files Modified

No application files were modified when this handoff was authored. Planned ownership for this track (paths may not exist until implementation):

- infra/ (except clickhouse owned T)
- apps/infrx-api/deploy/
- research/plan/evidence/i/

Shared composition roots, global types/manifests/locks and navigation remain coordinator-owned after foundation. Feature tests belong to this track; root integration tests belong to E. Request shared changes with exact imports/config needed.

## Immediate Next Steps

1. Inspect the manifest and claim one eligible task with the coordinator; record base SHA and dependency evidence.
2. Create an isolated worktree per the execution protocol, then read the listed source files and relevant task below.
3. Write/run the specified failure regression, implement the smallest complete unit, and return evidence plus a reviewable commit.

## Decisions Made

Deploy the integrated pilot reproducibly, prove recovery, and keep fleet rollout behind its own evidence gate. Use the shared contracts even when developing with fakes; no private replacement for another track’s state/service boundary.

## Assumptions Made

Start dependencies follow manifest v4: reviewed code/fixtures may enable development; new F2R/F2P/D1R gates require acceptance evidence. Integration dependencies may be replaced with contract fakes only during development. Environment defaults are provisional until measured; cloud/GPU/provider tests require an allocated environment and secrets supplied outside the repository.

## Potential Gotchas

Historical live inventory may be stale. GPU is east1 while PG is east2: journal RTT is a launch risk. Instance-store NVMe is not durable spool. ALB least-outstanding-requests cannot use slow start. Valkey recovery must rebuild PG truth.

## Task breakdown

### I1 — Read-only inventory and deploy design

**Start after:** none. **Integrate after:** none beyond the start/code gate.

**Imported baseline status:** `integrated` at wave 2; preserve completed work. Product revision/integration follow-up: `I0`. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Verify repository/deployment versions, resource topology, IAM/secret references and storage durability without printing secret values. Inventory required vs existing resources and priced estimates; include DB/journal network latency probe design, backup needs and allocated test environments. Do not create resources in this task.

**Acceptance:** Report clearly separates observed resources, historical claims and proposed changes; every mutable operation has an assigned later task and environment.

**Required test oracles:** OPS-RECOVER.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### I2 — Reproducible single-GPU deployment

**Scheduling:** Retired mixed task. Use `I2A`, `I2L` from the amendment briefs. The algorithm below is historical reference.

**Implementation:** Write idempotent provisioning/deploy config for durable payload/results/spool, PG access, observability/CH and secret injection. Add explicit dev/pilot mode, process isolation, readiness and migration ordering; choose engine/artifact pins from W. Deploy only in allocated scope with coordinator lock. Verify east1-east2 journal latency before release claims.

**Acceptance:** Fresh allocated environment reproduces pilot; durable state survives service restart; no secrets in repo/logs; deployment cannot accidentally start unauthenticated or unmetered mode.

**Required test oracles:** OPS-RECOVER, PERF-PILOT.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### I3 — Recovery, alarms and rollback runbooks

**Start after:** I2A, F2P. **Integrate after:** E3A. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Exercise backup restore, spool recovery, object expiry, disk/queue/budget/unknown-use alerts and planned maintenance. Record RPO/RTO measured per durable layer; test compatible rollback with admission paused and jobs drained/fenced. Supply operational dashboards from actual exported metrics.

**Acceptance:** A new operator can execute recovery with artifact/version references; restored ledger reconciles; rollback never bypasses holds or drops accepted jobs.

**Required test oracles:** OPS-RECOVER, TRACE-RECOVER, DUR-OUTBOX.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### I4 — Separately gated fleet deployment

**Start after:** I3, E4, F2P. **Integrate after:** none beyond the start/code gate. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Implement multiworker routing/scaling/AMI and capacity plan from measured pilot data, with staged rollout/drain and failover evidence. If ALB least-outstanding-requests is selected, slow-start remains0. Use Valkey only as index with PG reconciliation; evaluate capacity purchases separately, do not purchase from this task alone.

**Acceptance:** FLEET-GATE passes on allocated fleet; queue-index loss and worker loss preserve state invariants; projected cost and measured throughput clearly separated.

**Required test oracles:** FLEET-GATE, OPS-RECOVER.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

## Closed-loop verification

Use the test IDs above as explicit pass/fail oracles, not checklist prose. Unit tests prove local behavior; shared contract fixtures prove interchangeability; real service tests prove integration. Exercise failure paths as well as success, retain a failing reproduction, fix in the owning module and rerun affected cases.

Focused suites: foundation contract/baseline suites for F; isolated integration and allocated deployment drills for E/I.

After F2 establishes discovery, run the focused suite and relevant contract suite. Also run the existing Python baseline for gateway/runtime changes; console tests and lint for console changes; full console build for integrated UI/runtime changes. Record exact executable commands from the pinned environment in evidence. Unavailable external tests are pending, not skipped passes.

## Integration and rollback

Submit only owned paths. Coordinator handles shared wiring and migration order, then runs dependent suites on the merged SHA. Follow additive schema and drain/fence rollback rules; never bypass accounting or discard jobs as a shortcut. Feature-disable may stop new activity but must preserve existing durable state and retention obligations.

## Copyable Claude Opus 5 prompt

```text
Implement one eligible task from research/plan/handoffs/I-infrastructure.md in an isolated worktree. Read CLAUDE.md, HANDOFF.md and the handoff's shared-contract links first. Inspect the current checkout and newer coordinator evidence. Select the earliest unclaimed task whose start dependencies are integrated; report its ID and base SHA. Follow its owned paths and test oracles, use contract fakes only until integration dependencies exist, and do not modify shared wiring or another module's files independently. Deliver a reviewable commit and task evidence using research/plan/evidence/README.md. Do not claim mock-only work is integrated, run unauthorized live/paid operations, or implement deferred scope. Stop at this task's completed handback; leave later tasks explicit.
```

## Verification log

- 2026-09-20: Authored from reviewed repository/specs and accepted decisions. All tasks remain planned; test IDs are required future evidence.
