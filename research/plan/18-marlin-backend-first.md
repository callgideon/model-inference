# Marlin2B backend first — endpoint completion and optimization

**Current execution authority, 2026-09-22.** The user wants the backend for robust, optimized Marlin2B endpoint inference completed before the App. This supersedes the App-first dispatch instructions from 2026-09-21, while preserving the product architecture, individual-credit policy, prior implementation and later App/Lab plans.

## Deliverable

An operated, authenticated Marlin endpoint that a provisioned customer can call with finite video inputs for the SOP-use-case workflow. It supports the documented sync/output-streaming and explicit async modes, bounded uploads and dataset-client resume, durable job/output/accounting semantics, operator visibility/recovery and a pinned, measured serving configuration. Both Next.js applications can be stopped throughout backend acceptance.

Retain real key/auth/tenant controls, immutable model/rate resolution, CREDIT holds/settlement and legacy USD compatibility. Implement shared identity/grant functions once (A1 remains D-owned), expose protected headless operations through G6B, and later wire the App to those same functions. Public signup pages, catalog/credit/usage dashboards, email callbacks and provider Lab are subsequent work. Shared migrations remain under `apps/app/supabase/migrations/`; that path does not create a dependency on running the frontend.

## Meaning of robust and optimized

- **Correct under failure:** every accepted request is durable; fenced execution and persisted output prevent stale writes, duplicate settlement and unsafe retry; reconciliation is explicit when usage is unknown.
- **Bounded under load:** retrieval/decode/prepare/queue/engine/output buffers, time, disk and concurrency have enforced limits. Reject overload honestly rather than hiding it in an ever-growing queue. Fairness and cancellation remain effective during load.
- **Operable:** metrics/alerts and readiness expose problems without content leakage; deployment, drain, restart, backup restore and rollback are actually rehearsed. Runtime availability is independent of App/Lab uptime.
- **Measured optimization:** freeze a representative workload, baseline and acceptance criteria; profile phases; compare controlled preparation/engine changes; publish final limits, latency distributions, error/rejection rates, successful-work throughput and cost at those constraints. Model quality/parity is part of acceptance.

Do not invent a universal throughput or latency target. S2M/E1B record the workload owner’s targets or explicitly provisional engineering criteria before tuning. Missing SOP ground truth blocks accuracy claims, while processor/output parity and endpoint correctness still need evidence. A single GPU may be recoverable and bounded without being highly available; I4 can address a separately justified fleet/availability target independently of the UI. “Complete backend” is a certified supported envelope, not every future hardware/model/modality optimization.

## Dependency and worktree order

| Band | Work | Exit / parallel boundaries |
|---|---|---|
| B0 — preserved baseline and contracts | Review audit; F2R, I0, E2R, S2M; then F2P | F/I/E/S lanes can run independently. Review committed fixtures before feature consumers. |
| B1 — durable endpoint | D1R → D2–D5 and A1; M2/M3, Q2/Q3, W2/W3, G1R/G2/G3/G4U/G6B | D owns schema/financial functions; M/Q/W/G independent paths; common wiring coordinator-owned. No C/U frontend lane. |
| B2 — backend integration/deployment | E3B → I2B; then I3B recovery and E1B baseline measurements | Local service acceptance first. Infrastructure and performance evidence can run separately on allocated targets; do not collide on service ownership. |
| B3 — evidence-driven tuning | M4 then W4; operator/GPU experiments serialized on a shared target | Design/harness changes can overlap on isolated fixtures; measured M4 result precedes final W4 comparison so attribution stays clear. |
| B4 — final endpoint gate | E4B on combined final artifacts/config | Recheck protocol, security, accounting, parity, sustained/burst/soak/restore and independent operation. |
| A — consumer launch next | A2/A3/C0/C3A/U1R/U2/U3; E3A/I2A/I3/E4 | Reuse backend gates and infrastructure, add browser/onboarding/account UX. |
| L — provider Lab afterward | Existing L/T/J/N/H/B/P/R plans | No change to full follow-on roadmap or source-purpose rights. |

