# 24 — Fresh-session handoff: finish consumer v1

## Session Metadata

Prepared 2026-09-24 in `model-inference-v1-review`, branch `codex/consumer-v1-implementation-plan`, from main `6db71ee3`. This is a planning handoff; use the latest main containing this file as the implementation base. Fetch again before dispatch because another system may publish newer runtime evidence. Historical coordinator tracker v46 is preserved; this package does not fabricate runtime completion or a new operational tracker version. The implementation session must extend the existing HTML tracker using consumer-v1/06-progress-tracker.md, as now explicitly requested by the user.

## Current State Summary

The previous wave implemented the durable Marlin backend and a metered pilot. Main includes post-deployment repairs and E1B acceptance evidence, but no final accepted backend certificate was committed at the planning baseline. Audit 21 found additional consumer-launch seams: restart-safe uploads, durable cleanup, empty-media execution readiness, persisted result expiry, public capability/retention truth, benchmark validity and continuous operations. The App still needs real account/query/action wiring and public verified onboarding. This package assigns those repairs, then completes consumer v1; Lab remains later.

The task manifest has 14 new planned corrective packages and refined existing App briefs, with E3C/E4C as current backend gate roots. Previous implemented/integrated statuses remain intact. Do not rebuild earlier waves or equate runner implementation with release acceptance. S3 determines which findings newer evidence has already closed.

## Important Context

- Product 1 is `apps/app`: free consumer inference, public verified signup, **one-time 10,000 CREDIT per individual**, no refill, real keys, usage/balance and owned results. Product 2 is `apps/lab`: provider model/data/evaluation/improvement tools, after App.
- Backend-first remains mandatory. Finish and accept E4C before App feature dispatch; finish App E4 before Lab. Missing live inputs permit independent backend work, not automatic frontend/fleet expansion.
- Current profile is finite Marlin video up to the deployed 82-second cap with additional byte/frame/geometry constraints. Text/SSE output and explicit async are supported concepts; native video streaming, robot actions and verified SOP accuracy are not inferred from robotics use cases.
- Last reported pilot uses a single L40S, vLLM pinned in handoff 20, application `bda15866e5700f3856d7142580da842fba9bbd23`, concurrency 8, legacy USD admission and separate test CREDIT grant. Reconcile actual state; public CREDIT activation and approved rates remain work. Existing provisional credit values are not approved launch pricing.
- Main included three E1B cells with 120 attempts / 114 accepted / 6 deliberate over-cap refusals, zero failures and zero replay, at 0.5/s, 1/s and burst 8. These support the repaired body-drain path; they do not establish burst 32/full soak/video-only latency or an accepted certificate. Final run3 evidence was not committed at the baseline; read newest main first.
- PostgreSQL owns acceptance, readiness, identity, holds, fences, journal, result expiry and accounting. Valkey is rebuildable. Zero trace capture is not zero retention of serving payload/media/results. Every content-bearing store needs the approved lifecycle.
- Migrations 0001–0018 are already applied and immutable; allocate the next free number from current main. One D owner writes SQL. Use additive compatibility, not migration edits or resets.
- Reuse existing operational authorization/resource allocation recorded in the repo. Do not request permission again for authorized work; do not infer new GPU/Modal purchases or unrestricted fault testing. Each resource-consuming run still needs an explicit target/window/budget/stop profile.

## Immediate Next Steps

1. Inspect branch/worktree state, fetch/pull main without overwriting local work, read root conventions and the files below. Create a coordinator integration branch from the committed current main. Keep unrelated experiment work separate.
2. Run the plan validator and its negative tests. Assign tracker support to extend the existing progress renderer/HTML with task ownership, gate evidence and dependency-aware ETA; preserve its history. Start S3: reconcile actual deployed identity, newest operational tail/release decision, all RV findings and inputs. E2C can inventory the supported Linux environment concurrently.
3. Complete F2C contracts/fixtures and E2C reproducible checks. Freeze lifecycle/readiness/expiry/alias decisions and path ownership before parallel implementations consume them.
4. Dispatch D10, M5, W5, G7, G8, E1C and I8 in isolated worktrees from a committed integration SHA. M6 follows M5. Only the coordinator merges common composition/config/CI/layout/lockfiles. Integrate in manifest dependency order.
5. E3C runs real-service backend integration, process crashes, tenant denial, retention/readiness/expiry and exact reconciliation. E4C then extends existing E4B/E1B tooling for final approved-rate CREDIT, actual Marlin load/burst/soak/fault/restore/rollback proof. Follow the failure→regression→fix→retest loop in the load brief.
6. After accepted BACKEND-READY, execute the refined App C/A/U tasks and U4, then E3A/I2A/I3/E4. Prove a fresh verified individual can spend once-granted credits, retrieve output and exact usage, and revoke a key. Stop at explicit missing external inputs with a precise handback, not an invented pass.

