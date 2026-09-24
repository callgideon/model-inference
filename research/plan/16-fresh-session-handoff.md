# Handoff — complete the Marlin2B inference backend first

**2026-09-24 superseding dispatch:** [Program 22](22-consumer-v1-implementation.md), [handoff/prompt 24](24-consumer-v1-session-handoff.md) and the current [manifest](tasks.json) replace older next-task lists in this document. Existing implementation is preserved; S3 reconciles it, F2C freezes corrections, E3C/E4C close backend readiness, then App. Use this document for unchanged baseline contracts/ownership and historical context, not to restart completed waves.

> **Historical dispatch prompt, superseded for current state (2026-09-24).** The backend wave described below has largely landed. Start with [handoff 20, §14](20-platform-handoff-2026-09-24.md), [the operational tail](../../HANDOFF-20260924T2115Z.md) and [consumer v1 review 21](21-v1-consumer-readiness-review-2026-09-24.md). Do not rerun the completed F/D/M/Q/W/G waves from this prompt. Its product constraints remain applicable; its next-task list does not represent current dispatch.

## Session Metadata

Updated 2026-09-22 in `model-inference`, branch `codex/wave2-platform-audit`. Imported implementation main: `271add946771ddc4efc3cbc2044758443080759b`; audit: `07dfb64`; complete App/Lab plan: `2ea562a`. Use the latest committed branch tip containing [the backend-first handoffs](18-marlin-backend-first.md), not the old App-first prompt at `2ea562a` alone. Reconcile newer main/other-session commits before choosing an integration base. This handoff supersedes the previous dispatch order, not its completed evidence or later product scope.

## Current State Summary

Wave 2 is imported/audited; subsequent runtime/product features have not been implemented here. Original foundation/data/test/infrastructure integration and ten implemented wave-2 modules remain valuable, but the new durable runtime is not composed and CREDIT/identity revisions remain pending. `apps/lab` is a README only. The user's latest direction is to **complete all backend work for robust and optimized Marlin2B inference via an endpoint first**, then launch App, then build Lab. SOP verification over large robotics datasets remains the lead end application.

## Important Context

- The current deliverable is a **headless authenticated endpoint**, with real key/tenant controls, rate/serving pins, secure finite-video preparation, sync/output SSE and explicit async, durable jobs/journal/settlement, recovery/restore and operational telemetry. Both Next.js applications can remain stopped.
- G6B provisions/operates real clients using shared auth/state, A1 individual entitlement and D financial functions. Public signup pages/email flows, catalog/usage/balance dashboards and Lab are later. Never create a temporary unmetered or shared-secret shortcut.
- App product policy is unchanged: free plan, **10,000 CREDIT once per individual user**, no refill. Preserve historical USD without conversion; provider_dev wallets start at zero; external paid-provider USD budgets are separate. All amounts remain exact/string encoded.
- PostgreSQL owns accepted jobs, holds, leases, stream journal and settlement. Valkey is rebuildable. Optional trace capture may be off and may drop with metrics; accounting cannot. Ordinary chat never silently becomes async202; committed output cannot be regenerated.
- S2M pins the actual Marlin artifact/processor/protocol. A VLA application context or robotics dataset does not establish action generation, closed-loop actuation or native live input. Use bounded finite-video items and resumable client workflows for the large dataset.
- Optimization starts with E1B's measured representative baseline, then M4 video preparation and W4 engine/scheduling experiments, then E4B combined final evidence. “Optimized” means a measured supported configuration, not a universal maximum or unverified quality-preserving compression.
- Provider membership is not customer-content permission. Main auto-deploys App. Review-branch implementation does not implicitly authorize public cutover, hosted migration, paid calls or new compute purchases.

## Immediate Next Steps

1. Inspect status/log/worktrees/remotes, fetch the latest audit-plan branch, identify its committed SHA containing `18-marlin-backend-first.md`, and preserve newer work. Read root conventions plus the files below. Review current audit HEAD; do not restart wave 2.
2. Start F2R while I0, E2R and S2M run in disjoint worktrees where available. Publish reviewed F2P contracts before their consumers. Preserve original fixes and shared v1 rulings where unchanged.
3. Dispatch the **E4B dependency closure** only: D1R/D2–D5 and D-owned A1; G1R/G2/G3/G4U/G6B; M2/M3, Q2/Q3, W2/W3 plus existing foundations. Only D edits migrations; one coordinator owns common contracts/config/wiring/lockfiles. No frontend C/U/A2/A3 lane is required.
4. E3B proves actual local DB/auth/storage/queue integration and endpoint behavior with controlled engine and App/Lab absent. Integrate real durable/runtime/media/scheduler/installer prerequisites before mounting G2; fake-only work is not integrated.
5. I2B deploys the backend on an allocated target. I3B proves telemetry, recovery, restore and rollback; E1B establishes real-GPU phase measurements and capacity baseline. M4/W4 then test controlled optimizations. E4B repeats protocol/security/accounting/parity/load/soak/fault checks on the combined final candidate.
6. Record missing GPU/rate/legacy-account/workload inputs precisely and continue independent backend work. Do not substitute App UI work for a blocked backend gate. Once the backend candidate is accepted, activate App and reuse E3B/I2B/I3B/E4B; Lab follows App. The full later roadmap remains ready.

