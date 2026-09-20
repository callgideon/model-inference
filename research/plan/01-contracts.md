# Shared contracts — version 1

The [database map](06-database-map.md) specifies persistence keys and constraints. F2 must encode this document as executable types, fixtures and contract tests before feature branches diverge. This is a specification, not a claim those files exist. Changes require a coordinator-owned contract revision and affected consumers' tests.

## Identity, authorization and errors

- `request_id`: UUID minted at ingress, also the accepted job/usage/trace identity and `Inference-Id`. Rejected requests retain a request ID but are not accepted jobs or billable usage.
- Public `job_handle`: opaque cryptographically random lookup identifier, separate from the UUID. Every lookup verifies organization ownership; possession is insufficient. Chat response ID is `chatcmpl-<request_id>`.
- `AuthContext`: org_id, key_id, authenticated principal, role, entitlement_version. Admission rechecks key revocation, org suspension and current entitlement in its transaction; cached identity never bypasses that check. Legacy key must map to an explicit org/key for accepted pilot requests or be disabled at cutover.
- `ErrorEnvelope`: OpenAI-style error object with stable code, safe message and request_id; no upstream exception, signed URL, storage key or secret. Error classes: validation/unsupported 400, auth 401, ownership 404, forbidden 403, exhausted credit 402, capacity/rate 429 with retry guidance, unavailable durable dependency 503, synchronous deadline 504. Oversized intake 413. Public health is generic; protected readiness explains component state.

## Core records

| Type | Required content |
|---|---|
| NormalizedRequest | schema_version, request_id, org/key, model revision, canonical messages and parameters, immutable payload ref/digest, media refs, execution mode, timestamps/deadlines, trace policy snapshot |
| PriceSnapshot | immutable version, currency USD, input/output per-million rates as decimals, token rules/model revision, captured_at; missing model/rates reject admission |
| Admission | job ID/handle, idempotency scope/hash, payload hash, price snapshot, maximum hold, capacity reservations, state and outbox events |
| Lease | job_id, generation integer, worker_id, expires_at from database clock; every execution mutation includes this token |
| Chunk | job_id, generation, sequence integer, canonical event payload, event type, persisted_at; unique composite key |
| TerminalOutcome | succeeded/failed/cancelled/expired, cause, authoritative token usage or explicitly unknown, immutable result ref, settlement state |
| TraceEnvelope | schema version, request/org/key, mode, capture timestamps, content-complete flag, bounded canonical content ref, loss reason, request/model/price versions |
| Feedback | stable ID, request/org, author principal and role, submission channel, rating/correction, optional explicit calibration set, created_at |
| JudgeRun | stable run/sample IDs, consent snapshot, budget reservation, submit intent, external batch ID if known, state and reconciliation timestamps |

Money: migrate ledger and holds to `numeric(20, 8)` USD; migrate usage cost to the same precision without changing prior values. Compute with decimals, never binary floats. Round each final request debit once to eight decimal places using round-half-up; maximum reservations round upward. Grants are positive, debits negative, holds are separate reservations. Balance view exposes ledger total, reserved total and available = total minus reserved. Database transaction locks serialize grant/admit/settle updates per wallet. Monetary values cross JSON as decimal strings. Token counts are nonnegative integers from authoritative engine usage; output chunks are never a token estimator for billing.

Maximum hold uses the accepted model's validated input ceiling and requested output ceiling at the immutable rates. Before preparation determines actual media tokens, reserve the conservative input ceiling (context limit minus requested maximum output); reject preparation if the exact prompt exceeds that ceiling. Actual charged tokens cannot exceed the validated/reserved envelope. Serving preprocessing is included in the pilot price rather than adding an unspecified fee. A provider/engine protocol violation exceeding the envelope is a platform failure requiring reconciliation, not an unreserved customer debit.

## Ports and return semantics

These are logical async signatures; F2 chooses concrete import/type syntax once. All return typed domain errors and accept injected clock/IDs/storage clients for tests.

| Port / owner | Operations | Contract |
|---|---|---|
| JobStore / D | admit(request, idem, caps, hold); get_owned(org, handle); prepared(job, media); claim(job, worker); heartbeat(lease); cancel(org, handle); complete(lease, outcome); recover(now) | Atomic operations, idempotent terminal transitions, DB time and generation checks. Never expose unrestricted tenant reads to route callers. |
| StreamStore / D | append(lease, events); read_owned(org, job, cursor, limit); finalize_in_transaction(outcome); expire(now) | Commit before relay; cursor includes generation/sequence. Explicit gap/expiry error; no synthetic replay from regenerated output. |
| MediaStore / M | stage(org, request); prepare(job, profile); create_upload(org, constraints); finalize_upload(org, handle); resolve_owned(org, ref) | Immutable source content, checksum/size validation, tenant-scoped references, no caller-selected filesystem path. |
| Scheduler / Q | enqueue(index_event); claim_candidate(worker); acknowledge(index_event); remove(job); rebuild(snapshot) | Index only; a candidate becomes executable only through JobStore.claim. Replays cannot duplicate durable jobs or capacity reservations. |
| Engine / W | generate(lease, prepared_request); cancel(lease); health(); drain() | Canonical events plus authoritative usage/result; enforce attempt fencing before persistence and output. |
| TraceSink / T | offer(envelope); stats(); flush(deadline) | Bounded, nonblocking request-path offer. Returns accepted-in-memory/dropped, never promises fsync immediately. |
| FeedbackService / D, adapter C/G | accept(auth, request, feedback, idem); list_owned(auth, request) | PG commit plus outbox before acknowledgment; tenant ownership independent of ClickHouse projection lag. |
| JudgeCoordinator / D | reserve(run, consent, max_cost); begin_submit(run); record_submission(run, external_id); settle(run, actual); quarantine(run, reason) | Atomic budget and submission state; duplicate calls produce one run/intent. |
| Console services / C | usage, balances, traces, trace_content, feedback, settings, admin_grant, judge_runs | Named operations, bounded pagination, server-side auth on every invocation, no generic caller SQL or arbitrary object key. |

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

Non-stream deadline = preparation + queue + generation budgets subject to the absolute accepted deadline. Async mode detaches client lifetime, not these deadlines. Sync timeout/disconnect requests durable cancellation; do not leave silently executing work. Async event-observer disconnect detaches only. Explicit DELETE always attempts cancellation. A timeout racing a committed result returns that result where possible; durable state wins.

## Privacy and retention

Trace mode off produces no per-request ClickHouse trace row or content. Minimal stores metadata only; full allows canonical logical input/output and original text without lossy rewriting. Raw HTTP bytes are not promised after media normalization. Temporary media for serving is independent of trace content consent and is disclosed as processing cache. Tenant source digest + profile version namespace both media cache keys and engine multimodal UUIDs/prefix salts.

Result access expires at 24h, processing cache at 7d, full trace content at owner-selected <=90d, trace metadata at 13 calendar months. Short-lived signed URLs cannot outlive authorization/content expiry. Deletion immediately removes logical access; asynchronous storage cleanup is monitored. Judge consent is separate and must be current at submission, with recorded history.

## Verification log

- 2026-09-20: Frozen proposed v1 semantics for F2 implementation. All configuration values here require tests and, where performance-dependent, pilot measurement.
