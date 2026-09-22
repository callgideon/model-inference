# Platform 1 — roadmap and release gates

**Latest sequence (2026-09-22):** finish the [Marlin endpoint backend](../plan/18-marlin-backend-first.md), including recovery and measured optimization; then launch App, then build Lab. Product requirements below are retained. Headless provisioning and backend gates remove the App UI from endpoint readiness.

Milestones are sequenced by evidence, not promised dates. Wave 2 has been imported/audited; staffing, newer remote work and live state must be revalidated. Current priority is the Marlin inference backend; App launch is next. The [manifest](../plan/tasks.json) supplies module dependencies; the new A/S task briefs are in [amendment workstreams](../plan/09-amendment-workstreams.md).

| Milestone | Outcome and scope | Dependencies / owners | Exit gate |
|---|---|---|---|
| BACKEND first | Robust authenticated endpoint, durable execution, recovery/restore and measured video/GPU tuning | E3B/I2B/I3B/E1B/M4/W4/E4B with G6B headless operations | BACKEND-READY independent of App/Lab |
| APP-M0: reconcile and freeze | Reconcile remote commits; accept product split, CREDIT unit, individual entitlement, catalog/runtime contracts | S1, S2M, F2R/F2P; coordinator + D/C/G | SPLIT-CONTRACT: no ambiguous ownership/unit; runnable versioned fixtures; migration compatibility plan |
| APP-M1: local consumer loop | Signup/verification, grant, keys, catalog, admission, inference, usage and exhaustion | A1/A2/A3, D1R/D2–D5, G1/G2/G3/G4U, M/Q/W, C0/C3A, U1–U3, E1/E2R/E3A | APP-LOCAL: real local PG and fake engine end-to-end; credit/race/security/recovery tests pass |
| APP-M2: free Marlin pilot | Reproducible deployment, enabled verified signup, published credit rates, real GPU and failure evidence | I1/I2A/I3, E4 after APP-M1 | APP-PILOT: new user completes journey; measured representative load, restore/drain/rollback, reconciled balances; legacy-account cutover disposition |
| APP-M3: reliable expansion | Capacity/fleet when justified; additional validated models; optional callbacks and own feedback/history improvements | I4 after pilot; G5 and feedback slices independently; each model has publication gate | Each added feature passes relevant failure/consent tests; multi-model wallet holds reconcile; no untested availability claim |
| APP-M4: commercial product | Decide paid credits/plans, invoices, team budgets, dedicated capacity and provider commercial terms | Separate product decision plus measured unit economics | Payment lifecycle and consumer obligations tested end-to-end before paid launch |

## Parallel work after the contract revision

F/D remain the shared contract and persistence critical path. Media M, scheduling Q, engine W, gateway G, consumer service C, UI U/A2/A3, infrastructure I and verification E can implement against committed fixtures in isolated worktrees. Existing completed compatible work is reused. Two tasks on the same owned files are sequenced by their track owner even when their dependency graph permits concurrent coding.

App onboarding/catalog/usage UI is dispatched after the backend candidate is accepted; Lab follows the App. Shared identity/entitlement/key/registry/rate functions and protected headless operations belong to the current backend scope. App M1/M2 have no dependencies on trace UI V, judge J, datasets, training, or Lab deployment. Basic catalog publication can be operator-driven. Full capture may remain disabled at consumer launch; enabling it adds TRACE-BOUNDS/RECOVER/TENANT and retention gates.

## Pilot acceptance scenario

1. A fresh person verifies signup and sees 10,000 credits. Repeat/reorder callbacks and retry login; balance remains 10,000 before spending.
2. They see the actual Marlin credit rate and capability profile, create a key and complete supported finite-video SOP-oriented requests, output streaming and explicit async where supported. Run the bounded large-dataset recipe, interrupt/resume it and account for every accepted item without duplicate charging.
3. Usage shows admitted deployment/rate, authoritative quantities and exact charge; available credits include concurrent holds.
4. Exhaustion rejects cleanly without dispatch; revoke the key and confirm it cannot admit work. Another user cannot access any request, result or wallet.
5. Crash and recover worker/gateway/queue paths; there is one accepted job and one settlement per idempotent operation.
6. Disable Lab and optional analytics. Steps 1–5 continue to work. If tracing is enabled, prove bounds, privacy and loss behavior separately.

Consumer launch is a single-GPU pilot with disclosed limits. It does not certify fleet availability, robotics control latency or model-quality parity against a customer's incumbent.
