# Coordinator handoff — implementation continuation

**Current priority (2026-09-22):** [Marlin backend first](18-marlin-backend-first.md). E3B/I2B/I3B/E1B/M4/W4/E4B separate endpoint readiness and measured optimization from the later App browser/deployment gates; G6B provides protected headless operations. Use the updated [fresh-session handoff](16-fresh-session-handoff.md) and manifest for dispatch.

**Dispatch authority:** [complete build plan](12-complete-build-plan.md), [fresh-session handoff](16-fresh-session-handoff.md), [pending inputs](15-pending-inputs.md) and manifest v4. Current scope is BACKEND-FIRST-MARLIN. Follow S2M with endpoint completion and measured tuning; App then Lab follow accepted candidates. Older next-session text below is historical where it differs.

> **Current authority:** [wave-2 audit](10-wave2-platform-audit.md), [revision handoffs](11-wave3-revision-handoffs.md), [continuation prompt](PLATFORM-SPLIT-HANDOFF.md) and manifest v4. Wave 2 is imported at `271add9`; preserve its completion evidence. The older coordinator instructions below are historical where they conflict.

## Current State Summary

Wave 2 is merged at the imported main SHA `271add9`. The local audit reconciles the two-platform requirements, adds explicit contract/schema revisions and fixes bounded defects. Original integrated/implemented statuses remain intact; product-v2 compatibility is tracked separately. Use the actual committed audit handback as the base for new worktrees.

## Important Context

The consumer release is a free single-GPU pilot with public verified signup, 10,000 CREDIT once per individual, exact holds/settlement, explicit async, secure media and own usage. Provider operations, trace analysis and evaluation have separate Lab gates. Payments, commercial second-owner onboarding, OpenRouter and managed training remain later. Continue inference on optional capture loss; retain the 24h results/7d cache/up-to-90d content/13mo metadata policies. Historical production state requires read-only revalidation before deployment.

The complete authority begins with [product architecture](../platforms/README.md) and [the amendment](08-platform-split.md), then [scope](00-decisions-and-scope.md), [contracts](01-contracts.md), [durable protocols](02-durable-protocols.md), manifest v4 and the assigned brief. Historical research remains supporting context. Preserve research verification history; append new evidence. No secret values belong in handoffs, logs or commits.

## Immediate Next Steps

Follow the continuation handoff: S1 review → F2R (parallel I0/E2R/S2M) → F2P → D1R/D/M/Q/W/G/A1 backend slices → E3B → I2B/I3B/E1B → M4/W4/E4B. Integrate the full durable runtime before G2 cutover. Frontend and Lab slices follow accepted backend readiness. Manifest v4 gives exact dependencies; the old G2-before-W2 sequence is withdrawn. Preserve code and evidence from the original package.

## Architecture Overview

PostgreSQL owns durable jobs, holds, leases, stream journal, usage, feedback and evaluation coordination. Valkey/memory scheduling is an index. Media and result objects are immutable, tenant scoped and expiry controlled. Stream output is persisted before relay, terminal success after settlement. Trace capture is bounded and may drop with metrics; fsynced local spool is its durability boundary. ClickHouse is a deduplicated projection. Console services centralize tenant/role checks; UI work can run against shared fixtures.

## Critical Files

- [Root handoff](../../HANDOFF.md): historical live inventory and source map.
- [Project conventions](../../CLAUDE.md): source/research and implementation rules.
- [Package index](README.md): tracks and delivery sequence.
- [Task manifest](tasks.json): start and integration dependencies, owners, tests and planned statuses.
- [Evidence template](evidence/README.md): mandatory implementation handback content.

## Files Modified

Historical initial preparation changed only Markdown documentation and the documentation task manifest: the new plan package, root handoff/conventions/index, and relevant research/application spec supersession notices. No runtime code, migrations, dependencies, infrastructure resources or implementation worktrees were created. An old plaintext development-account password was removed from the current handoff; no credential value is copied into this package.

## Decisions Made

Current scope is recorded in the two-platform product docs and amended DEC-01 through DEC-10: individual 10,000-credit grant, separate CREDIT/USD units and independent App/Lab releases. PostgreSQL authority and explicit async remain. Worktree boundaries keep migrations with D, actions with C, ClickHouse schema with T and integration tests with E. Provider UI V goes to Lab. Shared composition, dependency locks and package extraction have one coordinator owner.

## Assumptions Made

Later sessions receive this repository and assigned task/environment, with credentials delivered separately. Local fake and real-service tests precede allocated cloud/GPU work. Current live inventory, cloud prices and provider capabilities may have changed; implementation owners verify them before relying on them. Performance limits in the plan are engineering defaults until measured.

## Potential Gotchas

Do not treat historical A0 fixes as new work: several already exist. Do not change to202 on ordinary chat overload, regenerate after committed output, settle unknown usage from chunk counts, or revert to an unmetered runtime. Do not use trace consent as judge consent or console origin as operator provenance. The DB region differs from the GPU region; journal latency is a pilot gate. Old research code examples are illustrative where this package supersedes them.

## Verification and next-session prompt

Planning observed 23 Python tests, 4 console tests and console lint passing before this documentation work; these are baseline observations only. Documentation validation is recorded separately in [the preparation report](evidence/documentation-review.md). Future tests, live fault drills and rollout gates remain unexecuted.

```text
Act as implementation coordinator. Start with research/plan/PLATFORM-SPLIT-HANDOFF.md and reconcile the work already running on this system. Read product architecture, amendment mapping/briefs and manifest v4 before assigning active tasks. Preserve compatible completed work and amend F2 contracts before merging conflicting credit/ownership changes. Follow independent App/Lab gates, shared-file ownership, durable protocols and evidence rules. Do not claim mock-only work integrated or exceed this session's environment authorization.
```

## Verification log

- 2026-09-20: Completed from session-handoff scaffold and repository review for external implementation sessions; application work remains planned.
