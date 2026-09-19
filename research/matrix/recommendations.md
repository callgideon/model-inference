# Recommendations — what to run where, and what to benchmark first

Research date **2026-09-19**. This document consolidates the five per-model GPU guides
([DeepSeek-V4.1-Flash](../models/deepseek41f/README.md) ·
[DeepSeek-V4.1-Flash-NVFP4](../models/deepseek41fnvfp4/README.md) ·
[Qwen3.8-27B](../models/qwen3827b/README.md) ·
[Kimi-K3](../models/kimik3/README.md) ·
[Marlin-2B](../models/marlin2b/README.md))
and the machine-readable [`pairs.json`](./pairs.json) into one deployment view.
Formulas, the S1–S4 scenarios and the `est.` / `meas.` / **⚠️ TO BE VERIFIED** legend are
[`METHODOLOGY.md`](../METHODOLOGY.md) §1–§8. **No number here is new**: every figure is either
read out of `pairs.json` with `python3` or carried from a linked source document. Where two
documents disagree, both are cited and neither is silently picked
([§2.5](#25-where-pairsjson-and-the-per-model-guides-disagree)).

Scenario shorthand throughout: **S1** = 4K in / 512 out at TPOT ≤ 50 ms (interactive),
**S4** = same shape with no SLO (max throughput), **blended** = 75 % input (half cached) /
25 % output at the S1 operating point ([METHODOLOGY §6](../METHODOLOGY.md)).

---

## 1. Verdict per model

**DeepSeek-V4.1-Flash** — run it, on the B300 node, and expect to lose to DeepSeek's own API on
output price. It is the only model in the set whose primary pair is `measured` on this repo's
exact silicon: 4 × B300 at TP4 gives **1,373 output tok/s/GPU at 23.31 ms TPOT**, and four
independent TP2 replicas per node give **2,656 tok/s/GPU** — a 1.48× throughput-per-GPU win at
the cost of long-prompt TTFT ([b300.md](../models/deepseek41f/b300.md), via
[README §Recommendation](../models/deepseek41f/README.md)). Two facts govern everything: the
MXFP4 experts execute **W4A8 on the 4,500 TFLOPS FP8 line, not the 13,500 TFLOPS FP4 line**, so
B300 ≡ B200 to within 1.5 % at every concurrency B200 can hold and B300 wins only on *capacity*
(+245 % at concurrency 128); and the 890 B/token FP4 KV cache is a **kernel** gate
(`capability.major == 10`), not an architecture one, so Hopper pays 1,650 B/token (H100: 3,200)
and loses the fused mega-attention kernel at the same time. Self-hosting costs **$1.50–$3.04 per
1M output** interactive on B300 against DeepSeek's **$0.60** off-peak API — eight independent
pair analyses agree that no *rented* GPU of any kind reaches $0.60, and only owned or amortised
silicon ($1.42–$2.31/GPU-hr) does. The inversion is on input-heavy agentic traffic, where B300
serves **$0.0198/1M per total token** at 95.6 % cache hit. Run it for data residency, the MIT
licence, freedom from the 2,500-request concurrency cap and 1M context at fixed cost — not for
$/token.

**DeepSeek-V4.1-Flash-NVFP4** — do not deploy it; deploy the base MXFP4 checkpoint. All eight
pair documents reach this independently ([README §Summary item 10](../models/deepseek41fnvfp4/README.md)).
It is **+16.99 GB (+3.33 %) larger** than the base for an accuracy wash (NVIDIA's own table is
4 wins / 2 losses, all within ±1.5 points), it adds **+5.5–5.9 % decode bytes per step** on a
decode that is memory-bound on every GPU studied — a direct 2–5.7 % TPOT penalty with no
compensating term — and **NVIDIA publishes no throughput, TTFT or TPOT figure for it on any
GPU**. Only GB300 is vendor-validated; B200 and B300 execute NVFP4 natively but are validated by
nobody; H100/H200 fall back to Marlin W4A16 (4-bit storage kept, FP4 math lost) with an open
correctness bug; A100 and MI355X are `not-runnable`. It has **zero `measured` pairs** in the
whole roster. The one reason to pick it is if NVIDIA's group-16 calibration audit is itself a
requirement.

**Qwen3.8-27B** — the easiest model here and the one with the least evidence behind it. It fits
**one GPU in every precision on every card in the survey**; TP is only ever a KV-headroom or
latency instrument and no fabric is ever needed. On the B300 node the shape is **eight
independent TP1 replicas, one per GPU**, which holds 8 × 325 = 2,600 concurrent 8K sequences
against TP8's 2,011 with zero NVLink traffic in the decode loop. Expect **12,463 tok/s/GPU at
20.54 ms TPOT and $0.165–$0.334 per 1M output**, blended **$0.060–$0.122** against Qwen Cloud's
$3.00/1M output — every GPU in the survey clears break-even at ≤ 21 % utilisation, so **the GPU
choice here is a capability and risk question, not a cost question**. Two flags move more than
any GPU choice: `--mamba-ssm-dtype bfloat16` (+57–60 % concurrency at 8K, *and* the gate on the
FlashInfer GDN prefill kernel on SM100/SM103) and `--kv-cache-dtype fp8_e4m3` (+40 %). The
caveat is total: **confidence is `estimate` on all eight pairs, and nobody has published a
throughput, TPOT or TTFT number for this model on any datacentre GPU** — not MLPerf, not
InferenceX (`200 []`), not either engine's cookbook.

**Kimi-K3** — the only model here that is genuinely hard to serve, and the only one where the
vendor API is competitive on its own terms. 2,779.9 B params, **1,560.9 GB on disk**, and a
memory profile with two pools (13,824 B/token FP8 MLA KV, TP-*replicated* unless DCP is on, plus
a 428.6 MiB KDA state slot × S=5 = 2.25 GB per request). **8 × B300 is both the minimum and the
reference platform** — one HGX node at 195.1 GB/GPU, TP8 + DCP8, and the only `verified: true`
cells for K3 anywhere — giving **198.9 tok/s/GPU at 49.9 ms TPOT** (just inside the SLO) and
**$10.34–$20.95 per 1M output** against Moonshot's $15.00. Hopper is out on cost at every
published tier (H100 needs 32 GPUs and cannot reach the 50 ms SLO at any concurrency; H200's best
measured point is 17.36 tok/s/GPU); A100 is `not-runnable`. The decisive variable is not silicon
but **your cache-hit rate**: a hit costs you ~10 % of a prefill but costs Moonshot 90 % of its
input revenue, so break-even falls ~2.0× from 0 % to 90 % hit, and at the 92 % OpenRouter
measures on real K3 traffic the only survivors are **B300 on neocloud pricing (47.6–67.7 %
utilisation) and B200 with DSpark**. Note the licence gate: Kimi K3 License §2 requires a
separate agreement with Moonshot for any Model-as-a-Service business above **US$20 M aggregate
revenue over 12 months**; internal use is exempt.

**Marlin-2B** — free to run, and nobody has ever run it. 4.426 GB of BF16 weights, 12,288 B/token
of KV across 6 of 24 layers; it fits every GPU in the set with 94–98 % of the card unused, and
the answer is always **TP1, scale by replicas**. But `MarlinForConditionalGeneration` is in no
engine registry: vLLM ≥ 0.29.0 loads it only after `--hf-overrides
'{"architectures":["Qwen3_5ForConditionalGeneration"]}'`, and **that remap has never been run end
to end, on any GPU**. Three structural facts dominate: the **240-frame cap** bounds every video
request at ~23,560 prefill tokens regardless of clip length; at fleet scale **every GPU is
prefill-bound** (the inverse of the batch-1 picture); and **prefix caching is worth structurally
zero** (the cacheable scaffold is < 0.2 % of a request). There is no vendor API to compare
against — HF `inferenceProviderMapping` is `{}` — so break-even is parametric, and *a single
rented H100 at $3.20/hr beats a $0.30/1M-output API at 7.9 % utilisation*. On the repo's node it
costs **$0.022–$0.0447 per 1M output**; the unit that actually bills is $/1,000 two-minute
captions, where B200 leads at **$0.432**, H100 is within 7 % at $0.461 and B300 is third at
$0.487. Run it on the node you already own; never buy hardware for it.

### 1.1 Evidence quality, by model

| Model | `measured` pairs | `estimate` pairs | `not-runnable` / `speculative` | Total ⚠️ across 8 GPUs |
|---|---|---|---|---:|
| DeepSeek-V4.1-Flash | b300, gb300, a100, rtx6000-pro | h100, h200, b200, mi355x | — | 157 |
| DeepSeek-V4.1-Flash-NVFP4 | **none** | h100, h200, b200, b300, gb300, rtx6000-pro | a100 (`not-runnable`), mi355x (`not-runnable`) | 151 |
| Qwen3.8-27B | **none** | all eight | — | 141 |
| Kimi-K3 | mi355x | h100, h200, b200, b300, gb300, rtx6000-pro | a100 (`not-runnable`) | 128 |
| Marlin-2B | **none** | h100, h200, b200, b300, gb300, a100, rtx6000-pro | mi355x (`speculative`) | 172 |

Read from [`pairs.json`](./pairs.json) `confidence` and `unverified_count`. **Only one model has
a `measured` pair on the repo's own hardware** (DeepSeek-V4.1-Flash on b300 and gb300); Kimi-K3's
single `measured` pair is on MI355X, which this repo does not own.

---

## 2. This repo's hardware

### 2.0 What the hardware is, and what the research actually covers

| Platform | What the research covers | Price basis used in every table below |
|---|---|---|
| **8 × B300 HGX node (owned)** | [`gpus/b300.md`](../gpus/b300.md), 268 GB/GPU as deployed, 2,144 GB/node | `low` $7.40 (Hyperstack) – `high` $15.00 (OCI) |
| **AWS `p6-b300.48xlarge`** | Same silicon as the owned node | On-demand **$17.802/GPU-hr**, i.e. *above* the `high` tier; spot $5.591, Capacity Block $14.04 ([cloud-pricing.md §5](../cross-cutting/cloud-pricing.md)) |
| **AWS `p6-b200.48xlarge`** | [`gpus/b200.md`](../gpus/b200.md), 180 GB/GPU | On-demand **$14.242/GPU-hr**; spot $5.261; Capacity Block $12.355 |
| **AWS `p6e-gb200` UltraServers** | ⚠️ **Not directly covered.** The research tree's NVL-rack GPU is **GB300 NVL72** ([`gpus/gb300.md`](../gpus/gb300.md)), not GB200. AWS sells `u-p6e-gb200x36` / `x72` (GB200, 186 GB) **Capacity-Blocks-only at $10.582/accel-hr**, confined to the US East (Dallas) Local Zone, and **no `p6e-gb300` SKU exists as of 2026-09-19** ([cloud-pricing.md §5](../cross-cutting/cloud-pricing.md)) | Every `gb300` row below is priced at **OCI $18.00 — the only published GB300 rate anywhere**, and is *not* a p6e-gb200 quote |
| **H200 / MI355X / RTX PRO 6000 clouds** | [`gpus/h200.md`](../gpus/h200.md) · [`gpus/mi355x.md`](../gpus/mi355x.md) · [`gpus/rtx6000-pro.md`](../gpus/rtx6000-pro.md) | $3.99–$7.912 · **$8.60 single price** · $1.80–$4.143 |

Planning prices, as pinned by every pair document via
[`cloud-pricing.md` §5.14](../cross-cutting/cloud-pricing.md):

| GPU | `low` $/GPU-hr | `high` $/GPU-hr |
|---|---:|---:|
| [b300](../gpus/b300.md) | $7.40 | $15.00 |
| [b200](../gpus/b200.md) | $6.00 | $14.00 |
| [gb300](../gpus/gb300.md) | $18.00 | $18.00 (single price) |
| [h200](../gpus/h200.md) | $3.99 | $7.912 |
| [mi355x](../gpus/mi355x.md) | $8.60 | $8.60 (single price) |
| [rtx6000-pro](../gpus/rtx6000-pro.md) | $1.80 | $4.143 |

### 2.1 Recommended engine and launch shape, per model, on 8 × B300

| Model | Engine | Parallelism (TP / EP / DP) | KV dtype | Speculative decoding | Prefix caching | The flag that fails silently if skipped |
|---|---|---|---|---|---|---|
| **[DeepSeek-V4.1-Flash](../models/deepseek41f/README.md)** | **vLLM `main`/nightly, pinned digest** (`min_vllm_version 0.30.0` does not exist; latest release is 0.29.0). SGLang also `verified` on B300 | **Interactive: 2 × TP4 servers/node.** **Batch: 4 × TP2 replicas/node** (2,656 vs 1,798 tok/s/GPU). **EP1** — wide-EP "runs badly" on an 8-GPU domain. Never cross nodes | **FP4 890 B/token native** (`FLASHMLA_SPARSE` / `MEGA_ATTN`, `sm_103` passes `major==10`) | **DSpark γ=5**, `probabilistic` draft, `block` rejection, adaptive verification on — worth 3.13× output per byte; removing it takes $/1M from $1.50 → $4.69 | **On.** 90.2–97.0 % hit measured → effective input **$0.036/1M** | `--engram-config '{"cpu_offload":true}'` is **only** for TP2. At TP4 the Engram tables shard and stay resident — leaving the offload on buys you the PCIe gather path that cost DGX Spark 5.9–7.8 ms/step |
| **[…-Flash-NVFP4](../models/deepseek41fnvfp4/README.md)** | *Run the base checkpoint instead.* If mandated: vLLM `main`/nightly | 2 × TP4/EP4 replicas per node (GPUs 0–3, 4–7). **Never TP2 at 268 GB** — 2 × 268 GB against 527.27 GB is `infeasible (KV)` before the 0.90 factor | FP4 890 B/token | DSpark ships but NVIDIA never exercised it; 2.6–2.7 accepted/step measured on the *base* at conc 1 | **Turn it back on** — NVIDIA's own recipe passes `--no-enable-prefix-caching` with no stated reason | Raise `--max-running-requests 16` / `--max-num-seqs 32`; vLLM's B300 profile's `--max-num-seqs 256` is the better start |
| **[Qwen3.8-27B](../models/qwen3827b/README.md)** | vLLM 0.29.0 `hw/b300.json` (not in the verified set) or SGLang's GB300 cell as the nearest published `sm_103` recipe | **8 × TP1 replicas, one per GPU, behind a router** — 2,600 vs TP8's 2,011 concurrent 8K seqs, 8 independent failure domains. **EP is n/a (dense)** | `--kv-cache-dtype fp8` → **+41 % concurrency** (231 → 325 @8K). The `nvidia/` export ships no `kv_cache_scheme`, so `auto` silently leaves it BF16 | **MTP γ=3** (the 0.425 B head ships in the checkpoint). Measured acceptance-vs-batch on the family: 1.55× @c4 → 1.15× @c256. $0.165 → $0.143 at bs 256 | **On.** Sustained output 3,856 → 10,245 tok/s/GPU across 0 %/50 %/90 % hit | **`--mamba-ssm-dtype bfloat16`** — +19 % concurrency *and* the gate on the FlashInfer GDN prefill fast path (48 of 64 layers). Also: **not** `--attention-backend flashinfer` (illegal for hybrid-GDN on SM100/103) and **not** `trtllm_mha` (hangs on SM103) |
| **[Kimi-K3](../models/kimik3/README.md)** | vLLM ≥ 0.27.1 or SGLang ≥ 0.5.17 — **the only `verified: true` K3 cells anywhere are 8 × B300** | **TP8 + DCP8, one replica = the whole node.** **Use DCP8, not EP8** — a2a buffers reclaim the KV that DCP buys, and `DCP8 + EP8` is exactly the combination in the [sglang#34260](https://github.com/sgl-project/sglang/issues/34260) crash | `--kv-cache-dtype fp8` **mandatory** — "bf16 KV does not fit 128 requests per replica"; `TOKENSPEED_MLA` force-rewrites it anyway | **DSpark** — the biggest output-cost lever: 3.00× at c=1, 1.64× at c=64, at the cost of admission 101 → 68 (−33 %) | **On, with `--prefix-match-unit 128` mandatory** or the hit boundary inflates to the Mamba state page | Do **not** pair L3 HiCache with DCP — the storage keys are not `dcp_rank`-aware and DCP is **silently dropped** |
| **[Marlin-2B](../models/marlin2b/README.md)** | vLLM ≥ 0.29.0 + the remap, **CUDA ≥ 13 image**. `transformers ≥ 5.7.0` + `trust_remote_code=True` is the only first-class path | **8 × TP1 replicas + an HTTP load balancer** = 6,024 concurrent 2-minute videos node-wide. `num_key_value_heads: 2` caps clean KV sharding at TP2; on A100, TP8 gives 602 concurrent/GPU against TP1's 2,086 | BF16 by default. FP8 KV is +88 % concurrency but **forces the FA4 hd256 kernel back to FA2** — and the fleet is prefill-bound, so it may *lower* sustained throughput | **Unavailable.** The checkpoint ships **zero `mtp` tensors** despite `mtp_num_hidden_layers: 1`. This fixes METHODOLOGY §2's `S` at **1** | **Structurally zero** — `--mamba-cache-mode=all` raises `NotImplementedError`, `align` is experimental, and the cacheable scaffold is < 0.2 % of a request | `--block-size 128` is what turns on the FA4 hd256 kernel; any other value **silently falls back to FA2**. `--mamba-cache-mode=align` is mandatory. Re-supply both EOS ids `[248044, 248046]` — an engine reading only `config.eos_token_id` runs to `max_new_tokens=2048` |

### 2.2 Expected numbers on the repo's hardware and the AWS p6 family

Built with `python3` from [`pairs.json`](./pairs.json). `$/1M out` bands are `low`–`high` at the
per-GPU rates in [§2.0](#20-what-the-hardware-is-and-what-the-research-actually-covers); the AWS
on-demand list is **above** the `high` tier for both p6 shapes, so treat the `high` column as the
optimistic AWS case.

| Model | GPU | Min / rec GPUs | Max conc @8K | @128K | Interactive: tok/s/GPU · TPOT · TTFT | $/1M out (S1) | Max-thr: tok/s/GPU · TPOT | $/1M out (S4) | Blended $/1M | Vendor API $/1M out | Confidence | ⚠️ |
|---|---|---|---:|---:|---|---|---|---|---|---|---|---:|
| DeepSeek-V4.1-Flash | [b300](../models/deepseek41f/b300.md) | 2 / 4 | 11,184 | 953 | 1,373 · 23.31 ms · 129 ms | $1.50–$3.04 | 2,656 · 24.1 ms | $0.774–$1.57 | $0.481–$0.975 | $0.600 | `measured` | 23 |
| DeepSeek-V4.1-Flash | [b200](../models/deepseek41f/b200.md) | 4 / 8 | 9,007 | 768 | 3,604 · 22.2 ms · 55 ms | $0.462–$1.08 | 4,696 · 27.3 ms | $0.355–$0.828 | $0.190–$0.443 | $0.600 | `estimate` | 16 |
| DeepSeek-V4.1-Flash | [gb300](../models/deepseek41f/gb300.md) | 4 / 4 | 16,164 | 1,378 | 887.1 · 25.1 ms · 452 ms | $5.64 | 1,242 · 50 ms | $4.03 | $1.72 | $0.600 | `measured` | 17 |
| …-Flash-NVFP4 | [b300](../models/deepseek41fnvfp4/b300.md) | 4 / 4 | 40,801 ⚠️ | 3,479 ⚠️ | 481 · 49.9 ms · 405 ms | $4.27–$8.66 | 9,531 · 53.7 ms | $0.216–$0.437 | $1.16–$2.35 | $0.600 | `estimate` | 22 |
| …-Flash-NVFP4 | [b200](../models/deepseek41fnvfp4/b200.md) | 4 / 8 | 2,444 | 208 | 2,947 · 21.7 ms · 32.3 ms | $0.565–$1.32 | 34,705 · 25.6 ms | $0.0480–$0.112 | $0.148–$0.346 | $0.600 | `estimate` | 14 |
| …-Flash-NVFP4 | [gb300](../models/deepseek41fnvfp4/gb300.md) | 4 / 4 | 11,113 | 947 | 4,034 · 15.87 ms · 11 ms | $1.24 | 14,060 · 18.21 ms | $0.356 | $0.331 | $0.600 | `estimate` | 20 |
| Qwen3.8-27B | [b300](../models/qwen3827b/b300.md) | 1 / 1 | 325 | 45 | 12,463 · 20.54 ms · 92 ms | $0.165–$0.334 | 13,461 · 28.5 ms | $0.153–$0.309 | $0.0602–$0.122 | $3.00 | `estimate` | 17 |
| Qwen3.8-27B | [b200](../models/qwen3827b/b200.md) | 1 / 1 | 199 | 28 | 8,989 · 14.2 ms · 129 ms | $0.185–$0.433 | 11,082 · 21.8 ms | $0.150–$0.351 | $0.0633–$0.148 | $3.00 | `estimate` | 16 |
| Qwen3.8-27B | [gb300](../models/qwen3827b/gb300.md) | 1 / 1 | 340 | 48 | 14,411 · 28.7 ms · 123 ms | $0.347 | 14,411 · 28.7 ms | $0.347 | $0.134 | $3.00 | `estimate` | 18 |
| Kimi-K3 | [b300](../models/kimik3/b300.md) | 8 / 8 | 101 | 64 | 198.9 · 49.9 ms · 10,139 ms | $10.34–$20.95 | 321.6 · 56.2 ms | $6.39–$12.96 | $3.12–$6.32 | $15.00 | `estimate` | 18 |
| Kimi-K3 | [b200](../models/kimik3/b200.md) | 16 / 16 | 401 | 225 | 102 · 39.2 ms · 4,161 ms | $16.34–$38.78 | 437 · 58.8 ms | $3.81–$9.05 | $4.40–$10.45 | $15.00 | `estimate` | 18 |
| Kimi-K3 | [gb300](../models/kimik3/gb300.md) | 8 / 8 | 169 | 98 | 253.3 · 46.7 ms · 331 ms | $19.74 | 344.4 · 51.6 ms | $14.52 | $5.61 | $15.00 | `estimate` | 16 |
| Marlin-2B | [b300](../models/marlin2b/b300.md) | 1 / 1 | 1,934 | 142 | 93,271 · 35.7 ms ⚠️ · 17.6 ms | $0.0220–$0.0447 | 93,271 · 35.7 ms | $0.0220–$0.0447 | $0.0092–$0.0185 | n/a | `estimate` | 23 |
| Marlin-2B | [b200](../models/marlin2b/b200.md) | 1 / 1 | 1,275 | 94 | 71,040 · 3.6 ms · 21.4 ms | $0.0235–$0.0547 | 85,544 · 11.97 ms | $0.0195–$0.0455 | $0.0095–$0.0221 | n/a | `estimate` | 16 |
| Marlin-2B | [gb300](../models/marlin2b/gb300.md) | 1 / 1 | 2,016 | 148 | 22,164 · 3.64 ms ⚠️ · 19.5 ms | $0.226 ⚠️ | 23,353 · 12.19 ms | $0.214 | $0.0680 | n/a | `estimate` | 22 |

⚠️-marked cells are the ones where `pairs.json` and the per-model guide disagree — see
[§2.5](#25-where-pairsjson-and-the-per-model-guides-disagree).

**Three readings the table does not carry.** (a) B200 has a **capacity cliff at concurrency 128
on 100K-token prompts** — TTFT 48 s vs B300's 0.50 s, 29 % of B300's output — which is precisely
the agentic shape this repo cares about, so the B200 rows' apparent cost advantage on
DeepSeek-V4.1-Flash is conditional on short prompts
([b200.md §3.4b](../models/deepseek41f/b200.md)). (b) Kimi-K3 **does not fit one p6-b200 node**:
8 × B200 overflows by 16 GB/GPU before one KV page, so it needs two instances as
`PP2 × TP8 + DCP8 + EP8`, and **vLLM does not offer DCP on B200** — long-context concurrency
there needs SGLang ([b200.md](../models/kimik3/b200.md)). (c) The Marlin-2B unit that actually
bills is $/1,000 two-minute captions, where the ordering is b200 **$0.432** · h100 $0.461 ·
b300 $0.487 · h200 $0.541 · rtx6000-pro $0.585 · a100 $0.696 · mi355x $0.863 · gb300 $1.085, and
reserved tiers reorder it materially (h200 `res1y` $2.79 → **$0.378/1k**)
([README §Cross-GPU comparison](../models/marlin2b/README.md)).

### 2.3 The H200 / MI355X / RTX PRO 6000 cloud option

| Model | GPU | Min / rec GPUs | Max conc @8K | @128K | Interactive: tok/s/GPU · TPOT · TTFT | $/1M out (S1) | Max-thr: tok/s/GPU · TPOT | $/1M out (S4) | Blended $/1M | Vendor API $/1M out | Confidence | ⚠️ |
|---|---|---|---:|---:|---|---|---|---|---|---|---|---:|
| DeepSeek-V4.1-Flash | [h200](../models/deepseek41f/h200.md) | 4 / 8 | 28,457 ⚠️ | 2,132 ⚠️ | 860 · 37.2 ms · 64 ms | $1.29–$2.56 | 2,418 · 52.9 ms | $0.458–$0.909 | $0.379–$0.752 | $0.600 | `estimate` | 16 |
| DeepSeek-V4.1-Flash | [mi355x](../models/deepseek41f/mi355x.md) | 4 / 4 | 7,649 | 573 | 1,291 · 24.79 ms · 100 ms | $1.85 | 2,241 · 28.56 ms | $1.07 | $0.748 | $0.600 | `estimate` | 30 |
| DeepSeek-V4.1-Flash | [rtx6000-pro](../models/deepseek41f/rtx6000-pro.md) | 4 / 4 | 336 | 25 | 185.1 · 10.8 ms · 1,700 ms | $2.70–$6.22 | 754.8 · 84.8 ms | $0.662–$1.52 | $0.718–$1.65 | $0.600 | `measured` | 20 |
| …-Flash-NVFP4 | [h200](../models/deepseek41fnvfp4/h200.md) | 8 / 8 | 3,103 | 232 | 1,094 · 29.2 ms · 267 ms | $1.01–$2.01 | 4,461 · 57.4 ms | $0.250–$0.490 | $0.282–$0.559 | $0.600 | `estimate` | 20 |
| …-Flash-NVFP4 | [mi355x](../models/deepseek41fnvfp4/mi355x.md) | 4 / 4 | 7,371 | 552 | 1,229.9 · 26.02 ms · 102 ms | $1.94 | 2,137.3 · 29.94 ms | $1.12 | $0.771 | $0.600 | `not-runnable` | 24 |
| …-Flash-NVFP4 | [rtx6000-pro](../models/deepseek41fnvfp4/rtx6000-pro.md) | 4 / 8 | 2,517 | 186 | 285 · 50 ms · 106 ms | $1.75–$4.04 | 541 · 59.1 ms | $0.924–$2.13 | $0.481–$1.11 | $0.600 | `estimate` | 18 |
| Qwen3.8-27B | [h200](../models/qwen3827b/h200.md) | 1 / 1 | 138 | 19 | 6,953 · 24.2 ms · 374 ms | $0.159–$0.452 | 6,953 · 24.2 ms | $0.159–$0.452 | $0.0820–$0.196 | $3.00 | `estimate` | 13 |
| Qwen3.8-27B | [mi355x](../models/qwen3827b/mi355x.md) | 1 / 1 | 356 | 50 | 11,312 · 38.3 ms · 153 ms | $0.211 | 12,225 · 83.8 ms | $0.195 | $0.0800 | $3.00 | `estimate` | 19 |
| Qwen3.8-27B | [rtx6000-pro](../models/qwen3827b/rtx6000-pro.md) | 1 / 1 | 91 | 12 | 2,052 · 31.2 ms · 509 ms | $0.244–$0.561 | 2,610 · 42.5 ms | $0.192–$0.441 | $0.0870–$0.199 | $3.00 | `estimate` | 14 |
| Kimi-K3 | [h200](../models/kimik3/h200.md) | 16 / 16 | 74 | 6 | 17.36 · 23.21 ms · 2,643 ms | $63.86–$126.63 | 25 · 185 ms | $44.33–$87.91 | $19.26–$38.19 | $15.00 | `estimate` | 12 |
| Kimi-K3 | [mi355x](../models/kimik3/mi355x.md) | 8 / 8 | 161 | 93 | 74 · 49.2 ms · 1,842 ms | $32.28 | 91.33 · 78.9 ms | $26.16 | $8.49 | $15.00 | `measured` | 19 |
| Kimi-K3 | [rtx6000-pro](../models/kimik3/rtx6000-pro.md) | 16 / 16 | 12 | 11 | 13.86 · 49.6 ms · 12,160 ms | $36.08–$83.05 | 14.33 · 52.3 ms | $34.89–$80.31 | $9.91–$22.81 | $15.00 | `estimate` | 15 |
| Marlin-2B | [h200](../models/marlin2b/h200.md) | 1 / 1 | 983 | 72 | 33,565 · 7.63 ms · 39.6 ms | $0.0330–$0.0655 | 37,903 · 40.92 ms | $0.0292–$0.0580 | $0.0127–$0.0251 | n/a | `estimate` | 23 |
| Marlin-2B | [mi355x](../models/marlin2b/mi355x.md) | 1 / 1 | 2,086 | 153 | 36,232 · 8.1 ms · 36 ms | $0.0660 | 41,304 · 79.7 ms | $0.0580 | $0.0236 | n/a | `speculative` | 16 |
| Marlin-2B | [rtx6000-pro](../models/marlin2b/rtx6000-pro.md) | 1 / 1 | 646 | 47 | 13,434 · 19.06 ms · 88 ms | $0.0372–$0.0857 | 15,886 · 64.14 ms | $0.0315–$0.0724 | $0.0135–$0.0311 | n/a | `estimate` | 26 |

**When to reach for these.** H200 and RTX PRO 6000 are the **only vendor-verified pairs for
Qwen3.8-27B** — an H200 replica at $3.99/GPU-hr is the lowest-risk way to get a real number for
that model. MI355X is the one GPU where the DeepSeek-V4.1-Flash checkpoint's MXFP4+FP8 is native
end to end with the Engram tables resident, and the only `measured`-by-three-parties platform for
Kimi-K3 — but its **$8.60 OCI rate is the only published price in existence**, `low` and `high`
are the same row, and a ⚠️ $2.95 secondary quote circulates that would flip every MI355X verdict.
RTX PRO 6000 for DeepSeek-V4.1-Flash runs only on community B12X/SGLang images; **stock vLLM
collapses to 2.3–4.0 tok/s** because CUDA graphs are worth ~200× there.

### 2.4 Which of these numbers a first benchmark must replace

| Number | Where it is used | Why it is soft | Replaced by |
|---|---|---|---|
| **Every DSpark / MTP acceptance rate** | Every `$/1M output` figure for DeepSeek-V4.1-Flash, its NVFP4 sibling and Kimi-K3 | 3.51 is a **synthetic harness constant**; SGLang pins `SGLANG_SIMULATE_ACC_LEN=4.5`; the only real K3 acceptances anywhere are a community rig's **0.415 / 0.619**, and real measured spread runs 5.51 (HumanEval) → 2.99 (AIME26). Swings $/1M output by up to **3–3.5×** | Benchmark **B1** |
| **Max-concurrency, everywhere** | Every capacity and cost column for the DeepSeek pair | Replicated-vs-sharded KV is a **4–8× difference** and no engine source states it in words; `pairs.json` and the guides already disagree by exactly 4× (nvfp4/b300) and 8× (deepseek41f/h200) | Benchmark **B2** |
| **All eight Qwen3.8-27B rows** | The entire Qwen section | `confidence: estimate` on all eight; **no throughput, TPOT or TTFT measurement exists on any datacentre GPU**; vLLM's state-slot count `S` is unpublished and worth a 1.5–2× swing | Benchmark **B3** |
| **All eight Marlin-2B rows** | The entire Marlin section | No measured throughput or latency on any hardware; the `--hf-overrides` remap has never been run; Path A (23,560 tokens) vs Path B (12,288) is a **1.914× gap** decided silently at request time | Benchmark **B4** |
| **The B300 interactive operating point (c=128)** | DeepSeek-V4.1-Flash S1 cost tables | b300.md's own audit flags that decode-only throughput **peaks at batch 32** (1,798 tok/s/GPU, TPOT 4.45 ms) and *falls* at 64/128 — batch 32 dominates on both axes while both clear the SLO. **A free 1.3× if it reproduces** | Benchmark **B5** |
| **Achieved MBU on Blackwell** | Every roofline row | b300.md calibrates **0.054–0.215** from two measured anchors; gb300.md plans **0.70** — same silicon family, ~8× apart. Measured MFU is 0.0217 decode / 0.0307 prefill against METHODOLOGY's 0.25–0.40 bands | Benchmark **B6** |
| **NVFP4-vs-MXFP4 "2–5.7 % slower"** | The entire NVFP4 verdict | Arithmetic on byte counts plus a memory-bound classification — both solid, **neither measured**, on any GPU | Benchmark **B7** |
| **Kimi-K3 admission cap (110 modelled / 101 measured)** and the DCP ITL cost (~1.8×) | Every K3 capacity and $/token row | The measured cap is one number from one `--mem-fraction-static`; sub-SKUs at 262.5 GB (DGX B300) and 263 GB (OCI) drop it ~110 → ~94 | Benchmark **B8** |
| **"MXFP4 executes W4A8"** | Why B300 ≡ B200 within 1.5 % | Inferred from PTX `.kind` families plus engine labelling. If it is really W4A4, **B300's entire 1.5× FP4 advantage over B200 becomes available** and nobody has measured it for this model on any GPU | Benchmark **B9** |
| **GDN kernel dispatch on 18 of 24 Marlin layers** | Every Marlin throughput cell | The measured FlashQLA-vs-FlashInfer-vs-Triton spread at Marlin's exact geometry is **1.22×–3.31×**, and the CUDA ≥ 13 gate is silent (official vLLM images default to cu128) | Benchmark **B10** |
| **Any 32K / 128K / 1M row, for any model** | S2 / S3 columns everywhere | **No published K3 measurement at any context other than 8K/1K exists on any hardware**; no 1M-context measurement exists for DeepSeek-V4.1-Flash on any hardware; all 15 B300 rows and all 8 GB300 rows are agentic traces with 72K–335K prompts at 90–97 % cache hit | Out of the top 10 — see the runners-up below |
| **Any vision / multimodal number** | Nothing — there are none | **Every published measurement of DeepSeek-V4.1-Flash on every GPU is text-only** (usually `--language-model-only`) for a model whose pipeline tag is `image-text-to-text`; no multimodal eval exists for any 4-bit Qwen3.8-27B build; zero accuracy evaluations exist for any Marlin-2B variant | Out of the top 10 |

### 2.5 Where `pairs.json` and the per-model guides disagree

Flagged rather than resolved, per [METHODOLOGY §8](../METHODOLOGY.md); both sources cited.

*Verification log — 2026-09-19:* one former entry is now **resolved**, not flagged.
`deepseek41f / b200` S4 read 5,676 tok/s/GPU · 22.6 ms → $0.294–$0.685 in `pairs.json`
against 4,696 · 27.3 ms → $0.355–$0.828 in
[b200.md §4.1](../models/deepseek41f/b200.md); `pairs.json` has been re-cut to the document's
corrected figures and §2.2's row and §5's lowest-$/token cell carry them.

*Verification log — 2026-09-19 (final consistency pass):* a second stale row found and closed
the same way. `deepseek41fnvfp4 / mi355x` §2.3's row still carried the pair document's own
pre-audit-fix figures (1,265.2 / 2,246.3 tok/s/GPU, $1.89 / $1.06, blended $0.758) after
[mi355x.md §0/§4.2](../models/deepseek41fnvfp4/mi355x.md) corrected its decode-bytes table
(the replicated KV read had been divided by the TP degree) to 1,229.9 / 2,137.3 tok/s/GPU,
$1.94 / $1.12, blended $0.771. `pairs.json` and §2.3's row above are now re-cut to match.

| Cell | [`pairs.json`](./pairs.json) | Per-model guide | Why |
|---|---|---|---|
| **deepseek41f / h200 max conc @8K, @128K** | 28,457 / 2,132 | **3,557 / 266** ([README §Cross-GPU](../models/deepseek41f/README.md)) | Exactly 8×. `pairs.json` carries the **aggregate** node budget; the guide carries the **per-GPU binding** reading, because `num_key_value_heads: 1` means the MLA latent is **replicated on every TP rank** and does not multiply by GPU count. The guide's reading is the one consistent with its own §1.3 tensor-sharding analysis |
| **deepseek41fnvfp4 / b300 max conc @8K, @128K** | 40,801 / 3,479 | **10,199 / 869** ([README §Cross-GPU](../models/deepseek41fnvfp4/README.md)) | Exactly 4×. The pair doc's own audit log records that its §3.2 decode formula divides the KV read by `n_gpus=4` (sharded reading) while its §5.3 validated launch commands run plain `--tp 4` (replicated reading). Both figures appear in that document; `pairs.json` took the optimistic one |
| **marlin2b / b300 interactive** | 93,271 tok/s/GPU · 35.7 ms (S1 = S4 at 3,327 concurrent) | **73,808 tok/s/GPU · 3.47 ms at b=256**, $0.0278–$0.0565 ([README §Cross-GPU](../models/marlin2b/README.md)) | The pair doc concludes TPOT ≤ 50 ms never binds — KV capacity does — so it collapses S1 onto S4; the guide's table reports a b=256 operating point instead. The $/1M difference is $0.0220–$0.0447 vs $0.0278–$0.0565 |
| **marlin2b / gb300 interactive and $/1M** | 22,164 tok/s/GPU, $0.226 | **70,403 tok/s/GPU decode-only → $0.071** ([README footnote †](../models/marlin2b/README.md)) | `pairs.json` carries gb300.md's **sustained** (serial prefill + decode) rate; every other pair doc prices `$/1M output` on the **decode-only** rate. The guide re-derives the common basis and says so in its own footnote. GB300 is the most expensive card in the set either way |
| **deepseek41f / gb300 max conc** | 16,164 / 1,378 | **11,193 / 954** resident, "(16,164 offloaded)" ([README §Cross-GPU](../models/deepseek41f/README.md)) | `pairs.json` carries the **Engram-offloaded** figure; the guide's binding column is the resident one, with the offloaded figure in parentheses |
| **How close B300 and B200 are on DeepSeek-V4.1-Flash** | *"B200 matches B300 within **2.5 %** until its 180 GB KV pool runs out at concurrency 128"* (`headline`, deepseek41f/b200) | *"B300 ≡ B200 to within **1.5 %** at every concurrency B200 can hold"* ([README §Summary item 8](../models/deepseek41f/README.md)) | Same claim, two tolerances, from the same measurement set. Immaterial to any decision — both say B300 wins on **capacity**, not rate — but quote whichever document you are citing, not a blend |

*Resolved 2026-09-19 (final consistency pass):* **deepseek41f / b200 S1 $/1M out** is no
longer a disagreement — both `pairs.json` ($0.462–$1.079) and
[README §Cross-GPU](../models/deepseek41f/README.md) ($0.39–$1.08, itself mirroring the
now-corrected `b200.md` §0) previously quoted the `res1y` tier as the band's `low` end; the
README row is recut to **$0.46–$1.08** and the row above is removed.

---

## 3. Prioritized benchmark plan: the 10 experiments that most reduce uncertainty

Ordered by how much money and how many ⚠️ items each one moves. Every experiment runs on the
repo's own 8 × B300 node unless stated. Pin the engine SHA on every run: vLLM moved the Kimi-K3
pair **2.2–2.8× in under a month**, and *any number without an engine SHA is worthless*.

| # | Model | GPU | Engine | Config | Metric | ⚠️ items it resolves |
|---|---|---|---|---|---|---|
| **B1** | DeepSeek-V4.1-Flash | 4 × B300 | vLLM nightly (pinned digest) | TP4, FP4 KV, DSpark γ=5 on / off, **your own production traffic** | `vllm:spec_decode_num_{accepted,draft}_tokens_total`, per-position acceptance, resulting $/1M out | The single biggest lever in the tree: [deepseek41f OQ#1](../models/deepseek41f/README.md), [kimik3 OQ A1](../models/kimik3/README.md), [nvfp4 benchmark #4](../models/deepseek41fnvfp4/README.md). 3.51 is a synthetic constant; the only real measurement of this checkpoint anywhere is a community **3.57** on different silicon with a 1.88–5.92 spread by workload. Kimi-K3's entire DSpark cost case rides on SGLang's pinned `SGLANG_SIMULATE_ACC_LEN=4.5` |
| **B2** | DeepSeek-V4.1-Flash (+ NVFP4) | 4 × and 8 × B300 | vLLM nightly | Boot at TP4 and TP8, fixed `--max-model-len`; read the KV pool | `gpu_kv_cache_usage_pct`, `kv_cache_pool_tokens`; TP8 pool ÷ TP4 pool | [deepseek41f OQ#4](../models/deepseek41f/README.md) (replicated vs sharded KV, **4–8×**), [nvfp4 OQ#3](../models/deepseek41fnvfp4/README.md), and both concurrency disagreements in [§2.5](#25-where-pairsjson-and-the-per-model-guides-disagree). Also settles [qwen OQ#8](../models/qwen3827b/README.md) (KV-head replication at TP > 4) if repeated on that model |
| **B3** | Qwen3.8-27B | 1 × B300 | SGLang (`bench_serving`) **and** vLLM | `--random-input-len 4096 --random-output-len 512` at concurrency {1, 8, 32, 64, 128, 256, 396}; read the mamba-cache allocation out of the vLLM boot log and divide by 78,446,592; read the selected attention and GDN backends out of the server log | tok/s/GPU, TPOT, TTFT, back-solved MBU; vLLM's `S`; which backend actually ran | **The parent of every other Qwen item.** [qwen OQ#1](../models/qwen3827b/README.md) (no measurement exists on any datacentre GPU), **#2** (`S` unpublished — 1.5–2× swing in every concurrency and cost figure), **#3** (MBU on a hybrid-GDN model unmeasured everywhere), **#18** (`trtllm_mha` hangs on SM103, and its documented workaround is illegal for hybrid-GDN) |
| **B4** | Marlin-2B | 1 × B300 | vLLM 0.29.0 + `--hf-overrides`, CUDA ≥ 13 image | Load the checkpoint; generate 10 captions; diff against `transformers` + `trust_remote_code=True` on the same clips; then count the prefill tokens vLLM produces for one fixed 2-minute clip | Load success, caption/timestamp diff, **prefill token count (23,560 = Path A vs 12,288 = Path B)** | [marlin OQ#1](../models/marlin2b/README.md) (the remap has never been run, on any GPU), **#2** (Path A vs Path B, a 1.914× gap and *"the largest single source of error in the entire analysis"* — a silent quality regression, not an error), **#3** (does serving preserve caption and grounding quality at all). Everything else about Marlin-2B is downstream of this |
| **B5** | DeepSeek-V4.1-Flash | 4 × B300 | vLLM nightly | TP4 at batch {32, 64, 128}, at your SLO, with prefix caching instrumented | decode-only tok/s/GPU, TPOT, `server_gpu_cache_hit_rate` vs concurrency | [deepseek41f OQ#9](../models/deepseek41f/README.md): batch 32 measured **1,798 tok/s/GPU at 4.45 ms TPOT** and *falls* at 64/128, while the cost tables are built on c=128 — **worth ~1.3× for free**. Same run finds the prefix-cache eviction knee: on H100's identical harness, output collapsed **4.6×** when the pool could no longer hold retained prefixes ([OQ#5 in the benchmark list](../models/deepseek41f/README.md)) |
| **B6** | DeepSeek-V4.1-Flash | 4 × B300 | vLLM nightly | Sweep batch {1, 32, 128, 256}; back out MBU from the byte model | achieved MBU / MFU per batch | [nvfp4 OQ#2](../models/deepseek41fnvfp4/README.md): b300.md calibrates MBU **0.054–0.215**, gb300.md plans **0.70** — same silicon family, ~8× apart, and the tree has no measured B300 anchor for the NVFP4 checkpoint. Also [deepseek41f OQ#3](../models/deepseek41f/README.md): the unexplained 5× roofline-vs-measurement gap at batch 128 (MFU 0.0217 decode against METHODOLOGY's 0.25–0.40) |
| **B7** | DeepSeek-V4.1-Flash **vs** -NVFP4 | 4 × B300, one TP4 replica | Same image, same flags, two containers | Paired A/B, identical traffic | tok/s/GPU, TPOT, bytes/step, GSM8K | [nvfp4 OQ#1](../models/deepseek41fnvfp4/README.md) + [deepseek41f OQ#10](../models/deepseek41f/README.md). *"The single highest-value experiment in the whole tree: nobody anywhere has run it, and it decides which checkpoint to deploy. Two containers, one tray, one afternoon."* NVIDIA's card contains zero throughput figures. Run a **correctness smoke test first** — NVFP4 backbone + MXFP4 DSpark experts in one process is validated by nobody, and its failure mode is silent (~0 % acceptance, garbage not a crash) |
| **B8** | Kimi-K3 | 8 × B300 | SGLang ≥ 0.5.17 (verified cell) or vLLM ≥ 0.27.1 | TP8 + DCP8 on/off, at your `--mem-fraction-static`; FP8 KV | `max_running_requests` (admission cap), ITL cost of DCP, tok/s/GPU; watch for [sglang#34260](https://github.com/sgl-project/sglang/issues/34260) | [kimik3 §What to benchmark first items 3 and 7](../models/kimik3/README.md): modelled **110** vs measured **101** at 8K, **68** under DSPARK; DCP is the single biggest capacity lever (+72 % ceiling at ~1.8× ITL, ~7.9× logical KV) and is ⚠️ force-incompatible with `--enable-symm-mem` and **silently dropped** by L3 HiCache. Confirm capacity with `nvidia-smi` first — a 262.5 GB or 263 GB sub-SKU drops the cap ~110 → ~94 |
| **B9** | DeepSeek-V4.1-Flash | 4 × B300 | vLLM nightly + SGLang | `cuobjdump -sass` on the expert GEMM; A/B SGLang's `--enable-w4a4-mxfp4-megamoe` | Which MMA `.kind` the expert GEMM emits; tok/s/GPU delta | [deepseek41f OQ#2 and #11](../models/deepseek41f/README.md). If the experts really run W4A8, **B300's entire 1.5× FP4 advantage over B200 is unavailable on the default path** — which the measured B300 ≡ B200 result corroborates. If W4A4 works, the 13,500 TFLOPS line opens and **nobody has measured it on any GPU for this model** |
| **B10** | Marlin-2B | 1 × B300 (and 1 × H200 for the control) | vLLM + `--hf-overrides` | Profile one prefill and read off which GDN kernel ran; then two runs: `--kv-cache-dtype auto --block-size 128` vs `fp8_e4m3` | Which of FlashQLA / FlashInfer / Triton dispatched; **videos/hour end-to-end and TTFT**, not just TPOT | [marlin OQ#7](../models/marlin2b/README.md) (**18 of 24 layers**, measured spread **1.22×–3.31×** at Marlin's exact geometry — the largest unquantified term in every throughput table), **#10 / #11** (FP8 KV forces the FA4 hd256 kernel back to FA2, and FP8 KV separately regresses prefill **×1.6** on hd-256 shapes — on a prefill-bound fleet it could *lower* sustained throughput), **#15** (the CUDA ≥ 13 gate is silent and official vLLM images default to cu128), **#8** (the GB200-vs-H200 anti-result: **1.00–1.16× of Hopper, one row at 0.69×**) |

**Runners-up, deliberately outside the top 10** — each is real and each is cheaper to defer:
a 32K / 128K sweep for Kimi-K3 and DeepSeek-V4.1-Flash (S2/S3 is pure extrapolation *everywhere
in this repo*); the Qwen MTP γ sweep and `--use-replayssm` A/B (targets 24–41 % of the decode
step, unpublished on any GPU); `--mamba-ssm-dtype bfloat16` A/B on throughput **and** a full
1,319-question GSM8K (SGLang calls bf16 state "an accuracy gate"); FP8-KV accuracy under long
context, where every one of DeepSeek-V4.1-Flash's 43 layers carries a 128-token SWA component and
`quantization-formats.md` warns that sliding-window layers are more KV-quant-sensitive; the
torchcodec-vs-NVDEC 240-frame decode test for Marlin (vLLM measured *"more than double the
throughput"* with NVDEC on a **16-frame** workload, and **B300 has no published NVDEC row at
all**); and vision for anything, which is unmeasured on every GPU in the roster.

---

## 4. Risks and mitigations

| # | Risk | Evidence | Mitigation |
|---|---|---|---|
| 1 | **Nothing is in a numbered release.** DeepSeek-V4.1-Flash's `min_vllm_version: 0.30.0` **does not exist** (latest is 0.29.0); SGLang ships a preview image; the measured GB300 runs used **three different images in six days** | [deepseek41f OQ#7](../models/deepseek41f/README.md), [nvfp4 OQ#23](../models/deepseek41fnvfp4/README.md) | Pin a nightly **digest**, not a tag. Treat every number in this tree as having a shelf life of weeks; re-measure quarterly and record the engine SHA with every result |
| 2 | **vLLM has no Blackwell CI at all** — its CUDA CI covers L4 and H100 only. `sm_100a` binaries do not load on B300; FlashInfer is *"architecture-gated but not yet signoff-qualified"* on SM103a | [kimik3 OQ#12](../models/kimik3/README.md), [marlin OQ (b300 §6 #8)](../models/marlin2b/README.md) | `cuobjdump --list-elf <lib>.so \| grep sm_103` before trusting any wheel. This is the **#1 cause of "works on B200, dies on B300"** |
| 3 | **Engine support gaps by model**: TensorRT-LLM has **no V4.1 entry at all** (and every MLPerf GB300 record is a TRT-LLM number); Kimi-K3 on A100 is `not-runnable` on every engine; `MarlinForConditionalGeneration` is in **no** engine registry; NVFP4-DeepSeek on MI355X and A100 is `not-runnable` | [`pairs.json`](./pairs.json) `supported_now`; [nvfp4 OQ#24](../models/deepseek41fnvfp4/README.md) | Do not plan capacity on an unsupported pair. For Marlin-2B, keep `transformers ≥ 5.7.0` + `trust_remote_code=True` as the fallback path — it is the only first-class one — and run **B4** before anything else |
| 4 | **Live correctness bugs on paths this repo would use.** vLLM **#51326** corrupts output on 8 × H100 at TP8 + expert parallelism (every working H100 row runs `decode_ep: 1`); **#49070** gives garbage at conc 1 and an illegal memory access at conc ≈ 8 on the Marlin FP4 MoE path; **#54035** (open) gives an FP8-KV + FA3 Hopper logprob mismatch, max Δ ≈ 0.56; [sglang#34260](https://github.com/sgl-project/sglang/issues/34260) crashes ~twice daily on 8 × B300 with `TP8 + DCP8 + EP8` + hierarchical caching, **closed with no workaround and no fix version** | [deepseek41f OQ#19](../models/deepseek41f/README.md), [marlin OQ#11](../models/marlin2b/README.md), [kimik3 OQ#13](../models/kimik3/README.md) | Run EP=1 for DeepSeek-V4.1-Flash on the node (wide-EP is worthless there anyway); use **DCP8 without EP8** for Kimi-K3; avoid FP8 KV + FA3 on Hopper where logprob accuracy matters — which it does for Marlin's timestamp grounding |
| 5 | **Gated and licence-restricted models.** Marlin-2B's `config.json` and `processor_config.json` are **gated on HF** (and the gated `processor_config.json` *confirms* the Path A / Path B conflict rather than resolving it). Kimi K3 License **§2** requires a separate Moonshot agreement for any MaaS business above **US$20 M aggregate revenue over 12 months**; **§3** requires UI attribution above 100 M MAU or $20 M monthly revenue | [marlin OQ#2](../models/marlin2b/README.md), [kimik3 §Licence gate](../models/kimik3/README.md) | Secure gate access before scheduling **B4**. **Internal use of Kimi-K3 is exempt; token resale is gated** — get the agreement in motion before any external K3 endpoint ships |
| 6 | **NVFP4 accuracy is a wash and its packaging is sloppy.** NVIDIA's own table is 4 wins / 2 losses within ±1.5 points; calibration was **1,024 samples at sequence length 512** for a 1,048,576-token-context model, and **AA-LCR — the one long-context benchmark — is the one NVFP4 loses**. `model.safetensors.index.json` still carries the *base* checkpoint's `metadata.total_size`, so a loader sizing from it under-allocates by 16.99 GB. The shipped `config.json` contradicts itself on activation scaling (`input_activations.dynamic = false` vs a sibling `activation_scheme: "dynamic"`) — an accuracy question on a W4A4 checkpoint | [nvfp4 OQ#16, #17, #19](../models/deepseek41fnvfp4/README.md) | **Run the base MXFP4 checkpoint.** All eight pair documents reach this independently. If NVFP4 is mandated, run **B7** with the correctness smoke test first, and evaluate on a long-context benchmark, not GSM8K |
| 7 | **Quantisation accuracy is unevaluated where it matters most.** No multimodal eval exists for any 4-bit Qwen3.8-27B build (AWQ calibration at `seq_len 512` for a 262 K-context vision model); **zero accuracy evaluations exist for any Marlin-2B variant, quantised or not** (`model-index: null`, no third-party reproduction, and ActivityNet/Charades/TimeLens appear in both the training-source and evaluation lists with no stated contamination control); Kimi-K3 has **no unquantised reference at all** (MXFP4 is QAT'd from the SFT stage — *"there is no BF16 Kimi-K3 checkpoint"*) | [qwen OQ#24](../models/qwen3827b/README.md), [marlin OQ#33](../models/marlin2b/README.md), [kimik3 OQ#18](../models/kimik3/README.md) | Run MMMU-Pro and a long-context retrieval task on the exact checkpoint before shipping vision traffic; run TimeLens-Bench mIoU before and after **any** Marlin quantisation — you would otherwise have no baseline to regress against |
| 8 | **ROCm maturity.** SGLang's gfx950 DeepSeek PR is **closed**; Dynamo has **no ROCm support**, so PD disaggregation is unavailable; DSpark on ROCm crashes out of the box in SGLang (`NameError: top_k_renorm_prob`) and speculation is worth 1.7–2.2× on MI355X; adaptive verification is refused on ROCm, and without DSpark the DeepSeek TPOT ≤ 50 ms ceiling falls to **batch 39**; SGLang forces `--disable-radix-cache` on its DeepSeek MI350X recipes and forfeits prefix caching entirely; `VLLM_USE_BREAKABLE_CUDAGRAPH=1` is mandatory or capture dies; **no gfx950 DSA indexer kernel is confirmed**, which decides MI355X's 1M-context viability | [deepseek41f §Optimization impact](../models/deepseek41f/README.md), [kimik3 OQ#11](../models/kimik3/README.md), [marlin OQ#18](../models/marlin2b/README.md) | Treat MI355X as an **evaluation and second-source** platform, not a primary. Its one genuine advantage — the DeepSeek checkpoint's MXFP4+FP8 native end to end with Engram resident at TP4 — is real; its economics rest on a single published price |
| 9 | **Quota and procurement.** AWS publishes **no standard 1y/3y RI for `p5en`, `p6-b200`, `p6-b300` or `p6e-gb200`** — Blackwell is Capacity-Blocks-first, and Capacity Block prices were **repriced upward effective 2026-07-01**. `p6e-gb200` is Capacity-Block-only, confined to the **US East (Dallas) Local Zone**, sold only as 36- or 72-accelerator UltraServers, and **no `p6e-gb300` SKU exists**. `p6-b300` on-demand is **$17.802/GPU-hr — 2.4× Hyperstack's $7.40** and above the `high` tier every table here uses | [cloud-pricing.md §5](../cross-cutting/cloud-pricing.md), [kimik3 §AWS p6 family](../models/kimik3/README.md) | **Use p6 when you already hold the capacity reservation; otherwise the economics live at the `low` tier.** Negotiate against spot ($5.591 for p6-b300, $5.261 for p6-b200) and Capacity Blocks ($14.04 / $12.355). At $17.802 a B300 is 15.1 GB of HBM per $/hr — *below* H100's |
| 10 | **Single-price GPUs.** GB300 and MI355X each rest on **exactly one published rate** (OCI $18.00 and $8.60); `low`, `high` and `res1y` are the same number. GB300's $18.00-vs-$2.31 anchor spread is **7.8×, larger than any technical uncertainty in that document**; a ⚠️ $2.95 MI355X quote circulates from a secondary source, at which MI355X becomes the cheapest row in the Qwen survey | [qwen OQ#5](../models/qwen3827b/README.md), [kimik3 OQ#6](../models/kimik3/README.md) | Get signed quotes (TensorWave / Crusoe for MI355X; CoreWeave / Together / Nebius for GB300) before either appears in a plan. **The MI355X verdict is a procurement question, not a silicon question** |
| 11 | **Build-vs-buy inverts with your cache-hit rate.** A cache hit costs you ~10 % of a prefill but costs the vendor 90 % of its input revenue, so break-even falls ~2.0× from 0 % to 90 % hit — *every time*. OpenRouter measures **92 %** on real Kimi-K3 traffic. At 90 % hit this eliminates all of H100, all of H200, GB300 at OCI list, B300 at OCI list, MI355X at OCI list and RTX PRO 6000 without speculation | [kimik3 §Cost vs vendor API](../models/kimik3/README.md), [deepseek41f §Cost vs vendor API](../models/deepseek41f/README.md) | Measure your own hit rate **before** sizing (benchmark **B5** gives it for free). For output-heavy `reasoning_effort=max` traffic the sign flips: self-hosting wins because output is 5–50× the price of cached input while costing the same bytes to produce |
| 12 | **Operational gotchas that bite on day one.** `mlx5dv_reg_dmabuf_mr` errno 524 → set `NCCL_DMABUF_ENABLE=0`; setting any one of the three Blackwell attention knobs *"cancels the auto-resolution for the others"*; DSPARK requires `pp_size == 1`; an unset `--max-running-requests` resets to 48 under spec; `pip install flash-linear-attention` is a hard dependency for the KDA layers; Kimi-K3 *"occasionally emits a tool-call format its own parser doesn't expect"*; **video and audio input are rejected** by K3's open-source processor despite the card's Video-MME 90.0; Qwen3.8-27B needs 3–10 minutes to READY (SGLang measures ~6.5 min to load 18 BF16 shards); vLLM and SGLang use **different tool-call parsers for the same chat template** (`qwen3_xml` vs `qwen3_coder`) — a silent failure where calls land in `message.content` | [kimik3 OQ#25](../models/kimik3/README.md), [qwen §Recommendation](../models/qwen3827b/README.md) | Bake these into the launch scripts, not the runbook. Schema-validate and retry tool calls. Budget 10 minutes before calling a boot hung |

---

## 5. Decision table — if the goal is X, pick Y

All picks are **within the model**; the cross-model choice is a product decision, not a serving
one. Cells give the shape, the headline number and its confidence. "On the node" means the
repo's own 8 × B300.

| Goal → | **Lowest interactive latency** | **Lowest $/token** | **Longest context** | **Single-node simplicity** |
|---|---|---|---|---|
| **[DeepSeek-V4.1-Flash](../models/deepseek41f/README.md)** | **B300 TP4** — 23.31 ms TPOT, 129 ms TTFT, 1,373 tok/s/GPU, `measured`. B200 TP8 is nominally faster (22.2 ms, 3,604 tok/s/GPU) but `estimate`, and it **collapses at concurrency 128 on 100K prompts** (TTFT 48 s) | **B300 as 4 × TP2 replicas** — $0.774–$1.57/1M out, 2,656 tok/s/GPU `measured`. B200 TP8 is cheaper on paper ($0.355–$0.828) but rests on a batch-1024 extrapolation past its measured capacity cliff. **Neither beats DeepSeek's own $0.60 API on output at any rented rate** — only owned silicon at $1.42–$2.31/GPU-hr does | **GB300** — native FP4 KV at 890 B/token (1M ctx = 890 MiB/seq), Grace C2C at 900 GB/s makes the Engram offload genuinely free, 1,378 concurrent @128K. But $5.64/1M out at the only published rate, and **no 1M-context measurement exists on any hardware** | **B300** — min **2** GPUs (TP2 + Engram offload), 4 recommended; 2,144 GB holds the checkpoint four times over. Never cross nodes: wide-EP *"runs badly"* on an 8-GPU domain and PD is *"a thousand-GPU problem"* |
| **[…-Flash-NVFP4](../models/deepseek41fnvfp4/README.md)** | **Don't** — use the base. Forced: GB300 TP4, 15.87 ms TPOT, 4,034 tok/s/GPU, `estimate`, the only vendor-validated pair | **Don't** — use the base. Forced: B200 TP4, $0.565–$1.32 S1 / $0.0480–$0.112 at the KV ceiling (a ceiling **14–200× beyond any published concurrency**; NVIDIA's own commands cap at 16–32) | **Don't** — use the base. It is **+16.99 GB** and its long-context evidence is the one benchmark it loses (AA-LCR), calibrated at `seq_len 512` | **Don't** — use the base. Forced: 2 × TP4/EP4 replicas per B300 node; **never TP2 at 268 GB** |
| **[Qwen3.8-27B](../models/qwen3827b/README.md)** | **B200 TP1** — 14.2 ms TPOT at bs 128, 8,989 tok/s/GPU. On the node, **B300 TP1** at 20.54 ms / 12,463 tok/s/GPU. Both `estimate`; **H200 or RTX PRO 6000 are the only vendor-verified pairs** and the lowest-risk way to get a real number | **B300 TP1 × 8 replicas** — blended **$0.0602–$0.122**, cheaper blended than B200's $0.0633–$0.148 despite B200 leading on S4 output alone ($0.150 vs $0.153). Every GPU clears break-even against Qwen Cloud's $3.00 at ≤ 21 % utilisation, so this is **not** where the decision lives | **B300 or GB300** — 1M needs NVFP4 weights + FP8 KV and Blackwell-Ultra capacity; B300 holds 6 concurrent 1M seqs per GPU at TP1 (27 at TP8), GB300 6, MI355X 6, and **no GPU in the survey holds even one at BF16 weights with BF16 KV**. Only Novita and Alibaba serve the full 1M window via API | **Any card, 1 GPU, TP1** — it fits one GPU in every precision on every card surveyed. On the node: 8 independent TP1 replicas behind a router, 8 failure domains, zero NVLink traffic in the decode loop |
| **[Kimi-K3](../models/kimik3/README.md)** | **GB300 TP8+DCP8** — 46.7 ms TPOT, 253.3 tok/s/GPU, but $19.74/1M out at the sole published rate. On the node: **B300 TP8+DCP8**, 49.9 ms — *just* inside the SLO. **H100 and A100 cannot reach 50 ms at any concurrency** | **B300 at max throughput** — $6.39–$12.96/1M out, **$3.12–$6.32 blended**, against Moonshot's $15.00. B200 + DSpark at the KV ceiling models **$2.63/1M out** — the lowest figure in the set, and modelled, not measured. At a 90 % cache hit only **B300 on neocloud pricing and B200 with DSpark survive** | **B200 16-GPU with DCP8** — 225 concurrent @128K, the difference between serving 128 K and not (29 → 225). ⚠️ **vLLM does not offer DCP on B200**, so that needs SGLang. **Serve at 300 K, not 1 M**: Moonshot's own card reports BrowseComp **91.2 with compaction at 300 K vs 90.4 at the full window**, and 1 M costs 20 concurrent per node against 124 at 32 K | **B300** — 8 GPUs = one HGX node = one replica, 195.1 GB/GPU, and **the only `verified: true` K3 cells anywhere**. MI355X also fits 8 GPUs on one UBB 2.0 baseboard and is `measured` by three parties. Everything else needs 2–4 nodes |
| **[Marlin-2B](../models/marlin2b/README.md)** | **B200 TP1** — **3.60 ms TPOT** at conc 256, 71,040 tok/s/GPU, and one of only three cards that get FA4's dedicated `head_dim 256` kernel. ⚠️ but the measured GDN kernels (18 of 24 layers) hit just **1.00–1.16× of H200**, so Blackwell may buy nothing on 75 % of this model | **B200 $0.0195/1M out** (S4) / **$0.432 per 1,000 two-minute captions**; H100 within 7 % at $0.461 with a measured 8 × H100 reference deployment for this exact workload. **Reserved H200 at $2.79/GPU-hr → $0.378/1k videos beats every on-demand row.** Worst: GB300 at $1.085/1k | **Capped at 262,144 tokens** (`rope_type: default`); there is no 1M path. Highest KV capacity is MI355X (2,086 @8K / 153 @128K) then GB300 (2,016 / 148); on the node B300 gives 1,934 / 142 and **753 concurrent 2-minute videos per GPU**. The **240-frame cap** means a 10-minute clip costs what a 2-minute one does, at 0.40 effective fps | **Any card, 1 GPU, TP1, replicas above that.** On the node: 8 TP1 replicas = **6,024 concurrent videos**, ~121,500 captions/hour, with the ConnectX-8 fabric, the NVLink domain and every FP4 core 100 % idle. *"Run it on B300 only because the node is already there; never buy one for it"* |

**One line that cuts across every row:** for DeepSeek-V4.1-Flash and Kimi-K3 the vendor API is
the thing to beat and rented GPUs do not beat it on output price; for Qwen3.8-27B and Marlin-2B
self-hosting wins so decisively (≤ 21 % against Qwen Cloud's $3.00/1M, and **7.9 %** for a single $3.20/hr H100 against a $0.30/1M API) that **the GPU choice
is a capability and risk question, not a cost one**. In all five cases the top of
[§3](#3-prioritized-benchmark-plan-the-10-experiments-that-most-reduce-uncertainty) — acceptance
rate, KV replication, and whether the thing loads at all — moves the answer more than any
hardware swap.

---

## Sources

Every number above is carried from one of these and linked at the point of use; nothing is
re-derived here beyond reading [`pairs.json`](./pairs.json) with `python3`.

- [`research/METHODOLOGY.md`](../METHODOLOGY.md) — §1 bytes/param, §2 KV and fixed state, §3 fit, §4 roofline and MBU/MFU bands, §6 cost model and scenarios S1–S4, §8 pinned GPU / model / price inputs
- [`research/matrix/pairs.json`](./pairs.json) — the 40-pair machine-readable summary: `fits`, `supported_now`, `min_gpus`, `recommended_gpus`, `parallelism`, `weight_format_used`, `attention_kernel`, `max_concurrency_8k/128k`, `interactive`, `max_throughput`, `blended_cost_per_1m_usd_low/high`, `vendor_api_output_usd_per_1m`, `price_per_gpu_hour_low/high`, `confidence`, `unverified_count`, `audit`
- Per-model GPU guides: [deepseek41f](../models/deepseek41f/README.md) · [deepseek41fnvfp4](../models/deepseek41fnvfp4/README.md) · [qwen3827b](../models/qwen3827b/README.md) · [kimik3](../models/kimik3/README.md) · [marlin2b](../models/marlin2b/README.md), and through them the 40 pair documents and five `architecture.md` files
- GPU foundation docs: [b300](../gpus/b300.md) · [b200](../gpus/b200.md) · [gb300](../gpus/gb300.md) · [h200](../gpus/h200.md) · [h100](../gpus/h100.md) · [a100](../gpus/a100.md) · [rtx6000-pro](../gpus/rtx6000-pro.md) · [mi355x](../gpus/mi355x.md)
- Cross-cutting: [`cloud-pricing.md`](../cross-cutting/cloud-pricing.md) (§5 AWS p6 / Capacity Blocks, §5.14 planning prices — the only price source any document here uses) · [`flash-attention.md`](../cross-cutting/flash-attention.md) · [`quantization-formats.md`](../cross-cutting/quantization-formats.md) · [`inference-engines.md`](../cross-cutting/inference-engines.md) · [`serving-optimizations.md`](../cross-cutting/serving-optimizations.md) · [`inferencex-api.md`](../cross-cutting/inferencex-api.md)
