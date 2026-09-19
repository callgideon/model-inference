# Playbook — decision rules and checklists

Condensed operator's guide to `research/scaling/` 01–09 and 12. **Every number here
is quoted from a linked document; nothing is derived in this file.** If a row and its
source disagree, the source wins. Per-GPU and per-model numbers live in
[`../METHODOLOGY.md`](../METHODOLOGY.md), [`../gpus/`](../gpus/),
[`../cross-cutting/`](../cross-cutting/), [`../models/`](../models/) and
[`../matrix/`](../matrix/) — link to them, never re-derive.

Repo context assumed throughout: five models (`deepseek41f`, `deepseek41fnvfp4`,
`qwen3827b`, `kimik3`, `marlin2b`) on 8×B300 HGX nodes (268 GB/GPU) with local NVMe,
plus AWS p6 nodes.

Short names used below: **01** [bare-metal cluster](01-bare-metal-cluster.md) ·
**02** [serving & routing](02-serving-stack-and-routing.md) ·
**03** [concurrency & admission](03-concurrency-and-admission-control.md) ·
**04** [throughput & utilisation](04-throughput-and-utilization.md) ·
**05** [autoscaling](05-autoscaling-and-predictive-scaling.md) ·
**06** [cold start](06-cold-start.md) · **07** [cost](07-cost-engineering.md) ·
**08** [reliability & ops](08-reliability-and-operations.md) ·
**09** [reference architectures](09-reference-architectures.md) ·
**12** [inference providers](12-inference-providers.md).

---

## 1. Decision tables

### 1.1 PD disaggregation — when

Run the gate in order; the first **no** ends it ([09 §4.3](09-reference-architectures.md),
[02 §8.3](02-serving-stack-and-routing.md)).

| # | Gate | Threshold | Verdict here |
|---|---|---|---|
| 1 | KV transfer cheap? `ctx × kv_bytes_per_token ÷ fabric rate` | DeepSeek-V4.1-Flash 890 B/tok → ~114 MB @128K ≈ 2.3 ms; Kimi-K3 13.5 KiB/tok → ~1.77 GB ≈ 35.4 ms (both `est.`) | DSF cheap, K3 expensive |
| 2 | Replica needs > 1 node? | ≤ 4 GPUs → PD only buys independent phase scaling | DSF, Qwen, Marlin fail this |
| 3 | What does PD cost in features? | SGLang PD drops **DSpark** on DSF, priced at **3.13× output per byte** ([`../matrix/recommendations.md`](../matrix/recommendations.md)) | settles it for the DSF pool |
| 4 | Fleet scale | disaggregation arithmetic "rarely closes" below ~1,000 GPUs; 20–30 % regression measured on small/untuned workloads ([`../cross-cutting/serving-optimizations.md` §3.5](../cross-cutting/serving-optimizations.md)). Revisit at **≥ 8 nodes** | not yet |
| 5 | Measured for your traffic? | published gains span **10–30 % to 70 %** | benchmark before adopting |

**Verdict:** PD **not** recommended for DSF / DSF-NVFP4 / Qwen3.8-27B / Marlin-2B;
Kimi-K3 is the only candidate, and it is marginal on gate 1.

