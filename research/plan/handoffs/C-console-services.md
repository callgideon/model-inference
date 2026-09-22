# C — Console data services and actions

**Current assignment scope:** [complete plan](../12-complete-build-plan.md), [fresh-session handoff](../16-fresh-session-handoff.md) and manifest v4. Preserve these module algorithms subject to the audited revisions. Dispatch App launch work first; later Lab or conditional tasks require their milestone activation.

> **2026-09-21 amendment:** Read [platform split](../08-platform-split.md), [new task briefs](../09-amendment-workstreams.md) and [manifest v4](../tasks.json) before this brief. They supersede conflicting paths, signup/unit rules and dependencies below. Wave 2 is imported at `271add9`; [audit](../10-wave2-platform-audit.md) and [revision handoffs](../11-wave3-revision-handoffs.md) govern continuation; existing evidence is not reset. C3 is replaced by C3A/C3F/C3L; consumer actions stay in App and provider actions belong in Lab.


## Current State Summary

Wave-2 baseline is audited in `10-wave2-platform-audit.md`; preserve its completed tasks and apply the new revision gates before pending work. This original brief supplies unchanged algorithms, not current progress claims. Give all UI modules one tenant-safe, paginated server boundary with shared financial, feedback and evaluation behavior. Start from the coordinator-assigned committed base, inspect newer evidence, and claim exactly one task below.

## Important Context

Read the [accepted scope](../00-decisions-and-scope.md), [contracts](../01-contracts.md), [durable protocols](../02-durable-protocols.md), [worktree rules](../03-execution-protocol.md) and [test oracles](../04-verification.md) before editing. These override conflicting historical examples. Application implementation belongs to the new session; this handoff does not claim a deployed feature.

## Architecture Overview

Publish typed service interfaces/fixture DTOs first. U/V call these actions; they must not invent alternate auth/storage. Bind tenant from trusted session, not route/filter input. Shared lib types outside feature root go through F/coordinator.

## Critical Files

- [apps/app/lib/session.ts](../../../apps/app/lib/session.ts)
- [apps/app/lib/credits.ts](../../../apps/app/lib/credits.ts)
- [apps/app/app/actions.ts](../../../apps/app/app/actions.ts)
- [apps/app/app/(console)/admin/actions.ts](../../../apps/app/app/(console)/admin/actions.ts)
- [research/traces/07-console-spec.md](../../../research/traces/07-console-spec.md)

## Files Modified

No application files were modified when this handoff was authored. Planned ownership for this track (paths may not exist until implementation):

- apps/app/lib/services/
- apps/app/lib/credits.ts
- apps/app/app/actions.ts (service migration)
- apps/app/app/(console)/api-keys/actions.ts
- apps/app/app/(console)/admin/actions.ts
- apps/app/tests/c/

Shared composition roots, global types/manifests/locks and navigation remain coordinator-owned after foundation. Feature tests belong to this track; root integration tests belong to E. Request shared changes with exact imports/config needed.

## Immediate Next Steps

1. Inspect the manifest and claim one eligible task with the coordinator; record base SHA and dependency evidence.
2. Create an isolated worktree per the execution protocol, then read the listed source files and relevant task below.
3. Write/run the specified failure regression, implement the smallest complete unit, and return evidence plus a reviewable commit.

## Decisions Made

Give all UI modules one tenant-safe, paginated server boundary with shared financial, feedback and evaluation behavior. Use the shared contracts even when developing with fakes; no private replacement for another track’s state/service boundary.

## Assumptions Made

Start dependencies follow manifest v4: reviewed code/fixtures may enable development; new F2R/F2P/D1R gates require acceptance evidence. Integration dependencies may be replaced with contract fakes only during development. Environment defaults are provisional until measured; cloud/GPU/provider tests require an allocated environment and secrets supplied outside the repository.

## Potential Gotchas

Current credits fetch/sum entire ledger; replace with reconciled summary plus pagination. chQuery parameter merging must not let callers override bound org. Service-role key bypasses RLS, so every server operation still needs explicit checks.

