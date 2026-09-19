# Reference architectures and case studies

**Research date: 2026-09-19.** Legend, cost formulas and pinned GPU/model inputs:
[`../METHODOLOGY.md`](../METHODOLOGY.md). Tree index: [`../README.md`](../README.md).

This document does **not** re-derive per-GPU specs, per-model fit, engine support,
quantization or serving-optimization numbers. Those live in
[`../gpus/`](../gpus/), [`../models/`](../models/),
[`../cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md),
[`../cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md),
[`../cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md)
and [`../matrix/`](../matrix/), and this document links to them. Where a published
architecture disagrees with a pinned value in `METHODOLOGY.md §8`, the disagreement
is stated explicitly rather than silently resolved.

**Running hardware assumption:** 8×B300 HGX nodes, 268 GB usable HBM per GPU
(2,144 GB/node), NVLink-5 inside the node, InfiniBand/RoCE between nodes, local
NVMe per node ([`../gpus/b300.md`](../gpus/b300.md)), plus AWS `p6` as overflow
([`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md)).

**Evidence labels used below**, on top of the tree's standard legend:

| Label | Meaning |
|---|---|
| `meas.` | A number the publisher says they measured on named hardware. |
| `vendor` | A vendor performance claim with no reproducible harness attached. |
| `est.` | Derived here from `METHODOLOGY.md` formulas or from `../matrix/pairs.json`. |
| ⚠️ **TO BE VERIFIED** | No primary source reachable, or sources conflict; method stated inline. |

A recurring caveat for §1: **almost every published number below is a
different model on different silicon at a different date.** They are useful as
*architecture* evidence (what shape the system took, what it bought) and as
*ratio* evidence (2× TTFT, 1.8× throughput). They are not transferable as
absolute tokens/s to a B300 node. The transferable column in each table says
what actually carries over.

---

## 1. Published production architectures, with numbers

### 1.1 DeepSeek — V3/R1 online inference system (day-6 open-source-week post)

The most complete public disclosure of a large-MoE production serving system,
including the cost sheet. All figures below are from DeepSeek's own file
([open-infra-index, day 6](https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md)),
measured over **UTC+8 2025-02-27 12:00 → 2025-02-28 12:00**.

| Item | Value | Label |
|---|---|---|
| Hardware | H800, all services | meas. |
| Prefill unit | *"Routed Expert EP32, MLA/Shared Expert DP32 … spans 4 nodes with 32 redundant routed experts, where each GPU handles 9 routed experts and 1 shared expert"* [src](https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md) | meas. |
| Decode unit | *"Routed Expert EP144, MLA/Shared Expert DP144 … spans 18 nodes with 32 redundant routed experts, where each GPU manages 2 routed experts and 1 shared expert"* [src](https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md) | meas. |
| Comm/compute overlap | prefill: dual-batch (2 microbatches alternating); decode: attention split in two, **5-stage pipeline** | meas. |
| Load balancing | three separate balancers: prefill (core-attention FLOPs + dispatch-send tokens), decode (KV-cache bytes + request count), expert-parallel (minimize max dispatch-receive load) | meas. |
| Node occupancy | peak **278** nodes, average **226.75** nodes × 8 H800 | meas. |
| Cost basis | *"Assuming the leasing cost of one H800 GPU is $2 per hour, the total daily cost amounts to $87,072"* | meas. + stated assumption |
| Input tokens / 24 h | **608 B**, of which **342 B (56.3 %) hit the on-disk KV cache** | meas. |
| Output tokens / 24 h | **168 B**; average output speed **20–22 tok/s**; average KV length per output token **4,989** | meas. |
| Per-node throughput | **~73.7k tok/s input** during prefilling **or ~14.8k tok/s output** during decoding | meas. |
| Economics | theoretical daily revenue **$562,027** at R1 pricing ($0.14 cache-hit in / $0.55 miss in / $2.19 out per 1M), **cost profit margin 545 %** — DeepSeek states actual revenue is *"substantially lower"* (V3 cheaper, web/app free, night discounts) | meas. + stated caveat |
| Capacity time-shifting | inference on all nodes at day peak; nodes returned to research/training at night | meas. |

**What transfers to 8×B300 nodes.** Four things, and only four:

1. **The prefill:decode node ratio is an economic variable, not a constant.**
   DeepSeek ran a 4-node prefill unit against an 18-node decode unit — decode-heavy
   by a wide margin, because their traffic was 3.6:1 input:output tokens and decode
   is the bandwidth-bound half. The transferable artefact is the *sizing formula*
   (§4.3), not the 4:18.
2. **Three load balancers, not one.** Attention FLOPs, KV bytes and expert
   dispatch load are three different imbalances and a single router metric cannot
   fix all three. This is the strongest argument in the literature for KV-aware
   routing plus EPLB rather than request-count round-robin.
3. **On-disk KV cache hit rate is the single biggest cost lever.** 56.3 % of input
   tokens never got prefilled. This repo's own measured hit rate for
   DeepSeek-V4.1-Flash on agentic traces is *higher still* — **90.2–97.0 %**
   ([`../matrix/recommendations.md §2.1`](../matrix/recommendations.md)) — so any
   architecture that does not give the cache a durable home is leaving most of the
   win on the table.
4. **Night-time capacity return.** If the cluster also trains, the inference pool
   should be an elastic tenant of the same scheduler, not a static reservation.

**What does not transfer.** EP144 across 18 nodes is a shape for a 671 B-class MoE
on 80 GB cards. This repo's models do not need it:
DeepSeek-V4.1-Flash is **TP4 recommended, 2 GPUs minimum, single node, EP1 — and
`../matrix/recommendations.md` states wide-EP "runs badly" on an 8-GPU domain**
([`../models/deepseek41f/b300.md`](../models/deepseek41f/b300.md),
[`../matrix/recommendations.md §2.1`](../matrix/recommendations.md)), and Kimi-K3
is **TP8+DCP8 on exactly one node** ([`../models/kimik3/b300.md`](../models/kimik3/b300.md)).
A 268 GB card changes the wide-EP calculus completely versus an 80 GB card.

### 1.2 Moonshot AI — Mooncake, the Kimi serving platform

