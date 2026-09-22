# Accepted scope and decisions

**Current priority (2026-09-22):** [Marlin backend first](18-marlin-backend-first.md). E3B/I2B/I3B/E1B/M4/W4/E4B separate endpoint readiness and measured optimization from the later App browser/deployment gates; G6B provides protected headless operations. Use the updated [fresh-session handoff](16-fresh-session-handoff.md) and manifest for dispatch.

**Current scope/sequence:** [complete implementation plan](12-complete-build-plan.md). Marlin2B SOP verification over large robotics datasets is the lead workload; complete the endpoint backend first, then launch the consumer App. This file’s initial scope is the App/early-Lab release. Later Lab M2–M4 now have executable packages; conditional/deferred work remains explicit.

## Product outcome

A developer can sign up, verify their identity, receive 10,000 promotional credits once per individual, create a key, submit text/video inference, retrieve explicitly asynchronous jobs and inspect their own usage in `apps/app`. Provider operations and improvement workflows belong in the separate `apps/lab`. Both reuse the durable inference backend. The [two-platform architecture](../platforms/README.md) and [implementation amendment](08-platform-split.md) govern the revised product boundary and release gates. First consumer release runs on one GPU; its failure semantics are honest and recovery is tested.

| ID | Decision | Consequence |
|---|---|---|
| DEC-01 | Free pilot first; amended 2026-09-21 | Only free plan; 10,000 CREDIT once per individual user after verified onboarding. No monthly refill. Reserve/settle inference in credits; operator adjustments remain audited. |
| DEC-02 | Public URLs and uploads; bounded base64 compatibility | Secure materialization and preprocessing are required, not postponed by restricting the API to uploads. |
| DEC-03 | Async is explicit | Ordinary chat never changes to HTTP 202 under load. Async jobs are a separate route or explicit preference before response headers. |
| DEC-04 | Inference continues on trace-capture loss | Trace loss is counted; only fsynced spool entries have the local durability guarantee. Accounting/job persistence remains mandatory. |
| DEC-05 | Results 24h, processing cache 7d, optional full content 90d, metadata 13mo | Access expires on time even if physical object/part deletion lags. Owners control trace mode and content retention within caps. |
| DEC-06 | Consumer organization model retained; provider permissions added | Personal consumer org links to user-owned wallet. No consumer teams/org-switcher yet. Lab provider capability/membership and platform operator permissions are distinct. |
| DEC-07 | Single-GPU pilot before fleet | Local scheduling is permitted, volatile job truth is not. Fleet rollout has independent capacity and failover gates. |
| DEC-08 | Historical units and balances preserved; amended 2026-09-21 | Preserve existing USD unchanged and never retrocharge historical usage. New CREDIT grant is independent and unique per user; no implicit USD conversion. |
| DEC-09 | PostgreSQL is durable authority | Valkey is a rebuildable scheduling index. Job acceptance, fencing, journal, settlement and feedback acceptance commit in PostgreSQL. |
| DEC-10 | Consent and tenant isolation are independent controls | Trace opt-in is not evaluation or response-cache consent. Recheck evaluation consent before submission; preserve a consent audit snapshot. |

The user's latest product clarification overrides earlier zero-grant and single-console choices. Verification timing, wallet binding and provider publication approval are engineering defaults documented in [decisions](../platforms/08-decisions-and-sources.md). DEC-09–10 and the protocols resolve review findings. Configuration defaults are provisional until measured; do not present them as achieved performance.

## In scope

- Auth, entitlement, revocation/suspension, request-size limits, pricing snapshots and admission capacity.
- Durable preparation/jobs, explicit async, queue fairness, leases, cancellation, result retrieval and resumable output journal.
- Promotional ledger, reservations, idempotent terminal settlement and reconciliation.
- Shared optional trace capture/projections/retention; provider analysis UI in Lab. App launch may disable capture but cannot bypass tests for enabled features.
- Lab feedback/review and separately permissioned/budgeted judge runs, with independent delivery gates. These are not consumer launch prerequisites.
- Reproducible tests, engine/corpus pins, operational dashboards, backup/recovery and separately gated fleet design.

## Deferred

Payments/invoices/refunds of real money; OpenRouter listing and its ZDR claims; commercial second-owner onboarding gate; consumer teams/pooling; response caching; fully managed training/RL infrastructure (external training/distillation integration is planned Lab scope); quantization/kernel experiments without parity evidence; capacity purchases or Savings Plans. Provider isolation tests with two synthetic providers are mandatory for Lab regardless of the deferred commercial second-owner gate. Training and specialized modalities now have an explicit [Lab roadmap](../platforms/06-lab-roadmap.md), not an App launch dependency. Detailed tasks now exist in [Lab improvement handoffs](13-lab-improvement-handoffs.md).

## Current implementation baseline

At documentation baseline `1db98e9`, the gateway is a monolith and no durable queue, trace backend or judge is implemented. Bounded auth caches, constant-time legacy-key comparison, non-stream inflight cleanup, one-time inline-media fetching and benchmark-note unit corrections already exist. Foundation must preserve those fixes rather than repeat them as outstanding tasks.

Previously observed local checks: 23 Python tests, 4 console tests and console lint passed during planning. Python dependencies were temporarily installed in an external environment; there is no reproducible Python lock yet. The console test command discovers only top-level library tests. These observations are not release evidence for future code.

## Verification log

- 2026-09-20: Reconciled user scope with repository baseline; supersedes the original paid-launch and automatic-202 assumptions.

- 2026-09-21: Amended for separate consumer App/provider Lab, individual signup credits and independent release gates; see the platform-split review. Implementation evidence on the other system remains unverified here.
