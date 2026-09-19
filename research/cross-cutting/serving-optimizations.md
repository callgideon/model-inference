# Serving optimizations: caching, speculative decoding, parallelism, disaggregation

**Research date: 2026-09-19.** Follows [`research/METHODOLOGY.md`](../METHODOLOGY.md) —
every number carries an inline source link or the marker **⚠️ TO BE VERIFIED** with
the estimation method stated. `est.` = derived from the METHODOLOGY formulas,
`meas.` = published measurement. Dense vs sparse TFLOPS separated; SXM vs PCIe
separated; marketing claims labelled as such.

This document covers *inference-time* optimizations — things you turn on in the
serving engine, not things you change in the checkpoint. Weight quantization and
attention-kernel selection live in the per-GPU and per-model documents; what is
here is caching, speculation, parallelism, disaggregation, batching, long context
and multimodal cost.

Repo models referenced throughout:

| Short name | Repo | Shape | Notes |
|---|---|---|---|
| DS-V4.1-Flash | `deepseek-ai/DeepSeek-V4.1-Flash` | 552B MoE backbone + 196B Engram, 8B active/prompt token, 16B/output token; **510.29 GB** checkpoint, routed experts **MXFP4** | Causal encoder-decoder, CSA2 sparse attention, DSpark drafter, 1M ctx [src](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) |
| DS-V4.1-Flash-NVFP4 | `nvidia/DeepSeek-V4.1-Flash-NVFP4` | same, experts NVFP4 g16; **527.27 GB — larger than the base** | `moe_quant_algo: NVFP4`, `ignore: ["*.attn.*","*.ffn.shared_experts.*","head","mtp.*"]` (repo `research/models/deepseek41fnvfp4/config.json`); accuracy-neutral, **no published speedup vs base** ⚠️ |
| Kimi-K3 | `moonshotai/Kimi-K3` | 2.8T MoE, 16-of-896 experts, 69 KDA + 24 MLA layers, MXFP4; **1,560.9 GB** checkpoint | 1M ctx, native vision [src](https://vllm.ai/blog/2026-07-27-k3) |
| Qwen3.8-27B | `Qwen/Qwen3.8-27B` | 27B dense hybrid, 16 full-attn of 64 layers, in-checkpoint MTP head; **55.56 GB** BF16 | 262K native ctx, YaRN to 1M [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) |
| Marlin-2B | `NemoStation/Marlin-2B` | 2B video-VLM, fine-tune of Qwen3.5-2B; **5.444 GB** BF16 | gated; `pipeline_tag: video-text-to-text` [src](https://huggingface.co/api/models/NemoStation/Marlin-2B) |

Checkpoint totals are the pinned METHODOLOGY §8 values (`index.json` `total_size`),
not vendor round figures. Per-(model, GPU) fit, throughput and cost tables live in
`research/models/<exp>/<gpu>.md`, not here.

---

## 1. Prefix and KV caching

### 1.1 What each engine implements

| Engine | Mechanism | Granularity | Default | Flag |
|---|---|---|---|---|
| vLLM | Automatic Prefix Caching (APC): hash table per KV page + LRU eviction | page (block) | on for most models; **off for Kimi-K3** | `--enable-prefix-caching` [src](https://vllm.ai/blog/2026-07-27-k3) |
| vLLM (hybrid models) | fine-grained prefix hits that may end *inside* a physical block, + KDA/SWA state snapshots at chosen boundaries | sub-block | on where supported | `VLLM_PREFIX_CACHE_RETENTION_INTERVAL` [src](https://vllm.ai/blog/2026-07-27-k3) |
| SGLang | RadixAttention: radix tree over token prefixes, LRU | token-prefix | on by default; hit rate exposed at `/metrics` | — [src](https://www.lmsys.org/blog/2024-01-17-sglang/) |
| SGLang (K3) | radix tree + copy-on-write / snapshot / donate for mutable KDA state | checkpointed prefix | on | `--enable-unified-memory` for the unified pool [src](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/) |
| TensorRT-LLM | KV cache block reuse, with `cache_salt` to isolate reuse between tenants | block | opt-in | `enable_block_reuse` [src](https://github.com/NVIDIA/TensorRT-LLM/issues/14918) |

The vLLM APC implementation "maintain[s] a hash table for each KV cache page,
augmented with custom eviction rules based on an LRU policy"
[src](https://packet.ai/blog/vllm-prefix-caching). SGLang's advantage is that the
radix tree matches at token granularity, so a shared prefix that does not land on
a block boundary still hits.

### 1.2 Measured hit-rate effects on TTFT and throughput

| Workload | Engine / HW | Effect | Source |
|---|---|---|---|
| Shared 512-token prefix, c=50 | SGLang vs vLLM | TTFT p50 −37 %, p95 −41 % | [src](https://www.spheron.network/blog/vllm-vs-sglang-2026/) |
| Prefix-heavy (RAG, multi-turn) | SGLang RadixAttention on H100 | up to **6.4×**; 29 % throughput edge over vLLM (16,200 vs 12,500 tok/s) — vendor-blog claim, workload not fully specified | [src](https://www.spheron.network/blog/vllm-vs-sglang-2026/) |
| Chat with 2K+ shared system prompt | vLLM APC | TTFT −60–80 % | [src](https://packet.ai/blog/vllm-prefix-caching) |
| Generic benchmark, ~50 % hit rate | vLLM APC | TTFT 78.9 s → 66.5 s (−15.8 %) | [src](https://packet.ai/blog/vllm-prefix-caching) |
| Dynamic-Sonnet, quarter-length shared prefix, 1×A100-SXM-80G, Llama-3.1-8B BF16, 256 req, max batch 128 | TRT-LLM v0.15.0 | throughput +34.7 %, TPOT +20.9 % | [src](https://blog.squeezebits.com/vllm-vs-tensorrtllm-12-automatic-prefix-caching-38189) |
| same | vLLM v0.6.3 | throughput +13.3 %, TPOT +9.8 % | [src](https://blog.squeezebits.com/vllm-vs-tensorrtllm-12-automatic-prefix-caching-38189) |
| Prefix ratio swept 0.1→0.9, 1K tokens, c=128 | TRT-LLM / vLLM | +49 % / +32 % throughput | [src](https://blog.squeezebits.com/vllm-vs-tensorrtllm-12-automatic-prefix-caching-38189) |
| **Random prompts, no shared prefix** (the cost of leaving it on) | vLLM v0.6.3, A100 | throughput **−36.7 %**, TPOT **+25.0 %** | [src](https://blog.squeezebits.com/vllm-vs-tensorrtllm-12-automatic-prefix-caching-38189) |
| same | TRT-LLM v0.15.0 | "relatively minor" overhead | [src](https://blog.squeezebits.com/vllm-vs-tensorrtllm-12-automatic-prefix-caching-38189) |
| KV cache reuse generally | TRT-LLM / NIM | NVIDIA claims **up to 5×** TTFT — Llama-70B on H100, enterprise-chatbot system-prompt burst, *"up to"* | [src](https://developer.nvidia.com/blog/5x-faster-time-to-first-token-with-nvidia-tensorrt-llm-kv-cache-early-reuse/) — **marketing claim**. The ~2×-typical figure previously cited here is **⚠️ TO BE VERIFIED**: it is not in [the block-reuse post](https://developer.nvidia.com/blog/introducing-new-kv-cache-reuse-optimizations-in-nvidia-tensorrt-llm/), whose only quantified claim is +20 % cache hit rate from priority-based eviction |

⚠️ The A100/vLLM-0.6.3 overhead figure is from 2024-era software. Whether current
vLLM still pays 36.7 % on zero-overlap traffic is **⚠️ TO BE VERIFIED** — no
2026-software measurement of the no-overlap case was found. Plan for a
*non-zero* cost and measure on your own traffic before leaving APC on for a
workload with no prefix structure.

**First-principles TTFT model** (METHODOLOGY §4). With prefix hit rate `h`, the
prefill work is over `(1−h)·T` tokens, so

```
TTFT(h) ≈ TTFT(0) · (1−h) + KV_load_time(h·T) + queueing
```

`est.` scaling for DS-V4.1-Flash, 8B active per prompt token:

| hit rate h | prefill FLOPs at T=128K | fraction of TTFT(0) |
|---|---|---|
| 0.00 | 2.097 PFLOP | 1.00 |
| 0.50 | 1.049 PFLOP | 0.50 |
| 0.90 | 0.210 PFLOP | 0.10 |
| 0.95 | 0.105 PFLOP | 0.05 |

(`2 × 8e9 × 131072 = 2.097e15` — arithmetic run in `python3`, active-param count
from [the model card](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash).)
The residual term is the KV *load*, which for GPU-resident cache is a pointer
swap and for offloaded cache is a real transfer — §1.4.

### 1.3 Caching when the state is not append-only

This is the 2026 problem. Attention KV is immutable once written, so a radix
tree over it is trivially safe. Recurrent state (KDA in Kimi-K3, Gated-DeltaNet
in Qwen3.8, sliding-window in DS-V4.x) is **overwritten in place at every
token**, so prefix caching has to manage mutable state.

**SGLang's answer** — three explicit state moves between the radix tree and a
request's working slot: *copy-on-write* (restore a shared checkpoint into a
private slot before the forward mutates it), *snapshot* (capture the advanced
state), *donate* (transfer the snapshot to the tree). Race-freedom comes from
stream ordering plus a ping-pong extra buffer; donate copies no state, only a
slot index [src](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/).

**Checkpoint placement decides the hit rate.** A recurrent state cannot run
backwards; it must be replayed forward from an earlier checkpoint. SGLang takes
checkpoints only at aligned radix nodes — chunk boundaries during prefill, a
fixed token interval during decode — keeps a sparse set under a per-path cap with
LRU, and (following Marconi, MLSys '25) prioritises branching points
[src](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/).

**vLLM's answer** — two complementary retention policies
[src](https://vllm.ai/blog/2026-07-27-k3):

| Policy | Rule | Control | Introduced |
|---|---|---|---|
| Interval-based | checkpoint every N tokens **plus always at prompt ends** | `VLLM_PREFIX_CACHE_RETENTION_INTERVAL` (0 = prompt-ends only) | PR #43447 (DeepSeek V4 + hybrid SWA), PR #45845 (K3) |
| Marconi-style selective | cache on the **second** hit — first sighting is evidence the prefix exists, second that it is shared | automatic | PR #37898, PR #47782 (K3) |

Setting the interval to 0 and keeping only prompt-end states is the
recommendation for multi-turn conversational traffic, because "the next turn
usually begins by replaying the previous turn's prompt"
[src](https://vllm.ai/blog/2026-07-27-k3).

**Measured**: internal KDA prefix checkpoints cut TTFT by **9–25 %** on Kimi-K3,
8K/1K workload on a B300 node under TP8
[src](https://vllm.ai/blog/2026-09-13-kimi-k3-performance-optimization).

### 1.4 KV offload to CPU / NVMe / remote

The tier hierarchy, with the four independent implementations:

| System | Tiers | Notable design point |
|---|---|---|
| vLLM native connector | GPU → pinned host | `--kv_offloading_backend native --kv_offloading_size <GB>` [src](https://vllm.ai/blog/2026-01-08-kv-offloading-connector) |
| vLLM tiered (`main`, targeted at 0.30 — **unreleased**: latest is **0.29.0, 2026-09-09**, [inference-engines.md §2.1](inference-engines.md)) | GPU → host (LRU/ARC) → filesystem / object store / peer RDMA | host-centric: *all* data flows through host memory; canonical layout is config-independent so KV is shareable across nodes with different TP or attention backends [src](https://vllm.ai/blog/2026-09-10-tiered-kv-offloading) |
| LMCache | GPU → CPU → NVMe → remote | the enterprise KV layer; used in GKE Inference [src](https://arxiv.org/pdf/2510.09665) |
| NVIDIA Dynamo KVBM | G1 GPU → G2 host → G3 local NVMe → G4 object store | NIXL descriptors expose file offsets for zero-copy I/O, optional GDS; disk-offload filtering (frequency ≥ 2) on by default to spare SSD endurance [src](https://docs.nvidia.com/dynamo/v1.3.0/user-guides/kv-cache-offloading) |
| Mooncake Store | distributed cross-instance KV pool over RDMA | solves the *cross-instance miss* when the router lands the next turn on another replica [src](https://vllm.ai/blog/2026-05-06-mooncake-store) |

Measured effects:

| Measurement | Setup | Result | Source |
|---|---|---|---|
| GPU↔CPU DMA bandwidth | H100 80GB + Xeon Sapphire Rapids, 500 GB DRAM, 2 MB blocks | **83.4 GB/s** bidirectional (DMA), 68.5 GB/s (custom CUDA kernels); ~50 GB/s single-direction | [src](https://vllm.ai/blog/2026-01-08-kv-offloading-connector) |
| TTFT from CPU cache hit | Llama-3.1-8B, H100 | **2×–22×** lower TTFT depending on prompt size | [src](https://vllm.ai/blog/2026-01-08-kv-offloading-connector) |
| Throughput under concurrency | same | up to **9×** | [src](https://vllm.ai/blog/2026-01-08-kv-offloading-connector) |
| vLLM 0.11.0 → 0.12.0 | same | 4× lower TTFT, 5× throughput, from moving physical block size from KB to 0.5–2 MB | [src](https://vllm.ai/blog/2026-01-08-kv-offloading-connector) |
| Storage tier at high session count | Qwen3.6-35B-A3B, 2×H100 TP2, 12K prompt + 8 rounds × 4K | parity to ~64 conversations; beyond 128 conversations storage tier **more than doubles** throughput | [src](https://vllm.ai/blog/2026-09-10-tiered-kv-offloading) |
| NVMe/disk tier, 128K system prompt | H100 (VAST) | TTFT ~11 s → ~1.5 s | [src](https://www.vastdata.com/blog/nvidia-dynamo-vast-scalable-optimized-inference) |
| Agentic multi-turn, AMD | MI300X, 32 users / 100K ctx | LMCache: TTFT avg −3.0×, p95 −2.1×, max −2.6×, 2.3× more requests vs HBM-only | [src](https://blog.lmcache.ai/en/2026/05/12/benchmarking-lmcache-for-multi-turn-agentic-workloads-on-amd-mi300x/) |
| MoE architecture rework | LMCache | TTFT ~13× lower on average (0.29 s vs 3.98 s), p99 >10× better | [src](https://blog.lmcache.ai/en/2026/04/03/lmcaches-new-architecture-boosts-moe-inference-performance-by-10x/) |
| Cross-instance pool | Kimi-2.5 NVFP4, 12×GB200, 1P1D, real Codex traces | hit rate **1.7 % → 92.2 %**; throughput **3.8×**; TTFT p50 **46×** lower; e2e latency **8.6×** lower | [src](https://vllm.ai/blog/2026-05-06-mooncake-store) |
| Scaling that pool | 12→60 GB200, round-robin routing | hit rate stays >95 %, near-linear scaling | [src](https://vllm.ai/blog/2026-05-06-mooncake-store) |

**The rule that falls out of this table**: the single biggest cache win in 2026
is not a bigger local cache, it is *making the cache visible across replicas*.
Going from 1.7 % to 92.2 % hit rate on Codex traces is a routing/storage change,
not a memory change.

**Reconciling tiers.** vLLM may find a local GPU hit with a partial tail, then a
*longer* prefix in Mooncake. The scheduler compares exact reusable token lengths
from both tiers and takes the longer, releasing the block reserved for the
shorter local tail — built entirely on the existing KV Connector API so every
connector gets it [src](https://vllm.ai/blog/2026-07-27-k3).

### 1.5 Cross-request cache economics — what vendors charge for cached input

All prices USD per 1M tokens, verified 2026-09-19.

| Vendor / model | Input (miss) | Cached input (hit) | Output | hit discount | Source |
|---|---|---|---|---|---|
| DeepSeek-V4-Flash | **$0.14** | **$0.0030** | $0.60 | **46.7×** | [src](https://deepseek.ai/pricing) |
| DeepSeek-V4-Pro | $0.660 | **$0.0220** | $1.98 | 30× | [src](https://deepseek.ai/pricing) |
| Kimi K3 | $3.00 | **$0.30** | $15.00 | 10× | [src](https://platform.kimi.ai/docs/pricing/chat-k3) |
| Kimi K2.7-Code | $0.95 | $0.19 | $4.00 | 5× | [src](https://platform.kimi.ai/docs/pricing/chat-k3) |
| Kimi K2.7-Code-Highspeed | $1.90 | $0.38 | $8.00 | 5× | [src](https://platform.kimi.ai/docs/pricing/chat-k3) |
| Kimi K2.6 | $0.95 | $0.16 | $4.00 | 5.9× | [src](https://platform.kimi.ai/docs/pricing/chat-k3) |
| Claude Opus 5 | $5.00 | $0.50 | $25.00 | 10× | [src](https://platform.claude.com/docs/en/about-claude/pricing) |
| Claude Sonnet 5 | $2.00 | $0.20 | $10.00 | 10× | [src](https://platform.claude.com/docs/en/about-claude/pricing) |
| Claude Fable 5.1 | $10.00 | **$0.25** | $50.00 | **40×** (0.025× multiplier) | [src](https://platform.claude.com/docs/en/about-claude/pricing) |
| Claude Haiku 4.5 | $1.00 | $0.10 | $5.00 | 10× | [src](https://platform.claude.com/docs/en/about-claude/pricing) |
| gpt-5.6-sol | $4.00 | $0.40 | $20.00 | 10× | [src](https://developers.openai.com/api/docs/pricing) |
| gpt-5.6-terra | $2.00 | $0.20 | $12.00 | 10× | [src](https://developers.openai.com/api/docs/pricing) |
| gpt-5.4 | $2.50 | $0.25 | $15.00 | 10× | [src](https://developers.openai.com/api/docs/pricing) |
| gpt-5-nano | $0.05 | $0.005 | $0.40 | 10× | [src](https://developers.openai.com/api/docs/pricing) |
| Alibaba Model Studio (implicit cache) | 1× | **0.2×** of standard (0.1× for `deepseek-v4.1-flash`; GLM 0.20–0.25×) | — | 5× | [src](https://www.alibabacloud.com/help/en/model-studio/context-cache) |
| Alibaba Model Studio (explicit cache) | write 1.25× | **0.1×** of standard | — | 10× | [src](https://www.alibabacloud.com/help/en/model-studio/context-cache) |

Note the two different billing shapes. DeepSeek, Moonshot, OpenAI and Alibaba's
*implicit* cache charge **nothing to write** — the discount is pure upside.
Anthropic and Alibaba's *explicit* cache charge a **write premium**, so caching
has a break-even:

| Cache TTL | Write multiplier | Read multiplier | Break-even reads | Source |
|---|---|---|---|---|
| Anthropic 5-minute | 1.25× | 0.10× | 0.28 → **1 read** | [src](https://platform.claude.com/docs/en/about-claude/pricing) |
| Anthropic 1-hour | 2.00× | 0.10× | 1.11 → **2 reads** | [src](https://platform.claude.com/docs/en/about-claude/pricing) |
| Anthropic 5-min, Fable 5.1 | 1.25× | 0.025× | 0.26 → **1 read** | [src](https://platform.claude.com/docs/en/about-claude/pricing) |
| Alibaba explicit | 1.25× | 0.10× | 0.28 → **1 read** | [src](https://www.alibabacloud.com/help/en/model-studio/context-cache) |

(Break-even `n = (write_mult − 1)/(1 − read_mult)`, computed in `python3`.)

**Blended $/1M input at hit rate h** — `(1−h)·miss + h·hit`, `est.`:

| Model | h=50 % | h=80 % | h=90 % | h=95 % |
|---|---|---|---|---|
| DeepSeek-V4-Flash | $0.0715 | $0.0304 | $0.0167 | $0.0099 |
| DeepSeek-V4-Pro | $0.3410 | $0.1496 | $0.0858 | $0.0539 |
| Kimi K3 | $1.6500 | $0.8400 | $0.5700 | $0.4350 |
| Claude Opus 5 | $2.7500 | $1.4000 | $0.9500 | $0.7250 |
| Claude Fable 5.1 | $5.1250 | $2.2000 | $1.2250 | $0.7375 |
| gpt-5.6-sol | $2.2000 | $1.1200 | $0.7600 | $0.5800 |
| gpt-5.4 | $1.3750 | $0.7000 | $0.4750 | $0.3625 |

Two observations. (a) OpenRouter reports a **92 % cache hit rate on Kimi K3
traffic** [src](https://emergent.sh/learn/kimi-k3-pricing) — agentic traffic is
genuinely that repetitive, so the `h=90 %` column is the realistic one for coding
agents, not the `h=50 %` column. (b) DeepSeek's 46.7× discount is only possible
because V4.1-Flash's KV is 890 B/token with FP4 KV (§5.1) — the cache is cheap enough to keep
that reloading it costs almost nothing. **The architecture sets the price.**

Minimum cacheable prefix: OpenAI 1,024 tokens of stable prefix
[src](https://devtoollab.com/blog/prompt-caching-guide); Alibaba 1,024 tokens for
both implicit and explicit
[src](https://www.alibabacloud.com/help/en/model-studio/context-cache). Alibaba
explicit-cache TTL is 5 minutes, resetting on hit; implicit TTL is indeterminate
[src](https://www.alibabacloud.com/help/en/model-studio/context-cache). No cache
storage fee at Alibaba; Moonshot's K3 pricing page lists no storage fee
[src](https://platform.kimi.ai/docs/pricing/chat-k3).

**Self-hosting comparison.** METHODOLOGY §6 was amended on 2026-09-19 to match
this table: assume ~10 % of the uncached prefill cost for **our own serving**
(the KV load / reuse), but for **vendor API comparisons** use the vendor's own
published cached-input ratio from the table above (DeepSeek ≈ 2.1 %, Anthropic
≈ 2.5 %, Alibaba 10–25 %), never a generic 10 %. The true vendor figure ranges
2 %–20 % depending on who you buy from. For a *self-hosted* deployment the right number is
the KV-load time over the prefill time it replaces — which for GPU-resident
cache is ~0 and for CPU-tier cache at 83.4 GB/s is computable:

```
DS-V4.1-Flash, 128K cached prefix = 131072 × 890 B = 0.117 GB
load from host at 83.4 GB/s       = 1.4 ms                        est.
prefill of the same 128K          = 2.097 PFLOP / (peak × MFU)    est.
```

At any plausible peak/MFU the load is three orders of magnitude cheaper than the
recompute. For DS-V4.1-Flash the case for aggressive CPU/NVMe KV offload is
overwhelming; for a model with conventional GQA KV it is a real transfer-time
tradeoff.

---

## 2. Speculative decoding

### 2.1 The methods in play as of 2026-09-19

| Method | Draft source | Shape | Where |
|---|---|---|---|
| **MTP / nextn** | extra prediction layers trained with the backbone | sequential, 1–3 tokens | DeepSeek V3/V4 (`num_nextn_predict_layers`), Qwen3.x (`mtp_num_hidden_layers: 1` in Qwen3.8-27B) |
| **DSpark** | block-diffusion draft + low-rank Markov bias head + confidence head | **parallel block**, drafting cost flat in block depth | DeepSeek V4.1-Flash (in-checkpoint), Kimi-K3 (separate `Inferact/Kimi-K3-DSpark`), Nemotron 3.5 Lightning |
| **EAGLE-3** | autoregressive drafting from fused target hidden states | chain or tree | merged into vLLM, SGLang and TensorRT-LLM main in early 2026 [src](https://www.spheron.network/blog/eagle-3-speculative-decoding-gpu-cloud/) |
| **DFlash / DFlash2** | parallel block prediction with anchor tokens | block | `incoai/Qwen3.8-27B-DFlash2`, vLLM ≥0.28.0 [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) |
| **n-gram** | prompt-lookup, no model | chain | vLLM built-in; strongest on copy-heavy traffic |

DS-V4.1-Flash is a special case: **V4.1 dropped the MTP module that V3 and V4
trained alongside the backbone, so DSpark is the only speculative method for this
checkpoint** [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash).
Note this does *not* contradict `num_nextn_predict_layers: 3` in
`research/models/deepseek41f/config.json`: that field names DSpark's **three draft
stages**, stored under `mtp.*` in the checkpoint. The tech report is explicit —
*"We omit the MTP module during backbone pre-training and use DSpark for
speculative decoding"* (§2.1, via
[`research/models/deepseek41f/architecture.md`](../models/deepseek41f/architecture.md) §7.1).

### 2.2 DSpark, concretely

From `research/models/deepseek41f/config.json` (repo) and the vLLM recipe:

| Parameter | DS-V4.1-Flash | Kimi-K3 |
|---|---|---|
| `dspark_block_size` | **5** | 8 positions per iteration (γ=7 + bonus) [src](https://vllm.ai/blog/2026-09-15-kimi-k3-dspark) |
| `dspark_markov_rank` | **256** | low-rank Markov head, rank ⚠️ TO BE VERIFIED |
| Draft stages | 3, reading attention input of layers **37, 38, 39** | 5-layer, 5B-param draft model [src](https://vllm.ai/blog/2026-09-15-kimi-k3-dspark) |
| Draft MoE | `dspark_n_routed_experts: 128`, `dspark_num_experts_per_tok: 3` | MLA-native draft mirroring K3's own attention |
| Drafter weight cost | **~14B params** added to the load [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash) | 5B [src](https://vllm.ai/blog/2026-09-15-kimi-k3-dspark) |
| Extra heads | Markov bias head + confidence head on the last stage | Markov logit-bias head + confidence head |
| Noise token | `dspark_noise_token_id: 128799` | — |

The three components do different jobs: the **parallel block** gives
non-causal predictions in one pass (so drafting cost stays flat as the block
deepens), the **Markov head** supplies the intra-block sequential dependency the
parallel pass cannot see, and the **confidence head** predicts per-position
acceptance probability, which is what makes adaptive verification possible
[src](https://vllm.ai/blog/2026-09-15-kimi-k3-dspark).

vLLM serve config for DS-V4.1-Flash, verbatim from the recipe
[src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash):

```json
{"method":"dspark","num_speculative_tokens":5,"draft_sample_method":"probabilistic",
 "rejection_sample_method":"block","enable_adaptive_verification":false}
```

and for Kimi-K3 [src](https://vllm.ai/blog/2026-07-27-k3):

```json
{"model":"Inferact/Kimi-K3-DSpark","method":"dspark","num_speculative_tokens":7,
 "attention_backend":"FLASHINFER_MLA","draft_sample_method":"probabilistic",
 "rejection_sample_method":"block"}
```

### 2.3 Acceptance rates — measured

| Model | Method | Workload | Accepted tokens / step | Source |
|---|---|---|---|---|
| Kimi-K3 | DSpark γ=7 | macro-average | **4.11** | [src](https://vllm.ai/blog/2026-09-15-kimi-k3-dspark) |
| Kimi-K3 | DSpark | math reasoning | **6.42** | [src](https://vllm.ai/blog/2026-09-15-kimi-k3-dspark) |
| Kimi-K3 | DSpark | code (HumanEval) | 4.96 | [src](https://vllm.ai/blog/2026-09-15-kimi-k3-dspark) |
| Kimi-K3 | DSpark | translation | 4.65 | [src](https://vllm.ai/blog/2026-09-15-kimi-k3-dspark) |
| Kimi-K3 | DSpark | LongBench-v2, 378K prompts | up to 5.31 | [src](https://vllm.ai/blog/2026-09-15-kimi-k3-dspark) |
| Kimi-K3 | DSpark | coding / creative writing | 4.73 / **2.61** | [src](https://vllm.ai/blog/2026-07-27-k3) |
| Kimi-K3 | DSpark | chat (SGLang measurement) | ~2.7 | [src](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/) |
| Kimi-K3 | DSpark | few-shot math (SGLang) | ~5.0 | [src](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/) |
| DS-V4.1-Flash | DSpark γ=5 | InferenceX #3058 "golden acceptance length" — **⚠️ synthetic benchmark constant, not a measurement**: the MI355X server config sets `"rejection_sample_method":"synthetic","synthetic_acceptance_length":3.51` (§2.7) | **3.51** `est.` | [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash) |
| DeepSeek-R1 | MTP | 128K/8K on GB300 | accept length **2.37** — source reads `accept length=2.37@MTP3`, i.e. **γ=3**, not γ=1 | [src](https://www.lmsys.org/blog/2026-02-19-gb300-longctx/) |
| DeepSeek-V3.2 / R1 | MTP | GB300, low-to-moderate concurrency | acceptance **>80 %** | [src](https://vllm.ai/blog/2026-02-13-gb300-deepseek) |
| Qwen3.8-27B FP8 | in-checkpoint MTP, γ=3 | 2×RTX 5090 TP2 | **0.771** | [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) |
| Qwen3.8-27B NVFP4 (Inferact) | MTP γ=3 | same | **0.897** | [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) |
| Qwen3.8-27B NVFP4 (unsloth) | MTP γ=3 | same | 0.788 | [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) |
| Qwen3.8-27B NVFP4, 1×5090, 32K | MTP γ=3 | `--enforce-eager` | 0.754 | [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) |
| Qwen3-8B | EAGLE-3 tree, GSM8K | k=21 tree | acceptance rate drops 0.415 → 0.300 → 0.095 across tree configs | [src](https://arxiv.org/pdf/2601.11580) |
| DeepSeek-V4-Pro-0813 | DSpark γ=7, 8×B300 TP8 | positional | position 1 **>70 %**, position 7 **<10 %** | [src](https://vllm.ai/blog/2026-08-14-dspark-adaptive-verification) |
| various (AMD) | all methods | positional | first positions 85–95 %, dropping to 20–40 % by position 7 | [src](https://vllm.ai/blog/2026-08-23-speculative-decoding-amd-gpus) |

Note the Qwen3.8-27B quantization result: **the uniform-W4A4 Inferact NVFP4 build
drafts better (0.897) than FP8 (0.771)**, while the mixed-precision unsloth NVFP4
build leaves room for roughly twice the KV cache but drafts at 0.788
[src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B). Acceptance is a property of the
*quantized* model, not of the architecture, and it is not monotone in precision.

Read acceptance from `vllm:spec_decode_num_{accepted,draft}_tokens_total` — the
recipe is explicit that "throughput alone cannot distinguish a working drafter
from one that loaded and was ignored"
[src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B).

### 2.4 Measured end-to-end speedups

| Model | HW / config | Baseline | With spec | Speedup | Source |
|---|---|---|---|---|---|
| Kimi-K3 | 16×GB300 NVL72, TP16, bs=1 | 118 tok/s | **370 tok/s** | 3.14× | [src](https://vllm.ai/blog/2026-07-27-k3) |
| Kimi-K3 | 8×GB300 NVL72, TP8, bs=1 | 111 tok/s | 331 tok/s | 2.98× | [src](https://vllm.ai/blog/2026-07-27-k3) |
| Kimi-K3 | SGLang, bs=1 | ~113 tok/s | ~423 tok/s | 3.74× | [src](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/) |
| Kimi-K3 | vLLM DSpark blog, single request | ~110 tok/s | ~435 tok/s | ~3.95× | [src](https://vllm.ai/blog/2026-09-15-kimi-k3-dspark) |
| Kimi-K3 | 16 concurrent | — | 683 tok/s output throughput; TTFT 379→479 ms | "~3.5× higher output throughput at matched interactivity" | [src](https://vllm.ai/blog/2026-09-15-kimi-k3-dspark) |
| DeepSeek-R1 | GB300 NVL72, 128K/8K | 23 TPS/user | 43 TPS/user | **+87 %** | [src](https://www.lmsys.org/blog/2026-02-19-gb300-longctx/) |
| DeepSeek-R1 | GB200, 128K/8K peak TPS/GPU | 147.9 | 169.1 | +14 % | [src](https://www.lmsys.org/blog/2026-02-19-gb300-longctx/) |
| DeepSeek-R1 | **GB300**, 128K/8K peak TPS/GPU | 226.2 | **224.2** | **−0.9 %** | [src](https://www.lmsys.org/blog/2026-02-19-gb300-longctx/) |
| gemma-4-26B-A4B-it | MI300X/MI355X, MATH500 | — | — | 2.87× (DFlash) | [src](https://vllm.ai/blog/2026-08-23-speculative-decoding-amd-gpus) |
| Gemma 4 | AMD, GSM8K | — | — | 2.74× (MTP) | [src](https://vllm.ai/blog/2026-08-23-speculative-decoding-amd-gpus) |
| Kimi-K2.5 | AMD, MATH500 | — | — | 2.68× (DFlash) | [src](https://vllm.ai/blog/2026-08-23-speculative-decoding-amd-gpus) |
| Qwen3.5-122B-A10B | AMD, native MTP | — | — | up to 2.20× | [src](https://vllm.ai/blog/2026-08-23-speculative-decoding-amd-gpus) |
| Qwen3-8B | AMD | — | — | only 1.08–1.63× | [src](https://vllm.ai/blog/2026-08-23-speculative-decoding-amd-gpus) |
| Llama-3.1-8B-Instruct | SGLang v0.4.4, 1×H100, EAGLE-3 | — | — | **1.81× at bs=2, 1.38× at bs=64** | [src](https://www.spheron.network/blog/eagle-3-speculative-decoding-gpu-cloud/) |
| DeepSeek-R1 FP4 | GB300 Dynamo TRT, 8K/1K, 150 tok/s/user | $2.35/M | **$0.11/M** | ~21× cost reduction | [src](https://newsletter.semianalysis.com/p/inferencex-v2-nvidia-blackwell-vs) |
| DeepSeek-R1 FP4 | B200 Dynamo TRT | $0.251/M total | **$0.057/M** with MTP | 4.4× | [src](https://newsletter.semianalysis.com/p/inferencex-v2-nvidia-blackwell-vs) |

**The GB300 row is the important one.** On the same 128K/8K workload, MTP gave
GB200 +14 % peak tokens/s/GPU but GB300 **−0.9 %** — and the *user-facing*
benefit was the opposite direction (+87 % TPS/user on GB300). Blackwell Ultra's
1.5× NVFP4 tensor-core throughput and 2× softmax SFU
[src](https://www.lmsys.org/blog/2026-02-20-gb300-inferencex/) move the decode
step further away from memory-bound, which is exactly the regime where free
verify slots stop being free. Speculative decoding on GB300 buys *latency*, not
*throughput*. Accuracy held: LongBench-v2 56.9 % with MTP vs 57.2 % without
[src](https://www.lmsys.org/blog/2026-02-19-gb300-longctx/).

SGLang had not yet enabled MTP on GB300 as of the Feb 2026 InferenceX post — it
was listed as roadmap [src](https://www.lmsys.org/blog/2026-02-20-gb300-inferencex/).
Whether that has landed by 2026-09-19 is **⚠️ TO BE VERIFIED**.

### 2.5 Why the gains shrink at high batch — and what to do about it

The mechanism, stated plainly by the vLLM adaptive-verification post: "at low
concurrency, GPUs are memory-bound with spare compute, making draft tokens
nearly free. At concurrency 256, draft tokens compete with real tokens for the
same compute", and rejected tokens waste that compute
[src](https://vllm.ai/blog/2026-08-14-dspark-adaptive-verification).

In METHODOLOGY §4 terms: at low batch `decode_step_time` is set by the
`bytes_per_decode_step / (HBM_BW × MBU)` branch, which is **independent of how
many query positions you verify**. At high batch the
`2 × active_params × batch / (peak_FLOPS × MFU)` branch takes over, and
verifying γ+1 positions multiplies `batch` by γ+1.

`est.` speedup model — `S = accept_len / (1 + vcr·γ)` where `vcr` is the marginal
cost of a verify token relative to a real decode token:

| Config | accept | γ | vcr=0 (bs=1) | vcr=0.5 | vcr=1 (compute-bound) |
|---|---|---|---|---|---|
| K3 DSpark, coding | 4.73 | 7 | 4.73× | 1.05× | **0.59×** |
| K3 DSpark, macro-avg | 4.11 | 7 | 4.11× | 0.91× | 0.51× |
| K3 DSpark, creative | 2.61 | 7 | 2.61× | 0.58× | 0.33× |
| DS-V4.1-Flash, "golden" (⚠️ synthetic constant, §2.7) | 3.51 | 5 | 3.51× | 1.00× | 0.58× |
| DeepSeek-R1 MTP | 2.37 | **3** | 2.37× | **0.95×** | **0.59×** |

γ=3 MTP degrades far more gently than γ=7 DSpark across the useful range, and
γ=7 DSpark is spectacular at bs=1 and a net loss if you keep verifying all seven
at compute-bound batch. **Short blocks survive load; long blocks do not.**
⚠️ **Caveat on this toy model, TO BE VERIFIED**: with the corrected γ=3 the
break-even marginal verify cost is `vcr* = (accept−1)/γ` = 0.46 for R1-MTP
against 0.53 for K3-DSpark-coding and 0.50 for DS-V4.1-Flash — i.e. the model
alone does **not** separate short from long blocks. The "short blocks survive
load" conclusion rests on the *measured* GB200/GB300 and bs=256 trimming data
above, not on this formula; `vcr` itself rising faster with γ is the unmodelled
term.

Two production fixes, both shipping:

**(a) vLLM adaptive verification** — `enable_adaptive_verification: true`. The
confidence head predicts per-position survival; a one-time profile records what
an extra verify token actually costs at each load level; a per-step planner keeps
tokens only while expected value covers marginal cost. Measured on
DeepSeek-V4-Pro-0813, 8×B300 (SM100) TP8, max-model-len 16384, FP8 KV,
`--max-num-seqs 256`: behaves like a longer fixed block at concurrency 1–64 and
like a shorter one at 128–256, "stays on the Pareto frontier throughout"; where
fixed verification "pays for all 21 slots including ones with near-zero
survival", adaptive "only verified the best B=11"
[src](https://vllm.ai/blog/2026-08-14-dspark-adaptive-verification).

Constraints: needs `AttentionCGSupport.ALWAYS` (full varlen decode CUDA graphs);
incompatible with `--enforce-eager`, LoRA and pipeline parallelism; disables
output logprobs [src](https://vllm.ai/blog/2026-08-14-dspark-adaptive-verification).

**(b) SGLang verify trimming** — same idea, different implementation. Measured on
Kimi-K3: break-even through bs=8, then the gap opens — **+68 %** throughput on a
chat panel and **+24 %** on few-shot math at **bs=256**, with accept length
easing 2.7→2.2 and 5.0→4.3 respectively
[src](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/). Below bs=8
trimming is break-even to mildly negative; a small-batch early-exit is the known
follow-up.

The cost curve is a **staircase**, not a smooth function: "flat shelves where
another token slots into the current kernel waves for almost nothing, and steep
risers where it starts a new one"
[src](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/). Plan
`--max-cudagraph-capture-size` against this — the DS-V4.1-Flash recipe rounds
`128 × (1 + 5) = 768` up to 1024 for exactly this reason
[src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash).

### 2.6 Speculation and recurrent state: ReplaySSM

Speculative decoding verifies γ+1 tokens and may accept only a prefix, so the
state must be rewindable. A KDA layer overwrites its state every token, so the
naive approach snapshots the whole K×V state after every draft step: 64 KB per
request, layer and head at K=V=128, × (γ+1) steps, across 69 KDA layers — and
that scratch is reserved *per running request*, so it caps concurrency.

**ReplaySSM** keeps each draft step's raw inputs `Sᵢ = (vᵢ, kᵢ, gkᵢ, βᵢ)` (~1 KB)
instead, written by the verify kernel on its way through, then replays only the
accepted prefix with one fold kernel. Results
[src](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/):

- draft-window memory **512 KB → 16 KB, ~32×**
- "handing that memory back to the state pool lifts the concurrency ceiling
  several-fold"
- rebuilt state is **bit-identical** to the recurrent baseline
- per-step time improves even at small batch, because verify no longer writes a
  snapshot per step

vLLM measures the same technique as "effective capacity rises 10.97 % under TP8"
[src](https://vllm.ai/blog/2026-09-13-kimi-k3-performance-optimization) —
a smaller number than SGLang's, on a different baseline; the two are not directly
comparable. ⚠️ Reconciling them is **TO BE VERIFIED**.

### 2.7 Platform gaps

**AMD**: vLLM currently refuses `enable_adaptive_verification: true` for
DS-V4.1-Flash on ROCm. Two checks in
`maybe_create_adaptive_verification_manager` fail — the indexer helper
`supports_device_cpu_query_lens_mismatch()` is `False`, and
`DeepseekV41ROCMAiterSparseSWABackend` reports `AttentionCGSupport.UNIFORM_BATCH`
rather than `ALWAYS`. The generated command therefore sets
`enable_adaptive_verification:false` and verifies the full block; DSpark still
drafts 5 tokens per round
[src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash).

**Benchmark-only synthetic acceptance**: the MI355X InferenceX #3058 server
config uses `"rejection_sample_method":"synthetic","synthetic_acceptance_length":3.51`
for throughput mode. The recipe is explicit: set `EVAL_ONLY=true` for real block
rejection, "use that mode for ordinary serving too"
[src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash). **Do not size a
deployment off a synthetic-acceptance benchmark number.**

**No universal setting**: the AMD study concludes the optimal
`num_speculative_tokens` "varied substantially by model and workload — no
universal setting emerged", and that speculative decoding "should be treated as a
runtime optimization rather than a fixed setting"
[src](https://vllm.ai/blog/2026-08-23-speculative-decoding-amd-gpus).

---

## 3. Parallelism for large MoE

### 3.1 The axes

| Axis | What it shards | KV effect | Comm per layer | Best for |
|---|---|---|---|---|
| **TP** | every GEMM, attention heads | KV sharded by head — **but not for MLA**, which has one KV head, so every rank holds a full copy [src](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/) | AllReduce (a barrier) | interactivity, small GPU counts |
| **EP** | experts across ranks | none | all-to-all dispatch + combine | MoE throughput at scale |
| **DP-attention** | replicates attention weights per rank, shards the batch | full KV per rank | none for attention | avoids TP's per-layer AllReduce; costs ~61 GB (KDA) + 11 GB (MLA) of replicated weight on K3 [src](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/) |
| **PP** | model by layer | each stage holds KV for its layers only | one hand-off per stage, hideable | **prefill** |
| **DCP** (decode context parallelism) | KV **by token position**, `p mod N == r` | KV capacity × N | one packed all-to-all per layer (partial output + log-sum-exp) | **decode** at long context |

**Why DCP works and TP does not, for MLA.** Position-sharding breaks softmax,
since each rank sees only 1/N of the keys. The fix is FlashAttention's own: each
rank returns its partial attention output plus a per-head log-sum-exp, one
all-to-all exchanges them, and a local merge by log-sum-exp is *exact*. The
result is already in the head layout the output projection expects under TP, so
nothing downstream changes
[src](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/).

KDA is the exception that proves the rule: its state is one fixed-size matrix
*per request*, not per token, so there is no position axis to shard and KDA
layers stay TP-sharded.

### 3.2 Measured: which topology wins, per phase

**Prefill — deep PP beats TEP.** Kimi-K3, 8K prefill across 2×4 GB300, topology
the only variable
[src](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/):

| Topology | Result |
|---|---|
| PP8×TP1 | ~**1.7× TEP8's ceiling**, lower TTFT, cheapest per 1k prefill tokens |
| PP4×TP2 | ties TEP8 — too shallow to cover its hand-offs, still pays TP2's AllReduce |
| TEP8 | baseline |

91 % of the stage hand-off is hidden behind the next chunk's compute on K3. Each
rank runs whole layers so GEMMs are 8× wider; each stage holds KV for only ~12
layers. A K3 prefill node running PP8 has **1.45–1.72× the prefill capacity of a
TEP8 node**, so one prefill node feeds several decode nodes
[src](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/). PP8 loses
only at a single request — "a prefill worker that idle is misconfigured anyway".

**Decode — DCP beats TP.** Kimi-K2.6 NVFP4 on 8×B200
[src](https://vllm.ai/blog/2026-08-07-decode-context-parallelism):

| Config | tok/s/GPU at c=512 | KV utilisation |
|---|---|---|
| DCP | **6,091** | 82 % at c=512 |
| TP baseline | ~1,863 (plateau) | 100 % at c=64, then stalls |

≈**3.3×**. Stable up to 200K+ sequence lengths. Flags
`--tensor-parallel-size N --decode-context-parallel-size M`. Limits: MLA requires
`TP ≥ DCP`; GQA caps DCP at `TP // num_key_value_heads`.

On K3, DCP8 stores 16 physical KV copies where TP stores 64 for the same 16 token
positions on 4 ranks — **~7.9× more logical KV capacity**
[src](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/), and TPOT p50
drops 13.8 ms → 10.5 ms at concurrency 1
[src](https://vllm.ai/blog/2026-09-13-kimi-k3-performance-optimization). vLLM's
DCP for K3 was still a prototype at the July launch, with "40 % higher throughput
than TP8 under selected workloads" in early experiments
[src](https://vllm.ai/blog/2026-07-27-k3).

**TP vs EP on GB300, DeepSeek-R1 NVFP4**: TP2 shows "50 % to 2× worse TPOT per
decode step" than EP2 [src](https://vllm.ai/blog/2026-02-13-gb300-deepseek).

### 3.3 DeepEP modes, all-to-all backends, EPLB

DeepEP has two dispatch modes, and the split maps exactly onto the P/D split:

| Mode | Optimised for | CUDA graph |
|---|---|---|
| `normal` | prefill — high throughput | — |
| `low_latency` | decode — low latency | yes |

`--deepep-mode auto` switches at runtime in SGLang; manual selection is for
debugging [src](https://docs.sglang.io/advanced_features/expert_parallelism.html).

Backend selection, SGLang `--moe-a2a-backend`
[src](https://docs.sglang.io/advanced_features/expert_parallelism.html):

| Backend | Use |
|---|---|
| `none` (default) | hybrid EP/TP via AllReduce/AllGather |
| `deepep` | large-scale EP |
| `mooncake` | elastic inference over RDMA |
| `nixl` | elastic EP with fault tolerance, dynamic scaling |
| `mori` | AMD ROCm |
| `flashinfer` | large-scale |
| `pplx` | low-latency decode on Hopper via NVSHMEM — **requires `--enable-dp-attention`** with `tp_size/attention_tp_size > 1` |

vLLM `--all2all-backend`
[src](https://docs.vllm.ai/en/latest/serving/expert_parallel_deployment/):

| Backend | Use |
|---|---|
| `allgather_reducescatter` | default, any EP+DP |
| `deepep_high_throughput` | multi-node **prefill** — grouped GEMM, continuous layout |
| `deepep_low_latency` | multi-node **decode** — CUDA graph support, masked layout |
| `flashinfer_nvlink_one_sided` | MNNVL systems (high-throughput, multi-node NVLink) |
| `flashinfer_nvlink_two_sided` | systems with NVLink across nodes |

For Kimi-K3 specifically vLLM recommends `flashinfer_nvlink_one_sided` for NVLink
and `deepep_v2` for RDMA, with MoE backend `deep_gemm_mega_moe` for DEP and
`flashinfer_trtllm` for TP>1 [src](https://vllm.ai/blog/2026-07-27-k3).

`EP_SIZE = TP_SIZE × DP_SIZE` in vLLM
[src](https://docs.vllm.ai/en/latest/serving/expert_parallel_deployment/).

**EPLB** (Expert-Parallel Load Balancer) analyses expert activation statistics and
computes an optimal arrangement, replicating hot experts to minimise per-GPU
utilisation variance. vLLM `--enable-eplb` with `--eplb-config`
[src](https://docs.vllm.ai/en/latest/serving/expert_parallel_deployment/):

| Key | Default | Meaning |
|---|---|---|
| `window_size` | 1000 | engine steps tracked for rebalancing |
| `step_interval` | 3000 | rebalance frequency |
| `num_redundant_experts` | **0** | extra experts per EP rank |
| `use_async` | true | non-blocking |
| `log_balancedness` | false | metrics |

**Memory overhead ≈ 2.4 GB per redundant expert per EP rank for DeepSeek-V3**
[src](https://docs.vllm.ai/en/latest/serving/expert_parallel_deployment/) — this
is the number to budget against `kv_budget` in METHODOLOGY §3. SGLang advises
larger batches for stable statistics and rebalancing roughly every 1,000 requests
[src](https://docs.sglang.io/advanced_features/expert_parallelism.html).

**Two-batch overlap** (`--enable-two-batch-overlap`, SGLang) interleaves attention
with dispatch/combine across micro-batches, "potentially up to 2× throughput"
[src](https://docs.sglang.io/advanced_features/expert_parallelism.html) — vendor
claim, workload unspecified. Note SGLang deliberately **did not** use TBO on
GB300 NVL72, adopting instead "a single-batch overlap strategy tuned to the
higher interconnect bandwidth of NVL72", running communication concurrently with
down-GEMM in a producer–consumer pattern and overlapping shared-expert compute on
another CUDA stream [src](https://www.lmsys.org/blog/2026-02-20-gb300-inferencex/).

**Elastic EP** (vLLM) resizes the DP worker count at runtime via
`POST /scale_elastic_ep`; EPLB reshuffles experts on scale-up and consolidates
them before rank removal on scale-down. Constraints: `tensor_parallel_size=1`
only, `api_server_count=1`, Ray DP backend, no DBO or MoE drafter
[src](https://vllm.ai/blog/2026-05-14-elastic-expert-parallelism). ⚠️ No measured
throughput numbers published — **TO BE VERIFIED**.

### 3.4 Wide-EP on NVL72 — measured

GB200/GB300 NVL72 connects 72 GPUs into one 130 TB/s NVLink domain
[src](https://www.lmsys.org/blog/2026-02-20-gb300-inferencex/), which is the
whole point for MoE: all-to-all dispatch/combine stays inside the scale-up
fabric. NVIDIA deploys EP across all 72 GPUs in one NVLink domain; wide-EP puts
4–32 experts per GPU rather than 32+
[src](https://newsletter.semianalysis.com/p/inferencex-v2-nvidia-blackwell-vs).

Blackwell Ultra deltas over Blackwell
[src](https://www.lmsys.org/blog/2026-02-20-gb300-inferencex/):

| Change | Factor |
|---|---|
| Peak NVFP4 tensor-core throughput per clock | 1.5× |
| Softmax throughput (upgraded SFU) | 2× |
| HBM3e capacity (12-Hi vs 8-Hi stacks) | 1.5× — **288 GB vs 192 GB per GPU** on the die/nameplate [src](https://www.lmsys.org/blog/2026-02-19-gb300-longctx/). **As deployed** (METHODOLOGY §8) size against **GB300 NVL72 = 288 GB/GPU, ≈279 usable** and **B200 HGX = 180 GB** (192 GB is the physical stack figure). HGX B300 is a *different* product at **268 GB/GPU, 2,144 GB per 8-GPU node** — do not merge the two |

DeepSeek-R1-NVFP4, ISL=128K / OSL=8K, EP16/TP16 decode + PP4 prefill, FP8
attention & KV [src](https://www.lmsys.org/blog/2026-02-19-gb300-longctx/):

| Metric | GB300 | GB200 | Ratio |
|---|---|---|---|
| Peak TPS/GPU, no MTP | **226.2** | 147.9 | 1.53× |
| Peak TPS/GPU, with MTP | 224.2 | 169.1 | 1.33× |
| TPS/user, no MTP | 23 | — | — |
| TPS/user, with MTP | 43 | — | +87 % |
| High-throughput scenario TPS/GPU | 204.7 | 147.9 | +38.4 % |
| Latency-balanced TPS/GPU | 167.9 | 106.5 | 1.58× |
| 128K prefill, no chunking | 15.2 s | 18.6 s | 1.22× |
| 128K prefill, 32K dynamic chunking | **8.6 s** | — | — |
| FMHA kernel | 205 ms | 277 ms | 1.35× |
| Max decode batch (DEP16) | 576 concurrent | 320 | 1.8× |
| Req/GPU theoretical cap | 40 | 24 | 1.67× |
| Practical target (~85 %) | 36 req/GPU | 20 req/GPU | 1.8× |

vLLM on GB300, NVFP4, v0.14.1/CUDA 13.0
[src](https://vllm.ai/blog/2026-02-13-gb300-deepseek):

| Model / config | Prefill-only TGS | Mixed 2K/1K TGS |
|---|---|---|
| DeepSeek-V3.2, NVFP4 TP2 | 7,360 | 2,816 |
| DeepSeek-R1, NVFP4 EP2 | **22,476** (ISL=2K) | 3,072 |

GB300 vs H200: **8×** on prefill (ISL=2K), **20×** on mixed 2K/128. GB300 vs
B300: only 12–14 % on prefill. TPOT with disaggregation stays under 60 ms at
batch 256 for V3.2, vs >80 ms integrated.

Headline InferenceX v2 claims — **all vendor/benchmark-house claims, read the
qualifiers** [src](https://newsletter.semianalysis.com/p/inferencex-v2-nvidia-blackwell-vs)
[src](https://www.lmsys.org/blog/2026-02-20-gb300-inferencex/):

- SGLang on GB300 NVL72: **up to 25× vs H200** on DeepSeek-R1 — the H200
  baseline is taken **at 50 TPS/user**; "in the absence of latency constraint
  H200 can achieve similar throughput".
- GB200 NVL72: up to **8× more tokens/GPU** in high-throughput regimes and **4×
  more tokens/user** in high-interactivity regimes vs InferenceMAXv1, in under
  4 months — a *software* gain.
- GB300 NVL72 vs H100 FP8 disagg+wideEP baseline: "up to 100× on FP8 vs FP4";
  65× vs H100 at 75 tok/s/user; 55× vs H200 NVL72 at the same interactivity.
- GB200 NVL72 delivers triple the per-GPU throughput of a single B200 at 60
  tok/s/user.
- Blackwell vs Hopper tokens-per-dollar: **9.7× at 40 tok/s/user, up to 65× at
  116 tok/s/user**.
- MLPerf v6.0: GB300 NVL72 led GPT-OSS-120B datacenter closed; a GB300 4-GPU
  compute tray posted 60,220 tok/s offline / 53,463 tok/s server
  [src](https://www.spheron.network/blog/mlperf-inference-v6-benchmark-results-2026/).
  GB300 NVL72 delivers 45 % higher DeepSeek-R1 offline throughput per GPU than
  GB200 NVL72 and ~5× Hopper
  [src](https://developer.nvidia.com/blog/nvidia-blackwell-ultra-sets-new-inference-records-in-mlperf-debut/).

**AMD's position on wide-EP.** MI355X is "currently not employed for any points"
on the InferenceX Pareto frontier with wideEP; SemiAnalysis judges AMD "more than
six months behind on open source distributed inferencing and wide expert
parallelism", and identifies *composability* as the specific problem —
optimizations work in isolation but "when combined with other optimizations, the
result is not as competitive"
[src](https://newsletter.semianalysis.com/p/inferencex-v2-nvidia-blackwell-vs).
Where AMD does compete: MI355X FP8 disagg-prefill is "quite competitive with
B200" at 60–120 tok/s/user, single-node AMD SGLang gives "better perf per TCO
than NVIDIA's SGLang for FP8", and MoRI gave "more than 20 % throughput increase
in the 20-45 tok/s/user interactivity range" in a month. AMD FP4 single-node
"gets absolutely mogged by Nvidia's B200". MLPerf v6.0: on Llama-2-70B AMD
matched B200 offline, near parity server, and **exceeded** interactive; vs B300,
92 % offline / 93 % server / **104 % interactive**
[src](https://www.siliconreport.com/nvidia-gb300-nvl72-vs-amd-mi355x-inference-e6f7d36e).

### 3.5 Prefill/decode disaggregation — when it pays

Supported across vLLM, SGLang, TensorRT-LLM, LMDeploy and Dynamo (GA since March
2026) [src](https://rdp.in/gpu-mart/knowledge-base/disaggregated-inference-prefill-decode-2026/).

**Measured gains:**

| Setup | Gain | Source |
|---|---|---|
| Multi-GPU vLLM, general | reliable 2–3× throughput | [src](https://rdp.in/gpu-mart/knowledge-base/disaggregated-inference-prefill-decode-2026/) |
| H100+H200 disagg, Llama-3.1-70B | +45 % cost/hour for **+75 % throughput** | [src](https://rdp.in/gpu-mart/knowledge-base/disaggregated-inference-prefill-decode-2026/) |
| Qwen3-VL-30B-A3B-Instruct FP8 on GB200, image requests | TTFT **−30 %**, throughput **+25 %** | [src](https://rdp.in/gpu-mart/knowledge-base/disaggregated-inference-prefill-decode-2026/) |
| AMD MORI-IO connector, 8×MI300X | **2.5× higher goodput** vs collocated | [src](https://vllm.ai/blog/2026-04-07-moriio-kv-connector) |
| DeepSeek-V3.2 GB300 | TPOT under 60 ms at batch 256 vs >80 ms integrated | [src](https://vllm.ai/blog/2026-02-13-gb300-deepseek) |
| Kimi-K3, PP8 prefill + DCP8/TP8 decode | composes to **2,808 tok/s/GPU** | [src](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/) |
| Kimi-2.5 NVFP4, 12×GB200 1P1D + Mooncake | 3.8× throughput, TTFT p50 46× lower | [src](https://vllm.ai/blog/2026-05-06-mooncake-store) |

**When it does not pay:**

- "The KV transfer cost is paid on every request regardless of whether the
  throughput gain materializes"
  [src](https://towardsdatascience.com/disaggregation-is-a-thousand-gpu-problem/).
- At small GPU counts, rounding losses dominate — you cannot allocate fractional
  GPUs, so specialization gains are eaten by incomplete worker utilization.
  Modular reports a **20–30 % performance drop** from disaggregation on small or
  untuned workloads
  [src](https://towardsdatascience.com/disaggregation-is-a-thousand-gpu-problem/).
- The clean rule from the title of that piece: **disaggregation is a thousand-GPU
  problem.** Below that, the arithmetic rarely closes.

**Hybrid-model hazard.** For K3, "PD disaggregation is unforgiving": the
recurrent KDA state, the full-attention paged KV *and* the block tables all have
to arrive correctly. vLLM's NIXL connector treats the shared page as two logical
views — token-level MLA cache and request-level KDA state including convolution
and recurrent state — exchanging MLA/KDA metadata at handshake and building
separate transfer descriptors. Under heterogeneous TP it tracks logical→physical
block mapping and **zeroes untransferred tail regions** to stop stale data
leaking through padding gaps [src](https://vllm.ai/blog/2026-07-27-k3).

**DS-V4.1-Flash verified layout**: 1P1D on GB200 NVL4 — one tray (4 GPUs) per
role, TP4 in each pool, KV over NIXL, fronted by
`vllm-router --vllm-pd-disaggregation`. Both pools disable FlashInfer autotune,
JIT and CuTeDSL warmup via `--kernel-config`, skip DeepGEMM warmup
(`VLLM_DEEP_GEMM_WARMUP=skip`), and cap `--max-num-seqs` at 32. On 8-GPU nodes
the same layout becomes TP8 per role. With speculative decoding on, **DSpark must
run in both pools so the transferred KV stays compatible**. The verified GB200
runs were text-only [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash).

Also worth knowing: SGLang couples Dynamo's KV-aware router directly to SGLang's
HiCache radix tree, exposing NIXL or Mooncake as transfer backends
[src](https://www.lmsys.org/blog/2026-02-20-gb300-inferencex/); Mooncake Transfer
Engine is integrated into TensorRT-LLM and into vLLM v1 as a KV connector
[src](https://github.com/kvcache-ai/Mooncake/blob/main/README.md); and SGLang
also ships **Encode-Prefill-Decode (EPD)** disaggregation with Mooncake as a
backend, separating the vision encoder from the language model
[src](https://kvcache-ai.github.io/Mooncake/).

---

## 4. Batching and SLOs

### 4.1 Continuous batching and chunked prefill

Assume both on (METHODOLOGY §5.7). Chunked prefill lets vLLM break a large
prefill into chunks and batch them with decode, balancing the compute-bound
prefill against memory-bound decode
[src](https://docs.vllm.ai/en/stable/configuration/optimization/).

The single knob is `--max-num-batched-tokens`: "smaller values generally favor
inter-token latency, larger values favor TTFT"
[src](https://docs.vllm.ai/en/stable/configuration/optimization/).

**Measured**, GLM-5.3-Flash FP8 on 4×GB200 TP4, 8192-token prompts / 1024-token
outputs, at concurrency 32/64/128
[src](https://github.com/vllm-project/vllm/issues/56975):

| Chunk budget | Throughput (tok/s) | p99 ITL (ms) |
|---|---|---|
| 16384 (default) | 1892 / 2607 / 3377 | 199 / 337 / 342 |
| 4096 | 1349 / 1679 / 1979 | 207 / 211 / 214 |
| 2048 | 956 / 1112 / 1212 | 189 / 193 / 194 |

Dropping the budget 16384→2048 costs **~64 % of throughput at c=128** and buys
p99 ITL 342→194 ms. That is an expensive way to buy tail latency — the CUDA-graph
lever below is cheaper.

### 4.2 CUDA graphs

The key mechanism: **prefill-containing steps run outside CUDA graphs** unless
the capture size covers them. Same job, same hardware
[src](https://github.com/vllm-project/vllm/issues/56975):

| `--max-cudagraph-capture-size` | Throughput | p99 ITL (ms) | Mean TTFT (ms) | p99 TTFT (ms) |
|---|---|---|---|---|
| default (1024 limit) | 1897 / 2620 / 3391 | 196 / 334 / 340 | 1427 / 1902 / 2645 | 5164 / 10136 / 19903 |
| 4096/4096 | 1799 / 2444 / 3046 | **112 / 117 / 120** | 883 / 1368 / 2229 | 6129 / 13234 / 25271 |
| 2048/2048 | 1687 / 2240 / 2699 | **72 / 75 / 81** | 964 / 1518 / 2749 | 7750 / 15768 / 32687 |

At 4096/4096: throughput −5 % to −10 %, mean TPOT +9 % to +13 %, p99 TTFT
+19 % to +31 % — and p99 ITL **196–340 ms → 112–120 ms**. This is the right trade
for a service that must bound p99 ITL, and the wrong one otherwise. The issue
title is blunt about the alternative: SGLang on the same job runs p50 ITL 13–16
ms and p99 ITL 13/16/20 ms (decode-only batching), at the cost of 1.2–4.5× higher
TTFT [src](https://github.com/vllm-project/vllm/issues/56975).

Sizing rule when speculating: capture size must cover
`max_num_seqs × (1 + num_speculative_tokens)` — DS-V4.1-Flash rounds
`128 × 6 = 768` up to 1024 [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash).

Two CUDA-graph failure modes worth knowing:

- **ROCm + DS-V4.1-Flash**: the model does not support `torch.compile`, and the
  ROCm sparse-SWA backend only supports uniform-batch CUDA graphs. Without
  `VLLM_USE_BREAKABLE_CUDAGRAPH=1`, the default `FULL_AND_PIECEWISE` dies at
  capture [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash).
- **Capture allocates outside `--gpu-memory-utilization`**. On one RTX 5090
  (31.4 GiB usable, not 32), Qwen3.8-27B NVFP4 starts only with
  `--enforce-eager`; raising or lowering `--gpu-memory-utilization` does not help
  — 0.80 and 0.93 "both leave the same 47.06 MiB free, because that budget covers
  weights and KV while graph capture allocates outside it"
  [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B).

### 4.3 The throughput-vs-TPOT Pareto curve

The shape of the frontier, and how each optimization moves it:

| Optimization | Moves the frontier | Evidence |
|---|---|---|
| NVFP4 weights | out on both axes (bandwidth **and** capacity) | halves dispatch traffic, frees KV capacity [src](https://www.lmsys.org/blog/2026-02-20-gb300-inferencex/) |
| Speculative decoding γ small (MTP) | out on interactivity, roughly neutral on throughput | GB200 +14 % TPS/GPU; GB300 −0.9 % [src](https://www.lmsys.org/blog/2026-02-19-gb300-longctx/) |
| Speculative decoding γ large (DSpark) | strongly out at low concurrency, **inward** at high concurrency unless trimmed | §2.5 |
| Adaptive verification / trimming | keeps you on the frontier across the whole sweep | [src](https://vllm.ai/blog/2026-08-14-dspark-adaptive-verification) |
| DCP | out at high concurrency only | 6,091 vs 1,863 tok/s/GPU at c=512 [src](https://vllm.ai/blog/2026-08-07-decode-context-parallelism) |
| PD disaggregation | out above ~1000 GPUs; **inward** below | −20–30 % on small/untuned [src](https://towardsdatascience.com/disaggregation-is-a-thousand-gpu-problem/) |
| Larger CUDA-graph capture | trades ~5–10 % throughput for ~2–3× better p99 ITL | [src](https://github.com/vllm-project/vllm/issues/56975) |
| Prefix caching | out, unless prefix overlap ≈ 0 | +13–49 % vs −36.7 % [src](https://blog.squeezebits.com/vllm-vs-tensorrtllm-12-automatic-prefix-caching-38189) |

Kimi-K3 on GB300 NVL72 spans "high-throughput serving at 2K+ TPGS to low-latency
serving at 100+ TPS/user" on one Pareto curve
[src](https://vllm.ai/blog/2026-07-27-k3) — a ~20× throughput range across the
frontier for a single model on a single platform. **Picking the operating point
matters more than picking the GPU.**

### 4.4 MBU and MFU achieved

Direct published MBU/MFU figures per GPU generation are scarce in the 2026
literature. What *is* published is tokens/s/GPU, from which MBU can be backed out
given the model's per-step byte count and the GPU's HBM bandwidth. Per-GPU HBM
bandwidth figures live in this repo's per-GPU documents; use them with:

```
MBU = (bytes_per_decode_step(batch) × steps_per_s) / HBM_BW
    = bytes_per_decode_step × (tokens_per_s / batch) / HBM_BW
```

METHODOLOGY planning defaults stand until a measurement replaces them: MBU
0.6–0.8 on H100/H200, 0.5–0.7 on first-gen Blackwell software, 0.4–0.6 on
MI355X/ROCm; MFU 0.35–0.5 BF16 prefill, 0.25–0.4 FP8, 0.2–0.35 FP4. Corroborating
signals for those defaults:

- **H200 is at the ceiling**: "H200 TRT single node: no performance changes over
  4-month period (near theoretical peak)"
  [src](https://newsletter.semianalysis.com/p/inferencex-v2-nvidia-blackwell-vs).
  Hopper software is done improving; Blackwell is not.
- **Blackwell software was far from the ceiling**: GB200 NVL72 gained up to 8×
  tokens/GPU in under 4 months with no hardware change
  [src](https://www.lmsys.org/blog/2026-02-20-gb300-inferencex/). Any MFU number
  measured on Blackwell has a short shelf life.
- **ROCm lags on the stack, not the silicon**: MI355X still required a forked
  vLLM 0.10.1 build and crashed on 0.14/0.15.1; 0.16.0 was expected to deliver
  MI355X optimizations [src](https://newsletter.semianalysis.com/p/inferencex-v2-nvidia-blackwell-vs).
- **Kernel-level headroom is real**: SGLang's K3 kernel campaign added +19.9,
  +10.3, +27.6 and +10.4 tok/s across four eras of launch-elimination, NVIDIA
  kernels, communication fusion and overlap — from a ~64 tok/s base to ~113
  [src](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/). At bs=1 a
  2.8T model is "not compute-bound — it is a launch-count and latency problem",
  93 attention + 92 latent-MoE layers per token.

⚠️ **Per-GPU-generation MBU/MFU tables are TO BE VERIFIED.** No source found that
publishes them directly for 2026 hardware; the numbers above are the defaults
plus inference from throughput trends.

One concrete micro-optimization datum, because it illustrates where the time
actually goes at low batch: vLLM's dedicated K3 KDA metadata builder reduced
metadata-preparation latency at **batch size 1 by 96 %, from 870 µs to 34 µs**
[src](https://vllm.ai/blog/2026-07-27-k3). At bs=1, 870 µs of Python-assembled
metadata was a larger cost than most kernels.

---

## 5. Long context, 128K–1M

### 5.1 KV pressure — the four repo models compared

`kv_bytes_per_token` and totals, computed per METHODOLOGY §2:

| Model | Attention type | Bytes/token | Fixed state/seq | 8K | 32K | 128K | 1M |
|---|---|---|---|---|---|---|---|
| **DS-V4.1-Flash** | CSA2, FP4 main KV (E2M1, one E4M3 scale/16 ch), shared across layers | **890 B** — the **FP4-KV figure**, which needs the Blackwell FP4-KV kernel (METHODOLOGY §8); other KV dtypes scale up from it [src](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) | **2.77 MiB** — 128-slot FP8 SWA ring per layer capped at W=128, `128 × (512 + 512/32) = 67,584 B`, × 43 layers ([architecture.md](../models/deepseek41f/architecture.md) §5.3) | 0.007 GiB | 0.027 GiB | 0.109 GiB | **0.869 GiB** |
| **Kimi-K3** | 24 MLA + 69 KDA, TP=8 | **27,648 B/token** (MLA only) — `24 × (kv_lora_rank 512 + qk_rope 64) × 2 B` from `research/models/kimik3/config.json`; the published round figure is "about 27 KB" [src](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/) | **2.25 GB per request** = `S=5` SGLang state slots × **428.6 MiB**/slot (fp32, incl. conv), i.e. **280.9 MB/GPU = 0.262 GiB** at attnTp8 (5 × 53.6 MiB) — METHODOLOGY §2/§8, [architecture.md](../models/kimik3/architecture.md) §5.3. The blog's "about 54 MB under TP=8" is **one** slot per GPU | 0.473 GiB | 1.105 GiB | 3.637 GiB | **27.26 GiB** |
| **Qwen3.8-27B** | 16 full-attn of 64, GQA n_kv=4, head_dim=256 | 65,536 B BF16 / **32,768 B FP8** `est.` | **153.9 MB** GDN state per slot (fp32; 78.4 MB bf16) — METHODOLOGY §8; `S=1` assumed, ⚠️ MTP γ=3 adds verify slots | 0.50 / 0.25 GiB | 2.0 / 1.0 GiB | 8.0 / 4.0 GiB | 64 / **32 GiB** |
| Marlin-2B | Qwen3.5-2B derivative, 6 GQA layers | **12,288 B (12 KiB)/token BF16** — METHODOLOGY §8 | **18.63 MiB** GDN per seq — METHODOLOGY §8; per-mode video prompt overheads ⚠️ TBV (gated repo) | 0.112 GiB | 0.393 GiB | 1.518 GiB | 12.018 GiB |

Derivations, all run in `python3`:

- DS-V4.1-Flash: `131072 × 890 = 116.6 MB`; `1048576 × 890 = 933 MB`. The model
  card's own framing: "a full 1M-token prompt holds under 1 GB of global KV"
  [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash). This is ~**1/4
  of DS-V4-Flash** and, per the card's Figure 1(b), ~437× smaller than DeepSeek-V1
  [src](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash).
- Kimi-K3: `280,857,600 B / 27,648 B = 10,158 tokens` — **the KDA state costs the
  same as ~10,000 tokens of MLA KV**, not ~2,000. Below ~10K context the
  recurrent state dominates; above it, MLA does. At 1M context MLA KV is
  27.0 GiB/seq and KDA is ~1 %. (Corrected 2026-09-19 per METHODOLOGY §2: the
  per-sequence recurrent state must be multiplied by the engine's **slot count
  `S`**. SGLang's default `extra_buffer` allocates **S=5** slots per K3 request;
  one slot is `69 × (96 × 128 × 128 × 4 B + 9 × 96 × 128 × 2 B) = 449,372,160 B
  = 428.6 MiB` at attnTp=1, so a request holds **2.25 GB**; sharded at attnTp8 a
  slot is 56,171,520 B = 53.6 MiB, so **5 × 53.6 MiB = 280.9 MB per GPU**. The
  blog's "about 54 MB under TP=8" is one slot, i.e. `S=1`.
  [architecture.md](../models/kimik3/architecture.md) §5.3. The earlier version
  of this table used both 27,000 B/token and `S=1`, understating the state 5×.)
- Qwen3.8-27B: `2 × 4 × 256 × B × 16 layers`. Crossover: the **153.9 MB** GDN
  state (METHODOLOGY §8: `48 × 48 × 128 × 128 × 4 B = 151.0 MB` recurrent
  + ~2.9 MB conv state at `linear_conv_kernel_dim: 4`, fp32) equals FP8 KV of
  **4,697 tokens**. At TP2 that is 77.0 MB/seq/GPU, so 128 concurrent sequences
  reserve **9.17 GiB/GPU of pure recurrent state** before any KV. `est.` from
  `config.json` (`linear_num_value_heads: 48`, `linear_key_head_dim: 128`,
  `linear_value_head_dim: 128`, `mamba_ssm_dtype: float32`) at `S=1`; ⚠️ vLLM's
  Mamba-style cache allocates ≥1 slot **plus speculative slots**, so with MTP
  γ=3 the real reservation is higher (METHODOLOGY §2).

The recipe's measured startup KV pools on 2×RTX 5090 TP2 at 262,144 context
[src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B):

| Precision | KV tokens | weights/GPU | MTP acceptance |
|---|---|---|---|
| FP8 | 377,456 | 14.28 GiB | 0.771 |
| NVFP4 (Inferact) | 445,875 | 12.02 GiB | 0.897 |
| NVFP4 (unsloth, mixed FP8 channel-wise + 4-bit groups) | **920,517** | 10.64 GiB | 0.788 |

⚠️ Reconciling `377,456 tokens × 32,768 B = 12.4 GB` against 2×31.4 GiB usable
minus 2×14.28 GiB weights leaves headroom unaccounted for unless the recurrent
state pool is ~9.2 GiB — which matches the `est.` above closely enough to be
suggestive but not proof. **TO BE VERIFIED** against a startup log.

And on 1×5090 at 32K, the levers rank like this
[src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B): 91,022 tokens with
`--enforce-eager`; 135,926 adding `--language-model-only`; 152,917 also capping
`--max-num-seqs 8`; 76,458 with BF16 KV instead of FP8. "fp8 KV is a choice here,
not a requirement."

### 5.2 Sparse attention: what it does to prefill FLOPs and decode bytes

**DSA (DeepSeek Sparse Attention, V3.2-Exp lineage).** A lightning indexer scores
all preceding tokens with a multi-head ReLU-gated dot product, then top-k
selection restricts main attention to k positions. Per-layer core attention goes
from **O(L²) to O(Lk)** with k=2048 ≪ L
[src](https://www.emergentmind.com/topics/deepseek-sparse-attention-dsa). During
decode "the indexer scores all S tokens but only reads its small MQA keys, and
the MLA attention then runs over exactly topk tokens — a fixed cost regardless of
how long the context grows"
[src](https://www.tensoreconomics.com/p/deepseek-sparse-attention-from-first).

Kernels as of 2026-09-19: `deepseek-ai/DeepSelect` ships TopK kernels for DSA
achieving **2–20× over vanilla `torch.topk`**
[src](https://github.com/deepseek-ai/DeepSelect); LiteTopK fuses MQA scoring,
online filtering and TopK "without materializing the [num_q, seq_len] score
matrix" and is in vLLM as PR #48726
[src](https://github.com/vllm-project/vllm/pull/48726).

**CSA2 (DS-V4.1-Flash).** Each attention layer gets one of three static modes —
**Full**, **Reindex**, or **Reuse** — sharing main KV and indexer K across layers
and reusing Top-K sparse-attention indices. In the decoder a **Hierarchical
Sparse Indexer** restricts later indexing layers to a candidate pool built by the
first Full-Mode layer, "bounding deeper indexer cost independently of context
length" [src](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash).

From `research/models/deepseek41f/config.json` (repo), the mode map is literal:

| Field | Value | Meaning |
|---|---|---|
| `kv_source_layer_ids` | `[2, 8, 14, 20]` | **only 4 of 40 layers compress and own KV**; the rest read that cache |
| `index_source_layer_ids` | `[2, 8, 14, 20, 24, 28, 32, 36]` | 8 indexing layers |
| `index_n_heads` / `index_head_dim` / `index_topk` | 32 / 128 / **512** | indexer keeps best 512 latents per query |
| `candidate_source_layer_id` / `candidate_topk_blocks` / `candidate_block_size` | 20 / 2048 / 8 | pre-filter stage: 2048 blocks of 8 |
| `sliding_window` | **128** | every layer attends a 128-token local window |
| `compress_ratios` | mostly 1 and 2 (V4 used 4 and 128) | |
| `compress_rope_theta` | 160000 | compressed KV rotates at its own theta because one latent stands for several tokens |

Attention FLOPs under METHODOLOGY §4, `est.`:

```
dense causal:   ~2 × n_q_heads × head_dim × T²          per full-attn layer
sliding window: 4 × n_q_heads × head_dim × T × W        W=128
sparse top-k:   4 × n_q_heads × head_dim × T × k        k=512
```

For DS-V4.1-Flash at T=1M the sparse term is `T × 512` rather than `T²` — a
**2048× reduction** in the attention term at 1M context, before counting the
indexer's own cost. The indexer is not free: it scores all S tokens every step,
which is why `DeepSelect`/LiteTopK exist at all.

**SWA Bounded Replay** is the other half: it "reconstructs missing SWA KV states
by replaying only the most recent n_win tokens, avoiding the need to persist SWA
KV to SSD and reducing the persistent KV cache footprint to roughly **1/8** of
that of DeepSeek-V4-Flash" [src](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash).
This is what makes 890 B/token cacheable at all — and therefore what makes
DeepSeek's $0.003/M cache-hit price possible (§1.5).

**DeepSeek V4's numbers for comparison**: c4a (~1/4) and c128a (~1/128) KV
compression, top-k over compressed tokens, 128-token sliding window, "only 9.62
GiB KV cache per sequence at 1M context — about **8.7× smaller** than the 83.9
GiB estimate for a 61-layer DeepSeek V3.2-style stack"
[src](https://vllm.ai/blog/2026-04-24-deepseek-v4). vLLM's engineering answer was
a unified logical block size of 256 native token positions across all layers,
compressor state modelled as a sliding window, and five cache types collapsed
into three page-size buckets; kernel fusion of compression + norm + RoPE gave
**1.4–3×**, multi-stream overlap of indexer/compression/SWA gave 5–6 % at low
batch [src](https://vllm.ai/blog/2026-04-24-deepseek-v4).

### 5.3 Hybrid linear attention

| Model | Mix | Per-token KV | Per-seq state |
|---|---|---|---|
| Kimi-K3 | 69 KDA + 24 MLA of 93; `full_attn_layers` every 4th plus 93 | 27,648 B (MLA only) | **2.25 GB per request** (S=5 × 428.6 MiB); **280.9 MB/GPU** at attnTp8 |
| Qwen3.8-27B | 48 linear (Gated DeltaNet) + 16 full of 64, `full_attention_interval: 4` | 32,768 B FP8 (full-attn only) | **153.9 MB** per slot `est.` (fp32; 78.4 MB bf16) |

The trade is explicit: constant state instead of growing KV makes 1M context
affordable, but **the constant is large — and it is multiplied by the engine's
slot count `S`**. K3's KDA state at SGLang's default S=5 equals ~10,158 tokens of
MLA KV; Qwen3.8-27B's linear state equals 4,697 tokens of FP8 KV. For *short*
requests a hybrid model is **more** memory-hungry per sequence than a pure
attention model of the same width; the crossover is several thousand tokens, and
sizing it at S=1 understates the reservation fivefold on K3.

Consequences that show up in production:

- **Unified memory** (SGLang, `--enable-unified-memory`). Two pools sized at
  startup is "a bet on the traffic"; with one pool, KDA states fill from one end
  and MLA KV from the other, with a single free region between. Freeing a block
  in the middle moves a block from the end into the gap so the free region stays
  contiguous. 53.6 MiB-per-slot state blocks and 27,648 B token blocks share the
  same bytes with no common page size
  [src](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/).
- **Checkpointing, not caching** — §1.3.
- **ReplaySSM** for speculation — §2.6.
- **DCP does not apply to KDA**: one fixed-size matrix per request, no position
  axis to shard [src](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/).

### 5.4 Chunked prefill scheduling at long context

Measured on DeepSeek-R1-NVFP4, 128K prefill
[src](https://www.lmsys.org/blog/2026-02-19-gb300-longctx/):

| Strategy | GB300 TTFT | GB200 TTFT |
|---|---|---|
| No chunking | 15.2 s | 18.6 s |
| **32K dynamic chunking** | **8.6 s** | — |

Dynamic chunking with a 32K initial chunk nearly **halves** 128K TTFT. The
underlying FMHA kernel is 1.35× faster on GB300 (205 ms vs 277 ms), so most of
the 8.6 s figure is scheduling, not silicon.

vLLM's equivalent for K3 is the **adaptive scheduling budget**: "TTFT down
55–65 %, throughput up to 41.5 %"
[src](https://vllm.ai/blog/2026-09-13-kimi-k3-performance-optimization).

The composed result for Kimi-K3 on a B300 node, TP8, 8K/1K, eight-token DSpark,
vLLM v0.27.1 → commit 82a85dc1
[src](https://vllm.ai/blog/2026-09-13-kimi-k3-performance-optimization):

| Concurrency | Latency reduction | Throughput | TTFT improvement |
|---|---|---|---|
| 1 | −57.2 % | **2.2×** (83 → 183 tok/s) | −83.4 % |
| 4 | −55.6 % | **2.5×** (167 → 417 tok/s) | −72.3 % |
| 16 | −60.3 % | **2.8×** (258 → 725 tok/s) | −85.3 % |

Contributions: adaptive scheduling budget TTFT −55–65 %/throughput +41.5 %;
internal KDA prefix checkpoints TTFT −9–25 %; zero-copy mixed KDA batches
throughput +5.2–7.7 % at c=4 and c=16; deferred MXFP4 finalization e2e latency
−5 %; ReplaySSM effective capacity +10.97 %; DCP8 TPOT p50 13.8 → 10.5 ms at c=1.
⚠️ The post does not show how these compose to 2.8× — **TO BE VERIFIED**.

### 5.5 Offload as a long-context strategy

**Hybrid HiSparse** (vLLM, targeted at v0.30 — **not yet released**; latest is
0.29.0, 2026-09-09, [inference-engines.md §2.1](inference-engines.md)) keeps sparse-MLA KV on GPU while
capacity allows and offloads under pressure, with three residency states — full,
mixed (request tails on GPU, older pages on CPU with "hot buffers" for frequently
accessed tokens), and none. Hot-buffer pages are ordinary blocks from the Hybrid
Memory Allocator, so token-level reuse works across requests. Measured on 8×H200
with GLM 5.3 (142K max seq) on OpenHands multi-turn traces: substantially higher
concurrency at all context lengths vs standard KV offloading; the 512 GiB offload
budget splits 384 GiB HiSparse + 128 GiB traditional. Config: TP8, FP8 KV,
`max_num_seqs=256`, `max_num_batched_tokens=32768`,
`gpu_memory_utilization=0.92`, HiSparse host pool 384 GiB per DP replica. MTP3
integration supported with hot-buffer sizing adjusted for verification tokens
[src](https://vllm.ai/blog/2026-09-08-glm53-part1-hybrid-sparse-offloading).

---

## 6. Multimodal

### 6.1 Vision encoder cost

| Model | Tower | Tokens per image | Notes |
|---|---|---|---|
| DS-V4.1-Flash | 32-layer ViT, hidden 1024, patch 14, intermediate 2816, 3× downsampling aligner | **cap 1024**, min 295,936 pixels | **no limit on images per prompt** [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash) |
| Kimi-K3 | MoonViT3d, `vt_num_hidden_layers: 27`, `vt_hidden_size: 1024`, 12 heads, patch 14, 2×2 merge | ⚠️ TBV | <1B params vs ~2T backbone [src](https://vllm.ai/blog/2026-07-27-k3) |
| Qwen3.8-27B | `depth: 27`, hidden 1152, patch 16, `spatial_merge_size: 2`, `temporal_patch_size: 2` | ⚠️ TBV | `deepstack_visual_indexes: []` |
| Marlin-2B | Qwen3.5-2B visual tower kept intact, video-capable | ⚠️ TBV | gated repo |

`est.` cost of one DS-V4.1-Flash image, from `config.json` (repo), run in
`python3`:

```
ViT params/layer     = 3·1024² + 1024² + 5632·1024 + 1024·2816 = 12,845,056
ViT attn+mlp params  = 32 × 12,845,056 = 411.0 M
FLOPs per patch token = 2 × 411.0e6 = 0.82 GFLOP
at 9216 patches (1024 tokens × 3×3 downsample):
  GEMM term       = 7.58 TFLOP
  ViT self-attn   = 11.13 TFLOP   (full, quadratic in 9216)
  aligner         = 0.15 TFLOP
  total           ≈ 18.86 TFLOP per image
text-stack cost of the resulting 1024 tokens at 8B active = 16.4 TFLOP
```

(The earlier version of this block used `32 × (4·1024² + 2·1024·2816) = 319 M`,
which omits the SwiGLU **down** projection `1024·2816`; the fused `w1 (5632,1024)`
is gate+up only. Corrected against the per-tensor inventory in
[`architecture.md`](../models/deepseek41f/architecture.md) §6.4, which lands on
the same 411.0 M / 18.86 TFLOP.)

So **one image costs slightly more than its own text tokens do in the backbone**
— ≈1,200 prefill tokens' worth of FLOPs for 1,024 tokens of context. The ViT is
not a rounding error at 8B active params, unlike on a 2T backbone. The
non-windowed assumption is now **confirmed**: the ViT runs 9,216 tokens of dense
bidirectional attention with no sparsity
([architecture.md](../models/deepseek41f/architecture.md) §6.4, from the released
`inference/model.py`).

### 6.2 Encoder parallelism

The consistent recommendation across all three multimodal models here is
**data-parallel ViT, not tensor-parallel**:

- DS-V4.1-Flash: `--mm-encoder-tp-mode data` — "at 32 layers / hidden 1024 the
  encoder is small enough that TP communication costs more than it saves, which
  can significantly reduce TTFT for multi-image requests". **Mutually exclusive
  with `--language-model-only`**
  [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash).
- Kimi-K3: `--mm-encoder-tp-mode=data` is **default**, because the vision encoder
  has `head_size=12`, which cannot be sharded evenly under TP=8, and it is <1B
  params against a ~2T backbone [src](https://vllm.ai/blog/2026-07-27-k3).
- Qwen3.8-27B on Ascend: `--mm-encoder-tp-mode data` in the verified command
  [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B).

**Text-only serving.** `--language-model-only` on DS-V4.1-Flash "drops the ViT
and aligner from the load and frees that VRAM for KV cache" — the verified GB200
TP4 and 1P1D runs were text-only
[src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash). On a 32 GB RTX
5090 serving Qwen3.8-27B at 32K, adding `--language-model-only` took the KV pool
from 91,022 to 135,926 tokens — **+49 %**
[src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B). If the workload is text, turning
the vision tower off is one of the cheapest capacity wins available.

**EPD disaggregation.** SGLang separates encode from prefill from decode with
Mooncake as transfer backend [src](https://kvcache-ai.github.io/Mooncake/).
NVIDIA measures 30 % lower TTFT and 25 % higher throughput on image requests with
Qwen3-VL-30B-A3B-Instruct FP8 on GB200 under PD disaggregation
[src](https://rdp.in/gpu-mart/knowledge-base/disaggregated-inference-prefill-decode-2026/).

### 6.3 Video token budgets

⚠️ **Largely TO BE VERIFIED.** Qwen3.8-27B's `vision_config` gives
`temporal_patch_size: 2` and `spatial_merge_size: 2`, which sets the frame→token
ratio, but no published per-frame token budget for these specific checkpoints was
found. Marlin-2B's card is gated (HTTP 403 without `HF_TOKEN`
[src](https://huggingface.co/api/models/NemoStation/Marlin-2B)), so its video
token budget, frame sampling policy and `caption`/`find` mode prompt overheads
are unverified here. Marlin-2B is a Qwen3.5-2B fine-tune "with the video-capable
visual tower kept intact", exposing `caption` and `find` modes through
`modeling_marlin.py` [src](https://www.aimodels.fyi/models/huggingFace/marlin-2b-nemostation).

One relevant 2026 datum on the serving side: vLLM published multi-GPU video
captioning scaling with NVIDIA hardware video decoders via PyNvVideoCodec
[src](https://vllm.ai/blog/2026-09-18-pynvvideocodec) — for video workloads the
decode of the *video* can be the bottleneck before the model is.

---

## 7. Worked examples

All use METHODOLOGY §4 and §6 formulas. Cost figures are
`price_per_gpu_hour / (tokens_per_s_per_GPU × 3600) × 1e6`.

### 7.1 DS-V4.1-Flash — agentic coding, 128K in / 2K out, high cache hit

Configuration: 4×B300 TP4 (the recipe defaults to TP2 for B300 and recommends TP4
"for high interactivity"), `--language-model-only`, DSpark γ=5, prefix caching on,
adaptive verification on (NVIDIA only)
[src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash).

| Quantity | Value | Basis |
|---|---|---|
| Weights | **510.29 GB = 475.24 GiB** (`model.safetensors.index.json` `total_size`, METHODOLOGY §8; the recipe rounds this to "511 GB on disk (476 GiB)") | [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash) + index.json |
| Checkpoint format | routed experts **MXFP4** (E2M1 + one E8M0 per 32), everything else FP8 32×32 UE8M0 — *not* NVFP4 | recipe: "Routed expert weights are MXFP4"; METHODOLOGY §8 |
| `vram_minimum_gb` | 614 (= total × 1.2 headroom) | recipe |
| KV at 128K | 0.109 GiB/seq + 2.77 MiB SWA ring = **0.111 GiB/seq** (FP4 KV) | `131072 × 890` |
| KV at 1M | 0.869 GiB/seq + 2.77 MiB SWA ring = **0.872 GiB/seq** (FP4 KV) | `1048576 × 890` |
| `max_concurrency(128K)` | **≈ 917** `est.` — `kv_budget/GPU = 268 × 0.90 − 510.29/4 − 4 = 109.63 GB = 102.1 GiB`, ÷ 0.1113 GiB. (If CSA2's latent KV shards across TP ranks rather than replicating, ≈ 3,668 — ⚠️ TO BE VERIFIED which; the replicated reading is the binding one) | METHODOLOGY §3 |
| Prefill FLOPs, cold 128K | 2.097 PFLOP | `2 × 8e9 × 131072` |
| Prefill FLOPs at h=0.90 | 0.210 PFLOP | ×0.10 |
| Decode active params | 16B/token | model card |
| Drafter load | +14B params | recipe |

The striking property: **KV is not the binding constraint.** The recipe says it
outright — "Weights and batch size, not cache, set the capacity limit". At 128K
context, ~900 concurrent sequences hold ~98 GiB of global KV, which on a 4×B300
tray (4 × 268 GB, METHODOLOGY §8) is affordable; the 475.24 GiB of weights is what
forces TP4 in the first place. Per METHODOLOGY §3 the ceiling is
`max_concurrency(128K) ≈ 917`, so a throughput or cost row at batch 1,000 would be
**infeasible (KV)** — 900 is the largest round batch that fits.
This inverts the usual sizing exercise: for DS-V4.1-Flash you size for weights
and expert-parallel bandwidth, then discover you have KV headroom to spare.

Cache economics at h=0.90: prefill work drops 10×, and since the encoder half is
only 8B active, TTFT is dominated by the *indexer and candidate stages*, not the
GEMMs — ⚠️ the split between the two is **TO BE VERIFIED** (no published
profile found).

Cost sanity check against the vendor: DeepSeek sells V4-Flash at **$0.14/M**
input, $0.0030/M cached, $0.60/M output [src](https://deepseek.ai/pricing). At a
self-hosted 4×B300 tray, matching $0.60/M output requires
`4 × p / (0.60 × 3600 / 1e6)` aggregate output tok/s from the tray. Using only
published B300 rates from [cloud-pricing.md](cloud-pricing.md) §5.7 / §9.4:

| $/GPU-hr | source | aggregate tok/s needed | tok/s/GPU needed |
|---|---|---|---|
| $3.70 ⚠️ | on-prem 3-yr amortised (cloud-pricing.md §9.4, est.) | 6,852 | **1,713** |
| $7.40 | Hyperstack on-demand — cheapest reputable | 13,704 | **3,426** |
| $15.00 | OCI `BM.GPU.B300.8` — cheapest hyperscaler | 27,778 | **6,944** |
| $17.80 | AWS `p6-b300.48xlarge` on-demand | 32,963 | **8,241** |

For reference, vLLM measured 2,816 tok/s/GPU mixed 2K/1K for DeepSeek-V3.2 NVFP4
TP2 on GB300 [src](https://vllm.ai/blog/2026-02-13-gb300-deepseek). So DeepSeek's
list output price is **below** what a *rented* B300 tray can deliver at any
published cloud rate, and is only reachable at roughly on-prem amortised cost
(~1,713 tok/s/GPU is comfortably under the 2,816 reference). Either they own the
silicon, run at much higher utilisation, or the 1M-context V4.1-Flash
architecture buys more than V3.2 did. (The earlier version of this paragraph used
a hypothetical $6/GPU-hour, which is not a price anyone publishes for B300.)

### 7.2 Kimi-K3 — 1M-context agentic session

Minimum: 8×B300 or GB300 NVL72, or 16×B200
[src](https://vllm.ai/blog/2026-07-27-k3).

Per GPU under TP8 (MLA KV is **replicated** on every rank — §3.1 — so these are
both the per-GPU and the logical per-sequence figures; KDA state is sharded, at
`S=5` slots × 53.6 MiB = 280.9 MB/GPU, i.e. **2.25 GB per request** node-wide):

| Context | MLA KV/seq/GPU | + KDA state (S=5) | Total/seq/GPU |
|---|---|---|---|
| 8K | 0.211 GiB | 0.262 GiB | 0.473 GiB |
| 32K | 0.844 GiB | 0.262 GiB | 1.105 GiB |
| 128K | 3.375 GiB | 0.262 GiB | 3.637 GiB |
| 1M | 27.000 GiB | 0.262 GiB | **27.26 GiB** |

Fit on an 8×B300 node (METHODOLOGY §3 and §8: **268 GB/GPU, 2,144 GB/node**;
Kimi-K3 checkpoint **1,560.9 GB = 195.1 GB/GPU**):

```
usable_hbm/GPU  = 268 × 0.90                       = 241.2 GB
per_gpu_weights = 1,560.9 / 8                      = 195.1 GB
activation_ws                                      ≈   4   GB
kv_budget/GPU                                      =  42.09 GB = 39.20 GiB
```

| Context | TP8 (MLA replicated) | DCP8 (MLA position-sharded) |
|---|---|---|
| 128K | **10** concurrent | **57** concurrent |
| 1M | **1** concurrent | **10** concurrent |

So at 1M context a single 8×B300 node serves **~1 sequence under plain TP8 and
~10 with DCP8** `est.` — the ~8× step matches SGLang's measured "about 7.9× on K3
with DCP8" [src](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/).
**DCP is not an optimization here, it is the difference between one user and ten.**

**Corrected 2026-09-19 (second pass).** The previous figures (744 GB free, ~26
sequences, ~200 with DCP) were wrong three ways: they used a "~1.4 TB" checkpoint
instead of the pinned **1,560.9 GB**, skipped METHODOLOGY §3's 0.90 usable factor
and activation workspace, and counted the KDA state at `S=1` (54 MB) instead of
`S=5` (280.9 MB/GPU). They also treated MLA KV as if it were sharded under TP8,
which §3.1 of this very document says it is not. The earlier pass had already
corrected the node total from the repo README's **2,304 GB** (= 8 × 288 GB, the
*physical/marketing* per-die figure; [`research/gpus/b300.md`](../gpus/b300.md):
*"that configuration does not exist as a shipping 8-GPU product"*) to
**2,144 GB**. METHODOLOGY §8 now pins HGX/DGX/AWS p6-b300 at **268 GB/GPU**
(DGX B300 262.5); the **279 GB/GPU** figure quoted in that earlier note belongs to
**GB300 NVL72** (288 GB nameplate, ≈279 usable), a different product, and must not
be mixed into an HGX B300 node total. ⚠️ Residual uncertainty: the activation
workspace is an estimate, not a measurement.

Stack to turn on, in descending order of measured value:

1. `--enable-prefix-caching` — **not on by default for K3**
   [src](https://vllm.ai/blog/2026-07-27-k3).
2. DSpark γ=7 — 3.14× at bs=1 [src](https://vllm.ai/blog/2026-07-27-k3).
3. PD disaggregation with PP8 prefill → DCP8/TP8 decode — 2,808 tok/s/GPU
   composed [src](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/).
4. Mooncake for cross-replica cache — 1.7 %→92.2 % hit rate on real agent traces
   [src](https://vllm.ai/blog/2026-05-06-mooncake-store).
5. `VLLM_PREFIX_CACHE_RETENTION_INTERVAL=0` for multi-turn chat
   [src](https://vllm.ai/blog/2026-07-27-k3).
6. Verify trimming / adaptive verification once concurrency >8
   [src](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/).

Cost, `est.` at published throughputs. Prices are rows from
[cloud-pricing.md](cloud-pricing.md) §5.7 / §5.9 only — no hypotheticals:

| Operating point | aggregate tok/s | **tok/s/GPU** | $7.40 (Hyperstack B300) | $15.00 (OCI B300) | $17.80 (AWS p6-b300) | $18.00 (OCI GB300) |
|---|---|---|---|---|---|---|
| bs=1, TP8, DSpark (max interactivity) | 331 on 8 GPUs | **41.4** | $49.68/M | $100.70/M | $119.50/M | $120.85/M |
| PD-disagg max throughput | 2,808 per GPU | **2,808** | $0.732/M | $1.484/M | $1.761/M | $1.781/M |

**⚠️ Corrected 2026-09-19**: the previous grid treated the bs=1 figure of
331 tok/s as *per GPU*. It is not — it is the output rate of a single stream on an
8-GPU TP8 node [src](https://vllm.ai/blog/2026-07-27-k3), i.e. **41.4 tok/s/GPU**,
so the bs=1 row was understated 8×.

**68× cost spread on the same hardware and model** (2,808 / 41.4), purely from
where you sit on the Pareto curve. Compare to Moonshot's list $15/M output
[src](https://platform.kimi.ai/docs/pricing/chat-k3) — at the cheapest published
B300 rate ($7.40) and the max-throughput point, the gross margin is **~95 %**; at
the bs=1 interactive point the cost is **$49.68/M against a $15/M list price**,
i.e. serving single-stream max-interactivity K3 at list is a **loss** at every
published GPU-hour rate. That is the structure SemiAnalysis measures on
OpenRouter providers — Crusoe ~83 % gross margin on input, 45 % on output
[src](https://newsletter.semianalysis.com/p/inferencex-v2-nvidia-blackwell-vs) —
and it is why nobody sells bs=1 at list.

### 7.3 Qwen3.8-27B — single-GPU, 262K context

Because it is dense and small, the optimization ranking inverts:

| Optimization | Value here | Why |
|---|---|---|
| Weight quant choice | **largest single lever** | unsloth NVFP4 gives 920,517 KV tokens vs 377,456 FP8 — **2.44×** [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) |
| `--language-model-only` | +49 % KV pool at 32K on 1×5090 | [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) |
| MTP γ=3 | 0.754–0.897 acceptance | [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) |
| FP8 KV | **1.19×** KV tokens measured (76,458 → 91,022 at 32K, 1×5090) — *not* the naive 2×, because the 153.9 MB/slot recurrent-state pool (METHODOLOGY §8) does not shrink with KV dtype | [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) |
| CUDA graphs | **negative on 1×5090** — NVFP4 only fits with `--enforce-eager` | [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) |
| EP / wide-EP / DeepEP | **N/A** — dense model | — |
| PD disaggregation | **N/A** at this scale | thousand-GPU problem |
| DCP | GQA caps DCP at `TP // n_kv_heads` = `TP // 4` | [src](https://vllm.ai/blog/2026-08-07-decode-context-parallelism) |

Measured single-stream decode on Ascend 950PR TP1 with MTP on: **64 tok/s** for a
2048-token completion [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B).

DFlash2 is available as an external drafter at γ=7
(`incoai/Qwen3.8-27B-DFlash2`, needs vLLM ≥0.28.0) — a longer block than the
in-checkpoint MTP's 3, so per §2.5 it will be better at bs=1 and worse under load
unless trimmed [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B).

Gotcha that costs more than any of the above: `--reasoning-parser qwen3` is "not
optional in practice: the chat template opens every assistant turn with
`<think>`, so without it the entire reasoning block lands in `message.content`
and a 2048-token budget can be spent before the answer starts"
[src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B). The same class of trap exists on
DS-V4.1-Flash, where "with both keys unset, thinking is ON at effort 50. The
do-nothing config is the most verbose one"
[src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash). **Reasoning-budget
misconfiguration wastes more output tokens than any serving optimization saves.**

### 7.4 Marlin-2B — video captioning

**5.444 GB** BF16 weights (METHODOLOGY §8; 2.21 B unique params, 2.72 B on disk
with the tied embedding duplicated), gated repo. KV is 12,288 B/token BF16 across
6 GQA layers plus an 18.63 MiB GDN state per sequence (§5.1). Serving
optimizations that matter at 2B:

- Prefix caching: low value — video prompts share little prefix beyond the
  canonical per-mode prompt that `modeling_marlin.py` wraps
  [src](https://www.aimodels.fyi/models/huggingFace/marlin-2b-nemostation).
- Speculative decoding: ⚠️ no drafter published for Marlin-2B — **TO BE
  VERIFIED**. Qwen3.5-2B drafters may or may not transfer to the fine-tune.
- Parallelism: TP1. Scale out by replicas.
- **The real optimization is video decode**, not LLM serving — see vLLM's
  PyNvVideoCodec multi-GPU video captioning work
  [src](https://vllm.ai/blog/2026-09-18-pynvvideocodec).
- Batching: many small requests; `--max-num-seqs` high, CUDA graphs on,
  `--max-num-batched-tokens` sized to the per-clip token budget (⚠️ TBV).

---

## 8. Decision checklist

Ordered by measured value-per-unit-effort, from the tables above.

1. **Turn prefix caching on** unless prefix overlap ≈ 0 (then measure the
   overhead — it was 36.7 % throughput on 2024-era vLLM).
2. **Make the cache cross-replica** (Mooncake / KVBM / LMCache / vLLM tiered).
   Biggest single measured win in this document: 1.7 % → 92.2 % hit rate.
3. **Check reasoning-budget and parser config.** Costs nothing, saves more output
   tokens than most kernels.
4. **Turn speculation on and measure acceptance from the engine's own counters**,
   not from throughput. Short blocks (γ≈1–3) if you serve at batch; long blocks
   (γ≈5–8) only with adaptive verification / trimming.
5. **Split parallelism by phase** once you are past one node: PP for prefill,
   TP or DCP for decode, EP for the MoE.
6. **DCP before more GPUs** when long-context concurrency is the limit — 3.3×
   at c=512 on K2.6/B200, and on an MLA model it is the difference between
   replicating KV on every TP rank and sharding it: ~1 vs ~10 concurrent
   1M-context K3 sequences on one 8×B300 node (§7.2).
7. **Size CUDA-graph capture to `max_num_seqs × (1 + γ)`**, then tune it against
   your p99 ITL target, not your throughput target.
8. **Dynamic chunked prefill at long context** — 128K TTFT 15.2 s → 8.6 s.
9. **`--mm-encoder-tp-mode data`** for any vision tower under ~1B params;
   `--language-model-only` if the traffic is text.
10. **PD disaggregation last**, and only above roughly a thousand GPUs.

---

## Open questions

Consolidated ⚠️ TO BE VERIFIED items.

1. **vLLM 0.30-era APC overhead on zero-overlap traffic.** The −36.7 % throughput
   / +25.0 % TPOT figure is vLLM v0.6.3 on A100 from the SqueezeBits study. No
   2026-software measurement of the no-shared-prefix case was found.
2. **Per-GPU-generation MBU and MFU tables.** No 2026 source publishes achieved
   MBU/MFU per GPU generation directly. METHODOLOGY defaults stand; back them out
   from tokens/s/GPU and per-GPU HBM bandwidth in this repo's per-GPU docs.
3. **SGLang MTP on GB300 NVL72.** Listed as roadmap in the Feb 2026 InferenceX
   post; whether it landed by 2026-09-19 is unverified.
4. **Kimi-K3 DSpark Markov head rank.** DS-V4.1-Flash's is 256
   (`dspark_markov_rank`, repo config). K3's draft is described only as "a
   low-rank Markov head"; the rank is not published.
5. **ReplaySSM capacity gain discrepancy.** SGLang reports 32× draft-window
   memory reduction "lifting the concurrency ceiling several-fold"; vLLM reports
   "effective capacity rises 10.97 % under TP8". Different baselines, not
   reconciled.
6. **Kimi-K3 2.8× throughput decomposition.** The per-optimization contributions
   published do not obviously compose to the headline 2.8×.
7. ~~**DS-V4.1-Flash SWA KV bytes per token.**~~ **Resolved 2026-09-19.** The
   890 B/token figure is the *global* compressed KV; the fixed 128-token sliding
   window per layer is additional and is **67,584 B/layer**
   (`128 slots × (512 × 1 B FP8 + 512/32 × 1 B E8M0 scale)`), **2.77 MiB per
   sequence** across 40 backbone + 3 DSpark layers — derived from the released
   `inference/model.py` in
   [`architecture.md`](../models/deepseek41f/architecture.md) §5.3. Still
   unpublished by DeepSeek directly.
8. **Qwen3.8-27B recurrent-state pool size.** The pinned **153.9 MB/slot**
   (METHODOLOGY §8 = `48 × 48 × 128 × 128 × 4 B` recurrent + ~2.9 MB conv state)
   plus 377,456 FP8 KV tokens roughly accounts for the 2×RTX 5090 memory budget,
   but no startup log confirms the split, and the engine's slot count `S` for
   this model is unpublished (vLLM's Mamba cache allocates ≥1 plus speculative
   slots, so MTP γ=3 raises it).
9. ~~**DS-V4.1-Flash ViT attention shape.**~~ **Resolved 2026-09-19.** The ViT
   runs full quadratic *dense bidirectional* self-attention over 9,216
   pre-downsample patches, no windowing
   ([architecture.md](../models/deepseek41f/architecture.md) §6.4). The `est.`
   itself was **corrected 17.0 → 18.86 TFLOP/image**: the old ViT param count
   omitted the SwiGLU down projection (§6.1).
10. **TTFT split between indexer/candidate stages and GEMMs** for DS-V4.1-Flash
    CSA2 prefill. No published profile.
11. **Kimi-K3 and Qwen3.8-27B vision token budgets per image/frame**, and
    **Marlin-2B's entire multimodal cost model** — gated repo, HTTP 403 without
    `HF_TOKEN`.
12. **Marlin-2B speculative drafter.** None published; whether a Qwen3.5-2B
    drafter transfers to the fine-tune is untested here.
13. **Elastic EP measured throughput.** vLLM's post describes the mechanism with
    no performance numbers.
14. ~~**8×B300 node HBM total.**~~ **Resolved 2026-09-19.** METHODOLOGY §8 pins
    HGX B300 / DGX B300 / AWS `p6-b300` at **268 GB per GPU = 2,144 GB per
    8-GPU node** (DGX B300 262.5), *not* 288 GB or 2,304 GB — that is the die
    nameplate. The **288 GB (≈279 usable)** figure belongs to **GB300 NVL72**, a
    separate product; the earlier note in §7.2 mixed the two. §7.2 now sizes
    against 2,144 GB with METHODOLOGY §3's 0.90 usable factor. Residual: the
    activation-workspace term is an estimate.
15. **InferenceX #3058 raw result tables.** The DS-V4.1-Flash recipe points at
    them for the MI355X numbers; the tables themselves were not retrieved, so no
    MI355X tokens/s figure for this model appears above.
16. **DS-V4.1-Flash-NVFP4 vs MXFP4 serving delta.** The repo carries both. The
    **base checkpoint's routed experts are MXFP4** (E2M1 + one E8M0 per 32, at
    0.53125 B/param), confirmed verbatim by the vLLM recipe — "Routed expert
    weights are MXFP4" — **not** NVFP4. NVIDIA's `deepseek41fnvfp4` build (NVFP4
    g16 experts, 0.5625 B/param, `ignore: ["*.attn.*","*.ffn.shared_experts.*",
    "head","mtp.*"]`) is therefore **larger, not smaller**: **527.27 GB vs
    510.29 GB** (METHODOLOGY §8), only 58 % of its bytes are NVFP4, and it is
    **accuracy-neutral with no published speedup over the base** ⚠️. No published
    head-to-head throughput comparison for this pair was found; the general
    InferenceX finding is that B200/GB300 NVFP4 beats MI355X MXFP4 when composed
    with disagg + wideEP, which is a different question.

---

## Sources

- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash
- https://recipes.vllm.ai/Qwen/Qwen3.8-27B
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash
- https://huggingface.co/api/models/NemoStation/Marlin-2B
- https://www.aimodels.fyi/models/huggingFace/marlin-2b-nemostation
- https://vllm.ai/blog/2026-07-27-k3
- https://vllm.ai/blog/2026-07-22-kimi-k3-preview
- https://vllm.ai/blog/2026-09-13-kimi-k3-performance-optimization
- https://vllm.ai/blog/2026-09-15-kimi-k3-dspark
- https://vllm.ai/blog/2026-08-14-dspark-adaptive-verification
- https://vllm.ai/blog/2026-08-23-speculative-decoding-amd-gpus
- https://vllm.ai/blog/2026-08-07-decode-context-parallelism
- https://vllm.ai/blog/2026-09-10-tiered-kv-offloading
- https://vllm.ai/blog/2026-01-08-kv-offloading-connector
- https://vllm.ai/blog/2026-05-06-mooncake-store
- https://vllm.ai/blog/2026-05-14-elastic-expert-parallelism
- https://vllm.ai/blog/2026-04-07-moriio-kv-connector
- https://vllm.ai/blog/2026-04-24-deepseek-v4
- https://vllm.ai/blog/2026-02-13-gb300-deepseek
- https://vllm.ai/blog/2026-09-08-glm53-part1-hybrid-sparse-offloading
- https://vllm.ai/blog/2026-09-18-pynvvideocodec
- https://github.com/vllm-project/vllm/issues/56975
- https://github.com/vllm-project/vllm/pull/48726
- https://docs.vllm.ai/en/latest/serving/expert_parallel_deployment/
- https://docs.vllm.ai/en/stable/configuration/optimization/
- https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/
- https://www.lmsys.org/blog/2026-02-19-gb300-longctx/
- https://www.lmsys.org/blog/2026-02-20-gb300-inferencex/
- https://www.lmsys.org/blog/2024-01-17-sglang/
- https://docs.sglang.io/advanced_features/expert_parallelism.html
- https://newsletter.semianalysis.com/p/inferencex-v2-nvidia-blackwell-vs
- https://newsletter.semianalysis.com/p/inferencemax-open-source-inference
- https://developer.nvidia.com/blog/nvidia-blackwell-ultra-sets-new-inference-records-in-mlperf-debut/
- https://developer.nvidia.com/blog/introducing-new-kv-cache-reuse-optimizations-in-nvidia-tensorrt-llm/
- https://developer.nvidia.com/blog/5x-faster-time-to-first-token-with-nvidia-tensorrt-llm-kv-cache-early-reuse/
- https://docs.nvidia.com/dynamo/v1.3.0/user-guides/kv-cache-offloading
- https://docs.nvidia.com/dynamo/v1.2.1/components/kvbm
- https://docs.nvidia.com/dynamo/dev/backends/sg-lang/disaggregation
- https://github.com/NVIDIA/TensorRT-LLM/issues/14918
- https://nvidia.github.io/TensorRT-LLM/latest/features/kvcache.html
- https://github.com/kvcache-ai/Mooncake/blob/main/README.md
- https://kvcache-ai.github.io/Mooncake/
- https://arxiv.org/pdf/2407.00079
- https://arxiv.org/pdf/2510.09665
- https://arxiv.org/abs/2601.11580
- https://arxiv.org/pdf/2503.01840
- https://blog.lmcache.ai/en/2026/05/12/benchmarking-lmcache-for-multi-turn-agentic-workloads-on-amd-mi300x/
- https://blog.lmcache.ai/en/2026/04/03/lmcaches-new-architecture-boosts-moe-inference-performance-by-10x/
- https://www.vastdata.com/blog/nvidia-dynamo-vast-scalable-optimized-inference
- https://blog.squeezebits.com/vllm-vs-tensorrtllm-12-automatic-prefix-caching-38189
- https://packet.ai/blog/vllm-prefix-caching
- https://www.spheron.network/blog/vllm-vs-sglang-2026/
- https://www.spheron.network/blog/eagle-3-speculative-decoding-gpu-cloud/
- https://www.spheron.network/blog/wide-expert-parallelism-eplb-moe-inference-gpu-cloud/
- https://www.spheron.network/blog/deepseek-sparse-attention-long-context-llm-gpu-cloud/
- https://www.spheron.network/blog/mlperf-inference-v6-benchmark-results-2026/
- https://towardsdatascience.com/disaggregation-is-a-thousand-gpu-problem/
- https://rdp.in/gpu-mart/knowledge-base/disaggregated-inference-prefill-decode-2026/
- https://github.com/deepseek-ai/DeepSelect
- https://www.tensoreconomics.com/p/deepseek-sparse-attention-from-first
- https://www.emergentmind.com/topics/deepseek-sparse-attention-dsa
- https://arxiv.org/html/2607.11976
- https://deepseek.ai/pricing
- https://platform.kimi.ai/docs/pricing/chat-k3
- https://platform.claude.com/docs/en/about-claude/pricing
- https://developers.openai.com/api/docs/pricing
- https://www.alibabacloud.com/help/en/model-studio/context-cache
- https://emergent.sh/learn/kimi-k3-pricing
- https://devtoollab.com/blog/prompt-caching-guide
- https://www.siliconreport.com/nvidia-gb300-nvl72-vs-amd-mi355x-inference-e6f7d36e
- https://amd.com/en/blogs/2026/amd-delivers-breakthrough-mlperf-inference-6-0-results.html

---

## Verification log (2026-09-19)

Adversarial re-check of this document. Every claim below was verified against a
**primary source fetched independently of this document's own citation**, or
recomputed with `python3` from `research/METHODOLOGY.md` and the repo
`config.json` files. Source links are the ones actually opened.

### CORRECTED (9)

| # | Claim | Old → New | Source opened |
|---|---|---|---|
| 1 | DeepSeek-V4-Flash API input price, §1.5 + §7.1 | **$0.15/M → $0.14/M**; cache discount **50× → 46.7×** | [deepseek.ai/pricing](https://deepseek.ai/pricing) |
| 2 | Blended $/1M input for DeepSeek-V4-Flash, §1.5 | h=50/80/90/95 %: **0.0765/0.0324/0.0177/0.0104 → 0.0715/0.0304/0.0167/0.0099** (cascade from #1; `(1−h)·0.14 + h·0.003`) | recomputed, `python3` |
| 3 | TRT-LLM KV-reuse speedup row, §1.2 | "~2× typical; NVIDIA claims up to 5×" cited to the block-reuse post → **5× re-sourced** to the early-reuse post (Llama-70B, H100, system-prompt burst, *"up to"*); **"~2× typical" now marked ⚠️ TO BE VERIFIED** — it is in neither post | [5x-faster-ttft post](https://developer.nvidia.com/blog/5x-faster-time-to-first-token-with-nvidia-tensorrt-llm-kv-cache-early-reuse/); [block-reuse post](https://developer.nvidia.com/blog/introducing-new-kv-cache-reuse-optimizations-in-nvidia-tensorrt-llm/) |
| 4 | DeepSeek-R1 MTP block depth in the §2.5 speedup model | **γ=1 → γ=3** (source reads `accept length=2.37@MTP3`); derived row **2.37 / 1.58 / 1.19 → 2.37 / 0.95 / 0.59** | [lmsys GB300 long-context](https://www.lmsys.org/blog/2026-02-19-gb300-longctx/) |
| 5 | Kimi-K3 MLA KV bytes/token, §5.1 + §5.3 + §7.2 | **27,000 B (round "27 KB") → 27,648 B exact** = `24 × (512 + 64) × 2 B`; totals **0.256/0.874/3.346/26.42 → 0.261/0.894/3.426/27.05 GiB**; crossover **2,000 → 1,963 tokens** | recomputed from `research/models/kimik3/config.json`; round figure confirmed at [lmsys K3 day-0](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/) |
| 6 | 8×B300 usable HBM for the K3 1M example, §7.2 | **2,304 GB → ~2,144 GB**; free for KV **900 → 744 GB**; concurrency **~34 → ~26**; with DCP8 **~270 → ~200** | [`research/gpus/b300.md`](../gpus/b300.md) (*"that configuration does not exist as a shipping 8-GPU product"*; 262.5–279 GB/GPU reported) |
| 7 | DS-V4.1-Flash ViT cost per image, §6.1 | params **319 M → 411.0 M**, FLOPs/patch **0.64 → 0.82 GFLOP**, GEMM **5.88 → 7.58 TFLOP**, total **17.0 → 18.86 TFLOP** (old formula omitted the SwiGLU down projection `1024·2816`) | recomputed from `research/models/deepseek41f/config.json`; matches [`architecture.md`](../models/deepseek41f/architecture.md) §6.4 |
| 8 | Qwen3.8-27B FP8-KV lever, §7.3 | **"2× KV tokens" → 1.19×** — the cited 76,458 → 91,022 is 1.19×, not 2×; the 151 MB/seq recurrent pool does not shrink with KV dtype | [vLLM Qwen3.8-27B recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) (numbers confirmed verbatim) |
| 9 | vLLM `--all2all-backend` list + EPLB default, §3.3 | added missing **`flashinfer_nvlink_two_sided`**; `num_redundant_experts` **"—" → 0** | [vLLM EP deployment docs](https://docs.vllm.ai/en/latest/serving/expert_parallel_deployment/) |

### CONFIRMED (31)

| Claim | Source opened |
|---|---|
| DS-V4.1-Flash global KV = **890 B/token** | [model card](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash); independently re-derived 720 (main, FP4 E2M1 + E4M3/16) + 170 (indexer, MXFP4) = 890 |
| DS-V4.1-Flash **552B backbone + 196B Engram, 8B active/prefill token, 16B/decode token** | [model card](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash); reproduced to 7.89B / 16.11B from `config.json` |
| SWA Bounded Replay cuts persistent KV to **~1/8 of DeepSeek-V4-Flash**; global KV **~1/4 of V4-Flash, ~437× vs V1** | [model card](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) — both figures are real and measure *different* things; no contradiction |
| V4.1 **dropped the MTP module**; DSpark is the only speculative method | [vLLM DS-V4.1-Flash recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash); reconciled with `num_nextn_predict_layers: 3` (= DSpark's 3 stages) — clarifying note added to §2.1 |
| DSpark config: `block_size 5`, `markov_rank 256`, target layers 37/38/39, 128 experts top-3, noise token 128799, drafter **~14B params** | `research/models/deepseek41f/config.json` + [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash) |
| DS-V4.1-Flash weights **511 GB / 476 GiB**, `vram_minimum_gb: 614`, TP4 for B300 high-interactivity, CUDA-graph `128 × 6 = 768 → 1024`, ROCm adaptive-verification refusal, golden acceptance **3.51** @ InferenceX #3058 | [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash) |
| DS-V4.1-Flash image cap **1024 tokens**, min **295,936 px**, no per-prompt image limit; `--mm-encoder-tp-mode data`; `--language-model-only` | [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash) |
| DeepSeek-V4-Pro **$0.660 / $0.0220 / $1.98** | [deepseek.ai/pricing](https://deepseek.ai/pricing) |
| Kimi **K3 $3.00/$0.30/$15.00**, K2.7-Code $0.95/$0.19/$4.00, K2.7-Code-Highspeed $1.90/$0.38/$8.00, K2.6 $0.95/$0.16/$4.00, no storage fee | [platform.kimi.ai](https://platform.kimi.ai/docs/pricing/chat-k3) |
| Claude **Opus 5 $5/$0.50/$25**, **Sonnet 5 $2/$0.20/$10**, **Fable 5.1 $10/$0.25/$50 (0.025× read)**, **Haiku 4.5 $1/$0.10/$5** | [platform.claude.com pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| Anthropic cache multipliers **1.25× (5 min) / 2× (1 h) / 0.1× read**, and the break-evens **1 read / 2 reads** | [platform.claude.com pricing](https://platform.claude.com/docs/en/about-claude/pricing) — stated verbatim; `n = (w−1)/(1−r)` re-derived: 0.278, 1.111, 0.256 |
| **gpt-5.6-sol $4/$0.40/$20**, gpt-5.6-terra $2/$0.20/$12, gpt-5.4 $2.50/$0.25/$15, gpt-5-nano $0.05/$0.005/$0.40 | [developers.openai.com pricing](https://developers.openai.com/api/docs/pricing) |
| Alibaba implicit **0.2×** (deepseek-v4.1-flash **0.1×**, GLM 0.20–0.25×); explicit **1.25× write / 0.1× read**; **1,024-token** minimum prefix both; explicit TTL **5 min resetting on hit**; implicit TTL unspecified; **no storage fee** | [Model Studio context cache](https://www.alibabacloud.com/help/en/model-studio/context-cache) |
| OpenRouter **92 % cache hit rate on Kimi-K3 traffic** | [emergent.sh](https://emergent.sh/learn/kimi-k3-pricing) |
| All other blended-cost rows in §1.5 (Pro, K3, Opus 5, Fable 5.1, gpt-5.6-sol, gpt-5.4) | recomputed, `python3` — exact |
| SqueezeBits APC study: **A100-SXM-80G, Llama-3.1-8B BF16, vLLM v0.6.3 / TRT-LLM v0.15.0**; +34.7 %/+20.9 %, +13.3 %/+9.8 %, +49 %/+32 % sweep, **−36.7 % throughput / +25.0 % TPOT** on random prompts | [squeezebits](https://blog.squeezebits.com/vllm-vs-tensorrtllm-12-automatic-prefix-caching-38189) |
| KV offload: **83.4 GB/s** DMA, 68.5 GB/s kernel, ~50 GB/s single-direction on H100; **2×–22×** TTFT; **9×** throughput; 0.11→0.12 **4×/5×** from block size 32 KB → 2 MB; `--kv_offloading_backend native` | [vLLM KV offloading connector](https://vllm.ai/blog/2026-01-08-kv-offloading-connector) |
| Mooncake: Kimi-2.5 NVFP4, 12×GB200 1P1D, Codex/SWE-bench-Pro traces — hit rate **1.7 % → 92.2 %**, throughput **3.8×**, TTFT p50 **46×**, e2e **8.6×**; 12→60 GB200 holds **>95 %**, near-linear | [vLLM Mooncake Store](https://vllm.ai/blog/2026-05-06-mooncake-store) |
| DCP: Kimi-K2.6 NVFP4 on **8×B200**, **6,091 vs ~1,863 tok/s/GPU at c=512**, 82 % KV utilisation; MLA needs `TP ≥ DCP` **and** `TP % DCP == 0`; GQA caps at `TP // n_kv_heads` | [vLLM DCP blog](https://vllm.ai/blog/2026-08-07-decode-context-parallelism) |
| GB300 vs GB200: **288 GB vs 192 GB**; 226.2/147.9 and 224.2/169.1 TPS/GPU; 23→43 TPS/user; 128K TTFT 15.2/18.6 s → **8.6 s** with 32K dynamic chunking; FMHA 205/277 ms; DEP16 576/320 batch; 40/24 and 36/20 req/GPU; LongBench-v2 **56.9 % vs 57.2 %**; EP16/TP16 decode + PP4 prefill, FP8 attention & KV | [lmsys GB300 long-context](https://www.lmsys.org/blog/2026-02-19-gb300-longctx/) |
| Kimi-K3 on vLLM: prefix caching **off by default**, `VLLM_PREFIX_CACHE_RETENTION_INTERVAL` PRs **#43447 / #45845**, DSpark **118→370** (TP16) and **111→331** (TP8) tok/s, accept **4.73 coding / 2.61 creative**, `flashinfer_nvlink_one_sided` + `deepep_v2` + `deep_gemm_mega_moe`, KDA metadata **870 µs → 34 µs (−96 %)**, DCP prototype **+40 % vs TP8**, minimum **8×B300 / GB300 NVL72 / 16×B200**, the DSpark serve JSON verbatim | [vLLM K3 blog](https://vllm.ai/blog/2026-07-27-k3) |
| Kimi-K3 on SGLang: MLA "about 27 KB", KDA "about 54 MB under TP=8", **DCP8 ≈7.9×**, **PP8 = 1.45–1.72× TEP8**, **2,808 tok/s/GPU** composed, accept ~2.7 chat / ~5.0 math, trimming **+68 % / +24 % at bs=256** (accept 2.7→2.2, 5.0→4.3), ReplaySSM **512 KB → 16 KB (~32×)**, DP-attention **61 GB KDA + 11 GB MLA**, kernel campaign **64 → ~113 tok/s** | [lmsys K3 day-0](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/) |
| GB300 vLLM: **v0.14.1 / CUDA 13.0**, V3.2 NVFP4 TP2 **7,360 / 2,816 TGS**, R1 NVFP4 EP2 **22,476 / 3,072 TGS**, **8× vs H200** prefill and **20×** mixed 2K/128, **+14 % vs B300** prefill, TP2 "50 % to 2×" worse TPOT than EP2, MTP acceptance **>80 %**, disagg TPOT **<60 ms vs >80 ms** at batch 256 | [vLLM GB300 DeepSeek](https://vllm.ai/blog/2026-02-13-gb300-deepseek) |
| vLLM chunked-prefill and CUDA-graph tables, §4.1/§4.2: GLM-5.3-Flash FP8, **4×GB200 TP4**, 8192/1024, c=32/64/128 — every throughput, p99 ITL, mean TTFT and p99 TTFT cell, and SGLang's **13/16/20 ms** p99 ITL | [vllm-project/vllm#56975](https://github.com/vllm-project/vllm/issues/56975) |
| EPLB defaults `window_size 1000`, `step_interval 3000`, `use_async true`, `log_balancedness false`; **≈2.4 GB per redundant expert per EP rank** on DeepSeek-V3; `EP_SIZE = TP_SIZE × DP_SIZE` | [vLLM EP deployment docs](https://docs.vllm.ai/en/latest/serving/expert_parallel_deployment/) |
| Qwen3.8-27B recipe: KV pools **377,456 / 445,875 / 920,517** tokens and weights **14.28 / 12.02 / 10.64 GiB**; acceptance **0.771 / 0.897 / 0.788**; 1×5090 32K **91,022 / 135,926 / 152,917 / 76,458**; Ascend 950PR **64 tok/s**; DFlash2 γ=7 needs vLLM ≥0.28.0; `--reasoning-parser qwen3` quote | [vLLM Qwen3.8-27B recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) |
| Qwen3.8-27B KV **65,536 B BF16 / 32,768 B FP8** per token = `2 × 4 kv-heads × 256 head_dim × B × 16 full-attn layers`; **16 full / 48 linear of 64** layers | recomputed from `research/models/qwen3827b/config.json` |
| Qwen3.8-27B recurrent state **151 MB/seq** = `48 layers × 48 value heads × 128 × 128 × 4 B (float32)`; crossover **4,608 FP8 KV tokens**; **9.0 GiB/GPU at 128 seqs, TP2** | recomputed from `config.json`, exact |
| Kimi-K3 shape: **896 experts top-16**, **69 KDA + 24 MLA of 93** layers, MXFP4 `group_size 32` with attention/shared-experts/lm_head/vision in the `ignore` list, 1M `max_position_embeddings` | `research/models/kimik3/config.json` |
| DS-V4.1-Flash CSA2 map: `kv_source_layer_ids [2,8,14,20]` (**4 of 40** own KV), 8 index-source layers, `index_topk 512`, candidate 2048×8, `sliding_window 128`, `compress_rope_theta 160000`; sparse term `T×512` vs `T²` = **2048× at 1M** | `research/models/deepseek41f/config.json`; ratio recomputed |
| All remaining internal arithmetic: prefill FLOPs `2×8e9×131072 = 2.097 PFLOP` and its h-scaling; `131072 × 890 = 116.6 MB`, `1048576 × 890 = 933 MB`; 0.117 GB / 83.4 GB/s = **1.4 ms**; §7.1 `4×6/(0.60×3600/1e6) = 11,111` tok/s → 2,778/GPU; §7.2 cost grid and **8.5× spread**, 96 % / 66 % margins; §4.1 **−64 %** at c=128; §4.2 **−5…−10 %** throughput / **+19…+31 %** p99 TTFT; §3.2 **3.3×**; all §3.4 ratios; §5.4 **2.2× / 2.5× / 2.8×**; §6.2 **+49 %**; §7.3 **2.44×** | recomputed, `python3` — all exact |

### UNVERIFIABLE (3)

| Claim | Why | Status |
|---|---|---|
| "~2× TTFT speedup typical" for TRT-LLM KV reuse (§1.2) | Not present in either NVIDIA post; only the "up to 5×" headline and a +20 % hit-rate figure exist | now marked **⚠️ TO BE VERIFIED** in §1.2 |
| Whether the §2.5 `S = accept/(1 + vcr·γ)` model actually separates short from long blocks (§2.5) | With the corrected γ=3 the break-even `vcr*` is 0.46 (MTP) vs 0.53 (K3-DSpark coding) vs 0.50 (DS-V4.1-Flash) — the model does **not** rank them; the conclusion rests on measured data instead | caveat added inline in §2.5 |
| Usable HBM per B300 in a shipping 8-GPU node (§7.2) | `b300.md` reports an unreconciled **262.5 / 268 / 279 / 288 GB** spread; 2,144 GB/node is the low-middle reading | **⚠️ TO BE VERIFIED** retained in §7.2 and Open Question 14 |

### Sections that did not change

§1.1, §1.3, §1.4, §2.2, §2.6, §2.7, §3.1, §3.5, §4.3, §4.4, §5.2, §5.5, §6.3, §7.4
and §8 were spot-checked and no arithmetic or support error was found. Open
Questions 7, 9 and 14 were rewritten above; 1–6, 8 and 10–16 stand as written.

### Sources opened for this pass

- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash
- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash
- https://recipes.vllm.ai/Qwen/Qwen3.8-27B
- https://deepseek.ai/pricing
- https://platform.kimi.ai/docs/pricing/chat-k3
- https://platform.claude.com/docs/en/about-claude/pricing
- https://developers.openai.com/api/docs/pricing
- https://www.alibabacloud.com/help/en/model-studio/context-cache
- https://emergent.sh/learn/kimi-k3-pricing
- https://vllm.ai/blog/2026-07-27-k3
- https://vllm.ai/blog/2026-01-08-kv-offloading-connector
- https://vllm.ai/blog/2026-05-06-mooncake-store
- https://vllm.ai/blog/2026-08-07-decode-context-parallelism
- https://vllm.ai/blog/2026-02-13-gb300-deepseek
- https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/
- https://www.lmsys.org/blog/2026-02-19-gb300-longctx/
- https://docs.vllm.ai/en/latest/serving/expert_parallel_deployment/
- https://github.com/vllm-project/vllm/issues/56975
- https://blog.squeezebits.com/vllm-vs-tensorrtllm-12-automatic-prefix-caching-38189
- https://developer.nvidia.com/blog/5x-faster-time-to-first-token-with-nvidia-tensorrt-llm-kv-cache-early-reuse/
- https://developer.nvidia.com/blog/introducing-new-kv-cache-reuse-optimizations-in-nvidia-tensorrt-llm/
- repo: `research/METHODOLOGY.md`, `research/gpus/b300.md`,
  `research/models/deepseek41f/{config.json,architecture.md}`,
  `research/models/kimik3/config.json`, `research/models/qwen3827b/config.json`

---

## Sweep log (2026-09-19)

Systemic sweep against the amended `research/METHODOLOGY.md` (§1 bytes-per-param
and mixed-precision rule, §2 state-slot multiplier `S`, §3 consistency rule, §6
cached-token rule + scenarios S1–S4, §8 pinned GPU/model/price inputs). Format:
**section · old → new · reason · source**.

| # | Section | Old → New | Reason | Source |
|---|---|---|---|---|
| 1 | Header model table | added pinned checkpoint totals (**510.29 / 527.27 / 1,560.9 / 55.56 / 5.444 GB**) and "routed experts **MXFP4**" for DS-V4.1-Flash; added the pointer that per-(model, GPU) fit/throughput/cost tables live in `research/models/<exp>/<gpu>.md` | METHODOLOGY §1 (mixed-precision checkpoints summed per tensor group, reconciled to `index.json`), §8; scope rule | METHODOLOGY §8; [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash) ("Routed expert weights are MXFP4") |
| 2 | §1.2 | "Whether vLLM **0.30** still pays 36.7 %" → "Whether **current** vLLM still pays 36.7 %" | 0.30.0 does not exist; latest is 0.29.0 | PyPI `vllm` JSON: 0.29.0, 2026-09-09; [inference-engines.md §2.1](inference-engines.md) |
| 3 | §1.4 | "vLLM tiered (**0.30**)" → "vLLM tiered (`main`, targeted at 0.30 — **unreleased**; latest 0.29.0, 2026-09-09)" | same | PyPI `vllm` JSON; inference-engines.md §2.1 |
| 4 | §1.5 | "V4.1-Flash's KV is 890 B/token (**§6**)" → "(**§5.1**), with FP4 KV" | wrong cross-reference (§6 is Multimodal); 890 B is the FP4-KV figure | METHODOLOGY §8 |
| 5 | §1.5 "Self-hosting comparison" | "METHODOLOGY §6 says to assume ~10 %" → now states the **amended** two-branch rule: ~10 % for our own serving, the vendor's published cached ratio (DeepSeek ≈ 2.1 %, Anthropic ≈ 2.5 %, Alibaba 10–25 %) for API comparisons | METHODOLOGY §6 was amended on 2026-09-19 to point at this document's §1.5 table | METHODOLOGY §6 |
| 6 | §2.3 | DS-V4.1-Flash acceptance **3.51** now labelled **⚠️ synthetic benchmark constant, `est.`**, with the `"rejection_sample_method":"synthetic"` config quoted inline | 3.51 is a configured constant, not a measurement | [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash), fetched 2026-09-19: `"rejection_sample_method":"synthetic","synthetic_acceptance_length":3.51`, "use synthetic acceptance for benchmark measurement only" |
| 7 | §2.5 | speedup-model row "DS-V4.1-Flash, golden" → "DS-V4.1-Flash, \"golden\" (⚠️ synthetic constant, §2.7)" | same | same |
| 8 | §3.4 | "1.5× — **288 GB vs 192 GB per GPU**" → same nameplate figures **plus** the as-deployed rule: GB300 NVL72 288 GB (≈279 usable), B200 HGX **180 GB** (192 is the stack figure), HGX B300 **268 GB/GPU = 2,144 GB/node**, "do not merge the two" | METHODOLOGY §8 capacities-as-deployed; HGX B300 and GB300 NVL72 are different products | METHODOLOGY §8; [gpus/b300.md](../gpus/b300.md) |
| 9 | §5.1 (DS-V4.1-Flash row) | "890 B" → "890 B — the **FP4-KV figure**, needs the Blackwell FP4-KV kernel"; SWA ring annotated "capped at W=128" | METHODOLOGY §2 sliding-window cap and §8 KV-dtype pin | [model card](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash), fetched: "Combined with **FP4 main KV caching** (E2M1, one E4M3 scale per 16 channels) … **890 bytes per token**" |
| 10 | §5.1 (Kimi-K3 row) | fixed state **54 MB → 2.25 GB per request** (`S=5` × 428.6 MiB), i.e. **280.9 MB = 0.262 GiB per GPU** at attnTp8; totals **0.261 / 0.894 / 3.426 / 27.05 GiB → 0.473 / 1.105 / 3.637 / 27.26 GiB** | METHODOLOGY §2: per-sequence recurrent state × engine slot count `S`; SGLang's `extra_buffer` default is 5 | METHODOLOGY §2/§8; [models/kimik3/architecture.md](../models/kimik3/architecture.md) §5.3; recomputed in `python3` |
| 11 | §5.1 (Qwen3.8-27B row) | linear state **151 MB → 153.9 MB** per slot (fp32; 78.4 MB bf16), `S=1` stated, ⚠️ note that MTP γ=3 adds verify slots | METHODOLOGY §8 pinned GDN state (recurrent 151.0 MB + ~2.9 MB conv) | METHODOLOGY §8 |
| 12 | §5.1 (Marlin-2B row) | "⚠️ TO BE VERIFIED (gated repo)" → **12,288 B/token BF16**, **18.63 MiB** GDN state, totals 0.112 / 0.393 / 1.518 / 12.018 GiB | METHODOLOGY §8 now pins these; only the per-mode video prompt overheads remain ⚠️ | METHODOLOGY §8 |
| 13 | §5.1 derivations | crossover "**1,963** tokens" → "**10,158** tokens"; "Below ~2K context the recurrent state dominates" → "Below ~10K"; "KDA is a rounding error at 1M" → "~1 %" | cascade from #10 (`280,857,600 / 27,648`) | recomputed, `python3` |
| 14 | §5.1 derivations | Qwen crossover **4,608 → 4,697** FP8 tokens; TP2 128-seq reservation **9.0 → 9.17 GiB/GPU**; 75.5 → 77.0 MB/seq/GPU | cascade from #11 (`153.9e6 / 32768`) | recomputed, `python3` |
| 15 | §5.1 (reconciliation note) | "recurrent state pool is ~9 GiB" → "~9.2 GiB" | cascade from #14 | recomputed |
| 16 | §5.3 table | Kimi-K3 "54 MB (TP8)" → "2.25 GB per request (S=5 × 428.6 MiB); 280.9 MB/GPU"; Qwen "151 MB" → "153.9 MB per slot"; "32 KB FP8" → "32,768 B FP8" | METHODOLOGY §2 slot count; §8 pinned state; units in bytes | METHODOLOGY §2/§8 |
| 17 | §5.3 prose | "K3's KDA state equals ~1,963 tokens … Qwen's 4,608" → "~10,158 … 4,697", plus "the constant is **multiplied by the engine's slot count S**" and "sizing it at S=1 understates the reservation fivefold on K3" | cascade from #10/#11 | recomputed |
| 18 | §5.3 unified-memory bullet | "54 MB and 27,648 B allocations" → "53.6 MiB-per-slot state blocks and 27,648 B token blocks" | the blog's 54 MB is one slot per GPU, not the per-request state | [lmsys K3 day-0](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/), fetched: "about 54 MB under TP=8" |
| 19 | §5.5 | "Hybrid HiSparse (vLLM, targeted at v0.30)" → adds "**not yet released**; latest is 0.29.0, 2026-09-09" | engine-version re-verification | PyPI `vllm` JSON |
| 20 | §7.1 table | Weights "476 GiB (511 GB on disk)" → "**510.29 GB = 475.24 GiB** (`index.json` `total_size`)", recipe round figure kept as a parenthetical; added a checkpoint-format row (routed experts **MXFP4**, not NVFP4); KV rows now include the 2.77 MiB SWA ring (0.111 / 0.872 GiB/seq) and are labelled FP4 KV; added **`max_concurrency(128K) ≈ 917` `est.`** | METHODOLOGY §1 (reconcile to pinned total), §2 (fixed state added to per-seq KV), §3 (fit/consistency rule) | METHODOLOGY §1/§3/§8; [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash); recomputed, `python3` |
| 21 | §7.1 prose | "1,000 concurrent sequences hold 109 GiB … affordable" → "~900 concurrent … ~98 GiB"; added "a row at batch 1,000 would be **infeasible (KV)**"; "476 GiB of weights" → "475.24 GiB" | METHODOLOGY §3 consistency rule — a batch above `max_concurrency(ctx)` may not be printed as a number | METHODOLOGY §3; recomputed |
| 22 | §7.1 cost check | "hypothetical **$6**/GPU-hour → 11,111 agg tok/s, 2,778 tok/s/GPU" → a four-row table at **$3.70 (on-prem 3-yr ⚠️) / $7.40 (Hyperstack) / $15.00 (OCI) / $17.80 (AWS)** → 6,852 / 13,704 / 27,778 / 32,963 agg and 1,713 / 3,426 / 6,944 / 8,241 tok/s/GPU; conclusion flipped from "roughly at achievable cost" to "**below** any published *rented* B300 rate, reachable only near on-prem amortised cost" | METHODOLOGY §6 / item 7: only rows from `cloud-pricing.md`; $6/GPU-hr is not a published B300 price | [cloud-pricing.md](cloud-pricing.md) §5.7 and §9.4; recomputed, `python3` |
| 23 | §7.2 KV table | header clarified to **per GPU under TP8** (MLA replicated per §3.1); KDA column **0.050 → 0.262 GiB** (S=5); totals **0.261 / 0.894 / 3.426 / 27.05 → 0.473 / 1.105 / 3.637 / 27.26 GiB** | cascade from #10 | recomputed, `python3` |
| 24 | §7.2 fit | "~1.4 TB checkpoint, **744 GB** free, **~26** concurrent 1M sequences, **~200** with DCP8" → full METHODOLOGY §3 derivation (`268 × 0.90 − 1,560.9/8 − 4 = 42.09 GB/GPU`) giving **1** concurrent at 1M under TP8 and **10** with DCP8 (10 / 57 at 128K) | pinned checkpoint 1,560.9 GB; §3's 0.90 usable factor + activation workspace; `S=5` state; MLA KV is replicated under TP8 per this document's own §3.1 | METHODOLOGY §3/§8; [lmsys K3 day-0](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/) ("about 7.9× on K3 with DCP8" — the derived ~8× step matches); recomputed, `python3` |
| 25 | §7.2 correction note | "shipping nodes report 262.5–268 GB per GPU … with Lambda's MLPerf submission at **279 GB/GPU**" → HGX/DGX/AWS p6-b300 pinned at **268 GB/GPU, 2,144 GB/node** (DGX B300 262.5); the **279 GB** figure reassigned to **GB300 NVL72** (288 GB nameplate, ≈279 usable), "a different product … must not be mixed into an HGX B300 node total" | METHODOLOGY §8: do not merge HGX B300 and GB300 NVL72 figures | METHODOLOGY §8; [gpus/b300.md](../gpus/b300.md) |
| 26 | §7.2 cost grid | columns **$2 / $4 / $6 / $8** (hypothetical) → **$7.40 Hyperstack B300 / $15.00 OCI B300 / $17.80 AWS p6-b300 / $18.00 OCI GB300**; bs=1 row relabelled **331 tok/s aggregate on 8 GPUs = 41.4 tok/s/GPU** (was printed as 331 tok/s/**GPU**); costs **$1.68–6.71/M → $49.68–120.85/M** (bs=1) and **$0.20–0.79/M → $0.732–1.781/M** (max throughput) | item 7 (only `cloud-pricing.md` rows) + a wrong denominator: 331 tok/s is a single-stream rate on a TP8 node, so the bs=1 row was understated 8× | [cloud-pricing.md](cloud-pricing.md) §5.7/§5.9; [vLLM K3 blog](https://vllm.ai/blog/2026-07-27-k3) ("8×GB300 NVL72, TP8, bs=1"); recomputed, `python3` |
| 27 | §7.2 conclusion | "**8.5×** cost spread … margin ~96 % at max throughput, **~66 %** at bs=1" → "**68×** spread (2,808 / 41.4) … **~95 %** at $7.40 max-throughput; bs=1 costs **$49.68/M against a $15/M list price**, i.e. a **loss** at every published rate" | cascade from #26 | recomputed, `python3`; [platform.kimi.ai](https://platform.kimi.ai/docs/pricing/chat-k3) |
| 28 | §7.3 | "the 151 MB/seq recurrent-state pool" → "the 153.9 MB/slot recurrent-state pool (METHODOLOGY §8)" | cascade from #11 | METHODOLOGY §8 |
| 29 | §7.4 | "~5 GB weights (repo README)" → "**5.444 GB** BF16 (2.21 B unique params, 2.72 B on disk with the tied embedding duplicated)"; added the pinned KV (12,288 B/token BF16, 6 GQA layers) and GDN state (18.63 MiB/seq) | METHODOLOGY §8 pinned model inputs | METHODOLOGY §8 |
| 30 | §8 item 6 | added "on an MLA model DCP is the difference between replicating KV on every TP rank and sharding it: ~1 vs ~10 concurrent 1M-context K3 sequences on one 8×B300 node (§7.2)" | the summary claim rested on the superseded §7.2 numbers | §7.2, recomputed |
| 31 | Open questions #8 | "the `est.` 151 MB/seq … conv state excluded" → "the pinned **153.9 MB/slot** (151.0 MB recurrent + ~2.9 MB conv)", plus the unpublished slot count `S` | cascade from #11 | METHODOLOGY §8 |
| 32 | Open questions #14 | open → **Resolved**: 268 GB/GPU, 2,144 GB/node pinned; 288 GB (≈279 usable) reassigned to GB300 NVL72; only the activation-workspace estimate remains | METHODOLOGY §8 resolves the former 262.5/268/279/288 spread | METHODOLOGY §8 |
| 33 | Open questions #16 | added: base routed experts are **MXFP4** (0.53125 B/param), NVIDIA's NVFP4 build is **larger — 527.27 GB vs 510.29 GB**, only 58 % NVFP4 by bytes, **accuracy-neutral with no published speedup** ⚠️ | METHODOLOGY §1/§8; the NVFP4 build is not a smaller or faster variant | METHODOLOGY §8; [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash) |

### Checklist items that did not apply

- **GB/GiB slips** beyond those listed: none found — §1.5, §5.2, §5.4, §6.1 already
  do their arithmetic in bytes and label GB vs GiB correctly.
- **Dense-vs-sparse TFLOPS**: this document quotes no TFLOPS figures, only ratios
  (the 1.5× Blackwell Ultra delta is correctly scoped to NVFP4).
- **RTX PRO 6000 bandwidth** (1,597 GB/s Server Edition): the GPU is not mentioned.
- **B200-vs-H200 gap on DeepSeek-R1 FP8** (1.7–3.0×): no such claim in this document.
- **TokenSpeed DS-V4.1-Flash recipe**: TokenSpeed is not mentioned here; the
  corrected statement lives in [inference-engines.md §2.5](inference-engines.md).
- **Engine release dates**: this document cites engine versions only inside
  measurement contexts (vLLM v0.6.3 / v0.14.1 / v0.27.1, SGLang v0.4.4,
  TRT-LLM v0.15.0), never as "current release" — no off-by-one date pattern.
  Latest releases re-checked from PyPI on 2026-09-19 (GitHub's releases API was
  rate-limited from this host): **vLLM 0.29.0, 2026-09-09**; **SGLang 0.5.20,
  2026-09-18**; TRT-LLM 1.2.1 stable per inference-engines.md §2.3. Only the
  forward references to an unreleased vLLM 0.30 needed flagging (#2, #3, #19).
- **Per-(model, GPU) tables**: none added; a pointer to
  `research/models/<exp>/<gpu>.md` was added under the header model table instead.

### Citation integrity spot-check (5 most load-bearing)

| Citation | Claim checked | Verdict |
|---|---|---|
| [huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) | 890 B/token global KV; SWA Bounded Replay → ~1/8 of V4-Flash; ~437× vs V1 | **Confirmed verbatim.** The card ties 890 B to "**FP4 main KV caching** (E2M1 format, one E4M3 scale per 16 channels)" — §5.1 now says so. The card does **not** itself say "Blackwell-only kernel"; that qualifier is attributed to METHODOLOGY §8, not to the card |
| [recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash) | golden acceptance 3.51; 511 GB / 476 GiB; `vram_minimum_gb: 614`; expert format | **Confirmed**, and it is explicit that 3.51 is the `synthetic_acceptance_length` "for benchmark measurement only", and that "Routed expert weights are MXFP4" — both now reflected (#6, #7, #20) |
| [lmsys.org/blog/2026-07-27-kimi-k3-day0-support](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/) | MLA "about 27 KB"; KDA state; PP8 = 1.7× TEP8; 2,808 tok/s/GPU; DCP8 7.9× | **Confirmed verbatim** for all five, *except* that the post says "about 54 MB under TP=8" for **one** state block and says nothing about 5 slots or 2.25 GB — the `S=5` multiplier comes from `models/kimik3/architecture.md` §5.3, which is now the citation for it (#10, #18) |
| [vllm.ai/blog/2026-08-07-decode-context-parallelism](https://vllm.ai/blog/2026-08-07-decode-context-parallelism) | 8×B200, 6,091 vs ~1,863 tok/s/GPU at c=512, 82 % KV utilisation, `TP ≥ DCP`, GQA cap | **Confirmed verbatim**, including both MLA divisibility constraints |
| [deepseek.ai/pricing](https://deepseek.ai/pricing) | $0.14 / $0.0030 / $0.60 per 1M | **Confirmed** by the previous pass and unchanged; the $0.15 figure is not on the page |
