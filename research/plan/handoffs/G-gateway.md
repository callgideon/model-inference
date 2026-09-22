# G — Public API and streaming relay

**Current assignment scope:** [complete plan](../12-complete-build-plan.md), [fresh-session handoff](../16-fresh-session-handoff.md) and manifest v4. Preserve these module algorithms subject to the audited revisions. Dispatch App launch work first; later Lab or conditional tasks require their milestone activation.

> **2026-09-21 amendment:** Read [platform split](../08-platform-split.md), [new task briefs](../09-amendment-workstreams.md) and [manifest v4](../tasks.json) before this brief. They supersede conflicting paths, signup/unit rules and dependencies below. Wave 2 is imported at `271add9`; [audit](../10-wave2-platform-audit.md) and [revision handoffs](../11-wave3-revision-handoffs.md) govern continuation; existing evidence is not reset. G4 is split into G4U/G4F/G4T; consumer upload delivery does not depend on judge or trace export.


## Current State Summary

Wave-2 baseline is audited in `10-wave2-platform-audit.md`; preserve its completed tasks and apply the new revision gates before pending work. This original brief supplies unchanged algorithms, not current progress claims. Expose a consistent authenticated API that acknowledges durable acceptance and reports honest synchronous/asynchronous outcomes. Start from the coordinator-assigned committed base, inspect newer evidence, and claim exactly one task below.

## Important Context

Read the [accepted scope](../00-decisions-and-scope.md), [contracts](../01-contracts.md), [durable protocols](../02-durable-protocols.md), [worktree rules](../03-execution-protocol.md) and [test oracles](../04-verification.md) before editing. These override conflicting historical examples. Application implementation belongs to the new session; this handoff does not claim a deployed feature.

## Architecture Overview

Route handlers consume shared ports; no direct financial or queue-state writes. Coordinator owns application factory/router registration. All public errors include request IDs and safe messages.

## Critical Files

- [apps/infrx-api/gateway.py](../../../apps/infrx-api/gateway.py)
- [apps/infrx-api/client_example.py](../../../apps/infrx-api/client_example.py)
- [research/production-api/10-implementation-spec.md](../../../research/production-api/10-implementation-spec.md)
- [research/traces/06-feedback-and-judge-spec.md](../../../research/traces/06-feedback-and-judge-spec.md)

## Files Modified

No application files were modified when this handoff was authored. Planned ownership for this track (paths may not exist until implementation):

- apps/infrx-api/infrx/gateway/routes/
- apps/infrx-api/infrx/auth/
- apps/infrx-api/tests/g/
- apps/infrx-api/client_example.py

Shared composition roots, global types/manifests/locks and navigation remain coordinator-owned after foundation. Feature tests belong to this track; root integration tests belong to E. Request shared changes with exact imports/config needed.

## Immediate Next Steps

1. Inspect the manifest and claim one eligible task with the coordinator; record base SHA and dependency evidence.
2. Create an isolated worktree per the execution protocol, then read the listed source files and relevant task below.
3. Write/run the specified failure regression, implement the smallest complete unit, and return evidence plus a reviewable commit.

## Decisions Made

Expose a consistent authenticated API that acknowledges durable acceptance and reports honest synchronous/asynchronous outcomes. Use the shared contracts even when developing with fakes; no private replacement for another track’s state/service boundary.

## Assumptions Made

Start dependencies follow manifest v4: reviewed code/fixtures may enable development; new F2R/F2P/D1R gates require acceptance evidence. Integration dependencies may be replaced with contract fakes only during development. Environment defaults are provisional until measured; cloud/GPU/provider tests require an allocated environment and secrets supplied outside the repository.

## Potential Gotchas

Body must be bounded before JSON parse. Increment bounded preparation admission before work. Normal chat never silently becomes 202. A stream never started must still release resources. Missing auth configuration is fatal outside explicit test/dev mode.

## Task breakdown

### G1 — Ingress, auth and capability validation

**Start after:** F2. **Integrate after:** D2.

**Imported baseline status:** `implemented` at wave 2; preserve completed work. Product revision/integration follow-up: `F2R`, `G1R`. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Mint ingress IDs, enforce intake limits/deadline before parsing, validate content/parameters and authenticate through bounded caches with revocation-safe admission. Remove exception text from public errors/health. Define dev/test-only unauthenticated mode with production startup rejection. Reconcile legacy key mapping for pilot cutover.

**Acceptance:** Malformed/oversized/unsupported/auth failures have stable status and no secret leak; cache bounds preserved; no accepted production request lacks tenant identity.

**Required test oracles:** MEDIA-SEC, DUR-RLS, F-BASE.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### G2 — Synchronous chat and persistent SSE relay

**Start after:** G1R. **Integrate after:** D5, W2, M2, Q3, I0. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Implement nonstream wait and accepted SSE relay from committed journal. Keepalive/progress stays distinct from model chunks; handle upstream error before/after headers. Parse reasoning markers across arbitrary chunk boundaries. On sync disconnect/timeout durably cancel, including generator-never-started paths; never emit 202 after SSE starts.

