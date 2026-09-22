# W — Engine execution and lifecycle

> **2026-09-21 amendment:** Read [platform split](../08-platform-split.md), [new task briefs](../09-amendment-workstreams.md) and [manifest v3](../tasks.json) before this brief. They supersede conflicting paths, signup/unit rules and dependencies below. Wave 2 is imported at `271add9`; [audit](../10-wave2-platform-audit.md) and [revision handoffs](../11-wave3-revision-handoffs.md) govern continuation; existing evidence is not reset.


## Current State Summary

Wave-2 baseline is audited in `10-wave2-platform-audit.md`; preserve its completed tasks and apply the new revision gates before pending work. This original brief supplies unchanged algorithms, not current progress claims. Execute one fenced attempt at a time and carry cancellation, results and authoritative usage through durable terminalization. Start from the coordinator-assigned committed base, inspect newer evidence, and claim exactly one task below.

## Important Context

Read the [accepted scope](../00-decisions-and-scope.md), [contracts](../01-contracts.md), [durable protocols](../02-durable-protocols.md), [worktree rules](../03-execution-protocol.md) and [test oracles](../04-verification.md) before editing. These override conflicting historical examples. Application implementation belongs to the new session; this handoff does not claim a deployed feature.

## Architecture Overview

Engine yields canonical events/usage; worker coordinates Scheduler, JobStore, StreamStore and MediaStore. Infrastructure owns service units; request wiring changes through coordinator.

## Critical Files

- [models/marlin2b/serve.sh](../../../models/marlin2b/serve.sh)
- [apps/infrx-api/gateway.py](../../../apps/infrx-api/gateway.py)
- [research/production-api/10-implementation-spec.md](../../../research/production-api/10-implementation-spec.md)

## Files Modified

No application files were modified when this handoff was authored. Planned ownership for this track (paths may not exist until implementation):

- apps/infrx-api/infrx/worker/
- apps/infrx-api/tests/w/
- models/marlin2b/serve.sh

Shared composition roots, global types/manifests/locks and navigation remain coordinator-owned after foundation. Feature tests belong to this track; root integration tests belong to E. Request shared changes with exact imports/config needed.

## Immediate Next Steps

1. Inspect the manifest and claim one eligible task with the coordinator; record base SHA and dependency evidence.
2. Create an isolated worktree per the execution protocol, then read the listed source files and relevant task below.
3. Write/run the specified failure regression, implement the smallest complete unit, and return evidence plus a reviewable commit.

## Decisions Made

Execute one fenced attempt at a time and carry cancellation, results and authoritative usage through durable terminalization. Use the shared contracts even when developing with fakes; no private replacement for another track’s state/service boundary.

## Assumptions Made

Start dependencies follow manifest v3: reviewed code/fixtures may enable development; new F2R/F2P/D1R gates require acceptance evidence. Integration dependencies may be replaced with contract fakes only during development. Environment defaults are provisional until measured; cloud/GPU/provider tests require an allocated environment and secrets supplied outside the repository.

## Potential Gotchas

No postpublication regeneration. Do not pass upstream DONE before settlement. Engine max context is per request, not summed across concurrent prompts. max-num-seqs 8/worker 10 are proposed until measured; pin engine rather than nightly.

## Task breakdown

### W1 — Engine adapter and deterministic execution fakes

**Start after:** F2. **Integrate after:** none beyond the start/code gate.

**Imported baseline status:** `implemented` at wave 2; preserve completed work. Product revision/integration follow-up: `F2R`, `W2`. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Implement engine request/response translation, video ref handling, version checks and authoritative usage parsing. Validate supported parameters/context after preparation. Provide fakes for prefill stall, split tokens, missing usage, engine error and abrupt exit. Preserve raw final text needed by canonical trace capture.

**Acceptance:** Adapter contract distinguishes transport failure, incomplete output and authoritative usage; unsupported request options fail explicitly.

**Required test oracles:** F-CONTRACT, API-STREAM.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### W2 — Lease-aware execution, cancellation and completion

**Start after:** W1, F2P. **Integrate after:** D5, M2, Q3. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Implement claim/heartbeat loop, preparation handoff, bounded generation/TTFT/stall timers and engine cancellation acknowledgment. Append through StreamStore before publication; persist result then complete transaction. On lease loss stop generation immediately; race cancellation and completion through JobStore.

**Acceptance:** Kill or fence attempts before/after first output; no stale append or double settlement; sync cancellation reaches engine; missing usage enters reconciliation.

**Required test oracles:** DUR-FENCE, DUR-OUTPUT, DUR-SETTLE.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### W3 — Drain, engine pin and measured concurrency

**Start after:** W2, F2P. **Integrate after:** E1. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Pin image/model/tokenizer revisions, configure serving limits and expose protected readiness/liveness. Drain stops new claims, waits within deadline, fences/cancels remainder and records outcomes. Run measured concurrency and media UUID capability probes with E; request service-unit changes from I.

**Acceptance:** Pinned engine supports required file/media identifiers and cancellation; drain preserves durable jobs; proposed limits have latency/memory evidence or remain disabled.

**Required test oracles:** OPS-RECOVER, PERF-PILOT.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

## Closed-loop verification

Use the test IDs above as explicit pass/fail oracles, not checklist prose. Unit tests prove local behavior; shared contract fixtures prove interchangeability; real service tests prove integration. Exercise failure paths as well as success, retain a failing reproduction, fix in the owning module and rerun affected cases.

Planned focused suite: apps/infrx-api/tests/w/

After F2 establishes discovery, run the focused suite and relevant contract suite. Also run the existing Python baseline for gateway/runtime changes; console tests and lint for console changes; full console build for integrated UI/runtime changes. Record exact executable commands from the pinned environment in evidence. Unavailable external tests are pending, not skipped passes.

## Integration and rollback

Submit only owned paths. Coordinator handles shared wiring and migration order, then runs dependent suites on the merged SHA. Follow additive schema and drain/fence rollback rules; never bypass accounting or discard jobs as a shortcut. Feature-disable may stop new activity but must preserve existing durable state and retention obligations.

## Copyable Claude Opus 5 prompt

```text
Implement one eligible task from research/plan/handoffs/W-worker.md in an isolated worktree. Read CLAUDE.md, HANDOFF.md and the handoff's shared-contract links first. Inspect the current checkout and newer coordinator evidence. Select the earliest unclaimed task whose start dependencies are integrated; report its ID and base SHA. Follow its owned paths and test oracles, use contract fakes only until integration dependencies exist, and do not modify shared wiring or another module's files independently. Deliver a reviewable commit and task evidence using research/plan/evidence/README.md. Do not claim mock-only work is integrated, run unauthorized live/paid operations, or implement deferred scope. Stop at this task's completed handback; leave later tasks explicit.
```

## Verification log

- 2026-09-20: Authored from reviewed repository/specs and accepted decisions. All tasks remain planned; test IDs are required future evidence.
