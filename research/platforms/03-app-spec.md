# Platform 1 — Inference App requirements

Location: `apps/app`. The product lets an individual developer obtain a usable model API, manage keys and understand the credits consumed. It is the consumer surface for the shared inference business.

## Primary journey and navigation

Signup → verify account → receive 10,000 credits once → select a live model → create/reveal an API key → copy a valid request → inspect outcome and remaining credits.

Initial navigation: **Models, API Keys, Usage, Credits, Docs, Settings**. The existing Billing route can redirect to Credits or retain its URL with a Credits label during migration. Remove disabled payment/invoice, Teams and Dedicated items from primary navigation until they provide a real workflow. Operator `/admin` remains a protected operations surface temporarily; it is not provider Lab.

Use an onboarding checklist only until the developer makes their first successful request. Show model availability, supported input types, explicit limits and credit rate before copying a request. Never imply that a coming-soon model has a working endpoint.

## Launch requirements

| ID | Requirement | Acceptance oracle / implementation coverage |
|---|---|---|
| APP-01 | Public email/password signup, verification, sign-in, recovery and sign-out | Unverified users cannot obtain a funded working key; callback/recovery errors are actionable without enumeration. A2 |
| APP-02 | One-time 10,000 individual credits | CREDIT-GRANT/IDENTITY; one personal wallet across models; no recurring refill. A1/D1/D5 |
| APP-03 | Live catalog with published rates, modality/limit/capability metadata and version identity | Draft/private/retired deployments are excluded; request snippets match resolved capabilities. A3/G1 |
| APP-04 | Consumer API keys: create, reveal once, list, revoke | Hash storage, server-side role check and admission recheck reject revoked credentials. U2/C3A/G1 |
| APP-05 | Robust text/video inference, secure URLs/uploads and supported SSE | Existing durable/API/media suites pass; unsupported options are rejected explicitly. G2/G4U/M/W |
| APP-06 | Explicit async jobs, owned retrieval/cancellation and bounded replay | No surprise 202 for ordinary chat; another consumer cannot read a handle. G3/D/Q |
| APP-07 | Credits page: total, reserved, available, grants, debits, adjustments | All amounts labeled credits; exact reconciliation with wallet and ledger; legacy USD separate. U1/C1 |
| APP-08 | Own usage/request history: model, timestamp, status, latency, tokens, credit charge, request ID | Cursor pagination, per-key/model/time filters, no dependency on optional content traces. U1/C1/D5 |
| APP-09 | Docs with working examples, capability differences, pricing, exhaustion, retry/idempotency, retention | Fresh user completes APP-JOURNEY from instructions; no embedded credential. A3/E3A |
| APP-10 | Privacy/settings: trace mode, retention and distinct data-use permissions when available | Trace off works; provider sharing/evaluation/training are never implied or prechecked by signup. U2/C3A |
| APP-11 | Operator controls for grant adjustments, suspension, published rates and incident diagnosis | Protected actions audited and idempotent; consumer/provider roles cannot invoke them. U3/C3A/A3 |
| APP-12 | Free-plan admission/abuse bounds and exhaustion UX | Stable 402/429 behavior, hard holds, no payment bait or automatic upgrade path. G1/D2/A2 |

The first published model is Marlin. Additional models can share catalog and wallet machinery but require their own serving/capability/rate validation. A model name appearing in historical research is not publication authorization or readiness evidence.

## Own history versus provider analysis

Consumer usage is an accounting and debugging feature and must work without ClickHouse, a judge or a dataset service. Include failed/rejected request diagnostics where safe, but distinguish them from accepted/billable usage. Results expire according to the result policy; persistent history is not a promise that full content remains available.

Optional own trace detail and user feedback can be added after the core journey. These use consumer-scoped services. Cross-customer analysis, evaluator configuration, calibration, dataset construction, training and endpoint management belong in Lab. A consumer can later opt to join Lab for their own model/workload experiments; that is a separate capability.

## App architecture

- App server actions call narrow shared repositories with trusted session identity. They do not import another app's routes/actions or query analytics with caller-supplied SQL.
- Signup and credit issuance are server/database transactions, not front-end effects. Keep service-role credentials server-only.
- Wallet, catalog and usage reads use shared PostgreSQL records. The runtime resolves consumer keys and admission independently of App availability.
- Share generic UI and contract packages where useful. Keep consumer navigation, onboarding and business actions in App.
- Model publication is operator-assisted until Lab publication is ready; consumer launch does not wait for a provider self-service dashboard.

## Quality and product measures

Release gates require zero duplicate signup grants in the concurrent test suite, exact credit reconciliation, cross-tenant rejection, tested revocation and recovery, and a successful fresh-account inference journey. Measure signup-to-first-success conversion/time, active inferencing users, errors by stage/model, queue/preparation/generation latency, credit exhaustion and support interventions.

Do not invent launch SLOs from single-clip benchmarks. Publish the measured single-GPU envelope and known interruption behavior. Accessibility, loading/empty/error states and secret handling are part of each UI acceptance check.

## Explicitly later

Payments, recurring plans, invoices, credit purchases, team pooling, provider payouts, dedicated capacity purchasing, marketplace revenue shares and third-party distribution. Also later: consumer-controlled A/B routing and rich trace exports. These are not hidden prerequisites for the initial free plan.
