# 24 — Fresh-session handoff: finish consumer v1

## Session Metadata

Prepared 2026-09-24 in `model-inference-v1-review`, branch `codex/consumer-v1-implementation-plan`, from main `6db71ee3`. This is a planning handoff; use the latest main containing this file as the implementation base. Fetch again before dispatch because another system may publish newer runtime evidence. Historical coordinator tracker v46 is preserved; this package does not fabricate runtime completion or a new operational tracker version.

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
2. Run the plan validator and its negative tests. Start S3: reconcile actual deployed identity, newest operational tail/release decision, all RV findings and inputs. E2C can inventory the supported Linux environment concurrently.
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
| [Review 21](21-v1-consumer-readiness-review-2026-09-24.md) | Evidence and RV-01…RV-12; do not rely on summary alone |
| [As-built handoff 20](20-platform-handoff-2026-09-24.md) | §14 reported live state and original wave evidence |
| [Operational tail](../../HANDOFF-20260924T2115Z.md) | Historical latest at baseline; read the current RESUME-NOW target too |
| [Pending inputs](15-pending-inputs.md) | Rates, workload/quality/budgets, auth and deployment boundaries |
| [Verification](04-verification.md) / [coverage](07-requirement-coverage.md) | Test IDs, repair and product requirement mapping |
| [Hosting roadmap 23](23-inference-hosting-roadmap.md) | Later GPU scaling/custom hosting/Modal discussion and comparison protocol |

## Files Modified

This planning session adds program 22, roadmap 23, this handoff and five detailed module briefs. It amends the task manifest, validator/negative tests, generated ledger, verification/coverage/input registers and current-entry links. No runtime behavior, live deployment, paid inference or resource provisioning is changed. Historical operational evidence and original dirty experiment checkout remain preserved.

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
Work in the model-inference repository. Complete consumer v1 using the latest main containing research/plan/24-consumer-v1-session-handoff.md. Start by inspecting local worktrees/status and fetching/pulling main safely; preserve unrelated changes and newer implementation evidence. Read root conventions, handoff 24, program 22, audit 21, handoff 20 §14, the current RESUME-NOW operational tail, tasks.json, 17-task-ledger.md and 15-pending-inputs.md. The five consumer-v1 briefs contain detailed module acceptance and test protocols.

Do S3 evidence reconciliation first; do not repeat repairs already proven closed on newer main. Complete F2C lifecycle/expiry/capability contracts and E2C reproducible Linux verification, then implement the corrective backend closure through E3C/E4C. Use isolated worktrees for disjoint lanes D10, M5, W5, G7, G8, E1C and I8; M6 follows M5. One owner writes additive migrations; coordinator owns common wiring/CI/config/lockfiles and integration. Preserve existing implemented statuses and immutable migrations. Do not treat runner implementation as accepted release evidence.

Follow the executable verification protocol in consumer-v1/05-client-and-load-testing.md: validate target/workload/candidate/budget/stop conditions, run the real-service journey, reproduce failures, add regression tests, fix the owning module, rerun affected cells, and finish with combined verification on the final SHA. Unexpected benchmark replay, missing services, incomplete soak or skipped required cases cannot count as PASS. Reuse valid prior evidence with explicit scope; preserve failures and raw sanitized measurements. Use existing authorized resources, but do not purchase new GPU/Modal capacity or run unbounded tests. Missing external input blocks only its dependent gate; continue independent authorized work and record the exact next step.

Backend-first is mandatory: after accepted E4C BACKEND-READY, complete C0/C3A/A2/A3/U1R/U2/U3/U4 and E3A/I2A/I3/E4. Prove verified signup, one-time 10,000 CREDIT per individual with no refill, key issue, actual finite-video inference and result, exact usage/balance, tenant isolation and revocation. apps/app is consumer distribution; apps/lab follows App. Current finite-video cap is 82 seconds with other declared resource limits. Do not claim native video streaming, robot action support, ZDR, unapproved prices or SOP accuracy.

Produce reviewed commits and updated task/evidence handoffs with exact commands, outcomes, candidate identity, remaining inputs and an honest gate verdict. Use research/plan/23-inference-hosting-roadmap.md only to preserve interfaces for our later GPU scale-up/down, load management, custom vLLM/SGLang hosting and Modal comparison discussion; do not expand this implementation into an unapproved general compute platform.
```