## Architecture Overview

The API/runtime serves real clients independently of the presentation apps. Shared protected operations own identities, keys, registry/rates and exact accounting; the future App invokes those same primitives. Shared migrations remain at `apps/app/supabase/migrations/` under D ownership without requiring a running frontend. Serving pins cover model, processor, prompt/harness, runtime and hardware; accepted jobs also pin rate/deployment. Single-GPU restart/recovery is distinct from multi-host availability; I4 is a separately justified backend fleet gate, independent of UI readiness.

## Critical Files

| File | Purpose |
|---|---|
| [Backend-first scope and handoffs](18-marlin-backend-first.md) | Current objective, eight added packages, measured optimization and acceptance |
| [Complete platform plan](12-complete-build-plan.md) | Backend → App → Lab sequencing, S2M and ownership |
| [Manifest](tasks.json) / [task ledger](17-task-ledger.md) | Exact active dependencies, preserved statuses and current dispatch scope |
| [Pending inputs](15-pending-inputs.md) | F2.2 dispositions and 18 product/environment inputs, including performance/availability criteria |
| [Revision briefs](11-wave3-revision-handoffs.md) | F2R/F2P/D1R/I0/E2R/G1R and later UI revisions |
| [Contracts](01-contracts.md) / [protocols](02-durable-protocols.md) | Durable runtime and compatibility invariants |
| [Execution](03-execution-protocol.md) / [verification](04-verification.md) | Worktree ownership, failure oracles and evidence rules |
| [Audit evidence](evidence/wave2-platform-audit.md) | Actual previous tests and environment limitations |
| [Later Lab briefs](13-lab-improvement-handoffs.md) / [extensions](14-expansion-gates.md) | Follow-on scope; not backend blockers |

## Files Modified

This priority revision changes documentation, task graph, gate definitions, requirement mapping, ledger validator and prompts. It adds G6B/E3B/I2B/I3B/E1B/M4/W4/E4B as planned backend packages and reuses their results from later App tasks. It preserves all previous task statuses and source implementation. No backend optimization, runtime cutover, GPU trial or hosted operation is claimed by authoring this plan.

## Decisions Made

| Decision | Rationale |
|---|---|
| Backend readiness independent of App | Old I2A/E3A/I3/E4 tied runtime proof to browser features, contrary to latest priority |
| Headless provisioning over real shared primitives | A usable endpoint needs keys/tenants/accounting even before self-service signup |
| Profile then tune then revalidate combined candidate | Prevent throughput improvements from hiding preprocessing, tail-latency, fairness or recovery regressions |
| Keep one durable state/financial implementation | Backend-first is a sequence change, not a new legacy mode or separate wallet system |
| App gates become reuse plus frontend deltas | Avoid duplicating runtime deployment/recovery and wasting previous evidence |
| I4 follows backend envelope, conditionally | Capacity/availability may require fleet independently of a consumer dashboard; no unsupported HA claim |

## Assumptions Made

S2M/E1B will freeze actual inputs/limits and predeclare workload targets or explicitly provisional engineering criteria. Existing model and GPU access must be verified before live evidence. Production rates and legacy-account transition remain release inputs. Test identities use the real backend auth model; provider/customer data rights stay explicit. Credentials are supplied through approved secret/environment flows, never in this handoff.

## Potential Gotchas

- Starting from `main` or `2ea562a` alone may miss this new sequence; fetch the committed tip containing the backend-first file.
- Never rewrite migrations 0001–0005 into CREDIT or convert USD history in place. A1 is D-owned backend entitlement, not a dependency on signup UI.
- Audit `make check` passed with Docker-dependent skips. Actual service integration remains required; Layer1 socket tests are not DB proof.
- Existing `serve.sh` defaults to a nightly image. W3 pins the supported image/model/processor; W4 validates tuning on that exact build rather than assuming current engine flags.
- Large-dataset throughput needs representative diverse inputs and rejection/error accounting; repeated cached clips and undersampled percentiles do not establish an operating envelope.
- Full content tracing, judge, Lab datasets/training, native streaming, action-policy hosting and new chips are not implicit endpoint requirements. Operational metrics and recovery tools are required.
- The pre-pull preservation stash may remain; do not pop/drop it blindly. No independent reviewer signoff is invented.

## Verification