The manifest owns precise start/integration edges. No start-ready fake work may be declared integrated. Every package below is split into reviewable 2–8-hour slices before assignment. E1 corpus/harness and W3 pin/cancellation/drain work already exist in the plan: E1B/M4/W4 extend them, not replace them.

## API compatibility and optimization boundaries

S2M pins the actual Marlin artifact/processor and supported request/response shapes. Reuse the existing HTTP/OpenAI-style subset and explicit job routes; ordinary chat never silently turns into 202. Live video input and robot actions remain separately contracted extensions. A large dataset is processed as bounded owned items with stable identities and resume, not a new unbounded batch API.

Start with runtime/media optimizations that preserve the model and preprocessing contract. Any change to frame policy, resolution, dtype, quantization, pruning, speculative behavior or custom kernels that changes numerical/semantic behavior is a separately pinned candidate with parity/quality tests. Introduce it only when the measured bottleneck and supported engine justify it. New chips/fleet/transport remain conditional; no blanket support claim follows from this plan.

## Pending inputs and launch conditions

[P-01/P-02/P-03/P-04/P-06](15-pending-inputs.md) cover rates/legacy accounts, real services, GPU/environment and model capabilities; P-18 records backend performance/availability targets. Public signup email/callback P-05 and Lab origin P-08 do not block this operated endpoint. SOP benchmark P-07 limits accuracy claims, not basic documented endpoint availability. A missing target blocks live evidence, not independent backend code/runbooks/tests. Production cutover and paid infrastructure remain explicit operations after reviewable readiness.

## Detailed backend additions

## G6B — Headless endpoint provisioning and operations

**Owner:** G. **Start:** F2P. **Real integration:** D1R, D5, A1, G1R. **Status:** planned.

**Owned paths:** `apps/infrx-api/infrx/operations/ (proposed)`; `apps/infrx-api/tests/g/`; `apps/infrx-api/client_example.py (coordinate with G owner)`.

### Implementation slices

1. G6B.a: Build an operator-only CLI or narrow service adapter over shared auth/state for existing verified consumer identities, personal wallet binding, scoped key issue/revoke/rotate and suspension. Reuse A1/D5 for grants and audited adjustments; never mint credits directly in a script.
2. G6B.b: Publish the pinned Marlin deployment/capability/rate records through protected operations; inspect own usage/holds and durable jobs, request cancellation and reconciliation with audit records. D owns any needed database mutations.
3. G6B.c: Add a headless quickstart and per-item large-dataset client recipe with stable idempotency, bounded concurrency, upload/poll/resume and explicit failures. Secrets come from environment/secure input, are revealed once where needed and never enter argv/logs.

### Acceptance and failure proof

A provisioned client can obtain scoped credentials, call the endpoint and reconcile its usage with both Next.js applications stopped. Preserve the individual 10,000-credit policy and historical USD. Public signup is not needed for this operated endpoint release; credentials still use the real auth/tenant model.

Foreign tenant, forged operator, revoked key, replayed adjustment/grant, unpriced/private deployment and stale rate are denied or deduplicated. No production demo credentials, direct table balance edits or unmetered fallback.

**Oracles:** API-OPS, API-AUTH, CREDIT-IDENTITY, CREDIT-RATE. Hand back current SHA, reviewed diff, commands/environment/skips, raw measured artifacts, regression/fault proof, rollback and remaining inputs.

## E3B — Backend-only durability, security and protocol integration gate

**Owner:** E. **Start:** E2R, F2P. **Real integration:** D1R, D5, G1R, G2, G3, G4U, G6B, M3, Q3, W3, I0, S2M. **Status:** planned.

**Owned paths:** `tests/integration/backend/ (proposed)`; `research/plan/evidence/e/`.

### Implementation slices

1. E3B.a: Compose actual PostgreSQL/PostgREST, object storage and the selected queue mode with controlled engine; provision two real tenants/keys through G6B and run URL/upload/text/video supported sync/SSE and explicit async journeys with no App/Lab process.
2. E3B.b: Exercise acceptance/preparation/dispatch/output/settlement crash boundaries, retry/idempotency, stale leases, revocation, malformed media, disconnect/backpressure, cancellation, saturation and queue rebuild; assert durable rows and accounting conservation.
3. E3B.c: Run intentional defect detection and merged-tree checks; publish API conformance fixtures, required telemetry and unresolved environment-only cases. If capture/callbacks are enabled, add their feature suites; off is a valid initial configuration.

