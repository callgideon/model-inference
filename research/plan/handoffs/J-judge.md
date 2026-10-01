# J — Evaluation workflow and calibration

**Module requirements / acceptance reference.** Read [current state](../../../STATUS.md), [planning index](../README.md), manifest status and the [carried register](../consumer-v1/10-carried-work-register.md) before dispatch. This brief retains algorithms and test obligations; its baseline narrative is not a current deployment inventory or instruction to repeat completed work.


## Original requirement context

The original wave-2 baseline is recorded in `10-wave2-platform-audit.md`; current completion is recorded in the manifest and evidence. This original brief supplies unchanged algorithms, not current progress claims. Build an auditable, consented and budget-bounded judge that starts in offline dry-run mode. Start from the coordinator-assigned committed base, inspect newer evidence, and claim exactly one task below.

## Important Context

Read the [accepted scope](../00-decisions-and-scope.md), [contracts](../01-contracts.md), [durable protocols](../02-durable-protocols.md), [worktree rules](../03-execution-protocol.md) and [test oracles](../04-verification.md) before editing. These override conflicting historical examples. Use these requirements only for the assigned unresolved slice; implementation and deployment status come from the current state record.

## Architecture Overview

JudgeCoordinator owns durable state through D, T owns score projection, C owns actions, V owns display. J supplies rubric schema/version, sampling, provider adapter and calibration outputs.

## Critical Files

- [research/traces/06-feedback-and-judge-spec.md](../../../research/traces/06-feedback-and-judge-spec.md)
- [research/traces/02-metrics-catalogue.md](../../../research/traces/02-metrics-catalogue.md)
- [research/cross-cutting/cloud-pricing.md](../../../research/cross-cutting/cloud-pricing.md)

## Files Modified

No application files were modified when this handoff was authored. Planned ownership for this track (paths may not exist until implementation):

- apps/infrx-api/infrx/judge/
- apps/infrx-api/tests/j/

Shared composition roots, global types/manifests/locks and navigation remain coordinator-owned after foundation. Feature tests belong to this track; root integration tests belong to E. Request shared changes with exact imports/config needed.

## Immediate Next Steps

1. Inspect the manifest and claim one eligible task with the coordinator; record base SHA and dependency evidence.
2. Create an isolated worktree per the execution protocol, then read the listed source files and relevant task below.
3. Write/run the specified failure regression, implement the smallest complete unit, and return evidence plus a reviewable commit.

## Decisions Made

Build an auditable, consented and budget-bounded judge that starts in offline dry-run mode. Use the shared contracts even when developing with fakes; no private replacement for another track’s state/service boundary.

## Assumptions Made

Start dependencies follow manifest v4: reviewed code/fixtures may enable development; new F2R/F2P/D1R gates require acceptance evidence. Integration dependencies may be replaced with contract fakes only during development. Environment defaults are provisional until measured; cloud/GPU/provider tests require an allocated environment and secrets supplied outside the repository.

## Potential Gotchas

Worst-case reservation includes reasoning/output limits, not average estimate. SDK automatic retries can double-submit after an ambiguous timeout. Missing video must not yield a groundedness pass. Only explicit operator calibration labels qualify.

## Task breakdown

### J1 — Dry-run sampler, rubric and score validation

**Start after:** F2. **Integrate after:** none beyond the start/code gate.

**Imported baseline status:** `implemented` at wave 2; preserve completed work. Product revision/integration follow-up: `F2R`, `J2`. Manifest v4, the backend-first overlay and revision handoffs govern current scope.

**Implementation:** Implement deterministic eligible sampling over owned consenting full traces, exclusions for missing/expired material, rubric/schema versioning and validated score parsing. Reproduce planned ~50-label stratification (25 uniform/15 failures/10 feedback) without labeling ordinary customer feedback as calibration. Dry-run outputs costs/sample IDs with zero network egress.

**Acceptance:** Seeded selection is repeatable; schema rejects malformed scores; missing media is marked limited; zero budget/default dry-run cannot invoke provider.

**Required test oracles:** JUDGE-SCORES, F-CONTRACT.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### J2 — Consent/budget coordinated submission and collection

**Start after:** J1, F2P. **Integrate after:** D6J, T2I, L2. Manifest v4, the backend-first overlay and revision handoffs govern current scope.

**Implementation:** Recheck current consent and content immediately before egress; reserve versioned worst-case amount including outstanding runs. Persist submit intent, disable unsafe auto retries, record provider ID and poll/collect idempotently. Timeout with unknown ID quarantines reservation; provide explicit evidence-based reconciliation. Bind provider rate/model versions from approved pricing inputs.

**Acceptance:** Concurrent submissions stay under budget; revoked traces never leave; unknown submit never auto-retries; duplicate/late results settle/project once.

**Required test oracles:** JUDGE-BUDGET, JUDGE-SCORES.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### J3 — Operator calibration and quality report

**Start after:** J2, F2P. **Integrate after:** C3L, V2. Manifest v4, the backend-first overlay and revision handoffs govern current scope.

**Implementation:** Implement explicit authorized calibration workflow, agreement analysis, rubric drift/version comparison and report export. Compute planned kappa>=0.6 and rank rho>=0.6 targets only on appropriate labeled pairs with sample sizes/uncertainty; flag insufficient evidence. Publish failure exemplars without leaking other tenants.

**Acceptance:** Calibration excludes customer-only labels, handles missing/constant scores, reports actual counts and cannot convert limited/no-media scores into a quality pass.

**Required test oracles:** JUDGE-SCORES, CONSOLE-FLOWS.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

## Closed-loop verification

Use the test IDs above as explicit pass/fail oracles, not checklist prose. Unit tests prove local behavior; shared contract fixtures prove interchangeability; real service tests prove integration. Exercise failure paths as well as success, retain a failing reproduction, fix in the owning module and rerun affected cases.

Planned focused suite: apps/infrx-api/tests/j/

After F2 establishes discovery, run the focused suite and relevant contract suite. Also run the existing Python baseline for gateway/runtime changes; console tests and lint for console changes; full console build for integrated UI/runtime changes. Record exact executable commands from the pinned environment in evidence. Unavailable external tests are pending, not skipped passes.

## Integration and rollback

Submit only owned paths. Coordinator handles shared wiring and migration order, then runs dependent suites on the merged SHA. Follow additive schema and drain/fence rollback rules; never bypass accounting or discard jobs as a shortcut. Feature-disable may stop new activity but must preserve existing durable state and retention obligations.

## Copyable Claude Opus 5 prompt

```text
Implement one eligible task from research/plan/handoffs/J-judge.md in an isolated worktree. Read CLAUDE.md, HANDOFF.md and the handoff's shared-contract links first. Inspect the current checkout and newer coordinator evidence. Select the earliest unclaimed task whose start dependencies are integrated; report its ID and base SHA. Follow its owned paths and test oracles, use contract fakes only until integration dependencies exist, and do not modify shared wiring or another module's files independently. Deliver a reviewable commit and task evidence using research/plan/evidence/README.md. Do not claim mock-only work is integrated, run unauthorized live/paid operations, or implement deferred scope. Stop at this task's completed handback; leave later tasks explicit.
```

## Verification log

- 2026-09-20: Authored from reviewed repository/specs and accepted decisions. All tasks remain planned; test IDs are required future evidence.