**If you do it:** size pools from measured rates, not a rule of thumb. Published optima
span **2:1 → 1:4.5** on the same axis ([04 §2.3](04-throughput-and-utilization.md)).
Start **fixed**, let replica autoscaling absorb drift; go dynamic only when the ratio
your traffic wants swings **> 2×** across the day. Formula and the two terms everyone
gets wrong (`1 − cache_hit_rate`; measured, never roofline'd, `prefill_tok_per_s`) are in
[04 §2.3](04-throughput-and-utilization.md). Budget the rollout from
[06 §5.6](06-cold-start.md) — a PD pool has **two** cold-start budgets and two pre-warm
policies (decode sized on the arrival ramp, prefill on the prompt-token rate), and boots
in dependency order at `max(t_P, t_D)` + KV handshake; the Kimi-K3 arithmetic is
[06 §8.6](06-cold-start.md).

### 1.2 TP vs DP replicas

The fork is a **model property**, not a hardware one ([04 §2.1](04-throughput-and-utilization.md)).

| Case | Test | Rule | Repo examples |
|---|---|---|---|
| **A — KV sharded** (GQA/MHA, `n_kv_heads ≥ TP`) | aggregate node KV rises with TP | prefer **fewer, wider** replicas; **stop at `TP = n_kv_heads`** | Qwen3.8-27B stops buying past TP4 (4 KV heads); Marlin-2B saturates at TP2 (2 KV heads) |
| **B — KV replicated** (MLA without DCP/DP-attention) | single-GPU budget, do not multiply by `n_gpus` | **halve TP whenever `W/N < ⅔ × (usable − workspace)` at the lower `N`** | DSF: 4×TP2 gives ~1.67× the aggregate KV of 1×TP8; measured **2,656 tok/s/GPU @TP2** vs **1,373 @TP4** |
| **Escape hatch** | DCP shards replicated MLA KV by token position | use it and go back to Case A | Kimi-K3 ships **TP8 + DCP8** |

Aggregate decode throughput is ~independent of `N`; what does not cancel is TPOT
(`∝1/N`, favours high TP), all-reduce cost, blast radius and per-replica batch
(all favour low TP). **The trade is TPOT for aggregate capacity — so run two pools of
different TP on the same node type, not one compromise TP**
([04 §8.1–8.3](04-throughput-and-utilization.md)).

### 1.3 Which router

Ladder, cheapest first — take the lowest rung that holds
([02 §3.1](02-serving-stack-and-routing.md)).

| Rung | Use when |
|---|---|
| Round-robin / random | uniform prompts, no shared prefixes. Also the baseline to measure against |
| Least-outstanding-requests | variable output lengths, no prefix reuse, single-GPU replicas |
| Power-of-two-choices | same, with many replicas (a global minimum is stale by the time you use it) |
| Queue-length / token-load | variable **prompt** lengths — request count lies when one request is 256K tokens |
| Prefix affinity (approximate) | shared system prompts, multi-turn chat, few-shot templates |
| Prefix affinity (precise, KV-event driven) | agentic/long system prompts where a wrong guess costs a full re-prefill |
| Session affinity | long multi-turn sessions, or non-append-only engine state |
| SLO / predicted-latency | high variance in both prompt and output length, per-request SLOs |

Three thresholds that decide it ([02 §3.2](02-serving-stack-and-routing.md)):
prefix routing's measured **+113 % throughput / −99.7 % TTFT p50** is a
**saturation-regime** gain (the arms track each other below ~rate 15), it costs
**+22.4 % ITL** deliberately, and the comparison published is against round-robin,
**not** against a tuned token-load router (⚠️ open).

Per-model verdicts, and the five-pool / one-gateway layout:
[02 §8.2, §8.7](02-serving-stack-and-routing.md). Pool-per-model is the default and is
mandatory for a base model; shared pool only via LoRA multiplexing on the same base
([02 §5.3](02-serving-stack-and-routing.md)).

### 1.4 When to scale to zero

`idle_cost_saved > cold_start_penalty × requests_affected`. **On owned bare metal the
left side is zero unless the node goes to another workload** — so the real lever is
fleet scheduling, not autoscaling ([05 §0, §5.4](05-autoscaling-and-predictive-scaling.md)).

| Model | Min replicas | Zero? | Gate |
|---|---:|---|---|
| Marlin-2B | 0–1 | **Yes** | 1 GPU, ≈35–55 s cold start pre-staged ([06 §0](06-cold-start.md)); worst-case first request = KEDA `pollingInterval` (30 s default) + `L` ⇒ **90 s–3.5 min** |
| Qwen3.8-27B | 0–1 | **Yes, with a cron floor** | ≥1 during business hours, zero overnight |
| DeepSeek-V4.1-Flash | **2** | **No** | cold start + min 2 gives N+1 against a single replica loss |
| DSF-NVFP4 | 1–2 | **No** | variant pool, follows the base |
| Kimi-K3 | **1 node** | **Never** | whole 8×B300 node per replica; front it with a queue and accept the wait |

Anti-thrash, with the defaults to change ([05 §5.3](05-autoscaling-and-predictive-scaling.md)):
HPA `scaleDown.stabilizationWindowSeconds` 300 → **1800**; `scaleUp` period **≥ L**;
KEDA `cooldownPeriod` 300 → **900** (Marlin/Qwen); Knative `max-scale-down-rate` 2.0 →
**≤ 1.25**. **Asymmetry rule: up fast in small steps, down slowly one replica at a time.**

Predictive scaling earns its keep **exactly when lead time exceeds headroom burn
time** — true for DSF and Kimi-K3, false for Marlin-2B and Qwen3.8-27B. Compose
reactive and predictive by `max()`; never let a forecast scale you *down*
([05 §0, §4.4](05-autoscaling-and-predictive-scaling.md)).

### 1.5 Buy vs rent vs API

First matching row wins ([07 §8.4](07-cost-engineering.md)); break-even cells are in
[`../matrix/cost-matrix.md` §6.2](../matrix/cost-matrix.md).

| If… | …then |
|---|---|
| < ~1M tokens/day, or bursty with no floor | **API** |
| break-even utilisation **> 100 %** in `cost-matrix.md` §6.2 | **API** (true for DSF on 15 of 16 cells; Kimi-K3 everywhere but B300) |
| break-even **< 30 %** and volume steady | **rent committed, or own** (Qwen3.8-27B is 6–28 % on every GPU) |
| break-even **30–100 %**, steady, `U ≥ 0.6` demonstrated | **rent committed** first; own after 12 months of evidence |
| no vendor API exists | own or rent — not a contest (Marlin-2B) |
| **≥ 4 nodes**, `U ≥ 0.6` sustained **≥ 12 months**, capacity holdable | **own** (own ÷ rent-`low` = 0.59–0.63, [07 §1.4](07-cost-engineering.md)) |
| residency / ZDR / air-gap required | **own** — a constraint, not a cost decision |

**The mistake this table exists to prevent:** comparing a self-hosted `$/1M` (a `U`=1
floor) against a vendor list price (`U`-inclusive). **Divide by your `U` first.**
Market-price cross-checks per model class: [12 §5](12-inference-providers.md).

### 1.6 Which KV tier

Tier names across KVBM / HiCache / LMCache and the sharing boundary:
[04 §4.1](04-throughput-and-utilization.md). **Bandwidth is never the reason not to
tier** — every model here clears the break-even by 2–4 orders of magnitude
([04 §4.3](04-throughput-and-utilization.md)). What decides it:

| Tier | Rule |
|---|---|
| **L1 / G1 — HBM** | always; prefix caching on for every model except Marlin-2B, where reuse is structurally zero |
| **L2 / G2 — host DRAM** | **on everywhere.** Nearly free, break-even is not close |
| **L3 / G3 — local NVMe** | on, with a write filter — `write_through_selective` (SGLang) / frequency ≥ 2 (Dynamo), or you build a bandwidth amplifier and burn SSD endurance |
| **L4 / G4 — shared, cross-replica** | **only** for multi-turn/agentic traffic spanning > 1 replica; measure the cross-instance miss rate first. Marlin-2B is likely dead weight ⚠️ |

The decisive evidence is a routing fact, not a memory fact: **1.7 % → 92.2 %** hit rate
purely by making the cache cross-instance ([04 §4.4](04-throughput-and-utilization.md)).
Four real decision inputs, in order: **hit rate**, **capacity × reuse window**
(`kv_block_reuse_gap_seconds` vs your eviction age), **latency not throughput**,
**SSD endurance**. Kimi-K3 exception: do **not** pair L3 HiCache with DCP — the storage
keys are not `dcp_rank`-aware and DCP is silently dropped
([`../matrix/recommendations.md` §2.1](../matrix/recommendations.md)).

Storage tiers for *weights* (a different question): **T3 → T2 → T0, never T3 → T0 at
serve time** ([01 §1.8](01-bare-metal-cluster.md)).

### 1.7 Workload × model class → stack

Full matrix, including RAG, batch and video rows: [09 §6](09-reference-architectures.md).
Its four cross-cutting rules: never one config for five models; batch and interactive
share hardware through a quota system; scale-to-zero is for small models only; **the
routing decision is a function of measured prefix reuse, not model size**.

Orchestrator: **Kubernetes** for this repo — every workload is a request-serving
endpoint with an SLO ([01 §3.4](01-bare-metal-cluster.md)).

---

## 2. Checklists

### 2.1 Bring up a new node

1. Firmware, driver and CUDA at the pinned stack — [01 §2.1, §2.4](01-bare-metal-cluster.md).
2. Fabric Manager / NVSwitch healthy; NVLink domain intact — [01 §2.2](01-bare-metal-cluster.md), [08 §2.4](08-reliability-and-operations.md).
3. GPU Operator (26.7.x line), Network Operator, NFD — [01 §3.5–3.6](01-bare-metal-cluster.md).
4. GPUDirect RDMA verified end to end, not just installed — [01 §1.5](01-bare-metal-cluster.md), anti-pattern [09 §5.2](09-reference-architectures.md).
5. MTU / jumbo frames; `hostNetwork` vs overlay decided for RDMA — [01 §4.3–4.4](01-bare-metal-cluster.md).
6. NVMe laid out (RAID0, ≥ the checkpoint budget in [01 §1.8, §5.4](01-bare-metal-cluster.md)).
7. `dcgmi diag --run 1` as a gate; **exit 205 ⇒ cordon permanently, 226 ⇒ retry once** — [08 §4.4](08-reliability-and-operations.md).
8. DCGM exporter + node_exporter + Xid/SXid scraping + NPD/NVSentinel — [01 §6](01-bare-metal-cluster.md).
9. Node labels and taints for the GPU generation and the big-model pool — [01 §3.9](01-bare-metal-cluster.md).
10. Pre-pull the serving image via DaemonSet; **pre-pull beats lazy-pull on a fixed node set** — [06 §5.2](06-cold-start.md).
11. Pre-stage weights onto node NVMe (`common/download.sh` as a DaemonSet) — [06 §5.3](06-cold-start.md).
12. Run the capacity re-validation benchmark before the node takes traffic — [08 §6.3](08-reliability-and-operations.md).

### 2.2 Deploy a new model

1. Confirm the fit and shape from [`../matrix/fit-matrix.md`](../matrix/fit-matrix.md) and [`../matrix/recommendations.md`](../matrix/recommendations.md) — do not invent a shape.
2. Pick TP/DP per §1.2; pick the router per §1.3; pick KV tiers per §1.6.
3. Set the capacity budgets: `max_num_seqs` = **the SLO batch, not the KV batch** ([03 §2.3](03-concurrency-and-admission-control.md)); `max_num_batched_tokens` start **8192** ([03 §2.4](03-concurrency-and-admission-control.md)).
4. Set the three admission layers — engine (`max_num_queued_reqs` → 503), router, gateway quota. **Never only one layer** ([03 §3](03-concurrency-and-admission-control.md)).
5. Probes tuned for multi-minute boots; liveness `initialDelaySeconds` > weight load + graph capture. **Never wire overload into liveness** — [06 §7.2](06-cold-start.md), [03 §8.5](03-concurrency-and-admission-control.md).
6. Drain path: `terminationGracePeriodSeconds` > one full generation; preStop sleep for EndpointSlice propagation — [03 §5.5](03-concurrency-and-admission-control.md), [08 §3.3](08-reliability-and-operations.md).
7. Persist the compile/JIT caches keyed by (model, dtype, GPU, **engine version**) — [06 §3.1](06-cold-start.md).
8. Cap the CUDA-graph capture ladder to the batch sizes actually served — [06 §3.2](06-cold-start.md).
9. Quality gates before any traffic: Gate 1 golden prompts / Gate 2 logprob diff / Gate 3 task evals, per the change class — [08 §5.4](08-reliability-and-operations.md).
10. Record the SLO row (availability, TTFT p95, TPOT p95) in [08 §1.2](08-reliability-and-operations.md)'s table.
11. Wire the alerts in §3 and the autoscaling policy row in [05 §8.1](05-autoscaling-and-predictive-scaling.md).
12. Run the benchmark plan template and store the artifact — [08 §6.4](08-reliability-and-operations.md), [03 §7.5](03-concurrency-and-admission-control.md).

### 2.3 Before a launch or peak event

1. Forecast the curve; confirm the workload is actually seasonal — [05 §4.1](05-autoscaling-and-predictive-scaling.md).
2. Schedule pre-warm **ahead of the event by ≥ cold-start p95** — [05 §4.6](05-autoscaling-and-predictive-scaling.md), [06 §5.4](06-cold-start.md).
3. Size the warm pool: `standby_capacity ≥ arrival_ramp_rate × cold_start_p95 × service_time` — [06 §5.1](06-cold-start.md). Price it before calling it free.
4. Raise min replicas / set the cron floor; **never let a forecast scale down**.
5. Verify headroom and N+1 against the queueing model — [05 §6.2, §6.4](05-autoscaling-and-predictive-scaling.md).
6. Confirm hot/cold spare policy for node-sized replicas (Kimi-K3) — [08 §1.2, §3.5](08-reliability-and-operations.md).
7. Freeze changes: no engine minor, no tokenizer, no chat-template change inside the window — [08 §5.1–5.2](08-reliability-and-operations.md).
8. Rehearse the degradation ladder (§2.4 step 2) and confirm step 2 is live-adjustable on this build.
9. Confirm gateway per-tenant quotas and the 429/503 split are correct — [02 §4.3](02-serving-stack-and-routing.md), [03 §3.5](03-concurrency-and-admission-control.md).
10. Load-test **open-model** (arrival rate), not closed-model (fixed VUs) — [03 §7.2](03-concurrency-and-admission-control.md).

### 2.4 During an incident

1. **Classify first** against the catalogue ranked by what actually pages you — [08 §2.1](08-reliability-and-operations.md). Rows 5, 7, 8, 9 (NCCL hang, engine deadlock, tokenizer bug, quality regression) **emit no error**.
2. Take the **cheapest sufficient** recovery action from the ladder — [08 §3.1](08-reliability-and-operations.md). GPU reset always decomposes into *drain pods on that GPU → reset → uncordon*.
3. If it is load, walk the graceful degradation ladder in order ([03 §5.6](03-concurrency-and-admission-control.md)): shed the sheddable tier → **lower `max_num_active_seqs`** (the best live lever, no graph recapture) → cap server-side `max_tokens` → (spec decoding: already handled by adaptive verification here) → lower `max_num_batched_tokens` → reject at the engine.
4. Use the matching runbook: Xid 79 · NCCL hang · KV OOM storm · 429 storm · slow weight load · hot node · stuck rolling update · cache-hit collapse · quality regression · IB degradation — [08 §8.1–8.10](08-reliability-and-operations.md).
5. Do **not** run `dcgmi diag` on a GPU serving traffic; `dcgmi health` is the passive tool — [08 §4.4](08-reliability-and-operations.md).
6. Keep the metrics that must outlive the incident — [08 §4.9](08-reliability-and-operations.md).

### 2.5 Monthly FinOps review

Six questions, each with the document that answers it ([07 §8.3](07-cost-engineering.md)):
did goodput-`U` move; is any model > 1.5× its `cost-matrix.md` cell; is any model above
its vendor-API break-even; what fraction went to the most expensive model; what was the
trough in GPU-hours and what filled it; did an engine upgrade land and were reference
rates re-measured.

| Target | Value |
|---|---|
| Goodput-`U` | **≥ 0.60**, aim 0.85 |
| $/1M blended, per model | within **1.5×** of its [`../matrix/cost-matrix.md` §4](../matrix/cost-matrix.md) cell at your price tier |
| Prefix-cache hit rate, agentic routes | **≥ 0.90** |
| Traffic on the most expensive model | **≤ 0.30** |
| Idle GPU-hours | **≤ 15 %** of allocated |

Waste taxonomy and the PromQL that measures it: [07 §7.2–7.3](07-cost-engineering.md).
Market reality check on whether self-hosting still wins: [12 §5.5–5.6](12-inference-providers.md).

---

## 3. The 20 metrics to alert on

vLLM names verbatim from [03 §7.6](03-concurrency-and-admission-control.md) and
[05 §2.2](05-autoscaling-and-predictive-scaling.md); SGLang / TRT-LLM / Dynamo / EPP
equivalents in [05 §2.2](05-autoscaling-and-predictive-scaling.md) and
[08 §4.2–4.3](08-reliability-and-operations.md). DCGM fields and their ⚠️ caveat:
[08 §4.4](08-reliability-and-operations.md).

| # | Signal | Threshold | Why / source |
|---|---|---|---|
| 1 | `vllm:request_queue_time_seconds` p99 | `> 0.5 × TTFT SLO` → **page** | earliest true saturation signal — 03 §7.6 |
| 2 | `vllm:num_requests_waiting` | `> 0` for 5 m | capacity exhausted — 03 §7.6 |
| 3 | `vllm:kv_cache_usage_perc` | `> 0.95` for 5 m | preemption imminent — 03 §7.6 |
| 4 | `rate(vllm:num_preemptions_total[5m])` | `> 0` | over-admission — 03 §7.6 |
| 5 | TTFT p99 | per-model row in 08 §1.2 (Qwen 400 ms ⚠️ · DSF 800 ms ⚠️ · K3 1.5 s ⚠️) | contractual |
| 6 | TPOT p99 | `> 50 ms` (S1, METHODOLOGY §6) | contractual |
| 7 | prefix hit rate (`hits/queries`) | `< 0.5` → routing broke; agentic target **≥ 0.90** | 03 §7.6, 07 §8.3 |
| 8 | `vllm:request_success_total{finished_reason="abort"}` | rising | users giving up — 03 §7.6 |
| 9 | Dynamo migration failures | `outcome != "success"` rising | fault tolerance not working — 03 §7.6 |
| 10 | generation tokens/s flat while `num_requests_running > 0` | any sustained window | **silent** NCCL hang / engine deadlock — 08 §2.6 |
| 11 | error-budget burn rate | **14.4** over 1 h/5 m → page; **6** over 6 h/30 m → page; **1** over 3 d/6 h → ticket | 08 §1.3 |
| 12 | `DCGM_FI_DEV_XID_ERRORS` | any fatal-class Xid | 08 §2.2, §4.4 ⚠️ |
| 13 | `DCGM_FI_DEV_ECC_DBE_VOL` | any | 08 §2.3 ⚠️ |
| 14 | `DCGM_FI_DEV_RETIRED_SBE` / `_DBE` | any increase | row-remap capacity tripwire — 08 §2.3 ⚠️ |
| 15 | `DCGM_FI_DEV_GPU_TEMP`, `_MEMORY_TEMP` | vendor limit | leading indicator for throttling — 08 §4.4 ⚠️ |
| 16 | `DCGM_FI_DEV_POWER_USAGE` | unexplained drift | power capping shows up as TPOT drift — 08 §4.4 ⚠️, 07 §6.3 |
| 17 | NVLink/NVSwitch SXid, Xid 74/155 | any | whole TP replica — 08 §2.4 |
| 18 | IB/RoCE link flap counters, NCCL retries | any, on multi-node replicas | 08 §2.5, §8.10 |
| 19 | MBU per pool | outside **0.50–0.70** planning band ⚠️ | 04 §8.3 — **never alert on `DCGM_FI_DEV_GPU_UTIL`**, it reads ~100 % with one SM busy (04 §1.2) |
| 20 | pod not Ready | `>` the startup budget (llm-d's recommended budget is 30 s × 60 = 30 min) | slow weight load — 08 §2.9, §8.5 |

Also worth a dashboard row rather than a page: gateway per-tenant 429 rate
([08 §8.4](08-reliability-and-operations.md)) and `PIPE_TENSOR_ACTIVE` low while the
engine reports busy — the silent-waste query in [04 §1.5](04-throughput-and-utilization.md).

---

## 4. The 15 engine flags that matter most

Defaults below are **this repo's recommended launch shapes**, from
[`../matrix/recommendations.md` §2.1](../matrix/recommendations.md),
[02 §8](02-serving-stack-and-routing.md) and [03 §8.5](03-concurrency-and-admission-control.md).
`—` = not applicable. Class defaults for vLLM (`max_num_batched_tokens` 2048,
`max_num_seqs` 128) and SGLang are quoted in [03 §1.2–1.3](03-concurrency-and-admission-control.md).

| # | Flag | DeepSeek-V4.1-Flash | DSF-NVFP4 | Qwen3.8-27B | Kimi-K3 | Marlin-2B |
|---|---|---|---|---|---|---|
| 1 | `--tensor-parallel-size` | **4** interactive / **2** batch | **4** (never TP2 at 268 GB) | **1** × 8 replicas | **8** | **1** × 8 replicas |
| 2 | `--decode-context-parallel-size` | — | — | — | **8** (DCP8, **not** EP8) | — |
| 3 | expert parallel | **EP1** — wide-EP runs badly on an 8-GPU domain | EP4 | n/a (dense) | see #2 | — |
| 4 | `--kv-cache-dtype` | FP4 890 B/tok native | FP4 890 B/tok | **`fp8`** → +41 % concurrency | **`fp8` mandatory** | BF16 (`auto`); FP8 forces FA4→FA2 |
| 5 | `--max-num-seqs` | **256** | **256** (recipe start; 32 is the alternative) | derive per 03 §2.3 ⚠️ | `--max-running-requests 32` (SGLang recipe) | derive per 03 §2.3 ⚠️ |
| 6 | `--max-num-active-seqs` | **256** | as #5 | as #5 | as #5 | as #5 |
| 7 | `--max-num-batched-tokens` | **8192** | 8192 | 8192 | `--max-prefill-tokens` 16384 (SGLang default) | 8192 |
| 8 | `--max-num-queued-reqs` | **280** (worked example) | derive | derive | derive | derive |
| 9 | `--max-num-queued-tokens` | **64000** = `target_TTFT × prefill_throughput` | derive | derive | derive | derive |
| 10 | `--max-model-len` | **131072** | 131072 | per pair doc | **1048576** | per pair doc |
| 11 | speculative decoding | **DSpark γ=5**, `probabilistic` draft, `block` rejection, **adaptive verification on** | ships, never exercised by NVIDIA ⚠️ | **MTP γ=3** | **DSpark** (block size 7) — costs admission 101 → 68 | **none** — checkpoint ships zero `mtp` tensors |
| 12 | prefix caching | **on** (90.2–97.0 % hit measured) | **on** — turn NVIDIA's `--no-enable-prefix-caching` back on | **on** | **on + `--prefix-match-unit 128` mandatory** | **structurally zero** — leave off |
| 13 | `--scheduling-policy` | **`priority`** | `fcfs` | `fcfs` | `fcfs` | `fcfs` |
| 14 | the flag that fails **silently** if skipped | `--engram-config '{"cpu_offload":true}'` is **TP2 only** | see #12 | **`--mamba-ssm-dtype bfloat16`**; not `flashinfer`, not `trtllm_mha` | do **not** pair L3 HiCache with DCP (DCP silently dropped) ⚠️ | **`--block-size 128`** (else FA4→FA2) + `--mamba-cache-mode=align` + both EOS ids `[248044, 248046]` |
| 15 | memory / KV sizing | `--gpu-memory-utilization` per pair doc | as base | `--max-mamba-cache-size` — GDN state is the binding knob | `--mem-fraction-static` **0.92** (SGLang recipe) | `--mamba-cache-mode=align` |

Two cross-model notes: **`max_num_batched_tokens ≥ max_num_seqs`**, and with chunked
prefill off, `max_num_batched_tokens ≥ max_model_len` or the server refuses to start
([03 §1.2](03-concurrency-and-admission-control.md)). ⚠️ Confirm every admission flag
exists in your image before deploying — the flag names moved between engine versions
([02 §2.1–2.2](02-serving-stack-and-routing.md)).

Knobs ranked by how much they move throughput, with their risks:
[04 §5.9](04-throughput-and-utilization.md).

---

## 5. Glossary

| Term | Meaning |
|---|---|
| **TTFT / TPOT / ITL** | time to first token; time per output token; inter-token latency. TPOT and ITL are **not** the same measurement — reconcile tool definitions before comparing ([03 §7.4](03-concurrency-and-admission-control.md)) |
| **Goodput** | throughput counting only requests that met their SLO ([03 §4.1](03-concurrency-and-admission-control.md)) |
| **`U` (utilisation)** | delivered output tok/s ÷ (reference tok/s/GPU × GPUs allocated); the honest definition is in [07 §1.5](07-cost-engineering.md) |
| **MBU / MFU** | achieved memory-bandwidth / FLOP utilisation. The definition and its trap: [04 §1.3](04-throughput-and-utilization.md) |
| **Batch occupancy** | running requests ÷ `max_num_seqs` ([04 §1.4](04-throughput-and-utilization.md)) |
| **PD disaggregation** | prefill and decode in separate pools, KV moved over RDMA ([09 §4.3](09-reference-architectures.md)) |
| **E/P/D** | encode / prefill / decode split — the relevant one for video VLMs ([09 §6](09-reference-architectures.md)) |
| **TP / DP / EP / PP / DCP** | tensor / data / expert / pipeline parallel; **DCP** = decode context parallel, shards replicated MLA KV by token position ([04 §2.1](04-throughput-and-utilization.md)) |
| **Wide-EP** | expert parallelism spanning nodes; an NVL72 pattern, not an 8-GPU HGX one ([04 §6.1, §6.3](04-throughput-and-utilization.md)) |
| **EPLB** | expert-parallel load balancing; on for any EP ≥ 16, budget its redundant-expert HBM ([04 §5.5](04-throughput-and-utilization.md)) |
| **G1–G4 / L1–L3** | KV tier names (KVBM / HiCache). Only G4 / L3 is shared across replicas ([04 §4.1](04-throughput-and-utilization.md)) |
| **Chunked prefill** | splitting a prompt pass across scheduler steps to stop it stalling decode ([03 §4.2](03-concurrency-and-admission-control.md)) |
| **Preemption (recompute / swap / retract)** | what an engine does when KV runs out ([03 §1.5](03-concurrency-and-admission-control.md)) |
| **Admission control / backpressure** | rejecting or delaying at engine, router and gateway — the three-layer ladder ([03 §3.10](03-concurrency-and-admission-control.md)) |
| **429 vs 503** | 429 = *this tenant* sent too much; 503 = *the fleet* is full. Both carry `Retry-After` ([03 §3.5](03-concurrency-and-admission-control.md)) |
| **Open- vs closed-model load** | arrival-rate vs fixed-VU load generation; closed-model measures your own client's backpressure ([03 §7.2](03-concurrency-and-admission-control.md)) |
| **InferencePool / EPP** | Gateway API Inference Extension's pool object and its endpoint-picker ([02 §4.1](02-serving-stack-and-routing.md)) |
| **Precise vs approximate prefix routing** | KV-event-driven index vs router-local hash ([02 §3.3](02-serving-stack-and-routing.md)) |
| **Lead time `L`** | seconds from scale-up decision to a replica serving; mostly cold start ([05 §4.5](05-autoscaling-and-predictive-scaling.md)) |
| **Headroom burn time** | how long current headroom absorbs growth before SLO breach ([05 §4.4](05-autoscaling-and-predictive-scaling.md)) |
| **Sleep mode / snapshot-restore** | vLLM sleep levels 1/2, `cuda-checkpoint`/CRIU; not production-ready for TP4/TP8 ([06 §4](06-cold-start.md)) |
| **Xid / SXid** | NVIDIA GPU and NVSwitch error codes; the catalogue *is* the runbook ([08 §2.2, §2.4](08-reliability-and-operations.md)) |
| **Burn-rate alerting** | multiwindow multi-burn-rate error-budget alerting ([08 §1.3](08-reliability-and-operations.md)) |
| **Break-even utilisation** | the `U` at which self-hosting matches a vendor API ([`../matrix/cost-matrix.md` §6.2](../matrix/cost-matrix.md), [07 §8.4](07-cost-engineering.md)) |
| **Reference architecture blueprints A/B/C** | 4-, 16- and 64-node 8×B300 layouts ([09 §7](09-reference-architectures.md)) |

---

**Everything above links back.** If you need a number this page does not carry, it is in
the linked section — or it does not exist yet, in which case it is in that document's
*Open questions*.
