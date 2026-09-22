# Handoff — build the Marlin consumer App, then the provider Lab

## Session Metadata

Prepared 2026-09-21 in `model-inference`, branch `codex/wave2-platform-audit`. Main implementation baseline: `271add946771ddc4efc3cbc2044758443080759b`. Audited baseline: `07dfb64`. This package is a follow-on documentation commit; use the current committed branch tip containing this file, not an uncommitted tree. Continues [the platform-split handoff](PLATFORM-SPLIT-HANDOFF.md) and supersedes its dispatch scope/prompt with the App-first priority. Historical evidence remains valid only for its recorded SHA/environment.

## Current State Summary

Wave 2 is imported and audited; wave-3 feature implementation has not started here. Five original foundation/data/test/infrastructure tasks are integrated and ten wave-2 modules are implemented under the old v1 contract. S1 audit is implemented pending independent review. There is no composed new runtime or product-v2 CREDIT schema yet. `apps/lab` is only a README. The latest user priority is **Marlin2B SOP verification over large robotics datasets**, starting with **launching the consumer inference App**. Full later Lab implementation is planned, not the immediate dispatch scope.

## Important Context

- `apps/app`: public verified signup, only a free plan, **10,000 CREDIT once per individual user**, keys, published Marlin endpoint, secure finite-video inference, explicit async, exact balances/holds/usage and recovery.
- `apps/lab`: later provider roles, model/serving versions, dev/prod, permitted traces/review, datasets/evaluation, external annotation/training and controlled rollout. App launch does not depend on Lab.
- The user calls the application a VLA use case. Verify the actual Marlin artifact/request/response contract; do not assume action output, closed-loop actuation or native live-video input. Recorded robotics-data SOP analysis is the current context.
- Preserve historical USD without conversion. Grants are unique per individual, not org/campaign. Provider_dev CREDIT starts at zero; paid external USD budgets are separate. All decimal quantities are exact/string encoded.
- PostgreSQL owns accepted jobs, holds, leases, journal and settlement; Valkey is rebuildable. Optional trace loss must not lose accounting. Ordinary chat never silently becomes async202. No regeneration after committed output.
- Provider membership/model ownership is not customer-content permission. Recheck source-purpose grants for access/export/teacher/training and revocation; capture consent alone is insufficient.
- Main auto-deploys App. Integrate on a review branch; do not make a main merge/push, hosted migration, paid provider call or deployment an implicit step of this prompt.

## Immediate Next Steps

1. Inspect status, branch, log, worktrees and remotes. Fetch the audit/complete-plan branch and identify a committed integration base containing this file and `07dfb64`. Preserve newer main or other-session work. Review the audit diff at current HEAD; do not redo wave 2.
2. Read [the complete plan](12-complete-build-plan.md), [pending inputs](15-pending-inputs.md), [manifest](tasks.json), [App spec](../platforms/03-app-spec.md), [credits](../platforms/02-credits.md), [revision briefs](11-wave3-revision-handoffs.md) and root repository conventions. Load other module briefs only when assigned; the old wave-2 prompt is historical.
3. Start F2R while I0, E2R and S2M run in disjoint worktrees if resources/agents are available. S2M pins actual Marlin launch capabilities and the bounded large-dataset API recipe. F2P follows reviewed F2R contracts.
4. Dispatch only the current App closure: D1R/D2–D5, C0/C3A, G1R/G2/G3/G4U, M/Q/W, A1–A3, U1R/U2/U3 and I/E gates, following exact manifest edges. One D migration owner and one coordinator for common wiring. Integrate actual dependencies before mounting G2.
5. Complete E3A with real local services, then prepare I2A/I3/E4. Missing staging/rate/GPU inputs remain release blockers; finish independent App tests, docs, operational scripts and review. Do not switch to broad Lab feature work just because live release is waiting.
6. Once the App launch candidate is accepted and the next scope is activated, use the already prepared Lab L0–L4 handoffs. Imported dataset evaluation can start without production capture; rollout need not await training. Conditional modality/hardware tasks require their activation contracts.

## Architecture Overview

Two separately deployed frontends reuse identity/registry/rates and durable inference. Shared migrations remain physically under `apps/app/supabase/migrations/` with D ownership. Serving revisions pin weights, adapters, tokenizer/preprocessing, prompt/harness and engine/hardware; admission also pins deployment/rate. Lab workers perform long evaluation/pipeline work outside Next.js handlers. Immutable manifests and current authorization coexist: preserving lineage never grants continued access to revoked content.

