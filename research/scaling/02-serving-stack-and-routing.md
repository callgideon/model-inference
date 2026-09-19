# Serving stack, gateways and request routing

Research date: **2026-09-19**. Every version and measurement below is pinned to that
date; this layer of the stack moves in weeks, not quarters (vLLM shipped **four minor
releases — 0.26.0, 0.27.0, 0.28.0, 0.29.0 — plus two patches (0.25.1, 0.27.1) between
2026-07-11 and 2026-09-09**; corrected 2026-09-19 from "six minor releases", which counted
the 0.25.0 start point and the 0.27.1 patch as minors —
[releases API](https://github.com/vllm-project/vllm/releases),
[cross-cutting/inference-engines.md §2.1](../cross-cutting/inference-engines.md)).

**Scope.** This document covers everything between the client socket and the engine
process: the gateway, the router/scheduler, how a request is placed on a replica, and
how a stream survives failure. It does **not** re-derive per-GPU capacity, per-model
fit, engine/GPU support, quantization, or the economics of prefix caching and
prefill/decode disaggregation — those are already fact-checked in this tree and are
**linked, not repeated**:

| For | Read |
|---|---|
| Formulas, pinned GPU/model inputs, cost model | [`METHODOLOGY.md`](../METHODOLOGY.md) |
| Which engine runs which model on which GPU, engine versions, feature cross-reference | [`cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md) |
| Prefix caching economics, speculative decoding, parallelism, **when PD disaggregation pays** | [`cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md) |
| KV bytes/token, max concurrency, min GPUs per model | [`matrix/fit-matrix.md`](../matrix/fit-matrix.md) |
| $/1M tokens per (model, GPU) | [`matrix/cost-matrix.md`](../matrix/cost-matrix.md) |
| Recommended engine + launch shape per model | [`matrix/recommendations.md`](../matrix/recommendations.md) |

**Legend** is [`METHODOLOGY.md` §Legend](../METHODOLOGY.md#legend): `[src]` = primary
source, `meas.` = published measurement, `est.` = derived, **⚠️ TO BE VERIFIED** =
estimate with the method stated inline.

**Running hardware.** 8×B300 HGX nodes (268 GB/GPU, 2,144 GB/node, NVLink inside the
node, InfiniBand/RoCE between nodes) with local NVMe, plus AWS p6 nodes
([`METHODOLOGY.md` §8](../METHODOLOGY.md#8-pinned-inputs-converged-values-from-the-fact-checked-docs)).

---

## 1. Layered architecture: who owns what

### 1.1 The five layers

```
                     ┌──────────────────────────────────────────────┐
  client  ─────────► │ L1  API gateway / edge                       │  auth, API keys, tenant
  (SDK, agent)       │     Envoy+Agent Router | Kong | NGINX | IPP  │  quotas, token rate limits,
                     └───────────────────┬──────────────────────────┘  model aliases, ext-provider
                                         │ HTTPRoute / model header    fallback, audit
                     ┌───────────────────▼──────────────────────────┐
                     │ L2  router / scheduler (per model pool)      │  endpoint choice: prefix
                     │     llm-d Router (EPP) | Dynamo frontend |   │  affinity, load, SLO,
                     │     SGLang Model Gateway | TRT-LLM disagg    │  queueing, priority, retries
                     └───────────────────┬──────────────────────────┘
                                         │ picked endpoint (+ prefill hint)
                     ┌───────────────────▼──────────────────────────┐
                     │ L3  engine replicas                          │  continuous batching,
                     │     vLLM / SGLang / TRT-LLM processes,       │  chunked prefill, CUDA
                     │     optionally split prefill ∥ decode pools  │  graphs, spec decoding
                     └───────────────────┬──────────────────────────┘
                                         │ KV blocks
                     ┌───────────────────▼──────────────────────────┐
                     │ L4  KV / cache tier                          │  device HBM → host DRAM →
                     │     engine radix cache, LMCache, HiCache,    │  NVMe → object store
                     │     Mooncake store, Dynamo KVBM              │
                     └──────────────────────────────────────────────┘
                     ┌──────────────────────────────────────────────┐
                     │ L0  control plane                            │  discovery, autoscaling,
                     │     K8s + GPU Operator, LWS, Grove, KEDA     │  gang scheduling, health
                     └──────────────────────────────────────────────┘
```

### 1.2 Ownership boundaries that actually matter

The boundary that causes the most production confusion is **L1 vs L2**. They are
different products with different failure modes, and the Kubernetes community has now
formalized the split:

- **L1 owns identity and money**: who is calling, which model alias they asked for, how
  many tokens they are allowed to burn, and where to send the request if the local pool
  is down. Agent Router (formerly Envoy AI Gateway) describes exactly this as the
  "Tier One Gateway … handling authentication, top-level routing, and global
  token-based rate limiting across all AI providers"
  [src](https://theagentrouter.ai/docs/).
- **L2 owns placement**: given a set of *interchangeable* replicas of *one* model, which
  one serves this request. It needs per-replica KV-cache state, queue depth and in-flight
  token load — signals L1 has no access to. The Gateway API Inference Extension calls
  this the **Endpoint Picker (EPP)**: "a data-plane component that communicates via the
  Envoy external processing protocol, and acts as the `Router`. It intercepts incoming
  inference requests and routes each request to the optimal model server replica"
  [src](https://raw.githubusercontent.com/kubernetes-sigs/gateway-api-inference-extension/main/README.md).

**Hard constraint on L2 that shapes every design below:** as of 2026-09-19,
llm-d documents "Single `InferencePool` and single `EPP` due to Envoy limitations …
Currently only one base model **per `InferencePool`** is supported. Multiple models are
supported via multiple `InferencePools`"
[src](https://raw.githubusercontent.com/llm-d/llm-d-router/main/docs/architecture.md).
So multi-model serving is an L1 problem (header-based fan-out to per-model pools), not
an L2 problem. §5 covers the consequences.

### 1.3 Where the tokenizer lives — a real latency decision

Prefix-affinity routing needs token IDs, not bytes, so the router must tokenize before
it can pick an endpoint. **That tokenization sits inside TTFT.** llm-d's precise-prefix
guide makes this explicit and recommends fronting the model servers rather than running
a separate renderer pool:

> "a dedicated pool is scheduled independently of the model servers, so as QPS climbs it
> saturates before they do and render latency — which sits inside TTFT, since every
> request is tokenized before it is routed — starts to dominate. Fronting the model
> servers instead makes render capacity scale with the fleet"
> [src](https://raw.githubusercontent.com/llm-d/llm-d/main/guides/precise-prefix-cache-routing/README.md)

The trade-offs it names, verbatim: tokenization CPU competes with serving on the same
pods (the GPU overlay requests 8 CPU / limits 16 per replica); the render call is a
synchronous hop to a GPU serving pod on the request path; and until the first model
server is `Ready` the Service has no endpoints and the `token-producer` calls fail.

SGLang does not implement vLLM's `/v1/*/render` endpoints, so an SGLang fleet must run a
dedicated GPU-less `vllm launch render` pool (the guide ships 3 replicas)
[src](https://raw.githubusercontent.com/llm-d/llm-d/main/guides/precise-prefix-cache-routing/README.md).
The SGLang Model Gateway sidesteps this differently: its gRPC router runs "fully in
Rust—tokenizer, reasoning parser, and tool parser all reside in-process"
[src](https://docs.sglang.io/advanced_features/sgl_model_gateway.html).

**Decision rule.** If the router tokenizes (any prefix-exact routing), budget a render
hop in TTFT and co-locate render with serving. If you cannot afford the hop, use an
approximate producer that hashes raw text blocks instead — llm-d's
`approx-prefix-cache-producer`, SGLang's `cache_aware` radix tree, and Ray's
`PrefixCacheAffinityRouter` all work on text and skip the hop, at a lower hit rate.

---

## 2. Engines and their serving surfaces as of 2026-09-19

### 2.1 Version pin

Taken from the GitHub releases API and PyPI on 2026-09-19. Where this table disagrees
with [`cross-cutting/inference-engines.md` §2`](../cross-cutting/inference-engines.md),
that document's engine versions (vLLM 0.29.0, SGLang 0.5.20, TRT-LLM 1.2.1 stable /
1.3.0rc27, Dynamo 1.5.0) are the same values; the router-layer rows are new here.
One **dated** disagreement, verified 2026-09-19 and left standing rather than silently
harmonised: that document dates `1.3.0rc27` **2026-09-17** (PyPI `upload_time`), this one
**2026-09-18** (GitHub `published_at` `2026-09-18T03:14:53Z`). Both are right for their own
source; use the GitHub date when comparing against the other rows in this table, which are
all `published_at`.

| Component | Latest as of 2026-09-19 | Date | Source |
|---|---|---|---|
| vLLM | **0.29.0** | 2026-09-09 | [releases](https://github.com/vllm-project/vllm/releases) |
| SGLang | **v0.5.20** | 2026-09-18 | [releases](https://github.com/sgl-project/sglang/releases) |
| TensorRT-LLM | **v1.3.0rc27** (pre-release; 1.2.1 last stable) | 2026-09-18 (GitHub `published_at`) | [releases](https://github.com/NVIDIA/TensorRT-LLM/releases) |
| NVIDIA Dynamo | **v1.4.2** last numbered stable; PyPI `ai-dynamo` **1.5.0** stable (uploaded 2026-09-19 04:21 UTC), newest dev `1.5.0.dev20260914` — **there is no `1.6.0.dev*` on PyPI** (corrected 2026-09-19) | 2026-08-29 / 2026-09-19 | [releases](https://github.com/ai-dynamo/dynamo/releases), [PyPI](https://pypi.org/pypi/ai-dynamo/json) |
| Dynamo model-snapshot tags | `v1.6.0-deepseek-v4.1-flash-dev.1`; `v1.5.0-kimi-k3-dev.1` | 2026-09-12 / 2026-09-09 | [releases](https://github.com/ai-dynamo/dynamo/releases) |
| llm-d | **v0.9.0** (v0.8.0 2026-06-24, v0.8.1 2026-06-26) — **corrected 2026-09-19 from "v0.7"**: the repo README still advertises "Version 0.7 (May 2026)" and is stale; the releases API is authoritative. v0.7.0 itself is 2026-05-12, so every "v0.7" *feature* statement below stands | 2026-08-17 | [releases](https://github.com/llm-d/llm-d/releases) (README stale: [README](https://raw.githubusercontent.com/llm-d/llm-d/main/README.md)) |
| llm-d Router (ex-Inference Scheduler) | **v0.10.0** (v0.11.0-rc.1 2026-09-18) | 2026-08-17 | [releases](https://github.com/llm-d/llm-d-router/releases) |
| Gateway API Inference Extension (GIE) | **v1.6.2** | 2026-09-17 | [releases](https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases) |
| Agent Router (ex-Envoy AI Gateway) | **v1.1.0** | 2026-08-21 | [release notes](https://theagentrouter.ai/release-notes/) |
| KServe | **v0.20.0** (v0.21.0-rc0 2026-09-10) | 2026-08-06 | [releases](https://github.com/kserve/kserve/releases) |
| AIBrix | **v0.7.0** | 2026-06-18 | [releases](https://github.com/vllm-project/aibrix/releases) |
| Ray (Serve LLM) | **ray-2.58.0** | 2026-08-23 | [releases](https://github.com/ray-project/ray/releases) |
| Triton Inference Server | **2.72.0** (container 26.08) | ⚠️ date not published on the README | [repo](https://github.com/triton-inference-server/server) |
| `sglang-router` PyPI | 0.3.2 — the component is now shipped as the **SGLang Model Gateway** (`sgl-model-gateway` binary) | 2026-01-15 | [PyPI](https://pypi.org/pypi/sglang-router/json), [docs](https://docs.sglang.io/advanced_features/sgl_model_gateway.html) |

### 2.2 Two renames that invalidate older docs

These matter because half the material on the internet still uses the old names.

1. **GIE v1.6.0 (2026-08-17) split the project.** The upstream README now carries an
   IMPORTANT box: "The Endpoint Picker (EPP), InferenceObjective and
   InferenceModelRewrite APIs, and Body Based Router (BBR) packages have moved to new
   repositories: EPP and associated APIs: `llm-d/llm-d-router`; BBR:
   `llm-d/llm-d-inference-payload-processor` … This repository will continue to host the
   **lightweight EPP (LWEPP)** and the **InferencePool API**"
   [src](https://raw.githubusercontent.com/kubernetes-sigs/gateway-api-inference-extension/main/README.md).
   The v1.6.0 release notes add that alpha APIs `InferenceObjective`,
   `InferenceModelRewrite` and `EndpointPickerConfig` were removed from GIE and
   `endpointPickerRef` became optional
   [src](https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases).
   **There is no `InferenceModel` CRD in the v1 API** — that name belongs to the
   pre-GA alpha and was superseded by `InferenceObjective` (scheduling goals, priority,
   latency targets) plus `InferenceModelRewrite` (model-name rewriting for A/B and
   canary), both now owned by llm-d
   [src](https://raw.githubusercontent.com/llm-d/llm-d-router/main/README.md).
2. **llm-d's "Inference Scheduler" is now the "llm-d Router".** The repo carries the
   terminology note: "**llm-d Router**: The complete intelligent entry point, comprising
   both the **Proxy** (e.g. Envoy) and the **Endpoint Picker (EPP)**. This term replaces
   'Inference Scheduler' in all contexts"
   [src](https://raw.githubusercontent.com/llm-d/llm-d-router/main/README.md).
3. **Envoy AI Gateway is now Agent Router.** "Agent Router is the new name for **Envoy
   AI Gateway**, now an **Agentic AI Foundation** project. Same code, same maintainers,
   new home — **no CRD, API, or CLI names have changed**"; `aigw`, `AIGatewayRoute` and
   the `aigateway.envoyproxy.io` API group are unchanged
   [src](https://theagentrouter.ai/docs/).

### 2.3 Feature matrix

`✅` = documented and shipped in the pinned version; `🚧` = documented as in progress or
experimental; `—` = not offered by this layer (may be delegated downward); `⚠️` = no
primary source found on 2026-09-19.

| | vLLM 0.29.0 (engine + OpenAI server) | SGLang 0.5.20 + Model Gateway | TensorRT-LLM 1.3.0rc27 (`trtllm-serve`) | Dynamo 1.5/1.6-dev | llm-d v0.9 (features sourced at v0.7+) / Router v0.10.0 | KServe 0.20 | AIBrix 0.7.0 | Ray Serve LLM (2.58) | Triton 2.72 |
|---|---|---|---|---|---|---|---|---|---|
| OpenAI-compatible server | ✅ `vllm serve` (`vllm.entrypoints.openai.api_server` deprecated in 0.29 in favour of `vllm.entrypoints.launchers`) | ✅ + `/v1/responses`, `/v1/rerank`, `/v1/classify`, `/v1/tokenize` | ✅ `trtllm-serve` | ✅ `python -m dynamo.frontend` | — (proxies engines) | ✅ | ✅ | ✅ | via backends |
| Prefix-aware routing **across replicas** | — (intra-replica APC only) | ✅ `--policy cache_aware` (default) | ✅ `router: {type: kv_cache_aware}` | ✅ `--router-mode kv` | ✅ approx + **precise** (KV events) | ✅ (delegates to GIE EPP) | ✅ `prefix-cache` | ✅ `PrefixCacheAffinityRouter` | ⚠️ |
| PD disaggregation | 🚧 experimental, 9 connectors ([engines doc §5](../cross-cutting/inference-engines.md)) | ✅ `--disaggregation-mode`, + EPD | ✅ `trtllm-serve disaggregated` | ✅ first-class, xPyD runtime-reconfigurable | ✅ well-lit path; E/PD 🚧 | ✅ (`spec.prefill`) | ✅ `pd` strategy | ✅ (Anyscale) | ✅ via TRT-LLM backend |
| Multi-LoRA | ✅ `--enable-lora`, runtime load/unload | ✅ | ⚠️ | ✅ (`DYN_LORA_ENABLED`; session affinity KV-mode only) | ✅ cache-aware LoRA routing (v0.5) | ✅ | ✅ "High-Density LoRA Management" | ⚠️ | ⚠️ |
| Speculative decoding | ✅ `--speculative-config` (mtp/dspark/dflash/eagle3) | ✅ `--speculative-algorithm` | ✅ MTP/EAGLE-3/DSpark | passes through | passes through | passes through | passes through | passes through | via backend |
| KV offload / tiering | ✅ `OffloadingConnector`, LMCache, FlexKV | ✅ HiCache L1/L2/L3 + Mooncake | ✅ KV Cache Connector API | ✅ KVBM (🚧 on SGLang) | ✅ tiered prefix cache + global index | ✅ | ✅ store-centric KV layer | ✅ | ⚠️ |
| Router consumes KV **events** | publishes via `--kv-events-config` | publishes (llm-d subscribes) | publishes to coordinator over ZMQ | ✅ consumes, `--no-router-kv-events` to fall back | ✅ consumes over ZMQ | via EPP | ✅ Redis-backed HA prefix tables | ✗ (text prefix tree) | ⚠️ |
| Session affinity | — | via gateway | `router: {type: conversation}` | ✅ `--router-session-affinity-ttl-secs`, `hard`/`soft` | ✅ `session-id-producer` plugin | via gateway | ✅ `session-affinity` (`x-session-id`) | ⚠️ | ⚠️ |
| SLO / latency-predicted routing | — | — | — | 🚧 AIC prefill load model | ✅ **GA in v0.7** (predicted-latency) | ✅ WVA | ✅ `slo` strategy | — | — |
| Fairness / priority / quotas | `--scheduling-policy priority` | `--max-concurrent-requests`, token bucket | — | policy-class queues, `fcfs`/`wspt`, strict priority tiers | flow control (`featureGates: [flowControl]`) | ✅ | ✅ `vtc-basic` (per-user token fairness) | — | — |
| Token-based rate limiting | ✗ | ✅ `--rate-limit-tokens-per-second` | ✗ | ✗ | ✗ (delegates to L1) | ✅ via Agent Router | ✅ | ✗ | ✗ |
| Autoscaling hooks | `/metrics` | `/metrics` (40+) | `/metrics` | Planner (SLA-based) + `/metrics` | WVA + scale-to-zero | WVA | KPA/APA + custom | Ray autoscaler | `/metrics` |
| Multi-node model | ✅ DP/TP/EP/PP, Ray or native | ✅ | ✅ | ✅ (+ Grove gang scheduling) | ✅ LWS, wide-EP | ✅ LeaderWorkerSet | ✅ | ✅ | ✅ |
| Observability | Prometheus | Prometheus + OTel traces | Prometheus | Prometheus (`dynamo_component_router_*`, `dynamo_router_overhead_*`, `dynamo_frontend_worker_*`) | OTel JSON stdout + Prometheus | ✅ | ✅ | ✅ | ✅ |

Engine-level rows that are already audited elsewhere (EPLB, DP-attention, DCP,
all-to-all backends, CUDA-graph flags) are in
[`cross-cutting/inference-engines.md` §5](../cross-cutting/inference-engines.md#5-engine-feature-cross-reference)
and are not duplicated here.

### 2.4 vLLM — the serving surface, precisely

vLLM 0.29 has **no cross-replica router of its own**. Its cluster-shaped surface is the
data-parallel deployment mode, which comes in three load-balancing flavours
[src](https://docs.vllm.ai/en/latest/serving/data_parallel_deployment/):

| Mode | Who balances | Flags |
|---|---|---|
| **Internal** | vLLM's own API server, with "direct visibility into each rank's queue state" | `--data-parallel-size N` (+ `--data-parallel-size-local`, `--data-parallel-address`, `--data-parallel-rpc-port`, `--headless` on non-leader nodes) |
| **External** | an outside LB; "the data parallel coordinator only runs on rank 0" | `--data-parallel-rank R` per process |
| **Hybrid** | vLLM balances within a node, an external LB balances across nodes | `--data-parallel-hybrid-lb` + `--data-parallel-start-rank` |

Single-node internal-LB, quoted verbatim:

```bash
vllm serve $MODEL --data-parallel-size 4 --tensor-parallel-size 2
```

Multi-node internal-LB (leader + headless follower), verbatim:

```bash
# Node 0 (10.99.48.128)
vllm serve $MODEL --data-parallel-size 4 --data-parallel-size-local 2 \
                  --data-parallel-address 10.99.48.128 --data-parallel-rpc-port 13345
# Node 1
vllm serve $MODEL --headless --data-parallel-size 4 --data-parallel-size-local 2 \
                  --data-parallel-start-rank 2 \
                  --data-parallel-address 10.99.48.128 --data-parallel-rpc-port 13345
```

The **DP coordinator** is, verbatim, "A separate DP Coordinator process that communicates
with all ranks, and a collective operation performed every N steps to determine when all
ranks become idle" (the trailing "and can be paused" in earlier drafts of this document was
not upstream text — corrected 2026-09-19 against the fetched page). This is what makes DP
MoE serving tolerable, because a rank with no work still has to step in lockstep for the
all-to-all.

The routing-relevant knobs vLLM exposes *to* an external router are the KV event stream
(`--kv-events-config`), the KV transfer connector (`--kv-transfer-config`), prefix
caching (`--enable-prefix-caching`), and the scheduler policy
(`--scheduling-policy {fcfs,priority}`, default `fcfs`)
[src](https://docs.vllm.ai/en/latest/configuration/engine_args/).

Note for anyone scripting against 0.29: `vllm.entrypoints.openai.api_server` now emits a
`DeprecationWarning` — "`python -m vllm.entrypoints.openai.api_server` command is
deprecated … Please use `vllm server` instead" — with the implementation moved under
`vllm.entrypoints.launchers`
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/entrypoints/openai/api_server.py).

### 2.5 SGLang — the Model Gateway

The SGLang router has been rebuilt and renamed to the **SGLang Model Gateway**. Its
documented feature set [src](https://docs.sglang.io/advanced_features/sgl_model_gateway.html):

- **Control plane**: Worker Manager (discovers via `/get_server_info`, `/get_model_info`),
  Job Queue, Load Monitor ("feeds cache-aware and power-of-two policies with live worker
  load statistics"), Health Checker, Tokenizer Registry.
- **Data plane**: HTTP routers (regular and PD), a **gRPC router** running "fully in
  Rust—tokenizer, reasoning parser, and tool parser all reside in-process", and an OpenAI
  router that "proxies OpenAI-compatible endpoints to external vendors (OpenAI, xAI, etc.)
  while keeping chat history and multi-turn orchestration local."
- **IGW mode** (`--enable-igw`) "dynamically instantiates multiple router stacks (HTTP
  regular/PD, gRPC) and applies per-model policies for multi-tenant deployments" — i.e.
  SGLang's gateway *does* do multi-model, unlike an InferencePool.
- Reliability primitives (retries, circuit breakers, token-bucket rate limiting, queuing)
  — see §6.
- WASM middleware "for custom request/response processing … authentication, rate
  limiting, billing, logging … without modifying or recompiling the gateway."

Five load-balancing policies, verbatim from the policy table: `random`, `round_robin`,
`power_of_two` ("Samples two workers and picks the lighter one"), `cache_aware`
("Combines cache locality with load balancing (default)"), `bucket` ("Divides workers
into load buckets with dynamic boundaries").

### 2.6 TensorRT-LLM — `trtllm-serve disaggregated`

TRT-LLM's own orchestrator is a separate OpenAI-compatible "disaggregated server" that
fans out to context and generation servers. Its routing surface is a per-pool `router`
block with **stateful** (`kv_cache_aware`, `conversation`) and **stateless**
(`round_robin`, `load_balancing`) types, and — new and important at scale — a
**coordinator + worker fleet** so the orchestrator itself is not a single-threaded
bottleneck
[src](https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/docs/source/features/disagg-serving.md):

> "**Coordinator** — a single process that owns all cluster state: the ctx/gen routers,
> worker readiness, and (for the KV-cache-aware router) the single ZMQ event-ingest
> endpoint. … **Fleet workers** — `num_workers` stateless disaggregated servers that
> share the public port via `SO_REUSEPORT` … Each holds a lightweight delegating client:
> it computes the routing key locally (e.g. block hashes) and delegates the placement
> decision to the coordinator over HTTP."

and the decision rule, verbatim:

> "The fleet is most useful with a *stateful* router (`kv_cache_aware`, `conversation`)
> where placement must be globally consistent — that decision is delegated to the
> coordinator. With a *stateless* router (`round_robin`, `load_balancing`) each worker
> simply places locally and no coordinator round-trip occurs."

### 2.7 NVIDIA Dynamo — frontend, KV router, planner, KVBM, NIXL

Dynamo positions itself as the orchestration layer, not an engine: "it doesn't replace
SGLang, TensorRT-LLM, or vLLM, it turns them into a coordinated multi-node inference
system"
[src](https://raw.githubusercontent.com/ai-dynamo/dynamo/main/README.md).
Its own "when to use" list is a usable decision rule: multiple GPUs/nodes to coordinate;
KV-aware routing; independently scaling prefill and decode; SLA-driven autoscaling; fast
cold starts. And the negative: "If you're running a single model on a single GPU, your
inference engine alone is probably sufficient."

New in 1.0, relevant here: "**K8s Inference Gateway plugin:** KV-aware routing inside the
standard Kubernetes gateway", "**Multimodal E/P/D:** Disaggregated encode/prefill/decode
with embedding cache — 30% faster TTFT on image workloads", "**Storage-tier KV offload:**
S3/Azure blob support + global KV events for cluster-wide cache visibility", and
per-request agentic hints for "priority, expected output length, and speculative prefill"
[src](https://raw.githubusercontent.com/ai-dynamo/dynamo/main/README.md).

Dynamo's headline numbers are **vendor claims**, and the README labels their provenance:
7× throughput/GPU (DeepSeek R1, GB200 NVL72 w/ Dynamo vs B200 without, InferenceX), 7×
faster model startup (ModelExpress weight streaming), **2× faster TTFT from KV-aware
routing (Qwen3-Coder 480B, Baseten)**, 80% fewer SLA breaches from Planner autoscaling at
5% lower TCO, 750× higher throughput (DeepSeek-R1 on GB300 NVL72, InferenceXv2). The
Baseten figure has an independent write-up with measured detail — see §3.6.

### 2.8 llm-d — Kubernetes-native, and the reference architecture the rest copies

llm-d is a **CNCF Sandbox** project since 2026-03, founded by Red Hat, Google Cloud, IBM
Research, CoreWeave and NVIDIA
[src](https://raw.githubusercontent.com/llm-d/llm-d/main/README.md).
Its Router is deployable two ways
[src](https://raw.githubusercontent.com/llm-d/llm-d-router/main/README.md):

- **Standalone Mode** — self-managed Envoy, either as a sidecar in the EPP pod (default,
  "suited to basic testing and local evaluations") or as a separate scalable Deployment
  (`router.proxy.mode=service`).
- **Gateway Mode (Inference Gateway)** — "The recommended mode for production
  environments … the EPP acts as a backend for an `InferencePool`, which is referenced by
  an `HTTPRoute` on a shared `Gateway`."

One operational constraint worth copying into your own runbook: "the only supported
`request_body_mode` and `response_body_mode` is `FULL_DUPLEX_STREAMED`". An Envoy you
configure yourself with buffered ext_proc bodies will break streaming.

The EPP pipeline is a **filters + scorers + pickers** chain over a pluggable data layer
[src](https://raw.githubusercontent.com/llm-d/llm-d-router/main/docs/architecture.md):
request control (screeners, data producers, admission) runs once per request; then each
**scheduling profile** runs Filtering → Scoring (weighted, currently sequential) → Pod
Selection ("The highest-scored pod is selected; if multiple pods share the same score,
one is selected at random"). Two profiles = disaggregated prefill. Metrics polling has a
base tick of `--refresh-metrics-interval` (default **50 ms**), with each source's own
`interval` rounded to a multiple of it.

### 2.9 The rest, briefly

- **KServe 0.20** — `LLMInferenceService` (`serving.kserve.io/v1alpha1`) became
  production-ready in v0.17 (2026-03-13) with "a GenAI-first architecture built on the
  llm-d framework … KV-cache aware intelligent routing, disaggregated prefill-decode,
  distributed inference with tensor/data/expert parallelism, Envoy AI Gateway integration
  with token-based rate limiting"
  [src](https://kserve.github.io/website/blog/kserve-0.17-release). Minimal spec,
  verbatim from the docs:

  ```yaml
  apiVersion: serving.kserve.io/v1alpha1
  kind: LLMInferenceService
  metadata: {name: llama-3-8b, namespace: default}
  spec:
    model: {uri: hf://meta-llama/Llama-3.1-8B-Instruct, name: meta-llama/Llama-3.1-8B-Instruct}
    replicas: 3
    template:
      containers:
        - name: main
          image: vllm/vllm-openai:latest
          resources: {limits: {nvidia.com/gpu: "1", cpu: "8", memory: 32Gi}}
    router: {gateway: {}, route: {}, scheduler: {}}
  ```

  ⚠️ **TO BE VERIFIED** — the full `spec.parallelism` / `spec.prefill` / `spec.worker`
  field set is referenced by the overview page but not published on it; the configuration
  guide URL returned 404 on 2026-09-19. ⚠️ **Also unresolved: the API version.** The
  manifest above is `serving.kserve.io/v1alpha1` exactly as the overview page (version
  label "0.20") publishes it, but the v0.17 GA blog announces LLMInferenceService under
  `serving.kserve.io/**v1alpha2**` — two first-party KServe pages disagree, re-checked
  2026-09-19. Read the CRD off your cluster before writing manifests.
- **AIBrix 0.7.0** — composable routing selected per request by a `routing-strategy`
  header or globally by `ROUTING_ALGORITHM`, with strategies `least-request`,
  `throughput` ("routes to the pod that has processed the fewest total weighted tokens"),
  `load-balance`, `prefix-cache`, `vtc-basic` ("balances per-user token fairness and pod
  utilization"), `slo`, `pd`, `session-affinity` (encodes target pod in `x-session-id`)
  [src](https://aibrix.readthedocs.io/latest/features/gateway-plugins.html). v0.7.0 adds
  **blended** strategies — verbatim from the release post,
  `AIBRIX_ROUTING_ALGORITHM="least-request:2,throughput:1"` (note the `AIBRIX_` prefix on
  the blended form; the gateway-plugins page documents the plain `ROUTING_ALGORITHM` for
  single strategies — corrected 2026-09-19) — a **Redis-backed HA gateway** ("Redis-backed
  state sharing across replicas. A generic state-sync layer keeps each replica's
  prefix-cache hash table consistent", with "per-pod running-request counts … snapshotted
  across the fleet"), and "v0.7.0 makes TensorRT-LLM a first-class engine alongside vLLM
  and SGLang"
  [src](https://aibrix.github.io/posts/2026-06-16-v0.7.0-release/).
- **Ray Serve LLM** — default router is Power of Two Choices ("Randomly sample two
  replicas. Route to the replica with fewer ongoing requests"); `PrefixCacheAffinityRouter`
  "checks load balance and uses prefix matching when replicas are balanced … Routes to
  replicas with highest prefix match (≥10% match rate); Routes to replicas with lowest
  cache utilization (<10% match rate); Falls back to Power of Two Choices when load is
  imbalanced"
  [src](https://docs.ray.io/en/latest/serve/llm/architecture/routing-policies.html).
  ⚠️ **TO BE VERIFIED** — the parameter names `imbalanced_threshold` (default 10) and
  `match_rate_threshold` (default 0.1) appear in secondary summaries of the Ray docs but
  are not on the routing-policies page fetched on 2026-09-19.
- **Triton 2.72.0** — still shipping (container 26.08), but NVIDIA's own LLM investment
  and every recipe for our five models is on Dynamo + engine backends. ⚠️ **TO BE
  VERIFIED** — no primary source found on 2026-09-19 stating Triton's supported position
  for LLM serving relative to Dynamo, or a deprecation. Treat Triton as the path for
  *non-LLM* models co-resident on the same cluster, not for these five.

---

## 3. Routing algorithms

### 3.1 The ladder, cheapest first

| Algorithm | State needed | Cost per request | When it is the right answer |
|---|---|---|---|
| Round-robin / random | none | ~0 | Uniform prompts, no shared prefixes, replicas identical. Also the correct baseline to measure against. |
| Least-outstanding-requests | in-flight count per replica | ~0 | Highly variable output lengths, no prefix reuse, single-GPU replicas. |
| Power-of-two-choices | two samples of the above | ~0 | Same, but with many replicas where a global minimum is stale by the time you use it. |
| Queue-length / token-load | scraped `/metrics` or router-local accounting | one scrape tick | Variable *prompt* lengths: request count is a bad proxy when one request is 256K tokens. |
| Prefix affinity (approximate) | text/block hash → replica map, router-local | hash + tree walk | Shared system prompts, multi-turn chat, few-shot templates. |
| Prefix affinity (precise, KV-event driven) | subscription to every replica's KV block events | tokenize + index lookup | High-value cache hits (agentic coding, long system prompts) where a wrong guess costs a full re-prefill. |
| Session affinity | session-id → replica binding with TTL | map lookup | Long multi-turn sessions, especially where the engine holds non-append-only state. |
| SLO / predicted-latency | trained latency model per pod | model inference per candidate | High variance in both prompt and output length, per-request SLOs. |
| PD-aware (two-profile) | all of the above, twice | two selections | Disaggregated pools only. |

### 3.2 Why prefix affinity is not just a cache optimization

The failure mode it prevents is not "slightly lower hit rate" — it is **fleet collapse**.
With round-robin over 8 replicas and a 6,000-token shared system prompt, every replica
re-prefills the prefix for every request, so prefill work scales with QPS while the
useful decode work does not. Measured, on identical hardware, both arms of the same
experiment (llm-d, 16×H100, 8 pods × TP=2, Qwen3-32B, 150 prefix groups, 6,000-token
shared prompt + 1,200-token question, 1,000-token output, Poisson rate ladder 3→60, 0
failed requests on either arm)
[src](https://raw.githubusercontent.com/llm-d/llm-d/main/guides/precise-prefix-cache-routing/benchmark-results/vllm-qwen3-32b-h100.md):

| Metric (vLLM arm) | k8s Service (RR) | llm-d precise | Δ |
|---|---:|---:|---:|
| Peak output tok/s | 6,986 | **14,892** | +113.2 % |
| Requests/s @ rate 60 | 6.57 | 14.60 | +122.2 % |
| TTFT p50 (s) | 54.6 | **0.19** | −99.7 % |
| TTFT p90 (s) | 135.5 | **0.26** | −99.8 % |
| ITL p50 (ms) | 46.4 | 56.8 | **+22.4 %** |

Same harness on SGLang
[src](https://raw.githubusercontent.com/llm-d/llm-d/main/guides/precise-prefix-cache-routing/benchmark-results/sglang-qwen3-32b-h100.md):
peak output 6,884 → **15,532** tok/s (+125.6 %), TTFT p50 81.9 s → 0.15 s, p90 132.9 s →
0.27 s, ITL p50 37.9 → 55.1 ms (**+45.4 %**).

Three things to take from this and nothing else:

1. **The gain is a saturation-regime gain.** "The two arms track each other until the
   fleet saturates" — below ~rate 15 the difference is within noise. If your pool is
   never near saturation, prefix routing buys you little and costs you a tokenize hop.
2. **ITL gets worse, deliberately.** "affinity routing packs more concurrent work onto
   cache-warm pods, so per-token decode is marginally slower." If your SLO is TPOT-dominated
   rather than TTFT-dominated, this is a regression, not an improvement. Weigh it against
   [`METHODOLOGY.md` §6](../METHODOLOGY.md#6-cost)'s S1 (TPOT ≤ 50 ms) operating point —
   at 56.8 ms ITL the vLLM arm is *outside* S1 while the round-robin arm at 46.4 ms is
   inside it, at one-seventh the throughput.
3. **The comparison is against round-robin, not against least-loaded.** Nobody has
   published, in a source this document could find, prefix-affinity vs. a well-tuned
   token-load router on identical hardware. ⚠️ see Open questions.

### 3.3 Precise vs approximate prefix knowledge

**Approximate** producers hash the request text (or tokens) into blocks and remember what
the router itself sent where. llm-d's `approx-prefix-cache-producer`, SGLang's
`cache_aware` radix tree, and Ray's prefix tree are all of this kind. They are cheap and
need nothing from the engine, but they drift: they do not see evictions, and they do not
see cache entries created by a *different* router replica.

**Precise** producers subscribe to the engine's own KV block events. llm-d's mechanism,
verbatim
[src](https://raw.githubusercontent.com/llm-d/llm-d/main/guides/precise-prefix-cache-routing/README.md):

> 1. **Model server pods publish KV-cache events** — each pod (vLLM or SGLang) runs with
>    `--kv-events-config '{...,"publisher":"zmq","endpoint":"$(KV_EVENTS_ENDPOINT)","topic":"kv@$(POD_IP):$(POD_PORT)@<model>"}'`
>    and `KV_EVENTS_ENDPOINT=tcp://*:5556` … The GPU vLLM backend (v0.26.0+) additionally
>    binds a ZMQ ROUTER socket on port 5559 and retains the last 10,000 batches in an
>    in-memory replay buffer for index recovery.
> 2. **Router subscribes per pod** … All replicas converge to the same index. When a
>    replay endpoint is available, each subscriber requests buffered events on first
>    connect (or after an EPP restart) to rebuild its KV-block index without waiting for
>    live traffic.
> 3. **Router tokenizes the prompt** … 4. **Filter + score**

The replay buffer is the part most people miss: without it, an EPP restart means a cold
index and a thundering herd of cache misses until traffic repopulates it.

**Block size must match on both sides.** The guide's default config pins vLLM
`--block-size 64` against the producer's `tokenProcessorConfig.blockSizeTokens: 64` (SGLang:
`--page-size=64`). A mismatch silently degrades hit detection.

### 3.4 Dynamo's cost model — the most explicit one published

Dynamo does not do affinity; it does **placement with a cost function**, published
verbatim [src](https://raw.githubusercontent.com/ai-dynamo/dynamo/main/docs/fern/pages/developer-guide/knowledge-base/modular-components/router/routing-concepts.md):

```text
raw_prefill_blocks      = active_prefill_blocks + incoming_prompt_blocks
adjusted_prefill_blocks = max(0, raw_prefill_blocks - overlap_credit_blocks)
potential_decode_blocks = active_decode_blocks + incoming_active_blocks
active_request_blocks   = decode_active_request_weight * active_requests
cost = prefill_load_scale * adjusted_prefill_blocks + potential_decode_blocks + active_request_blocks
```

with: "`overlap_credit_blocks` combines the configured device, host, disk, and
shared-cache credits … The adjusted prefill term is clamped at zero, so overlap credits
never make it negative … The router selects the lowest-cost eligible worker." And the
conditional-disaggregation exception: `cost = max(0, potential_decode_blocks -
overlap_credit_blocks) + active_request_blocks`, which "lets a cache-hot decode worker
bypass the ordinary prefill path."

Flags and defaults, from the Frontend Configuration Reference
[src](https://raw.githubusercontent.com/ai-dynamo/dynamo/main/docs/fern/pages/reference/components/frontend-configuration.mdx):

| Flag | Default | Meaning |
|---|---|---|
| `--router-mode` | `round-robin` | `round-robin \| random \| power-of-two \| kv \| direct \| least-loaded \| device-aware-weighted` |
| `--router-kv-overlap-score-credit` | `1.0` | Device-local prefix-overlap credit multiplier |
| `--router-kv-overlap-score-credit-decay` | `0.0` | Decays that credit for workers with excess active prefill |
| `--router-prefill-load-scale` | `1.0` | Weight of prompt-side load vs decode blocks |
| `--router-host-cache-hit-weight` | `0.75` | Credit for host-pinned (CPU offload) overlap |
| `--router-disk-cache-hit-weight` | `0.25` | Credit for disk/NVMe-tier overlap |
| `--router-decode-active-request-weight` | `0.0` | **Experimental**; block-equivalent cost per active request |
| `--router-temperature` | `0.0` | Softmax temperature over normalized cost logits; `0` = deterministic |
| `--router-kv-events` / `--no-router-kv-events` | `true` | Consume engine KV events, else predict from routing decisions |
| `--router-ttl-secs` | `120.0` | Block TTL in approximate mode |
| `--router-predicted-ttl-secs` | `null` | Side-index TTL for sibling co-location |
| `--router-track-active-blocks` | `true` | Count in-progress requests' blocks |
| `--router-track-output-blocks` | `false` | Also count generated blocks (with `nvext.agent_hints.osl` decay) |
| `--router-session-affinity-ttl-secs` | `null` | Enable session affinity with this idle TTL (1..31536000) |
| `--router-session-affinity-mode` | `hard` ⚠️ | `hard` = exact dispatch; `soft` = advisory target through normal selection. ⚠️ **TO BE VERIFIED** — every other default in this table was re-read off the Frontend Configuration Reference on 2026-09-19 and matched exactly; this row's *default* was the one value that page did not surface on re-fetch (the flag and its two values did). Confirm with `python -m dynamo.frontend --help` before relying on it. |
| `--router-queue-threshold` | `null` | Queue while all eligible workers exceed `threshold × max_num_batched_tokens` |
| `--router-queue-policy` | `fcfs` | `fcfs` optimizes tail TTFT; `wspt` optimizes average TTFT |
| `--router-replica-sync` | `false` | Best-effort active-sequence sync across router replicas |
| `--active-decode-blocks-threshold` / `--active-prefill-tokens-threshold[-frac]` | `null` | Busy-rejection thresholds (OR logic between the two prefill forms) |

Two tuning rules worth memorising, quoted
[src](https://raw.githubusercontent.com/ai-dynamo/dynamo/main/docs/fern/pages/developer-guide/knowledge-base/modular-components/router/configuration-and-tuning.md):

- "`--router-kv-overlap-score-credit` is the primary knob … Higher values steer requests
  toward workers with better cache overlap and reduce TTFT. … Lower values distribute load
  more evenly and reduce ITL." (Same TTFT-vs-ITL trade as §3.2, exposed as one float.)
- `--router-kv-overlap-score-credit-decay` exists specifically to stop starvation: "The
  router normalizes the excess active prefill blocks by the incoming request size and
  multiplies the configured overlap credit by `1 / (1 + decay * normalized_excess)`. For
  example, a decay of `1` halves device credit at one request-equivalent of excess prefill
  load."

`wspt` orders by `(1 + priority_jump) / scheduling_cost_tokens` where
`scheduling_cost_tokens = max(1, raw_isl_tokens - cached_tokens)` — i.e. shortest-uncached-
prompt-first. `fcfs` orders by `priority_jump - arrival_offset`.

**Deprecation trap.** `--router-kv-overlap-score-weight`, `--kv-overlap-score-weight`,
`DYN_ROUTER_KV_OVERLAP_SCORE_WEIGHT` and `DYN_OVERLAP_SCORE_WEIGHT` still work but warn,
and "Nonzero legacy values map to `prefill_load_scale` to preserve existing behavior
without changing overlap credit." Any tuning guide written before 2026 that tells you to
raise `kv-overlap-score-weight` is actually telling you to raise `prefill_load_scale`.

**A config-precedence trap that bites in Kubernetes**: "if the Frontend sets
`--router-kv-overlap-score-credit 2.5` but a worker set advertises only `--router-mode kv`,
the worker set uses the default overlap credit of `1.0`." Worker-advertised router config
*replaces* rather than merges with the frontend's. Check the `Activating prefill router`
log line.

### 3.5 llm-d's scorer/filter vocabulary and PD-aware profiles

The PD guide's shipped router configuration is the cleanest published example of
**phase-specific routing** — prefill is chosen for cache locality and prefill token load,
decode is chosen purely for in-flight request count
[src](https://raw.githubusercontent.com/llm-d/llm-d/main/guides/pd-disaggregation/router/pd-disaggregation.values.yaml):

```yaml
schedulingProfiles:
# Prefill does the prompt pass: keep prefix groups on cache-warm pods
# (affinity filter) and pick by queued prefill token load.
- name: prefill
  plugins:
  - pluginRef: prefill-filter
  - pluginRef: prefix-cache-affinity-filter
  - pluginRef: token-load-scorer
  - pluginRef: max-score-picker
# Pure decode is bound by concurrent in-flight requests, not prefill
# throughput: pick the least-busy endpoint.
- name: decode
  plugins:
  - pluginRef: decode-filter
  - pluginRef: active-request-scorer
  - pluginRef: max-score-picker
```

The `prefix-cache-affinity-filter` is gated by a **calibrated** constant, not a guess:

```yaml
- type: prefix-cache-affinity-filter
  parameters:
    # Measured with guides/recipes/router/calibration/calibrate.sh through the full
    # P/D path on the reference fleet (gpt-oss-120b, 8x prefill TP=1 on H200,
    # chunk 8192). Includes the NIXL KV transfer + sidecar hop, so it is the
    # operational ceiling the gate acts on. Re-measure for other hardware/models.
    peakPrefillThroughput: 33821
```

The aggregated guide's value on Qwen3-32B / H100 80 GB / TP=2 / chunk 8192 is
`peakPrefillThroughput: 15926`, measured by the same `calibrate.sh`
[src](https://raw.githubusercontent.com/llm-d/llm-d/main/guides/precise-prefix-cache-routing/router/precise-prefix-cache-routing.values.yaml).
**This constant is hardware- and model-specific and must be re-measured** — an
uncalibrated gate either never bypasses saturated cache-warm pods (tail collapse) or
always bypasses them (no affinity).

The escape-hatch behaviour is what stops hot-spotting: "the `prefix-cache-affinity-filter`
narrows candidates to the pods where the request's prefix blocks are resident (falling
back to the least-loaded pods when the cache-warm set is saturated past
`peakPrefillThroughput`), and the `token-load-scorer` picks the endpoint with the least
in-flight token load among them."

**HA caveat, quoted, and it is a real operational limit:** "The router runs as a **single
replica** by default: the `token-load-scorer`'s in-flight token accounting is local to
each EPP process, so two active-active replicas would each see only half the per-endpoint
load and mis-gate the affinity filter. The precise KV index itself is HA-safe (each
replica converges independently via pod-discovery)". Dynamo's equivalent is
`--router-replica-sync` (default `false`), and its session-affinity sync is explicitly
"advisory".

### 3.6 SGLang cache-aware, and its tunables

The default policy combines locality with a rebalance trigger; the parameters and
defaults, verbatim
[src](https://docs.sglang.io/advanced_features/sgl_model_gateway.html):

| Parameter | Default | Description |
|---|---|---|
| `--cache-threshold` | 0.3 | Minimum prefix match ratio for cache hit |
| `--balance-abs-threshold` | 64 | Absolute load difference before rebalancing |
| `--balance-rel-threshold` | 1.5 | Relative load ratio before rebalancing |
| `--eviction-interval-secs` | 120 | Cache eviction cadence in seconds |
| `--max-tree-size` | 67108864 | Maximum nodes in cache tree |

Dynamo ships a built-in worker-selection policy that reproduces this exact behaviour:
`dynamo-two-tier-cost-fn` "Ranks on two tiers instead of one additive cost: active-request
load first, then device-KV prefix overlap. … Thresholds and selection order ported from
the experimental SGLang router's `cache_aware_zmq` policy."

### 3.7 SLO-aware / predicted-latency routing — GA, and honestly benchmarked

llm-d v0.7 (2026-05) graduated predicted-latency scheduling to GA. Its own guide states
when **not** to use it: "Skip it when your pool is **heterogeneous** — mixed GPU types,
model variants, or serving configurations in the same pool will produce inaccurate
predictions, because the predictor assumes a single pod shape"
[src](https://raw.githubusercontent.com/llm-d/llm-d/main/guides/predicted-latency-routing/README.md).

Published results, all from that guide, all against a stated baseline:

| Workload | Fleet | vs plain k8s Service | vs the token/affinity scorer |
|---|---|---|---|
| Code generation, Qwen3-32B | 10 vLLM decode servers TP=2 (20×H100), concurrency 10→100 | +38 % input / +40 % output throughput; TTFT p50 −13 %, p90 −47 % | within ~1 % input throughput, ~2 % median TTFT; **TPOT p90 ~100 ms vs ~950 ms (−89 %)**; token scorer holds TTFT p90 ~13 % better |
| Agentic long-context, Qwen3-Coder-480B-A35B-FP8 | 8 vLLM on TPU v7x 2×2×1, prompts to ~256K | @conc 40: **87K vs 37K** input tok/s (~2.3×), TTFT p50 **1.8 s vs 17.4 s**, p90 34 s vs 69 s, TPOT p50/p90 30/45 ms vs 48/372 ms; avg −88 % median TTFT over conc 5→40 | within a few percent through ~conc 60; token scorer holds the tail better at conc 70–80 |
| P/D disagg, gpt-oss-120b | 8 prefill TP=1 + 2 decode TP=4 on 16×H200, RDMA/RoCE | — | both saturate at **44.3 req/s ≈ 234K tok/s**, TPOT identical; @45 req/s TTFT p50 −20 % (320 vs 401 ms), p90 −25 %, **p99 −62 % (812 ms vs 2.1 s)**; within noise below ~30 req/s |
| Multimodal, Qwen3-VL-32B | 8 vLLM TP=2 (16×H200), 3×720p images + ~1.3K text tokens | +30 % throughput @ rate 35 (9,449 vs 7,272 tok/s), median TTFT **29× lower** (0.66 s vs 19.2 s), 1 failed request vs 27 | — |

And the caveat that decides whether you can use it at all: "**Ramped load hides the
predictor's cold start.** The predictor trains in-run … A step load onto a freshly
restarted EPP does pay a cold-start penalty, since an EPP restart resets the model."

**Decision rule.** Predicted-latency buys you (a) no hand-tuned scoring weights and (b)
much better TPOT/TTFT *tails*; it does not beat a well-calibrated token-load + prefix
router on medians. Take it when your prompt/output length distribution is wide and you
cannot keep `peakPrefillThroughput` and scorer weights calibrated as traffic shifts. Skip
it on a heterogeneous pool, and skip it if your traffic arrives as steps onto cold EPPs.

### 3.8 Independently reported gains

| Source | Setup | Measured |
|---|---|---|
| Baseten + Dynamo KV-aware routing, 2026-03-16 [src](https://www.baseten.co/blog/how-baseten-achieved-2x-faster-inference-with-nvidia-dynamo/) | Qwen3 Coder 480B, 4 GPU replicas, ~50K in / 1K out, 89 % cache hit across replicas, vs **random** routing | TTFT **−50 %** avg, TPOT **−34 %**, P95 latency −48 %, P99 −49 %, **+61 % req/s**, +62 % output tok/s |
| llm-d README (Tesla / Red Hat) [src](https://raw.githubusercontent.com/llm-d/llm-d/main/README.md) | Llama 3.1 70B, 4× AMD MI300X, prefix-cache-aware vs round-robin | **3× output throughput, 2× faster TTFT** |
| llm-d README (AWS) | GPT-OSS on p6-b200, PD disagg vs standard vLLM | up to **+70 % tokens/sec** |
| llm-d README (Oracle) | GPT-OSS-120B and Llama 3.3 70B on MI300X, disagg on identical infra | **10–30 %** throughput |
| llm-d v0.5 | 16×16 B200 wide-EP | **50k tok/s** cluster, ~3.1k tok/s per decode GPU |
| llm-d v0.5 | 4×H100, hierarchical KV offload at 250 concurrent users vs GPU-only | **13.9×** throughput |
| Ray Serve `PrefixCacheAffinityRouter` (Ray 2.49) [src](https://docs.ray.io/en/latest/serve/llm/prefix-aware-request-router.html) | 32B model | **−60 % TTFT**, **>+40 %** end-to-end throughput |
| CacheWise (arXiv 2606.16824, 2026-06-15) [src](https://arxiv.org/abs/2606.16824) | coding-agent sessions, prefix-aware scheduling + reuse-aware eviction in vLLM | **2–2.6×** fewer KV evictions, **up to 3.5×** faster session completion |

Cross-check against this tree: [`cross-cutting/serving-optimizations.md`
§1.2](../cross-cutting/serving-optimizations.md) already holds the *intra-replica* prefix
cache hit-rate effects; the numbers above are the *inter-replica* routing effect on top of
them, and the two multiply — routing is what makes the engine's cache actually fire.

### 3.9 Anti-patterns

- **Consistent hashing on the raw prompt.** Cheap, and it does produce affinity, but it
  has no load term at all: one hot prefix group pins one replica and you get the ITL
  collapse without the throughput win. Every production router above adds an explicit load
  escape hatch (`peakPrefillThroughput`, `--balance-abs-threshold`,
  `imbalanced_threshold`, `credit-decay`). If you hash, hash *and* bound.
- **Session affinity at the ingress without affinity in the router.** Dynamo's own
  guidance: "For strict affinity, configure the ingress or load balancer to consistently
  route a session to one frontend … When hashing at ingress, hash the raw session header
  rather than Dynamo's normalized internal `session_id`."
- **Turning on `--router-decode-active-request-weight` by default.** Dynamo: "It can
  regress throughput, TTFT, and ITL when decode remains primarily memory-bound, so
  benchmark representative traffic and start with a small weight before increasing it."
- **A queue threshold on SGLang without `--max-prefill-tokens`.** Documented footgun:
  when `--max-prefill-tokens` is unset, the MDC's `max_num_batched_tokens` falls back to
  `max_total_num_tokens` (the whole KV pool), so "a threshold like `1.0` may effectively
  never queue."

---

## 4. Gateways: Gateway API Inference Extension, Agent Router, and the rest

### 4.1 InferencePool (`inference.networking.k8s.io/v1`)

The v1 API is GA (since GIE v1.0.0) and is deliberately tiny — a selector, ports, and a
pointer at an endpoint picker
[src](https://gateway-api-inference-extension.sigs.k8s.io/api-types/inferencepool/):

```yaml
apiVersion: inference.networking.k8s.io/v1
kind: InferencePool
metadata:
  name: vllm-qwen3-32b
spec:
  selector:
    matchLabels:
      app: vllm-qwen3-32b
  targetPorts:
    - number: 8000
  endpointPickerRef:
    name: vllm-qwen3-32b-epp
    port:
      number: 9002
    failureMode: FailOpen
```

`endpointPickerRef` is **optional as of v1.5.0** (2026-04-19); `failureMode: FailOpen`
means the gateway falls back to its own load balancing if the EPP is unreachable — which
is the difference between "routing gets worse" and "the model is down". Choose
`FailClose` only where a wrong placement is worse than an error. `InferencePoolImport`
exists for multi-cluster pools
[src](https://gateway-api-inference-extension.sigs.k8s.io/).

The mechanism is Envoy `ext_proc`: "the Gateway will forward the request information to
the endpoint selection extension for that pool", which replies with the endpoint. llm-d
requires `FULL_DUPLEX_STREAMED` body modes (§2.8).

### 4.2 Agent Router (ex-Envoy AI Gateway) — the L1 tier

Release history, verbatim dates and headline features
[src](https://theagentrouter.ai/release-notes/): v1.1.0 (2026-08-21) "Token counting,
per-request credentials, stream idle timeout, MCP hostname routing, OpenTelemetry GenAI
tracing, and HTTP CONNECT egress"; v1.0.0 (2026-06-23) "The first stable, generally
available release" with "16 LLM providers, an MCP gateway, multimodal and audio endpoints,
enterprise observability, and multi-tenant routing"; v0.7.0 (2026-06-04) "quota-aware rate
limiting"; v0.3.0 (2025-08-21) "Intelligent inference routing with Endpoint Picker
Provider".

**Model routing to InferencePools**, verbatim
[src](https://theagentrouter.ai/docs/next/capabilities/inference/aigatewayroute-inferencepool/):

```yaml
apiVersion: aigateway.envoyproxy.io/v1beta1
kind: AIGatewayRoute
metadata:
  name: inference-pool-with-aigwroute
  namespace: default
spec:
  parentRefs:
    - name: inference-pool-with-aigwroute
      kind: Gateway
      group: gateway.networking.k8s.io
  rules:
    - matches:
        - headers:
            - type: Exact
              name: x-ai-eg-model
              value: meta-llama/Llama-3.1-8B-Instruct
      backendRefs:
        - group: inference.networking.k8s.io
          kind: InferencePool
          name: vllm-llama3-8b-instruct
```

The `x-ai-eg-model` header is set by the gateway from the request body's `model` field —
that is the model-alias mechanism, and it is also how a request can be sent to an external
provider instead of a local pool by pointing a rule's `backendRefs` at an
`AIServiceBackend` rather than an `InferencePool`.

### 4.3 Token-based rate limiting and tenant quotas

Two declarations are needed: the route declares what a request *costs*, the traffic policy
declares the *budget*
[src](https://theagentrouter.ai/docs/0.1/capabilities/usage-based-ratelimiting/).

```yaml
# 1. On the AIGatewayRoute: what counts as cost
spec:
  llmRequestCosts:
    - metadataKey: llm_input_token
      type: InputToken
    - metadataKey: llm_output_token
      type: OutputToken
    - metadataKey: llm_total_token
      type: TotalToken
    - metadataKey: custom_cost
      type: CEL
      cel: "input_tokens * 0.5 + output_tokens * 1.5"
```

```yaml
# 2. On a BackendTrafficPolicy: the budget, keyed per user × model
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: BackendTrafficPolicy
metadata:
  name: model-specific-token-limit-policy
  namespace: default
spec:
  targetRefs:
    - name: envoy-ai-gateway-token-ratelimit
      kind: Gateway
      group: gateway.networking.k8s.io
  rateLimit:
    type: Global
    global:
      rules:
        - clientSelectors:
            - headers:
                - name: x-user-id
                  type: Distinct
                - name: x-ai-eg-model
                  type: Exact
                  value: gpt-4
          limit:
            requests: 1000
            unit: Hour
          cost:
            request:
              from: Number
              number: 0        # request cost 0 so only tokens count
            response:
              from: Metadata
              metadata:
                namespace: io.envoy.ai_gateway
                key: llm_total_token
```

The `CEL` cost type is the one that matters for a mixed fleet: input and output tokens do
not cost the same to serve, and the ratio differs per model — for these five models the
per-1M input/output split is in
[`matrix/cost-matrix.md`](../matrix/cost-matrix.md), and a CEL expression is the place to
encode it so a tenant's quota tracks real GPU cost rather than raw token count.

**Design consequence.** Token cost is only known *after* the response, so a token budget
is enforced with a one-request lag: a single request can overshoot the bucket. For hard
per-tenant isolation you need an admission limit too — a concurrency cap
(`--max-concurrent-requests` on the SGLang gateway) or a fairness policy in the router
(llm-d `static-usage-limit-policy`, AIBrix `vtc-basic`).

**Engine-level token limiting**, as a second line of defence, from the SGLang gateway:
`--max-concurrent-requests 256`, `--rate-limit-tokens-per-second 512`, `--queue-size 128`,
`--queue-timeout-secs 30`, with documented responses "`429 Too Many Requests` when queue
is full" and "`408 Request Timeout` when queue timeout expires"
[src](https://docs.sglang.io/advanced_features/sgl_model_gateway.html).

**API keys / auth.** Do *not* rely on the engine. vLLM's own doc warns: "The `--api-key`
option (or `VLLM_API_KEY` environment variable) only [enables a static key] … Do not rely
on `--api-key`"
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/serving/online_serving/openai_compatible_server.md).
Terminate auth at L1. The SGLang gateway does offer `--api-key`, TLS and mTLS
(`--tls-cert-path`, `--client-cert-path`, `--ca-cert-path`) if you need a second boundary.

### 4.4 NGINX / Kong / Traefik

NGINX Gateway Fabric implements the Gateway API Inference Extension
[src](https://docs.nginx.com/nginx-gateway-fabric/how-to/gateway-api-inference-extension/),
as do Istio
[src](https://istio.io/latest/docs/tasks/traffic-management/ingress/gateway-api-inference-extension/)
and (per llm-d's guides) GKE Inference Gateway, kgateway and agentgateway — llm-d's Helm
chart takes `provider.name ∈ {none, gke, agentgateway, istio}`. **⚠️ TO BE VERIFIED** —
current Kong and Traefik AI-plugin capabilities (token rate limiting, InferencePool
support) were not fetched on 2026-09-19 because the session's WebSearch budget was
exhausted (see Open questions). Treat "any ext_proc-capable, Gateway-API-conformant proxy
works" as the safe general statement, and the three named above (Envoy Gateway/Agent
Router, Istio, NGINX Gateway Fabric) as the ones with a primary source.

### 4.5 Fallback to external APIs

Two published mechanisms:

1. **Agent Router**: cross-backend failover, present since v0.2.0 (2025-06-05), with
   "automatic failover mechanisms to ensure service reliability" and 16 providers in v1.0.0
   [src](https://theagentrouter.ai/release-notes/). Route rules point at
   `AIServiceBackend`s, so a fallback is a route-level change, not a code change.
2. **SGLang Model Gateway OpenAI backend**: `--backend openai --worker-urls
   https://api.openai.com`, which "proxies OpenAI-compatible endpoints to external vendors
   (OpenAI, xAI, etc.) while keeping chat history and multi-turn orchestration local"
   [src](https://docs.sglang.io/advanced_features/sgl_model_gateway.html).

For these five models the fallback target is the model vendor's own API; the break-even
prices are in [`matrix/cost-matrix.md` §6.1](../matrix/cost-matrix.md) (DeepSeek $0.60,
Moonshot $15.00, Qwen $3.00 per 1M output tokens) — which is also the ceiling on what an
overflow request is worth paying.

---

## 5. Multi-model and multi-tenant serving

### 5.1 Model-per-pool is the current architecture, not a choice

Restating the constraint from §1.2: one base model per `InferencePool`; multiple models =
multiple pools + header-based fan-out at L1. llm-d ships the **Inference Payload Processor
(IPP)** for exactly this: it "extracts the model name from the request body and sets
routing headers. HTTPRoutes then match these headers to direct traffic to the appropriate
InferencePool"
[src](https://raw.githubusercontent.com/llm-d/llm-d/main/guides/multi-model-routing/README.md).

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: qwen-route
spec:
  parentRefs:
  - name: llm-d-inference-gateway
  rules:
  - matches:
    - headers:
      - name: X-Gateway-Base-Model-Name
        value: Qwen/Qwen3-32B
    backendRefs:
    - group: inference.networking.k8s.io
      kind: InferencePool
      name: qwen-pool
```

Two constraints the guide states explicitly: "All model names must be globally unique
across all InferencePools", and do **not** create pool-level catch-all routes
(`httpRoute.create=true`) alongside header routes — "Pool-level catch-all routes would
conflict with header-based routing."

The alternative is a gateway that natively multiplexes models: the SGLang Model Gateway's
IGW mode (`--enable-igw`) with per-model policies and dynamic worker registration
(`POST /workers` with `model_id`, `priority`, `labels`), or AIBrix's gateway.

### 5.2 LoRA multiplexing

vLLM serves adapters on a shared base-model replica
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/lora.md):

```bash
vllm serve meta-llama/Llama-3.2-3B-Instruct \
    --enable-lora \
    --lora-modules sql-lora=jeeejeee/llama32-3b-text2sql-spider \
    --max-lora-rank 64
```

with runtime load/unload behind `VLLM_ALLOW_RUNTIME_LORA_UPDATING=True` and
`POST /v1/load_lora_adapter`. The doc's sizing rule, verbatim: "if your LoRA adapters have
ranks [16, 32, 64], use `--max-lora-rank 64` rather than 256" — because the parameter
"affects memory allocation and performance."

Routing an adapter name to the right *pool* is IPP's job; routing it to the right
*replica* (one that has the adapter resident) is the EPP's — llm-d v0.5 (2026-02) added
"cache-aware LoRA routing", and the EPP's data layer polls `/v1/models` for "served models
and LoRA adapters". The adapter→base-model mapping is a ConfigMap:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: qwen-model-mapping
  labels:
    inference.llm-d.ai/ipp-managed: "true"
data:
  baseModel: "Qwen/Qwen3-32B"
  adapters: |
    - food-review-1
    - travel-assistant
```

**For this repo, LoRA multiplexing is relevant only to Qwen3.8-27B and Marlin-2B.** The
three large MoE checkpoints are one-model-per-pool by construction — a 510 GB or 1,561 GB
checkpoint does not share a replica with anything
([`matrix/fit-matrix.md`](../matrix/fit-matrix.md)).

### 5.3 Shared pool vs pool-per-model — the decision rule

| Choose | When |
|---|---|
| **Pool per model** (the default, and mandatory for a base model) | The models differ in weights. Also when SLOs differ enough that you want independent autoscaling and independent saturation. |
| **Shared pool + LoRA multiplexing** | Same base model, many fine-tunes, each with traffic too low to justify a replica. The break-even is roughly: adapter QPS × cost-per-replica-hour < the marginal ITL cost of multiplexing. ⚠️ no published measurement of vLLM 0.29 multi-LoRA ITL overhead was found on 2026-09-19. |
| **Shared pool across *different* models** | Not supported by InferencePool today. Only via a gateway that multiplexes (SGLang IGW, AIBrix). |

### 5.4 Priority classes, fairness and quotas

Four layers can enforce priority, and they do not coordinate:

1. **L1 quota** — token budget per tenant (§4.3). Rejects, does not reorder.
2. **Router flow control** — llm-d's admission layer, off by default: "enable it with
   `featureGates: ["flowControl"]` … `fcfs-ordering-policy`,
   `global-strict-fairness-policy`, and `static-usage-limit-policy` are configured when
   absent. `utilization-detector` is configured as the saturation detector when none is
   set"
   [src](https://raw.githubusercontent.com/llm-d/llm-d-router/main/docs/architecture.md).
3. **Router queue** — Dynamo's policy-class queues. The pending-queue key is
   `(strict_priority, due_at, policy_key)`; "Higher strict tiers always win", set by
   `nvext.agent_hints.strict_priority`, with `nvext.agent_hints.priority` adjusting
   ordering within a tier. Queueing is off until `--router-queue-threshold` is set, and
   "Priority hints have no router-level effect when requests do not enter this queue."
4. **Engine scheduler** — vLLM `--scheduling-policy priority` (default `fcfs`).

**Decision rule.** Put *rejection* at L1 (it is the only layer that knows who is paying),
*ordering* in the router (it is the only layer that sees the whole pool), and leave the
engine on `fcfs` unless you have a measured head-of-line-blocking problem — engine-level
priority reorders within one replica's batch only, which is the smallest possible lever.
For per-user fairness specifically, AIBrix's `vtc-basic` ("balances per-user token
fairness and pod utilization") is the only published router-level *token*-fairness
implementation found.

---

## 6. Streaming, cancellation, timeouts, retries and hedging

### 6.1 Streaming transports

SSE over HTTP/1.1 or HTTP/2 is the universal path (`/v1/chat/completions` with
`stream: true`); WebSocket appears only for realtime/audio surfaces (Dynamo exposes
`/v1/realtime`). Two knobs that prevent the classic "idle proxy kills the stream" failure:

- **Dynamo**: `DYN_HTTP_SSE_KEEP_ALIVE_INTERVAL_MS` (default **0**, i.e. *off*) —
  "Interval in milliseconds between SSE comment frames while a streaming response has no
  data"
  [src](https://raw.githubusercontent.com/ai-dynamo/dynamo/main/docs/fern/pages/reference/components/frontend-configuration.mdx).
  Set this if anything between client and frontend has an idle timeout shorter than your
  worst-case TTFT. On a 1M-context Kimi-K3 prefill, that is not hypothetical.
- **Agent Router v1.1.0** added a "stream idle timeout"
  [src](https://theagentrouter.ai/release-notes/).
- **TRT-LLM**: `server_keep_alive_timeout` (default **10** s) — raise it "when clients
  hold large idle connection pools and hit `Connection reset by peer` on a reused
  connection"
  [src](https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/docs/source/features/disagg-serving.md).

### 6.2 Cancellation and disconnect propagation

| Layer | Documented behaviour as of 2026-09-19 |
|---|---|
| vLLM | Exposes an `abort` finish reason: `vllm:request_success_total{finished_reason="abort",...}` [src](https://docs.vllm.ai/en/latest/design/metrics/). ⚠️ **TO BE VERIFIED** — the metrics doc "does not explicitly explain the relationship between client disconnections and the 'abort' finish reason"; the mapping from a dropped client socket to an engine abort is not stated in a doc this pass could fetch. Monitor the counter and validate empirically (benchmark B-cancel below). |
| SGLang Model Gateway | Golang bindings documented with "Full streaming support with context cancellation"; `/v1/responses/{id}/cancel` for background responses; the HTTP PD router "rejects detached background requests because it does not implement their retrieval/cancel lifecycle" [src](https://docs.sglang.io/advanced_features/sgl_model_gateway.html). |
| Dynamo | Frontend exposes `is_cancelled()` to route extensions; session-affinity semantics are explicitly cancellation-aware — "A later stream error or cancellation does not roll back the rebind", and "When a request lease ends after EOF, early drop, error, or cancellation, the idle timer restarts." Graceful shutdown waits `DYN_HTTP_GRACEFUL_SHUTDOWN_TIMEOUT_SECS` (default 5) for admitted response bodies. |
| TRT-LLM disagg | `kv_transfer_timeout_ms` (default **60000**) "bounds how long a request may wait for its KV cache before it is cancelled and cleaned up." |
| llm-d / Envoy | ext_proc must be `FULL_DUPLEX_STREAMED`, which is what allows the proxy to see and propagate a half-close mid-stream. |

**This is the weakest-documented area of the whole stack** and it is the one that costs
real GPU-seconds: an abandoned agentic request that keeps decoding to 2,000 tokens is pure
waste. Verify it per engine version rather than assuming.

### 6.3 Retries, backoff and circuit breaking

The SGLang Model Gateway publishes the most complete set of defaults
[src](https://docs.sglang.io/advanced_features/sgl_model_gateway.html):

| Parameter | Default | | Parameter | Default |
|---|---|---|---|---|
| `--retry-max-retries` | 5 | | `--cb-failure-threshold` | 5 |
| `--retry-initial-backoff-ms` | 50 | | `--cb-success-threshold` | 2 |
| `--retry-max-backoff-ms` | 5000 | | `--cb-timeout-duration-secs` | 30 |
| `--retry-backoff-multiplier` | 2.0 | | `--cb-window-duration-secs` | 60 |
| `--retry-jitter-factor` | 0.1 | | `--disable-circuit-breaker` | false |

**Retryable status codes: 408, 429, 500, 502, 503, 504.** Circuit-breaker states are
Closed / Open / Half-Open, per worker.

**Retry safety rule.** An LLM completion is only idempotent if it is deterministic
(`temperature: 0` plus a deterministic kernel path — SGLang documents a deterministic
inference mode) *and* the client can tolerate a duplicate charge. Otherwise: retry freely
on a pre-stream failure (connection refused, 503 before the first SSE frame), and **never
retry after the first token has been emitted** — the client has already seen output, and a
retry produces a different continuation. The correct mid-stream recovery is migration, not
retry.

### 6.4 Migration instead of hedging

Dynamo is the only stack in this set with published mid-stream recovery: "a worker fails
mid-generation or its shutdown grace period expires" triggers migration of "the
in-progress request to a healthy worker and continue[s] from the exact point of failure —
no tokens lost or duplicated", replaying cached token state onto the new worker. Flags:
`--migration-limit N` (`DYN_MIGRATION_LIMIT`) and `--migration-max-seq-len`
(`DYN_MIGRATION_MAX_SEQ_LEN`, which caps the memory cost of token tracking). **Not
supported** for multi-choice requests (`n > 1`) or guided decoding — "the guided-decoding
FSM initializes fresh per worker, causing corrupted output (typically duplicated or nested
JSON)"
[src](https://docs.nvidia.com/dynamo/user-guides/fault-tolerance/request-migration).

That exclusion matters here: **structured output plus migration is unsafe**, and every one
of our recipe deployments enables structural tags (`--dyn-enable-structural-tag`). Choose
one per pool.

**Hedging** (sending the same request to two replicas and taking the first response) is
not offered by any of these routers and should not be built: it doubles prefill cost on a
GPU-bound system, and the very workloads where TTFT is worst (long shared prefixes) are
the ones where affinity routing already fixes it. Use `--router-temperature > 0` if you
want stochastic spreading without duplication.

### 6.5 Overload signalling

Dynamo returns **529** by default for admission-control rejection
(`DYN_HTTP_OVERLOAD_STATUS_CODE`, default `529`, with the note "Use `503` only for clients
that [require it]"). Busy thresholds are `--active-decode-blocks-threshold`,
`--active-prefill-tokens-threshold` and `--active-prefill-tokens-threshold-frac`, combined
with OR logic, and settable at runtime via `POST /busy_threshold`. Body size is capped at
`DYN_HTTP_BODY_LIMIT_MB` (default **192**) — relevant for Marlin-2B, where inline video
payloads are large; the doc's own advice is to "keep HTTP images as `https://` URLs so
only a reference crosses the request plane."

### 6.6 Chunked decode — head-of-line blocking without PD disaggregation

llm-d's pd-sidecar ships an experimental `--decode-chunk-size` (default `0` = disabled)
that "splits the decode stage into a sequence of shorter decode calls, each capped at a
configurable token budget", re-emitting SSE deltas in real time and concatenating for
non-streaming
[src](https://raw.githubusercontent.com/llm-d/llm-d-router/main/docs/architecture.md).
Stated benefits: better average TTFT, "Prevent head-of-line blocking by long requests in
run-to-completion", more predictable execution time. Guidance: "For best performance use a
multiple of the KV cache block size."

This is the cheap alternative to PD disaggregation for a fleet too small for it
([`cross-cutting/serving-optimizations.md` §3.5](../cross-cutting/serving-optimizations.md)
— "disaggregation is a thousand-GPU problem"). ⚠️ No measured numbers published for
chunked decode as of 2026-09-19.

---

## 7. Reference deployment manifests

All four are quoted from upstream docs/repos, trimmed only where marked.

### 7.1 Dynamo — aggregated, KV-aware routing (the DeepSeek-V4.1-Flash recipe)

From `recipes/deepseek-v4.1-flash/sglang/agg-gb200/deploy.yaml` at tag
`v1.6.0-deepseek-v4.1-flash-dev.1`
[src](https://github.com/ai-dynamo/dynamo/blob/release/1.6.0-deepseek-v4.1-flash-dev.1/recipes/deepseek-v4.1-flash/sglang/agg-gb200/deploy.yaml).
Header comment verbatim: "DeepSeek-V4.1-Flash on SGLang, aggregated. Two workers, TP4
each, 8x GB200. KV-aware routing, DSpark speculative decoding, up to 1M tokens of
context. Do not set the attention, MoE, or GEMM backend flags. SGLang resolves them for
this model. A hand-set backend flag selects a slower fallback."

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: deepseek-v41-flash-sglang-gb200-agg-sglang-config
data:
  worker.yaml: |-
    model-path: deepseek-ai/DeepSeek-V4.1-Flash
    served-model-name: deepseek-ai/DeepSeek-V4.1-Flash
    trust-remote-code: true
    tp-size: 4
    ep-size: 4
    page-size: 256
    mem-fraction-static: 0.8
    max-running-requests: 256
    speculative-algorithm: DSPARK
    speculative-dspark-block-size: 5
    reasoning-parser: auto
    enable-metrics: true
    watchdog-timeout: 3600
    host: 0.0.0.0
    port: 30000
---
apiVersion: nvidia.com/v1beta1
kind: DynamoGraphDeployment
metadata:
  name: deepseek-v41-flash-sglang-gb200-agg
spec:
  backendFramework: sglang
  components:
    - name: Frontend
      type: frontend
      replicas: 1
      podTemplate:
        spec:
          containers:
            - name: main
              image: nvcr.io/nvidia/ai-dynamo/sglang-runtime:1.6.0-deepseek-v4.1-flash-dev.1
              command: [python3, -m, dynamo.frontend]
              args: ["--http-port", "8000"]
              env:
                - {name: DYN_ROUTER_MODE, value: kv}        # <-- the whole routing decision
              ports: [{name: http, containerPort: 8000, protocol: TCP}]
              resources: {requests: {cpu: "16", memory: 128Gi}}
    - name: Worker
      type: worker
      replicas: 2
      sharedMemorySize: 200Gi
      podTemplate:
        spec:
          terminationGracePeriodSeconds: 60
          containers:
            - name: main
              image: nvcr.io/nvidia/ai-dynamo/sglang-runtime:1.6.0-deepseek-v4.1-flash-dev.1
              command: [python3, -m, dynamo.sglang]
              args:
                - --config
                - /etc/sglang/worker.yaml
                # The KV router scores workers from these events.
                - --kv-events-config
                - '{"publisher":"zmq","topic":"kv-events","endpoint":"tcp://*:5557"}'
                - --dyn-reasoning-parser
                - deepseek_v41
                - --dyn-tool-call-parser
                - deepseek_v41
                - --dyn-enable-structural-tag
                - --cuda-graph-max-bs-decode
                - "64"
              env:
                # Without these two, every start pays minutes of DeepGEMM JIT.
                - {name: SGLANG_JIT_DEEPGEMM_PRECOMPILE, value: "0"}
                - {name: SGLANG_JIT_DEEPGEMM_FAST_WARMUP, value: "1"}
                # Keep --enable-metrics on, but turn the forward-pass publisher
                # off. It crashes this model on a null seq_lens_cpu.
                - {name: DYN_FORWARDPASS_METRIC_PORT, value: ""}
              ports:
                - {name: system, containerPort: 9090, protocol: TCP}
                - {name: kv-events, containerPort: 5557, protocol: TCP}
              resources:
                requests: {cpu: "16", memory: 512Gi, ephemeral-storage: 64Gi, nvidia.com/gpu: "4"}
                limits:   {nvidia.com/gpu: "4"}
```

The release notes are explicit that this is not a performance claim: "Day-0 functional
scope only; no published performance claim", "neither is benchmarked", and for the
disaggregated sibling profile: "**Disaggregated SGLang does not support DSpark for this
model. Use the aggregated target for speculative decoding**" and "**KV-aware routing is a
no-op on the disaggregated target with a single decode worker**"
[src](https://github.com/ai-dynamo/dynamo/releases/tag/v1.6.0-deepseek-v4.1-flash-dev.1).

### 7.2 llm-d — precise prefix-cache routing on Kubernetes

Install (Standalone Mode), verbatim
[src](https://raw.githubusercontent.com/llm-d/llm-d/main/guides/precise-prefix-cache-routing/README.md):

```bash
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api-inference-extension/${GAIE_URL}/v1-manifests.yaml
helm install ${GUIDE_NAME} ${ROUTER_STANDALONE_CHART} \
  -f ${REPO_ROOT}/guides/recipes/router/base.values.yaml \
  -f ${REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml \
  -n ${NAMESPACE} --version ${ROUTER_CHART_VERSION}
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/modelserver/gpu/vllm/base/
kubectl apply -n ${NAMESPACE} -k ${REPO_ROOT}/guides/${GUIDE_NAME}/render/
```

Gateway Mode swaps the chart and sets `provider.name ∈ {none, gke, agentgateway, istio}`
with `httpRoute.create=true`.

The EPP plugin config (the part that actually is the routing policy), verbatim
[src](https://raw.githubusercontent.com/llm-d/llm-d/main/guides/precise-prefix-cache-routing/router/precise-prefix-cache-routing.values.yaml):

```yaml
router:
  epp:
    replicas: 1
    flags:
      ha-enable-leader-election: false
    pluginsConfigFile: "precise-prefix-cache-routing-plugins.yaml"
    pluginsCustomConfig:
      precise-prefix-cache-routing-plugins.yaml: |
        apiVersion: llm-d.ai/v1alpha1
        kind: EndpointPickerConfig
        plugins:
          - type: token-producer
            parameters:
              modelName: Qwen/Qwen3-32B
              vllm:
                url: "http://precise-prefix-cache-routing-render:8000"
          - type: endpoint-notification-source
          - type: precise-prefix-cache-producer
            parameters:
              tokenProcessorConfig:
                blockSizeTokens: 64
              speculativeIndexing: true
              indexerConfig:
                kvBlockIndexConfig:
                  enableMetrics: true
              kvEventsConfig:
                topicFilter: "kv@"
                concurrency: 8
                discoverPods: true
                podDiscoveryConfig:
                  socketPort: 5556
                  replaySocketPort: 5559
          - type: inflight-load-producer
            parameters:
              prefixMatchInfoProducerName: precise-prefix-cache-producer
          - type: prefix-cache-affinity-filter
            parameters:
              prefixMatchInfoProducerName: precise-prefix-cache-producer
              peakPrefillThroughput: 15926   # calibrate.sh on YOUR fleet
          - type: token-load-scorer
        dataLayer:
          sources:
            - pluginRef: endpoint-notification-source
              extractors:
                - pluginRef: precise-prefix-cache-producer
        schedulingProfiles:
          - name: default
            plugins:
              - pluginRef: prefix-cache-affinity-filter
              - pluginRef: token-load-scorer
  modelServers:
    type: vllm
    targetPorts: [{number: 8000}]
    matchLabels:
      llm-d.ai/guide: "precise-prefix-cache-routing"
```

The model server side must publish events and match the block size:
`--block-size 64` on vLLM (`--page-size=64` on SGLang) plus
`--kv-events-config '{...,"publisher":"zmq","endpoint":"$(KV_EVENTS_ENDPOINT)","topic":"kv@$(POD_IP):$(POD_PORT)@<model>"}'`
with `KV_EVENTS_ENDPOINT=tcp://*:5556`.

### 7.3 Plain vLLM / SGLang behind the SGLang Model Gateway

Simplest useful production shape — no Gateway API, no EPP, Kubernetes service discovery,
cache-aware policy
[src](https://docs.sglang.io/advanced_features/sgl_model_gateway.html):

```bash
python -m sglang_router.launch_router \
  --service-discovery \
  --selector app=sglang-worker role=inference \
  --service-discovery-namespace production \
  --service-discovery-port 8000 \
  --policy cache_aware \
  --cache-threshold 0.5 \
  --balance-abs-threshold 32 \
  --balance-rel-threshold 1.5 \
  --max-concurrent-requests 256 \
  --queue-size 128 --queue-timeout-secs 30 \
  --prometheus-host 0.0.0.0 --prometheus-port 29000
```

PD mode, with per-role policies:

```bash
python -m sglang_router.launch_router \
  --pd-disaggregation \
  --prefill http://prefill1:30001 9001 \
  --decode http://decode1:30011 \
  --prefill-policy cache_aware \
  --decode-policy power_of_two
```

(In Kubernetes: `--prefill-selector app=sglang component=prefill`,
`--decode-selector app=sglang component=decode`; "Prefill pods can expose bootstrap ports
via the `sglang.ai/bootstrap-port` annotation. RBAC must allow `get`, `list`, and `watch`
on pods.")

`--prefill-policy cache_aware` / `--decode-policy power_of_two` is the same phase split as
llm-d's two profiles in §3.5, reached independently by two projects — good evidence it is
the right shape.

### 7.4 TensorRT-LLM `trtllm-serve disaggregated` with a coordinator fleet

Verbatim
[src](https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/docs/source/features/disagg-serving.md):

```yaml
hostname: localhost
port: 8000
backend: pytorch
# Run 4 stateless disaggregated-server workers on port 8000, with an implicit
# coordinator started in-process on port 7999 (port - 1).
num_workers: 4
context_servers:
  num_instances: 2
  urls:
      - "localhost:8001"
      - "localhost:8002"
  router:
    type: kv_cache_aware
generation_servers:
  num_instances: 1
  urls:
      - "localhost:8003"
  router:
    type: kv_cache_aware
```

with the workers started as:

```bash
echo -e "disable_overlap_scheduler: True\ncache_transceiver_config:\n  backend: NIXL" > context_config.yml
echo -e "cache_transceiver_config:\n  backend: NIXL" > gen_config.yml
trtllm-serve $MODEL --host localhost --port 8001 --backend pytorch --config ./context_config.yml
trtllm-serve $MODEL --host localhost --port 8003 --backend pytorch --config ./gen_config.yml
trtllm-serve disaggregated -c disagg_config.yaml
```

Gotcha the doc calls out, worth putting in a preflight check: "`backend` … has no default
— if it is left unset, the worker still starts, but it brings up no cache transceiver and
rejects the disaggregated requests it is then routed. Set the same value on the context
and the generation worker." Verify with
`grep "Using KvCacheTransceiverV2" log_ctx_0 log_gen_0`.

---

## 8. Worked example: routing design for this repo's five models on 8×B300

### 8.1 Inputs

From [`METHODOLOGY.md` §8](../METHODOLOGY.md#8-pinned-inputs-converged-values-from-the-fact-checked-docs),
[`matrix/fit-matrix.md`](../matrix/fit-matrix.md) and
[`matrix/recommendations.md`](../matrix/recommendations.md): 268 GB/GPU, 2,144 GB per
8-GPU node. Minimum GPUs on B300: DeepSeek-V4.1-Flash **2**, DeepSeek-V4.1-Flash-NVFP4
**4**, Qwen3.8-27B **1**, Kimi-K3 **8**, Marlin-2B **1**.

### 8.2 The routing decision per model

| Model | Replica shape on one 8×B300 node | Endpoints per node | Routing verdict | Why |
|---|---|---|---|---|
| DeepSeek-V4.1-Flash | 2 × (TP4/EP4) aggregated | 2 | **KV-aware / prefix-precise routing** | Agentic + long shared prefixes; 890 B/token KV on sm_103 makes a hit enormously cheaper than a re-prefill |
| DeepSeek-V4.1-Flash-NVFP4 | 1 × TP4 or 2 × TP4 | 1–2 | **Same as base**, same pool policy, separate pool | Identical KV layout and prefix semantics ([`METHODOLOGY.md` §8](../METHODOLOGY.md#models)) |
| Qwen3.8-27B | 8 × single-GPU | 8 | **Token-load / power-of-two + session affinity**; prefix affinity only after measurement | Only 16 of 64 layers hold KV; the GDN state is per-sequence and not prefix-reusable |
| Kimi-K3 | 1 × TP8 (whole node) | 1 per node | **Least-loaded across nodes + hard session affinity**; KV-aware only once ≥ 2 nodes and HiCache is on | NVIDIA's own GB300 recipe ships `least-loaded`; there is nothing to route *within* a node |
| Marlin-2B | 8 × single-GPU | 8 | **Plain least-outstanding-requests / power-of-two** | Video prompts are unique; prefix caching has nothing to hit |

### 8.3 DeepSeek-V4.1-Flash — the prefix-routing case, in detail

Two TP4/EP4 workers per node is NVIDIA's own shape (§7.1) and it is the shape that makes
routing matter: with `replicas: 2` per node and N nodes you have 2N routable endpoints, a
510 GB checkpoint whose prefill is expensive, and agentic traffic with long shared
prefixes. Set `DYN_ROUTER_MODE=kv` (as the recipe does) or run llm-d precise prefix
routing against vLLM with `--kv-events-config`.

Tuning, with the decision rule rather than a number:

- Start at `--router-kv-overlap-score-credit 1.0` (the default) and move it **up** only if
  your workload is prefill-heavy (long prompts, short outputs) and TTFT is your SLO; move
  it **down** if TPOT is your SLO. This is the same TTFT↔ITL trade §3.2 measured at
  +113 % throughput / +22 % ITL.
- Set `--router-kv-overlap-score-credit-decay` to a small positive value (`0.5`–`1.0`) the
  moment you have more than two endpoints, or a cache-rich worker will win every time and
  a freshly scaled-up worker will never warm. At decay `1`, device credit halves at one
  request-equivalent of excess prefill load.
- `--router-host-cache-hit-weight 0.75` / `--router-disk-cache-hit-weight 0.25` are already
  the right shape for this node: local NVMe is the L3 tier, and a disk hit is genuinely
  worth ~⅓ of a device hit.
- Do **not** enable `--router-track-output-blocks` unless outputs are long and variable;
  for 512–2K-token outputs (S1/S3 in
  [`METHODOLOGY.md` §6](../METHODOLOGY.md#6-cost)) it adds accounting for little.

**Do not disaggregate this model on a small fleet.** Three independent reasons, all
sourced: (1) "Disaggregated SGLang does not support DSpark for this model" — and DSpark
γ=5 is worth 3.13× output per byte and is pinned as the default in
[`matrix/recommendations.md`](../matrix/recommendations.md); (2) "KV-aware routing is a
no-op on the disaggregated target with a single decode worker"; (3)
[`cross-cutting/serving-optimizations.md` §3.5](../cross-cutting/serving-optimizations.md)'s
finding that below ~1,000 GPUs the disaggregation arithmetic rarely closes, with a
measured 20–30 % regression on small or untuned workloads. Revisit at ≥ 8 nodes.

If you do want PD later, the verified layout is in that same section (1P1D on GB200 NVL4,
TP4 per role, DSpark in *both* pools so transferred KV stays compatible), and the routing
config to copy is llm-d's two-profile file in §3.5.

### 8.4 Kimi-K3 — where prefix routing has nothing to do

Kimi-K3 at 1,561 GB needs the whole node (195 GB/GPU at TP8). **One replica per node means
there is no intra-node routing decision at all** — the router's only job is picking a
node. NVIDIA's own GB300 agentic recipe sets exactly that:

```yaml
# recipes/kimi-k3/sglang/agg-gb300-agentic/deploy.yaml
env:
  - {name: DYN_ROUTER_MODE, value: least-loaded}     # not kv
args: ["--router-min-initial-workers", "3", ...]
# worker.yaml: tp-size: 8, dcp-size: 8, context-length: 1048576,
#   max-running-requests: 32, mem-fraction-static: 0.92,
#   speculative-algorithm: DSPARK, speculative-draft-model-path: RadixArk/Kimi-K3-DSpark,
#   speculative-dspark-block-size: 7, enable-hierarchical-cache: true, hicache-size: 100,
#   kv-cache-dtype: fp8_e4m3
```
[src](https://github.com/ai-dynamo/dynamo/blob/main/recipes/kimi-k3/sglang/agg-gb300-agentic/deploy.yaml)

Read `max-running-requests: 32` against
[`matrix/fit-matrix.md`](../matrix/fit-matrix.md)'s concurrency numbers: **the pool is
tiny**, so admission control matters far more than placement. Design consequently:

1. **Hard session affinity.** `--router-session-affinity-ttl-secs` with
   `--router-session-affinity-mode hard`, keyed on `X-Dynamo-Session-ID`. A 1M-token
   agentic session that lands on a different node is a full re-prefill of up to 1M tokens;
   at 2.25 GB of KDA state per request plus 13.5 KiB/token MLA
   ([`METHODOLOGY.md` §8](../METHODOLOGY.md#models)) it is also **13.5 GiB = 14.50 GB** of
   KV you cannot afford to rebuild (`1,048,576 × 13.5 KiB = 14,495,514,624 B`; recomputed
   2026-09-19 — earlier drafts printed "13.5 GB", mixing the GiB figure with a GB label,
   which [`METHODOLOGY.md` §1](../METHODOLOGY.md#1-weight-memory) forbids). Also hash the raw session header at the ingress so the same frontend
   handles the session (Dynamo's own guidance, §3.9).
2. **Queue, don't reject.** `--router-queue-threshold` with `--router-queue-policy fcfs`
   for tail TTFT. With 32 concurrent slots, the arrival process will exceed capacity
   routinely and FCFS queueing is better than 529s.
3. **KV-aware routing becomes worthwhile only at ≥ 2 nodes and with HiCache on** — the
   recipe's `enable-hierarchical-cache: true` / `hicache-size: 100` gives the router a
   host tier to credit (`--router-host-cache-hit-weight`, default 0.75). Below that,
   `least-loaded` is correct and simpler.

Two caveats carried from elsewhere in this tree: the B300 HGX node is a *single* NVLink
domain, so `dcp-size: 8` is intra-node; and multi-node Kimi-K3 PD is "unforgiving" because
the recurrent KDA state, paged full-attention KV and block tables all have to arrive
([`cross-cutting/serving-optimizations.md` §3.5](../cross-cutting/serving-optimizations.md)).

### 8.5 Qwen3.8-27B — eight single-GPU replicas, and why prefix affinity is not obvious

Eight replicas per node, 1 GPU each, BF16 55.56 GB or FP8 30.87 GB — comfortably inside
268 GB. This is the classic prefix-routing shape *except* that the model is a hybrid: 48
of 64 layers are linear-attention (GDN) and only 16 hold a KV cache, with a per-sequence
GDN state of 392.2 MB (S=5 × 78.4 MB bf16 under SGLang's default)
([`METHODOLOGY.md` §8](../METHODOLOGY.md#models)).

Consequences for routing:

- The reusable-across-requests part of the state is only the 16 full-attention layers' KV.
  Recurrent state is per-sequence and not prefix-shareable in the usual sense — see
  [`cross-cutting/serving-optimizations.md` §1.3](../cross-cutting/serving-optimizations.md)
  ("Caching when the state is not append-only").
- SGLang's `--mamba-radix-cache-strategy extra_buffer` is what costs S=5 slots;
  `--disable-radix-cache` drops it to S=1, i.e. **turning prefix caching off buys you ~5×
  the per-request state budget**. That is a routing-relevant trade, not just an engine one:
  if you disable radix caching for concurrency, prefix-affinity routing has nothing left to
  exploit and you should run `power_of_two`.
- **Default recommendation:** `power_of_two` or a token-load scorer, plus session affinity
  for multi-turn chat. Add prefix affinity only after an A/B on your own traffic.
  ⚠️ **TO BE VERIFIED** — no published measurement of cross-replica prefix-affinity routing
  on a hybrid linear-attention model was found on 2026-09-19; the reasoning above is
  derived from the architecture (16/64 cacheable layers) and this tree's own state
  accounting, not from a benchmark.

Also note that with eight replicas per node, the **router** is now a real cost centre: at
8 replicas × 50 ms metric ticks (llm-d `--refresh-metrics-interval` default) the EPP is
doing 160 scrapes/s per node before it does any routing.

### 8.6 Marlin-2B — the boring one, deliberately

2.21 B params, 5.444 GB BF16, one GPU, video in (2 fps, ≤ 240 frames, 200,704 px/frame).
Requests are dominated by encode; prompts are unique. Put it behind a plain Kubernetes
Service with least-outstanding-requests, or the SGLang gateway with
`--policy power_of_two`. No EPP, no KV events, no tokenize hop — every one of those would
add latency to buy a cache hit that does not exist.

Two things that *would* change the verdict, both currently experimental:

- **E/PD disaggregation.** llm-d supports E/PD and E/P/D via the `disagg-profile-handler`,
  but "Encode disaggregation is an experimental feature. When enabled, the router
  identifies all pods capable of encoding, and the vLLM sidecar distributes multimedia
  requests to randomly selected pods from that subset. More sophisticated selection
  strategies are planned"
  [src](https://raw.githubusercontent.com/llm-d/llm-d-router/main/docs/disaggregation.md).
  Random encode placement is not worth a new pool on a 1-GPU model.
- **Repeated media.** If the same videos are re-queried (evaluation harnesses, retry
  loops), llm-d's `mm-embeddings-cache-producer` plus Dynamo's multimodal E/P/D embedding
  cache ("30 % faster TTFT on image workloads", vendor claim) become relevant. The
  multimodal predicted-latency benchmark in §3.7 shows what byte-identical media reuse is
  worth on a *larger* VLM: +30 % throughput and 29× lower median TTFT vs round-robin — but
  that workload was constructed with 600 prefix groups of 5 identical prompts, which is not
  representative of ad-hoc video captioning.

Raise `DYN_HTTP_BODY_LIMIT_MB` (default 192) if clients inline video, or require
`https://` URLs so only a reference crosses the request plane.

### 8.7 Putting it together — one gateway, five pools

```
                       Agent Router (Gateway + AIGatewayRoute)
                       ├─ auth / API keys / tenant identity
                       ├─ llmRequestCosts → BackendTrafficPolicy token quotas
                       └─ x-ai-eg-model  ─┬─────────────────────────────────────┐
                                          │                                      │
   InferencePool ds-v41-flash  ◄──────────┤  EPP: precise prefix + token load    │
     2 × TP4/EP4 per node, DSpark         │  (Dynamo kv router is the alternative)
   InferencePool ds-v41-flash-nvfp4 ◄─────┤  same policy, separate pool          │
   InferencePool qwen38-27b     ◄─────────┤  EPP: token-load + session affinity  │
     8 × 1 GPU                            │                                      │
   InferencePool kimi-k3        ◄─────────┤  least-loaded + HARD session affinity│
     1 × TP8 per node                     │  + FCFS queue (32 slots/node)        │
   InferencePool marlin-2b      ◄─────────┘  plain LOR / power-of-two, no EPP    │
     8 × 1 GPU
```

Five pools because InferencePool is one-base-model (§5.1); one L1 gateway because token
quotas and API keys are global; four different L2 policies because the models are four
different shapes. If you would rather not run four EPP configurations, the honest
fallback is: run precise prefix routing for the two DeepSeek pools only (where §3.2 says
the gain is 2×) and `power_of_two` everywhere else.

### 8.8 Benchmarks this design needs before it is trusted

Ordered by how much a wrong assumption costs, and all runnable with
[`llm-d-benchmark`](https://github.com/llm-d/llm-d-benchmark) /
[`inference-perf`](https://github.com/kubernetes-sigs/inference-perf):

| id | Question | Method |
|---|---|---|
| **R1** | What is `peakPrefillThroughput` for DeepSeek-V4.1-Flash on 8×B300, TP4? | `guides/recipes/router/calibration/calibrate.sh`. Every affinity gate in §7.2 is wrong without it. Relates to open question 5 in [`README.md`](../README.md) — prefill rates on this model class are the weakest input in the tree. |
| **R2** | Prefix routing vs `power_of_two` (not vs round-robin) on real agentic traffic | Same ladder as §3.2, three arms. Closes the gap flagged in §3.2 item 3. |
| **R3** | Does a client disconnect actually abort the engine request? | Drive a stream, kill the client at token 10, watch `vllm:request_success_total{finished_reason="abort"}` and GPU utilization. Closes the §6.2 ⚠️. |
| **R4** | Does prefix affinity help Qwen3.8-27B at all, and at what `S` (radix on/off)? | A/B `cache_aware` vs `power_of_two` × `--disable-radix-cache` on/off. Closes the §8.5 ⚠️. |
| **R5** | Kimi-K3 session-affinity hit rate and re-prefill cost at 1M context | Replay an agentic session trace through `hard` vs no affinity; measure TTFT p99 and node-hours. |
| **R6** | EPP CPU and added TTFT at our replica count | Measure `dynamo_router_overhead_*` / EPP latency at 8 and 16 endpoints; size per the [EPP Container Sizing Guide](https://github.com/llm-d/llm-d-router/blob/main/docs/operations.md). |

---

## Open questions

All ⚠️ items from above, consolidated.

1. **⚠️ Method limitation — WebSearch budget.** This document was researched with 8
   WebSearch queries, not the ≥ 15 the brief asked for: the session-wide WebSearch budget
   (200 calls, shared across the parallel research program) was exhausted mid-pass. The
   remainder of the research was done with WebFetch and `curl` directly against primary
   sources (GitHub raw files, the GitHub releases API, PyPI, vendor docs), which is the
   brief's stated preference; but discovery of sources I did not already know to look for
   is weaker than intended. Specifically not covered for lack of search: Kong and Traefik
   AI plugins (§4.4), Cloudflare / Character.AI / Modal / Together / Fireworks /
   Perplexity engineering blogs on routing, and MLPerf/InferenceMAX router-layer rows.
2. **⚠️ No published prefix-affinity vs well-tuned-load-router comparison.** Every
   measured number in §3 compares against round-robin or random. The delta over a
   competent `power_of_two`/token-load baseline — which is what a team would actually
   deploy as step one — is unpublished. This inflates the apparent value of the whole
   affinity-routing layer by an unknown amount. Benchmark R2.
3. **⚠️ Client-disconnect → engine-abort mapping is undocumented.** vLLM exposes
   `finished_reason="abort"` but the docs "do not explicitly explain the relationship
   between client disconnections and the 'abort' finish reason"
   [src](https://docs.vllm.ai/en/latest/design/metrics/). SGLang and TRT-LLM publish
   nothing equivalent. On agentic traffic with high abandonment this is directly wasted
   GPU time. Benchmark R3.
4. **⚠️ Ray Serve `PrefixCacheAffinityRouter` parameter names and defaults.** The values
   `imbalanced_threshold=10` and `match_rate_threshold=0.1` are widely repeated but do not
   appear on the routing-policies page fetched 2026-09-19; that page gives only the ≥10 %
   / <10 % match-rate behaviour. Verify against the Ray 2.58 API reference before relying
   on them.
5. **⚠️ KServe `LLMInferenceService` full spec.** The overview page publishes only a
   minimal example; `spec.parallelism`, `spec.prefill`, `spec.worker` and the scheduler
   sub-spec are referenced but the configuration guide URL 404s. Also unresolved: the
   overview page says "Version: 0.20" while the LLMInferenceService GA blog is v0.17
   (2026-03-13) and the latest tag is v0.20.0 (2026-08-06) — the doc site's version label
   and the release stream should be reconciled before writing manifests against it.
6. **⚠️ Triton's supported position for LLM serving.** No primary source found stating
   whether Triton 2.72.0 is recommended, maintained-but-superseded, or deprecated for LLM
   workloads relative to Dynamo. Every recipe for the five models in this repo is on
   Dynamo or the engines directly.
7. **⚠️ Kong / Traefik AI-gateway capabilities.** Not fetched (see item 1). The
   ext_proc/Gateway-API statement in §4.4 is safe; specific token-rate-limiting or
   InferencePool claims for those two products are not made here.
8. **⚠️ vLLM multi-LoRA ITL overhead on 0.29.** No published measurement found, so the
   shared-pool-vs-pool-per-model break-even in §5.3 has no numeric threshold.
9. **⚠️ Prefix-affinity routing on hybrid linear-attention models (Qwen3.8-27B, Kimi-K3).**
   No benchmark exists for cross-replica prefix routing where most layers hold recurrent
   state rather than KV. §8.5's recommendation is derived from the 16/64 cacheable-layer
   split and this tree's state accounting, not measured. Benchmark R4.
10. **⚠️ llm-d chunked decode has no published numbers.** `--decode-chunk-size` is
    documented with claimed benefits (average TTFT, head-of-line blocking) and no
    measurements as of 2026-09-19.
11. **⚠️ Dynamo's DeepSeek-V4.1-Flash and Kimi-K3 recipes are GB200/GB300, not B300 HGX.**
    Both are day-0 snapshot builds explicitly "not recommended for production" and "not
    benchmarked", and the GB200 recipes "require ARM-node scheduling". The TP4/EP4 × 2
    shape and the router-mode choices in §8 are read across from them to 8×B300 HGX
    (x86, single NVLink domain, 268 GB/GPU) and have not been validated on that hardware.
12. **⚠️ EPP scaling at our endpoint counts.** llm-d pins the EPP to a single replica
    whenever `token-load-scorer` is in the profile, because in-flight token accounting is
    per-process. With five pools and up to 8 endpoints each, EPP CPU and its contribution
    to TTFT are unmeasured here. Benchmark R6.
13. **⚠️ Structured output vs request migration are mutually exclusive.** Dynamo's
    migration explicitly corrupts guided decoding, and every recipe in §7 enables
    `--dyn-enable-structural-tag`. Whether a pool should trade migration for structured
    output is a product decision this document cannot make, and no source quantifies the
    failure rate migration would have saved.
14. **⚠️ Token-quota enforcement lags by one request.** Because token cost is known only
    after the response, a `BackendTrafficPolicy` token bucket can be overshot by a single
    request of arbitrary size. No source describes a pre-admission token estimate in Agent
    Router v1.1.0; "Token counting" in that release may address this — unverified.

---

## Sources

Primary sources fetched on 2026-09-19. Release dates come from the GitHub Releases API
(`published_at`) and PyPI (`upload_time`) unless otherwise noted.

**Kubernetes / gateway layer**
- Gateway API Inference Extension — [README](https://raw.githubusercontent.com/kubernetes-sigs/gateway-api-inference-extension/main/README.md), [InferencePool API](https://gateway-api-inference-extension.sigs.k8s.io/api-types/inferencepool/), [introduction](https://gateway-api-inference-extension.sigs.k8s.io/), [releases](https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases)
- Agent Router (ex-Envoy AI Gateway) — [docs home](https://theagentrouter.ai/docs/), [release notes](https://theagentrouter.ai/release-notes/), [AIGatewayRoute + InferencePool](https://theagentrouter.ai/docs/next/capabilities/inference/aigatewayroute-inferencepool/), [usage-based rate limiting](https://theagentrouter.ai/docs/0.1/capabilities/usage-based-ratelimiting/)
- Envoy External Processing — [ext_proc filter](https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/ext_proc_filter)
- NGINX Gateway Fabric — [Gateway API Inference Extension](https://docs.nginx.com/nginx-gateway-fabric/how-to/gateway-api-inference-extension/); Istio — [task](https://istio.io/latest/docs/tasks/traffic-management/ingress/gateway-api-inference-extension/)

**llm-d**
- [README](https://raw.githubusercontent.com/llm-d/llm-d/main/README.md) · [llm-d Router README](https://raw.githubusercontent.com/llm-d/llm-d-router/main/README.md) · [Router architecture](https://raw.githubusercontent.com/llm-d/llm-d-router/main/docs/architecture.md) · [Disaggregation design](https://raw.githubusercontent.com/llm-d/llm-d-router/main/docs/disaggregation.md)
- [Precise prefix-cache routing guide](https://raw.githubusercontent.com/llm-d/llm-d/main/guides/precise-prefix-cache-routing/README.md) · [its router values](https://raw.githubusercontent.com/llm-d/llm-d/main/guides/precise-prefix-cache-routing/router/precise-prefix-cache-routing.values.yaml) · [vLLM 16×H100 results](https://raw.githubusercontent.com/llm-d/llm-d/main/guides/precise-prefix-cache-routing/benchmark-results/vllm-qwen3-32b-h100.md) · [SGLang 16×H100 results](https://raw.githubusercontent.com/llm-d/llm-d/main/guides/precise-prefix-cache-routing/benchmark-results/sglang-qwen3-32b-h100.md)
- [PD disaggregation router values](https://raw.githubusercontent.com/llm-d/llm-d/main/guides/pd-disaggregation/router/pd-disaggregation.values.yaml) · [Predicted latency-based routing guide](https://raw.githubusercontent.com/llm-d/llm-d/main/guides/predicted-latency-routing/README.md) · [Multi-model routing guide](https://raw.githubusercontent.com/llm-d/llm-d/main/guides/multi-model-routing/README.md) · [its HTTPRoutes](https://raw.githubusercontent.com/llm-d/llm-d/main/guides/multi-model-routing/manifests/httproutes.yaml) · [env.sh version pins](https://raw.githubusercontent.com/llm-d/llm-d/main/guides/env.sh)

**NVIDIA Dynamo**
- [README](https://raw.githubusercontent.com/ai-dynamo/dynamo/main/README.md) · [Frontend Configuration Reference](https://raw.githubusercontent.com/ai-dynamo/dynamo/main/docs/fern/pages/reference/components/frontend-configuration.mdx) · [Routing Concepts (cost model)](https://raw.githubusercontent.com/ai-dynamo/dynamo/main/docs/fern/pages/developer-guide/knowledge-base/modular-components/router/routing-concepts.md) · [Configuration and Tuning](https://raw.githubusercontent.com/ai-dynamo/dynamo/main/docs/fern/pages/developer-guide/knowledge-base/modular-components/router/configuration-and-tuning.md) · [Request migration](https://docs.nvidia.com/dynamo/user-guides/fault-tolerance/request-migration)
- Recipes: [DeepSeek-V4.1-Flash agg GB200](https://github.com/ai-dynamo/dynamo/blob/release/1.6.0-deepseek-v4.1-flash-dev.1/recipes/deepseek-v4.1-flash/sglang/agg-gb200/deploy.yaml) · [its release notes](https://github.com/ai-dynamo/dynamo/releases/tag/v1.6.0-deepseek-v4.1-flash-dev.1) · [Kimi-K3 agg GB300 agentic](https://github.com/ai-dynamo/dynamo/blob/main/recipes/kimi-k3/sglang/agg-gb300-agentic/deploy.yaml)

**Engines**
- vLLM — [data-parallel deployment](https://docs.vllm.ai/en/latest/serving/data_parallel_deployment/) · [OpenAI-compatible server](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/serving/online_serving/openai_compatible_server.md) · [LoRA adapters](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/lora.md) · [engine args](https://docs.vllm.ai/en/latest/configuration/engine_args/) · [metrics design](https://docs.vllm.ai/en/latest/design/metrics/) · [api_server deprecation shim](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/entrypoints/openai/api_server.py)
- SGLang — [Model Gateway](https://docs.sglang.io/advanced_features/sgl_model_gateway.html) · [PD disaggregation](https://raw.githubusercontent.com/sgl-project/sglang/main/docs/docs/advanced_features/pd_disaggregation.mdx) · [llm-d integration](https://raw.githubusercontent.com/sgl-project/sglang/main/docs/docs/advanced_features/llm-d.mdx)
- TensorRT-LLM — [Disaggregated Serving](https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/docs/source/features/disagg-serving.md)
- Triton — [repository](https://github.com/triton-inference-server/server)

**Platforms**
- KServe — [v0.17 release blog](https://kserve.github.io/website/blog/kserve-0.17-release) · [LLMInferenceService overview](https://kserve.github.io/website/docs/model-serving/generative-inference/llmisvc/llmisvc-overview) · [releases](https://github.com/kserve/kserve/releases)
- AIBrix — [README](https://raw.githubusercontent.com/vllm-project/aibrix/main/README.md) · [gateway plugins](https://aibrix.readthedocs.io/latest/features/gateway-plugins.html) · [v0.7.0 blog](https://aibrix.github.io/posts/2026-06-16-v0.7.0-release/)
- Ray Serve LLM — [routing policies](https://docs.ray.io/en/latest/serve/llm/architecture/routing-policies.html) · [PrefixCacheAffinityRouter](https://docs.ray.io/en/latest/serve/llm/prefix-aware-request-router.html)

**Measurements and papers**
- Baseten — [2× faster inference with NVIDIA Dynamo](https://www.baseten.co/blog/how-baseten-achieved-2x-faster-inference-with-nvidia-dynamo/) (2026-03-16)
- CacheWise — [arXiv 2606.16824](https://arxiv.org/abs/2606.16824) (2026-06-15)
- Accuracy Is Speed / LAAR — [arXiv 2604.15732](https://arxiv.org/abs/2604.15732) (2026-04-17)

**Version data**
- [vLLM releases](https://github.com/vllm-project/vllm/releases) · [SGLang releases](https://github.com/sgl-project/sglang/releases) · [TensorRT-LLM releases](https://github.com/NVIDIA/TensorRT-LLM/releases) · [Dynamo releases](https://github.com/ai-dynamo/dynamo/releases) · [`ai-dynamo` on PyPI](https://pypi.org/pypi/ai-dynamo/json) · [llm-d-router releases](https://github.com/llm-d/llm-d-router/releases) · [AIBrix releases](https://github.com/vllm-project/aibrix/releases) · [Ray releases](https://github.com/ray-project/ray/releases) · [`sglang-router` on PyPI](https://pypi.org/pypi/sglang-router/json)

**This repo**
- [`METHODOLOGY.md`](../METHODOLOGY.md) · [`README.md`](../README.md) · [`cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md) · [`cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md) · [`matrix/fit-matrix.md`](../matrix/fit-matrix.md) · [`matrix/cost-matrix.md`](../matrix/cost-matrix.md) · [`matrix/recommendations.md`](../matrix/recommendations.md)

---

## Verification log (2026-09-19)

Adversarial re-check of the 25 most consequential claims in this document. Every primary
source below was **opened**, not taken from this document's own citation; every repo
cross-reference was **opened and the number located in the target file**; every derivation
was recomputed with `python3`. Verdicts: **CONFIRMED** = the source says what this document
says; **CORRECTED** = it did not, and the text above has been changed; **UNVERIFIABLE** =
no primary source settles it, and a ⚠️ now marks it in place.

### A. Engine / component versions and dates (§2.1)

| # | Claim | Verdict | Source opened |
|---|---|---|---|
| 1 | vLLM **0.29.0**, 2026-09-09 | **CONFIRMED** (`published_at 2026-09-09T08:54:49Z`) | https://github.com/vllm-project/vllm/releases |
| 2 | "vLLM shipped **six minor releases** between 2026-07-11 and 2026-09-09" | **CORRECTED** → **four** minor (0.26.0, 0.27.0, 0.28.0, 0.29.0) + two patches (0.25.1 2026-07-14, 0.27.1 2026-08-11); seven tags inclusive of the 0.25.0 start point. The "six" counted `inference-engines.md` §2.1's six *table rows*, which include 0.25.0 and the 0.27.1 patch | https://github.com/vllm-project/vllm/releases |
| 3 | SGLang **v0.5.20**, 2026-09-18 | **CONFIRMED** (`2026-09-18T22:41:33Z`) | https://github.com/sgl-project/sglang/releases |
| 4 | TensorRT-LLM **v1.3.0rc27**, 2026-09-18, 1.2.1 last stable | **CONFIRMED** (`2026-09-18T03:14:53Z`, `prerelease=true`). Cross-doc conflict recorded inline: `inference-engines.md` dates it 2026-09-17 from PyPI `upload_time`. Both right for their own source | https://github.com/NVIDIA/TensorRT-LLM/releases |
| 5 | Dynamo: **v1.4.2** last numbered stable 2026-08-29; PyPI `ai-dynamo` **1.5.0** stable + **`1.6.0.dev20260919`** | **CORRECTED** on the dev build. v1.4.2 `2026-08-29T01:29:24Z` ✓; PyPI 1.5.0 uploaded `2026-09-19 04:21:13` ✓; but PyPI carries **no `1.6.0.dev*` at all** — the newest dev is `1.5.0.dev20260914`. Row rewritten | https://github.com/ai-dynamo/dynamo/releases · https://pypi.org/pypi/ai-dynamo/json |
| 6 | Dynamo model-snapshot tags dated 2026-09-12 / 2026-09-09 | **CONFIRMED** (`v1.6.0-deepseek-v4.1-flash-dev.1` `2026-09-12T02:33:02Z`; `v1.5.0-kimi-k3-dev.1` `2026-09-09T20:55:53Z`) | https://github.com/ai-dynamo/dynamo/releases |
| 7 | llm-d latest = **v0.7**, 2026-05 | **CORRECTED** → **v0.9.0, 2026-08-17** (v0.8.0 2026-06-24, v0.8.1 2026-06-26). The repo README this document cited still prints "Version 0.7 (May 2026)" and is **stale** — a citation that was trusted rather than cross-checked. v0.7.0's own date (`2026-05-12T19:15:56Z`) is right, so §3.7's "llm-d v0.7 (2026-05) graduated predicted-latency to GA" survives unchanged | https://github.com/llm-d/llm-d/releases |
| 8 | llm-d Router **v0.10.0** 2026-08-17, v0.11.0-rc.1 2026-09-18 | **CONFIRMED** (`2026-08-17T21:00:49Z`, `2026-09-18T20:52:06Z`) | https://github.com/llm-d/llm-d-router/releases |
| 9 | GIE **v1.6.2** 2026-09-17; v1.6.0 2026-08-17; `endpointPickerRef` optional **as of v1.5.0 (2026-04-19)** | **CONFIRMED** on all three (`v1.6.2 2026-09-17T20:18:39Z`, `v1.6.0 2026-08-17T17:39:38Z`, `v1.5.0 2026-04-19T18:13:38Z`); the API page states verbatim "Until release `v1.5.0`, the InferencePool field `endpointPickerRef` was required. Currently, it is optional." | https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases · https://gateway-api-inference-extension.sigs.k8s.io/api-types/inferencepool/ |
| 10 | KServe **v0.20.0** 2026-08-06 (v0.21.0-rc0 2026-09-10); AIBrix **v0.7.0** 2026-06-18; Ray **ray-2.58.0** 2026-08-23; `sglang-router` **0.3.2** 2026-01-15 | **CONFIRMED**, all four (`2026-08-06T15:07:42Z`, `2026-09-10T17:44:50Z`, `2026-06-18T00:23:05Z`, `2026-08-23T05:42:08Z`; PyPI `upload_time_iso_8601 2026-01-15T19:55:00.149982Z`) | the four releases APIs · https://pypi.org/pypi/sglang-router/0.3.2/json |
| 11 | Agent Router v1.1.0 **2026-08-21**, v1.0.0 **2026-06-23**, v0.7.0 **2026-06-04** ("quota-aware rate limiting"), v0.3.0 **2025-08-21** (EPP provider), failover since **v0.2.0 (2025-06-05)** | **CONFIRMED**, all five dates and all five feature strings | https://theagentrouter.ai/release-notes/ |

### B. Feature-support statements, flags and config fields

| # | Claim | Verdict | Source opened |
|---|---|---|---|
| 12 | Dynamo frontend flag defaults (§3.4, §6.1, §6.5): `--router-mode round-robin` with all seven values, overlap credit `1.0`, decay `0.0`, `prefill-load-scale 1.0`, host `0.75`, disk `0.25`, decode-active-request-weight `0.0`, temperature `0.0`, ttl `120.0`, predicted-ttl `null`, track-active `true`, track-output `false`, session-affinity-ttl `null`, queue-threshold `null`, queue-policy `fcfs`, replica-sync `false`, `DYN_HTTP_SSE_KEEP_ALIVE_INTERVAL_MS 0`, `DYN_HTTP_BODY_LIMIT_MB 192`, `DYN_HTTP_OVERLOAD_STATUS_CODE 529`, `DYN_HTTP_GRACEFUL_SHUTDOWN_TIMEOUT_SECS 5` | **CONFIRMED — 20 of 21 exact.** The 21st, `--router-session-affinity-mode` default `hard`, did **not** surface on re-fetch → now marked ⚠️ in the table | https://raw.githubusercontent.com/ai-dynamo/dynamo/main/docs/fern/pages/reference/components/frontend-configuration.mdx |
| 13 | Dynamo cost model (§3.4): the five-line formula, the conditional-disagg exception, the overlap-credit/clamp sentences, "The router selects the lowest-cost eligible worker" | **CONFIRMED** verbatim, all of it | https://raw.githubusercontent.com/ai-dynamo/dynamo/main/docs/fern/pages/developer-guide/knowledge-base/modular-components/router/routing-concepts.md |
| 14 | Dynamo tuning (§3.4): overlap credit is "the primary knob"; decay `1/(1 + decay × normalized_excess)` halving credit at one request-equivalent; `wspt` key `(1 + priority_jump)/scheduling_cost_tokens` with `scheduling_cost_tokens = max(1, raw_isl_tokens − cached_tokens)`; `fcfs` key `priority_jump − arrival_offset`; the `kv-overlap-score-weight` → `prefill_load_scale` deprecation; the `2.5`-vs-`1.0` worker-advertisement precedence trap | **CONFIRMED**, all six. (These live in *configuration-and-tuning*, not *routing-concepts*, which is how this document already attributes them) | https://raw.githubusercontent.com/ai-dynamo/dynamo/main/docs/fern/pages/developer-guide/knowledge-base/modular-components/router/configuration-and-tuning.md |
| 15 | SGLang Model Gateway (§3.6, §4.3, §6.3): cache-aware defaults `0.3 / 64 / 1.5 / 120 / 67108864`; the five policies and their descriptions; retry `5 / 50 / 5000 / 2.0 / 0.1`; circuit breaker `5 / 2 / 30 / 60`; retryable codes **408, 429, 500, 502, 503, 504**; `429` queue-full / `408` queue-timeout; `--enable-igw` multi-model | **CONFIRMED**, every value and every code | https://docs.sglang.io/advanced_features/sgl_model_gateway.html |
| 16 | vLLM DP deployment (§2.4): three LB modes; the single-node and the two multi-node commands **verbatim including `10.99.48.128` and port `13345`**; the DP-coordinator sentence | **CONFIRMED on the commands, CORRECTED on the quote.** All three command blocks match character-for-character. The coordinator sentence upstream ends "…to determine when all ranks become idle" — the trailing "and can be paused" was this document's own addition inside quotation marks, and has been removed | https://docs.vllm.ai/en/latest/serving/data_parallel_deployment/ |
| 17 | vLLM `--scheduling-policy {fcfs,priority}` default `fcfs`; `--kv-events-config`, `--kv-transfer-config`, `--enable-prefix-caching`, `--block-size` all exist | **CONFIRMED** (help text: "'fcfs' means first come first served … 'priority' means requests are handled based on given priority") | https://docs.vllm.ai/en/latest/configuration/engine_args/ |
| 18 | vLLM 0.29 deprecation (§2.4): the warning names **`vllm server`** and the implementation moved to `vllm.entrypoints.launchers` | **CONFIRMED** verbatim — including that upstream really does say `vllm server`, not `vllm serve`, which reads like a typo but is the shipped string | https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/entrypoints/openai/api_server.py |
| 19 | GIE README (§1.2, §2.2): the EPP definition; the IMPORTANT box moving EPP/InferenceObjective/InferenceModelRewrite/BBR out and keeping **LWEPP + InferencePool API**; no `InferenceModel` CRD in v1 | **CONFIRMED** verbatim; the README's v1 surface contains no `InferenceModel` | https://raw.githubusercontent.com/kubernetes-sigs/gateway-api-inference-extension/main/README.md |
| 20 | llm-d Router architecture (§1.2, §2.8, §5.4, §6.6): "Single `InferencePool` and single `EPP` due to Envoy limitations", one base model per pool; `--refresh-metrics-interval` default **50 ms**; the `flowControl` gate with its three policies + `utilization-detector`; `--decode-chunk-size` default **0**; the random tie-break sentence | **CONFIRMED**, all five | https://raw.githubusercontent.com/llm-d/llm-d-router/main/docs/architecture.md |
| 21 | llm-d precise-prefix mechanism (§1.3, §3.3, §7.2): render latency inside TTFT; GPU overlay **8 CPU / 16 limit**; vLLM **v0.26.0+** ROUTER socket on **5559** with a **10,000-batch** replay buffer; SGLang needs a GPU-less `vllm launch render` pool of **3 replicas**; `--block-size 64` ↔ `blockSizeTokens: 64` ↔ `--page-size=64` | **CONFIRMED**, all five | https://raw.githubusercontent.com/llm-d/llm-d/main/guides/precise-prefix-cache-routing/README.md |
| 22 | TRT-LLM disagg (§2.6, §6.1, §6.2, §7.4): stateful `kv_cache_aware`/`conversation` vs stateless `round_robin`/`load_balancing`; coordinator + `SO_REUSEPORT` fleet; the fleet decision rule; `server_keep_alive_timeout` **10 s**; `kv_transfer_timeout_ms` **60000**; `backend` has no default; the `num_workers: 4` YAML and the implicit coordinator on **port − 1 = 7999** | **CONFIRMED**, all seven, including the YAML block field-for-field | https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/docs/source/features/disagg-serving.md |
| 23 | AIBrix (§2.9): the eight strategy names and descriptions; header/env selection; v0.7.0 blended strategies; Redis-backed HA; TRT-LLM first-class | **CORRECTED in part.** The eight strategies, `routing-strategy` header and `ROUTING_ALGORITHM` env var are confirmed on the gateway-plugins page; the blended form is confirmed on the release post but is spelled **`AIBRIX_ROUTING_ALGORITHM="least-request:2,throughput:1"`**, and the Redis sentence this document printed inside quotation marks is a paraphrase — both now quoted as published | https://aibrix.readthedocs.io/latest/features/gateway-plugins.html · https://aibrix.github.io/posts/2026-06-16-v0.7.0-release/ |
| 24 | KServe (§2.9): `LLMInferenceService` GA'd in v0.17 (2026-03-13) on the llm-d framework; the minimal manifest | **CONFIRMED on the manifest and the blog text; new conflict found on the API version.** The overview page (label "Version: 0.20") publishes `serving.kserve.io/v1alpha1` exactly as printed here; the v0.17 GA blog announces `serving.kserve.io/v1alpha2`. Two first-party pages disagree — now flagged ⚠️ in §2.9 | https://kserve.github.io/website/blog/kserve-0.17-release · https://kserve.github.io/website/docs/model-serving/generative-inference/llmisvc/llmisvc-overview |
| 25 | Ray Serve LLM (§2.9, §3.9): P2C default; the `PrefixCacheAffinityRouter` ≥10 % / <10 % rules; and the existing ⚠️ that `imbalanced_threshold` / `match_rate_threshold` are **not** on the page | **CONFIRMED, ⚠️ upheld.** The page gives the P2C default and the three match-rate rules verbatim and names **neither** parameter — this document's ⚠️ was correct and stays | https://docs.ray.io/en/latest/serve/llm/architecture/routing-policies.html |

### C. Measured numbers (all recomputed with `python3`)

| # | Claim | Verdict | Source opened |
|---|---|---|---|
| 26 | §3.2 vLLM arm: setup (16×H100, 8 pods TP=2, Qwen3-32B, 150 prefix groups, 6,000+1,200 in / 1,000 out, Poisson 3→60, 0 failures) and 6,986→14,892 tok/s, 6.57→14.60 req/s, TTFT p50 54.6→0.19 s, p90 135.5→0.26 s, ITL 46.4→56.8 ms | **CONFIRMED.** Deltas recomputed: **+113.2 %**, **+122.2 %**, **−99.65 %** (printed −99.7 ✓), **−99.81 %** (printed −99.8 ✓), **+22.4 %**. Both quoted sentences ("The two arms track each other until the fleet saturates"; "affinity routing packs more concurrent work onto cache-warm pods…") are verbatim | https://raw.githubusercontent.com/llm-d/llm-d/main/guides/precise-prefix-cache-routing/benchmark-results/vllm-qwen3-32b-h100.md |
| 27 | §3.2 SGLang arm: 6,884→15,532 tok/s (+125.6 %), TTFT p50 81.9→0.15 s, p90 132.9→0.27 s, ITL 37.9→55.1 ms (+45.4 %) | **CONFIRMED**; recomputed **+125.6 %** and **+45.4 %** exactly | https://raw.githubusercontent.com/llm-d/llm-d/main/guides/precise-prefix-cache-routing/benchmark-results/sglang-qwen3-32b-h100.md |
| 28 | §3.7 predicted-latency: all four workload rows (+38/+40 %, TTFT p50 −13 % / p90 −47 %, TPOT p90 ~100 vs ~950 ms; 87K vs 37K tok/s, 1.8 vs 17.4 s, 30/45 vs 48/372 ms, −88 %; 44.3 req/s ≈ 234K tok/s, 320 vs 401 ms, p99 812 ms vs 2.1 s; 9,449 vs 7,272 tok/s, 0.66 vs 19.2 s, 1 vs 27 failures) plus the heterogeneous-pool and cold-start quotes | **CONFIRMED**, every cell. Recomputed: 87/37 = **2.35×** (printed ~2.3 ✓); 9,449/7,272 = **+29.9 %** (printed +30 ✓); 19.2/0.66 = **29.1×** (printed 29× ✓); 401→320 = **−20.2 %** ✓; 794→595 = **−25.1 %** ✓; 2,100→812 = **−61.3 %** (printed −62 %, inside the rounding of a displayed "2.1 s" — left) | https://raw.githubusercontent.com/llm-d/llm-d/main/guides/predicted-latency-routing/README.md |
| 29 | §3.8 Baseten: Qwen3 Coder 480B, 4 replicas, ~50K in / 1K out, 89 % cross-replica hit, **vs random**, TTFT −50 %, TPOT −34 %, P95 −48 %, P99 −49 %, +61 % req/s, +62 % output tok/s, 2026-03-16 | **CONFIRMED**, every figure and the baseline | https://www.baseten.co/blog/how-baseten-achieved-2x-faster-inference-with-nvidia-dynamo/ |
| 30 | §3.8 llm-d README rows: Tesla/Red Hat 3× throughput / 2× TTFT on 4×MI300X; AWS +70 % on p6-b200; Oracle 10–30 %; v0.5 wide-EP 50k tok/s on 16×16 B200 ≈ 3.1k per decode GPU; 13.9× hierarchical KV offload on 4×H100 @250 users; CNCF Sandbox since 2026-03 with the five founders | **CONFIRMED**, all six. `50,000 / 16 = 3,125` ✓ | https://raw.githubusercontent.com/llm-d/llm-d/main/README.md |
| 31 | §2.7 Dynamo vendor claims: 7× throughput/GPU (DeepSeek R1, GB200 NVL72 vs B200, InferenceX); 7× startup (ModelExpress); 2× TTFT (Qwen3-Coder 480B, Baseten); 80 % fewer SLA breaches at 5 % lower TCO; 750× (DeepSeek-R1, GB300 NVL72, InferenceXv2); the three "New in 1.0" items incl. **30 % faster TTFT** multimodal; the single-GPU sentence | **CONFIRMED** verbatim. Provenance note: the 80 %/5 % row's source is an **Alibaba APSARA 2025 conference talk**, not a written benchmark — weaker than the other four, and this document's "vendor claims" framing is right | https://raw.githubusercontent.com/ai-dynamo/dynamo/main/README.md |
| 32 | §3.8 CacheWise (arXiv 2606.16824, 2026-06-15): **2–2.6×** fewer KV evictions, **up to 3.5×** faster session completion, prefix-aware scheduling + reuse-aware eviction in vLLM | **CONFIRMED** — real paper, "CacheWise: Understanding Workloads and Optimizing KVCache Management for Efficiently Serving LLM Coding Agents", Tiwari et al., 2026-06-15; both figures are the abstract's | https://arxiv.org/abs/2606.16824 |
| 33 | §7.1 Dynamo DeepSeek-V4.1-Flash recipe: two TP4/EP4 workers on 8×GB200, DSpark block size 5, 1M context, and the four quoted limitations ("Day-0 functional scope only; no published performance claim", "neither is benchmarked", "Disaggregated SGLang does not support DSpark for this model…", "KV-aware routing is a no-op on the disaggregated target with a single decode worker") | **CONFIRMED**, all of it, plus the ARM-node-scheduling caveat this document carries in open question 11 | https://github.com/ai-dynamo/dynamo/releases/tag/v1.6.0-deepseek-v4.1-flash-dev.1 |
| 34 | §8.4 Kimi-K3 GB300 recipe: `DYN_ROUTER_MODE: least-loaded`, `--router-min-initial-workers 3`, `tp-size: 8`, `dcp-size: 8`, `context-length: 1048576`, `max-running-requests: 32`, `mem-fraction-static: 0.92`, `speculative-algorithm: DSPARK`, `speculative-draft-model-path: RadixArk/Kimi-K3-DSpark`, `speculative-dspark-block-size: 7`, `enable-hierarchical-cache: true`, `hicache-size: 100`, `kv-cache-dtype: fp8_e4m3` | **CONFIRMED** — all thirteen fields read directly out of the file at `main` | https://github.com/ai-dynamo/dynamo/blob/main/recipes/kimi-k3/sglang/agg-gb300-agentic/deploy.yaml |

### D. Claims about this repo's models and `research/` documents

| # | Claim | Verdict | File opened |
|---|---|---|---|
| 35 | §8.1 "268 GB/GPU, 2,144 GB per 8-GPU node" | **CONFIRMED** — `METHODOLOGY.md` §8 B300 row, exactly | [`METHODOLOGY.md` §8](../METHODOLOGY.md) |
| 36 | §8.1 min GPUs on B300: DS-V4.1-Flash **2**, NVFP4 **4**, Qwen3.8-27B **1**, Kimi-K3 **8**, Marlin-2B **1** | **CONFIRMED** — all five cells located in the §1 fit grid's B300 column (`min 2` / `min 4` / `min 1` / `min 8` / `min 1`) | [`matrix/fit-matrix.md` §1](../matrix/fit-matrix.md) |
| 37 | §8.3 "DSpark γ=5 … is worth **3.13× output per byte** and is pinned as the default" | **CONFIRMED** — the DeepSeek-V4.1-Flash row says "**DSpark γ=5** … worth 3.13× output per byte; removing it takes $/1M from $1.50 → $4.69" | [`matrix/recommendations.md`](../matrix/recommendations.md) |
| 38 | §4.5 fallback break-evens "DeepSeek **$0.60**, Moonshot **$15.00**, Qwen **$3.00** per 1M output tokens" | **CONFIRMED** — §6's vendor-API table, all three output columns, to the cent | [`matrix/cost-matrix.md` §6](../matrix/cost-matrix.md) |
| 39 | §8.3 "below ~1,000 GPUs the disaggregation arithmetic rarely closes, with a measured **20–30 %** regression on small or untuned workloads", cited to §3.5 | **CONFIRMED** — §3.5 is indeed "Prefill/decode disaggregation — when it pays", and prints "a **20–30 % performance drop** from disaggregation on small or untuned" plus "disaggregation is a thousand-GPU problem". §1.2 and §1.3, cited in §3.8 and §8.5, are also the sections this document names | [`cross-cutting/serving-optimizations.md` §1.2/§1.3/§3.5](../cross-cutting/serving-optimizations.md) |
| 40 | §8.5 Qwen3.8-27B: **48 of 64** layers GDN, 16 hold KV; GDN state **392.2 MB** (S=5 × 78.4 MB bf16, SGLang default); BF16 **55.56 GB** / FP8 **30.87 GB**; `--mamba-radix-cache-strategy extra_buffer` ↔ `--disable-radix-cache` → S=1 | **CONFIRMED** against `METHODOLOGY.md` §8's model row *and* its `C7-qwen38-gdn-state-slots` pin log, which pins exactly `S = 5 × 78,446,592 B (bf16) = 392.2 MB per request`; `recommendations.md` independently prints "48 of 64 layers" | [`METHODOLOGY.md` §8](../METHODOLOGY.md) · [`matrix/recommendations.md`](../matrix/recommendations.md) |
| 41 | §8.4 Kimi-K3: **1,561 GB** checkpoint, **195 GB/GPU at TP8**, **13.5 KiB/token** MLA, **2.25 GB** KDA state/request, and "**13.5 GB** of KV" at 1M context | **CORRECTED on the last one.** The first four match `METHODOLOGY.md` §8 exactly (1,560.9 GB; `1,560.9/8 = 195.1` recomputed ✓; 13.5 KiB/token FP8 over 24 of 93 layers; KDA 428.6 MiB × S=5 = 2.25 GB). But `1,048,576 × 13.5 KiB = 14,495,514,624 B` = **13.5 GiB = 14.50 GB** — the document printed the GiB number under a GB label, which `METHODOLOGY.md` §1's units rule forbids. Fixed in place | [`METHODOLOGY.md` §1/§8](../METHODOLOGY.md) |
| 42 | §8.6 Marlin-2B: 2.21 B params, **5.444 GB** BF16, video 2 fps / ≤ 240 frames / 200,704 px per frame | **CONFIRMED** — `METHODOLOGY.md` §8's Marlin-2B row, field for field | [`METHODOLOGY.md` §8](../METHODOLOGY.md) |
| 43 | §8.2/§8.3 DeepSeek-V4.1-Flash: **510 GB** checkpoint, **890 B/token** KV "on sm_103" | **CONFIRMED** — 510.29 GB from the index.json, and `METHODOLOGY.md`'s `C5-890b-fp4-kv-kernel-gate` pin log restricts the 890 B path to `capability.major == 10` → sm_100/sm_103, which is what §8.2's cell says. `510.29/4 = 127.6 GB/GPU` at TP4 ✓ inside 268 GB | [`METHODOLOGY.md` §8 + pin log](../METHODOLOGY.md) |
| 44 | §8.5 "8 replicas × 50 ms metric ticks → **160 scrapes/s** per node" | **CONFIRMED** — `8 / 0.05 = 160.0`, and the 50 ms tick is the llm-d Router default verified at #20 | recomputed with `python3` |

### E. What this pass could not settle

- `--router-session-affinity-mode`'s default (#12) — ⚠️ added in §3.4.
- KServe's `LLMInferenceService` API version (#24) — ⚠️ added in §2.9; two first-party KServe pages disagree, and the existing open question 5 already covers the version-label half of it.
- Triton 2.72.0's supported position for LLM serving (§2.9) and its release date — no primary source; the document's existing ⚠️ stands unchanged.
- Everything in **Open questions 1–14** above was re-read and none of it was contradicted by a source this pass opened; all fourteen stand.
- **Dangling source:** *Accuracy Is Speed / LAAR* (arXiv 2604.15732, Yoshimura et al., 2026-04-17) is listed under Sources but is **not cited anywhere in the body**. The paper is real and on-topic — it proposes accuracy-aware routing scored by time-to-correct-answer, a rung §3.1's ladder does not have. Either cite it in §3.1 or drop it from Sources; flagged, not silently removed.
