# M — Secure media ingestion and preparation

**Current assignment scope:** [complete plan](../12-complete-build-plan.md), [fresh-session handoff](../16-fresh-session-handoff.md) and manifest v4. Preserve these module algorithms subject to the audited revisions. Dispatch the [Marlin backend closure](../18-marlin-backend-first.md) first. App/browser and Lab work follow their acceptance gates.

> **2026-09-21 amendment:** Read [platform split](../08-platform-split.md), [new task briefs](../09-amendment-workstreams.md) and [manifest v4](../tasks.json) before this brief. They supersede conflicting paths, signup/unit rules and dependencies below. Wave 2 is imported at `271add9`; [audit](../10-wave2-platform-audit.md) and [revision handoffs](../11-wave3-revision-handoffs.md) govern continuation; existing evidence is not reset.


## Current State Summary

Wave-2 baseline is audited in `10-wave2-platform-audit.md`; preserve its completed tasks and apply the new revision gates before pending work. This original brief supplies unchanged algorithms, not current progress claims. Deliver tenant-safe, bounded, reproducible media suitable for both immediate and deferred execution. Start from the coordinator-assigned committed base, inspect newer evidence, and claim exactly one task below.

## Important Context

Read the [accepted scope](../00-decisions-and-scope.md), [contracts](../01-contracts.md), [durable protocols](../02-durable-protocols.md), [worktree rules](../03-execution-protocol.md) and [test oracles](../04-verification.md) before editing. These override conflicting historical examples. Application implementation belongs to the new session; this handoff does not claim a deployed feature.

## Architecture Overview

MediaStore stages normalized payloads and returns immutable owned refs. Preparation reports progress/result to JobStore; it does not create or settle its own jobs. Profile version controls cache keys and engine media identity.

## Critical Files

- [apps/infrx-api/gateway.py](../../../apps/infrx-api/gateway.py)
- [apps/infrx-api/tests/test_media.py](../../../apps/infrx-api/tests/test_media.py)
- [research/production-api/10-implementation-spec.md](../../../research/production-api/10-implementation-spec.md)
- [models/marlin2b/tokens.py](../../../models/marlin2b/tokens.py)

## Files Modified

No application files were modified when this handoff was authored. Planned ownership for this track (paths may not exist until implementation):

- apps/infrx-api/infrx/media/
- apps/infrx-api/tests/m/

Shared composition roots, global types/manifests/locks and navigation remain coordinator-owned after foundation. Feature tests belong to this track; root integration tests belong to E. Request shared changes with exact imports/config needed.

## Immediate Next Steps

1. Inspect the manifest and claim one eligible task with the coordinator; record base SHA and dependency evidence.
2. Create an isolated worktree per the execution protocol, then read the listed source files and relevant task below.
3. Write/run the specified failure regression, implement the smallest complete unit, and return evidence plus a reviewable commit.

## Decisions Made

Deliver tenant-safe, bounded, reproducible media suitable for both immediate and deferred execution. Use the shared contracts even when developing with fakes; no private replacement for another track’s state/service boundary.

## Assumptions Made

Start dependencies follow manifest v4: reviewed code/fixtures may enable development; new F2R/F2P/D1R gates require acceptance evidence. Integration dependencies may be replaced with contract fakes only during development. Environment defaults are provisional until measured; cloud/GPU/provider tests require an allocated environment and secrets supplied outside the repository.

## Potential Gotchas

Resolving then allowing HTTP client re-resolution is still vulnerable. Limit pixel area to200704, not longest edge448. Never trust probe metadata alone for resource limits. Temp serving media retention is distinct from opted-in trace retention.

## Task breakdown

### M1 — Bound and secure URL/base64 materialization

**Start after:** F2. **Integrate after:** none beyond the start/code gate.

**Imported baseline status:** `implemented` at wave 2; preserve completed work. Product revision/integration follow-up: `F2R`, `M2`. Manifest v4, the backend-first overlay and revision handoffs govern current scope.

