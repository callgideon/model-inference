# Handoff — continue after the wave-2 product audit

> **Superseded entry point:** use [the complete fresh-session handoff](16-fresh-session-handoff.md) and [build plan](12-complete-build-plan.md). This audit handoff remains historical context. The latest priority is Marlin SOP inference App first; the prompt below predates that priority and the fully decomposed Lab backlog. Its 77-record counts refer to manifest v3 at audit time.

## Session Metadata

Prepared 2026-09-21. Repository: `model-inference`. Imported implementation base: `271add946771ddc4efc3cbc2044758443080759b` from `origin/main`. Audit branch: `codex/wave2-platform-audit`. Before creating worktrees, use the actual committed audit handback SHA and inspect any newer main commits. Never use an uncommitted coordinator tree as a base.

## Current State Summary

Wave 2 is complete under the original v1 implementation plan: all eleven modules are merged, D1 has imported real-database evidence, and the other ten are implemented without full product integration. The audit preserves that work and revises it for **consumer App** and **provider Lab**. Wave-3 feature implementation has not started.

The common v1 contracts and SQL still encode USD/org wallets. The target requires CREDIT wallets owned by individual consumers, 10,000 credits once per individual user, provider dev wallets and distinct permission audiences. These are new contract/migration tasks, not a terminology change. Lab currently contains a README only.

## Important Context

Read [the audit](10-wave2-platform-audit.md), [revision handoffs](11-wave3-revision-handoffs.md), [product index](../platforms/README.md), [credits](../platforms/02-credits.md), [manifest v3](tasks.json) and your original module brief. Current audit/manifest instructions supersede the old wave-2 handoff's next-step list. Preserve its evidence, accepted R1–R60 rulings and corrections where unchanged.

`apps/app` serves model consumers. `apps/lab` serves model providers. Shared backend services remain shared. Provider membership and model ownership do not grant access to customer content; source-purpose grants are independent of capture consent. Public inference retains durable acceptance, reservations, fenced execution, committed output and exact terminal accounting. App can launch with capture disabled and does not wait for Lab or a live judge.

## Immediate Next Steps

1. Review/commit the S1 audit handback and confirm the common base. Reconcile only changes newer than the imported SHA; do not repeat completed F1/F2/D1 work.
2. F2R closes the **remaining** original F2.2 carryovers. I0 installer tests and E2R service-harness repairs may proceed independently in separate worktrees. The audit disposition table identifies fixes already made and work transferred to later owners.
3. F2P encodes versioned CREDIT/identity/serving/provider contracts and fixtures. Publish a reviewed commit before new feature owners consume them.
4. Dispatch D1R, C0, G1R, M/Q/W, A and L slices against that base. D1R uses additive migrations after 0005. C0 wires App database reporting independently of Lab C2; U1R adapts usage/balance views to CREDIT while retaining explicit USD history. Use manifest start/integration edges and path ownership to maximize useful parallelism.
5. Integrate D2–D5, M2, Q3 and W2 before G2 mounts the new runtime; I0 is mandatory. Build/test drafts earlier against committed fakes, but do not claim them integrated.
6. E3A verifies the consumer journey; I2A/I3/E4 need allocated deployment evidence. L1–L4/E3L/I2L are independent provider operations. V1M moves the existing trace explorer to Lab after shell/permissions; T/C2/J/V2/V3 feed E5L.

## Architecture Overview

Separate UI/deployment/auth boundaries, shared identity/registry/rates/runtime. PostgreSQL owns jobs, wallets, leases, journal and settlement. Valkey is rebuildable. Optional capture cannot lose accounting. Serving revisions pin weights, adapters, prompt/harness, preprocessing and runtime; admission also pins deployment and rate. Consumer CREDIT, provider dev CREDIT and external-provider USD budgets have explicit ownership and cannot be mixed.

## Critical Files

- [Audit and all F2.2 dispositions](10-wave2-platform-audit.md)
- [Nine executable revision handoffs](11-wave3-revision-handoffs.md)
- [Task graph](tasks.json): 77 records, 71 active, six retired mixed tasks
- [Other amendment task briefs](09-amendment-workstreams.md)
- [Target contracts](01-contracts.md) versus [implemented v1 encoding](08-contracts-v1-encoding.md)
- [Additive database target](06-database-map.md)
- [Original coordinator evidence](evidence/coordinator/STATUS.md)
- [Local verification and limitations](evidence/wave2-platform-audit.md)
- [App](../platforms/03-app-spec.md) and [Lab](../platforms/05-lab-spec.md) specifications and their adjacent roadmaps

