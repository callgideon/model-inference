# Model-to-API platform: product and market research

Research date: **2026-09-19**. Scope confirmed with the user: **our AWS first**.

The product is a platform that turns a startup's model into an operated, metered, commercial API. Marlin-2B is the first model and internal proving ground. The initial deliverable is a paid Marlin endpoint; the platform milestone is a second model owner launching without bespoke billing or serving work.

Read in order:

1. [Competitors and market position](01-market-and-competitors.md)
2. [Prioritized requirements and release gates](02-priorities.md)
3. [System boundaries, economics, and OpenRouter](03-operating-model.md)

This complements the existing [serving research](../scaling/README.md). The existing [platform research](../platform/README.md) addresses a different, larger product: closed-loop distillation. Training, annotation, and automatic model improvement are later expansion options, not prerequisites for this launch.

## Recommendation

Start with **“launch and run a paid model API”**, aimed at small model teams. Deliver one coherent journey: import a supported model, validate it, deploy it, issue customer keys, set limits and prices, collect payment, inspect requests, and understand margin. Add distribution once the endpoint is dependable.

The market already contains this category. Baseten for Model Labs directly addresses commercialization and distribution. Modal now provides managed inference endpoints as well as compute. An endpoint, API keys, and a dashboard are baseline capabilities. Our differentiation must be demonstrated through onboarding simplicity, support for underserved specialist workloads, transparent economics, and customer ownership—not asserted from a feature checklist.

## Decisions and unresolved questions

| Item | Position |
|---|---|
| Hosting | Confirmed: our AWS first |
| First workload | Marlin video captioning and temporal grounding |
| Initial audience | Recommended: invited developers consuming Marlin; then small model teams with supported architectures |
| Commercial launch | Recommended: our company sells Marlin usage directly |
| First external model owners | Recommended: private endpoints with minimum capacity commitment; assisted onboarding |
| Reselling external owners' models | Requires explicit choice of customer contract, pricing authority, and owner compensation before public sales |
| Arbitrary model support | Deferred; publish a supported runtime/architecture matrix |
| OpenRouter | Prepare early; apply when ready; acceptance and traffic are external dependencies |
| Pricing | No launch rates recommended until representative workload and utilization costs are measured |
| Delivery dates | Unestimated: staffing and production readiness have not been assessed |

Research is based on public primary sources and repository records, not competitor account access or customer interviews. “Unknown” means unverified, not absent. Vendor performance claims are not independently benchmarked. Priorities and product positioning are recommendations.

## Verification log

- 2026-09-19: Reviewed current Modal, Morph, Wafer, Baseten, Fireworks, Together, Runpod, OpenRouter, LiteLLM, Langfuse, Stripe, and Marlin primary sources. Inspected repository Marlin experiment notes. No infrastructure deployed or vendor applications submitted.