The earlier audit evidence records passing root checks with explicit skips and Layer1 integration/canaries; actual Docker services, hosted state and GPU evidence were unavailable in that audit. This planning revision uses documentation consistency checks, not runtime retesting. No model-quality, throughput, cost or reliability result is newly claimed.

```bash
python3 research/plan/scripts/validate_plan.py
```

Implementation uses the root `make` targets and the relevant module/integration commands from [verification](04-verification.md). Record command/environment/seed/SHAs/results/skips/raw artifact hashes; review current HEAD, fix defects and rerun affected merged-tree checks. Use [the evidence format](evidence/README.md).

## Copyable coordinator prompt

```text
Complete the model-inference backend for robust, measured and optimized Marlin2B inference via an endpoint FIRST. SOP verification over large robotics datasets is the lead application. App UI launch comes after the backend; provider Lab comes after App.

Inspect git status/log/worktrees and fetch origin/codex/wave2-platform-audit. Use its latest committed tip containing research/plan/18-marlin-backend-first.md, preserving newer work. Main was imported at 271add946771ddc4efc3cbc2044758443080759b; 07dfb64 is the audit and 2ea562a the earlier App-first plan. The current backend-first amendment supersedes that dispatch order. Create isolated worktrees from a recorded committed integration SHA.

Read CLAUDE.md, research/plan/16-fresh-session-handoff.md, 18-marlin-backend-first.md, 12-complete-build-plan.md, 15-pending-inputs.md and tasks.json; then shared contracts/protocols and your module briefs. Ignore historical model-specific orchestration terms. Preserve wave-2 code/evidence. Implement the backend, not another planning-only pass.

Review audit HEAD, close F2R with I0/E2R/S2M in independent worktrees where available, then F2P. Dispatch the E4B dependency closure: durable D/M/Q/W/G runtime plus D-owned A1 and G6B headless provisioning. Only D edits migrations; coordinator owns common wiring/config/lockfiles. Integrate real dependencies before G2 cutover. Do not build signup/catalog/usage dashboards or Lab now.

The endpoint must use real scoped credentials, tenant authorization, model/rate pins, CREDIT holds/settlement, bounded secure media, supported sync/SSE and explicit async, idempotency, cancellation/replay and recovery. Preserve legacy USD and one-time 10,000 CREDIT per individual; reuse shared financial functions rather than scripts that edit balances. App and Lab must be unnecessary for backend acceptance. Pin actual Marlin capabilities; the VLA/SOP context does not imply robot actuation or native live input.

Pass E3B against actual local services. Use I2B for allocated backend deployment; I3B for telemetry/recovery/restore/rollback; E1B for actual-GPU baseline and phase profiling. Then M4/W4 optimize video preparation and engine/scheduling using controlled experiments. E4B validates the combined final candidate with representative sustained/burst/soak/fault loads, exact accounting and quality/parity. Report cost, error/rejection denominator and latency/throughput within the measured envelope; do not invent performance targets or claim single-GPU high availability.

Keep working through backend implementation, review and closed-loop tests. When live resources or rate/workload inputs are missing, record the exact blocked gate and finish independent backend work. Do not substitute UI work or treat skips/fakes as passing live evidence. After accepted backend readiness, the existing App and Lab plans define subsequent phases.

Integrate and hand back on a review branch with committed code, actual evidence, unresolved inputs and next task. Main auto-deploys; public cutover, hosted migration and additional paid infrastructure require their actual release scope. Never implicitly expose customer content or actuate hardware.
```

## Module-session prompt template

```text
Implement <TASK_ID>, slices <SLICES>, in <WORKTREE/BRANCH> from reviewed committed <BASE_SHA>, integrating toward <INTEGRATION_BRANCH>. Owned paths: <OWNED_PATHS>. Coordinator fills these values before dispatch.

Read research/plan/16-fresh-session-handoff.md, 18-marlin-backend-first.md, tasks.json, the selected brief and oracle definitions. Backend-first scope governs. Reuse the shared state/auth/financial model; do not duplicate it or edit other owners’ files. Start-ready fakes permit coding, not integrated status.

Implement and test the assigned slices, inject the stated faults, review current HEAD, fix findings and rerun affected checks. Request common wiring/schema changes through the coordinator. Return code SHA, exact commands/environment/skips/raw evidence, reviewed findings, changed contracts, migration/rollback, missing inputs and next unblocked task. No production or shared task-manifest edits by module workers.
```

## Continuation record

Append current scope/integration SHA, task/slice/owner/worktree/base, changed paths, review SHA/findings, test commands/environment/results/skips/artifacts, status by gate, unresolved P-input IDs, rollback and next task under `research/plan/evidence/coordinator/`. Coordinator updates manifest states only after evidence; retain historical records and distinguish implemented, locally integrated, release-ready and deployed.
