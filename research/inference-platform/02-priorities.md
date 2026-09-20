# Prioritized product requirements

Scope: our AWS; Marlin first; other model owners next. These are recommended release gates, not delivery-date estimates. P0 means required before paid external use; P1 means the repeatable platform and distribution phase; P2 means expansion after demand.

## P0 — a trustworthy paid Marlin API

| Capability | Minimum release scope | Acceptance evidence |
|---|---|---|
| Serving recipe | Pin weights, container digest, tokenizer, preprocessing and canonical prompts; healthy warm worker | Representative caption/find parity suite passes; rollback restores known version |
| API contract | `/v1/chat/completions`, SSE streaming, `/v1/models`, bearer auth, documented errors and supported parameters | Python/JS client checks; final usage matches non-stream accounting; disconnect cancels work |
| Video input | Documented URL/upload workflow, bounded bytes/duration/resolution/frame budget, download/decode timeout | Mixed real clips work; malformed/oversized inputs reject predictably; URL fetch cannot reach internal services |
| Capability honesty | Explicit context/input limits and modality metadata | Unsupported tools, audio, structured output or parameters reject clearly instead of silently succeeding |
| Accounts and projects | Organization, project, owner/member roles; separate operator access | A member of one organization cannot read another's keys, usage, assets or traces |
| API keys | Create, reveal once, hash storage, name, scope, revoke, rotate, last-used | Revocation reaches all gateways within a documented bound; inference keys cannot manage accounts |
| Entitlements | Model allowlist and project/customer association | Private models cannot be discovered or invoked by another tenant |
| Rate and capacity limits | Requests/minute, tokens/minute, concurrent requests, payload limits; per-account and per-key enforcement | Parallel gateway load cannot evade limits; clear 429 response and retry guidance |
| Queue/admission | Bounded queues, timeouts, request cancellation, per-tenant fairness | One customer cannot monopolize workers; overload does not become an unbounded queue |
| Pricing and metering | Versioned input/output rates; exact serving usage; effective dates and request-level charge | Old requests retain original rates; retries/events do not duplicate charges |
| Credits and payments | Prepaid service credits; payment-confirmed top-up; separate promotional grants; refund/adjustment audit | Duplicate/delayed webhooks, simultaneous requests and failed requests preserve balance correctness |
| Hard spend protection | Reserve bounded maximum charge before expensive work; settle actual usage and release remainder | Concurrent requests cannot spend the same available credit; abandoned holds reconcile |
| Customer usage | Requests, input/output tokens, spend and remaining/held credits by model/key/date | Customer totals reconcile to usage ledger; CSV export explains charges |
| Request observability | Request ID, timings, tokens, model/deployment version, status, estimated internal cost | Support can trace a failed request across gateway, preprocessing and worker |
| Input/output inspection | Project-controlled capture, redaction, retention/deletion, access audit; content off by default | Enabled projects can inspect allowed payloads; disabled projects retain metadata only |
| Operational visibility | Error rate, queue, TTFT, completion latency, CPU/GPU/memory and worker readiness; alerts | An unhealthy worker stops receiving traffic; documented restart/replacement process |
| Cost tracking | AWS capacity/storage/network and serving overhead allocated to model; idle cost visible | Revenue and fully allocated cost shown separately; estimates labeled until reconciled |
| Developer onboarding | Pricing/limits page, quickstart, copyable API example, caption/find examples, small playground | New invited developer completes a paid successful call without operator assistance |
| Operator controls | Suspend tenant/key/model, adjust credits with reason, inspect metering failures, replay events | Every privileged action records actor/time/reason; metering recovery avoids duplicate settlement |
| Production basics | Private workers, least-privilege secrets, backups/restore, deployment health gates, incident contact | Recovery drill works; no credentials in logs or trace payloads |

The initial paid pilot may use one warm GPU with an explicitly limited availability commitment. Broad production commitments require tested replacement capacity and redundancy. Automated elastic scaling can wait if pilot limits fit measured capacity; overload protection cannot.

Full observability includes input/output inspection, but does not mean recording every customer's content by default. For video, begin with governed artifact references and processing metadata rather than embedding large base64 media in traces.

