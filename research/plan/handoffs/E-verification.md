# E — Independent verification and release evidence

## Current State Summary

Implementation has not started in this documentation package. Turn requirements into reproducible adversarial tests and an honest release decision across module boundaries. Start from the coordinator-assigned committed base, inspect newer evidence, and claim exactly one task below.

## Important Context

Read the [accepted scope](../00-decisions-and-scope.md), [contracts](../01-contracts.md), [durable protocols](../02-durable-protocols.md), [worktree rules](../03-execution-protocol.md) and [test oracles](../04-verification.md) before editing. These override conflicting historical examples. Application implementation belongs to the new session; this handoff does not claim a deployed feature.

## Architecture Overview

E owns test harness and failure injection, not production feature implementations. Module owners fix findings in their worktrees. Request central CI/manifest changes via coordinator. Tests must call real adapters at integration boundaries.

## Critical Files

- [models/marlin2b/bench.py](../../../models/marlin2b/bench.py)
- [models/marlin2b/results/notes.md](../../../models/marlin2b/results/notes.md)
- [research/production-api/10-implementation-spec.md](../../../research/production-api/10-implementation-spec.md)
- [research/traces/08-phases-and-test-plan.md](../../../research/traces/08-phases-and-test-plan.md)

## Files Modified

No application files were modified when this handoff was authored. Planned ownership for this track (paths may not exist until implementation):

- models/marlin2b/bench.py
- models/marlin2b/results/ (new evidence only)
- tests/integration/
- apps/app/tests/e2e/
- research/plan/evidence/e/

Shared composition roots, global types/manifests/locks and navigation remain coordinator-owned after foundation. Feature tests belong to this track; root integration tests belong to E. Request shared changes with exact imports/config needed.

## Immediate Next Steps

1. Inspect the manifest and claim one eligible task with the coordinator; record base SHA and dependency evidence.
2. Create an isolated worktree per the execution protocol, then read the listed source files and relevant task below.
3. Write/run the specified failure regression, implement the smallest complete unit, and return evidence plus a reviewable commit.

## Decisions Made

Turn requirements into reproducible adversarial tests and an honest release decision across module boundaries. Use the shared contracts even when developing with fakes; no private replacement for another track’s state/service boundary.

## Assumptions Made

Start dependencies have been integrated before coding. Integration dependencies may be replaced with contract fakes only during development. Environment defaults are provisional until measured; cloud/GPU/provider tests require an allocated environment and secrets supplied outside the repository.

## Potential Gotchas

Existing benchmark has no usable auth configuration and repeats one clip. Warm-cache closed-loop throughput is not a diverse arrival SLO. A single failover success does not establish durable no-loss semantics; test commit boundaries systematically.

## Task breakdown

### E1 — Distinct corpus and authenticated benchmark client

**Start after:** none; independently ready. **Integrate after:** its start dependencies; no additional external module dependency.

**Implementation:** Create corpus manifest with licenses/consent/hashes and at least 64 distinct clips plus 32 fast subset. Extend benchmark with explicit auth from environment, text/video/upload forms, open-loop arrival rate, stable seeds and raw timing/outcome output. Preserve historical bench rows. Record baseline tool limits; avoid private media in git.

**Acceptance:** Client exercises intended API with no embedded secrets; reproducible seed includes diverse cold/warm paths; latency phases and rejection denominator are recorded.

**Required test oracles:** PERF-PILOT, MEDIA-PARITY.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### E2 — Pinned integration services and fault harness

**Start after:** E1, F2. **Integrate after:** its start dependencies; no additional external module dependency.

**Implementation:** Create local isolated compose for PG/Valkey/CH/S3-compatible store and controllable fake vLLM. Add seeded fixture generation, migration/RLS role runners, process-kill/network/drop/clock injection and cleanup scoped to test namespace. Map original test IDs to namespaced cases; prove nested console tests and cross-module suites are discovered.

