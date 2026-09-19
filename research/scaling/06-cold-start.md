# Cold start: boot, weight loading, warm-up and pre-warming

**Research date: 2026-09-19.** Follows [`research/METHODOLOGY.md`](../METHODOLOGY.md) — every
non-trivial claim carries an inline `[src](url)` or the marker **⚠️ TO BE VERIFIED** with the
estimation method stated inline. `meas.` = published measurement (whose, on what hardware, is
always named). `est.` = derived here from a stated formula. Vendor claims are labelled as
claims. Model sizes, GPU capacities and KV-byte figures are **not re-derived here** — they are
taken from [METHODOLOGY §8](../METHODOLOGY.md#8-pinned-inputs-converged-values-from-the-fact-checked-docs);
engine versions and launch flags from
[`cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md); prices from
[`cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md).

> **Scope.** This document answers: *how long does it take from "Kubernetes schedules a pod" to
> "this replica can serve a request at its steady-state TTFT", where does that time go, and what
> can be pre-paid, cached, snapshotted or overlapped so an autoscaling event does not show up in
> p99 TTFT.* It does **not** cover *when* to scale (that is the forecaster/autoscaler document in
> this folder) or *how to route around* a warming replica (the routing document) — it supplies the
> number those documents need: **the cold-start budget per model**, and the pre-warm policy that
> makes it invisible.

---

## 0. Executive summary

**The one-line version:** on this repo's hardware, a cold replica is **not** a seconds-scale
event for anything except Marlin-2B, and the dominant term moves with model size — weight I/O
dominates the two big models, CUDA-graph capture dominates the small ones, and *neither* can be
fixed by buying faster storage alone.

| Model | Checkpoint | Shape on 8×B300 | Cold start, weights pre-staged on node NVMe (est.) | Cold start, cold pull from S3 (est.) | Dominant term |
|---|---:|---|---:|---:|---|
| [Marlin-2B](../models/marlin2b/README.md) | 5.44 GB | 1 GPU | **≈ 35–55 s** | ≈ 37–57 s | CUDA-graph capture + imports |
| [Qwen3.8-27B](../models/qwen3827b/README.md) | 55.56 GB BF16 | 1 GPU | **≈ 50–80 s** | ≈ 65–95 s | capture + weight load |
| [DeepSeek-V4.1-Flash](../models/deepseek41f/README.md) | 510.29 GB | TP4 (2 replicas/node) | **≈ 2–3 min** | ≈ 4.5–6 min | weight load, then capture |
| [DeepSeek-V4.1-Flash-NVFP4](../models/deepseek41fnvfp4/README.md) | 527.27 GB | TP4 | **≈ 2–3 min** | ≈ 4.5–6 min | weight load |
| [Kimi-K3](../models/kimik3/README.md) | 1,560.9 GB | TP8, whole node | **≈ 3.5–5 min** | ≈ 11–13 min | weight load |

Full stage-by-stage arithmetic and its assumptions: **§8**. These are `est.` built from measured
per-stage figures on *other* models (§1–§3) applied to this repo's checkpoint sizes; no member of
this repo's five has a published end-to-end startup measurement, which is
[open question 1](#open-questions).

**Six decisions, with the rule and the trade-off:**

| # | Decision | Rule | Trade-off |
|---|---|---|---|
| 1 | Where do weights come from? | Node-local NVMe if the checkpoint fits the node's disk budget and the replica is long-lived; **P2P/RDMA from a serving peer** if you scale the same model out repeatedly; object store only as the backstop | NVMe pre-staging costs disk on every node (1.56 TB × models) and a cache-invalidation story; P2P costs a control plane and a first "seed" replica |
| 2 | Which loader? | Never the default `auto` safetensors loader on a >100 GB checkpoint. `runai_streamer` or `instanttensor` on local/object storage; `fastsafetensors` where GDS exists — which is what this repo's own Kimi-K3 B300 recipe already pins ([inference-engines.md §6.3](../cross-cutting/inference-engines.md)) | Streamers need CPU threads and a pinned host buffer; GDS needs a filesystem and driver that support it |
| 3 | Compile / JIT caches | **Always** persist `~/.cache/vllm/torch_compile_cache`, `~/.cache/flashinfer`, `$HOME/.dj` (DeepGEMM) and the Triton/Inductor caches on node-local NVMe, keyed by (model, dtype, GPU, engine version) | A stale cache is silently wrong-keyed at best and a crash-loop at worst; the key must include the engine version |
| 4 | CUDA graphs | Keep them on (they are worth the decode throughput — [serving-optimizations.md](../cross-cutting/serving-optimizations.md)), but **cap the capture ladder** to the batch sizes you actually serve. `--enforce-eager` is a debugging tool, not a cold-start strategy | A short ladder means padding waste at unusual batch sizes; eager mode costs steady-state throughput permanently to save ~30–60 s once |
| 5 | Warm pool | Size the standby pool so that `standby_capacity ≥ arrival_ramp_rate × cold_start_p95`; below that, cold starts *will* appear in p99 TTFT | Standby GPUs are billed at full rate ($7.40/GPU-hr = `planning price · b300 · low`, $15.00 = `· high` — [cloud-pricing.md §5.14](../cross-cutting/cloud-pricing.md); AWS `p6-b300.48xlarge` on-demand is **$17.802**/GPU-hr, from [cloud-pricing.md §3.1](../cross-cutting/cloud-pricing.md), *not* §5.14, which pins OCI $15.00 as the hyperscaler row) whether or not they serve |
| 6 | Snapshot/restore | Worth it for **single-GPU** replicas (Marlin-2B, Qwen3.8-27B) today; **not** production-ready for TP4/TP8 replicas — `cuda-checkpoint` does not support the IPC memory multi-GPU inference needs [src](https://arxiv.org/html/2604.06664v1) | A snapshot is another artifact to version and garbage-collect, 3–7 GB per image |

---

## 1. Anatomy of an LLM replica cold start

### 1.1 The stages

A replica's cold start is ten stages, of which only three scale with model size. Splitting them
matters because each has a *different* fix: some are removed by pre-pulling, some by caching,
some only by overlapping.

| # | Stage | Scales with | Who pays it | Removable by |
|---|---|---|---|---|
| 1 | Node/driver readiness (GPU Operator: driver, container toolkit, device plugin, DCGM) | — (once per node) | node join | pre-installed driver (`driver.enabled=false`) |
| 2 | Container image pull + unpack | image size | first pod per (node, image) | pre-pull, lazy pull, P2P (§5.2) |
| 3 | Container start + Python imports + framework bootstrap | engine, not model | every pod | warm process pool, snapshot |
| 4 | Tokenizer + config load | tokenizer file size | every pod | trivial (< 0.3 s) |
| 5 | Distributed init (NCCL/NVSHMEM, `torch.distributed`) | world size | every pod | — (small) |
| 6 | **Weight load** (source → host → HBM) | **checkpoint bytes** | every pod | §2 — the biggest lever |
| 7 | Compile / kernel materialization (`torch.compile` replay, FlashInfer JIT, DeepGEMM DeepJIT, autotune) | layer count, graph size | every pod, unless cached | §3 |
| 8 | KV-cache profiling + allocation | model size (MoE deviates) | every pod | — (small, 0.7–1 s class) |
| 9 | **CUDA-graph capture** | model forward time × number of captured batch sizes | every pod | §3, §4 — the second biggest lever |
| 10 | Health-ready + first-request warm-up | prompt length | every pod | pre-warm request set (§3.5) |

### 1.2 Measured per-stage durations — vLLM

The only systematic public characterization is *Breaking the Ice: Analyzing Cold Start Latency in
vLLM* (MLSys 2026, Paderborn/IBM Research), which decomposes vLLM **v0.10.1.1** startup on an
H100 NVL + AMD EPYC 9354 node across 22 models
[src](https://arxiv.org/abs/2606.07362). Its headline decomposition, Llama3.2-3B, **total 20.32 s**
[src](https://arxiv.org/html/2606.07362v3):

| Stage | Measured (Llama3.2-3B, H100, vLLM v0.10.1.1) | Scaling law the paper fits | Resource |
|---|---:|---|---|
| Framework bootstrapping | ~2–4 s (model-independent) | flat | CPU |
| Tokenizer init | **0.08 s** (Llama2-7B, 1.8 MB tokenizer) → **0.29 s** (Llama3-3B, 8.7 MB) | linear in tokenizer file size, PCC 0.99 | CPU |
| Model structure init | **0.1 ± 0.05 s** | flat, architecture-independent | CPU |
| Weight load (warm page cache) | **0.5–1 s** (1.8–3 B params) → **~5 s** (DeepSeek-V2-Lite-16B) | linear in bytes, PCC = 1 | CPU/IO |
| `torch.compile` (cache **hit**) | **2.89 s** (Qwen-MoE-14.3B, 808 KB graph) → **5.66 s** (Yi-9B, 1.69 MB graph) | linear in compiled-graph size, PCC 0.95 | CPU |
| `torch.compile` (cache **miss**, `VLLM_DISABLE_COMPILE_CACHE=1`) | **11–21 s** across models | linear, PCC 0.95 | CPU |
| KV-cache profiling (compile excluded) | **0.67 s** (Qwen-0.5B) → **0.94–0.97 s** (Qwen-MoE-14.3B, DeepSeek-V2-Lite-16B) | linear in params, PCC 0.92 — **MoE deviates upward** | GPU |
| CUDA-graph capture | **0.91 s** (Qwen-0.5B) → **1.51 s** (Qwen-MoE-14.3B); **0.33 s at 3 capture sizes → 1.8 s at 60** (Llama2-7B) | linear in *both* model size and number of captured batch sizes, PCC 0.99 / 1.0 | GPU |

Three findings from that paper that change how you budget:

1. **The process is CPU-bound, not GPU-bound.** Only KV profiling and graph capture are
   GPU-bound; everything else is Python, imports and compiler work
   [src](https://arxiv.org/html/2606.07362v3). A faster GPU does **not** shorten a cold start.
   This is why `p6-b300.48xlarge`'s 192 vCPU / 4 TB RAM
   [src](https://aws.amazon.com/blogs/aws/accelerate-large-scale-ai-applications-with-the-new-amazon-ec2-p6-b300-instances)
   matters to startup as much as the eight B300s do.
2. **Storage speed barely moves the total at small scale.** Re-running with the Linux buffer cache
   flushed, against an LVM RAID-0 of four PCIe 5.0 SSDs measured with `fio` at **25 GB/s read /
   15 GB/s write**, the weight-load step slowed **0.5×** but the *end-to-end* startup changed only
   **1.04×** — "because the loading step constitutes only about 7–10 % of the total startup
   duration in our measurements" [src](https://arxiv.org/html/2606.07362v3). **That conclusion
   inverts at this repo's scale**: at 510 GB and 1.56 TB the loading step is 60–90 % of the total
   (§8), which is exactly why §2 is the longest section here.
3. **Startup latency is not monotone in engine version.** Across the last nine vLLM releases the
   paper measures **> 4× variance** in startup time for one model, and a **2× reduction between
   v0.9 and v0.10** [src](https://arxiv.org/html/2606.07362v3). Pin the engine version in your
   cold-start SLO, or the SLO is meaningless. (This tree pins vLLM **0.29.0** / SGLang **0.5.20**
   as of 2026-09-19 — [inference-engines.md §0](../cross-cutting/inference-engines.md).)

The paper also publishes a **white-box per-stage regression predictor** (one linear regressor per
stage, trained from a few hours of automated runs), validated at **MSE 2.42 s, max error 2.08 s**
on held-out non-MoE models, and re-validated on v0.11 at MSE 2.62 s; the artifact is at
`https://github.com/upb-cn/vllm-startup-profiler`
[src](https://arxiv.org/html/2606.07362v3). **This is the right tool to calibrate the §8 budget
against real hardware** — see [open question 1](#open-questions). Note its stated limit: the
predictor is fitted **for non-MoE models only**, and MoE models deviate in KV profiling because of
"dynamic expert activation and load-balancing mechanisms" [same src] — i.e. four of this repo's
five models are outside its validated envelope.

### 1.3 Measured per-stage durations — SGLang

SGLang's own team published a startup breakdown for **Ling-2.6-1T FP8 on 8×H20-3e**, 161 shards,
W8A8 FP8, slowest rank 495.3 s, 120 GB per card
[src](https://www.lmsys.org/blog/2026-08-21-sglang-fast-recovery):

| Phase | Duration | Share |
|---|---:|---:|
| **Load weight (from disk)** | **~495 s** | **93.9 %** |
| Tokenizer init | ~13 s | 2.4 % |
| Capture CUDA graph | ~7.7 s | 1.5 % |
| Init `torch.distributed` | ~5 s | 0.9 % |
| Server ready / pre-init / cache allocation | ~4 s + ~1 s + ~1 s | 1.1 % |
| **Total** | **~527 s (8.8 min)** | |

Two things to carry forward: (a) at trillion-parameter scale the *shape* of the pie is completely
different from the 3 B case in §1.2 — weight loading is 94 % — and (b) `torch.distributed` init
across 8 ranks costs ~5 s, a figure §8 reuses.

An independent practitioner breakdown of a **122 B model on a B200** with SGLang, with kernel
caches cleared (`echo 3 > /proc/sys/vm/drop_caches`), reports **695 s total: 21 s Python imports,
531 s weight loading**, the rest across FlashInfer autotune, DeepGEMM, graph capture and server
warm-up [src](https://fergusfinn.com/blog/fast-sglang-starts/) (2026-04-06). The **21 s of Python
imports** is the number to plan on for a large-engine container — an order of magnitude above the
paper's 2–4 s for a small vLLM process, and a warning that "framework bootstrap is flat" only
holds within one engine/image.

### 1.4 TensorRT-LLM: engine build vs PyTorch backend

TRT-LLM's historic cold start had a distinct extra stage — an **ahead-of-time engine build**
producing a serialized plan file per (model, precision, parallelism, max batch, max seq len). That
stage is **no longer on this repo's critical path**: in TRT-LLM as tracked by this tree, *"the
TensorRT (graph-compiler) backend is **removed**; PyTorch is the sole execution backend"*
([inference-engines.md §2.3](../cross-cutting/inference-engines.md)). The practical consequences:

- There is no multi-minute-to-hours `trtllm-build` step to cache any more, and no plan-file
  invalidation matrix to manage. The startup profile converges on the vLLM/SGLang shape: imports →
  weights → JIT/compile → graph capture.
- The AutoDeploy path, which extracts a graph from a HF model and applies automated graph
  transformations into the PyTorch runtime, is *"under active development and is currently in a
  prototype stage"*, with *"the code … experimental, subject to change, and may include
  backward-incompatible updates"*
  [src](https://nvidia.github.io/TensorRT-LLM/torch/auto_deploy/auto-deploy.html). **⚠️ TO BE
  VERIFIED — corrected 2026-09-19:** an earlier draft of this document said AutoDeploy is *"being
  deprecated"* in favour of *"agentic approaches to improve time to functional model support in the
  PyTorch backend."* Neither statement appears on the cited page (re-fetched 2026-09-19); treat the
  deprecation claim as unsourced until an NVIDIA roadmap page carries it. Either way AutoDeploy is
  a prototype, so nothing in §8 depends on it.
- It also removes an option: you can no longer amortize *all* kernel selection into an offline
  build artifact. What survives is per-library caching (§3).

Relevance to this repo is limited anyway: TRT-LLM has **no DeepSeek-V4.1 support** and Kimi-K3
only as a Blackwell-only build-from-source
([inference-engines.md §0](../cross-cutting/inference-engines.md)).

### 1.5 Dynamo

[NVIDIA Dynamo](https://docs.nvidia.com/dynamo/) is an orchestration layer over vLLM/SGLang/TRT-LLM
([inference-engines.md §0](../cross-cutting/inference-engines.md)), so a Dynamo worker's cold start
**is** its backend engine's cold start plus Dynamo's own component startup (frontend, router,
etcd/NATS registration). What Dynamo adds that is specific to this document is **ModelExpress**
(§2.4), its weight- and artifact-transfer plane, and the **Planner** (§5.4), which is the component
that has to *know* the cold-start number to scale without oscillating.

### 1.6 How the stages scale across this repo's size range

| | Marlin-2B (5.44 GB) | Qwen3.8-27B (55.56 GB BF16) | DeepSeek-V4.1-Flash (510.29 GB) | Kimi-K3 (1,560.9 GB) |
|---|---|---|---|---|
| Weight load | seconds | seconds–tens | **tens of seconds–minutes** | **minutes** |
| Imports/bootstrap | ~10–21 s, flat | same | same | same |
| Compile cache **hit** | seconds | seconds | tens of seconds ⚠️ | tens of seconds ⚠️ |
| Compile cache **miss** | tens of seconds | tens of seconds | **minutes** ⚠️ | **minutes** ⚠️ |
| CUDA-graph capture | ~1–2 s at a short ladder | seconds | **tens of seconds** (26-size ladder, §3.2) | **tens of seconds** |
| Ratio weight-load : rest | ~1 : 10 | ~1 : 3 | ~3 : 1 | ~8 : 1 |

The ratio flip between Qwen3.8-27B and DeepSeek-V4.1-Flash is the single most important structural
fact in this document: **the optimization that matters is different on either side of it.** Below
~100 GB, spend your effort on §3 (caches and graph capture). Above it, spend it on §2 (getting
bytes into HBM) — and then on §3, because once §2 is fixed §3 becomes the bottleneck again (this
is exactly what the SGLang Weight Cache Daemon result shows: 8.8 min → 0.528 min, at which point
the 7.7 s of graph capture is suddenly 24 % of the remaining budget
[src](https://www.lmsys.org/blog/2026-08-21-sglang-fast-recovery)).

---

## 2. Weight loading fast paths

### 2.1 The physical ceilings

Before comparing loaders, know what the hardware can do, because most loaders' "speedup" is just
"stopped leaving bandwidth on the floor."

| Path | Ceiling | Source |
|---|---:|---|
| Single-threaded read, cloud gp3-class SSD | ~1 GiB/s | Run:ai measures concurrency 1 = 47.56 s for a 15 GB model = 0.32 GB/s, and concurrency 16 = 14.34 s = 1.05 GB/s, "the limit of the GP3 SSD" [src](https://github.com/run-ai/runai-model-streamer/blob/master/docs/src/benchmarks.md) |
| 4× PCIe 5.0 NVMe, RAID-0/LVM | **25 GB/s read** (fio, large transfers) | [src](https://arxiv.org/html/2606.07362v3) §3.3 |
| `p6-b300.48xlarge` local instance store | 8 × 3.84 TB (30.7 TB raw) | [src](https://aws.amazon.com/blogs/aws/accelerate-large-scale-ai-applications-with-the-new-amazon-ec2-p6-b300-instances) — aggregate throughput not published; **⚠️ TO BE VERIFIED** by `fio` on the node. This document uses the task's planning band of **10–20 GB/s**, consistent with 4×PCIe-5 measuring 25 GB/s above |
| `p6-b300` EBS / ENA / EFA | 100 Gbps (12.5 GB/s) / 300 Gbps (37.5 GB/s) / 6.4 Tbps | [src](https://aws.amazon.com/blogs/aws/accelerate-large-scale-ai-applications-with-the-new-amazon-ec2-p6-b300-instances) |
| S3 → instance, stock sequential range submission | **5.96 Gbps ≈ 0.75 GB/s** | AWS controlled experiment [src](https://aws.amazon.com/blogs/containers/fast-model-loading-for-ai-inference-on-amazon-eks/) (2026-09-01) |
| S3 → instance, **all ranges submitted at once** | **33–39 Gbps ≈ 4.1–4.9 GB/s** | same src |
| Host → device, PCIe Gen5 x16 | ~63 GB/s per direction theoretical | **⚠️ TO BE VERIFIED** — method: PCIe 5.0 spec 32 GT/s × 16 lanes × 128b/130b encoding ≈ 63 GB/s; achieved H2D is typically lower. No fetched primary source; not load-bearing below because storage, not PCIe, is the binding constraint at 10–20 GB/s |

**The recurring lesson:** the default loader's problem is almost never the device, it is
*concurrency*. Run:ai's own benchmark makes the point cleanly — same disk, same model, 47.56 s at
concurrency 1 vs 14.34 s at concurrency 16 [src](https://github.com/run-ai/runai-model-streamer/blob/master/docs/src/benchmarks.md).
AWS's S3 experiment makes the same point at the network layer: 5.96 Gbps stock vs 33–39 Gbps when
all byte ranges are submitted at once [src](https://aws.amazon.com/blogs/containers/fast-model-loading-for-ai-inference-on-amazon-eks/).

### 2.2 Loader comparison — measured

**Run:ai Model Streamer vs HF safetensors vs CoreWeave Tensorizer**, Meta-Llama-3-8B (15 GB, single
safetensors file), AWS g5.12xlarge / 1× A10G, CUDA 12.4, vLLM 0.5.5, Model Streamer 0.6.0,
Tensorizer 2.9.0, **cold-start conditions with caches cleared between runs**
[src](https://github.com/run-ai/runai-model-streamer/blob/master/docs/src/benchmarks.md):

| Storage | HF safetensors | Model Streamer (best) | Tensorizer (best) |
|---|---:|---:|---:|
| GP3 SSD (1,000 MiB/s cap) | 47.99 s | **14.34 s** (concurrency 16) | 16.11 s (16 readers) |
| IO2 SSD (up to 4,000 MiB/s) | 47.00 s | **7.53 s** (concurrency 8) | 10.36 s (8 readers) |
| S3 (same region) | n/a (unsupported) | **4.88 s** (concurrency 32) | 37.36 s (16 readers) |

And **end-to-end vLLM time-to-ready** with each loader (same src, Appendix D):

| Storage | Safetensors | Model Streamer | Tensorizer |
|---|---:|---:|---:|
| GP3 SSD | 66.13 s | **35.08 s** | 36.19 s |
| IO2 SSD | 62.69 s | **28.28 s** | 30.88 s |
| S3 | — | **23.18 s** | 65.18 s |

Note the discrepancy with the MLSys paper, which found **Tensorizer** fastest ("loading models up
to 53–60 % of Safetensors' time") and Run:ai only "moderate gains"
[src](https://arxiv.org/html/2606.07362v3) §3.4. Both are measurements; they disagree because they
test different storage. Tensorizer wins on *local* disk in the paper's setup; Model Streamer wins
decisively on *S3* in Run:ai's (4.88 s vs 37.36 s), which is the case this repo cares about.
Neither is a vendor-neutral benchmark — Run:ai's is Run:ai's own. Treat the ranking as
**storage-dependent, not absolute**, and measure on your own node
([open question 2](#open-questions)).

**fastsafetensors (IBM Research)** — the loader this repo's own Kimi-K3 B300 recipe already pins
(`--load-format fastsafetensors`, [inference-engines.md §6.3](../cross-cutting/inference-engines.md)).
Published results from the CLOUD 2025 paper and the repo's benchmark docs
[src](https://github.com/foundation-model-stack/fastsafetensors):

- **4.8×–7.5× faster** than the default safetensors deserializer on Llama, Falcon and Bloom.
- **26.4 GB/s NVMe read throughput** for Llama-70B on four GPUs **with GDS**.
- vLLM integration: Llama-2-13B startup **12.39 s → 4.74 s** on 4× L40S; **16.04 s → 6.88 s** on
  1× A100.
- ROCm, no GDS: the `nogds` path reached **6.02 GB/s** vs **1.28 GB/s** with `mmap` for
  GPT-2 Medium (4.7×).

The mechanism is the important part: it *"copies parameter groups directly to device memory for
instantiation, enabling optimizations like parallelized copying, peer-to-peer DMA, and GPU
offloading"* [src](https://arxiv.org/abs/2505.23072) — i.e. it removes the
deserialize-into-host-tensor-then-copy round trip that the default loader pays per tensor.

**InstantTensor** — the newest loader in vLLM's `--load-format` set, and by a wide margin the
fastest published figures on large checkpoints
[src](https://github.com/vllm-project/vllm/blob/main/docs/models/extensions/instanttensor.md):

| Model | GPUs | Backend | Load time | Throughput | Speedup |
|---|---|---|---:|---:|---:|
| Qwen3-30B-A3B | 1×H200 | safetensors | 57.4 s | 1.1 GB/s | 1× |
| Qwen3-30B-A3B | 1×H200 | **InstantTensor** | **1.77 s** | **35 GB/s** | **32.4×** |
| DeepSeek-R1 | 8×H200 | safetensors | 160 s | 4.3 GB/s | 1× |
| DeepSeek-R1 | 8×H200 | **InstantTensor** | **15.3 s** | **45 GB/s** | **10.5×** |

45 GB/s aggregate on an 8-GPU node is the highest single-node storage→HBM figure in any source
fetched for this document, and it is the figure that makes the §8 "NVMe 20 GB/s" planning band
look *conservative* rather than optimistic — but it is a vendor-published benchmark in the
integrating project's own docs, on H200 not B300, and the storage behind it is not described.
**⚠️ Treat 35–45 GB/s as an upper bound to be reproduced, not a planning input.**

### 2.3 The full `--load-format` matrix

vLLM's `LoadFormat` enum, as documented
[src](https://docs.vllm.ai/en/latest/api/vllm/config/load/):

| `--load-format` | What it does | When to use |
|---|---|---|
| `auto` | safetensors, falling back to `.bin` | never, above ~50 GB |
| `safetensors` / `pt` / `npcache` | explicit format selection; `npcache` writes a numpy cache to speed later loads | legacy checkpoints |
| `instanttensor` | safetensors on CUDA with distributed loading + pipelined prefetch; **GDS when available** | fastest published local path (§2.2) |
| `ipc_cache` | post-quantized weights via **CUDA IPC**, for fast engine restarts | in-place restarts on the same node (§2.5) |
| `tensorizer` | CoreWeave tensorizer proprietary format | local disk; requires a serialization step |
| `runai_streamer` | Run:ai Model Streamer for safetensors | object storage, and any case where the default loader under-uses the device |
| `runai_streamer_sharded` | same, from **pre-sharded** files `model-rank-{rank}-part-{part}.safetensors` | TP/PP, where each worker should read only its own shard [src](https://docs.vllm.ai/en/stable/models/extensions/runai_model_streamer/) |
| `sharded_state` | pre-sharded checkpoints for TP | as above, native path |
| `modelexpress` | Dynamo ModelExpress: P2P RDMA from a serving peer, else a strategy chain (§2.4) | repeated scale-out of the same model |
| `fastsafetensors` | fastsafetensors iterator — *"enables loading model weights to GPU memory by leveraging GPU direct storage"* [src](https://docs.vllm.ai/en/latest/models/extensions/fastsafetensor/). Documented on its own extensions page, **not** in the `LoadFormat` enum listing (which as fetched on 2026-09-19 carries `auto, pt, safetensors, instanttensor, ipc_cache, npcache, dummy, tensorizer, runai_streamer, runai_streamer_sharded, sharded_state, mistral, modelexpress`) | GDS-equipped nodes; already this repo's Kimi-K3 recipe |
| `mistral` | Mistral-format checkpoints (present in the enum; listed here for completeness) | Mistral releases |
| `dummy` | random weights | profiling startup *without* the weight-load term — useful for isolating §3 costs |

SGLang's set overlaps and adds three things worth stealing
[src](https://docs.sglang.io/docs/advanced_features/model_loading):

- `--load-format remote_instance` — *"Pull weights over the network from another running SGLang
  instance rather than from disk."* The P2P idea, native.
- `--weight-loader-prefetch-checkpoints` — *"Prefetch checkpoint files into the OS page cache
  before loading,"* reducing network I/O on shared filesystems **"from N×checkpoint to
  1×checkpoint."** This names the read-amplification trap directly: with N TP ranks each opening
  the whole checkpoint, a shared filesystem serves N copies unless something dedupes. On an 8-way
  TP Kimi-K3 that is the difference between 1.56 TB and 12.5 TB of reads.
- `--weight-loader-drop-cache-after-load` — free page cache after each shard, for when host RAM is
  the binding constraint.
- `--load-format layered` — *"Load weights layer by layer, so a layer can be quantized before the
  next is loaded"* — the memory-ceiling escape hatch for on-load quantization (§2.7).

**Decision rule for this repo.** Pre-sharded + streamed is strictly better than
monolithic + streamed at TP≥4, because it removes read amplification *and* lets each rank's I/O
proceed independently. The trade-off is that a pre-sharded checkpoint is **bound to its TP degree**
— re-sharding DeepSeek-V4.1-Flash from TP4 to TP2 (the shape [b300.md](../models/deepseek41f/b300.md)
actually recommends switching between) means regenerating 510 GB of shards. Keep the canonical
unsharded checkpoint on S3 and materialize per-TP shard sets on node NVMe only for the shapes you
actually run.

### 2.4 P2P: weights from a peer, not from storage

This is the largest single lever available for scale-out, and it is the one that inverts the whole
problem: **the fastest copy of the weights is the one already in a serving replica's HBM.**

**NVIDIA Dynamo ModelExpress**, measured on DeepSeek-V4-Pro, vLLM 0.23.0, TP=8, **8×B200 node with
ConnectX-7 NICs** [src](https://github.com/ai-dynamo/modelexpress):

| Loading path | Time | vs cold HF pull |
|---|---:|---:|
| Cold pull from Hugging Face | 8 m 53 s | 1× |
| ModelStreamer from S3 | 3 m 16 s | 2.7× |
| High-throughput local storage, cold page cache | 1 m 10 s | 7.6× |
| **P2P GPU-to-GPU over NIXL/RDMA** | **11 s** | **48×** |

And separately, the **artifact** half of the same problem — because once weights are 11 s, JIT
caches dominate:

| Startup path | API ready | Speedup |
|---|---:|---:|
| Cold start from VAST, no P2P source | 8 m 1 s | 1× |
| P2P RDMA **weights only** | 7 m | 1.1× |
| P2P RDMA **weights and kernel artifacts** | **1 m 44 s** | **4.6×** |

Read those two rows together: **transferring weights alone bought 1.1×.** The 4.6× came from also
shipping the *"compatible Triton, DeepGEMM, TileLang, CuTe DSL, and FlashInfer caches"* [same src].
That is the single most useful empirical result in this document for a DeepSeek/Kimi deployment,
because both models lean on exactly those JIT'd kernel families
([flash-attention.md](../cross-cutting/flash-attention.md),
[quantization-formats.md](../cross-cutting/quantization-formats.md)).

Its strategy chain, tried in fixed order with safe fallback [same src]:

1. Serving peer → GPU over NIXL P2P RDMA
2. Server cache → runtime
3. Local safetensors → GPU via InstantTensor
4. Remote/local storage → GPU with ModelStreamer
5. Local storage → GPU with GDS
6. Engine's native loader

```bash
export MX_SERVER_ADDRESS=modelexpress-server:8001
export MX_P2P_METADATA=1
export MX_ARTIFACT_TRANSFER=1          # ← the 4.6× flag, not the 1.1× one

vllm serve deepseek-ai/DeepSeek-V4-Pro \
  --load-format modelexpress \
  --tensor-parallel-size 8 \
  --trust-remote-code
```
[src](https://github.com/ai-dynamo/modelexpress) — note `MX_ARTIFACT_TRANSFER=1` additionally
requires a central-coordinator metadata backend (`redis` or `kubernetes`) and writable staging +
runtime cache dirs; it is **not** supported with the decentralized `k8s-service` backend.

One non-obvious cost this benchmark exposes: **NIXL memory registration** is itself a startup
term, and the default is the slow one [same src]:

| Registration strategy | Time | Speedup |
|---|---:|---:|
| Per tensor (**default**) | 8.16 s | 1× |
| Pool registration (`MX_POOL_REG=1`) | 1.14 s | 7.1× |
| VMM arena (`MX_VMM_ARENA=1`) | 0.79 s | 10.3× |

Set one of them (not both). 8 s is 7 % of a two-minute budget for free.

Corroboration that this is the industry direction, not one vendor's: the Foundry authors state
that *"recent systems reduce weight loading to 1–2 seconds via RDMA-based transfer from peer
instances, even for trillion-parameter models such as Kimi-K2"*
[src](https://arxiv.org/html/2604.06664v1), citing Perplexity and the Ant Group DeepXPU/SGLang
work. **⚠️ TO BE VERIFIED** — those underlying primary blogs were not fetched for this document
(web-search budget exhausted); the 1–2 s claim is reported here at second hand and is the most
optimistic figure in this section.

**Trade-off.** P2P makes the *first* replica the expensive one and every subsequent replica nearly
free, so it converts cold-start cost from `O(replicas)` to `O(1)` per scale-out event — but it
introduces a hard dependency: **if there is no healthy source replica, you are back to the storage
path.** Scaling from zero never benefits. That is an argument for keeping `min_replicas ≥ 1` on
any model whose cold start you care about, which §5.5 costs out.

### 2.5 In-place restart: weights already in HBM

Distinct from P2P *across* nodes: keeping a copy resident on the **same** node so an engine restart
does not re-read anything.

**SGLang Weight Cache Daemon** — a persistent GPU process per TP rank holding post-quantized,
TP-sharded weights, exported to new engine instances via **CUDA IPC zero-copy mapping**; the new
engine builds its model on the `meta` device and swaps parameter pointers to the IPC-mapped
tensors [src](https://www.lmsys.org/blog/2026-08-21-sglang-fast-recovery):

| Model | Weight load before | after | Speedup |
|---|---:|---:|---:|
| Ling-2.6-1T FP8, 8×H20-3e | ~495 s (disk) | **~0.63 s** (IPC) | **~785×** |
| Qwen3-235B FP8 | ~306–327 s | **< 1 s** | ~500× |

(The blog states both the "~495 s → ~0.63 s, ~785×" headline and, elsewhere, a "~405–411 s → <1 s,
~780×" figure for the same model — the latter appears to exclude part of the disk path. 495 s is
the figure its own profiling table carries and the one §1.3 and §8 use.)

End-to-end: **8.8 min → 0.528 min, a 93.9 % reduction** for Ling-2.6-1T [same src].

```bash
# daemon (holds weights):  --model-path --tp-size --load-format --dtype --quantization
# engine (borrows them):   --weight-cache-mode client
```

Modes are `daemon` (engine spawns it; first load still ~495 s, restarts < 1 s), `client` (attach to
a pre-running daemon), `off` (default). **Limits, quoted:** *"Any mismatch between the engine's
config and the daemon's cached config triggers a full disk reload"*; the fingerprint covers model
path, TP/DP size, quantization hash, dtype, device capability and torch version. Quantization
support is an **allowlist** — *"Currently verified: unquantized and block-wise FP8"*; per-tensor
FP8, Marlin, AWQ/GPTQ *"raise a hard error."* Phase 1 covers TP + PP, single- and multi-node;
**DP/EP is future work** [same src].

For this repo that allowlist is decisive: DeepSeek-V4.1-Flash is **MXFP4 experts + FP8 32×32 UE8M0
non-expert**, Kimi-K3 is **97.9 % MXFP4 by bytes**
([METHODOLOGY §8](../METHODOLOGY.md#models)) — neither is "unquantized or block-wise FP8", and
Kimi-K3's recommended shapes use EP. **The daemon as shipped does not cover this repo's two big
models.** ⚠️ Re-check per SGLang release; the mechanism is right even where the allowlist is not
yet.

vLLM's equivalent is `--load-format ipc_cache`, documented as *"post-quantized weights via CUDA IPC
for fast engine restarts"* [src](https://docs.vllm.ai/en/latest/api/vllm/config/load/) — the same
idea, no published benchmark found. ⚠️

### 2.6 Object-store and shared-filesystem paths

- **Direct streaming from S3/GCS/Azure.** `runai_streamer` accepts `s3://`, `gs://` and `az://`
  paths natively, with `concurrency` controlling client instances and `memory_limit` bounding the
  CPU staging buffer [src](https://docs.vllm.ai/en/stable/models/extensions/runai_model_streamer/):
  ```bash
  vllm serve <model> \
    --load-format runai_streamer \
    --model-loader-extra-config '{"concurrency":32,"memory_limit":5368709120}'
  ```
  `distributed: true` *"enables distributed streaming on CUDA/ROCM devices, potentially improving
  loading times from object storage"* [same src] — i.e. TP ranks divide the remote reads rather
  than each pulling everything.
- **What AWS measured on EKS** (2026-09-01), p5.48xlarge, Run:ai Model Streamer from S3
  [src](https://aws.amazon.com/blogs/containers/fast-model-loading-for-ai-inference-on-amazon-eks/):

  | Model | Config | Initial launch | Subsequent |
  |---|---|---:|---:|
  | Qwen3-6-35B-A3B (67 GiB), TP=2 | baseline | 82 s | 82 s |
  | | + streamer tuning | 65 s | 65 s |
  | | + torch.compile cache | 65 s | **16 s** |
  | Llama-4-Scout (203 GiB), TP=4 | baseline | 457 s | 463 s |
  | | + streamer tuning | **59 s** | 57 s |
  | | + torch.compile cache | 60 s | **32 s** |

  Their stage split is the §1.6 ratio flip in one table: at 64 GiB it was *"weights loading (S3 to
  GPU): ~29 s, 35 % … torch.compile: ~53 s, 65 %"*; at 203 GiB it was *"weights ~423 s, 92 % …
  torch.compile ~34 s, 8 %"* [same src]. They also measured the **s5cmd-to-NVMe-then-load** path:
  *"approximately 25 s for 200 GB to NVMe, then 20 s to GPU"* — 8 GB/s and 10 GB/s respectively,
  which is a useful independent anchor for §8's NVMe band.
- **Shared/parallel filesystems.** `p6-b300` publishes *"up to 1.2 Tbps of throughput to the Lustre
  file system"* with EFA + GPUDirect Storage
  [src](https://aws.amazon.com/blogs/aws/accelerate-large-scale-ai-applications-with-the-new-amazon-ec2-p6-b300-instances)
  — 150 GB/s, above any loader measured here, but that is an aggregate fabric number, not a
  per-replica load rate. ⚠️ Not used as a planning input.
- **GPUDirect Storage** is the common enabler under `fastsafetensors`, `instanttensor` and
  ModelExpress's strategy 5 — NVMe → HBM without the host bounce. The measured payoff:
  fastsafetensors' **26.4 GB/s** on Llama-70B/4 GPUs with GDS vs its own `nogds` path at
  6.02 GB/s on ROCm [src](https://github.com/foundation-model-stack/fastsafetensors).

### 2.7 Checkpoint layout: pre-quantized vs on-load

METHODOLOGY §1 already fixes the byte counts; the cold-start question is *when* the quantization
happens.

- **Pre-quantized on disk** (this repo's normal case): DeepSeek-V4.1-Flash ships MXFP4 experts,
  Kimi-K3 ships 97.9 % MXFP4, `nvidia/DeepSeek-V4.1-Flash-NVFP4` ships NVFP4 experts
  ([METHODOLOGY §8](../METHODOLOGY.md#models)). Startup cost = bytes ÷ bandwidth, no conversion.
- **On-load quantization** (BF16 checkpoint → FP8/INT4 at load): saves *disk and network* bytes but
  adds a CPU/GPU conversion pass and, critically, needs headroom for the unquantized tensor during
  conversion — which is why SGLang has `--load-format layered` ("so a layer can be quantized before
  the next is loaded") [src](https://docs.sglang.io/docs/advanced_features/model_loading).
- **The counter-intuitive one, already pinned in this tree:** the NVFP4 DeepSeek variant is
  **larger** on disk than the base — 527.27 GB vs 510.29 GB — because only 58 % of its bytes are
  NVFP4 and the format carries +12.5 % scale overhead
  ([METHODOLOGY §1, §8](../METHODOLOGY.md)). **Choosing the "smaller" quantization made the cold
  start ~3 % slower**, which is worth knowing before anyone assumes FP4 implies faster loading.

**Decision rule:** pre-quantize offline, always, for any model you will start more than a handful
of times. On-load quantization is for the case where you cannot mirror another 500 GB per variant
— and then `layered` is mandatory, not optional.

---

## 3. Compile, JIT and graph caches

### 3.1 What actually gets cached, and where

Five independent caches, five different directories, five different invalidation rules. Missing any
one of them re-pays its full cost on every replica.

| Cache | Default location | Env override | Cost of a miss |
|---|---|---|---|
| vLLM `torch.compile` artifacts (FX graphs + Inductor output) | `~/.cache/vllm/torch_compile_cache/` [src](https://docs.vllm.ai/en/latest/design/torch_compile/) | `VLLM_CACHE_ROOT`; disable with `VLLM_DISABLE_COMPILE_CACHE=1` | **11–21 s** vs 3–6 s cached, on ≤16 B models [src](https://arxiv.org/html/2606.07362v3) §3.5 |
| TorchInductor | `~/.cache/torch/inductor` | `TORCHINDUCTOR_CACHE_DIR` | included above |
| Triton | `~/.triton/cache` | `TRITON_CACHE_DIR` | ⚠️ unmeasured here |
| FlashInfer JIT | `~/.cache/flashinfer` [src](https://fergusfinn.com/blog/fast-sglang-starts/) | see `flashinfer show-config` | eliminated entirely by installing `flashinfer-jit-cache` (§3.3) |
| DeepGEMM DeepJIT | `$HOME/.dj` [src](https://github.com/deepseek-ai/DeepGEMM) | `DG_JIT_CACHE_DIR` (accepts a `:`-separated list; first hit wins, a miss compiles into the first path) | ⚠️ unmeasured; DeepGEMM compiles **all** kernels at runtime — *"All kernels are compiled at runtime through DeepJIT, requiring no CUDA compilation during installation"* [same src] |

vLLM's cache key is explicitly documented as covering *"all configuration hashes from vLLM configs,
PyTorch compilation settings, and the model's forward function and the relevant functions called by
the forward function"*, and *"any code change in the above files will trigger compilation cache
miss, and therefore recompilation"* [src](https://docs.vllm.ai/en/latest/design/torch_compile/).
What the page does say about reuse, verbatim, is that *"you can directly copy the whole
`~/.cache/vllm/torch_compile_cache` directory in your deployment scenario to save a great amount of
compilation time"* [same src].

**⚠️ TO BE VERIFIED — corrected 2026-09-19:** an earlier draft quoted the same page as saying *"the
compiled artifacts and the cache can be reused across machines with the same environment. If you
have an autoscaling use case, make sure to generate the cache directory once and share it among
instances."* Two independent fetches of that page on 2026-09-19 (one a targeted keyword search for
"autoscaling", "across machines", "share it among instances") found no such sentence. The weaker
"directly copy the whole directory" statement above is confirmed and is what §3.4 and §7.5 rest on;
the cross-machine validity of the cache is therefore an inference from the documented cache key
(configs + PyTorch settings + traced source), not a quoted vLLM guarantee.

A practitioner's summary of the invalidation surface, worth quoting because it tells you what does
**not** invalidate: the cache is invalidated when *model architecture, data type, GPU type, or
vLLM/PyTorch versions* change; **weight updates alone do not invalidate it**
[src](https://route179.dev/2026/06/30/optimizing-vllm-cold-start-with-model-streaming-and-compile-caching/).
That is the licence to hot-swap fine-tuned weights of the same architecture without paying
recompilation (§6.3).

Measured payoff, end to end, on **one DGX Spark (GB10, unified memory) running as an EKS Hybrid
Node** with `nvidia/Qwen3.6-35B-A3B-NVFP4` (35 B MoE, 3 B active), vLLM **v0.22.1**, weights on
local NVMe — a **practitioner** measurement, not AWS's
[src](https://route179.dev/2026/06/30/optimizing-vllm-cold-start-with-model-streaming-and-compile-caching/):

| Configuration | Weight load | torch.compile | Profiling/warm-up | **Total** |
|---|---:|---:|---:|---:|
| Baseline | ~142 s | ~39 s | ~136 s | **317 s** |
| + Model Streamer (`concurrency: 8`) | 59 s | 38 s | 124 s | **221 s** (1.43×) |
| + `VLLM_CACHE_ROOT` on persistent disk | 59 s | **10 s** | 98 s | **167 s** (1.90×) |

```yaml
# the entirety of the compile-cache fix, as deployed
env:
  - name: VLLM_CACHE_ROOT
    value: /root/.cache/huggingface/vllm_compile_cache
```
[same src]

And AWS's EKS numbers say the same thing from the other direction: torch.compile caching took the
*subsequent* launch of Qwen3-6-35B-A3B from **65 s to 16 s** and Llama-4-Scout from **60 s to 32 s**
[src](https://aws.amazon.com/blogs/containers/fast-model-loading-for-ai-inference-on-amazon-eks/).

**Operational warning.** Mounting the cache into a container is where this goes wrong in practice —
a real failure mode is `VLLM_CACHE_ROOT` pointed at a directory owned by another init container's
user, crashing **every** cold start
([giantswarm/agent-platform#541](https://github.com/giantswarm/agent-platform/issues/541)). Mount a
dedicated subPath the engine's UID owns; do not share the HF cache directory's root.

### 3.2 CUDA-graph capture: the term that does not shrink with faster storage

Capture cost is `(number of captured batch sizes) × (one model forward)`. Both factors are under
your control and both are usually set badly.

Measured anchors:

| Configuration | Capture cost | Source |
|---|---:|---|
| Llama2-7B, **3** capture sizes | 0.33 s | [src](https://arxiv.org/html/2606.07362v3) |
| Llama2-7B, **60** capture sizes | 1.80 s | same |
| Qwen-MoE-14.3B, default ladder | 1.51 s | same |
| Ling-2.6-1T FP8, 8×H20-3e | **7.7 s** | [src](https://www.lmsys.org/blog/2026-08-21-sglang-fast-recovery) |
| Qwen3-14B DP1–DP8, **512** graphs | **36–48 s** | [src](https://arxiv.org/html/2604.06664v1) |
| Qwen3-30B-A3B EP2–EP8, **512** graphs | **112–154 s** | same |
| **Qwen3-235B-A22B EP8, 512 graphs** | **650 s (≈ 10 min)** | same |

That last row is the warning label on this whole section: **on a large MoE with a full 512-size
ladder, graph capture alone can be ten minutes**, and it is entirely CPU/driver work that no
storage upgrade touches. Foundry measures the per-op costs directly: stream capture 59.7–198.6 ms
per graph vs graph construction via the explicit-construction APIs at 31.1–69.5 ms (1.9–2.9×
faster), and notes that *"parallel graph construction results in driver contention"*, so 512
sequential builds still cost ≈ 35.6 s even on the faster path [same src].

**This repo's ladders, as already pinned:**

- DeepSeek-V4.1-Flash on B300 uses *"an explicit 26-size CUDA-graph ladder to 8190"* with
  `--max-cudagraph-capture-size 8190`
  ([inference-engines.md §3](../cross-cutting/inference-engines.md), quoting
  [recipes.vllm.ai](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/b300.json)).
  **26 sizes, not 512** — a ~20× smaller capture bill than the Foundry worst case, which is why §8
  budgets tens of seconds rather than minutes.
- Escape hatches this tree already documents for models that cannot take the default
  `FULL_AND_PIECEWISE`: `cudagraph_mode: FULL_DECODE_ONLY` (Kimi-K3 on ROCm/Ascend) and
  `VLLM_USE_BREAKABLE_CUDAGRAPH=1` (DS-V4.1 on ROCm, where *"DeepSeek-V4.1-Flash does not support
  `torch.compile`, and the ROCm sparse SWA backend only supports uniform-batch CUDA graphs. Without
  breakable CUDA graphs, default `FULL_AND_PIECEWISE` dies at capture"*) [same src].

```bash
# shape the ladder to the batch sizes you actually serve
vllm serve <model> \
  --compilation-config '{"cudagraph_mode":"FULL_AND_PIECEWISE","cudagraph_capture_sizes":[1,2,4,8,16,32,64,128,256]}'
```
[flag semantics: `cudagraph_capture_sizes` is documented on
[src](https://docs.vllm.ai/en/latest/design/torch_compile/) (with the example `[1, 2, 4, 8]`);
`cudagraph_mode` and `--max-cudagraph-capture-size` are documented in
[inference-engines.md §2.1, §3.6](../cross-cutting/inference-engines.md) — ⚠️ the torch.compile
design page as fetched 2026-09-19 does **not** carry `cudagraph_mode`]

**Decision rule.** Capture the sizes your router actually produces. If your continuous batcher
rarely exceeds 256, capturing to 8190 buys padding waste at the cost of startup minutes. The
trade-off is real though: a batch size above the top of the ladder falls back to eager/piecewise
execution for that step, so a short ladder trades a one-time startup saving for a recurring tail
latency on unusual batches. **Measure the batch-size histogram first** — and note this is a
throughput/utilization question the serving document owns, not one to settle here.

### 3.3 Autotune and JIT warm-up: skip it, or ship it

Three distinct runtime-compilation sources, and this repo's own recipes already take a position on
all three:

1. **FlashInfer autotune.** Both the Kimi-K3 B300 and H100 vLLM recipes pass
   `--no-enable-flashinfer-autotune`
   ([inference-engines.md §6.3](../cross-cutting/inference-engines.md)), and the verified
   DeepSeek-V4.1-Flash 1P1D GB200 layout *"disable[s] FlashInfer autotune, JIT and CuTeDSL warmup
   via `--kernel-config`"*
   ([serving-optimizations.md](../cross-cutting/serving-optimizations.md)). The trade-off is
   explicit: you trade a possibly-better kernel selection for a shorter, more deterministic
   startup.
2. **FlashInfer JIT.** Rather than disabling, you can **pre-build**: FlashInfer ships prebuilt
   kernel caches that *"eliminate compilation and downloading overhead at runtime"*
   [src](https://docs.flashinfer.ai/installation.html):
   ```bash
   pip install flashinfer-jit-cache --index-url https://flashinfer.ai/whl/cu129   # cu130 / cu134
   flashinfer show-config     # prints cubin status, module compilation status, artifact paths
   ```
   Bake that wheel into the serving image and the cache is pre-pulled with the image (§5.2).
3. **DeepGEMM.** `VLLM_DEEP_GEMM_WARMUP=skip` is in the verified DS-V4.1 disaggregated layout
   ([serving-optimizations.md](../cross-cutting/serving-optimizations.md)). Where you do **not**
   skip it, point `DG_JIT_CACHE_DIR` at persistent NVMe; its multi-path form
   (`/opt/prebuilt-dj:/var/cache/dj`) is purpose-built for a read-only baked-in cache with a
   writable overlay [src](https://github.com/deepseek-ai/DeepGEMM).

**Decision rule.** Skip autotune in production (determinism > a few percent of kernel quality);
**ship** JIT caches rather than skipping JIT (there is no quality trade-off, only a packaging
one).

### 3.4 Shipping caches between replicas

ModelExpress's `MX_ARTIFACT_TRANSFER=1` transfers *"compatible Triton, DeepGEMM, TileLang, CuTe DSL,
and FlashInfer caches"* between replicas over RDMA, verifies them, and installs them into the
target's filesystem cache — *"they are not loaded into GPU memory"*
[src](https://github.com/ai-dynamo/modelexpress). This is the strongest available answer to "how do
I get the cache onto a node that has never run this model", and its measured worth on that
benchmark was **7 m → 1 m 44 s**, i.e. bigger than the weight-transfer win.

The low-tech alternative, which vLLM endorses: generate the cache once, and mount it read-only
everywhere — *"directly copy the whole `~/.cache/vllm/torch_compile_cache` directory in your
deployment scenario"*, and for autoscaling *"generate the cache directory once and share it among
instances"* [src](https://docs.vllm.ai/en/latest/design/torch_compile/). A DaemonSet that
pre-populates `/var/cache/vllm/<model>-<dtype>-<gpu>-<engineversion>/` on every node is ~20 lines
of YAML (§7.5) and needs no new infrastructure.

**⚠️ Caution:** vLLM has a **closed** bug report titled *"Launching multiple vLLM processes at the
same time doesn't work well with vLLM's compile cache"*
([vllm#24601](https://github.com/vllm-project/vllm/issues/24601), opened 2025-09-10, **closed** as
of 2026-09-19 — corrected here; an earlier draft called it open). Parallel processes wrote compiled
artifacts into identical cache directories and clobbered each other; the proposed fixes were atomic
artifact files or read/write locks. **⚠️ Which fix landed, and in which vLLM release, was not
established here** — so keep the mitigation regardless: a shared cache should be **read-only** at
serving time with writes going to a per-pod overlay, or two simultaneously-starting replicas can
corrupt each other's cache population.

### 3.5 Warm-up request sets

The engine reporting ready is not the same as the engine being fast. Two distinct reasons:

- **Lazy kernel materialization.** vLLM states *"all the compilation finishes before we serve any
  requests"* [src](https://docs.vllm.ai/en/latest/design/torch_compile/), which covers
  `torch.compile` — but not every JIT path in every backend library. The vLLM sleep-mode benchmarks
  show the residue concretely: *"Without Warm-Up, every Wake-Up Pays Compilation Cost"*, measured as
  a **5.8× and 7.1× slower first inference** in the two ablation cases
  [src](https://vllm-project.github.io/2025/10/26/sleep-mode.html).
- **Allocator and cache state.** The first request also populates the prefix cache and forces the
  allocator into its steady-state shape.

**A warm-up set should exercise the shapes your traffic has**, not one token:

| Probe | Why |
|---|---|
| Short prompt, few tokens out | smallest captured batch size, decode path |
| Long prompt at your p90 input length | chunked-prefill path at the real `--max-num-batched-tokens` |
| A prompt at `max_model_len` bounds (or near) | the long-context attention kernel |
| Several concurrent requests spanning the capture ladder | forces the graph replay path at >1 batch |
| For Marlin-2B: a real video clip at 2 fps | the vision encoder path, which the text warm-up never touches ([marlin2b/architecture.md](../models/marlin2b/architecture.md)) |
| With DSpark/MTP on: enough tokens to exercise the draft head | the speculative path is a separate kernel set ([serving-optimizations.md](../cross-cutting/serving-optimizations.md)) |

**Do this before readiness goes green, not after** — §7.2 shows how (a startup probe that only
passes once warm-up has run).

⚠️ **TO BE VERIFIED:** no measurement exists in this tree of how much warm-up the five repo models
actually need. The **Route179 DGX Spark GB10** breakdown (not AWS's EKS one — misattributed in an
earlier draft, corrected 2026-09-19) lumps *"profiling/warmup"* at **98–136 s** for a 35 B MoE
[src](https://route179.dev/2026/06/30/optimizing-vllm-cold-start-with-model-streaming-and-compile-caching/)
— a suspiciously large number, and one measured on a **unified-memory GB10 desktop part**, not a
discrete-HBM datacentre GPU, so its transferability to B300 is itself ⚠️. §8 carries it as a ⚠️
band.

---

## 4. Snapshot and restore

### 4.1 `cuda-checkpoint` and CRIU

NVIDIA's `cuda-checkpoint` exposes checkpoint/restore for CUDA state and combines with CRIU to
checkpoint a whole CUDA process
[src](https://github.com/NVIDIA/cuda-checkpoint/blob/main/README.md). Its state machine is four
actions — `lock`, `checkpoint`, `restore`, `unlock` — plus a `toggle`:

```
cuda-checkpoint --get-state --pid <pid>
cuda-checkpoint --action lock|checkpoint|restore|unlock --pid <pid> [--timeout <ms>]
cuda-checkpoint --toggle --pid <pid>
```

Driver-gated feature ladder, quoted [same src]:

| Driver | Adds |
|---|---|
| 550+ | baseline utility support |
| 570 | NVML support; **integration with CRIU 4.0+, providing process-tree support**; CUDA Driver interfaces at parity; separate `lock` command with timeout |
| 580 | **GPU migration**; container partial passthrough |
| 595 | ARM CPU support |
| 610 | `cuIpcGetMemHandle`-based **CUDA IPC support** |

**Documented limitations** [same src]: does not support **UVM memory** or IPC memory created with
`cuMemExportToShareableHandle()`; *"waits for already-submitted CUDA work to finish before
completing a checkpoint"*; and *"does not attempt to keep the process in a good state if an error
… is encountered during checkpoint or restore."* Community reporting adds that MIG and MPS are
also unsupported, and that CRIU's own CUDA plugin has historically refused multi-GPU checkpointing
outright, with cross-GPU transports being the blocker
([DevZero on CRIUgpu](https://www.devzero.io/blog/gpu-container-checkpoint-restore),
[NVIDIA/nvcf#1183](https://github.com/NVIDIA/nvcf/issues/1183) — **⚠️ secondary sources**; the
authoritative statement is the one below).

**The authoritative statement for this repo's TP replicas**, from the Foundry evaluation:
*"CUDA-checkpoint does not support the IPC memory required by communication kernels (e.g., DeepEP),
and its restore latency grows disproportionately for multi-GPU data-parallel engines, making it
less efficient than launching multiple independent single-GPU instances"* — so the authors
*"compare with it only on single-GPU settings"*, and note separately that it *"doesn't even support
expert parallelism"* [src](https://arxiv.org/html/2604.06664v1).

**That rules it out for DeepSeek-V4.1-Flash (TP4/TP2 + EP) and Kimi-K3 (TP8) today.** It remains
viable for the two single-GPU models.

Measured, single-GPU, on H200 (vLLM's weights and KV released first via sleep mode so the image
does not contain the full 141 GB) [same src]:

| | Restore time | Image size |
|---|---:|---:|
| CUDA-checkpoint + CRIU | **5.7–6.1 s** (4.9–7.9× faster than vLLM cold init) | 3.7–6.6 GB |
| Foundry (§4.3) | **1.3–2.3 s** (2.6–4.4× faster than CUDA-checkpoint) | 1.1–2.2 GB |

### 4.2 Platform snapshotting: Modal, Cerebrium, ServerlessLLM

**Modal GPU memory snapshots** (alpha, announced 2025-07-30) capture *"device memory contents (GPU
vRAM), such as model weights, CUDA kernels, CUDA objects, like streams and contexts, memory mappings
and their addresses"*, requiring NVIDIA drivers in the **570/575 branches**
[src](https://modal.com/blog/gpu-mem-snapshots). Published results:

| Workload | Cold | With GPU snapshot |
|---|---:|---:|
| Parakeet | ~20 s | **~2 s** (P0) |
| ViT with `torch.compile` | 8.5 s | **2.25 s** (P0) |
| **vLLM Qwen2.5** | **45 s** | **5 s** (P0) |

Enabled with `experimental_options={"enable_gpu_snapshot": True}` alongside
`enable_memory_snapshot=True` [same src]. Modal's CPU-only memory snapshots report *"cold starts
reduced by an average of 71 %… and by as much as 88 % on vLLM"*
[src](https://modal.com/blog/mem-snapshots). Multi-GPU support and CUDA-graph handling are **not
addressed** in the published material ⚠️.

**vLLM's own tracking issue for this capability is
[vllm#33930](https://github.com/vllm-project/vllm/issues/33930)** — i.e. it is a feature request in
the engine, not a shipped feature, as of 2026-09-19. ⚠️ Status not re-verified.

**ServerlessLLM** (arXiv 2401.14351) is the academic reference for loading-optimized checkpoints +
multi-tier caching + live migration for serverless LLM inference. **⚠️ Its numbers were not fetched
for this document** (search budget exhausted); cited here as the right prior art to read, not as a
source of figures. The same applies to **InferX** and **HydraServe**
([arXiv 2502.15524](https://arxiv.org/pdf/2502.15524)).

### 4.3 Foundry: snapshot the *graphs*, not the process

The most relevant 2026 result, because it targets exactly the term that P2P weight loading leaves
behind. Foundry persists CUDA-graph **topology and execution context** offline and reconstructs
executable graphs online, rather than checkpointing the whole process
[src](https://arxiv.org/html/2604.06664v1). Evaluated on 8×H200 (and 8×B200), NVIDIA driver 590.48,
CUDA 13.1, **vLLM v0.11.2**, PyTorch 2.9, 512 captured graphs covering batch 1–512, with the
`torch.compile` cache warm and weight-loading time excluded:

| Configuration | vLLM w/ graphs | Foundry | Reduction |
|---|---:|---:|---:|
| Qwen3-14B DP1–DP8 | 36–48 s | **1.7–1.8 s** | 95 % |
| Llama3-8B | 28 s | **1.3 s** | 95 % |
| Gemma3-12B | 45 s | **2.0 s** | 95 % |
| Qwen3-30B-A3B EP2–EP8 | 112–154 s | **2.7–2.8 s** | 97–98 % |
| **Qwen3-235B-A22B EP8** | **650 s** | **3.9 s** | **99 %** |

Two design points that matter for TP/EP deployments — and that distinguish it from process
checkpointing:

1. **Single-GPU capture, multi-GPU deploy.** *"Foundry exploits this invariance by performing SAVE
   on a single GPU with dummy communication, and then reconstructing rank-specific graphs during
   LOAD by patching in the actual communication handles and rank identifiers… reducing archive
   storage proportionally, e.g., by 64× for a 64-GPU cluster"* [same src]. Foundry's Qwen3-235B EP8
   archive is **2.2 GB total** (1.4 GB of it kernel binaries), vs 3.7 GB for a single-GPU
   CUDA-checkpoint image of a 14 B model.
2. **No throughput regression.** TPOT curves for Foundry-restored graphs and natively captured ones
   *"overlap almost perfectly"* across DP1–4, DeepGEMM custom kernels, sharded large models, and
   both H200 and B200 [same src].

**Maturity caveat, stated plainly:** Foundry is a research prototype (open-sourced at
`https://github.com/foundry-org/foundry`) with a vLLM integration, **not** a vLLM feature, and it
was not evaluated on any of this repo's five models or on B300. Its relevance here is (a) it
quantifies the graph-capture term better than any other source, and (b) it says the ceiling on
graph-capture elimination is ~2–4 s, not zero. ⚠️ Do not plan a 2026 deployment on it; do plan on
capture costing tens of seconds unless you cap the ladder.

### 4.4 vLLM sleep mode — the practical middle ground

Not a snapshot, but the same economics for **multi-model** nodes: keep the process, allocator, CUDA
graphs and JIT-compiled kernels alive while releasing weights and/or KV
[src](https://vllm-project.github.io/2025/10/26/sleep-mode.html). Measured on A100 and A4000,
vLLM 0.11.0:

| Model | Cold start | Level-1 wake | Level-2 wake |
|---|---:|---:|---:|
| Qwen3-0.6B | 37.6 s | **0.26 s** (145×) | 0.85 s (45×) |
| Phi-3-vision-128k | 58.1 s | **0.82 s** (71×) | 2.58 s (23×) |

First-inference latency after waking is also **81–88 %** better than after a cold start (83 % for
Qwen3-0.6B, 81 % for Phi-3-vision on A4000) [same src]. The ratios in the table above are computed
here from the blog's own cold/wake columns; the blog's *own* headline speedups are stated as
**18–20× for Level 1 and 23–45× for Level 2** on model switching, which includes the sleep leg and
so is the more conservative figure to quote. Level 1 offloads weights to CPU RAM (fast wake, needs
the RAM); Level 2 discards them (slower wake, minimal RAM). API:

```bash
vllm serve <model> --enable-sleep-mode
export VLLM_SERVER_DEV_MODE=1          # required; "should only be exposed in trusted networks"
# POST /sleep?level=1|2 ; POST /wake_up ; POST /collective_rpc (L2) ; POST /reset_prefix_cache (L2)
```

**Applicability to this repo.** Level 1 requires host RAM ≥ weights. `p6-b300.48xlarge` has **4 TB**
[src](https://aws.amazon.com/blogs/aws/accelerate-large-scale-ai-applications-with-the-new-amazon-ec2-p6-b300-instances),
so Level-1 sleep is *memory-feasible* for DeepSeek-V4.1-Flash (510 GB) and even Kimi-K3 (1.56 TB)
— though at 1.56 TB you have consumed 39 % of host RAM to hold a sleeping model, and the wake is a
1.56 TB host→device copy, not a 0.26 s pointer swap. **⚠️ No published sleep-mode measurement
exists at this scale**; the blog's 18–45× headline is from 0.6 B and 3.8 B models on A100/A4000
(an earlier draft of this document said "18–200× … from 0.6–235 B models" — **corrected
2026-09-19**, no 235 B row and no 200× figure appears in the cited post). Estimated Kimi-K3 Level-1
wake = 1,560.9 GB ÷ ~50 GB/s aggregate H2D ≈ **31 s** `est.` ⚠️ — still 5–10× better than a cold
start, but not "instant."

The independent cross-check: the MLSys paper reports its per-stage predictor *"can be used to
estimate the performance of fast model re-initialization mechanisms such as vLLM's sleep mode, and
that its estimates match the performance trends reported by the vLLM developers"*
[src](https://arxiv.org/html/2606.07362v3).

### 4.5 Maturity summary

| Technique | Single GPU | TP2–TP8 | EP | This repo's verdict |
|---|---|---|---|---|
| `cuda-checkpoint` + CRIU | ✅ 5.7–6.1 s restore | ❌ no IPC memory; restore latency grows disproportionately | ❌ unsupported | Marlin-2B / Qwen3.8-27B only |
| Modal-style platform snapshot | ✅ measured | ⚠️ unaddressed | ⚠️ unaddressed | not available on bare metal; read as a target |
| Foundry (graph templates) | ✅ | ✅ (design point) | ✅ (design point) | research prototype; best evidence of the ceiling |
| vLLM sleep mode | ✅ | ✅ (works with TP/PP/EP per the blog) | ✅ | **the practical option today** for multi-model and for keeping a warm standby cheap |
| SGLang weight-cache daemon | ✅ | ✅ TP+PP | ❌ future work | blocked by the quantization allowlist for DS/K3 (§2.5) |

---

## 5. Pre-warming strategies

### 5.1 Warm pools and standby replicas

The only technique that makes cold start *structurally* invisible: never be on the critical path.

**Sizing rule.** A warm pool absorbs the ramp that arrives while a new replica boots:

```
required_standby_capacity ≥ peak_arrival_ramp_rate (req/s per second) × cold_start_p95 (s)
                              × service_time_per_request (s)          # = concurrent seats needed
n_standby = ceil( required_standby_capacity / capacity_per_replica )
```

with `capacity_per_replica` = the max concurrency at your context, which
[METHODOLOGY §3](../METHODOLOGY.md#3-fit) already defines and
[fit-matrix.md](../matrix/fit-matrix.md) already tabulates per (model, GPU). The trade-off is a
straight cash one: a standby B300 replica costs the same as a serving one.

**Worked cost, DeepSeek-V4.1-Flash TP4 on B300** (2 replicas per node — the shape
[deepseek41f/README.md](../models/deepseek41f/README.md) recommends for interactive serving): one
standby replica = 4 GPUs. At `planning price · b300 · low` ($7.40,
[cloud-pricing.md §5.14](../cross-cutting/cloud-pricing.md)) and AWS `p6-b300.48xlarge` on-demand
($17.802/GPU-hr, [cloud-pricing.md §3.1](../cross-cutting/cloud-pricing.md) — the most expensive
published B300 row, used here as the ceiling; §5.14's `· high` is OCI at $15.00), that is
**$29.60–$71.21 per hour of standby**, ~$21.3k–$51.3k/month at 720 h. Whether that is
cheaper than the SLO violation is a business question, but the number should be stated before
anyone calls pre-warming "free."

**Cheaper variants, in increasing order of risk:**

1. **Sleeping standby** (§4.4) — a replica that has paid every cold-start stage but released
   weights to host RAM. Costs the same GPU but can host a *different* model meanwhile.
2. **Over-provisioned `min_replicas`** — the blunt instrument, and the only one that also solves
   the "no P2P source exists at zero replicas" problem (§2.4).
3. **Warm process pool** — pre-spawned Python workers that have imported the engine but hold no
   model-specific GPU state; Foundry describes exactly this as standard practice, noting
   environment init *"is modest (typically under 10 s) and can largely be removed from the critical
   path using pre-warmed workers"* [src](https://arxiv.org/html/2604.06664v1). Removes stage 3 only
   — worth ~10–21 s (§1.3), not the minutes.
4. **Scale-to-zero with an honest SLO** — acceptable only for Marlin-2B-class models, where the
   whole budget is under a minute.

### 5.2 Pre-pulled images and lazy pulls

Serving images for this stack are large (CUDA + PyTorch + engine + FlashInfer + DeepGEMM), and the
image pull is the one stage a **DaemonSet or a `pause`-image pre-puller removes entirely**. Three
families of mitigation, in order of how much machinery they need:

| Technique | Mechanism | Evidence |
|---|---|---|
| **Pre-pull** (DaemonSet pulling the serving image on every GPU node) | image is already in containerd's content store when the pod schedules | zero new infrastructure; strictly the first thing to do |
| **Lazy pull — eStargz / stargz-snapshotter** | seekable, backward-compatible layer format; container starts against an index | "even the simplest lazy-pulling setup … delivers a 10× improvement in total cold-start time" ⚠️ secondary source [src](https://blog.zmalik.dev/p/lazy-pulling-container-images-a-deep) |
| **Lazy pull — SOCI (AWS)** | external **zTOC** index storing periodic zlib-decompressor state, stored as a separate OCI artifact via the Referrers API so *"the original image stays completely unmodified"*; one zTOC per layer, layers below `--min-layer-size` skipped [src](https://github.com/awslabs/soci-snapshotter/blob/main/docs/getting-started.md) | SOCI's **parallel-pull** mode (for images that will be read in full anyway — which describes an AI image) measured "roughly 60 % off a 10 GB image" ⚠️ secondary |
| **Lazy pull — Nydus** | chunk-based content-addressable filesystem over RAFS, with P2P | [src](https://github.com/containerd/nydus-snapshotter) |
| **Managed image streaming** | GKE Image Streaming cut a 5.4 GB Triton image's start from **191 s to 30 s** ⚠️ secondary [src](https://blog.zmalik.dev/p/lazy-pulling-container-images-a-deep) | not applicable on bare metal |

⚠️ **Caveat worth taking seriously**: lazy loading moves I/O from startup into the *request path*,
and at least one practitioner report argues this degrades AI inference specifically, because the
image is read in full anyway
([Tensorfuse](https://tensorfuse.io/docs/blogs/lazy_loading_performance_degradation)). For a
bare-metal cluster with a fixed node set, **pre-pulling beats lazy-pulling**: you control the nodes,
the image set is small, and a DaemonSet costs nothing at serving time. Reserve SOCI/Nydus for
elastic cloud capacity where nodes appear without warning.

### 5.3 Pre-staged weights on every node's NVMe

This repo already has the right primitive: [`common/download.sh`](../../common/download.sh) tries
**S3 first, Hugging Face second**, lands in `$WEIGHTS_ROOT/$EXP` (default `/mnt/nvme`, "instance-store
NVMe on a p6 node"), and pre-checks free space **in bytes** before starting. Turning it into a
pre-warmer is a scheduling change, not a code change:

```yaml
# DaemonSet: stage the weight set on every GPU node's NVMe before any serving pod needs it
apiVersion: apps/v1
kind: DaemonSet
metadata: {name: weight-prefetch, namespace: inference}
spec:
  selector: {matchLabels: {app: weight-prefetch}}
  template:
    metadata: {labels: {app: weight-prefetch}}
    spec:
      nodeSelector: {nvidia.com/gpu.product: NVIDIA-B300}
      priorityClassName: weight-prefetch-low     # must NOT preempt serving pods
      containers:
        - name: prefetch
          image: <repo-tooling-image>            # needs s5cmd + huggingface_hub
          command: ["/bin/bash","-c"]
          args:
            - |
              set -euo pipefail
              for exp in qwen3827b marlin2b deepseek41f; do
                DEST=/mnt/nvme/$exp ./$exp/download.sh
              done
              sleep infinity                      # keep the DaemonSet pod alive as a marker
          env:
            - {name: S3_BUCKET, valueFrom: {secretKeyRef: {name: weights-s3, key: bucket}}}
            - {name: WORKERS, value: "64"}        # download.sh passes this to s5cmd --numworkers
          volumeMounts: [{name: nvme, mountPath: /mnt/nvme}]
      volumes: [{name: nvme, hostPath: {path: /mnt/nvme, type: Directory}}]
```

Three sizing facts that constrain the policy:

- `p6-b300.48xlarge` has **8 × 3.84 TB = 30.7 TB** of instance store
  [src](https://aws.amazon.com/blogs/aws/accelerate-large-scale-ai-applications-with-the-new-amazon-ec2-p6-b300-instances).
  All five checkpoints total **2,659.5 GB** (5.444 + 55.56 + 510.29 + 527.27 + 1,560.9, from
  [METHODOLOGY §8](../METHODOLOGY.md#models)) — **8.7 % of the node's NVMe.** Staging *everything*
  on *every* node is affordable here. That is a genuinely unusual luxury and it should be used.
- Instance store is **ephemeral**: it does not survive a stop/start. The DaemonSet must be
  idempotent and re-run on node join, which `download.sh`'s existing structure already supports.
- `download.sh`'s free-space check demands `SIZE_GB + 20` GB, compared **in bytes** on purpose (the
  script's own comment: mixing decimal GB with `df -BG`'s GiB "silently over-demands ~7 %"). Keep
  that property if you rewrite it for Kubernetes.

**Decision rule.** Pre-stage on NVMe when `(checkpoint_bytes × nodes) / staging_bandwidth` is
cheaper than the aggregate cold-start delay you would otherwise pay, which for a fixed bare-metal
fleet it essentially always is. Pre-stage on **S3 only** when the model is rarely served and the
extra 2.9 min (§8) of pull is acceptable.

### 5.4 Predictive pre-warm from the forecaster

The autoscaling document in this folder owns the forecaster; this section owns the **interface**
between it and cold start, which is one number and one inequality.

Dynamo's SLA Planner is the concrete reference implementation: it *"observes system metrics,
predicts future load, and adjusts prefill/decode worker replica counts to proactively meet SLA
targets"*, with `load_predictor` ∈ {`arima` (default), `prophet`, `kalman`, `constant`}, forecasting
`next_num_req`, `next_isl` and `next_osl` for the coming interval
[src](https://docs.nvidia.com/dynamo/v1.3.0/components/planner). Its defaults:

| Key | Default |
|---|---:|
| `load_predictor` | `"arima"` |
| `throughput_adjustment_interval_seconds` | **180** |
| `load_adjustment_interval_seconds` | 5 |
| `load_scaling_down_sensitivity` | 80 |

**The inequality that matters:**

```
forecast_horizon  ≥  cold_start_p95  +  adjustment_interval
```

Dynamo's own docs acknowledge the constraint — *"Spinning up a GPU worker takes minutes, not
seconds"* — but, as fetched, **describe no configuration parameter that accounts for scaling lead
time** [same src]. That is the gap to close operationally: if your DeepSeek-V4.1-Flash cold start is
~2–3 min (§8) and the planner adjusts every 180 s on a forecast of the *next* interval, the replica
arrives roughly one interval late, and a naïve controller will have already scaled again —
classic oscillation. Two mitigations:

1. **Forecast further ahead than you boot.** Set the horizon to ≥ `cold_start_p95 + interval`, and
   accept the forecast-error cost of looking further out.
2. **Make the boot shorter than the interval**, via §2.4 + §3.4 (P2P weights + artifact transfer
   took an 8 m 1 s start to 1 m 44 s [src](https://github.com/ai-dynamo/modelexpress)), so the
   controller's natural cadence is no longer shorter than the actuator's latency.

The MLSys predictor (§1.2) is the missing third piece: a per-stage model that gives the autoscaler a
*current, hardware-specific* `cold_start_p95` rather than a hard-coded guess
[src](https://arxiv.org/html/2606.07362v3).

### 5.5 Over-provisioned min replicas vs a cold-start SLO

| Policy | p99 TTFT during a 2× ramp | Cost | When to pick it |
|---|---|---|---|
| Scale to zero | cold start fully visible: **+2–11 min** on the unlucky requests (§8) | lowest | dev/eval only; or Marlin-2B where the number is ~35–55 s |
| `min_replicas = 1`, reactive scale-up | first ramp partly visible | 1 replica | anything with P2P (§2.4) — the 1 replica is also the weight *source* |
| Warm standby sized by §5.1 | invisible if the sizing rule holds | +N replicas | production interactive (S1/S2 in [METHODOLOGY §6](../METHODOLOGY.md#6-cost)) |
| Predictive pre-warm (§5.4) | invisible for *predictable* load; reactive fallback for surprises | between the two | production with diurnal traffic — the common case |

**Recommendation for this repo**: predictive pre-warm *plus* a small warm standby for forecast
error, with `min_replicas ≥ 1` per model so the P2P path always has a source. The decision rule for
the standby size is §5.1's inequality; the trade-off is that every replica of forecast error costs a
full replica-hour whether or not traffic arrives.

---

## 6. Rolling updates and model version swaps

A rollout is a cold start you scheduled, which means every §1–§5 technique applies — plus one
constraint the autoscaler does not have: **you must not lose in-flight requests, and you may not
have spare GPUs to surge into.**

### 6.1 Blue/green with drain

llm-d's rollout guide names three strategies and, importantly, states the LLM-specific risk
explicitly: *"Long-running requests cannot be migrated, new pods require warm-up time for model
weights and CUDA graphs, and GPU memory is expensive"*
[src](https://llm-d.ai/docs/dev/operations/rollouts).

| Strategy | Routing control | Rollback | Resource cost |
|---|---|---|---|
| Rolling update (e.g. 25 % at a time) | random across healthy pods | slow (reverse pod creation) | low temporary surge |
| **Blue/green** (second complete InferencePool + HTTPRoute weights 1 % → 5 % → 10 % → 50 % → 100 %) | precise percentages | **instant** (flip the weight) | high — dual environments |
| LoRA adapter rollout (`InferenceModelRewrite`) | per-adapter | fast | none |

[same src]

**The GPU-scarcity trade-off is the whole decision.** Blue/green for Kimi-K3 means a **second entire
8×B300 node**; for DeepSeek-V4.1-Flash TP4 it means 4 more GPUs. On a fixed bare-metal cluster,
blue/green is affordable for the small models and a capacity-planning event for the large ones. The
rolling-update alternative — with `maxSurge: 0, maxUnavailable: 1` when there is no spare capacity —
means you run degraded during the rollout, so it must be scheduled against a traffic trough, and
the cold start of each replacement is fully on the critical path.

### 6.2 Draining KV without dropping requests

llm-d documents the two-layer drain precisely [src](https://llm-d.ai/docs/dev/operations/graceful-shutdown):

- **Routing layer (EPP):** stops sending new requests to the terminating pod and *"evicts queued
  requests with a retryable 503 Service Unavailable"* — retryable, so the client's next attempt
  lands on a healthy pod.
- **Model-server layer:** without a shutdown timeout, vLLM *"immediately aborts in-flight requests
  and exits."* With `--shutdown-timeout N`, it *"catches SIGTERM and continues serving the currently
  running requests for up to N seconds."*
- **The invariant:** *"`terminationGracePeriodSeconds` must be greater than `--shutdown-timeout`
  (plus a small buffer for the `preStop` hook and process cleanup)."*

```yaml
apiVersion: v1
kind: Pod
metadata: {name: vllm}
spec:
  terminationGracePeriodSeconds: 120      # > shutdown-timeout + preStop + cleanup
  containers:
    - name: vllm
      args: ["--shutdown-timeout", "90"]  # ≈ p99 acceptable drain duration
      readinessProbe:
        httpGet: {path: /v1/models, port: 8000}
        periodSeconds: 5
```
[same src]

**Tuning rule:** set `--shutdown-timeout` ≈ the p99 *request* duration you are willing to wait for,
and `terminationGracePeriodSeconds` with headroom above it. For this repo that varies enormously by
scenario — an S1 request (4K in / 512 out, TPOT ≤ 50 ms → ~26 s of decode) drains inside 90 s; an
S3 request (128K in / 2K out, [METHODOLOGY §6](../METHODOLOGY.md#6-cost)) may not. **A long-context
model needs a longer grace period than the llm-d default example**, and the cost of getting it wrong
is dropped 128K-context requests that have already burned their prefill.

⚠️ Prefix-cache and KV state on the draining pod are **lost**, not migrated: the replacement pod
starts with an empty prefix cache, so the *effective* cost of a rollout includes the re-warming of
the cache, not just the process start. [serving-optimizations.md
§1.5](../cross-cutting/serving-optimizations.md) quantifies what a cache hit is worth; multiplying
that by the miss window is left as an ⚠️ open question, since no source fetched here measures
post-rollout cache re-warm time.

### 6.3 Weight hot-swap and LoRA hot-load

- **Same architecture, new weights.** Because the vLLM compile cache is invalidated by *model
  architecture, dtype, GPU type and engine version* but **not** by weight values
  [src](https://route179.dev/2026/06/30/optimizing-vllm-cold-start-with-model-streaming-and-compile-caching/),
  swapping a fine-tune of the same base skips the §3 term entirely — you pay §2 (the weight bytes)
  and nothing else. Sleep-mode Level 2 plus `POST /collective_rpc` is the shipped mechanism for
  reloading weights in place [src](https://vllm-project.github.io/2025/10/26/sleep-mode.html).
- **LoRA hot-load**, the cheapest version swap available:
  ```bash
  vllm serve <base> --enable-lora --max-lora-rank 64
  export VLLM_ALLOW_RUNTIME_LORA_UPDATING=True
  # POST /v1/load_lora_adapter   ; POST /v1/unload_lora_adapter
  ```
  vLLM warns this *"comes with security risks"* and *"should not be used in production unless it is
  an isolated, fully trusted environment"*; `load_inplace` replaces an adapter's weights under the
  same name [src](https://docs.vllm.ai/en/latest/features/lora/). AIBrix builds a control plane on
  exactly this, *"enabling dynamic loading and unloading of LoRA adapters… reusing base models
  across different tasks while swapping in lightweight LoRA adapters"*
  [src](https://arxiv.org/pdf/2504.03648). ⚠️ AIBrix figures are from search snippets of the paper,
  not a fetched full text.
- **Choosing `--max-lora-rank` too high "wastes memory and can cause performance issues"**
  [src](https://docs.vllm.ai/en/latest/features/lora/) — and it is fixed at server start, so it is
  a cold-start-time decision with steady-state consequences.

### 6.4 In-place engine restarts

The case the §2.5 mechanisms exist for: restarting the engine process (config change, engine
upgrade, leaked memory) **on a node whose GPUs already hold the weights**. SGLang's daemon takes
this from ~495 s to ~0.63 s for a 1 T model [src](https://www.lmsys.org/blog/2026-08-21-sglang-fast-recovery);
vLLM's `--load-format ipc_cache` targets the same case
[src](https://docs.vllm.ai/en/latest/api/vllm/config/load/). Note the daemon's fingerprint includes
**torch version and device capability**, so an *engine upgrade* — the most common reason to restart
— is exactly the case that forces a full disk reload [same src]. The mechanism helps with crashes
and config flips, not with upgrades.

---

## 7. Kubernetes specifics

### 7.1 The one-line summary

Kubernetes' defaults assume containers start in seconds. Every one of these settings exists because
that assumption is false here.

### 7.2 Probes tuned for multi-minute boots

Kubernetes' own guidance: *"If your container usually starts in more than
`initialDelaySeconds + failureThreshold × periodSeconds`, you should specify a startup probe that
checks the same endpoint as the liveness probe"*, and *"Kubernetes does not execute liveness or
readiness probes until the startup probe succeeds"*
[src](https://kubernetes.io/docs/concepts/workloads/pods/probes/). Defaults are `periodSeconds: 10`,
`failureThreshold: 3`, `successThreshold: 1` [same src] — i.e. **30 seconds** before the kubelet
starts killing your Kimi-K3 pod, which needs an order of magnitude more.

```yaml
# DeepSeek-V4.1-Flash TP4 on B300; budget from §8 is ~2–3 min warm, ~6 min from S3
startupProbe:
  httpGet: {path: /health, port: 8000}
  periodSeconds: 10
  failureThreshold: 90          # 900 s ceiling: ~3× the S3-cold p95 budget
  timeoutSeconds: 5
readinessProbe:                 # only runs after startupProbe succeeds
  httpGet: {path: /v1/models, port: 8000}
  periodSeconds: 5
  failureThreshold: 3
livenessProbe:                  # ditto — never fires during boot
  httpGet: {path: /health, port: 8000}
  periodSeconds: 10
  failureThreshold: 3
```

Sizing rule: `failureThreshold × periodSeconds ≥ 3 × cold_start_p95` for the *slowest supported*
source (S3, cold page cache, cold compile cache). For Kimi-K3 that is ≥ 2,400 s. The engine agrees:
this repo's own Kimi-K3 and DeepSeek recipes both export **`VLLM_ENGINE_READY_TIMEOUT_S=3600`**
([inference-engines.md §6.1, §6.3](../cross-cutting/inference-engines.md)), with the note
*"Long load times are expected."* **Make the startup probe's ceiling and the engine's internal
timeout agree**, or one will kill the pod while the other is still waiting.

Two refinements:

- **Point readiness at a warm-up-gated endpoint.** If `/health` returns 200 the moment the HTTP
  server binds, the pod receives traffic while stage 10 is unpaid, and the first real requests eat
  the 5–7× first-inference penalty (§3.5). Either use an endpoint the engine only greens after
  warm-up, or run the warm-up set from a `postStart`/sidecar and gate readiness on its completion
  marker.
- **`terminationGracePeriodSeconds` on the probe** is distinct from the pod's and can override it
  for liveness-triggered kills [src](https://kubernetes.io/docs/concepts/workloads/pods/probes/).

### 7.3 PodDisruptionBudgets and priority

- **PDB.** With 2 DeepSeek TP4 replicas per node, `minAvailable: 1` per model keeps a voluntary
  disruption (node drain, cluster upgrade) from taking both — remembering that the replacement pays
  a full cold start, so a PDB that allows two simultaneous evictions creates a multi-minute capacity
  hole, not a rolling one.
- **PriorityClass.** Three tiers, and the ordering is the point:

  | Class | Value | Who | Preemptible by |
  |---|---:|---|---|
  | `inference-serving` | 1000000 | live replicas | nothing |
  | `inference-standby` | 100000 | warm-pool replicas (§5.1) | serving pods |
  | `weight-prefetch-low` | 100 | the DaemonSet of §5.3, cache warmers | everything |

  The middle tier is what makes a warm pool safe: standby replicas yield their GPUs to real serving
  demand rather than blocking it, which is the only way a warm pool is not just a permanent 
  capacity tax. Note the catch — **a preempted standby is a standby you no longer have**, so a pool
  sized by §5.1 must be sized on the *post-preemption* count.

### 7.4 Topology-aware volume binding

Weights staged on node-local NVMe are the definition of a topology-constrained volume: a pod that
must run where its data is. Use a local-volume StorageClass with
`volumeBindingMode: WaitForFirstConsumer` so the scheduler picks the node **before** binding the
PV — otherwise the volume binds to a node the scheduler then can't use, and the pod pends forever
or falls back to the slow path. The simpler alternative, used in §5.3's DaemonSet, is a plain
`hostPath` mount of `/mnt/nvme` plus a node label (`weights.example.com/deepseek41f: "staged"`) that
the serving pod requires via `nodeAffinity` — no PV machinery, and the label is set by the
prefetcher when the download completes. ⚠️ No primary source fetched for this subsection; it is
standard Kubernetes practice, stated as such, not as a citation.

### 7.5 DaemonSet cache warmers

Two DaemonSets, both `weight-prefetch-low` priority:

1. **Weights** — §5.3.
2. **Compile/JIT caches** — populate `/var/cache/vllm/<key>/`, `/var/cache/flashinfer/`,
   `/var/cache/dj/` on every node from a canonical build, then mount them **read-only** into
   serving pods, with `VLLM_CACHE_ROOT` (or `DG_JIT_CACHE_DIR`'s `:`-separated multi-path form
   [src](https://github.com/deepseek-ai/DeepGEMM)) pointing a writable overlay second. Read-only
   avoids the concurrent-population hazard of §3.4.

   The cache key must include **model, dtype, GPU arch and engine version**. vLLM documents the key
   as covering the configs, the PyTorch compilation settings and the traced forward source
   [src](https://docs.vllm.ai/en/latest/design/torch_compile/); the practitioner enumeration that
   maps that onto deployable key fields is *"model architecture, layer shapes, data types,
   quantization scheme, GPU compute capability, compile flags, and vLLM/PyTorch versions"*
   [src](https://route179.dev/2026/06/30/optimizing-vllm-cold-start-with-model-streaming-and-compile-caching/).
   Key it wrong and you either miss every time (silent, slow) or hit wrongly (loud, or worse,
   silent).

### 7.6 GPU node readiness

The NVIDIA GPU Operator installs *"the NVIDIA GPU driver, NVIDIA Container Toolkit, NVIDIA Device
Plugin, DCGM Exporter, and MIG Manager as pods on every GPU worker node"*
[src](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html). For
a bare-metal cluster with drivers baked into the host image, install with
`--set driver.enabled=false --set toolkit.enabled=false`, which removes driver-container install
time from every node join [same src]. **The operator's docs do not publish node-readiness
timings** [same src] — ⚠️ measure it on your own nodes; it is a per-node-join cost, not a per-pod
one, so it matters for cluster scale-out, not for replica scale-out.

---

## 8. Worked example: cold-start budget on an 8×B300 node

### 8.1 Assumptions

| Input | Value | Source |
|---|---|---|
| Node | 8× B300, 268 GB/GPU (2,144 GB/node), 192 vCPU, 4 TB RAM, 8× 3.84 TB NVMe | [METHODOLOGY §8](../METHODOLOGY.md#gpus), [AWS p6-b300](https://aws.amazon.com/blogs/aws/accelerate-large-scale-ai-applications-with-the-new-amazon-ec2-p6-b300-instances) |
| Local NVMe → HBM | **10 GB/s** (conservative) and **20 GB/s** (tuned) | task planning band; bracketed by AWS's measured 8–10 GB/s s5cmd/NVMe path [src](https://aws.amazon.com/blogs/containers/fast-model-loading-for-ai-inference-on-amazon-eks/) below and 4×PCIe-5 at 25 GB/s [src](https://arxiv.org/html/2606.07362v3) above |
| S3 → HBM | **3 GB/s** | task planning figure; consistent with AWS's measured 33–39 Gbps (4.1–4.9 GB/s) tuned and 5.96 Gbps stock [src](https://aws.amazon.com/blogs/containers/fast-model-loading-for-ai-inference-on-amazon-eks/) |
| Image | pre-pulled (§5.2) → **0 s** | policy choice |
| Compile/JIT caches | **warm** on node NVMe (§3, §7.5) | policy choice |
| CUDA-graph ladder | 26 sizes for DeepSeek on B300; comparable elsewhere | [inference-engines.md §3](../cross-cutting/inference-engines.md) |
| Autotune | disabled (`--no-enable-flashinfer-autotune`, `VLLM_DEEP_GEMM_WARMUP=skip`) | this repo's own recipes, [inference-engines.md](../cross-cutting/inference-engines.md), [serving-optimizations.md](../cross-cutting/serving-optimizations.md) |

**Every figure in §8.2–8.4 is `est.`** — arithmetic on measured per-stage figures from §1–§3,
applied to this repo's checkpoint sizes. None of the five models has a published end-to-end startup
measurement ([open question 1](#open-questions)).

### 8.2 Weight-load term alone

`t_weights = checkpoint_bytes ÷ effective_rate`, checkpoint bytes from
[METHODOLOGY §8](../METHODOLOGY.md#models) (decimal GB):

| Model | Checkpoint | NVMe @10 GB/s | NVMe @20 GB/s | S3 @3 GB/s | P2P RDMA (§2.4) |
|---|---:|---:|---:|---:|---:|
| Marlin-2B | 5.444 GB | 0.5 s | 0.3 s | 1.8 s | ~1 s ⚠️ |
| Qwen3.8-27B (BF16) | 55.56 GB | 5.6 s | 2.8 s | 18.5 s | ~2 s ⚠️ |
| Qwen3.8-27B (FP8) | 30.87 GB | 3.1 s | 1.5 s | 10.3 s | ~2 s ⚠️ |
| DeepSeek-V4.1-Flash | 510.29 GB | **51.0 s** | **25.5 s** | **170.1 s** | ~11 s ⚠️ |
| DeepSeek-V4.1-Flash-NVFP4 | 527.27 GB | 52.7 s | 26.4 s | 175.8 s | ~11 s ⚠️ |
| Kimi-K3 | 1,560.9 GB | **156.1 s** | **78.0 s** | **520.3 s** | ~35 s ⚠️ |

P2P column: extrapolated from ModelExpress's measured **11 s for DeepSeek-V4-Pro TP8 on 8×B200 with
ConnectX-7** [src](https://github.com/ai-dynamo/modelexpress), whose checkpoint size is not
published — so the column is **⚠️ TO BE VERIFIED**, scaled by bytes against an assumed
DeepSeek-V4-Pro ≈ DeepSeek-V4.1-Flash size. Do not plan on it; measure it.

Two things this table does **not** say, and should:

- **Read amplification.** Without `runai_streamer_sharded` / `sharded_state` /
  `--weight-loader-prefetch-checkpoints`, an N-rank TP job can read N× these bytes from a shared
  source (§2.3). For Kimi-K3 TP8 that is 12.5 TB, not 1.56 TB. Host page cache absorbs it only if
  the checkpoint fits RAM — 1.56 TB against 4 TB does fit, but with 39 % of host RAM committed.
- **`--engram-config '{"cpu_offload":true}'`** on the DeepSeek TP2/H100 shapes keeps ~183 GiB of
  Engram tables in host memory ([inference-engines.md §6.1](../cross-cutting/inference-engines.md)).
  Those bytes still cross NVMe→host, but not host→HBM, so the load term shifts rather than
  disappearing — and the recommended B300 shape is **TP4**, where the tables shard and stay
  resident ([deepseek41f/README.md](../models/deepseek41f/README.md)), so §8.3 budgets the full
  510 GB into HBM.

### 8.3 Full stage budget, warm caches, weights on node NVMe

| Stage | Marlin-2B (1 GPU) | Qwen3.8-27B (1 GPU) | DeepSeek-V4.1-Flash (TP4) | Kimi-K3 (TP8) |
|---|---:|---:|---:|---:|
| 1. Image pull (pre-pulled) | 0 | 0 | 0 | 0 |
| 2. Container + imports + bootstrap | 10–21 s ᵃ | 10–21 s | 10–21 s | 10–21 s |
| 3. Tokenizer + config | < 0.5 s ᵇ | < 0.5 s | ~1 s | **~13 s** ᶜ |
| 4. `torch.distributed` / NCCL init | 0 | 0 | ~5 s ᵈ | ~5 s ᵈ |
| 5. **Weight load @10–20 GB/s** | 0.3–0.5 s | 2.8–5.6 s | **25.5–51.0 s** | **78.0–156.1 s** |
| 6. Compile-cache load (hit) | 3–6 s ᵉ | 3–6 s | 10–20 s ⚠️ | 10–20 s ⚠️ |
| 7. KV profiling + allocation | ~1 s ᶠ | ~1 s | 2–10 s ⚠️ (MoE deviates) | 2–10 s ⚠️ |
| 8. **CUDA-graph capture** | 1–2 s ᵍ | 2–5 s | **26–78 s** ⚠️ ʰ | **26–78 s** ⚠️ ʰ |
| 9. NIXL/IPC registration (if P2P) | — | — | 0.8–8.2 s ⁱ | 0.8–8.2 s ⁱ |
| 10. Warm-up request set | 5–15 s ⚠️ | 5–15 s ⚠️ | 15–40 s ⚠️ | 20–60 s ⚠️ |
| **Total (est.)** | **20–46 s** | **24–54 s** | **94–226 s** | **164–363 s** |
| **Rounded planning figure** | **≈ 35–55 s** | **≈ 50–80 s** | **≈ 2–3 min** | **≈ 3.5–5 min** |

**⚠️ Arithmetic corrections, 2026-09-19.** (a) The Kimi-K3 **Total** low end was printed as 151 s;
summing the low ends of stages 2–10 (NIXL excluded, as for DeepSeek) gives **10 + 13 + 5 + 78.0 +
10 + 2 + 26 + 20 = 164.0 s** — the ~13 s tokenizer stage had been dropped. Recomputed with
`python3`: Marlin-2B **20.3–46.0 s**, Qwen3.8-27B **23.8–54.1 s**, DeepSeek-V4.1-Flash
**94.5–226.0 s (1.57–3.77 min)**, Kimi-K3 **164.0–363.1 s (2.73–6.05 min)**.
(b) The **Rounded planning figure** row is therefore *not* a rounding of the Total row — it is a
judgement band that sits above the low end and below the high end on every model (Marlin 35–55 vs
20–46; Qwen 50–80 vs 24–54; Kimi 3.5–5 min vs 2.7–6.1 min). It is kept because it is the figure
[`01-bare-metal-cluster.md` §5.5](./01-bare-metal-cluster.md) already pins for Kimi-K3 (*"treat
**3–5 minutes** as the planning figure until measured"*), but it should be read as **a padded
planning band, not arithmetic**, until [open question 1](#open-questions) is closed by measurement.
(c) ⚠️ **Contradiction with [`05-autoscaling-and-predictive-scaling.md` §5.3](./05-autoscaling-and-predictive-scaling.md)**,
which carries cold-start estimates of **1–3 min (Marlin-2B), 2–4 min (Qwen3.8-27B), 5–12 min
(DeepSeek-V4.1-Flash), 8–20 min (Kimi-K3)** — 2–4× this document's warm-NVMe figures and above even
its S3-cold ones for the small models. Neither set is measured. **The two documents must converge
on one band**; this document's is built stage-by-stage from cited per-stage measurements (§1–§3)
and is the one to reconcile against.

ᵃ 2–4 s measured for a small vLLM process [src](https://arxiv.org/html/2606.07362v3); **21 s**
measured for a large SGLang container [src](https://fergusfinn.com/blog/fast-sglang-starts/). The
band spans both. vLLM ≥ 0.11's `Model_Class` metadata caching cut one substep from 4.47 s to 0.12 s
[src](https://arxiv.org/html/2606.07362v3).
ᵇ 0.08–0.29 s measured, linear in tokenizer file size [same src].
ᶜ ~13 s measured for Ling-2.6-1T on SGLang [src](https://www.lmsys.org/blog/2026-08-21-sglang-fast-recovery)
— a trillion-class tokenizer/config path, applied to Kimi-K3 by analogy. ⚠️
ᵈ ~5 s measured for 8-rank init [same src].
ᵉ 2.89–5.66 s measured at ≤16 B [src](https://arxiv.org/html/2606.07362v3); scaled up for the large
MoEs by layer count. ⚠️ The **cache-miss** figure is 11–21 s at ≤16 B, so a cold cache on
DeepSeek/Kimi plausibly costs **minutes** — which is why §7.5 exists.
ᶠ 0.67–0.97 s measured; MoE models deviate upward [same src].
ᵍ 0.33 s (3 sizes) – 1.8 s (60 sizes) measured on Llama2-7B [same src].
ʰ **The widest ⚠️ in the table.** Method: the B300 DeepSeek profile captures **26** sizes
([inference-engines.md §3](../cross-cutting/inference-engines.md)); Foundry measures 512 graphs at
650 s for Qwen3-235B-A22B EP8 (≈ 1.27 s/graph) and 112–154 s for Qwen3-30B-A3B EP8 (≈ 0.22–0.30
s/graph) [src](https://arxiv.org/html/2604.06664v1). 26 × (1.0–3.0 s/graph) = 26–78 s, where the
upper end reflects that DeepSeek-V4.1-Flash's forward is expensive — the repo measures
**220–222 ms chunked-prefill steps on B200** ([gpus/b200.md §9.3](../gpus/b200.md)). SGLang's
measured 7.7 s for Ling-2.6-1T at *its* default ladder [src](https://www.lmsys.org/blog/2026-08-21-sglang-fast-recovery)
suggests the low end may be right; the ladder length, not the model, decides.
ⁱ 8.16 s per-tensor default → 1.14 s with `MX_POOL_REG=1` → 0.79 s with `MX_VMM_ARENA=1`
[src](https://github.com/ai-dynamo/modelexpress).

### 8.4 The same budget, cold from S3

Replace stage 5 with the S3 column of §8.2:

| Model | Warm-NVMe total | **S3-cold total** | Delta | Recomputed S3-cold from the §8.3 **Total** row |
|---|---:|---:|---:|---:|
| Marlin-2B | ≈ 35–55 s | ≈ 37–57 s | +1.3–1.5 s | 21.8–47.3 s |
| Qwen3.8-27B (BF16) | ≈ 50–80 s | ≈ 65–95 s | +12.9–15.7 s | 39.5–67.0 s |
| DeepSeek-V4.1-Flash | ≈ 2–3 min | **≈ 4.5–6 min** | +119–145 s | 239.1–345.1 s (**3.98–5.75 min**) |
| Kimi-K3 | ≈ 3.5–5 min | **≈ 11–13 min** | +364–442 s | 606.3–727.3 s (**10.10–12.12 min**) |

The last column (added 2026-09-19, `python3`) substitutes the S3 rate into the §8.3 **Total** row
directly, rather than into the padded planning band. The two big models land within ~10 % of the
printed figures; the two small ones do not, because their planning band is padded by ~15–30 s of
margin the arithmetic does not contain. **Use the planning band for capacity decisions and the
recomputed column for anything that must add up.**

**Reading:** pre-staging weights on node NVMe (§5.3) is worth **2–7 minutes per replica start** on
the two large models and essentially nothing on Marlin-2B. Since staging all five checkpoints
consumes only 8.7 % of the node's 30.7 TB instance store (§5.3), the decision is trivial: **stage
everything, everywhere.** The only reason not to is instance-store ephemerality, which the
DaemonSet handles.

### 8.5 The pre-warm policy that keeps p99 TTFT inside SLO

Take the S1 interactive SLO from [METHODOLOGY §6](../METHODOLOGY.md#6-cost) — 4K in / 512 out,
TPOT ≤ 50 ms, i.e. **512 × 50 ms = 25.6 s** of decode for a whole request. A cold start of 2–5
minutes is **≈ 5–12× the entire request budget** (120 s / 25.6 s = 4.7×; 300 s / 25.6 s = 11.7× —
recomputed 2026-09-19; an earlier draft said "40–100× … misses the SLO by two orders of magnitude",
which is wrong by roughly an order of magnitude itself). Measured against the *per-token* budget it
is 2,400–6,000×, which is the number that actually matters to the client: a request routed to a
booting replica waits minutes for its first token. Either way there is no version of "absorb it in
the tail". The policy therefore has to guarantee a replica is *never* on the critical path, not
merely minimize how often it is.

**Policy, per model:**

| Model | `min_replicas` | Warm standby | Pre-warm trigger | Rationale |
|---|---:|---|---|---|
| Marlin-2B | 1 | 0 (scale reactively) | reactive is fine | 35–55 s boot; a reactive scale-up covers a ramp within one forecast interval |
| Qwen3.8-27B | 1 | 0–1 | reactive + forecast | 50–80 s; a single standby covers forecast error cheaply (1 GPU) |
| DeepSeek-V4.1-Flash | 1 (= P2P seed) | **1 replica (4 GPUs)** | forecast horizon ≥ `cold_start_p95 + 180 s` ≈ **8 min** | 2–3 min boot vs Dynamo's 180 s default interval — without a horizon extension the controller oscillates (§5.4) |
| DeepSeek-V4.1-Flash-NVFP4 | 1 | shares the standby with the base if both are served | as above | same shape, same numbers |
| Kimi-K3 | 1 (a whole node) | **1 node, or accept the SLO break** | forecast horizon ≥ **10 min** | 3.5–5 min warm / 11–13 min cold; a standby node is $59–$142/hr. This is a business decision, not an engineering one |

**The three mechanisms, applied in order:**

1. **Make the boot fit inside the control interval.** Adopt §2.4 (P2P weights) + §3.4 (artifact
   transfer): the one published end-to-end measurement of both together is **8 m 1 s → 1 m 44 s**
   [src](https://github.com/ai-dynamo/modelexpress). At 1 m 44 s, DeepSeek-V4.1-Flash boots *within*
   a single 180 s planner interval, and the oscillation problem disappears without extending the
   horizon. **This is the highest-leverage change in this document.**
2. **Extend the forecast horizon** to `cold_start_p95 + adjustment_interval` for whatever boot time
   remains (§5.4), using ARIMA/Prophet as Dynamo ships
   [src](https://docs.nvidia.com/dynamo/v1.3.0/components/planner).
3. **Hold a standby sized for forecast error only**, not for the whole ramp — §5.1's inequality
   evaluated against the *residual* (unforecast) ramp rate, at `inference-standby` priority (§7.3)
   so it yields to real demand.

**Validation this policy needs before it is trusted:** measure `cold_start_p95` per model on the
real node, per §1.2's predictor methodology, and re-measure after every engine bump — the MLSys
paper's **> 4× variance across nine vLLM releases** [src](https://arxiv.org/html/2606.07362v3) means
a cold-start SLO that is not re-measured per release is not an SLO.

---

## Open questions

All ⚠️ items in this document, consolidated.

1. **No end-to-end cold-start measurement exists for any of this repo's five models.** Every figure
   in §8 is `est.` — arithmetic on measurements taken on *other* models. The fix is cheap and
   should be the first benchmark run: `vllm serve` / `sglang serve` with timestamped stage logs, on
   the real B300 node, per model, ×5 runs, with and without warm caches. The MLSys artifact
   (`https://github.com/upb-cn/vllm-startup-profiler`
   [src](https://arxiv.org/html/2606.07362v3)) automates most of it. **Note its stated limit: the
   published predictor is fitted for non-MoE models, and four of this repo's five are MoE or
   hybrid.**
2. **Loader ranking is storage-dependent and the two published rankings disagree.** The MLSys paper
   finds Tensorizer fastest (53–60 % of safetensors time) with Run:ai only "moderate"
   [src](https://arxiv.org/html/2606.07362v3); Run:ai's own benchmark finds Model Streamer
   decisively faster on S3 (4.88 s vs 37.36 s)
   [src](https://github.com/run-ai/runai-model-streamer/blob/master/docs/src/benchmarks.md). Neither
   tested `instanttensor`, `fastsafetensors` and `runai_streamer` head to head on Blackwell + local
   NVMe, which is this repo's actual case.
3. **InstantTensor's 35–45 GB/s is unreproduced.** It is the fastest storage→HBM figure in any
   source here, published in vLLM's own docs
   [src](https://github.com/vllm-project/vllm/blob/main/docs/models/extensions/instanttensor.md),
   on H200 with undescribed storage. If it holds on B300 + instance-store NVMe it halves §8's weight
   term again.
4. **`p6-b300` local instance-store aggregate throughput is unpublished.** AWS states 8 × 3.84 TB
   but no GB/s [src](https://aws.amazon.com/blogs/aws/accelerate-large-scale-ai-applications-with-the-new-amazon-ec2-p6-b300-instances).
   §8's 10–20 GB/s band is a planning assumption bracketed by other systems' measurements; one `fio`
   run settles it.
5. **The CUDA-graph capture estimate for DeepSeek-V4.1-Flash and Kimi-K3 (26–78 s) spans 3×.** It is
   extrapolated from Foundry's per-graph costs on Qwen3 MoEs
   [src](https://arxiv.org/html/2604.06664v1) against this repo's measured 220–222 ms B200 step time
   ([gpus/b200.md §9.3](../gpus/b200.md)). A single timed capture on the real ladder closes it — and
   it is the second-largest term in the budget.
6. **Compile-cache load time at 500 GB–1.5 TB scale is unmeasured.** §8 carries 10–20 s by
   extrapolating the paper's 2.89–5.66 s at ≤16 B [src](https://arxiv.org/html/2606.07362v3). The
   *cache-miss* case is worse and more important: 11–21 s at ≤16 B implies minutes here, and nothing
   published bounds it.
7. **Warm-up request-set cost is unquantified for these models.** The **Route179 DGX Spark GB10**
   breakdown (*not* AWS's EKS one — misattributed in an earlier draft, corrected 2026-09-19) puts
   "profiling/warmup" at **98–136 s** for a 35 B MoE on a unified-memory desktop part
   [src](https://route179.dev/2026/06/30/optimizing-vllm-cold-start-with-model-streaming-and-compile-caching/)
   — large enough that, if it generalizes, it would be a top-three term in §8 rather than the 15–60 s
   band budgeted. Marlin-2B's video path and the DSpark/MTP draft-head path are separate warm-up
   surfaces neither measured nor modelled.
8. **The P2P/RDMA column in §8.2 is an extrapolation.** ModelExpress's 11 s is for DeepSeek-V4-Pro,
   whose checkpoint size is not published [src](https://github.com/ai-dynamo/modelexpress); the
   Foundry paper's second-hand *"1–2 seconds for trillion-parameter models such as Kimi-K2"*
   [src](https://arxiv.org/html/2604.06664v1) is more optimistic still, and its primary sources
   (Perplexity, Ant Group DeepXPU/SGLang) were not fetched — the web-search budget for this session
   was exhausted. Both need a first-party measurement on this fabric.
9. **SGLang's Weight Cache Daemon quantization allowlist excludes this repo's two big models.** It
   verifies only *"unquantized and block-wise FP8"*; per-tensor FP8, Marlin and AWQ/GPTQ *"raise a
   hard error"*, and DP/EP is future work
   [src](https://www.lmsys.org/blog/2026-08-21-sglang-fast-recovery). DeepSeek-V4.1-Flash (MXFP4 +
   FP8 32×32 UE8M0) and Kimi-K3 (97.9 % MXFP4) are outside it as of 2026-09-19. Re-check per
   release; the 785× is worth chasing.
10. **vLLM's `--load-format ipc_cache` has no published benchmark.** It is documented as
    "post-quantized weights via CUDA IPC for fast engine restarts"
    [src](https://docs.vllm.ai/en/latest/api/vllm/config/load/) and is the vLLM analogue of the
    SGLang daemon, but no numbers were found.
11. **Snapshot/restore for multi-GPU TP replicas has no viable path today.** `cuda-checkpoint`
    *"does not support the IPC memory required by communication kernels (e.g., DeepEP)"* and
    *"doesn't even support expert parallelism"* [src](https://arxiv.org/html/2604.06664v1); driver
    610 adds `cuIpcGetMemHandle`-based IPC support
    [src](https://github.com/NVIDIA/cuda-checkpoint/blob/main/README.md), which may or may not close
    it. Re-check when a 610-branch driver is deployed.
12. **vLLM GPU memory snapshotting is an open feature request**
    ([vllm#33930](https://github.com/vllm-project/vllm/issues/33930)), not a shipped feature; status
    not re-verified on 2026-09-19.
13. **Concurrent compile-cache population may be unsafe.**
    [vllm#24601](https://github.com/vllm-project/vllm/issues/24601) reports that launching multiple
    vLLM processes simultaneously "doesn't work well with vLLM's compile cache" (parallel processes
    clobber the same artifact directories). **Corrected 2026-09-19: the issue is CLOSED** (opened
    2025-09-10), not open as an earlier draft said — but ⚠️ *which* of the two proposed fixes
    (atomic artifact files, or read/write locks) landed and in which release was not established,
    so §7.5's read-only-plus-overlay pattern stays the mitigation.
14. **vLLM sleep mode has no published measurement above 235 B.** The §4.4 Kimi-K3 Level-1 wake
    estimate (~31 s) is `est.` = 1,560.9 GB ÷ ~50 GB/s assumed aggregate H2D, with the H2D rate
    itself unverified.
15. **Post-rollout prefix-cache re-warm cost is unmodelled.** §6.2 notes KV and prefix-cache state
    are lost on drain; nothing fetched here measures how long a replacement replica takes to reach
    its steady-state hit rate, which is the real (not nominal) cost of a rolling update.
16. **PCIe Gen5 x16 host→device achieved bandwidth** is stated in §2.1 as a spec derivation
    (~63 GB/s/direction) with no fetched source. Not load-bearing at a 10–20 GB/s storage ceiling,
    but it becomes the binding constraint if InstantTensor's 45 GB/s (open question 3) is confirmed.
17. **Lazy image pulling may hurt rather than help for inference images**, since the image is read
    in full anyway ([Tensorfuse](https://tensorfuse.io/docs/blogs/lazy_loading_performance_degradation));
    no measurement on an ~20 GB CUDA/vLLM image was found either way. §5.2's recommendation
    (pre-pull on bare metal) sidesteps rather than settles it.
18. **Dynamo's planner exposes no scaling-lead-time parameter.** It acknowledges *"Spinning up a GPU
    worker takes minutes, not seconds"* but, as fetched, has no configuration for it
    [src](https://docs.nvidia.com/dynamo/v1.3.0/components/planner). §5.4's inequality is therefore
    an operator-side discipline, not a supported feature — verify against the current release before
    relying on it.
19. **GPU Operator node-readiness timing is unpublished**
    [src](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html);
    a per-node-join cost, so lower priority than the rest.
20. **AIBrix's Cold Start Manager claims are cited from search snippets**, not a fetched full text of
    [arXiv 2504.03648](https://arxiv.org/pdf/2504.03648); the same applies to ServerlessLLM
    ([2401.14351](https://arxiv.org/pdf/2401.14351)), HydraServe
    ([2502.15524](https://arxiv.org/pdf/2502.15524)) and InferX. None of their numbers are used in
    this document's tables.

---

## Sources

Primary sources fetched for this document, 2026-09-19.

**Papers**

- Kabakibo, Trivedi, Wang — *Breaking the Ice: Analyzing Cold Start Latency in vLLM*, MLSys 2026 —
  https://arxiv.org/abs/2606.07362 · HTML v3: https://arxiv.org/html/2606.07362v3 · artifact:
  https://github.com/upb-cn/vllm-startup-profiler
- *Foundry: Template-Based CUDA Graph Context Materialization for Fast LLM Serving Cold Start* —
  https://arxiv.org/html/2604.06664v1
- Yoshimura, Chiba, Sethi, Waddington, Sundararaman — *Speeding up Model Loading with
  fastsafetensors*, IEEE CLOUD 2025 — https://arxiv.org/abs/2505.23072
- *AIBrix: Towards Scalable, Cost-Effective LLM Inference Infrastructure* —
  https://arxiv.org/pdf/2504.03648 (⚠️ snippet-level only)

**Engine documentation**

- vLLM `LoadConfig` / `LoadFormat` — https://docs.vllm.ai/en/latest/api/vllm/config/load/
- vLLM `torch.compile` integration — https://docs.vllm.ai/en/latest/design/torch_compile/
- vLLM Run:ai Model Streamer — https://docs.vllm.ai/en/stable/models/extensions/runai_model_streamer/
- vLLM InstantTensor — https://github.com/vllm-project/vllm/blob/main/docs/models/extensions/instanttensor.md
- vLLM LoRA — https://docs.vllm.ai/en/latest/features/lora/
- vLLM Sleep Mode blog — https://vllm-project.github.io/2025/10/26/sleep-mode.html
- SGLang model loading — https://docs.sglang.io/docs/advanced_features/model_loading
- LMSYS — *Fast Engine Recovery: Sub-Second Engine Restart for SGLang via Weight Cache Daemon*
  (2026-08-21) — https://www.lmsys.org/blog/2026-08-21-sglang-fast-recovery
- TensorRT-LLM AutoDeploy — https://nvidia.github.io/TensorRT-LLM/torch/auto_deploy/auto-deploy.html
- FlashInfer installation / JIT cache — https://docs.flashinfer.ai/installation.html
- DeepGEMM (DeepJIT, `DG_JIT_CACHE_DIR`) — https://github.com/deepseek-ai/DeepGEMM

**Loaders and transfer planes**

- Run:ai Model Streamer benchmarks —
  https://github.com/run-ai/runai-model-streamer/blob/master/docs/src/benchmarks.md
- fastsafetensors — https://github.com/foundation-model-stack/fastsafetensors
- NVIDIA Dynamo ModelExpress — https://github.com/ai-dynamo/modelexpress
- NVIDIA `cuda-checkpoint` — https://github.com/NVIDIA/cuda-checkpoint/blob/main/README.md

**Orchestration**

- Kubernetes probes — https://kubernetes.io/docs/concepts/workloads/pods/probes/
- llm-d graceful shutdown & draining — https://llm-d.ai/docs/dev/operations/graceful-shutdown
- llm-d rollouts — https://llm-d.ai/docs/dev/operations/rollouts
- NVIDIA Dynamo Planner — https://docs.nvidia.com/dynamo/v1.3.0/components/planner
- NVIDIA GPU Operator getting started —
  https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html
- SOCI snapshotter — https://github.com/awslabs/soci-snapshotter/blob/main/docs/getting-started.md
- Nydus snapshotter — https://github.com/containerd/nydus-snapshotter
- CNCF — *Peer-to-Peer acceleration for AI model distribution with Dragonfly* (2026-04-06) —
  https://www.cncf.io/blog/2026/04/06/peer-to-peer-acceleration-for-ai-model-distribution-with-dragonfly/

**Platform and practitioner measurements**

- AWS — *Fast model loading for AI inference on Amazon EKS* (2026-09-01) —
  https://aws.amazon.com/blogs/containers/fast-model-loading-for-ai-inference-on-amazon-eks/
- AWS — EC2 P6-B300 launch —
  https://aws.amazon.com/blogs/aws/accelerate-large-scale-ai-applications-with-the-new-amazon-ec2-p6-b300-instances
- Modal — *GPU Memory Snapshots* (2025-07-30) — https://modal.com/blog/gpu-mem-snapshots ·
  *Memory snapshots* — https://modal.com/blog/mem-snapshots
- Baseten — *How the Baseten Delivery Network makes cold starts fast* —
  https://www.baseten.co/blog/how-the-baseten-delivery-network-bdn-makes-cold-starts-fast/
- Route179 — *Optimizing vLLM Cold Start with Model Streaming and Compile Caching* (2026-06-30) —
  https://route179.dev/2026/06/30/optimizing-vllm-cold-start-with-model-streaming-and-compile-caching/
- Fergus Finn — *Cloudburst: 70× faster cold(ish) starts for SGLang* (2026-04-06) —
  https://fergusfinn.com/blog/fast-sglang-starts/
- Tensorfuse — *Lazy loading isn't the magic pill to fix AI inference* (⚠️ secondary) —
  https://tensorfuse.io/docs/blogs/lazy_loading_performance_degradation

**Issue trackers referenced**

- vLLM GPU memory snapshotting feature request — https://github.com/vllm-project/vllm/issues/33930
- vLLM concurrent compile-cache hazard — https://github.com/vllm-project/vllm/issues/24601
- `VLLM_CACHE_ROOT` ownership crash-loop — https://github.com/giantswarm/agent-platform/issues/541

**Repo cross-references (not re-derived here)**

- [`research/METHODOLOGY.md`](../METHODOLOGY.md) — §1 weight memory, §3 fit, §6 cost, §8 pinned
  GPU/model inputs
- [`research/README.md`](../README.md) — tree index and open questions
- [`research/cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md) — engine
  versions, launch commands, `VLLM_ENGINE_READY_TIMEOUT_S`, CUDA-graph ladders
- [`research/cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md) —
  prefix caching, speculative decoding, disaggregation, autotune/JIT skip flags
- [`research/cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) — §5.14 price tiers
  used in §5.1 and §5.5
- [`research/gpus/b300.md`](../gpus/b300.md), [`research/gpus/b200.md`](../gpus/b200.md) — capacity
  and the measured B200 prefill step time used in §8.3 note ʰ
- [`research/matrix/fit-matrix.md`](../matrix/fit-matrix.md) — max concurrency per replica, the
  `capacity_per_replica` input to §5.1
- [`common/download.sh`](../../common/download.sh), [`common/env.sh`](../../common/env.sh) — the
  existing S3-first weight staging path extended in §5.3

---

## Verification log (2026-09-19)

Adversarial fact-check. Every claim below was checked by opening the **primary** source (not the
doc's summary of it) or, for `research/` cross-references, by opening the file and confirming the
number is actually there. Every derivation was recomputed with `python3`. **52 claims checked: 40
CONFIRMED, 11 CORRECTED, 1 UNVERIFIABLE.** Nothing was removed; every correction names the source
that overrides the old text, and every claim that could not be confirmed is marked
**⚠️ TO BE VERIFIED** in place rather than deleted.

### Papers and measured figures

| # | Claim as printed | Verdict | Source opened |
|---|---|---|---|
| 1 | *Breaking the Ice: Analyzing Cold Start Latency in vLLM*, MLSys 2026, Kabakibo / Trivedi / Wang; vLLM **v0.10.1.1**, H100 NVL + AMD EPYC 9354, **22 models**; Llama3.2-3B **total 20.32 s** | **CONFIRMED** verbatim (title, venue, authors, version, hardware, 22 models, 20.32 s) | https://arxiv.org/abs/2606.07362 · https://arxiv.org/html/2606.07362v3 |
| 2 | Per-stage figures: tokenizer **0.08 s** (Llama2-7B) → **0.29 s** (Llama3-3B), PCC 0.99; `torch.compile` hit **3–6 s** / miss **11–21 s**; KV profiling **0.67 → 0.97 s**, PCC 0.92, MoE deviates; capture **0.91 s** (Qwen-0.5B), **1.51 s** (Qwen-MoE-14.3B), **0.33 s @3 → 1.8 s @60** (Llama2-7B), PCC 1.0 | **CONFIRMED** — every figure matches | same |
| 3 | fio SSD **25 GB/s read / 15 GB/s write**; flushing page cache slowed loading **0.5×** but end-to-end only **1.04×**, because loading is *"7–10 % of the total startup duration"* | **CONFIRMED** verbatim | https://arxiv.org/html/2606.07362v3 |
| 4 | **> 4× variance** across nine vLLM releases; **2× reduction v0.9 → v0.10**; predictor **MSE 2.42 s, max error 2.08 s**, re-validated on v0.11 at **2.62 s**; non-MoE only | **CONFIRMED** (max error 2.08 s is on Llama3-3B) | same |
| 5 | MLSys paper finds **Tensorizer** fastest at *"53–60 % of Safetensors' time"*, Run:ai only moderate — i.e. a genuine disagreement with Run:ai's own benchmark | **CONFIRMED**; the disagreement is real and §2.2 states it correctly | same |
| 6 | Foundry: Qwen3-14B DP1–DP8 **36–48 s → 1.7–1.8 s**; Qwen3-30B-A3B EP2–EP8 **112–154 s → 2.7–2.8 s**; **Qwen3-235B-A22B EP8 650 s → 3.9 s (99 %)**; archive **2.2 GB (1.4 GB kernel binaries)** vs **3.7 GB** CUDA-checkpoint image of a 14 B model | **CONFIRMED** — all figures match, including the 3.4–5× archive-size ratio | https://arxiv.org/html/2604.06664v1 |
| 7 | *"CUDA-checkpoint does not support the IPC memory required by communication kernels (e.g., DeepEP)"*, restore latency grows for multi-GPU DP, single-GPU restore **5.7–6.1 s** | **CONFIRMED** verbatim | same |
| 8 | fastsafetensors **4.8×–7.5×** vs default deserializer; **26.4 GB/s** NVMe with GDS (Llama-70B, 4 GPUs); vLLM **12.39 s → 4.74 s** (4× L40S), **16.04 s → 6.88 s** (1× A100); ROCm `nogds` **6.02 vs 1.28 GB/s** (4.7×) | **CONFIRMED** — every figure verbatim | https://github.com/foundation-model-stack/fastsafetensors |

### Engine and vendor measurements

| # | Claim as printed | Verdict | Source opened |
|---|---|---|---|
| 9 | SGLang Ling-2.6-1T FP8 on 8×H20-3e, **161 shards**: weight load ~495 s (93.9 %), tokenizer ~13 s, capture **7.7 s**, `torch.distributed` ~5 s, total **~527 s**; daemon **~495 s → ~0.63 s (~785×)**; end-to-end **8.8 min → 0.528 min (93.9 %)**; allowlist *"unquantized and block-wise FP8"*, per-tensor FP8 / Marlin / AWQ/GPTQ *"raise a hard error"*; **DP/EP future work**; fingerprint = model path, TP/DP size, quant-config hash, dtype + device capability and torch version; *"Any mismatch … triggers a **full disk reload**"* | **CONFIRMED** — all of it, verbatim | https://www.lmsys.org/blog/2026-08-21-sglang-fast-recovery |
| 10 | §1.3's "Other ~7 s, 1.3 %" | **CORRECTED** → the blog itemizes *server ready ~4 s + pre-init & other ~1 s + cache allocation ~1 s = ~6 s (1.1 %)*. Table row replaced; the ~527 s total is unaffected | same |
| 11 | 122 B model on a B200 with SGLang, `drop_caches`: **695 s total, 21 s Python imports, 531 s weight loading**, 2026-04-06 | **CONFIRMED**, and refined: the model is **Qwen3.5-122B-A10B-FP8**, a **single B200**, **SGLang v0.5.10** | https://fergusfinn.com/blog/fast-sglang-starts/ |
| 12 | Run:ai benchmark: Meta-Llama-3-8B 15 GB, g5.12xlarge / A10G, CUDA 12.4, vLLM 0.5.5, Streamer 0.6.0, Tensorizer 2.9.0; GP3 47.99 / **14.34** (c16) / 16.11; IO2 47.00 / **7.53** (c8) / 10.36; S3 n/a / **4.88** (c32) / 37.36; end-to-end 66.13 / **35.08** / 36.19, 62.69 / **28.28** / 30.88, — / **23.18** / 65.18; c1 = **47.56 s** | **CONFIRMED** — every cell, including concurrency values | https://github.com/run-ai/runai-model-streamer/blob/master/docs/src/benchmarks.md |
| 13 | InstantTensor: Qwen3-30B-A3B 1×H200 **57.4 s / 1.1 GB/s → 1.77 s / 35 GB/s (32.4×)**; DeepSeek-R1 8×H200 **160 s / 4.3 GB/s → 15.3 s / 45 GB/s (10.5×)**; GDS when available | **CONFIRMED** verbatim | https://github.com/vllm-project/vllm/blob/main/docs/models/extensions/instanttensor.md |
| 14 | ModelExpress on DeepSeek-V4-Pro, vLLM 0.23.0, TP=8, 8×B200: **8m53s / 3m16s / 1m10s / 11 s (48×)**; artifacts **8m1s / 7m (1.1×) / 1m44s (4.6×)**; NIXL registration **8.16 / 1.14 / 0.79 s**; 6-step strategy chain in the printed order; `MX_ARTIFACT_TRANSFER=1` depends on `MX_P2P_METADATA=1`, which requires a **Redis or Kubernetes** central coordinator; the transferred caches are *"Triton, DeepGEMM, TileLang, CuTe DSL, and FlashInfer"* | **CONFIRMED** — every number and the chain order | https://github.com/ai-dynamo/modelexpress |
| 15 | AWS EKS: stock S3 **5.96 Gbps** vs all-ranges-at-once **33–39 Gbps**; p5.48xlarge; Qwen3-6-35B-A3B 67 GiB TP2 **82 / 65 / 65 → 16 s**; Llama-4-Scout 203 GiB TP4 **457 / 59 / 60 → 32 s**; 64 GiB split *"~29 s, 35 % … ~53 s, 65 %"*; 203 GiB split *"~423 s, 92 % … ~34 s, 8 %"*; s5cmd *"~25 s for 200 GB to NVMe … ~20 s to GPU"*; published **2026-09-01** | **CONFIRMED** — every figure and both quotes | https://aws.amazon.com/blogs/containers/fast-model-loading-for-ai-inference-on-amazon-eks/ |
| 16 | `p6-b300.48xlarge`: 8× B300, **2144 GB HBM3e**, **192 vCPU**, **4 TB** RAM, **8 × 3.84 TB** local, EBS **100 Gbps**, ENA **300 Gbps**, EFA **6.4 Tbps**, *"up to 1.2Tbps of throughput to the Lustre file system"* with EFA + GDS; **aggregate local NVMe GB/s is not published** | **CONFIRMED**, including the negative (open question 4 stands) | https://aws.amazon.com/blogs/aws/accelerate-large-scale-ai-applications-with-the-new-amazon-ec2-p6-b300-instances |
| 17 | Route179: baseline **142 / 39 / 136 = 317 s**; + Streamer **59 / 38 / 124 = 221 s**; + `VLLM_CACHE_ROOT` **59 / 10 / 98 = 167 s**; the YAML snippet; weight updates alone do not invalidate the compile cache | **CONFIRMED** (rows sum exactly; 1.43× and 1.90× recomputed). **CORRECTED separately**: the doc twice called this *"AWS's EKS breakdown"* — it is a **practitioner post on one DGX Spark GB10 (unified memory) EKS Hybrid Node, vLLM v0.22.1**, a different source from #15. Fixed in §3.1, §3.5 and open question 7 | https://route179.dev/2026/06/30/optimizing-vllm-cold-start-with-model-streaming-and-compile-caching/ |
| 18 | Modal GPU snapshots: capture quote verbatim; driver **570/575**; Parakeet **~20 s → ~2 s**, ViT **8.5 → 2.25 s**, **vLLM Qwen2.5 45 s → 5 s**; `experimental_options={"enable_gpu_snapshot": True}`; **2025-07-30** | **CONFIRMED** (the vLLM row is Qwen2.5-**0.5B**-Instruct) | https://modal.com/blog/gpu-mem-snapshots |
| 19 | vLLM sleep mode: Qwen3-0.6B **37.6 / 0.26 / 0.85 s**, Phi-3-vision-128k **58.1 / 0.82 / 2.58 s**, A100 + A4000, vLLM **0.11.0**; `--enable-sleep-mode`, `VLLM_SERVER_DEV_MODE=1`, `/sleep`, `/wake_up`, `/collective_rpc`, `/reset_prefix_cache` | **CONFIRMED** | https://vllm-project.github.io/2025/10/26/sleep-mode.html |
| 20 | "first-inference latency **81–87 %** better"; "**5–7×** slower first inference"; "the **18–200×** headline … from 0.6–**235 B** models" | **CORRECTED** → **81–88 %** (83 % Qwen3-0.6B, 81 % Phi-3-vision); **5.8× and 7.1×**; the blog's headlines are **18–20× (L1)** and **23–45× (L2)** — there is **no 235 B row and no 200× figure** in the post. The §4.4 table's own 145×/45×/71×/23× ratios are arithmetically correct against its cold/wake columns and are kept, now labelled as derived | same |

### Flags, configuration and feature support

| # | Claim as printed | Verdict | Source opened |
|---|---|---|---|
| 21 | vLLM `LoadFormat` contains `instanttensor` (*"distributed loading with pipelined prefetching and fast direct I/O"*), `ipc_cache` (*"map post-quantized weights from a local weight cache daemon via CUDA IPC for fast engine restarts"*), `modelexpress`, `runai_streamer(_sharded)`, `sharded_state`, `tensorizer`, `npcache`, `dummy`, `auto`, `pt`, `safetensors` | **CONFIRMED** verbatim for all three quoted descriptions | https://docs.vllm.ai/en/latest/api/vllm/config/load/ |
| 22 | `fastsafetensors` listed in the same table as a vLLM `--load-format` | **CORRECTED (scope, not substance)** → the flag **is** valid (*"enables loading model weights to GPU memory by leveraging GPU direct storage"*) but is documented on its own extensions page, **not** in the `LoadFormat` enum listing, which additionally carries `mistral` (omitted by the doc). Both notes added to §2.3 | https://docs.vllm.ai/en/latest/models/extensions/fastsafetensor/ |
| 23 | SGLang `remote_instance`, `layered`, `--weight-loader-prefetch-checkpoints` (*"from N×checkpoint to 1×checkpoint"*), `--weight-loader-drop-cache-after-load` | **CONFIRMED** — all four quotes verbatim, including the N× → 1× phrasing | https://docs.sglang.io/docs/advanced_features/model_loading |
| 24 | vLLM compile cache: default `~/.cache/vllm/torch_compile_cache/`, `VLLM_DISABLE_COMPILE_CACHE=1`, key covers configs + PyTorch settings + traced forward, code changes invalidate, *"all the compilation finishes before we serve any requests"*, *"directly copy the whole … directory"* | **CONFIRMED** | https://docs.vllm.ai/en/latest/design/torch_compile/ |
| 25 | The quote *"the compiled artifacts and the cache can be reused across machines with the same environment. If you have an autoscaling use case, make sure to generate the cache directory once and share it among instances"*, attributed to that page | **UNVERIFIABLE** → two fetches (one a targeted keyword search for "autoscaling" / "across machines" / "share it among instances") found no such sentence. Marked **⚠️ TO BE VERIFIED** in §3.1; §3.4 and §7.5 now rest on the confirmed weaker quote instead. `VLLM_CACHE_ROOT` is likewise **not** on that page (it is real — Route179 deploys it — but the citation was wrong) | same |
| 26 | Dynamo SLA Planner: `load_predictor` ∈ {arima (default), prophet, kalman, constant}; `throughput_adjustment_interval_seconds` **180**, `load_adjustment_interval_seconds` **5**, `load_scaling_down_sensitivity` **80**; *"Spinning up a GPU worker takes minutes, not seconds"*; **no scaling-lead-time parameter** | **CONFIRMED**, negative included — open question 18 stands | https://docs.nvidia.com/dynamo/v1.3.0/components/planner |
| 27 | `cuda-checkpoint` driver ladder **550 / 570 (NVML, CRIU 4.0+, lock w/ timeout) / 580 (GPU migration, container partial passthrough) / 595 (ARM) / 610 (`cuIpcGetMemHandle` IPC)**; actions `lock|checkpoint|restore|unlock` + `toggle`; limitations (UVM, `cuMemExportToShareableHandle` IPC, waits for submitted work, no good-state guarantee on error) | **CONFIRMED** verbatim | https://github.com/NVIDIA/cuda-checkpoint/blob/main/README.md |
| 28 | Kubernetes: *"If your container usually starts in more than `initialDelaySeconds + failureThreshold × periodSeconds`…"*; liveness/readiness do not run until the startup probe succeeds; defaults `periodSeconds` 10, `failureThreshold` 3, `successThreshold` 1 → **30 s**; probe-level `terminationGracePeriodSeconds` exists | **CONFIRMED** (also: `timeoutSeconds` default 1 s, `initialDelaySeconds` 0) | https://kubernetes.io/docs/concepts/workloads/pods/probes/ |
| 29 | llm-d drain: EPP *"evicts queued requests with a retryable `503 Service Unavailable`"*; vLLM *"immediately `aborts` in-flight requests and exits"*; `--shutdown-timeout N` *"catches `SIGTERM` and continues serving the currently running requests for up to `N` seconds"*; *"`terminationGracePeriodSeconds` must be **greater than** `--shutdown-timeout`"*; example **120 / 90** | **CONFIRMED** verbatim, example values included | https://llm-d.ai/docs/dev/operations/graceful-shutdown |
| 30 | llm-d rollouts: three strategies; blue/green via a second InferencePool with HTTPRoute weights **1 % → 5 % → 10 % → 50 % → 100 %**; LoRA rollout via `InferenceModelRewrite`; the LLM-specific risk (long-running requests cannot be migrated, new pods need warm-up for weights and CUDA graphs, GPU memory is expensive) | **CONFIRMED** — note the risk statement is a bulleted list on the page, not the single sentence the doc quotes; substance is exact | https://llm-d.ai/docs/dev/operations/rollouts |
| 31 | FlashInfer `pip install flashinfer-jit-cache --index-url https://flashinfer.ai/whl/cu129` (cu130 / cu134 variants), *"eliminates compilation and downloading overhead at runtime"*, `flashinfer show-config`; DeepGEMM `$HOME/.dj`, `DG_JIT_CACHE_DIR` accepts a `:`-separated list (first hit wins, miss compiles into the first path), *"All kernels are compiled at runtime through DeepJIT, requiring no CUDA compilation during installation"*; vLLM LoRA `VLLM_ALLOW_RUNTIME_LORA_UPDATING`, `/v1/load_lora_adapter`, `load_inplace`, *"comes with security risks… should not be used in production unless it is an isolated, fully trusted environment"*, `--max-lora-rank` too high wastes memory | **CONFIRMED** — all three sources, verbatim | https://docs.flashinfer.ai/installation.html · https://github.com/deepseek-ai/DeepGEMM · https://docs.vllm.ai/en/latest/features/lora/ |
| 32 | `vllm#33930` is an **open** feature request for GPU memory snapshotting; `vllm#24601` is an **open** issue on concurrent compile-cache population | **#33930 CONFIRMED** (*"[Feature]: GPU Memory Snapshotting to reduce cold starts"*, open, 2026-02-05). **#24601 CORRECTED** → *"[Bug]: Launching multiple vLLM processes at the same time doesn't work well with vLLM's compile cache"*, opened 2025-09-10, **CLOSED**. Fixed in §3.4 and open question 13 | https://github.com/vllm-project/vllm/issues/33930 · https://github.com/vllm-project/vllm/issues/24601 |

### `research/` cross-references (file opened, number located)

| # | Claim as printed | Verdict | Where it actually is |
|---|---|---|---|
| 33 | *"an explicit 26-size CUDA-graph ladder to 8190"* with `--max-cudagraph-capture-size 8190`, cited to `inference-engines.md §3` | **CONFIRMED** — that exact phrase is in **§3.6 (B300)** line 513, and §2.1 line 105–106 carries the same fact as *"an explicit 26-entry capture ladder up to 8190"*, sourced to `recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/b300.json` | `research/cross-cutting/inference-engines.md` |
| 34 | `VLLM_ENGINE_READY_TIMEOUT_S=3600` in the Kimi-K3 and DeepSeek recipes, with *"Long load times are expected"*; `--load-format fastsafetensors --no-enable-flashinfer-autotune` in the **vLLM** B300 Kimi-K3 recipe | **CONFIRMED** — lines 758, 772, 871, 1048–1053 | same |
| 35 | vLLM **0.29.0** / SGLang **0.5.20** pinned as of 2026-09-19 | **CONFIRMED** — §0 lines 21–22 | same |
| 36 | *"the TensorRT (graph-compiler) backend is **removed**; PyTorch is the sole execution backend"* | **CONFIRMED** — §2.3 line 253 | same |
| 37 | B200 measured **220–222 ms** 16,384-token chunked-prefill steps, cited to `gpus/b200.md §9.3` | **CONFIRMED** — §9.3 "Prefill upper bound", line 720, derived from vLLM PR #56686 at TP8 | `research/gpus/b200.md` |
| 38 | DeepSeek-V4.1-Flash on B300 as **TP4, 2 replicas per node** | **CONFIRMED** — *"Interactive / agentic serving: two TP4 servers per 8× B300 node"*, and at TP4 the Engram tables shard and stay resident (so §8.3 correctly budgets the full 510 GB into HBM) | `research/models/deepseek41f/README.md` |
| 39 | Standby price *"$17.80/GPU-hr on-demand AWS p6-b300, $7.40 at the cheapest B300 seller — cloud-pricing.md **§5.14**"* | **CORRECTED** → §5.14 pins `b300` at **low $7.40 (Hyperstack) / high $15.00 (OCI) / res1y $7.94** and contains **no AWS B300 row**. The $17.802 figure is real but lives in the **§3.1 AWS** and §5.7 B300 tables. Citations fixed in §0 and §5.1; the dollar amounts ($29.60–$71.21/hr, $59–$142/hr) are unchanged and recompute exactly | `research/cross-cutting/cloud-pricing.md` |
| 40 | `common/download.sh` tries **S3 first, HF second**, lands in `$WEIGHTS_ROOT/$EXP` (default `/mnt/nvme`), pre-checks free space **in bytes** at `SIZE_GB + 20`, passes `WORKERS` to `s5cmd --numworkers` | **CONFIRMED** — `need_b=$(( (SIZE_GB + 20) * 1000000000 ))` (line 41) with the verbatim comment about `df -BG` over-demanding ~7 %; `s5cmd --numworkers "${WORKERS:-64}"` (line 54); `WEIGHTS_ROOT="${WEIGHTS_ROOT:-/mnt/nvme}"` (`common/env.sh` line 15) | `common/download.sh`, `common/env.sh` |

### Recomputed derivations (`python3`)

| # | Derivation | Verdict |
|---|---|---|
| 41 | §8.2 weight-load table — all 24 cells (`bytes ÷ 10, 20, 3 GB/s`) | **CONFIRMED** exactly (e.g. 510.29/10 = 51.03; 1560.9/20 = 78.05; 1560.9/3 = 520.30) |
| 42 | §5.3 "all five checkpoints total **2,659.5 GB** = **8.7 %** of 30.7 TB"; Kimi-K3 TP8 read amplification **12.5 TB**; 1.56 TB = **39 %** of 4 TB host RAM | **CONFIRMED** (2,659.464 GB; 8.657 %; 12.487 TB; 39.0 %) |
| 43 | §2.7 NVFP4 variant is **~3 %** larger / slower to load (527.27 vs 510.29 GB) | **CONFIRMED** (3.33 %) |
| 44 | §5.1 standby cost **$29.60–$71.21/hr**, **~$21.3k–$51.3k/month**; §5.5/§8.5 Kimi node **$59–$142/hr** | **CONFIRMED** (4×7.40 = 29.60; 4×17.802 = 71.208; ×720 h = 21,312 / 51,270; 8×7.40 = 59.20; 8×17.802 = 142.42). Month basis is **720 h**, now stated |
| 45 | §1.6 / §2.5 "8.8 min → 0.528 min" and "7.7 s of capture is **24 %** of the remaining budget"; §1.3 shares | **CONFIRMED** (527 s → 31.7 s; 495/527 = 93.9 %; 7.7/31.7 = 24.3 %) |
| 46 | §2.2 / §2.4 / §3.1 / §3.2 derived ratios: InstantTensor 32.4× and 10.5× (and 63 GB / 688 GB implied model sizes, self-consistent); ModelExpress 2.7×/7.6×/48×/1.1×/4.6×/7.1×/10.3× and "8 s is 7 % of a two-minute budget"; Route179 1.43× and 1.90×; Foundry **1.27 s/graph** and **0.22–0.30 s/graph**, 26 × (1.0–3.0) = **26–78 s** | **CONFIRMED** — every one |
| 47 | §4.4 Kimi-K3 Level-1 wake `est.` = 1,560.9 GB ÷ ~50 GB/s ≈ **31 s** | **CONFIRMED** (31.2 s). The ~50 GB/s H2D input remains ⚠️ (open question 14) |
| 48 | §8.3 **Total (est.)** row | **CORRECTED** → Kimi-K3 low end **151 s → 164 s** (the ~13 s tokenizer stage was dropped from the sum). Recomputed bands: Marlin **20.3–46.0 s**, Qwen **23.8–54.1 s**, DeepSeek **94.5–226.0 s**, Kimi **164.0–363.1 s**. The other three totals were already correct |
| 49 | §8.3 **Rounded planning figure** row vs the Total row | **CORRECTED (flagged, not overwritten)** → the band is not a rounding of the total on any model (Marlin 35–55 vs 20–46; Qwen 50–80 vs 24–54; Kimi 3.5–5 min vs 2.73–6.05 min). It is kept because `01-bare-metal-cluster.md` §5.5 independently pins *"**3–5 minutes** as the planning figure"* for Kimi-K3, but it is now labelled a **padded planning band, not arithmetic** |
| 50 | §8.4 S3-cold totals | **CORRECTED (augmented)** → deltas are right (+1.3–1.5 / +12.9–15.7 / +119–145 / +364–442 s) but were added to the padded band. A recomputed column was added from the Total row: **21.8–47.3 s / 39.5–67.0 s / 239.1–345.1 s (3.98–5.75 min) / 606.3–727.3 s (10.10–12.12 min)** |
| 51 | §8.5 "a cold start of 2–5 minutes is **40–100× the entire request budget**" | **CORRECTED** → an S1 request is 512 × 50 ms = **25.6 s** of decode, so 120–300 s is **4.7–11.7×**, not 40–100×. Rewritten, with the per-token comparison (2,400–6,000×) given separately since that is the ratio the client experiences |
| 52 | §1.1 "a replica's cold start is **nine** stages" against a table with rows 1–10 | **CORRECTED** → **ten** |

### Structural gaps found (not fixed here)

- **Cross-document contradiction with [`05-autoscaling-and-predictive-scaling.md`](./05-autoscaling-and-predictive-scaling.md)**, which carries cold-start estimates of **1–3 min (Marlin-2B), 2–4 min (Qwen3.8-27B), 5–12 min (DeepSeek-V4.1-Flash), 8–20 min (Kimi-K3)** — 2–4× this document's warm-NVMe bands and, for the two small models, above even its S3-cold ones. Both are `est.`; neither is measured. Flagged inline in §8.3; **the two documents must converge on one band**, and this one's is built stage-by-stage from cited per-stage measurements.
- [`01-bare-metal-cluster.md` §5.5](./01-bare-metal-cluster.md) agrees with this document (*"treat **3–5 minutes** as the planning figure"* for Kimi-K3 on B300, *"~35 s–2.6 min weight load (loader-dependent)"*), so 05 is the outlier of the three.
- The scope note promises this document supplies *"the cold-start budget per model"* to the autoscaler and routing documents. It does (§8), but nothing in §8 is measured — every number is `est.` on other models' per-stage figures. **Open question 1 is not a footnote; it is the document's load-bearing gap.**
- **Not covered against the brief**: nothing on cold start for the **prefill/decode-disaggregated** shapes this tree recommends elsewhere (a PD deployment boots two differently-shaped workers and the SLA Planner scales them independently — §5.4 cites the planner but never budgets a P or D worker separately); and nothing on **multi-node** cold start for Kimi-K3 at TP32 on H100, which `inference-engines.md` §6.3 documents as a shipped shape and where `torch.distributed` init across 32 ranks is not the ~5 s 8-rank figure §8.3 reuses.
- **Claims that looked invented and were run down**: the AutoDeploy "being deprecated / agentic approaches" statement (**not on the cited page** — corrected, §1.4); the vLLM "share the cache among instances for autoscaling" quote (**not on the cited page** — ⚠️, §3.1); the sleep-mode "18–200× from 0.6–235 B models" headline (**no 235 B row, no 200×** — corrected, §4.4). Everything else that read as suspiciously precise — 0.63 s, 785×, 11 s, 650 s, 26.4 GB/s, 45 GB/s, 5.96 Gbps — checked out verbatim against its primary source.