**Implementation:** Implement connection pinned to validated IP with TLS hostname verification, every-hop validation, public IPv4/IPv6 policy, DNS rebinding protection and no proxy-env bypass. Stream fetch with aggregate byte/time limits and redirect budget. Strictly decode bounded base64 and preserve current once-only fetch behavior. Stage canonical request and source metadata durably; sanitize logs.

**Acceptance:** Adversarial resolver and redirect fixtures cannot hit internal addresses; oversized bodies stop promptly; every accepted request has a recoverable payload ref with digest.

**Required test oracles:** MEDIA-SEC, F-CONTRACT.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### M2 — Versioned preprocessing and tenant cache

**Start after:** F2P, M1. **Integrate after:** D2, W1. Manifest v4, the backend-first overlay and revision handoffs govern current scope.

**Implementation:** Implement bounded probe/transcode pool, cancellation/kill of process groups, duration/frame/token budgets and no-upsize aspect-preserving even dimensions without pixel-area overshoot. Keep CRF23 until CRF28 parity is measured; quantized budgets stay disabled pending evidence. Hash source plus tenant/profile; namespace engine multimodal identifiers as well as prefix cache. Persist prepared artifact before JobStore.prepared.

**Acceptance:** Corrupt/slow/media-bomb cases fail within preparation budget and release resources; cache separation and deterministic profiles hold; distinct-clip parity results attached.

**Required test oracles:** MEDIA-PARITY, MEDIA-SEC.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### M3 — Owned uploads, expiry and orphan collection

**Start after:** F2P, M1. **Integrate after:** D2. Manifest v4, the backend-first overlay and revision handoffs govern current scope.

**Implementation:** Implement constrained upload creation/finalization, immutable completed handles, authoritative checksum/size/object-owner validation and upload-to-job references. Prevent mutable object replacement after finalization. Add aborted/staged/orphan GC with active-job references respected and processing-cache logical expiry. Supply owned resolver for trace/judge reuse under independent consent.

**Acceptance:** Cross-org refs and unfinalized/replaced uploads are rejected; retries finalize once; GC cannot remove live referenced input and eventually removes failed-stage blobs.

**Required test oracles:** MEDIA-SEC, TRACE-TENANT.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

## Closed-loop verification

Use the test IDs above as explicit pass/fail oracles, not checklist prose. Unit tests prove local behavior; shared contract fixtures prove interchangeability; real service tests prove integration. Exercise failure paths as well as success, retain a failing reproduction, fix in the owning module and rerun affected cases.

Planned focused suite: apps/infrx-api/tests/m/

After F2 establishes discovery, run the focused suite and relevant contract suite. Also run the existing Python baseline for gateway/runtime changes; console tests and lint for console changes; full console build for integrated UI/runtime changes. Record exact executable commands from the pinned environment in evidence. Unavailable external tests are pending, not skipped passes.

## Integration and rollback

Submit only owned paths. Coordinator handles shared wiring and migration order, then runs dependent suites on the merged SHA. Follow additive schema and drain/fence rollback rules; never bypass accounting or discard jobs as a shortcut. Feature-disable may stop new activity but must preserve existing durable state and retention obligations.

## Copyable Claude Opus 5 prompt

```text
Implement one eligible task from research/plan/handoffs/M-media.md in an isolated worktree. Read CLAUDE.md, HANDOFF.md and the handoff's shared-contract links first. Inspect the current checkout and newer coordinator evidence. Select the earliest unclaimed task whose start dependencies are integrated; report its ID and base SHA. Follow its owned paths and test oracles, use contract fakes only until integration dependencies exist, and do not modify shared wiring or another module's files independently. Deliver a reviewable commit and task evidence using research/plan/evidence/README.md. Do not claim mock-only work is integrated, run unauthorized live/paid operations, or implement deferred scope. Stop at this task's completed handback; leave later tasks explicit.
```

## Verification log

- 2026-09-20: Authored from reviewed repository/specs and accepted decisions. All tasks remain planned; test IDs are required future evidence.