**Acceptance:** One documented command provisions fresh isolated services and runs smoke/contract tests; no production credentials needed; intentional failure is detected rather than skipped.

**Required test oracles:** F-CONTRACT, DUR-RLS.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### E3 — Cross-module failures and security gate

**Start after:** E2. **Integrate after:** D6, M3, Q3, W3, G4, T3, J2, C3, U3, V3, G5, J3.

**Implementation:** Implement every local applicable verification-table case with before/after durable-state assertions. Inject every acceptance/output/terminal boundary, duplicate outbox/projection, spoofed tenant/role and consent/budget race. Run actual Lua/SQL/DDL, not only mocks. Report defects to owning module and rerun targeted plus affected integration suites after fixes.

**Acceptance:** No open correctness/privacy/financial blocker; accepted-job accounting conservation and tenant isolation hold; unsupported live-only cases listed as pending, never passed.

**Required test oracles:** DUR-ADMIT, DUR-CAP, DUR-FENCE, DUR-OUTPUT, DUR-SETTLE, DUR-OUTBOX, DUR-RLS, MEDIA-SEC, API-MODES, API-STREAM, API-CALLBACK, TRACE-BOUNDS, TRACE-RECOVER, TRACE-TENANT, FEEDBACK-ACK, JUDGE-BUDGET, JUDGE-SCORES, CONSOLE-FLOWS.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### E4 — Single-GPU release evidence and launch decision

**Start after:** E3. **Integrate after:** I3.

**Implementation:** Run assigned live corpus/latency/overhead/recovery tests on pinned deployment, document actual pilot envelope and unresolved SLO gaps. Validate GPU parity, journal RTT/batching and resource budgets; separate dry-run judge from authorized live judge evidence. Publish pass/fail/conditional decision per gate with raw evidence and rollback trigger.

**Acceptance:** G3 is either evidenced or explicitly blocked with owner/reproduction; no extrapolated fleet readiness or paid-launch claim; signed-off pilot evidence unblocks I4 only when passed.

**Required test oracles:** PERF-PILOT, OPS-RECOVER, MEDIA-PARITY, TRACE-BOUNDS.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

## Closed-loop verification

Use the test IDs above as explicit pass/fail oracles, not checklist prose. Unit tests prove local behavior; shared contract fixtures prove interchangeability; real service tests prove integration. Exercise failure paths as well as success, retain a failing reproduction, fix in the owning module and rerun affected cases.

Focused suites: foundation contract/baseline suites for F; isolated integration and allocated deployment drills for E/I.

After F2 establishes discovery, run the focused suite and relevant contract suite. Also run the existing Python baseline for gateway/runtime changes; console tests and lint for console changes; full console build for integrated UI/runtime changes. Record exact executable commands from the pinned environment in evidence. Unavailable external tests are pending, not skipped passes.

## Integration and rollback

Submit only owned paths. Coordinator handles shared wiring and migration order, then runs dependent suites on the merged SHA. Follow additive schema and drain/fence rollback rules; never bypass accounting or discard jobs as a shortcut. Feature-disable may stop new activity but must preserve existing durable state and retention obligations.

## Copyable Claude Opus 5 prompt

```text
Implement one eligible task from research/plan/handoffs/E-verification.md in an isolated worktree. Read CLAUDE.md, HANDOFF.md and the handoff's shared-contract links first. Inspect the current checkout and newer coordinator evidence. Select the earliest unclaimed task whose start dependencies are integrated; report its ID and base SHA. Follow its owned paths and test oracles, use contract fakes only until integration dependencies exist, and do not modify shared wiring or another module's files independently. Deliver a reviewable commit and task evidence using research/plan/evidence/README.md. Do not claim mock-only work is integrated, run unauthorized live/paid operations, or implement deferred scope. Stop at this task's completed handback; leave later tasks explicit.
```

## Verification log

- 2026-09-20: Authored from reviewed repository/specs and accepted decisions. All tasks remain planned; test IDs are required future evidence.
