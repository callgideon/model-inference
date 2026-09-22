# T — Trace capture, projection and retention

**Current assignment scope:** [complete plan](../12-complete-build-plan.md), [fresh-session handoff](../16-fresh-session-handoff.md) and manifest v4. Preserve these module algorithms subject to the audited revisions. Dispatch App launch work first; later Lab or conditional tasks require their milestone activation.

> **2026-09-21 amendment:** Read [platform split](../08-platform-split.md), [new task briefs](../09-amendment-workstreams.md) and [manifest v4](../tasks.json) before this brief. They supersede conflicting paths, signup/unit rules and dependencies below. Wave 2 is imported at `271add9`; [audit](../10-wave2-platform-audit.md) and [revision handoffs](../11-wave3-revision-handoffs.md) govern continuation; existing evidence is not reset. T2 is split into T2I/T2F; capture can be disabled for App launch and is shared infrastructure for Lab.


## Current State Summary

Wave-2 baseline is audited in `10-wave2-platform-audit.md`; preserve its completed tasks and apply the new revision gates before pending work. This original brief supplies unchanged algorithms, not current progress claims. Capture useful opted-in traces without unbounded resource use or making inference depend on the analytics stack. Start from the coordinator-assigned committed base, inspect newer evidence, and claim exactly one task below.

## Important Context

Read the [accepted scope](../00-decisions-and-scope.md), [contracts](../01-contracts.md), [durable protocols](../02-durable-protocols.md), [worktree rules](../03-execution-protocol.md) and [test oracles](../04-verification.md) before editing. These override conflicting historical examples. Application implementation belongs to the new session; this handoff does not claim a deployed feature.

## Architecture Overview

TraceSink provides nonblocking bounded offer and explicit durability/loss counters. Shipper projects PG outbox metadata and durable spool content to ClickHouse/S3. C gets named query/view definitions, never arbitrary SQL from client.

## Critical Files

- [research/traces/01-requirements.md](../../../research/traces/01-requirements.md)
- [research/traces/02-metrics-catalogue.md](../../../research/traces/02-metrics-catalogue.md)
- [research/traces/03-architecture.md](../../../research/traces/03-architecture.md)
- [research/traces/04-data-model.md](../../../research/traces/04-data-model.md)
- [research/traces/05-gateway-capture-spec.md](../../../research/traces/05-gateway-capture-spec.md)

## Files Modified

No application files were modified when this handoff was authored. Planned ownership for this track (paths may not exist until implementation):

- apps/infrx-api/infrx/traces/
- apps/infrx-api/tests/t/
- infra/clickhouse/

Shared composition roots, global types/manifests/locks and navigation remain coordinator-owned after foundation. Feature tests belong to this track; root integration tests belong to E. Request shared changes with exact imports/config needed.

## Immediate Next Steps

1. Inspect the manifest and claim one eligible task with the coordinator; record base SHA and dependency evidence.
2. Create an isolated worktree per the execution protocol, then read the listed source files and relevant task below.
3. Write/run the specified failure regression, implement the smallest complete unit, and return evidence plus a reviewable commit.

## Decisions Made

Capture useful opted-in traces without unbounded resource use or making inference depend on the analytics stack. Use the shared contracts even when developing with fakes; no private replacement for another track’s state/service boundary.

## Assumptions Made

Start dependencies follow manifest v4: reviewed code/fixtures may enable development; new F2R/F2P/D1R gates require acceptance evidence. Integration dependencies may be replaced with contract fakes only during development. Environment defaults are provisional until measured; cloud/GPU/provider tests require an allocated environment and secrets supplied outside the repository.

## Potential Gotchas

An async function still blocks the event loop if it writes synchronously. Count limits alone do not bound large content. ReplacingMergeTree needs valid version types and consumer dedup, not merely a table name. Off traces are absent from CH denominator.

## Task breakdown

### T1 — Byte-budgeted capture and persistent spool

