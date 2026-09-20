# Deep traces — metrics and field catalogue

> **Implementation amendment — 2026-09-20.** The current implementation authority is [the unified plan](../plan/README.md), especially [contracts](../plan/01-contracts.md), [durable protocols](../plan/02-durable-protocols.md) and [verification](../plan/04-verification.md). The text below is historical research where it conflicts. Trace loss does not stop inference; capture has active-byte, queue, spool and disk limits, and local durability begins at fsync on persistent storage. Off-mode requests have no CH trace row and are excluded from trace-coverage denominators. Canonical logical content is preserved, not raw HTTP wire bytes after normalization. Feedback 201 requires PG/outbox durability and tenant ownership independent of CH lag. Channel and author role are separate; customer console feedback is not an operator calibration label. Judge requires current consent, hard worst-case budget reservations and ambiguous-submit quarantine. CH schema/version/dedup must run against the pinned server; logical expiry is enforced before physical TTL deletion. Retention: 24h results, 7d processing cache, <=90d optional full content, 13mo metadata.


Research date **2026-09-20**. The exhaustive, deduplicated list of what can
be recorded per API call, from three sources: the product survey
(LangSmith, Langfuse, OpenTelemetry GenAI semconv, OpenInference/Phoenix,
Helicone, Braintrust, W&B Weave, Datadog, Portkey, OpenPipe, Baseten, OpenAI
`store`/`metadata`, Anthropic usage API), the platform research
([`../platform/01-observability-and-tracing.md`](../platform/01-observability-and-tracing.md)
§1.2–1.7, §2.5), and what `apps/infrx-api/gateway.py` and vLLM actually
expose. Requirements are [`01-requirements.md`](01-requirements.md); the
columns named here are the ones in [`04-data-model.md`](04-data-model.md).

