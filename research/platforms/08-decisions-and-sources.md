# Decisions, assumptions and research context

Updated 2026-09-22. Recommendations below are explicit so later sessions do not mistake them for user-confirmed commercial terms.

## Confirmed by the user

- Separate consumer App (`apps/app`) and provider Lab (`apps/lab`).
- Consumer public signup, API keys, central credits, only the free plan initially.
- **One-time 10,000 promotional credits per individual user.**
- Provider models/endpoints, input/output configuration, inference analysis, datasets, benchmarking and improvement workflows; OpenAI-style standards where practical.
- Inference optimization, scaling, throughput, cost and heterogeneous compute are the company's internal business focus. Model optimization may remain an internal service whose outputs are benchmarked as new variants.
- Latest priority: Marlin2B SOP verification over large robotics datasets; **complete the robust and optimized inference backend first**, then launch the inference App. The user describes a VLA application context; validate actual endpoint modalities/output before advertising action or streaming capability. Backend readiness is E3B/E4B; App follows the backend and Lab follows the accepted App candidate. Video/robotics/LLM remain broader directions; speech is deferred.
- Wave-2 implementation from the other system is now pulled at `271add9`; the repository is audited. Unpushed work, remote worktrees and current hosted state remain unverified. See the wave-2 audit for present versus target behavior.

## Architecture defaults adopted by this amendment

| Decision | Rationale / revision trigger |
|---|---|
| Shared backend contracts, separate apps and gates | Reuse durable runtime; avoid duplicate wallets/registries and Lab blocking consumer launch |
| Verified signup before grant; entitlement unique by individual | Prevent callback/org churn from minting credits; verification details can change with auth policy |
| Personal CREDIT wallet linked to initial personal consumer org | Preserve org-scoped API ownership while enforcing individual grant; teams need a later explicit pooling design |
| CREDIT amounts exact decimal; USD costs separate | User-defined credits cannot silently inherit the baseline USD unit |
| Provider proposes rates; platform approves public publication initially | Central wallet needs controlled, immutable metering; delegation can expand later |
| Provider aggregate metrics by default, customer content only by scoped grant | Serving ownership does not establish customer data ownership |
| Assisted supported-model onboarding | Avoid arbitrary runtime execution and generalized training complexity in the first release |
| Operator-assisted catalog before Lab | Consumer launch can proceed independently |
| Import existing pipelines/checkpoints before managed training | Provider teams already have workflows; establish reproducible evidence first |
| Migrations stay in current physical path with one owner | Reduce conflicts with active implementation; shared logical ownership is sufficient |

## Open decisions and when they matter

| Question | Proposed handling | Blocks |
|---|---|---|
| Production model credit rates | Publish measured/cost-informed operator-approved values; no arbitrary credit/USD conversion | Consumer production release, not scaffolding/tests |
| Existing nonzero spendable USD balances | Retain history; explicit conversion or account transition policy | Cutover of affected existing accounts |
| Promotional credit expiry | No expiry until a disclosed policy is chosen; no monthly refill | No initial implementation block |
| Provider/customer commercial relationship and payouts | No payout/revenue-share assumption in the free pilot | Commercial release |
| Lab hostname and deployment project | Separate app deployment; configure origin/callback allowlists when provisioned | Lab deployment only |
| Marlin live-input task and acceptable delay | Specify periodic description, recent-window QA or SOP event detection with metrics | Live-video runtime, not finite-clip hosting |
| Robotics design partner/task/location | Measure end-to-end deadlines and robot integration before picking transport/placement | Robotics service release |
| Data sharing terms and training reuse | Explicit purpose-specific authorization and revocation handling | Customer data use in Lab beyond aggregates |
| Remote implementation state | Coordinator inventories actual commits/worktrees before applying task mapping | Integration assignments, not documentation |

The initial failed/cancelled execution charging policy is proposed in [credits](02-credits.md) and must be disclosed before launch. No new user clarification is required to continue documentation or local contract work.

See [the complete pending-input register](../plan/15-pending-inputs.md) for owners, blockers and work that can continue, including SOP rubric/dataset access and external training/teacher choices.

## Evidence and sources

The prior [platform research](../platform/README.md) and [inference-platform research](../inference-platform/README.md) remain background. Their all-in-one product scope, paid-first assumptions, competitor absence claims, historical prices and model lists do not override this amendment and are not re-certified here.

Current source checks for the contract boundaries:

- The [OpenAI Chat API reference](https://developers.openai.com/api/reference/resources/chat) supplies request/chunk/usage structure for compatibility fixtures. Our supported subset and credit extensions are our design decisions, not a claim of complete API equivalence.
- [OpenPI remote inference](https://github.com/Physical-Intelligence/openpi/blob/main/docs/remote_inference.md) is the reference for a policy-specific robotics adapter. Do not infer production WAN timing suitability from an example remote client/server.
- The [OpenTelemetry GenAI conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) are a vocabulary reference for future telemetry adapters. Pin an actual semantic-convention version when implementing; business identity, credits and permissions stay explicit domain fields.

The user's reports about the Marlin team's annotation/training pipeline and provider relationship are recorded as customer context, not independently audited results. Model names mentioned in brainstorming must resolve to official artifact IDs before registration. No new GPU benchmark, market census or vendor price comparison was performed for this architecture amendment.
