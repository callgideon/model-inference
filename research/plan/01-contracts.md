# Shared contracts — version 2 product amendment

The [database map](06-database-map.md) specifies persistence keys and constraints. Read [architecture](../platforms/01-architecture.md), [credits](../platforms/02-credits.md) and [API boundaries](../platforms/07-api-contracts.md) for the 2026-09-21 amendment. F2R repairs the implemented v1 contract at `271add9`; F2P must encode this revision 2 as executable types, fixtures and contract tests. This document is the target, not a claim that product-v2 code or SQL exists. Preserve the implemented signatures in [ports.py](../../apps/infrx-api/infrx/contracts/ports.py) and accepted v1 rulings except explicitly revised behavior. Changes require a coordinator-owned revision and affected consumers' tests.

**Executable encoding.** Revision 2 exists as code: the v1 → v2 field map, its rulings (R64–R78 in [08 §10](08-contracts-v1-encoding.md)) and what the wire-in composed are in [01a-contracts-v2-map.md](01a-contracts-v2-map.md); the persistence side is [06a-database-map-v2.md](06a-database-map-v2.md).

## Identity, authorization and errors

- `request_id`: UUID minted at ingress, also the accepted job/usage/trace identity and `Inference-Id`. Rejected requests retain a request ID but are not accepted jobs or billable usage.
- Public `job_handle`: opaque cryptographically random lookup identifier, separate from the UUID. Every lookup verifies organization ownership; possession is insufficient. Chat response ID is `chatcmpl-<request_id>`.
- `AuthContext`: org_id, key_id, authenticated principal, role, entitlement_version. Admission rechecks key revocation, org suspension and current entitlement in its transaction; cached identity never bypasses that check. Legacy key must map to an explicit org/key for accepted pilot requests or be disabled at cutover.
- Revision 2 adds trusted consumer wallet binding and distinct `ProviderAuthContext`/credential audience. Requests pin provider_org_id, model/serving/deployment revision and access-policy version. Neither consumer org ownership nor the `models.provider` display label establishes provider access. Public listings can be operator-managed before Lab exists.
- `ErrorEnvelope`: OpenAI-style error object with stable code, safe message and request_id; no upstream exception, signed URL, storage key or secret. Error classes: validation/unsupported 400, auth 401, ownership 404, forbidden 403, exhausted credit 402, capacity/rate 429 with retry guidance, unavailable durable dependency 503, synchronous deadline 504. Oversized intake 413. Public health is generic; protected readiness explains component state.

## Core records

| Type | Required content |
|---|---|
| NormalizedRequest | schema_version, request_id, org/key, model revision, canonical messages and parameters, immutable payload ref/digest, media refs, execution mode, timestamps/deadlines, trace policy snapshot |
| PriceSnapshot | immutable approved rate-card version, unit CREDIT, input/output per-million rates as decimals, meter rules and serving/deployment revision, captured_at; public calls require a published listing, private dev calls require matching endpoint credential/entitlement |
| Admission | job ID/handle, idempotency scope/hash, payload hash, price snapshot, maximum hold, capacity reservations, state and outbox events |
| Lease | job_id, generation integer, worker_id, expires_at from database clock; every execution mutation includes this token |
| Chunk | job_id, generation, sequence integer, canonical event payload, event type, persisted_at; unique composite key |
| TerminalOutcome | succeeded/failed/cancelled/expired, cause, authoritative token usage or explicitly unknown, immutable result ref, settlement state |
| TraceEnvelope | schema version, request/org/key, mode, capture timestamps, content-complete flag, bounded canonical content ref, loss reason, request/model/price versions |
| Feedback | stable ID, request/org, author principal and role, submission channel, rating/correction, optional explicit calibration set, created_at |
| JudgeRun | stable run/sample IDs, consent snapshot, budget reservation, submit intent, external batch ID if known, state and reconciliation timestamps |

Consumer amounts: use `numeric(20, 8)` CREDIT in separate wallet/ledger/hold records; preserve baseline USD history exactly, never relabel it or mix units. Internal costs and Lab judge budgets remain explicitly denominated USD. Compute with decimals, never binary floats. Round each final request debit once to eight decimal places using round-half-up; maximum reservations round upward. Grants are positive, debits negative, holds are separate reservations. Balance view exposes ledger total, reserved total and available = total minus reserved. Database transaction locks serialize grant/admit/settle updates per wallet. Amounts cross JSON as decimal strings. Token counts are nonnegative integers from authoritative engine usage; output chunks are never a token estimator for billing.