## Architecture Overview

One shared durable inference runtime serves headless clients and the later App. Shared protected operations own grants, keys, rates and accounting; the App consumes them rather than creating parallel financial logic. Media preparation and engine work are bounded and versioned; gateway/worker replacement must preserve admitted work. The current single-node recovery design is not high availability. Future hosting/scaling interfaces are described in roadmap 23, not scheduled as unbounded v1 work.

## Critical Files

| File | Use |
|---|---|
| [Program 22](22-consumer-v1-implementation.md) | Scope, bands, file ownership, repair coverage and invariant gates |
| [Manifest](tasks.json) / [ledger](17-task-ledger.md) | Only authoritative task/dependency/status graph |
| [Contracts/data brief](consumer-v1/01-contracts-and-data.md) | F2C and D10 |
| [Runtime brief](consumer-v1/02-runtime.md) | M5, M6, W5, G7, G8 |
| [Operations/verification brief](consumer-v1/03-operations-and-verification.md) | S3, E2C, I8, E3C, E4C |
| [App brief](consumer-v1/04-app.md) | C0/C3A/A2/A3/U1R/U2/U3/U4/I2A/I3/E3A/E4 |
| [Load/client protocol](consumer-v1/05-client-and-load-testing.md) | E1C, resumable datasets, valid measurement and bounded automated fix loop |
| [Tracker/parallel coordination brief](consumer-v1/06-progress-tracker.md) | Extend existing HTML tracker; assignments, gate evidence, resource-aware ETA and safe concurrent dispatch |
| [Review 21](21-v1-consumer-readiness-review-2026-09-24.md) | Evidence and RV-01…RV-12; do not rely on summary alone |
| [As-built handoff 20](20-platform-handoff-2026-09-24.md) | §14 reported live state and original wave evidence |
| [Operational tail](../../HANDOFF-20260924T2115Z.md) | Historical latest at baseline; read the current RESUME-NOW target too |
| [Pending inputs](15-pending-inputs.md) | Rates, workload/quality/budgets, auth and deployment boundaries |
| [Verification](04-verification.md) / [coverage](07-requirement-coverage.md) | Test IDs, repair and product requirement mapping |
| [Hosting roadmap 23](23-inference-hosting-roadmap.md) | Later GPU scaling/custom hosting/Modal discussion and comparison protocol |

## Files Modified

This planning session adds program 22, roadmap 23, this handoff and five detailed module briefs, followed by explicit tracker/parallel-coordination instructions. It amends the task manifest, validator/negative tests, generated ledger, verification/coverage/input registers and current-entry links. No runtime behavior, live deployment, paid inference or resource provisioning is changed. Historical operational evidence and original dirty experiment checkout remain preserved.

## Decisions Made

| Decision | Reason |
|---|---|
| Add corrective task IDs while preserving old statuses | As-built software evidence and unfinished release proof are different facts |
| F2C before disjoint runtime lanes; one SQL owner | Readiness, uploads, GC and expiry cross modules and cannot be independently invented |
| E3C/E4C replace current backend gate roots | Old runner/code completion cannot bypass new launch-path findings |
| Refine existing App tasks and add consumer request detail U4 | Complete the user's output/charge journey without rebuilding the App or provider trace tools |
| Keep fleet/custom hosting as a separate proposal | Establish reliable measured primitives first and decide cost/latency/availability scope explicitly |

## Assumptions Made

Current main may advance before implementation. S3 must reclassify proven fixes and preserve new evidence. The supported Linux runner and existing authorized GPU environment will be used where available; this Mac planning environment has no functioning Docker daemon. Numeric launch SLOs, approved credit rates and benchmark spend bounds are external inputs; the implementation must not invent them. Planned slice sizes are review estimates, not launch dates.

## Potential Gotchas

