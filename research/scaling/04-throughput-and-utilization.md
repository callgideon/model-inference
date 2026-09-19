# Maximising throughput and GPU utilisation

**Research date: 2026-09-19.** Formulas, pinned GPU/model inputs and the
`[src]` / ⚠️ / `est.` / `meas.` legend come from
[`../METHODOLOGY.md`](../METHODOLOGY.md); the tree index is
[`../README.md`](../README.md).

## 0. Scope, and what this document deliberately does not repeat

[`../cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md)
already covers the **per-request** and **per-replica** levers: prefix and KV
caching (§1), speculative decoding including why it degrades at high batch
(§2.5), parallelism axes for large MoE and the TP/EP/DP/PP/DCP measurements
(§3), continuous batching, chunked prefill, CUDA graphs and the
throughput-vs-TPOT Pareto curve (§4), long-context KV pressure (§5). Weight and
KV formats are in
[`../cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md);
engine support per GPU is in
[`../cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md).

**This document is about the layer above that**: given a fleet of nodes and a
mix of models and traffic classes, how do you measure whether the silicon is
working, how do you shape replicas and pools so the *cluster* aggregate is
high, and how do you fill the capacity that a single well-tuned replica leaves
on the floor. Where a number already exists in the docs above, it is linked,
not re-printed.

**Sibling documents in `research/scaling/`** cover the cluster substrate
(Kubernetes, GPU Operator, networking), autoscaling and predictive scaling,
request admission and routing, and cold-start. This document assumes those
exist and stays on utilisation; where the boundary is thin (the Dynamo SLA
planner, the inference-aware gateway) it is named and handed off.

**Summary of the recommendations derived below**, each with its decision rule
in the section that derives it:

| # | Recommendation | Rule | §|
|---|---|---|---|
| 1 | Alert on `DCGM_FI_PROF_PIPE_TENSOR_ACTIVE` + MBU, never on `GPU_UTIL` | `GPU_UTIL` reads 100 % with one SM busy | [§1](#1-utilisation-metrics-that-matter) |
| 2 | For **replicated-KV** models (MLA at TP without DCP), prefer *more, smaller* TP replicas | halve TP whenever `W/N < ⅔ × (usable − workspace)` at the lower `N` | [§2.1](#21-replica-sizing-tp-vs-dp-and-the-one-piece-of-algebra-that-decides-it) |
| 3 | For **sharded-KV** models (GQA with `n_kv_heads ≥ TP`), prefer *fewer, wider* replicas | aggregate KV rises with TP; stop at `TP = n_kv_heads` | [§2.1](#21-replica-sizing-tp-vs-dp-and-the-one-piece-of-algebra-that-decides-it) |
| 4 | Size P:D pools from measured rates, not from a rule of thumb; start at a **fixed** ratio | published optima span 2:1 to 1:3 on the same axis | [§2.3](#23-prefilldecode-pool-ratios) |
| 5 | Run a batch tier at strict-lower priority on the same replicas before buying idle-time isolation | vLLM priority preemption costs < 4 % (measured 3.9 %) | [§3.1](#31-two-traffic-classes-one-fleet) |
| 6 | Tier KV to host/NVMe by default; the break-even is *capacity and hit rate*, never bandwidth | `load_time ≪ recompute_time` by 10²–10⁴× for every model here | [§4.3](#43-the-break-even-that-actually-matters-is-not-bandwidth) |
| 7 | Turn EPLB on for any EP ≥ 16 deployment and budget its redundant-expert HBM | measured 1.49× prefill / 2.54× decode | [§5.5](#55-expert-parallel-load-balancing-eplb), [§6.2](#62-expert-placement-and-eplb-policies) |
| 8 | On 8-GPU HGX nodes, keep EP inside the node; cross-node EP is an NVL72 pattern | all-to-all leaves NVLink for IB at EP > 8 | [§6.3](#63-nvl72-versus-8-gpu-nodes) |

---

## 1. Utilisation metrics that matter

### 1.1 The four layers, and why only one of them is usually instrumented

| Layer | Question it answers | Typical metric | Who owns it |
|---|---|---|---|
| Fleet | What fraction of GPU-hours paid for are serving *any* traffic? | allocated GPU-hours ÷ billed GPU-hours | capacity planning |
| Scheduler | Of the serving GPU-hours, what fraction is in a batch large enough to matter? | batch occupancy, KV utilisation, queue depth | engine + router |
| Device | Of the busy time, what fraction of the silicon is engaged? | `SM_ACTIVE`, `SM_OCCUPANCY`, `PIPE_TENSOR_ACTIVE`, `DRAM_ACTIVE` | DCGM |
| Roofline | Of the engaged silicon, what fraction of the *achievable* bound is reached? | **MBU**, **MFU** | derived |

Most fleets instrument only the device layer, and usually only its worst
metric. The fleet layer is where the money is: a replica at MBU 0.7 that is
idle 60 % of the day is worse than a replica at MBU 0.45 that is never idle.

### 1.2 "GPU utilisation" is the wrong number

NVML / `nvidia-smi` `GPU_UTIL` is a *time-occupancy flag*, not a work
measurement. Elvinger et al. state the failure mode directly: approaches
relying on `nvidia-smi` "might falsely consider a GPU fully utilized even if
only a single SM is occupied", because the metric shows "the percentage of time
a kernel is active on a GPU, without revealing how well this kernel utilizes
the various GPU resources"
[src](https://arxiv.org/html/2501.16909v1) (Elvinger et al., arXiv 2501.16909,
2025).

This is not an edge case for LLM serving — it is the *normal* state of
autoregressive decode, which launches a long stream of small kernels, each
resident for the whole sampling window and each touching a small fraction of
the SMs. A B300 has far more SM capacity than a batch-8 decode step can fill;
`GPU_UTIL` will still read ~100 %.

The replacements, with NVIDIA's own definitions quoted from the DCGM profiling
reference [src](https://docs.nvidia.com/datacenter/dcgm/latest/learn/modules/profiling.html):

| DCGM field | Definition (verbatim) | Reads what |
|---|---|---|
| `DCGM_FI_PROF_GR_ENGINE_UTIL_RATIO` (1001) | "The fraction of time any portion of the graphics or compute engines were active." | the `GPU_UTIL` analogue — same blind spot |
| `DCGM_FI_PROF_SM_UTIL_RATIO` (1002, exporter name `DCGM_FI_PROF_SM_ACTIVE`) | "The fraction of time at least one warp was active on a multiprocessor, averaged over all multiprocessors." | how **broadly** the SMs are engaged |
| `DCGM_FI_PROF_SM_OCCUPANCY_RATIO` (1003) | "The fraction of resident warps on a multiprocessor, relative to the maximum number of concurrent warps supported." | how **deeply** each SM is engaged |
| `DCGM_FI_PROF_TENSOR_UTIL_RATIO` (1004, exporter `PIPE_TENSOR_ACTIVE`) | "The fraction of cycles the tensor (HMMA / IMMA) pipe was active." | whether the units you **pay for** are working |
| `DCGM_FI_PROF_DRAM_UTIL_RATIO` (1005) | "The fraction of cycles where data was sent to or received from device memory." | whether **bandwidth** is the bottleneck |
| `DCGM_FI_PROF_NVLINK_TX_BYTES` / `RX_BYTES` (1011/1012) | "The rate of data transmitted / received over NVLink, not including protocol headers, in bytes per second." | TP all-reduce and EP all-to-all pressure |
| `DCGM_FI_PROF_PCIE_TX_BYTES` / `RX_BYTES` (1009/1010) | "The rate of data transmitted / received over the PCIe bus, including both protocol headers and data payloads, in bytes per second." | KV offload traffic to host |

**Interpretation table for a decode-dominated replica** (⚠️ **TO BE VERIFIED** —
these bands are inference from the definitions above plus the METHODOLOGY §4
MBU/MFU planning bands, not a published production dataset; no source found
that publishes per-generation DCGM bands for 2026 LLM serving):

| Pattern | Diagnosis | Action |
|---|---|---|
| `SM_ACTIVE` high, `SM_OCCUPANCY` low, `DRAM_ACTIVE` high | healthy memory-bound decode | nothing; this is the target |
| `SM_ACTIVE` high, `DRAM_ACTIVE` low, `PIPE_TENSOR_ACTIVE` low | launch/latency bound — small batch, no CUDA graphs, Python overhead | raise batch, enable CUDA graphs ([§5.1](#51-cuda-graphs-and-torchcompile)) |
| `PIPE_TENSOR_ACTIVE` high | prefill-dominated step | fine; check TTFT SLO instead |
| `NVLINK_TX_BYTES` a large fraction of link peak | TP all-reduce or EP all-to-all is the bottleneck | lower TP ([§2.1](#21-replica-sizing-tp-vs-dp-and-the-one-piece-of-algebra-that-decides-it)), or check EPLB ([§5.5](#55-expert-parallel-load-balancing-eplb)) |
| all of the above low but `GR_ENGINE_ACTIVE` high | you are measuring an idle engine's heartbeat | check queue depth and routing, not the GPU |

### 1.3 MBU — the definition, and the trap in it

The canonical definition is Databricks', and it is worth quoting exactly
because half the re-statements of it drop the KV term:

> "MBU is defined as (achieved memory bandwidth) / (peak memory bandwidth)
> where achieved memory bandwidth is ((total model parameter size + KV cache
> size) / TPOT)."
> [src](https://www.databricks.com/blog/llm-inference-performance-engineering-best-practices)

and:

> "MBU is complementary to the Model Flops Utilization (MFU; introduced in the
> PaLM paper) metric which is important in compute-bound settings." … "MBU
> values close to 100% imply that the inference system is effectively utilizing
> the available memory bandwidth." [src](https://www.databricks.com/blog/llm-inference-performance-engineering-best-practices)

METHODOLOGY §4 uses the same quantity with the MoE correction (an MoE reads
only the experts actually hit, not all weights), which matters enormously for
the models here — a naïve `all_weights / TPOT` MBU for Kimi-K3 (2.78 T
parameters, 104.19 B active) would read absurdly high.

**The trap.** Databricks' own measurements show MBU *falling* as batch rises:
~50 % on a single A100-40G at batch 1, "60%" at batch 1 on 2×H100 and "55%" at
batch 1 on 4×A100-40GB, with **"MBU decreases as batch size increases"**
[src](https://www.databricks.com/blog/llm-inference-performance-engineering-best-practices).
That is not a regression — it is the roofline crossing over. As batch grows,
the step stops being bandwidth-bound and becomes compute-bound, so the
*bandwidth* utilisation falls while *throughput* rises. **MBU is therefore a
diagnostic, not an objective.** Optimising MBU directly pushes you toward batch
1. The objective is tokens/s/GPU subject to a TPOT SLO; MBU tells you *which
roofline you are against*, which tells you which knob moves you.

Practical rule: compute MBU **and** MFU on the same interval. The larger of the
two is your binding constraint.
- MBU high, MFU low → memory-bound: quantize weights, shrink KV, raise batch.
- MFU high, MBU low → compute-bound: this is prefill, or a batch already past
  the crossover; chunk prefill, add replicas, or accept it.
- Both mediocre → overhead-bound: kernel launches, scheduling, communication.
  This is the case §5 addresses.

Typical production values: see
[`../cross-cutting/serving-optimizations.md` §4.4](../cross-cutting/serving-optimizations.md)
for the corroborating signals and the standing ⚠️ that **no per-GPU-generation
MBU/MFU table exists for 2026 hardware anywhere in this tree**. The planning
bands (METHODOLOGY §4) remain: MBU 0.6–0.8 on H100/H200, 0.5–0.7 on first-gen
Blackwell software, 0.4–0.6 on MI355X/ROCm.

### 1.4 KV-cache utilisation and batch occupancy

Two scheduler-layer metrics that matter more day-to-day than either roofline
number, because they are the ones that *change* while traffic changes.

**KV utilisation** — fraction of the KV block pool in use. It is the direct
proxy for how close you are to preemption. vLLM's tuning guide is explicit that
preemption is the failure mode and lists the remedies: "Increase
`gpu_memory_utilization`… Decrease `max_num_seqs` or `max_num_batched_tokens`…
Increase `tensor_parallel_size`… Increase `pipeline_parallel_size`", and notes
"In vLLM V1, the default preemption mode is `RECOMPUTE` rather than `SWAP`, as
recomputation has lower overhead in the V1 architecture"
[src](https://github.com/vllm-project/vllm/blob/main/docs/configuration/optimization.md).
`RECOMPUTE` means a preemption throws away work — a KV-utilisation spike is a
throughput loss, not just a latency blip.

**Batch occupancy** — running requests ÷ the scheduler's ceiling
(`max_num_seqs`). Distinguishes "no traffic" from "traffic blocked on KV" from
"traffic blocked on the token budget". Paired with queue depth it is the single
most useful autoscaling signal, and is exactly what Dynamo's load-based mode
uses ([§2.3](#23-prefilldecode-pool-ratios)).

### 1.5 Metric names per engine, and PromQL recipes

**vLLM** exposes `vllm:*` Prometheus metrics; the reference list
[src](https://docs.vllm.ai/en/stable/design/metrics/):

| Metric | Type | Description (from docs) |
|---|---|---|
| `vllm:num_requests_running` | Gauge | "Number of requests currently running" |
| `vllm:num_requests_waiting` | Gauge | requests awaiting scheduling |
| `vllm:kv_cache_usage_perc` | Gauge | "Fraction of used KV cache blocks (0–1)" |
| `vllm:prefix_cache_queries` / `vllm:prefix_cache_hits` | Counter | "Number of prefix cache queries" / "hits" |
| `vllm:prompt_tokens_total` / `vllm:generation_tokens_total` | Counter | "Total number of prompt tokens processed" / "generated tokens" |
| `vllm:time_to_first_token_seconds` | Histogram | TTFT |
| `vllm:inter_token_latency_seconds` | Histogram | ITL / TPOT |
| `vllm:request_prefill_time_seconds` / `vllm:request_decode_time_seconds` | Histogram | per-phase time |
| `vllm:request_queue_time_seconds` | Histogram | queue time before scheduling |
| `vllm:kv_block_lifetime_seconds`, `vllm:kv_block_idle_before_evict_seconds`, `vllm:kv_block_reuse_gap_seconds` | Histogram | block allocation→eviction, idle-before-evict, reuse gap |
| `vllm:lora_requests_info` | Gauge | running/waiting LoRA adapter counts |
| ~~`vllm:spec_decode_draft_acceptance_rate`, `vllm:spec_decode_efficiency`, `vllm:spec_decode_num_{accepted,draft,emitted}_tokens`~~ | — | **Corrected 2026-09-19: NOT exported.** The metrics design doc lists the `spec_decode_*` family under **"Future Work"**, not under the implemented V1 surface [src](https://docs.vllm.ai/en/stable/design/metrics/). Do not build dashboards or recording rules on them |

⚠️ **TO BE VERIFIED per engine version**: the `vllm:gpu_cache_usage_perc` →
`vllm:kv_cache_usage_perc` rename between V0 and V1 has moved across releases
(the V1 name is the one the current design doc documents, verified
2026-09-19); the tree pins vLLM **0.29.0,
2026-09-09** ([`../cross-cutting/inference-engines.md` §2.1](../cross-cutting/inference-engines.md)).
Scrape `/metrics` on the deployed build before writing dashboards.

The three `kv_block_*` histograms are underused and are the right instrument
for [§4](#4-memory-tiering-for-kv): a short `kv_block_reuse_gap_seconds` with a
short `kv_block_lifetime_seconds` means you are evicting blocks that were about
to be reused — a capacity problem a host tier fixes.

**SGLang** exposes `sglang:*` (enable with `--enable-metrics`)
[src](https://docs.sglang.io/references/production_metrics.html). **Corrected 2026-09-19:**
the three `estimated_*` counters need a *second* flag — the page states "These
metrics are available when both `--enable-metrics` and `--enable-mfu-metrics`
are enabled." A deployment with only `--enable-metrics` exports no MBU/MFU
numerators at all:

| Metric | Type | Description |
|---|---|---|
| `sglang:num_running_reqs` | Gauge | "The number of running requests" |
| `sglang:num_queue_reqs` | Gauge | "The number of requests in the waiting queue" |
| `sglang:token_usage` | Gauge | "The token usage" (KV pool fraction) |
| `sglang:num_used_tokens` | Gauge | "The number of used tokens" |
| `sglang:cache_hit_rate` | Gauge | "The cache hit rate" |
| `sglang:gen_throughput` | Gauge | "The generate throughput (token/s)" |
| `sglang:prompt_tokens_total` / `sglang:generation_tokens_total` | Counter | prefill / generation tokens |
| `sglang:time_to_first_token_seconds`, `sglang:time_per_output_token_seconds`, `sglang:e2e_request_latency_seconds` | Histogram | latency |
| `sglang:spec_num_steps`, `sglang:spec_num_draft_tokens` | Gauge | active speculative config |
| **`sglang:estimated_flops_per_gpu_total`** | Counter | estimated FLOPs |
| **`sglang:estimated_read_bytes_per_gpu_total`** | Counter | estimated bytes read from memory |
| **`sglang:estimated_write_bytes_per_gpu_total`** | Counter | estimated bytes written |

The last three are the most valuable metrics in this section: **SGLang exports
the numerators of MFU and MBU directly** — provided **`--enable-mfu-metrics` is
also set** (see above), so both become one-line PromQL rather
than a model-specific derivation. ⚠️ Note the name is `estimated_*` — SGLang
computes these from its own model description, so they inherit whatever
assumptions that description makes (MoE expert-hit counting in particular);
treat them as `est.`, not `meas.`

⚠️ **Version gap**: SGLang v0.5.4+ changed the metric prefix from `sglang:` to
`sglang_`, per a community deployment guide
[src](https://kuncoro.io/blog/sglang-prometheus-metrics-guide/) — the tree pins
SGLang **0.5.20** ([`../cross-cutting/inference-engines.md` §2.2](../cross-cutting/inference-engines.md)),
so **expect `sglang_` underscores on the deployed build**. The official metrics
page above still documents the colon form. Verify on the running server. This
is a real dashboard-breaking discrepancy, not a cosmetic one.

**Dynamo** re-exports backend metrics and adds its own; `sglang` and `vllm`
backends both have documented Prometheus surfaces
[src](https://docs.nvidia.com/dynamo/latest/backends/sglang/prometheus.html).

**PromQL recipes** (`HBM_BW` and `PEAK_FLOPS` from METHODOLOGY §8; per-GPU
recording rules):

```promql
# --- SGLang: MBU and MFU directly -------------------------------------------
# B300: HBM_BW = 8.0e12 B/s ; FP4 dense peak = 13.5e15 FLOP/s  (METHODOLOGY §8)
record: gpu:mbu
expr:   rate(sglang_estimated_read_bytes_per_gpu_total[1m]) / 8.0e12

record: gpu:mfu
expr:   rate(sglang_estimated_flops_per_gpu_total[1m]) / 13.5e15

# --- vLLM: no byte counter, so derive steps/s then apply METHODOLOGY §4 ------
# steps_per_s = output tokens/s / (batch x accepted_tokens_per_step)
# NOTE (corrected 2026-09-19): vLLM does NOT export vllm:spec_decode_*; that
# family is "Future Work" in the metrics design doc. The acceptance factor must
# come from your own benchmark or from the engine log, as a static recording
# rule `engine:accepted_tokens_per_step`, not from a scraped counter.
record: engine:decode_steps_per_s
expr: |
  rate(vllm:generation_tokens_total[1m])
    / clamp_min(avg_over_time(vllm:num_requests_running[1m]), 1)
    / clamp_min(engine:accepted_tokens_per_step, 1)
# then: MBU = bytes_per_decode_step(batch) * engine:decode_steps_per_s / HBM_BW
# bytes_per_decode_step comes from METHODOLOGY §4 (MoE: distinct-expert formula)
# and the model's architecture.md -- it is NOT a constant, it scales with batch
# and with sum(ctx) via the KV term.

# --- scheduler layer, engine-agnostic ---------------------------------------
record: engine:kv_utilisation
expr:   vllm:kv_cache_usage_perc            # or sglang_token_usage

record: engine:batch_occupancy
expr:   vllm:num_requests_running / on(instance) group_left engine:max_num_seqs

record: engine:prefix_hit_rate
expr:   rate(vllm:prefix_cache_hits[5m]) / clamp_min(rate(vllm:prefix_cache_queries[5m]), 1)

# --- device layer -----------------------------------------------------------
record: gpu:tensor_active
expr:   DCGM_FI_PROF_PIPE_TENSOR_ACTIVE
record: gpu:sm_active
expr:   DCGM_FI_PROF_SM_ACTIVE
```

The vLLM MBU recipe's divisor is `engine:accepted_tokens_per_step`
(= `acceptance_rate × γ + 1`), with γ = 5 draft tokens this repo's pinned DSpark
default ([`../matrix/recommendations.md`](../matrix/recommendations.md)). ⚠️
Because vLLM exports no acceptance-rate metric, this constant is a measured
input you must refresh yourself — it is not scraped. Without the correction, MBU is
overstated by the acceptance factor — a 3.13× error at the DSpark operating
point.

**Alerting rule (recommended).** Page on the *pair*, never on either alone:

```promql
# Capacity exhaustion: KV nearly full AND a non-empty queue
engine:kv_utilisation > 0.90 and vllm:num_requests_waiting > 0

# Silent waste: engine busy, silicon idle -- the GPU_UTIL blind spot
gpu:sm_active > 0.8 and gpu:tensor_active < 0.05 and engine:batch_occupancy < 0.2
```

The 0.90 threshold is the standard operational band, not a measured optimum —
⚠️ **TO BE VERIFIED** against your own preemption-counter trace, since the right
value depends on your sequence-length variance.

---

## 2. Batching at the cluster level

Continuous batching maximises one replica. The cluster question is different:
**how many replicas, of what shape, and how is traffic split across them.**

### 2.1 Replica sizing: TP vs DP, and the one piece of algebra that decides it

Take a node of `G` GPUs, usable per-GPU memory `U` (METHODOLOGY §3:
`hbm_capacity × 0.90`), resident weight bytes `W` for the whole model,
activation workspace `ws`, tensor parallel degree `N`, so the node runs `G/N`
independent replicas.

**Aggregate decode throughput is, to first order, independent of `N`.** Each
GPU reads `W/N` weight bytes per step, so step time scales as `1/N`, and you
have `N`× fewer replicas: the two cancel. What does *not* cancel:

| Quantity | Scales with `N` | Direction |
|---|---|---|
| TPOT (per-user interactivity) | `∝ 1/N` | **higher TP is better** |
| All-reduce cost per layer | grows with rank count (METHODOLOGY §4: ~5–15 % for TP8 over NVLink, 20–40 % multi-node) | **lower TP is better** |
| Failure blast radius / rolling-upgrade granularity | `∝ N` | **lower TP is better** |
| Per-replica batch needed to reach the same occupancy | `∝ N` | **lower TP is better** at low traffic |
| **Aggregate KV capacity** | **depends on whether KV is sharded** | see below |

The KV term is the one that actually decides it, and it forks on a model
property, not a hardware one.

**Case A — KV is sharded across TP ranks** (GQA / MHA with
`n_kv_heads ≥ N`). Aggregate node KV is `G × (U − W/N − ws)`, which *rises*
monotonically with `N`. Higher TP wins on capacity **and** on TPOT; the only
cost is communication. Stop at `N = n_kv_heads`: past that, TP replicates KV
heads and you get Case B's economics with Case A's communication bill. This is
exactly why this tree says Qwen3.8-27B "stops buying \[KV budget] past TP4
because the model has 4 KV heads (TP8 replicates them ×2)"
([`../matrix/pairs.json`](../matrix/pairs.json), `qwen3827b/b300`), and why
Marlin-2B "saturates at TP2 because `num_key_value_heads=2`" (same file).

**Case B — KV is replicated on every rank** (MLA without DCP or DP-attention;
`num_key_value_heads = 1`). METHODOLOGY §3 pins the reading: with replicated KV
you use the **single-GPU** budget and do not multiply by `n_gpus`. So one
TP-`N` replica supports `(U − W/N − ws) / per_seq` sequences, and `G/N` replicas
of TP-`N` support `(G/N) × (U − W/N − ws) / per_seq`.

Comparing TP `N` against TP `2N` on the same node, halving TP wins on aggregate
seats when

```
2 × (U − W/N − ws)  >  (U − W/(2N) − ws)
⟺  U − ws  >  (3/2) × (W/N)
⟺  W/N  <  (2/3) × (U − ws)
```

**Rule: for a replicated-KV model, halve TP whenever the per-GPU weight share
at the *lower* TP still sits below two-thirds of the usable budget.** Below
that line you gain aggregate KV seats by splitting; above it you lose them.

*Worked, this repo's B300 node.* `U = 0.90 × 268 GB = 241.2 GB` (METHODOLOGY
§8), `ws ≈ 5 GB`, so the threshold is `(2/3) × 236.2 = 157.5 GB`. For
DeepSeek-V4.1-Flash on B300 the Engram tables are host-offloaded (vLLM's
documented B300 default is `--engram-config '{"cpu_offload":true}'`,
[`../matrix/pairs.json`](../matrix/pairs.json) `deepseek41f/b300`), leaving
resident weights of `510.29 − 202.76 = 307.53 GB` (METHODOLOGY §8 checkpoint
size minus the Engram figure from the `deepseek41f/h100` row):

| TP | `W/N` GB | below 157.5 GB? | replicas / node | per-GPU KV budget GB |
|---:|---:|---|---:|---:|
| 8 | 38.4 | ✓ | 1 | 197.8 |
| 4 | 76.9 | ✓ | 2 | 159.3 |
| 2 | 153.8 | ✓ (barely) | 4 | 82.4 |
| 1 | 307.5 | ✗ — does not fit | — | — |

Aggregate seats scale as `(G/N) × budget`: 197.8 (TP8) → 318.6 (2×TP4) → 329.7
(4×TP2), i.e. **TP2 replicas give ~1.67× the aggregate KV of a single TP8**. The
repo reaches the same conclusion independently and from measurement: the
`deepseek41f/b300` row records "4 independent TP2 replicas per 8-GPU node is the
max-throughput shape", and its measured operating points are **2,656
tok/s/GPU at 2 GPUs (max-throughput)** versus **1,373 tok/s/GPU at 4 GPUs
(interactive)** — per-GPU throughput roughly doubling as TP halves
([`../models/deepseek41f/b300.md`](../models/deepseek41f/b300.md),
[`../matrix/pairs.json`](../matrix/pairs.json); `confidence: measured`).

**The trade you are making** is TPOT for aggregate capacity. TP2's step time is
4× TP8's for the same batch; you claw it back by running 4× the batch across 4
replicas. That is fine for a throughput pool and wrong for an interactive pool.
Hence the standard shape: **two pools of different TP on the same node type**,
not one compromise TP. See [§8](#8-worked-example-a-utilisation-plan-for-this-repos-nodes).

**The escape hatch from Case B** is DCP (decode context parallelism), which
shards replicated MLA KV by token position and restores Case A's economics —
`vllm serve --tensor-parallel-size N --decode-context-parallel-size M`, with
measured 6,091 vs ~1,863 tok/s/GPU at c=512 on Kimi-K2.6 NVFP4 / 8×B200. Full
treatment and limits in
[`../cross-cutting/serving-optimizations.md` §3.2](../cross-cutting/serving-optimizations.md).
Where DCP is available, use it and go back to Case A: this is why the repo's
`kimik3/b300` shape is `TP8 + DCP8` on one node
([`../matrix/pairs.json`](../matrix/pairs.json)).

### 2.2 Data-parallel attention for MoE

For a sparse MoE the replica-shape question changes again, because the two
halves of the model want opposite things: attention wants **replication** (its
weights are small, its KV is what is scarce), experts want **sharding** (their
weights are enormous, their per-token work is tiny).

vLLM's answer is DP-attention + EP, with the EP size derived automatically
[src](https://github.com/vllm-project/vllm/blob/main/docs/serving/expert_parallel_deployment.md):

> `EP_SIZE = TP_SIZE × DP_SIZE`

and the layer behaviour spelled out:

| Layer type | Behaviour | Parallelism |
|---|---|---|
| Expert (MoE) layers | "Sharded across all EP ranks" | EP of size `TP × DP` |
| Attention, `TP = 1` | "Attention weights are **replicated** across all DP ranks" | DP |
| Attention, `TP > 1` | "sharded using tensor parallelism across TP ranks within each DP group" | TP within DP |

The canonical single-node shape, quoted from the same doc:

```bash
# 1-way TP, 8-way (attention) data parallel, 8-way expert parallel
vllm serve deepseek-ai/DeepSeek-V3-0324 \
    --tensor-parallel-size 1 \
    --data-parallel-size 8 \
    --enable-expert-parallel
```

and the key note: "Without `--enable-expert-parallel`, MoE layers would use
tensor parallelism (forming a TP group of size `TP × DP`), similar to dense
models."

**Why this raises cluster utilisation and not just replica throughput.** The
DeepSeek inference-system writeup states the mechanism: "EP significantly
scales the batch size, enhancing GPU matrix computation efficiency and boosting
throughput" and "EP distributes experts across GPUs, with each GPU processing
only a small subset of experts (reducing memory access demands), thereby
lowering latency" — with the sparsity argument made explicitly: "where only 8
out of 256 experts per layer are activated—the model's high sparsity
necessitates an extremely large overall batch size. This ensures sufficient
batch size per expert"
[src](https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md).

That last sentence is the cluster-level insight: **for a sparse MoE, batch size
is not a per-replica tuning knob, it is a fleet-sizing constraint.** An expert
that sees 3 tokens per step runs a 3-row GEMM. The cluster-wide batch has to be
large enough that `batch × top_k / n_experts` is a respectable GEMM width, and
the only ways to get there are (a) more concurrent traffic, (b) wider EP so each
GPU holds fewer experts, or (c) fewer replicas holding more of the traffic
each. Splitting traffic across many narrow MoE replicas is the *opposite* of
what the arithmetic wants, and is the most common way to hold an MoE fleet
wrong.

Cost of DP-attention: attention weights replicate per rank. Measured on
Kimi-K3, "~61 GB (KDA) + 11 GB (MLA) of replicated weight"
([`../cross-cutting/serving-optimizations.md` §3.1](../cross-cutting/serving-optimizations.md),
citing LMSYS). That is memory you are not spending on KV — the trade is
explicit.

### 2.3 Prefill/decode pool ratios

Once prefill and decode run in separate pools, the ratio is a capacity-planning
variable. The honest summary of the literature is that **the published optima
disagree by almost an order of magnitude, because they are measuring different
workloads**:

| Source | Hardware / model | Workload | P:D ratio | Result |
|---|---|---|---|---|
| DistServe, OSDI '24 [src](https://www.usenix.org/system/files/osdi24-zhong-yinmin.pdf) | simulation + real | ISL 512 / OSL 64 (prefill-heavy) | **2:1** | met both TTFT and TPOT targets; overall "7.4× more requests or 12.6× tighter SLO" vs the state of the art |
| dstack, 2025-09-25 [src](https://dstack.ai/blog/benchmarking-pd-ratios/) | 8×H200 SXM5, gpt-oss-120b, SGLang | ISL>OSL, ISL<OSL, ISL≈OSL | **1:3** | "1:3 again leads across all metrics" at higher concurrency; "1:3 is the safer default" even for balanced |
| DeepSeek production [src](https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md) | H800, DeepSeek-V3/R1 | real traffic | **4 nodes : 18 nodes ≈ 1:4.5** | "Prefilling Phase \[Routed Expert EP32, MLA/Shared Expert DP32]: Each deployment unit spans 4 nodes"; "Decoding Phase \[Routed Expert EP144, MLA/Shared Expert DP144]: Each deployment unit spans 18 nodes" |
| SGLang on 96×H100 [src](https://www.lmsys.org/blog/2025-05-05-large-scale-ep/) | DeepSeek-V3 | ISL 2K | **4 nodes : 9 nodes ≈ 1:2.25** | 52.3k input tok/s and 22.3k output tok/s per node |
| Kimi-K3, GB300 [src](https://www.lmsys.org/blog/2026-07-27-kimi-k3-day0-support/) | PP8 prefill + DCP8/TP8 decode | — | "one prefill node feeds several decode nodes" | PP8 prefill node has "1.45–1.72× the prefill capacity of a TEP8 node" (via [`../cross-cutting/serving-optimizations.md` §3.2](../cross-cutting/serving-optimizations.md)) |

**Do not copy a ratio. Derive it.** The ratio is determined by your traffic's
token mix and your measured per-pool rates:

```
prefill_gpus : decode_gpus
  = (λ × ISL × (1 − cache_hit_rate)) / prefill_tok_per_s_per_gpu
  : (λ × OSL) / decode_tok_per_s_per_gpu

λ = requests/s ; ISL/OSL = mean input/output tokens
```

Two terms are routinely got wrong:
- **`(1 − cache_hit_rate)`.** DeepSeek's own traffic had "342B tokens (56.3%)
  hit the on-disk KV cache"
  [src](https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md).
  Sizing a prefill pool without the cache term over-provisions it by ~2.3× on
  that workload. On agentic traffic the hit rate is higher still — Mooncake's
  Codex trace went from 1.7 % to 92.2 % hit rate purely by making the cache
  cross-instance ([`../cross-cutting/serving-optimizations.md` §1.4](../cross-cutting/serving-optimizations.md)).
- **`prefill_tok_per_s_per_gpu`** must be *measured*, not roofline'd. This tree
  has a hard-won ⚠️ on exactly this: GEMM-only prefill rooflines are **~13.4×
  optimistic** for the sparse/indexer-attention model class — 9,267 measured vs
  123,750 roofline tok/s/GPU for DeepSeek-V4.1-Flash on B200
  ([`../README.md` open question 5](../README.md),
  [`../models/deepseek41f/b200.md` §3.1](../models/deepseek41f/b200.md)). A P:D
  ratio built on the roofline would under-provision prefill by an order of
  magnitude.

**Fixed or dynamic?** dstack tested exactly this and concluded that "Across all
workload profiles and concurrency levels, a fixed ratio delivered robust
performance" and that "a fixed ratio combined with standard autoscaling can
achieve similar outcomes with simpler orchestration"
[src](https://dstack.ai/blog/benchmarking-pd-ratios/). **Recommendation: start
fixed, at the ratio your own formula gives, and let ordinary replica
autoscaling absorb drift.** Move to a dynamic controller only when you have
measured that the ratio your traffic wants swings by more than ~2× across the
day.

The dynamic option, when you get there, is Dynamo's Planner, which runs two
loops [src](https://docs.nvidia.com/dynamo/knowledge-base/modular-components/planner/overview):

| Field | Default | Meaning |
|---|---|---|
| `ttft_ms` | `500.0` | "Target Time To First Token (ms)" |
| `itl_ms` | `50.0` | "Target Inter-Token Latency (ms)" |
| `throughput_adjustment_interval_seconds` | `180` | long-term, prediction-driven capacity |
| `load_adjustment_interval_seconds` | `5` | short-term, reacts to ForwardPassMetrics |
| `load_predictor` | `arima` | `arima`, `prophet`, `kalman`, `constant` |
| `prefill_scale_up_queue_tokens` / `..._down_...` | — | "Queue token thresholds for `optimization_target: load` prefill scaling" |
| `decode_scale_up_kv_rate` / `..._down_...` | — | "Decode KV utilization thresholds" |

with the documented division of labour: "When both modes are enabled,
throughput-based scaling provides a capacity floor (long-term planning) while
load-based scaling handles real-time adjustments above that floor", and the best practice "use a longer
`throughput_adjustment_interval_seconds` than
`load_adjustment_interval_seconds`". Note that the two load signals are exactly
[§1.4](#14-kv-cache-utilisation-and-batch-occupancy)'s two metrics: **prefill
scales on queue depth, decode scales on KV utilisation.** ⚠️ The docs do not
publish the closed-form replica formula — they reference "the Rust engine perf
model" using "native AIC estimates, online FPM tuning, and FPM regression
fallback" — so the mapping from SLO to replica count is not reproducible from
documentation alone. Predictive scaling proper belongs to the sibling
autoscaling document.

### 2.4 Operating points, and why autoscaling must not drift them

[`../cross-cutting/serving-optimizations.md` §4.3](../cross-cutting/serving-optimizations.md)
establishes the single most important framing in this whole area: Kimi-K3 on
GB300 NVL72 spans "high-throughput serving at 2K+ TPGS to low-latency serving
at 100+ TPS/user" on one Pareto curve — a ~20× throughput range for one model
on one platform — and concludes **"Picking the operating point matters more
than picking the GPU."**

At cluster level this has a corollary that is easy to violate: an operating
point is a *pinned* `(max_num_seqs, max_num_batched_tokens, cudagraph capture
size, TP, spec-decode γ)` tuple, and a replica pool should have exactly one.
The failure mode is an autoscaler that adds replicas under load while the
existing replicas' batch keeps growing — you slide along the Pareto curve
toward throughput at exactly the moment your users are complaining about
latency. **Cap `max_num_seqs` at the SLO-satisfying batch and let the
autoscaler add replicas**; do not let batch be the overflow valve. That is what
makes replica count a meaningful control signal at all.

---

## 3. Filling idle capacity

Everything above maximises a busy replica. The remaining waste is *time*: an
interactive fleet sized for peak is, by construction, heavily idle off-peak.

### 3.1 Two traffic classes, one fleet

The market price of latency tolerance is public and remarkably consistent:
OpenAI's Batch API is documented at a **50 % discount** with a 24-hour
completion window
[src](https://developers.openai.com/api/docs/guides/batch), and Anthropic's
Message Batches API likewise offers **50 % off** input and output tokens
[src](https://platform.claude.com/docs/en/build-with-claude/batch-processing).
Both frame it the same way: half price in exchange for asynchrony.

Internally the same arbitrage exists and is larger, because you already own the
GPUs. The implementation ladder, cheapest first:

**(a) Priority classes inside one engine.** vLLM implements priority
scheduling: requests are ordered by a user-supplied priority (lower value =
earlier), ties broken FCFS, with forced preemption — "All requests in the
running queue and the waiting queue are sorted first based on this priority. If
there is a tie, it falls back to the FCFS policy" and "If there are requests in
the running queue whose priority is lower than the requests in the waiting
queue, they are forcefully preempted out back into the waiting queue to allow
immediate execution of the higher priority request"
[src](https://github.com/vllm-project/vllm/pull/5958). The measured cost, from
the same PR: "Performance slowdown from `_schedule_priority_preemption` is <4%
with the priority policy for Llama 8B. No performance degradation when policy is
not enabled" — the posted throughput pair is **14.56 req/s (priority) vs
15.15 req/s (FCFS) = 3.9 %**
[src](https://github.com/vllm-project/vllm/pull/5958).

**Corrected 2026-09-19:** this document previously cited RFC issue #6077 for all
of the above. The issue body contains the *motivation* only — none of these
quotes and no percentage appear in it. The implementing **PR #5958** is the
primary source and is what is cited now.

That 3.9 % figure is the whole argument. **Run the batch tier as low-priority
requests on the interactive replicas.** You pay ~4 % of interactive throughput
and recover the entire idle fraction — for a fleet at 45 % average utilisation
that is a >2× improvement in useful work per GPU-hour, against a 4 % tax. No
extra hardware, no extra pool, no scheduler integration.

Caveat that decides whether this is free or not: vLLM V1 preempts by
`RECOMPUTE`, not `SWAP`
[src](https://github.com/vllm-project/vllm/blob/main/docs/configuration/optimization.md).
A preempted batch request loses its decode progress. For long-output batch jobs
this turns into repeated wasted work; keep batch-tier `max_tokens` modest, or
accept the churn. ⚠️ **TO BE VERIFIED** — no published measurement of
recompute-churn cost for a long-output low-priority tier.

**(b) Diurnal repurposing at node granularity.** DeepSeek's published practice
is the reference implementation: "due to high service load during the day and
low load at night, we implemented a mechanism to deploy inference services
across all nodes during peak daytime hours. During low-load nighttime periods,
we reduce inference nodes and allocate resources to research and training."
The measured swing: **"the combined peak node occupancy for V3 and R1 inference
services reached 278, with an average occupancy of 226.75 nodes"**
[src](https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md).

That is a peak:mean ratio of **1.226** — i.e. even a fleet that actively
harvests its trough only recovers ~18 % of peak capacity at night. Useful
calibration against optimistic "we'll train on the idle GPUs" plans: the
headroom in a *well-managed* inference fleet is real but not enormous, and
DeepSeek's number is the best public data point on its size.

**(c) Cluster-level preemptible queues.** Kueue admits workloads against quota
*before* their pods exist, and supports cohort borrowing plus preemption:
`reclaimWithinCohort` "determines whether a pending Workload can preempt
Workloads from other ClusterQueues in the cohort that are using more than their
nominal quota", with `LowerPriority` (preempt only lower-priority cohort
workloads) or `Any` [src](https://kueue.sigs.k8s.io/docs/concepts/cluster_queue/).
This is the right mechanism for "training and batch jobs may use inference's
nominal quota, and must give it back within one gang". Volcano and Run:ai
occupy the same slot with different ergonomics.

**Decision rule.** Use (a) when the batch work is the *same model* — it is
nearly free and needs no infrastructure. Use (b)+(c) when the batch work is a
*different* workload (training, evals, a different model), because then you
need whole GPUs back, not scheduler priority.

### 3.2 Request coalescing

Two distinct things get called this, and only one is well-supported:

- **Prefix-level coalescing** — two requests sharing a prefix computing it
  once. This is prefix caching and it is solved; see
  [`../cross-cutting/serving-optimizations.md` §1](../cross-cutting/serving-optimizations.md)
  and [§4](#4-memory-tiering-for-kv) below. The cluster-level version is
  routing requests *to the replica that already holds the prefix* — the
  Gateway API Inference Extension's Endpoint Picker does prefix-cache-aware and
  LoRA-affinity-aware selection rather than round-robin
  [src](https://gateway-api-inference-extension.sigs.k8s.io/). ⚠️ **TO BE
  VERIFIED — re-fetched 2026-09-19, the cited landing page does not carry this
  text.** It defines the EPP only as "An implementation of an `Inference
  Router`" that "will fetch metrics from whichever portion of the InferencePool
  endpoints can best achieve the configured objectives". The prefix-cache and
  LoRA-affinity behaviour is documented on subpages that were not opened; cite
  the subpage, not the root, before relying on it. The same ⚠️ applies to the
  two other quotations from this URL ([§3.4](#34-lora-multiplexing),
  [§6.2](#62-expert-placement-and-eplb-policies)). Routing belongs
  to the sibling routing document; the utilisation consequence is
  [§4.4](#44-hit-rate-economics)'s.
- **Identical-request deduplication** — collapsing byte-identical concurrent
  requests into one generation, fanned out to all callers. ⚠️ **TO BE VERIFIED**
  — no production inference stack found that ships this as a feature, and it is
  only safe at temperature 0 with identical sampling parameters. Given prefix
  caching already recovers the prefill, the remaining saving is decode-only and
  applies to a narrow slice of traffic. **Recommendation: do not build it**
  unless you can show a measured duplicate rate above a few percent.

### 3.3 Multi-model consolidation on one GPU

For Marlin-2B (5.444 GB BF16) and Qwen3.8-27B (30.87 GB FP8) on a 268 GB B300,
one model per GPU leaves most of the card unused at low traffic. Three
mechanisms, with very different properties:

| Mechanism | Isolation | Granularity | Concurrency model | Verdict for this repo |
|---|---|---|---|---|
| **MIG** | hardware memory + fault isolation | fixed profiles | true spatial partition | best for guaranteed small-model capacity |
| **MPS** | none (trusted workloads) | soft | concurrent kernel submission | best for bursty trusted co-tenancy |
| **Time-slicing** | none | soft | rotation | **not for latency-SLO inference** |

NVIDIA's GPU Operator documentation states the time-slicing caveat plainly:
**"Unlike Multi-Instance GPU (MIG), there is no memory or fault-isolation
between replicas"**, and adds the one people get wrong: **"A request for more
than one time-sliced GPU does not guarantee that the pod receives access to a
proportional amount of GPU compute power"**
[src](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-sharing.html).
The configuration is a ConfigMap:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: time-slicing-config
data:
  any: |-
    version: v1
    flags:
      migStrategy: none
    sharing:
      timeSlicing:
        renameByDefault: false
        failRequestsGreaterThanOne: false
        resources:
          - name: nvidia.com/gpu
            replicas: 4
```

Time-slicing adds *context-switch* latency to every request and gives no memory
isolation, so an OOM in one tenant takes the other down. For an inference fleet
with a TPOT SLO it is the wrong tool; it earns its place for dev/notebook
pools.

**MIG profiles.** NVIDIA's supported-profiles table lists B200
[src](https://docs.nvidia.com/datacenter/tesla/mig-user-guide/supported-mig-profiles.html):

| Profile | Memory fraction | Max instances | SM fraction |
|---|---|---:|---|
| `MIG 1g.23gb` | 1/8 | 7 | 1/7 |
| `MIG 1g.23gb+me` | 1/8 | 1 | 1/7 |
| `MIG 1g.45gb` | 2/8 | 4 | 1/7 |
| `MIG 2g.45gb` | 2/8 | 3 | 2/7 |
| `MIG 3g.90gb` | 4/8 | 2 | 3/7 |
| `MIG 4g.90gb` | 4/8 | 1 | 4/7 |
| `MIG 7g.180gb` | Full | 1 | Full |

⚠️ **TO BE VERIFIED — B300 is not listed on that page.** The profiles page
covers B200, RTX PRO 6000/5000/4500 Blackwell, Thor iGPU, H100, H200, A100 and
A30 as of the fetch on 2026-09-19. **Do not assume B300 MIG geometry by scaling
B200's 180 GB table to 268 GB.** The estimation method if you must plan
provisionally: Blackwell MIG slices memory in eighths and SMs in sevenths, so a
`1g` slice on a 268 GB B300 would be ~33.5 GB — but slice counts and the
`+me` (media engine) variants are silicon-specific and must be read off
`nvidia-smi mig -lgip` on the actual card.

**What fits where, on B200's published geometry** (weights from METHODOLOGY §8;
KV budget is what is left after weights and ~2 GB workspace):

| Model | Weights | Smallest B200 MIG slice that fits | Headroom for KV |
|---|---|---|---:|
| Marlin-2B BF16 | 5.444 GB | `1g.23gb` | ~15.5 GB → **~129 seats** at 8K `est.` (12 KiB/token + 18.63 MiB GDN state/seq) |
| Qwen3.8-27B FP8 | 30.87 GB | `3g.90gb` | ~57 GB → **~86 seats** at 8K `est.` (32 KiB/token FP8 + 392.2 MB GDN state/seq) |
| Qwen3.8-27B NVFP4 | 21.92 GB | `1g.45gb` | ~21 GB | 
| DeepSeek / Kimi | ≫ 180 GB | none — needs multi-GPU | — |

**Corrected 2026-09-19.** The two seat counts previously printed here (~1,290
and ~1,700) were arithmetically wrong by ~10× and ~20×: they divided the
headroom by one *kilo-token* of KV rather than by the 8K context the column
header claims, and omitted the per-sequence state entirely. Recomputed with
`python3` per METHODOLOGY §3
(`seats = headroom / (ctx × kv_bytes_per_token + fixed_state)`):
`15.56e9 / (8192×12,288 + 19.54e6) = 129` and
`57.13e9 / (8192×32,768 + 392.2e6) = 86`. Ignoring the per-seq state entirely
still only gives 155 and 213 — the error was the context term, and the state
term then roughly halves Qwen's result again.

`est.` per METHODOLOGY §3; these are capacity checks only, not throughput
claims — a MIG slice gets a fraction of the SMs *and* a fraction of the HBM
bandwidth, so decode throughput on `1g` is roughly 1/7 of the full card's, and
the per-seq state (Qwen3.8-27B's 392.2 MB GDN state per request, METHODOLOGY §8)
eats into the seat count sharply.

**Decision rule.** MIG when you need a *guaranteed* floor per tenant/model and
the model fits a slice — its hardware isolation is the only one that survives a
misbehaving co-tenant. MPS when the co-tenants are your own trusted services
with bursty, low-concurrency traffic and you want the whole card's bandwidth
available to whoever needs it. Neither when a single model can fill the card:
consolidation that halves each model's bandwidth to avoid an idle GPU is a bad
trade whenever you could instead have put more *traffic* on one model
([§3.1](#31-two-traffic-classes-one-fleet)).

### 3.4 LoRA multiplexing

When the variation between "models" is adapters rather than base weights,
multiplexing is dramatically cheaper than consolidation — one base model, many
adapters, one KV pool.

vLLM serves this with `--enable-lora`, `--max-loras` (adapters resident per
batch) and `--max-cpu-loras` (adapters held in host RAM, swapped in on demand)
[src](https://docs.vllm.ai/en/latest/features/lora/). The reported overhead is
"under 5%" even with LoRA on all seven linear layers per block, but with the
important qualifier that "as the number of unique LoRAs increases, vLLM
multi-LoRA throughput degrades as it needs to schedule the requests and load and
offload the adapters" [src](https://blog.squeezebits.com/37065). S-LoRA, whose
techniques vLLM adopted, reports "throughput up to 4x higher than vLLM-packed
when serving a small number of adapters, and up to 30x higher than PEFT, while
supporting a significantly larger number of adapters"
[src](https://arxiv.org/pdf/2311.03285).

**The cluster-level piece is affinity routing.** Adapter swap cost is per-miss,
so a round-robin router across N replicas multiplies the swap rate by N. The
Endpoint Picker's "adapter affinity for models using LoRA adapters, where
requests are preferentially routed to servers that already have the required
adapter loaded" is what makes `--max-loras` small-and-hot instead of
large-and-thrashing [src](https://gateway-api-inference-extension.sigs.k8s.io/).

**Applicability here: low.** None of this repo's five models is an adapter
family — they are five distinct base models with incompatible architectures.
LoRA multiplexing is the right answer for a per-customer-fine-tune product, not
for this fleet. Recorded for completeness; do not build it speculatively.

---

## 4. Memory tiering for KV

[`../cross-cutting/serving-optimizations.md` §1.4](../cross-cutting/serving-optimizations.md)
lists the five implementations (vLLM native + tiered, LMCache, Dynamo KVBM,
Mooncake Store) and their measured TTFT/throughput effects. This section adds
the three things that document leaves open: the bandwidth ladder, the
break-even arithmetic, and what the hit-rate economics do to
[§2.3](#23-prefilldecode-pool-ratios)'s pool sizing.

### 4.1 The tier ladder, with names

The four systems all converge on the same hierarchy with different labels:

| Tier | Dynamo KVBM | SGLang HiCache | LMCache | Shared across replicas? |
|---|---|---|---|---|
| GPU HBM | **G1** device pool | **L1** (GPU) | GPU | no |
| Host DRAM (pinned) | **G2** host pool | **L2** (host) | CPU | no |
| Local NVMe | **G3** disk pool | (via L3 `file`) | NVMe | no |
| Remote / object / RDMA pool | **G4** remote | **L3** (mooncake, hf3fs, nixl, aibrix) | remote | **yes** |

KVBM's tiering is documented as: "KVBM tracks KV block locations across device
memory (G1), CPU memory within and across nodes (G2), local/pooled SSDs (G3),
and remote storage (G4)", with G3 using "NIXL descriptors \[to] expose file
offsets/regions for zero-copy I/O and optional GDS", and offload policies
`offload.g1_to_g2` / `offload.g2_to_g3` taking `pass_all` / `presence` /
`presence_lfu` predicates
[src](https://docs.nvidia.com/dynamo/latest/architecture/kvbm_components.html).
⚠️ **TO BE VERIFIED — the cited URL 404s as of 2026-09-19** (so does
`.../architecture/kvbm_intro.html`). The G1–G4 tier model itself is corroborated
in-tree at
[`../cross-cutting/serving-optimizations.md` §1.4](../cross-cutting/serving-optimizations.md),
which cites `docs.nvidia.com/dynamo/v1.3.0/user-guides/kv-cache-offloading`; the
`offload.g1_to_g2` / `pass_all` / `presence_lfu` config names are **not**
confirmed against any reachable page. Re-source before writing them into a
config.

HiCache draws the sharing boundary explicitly: "L1 and L2 KV caches are private
to each inference instance, whereas the L3 KV cache is shared among all
inference instances within the cluster"
[src](https://docs.sglang.io/advanced_features/hicache_design.html). Its flags:

| Flag | Meaning |
|---|---|
| `--enable-hierarchical-cache` | turn HiCache on |
| `--hicache-ratio` | "Ratio of host to device KV cache memory (must exceed 1)" |
| `--hicache-size` | "Host KV cache pool size in gigabytes per rank" |
| `--hicache-storage-backend` | `file`, `mooncake`, `hf3fs`, `nixl`, `aibrix`, `dynamic` |
| `--hicache-write-policy` | `write_through` / `write_through_selective` / `write_back` |
| `--hicache-io-backend` | `direct` or `kernel` (CPU↔GPU transfer method) |
| `--page-size` | token granularity for storage operations |

with the write policies defined as: `write_through` — "Every access is
immediately written back to the next level"; `write_through_selective` — "Data
is written back only after the access frequency exceeds a threshold";
`write_back` — "Data is written back to the next level only when it is evicted"
[src](https://docs.sglang.io/advanced_features/hicache_design.html). Prefetch
termination is `best_effort` / `wait_complete` / `timeout`.

**Policy recommendation:** `write_through_selective` for a shared L3, because
an unconditional write-through of every prefill's KV to a network tier is a
bandwidth amplifier that mostly stores blocks nobody reuses. Dynamo makes the
same call by default — its disk-offload filtering is documented as "frequency
≥ 2 … on by default to spare SSD endurance"
([`../cross-cutting/serving-optimizations.md` §1.4](../cross-cutting/serving-optimizations.md)).

### 4.2 The bandwidth ladder

| Path | Bandwidth | Source / basis |
|---|---:|---|
| B300 HBM3e | **8.0 TB/s** | METHODOLOGY §8 |
| NVLink within an 8-GPU HGX node | scale-up fabric | [`../gpus/b300.md`](../gpus/b300.md) |
| GB300 NVL72 NVLink domain | **130 TB/s** aggregate | [`../cross-cutting/serving-optimizations.md` §3.4](../cross-cutting/serving-optimizations.md) |
| GPU ↔ host, measured DMA | **83.4 GB/s** bidirectional (68.5 with custom CUDA kernels; ~50 single-direction) | H100 + Xeon Sapphire Rapids, 500 GB DRAM, 2 MB blocks [src](https://vllm.ai/blog/2026-01-08-kv-offloading-connector) |
| Local NVMe (Gen5) | ~7–14 GB/s per drive | ⚠️ **TO BE VERIFIED** — no primary source fetched; vendor-class figure, scale by drive count |
| RDMA to a remote KV pool | link-rate (400 Gb/s ≈ 50 GB/s per NIC) | ⚠️ **TO BE VERIFIED** — nominal, not measured on this fabric |

Two facts to hold onto: **HBM to host is ~96×, host to NVMe another ~8×**
(8,000 GB/s → 83.4 GB/s → ~10 GB/s; recomputed 2026-09-19 — the earlier
"roughly two orders of magnitude per step" was right for the first step and
wrong for the second), and **the measured GPU↔host number is
sensitive to transfer granularity** — vLLM's own 0.11.0→0.12.0 jump of "4×
lower TTFT, 5× throughput" came "from moving physical block size from KB to
0.5–2 MB" ([`../cross-cutting/serving-optimizations.md` §1.4](../cross-cutting/serving-optimizations.md),
citing the same blog). A tiering deployment with a small `--page-size` will
measure a fraction of the 83.4 GB/s and conclude, wrongly, that offload does
not work.

### 4.3 The break-even that actually matters is not bandwidth

The intuitive break-even — "offload pays when loading the KV is faster than
recomputing it" — is real but is essentially never the binding constraint. Set
the two times equal:

```
load_time(T)      = T × kv_bytes_per_token / transfer_BW
recompute_time(T) = T / prefill_tokens_per_s_per_gpu       (measured, not roofline)

offload wins  ⟺  kv_bytes_per_token / transfer_BW  <  1 / prefill_tok_per_s
              ⟺  prefill_tok_per_s  <  transfer_BW / kv_bytes_per_token
```

The right-hand side is "how many tokens per second of KV the link can deliver".
Evaluated at the measured host DMA rate of 83.4 GB/s:

| Model | `kv_bytes_per_token` (METHODOLOGY §8) | KV tokens/s the link delivers | Measured/est. prefill tok/s/GPU | Margin |
|---|---:|---:|---:|---:|
| DeepSeek-V4.1-Flash on B300 | 890 B (FP4 KV, sm_103) | 93.7 M | **9,267 `meas.`** (B200, [`../models/deepseek41f/b200.md` §3.1](../models/deepseek41f/b200.md)) | **~10,100×** |
| DeepSeek-V4.1-Flash on H100 | 3,200 B (BF16, measured pool) | 26.1 M | ⚠️ unmeasured | ≫ 10³× |
| Kimi-K3 | 13.5 KiB (13,824 B) | 6.03 M | ⚠️ unmeasured | ≫ 10³× |
| Qwen3.8-27B FP8 | 32 KiB (32,768 B) | 2.54 M | ~24,300 `est.` (roofline, METHODOLOGY §4 at MFU 0.30) | **~105×** |
| Marlin-2B BF16 | 12 KiB (12,288 B) | 6.79 M | high (2.21 B active) | ≫ 10² × |

**Conclusion: bandwidth is never the reason not to tier.** Even the worst case
here — Qwen3.8-27B, whose KV is 37× fatter per token than DeepSeek's MLA cache
and whose prefill is cheap because it is a small dense model — clears the bar by
two orders of magnitude. The break-even bandwidth for Qwen3.8-27B is
`24,300 × 32,768 = 0.80 GB/s`; anything faster than a single SATA SSD wins.

What actually decides it:

1. **Hit rate.** A tier you never hit costs write bandwidth and buys nothing.
   See [§4.4](#44-hit-rate-economics).
2. **Capacity × reuse window.** The tier must be large enough to still hold the
   block when the next turn arrives. vLLM's `kv_block_reuse_gap_seconds`
   histogram ([§1.5](#15-metric-names-per-engine-and-promql-recipes)) versus
   your tier's eviction age is the exact test.
3. **Latency, not throughput.** A 2 MB transfer at 83.4 GB/s takes 24 µs, but
   the *round trip* through a scheduler, a lookup and a network tier is
   milliseconds. This is why HiCache's `best_effort` prefetch policy exists —
   "Terminates immediately when GPU can execute prefill computation"
   [src](https://docs.sglang.io/advanced_features/hicache_design.html) — and why
   it is the right default for an interactive pool.
4. **SSD endurance**, on the G3/NVMe tier. Hence Dynamo's frequency ≥ 2 filter.

### 4.4 Hit-rate economics

METHODOLOGY §6 pins the cost model: a prefix-cache hit "skips prefill compute
and costs only the KV load / reuse — assume 10 % of the uncached prefill cost
unless measured". So the saving from a hit rate `h` on the input side is
`0.9 × h` of prefill cost, and the corresponding prefill-pool sizing shrinks by
the same factor ([§2.3](#23-prefilldecode-pool-ratios)).

The published hit rates that matter, all already collated in
[`../cross-cutting/serving-optimizations.md` §1.4](../cross-cutting/serving-optimizations.md)
— the cluster-level reading of them is:

| Hit rate | Setting | What produced it |
|---:|---|---|
| **56.3 %** | DeepSeek production, all traffic, on-disk KV cache [src](https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md) | a persistent disk tier on real chat+API traffic |
| **95 %** | Character.AI, inter-turn KV caching on host memory [src](https://blog.character.ai/optimizing-ai-inference-at-character-ai-2/) | sticky sessions + an average dialogue history of **180 messages** |
| **1.7 % → 92.2 %** | Kimi-2.5 NVFP4, 12×GB200 1P1D, real Codex traces | making the cache **cross-instance** (Mooncake Store), not making it bigger |

The third row is the cluster lesson and it is worth restating as a rule: **the
largest available cache win in 2026 is a routing and sharing change, not a
memory change.** A 1.7 % hit rate on agentic traffic is what you get when a
round-robin router lands turn *n+1* of a conversation on a replica that never
saw turn *n*. No amount of per-replica L2 fixes that; an L3 that every replica
can read does. The same source reports the pool scaling cleanly — "12→60
GB200, round-robin routing, hit rate stays >95 %, near-linear"
([`../cross-cutting/serving-optimizations.md` §1.4](../cross-cutting/serving-optimizations.md)).

LMCache reports "up to 15x improvement in throughput" with vLLM across
multi-round QA and document analysis, and flags a trap worth knowing for
agentic serving: context truncation, "this widely-used industry technique … can
greatly reduce prefix cache hit ratio by half"
[src](https://arxiv.org/abs/2510.09665) (arXiv 2510.09665, v1 2025-10-08).
Truncating a conversation from the *front* invalidates every cached prefix
beyond the cut — an application-layer decision that silently halves an
infrastructure-layer metric.

AIBrix's distributed KV cache is reported as "a 50% increase in throughput and
a 70% reduction in inference latency"
[src](https://aibrix.readthedocs.io/latest/designs/aibrix-kvcache-offloading-framework.html),
with pluggable eviction (**S3FIFO by default**, LRU, FIFO) and backends
including InfiniStore, HPKV, PrisKV and EIC (confirmed on the page
2026-09-19). ⚠️ **TO BE VERIFIED — the 50 % / 70 % figures are not on that
page.** Re-fetched 2026-09-19: the design document claims only "substantial
performance gains" with no percentage, no workload and no benchmark. Do not
plan against 50 %/70 % until a primary source for them is found.

**Decision rule for this repo.** Enable host tiering (L2/G2) everywhere — it is
nearly free and the break-even is not close. Add a shared L3 **only** for
models serving multi-turn or agentic traffic across more than one replica, and
measure the cross-instance miss rate first: if your router is already
prefix-affine and your replicas are few, L3 buys little. For Marlin-2B (video
captioning, low prefix reuse across distinct videos) the L3 is likely dead
weight — ⚠️ **TO BE VERIFIED**, no measured prefix-overlap statistic exists for
this repo's video traffic.

---

## 5. Kernel-level and engine-level knobs that change throughput at scale

[`../cross-cutting/serving-optimizations.md` §4.1–4.2](../cross-cutting/serving-optimizations.md)
carries the measured trade-off tables for chunked-prefill budget and CUDA-graph
capture size (the GLM-5.3-Flash / 4×GB200 sweeps). This section is the knob
inventory with the doc-quoted semantics and the cluster-level caveats, not a
repeat of those numbers.

### 5.1 CUDA graphs and torch.compile

vLLM's optimization levels, quoted
[src](https://github.com/vllm-project/vllm/blob/main/docs/configuration/optimization.md):

> - `-O0`: No optimizations. Fastest startup time, but lowest performance.
> - `-O1`: Fast optimization. Simple compilation and fast fusions, and PIECEWISE cudagraphs.
> - `-O2`: Default optimization. Additional compilation ranges, additional fusions, FULL_AND_PIECEWISE cudagraphs.
> - `-O3`: Aggressive optimization. Currently equal to `-O2`, but may include additional time-consuming or experimental optimizations in the future.

The mechanism that makes capture size a *throughput* knob and not just a
latency one is in the sibling doc: **prefill-containing steps run outside CUDA
graphs unless the capture size covers them**
([`../cross-cutting/serving-optimizations.md` §4.2](../cross-cutting/serving-optimizations.md)).
And the sizing rule when speculating, from the vLLM recipe: capture size must
cover `max_num_seqs × (1 + num_speculative_tokens)` — DS-V4.1-Flash rounds
`128 × 6 = 768` up to 1024 (same section).

Two cluster-level consequences the per-replica treatment does not cover:

**(a) Compile cache is a fleet asset.** "vLLM persists `torch.compile` artifacts
under `VLLM_CACHE_ROOT` (default `~/.cache/vllm`), and the cache directory can
be copied between machines or baked into a container image"; set
`VLLM_FORCE_AOT_LOAD=1` "to fail loudly instead of silently recompiling when the
cache misses (any change to the model, config, relevant `VLLM_*` environment
variables, torch build, or GPU model invalidates it)"
[src](https://github.com/vllm-project/vllm/blob/main/docs/configuration/optimization.md).
**Bake the cache into the image and set `VLLM_FORCE_AOT_LOAD=1` in production.**
A silent recompile on every scale-out event is a cold-start regression that
shows up as a latency spike and never as an error. (Cold-start proper is the
sibling document's topic; this flag is the half of it that lives in the engine.)

**(b) Graph capture allocates outside `--gpu-memory-utilization`.** Documented
failure, quoted in
[`../cross-cutting/serving-optimizations.md` §4.2](../cross-cutting/serving-optimizations.md):
0.80 and 0.93 "both leave the same 47.06 MiB free, because that budget covers
weights and KV while graph capture allocates outside it". At fleet scale this is
the difference between a config that boots on every node and one that boots on
most of them.

### 5.2 `max-num-batched-tokens` and chunked prefill

Quoted verbatim from the tuning guide
[src](https://github.com/vllm-project/vllm/blob/main/docs/configuration/optimization.md):

> - Smaller values (e.g., 2048) achieve better ITL because there are fewer prefills slowing down decodes.
> - Higher values achieve better time to first token (TTFT) as you can process more prefill tokens in a batch.
> - For optimal throughput, we recommend setting `max_num_batched_tokens > 8192` especially for smaller models on large GPUs.
> - If `max_num_batched_tokens` is the same as `max_model_len`, that's almost the equivalent to the V0 default scheduling policy (except that it still prioritizes decodes).

with the scheduler behaviour: "In V1, **chunked prefill is enabled by default
whenever possible**. With chunked prefill enabled, the scheduling policy
prioritizes decode requests. It batches all pending decode requests before
scheduling any prefill operations." And the crash guard: "When chunked prefill
is disabled, `max_num_batched_tokens` must be greater than `max_model_len`."

**Cluster framing.** This knob is *per pool*, and the two pools want opposite
settings — a prefill pool wants it large (TTFT), a decode pool wants it small
(ITL). Under PD disaggregation that tension disappears, which is a real and
under-stated benefit of disaggregation beyond the throughput numbers: you stop
having to pick one value that is wrong for half your traffic. Under
*aggregated* serving you are picking a point on
[§2.4](#24-operating-points-and-why-autoscaling-must-not-drift-them)'s Pareto
curve, and the measured cost of buying tail latency this way is steep — 16384→2048
costs "~64 % of throughput at c=128"
([`../cross-cutting/serving-optimizations.md` §4.1](../cross-cutting/serving-optimizations.md)) —
so prefer the CUDA-graph lever for tail ITL and leave the token budget high.

### 5.3 Attention kernel and engine version

Which FA/FlashInfer/FlashMLA kernel actually executes for a given (model, GPU)
is a capability gate, not a tuning knob, and this tree documents it exhaustively
in [`../cross-cutting/flash-attention.md`](../cross-cutting/flash-attention.md)
and [`../matrix/gpu-optimizations.md`](../matrix/gpu-optimizations.md). The
utilisation-relevant summary: for DeepSeek-V4.1-Flash the **KV bytes per token
change by 3.6×** depending on which kernel the engine resolves (890 B on
sm_100/sm_103 via `nvfp4_ds_mla` → `FLASHMLA_MEGA_ATTN_DSV41`; 1,650 B FP8
elsewhere; 3,200 B BF16 measured on H100), METHODOLOGY §8. That is a 3.6×
swing in KV capacity and therefore in achievable batch — a larger effect than
any knob in this section. **Pin engine versions per pool and re-verify the
resolved kernel after every upgrade**; a silent kernel-gate change is a silent
capacity change.

### 5.4 Speculative decoding at high batch

Covered in full at
[`../cross-cutting/serving-optimizations.md` §2.5](../cross-cutting/serving-optimizations.md)
("Why the gains shrink at high batch — and what to do about it") and §4.3's
Pareto table, which records that large-γ speculation moves the frontier
"strongly out at low concurrency, **inward** at high concurrency unless
trimmed", with adaptive verification keeping you on the frontier across the
sweep. Not repeated.

The cluster-level rule that falls out: **speculation settings are a property of
the operating point, not of the model.** A fleet running one γ across an
interactive pool and a batch pool is leaving throughput on the floor in the
batch pool and latency on the floor in the interactive one. Since
[§3.1](#31-two-traffic-classes-one-fleet) recommends co-locating the batch tier
on the interactive replicas by priority, note the tension explicitly: you get
one γ per *replica*, so the co-location saving and the per-class γ optimum are
in conflict. ⚠️ **TO BE VERIFIED** — no published measurement of the size of
that conflict; the estimation method would be to run the §4.3 Pareto sweep at
the interactive γ with a mixed-priority load and compare aggregate tokens/s
against two separate pools.

### 5.5 Expert-parallel load balancing (EPLB)

The problem, stated in vLLM's own docs: "While MoE models are typically trained
so that each expert receives a similar number of tokens, in practice the
distribution of tokens across experts can be highly skewed"
[src](https://github.com/vllm-project/vllm/blob/main/docs/serving/expert_parallel_deployment.md).
Skew is a *utilisation* problem specifically: the step is as slow as the
slowest rank, so one hot expert idles every other GPU in the EP group.

DeepSeek's EPLB algorithm is the reference. Two policies
[src](https://github.com/deepseek-ai/EPLB/blob/main/README.md):

> **Hierarchical Load Balancing** — "When the number of server nodes divides the number of expert groups … We first pack the expert groups to nodes evenly … Then, we replicate the experts within each node … The hierarchical load balancing policy can be used in prefilling stage with a smaller expert-parallel size."
>
> **Global Load Balancing** — "we use the global load balancing policy that replicates the experts globally regardless of expert groups … This policy can be adopted in decoding stage with a larger expert-parallel size."

Note the policy split maps onto the P/D split, exactly like DeepEP's dispatch
modes do. The README is also candid about what it does not solve: "the exact
method to predict the loads of experts is out of this repo's scope. A common
method is to use moving average of historical statistics."

vLLM's implementation, with the documented parameters
[src](https://github.com/vllm-project/vllm/blob/main/docs/serving/expert_parallel_deployment.md):

| Parameter | Description | Default |
|---|---|---|
| `window_size` | "Number of engine steps to track for rebalancing decisions" | 1000 |
| `step_interval` | "Frequency of rebalancing (every N engine steps)" | 3000 |
| `log_balancedness` | "Log balancedness metrics (avg tokens per expert ÷ max tokens per expert)" | `false` |
| `num_redundant_experts` | "Additional global experts per EP rank beyond equal distribution" | 0 |
| `use_async` | "Use non-blocking EPLB for reduced latency overhead" | `true` |
| `communicator` | `torch_nccl`, `torch_gloo`, `pynccl`, `nixl`, or auto | `null` |

```bash
vllm serve deepseek-ai/DeepSeek-V3-0324 \
    --tensor-parallel-size 1 \
    --data-parallel-size 8 \
    --enable-expert-parallel \
    --enable-eplb \
    --eplb-config '{"window_size":1000,"step_interval":3000,"num_redundant_experts":2,"log_balancedness":true}'
```

**Turn on `log_balancedness`.** It is the only direct measurement of the thing
this section is about — "avg tokens per expert ÷ max tokens per expert" *is*
expert-level utilisation, and a value of 0.4 means 60 % of your EP group's
expert compute is waiting on one rank.

**The memory bill, quoted:** "This overhead equals
`NUM_MOE_LAYERS * BYTES_PER_EXPERT * (NUM_TOTAL_EXPERTS + NUM_REDUNDANT_EXPERTS) ÷ NUM_EP_RANKS`.
For DeepSeekV3, this is approximately `2.4 GB` for one redundant expert per EP
rank", with the warning that "EPLB may not be a good fit for memory constrained
environments or when KV cache space is at a premium"
[src](https://github.com/vllm-project/vllm/blob/main/docs/serving/expert_parallel_deployment.md).
At scale vLLM recommends the opposite of frugality: "We recommend setting
`--eplb-config '{"num_redundant_experts":32}'` to 32 in large scale use cases so
the most popular experts are always available."

**Measured payoff**, DeepSeek-V3 on 96×H100 with PD disaggregation: EPLB gave
**1.49× on prefill and 2.54× on decode**
[src](https://www.lmsys.org/blog/2025-05-05-large-scale-ep/). That 2.54× is the
largest single-knob decode gain in this document, and it buys *nothing* at EP
sizes small enough that skew averages out within a rank.

**Decision rule:** enable EPLB whenever EP ≥ 16, budget
`num_redundant_experts × per-rank expert bytes` against your KV budget
(METHODOLOGY §3), and check `log_balancedness` before and after — if
balancedness was already > 0.85, you are paying HBM for nothing.

### 5.6 NCCL tuning

Relevant variables, from the NCCL environment reference
[src](https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html):

| Variable | What it does | Inference relevance |
|---|---|---|
| `NCCL_IB_HCA` | "Define to filter IB Verbs interfaces to be used by NCCL"; comma-separated, each entry `<hca>[:<port>[:<rail>[:<plane>]]]`, e.g. `mlx5_0:1,mlx5_1:1` | pins each GPU group to its nearest NIC — matters on 8-GPU nodes with multiple rails |
| `NCCL_NVLS_ENABLE` | controls NVLink SHARP offload of collectives (e.g. `ncclAllReduce`) to the NVSwitch domain; "available in third-generation NVSwitch systems (NVLink4) with Hopper and later"; default 2, 0 disables, 1 enables | TP all-reduce on B300/GB300 |
| `NCCL_MIN_NCHANNELS` ⚠️ **deprecated in favour of `NCCL_MIN_CTAS`** | "Increasing the number of channels also increases the number of CUDA blocks NCCL uses, which may be useful to improve performance; however, it uses more CUDA compute resources" | the trade is explicit: comm bandwidth vs SMs available to the model |
| `NCCL_NET_GDR_LEVEL` | GPUDirect RDMA path selection | multi-node EP / KV transfer |

The `NCCL_MIN_NCHANNELS` note is the one that bites at inference: **NCCL
channels consume SMs**, and a decode step is already launch-bound. Raising
channels to speed up all-reduce can reduce end-to-end throughput by starving the
attention kernels. ⚠️ **TO BE VERIFIED** — no published inference-side
measurement of the channel-count/SM trade-off; the estimation method is an A/B
at fixed batch watching `DCGM_FI_PROF_SM_ACTIVE` and tokens/s together.

vLLM also documents a hard version floor tied to its EP backend: "The
`deepep_v2` backend requires NCCL >= 2.30.4. PyTorch ships an older NCCL, so
you must upgrade it before building or running DeepEP"
[src](https://github.com/vllm-project/vllm/blob/main/docs/serving/expert_parallel_deployment.md),
plus the InfiniBand init workaround `export GLOO_SOCKET_IFNAME=eth0` and the
Kubernetes requirements "verify that every pod runs with `hostNetwork: true`,
`securityContext.privileged: true` to access Infiniband" and `ulimit -l`
unlimited.

### 5.7 NUMA pinning

vLLM ships first-class NUMA binding, with the rationale quoted
[src](https://github.com/vllm-project/vllm/blob/main/docs/configuration/optimization.md):

> "On multi-socket GPU servers, GPU worker processes can lose performance if their CPU execution and memory allocation drift away from the NUMA node nearest to the GPU. vLLM can pin each worker with `numactl` before the Python subprocess starts, so the interpreter, imports, and early allocator state are created with the desired NUMA policy from the beginning."

```bash
# Auto-detect NUMA nodes for visible GPUs
vllm serve meta-llama/Llama-3.1-8B-Instruct \
  --tensor-parallel-size 4 \
  --numa-bind

# Explicit NUMA-node mapping
vllm serve meta-llama/Llama-3.1-8B-Instruct \
  --tensor-parallel-size 4 \
  --numa-bind \
  --numa-bind-nodes 0 0 1 1

# Explicit CPU pinning
vllm serve meta-llama/Llama-3.1-8B-Instruct \
  --tensor-parallel-size 4 \
  --numa-bind \
  --numa-bind-nodes 0 0 1 1 \
  --numa-bind-cpus 0-3 4-7 48-51 52-55
```

Container caveat, quoted: "In containerized environments, NUMA policy syscalls
may require extra permissions, such as `--cap-add SYS_NICE` when running via
`docker run`." And the scope limit: "The current implementation binds GPU
execution processes such as `EngineCore` and multiprocessing workers. It does
not apply NUMA binding to frontend API server processes or the DP coordinator."

**Why this is a §4 concern and not cosmetic:** the host DRAM tier
([§4.1](#41-the-tier-ladder-with-names)) is reached over PCIe from a specific
socket. A worker whose pinned host memory is on the far NUMA node pays a QPI/UPI
hop on every KV offload transfer, on top of the PCIe cost. **If you enable host
KV tiering, enable `--numa-bind`** — the two are the same optimisation seen from
two sides. ⚠️ **TO BE VERIFIED** — no measurement of the far-NUMA penalty on KV
offload bandwidth specifically; the 83.4 GB/s figure in
[§4.2](#42-the-bandwidth-ladder) does not state its NUMA configuration.

### 5.8 Hugepages

**Corrected 2026-09-19 — the previous text said the opposite of what the cited
paper reports.** This section formerly claimed the paper found hugepages
"within measurement noise" with dTLB misses dropping "only 16%". Neither string
occurs anywhere in arXiv 2509.18886; the text was re-extracted from the PDF on
2026-09-19 and the paper's actual finding is that **page size matters**:

> "The overhead of VM TH over VM FH quantifies the performance cost due to the
> lack of 1 GB hugepage support in TDX at **3.19–5.20%**."
>
> "**Insight 7:** TDX uses self-allocated transparent hugepages and ignores
> manually reserved hugepages, which costs **up to 5% of raw performance**."
> [src](https://arxiv.org/pdf/2509.18886), *Confidential LLM Inference:
> Performance and Cost Across CPU and GPU TEEs*

`VM FH` is a VM on preallocated 1 GB hugepages, `VM TH` the same VM on 2 MB
transparent hugepages. So 1 GB pages are worth **3.2–5.2 %** over 2 MB pages in
that study, and the paper's own summary lists "hugepages" among the sources of
CPU-TEE overhead. Two caveats keep this off the recommended list anyway: the
measurement is **CPU-side inference inside a TEE**, not GPU serving, so the
memory path it stresses is not the one a GPU decode step uses; and the effect
is single-digit percent, an order of magnitude below the NUMA and
transfer-granularity levers next door.

**Recommendation (unchanged, reasoning replaced): leave transparent hugepages at
the distro default and spend the tuning effort on NUMA binding and transfer
granularity first** — but on a host-KV-tiering deployment, where the host
allocator *is* on the critical path, a 1 GB-page A/B is worth one afternoon.
⚠️ **TO BE VERIFIED** — no GPU-serving-side hugepage measurement found.

### 5.9 Knob summary

| Knob | Axis it moves | Typical magnitude | Risk |
|---|---|---|---|
| CUDA-graph capture size | p99 ITL ↓, throughput ↓ | ~2–3× ITL for 5–10 % throughput ([serving-opt §4.2](../cross-cutting/serving-optimizations.md)) | p99 TTFT up 19–31 % |
| `max-num-batched-tokens` | TTFT ↔ ITL | 64 % throughput at c=128 for 16384→2048 ([serving-opt §4.1](../cross-cutting/serving-optimizations.md)) | crashes if < `max_model_len` with chunking off |
| EPLB | decode throughput ↑ | **2.54×** decode, 1.49× prefill at EP32/EP72 [src](https://www.lmsys.org/blog/2025-05-05-large-scale-ep/) | ~2.4 GB/rank HBM per redundant expert |
| `torch.compile` cache baked into image | cold start ↓ | cold-start only | silent recompile without `VLLM_FORCE_AOT_LOAD=1` |
| `--numa-bind` | host-tier bandwidth ↑ | ⚠️ unmeasured | needs `SYS_NICE` in containers |
| `NCCL_MIN_NCHANNELS` | comm ↑, SMs ↓ | ⚠️ unmeasured for inference | can *reduce* tokens/s |
| Hugepages | host-side memory path | **3.2–5.2 %** (1 GB vs 2 MB pages) in a **CPU-TEE** study, ⚠️ not GPU serving [src](https://arxiv.org/pdf/2509.18886) | low; unmeasured on this workload |
| Attention-kernel gate (engine version) | **KV bytes/token** | up to **3.6×** (METHODOLOGY §8) | silent on upgrade |

---

## 6. MoE-specific cluster patterns

### 6.1 Wide-EP across nodes

The mechanism and the measured NVL72 results are in
[`../cross-cutting/serving-optimizations.md` §3.3–3.4](../cross-cutting/serving-optimizations.md)
(DeepEP `normal`/`low_latency` modes, the SGLang `--moe-a2a-backend` and vLLM
`--all2all-backend` tables, the GB300-vs-GB200 measurements). The
cluster-shaping additions:

**Backend selection is a P/D decision, not a preference.** vLLM's own table
[src](https://github.com/vllm-project/vllm/blob/main/docs/serving/expert_parallel_deployment.md):

| Backend | Use case | Features |
|---|---|---|
| `allgather_reducescatter` | "Default backend" | "Standard all2all using allgather/reducescatter primitives" |
| `deepep_high_throughput` | "Multi-node prefill" | "Grouped GEMM with continuous layout, optimized for prefill" |
| `deepep_low_latency` | "Multi-node decode" | "CUDA graph support, masked layout, optimized for decode" |
| `flashinfer_nvlink_one_sided` / `..._two_sided` | "MNNVL systems" | one-/two-sided A2A for multi-node NVLink |

with the explicit warning that they do not generalise: "The `high_throughput`
and `low_latency` kernels are optimized for disaggregated serving and **may show
poor performance for mixed workloads**" (same doc). **If you have not
disaggregated, use the default backend** — reaching for DeepEP in an aggregated
deployment is a documented way to lose throughput.

**Overlapping communication with compute is where the wide-EP throughput
actually comes from.** DeepSeek describes a "dual-batch overlap strategy to hide
communication costs and improve overall throughput by splitting a batch of
requests into two microbatches", with the decode phase further subdivided:
"we subdivide the attention layer into two steps and use a 5-stage pipeline to
achieve a seamless communication-computation overlapping"
[src](https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md).
vLLM exposes this as `--enable-dbo` (with `--dbo-decode-token-threshold`), plus
`--async-scheduling` to "overlap scheduling with model execution"
[src](https://github.com/vllm-project/vllm/blob/main/docs/serving/expert_parallel_deployment.md),
[src](https://vllm.ai/blog/2025-12-17-large-scale-serving).

### 6.2 Expert placement and EPLB policies

DeepSeek's system decomposes load balancing into **three** balancers, not one —
and naming them separately is the useful part, because they fail differently
[src](https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md):

| Balancer | Key issue (quoted) | Objective (quoted) |
|---|---|---|
| **Prefill** | "Varying request counts and sequence lengths across DP instances lead to imbalanced core-attention computation and dispatch send load." | "Balance core-attention computation across GPUs"; "Equalize input token counts per GPU" |
| **Decode** | "Uneven request counts and sequence lengths across DP instances cause disparities in core-attention computation (linked to KVCache usage) and dispatch send load." | "Balance KVCache usage across GPUs"; "Equalize request counts per GPU" |
| **Expert-Parallel** | "there exist inherently high-load experts, resulting in an imbalance in expert computational workloads across different GPUs" | "minimize the maximum dispatch receive load across all GPUs" |

and the framing sentence that justifies all three: "if a single GPU is
overloaded with computation or communication, it becomes a performance
bottleneck, slowing the entire system while leaving other GPUs idle."

The first two are **router** responsibilities (balance KV usage and request
counts across DP ranks), the third is **EPLB**'s. A fleet that runs EPLB but
routes round-robin has fixed one of three. The corresponding router capability
is the Endpoint Picker's "Resource Utilization Analysis monitoring GPU memory
usage, particularly KV cache utilization"
[src](https://gateway-api-inference-extension.sigs.k8s.io/) — i.e. route on
`vllm:kv_cache_usage_perc`, which is the decode balancer, implemented.

Placement also matters for locality: EPLB "attempt\[s] to place the experts of
the same group to the same node to reduce inter-node data traffic, whenever
possible", enabled by DeepSeek-V3's group-limited expert routing
[src](https://github.com/deepseek-ai/EPLB/blob/main/README.md). That is a
topology-aware optimisation and it is why the hierarchical policy needs "the
number of server nodes \[to divide] the number of expert groups" — on an 8-GPU
HGX fleet, expert-group counts that are not multiples of your node count fall
back to the global policy and lose the locality.

### 6.3 NVL72 versus 8-GPU nodes

This repo's hardware is **8×B300 HGX nodes: NVLink within the node,
InfiniBand/RoCE between nodes** (METHODOLOGY §8,
[`../gpus/b300.md`](../gpus/b300.md)). GB300 NVL72 puts 72 GPUs in one
130 TB/s NVLink domain
([`../cross-cutting/serving-optimizations.md` §3.4](../cross-cutting/serving-optimizations.md)).
The difference is not a percentage; it is a **discontinuity at EP = 8**:

| EP size on an 8-GPU HGX node | All-to-all path | Comm overhead band (METHODOLOGY §4) |
|---|---|---|
| ≤ 8 | NVLink, intra-node | ~5–15 % |
| > 8 | InfiniBand / RoCE, inter-node | **20–40 %** |

On NVL72 that step does not occur until EP = 72. NVIDIA's measurement of what
the extra width buys: **"Large-scale Expert Parallelism (EP) rank 32 delivers up
to 1.8x higher output token throughput per GPU compared to small EP rank 8 at
100 tokens/sec per user"** on GB200 NVL72 with DeepSeek-R1, disaggregated
serving and MTP enabled in both configurations
[src](https://developer.nvidia.com/blog/scaling-large-moe-models-with-wide-expert-parallelism-on-nvl72-rack-scale-systems/)
(2025-10-20). Note the qualifier carefully: **at a fixed 100 tok/s/user**, i.e.
this is a gain on the interactivity-constrained part of the Pareto curve, not an
unconditional 1.8×. The same blog reports the expert density at EP = 64 as "four
experts per GPU per layer, for a total of 232 experts assigned per GPU" for a
256-expert, 671 B model. ⚠️ The blog publishes **no EP8/EP16/EP32 tokens/s/GPU
table** and **no quantified EPLB-alone delta** — the 1.8× is the only ratio
given.

**Published tokens/s/GPU against EP size**, assembled from four independent
sources (note: different models, GPUs, sequence lengths and dates — this is a
*landscape*, not a controlled scaling curve):

| Deployment | GPU | EP (prefill / decode) | Prefill tok/s/node | Decode tok/s/node | Decode tok/s/GPU |
|---|---|---|---:|---:|---:|
| DeepSeek-V3/R1 production, 2025-02-27/28 [src](https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md) | H800 | EP32 / EP144 | **~73.7k** (incl. cache hits) | **~14.8k** | ~1,850 |
| SGLang, 96×H100, 2025-05-05 [src](https://www.lmsys.org/blog/2025-05-05-large-scale-ep/) | H100 | EP32 / EP72 | **52.3k** (ISL 2K) | **22.3k** | ~2,787 |
| vLLM wide-EP, Coreweave, 2025-12-17 [src](https://vllm.ai/blog/2025-12-17-large-scale-serving) | H200 | wide-EP | — | — | **2.2k** (up from ~1.5k) |

The SGLang run is the most instructive because it reports the phase breakdown
and the comparison to DeepSeek's own profile
[src](https://www.lmsys.org/blog/2025-05-05-large-scale-ep/):

| Phase | Config | Measured |
|---|---|---|
| Prefill, 4 nodes, EP32 | 16,384 tokens/device, 4,096 input len | 57,674 (1K) / 54,543 (2K) / 50,302 (4K) tok/s/node |
| Decode, 9 nodes, EP72 | 2K inputs, batch 256 | 22,282 tok/s/node |
| Decode with simulated MTP | 4K inputs | 17,373 tok/s/node |
| vs DeepSeek's official profile | — | within **5.6 %** (prefill), **6.6 %** below (decode under simulated MTP) |

That last row is the important one for planning: an open-source stack on H100
reproduced a production H800 deployment to within ~6 %. **The published
production numbers are reachable, not aspirational** — which makes DeepSeek's
73.7k/14.8k a legitimate target to size against rather than a vendor headline.

**Applicability to this repo.** Kimi-K3 needs a full 8×B300 node and its pinned
shape is `TP8 + DCP8` on **one** node, with a note that "EP8 optional but
conflicts with DCP's a2a buffers" and that "PP/DP-attention/wide-EP" are not the
recommended shape ([`../matrix/pairs.json`](../matrix/pairs.json),
[`../models/kimik3/b300.md`](../models/kimik3/b300.md)). DeepSeek-V4.1-Flash fits
in **2–4** GPUs on B300 ([`../models/deepseek41f/b300.md`](../models/deepseek41f/b300.md)),
so its EP never needs to leave the node either. **On 8×B300 HGX, wide-EP is not
this repo's pattern** — it is what you would buy an NVL72 rack for. The wide-EP
literature is still directly useful for two things: EPLB (which applies at
EP = 8 too, weakly) and the DP-attention shape ([§2.2](#22-data-parallel-attention-for-moe)),
which is the intra-node half of the same idea.

---

## 7. Case studies with numbers

### 7.1 DeepSeek-V3/R1 production (the day-6 disclosure)

Still the most complete public inference-economics disclosure, and the anchor
for most of this document
[src](https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md).
All figures are for UTC+8 2025-02-27 12:00 → 2025-02-28 12:00, H800, FP8 GEMM +
FP8 dispatch, BF16 core-MLA and combine.

| Quantity | Value |
|---|---:|
| Peak node occupancy (V3 + R1) | **278 nodes** (8×H800 each) |
| Average node occupancy | **226.75 nodes** |
| Peak : mean | **1.226** |
| Total input tokens | 608 B |
| — of which hit the on-disk KV cache | **342 B (56.3 %)** |
| Total output tokens | 168 B |
| Average output speed | 20–22 tok/s |
| Average KV-cache length per output token | 4,989 tokens |
| Prefill throughput per node | **~73.7k tok/s** (incl. cache hits) |
| Decode throughput per node | **~14.8k tok/s** |
| Daily cost at $2/H800-hour | **$87,072** |
| Theoretical daily revenue at R1 list price | **$562,027** |
| Cost profit margin | **545 %** |

Read the qualifiers, which DeepSeek states itself: "our actual revenue is
substantially lower" because V3 is cheaper than R1, "only a subset of services
are monetized (web and APP access remain free)", and nighttime discounts apply.
The 545 % is a *capacity* economics figure, not a P&L.

Three things to take from it, all used above:
1. The **56.3 % disk-cache hit rate** is the single best public calibration for
   prefill-pool sizing ([§2.3](#23-prefilldecode-pool-ratios)).
2. The **1.226 peak:mean** bounds what diurnal harvesting is worth
   ([§3.1](#31-two-traffic-classes-one-fleet)).
3. The **EP32 prefill / EP144 decode asymmetry over 4 vs 18 nodes** is the
   production P:D ratio, and it is far more decode-heavy than DistServe's 2:1.

### 7.2 Mooncake / Kimi

Mooncake's KVCache-centric disaggregated architecture won Best Paper at FAST '25
[src](https://www.usenix.org/conference/fast25/presentation/qin) (arXiv
2407.00079). It "physically separates prefill and decode clusters" and builds a
distributed KV pool by leveraging "the underutilized CPU, DRAM, and SSD
resources of the GPU cluster" (the abstract says CPU/DRAM/SSD — **corrected
2026-09-19**, an earlier draft here added "and NIC"), reporting "up to a 525%
increase in throughput in certain simulated scenarios while adhering to SLOs"
(paper abstract, arXiv 2407.00079; note "simulated" — this is the paper's upper
bound, not a production measurement). The abstract's *production* figure is the
one to plan against: "Under real workloads, Mooncake's innovative architecture
enables Kimi to handle **75% more requests**."

The production-scale datapoint that matters more for this document is the
cross-instance cache result already collated in
[`../cross-cutting/serving-optimizations.md` §1.4](../cross-cutting/serving-optimizations.md):
Kimi-2.5 NVFP4 on 12×GB200, 1P1D, real Codex traces — hit rate **1.7 % →
92.2 %**, throughput **3.8×**, TTFT p50 **46×** lower, e2e latency **8.6×**
lower, and near-linear scaling from 12 to 60 GB200 with round-robin routing and
hit rate staying above 95 %.

⚠️ A widely-repeated figure — "Kimi-K2 on 128 H200 GPUs: 224k tokens/sec
prefill, 288k tokens/sec decode" — surfaced in search but **could not be traced
to a primary Moonshot or Mooncake document** on 2026-09-19. Not used anywhere in
this document. **TO BE VERIFIED** before citing.

### 7.3 Character.AI

The most extreme published KV-reduction case, and the clearest demonstration
that *architecture* beats *infrastructure* when you control the model
[src](https://blog.character.ai/optimizing-ai-inference-at-character-ai-2/)
(2024-06-20):

| Technique | Effect (quoted / stated) |
|---|---|
| Multi-Query Attention | "8X reduction" vs GQA |
| Hybrid attention horizons | local window of **1,024 tokens**; global attention in **1 of every 6 layers** |
| Cross-layer KV sharing | additional **2–3×** |
| **Combined** | **"more than 20X"** KV cache reduction |
| Native int8 training | weights, activations **and** attention KV, with custom int8 kernels for matmul and attention — "eliminating the risk of training/serving mismatch" |
| Inter-turn KV caching on host memory | **95 % cache rate** |
| Serving scale | "more than 20,000 inference queries per second" |
| Average dialogue history | **180 messages** |
| Cost trajectory | "reduced serving costs by a factor of 33 compared to when we began in late 2022" |
| vs commercial APIs | "at least 13.5X" cheaper |

The 95 % host-memory hit rate is not a caching achievement so much as a
*workload* one — 180-message conversations with sticky routing make the hit
rate almost free. The transferable lesson for this repo's agentic traffic is
[§4.4](#44-hit-rate-economics)'s: session affinity plus a host tier gets most of
the way, and a shared L3 covers the rest.

⚠️ The "13.5X cheaper" and "factor of 33" are self-reported and unaudited;
treat as vendor-claimed. Note also the date — 2024-06-20 — which predates every
other source in this document by 15+ months; the *techniques* are current, the
*ratios* are against a 2024 baseline.

### 7.4 NVIDIA Dynamo + GB200/GB300

Measured GB300-vs-GB200 and GB300-vs-H200 results (peak TPS/GPU, MTP effects,
128K prefill times, decode batch caps, disaggregation TPOT) are tabulated in
[`../cross-cutting/serving-optimizations.md` §3.4](../cross-cutting/serving-optimizations.md)
and are not repeated. The cluster-control half — Dynamo's Planner, its two
scaling loops and its SLO parameters — is in
[§2.3](#23-prefilldecode-pool-ratios); the KVBM tier model is in
[§4.1](#41-the-tier-ladder-with-names).

The one framing point worth adding: the SemiAnalysis/LMSYS observation that
GB200 NVL72 gained "up to 8× more tokens/GPU in high-throughput regimes … in
under 4 months — a *software* gain"
([`../cross-cutting/serving-optimizations.md` §3.4](../cross-cutting/serving-optimizations.md))
means **any Blackwell utilisation measurement has a short shelf life**. A
capacity plan built on a measured MBU from an engine release six months old is
planning against a moved frontier. This is the practical argument for
continuous re-benchmarking, i.e. for §7.5.

### 7.5 InferenceMAX / InferenceX

SemiAnalysis's InferenceMAX (renamed InferenceX) is an open-source benchmark
that "runs its suite of benchmarks every night on hundreds of chips, continually
re-benchmarking the world's most popular open-source inference frameworks and
models to track real performance in real time"
[src](https://newsletter.semianalysis.com/p/inferencemax-open-source-inference),
and InferenceXv2 "is now the first suite to benchmark the Blackwell Ultra GB300
NVL72 and B300 across the whole pareto frontier curve"
[src](https://newsletter.semianalysis.com/p/inferencex-v2-nvidia-blackwell-vs).
Its framing metric is the one [§2.4](#24-operating-points-and-why-autoscaling-must-not-drift-them)
uses: "Interactivity (tok/s/user) describes how fast each user of a system
receives tokens", plotted against throughput.

This tree can re-fetch its rows programmatically — see
[`../cross-cutting/inferencex-api.md`](../cross-cutting/inferencex-api.md), which
also records the coverage gaps: **no A100 rows, RTX PRO 6000 has Qwen3.5 rows
only, and Qwen3.8-27B returns 0 rows.**

**Recommended use:** treat InferenceX as the *external* Pareto frontier for a
given (model, GPU, engine-version) and your own benchmark as the *internal*
one. The gap between them is your configuration debt. Because it re-runs
nightly, it is also the cheapest early warning that an engine upgrade moved the
frontier — which, per §7.4, it does roughly monthly on Blackwell.

---

## 8. Worked example: a utilisation plan for this repo's nodes

**Hardware** (METHODOLOGY §8): 8×B300 HGX nodes — 268 GB/GPU as deployed,
2,144 GB/node, 8.0 TB/s HBM, NVLink within the node, InfiniBand/RoCE between
nodes, local NVMe; plus AWS p6 nodes (same B300 silicon, per
[`../gpus/b300.md`](../gpus/b300.md)). Usable per GPU
`U = 0.90 × 268 = 241.2 GB`.

Every throughput and concurrency figure below is quoted from the pair documents
via [`../matrix/pairs.json`](../matrix/pairs.json); **nothing here is a new
number.** Confidence labels are carried through.

### 8.1 What runs where

| Model | Shape (pair doc) | Replicas / node | Decode tok/s/GPU | **Decode tok/s/node** | Conf. |
|---|---|---:|---:|---:|---|
| [DeepSeek-V4.1-Flash](../models/deepseek41f/b300.md) — throughput pool | **TP2**, Engram host-offloaded | 4 | 2,656 @ c=128 | **21,248** | `measured` |
| [DeepSeek-V4.1-Flash](../models/deepseek41f/b300.md) — interactive pool | **TP4** | 2 | 1,373 @ c=128, TPOT 23.3 ms | **10,984** | `measured` |
| [DeepSeek-V4.1-Flash-NVFP4](../models/deepseek41fnvfp4/b300.md) | **TP4** | 2 | 9,531 @ c=2048 | **76,248** | `estimate` |
| [Qwen3.8-27B](../models/qwen3827b/b300.md) | **TP1**, DP replication | 8 | 13,461 @ c=384 | **107,688** | `estimate` |
| [Kimi-K3](../models/kimik3/b300.md) | **TP8 + DCP8**, whole node | 1 | 569.4 @ c=256 | **4,555** | `estimate` |
| [Marlin-2B](../models/marlin2b/b300.md) | **TP1**, DP replication | 8 | 93,271 @ c=3,327 (4K ctx) | **746,168** | `estimate` |

Reading notes, all from the source documents:

- **DeepSeek's two pools are the [§2.1](#21-replica-sizing-tp-vs-dp-and-the-one-piece-of-algebra-that-decides-it)
  rule made concrete.** TP2 nearly doubles per-GPU throughput over TP4 because
  the model's KV is replicated per rank, so four narrow replicas hold more
  aggregate KV than two wide ones. The cost is TPOT: the interactive row hits
  23.31 ms at TP4, the throughput row 24.1 ms at TP2 **at the same c=128 per
  replica** — but the TP2 replicas are each serving that batch on 2 GPUs, so per
  *user* interactivity at equal node load is worse. Run both pools, route by
  SLO class.
- **Kimi-K3 monopolises a node.** 8 GPUs, `TP8 + DCP8`, 4,555 tok/s/node. Its
  interactive row carries **TTFT 10,139 ms** at c=111
  ([`../matrix/pairs.json`](../matrix/pairs.json)), which is a batch-wave
  artefact, not a user-facing latency — this model is a throughput deployment on
  this hardware, and its `sustained_output_tokens_per_s_per_gpu` (198.9
  interactive / 321.6 max-throughput) is the honest end-to-end rate.
- **Marlin-2B's 93,271 tok/s/GPU is at 4K context**, where
  `max_concurrency = 3,327` ([`../models/marlin2b/b300.md`](../models/marlin2b/b300.md) §3.2);
  at 8K it is 1,934 and at 128K it is 142. It is also a video VLM whose real
  cost is the ViT (65.3 TFLOP at 240 frames), so its "token" rate is not
  comparable to the text models'. Its own §3.2 note flags that at these
  concurrencies "queueing dominates TTFT" and the figures deserve a 0.30–0.45×
  haircut against the full-batch roofline.

⚠️ **Discrepancy to flag, not resolve here.** The NVFP4 build's node total
(76,248 `estimate`, at c=2048) is **3.6× the base build's** (21,248 `measured`,
at c=128). These are different operating points on the same Pareto curve —
c=2048 vs c=128 — and different confidence labels, so they are not directly
comparable, but a reader scanning the table will compare them. The base row is
the only `measured` DeepSeek row in the tree. **Plan against 21,248 and treat
76,248 as an unvalidated ceiling.** See
[`../matrix/cost-matrix.md` §9.3](../matrix/cost-matrix.md) for the related
measured-vs-roofline gaps.

### 8.2 Node allocation for a mixed fleet

A concrete 6-node plan, assuming interactive DeepSeek is the primary product,
Kimi-K3 serves a long-context tier, and Qwen/Marlin serve auxiliary traffic:

| Nodes | Pool | Shape | Purpose |
|---:|---|---|---|
| 2 | DeepSeek interactive | 2 × TP4 per node | TPOT ≤ 50 ms tier (METHODOLOGY §6 S1/S2) |
| 1 | DeepSeek throughput | 4 × TP2 per node | batch/async tier + overflow |
| 1 | Kimi-K3 | 1 × TP8+DCP8 | long-context / hard-query tier |
| 1 | Qwen3.8-27B + Marlin-2B | 8 × TP1, mixed | cheap tier; Marlin gets the video queue |
| 1 | float | — | rolling upgrades, failure headroom, off-peak batch |

The float node is not slack: it is what makes the [§2.1](#21-replica-sizing-tp-vs-dp-and-the-one-piece-of-algebra-that-decides-it)
argument for low TP pay off — small replicas mean a node can be drained and
re-imaged without dropping a whole model's capacity.

### 8.3 Target bands

⚠️ **All targets below are planning bands derived from METHODOLOGY §4 and the
operational reasoning in [§1](#1-utilisation-metrics-that-matter), not
measurements.** The tree's standing open question 4 applies: *no
per-GPU-generation MBU/MFU measurement exists anywhere in it*
([`../README.md`](../README.md)).

| Pool | Target MBU | Target KV utilisation | Target batch occupancy | Page when |
|---|---|---|---|---|
| DeepSeek interactive (TP4) | 0.50–0.70 (Blackwell band) | 0.60–0.80 | 0.6–0.9 | KV > 0.90 **and** queue > 0 |
| DeepSeek throughput (TP2) | 0.50–0.70 | **0.85–0.95** | ~1.0 | queue grows monotonically 10 min |
| Kimi-K3 (TP8+DCP8) | 0.50–0.70 | 0.70–0.85 | 0.6–0.9 | KV > 0.90; DCP means KV is the scarce axis |
| Qwen3.8-27B (TP1) | 0.50–0.70 | 0.60–0.80 | 0.6–0.9 | GDN state (392.2 MB/req, METHODOLOGY §8) makes seats scarce before bytes do |
| Marlin-2B (TP1) | n/a — ViT-bound | 0.50–0.70 | — | watch `PIPE_TENSOR_ACTIVE`, not MBU |

The KV bands differ **on purpose**: a throughput pool should run near the
preemption edge because a preemption there costs a recompute and nothing else,
while an interactive pool should not, because a preemption there is an SLO
violation. That asymmetry is the whole reason to have two pools.

Marlin-2B is the exception that proves [§1.3](#13-mbu--the-definition-and-the-trap-in-it)'s
point: its vision encoder is compute-bound, so MBU is the wrong instrument and
`PIPE_TENSOR_ACTIVE` plus MFU is the right one.

### 8.4 KV tiering plan

Per [§4.3](#43-the-break-even-that-actually-matters-is-not-bandwidth), the
bandwidth break-even is met by 2–4 orders of magnitude for every model here, so
the decisions are about hit rate and capacity:

| Model | L2 host tier | L3 shared tier | Rationale |
|---|---|---|---|
| DeepSeek-V4.1-Flash | **yes** | **yes** if agentic traffic spans replicas | 890 B/token KV on B300 is the cheapest KV in the fleet to store and move; agentic reuse is high |
| Kimi-K3 | **yes** | yes | single replica per node → L3 is what makes a *second* node's cache useful |
| Qwen3.8-27B | yes | ⚠️ measure first | 32 KiB/token FP8 is 37× DeepSeek's; the L3 fills 37× faster for the same token count |
| Marlin-2B | yes | **probably not** | distinct videos → low prefix overlap ⚠️ unmeasured |

Set `--hicache-write-policy write_through_selective` on any L3
([§4.1](#41-the-tier-ladder-with-names)), enable `--numa-bind`
([§5.7](#57-numa-pinning)) on every replica that uses a host tier, and watch
`vllm:kv_block_reuse_gap_seconds` against the tier's eviction age
([§4.3](#43-the-break-even-that-actually-matters-is-not-bandwidth)) to decide
sizing.

### 8.5 The off-peak plan

Following [§3.1](#31-two-traffic-classes-one-fleet) and calibrating against
DeepSeek's measured 1.226 peak:mean:

1. **Always on**: batch-tier traffic as low-priority requests on the DeepSeek
   throughput pool. ~4 % interactive tax, no infrastructure.
2. **Off-peak**: drain the second DeepSeek interactive node into the throughput
   pool (re-launch at TP2, 4 replicas) — a shape change, not a model change,
   and one that nearly doubles that node's aggregate KV seats per
   [§2.1](#21-replica-sizing-tp-vs-dp-and-the-one-piece-of-algebra-that-decides-it).
3. **Deep trough**: hand the float node and the drained interactive node to
   Kueue's batch cohort for evals/training, with `reclaimWithinCohort:
   LowerPriority` so the inference queue can take them back within one gang.

Expected recovery, sizing off DeepSeek's published peak:mean: **~18 % of peak
GPU-hours**, not the 50 %+ that a naive look at a nighttime traffic graph
suggests. Budget the engineering accordingly — step 1 is where most of the
value is, for almost none of the work.

---

## Open questions

Consolidated ⚠️ items from every section above. Each states the estimation
method used in the meantime.

1. **No per-GPU-generation MBU/MFU measurement exists in this tree** (§1.3,
   §8.3). Every target band here is METHODOLOGY §4's planning default. This is
   [`../README.md`](../README.md) open question 4 and is not new; it is restated
   because §8.3's whole table inherits it. *Method: METHODOLOGY §4 planning
   bands.*
2. **DCGM interpretation bands for LLM decode are unpublished** (§1.2). The
   diagnosis table is inference from NVIDIA's metric definitions plus the
   METHODOLOGY roofline, not a production dataset. *Method: reasoning from
   definitions.*
3. **SGLang metric prefix: `sglang:` vs `sglang_`** (§1.5). The official
   production-metrics page documents the colon form; a community guide reports
   the change to underscores in v0.5.4+ and this tree pins 0.5.20. Dashboards
   built on the wrong form silently show nothing. *Method: scrape `/metrics` on
   the deployed build.*
4. **vLLM spec-decode metric family shape across 0.29.0** (§1.5). The metric
   list is from the current stable docs; the `spec_decode_*` family has moved
   between V0 and V1. *Method: scrape `/metrics`.*
5. **SGLang's `estimated_*_bytes/flops` counters are `est.`, not `meas.`**
   (§1.5). They inherit SGLang's internal model description, notably its MoE
   expert-hit accounting. An MBU computed from them is only as good as that.
   *Method: cross-check one operating point against METHODOLOGY §4's
   `bytes_per_decode_step`.*
6. **The 0.90 KV-utilisation alert threshold is operational convention, not a
   measured optimum** (§1.5). The right value depends on sequence-length
   variance. *Method: trace your own preemption counter against KV utilisation.*
7. **Recompute-churn cost of a long-output low-priority tier is unmeasured**
   (§3.1). vLLM V1 preempts by `RECOMPUTE`, so a preempted batch request loses
   decode progress; how much that costs at a realistic batch-tier output length
   is unpublished. *Method: none applied; recommendation is to cap batch-tier
   `max_tokens` conservatively.*
8. **Identical-request deduplication is not shipped by any stack found**
   (§3.2), and the duplicate rate on real traffic is unmeasured. *Method:
   recommendation is not to build it.*
9. **B300 MIG profiles are not in NVIDIA's supported-profiles page** as of
   2026-09-19 (§3.3), which lists B200, RTX PRO 6000/5000/4500, Thor, H100,
   H200, A100, A30. Do not scale B200's 180 GB table to 268 GB. *Method: read
   `nvidia-smi mig -lgip` on the card.*
10. **The MIG fit table in §3.3 is capacity-only.** A `1g` slice gets ~1/7 of the
    SMs and a fraction of the bandwidth, so throughput does not follow capacity.
    *Method: METHODOLOGY §3 fit arithmetic only; no throughput claim made.*
11. **NVMe and RDMA bandwidth rows in §4.2 are nominal**, not measured on this
    fabric, and the 83.4 GB/s host-DMA figure does not state its NUMA
    configuration (§4.2, §5.7). *Method: vendor-class figures, labelled.*
12. **Prefix-overlap statistics for this repo's video traffic (Marlin-2B) do not
    exist** (§4.4, §8.4), so the "L3 is probably dead weight" call is reasoning,
    not measurement. *Method: measure `prefix_cache_hits / queries` on a real
    video queue before provisioning L3.*
13. **AIBrix's "50 % throughput / 70 % latency" figures are vendor-published
    without a stated workload** (§4.4). *Method: labelled vendor-claimed.*
14. **The per-class speculative-decoding γ conflicts with co-locating the batch
    tier on interactive replicas** (§5.4), and the size of that conflict is
    unmeasured. *Method: proposed A/B stated inline.*
15. **`NCCL_MIN_NCHANNELS`' SM-vs-comm trade-off has no published inference-side
    measurement** (§5.6). Raising channels can reduce end-to-end tokens/s.
    *Method: proposed A/B watching `SM_ACTIVE` and tokens/s together.*
16. **The far-NUMA penalty on KV-offload bandwidth is unmeasured** (§5.7).
    *Method: reasoning from the PCIe/UPI topology; recommendation is to enable
    `--numa-bind` regardless, since it is free.*
17. **Hugepages: the §5.8 evidence was misread and is now corrected** — arXiv
    2509.18886 does **not** report a null result; it reports **3.19–5.20 %**
    cost from 2 MB transparent pages vs preallocated 1 GB pages, in a **CPU**
    TEE. No GPU-serving-side hugepage measurement exists. *Method: quoted with
    its platform caveat; recommendation is still distro default, with a 1 GB-page
    A/B worth running on host-KV-tiering nodes.*
18. **NVIDIA's wide-EP blog publishes no EP8/EP16/EP32 tokens/s/GPU table and no
    EPLB-alone delta** (§6.3) — the 1.8× is the only ratio given, and only at a
    fixed 100 tok/s/user with MTP and disaggregation enabled in both arms.
    *Method: quoted with its qualifier; not extrapolated.*
19. **The EP-size landscape table in §6.3 is not a controlled scaling curve** —
    four sources, different models, GPUs, sequence lengths and dates.
    *Method: labelled as a landscape; no curve fitted.*
20. **"Kimi-K2 on 128 H200: 224k tok/s prefill, 288k tok/s decode" could not be
    traced to a primary source** on 2026-09-19 (§7.2) and is used nowhere.
    *Method: excluded.*
21. **Character.AI's "13.5× cheaper" and "factor of 33" are self-reported and
    unaudited**, against a late-2022 baseline, in a 2024-06-20 post (§7.3).
    *Method: labelled vendor-claimed with its date.*
22. **Mooncake's "up to 525% increase in throughput" is explicitly "in certain
    simulated scenarios"** (§7.2). *Method: quoted with the qualifier.*
23. **The DeepSeek-V4.1-Flash-NVFP4 node total (76,248 tok/s `estimate`, c=2048)
    is 3.6× the base build's (21,248 `measured`, c=128)** (§8.1). Different
    operating points and different confidence labels; the base is the tree's
    only `measured` DeepSeek B300 row. *Method: plan against the measured
    figure; treat the estimate as an unvalidated ceiling.*
24. **Marlin-2B's §8.1 row is at 4K context** where `max_concurrency = 3,327`;
    its own pair document applies a 0.30–0.45× haircut to full-batch rooflines
    and notes queueing dominates TTFT at that concurrency (§8.1). *Method:
    quoted with the source document's own caveats.*
25. **Prefill rates for the DSA/CSA model class remain the weakest input**
    (§2.3) — this tree's open question 5, with a measured 13.4× roofline gap.
    Any P:D ratio built on an unmeasured prefill rate under-provisions prefill.
    *Method: use the measured 9,267 tok/s/GPU where it exists; refuse to
    formula-fill where it does not.*
26. **vLLM exports no `spec_decode_*` metrics** (§1.5) — the family is listed
    under "Future Work" in the metrics design doc, so the acceptance factor in
    the MBU recipe must come from your own benchmark, not a scrape. *Method:
    static recording rule `engine:accepted_tokens_per_step`.*
27. **SGLang's `estimated_*` counters need `--enable-mfu-metrics` as well as
    `--enable-metrics`** (§1.5). A dashboard built on `--enable-metrics` alone
    shows nothing for MBU/MFU. *Method: confirmed on the official metrics page;
    verify on the build.*
28. **Three citations resolve to pages that do not contain the quoted text**
    (found 2026-09-19): the Dynamo KVBM components URL 404s (§4.1), the Gateway
    API Inference Extension landing page carries none of the three routing
    quotations taken from it (§3.2, §3.4, §6.2), and the AIBrix design page
    contains no 50 %/70 % figures (§4.4). *Method: each is marked ⚠️ inline and
    must be re-sourced to a subpage or dropped.*
29. **Dynamo's Planner publishes no closed-form SLO→replica formula** (§2.3) —
    it references an internal Rust perf model. The scaling behaviour is
    therefore not reproducible from documentation. *Method: use the documented
    thresholds and treat replica counts as empirical.*

---

## Sources

Primary documents fetched and read for this document on **2026-09-19**. Sources
already catalogued by
[`../cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md)
are cited inline above but not duplicated here unless a new quotation was taken.

**Metrics and observability**

- NVIDIA DCGM, Profiling metrics reference — `DCGM_FI_PROF_*` field definitions. https://docs.nvidia.com/datacenter/dcgm/latest/learn/modules/profiling.html
- Elvinger et al., *Measuring GPU utilization one level deeper*, arXiv 2501.16909 (2025). https://arxiv.org/html/2501.16909v1
- vLLM, *Metrics* (design docs) — `vllm:*` Prometheus surface. https://docs.vllm.ai/en/stable/design/metrics/
- SGLang, *Production Metrics* — `sglang:*` surface; the `estimated_*` counters require **both** `--enable-metrics` and `--enable-mfu-metrics`. https://docs.sglang.io/references/production_metrics.html
- Kuncoro, *SGLang Prometheus Metrics: A Guide for Production Monitoring* — source for the `sglang:` → `sglang_` prefix change in v0.5.4+ (secondary; ⚠️ verify on the build). https://kuncoro.io/blog/sglang-prometheus-metrics-guide/
- NVIDIA Dynamo, *SGLang Prometheus Metrics*. https://docs.nvidia.com/dynamo/latest/backends/sglang/prometheus.html
- Databricks, *LLM Inference Performance Engineering: Best Practices* — the MBU definition and measured MBU figures. https://www.databricks.com/blog/llm-inference-performance-engineering-best-practices

**Engine configuration**

- vLLM, `docs/configuration/optimization.md` (main) — preemption remedies, chunked prefill, `max_num_batched_tokens` guidance, `-O0`..`-O3`, `torch.compile` cache, `--numa-bind`. https://github.com/vllm-project/vllm/blob/main/docs/configuration/optimization.md
- vLLM, `docs/serving/expert_parallel_deployment.md` (main) — `EP_SIZE = TP × DP`, all2all backend table, EPLB parameters and memory formula, DBO, NCCL/IB troubleshooting. https://github.com/vllm-project/vllm/blob/main/docs/serving/expert_parallel_deployment.md
- vLLM, *Data Parallel Deployment*. https://docs.vllm.ai/en/latest/serving/data_parallel_deployment/
- vLLM, *LoRA Adapters* — `--enable-lora`, `--max-loras`, `--max-cpu-loras`. https://docs.vllm.ai/en/latest/features/lora/
- vLLM **PR #5958**, *[Core] Priority-based scheduling* — policy semantics, FCFS tie-break, forced preemption, and the measured "<4%" slowdown for Llama 8B (14.56 vs 15.15 req/s). https://github.com/vllm-project/vllm/pull/5958
- vLLM issue #6077, *[RFC]: Priority Scheduling* — motivation only; contains **none** of the quotations previously attributed to it. https://github.com/vllm-project/vllm/issues/6077
- SGLang, *HiCache System Design and Optimization* — L1/L2/L3, `--hicache-*` flags, write and prefetch policies. https://docs.sglang.io/advanced_features/hicache_design.html
- NVIDIA NCCL, *Environment Variables*. https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html

**Cluster control plane**

- NVIDIA Dynamo, *Planner overview* — SLA vs load scaling, `ttft_ms` / `itl_ms`, adjustment intervals, load predictors, prefill-queue and decode-KV thresholds. https://docs.nvidia.com/dynamo/knowledge-base/modular-components/planner/overview
- NVIDIA Dynamo, *Understanding KVBM components* — G1–G4 tiers, NIXL descriptors, offload policies. ⚠️ **404 on 2026-09-19.** https://docs.nvidia.com/dynamo/latest/architecture/kvbm_components.html
- NVIDIA GPU Operator, *Time-Slicing GPUs in Kubernetes* — ConfigMap, isolation caveats. https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-sharing.html
- NVIDIA, *Supported MIG Profiles* — B200 profile table; B300 absent. https://docs.nvidia.com/datacenter/tesla/mig-user-guide/supported-mig-profiles.html
- Kueue, *Cluster Queue* — cohorts, borrowing, `reclaimWithinCohort`. https://kueue.sigs.k8s.io/docs/concepts/cluster_queue/
- Kubernetes SIG, *Gateway API Inference Extension* — Endpoint Picker. ⚠️ The landing page does **not** contain the prefix-cache-aware / LoRA-affinity / KV-utilisation quotations cited to it; re-source to a subpage. https://gateway-api-inference-extension.sigs.k8s.io/
- AIBrix, *KVCache Offloading Framework* — S3FIFO/LRU/FIFO eviction, InfiniStore/HPKV/PrisKV/EIC backends; ⚠️ **no 50 %/70 % figures on the page**. https://aibrix.readthedocs.io/latest/designs/aibrix-kvcache-offloading-framework.html

**Measurements, papers and production disclosures**

- DeepSeek, *Day 6: DeepSeek-V3/R1 Inference System Overview*, open-infra-index (2025-03). https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md
- DeepSeek, *EPLB — Expert Parallelism Load Balancer* README. https://github.com/deepseek-ai/EPLB/blob/main/README.md
- LMSYS, *Deploying DeepSeek with PD Disaggregation and Large-Scale Expert Parallelism on 96 H100 GPUs* (2025-05-05) — EP32/EP72, per-node rates, EPLB 1.49×/2.54×. https://www.lmsys.org/blog/2025-05-05-large-scale-ep/
- vLLM, *Large Scale Serving: DeepSeek @ 2.2k tok/s/H200 with Wide-EP* (2025-12-17). https://vllm.ai/blog/2025-12-17-large-scale-serving
- NVIDIA, *Scaling Large MoE Models with Wide Expert Parallelism on NVL72 Rack Scale Systems* (2025-10-20) — EP32 vs EP8 1.8× at 100 tok/s/user. https://developer.nvidia.com/blog/scaling-large-moe-models-with-wide-expert-parallelism-on-nvl72-rack-scale-systems/
- Zhong et al., *DistServe*, OSDI '24 — goodput, 2:1 at ISL 512 / OSL 64, 7.4× / 12.6×. https://www.usenix.org/system/files/osdi24-zhong-yinmin.pdf
- dstack, *Benchmarking Prefill–Decode ratios: fixed vs dynamic* (2025-09-25) — 8×H200, gpt-oss-120b, SGLang, 3:1 / 2:2 / 1:3. https://dstack.ai/blog/benchmarking-pd-ratios/
- Qin et al., *Mooncake: Trading More Storage for Less Computation*, FAST '25 (Best Paper); arXiv 2407.00079. https://www.usenix.org/conference/fast25/presentation/qin
- LMCache team, *LMCache: An Efficient KV Cache Layer for Enterprise-Scale LLM Inference*, arXiv 2510.09665 (v1 2025-10-08). https://arxiv.org/abs/2510.09665
- Character.AI, *Optimizing AI Inference at Character.AI* (2024-06-20). https://blog.character.ai/optimizing-ai-inference-at-character-ai-2/
- vLLM, *KV Offloading Connector* (2026-01-08) — measured 83.4 GB/s host DMA, block-size effect. https://vllm.ai/blog/2026-01-08-kv-offloading-connector
- Sheng et al., *S-LoRA: Serving Thousands of Concurrent LoRA Adapters*, arXiv 2311.03285. https://arxiv.org/pdf/2311.03285
- SqueezeBits, *[vLLM vs TensorRT-LLM] #10 Serving Multiple LoRAs at Once* — "under 5%" overhead and its degradation caveat. https://blog.squeezebits.com/37065
- SemiAnalysis, *InferenceMAX: Open Source Inference Benchmarking*. https://newsletter.semianalysis.com/p/inferencemax-open-source-inference
- SemiAnalysis, *InferenceX v2: NVIDIA Blackwell vs AMD vs Hopper*. https://newsletter.semianalysis.com/p/inferencex-v2-nvidia-blackwell-vs
- *Confidential LLM Inference: Performance and Cost Across CPU and GPU TEEs*, arXiv 2509.18886 — hugepages **cost 3.19–5.20 %** when 1 GB pages are unavailable (CPU TEE; **not** a null result — §5.8 corrected 2026-09-19). https://arxiv.org/pdf/2509.18886

**Commercial batch-tier pricing (for the idle-capacity economics in §3.1)**

- OpenAI, *Batch API* — 50 % discount, 24-hour window. https://developers.openai.com/api/docs/guides/batch
- Anthropic, *Batch processing* — 50 % discount on input and output tokens. https://platform.claude.com/docs/en/build-with-claude/batch-processing

**In-tree references linked from this document**

- [`../METHODOLOGY.md`](../METHODOLOGY.md) — formulas, legend, §8 pinned GPU/model inputs.
- [`../README.md`](../README.md) — tree index and the 14 cross-tree open questions.
- [`../cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md) — per-request optimizations; §1.4 KV tiers, §2.5 spec decode at batch, §3 parallelism, §4 batching/CUDA graphs/Pareto/MBU.
- [`../cross-cutting/inference-engines.md`](../cross-cutting/inference-engines.md) — pinned engine versions and per-GPU support.
- [`../cross-cutting/flash-attention.md`](../cross-cutting/flash-attention.md), [`../cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md) — kernel and format gates.
- [`../cross-cutting/inferencex-api.md`](../cross-cutting/inferencex-api.md) — re-fetching InferenceX rows.
- [`../matrix/pairs.json`](../matrix/pairs.json), [`../matrix/fit-matrix.md`](../matrix/fit-matrix.md), [`../matrix/cost-matrix.md`](../matrix/cost-matrix.md), [`../matrix/recommendations.md`](../matrix/recommendations.md) — the 40 pair analyses and their summaries.
- [`../models/deepseek41f/b300.md`](../models/deepseek41f/b300.md), [`../models/deepseek41fnvfp4/b300.md`](../models/deepseek41fnvfp4/b300.md), [`../models/qwen3827b/b300.md`](../models/qwen3827b/b300.md), [`../models/kimik3/b300.md`](../models/kimik3/b300.md), [`../models/marlin2b/b300.md`](../models/marlin2b/b300.md) — the pair documents §8 cites.
- [`../gpus/b300.md`](../gpus/b300.md), [`../gpus/gb300.md`](../gpus/gb300.md) — node topology and fabric.

---

## Verification log (2026-09-19)

Adversarial fact-check. Every claim below was re-derived or re-sourced by
opening the primary document itself (not the citation text), and every
derivation was recomputed with `python3`. `CORRECTED` rows have been edited in
place above; `UNVERIFIABLE` rows carry an inline ⚠️ **TO BE VERIFIED**.

### Engine / operator feature-support and flags

| # | § | Claim as written | Verdict | Source opened |
|---|---|---|---|---|
| 1 | 1.2 | DCGM field IDs 1001–1005, 1009–1012 and their verbatim definitions | **CONFIRMED** — field IDs and wording match exactly; one word restored to the 1003 quote ("…concurrent warps **supported**") | https://docs.nvidia.com/datacenter/dcgm/latest/learn/modules/profiling.html |
| 2 | 1.5 | vLLM `vllm:*` metric names/types incl. the three `kv_block_*` histograms | **CONFIRMED** verbatim, `vllm:kv_cache_usage_perc` is the V1 name | https://docs.vllm.ai/en/stable/design/metrics/ |
| 3 | 1.5 | `vllm:spec_decode_draft_acceptance_rate` / `_efficiency` exist and are scrapeable | **CORRECTED** — both are listed under **"Future Work"**, i.e. not exported. Metric row struck and the PromQL recipe rewritten to take the acceptance factor from a static recording rule | https://docs.vllm.ai/en/stable/design/metrics/ |
| 4 | 1.5 | SGLang `estimated_flops/read_bytes/write_bytes_per_gpu_total` "enable with `--enable-metrics`" | **CORRECTED** — the page states "available when **both** `--enable-metrics` and `--enable-mfu-metrics` are enabled". Colon prefix `sglang:` confirmed on the official page (the `sglang_` ⚠️ stands) | https://docs.sglang.io/references/production_metrics.html |
| 5 | 5.1 | vLLM `-O0`…`-O3` descriptions, `VLLM_CACHE_ROOT`, `VLLM_FORCE_AOT_LOAD=1` | **CONFIRMED** verbatim | https://github.com/vllm-project/vllm/blob/main/docs/configuration/optimization.md |
| 6 | 1.4 / 3.1 / 5.2 | Preemption remedy list; `RECOMPUTE` is the V1 default; chunked-prefill default; `max_num_batched_tokens` bullets and the `> max_model_len` guard | **CONFIRMED** verbatim, all six statements | https://github.com/vllm-project/vllm/blob/main/docs/configuration/optimization.md |
| 7 | 5.7 | `--numa-bind` rationale, `--numa-bind-nodes` / `--numa-bind-cpus`, `SYS_NICE`, EngineCore scope limit | **CONFIRMED** verbatim | https://github.com/vllm-project/vllm/blob/main/docs/configuration/optimization.md |
| 8 | 2.2 / 6.1 | `EP_SIZE = TP_SIZE × DP_SIZE`; layer-behaviour table; the DeepSeek-V3-0324 example; the "without `--enable-expert-parallel`" note; the all2all backend table and the mixed-workload warning | **CONFIRMED** verbatim | https://github.com/vllm-project/vllm/blob/main/docs/serving/expert_parallel_deployment.md |
| 9 | 5.5 | EPLB parameter defaults `window_size 1000`, `step_interval 3000`, `log_balancedness false`, `num_redundant_experts 0`, `use_async true`; the memory formula and "approximately 2.4 GB"; the `num_redundant_experts: 32` recommendation | **CONFIRMED** — every default matches | https://github.com/vllm-project/vllm/blob/main/docs/serving/expert_parallel_deployment.md |
| 10 | 5.6 | `deepep_v2` requires **NCCL ≥ 2.30.4** | **CONFIRMED** verbatim | https://github.com/vllm-project/vllm/blob/main/docs/serving/expert_parallel_deployment.md |
| 11 | 5.6 | `NCCL_IB_HCA`, `NCCL_NVLS_ENABLE` default 2, `NCCL_MIN_NCHANNELS` CUDA-block trade | **CORRECTED** — `NCCL_MIN_NCHANNELS` is **deprecated in favour of `NCCL_MIN_CTAS`** (added); `NCCL_IB_HCA`'s paraphrase replaced with the real wording and the full `<hca>[:<port>[:<rail>[:<plane>]]]` syntax; default 2 confirmed | https://docs.nvidia.com/deeplearning/nccl/user-guide/docs/env.html |
| 12 | 4.1 | SGLang HiCache L1/L2/L3 privacy sentence, `--hicache-*` flags, write and prefetch policies | **CONFIRMED** — sharing sentence and `best_effort` description verbatim; flag set and backend list match | https://docs.sglang.io/advanced_features/hicache_design.html |
| 13 | 3.3 | GPU Operator time-slicing: no memory/fault isolation, no proportional compute, the ConfigMap YAML | **CONFIRMED** verbatim, YAML field-for-field | https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-sharing.html |
| 14 | 3.3 | B200 MIG profile table (names, memory fractions, instance counts, SM fractions) and **B300 absent** | **CONFIRMED** — all 7 rows match exactly; page covers B200, RTX PRO 6000/5000/4500, Thor, H100, H200, A100, A30 and **no B300** | https://docs.nvidia.com/datacenter/tesla/mig-user-guide/supported-mig-profiles.html |
| 15 | 3.1 | Kueue `reclaimWithinCohort` definition and `LowerPriority` / `Any` | **CONFIRMED** verbatim | https://kueue.sigs.k8s.io/docs/concepts/cluster_queue/ |
| 16 | 2.3 | Dynamo Planner `ttft_ms 500.0`, `itl_ms 50.0`, `180 s` / `5 s` intervals, predictors `arima/prophet/kalman/constant`, prefill-queue and decode-KV thresholds | **CORRECTED (minor)** — all values confirmed; `load_predictor` default `arima` added, and the division-of-labour sentence replaced with the page's real wording ("capacity floor (long-term planning) … real-time adjustments above that floor") | https://docs.nvidia.com/dynamo/knowledge-base/modular-components/planner/overview |
| 17 | 4.1 | Dynamo KVBM G1–G4, NIXL/GDS, `offload.g1_to_g2` predicates | **UNVERIFIABLE** — cited URL **404s**, as does `kvbm_intro.html`. Marked ⚠️; the tier model survives on the in-tree corroboration, the config names do not | https://docs.nvidia.com/dynamo/latest/architecture/kvbm_components.html (404) |
| 18 | 3.2 / 3.4 / 6.2 | Three Endpoint Picker quotations (prefix-cache-aware routing, LoRA affinity, KV-utilisation analysis) | **UNVERIFIABLE at the cited URL** — the landing page defines the EPP only as "An implementation of an `Inference Router`". Marked ⚠️ in all three places | https://gateway-api-inference-extension.sigs.k8s.io/ |

### Measured numbers from papers, blogs and production disclosures

| # | § | Claim as written | Verdict | Source opened |
|---|---|---|---|---|
| 19 | 1.2 | Elvinger et al.: "might falsely consider a GPU fully utilized even if only a single SM is occupied" / "the percentage of time a kernel is active …" | **CONFIRMED** both verbatim; *Measuring GPU utilization one level deeper*, Elvinger, Strati, Enright Jerger, Klimovic | https://arxiv.org/html/2501.16909v1 |
| 20 | 1.3 | Databricks MBU definition; MFU-complementarity; 50 % on A100-40GB, 60 % on 2×H100, 55 % on 4×A100-40GB at batch 1; MBU falls with batch | **CONFIRMED** — definition and all three percentages exact. Quote trimmed: the blog says "MBU decreases as batch size increases", not "…across all configurations tested" | https://www.databricks.com/blog/llm-inference-performance-engineering-best-practices |
| 21 | 3.1 / 7.1 | DeepSeek day-6: 278 peak / 226.75 mean nodes, 608 B in, 342 B (56.3 %) cached, 168 B out, 20–22 tok/s, 4,989 KV len, 73.7k prefill, 14.8k decode, $87,072, $562,027, 545 %; EP32/DP32 over 4 nodes, EP144/DP144 over 18 nodes; the "8 of 256 experts", day/night reallocation, dual-batch-overlap and 5-stage-pipeline sentences | **CONFIRMED** — every figure and every quotation matches the primary document | https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md |
| 22 | 2.3 / 5.5 / 6.3 | LMSYS 96×H100: 4 nodes EP32 prefill / 9 nodes EP72 decode; 52.3k in / 22.3k out per node; 57,674 / 54,543 / 50,302; 22,282; 17,373; within 5.6 % / 6.6 % of DeepSeek's profile; EPLB **1.49× prefill / 2.54× decode** | **CONFIRMED** — all eleven figures | https://www.lmsys.org/blog/2025-05-05-large-scale-ep/ |
| 23 | 6.3 | NVIDIA wide-EP: "EP rank 32 delivers up to 1.8x higher output token throughput per GPU compared to small EP rank 8 at 100 tokens/sec per user"; "four experts per GPU per layer, for a total of 232 experts assigned per GPU"; dated 2025-10-20 | **CONFIRMED** verbatim, including the date and the fixed-interactivity qualifier | https://developer.nvidia.com/blog/scaling-large-moe-models-with-wide-expert-parallelism-on-nvl72-rack-scale-systems/ |
| 24 | 2.3 | DistServe "7.4x more requests or 12.6x tighter SLO" | **CONFIRMED** verbatim (OSDI '24) | https://arxiv.org/abs/2401.09670 |
| 25 | 2.3 | dstack: 8×H200 SXM5, `gpt-oss-120b`, SGLang; **1:3** wins; "Across all workload profiles and concurrency levels, a fixed ratio delivered robust performance"; "a fixed ratio combined with standard autoscaling…"; 2025-09-25 | **CONFIRMED** — hardware, model, engine, ratios (3:1 / 2:2 / 1:3), both quotations and the date | https://dstack.ai/blog/benchmarking-pd-ratios/ |
| 26 | 3.1 | vLLM priority scheduling: FCFS tie-break, forced-preemption sentence, "<4 %" for Llama 8B, "no performance degradation when the policy is not enabled" | **CORRECTED (citation)** — the substance is real but lives in **PR #5958**, not RFC issue #6077, whose body contains none of it. Posted pair 14.56 vs 15.15 req/s recomputed = **3.9 %**; citation and figures updated | https://github.com/vllm-project/vllm/pull/5958 |
| 27 | 3.1 | OpenAI Batch API "50 % discount", 24-hour window; Anthropic Message Batches 50 % off input **and** output | **CONFIRMED** both — "50% cost discount compared to synchronous APIs", "24h"; Anthropic "reducing costs by 50%" | https://developers.openai.com/api/docs/guides/batch · https://platform.claude.com/docs/en/build-with-claude/batch-processing |
| 28 | 4.2 | GPU↔host DMA **83.4 GB/s** bidirectional, 68.5 with custom kernels, ~50 single-direction; H100 + Xeon Sapphire Rapids, 500 GB DRAM, 2 MB blocks; "4× lower TTFT, 5× throughput" from KB→0.5–2 MB blocks | **CONFIRMED** — every figure and the hardware line | https://vllm.ai/blog/2026-01-08-kv-offloading-connector |
| 29 | 5.5 / 6.2 | DeepSeek EPLB hierarchical vs global policies; same-group-same-node placement; "the exact method to predict the loads of experts is out of this repo's scope … moving average of historical statistics" | **CONFIRMED** verbatim | https://github.com/deepseek-ai/EPLB/blob/main/README.md |
| 30 | 4.4 | LMCache "up to 15x improvement in throughput"; context truncation "can greatly reduce prefix cache hit ratio by half"; arXiv 2510.09665 v1 2025-10-08 | **CONFIRMED** verbatim, including the v1 date | https://arxiv.org/abs/2510.09665 |
| 31 | 7.2 | Mooncake "up to a 525% increase in throughput in certain simulated scenarios while adhering to SLOs"; separates prefill/decode clusters; builds the pool from underused cluster resources | **CORRECTED (minor)** — 525 % and the simulated qualifier verbatim; the resource list is "CPU, DRAM, and SSD" (**no NIC**), fixed. The abstract's production figure — Kimi handling "**75% more requests**" — was missing and has been added | https://arxiv.org/abs/2407.00079 |
| 32 | 4.4 / 7.3 | Character.AI: MQA "8X reduction" vs GQA; 1,024 local window; global attention in 1 of 6 layers; cross-layer sharing 2–3×; combined "more than 20X"; 95 % cache rate; >20,000 QPS; 180-message average; "factor of 33"; "at least 13.5X"; 2024-06-20 | **CONFIRMED** — all ten figures and the date | https://blog.character.ai/optimizing-ai-inference-at-character-ai-2/ |
| 33 | 4.4 | AIBrix "a 50% increase in throughput and a 70% reduction in inference latency" | **CORRECTED** — **neither figure appears on the cited page**, which claims only "substantial performance gains" with no workload. Replaced with a ⚠️. Eviction policies confirmed and extended (S3FIFO **default**, LRU, FIFO), backends confirmed (InfiniStore, HPKV, PrisKV, EIC) | https://aibrix.readthedocs.io/latest/designs/aibrix-kvcache-offloading-framework.html |
| 34 | 5.8 | Hugepages: "all latency and throughput metrics remain within measurement noise"; dTLB misses dropped "only 16%" | **CORRECTED — the most serious error found.** Neither string exists in arXiv 2509.18886; the PDF was decompressed and searched on 2026-09-19. The paper reports the **opposite**: losing 1 GB hugepages costs **3.19–5.20 %**, and *Insight 7* says ignoring reserved hugepages "costs up to 5% of raw performance". §5.8, the §5.9 knob row and open question 17 all rewritten | https://arxiv.org/pdf/2509.18886 |

### Cross-references into `research/` (each file opened, each number located)

| # | § | Cross-reference | Verdict |
|---|---|---|---|
| 35 | 2.1 | `deepseek41f/b300` "4 independent TP2 replicas per 8-GPU node is the max-throughput shape"; 2,656 @ 2 GPUs vs 1,373 @ 4 GPUs, TPOT 23.31, `confidence: measured`; `--engram-config '{"cpu_offload":true}'` | **CONFIRMED** verbatim in `matrix/pairs.json` |
| 36 | 2.1 | Qwen "stops buying past TP4 because the model has 4 KV heads (TP8 replicates them ×2)"; Marlin "saturates at TP2 because `num_key_value_heads=2`" | **CONFIRMED** verbatim, both in `matrix/pairs.json` |
| 37 | 2.1 | DCP 6,091 vs ~1,863 tok/s/GPU at c=512, Kimi-K2.6 NVFP4 / 8×B200 | **CONFIRMED** — `cross-cutting/serving-optimizations.md` §3.2 |
| 38 | 2.2 | "~61 GB (KDA) + 11 GB (MLA) of replicated weight" on K3 | **CONFIRMED** verbatim — `serving-optimizations.md` line 511 |
| 39 | 2.3 / open q. 25 | 9,267 `meas.` vs 123,750 roofline = 13.4× | **CONFIRMED** — `README.md` open question 5 and `matrix/cost-matrix.md`; ratio recomputed 13.35 |
| 40 | 2.4 | "2K+ TPGS … 100+ TPS/user", ~20× range, "Picking the operating point matters more than picking the GPU" | **CONFIRMED** verbatim — `serving-optimizations.md` §4.3 |
| 41 | 5.1 / 5.2 / 5.9 | 47.06 MiB graph-capture anomaly; `128 × 6 = 768` → 1024; −64 % at c=128 for 16384→2048; ~5–10 % throughput for ~2–3× p99 ITL; p99 TTFT +19–31 % | **CONFIRMED** — all five in `serving-optimizations.md` §4.1/§4.2/§4.3 |
| 42 | 4.4 / 7.2 | 1.7 % → 92.2 %, 3.8×, TTFT p50 46×, e2e 8.6×, >95 % at 12→60 GB200 | **CONFIRMED** verbatim — `serving-optimizations.md` §1.4 |
| 43 | 4.1 | Dynamo disk-offload "frequency ≥ 2 … on by default to spare SSD endurance" | **CONFIRMED** verbatim — `serving-optimizations.md` line 138 |
| 44 | 8.1 | All six §8.1 rows (shapes, per-GPU rates, concurrencies, confidence labels) and Kimi's 198.9 / 321.6 sustained rates and 10,139 ms TTFT at c=111 | **CONFIRMED** against `matrix/pairs.json` row-by-row |
| 45 | 8.1 | Marlin-2B `max_concurrency` 1,934 @ 8K and **142** @ 128K | **CONFIRMED** — `models/marlin2b/b300.md` §3.2 |
| 46 | 1.5 / METH. | DSpark γ=5 "worth 3.13× output per byte" | **CONFIRMED** verbatim — `matrix/recommendations.md` |
| 47 | Sources | "the 14 cross-tree open questions" | **CONFIRMED** — `README.md` §6 is titled "Top 14 open questions across the tree" |
| 48 | 8.3 / open q. 1 | "no per-GPU-generation MBU/MFU measurement exists anywhere in this tree" | **CONFIRMED** — `README.md` open question 4 |

### Recomputed derivations (`python3`, all exact unless noted)

| # | § | Derivation | Verdict |
|---|---|---|---|
| 49 | 2.1 | `U = 0.90 × 268 = 241.2`; threshold `(2/3) × 236.2 = 157.47`; `W = 510.29 − 202.76 = 307.53`; `W/N` = 38.44 / 76.88 / 153.77 / 307.53; budgets 197.76 / 159.32 / 82.44 (TP1 = **−71.33**, correctly "does not fit") | **CONFIRMED** |
| 50 | 2.1 | Aggregate seats 197.8 → 318.6 → **329.7** (printed 329.6, fixed); ratio **1.667** ✓ "~1.67×" | **CORRECTED (rounding)** |
| 51 | 3.1 / 7.1 / 8.5 | `278 / 226.75 = 1.2260`; `(278 − 226.75)/278 = 18.4 %` ✓ "~18 %" | **CONFIRMED** |
| 52 | 2.3 | `1/(1 − 0.563) = 2.288` ✓ "~2.3×" | **CONFIRMED** |
| 53 | 4.3 | Link tok/s at 83.4 GB/s: 93.71 M / 26.06 M / 6.03 M / 2.55 M / 6.79 M; margins 10,112× and 105×; Qwen break-even `24,300 × 32,768 = 0.80 GB/s`; `32,768/890 = 36.8` ✓ "37×" | **CONFIRMED** (all six) |
| 54 | 5.3 / 5.9 | `3,200 / 890 = 3.60` ✓ "3.6×" | **CONFIRMED** |
| 55 | 8.1 | Node totals 21,248 / 10,984 / 76,248 / 107,688 / 4,555 / 746,168 = per-GPU × 8 ✓; `76,248 / 21,248 = 3.588` ✓ "3.6×" | **CONFIRMED** |
| 56 | 3.3 | MIG headroom `23 − 5.444 − 2 = 15.56`; `90 − 30.87 − 2 = 57.13`; `45 − 21.92 − 2 = 21.08` | **CONFIRMED** |
| 57 | 3.3 | MIG seat counts "~1,290" (Marlin @ 8K) and "~1,700" (Qwen FP8 @ 8K) | **CORRECTED — wrong by ~10× and ~20×.** Both were computed against ~1K of context, not the 8K the column claims, and omitted the per-sequence state. Correct: `15.56e9/(8192×12,288 + 19.54e6) = ` **129** and `57.13e9/(8192×32,768 + 392.2e6) = ` **86**. Even ignoring the state entirely: 155 and 213 |
| 58 | 4.2 | "each step down is roughly two orders of magnitude" for 8,000 → 83.4 → ~10 GB/s | **CORRECTED** — `8000/83.4 = 95.9×` (two orders ✓) but `83.4/10 = 8.3×` (one order ✗). Rewritten as "~96× then ~8×" |
| 59 | 3.1 | vLLM priority tax "4 %" | **CORRECTED** — `(15.15 − 14.56)/15.15 = 3.89 %`; prose now says 3.9 % |

**Totals: 59 claims checked — 44 CONFIRMED, 12 CORRECTED, 3 UNVERIFIABLE
(marked ⚠️ inline).** The two changes that alter a planning decision are §3.3's
MIG seat counts (a MIG slice holds ~100 seats, not ~1,500 — MIG is a far weaker
consolidation story than the table implied) and §5.8's hugepage reversal.