`SignupGrant`: unique initial entitlement by individual user, immutable wallet, +10000.00000000 CREDIT, verification evidence reference, campaign metadata and ledger operation. Campaign revisions/org creation do not reset eligibility. Wallet is user-owned and linked to the initial personal consumer org. Atomic issuance is server-only and idempotent. See [full credit and migration policy](../platforms/02-credits.md).

Wallet kind distinguishes individual consumer wallets from operator-funded provider dev budgets. Provider dev wallets receive no signup grant and cannot transfer into consumer wallets. A private dev credential binds provider, endpoint/environment and dev wallet; public consumer keys cannot access private dev endpoints. Both use the same CREDIT hold/settlement invariant. Judge/training budgets remain separate USD records.

Maximum hold uses the accepted model's validated input ceiling and requested output ceiling at the immutable rates. Before preparation determines actual media tokens, reserve the conservative input ceiling (context limit minus requested maximum output); reject preparation if the exact prompt exceeds that ceiling. Actual charged tokens cannot exceed the validated/reserved envelope. Serving preprocessing is included in the pilot price rather than adding an unspecified fee. A provider/engine protocol violation exceeding the envelope is a platform failure requiring reconciliation, not an unreserved customer debit.

## Ports and return semantics

These are summaries of operations; `ports.py` is the implemented v1 signature authority and F2P publishes the v2 diff. All return typed domain errors and accept injected clock/IDs/storage clients for tests.

| Port / owner | Operations | Contract |
|---|---|---|
| JobStore / D | admit(request, idem, caps); get_owned(org, handle); claim_preparation(job, worker); prepared(lease, media); load_work(lease); claim(job, worker); heartbeat(lease); cancel(org, handle); complete(lease, outcome); recover() | Atomic operations, idempotent terminal transitions, DB time and generation checks. Never expose unrestricted tenant reads to route callers. |
| StreamStore / D | append(lease, events); read_owned(org, job, cursor, limit); finalize_in_transaction(outcome); expire(now) | Commit before relay; cursor includes generation/sequence. Explicit gap/expiry error; no synthetic replay from regenerated output. |
| MediaStore / M | stage/attach per F2R produced-ref revision; prepare per fenced job/profile; create_upload(org, constraints); finalize_upload(org, handle); resolve_owned(org, ref) | Immutable source content, checksum/size validation, tenant-scoped references, no caller-selected filesystem path. |
| Scheduler / Q | enqueue(index_event); claim_candidate(worker); acknowledge(index_event); remove(job); rebuild(snapshot) | Index only; a candidate becomes executable only through JobStore.claim. Replays cannot duplicate durable jobs or capacity reservations. |
| Engine / W | generate(lease, prepared_request); cancel(lease); health(); drain() | Canonical events plus authoritative usage/result; enforce attempt fencing before persistence and output. |
| TraceSink / T | offer(envelope); stats(); flush(deadline) | Bounded, nonblocking request-path offer. Returns accepted-in-memory/dropped, never promises fsync immediately. |
| FeedbackService / D, adapter C/G | accept(auth, request, feedback, idem); list_owned(auth, request) | PG commit plus outbox before acknowledgment; tenant ownership independent of ClickHouse projection lag. |
| JudgeCoordinator / D | reserve(run, consent, max_cost); begin_submit(run); record_submission(run, external_id); settle(run, actual); quarantine(run, reason) | Atomic budget and submission state; duplicate calls produce one run/intent. |
| Consumer services / C3A | own usage, CREDIT balances, keys, settings, operator grant | Named operations, bounded pagination, trusted consumer wallet/org identity; no trace/judge dependency for core usage. |
| Lab and data services / C2/C3F/C3L, L | provider registry, deployment, authorized traces/content/review, judge runs | Provider membership plus purpose-specific source grants; no generic SQL/object resolver or implicit customer-data access. |

## HTTP behavior

