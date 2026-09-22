# D — Durable state and promotional accounting

> **2026-09-21 amendment:** Read [platform split](../08-platform-split.md), [new task briefs](../09-amendment-workstreams.md) and [manifest v3](../tasks.json) before this brief. They supersede conflicting paths, signup/unit rules and dependencies below. Wave 2 is imported at `271add9`; [audit](../10-wave2-platform-audit.md) and [revision handoffs](../11-wave3-revision-handoffs.md) govern continuation; existing evidence is not reset. Signup grants are now 10,000 CREDIT once per individual user; preserve historical USD separately. D6 is split into D6F/D6J.


## Current State Summary

Wave-2 baseline is audited in `10-wave2-platform-audit.md`; preserve its completed tasks and apply the new revision gates before pending work. This original brief supplies unchanged algorithms, not current progress claims. Implement the authoritative transactional state machine for every accepted request and all billable/external side effects. Start from the coordinator-assigned committed base, inspect newer evidence, and claim exactly one task below.

## Important Context

Read the [accepted scope](../00-decisions-and-scope.md), [contracts](../01-contracts.md), [durable protocols](../02-durable-protocols.md), [worktree rules](../03-execution-protocol.md) and [test oracles](../04-verification.md) before editing. These override conflicting historical examples. Application implementation belongs to the new session; this handoff does not claim a deployed feature.

## Architecture Overview

JobStore, StreamStore, FeedbackService storage operations and JudgeCoordinator. Publish migration/RPC names and database role matrix to C/G/J. Q is a consumer of outbox/job snapshots, never the authority.

## Critical Files

- [apps/app/supabase/migrations/0001_init.sql](../../../apps/app/supabase/migrations/0001_init.sql)
- [apps/app/supabase/migrations/0002_seed_models.sql](../../../apps/app/supabase/migrations/0002_seed_models.sql)
- [apps/app/lib/credits.ts](../../../apps/app/lib/credits.ts)
- [apps/infrx-api/gateway.py](../../../apps/infrx-api/gateway.py)

## Files Modified

No application files were modified when this handoff was authored. Planned ownership for this track (paths may not exist until implementation):

- apps/app/supabase/migrations/ (all future migrations)
- apps/infrx-api/infrx/state/
- apps/infrx-api/tests/d/

Shared composition roots, global types/manifests/locks and navigation remain coordinator-owned after foundation. Feature tests belong to this track; root integration tests belong to E. Request shared changes with exact imports/config needed.

## Immediate Next Steps

1. Inspect the manifest and claim one eligible task with the coordinator; record base SHA and dependency evidence.
2. Create an isolated worktree per the execution protocol, then read the listed source files and relevant task below.
3. Write/run the specified failure regression, implement the smallest complete unit, and return evidence plus a reviewable commit.

## Decisions Made

Implement the authoritative transactional state machine for every accepted request and all billable/external side effects. Use the shared contracts even when developing with fakes; no private replacement for another track’s state/service boundary.

## Assumptions Made

Start dependencies follow manifest v3: reviewed code/fixtures may enable development; new F2R/F2P/D1R gates require acceptance evidence. Integration dependencies may be replaced with contract fakes only during development. Environment defaults are provisional until measured; cloud/GPU/provider tests require an allocated environment and secrets supplied outside the repository.

## Potential Gotchas

Existing models.limits already exists. Existing HTTP status stays numeric. Broad UPDATE grants can defeat protected new columns. Unknown usage must never become a later surprise debit. All locks must follow one global order.

## Task breakdown

### D1 — Migrate durable schema, wallet summaries and permissions

**Start after:** F2. **Integrate after:** none beyond the start/code gate.

**Imported baseline status:** `integrated` at wave 2; preserve completed work. Product revision/integration follow-up: `D1R`. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Create jobs, attempts/leases, staged refs, capacity reservations, holds, price versions, stream chunks, outbox, idempotency tombstones, feedback and judge coordination tables. Add stable unique keys and state checks/indexes. Preserve historical USD separately; add CREDIT wallets and one-time 10,000 grants unique by individual, with exact decimal arithmetic and migration fixtures. Add role/RLS/column-grant matrix, explicit RPC permissions and service-only mutation boundaries. Reserve migration numbers centrally.

**Acceptance:** Upgrade from a seeded copy of current schema preserves every balance/usage value; member cannot forge protected state; all new tables have bounded access/index plans and reversibility notes.

**Required test oracles:** DUR-RLS, DUR-CAP.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### D2 — Atomic admission, durable preparation and dispatch outbox

**Start after:** D1R. **Integrate after:** none beyond the start/code gate. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Implement stage-ref validation, idempotency payload comparison, authorization recheck, stable lock order, admission capacity and maximum hold in one transaction. Include prepare-to-queued transition, both outbox types, expiry/GC and delivery acknowledgment. Test lost acknowledgment and duplicate delivery independently of queue backend.

**Acceptance:** Each acknowledged acceptance owns exactly one job/hold/capacity pair and dispatch intent; rejects own none; changed idem payload 409; replay returns original identity.

