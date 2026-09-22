# V — Trace explorer and feedback UI

**Current assignment scope:** [complete plan](../12-complete-build-plan.md), [fresh-session handoff](../16-fresh-session-handoff.md) and manifest v4. Preserve these module algorithms subject to the audited revisions. Dispatch App launch work first; later Lab or conditional tasks require their milestone activation.

> **2026-09-21 amendment:** Read [platform split](../08-platform-split.md), [new task briefs](../09-amendment-workstreams.md) and [manifest v4](../tasks.json) before this brief. They supersede conflicting paths, signup/unit rules and dependencies below. Wave 2 is imported at `271add9`; [audit](../10-wave2-platform-audit.md) and [revision handoffs](../11-wave3-revision-handoffs.md) govern continuation; existing evidence is not reset. Provider trace/review/judge UI belongs in apps/lab; do not implement the old apps/app destinations.


## Current State Summary

Wave-2 baseline is audited in `10-wave2-platform-audit.md`; preserve its completed tasks and apply the new revision gates before pending work. This original brief supplies unchanged algorithms, not current progress claims. Deliver a usable trace explorer with clear completeness, retention, feedback and evaluation provenance. Start from the coordinator-assigned committed base, inspect newer evidence, and claim exactly one task below.

## Important Context

Read the [accepted scope](../00-decisions-and-scope.md), [contracts](../01-contracts.md), [durable protocols](../02-durable-protocols.md), [worktree rules](../03-execution-protocol.md) and [test oracles](../04-verification.md) before editing. These override conflicting historical examples. Application implementation belongs to the new session; this handoff does not claim a deployed feature.

## Architecture Overview

Consume C repositories/actions and J score DTOs; no direct CH/S3 access. Request navigation wiring from coordinator. U owns settings; link there rather than duplicate controls.

## Critical Files

- [research/traces/07-console-spec.md](../../../research/traces/07-console-spec.md)
- [research/traces/02-metrics-catalogue.md](../../../research/traces/02-metrics-catalogue.md)
- [apps/app/app/(console)/layout.tsx](../../../apps/app/app/(console)/layout.tsx)

## Files Modified

No application files were modified when this handoff was authored. Planned ownership for this track (paths may not exist until implementation):

- apps/lab/app/(provider)/requests/
- apps/lab/components/traces/
- apps/lab/tests/v/

Shared composition roots, global types/manifests/locks and navigation remain coordinator-owned after foundation. Feature tests belong to this track; root integration tests belong to E. Request shared changes with exact imports/config needed.

## Immediate Next Steps

1. Inspect the manifest and claim one eligible task with the coordinator; record base SHA and dependency evidence.
2. Create an isolated worktree per the execution protocol, then read the listed source files and relevant task below.
3. Write/run the specified failure regression, implement the smallest complete unit, and return evidence plus a reviewable commit.

## Decisions Made

Deliver a usable trace explorer with clear completeness, retention, feedback and evaluation provenance. Use the shared contracts even when developing with fakes; no private replacement for another track’s state/service boundary.

## Assumptions Made

Start dependencies follow manifest v4: reviewed code/fixtures may enable development; new F2R/F2P/D1R gates require acceptance evidence. Integration dependencies may be replaced with contract fakes only during development. Environment defaults are provisional until measured; cloud/GPU/provider tests require an allocated environment and secrets supplied outside the repository.

## Potential Gotchas

Canonical logical content is not guaranteed raw HTTP bytes. Lost/expired/off traces have different states. Console feedback is customer feedback unless operator identity and explicit calibration action prove otherwise.

## Task breakdown

### V1 — Paginated trace list and filters

**Start after:** F2. **Integrate after:** V1M.

**Imported baseline status:** `implemented` at wave 2; preserve completed work. Product revision/integration follow-up: `V1M`. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Build bounded list with model/key/time/outcome/quality filters, stable cursors and URL state. Show content availability and projection lag/loss without conflating absent off-mode traces with failures. Provide loading/empty/error/retry and keyboard-accessible navigation.

**Acceptance:** Fixture and real-service tests agree on pagination and tenant filtering; stale requests cannot overwrite current filters; no raw query parameter override surface.

**Required test oracles:** CONSOLE-FLOWS, TRACE-TENANT.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### V2 — Trace detail, content and feedback

**Start after:** V1M. **Integrate after:** C2, C3F. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Render timings/usage/request versions and safe text/media content with metadata-only/pending/lost/expired states. Provide feedback submission and provenance history using durable action results; handle CH lag immediately after acceptance. Never render model HTML unsanitized.

**Acceptance:** Cross-org/deep-link tests deny access; feedback appears from accepted durable record while projection lags; expired media links disappear; keyboard/error flows work.

**Required test oracles:** CONSOLE-FLOWS, FEEDBACK-ACK, TRACE-TENANT.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### V3 — Judge score and calibration presentation

**Start after:** V2, F2P. **Integrate after:** J2. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Show rubric/model versions, sample/run state, limited evaluations, budget/ambiguous submit status and operator calibration affordance through C actions. Link to U consent settings. Distinguish estimated/dry-run outputs from real provider results and statistical targets from achieved quality.

**Acceptance:** No-media score cannot look like groundedness success; ambiguous runs show held budget; only authorized explicit labels enter calibration; no paid-submit default.

**Required test oracles:** JUDGE-SCORES, CONSOLE-FLOWS.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

## Closed-loop verification

Use the test IDs above as explicit pass/fail oracles, not checklist prose. Unit tests prove local behavior; shared contract fixtures prove interchangeability; real service tests prove integration. Exercise failure paths as well as success, retain a failing reproduction, fix in the owning module and rerun affected cases.

Planned focused suite: apps/lab/tests/v/

After F2 establishes discovery, run the focused suite and relevant contract suite. Also run the existing Python baseline for gateway/runtime changes; console tests and lint for console changes; full console build for integrated UI/runtime changes. Record exact executable commands from the pinned environment in evidence. Unavailable external tests are pending, not skipped passes.

## Integration and rollback

Submit only owned paths. Coordinator handles shared wiring and migration order, then runs dependent suites on the merged SHA. Follow additive schema and drain/fence rollback rules; never bypass accounting or discard jobs as a shortcut. Feature-disable may stop new activity but must preserve existing durable state and retention obligations.

## Copyable Claude Opus 5 prompt

```text
Implement one eligible task from research/plan/handoffs/V-trace-ui.md in an isolated worktree. Read CLAUDE.md, HANDOFF.md and the handoff's shared-contract links first. Inspect the current checkout and newer coordinator evidence. Select the earliest unclaimed task whose start dependencies are integrated; report its ID and base SHA. Follow its owned paths and test oracles, use contract fakes only until integration dependencies exist, and do not modify shared wiring or another module's files independently. Deliver a reviewable commit and task evidence using research/plan/evidence/README.md. Do not claim mock-only work is integrated, run unauthorized live/paid operations, or implement deferred scope. Stop at this task's completed handback; leave later tasks explicit.
```

## Verification log

- 2026-09-20: Authored from reviewed repository/specs and accepted decisions. All tasks remain planned; test IDs are required future evidence.