- `/v1/chat/completions`: synchronous by default. Explicit `Prefer: respond-async` may produce 202 only before headers; echo preference applied when used. `POST /v1/jobs` always requests async and returns 202 only after durable acceptance.
- `GET /v1/jobs/{handle}` returns owned status and result availability; `DELETE` requests durable cancellation. `GET /v1/jobs/{handle}/result` returns result, pending conflict, failure or expired 410. `GET /v1/jobs/{handle}/events` supports bounded replay with `Last-Event-ID`; invalid cursor 400, replay gap/expired journal 410. Status remains available after result expiry under metadata policy.
- Upload flow: `POST /v1/uploads`, upload via constrained signed destination, then `POST /v1/uploads/{handle}/complete`. Completion verifies object metadata/checksum and ownership before use. JSON media references identify owned upload handles; arbitrary S3 paths are never accepted.
- `POST /v1/feedback` uses a request ID and an idempotency key; 201 means PG acceptance. `GET /v1/traces/{request_id}` exports owned trace metadata/content subject to mode/expiry. Original traces spec supplies field semantics, overridden here for identity/provenance/retention.
- Optional async completion callbacks use a registered owned HTTPS destination and terminal outbox; signed immutable events are at-least-once, independently retryable and never a reason to rerun inference. G5 defines capped retry/dead-letter policy; D stores intent and delivery state. No automatic callback on ordinary synchronous chat.
- Idempotency scope is org + operation + key; same canonical payload returns original acceptance, changed payload is 409. Retain mappings/tombstones at least 24h after terminal state; active jobs never lose mappings. An expired replay must not silently submit a new billable job. Feedback/grants retain their uniqueness for their record lifetime. F2 freezes exact envelopes with fixtures.
- Supported initial model request: text/video, `n=1`, maximum output 2, 048, per-request total context 32, 768 after preprocessing/tokenization. Reject unsupported tools/structured-output options explicitly. Context is not summed across concurrent requests. Engine/prompt profiles are versioned.

## Limits and deadlines (provisional defaults)

| Setting | Default / rule |
|---|---|
| Intake | 96 MiB encoded body; 30s from first byte; decoded base64/media source maximum 64 MiB |
| URL fetch | Connect 3s, total 20s across up to 3 redirects; validate and pin every destination |
| Preparation | Overall 120s from completed bounded intake; probe 10s; transcode budget min(remaining preparation time, max(15s, 0.5 × duration)) |
| Queue wait | Interactive 10s; async 600s; starts at durable queued transition; no extension through retries |
| Generation | Overall 300s; first-token 60s; inter-event stall 20s; bounded retries do not reset absolute job deadline |
| Lease | 120s, heartbeat every 40s, DB time; at most two prepublication retries after initial attempt |
| SSE progress | Comment keepalive every 10s after acceptance; target first progress within 2s, measured including persistence, not a promised preparation completion time |
| Journal capacity | 16 MiB reserved per admitted job, 1 GiB total active journal reservation; event maximum1 MiB; reject before acceptance if reservation unavailable. Actual stored bytes and unexpired terminal chunks also consume the total budget. |
| Preparation/capacity | 2 active preparation processes per pilot host; 64 total accepted nonterminal jobs, 16 per org, 8 per key, subject to journal/credit caps; all provisional and configurable |
| Initial engine limits | Engine sequences target 8, worker admission target 10; validate against real KV/memory/latency before enabling |

Admission rejects elapsed deadlines and clamps a future caller bound to min(caller deadline, DB now + preparation + queue + generation budgets), persisting that accepted bound for replay (R-3). No gateway skew margin substitutes for the DB clock. Async mode detaches client lifetime, not these deadlines. Sync timeout/disconnect requests durable cancellation; do not leave silently executing work. Async event-observer disconnect detaches only. Explicit DELETE always attempts cancellation. A timeout racing a committed result returns that result where possible; durable state wins.

## Privacy and retention

Trace mode off produces no per-request ClickHouse trace row or content. Minimal stores metadata only; full allows canonical logical input/output and original text without lossy rewriting. Raw HTTP bytes are not promised after media normalization. Temporary media for serving is independent of trace content consent and is disclosed as processing cache. Tenant source digest + profile version namespace both media cache keys and engine multimodal UUIDs/prefix salts.

Result access expires at 24h, processing cache at 7d, full trace content at owner-selected <=90d, trace metadata at 13 calendar months. Short-lived signed URLs cannot outlive authorization/content expiry. Deletion immediately removes logical access; asynchronous storage cleanup is monitored. Judge consent is separate and must be current at submission, with recorded history.

## Verification log

- 2026-09-20: Frozen proposed v1 semantics for F2 implementation. All configuration values here require tests and, where performance-dependent, pilot measurement.

- 2026-09-21: Amended for separate consumer App/provider Lab, individual signup credits and independent release gates; see the platform-split review. Implementation evidence on the other system remains unverified here.

- 2026-09-22: F2P wire-in (item 7, file 13): linked the executable v2 appendix (01a) and its persistence appendix (06a). No contract text here changed.

## F3 follow-on Lab contracts (not App launch prerequisites)

F3 publishes the concrete Python/TypeScript encoding and shared fixtures for these records after F2P. The following fields/invariants define its required scope; do not add all later schemas to the App runtime just to satisfy a roadmap.