### Acceptance and failure proof

BACKEND-LOCAL passes on actual local services independently of signup/catalog/usage UI, Lab, ClickHouse content projections or judge. Every accepted job has one durable identity and terminal/reconciliation path; invalid/overloaded calls cannot leak capacity, holds or unauthorized content.

Prove tests fail for missing durable acceptance, stale append, duplicate settlement, foreign result access and forced fallback to legacy unmetered ingress. Missing Docker/services is pending, not a skipped successful gate.

**Oracles:** BACKEND-JOURNEY, DUR-ADMIT, DUR-FENCE, DUR-OUTPUT, DUR-SETTLE, DUR-CAP, DUR-OUTBOX, DUR-RLS, MEDIA-SEC, API-MODES, API-STREAM, API-OPS, CREDIT-SPEND. Hand back current SHA, reviewed diff, commands/environment/skips, raw measured artifacts, regression/fault proof, rollback and remaining inputs.

## I2B — Reproducible Marlin endpoint deployment independent of frontends

**Owner:** I. **Start:** I1, F2P. **Real integration:** E3B, I0, W3, G6B. **Status:** planned.

**Owned paths:** `infra/ backend deployment (exclude T-owned ClickHouse)`; `apps/infrx-api/deploy/`; `research/plan/evidence/i/`.

### Implementation slices

1. I2B.a: Package gateway, preparation/worker, selected queue mode and durable services with pinned image/model/processor, config schema, least-privilege identities, resource/disk budgets and startup/readiness rules.
2. I2B.b: Provide deploy/preflight/migrate/drain/rollback scripts and local rehearsal; configure TLS/ingress, private engine, protected metrics and names-only secret references. Reuse I0 atomic installation and D-owned additive migrations.
3. I2B.c: On an allocated staging/GPU target, deploy the headless endpoint and prove authenticated external calls, internal-engine isolation, owned uploads/results and observed accounting with App/Lab absent; record precise SHA/config/region and legacy-account transition.

### Acceptance and failure proof

No Vercel app, browser callback or signup email dependency. The endpoint is reproducibly deployable and has independently observable health/readiness. Actual target application requires allocated scope/resources; local script tests alone are implemented, not hosted integration.

Partial secret/config/migration failure cannot restart into an unsafe state or expose the raw engine. Public health is sanitized; expired/revoked credentials and blocked URLs fail on the deployed path.

**Oracles:** BACKEND-DEPLOY, DEPLOY-FAILCLOSED, OPS-RECOVER. Hand back current SHA, reviewed diff, commands/environment/skips, raw measured artifacts, regression/fault proof, rollback and remaining inputs.

## I3B — Backend recovery, observability, restore and rollback proof

**Owner:** I. **Start:** I2B, F2P. **Real integration:** E3B. **Status:** planned.

**Owned paths:** `infra/ backend metrics/alerts/runbooks`; `apps/infrx-api/deploy/`; `research/plan/evidence/i/`.

### Implementation slices

1. I3B.a: Publish operational metrics for preparation/queue/prefill/decode/journal/settlement phases, CPU/GPU/memory/disk, saturation/rejections, accepted outcomes, lease loss and reconciliation; define sanitized dashboards/alerts without customer payloads.
2. I3B.b: Drill gateway/worker/engine/host restart, DB and object-store interruption, queue-index loss, backup restore, disk exhaustion, drain and rollout rollback; reconcile accepted jobs, pins, holds and terminal usage after each.
3. I3B.c: Record measured recovery windows, durability boundaries, runbooks and alert delivery. Test compatibility during additive migration and rollback; use maintenance refusal when no safe metered runtime exists.

### Acceptance and failure proof

