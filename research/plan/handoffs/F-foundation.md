# F — Foundation and shared contracts

## Current State Summary

Implementation has not started in this documentation package. Create a stable seam around existing behavior, then executable contracts so twelve feature tracks can work independently. Start from the coordinator-assigned committed base, inspect newer evidence, and claim exactly one task below.

## Important Context

Read the [accepted scope](../00-decisions-and-scope.md), [contracts](../01-contracts.md), [durable protocols](../02-durable-protocols.md), [worktree rules](../03-execution-protocol.md) and [test oracles](../04-verification.md) before editing. These override conflicting historical examples. Application implementation belongs to the new session; this handoff does not claim a deployed feature.

## Architecture Overview

Publish AuthContext, NormalizedRequest, price/lease/outcome/envelope types, all ports from contracts v1, fake clock and stable fixtures for Python and TypeScript. Do not implement another queue or ledger while extracting.

## Critical Files

- [apps/infrx-api/gateway.py](../../../apps/infrx-api/gateway.py)
- [apps/infrx-api/tests/test_gateway_auth.py](../../../apps/infrx-api/tests/test_gateway_auth.py)
- [apps/infrx-api/tests/test_inflight.py](../../../apps/infrx-api/tests/test_inflight.py)
- [apps/infrx-api/tests/test_media.py](../../../apps/infrx-api/tests/test_media.py)
- [apps/app/package.json](../../../apps/app/package.json)

## Files Modified

No application files were modified when this handoff was authored. Planned ownership for this track (paths may not exist until implementation):

- apps/infrx-api/gateway.py (extraction only)
- apps/infrx-api/infrx/ (initial scaffolding only; transfer feature ownership at F2)
- apps/infrx-api/tests/contracts/
- apps/app/tests/contracts/
- shared package/dependency/test configuration (through coordinator)

Shared composition roots, global types/manifests/locks and navigation remain coordinator-owned after foundation. Feature tests belong to this track; root integration tests belong to E. Request shared changes with exact imports/config needed.

## Immediate Next Steps

1. Inspect the manifest and claim one eligible task with the coordinator; record base SHA and dependency evidence.
2. Create an isolated worktree per the execution protocol, then read the listed source files and relevant task below.
3. Write/run the specified failure regression, implement the smallest complete unit, and return evidence plus a reviewable commit.

## Decisions Made

Create a stable seam around existing behavior, then executable contracts so twelve feature tracks can work independently. Use the shared contracts even when developing with fakes; no private replacement for another track’s state/service boundary.

## Assumptions Made

Start dependencies have been integrated before coding. Integration dependencies may be replaced with contract fakes only during development. Environment defaults are provisional until measured; cloud/GPU/provider tests require an allocated environment and secrets supplied outside the repository.

## Potential Gotchas

Existing cache bounds and non-stream cleanup are already fixed. Preserve externally visible behavior in F1; security behavior changes belong to G/M. The Python name queue shadows the standard library: use infrx/scheduling. Console test discovery currently misses nested suites.

## Task breakdown

### F1 — Extract current gateway behind an application factory

**Start after:** none; independently ready. **Integrate after:** its start dependencies; no additional external module dependency.

**Implementation:** Inventory imports and behavior with the current 23 tests. Move auth, request validation, media and usage boundaries into named modules with a compatibility gateway entry point. Inject clients/clock/config, preserve deployment import path and isolate side effects from import. Keep extraction small enough to compare old/new fixtures.

**Acceptance:** Existing cases and deployment import smoke pass; no new runtime service dependency; a rollback can restore the original entrypoint without migration.

**Required test oracles:** F-BASE.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### F2 — Freeze typed contracts, pins and executable fixtures

**Start after:** F1. **Integrate after:** its start dependencies; no additional external module dependency.

**Implementation:** Encode contracts v1 and serialized success/error/job/feedback/decimal fixtures. Define exact configuration names and concrete bounded capacities, including journal byte reservations, task-local ports and schema versions. Add fake adapters for each port with deterministic failure injection. Pin Python and engine-independent dependencies; update recursive console test discovery and central CI commands through the coordinator. Document any design refinement before consumers branch.

**Acceptance:** Every port has an executable conformance fixture; nested console tests are demonstrably discovered; a clean checkout runs the baseline and contract suites without cloud secrets.

**Required test oracles:** F-CONTRACT, F-BASE.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

## Closed-loop verification

Use the test IDs above as explicit pass/fail oracles, not checklist prose. Unit tests prove local behavior; shared contract fixtures prove interchangeability; real service tests prove integration. Exercise failure paths as well as success, retain a failing reproduction, fix in the owning module and rerun affected cases.

Focused suites: foundation contract/baseline suites for F; isolated integration and allocated deployment drills for E/I.

After F2 establishes discovery, run the focused suite and relevant contract suite. Also run the existing Python baseline for gateway/runtime changes; console tests and lint for console changes; full console build for integrated UI/runtime changes. Record exact executable commands from the pinned environment in evidence. Unavailable external tests are pending, not skipped passes.

## Integration and rollback

Submit only owned paths. Coordinator handles shared wiring and migration order, then runs dependent suites on the merged SHA. Follow additive schema and drain/fence rollback rules; never bypass accounting or discard jobs as a shortcut. Feature-disable may stop new activity but must preserve existing durable state and retention obligations.

## Copyable Claude Opus 5 prompt

```text
Implement one eligible task from research/plan/handoffs/F-foundation.md in an isolated worktree. Read CLAUDE.md, HANDOFF.md and the handoff's shared-contract links first. Inspect the current checkout and newer coordinator evidence. Select the earliest unclaimed task whose start dependencies are integrated; report its ID and base SHA. Follow its owned paths and test oracles, use contract fakes only until integration dependencies exist, and do not modify shared wiring or another module's files independently. Deliver a reviewable commit and task evidence using research/plan/evidence/README.md. Do not claim mock-only work is integrated, run unauthorized live/paid operations, or implement deferred scope. Stop at this task's completed handback; leave later tasks explicit.
```

## Verification log

- 2026-09-20: Authored from reviewed repository/specs and accepted decisions. All tasks remain planned; test IDs are required future evidence.