**Required test oracles:** DUR-ADMIT, DUR-CAP, DUR-OUTBOX.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### D3 — Fenced leases, recovery and cancellation

**Start after:** D2, F2P. **Integrate after:** none beyond the start/code gate. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Implement atomic claim/generation increment, DB-clock heartbeat, cancellation and lease reaper. Fence preparation too. Define prepublication retry counter and absolute deadline checks. Expose typed conflicts for stale attempts. Race cancel/complete and recovery/heartbeat under actual PostgreSQL transactions.

**Acceptance:** Only one active attempt can mutate state; prepublication retries are bounded; publication marker permanently prohibits regeneration; cancellation releases resources through terminalization.

**Required test oracles:** DUR-FENCE, DUR-OUTPUT.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### D4 — Persistent stream journal and replay

**Start after:** D3, F2P. **Integrate after:** none beyond the start/code gate. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Implement bounded batch append, output-owner marker, generation/sequence IDs, tenant-scoped cursor reads and expiry. Reserve journal capacity at admission using F2 limits; implement explicit gap handling, pruning and usage metrics. Append checks state/generation/lease in the same transaction; commit before any subscriber notification.

**Acceptance:** Replayed output exactly matches committed events; stale writer fails; no silent gaps or publication before commit; journal overflow has a bounded failure path.

**Required test oracles:** DUR-OUTPUT, DUR-FENCE.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### D5 — Terminal transaction, grants and reconciliation

**Start after:** D4, F2P. **Integrate after:** none beyond the start/code gate. Manifest v3 and the revision handoffs govern current scope.

**Implementation:** Complete result-ref validation, outcome/usage insert, fixed-price decimal settlement, hold/capacity release, terminal journal event and outbox atomically. Implement audited idempotent operator grants and summary/ledger reconciliation. Cover platform-free errors, known-use cancellation, unknown-use quarantine and fenced terminal release after 24h; late data internal-only.

**Acceptance:** Duplicate terminal calls cannot double debit; terminal success cannot exist without usage/result; historical usage is not charged; wallet summary equals immutable ledger and active holds.

**Required test oracles:** DUR-SETTLE, DUR-CAP, DUR-OUTPUT.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

### D6 — Durable feedback and judge coordination

**Scheduling:** Retired mixed task. Use `D6F`, `D6J` from the amendment briefs. The algorithm below is historical reference.

**Implementation:** Implement feedback ownership on durable records with idempotency/outbox and authenticated provenance. Add operator-only calibration membership. Implement budget locking/reserve/settle across outstanding runs, unique submit intent, consent snapshot refs, ambiguous quarantine and replay-safe collection keys. Return typed APIs for Python and console RPC adapters.

**Acceptance:** Feedback 201 can be recovered before CH projection; customer cannot stamp operator labels; simultaneous judge runs cannot exceed available reserved budget or create duplicate submit intents.

**Required test oracles:** FEEDBACK-ACK, JUDGE-BUDGET, DUR-RLS.

**Deliverable:** owned implementation and regression tests, focused test results, any interface/migration notes, and a task-specific evidence report containing the implementation SHA. Do not mark integrated until real integration dependencies pass.

## Closed-loop verification

Use the test IDs above as explicit pass/fail oracles, not checklist prose. Unit tests prove local behavior; shared contract fixtures prove interchangeability; real service tests prove integration. Exercise failure paths as well as success, retain a failing reproduction, fix in the owning module and rerun affected cases.

Planned focused suite: apps/infrx-api/tests/d/

After F2 establishes discovery, run the focused suite and relevant contract suite. Also run the existing Python baseline for gateway/runtime changes; console tests and lint for console changes; full console build for integrated UI/runtime changes. Record exact executable commands from the pinned environment in evidence. Unavailable external tests are pending, not skipped passes.

## Integration and rollback

Submit only owned paths. Coordinator handles shared wiring and migration order, then runs dependent suites on the merged SHA. Follow additive schema and drain/fence rollback rules; never bypass accounting or discard jobs as a shortcut. Feature-disable may stop new activity but must preserve existing durable state and retention obligations.

## Copyable Claude Opus 5 prompt

```text
Implement one eligible task from research/plan/handoffs/D-durable-state.md in an isolated worktree. Read CLAUDE.md, HANDOFF.md and the handoff's shared-contract links first. Inspect the current checkout and newer coordinator evidence. Select the earliest unclaimed task whose start dependencies are integrated; report its ID and base SHA. Follow its owned paths and test oracles, use contract fakes only until integration dependencies exist, and do not modify shared wiring or another module's files independently. Deliver a reviewable commit and task evidence using research/plan/evidence/README.md. Do not claim mock-only work is integrated, run unauthorized live/paid operations, or implement deferred scope. Stop at this task's completed handback; leave later tasks explicit.
```

## Verification log

- 2026-09-20: Authored from reviewed repository/specs and accepted decisions. All tasks remain planned; test IDs are required future evidence.
