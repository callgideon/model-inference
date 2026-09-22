# U — Usage, balances, keys and administration UI

> **2026-09-21 amendment:** Read [platform split](../08-platform-split.md), [new task briefs](../09-amendment-workstreams.md) and [manifest v3](../tasks.json) before this brief. They supersede conflicting paths, signup/unit rules and dependencies below. Wave 2 is imported at `271add9`; [audit](../10-wave2-platform-audit.md) and [revision handoffs](../11-wave3-revision-handoffs.md) govern continuation; existing evidence is not reset.


## Current State Summary

Wave-2 baseline is audited in `10-wave2-platform-audit.md`; preserve its completed tasks and apply the new revision gates before pending work. This original brief supplies unchanged algorithms, not current progress claims. Make pilot consumption, limits and controls understandable while preserving the existing console design and organization model. Start from the coordinator-assigned committed base, inspect newer evidence, and claim exactly one task below.

## Important Context

Read the [accepted scope](../00-decisions-and-scope.md), [contracts](../01-contracts.md), [durable protocols](../02-durable-protocols.md), [worktree rules](../03-execution-protocol.md) and [test oracles](../04-verification.md) before editing. These override conflicting historical examples. Application implementation belongs to the new session; this handoff does not claim a deployed feature.

## Architecture Overview

Use C fixture DTOs and named server actions. U owns settings UI including trace/retention/evaluation consent; V links to it. Coordinator owns global sidebar/layout registration and shared components.

## Critical Files

- [apps/README.md](../../../apps/README.md)
- [apps/app/app/(console)/billing/page.tsx](../../../apps/app/app/(console)/billing/page.tsx)
- [apps/app/app/(console)/usage/page.tsx](../../../apps/app/app/(console)/usage/page.tsx)
- [apps/app/app/(console)/api-keys/page.tsx](../../../apps/app/app/(console)/api-keys/page.tsx)
- [apps/app/app/(console)/admin/page.tsx](../../../apps/app/app/(console)/admin/page.tsx)

## Files Modified

No application files were modified when this handoff was authored. Planned ownership for this track (paths may not exist until implementation):

- apps/app/app/(console)/billing/ (presentation only)
- apps/app/app/(console)/usage/
- apps/app/app/(console)/api-keys/ (presentation; actions owned C)
- apps/app/app/(console)/admin/ (presentation; actions owned C)
- apps/app/app/(console)/settings/
- apps/app/tests/u/

Shared composition roots, global types/manifests/locks and navigation remain coordinator-owned after foundation. Feature tests belong to this track; root integration tests belong to E. Request shared changes with exact imports/config needed.

## Immediate Next Steps

1. Inspect the manifest and claim one eligible task with the coordinator; record base SHA and dependency evidence.
2. Create an isolated worktree per the execution protocol, then read the listed source files and relevant task below.
3. Write/run the specified failure regression, implement the smallest complete unit, and return evidence plus a reviewable commit.

## Decisions Made

Make pilot consumption, limits and controls understandable while preserving the existing console design and organization model. Use the shared contracts even when developing with fakes; no private replacement for another track’s state/service boundary.

## Assumptions Made

Start dependencies follow manifest v3: reviewed code/fixtures may enable development; new F2R/F2P/D1R gates require acceptance evidence. Integration dependencies may be replaced with contract fakes only during development. Environment defaults are provisional until measured; cloud/GPU/provider tests require an allocated environment and secrets supplied outside the repository.

## Potential Gotchas

Promotional balance is not cash revenue and held funds are not spendable. Do not add payment buttons, team management or invitations. Hiding an admin button is not authorization. Error/loading/empty states are part of each task.

## Task breakdown

### U1 — Usage and promotional balance views

**Start after:** F2. **Integrate after:** C0, D5.

**Imported baseline status:** `implemented` at wave 2; preserve completed work. Product revision/integration follow-up: `U1R`. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Build usage filters/pagination, token/cost/outcome breakdown and balance total/reserved/available with explicit promotional labels. Explain pending reconciliation and platform-absorbed failures without showing estimated tokens as authoritative charges. Build from C fixtures first.

**Acceptance:** Verified new user displays the persisted one-time 10,000-credit grant; pending/failed eligibility has an explicit state; concurrent holds update available balance; usage paginates and CREDIT formatting remains exact, with legacy USD separate.

**Required test oracles:** CONSOLE-FLOWS.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### U2 — Keys and privacy/settings controls

**Start after:** U1R, F2P. **Integrate after:** C3A. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Preserve create-once secret presentation and revoke flows; add allowed model/limit controls only to authorized roles. Add trace mode, capped retention and separate evaluation consent settings with consequences explained. Handle save errors, stale form state and keyboard navigation; no org-switcher.

**Acceptance:** Member/owner fixtures show correct affordances; direct server checks still enforce privileges; revocation/consent changes reflected in subsequent admission/submission tests.

**Required test oracles:** CONSOLE-FLOWS, DUR-RLS.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### U3 — Operator grants, suspension and pilot operations

**Start after:** U1R, F2P. **Integrate after:** C3A. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Build operator-only grant form with required reason and replay-safe submit ID, ledger audit pagination, org suspension/entitlement controls and reconciliation indicators. Keep customer billing separate from operator actions; prevent double-click grants visually and server-side.

**Acceptance:** Duplicate submit creates one audited grant; nonoperator navigation/action denied; suspension does not corrupt existing terminal accounting; zero/invalid grant validation shown.

**Required test oracles:** CONSOLE-FLOWS, DUR-CAP, DUR-RLS.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

## Closed-loop verification

Use the test IDs above as explicit pass/fail oracles, not checklist prose. Unit tests prove local behavior; shared contract fixtures prove interchangeability; real service tests prove integration. Exercise failure paths as well as success, retain a failing reproduction, fix in the owning module and rerun affected cases.

Planned focused suite: apps/app/tests/u/

After F2 establishes discovery, run the focused suite and relevant contract suite. Also run the existing Python baseline for gateway/runtime changes; console tests and lint for console changes; full console build for integrated UI/runtime changes. Record exact executable commands from the pinned environment in evidence. Unavailable external tests are pending, not skipped passes.

## Integration and rollback

Submit only owned paths. Coordinator handles shared wiring and migration order, then runs dependent suites on the merged SHA. Follow additive schema and drain/fence rollback rules; never bypass accounting or discard jobs as a shortcut. Feature-disable may stop new activity but must preserve existing durable state and retention obligations.

## Copyable Claude Opus 5 prompt

```text
Implement one eligible task from research/plan/handoffs/U-administration-ui.md in an isolated worktree. Read CLAUDE.md, HANDOFF.md and the handoff's shared-contract links first. Inspect the current checkout and newer coordinator evidence. Select the earliest unclaimed task whose start dependencies are integrated; report its ID and base SHA. Follow its owned paths and test oracles, use contract fakes only until integration dependencies exist, and do not modify shared wiring or another module's files independently. Deliver a reviewable commit and task evidence using research/plan/evidence/README.md. Do not claim mock-only work is integrated, run unauthorized live/paid operations, or implement deferred scope. Stop at this task's completed handback; leave later tasks explicit.
```

## Verification log

- 2026-09-20: Authored from reviewed repository/specs and accepted decisions. All tasks remain planned; test IDs are required future evidence.