| Record | Required identity/configuration | Invariants |
|---|---|---|
| SourceRef / DataUseGrantRef | owner, source digest/episode/time basis, license/provenance, purpose/destination, expiry/revocation version | Resolve current authorization at access/submit; snapshots audit permission at a time, never override later revocation |
| DatasetVersion / Sample | immutable manifest/schema hash, source and parent refs, sample ID/content digest, media timestamps/units, labels/method, grouping/split policy/seed | Publish atomically; corrections create new records/version; preserve train/dev/holdout partition and all source restrictions |
| HarnessRevision | prompt/processor/input/tool schema/recorded response refs, adapter version and capability coverage | Immutable; safe built-in replay adapters; unsupported side effects explicit; no uploaded arbitrary code |
| EvaluationRun / Case / Attempt | dataset/split, serving/harness/evaluator/environment pins, seed, case universe, purpose, budget, lease generation, idempotency key | Immutable run specification; one logical case result; attempts and missing/errors retained; cancellation and stale writes fenced |
| EvaluationReport / Decision | paired case IDs, denominator/coverage, source-cluster slices, latency and unit-tagged costs, uncertainty method, predeclared thresholds | Record method/version and inconclusive outcomes; no incomparable run promoted by aggregate-only score |
| CheckpointReceipt | external run/provider, authenticated event ID/sequence, artifact digest, subscription/suite revision | Unique receipt and benchmark per subscription/version; late/duplicate/out-of-order events visible; no automatic deployment |
| Annotation / ReviewDecision | original evidence, method/model/prompt/rubric, reviewer provenance, correction parent, confidence/disagreement | Human, synthetic and customer feedback origins stay distinct; no forgery of calibration authority |
| ExternalPipelineRun | connector/capability version, dataset/config hash, submit intent, external ID, callback receipts, checkpoints, unit-tagged reservations/costs | Manual export/import is supported explicitly; automatic submit is available only for tested adapters; ambiguous submit is never blindly retried |
| ReleasePolicy / Assignment | provider/endpoint, permitted serving variants, allocation unit/seed, traffic/budget bounds, predeclared guardrails, policy revision | Explicit pins honored; assignment is server-derived; future admissions use current policy, old jobs stay pinned |
| OptimizationEvidence | serving/hardware/runtime/build/dtype/processor pins, baseline/corpus/load profile and raw report digests | New execution identity for material change; measured support limited to the tested combination |

Evaluation states must distinguish queued/running/succeeded/partial/failed/cancelled/restricted; per-case outcomes include explicit missing/unsupported states. External pipeline states must additionally distinguish submission intent, submitted, unknown submission and reconciling. F3/D7/D8 define exact legal transitions and cancellation/late-artifact semantics in executable fixtures. Monetary states use the existing exact budgets/settlement protocol; a new UI must not invent a parallel spend counter.

Requests used for Lab benchmark execution use a funded provider_dev wallet and bounded concurrency; consumer signup grants are not training/evaluation credits. External teacher/training USD costs are never summed with CREDIT. Full object payloads live in authorized object storage; relational rows store bounded references and lineage. Current inference contracts remain backward compatible while the later workers/apps adopt F3.

## F2C.a lifecycle ports (2026-09-24)

Additive to the ports table above; semantics and decisions in [02 §F2C lifecycle amendment](02-durable-protocols.md). Python is the signature authority (`apps/infrx-api/infrx/contracts/v2/lifecycle.py`); the console half (`apps/app/lib/contracts/v2/lifecycle.ts`) carries only browser-safe types - vocabularies, the upload ticket and the readiness view - and exact decoders.

| Port / owner | Operations | Contract |
|---|---|---|
| UploadRepository / D10, adapted by M5 | create(org, constraints); acknowledge_put(org, handle, bytes, digest); complete(org, handle, source); abort(org, handle, refusal); resolve(org, handle); expire(limit) | Durable ticket (constraints, owner, window, receipt, immutable finalized source) reloaded by any process; the caller names no tenant, handle or expiry; one handle names one set of bytes; a failed check is final; another tenant's handle is `not_found`. |
| ReadinessStore / D10, consumed by W5/G7 | admit_ready(request, idem, expectation); readiness(job); claim_preparation(job, worker) | Manifest and marker in the admission transaction; empty manifest is ready-with-zero, missing marker is not_ready; preparation refuses not_ready. |
| ContentLifecycle / D10, driven by M6 | register(identity); references(content); candidates(after, limit); claim(content, generation, holder); tombstone(claim); acknowledge_delete(tombstone) | Persisted eligibility and references decide deletion; fenced leased claims; recheck inside tombstone; tombstoned keys refuse new use until acknowledged; an older generation's acknowledgement is a no-op. |

- 2026-09-24: F2C.a ports section appended. Fake-backed conformance only.
