# 12b — Optimized per-token model-API providers and custom-silicon providers

**Research date: 2026-09-19.** Part B of the provider study. Part A
(`research/scaling/12a-*`) covers serverless-GPU / container platforms (Modal,
fal, Baseten, Replicate, RunPod, Beam …); this document covers the operators
that sell **tokens** rather than **GPU-seconds**, plus the three companies that
sell tokens off silicon they designed themselves.

Audience: an engineer who already owns (or rents) bare metal and wants to know
which of these companies' engineering is *reusable* and which is a property of
their business model, their fleet size, or their chip.

## 0. How to read this document

**Legend** — same as [`METHODOLOGY.md` §Legend](../METHODOLOGY.md#legend):

| Marker | Meaning |
|---|---|
| `[src](url)` | Traced to a primary document — vendor engineering blog, docs page, pricing page, paper, repo. |
| **⚠️ TO BE VERIFIED** | No primary source found, or sources conflict. The gap is stated inline. |
| **(inferred)** | *Not* published by the provider. Derived here from their docs, pricing, job posts or API behaviour. Always labelled. |
| `meas.` | A number the provider published as a measurement. |

**Three things this document refuses to do.** It does not state internal
architecture a provider has not published; where a field is genuinely unknown
it says so rather than reasoning from a competitor's design. It does not treat
a marketing comparison as a measurement — "4× faster than vLLM" is recorded
with its date, its hardware and its baseline, because all three move. And it
does not re-derive a single cost figure: every self-hosting number is quoted by
name from [`matrix/cost-matrix.md`](../matrix/cost-matrix.md).

**Where the engineering itself is documented.** This file is a study of
*operators*. The techniques they use are documented once, generically, in:

- [`cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md)
  — §1 prefix/KV caching, §2 speculative decoding, §3 MoE parallelism, §4
  batching and SLOs, §5 long context.
- [`cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md)
  — vLLM / SGLang / TRT-LLM support per GPU and per model.
- [`cross-cutting/flash-attention.md`](../cross-cutting/flash-attention.md)
  — FA2/FA3/FA4, FlashMLA, FlashInfer, DSA/CSA kernels.
- [`cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md)
  — NVFP4 vs MXFP4 vs FP8, bytes/param, which GPU executes which natively.
- [`cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) §5.14 —
  the `low`/`high`/`res1y` $/GPU-hour rows every cost figure cites.

Sibling documents in this series — [`01`](01-workload-characterisation.md)
through [`09`](09-cost-and-capacity-planning.md) and
[`12a`](12a-serverless-gpu-platforms.md) — are being written in the same pass;
if a link below is dead, that file has not landed yet. Nothing in this document
depends on them for its arithmetic.

---

## 1. Scope, method, and what the sources actually are

### 1.1 The three business models in this document

| Model | Who | What you buy | What the provider owns |
|---|---|---|---|
| **Per-token API on GPUs** | Together, Fireworks, DeepInfra, Novita, and the ~20 endpoints visible through OpenRouter | $/1M tokens, plus optional dedicated endpoints at $/GPU-hour | An inference engine + a GPU fleet, usually rented |
| **Per-token API on custom silicon** | Groq, Cerebras, SambaNova | $/1M tokens only (Cerebras and Groq also sell reserved/dedicated capacity) | The chip, the compiler, the runtime and the data centre |
| **First-party model operator** | DeepSeek, Moonshot, Mistral, Anthropic, OpenAI, Google | $/1M tokens for *their own* model | Everything, including the model |
| **Router (owns no compute)** | OpenRouter | A single API over everyone else's endpoints | Nothing but the routing layer |

The fourth row matters to a self-hoster for a reason that is not obvious:
OpenRouter's `/api/v1/models/{slug}/endpoints` endpoint is the only place where
**twenty providers' prices, quantization formats, context limits and 30-minute
uptime for the same model** are published side by side and machine-readable. It
is used as a price source throughout §5 — and its per-endpoint `quantization`
field is the closest thing the market has to a truth-in-labelling regime.

### 1.2 Method and its limits

Every profile was built by fetching the provider's own engineering blog, docs,
pricing page and (where it exists) GitHub, then labelling each field as
published / inferred / unknown. Two sources could not be fetched at all and are
marked as gaps rather than filled from memory:

- **Perplexity's blog** (`perplexity.ai/hub/blog/*`) returns HTTP 403 behind a
  Cloudflare interstitial to every user-agent tried. Perplexity's profile is
  therefore built from its **public GitHub repositories only**, which is a
  genuine restriction on what §2.8 can claim. **⚠️ TO BE VERIFIED** — the
  engineering posts exist and are cited across the industry; they could not be
  read on 2026-09-19.
- **Google's Vertex Provisioned Throughput GSU tables** — the overview and
  purchase pages fetched as navigation shells with the numeric tables absent.
  §2.13 records the mechanism without the burndown rates. **⚠️ TO BE VERIFIED**.

Two more limits worth stating up front. **Cold-start numbers are published by
almost nobody in this class** — Together is the sole exception, and its numbers
are for *its own* replicas, not a general law. And **no provider in this
document publishes fleet utilisation**; every statement in the §(i) "what
pricing implies about utilisation" fields is explicitly labelled *(inferred)*
and is an argument from published price against this repo's own cost model, not
a disclosure.

---

## 2. Provider profiles

Each profile carries the same eleven fields (a)–(k). A field reading
"**Not published.**" means exactly that — the search was made and came back
empty.

---

### 2.1 Together AI

The most documented inference operator in this study, by a wide margin. Roughly
a dozen engineering posts across 2024–2026 describe the engine, the speculator
system, the disaggregation design, the autoscaler and the uptime architecture,
with numbers and dates.

**(a) What they sell.** Serverless per-token endpoints; **Dedicated Endpoints**
billed hourly per GPU; **Dedicated Container Inference** (bring-your-own Docker
image) for generative-media models; GPU Clusters (on-demand and reserved); a
Batch API; fine-tuning. Serverless is explicitly positioned as the on-ramp —
*"Most teams start with serverless inference and move to dedicated endpoints at
scale"* [src](https://www.together.ai/pricing).

**(b) Hardware and where it runs.** Owned and operated, not rented from a
hyperscaler — the uptime post is unusually direct: *"one ticket covers the
hardware, network, storage, and software"* rather than renting hyperscaler
capacity [src](https://www.together.ai/blog/99-9-uptime-for-inference)
(2026-07-16). Generations named across posts: A100 SXM4 and H100 SXM5 with 3200
Gbps InfiniBand in the 2023 cluster post
[src](https://www.together.ai/blog/20-exaflops-gpu-clusters) (2023-11-13);
HGX B200 as the serving target from 2025 onward
[src](https://www.together.ai/blog/fastest-inference-for-deepseek-r1-0528-with-nvidia-hgx-b200)
(2025-07-17); GB200 NVL72 clusters at 36k GPUs
[src](https://www.together.ai/blog/nvidia-gb200-together-gpu-cluster-36k).
Dedicated-endpoint GPU menu as published: L40 48GB $1.49/h, L40S 48GB $2.10/h,
A100 PCIe/SXM 80GB $2.40–$2.59/h, H100 80GB $3.36/h, H200 141GB $4.99/h
[src](https://www.together.ai/blog/on-demand-dedicated-endpoints) (2025-03-13);
the current pricing page lists dedicated HGX H100 at $3.99/h (promotional, from
$5.49) and HGX B200 at $8.99/h, with clusters at H100 $3.99 / H200 $5.99 /
B200 $8.19 per GPU-hour and reserved 7–180+ day terms at $3.19–$7.99
[src](https://www.together.ai/pricing).

**(c) Orchestration and isolation.** Multiple deployments behind one stable
endpoint, with canary, blue-green and rolling updates plus automatic rollback
on performance thresholds; A/B testing and shadow traffic mirroring
[src](https://www.together.ai/blog/the-production-platform-for-open-weight-ai-inference)
(2026-07-23). Container inference isolates *"between batch, real-time, and
untrusted traffic"* using multiple independent queues and policy-driven traffic
control rather than a single FIFO
[src](https://www.together.ai/blog/dedicated-container-inference) (2026-02-12).
The underlying container runtime and sandbox technology are **not published**;
the CLI surface (`tg beta endpoints update …`) and replica vocabulary are
Kubernetes-shaped *(inferred)*, but Together never names an orchestrator.

**(d) Cold-start engineering.** The only provider here with published numbers.
Measured on 1×H100 replicas: base 9B model **~86 s to READY**; a custom
fine-tune (18 GB) **~145 s**; scale-up 1→2 replicas **~2.5 min**; restart from
STOPPED **1–2 min**
[src](https://www.together.ai/blog/autoscaling-endpoints-for-llm-inference)
(2026-07-31). At the platform level: deployment times **2–14 minutes** by model
size (small ~2–5 min, frontier MoE ~7–14 min) and **"4× faster warm starts"**
via fleet-wide model-weight caching and proactive pre-warming
[src](https://www.together.ai/blog/the-production-platform-for-open-weight-ai-inference)
(2026-07-23). The four cold-start stages named — GPU placement, weight download,
engine load, warmup — are the same four a self-hoster faces.

**(e) Autoscaling.** Eight inference-native metrics in three families:
concurrency-driven (`inflight_requests`, the default, target 8), SLO-driven
(`ttft`, `e2e_latency`), efficiency-driven (`gpu_utilization`,
`token_utilization`). Proportional control:
`desired = ceil(observed / target × current)`. Asymmetric windows — short
`scale_up_window` (60 s in their example), long `scale_down_window` (default
5 min). **Scale-to-zero does not auto-wake**: `min_replicas: 0` requires
`max_replicas: 0`, i.e. an explicit stop, which they say suits dev/staging and
not production SLOs.

The replay experiment is the most transferable single result in this document.
Same load, three policies, Qwen3.5-9B, 1–3 replicas:

| Policy | Scaled | Replica-min | Requests | Outcome |
|---|---|---:|---:|---|
| `inflight_requests` (8) | 1→2→3 | 26 | 40.6k | Scaled correctly; p95 improved |
| `ttft` p95 (300 ms) | never | 18 | 46.4k | Engine healthy; **client** p95 hit 3–5 s |
| `gpu_utilization` (75 %) | never | 18 | 46.5k | GPU never crossed threshold while saturated |

*"A GPU can read 60% utilized while the engine's request queue is already
backing up"* — only the concurrency signal detected real pressure
[src](https://www.together.ai/blog/autoscaling-endpoints-for-llm-inference).

**(f) Serving engine and per-request optimizations.** Own engine (Together
Inference Engine), not a vLLM fork. Lineage and components:

| Component | What is claimed | Date / source |
|---|---|---|
| Together Inference Engine 2.0 | 4× decode throughput vs open-source vLLM; 1.3–2.5× vs Bedrock/Azure/Fireworks/OctoAI; FlashAttention-3 at *"up to 75% of an H100's maximum"*; proprietary MHA + GEMM kernels for quantized inference; quality-preserving quantization via QuIP-style incoherence processing | 2024-07-18 [src](https://www.together.ai/blog/together-inference-engine-2) |
| Speculative decoding (research) | MagicDec (self-speculation + StreamingLLM), Adaptive Sequoia trees; **2.0×** on LLaMA-2-7B-32K, **1.84×** on LLaMA-3.1-8B, on 8×A100 — and the key claim that speculation *still pays at large batch* once the KV cache makes decode memory-bound again | 2024-09-05 [src](https://www.together.ai/blog/speculative-decoding-for-high-throughput-long-context-inference) |
| Medusa / Sequoia / SpecExec | Named as integrated algorithms, with custom drafters trained on RedPajama (30T tokens) | 2024-07-18 [src](https://www.together.ai/blog/together-inference-engine-2) |
| Together Kernel Collection (TKC) | FP8 GEMM **>2×** over H100 FP8 GEMMs; attention **1.8×** faster than FlashAttention-3; built on CUTLASS, Triton and ThunderKittens — the FP8 kernel in *"under two weeks, fewer than 200 lines"* | 2025-02-13 [src](https://www.together.ai/blog/nvidia-hgx-b200-with-together-kernel-collection) |
| Custom speculators (product) | Trained on a customer's own traffic: **1.23–1.45×** over the base speculator, **1.85–2.97×** over plain next-token; **23–26 %** GPU-hour reduction vs base speculator, **49–61 %** vs unoptimized. 20M tokens (~10k pairs) already buys >1.10× | 2025-05-12 [src](https://www.together.ai/blog/customized-speculative-decoding) |
| ATLAS (adaptive speculator) | Heavyweight static speculator + lightweight adaptive speculator learning from live traffic + confidence-aware controller adjusting lookahead. DeepSeek-V3.1 **500 tok/s** (2.65×); Kimi-K2 **460 tok/s**; up to **4×** over FP8 baseline (105→501 TPS) — 4×B200, **batch size 1** | 2025-10-10 [src](https://www.together.ai/blog/adaptive-learning-speculator-system-atlas) |
| CPD — cache-aware PD disaggregation | Adds a **pre-prefill tier** above ordinary prefill/decode: the router estimates prompt reusability and sends low-reuse ("cold") requests to pre-prefill nodes so they cannot block the warm path. Three-level cache — GPU HBM, host DRAM, cluster-wide RDMA cache. **35–40 % higher sustainable throughput** vs conventional disaggregation, tighter tails, sub-second-to-low-second median TTFT at saturation. B200, TP4 prefill nodes | 2026-03-04 [src](https://www.together.ai/blog/cache-aware-disaggregated-inference) |
| DeepSeek-V4-class serving | Three coexisting KV layouts (CSA stride-4 / HCA stride-128 / SWA ~128-token exact local), each needing its own eviction and prefix-reuse policy; single HGX B200 node moved **~1.2M → 3.7M tokens** of KV capacity by changing the SWA cache policy; SWA recompute-on-hit bounded to ~8K tokens; per-token KV initially 3.8 KB vs V3's 3.4 KB | 2026-05-11 [src](https://www.together.ai/blog/serving-deepseek-v4-why-million-token-context-is-an-inference-systems-problem) |
| Kernels practice | ThunderKittens cuts *"1,000+ lines of CUDA to 100–200"*; FP4/FP8 GEMMs up to **2× over cuBLAS on H100** within one week of Blackwell access; team ~15 people as of 2025-03 | 2026-04-01 [src](https://www.together.ai/blog/inside-the-together-ai-kernels-team) |
| Consolidated levers | FP16→FP8→FP4 worth **20–40 %** throughput; tuned MTP/speculation **20–50 %** faster decode; regional inference proxies shave **50–100 ms** off TTFT | 2026-01-22 [src](https://www.together.ai/blog/optimizing-inference-speed-and-costs) |

**(g) Routing, concurrency, rate limits, SLAs.** Contractual **99 % single-DC /
99.9 % multi-DC**, with 99.9 % *delivered* claimed. Engineering: weights
deployed across two facilities each sized to absorb full load, live traffic to
both (not cold standby), seconds-level failover, passive hardware telemetry plus
active health checks run *between workloads* by the scheduler — "detect fast,
drain, replace". Measured at inference completion, not at the load balancer.
They cite 2B+ tokens/minute during evaluation windows
[src](https://www.together.ai/blog/99-9-uptime-for-inference) (2026-07-16), and
**>400 trillion tokens/month** platform-wide
[src](https://www.together.ai/blog/the-production-platform-for-open-weight-ai-inference).

**(h) Observability / developer surface.** Organisation-level **Prometheus
endpoints** for external scraping, in-product analytics, `tg` CLI, REST and web
UI. Batch API: ≤50,000 requests/batch, 100 MB input file, 10 MB/line, **30B
tokens enqueued per model**, up to 50 % discount, charged only for successful
responses, dedicated endpoints eligible but **not discounted**
[src](https://docs.together.ai/docs/batch-inference).

**(i) Pricing and what it implies.** DeepSeek V4.1 Flash $0.30/$1.20 per 1M;
DeepSeek V4 Pro 0813 $1.32/$3.96; Qwen3.7-Plus $0.32/$1.28; Llama 3.3 70B
$1.04/$1.04 [src](https://www.together.ai/pricing). *(Inferred)* Together sits
in the middle of the DeepSeek-V4.1-Flash market (§5.1) at exactly the DeepSeek
first-party output price ×2 — consistent with a business that prices off
*capability and SLA* rather than to the floor, and that expects the
price-sensitive tail to go to dedicated endpoints where utilisation risk moves
to the customer.

**(j) Published numbers.** Collected above. The headline serving figures:
DeepSeek-R1-0528 on B200 **334 TPS** serverless peak (vs 302 unoptimized), up to
**386 TPS** at batch 1 on dedicated, **2.3–2.8×** vs H200
[src](https://www.together.ai/blog/fastest-inference-for-deepseek-r1-0528-with-nvidia-hgx-b200);
"#1 output speed among GPU-based providers" on Artificial Analysis with
GPT-OSS-20B ~2×, Qwen3-235B >2.75×, Kimi-K2 >65 %, DeepSeek-R1-0528 >13 % over
the next fastest
[src](https://www.together.ai/blog/fastest-inference-for-the-top-open-source-models).

**(k) What transfers to bare metal.**

*Transfers directly.* The autoscaling result — **scale on queue depth, not GPU
utilisation, and never on TTFT alone** — is a free win and costs one Prometheus
rule. The asymmetric window discipline (fast up, slow down) is the correct
default for anything with a 90–150 s cold start, which is every LLM replica.
CPD's core insight is implementable on any cluster that already does PD
disaggregation: **classify requests by expected prefix reuse and give cold
prefills their own pool**, so a 200K-token cold prompt cannot head-of-line-block
a warm 2K one. The three-level KV hierarchy (HBM → host DRAM → RDMA pool) is
exactly what LMCache/Mooncake-style tiering gives you on open engines. And the
custom-speculator economics are the single highest-leverage number here: **20M
tokens of your own traffic is enough to beat a generic drafter**, which any
self-hoster with a month of logs already has.

*Does not transfer.* "4× faster warm starts through fleet-wide weight caching"
needs a fleet — the win comes from another replica somewhere already holding the
weights. The 99.9 % multi-DC architecture requires two facilities each sized for
full load, i.e. ~2× the hardware; a single-site cluster buys at most the
detect-fast-drain-replace half of it. TKC and the ThunderKittens kernels are not
distributed as a usable artifact for outside workloads — the *library* is open,
the tuned FP8/FP4 kernels are the product.

---

### 2.2 Fireworks AI

The closest competitor to Together in both product shape and publication habit,
with a different emphasis: Fireworks publishes more about **quantization
methodology and deployment-shape search**, less about fleet and uptime.

**(a) What they sell.** Three tiers, explicitly: **Serverless** (per-token,
"high rate limits and postpaid billing"), **On-Demand Deployments** (per
GPU-second, "faster speeds, higher rate limits, and lower costs at scale"), and
**Enterprise** (reserved) [src](https://fireworks.ai/pricing). Plus fine-tuning,
a Batch API, multi-LoRA, and an eval/RL product line.

**(b) Hardware and where it runs.** GPU menu from the deployment docs: **A100
80GB, H100 80GB, H200 141GB, B200 180GB, B300 288GB, AMD MI325X 256GB, MI350X
288GB**, with the caveat that *"availability varies by region"* and *"not every
model runs on every GPU"*
[src](https://docs.fireworks.ai/guides/ondemand-deployments). On-demand hourly:
H100 80GB **$8.00**, H200 141GB **$8.00**, B200 180GB **$13.00**, B300 288GB
**$15.00**, GB300 288GB **$20.00** [src](https://fireworks.ai/pricing). The AMD
partnership (MI325X/MI355X) was announced 2025-10-20 with *no* benchmarks —
*"Stay tuned for benchmarks"*
[src](https://fireworks.ai/blog/fireworks-amd-ai-infrastructure-partnership).

Where it physically runs is **not published**. The nearest statement is from
2024: *"The same infrastructure for on-demand powers companies like Uber,
Doordash and Cursor"* [src](https://fireworks.ai/blog/why-gpus-on-demand)
(2024-06-03), which says nothing about ownership. *(Inferred)* the per-region
GPU availability caveat and the presence of GB300 alongside MI350X within months
of each launch is the signature of **rented neocloud capacity across several
vendors**, not an owned fleet — but Fireworks has never said so, and this is an
inference from a docs caveat. Their own 2026 post on providers-vs-routers
insists *"the company controlling the API endpoint is the same company
controlling the hardware"*
[src](https://fireworks.ai/blog/inference-providers-vs-api-routers)
(2026-03-06), which asserts operational control, not ownership.

**(c) Orchestration and isolation.** Replica-based, with `--accelerator-count`
overriding GPUs per replica, per-deployment autoscaling policy, and region
pinning. The orchestrator and sandbox are **not published**.

**(d) Cold-start engineering.** No measured cold-start times are published.
What *is* documented is the behaviour, which matters more than the number: a
scaled-to-zero deployment receiving a request **immediately returns HTTP 503
with `DEPLOYMENT_SCALING_UP`** and *"Requests to a scaled-to-zero deployment are
**not queued**"* — the client must retry
[src](https://docs.fireworks.ai/deployments/autoscaling). That is a design
choice worth copying or rejecting deliberately, not a bug.

**(e) Autoscaling.** Scale-up window **30 s** default; scale-down **10 min**
default; scale-to-zero after **1 hour** idle (minimum 5 min); deployments with
`min_replicas: 0` are **auto-deleted after 7 days** without traffic. Signals:
general load target (0–1), tokens generated/s/replica, prompt tokens/s/replica,
requests/s/replica, concurrent requests/replica — *"when multiple targets are
specified, the maximum replica count across all is used"*
[src](https://docs.fireworks.ai/deployments/autoscaling). Note the contrast with
Together: Fireworks' default down-window is **2× longer** and its up-window
**2× shorter**, the same asymmetry pushed further.

**(f) Serving engine and per-request optimizations.** Own stack (FireAttention),
not a vLLM fork.

| Component | What is claimed | Date / source |
|---|---|---|
| FireAttention V4 / NVFP4 | NVFP4 chosen over MXFP4 for Blackwell: *"2× FLOPs throughput"* vs competing micro-scaling modes, 1.5–2× less memory bandwidth, better quality from block size 16 vs 32 and FP8-compatible scales. Complete backend rewrite for TensorCore Gen 5 (`sm_100`), because Hopper `9.0a` ops are not forward-compatible. GEMM, grouped GEMM and attention all re-authored. **>250 tok/s on DeepSeek V3, 8-GPU NVLink**; **3.5×** vs SGLang FP8 on H200; beats TRT-LLM's FP4 *"by a significant margin"*. QAT recovers FP4/FP8 quality loss | 2025-05-28 [src](https://fireworks.ai/blog/fireattention-v4-fp4-b200) |
| Quantization methodology | SmoothQuant, GPTQ, Hadamard/SpinQuant outlier transforms; scales from per-tensor to per-small-group. **Four named aggression levels**: L1 MLP (excl. first/last), L2 +omitted layers +QKV, L3 +KV cache, L4 +attention prefill. Quality judged by **KL divergence and token rejection rate, prefill and generation measured separately**, on forced quantized generation — not task metrics. Llama-3.1-8B generation KLD 0.00286 (L1) → 0.00796 (L4); their bar is **KLD < 0.007** | 2024-08-01 [src](https://fireworks.ai/blog/fireworks-quantization) |
| Multi-LoRA | Cross-model continuous batching + dynamic adapter loading with caching, so *"the number of supported LoRAs is not constrained by GPU memory and new models can be added or taken down within seconds"*. **Thousands of LoRAs on one Mixtral/Mistral cluster** (Cresta). ~**90 % of base-model** speed when serving hundreds of adapters. Priced at base-model rates | 2024-09-18 [src](https://fireworks.ai/blog/multi-lora) |
| FireOptimizer | Adaptive speculative execution: drafters trained on the customer's data. **Up to 3× latency improvement** vs generic drafters; Cursor **2×**. Worked example: **76 % hit rate → 2× faster** vs a generic drafter's **29 % hit rate → 1.5× *slower*** | 2024-08-30 [src](https://fireworks.ai/blog/fireoptimizer) |
| 3D FireOptimizer | Search over **deployment shape (which GPU), sharding (data/tensor/sequence parallel, disaggregation), and scheduling (TTFT vs throughput)**, producing Pareto curves per workload. Workloads characterised as (120k in / 400k out / 116k cached), (2.5k / 1.5k), (5k / 500 / 1.5k cache) | 2025-06-14 [src](https://fireworks.ai/blog/3d-fireoptimizer) |
| Sparse-attention kernels (MiniMax M3) | Four-stage pipeline — index build, schedule, main sparse attention, combine. Uses **tcgen05**, TMA, `cp.async` gather, 128×128 MMA on SM100. **KV-stationary** execution (each selected KV block loaded once in the outer loop, queries gathered). **1.9–2.4×** over query-stationary FlashInfer, **~1.6×** over MiniMax's own open MSA kernel; full module **1.18–1.43×**. Peak ~**980 TFLOP/s at ~4.1 TB/s** HBM | 2026-07-10 [src](https://fireworks.ai/blog/kernel-optimization-for-minimax-m3-on-nvidia-blackwell) |

**(g) Routing, concurrency, rate limits, SLAs.** Serverless is sold in
**Standard / Priority / Fast** tiers, with Priority at a flat **1.25×**
Standard across every model (see §5.1) and "Fast" as a separate,
higher-priced model variant. US-only serverless models are priced at **1.5×**
base from 2026-09-01 [src](https://docs.fireworks.ai/serverless/pricing). Their
routing argument, aimed at OpenRouter: *"Proxy hops are always additive"* —
median TTFT through a router is necessarily worse, though p95 can improve via
redundancy; and routers *"lack visibility into GPU-level optimization decisions
made upstream"*. They also raise "shadow traffic" (live requests duplicated for
evaluation, invisible in the response) and note a router's DPA *"can only bind
itself"*, not its upstreams
[src](https://fireworks.ai/blog/inference-providers-vs-api-routers) (2026-03-06).
No numeric SLA is published.

**(h) Observability / developer surface.** OpenAI-compatible API, a Responses
API, CLI (`firectl`), eval-protocol tooling. Batch API: **50 % below
serverless**, results *"in a few hours, with a max turnaround time of 24
hours"*, **no upper limit on requests per batch**, datasets <500 MB
[src](https://fireworks.ai/blog/batch-api) (2025-07-31).

**(i) Pricing and what it implies.** Serverless, per 1M, input / cached / output
[src](https://docs.fireworks.ai/serverless/pricing):

| Model | Standard | Priority |
|---|---|---|
| **DeepSeek V4.1 Flash** | **$0.30 / $0.006 / $1.20** | $0.375 / $0.0075 / $1.50 |
| DeepSeek V4.1 Flash (US) | $0.45 / $0.009 / $1.80 | $0.5625 / $0.01125 / $2.25 |
| DeepSeek V4 Pro (0813) | $1.32 / $0.044 / $3.96 | $1.65 / $0.055 / $4.95 |
| DeepSeek V4 Flash (0731) | $0.22 / $0.007 / $0.66 | $0.275 / $0.00875 / $0.825 |
| **Kimi K3** | **$3.00 / $0.30 / $15.00** | $3.75 / $0.375 / $18.75 |
| Kimi K3 (US) | $4.50 / $0.45 / $22.50 | $5.625 / $0.5625 / $28.125 |
| Qwen 3.8 Max | $2.00 / $0.25 / $6.00 | $3.00 / $0.375 / $9.00 |
| GLM 5.3 Flash | $0.15 / $0.03 / $0.50 | $0.1875 / $0.0375 / $0.625 |

**A discrepancy worth recording.** OpenRouter's endpoint API lists a Fireworks
endpoint for `deepseek/deepseek-v4.1-flash` at **$0.22 in / $0.66 out / $0.007
cached** [src](https://openrouter.ai/api/v1/models/deepseek/deepseek-v4.1-flash/endpoints)
— which is Fireworks' *V4 Flash 0731* price, not its V4.1 Flash price. Either
OpenRouter is routing to a differently-named deployment, or one of the two
tables is stale. Both are cited in §5.1 rather than silently picking one.
**⚠️ TO BE VERIFIED.**

*(Inferred)* The flat 1.25× Priority multiplier is the most informative pricing
signal Fireworks emits: a uniform 25 % premium for queue priority across every
model implies priority is sold as **scheduler weight against a shared pool**,
not as reserved hardware — if it were reserved capacity the premium would scale
with how hard the model is to host. Compare Anthropic's Priority Tier (§2.13),
which *is* reserved and is sold as ITPM/OTPM commitments with a duration.

**(j) Published numbers.** In the table above. Note that the headline *"3.5×
vs SGLang"* is FP4-on-B200 against FP8-on-H200 — a hardware-generation change
and a format change together, not an engine A/B.

**(k) What transfers.** The **quantization-aggression ladder (L1–L4) plus
KLD-based acceptance** is the most directly copyable artifact in this entire
document. It gives you a defensible, task-independent way to decide how far to
push a checkpoint — measure KL divergence and token rejection on forced
quantized generation, prefill and decode separately, and hold a bar (theirs:
KLD < 0.007). Do that once and quantization stops being a vibe. The
**76 % vs 29 % acceptance-rate example** is the clearest published statement of
why generic drafters can make things *worse*, and it bounds what
[`serving-optimizations.md` §2](../cross-cutting/serving-optimizations.md#2-speculative-decoding)
calls the acceptance-rate risk. The **KV-stationary sparse-attention pattern**
(load each selected KV block once in the outer loop, gather queries) is a real
kernel-design lesson for anyone writing DSA/CSA-family kernels, and it is the
same shape the repo's own sparse-attention models need. The autoscaling
defaults (30 s up / 10 min down / 1 h to zero) are a sane starting point.

*Does not transfer.* FireAttention is not available outside Fireworks. The
multi-LoRA *"thousands of adapters"* economics depend on aggregating demand
across customers — a single tenant with three adapters gets almost none of it.

---

### 2.3 DeepInfra

The price floor of the GPU-based market, and the most transparent about
hardware-per-price of anyone here.

**(a) What they sell.** Per-token serverless across a wide model catalogue;
**dedicated instances** for custom/fine-tuned models billed hourly; raw GPU
cluster rental with SSH.

**(b) Hardware.** Named without hedging: *"run your own fine-tuned LLM on
**A100 / H100 / H200 / B200 / B300** with autoscaling"*
[src](https://docs.deepinfra.com/). Dedicated hourly: **A100 80GB $0.89/h,
H100 80GB $2.20/h, H200 141GB $2.69/h** [src](https://deepinfra.com/pricing).
Where the fleet runs is **not published**.

Those hourly rates are the single most useful datum DeepInfra publishes. Set
against [`cloud-pricing.md` §5.14](../cross-cutting/cloud-pricing.md)'s planning
rows — H100 `low` $3.20 (Hyperstack), H200 `low` $3.99 — DeepInfra is **retailing
H100 at 0.69× and H200 at 0.67× the cheapest reputable on-demand price this repo
could find**. *(Inferred)* That is only possible on long-term committed or owned
capacity resold at a margin over amortised cost; it is not resale of on-demand.
It also means a self-hoster comparing "rent vs DeepInfra dedicated" is comparing
against a *better* hardware price than the one in this repo's cost grid.

**(c) Orchestration and isolation.** Private deployments *"for users needing
data isolation or custom models"*; the mechanism is **not published**.

**(d) Cold-start engineering.** **Not published.**

**(e) Autoscaling.** Stated to exist ("with autoscaling"), billed at
**minute granularity** for custom deployments, with *"no idle GPU charges or
minimums"* on serverless [src](https://deepinfra.com/pricing). Signals,
windows and scale-to-zero behaviour are **not published**.

**(f) Serving engine.** **Not published.** DeepInfra publishes no engine or
kernel work. What it *does* publish, via OpenRouter's per-endpoint metadata, is
its **quantization per model**, and this is where DeepInfra becomes interesting:
it operates several endpoints for the same model at different precisions and
different prices — `deepinfra/bf16`, `deepinfra/fp8`, `deepinfra/turbo` all
appear for `openai/gpt-oss-120b` at $0.037/$0.17, $0.20/$0.95 and $0.15/$0.60
respectively [src](https://openrouter.ai/api/v1/models/openai/gpt-oss-120b/endpoints).
For `qwen/qwen3.8-27b` its endpoint is labelled **bf16** at $0.15/$1.875 —
i.e. **DeepInfra undercuts most FP8 competitors while serving BF16**, which is
either a very good deal or a labelling question. **⚠️ TO BE VERIFIED** —
OpenRouter's `quantization` field is provider-declared.

**(g) Routing, rate limits, SLAs.** **Not published** beyond OpenAI-compatible
rate limits.

**(h) Observability.** OpenAI SDK compatibility, *"no code changes required"*.

**(i) Pricing.** Per 1M [src](https://deepinfra.com/pricing): DeepSeek-V4-Flash-0731
$0.06/$0.18; DeepSeek-V4-Pro $1.30/$2.60; DeepSeek-V3 $0.32/$0.89; Qwen3.5-9B
$0.10/$0.15; Qwen3-Max $1.20/$6.00; Qwen3.6-27B $0.32/$3.20; Kimi-K2.6
$0.75/$3.50; Kimi-K3 $2.85/$14.25; Llama-3.1-8B $0.02/$0.04; Llama-3.3-70B
$0.10/$0.32. Via OpenRouter, DeepSeek V4.1 Flash at **$0.14/$0.42** (fp8) —
**the cheapest endpoint for that model in the entire market on 2026-09-19**
(§5.1).

*(Inferred)* $0.14/$0.42 is **below DeepSeek's own first-party $0.15/$0.60
off-peak price**. A reseller undercutting the model's author on output by 30 %
is either running at materially higher utilisation than DeepSeek's own fleet,
running a more aggressive quantization, or buying share. The repo's own numbers
say the first is plausible: DeepSeek's published margin analysis (§2.10) implies
its API is priced far *above* its marginal cost, leaving a wide corridor.

**(j) Published numbers.** Throughput/latency: **none published**. Uptime via
OpenRouter's rolling 30-minute window: 100.0 % for `gpt-oss-120b`, 94.25 % for
`qwen3.8-27b`, 82.11 % for `kimi-k3` on 2026-09-19
[src](https://openrouter.ai/api/v1/models/moonshotai/kimi-k3/endpoints) — the
lowest of any Kimi-K3 endpoint listed that day, which is a real datum about what
the cheap tier costs in availability.

**(k) What transfers.** Almost nothing technical — DeepInfra publishes no
engineering. What transfers is **the price signal**: DeepInfra's dedicated
hourly rates are a usable floor estimate for what committed GPU capacity
actually costs, and its per-token floor is the number your self-hosted $/1M has
to beat if "just buy tokens" is on the table. Its multi-precision endpoint
strategy is also a reminder that **precision is a product axis**, not only an
engineering one.

---

### 2.4 Novita AI

A mid-market per-token provider that, unusually for its price tier, does publish
kernel work — and publishes it against the exact model families this repo cares
about.

**(a) What they sell.** *"200+ models, on-demand GPUs, and secure agent
runtimes — unified under one API"*, plus batch inference at a **50 % discount on
input and output** [src](https://novita.ai/pricing).

**(b) Hardware.** Named in the engineering posts rather than the pricing page:
**H200** and **Blackwell (B200/B300)** for the MoE kernel work, with explicit
SM90 and SM100-family paths
[src](https://novita.ai/blog/novita-chord-w4a16-moe/) (2026-09-15). GPU hourly
pricing is **not published** on the pricing page.

**(c) Orchestration and isolation.** **Not published.**

**(d) Cold-start engineering.** **Not published.**

**(e) Autoscaling.** **Not published.**

**(f) Serving engine and per-request optimizations.** **vLLM- and
SGLang-based**, with upstream-contributed kernels — the opposite of the Together
/ Fireworks proprietary-engine model, and much more directly reusable:

| Work | What it is | Numbers | Date |
|---|---|---|---|
| **Chord** W4A16 INT4 MoE kernels | A CUDA operator package for INT4 (W4A16) MoE inference on Kimi-K2.x, in two families: *indexed* (Humming-derived, prefill+decode, H200/B200/B300) and *grouped* (SM90-specialised, different routing and weight layout). Enabled in vLLM via `--quantization humming` / `moe_backend="humming"` | Per-layer vs public Humming: **H200 prefill 1.11–1.20×, H200 decode 1.16–1.24×, B300 decode 1.81–2.15×**; grouped 1.00–1.35× | 2026-09-15 [src](https://novita.ai/blog/novita-chord-w4a16-moe/) |
| DSpark speculative decoding in vLLM | Lightweight drafters with parallel block-drafting, no target-model change. Swept draft window n=3→7 | Kimi-K2.6 **2.17× → 2.55×** (+17.8 %); Kimi-K2.7-Code **2.12× → 2.36×** (+11.7 %). TP=8, CUDA graphs, `max_model_len=20000`, **batch size 1**. Explicit finding: *"a larger draft window does not automatically improve throughput"* — DSpark held acceptance across larger windows better than Eagle3-MLA | 2026-07-10 [src](https://novita.ai/blog/kimi-k2-dspark-speculative-decoding-throughput/) |
| GLM4-MoE on SGLang | Production tuning | **65 % faster TTFT** | 2026-01-21 [src](https://novita.ai/blog/optimizing-glm4-moe-for-production/) |

**(g) Routing, rate limits, SLAs.** **Not published.**

**(h) Observability.** OpenAI-compatible API.

**(i) Pricing.** Per 1M [src](https://novita.ai/pricing): DeepSeek V4.1 Flash
$0.30/$1.20; DeepSeek V4 Pro $1.6/$3.2; DeepSeek V3.1 $0.27/$1.00; Llama 3.1 8B
$0.02/$0.05; Llama 3.3 70B $0.135/$0.40; Qwen 3.8 Flash $0.15/$0.47; Qwen 3.5
27B $0.30/$2.40. Via OpenRouter, DeepSeek V4.1 Flash at **$0.285/$1.14** (fp8)
and Qwen3.8-27B at $0.42/$3.00. Batch at **50 % off both directions** — a
steeper batch discount than the 50 %-off-total that Anthropic, OpenAI, Groq,
Fireworks and Together all converge on, though arithmetically identical.

**(j) Published numbers.** Kernel speedups above. Uptime via OpenRouter on
2026-09-19: `gpt-oss-120b` 85.0 %, `qwen3.8-27b` 100 %.

**(k) What transfers.** **More than any other provider in this document**,
because Novita's optimizations ship as vLLM/SGLang paths rather than as a closed
engine. The DSpark window sweep is directly actionable for this repo's DeepSeek
and Kimi experiments — it is a published acceptance-vs-window curve on the exact
model family, and it says the draft window is a tunable with a real optimum, not
a "bigger is better" knob. That is the empirical backing
[`serving-optimizations.md` §2](../cross-cutting/serving-optimizations.md#2-speculative-decoding)
asks for and
[`README.md` open question 6](../README.md#6-top-14-open-questions-across-the-tree)
flags as missing. The Chord B300-decode **1.81–2.15×** figure is a concrete
statement that *MoE kernel choice on Blackwell is worth roughly a hardware
generation*, which bears directly on the Kimi-K3 B300 cells in
[`cost-matrix.md` §3](../matrix/cost-matrix.md#3-max-throughput--1m-output-tokens-s4-4k-in--512-out).

Caveat for both: **batch size 1**. Neither the DSpark nor the ATLAS numbers say
anything about speculation at the batch sizes this repo's cost grid operates at
(64–3,327). Treat them as latency results, not throughput results.

---

### 2.5 Groq

The oldest custom-silicon inference story, and — as of 2026 — the one whose
premise has changed most.

**(a) What they sell.** GroqCloud: per-token APIs on Free / Developer /
Enterprise tiers, a Batch API, and enterprise/sovereign deployments. No GPU-hour
or chip-hour product is publicly priced.

**(b) Hardware and where it runs.** *This is the field that moved.* Three
published facts, in order:

1. **The LPU as designed**: on-chip SRAM with *"memory bandwidth upwards of 80
   terabytes/second"* against GPU off-chip memory at ~8 TB/s — a claimed 10×;
   deterministic execution where *"every execution step is completely
   predictable to the smallest execution period"*, statically scheduled at
   compile time; a compiler designed *before* the chip; chip-to-chip
   `RealScale` interconnect forming *"one shared resource fabric"* with no
   external routers or controllers; **14 nm** current generation with 4 nm
   planned [src](https://groq.com/blog/the-groq-lpu-explained). ⚠️ The LPU-explained
   post carries no visible date and the 14 nm/4 nm statement is clearly several
   years old; treat the *architecture* as sourced and the *process node* as
   stale.
2. **2025-12-24 — NVIDIA licensing.** Groq and NVIDIA entered a *"non-exclusive
   inference technology licensing agreement"*; founder Jonathan Ross, president
   Sunny Madra *"and team members will transition to NVIDIA"*; Simon Edwards
   becomes CEO; GroqCloud *"will continue to operate without interruption"*
   [src](https://groq.com/newsroom/groq-and-nvidia-enter-non-exclusive-inference-technology-licensing-agreement-to-accelerate-ai-inference-at-global-scale).
   The release does not address the LPU roadmap.
3. **2026-08-24 — Groq deploys NVIDIA silicon.** Groq will deploy **NVIDIA
   Groq 3 LPX accelerators paired with Vera Rubin NVL72**, with Dell, claiming
   **3,400 output tokens/second on Gemma 4 31B at 100K context** and *"4× higher
   interactivity for latency-sensitive agentic tasks"*
   [src](https://groq.com/blog/groq-among-the-first-to-bring-nvidia-groq-3-lpx-and-vera-rubin-nvl72-to-market).
   The same post claims *"six million developers"* and *"trillions of tokens
   weekly"*.

*(Inferred, and flagged as the most consequential inference in this document):*
the entity now called Groq is **in transition from a custom-silicon operator to
a specialised NVIDIA-based inference cloud running licensed Groq scheduling
technology**. Nothing published says the LPU fleet is being retired, and the
existing GroqCloud models still serve at LPU-class speeds; but a company whose
thesis was "GPUs are the wrong architecture"
[src](https://groq.com/blog/why-ai-requires-a-new-chip-architecture) announcing
it is among the first to market with an NVIDIA rack-scale system is a
structural change, not a product line extension. **⚠️ TO BE VERIFIED** — the
LPU's share of GroqCloud serving in 2026-09 is not published.

Physical footprint (published, all newsroom): Helsinki (Europe), Sydney
(APAC), a UK facility via Equinix, Saudi Arabia with Aramco Digital, plus a US
DOE partnership. Developer count *"now exceeding 3.5 million"* on the capacity
post [src](https://groq.com/blog/groqcloud-expanding-to-meet-demand), 6M on the
August 2026 post — LPU counts, tokens/day and utilisation are **not published**.

**(c) Orchestration and isolation.** **Not published.** By construction the LPU
model admits far less of it than a GPU cloud: the compiler *"maps and schedules
a program to run across one or multiple LPUs"* ahead of time, so there is no
runtime scheduler to multiplex against in the way a GPU has.

**(d) Cold-start engineering.** **Not published**, and *(inferred)* largely not
applicable in the GPU sense: weights live in SRAM across a statically-allocated
set of chips, so a model is either resident or it is not. The corollary is that
GroqCloud's model catalogue is **small and curated** — five production models
and a handful of previews [src](https://console.groq.com/docs/models) — which is
what you would expect when residency is binary and expensive.

**(e) Autoscaling.** **Not published**; no scale-to-zero, no per-container
concurrency knob is exposed. The user-visible capacity control is rate limits.

**(f) Serving engine and per-request optimizations.** Groq Compiler + RealScale.
For MoE and large models the only published statement is that chips
*"interconnect and create one shared resource fabric for models to run on"* and
that Llama 4 Maverick (400B MoE) was deployed day-of-release
[src](https://groq.com/blog/from-speed-to-scale-how-groq-is-optimized-for-moe-other-large-models)
— sharding strategy, SRAM budgeting and scheduling detail are **not published**.
Quantization is branded **TruePoint Numerics** and referenced on model pages
(*"Groq applies their TruePoint Numerics quantization approach to maintain
accuracy while accelerating performance"*
[src](https://console.groq.com/docs/model/qwen/qwen3.8-27b)) but has **no public
technical page** — the URL returns 404. **⚠️ TO BE VERIFIED.**

**(g) Routing, concurrency, rate limits, SLAs.** Rate limits are the product
surface and are published per model per plan. Developer plan, e.g.:
`gpt-oss-120b` **250K TPM / 1K RPM**; `qwen/qwen3.8-27b` **250K TPM / 1K RPM**;
Compound **200K TPM / 200 RPM**; Whisper metered in **ASH** (audio sample hours)
[src](https://console.groq.com/docs/models). The two Llama production models are
**Enterprise-only, "ContactSales" for both price and limits** — a notable
retreat from the open self-serve catalogue Groq launched with. No numeric uptime
SLA is published.

**(h) Observability / developer surface.** OpenAI-compatible API,
`api.groq.com/openai/v1/models`, a console, agentic tooling (Compound systems
with built-in web search and code execution). Batch API: **50 % discount**
(raised from a standard 25 % during a 2025 promotion), **24 hours to 7 days**
completion windows — a *longer* maximum window than anyone else here — ≤50,000
lines and 200 MB per JSONL, charged only for completed requests, inputs and
results retained 30 days
[src](https://console.groq.com/docs/batch),
[src](https://groq.com/blog/batch-processing-with-groqcloud-for-ai-inference-workloads).

**(i) Pricing and what it implies.** Published per 1M
[src](https://console.groq.com/docs/models):

| Model | Speed (tok/s) | Input | Output | Context |
|---|---:|---:|---:|---:|
| `openai/gpt-oss-120b` | 500 | $0.15 | $0.60 | 131,072 |
| `openai/gpt-oss-20b` | 1000 | $0.075 | $0.30 | 131,072 |
| `qwen/qwen3.8-27b` (preview) | 450 | **$0.80** | **$4.00** | 131,042 |
| `groq/compound` | 450 | — | 200K TPM / 200 RPM | 131,072 |
| `llama-3.1-8b-instant` | 560 | ContactSales | ContactSales | 131,072 |
| `llama-3.3-70b-versatile` | 280 | ContactSales | ContactSales | 131,072 |

*(Inferred)* The shape of this table is the tell. Groq prices `gpt-oss-120b` at
exactly the market rate ($0.15/$0.60 — identical to Together, Nebius, Bedrock
and SiliconFlow, §5.3) while delivering ~500 tok/s, and prices the **dense 27B
at $0.80/$4.00, which is 5.3× the market's cheapest Qwen3.8-27B endpoint and
1.67× the highest GPU-based one**. A provider whose architecture holds weights
in SRAM is *structurally* expensive per unit of resident parameter and cheap per
unit of latency; the price table reads exactly that way. The **7-day batch
window** — three to seven times longer than anyone else's — is the other tell:
*(inferred)* batch on Groq is filling scheduling gaps in a statically-compiled
fleet with far less elasticity than a GPU cluster, so it needs a wider window to
find room.

**(j) Published numbers.** Per-model tok/s in the table (these are Groq's own
published figures, not measured here); 3,400 output tok/s on Gemma 4 31B @100K
on the new NVIDIA platform; ~80 TB/s SRAM bandwidth. Cold start, uptime and
utilisation: **none published**.

**(k) What transfers.** *Architecturally, almost nothing* — the entire thesis is
"put the weights in SRAM", which a GPU owner cannot do. Two things do transfer.
First, **the determinism argument**: compile-time static scheduling means no
runtime contention and therefore tight, predictable tail latency. The
GPU-side version of that is real and cheap — CUDA graphs, fixed batch shapes,
pinned memory pools — and is exactly what Fireworks writes about when it
discusses CUDA graphs eliminating host-side launch overhead. Second, **the
7-day batch window as a capacity-smoothing instrument**: if your cluster's
interactive load is peaky, the correct lever is a long-window batch queue, not
more GPUs.

The thing that emphatically does *not* transfer is the headline tok/s. 450–1000
tok/s on Groq is **per-user single-stream speed** on a fleet whose economics are
set by chip count, not a throughput figure comparable to the aggregate
tok/s/GPU columns in [`cost-matrix.md` §3](../matrix/cost-matrix.md).

---

### 2.6 Cerebras

The clearest architectural contrast to GPUs in this document, and the only
provider here serving **one of this repo's exact models** (Qwen3.8-27B) at a
published price and speed.

**(a) What they sell.** Cerebras Inference: free trial, pay-as-you-go
per-token public endpoints, and **Dedicated Endpoints** (reserved private
capacity, bring-your-own weights, bespoke draft models and quantization
strategies) [src](https://inference-docs.cerebras.ai/dedicated/overview). Also
resold through AWS Marketplace, OpenRouter, Hugging Face and Vercel
[src](https://www.cerebras.ai/pricing).

**(b) Hardware.** Owned CS-series systems in own/partner data centres.
Generations, with dates:

| Gen | Published specs | Source |
|---|---|---|
| **WSE-3 / CS-3** | 4 trillion transistors, **900,000 cores**, **44 GB on-chip SRAM**, **21 PB/s** aggregate memory bandwidth — *"7,000× that of an H100"*, and ~*"1,000–2,000× higher effective memory bandwidth than an NVIDIA B200"* | [src](https://www.cerebras.ai/blog/introducing-cerebras-inference-ai-at-instant-speed) (2024-08-27), [src](https://www.cerebras.ai/blog/disaggregated-inference) (2026-03-13) |
| **WSE-3T / CS-4** | **53.5 PB/s** aggregate on-wafer fabric bandwidth; AC/DC converters placed *"about 0.5 millimetres from the wafer"* vs ~50 mm conventionally, ~2× power-delivery efficiency | Hot Chips 2026, [src](https://www.cerebras.ai/blog/ultrafast-frontier-inference-cerebras-deep-dive-at-hot-chips-2026) (2026-08-25) |
| **CS-5 (targeted)** | *"up to 10,000 output tokens per second per user"* on leading open-source models; *"up to 5,000"* for frontier models | same |
| **CS-6 (future)** | wafer-scale SRAM + compute with **3D-stacked DRAM** to add capacity without losing locality | same |

**(c) Orchestration and isolation.** Public endpoints are shared; **Dedicated
Endpoints** are *"a private, provisioned instance … reserved exclusively for
your organization"* so that *"latency and throughput are not affected by other
users"* [src](https://inference-docs.cerebras.ai/dedicated/overview). The
internal scheduler is **not published**.

**(d) Cold-start engineering.** **Not published**; *(inferred)* structurally
similar to Groq — weights are resident in wafer SRAM, so the unit of
provisioning is a system, not a container, and the public catalogue is
correspondingly tiny: **two models** on public endpoints (`gpt-oss-120b`,
`qwen-3.8-27b`) with a much larger family list available only on dedicated
endpoints [src](https://inference-docs.cerebras.ai/models/overview).

**(e) Autoscaling.** **Not published.** The user-visible capacity mechanism is a
**dual-bucket rate limit**: every organisation has an *uncached token limit* and
a *total token limit*, so improving cache hit rate *"lets the same uncached
limit serve significantly more total tokens"*. Token-bucket replenishment
(continuous, not interval-reset); requests are pre-screened against
`max_completion_tokens` and rejected before processing if the estimate would
exceed quota [src](https://inference-docs.cerebras.ai/support/rate-limits).

*That dual-bucket design is the most quietly informative thing Cerebras
publishes.* Rate-limiting on **uncached** tokens separately from total tokens is
an explicit admission that the scarce resource is **prefill compute**, not
served tokens — which is precisely the finding
[`README.md` open question 5](../README.md#6-top-14-open-questions-across-the-tree)
records for this repo's own sparse-attention models.

**(f) Serving engine and per-request optimizations.**

- **Model partitioning:** models are split *"at layer boundaries across multiple
  CS-3 systems,"* with weights staying in local SRAM and only activations moving
  between stages [src](https://www.cerebras.ai/blog/how-cerebras-serves-gpt-5-6-sol-at-up-to-750-tokens-per-second)
  (2026-08-27). Earlier: *"20B models fit on a single CS-3 while 70B models fit
  on as few as four systems"* [src](https://www.cerebras.ai/blog/introducing-cerebras-inference-ai-at-instant-speed).
  On multi-wafer: *"execution is pipelined to keep high-volume tensor and expert
  communication within each wafer. Only lower-volume data, primarily
  activations, moves between wafers"*
  [src](https://www.cerebras.ai/blog/ultrafast-frontier-inference-cerebras-deep-dive-at-hot-chips-2026).
  **This is pipeline parallelism with tensor/expert parallelism confined inside
  a wafer** — the inverse of the GPU playbook, where TP crosses NVLink and PP is
  avoided.
- **MoE:** avoids expert parallelism entirely where it can — *"store much
  bigger models on the chip directly (roughly up to 1B in total parameter
  count)"*, weight streaming above that. The named innovation is **Batch Tiling
  on Attention (BTA)**: split attention's input batch into G tiles of size B,
  process independently, concatenate. Without BTA, throughput degraded **53 %**
  with more experts and **86 %** at lower sparsity on a Qwen3 3B-active/128-expert
  configuration; with BTA it stayed *"close to the dense model, across all
  expert counts"*. The WSE has *"about 900 times more on-chip memory (SRAM) than
  a latest single GPU"* [src](https://www.cerebras.ai/blog/moe-guide-scale) (2025-09).
- **Disaggregation:** announced **2026-03-13** with AWS — **Trainium for prefill,
  CS-3 for decode**, over EFA. Claims: **5× token throughput**, token generation
  **1,200 tok/s** (vs a 50 tok/s baseline), *"up to 4.5× improvement in P95
  latency"* on agentic workloads [src](https://www.cerebras.ai/blog/disaggregated-inference).
  Cerebras buying someone else's compute-optimised silicon for prefill is the
  strongest possible statement that **wafer-scale SRAM is a decode advantage,
  not a prefill one**.
- **Speculative decoding:** used — the 2024-10-24 "3× faster" release attributes
  Llama 3.1-70B at **2,100 tok/s** to rewritten MatMul/reduce/broadcast/elementwise
  kernels, asynchronous wafer I/O, and speculative decoding, calling it
  *"more than a hardware generation's worth of performance in a single software
  release"* [src](https://www.cerebras.ai/blog/cerebras-inference-3x-faster).
- **Quantization:** explicitly *storage-only*. *"Cerebras uses selective
  weight-only quantization only during storage… weights are stored in partial
  16-bit / 8-bit / 4-bit… For quality, sensitive layers are stored at full
  precision with dequantization on the fly… **The activations, attention, and kv
  cache remain in full precision and unquantized.**"* And: *"All of our public
  models are unpruned"*, with REAP-pruned models shared to Hugging Face for
  research but **not served**
  [src](https://inference-docs.cerebras.ai/models/overview).
- **Prompt caching:** automatic, no breakpoints or headers, **128-token blocks**,
  exact-prefix matching, **TTL guaranteed 5 minutes** and up to 1 hour under
  favourable load [src](https://inference-docs.cerebras.ai/capabilities/prompt-caching).
- **Multi-LoRA:** announced [src](https://www.cerebras.ai/blog/introducing-multi-lora-on-cerebras-inference);
  the hardware mechanism, adapter-switch cost and SRAM budget are **not published**.
  **⚠️ TO BE VERIFIED.**

**(g) Routing, concurrency, rate limits, SLAs.** Rate limits per model per tier,
e.g. Qwen 3.8 27B: Free Trial **5 RPM / 30K input TPM / 90K total TPM**;
Developer **300 RPM / 150K input TPM / 450K total TPM**
[src](https://inference-docs.cerebras.ai/models/qwen-3.8-27b). Production SLAs
are named as a dedicated-endpoint feature but **no numeric SLA is published**.

**(h) Observability / developer surface.** OpenAI-compatible SDK, reasoning
controls, structured outputs, tool calling with `strict: true`, `llms.txt` docs
index.

**(i) Pricing and what it implies.** Published per 1M
[src](https://inference-docs.cerebras.ai/models/qwen-3.8-27b),
[src](https://inference-docs.cerebras.ai/models/openai-oss):

| Model | Speed | Input | Output | Context (free/paid) |
|---|---:|---:|---:|---|
| `gpt-oss-120b` | **~3,000 tok/s** | $0.35 | $0.75 | 65k / 131k |
| `qwen-3.8-27b` | **~1,850 tok/s** | **$0.99** | **$1.49** | 64k / 128k |

*(Inferred, and this is the most interesting price in the document)*: Cerebras
is the **only provider in the market whose output price is within 1.5× of its
input price**. Everyone on GPUs charges 4–12× more for output than input,
because on a GPU output is memory-bandwidth-bound and input is compute-bound and
batchable. Cerebras charges **1.5×**. That ratio *is* the architecture showing
through the P&L: with 21 PB/s of SRAM bandwidth, decode stops being the
expensive phase, and the pricing follows. Correspondingly its *input* price
($0.99/1M on a 27B dense) is **4–10× the GPU market's**, because prefill FLOPs
on a wafer are not cheap. See §4.3.

**(j) Published numbers.** Llama3.1-8B 1,800 tok/s and 70B 450 tok/s at launch
(2024-08-27); Llama 3.1-70B 2,100 tok/s (2024-10-24); gpt-oss-120b *"a
blistering 3,000 tokens/sec"*; GPT-5.6 Sol **up to 750 tok/s** across multiple
CS-3s with *"the same model weights and precision as the standard endpoint — no
distillation or quantization"* (2026-08-27); disaggregated 1,200 tok/s and 4.5×
P95 (2026-03-13). Cold start, uptime, utilisation: **not published**.

**(k) What transfers.** The **dual-bucket rate limit** (uncached tokens vs
total tokens) is a design any self-hoster should steal outright: it is the only
admission-control scheme in this document that prices the thing that actually
saturates — prefill — instead of the thing that is easy to count. **Automatic
128-token-block prefix caching with a guaranteed TTL and no client
breakpoints** is a better developer contract than the explicit-breakpoint model,
and vLLM/SGLang already implement the mechanism. The **BTA** idea (tile the
attention batch to keep utilisation up as sparsity rises) has a GPU analogue
worth testing on this repo's MoE models. And the **storage-only quantization**
stance is a useful, explicitly-stated counterposition to
[`quantization-formats.md`](../cross-cutting/quantization-formats.md)'s
compute-precision story: dequantize-on-the-fly, keep activations/attention/KV in
full precision, and you buy capacity without buying accuracy risk — at the cost
of the FLOPs speedup, which on a wafer you do not need.

*Does not transfer.* Everything downstream of 44 GB of SRAM at 21 PB/s. The
layer-boundary pipeline split across systems, in particular, is the *opposite*
of the right answer on GPUs, where pipeline bubbles at inference batch sizes are
punishing and NVLink makes TP cheap.

---

### 2.7 SambaNova

The least-documented of the three custom-silicon operators, but the one with a
peer-reviewed architecture paper.

**(a) What they sell.** SambaCloud per-token APIs with cached-input pricing on
some models; enterprise on-prem SN40L systems (not publicly priced).

**(b) Hardware.** **SN40L Reconfigurable Dataflow Unit (RDU)** — a streaming
dataflow architecture with a **three-tier memory system**: on-chip distributed
SRAM, on-package HBM, off-package DDR DRAM. Published in *Proceedings of MICRO
2024* (submitted 2024-05-13, revised 2024-11-05)
[src](https://arxiv.org/abs/2405.07518). The paper's framing is explicitly the
memory wall: modern accelerators have *"disproportionately high
compute-to-memory ratios"*.

**(c) Orchestration and isolation.** **Not published.**

**(d) Cold-start engineering.** This is SambaNova's distinctive claim and it is
a cold-start claim in disguise: an 8-socket RDU node achieves **15×–31× speedups
in model-switching time** versus conventional approaches
[src](https://arxiv.org/abs/2405.07518). The three-tier memory is what buys it —
DDR holds many models' weights, HBM stages them, SRAM runs them, so switching
between experts/models is a memory promotion rather than a reload from storage.
For a Composition-of-Experts deployment the paper claims **up to 19× reduction
in machine footprint**.

**(e) Autoscaling.** **Not published.**

**(f) Serving engine.** Proprietary dataflow compiler and runtime; **not
publicly documented** beyond the paper. Per-request optimizations (speculative
decoding, prefix caching, disaggregation) are **not published**, except that
prompt caching exists for exactly one model (below).

**(g) Routing, rate limits, SLAs.** **Not published.**

**(h) Observability.** OpenAI-compatible API.

**(i) Pricing and what it implies.** Per 1M
[src](https://cloud.sambanova.ai/plans/pricing): MiniMax-M2.7 **$0.06 cached /
$0.60 in / $2.40 out**; DeepSeek-V3.1 and V3.2 **$3.00 / $4.50, no cached
tier**; Gemma-4-31B-it $0.38/$1.15; gpt-oss-120b $0.22/$0.59;
Meta-Llama-3.3-70B $0.60/$1.20; MiniMax-M3 $0.60/$2.40. Via OpenRouter,
SambaNova's `gpt-oss-120b` endpoint is $0.14/$0.95.

*(Inferred)* **Only MiniMax-M2.7 supports cached input**; every other model
reads "N/A". Prompt caching is normally a property of the *engine*, not the
model — so a provider that offers it on exactly one model is most likely running
that model on a newer/different serving path. And the DeepSeek-V3.1/V3.2 price
($3.00/$4.50) is **10–20× the GPU market's price for the same model**, which
says SambaNova is not competing on DeepSeek-class cost at all.

**(j) Published numbers.** From the paper: **2–13×** speedups across benchmarks
on eight RDU sockets, **3.7×** over DGX H100, **6.6×** over DGX A100, 15–31×
model switching, up to 19× footprint reduction. SambaCloud publishes **no
tokens/second figures at all** — the docs give context windows only
[src](https://docs.sambanova.ai/cloud/docs/get-started/supported-models). For a
company whose peers lead with speed, that absence is itself a signal.
**⚠️ TO BE VERIFIED.**

**(k) What transfers.** The **three-tier memory hierarchy for fast model
switching** is the transferable idea, and it maps cleanly onto GPU hardware:
DDR/host-DRAM as a weight staging tier, HBM as the resident tier, with promotion
rather than reload. That is exactly the architecture behind Together's
*"fleet-wide model weight caching"* and Fireworks' *"LoRA adapters with caching…
new models can be added or taken down within seconds"* — same idea, different
substrate. If you host many models or many adapters on a fixed cluster, **keep a
host-DRAM weight tier and promote, never re-download**. Nothing else transfers;
the RDU's reconfigurable dataflow has no GPU analogue.

---

### 2.8 Perplexity

**Scope warning.** Perplexity's engineering blog (`perplexity.ai/hub/blog/*` and
`research.perplexity.ai`, which redirects there) returned **HTTP 403 behind a
Cloudflare interstitial to every request made on 2026-09-19**. The posts
referenced across the industry — on TRT-LLM adoption, H100 serving economics,
and multi-node DeepSeek deployment — could not be read. **This profile is built
from Perplexity's public GitHub only**, and every field below is narrower than
the truth.

**(a) What they sell.** A consumer/enterprise answer engine, plus the
Perplexity API (Sonar family). *(Inferred from the repos)* Perplexity is
primarily an *operator for its own product*, with the API as a secondary
surface — which is why its published work is systems infrastructure rather than
product docs.

**(b) Hardware.** From the repos: CUDA arch targets `9.0a+PTX` (H100-class and
newer) in `pplx-kernels`; **AWS EFA** named as a transport in `fabric-lib`
[src](https://github.com/perplexityai/pplx-garden). *(Inferred)* a substantial
H100/H200-class fleet on AWS and/or similar, given EFA support is a first-class
transport rather than an afterthought.

**(c) Orchestration and isolation.** **Not published.**

**(d) Cold-start engineering.** Not published for serving. Adjacent: `fabric-lib`
claims **"Weight Transfer for RL Post-Training in under 2 seconds"**
[src](https://github.com/perplexityai/pplx-garden) — an RL-rollout number, not
an inference cold start, but it bounds what RDMA weight movement can do.

**(e) Autoscaling.** **Not published.**

**(f) Serving engine and per-request optimizations.** The open repos are the
evidence:

- **`pplx-kernels`** — MoE all-to-all dispatch/combine kernels over **NVSHMEM**,
  with *"Flexible transportation layers: NVLink, IBGDA, IBRC, EFA"*, CUDA Graph
  support, and *"overlapping communication and computation"*. Benchmarks span
  **EP8–EP128**. Now carries a deprecation notice pointing at `pplx-garden`
  [src](https://github.com/perplexityai/pplx-kernels).
- **`pplx-garden`** — successor umbrella. `fabric-lib`: point-to-point RDMA
  communication for LLM systems, targeting *"trillion-parameter models on AWS
  EFA"*, with an **MLSys'26** paper reference. Also `lily` (Rust + Metal
  inference server for Qwen3.6-35B-A3B on Apple Silicon) and `pplx-unigram`
  (unigram tokenizer CPU performance). The stated operational priorities
  include **disaggregated prefill/decode**
  [src](https://github.com/perplexityai/pplx-garden).

*(Inferred)* An organisation that writes its own EP8–EP128 all-to-all kernels
over four transports, and then a general RDMA fabric library, is running **large
MoE models across many nodes with expert parallelism as the primary
parallelism** — the same shape as DeepSeek's published EP144 decode (§2.10).
That is an inference from the repos' contents, not a statement Perplexity has
made here.

**(g)–(i) Routing, observability, pricing.** **Not retrievable** on 2026-09-19.

**(j) Published numbers.** EP8–EP128 dispatch/combine benchmarks in-repo;
<2 s weight transfer. Serving throughput/latency/uptime: **not retrievable**.

**(k) What transfers.** `pplx-kernels` and `fabric-lib` are **permissively
published and directly usable** — they are the only production MoE all-to-all
kernels in this document that a self-hoster can actually run. For a cluster
serving this repo's DeepSeek-V4.1-Flash or Kimi-K3 at EP8 or above, the
transport abstraction alone (NVLink / IBGDA / IBRC / EFA behind one interface)
is worth reading before writing anything bespoke. See
[`serving-optimizations.md` §3](../cross-cutting/serving-optimizations.md#3-parallelism-for-large-moe).

---

### 2.9 OpenRouter

Owns no compute. Included because it is the market's price and quality
transparency layer, and because its routing semantics are a specification a
self-hoster can implement internally across their own pools.

**(a) What they sell.** A single OpenAI-compatible API over ~447 models and
several hundred provider endpoints, with routing policy exposed as request
parameters.

**(b) Hardware.** None.

**(c) Orchestration.** Not applicable.

**(d) Cold start.** Not applicable, and *(inferred)* this is part of the
product: routing to an already-warm provider is a cold-start avoidance
mechanism for the caller.

**(e) Autoscaling.** Not applicable.

**(f) "Serving engine" — the routing policy.** The default is the interesting
part: providers that *"have not seen significant outages in the last 30
seconds"* are load-balanced **weighted by inverse price squared** — a provider
at $1/1M gets **9× the traffic** of one at $3/1M, at equal uptime
[src](https://openrouter.ai/docs/features/provider-routing). Overrides:

| Parameter | Effect |
|---|---|
| `sort` = `price` / `throughput` / `latency` | Hard sort, no load balancing |
| `sort.by` + `sort.partition` (`model` \| `none`) | With model fallbacks, sort within each model or globally across all of them |
| `:nitro` / `:floor` suffixes | Shorthand: `:nitro` = throughput sort + priority service tiers; `:floor` = price sort + flex tiers |
| `preferred_min_throughput`, `preferred_max_latency` | Thresholds over a **rolling 5-minute window**, as a number or a `{p50,p75,p90,p99}` object. Preferred, not exclusive — they remain fallbacks |
| `order`, `only`, `ignore`, `allow_fallbacks` | Explicit provider control; base slug matches all regions, full slug pins one |
| `quantizations` | Filter to `int4, int8, fp4, fp6, fp8, fp16, bf16, fp32, unknown` |
| `max_price` | `{prompt, completion, request, image}` ceilings — over-price requests are **rejected, not upgraded** |
| `data_collection: "deny"`, `zdr: true`, `require_parameters`, `enforce_distillable_text` | Policy and capability filters |

**(g) Routing, rate limits, SLAs.** Uptime is tracked per endpoint and exposed
through the Endpoints API and dashboards (hourly over 3 days, 24-hour trend, and
"with routing" vs "without routing" availability)
[src](https://openrouter.ai/docs/features/uptime-optimization). No numeric SLA.
Fireworks' rebuttal — that proxy hops are additive to median TTFT while
improving p95 — is the fair statement of the trade
[src](https://fireworks.ai/blog/inference-providers-vs-api-routers).

**(h) Observability / developer surface.** The `/api/v1/models/{author}/{slug}/endpoints`
API returns, per endpoint: `provider_name`, `tag`, `pricing` (prompt,
completion, `input_cache_read`), `context_length`, **`quantization`**,
`max_completion_tokens`, `supported_parameters`, `status`, and
`uptime_last_5m / _30m / _1d`. That is the source for every third-party price
in §5.

**(i) Pricing and what it implies.** *"We pass through the pricing of the
underlying providers without any markup on inference pricing."* Revenue comes
from **5.5 % ($0.80 min) on Stripe credit purchases, 5 % on crypto**, and BYOK
at **5 % of usage above $25,000/month list price** (pay-as-you-go) or **above
$200,000/month** (enterprise). Prompts and completions are **not logged by
default**, *"even if an error occurs, unless you opt-in"*
[src](https://openrouter.ai/docs/faq).

**(j) Published numbers.** Per-endpoint uptime, live. On 2026-09-19 the
DeepSeek-V4.1-Flash endpoints ranged 99.5–100 % over 30 minutes with two
endpoints in a negative-status state; the Kimi-K3 endpoints ranged **82.11 %
(DeepInfra) to 100 %**. Latency and throughput fields were **null across every
endpoint queried** on that date — the API carries `latency_last_30m` and
`throughput_last_30m` fields but they were unpopulated. **⚠️ TO BE VERIFIED.**

**(k) What transfers.** Two things, both concrete. First, **inverse-price-squared
weighted load balancing gated on a 30-second outage window** is a good default
for any multi-pool router, substituting your own pools' marginal cost for price.
Second, and more useful: **the `quantizations` filter is a contract you should
impose internally**. A fleet that serves the same model at FP8 on some nodes and
NVFP4 on others, without the caller being able to say which, is silently varying
quality — exactly the failure OpenRouter's field exists to surface.

---

### 2.10 DeepSeek — first-party operator

The most detailed public disclosure of a production LLM serving system by any
operator, anywhere.

**(a) What they sell.** First-party per-token API with peak/off-peak pricing.

**(b) Hardware.** **H800 nodes, 8 GPUs each.** In the 24-hour window 2025-02-27
to 2025-02-28: **peak 278 nodes, average 226.75 nodes**
[src](https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md).

**(c) Orchestration.** Not published as such; the serving topology is:

**(f) Serving engine and per-request optimizations** — taken first, because it
is the substance:

| Element | Published detail |
|---|---|
| **Prefill** | **EP32 across 4 nodes**; each GPU hosts **9 routed experts + 1 shared expert**; **DP32** for MLA/shared-expert components |
| **Decode** | **EP144 across 18 nodes**; each GPU hosts **2 routed experts + 1 shared expert**; **DP144** |
| **Overlap** | Prefill uses a **dual-batch overlap**: two microbatches alternate so communication hides behind computation. Decode uses a **5-stage pipeline** subdividing attention layers for seamless comm/compute overlap |
| **Load balancing** | **Three distinct balancers.** (1) *Prefill*: balances core-attention compute and dispatch-send load across varying request counts. (2) *Decode*: equalises **KVCache usage** and request count per GPU. (3) *Expert-parallel*: minimises maximum dispatch-receive load |
| **Throughput** | **~73.7k tokens/s/node prefill**, **~14.8k tokens/s/node decode**; average output speed **20–22 tok/s** per user |
| **Volume** | 608B input tokens (**342B cached — 56 %**), 168B output tokens, in 24 h |

**(d) Cold start / (e) autoscaling.** Not published directly, but the node count
moving between 226.75 average and 278 peak in one day is **elastic
provisioning at node granularity**, and DeepSeek separately runs nighttime
discounting — *(inferred)* an explicit demand-shaping strategy to flatten that
curve rather than scale to it.

**(g)–(h) Routing, rate limits, observability.** Not published in this
document.

**(i) Pricing and what it implies.** This is the part every self-hoster should
read. Daily infrastructure cost **$87,072** at an assumed **$2/GPU-hour**;
theoretical daily revenue at R1 pricing **$562,027**; **theoretical profit
margin 545 %** — with the honest caveat that actual revenue is *"substantially
lower"* because V3 is cheaper, some access is free, and nights are discounted
[src](https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md).

Current first-party prices, as recorded in
[`cost-matrix.md` §6.1](../matrix/cost-matrix.md#61-published-vendor-prices):
DeepSeek-V4.1-Flash off-peak **$0.15 / $0.003 cached / $0.60**, peak
$0.30/$0.006/$1.20. Cached input is **2 % of uncached**, not the generic 10 %.

**(j) Published numbers.** All of the above.

**(k) What transfers.** More than anything else in this document, and it is the
reference point for [`serving-optimizations.md` §3](../cross-cutting/serving-optimizations.md#3-parallelism-for-large-moe):

- **Prefill and decode want completely different EP degrees** — EP32 vs EP144, a
  4.5× difference, on the same model. Any single-topology deployment is wrong
  for one of the two phases. This is the strongest published argument for PD
  disaggregation, and it is quantified.
- **Three load balancers, not one.** Decode balances *KV-cache occupancy*;
  prefill balances *attention compute and dispatch-send*; EP balances
  *dispatch-receive*. A single "least connections" balancer gets all three
  wrong.
- **56 % cache hit rate in production** (342B of 608B input tokens) is a real
  operational number for an agentic/chat mix, and it validates
  [`METHODOLOGY.md` §6](../METHODOLOGY.md#6-cost)'s blended assumption of half
  the input cached.
- **The margin corridor.** A 545 % theoretical margin at $2/GPU-hour means the
  *published API price is not near marginal cost*. When
  [`cost-matrix.md` §6.2](../matrix/cost-matrix.md#62-break-even-utilisation)
  says self-hosting DeepSeek-V4.1-Flash needs **92 % utilisation on B200** to
  match the API price, that is being measured against a price with an enormous
  margin baked in — the *physics* gap is far smaller than the *price* gap. What
  the API is really selling you is **someone else's utilisation risk**.

---

### 2.11 Moonshot AI (Kimi) — first-party operator

**(a) What they sell.** First-party Kimi API; Kimi-K3 is also resold widely
(twenty OpenRouter endpoints on 2026-09-19).

**(b) Hardware.** Not published in the paper beyond "GPU cluster". **⚠️ TO BE
VERIFIED.**

**(c)–(f) Architecture — Mooncake.** Moonshot's serving platform is published as
a paper (arXiv 2407.00079, submitted 2024-06-24, v4 2025-09-03) and is
production infrastructure, not research
[src](https://arxiv.org/abs/2407.00079):

- **Disaggregated prefill and decoding clusters.**
- **KVCache-centric design**: the distinguishing move is using *"the
  underutilized CPU, DRAM, and SSD resources of the GPU cluster to implement a
  disaggregated cache of KVCache"* — the KV cache becomes a first-class,
  cluster-wide, tiered store rather than per-GPU state.
- **KVCache-centric scheduler** balancing throughput against latency SLOs.
- **Prediction-based early rejection** for overload — the scheduler predicts
  and rejects early rather than accepting work it cannot finish within SLO.
- Measured: **up to 525 % throughput increase** in simulation under SLO, and in
  production *"Kimi handles 75 % more requests"*. Strongest on long context.

**(g)–(h) Routing, rate limits, observability.** Not published here.

**(i) Pricing.** Moonshot first-party Kimi-K3 **$3.00 / $0.30 cached / $15.00**
per 1M ([`cost-matrix.md` §6.1](../matrix/cost-matrix.md#61-published-vendor-prices),
and confirmed on OpenRouter's Moonshot AI endpoint, which also declares
**mxfp4** quantization
[src](https://openrouter.ai/api/v1/models/moonshotai/kimi-k3/endpoints)). A
`:batch` variant is listed at the same $3.00/$15.00.

*(Inferred)* Moonshot's own endpoint declaring **MXFP4** is a useful data point
for [`gpu-optimizations.md`](../matrix/gpu-optimizations.md)'s Kimi-K3 format
question: the model's author serves it at MXFP4, while third parties declare
fp4 (Relace, InferenceNet, Sail Research, Parasail), mxfp4 (Chutes, Modal),
fp8 (BaseTen) and **bf16** (DeepInfra) for the same model ID. That is a ~4×
spread in weight bytes behind one API name.

**(k) What transfers.** **Mooncake is the blueprint for tiered KV caching on a
cluster you already own**, and unlike everything in §2.1–§2.2 it is published in
full with an open implementation lineage. Two specifics: (1) *your idle CPU
DRAM and NVMe are a KV cache tier* — on a node with 2 TB of host RAM serving a
model with an 890 B–3.2 KB/token KV footprint (see
[`fit-matrix.md` §6.3](../matrix/fit-matrix.md)), that is an enormous amount of
recoverable prefill; (2) **predictive early rejection beats queueing** under
overload, because a request admitted and missed costs the same compute as one
served. Both are directly implementable.

---

### 2.12 Mistral — first-party operator

**(a) What they sell.** La Plateforme per-token APIs; self-deployment of
open-weight models; cloud-partner distribution.

**(b)–(f).** Mistral publishes **no inference-systems engineering** of the kind
Together, Fireworks, DeepSeek or Moonshot do. What it publishes instead is
**deployment guidance pointing at other people's engines**: the self-deployment
docs cover **vLLM** (with `tokenizer_mode="mistral"`, `load_format="mistral"`,
`config_format="mistral"`, vLLM ≥0.6.1.post1), **TensorRT**, SkyPilot,
Cerebrium, Cloudflare Workers AI and TGI
[src](https://docs.mistral.ai/deployment/self-deployment/vllm/). The docs
navigation also lists a **Priority Tier** and **Regional inference** under
Deployment → Cloud; the priority-tier page URL tried returned 404 and its terms
could not be read. **⚠️ TO BE VERIFIED.**

*(Inferred)* Mistral's strategic position is as a **model vendor whose serving
is deliberately commoditised** — it documents how to run its models on vLLM
rather than claiming a proprietary engine advantage. For a self-hoster that is
the friendliest possible posture, and it is the reason Mistral models have no
"which quantization is this endpoint actually running" problem: the reference
path is the open one.

**(g)–(j).** Not published / not retrievable.

**(k) What transfers.** The `tokenizer_mode`/`load_format`/`config_format`
trio is a real operational detail — Mistral checkpoints served through the
generic HF path tokenize differently, which is a silent quality bug. Beyond
that, Mistral is a reminder that **not every model vendor's serving advantage is
real**; some models are best run on the open engines this repo already
evaluates in [`inference-engines.md`](../cross-cutting/inference-engines.md).

---

### 2.13 Anthropic, OpenAI, Google — what the frontier labs publish about serving

None of these three publishes its serving architecture. What they publish is
**the commercial interface to their scheduler**, which is informative in a
different way: batch discounts, priority tiers and provisioned throughput are
each a price put on a scheduling decision, and reading them tells you what they
believe their scarce resource is.

#### Anthropic

**Message Batches API** — *"most batches finishing in less than 1 hour while
reducing costs by 50 % and increasing throughput"*; asynchronous, poll for
status, results retrieved when all requests end
[src](https://platform.claude.com/docs/en/docs/build-with-claude/batch-processing).

**Service tiers** — three: Priority, Standard, Batch
[src](https://platform.claude.com/docs/en/api/service-tiers). The mechanics are
unusually explicit:

- A Priority Tier commitment is **a number of input tokens per minute, a number
  of output tokens per minute, a duration (1, 3, 6 or 12 months), and a specific
  model version**. It *"targets 99.5 % uptime with prioritized computational
  resources"*. Over-commitment traffic **falls back to standard automatically**.
- **Burndown rates**, which are the interesting part — capacity is consumed at:
  cache reads **0.1 tokens/token**, cache writes **1.25** (5-minute TTL) or
  **2.00** (1-hour TTL), US-only inference **1.1** on Claude 4.6+, everything
  else 1.0. *"These burndown rates reflect the relative pricing of each token
  type."*
- `service_tier` is a request parameter (`auto` | `standard_only`); the response
  `usage` object reports which tier served it; headers expose
  `anthropic-priority-{input,output}-tokens-{limit,remaining,reset}`.
- **Priority Tier capacity commitments are no longer available for purchase**
  as of this doc's state on 2026-09-19; existing commitments run to contract
  end. Priority is supported on all models *except* Claude Fable 5.1, Mythos
  5.1, Mythos 5, Mythos Preview, Opus 5 and Sonnet 5.

*(Inferred)* A cache **write** costing 1.25–2.0× a normal input token, while a
cache **read** costs 0.1×, is a published statement that **writing a KV cache
entry is more expensive than computing the tokens normally** — the write is the
prefill *plus* the persistence. Any self-hoster modelling prefix caching should
carry a write cost, not just a read discount; this repo's
[`METHODOLOGY.md` §6](../METHODOLOGY.md#6-cost) 10 %-of-uncached read rule has no
write-side term. The 1-hour TTL costing 1.6× the 5-minute TTL prices **cache
residency**, which is a real bandwidth/capacity cost on any tiered KV store.

#### OpenAI

**Batch API** — **50 % discount**, single completion window `24h`, *"and often
more quickly"*; **50,000 requests/batch**, **200 MB file**, **2,000 batches per
hour**, model-specific queued-token maxima, and — the operationally important
bit — **a separate rate-limit pool** that does not draw down synchronous quotas
[src](https://developers.openai.com/api/docs/guides/batch).

**Flex processing** — *"priced at Batch API rates, with additional discounts
from prompt caching"*, in exchange for *"slower response times and occasional
resource unavailability"*; default 10-minute request timeout should be raised;
can return **429 Resource Unavailable** with no charge. In beta with limited
model availability; exact multipliers and the model list are **not in the docs**
[src](https://developers.openai.com/api/docs/guides/flex-processing).
**⚠️ TO BE VERIFIED.**

*(Inferred)* Flex is batch-priced but synchronous, which means it is sold as
**preemptible interactive capacity** — the 429-with-no-charge is the preemption.
That is the API-level expression of a spot market in scheduler slots.

#### Google

**Vertex AI Provisioned Throughput** exists and is documented as a product
family (overview, supported models, *Calculate Provisioned Throughput
requirements*, purchase, use). The unit of sale is the **GSU (Generative AI
Scale Unit)**, and throughput is quantified per model in tokens/second per GSU
with a burndown that differs by modality, with spillover to pay-as-you-go. **The
numeric tables — GSU throughput per model, minimum GSU per model, commitment
durations, burndown rates — could not be retrieved on 2026-09-19**; both the
overview and purchase pages fetched as navigation shells.
**⚠️ TO BE VERIFIED**
[src](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/provisioned-throughput/overview).

#### What transfers from all three

**The 50 % batch discount is unanimous.** Anthropic, OpenAI, Groq, Fireworks and
Together all land on exactly 50 %, with Novita's "50 % off both directions"
arithmetically identical. Five independent operators converging on the same
number is a strong signal about **what deferrable work is worth**: roughly half.
For a self-hoster the corollary is direct — if you can shift half your token
volume into a long-window queue, you need roughly half the peak capacity, and
that is worth about the same 50 %. Groq's willingness to go to a **7-day** window
suggests the discount could go further if the deferral does.

**Priority tiers are the inverse trade**, and Anthropic's is the more honest
construction: a *reserved* ITPM/OTPM commitment with a duration and a 99.5 %
target, priced per token-type by burndown. Fireworks' flat 1.25× (§2.2) is the
*scheduler-weight* version of the same product. Knowing which one you are buying
matters: only the first survives a genuine capacity crunch.

---

## 3. Comparison table

All figures as published on **2026-09-19**. "n/p" = not published.

| | **Together** | **Fireworks** | **DeepInfra** | **Novita** | **Groq** | **Cerebras** | **SambaNova** | **Perplexity** | **OpenRouter** | **DeepSeek** | **Moonshot** |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **(a) Sells** | tokens + dedicated $/GPU-h + containers + clusters | tokens (3 tiers) + on-demand $/GPU-s + enterprise | tokens + dedicated $/GPU-h + GPU rental | tokens + on-demand GPUs | tokens only | tokens + dedicated endpoints | tokens + on-prem systems | own product + API | routing only | tokens (own model) | tokens (own model) |
| **(b) Silicon** | A100→H100→H200→B200→GB200; **owned** | A100/H100/H200/B200/B300 + MI325X/MI350X; ownership n/p | A100/H100/H200/B200/B300 | H200 + Blackwell | LPU (SRAM, ~80 TB/s) **→ NVIDIA Groq 3 LPX + Vera Rubin NVL72, 2026-08** | WSE-3 (44 GB SRAM, 21 PB/s) → WSE-3T/CS-4 (53.5 PB/s) | SN40L RDU, 3-tier SRAM/HBM/DDR | H100+ class, EFA (inferred) | none | H800 ×8/node, 227–278 nodes | n/p |
| **(c) Isolation** | queue-class isolation; orchestrator n/p | replica-based; n/p | "private deployments"; n/p | n/p | n/p | shared vs dedicated instance | n/p | n/p | n/a | n/a | n/a |
| **(d) Cold start** | **86 s (9B) / 145 s (18B FT) / 2–14 min deploy / 4× warm-start** | n/p; **503 not queued** at zero | n/p | n/p | n/a (SRAM-resident) | n/a (SRAM-resident) | **15–31× faster model switch** (3-tier) | <2 s weight transfer (RL) | n/a | n/p | n/p |
| **(e) Autoscale** | 8 signals; default `inflight_requests`=8; up 60 s / down 300 s; **no auto-wake from zero** | up 30 s / down 10 min / zero 1 h; 5 signals; auto-delete 7 d | exists, minute billing; params n/p | n/p | n/p | n/p (dual-bucket rate limits) | n/p | n/p | n/a | node-granular, 227→278 in 24 h | n/p |
| **(f) Engine** | own (TIE 2.0) + TKC + ATLAS + CPD | own (FireAttention V4) + FireOptimizer | n/p; multi-precision endpoints | **vLLM/SGLang + own kernels** | Groq Compiler, static schedule | own; layer-split pipeline across wafers | own dataflow compiler | own MoE a2a kernels (open) | n/a | own; EP32/EP144 | Mooncake |
| **(g) SLA** | **99 %/99.9 % contractual** | n/p | n/p | n/p | n/p | n/p (dedicated only) | n/p | n/p | n/p | n/p | n/p |
| **(h) Batch** | 50 %, 24 h, 30B tok queued | 50 %, ≤24 h, no request cap | n/p | **50 % both directions** | **50 %, 24 h–7 d** | n/p | n/p | n/a | passthrough | n/p | `:batch` at same price |
| **(i) DS-V4.1-Flash $/1M** | $0.30/$1.20 | $0.30/$1.20 (Std) | **$0.14/$0.42** | $0.285/$1.14 | — | — | — | — | passthrough | **$0.15/$0.60** (off-peak) | — |
| **(i) Qwen3.8-27B $/1M** | — | — | $0.15/$1.875 | $0.42/$3.00 | **$0.80/$4.00** @450 t/s | **$0.99/$1.49** @1,850 t/s | — | — | passthrough | — | — |
| **(j) Best published speed** | 500 TPS DS-V3.1 (ATLAS, B200, bs=1) | >250 t/s DS-V3 (8×B200 NVLink) | n/p | 2.55× spec-decode | 1,000 t/s (gpt-oss-20b) | **3,000 t/s (gpt-oss-120b)** | n/p (none published) | n/p | n/a | **73.7k prefill / 14.8k decode tok/s/node** | n/p |
| **(k) Most transferable** | queue-depth autoscaling; cache-aware PD; custom speculators from 20M tokens | **KLD quantization ladder**; 76 %/29 % acceptance; KV-stationary kernels | price floor for "buy vs build" | **vLLM-shipped MoE kernels + DSpark window sweep** | long-window batch as capacity smoothing | **dual-bucket (uncached vs total) rate limiting**; auto 128-block prefix cache | 3-tier weight staging | **open MoE all-to-all kernels** | inverse-price² balancing; quantization contract | **EP32≠EP144; three load balancers; 56 % hit rate** | **KV tiering into host DRAM/SSD; predictive early rejection** |

---

## 4. What the custom-silicon players change about the scaling problem

This repo's entire cost model rests on one inequality, from
[`METHODOLOGY.md` §4](../METHODOLOGY.md#4-throughput-and-latency-roofline):

```
decode_step_time ≈ max( bytes_per_step / (HBM_BW × MBU),
                        2 × active_params × batch / (peak_FLOPS × MFU) )
```

On every GPU in [`gpus/`](../gpus/), the left term wins at the batch sizes that
matter, and `bytes_per_step` is dominated by weights plus KV. Groq and Cerebras
attack the denominator of the left term by four orders of magnitude. That
changes three things, and leaves one thing unchanged.

### 4.1 The KV-cache HBM ceiling stops being the binding constraint — and becomes a different one

[`fit-matrix.md` §2](../matrix/fit-matrix.md) computes max concurrency for every
pair as `kv_budget / kv_bytes_per_token / context`, and the answers are brutal:
Kimi-K3 on H200 at 128K context falls to **12 concurrent requests** after the
2026-09-19 replicated-KV resolution; DeepSeek-V4.1-Flash on H100 to **9**. The
whole interactive column of [`cost-matrix.md` §2](../matrix/cost-matrix.md#2-interactive--1m-output-tokens-at-tpot--50-ms-s1-4k-in--512-out)
is shaped by it.

On a wafer, **44 GB of SRAM at 21 PB/s** replaces **141–288 GB of HBM at
4.8–8 TB/s**. The KV cache is no longer competing with weights for a
bandwidth-starved pool; it is competing for a *capacity*-starved one that is
three orders of magnitude faster. So:

- The **per-token KV footprint stops setting the concurrency ceiling** in the
  way it does on GPUs, because the ceiling is now total resident capacity across
  a fleet of wafers, allocated at provisioning time by the compiler — not a
  runtime pool you can trade against batch size.
- But the ceiling **becomes static**. A GPU operator can re-tune
  `--gpu-memory-utilization`, switch KV dtype from BF16 to FP8 to NVFP4 (890 B
  vs 1,650 B vs 3,200 B per token for DeepSeek-V4.1-Flash — see
  [`fit-matrix.md` §6.3](../matrix/fit-matrix.md)) and buy 3.6× the concurrency
  in an afternoon. A wafer operator cannot: Cerebras states outright that
  *"activations, attention, and kv cache remain in full precision and
  unquantized"*. **The KV-quantization lever this repo spends four sections on
  does not exist on the custom-silicon side.**
- The multi-wafer split is **pipeline-parallel at layer boundaries**, with TP and
  expert communication kept *inside* a wafer. That is the reverse of GPU
  practice. It works because inter-wafer traffic is activations only — small —
  whereas GPU pipeline parallelism at inference batch sizes produces bubbles.

### 4.2 Batch-size behaviour inverts

The single most important difference, and the one that decides when these
platforms are a good deal.

On a GPU, decode is memory-bandwidth-bound, so **larger batches are nearly free
in time and directly proportional in throughput** — this is why
[`cost-matrix.md` §3](../matrix/cost-matrix.md#3-max-throughput--1m-output-tokens-s4-4k-in--512-out)'s
S4 operating points sit at concurrency 256–3,327 and cost 2–10× less per token
than the S1 points at concurrency 8–128. Batching is the entire GPU cost story.

On Groq and Cerebras the weights are already resident at enormous bandwidth, so
**there is far less bandwidth-bound slack for batching to absorb.** Three
consequences follow, and all three are visible in published behaviour rather
than asserted here:

1. **Their headline numbers are per-user, not aggregate.** Cerebras' CS-5 target
   is *"up to 10,000 output tokens per second **per user**"*; Groq's table is
   "SPEED (T/SEC)" per stream. Nobody in §2.5–§2.7 publishes an aggregate
   tokens/s/chip figure, and that omission is consistent across all three
   vendors. Compare [`cost-matrix.md`](../matrix/cost-matrix.md)'s operating
   points, which are always `GPUs × concurrency · aggregate tok/s/GPU · TPOT`.
   **⚠️ The two number families are not comparable and must never be put in one
   column.**
2. **Cerebras bought Trainium for prefill.** The 2026-03-13 disaggregation
   announcement pairs *"compute-optimized"* AWS Trainium for prefill with CS-3
   for decode. A company with 21 PB/s of memory bandwidth outsourcing the
   *compute-bound* phase is the clearest available evidence that wafer-scale is a
   decode-side architecture whose prefill economics are poor.
3. **Cerebras needed BTA to keep MoE utilisation up.** Without batch tiling,
   throughput degraded **53 % with more experts and 86 % at lower sparsity**
   [src](https://www.cerebras.ai/blog/moe-guide-scale). Sparse models
   *underutilise* a wafer in a way they do not underutilise a bandwidth-bound
   GPU — the opposite of the GPU intuition, where MoE sparsity is what makes
   large models affordable.

### 4.3 The price ratio tells you the architecture

Set the output/input price ratio side by side. This is the cleanest published
fingerprint of where each architecture's cost lives:

| Provider | Model | Input $/1M | Output $/1M | **out ÷ in** |
|---|---|---:|---:|---:|
| **Cerebras** | qwen-3.8-27b | $0.99 | $1.49 | **1.5×** |
| **Cerebras** | gpt-oss-120b | $0.35 | $0.75 | **2.1×** |
| SambaNova | gpt-oss-120b | $0.22 | $0.59 | 2.7× |
| Groq | gpt-oss-120b | $0.15 | $0.60 | 4.0× |
| Groq | qwen/qwen3.8-27b | $0.80 | $4.00 | **5.0×** |
| DeepInfra | qwen3.8-27b | $0.15 | $1.875 | 12.5× |
| Together / Fireworks | DeepSeek-V4.1-Flash | $0.30 | $1.20 | 4.0× |
| DeepSeek (first-party) | DeepSeek-V4.1-Flash | $0.15 | $0.60 | 4.0× |
| Qwen Cloud list | Qwen3.8-27B | $0.50 | $3.00 | 6.0× |

Cerebras at **1.5×** is the outlier and it is not an accident: on a wafer,
decode is cheap and prefill is not, so the ratio compresses. Groq at **5.0×** on
the same 27B model sits in the GPU range — *(inferred)* consistent with Groq
pricing to the market on a model where its architecture does not give it a cost
edge, and/or with the fleet transition described in §2.5.

And note the **absolute** input prices: Cerebras charges **$0.99/1M input on a
27B dense** where DeepInfra charges **$0.15** and Darkbloom **$0.10** — a 6.6–10×
premium on prefill. That is the cost of the architecture, paid in the phase it
is worst at.

### 4.4 Cost per token vs this repo's GPU numbers

Against [`cost-matrix.md` §4](../matrix/cost-matrix.md#4-blended--1m-tokens-75--input-half-of-it-cached-at-10--25--output)'s
blended self-hosting figure for **Qwen3.8-27B — $0.0602–$0.1221 on B300, best
cell in the matrix** — the custom-silicon prices are:

| Provider | Blended (75/25, half input cached) | Self-hosting must be busy… |
|---|---:|---:|
| Cerebras `qwen-3.8-27b` | **$1.1150** | **5.4 % – 11.0 %** |
| Groq `qwen/qwen3.8-27b` | **$1.6000** | **3.8 % – 7.6 %** |
| Cheapest GPU endpoint (Darkbloom, fp4) | $0.5250 | 11.5 % – 23.3 % |
| Qwen Cloud list ([§6.1](../matrix/cost-matrix.md#61-published-vendor-prices)) | $0.9563 | 6.3 % – 12.8 % |

(Break-even `u* = self-hosted blended ÷ provider blended`, the same identity as
[`cost-matrix.md` §6.2](../matrix/cost-matrix.md#62-break-even-utilisation);
Groq and Cerebras publish no cached-input rate, so their blends use the full
input price — which makes them *look better* than a cached-rate blend would.)

**A rented B300 that is busy 8 % of the time beats Groq on this model.** The
custom-silicon platforms are not competing on cost per token for a 27B dense
model, and their own pricing says so. What they sell is **1,850–3,000 tokens per
second to a single user**, which no configuration in
[`cost-matrix.md`](../matrix/cost-matrix.md) reaches at any price — the fastest
Qwen3.8-27B S1 operating point in the whole matrix is GB300 at **28.7 ms TPOT**,
i.e. ~35 tok/s per stream. That is a **50× per-stream gap**, and it is the
entire product.

### 4.5 The thing that does not change

**Prefill.** Every architecture in this document, silicon included, ends up
disaggregating prefill from decode and giving prefill different, compute-heavier
hardware or a different topology: DeepSeek EP32-vs-EP144, Moonshot's separate
prefill cluster, Together's three-tier CPD, Fireworks' disaggregation as a
FireOptimizer axis, Cerebras buying Trainium. **Prefill/decode disaggregation is
the one architectural conclusion that every serious operator in this study
reached independently**, on GPUs, on wafers and on dataflow units alike. If a
bare-metal cluster adopts exactly one idea from this document, it should be that
one — see [`serving-optimizations.md` §3](../cross-cutting/serving-optimizations.md#3-parallelism-for-large-moe).

---

## 5. Pricing signals — the market on 2026-09-19

Self-hosting figures are quoted **by name** from
[`cost-matrix.md` §4](../matrix/cost-matrix.md#4-blended--1m-tokens-75--input-half-of-it-cached-at-10--25--output);
nothing here re-derives one. Provider blends use
[`METHODOLOGY.md` §6](../METHODOLOGY.md#6-cost)'s mix — **75 % input, half of it
cached at the provider's own published cached rate, 25 % output** — and
break-even is `u* = self-hosted blended ÷ provider blended`, exactly as in
[`cost-matrix.md` §6.2](../matrix/cost-matrix.md#62-break-even-utilisation).

**Validation.** Reproducing the repo's own §6.2 cells with this generator gives
DeepSeek-V4.1-Flash on B200 = **91.6 %** (matrix prints 92 %) and Qwen3.8-27B on
B300 = **6.3 % / 12.8 %** (matrix prints 6 % / 13 %). The arithmetic below is
the same arithmetic.

Third-party prices are from
[`openrouter.ai/api/v1/models/{slug}/endpoints`](https://openrouter.ai/docs/features/provider-routing),
fetched 2026-09-19; first-party prices are from each vendor's own page.

### 5.1 DeepSeek-V4.1-Flash-class MoE — 23 endpoints

Self-hosted blended, best cell: **base checkpoint B200 $0.190–$0.443**;
**NVFP4 checkpoint B200 $0.1485–$0.3464**
([`cost-matrix.md` §4](../matrix/cost-matrix.md#4-blended--1m-tokens-75--input-half-of-it-cached-at-10--25--output)).

| Provider | in $/1M | out $/1M | cached in | **blended** | u* base (low–high) | u* NVFP4 (low–high) | declared quant |
|---|---:|---:|---:|---:|---:|---:|---|
| **DeepInfra** | 0.140 | 0.420 | 0.0042 | **0.1591** | 119 % ✗ / 279 % ✗ | **93 %** / 218 % ✗ | fp8 |
| Relace | 0.130 | 0.520 | 0.0026 | 0.1797 | 106 % ✗ / 247 % ✗ | **83 %** / 193 % ✗ | fp4 |
| Morph | 0.135 | 0.540 | 0.0041 | 0.1871 | 102 % ✗ / 237 % ✗ | **79 %** / 185 % ✗ | unknown |
| **DeepSeek (first-party)** | 0.150 | 0.600 | 0.0030 | **0.2074** | **92 %** / 214 % ✗ | **72 %** / 167 % ✗ | unknown |
| Alibaba | 0.150 | 0.600 | 0.0150 | 0.2119 | **90 %** / 209 % ✗ | **70 %** / 164 % ✗ | unknown |
| **Wafer** | 0.200 | 0.600 | 0.0060 | 0.2273 | **84 %** / 195 % ✗ | **65 %** / 152 % ✗ | unknown |
| Sail Research | 0.200 | 0.600 | 0.0400 | 0.2400 | **79 %** / 185 % ✗ | **62 %** / 144 % ✗ | fp4 |
| Fireworks *(OR row)* | 0.220 | 0.660 | 0.0070 | 0.2501 | **76 %** / 177 % ✗ | **59 %** / 139 % ✗ | unknown |
| StreamLake | 0.282 | 1.128 | 0.0056 | 0.3899 | **49 %** / 114 % ✗ | **38 %** / **89 %** | fp8 |
| GMICloud | 0.285 | 1.140 | 0.0057 | 0.3940 | **48 %** / 112 % ✗ | **38 %** / **88 %** | fp8 |
| **Novita** | 0.285 | 1.140 | 0.0057 | 0.3940 | **48 %** / 112 % ✗ | **38 %** / **88 %** | fp8 |
| **Fireworks** *(own docs, Standard)* | 0.300 | 1.200 | 0.0060 | 0.4147 | **46 %** / 107 % ✗ | **36 %** / **84 %** | — |
| **Together** | 0.300 | 1.200 | 0.0060 | 0.4147 | **46 %** / 107 % ✗ | **36 %** / **84 %** | unknown |
| Makora / DigitalOcean / SiliconFlow / Parasail | 0.300 | 1.200 | 0.0060 | 0.4147 | **46 %** / 107 % ✗ | **36 %** / **84 %** | unknown / fp8 |
| **Modal** | 0.300 | 1.200 | 0.0300 | 0.4237 | **45 %** / 105 % ✗ | **35 %** / **82 %** | unknown |
| AtlasCloud / BaseTen | 0.300 | 1.200 | 0.0300 | 0.4237 | **45 %** / 105 % ✗ | **35 %** / **82 %** | fp8 |
| Phala | 0.345 | 1.380 | 0.0069 | 0.4770 | **40 %** / **93 %** | **31 %** / **73 %** | unknown |
| Fireworks *(Priority)* / Venice | 0.375 | 1.500 | 0.0075 | 0.5184 | **37 %** / **85 %** | **29 %** / **67 %** | — / fp8 |

`✗` = above 100 %, i.e. self-hosting cannot match that price at any utilisation.
"low" = cheapest reputable on-demand B200 ($6.00 Hyperstack); "high" = cheapest
hyperscaler B200 ($14.00 OCI) — [`cloud-pricing.md` §5.14](../cross-cutting/cloud-pricing.md).

**What it says.**

1. **The floor moved below the model author.** DeepInfra at $0.14/$0.42 undercuts
   DeepSeek's own off-peak $0.15/$0.60 by **23 % blended**. Against that floor,
   the base checkpoint on B200 at the cheap tier needs **119 % utilisation — it
   cannot win at all**, and even the NVFP4 checkpoint needs **93 %**.
2. **Self-hosting the base checkpoint beats essentially nobody in the cheap half
   of the market.** Of 23 endpoints, the base checkpoint clears 100 % against 20
   of them only on the *low* price tier, and on the *high* (hyperscaler) tier it
   clears **three**. This agrees with
   [`cost-matrix.md` §6.2](../matrix/cost-matrix.md#62-break-even-utilisation)'s
   verdict — one cell of sixteen — and extends it: the market is now *cheaper*
   than the single vendor that §6.2 measured against.
3. **The 3.7× spread across identical model IDs is the real finding.**
   $0.1591 to $0.5184 blended, same `deepseek/deepseek-v4.1-flash` slug. The
   declared quantizations behind that spread run **fp4 → fp8 → unknown**, and
   twelve of 23 endpoints declare `unknown`. A self-hoster's quality baseline is
   not comparable to "the API" — it is comparable to *a specific endpoint at a
   specific precision*, and half the market will not tell you which.
4. **⚠️ Fireworks appears twice at incompatible prices** ($0.22/$0.66 via
   OpenRouter vs $0.30/$1.20 in its own docs). Both rows are printed. This is
   unresolved.

### 5.2 Qwen3.8-27B dense — 19 endpoints, including both custom-silicon vendors

Self-hosted blended, best cell: **B300 $0.0602–$0.1221**; best interactive cell
H200 $0.159/1M output
([`cost-matrix.md` §2](../matrix/cost-matrix.md#2-interactive--1m-output-tokens-at-tpot--50-ms-s1-4k-in--512-out), §4).

| Provider | in $/1M | out $/1M | cached in | **blended** | u* (low–high) | quant | notes |
|---|---:|---:|---:|---:|---:|---|---|
| Darkbloom | 0.100 | 1.800 | — | **0.5250** | **11.5 % / 23.3 %** | fp4 | cheapest blended |
| **DeepInfra** | 0.150 | 1.875 | 0.0375 | 0.5391 | 11.2 % / 22.7 % | **bf16** | undercuts FP8 rivals at BF16 |
| Phala | 0.199 | 2.075 | 0.0415 | 0.6090 | 9.9 % / 20.0 % | unknown | |
| Chutes | 0.240 | 2.200 | 0.0240 | 0.6490 | 9.3 % / 18.8 % | fp8 | |
| Parasail | 0.240 | 2.200 | 0.0500 | 0.6588 | 9.1 % / 18.5 % | fp8 | |
| AkashML | 0.250 | 2.200 | 0.0500 | 0.6625 | 9.1 % / 18.4 % | fp8 | |
| DekaLLM | 0.200 | 2.500 | 0.0500 | 0.7188 | 8.4 % / 17.0 % | unknown | |
| Reka | 0.214 | 2.550 | 0.1500 | 0.7740 | 7.8 % / 15.8 % | fp8 | |
| Mancer 2 | 0.200 | 2.500 | — | 0.7750 | 7.8 % / 15.8 % | fp8 | 100 % uptime |
| Ionstream | 0.280 | 2.550 | 0.1000 | 0.7800 | 7.7 % / 15.7 % | fp8 | |
| Alibaba *(via OR)* | 0.425 | 2.550 | 0.0850 | 0.8287 | 7.3 % / 14.7 % | unknown | 1M ctx |
| Io Net | 0.300 | 2.800 | 0.1800 | 0.8800 | 6.8 % / 13.9 % | fp8 | 64K ctx only |
| Novita | 0.420 | 3.000 | 0.0850 | 0.9394 | 6.4 % / 13.0 % | unknown | 1M ctx |
| CoreWeave | 0.400 | 3.000 | 0.1500 | 0.9563 | 6.3 % / 12.8 % | fp8 | |
| **Qwen Cloud list** ([§6.1](../matrix/cost-matrix.md#61-published-vendor-prices)) | 0.500 | 3.000 | 0.0500 | **0.9563** | **6.3 % / 12.8 %** | — | the repo's existing denominator |
| Cloudflare | 0.450 | 3.200 | 0.0500 | 0.9875 | 6.1 % / 12.4 % | unknown | 91.07 % 30-min uptime |
| **Cerebras** | 0.990 | 1.490 | n/p | **1.1150** | **5.4 % / 11.0 %** | fp16 declared | **~1,850 tok/s** |
| Venice | 0.450 | 3.200 | — | 1.1375 | 5.3 % / 10.7 % | fp8 | |
| **Groq** | 0.800 | 4.000 | n/p | **1.6000** | **3.8 % / 7.6 %** | n/p | **~450 tok/s**, preview |

"low" = B300 at $7.40 (Hyperstack), "high" = $15.00 (OCI).

**What it says.**

1. **Self-hosting wins this model against every endpoint in the market, by a
   wide margin.** Worst case is **23.3 %** utilisation (vs the cheapest fp4
   endpoint, at hyperscaler B300 prices); best case **3.8 %**. That confirms and
   strengthens [`cost-matrix.md` §6.2](../matrix/cost-matrix.md#62-break-even-utilisation)'s
   "self-hosting wins everywhere, 6–28 % break-even" — the 2026-09-19 market has
   a *cheaper* floor than Qwen Cloud list (Darkbloom $0.525 vs $0.956) and
   self-hosting still wins by 4×.
2. **A dense 27B is the shape self-hosting is built for.** One GPU, no expert
   parallelism, no MoE all-to-all, no exotic KV layout. Everything in this
   document that is hard — EP144, three load balancers, KVCache-centric
   scheduling, wafer partitioning — is machinery for models that do not fit on
   one card. For a model that does, the machinery is pure overhead, and the
   provider has to charge for it.
3. **The precision spread is 4× and the price does not track it.** DeepInfra
   serves **bf16** at $0.5391 blended while Reka and Ionstream serve **fp8** at
   $0.774–$0.78 — i.e. the *higher-precision* endpoint is **31 % cheaper**. Any
   "the API is cheaper than self-hosting at quality X" argument has to name the
   endpoint.

### 5.3 Cross-check: gpt-oss-120b, where all three custom-silicon vendors compete

Not a repo model, but the only slug served by Groq, Cerebras, SambaNova *and*
twenty GPU providers — so it isolates the silicon variable at fixed model
[src](https://openrouter.ai/api/v1/models/openai/gpt-oss-120b/endpoints):

| Provider | in | out | out÷in | Notes |
|---|---:|---:|---:|---|
| AkashML / CoreWeave / DekaLLM | $0.030 | $0.17–0.18 | 5.7–6.0× | bf16 / fp4 |
| DeepInfra `bf16` | $0.037 | $0.17 | 4.6× | cheapest overall |
| Crusoe / Novita | $0.050 | $0.25 | 5.0× | |
| **SambaNova** | $0.140 | $0.95 | 6.8× | |
| **Groq** | $0.150 | $0.60 | 4.0× | **500 tok/s** |
| Together / Nebius / Bedrock / SiliconFlow / Phala | $0.150 | $0.60 | 4.0× | market consensus price |
| **Cerebras** `fp16` | $0.350 | $0.75 | **2.1×** | **~3,000 tok/s** |

Groq prices **identically to Together, Nebius and Bedrock** while delivering
~500 tok/s per stream; Cerebras charges **2.3× more on input, 1.25× on output**
and delivers ~6× the per-stream speed. **Cerebras' 2.1× out/in ratio persists
across both its models**, which is the strongest evidence that the compressed
ratio in §4.3 is architectural and not a one-model promotion. Meanwhile the
cheapest GPU endpoint is **$0.037/$0.17 — a 9.5× input spread within one slug**.

### 5.4 Kimi-K3 — 20 endpoints, for the format question

Self-hosted blended, best cell: **B300 $2.3808–$4.8259**
([`cost-matrix.md` §4](../matrix/cost-matrix.md#4-blended--1m-tokens-75--input-half-of-it-cached-at-10--25--output)).
Cheapest endpoint **Relace $1.70/$8.50 (fp4)**; Moonshot first-party
**$3.00/$0.30/$15.00 (mxfp4)**; most expensive **Morph $6.00/$22.50**. Declared
quantizations across the twenty: **fp4** (Relace, InferenceNet, Sail Research,
Parasail), **mxfp4** (Chutes, Modal, Moonshot AI), **fp8** (BaseTen), **bf16**
(DeepInfra), **unknown** (eleven)
[src](https://openrouter.ai/api/v1/models/moonshotai/kimi-k3/endpoints).

Against Moonshot's own $4.99 blended, B300 at $2.38 needs **48 %** and at $4.83
needs **97 %** — reproducing
[`cost-matrix.md` §6.2](../matrix/cost-matrix.md#62-break-even-utilisation)
exactly. Against the *cheapest* endpoint (Relace, blended **$2.8262**), B300
needs **84 % / 171 % ✗** — i.e. **the cheap tier of the market has already
closed most of the gap that §6.2 says B300 self-hosting opens**, and on
hyperscaler pricing it has closed it entirely.

### 5.5 Summary — what utilisation self-hosting must hit

| This repo's model | Best self-hosted blended | vs cheapest endpoint | vs first-party/list | Verdict |
|---|---|---|---|---|
| DeepSeek-V4.1-Flash (base) | B200 $0.190–$0.443 | **119 % / 279 % ✗** (DeepInfra $0.1591) | 92 % / 214 % ✗ (DeepSeek $0.2074) | **Buy tokens.** Cannot win against the market floor at any utilisation. |
| DeepSeek-V4.1-Flash-NVFP4 | B200 $0.1485–$0.3464 | **93 % / 218 % ✗** | 72 % / 167 % ✗ | **Marginal.** Wins only on cheap-tier B200 at near-full utilisation, at the matrix's least defensible operating point (concurrency 3,557, [§9.2](../matrix/cost-matrix.md#92-cells-that-are-priced-but-cannot-be-bought-today)). |
| **Qwen3.8-27B** | **B300 $0.0602–$0.1221** | **11.5 % / 23.3 %** | 6.3 % / 12.8 % | **Self-host.** Wins against all 19 endpoints including both custom-silicon vendors. |
| Kimi-K3 | B300 $2.3808–$4.8259 | **84 % / 171 % ✗** (Relace $2.8262) | 48 % / 97 % (Moonshot $4.99) | **Buy tokens** unless you need the weights on your own metal. |
| Marlin-2B | B300 $0.0092–$0.0185 | no endpoint exists | — | **Self-host** — no market. |

The pattern is consistent and worth stating plainly: **self-hosting wins
decisively on the model that fits on one GPU and loses on every model that does
not.** For the large MoE models, what you are buying from a provider is not
cheaper silicon — §2.10's 545 % theoretical margin proves the price is not near
cost — it is **aggregated utilisation** across thousands of tenants, which is
the one input a single-tenant cluster cannot manufacture.

---

## Open questions

1. **Is Groq still serving on LPUs?** The 2025-12-24 NVIDIA licensing agreement
   moved the founder and *"team members"* to NVIDIA, and by 2026-08-24 Groq was
   announcing deployment of NVIDIA Groq 3 LPX + Vera Rubin NVL72. Nothing
   published says what fraction of GroqCloud traffic is served on LPUs today, or
   whether the LPU roadmap continues. Every "custom silicon" statement about
   Groq in §4 is therefore about the architecture as designed, not necessarily
   as currently deployed. **⚠️ TO BE VERIFIED.**
   [§2.5](#25-groq)
2. **Fireworks' DeepSeek-V4.1-Flash price is published twice at
   incompatible values** — $0.22/$0.66 via OpenRouter, $0.30/$1.20 in its own
   serverless pricing docs. Either OpenRouter routes to a different deployment
   or one table is stale. Both are printed in §5.1; neither is preferred.
3. **Perplexity's entire serving story is unread.** Its blog is behind a
   Cloudflare interstitial that returns 403 to every user-agent tried on
   2026-09-19. §2.8 is built from GitHub only, and its claims about hardware and
   parallelism are labelled *(inferred)* for that reason. A single successful
   fetch would likely close several fields.
4. **Google's Vertex Provisioned Throughput GSU tables could not be
   retrieved** — the product exists and is documented structurally, but
   tokens/second per GSU, minimum GSU per model, burndown rates and commitment
   durations all fetched as empty navigation shells. §2.13's Google entry is a
   mechanism description with no numbers.
5. **Neither Groq nor Cerebras publishes an aggregate throughput figure per
   chip or per system** — only per-user token rates. Without one, §4.4's cost
   comparison can only be made price-to-price, never
   $/token-at-equal-utilisation, and the question of whether wafer-scale is
   actually cheaper per token *at full load* is unanswerable from public data.
6. **`quantization: "unknown"` covers roughly half the market.** Twelve of 23
   DeepSeek-V4.1-Flash endpoints and eleven of 20 Kimi-K3 endpoints declare
   unknown precision. The field is provider-declared and unverified, so even the
   declared values are claims. Any price comparison in §5 is a comparison at
   *unknown quality* for those rows.
7. **DeepInfra serves `qwen3.8-27b` declared BF16 at a price below most FP8
   competitors** and `kimi-k3` declared BF16 at $2.85/$14.25. Either the
   declarations are wrong, or DeepInfra's hardware economics are materially
   better than the rest of the market's. Its published dedicated rates (H100
   $2.20/h vs this repo's $3.20 `low`) point at the latter but do not prove it.
8. **Every speculative-decoding number in this document is at batch size 1.**
   Together's ATLAS (4×B200, bs=1), Novita's DSpark (TP8, bs=1), Fireworks'
   acceptance-rate examples. This repo's cost grid operates at concurrency
   64–3,327. The interaction of speculation with large batch is exactly what
   [`README.md` open question 6](../README.md#6-top-14-open-questions-across-the-tree)
   flags as unmeasured, and no provider in this study has published it — except
   Together's 2024 research post, which argues the *opposite* of the usual
   intuition (speculation keeps paying at large batch once KV makes decode
   memory-bound again) on 8×A100 and 32K context only.
9. **No provider publishes fleet utilisation.** Every §(i) inference about what
   a price implies rests on comparing published price to this repo's cost model,
   which is a bound, not a measurement. DeepSeek's 545 % theoretical margin is
   the single closest thing to a disclosure and it is explicitly labelled
   theoretical by its authors.
10. **Cold-start times are published by exactly one provider.** Together's
    86 s / 145 s / 2.5 min figures are the only measured numbers in this class.
    Whether they generalise — to other engines, other model sizes, other storage
    paths — is untested, and §12a's serverless platforms report cold starts in a
    completely different regime (seconds, via snapshotting) that may or may not
    be reachable for multi-GPU LLM replicas.
11. **Groq's TruePoint Numerics has no public technical page.** It is referenced
    on model pages as the quantization approach but `console.groq.com/docs/truepoint`
    and `groq.com/blog/truepoint*` both 404. Its precision, which tensors it
    covers, and its quality methodology are unknown.
12. **Cerebras' multi-LoRA mechanism is unpublished.** On a platform whose whole
    premise is SRAM residency, how adapters are held and switched — and what
    they cost in SRAM — is the interesting question, and the announcement does
    not address it.
13. **OpenRouter's `latency_last_30m` and `throughput_last_30m` fields were
    null for every endpoint queried on 2026-09-19.** The API carries them and
    the docs describe threshold routing over a rolling 5-minute window built on
    them, so the data exists somewhere; it did not come back through the public
    endpoint. That is the one missing piece that would let §5's price tables
    carry a measured speed column.

---

## Sources

Every URL below was fetched on **2026-09-19** unless the retrieval failed, in
which case the failure is recorded.

**Together AI**
- [Together Inference Engine 2.0](https://www.together.ai/blog/together-inference-engine-2) — 2024-07-18
- [Speculative decoding for high-throughput long-context inference](https://www.together.ai/blog/speculative-decoding-for-high-throughput-long-context-inference) — 2024-09-05
- [NVIDIA HGX B200 with Together Kernel Collection](https://www.together.ai/blog/nvidia-hgx-b200-with-together-kernel-collection) — 2025-02-13
- [On-demand dedicated endpoints](https://www.together.ai/blog/on-demand-dedicated-endpoints) — 2025-03-13
- [Customized speculative decoding](https://www.together.ai/blog/customized-speculative-decoding) — 2025-05-12
- [Fastest inference for DeepSeek-R1-0528 with NVIDIA HGX B200](https://www.together.ai/blog/fastest-inference-for-deepseek-r1-0528-with-nvidia-hgx-b200) — 2025-07-17
- [ATLAS: adaptive-learning speculator system](https://www.together.ai/blog/adaptive-learning-speculator-system-atlas) — 2025-10-10
- [Optimizing inference speed and costs](https://www.together.ai/blog/optimizing-inference-speed-and-costs) — 2026-01-22
- [Dedicated container inference](https://www.together.ai/blog/dedicated-container-inference) — 2026-02-12
- [Cache-aware disaggregated inference (CPD)](https://www.together.ai/blog/cache-aware-disaggregated-inference) — 2026-03-04
- [Inside the Together AI kernels team](https://www.together.ai/blog/inside-the-together-ai-kernels-team) — 2026-04-01
- [Serving DeepSeek-V4: why million-token context is an inference systems problem](https://www.together.ai/blog/serving-deepseek-v4-why-million-token-context-is-an-inference-systems-problem) — 2026-05-11
- [99.9 % uptime for inference](https://www.together.ai/blog/99-9-uptime-for-inference) — 2026-07-16
- [The production platform for open-weight AI inference](https://www.together.ai/blog/the-production-platform-for-open-weight-ai-inference) — 2026-07-23
- [Autoscaling endpoints for LLM inference](https://www.together.ai/blog/autoscaling-endpoints-for-llm-inference) — 2026-07-31
- [Accelerate inference for large-scale workloads](https://www.together.ai/blog/accelerate-inference-large-scale-workloads) — modified 2026-09-10
- [Fastest inference for the top open-source models](https://www.together.ai/blog/fastest-inference-for-the-top-open-source-models)
- [Distribution-aware speculative decoding](https://www.together.ai/blog/distribution-aware-speculative-decoding) — 2026-04-24
- [20 exaflops GPU clusters](https://www.together.ai/blog/20-exaflops-gpu-clusters) — 2023-11-13
- [Pricing](https://www.together.ai/pricing) · [Batch inference docs](https://docs.together.ai/docs/batch-inference)

**Fireworks AI**
- [Fireworks quantization](https://fireworks.ai/blog/fireworks-quantization) — 2024-08-01
- [FireOptimizer](https://fireworks.ai/blog/fireoptimizer) — 2024-08-30
- [Multi-LoRA](https://fireworks.ai/blog/multi-lora) — 2024-09-18
- [FireAttention V4: FP4 on B200](https://fireworks.ai/blog/fireattention-v4-fp4-b200) — 2025-05-28
- [3D FireOptimizer](https://fireworks.ai/blog/3d-fireoptimizer) — 2025-06-14
- [Batch API](https://fireworks.ai/blog/batch-api) — 2025-07-31
- [Fireworks–AMD AI infrastructure partnership](https://fireworks.ai/blog/fireworks-amd-ai-infrastructure-partnership) — 2025-10-20
- [Blazing fast inference on top OSS models](https://fireworks.ai/blog/blazing-fast-inference-on-top-oss-models) — 2026-01-27
- [Inference providers vs API routers](https://fireworks.ai/blog/inference-providers-vs-api-routers) — 2026-03-06
- [Kernel optimization for MiniMax M3 on NVIDIA Blackwell](https://fireworks.ai/blog/kernel-optimization-for-minimax-m3-on-nvidia-blackwell) — 2026-07-10
- [Why GPUs on demand](https://fireworks.ai/blog/why-gpus-on-demand) — 2024-06-03
- [Pricing](https://fireworks.ai/pricing) · [Serverless pricing](https://docs.fireworks.ai/serverless/pricing) · [On-demand deployments](https://docs.fireworks.ai/guides/ondemand-deployments) · [Autoscaling](https://docs.fireworks.ai/deployments/autoscaling)

**DeepInfra / Novita**
- [DeepInfra pricing](https://deepinfra.com/pricing) · [DeepInfra docs](https://docs.deepinfra.com/)
- [Novita pricing](https://novita.ai/pricing) · [Novita blog](https://novita.ai/blog)
- [Chord W4A16 INT4 MoE kernel](https://novita.ai/blog/novita-chord-w4a16-moe/) — 2026-09-15
- [Kimi DSpark speculative decoding in vLLM](https://novita.ai/blog/kimi-k2-dspark-speculative-decoding-throughput/) — 2026-07-10
- [Optimizing GLM4-MoE for production with SGLang](https://novita.ai/blog/optimizing-glm4-moe-for-production/) — 2026-01-21

**Groq**
- [The Groq LPU explained](https://groq.com/blog/the-groq-lpu-explained) — ⚠️ undated
- [Why AI requires a new chip architecture](https://groq.com/blog/why-ai-requires-a-new-chip-architecture) — 2019-10-22
- [From speed to scale: how Groq is optimized for MoE and other large models](https://groq.com/blog/from-speed-to-scale-how-groq-is-optimized-for-moe-other-large-models)
- [Batch processing with GroqCloud](https://groq.com/blog/batch-processing-with-groqcloud-for-ai-inference-workloads) — 2025-03-13
- [GroqCloud expanding to meet demand](https://groq.com/blog/groqcloud-expanding-to-meet-demand)
- [Groq and NVIDIA enter non-exclusive inference technology licensing agreement](https://groq.com/newsroom/groq-and-nvidia-enter-non-exclusive-inference-technology-licensing-agreement-to-accelerate-ai-inference-at-global-scale) — 2025-12-24
- [Groq among the first to bring NVIDIA Groq 3 LPX and Vera Rubin NVL72 to market](https://groq.com/blog/groq-among-the-first-to-bring-nvidia-groq-3-lpx-and-vera-rubin-nvl72-to-market) — 2026-08-24
- [Supported models (prices, speeds, rate limits)](https://console.groq.com/docs/models) · [Batch docs](https://console.groq.com/docs/batch) · [Qwen3.8-27B model page](https://console.groq.com/docs/model/qwen/qwen3.8-27b)
- ❌ `groq.com/pricing` renders client-side; no prices retrievable. ❌ `console.groq.com/docs/truepoint` 404. ❌ ISCA-2020 TSP PDF 404 at both `wow.groq.com` and `groq.com`.

**Cerebras**
- [Introducing Cerebras Inference: AI at instant speed](https://www.cerebras.ai/blog/introducing-cerebras-inference-ai-at-instant-speed) — 2024-08-27
- [Cerebras inference 3× faster](https://www.cerebras.ai/blog/cerebras-inference-3x-faster) — 2024-10-24
- [MoE guide: scale](https://www.cerebras.ai/blog/moe-guide-scale) — 2025-09
- [Disaggregated inference (with AWS Trainium)](https://www.cerebras.ai/blog/disaggregated-inference) — 2026-03-13
- [Ultrafast frontier inference: Hot Chips 2026 deep dive](https://www.cerebras.ai/blog/ultrafast-frontier-inference-cerebras-deep-dive-at-hot-chips-2026) — 2026-08-25
- [How Cerebras serves GPT-5.6 Sol at up to 750 tokens/second](https://www.cerebras.ai/blog/how-cerebras-serves-gpt-5-6-sol-at-up-to-750-tokens-per-second) — 2026-08-27
- [Introducing multi-LoRA on Cerebras Inference](https://www.cerebras.ai/blog/introducing-multi-lora-on-cerebras-inference)
- [Model catalog](https://inference-docs.cerebras.ai/models/overview) · [Qwen 3.8 27B](https://inference-docs.cerebras.ai/models/qwen-3.8-27b) · [GPT OSS](https://inference-docs.cerebras.ai/models/openai-oss) · [Rate limits](https://inference-docs.cerebras.ai/support/rate-limits) · [Prompt caching](https://inference-docs.cerebras.ai/capabilities/prompt-caching) · [Dedicated endpoints](https://inference-docs.cerebras.ai/dedicated/overview) · [Pricing page](https://www.cerebras.ai/pricing)

**SambaNova**
- [SN40L: Scaling the AI memory wall with dataflow and composition of experts](https://arxiv.org/abs/2405.07518) — MICRO 2024
- [SambaCloud pricing](https://cloud.sambanova.ai/plans/pricing) · [Supported models](https://docs.sambanova.ai/cloud/docs/get-started/supported-models)

**Perplexity**
- [pplx-kernels](https://github.com/perplexityai/pplx-kernels) · [pplx-garden](https://github.com/perplexityai/pplx-garden)
- ❌ `perplexity.ai/hub/blog/*` and `research.perplexity.ai/*` — HTTP 403 (Cloudflare) to every user-agent tried, 2026-09-19.

**OpenRouter**
- [Provider routing](https://openrouter.ai/docs/features/provider-routing) · [Uptime optimization](https://openrouter.ai/docs/features/uptime-optimization) · [FAQ (fees, logging)](https://openrouter.ai/docs/faq)
- Endpoint price/quant/uptime data, fetched 2026-09-19:
  [deepseek-v4.1-flash](https://openrouter.ai/api/v1/models/deepseek/deepseek-v4.1-flash/endpoints) ·
  [qwen3.8-27b](https://openrouter.ai/api/v1/models/qwen/qwen3.8-27b/endpoints) ·
  [kimi-k3](https://openrouter.ai/api/v1/models/moonshotai/kimi-k3/endpoints) ·
  [gpt-oss-120b](https://openrouter.ai/api/v1/models/openai/gpt-oss-120b/endpoints) ·
  [models list](https://openrouter.ai/api/v1/models)
- [Provider page: Wafer](https://openrouter.ai/provider/wafer)

**First-party model operators**
- [DeepSeek V3/R1 inference system overview](https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md) — 2025-03
- [Mooncake: a KVCache-centric disaggregated architecture for LLM serving](https://arxiv.org/abs/2407.00079) — v1 2024-06-24, v4 2025-09-03
- [Mistral self-deployment: vLLM](https://docs.mistral.ai/deployment/self-deployment/vllm/)
- [Anthropic: batch processing](https://platform.claude.com/docs/en/docs/build-with-claude/batch-processing) · [Anthropic: service tiers](https://platform.claude.com/docs/en/api/service-tiers)
- [OpenAI: Batch API](https://developers.openai.com/api/docs/guides/batch) · [OpenAI: flex processing](https://developers.openai.com/api/docs/guides/flex-processing)
- [Google Vertex AI: Provisioned Throughput overview](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/provisioned-throughput/overview) — ⚠️ numeric tables not retrievable

**Repo cross-references (not external sources)**
- [`METHODOLOGY.md`](../METHODOLOGY.md) §3 fit, §4 roofline, §6 cost
- [`matrix/cost-matrix.md`](../matrix/cost-matrix.md) §1–§4, §6.1, §6.2, §9.2
- [`matrix/fit-matrix.md`](../matrix/fit-matrix.md) §2, §6.1, §6.3
- [`matrix/gpu-optimizations.md`](../matrix/gpu-optimizations.md)
- [`cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md) §1–§5
- [`cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md)
- [`cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md)
- [`cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) §5.14
- [`README.md`](../README.md) §6 open questions 5, 6