Mooncake is the closest published system to what this repo would build for
Kimi-K3, and it is the reference architecture for KV-cache-centric disaggregation.
Source: Qin et al., *"Mooncake: Trading More Storage for Less Computation — A
KVCache-centric Architecture for Serving LLM Chatbot"*, **USENIX FAST '25**
([paper PDF](https://www.usenix.org/system/files/fast25-qin.pdf),
[arXiv:2407.00079](https://arxiv.org/abs/2407.00079)).

| Item | Value | Label |
|---|---|---|
| Scale | *"more than 100 billion tokens a day"*, *"thousands of nodes"* | meas. |
| Topology | separate **prefill** and **decode** clusters + a disaggregated **KVCache pool** built from the cluster's own CPU, DRAM, SSD and NIC | meas. |
| Scheduler | **Conductor**, a global KVCache-centric scheduler: tokenize → select a *pair* of prefill nodes and a decode node → dispatch | meas. |
| Long-prompt prefill | **chunked pipeline parallelism (CPP)** across multiple prefill nodes — chosen over sequence parallelism because it *"reduces network consumption and simplifies the reliance on frequent elastic scaling"* | meas. |
| Overload policy | **prediction-based early rejection**; Conductor *"directly returns the HTTP 429 Too [Many Requests]"* when the SLO is not achievable | meas. |
| Production gain | *"M OONCAKE enables Kimi to handle **115 % and 107 % more requests** on the A800 and H800 clusters, respectively, compared to our previous systems based on vLLM"* | meas. |
| Benchmark gain | *"up to a **498 % increase in the effective request capacity** while meeting SLOs"* vs baseline | meas. |
| Global vs local cache | global cache hit rate *"up to **2.36×** higher than that of the local cache, resulting in up to **48 % savings** in prefill computation time"*; a separate figure reports *"a maximum increase of **136 %** in cache hit rate"* | meas. |
| Transfer engine | 40 GB transfer (LLaMA3-70B @128k KV): **87 GB/s at 4×200 Gbps**, **190 GB/s at 8×400 Gbps** — *"approximately 2.4× and 4.6× faster"* than TCP | meas. |
| Network | HGX machines, **100/200 Gbps NIC per A800 GPU**, **200/400 Gbps NIC per H800 GPU**, RoCEv2 tuned by the cloud provider; hot KVCache blocks are **replicated** to mitigate congestion | meas. |
| Bandwidth floor for PD | the paper derives a minimum KV-transfer bandwidth `B` from model and machine parameters: **6 GB/s for 8×A800, 19 GB/s for 8×H800** (LLaMA3-70B) | meas. |
| Test rig | 16 nodes × 8×A800-SXM4-80GB, replayed real conversation traces | meas. |
| Ceiling observed | one workload reached only *"up to 50 % of the theoretical cache hit rate"*; a figure caps a workload's theoretical max at **75 %** | meas. |

**What transfers.** Four things, directly:

1. **Compute the KV-transfer bandwidth floor before choosing PD.** Mooncake's
   `B` derivation is the right gate: 19 GB/s was the floor for a 70 B dense model
   on 8×H800. Kimi-K3's MLA KV is **13.5 KiB/token FP8**
   (`METHODOLOGY.md §8`), so a 128k-token handoff is ~1.77 GB
   (128,000 × 13,824 B = 1,769,472,000 B; recomputed 2026-09-19 — an earlier
   draft printed 1.73 GB, which used 13.5 **KB** decimal instead of the pinned
   13.5 KiB), and at 400 Gbps
   (50 GB/s line rate) that is ~35 ms of wire time before protocol overheads —
   comparable to a whole decode step budget. **est.**, from the pinned KV/token
   value; the implication is that Kimi-K3 PD across IB is only defensible at long
   prompts with a high enough prefill saving to pay 35 ms+.
   DeepSeek-V4.1-Flash is the opposite case: **890 B/token on sm_103**
   (`METHODOLOGY.md §8`), so 128k tokens is **~114 MB** — ~2.3 ms at 400 Gbps,
   **est.** — which is why an MLA+FP4-KV model is the *good* PD candidate and a
   13.5 KiB/token model is the marginal one.
2. **Hot-block replication beats congestion control.** Mooncake replicates
   frequently-read KV blocks rather than tuning the fabric harder.
3. **Early rejection is part of the architecture.** A 429 at admission time,
   computed from a *predicted* completion time, is strictly better than accepting
   a request that will miss its SLO and consume KV pages while doing it.
4. **CPP over SP for long prefill.** For 1M-context traffic on this repo's models,
   chunked pipeline parallelism across prefill nodes is the published,
   production-proven way to cut TTFT without a permanent wide-TP deployment.

**Relation to this repo.** Mooncake's transfer engine is the same code path SGLang
exposes as `--disaggregation-transfer-backend mooncake`
([SGLang PD docs](https://docs.sglang.io/advanced_features/pd_disaggregation.html)),
and it is the transport NVIDIA's own **DeepSeek-V4.1-Flash disaggregated recipe**
selects (§2.1). So this is not a paper to admire — it is the shipping default.

### 1.3 Character.AI — memory-efficient inference at chat scale

Source: [*"Optimizing AI Inference at Character.AI"*](https://blog.character.ai/optimizing-ai-inference-at-character-ai-2/).

| Item | Value | Label |
|---|---|---|
| Scale | *"we serve more than 20,000 inference queries per second"*, stated as ≈20 % of Google Search volume | meas. |
| Attention | **Multi-Query Attention in all layers** — *"reduces KV cache size by 8X compared to the Grouped-Query Attention adopted in most open source models"* | meas. |
| Hybrid horizons | local attention with a **1024-token window**; *"only 1 out of every 6 layers uses global attention"* | meas. |
| Cross-layer sharing | further **2–3×** KV reduction | meas. |
| Inter-turn cache | *"a **95 % cache rate**"*, average dialogue history **180 messages** | meas. |
| Precision | **int8 on weights, activations and KV**, with custom int8 kernels, and *natively trained* in int8 rather than PTQ | meas. |
| Cost | serving cost reduced **33×** since late 2022; commercial APIs would cost *"at least 13.5X more than with our systems"* | meas. (self-reported) |

**What transfers.** The **95 % cache rate** is the number to internalize: a chat
product with long stable histories converges to a regime where prefill is almost
entirely cache reads, and the whole architecture should be built around *keeping
that cache alive across turns and across replicas*, not around raw FLOPs. This
repo's own agentic traces measure **90.2–97.0 %** for DeepSeek-V4.1-Flash
([`../matrix/recommendations.md §2.1`](../matrix/recommendations.md)) — the same
regime.

The architectural consequences that apply here: (a) session-sticky, prefix-aware
routing is not an optimization, it is the load-bearing wall; (b) a cache tier that
survives replica restarts is worth more than another GPU; (c) the KV-per-token
figure decides everything downstream, which is exactly why
`METHODOLOGY.md §2` insists on computing it per model.

**What does not transfer.** Character.AI co-designed the *model* (MQA everywhere,
5:1 local:global, native int8). This repo serves five fixed checkpoints and has no
such lever. The closest available substitutes are `--kv-cache-dtype fp8` (worth
**+41 % concurrency** on Qwen3.8-27B, 231 → 325 @8K,
[`../matrix/recommendations.md §2.1`](../matrix/recommendations.md)) and
DeepSeek-V4.1-Flash's native 890 B/token FP4 KV on sm_103.

### 1.4 NVIDIA — Dynamo, GB200/GB300 NVL72, wide-EP

**Dynamo** is NVIDIA's orchestration layer above vLLM / SGLang / TensorRT-LLM
([README](https://github.com/ai-dynamo/dynamo)). Release containers at research
date: `nvcr.io/nvidia/ai-dynamo/{sglang,tensorrtllm,vllm}-runtime:1.4.2`, with
model-specific dev tags (`1.5.0-kimi-k3-dev.1`, `1.6.0-deepseek-v4.1-flash-dev.1`)
shipping ahead of the numbered release — see §2.1.

Dynamo's own headline table ([README "Key Results"](https://github.com/ai-dynamo/dynamo)),
all **vendor**-labelled except where a third party is named:

| Claim | Context | Label |
|---|---|---|
| **7×** higher throughput per GPU | DeepSeek-R1, GB200 NVL72 w/ Dynamo vs B200 without ([InferenceX](https://inferencex.semianalysis.com/)) | vendor, third-party harness |
| **7×** faster model startup | ModelExpress weight streaming, DeepSeek-V3 on H200 | vendor |
| **2×** faster TTFT | KV-aware routing, Qwen3-Coder 480B ([Baseten](https://www.baseten.co/blog/how-baseten-achieved-2x-faster-inference-with-nvidia-dynamo/)) | third-party meas. (§1.6) |
| **80 %** fewer SLA breaches at **5 %** lower TCO | Planner autoscaling, Alibaba APSARA 2025 | vendor, citing a conference talk |
| **750×** higher throughput | DeepSeek-R1 on GB300 NVL72 (InferenceXv2) | vendor ⚠️ **TO BE VERIFIED** — no baseline is stated in the README row; a 750× ratio is only meaningful against a named baseline and this document does not reproduce it |

**Wide-EP on NVL72** ([NVIDIA, 2025-10-20](https://developer.nvidia.com/blog/scaling-large-moe-models-with-wide-expert-parallelism-on-nvl72-rack-scale-systems/)):
*"Large EP rank 32 delivers up to **1.8x higher output token throughput per GPU**
compared to small EP rank 8 at 100 tokens/sec per user"* on DeepSeek-R1
(671 B, 256 experts) over the GB200 NVL72's **130 TB/s** NVLink domain, with
TensorRT-LLM Wide-EP and an **Expert Parallel Load Balancer (EPLB)** in static and
online modes. `meas.` on NVIDIA's rig, `vendor` in provenance.

**SGLang on GB300 NVL72** ([LMSYS, 2026-02-20](https://www.lmsys.org/blog/2026-02-20-gb300-inferencex/)):
up to *"**25x** higher performance running DeepSeek R1 on GB300 NVL72 compared to
H200"*. The 8×/4× pair is a **separate, GB200 claim** and is quoted here in full
because the framing matters: *"Compared to our prior InferenceMAXv1 submission,
less than 4 months ago, the latest v2 release with low precision NVFP4 delivers
up to **8x** more tokens-per-GPU in high throughput regimes and up to **4x** more
tokens-per-user in high interactivity regimes … on existing GB200 NVL72
deployments"* — i.e. it is a **software-release** delta on GB200, not a gain
"within four months of GB200 availability" (corrected 2026-09-19; the earlier
wording attributed the improvement to hardware age rather than to the
InferenceMAXv1 → v2 software jump). Stack: NVFP4 for MoE **and** dense layers,
single-batch overlap, **Dynamo for prefill-decode disaggregation**, HiCache radix
tree, NIXL **and** Mooncake as KV transfer backends. Baseline stated as 50 TPS/user
interactivity on H200.

**Measured per-chip, B300 vs GB300** ([InferenceX compare page](https://inferencex.semianalysis.com/compare/deepseek-r1-b300-vs-gb300),
DeepSeek-R1-0528 671B, 8K/1K, FP4):

| Interactivity | B300 tok/s/chip | GB300 NVL72 tok/s/chip |
|---|---:|---:|
| 73 tok/s/user | 3,748 | 6,969 |
| 126 tok/s/user | 1,016 | 3,763 |
| 178 tok/s/user | 719 | **545** |

`meas.` The inversion at 178 tok/s/user — where the **HGX B300 beats the NVL72
rack** — is the single most decision-relevant row on that page for this repo,
because it says the rack's advantage is a *throughput-regime* advantage, not a
latency one. ⚠️ **TO BE VERIFIED**: the compare page does not state which framework
(Dynamo-SGLang / Dynamo-TRTLLM / SGLang) produced these three points, nor the run
date; re-fetch via the API described in
[`../cross-cutting/inferencex-api.md`](../cross-cutting/inferencex-api.md) and
record the `framework` and `run_date` fields before citing them in a plan.

**What transfers to 8×B300.** The NVL72 results are about a **72-GPU NVLink
domain**. An HGX B300 node is an **8-GPU** domain. Wide-EP's 1.8× came from
spreading 256 experts over 32 ranks *inside* a coherent fabric; doing the same over
InfiniBand between HGX nodes pays a different price entirely. This repo's own
finding is consistent and blunt: wide-EP **"runs badly" on an 8-GPU domain** for
DeepSeek-V4.1-Flash, and the recommendation is independent TP4 or TP2 replicas
([`../matrix/recommendations.md §2.1`](../matrix/recommendations.md)). What *does*
transfer is EPLB — expert load imbalance exists at EP4/EP8 too, it is just cheaper
to fix.

### 1.5 Anthropic — Managed Agents (public engineering post)

Source: [*"Scaling Managed Agents: Decoupling the brain from the hands"*](https://www.anthropic.com/engineering/managed-agents),
**2026-04-08**. This is an *agent-platform* architecture post, not a GPU-serving
post, and it is included because §6's agentic row depends on it.

The design separates three things: a **stateless brain** (the harness running the
model loop), **replaceable hands** (sandboxes and tools, addressed through a
uniform `execute(name, input)`), and a **durable session** — an append-only event
log that lives *outside* the model's context window. On harness crash, a new
harness calls `wake(sessionId)` and resumes from the log; context is rebuilt by
calling `getEvents()` and transforming the slice *"for cache optimization"*.
Reported: time-to-first-token improved **60 % (p50)** and **90 %+ (p95)**
(`meas.`, self-reported), attributed to provisioning containers on demand through
tool calls instead of up front.

**What transfers.** Two things that change a serving architecture:

1. **The session, not the request, is the routing key.** If context is rebuilt
   from a durable log on every turn, the prefix presented to the engine is
   *deterministic and reconstructable* — which is precisely the condition under
   which prefix-cache-aware routing pays. Route by session id, pin the session to
   a replica while its KV is warm, and fall back to the global KV tier on eviction.
2. **Cold-start is in the agent loop, not just at deploy.** "Provision on demand"
   means the p95 TTFT of the *platform* includes sandbox start. The equivalent on
   the inference side is replica cold-start (§1.8): if a scale-up takes minutes,
   agentic p95 eats it.

⚠️ **TO BE VERIFIED**: no Anthropic or OpenAI post fetched for this document
discloses GPU topology, parallelism, PD ratios or per-GPU throughput. Claims about
their *inference* topology are not made here. (Third-party summaries exist; none
are primary and none are cited.)

### 1.6 Baseten — KV-aware routing in production

Source: [Baseten, 2026-03-16](https://www.baseten.co/blog/how-baseten-achieved-2x-faster-inference-with-nvidia-dynamo/).
Model **Qwen3-Coder-480B-A35B** (262K native context), stress test at **~50k input
/ ~1k output tokens**, **four replicas**, **89 % KV cache hit rate**, Dynamo's
LLM-aware router (*"Hashing incoming requests and organizing them in a Radix Tree"*,
routing on an *"overlap score between the request and the KV cache blocks already
active across all GPUs"*).

| Metric | KV routing vs round-robin | Label |
|---|---|---|
| TTFT, mean | **−50 %** | meas. |
| TTFT, P95 (shadowed production traffic from OpenRouter) | **−48 %** | meas. |
| TTFT, P99 | **−49 %** | meas. |
| TPOT, mean | **−34 %** | meas. |
| Requests/s | **+61 %** | meas. |
| Output tokens/s | **+62 %** | meas. |

Independent corroboration on smaller hardware
([Azure AKS engineering blog, 2026-03-16](https://blog.aks.azure.com/2026/03/16/dynamo-on-aks-part-3)):
8×H100 across 4 × `Standard_NC80adis_H100_v5`, Qwen3-32B, **~23,000 requests from
the Mooncake Tool Agent Traces**: TTFT mean **~20.4×** better, TTFT P99 **~15.9×**,
end-to-end mean **~4.3×**, E2E P99 **~3.8×** vs round-robin. `meas.` The
order-of-magnitude gap between Baseten's 2× and Azure's 20× is explained by prefix
overlap in the trace, not by the router — which is the point: **the value of
KV-aware routing is a function of your traffic's prefix reuse, and nothing else.**

**What transfers.** Everything. This is the highest ROI-per-line-of-config item in
the entire document, it requires no model change, and this repo's traffic
(90.2–97.0 % measured prefix hit on agentic traces) is in the regime where it pays
most. The decision rule: **if measured prefix-cache hit rate > ~40 %, KV-aware
routing is mandatory; below ~10 %, it is overhead and a least-loaded policy wins.**
⚠️ The 40 %/10 % thresholds are an estimate interpolated from the Baseten (89 %
hit → 2×) and Azure (tool-agent traces → 20×) points; no source states a
break-even, so measure on a traffic replay before committing.

### 1.7 Perplexity, Fireworks, Together, Databricks, Cloudflare

**Perplexity.** Open-sourced [`pplx-garden`](https://github.com/perplexityai/pplx-garden),
whose `fabric-lib` is an **RDMA TransferEngine plus a point-to-point MoE
dispatch/combine kernel** (MLSys'26 paper, [arXiv:2510.27656](https://arxiv.org/abs/2510.27656)).
The engineering blogs *Disaggregated Prefill and Decode*, *Efficient and Portable
Mixture-of-Experts Communication* and *Enabling Trillion-Parameter Models on AWS
EFA* are linked from that README.
⚠️ **TO BE VERIFIED** — `research.perplexity.ai` and `perplexity.ai/hub` returned
a JS/anti-bot interstitial to both WebFetch and `curl` on 2026-09-19, so **no
Perplexity number is quoted in this document.** The architectural fact that is
safe, because it is in the open-source README: they run MoE dispatch/combine as a
*P2P RDMA* kernel rather than through NCCL collectives, and they target **AWS EFA**
as well as InfiniBand — which matters directly for the `p6` overflow path in §4.5.

**Fireworks** ([fireworks.ai/inference](https://fireworks.ai/inference)):
*"Prefill and decode have different hardware profiles. We split them onto separate
pools so each scales independently, cutting latency without stranding GPUs"*;
*"disaggregated KV caching and prompt-aware routing"*; *"Frontier MoE models served
across nodes with composable parallelism"*; *"up to **4x** higher throughput"*
(**vendor**, no harness). The transferable content is the phrase "without stranding
GPUs" — the real argument for PD is *utilization*, not peak latency.

**Together, Databricks.** ⚠️ **TO BE VERIFIED** — no primary engineering post from
either was fetched for this document (the session's web-search budget was exhausted
before those queries ran). No claims are made about their architectures. This is an
open item in §Open questions.

**Cloudflare Workers AI** ([blog, 2023-09-27](https://blog.cloudflare.com/workers-ai/)).
Serverless GPU inference placed on the edge network; billing normalized to a
**"Neuron"** unit across modalities; two placement tiers — *"Regular Twitch Neurons"*
run *"wherever there's capacity"*, *"Fast Twitch Neurons"* run *"at nearest user
location"*. Rollout stated as 7 sites at launch → ~100 data centres by end-2023 →
near-global by end-2024. `vendor`. **What transfers:** the *two-tier placement*
idea — a cheap "anywhere with capacity" queue and an expensive "nearest/warmest"
queue — is exactly the right shape for mixing batch and interactive traffic on one
bare-metal cluster (§6, batch row). Note the date: this is a 2023 post and the
only primary Cloudflare source fetched; do not treat its capacity numbers as
current.

### 1.8 Cold-start systems: Modal and ServerlessLLM

**Modal GPU memory snapshots** ([blog, 2025-07-30](https://modal.com/blog/gpu-mem-snapshots)).
Checkpoints *"Device memory contents (GPU vRAM), such as model weights"*,
*"CUDA kernels"*, *"CUDA objects, like streams and contexts"* and *"Memory mappings
and their addresses"*, then replays them on restore.

| Workload | Before | After | Label |
|---|---:|---:|---|
| Parakeet (audio) | 20 s (P0) | **2 s** | meas. |
| ViT + `torch.compile` | 8.5 s | **2.25 s** | meas. |
| vLLM + Qwen2.5 | 45 s | **5 s** | meas. |

Requirement: NVIDIA drivers in the **570/575 branches** for the CUDA
checkpoint/restore APIs, described as **alpha**.

**ServerlessLLM** (Fu et al., **USENIX OSDI '24**,
[paper](https://www.usenix.org/system/files/osdi24-fu.pdf)). Three contributions:
a **loading-optimized checkpoint format** with a multi-tier loader, **live
migration of in-flight inference**, and **startup-time-optimized scheduling** that
places a model on the server whose local checkpoint locality minimizes
time-to-first-inference. Reported **10–200× latency reduction** vs state-of-the-art
serverless systems (`meas.`, paper's harness).

**What transfers to bare metal.** On a *bare-metal* cluster the serverless framing
is mostly irrelevant — replicas are long-lived — but three mechanisms are not:

1. **Checkpoint locality as a scheduling input.** Kimi-K3 is **1,560.9 GB on disk**
   (`METHODOLOGY.md §8`). Pulling that from network storage on every cold start is
   a multi-minute event; from node-local NVMe it is a bandwidth problem you control.
   Pin weights to node-local NVMe and make the scheduler prefer nodes that already
   have them — that is ServerlessLLM's third contribution, and it is free to
   implement as a node label plus affinity.
2. **Weight streaming GPU→GPU.** Dynamo's **ModelExpress** streams weights over
   NIXL/NVLink from an already-loaded replica, claimed **7× faster startup**
   (DeepSeek-V3 on H200, `vendor` —
   [Dynamo README](https://github.com/ai-dynamo/dynamo)). For a 1.56 TB checkpoint
   this is the difference between a viable and a non-viable scale-up.
3. **Snapshot/restore is not yet a bare-metal production tool at this scale.**
   Modal's numbers are for models orders of magnitude smaller than Kimi-K3, on an
   alpha driver API. ⚠️ **TO BE VERIFIED**: no published measurement of CUDA
   checkpoint/restore on a 1.5 TB multi-GPU TP8 process exists as of 2026-09-19.
   Plan cold-start around weight streaming and warm pools, not snapshots.

### 1.9 Anyscale / Ray and the vLLM wide-EP baseline

**Anyscale / Ray Serve LLM** ([blog, 2025-11-26](https://www.anyscale.com/blog/ray-serve-llm-anyscale-apis-wide-ep-disaggregated-serving-vllm)):
wide-EP via a `build_dp_deployment` builder (one ingress deployment + a `DPServer`
deployment, replicas registering with a `DPRankAssigner` actor to negotiate shared
IP/port for the vLLM engines), and PD via `build_pd_openai_app` with a
`PDProxyServer` that runs prefill at `max_tokens=1` *"to fill the KV cache"* then
hands metadata to the decode deployment. Reported **2.4k tok/s per H200 on Nebius
with InfiniBand** — the post's exact phrase is *"2.4k tps/H200 on Nebius with
Infiniband"* (`meas.`). Config shape from the post (the post writes the DP degree
as the symbol **`DP_SIZE`**, not a literal; corrected 2026-09-19, an earlier draft
presented `16` as if quoted):

```python
config = LLMConfig(
   model_loading_config="deepseek-ai/DeepSeek-R1",
   engine_kwargs=dict(
      data_parallel_size=DP_SIZE,      # post's symbol; 16 is this document's example
      enable_expert_parallel=True,
   ),
)
```

**vLLM project** ([blog, 2025-12-17](https://vllm.ai/blog/2025-12-17-large-scale-serving)):
*"a sustained throughput of **2.2k tokens/s per H200 GPU** in production-like,
multi-node deployments"* for DeepSeek with wide-EP, up from ~1.5k previously;
vLLM **v0.11.0** (V1 engine), **DeepEP** kernels, **ConnectX-7** InfiniBand, and
llm-d / Dynamo / Ray Serve LLM named as the deployment frameworks. `meas.`

These two independent 2.2–2.4k tok/s/H200 numbers are the best available
cross-check on what a well-tuned wide-EP deployment of a 671 B-class MoE achieves
on Hopper, and they are a useful sanity floor when reading this repo's B300
estimates for a *different* model — but they are **not** comparable to
`pairs.json` rows, which are decode-only per `METHODOLOGY.md §4`.

---

## 2. Open-source stacks end-to-end on Kubernetes, as of 2026-09-19

| Stack | Version at research date | Deployment unit | Routing | PD | KV tiering | Autoscaling | Maturity |
|---|---|---|---|---|---|---|---|
| **NVIDIA Dynamo** | containers `1.4.2`; model dev tags `1.5.0` / `1.6.0`; "New in 1.0" feature list | `DynamoGraphDeployment` CRD (+ `DynamoGraphDeploymentRequest` for zero-config), `ComputeDomain` for MNNVL | Dynamo Frontend+Router (`DYN_ROUTER_MODE=kv`) **or** Gateway API + Dynamo EPP | first-class (`prefill`/`decode` components) | **KVBM** GPU→CPU→SSD→remote + S3/Azure blob | **SLA Planner** (`ttft_ms`, `itl_ms`) | Production; **the only stack shipping recipes for this repo's exact models** |
| **llm-d** | **v0.9.0 (2026-08-17)** — corrected 2026-09-19 from v0.7: the repo README this row cited still advertises "Version 0.7 (May 2026)" and is stale, confirmed by `curl` against the GitHub releases API (`v0.9.0`, `published_at 2026-08-17T23:38:08Z`); see [`02-serving-stack-and-routing.md` §2.1](02-serving-stack-and-routing.md#21-version-pin). CNCF Sandbox since 2026-03; the well-lit-paths feature list below is v0.7's and stands unchanged | Helm/kustomize "well-lit paths" over Deployments + **LeaderWorkerSet** | Inference Gateway + **EPP** (prefix-cache-aware, predicted-latency) | well-lit path | tiered prefix cache (CPU/NVMe/network) | **KEDA + EPP** (queue / saturation / token-aware / SLO-aware), WVA, Kueue rebalancing | Production for the foundations; batch gateway + async are **experimental** |
| **AIBrix** | **v0.7.0 (2026-06-16)** | CRDs + operator (`kubectl apply -f aibrix-core-v0.7.0.yaml`) | LLM gateway, prefix-aware + load-aware | yes | distributed KV cache | LLM-specific autoscalers, SLO-driven GPU optimizer | Production at ByteDance scale; hybrid K8s+Ray orchestration |
| **KServe + vLLM** | **v0.20.0 (2026-08-06)** — corrected 2026-09-19 from v0.17: confirmed by `curl` against the GitHub releases API (`v0.20.0`, `published_at 2026-08-06T15:07:42Z`; `v0.21.0-rc0` is a pre-release); see [`02-serving-stack-and-routing.md` §2.1](02-serving-stack-and-routing.md#21-version-pin) | `LLMInferenceService` CRD | GIE **v1.3.0** EPP (v0.17); **v1.6.2** current, per §2.1 above | `spec.prefill` | via llm-d primitives | HPA/KEDA | `LLMInferenceService` **graduated to production-ready in 0.17**; built *on* llm-d |
| **SGLang router + PD** | router flags documented; SGLang PD roadmap issue #21703 targets 2026 Q2 items | plain processes / any orchestrator | `sglang_router --pd-disaggregation` | native (`--disaggregation-mode`) | HiCache (L1/L2/L3) | none built in | Engine-level; you supply the control plane |
| **Ray Serve LLM** | Ray **2.58** docs; Anyscale post 2025-11-26 | Python `LLMConfig` + Serve deployments | `PrefixCacheAffinityRouter` | `build_pd_openai_app` | via engine | Ray Serve autoscaling | Production; programmable rather than declarative |

Sources: [Dynamo README](https://github.com/ai-dynamo/dynamo),
[llm-d README](https://github.com/llm-d/llm-d) and
[well-lit paths](https://llm-d.ai/docs/0.7/well-lit-paths),
[AIBrix README](https://github.com/vllm-project/aibrix) +
[arXiv:2504.03648](https://arxiv.org/abs/2504.03648),
[KServe v0.17](https://kserve.github.io/website/blog/kserve-0.17-release),
[SGLang PD docs](https://docs.sglang.io/advanced_features/pd_disaggregation.html),
[Ray Serve LLM](https://docs.ray.io/en/latest/serve/llm/index.html).

### 2.1 NVIDIA Dynamo

**Components** (from the [README](https://github.com/ai-dynamo/dynamo) capability
table, `vendor` descriptions):

| Component | Function |
|---|---|
| Disaggregated prefill/decode | *"Separates prefill and decode into independently scalable GPU pools"* |
| KV-aware router | *"Routes requests based on worker load and KV cache overlap"* — *"2x faster TTFT"* |
| **KVBM** (KV Block Manager) | *"Offloads KV cache across GPU → CPU → SSD → remote storage"* |
| **ModelExpress** | *"Streams model weights GPU-to-GPU via NIXL/NVLink"* — *"7x faster cold-start"* |
| **Planner** | *"SLA-driven autoscaler that profiles workloads and right-sizes pools"* |
| **Grove** | *"K8s operator for topology-aware gang scheduling (NVL72)"* |
| **AISimulate** | *"Predicts serving behavior and searches deployment configurations offline"* |
| Fault tolerance | *"Canary health checks + in-flight request migration"* |

**Two routing topologies**, both OpenAI-compatible
([README](https://github.com/ai-dynamo/dynamo)): Dynamo-native
(`client → Frontend → Router → workers`) or Gateway API + GAIE
(`client → Gateway → EPP → Frontend sidecar (direct) → workers`). Choose the
second when the platform already standardizes on Gateway API and wants auth, rate
limiting and policy at the cluster edge.

**Zero-config deploy (`DGDR`, beta in 1.0)** — quoted verbatim from the README:

```yaml
# Zero-config deploy: specify model + SLA, Dynamo handles the rest
apiVersion: nvidia.com/v1beta1
kind: DynamoGraphDeploymentRequest
metadata:
  name: my-model
spec:
  model: Qwen/Qwen3-0.6B
  backend: vllm
  sla:
    ttft: 200.0   # ms
    itl: 20.0     # ms
  autoApply: true
```

**SLA Planner** ([planner guide](https://docs.nvidia.com/dynamo/components/planner/planner-guide)):
targets `ttft_ms` (default 500) and `itl_ms` (default 50) using AIConfigurator
forward-pass estimates plus live Forward Pass Metrics (batch composition, queue
depth, token counts). It runs two modes that compose:
**throughput-based (predictive)** sets a replica *floor* from a traffic forecast,
then **load-based (reactive)** may raise it above that floor subject to a global
GPU budget clamp. Key fields: `"optimization_target": "sla"`,
`"enable_throughput_scaling"`, `"enable_load_scaling"`, `"ttft_ms"`, `"itl_ms"`,
`"pre_deployment_sweeping_mode": "rapid" | "thorough" | "none"` (rapid ≈30 s,
thorough 2–4 h). **Required before it works**: profiler-generated engine
performance data (prefill TTFT vs ISL, decode ITL vs KV-cache utilization) in
ConfigMaps, or enough live FPM observations. Stated limitation: if a scaling
submission loses acknowledgement and a later error occurs, *"the Planner stops
automatic effect submission to avoid duplicating an action"* — i.e. it fails safe,
and someone must notice.

**Why Dynamo is the default recommendation for this repo:** it is the only stack
that ships **tested recipes for `moonshotai/Kimi-K3` and
`deepseek-ai/DeepSeek-V4.1-Flash` by name**
([recipes/](https://github.com/ai-dynamo/dynamo/tree/main/recipes)):

| Recipe | Backend | Shape |
|---|---|---|
| `kimi-k3/vllm/agg-{gb200,gb300,h200}-agentic` | vLLM | aggregated |
| `kimi-k3/vllm/disagg-gb300-agentic` | vLLM | prefill 2 nodes + decode 2 nodes |
| `kimi-k3/sglang/…` | SGLang | — |
| `deepseek-v4.1-flash/sglang/{agg,disagg}-gb200` | SGLang | TP4 each side |

**Kimi-K3 aggregated, quoted from
[`recipes/kimi-k3/vllm/agg-gb300-agentic/deploy.yaml`](https://github.com/ai-dynamo/dynamo/blob/main/recipes/kimi-k3/vllm/agg-gb300-agentic/deploy.yaml)** —
the load-bearing lines:

```yaml
apiVersion: nvidia.com/v1beta1
kind: DynamoGraphDeployment
metadata:
  name: kimi-k3-vllm-gb300-agg-agentic
spec:
  backendFramework: vllm
  components:
  - name: worker
    type: worker
    replicas: 1
    multinode:
      nodeCount: 4
    podTemplate:
      spec:
        containers:
        - name: main
          image: nvcr.io/nvidia/ai-dynamo/vllm-runtime:1.5.0-kimi-k3-dev.1
          args:
          - --model
          - moonshotai/Kimi-K3
          - --tensor-parallel-size
          - "16"
          - --decode-context-parallel-size
          - "16"
          - --dcp-comm-backend
          - a2a
          - --enable-expert-parallel
          - --moe-backend
          - deep_gemm_mega_moe
          - --all2all-backend
          - flashinfer_nvlink_two_sided
          - --load-format
          - fastsafetensors
          - --gpu-memory-utilization
          - "0.85"
          - --max-num-seqs
          - "32"
          - --enable-prefix-caching
          - --prefix-match-unit
          - "128"
          - --kv-cache-dtype
          - fp8
          - --attention-backend
          - TOKENSPEED_MLA
          - --kv-events-config
          - '{"publisher":"zmq","topic":"kv-events","endpoint":"tcp://*:20081","enable_kv_cache_events":true}'
          - --speculative-config
          - '{"model":"Inferact/Kimi-K3-DSpark","attention_backend":"TOKENSPEED_MLA","method":"dspark","num_speculative_tokens":7}'
        terminationGracePeriodSeconds: 60
```

Five things to read out of it, all relevant to a B300 deployment:

1. **`nodeCount: 4` × 4 GPUs = TP16 + DCP16 inside one NVLink (MNNVL) domain.**
   That is a GB300 NVL72 shape. On **8×B300 HGX** the equivalent domain is 8 GPUs,
   and this repo pins **TP8 + DCP8, one replica = one node**
   ([`../models/kimik3/b300.md`](../models/kimik3/b300.md),
   [`../matrix/recommendations.md §2.1`](../matrix/recommendations.md)). These do
   not contradict each other — different NVLink domain sizes — but do **not** copy
   `--tensor-parallel-size 16` onto an HGX node.
2. **`--kv-cache-dtype fp8`, `--enable-prefix-caching`, `--prefix-match-unit 128`**
   match this repo's pins exactly, including the mandatory 128-token match unit
   (this repo's reason: otherwise *"the hit boundary inflates to the Mamba state
   page"*, [`../matrix/recommendations.md §2.1`](../matrix/recommendations.md)).
3. **`--enable-expert-parallel` is present *with* DCP16** in NVIDIA's recipe,
   whereas this repo warns that `DCP8 + EP8` is the combination in
   [sglang#34260](https://github.com/sgl-project/sglang/issues/34260) and
   recommends DCP without EP. **Stated disagreement**: NVIDIA's recipe is vLLM on
   GB300; the repo's warning is SGLang on B300. Both can be true. ⚠️ **TO BE
   VERIFIED** on the actual node before enabling EP alongside DCP.
4. **`--kv-events-config` publishes KV events over ZMQ** — this is what makes the
   router KV-aware. Without it `DYN_ROUTER_MODE=kv` degrades to load-only routing.
5. **DSpark is configured with `num_speculative_tokens: 7`**, and the recipe's own
   comment shows the benchmarking-only variant pinning
   `"synthetic_acceptance_length": 4.2584`. This repo prices DSpark at **γ=5** with
   acceptance as the **single largest open uncertainty** in the tree
   ([`../matrix/recommendations.md §2.4`, benchmark B1](../matrix/recommendations.md)).
   The 4.2584 constant is a **synthetic harness value**, not a measurement — do not
   quote it as one.

**DeepSeek-V4.1-Flash disaggregated, quoted from
[`recipes/deepseek-v4.1-flash/sglang/disagg-gb200/deploy-generic.yaml`](https://github.com/ai-dynamo/dynamo/blob/main/recipes/deepseek-v4.1-flash/sglang/disagg-gb200/deploy-generic.yaml)**:

```yaml
  decode.yaml: |-
    model-path: deepseek-ai/DeepSeek-V4.1-Flash
    tp-size: 4
    ep-size: 4
    page-size: 256
    mem-fraction-static: 0.8
    max-running-requests: 256
    disaggregation-mode: decode
    disaggregation-transfer-backend: mooncake
    disaggregation-bootstrap-port: 30001
  prefill.yaml: |-
    model-path: deepseek-ai/DeepSeek-V4.1-Flash
    tp-size: 4
    ep-size: 4
    page-size: 256
    mem-fraction-static: 0.8
    max-running-requests: 256
    max-prefill-tokens: 16384
    disaggregation-mode: prefill
    disaggregation-transfer-backend: mooncake
    disaggregation-bootstrap-port: 30001
    load-balance-method: round_robin
```

The header comments in that file are worth more than the YAML, and are quoted
verbatim because each is an anti-pattern warning from the vendor:

> *"DSpark speculative decoding is absent here. SGLang refuses it under prefill
> and decode disaggregation for this model. The aggregated recipe ships it."*

> *"KV does NOT ride the NVLink fabric. The fabric is faster, but it needs both
> workers in one NVLink clique, because a fabric memory handle cannot be imported
> across cliques. A split gives no error: the deployment reports Ready and chat
> completions return HTTP 200 with content null."*

> *"Do not set the attention, MoE, or GEMM backend flags. SGLang resolves them for
> this model. A hand-set backend flag selects a slower fallback."*

The first is a **hard architectural trade-off for this repo**: this tree's cost
model prices DeepSeek-V4.1-Flash *with DSpark γ=5 enabled*, and removing it moves
$/1M output from **$1.50 → $4.69**
([`../matrix/recommendations.md §2.1`](../matrix/recommendations.md)). If SGLang
refuses DSpark under PD, then **PD and DSpark are mutually exclusive on SGLang for
this model**, and on a 268 GB card — where the whole model fits in 2–4 GPUs and KV
is not the binding constraint — **DSpark wins and PD should not be used**. That is
the single most consequential finding in this section for this repo's DeepSeek
deployment. ⚠️ **TO BE VERIFIED**: whether vLLM's PD path (NixlConnector) has the
same restriction; the Kimi-K3 disagg recipe uses
`'{"kv_connector":"NixlConnector","kv_role":"kv_both"}'` and *does* carry a
speculative config, which suggests vLLM does not.

### 2.2 llm-d

**v0.7 (2026-05)** shipped the well-lit paths described below; current latest is
**v0.9.0 (2026-08-17)** — the README still prints "Version 0.7 (May 2026)" and is
stale, confirmed by `curl` against the GitHub releases API (§2 table above). Joined
the **CNCF as a Sandbox project in 2026-03**, founded by
Red Hat, Google Cloud, IBM Research, CoreWeave and NVIDIA
([README](https://github.com/llm-d/llm-d)).

**Well-lit paths** ([v0.7 docs](https://llm-d.ai/docs/0.7/well-lit-paths)), grouped
as the docs group them, with maturity:

| Group | Path | Status |
|---|---|---|
| Intelligent routing | Optimized Baseline | core |
| | Predicted Latency-Based Routing | core (GA in v0.7) |
| Advanced KV-cache | Precise Prefix Cache Routing | core |
| | Tiered Prefix Cache (CPU RAM / NVMe / network storage) | core |
| Serving large models | Prefill/Decode Disaggregation | core |
| | Wide Expert-Parallelism | core |
| Operational | Flow Control (multi-tenant queuing) | core |
| | Workload Autoscaling | core |
| Batch | Asynchronous Processing | **experimental** |
| | Batch Gateway (OpenAI-compatible Batch API) | **experimental** |

**Validated performance** ([README](https://github.com/llm-d/llm-d), each row
linking a partner blog — `meas.` on the named hardware):

| Result | Hardware / model | Source |
|---|---|---|
| **3×** output throughput, **2×** faster TTFT, prefix-cache routing vs round-robin | Llama 3.1 70B, 4× AMD MI300X (Tesla / Red Hat) | llm-d blog |
| **40 %** TTFT and ITL reduction, predicted-latency scheduling vs heuristics | NVIDIA GPUs (Google) | llm-d blog |
| up to **70 %** higher tok/s with PD disaggregation vs standard vLLM | GPT-OSS on NVIDIA B200 (`p6-b200`), AWS | AWS ML blog |
| **10–30 %** throughput improvement with disaggregation on identical infrastructure | GPT-OSS-120B, Llama 3.3 70B, AMD MI300X (Oracle) | Oracle blog |
| **~50k output tok/s** cluster, **~3.1k output tok/s per decode GPU**, wide-EP | **16×16 NVIDIA B200** | [v0.5 blog, 2026-02-04](https://llm-d.ai/blog/llm-d-v0.5-sustaining-performance-at-scale) |
| **13.9×** throughput at 250 concurrent users, hierarchical KV offloading vs GPU-only (~185k tok/s sustained) | 4× NVIDIA H100, Llama-3.1-70B, IBM Storage Scale | v0.5 blog |
| **2.4×** greater resilience under congestion (7.1 % vs 17.1 % latency degradation), UCCL transport | — | v0.5 blog |

The **16×16 B200 → ~3.1k tok/s per decode GPU** row is the closest published
Blackwell-generation wide-EP anchor available, and it is worth holding next to this
repo's B300 estimates when reading §7 — though note it is a *different model* and
`pairs.json` rows are decode-only per `METHODOLOGY.md §4`, so the comparison is
directional only.

**Wide-EP requirements** ([path doc](https://llm-d.ai/docs/well-lit-paths/foundations/wide-expert-parallelism)):
**full-mesh InfiniBand/RoCE** for GPU-initiated RDMA; dispatch/combine over the
**DeepEP** backend on **NVSHMEM with `ibgda` transport**; **LeaderWorkerSet** for
the multi-node pod group; **NIXL** for decode-side KV block retrieval over RDMA.
Its stated motivation is KV pressure in *"very large MoE models like DeepSeek-R1
consuming 500GB+ of RAM"* whose *"MLA [attention] replicate[s] the KV cache when
sharded with tensor parallelism"* — which is exactly the replicated-KV effect
`METHODOLOGY.md §3` pins for this repo's MLA models and the reason Kimi-K3's B300
row uses **DCP8** rather than plain TP8.

**Autoscaling** ([guides/workload-autoscaling](https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/README.md)) —
this is the best-written public treatment of the subject and §5.1 quotes it. Five
paths:

| Path | Signal | Extra components | Scale-to-zero |
|---|---|---|---|
| Queue-based (recommended default) | EPP queue depth + running requests | KEDA + Prometheus | supported |
| Saturation-based | normalized pool saturation 0.0–1.0+ | KEDA + Prometheus | "needs validation" |
| Token-aware | EPP in-flight tokens ÷ calibrated `peakPrefillThroughput` → *seconds of backlog*; decode on per-pod KV occupancy | + one calibration run per hardware/model | "needs validation" |
| SLO-aware | EPP **estimated** TTFT/TPOT ÷ SLO, via a Prometheus recording rule and a KEDA `ScaledObject` with an expr-lang formula | KEDA + 1 recording rule | via KEDA |
| WVA (legacy) | `wva_desired_replicas` from KV utilization, queue depth, performance budgets across **heterogeneous** GPU types | WVA controller | supported |

Plus **Kueue-based replica rebalancing (experimental)**: each model gets a
`ClusterQueue` with a guaranteed GPU floor in a shared cohort, replica pods are
admitted only when quota is free, *"so an idle model's GPUs are lent to a busy one
and reclaimed by preemption"*, and *"enforcement happens below the HPA"*. **This is
the right answer to this repo's central scheduling problem** — five models sharing
one fixed bare-metal GPU budget, where Kimi-K3 alone wants a whole node — and it is
labelled experimental, so treat it as a design target rather than a dependency.

**Token-aware autoscaling** deserves a direct quote because it is the correct
critique of request-count scaling:

> *"An 8192-token prompt is 16x the prefill work of a 512-token one, and a request
> or queue-depth counter rates them the same, so the same request rate can mean a
> third of a replica or three replicas of prefill work."*

### 2.3 AIBrix

**v0.7.0, 2026-06-16** ([README](https://github.com/vllm-project/aibrix)); white
paper [arXiv:2504.03648](https://arxiv.org/abs/2504.03648). Installed as plain
manifests:

```shell
kubectl apply -f "https://github.com/vllm-project/aibrix/releases/download/v0.7.0/aibrix-dependency-v0.7.0.yaml" --server-side
kubectl apply -f "https://github.com/vllm-project/aibrix/releases/download/v0.7.0/aibrix-core-crds-v0.7.0.yaml" --server-side
kubectl apply -f "https://github.com/vllm-project/aibrix/releases/download/v0.7.0/aibrix-core-v0.7.0.yaml"
```

Feature set from the README: high-density LoRA management, LLM gateway and routing,
LLM-app-tailored autoscaler, unified AI runtime sidecar, distributed inference,
**distributed KV cache**, cost-efficient **heterogeneous serving**, GPU hardware
failure detection. The paper's headline: the distributed KV cache yields
*"a **50 % increase in throughput** and a **70 % reduction in inference latency**"*
(paper's own harness — label it `meas.` within that paper, `vendor` in provenance),
and orchestration is **hybrid: Kubernetes for coarse-grained scheduling, Ray for
fine-grained execution**.

**Where it fits this repo:** AIBrix's differentiators are LoRA density and
heterogeneous cost-aware serving. This repo serves **five full checkpoints and no
LoRA adapters**, on **one GPU SKU** (8×B300) plus an AWS `p6` overflow. That
removes AIBrix's two strongest advantages. Its **GPU failure detection** is a real
gap-filler on bare metal, but is also available from DCGM + the GPU Operator
directly. **Decision rule: choose AIBrix if the fleet is heterogeneous or
LoRA-heavy; otherwise Dynamo or llm-d dominate for this workload.**

### 2.4 KServe + vLLM

**v0.17, 2026-03-13** ([release blog](https://kserve.github.io/website/blog/kserve-0.17-release))
is where `LLMInferenceService` **graduated from experimental to production-ready**,
is *built on llm-d*, and integrated **Gateway Inference Extension v1.3.0**. The
release note also flags a **breaking Helm chart restructuring** — upgrading from
v0.16 requires a migration guide, not `helm upgrade`. Current latest is
**v0.20.0 (2026-08-06)**, confirmed by `curl` against the GitHub releases API
(§2 table above); GIE is now at v1.6.2 per
[`02-serving-stack-and-routing.md` §2.1](02-serving-stack-and-routing.md#21-version-pin).

Minimal example, quoted verbatim from the release blog:

```yaml
apiVersion: serving.kserve.io/v1alpha2
kind: LLMInferenceService
metadata:
  name: llama3-serving
spec:
  model:
    uri: hf://meta-llama/Llama-3.1-8B-Instruct
    name: meta-llama--Llama-3.1-8B-Instruct
  replicas: 3
  template:
    spec:
      containers:
        - name: vllm
          resources:
            limits:
              nvidia.com/gpu: "1"
  router:
    gateway:
      managed: {}
    route:
      httpRoute: {}
    scheduler:
      pool: {}
```

Prefill/decode separation, quoted from the
[configuration guide](https://kserve.github.io/website/docs/model-serving/generative-inference/llmisvc/llmisvc-configuration):

```yaml
spec:
  replicas: 1
  template:
    containers:
      - name: main
        image: vllm/vllm-openai:latest
        args:
          - "--enforce-eager"
        resources:
          limits:
            nvidia.com/gpu: "1"
  prefill:
    replicas: 2
    template:
      containers:
        - name: main
          image: vllm/vllm-openai:latest
          args:
            - "--enable-chunked-prefill"
          resources:
            limits:
              nvidia.com/gpu: "2"
```

Top-level spec fields: `router` (gateway, HTTPRoute, scheduler exposure),
`scheduler` (InferencePool reference or creation), `parallelism`
(`tensor`, `data`, `dataLocal`, `expert`, `dataRPCPort`), `worker` (worker pod
template for multi-node **LeaderWorkerSet** deployments), `prefill`, `template`.

**Where it fits:** KServe is the right choice when the organization already runs
KServe for classical models and wants one CRD surface. It is llm-d underneath, so
its ceiling is llm-d's ceiling; the cost is one more abstraction between you and
the engine flags, which matters for models like Kimi-K3 whose working config is a
30-flag vLLM command line.

### 2.5 The routing layer: Gateway API Inference Extension, and gateways

**GAIE** ([InferencePool API](https://gateway-api-inference-extension.sigs.k8s.io/api-types/inferencepool/))
is the Kubernetes-standard way to express "a pool of model servers with a smart
picker". `InferencePool` (`inference.networking.k8s.io/v1`) has a pod `selector`,
`targetPorts`, and an `endpointPickerRef` naming the **EPP** service. The EPP
*"tracks key metrics on each model server (i.e. the KV-cache utilization, queue
length of pending requests, active LoRA adapters, etc.) and routes incoming
inference requests to the optimal model server replica based on these metrics"*.
`failureMode: FailOpen` forwards to a gateway-chosen endpoint if the EPP is
unresponsive — **set this**, or an EPP outage becomes a total outage. As of
**v1.5.0** `endpointPickerRef` is optional, allowing an `InferencePool` without a
user-managed EPP.

Dynamo plugs into this directly via its **Dynamo Endpoint Picker Plugin** with
workers in `--router-mode direct` ([README](https://github.com/ai-dynamo/dynamo)),
so "Dynamo vs GAIE" is a false choice — you can have Dynamo's KV router *inside*
the standard gateway.

⚠️ **TO BE VERIFIED**: **Envoy AI Gateway**. Its documented URL
`aigateway.envoyproxy.io` 301-redirected to `theagentrouter.ai` on 2026-09-19 and
was not fetched, so its token-based rate limiting and provider-routing features are
**not** characterized here. KServe's release blog does state that
`LLMInferenceService` provides *"token-based rate limiting through Envoy AI Gateway
integration"* — cited as KServe's claim, not Envoy's documentation.

### 2.6 Multi-node pod grouping: LeaderWorkerSet and DisaggregatedSet

Every multi-node shape in this document (TP16 across GB300 nodes, PD pools, wide-EP
groups) needs pods scheduled and rolled out **as a group**.
[**LeaderWorkerSet**](https://github.com/kubernetes-sigs/lws) provides exactly that:
a leader + workers as one unit of replication, with group-level rolling updates.
Gang scheduling is **Alpha** and the API may change
([lws#167](https://github.com/kubernetes-sigs/lws/issues/167) is the open
discussion), so for strict all-or-nothing placement pair it with **Kueue** or
**Volcano**, or with Dynamo's **Grove** operator.

LWS also now ships a **`DisaggregatedSet`** API — one child LWS per role. Quoted
verbatim from
[`docs/examples/disaggregatedset/basic/vllm.yaml`](https://github.com/kubernetes-sigs/lws/blob/main/docs/examples/disaggregatedset/basic/vllm.yaml)
(header comment included because it is an honest maturity signal):

```yaml
# DisaggregatedSet basic prefill/decode inference with vLLM.
# vLLM streams KV cache prefill->decode via --kv-transfer-config. The
# connector (NIXL, LMCache, etc.) and prefill<->decode wiring are
# deployment-specific; the config below is a starting point, not turnkey.
apiVersion: disaggregatedset.x-k8s.io/v1
kind: DisaggregatedSet
metadata:
  name: vllm-disagg
spec:
  roles:
    - name: prefill
      spec:
        leaderWorkerTemplate:
          size: 1
          workerTemplate:
            spec:
              containers:
                - name: vllm
                  image: vllm/vllm-openai:v0.28.0 # pinned; bump to the latest release
                  command:
                    - sh
                    - -c
                    - "python3 -m vllm.entrypoints.openai.api_server --port 8080
                       --model meta-llama/Meta-Llama-3.1-8B-Instruct
                       --kv-transfer-config '{\"kv_connector\":\"NixlConnector\",\"kv_role\":\"kv_producer\"}'"
    - name: decode
      # ... identical, with "kv_role":"kv_consumer"
```

### 2.7 KV tiering: LMCache, HiCache, KVBM, Mooncake Store

| Layer | Engine | Enablement | Evidence |
|---|---|---|---|
| **LMCache** | vLLM (also SGLang, Dynamo) | `LMCACHE_L1_SIZE_GB=16`; in vLLM production-stack, Helm `lmcacheConfig: {enabled: true, cpuOffloadingBufferSize: "20"}` | MP-mode blog (below) |
| **HiCache** | SGLang | `--enable-hierarchical-cache --hicache-ratio 2 --hicache-write-policy write_through --hicache-storage-backend nixl` | [Dynamo KV-offload doc](https://docs.nvidia.com/dynamo/components/kvbm) |
| **FlexKV** (experimental) | vLLM | `FLEXKV_CPU_CACHE_GB=32`, `DYNAMO_USE_FLEXKV=1` | same |
| **KVBM** | Dynamo | GPU → CPU → SSD → remote, plus S3/Azure blob in 1.0 | [Dynamo README](https://github.com/ai-dynamo/dynamo) |
| **Mooncake Store** | SGLang PD transport + store | `--disaggregation-transfer-backend mooncake`; `uv pip install mooncake-transfer-engine` | [SGLang PD docs](https://docs.sglang.io/advanced_features/pd_disaggregation.html) |

**LMCache MP mode** ([blog, 2026-04-03](https://blog.lmcache.ai/en/2026/04/03/lmcaches-new-architecture-boosts-moe-inference-performance-by-10x/)):
replaces per-process KV buffers with *"a unified KV-cache layer"* shared across
serving processes. On **8× H100 80GB**, **Qwen3-235B-A22B-Instruct-2507-FP8**,
8-way data-parallel vLLM with expert parallelism, server launched
`--l1-size-gb 400 --eviction-policy LRU`: TTFT mean **0.29 s vs 3.98 s (13.7×)**,
TTFT p99 **1.30 s vs 13.55 s (>10×)**, decoding throughput **37.47 vs 9.81 tok/s
(≈4×)**. `meas.` The mechanism matters more than the multiplier: **with N
data-parallel replicas on one node, N private CPU caches waste N−1 copies of every
shared prefix.** An 8×B300 node running 8 TP1 Qwen3.8-27B replicas (this repo's
recommended shape) is exactly that topology.

llm-d's independent number for the same idea: **13.9× at 250 concurrent users**,
4×H100, hierarchical KV offloading vs GPU-only ([v0.5 blog](https://llm-d.ai/blog/llm-d-v0.5-sustaining-performance-at-scale)).
Two independent stacks reporting ~14× on host-memory KV tiering is strong evidence
that the host-DRAM tier is the highest-leverage cheap addition to any of these
architectures. Per-model KV/token figures that decide how much DRAM to buy are in
`METHODOLOGY.md §8` and
[`../cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md).

### 2.8 Which stack for which model class

| Model class in this repo | Recommended stack | Decision rule | Trade-off accepted |
|---|---|---|---|
| **Kimi-K3** (2.8T MoE, one whole node) | **Dynamo** (vLLM backend, aggregated), LWS/Grove for the node group | Only stack with a named, tested Kimi-K3 recipe; TP8+DCP8 is a single-node group so you need gang scheduling and nothing else | You inherit NVIDIA's release cadence (dev-tagged images ahead of numbered releases) |
| **DeepSeek-V4.1-Flash / -NVFP4** (TP2–TP4, fits ≤ half a node) | **Dynamo or llm-d**, aggregated, KV-aware routing, **no PD** | PD's benefit is small when the model fits in 4 GPUs and KV/token is 890 B; PD's cost is losing DSpark on SGLang (§2.1), worth 3.13× output per byte | Give up independent prefill scaling; accept prefill/decode interference, mitigated by chunked prefill |
| **Qwen3.8-27B** (dense, TP1 × 8 replicas/node) | **llm-d** optimized baseline, or plain vLLM + GAIE | Eight independent single-GPU replicas is a plain Deployment; all the value is in the router and a shared host-DRAM KV tier | No PD, no wide-EP — none apply |
| **Marlin-2B** (video VLM, TP1) | **Dynamo multimodal E/P/D** or plain vLLM | Dynamo 1.0 ships *"Disaggregated encode/prefill/decode with embedding cache — 30% faster TTFT on image workloads"* (`vendor`; ⚠️ **TO BE VERIFIED** — the 30 % figure is **not** in the Dynamo README's Key Results or capability tables as fetched 2026-09-19, and no backing recipe or benchmark page was located; the E/P/D *architecture* is real, the multiplier is unsourced), which is the only published architecture addressing the encode stage separately | Video is prefill/encode-bound; a text-oriented router adds little |
| Mixed, all five | **one control plane, several pools** | Never one router config for five models; one gateway, five `InferencePool`s, per-pool policies | Operational surface |

---

## 3. Slurm-based serving

Many GPU shops already run Slurm and have no Kubernetes. Serving from Slurm is
viable, and for a fixed-size bare-metal cluster with a small number of long-lived
model endpoints it is often *sufficient*.

### 3.1 The shape

1. **Long-running service jobs.** Submit the engine as a normal batch job with a
   long or unlimited `--time`, in a dedicated partition or reservation so that
   training jobs cannot preempt it. The job's `srun` starts `vllm serve` /
   `python -m sglang.launch_server` on the allocated nodes, and the job's lifetime
   is the endpoint's lifetime.
2. **Containers via Pyxis + Enroot.**
   [Pyxis](https://github.com/NVIDIA/pyxis) is *"a SPANK plugin for the Slurm
   Workload Manager"* that *"allows unprivileged cluster users to run containerized
   tasks through the `srun` command"*, adding `--container-image` and
   `--container-mounts`. It requires [Enroot](https://github.com/NVIDIA/enroot)
   `3.1.0`, and it *"Supports multi-node MPI jobs through PMI2 or PMIx"* — which is
   what makes multi-node TP/PP work under Slurm. **Version pinning warning from the
   README**: *"Since Slurm 21.08, pyxis must be compiled against the release of
   Slurm that is going to be deployed on the cluster"*, otherwise Slurm refuses to
   load it with `Incompatible plugin version`.
3. **Service discovery and load balancing.** Slurm has none. The job must register
   its `host:port` somewhere the front end reads. The published pattern (below)
   uses a small database plus a gateway; a simpler one is an
   `sglang_router`/nginx/HAProxy upstream list regenerated from
   `squeue`/`scontrol` output.
4. **Autoscaling.** Slurm's own queue is the scaler: submit N service jobs, let
   Slurm place them, and add or cancel jobs from an external controller.

### 3.2 A published Slurm+K8s+vLLM design

[arXiv:2511.21413](https://arxiv.org/html/2511.21413v1), *"Automated Dynamic AI
Inference Scaling on HPC-Infrastructure: Integrating Kubernetes, Slurm and vLLM"*,
splits the system into a **Kubernetes management layer** and an **HPC compute
layer**: a Web Gateway validates requests and looks up available vLLM endpoints in
PostgreSQL; a Job Worker compares desired vs actual instances and triggers
submissions; **Slurm Submit deploys via SSH with `sbatch`**; an Endpoint Gateway
assigns ports and registers new endpoints; an Endpoint Worker health-checks them
with a **30-minute startup timeout**; a Metrics Gateway exposes Prometheus targets.

Scaling trigger, quoted: *"A queue time above 5 seconds over 30 sustained seconds
triggered instantiation of an additional model instance"*, explicitly using GPU
metrics rather than request count.

Measured on Mistral Small 3.2 24B, GPU-L = 1× H100, Mellanox InfiniBand HDR100
(`meas.`):

| Concurrent requests | E2E latency | TTFT | Throughput |
|---:|---:|---:|---:|
| 100 | 1,149.93 ms | 207.35 ms | 5.79 req/s |
| 500 | 3,076.11 ms | 1,133.02 ms | 19.31 req/s |
| 1000 | 9,419.23 ms | 3,109.98 ms | 26.95 req/s |

The paper reports *"approximately 500 ms"* of Web Gateway overhead at high
concurrency and identifies the **gateway, not the GPU**, as the bottleneck at 1000+
concurrent requests.

### 3.3 When Slurm is good enough, and when it is not

| Condition | Verdict |
|---|---|
| Fixed set of model endpoints, changed by a human weekly | **Good enough.** Kubernetes buys you nothing a `sbatch` script and HAProxy do not. |
| Cluster already runs Slurm for training and you want night-time capacity return (§1.1) | **Good.** One scheduler owning all GPUs is strictly simpler than two fighting. |
| You need KV-aware routing | **Not good enough out of the box** — but `sglang_router`, Dynamo's frontend, or an `InferencePool`-less EPP can run *inside* a Slurm job as the front end. |
| You need PD disaggregation with RDMA KV transfer | **Workable** — SGLang's `--disaggregation-*` flags do not care about the orchestrator, and Slurm+Pyxis gives you IB devices natively with no CNI wrangling. This is arguably *easier* than Kubernetes. |
| You need per-request autoscaling with seconds-scale reaction | **Not good enough.** Slurm scheduling intervals plus a 1.56 TB weight load is minutes. |
| You need rolling upgrades with drain and zero dropped requests | **Not good enough** without building it: Slurm has no readiness/endpoint concept. |

**Decision rule:** if the cluster's GPU count is fixed, the model set is small, and
the SLA is "capacity, not elasticity", stay on Slurm and spend the saved complexity
budget on the router and the KV tier. Move to Kubernetes when you need
**(a)** automated scale-up/down inside minutes, **(b)** rolling upgrades without a
maintenance window, or **(c)** more than a handful of model pools sharing one
budget.

---

## 4. Topology patterns

Six patterns, in ascending order of coupling. The **rule of thumb that orders
them**: prefer the *least coupled* topology that fits the model and meets the SLO,
because coupling is what turns one GPU failure into an outage.

### 4.1 Single-node replicas behind a load balancer (TP ≤ 8)

One replica = one TP group inside one NVLink domain. N replicas per node or per
cluster, behind a KV-aware router. No inter-node traffic on the model path at all.

**This is the correct default for four of this repo's five models**:
DeepSeek-V4.1-Flash (TP4, or 4 × TP2 for batch), DeepSeek-V4.1-Flash-NVFP4
(2 × TP4/EP4 per node), Qwen3.8-27B (**8 × TP1 per node**), Marlin-2B (TP1)
([`../matrix/recommendations.md §2.1`](../matrix/recommendations.md)). Kimi-K3 is
the exception only because one replica *is* the whole node (TP8 + DCP8).

Failure domain: one replica. Rolling upgrade: trivial. Router requirement:
prefix-aware, because with 8 independent Qwen replicas on a node, naive round-robin
destroys the prefix cache 8 ways (§2.7).

### 4.2 Multi-node TP / PP

One replica spanning nodes over InfiniBand/RoCE. `METHODOLOGY.md §4` prices the
comm overhead at **5–15 % for TP=8 over NVLink, 20–40 % for multi-node TP over
InfiniBand** — i.e. crossing the node boundary costs roughly a quarter of your
throughput, permanently.

**Use it only when the model does not fit.** On 268 GB cards, none of this repo's
models require it: Kimi-K3's 1,560.9 GB checkpoint fits one node at 195 GB/GPU
(`METHODOLOGY.md §8`). This repo's DeepSeek row says it explicitly:
*"16/32 GPUs fit but cross InfiniBand for no gain; use DP2xTP8 replicas instead of
TP16"* (`pairs.json`, `deepseek41f/h100`).

### 4.3 PD-disaggregated pools with NIXL / Mooncake over IB

Separate prefill and decode pools, KV transferred over RDMA
(`NixlConnector` for vLLM, Mooncake or NIXL for SGLang). Manifests: §2.1
(Dynamo), §2.6 (DisaggregatedSet), §2.4 (KServe `spec.prefill`).

**Sizing formula** (derived here from the phase throughputs, `est.`):

```
prefill_nodes   input_tokens_per_s
------------- = ------------------------------------------------
decode_nodes    output_tokens_per_s × (prefill_rate / decode_rate)

where prefill_rate  = sustained prefill tokens/s per node (cache MISSES only)
      decode_rate   = sustained output tokens/s per node
```

Worked with DeepSeek's own published per-node rates (§1.1: 73.7k in/s prefill incl.
cache hits, 14.8k out/s decode, H800): at their 608 B in : 168 B out ratio with
56.3 % cache hits, uncached input is 266 B tokens. `266/73.7k : 168/14.8k` =
**3.61M : 11.35M** node-seconds (3,605,102 s : 11,351,351 s; recomputed with
`python3` 2026-09-19 — an earlier draft mislabelled these `3.61k : 11.35k`,
off by 1,000× on both sides, so the ratio it produced was right)
→ **prefill:decode ≈ 1 : 3.1** (`est.` from their
numbers). Their deployed unit sizes were 4-node prefill and 18-node decode, a
different ratio — the difference is that deployment *units* are not pool sizes and
the counts of each unit are not published. ⚠️ **TO BE VERIFIED**: the number of
prefill and decode units DeepSeek ran concurrently is not in the post, so the true
pool ratio cannot be recovered.

**Go/no-go gate for PD, in order:**

1. **Does KV/token make the transfer cheap?** Compute `ctx × kv_bytes_per_token`
   and divide by the achievable fabric rate. DeepSeek-V4.1-Flash at 890 B/token:
   **~114 MB at 128k → ~2.3 ms at 400 Gbps** (`est.`) → cheap. Kimi-K3 at
   13.5 KiB/token: **~1.77 GB at 128k → ~35.4 ms** (`est.`) → expensive. Mooncake's
   own derived floor for a 70 B dense model on 8×H800 was **19 GB/s** (§1.2).
2. **Does the model even need more than one node?** If a replica fits in ≤ 4 GPUs,
   PD mostly buys you the *ability to scale the phases independently*, which only
   pays under a strongly skewed and *variable* input:output ratio.
3. **What does PD cost you in features?** On SGLang, for DeepSeek-V4.1-Flash, it
   costs **DSpark** (§2.1) — and this tree prices DSpark as a **3.13× output-per-byte**
   lever ([`../matrix/recommendations.md §2.1`](../matrix/recommendations.md)).
   That alone settles it for this repo's DeepSeek pool.
4. **Is the gain measured for *your* workload?** Published gains span
   **10–30 %** (Oracle, MI300X) to **70 %** (AWS, B200, GPT-OSS) — both from
   llm-d's own README (§2.2). That is a wide band; measure.

**Verdict for this repo:** PD is **not** recommended for DeepSeek-V4.1-Flash
(DSpark conflict + the model fits in 4 GPUs), **not** recommended for Qwen3.8-27B
or Marlin-2B (single GPU), and is the **only** disaggregation candidate for
Kimi-K3 — where it is marginal on KV-transfer cost and is exactly the shape
NVIDIA's `disagg-gb300-agentic` recipe implements (2 prefill nodes + 2 decode
nodes, `NixlConnector`, `kv_role: kv_both`). Benchmark before adopting.

### 4.4 Wide-EP across a rack

Experts spread over many ranks in one coherent fabric. Evidence: **EP32 gives 1.8×
over EP8** on GB200 NVL72 at 100 tok/s/user (NVIDIA, §1.4); **~3.1k output tok/s
per decode GPU on 16×16 B200** (llm-d v0.5, §2.2); **2.2–2.4k tok/s/H200** (vLLM
and Anyscale, §1.9). Requirements: full-mesh IB/RoCE, DeepEP over NVSHMEM `ibgda`,
LWS for the group, NIXL for decode-side KV fetch (§2.2), and EPLB to stop one hot
expert from serializing the step (§1.1, §1.4).

**Does not apply to this repo's B300 nodes**, and this is a real finding rather
than an omission: an 8-GPU NVLink domain is not a rack, and
[`../matrix/recommendations.md §2.1`](../matrix/recommendations.md) states wide-EP
*"runs badly"* on it for DeepSeek-V4.1-Flash. If a GB300 NVL72 rack is added later,
revisit — that is the hardware these numbers describe.

### 4.5 Hybrid with cloud overflow

Bare-metal base capacity + burst to AWS `p6`. Three constraints from this tree:

- **Price.** The `high` tier is **$15.00/GPU-h** (OCI `BM.GPU.B300.8`) against the
  `low` tier **$7.40** (Hyperstack HGX B300)
  ([`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md),
  `METHODOLOGY.md §8`). **AWS `p6-b300` — the overflow path this section is
  actually about — is neither**: `p6-b300.48xlarge` on-demand is
  **$17.802/GPU-h** ($142.416 / 8), with capacity blocks at **$14.04**
  ([cloud-pricing.md §AWS](../cross-cutting/cloud-pricing.md)). Corrected
  2026-09-19; an earlier draft wrote "`p6-b300`/OCI B300 `high` tier is $15.00",
  conflating the two vendors. Overflow therefore costs **2.0× (OCI)** to
  **2.4× (AWS on-demand)** per token — consistent with `METHODOLOGY.md §8`'s
  "hyperscalers run 2.2–2.4× neocloud rates". Burst for peaks, never for base.
- **Fabric.** AWS uses **EFA**, not InfiniBand. Perplexity's `fabric-lib` explicitly
  targets EFA ([pplx-garden](https://github.com/perplexityai/pplx-garden)), and
  NCCL/DeepEP behaviour differs. Keep overflow replicas **single-node** (§4.1) so
  the fabric never appears on the model path.
- **Cold start.** A burst replica must load weights. Kimi-K3 is 1,560.9 GB — do not
  put Kimi-K3 in the overflow tier at all. Qwen3.8-27B (30.87 GB FP8) and
  Marlin-2B (5.444 GB BF16) are the natural overflow models.

**Decision rule:** overflow the *smallest, most elastic* model, keep the big MoE
pinned to owned metal, and route by model rather than by load.

### 4.6 Multi-region active-active

Two or more clusters, each independently able to serve, with the router doing
health-based failover. llm-d v0.5 added *"active-active HA"* with *"dynamic
discovery of vLLM pods"* ([v0.5 blog](https://llm-d.ai/blog/llm-d-v0.5-sustaining-performance-at-scale)).
Cloudflare's two-tier placement (§1.7) is the commercial version.

The hard part is **KV cache locality**: a global KV tier across regions is a WAN
round trip and defeats the purpose, so each region keeps its own cache and a
failover costs you a full cold prefill. Consequence: **route sessions
region-stickily and accept that a regional failover is a cache-cold event.** Size
each region for `total_peak / (n_regions − 1)` if you want true active-active, which
is a ≥ 2× capacity tax at n=2.

⚠️ **TO BE VERIFIED**: no primary source fetched for this document measures
multi-region LLM failover latency or cache-warm-up cost. The capacity arithmetic
above is standard HA sizing, not an LLM-specific measurement.

---

## 5. Anti-patterns seen in the wild

Each one has a source, a failure mode, and a fix.

### 5.1 CPU/GPU-utilization HPA

**The claim, from llm-d's own guide** ([workload-autoscaling README](https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/README.md)):

> *"Traditional autoscaling indicators like resource utilization metrics (CPU/GPU)
> are often lagging indicators — they only reflect saturation after it has already
> occurred, by which time latency has spiked and requests may be failing. For LLM
> inference, this problem is compounded by the fact that GPU utilization is often
> pegged near 100% during active batching regardless of actual load, making it an
> entirely unreliable signal."*

**Failure mode:** GPU utilization is a duty-cycle metric. Continuous batching keeps
it pinned whether the engine is running 5 requests or 500, so the HPA either never
scales or oscillates. CPU is worse — the engine process is mostly idle.

**Fix:** KEDA on engine-level signals. The minimum viable version is
`vllm:num_requests_waiting` per replica; better is llm-d's **token-aware** path
(in-flight tokens ÷ a calibrated `peakPrefillThroughput`, giving *seconds of
backlog* directly comparable to a TTFT SLO budget) or the **SLO-aware** path
(EPP-estimated TTFT/TPOT ÷ SLO through a Prometheus recording rule and a KEDA
`ScaledObject`). Dynamo's equivalent is the SLA Planner's `ttft_ms`/`itl_ms`
(§2.1). Watch the whole loop, not one metric — llm-d's failure taxonomy names the
three handoffs: *demand signal saturated but replicas flat*, *replica target climbs
but the pool does not grow*, *replicas flapping*.

**This repo's corollary:** a scale-up that takes minutes (weight load) cannot be
driven by a lagging signal at all. Predictive scaling (Planner's throughput mode,
a floor from a traffic forecast) plus a warm pool is the only shape that works for
Kimi-K3.

### 5.2 RDMA without the plumbing

**Sources:** [NVIDIA GPU Operator GPUDirect RDMA docs](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-operator-rdma.html)
and the RDMA overlay of NVIDIA's own DeepSeek recipe
([`deploy-gke-rdma.yaml`](https://github.com/ai-dynamo/dynamo/blob/main/recipes/deepseek-v4.1-flash/sglang/disagg-gb200/deploy-gke-rdma.yaml)).

**Failure mode:** the pods come up, NCCL falls back to TCP over the pod network,
and everyone spends a week wondering why multi-node throughput is a third of
expectation — or, worse, the silent-null failure quoted in §2.1.

**What must actually be present:**

- Either **DMA-BUF** (Open Kernel module driver, CUDA ≥ 11.7, Linux ≥ 5.12) or the
  legacy **`nvidia-peermem`** module with MLNX_OFED/DOCA-OFED. DMA-BUF requires
  the Open Kernel module driver, **CUDA ≥ 11.7, Linux ≥ 5.12 and Turing or newer**,
  and makes MLNX_OFED/DOCA-OFED *optional*; the legacy path works with any
  supported driver and any CUDA but **requires** MLNX_OFED/DOCA-OFED. Helm flags,
  quoted:
  `--set driver.rdma.useHostMofed=true` (host-installed network drivers),
  `--set driver.rdma.enabled=true` (legacy `nvidia-peermem`),
  `--set driver.kernelModuleType=open` (open kernel module driver — needed on
  pre-R570 branches, where it is not the default),
  `--set gds.enabled=true` (**GPUDirect *Storage*, not RDMA** — corrected
  2026-09-19; an earlier draft of this document labelled this flag
  "DMA-BUF with Network-Operator-managed drivers", which the GPU Operator doc
  does not say).
- Pod spec: `securityContext.capabilities.add: ["IPC_LOCK"]`, an RDMA device
  resource (`rdma/rdma_shared_device_a: 1`) **and** the secondary-network
  annotation. NVIDIA's own Kimi-K3 and DeepSeek recipes add
  `[IPC_LOCK, SYS_PTRACE, SYS_RESOURCE]`.
- Secondary interfaces actually attached. Quoted from the GKE overlay:

```yaml
        annotations:
          networking.gke.io/default-interface: eth0
          networking.gke.io/interfaces: '[{"interfaceName":"eth0","network":"default"},{"interfaceName":"rdma0","network":"rdma-0"},{"interfaceName":"rdma1","network":"rdma-1"},{"interfaceName":"rdma2","network":"rdma-2"},{"interfaceName":"rdma3","network":"rdma-3"}]'
# ...
            requests:
              networking.gke.io.networks/rdma-0: "1"
              networking.gke.io.networks/rdma-1: "1"
              networking.gke.io.networks/rdma-2: "1"
              networking.gke.io.networks/rdma-3: "1"
              nvidia.com/gpu: "4"
```

- On bare metal the equivalent is Multus + the NVIDIA Network Operator's RDMA
  shared device plugin, or `hostNetwork: true` as the blunt instrument. **Verify by
  measurement, not by manifest**: run a NCCL all-reduce or the engine's own KV
  transfer benchmark and compare against line rate before declaring victory.

### 5.3 Model weights baked into the container image

**Failure mode:** a 1.56 TB Kimi-K3 checkpoint in an image means every scale-up is
a registry pull, every node needs the layer cached, and image GC becomes an
operational hazard. It also couples engine upgrades to weight redistribution.

**Evidence that the industry does not do this:** every NVIDIA recipe in §2.1 mounts
a **PVC** and runs offline:

```yaml
          env:
          - name: HF_HOME
            value: /shared-model-cache
          - name: HF_HUB_OFFLINE
            value: "1"
          - name: TRANSFORMERS_OFFLINE
            value: "1"
          volumeMounts:
          - name: shared-model-cache
            mountPath: /shared-model-cache
        volumes:
        - name: shared-model-cache
          persistentVolumeClaim:
            claimName: shared-model-cache
```

**Fix, in order of increasing sophistication:** (1) PVC or node-local NVMe with
`HF_HUB_OFFLINE=1`; (2) checkpoint-locality-aware scheduling — prefer nodes that
already hold the weights (ServerlessLLM's third contribution, §1.8); (3) GPU-to-GPU
weight streaming from a warm replica (Dynamo **ModelExpress**, *"7x faster
cold-start"*, `vendor`). NVIDIA's recipes also pass
`--load-format fastsafetensors --safetensors-load-strategy lazy`, which is free.

### 5.4 One giant TP16 across InfiniBand when 2 × TP8 replicas would do

**Evidence from this repo:** *"16/32 GPUs fit but cross InfiniBand for no gain; use
DP2xTP8 replicas instead of TP16"* (`pairs.json`, `deepseek41f/h100`), and for
Qwen3.8-27B: *"Run TP1 x8 replicas, not TP8"*, because *"TP only buys KV budget and
stops buying it past TP4 because the model has 4 KV heads (TP8 replicates them
×2)"* ([`../matrix/recommendations.md §2.1`](../matrix/recommendations.md),
`pairs.json`). `METHODOLOGY.md §4` prices multi-node TP at **20–40 % comm
overhead**.

**Failure mode:** you pay the all-reduce on every layer, over a fabric an order of
magnitude slower than NVLink, and you convert two independent failure domains into
one. A single NIC flap takes down the replica.

**And the silent version**, quoted from NVIDIA's own recipe (§2.1):

> *"A split gives no error: the deployment reports Ready and chat completions
> return HTTP 200 with content null."*

**Fix:** the rule is **maximize replicas, minimize TP, subject to fit**. Raise TP
only to make the model fit or to buy KV budget that is actually shardable — and
check whether it *is* shardable: `METHODOLOGY.md §3` warns that with
`num_key_value_heads = 1` (MLA without DCP/DP-attention) KV is **replicated per
rank**, so TP buys weight capacity and **no** KV capacity.

### 5.5 No KV-aware routing on agentic traffic

**Evidence:** Azure/Dynamo on Mooncake Tool Agent Traces — TTFT mean **~20.4×**,
P99 **~15.9×** better with KV routing than round-robin (§1.6). Baseten on
real 50k-token coding traffic at 89 % hit — TTFT **−50 %**, RPS **+61 %** (§1.6).
Character.AI at **95 %** inter-turn hit (§1.3). This repo's own agentic traces at
**90.2–97.0 %** ([`../matrix/recommendations.md §2.1`](../matrix/recommendations.md)).

**Failure mode:** round-robin across N replicas divides your effective cache by N
and re-prefills a 50k-token agent history on every turn. At 8 TP1 Qwen replicas per
node that is an 8× cache dilution *within a single node* — and the LMCache MP-mode
result (§2.7) shows the same dilution happening in host memory unless the CPU tier
is shared.

**Fix:** prefix-aware routing (Dynamo router with `--router-kv-events` + workers
publishing `--kv-events-config`; llm-d's precise prefix-cache routing; GAIE EPP;
Ray's `PrefixCacheAffinityRouter`) **plus** a shared host-DRAM KV tier so a cache
miss at the router is still a hit at L2. **Decision rule** (§1.6): measured prefix
reuse > ~40 % → mandatory; < ~10 % → use least-loaded instead. ⚠️ thresholds are
estimated, not sourced.

### 5.6 No drain on scale-down or rolling update

**Failure mode:** a replica is removed from the endpoint list and SIGTERM'd while
holding in-flight generations. For a 2k-token completion at 25 ms TPOT that is
50 seconds of work discarded, returned to the user as a truncated stream or a 5xx.
Kubernetes' default `terminationGracePeriodSeconds` is 30 s — shorter than a single
long generation.

**Evidence of the fix in production manifests:** every NVIDIA recipe in §2.1 sets
`terminationGracePeriodSeconds: 60` explicitly. Dynamo's fault-tolerance
capability is described as *"Canary health checks + in-flight request migration —
Workers fail; user requests don't"* ([README](https://github.com/ai-dynamo/dynamo)),
i.e. the stack's answer is *migrate*, not *wait*. ServerlessLLM's second
contribution is **live migration of LLM inference** *"ensuring minimal user
interruption"* (§1.8). Mooncake's answer at the other end of the pipe is the
**prediction-based early rejection** that stops admitting work that will not
finish (§1.2).

**Fix, layered:** (1) set `terminationGracePeriodSeconds` ≥ your p99 generation
time — for 2k-output traffic at 25 ms TPOT that is ≥ 60 s, and for 128k-context
batch work far more; (2) a `preStop` hook or engine flag that stops admitting new
requests while finishing in-flight ones; (3) readiness gating so the router removes
the endpoint *before* the drain begins; (4) request migration where the stack
supports it. ⚠️ **TO BE VERIFIED**: the exact vLLM/SGLang flag or endpoint that
disables admission without killing in-flight requests was not confirmed from
primary docs for this document — verify against the engine version you pin
([`../cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md)).

### 5.7 Two further anti-patterns worth naming

- **Hand-setting backend flags the engine resolves itself.** Quoted from NVIDIA's
  DeepSeek recipe: *"Do not set the attention, MoE, or GEMM backend flags. SGLang
  resolves them for this model. A hand-set backend flag selects a slower
  fallback."* This repo has the matching landmine list for Qwen3.8-27B: **not**
  `--attention-backend flashinfer` (illegal for hybrid-GDN on SM100/103) and
  **not** `trtllm_mha` (hangs on SM103)
  ([`../matrix/recommendations.md §2.1`](../matrix/recommendations.md)).
- **Running a tag instead of a digest.** This repo's own risk register:
  *"Nothing is in a numbered release … the measured GB300 runs used three different
  images in six days"* ([`../matrix/recommendations.md §4`](../matrix/recommendations.md)).
  NVIDIA's recipes ship `1.5.0-kimi-k3-dev.1` and `1.6.0-deepseek-v4.1-flash-dev.1`
  — model-specific dev tags ahead of the `1.4.2` release. **Pin digests.**

---

## 6. Decision matrix: workload × model class → stack

Columns: recommended stack, routing policy, PD, scaling policy. Model classes map
to this repo as: *small dense* = Qwen3.8-27B, Marlin-2B; *large MoE* =
DeepSeek-V4.1-Flash (fits ≤ 4 GPUs) and Kimi-K3 (one node); *1M-context* = the
long-context operating point of either MoE.

| Workload | Model class | Stack | Routing | PD | Scaling policy |
|---|---|---|---|---|---|
| **Chat** (short prompts, high concurrency, TPOT-bound) | small dense | llm-d optimized baseline, or vLLM + GAIE | prefix-aware; session-sticky | no | KEDA on EPP queue depth; scale-to-zero off (keep 1 warm) |
| | large MoE (fits ≤ 4 GPU) | Dynamo aggregated, KV router | prefix-aware + load | no — keep DSpark | KEDA queue depth; predictive floor from diurnal forecast |
| | large MoE (whole node) | Dynamo aggregated, LWS/Grove gang | KV-aware, session-sticky | no | **step function**: whole nodes, predictive only; never reactive |
| **Agents / tools** (50k+ prompts, 90 %+ prefix reuse, bursty) | small dense | llm-d precise prefix-cache routing + tiered cache | **KV-aware, mandatory** | no | token-aware (llm-d) — request counts lie when prompts vary 100× |
| | large MoE | Dynamo KV router + KVBM (CPU+NVMe) | **KV-aware, mandatory**; route by session id (§1.5) | no (DSpark conflict on SGLang) | SLA Planner on `ttft_ms`; warm pool sized to burst |
| | 1M-context | Dynamo, + chunked pipeline parallel prefill (§1.2) if TTFT-bound | KV-aware + longest-prefix | **yes, if** the KV/token gate in §4.3 passes | SLO-aware (predicted TTFT ÷ SLO) |
| **RAG** (medium prompts, low reuse across users, high reuse of the corpus prefix) | small dense | vLLM + GAIE | prefix-aware on the **shared system/corpus prefix**; least-loaded otherwise | no | saturation-based |
| | large MoE | Dynamo or llm-d | hybrid: prefix for the corpus block, load for the tail | no | queue depth |
| **Batch / offline** (no latency SLO, maximize $/token) | any | llm-d **Batch Gateway** (experimental) or a plain job queue | none — pack for throughput | no | fill the trough: run at night on nodes returned from interactive (§1.1); Kueue cohort with lowest priority |
| **Video / multimodal** (encode-bound, Marlin-2B) | small dense VLM | Dynamo **multimodal E/P/D** with embedding cache, or plain vLLM | by media hash, to reuse the embedding cache | **encode/prefill/decode** split is the relevant one, not P/D | queue depth on the encode stage |

**Cross-cutting rules that the matrix assumes:**

1. **Never one config for five models.** One gateway, one `InferencePool` per
   model pool, per-pool routing and scaling policy.
2. **Batch and interactive share hardware through a quota system, not a prayer.**
   Kueue `ClusterQueue`s in one cohort with per-model GPU floors and preemption
   (llm-d's experimental rebalancing, §2.2) is the published mechanism.
3. **Scale-to-zero is for small models only.** Qwen3.8-27B (30.87 GB FP8) and
   Marlin-2B (5.444 GB) can afford it; Kimi-K3 (1,560.9 GB) cannot, at any
   cold-start technology available on 2026-09-19 (§1.8).
4. **The routing decision is a function of measured prefix reuse, not of model
   size** (§1.6, §5.5).

---

## 7. Three blueprints on 8×B300 nodes

All three use the same building blocks and differ only in pool sizes. Every
throughput and price cell below is **`est.` — computed here by multiplying a
per-GPU figure from [`../matrix/pairs.json`](../matrix/pairs.json) by a GPU
count.** They are *capacity envelopes at each model's stated operating point*, not
measurements of a cluster, and they inherit every caveat in
[`../matrix/recommendations.md §2.4`](../matrix/recommendations.md) — above all the
**DSpark acceptance rate**, which alone swings `$/1M output` by up to **3–3.5×**.

**Per-GPU inputs used**, all from `pairs.json` B300 rows (see
[`../matrix/cost-matrix.md`](../matrix/cost-matrix.md) for the full grids):

| Model | Shape | S1 out tok/s/GPU | S1 $/1M out (low–high) | S4 out tok/s/GPU | Blended $/1M (low–high) |
|---|---|---:|---:|---:|---:|
| DeepSeek-V4.1-Flash | TP4 (S1) / TP2 (S4) | 1,373 | $1.4973–$3.0352 | 2,656 | $0.4809–$0.9747 |
| Qwen3.8-27B | TP1 × 8/node | 12,463 | $0.1649–$0.3343 | 13,461 | $0.0602–$0.1221 |
| Kimi-K3 | TP8 + DCP8, 1 node | 278.06 (decode-only; 198.9 sustained — only the sustained figure is corroborated by a published B300 measurement, [`../models/kimik3/b300.md §3.8`](../models/kimik3/b300.md)) | $7.3926–$14.985 | 569.4 | $2.3811–$4.8265 |
| Marlin-2B | TP1 | 93,271 | $0.022–$0.0447 | 93,271 | $0.0092–$0.0185 |

GPU-hour prices: `low` **$7.40** (Hyperstack), `high` **$15.00** (OCI), from
[`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) — used
here so the blueprints are comparable with the rest of the tree even though owned
metal has a different (amortized) cost basis.

### 7.1 Blueprint A — 4 nodes (32 B300)

The smallest cluster that runs all five models simultaneously.

| Pool | Nodes | GPUs | Shape | Replicas | Aggregate S1 out tok/s (`est.`) |
|---|---:|---:|---|---:|---:|
| Kimi-K3 | 1 | 8 | TP8 + DCP8, `--kv-cache-dtype fp8`, `--prefix-match-unit 128`, DSpark | 1 | **2,224** (1,591 sustained) |
| DeepSeek-V4.1-Flash | 2 | 16 | TP4, DSpark γ=5, prefix caching on | 4 | **21,968** |
| Qwen3.8-27B | 0.75 | 6 | TP1, `--kv-cache-dtype fp8`, MTP γ=3 | 6 | **74,778** |
| Marlin-2B | 0.25 | 2 | TP1 | 2 | 186,542 (video) |
| **Text total** | **4** | **32** | | | **≈ 99.0k out tok/s** |

Cluster cost at list rates: **$236.80/h low, $480.00/h high**.

**Control plane.** One Dynamo Platform install; four `DynamoGraphDeployment`s, one
per model; `DYN_ROUTER_MODE=kv` on every frontend; workers publish
`--kv-events-config`. No Kubernetes gateway needed at this size — the Dynamo-native
path (`client → Frontend → Router → workers`) is enough (§2.1).

**KV tiers.** L1 = HBM (per replica). L2 = host DRAM via LMCache in **MP mode**
(one unified cache per node, `--l1-size-gb` sized to ~60 % of node DRAM) — this is
the single most valuable addition on the Qwen node, where 6 TP1 replicas would
otherwise keep 6 private caches (§2.7). L3 = node-local NVMe. **No cross-node KV
tier at 4 nodes** — the coordination cost is not repaid.

**Scaling policy.** Static. Four nodes is below the size where autoscaling repays
its complexity: pin pools, run KEDA in observe-only mode to collect the queue-depth
and KV-occupancy traces you will need for Blueprint B, and do capacity changes by
hand.

**Failure posture.** Kimi-K3 has **no redundancy** — one node, one replica, and a
node loss is a full Kimi-K3 outage. If Kimi-K3 has an availability SLA, Blueprint A
does not meet it; that is the honest reason to go to 16 nodes.

### 7.2 Blueprint B — 16 nodes (128 B300)

| Pool | Nodes | GPUs | Shape | Replicas | Aggregate S1 out tok/s (`est.`) |
|---|---:|---:|---|---:|---:|
| Kimi-K3 | 2 | 16 | TP8 + DCP8 | 2 | **4,449** |
| DeepSeek-V4.1-Flash | 10 | 80 | TP4 aggregated (20 replicas); **or** PD pools if §4.3's gate passes | 20 | **109,840** |
| Qwen3.8-27B | 2 | 16 | TP1 | 16 | **199,408** |
| Marlin-2B | 1 | 8 | TP1 | 8 | 746,168 (video) |
| Spare / canary / burst | 1 | 8 | — | — | — |
| **Text total** | **16** | **128** (120 serving) | | | **≈ 314k out tok/s** |

Cluster cost at list rates: **$947.20/h low, $1,920.00/h high** (all 16 nodes).
S4 (max-throughput) aggregate for the same GPU allocation: **≈ 437k out tok/s**
(`est.`, using the S4 per-GPU column).

**Control plane.** Kubernetes with a Gateway API front door: one gateway, one
`InferencePool` per model pool, EPP per pool with `failureMode: FailOpen` (§2.5).
Either llm-d's EPP or Dynamo's Endpoint Picker Plugin behind GAIE. **LeaderWorkerSet**
for the two Kimi-K3 node-groups, with Kueue or Volcano supplying gang scheduling
(LWS's own is Alpha, §2.6). Prometheus + DCGM; the observability loop llm-d
specifies — demand (`vllm:num_requests_waiting`, `vllm:kv_cache_usage_perc`),
decision (desired replicas), convergence (ready endpoints), outcome
(`vllm:time_to_first_token_seconds`) — is the minimum, because scaling failures
show up at a *handoff*, not in one metric (§5.1).

**KV tiers.** L1 HBM; L2 per-node unified host DRAM (LMCache MP / KVBM); **L3
cluster-wide NVMe with a global index** — at 20 DeepSeek replicas the global cache
is where Mooncake's *"2.36× higher hit rate than local cache"* and *"up to 48 %
savings in prefill computation time"* (§1.2) start to apply. Replicate hot blocks
rather than tuning the fabric (§1.2).

**Scaling policy.** KEDA, **token-aware** for the DeepSeek and Qwen pools
(prompt sizes vary 100× on agentic traffic, §2.2), queue-depth for Marlin. Kimi-K3
is a **predictive step function only**: scale it on a forecast, in whole nodes,
with a lead time equal to the weight-load time, never reactively. The 1 spare node
is the Kimi-K3 warm standby and the canary target for engine upgrades.

**Failure posture.** Every pool has ≥ 2 replicas. Node loss degrades but does not
outage. Rolling upgrades go canary → spare node → one pool at a time, with drain
per §5.6.

### 7.3 Blueprint C — 64 nodes (512 B300)

| Pool | Nodes | GPUs | Shape | Replicas | Aggregate S1 out tok/s (`est.`) |
|---|---:|---:|---|---:|---:|
| Kimi-K3 | 8 | 64 | TP8 + DCP8; evaluate the Dynamo `disagg` shape (§2.1) on 2+2 nodes | 8 | **17,796** |
| DeepSeek-V4.1-Flash | 40 | 320 | TP4 interactive pool + TP2 batch pool | 80 (TP4-equivalent) | **439,360** |
| Qwen3.8-27B | 8 | 64 | TP1 | 64 | **797,632** |
| Marlin-2B | 4 | 32 | TP1 | 32 | 2,984,672 (video) |
| Spare / canary / overflow-staging | 4 | 32 | — | — | — |
| **Text total** | **64** | **512** (480 serving) | | | **≈ 1.25M out tok/s** |

Cluster cost at list rates: **$3,788.80/h low, $7,680.00/h high**.
S4 aggregate for the same allocation: **≈ 1.75M out tok/s** (`est.`).

**Control plane.** Same as B, plus: **multi-tenant flow control** (llm-d's flow
control path) because at this size one tenant's burst is another's outage;
**Kueue cohorts with per-model GPU floors** so the pools share the budget instead
of fighting (§2.2); and **Dynamo's SLA Planner or llm-d's SLO-aware KEDA path**
rather than raw queue depth, because at 80 DeepSeek replicas the objective is
"meet the SLO at minimum GPUs", not "keep the queue short". Planner requires a
pre-deployment sweep — budget the `thorough` mode's **2–4 h** per model/hardware
combination (§2.1).

**KV tiers.** L1/L2 as B; **L3 becomes a first-class service**: a dedicated
cluster-wide KV store (Mooncake Store or KVBM with remote/S3 backing) with hot-block
replication. At DeepSeek's published ratio — 56.3 % of input tokens served from an
on-disk KV cache (§1.1) — and this repo's measured 90.2–97.0 % agentic hit rate,
the L3 tier is the largest single cost lever in Blueprint C, ahead of any
kernel-level optimization.

**Scaling policy.** Predictive (forecast-driven floor) + reactive (load above the
floor), which is exactly Dynamo Planner's two-mode composition with the global GPU
budget clamp (§2.1). Batch traffic fills the diurnal trough at lowest Kueue
priority, mirroring DeepSeek's night-time node return (§1.1). Cloud overflow
(§4.5) is configured for Qwen3.8-27B and Marlin-2B only, priced at roughly 2×, and
never for Kimi-K3.

**Blueprint C's unresolved question** is whether the 40-node DeepSeek pool should
be PD-disaggregated. The case *for*: at this size the prefill/decode ratio is
measurable and phase-independent scaling reclaims real stranded capacity
(Fireworks' *"without stranding GPUs"*, §1.7). The case *against*: SGLang refuses
DSpark under PD for this model (§2.1), and DSpark is worth **$1.50 → $4.69 per 1M
output** in this tree. **Decision rule: run the vLLM `NixlConnector` PD path
against the aggregated+DSpark path on 8 nodes, with production traffic, and let
$/1M output decide.** That is benchmark **B1**-adjacent in
[`../matrix/recommendations.md §3`](../matrix/recommendations.md) and should be run
before Blueprint C is built, not after.

### 7.4 What all three blueprints share

| Decision | Choice | Why |
|---|---|---|
| Weights | node-local NVMe + PVC, `HF_HUB_OFFLINE=1`, `--load-format fastsafetensors` | §5.3 |
| Images | **pinned digests**, not tags | §5.7, [`../matrix/recommendations.md §4`](../matrix/recommendations.md) |
| Routing | KV/prefix-aware everywhere prefix reuse > ~40 % | §1.6, §5.5 |
| KV dtype | FP8 where the engine supports it (mandatory on Kimi-K3) | [`../matrix/recommendations.md §2.1`](../matrix/recommendations.md) |
| Speculative decoding | on (DSpark / MTP) unless a topology forbids it | §2.1; the largest single $/token lever in this tree |
| TP | minimum that fits; replicas over parallelism | §5.4 |
| Multi-node model path | avoided entirely except Kimi-K3 PD (if benchmarked) | §4.2, `METHODOLOGY.md §4` |
| Drain | `terminationGracePeriodSeconds` ≥ p99 generation time | §5.6 |
| Autoscaling signal | engine queue/tokens/SLO — never CPU or GPU utilization | §5.1 |

---

## Open questions

All ⚠️ items from this document, consolidated. Each names the estimation method or
the reason it is unresolved.

1. **Dynamo's "750× higher throughput" (DeepSeek-R1, GB300 NVL72, InferenceXv2).**
   The README row states no baseline. A ratio without a baseline is not a number.
   *Resolution:* fetch the InferenceXv2 run via
   [`../cross-cutting/inferencex-api.md`](../cross-cutting/inferencex-api.md) and
   read the comparison pair, or drop the claim.
2. **InferenceX B300-vs-GB300 rows lack a framework and a run date.** The three
   interactivity points in §1.4 (including the decision-relevant inversion at
   178 tok/s/user, where B300 beats the NVL72) are quoted from a compare page that
   does not state which of Dynamo-SGLang / Dynamo-TRTLLM / SGLang produced them.
   *Resolution:* re-fetch via the API and record `framework` and `run_date`.
3. **Does vLLM's PD path preserve DSpark for DeepSeek-V4.1-Flash?** NVIDIA's SGLang
   recipe states SGLang *"refuses it under prefill and decode disaggregation for
   this model"*, while the Kimi-K3 vLLM disagg recipe carries a speculative config.
   This decides whether §7.3's 40-node DeepSeek pool can be disaggregated at all.
   *Resolution:* run both paths on 8 nodes with production traffic (§7.3).
4. **`--enable-expert-parallel` together with DCP.** NVIDIA's Kimi-K3 vLLM GB300
   recipe enables both; this tree warns that `DCP8 + EP8` is the combination in
   [sglang#34260](https://github.com/sgl-project/sglang/issues/34260) and
   recommends DCP alone on B300. Different engine, different NVLink domain — both
   may be correct. *Resolution:* A/B on the actual node before enabling EP.
5. **Per-request KV-transfer times in §1.2 and §4.3 are `est.`**, computed as
   `ctx × kv_bytes_per_token ÷ line_rate` from `METHODOLOGY.md §8`'s pinned
   KV/token values (890 B for DeepSeek-V4.1-Flash on sm_103, 13.5 KiB for
   Kimi-K3) at a nominal 400 Gbps. They ignore protocol overhead, staging buffers
   and the achieved-vs-line-rate gap Mooncake measured (87 GB/s on 4×200 Gbps =
   ~87 % of line rate). *Resolution:* measure the engine's own KV transfer
   benchmark on the node.
6. **The prefix-reuse thresholds for KV-aware routing (~40 % mandatory / ~10 %
   harmful)** are interpolated between the Baseten point (89 % hit → 2× TTFT) and
   the Azure point (tool-agent traces → 20× TTFT). No source states a break-even.
   *Resolution:* replay production traffic against both policies.
7. **DeepSeek's true prefill:decode *pool* ratio is not recoverable** from the
   day-6 post. The post gives deployment *unit* sizes (4-node prefill, 18-node
   decode) but not how many of each ran. §4.3's 1:3.1 is `est.` from their token
   counts and per-node rates, not a disclosed ratio.
8. **Envoy AI Gateway is uncharacterized.** `aigateway.envoyproxy.io`
   301-redirected to `theagentrouter.ai` on 2026-09-19 and was not fetched. Its
   token-based rate limiting is cited only as KServe's claim about it.
9. **Together AI and Databricks are absent.** No primary engineering post from
   either was fetched (the session's web-search budget was exhausted). No claims
   are made about their architectures; this is a coverage gap, not a finding.
10. **Perplexity's published numbers are unverified.** `research.perplexity.ai` and
    `perplexity.ai/hub` returned an anti-bot interstitial to WebFetch and `curl`.
    Only facts present in the open-source `pplx-garden` README are used.
11. **No primary Anthropic or OpenAI post fetched here discloses inference
    topology** (parallelism, PD ratios, per-GPU throughput). §1.5 uses only the
    Managed Agents post's platform architecture and its stated TTFT improvements.
12. **CUDA checkpoint/restore at 1.5 TB / TP8 is unmeasured.** Modal's numbers are
    for models orders of magnitude smaller, on an alpha driver API (570/575
    branches). Do not plan Kimi-K3 cold-start around snapshots.
13. **Graceful-drain mechanics are engine-version-specific.** The exact vLLM /
    SGLang flag or endpoint that stops admitting new requests while finishing
    in-flight ones was not confirmed from primary docs for this document.
    *Resolution:* verify against the pinned engine version
    ([`../cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md)).
14. **Multi-region failover cost is unmeasured.** No primary source fetched here
    measures LLM cross-region failover latency or cache warm-up cost; §4.6's
    capacity arithmetic is standard HA sizing.
15. **Every aggregate in §7 is `est.`** — a per-GPU `pairs.json` figure multiplied
    by a GPU count, with no allowance for router overhead, cross-replica load
    imbalance, cache-tier contention, or the diurnal duty cycle. Treat them as
    capacity envelopes. They also inherit `pairs.json`'s own open items, above all
    the **DSpark/MTP acceptance rate** (up to **3–3.5×** swing in `$/1M output`,
    [`../matrix/recommendations.md §2.4`](../matrix/recommendations.md)) and the
    Kimi-K3 decode-only vs sustained basis difference (278.06 vs 198.9 tok/s/GPU).
16. **The `low`/`high` GPU-hour prices used in §7 are rental rates**, not the
    amortized cost of owned metal. They are used so the blueprints line up with the
    rest of the tree; substitute your own capex/opex basis before using any §7
    dollar figure in a business case.

---

## Sources

Every URL below was fetched (WebFetch or `curl`) on **2026-09-19** for this
document, except where noted as blocked.

**Production architectures**

- DeepSeek, *DeepSeek-V3/R1 Inference System Overview* (open-source week, day 6) — https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md
- Qin et al., *Mooncake: Trading More Storage for Less Computation — A KVCache-centric Architecture for Serving LLM Chatbot*, USENIX FAST '25 — https://www.usenix.org/system/files/fast25-qin.pdf · https://arxiv.org/abs/2407.00079
- Character.AI, *Optimizing AI Inference at Character.AI* — https://blog.character.ai/optimizing-ai-inference-at-character-ai-2/
- NVIDIA, *Scaling Large MoE Models with Wide Expert Parallelism on NVL72 Rack Scale Systems* (2025-10-20) — https://developer.nvidia.com/blog/scaling-large-moe-models-with-wide-expert-parallelism-on-nvl72-rack-scale-systems/
- LMSYS, *Unlocking 25x Inference Performance with SGLang on NVIDIA GB300 NVL72* (2026-02-20) — https://www.lmsys.org/blog/2026-02-20-gb300-inferencex/
- SemiAnalysis InferenceX, *DeepSeek R1 — B300 vs GB300 NVL72* — https://inferencex.semianalysis.com/compare/deepseek-r1-b300-vs-gb300
- Anthropic, *Scaling Managed Agents: Decoupling the brain from the hands* (2026-04-08) — https://www.anthropic.com/engineering/managed-agents
- Baseten, *2x faster inference with KV cache-aware routing* (2026-03-16) — https://www.baseten.co/blog/how-baseten-achieved-2x-faster-inference-with-nvidia-dynamo/
- Azure AKS Engineering, *Scaling multi-node LLM inference with NVIDIA Dynamo and NVIDIA GPUs on AKS (Part 3)* (2026-03-16) — https://blog.aks.azure.com/2026/03/16/dynamo-on-aks-part-3
- Perplexity, `pplx-garden` (fabric-lib RDMA TransferEngine + P2P MoE kernel) — https://github.com/perplexityai/pplx-garden · MLSys'26 paper https://arxiv.org/abs/2510.27656 · *(perplexity.ai blog pages were anti-bot blocked, not fetched)*
- Fireworks AI, *Inference* — https://fireworks.ai/inference
- Cloudflare, *Workers AI: serverless GPU-powered inference on Cloudflare's global network* (2023-09-27) — https://blog.cloudflare.com/workers-ai/
- Modal, *GPU Memory Snapshots: Supercharging sub-second startup* (2025-07-30) — https://modal.com/blog/gpu-mem-snapshots
- Fu et al., *ServerlessLLM: Low-Latency Serverless Inference for Large Language Models*, USENIX OSDI '24 — https://www.usenix.org/system/files/osdi24-fu.pdf
- Anyscale, *Ray Serve LLM on Anyscale: Wide-EP and Disaggregated Serving with vLLM* (2025-11-26) — https://www.anyscale.com/blog/ray-serve-llm-anyscale-apis-wide-ep-disaggregated-serving-vllm
- vLLM, *Large Scale Serving: DeepSeek @ 2.2k tok/s/H200 with Wide-EP* (2025-12-17) — https://vllm.ai/blog/2025-12-17-large-scale-serving

**Open-source stacks**

- NVIDIA Dynamo — https://github.com/ai-dynamo/dynamo · Planner guide https://docs.nvidia.com/dynamo/components/planner/planner-guide · KV offloading / KVBM https://docs.nvidia.com/dynamo/components/kvbm
- Dynamo recipe, Kimi-K3 aggregated GB300 — https://github.com/ai-dynamo/dynamo/blob/main/recipes/kimi-k3/vllm/agg-gb300-agentic/deploy.yaml
- Dynamo recipe, Kimi-K3 disaggregated GB300 — https://github.com/ai-dynamo/dynamo/blob/main/recipes/kimi-k3/vllm/disagg-gb300-agentic/deploy.yaml
- Dynamo recipe, DeepSeek-V4.1-Flash SGLang disaggregated GB200 — https://github.com/ai-dynamo/dynamo/blob/main/recipes/deepseek-v4.1-flash/sglang/disagg-gb200/deploy-generic.yaml · RDMA overlay https://github.com/ai-dynamo/dynamo/blob/main/recipes/deepseek-v4.1-flash/sglang/disagg-gb200/deploy-gke-rdma.yaml
- llm-d — https://github.com/llm-d/llm-d · well-lit paths (v0.7) https://llm-d.ai/docs/0.7/well-lit-paths · wide-EP https://llm-d.ai/docs/well-lit-paths/foundations/wide-expert-parallelism · workload autoscaling https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/README.md · v0.5 release (2026-02-04) https://llm-d.ai/blog/llm-d-v0.5-sustaining-performance-at-scale
- AIBrix — https://github.com/vllm-project/aibrix · white paper https://arxiv.org/abs/2504.03648
- KServe v0.17 release (2026-03-13) — https://kserve.github.io/website/blog/kserve-0.17-release · LLMInferenceService configuration https://kserve.github.io/website/docs/model-serving/generative-inference/llmisvc/llmisvc-configuration
- SGLang, *PD Disaggregation* — https://docs.sglang.io/advanced_features/pd_disaggregation.html
- Ray Serve LLM — https://docs.ray.io/en/latest/serve/llm/index.html
- Gateway API Inference Extension, `InferencePool` — https://gateway-api-inference-extension.sigs.k8s.io/api-types/inferencepool/
- LeaderWorkerSet — https://github.com/kubernetes-sigs/lws · `DisaggregatedSet` example https://github.com/kubernetes-sigs/lws/blob/main/docs/examples/disaggregatedset/basic/vllm.yaml · gang-scheduling issue https://github.com/kubernetes-sigs/lws/issues/167
- LMCache, *LMCache's New Architecture Boosts MoE Inference Performance by 10×* (2026-04-03) — https://blog.lmcache.ai/en/2026/04/03/lmcaches-new-architecture-boosts-moe-inference-performance-by-10x/
- vLLM production-stack, *Offload KV Cache to CPU with LMCache* — https://github.com/vllm-project/production-stack/blob/main/tutorials/05-offload-kv-cache.md

**Slurm and infrastructure**

- NVIDIA Pyxis — https://github.com/NVIDIA/pyxis · Enroot https://github.com/NVIDIA/enroot
- *Automated Dynamic AI Inference Scaling on HPC-Infrastructure: Integrating Kubernetes, Slurm and vLLM* — https://arxiv.org/html/2511.21413v1
- NVIDIA GPU Operator, *GPUDirect RDMA and GPUDirect Storage* — https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-operator-rdma.html

**Internal (this repo)**

- [`../METHODOLOGY.md`](../METHODOLOGY.md) — formulas, legend, §8 pinned GPU/model inputs
- [`../README.md`](../README.md) — tree index
- [`../matrix/pairs.json`](../matrix/pairs.json), [`../matrix/cost-matrix.md`](../matrix/cost-matrix.md), [`../matrix/fit-matrix.md`](../matrix/fit-matrix.md), [`../matrix/recommendations.md`](../matrix/recommendations.md), [`../matrix/gpu-optimizations.md`](../matrix/gpu-optimizations.md)
- [`../gpus/b300.md`](../gpus/b300.md), [`../gpus/gb300.md`](../gpus/gb300.md)
- [`../models/kimik3/b300.md`](../models/kimik3/b300.md), [`../models/deepseek41f/b300.md`](../models/deepseek41f/b300.md), [`../models/qwen3827b/b300.md`](../models/qwen3827b/b300.md), [`../models/marlin2b/b300.md`](../models/marlin2b/b300.md)
- [`../cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md), [`../cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md), [`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md), [`../cross-cutting/inferencex-api.md`](../cross-cutting/inferencex-api.md), [`../cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md)

---

## Verification log (2026-09-19)

Adversarial fact-check. Every source below was opened directly (WebFetch / `curl`
/ `pdftotext`); the document's own citation text was never taken on trust, and
every `research/` cross-reference was opened and the number located in the target
file. All derivations were recomputed with `python3`. **43 claim groups checked
(well past the 25 the brief asked for, because several of the most consequential
items — the DeepSeek cost sheet, the Mooncake measurements, the two Dynamo
recipes, the §7 blueprint arithmetic and the repo cross-references — decompose
into many independently checkable numbers): 34 CONFIRMED, 8 CORRECTED,
1 UNVERIFIABLE.**

### CONFIRMED

| # | Claim (§) | Verdict | Source opened |
|---|---|---|---|
| 1 | §1.1 DeepSeek node occupancy 278 peak / 226.75 avg; "$2 per hour" H800; **$87,072** daily. Recomputed: `226.75 × 8 × 2 × 24 = 87,072.0` exactly | CONFIRMED | https://raw.githubusercontent.com/deepseek-ai/open-infra-index/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md |
| 2 | §1.1 608 B input / **342 B (56.3 %)** on-disk KV hit / 168 B output / 20–22 tok/s / avg KV length 4,989 | CONFIRMED | same |
| 3 | §1.1 per-node **~73.7k tok/s input (incl. cache hits)** / **~14.8k tok/s output**; prefill unit 4 nodes, decode unit 18 nodes, 32 redundant routed experts, 9+1 and 2+1 experts per GPU; dual-batch overlap (prefill) and **5-stage pipeline** (decode) | CONFIRMED | same |
| 4 | §1.1 revenue **$562,027**, margin **545 %**, R1 pricing $0.14 / $0.55 / $2.19 per 1M. Recomputed: `(562,027 − 87,072)/87,072 = 545.47 %` | CONFIRMED | same |
| 5 | §1.2 Mooncake **"115 % and 107 % more requests"** (A800/H800 vs prior vLLM-based systems) and **"up to a 498 % increase in the effective request capacity"**. *(Note: the arXiv v1 abstract says 75 % / 525 %; the FAST '25 camera-ready — which this document cites — says 115 % / 107 % / 498 %. The document cites the right version.)* | CONFIRMED | https://www.usenix.org/system/files/fast25-qin.pdf |
| 6 | §1.2 cache hit rate **"up to 2.36× higher than that of the local cache"**, **"up to 48 % savings"** in prefill compute, the separate **136 %** maximum increase, the **75 % max** theoretical ceiling and **"only up to 50 % of the theoretical cache hit rate"** | CONFIRMED | same |
| 7 | §1.2 transfer engine **87 GB/s @ 4×200 Gbps**, **190 GB/s @ 8×400 Gbps**, **"approximately 2.4× and 4.6× faster"** than TCP, 40 GB / LLaMA3-70B @128k; NICs **100/200 Gbps per A800**, **200/400 Gbps per H800**, RoCEv2; **B floor 6 GB/s (8×A800) / 19 GB/s (8×H800)**; rig **16 nodes × 8×A800-SXM4-80GB**; HTTP 429 early rejection; CPP chosen over SP | CONFIRMED | same |
| 8 | §1.3 Character.AI **20,000 qps**, MQA **8×** vs GQA, **1024** attention horizon, **"only 1 out of every 6 layers uses global attention"**, cross-layer sharing **2–3×**, **95 % cache rate** / **180 messages**, natively trained int8, **33×** cost reduction, **13.5X** vs commercial APIs | CONFIRMED | https://blog.character.ai/optimizing-ai-inference-at-character-ai-2/ |
| 9 | §1.4 wide-EP **"EP rank 32 delivers up to 1.8x higher output token throughput per GPU compared to small EP rank 8 at 100 tokens/sec per user"**, DeepSeek-R1 / 256 experts, **130 TB/s** NVLink, EPLB static **and** online, TensorRT-LLM | CONFIRMED | https://developer.nvidia.com/blog/scaling-large-moe-models-with-wide-expert-parallelism-on-nvl72-rack-scale-systems/ |
| 10 | §1.4 InferenceX B300-vs-GB300 rows **3,747.8 / 6,968.6** @73, **1,015.7 / 3,762.8** @126, **718.9 / 544.7** @178 — including the **inversion at 178 tok/s/user**. The page states **no framework and no run date**, so the document's ⚠️ on that row is justified and stays | CONFIRMED | https://inferencex.semianalysis.com/compare/deepseek-r1-b300-vs-gb300 |
| 11 | §1.4 / §2.1 Dynamo README "Key Results": **7×** throughput/GPU, **7×** startup, **2×** TTFT, **80 %** fewer SLA breaches at **5 %** lower TCO, **750×** (GB300 NVL72, InferenceXv2) — and the 750× row indeed **states no baseline**, so the document's ⚠️ is correct. Capability table (KVBM, ModelExpress, Planner, Grove, AISimulate, fault tolerance) and container tag **1.4.2** all verbatim | CONFIRMED | https://github.com/ai-dynamo/dynamo |
| 12 | §2.1 Kimi-K3 `agg-gb300-agentic/deploy.yaml`: `nodeCount: 4`, image `1.5.0-kimi-k3-dev.1`, `--tensor-parallel-size 16`, `--decode-context-parallel-size 16`, `--dcp-comm-backend a2a`, `--enable-expert-parallel`, `--moe-backend deep_gemm_mega_moe`, `--all2all-backend flashinfer_nvlink_two_sided`, `--load-format fastsafetensors`, `--gpu-memory-utilization 0.85`, `--max-num-seqs 32`, `--enable-prefix-caching`, `--prefix-match-unit 128`, `--kv-cache-dtype fp8`, `--attention-backend TOKENSPEED_MLA`, ZMQ `--kv-events-config` on `tcp://*:20081`, DSpark `num_speculative_tokens: 7`, `terminationGracePeriodSeconds: 60` — **every flag verbatim** | CONFIRMED | https://raw.githubusercontent.com/ai-dynamo/dynamo/main/recipes/kimi-k3/vllm/agg-gb300-agentic/deploy.yaml |
| 13 | §2.1 item 5: the recipe's commented **"For perf benchmarking only"** variant does carry `"rejection_sample_method":"synthetic","synthetic_acceptance_length":4.2584`. The document's warning that 4.2584 is a synthetic harness value, not a measurement, is exactly right | CONFIRMED | same file (lines ~185–188) |
| 14 | §2.1 DeepSeek `disagg-gb200/deploy-generic.yaml`: all three header warnings verbatim (DSpark refused under PD; *"KV does NOT ride the NVLink fabric … HTTP 200 with content null"*; do not hand-set attention/MoE/GEMM backends) and every YAML field (`tp-size: 4`, `ep-size: 4`, `page-size: 256`, `mem-fraction-static: 0.8`, `max-running-requests: 256`, `max-prefill-tokens: 16384`, `disaggregation-transfer-backend: mooncake`, bootstrap port 30001, `load-balance-method: round_robin`) | CONFIRMED | https://raw.githubusercontent.com/ai-dynamo/dynamo/main/recipes/deepseek-v4.1-flash/sglang/disagg-gb200/deploy-generic.yaml |
| 15 | §1.6 Baseten: Qwen3-Coder-480B (262K ctx), ~50k in / ~1k out, **4 replicas**, **89 %** hit, TTFT mean **−50 %**, P95 **−48 %**, P99 **−49 %**, TPOT **−34 %**, RPS **+61 %**, output tok/s **+62 %**; radix-tree hashing + overlap score | CONFIRMED | https://www.baseten.co/blog/how-baseten-achieved-2x-faster-inference-with-nvidia-dynamo/ |
| 16 | §1.6 / §5.5 Azure AKS: **8×H100 across 4 × `Standard_NC80adis_H100_v5`**, Qwen3-32B, **~23,000** Mooncake Tool Agent Traces requests; TTFT mean **20.4×** (2,658 vs 53,877 ms), TTFT P99 **15.9×** (17,585 vs 280,221 ms), E2E mean **4.3×**, E2E P99 **3.8×** | CONFIRMED | https://blog.aks.azure.com/2026/03/16/dynamo-on-aks-part-3 |
| 17 | §2.2 llm-d **v0.7 (2026-05)**, **CNCF Sandbox since 2026-03**, founders Red Hat / Google Cloud / IBM Research / CoreWeave / NVIDIA; validated-performance rows **3× throughput + 2× TTFT** (Llama 3.1 70B, 4× MI300X), **40 %** TTFT+ITL (predicted latency), **up to 70 %** PD on GPT-OSS/B200, **10–30 %** disagg on MI300X — all four verbatim | CONFIRMED | https://github.com/llm-d/llm-d |
| 18 | §2.2 / §2.7 / §4.4 / §4.6 llm-d v0.5 blog: **16× prefill + 16× decode B200**, **~50k output tok/s total / ~3.1k per decode GPU**; **13.9×** at **250 concurrent users**, 4× H100, Llama-3.1-70B, **~185k tok/s** sustained; UCCL **2.4×** resilience (**7.1 % vs 17.1 %**); active-active HA with dynamic discovery of vLLM pods | CONFIRMED | https://llm-d.ai/blog/llm-d-v0.5-sustaining-performance-at-scale |
| 19 | §2.2 / §5.1 llm-d workload-autoscaling: the five paths and their signals/scale-to-zero status (queue-based supported; saturation-based and token-aware "needs validation"; SLO-aware via KEDA; WVA supported), the Kueue `ClusterQueue` rebalancing description, and the **"8192-token prompt is 16x the prefill work of a 512-token one"** quote verbatim (`8192/512 = 16` ✓) | CONFIRMED | https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/README.md |
| 20 | §2.1 / §5.1 / §7.3 Dynamo SLA Planner: `optimization_target: "sla"`, `enable_throughput_scaling`, `enable_load_scaling`, **`ttft_ms` default 500.0**, **`itl_ms` default 50.0**, `pre_deployment_sweeping_mode` = `rapid` (**~30 s**, AIC simulation) / `thorough` (**2–4 h**, real GPUs) / `none`; and the fail-safe limitation quote (*"stops automatic effect submission to avoid duplicating an action"*) | CONFIRMED | https://docs.nvidia.com/dynamo/components/planner/planner-guide |
| 21 | §2.3 AIBrix paper: *"a 50 % increase in throughput and a 70 % reduction in inference latency"*, hybrid **Kubernetes for coarse-grained scheduling, Ray for fine-grained execution** | CONFIRMED | https://arxiv.org/abs/2504.03648 |
| 22 | §2.4 KServe **v0.17, 2026-03-13**; `LLMInferenceService` graduated experimental → production-ready; **GIE v1.3.0**; **breaking** Helm restructuring (no plain `helm upgrade` from v0.16); the minimal YAML including `apiVersion: serving.kserve.io/v1alpha2` matches character-for-character; token-based rate limiting via Envoy AI Gateway is indeed **KServe's** claim | CONFIRMED | https://kserve.github.io/website/blog/kserve-0.17-release |
| 23 | §2.5 GAIE: `inference.networking.k8s.io/v1`, `selector` / `targetPorts` / `endpointPickerRef`; the EPP metrics quote verbatim; `failureMode: FailOpen` semantics; **"Until release `v1.5.0`, the InferencePool field `endpointPickerRef` was required. Currently, it is optional"** | CONFIRMED | https://gateway-api-inference-extension.sigs.k8s.io/api-types/inferencepool/ |
| 24 | §2.6 LWS `DisaggregatedSet`: header comment ("starting point, not turnkey"), `apiVersion: disaggregatedset.x-k8s.io/v1`, roles `prefill`/`decode`, image **`vllm/vllm-openai:v0.28.0`** with the "pinned; bump to the latest release" comment, `NixlConnector` `kv_producer`/`kv_consumer` | CONFIRMED | https://github.com/kubernetes-sigs/lws/blob/main/docs/examples/disaggregatedset/basic/vllm.yaml |
| 25 | §2.7 SGLang PD: `--disaggregation-mode prefill|decode`, `--disaggregation-transfer-backend` accepts **`mooncake` (default)**, `nixl`, `ascend`; `uv pip install mooncake-transfer-engine`; `sglang_router --pd-disaggregation` is documented | CONFIRMED | https://docs.sglang.io/advanced_features/pd_disaggregation.html |
| 26 | §3.1 Pyxis: *"a SPANK plugin for the Slurm Workload Manager"*, unprivileged `srun`, **enroot `3.1.0`**, *"Supports multi-node MPI jobs through PMI2 or PMIx"*, *"Since Slurm 21.08, pyxis must be compiled against the release of Slurm that is going to be deployed"* → `Incompatible plugin version` | CONFIRMED | https://github.com/NVIDIA/pyxis |
| 27 | §3.2 arXiv:2511.21413: *"A queue time above 5 seconds over 30 sustained seconds"*, configurable **30-minute** startup timeout, **~500 ms** Web Gateway overhead, **Mistral Small 3.2 24B Instruct**, GPU-L = **1× H100**, **Mellanox InfiniBand HDR100**; and the full results row matches the paper's GPU-L-via-gateway column: E2EL median **1,149.93 / 3,076.11 / 9,419.23 ms**, TTFT median **207.35 / 1,133.02 / 3,109.98 ms**, **5.79 / 19.31 / 26.95 req/s** | CONFIRMED | https://arxiv.org/html/2511.21413v1 |
| 28 | §1.8 Modal: Parakeet **20 s → 2 s**, ViT+`torch.compile` **8.5 s → 2.25 s**, vLLM+Qwen2.5 **45 s → 5 s**; **570 and 575** driver branches; **alpha**; the four checkpointed categories verbatim | CONFIRMED | https://modal.com/blog/gpu-mem-snapshots |
| 29 | §1.8 ServerlessLLM: abstract says *"reducing latency by 10 - 200X"* — the document's **10–200×** is right (a summarizer misread it as 10–100×; the PDF text is unambiguous); three contributions match | CONFIRMED | https://www.usenix.org/system/files/osdi24-fu.pdf |
| 30 | §1.7 Cloudflare Workers AI: **"Neurons"** billing unit, *"Regular Twitch Neurons (RTN) — running wherever there's capacity"* / *"Fast Twitch Neurons (FTN) — running at nearest user location"*, **7 sites → ~100 by end-2023 → nearly everywhere by end-2024**, published **2023-09-27** | CONFIRMED | https://blog.cloudflare.com/workers-ai/ |
| 31 | §1.5 Anthropic Managed Agents, **2026-04-08**: *"our p50 TTFT dropped roughly 60 % and p95 dropped over 90 %"*; stateless brain / `execute(name, input)` / durable session log / `wake(sessionId)` / `getEvents()` / *"for cache optimization"* | CONFIRMED | https://www.anthropic.com/engineering/managed-agents |
| 32 | §1.9 vLLM blog: **2.2k tok/s per H200** (up from ~1.5k), **v0.11.0** V1 engine, **DeepEP** kernels, **ConnectX-7** InfiniBand, llm-d / Dynamo / Ray Serve LLM all named | CONFIRMED | https://vllm.ai/blog/2025-12-17-large-scale-serving |
| 33 | **Repo cross-references — every one opened and the number located.** `matrix/recommendations.md`: **90.2–97.0 %** agentic hit; DSpark **3.13× output per byte** and **$1.50 → $4.69**; wide-EP *"runs badly"* on an 8-GPU domain; `--kv-cache-dtype fp8` **+41 % (231 → 325 @8K)** on Qwen3.8-27B; the `flashinfer`/`trtllm_mha` landmines; DSpark acceptance swings **$/1M output by 3–3.5×**. `matrix/pairs.json` B300 rows: DeepSeek **1,373 / $1.4973–$3.0352 / 2,656 / blended $0.4809–$0.9747**; Qwen **12,463 / $0.1649–$0.3343 / 13,461 / $0.0602–$0.1221**; Kimi **278.06 decode-only, 198.9 sustained / $7.3926–$14.985 / 569.4 / $2.3811–$4.8265**; Marlin **93,271 / $0.022–$0.0447 / $0.0092–$0.0185**; and the verbatim *"16/32 GPUs fit but cross InfiniBand for no gain; use DP2xTP8 replicas instead of TP16"* and *"TP only buys KV budget and stops buying it past TP4 because the model has 4 KV heads (TP8 replicates them ×2)"*. `METHODOLOGY.md §8`: 268 GB/GPU (2,144 GB/node), Kimi-K3 **1,560.9 GB** (= **195.1 GB/GPU** at 8, recomputed), 13.5 KiB/token MLA KV, 890 B/token on sm_103, Qwen **30.87 GB** FP8, Marlin **5.444 GB** BF16, comm overhead **5–15 % / 20–40 %** | CONFIRMED | local files |
| 34 | **§7 blueprint arithmetic — every cell recomputed with `python3`.** A: `278.06×8 = 2,224` (`198.9×8 = 1,591`), `1,373×16 = 21,968`, `12,463×6 = 74,778`, `93,271×2 = 186,542`, text total **98,970 ≈ 99.0k**, cost `32×7.40 = $236.80` / `32×15 = $480.00`. B: `4,449`, `109,840`, `199,408`, `746,168`, total **313,697 ≈ 314k**, `$947.20` / `$1,920.00`, S4 `569.4×16 + 2,656×80 + 13,461×16 = 436,966 ≈ 437k`. C: `17,796`, `439,360`, `797,632`, `2,984,672`, total **1,254,788 ≈ 1.25M**, `$3,788.80` / `$7,680.00`, S4 **1,747,866 ≈ 1.75M`**. Also §4.3 `608/168 = 3.62 ≈ 3.6:1`; §5.6 `2,000 × 25 ms = 50 s`; §1.2 `400 Gbps = 50 GB/s`; open question 5's `87/100 = 87 %` of line rate. **All correct as printed** | CONFIRMED | `python3` |

### CORRECTED

| # | Claim (§) | Was | Now | Source that settles it |
|---|---|---|---|---|
| C1 | §1.2 item 1 and §4.3 gate 1 — Kimi-K3 128k KV handoff size | **~1.73 GB** | **~1.77 GB** (`128,000 × 13,824 B = 1,769,472,000 B`; 35.4 ms at 50 GB/s) | Arithmetic on `METHODOLOGY.md §8`'s pinned **13.5 KiB**/token. 1.73 GB is `128,000 × 13,500` — 13.5 **KB** decimal. The paired DeepSeek figure (114 MB = `128,000 × 890`) uses the same 128,000-token basis, so the two were internally inconsistent. The ~35 ms conclusion is unchanged |
| C2 | §4.3 PD sizing worked example — node-second units | **`3.61k : 11.35k`** | **3.61M : 11.35M** (3,605,102 s : 11,351,351 s) | `python3`: `265.7e9/73.7e3` and `168e9/14.8e3`. Off by 1,000× on both sides, so the derived **1:3.1** ratio was unaffected |
| C3 | §2.7 LMCache MP TTFT multiplier | **13.6×** | **13.7×** (`3.98 / 0.29 = 13.72`) | https://blog.lmcache.ai/en/2026/04/03/lmcaches-new-architecture-boosts-moe-inference-performance-by-10x/ — 0.29 s / 3.98 s, p99 1.30 / 13.55 s (10.4×, "> 10×" ✓), 37.47 / 9.81 tok/s (3.82×, "≈ 4×" ✓); 8×H100 80GB, Qwen3-235B-A22B-Instruct-2507-FP8, `--l1-size-gb 400 --eviction-policy LRU` all ✓ |
| C4 | §5.1 llm-d block quote | *"by which **point** latency has spiked"* | *"by which **time** latency has spiked"* | https://github.com/llm-d/llm-d/blob/main/guides/workload-autoscaling/README.md — a block quote must be exact |
| C5 | §5.2 GPU Operator Helm flag `--set gds.enabled=true` | labelled *"DMA-BUF with Network-Operator-managed drivers"* | **GPUDirect *Storage***, not an RDMA enablement flag; the DMA-BUF prerequisites (Open Kernel module driver, CUDA ≥ 11.7, Linux ≥ 5.12, Turing+; MLNX_OFED/DOCA-OFED *optional*) and the legacy path's *mandatory* OFED requirement are now stated | https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-operator-rdma.html |
| C6 | §4.5 overflow price | *"`p6-b300`/OCI B300 `high` tier is **$15.00**/GPU-h … overflow costs roughly **2×**"* | The two vendors are separated: **$15.00** is OCI `BM.GPU.B300.8`; **AWS `p6-b300.48xlarge` on-demand is $17.802/GPU-h** ($142.416 ÷ 8), capacity blocks **$14.04**. Overflow is **2.0× (OCI) – 2.4× (AWS on-demand)** vs the $7.40 `low` tier | [`../cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) (AWS price-list JSON row and OCI SKU B112237), consistent with `METHODOLOGY.md §8`'s "2.2–2.4× neocloud rates" |
| C7 | §1.4 LMSYS 8×/4× attribution | *"within four months of GB200 availability"* | *"Compared to our prior **InferenceMAXv1 submission, less than 4 months ago**, the latest v2 release with low precision NVFP4 delivers up to 8x more tokens-per-GPU … on **existing GB200 NVL72 deployments**"* — a software-release delta, not a hardware-age one | https://www.lmsys.org/blog/2026-02-20-gb300-inferencex/ (fetched and grepped verbatim). The separate **25× vs H200** headline and the 50 TPS/user baseline are confirmed unchanged |
| C8 | §1.9 Anyscale config snippet, presented as *"quoted from the post"* | `data_parallel_size=16` | The post writes **`data_parallel_size=DP_SIZE`**; `16` was this document's substitution and is now labelled as such. The post's exact throughput phrase — *"2.4k tps/H200 on Nebius with Infiniband"* — is added | https://www.anyscale.com/blog/ray-serve-llm-anyscale-apis-wide-ep-disaggregated-serving-vllm |

### UNVERIFIABLE

| # | Claim (§) | Why |
|---|---|---|
| U1 | §2.8 / §6 — Dynamo 1.0 multimodal E/P/D *"Disaggregated encode/prefill/decode with embedding cache — **30 % faster TTFT on image workloads**"* | Not present in the Dynamo README "Key Results" table or the capability table that were fetched, and no recipe or benchmark page backing the 30 % figure was located. It is already labelled `vendor`; it is now also **⚠️ TO BE VERIFIED** — do not use it to justify the Marlin-2B stack choice without finding the primary page |

*(Related but **not** an error: the `recipes/` index page summarises Kimi-K3 as "TP16 over MNNVL (GB200) / **TP8** (GB300)", while the `agg-gb300-agentic/deploy.yaml` this document quotes actually carries `--tensor-parallel-size 16` over `nodeCount: 4`. The deploy.yaml is the primary artefact and the document quotes it correctly; the index summary is the thing that is stale. Flagged here so a future reader does not "fix" §2.1 toward the index.)*

### Structural notes

- **Post-log update (2026-09-19, cross-doc consistency pass):** items #17 and #22
  above CONFIRMED llm-d **v0.7** and KServe **v0.17** against the README/blog pages
  that were open at the time; those pages describe real historical releases and the
  CONFIRMED verdicts stand for what they say. But `02-serving-stack-and-routing.md`
  §2.1 later found the llm-d README stale and pulled current versions from the
  GitHub releases API instead: llm-d is now **v0.9.0** (2026-08-17), KServe is now
  **v0.20.0** (2026-08-06). The §2 table and §2.2/§2.4 above are updated to match;
  see those sections for the `curl`-verified detail.
- **Fireworks' *"up to 4x higher throughput"* (§1.7)** was not re-opened in this
  pass; it remains `vendor`-labelled with no harness, which is the correct
  treatment either way.
- **`research/scaling/` is missing six of the nine numbered documents** the
  folder's numbering implies (present: `01`, `04`, `07`, `09`). `09` forward-refs
  §4.3, §5.1, §7 etc. only within itself, so no cross-document link is broken —
  but the brief's topics of **concurrency handling, autoscaling mechanics,
  latency/cold-start and predictive scaling** currently exist only as fragments
  inside `09`, which is a reference-architecture survey, not a treatment of them.
- **No contradiction was found** between this document and `METHODOLOGY.md §8`,
  `matrix/pairs.json`, `matrix/cost-matrix.md` or `matrix/recommendations.md`
  after C1/C2/C6 above. The one *stated* disagreement the document already
  carries (NVIDIA's `--enable-expert-parallel` with DCP16 vs this tree's
  DCP-without-EP recommendation, §2.1 item 3 / open question 4) is correctly
  framed as engine- and domain-dependent rather than as an error on either side.
- **Nothing in this document reads as invented.** Every number that could be
  traced to a primary artefact was found there, including the ones that looked
  most suspicious going in (the 750× with no baseline, the 4.2584 synthetic
  acceptance constant, and the 178 tok/s/user B300-beats-NVL72 inversion) — all
  three are real, and all three were already correctly hedged.

- **2026-09-19, gap `G3` (Kimi-K3 B300 anchor).** §7's per-GPU input table now
  qualifies the Kimi-K3 row: of its two rates only the **sustained** 198.9
  tok/s/GPU is corroborated by a published B300 measurement — Wafer's 196 out
  tok/s/GPU peak aggregate (TP8+DCP8, SGLang, DSpark, ISL 1024 / OSL 400, c64;
  <https://www.wafer.ai/blog/kimi-k3-mi355x>, 2026-07-31), within 1.5 % of it and
  1.42× under the decode-only 278.06 that Blueprints A/B/C's 2,224 / 4,449 /
  17,796 tok/s/node figures rest on (2.9× under the S4 569.4). Reconciled in
  [`../models/kimik3/b300.md`](../models/kimik3/b300.md) §3.8 as a basis
  difference, not an error; **no number in this document changed.**
