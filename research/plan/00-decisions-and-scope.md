# Accepted scope and decisions

## Product outcome

A developer can receive an operator-issued promotional balance, create a key, submit text/video inference, understand admission and errors, retrieve explicitly asynchronous jobs, inspect their own usage and opted-in traces, and provide feedback. Operators can measure reliability and evaluate consenting requests. First release runs on one GPU; its failure semantics are honest and recovery is tested.

| ID | Decision | Consequence |
|---|---|---|
| DEC-01 | Free pilot first | No payment processor or cash-revenue claim. Promotional balance is reserved and settled. New organizations start at zero; operators grant explicitly. |
| DEC-02 | Public URLs and uploads; bounded base64 compatibility | Secure materialization and preprocessing are required, not postponed by restricting the API to uploads. |
| DEC-03 | Async is explicit | Ordinary chat never changes to HTTP 202 under load. Async jobs are a separate route or explicit preference before response headers. |
| DEC-04 | Inference continues on trace-capture loss | Trace loss is counted; only fsynced spool entries have the local durability guarantee. Accounting/job persistence remains mandatory. |
| DEC-05 | Results 24h, processing cache 7d, optional full content 90d, metadata 13mo | Access expires on time even if physical object/part deletion lags. Owners control trace mode and content retention within caps. |
| DEC-06 | Existing organization/owner/member model | No projects, invitations, team management or org-switcher. Platform operator authorization is separate from org ownership. |
| DEC-07 | Single-GPU pilot before fleet | Local scheduling is permitted, volatile job truth is not. Fleet rollout has independent capacity and failover gates. |
| DEC-08 | Existing balances preserved | Migration never re-debits historical usage; no automatic free grants on signup. |
| DEC-09 | PostgreSQL is durable authority | Valkey is a rebuildable scheduling index. Job acceptance, fencing, journal, settlement and feedback acceptance commit in PostgreSQL. |
| DEC-10 | Consent and tenant isolation are independent controls | Trace opt-in is not evaluation or response-cache consent. Recheck evaluation consent before submission; preserve a consent audit snapshot. |

DEC-01–08 reflect user choices. DEC-09–10 and the protocols in this package resolve review findings and are implementation design decisions. Configuration defaults below are provisional engineering limits until measured; do not present them as achieved performance.

## In scope

- Auth, entitlement, revocation/suspension, request-size limits, pricing snapshots and admission capacity.
- Durable preparation/jobs, explicit async, queue fairness, leases, cancellation, result retrieval and resumable output journal.
- Promotional ledger, reservations, idempotent terminal settlement and reconciliation.
- Bounded trace capture, ClickHouse projections, S3 content, metadata/content retention and tenant-safe console access.
- Feedback with provenance; consent-gated, budgeted judge runs, score validation and calibration.
- Reproducible tests, engine/corpus pins, operational dashboards, backup/recovery and separately gated fleet design.

## Deferred

Payments/invoices/refunds of real money; OpenRouter listing and its ZDR claims; second-owner isolation validation as a commercial launch gate; team/project product expansion; response caching; training/distillation pipeline; quantization/kernel experiments without parity evidence; capacity purchases or Savings Plans. Preserve extension points without implementing these features now.

## Current implementation baseline

At documentation baseline `1db98e9`, the gateway is a monolith and no durable queue, trace backend or judge is implemented. Bounded auth caches, constant-time legacy-key comparison, non-stream inflight cleanup, one-time inline-media fetching and benchmark-note unit corrections already exist. Foundation must preserve those fixes rather than repeat them as outstanding tasks.

Previously observed local checks: 23 Python tests, 4 console tests and console lint passed during planning. Python dependencies were temporarily installed in an external environment; there is no reproducible Python lock yet. The console test command discovers only top-level library tests. These observations are not release evidence for future code.

## Verification log

- 2026-09-20: Reconciled user scope with repository baseline; supersedes the original paid-launch and automatic-202 assumptions.