**Start after:** F2. **Integrate after:** none beyond the start/code gate.

**Imported baseline status:** `implemented` at wave 2; preserve completed work. Product revision/integration follow-up: `F2R`, `T2I`. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Implement budgets during active accumulation and queued capture, metadata reserve, whole-content discard on overflow and bounded offer. Use dedicated spool writer with checksummed/versioned segments, fsync state and disk/free-space caps; separate shipping. Capture final canonical logical content plus raw model text, not raw request-wire byte claims.

**Acceptance:** Slow disk and oversized concurrent traces stay within declared memory/disk budgets; inference continues; drop reasons distinguish memory, disk, shutdown and malformed content.

**Required test oracles:** TRACE-BOUNDS, F-CONTRACT.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### T2 — Idempotent analytics and content shipping

**Scheduling:** Retired mixed task. Use `T2I`, `T2F` from the amendment briefs. The algorithm below is historical reference.

**Implementation:** Execute/version real ClickHouse DDL, nonnullable versioning and deduplicating views; implement idempotent object writes, spool segment acknowledgment, outbox projection and poison-record quarantine. Keep accounting metadata separate from optional content. Test CH outage, duplicate events and restart before/after fsync.

**Acceptance:** Only promised fsynced records recover; duplicate delivery leaves one logical trace/feedback/score; metadata arrives independently of content upload; loss metrics use correct eligible counts.

**Required test oracles:** TRACE-RECOVER, FEEDBACK-ACK.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### T3 — Logical retention, deletion and observability

**Start after:** T2I, F2P. **Integrate after:** T2F. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Implement result/content/cache/metadata logical expiry integration, deletion queue and physical-cleanup lag alarms. TTLs cannot expose expired rows. Add capture/spool/projection metrics and dashboards specification for I, including off/minimal/full denominators and bytes. Prove no copytruncate path.

**Acceptance:** At expiry the console/export cannot read content even while CH parts/S3 objects remain; active references respected; cleanup backlog and durability gaps are measurable.

**Required test oracles:** TRACE-TENANT, TRACE-RECOVER, OPS-RECOVER.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

## Closed-loop verification

Use the test IDs above as explicit pass/fail oracles, not checklist prose. Unit tests prove local behavior; shared contract fixtures prove interchangeability; real service tests prove integration. Exercise failure paths as well as success, retain a failing reproduction, fix in the owning module and rerun affected cases.

Planned focused suite: apps/infrx-api/tests/t/

After F2 establishes discovery, run the focused suite and relevant contract suite. Also run the existing Python baseline for gateway/runtime changes; console tests and lint for console changes; full console build for integrated UI/runtime changes. Record exact executable commands from the pinned environment in evidence. Unavailable external tests are pending, not skipped passes.

## Integration and rollback

Submit only owned paths. Coordinator handles shared wiring and migration order, then runs dependent suites on the merged SHA. Follow additive schema and drain/fence rollback rules; never bypass accounting or discard jobs as a shortcut. Feature-disable may stop new activity but must preserve existing durable state and retention obligations.

## Copyable Claude Opus 5 prompt

```text
Implement one eligible task from research/plan/handoffs/T-traces.md in an isolated worktree. Read CLAUDE.md, HANDOFF.md and the handoff's shared-contract links first. Inspect the current checkout and newer coordinator evidence. Select the earliest unclaimed task whose start dependencies are integrated; report its ID and base SHA. Follow its owned paths and test oracles, use contract fakes only until integration dependencies exist, and do not modify shared wiring or another module's files independently. Deliver a reviewable commit and task evidence using research/plan/evidence/README.md. Do not claim mock-only work is integrated, run unauthorized live/paid operations, or implement deferred scope. Stop at this task's completed handback; leave later tasks explicit.
```

## Verification log

- 2026-09-20: Authored from reviewed repository/specs and accepted decisions. All tasks remain planned; test IDs are required future evidence.
