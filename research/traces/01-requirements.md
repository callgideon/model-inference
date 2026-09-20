# Deep traces — requirements

> **Implementation amendment — 2026-09-20.** The current implementation authority is [the unified plan](../plan/README.md), especially [contracts](../plan/01-contracts.md), [durable protocols](../plan/02-durable-protocols.md) and [verification](../plan/04-verification.md). The text below is historical research where it conflicts. Trace loss does not stop inference; capture has active-byte, queue, spool and disk limits, and local durability begins at fsync on persistent storage. Off-mode requests have no CH trace row and are excluded from trace-coverage denominators. Canonical logical content is preserved, not raw HTTP wire bytes after normalization. Feedback 201 requires PG/outbox durability and tenant ownership independent of CH lag. Channel and author role are separate; customer console feedback is not an operator calibration label. Judge requires current consent, hard worst-case budget reservations and ambiguous-submit quarantine. CH schema/version/dedup must run against the pinned server; logical expiry is enforced before physical TTL deletion. Retention: 24h results, 7d processing cache, <=90d optional full content, 13mo metadata.


Research and decision date **2026-09-20**. This is the requirements document for
**opt-in, per-request deep tracing** of the inference API: what a trace is, who
turns it on, what it must never cost the request, and what "evaluate the
production model's relevance and accuracy" means as acceptance criteria. Design
follows in [`03-architecture.md`](03-architecture.md); the field-by-field
catalogue is [`02-metrics-catalogue.md`](02-metrics-catalogue.md); the build
order is [`08-phases-and-test-plan.md`](08-phases-and-test-plan.md).