Observed recovery and accepted-job/accounting invariants are evidenced independently of the App. Single-GPU outage behavior is explicit: automatic process recovery is not multi-host high availability. Alerts and recovery tools are operator-facing backend requirements.

Inject node/process loss and storage/network faults; no silent accepted-job loss within declared durable boundaries, duplicate charge, leaked hold or unsafe downgrade. Backups must be restored and checked, not merely listed.

**Oracles:** OPS-RECOVER, BACKEND-OBSERVE, DUR-OUTBOX. Hand back current SHA, reviewed diff, commands/environment/skips, raw measured artifacts, regression/fault proof, rollback and remaining inputs.

## E1B — Measure the end-to-end Marlin baseline and operating envelope

**Owner:** E. **Start:** E1, S2M, F2P. **Real integration:** E3B, I2B, W3. **Status:** planned.

**Owned paths:** `models/marlin2b/bench.py`; `models/marlin2b/corpus-synth/`; `models/marlin2b/tests/`; `models/marlin2b/results/ (new evidence only)`. `tests/integration/backend/performance/` is withdrawn until I2B has deployed (coordinator, 2026-09-22: every case would be a skip without a live target; the run matrix is predeclared in `models/marlin2b/results/E1B-protocol.md`).

### Implementation slices

1. E1B.a: Extend the existing E1 corpus/client only where needed to cover clip duration/resolution/codec/frame-count/output-length distributions, upload/URL paths, cold/warm preparation and mixed tenants; freeze profile/seed/reference and predeclare latency/error/quality/cost acceptance before tuning.
2. E1B.b: Measure direct engine and full gateway on identical pinned input/processor semantics; capture retrieval/decode/preparation, queue, prefill/TTFT, decode, journal/DB, persistence and settlement time alongside CPU/GPU/memory/I/O.
3. E1B.c: Run open-loop sustained and burst traffic, concurrency/rate sweeps, cancellation and long-duration stability; include rejects/timeouts/failures in denominators. Publish raw observations, confidence/sample sufficiency, bottlenecks and honest recommended capacity limits.

### Acceptance and failure proof

An actual GPU baseline and bounded serving envelope exist before M4/W4 performance claims. Report successful videos per second or video-seconds processed per second together with clip/frame/output profile, token metrics, p50/p95 and supported tails, cost per successful unit and full service costs; CREDIT is not USD.

Repeated single-clip/warm-cache-only results, undersampled p99, hidden rejections, queue growth, rising memory or changed preprocessing invalidate claims. Missing SOP labels prevents accuracy certification; processor/output parity still has to be measured.

**Oracles:** PERF-ENVELOPE, MARLIN-SOP, MEDIA-PARITY. Hand back current SHA, reviewed diff, commands/environment/skips, raw measured artifacts, regression/fault proof, rollback and remaining inputs.

## M4 — Optimize bounded video retrieval, decoding and preparation

**Owner:** M. **Start:** M2, M3, F2P. **Real integration:** E1B, W2. **Status:** planned.

**Owned paths:** `apps/infrx-api/infrx/media/`; `apps/infrx-api/tests/m/`.

### Implementation slices

1. M4.a: Profile the E1B bottleneck; reduce duplicate fetch/decode/copy work and tune bounded preprocessing concurrency/prefetch/materialization against actual CPU, RAM, disk and GPU transfer limits.
2. M4.b: Reuse the existing versioned tenant-scoped preprocessing cache only within its source/expiry semantics; validate media/processor identity and garbage collection under simultaneous jobs. Do not introduce response caching or cross-tenant content reuse.
3. M4.c: Run controlled before/after cold/warm and adverse-media trials, correctness/security/parity tests and load/fault cases; ship only supported improvements, or retain baseline with evidence that proposed changes failed the criteria.

### Acceptance and failure proof

Published settings improve a measured bottleneck or are explicitly rejected, with raw evidence. Frame sampling/resolution/timestamps/normalization changes are material serving changes requiring a separately versioned quality check; they cannot masquerade as lossless implementation tuning.

Cache aliasing, reordered/time-shifted frames, cross-tenant reuse, decompression pressure, duplicate requests, expired content and cancellation are tested. Increased throughput with worse required quality/latency or unbounded memory is not a pass.

