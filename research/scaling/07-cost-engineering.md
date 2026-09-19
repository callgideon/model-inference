# Reducing inference cost

Research date **2026-09-19**. This document is the **fleet-level** cost view: how a
whole cluster's money is spent, where it leaks, and which levers move it. It sits on
top of, and never re-derives, two existing documents:

- [`research/METHODOLOGY.md` §6](../METHODOLOGY.md#6-cost) — the per-token cost
  formulas, the blended definition, the cached-input convention, and the S1/S4
  scenarios. Every `$/1M` figure below is that arithmetic, not a new model.
- [`research/matrix/cost-matrix.md`](../matrix/cost-matrix.md) — the per-(model, GPU)
  `$/1M` grid at `low` / `high` / `res1y` price tiers, the vendor break-even table,
  and the sensitivity analyses. **Cells are quoted, never recomputed.**
- [`research/cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) —
  every `$/GPU-hour`, purchase price, power figure and colo rate. §5.14 is the only
  legal source of a price; §7–§9 are the only legal source of a capex or kW figure.

**Legend** ([METHODOLOGY §Legend](../METHODOLOGY.md#legend)): `[src](url)` = primary
source; **⚠️ TO BE VERIFIED** = estimate with the method stated inline; `est.` =
derived from a METHODOLOGY formula; `meas.` = published measurement.

**Software versions and dates matter.** Where a claim depends on an engine version,
a price sheet or a benchmark date, that date is printed. A price or a discount with
no date next to it in this document is a bug.

---

## 1. Cost model of a fleet

### 1.1 The identity everything reduces to

[METHODOLOGY §6](../METHODOLOGY.md#6-cost) defines the per-token cost at an
operating point:

```
cost_per_1M_output_tokens = cost_per_hour / (aggregate_output_tok_s × 3600) × 1e6
```

That formula assumes the cluster produces `aggregate_output_tok_s` **for the whole
hour**. A real fleet does not. Introducing utilisation `U` — the fraction of paid
GPU-hours actually spent producing tokens at the modelled rate — gives the identity
this whole document is about:

```
$/token  =  ($/GPU-hour × n_gpus)  ÷  (tok/s × 3600 × U)

equivalently:   $/token(U) = $/token(U = 1) ÷ U
```

Three consequences, and they are the shape of the entire cost problem:

1. **Every `$/1M` figure in [`cost-matrix.md`](../matrix/cost-matrix.md) is a
   `U = 1` figure.** It is a floor, not a forecast. At `U = 0.30` every cell in that
   document triples.
2. **`U` is worth more than any per-token optimisation below ~60 %.** Going from
   `U = 0.30` to `U = 0.60` is a 2× cost cut — larger than quantization
   ([§3](#3-per-token-levers-already-quantified-elsewhere)), comparable to
   speculative decoding, and it requires no kernel work at all. Above ~85 % the
   remaining headroom is small and the queueing cost of chasing it is real.
3. **Nobody publishes their `U`.** NVIDIA states the mechanism without numbers —
   *"A cluster running at 40% utilization produces twice the effective cost per
   token compared to the same cluster at 80% utilization."*
   [src](https://perspectives.nvidia.com/utilization-rates-inference-cluster-economics-hardware/)
   (re-fetched 2026-09-19: the quote ends there — the trailing clause *"regardless
   of the hardware platform"* printed in earlier drafts is not on the page)
   (page last updated 2026-04-13; the page itself carries an AI-generated-content
   disclaimer). The only large-sample measurement found is Cast AI's, and it is
   brutal: across *"tens of thousands of clusters"* (the press release does not name
   the clouds ⚠️), GPU utilisation averaged **5 %**, against CPU 8 % and memory 20 %
   [src](https://cast.ai/press-release/2026-state-of-kubernetes-optimization-report/)
   (published 2026-04-21). That is a general-Kubernetes number covering training,
   notebooks and dev clusters, **not** a dedicated inference fleet — but it sets the
   prior for what an unmanaged GPU estate does.

### 1.2 The five ways to buy a GPU-hour

| Mode | What you pay for | `U` you are exposed to | Interruption notice | Cost floor | Cost ceiling |
|---|---|---|---|---|---|
| **Own** (colo or on-prem) | 8,760 h/GPU/year regardless of load | 100 % yours | none — you own it | Lowest $/GPU-hr if `U` is high | Stranded capital if `U` is low; 3-year lock |
| **Rent bare metal, committed** (`res1y`) | Contract hours | 100 % yours | none for the contract term | 0.70–0.85× on-demand on H200/RTX PRO 6000 | **Not a discount everywhere** — see below |
| **Rent bare metal, on-demand** | Hours you hold the node | 100 % yours | none while you hold it | Flexible | 2.0–2.3× the owned rate |
| **Rent spot / preemptible** | Hours you hold the node, until it is reclaimed | 100 % yours, **plus** preemption risk | **two minutes**, *"emitted on a best effort basis"* ([AWS](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-instance-termination-notices.html)) | *"up to 90% off"* On-Demand ([AWS](https://aws.amazon.com/ec2/spot/)); this tree's own p6-b300 point is **−68.6 %** ($5.591 vs $17.802) | Preempted mid-request; a reload you cannot finish inside the notice — see [§6.4](#64-spot-and-preemptible-capacity) |
| **Buy tokens from an API** | Tokens only | **Zero** — the vendor eats it | n/a | You never pay for idle | You pay the vendor's margin and accept their SLO |

The `res1y` caveat is load-bearing and is already established in
[`cost-matrix.md` §7.4](../matrix/cost-matrix.md#74-reserved-res1y-pricing): B300's
cheapest published 1-year commitment (**$7.94**, DigitalOcean 12-month) is **+7.3 %
above** its cheapest on-demand rate (**$7.40**, Hyperstack), and GB300 and MI355X
have **no published commitment price at all**. The real reserved wins in this tree
are H200 (−30 %) and RTX PRO 6000 (−28 %).

Availability is a cost input, not a footnote.
[`cloud-pricing.md` §11](../cross-cutting/cloud-pricing.md) records that GCP will
not sell B200 on demand at all, AWS Blackwell is Capacity-Block-first with no
standard RIs, Together's headline H200/B200 rates require a 256-GPU minimum, and
CoreWeave was largely sold out of 2026 capacity while raising list prices. A
"cheapest" price you cannot buy at the size you need is not a price.

### 1.3 Capex and opex of an owned 8×B300 node

Every input is taken by name from
[`cloud-pricing.md`](../cross-cutting/cloud-pricing.md); nothing here is new.

| Line | Value | Source / status |
|---|---:|---|
| 8-GPU HGX B300 server, all-in | **$600,000** | ⚠️ **TO BE VERIFIED** — [`cloud-pricing.md` §7.2](../cross-cutting/cloud-pricing.md) est. $560k–$660k (DGX B200 $515k [src](https://www.gpu.fm/blog/nvidia-b200-complete-buyers-guide-2026) scaled 1.1–1.3× for HBM and TBP); **$600k is below the $610k midpoint of that band** — it is
§9.2's pinned value, used here for consistency, not the arithmetic midpoint |
| Capex per GPU | $75,000 | derived |
| Node facility draw | **15.5 kW** | ⚠️ est. — [`cloud-pricing.md` §8.2](../cross-cutting/cloud-pricing.md) 15–16 kW; §9.2 uses 1.938 kW/GPU |
| Colo, GPU-density liquid-cooled | **$180/kW-month** ⚠️ **TO BE VERIFIED** | [`cloud-pricing.md` §8.3](../cross-cutting/cloud-pricing.md)'s mid of a $150–$250 GPU-density band attributed to [Encor Advisors](https://encoradvisors.com/data-center-colocation-pricing/) ⚠️ secondary. **Re-fetched 2026-09-19: that page carries only *"250-500 kW deployments were approaching approximately $196 per kW per month in H2 2025"* (CBRE, second-hand) and a $120–$180 retail / $80–$130 wholesale band — the $150–$250 liquid-cooled band was not found on it.** No primary CBRE document was retrievable |
| Amortisation life | 36 months | planning choice; §9.2 also prints 60 months |

```
per node per month (capex + facility only)
  capex      = $600,000 / 36                     = $16,666.67
  power/colo = 15.504 kW × $180/kW-month         =  $2,790.72
  subtotal                                        = $19,457.39   →  $3.3295/GPU-hr at U = 1
```
(Arithmetic run in `python3`; reproduces
[`cloud-pricing.md` §9.2](../cross-cutting/cloud-pricing.md)'s **$3.702/GPU-hr at
`U` = 0.90** to 0.1 %: `3.3295 / 0.90 = 3.699`. §9.2 divides capex and power by
`3 × 8760 × 0.90` directly rather than by a 730.5 h month, which is the whole gap.)

**What that subtotal excludes**, per
[`cloud-pricing.md` §9.1](../cross-cutting/cloud-pricing.md): *"network fabric
(IB/Ethernet switches, cables, optics — commonly 8–15% of cluster capex), storage,
remote hands, software licences (e.g. NVIDIA AI Enterprise, which OCI charges
$2.50–$4.00/GPU-hr for), spares, staff, and cost of capital. **Add 20–35% to every
number in §9.2 for a realistic fully-loaded figure.***

Two of those are large enough to name separately at fleet scale:

- **NVIDIA AI Enterprise licensing.** At OCI's $2.50–$4.00/GPU-hr
  ([`cloud-pricing.md` §9.1](../cross-cutting/cloud-pricing.md)) this would *exceed*
  the entire owned capex+power rate. It is optional for an open-source vLLM/SGLang
  stack and mandatory for supported NIM/Run:ai deployments — the single largest
  discretionary line in an owned fleet's opex. Decide it explicitly.
- **Colo bills provisioned kW, not consumed kW**
  ([`cloud-pricing.md` §8.3](../cross-cutting/cloud-pricing.md)), so a node that
  idles at 30 % of TDP still pays 100 % of the colo line. **Power is a fixed cost in
  a colo and a variable cost only if you own the substation.** This is why
  [§6.3](#63-power-capping-and-what-it-actually-buys)'s power-capping lever is
  weaker than it looks for most readers.

### 1.4 Worked TCO — 4 and 16 8×B300 nodes over 3 years

Inputs as §1.3, plus: fabric at 12 % of capex (mid of
[`cloud-pricing.md` §9.1](../cross-cutting/cloud-pricing.md)'s 8–15 %), spares at
3 %, and staff at **⚠️ TO BE VERIFIED** — 1.0 loaded FTE for 4 nodes and 2.5 for 16,
at $260k/FTE-year. No public source for GPU-platform staffing ratios was found;
the method is "one on-call-capable platform engineer per rack-scale deployment,
sub-linear thereafter". Rent comparisons use
[`cloud-pricing.md` §5.14](../cross-cutting/cloud-pricing.md)'s B300 rows verbatim:
`low` $7.40 (Hyperstack), `res1y` $7.94 (DigitalOcean 12-mo), `high` $15.00 (OCI).

| Line, 3 years | **4 nodes (32 B300)** | **16 nodes (128 B300)** |
|---|---:|---:|
| Server capex | $2,400,000 | $9,600,000 |
| Fabric (12 %) | $288,000 | $1,152,000 |
| Spares (3 %) | $72,000 | $288,000 |
| Colo + power (36 mo @ $180/kW-mo) | $401,864 | $1,607,455 |
| Staff ⚠️ | $780,000 | $1,950,000 |
| **Total owned, 3 yr** | **$3,941,864** | **$14,597,455** |
| → $/GPU-hr at `U` = 1 | **$4.687** | **$4.340** |
| → $/GPU-hr at `U` = 0.85 | $5.515 | $5.105 |
| → $/GPU-hr at `U` = 0.60 | $7.812 | $7.233 |
| Rent 3 yr @ `low` $7.40 | $6,223,104 | $24,892,416 |
| Rent 3 yr @ `res1y` $7.94 | $6,677,222 | $26,708,890 |
| Rent 3 yr @ `high` $15.00 | $12,614,400 | $50,457,600 |
| **Own ÷ rent-`low`** | **0.63** | **0.59** |
| **Own ÷ rent-`high`** | **0.31** | **0.29** |

`est.`, `python3`; every input cited above. Read this table three ways:

1. **Owning beats renting on a 3-year horizon at any utilisation the rented node
   would also have suffered** — because both sides are exposed to the same `U`. The
   0.59–0.63 ratio against the *cheapest reputable neocloud* matches
   [`cloud-pricing.md` §9.4](../cross-cutting/cloud-pricing.md)'s conclusion
   (*"on-prem at 90% utilisation is roughly half the cheapest neocloud on-demand
   price"*) once staff and fabric are added — the added lines are exactly what turn
   "half" into "0.6".
2. **Scale barely helps.** 16 nodes is only 7 % cheaper per GPU-hour than 4, because
   capex and colo are linear and only staff is sub-linear. Do not buy a bigger
   cluster for a volume discount you will not get from the datacentre.
3. **The break-even is a duty-cycle question, not a price question.** The owned
   fleet's cost is *fixed*; the rented fleet's can be turned off. If the workload
   runs 8 h/day, 5 days/week (`U_wall` ≈ 0.24 of wall-clock **hours held**), renting
   on-demand and releasing the node wins outright — 0.24 × $6.22M = $1.5M against
   $3.94M owned. The owned case only wins when you can keep the node *held* nearly
   all the time, whether or not it is busy.

⚠️ **TO BE VERIFIED across this whole subsection:** the B300 server price, node kW,
colo rate and staffing are all estimates, three of them estimates-on-estimates
([`cloud-pricing.md` §9.2](../cross-cutting/cloud-pricing.md) flags this for the
same rows). Treat the *ratios* as robust and the *absolute* totals as ±25 %.

### 1.5 What `U` has to mean to be honest

"Utilisation" is the most-abused word in this document's subject. Four different
quantities get called it, and they differ by an order of magnitude on the same
cluster:

| Name | Definition | Typical failure |
|---|---|---|
| **Allocation** | GPU-hours assigned to a pod ÷ GPU-hours paid | Reads 100 % on an idle reserved node |
| **`DCGM_FI_DEV_GPU_UTIL`** | *"the percent of time over the past sample period during which one or more kernels was executing"* | *"if a single tiny kernel runs on 1 of an H100's 132 streaming multiprocessors for the entire sampling window, this metric reads 100%, while the other 131 SMs sit idle"* [src](https://devopsbeast.com/blog/dcgm-prometheus-gpu-observability) |
| **`DCGM_FI_PROF_SM_ACTIVE` / `SM_OCCUPANCY`** | ratio of cycles with ≥1 warp resident / ratio of warps resident per SM [src](https://github.com/NVIDIA/dcgm-exporter/blob/main/etc/dcp-metrics-included.csv) | Honest about the GPU, silent about whether the tokens were wanted |
| **Goodput-`U`** *(the one in this document's formula)* | delivered tokens ÷ (modelled tok/s × paid GPU-hours) | Requires a modelled rate to divide by — i.e. requires [`cost-matrix.md`](../matrix/cost-matrix.md) |

**Decision rule:** bill and forecast on **goodput-`U`**; alert and debug on
`SM_ACTIVE` + MBU; never put `DCGM_FI_DEV_GPU_UTIL` on a cost dashboard. The
trade-off is that goodput-`U` needs a per-(model, GPU) reference rate maintained by
hand — this tree's [`pairs.json`](../matrix/pairs.json) is exactly that artefact,
and it goes stale when engines are upgraded.

---

## 2. Utilisation levers, in cost terms

### 2.1 Choosing the operating point (batch size)

The largest single utilisation lever is not a scheduler setting — it is which point
on the throughput-vs-TPOT Pareto curve you sell.
[`serving-optimizations.md` §4.3](../cross-cutting/serving-optimizations.md)
describes the curve; [`cost-matrix.md` §2 vs §3](../matrix/cost-matrix.md) prices
its two ends. For this repo's models on B300 at the `low` tier:

| Model | S1 (TPOT ≤ 50 ms) $/1M out | S4 (max throughput) $/1M out | S1 ÷ S4 |
|---|---:|---:|---:|
| DeepSeek-V4.1-Flash | [$1.4973](../models/deepseek41f/b300.md) | [$0.774](../models/deepseek41f/b300.md) | **1.93×** |
| DeepSeek-V4.1-Flash-NVFP4 | [$4.274](../models/deepseek41fnvfp4/b300.md) | [$0.216](../models/deepseek41fnvfp4/b300.md) | **19.8×** |
| Qwen3.8-27B | [$0.1649](../models/qwen3827b/b300.md) | [$0.1527](../models/qwen3827b/b300.md) | 1.08× |
| Kimi-K3 | [$7.3914](../models/kimik3/b300.md) | [$3.61](../models/kimik3/b300.md) | **2.05×** |
| Marlin-2B | [$0.022](../models/marlin2b/b300.md) | [$0.022](../models/marlin2b/b300.md) | 1.00× |

Read: **the interactive SLO costs 1.9–2.1× on the two big MoEs and nothing on the
small dense models.** Qwen3.8-27B and Marlin-2B are already at or past their knee at
the interactive point, so there is no batch-side money to find on them — their
levers are all in [§5](#5-model-level-levers) and [§6](#6-hardware-levers). The
19.8× on `deepseek41fnvfp4/b300` is *not* a batching insight: it is the
concurrency-2,048 outlier that [`cost-matrix.md` §9.2](../matrix/cost-matrix.md)
already flags as the least defensible operating point in the matrix, and its S1
point (batch 96, TPOT 49.9 ms) is unusually bad. Do not plan on either end of that
row without a measurement.

**Decision rule.** Price two products, not one. Sell the S1 point at the interactive
price and the S4 point at a "flex"/batch price ([§2.4](#24-mixed-slo-tiers)); route
the traffic that does not care about TPOT to the S4 pool. The trade-off is a second
pool to operate and a worse `U` on *each* pool than a single merged pool would
have — which is why this only pays once each pool independently exceeds the
minimum-replica size for its model ([`fit-matrix.md` §1](../matrix/fit-matrix.md)).

### 2.2 Autoscaling tightness

Tight autoscaling raises `U` and raises tail latency; loose autoscaling does the
reverse. The cost of looseness is exactly `(1 − U)` of the fleet bill.

The reason nobody scales tight is the cold start. Measured decomposition of vLLM
startup, from the first systematic study (MLSys 2026, arXiv:2606.07362v3,
2026-06-29 [src](https://arxiv.org/abs/2606.07362)):

- Startup is **six steps** — vLLM framework bootstrapping (runtime + API server),
  tokenizer init, model loading (architecture + weight transfer), torch compilation,
  KV-cache profiling, CUDA-graph capture — and is *"predominantly CPU-bound"*, with
  only the last two GPU-bound. (Corrected 2026-09-19: earlier drafts dropped the
  framework-bootstrapping step and split model loading in two.)
- Llama3.2-3B on their rig: **20.32 s total**.
- **Engine version dominates**: across nine vLLM releases serving OPT-6.7B on an
  H100 the paper states *"there is more than 4× variance in latencies, and a 2×
  latencies reduction observed between v0.9 and v0.10"*. ⚠️ **TO BE VERIFIED —
  corrected 2026-09-19:** earlier drafts printed **"7.8 s (v0.4.0) to 36.41 s
  (v0.9.0)"**; those version-pinned seconds appear nowhere in the paper's text. The
  values live only in Figure 1, which reads roughly **3 s to >12 s**, so the
  defensible claim is the ratio (**> 4×**), not the endpoints. Pin and benchmark
  your engine version before you tune your autoscaler.
- CUDA-graph capture scales linearly with model size (PCC 0.99: Qwen-0.5B 0.91 s →
  Qwen-MoE-14.3B 1.51 s) **and with the number of captured batch sizes**
  (Llama2-7B: 0.33 s for 3 sizes → 1.8 s for 60, PCC 1.0).
- The same paper's trace analysis is the demand-side half of the argument: across
  Azure, Shanghai AI Lab, Mooncake and Alibaba production traces, **peak-to-mean
  request-rate ratios are 2–20×**, *"making resource provisioning challenging"*.

Those two facts together give the rule: **a 2–20× peak-to-mean workload cannot be
served at high `U` by reactive scaling alone when a replica takes 20 s to 15 min to
appear.** The cost-relevant responses, cheapest first:

1. **Keep the process alive and swap the weights.** vLLM Sleep Mode measures
   **0.26–0.85 s wake for Qwen3-0.6B** and **0.82–2.58 s for Phi-3-vision**, against
   **37.6 s / 58.1 s cold starts** on an A100 — *"18-200x faster model switching"*,
   and first inference **61–88 %** faster (86 % Qwen3-0.6B, 88 % Phi-3-vision on the
   A100 rig; corrected 2026-09-19 from "87–88 %")
   [src](https://vllm.ai/blog/2025-10-26-sleep-mode) (2025-10-26). Level 1 offloads
   weights to CPU RAM (*"~10-100GB+ CPU RAM per model"*), Level 2 discards them
   (*"~MB CPU RAM"*). This is the single best cost lever for a multi-model node.
2. **Scale on queue depth, not GPU utilisation** — `vllm:num_requests_waiting` is
   the metric that tracks unmet demand; KV-cache utilisation is the leading
   indicator. GPU utilisation is a *lagging* signal and scaling on it guarantees you
   are always one cold start behind.
3. **Warm pools.** Hold `ceil(peak/mean)` worth of warm-but-idle replicas and accept
   the `U` hit as an explicit, budgeted line rather than an accident.

### 2.3 Filling the troughs with batch work

If the fleet is sized for peak and peak is 2–20× the mean
([§2.2](#22-autoscaling-tightness)), then by construction most GPU-hours are trough
hours. Filling them is the highest-leverage `U` move available, and the reference
implementation is DeepSeek's own, disclosed in full:

> *"due to high service load during the day and low load at night, we implemented a
> mechanism to deploy inference services across all nodes during peak daytime hours.
> During low-load nighttime periods, we reduce inference nodes and allocate resources
> to research and training."*
> — [DeepSeek-V3/R1 Inference System Overview](https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md)
> (2025-03-01)

The same disclosure quantifies the swing: **peak node occupancy 278, average
226.75** over the 24 h from 2025-02-27 12:00 UTC+8 — i.e. the fleet ran at **81.6 %
of its own peak**, which is the highest credibly-published inference `U` in any
source found for this document. At $2/H800-GPU-hour that was **$87,072/day**
against a theoretical **$562,027/day** of revenue at R1 list pricing — the famous
**545 % cost-profit ratio**, which the same page immediately qualifies: *"our actual
revenue is substantially lower"* because V3 is priced below R1, web and app access
are free, and nighttime discounts apply. **Cite the 81.6 % occupancy, not the
545 %.**

Work that fits a trough, in descending order of how well it fits:

| Trough filler | Why it fits | Cost caveat |
|---|---|---|
| Batch/flex-tier inference ([§2.4](#24-mixed-slo-tiers)) | Same engine, same weights, no reload; preemptible at request granularity | Needs a queue and a deadline scheduler |
| Evals / regression suites | Deterministic, deadline-insensitive | Competes with batch tier for the same slots |
| Synthetic-data generation, distillation corpora ([§5.3](#53-distillation)) | Extremely deadline-insensitive | Long-running jobs resist preemption unless checkpointed |
| Fine-tuning / research | DeepSeek's stated use | Different memory profile; usually needs a node drain, not a co-tenancy |
| Batch embedding / re-indexing for RAG | Tiny KV, huge batch | Often cheaper on CPU |

**Decision rule.** Trough-filling pays when the filler's marginal value per GPU-hour
exceeds zero *and* the switching cost is below the trough length. With Sleep Mode's
sub-second wake ([§2.2](#22-autoscaling-tightness)) the switching cost is
negligible for same-node model swaps; with a full node drain and reload it is 20 s
to 15 min, so troughs shorter than ~10 minutes are not worth switching for.

### 2.4 Mixed-SLO tiers — what the vendors actually sell

Every major vendor now sells the same silicon at two or three prices separated only
by latency SLO. This is [§2.1](#21-choosing-the-operating-point-batch-size)'s Pareto
curve turned into a price list, and it is the cleanest way to monetise trough
capacity.

| Vendor | Tier | Discount | Window / SLO | Limits | Source |
|---|---|---|---|---|---|
| **Anthropic** | Message Batches | **50 %** on *all* usage incl. cache reads and writes | *"most batches finishing in less than 1 hour"*; results at completion or 24 h, whichever first; **batches expire at 24 h** (expired requests are not billed) | 100,000 requests **or** 256 MB, whichever first; results retained 29 days | [src](https://platform.claude.com/docs/en/build-with-claude/batch-processing) |
| **OpenAI** | Batch API | **50 %** | completion window *"can only be set to `24h`"* | 50,000 requests, 200 MB input file | [src](https://developers.openai.com/api/docs/guides/batch) |
| **OpenAI** | Flex (`service_tier: "flex"`) | *"priced at Batch API rates, with additional discounts from prompt caching"* — i.e. ~50 %, **synchronously** | *"slower response times and occasional resource unavailability"*; *"request timeouts are more likely"*, raise the 10-minute default | beta, limited model availability | [src](https://developers.openai.com/api/docs/guides/flex-processing) |
| **DeepSeek** | Off-peak | **50 %** (*"Off-peak rates are half of the peak rates"*) | Peak = 01:00–04:00 and 06:00–10:00 UTC, Mon–Fri, excluding Chinese public holidays | none | [src](https://api-docs.deepseek.com/quick_start/pricing) |

Three design lessons for a self-hosted fleet:

- **50 % is the market-clearing discount for "24 h instead of now".** Both US
  frontier labs landed on exactly 50 %. If your batch tier prices below ~50 % off
  you are giving away margin; above it, nobody moves.
- **Discounts compose with caching.** Anthropic's batch discount applies to cache
  reads *and* writes, and the docs note *"users typically experience cache hit rates
  ranging from 30% to 98%"* inside batches — best-effort, because batch requests are
  processed concurrently rather than in submission order
  [src](https://platform.claude.com/docs/en/build-with-claude/batch-processing).
  A self-hosted batch tier should **sort by prefix before dispatch**, which a vendor
  cannot do across tenants and you can.
- **DeepSeek's design is the cheapest to operate**: a *clock*, not a queue. No batch
  API, no job objects, no result retention — just a time-of-day price. It shifts
  demand into the trough with zero scheduler. The trade-off is that it only works if
  your demand is time-zone-concentrated (DeepSeek's is; a global fleet's is not).

**Decision rule for this repo.** Qwen3.8-27B and Marlin-2B have S1 ≈ S4
([§2.1](#21-choosing-the-operating-point-batch-size)) so a flex tier on them buys
nothing on the *serving* side — its only value is demand-shaping. The two big MoEs
have a real 1.9–2.1× S1→S4 gap, so a flex tier on DeepSeek-V4.1-Flash and Kimi-K3
is worth building; a ~50 % discount on those is roughly break-even against the
2× throughput gain, i.e. **the tier pays for itself in `U`, not in margin**.

### 2.5 Scale-to-zero for small models

Scale-to-zero is the only lever that makes `U` undefined rather than low: you stop
paying. It applies to exactly the models where a replica is small enough that the
cold start is short — in this repo, **Marlin-2B** (1 GPU,
[`marlin2b/b300.md`](../models/marlin2b/b300.md)) and arguably **Qwen3.8-27B**
(1 GPU, [`qwen3827b/b300.md`](../models/qwen3827b/b300.md)).

KEDA's `ScaledObject` is the standard mechanism; the defaults matter because they
are the thing you will get wrong
[src](https://keda.sh/docs/2.17/reference/scaledobject-spec/):

```yaml
pollingInterval:  30       # Optional. Default: 30 seconds
cooldownPeriod:   300      # Optional. Default: 300 seconds
initialCooldownPeriod: 0   # Optional. Default: 0 seconds
idleReplicaCount: 0        # Optional. Default: ignored, must be less than minReplicaCount
minReplicaCount:  1        # Optional. Default: 0
maxReplicaCount:  100      # Optional. Default: 100
```

KEDA splits the problem into an *activation* phase — *"the moment when KEDA
(operator) has to decide if the workload should be scaled from/to zero"* — and a
*scaling* phase handled by the HPA; *"if the minimum replicas is >= 1, the scaler is
always active and the activation value will be ignored"*
[src](https://keda.sh/docs/2.17/concepts/scaling-deployments/). The three numbers
that set your cost:

- **`pollingInterval` (30 s default)** is the floor on 0→1 detection latency. A
  request arriving just after a poll waits up to 30 s *before* the cold start even
  begins.
- **`cooldownPeriod` (300 s default)** is how much idle you pay for after the last
  request. At B300 `low` this is 5 min × $7.40 = **$0.62 per scale-down event** per
  GPU — trivial once, expensive if traffic oscillates around the threshold. Set it
  longer than your inter-arrival gap, not shorter.
- **`idleReplicaCount`** lets you park at a non-zero floor without paying
  `minReplicaCount`'s always-active penalty.

**Decision rule.** Scale to zero when `idle_cost_saved > cold_start_cost ×
scale_events + SLO_penalty`. Concretely: worth it for Marlin-2B's video-captioning
batch traffic (bursty, no interactive SLO, sub-minute reload of a 5.444 GB
checkpoint per [`marlin2b/architecture.md`](../models/marlin2b/architecture.md));
**not** worth it for any model behind an interactive SLO, and never for the
multi-node models — Kimi-K3 needs a full 8×B300 node
([`kimik3/b300.md`](../models/kimik3/b300.md)) and 1.56 TB of weights off local
NVMe, where the cold start is minutes.

**Cheaper alternative to consider first:** rather than scale to zero, *share the
GPU*. The NVIDIA GPU Operator supports time-slicing (*"GPU time-slicing enables
workloads that are scheduled on oversubscribed GPUs to interleave with one
another"*) and MIG (*"partition a GPU into several smaller, predefined instances,
each of which looks like a mini-GPU that provides memory and fault isolation at the
hardware layer"*), with the explicit trade-off that under time-slicing *"there is no
memory or fault-isolation between replicas"*
[src](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-sharing.html):

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
(Verbatim from the GPU Operator page; the two boolean flags were missing from
earlier drafts of this block.)

For LLM serving specifically, time-slicing is usually the **wrong** tool — two vLLM
processes each want to claim ~90 % of HBM for the KV pool, and there is no memory
isolation to stop them. Prefer **one engine process serving multiple models via
Sleep Mode** ([§2.2](#22-autoscaling-tightness)) over two processes time-slicing one
GPU. MIG is viable for Marlin-2B-class models where a 40 GB slice is ample.

---

## 3. Per-token levers already quantified elsewhere

This section **links**; it does not re-derive. Each row's percentage is the effect
that document measured or estimated, on the workload it names.

### 3.1 The table

| Lever | Measured / estimated effect | Applies to (this repo) | Where it is established |
|---|---|---|---|
| **Weight quantization** (BF16 → FP8 → NVFP4/MXFP4) | Format determines both bytes/param and whether the GPU accelerates it. Kimi-K3 is 97.9 % MXFP4 by bytes but only 46.7 % of *active* params → **FP4 speedup capped ≈ 1.89×**. On H100/A100 FP4 checkpoints run as Marlin W4A16 — *memory win, not speed* | all five | [`quantization-formats.md`](../cross-cutting/quantization-formats.md), [METHODOLOGY §8](../METHODOLOGY.md#8-pinned-inputs-converged-values-from-the-fact-checked-docs) |
| **KV quantization** (BF16 → FP8 → FP4) | DeepSeek-V4.1-Flash: **3,200 B/token BF16 (H100, meas.) → 1,650 B FP8 → 890 B FP4 on sm_100/sm_103 only**. Directly multiplies `max_concurrency` and therefore the achievable batch | both DeepSeek checkpoints | [METHODOLOGY §8](../METHODOLOGY.md#models), [`fit-matrix.md` §6.3](../matrix/fit-matrix.md) |
| **Speculative decoding** (DSpark / MTP) | **3.1× worse without it** on `deepseek41f/gb300` and `/b300`; **6.7× worse** on `/mi355x`; **3.6× worse** on `/h100`; **1.64× better with it** on `kimik3/b300`. Up to **4.3× or nothing** on `qwen3827b/a100` depending on `vcr` | DeepSeek ×2, Kimi-K3, Qwen3.8-27B | [`cost-matrix.md` §7.3](../matrix/cost-matrix.md#73-speculative-decoding-on--off) |
| **Prefix caching** | Input-leg multiplier **1.000 / 0.550 / 0.190** at h = 0 / 50 / 90 %. Blended effect depends entirely on the output share — see [§4](#4-caching-economics) | all except Marlin-2B (h ≈ 0) | [`cost-matrix.md` §7.2](../matrix/cost-matrix.md#72-prefix-cache-hit-rate-0--50--90-) |
| **Prefill/decode disaggregation** | **2–3× throughput** (vLLM, general); **+45 % cost/hour for +75 % throughput** (H100+H200, Llama-3.1-70B); **2.5× goodput** (MORI-IO, 8×MI300X). But **−20–30 %** on small or untuned deployments | fleets ≳ 1,000 GPUs | [`serving-optimizations.md` §3.5](../cross-cutting/serving-optimizations.md) |
| **Wide-EP on NVL72** | GB300 vs GB200 on DeepSeek-R1 128K/8K: peak **226.2 vs 147.9 TPS/GPU (1.53×)**; max decode batch **576 vs 320 (1.8×)** | Kimi-K3, DeepSeek ×2 | [`serving-optimizations.md` §3.4](../cross-cutting/serving-optimizations.md) |
| **MBU** (not a lever, a calibration) | Cost is **inversely linear** in MBU: ±20 % MBU = **×0.833 / ×1.25** on every `$/1M` in the tree | all | [`cost-matrix.md` §7.1](../matrix/cost-matrix.md#71-20--mbu) |

### 3.2 Combined effect, and why you cannot multiply the column

The naive product of the column above is ~20–40×. The honest combined number is
much smaller, for four reasons that each have a citation:

1. **Speculation and large batch are substitutes, not complements.** vLLM states it
   plainly: *"At batch size 1 that is a good trade: the GPU is memory-bound with
   spare compute, so the extra work (draft tokens) is close to free"* — and at
   concurrency 256, *"Draft tokens now compete with real tokens for the same
   compute, and every rejected token wastes useful compute; with enough rejected
   tokens, throughput drops significantly"*
   [src](https://vllm.ai/blog/2026-08-14-dspark-adaptive-verification) (quoted
   verbatim 2026-09-19; earlier drafts paraphrased inside quote marks). The
   speculation row's 3–6.7× is measured at operating points that
   [§2.1](#21-choosing-the-operating-point-batch-size)'s S4 column does not use.
2. **Faster silicon eats the speculation gain.** On the identical 128K/8K workload,
   MTP was worth **+14 % on GB200 and −0.9 % on GB300**
   [src](https://www.lmsys.org/blog/2026-02-19-gb300-longctx/). The 1.5× NVFP4
   tensor cores and 2× softmax SFU move decode off the memory-bound branch where
   verify slots were free. **Speculative decoding on Blackwell Ultra buys latency,
   not throughput.**
3. **Caching and output-heavy blends are substitutes.**
   [`cost-matrix.md` §7.2](../matrix/cost-matrix.md#72-prefix-cache-hit-rate-0--50--90-)
   is explicit: *"once output is ~80 % of the blend, prefix caching stops being a
   cost lever and becomes a TTFT lever."* On `deepseek41fnvfp4/b200`, output is 95 %
   of the blend and moving h from 0 % to 90 % changes blended cost by 7 %.
4. **Quantization's win is often capacity, not speed.** On H100/A100 an FP4
   checkpoint executes as W4A16 Marlin — it buys `max_concurrency` (via smaller
   weights) which buys batch which buys throughput, one indirection removed from the
   headline number.

**Decision rule for stacking.** Apply the levers in this order and re-measure after
each: (a) KV format — it gates batch; (b) batch/operating point — it gates
everything downstream; (c) prefix caching — free if your traffic has structure,
**−36.7 % throughput if it does not** (vLLM v0.6.3 on A100 with random prompts
[src](https://blog.squeezebits.com/vllm-vs-tensorrtllm-12-automatic-prefix-caching-38189);
⚠️ that is 2024-era software and is
[flagged as needing re-measurement](../cross-cutting/serving-optimizations.md)); (d)
speculation — **only if you are still memory-bound after (b)**; (e) PD
disaggregation — only above ~1,000 GPUs.

---

## 4. Caching economics

### 4.1 Hit rate is a property of the workload, not of the cache

| Workload | Measured / reported prefix-cache hit rate | Source |
|---|---:|---|
| Coding agents (Claude Code traces, 100K contexts) | **93–97 % prefix reuse across turns**; 72.4 % realised hit rate under memory pressure | [src](https://blog.lmcache.ai/en/2026/05/12/benchmarking-lmcache-for-multi-turn-agentic-workloads-on-amd-mi300x/) (2026-05) |
| Agentic traffic on Kimi K3 via OpenRouter | **92 %** | [src](https://emergent.sh/learn/kimi-k3-pricing) via [`serving-optimizations.md` §1.5](../cross-cutting/serving-optimizations.md) |
| Agentic coding traces, DeepSeek-V4.1-Flash | **94.3–98.3 %**, median 97.6 % | [`deepseek41fnvfp4/a100.md`](../models/deepseek41fnvfp4/a100.md) |
| InferenceX agentic traces, DeepSeek-V4.1-Flash | **99.7 %** — *"input cost effectively vanishes"* | [`deepseek41f/mi355x.md`](../models/deepseek41f/mi355x.md) |
| Codex agentic traces with cluster-wide KV (Mooncake) | **1.7 % → 92.2 %** | [src](https://vllm.ai/blog/2026-05-06-mooncake-store) (2026-05-06) |
| Chat with a ≥2K shared system prompt | TTFT −60–80 % (hit rate not stated) | [src](https://packet.ai/blog/vllm-prefix-caching) |
| Anthropic Message Batches (cross-tenant, unordered) | *"30% to 98%"* | [src](https://platform.claude.com/docs/en/build-with-claude/batch-processing) |
| Video captioning (Marlin-2B) | **≈ 0 %** | [`marlin2b/gb300.md`](../models/marlin2b/gb300.md) |
| Generic benchmark, no prefix structure | throughput **−36.7 %** from leaving APC on (vLLM v0.6.3, A100, 2024 software ⚠️) | [src](https://blog.squeezebits.com/vllm-vs-tensorrtllm-12-automatic-prefix-caching-38189) |

The spread is **0 % to 99.7 %**, which is why METHODOLOGY's blended definition fixes
h = 50 % as a *convention* and not a forecast. The cost consequence, from
[`cost-matrix.md` §7.2](../matrix/cost-matrix.md#72-prefix-cache-hit-rate-0--50--90-),
is the input-leg multiplier **1.000 / 0.550 / 0.190** at h = 0 / 50 / 90 %.

**Decision rule.** Measure h per *route*, not per fleet. A single blended h across a
chat endpoint (h ≈ 0.5), an agent endpoint (h ≈ 0.95) and a video endpoint (h = 0)
is an average of three different businesses and will misprice all three.

### 4.2 Cross-replica KV sharing

Per-replica prefix caching caps out at one replica's HBM. Once the working set
exceeds it, the hit rate collapses even though the *workload* is still repetitive —
the LMCache MI300X study makes exactly this point, that benefits appear *"under
stress"* when working sets exceed ~250–300k tokens, and that *"synthetic cache-rate
benchmarks understate LMCache's value by ~10-17%"* because they lack memory pressure
[src](https://blog.lmcache.ai/en/2026/05/12/benchmarking-lmcache-for-multi-turn-agentic-workloads-on-amd-mi300x/).

Two production answers, both measured:

**Mooncake** (Moonshot's own platform, integrated into vLLM v1 and TensorRT-LLM as a
KV connector). On real Codex agentic traces, Kimi-2.5 NVFP4, 12× GB200, 1 prefill +
1 decode, TP4 prefill / DP8+EP decode
[src](https://vllm.ai/blog/2026-05-06-mooncake-store) (2026-05-06):

| Metric | Result |
|---|---|
| Cache hit rate | **1.7 % → 92.2 %** |
| Throughput | **3.8×** |
| TTFT (p50) | **46× lower** |
| End-to-end latency | **8.6× lower** |
| Scaling 12 → 60 GB200 | *"scales nearly linearly"*, *">95% cache hit rate at all scales"* with round-robin routing |

**KV-cache-aware routing** (llm-d Endpoint Picker, also the basis of GKE Inference
Gateway). Red Hat's published run: 4,776 queries, 4,176 hits = **87.4 % hit rate**;
TTFT **2,850 ms → 340 ms (88 % faster)** for warm hits
[src](https://developers.redhat.com/articles/2025/10/07/master-kv-cache-aware-routing-llm-d-efficient-ai-inference)
(2025-10-07). ⚠️ That article does not state the model or GPU count for the TTFT
figures, so treat the 88 % as directional. LMCache also ships multi-node P2P CPU
memory sharing, promoted from experimental to production in 2026-01
[src](https://github.com/LMCache/LMCache).

**Decision rule.** Cross-replica KV sharing pays when `(replicas > 1) AND (working
set > per-replica HBM) AND (h_workload > ~0.5)`. For this repo: **yes** for
DeepSeek-V4.1-Flash agentic traffic (h ≈ 0.95+, 2 replicas per node), **yes** for
Kimi-K3 across nodes (1.56 TB weights, 13.5 KiB/token MLA KV), **no** for
Marlin-2B (h ≈ 0), **marginal** for Qwen3.8-27B (8 single-GPU replicas per node —
sharing helps only if the same prefixes land on different replicas, i.e. only if you
are *not* already doing prefix-aware routing). Note the ordering: **prefix-aware
routing is cheaper than a shared KV store and should be tried first** — it needs a
gateway, not a storage tier.

### 4.3 Cached-token pricing as a product lever

[`serving-optimizations.md` §1.5](../cross-cutting/serving-optimizations.md) carries
the full vendor table. The cost-engineering reading of it:

| Billing shape | Vendors | Break-even |
|---|---|---|
| **Free to write, cheap to read** | DeepSeek (46.7× discount), Moonshot (10×), OpenAI (10×), Alibaba *implicit* (5×) | Zero — the discount is pure upside |
| **Write premium, cheap to read** | Anthropic (1.25× write / 0.10× read on 5-min; 2.00× / 0.10× on 1-hour), Alibaba *explicit* | **1 read** (5-min) or **2 reads** (1-hour), from `n = (write_mult − 1)/(1 − read_mult)` |

The extremes bracket what an architecture can support. DeepSeek's cached-vs-miss
input ratio is the steepest in the table, but **the two DeepSeek pages disagree and
this document has been printing both** (resolved 2026-09-19):
**$0.003 vs $0.15 → 50×** on the API doc
[src](https://api-docs.deepseek.com/quick_start/pricing), and **$0.0030 vs $0.14 →
46.7×** on the marketing page [src](https://deepseek.ai/pricing), which is the value
[`serving-optimizations.md` §1.5](../cross-cutting/serving-optimizations.md) pinned
and the one the row above uses. **Use 46.7× and the $0.14 miss price**; the 50× came
from the API doc's $0.15 and is carried here only as the upper bracket. ⚠️ The
$0.14/$0.15 split itself is unresolved across the tree —
[`cost-matrix.md` §6.1](../matrix/cost-matrix.md#61-published-vendor-prices) still
prices the vendor blend off $0.15. Either way the discount is only possible
because V4.1-Flash's KV is 890 B/token with FP4 KV, so *"a cache hit loads
890 B/token of global KV and replays only `n_win = 128` tokens for SWA"*
([`deepseek41f/architecture.md` §11.1](../models/deepseek41f/architecture.md)).
**The architecture sets the price.** Kimi-K3's 13.5 KiB/token MLA KV is 15× larger
per token, and Moonshot's discount is correspondingly 10× rather than 50×
([`kimik3/architecture.md` §11](../models/kimik3/architecture.md)).

**Consequence for our own accounting.** [METHODOLOGY §6](../METHODOLOGY.md#6-cost)
prices a self-hosted cache hit at 10 % of uncached prefill. That is deliberately
conservative: for GPU-resident cache the true figure is ~0 (a pointer swap), and for
CPU-tier cache it is computable —
[`serving-optimizations.md` §1.5](../cross-cutting/serving-optimizations.md) works
it as a 128K DeepSeek prefix = 0.117 GB loading in **1.4 ms** at 83.4 GB/s against a
2.097 PFLOP recompute, *"three orders of magnitude cheaper"*. Keep the 10 %
convention for cross-document comparability, and know that on this model class it
overstates the cost of a hit by roughly 10×.

**Decision rule for pricing your own cached tokens.** Set the cached-input price at
`max(true marginal cost, ~2 % of miss price)` and let the discount do the demand
shaping — a steep cached-token discount is the cheapest possible incentive for
clients to structure their prompts as stable-prefix-then-variable-suffix, which is
the single behaviour that most improves your `U`.

### 4.4 Cache tiers: what each byte costs, and when to recompute instead

The tier hierarchy and its bandwidths, as the ecosystem documents them:

| Tier | Bandwidth | Cost basis | Reference |
|---|---|---|---|
| GPU HBM (L1) | 8.0 TB/s on B300 | **$0.0276/GB-hr** at B300 `low`; $0.0560 at `high` | [`cloud-pricing.md` §10.1](../cross-cutting/cloud-pricing.md) |
| CPU DRAM (L2) | ~40–63 GB/s over PCIe 5.0 (reported range) | node RAM, already paid for in capex | LMCache used **64 GB CPU DRAM L2** [src](https://blog.lmcache.ai/en/2026/05/12/benchmarking-lmcache-for-multi-turn-agentic-workloads-on-amd-mi300x/) |
| Local NVMe (L3) | ~7–12 GB/s (Gen5) | local disk, in capex | LMCache backends: *"CPU RAM, local disk (SSD), Redis/Valkey, Mooncake, InfiniStore, S3-compatible object storage, NIXL, and GDS"* [src](https://github.com/LMCache/LMCache) |
| Remote KV store | fabric-limited | separate service | Mooncake, LMCache P2P |

⚠️ **TO BE VERIFIED:** no primary source was retrievable for CPU DRAM or NVMe
$/GB-hour on a rented B300 node (the bandwidth figures above come from a secondary
aggregator). Method for planning until one exists: DRAM and NVMe are **already
bought** as part of the node — their marginal $/GB-hr is zero up to the node's
installed capacity and infinite beyond it. That, not a price list, is the real
constraint: you tier into RAM and NVMe because they are free-at-the-margin, and you
stop when the node runs out.

**The recompute-vs-load decision**, stated as a formula rather than a rule of thumb:

```
load_time(prefix) = prefix_tokens × kv_bytes_per_token / tier_bandwidth
recompute_time(prefix) = prefill_time(prefix)        [METHODOLOGY §4]

load if:  load_time < recompute_time × (1 − safety)
```

Worked for this repo's two extremes, using
[METHODOLOGY §8](../METHODOLOGY.md#models)'s pinned KV figures and a 128K prefix:

| Model | KV/token (executed) | 128K prefix bytes | NVMe load @ 10 GB/s | Verdict |
|---|---:|---:|---:|---|
| DeepSeek-V4.1-Flash on B300 | 890 B (FP4, sm_103) | 0.117 GB | **~12 ms** | Always load. [`serving-optimizations.md` §1.5](../cross-cutting/serving-optimizations.md): *"the case for aggressive CPU/NVMe KV offload is overwhelming"* |
| Kimi-K3 | 13.5 KiB (FP8 MLA, 24 of 93 layers) | **1.81 GB** (131,072 × 13,824 B) | **~181 ms** | Load, but it is a real transfer — and the per-request KDA state (2.25 GB/request, `S`=5) is **not** prefix-cacheable the same way ([`serving-optimizations.md` §1.3](../cross-cutting/serving-optimizations.md)) |

`est.`, METHODOLOGY §2 arithmetic on pinned inputs. **The general rule: KV offload
is a bandwidth trade whose attractiveness is set by `kv_bytes_per_token`, which is
an architecture property.** Sparse/latent-attention models (both DeepSeeks) make
offload nearly free; conventional-GQA and hybrid-state models make it a genuine
transfer cost, and mutable recurrent state makes it a correctness problem before it
is a cost problem.

---

## 5. Model-level levers

These are the levers that change *which* tokens you generate, and they are usually
larger than every hardware lever combined — because they attack the numerator.

### 5.1 Right-sizing: Qwen3.8-27B vs DeepSeek-V4.1-Flash per task

The blended costs on the same B300 node, from
[`cost-matrix.md` §4](../matrix/cost-matrix.md) at the `low` tier:

| Model | Blended $/1M (B300, `low`) | GPUs per replica | Ratio vs Qwen |
|---|---:|---:|---:|
| Marlin-2B | [$0.0092](../models/marlin2b/b300.md) | 1 | 0.15× |
| **Qwen3.8-27B** | [**$0.0602**](../models/qwen3827b/b300.md) | 1 | 1.00× |
| DeepSeek-V4.1-Flash | [$0.4809](../models/deepseek41f/b300.md) | 4 | **8.0×** |
| DeepSeek-V4.1-Flash-NVFP4 | [$1.16](../models/deepseek41fnvfp4/b300.md) | 4 | 19.3× |
| Kimi-K3 | [$2.3811](../models/kimik3/b300.md) | 8 | **39.6×** |

**Every request served by Qwen3.8-27B instead of DeepSeek-V4.1-Flash is an 8×
saving; instead of Kimi-K3, a 40× saving.** No kernel, quantization or scheduler
change in this document comes close. The trade-off is quality, and it is the only
one that requires an eval rather than a benchmark.

Note the ordering is *not* by parameter count: DeepSeek-V4.1-Flash-NVFP4 is 2.4×
DeepSeek-V4.1-Flash on blended cost at B300 despite being the same model, because
its B300 S1 operating point is poor ([`cost-matrix.md` §9.2](../matrix/cost-matrix.md)
flags it). Right-sizing is a (model, GPU, operating point) decision, not a model
decision.

### 5.2 Routing by difficulty, and cascades

RouteLLM is the reference open result: routers trained on Chatbot Arena preference
data, evaluated as "what fraction of queries must go to the strong model to retain
95 % of its quality"
[src](https://www.lmsys.org/blog/2024-07-01-routellm/),
[paper](https://arxiv.org/abs/2406.18665):

| Benchmark | Strong-model calls at 95 % quality | Reported saving |
|---|---:|---|
| MT Bench | **14 %** (matrix factorization + LLM-judge augmentation) | *"75% cheaper than the random baseline"*; headline *"over 85%"* vs GPT-4-only |
| MMLU | **54 %** (causal-LLM router + golden-label augmentation) | *"14% cheaper than the random baseline"*; headline 45 % vs GPT-4-only |
| GSM8K | not broken out | headline 35 % vs GPT-4-only |

⚠️ Two caveats the headline numbers hide, both visible in the table: (a) the
"over 85 %" figure is against *GPT-4-only*, while the honest comparison is against a
*random* router, where the same result is 75 % / 14 %; (b) **the saving collapses on
knowledge-heavy benchmarks** — MMLU needs 54 % of queries on the strong model. The
2024 model pair (GPT-4 / Mixtral 8x7B) is also far apart in capability; a 2026 pair
that is closer together routes worse.

**Applied to this repo**, the routing decision is between Qwen3.8-27B and
DeepSeek-V4.1-Flash, an 8× cost ratio ([§5.1](#51-right-sizing-qwen3827b-vs-deepseek-v41-flash-per-task)).
The arithmetic of a two-tier cascade:

```
blended_cost(f) = f × $0.4809 + (1 − f) × $0.0602 + router_cost      [B300, low]
  f = 1.00  →  $0.4809      (all DeepSeek)
  f = 0.50  →  $0.2706      (−44 %)
  f = 0.14  →  $0.1191      (−75 %, RouteLLM's MT-Bench operating point)
  f = 0.00  →  $0.0602      (−87 %)
```
`est.`, `python3`, on [`cost-matrix.md` §4](../matrix/cost-matrix.md) cells.

**Cascade (try-cheap-then-escalate) vs router (decide-upfront).** A cascade pays the
cheap model's cost on *every* request plus the expensive one on escalations, so its
break-even is `escalation_rate < 1 − (cheap/expensive) = 1 − 0.125 = 87.5 %` — i.e.
a cascade is cheaper than always-DeepSeek at any escalation rate below 87.5 %, but
it **adds the cheap model's latency to every escalated request**. A router adds
router latency to everything but never double-pays. **Decision rule: cascade when
the cheap model is ≥5× cheaper and the escalation signal is only visible in its
output (e.g. self-reported uncertainty, failed schema validation); route when a
cheap classifier on the *prompt* is predictive.**

### 5.3 Distillation

Distillation moves the cost saving from runtime to training time, permanently.
Reported production figures cluster at 75–90 % cost reduction, but **no primary,
methodologically-transparent measurement was found for this document** — every
figure below is a vendor or practitioner blog. The available figures are:

- *"up to 30x cost reduction while improving or maintaining competitive
  performance"* from programmatic data curation
  [src](https://www.tensorzero.com/blog/distillation-programmatic-data-curation-smarter-llms-5-30x-cheaper-inference/)
  — vendor blog. Re-fetched 2026-09-19: the benchmarks *are* named (CoNLL++ NER
  **31.0×**, BabyAI navigation 29.4×, Multi-Hop RAG 23.1×), but every figure is a
  **cost-per-successful-task** ratio against GPT-4.1, not a cost-per-token ratio, so
  it is not comparable with anything else in this document. ⚠️
- ~75–80 % inference-cost reduction reported in practitioner write-ups
  [src](https://redis.io/blog/model-distillation-llm-guide/). ⚠️ secondary.

**⚠️ TO BE VERIFIED.** Treat distillation's saving as *bounded above by the
right-sizing ratio in [§5.1](#51-right-sizing-qwen3827b-vs-deepseek-v41-flash-per-task)*
— a distilled model is a smaller model, and the smaller model's serving cost is
already in the table. Distillation's real contribution is **raising the fraction
`(1 − f)` that the cheap tier can handle**, i.e. it makes
[§5.2](#52-routing-by-difficulty-and-cascades)'s cascade work at a lower `f`, not a
new cost mechanism. Budget it as a one-off training cost (fillable into a trough,
[§2.3](#23-filling-the-troughs-with-batch-work)) amortised over the serving volume
it shifts.

### 5.4 Output-length control

Output tokens are 25 % of the blended token mix by METHODOLOGY's convention but
**60–95 % of the blended cost** in this repo's models
([`cost-matrix.md` §7.2](../matrix/cost-matrix.md#72-prefix-cache-hit-rate-0--50--90-)'s
"output share" column: DeepSeek 61 %, DeepSeek-NVFP4 95 %, Qwen 68 %, Kimi-K3 78 %,
Marlin 60 %). **Output length is therefore the largest single token-side lever.**

Mechanisms, cheapest to implement first:

1. **`max_tokens` as a hard budget, per route.** Free, immediate, and the only one
   with no quality-model risk. The cost of getting it wrong is truncation, which is
   visible.
2. **Reasoning/thinking budget caps.** Agentic traffic is the problem case: Gartner
   (March 2026, cited secondhand) puts agentic tasks at 5–30× the tokens of a chat
   turn ⚠️; the mechanism is uncontroversial even where the multiplier is not
   sourced.
3. **Budget-aware prompting / control tokens.** Published methods report
   **27–51 % reduction in chain-of-thought length with no accuracy loss** across
   text, visual and video reasoning ⚠️ (reported in a 2026 arXiv preprint,
   [arXiv:2606.03965](https://arxiv.org/pdf/2606.03965); not independently
   replicated). Treat as directional.
4. **Structured outputs.** ⚠️ **TO BE VERIFIED** — no measurement was retrievable.
   The mechanism is sound and worth stating as a hypothesis to test: constrained
   decoding against a JSON schema removes preamble and prose from the output, and a
   schema-constrained answer is typically a small fraction of the tokens of the same
   answer in prose. The counterweight is grammar-compilation overhead per unique
   schema and reduced batching efficiency when many distinct grammars are live.
   **Measure both before claiming a saving.**

**Decision rule.** Cap output length per route before touching anything in
[§3](#3-per-token-levers-already-quantified-elsewhere) or
[§6](#6-hardware-levers). A 30 % cut in mean output length is worth 18–29 % of the
blended bill on this repo's models — more than the entire gain from moving to a
newer GPU generation on three of five.

### 5.5 Prompt compression

LLMLingua (Microsoft Research, EMNLP'23 / ACL'24,
[arXiv:2310.05736](https://arxiv.org/abs/2310.05736)) uses a small LM (GPT2-small,
LLaMA-7B) to drop non-essential prompt tokens, claiming *"up to 20x compression with
minimal performance loss"*
[src](https://github.com/microsoft/LLMLingua), evaluated on GSM8K, BBH, ShareGPT and
Arxiv-March23.

Three reasons that 20× does **not** become 20× off your bill:

1. **It only touches the input leg.** At METHODOLOGY's 75/25 mix with h = 50 %, input
   is 39 % of the blend on DeepSeek-V4.1-Flash and **5 %** on
   DeepSeek-V4.1-Flash-NVFP4 ([§3.2](#32-combined-effect-and-why-you-cannot-multiply-the-column)).
2. **It competes directly with prefix caching, and loses.** Compression *destroys
   the stable prefix* that the cache keys on. On agentic traffic at h = 0.95+
   ([§4.1](#41-hit-rate-is-a-property-of-the-workload-not-of-the-cache)) the cached
   input already costs ~2–10 % of a miss; compressing it to 1/20th of a *miss* is
   strictly worse than hitting the cache. **Do not run prompt compression and prefix
   caching on the same route.**
3. **The compressor is itself an inference cost** — a 7B forward pass over the full
   prompt, on every request.

**Decision rule.** Prompt compression is for **long, unique, un-cacheable inputs**:
one-shot document analysis, RAG over freshly-retrieved passages, bulk
classification of novel text. For chat and agents, prefix caching dominates it.
⚠️ The 20× headline is the paper's best case; no independent 2026 replication was
found.

### 5.6 Token accounting and chargeback per tenant

You cannot manage any lever in this section without per-tenant token accounting.
OpenCost (CNCF) ships this natively as of the current `develop` branch, for
vLLM-based deployments including llm-d
[src](https://github.com/opencost/opencost/blob/develop/docs/inference-cost-tracking.md):

```yaml
env:
  - name: INFERENCE_COST_ENABLED
    value: "true"
  - name: INFERENCE_MODEL_LABEL
    value: "llm-d.ai/model"
```

It collects `prompt_tokens_total`, `generation_tokens_total`, prefill/decode timing
and KV-cache hits from vLLM via Prometheus, joins them to OpenCost's allocation
layer, and emits:

| Metric | Meaning |
|---|---|
| `llm_total_hourly_cost` | *"Hourly infrastructure cost attributed to a model"* — instantaneous $/hour rate |
| `llm_cost_per_million_tokens{phase=""}` | blended |
| `llm_cost_per_million_tokens{phase="prompt"}` | *"Cost per 1M **delivered** input tokens"* |
| `llm_cost_per_million_tokens{phase="generation"}` | output |
| `llm_cache_savings_fraction` | *"Fraction of prompt tokens served from the KV cache (range 0–1)"* |

The design decision worth copying is the **two cost bases**, which is precisely
[§1.5](#15-what-u-has-to-mean-to-be-honest)'s distinction made billable:

| Basis | Definition | Use |
|---|---|---|
| `cost_basis=allocation` | `max(request, usage) × price` + **idle share** + shared-infra share. *"Reconciles to the infrastructure bill."* | chargeback / showback |
| `cost_basis=usage` | actual consumption only; *"Does **not** reconcile to the bill; idle and shared infrastructure costs are excluded."* | workload efficiency analysis |

**Decision rule.** Charge tenants on `allocation` (so idle has an owner) and tune on
`usage` (so engineers see the workload, not the fleet's idle). Publishing only
`usage` to tenants is how a fleet ends up with nobody responsible for the 40 % that
is idle. Note also the `allocation_method` label: when vLLM timing metrics are
unavailable, OpenCost falls back to a *"fixed output/input cost ratio (default
2.5×)"* — check that label before trusting an input/output split.

---

## 6. Hardware levers

### 6.1 Generation choice — $/token by GPU

Already computed. [`cost-matrix.md` §8](../matrix/cost-matrix.md#8-ranking-per-model)
ranks every model; the blended `low`-tier winners:

| Model | Cheapest blended GPU | $/1M blended | Runner-up |
|---|---|---:|---|
| DeepSeek-V4.1-Flash | [B200](../models/deepseek41f/b200.md) | **$0.19** | A100 $0.361 * |
| DeepSeek-V4.1-Flash-NVFP4 | [B200](../models/deepseek41fnvfp4/b200.md) | **$0.1485** | H100 $0.254 * |
| Qwen3.8-27B | [B300](../models/qwen3827b/b300.md) | **$0.0602** | B200 $0.0633 |
| Kimi-K3 | [B300](../models/kimik3/b300.md) | **$2.3811** | B200 $4.402 |
| Marlin-2B | [B300](../models/marlin2b/b300.md) | **$0.0092** | H100 $0.0095 |

The two structural findings in that ranking, both cost-relevant and both already
established: **the newest GPU is not the cheapest for three of five models** (B200
beats B300 on both DeepSeek checkpoints), and **best tokens/s/GPU is rarely the
cheapest cell** — GB300 takes a top-3 throughput slot for three of five models but a
top-3 *cost* slot only for Kimi-K3, because at $18.00/GPU-h it is 2.4× H100's `low`
with no second seller.

External corroboration of the generational effect, measured on different models by
SemiAnalysis InferenceX (*"close to 1000 frontier GPUs for a full benchmark run
across all SKUs"* — corrected 2026-09-19 from "~200 chips"): on MiniMax M2.5/M2.7
230B at 66 tok/s/user,
**B200 $0.10/M vs H100 $0.26/M**; at 89 tok/s/user, **$0.15 vs $0.40**
[src](https://inferencex.semianalysis.com/compare-per-dollar/minimax-m27-b200-vs-h100).
Note InferenceX *"benchmarks on random data and disables prefix caching"*
[src](https://newsletter.semianalysis.com/p/inferencex-v2-nvidia-blackwell-vs), so
its figures are an un-cached floor, systematically pessimistic for agentic traffic.
Re-fetch per [`inferencex-api.md`](../cross-cutting/inferencex-api.md).

### 6.2 Mixing generations

The fit and cost matrices make the mixed-fleet case concrete for this repo:

| Tier | Hardware | Models | Rationale |
|---|---|---|---|
| **Small-model tier** | H100 or H200, 1 GPU/replica | Marlin-2B (H100 $0.0095 blended), Qwen3.8-27B (H100 $0.089 / H200 $0.082) | Both fit on one GPU on every GPU in the tree ([`fit-matrix.md` §1](../matrix/fit-matrix.md)); the Blackwell premium buys throughput these models do not need at their operating point |
| **MoE tier** | B200 or B300, 4–8 GPU/replica | DeepSeek-V4.1-Flash (B200 $0.19), Kimi-K3 (B300 $2.3811 — **B300 is the only GPU where Kimi clears break-even against Moonshot's API**, at 48 %/97 %) | FP4 KV (890 B/token) executes **only** on sm_100/sm_103; wide-EP needs the NVLink domain |
| **Trough tier** | whatever is cheapest and idle | batch/flex ([§2.4](#24-mixed-slo-tiers)), evals, distillation corpora | `U` is the product, not latency |

**Decision rule.** Mix generations when the model set spans ≥8× in blended cost and
≥4× in GPUs-per-replica — which this repo's does (Marlin-2B $0.0092 vs Kimi-K3
$2.3811 = 259×; 1 GPU vs 8). The cost of mixing is a heterogeneous scheduler,
per-generation engine builds and per-generation kernel gaps — and the kernel gaps
are real here: the H100 tier cannot run DeepSeek's FP4 KV path at all
([METHODOLOGY §8](../METHODOLOGY.md#models)), and `--disable-radix-cache` on
SGLang/MI350X forfeits prefix caching entirely, *"worth roughly 2× on blended cost
for agentic traffic"* ([`deepseek41f/mi355x.md`](../models/deepseek41f/mi355x.md)).

### 6.3 Power capping, and what it actually buys

This lever is almost entirely a **facility** lever, not a cost-per-token lever, and
the 2026 measurement is unusually clear about why.

*The Illusion of Power Capping in LLM Decode* (arXiv:2605.11999v1, 2026-05-12
[src](https://arxiv.org/html/2605.11999)), H200 SXM (700 W TDP), **five** ~4B
architectures — GQA, GQA-ctrl (Minitron-4B, the ablation control), MLA (a TransMLA
variant), Gated DeltaNet and Mamba2 — vLLM BF16 (corrected 2026-09-19 from "four"):

| Finding | Number |
|---|---|
| Decode power draw | **137–300 W** on a 700 W GPU — *"no cap we apply ever triggers, because memory-bound decode saturates HBM bandwidth"* |
| Power capping efficacy | *"power-cap configurations producing nearly identical results regardless of cap level (280–700W)"* |
| SM clock locking, energy saved | **up to 32 %** of decode energy (GDN: 30 % at BS=1, 32 % at BS=32) |
| Throughput cost of that | **< 1 %** at optimal settings |
| The wasted band | **1590–1830 MHz** (corrected 2026-09-19 from "1590–1980"): requesting 1980 MHz *"yields only ≈1830 MHz sustained; all settings ≤1590 MHz are honoured exactly"*, and across that 240 MHz gap the *"median throughput difference between 1590 and 1980 MHz is <<0.1%"* while *"the extra clock cycles only waste power (+7–13%)"* |
| Verdict | SM clock locking *"Pareto-dominates power capping universally"* |

Two corroborating results. **Corrected 2026-09-19:** arXiv:2501.08219
(*Characterizing LLM Inference Energy-Performance Tradeoffs across Workloads and GPU
Scaling*, five decoder-only LLMs 1B–32B, four NLP benchmarks) does **not** say "up to
30% without requiring any modifications to the model" — that quote is not in the
paper. What it says is that decode *"dominates inference time (77-91%) and is largely
insensitive to GPU frequency"*, so *"reducing GPU frequency from 2842 MHz to 180 MHz
achieves an average of 42% energy savings with only a 1-6% latency increase"*
[src](https://arxiv.org/abs/2501.08219) — a **larger** effect than this document
previously claimed, on a much wider clock range, and it corroborates the
2605.11999 mechanism rather than the 30 % number. A separate multi-request study finds
raising the cap beyond ~200 W stops reducing latency
[src](https://arxiv.org/pdf/2604.09611) ⚠️ (different, smaller hardware — do not
transfer the 200 W figure to a B300).

**What this means for your bill.** If you rent, power capping saves you **nothing** —
you pay $/GPU-hour. If you colo, it saves you **nothing on the colo line** either,
because colo bills *provisioned* kW
([`cloud-pricing.md` §8.3](../cross-cutting/cloud-pricing.md)). It saves money only
where you pay metered electricity, and it buys **density** — more GPUs behind the
same breaker — everywhere. Given that a GB300 NVL72 rack is **135 kW TDP / up to
155 kW peak**
[src](https://lenovopress.lenovo.com/lp2357-lenovo-nvidia-gb300-nvl72-rack-scale-ai),
and [`cloud-pricing.md` §8.2](../cross-cutting/cloud-pricing.md) calls rack power
*"the single biggest constraint on on-prem Blackwell-Ultra deployment"*, density is
the real prize.

**Decision rule.** Lock SM clocks (`nvidia-smi -lgc`) at the knee, do **not** set a
power cap, and re-measure per model — the paper's spread across four attention
architectures on identical hardware is the warning that this is architecture-
dependent. ⚠️ The measurement is on H200 with ~4B models; whether the same knee
exists on B300 with a 552B-backbone MoE is **TO BE VERIFIED**, and the prior is that
it is *weaker*, because Blackwell Ultra's 1.5× FP4 tensor cores push decode off the
memory-bound branch ([§3.2](#32-combined-effect-and-why-you-cannot-multiply-the-column)).

### 6.4 Spot and preemptible capacity

**Scope, first.** This lever applies to **the AWS p6 burst tier only**. The owned
8×B300 nodes have **no spot tier** — you bought the hours, nobody can reclaim them,
and §1.2's "Own" row has no interruption column for that reason. Everything below
concerns the p6-b300 capacity
[`05` §6.5](05-autoscaling-and-predictive-scaling.md#65-hybrid-reserved-bare-metal--cloud-burst-and-the-cross-over)
provisions alongside the metal.

**The published terms.**

| Term | Value | Source |
|---|---|---|
| Discount band | *"up to 90% off"* On-Demand | [aws.amazon.com/ec2/spot](https://aws.amazon.com/ec2/spot/) |
| This tree's actual p6-b300 point | **$5.591 vs $17.802/GPU-hr = −68.6 %** on-demand; **−29.6 %** vs `res1y` $7.94 | [`05` §6.5](05-autoscaling-and-predictive-scaling.md) (Vantage-reported) ⚠️ |
| Interruption notice | *"a warning that is issued **two minutes** before Amazon EC2 stops or terminates your Spot Instance"*; *"Interruption notices are emitted on a **best effort** basis"*; check *"every 5 seconds"* | [AWS EC2 UG — interruption notices](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-instance-termination-notices.html) |
| Notice exception | hibernate gets a notice but **no two-minute warning**, *"because the hibernation process begins immediately"* | same |
| Why you are reclaimed | capacity (*"when it needs it back"*, host maintenance, hardware decommission), price above your max, or a launch-group/AZ-group constraint | [AWS EC2 UG — interruptions](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-interruptions.html) |

Note the second row: the headline *"up to 90 %"* is not what this fleet's instance
type is actually priced at. Use −68.6 %, and hold it ⚠️ — p6-b300 spot availability
and interruption rates are **not published** ([`05` §6.5](05-autoscaling-and-predictive-scaling.md)).

**The survivability test, which decides the question.** A two-minute notice is only
useful if a replacement replica can be *ready* inside it. It cannot be. From
[`06` §1](06-cold-start.md#1-what-a-cold-start-actually-costs), cold-start to
first-token on this repo's models:

| Model | Weights pre-staged on node NVMe | Cold pull from S3 | Fits in a 2-min notice? |
|---|---:|---:|---|
| Marlin-2B | ≈ 35–55 s | ≈ 37–57 s | **yes** |
| Qwen3.8-27B | ≈ 50–80 s | ≈ 65–95 s | **marginally**, staged only |
| DeepSeek-V4.1-Flash (±NVFP4) | ≈ 2–3 min | ≈ 4.5–6 min | **no** |
| Kimi-K3 | ≈ 3.5–5 min | ≈ 11–13 min | **no** |

[`05` §6.5](05-autoscaling-and-predictive-scaling.md) states the same test in one
line — *"the entire question for spot is whether a 2-minute interruption notice is
survivable for a replica with a 5–12 min reload. For DeepSeek and Kimi it is not;
for Marlin-2B it plausibly is."* This section is where that arithmetic enters the
cost model, and the conclusion is narrow:

- **Never for an interactive pool with a warm floor.** The floor exists to absorb
  bursts without a cold start ([§2.5](#25-scale-to-zero-for-small-models)); a floor
  replica that can vanish on two minutes' notice is not a floor. Worse, correlated
  reclamation is the normal case — spot capacity is pulled by instance type and AZ,
  so you lose several at once, exactly when you cannot rebuild any of them.
- **Only for the batch / flex tier of [§2.4](#24-mixed-slo-tiers--what-the-vendors-actually-sell).**
  That tier already sells a 24-hour window for a 50 % discount. A job that may be
  restarted twice inside 24 h violates nothing it promised.
- **Marlin-2B is the one exception worth testing**, because its 35–57 s boot fits
  the notice and it can be drained and re-placed like any stateless replica.

**Checkpoint-resume economics.** Deferrable work only survives preemption if it
checkpoints, and the break-even is

```
spot is worth it  iff  (C_ondemand − C_spot) × T  >  checkpoint_write_cost × E[preemptions in T]
```

where `checkpoint_write_cost` here is **not** model state — it is the cost of
re-reaching the point you lost, i.e. the reload plus the in-flight batch. Per node
(8 GPUs), from the prices above:

```
spot saving vs on-demand = (17.802 − 5.591) × 8 = $97.69/node-hour
spot saving vs res1y     = ( 7.940 − 5.591) × 8 = $18.79/node-hour
cost of one preemption, Kimi-K3 cold pull = 13 min × 8 × $5.591/hr = $9.69
break-even, vs on-demand = 97.69 / 9.69 = ~10.1 preemptions/hour
break-even, vs res1y     = 18.79 / 9.69 = ~1.9 preemptions/hour
```

`est.`, `python3`, on the ⚠️ Vantage spot price. Read that carefully: on the **cost**
axis spot tolerates roughly ten interruptions an hour before it stops paying — no
plausible interruption rate gets near that. **The binding constraint is the SLO, not
the money.** Which is the whole point of scoping spot to the batch tier.

The checkpoint *mechanism* costs nothing extra because it already exists: §2.4's
deferred tier is a durable queue with per-request granularity, and
[`12` §6.1 row 11](12-inference-providers.md#61-the-ranked-list) records the shape
five operators ship — *"queue-based `/run` with guaranteed execution and retries"*.
A completed request's output tokens **are** the checkpoint; the unit of lost work is
one in-flight request plus the reload, not a job. So the only term worth measuring
is the reload, and there is nothing new to build: drain on the two-minute notice,
let in-flight requests finish or requeue, and re-place on reserved capacity.

⚠️ **TO BE VERIFIED:** p6-b300 spot interruption rate (AWS publishes the Spot
placement score and interruption frequency for common types, not for p6-b300), and
whether a two-minute drain is long enough for an in-flight Kimi-K3 request at the
§2.4 batch operating point — at 10.1 s TTFT plus decode it plausibly is not.

**Egress and storage for weight distribution.** The other cost line §1.3's node
model omits. Every cold pull moves the checkpoint, and Kimi-K3's is
**1,560,936,091,448 B = 1,560.9 GB**
([`kimik3/b300.md` §4](../models/kimik3/b300.md), from `architecture.md` §1.2/§4).
[`12` §6.1 row 4](12-inference-providers.md#61-the-ranked-list) states the fan-out
arithmetic: *"Pulling 1.56 TB to eight nodes from one object store is **12.5 TB** of
origin traffic; the ring makes it **1.56 TB**"* — an **8× reduction in origin bytes**
for one Baseten-style consistent-hash peer ring
([`01` §5](01-bare-metal-cluster.md), [`06` §2](06-cold-start.md)).

What those bytes cost depends entirely on where the origin sits:

| Path | Per-GB transfer | 12.5 TB naive fan-out | 1.56 TB peer ring |
|---|---|---:|---:|
| S3 → EC2, **same region** | **$0.00** — *"Data transferred from an Amazon S3 bucket to any AWS service(s) within the same AWS Region … are free"* ([S3 pricing](https://aws.amazon.com/s3/pricing/)) | $0 | $0 |
| S3 GET requests, 8 MiB ranges | **$0.0004 per 1,000** ([S3 pricing](https://aws.amazon.com/s3/pricing/)) | ~1.49 M GETs = **$0.60** | ~186 k GETs = **$0.07** |
| On-prem origin → AWS p6 | ⚠️ **TO BE VERIFIED** — your transit/IX bill, not AWS's (AWS data transfer *in* is free); at a placeholder $0.02/GB this is **$250** vs **$31** per fan-out | ⚠️ | ⚠️ |
| S3 Standard at-rest, 1,560.9 GB | ⚠️ **TO BE VERIFIED** — the S3 pricing page does not render a per-region rate; at the widely-quoted $0.023/GB-month ⚠️ it is **$35.90/month** to keep one Kimi-K3 checkpoint hot | — | — |

`est.`, `python3` (GET counts = 1,560.9 GB ÷ 8 MiB × 8 nodes and × 1).

**Decision rule.** Keep the origin in the same AWS region as the p6 burst tier and
the dollar cost of weight distribution collapses to request charges — under a dollar
per full fleet cold pull, which is noise against $97.69/node-hour. Put the origin
on-prem and it becomes a real per-cold-start line *and* a bandwidth bottleneck. The
peer ring is worth building for **time**, not for money: it is the difference
between 12.5 TB and 1.56 TB crossing one link while eight nodes wait.

### 6.5 Secondhand A100 / H100 economics

⚠️ All prices in this subsection are trade-press or broker aggregates; **no
primary source publishes used-GPU prices.** Treat as indicative.

| Asset | Used price, 2026 | Residual pattern | Source |
|---|---|---|---|
| H100 SXM5 | $18,000–$22,000 stabilised; wider market $15,000–$28,000 | ~90 % at 12 mo, 80 % at 24 mo, **60–65 % at 36 mo**, then ~−10 %/yr | [src](https://hashrateindex.com/blog/used-gpu-market-pricing-deprecation-secondary-ai/), [src](https://www.mercatus-ai.com/blog/h100-depreciation) ⚠️ |
| A100 80GB | $12,000–$18,000 | **30–50 % of original list** by 2026; a further 10–15 % decline expected through 2026 | [src](https://hashrateindex.com/blog/used-gpu-market-pricing-deprecation-secondary-ai/) ⚠️ |
| H100, decommissioned/refurb | $18,000–$22,000 | — | [`cloud-pricing.md` §7.1](../cross-cutting/cloud-pricing.md) citing [Mercatus](https://www.mercatus-ai.com/blog/h100-gpu-cost) ⚠️ |

The arithmetic that matters: at a used H100 price of **$20,000/GPU**, an 8-GPU
secondhand node costs ~$160k in GPUs plus a platform. Re-running
[§1.3](#13-capex-and-opex-of-an-owned-8b300-node)'s model at $200k/node and
1.275 kW/GPU (H100 SXM, [`cloud-pricing.md` §8.1](../cross-cutting/cloud-pricing.md)):

```
capex term  = 200,000 / 36 months                    = $5,555.56/node-month
power term  = 10.2 kW × $180                          = $1,836.00/node-month
total                                                  = $7,391.56  →  $1.265/GPU-hr at U = 1
```
`est.`, `python3`. Against Hyperstack's H100 `low` of **$3.20/GPU-hr**
([`cloud-pricing.md` §5.14](../cross-cutting/cloud-pricing.md)) that is **2.5×
cheaper**. **Corrected 2026-09-19:** the comparison against on-prem-new H100
($1.856/GPU-hr, [`cloud-pricing.md` §9.2](../cross-cutting/cloud-pricing.md)) was
printed as "~1.5× cheaper", but §9.2's figure is at `U` = 0.90 and $1.265 is at
`U` = 1. **At the same `U` = 0.90 the secondhand node is $1.405/GPU-hr, i.e.
1.32× cheaper, not 1.5×.** (`python3`: 7,391.56 / (8 × 730.5 × 0.90) = 1.405;
1.856 / 1.405 = 1.32.)

**Decision rule.** Secondhand Hopper is the cheapest way to serve the
**small-model tier** ([§6.2](#62-mixing-generations)) — Marlin-2B's cheapest cells
are H100 and A100 anyway, and Qwen3.8-27B's H100 blend is $0.089. It is the
**wrong** buy for the MoE tier: H100 cannot execute DeepSeek's FP4 KV path
([METHODOLOGY §8](../METHODOLOGY.md#models)), needs ≥32 cards for Kimi-K3
([`cost-matrix.md` resolution log](../matrix/cost-matrix.md)), and `kimik3/h100`
never reaches TPOT ≤ 50 ms at any concurrency. The risk to price in is the residual
cliff: H100s *"will depreciate sharply when Rubin-architecture GPUs arrive (late
2026–2027)"* ⚠️ — buy them on a ≤24-month payback, not a 36-month one.

### 6.6 NVL72 vs HGX

| | HGX B300 (8-GPU node) | GB300 NVL72 (rack) |
|---|---|---|
| Memory as deployed | 268 GB/GPU, 2,144 GB/node | 288 GB/GPU (≈279 usable), 72-GPU NVLink domain |
| Dense BF16 / FP8 / FP4 | 2,250 / 4,500 / 13,500 | 2,500 / 5,000 / 15,000 |
| Rack power | ~15–16 kW/node ⚠️ | **135 kW TDP, up to 155 kW peak**, ~90 % liquid |
| Cheapest published $/GPU-hr | $7.40 (Hyperstack) | **$18.00 (OCI — the only published rate worldwide)** |
| Purchase | ~$600k/node ⚠️ | ~$3.3–3.8M/rack ⚠️ est. |

All rows: [METHODOLOGY §8](../METHODOLOGY.md#gpus),
[`cloud-pricing.md` §5.14/§7.2/§8.2](../cross-cutting/cloud-pricing.md).

The measured cost comparison inverts with interactivity — SemiAnalysis InferenceX on
DeepSeek R1, at a stated owning-hyperscaler chip price of **B300 $2.26/chip/hr vs
GB300 NVL72 $2.31/chip/hr** (*"SemiAnalysis Market July 2026 Pricing Surveys & AI
Cloud TCO Model"*)
[src](https://inferencex.semianalysis.com/compare-per-dollar/deepseek-r1-b300-vs-gb300):

| Interactivity | B300 $/M | GB300 NVL72 $/M | Winner |
|---|---:|---:|---|
| 88 tok/s/user | $0.13 | **$0.06** | GB300, *"102% more total tokens per dollar"* |
| 162 tok/s/user | $1.16 | **$0.33** | GB300, *"255% more cost-efficient"* |
| 235 tok/s/user | **$2.68** | $2.76 | B300, by 3 % |

**Decision rule.** NVL72 wins where the all-to-all stays inside the NVLink domain —
large-MoE decode at moderate interactivity, which is exactly Kimi-K3 and
DeepSeek-V4.1-Flash. It loses at extreme interactivity and it loses in *this tree's*
cost matrix for a different reason entirely: **there is one seller.** At OCI's
$18.00 with no published commitment price
([`cost-matrix.md` §1](../matrix/cost-matrix.md)), GB300 is expensive here because
nobody discounts it, not because it is slow. If you can buy GB300 at the
$2.31/chip/hr owning-cost SemiAnalysis models, every GB300 cell in
[`cost-matrix.md`](../matrix/cost-matrix.md) drops ~7.8× and the rankings invert.
**The rental market, not the silicon, is what makes NVL72 expensive in this
repo.** ⚠️ That $2.31 is a survey figure for hyperscalers who own, not a price
anyone can buy at.

---

## 7. Operational cost, waste, and how to measure it

### 7.1 The lines that are not GPU-hours

| Line | Scale | Notes |
|---|---|---|
| Observability | Prometheus + DCGM-exporter + OpenCost; storage is the cost, not compute | High-cardinality per-request metrics are the usual blow-up: `model × tenant × route × phase` |
| On-call | 1.0 FTE per rack-scale deployment ⚠️ ([§1.4](#14-worked-tco--4-and-16-8b300-nodes-over-3-years)) | The dominant non-hardware line at 4 nodes: **$780k of a $3.94M 3-year TCO = 20 %** |
| Engine upgrades | Recurring; unavoidable | Startup latency varied **more than 4×** across nine vLLM releases ([§2.2](#22-autoscaling-tightness); the "4.6×" printed here before 2026-09-19 was derived from two figure-read endpoints the paper does not state) and [`pairs.json`](../matrix/pairs.json) goes stale on every one |
| Software licences | $0 (open-source vLLM/SGLang) to **$2.50–$4.00/GPU-hr** (NVIDIA AI Enterprise, OCI rate) | [`cloud-pricing.md` §9.1](../cross-cutting/cloud-pricing.md). Can exceed the hardware line |
| Network fabric | **8–15 % of cluster capex** | [`cloud-pricing.md` §9.1](../cross-cutting/cloud-pricing.md) |

### 7.2 A waste taxonomy

| Waste | Mechanism | Rough size |
|---|---|---|
| **Idle GPUs** | Provisioned for peak, peak-to-mean is 2–20× ([arXiv:2606.07362](https://arxiv.org/abs/2606.07362)) | The dominant term. Cast AI's cross-industry measurement: **5 % GPU utilisation** ⚠️ general-K8s, not inference [src](https://cast.ai/press-release/2026-state-of-kubernetes-optimization-report/) |
| **Cold starts** | ~20 s of engine startup for a 3B model, with **> 4× spread across engine versions** ⚠️ ([§2.2](#22-autoscaling-tightness) — the "7.8–36.4 s" endpoints printed here before 2026-09-19 are not in the source); plus image pull and weight load | At 20 s/start and 100 starts/day on one B300 GPU: 0.56 GPU-h/day = 2.3 % of that GPU |
| **Image pulls** | Multi-GB CUDA/engine images re-pulled on every cold node | Mitigate by pre-pull DaemonSet + node-local registry mirror; not a GPU cost but it lengthens every cold start |
| **Failed nodes** | GPU falls out mid-request; whole replica's KV pool is lost | Detect with DCGM health, not liveness probes |
| **Wasted KV pool** | `gpu_memory_utilization` set high, actual concurrency low → HBM reserved and unused | Visible as low `llm_cache_savings_fraction` alongside low concurrency |
| **Rejected speculative tokens** | Verify compute spent on tokens that are discarded | Grows with batch; see [§3.2](#32-combined-effect-and-why-you-cannot-multiply-the-column) |
| **Prefix cache on unstructured traffic** | APC bookkeeping with no hits | **−36.7 % throughput** measured on vLLM v0.6.3/A100 ⚠️ 2024 software [src](https://blog.squeezebits.com/vllm-vs-tensorrtllm-12-automatic-prefix-caching-38189) |

### 7.3 Measuring waste — the actual queries

**GPU-hours idle** (allocation minus goodput), using OpenCost's two bases
([§5.6](#56-token-accounting-and-chargeback-per-tenant)):

```promql
# $/hour attributed to a model that produced no tokens in the window
llm_total_hourly_cost{cost_basis="allocation"}
  unless
llm_total_hourly_cost{cost_basis="usage"} > 0
```

**Goodput-`U` per model** — the quantity [§1.1](#11-the-identity-everything-reduces-to)
divides by. Requires a reference rate from
[`pairs.json`](../matrix/pairs.json) as a recording rule:

```promql
# U = delivered output tok/s  ÷  (reference tok/s/GPU × GPUs allocated)
  rate(vllm:generation_tokens_total[5m])
/ on(model_name) group_left
  (pairs_reference_out_tok_s_per_gpu * pairs_gpus_allocated)
```

**KV utilisation histogram** — the leading indicator for both over- and
under-provisioning. vLLM exposes KV-cache usage; alert on the *distribution*, not the
mean: a p50 of 20 % with a p99 of 98 % is a fleet that is simultaneously wasting HBM
and dropping requests, and the fix (more replicas of a smaller KV pool, or
prefix-aware routing) is different from the fix for a flat 20 %.

**GPU efficiency, honestly** — per
[§1.5](#15-what-u-has-to-mean-to-be-honest), pair the two DCGM profiling metrics and
ignore the utilisation flag:

```promql
DCGM_FI_PROF_SM_ACTIVE      # ratio of cycles an SM has >=1 warp assigned
DCGM_FI_PROF_SM_OCCUPANCY   # ratio of warps resident on an SM
```
[src](https://github.com/NVIDIA/dcgm-exporter/blob/main/etc/dcp-metrics-included.csv).
*"SM occupancy taken together with GPU SM activity can let you know if the GPUs are
being efficiently and fully utilized"*
[src](https://docs.nvidia.com/datacenter/dcgm/latest/learn/modules/profiling.html).

**Cache effectiveness**: `llm_cache_savings_fraction` per model per route
([§5.6](#56-token-accounting-and-chargeback-per-tenant)) — *"A value of `0.9` means
90% of prompt tokens were cache hits and required no prefill computation."* This is
the metric that tells you whether
[§4.1](#41-hit-rate-is-a-property-of-the-workload-not-of-the-cache)'s 0 %–99.7 %
spread applies to *your* traffic.

---

## 8. The FinOps loop

### 8.1 What the FinOps Foundation asks for

The FinOps Foundation's *FinOps for AI* guidance (last updated **2026-02-17**
[src](https://www.finops.org/wg/finops-for-ai-overview/)) defines ten AI KPIs; the
four that matter for an inference fleet are **Cost Per Inference** (*"the cost
incurred for a single inference"*, total inference cost ÷ requests), **Cost Per
Token** (*"Cost Per Token=Total Cost/Number of Tokens Used"*), **Resource
Utilization Efficiency** (*"Actual usage versus provisioned capacity"*) and
**Anomaly Detection Rate**. It structures adoption as **Crawl / Walk / Run**
maturity phases rather than an inform/optimize/operate loop.

⚠️ Secondary reporting attributes FOCUS 1.2 (token lifecycle, ratified 2025-05-29)
and FOCUS 1.3 (split cost allocation for shared resources, ratified 2025-12-04) to
the same body; **neither ratification was verified against a primary FOCUS
specification document for this write-up.**

### 8.2 The dashboard that is worth building

Seven panels, in this order, because that is the order you debug in:

| # | Panel | Metric | Alert on |
|---|---|---|---|
| 1 | **Fleet bill, run-rate** | `sum(llm_total_hourly_cost{cost_basis="allocation"})` × 730.5 | Week-over-week Δ > 15 % |
| 2 | **Goodput-`U` per model** | [§7.3](#73-measuring-waste--the-actual-queries) recording rule | `U` < target for 6 h |
| 3 | **$/1M blended per model**, vs the [`cost-matrix.md`](../matrix/cost-matrix.md) cell | `llm_cost_per_million_tokens{phase=""}` | > 1.5× the cell → an engine or config regression |
| 4 | **$/1M per tenant** | same, grouped by tenant label | tenant unit economics inverted |
| 5 | **Cache hit fraction per route** | `llm_cache_savings_fraction` | drop > 20 pp (routing broke, or traffic changed) |
| 6 | **KV utilisation histogram** | vLLM KV usage, p50/p95/p99 | p99 > 95 % *and* p50 < 30 % |
| 7 | **Queue depth** | `vllm:num_requests_waiting` | sustained > 0 (this, not GPU util, is the scale signal) |

Panel 3 is the one most fleets lack and the one that catches the most money:
**a per-token cost that has drifted away from the modelled cell is a regression, and
it is almost always an engine upgrade, a lost prefix cache, or speculation silently
disabled.** [`cost-matrix.md` §7.3](../matrix/cost-matrix.md#73-speculative-decoding-on--off)
prices that last failure at 3.1–6.7× on DeepSeek.

### 8.3 Monthly review, and unit-economics targets

Six questions, each with the document that answers it:

1. Did goodput-`U` move? If not, nothing else matters
   ([§1.1](#11-the-identity-everything-reduces-to)).
2. Is any model's $/1M more than 1.5× its
   [`cost-matrix.md`](../matrix/cost-matrix.md) cell? Regression hunt.
3. Is any model above its vendor-API break-even
   ([`cost-matrix.md` §6.2](../matrix/cost-matrix.md#62-break-even-utilisation))? If
   so, why are we self-hosting it?
4. What fraction of traffic went to the most expensive model, and could a router
   have moved it ([§5.2](#52-routing-by-difficulty-and-cascades))?
5. What is the trough, in GPU-hours, and what filled it
   ([§2.3](#23-filling-the-troughs-with-batch-work))?
6. Did an engine upgrade land, and were the reference rates re-measured
   ([§2.2](#22-autoscaling-tightness))?

Unit-economics targets, derived from this tree rather than invented:

| Target | Value | Why |
|---|---|---|
| Goodput-`U` | **≥ 0.60**, aim 0.85 | Below 0.60 the owned-vs-rent case
([§1.4](#14-worked-tco--4-and-16-8b300-nodes-over-3-years)) weakens sharply; above 0.85 queueing dominates |
| $/1M blended, per model | within **1.5×** of its [`cost-matrix.md` §4](../matrix/cost-matrix.md) cell at your price tier | The cell is a `U`=1 floor; 1.5× is roughly `U` = 0.67 |
| Prefix-cache hit rate, agentic routes | **≥ 0.90** | Every agentic measurement found is 0.92–0.997 ([§4.1](#41-hit-rate-is-a-property-of-the-workload-not-of-the-cache)) |
| Fraction of traffic on the most expensive model | **≤ 0.30** | [§5.2](#52-routing-by-difficulty-and-cascades)'s cascade arithmetic |
| Idle GPU-hours | **≤ 15 %** of allocated | The complement of the `U` target |

### 8.4 Decision table — own vs rent vs API

Rows are read top to bottom; the first matching row wins.

| If… | …then | Because |
|---|---|---|
| Volume < ~1M tokens/day, or bursty with no floor | **API** | You cannot reach any `U` that beats a vendor's, and you pay zero for idle |
| The model's break-even utilisation is **> 100 %** in [`cost-matrix.md` §6.2](../matrix/cost-matrix.md#62-break-even-utilisation) | **API** | Self-hosting cannot match the API at any `U`. True for DeepSeek-V4.1-Flash on 15 of 16 cells, and Kimi-K3 everywhere but B300 |
| Break-even < 30 % and volume is steady | **Rent committed, or own** | Qwen3.8-27B is 6–28 % on every GPU — self-hosting wins with a large margin of error |
| Break-even 30–100 %, volume steady, `U` ≥ 0.6 demonstrated | **Rent committed** first, own after 12 months of evidence | Kimi-K3/B300 at 48 %/97 % is exactly this case, and its 48 % rests on a **10.1 s batch-wave TTFT** at batch 111 ([`kimik3/b300.md`](../models/kimik3/b300.md)) |
| No vendor API exists | **Own or rent; it is not a contest** | Marlin-2B, blended $0.0092–$0.031 ([`cost-matrix.md` §6.2](../matrix/cost-matrix.md)) |
| ≥ 4 nodes, `U` ≥ 0.6 sustained for ≥ 12 months, and you can hold the capacity | **Own** | [§1.4](#14-worked-tco--4-and-16-8b300-nodes-over-3-years): own ÷ rent-`low` = 0.59–0.63 |
| Data residency, ZDR or air-gap is a requirement | **Own** | Not a cost decision; price it as a constraint |
| You need GB300 and are not a hyperscaler | **Rent, and hold your nose** | One published seller at $18.00, no commitment price ([`cost-matrix.md` §1](../matrix/cost-matrix.md)) |

**The single most common mistake this table is designed to prevent:** comparing a
self-hosted `$/1M` from [`cost-matrix.md`](../matrix/cost-matrix.md) (a `U` = 1
floor) against a vendor's list price (a real, `U`-inclusive price) and concluding
self-hosting wins. Divide by your `U` first. At `U` = 0.30 the DeepSeek-V4.1-Flash
B200 blend of $0.19 becomes **$0.633** — three times DeepSeek's own $0.2074.

---

## 9. Worked example — this repo's five models on 8×B300

### 9.1 Assumptions, all traceable

- **One 8×B300 HGX node.** Operating points and per-GPU rates are
  [`cost-matrix.md` §2](../matrix/cost-matrix.md)'s **S1** (interactive, TPOT ≤ 50 ms)
  B300 column, unchanged.
- **Price tier `low` = $7.40/GPU-hr** (Hyperstack,
  [`cloud-pricing.md` §5.14](../cross-cutting/cloud-pricing.md)) → **$59.20/node-hour
  = $43,245.60/node-month** at 730.5 h/month.
- **Owned comparison** = [§1.3](#13-capex-and-opex-of-an-owned-8b300-node)'s
  $3.3295/GPU-hr plus the +28 % mid-point of §9.1's 20–35 % unmodelled load =
  **$4.2617/GPU-hr → $24,905/node-month**. ⚠️ inherits every estimate in §1.3.
- **Replicas per node** from [`fit-matrix.md` §1](../matrix/fit-matrix.md)'s minimum
  GPU counts, at the S1 operating point's parallelism.
- **`$/1M(U) = $/1M(U=1) ÷ U`**, per [§1.1](#11-the-identity-everything-reduces-to).

### 9.2 Capacity and $/1M output at the `low` tier

| Model | GPUs/replica | Replicas/node | Aggregate out tok/s/node | **$/1M out @ 30 %** | **@ 60 %** | **@ 85 %** | Vendor $/1M out |
|---|---:|---:|---:|---:|---:|---:|---:|
| [DeepSeek-V4.1-Flash](../models/deepseek41f/b300.md) | 4 | 2 | 10,984 | $4.9910 | $2.4955 | **$1.7615** | $0.60 |
| [DeepSeek-V4.1-Flash-NVFP4](../models/deepseek41fnvfp4/b300.md) ⚠️ | 4 | 2 | 3,848 | $14.2467 | $7.1233 | $5.0282 | $0.60 |
| [Qwen3.8-27B](../models/qwen3827b/b300.md) | 1 | 8 | 99,704 | $0.5497 | $0.2748 | **$0.1940** | $3.00 |
| [Kimi-K3](../models/kimik3/b300.md) | 8 | 1 | 2,224 | $24.6380 | $12.3190 | **$8.6958** | $15.00 |
| [Marlin-2B](../models/marlin2b/b300.md) | 1 | 8 | 746,168 | $0.0733 | $0.0367 | **$0.0259** | none |

`est.`, `python3`; the `U`=1 column reproduces
[`cost-matrix.md` §2](../matrix/cost-matrix.md)'s B300 cells exactly
($1.4973 / $4.274 / $0.1649 / $7.3914 / $0.022). ⚠️ The NVFP4 row is built on
[`cost-matrix.md` §9.2](../matrix/cost-matrix.md)'s flagged B300 operating point
(batch 96, TPOT 49.9 ms) and should not be planned on without a measurement.

⚠️ **The Kimi-K3 row's 2,224 tok/s/node is the decode-only basis, and the only
published B300 measurement of this model is on the sustained one.** Wafer
(TP8 + DCP8, SGLang, DSpark, ISL 1024 / OSL 400, peak at c64, 2026-07-31)
measured **1,568 tok/s/node = 196 out tok/s/GPU**, within 1.5 % of
[`kimik3/b300.md`](../models/kimik3/b300.md) §4.2's sustained 198.9 and 2.9× under
the S4 decode-only rate. The reconciliation ([`kimik3/b300.md` §3.8](../models/kimik3/b300.md))
found no error — the two rates are different definitions, not a disagreement — so
**no figure in this table was recut**. A capacity plan that must survive contact
with the published measurement should use the sustained node rate (1,591 at S1),
which raises this row's `$/1M out` by the same 1.40×.

### 9.3 Blended $/1M vs the vendor API, at each utilisation

Blended per [METHODOLOGY §6](../METHODOLOGY.md#6-cost)
(`0.4125 × c_in + 0.25 × c_out`), from
[`cost-matrix.md` §4](../matrix/cost-matrix.md)'s B300 `low` cells; vendor blends
from [`cost-matrix.md` §6.1](../matrix/cost-matrix.md#61-published-vendor-prices)
using each vendor's own cached-input ratio.

| Model | `U`=1 (the published cell) | **@ 30 %** | **@ 60 %** | **@ 85 %** | Vendor blended | `U` needed to match the vendor |
|---|---:|---:|---:|---:|---:|---:|
| DeepSeek-V4.1-Flash | $0.4809 | $1.6030 | $0.8015 | $0.5658 | **$0.2074** | **232 % — impossible** |
| DeepSeek-V4.1-Flash-NVFP4 ⚠️ | $1.16 | $3.8667 | $1.9333 | $1.3647 | **$0.2074** | **559 % — impossible** |
| **Qwen3.8-27B** | $0.0602 | $0.2007 | $0.1003 | $0.0708 | **$0.956** | **6.3 %** ✓ |
| **Kimi-K3** | $2.3811 | $7.9370 | $3.9685 | $2.8013 | **$4.99** | **47.7 %** ✓ |
| Marlin-2B | $0.0092 | $0.0307 | $0.0153 | $0.0108 | none | n/a |

These reproduce [`cost-matrix.md` §6.2](../matrix/cost-matrix.md#62-break-even-utilisation)'s
B300 column (232 % / 559 % / 6 % / 48 %) — as they must, since break-even
utilisation *is* `self-hosted ÷ vendor`, which is the last column here.

### 9.4 Monthly cost of one node, and what it buys

At `U` = 0.60, one 8×B300 node costs **$43,246/month rented** at Hyperstack's rate,
or **$24,905/month owned** (⚠️ §9.1's estimate), and delivers:

| Model | Output tokens/node-month @ 60 % | $/1M out, rented | $/1M out, owned ⚠️ |
|---|---:|---:|---:|
| DeepSeek-V4.1-Flash | 17,331 M | $2.4952 | $1.4370 |
| DeepSeek-V4.1-Flash-NVFP4 ⚠️ | 6,072 M | $7.1225 | $4.1019 |
| Qwen3.8-27B | 157,321 M | $0.2749 | $0.1583 |
| Kimi-K3 | 3,509 M | $12.3235 | $7.0972 |
| Marlin-2B | 1,177,364 M | $0.0367 | $0.0212 |

**Owning cuts the per-token cost by 42 % at any utilisation** — the ratio
$24,905/$43,246 = 0.576 is `U`-independent, because both sides are fixed monthly
costs. That is the whole own-vs-rent argument in one number, and it is why
[§8.4](#84-decision-table--own-vs-rent-vs-api)'s decision table keys on
*commitment horizon*, not on utilisation.

### 9.5 What the five rows say, read together

1. **Qwen3.8-27B is the fleet's profit centre.** It clears its vendor API at 6.3 %
   utilisation and delivers 157 **billion** output tokens per node-month at 60 %.
   Every request that can be served by it instead of a bigger model is worth 8–40×
   ([§5.1](#51-right-sizing-qwen3827b-vs-deepseek-v41-flash-per-task)).
2. **DeepSeek-V4.1-Flash should be bought, not hosted — on B300.** At 232 %
   break-even it cannot match DeepSeek's own $0.2074 blend at any utilisation on
   this node. [`cost-matrix.md` §6.2](../matrix/cost-matrix.md#62-break-even-utilisation)
   shows **B200 at 92 %** is the only cell in sixteen that comes close, and that is
   a node busy 92 % of every hour merely drawing level. **Self-host it for
   residency, latency or control — not for price.**
3. **Kimi-K3 is the one genuine self-hosting win among the big models, and only on
   B300, and only if you can keep one whole node ≥48 % busy on Kimi traffic
   alone.** At $18/GPU-h GB300 it is 112 % — impossible. The 47.7 % also rests on
   an S1 point with a **10.1 s batch-wave TTFT**
   ([`kimik3/b300.md`](../models/kimik3/b300.md)); decode-only cost says nothing
   about that queueing delay, and a customer will notice it.
4. **Marlin-2B is free at any sane volume** — $0.0259/1M at 85 %, no vendor
   alternative, and h ≈ 0 so none of [§4](#4-caching-economics) applies. Its only
   real lever is [§2.5](#25-scale-to-zero-for-small-models).
5. **Utilisation outranks hardware on four of five rows.** Moving
   DeepSeek-V4.1-Flash from 30 % to 85 % `U` is worth **2.83×**; moving it from B300
   to its best GPU (B200, $0.19 blended vs $0.4809) is worth 2.53×. Doing both is
   worth 7.2×, and the free one is the utilisation.

---

## Open questions

Consolidated ⚠️ items from this document. Items already tracked in
[`README.md` §6](../README.md) or
[`cost-matrix.md` §9.3](../matrix/cost-matrix.md#93-open-questions-this-matrix-cannot-close)
are referenced, not duplicated.

1. **The 8×B300 server price, node kW and colo rate are all estimates**
   ($600k ⚠️, 15.5 kW ⚠️, $180/kW-mo from a secondary aggregator), so every absolute
   figure in [§1.3](#13-capex-and-opex-of-an-owned-8b300-node),
   [§1.4](#14-worked-tco--4-and-16-8b300-nodes-over-3-years) and
   [§9.4](#94-monthly-cost-of-one-node-and-what-it-buys) carries ±25 %. The *ratios*
   are robust. Inherited from
   [`cloud-pricing.md` §7.2/§8.2/§8.3](../cross-cutting/cloud-pricing.md).
2. **No staffing source exists.** The 1.0 / 2.5 FTE figures in
   [§1.4](#14-worked-tco--4-and-16-8b300-nodes-over-3-years) are assumptions, and
   staff is 20 % of the 4-node TCO. A published GPU-platform staffing ratio would
   move the own-vs-rent conclusion at small scale more than any hardware input.
3. **No measured goodput-`U` for a dedicated inference fleet was found anywhere.**
   The only large-sample number is Cast AI's 5 % across general Kubernetes clusters,
   and the only credible inference figure is DeepSeek's 81.6 % *of its own peak*
   (a different denominator). The 30/60/85 % scenarios in
   [§9](#9-worked-example--this-repos-five-models-on-8b300) are brackets, not
   forecasts.
4. **CPU DRAM and NVMe $/GB-hour on a rented node have no primary source**
   ([§4.4](#44-cache-tiers-what-each-byte-costs-and-when-to-recompute-instead)). The
   planning stance — zero at the margin up to installed capacity — is a reasoning
   step, not a measurement.
5. **The power-capping / SM-clock result is measured on H200 with ~4B models**
   ([§6.3](#63-power-capping-and-what-it-actually-buys)). Whether the 32 %-energy /
   <1 %-throughput knee survives on B300 with a 552B-backbone MoE is unverified, and
   the prior is that it is weaker.
6. **Structured outputs' cost effect is unmeasured**
   ([§5.4](#54-output-length-control)). Both the token saving and the
   grammar-compilation / batching penalty need a measurement on this repo's models
   before either is claimed.
7. **Distillation's 5–30× figures are all vendor or practitioner blogs**
   ([§5.3](#53-distillation)); none states a reproducible methodology. The bound
   from [§5.1](#51-right-sizing-qwen3827b-vs-deepseek-v41-flash-per-task) is the
   defensible statement.
8. **RouteLLM's numbers are from 2024 and a far-apart model pair.** Whether a
   Qwen3.8-27B ↔ DeepSeek-V4.1-Flash router reaches the 14 %-strong-model operating
   point on this repo's traffic is unknown; the MMLU result (54 %) is the pessimistic
   anchor ([§5.2](#52-routing-by-difficulty-and-cascades)).
9. **Prompt compression's 20× has no 2026 independent replication**, and its
   interaction with prefix caching is argued from first principles here rather than
   measured ([§5.5](#55-prompt-compression)).
10. **The vLLM APC overhead on unstructured traffic (−36.7 %) is 2024-era software**
    — flagged as needing re-measurement in
    [`serving-optimizations.md` §1.2](../cross-cutting/serving-optimizations.md) and
    inherited here in [§3.2](#32-combined-effect-and-why-you-cannot-multiply-the-column)
    and [§7.2](#72-a-waste-taxonomy).
11. **SemiAnalysis's $2.26/$2.31 per-chip-hour owning cost**
    ([§6.6](#66-nvl72-vs-hgx)) would invert every GB300 cell in
    [`cost-matrix.md`](../matrix/cost-matrix.md) if it were purchasable. It is a
    hyperscaler-owning survey figure, and the InferenceX methodology's own TCO
    inputs are not disclosed.
12. **FOCUS 1.2 / 1.3 ratification dates are secondary-sourced**
    ([§8.1](#81-what-the-finops-foundation-asks-for)); only the *FinOps for AI*
    guidance page (2026-02-17) was verified directly.
13. **llm-d's 88 %-faster-TTFT figure has no stated model or GPU count**
    ([§4.2](#42-cross-replica-kv-sharing)); it is directional only.
14. **Every per-token lever in [§3](#3-per-token-levers-already-quantified-elsewhere)
    inherits its source document's open questions** — in particular the ±20 % MBU
    band on every non-`measured` cell
    ([`README.md` §6 item 4](../README.md)), the DSpark acceptance-rate constants
    ([item 6](../README.md)), and the order-of-magnitude uncertainty on un-measured
    prefill rates for DSA/CSA-family models ([item 5](../README.md)). **None of the
    utilisation-side arithmetic in this document reduces those.**

---

## Sources

Primary sources fetched for this document, 2026-09-19. Sources reached via a sibling
research document are cited inline at their point of use and are not repeated here.

**Vendor pricing and tiering**
- [Anthropic — Batch processing](https://platform.claude.com/docs/en/build-with-claude/batch-processing) — 50 % discount, 100,000 requests / 256 MB, 24 h expiry, 29-day retention, 30–98 % in-batch cache hit rates
- [OpenAI — Batch API](https://developers.openai.com/api/docs/guides/batch) — 50 % discount, 24 h window, 50,000 requests / 200 MB
- [OpenAI — Flex processing](https://developers.openai.com/api/docs/guides/flex-processing) — `service_tier: "flex"`, Batch rates synchronously
- [DeepSeek — API pricing](https://api-docs.deepseek.com/quick_start/pricing) — peak/off-peak windows, cache-hit vs miss

**Fleet economics and utilisation**
- [DeepSeek — V3/R1 Inference System Overview](https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md) (2025-03-01) — 278 peak / 226.75 average nodes, $87,072/day, 56.3 % on-disk KV hit rate, day/night reallocation
- [Cast AI — 2026 State of Kubernetes Optimization Report](https://cast.ai/press-release/2026-state-of-kubernetes-optimization-report/) (2026-04-21) — GPU 5 %, CPU 8 %, memory 20 %
- [NVIDIA Perspectives — utilization rates and inference cluster economics](https://perspectives.nvidia.com/utilization-rates-inference-cluster-economics-hardware/) (2026-04-13) — the 40 %→80 % doubling statement
- [NVIDIA Perspectives — TCO](https://perspectives.nvidia.com/ai-infrastructure/total-cost-of-ownership/) (2026-09-17) — token-centric TCO framing; no formula disclosed
- [SemiAnalysis InferenceX — DeepSeek R1, B300 vs GB300 NVL72](https://inferencex.semianalysis.com/compare-per-dollar/deepseek-r1-b300-vs-gb300) — $/M by interactivity, $2.26/$2.31 per chip-hour
- [SemiAnalysis InferenceX — MiniMax M2.5/M2.7, B200 vs H100](https://inferencex.semianalysis.com/compare-per-dollar/minimax-m27-b200-vs-h100)
- [SemiAnalysis — InferenceX v2](https://newsletter.semianalysis.com/p/inferencex-v2-nvidia-blackwell-vs) — methodology caveat (random data, prefix caching disabled); *"close to 1000 frontier GPUs for a full benchmark run across all SKUs"*

**Cold start, autoscaling, GPU sharing**
- [Kabakibo, Trivedi, Wang — *Breaking the Ice: Analyzing Cold Start Latency in vLLM*, MLSys 2026](https://arxiv.org/abs/2606.07362) (v3, 2026-06-29) — six-step decomposition, **> 4× startup spread across nine vLLM releases** (the paper states the ratio, not endpoints ⚠️), CUDA-graph scaling, 2–20× peak-to-mean production traces
- [vLLM — Zero-Reload Model Switching with Sleep Mode](https://vllm.ai/blog/2025-10-26-sleep-mode) (2025-10-26) — 0.26–2.58 s wake, 18–200× vs cold start
- [KEDA — ScaledObject specification](https://keda.sh/docs/2.17/reference/scaledobject-spec/) — defaults
- [KEDA — Scaling Deployments](https://keda.sh/docs/2.17/concepts/scaling-deployments/) — activation vs scaling phases
- [NVIDIA GPU Operator — GPU sharing](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-sharing.html) — time-slicing vs MIG, isolation trade-offs

**Caching**
- [vLLM — Serving Agentic Workloads at Scale with vLLM × Mooncake](https://vllm.ai/blog/2026-05-06-mooncake-store) (2026-05-06) — 1.7 %→92.2 % hit rate, 3.8× throughput, 46× TTFT, 12→60 GB200 scaling
- [LMCache — Benchmarking for multi-turn agentic workloads on AMD MI300X](https://blog.lmcache.ai/en/2026/05/12/benchmarking-lmcache-for-multi-turn-agentic-workloads-on-amd-mi300x/) (2026-05) — 739 Claude Code traces, 93–97 % prefix reuse, 72.4 % realised hit rate, 3.0× TTFT
- [LMCache — README](https://github.com/LMCache/LMCache) — tiering, storage backends, P2P CPU sharing (production 2026-01)
- [Red Hat Developer — KV cache aware routing with llm-d](https://developers.redhat.com/articles/2025/10/07/master-kv-cache-aware-routing-llm-d-efficient-ai-inference) (2025-10-07) — 87.4 % hit rate, 2,850→340 ms TTFT
- [vLLM — Automatic prefix caching design](https://docs.vllm.ai/en/latest/design/prefix_caching.html) — hashing, `--prefix-caching-hash-algo`

**Model-level levers**
- [LMSYS — RouteLLM](https://www.lmsys.org/blog/2024-07-01-routellm/) and [arXiv:2406.18665](https://arxiv.org/abs/2406.18665) — 14 % / 54 % strong-model calls at 95 % quality
- [Microsoft — LLMLingua](https://github.com/microsoft/LLMLingua), [arXiv:2310.05736](https://arxiv.org/abs/2310.05736) — up to 20× compression
- [TensorZero — Distillation with programmatic data curation](https://www.tensorzero.com/blog/distillation-programmatic-data-curation-smarter-llms-5-30x-cheaper-inference/) ⚠️ vendor blog
- [arXiv:2606.03965 — Agentic Chain-of-Thought Steering](https://arxiv.org/pdf/2606.03965) ⚠️ preprint, 27–51 % CoT reduction
- [Character.AI — Optimizing AI Inference](https://blog.character.ai/optimizing-ai-inference-at-character-ai/) (2024-06-20) — 33× cost reduction since 2022, ~20,000 QPS, <1¢/hour of conversation (technique-level numbers not disclosed on this page)

**Power and hardware**
- [*The Illusion of Power Capping in LLM Decode*, arXiv:2605.11999v1](https://arxiv.org/html/2605.11999) (2026-05-12) — 137–300 W decode on a 700 W H200, SM clock locking 32 % energy at <1 % throughput
- [*Characterizing LLM Inference Energy-Performance Tradeoffs across Workloads and GPU Scaling*, arXiv:2501.08219](https://arxiv.org/abs/2501.08219) — decode is 77–91 % of inference time and frequency-insensitive; **42 % average energy saving for 1–6 % latency** from 2842 → 180 MHz (corrected 2026-09-19 from "up to 30 %")
- [*Characterizing Performance-Energy Trade-offs in Multi-Request Workflows*, arXiv:2604.09611](https://arxiv.org/pdf/2604.09611) ⚠️ different hardware
- [Lenovo — GB300 NVL72](https://lenovopress.lenovo.com/lp2357-lenovo-nvidia-gb300-nvl72-rack-scale-ai) — 135 kW TDP / 155 kW peak, 1,100 W per GPU
- [Hashrate Index — Used GPU market pricing and depreciation](https://hashrateindex.com/blog/used-gpu-market-pricing-deprecation-secondary-ai/) ⚠️ and [Mercatus — H100 depreciation](https://www.mercatus-ai.com/blog/h100-depreciation) ⚠️

**Measurement, accounting, FinOps**
- [OpenCost — AI Inference Cost Tracking](https://github.com/opencost/opencost/blob/develop/docs/inference-cost-tracking.md) — `llm_cost_per_million_tokens`, `llm_cache_savings_fraction`, allocation vs usage cost bases
- [OpenCost — README](https://github.com/opencost/opencost) — feature list including vLLM/llm-d inference cost tracking
- [NVIDIA DCGM — Profiling](https://docs.nvidia.com/datacenter/dcgm/latest/learn/modules/profiling.html) and [dcgm-exporter metrics CSV](https://github.com/NVIDIA/dcgm-exporter/blob/main/etc/dcp-metrics-included.csv) — `SM_ACTIVE`, `SM_OCCUPANCY`
- [FinOps Foundation — FinOps for AI Overview](https://www.finops.org/wg/finops-for-ai-overview/) (updated 2026-02-17) — the ten AI KPIs, Crawl/Walk/Run

**In-tree documents this builds on (not re-derived)**
- [`METHODOLOGY.md` §6](../METHODOLOGY.md#6-cost) · [`matrix/cost-matrix.md`](../matrix/cost-matrix.md) · [`matrix/fit-matrix.md`](../matrix/fit-matrix.md) · [`matrix/recommendations.md`](../matrix/recommendations.md) · [`matrix/pairs.json`](../matrix/pairs.json) · [`cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) · [`cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md) · [`cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md) · [`cross-cutting/inferencex-api.md`](../cross-cutting/inferencex-api.md) · the five `models/<exp>/architecture.md` §11 vendor-pricing sections

---

## Verification log (2026-09-19)

Adversarial fact-check. Every primary source below was **opened**, not taken from
this document's own citation; every derivation was re-run in `python3`; every
`research/` cross-reference was opened and the cited number located in the target
file. 56 claims checked: **41 CONFIRMED · 13 CORRECTED · 2 UNVERIFIABLE.**

### CORRECTED

| # | §  | Claim as printed | Verdict / what the source says | Source opened |
|---|---|---|---|---|
| 1 | 1.1 | NVIDIA quote ended *"…at 80% utilization, **regardless of the hardware platform**"* | **CORRECTED** — the page's sentence ends at *"80% utilization."*; the trailing clause is not on it. Date 2026-04-13 and the AI-generated-content disclaimer both confirmed | https://perspectives.nvidia.com/utilization-rates-inference-cluster-economics-hardware/ |
| 2 | 1.3 | $600,000 is the *"midpoint"* of cloud-pricing's $560k–$660k band | **CORRECTED** — the midpoint is $610k. $600k is §9.2's pinned value, kept for consistency, and now labelled as such. DGX B200 $515k list confirmed on the cited page | https://www.gpu.fm/blog/nvidia-b200-complete-buyers-guide-2026 |
| 3 | 2.2 | Six startup steps listed as tokenizer / model init / weight load / torch.compile / KV profiling / CUDA graph | **CORRECTED** — the paper's six are **framework bootstrapping**, tokenizer init, model loading, torch compilation, KV-cache profiling, CUDA-graph capture. *"Predominantly CPU-bound"* and 20.32 s for Llama3.2-3B confirmed | https://arxiv.org/html/2606.07362v3 |
| 4 | 2.2, 7.1, 7.2 | *"7.8 s (v0.4.0) to 36.41 s (v0.9.0)"*; *"varied 4.6×"*; *"7.8–36.4 s"* | **CORRECTED** — those endpoints are in no sentence of the paper. Its text supports only *"more than 4× variance … and a 2× latencies reduction observed between v0.9 and v0.10"*; Figure 1 reads ≈3 s to >12 s. All three sites recut to the ratio and marked ⚠️ | https://arxiv.org/html/2606.07362v3 |
| 5 | 2.2 | Sleep Mode first inference *"87–88 % faster"* | **CORRECTED** — the blog states **61–88 %** (86 % Qwen3-0.6B, 88 % Phi-3-vision on A100). Wake times 0.26/0.85 s and 0.82/2.58 s, cold starts 37.6 s / 58.1 s, *"18-200x"*, and the L1 (10–100 GB+ CPU RAM) / L2 (~MB) split all confirmed | https://vllm.ai/blog/2025-10-26-sleep-mode |
| 6 | 2.5 | time-slicing ConfigMap YAML | **CORRECTED** — two required-by-example fields were missing: `renameByDefault: false`, `failRequestsGreaterThanOne: false`, and `resources` is indented one level deeper. Both the time-slicing and MIG definitions, and the no-memory/fault-isolation sentence, are verbatim-correct | https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/gpu-sharing.html |
| 7 | 3.2 | vLLM DSpark sentence given inside quote marks | **CORRECTED** — it was a paraphrase. Actual: *"At batch size 1 that is a good trade: the GPU is memory-bound with spare compute, so the extra work (draft tokens) is close to free"* / *"Draft tokens now compete with real tokens for the same compute, and every rejected token wastes useful compute…"* | https://vllm.ai/blog/2026-08-14-dspark-adaptive-verification |
| 8 | 4.3 | Table says DeepSeek **46.7×**, body says *"$0.003 vs $0.15 … a 50× discount"* | **CORRECTED — internal contradiction.** Both are real but come from different DeepSeek pages: the API doc prices `deepseek-flash` off-peak input-miss at **$0.15** (→ 50×), the marketing page at **$0.14** (→ 46.7×). Resolved in favour of 46.7 × / $0.14 (the value `serving-optimizations.md` §1.5 pinned), with the conflict named. ⚠️ `cost-matrix.md` §6.1 still blends off $0.15 | https://api-docs.deepseek.com/quick_start/pricing · https://deepseek.ai/pricing |
| 9 | 6.1 | InferenceX *"nightly, ~200 chips"* | **CORRECTED** — *"InferenceX utilizes close to 1000 frontier GPUs for a full benchmark run across all SKUs."* The *"benchmarks on random data and disables prefix caching"* caveat is verbatim-correct | https://newsletter.semianalysis.com/p/inferencex-v2-nvidia-blackwell-vs |
| 10 | 6.3 | *"four ~4B models"* | **CORRECTED** — **five** architectures: GQA, GQA-ctrl (Minitron-4B), MLA (TransMLA variant), Gated DeltaNet, Mamba2 | https://arxiv.org/html/2605.11999 |
| 11 | 6.3 | wasted band *"1590–1980 MHz"* | **CORRECTED** — the band is **1590–1830 MHz**: *"Requesting 1980 MHz yields only ≈1830 MHz sustained."* The `<<0.1 %` throughput and `+7–13 %` power figures are verbatim-correct | https://arxiv.org/html/2605.11999 |
| 12 | 6.3 | arXiv:2501.08219 *"by up to 30% without requiring any modifications to the model"* | **CORRECTED — quote not in the paper.** It reports *"an average of 42% energy savings with only a 1-6% latency increase"* going 2842 → 180 MHz, on five 1B–32B decoder-only LLMs across four NLP benchmarks, with decode *"77-91%"* of inference time | https://arxiv.org/abs/2501.08219 |
| 13 | 6.5 | secondhand H100 *"~1.5× cheaper"* than on-prem-new | **CORRECTED — unit mismatch.** $1.265 is at `U` = 1, $1.856 at `U` = 0.90. At equal `U` = 0.90 the secondhand node is $1.405 → **1.32×**, not 1.5×. (The 2.5× vs Hyperstack's $3.20 rent is fine — rent has no `U` term) | `python3`; `cross-cutting/cloud-pricing.md` §9.2 (opened, $1.856 / 1.275 kW located) |

### UNVERIFIABLE

| # | § | Claim | Why |
|---|---|---|---|
| 14 | 1.3 | Colo $180/kW-month, *"band $150–$250"* | The Encor Advisors page carries *"250-500 kW deployments were approaching approximately $196 per kW per month in H2 2025"* (CBRE, second-hand), a $120–$180 retail band and an $80–$130 wholesale band. The **$150–$250 GPU-density liquid-cooled band was not on the page** when re-fetched; it exists only in `cloud-pricing.md` §8.3, itself ⚠️-marked. Row now marked ⚠️ **TO BE VERIFIED** inline |
| 15 | 5.4 | arXiv:2606.03965 *"27–51 % reduction in chain-of-thought length"* | Title confirmed (*Agentic Chain-of-Thought Steering for Efficient and Controllable LLM Reasoning*), but the PDF's text layer did not yield the percentages on two attempts. The document's existing ⚠️ *"not independently replicated"* stands and is sufficient |

### CONFIRMED

Version numbers, feature-support and configuration:

| § | Claim | Source opened |
|---|---|---|
| 2.5 | KEDA `ScaledObject` defaults — `pollingInterval: 30`, `cooldownPeriod: 300`, `initialCooldownPeriod: 0`, `idleReplicaCount` *"ignored, must be less than minReplicaCount"*, `minReplicaCount` default **0**, `maxReplicaCount` **100** — every comment verbatim | https://keda.sh/docs/2.17/reference/scaledobject-spec/ |
| 2.5 | KEDA activation-vs-scaling quotes, and *"If the minimum replicas is >= 1, the scaler is always active and the activation value will be ignored"* | https://keda.sh/docs/2.17/concepts/scaling-deployments/ |
| 5.6 | OpenCost `INFERENCE_COST_ENABLED` / `INFERENCE_MODEL_LABEL: llm-d.ai/model`; `llm_total_hourly_cost`, `llm_cost_per_million_tokens`, `llm_cache_savings_fraction`; `cost_basis=allocation` *"Reconciles to the infrastructure bill"* vs `usage` *"Does not reconcile to the bill"*; **2.5× fallback output/input ratio** | https://github.com/opencost/opencost/blob/develop/docs/inference-cost-tracking.md |
| 1.5, 7.3 | `DCGM_FI_PROF_SM_ACTIVE` = *"The ratio of cycles an SM has at least 1 warp assigned"*; `DCGM_FI_PROF_SM_OCCUPANCY` = *"The ratio of number of warps resident on an SM"* (both shipped commented-out in the exporter's default CSV) | https://github.com/NVIDIA/dcgm-exporter/blob/main/etc/dcp-metrics-included.csv |
| 1.5 | `DCGM_FI_DEV_GPU_UTIL` = *"the percent of time over the past sample period during which one or more kernels was executing"*; the 1-of-132-SM reads-100 % illustration, 131 idle | https://devopsbeast.com/blog/dcgm-prometheus-gpu-observability |

Vendor pricing and tiering (§2.4 table, all four rows):

| § | Claim | Source opened |
|---|---|---|
| 2.4 | Anthropic: **50 % of standard prices on all usage**, stacks with prompt caching; *"most batches finishing in less than 1 hour"*; results at completion or 24 h; **batches expire at 24 h and expired requests are not billed**; **100,000 requests or 256 MB**; **29-day** result retention; *"cache hit rates ranging from 30% to 98%"* | https://platform.claude.com/docs/en/build-with-claude/batch-processing |
| 2.4 | OpenAI Batch: *"50% cost discount"*, *"the completion window can only be set to `24h`"*, *"up to 50,000 requests, and a batch input file can be up to 200 MB"* | https://developers.openai.com/api/docs/guides/batch |
| 2.4 | OpenAI Flex: *"Tokens are priced at Batch API rates, with additional discounts from prompt caching"*; *"slower response times and occasional resource unavailability"*; *"request timeouts are more likely"*; *"The default timeout is 10 minutes"*; beta, limited model availability | https://developers.openai.com/api/docs/guides/flex-processing |
| 2.4 | DeepSeek: peak = **01:00–04:00 and 06:00–10:00 UTC, Mon–Fri**, *"All other hours are off-peak, including weekends and Chinese public holidays in full"*; off-peak is half of peak; `deepseek-flash` $0.15 / $0.003 / $0.60 off-peak | https://api-docs.deepseek.com/quick_start/pricing |

Measured numbers from papers and blogs:

| § | Claim | Source opened |
|---|---|---|
| 1.1 | Cast AI: GPU **5 %**, CPU **8 %**, memory **20 %**, published 2026-04-21, *"tens of thousands of clusters"* (clouds not named — softened inline) | https://cast.ai/press-release/2026-state-of-kubernetes-optimization-report/ |
| 2.2 | CUDA-graph capture PCC **0.99**, Qwen-0.5B 0.91 s → Qwen-MoE-14.3B 1.51 s; batch-size sweep (Llama2-7B) 0.33 s → 1.8 s, PCC 1.0; peak-to-mean **2–20×** across Azure, Shanghai AI Lab, Mooncake and Alibaba traces | https://arxiv.org/html/2606.07362v3 |
| 2.3 | DeepSeek V3/R1: peak **278** / average **226.75** nodes over 2025-02-27 12:00 → 02-28 12:00 UTC+8; *"$2 per hour … total daily cost amounts to $87,072"*; *"$562,027"*; *"cost profit margin of 545%"*; 56.3 % on-disk KV hit; the day/night reallocation quote verbatim. **226.75 / 278 = 81.6 %** re-derived | https://github.com/deepseek-ai/open-infra-index/blob/main/202502OpenSourceWeek/day_6_one_more_thing_deepseekV3R1_inference_system_overview.md |
| 3.1, 3.2 | Wide-EP: GB300 **226.2** vs GB200 **147.9** TPS/GPU (**1.53×**); decode batch **576 vs 320** (1.8×); MTP **147.9 → 169.1 = +14.3 % on GB200** and **226.2 → 224.2 = −0.9 % on GB300** — the document's "+14 % / −0.9 %" is exact | https://www.lmsys.org/blog/2026-02-19-gb300-longctx/ |
| 3.2, 4.1, 7.2 | APC on unstructured traffic: *"a throughput reduction of ~36.7%"*, vLLM **v0.6.3**, 1× A100-SXM-80G, article dated **2024-12-23** — the ⚠️ "2024-era software" caveat is correct | https://blog.squeezebits.com/vllm-vs-tensorrtllm-12-automatic-prefix-caching-38189 |
| 4.1, 4.2 | LMCache MI300X: **739** Claude Code traces, *"~93-97%"* prefix reuse, **72.4 %** realised hit rate under stress, **250–300k**-token crossover, *"understate LMCache's value by ~10-17%"*, **64 GB** CPU DRAM L2 (`LMCACHE_MAX_LOCAL_CPU_SIZE=64`), 3.0× TTFT | https://blog.lmcache.ai/en/2026/05/12/benchmarking-lmcache-for-multi-turn-agentic-workloads-on-amd-mi300x/ |
| 4.1, 4.2 | Mooncake: **1.7 % → 92.2 %**, **3.8×** throughput, **46×** p50 TTFT, **8.6×** e2e; Kimi-2.5 NVFP4 on 12× GB200, 1P1D, TP4 prefill / DP8+EP decode; 12 → 60 GB200 *"scales nearly linearly"* with *">95% cache hit rate at all scales"* | https://vllm.ai/blog/2026-05-06-mooncake-store |
| 4.2 | llm-d: **4,776** queries, **4,176** hits = **87.4 %**; TTFT **2,850 → 340 ms (88 %)**; and the document's ⚠️ is right — the article names **neither the model nor the GPU count** | https://developers.redhat.com/articles/2025/10/07/master-kv-cache-aware-routing-llm-d-efficient-ai-inference |
| 5.2 | RouteLLM: MT Bench **14 %** GPT-4 calls (matrix factorization + LLM-judge augmentation), *"75% cheaper than the random baseline"*, headline *"over 85%"*; MMLU **54 %** (causal-LLM + golden-label), *"14% cheaper than the random baseline"*, headline 45 %; GSM8K 35 % headline, call fraction not broken out. Both of the document's ⚠️ caveats hold | https://www.lmsys.org/blog/2024-07-01-routellm/ |
| 5.5 | LLMLingua: *"up to 20x compression with minimal performance loss"*; GPT2-small / LLaMA-7B compressors | https://github.com/microsoft/LLMLingua |
| 6.1 | InferenceX MiniMax M2.5/M2.7 230B: 66 tok/s/user **B200 $0.10 vs H100 $0.26**; 89 tok/s/user **$0.15 vs $0.40** | https://inferencex.semianalysis.com/compare-per-dollar/minimax-m27-b200-vs-h100 |
| 6.3 | Power capping: decode **137–300 W** on a 700 W H200; *"no cap ever triggers"*; caps **280–700 W** structurally ineffective; SM-clock locking **up to 32 %** energy (GDN 30 % @ BS=1, 32 % @ BS=32) at **< 1 %** throughput; *"SM clock locking Pareto-dominates power capping universally"* | https://arxiv.org/html/2605.11999 |
| 6.3, 6.6 | GB300 NVL72: *"135 kW TDP; up to 155 kW peak"*, *"about 10% to air and 90% to liquid"*, TGP **1100 W**/GPU | https://lenovopress.lenovo.com/lp2357-lenovo-nvidia-gb300-nvl72-rack-scale-ai |
| 6.6 | InferenceX DeepSeek R1: **B300 $2.26 / GB300 $2.31 per chip-hr** (*"SemiAnalysis Market July 2026 Pricing Surveys & AI Cloud TCO Model"*); 88 tok/s/user $0.13 vs $0.06 (*"102% more total tokens per dollar"*); 162 → $1.16 vs $0.33 (*"255% more cost-efficient"*); 235 → $2.68 vs $2.76 (*"3%"*) | https://inferencex.semianalysis.com/compare-per-dollar/deepseek-r1-b300-vs-gb300 |
| 8.1 | FinOps for AI: **ten** AI KPIs, last updated **2026-02-17**, Crawl/Walk/Run maturity; Cost Per Inference, Cost Per Token, Resource Utilization Efficiency (*"Actual Resource Utilization/Provisioned Capacity"*) and Anomaly Detection Rate all defined as quoted | https://www.finops.org/wg/finops-for-ai-overview/ |

Arithmetic re-derived in `python3` (all reproduce):

| § | Recomputed | Result |
|---|---|---|
| 1.3 | `600,000/36 = 16,666.67`; `15.504 × 180 = 2,790.72`; subtotal `19,457.39`; `/(8 × 730.5) = 3.3295` | exact |
| 1.4 | every cell: capex 2.4 M / 9.6 M, fabric 288 k / 1.152 M, spares 72 k / 288 k, colo 401,864 / 1,607,455, staff 780 k / 1.95 M, totals **3,941,864 / 14,597,455**; $/GPU-hr **4.687 / 4.340** (3 yr = 26,280 h); at `U` 0.85 / 0.60; rent 6,223,104 / 6,677,222 / 12,614,400 (×4 at 16 nodes); ratios **0.63 / 0.59** and **0.31 / 0.29**; *"16 nodes only 7 % cheaper"* (4.340/4.687 = 0.926); `0.24 × 6.22 M = 1.49 M` | exact |
| 2.5 | `5 min × $7.40 = $0.62` per scale-down event per GPU | exact |
| 4.4 | DeepSeek `131,072 × 890 B = 0.1167 GB → 11.7 ms @ 10 GB/s`; Kimi `131,072 × 13,824 B = 1.812 GB → 181 ms` | exact |
| 5.2 | cascade `f × 0.4809 + (1−f) × 0.0602` → **0.4809 / 0.2706 / 0.1191 / 0.0602**; break-even `1 − 0.0602/0.4809 = 87.5 %` | exact |
| 6.4 | `200,000/36 = 5,555.56` + `10.2 × 180 = 1,836` = `7,391.56 → $1.265/GPU-hr`; `3.20/1.265 = 2.53×` | exact |
| 6.6 | `18.00 / 2.31 = 7.79×` | exact |
| 7.1 | staff `780 k / 3,941,864 = 19.8 %` ≈ 20 % | exact |
| 7.2 | `100 × 20 s = 0.556 GPU-h/day = 2.3 %` | exact |
| 9.1–9.4 | node-hour `$59.20`, node-month `$43,245.60`; aggregate tok/s = per-GPU rate × 8 for all five models (**10,984 / 3,848 / 99,704 / 2,224 / 746,168**); every `$/1M out` at `U` = 1 / 0.30 / 0.60 / 0.85; owned `3.3295 × 1.28 = 4.2618 → $24,906/node-month`; ratio `24,905/43,246 = 0.576` and its `U`-independence; tokens/node-month at 60 %; break-evens **232 % / 559 % / 6.3 % / 47.7 %**; §9.5's **2.83× / 2.53× / 7.2×** | exact |

`research/` cross-references (each file opened, each number located):

| § | Cross-reference | Verdict |
|---|---|---|
| 2.1, 9.2 | `cost-matrix.md` §2/§3 B300 cells $1.4973 / $0.774, $4.274 / $0.216, $0.1649 / $0.1527, $7.3914 / $3.61, $0.022 / $0.022 — and the S1 operating points (4×128·1,373·23.31 ms etc.) that generate §9.2's aggregate rates | **CONFIRMED**; ratios 1.93 / 19.8 / 1.08 / 2.05 / 1.00 re-derived |
| 5.1, 6.1, 9.3 | `cost-matrix.md` §4 blended B300/low and §8 cheapest-blended winners | **CONFIRMED** except Kimi-K3 — see structural note 1 below |
| 3.1 | `cost-matrix.md` §7.1 (×0.833 / ×1.25), §7.2 (1.000 / 0.550 / 0.190 and the 61/95/68/78/60 % output shares), §7.3 (3.1× / 6.7× / 3.6× / 1.64× / 4.3×), §7.4 (B300 res1y $7.94 vs low $7.40) | **CONFIRMED**, all located verbatim |
| 3.1, 4.4 | `serving-optimizations.md` §3.5 (2–3×, +45 %/+75 %, MORI-IO 2.5×, −20–30 %) and §1.5 (0.117 GB, 1.4 ms @ 83.4 GB/s, 2.097 PFLOP) | **CONFIRMED** |
| 1.3, 4.4, 6.5, 9.1 | `cloud-pricing.md` §5.14 ($7.40 / $7.94 / $15.00 B300, $3.20 H100), §8.1 (1.275 kW), §8.2 (15–16 kW HGX B300), §8.3 (provisioned-kW billing), §9.1 ($2.50–$4.00 NVIDIA AI Enterprise, 8–15 % fabric, "add 20–35 %"), §9.2 ($3.702 B300, $1.856 H100, 1.938 kW/GPU), §10.1 ($0.0276 / $0.0560 per GB-hr) | **CONFIRMED** |
| 6.6 | GB300 rack purchase *"~$3.3–3.8M"* | ⚠️ `cloud-pricing.md` §9.2 carries a single **$3.5 M est. ⚠️**, not a $3.3–3.8 M band. Left as printed (it brackets the pinned value) but it is a widening of the source |
| 8.4, 9.5 | `cost-matrix.md` §6.2 — DeepSeek base clears 100 % in **1 of 16** cells (B200 low, 92 %), so *"15 of 16"* is right; Kimi-K3 clears only on B300 (48 %/97 %); Qwen 6–28 %; the 10.1 s batch-wave TTFT at batch 111 | **CONFIRMED** (`kimik3/b300.md` §4.2 opened) |

### Structural notes (not fixed here — they belong to other files)

1. **Kimi-K3 B300 blended: $2.3811 vs $2.3808.** This document uses **$2.3811** in §5.1, §6.1, §6.2 and §9.3. `models/kimik3/b300.md` (the pair document, the authority) prints **$2.3811**; `matrix/cost-matrix.md` §4's grid prints **$2.3808**. This document matches the pair document, so nothing was changed here — but `cost-matrix.md` §4's cell is 0.01 % stale.
2. **`cost-matrix.md` §7.3 contradicts its own §2–§4 header for Kimi-K3.** §7.3 says *"Every DeepSeek and Kimi cost in §2–§4 is **with** speculation on"*, yet its own Kimi row lists the **without**-DSpark figures ($7.3926 output / $2.3811 blended) as exactly the values §2/§4 publish, and the with-DSpark figures ($4.508 / $1.660) as the alternative. One of the two is wrong, and every Kimi-K3 price quoted in this document inherits whichever it is.
3. **DeepSeek input price is forked across the tree** — $0.14 (`serving-optimizations.md` §1.5, from deepseek.ai/pricing) vs $0.15 (`cost-matrix.md` §6.1, from api-docs.deepseek.com). Both pages were opened today and both are live. This forks the vendor blend ($0.2074) that §8.4, §9.3 and §9.5 all key on, and it should be pinned in `METHODOLOGY.md` §8.
4. **§4.4's CPU DRAM bandwidth (~40–63 GB/s) undercuts the tree's own measurement.** `serving-optimizations.md` §1.5 carries **83.4 GB/s** GPU↔CPU DMA, measured on H100 + Sapphire Rapids with 2 MB blocks, and §4.3 of this document uses that 83.4 GB/s figure four paragraphs later. The §4.4 table's "reported range" is a secondary aggregator's and should be replaced by the in-tree measurement.
5. **Missing versus the brief.** ~~This document has no section on **spot / preemptible capacity** and **checkpoint-resume economics**~~ … ~~It also never prices **egress / storage** for weight distribution~~. **Closed 2026-09-19 — see note 8 and [§6.4](#64-spot-and-preemptible-capacity).** The original finding stood: spot is a fifth way to buy a GPU-hour with a different risk shape from the four §1.2 listed, and Kimi-K3's 1.56 TB is a real per-cold-start cost that §2.5 alluded to but never costed.
6. **Nothing in this document looks invented.** Every number traced to either a live primary source, an in-tree document, or a `python3` derivation shown inline. The failures found were all of the *over-quoting* kind — quotation marks around paraphrases (§1.1, §3.2), figure-read values presented as stated text (§2.2), and a citation attached to a page that does not carry the number (§1.3 colo, §6.3 arXiv:2501.08219).

7. **2026-09-19, gap `G3` (Kimi-K3 B300 anchor) — checked, no recut.** §9.2's Kimi-K3 row (2,224 tok/s/node, $24.6380/$12.3190/$8.6958) and §9.4's $12.3235/$7.0972 are on the decode-only basis. The one published B300 measurement of Kimi-K3 — Wafer, TP8+DCP8, SGLang, DSpark, ISL 1024 / OSL 400, peak at c64: 1,568 tok/s/node = **196 out tok/s/GPU** (<https://www.wafer.ai/blog/kimi-k3-mi355x>, 2026-07-31) — falls within **1.5 %** of `kimik3/b300.md` §4.2's **sustained** 198.9 tok/s/GPU, corroborating the sustained basis and explaining the 2.9× gap to the decode-only S4 rate as a basis difference rather than an error ([`kimik3/b300.md` §3.8](../models/kimik3/b300.md)). **No figure was recut**; §9.2 gains a footnote naming the anchor and the 1.40× sustained-basis adjustment a planner would apply.

8. **2026-09-19, gap `G2` (spot / preemptible and weight-distribution egress) — closed, note 5 resolved.** §1.2 becomes a **five**-row buy table with an interruption-notice column, and a new **[§6.4](#64-spot-and-preemptible-capacity)** covers spot scoped to the **AWS p6 burst tier only** (owned metal has no spot tier, stated explicitly). Primary sources opened today: <https://aws.amazon.com/ec2/spot/> (*"up to 90% off"* — the page carries **no** interruption-notice text), <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-interruptions.html> (reclamation reasons; terminate/stop/hibernate) and <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/spot-instance-termination-notices.html> (*"a warning that is issued two minutes before"*, *"emitted on a best effort basis"*, check *"every 5 seconds"*, hibernate exempted from the two-minute warning) — the 2-minute figure is on the **third** page, not the second. The survivability test is carried over verbatim from [`05` §6.5](05-autoscaling-and-predictive-scaling.md) and joined to [`06` §1](06-cold-start.md)'s per-model cold-start table: 2 min < 2–13 min for both MoEs, so spot is batch/flex-tier only and never an interactive warm floor. New `python3` arithmetic, all on prices already in the tree: node-hour saving **$97.69** (vs on-demand) / **$18.79** (vs `res1y`), one Kimi-K3 preemption **$9.69**, break-even **~10.1** / **~1.9** preemptions per hour — i.e. the constraint is the SLO, not the money. Egress/storage priced from <https://aws.amazon.com/s3/pricing/>: same-region S3→EC2 transfer is **free** and GETs are **$0.0004/1,000**, so one full 12.5 TB fleet fan-out costs **$0.60** in requests (peer ring: **$0.07**) — the [`12` §6.1 row 4](12-inference-providers.md#61-the-ranked-list) 12.5 TB→1.56 TB ring is worth building for **time**, not money. ⚠️ **TO BE VERIFIED and marked as such in §6.4:** p6-b300 spot price (Vantage-reported, not a capacity guarantee) and its interruption rate (unpublished); on-prem-origin→AWS per-GB transit; the S3 Standard at-rest rate (the pricing page renders no per-region table). Companion edit: [`10` §7](10-blueprint.md)'s *"explicitly not built"* clause rescoped to owned metal.