Legend and conventions: [`../METHODOLOGY.md`](../METHODOLOGY.md#legend) —
`[src]` = primary source, **⚠️ TO BE VERIFIED** = estimate with method stated,
`meas.` = measured, `est.` = derived.

---

## 1. Problem statement

The gateway (`apps/infrx-api/gateway.py`) records one metadata row per request
in Supabase `usage_events` — tokens, TTFT, latency, status, cost — and the
console's Usage page shows the aggregates. That answers *how much* and *how
fast*. It cannot answer *what was asked, what was answered, and was the answer
any good*, because by design (`apps/README.md` §4: "No prompt or video content
is stored anywhere") nothing but metadata is kept.

Three things need the content:

1. **Performance analysis of the production model** beyond percentiles: which
   prompts are slow, which clips blow the token budget, where `finish_reason`
   is `length`, what the `<think>` block costs, whether a deployment silently
   landed on the wrong video budget (the 1.9× Path-A/B fork in
   [`../../models/marlin2b/README.md`](../../models/marlin2b/README.md)).
2. **Relevance / accuracy evaluation** of real traffic: human feedback on real
   answers, and an LLM judge scoring a sample against a rubric, both keyed to
   the exact request so a bad score points at a reproducible input.
3. **The closed loop** ([`../platform/00-goal-and-problem-statement.md`](../platform/00-goal-and-problem-statement.md)
   stage S2): production traces are the raw material for annotation (S3),
   datasets (S4) and training (S5). A trace that is lossy or sampled is a
   debugging artefact, not a training example ([`../platform/01-observability-and-tracing.md`](../platform/01-observability-and-tracing.md) §1.1).

The constraint that shapes everything: **tracing must add nothing to the
request's latency.** The gateway already has a zero-hot-path pattern for
usage rows (bounded `asyncio.Queue` → background worker → retry → disk spill);
traces are the same pattern with a larger payload.

## 2. Decisions already taken (this session, 2026-09-20)

These were decided with the owner before this document was written. They are
inputs, not options.

| # | Decision | Consequence |
|---|---|---|
| D1 | **Platform trace store from day one**, not a pilot table in Supabase | ClickHouse + S3 are new infrastructure in this program |
| D2 | **Own ClickHouse + S3, own Traces page in the console**; no Langfuse / SaaS | We own schema, viewer, feedback API, judge job. [`../platform/01`](../platform/01-observability-and-tracing.md) §6.3's "adopt Langfuse" verdict is **superseded** for this program; its schema (§1.5) is kept |
| D3 | **Opt-in per API key** (`trace_level`: `off` \| `metadata` \| `full`) with a **per-request header override** (`X-Infrx-Trace`) | Metadata rows always exist (they do today); content capture is the opt-in |
| D4 | v1 includes **capture + viewer + feedback API + async LLM-as-judge** | Judge model, rubric, sampling and egress policy are in scope |
| D5 | **Capture approach C**: spool-and-ship from the gateway now (own schema); vLLM OTLP engine spans joined later, when the production-api plan's Phase 3 brings a collector | No OTel SDK or Collector in v1; `X-Request-Id` is forwarded so the later join has a key |
| D6 | **ClickHouse self-hosted** in docker on a small non-GPU EC2 in the existing account | We own backups (to S3) and upgrades |
| D7 | Work is **documented first, then built in small verifiable phases**; no implementation before the spec set is reviewed | This tree; [`08`](08-phases-and-test-plan.md) |
| D8 | Judge egress (customer content → Anthropic) is a **separate opt-in** from content capture | `judge_enabled` per key, default off |

## 3. Users and journeys

| Actor | Journey | Today | With traces |
|---|---|---|---|
| **Customer developer** | turns tracing on for a key, runs traffic, opens Traces, filters (key, time, status, latency, has-video, finish reason, score), opens one trace, reads prompt/output/clip metadata, gives a thumbs / correction, sees judge scores | Usage tiles only | full journey; `X-Infrx-Trace: off` on a sensitive route |
| **Customer developer (programmatic)** | posts feedback from their own app with the `Inference-Id` they already receive; exports traces as JSONL for their own analysis | — | `POST /v1/feedback`, `GET /v1/traces` (export) |
| **Operator (us)** | sees every org's traces (operator flag), configures the judge rubric and sample rate per org, watches judge cost, investigates a support ticket by `Inference-Id` | `usage.jsonl` on the box | console admin + ClickHouse |
| **Platform (later, S3–S5)** | builds datasets from traces + scores | — | Parquet export from ClickHouse + S3 blobs (out of scope for v1, see §7) |

## 4. Functional requirements

Ids are `T` (trace), `F` (feedback), `J` (judge), `C` (console), `O` (operations).
"Acceptance" is the check that closes the requirement; each maps to a test id in
[`08`](08-phases-and-test-plan.md).

### 4.1 Capture

| id | requirement | acceptance |
|---|---|---|
| T1 | Every **attributable** request through `POST /v1/chat/completions` produces **exactly one trace row**, keyed by the `Inference-Id` already returned to the client, whatever the outcome (200, 4xx, 5xx, client disconnect, upstream error). A refusal issued before a key is known (401 invalid key, 503 key-lookup unavailable) has no org to write under; it is counted (`infrx_traces_unattributed_refusals_total`), not rowed | count(trace rows) == count(`usage_events`) + count(legacy-key requests in the spool) over any window, and count(rows) + unattributed refusals == arrivals; a 400 for a bad video still has a row with `status=400` and `error_type` |
| T2 | `trace_level` per API key: `off` (nothing beyond `usage_events`), `metadata` (structured row, no content), `full` (row + content blob). Default for new keys: **`metadata`** | key created in the console → row appears without content; toggled to `full` → next request has a `content_ref` |
| T3 | Per-request override header `X-Infrx-Trace: off \| metadata \| full`; the header **can lower but never raise** above the key's level (a `metadata` key cannot request `full`) | header `off` on a `full` key → row without content and `trace_level_effective=off`; header `full` on a `metadata` key → `metadata` |
| T4 | The structured row carries every field in [`02`](02-metrics-catalogue.md) marked *gateway* and *phase 1*: identity, request parameters, token counts (incl. `cached_tokens` and `multimodal_tokens` from vLLM), timings (t_auth, t_media, t_upstream_first_byte, ttft, wall), video budget (`duration_s`, `frames`, `mm_processor_kwargs`), `finish_reason`, `status`, `error_type`, `concurrency_at_admission`, cost with `price_table_version` | the row for a streamed video request has all of these non-null |
| T5 | With `full`, the content blob holds the **exact bytes**: the request body as received (video part replaced by a media reference), every response choice as sent to the client, the `reasoning_content` (`<think>` block) that was stripped from the client response, the raw SSE chunk count, and the upstream response `id`. **Never truncated** | sha256 of `content.request.messages` re-serialised equals sha256 of the received body's `messages`; `content.output[0].text` equals the client's concatenated deltas |
| T6 | Media is stored **by reference, never inline**: `sha256` of the source bytes, `mime`, `bytes`, `duration_s`, `frames_sent`, `sample_fps`, `source` (`url` \| `data` \| `upload`), and, when the production-api media stage exists, the transcoded clip's S3 key. Source `data:` URLs are hashed, not copied, unless `media_copy=true` on the org | a 30 MB `data:` clip produces a row and blob under 64 KB; `media.sha256` matches `sha256sum` of the decoded clip |
| T7 | Trace assembly runs in the same `finally:` as the usage row; streaming deltas are accumulated in the generator that already parses each chunk; **no additional `await` on the request path** except `put_nowait` | code review + the hot-path test in [`08`](08-phases-and-test-plan.md) (NF1) |
| T8 | Traces are written to a **local append-only spool** (`traces.jsonl`) before any network, then shipped by a background task in batches: content → S3 by sha256, row → ClickHouse. Ship failures retry with backoff, then park in `traces_failed.jsonl` for `replay_traces.py` | kill ClickHouse for 10 min under load → zero rows lost after replay; kill the gateway mid-batch → no duplicate rows (idempotent by `id`) |
| T9 | `X-Request-Id: <Inference-Id>` is sent to vLLM on every upstream call so vLLM's response `id` becomes `chatcmpl-<Inference-Id>` and its OTLP spans (D5, later) carry the same id [src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/entrypoints/serve/engine/serving.py) | upstream `id` in the trace equals `chatcmpl-` + `Inference-Id` |
| T10 | Optional client context: `X-Infrx-Session-Id`, `X-Infrx-User-Id` (hashed per org before storage), and the OpenAI body field `metadata` (≤16 string pairs, ≤64-char keys, ≤512-char values, same limits as OpenAI's [src](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/update)) are stored on the row and filterable | a request with `metadata: {"route": "qa"}` is found by `metadata.route = 'qa'` in the console |
| T11 | `schema_valid` is computed at ingest when the request had `response_format` of type `json_object` / `json_schema`; tool-call arguments are stored raw **and** parsed | a response that is not valid JSON under `json_object` shows `schema_valid=false` |
| T12 | Legacy `GATEWAY_API_KEY` requests (no org) are traced at `metadata` level into the spool only, org `null`, never shipped to ClickHouse — same rule as `usage_events` today | — |

### 4.2 Feedback (scores)

| id | requirement | acceptance |
|---|---|---|
| F1 | `POST /v1/feedback` on the gateway: `{trace_id, name, value, comment?, expected?, source?}` where `value` is numeric, boolean, or a categorical label; authenticated with the org's API key; the trace must belong to the org | 201 on own trace; 404 on another org's trace; 400 on unknown `name` type |
| F2 | Scores are **append-only** rows in a separate table keyed by `trace_id` (a trace is immutable; later signals never mutate it) | posting twice yields two rows, latest wins in the viewer |
| F3 | The console's trace detail view has thumbs up/down, a 1–5 rating, and a free-text correction (`expected`) that write through the same path (server action → same table) | a console thumb appears in `GET /v1/feedback?trace_id=` |
| F4 | Scores from three `source`s are distinguishable: `user` (API), `operator` (console), `judge` (J-rows) | the viewer shows them in separate columns |

### 4.3 LLM-as-judge

| id | requirement | acceptance |
|---|---|---|
| J1 | A scheduled job selects traces not yet judged from orgs/keys with `judge_enabled=true` and `trace_level=full`, by policy: **100 %** of `finish_reason=length`, `schema_valid=false`, and traces with any `user` score are judged; `status>=500` traces (no answer to judge) are **selected and recorded** as `judge_items.status='skipped_no_output'` so failure rates are visible in the same table; **N %** uniform sample of the rest (`judge_sample_pct`, default 5) | a run's selection matches the policy exactly on a synthetic day of traffic |
| J2 | Judging is **asynchronous and batched** via the Anthropic Message Batches API (results by `custom_id = trace_id`, 50 % price, ≤24 h) [src](https://platform.claude.com/docs/en/build-with-claude/batch-processing); never on the request path, never on the GPU box | the judge never calls `/v1/messages` synchronously; the gateway process has no Anthropic dependency |
| J3 | The judge sees: system prompt + user text, **up to K sampled frames** of the clip as images (K default 8, from the transcoded clip or decoded from the source), the model's output, and a rubric; it returns **structured JSON** with per-criterion scores (relevance to the question, groundedness in the clip, completeness, format validity, refusal/abstention) each 1–5 with a one-sentence rationale, plus an overall `pass` boolean | every judge row validates against the JSON schema; `parsed_output` never null |
| J4 | Rubrics are versioned per org (`rubric_id`, `rubric_version`) and the judge model id is stored on every score; changing either starts a new score series, never overwrites | two rubric versions produce distinguishable rows |
| J5 | Egress gate: a trace leaves the tenant boundary only if `key.judge_enabled` **and** `org.judge_egress_ok` **and** `trace_level_effective=full`; every egress is logged (`judge_runs` table with trace ids, model, cost) | a trace on a key with `judge_enabled=false` is never in any batch |
| J6 | Cost is bounded: per-org daily judge budget in USD (`judge_daily_budget_usd`); the selector stops at the budget using the estimate `tokens_in × $5/1M + tokens_out × $25/1M × 0.5` (Opus 5 batch) and stores actual `usage` from the result | a $1 budget yields ≤ the number of traces the estimate allows; actual spend recorded |
| J7 | Judge model default `claude-opus-5` with adaptive thinking and structured outputs; the id is config, not code | config change switches models without a deploy of the gateway |

### 4.4 Console

| id | requirement | acceptance |
|---|---|---|
| C1 | **Traces** page in the sidebar: table of the org's traces (time, key, status, model, video s, prompt/output tokens, TTFT, latency, finish reason, score badges), time range + key filter (reuse Usage's controls), plus status / has-video / finish_reason / min-latency / has-score filters; cursor pagination by `(ts, id)` | 10k-row org lists in < 1 s p95 from ClickHouse |
| C2 | Trace detail `/traces/[id]`: request parameters, timing waterfall (auth → media → queue → first byte → done), token breakdown (text/multimodal/cached/reasoning), the prompt (rendered messages), the clip metadata with a frame strip if available, the output, the stripped `<think>` block **clearly labelled as not sent to the client**, all scores, feedback controls (F3), a "copy as cURL" replay | a `metadata`-level trace renders everything except content with a "content not captured for this key" notice |
| C3 | API Keys page: `trace_level` selector and `judge` toggle per key; owners only; the change takes effect on the gateway within the auth cache TTL (60 s) | toggle → next request after 60 s reflects it |
| C4 | Org settings (owner): `trace_retention_days` (content), `judge_daily_budget_usd`, `judge_sample_pct`, rubric text (`judge_rubric`), `judge_egress_ok` acknowledgement checkbox with the plain-language statement of what leaves | persisted; enforced by the gateway/judge |
| C5 | Operators see all orgs (org selector) and a Judge tab: runs, cost per day, failure counts | — |
| C6 | Docs page gains a "Traces" section: what is captured at each level, the headers, the feedback endpoint, retention, and the judge egress statement | — |
| C7 | Tenancy is enforced **server-side**: every ClickHouse query carries `org_id = {session org}` bound as a parameter; content blobs are fetched server-side only after the row check (the browser never reads S3 content); judge frames use 60-second presigned URLs minted only after the row check | a crafted request for another org's trace id gets 404 |

### 4.5 Operations

| id | requirement | acceptance |
|---|---|---|
| O1 | ClickHouse and the judge run on one small EC2 (`infrx-obs`), docker compose, pinned image digests, on an EBS volume; nightly `BACKUP` to S3 with a weekly full + daily incremental [src](https://clickhouse.com/docs/operations/backup) | restore drill into a fresh container passes |
| O2 | Retention: rows **13 months** (drift baselines need >12), content blobs **90 days** default (per-org `trace_retention_days`, 7–365), judge frames **30 days**, `traces_failed.jsonl` until replayed; TTL on the ClickHouse table, S3 lifecycle on prefixes | expired rows/objects are gone within 48 h of the deadline |
| O3 | Tenant deletion: `DELETE` by `org_id` on all tables + S3 prefix delete, with an audit row; completes within 24 h | drill on a test org |
| O4 | The shipper exports Prometheus counters on the gateway `/metrics` (production-api Phase 3) or, before that, in `usage.jsonl`-style JSON log lines: spooled, shipped, failed, parked, batch latency | `infrx_traces_*` visible |
| O5 | Secrets: ClickHouse password and Anthropic key in SSM/Secrets Manager, never in the repo; ClickHouse HTTP port reachable only from the gateway SG and the console's egress (Vercel → **via an authenticated HTTPS reverse proxy with a static allowlist token**, since Vercel has no fixed egress IP by default) | port scan from the internet: closed |

## 5. Non-functional requirements

| id | requirement | how it is verified |
|---|---|---|
| NF1 | **Hot path**: tracing adds **≤ 1 ms p50 and ≤ 3 ms p99** to `wall_s` and **0 ms** to TTFT at concurrency 8, measured A/B (`trace_level=off` vs `full`) with `bench.py` on distinct clips. Budget reasoning: assembling a ~20 KB dict and `put_nowait` is microseconds; the file append is the only I/O and is ~50 µs on NVMe | [`08`](08-phases-and-test-plan.md) H1 |
| NF2 | **Durability**: no trace is lost if ClickHouse or S3 are unreachable for ≤ 24 h or the gateway restarts; the spool is the record, the DB is a copy | H2/H3 |
| NF3 | **Exactness**: content is byte-exact and never truncated; a row's `content_bytes` equals the blob's size | T5 |
| NF4 | **Tenancy**: `org_id` is a partition/sort key in every table and every query; there is no cross-org read path | C7, O3 |
| NF5 | **Privacy**: content only with `full`; `<think>` and user ids are labelled; judge egress is a separate consent; deletion is a tenant-scoped operation; **no PII detection in v1** (documented as a known gap; Presidio is the named follow-up per [`../platform/01`](../platform/01-observability-and-tracing.md) §3.6) | docs + O3 |
| NF6 | **Idempotency**: rows keyed by `Inference-Id`; a replay never duplicates (ReplacingMergeTree + `FINAL` / `argMax` on read) | H3 |
| NF7 | **Volume**: sized for **1M traces/month** at pilot with headroom to 50M (the [`../platform/01`](../platform/01-observability-and-tracing.md) §7 workload); the row schema is the same at both scales | [`03`](03-architecture.md) §7 |
| NF8 | **Cost**: infra ≤ **$150/month** at pilot (one EC2 + EBS + S3 + backups); judge cost bounded per org by J6; the estimate at 1M req/mo, 5 % judged, is in [`06`](06-feedback-and-judge-spec.md) | [`03`](03-architecture.md) §7 |
| NF9 | **Compatibility with the production-api refactor**: the capture seam is the one `usage.py` will own (one record per job id); `trace_level` lives on the same `api_keys` row the auth cache already fetches, so no extra lookup | [`05`](05-gateway-capture-spec.md) §1 |
| NF10 | **No new hot-path dependency**: the gateway gains no new Python package for capture (httpx + stdlib); the ClickHouse and Anthropic clients live in the shipper/judge only | requirements file diff |
| NF11 | **Observability of the observer**: every drop, spill, replay and judge failure is counted and visible | O4 |

## 6. Constraints from the systems that already exist

- **`usage_events` stays.** It is the billing/console-tiles contract and Supabase
  is the console's database. Traces do not replace it; the trace row carries a
  superset and the two are joinable by `id`. ⚠️ Later the Usage page may read
  ClickHouse instead — not in scope.
- **`Inference-Id` is the trace id.** Today a `uuid4`; after production-api
  Phase 2 it is the uuid half of the job id ([`../production-api/10-implementation-spec.md`](../production-api/10-implementation-spec.md) §2). Both are 128-bit; the ClickHouse column is `UUID`.
- **Auth cache shape.** `authenticate()` fetches `id, org_id, revoked_at`; it
  gains `trace_level, judge_enabled` in the same `select`, so opt-in costs no
  request-path I/O.
- **vLLM flags.** `--enable-prompt-tokens-details` must be on for
  `prompt_tokens_details.{cached_tokens, multimodal_tokens}` [src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/entrypoints/openai/chat_completion/serving.py); `--enable-request-id-headers` for the echoed `X-Request-Id` [src](https://docs.vllm.ai/en/latest/cli/serve/). Both are `serve.sh` changes; neither is on today. ⚠️ **TO BE VERIFIED on the pinned nightly** that `multimodal_tokens` is populated for video (the source gates it on `mm_token_counts` being non-empty).
- **The `<think>` strip.** Capture happens *before* `THINK.sub`, in the same
  code path, so the trace holds what the model produced and the client gets
  what it gets today. No behaviour change on the wire.
- **Media stage.** Before production-api Phase 1, the gateway holds the source
  bytes in memory (`prepare_video`) — the sha256 is computed there. After Phase
  1, `MediaRef.sha256` and the S3 key are already computed; the trace copies them.
- **Vercel → ClickHouse.** The console runs on Vercel; ClickHouse must not be
  public. O5's proxy (Caddy with a bearer token, on the `infrx-obs` box, TLS via
  ACM/Let's Encrypt) is the v1 answer; a VPC-peered path is the later one.

## 7. Out of scope for v1 (named so they are not accidentally built)

- Dataset export to Parquet/Iceberg, annotation queues, training (S3–S5).
- PII detection/redaction (Presidio) — NF5 records the gap.
- Engine-side per-request breakdown (`queue/prefill/decode`) — D5, after the
  production-api Phase 3 collector; the join key (T9) is laid now.
- Embeddings, clustering, drift monitors ([`../platform/01`](../platform/01-observability-and-tracing.md) §4).
- Shadow/A-B traffic, `endpoint_role`/`arm` columns are **present but constant** (`main`).
- Prompt registry / template versioning (`prompt_stack_hash` is computed; the registry is not).
- Response caching (production-api Phase 6) — it will *read* `request_hash` from the trace row.
- Multi-model: the schema has `model_id`; only Marlin is served.

## 8. Success criteria for the whole feature

1. A developer can turn on `full` for a key, make a video request, and within
   **60 s** open that exact request in the console with prompt, clip metadata,
   output, `<think>`, timings and tokens — and the request was not measurably
   slower (NF1).
2. Operators can answer, from the console alone: *what fraction of yesterday's
   requests hit `finish_reason=length`; which clips exceeded 12k multimodal
   tokens; what the judge's median groundedness was, per key.*
3. Killing ClickHouse for an hour loses nothing (NF2).
4. Every byte that leaves the tenant boundary to the judge is traceable to an
   explicit per-key + per-org opt-in and a `judge_runs` row (J5).

## 9. Open questions

1. ⚠️ **Frame source for the judge before Phase 1.** Without the transcoded
   clip in S3, frames must be decoded from the source again (`ffmpeg` on the
   obs box from a `media_copy`) or the judge runs text-only for `url` sources
   we did not copy. Proposal: `media_copy=true` for judge-enabled keys only,
   capped at `MAX_VIDEO_MB`; revisit when Phase 1 lands. *Owner: [`06`](06-feedback-and-judge-spec.md).*
2. ⚠️ **Does `multimodal_tokens` count video on the pinned nightly?** Verify
   with one request; fall back to `prompt_tokens − text_tokens_est` if not.
3. ⚠️ **Vercel egress to ClickHouse**: token-authenticated proxy (v1) vs
   Vercel Secure Compute / static IPs (paid). Decide at Phase 4 of [`08`](08-phases-and-test-plan.md).
4. ⚠️ **Retention default (90 d content)** is a product choice, not derived;
   the storage arithmetic in [`03`](03-architecture.md) §7 shows it is not a
   cost question at pilot scale.
5. ⚠️ **Judge rubric wording** for video captioning/QA is a first draft in
   [`06`](06-feedback-and-judge-spec.md) and must be validated against ~50
   human-labelled traces before its scores are trusted ([`../platform/04-evals-and-ab-testing.md`](../platform/04-evals-and-ab-testing.md) on judge validity).

---

## Verification log

- 2026-09-20 — written against `main` at `5210c67` (HANDOFF.md present; gateway.py 500 lines). All `[src]` URLs were fetched during the same session; vLLM `X-Request-Id` behaviour read from `vllm/entrypoints/serve/engine/serving.py` (`_base_request_id`) and `vllm/entrypoints/serve/middleware/x_request_id.py`; `prompt_tokens_details` gating read from `vllm/entrypoints/openai/chat_completion/serving.py` (`_make_prompt_tokens_details(enable_prompt_tokens_details, num_cached_tokens, num_cache_creation_tokens, mm_token_counts)`). Anthropic Batches limits (100k requests / 256 MB, ≤24 h, 50 %) from the bundled SDK reference. OpenAI `metadata` limits (16 pairs, 64/512 chars) from the OpenAI reference. Nothing here is measured yet; NF1's budget is a target, not a result.

### Implementation-plan amendment log — 2026-09-20

Documentation reconciliation only: incorporated the unified plan and review corrections above. Historical measurements and previous verification entries remain unchanged; new implementation/live tests are pending.