Legend: [`../METHODOLOGY.md`](../METHODOLOGY.md#legend) — `[src]` primary
source, **⚠️ TO BE VERIFIED** unsourced or unconfirmed, `meas.` measured,
`est.` derived.

---

## 1. How to read the tables

**The vantage rule.** Every field has one cheapest place to observe it, and a
field observed elsewhere is a different number (client-side TTFT includes the
network; engine-side TTFT does not). The `vantage` column names that place:

| Vantage | Sees | Cannot see | Hot-path cost |
|---|---|---|---|
| **gateway** | exact bytes on the wire, params, wall-clock TTFT/latency, tokens, status, key/org, the video budget it computed | in-engine breakdown | ~0 when queued ([`03`](03-architecture.md) §2) |
| **engine** (vLLM) | queue / prefill / decode split, KV-cache %, prefix-cache hits, preemptions | who, why | 0 — `/metrics` is aggregate; per-request only via `--otlp-traces-endpoint` |
| **client** | user-perceived latency, user id, session, outcome | our internals | none (their code); reaches us as headers / `metadata` |
| **post-hoc** | judge scores, schema validity, feedback, dedup | — | 0 by construction |

**Columns.** `field` = our canonical name, which is the `traces` / `scores`
column in [`04`](04-data-model.md) §2 when one exists, a `content.…` path into
the blob of [`04`](04-data-model.md) §3.1, or *derived* (computed at query
time). `standard mapping` = the exact attribute name in OTel GenAI semconv
(`gen_ai.*`) [src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)
or OpenInference (`llm.*`) [src](https://github.com/Arize-ai/openinference/blob/main/spec/semantic_conventions.md)
where one exists. `phase` per [`01`](01-requirements.md) §7 and D5: **P1**
capture, **P2** feedback + judge, **P3** engine spans (after the
production-api Phase 3 collector), **later** = out of v1. `consumer` = the
requirement (T/F/J/C/O) or platform stage (S2–S9,
[`../platform/00-goal-and-problem-statement.md`](../platform/00-goal-and-problem-statement.md))
that needs it; a field with no consumer is not stored.

## 2. Master table

### 2.1 Identity and context

| field | type / unit | vantage | standard mapping | products | phase | consumer |
|---|---|---|---|---|---|---|
| `id` | UUID = `Inference-Id` | gateway | `gen_ai.response.id` (ours is the request id; vLLM's is `chatcmpl-<id>`) | LangSmith `id`, Langfuse observation id, Helicone `helicone-id`, Portkey `x-portkey-trace-id` | P1 | T1, everything joins on it |
| `org_id`, `api_key_id` | UUID | gateway (auth row) | — (tenant ids are private in every convention) | Helicone `Helicone-Auth`, Langfuse project | P1 | NF4, C7 |
| `ts` | DateTime64(3) | gateway | span start time | LangSmith `start_time`, Langfuse `startTime`, Weave `started_at` | P1 | every query |
| `ingested_at` | DateTime64(3) | shipper | — | — | P1 | NF6 (Replacing version) |
| `model_id` | string | gateway | `gen_ai.request.model` | all | P1 | T4 |
| `served_model` | string | gateway | `gen_ai.response.model` / `llm.response.model_name` | OTel, OpenInference, Langfuse resolved `model` | P1 | T4 |
| `artifact_id` | string | deploy env | none — [`../platform/01`](../platform/01-observability-and-tracing.md) §1.4 `lfp.artifact.id` | none | P1 | S7–S9 lineage; "which build produced this token" |
| `endpoint_role` | `main` (constant in v1) | gateway | none — `lfp.endpoint.role` | none | P1 (constant) | S9 A/B join key, later |
| `gateway_version` | git sha[:12] | deploy env | `service.version` (resource attr) | Datadog `version` tag | P1 | incident forensics |
| `trace_level` | `metadata` \| `full` (effective) | gateway | none | Portkey `x-portkey-debug`, LiteLLM `x-litellm-enable-message-redaction` | P1 | T2, T3, C2 |
| `session_id` | string, client-supplied | client (`X-Infrx-Session-Id`) | `gen_ai.conversation.id`; OpenInference `session.id` | LangSmith `session_id`, Langfuse `sessionId`, Helicone `Helicone-Session-Id`, Datadog `session_id` | P1 | T10, S9 randomisation unit. Semconv: never synthesise it [src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md) |
| `user_hash` | sha256(org_salt ‖ user id) | client (`X-Infrx-User-Id`) | OpenInference `user.id`; OpenAI `user` / `metadata` | Langfuse `userId`, Helicone `Helicone-User-Id`, Portkey `_user` | P1 | T10, S4 split-by-entity |
| `metadata` | Map(String,String), ≤16 pairs, keys ≤64, values ≤512 | client (body `metadata`) | `gen_ai.*` has none; OpenInference `metadata` (JSON string) | OpenAI `metadata` (16 pairs, `store:true`) [src](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/update), LangSmith `metadata`, Langfuse `metadata`, Helicone `Helicone-Property-*`, Portkey `x-portkey-metadata`, OpenPipe `tags` | P1 | T10, C1 filters |
| `request_id_upstream` | `chatcmpl-<id>` | gateway (response body) | `gen_ai.response.id` | vLLM echoes `X-Request-Id` [src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/entrypoints/serve/engine/serving.py) | P1 | T9, P3 join key |
| `content.request.headers` | allowlisted headers | gateway | none | Helicone stores all `Helicone-*` | P1 (`full`) | forensics |
| trace / parent span ids (W3C `traceparent`) | — | client | `trace_id`, `span_id` | LangSmith `trace_id`/`dotted_order`, Portkey parses `traceparent`, vLLM propagates it | **later** | no consumer until the SDK/agent plane exists |
| span kind / run type | — | client | `openinference.span.kind`, `traceloop.span.kind`, Datadog span kinds | LangSmith `run_type`, Langfuse `type` | **later** | one span per request in v1; no tree |

### 2.2 Request configuration

| field | type / unit | vantage | standard mapping | products | phase | consumer |
|---|---|---|---|---|---|---|
| `stream` | bool | gateway | `gen_ai.request.stream` | all | P1 | T4; `usage_events.stream` |
| `requested_model` | string | gateway (before rewrite) | `gen_ai.request.model` | all | P1 | T4 |
| `params` | JSON string of all sampling params | gateway | OpenInference `llm.invocation_parameters`; Langfuse `modelParameters` | Langfuse, Phoenix | P1 | T4, S5/S7 (doc 00 §2.3: defaults shift scores) |
| `temperature`, `top_p`, `max_tokens`, `seed`, `n` | promoted columns | gateway | `gen_ai.request.temperature` / `.top_p` / `.max_tokens` / `.seed` / `.choice_count` | OTel, Traceloop, Datadog `metadata` | P1 | C1 filters |
| `top_k`, `frequency_penalty`, `presence_penalty`, `stop` | inside `params` | gateway | `gen_ai.request.top_k` / `.frequency_penalty` / `.presence_penalty` / `.stop_sequences` | OTel, Traceloop `llm.chat.stop_sequences` | P1 | S5 |
| `response_format_type` | `text` \| `json_object` \| `json_schema` | gateway | `gen_ai.output.type` (replaces deprecated `gen_ai.openai.request.response_format`) | OTel | P1 | T11 |
| `n_messages`, `n_system`, `n_video`, `n_image`, `n_tools` | counts | gateway | — (derivable from `gen_ai.input.messages`) | — | P1 | C1 filters at `metadata` level |
| `prompt_chars`, `request_bytes` | chars / bytes | gateway | — | Baseten request-size percentiles [src](https://docs.baseten.co/observability/metrics) | P1 | sizing, NF7 |
| `prompt_hash` | sha256 | gateway | none | — | P1 | dedup, response cache (production-api Phase 6 `request_hash`) |
| `prompt_stack_hash` | sha256(system + tools + params) | gateway | none — `lfp.prompt_stack.sha256` | none | P1 | S9: detects the silent prompt change ([`../platform/01`](../platform/01-observability-and-tracing.md) §1.2a) |
| `content.request.tools` | JSON schema array | gateway | `gen_ai.tool.definitions` (Opt-In) | OTel, Datadog `tool` span | P1 (`full`) | S5, S7 |
| prompt template id / version | — | client | `gen_ai.prompt.name` / `.version`; OpenInference `llm.prompt_template.{template,variables,version}` | Langfuse prompt link, Phoenix | **later** | no registry in v1 ([`01`](01-requirements.md) §7) |
| `mm_kwargs` | JSON | gateway | none | none | P1 | Marlin-specific, §3 |

### 2.3 Input content (`full` only)

| field | type / unit | vantage | standard mapping | products | phase | consumer |
|---|---|---|---|---|---|---|
| `content.request.messages[]` | role + parts, exact order, media by reference | gateway | `gen_ai.input.messages` (Opt-In; JSON string in practice); OpenInference `llm.input_messages.<i>.message.{role,content}` | LangSmith `inputs`, Langfuse `input`, Datadog `input_data`, OpenPipe `input` | P1 | T5, S3, S5 — untruncated or useless as a training example |
| system instructions | first `system` message text (inside `messages`) | gateway | `gen_ai.system_instructions` (Opt-In) | OTel | P1 | S3, S5, S7 |
| RAG / retrieval context | — | client | `gen_ai.retrieval.documents`; OpenInference `retrieval.documents` | Phoenix, Datadog `retrieval` span | **later** | no retrieval in the Marlin API |

### 2.4 Media

| field | type / unit | vantage | standard mapping | products | phase | consumer |
|---|---|---|---|---|---|---|
| `media_sha256` | sha256 of source bytes | gateway (media stage, concurrent with ffprobe) | none — semconv external-content reference is a `TODO` [src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md); Langfuse dedups media by sha256 [src](https://langfuse.com/docs/tracing-features/multi-modality) | Langfuse Media | P1 | T6; production-api cache key |
| `media_bytes`, `media_mime` | bytes, MIME | gateway | Langfuse media `content type` | Langfuse | P1 | T6 |
| `media_duration_s` | s | gateway (ffprobe) | none | none | P1 | T4; = `usage_events.video_seconds` |
| `media_frames`, `media_fps` | count, fps | gateway (`budget_kwargs`) | none | none | P1 | §3 — "what the model saw" |
| `media_source` | `url` \| `data` \| `upload` | gateway | OpenInference `input.mime_type` (nearest) | — | P1 | T6 |
| `media_s3_key`, `media_cache_hit` | key, `miss\|l1\|l2\|index` | gateway (Phase 1 media stage) | none | Helicone `Helicone-Cache: HIT\|MISS` (response cache, different thing) | P1 (null until production-api Phase 1) | T6; `usage_events.cached` |
| raw media bytes | — | gateway | — | Langfuse uploads to S3 by default | **only with `org.media_copy`** | J3 frames; [`../platform/01`](../platform/01-observability-and-tracing.md) §7: refs-not-bytes is worth ~$16k/mo at scale |

### 2.5 Output content

| field | type / unit | vantage | standard mapping | products | phase | consumer |
|---|---|---|---|---|---|---|
| `content.response.choices[].content` | exactly what the client received | gateway | `gen_ai.output.messages` (Opt-In); OpenInference `llm.output_messages.<i>.message.content` | LangSmith `outputs`, Langfuse `output`, Datadog `output_data` | P1 (`full`) | T5, S3, S5 |
| `content.response.choices[].reasoning_content` | the stripped `<think>` block | gateway (before `THINK.sub`) | none in semconv (reasoning tokens exist, content does not) | none | P1 (`full`) | S5: highest-value distillation bytes ([`../platform/01`](../platform/01-observability-and-tracing.md) §1.2c); C2 must label it |
| `finish_reasons` | Array(String) | gateway (response) | `gen_ai.response.finish_reasons`; vLLM label `finished_reason` on `vllm:request_success_total` | OTel, vLLM | P1 | T4; J1 (`length` = truncation) |
| `n_choices` | count | gateway | `gen_ai.request.choice.count` | OTel | P1 | T4 |
| `content.response.choices[].tool_calls_raw` / `.tool_calls` | raw string **and** parsed JSON | gateway | `gen_ai.tool.call.{id,arguments}`; OpenInference `message.tool_calls` | OTel | P1 (`full`) | T11 — escaping differences are the bug being hunted |
| `schema_valid` | Nullable(Bool) | post-hoc at ship | none — `lfp.output.schema_valid` | none | P1 | T11, J1; doc 00 §2.3 requires 100 % |
| `output_chars`, `think_chars` | chars | gateway | — | — | P1 | C2 token breakdown; reasoning-token proxy |
| `sse_chunks` | count | gateway (stream) | semconv streaming section is `TODO` | — | P1 | T5 invariant; chunking anomalies |
| `client_aborted` | bool | gateway (generator close) | vLLM `finished_reason="abort"` (engine side) | vLLM | P1 | T4; production-api F22 cancellation |
| `content.response.error` | `{type, code, message, upstream_body?}` | gateway | `error.type` (Stable) | LangSmith `error`, Langfuse `statusMessage` | P1 | T1 |

### 2.6 Timing

All milliseconds from `t0` = request receipt at the gateway.

| field | type / unit | vantage | standard mapping | products | phase | consumer |
|---|---|---|---|---|---|---|
| `t_auth_ms` | ms | gateway | none | — | P1 | production-api NF (auth < 5 ms warm) |
| `t_media_ms` | ms | gateway | none (`Server-Timing: fetch;probe;xcode` in production-api spec 10 §4.1) | — | P1 | the CPU bottleneck ([`../production-api/06-throughput-and-latency-optimization.md`](../production-api/06-throughput-and-latency-optimization.md)) |
| `t_admit_ms` | ms | gateway (Phase 2 queue) | none | — | P1 (= `t_media_ms` until Phase 2) | queue wait; `usage_events.queue_wait_ms` |
| `t_first_byte_ms` | ms | gateway (upstream headers) | — | — | P1 | separates network/queue from generation |
| `ttft_ms` | ms | gateway (first content delta after strip) | `gen_ai.response.time_to_first_chunk` (span); `gen_ai.client.operation.time_to_first_chunk` (metric); Langfuse `completionStartTime` | LangSmith TTFT, OTel, Langfuse | P1 | I3; `usage_events.ttft_ms`. ⚠️ Our TTFT is *after* the `<think>` strip, so a long think block inflates it — `t_first_byte_ms` is the pre-think number |
| `wall_ms` | ms | gateway | `gen_ai.client.operation.duration`; vLLM `vllm:e2e_request_latency_seconds` | all | P1 | `usage_events.latency_ms` |
| `tpot_ms` | derived `(wall − ttft) / (completion_tokens − 1)` | gateway | `gen_ai.client.operation.time_per_output_chunk`; vLLM `vllm:request_time_per_output_token_seconds` (same definition) [src](https://github.com/vllm-project/vllm/blob/main/docs/design/metrics.md) | OTel, vLLM | P1 | I3; console tok/s tile |
| `concurrency_at_admission` | count | gateway (`inflight`) | none | none | P1 | makes p99 comparable across load ([`../platform/01`](../platform/01-observability-and-tracing.md) §1.2e) |
| `queue_ms`, `prefill_ms`, `decode_ms`, `scheduler_ms`, `model_forward_ms`, `model_execute_ms`, `model_inference_ms` | ms, **per request** | engine (OTLP span) | vLLM custom `gen_ai.latency.time_in_queue`, `.time_to_first_token`, `.e2e`, `.time_in_scheduler`, `.time_in_model_{forward,execute,prefill,decode,inference}` [src](https://github.com/vllm-project/vllm/blob/main/vllm/tracing/utils.py); semconv server metrics `gen_ai.server.time_to_first_token`, `.time_per_output_token`, `.request.duration` | vLLM, SGLang (`--enable-trace`) | **P3** | S8; "is the candidate slower or the fleet busier" |
| `retry_count` | — | gateway | semconv: the span covers the logical operation incl. retries | — | **later** | no gateway retries until production-api Phase 2 (`attempts`) |

### 2.7 Tokens and usage

| field | type / unit | vantage | standard mapping | products | phase | consumer |
|---|---|---|---|---|---|---|
| `prompt_tokens` | tokens | engine via `usage` | `gen_ai.usage.input_tokens` (`gen_ai.usage.prompt_tokens` **deprecated**, still what vLLM's OTLP emits); OpenInference `llm.token_count.prompt`; vLLM `vllm:request_prompt_tokens` | all | P1 | I2; `usage_events.prompt_tokens` |
| `completion_tokens` | tokens | engine via `usage` | `gen_ai.usage.output_tokens`; `llm.token_count.completion`; `vllm:request_generation_tokens` | all | P1 | I2 |
| `cached_tokens` | tokens | engine via `usage.prompt_tokens_details.cached_tokens` (needs `--enable-prompt-tokens-details`) [src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/entrypoints/openai/chat_completion/serving.py) | `gen_ai.usage.cache_read.input_tokens`; OpenInference `llm.token_count.prompt_details.cache_read` | OTel, OpenInference, Anthropic usage API | P1 | I2 cost claims; APC effectiveness ([`../production-api/05-caching.md`](../production-api/05-caching.md)) |
| `created_cache_tokens` | tokens | engine (same object) | `gen_ai.usage.cache_creation.input_tokens`; `llm.token_count.prompt_details.cache_write` | OTel, Anthropic | **later** (inside `content.upstream.usage`, not a column) | no consumer until cache billing exists |
| `multimodal_tokens` | tokens | engine (same object, gated on `mm_token_counts`) | `gen_ai.usage.image.input_tokens` is the nearest (semconv splits usage by modality) | OTel (modality split) | P1 | §3: the Path-A/B budget detector. ⚠️ populated-for-video **TO BE VERIFIED** on the pinned nightly |
| reasoning tokens | — | engine | `gen_ai.usage.reasoning.output_tokens`; `llm.token_count.completion_details.reasoning` | OTel, OpenInference, Traceloop | **later** — vLLM does not split them; `think_chars` is the proxy | I2 |
| total tokens | derived | — | `llm.token_count.total` | LangSmith `total_tokens` | derived | — |
| `content.upstream.usage` | raw usage object | gateway | — | — | P1 (`full`) | keeps whatever vLLM adds next |

### 2.8 Cost

| field | type / unit | vantage | standard mapping | products | phase | consumer |
|---|---|---|---|---|---|---|
| `cost_usd` | USD, computed at ship | post-hoc (shipper, same `cost()` as `usage_events`) | OpenInference `llm.cost.{prompt,completion}`; semconv has none | LangSmith `total_cost`, Langfuse `costDetails`, Weave `summary.weave.costs`, Datadog `total_cost` | P1 | I2; `usage_events.cost_usd` |
| `input_usd_per_m`, `output_usd_per_m`, `price_table_version` | prices used | shipper | none — `lfp.cost.price_table_version` | none | P1 | history never rewrites on a price change ([`../platform/01`](../platform/01-observability-and-tracing.md) §1.2d) |
| org-level cost report | USD by day/key | post-hoc query | Anthropic Admin `cost_report` shape [src](https://docs.anthropic.com/en/api/usage-cost-api) | Anthropic, OpenAI dashboards | derived | C5 (judge cost), Usage page |

### 2.9 Engine and fleet (aggregate, not per request)

Scraped from vLLM `/metrics` by the production-api Phase 3 Prometheus agent;
joined to traces only by time window. Full list in §4.

### 2.10 Errors

| field | type / unit | vantage | standard mapping | products | phase | consumer |
|---|---|---|---|---|---|---|
| `status` | HTTP | gateway | `http.response.status_code` | all | P1 | T1; `usage_events.status` |
| `error_type` | `auth\|capacity\|invalid_request\|media\|upstream\|timeout\|client_abort` | gateway | `error.type` (Stable) | OTel; Langfuse `level=ERROR` | P1 | T1, J1 |
| `error_code` | e.g. `blocked-address`, `queue_full` | gateway | production-api spec 10 §4 `code` | — | P1 | support |
| `error_message` | text | gateway | Langfuse `statusMessage` | Langfuse, LangSmith `error` | P1 | support |
| level / severity | — | — | Langfuse `level` DEBUG/DEFAULT/WARNING/ERROR | Langfuse | **not stored** — derivable from `status` | — |

### 2.11 Quality and feedback signals (→ `scores`)

| field | type / unit | vantage | standard mapping | products | phase | consumer |
|---|---|---|---|---|---|---|
| `scores.name` = `thumb` | `kind=boolean` | client / console | `gen_ai.evaluation.name` + `.score.label`; LangSmith feedback `{key, score}`; Langfuse `dataType=BOOLEAN` | LangSmith, Langfuse, Braintrust `scores`, Helicone `logFeedback`, Datadog `@feedback` | P2 | F1, F3 |
| `rating` | `kind=numeric` 1–5 | client / console | `gen_ai.evaluation.score.value`; Langfuse `NUMERIC` | same | P2 | F1 |
| `correction` | `kind=text` in `value_text` | client / console | Braintrust `expected` [src](https://www.braintrust.dev/docs/guides/logs/score) | Braintrust, LangSmith `reference_example_id` | P2 | F1; the future SFT target |
| `comment` | text | client / console | Langfuse `comment` | Langfuse, Braintrust | P2 | F1 |
| judge criteria: `relevance`, `groundedness`, `completeness`, `format_validity`, `refusal`, `overall_pass` | `kind=numeric` 1–5 / `boolean`, + `rationale` | post-hoc (judge) | `gen_ai.evaluation.result` event: `gen_ai.evaluation.{name,score.value,score.label,explanation}` [src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-events.md) | LangSmith online evaluators, Langfuse LLM-as-judge, Weave Monitors, Datadog `@evaluation` | P2 | J3 |
| `judge_model`, `rubric_id`, `rubric_version`, `judge_run_id` | provenance | judge | — | Langfuse `configId`, LangSmith evaluator id | P2 | J4 |
| `source` | `user\|operator\|judge` | writer | Langfuse `source` | Langfuse | P2 | F4 |
| implicit: regenerate, edit distance, abandonment, copy | derived | client app / post-hoc | none | practitioner pattern; not a vendor field | **later** | S3 sampling ([`../platform/01`](../platform/01-observability-and-tracing.md) §1.2g) |
| guardrail result | pass/fail | inline | OpenInference `GUARDRAIL` span; Weave Guardrails | Phoenix, Weave | **later** | no guardrails in the Marlin API |
| downstream outcome | webhook | client | none | none | **later** | strongest label; needs a customer webhook |

### 2.12 Privacy and sampling controls

| field | type / unit | vantage | standard mapping | products | phase | consumer |
|---|---|---|---|---|---|---|
| `trace_level` (effective) | see 2.1 | gateway | semconv content attributes are Opt-In by spec | Portkey `x-portkey-debug: false`, LiteLLM `turn_off_message_logging` / `x-litellm-enable-message-redaction` | P1 | T2, T3 |
| `redaction_status` | `raw` (constant in v1) | (future) redaction job | Collector redaction processor (Beta) | Datadog auto-scan, Kong AI Sanitizer, Presidio | P1 column, **later** job | NF5 |
| `content_ref`, `content_bytes`, `content_sha256` | S3 key, bytes, sha256 | shipper | the semconv "store content externally, record references" pattern [src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md) | Langfuse S3 media | P1 | T5, C2 |
| org `trace_retention_days`, S3 `ttl` tag | days | config | Langfuse retention, Braintrust retention $/GB | — | P1 | O2 |
| judge egress consent (`judge_enabled` ∧ `judge_egress_ok`) | bool | config | none — `lfp.consent.teacher_egress` | none | P2 | J5, D8 |
| sampling of **content** | `judge_sample_pct`; rows are never sampled | config | Datadog `DD_LLMOBS_SAMPLE_RATE`; OTel head/tail sampling | Datadog, MLflow sampling ratio | P2 | J1; [`../platform/01`](../platform/01-observability-and-tracing.md) §6.2 "sample content, never rows" |

**Row count:** 2.1 (17) + 2.2 (14) + 2.3 (3) + 2.4 (7) + 2.5 (10) + 2.6 (10) + 2.7 (8) + 2.8 (3) + 2.10 (5) + 2.11 (10) + 2.12 (6) = **93 rows**.

## 3. Fields no vendor has, and why each earns its place

| field | why it exists here |
|---|---|
| `media_frames`, `media_fps`, `mm_kwargs` | Marlin's 240-frame cap bounds every request at ~23,560 prefill tokens; a 10-minute clip at 0.40 fps and an hour at 0.067 fps cost the same ([`../../models/marlin2b/README.md`](../../models/marlin2b/README.md) §9). The model's input is the frame stack, not the clip; a dataset item built from "duration" is not reproducible. |
| `multimodal_tokens` | The gateway sets the training grid; the processor default costs 6× the tokens ([`../../models/marlin2b/results/notes.md`](../../models/marlin2b/results/notes.md)). `multimodal_tokens` per request is the only cheap detector for a deployment that silently landed on the wrong budget — the 1.917× Path-A/B fork [`../platform/01`](../platform/01-observability-and-tracing.md) §1.6 flags. |
| `think_chars` / `reasoning_content` | The gateway strips `<think>` before the client sees it; without capture the highest-value distillation bytes are lost on the wire. Also the explanation for "why is TTFT after strip 2 s when first byte was 200 ms". |
| `prompt_stack_hash` | A customer editing the system prompt is a silent distribution shift that voids every parity claim (doc 00 §2.2). One hash, one alert. |
| `artifact_id` | Gate-twice (doc 00 §1.2): the quantised, engine-pinned build that produced a token is a training-and-eval fact. No convention carries it; vLLM's `served_model` is a name, not a digest. |
| `concurrency_at_admission` | p99 TTFT at c=1 (0.77 s) and c=8 (3.35 s) differ 4.4× on the same model (`meas.`, HANDOFF §4). Without the concurrency at admission, latency regressions and load are indistinguishable. |
| `media_cache_hit` | The media stage is the bottleneck ([`../production-api/06`](../production-api/06-throughput-and-latency-optimization.md)); hit rate in real traffic decides whether Phase 1 is a 2× or a 5× and is unknown. |
| `t_first_byte_ms` vs `ttft_ms` | Two TTFTs: before and after the think block. The console tile uses the second; the engine sees the first. |

## 4. Engine-side signals (Phase 3, aggregate unless noted)

### 4.1 vLLM Prometheus `/metrics` (aggregate; scraped, not per request)

Names verified against vLLM's metrics design doc in
[`../production-api/03-request-handling-and-queueing.md`](../production-api/03-request-handling-and-queueing.md)
(verification log item 11) and [`../production-api/07-reliability-observability-operations.md`](../production-api/07-reliability-observability-operations.md) §1.2
[src](https://docs.vllm.ai/en/latest/usage/metrics.html). Deprecated metrics
disappear one minor version after deprecation; `serve.sh` pins `nightly`, so
**re-read `/metrics` on every image pull**.

| metric | type | answers |
|---|---|---|
| `vllm:num_requests_running`, `vllm:num_requests_waiting` | gauge | scheduler state; the autoscaling secondary signal |
| `vllm:kv_cache_usage_perc` | gauge 0–1 | KV pressure; production-api spec 10 safety valve at 0.95 |
| `vllm:request_queue_time_seconds` | histogram | engine-side queue wait |
| `vllm:time_to_first_token_seconds` | histogram | engine TTFT (no network, no gateway media stage) |
| `vllm:request_time_per_output_token_seconds` | histogram | TPOT, `(e2e − TTFT)/(output − 1)` |
| `vllm:e2e_request_latency_seconds` | histogram | engine e2e |
| `vllm:request_prefill_time_seconds`, `vllm:request_decode_time_seconds` | histogram | phase split — ⚠️ named in the survey; **TO BE VERIFIED** on the pinned nightly |
| `vllm:request_prompt_tokens`, `vllm:request_generation_tokens` | histogram | token distributions |
| `vllm:prompt_tokens_total`, `vllm:generation_tokens_total`, `vllm:iteration_tokens_total` | counter | throughput; `marlin:decode_batch_occupancy` recording rule |
| `vllm:request_success_total{finished_reason}` | counter | `stop` / `length` / `abort` rates |
| `vllm:prefix_cache_queries`, `vllm:prefix_cache_hits` | counter | APC hit rate (~0 % on video, [`../production-api/05`](../production-api/05-caching.md)) |
| `vllm:mm_cache_queries`, `vllm:mm_cache_hits` | counter | multimodal processor cache; B0.1's "clips are distinct" check |
| `vllm:num_preemptions` | counter | KV eviction under load (name per `07` §1.2; the survey found no `_total` form ⚠️) |
| `vllm:corrupted_requests` | counter | engine-side failures |
| `vllm:cache_config_info` | gauge, labels only | `block_size`, `cache_dtype`, `enable_prefix_caching`, … (not `max_num_seqs`) |
| `vllm:spec_decode_*` | gauge/counter | not used (no speculative decoding on Marlin) |

### 4.2 vLLM OTLP span attributes (per request, Phase 3)

`--otlp-traces-endpoint`, gRPC default, W3C `traceparent` propagated
[src](https://github.com/vllm-project/vllm/blob/main/examples/observability/opentelemetry/README.md).
From `vllm/tracing/utils.py` [src](https://github.com/vllm-project/vllm/blob/main/vllm/tracing/utils.py):
`gen_ai.request.id` (= our `Inference-Id` via `X-Request-Id`, T9),
`gen_ai.request.n`, `gen_ai.request.{max_tokens,top_p,temperature}`,
`gen_ai.response.model`, `gen_ai.usage.prompt_tokens` /
`gen_ai.usage.completion_tokens` (**deprecated names** — normalise to
`input_tokens`/`output_tokens` at the collector), `gen_ai.usage.num_sequences`,
`gen_ai.latency.time_in_queue`, `gen_ai.latency.time_to_first_token`,
`gen_ai.latency.e2e`, `gen_ai.latency.time_in_scheduler`,
`gen_ai.latency.time_in_model_forward`, `…_model_execute`,
`…_model_prefill`, `…_model_decode`, `…_model_inference`. These land in a
future `engine_spans` table keyed by `gen_ai.request.id`
([`03`](03-architecture.md) §10.1). SGLang equivalents (`--enable-trace`,
levels 0–3) in [`../platform/01`](../platform/01-observability-and-tracing.md) §2.5.

## 5. Quality-signal taxonomy → `scores`

| signal | strength | arrives | `name` | `kind` | `source` | phase |
|---|---|---|---|---|---|---|
| thumbs up/down | weak, biased to extremes | seconds–minutes | `thumb` | boolean | user / operator | P2 |
| 1–5 rating | weak–medium | seconds–minutes | `rating` | numeric | user / operator | P2 |
| correction (expected answer) | **strong** | minutes–hours | `correction` | text | user / operator | P2 |
| free comment | context | — | `comment` (or the `comment` column on any row) | text | user / operator | P2 |
| judge relevance / groundedness / completeness / format_validity / refusal | medium, validated separately | ≤ 24 h (batch) | criterion name | numeric 1–5 | judge | P2 |
| judge overall pass | medium | ≤ 24 h | `overall_pass` | boolean | judge | P2 |
| `schema_valid = false` | **perfect precision** | at ship | (column on `traces`, not a score) | — | — | P1 |
| `finish_reason = length` | high | at ship | (column) | — | — | P1 |
| regenerate / rephrase in-session | strong negative | seconds | `regenerate` | boolean | derived | later |
| edit distance draft→shipped | **strong** | minutes–hours | `edit_distance` | numeric | client app | later |
| abandonment | medium negative | minutes | `abandon` | boolean | derived | later |
| escalation to human/incumbent | **strong negative** | minutes | `escalation` | boolean | client | later |
| downstream outcome | **strongest** | hours–weeks | `outcome:<kind>` | boolean/numeric | client webhook | later |

RealHumanEval's finding that human preference does not track task performance
[src](https://arxiv.org/abs/2404.02806) is why the table ranks outcome and
correction above thumbs; ~97 % of interactions carry no explicit feedback
(survey, practitioner write-ups ⚠️ unsourced figure), which is why the judge
exists.

## 6. Per-product data models (compact)

**LangSmith** — Run is the unit: `id`, `trace_id`, `dotted_order` (`{timestamp}{uuid}` chain), `run_type`, `inputs`, `outputs`, `error`, `start_time`/`end_time`, `tags`, `metadata` (via `extra`), `parent_run_id`, `session_id`, `reference_example_id`, `feedback_stats`; tokens/cost (`total_tokens`, `prompt_tokens`, `completion_tokens`, `total_cost`, `prompt_cost`, `completion_cost`) in `run.extra['usage']`; `wrap_openai()` sets `ls_provider`/`ls_model_name` (both needed for auto-cost). Feedback `{key, score}`; online evaluators run by automation rules and auto-extend retention. [src](https://docs.langchain.com/langsmith/observability-concepts), [src](https://reference.langchain.com/python/langsmith/schemas/Run), [src](https://docs.langchain.com/langsmith/rules), [src](https://docs.langchain.com/langsmith/online-evaluations-llm-as-judge)

**Langfuse** — Trace (`sessionId`, `userId`, `tags`, `metadata`, `environment`) → Observations of `type` SPAN/GENERATION/EVENT; a generation has `model`, `modelParameters`, `input`, `output`, `usageDetails` (keys must match the model's usage types), `costDetails` (derived), `level`, `statusMessage`, `completionStartTime` (→ TTFT), prompt link. Scores: `dataType` NUMERIC/CATEGORICAL/BOOLEAN/TEXT, `value`, `comment`, `source`, `configId`, attachable to trace/observation/session. Client-side masking hooks (`mask_otel_spans`, JS `mask`). [src](https://langfuse.com/docs/observability/data-model), [src](https://langfuse.com/docs/observability/features/token-and-cost-tracking), [src](https://langfuse.com/docs/evaluation/scores/overview), [src](https://langfuse.com/docs/observability/features/masking)

**OpenTelemetry GenAI semconv** (own repo since 2026-05, status Development) — spans: `gen_ai.provider.name`, `gen_ai.operation.name`, `gen_ai.request.{model,max_tokens,temperature,top_k,top_p,frequency_penalty,presence_penalty,seed,stop_sequences,stream,choice_count}`, `gen_ai.response.{id,model,finish_reasons,time_to_first_chunk}`, `gen_ai.usage.{input_tokens,output_tokens,cache_creation.input_tokens,cache_read.input_tokens,reasoning.output_tokens}` (+ per-modality), `gen_ai.conversation.id`, `gen_ai.tool.*`, `gen_ai.evaluation.*`; Opt-In content `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions`, `gen_ai.tool.definitions`. Metrics: `gen_ai.client.token.usage`, `gen_ai.client.operation.{duration,time_to_first_chunk,time_per_output_chunk}`, `gen_ai.server.{request.duration,time_to_first_token,time_per_output_token}`. Deprecated: `gen_ai.prompt`, `gen_ai.completion`, `gen_ai.system`, `gen_ai.usage.{prompt,completion}_tokens`. [src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md), [src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md), [src](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)

**OpenLLMetry / Traceloop** — semconv plus `traceloop.span.kind` (workflow/task/agent/tool), `traceloop.workflow.name`, `traceloop.entity.name`, `traceloop.association.properties`; still emits deprecated `gen_ai.prompt`/`gen_ai.completion` in some versions. [src](https://www.traceloop.com/docs/openllmetry/contributing/semantic-conventions), [src](https://github.com/traceloop/openllmetry/issues/3515)

**Arize Phoenix / OpenInference** — required `openinference.span.kind` ∈ {LLM, EMBEDDING, CHAIN, RETRIEVER, RERANKER, TOOL, AGENT, GUARDRAIL, EVALUATOR, PROMPT}; `input.value`/`output.value` + `*.mime_type`; `llm.model_name`, `llm.system`, `llm.provider`, `llm.invocation_parameters`, `llm.input_messages.<i>.message.{role,content}`, `llm.output_messages.<i>.message.{role,content,tool_calls}`, `llm.token_count.{prompt,completion,total,prompt_details.cache_read,prompt_details.cache_write,completion_details.reasoning,completion_details.audio}`, `llm.cost.{prompt,completion}`, `llm.prompt_template.{template,variables,version}`, `session.id`, `user.id`, `tag.tags`, `metadata`. [src](https://github.com/Arize-ai/openinference/blob/main/spec/semantic_conventions.md), [src](https://arize.com/docs/phoenix/tracing/concepts-tracing/otel-openinference/semantic-conventions)

**Helicone** — header-driven proxy: `Helicone-Auth`, `Helicone-User-Id`, `Helicone-Session-Id`, `Helicone-Property-*`, `Helicone-Cache-Enabled` (+ `Helicone-Cache: HIT|MISS`), `Helicone-Retry`, `Helicone-RateLimit-Policy: [quota];w=[s];u=[request|cents];s=[user|property|global]`; response `helicone-id` for `logFeedback(id, rating)`. Ingest: Worker → raw bodies to S3 → Kafka → ClickHouse (`VersionedCollapsingMergeTree`). [src](https://docs.helicone.ai/helicone-headers/header-directory), [src](https://docs.helicone.ai/features/advanced-usage/custom-rate-limits), [src](https://upstash.com/blog/implementing-upstash-kafka-with-cloudflare-workers)

**Braintrust** — span: `input`, `output`, `expected`, `scores` (string→number), `metadata`, `metrics`; feedback = score, `expected` correction, `comment`. [src](https://www.braintrust.dev/docs/guides/logs/write), [src](https://www.braintrust.dev/docs/guides/logs/score)

**W&B Weave** — Call: `id`, `trace_id`, `parent_id`, `started_at`/`ended_at`, `attributes`, `inputs`, `output`, `summary` (incl. `summary["weave"]["costs"]` from a price table, custom model costs supported); Scorers as Guardrails (inline) or Monitors (async, sampled). [src](https://docs.wandb.ai/weave/guides/tracking/call-schema-reference), [src](https://docs.wandb.ai/weave/guides/evaluation/guardrails_and_monitors)

**Datadog LLM Observability** — span kinds llm/workflow/agent/tool/task/embedding/retrieval; `input_data`/`output_data`, `metadata`, `metrics` (tokens incl. cache variants, `input_cost`/`output_cost`/`total_cost`), `tags`, `session_id`, `ml_app`; `@evaluation`, `@feedback` at query time; `DD_LLMOBS_SAMPLE_RATE`. [src](https://docs.datadoghq.com/llm_observability/instrument/sdk/), [src](https://docs.datadoghq.com/llm_observability/monitoring/querying/)

**Portkey** — `x-portkey-trace-id` (or W3C `traceparent`), `x-portkey-parent-span-id`, `x-portkey-metadata` (`_user`), `baggage` merged, `x-portkey-debug: false` opts a request out of logging; self-declared 20–40 ms added latency. [src](https://docs.portkey.ai/docs/product/observability-modern-monitoring-for-llms/traces), [src](https://portkey.ai/docs/introduction/what-is-portkey)

**OpenPipe** — request log `{input, output, tags}` via SDK `openpipe.tags` or `op-tags` header; JSONL export for fine-tuning. [src](https://docs.openpipe.ai/features/request-logs/logging-requests)

**Baseten** — metrics only: payload-size and latency percentiles, tokens/s; Truss OTel tracing off by default for hot-path overhead. [src](https://docs.baseten.co/observability/metrics)

**OpenAI stored completions** — `store: true` persists input/output; `metadata` ≤16 pairs is the only mutable field. [src](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/update)

**Anthropic** — org-level Admin API `usage_report/messages` (uncached input, cache reads/writes, output; by key/workspace/model/tier) and `cost_report` (USD by day); Message Batches for async execution. [src](https://docs.anthropic.com/en/api/usage-cost-api), [src](https://docs.anthropic.com/en/docs/build-with-claude/message-batches)

## 7. Async / zero-hot-path techniques

| technique | who | url | adopted? |
|---|---|---|---|
| Object-store-first ingestion: raw event → S3, reference → Redis, worker → ClickHouse | Langfuse v3 | [src](https://langfuse.com/blog/2024-12-langfuse-v3-infrastructure-evolution), [src](https://langfuse.com/self-hosting) | **Yes, shape-wise**: spool → S3 blob → ClickHouse row ([`03`](03-architecture.md) §3), local file instead of Redis |
| Edge proxy → S3 bodies + Kafka → ClickHouse consumer | Helicone | [src](https://upstash.com/blog/implementing-upstash-kafka-with-cloudflare-workers) | No Kafka; the gateway's bounded queue + spool is the same decoupling on one box |
| Bounded in-process queue + batch + retry + disk spill | this repo (`usage_events`) | `gateway.py` `enqueue()`/`ingest()` | **Yes** — the existing pattern, larger payload |
| Async export in the SDK, batching tuned for GenAI payload sizes | OTel SDK / semconv guidance | [src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md) | Not in v1 (D5); the OTLP path is the Phase 3 engine join |
| Content externalised, references on the span | OTel semconv pattern 3 | same | **Yes** (`content_ref`) |
| Masking on the exporter thread, never the request | Langfuse `mask_otel_spans` | [src](https://langfuse.com/docs/observability/features/masking) | Later (`redaction_status`); the seam is the worker |
| Sample content, never rows; 100 % errors / feedback / outliers | platform/01 §6.2; practitioner tail-sampling advice | [src](https://opentelemetry.io/docs/concepts/sampling/) | **Yes** — rows always; judge sampling per J1 |
| Batch API for the *judge* (async, 50 % price) | Anthropic Message Batches | [src](https://docs.anthropic.com/en/docs/build-with-claude/message-batches) | **Yes** (J2) |
| Engine-level async OTLP exporter | vLLM / SGLang | [src](https://github.com/vllm-project/vllm/blob/main/examples/observability/opentelemetry/README.md) | Phase 3 |

## 8. Deliberately not captured in v1

| omitted | why |
|---|---|
| host CPU/memory/GPU per request | fleet metrics (DCGM/node exporter) in production-api Phase 3; never enters the loop |
| per-attempt retry spans | no gateway retries until production-api Phase 2; then `attempts` is one column, not a span tree |
| raw media bytes by default | 1,500× a text trace; `ref_only` + `media_copy` opt-in ([`../platform/01`](../platform/01-observability-and-tracing.md) §7.2) |
| PII detection / redaction | NF5 gap; `redaction_status` column reserved; Presidio is the named follow-up |
| trace trees / agent spans / tool spans | one request = one span; no agent plane |
| prompt template registry | no templates in the API; `prompt_stack_hash` detects change without one |
| embeddings, clustering, drift | platform/01 §4; needs the rows first |
| `created_cache_tokens`, reasoning-token counts as columns | kept raw in `content.upstream.usage`; promote when a consumer exists |

---

## Verification log

- 2026-09-20 — Rows in §2 cross-checked against [`04-data-model.md`](04-data-model.md) §2.1/§2.2 column names; every `traces`/`scores` column appears exactly once here. vLLM `prompt_tokens_details` fields and gating (`_make_prompt_tokens_details(enable_prompt_tokens_details, num_cached_tokens, num_cache_creation_tokens, mm_token_counts)`) and `_base_request_id` reading `X-Request-Id` were fetched from the vLLM repo this session; `vllm/tracing/utils.py` attribute names are as verified in [`../platform/01`](../platform/01-observability-and-tracing.md) (2026-09-19). Prometheus names are those confirmed in `../production-api/03` verification item 11 and `07` §1.2; `vllm:request_prefill_time_seconds` / `_decode_time_seconds` and the exact `num_preemptions` spelling are **⚠️ TO BE VERIFIED** on the deployed nightly's `/metrics`. Vendor URLs are carried verbatim from the 2026-09-20 survey; the LangSmith and Langfuse API-reference pages render as JS apps and were confirmed via search excerpts only. The "~97 % without explicit feedback" figure is a practitioner claim without a primary source and is marked.

### Implementation-plan amendment log — 2026-09-20

Documentation reconciliation only: incorporated the unified plan and review corrections above. Historical measurements and previous verification entries remain unchanged; new implementation/live tests are pending.