## Task breakdown

### C1 — Typed repositories, pagination and tenant query boundary

**Start after:** F2. **Integrate after:** D1, C0.

**Imported baseline status:** `implemented` at wave 2; preserve completed work. Product revision/integration follow-up: `F2R`, `F2P`, `C0`. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Implement server-only sessions/role checks and named PG/CH queries with bounded cursor pagination/filter allowlists. Reserve tenant parameter names; bind trusted tenant after validating user filters. Provide matching fixtures for U/V. Migrate credits to wallet/hold views and test current selected-org behavior without adding org switching.

**Acceptance:** Cross-org IDs/parameters and direct service invocation fail; large ledger/trace lists paginate; balance includes reservations; no secret/query client bundle exposure.

**Required test oracles:** TRACE-TENANT, DUR-RLS, CONSOLE-FLOWS.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### C2 — Content access, expiry and safe signed references

**Start after:** F2P, C0. **Integrate after:** M3, T3, L2. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Resolve owned trace metadata before object ref; check prefix/object metadata and logical expiry, issue short-lived bounded signed access. Support metadata-only, lost-content, expired and pending projection states. Normalize renderable text safely; never accept raw storage paths.

**Acceptance:** Forged row refs/tenant params cannot expose objects; expired content denied despite physical presence; missing content is understandable and does not crash page.

**Required test oracles:** TRACE-TENANT, CONSOLE-FLOWS.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### C3 — Shared key/settings/grant/feedback/judge actions

**Scheduling:** Retired mixed task. Use `C3A`, `C3F`, `C3L` from the amendment briefs. The algorithm below is historical reference.

**Implementation:** Move mutations into service boundary; preserve role separation, idempotent operator grants with reasons, key revocation and trace/retention/evaluation settings validation. Delegate feedback and judge state to D/J rather than writing CH directly. Revalidate affected pages and provide typed failure DTOs.

**Acceptance:** Direct unauthorized actions fail; duplicate submissions do not double grant or judge; owner privacy changes enforce caps and current consent; customer console feedback retains customer role.

**Required test oracles:** FEEDBACK-ACK, JUDGE-BUDGET, DUR-RLS, CONSOLE-FLOWS.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

## Closed-loop verification

Use the test IDs above as explicit pass/fail oracles, not checklist prose. Unit tests prove local behavior; shared contract fixtures prove interchangeability; real service tests prove integration. Exercise failure paths as well as success, retain a failing reproduction, fix in the owning module and rerun affected cases.

Planned focused suite: apps/app/tests/c/

After F2 establishes discovery, run the focused suite and relevant contract suite. Also run the existing Python baseline for gateway/runtime changes; console tests and lint for console changes; full console build for integrated UI/runtime changes. Record exact executable commands from the pinned environment in evidence. Unavailable external tests are pending, not skipped passes.

## Integration and rollback

Submit only owned paths. Coordinator handles shared wiring and migration order, then runs dependent suites on the merged SHA. Follow additive schema and drain/fence rollback rules; never bypass accounting or discard jobs as a shortcut. Feature-disable may stop new activity but must preserve existing durable state and retention obligations.

## Copyable Claude Opus 5 prompt

```text
Implement one eligible task from research/plan/handoffs/C-console-services.md in an isolated worktree. Read CLAUDE.md, HANDOFF.md and the handoff's shared-contract links first. Inspect the current checkout and newer coordinator evidence. Select the earliest unclaimed task whose start dependencies are integrated; report its ID and base SHA. Follow its owned paths and test oracles, use contract fakes only until integration dependencies exist, and do not modify shared wiring or another module's files independently. Deliver a reviewable commit and task evidence using research/plan/evidence/README.md. Do not claim mock-only work is integrated, run unauthorized live/paid operations, or implement deferred scope. Stop at this task's completed handback; leave later tasks explicit.
```

## Verification log

- 2026-09-20: Authored from reviewed repository/specs and accepted decisions. All tasks remain planned; test IDs are required future evidence.