## Files Modified

The audit combines the earlier product documentation with wave-2 implementation evidence. It adds nine revision tasks, corrects dependencies/authority entry points, and makes bounded local fixes: fixture preview gating, exact held balances, deadline clamp, decimal context isolation, portable descriptor/process checks and stronger mutation oracles. SQL history, dependency locks and production settings are unchanged. Provider UI has not yet moved, signup credits are not implemented, and no runtime cutover occurred.

## Decisions Made

Preserve completed modules and migrations. Revise contracts explicitly before further integration. Add C0 for real consumer reporting, separate from C2 Lab content. Use I0 as a cutover prerequisite rather than making G2 depend on the entire I2A deployment task. Move V1 through V1M only when Lab shell/permissions exist. Keep all 16 original F2.2 items assigned and all old task IDs traceable.

## Assumptions Made

Verified signup, immutable personal wallet binding, operator-approved initial rates and no promotional expiry remain documented defaults. Production rates, affected nonzero USD account transition, Lab origin and GPU environment are release inputs. No exchange rate is chosen. Other-system worktrees and hosted state must be inspected when relevant; this audit only verifies imported commits and local tests.

## Potential Gotchas

- `implemented` under v1 does not mean product-v2 compatible, real-service integrated or live.
- 0003–0005 exist and have USD constraints/triggers; never edit them into a CREDIT schema.
- Usage/Balance/Traces previews require an explicit development flag; production does not show fake account reports. C0 must provide real App data before release.
- Legacy fallback is permitted only when the summary function is absent; an existing function returning a missing row/error must not bypass holds.
- Local Docker is unavailable in audit evidence; skipped SQL tests and Layer 1 do not certify Layer 2.
- D's fixed test container ownership still needs E2R before concurrent database suites.
- Main auto-deploys App. Merge/push and cloud changes require their actual reviewed release context, not an assumption based on historical session instructions.

## Verification

Use [the audit evidence](evidence/wave2-platform-audit.md) for actual outputs. Review at handback HEAD remains required before a coordinated merge. No hosted migration, live GPU run, paid judge call, independent review signoff or deployment is claimed.

## Copyable prompt

```text
Continue model-inference after the wave-2 product audit. Ignore implementation-model-specific terms in historical notes.

First inspect git status/log/worktrees and identify the committed audit handback on codex/wave2-platform-audit. Main was imported at 271add946771ddc4efc3cbc2044758443080759b. Preserve newer work and original module evidence; do not restart wave 2 or branch from an uncommitted tree.

Read CLAUDE.md, research/plan/10-wave2-platform-audit.md, research/plan/11-wave3-revision-handoffs.md, research/plan/PLATFORM-SPLIT-HANDOFF.md, research/platforms/README.md and research/plan/tasks.json. The old wave-2 handoff is historical evidence; its wave-3 ordering is superseded.

apps/app is consumer inference: only a free plan initially, public verified signup, 10,000 CREDIT ONCE PER INDIVIDUAL USER, model catalog, API keys, exact balances and own usage. apps/lab is provider operations: models and serving versions, dev/prod endpoints, scoped evidence/review/evaluation and later data/training. They share backend services but have separate permissions and release gates. Preserve USD history without conversion; provider ownership does not imply customer data rights.

Start with remaining F2R repairs; I0 and E2R can run independently in isolated worktrees. Then F2P freezes product-v2 contracts. D1R adds migrations after 0005, C0 connects real consumer reporting, U1R adapts credit-aware usage/balance views, G1R revises credential/endpoint audiences, and V1M moves the implemented trace UI only after Lab shell and permissions exist. Follow the manifest for parallel module development and real integration dependencies. Do not mount G2 until D5/W2/M2/Q3/I0 are proven together.

Use the canonical make targets and report all skips. Review each task at its current HEAD with meaningful adversarial cases and intentional defect detection, fix findings, then rerun affected checks on the merged tree. Fake-only work is implemented, not integrated. Preserve append-only verification history. Do not deploy, apply hosted migrations, expose customer content or call paid providers as an implicit consequence of this handoff.
```