## Critical Files

| File | Purpose |
|---|---|
| [Complete build plan](12-complete-build-plan.md) | Current scope, phase order, S2M brief, worktree boundaries and completion levels |
| [Task ledger](17-task-ledger.md) / [manifest](tasks.json) | Every original, revised, new and retired task; exact dependencies and status |
| [Pending inputs](15-pending-inputs.md) | All 16 F2.2 carryovers, decisions, environment inputs and block handling |
| [Revision briefs](11-wave3-revision-handoffs.md) | F2R/F2P/D1R/C0/I0/E2R/G1R/V1M/U1R |
| [Additional Lab briefs](13-lab-improvement-handoffs.md) | Detailed dataset/eval/annotation/training/rollout packages and failure proof |
| [Conditional extensions](14-expansion-gates.md) | Video/robotics/hardware discovery and trial boundaries |
| [Contracts](01-contracts.md) / [protocols](02-durable-protocols.md) | Shared runtime invariants; v1 encoding retained separately |
| [Execution](03-execution-protocol.md) / [verification](04-verification.md) | Isolated worktrees, single writers, current-HEAD review and evidence |
| [Audit evidence](evidence/wave2-platform-audit.md) | What was actually tested at the audit boundary |

## Files Modified

This follow-on package updates plans, roadmaps, task routing and handoff documents; adds complete Lab packages, conditional extension gates, a pending-input register, a generated task ledger and documentation validation tooling. It does not implement CREDIT wallets, Lab, migrations, runtime composition or deployments. The earlier audit commit contains the bounded code fixes listed in its evidence.

## Decisions Made

| Decision | Rationale |
|---|---|
| App-first execution; full Lab plan retained | Matches the user's latest immediate launch priority without losing the broader platform architecture |
| Marlin SOP profile S2M is explicit | Makes documented inference capabilities and large-dataset usage testable without inventing action/streaming support |
| Keep original task statuses and revision tasks separate | Reuse wave-2 work while accurately reporting incompatible target contracts |
| Manual external training connector is the initial real workflow | Integrates existing provider pipelines; avoids claiming an unselected paid training service works |
| Separate local/staging/live gates | Tests against a fake model cannot certify model quality or a deployed endpoint |
| Conditional extensions have explicit activation records | Robotics/streaming/hardware requirements remain workload-specific; speech stays deferred |

## Assumptions Made

Verified onboarding before grant, no expiry/refill, protected personal-wallet binding and operator-approved publication are current defaults. SOP rubric/dataset labels and live rates/environment may require provider/operator inputs. The next session has this Git branch or a merge containing it. Credentials are supplied through the environment/secret manager, never this handoff. No extra platform or framework migration is presumed.

## Potential Gotchas

- `main` alone still may not contain this package. Do not restart from the old handoff because it says main.
- Migrations 0003–0005 already exist; add compatible migrations after them. Never convert historical USD rows in place.
- Audit machine had Docker unavailable: `make check` passed with Docker skips; Layer 1 engine tests are not Layer 2 database proof.
- `implemented` v1 modules need amended integration; Lab README is not a working app.
- Fixture account reporting is disabled in production. Missing/failed real context must be explicit, never a demo balance or zero success.
- Stash from the pre-pull documentation preservation may still exist locally; do not pop/drop it blindly. All necessary audit changes were committed.
- Additional task paths are proposed. F/coordinator must add shared discovery/config once; workers do not invent parallel auth/wallet/job stores.
- No reviewer has yet independently signed off on this complete package. Validate, review and fix before claiming that signoff.

## Verification

Prior audit evidence records `make check` exit0 (API 1518 passed/70 skipped; API mutation suite 955 passed/84 skipped; console255 passed; bench40 passed; lint/typecheck and console mutation oracles passed) and integration Layer1/canary exit0 (engine8/8; integration67 passed/21 skipped; 42 defects detected plus an expected survivor control). These are historical results from the audit, not tests rerun for this documentation package. Full real-service, hosted, GPU and paid-provider checks remain unverified here.

Run documentation consistency with:

```bash
python3 research/plan/scripts/validate_plan.py
```

Implementation uses the root `make` targets and environment requirements in [verification](04-verification.md). Add Lab/test discovery only when that code exists. Record all skips, actual SHAs and failed drills. Keep [evidence format](evidence/README.md) and append-only coordinator history.

## Copyable coordinator prompt

