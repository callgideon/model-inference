# Inference observability, tracing and request capture

Research date **2026-09-19**. This document is the **observability + traces**
component of the closed-loop platform framed in
[`00-goal-and-problem-statement.md`](00-goal-and-problem-statement.md). In doc 00's
§6 decomposition that component is labelled "doc 02"; the file is numbered `01`
here because the programme's file ordering was fixed after that table was written.
**Where doc 00 says "doc 02 — Observability + traces", it means this file.** Doc 00's
"doc 01 — Inference, endpoints, versioning" is a separate, still-unwritten document,
referred to below as *the endpoint/versioning doc*.
**The same offset applies to every other "doc NN" reference below** (fact-check
2026-09-19): they follow doc 00 §6's *component labels*, not filenames. Current
mapping — 00 §6 label → file: 02→`01` (this file), 04→`04`, 06→`05`, 07→ the A/B
machinery now inside [`04-evals-and-ab-testing.md`](04-evals-and-ab-testing.md),
09→[`07-competitor-analysis.md`](07-competitor-analysis.md) (which says so in its
own numbering note), 08 (governance/privacy) → **no file; `08` is economics**.
Labels 01, 03 and 05 (endpoints, annotation, training) have **no file at all**.
⚠️ Every "*Owner: doc NN*" in the Open questions below therefore needs that map
applied, and three of them name a document that does not yet exist.

**Conventions.** Legend, cost formulas and price tiers are
[`research/METHODOLOGY.md`](../METHODOLOGY.md). Serving-stack, routing, concurrency
and cost-engineering facts are [`research/scaling/`](../scaling/); per-model
architecture is [`research/models/`](../models/); per-model × per-GPU $/1M is
[`research/matrix/`](../matrix/). Stage names **S1–S9**, invariants **I1–I7** and
actors are doc 00 §1 and are not re-derived here.

| Marker | Meaning |
|---|---|
| `[src](url)` | Primary source fetched 2026-09-19 (vendor docs/pricing, spec repo, paper, source code). |
| **⚠️ TO BE VERIFIED** | No primary source obtained, or the claim is my inference; reasoning stated inline. |
| `meas.` | A published measurement or a value read out of source code. |
| `vendor` | Vendor-claimed, not independently verified. |
| `est.` | Arithmetic from sourced inputs; shown below, recomputed with `python3`. |

> **Research-method caveat, stated up front.** This session's **WebSearch budget was
> already exhausted (200/200) before this agent started** — the same wall doc 00 hit.
> Every source below was reached by **WebFetch or `curl` against a URL I could name**,
> or by following links from a fetched page. The consequence: **§2 is a survey of the
> tools named in the task brief plus what those pages linked to, not a market scan.**
> A tool that exists but that I could not name is missing, not disproven. Nothing in
> this document is recalled from memory: if it has no `[src]`, it is marked ⚠️ or is
> arithmetic shown in full.

---

## 1. What to capture per request for a closed loop

### 1.1 The rule that decides the schema

Observability for a *closed loop* is not observability for *debugging*. The
difference is a hard test that every field must pass:

> **A field earns its place in the trace schema only if a named downstream stage
> (S3 annotation, S4 datasets/evals, S5 training, S7 gate, S9 A/B) consumes it, or
> if its absence makes a trace unusable as a training example.**

That test kills a lot of conventional telemetry and adds fields no APM vendor ships.
Two examples of each:

- **Killed**: host-level CPU/memory per request (never enters the loop; belongs in
  fleet metrics, [`scaling/08-reliability-and-operations.md`](../scaling/08-reliability-and-operations.md)).
  Span events for every retry attempt (the loop needs *that* it retried, not the
  per-attempt jitter).