**Oracles:** MEDIA-OPT, MEDIA-SEC, MEDIA-PARITY, PERF-ENVELOPE. Hand back current SHA, reviewed diff, commands/environment/skips, raw measured artifacts, regression/fault proof, rollback and remaining inputs.

## W4 — Tune Marlin GPU serving and scheduler admission from measured evidence

**Owner:** W. **Start:** W3, F2P. **Real integration:** E1B, M4, Q3. **Status:** planned.

**Owned paths:** `apps/infrx-api/infrx/worker/`; `apps/infrx-api/tests/w/`; `models/marlin2b/serve.sh`; `Scheduler/config changes via Q/coordinator`.

### Implementation slices

1. W4.a: Use E1B phase/resource evidence to choose a bounded experiment matrix for engine batch/concurrency/token/KV-memory limits, context bounds, scheduling/admission and other capabilities actually supported by the pinned engine; verify available flags in its source/docs before use.
2. W4.b: Compare one controlled change at a time under mixed clips/output lengths and tenants; coordinate Q/config changes rather than building a second scheduler. Keep raw/visible output, usage, cancellation, fencing and journal ordering conformance intact.
3. W4.c: Run sustained/burst/cold/recovery trials on the chosen candidate and paired baseline, attach quality/parity and cost/tail evidence, then freeze the selected reproducible runtime profile and overload limits with rollback.

### Acceptance and failure proof

Optimized means measured settings on an identified GPU/model/workload satisfying predeclared correctness/quality/latency/error/resource constraints, not a promise of global optimality. Quantization, pruning, speculative methods or kernel rewrites are follow-on variant experiments unless baseline evidence justifies and separately validates them.

OOM, growing KV/host memory, short-job starvation, cancellation failure, output/usage drift, severe tail latency or overload masking disqualify a candidate. No maximum-throughput setting is shipped solely from token/s on identical cached prompts.

**Oracles:** ENGINE-OPT, PERF-ENVELOPE, MEDIA-PARITY, OPS-RECOVER. Hand back current SHA, reviewed diff, commands/environment/skips, raw measured artifacts, regression/fault proof, rollback and remaining inputs.

## E4B — Certify the robust and measured Marlin endpoint release candidate

**Owner:** E. **Start:** E3B, F2P. **Real integration:** I3B, E1B, M4, W4. **Status:** planned.

**Owned paths:** `tests/integration/backend/`; `models/marlin2b/results/ (new evidence only)`; `research/plan/evidence/e/`.

### Implementation slices

1. E4B.a: Re-run the backend protocol/security/durability suite and actual Marlin corpus at the final combined deployment/config; execute the large-dataset client recipe with interruption/resume and exact usage reconciliation.
2. E4B.b: Run the declared load envelope, soak, overload and recovery matrix with final M4/W4 settings, actual media/object/DB network placement and App/Lab stopped; reject any optimization that invalidates earlier evidence.
3. E4B.c: Produce a release decision with SHA/image/artifact/config hashes, measured limits/SLOs/cost/quality coverage, remaining inputs, failure/rollback triggers and operator runbook. Deliver headless request examples and endpoint capability docs.

### Acceptance and failure proof

BACKEND-READY means a provisioned external client can use the real metered Marlin endpoint reliably within a measured envelope and operators can detect/recover failures. It requires allocated GPU/staging proof; software-only tests cannot close this gate. Public cutover is a separately recorded authorized action. App work consumes this certified backend next.

Any missing real-service/model evidence, unresolved security/accounting defect, untested restore, uncontrolled resource growth or unsupported quality claim leaves the appropriate gate pending with a named owner; do not declare a vaguely super-robust endpoint.

**Oracles:** BACKEND-JOURNEY, BACKEND-DEPLOY, BACKEND-OBSERVE, PERF-ENVELOPE, ENGINE-OPT, MEDIA-OPT, OPS-RECOVER, MARLIN-SOP. Hand back current SHA, reviewed diff, commands/environment/skips, raw measured artifacts, regression/fault proof, rollback and remaining inputs.