The original checkout `model-inference` has unrelated dirty experiments; do not stash/reset/delete them. The clean review checkout was used to isolate this package. Root main pushes may trigger App deployment even when the GPU is separately operated. Transaction pooling must be verified for role/search-path/prepared-statement behavior. A health URL cannot prove rollback serves video or that the current backup is known-good. Local process maps cannot protect objects from a restarted collector. An intentional dataset resume replay is legitimate; an unexpected replay makes a capacity benchmark invalid. A missing Linux/GPU prerequisite is not a passing skip.

## Verification and next-session commands

Run from repository root:

```bash
python3 research/plan/scripts/validate_plan.py
python3 -m unittest discover -s research/plan/scripts -p test_validate_plan.py
```

The planning validation record is [here](evidence/v1-plan-20260924/verification.json). Runtime suites from audit 21 are prior evidence only; this planning session does not rerun a GPU certificate. Proposed `consumer-local`, `backend-certify` and `app-e2e` Make targets are implementation deliverables; inspect whether they exist before invoking them. Extend existing runners and record actual commands instead of copying hypothetical commands as evidence.

## Copyable implementation prompt

```text
You are the implementation coordinator for model-inference. Analyze the committed plans and then start implementing; do not stop at a plan or summary. Use the maximum safe parallel agents supported by your environment, each in an isolated worktree, and maintain an HTML progress/ETA tracker throughout the work.

1. Synchronize and establish the actual baseline

Inspect git status, worktrees and branches; fetch and pull the latest main without overwriting unrelated work. The planning baseline commit is ba8ffc40, followed by the tracker/handoff amendment; use current main containing this handoff and consumer-v1/06-progress-tracker.md, not an old pinned checkout. Create a coordinator integration branch from a committed main SHA.

Read root conventions and HANDOFF.md, then:
- research/plan/24-consumer-v1-session-handoff.md
- research/plan/22-consumer-v1-implementation.md
- research/plan/21-v1-consumer-readiness-review-2026-09-24.md
- research/plan/20-platform-handoff-2026-09-24.md, especially section 14
- the current RESUME-NOW target and latest operational tail/release evidence
- research/plan/tasks.json, 17-task-ledger.md, 15-pending-inputs.md, 03-execution-protocol.md and 04-verification.md
- all six briefs in research/plan/consumer-v1/.

Run the plan validator and its tests. Start S3: compare the plan, actual code, migrations, deployed configuration and newest test/certification reports. Classify RV-01 through RV-12 as open, fixed with evidence or superseded with reason. Preserve earlier implemented/integrated task history. Do not repeat proven fixes or infer release acceptance from an implemented runner. Update the plan only where evidence justifies a correction, retaining the user's scope and acceptance criteria.

2. Build the tracker immediately alongside reconciliation

Extend the existing research/plan/scripts/progress.py, evidence/coordinator/progress-state.json, PROGRESS.md and progress.html. Follow consumer-v1/06-progress-tracker.md. Preserve the previous tracker history; its old E4B closure and old cadence forecast are not current authority.

Use tasks.json as the sole task/dependency graph and a coordinator-owned overlay for activity/assignments/estimates. Render a self-contained HTML file that opens locally, with searchable tasks, status filters, backend/App/deferred scope summaries, agent/worktree ownership, review/integration queues, dependency/critical-path view, blockers, acceptance checklists, evidence links, test/soak progress and ETA ranges. Keep implementation, integration, deployment and accepted release states separate. Show last update and stale-data warnings. A green implementation task cannot make a pending release gate green.

Base ETA on inspected remaining effort, confidence and dependency/resource constraints, including the SQL writer, review/rework, integration queue and serialized GPU/soak windows. Do not divide task count by agent count or invent finish dates. Unknown input availability means unknown/conditional ETA. Reforecast as evidence changes. Update on task transitions/merges/failures and around every 5–10 minutes during active work. One coordinator writes shared state atomically; agents submit separate updates. Test coverage, gate semantics, ETA constraints and safe escaping, then open the HTML and verify filters/details/reload. Report the tracker path early; do not let dashboard polish delay ready implementation.

3. Maximize safe parallel implementation

Inventory agent slots, CPU/RAM, test-service capacity and GPU access. Keep available slots occupied with useful dependency-ready work or independent review. Every writer gets codex/<task>-<slug>, a worktree from a committed integration SHA, explicit file ownership, isolated test resources, acceptance criteria and handback format. Never have several agents edit the same shared file or run destructive tests against the same resource.

Begin with S3 reconciliation, E2C environment inventory and tracker support. Complete F2C's reviewed lifecycle/readiness/expiry/capability contracts before their consumers. Then dispatch D10, M5, W5, G7, G8, E1C and I8 in parallel to the extent available slots and committed prerequisites allow. M6 follows M5. Reuse agents for the next ready slice; give an independent reviewer finished work while other writers continue. Reserve coordinator capacity for integration and unblockers. If slots are limited, prioritize the critical path and queue remaining lanes rather than bypassing dependencies.

One D owner writes additive migrations. Already-applied migrations are immutable; allocate the next free number from latest main. Coordinator owns common contracts/composition/config/CI/lockfiles/navigation and merges sequentially. E2C/E3C/E4C runner edits and shared GPU deployments/fault tests require serialized ownership. Agents hand back commits, changed paths, focused test results, failed-then-passed regressions, evidence, wiring requests, remaining issues and updated effort estimates. Review each handback and verify the combined tree before claiming integration.

4. Complete and certify the backend first

Implement the corrective closure through E3C and E4C using the detailed briefs. Cover durable uploads across restart, safe cleanup, readiness including text-only requests, persisted result expiry, truthful discovery/limits/retention, canonical pricing, exact CREDIT operations, continuous monitoring, pool/role safety, artifact restoration and real serving rollback.

Follow consumer-v1/05-client-and-load-testing.md: freeze candidate/workload/config/card identities and validate target, resource, duration, volume, spend and stop bounds. Exercise actual service adapters, two tenants, sync/SSE/async, interruption/resume, accounting reconciliation, open-loop sustained load, bursts, overload, fairness, soak and recovery. Use the actual pinned Marlin target for model/performance proof. Record all attempts, fresh generation versus replay, rejected/failed requests, latency distributions, resource behavior, output parity and attributable cost.

For each failure: reproduce the smallest failing case, add a meaningful regression, fix the owning module, rerun focused and impacted integration checks, redeploy the pinned candidate when required, repeat the failed cell, then perform final combined verification after the last runtime change. Preserve failed evidence. Missing services, skipped required cases, incomplete soak, unexpected benchmark replay and driver-limited traffic are not passing release evidence. Reuse valid previous measurements only with an explicit unaffected-path/profile justification.

Reuse existing authorized resources and operations; do not repeatedly ask for permission already granted. Do not purchase new GPU/Modal capacity, submit unapproved paid calls or run unbounded fault/load tests. Missing external inputs block their dependent gate only: continue independent authorized backend work and record the precise missing input and next command. Never invent rates, budgets, SLOs or approval.

5. Finish the consumer App after accepted BACKEND-READY

Only after E4C is accepted, execute C0/C3A/A2/A3/U1R/U2/U3/U4, then E3A/I2A/I3/E4. Prove the hosted journey: fresh verified individual -> one-time 10,000 CREDIT -> API key -> actual finite-video inference -> owned result/request details -> exact usage/balance -> key revocation. Include repeated callbacks, low funds, failures/expiry and cross-tenant denial. Production data must come from real services, not fixture fallbacks.

apps/app is consumer distribution. apps/lab is the later provider product. There is no monthly promotional refill. Preserve historical USD separately. The current finite-video cap is 82 seconds with additional declared byte/frame/geometry constraints. Do not claim native live-video input, robot action support, ZDR, unapproved prices or proven SOP accuracy.

research/plan/23-inference-hosting-roadmap.md preserves later GPU scaling/scale-down, load management, custom vLLM/SGLang hosting and Modal comparisons. Keep relevant interfaces ready; do not expand this launch wave into that separate hosting program or Lab implementation.

6. Maintain reviewable delivery and handoffs

Commit coherent changes, integrate in dependency order and push reviewed, validated milestones to main under the repository's release workflow. Preserve newer remote work; never force-push main or merge unrelated experiment branches. Main may trigger App deployment, so apply the documented deployment/gate controls. Keep manifest, generated ledger, tracker, input register and evidence consistent at each checkpoint.

Continue through the authorized implementation and verification; do not end after dispatching agents. At a real external blocker, leave exact state, passing/failing/not-run checks, candidate identity, active worktrees/processes, resource cleanup, blocker owner and a copyable resume step. At completion, provide the tracker, accepted gate evidence, actual measured operating envelope, remaining deferred scope and release decision. Report only what was verified.
```
