# E4B — Marlin endpoint capability document and headless examples

**Generated** by `tests/integration/backend/endpoint_doc.py --write` from the code at
the commit this file is in; `tests/integration/backend/test_endpoint_doc.py` fails when
it is stale. It describes the metered endpoint **as the cutover mounts it** (G2-R1,
held at this commit): until then the deployed gateway is the legacy one. Nothing here
is a measured limit or an SLO: measured limits are the box's (E4B-release-decision.md).

## Model

| Field | Value | Source |
|---|---|---|
| requested model (pinned) | `nemostation/marlin-2b@2026-09-01` | G6B `marlin_release` / contracts v2 fixtures |
| rate card | `rc_marlin2b_20260901T000000Z_provisional_p01` | provisional until P-01 decides the rates |
| serving revision's engine-options digest | `sha256:4444444444444444444444444444444444444444444444444444444444444444` | ⚠️ the fixture placeholder, not W3's measured pin (E4B config-pin finding) |
| runtime image | `vllm/vllm-openai:nightly` | ⚠️ a moving tag in the published record (same finding) |

## Routes

Every `/v1/` route but `/v1/models` takes `Authorization: Bearer <key>` (a scoped key G6B's operator CLI issues); a handle of another tenant, an unknown handle and a malformed one are the same 404.

| Method | Path | Module | What |
|---|---|---|---|
| GET | `/health` | health | engine liveness (legacy shape; the edge answers it sanitized) |
| GET | `/healthz` | ingress | gateway liveness: `{"status": "ok"}`, no component state |
| GET | `/metrics` | route | Prometheus text for a direct loopback peer only; anyone else gets the byte-identical 404 of an unknown path |
| GET | `/readyz` | ingress | readiness with component state, for a direct loopback peer only (the edge and any proxied caller get the unknown-path 404) |
| POST | `/v1/chat/completions` | ingress | chat: JSON by default, SSE with `"stream": true`, a 202 job with `Prefer: respond-async` |
| POST | `/v1/jobs` | jobs | an explicit asynchronous job: the chat body, answered 202 `JobAccepted` once admission has committed |
| GET | `/v1/jobs/{handle}` | jobs | `JobStatus` from the committed row |
| DELETE | `/v1/jobs/{handle}` | jobs | cancel (`client_cancelled`), answering the committed outcome |
| GET | `/v1/jobs/{handle}/events` | jobs | the committed journal as SSE from `Last-Event-ID`; an observer that leaves detaches, never cancels |
| GET | `/v1/jobs/{handle}/result` | jobs | `JobResult`; `result_pending` (409) while it runs, `result_expired` (410) past the result's TTL |
| GET | `/v1/models` | models | the model list (OpenAI shape) |
| POST | `/v1/uploads` | uploads | create an upload from its constraints: 201 `UploadCreated` |
| PUT | `/v1/uploads/{handle}` | uploads | the bytes, to the constrained destination: 204 |
| POST | `/v1/uploads/{handle}/complete` | uploads | finalize (no body): 200 `UploadCompleted`; then send `infrx-upload:<handle>` |

## Execution modes and idempotency (R94)

| Mode | How a client selects it |
|---|---|
| `sync` | `POST /v1/chat/completions` (the default) |
| `stream` | `"stream": true` on the chat route (SSE) |
| `async` | `POST /v1/jobs`, or `Prefer: respond-async` on chat |

`Idempotency-Key` names one operation, one canonical payload **and one mode** (R94): a replay in the same mode answers the same job (the `Idempotency-Replayed` header says so); the same key with another payload or another mode is `409 idempotency_conflict` and writes nothing. A key keeps answering for `idempotency_ttl_s` = 86400 s after terminal. A 202 carries `Retry-After: 2` as the poll hint and `Location` naming the job. `POST /v1/jobs` is always async: a body with `"stream": true` is refused `invalid_request` (400) with `param` `stream`.

## Request parameters

Accepted: `frequency_penalty`, `max_completion_tokens`, `max_tokens`, `messages`, `model`, `n`, `presence_penalty`, `seed`, `stop`, `stream`, `temperature`, `top_p`.

Every other name is refused as `unsupported_parameter` naming itself; these are named so the refusal is deliberate: `function_call`, `functions`, `logit_bias`, `logprobs`, `parallel_tool_calls`, `price_snapshot`, `response_format`, `tool_choice`, `tools`, `top_logprobs`. A `null` value is `invalid_request`.

Content parts: `text` and `video_url`; a video reference is one of `http://`, `https://`, `data:<mime>;base64,…` or `infrx-upload:<handle>`. Roles: `assistant`, `system`, `user`.

## Limits (contracts `limits.DEFAULTS` and the validator)

| Setting | Default | Bounds |
|---|---|---|
| `max_request_bytes` | 100663296 | request body, bytes |
| `max_media_bytes` | 67108864 | one video, decoded bytes (also an upload's ceiling) |
| `max_video_seconds` | 120 | one video's duration, s (profile v1; the deployed cap is configuration: P-20 applies 72) |
| `intake_timeout_s` | 30 | reading a request or upload body, s |
| `media_fetch_timeout_s` | 20 | fetching a video URL, s |
| `media_fetch_max_redirects` | 3 | redirects followed for a video URL |
| `queue_wait_interactive_s` | 10 | queue wait before a sync/SSE job expires (`queue_wait_expired`, free), s |
| `queue_wait_async_s` | 600 | queue wait before an async job expires, s |
| `ttft_timeout_s` | 60 | time to the first token, s |
| `generation_timeout_s` | 300 | generation, s |
| `max_output_tokens` | 2048 | output tokens per request |
| `max_context_tokens` | 32768 | prompt + output tokens |
| `max_active_jobs_per_key` | 8 | active jobs per API key |
| `max_active_jobs_per_org` | 16 | active jobs per organization |
| `max_active_jobs` | 64 | active jobs on the deployment |
| `result_ttl_s` | 86400 | a result stays readable after terminal, s |
| `idempotency_ttl_s` | 86400 | a key keeps answering its job after terminal, s |
| `journal_chunk_ttl_s` | 3600 | the SSE replay window, s |
| `sse_keepalive_s` | 10 | SSE keep-alive comment interval, s |

| Validator bound | Value |
|---|---|
| `MAX_MESSAGES` | 64 |
| `MAX_PARTS_PER_MESSAGE` | 16 |
| `MAX_VIDEO_PARTS` | 1 |
| `MAX_TEXT_CODEPOINTS` | 131072 |
| `MAX_URL_CHARS` | 8192 |
| `MAX_STOP_SEQUENCES` | 4 |
| `MAX_STOP_CHARS` | 64 |

## Error codes (`contracts.errors`)

The body is always `{"error": {"message", "type", "code", "param"}}` with the fixed message below; nothing upstream is ever echoed.

| Code | HTTP | Type | Retry-After | Message |
|---|---|---|---|---|
| `context_length_exceeded` | 400 | invalid_request_error |  | The request exceeds the model context limit. |
| `invalid_cursor` | 400 | invalid_request_error |  | The event cursor is not valid. |
| `invalid_request` | 400 | invalid_request_error |  | The request is not valid. |
| `media_fetch_failed` | 400 | invalid_request_error |  | The media source could not be retrieved. |
| `unsupported_media` | 400 | invalid_request_error |  | The media type is not supported. |
| `unsupported_parameter` | 400 | invalid_request_error |  | A requested parameter is not supported. |
| `invalid_api_key` | 401 | authentication_error |  | The API key is missing, malformed or revoked. |
| `insufficient_credit` | 402 | insufficient_quota |  | The organization does not have enough available credit. |
| `forbidden` | 403 | permission_error |  | This action is not permitted. |
| `model_not_entitled` | 403 | permission_error |  | The organization is not entitled to this model. |
| `org_suspended` | 403 | permission_error |  | The organization is suspended. |
| `not_found` | 404 | not_found_error |  | The requested resource was not found. |
| `idempotency_conflict` | 409 | conflict_error |  | This idempotency key was used with a different request payload. |
| `result_pending` | 409 | conflict_error |  | The result is not available yet. |
| `state_conflict` | 409 | conflict_error |  | The job is not in a state that allows this operation. |
| `idempotency_expired` | 410 | gone_error |  | This idempotency key has expired; submit a new request. |
| `journal_expired` | 410 | gone_error |  | The event journal for this job has expired. |
| `replay_gap` | 410 | gone_error |  | The requested events are no longer available for replay. |
| `result_expired` | 410 | gone_error |  | The result is no longer available. |
| `upload_expired` | 410 | gone_error |  | The upload window has expired. |
| `request_too_large` | 413 | invalid_request_error |  | The request body is too large. |
| `capacity_exhausted` | 429 | rate_limit_error | yes | Capacity is exhausted; retry later. |
| `journal_capacity_exhausted` | 429 | rate_limit_error | yes | Output journal capacity is exhausted; retry later. |
| `rate_limited` | 429 | rate_limit_error | yes | Too many requests; retry later. |
| `internal_error` | 500 | server_error |  | An internal error occurred. |
| `dependency_unavailable` | 503 | server_error | yes | A required dependency is unavailable; retry later. |
| `deadline_exceeded` | 504 | server_error |  | The request exceeded its deadline. |

In a stream that already answered 200, a terminal error event carries one of `status_unknown`, `stream_interrupted`.

## Terminal causes, cancellation and what is billed

| Cause | States | A canceller may give it | Billable |
|---|---|---|---|
| `completed` | succeeded |  | yes |
| `client_cancelled` | cancelled | yes | yes |
| `client_disconnected` | cancelled, failed | yes | yes |
| `sync_deadline` | cancelled, failed | yes | no (platform-absorbed) |
| `queue_wait_expired` | expired |  | no (platform-absorbed) |
| `deadline_exceeded` | expired, failed |  | no (platform-absorbed) |
| `invalid_media` | failed |  | no (platform-absorbed) |
| `preparation_failed` | failed |  | no (platform-absorbed) |
| `engine_error` | failed |  | no (platform-absorbed) |
| `engine_incomplete` | failed |  | no (platform-absorbed) |
| `lost_after_publication` | failed |  | no (platform-absorbed) |
| `journal_write_failed` | failed |  | no (platform-absorbed) |
| `retries_exhausted` | failed |  | no (platform-absorbed) |
| `platform_error` | failed |  | no (platform-absorbed) |

## Usage certainty and settlement

Usage is `authoritative` or `unknown`; only authoritative usage on a billable cause settles a debit. Settlement states: `settled`, `released_free`, `held_unknown`, `released_platform_absorbed`. An unknown-usage hold is reconciled after `unknown_usage_reconcile_s` = 86400 s.

## Headers

`Authorization`, `Idempotency-Key`, `Idempotency-Replayed`, `Inference-Id`, `Last-Event-ID`, `Location`, `Prefer`, `Preference-Applied`, `Retry-After`, `Server-Timing`.

## Response shapes (`contracts.wire`)

| Shape | Fields |
|---|---|
| `JobAccepted` | `job_handle`, `request_id`, `state`, `execution_mode`, `created_at`, `deadline_at`, `idempotency_replayed` |
| `JobStatus` | `job_handle`, `request_id`, `state`, `cause`, `created_at`, `updated_at`, `result_available`, `result_expires_at`, `usage`, `usage_certainty` |
| `JobResult` | `job_handle`, `request_id`, `state`, `cause`, `response`, `usage`, `completed_at` |
| `UploadCreated` | `upload_handle`, `destination_ref`, `max_bytes`, `accepted_mime`, `state`, `expires_at` |
| `UploadCompleted` | `upload_handle`, `state`, `media` |

## Headless examples

```bash
export BASE=https://<host>            # the edge; the gateway's /v1 routes sit under it
umask 077; printf 'Authorization: Bearer %s\n' "$INFRX_API_KEY" > .auth
#          ^ the key is read from the environment into a 0600 header file: never argv

# 1. sync JSON chat - one item, one Idempotency-Key (sop1.<item_key> for a dataset)
curl -sS -H @.auth -H 'Content-Type: application/json' -H 'Idempotency-Key: sop1.k1' \
     "$BASE/v1/chat/completions" -d '{"model": "nemostation/marlin-2b@2026-09-01", "max_tokens": 512, "messages": [{"role": "user", "content": [{"type": "video_url", "video_url": {"url": "https://media.example.com/clip.mp4"}}, {"type": "text", "text": "Provide a spatial description of this clip followed by time-ranged events."}]}]}'

# 2. the same request as SSE: the first frame names the job, the last carries usage
curl -sS -N -H @.auth -H 'Content-Type: application/json' -H 'Idempotency-Key: sop1.k2' \
     "$BASE/v1/chat/completions" -d '{"model": "nemostation/marlin-2b@2026-09-01", "max_tokens": 512, "messages": [{"role": "user", "content": [{"type": "video_url", "video_url": {"url": "https://media.example.com/clip.mp4"}}, {"type": "text", "text": "Provide a spatial description of this clip followed by time-ranged events."}]}], "stream": true}'

# 3. an explicit async job, polled at the 202's Retry-After, then its result
curl -sS -D - -H @.auth -H 'Content-Type: application/json' -H 'Idempotency-Key: sop1.k3' \
     "$BASE/v1/jobs" -d '{"model": "nemostation/marlin-2b@2026-09-01", "max_tokens": 512, "messages": [{"role": "user", "content": [{"type": "video_url", "video_url": {"url": "https://media.example.com/clip.mp4"}}, {"type": "text", "text": "Provide a spatial description of this clip followed by time-ranged events."}]}]}'          # 202 JobAccepted: job_handle
JOB=<job_handle>
curl -sS -H @.auth "$BASE/v1/jobs/$JOB"                     # JobStatus
curl -sS -H @.auth "$BASE/v1/jobs/$JOB/result"              # JobResult (409 result_pending while it runs)
curl -sS -N -H @.auth -H 'Last-Event-ID: <id>' "$BASE/v1/jobs/$JOB/events"   # replay from a cursor
curl -sS -X DELETE -H @.auth "$BASE/v1/jobs/$JOB"           # cancel: client_cancelled

# 4. async on the chat route itself
curl -sS -D - -H @.auth -H 'Content-Type: application/json' -H 'Prefer: respond-async' \
     -H 'Idempotency-Key: sop1.k4' "$BASE/v1/chat/completions" -d '{"model": "nemostation/marlin-2b@2026-09-01", "max_tokens": 512, "messages": [{"role": "user", "content": [{"type": "video_url", "video_url": {"url": "https://media.example.com/clip.mp4"}}, {"type": "text", "text": "Provide a spatial description of this clip followed by time-ranged events."}]}]}'

# 5. an owned upload, then the chat request names it
curl -sS -H @.auth -H 'Content-Type: application/json' "$BASE/v1/uploads" \
     -d '{"accepted_mime": ["video/mp4"], "max_bytes": 67108864}'   # 201 UploadCreated
UPL=<upload_handle>
curl -sS -X PUT -H @.auth -H 'Content-Type: video/mp4' --data-binary @clip.mp4 \
     "$BASE/v1/uploads/$UPL"   # 204
curl -sS -X POST -H @.auth "$BASE/v1/uploads/$UPL/complete"
#   then in the chat body: {"type": "video_url", "video_url": {"url": "infrx-upload:$UPL"}}

# 6. R94: the same key in another mode is a conflict, and writes nothing
curl -sS -H @.auth -H 'Content-Type: application/json' -H 'Idempotency-Key: sop1.k1' \
     "$BASE/v1/chat/completions" -d '{"model": "nemostation/marlin-2b@2026-09-01", "max_tokens": 512, "messages": [{"role": "user", "content": [{"type": "video_url", "video_url": {"url": "https://media.example.com/clip.mp4"}}, {"type": "text", "text": "Provide a spatial description of this clip followed by time-ranged events."}]}], "stream": true}'   # 409 idempotency_conflict
```

## Verification log

- 2026-09-23 (E4B.c): generated by `endpoint_doc.py --write` from the code at the commit
  that adds it (integration head `7c52627` + the E4B commits); every table read from the
  code, only the route descriptions and the curl examples are prose. Not run against any
  deployed endpoint: the cutover that mounts these routes is held (G2-R1).
- 2026-09-23 (E4B review F9): regenerated. Every error status the prose cites is now read
  from the catalogue (and checked in the examples too), the DELETE cause from the jobs
  module's call, the routes needing no key from the modules' own sources; the Headers list
  gains the 202's `Location`, and the POST /v1/jobs streaming refusal is documented.