**Acceptance:** Complete sync/SSE matrix passes; success follows durable terminal commit; no inflight leaks or silent orphan execution; physical connection loss has recoverable identity.

**Required test oracles:** API-MODES, API-STREAM, DUR-OUTPUT.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### G3 — Explicit jobs, status, cancellation and replay

**Start after:** G1R. **Integrate after:** D5, W2, I0. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Implement explicit preference and jobs routes, owned opaque handles, idempotency conflict/replay, async observer detach, DELETE cancellation, status/result expiry and event cursor semantics. Update client example with authenticated sync and explicit async flows without embedding credentials.

**Acceptance:** Retry after lost 202 returns same job; cross-tenant handles 404; expired results/journal 410; detached observers do not cancel jobs, explicit DELETE does.

**Required test oracles:** API-MODES, DUR-ADMIT, DUR-FENCE.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### G4 — Uploads, feedback and trace export adapters

**Scheduling:** Retired mixed task. Use `G4U`, `G4F`, `G4T` from the amendment briefs. The algorithm below is historical reference.

**Implementation:** Expose upload initiation/finalization with MediaStore; accept feedback through shared durable service; expose owned trace export with logical expiry/consent and bounded response size. Validate filters/IDs and propagate stable domain errors. Do not query arbitrary object keys from user input.

**Acceptance:** Feedback before CH arrival succeeds durably; spoofed role fails; uploads/export enforce tenant boundary and expiry; retries do not duplicate feedback.

**Required test oracles:** FEEDBACK-ACK, TRACE-TENANT, MEDIA-SEC.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### G5 — Signed async completion callbacks

**Start after:** G3, F2P. **Integrate after:** D5, M1. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Support optional registered callback destinations for explicit async jobs. Write callback delivery intent in the terminal outbox transaction; deliver outside execution/settlement. Reuse M's pinned public-destination policy on each connection/redirect, bound response bytes/time, sign immutable body plus timestamp/event ID with a versioned per-org secret supplied outside code. Deliver at least once, with stable delivery ID, capped exponential retry (1min, 5min, 30min, 2h, 8h; stop after 24h) and operator-visible dead letter. Never rerun inference on callback failure. Prevent caller-supplied callback headers/credentials from becoming an egress primitive; only registered owned destinations are accepted. Disable redirect following by default. Provide receiver-side signature/replay-window documentation and safe manual redelivery.

**Acceptance:** Failed receiver, rotated secret, duplicate response acknowledgment and private/DNS-rebound destination leave job/usage unchanged. Stable signed delivery can be verified and deduplicated by the receiver; unknown destinations are rejected before acceptance. Callback secrets are encrypted/server-only and never shown in trace content or logs.

**Required test oracles:** API-CALLBACK, MEDIA-SEC, DUR-OUTBOX.

**Deliverable:** route/dispatcher adapter, signature fixtures with nonsecret test material, retry/failure regressions and callback delivery evidence. Request any additional outbox fields from D; do not edit migrations in this worktree.

## Closed-loop verification

Use the test IDs above as explicit pass/fail oracles, not checklist prose. Unit tests prove local behavior; shared contract fixtures prove interchangeability; real service tests prove integration. Exercise failure paths as well as success, retain a failing reproduction, fix in the owning module and rerun affected cases.

Planned focused suite: apps/infrx-api/tests/g/

After F2 establishes discovery, run the focused suite and relevant contract suite. Also run the existing Python baseline for gateway/runtime changes; console tests and lint for console changes; full console build for integrated UI/runtime changes. Record exact executable commands from the pinned environment in evidence. Unavailable external tests are pending, not skipped passes.

## Integration and rollback

Submit only owned paths. Coordinator handles shared wiring and migration order, then runs dependent suites on the merged SHA. Follow additive schema and drain/fence rollback rules; never bypass accounting or discard jobs as a shortcut. Feature-disable may stop new activity but must preserve existing durable state and retention obligations.

## Copyable Claude Opus 5 prompt

```text
Implement one eligible task from research/plan/handoffs/G-gateway.md in an isolated worktree. Read CLAUDE.md, HANDOFF.md and the handoff's shared-contract links first. Inspect the current checkout and newer coordinator evidence. Select the earliest unclaimed task whose start dependencies are integrated; report its ID and base SHA. Follow its owned paths and test oracles, use contract fakes only until integration dependencies exist, and do not modify shared wiring or another module's files independently. Deliver a reviewable commit and task evidence using research/plan/evidence/README.md. Do not claim mock-only work is integrated, run unauthorized live/paid operations, or implement deferred scope. Stop at this task's completed handback; leave later tasks explicit.
```

## Verification log

- 2026-09-20: Authored from reviewed repository/specs and accepted decisions. All tasks remain planned; test IDs are required future evidence.