```text
Implement model-inference from the completed two-platform plan, with APP-FIRST priority: launch apps/app for Marlin2B inference serving SOP verification over large robotics datasets. Lab is subsequent work; do not divert into building datasets/training/provider UI before the App launch candidate is accepted.

First inspect git status/log/worktrees. Fetch origin and use the committed tip of codex/wave2-platform-audit containing research/plan/16-fresh-session-handoff.md (or a reviewed branch/merge containing it). It includes the audit of main 271add946771ddc4efc3cbc2044758443080759b and audit commit 07dfb64. Preserve newer work. Main alone may still contain only the old plan. Create an integration branch from the selected committed SHA; do not reset anyone’s checkout.

Read CLAUDE.md (model-neutral repository conventions), research/plan/16-fresh-session-handoff.md, 12-complete-build-plan.md, 15-pending-inputs.md, tasks.json and the App/credit product specs. Then load 11-wave3-revision-handoffs.md, shared contracts/protocols and the selected module briefs. Historical Claude-specific terms are not required tooling. Implement the work; do not stop after another plan.

Preserve wave-2 code/evidence. Review audit HEAD, then F2R with I0/E2R/S2M in isolated worktrees where available, followed by F2P. Use reviewed fixtures and single-owner paths to maximize useful parallel work. Only D edits migrations; the coordinator owns common wiring/lockfiles/manifest. Follow start versus real integration dependencies. Do not mount the new gateway before durable state, worker/media/scheduler and installer prerequisites are integrated.

apps/app has only a free plan: public verified signup, 10,000 CREDIT ONCE PER INDIVIDUAL USER, API keys, actual published Marlin capabilities/rates/examples, secure finite-video inference, explicit async, exact balances/holds and own usage. Preserve legacy USD without conversion. App works while Lab/optional capture/judge are unavailable. Pin the actual Marlin artifact and input/output contract through S2M; the VLA/SOP context does not imply robot actuation or native live streaming. Support large datasets with tested bounded inputs, stable per-item IDs, idempotency and async resume; do not invent a new batch product or claim unmeasured SOP accuracy.

Complete the App task closure and E3A with real local services, then prepare I2A/I3/E4 release evidence. Run focused tests, failure injections and current-HEAD review; fix findings and rerun affected tests on the merged tree. Report all skips and external blockers exactly. Missing live resources do not justify claiming integrated/live status or diverting into a broad Lab build. Finish independent App implementation and release preparation.

Record task/slice assignments, SHAs, commands, evidence, reviews and remaining inputs after each integration. Keep working until the current App scope is complete or genuinely externally blocked. Once the App launch candidate is accepted and the next milestone is activated, follow the prepared Lab M0–M4 handoffs in 13-lab-improvement-handoffs.md; conditional X tasks require 14-expansion-gates.md. Speech and managed RL infrastructure remain deferred.

Integrate on a review branch. Do not implicitly merge/push main (it auto-deploys), apply hosted migrations, buy compute, call paid teacher/training services, expose customer content or actuate hardware. Surface exact release inputs when local work makes the action reviewable. End each session with a committed handback, actual test evidence, pending gates and the next unblocked task.
```

## Module-session prompt template

```text
Implement task <TASK_ID>, slices <SLICES>, from committed base <BASE_SHA> in isolated worktree/branch <PATH_AND_BRANCH>. Integrate toward <INTEGRATION_BRANCH>; owned paths are <OWNED_PATHS>. These substitutions must be filled by the coordinator before dispatch.

Read research/plan/16-fresh-session-handoff.md, tasks.json, 03-execution-protocol.md, the task brief and named oracle definitions. Use the current App-first scope; do not rewrite the overall architecture or duplicate shared auth/wallet/registry/state. Confirm start dependencies are reviewed and committed. Keep real integration dependencies explicit; fakes permit development only.

Implement and test the assigned slices, inject the brief’s failure cases, review at current HEAD and fix findings. Request shared wiring/schema changes through the coordinator. Hand back committed code, exact tests/skips/environment/SHAs, failure proof, changed contracts, migration/rollback, unresolved inputs and next task. Do not edit other owners’ files, production, the shared task manifest or unrelated worktrees.
```

## Continuation record for subsequent sessions

Each implementation handback records: active scope; integration SHA/branch; task/slice owner/worktree/base; files changed; reviewed SHA/findings; test command/environment/results/skips/artifact paths; implemented versus integrated versus release state; unresolved P-input IDs; migration and rollback notes; next task. Append under `research/plan/evidence/coordinator/` and update manifest statuses only after the relevant evidence exists. Never replace prior evidence or mark a parent complete because its plan was authored.