- **Added**: the **hash of the assembled prompt stack** (doc 00 §2.2 — a customer
  prompt change is a silent distribution shift that voids the parity claim), and the
  **served-artifact id** (doc 00 §1.2 — the gate-twice rule means "which quantised
  build produced this token" is a training-and-eval fact, not an ops fact).

The second consequence is a data-modelling one. A trace used for debugging can be
lossy and sampled. **A trace used as a training example cannot be lossy**, because
the student is trained to reproduce *exactly* the input→output mapping that was
served. If the trace store truncates a 20k-token system prompt at 8k, every example
derived from it teaches the student the wrong task. This single fact drives §3's
storage design and rules out several tools in §2 at their default settings.

### 1.2 The per-request capture list

Grouped by which loop stage consumes it. "Required" means the loop breaks without it.

**(a) The prompt stack — the exact bytes that were sent**

| Field | Req. | Consumed by | Notes |
|---|:--:|---|---|
| `system_instructions` (full text, untruncated) | ✅ | S3, S5, S7 | Doc 00 §2.1: 1–20k tokens, assembled per request. Must be the *rendered* text, not the template id. |
| `prompt_template_id` + `prompt_version` | ✅ | S7, S9 | OTel has `gen_ai.prompt.name` / `gen_ai.prompt.version` [[src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)]. |
| `prompt_stack_hash` (sha256 over rendered system + tool defs + params) | ✅ | S9 | **Not in any convention.** The mechanism that detects doc 00 §2.2's silent shift. |
| `messages[]` (role, ordered parts: text / image_ref / video_ref / tool_call / tool_result) | ✅ | S3, S5 | Structure must survive; see §1.6 for media. |
| `tool_definitions[]` (full JSON Schema) | ✅ | S5, S7 | Doc 00 §3.3: student tool-use degrades with tool count; per-tool accuracy needs the definition set, not just the calls. |
| `rag_context[]` (doc ids + content refs + scores) | ◻︎ | S3, S4 | Doc 00 §2.1 pt 4 — retrieval is a confound that gets blamed on the model. Without it, a quality regression cannot be attributed. |

**(b) Model, version and parameters**

| Field | Req. | Consumed by | Notes |
|---|:--:|---|---|
| `request.model`, `response.model` | ✅ | all | Providers silently serve point releases; `response.model` is the ground truth. |
| `served_artifact_id` | ✅ | S7, S8, S9 | Weights + quantisation format + engine + engine flags + chat template + tokenizer, as one immutable id. Owned by the endpoint/versioning doc; **referenced from every trace**. |
| `endpoint_role` ∈ {main, dev, shadow} | ✅ | S9 | The A/B control plane's join key. |
| `temperature`, `top_p`, `top_k`, `seed`, `max_tokens`, `stop`, `stream`, `reasoning_level`, `response_format` | ✅ | S5, S7 | Doc 00 §2.3: sampling defaults shift eval scores for reasons unrelated to the model. All are named `gen_ai.request.*` attributes [[src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)]. |
| `provider` | ✅ | S3, cost | `gen_ai.provider.name`, which the spec explicitly calls "a discriminator that identifies the GenAI telemetry format flavor" [[ibid.](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)]. |

**(c) Output**

| Field | Req. | Consumed by | Notes |
|---|:--:|---|---|
| `output_messages[]` (all choices, with parts) | ✅ | S3, S5, S7 | |
| `finish_reason[]` | ✅ | S4, S7 | Doc 00 §2.3: refusals carry a distinct stop reason and an app may branch on it. Also the `length` truncation signal, which silently corrupts training examples. |
| `tool_calls[]` (id, name, **arguments as parsed JSON**) | ✅ | S5, S7 | Doc 00 §2.3: *always `json.loads`, never string-match* — store the parsed object **and** the raw string, because escaping differences between engines are exactly the failure being hunted. |
| `structured_output_valid` (bool) + `schema_id` | ✅ | S7 | Doc 00 §2.3 requires 100 %, not 99.5 %. Computed at capture time; cheap; otherwise nobody ever computes it. |
| `reasoning_content` / thinking tokens | ◻︎ | S3, S5 | Orca's result is that imitating *explanation traces* is what closes the gap (doc 00 §3.1) — so when the incumbent exposes them, they are the highest-value bytes in the trace. Legally the riskiest too (§8.1 of doc 00). |

**(d) Cost and tokens**

| Field | Req. | Consumed by | Notes |
|---|:--:|---|---|
| `usage.input_tokens`, `usage.output_tokens` | ✅ | I2 | Note the rename: `gen_ai.usage.prompt_tokens`/`completion_tokens` are **deprecated** in favour of `input_tokens`/`output_tokens` [[src](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)]. §2.5 shows vLLM still emits the old names. |
| `usage.cache_read.input_tokens`, `usage.cache_write.input_tokens` | ✅ | I2 | Doc 00 §4.4: cache hit rate is the difference between a 138× and a 116× pitch. Without these two fields the cost comparison is not defensible. Both are `Recommended` in semconv [[src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)]. |
| `usage.reasoning.output_tokens` | ◻︎ | I2 | Billed as output; invisible to the user; a real source of "why is my bill higher than my token count". |
| `usage.{text,image,audio}.{input,output}_tokens` | ◻︎ | I2, video | Semconv splits usage by modality [[ibid.](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)] — the only standard way to say "this request cost 23,560 prefill tokens because it was 240 frames". |
| `cost_usd` (computed, with `price_table_version`) | ✅ | I2 | **Compute at ingest, store the price-table version.** Recomputing historical cost after a vendor price change silently rewrites history and breaks every prior A/B cost claim. |

**(e) Latency**

| Field | Req. | Consumed by | Notes |
|---|:--:|---|---|
| `ttft_ms` (client-observed) | ✅ | I3 | `gen_ai.response.time_to_first_chunk` on the span, and `gen_ai.client.operation.time_to_first_chunk` as a metric [[src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md)]. |
| `tpot_ms` / time-per-output-chunk | ✅ | I3 | `gen_ai.client.operation.time_per_output_chunk`, and server-side `gen_ai.server.time_per_output_token` [[ibid.](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md)]. |
| `e2e_ms`, `queue_ms`, `prefill_ms`, `decode_ms` | ◻︎ | I3, S8 | Only the engine can produce the breakdown; vLLM emits all four as custom attributes (§2.5). Needed to answer "is the candidate slower, or is the fleet just busier" — the question that otherwise stalls every A/B. |
| `concurrency_at_admission` | ◻︎ | I3, S8 | The number that makes p99 comparable across arms. See [`scaling/03-concurrency-and-admission-control.md`](../scaling/03-concurrency-and-admission-control.md). |
| `retry_count`, `error.type` | ✅ | S4 | A retried request is one logical operation; semconv says the span "SHOULD cover the duration of the logical operation with all retries" [[src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)]. |

**(f) Identity and grouping**

| Field | Req. | Consumed by | Notes |
|---|:--:|---|---|
| `tenant_id` | ✅ | I6 | Partition key everywhere. Doc 00 §8.2: tenant isolation is absolute. |
| `session_id` / `conversation_id` | ✅ | S9 | `gen_ai.conversation.id`. **The randomisation unit for A/B** (doc 00 §5.4: never randomise at the request in a multi-turn product). Semconv is careful here: "When no identifier for the conversation is available, instrumentations SHOULD NOT populate conversation id… a new UUID, a trace identifier, or a hash" is explicitly *not* acceptable [[ibid.](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)] — which means **the platform must mint and propagate a stable session id itself**, because the frameworks often cannot. |
| `end_user_pseudonym_id` | ✅ | S4, S9 | Pseudonymous, per-tenant salted hash. Doc 00 §8.4: splits must be by stable entity, never by row. |
| `turn_index`, `parent_span_id`, `trace_id` | ✅ | S4, S5 | Session-level eval needs the full trajectory (doc 00 §5.4). |
| `agent_run_id`, `workflow_name` | ◻︎ | S4 | `gen_ai.agent.*`, `gen_ai.workflow.name` exist in the registry. |
| `task_id` | ✅ | all | The platform's unit of work: one customer + one task = one student + one eval + one endpoint pair. Not in any convention; it is our primary partition. |

**(g) Feedback and outcome signals — the scarcest and most valuable data**

Doc 00 §5.1 is blunt: *"Wherever an outcome signal exists (ticket resolved, code
merged, transaction completed), it beats any judge"*, backed by the RealHumanEval
finding that even human preference does not correlate with task performance
[[src](https://arxiv.org/abs/2404.02806)]. So the schema must make outcome signals
first-class and **arriving late**:

| Signal | Latency to arrive | Strength | Capture mechanism |
|---|---|---|---|
| Explicit thumbs up/down | seconds–minutes | weak, biased to extremes | Feedback API keyed by `trace_id` |
| **Edit distance between model output and what the user shipped** | minutes–hours | **strong** | App instrumentation; needs customer cooperation |
| Retry / regenerate | seconds | strong negative | Derivable from traces: same session, near-identical prompt (§4.1) |
| Escalation to human / to the incumbent model | minutes | **strong negative** | Route event |
| Downstream task outcome (ticket closed, code merged, payment completed) | hours–weeks | **strongest** | Customer webhook, joined asynchronously |
| Abandonment (session ends after this turn) | minutes | medium negative | Derivable |
| Judge score | minutes | medium, and validated separately | `gen_ai.evaluation.result` event (§1.3) |

**The design consequence is a late-binding join.** Outcome signals arrive days after
the trace. Any store that treats a trace as immutable-and-final at write time cannot
hold them. The platform's answer (§6.2): traces are immutable; **signals are a
separate append-only table keyed by `trace_id`**, and every dataset build is a join
at a stated cut-off time. Mutating the trace row instead is the mistake that makes
dataset builds irreproducible.

### 1.3 OpenTelemetry GenAI semantic conventions, as of 2026-09-19

**The first fact is structural and recent: the GenAI conventions no longer live in
the main semantic-conventions repo.** `opentelemetry.io/docs/specs/semconv/gen-ai/`
now returns only *"GenAI semantic conventions have moved to the OpenTelemetry GenAI
semantic conventions repository. This page has moved and is no longer maintained in
this repository."* [[src](https://opentelemetry.io/docs/specs/semconv/gen-ai/)]. The
new home is `open-telemetry/semantic-conventions-genai`, **created 2026-05-05**, 380
stars, last pushed 2026-09-16 (`meas.`, GitHub API). It "extends the OpenTelemetry
Semantic Conventions with GenAI-specific conventions, using Weaver to manage
dependencies on the core semantic conventions", and its Schema URL section reads, in
full, `TODO` [[src](https://github.com/open-telemetry/semantic-conventions-genai)].

⚠️ **Trap for anyone reading the old page.** The mirrored attribute registry at
`opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/` renders **every**
`gen_ai.*` attribute with stability "Deprecated"
[[src](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)].
That is an artifact of the *page* being deprecated, not of the attributes. The
authoritative repo marks the same attributes **Development**
[[src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)].
Do not let a tooling decision rest on the mirror. (The genuinely deprecated ones are
listed below.)

**Status: Development, across the board.** Every GenAI document in the repo carries
`**Status**: [Development]` — spans, metrics, events, agent spans, exceptions
[[src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/README.md)].
No part of the GenAI convention is Stable. The only Stable attributes appearing in
GenAI spans are borrowed from core semconv: `error.type`, `server.address`,
`server.port`
[[src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)].
**Decision rule:** adopt the conventions as the wire format (the ecosystem has
converged, §2), but **do not make a Development-status spec the platform's internal
storage schema** — see §1.5.

**The signals defined.**

*Spans* [[src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)]:
Inference, Embeddings, Retrievals, Fetch response, Memory, Execute tool. Plus agent
spans in a separate document
[[src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-agent-spans.md)]
and MCP conventions
[[src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/mcp.md)].
Span kind SHOULD be `CLIENT`, MAY be `INTERNAL` for in-process models; **span name
SHOULD be `{gen_ai.operation.name} {gen_ai.request.model}`**.

`gen_ai.operation.name` values include `chat`, `generate_content`,
`text_completion`, `embeddings`, `execute_tool`, `fetch_response`, `create_agent`,
and a family of memory operations (`create_memory`, `delete_memory`,
`create_memory_store`, `delete_memory_store`, …) [[ibid.](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)].

*Requirement levels on the inference span* [[ibid.](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)]:

| Level | Attributes |
|---|---|
| **Required** | `gen_ai.operation.name`, `gen_ai.provider.name` |
| **Conditionally Required** | `error.type`, `gen_ai.conversation.id`, `gen_ai.output.type`, `gen_ai.prompt.name`, `gen_ai.prompt.version`, `gen_ai.request.choice.count`, `gen_ai.request.model`, `gen_ai.request.seed`, `gen_ai.request.stream`, `gen_ai.request.top_k`, `server.port` |
| **Recommended** | `gen_ai.conversation.compacted`, `gen_ai.request.{frequency_penalty,max_tokens,presence_penalty,previous_response.id,reasoning.level,stop_sequences,temperature,top_p}`, `gen_ai.response.{finish_reasons,id,model,time_to_first_chunk}`, the whole `gen_ai.usage.*` family (text/image/audio × input/output/cache_read/cache_write, plus `reasoning.output_tokens`), `server.address` |
| **Opt-In** | `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions`, `gen_ai.tool.definitions`, `gen_ai.prompt.variable` |

**That last row is the single most important fact in this section for our platform.**
The four attributes that carry the actual content — the only attributes from which a
training example can be built — are **Opt-In**, i.e. off by default. The spec is
explicit about why:

> "Model instructions, user messages, and model outputs are considered sensitive and
> are often large in size. Recording large or sensitive content in telemetry may be
> problematic due to high storage costs, regulatory requirements, or the need to
> enforce different access models for operational and user data. OpenTelemetry
> instrumentations SHOULD NOT capture them by default, but SHOULD provide an option
> for users to opt in."
> [[src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)]

It then names three usage patterns: (1) don't record, the default; (2) record on the
span attributes, "best suited for situations where telemetry volume is manageable
and either privacy regulations do not apply or the telemetry storage complies with
them, for example, in pre-production environments"; (3) **"Store content externally
and record references on the spans… recommended in production environments where
telemetry volume is a concern or sensitive data needs to be handled securely. Using
external storage enables separate access controls."** [[ibid.](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)]

Pattern 3 is what this platform must do (§1.6, §3.2, §6.2). And here is the gap that
matters: the spec supports it via an in-process upload hook that "SHOULD" be invoked
"regardless of the span sampling decision", and can enrich the span — but on how the
reference itself is recorded it says, verbatim:

> "**TODO: document a common approach to record references to externally stored
> content.**" [[ibid.](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)]

⚠️ **So there is no standard for the exact thing the platform needs most.** We will
define our own reference format (§1.6) and accept that it is not portable. §5's
`Streaming chunks` section is also a bare `TODO` [[ibid.](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)].

One further practical wrinkle: structured attribute values "may not yet be supported
on spans" in a given language, in which case "the corresponding attribute value
SHOULD be serialized to JSON string on spans and recorded in its structured form on
events" [[ibid.](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)],
pending an accepted OTEP on complex attribute values. **In practice: expect
`gen_ai.input.messages` as a JSON *string* in 2026, and write the parser accordingly.**

*Metrics* [[src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md)]
— note the deliberate client/server split, which maps exactly onto our "is the
candidate slower or is the fleet busier" problem:

| Metric | Side | Bucket boundaries (spec-recommended) |
|---|---|---|
| `gen_ai.client.token.usage` | client | 1, 4, 16, 64, 256, 1024, 4096, 16384, 65536, 262144, 1048576, 4194304, 16777216, 67108864 |
| `gen_ai.client.operation.duration` | client | 0.01 … 81.92 (powers of 2) |
| `gen_ai.client.operation.time_to_first_chunk` | client | 0.01 … 81.92 |
| `gen_ai.client.operation.time_per_output_chunk` | client | 0.01 … 81.92 |
| `gen_ai.server.request.duration` | **server** | — |
| `gen_ai.server.time_to_first_token` | **server** | — |
| `gen_ai.server.time_per_output_token` | **server** | — |
| `gen_ai.invoke_workflow.duration` | orchestration | 1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600, 7200 |
| `gen_ai.invoke_agent.duration` / `.inference_calls` / `.tool_calls` | agent | — |
| `gen_ai.execute_tool.duration` | tool | — |

The token-usage histogram's top bucket at 67,108,864 tokens is a reminder that the
convention is written for a world with very long contexts; our 23,560-token video
prefill sits in the 16384–65536 bucket.

*Events* [[src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-events.md)]:

- **`gen_ai.client.inference.operation.details`** (Opt-In) — "could be used to store
  input and output details independently from traces". This is the seam that lets
  content go to a *different* backend, with different retention and access control,
  from the operational spans. **We use it exactly that way** (§6.2).
- **`gen_ai.evaluation.result`** (Recommended) — "This event captures the result of
  evaluating GenAI output… SHOULD be parented to GenAI operation span being evaluated
  when possible or set `gen_ai.response.id` when span id is not available." Required
  `gen_ai.evaluation.name`; conditionally `gen_ai.evaluation.score.label` (low
  cardinality, e.g. `pass`/`fail`/`correct`) and `.score.value` (double); recommended
  `gen_ai.evaluation.explanation`. **This is a standard, already-defined carrier for
  judge scores attached to production traces** — i.e. doc 00's S3 and S7 signals have
  a wire format, and we should not invent one.

Provider-specific conventions exist for Anthropic, OpenAI, AWS Bedrock and Azure AI
Inference [[src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/README.md)],
which matters because our customers' incumbents are exactly those providers (doc 00
§2.1).

*Deprecated, and still widely emitted in the wild*
[[src](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)]:
`gen_ai.prompt` and `gen_ai.completion` (→ use the messages attributes/events),
`gen_ai.system` (→ `gen_ai.provider.name`), `gen_ai.usage.prompt_tokens` and
`gen_ai.usage.completion_tokens` (→ `input_tokens`/`output_tokens`), and five
`gen_ai.openai.*` attributes (→ `gen_ai.output.type`, `gen_ai.request.seed`,
`openai.*`). §2.5 shows vLLM emitting two of these today.

### 1.4 What the convention does *not* give the closed loop

| Missing | Why it matters | Our answer |
|---|---|---|
| Reference format for externally-stored content | The spec's own `TODO`; and pattern 3 is the only viable production pattern | Define `lfp://{tenant}/{task}/{blob_sha256}` + `content_ref` attributes (§1.6) |
| `served_artifact_id` | Gate-twice (doc 00 §1.2) needs to know which quantised build emitted each token | Platform attribute `lfp.artifact.id` |
| `prompt_stack_hash` | Detects the silent distribution shift of doc 00 §2.2 | Platform attribute `lfp.prompt_stack.sha256` |
| Endpoint role (main/dev/shadow) and experiment arm | The A/B join key (doc 00 §5.4) | `lfp.endpoint.role`, `lfp.experiment.id`, `lfp.arm` |
| Cost | Not a telemetry concept in semconv; it is the product's headline metric (I2) | `lfp.cost.usd` + `lfp.cost.price_table_version` |
| Late-arriving outcome signals | Traces are immutable; outcomes are not | Separate `signals` table (§1.2g, §6.2) |
| Tenant | I6 isolation | `lfp.tenant.id` as partition key, enforced below the query layer |
| Structured-output validity | Doc 00 §2.3 needs 100 %, and nobody computes it retroactively | `lfp.output.schema_valid`, `lfp.output.schema_id`, computed at ingest |

⚠️ **Naming risk.** Custom attributes in a private namespace (`lfp.*`) are safe from
collision but invisible to every off-the-shelf backend's UI. Langfuse's mapping, for
example, nests unmapped OTel attributes under `metadata.attributes` and only
top-level metadata keys are directly filterable
[[src](https://langfuse.com/integrations/native/opentelemetry)] — so a custom
attribute we need to *filter* on must be promoted into whatever the chosen backend
treats as first-class. This is a real, concrete adoption cost of buying rather than
building (§6).

### 1.5 The schema

Two schemas, deliberately, because they change on different clocks.

**(1) The wire format is OTel GenAI semconv + an `lfp.*` extension namespace.** We do
not invent a transport. This buys the whole instrumentation ecosystem (§2) and an
exit (doc 00 §8.6).

**(2) The storage schema is ours, normalised, and versioned independently.** Reason:
the convention is Development-status, the content attributes are Opt-In, the reference
format is a `TODO`, and two token attributes were renamed within the last generation.
A store whose columns are literally the spec's attribute names inherits every rename.

Canonical storage model — four tables, one blob store:

```
trace_span        one row per GenAI operation (the operational record)
  tenant_id, task_id, trace_id, span_id, parent_span_id, session_id,
  end_user_pseudonym_id, ts, operation_name, provider, request_model,
  response_model, served_artifact_id, endpoint_role, experiment_id, arm,
  prompt_template_id, prompt_version, prompt_stack_hash,
  params (JSON), finish_reasons[], error_type, retry_count,
  input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
  reasoning_tokens, image_input_tokens, audio_input_tokens,
  cost_usd, price_table_version,
  ttft_ms, tpot_ms, e2e_ms, queue_ms, prefill_ms, decode_ms,
  concurrency_at_admission, schema_valid, schema_id,
  content_ref, content_bytes, redaction_status, pii_entities[]

content_blob      immutable, content-addressed (S3/GCS), one object per span
  { system_instructions, input_messages, output_messages,
    tool_definitions, tool_calls_raw, reasoning_content, rag_context }

media_object      immutable, content-addressed; images/audio/video
  sha256, mime, bytes, duration_s, frame_count, source_uri, tenant_id

signal            append-only, late-arriving, keyed by trace_id
  trace_id, ts, kind (thumb|edit|retry|escalation|outcome|abandon|judge),
  value, weight, source, judge_model, judge_version, explanation

dataset_item      the S4 boundary; see §5
```

Three properties that are not optional:

1. **`content_blob` is content-addressed by sha256.** Free dedup across retries and
   across the many requests that share a 15k-token system prompt (§3.5); and the
   hash *is* the identity used by `prompt_stack_hash`.
2. **`trace_span` never mutates.** Judge scores, human labels and outcomes are
   `signal` rows. Any dataset build states its cut-off timestamp.
3. **`redaction_status` is a column, not a pipeline property**, with values
   `raw | redacted | redaction_failed | redaction_skipped`. Doc 00 §8.2 requires
   redaction *on the egress edge*; a column makes "has this blob been cleared for
   teacher egress?" a query, not a belief. **Nothing with `redaction_status != redacted`
   may leave the tenant boundary.** (§3.6)

### 1.6 Multimodal: store references, never bytes, in the span

For images, audio and video the rule is simple and the industry already agrees.

**What the spec says:** external storage is the recommended production pattern, with
an upload hook, and the reference format is a `TODO` [[src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)].

**What a mature implementation does:** Langfuse's SDKs "automatically detect and
handle base64 encoded media by extracting it, uploading it separately as a Langfuse
Media file" and replacing it with a reference string; media lands in S3-compatible
object storage; upload presigned URLs carry "content validation (length, type,
SHA256 hash)"; and **"File uniqueness [is] determined by project, content type, and
content SHA256 hash"**, so duplicates just reference the existing media id
[[src](https://langfuse.com/docs/tracing-features/multi-modality)]. Supported MIME
types span images (PNG/JPEG/WebP/GIF/SVG/TIFF/BMP/AVIF/HEIC), audio
(MP3/WAV/OGG/AAC/FLAC/Opus), **video (MP4/WebM/OGG/MPEG/MOV/AVI/MKV)**, documents and
data formats [[ibid.](https://langfuse.com/docs/tracing-features/multi-modality)].
Media supplied as an external URL renders inline without being uploaded at all
[[ibid.](https://langfuse.com/docs/tracing-features/multi-modality)] — which is the
cheapest option when the customer already hosts the asset and will keep it.

**Our reference format** (since the spec has none):

```
lfp.content.ref          = "lfp://{tenant}/{task}/blob/{sha256}"     # span text payload
lfp.media[i].ref         = "lfp://{tenant}/{task}/media/{sha256}"
lfp.media[i].mime        = "video/mp4"
lfp.media[i].bytes       = 31457280
lfp.media[i].duration_s  = 120.0
lfp.media[i].frames_sent = 240          # what the model actually saw
lfp.media[i].sample_fps  = 2.0
lfp.media[i].retention   = "customer_hosted" | "platform_copy" | "ref_only"
```

`frames_sent` and `sample_fps` are not decoration. For the repo's video student,
Marlin-2B, the **240-frame cap bounds every request at ~23,560 prefill tokens
regardless of clip length**, so a 10-minute clip is sampled at 0.40 fps and an hour
at 0.067 fps *for identical cost*
([`models/marlin2b/README.md`](../models/marlin2b/README.md) §9). Two consequences
for the trace schema:

- **The trace must record the frames the model saw, not the clip's duration**,
  because the model's input is the frame stack, and a dataset item built from
  "duration" is not reproducible.
- That same README flags a **Path A vs Path B token-budget fork — 23,560 vs 12,288
  prefill tokens, a 1.917× gap — that raises no error and is "a silent quality
  regression"** (⚠️ the README prints the ratio as **1.914×**, which is
  23,**520**/12,288; its item 2 says 23,560 and its open question 2 says 23,520, so
  the source is internally inconsistent by 40 tokens — recomputed 2026-09-19:
  23,560/12,288 = 1.9173, 23,520/12,288 = 1.9141) ([ibid.](../models/marlin2b/README.md) item 2 of the open questions).
  **Capturing `frames_sent` and the realised `image_input_tokens` per request is the
  only cheap detector for a deployment that silently landed on Path B.** That is a
  concrete case of observability catching a serving bug that evals would attribute
  to the model.

**Retention rule for media**, driven by §7's arithmetic: default to `ref_only`
(customer-hosted URI + our sha256 + our frame metadata), and take a `platform_copy`
only for (a) items promoted into a dataset, (b) items sampled for annotation, and
(c) a small rolling window for incident forensics. §7 shows why: the video blobs are
60–240× the size of all the text traces combined.

### 1.7 A note on who measures latency

I3 is a p50 *and* p99 TTFT/TPOT claim at the customer's concurrency (doc 00 §1.3).
Three vantage points give three different numbers:

| Vantage | Sees | Blind to | Source |
|---|---|---|---|
| Customer SDK / client | true user-perceived TTFT incl. network | queueing vs compute split | `gen_ai.client.*` metrics [[src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md)] |
| Gateway | per-provider comparison, retries, fallbacks | in-engine breakdown | §2.4 |
| Engine (vLLM/SGLang) | queue / prefill / decode split | network, client-side buffering | §2.5 |

**Decision rule: the A/B claim (I3) is made on the client-side number, and the
engineering work (S8) is driven by the engine-side breakdown.** Mixing them is how a
team spends a week optimising decode for a regression that was queueing.

---

## 2. Tooling landscape

### 2.1 Three capture planes, and the rule for choosing

| Plane | How | Sees | Cannot see | Latency added to the customer's production path |
|---|---|---|---|---|
| **SDK / in-app** | auto-instrumentation in the customer's process | full app context, RAG, tool results, business outcome, user id | nothing about our engine internals | ~0 if async export; the spec's own advice is to tune batching for GenAI volumes [[src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)] |
| **Gateway / proxy** | requests routed through a proxy | exact bytes on the wire, cost, provider errors, retries, fallbacks; **works with zero customer code change** | app-level context and outcomes | **real and non-zero** — Portkey's own estimate is "a total latency addition between 20-40ms compared to direct API calls" [[src](https://portkey.ai/docs/introduction/what-is-portkey)] |
| **Engine** | vLLM/SGLang OTLP export | queue/prefill/decode breakdown, scheduler behaviour | the prompt's meaning, the user, the outcome | ~0 (async exporter), only for models *we* serve |

**Decision rule.** The loop needs all three, but they are not interchangeable and the
ordering is forced by doc 00's MVP criterion 1 — *≥99.9 % of requests captured, added
p99 latency ≤ 5 ms*:

1. **SDK-first for the customer's incumbent traffic.** A 20–40 ms gateway tax on the
   customer's *existing* GPT-5.6/Opus-5 production path fails criterion 1 outright,
   and — worse — makes us a SPOF on their revenue path before we have earned any
   trust (doc 00 §1.2, S1 failure mode).
2. **Gateway for our own `main`/`dev`/`shadow` endpoints**, where we are already in
   the path and the tax is ours to pay, and where the gateway is how shadow mirroring
   is implemented anyway (§5.3).
3. **Engine telemetry always**, on our own fleet, because it is free and it is the
   only source for S8's optimisation work.
4. Offer the gateway for incumbent traffic only as an **opt-in convenience** for
   customers who cannot instrument their app — and quote them the latency honestly.

### 2.2 The landscape table

Prices are list, fetched 2026-09-19. "Units" differ between vendors and the
differences are large — see §7 for the same workload priced across five of them.

| Tool | Hosting | License | Priced on | List price | Loop-relevant strengths | Loop-relevant gaps |
|---|---|---|---|---|---|---|
| **Langfuse** | Cloud (EU/US/JP/HIPAA) + self-host | MIT ("MIT Expat") core, `ee/` under a commercial Enterprise License — read from the repo's own `LICENSE`/`ee/LICENSE`, **not** the self-hosting page, which states no licence [[src](https://raw.githubusercontent.com/langfuse/langfuse/main/LICENSE)]. **⚠️ Ownership: that `LICENSE` is copyright "ClickHouse, Inc. 2023-2026", and the repo README states "since January 2026 we're part of ClickHouse"** [[src](https://raw.githubusercontent.com/langfuse/langfuse/main/README.md)] — corrected 2026-09-19; the original draft missed this | "billable units" = traces + observations + scores | $0 (50k units, 30 d) / $29 Core (100k, 90 d) / $199 Pro (3 y) / $2,499 Enterprise; overage $8→$7→$6.50→$6 per 100k graduated [[src](https://langfuse.com/pricing)] | OTLP ingest; datasets with true versioning; judge-on-production; **S3-backed multimodal with sha256 dedup**; self-host is the full product | Unit definition multiplies cost ~5× vs per-request (§7); no training, no endpoints, no rollout |
| **Arize Phoenix** | self-host (`uvx arize-phoenix serve`), Docker/K8s; Phoenix Cloud | **Elastic License 2.0**, "portions … patent protected" [[src](https://github.com/Arize-ai/phoenix)] | — (OSS) | free self-host; Cloud price ⚠️ not on the docs page fetched | "built on top of OpenTelemetry… vendor, language, and framework agnostic" [[ibid.](https://github.com/Arize-ai/phoenix)]; datasets + experiments + evaluators as "test cases… similar to a unit test suite" [[src](https://arize.com/docs/phoenix/datasets-and-experiments/overview-datasets)] | **ELv2 is not OSI open source** — it forbids offering the product as a managed service. For a platform *vendor*, that is a licence to read, not to build on. ⚠️ Legal review required before any embedding |
| **LangSmith** | Cloud; self-host/hybrid on Enterprise | proprietary | seats + traces + LCU/LSU | $0 Developer (5k base traces/mo) / $39 per seat Plus (10k) / Enterprise custom; **$1.50/LCU compute, $1.00/LSU storage**; base traces 14-day retention, extended 400-day [[src](https://www.langchain.com/pricing-langsmith)] | Mature datasets/experiments; the 14 d vs 400 d split is honest about the real cost driver | Per-trace overage rate not published on the pricing page ⚠️; strongest inside the LangChain ecosystem |
| **Braintrust** | Cloud; **on-prem or hosted on Enterprise** | proprietary | **GB processed + scores** | $0 Starter ($10 credits, 1 GB, 10k scores, 14 d) / $249 Pro ($100 credits, 5 GB, 50k scores, 30 d) / Enterprise; overage **$4/GB Starter, $3/GB Pro**; scores **$2.50/1k Starter, $1.50/1k Pro**; retention $0.50/GB/mo [[src](https://www.braintrust.dev/pricing)] | Eval-first design; human review; "Loop Agent" for autonomous eval/test-case generation | **Per-score pricing is lethal at our judge volumes** (§7); GB-based metering penalises exactly the full-fidelity capture the loop requires |
| **W&B Weave** | Cloud; self-host/private on Enterprise | proprietary | **ingested bytes** | Free 1 GB Weave ingest/mo; **Pro from $60/mo, 1.5 GB**; **additional ingestion $0.10/MB**; storage overage $0.03/GB [[src](https://wandb.ai/site/pricing/)] | "OTel-compatible SDK"; LLM judges and custom scorers; session/turn/tool-call tracking [[src](https://docs.wandb.ai/weave/)] | **$0.10/MB — $100/GB decimal, $102.40/GiB if W&B means MiB (the page does not say which ⚠️).** At full-fidelity capture this is the most expensive option found by ~30× (§7) |
| **Helicone** | Cloud; OSS; on-prem on Enterprise | open source (per its own page) | requests | Free (10k req) / **Pro $79** / **Team $799** / Enterprise; retention 7 d / 1 mo / 3 mo / forever [[src](https://www.helicone.ai/pricing)] | Gateway + async logging; caching and rate limits built in | **Overage rate not published** ⚠️; 1–3 month retention is below what a training loop needs |
| **OpenLLMetry / Traceloop** | library; any OTLP backend | open source (license ⚠️ not stated on the page fetched) | — | — | "non-intrusive tracing built on OpenTelemetry"; exports to Traceloop "or to your existing observability stack", 30+ backends incl. Datadog, Grafana, Honeycomb, Splunk, OTel Collector [[src](https://www.traceloop.com/docs/openllmetry/introduction)] | It is instrumentation, not a store. Which is the point |
| **Datadog Agent Observability** (formerly LLM Observability) | SaaS | proprietary | ⚠️ **not published on the pricing page fetched** — the row exists under AI products with no unit or price [[src](https://www.datadoghq.com/pricing/?product=llm-observability)] | — | "natively supports OpenTelemetry GenAI Semantic Conventions"; **"automated hierarchical topic clustering" called Patterns**; automatic sensitive-data scan/redact; prompt-injection evals; `DD_LLMOBS_SAMPLE_RATE` [[src](https://docs.datadoghq.com/llm_observability/), [sdk](https://docs.datadoghq.com/llm_observability/instrumentation/sdk/)] | Unpriced on a public page is a procurement problem for a per-tenant cost model; payload-size and retention limits not documented on the pages fetched ⚠️ |
| **Honeycomb** | SaaS | proprietary | **events/month** | Free 20M events + 100M metric points; **Pro from $150/mo, up to 750M events**; Enterprise from 10B events/yr; Telemetry Pipeline add-on **from $0.10/GB** [[src](https://www.honeycomb.io/pricing)] | Event-based pricing is **an order of magnitude cheaper per span** than LLM-native tools (§7); mature high-cardinality querying | No GenAI-native primitives: no datasets, no judges, no dataset→experiment lineage. It is a query engine, not a loop component |
| **Portkey** | Cloud/edge; **private cloud on Enterprise** | gateway is open source and "free to use" | requests | free tier 10k req/mo; paid plans ⚠️ not enumerated on the page fetched [[src](https://portkey.ai/docs/introduction/what-is-portkey)] | 250+ models, guardrails incl. PII, routing/fallback/caching; ISO 27001 + SOC 2, optional **no-logging** mode; **vendor** 25M+ daily requests, 99.99 % uptime | **Self-declared 20–40 ms added latency** — see §2.1's decision rule |
| **Lunary** | Cloud (EU) + self-host CE | CE free; EE paid | events | Free 10k events/mo, 30 d / **Team $20 per user/mo, 50k events, +$10 per 50k, 1 y** / Enterprise (self-host, SSO, **PII masking**, warehouse connectors) [[src](https://lunary.ai/pricing)] | EU hosting, GDPR posture, PII masking on EE; graceful overage ("data will still be captured") | $10/50k events = $200/1M is the most expensive per-event rate found (§7) |
| **Opik (Comet)** | self-host OSS + Cloud | **Apache-2.0**, 22.1k★ (`meas.`, GitHub API 2026-09-19 — corrected from the draft's "21k★ claimed") | **spans** | Free 25k spans/mo, 60 d / **Pro $19/mo, 100k spans**, 60 d / Enterprise; overage **$5 per 100k spans**; extended retention (to 400 d) **$29 per 100k** [[src](https://www.comet.com/site/pricing/)] | 30+ judge metrics, guardrails incl. PII, prompt optimizer, production-trace evaluation with alerts [[src](https://www.comet.com/site/products/opik/)] | Per-span metering again; retention-upgrade price ($29/100k) is ~6× the ingest price — read that as "long retention is the product" |
| **MLflow tracing** | self-host, OSS | Apache-2.0 (project) | — | free | **"fully compatible with OpenTelemetry"** and "natively supports GenAI Semantic Conventions for export and ingestion"; one-line autolog; async logging; lightweight `mlflow-tracing` SDK with a **vendor**-claimed "95% smaller footprint"; sampling ratio control [[src](https://mlflow.org/docs/latest/genai/tracing/)] | No purpose-built high-volume trace store; you bring the backend |

**Maturity read, 2026-09-19.** The category has commoditised, exactly as doc 00 §7.1
concluded — but it has commoditised *unevenly*. Trace capture, judge scoring and
dataset/experiment primitives are table stakes and present in six of these. What is
**not** commoditised, in any of them: production-traffic **replay against a candidate
model with statistical rigour** (§5.3), **late-arriving outcome joins** (§1.2g), and
**artifact-level lineage from a served quantised build back to the gate that passed
it** (doc 00 §1.2). That gap is where doc 07 and the endpoint/versioning doc live,
and it is why this component is a *buy* and those are *builds*.

### 2.3 Notes that change a decision

- **Langfuse's OTLP endpoint is HTTP-only.** "OTLP over HTTP (JSON/protobuf) only;
  gRPC not supported" [[src](https://langfuse.com/integrations/native/opentelemetry)].
  vLLM and SGLang both default to **gRPC** ([§2.5](#25-engine-level-vllm-and-sglang));
  so engine spans reach Langfuse only via a Collector that converts. Small, but it is
  a component nobody budgets for.
- **Langfuse's attribute precedence** is: `langfuse.*` → `gen_ai.*` → framework
  conventions (OpenInference `input.value`/`output.value`, MLflow
  `mlflow.spanInputs`/`spanOutputs`) → inference from the presence of a model name
  [[ibid.](https://langfuse.com/integrations/native/opentelemetry)]. **Emitting both
  `gen_ai.*` and OpenInference attributes is therefore safe and is the portable
  choice** — which matters because the two conventions coexist (§2.6).
- **Langfuse ingest limits**: 5 MB per request and per response; 1,000 req/min
  (Hobby), 4,000 (Core), 20,000 (Pro/Team/Enterprise); 429 with `Retry-After`; buckets
  are shared org-wide and are fixed-window, not rolling
  [[src](https://langfuse.com/faq/all/api-limits)]. A 20k-token system prompt is
  ~80 KB, so the 5 MB cap is not binding for text — but a batch of 64 spans each
  carrying a full prompt stack is ~5 MB, so **batch size must be tuned against the
  payload cap, not the span count**. (This 64 and §7.1's ~250 are the same rule at
  two prompt sizes — 5 MB ÷ 80 KB = 62.5 for a 20k-token stack, 5 MB ÷ 20,096 B =
  248.8 for the canonical 4K-in shape. Not a contradiction; noted because it reads
  as one.)
- **Langfuse scale**, self-hosted: Postgres (OLTP) + **ClickHouse (traces,
  observations, scores)** + Redis/Valkey (cache/queue) + **S3/blob (raw events,
  multimodal attachments, exports)**, with events persisted to S3 *before* DB writes
  for recoverability; **vendor**-claimed "90B+ observations per month"
  [[src](https://langfuse.com/self-hosting)]. That architecture is, almost exactly,
  the one §6.2 arrives at independently — which is the strongest available argument
  for adopting rather than building. **⚠️ Added at the 2026-09-19 fact-check: this
  is no longer an independent convergence.** Langfuse's repo README states *"since
  January 2026 we're part of ClickHouse"* and its `LICENSE` is copyright
  *"ClickHouse, Inc. 2023-2026"*
  [[src](https://raw.githubusercontent.com/langfuse/langfuse/main/README.md),
  [LICENSE](https://raw.githubusercontent.com/langfuse/langfuse/main/LICENSE)].
  Langfuse building on ClickHouse is now a first-party choice, so it is weak
  evidence for our §3.2 store decision; and adopting Langfuse + ClickHouse
  concentrates the trace store, the OLAP engine and the managed-cost line
  (§7.2's $2,279/mo compute) in **one vendor**. The MIT Expat core is irrevocable
  for released code, which caps the downside; the EE add-ons and ClickHouse Cloud
  pricing are not. This belongs in doc 00 §8.6's vendor-risk column.
- **Phoenix's licence is the finding.** Elastic License 2.0
  [[src](https://github.com/Arize-ai/phoenix)], not Apache/MIT. ELv2 restricts
  providing the software to third parties as a managed service. Doc 00 §6 lists
  Phoenix among the "adopt" options for this component; **⚠️ that recommendation needs
  a legal review before Phoenix is embedded in a product we sell.** Internal use is a
  different question from redistribution. Langfuse (MIT core) and MLflow do not carry
  this constraint.
- **Braintrust and Weave price the thing we do most.** Braintrust bills **$1.50 per
  1,000 scores** on Pro [[src](https://www.braintrust.dev/pricing)]; a closed loop
  that judges even 1 % of 50M requests/month emits 500k scores — `est.` **$750/mo**
  just in score fees, and 10 % judging is $7,500/mo. Weave bills **$0.10/MB ingested**
  [[src](https://wandb.ai/site/pricing/)]; full-fidelity capture of the §7 workload is
  ~971 GB/month → `est.` **$97,075/mo** on a decimal-MB reading (⚠️ **$99,405/mo**
  if W&B's "MB" means MiB; the page does not define it — corrected 2026-09-19 from a
  flat $99,404, which multiplied *decimal* GB by *binary* MB). Neither is a criticism of the products; both
  are a statement that **their pricing models assume sampled capture, and the loop
  assumes full capture.**
- **Datadog's naming has moved.** The product pages now read "Agent Observability"
  while the docs path remains `/llm_observability/`
  [[src](https://docs.datadoghq.com/llm_observability/)]. Its **Patterns** feature —
  "automated hierarchical topic clustering" [[ibid.](https://docs.datadoghq.com/llm_observability/)]
  — is the commercial instance of §4.2, which is evidence the clustering workflow is
  considered table stakes by a large APM vendor.

### 2.4 Gateway-level capture

| Gateway | Capture | Notable for the loop |
|---|---|---|
| **LiteLLM proxy** | `success_callback` / `failure_callback` to Langfuse, OTel, Datadog, Arize, Langtrace, MLflow, Langsmith, Lunary, Sentry, Athina, Galileo, Deepeval, OpenMeter, plus S3/GCS/Azure Blob/SQS/PubSub and custom Python classes [[src](https://docs.litellm.ai/docs/proxy/logging)] | The "standard logging object" carries model, messages, responses, tokens, costs, metadata. **Redaction is first-class**: `turn_off_message_logging: True`, per-request header `x-litellm-enable-message-redaction: true`, and `redact_user_api_key_info` [[ibid.](https://docs.litellm.ai/docs/proxy/logging)]. Async callbacks (`async_log_success_event`). Widest backend fan-out of anything here |
| **Agent Router** (**formerly Envoy AI Gateway**) | Envoy-based; OpenInference tracing since v0.3 | **Renamed and re-homed: "Agent Router is the new name for Envoy AI Gateway, now an Agentic AI Foundation project"**, announced 2026-09-09 for a 2026-09-10 transfer, ⚠️ and with no breaking interface change — the blog index as re-fetched 2026-09-19 reads *"Same code, same maintainers. Same APIs"*; **the draft's verbatim quote "no CRD, API, or CLI names have changed" could not be reproduced from the fetched page**, and the per-post URL 404s, so treat the exact wording as unconfirmed [[src](https://theagentrouter.ai/docs/), [blog](https://theagentrouter.ai/blog/)]. v1.0 shipped 2026-06-23 with "a committed-stable control-plane API, 16 AI providers, an MCP gateway, multimodal support, and enterprise observability"; v0.3 (2025-08-22) added "enterprise observability with OpenInference tracing" [[blog](https://theagentrouter.ai/blog/)]. Current docs version 1.1 |
| **Kong AI Gateway** | OTel per-request span attributes + aggregated metrics for AI, MCP and A2A traffic | Token metrics, **per-request cost including cache, context-window and service-tier pricing**, latency/volume/error analytics, audit logging; **AI Sanitizer plugin for PII redaction**, semantic cache, prompt compressor, prompt guards [[src](https://developer.konghq.com/ai-gateway/)]. Data planes run in your environment, control/observability via Konnect |
| **Portkey** | automatic logging of all LLM requests | Guardrails incl. PII; optional no-logging mode; **self-estimated 20–40 ms added latency** [[src](https://portkey.ai/docs/introduction/what-is-portkey)] |

**Decision rule for the gateway plane.** Use **LiteLLM** in front of our own
endpoints, because the fan-out means the trace store choice stays reversible (doc 00
§8.6) and because its redaction switches are per-request — which is what §3.6's
egress boundary needs. Use **Agent Router** only if the deployment is already
Envoy/Kubernetes-native and wants the CRD model; note that a project that renamed and
changed foundations this month is a governance risk to weigh against Envoy's
maturity. Do **not** put any gateway in front of the customer's incumbent traffic by
default (§2.1).

### 2.5 Engine-level: vLLM and SGLang

**vLLM.** OTLP tracing ships in-tree; the core OTel packages "are bundled with vLLM…
Manual installation is not required". Enable with
`vllm serve … --otlp-traces-endpoint=$OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`; **gRPC is
the default transport**, `http/protobuf` via `OTEL_EXPORTER_OTLP_TRACES_PROTOCOL`.
W3C `traceparent`/`tracestate` propagate from the client, so an app span and the
engine span join into one trace
[[src](https://github.com/vllm-project/vllm/blob/main/examples/observability/opentelemetry/README.md)].

Reading the source rather than the docs (`meas.`, `vllm/tracing/utils.py`, Apache-2.0
[[src](https://github.com/vllm-project/vllm/blob/main/vllm/tracing/utils.py)]) gives
the exact attribute set, and it contains two findings:

```python
GEN_AI_USAGE_COMPLETION_TOKENS = "gen_ai.usage.completion_tokens"   # DEPRECATED name
GEN_AI_USAGE_PROMPT_TOKENS     = "gen_ai.usage.prompt_tokens"       # DEPRECATED name
GEN_AI_REQUEST_MAX_TOKENS / TOP_P / TEMPERATURE, GEN_AI_RESPONSE_MODEL
# "Custom attributes added until they are standardized":
GEN_AI_REQUEST_ID, GEN_AI_REQUEST_N, GEN_AI_USAGE_NUM_SEQUENCES,
GEN_AI_LATENCY_TIME_IN_QUEUE, GEN_AI_LATENCY_TIME_TO_FIRST_TOKEN,
GEN_AI_LATENCY_E2E, GEN_AI_LATENCY_TIME_IN_SCHEDULER,
GEN_AI_LATENCY_TIME_IN_MODEL_{FORWARD,EXECUTE,PREFILL,DECODE,INFERENCE}
```

1. **vLLM emits the deprecated token names** (`prompt_tokens`/`completion_tokens`),
   which semconv replaced with `input_tokens`/`output_tokens`
   [[src](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)].
   **Our Collector must normalise them**, or engine-side and client-side token counts
   will not join. This is a two-line transform-processor rule and a 100 %-guaranteed
   bug if skipped.
2. **The `gen_ai.latency.time_in_model_{prefill,decode,forward,execute}` breakdown is
   not standard and is exactly what S8 needs.** No SaaS product will surface it
   natively. It is a strong argument for keeping raw spans in our own store rather
   than only in a vendor's (§6).

vLLM's own comment is candid: these are "largely based on OpenTelemetry Semantic
Conventions but are defined here as constants so they can be used by any backend",
with names "copied … to avoid version conflicts"
[[ibid.](https://github.com/vllm-project/vllm/blob/main/vllm/tracing/utils.py)].

**SGLang.** "SGLang exports request trace data based on the OpenTelemetry Collector.
You can enable tracing by adding the `--enable-trace` and configure the OpenTelemetry
Collector endpoint using `--otlp-traces-endpoint`"; `pip install -e "python[tracing]"`;
gRPC on 4317 by default, HTTP via `OTEL_EXPORTER_OTLP_TRACES_PROTOCOL`; batching via
`SGLANG_OTLP_EXPORTER_SCHEDULE_DELAY_MILLIS` and
`SGLANG_OTLP_EXPORTER_MAX_EXPORT_BATCH_SIZE`; **the router
(`sglang_router.launch_router`) can be traced too**, so prefill/decode-disaggregated
deployments produce joined traces
[[src](https://docs.sglang.io/docs/references/production_request_trace.md)].

Its **dynamically adjustable trace level** is the operationally interesting part:

```
0: disable tracing
1: Trace important slices
2: Trace all slices except nested ones
3: Trace all slices (default)
```
[[ibid.](https://docs.sglang.io/docs/references/production_request_trace.md)]

**Decision rule:** run SGLang at level 1 in steady state and raise it to 3 for a
bounded window during an S8 optimisation pass or an incident. Level 3 as a permanent
default is span volume with no consumer — the exact waste §3.1 is about. Engine
tracing carries **no prompt content** in either engine, which is why it is a
complement to, not a substitute for, SDK/gateway capture.

### 2.6 Two conventions, not one

**OpenInference** (Arize's convention, which Phoenix and Agent Router emit) is a
parallel vocabulary, structurally different from semconv: a required
`openinference.span.kind` ∈ {`LLM`, …} discriminator, and attributes named
`llm.input_messages`, `llm.output_messages`, `llm.invocation_parameters`,
`llm.token_count.{prompt,completion,total}`,
`llm.token_count.prompt_details.{cache_read,cache_write,audio}`,
`llm.token_count.completion_details.{reasoning,audio}`, `llm.cost.{prompt,completion}`,
`llm.prompt_template.{template,variables,version}`, `llm.provider`, `llm.system`,
`input.value` / `output.value` / `input.mime_type`, `embedding.*`, `document.*`,
`image.url`, `session.id`, `session.annotations`, `session.evaluations`
(`meas.`, from the spec file
[[src](https://github.com/Arize-ai/openinference/blob/main/spec/semantic_conventions.md)]).

Two things stand out. OpenInference has **`llm.cost.prompt` / `llm.cost.completion`
natively**, which semconv does not (§1.4); and it has
**`session.annotations` / `session.evaluations` as first-class session-level fields**,
which is closer to what a closed loop needs than anything in semconv.

⚠️ **The conventions have not merged, and both are actively emitted.** Langfuse maps
both [[src](https://langfuse.com/integrations/native/opentelemetry)]; Agent Router
emits OpenInference [[src](https://theagentrouter.ai/blog/)]; MLflow and Datadog
claim native semconv support
[[src](https://mlflow.org/docs/latest/genai/tracing/), [dd](https://docs.datadoghq.com/llm_observability/)].
**Rule: normalise to semconv + `lfp.*` at the Collector, and keep the original
attributes in a raw sidecar column for one retention window.** Dropping the originals
is how you discover, three months later, that a token count you rely on came from a
convention you no longer record.

---

## 3. Storage and analytics

### 3.1 Volume: the arithmetic that decides everything else

Let `R` = requests/month, `T_in`/`T_out` = tokens, `b` ≈ 4 bytes/token for English
text (⚠️ a standard rule of thumb, not a measured constant for any specific
tokenizer; it should be measured per customer at onboarding).

```
row_bytes ≈ (T_in + T_out) · b  +  metadata (~2 KB)
month_bytes ≈ R · row_bytes  +  Σ media_bytes
```

For the repo's canonical request shape (4,000 in / 512 out, doc 00 §4.1):
`(4,512 × 4) + 2,048` = **20,096 B ≈ 19.6 KiB per text trace** (`est.`).

| R (requests/month) | Text-trace bytes/mo | At 14× compression |
|---:|---:|---:|
| 1M | 20.1 GB | 1.4 GB |
| 10M | 201 GB | 14.4 GB |
| **50M** | **1.00 TB** | **72 GB** |
| 500M | 10.0 TB | 718 GB |

The 14× is ClickHouse's own claim for observability data: "ClickHouse compresses logs
and traces on average up to 14x" versus original JSON
[[src](https://clickhouse.com/docs/use-cases/observability/introduction)] (**vendor**).
⚠️ Prompt text is *not* the low-cardinality field set that claim is built on (HTTP
codes, service names) — for free text I would plan on 3–5× with ZSTD and treat 14× as
an upper bound until measured. The structured columns will beat 14×; the content
blobs will not, which is one more reason to separate them (§3.2).

**The number that actually decides the architecture is media.** A single 2-minute
1080p clip at ~2 Mbps is ~30 MB — **1,500× a text trace**. §7 does this arithmetic in
full; the headline is that at 2M video requests/month the clips are ~60 TB/month
against ~1 TB/month for 48M text traces.

### 3.2 Store choice

| Layer | Choice | Why | Rejected alternatives |
|---|---|---|---|
| **Structured spans + metrics** | **ClickHouse** | Column store, codecs, high-cardinality filters, `argMax`/`quantile` at scale. It is what Langfuse itself uses for traces/observations/scores [[src](https://langfuse.com/self-hosting)] — ⚠️ but Langfuse has been part of ClickHouse since January 2026 [[src](https://raw.githubusercontent.com/langfuse/langfuse/main/README.md)], so read that as a first-party choice, not third-party corroboration (§2.3) and what ClickHouse positions for observability [[src](https://clickhouse.com/docs/use-cases/observability/introduction)] | Postgres: fine to ~10M rows/mo, dies on the analytics in §4. Elasticsearch: the NVIDIA blueprint used it and the blueprint is deprecated (doc 00 §7.1) |
| **Control-plane state** (tenants, tasks, artifacts, dataset definitions, experiment config) | **Postgres** | Transactions, FKs, small. Same split Langfuse makes [[src](https://langfuse.com/self-hosting)] | Putting it in ClickHouse: no transactions, no updates |
| **Content blobs + media** | **S3-compatible, content-addressed** | $0.023/GB-mo Standard, $0.0125 Standard-IA, $0.004 Glacier Instant Retrieval, us-east-1 [[src](https://aws.amazon.com/s3/pricing/)]; the pattern the OTel spec itself recommends [[src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)] | Content in span attributes: blows every backend's payload cap and every vendor's GB-based bill (§2.3) |
| **Training/eval export** | **Parquet on object storage**, optionally Iceberg-registered | The format every trainer reads; the lakehouse layer gives snapshot isolation so a dataset version is a table snapshot | Exporting from the OLAP store at training time: couples training throughput to the query cluster |
| **Queue/cache** | Redis/Valkey | Ingest spooling and rate-limit state; again matching Langfuse's shape [[src](https://langfuse.com/self-hosting)] | — |

**The ingest ordering rule**, taken directly from Langfuse's design: **persist the raw
event to object storage before the database write**, so ingestion is recoverable and
a ClickHouse incident never loses a training example
[[src](https://langfuse.com/self-hosting)]. For a system whose data *is* the product,
this is not optional.

Managed-price anchors for sizing (`est.`, from list prices fetched 2026-09-19):
ClickHouse Cloud compute is billed per compute-unit-hour (1 unit = 8 GiB + 2 vCPU) at
**$0.39030/unit/hr** on AWS us-east-1 Enterprise → **$1,140/mo for 4 units, $4,559/mo
for 16**; storage **$25.30/TB-month** (AWS us-east-1), **$22.00** on GCP us-central1;
egress: public-internet **$0.115/GB** (AWS us-east-1) and **$0.114/GB** (GCP
us-central1), inter-region $0.031 / $0.036 (`meas.`, re-fetched 2026-09-19 —
⚠️ the draft's **$0.151** upper bound does not appear on the page and is withdrawn)
[[src](https://clickhouse.com/pricing)].

### 3.3 Retention: four clocks, not one

The mistake is a single retention number. Four classes of data have genuinely
different economics and different legal exposure:

| Class | Default retention | Driver | Cost at §7 scale (`est.`) |
|---|---|---|---|
| **Structured span rows** | 13 months | Drift baselines and year-over-year seasonality need >12 months (§4.3) | ~72 GB/mo compressed → **$23/mo of ClickHouse storage in month 13** |
| **Content blobs (text)** | 90 days hot, then Glacier IR or delete | The annotation sample is drawn within days; unsampled prompts are dead weight | Standard → IA is a 1.84× saving, IA → GIR a further 3.1× [[src](https://aws.amazon.com/s3/pricing/)] |
| **Media (video/image)** | `ref_only` by default; `platform_copy` only for promoted items | 1,500× the size of a text trace (§3.1) | §7: $1,380/mo Standard vs **$240/mo Glacier IR** for 60 TB |
| **Derived datasets + eval sets** | **Indefinite, immutable** | Doc 00 §8.4: an eval-set refresh invalidates every historical score, so the old version must remain readable | Tiny |

**Deletion is a contract problem, not a storage problem.** Doc 00 §8.2 open question
12 stands: a trace deleted from the store is still in the checkpoint it trained. What
observability *can* deliver — and must, for the DPA to be signable — is: (a) a
tenant-scoped hard-delete that provably removes rows, blobs and derived dataset items
within an SLA; (b) **an audit record of which dataset versions and which training runs
consumed that trace**, so the retrain obligation is at least *computable*. That
lineage (§5.1) is the deliverable; un-training is not.

### 3.4 Indexing: three access patterns, three indexes

| Pattern | Query | Index |
|---|---|---|
| **Operational** | "p99 TTFT for task X on artifact Y, last hour, by arm" | ClickHouse `ORDER BY (tenant_id, task_id, ts)`, with `endpoint_role`/`arm` as low-cardinality columns |
| **Lexical** | "every trace containing this error string / this tool name / this customer id" | ClickHouse token/ngram bloom-filter skip indexes on the text columns; **not** a general-purpose search engine |
| **Semantic** | "traces that mean the same thing as this one"; topic discovery; near-dup detection | A separate **embedding table**: `(trace_id, model_id, vector)` with ANN search |

**Embedding discipline.** Embed *the user turn plus the rendered task-relevant
context*, never the whole prompt stack — a 15k-token system prompt shared by all
traffic dominates the vector and every request looks identical. Version the
embedding model id in the row: re-embedding is the most expensive routine operation
in this component, and a silently swapped embedding model invalidates every stored
cluster and every drift baseline at once.

⚠️ Whether to run ANN inside ClickHouse or in a dedicated vector store is
**deployment-dependent and was not resolved here** — I did not obtain a primary
source benchmarking ClickHouse vector search at ~50M vectors/month. The bias should
be toward one fewer system.

### 3.5 Dedup: two different jobs

**Exact dedup** is free if blobs are content-addressed (§1.5). Retries, health
checks, replayed requests and the shared system prompt collapse to one object. This
is the same mechanism Langfuse uses — "File uniqueness determined by project, content
type, and content SHA256 hash" [[src](https://langfuse.com/docs/tracing-features/multi-modality)].

**Semantic dedup** is the one that affects model quality, and there are two
independent published reasons to do it:

- **Training efficiency.** SemDeDup "can remove 50% of the data with minimal
  performance loss, effectively halving training time", with out-of-distribution
  performance actually improving, by clustering pre-trained embeddings and dropping
  near-duplicates [[src](https://arxiv.org/abs/2303.09540)]. Production traffic is
  *far* more redundant than web-scale corpora — the same intent recurs thousands of
  times a day — so the removable fraction should be higher, not lower. ⚠️ Inferred;
  the paper measured web data, not production LLM traffic.
- **Privacy.** "A sequence that is present 10 times in the training data is on
  average generated ~1000 times more often than a sequence that is present only
  once", and existing privacy attacks "have near-chance accuracy on non-duplicated
  training sequences" [[src](https://arxiv.org/abs/2202.06539)]. **Deduplication is
  therefore a privacy control, not only an efficiency one** — directly relevant to
  doc 00's I6 and §8.2, and worth stating in the customer's DPA.

**Where to apply it.** Dedup at *dataset build* (S4), not at ingest. Duplicate rate is
itself a signal — a spike in near-identical requests is either a retry storm, a broken
client, or a bot, and all three matter operationally. Delete the duplicates from the
training set, keep the counts in the trace store.

### 3.6 PII: detection, redaction, and the egress boundary

Doc 00 §8.2's rule is architectural: *redact before egress, not before
storage-and-then-egress*. Making that real needs three layers.

**Layer 1 — never capture it.** The cheapest PII is the PII that was never recorded.
LiteLLM's `turn_off_message_logging` and per-request
`x-litellm-enable-message-redaction` header [[src](https://docs.litellm.ai/docs/proxy/logging)]
let a customer mark whole routes as content-free while still capturing tokens, cost
and latency. Per-route opt-out is a prerequisite for signing an enterprise DPA, and
it costs us only the training examples from that route.

**Layer 2 — redact in the telemetry pipeline.** The OTel Collector's **redaction
processor** deletes attributes not on an allowlist and masks values matching
blocklist regexes, with `allowed_keys`, `ignored_keys`, `blocked_key_patterns`,
`blocked_values`, `allowed_values`, `url_sanitizer`, and hashing via MD5/SHA1/SHA3/
HMAC-SHA256/HMAC-SHA512 instead of fixed-string masking; it supports traces, logs and
metrics, and has a `summary` parameter for an audit trail. **Stability: Beta for
traces, Alpha for logs and metrics**
[[src](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/redactionprocessor)].

**Decision rule:** use the redaction processor as a *backstop* for structured
attributes (keys, URLs, ids), not as the primary content scrubber. An allowlist
approach that strips everything unknown is exactly wrong for our content attributes,
which are unbounded free text. Use HMAC hashing rather than masking wherever the
value must remain *joinable* — a hashed user id still supports doc 00 §8.4's
"split by stable entity", while a masked one does not.

**Layer 3 — entity-level detection on content, at the egress edge.**

**Presidio** is the default, with an important 2026 governance change. The project
"is in the process of transitioning from a Microsoft-owned project to an independent,
community-governed open source project under the new GitHub organization Data Privacy
Stack"; "Presidio will continue to be open source under the MIT license"; "Microsoft
supports this transition"; and operationally, **"New Docker image releases are
published to GitHub Container Registry under the Data Privacy Stack organization.
Legacy Microsoft Container Registry (MCR) images remain available for older tags but
are no longer updated; update `mcr.microsoft.com/presidio-*` image references to
`ghcr.io/data-privacy-stack/presidio-*`"**
[[src](https://github.com/data-privacy-stack/presidio/blob/main/docs/project_transition.md)].
`microsoft.github.io/presidio` and `github.com/microsoft/presidio` both redirect to
the new home (`meas.`, observed 2026-09-19). Licence MIT, ~10.9k stars
[[src](https://github.com/microsoft/presidio)].

Capabilities: NER, regex, rule-based logic, checksum validation and context
enhancement; text (Analyzer), images (Image Redactor, with OCR), and
structured/semi-structured data (Presidio Structured); out-of-the-box entities
include credit cards, names, locations, SSNs, crypto wallets, phone numbers and
financial data [[src](https://presidio.dataprivacystack.org/)]. And the caveat the
project states itself, which belongs verbatim in our customer documentation:

> "Presidio can help identify sensitive/PII data in un/structured text. However,
> because it is using automated detection mechanisms, there is no guarantee that
> Presidio will find all sensitive information. Consequently, additional systems and
> protections should be employed."
> [[src](https://presidio.dataprivacystack.org/)]

*(Note on sourcing: a WebFetch summary of the Presidio docs site asserted the project
was "originally developed by Anthropic". That is false — the transition document and
the repo redirect both establish Microsoft as the originating owner. Recorded here as
a caution that summarised fetches can hallucinate attribution; every provenance claim
in this document was taken from a primary artifact, not a summary.)*

**LLM-based redaction** is the complement, not the replacement: it catches
contextual PII that no entity recogniser has a pattern for ("the patient in room
4B", "my manager Dave's salary"). Several vendors ship it — Opik's guardrails cover
"content, policy violations, and PII protection"
[[src](https://www.comet.com/site/products/opik/)]; Kong ships an **AI Sanitizer**
plugin [[src](https://developer.konghq.com/ai-gateway/)]; Portkey guardrails include
PII [[src](https://portkey.ai/docs/introduction/what-is-portkey)]; Lunary sells PII
masking on Enterprise [[src](https://lunary.ai/pricing)]; Datadog scans and redacts
automatically [[src](https://docs.datadoghq.com/llm_observability/)]. Doc 00's advice
holds — *do not write a PII detector*.

**The pipeline that satisfies I6**, in order:

```
capture (raw, tenant-encrypted, in-region)
  → Presidio + regex + checksum          → pii_entities[], redaction_status
  → LLM-based contextual pass (sampled)   → escalation queue for low-confidence spans
  → egress gate: redaction_status == 'redacted' AND tenant.teacher_policy allows
  → teacher / judge call (S3)
```

The gate is a hard predicate in code, evaluated on every row that leaves the tenant
boundary, and its decisions are logged with the policy version. Doc 00 §8.1's
per-tenant teacher policy ("which teacher produced which label, under whose
agreement") is enforced *here*, in the observability layer, because this is the only
place that sees every byte before it leaves.

**Consent and regional constraints.** Three requirements that shape the schema, not
just the ops:

1. **Residency is a storage-class decision made at ingest**, not a filter at query
   time. Regional isolation of the trace store, the blob store *and* the teacher
   endpoint. The precedent exists commercially: Langfuse operates EU/US/JP and a
   separate HIPAA cloud with distinct OTLP endpoints
   [[src](https://langfuse.com/integrations/native/opentelemetry)]; Lunary hosts in
   Europe with a GDPR posture [[src](https://lunary.ai/pricing)].
2. **Consent is per-route and per-purpose**, carried on the trace
   (`lfp.consent.capture`, `lfp.consent.train`, `lfp.consent.teacher_egress`), because
   a customer will consent to capture-for-debugging long before capture-for-training.
   Doc 00 §1.1 notes end users "never opt into being an experiment subject"; the
   platform cannot fix that, but it can make the customer's own consent state a
   queryable property of every training example.
3. **⚠️ Whether end-user consent for *model training* on their inputs is validly
   obtained by the customer's existing terms is a legal question this document cannot
   answer**, and it is the customer's liability, not ours. Doc 08 owns it. The
   observability layer's obligation is to make the answer *enforceable* once given.

---

## 4. Analysis workflows that feed the loop

This section is where observability stops being ops and starts being the data engine.
Doc 00 §7.2's conclusion is the design brief: **mine the disagreements, not the
average** — the platform's equivalent of an autonomous-driving disengagement.

### 4.1 Failure mining

Six detectors, cheapest first. All run as scheduled ClickHouse queries over
`trace_span ⋈ signal`; none needs a model.

| Detector | Query shape | Precision | Cost |
|---|---|---|---|
| **Explicit negative feedback** | `signal.kind='thumb' AND value<0` | high, tiny recall | free |
| **Retry / regenerate** | same `session_id`, ≥0.9 cosine similarity to the previous turn's input, within 120 s | high | one embedding per turn |
| **Escalation** | routed to the incumbent, or to a human | **highest** | free |
| **Refusal / anomalous stop** | `finish_reason` in the refusal set, or `length` truncation | high for the truncation case | free |
| **Structured-output failure** | `schema_valid = false` | **perfect precision** — doc 00 §2.3 calls this a hard failure, not a quality regression | free (computed at ingest, §1.2c) |
| **Latency outlier** | `ttft_ms > p99(task, artifact, hour)` | medium; often infra, not model | free |

Two model-based detectors are worth the money on a sampled basis:

- **Uncertainty.** Semantic entropy — clustering multiple samples by meaning before
  computing entropy, because "different sentences can mean the same thing" — is "more
  predictive of model accuracy on question answering data sets than comparable
  baselines", unsupervised and needing no model modification
  [[src](https://arxiv.org/abs/2302.09664)]. **Mechanism for us:** for a sampled
  slice, draw k=5 samples from the *student* at temperature>0, embed, cluster, compute
  entropy over clusters. **Cost:** 5× generation on a sampled slice — which is our
  own cheap GPU, not teacher tokens. **Output:** a per-request confidence used both
  for annotation sampling (§4.4) and for the frontier-fallback route doc 00 §3.3
  recommends for long-tail reasoning. **Failure mode:** it measures *disagreement of
  the student with itself*, which is not the same as being wrong — it will be
  confidently wrong on a systematic bias inherited from the teacher.
- **Student-vs-incumbent disagreement**, on shadow traffic (§5.3). Doc 00 §5.6 item 3
  says this is what actually closes the sale: *"here are the 214 requests where the
  two models differed, and here is who was right."*

**Decision rule:** treat every detector's output as a *candidate*, and rank the union
by expected information gain (§4.4) rather than by detector. Mining each signal in its
own silo produces six redundant queues of the same 200 hard requests.

### 4.2 Clustering by intent

**Mechanism.** BERTopic's pipeline — transformer embeddings → dimensionality
reduction → clustering → class-based TF-IDF for topic representations — is the
well-documented default, framing topic modelling as clustering rather than a
probabilistic model, and the author reports it "generates coherent topics and remains
competitive across a variety of benchmarks"
[[src](https://arxiv.org/abs/2203.05794)]. The commercial instantiation exists too:
Datadog ships "automated hierarchical topic clustering" as **Patterns**
[[src](https://docs.datadoghq.com/llm_observability/)].

**Inputs:** embeddings of the user turn (§3.4). **Outputs:** a cluster id per trace, a
label per cluster, cluster size, and — the part that matters — **per-cluster quality
and cost**. **Cost:** one embedding per trace (batched, on our own GPUs, negligible
against inference) plus a periodic clustering job.

**Why the loop needs it, in three uses:**

1. **Slice definition.** Doc 00 §5.2 forbids aggregate-only claims; the clusters
   *are* the slices, and they are derived from the customer's real traffic rather
   than guessed in a kickoff meeting.
2. **Scoping the student.** A task whose traffic is 80 % in three clusters is a good
   distillation target; one spread over 200 clusters is doc 00 §3.3's "tool-use
   breadth" failure waiting to happen. **This is a qualification signal available
   before any training spend**, and it belongs in the sales motion next to doc 00
   §4.3's volume filter.
3. **Coverage accounting** (§4.5).

**Failure mode:** cluster labels drift when the embedding model changes, and every
historical slice claim silently changes meaning. Pin the embedding model version into
the cluster id; re-clustering is a new cluster *set*, with a mapping, not an update.

### 4.3 Drift detection

Three distributions drift independently and need separate monitors:

| Drift | What moves | Monitor | Action |
|---|---|---|---|
| **Prompt-stack drift** | the customer edited the system prompt or tool set | `prompt_stack_hash` changes | **Immediate.** Doc 00 §2.2: the parity claim is void. Alert, freeze promotion, re-baseline |
| **Input distribution drift** | traffic mix moved | two-sample test on embeddings (below) | Re-sample for annotation; check per-cluster quality |
| **Output distribution drift** | the model changed, or collapsed | length, entropy, distinct-n, refusal rate, tool-call rate | Doc 00 §8.3: "collapse shows up as narrowing before it shows up as a score drop" |

**Method, with a source.** "Failing Loudly" evaluated dataset-shift detectors across
many perturbations and found that **two-sample testing on a dimensionality-reduced
representation, using a pre-trained classifier for the reduction, outperformed the
alternatives**, while "domain-discriminating approaches tend to be helpful for
characterizing shifts qualitatively and determining if they are harmful"
[[src](https://arxiv.org/abs/1810.11953)].

**Translated into our pipeline:**

- **Reduction:** the embedding of the user turn (§3.4) — the modern stand-in for the
  paper's pre-trained-classifier reduction.
- **Test:** a multivariate two-sample test (or per-dimension KS with a multiple-
  comparison correction) between a fixed reference window (the training window) and a
  rolling current window.
- **Characterisation:** train a cheap domain classifier reference-vs-current; if it
  separates them well, its most important features name *what* moved — which is
  precisely the paper's qualitative branch and what turns an alert into a work item.
- **Harm test, and this is the one that matters:** drift is only actionable if
  quality moved. **Always pair the distribution alert with the per-cluster quality
  delta.** A traffic-mix shift into a cluster the student is good at is good news.

⚠️ **The monitor's own baseline is the weak point.** A rolling reference window
absorbs slow drift and never fires; a frozen one fires forever after any legitimate
product change. The practical answer is two monitors — frozen-vs-current (has the
world moved since training?) and rolling (did something break *today*?) — with
different thresholds and different owners. ⚠️ Thresholds are unsourced here; they must
be calibrated per tenant against a period of known-good traffic.

### 4.4 Sampling for annotation

This is the highest-leverage decision in the whole component, because annotation cost
is one of the three one-time costs in doc 00 §4.3 and the *only* one the platform
controls directly.

**The three classical strategies, with sources:**

| Strategy | Mechanism | Source | Weakness alone |
|---|---|---|---|
| **Uncertainty** | pick where the model is least confident | semantic entropy [[src](https://arxiv.org/abs/2302.09664)] | In a *batch*, picks near-identical hard examples |
| **Diversity / coverage** | pick a set that covers the space — k-center over embeddings, "choosing set of points such that a model learned over the selected subset is competitive for the remaining data points" | core-set [[src](https://arxiv.org/abs/1708.00489)] | Ignores difficulty; spends budget on easy-but-distinct items |
| **Both at once** | BADGE selects "groups of points that are disparate and high-magnitude when represented in a hallucinated gradient space", combining uncertainty and diversity **without hyperparameter tuning**, and "consistently performs as well or better" across batch sizes and architectures | [[src](https://arxiv.org/abs/1906.03671)] | Needs gradient embeddings from the student — available to us, since we own the student |

**The recommended policy is a stratified budget, not a single strategy.** For an
annotation budget of N examples per round:

| Share | Stratum | Selector | Rationale |
|---:|---|---|---|
| 40 % | **Mined failures** (§4.1) | rank by detector confidence × cluster frequency | Doc 00 §7.2: disagreements are the disengagements |
| 25 % | **Uncertainty × diversity** | BADGE-style over the student's gradient embeddings | The classical active-learning core |
| 20 % | **Stratified by cluster**, proportional to traffic | random within cluster | Keeps the training set representative; without it the student is trained only on hard cases and regresses on the easy 80 % |
| 10 % | **Rare/tail clusters**, oversampled | random within small clusters | Doc 00 §3.3: rare hard cases are rare by construction |
| 5 % | **Pure random** | uniform | **The unbiased estimator.** The only stratum from which an honest estimate of production quality can be computed; everything else is selection-biased |

**That last 5 % is non-negotiable and is routinely omitted.** Without a uniform
sample, every quality number the platform reports is conditioned on its own selection
policy, and the dashboards become unfalsifiable.

**Three compounding constraints:**

1. **Selection bias in the eval.** Items selected by uncertainty must not enter the
   *test* split, or the test set is adversarially hard by construction and the
   non-inferiority claim (doc 00 §5.2) is measuring the wrong thing. **Rule: the test
   split is drawn from the uniform stratum and from stratified-by-cluster only.**
2. **Cost.** Judge-based scoring runs "$0.01-0.10 per assessment" (**vendor**,
   Langfuse's own guidance [[src](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge)]).
   At 50M requests/month: judging 0.1 % = `est.` **$500–$5,000/mo**; 1 % =
   **$5,000–$50,000/mo**; 100 % = **$500k–$5M/mo**. Teacher *labelling* is a separate
   and larger unit cost: 100k examples at 4K in/512 out against GPT-6 Astra is
   **$6,560, or $3,280 batched** (doc 00 §4.3a). **Decision rule: judge a stratified
   1 % continuously; teacher-label only what the sampler selects.**
3. **Class imbalance in the failure strata.** If the student is at 95 % quality, a
   uniform sample yields 1 failure in 20. Mining is what makes the annotation budget
   land on the 5 %; stratified sampling is what stops the student forgetting the 95 %.

### 4.5 Coverage metrics

Four numbers, reported per task, that answer "do we have enough data, and of the
right kind?" — the question a customer asks before approving a training round.

| Metric | Definition | Decision it drives |
|---|---|---|
| **Cluster coverage** | share of traffic in clusters with ≥ k annotated examples (k ≈ 30) | Where to spend the next annotation round |
| **Tail mass** | share of traffic in clusters below the size threshold | High tail mass ⇒ doc 00 §3.3's long-tail failure; consider capping the student's scope and routing the tail |
| **Slice power** | per slice, the achievable non-inferiority margin δ at 80 % power given the current annotated n, from doc 00 §5.3's table | **Turns "we need more data" into a number and a price.** This is the single most useful thing to put on the customer dashboard |
| **Feedback density** | share of traces carrying any outcome signal (§1.2g) | Below ~1 %, judge validity (doc 00 §5.1) is load-bearing and must be budgeted; above ~10 %, hunt for the outcome signal instead of building a rubric |

⚠️ The k ≈ 30 and the 1 %/10 % thresholds are **my heuristics, not sourced**. The
slice-power metric is the one that is actually derived — from doc 00 §5.3's
`n ≈ (z_{1−α}+z_{1−β})² · 2p(1−p)/δ²`.

---

## 5. Linking traces to evals and datasets

### 5.1 The lineage chain

The property that must hold end-to-end:

> **Given any production response, the platform can name the artifact that produced
> it, the training run that produced that artifact, the dataset version that fed the
> run, the dataset items in it, the traces those items came from, the annotation that
> labelled them and the judge version that scored them — and can walk the chain in
> both directions.**

Forwards, this answers "which customers' models were trained on this trace?" (the
deletion obligation, §3.3). Backwards, it answers "why did the model say this?" —
which is the only defensible response to a customer incident, and doc 00 §8.5's
"every production incident becomes a test case" is unimplementable without it.

Concretely, six immutable ids and five join tables:

```
trace_id ──► dataset_item_id ──► dataset_version_id ──► training_run_id
                   │                      │                    │
                   └── annotation_id ─────┘                    ▼
                          │                            checkpoint_id
                          └── judge_run_id                     │
                                 └── judge_model_version       ▼
                                                        artifact_id  (quantised, engine-pinned)
                                                               │
                                                               ▼
                                                        endpoint_binding (main|dev|shadow, from_ts, to_ts)
```

`artifact_id` → `endpoint_binding` is what makes the gate-twice rule (doc 00 §1.2)
auditable: the gate result is attached to the `artifact_id`, not the `checkpoint_id`,
so a promotion of an un-gated quantisation is a **schema violation rather than a
process failure**. That distinction is the difference between a control that works and
a policy nobody follows.

### 5.2 Versioned datasets

The mechanics are commodity and should be adopted rather than designed. Langfuse's
model is the clearest primary source: dataset items link to a trace (and optionally a
specific observation), and **"Every `add`, `update`, `delete`, or `archive` of dataset
items produces a new dataset version"**, tracked by timestamp so a dataset's state at
any point is retrievable and experiments can be re-run against historical versions
[[src](https://langfuse.com/docs/evaluation/dataset-runs/datasets)]. Phoenix frames
the same primitives as a test suite: datasets are "collections of examples that
provide the `inputs` and, optionally, expected `reference` outputs", and dataset
evaluators "serve as **test cases** that automatically score outputs when running
experiments—forming an evaluation harness similar to a unit test suite"
[[src](https://arize.com/docs/phoenix/datasets-and-experiments/overview-datasets)].

**Four things the platform must add on top**, none of which these tools provide:

1. **Contamination-safe splitting.** Doc 00 §8.4: split by stable entity (user,
   session, document), never by row; hash-dedup near-duplicates *across* splits
   (§3.5); timestamp the splits so test is strictly later than train where the task is
   temporal. **A dataset version is invalid unless it records its split predicate.**
2. **Cut-off time for late signals.** §1.2g: every dataset version states the
   timestamp at which outcome signals were joined, or reruns are irreproducible.
3. **The prompt stack is part of the item.** Doc 00 §2.2 makes the prompt stack part
   of the versioned artifact; a dataset item that stores only the user turn trains a
   student for a different task than the one that will be served.
4. **Frozen test sets are write-once.** Doc 00 §8.4: an eval-set refresh invalidates
   every historical score. Enforce by making the test split immutable and forcing a
   *new* eval-suite id on refresh, with historical scores labelled by suite id.

### 5.3 Replay and shadow mode

Doc 00 §5.4 makes shadow-first the recommended default rollout mode, for a reason
that is pure statistics: a 1-in-10,000 failure needs ~30,000 samples to see three,
so aggregate A/B will never find it, while shadow traffic gets **full coverage at zero
user risk and costs only compute**.

Three mechanisms, with different properties:

| Mechanism | How | Sees | Cost | Risk |
|---|---|---|---|---|
| **Offline replay** | re-run stored traces against a candidate, from the trace store | everything recorded; deterministic; repeatable | our GPU only | **Stale tool results.** Replaying an agent trajectory against live tools is non-deterministic and often destructive — doc 00 §5.4: mock the tool layer deterministically so the eval measures the model, not the flaky API behind tool #4 |
| **Async shadow (from the trace stream)** | consume the trace topic, fire the candidate, store both, diff | real traffic, real distribution, near-real-time | our GPU + storage | None to the user; a few seconds of lag |
| **Inline mirroring (proxy)** | gateway duplicates the live request | true concurrency and true latency conditions | our GPU + gateway | **Side effects.** A mirrored request that calls a tool that charges a credit card charges it twice |

**The primary source on inline mirroring** is Envoy's `RequestMirrorPolicy`, and its
documented semantics are exactly right for this use: the router is **"fire and
forget"** — the proxy does not wait for the shadow cluster before returning the
primary response; **"All normal statistics are collected for the shadow cluster making
this feature useful for testing"** while the shadow *response* is discarded; the
host/authority header is altered so that **`-shadow` is appended** (disable via
`disable_shadow_host_suffix_append`); the sampled fraction is set by `runtime_fraction`
(all requests if unspecified); and the caveats are that shadowing is not triggered if
the primary cluster does not exist and that **HTTP CONNECT and upgrades are not
supported** [[src](https://www.envoyproxy.io/docs/envoy/latest/api-v3/config/route/v3/route_components.proto)].

**Recommended default: async shadow from the trace stream, not inline mirroring.**
The reasons are ours, not Envoy's: (a) the shadow response must be *stored and
diffed*, and Envoy discards it; (b) inline mirroring doubles the load on the
customer's critical path's fate-sharing surface; (c) async shadow works for customers
whose traffic we capture by SDK and never proxy (§2.1); (d) tool side effects are
avoidable only when we control the replay harness. **Use inline mirroring only when
the question is specifically about latency-under-real-concurrency**, which async
shadow cannot answer.

**Shadow economics, and the reason this is affordable.** Shadowing 100 % of traffic
means serving 100 % of traffic twice. For the repo's five students that is
$0.0092–$2.38 per blended 1M tokens ([`matrix/cost-matrix.md`](../matrix/cost-matrix.md),
doc 00 §4.2) — i.e. the shadow arm of a Qwen3.8-27B candidate costs $0.0602/1M against
an incumbent at $8.31/1M. **Shadowing the student is ~0.7 % of what the incumbent
traffic already costs.** It is the cheapest confidence the platform can buy, and doc
00 §5.6 says it is the item that does the most selling. The constraint is GPU
*capacity*, not token cost — see [`scaling/04-throughput-and-utilization.md`](../scaling/04-throughput-and-utilization.md)
and doc 00 §4.5 on the idle-GPU floor.

**What must be recorded for a shadow diff to be usable:** both outputs, both token
counts, both latencies, the identical input blob hash (proving the arms saw the same
bytes), the judge's verdict with the judge version, and — critically — **the
disagreement class** (semantically equivalent / different-but-both-acceptable /
candidate worse / candidate better). The last one is a human or judge label, and it is
what turns "214 requests differed" into doc 00 §5.6's clickable list.

### 5.4 What the tools support, honestly

| Capability | Langfuse | Phoenix | Braintrust | Weave | Opik |
|---|---|---|---|---|---|
| Trace → dataset item with backlink | ✅ [[src](https://langfuse.com/docs/evaluation/dataset-runs/datasets)] | ✅ [[src](https://arize.com/docs/phoenix/datasets-and-experiments/overview-datasets)] | ✅ (implied by Observe+Evaluate) | ✅ ⚠️ not confirmed on the page fetched | ✅ [[src](https://www.comet.com/site/products/opik/)] |
| Dataset versioning | ✅ explicit, per-mutation [[src](https://langfuse.com/docs/evaluation/dataset-runs/datasets)] | ⚠️ not stated on page fetched | ⚠️ | ⚠️ | ⚠️ |
| Run experiment vs a candidate model | ✅ | ✅ | ✅ | ✅ | ✅ |
| Judge on **production** traces, filtered + sampled | ✅ — rules stack observation filters (type, name, metadata) with trace filters (userId, sessionId, tags, version); scores attach to the matched observation; sampling and cheaper judge models are the recommended cost controls [[src](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge)] | ✅ | ✅ | ✅ | ✅ with alerts [[src](https://www.comet.com/site/products/opik/)] |
| **Shadow/mirror production traffic to a candidate** | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Statistically-framed A/B with power and margin** | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Artifact-level lineage to a quantised serving build** | ❌ | ❌ | ❌ | ❌ | ❌ |

The bottom three rows are empty across the board (⚠️ on the pages fetched; a feature
could exist and not be documented where I looked). **That emptiness is the product
thesis restated as a table**, and it agrees with doc 00's judgement that doc 07 and
the endpoint/versioning doc are builds while this component is a buy.

---

## 6. Build vs buy

### 6.1 What an OSS stack covers, and the six gaps

**The stack:** OTel SDKs + OpenLLMetry instrumentation → OTel Collector (redaction,
normalisation, tail sampling) → Langfuse (MIT core, self-hosted: ClickHouse + Postgres
+ Redis + S3) → our own analysis jobs → Parquet export.

| Loop requirement | Covered by OSS? | Gap |
|---|:--:|---|
| Capture full prompt/response/tools | ✅ | Content attributes are Opt-In by spec — must be deliberately enabled and defended [[src](https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md)] |
| Multimodal refs, not bytes | ✅ | Langfuse's S3 media with sha256 dedup [[src](https://langfuse.com/docs/tracing-features/multi-modality)]; but **no standard reference format** (spec `TODO`) so ours is bespoke |
| Cheap storage at 50M+ req/mo | ✅ | ClickHouse + S3; §7 shows the self-host bill is ~3 % of the cheapest SaaS equivalent |
| PII redaction | ✅ | Presidio (MIT) + Collector redaction processor (**Beta for traces**) [[src](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/redactionprocessor)]; neither is a guarantee [[src](https://presidio.dataprivacystack.org/)] |
| Judge scores on production traces | ✅ | Langfuse evaluators [[src](https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge)]; **judge *validation* against a gold set is ours** (doc 00 §5.1) |
| Dataset versioning + experiments | ✅ | Contamination-safe splitting, cut-off times and frozen suites are ours (§5.2) |
| **Prompt-stack hash + drift on it** | ❌ | **Gap 1.** Nothing computes it. ~50 lines at the Collector |
| **Artifact-level lineage to the served quantised build** | ❌ | **Gap 2.** The gate-twice invariant has no home in any tool |
| **Late-arriving outcome signal joins** | ❌ | **Gap 3.** Every tool models a trace as complete at write time |
| **Shadow capture + diff + disagreement classification** | ❌ | **Gap 4.** The single most sales-relevant workflow (doc 00 §5.6 item 3) |
| **Annotation sampler (stratified, uncertainty × diversity, with a uniform stratum)** | ❌ | **Gap 5.** §4.4 |
| **Per-tenant teacher-egress policy gate with audit** | ❌ | **Gap 6.** Doc 00 §8.1 — and this one is existential, not convenient |

**Six gaps, all small, all ours.** None is a platform; together they are perhaps a
quarter of one engineer-year, and they are precisely the parts a vendor cannot sell
us because they are specific to the closed loop.

### 6.2 Reference pipeline

```
  customer app ──(OTel SDK, async, batched)──┐
  our gateway (LiteLLM) ─────────────────────┤
  vLLM / SGLang (--otlp-traces-endpoint) ────┤
                                             ▼
                              ┌──────────── OTel Collector ────────────┐
                              │ 1. normalise: OpenInference→semconv,   │
                              │    vLLM's deprecated token names,      │
                              │    lfp.* promotion                      │
                              │ 2. content hook → S3 (sha256), replace │
                              │    with lfp.content.ref                 │
                              │ 3. redactionprocessor (structured)      │
                              │ 4. tail sampling (see below)            │
                              └───────────────┬────────────────────────┘
                                              ▼
                       raw event → S3 (durability first, Langfuse's ordering)
                                              ▼
           ┌──────────────── ingest worker ───────────────┐
           │ cost_usd + price_table_version               │
           │ schema_valid, prompt_stack_hash              │
           │ Presidio pass → pii_entities, redaction_status│
           │ embedding (batched, our GPU)                  │
           └───────────────┬──────────────────────────────┘
                           ▼
     ClickHouse: trace_span, embedding, signal   │  Postgres: tenants, tasks,
     S3: content_blob, media_object              │  artifacts, datasets, experiments
                           ▼
       scheduled jobs: failure mining · clustering · drift · sampler · coverage
                           ▼
              EGRESS GATE (redaction_status + tenant teacher policy, audited)
                           ▼
           S3 annotation ──► S4 datasets (Parquet/Iceberg) ──► S5 training
```

**Sampling policy, stated precisely**, because this is where most of the cost lives
and where the most damage is done:

| Class | Head sampling | Content kept |
|---|---|---|
| Errors, refusals, `schema_valid=false`, truncations | **100 %** | full |
| Any trace with a feedback signal | **100 %** | full |
| Shadow/dev-endpoint traffic | **100 %** | full |
| Latency outliers above the task p99 | **100 %** | full |
| Baseline production traffic | **100 % of structured rows**, N % of content | metadata always; content per the customer's tier |
| Health checks, synthetic probes | 0 % | none |

**The asymmetry is deliberate: sample content, never sample the row.** Structured rows
are ~2 KB and carry every metric, every cost number and every drift feature; content
is ~18 KB and is only needed for the fraction that becomes training data or gets
judged. Sampling rows destroys the denominators that make every §4 metric meaningful.

On **tail sampling**: OTel's own guidance is that it enables the right policies
(keep all error traces, filter by latency) but requires "stateful systems that can
accept and store a large amount of data", "dozens or even hundreds of compute nodes",
is prone to vendor lock-in, and carries "the indirect opportunity cost of missing
critical information" when the strategy is wrong
[[src](https://opentelemetry.io/docs/concepts/sampling/)]. **Decision rule: prefer
head-sampling on attributes we control (endpoint role, error, feedback presence) over
a stateful tail sampler.** Because we mint the trace and know at request time whether
it is shadow, dev or error-bearing, most of the tail sampler's value is available
without its cost. Revisit only if per-trace content volume forces it.

### 6.3 Buy/build verdict

| Component | Verdict | Why |
|---|---|---|
| Instrumentation + wire format | **Adopt**: OTel GenAI semconv + OpenLLMetry | Ecosystem convergence; an exit (doc 00 §8.6) |
| Collector + redaction + normalisation | **Adopt + configure** | 50 lines of YAML and two custom processors |
| Trace store + UI + datasets + judges | **Adopt: self-hosted Langfuse (MIT)** | Same architecture we would build (§3.2); §7 shows self-hosting is 4–38× cheaper than the SaaS rows at 50M req/mo; MIT core avoids the ELv2 problem (§2.3). **⚠️ New at the 2026-09-19 fact-check: Langfuse has been part of ClickHouse since January 2026** [[src](https://raw.githubusercontent.com/langfuse/langfuse/main/README.md)] — so "Langfuse + ClickHouse" is now *one* vendor, not two, and this row and the §3.2 store choice share a single concentration risk. MIT Expat is irrevocable for released code, which bounds but does not remove it |
| Raw span store for engine-level telemetry | **Build (thin)**: our own ClickHouse tables | `gen_ai.latency.time_in_model_*` has no home in a product (§2.5) |
| PII detection | **Adopt: Presidio + Collector** | Doc 00: do not write a PII detector |
| **Failure mining, clustering, drift, sampler, coverage** | **Build** | The six gaps of §6.1 |
| **Shadow capture and diff** | **Build** | Nobody ships it (§5.4) |
| **Egress gate + teacher-policy audit** | **Build** | Doc 00 §8.1 is existential |
| Cloud SaaS observability | **Avoid as the system of record** | Unit economics (§7) and doc 00 §8.6's three sunsets in twelve months. Fine as a *second* destination for a sampled slice, which OpenLLMetry's 30+ backends make nearly free [[src](https://www.traceloop.com/docs/openllmetry/introduction)] |

---

## 7. Worked example: 50M requests/month, GPT-5.6-class endpoint, with video

**Scenario.** One customer, one task, incumbent GPT-5.6 Sol ($4/$0.40/$20 per 1M,
doc 00 §2.1). 50M requests/month, of which **48M text** at the repo's canonical shape
(4,000 in / 512 out) and **2M video-understanding** requests. Video requests carry a
~2-minute clip; the model sees 240 frames ≈ 23,560 prefill tokens
([`models/marlin2b/README.md`](../models/marlin2b/README.md) §9).

All arithmetic below is `est.`, recomputed with `python3`; inputs are the sourced list
prices above.

### 7.1 Traffic shape

| Quantity | Value |
|---|---:|
| Requests/month | 50,000,000 |
| Mean requests/s | **19.3** |
| Peak at 5× diurnal | **96.5 req/s** |
| Text trace raw bytes (content + 2 KB metadata) | 20,096 B = **19.6 KiB** |
| Text rows/month | 48M × 20,096 B = **964.6 GB** |
| Video span rows (metadata + refs only, ~3 KB) | 2M × 3,072 B = **6.1 GB** |
| **Total row bytes/month** | **0.971 TB** |
| Mean ingest bandwidth | **375 KB/s**; **1.87 MB/s** at 5× peak |

Against Langfuse's published limits — 5 MB/request, 20,000 req/min on Pro+
[[src](https://langfuse.com/faq/all/api-limits)] — 96.5 req/s of traces is ~5,790
spans/min before batching, comfortably inside the bucket; the binding constraint is
the **5 MB payload cap against batch size**, i.e. ~250 full-content spans per batch,
not the rate limit.

### 7.2 Storage, self-hosted

| Item | Monthly | Note |
|---|---:|---|
| ClickHouse rows at 14× | 69 GB | vendor-claimed ratio [[src](https://clickhouse.com/docs/use-cases/observability/introduction)]; at a conservative 5× it is 194 GB |
| ClickHouse Cloud storage, month 1 | **$1.75** | $25.30/TB-mo, AWS us-east-1 [[src](https://clickhouse.com/pricing)] |
| ClickHouse storage at 13-month retention (0.90 TB accumulated) | **$22.81/mo** | The rows are *free*. This is the finding |
| ClickHouse Cloud compute, 8 units | **$2,279/mo** | $0.39030/unit-hr × 730 [[src](https://clickhouse.com/pricing)] |
| Text content blobs, S3 Standard (964.6 GB) | **$22.19/mo** | $0.023/GB-mo [[src](https://aws.amazon.com/s3/pricing/)] |
| S3 PUT, ~50M objects | **$250/mo** | $0.005/1,000 PUT [[ibid.](https://aws.amazon.com/s3/pricing/)] — **more than the storage** |
| **Subtotal, text side** | **≈ $2,575/mo** | dominated by *compute*, not data |

**Now the video, which is the whole story.**

| Clip copy policy | Volume/month | S3 Standard | Standard-IA | Glacier IR |
|---|---:|---:|---:|---:|
| `platform_copy` all, ~5 MB/clip (720p, short) | 10.0 TB | $230 | $125 | $40 |
| `platform_copy` all, **~30 MB/clip (1080p, 2 min @ 2 Mbps)** | **60.0 TB** | **$1,380** | **$750** | **$240** |
| `platform_copy` all, ~120 MB/clip (higher bitrate) | 240 TB | $5,520 | $3,000 | $960 |
| **`ref_only` + copy 2 % (sampled + promoted)** | **1.2 TB** | **$28** | — | — |

And these are *monthly accruals*: at 30 MB/clip, Standard, the cumulative bill after
12 months of retention is ~$16.6k/month, against $28/month for the `ref_only` policy
plus sampling. **§1.6's retention rule is worth ~$16k/month on one customer.** This is
the concrete reason "store refs not bytes" is an architectural invariant and not a
style preference.

### 7.3 The same workload, priced on SaaS

Assume a modest span decomposition: **5 billable units per request** (1 trace + 3
observations + 1 score) — conservative for an agentic task, aggressive for a
single-shot one.

| Vendor | Unit | Units/mo | Monthly (`est.`) | Source for the rate |
|---|---|---:|---:|---|
| **Honeycomb Pro** | events | 250M | **from $150** (Pro covers up to 750M events) | [[src](https://www.honeycomb.io/pricing)] |
| **Opik** | spans | 250M | **$12,495** ($5/100k over 100k) | [[src](https://www.comet.com/site/pricing/)] |
| **Langfuse Cloud** | units | 250M | **$15,302** + plan fee (graduated $8→$6/100k) | [[src](https://langfuse.com/pricing)] |
| **Langfuse Cloud**, 1 unit/req | units | 50M | $3,302 + plan fee | [[ibid.](https://langfuse.com/pricing)] |
| **Braintrust Pro** | GB + scores | 971 GB + 5M scores | **$2,912 data + $7,500 scores = $10,412** | [[src](https://www.braintrust.dev/pricing)] |
| **Braintrust Pro**, judging 100 % | GB + scores | 971 GB + 50M scores | $2,912 + **$75,000** | [[ibid.](https://www.braintrust.dev/pricing)] |
| **Lunary Team** | events | 250M | **$49,990** ($10/50k) | [[src](https://lunary.ai/pricing)] |
| **W&B Weave** | **ingested bytes** | 971 GB | **$97,075** ($0.10/MB decimal); ⚠️ **$99,405** on a MiB reading | [[src](https://wandb.ai/site/pricing/)] |
| **Self-hosted OSS** | — | — | **≈ $2,575 infra + $1,380 video blobs** (or **$28** with `ref_only`) | §7.2 |
| **LangSmith** | traces + LCU/LSU | — | ⚠️ **not computable** — per-trace overage not published [[src](https://www.langchain.com/pricing-langsmith)] |
| **Datadog** | ⚠️ | — | ⚠️ **not published** [[src](https://www.datadoghq.com/pricing/?product=llm-observability)] |
| **Helicone** | requests | 50M | ⚠️ **overage not published** [[src](https://www.helicone.ai/pricing)] |

**Three readings.**

1. **Self-hosting is 4–38× cheaper than the SaaS equivalents**, while being *the same
   software* in Langfuse's case — its self-host is the full product under MIT
   [[src](https://langfuse.com/self-hosting)]. Precisely (`est.`, self-host text-side
   $2,575): **4.0× vs Braintrust Pro** ($10,412), **4.9× vs Opik** ($12,495), **5.9×
   vs Langfuse Cloud at 5 units/request** ($15,302), **19.4× vs Lunary** ($49,990),
   **37.7× vs Weave** ($97,075; 38.6× on the MiB reading). Only the 1-unit-per-request Langfuse Cloud row
   ($3,302) is close, and that unit assumption is unrealistic for anything agentic.
   At 50M requests/month, self-hosting is not a preference, it is arithmetic.
2. **Metering unit matters more than list price.** Honeycomb at "from $150" and Weave
   at $97,075 are serving the same 250M events; the difference is entirely that one
   prices events and the other prices bytes, and LLM traces are fat events. **Never
   compare LLM-observability vendors on headline plan price; compute the bill on your
   own byte-and-span profile.** The 647× spread between the cheapest and dearest row
   here is the whole argument (663× on the MiB reading).
3. **Scores are a separate, larger meter.** Braintrust's $1.50/1k scores turns a
   decision to judge 100 % of traffic into a **$75,000/month** line item. §4.4's
   stratified-1 % policy is not frugality; at this volume it is the difference between
   a viable and a non-viable product.

### 7.4 Putting it against the thing being sold

The customer's incumbent spend, at GPT-5.6 Sol's $6.650 blended /1M (doc 00 §4.1):

- Text: 48M × 4,512 tok = 216.6B tokens → **$1.44M/month** (`est.`).
- Video: 2M × ~24,072 tok = 48.1B tokens → **$320k/month** (`est.`).
- **Total ≈ $1.76M/month.**

Against that, **full-fidelity observability self-hosted is ~$4.0k/month including a
30 MB/clip video copy, or ~$2.6k/month with `ref_only` — i.e. 0.15–0.23 % of the
incumbent bill.** Even the most expensive SaaS row above ($97.1k) is 5.5 %.

**The decision rule this produces, and it should go in front of the buyer on day one:**
*Observability is never the reason not to do this.* The cost objection a customer
raises will be about the GPU floor (doc 00 §4.5) or the annotation and eval budget
(doc 00 §4.3), never about trace storage. What observability *does* cost is
**engineering time and privacy risk**, and both are managed in §3.6 and §6, not on a
price list.

---

## Implications for the platform

**What to build** — small, ours, and not purchasable:

1. **The Collector normalisation + enrichment layer.** OpenInference→semconv mapping,
   vLLM's deprecated token-name fix, `lfp.*` promotion, content externalisation with
   sha256 addressing, `cost_usd` + `price_table_version` at ingest, `schema_valid`,
   `prompt_stack_hash`. Perhaps a week of work; everything downstream depends on it.
2. **The egress gate.** A hard predicate — `redaction_status == 'redacted' AND
   tenant.teacher_policy.allows(teacher, purpose)` — evaluated on every row leaving the
   tenant boundary, logged with the policy version. Doc 00 §8.1 makes this existential,
   and the observability layer is the only place that sees every byte in time.
3. **The `signal` table and its late-binding join.** Every tool models a trace as
   complete at write time; outcome signals arrive days later and are the strongest
   labels we will ever get (doc 00 §5.1).
4. **The annotation sampler** of §4.4 — stratified across mined failures, uncertainty
   × diversity, proportional clusters, oversampled tails, **and a mandatory 5 % uniform
   stratum** that is the only unbiased estimator of production quality.
5. **Shadow capture, diff and disagreement classification** (§5.3). Nobody ships it;
   doc 00 §5.6 says it is what closes the sale; it costs ~0.7 % of the incumbent's
   token spend to run.
6. **Artifact-level lineage** (§5.1) so that the gate-twice invariant is a schema
   constraint rather than a process someone remembers.
7. **The coverage dashboard**, and specifically **slice power** — the per-slice
   achievable margin δ at 80 % power given current annotated n. It converts "we need
   more data" into a number and a price, which is the conversation doc 00 §5.3 says
   the platform must be able to have.

**What to buy / adopt:**

- **OTel GenAI semantic conventions as the wire format** — with eyes open: status
  Development, content attributes Opt-In, the external-content reference format a
  `TODO`, and two token attributes already renamed once.
- **Self-hosted Langfuse (MIT core)** as trace store, UI, datasets and production
  judges. §7 shows the SaaS rows cost 4–38× more at 50M requests/month — 5.9× for
  Langfuse Cloud itself, for the same software — and its self-host architecture is the
  one we would have designed.
- **OpenLLMetry** for instrumentation breadth and a 30-backend escape hatch.
- **LiteLLM** in front of our own endpoints, for the backend fan-out and per-request
  redaction switches.
- **Presidio (MIT, now Data Privacy Stack) + the Collector redaction processor** for
  PII. Update `mcr.microsoft.com/presidio-*` image references to
  `ghcr.io/data-privacy-stack/presidio-*`.
- **ClickHouse + S3 + Postgres**, with raw events persisted to object storage before
  the database write.

**What to avoid:**

- **A gateway in the customer's incumbent production path by default.** 20–40 ms of
  self-declared added latency fails doc 00's MVP criterion 1 and makes us a SPOF on
  their revenue before we have earned anything.
- **Sampling trace *rows*.** Sample content; never sample rows, or every denominator
  in §4 becomes a guess.
- **Truncating prompts or responses.** A lossy trace is a debugging artifact; a
  training example must be byte-exact.
- **Storing media bytes in the trace.** §7: `ref_only` versus a 30 MB/clip copy is
  ~$16k/month on one customer.
- **Buying an LLM-observability SaaS as the system of record at scale**, and
  especially **byte-metered or score-metered plans** when the loop's whole premise is
  full capture and continuous judging.
- **Embedding Phoenix in the product without a legal review.** ELv2, not OSI open
  source — ⚠️ doc 00 §6 lists it as an adopt candidate and that needs qualifying.
- **Treating the OTel mirror pages as authoritative.** They render every `gen_ai.*`
  attribute as Deprecated, which is false.
- **Mutating trace rows** to attach late signals. It makes every dataset build
  irreproducible, which silently breaks doc 00 §8.4's contamination discipline.

---

## Open questions

⚠️ Consolidated. Each names the owner.

1. **⚠️ Market coverage, again.** This session had **no WebSearch** (200/200 exhausted
   before start). §2 covers the tools the brief named plus what their pages linked to.
   The same gap doc 00 open question 1 records. *Owner: doc 09, with search available.*
2. **⚠️ Datadog LLM/Agent Observability pricing.** No unit and no price on the public
   pricing page fetched. Unpriceable means unmodellable for a per-tenant cost of goods.
   *Owner: this doc, on a future pass.*
3. **⚠️ LangSmith per-trace overage** and the LCU/LSU consumption model. The pricing
   page gives $1.50/LCU and $1.00/LSU but no mapping from traces to units. *Owner:
   this doc.*
4. **⚠️ Helicone overage rate.** "10K free" plus a calculator, no published per-request
   rate. *Owner: this doc.*
5. **⚠️ Phoenix's licence versus our product.** ELv2 restricts managed-service
   redistribution. Internal use is likely fine; embedding in a sold platform is not
   obviously fine. *Owner: doc 08, with counsel.*
6. **⚠️ Real compression ratio for LLM trace content.** ClickHouse's 14× is for
   conventional observability data; free-text prompts are a different distribution. A
   day's measurement on real traffic resolves it and moves the §7 storage line by 3×.
   *Owner: this doc.*
7. **⚠️ Bytes-per-token for the customer's tokenizer.** The 4 B/token rule of thumb
   drives every volume estimate here. Measure at onboarding. *Owner: this doc.*
8. **⚠️ ANN vector search at 50M vectors/month: in ClickHouse or a separate store?**
   No primary benchmark obtained. *Owner: this doc.*
9. **⚠️ Whether the OTel content-reference `TODO` gets standardised**, and on what
   timeline. If it does, our `lfp://` scheme should migrate; if it does not, we carry a
   bespoke format forever. *Owner: this doc; watch the genai semconv repo.*
10. **⚠️ Agent Router's governance change.** Envoy AI Gateway became an Agentic AI
    Foundation project on 2026-09-10. Foundation transfers have historically preceded
    both stabilisation and abandonment; doc 00 §8.6's three sunsets counsel caution
    before a hard dependency. *Owner: doc 09.*
11. **⚠️ Drift thresholds.** No sourced default for how large a two-sample-test
    statistic must be before it means anything for *our* traffic. Must be calibrated
    per tenant on known-good traffic. *Owner: doc 07, jointly.*
12. **⚠️ Semantic-dedup removable fraction on production traffic.** SemDeDup's 50 %
    is web-scale; production traffic should be more redundant, but that is my
    inference, not a measurement. It directly sizes the training bill. *Owner: doc 03.*
13. **⚠️ Coverage heuristics.** k ≈ 30 annotated examples per cluster, 1 %/10 %
    feedback-density thresholds — mine, unsourced. *Owner: doc 04.*
14. **⚠️ End-user consent for training on captured inputs.** Whether a customer's
    existing terms validly cover it is a legal question with a per-jurisdiction
    answer. The platform's job is to make the answer enforceable per route and per
    purpose. *Owner: doc 08.*
15. **⚠️ Whether customers will accept content capture at all on their hottest
    routes.** §3.6 layer 1 lets them opt out per route, and each opt-out removes exactly
    the training data the loop needs. The commercial shape of that trade is unknown.
    *Owner: doc 00/doc 08 as a product decision.*
16. **⚠️ Multimodal payload caps across backends.** Langfuse publishes a 5 MB
    request/response limit but "The documentation doesn't specify explicit size limits"
    for individual media attachments; Datadog's SDK page documents no payload cap.
    Untested limits become 4xx storms in production. *Owner: this doc.*

---

## Sources

All fetched 2026-09-19 by WebFetch or `curl` unless the source states its own date.
**No WebSearch was available in this session** (budget exhausted before start).

**Specifications and conventions**
- OpenTelemetry GenAI semantic conventions repo (created 2026-05-05; 380★; pushed 2026-09-16) — https://github.com/open-telemetry/semantic-conventions-genai
- GenAI spans (Development; attribute requirement levels; content-capture patterns; external-storage hook; `TODO`s) — https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-spans.md
- GenAI metrics (client/server split; bucket boundaries) — https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-metrics.md
- GenAI events (`gen_ai.client.inference.operation.details`, `gen_ai.evaluation.result`) — https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/gen-ai-events.md
- GenAI conventions index (signals; provider-specific docs; MCP) — https://github.com/open-telemetry/semantic-conventions-genai/blob/main/docs/gen-ai/README.md
- opentelemetry.io GenAI page (the "moved" notice) — https://opentelemetry.io/docs/specs/semconv/gen-ai/
- opentelemetry.io attribute registry mirror (renders all `gen_ai.*` as Deprecated; deprecated-attribute list) — https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/
- OpenTelemetry sampling concepts (head vs tail; tail-sampler caveats) — https://opentelemetry.io/docs/concepts/sampling/
- OTel Collector redaction processor (Beta for traces) — https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/redactionprocessor
- OpenInference semantic conventions — https://github.com/Arize-ai/openinference/blob/main/spec/semantic_conventions.md

**Engines and gateways**
- vLLM OpenTelemetry example — https://github.com/vllm-project/vllm/blob/main/examples/observability/opentelemetry/README.md
- vLLM `SpanAttributes` source (deprecated token names; `gen_ai.latency.*`) — https://github.com/vllm-project/vllm/blob/main/vllm/tracing/utils.py
- SGLang production request tracing (`--enable-trace`, trace levels 0–3, router tracing) — https://docs.sglang.io/docs/references/production_request_trace.md
- LiteLLM proxy logging (integration list; redaction switches; async callbacks) — https://docs.litellm.ai/docs/proxy/logging
- Agent Router docs (formerly Envoy AI Gateway; v1.1) — https://theagentrouter.ai/docs/
- Agent Router blog (rename 2026-09-09/10; v1.0 2026-06-23; v0.3 2025-08-22 OpenInference tracing) — https://theagentrouter.ai/blog/
- Kong AI Gateway (OTel spans, token/cost analytics, AI Sanitizer) — https://developer.konghq.com/ai-gateway/
- Portkey (gateway features, guardrails, 20–40 ms added latency) — https://portkey.ai/docs/introduction/what-is-portkey
- Envoy `RequestMirrorPolicy` (fire-and-forget, `-shadow` suffix, `runtime_fraction`, caveats) — https://www.envoyproxy.io/docs/envoy/latest/api-v3/config/route/v3/route_components.proto

**Observability / eval platforms**
- Langfuse pricing (plans, units, overage, retention) — https://langfuse.com/pricing
- Langfuse self-hosting (ClickHouse/Postgres/Redis/S3; 90B+ observations/mo; MIT core) — https://langfuse.com/self-hosting
- Langfuse OTel ingestion (endpoints, attribute precedence, HTTP-only) — https://langfuse.com/integrations/native/opentelemetry
- Langfuse API limits (5 MB payload; per-plan rate limits) — https://langfuse.com/faq/all/api-limits
- Langfuse multimodality (S3 media, sha256 dedup, MIME list) — https://langfuse.com/docs/tracing-features/multi-modality
- Langfuse datasets (trace links, per-mutation versioning) — https://langfuse.com/docs/evaluation/dataset-runs/datasets
- Langfuse LLM-as-a-judge on production (filters, score attachment, $0.01–0.10/assessment) — https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge
- Arize Phoenix docs — https://arize.com/docs/phoenix
- Phoenix datasets and experiments — https://arize.com/docs/phoenix/datasets-and-experiments/overview-datasets
- Phoenix repo (Elastic License 2.0; 11.5k★) — https://github.com/Arize-ai/phoenix
- LangSmith pricing — https://www.langchain.com/pricing-langsmith
- Braintrust pricing (GB + scores) — https://www.braintrust.dev/pricing
- W&B Weave docs — https://docs.wandb.ai/weave/
- W&B pricing ($0.10/MB Weave ingestion) — https://wandb.ai/site/pricing/
- Helicone pricing — https://www.helicone.ai/pricing
- OpenLLMetry introduction (OTel-based; 30+ backends) — https://www.traceloop.com/docs/openllmetry/introduction
- Datadog LLM/Agent Observability docs (semconv support; Patterns clustering; auto-redaction) — https://docs.datadoghq.com/llm_observability/
- Datadog LLM Observability SDK (`DD_LLMOBS_SAMPLE_RATE`; annotation APIs) — https://docs.datadoghq.com/llm_observability/instrumentation/sdk/
- Datadog pricing (LLM Observability row unpriced) — https://www.datadoghq.com/pricing/?product=llm-observability
- Honeycomb pricing (events-based; Telemetry Pipeline $0.10/GB) — https://www.honeycomb.io/pricing
- Lunary pricing — https://lunary.ai/pricing
- Opik product page — https://www.comet.com/site/products/opik/
- Comet/Opik pricing (per-span; retention upgrades) — https://www.comet.com/site/pricing/
- MLflow GenAI tracing (semconv native; async logging; sampling) — https://mlflow.org/docs/latest/genai/tracing/

**Storage, privacy, infrastructure**
- ClickHouse observability use case (up to 14× compression) — https://clickhouse.com/docs/use-cases/observability/introduction
- ClickHouse Cloud pricing (compute-unit-hour; $/TB-month; egress) — https://clickhouse.com/pricing
- AWS S3 pricing (Standard/IA/Glacier IR; PUT/GET) — https://aws.amazon.com/s3/pricing/
- Presidio docs (engines, modalities, accuracy caveat) — https://presidio.dataprivacystack.org/
- Presidio project transition (Microsoft → Data Privacy Stack; MIT; registry move) — https://github.com/data-privacy-stack/presidio/blob/main/docs/project_transition.md
- Presidio repo (MIT; redirect from microsoft/presidio) — https://github.com/microsoft/presidio

**Papers**
- Failing Loudly: An Empirical Study of Methods for Detecting Dataset Shift (Rabanser, Günnemann, Lipton; 2018-10-29, rev. 2019-10-28) — https://arxiv.org/abs/1810.11953
- Deep Batch Active Learning by Diverse, Uncertain Gradient Lower Bounds (BADGE; Ash et al.; 2019-06-09, ICLR 2020) — https://arxiv.org/abs/1906.03671
- Active Learning for Convolutional Neural Networks: A Core-Set Approach (Sener & Savarese; 2017-08-01, ICLR 2018) — https://arxiv.org/abs/1708.00489
- SemDeDup: Data-efficient learning at web-scale through semantic deduplication (Abbas et al.; 2023-03-16) — https://arxiv.org/abs/2303.09540
- Deduplicating Training Data Mitigates Privacy Risks in Language Models (Kandpal, Wallace, Raffel; 2022-02-14) — https://arxiv.org/abs/2202.06539
- BERTopic: Neural topic modeling with a class-based TF-IDF procedure (Grootendorst; 2022-03-11) — https://arxiv.org/abs/2203.05794
- Semantic Uncertainty: Linguistic Invariances for Uncertainty Estimation in NLG (Kuhn, Gal, Farquhar; 2023-02-19, ICLR 2023 Spotlight) — https://arxiv.org/abs/2302.09664
- RouteLLM: Learning to Route LLMs with Preference Data (Ong et al.; 2024-06-26, rev. 2025-02-23) — https://arxiv.org/abs/2406.18665
- The RealHumanEval (cited via doc 00 §5.1) — https://arxiv.org/abs/2404.02806

**This repository**
- [`research/platform/00-goal-and-problem-statement.md`](00-goal-and-problem-statement.md) — the loop, stages S1–S9, invariants I1–I7, economics, the confidence crux
- [`research/METHODOLOGY.md`](../METHODOLOGY.md) — blended-cost definition and price tiers
- [`research/models/marlin2b/README.md`](../models/marlin2b/README.md) — 240-frame cap, ~23,560 prefill tokens, the Path A/B token-budget fork (1.917× at 23,560; the README's own 1.914× is 23,520 — see §1.6)
- [`research/matrix/cost-matrix.md`](../matrix/cost-matrix.md) — per-model per-GPU $/1M used in §5.3's shadow economics
- [`research/scaling/03-concurrency-and-admission-control.md`](../scaling/03-concurrency-and-admission-control.md), [`04-throughput-and-utilization.md`](../scaling/04-throughput-and-utilization.md), [`08-reliability-and-operations.md`](../scaling/08-reliability-and-operations.md)

---

## Verification log (2026-09-19)

Adversarial fact-check of this document. Every claim below was checked by opening
the **primary** source (vendor pricing page, arXiv abstract page, repo file, GitHub
API, or the referenced `research/` file) — never by trusting the citation as
written. Derivations were recomputed with `python3`. **25 claims checked: 18
CONFIRMED, 6 CORRECTED, 1 UNVERIFIABLE.** Edits are in place above and are marked
inline where they change a number or a verdict.

### CONFIRMED (18)

| # | Claim | Source opened | Result |
|---:|---|---|---|
| 1 | SemDeDup "can remove 50% of the data with minimal performance loss, effectively halving training time"; OOD performance improves | [arxiv.org/abs/2303.09540](https://arxiv.org/abs/2303.09540) | Verbatim in abstract. Abbas, Tirumala, Simig, Ganguli, Morcos; 2023-03-16 |
| 2 | "a sequence that is present 10 times … generated ~1000 times more often"; attacks have "near-chance accuracy on non-duplicated training sequences" | [arxiv.org/abs/2202.06539](https://arxiv.org/abs/2202.06539) | Both verbatim. Kandpal, Wallace, Raffel; 2022-02-14 |
| 3 | Semantic entropy "more predictive of model accuracy on question answering data sets than comparable baselines"; "different sentences can mean the same thing" | [arxiv.org/abs/2302.09664](https://arxiv.org/abs/2302.09664) | Both verbatim. Kuhn, Gal, Farquhar; 2023-02-19; ICLR 2023 Spotlight |
| 4 | BADGE: "disparate and high-magnitude when represented in a hallucinated gradient space"; no hand-tuned hyperparameters; "consistently performs as well or better" | [arxiv.org/abs/1906.03671](https://arxiv.org/abs/1906.03671) | All three verbatim. Ash, Zhang, Krishnamurthy, Langford, Agarwal; 2019-06-09; ICLR 2020 |
| 5 | Core-set = "choosing set of points such that a model learned over the selected subset is competitive for the remaining data points" | [arxiv.org/abs/1708.00489](https://arxiv.org/abs/1708.00489) | Verbatim. Sener & Savarese; 2017-08-01; ICLR 2018 |
| 6 | BERTopic pipeline and "generates coherent topics and remains competitive across a variety of benchmarks" | [arxiv.org/abs/2203.05794](https://arxiv.org/abs/2203.05794) | Verbatim. Grootendorst; 2022-03-11. (Minor: the *abstract* does not name the dimensionality-reduction step §4.2 lists; the method does use it. Left as written.) |
| 7 | Failing Loudly: two-sample testing on a pre-trained-classifier dimensionality reduction "performs best"; "domain-discriminating approaches tend to be helpful for characterizing shifts qualitatively and determining if they are harmful" | [arxiv.org/abs/1810.11953](https://arxiv.org/abs/1810.11953) | Verbatim. Rabanser, Günnemann, Lipton; 2018-10-29, rev. 2019-10-28 |
| 8 | RealHumanEval: human preference does not correlate with task performance | [arxiv.org/abs/2404.02806](https://arxiv.org/abs/2404.02806) | Confirmed: "programmer preferences do not correlate with their actual performance". Note the paper *also* finds benchmark gains **do** raise productivity, non-proportionally — §1.2g's use of it is narrow and correct |
| 9 | Langfuse plans $0 (50k, 30 d) / $29 Core (100k, 90 d) / $199 Pro (3 y) / $2,499 Enterprise; graduated overage $8 → $7 → $6.50 → $6 per 100k; unit = traces + observations + scores | [langfuse.com/pricing](https://langfuse.com/pricing) | Exact, every figure |
| 10 | Langfuse limits: 5 MB per request and per response; 1,000 / 4,000 / 20,000 req/min; 429 + `Retry-After`; org-wide, fixed-window not rolling | [langfuse.com/faq/all/api-limits](https://langfuse.com/faq/all/api-limits) | Exact, every figure |
| 11 | Braintrust: $0 Starter ($10 credits, 1 GB, 10k scores, 14 d) / $249 Pro ($100, 5 GB, 50k, 30 d); $4 and $3/GB; $2.50 and $1.50/1k scores; $0.50/GB/mo retention | [braintrust.dev/pricing](https://www.braintrust.dev/pricing) | Exact |
| 12 | W&B Weave: free 1 GB/mo; Pro from $60/mo with 1.5 GB; $0.10/MB additional ingestion; $0.03/GB storage overage | [wandb.ai/site/pricing](https://wandb.ai/site/pricing/) | Exact (see CORRECTED #3 for the arithmetic built on it) |
| 13 | Opik: free 25k spans/60 d; Pro $19/mo, 100k spans, 60 d; $5/100k overage; $29/100k to extend retention to 400 d. Honeycomb: free 20M events + 100M metric points; Pro from $150 up to 750M events; Enterprise from 10B/yr; pipeline from $0.10/GB. Lunary: free 10k/30 d; Team $20/user/mo, 50k events, +$10/50k, 1 y; "data will still be captured"; Enterprise PII masking + self-host | [comet.com/site/pricing](https://www.comet.com/site/pricing/), [honeycomb.io/pricing](https://www.honeycomb.io/pricing), [lunary.ai/pricing](https://lunary.ai/pricing) | All exact |
| 14 | LangSmith: Developer free 5k base traces; Plus $39/seat with 10k; $1.50/LCU, $1.00/LSU; 14 d base / 400 d extended; **no per-trace overage published**. Helicone: Pro $79, Team $799, 10k free, 7 d / 1 mo / 3 mo / forever; **no overage rate published** | [langchain.com/pricing-langsmith](https://www.langchain.com/pricing-langsmith), [helicone.ai/pricing](https://www.helicone.ai/pricing) | Exact — including that the two ⚠️ "not published" markers are correct, not laziness |
| 15 | ClickHouse Cloud: 1 unit = 8 GiB + 2 vCPU; $0.39030/unit-hr AWS us-east-1 Enterprise; storage $25.30/TB-mo AWS, $22.00 GCP. AWS S3 us-east-1: $0.023 / $0.0125 / $0.004 per GB-mo; $0.005 per 1,000 PUT | [clickhouse.com/pricing](https://clickhouse.com/pricing), [aws.amazon.com/s3/pricing](https://aws.amazon.com/s3/pricing/) | Exact (egress corrected separately — CORRECTED #5) |
| 16 | ClickHouse "compresses logs and traces on average up to 14x" vs original JSON | [clickhouse.com/docs/use-cases/observability/introduction](https://clickhouse.com/docs/use-cases/observability/introduction) | Verbatim, and the "versus JSON" framing §3.1's ⚠️ relies on is correct |
| 17 | The GenAI semconv repo exists, was **created 2026-05-05**, **380 stars**, **last pushed 2026-09-16**, uses Weaver, Schema URL section is `TODO` | GitHub API `/repos/open-telemetry/semantic-conventions-genai` + [the repo](https://github.com/open-telemetry/semantic-conventions-genai) | `created_at` 2026-05-05T03:08:44Z, `pushed_at` 2026-09-16T01:54:07Z, `stargazers_count` 380 — all three exact |
| 18 | vLLM's `SpanAttributes` emits `gen_ai.usage.completion_tokens` / `prompt_tokens` (deprecated names) and the non-standard `gen_ai.latency.time_in_model_{forward,execute,prefill,decode,inference}` family; the "copied … to avoid version conflicts" comment. Envoy `RequestMirrorPolicy`: fire-and-forget, all normal stats collected, `-shadow` suffix + `disable_shadow_host_suffix_append`, all requests mirrored if `runtime_fraction` unset, no shadowing without the primary cluster, no CONNECT/upgrades. Presidio: Microsoft → Data Privacy Stack, MIT, "Microsoft supports this transition", MCR → GHCR image move. Phoenix: **Elastic License 2.0** + patent notice, 11.5k★. Portkey: "a total latency addition between 20-40ms", 250+ models, 25M+ daily requests, 99.99 %, ISO 27001 + SOC 2, 10k free req/mo | [vllm/tracing/utils.py](https://github.com/vllm-project/vllm/blob/main/vllm/tracing/utils.py), [Envoy route_components.proto](https://www.envoyproxy.io/docs/envoy/latest/api-v3/config/route/v3/route_components.proto), [presidio project_transition.md](https://github.com/data-privacy-stack/presidio/blob/main/docs/project_transition.md), [Arize-ai/phoenix](https://github.com/Arize-ai/phoenix), [portkey.ai](https://portkey.ai/docs/introduction/what-is-portkey) | Every quoted string reproduced verbatim from the primary artifact. The ELv2 finding — the most consequential legal claim in the document — stands |

Repo cross-references opened and confirmed: `matrix/cost-matrix.md` ($0.0092
marlin2b·B300, $2.3811 kimik3·B300, $0.0602 qwen3827b·B300 — §5.3's
"$0.0092–$2.38" range is exact); doc 00 §4.1 (GPT-5.6 Sol $4/$0.40/$20, blended
$6.650; Opus 5 blended $8.3125 — §5.3's "$8.31" and the 0.72 % shadow ratio hold)
and §4.3a ($6,560 / $3,280 batched for 100k GPT-6 Astra labels — exact).

All of §7's arithmetic was recomputed: row = 20,096 B; 48M rows = 964.608 GB;
+6.144 GB video spans = 0.971 TB; 19.29 mean and 96.45 peak req/s; 5,787
spans/min; 69.3 GB at 14×; $1.75 month-1 and $22.81 at 13 months; $2,279.35
compute; $22.19 S3; $250 PUT; **$2,574 subtotal**; video $230/$1,380/$5,520
Standard, $125/$750/$3,000 IA, $40/$240/$960 GIR, $27.60 at `ref_only` 2 %,
$16,560 cumulative at 12 months; Langfuse $15,302 at 250M units and $3,302 at
50M; Opik $12,495; Lunary $49,990; Braintrust $7,500 / $75,000 scores; judge
$500–$5,000 at 0.1 %; incumbent $1.44M text + $320k video = $1.76M; 0.15 % /
0.23 %; 1,493× media-to-text ratio; 1.84× and 3.125× storage-class savings.
**All correct as printed** except the three items in CORRECTED below.

### CORRECTED (6)

1. **Langfuse is owned by ClickHouse — the document's single largest miss.** The
   draft cited `langfuse.com/self-hosting` for "MIT core, EE add-ons"; that page
   states **no licence at all**. The repo's own `LICENSE` does — "MIT Expat" core
   with `ee/` under a commercial Enterprise License — and it is **copyright
   "ClickHouse, Inc. 2023-2026"**. The repo README states *"since January 2026
   we're part of ClickHouse"*
   [[src](https://raw.githubusercontent.com/langfuse/langfuse/main/LICENSE),
   [README](https://raw.githubusercontent.com/langfuse/langfuse/main/README.md)].
   Three places in this document rested on Langfuse and ClickHouse being
   independent and now say otherwise: §2.2's licence cell, §2.3's "strongest
   available argument for adopting rather than building" (it is now a first-party
   choice, so it is *weak* corroboration), §3.2's store-choice row, and §6.3's
   adopt verdict (Langfuse + ClickHouse Cloud is now **one** vendor and one
   concentration risk, against doc 00 §8.6's sunset theme). The MIT Expat grant on
   released code is irrevocable, which bounds the exposure; the EE add-ons and
   ClickHouse Cloud pricing are not bounded.
2. **Weave's $99,404/mo mixed decimal GB with binary MB.** §7.1 establishes
   970.752 GB decimal; at $0.10/MB that is **$97,075**, not $99,404 (which is
   970.752 × 1024 × $0.10 = $99,405, i.e. a MiB reading). W&B's page does not
   define "MB", so both readings are now printed with the decimal one primary.
   Downstream: "38.6× vs Weave" → **37.7×**; "4–39× cheaper" → **4–38×** (§7.3
   reading 1, §6.3, Implications); "663× spread" → **647×**; "$99.4k is 5.6 %" →
   **$97.1k is 5.5 %**; §2.2's "$0.10/MB = $102.40/GB" → $100/GB decimal, $102.40
   per **GiB**.
3. **§7.1's "366 KB/s" mean ingest bandwidth was internally inconsistent** with
   its own "1.87 MB/s at 5× peak". 970,752,000,000 B ÷ (30 × 86,400 s) =
   **374.5 KB/s**, and 5 × that is 1.87 MB/s. Corrected to **375 KB/s**; the peak
   was right all along.
4. **The Marlin-2B Path A/B ratio is 1.917×, not 1.914×** — and the *source* is
   internally inconsistent. 23,560 / 12,288 = **1.9173**; the 1.914× printed in
   both this document and
   [`models/marlin2b/README.md`](../models/marlin2b/README.md) is
   23,**520**/12,288 = 1.9141. That README says **23,560** in its item 2 and
   **23,520** in its open question 2, a 40-token disagreement inside one file.
   Flagged inline in §1.6 and in Sources. ⚠️ Upstream fix belongs in
   `models/marlin2b/README.md`, not here.
5. **ClickHouse Cloud egress "$0.114–$0.151/GB": the upper bound is withdrawn.**
   The page as re-fetched gives public-internet $0.115/GB (AWS us-east-1) and
   $0.114/GB (GCP us-central1), with inter-region $0.031 / $0.036. No $0.151
   appears. §3.2 now prints the four sourced figures and marks the withdrawal.
6. **Opik's licence and star count.** "open source core (21k★ claimed)" →
   **Apache-2.0, 22.1k★** (GitHub API `/repos/comet-ml/opik`, 2026-09-19). A
   licence named from a primary artifact matters here for exactly the reason
   §2.3's Phoenix bullet argues.

*Two arithmetic nits not worth an edit, recorded for completeness:* §7.3's
Braintrust data line ($2,912 = 970.752 GB × $3) ignores the 5 GB included in Pro;
the exact figure is $2,897, which moves nothing. And §2.3's "a batch of 64 spans
… is ~5 MB" versus §7.1's "~250 full-content spans per batch" are the same rule
at two prompt sizes (62.5 at 80 KB, 248.8 at 20,096 B); a parenthetical now says
so, because it reads as a contradiction.

### UNVERIFIABLE (1)

**Agent Router's "no CRD, API, or CLI names have changed".** The rename itself is
confirmed — the post *"Envoy AI Gateway is becoming Agent Router, an Agentic AI
Foundation project"*, announced 2026-09-09 for a 2026-09-10 transfer, with v1.0
on 2026-06-23 ("16 AI providers, an MCP gateway, multimodal support, and
enterprise observability") and v0.3 on 2025-08-22 ("enterprise observability with
OpenInference tracing") [[blog](https://theagentrouter.ai/blog/)]. But the blog
index as fetched reads *"Same code, same maintainers. Same APIs"*, and the
per-post URL returns 404, so the document's **verbatim** quote could not be
reproduced. Substance stands; the quotation marks do not. Marked ⚠️ inline in
§2.4. This is the claim §2.4's decision rule and open question 10 lean on, so it
should be re-checked when the post is reachable.

### Method note

This pass had **WebSearch unavailable for the same reason the document's opening
caveat states** — the session budget was exhausted (200/200) — so it could
neither widen §2's market scan nor close open question 1. Every check above was a
direct fetch of a URL nameable in advance, the GitHub API, or a file in this
repository. The document's own ⚠️ markers were tested, not assumed: **all four
"not published" claims (Datadog, LangSmith per-trace, Helicone overage, Portkey
paid tiers) are accurate.**