## P1 — make the platform repeatable

| Capability | Scope and dependency |
|---|---|
| Assisted model-owner onboarding | Owner organization, supported HF/S3 artifact import, revision/digest, credentials validation, model rights record; prerequisite to external private models |
| Deployment lifecycle | Validation → building/loading → ready → degraded/failed → draining/stopped; logs, retries, warmup, health and rollback |
| Stable endpoint/version model | Endpoint points to immutable deployment; explicit promotion, deprecation and rollback |
| Supported model catalog | Compatibility matrix, model card, modalities, limits, pricing and quickstart; avoid “any model” claims |
| Capacity plans | Shared approved models versus dedicated private models; min/max replicas, scale budget, pause and estimated idle cost |
| Owner customer administration | Invite customer, issue/revoke scoped keys, override quotas and prices, inspect customer usage; required before owners sell through us |
| Commercial model for external owners | Choose hosting-only versus platform resale; customer contract, price authority, remittance statement, refunds/chargebacks responsibility |
| Automated billing convenience | Auto-top-up with consent/caps, low-balance notifications, invoices, payment failures and receipts |
| OpenRouter integration | Provider credential, capabilities/pricing metadata, verified video contract, load tests, partner usage reconciliation |
| Branded API/customer portal | Model-owner branding and domain when design partners require it; shared platform domain suffices initially |
| Better observability | Search, trace export, timing breakdown, deployment comparisons, error grouping and operator alerts |
| Async video jobs | Job IDs, polling, cancellation, signed webhooks, asset lifecycle and billing rules if long clips exceed synchronous limits |
| Automated autoscaling | Queue/concurrency and measured capacity signals, warm minimums, hard maximum spend, rollout/cold-start handling |
| Reliability maturity | SLO dashboard, synthetic probes, spare capacity, tested rolling deployment, documented maintenance behavior |

P1 is not one release. Sequence it as **second private model → owner customer workflow → public distribution**. OpenRouter preparation can run alongside the first two; acceptance is not a gate for direct sales.

## P2 — expand only when justified

| Area | Deferred functionality |
|---|---|
| Broader hosting | Customer AWS, multi-cloud/region, private networking, arbitrary custom-code workloads and hardened build isolation |
| Enterprise administration | SSO/SCIM, fine-grained roles, enterprise contracts and compliance attestations |
| Model performance | Advanced caching, quantization profiles, speculative decoding and automatic tuning with quality gates |
| Commerce | Self-service marketplace publishing, automated revenue sharing/payouts, complex discounts, commitments and channel settlement |
| Model families | Embeddings, speech, image generation and other modalities with their own contracts/meters |
| Developer ecosystem | Terraform, expanded SDKs, partner catalogs, migration utilities and advanced webhooks |
| Model improvement | Feedback datasets, evaluations, A/B tests, fine-tuning and distillation |

Move a deferred feature forward when a committed customer requires it. Tenant isolation and validation of any code we execute remain prerequisites even during assisted onboarding.

## Release gates and success measures

1. **Serving gate:** unique mixed-duration clips, cold and warm paths, sustained concurrency, cancelled streams, invalid videos and long-context boundaries tested. Compare model quality to the reference implementation.
2. **Paid pilot gate:** end-to-end payment → credit → authenticated call → usage → charge → trace; duplicate webhook/event tests, overspend race tests, cross-tenant checks and recovery drill pass.
3. **Platform gate:** a second owner deploys a supported model without new gateway or billing code; track human intervention and first-success time.
4. **Distribution gate:** partner-specific API/capability validation and reconciliation pass; accepted agreement and integration approval exist.

Track time to first successful/paid request, repeat weekly paying usage, paid owners, deployment success rate, operator hours per owner, successful requests inside latency target, ledger discrepancies and model-level contribution margin. Exact numerical targets should be set after the pilot baseline; do not invent an SLA from the current two-clip benchmark.

## Verification log

- 2026-09-19: Priorities derived from user scope, [competitor evidence](01-market-and-competitors.md), and [local Marlin results](../../models/marlin2b/results/notes.md). Release tests listed here are proposed acceptance work, not tests executed in this research session.
