# nvidia/DeepSeek-V4.1-Flash-NVFP4 on NVIDIA RTX PRO 6000 Blackwell Server Edition 96GB GDDR7 (PCIe only, no NVLink; sm_120)

Research date: **2026-09-19**. Formulas, markers, standard scenarios S1–S4 and the
blended definition follow [`research/METHODOLOGY.md`](../../METHODOLOGY.md) verbatim.
Model inputs from [`architecture.md`](./architecture.md); GPU inputs from
[`gpus/rtx6000-pro.md`](../../gpus/rtx6000-pro.md); kernels from
[`cross-cutting/flash-attention.md`](../../cross-cutting/flash-attention.md) and
[`cross-cutting/quantization-formats.md`](../../cross-cutting/quantization-formats.md);
engine versions from [`cross-cutting/inference-engines.md`](../../cross-cutting/inference-engines.md);
prices from [`cross-cutting/cloud-pricing.md`](../../cross-cutting/cloud-pricing.md) §5.10.
The base-checkpoint pair is [`models/deepseek41f/rtx6000-pro.md`](../deepseek41f/rtx6000-pro.md)
and is the comparator this whole document is written against.

> **The question this document exists to answer.** METHODOLOGY §8 states it for us:
> *"DeepSeek-V4.1-Flash experts are MXFP4 (not NVFP4); the NVFP4 build is larger
> (527.27 GB vs 510.29 GB) and has no published speedup — if your pair is the NVFP4
> build, the central question is whether it beats the base checkpoint on this GPU at
> all."*
> **Answer: no, and on this GPU specifically it is the worst of the eight GPUs in
> this repo to reach for it on.** It costs +16.99 GB of weights, which on the
> 4-GPU shape that both measured base-checkpoint deployments use consumes **77 % of
> the remaining KV budget** (§1), and it trades the base checkpoint's *native*
> MXFP4 W4A8 fused-MoE path on sm_120 for a path that is either a Marlin W4A16
> dequant fallback or an opt-in, unvalidated B12X NVFP4 route (§2). Every §3 and §4
> number below is therefore an estimate of a configuration nobody should choose;
> it is computed in full because the brief asks for it and because the *size* of
> the penalty is the finding.

> **Which silicon.** Every published measurement anywhere near this pair was taken
> on **Workstation Edition or Max-Q Workstation Edition** (1,792 GB/s, 2,015.2
> TFLOPS dense FP4). This document targets the **Server Edition** — **1,597 GB/s**,
> **1,920 TFLOPS dense FP4** ([gpus/rtx6000-pro.md §1, §2, §3c](../../gpus/rtx6000-pro.md)).
> Decode is bandwidth-bound here, so measured figures are scaled by
> **1,597/1,792 = 0.8912** (−10.9 %) and labelled `est.`; prefill by
> **1,920/2,015.2 = 0.9528** (−4.7 %).
> **Disagreement to record (METHODOLOGY §8).** `gpus/rtx6000-pro.md` §3c derives
> Server-Edition dense **480 BF16 / 960 FP8 / 1,920 FP4** from the implied 2,494 MHz
> boost; [`flash-attention.md` §2](../../cross-cutting/flash-attention.md) instead
> halves NVIDIA's printed sparse figures to **500 / 1,000 / 2,000**; METHODOLOGY §8
> pins "≈ 2,000 dense (4,000 sparse)". The spread is 4 %. This document plans with
> **1,920** so its prefill-MFU figures are directly comparable with the base-pair
> document, and states the 2,000 alternative here rather than picking silently.

---

## 0. Verdict

1. **Runnable in principle, validated nowhere.** No engine, vendor or community stack
   has been shown serving `nvidia/DeepSeek-V4.1-Flash-NVFP4` on sm_120 as of
   2026-09-19 — NVIDIA's card scopes it to "NVIDIA Blackwell" but tested only GB300/TP4
   on vLLM `vllm/vllm-openai:deepseekv41-flash-0909` and SGLang `lmsysorg/sglang:dev-cu13-dsv41`
   ([architecture.md §8.2–8.3](./architecture.md)). The nearest thing that exists is the
   **V4-Flash** NVFP4 sibling on 2× RTX PRO 6000, which runs only with hand-written
   vLLM patches ([hikarioyama](https://github.com/hikarioyama/dsv4-flash-nvfp4-sm120),
   [Infatoshi](https://github.com/Infatoshi/dsv4-flash-2x-rtxpro6000s)).
2. **Minimum 4 GPUs, recommended 8**, TP (+EP), replicated KV, PCIe Gen5, Engram tables
   in pinned host RAM. 4 GPUs fit only with Engram offloaded and leave **1.27 GB of KV
   per GPU at `--gpu-memory-utilization 0.90`** (§1) — against the base checkpoint's
   5.52 GB. 8 GPUs with Engram offloaded is the first genuinely comfortable shape.
3. **Weight format actually executed: NVFP4 experts *dequantised* to W4A16 via Marlin
   by default** ([quantization-formats.md §9.6 cell e](../../cross-cutting/quantization-formats.md):
   *"assume dequant until measured"*), with an opt-in native `--moe-backend flashinfer_b12x`
   route. MTP/DSpark experts remain **MXFP4** and the NVFP4/MXFP4 mix is a measured,
   reproduced failure mode on this exact GPU (§2, §6). KV runs **FP8 (1,650 B/token)**,
   not the shipped 890 B/token FP4 cache.
4. **Interactive (S1, TPOT ≤ 50 ms): $1.75–$4.04 per 1M output tokens** at the
   recommended 8-GPU/C114 point, **$3.06–$7.04** at the 4-GPU/C32 point, and
   **$5.22–$12.02** on the 4-GPU Marlin path; blended 75/25 **$0.48–$1.11**,
   **$0.81–$1.86** and **$1.35–$3.10** respectively.
5. **Max throughput (S4, C256, no SLO), 8 GPU: $0.92–$2.13 per 1M output** `est.`
   (kappa-lo; kappa-hi is $0.62–$1.42)
   **Confidence: `estimate`.** Zero measurements of this checkpoint on this GPU exist;
   the calibration κ is borrowed from the *base* checkpoint on the *same* GPU. Against
   DeepSeek's own $0.60/M output and $0.2074/M blended, this pair **never breaks even
   at any interactive operating point at any published rental price**.

---

## 1. Fit

Fit is hardware, so this section is exact regardless of §2's software verdict.
Per METHODOLOGY §3: `usable_hbm = 96e9 × 0.90 = 86.40e9 B = 80.47 GiB`. The card is
96 **GB** (89.41 GiB), not 96 GiB. Topology set for a PCIe card with no NVLink:
{1, 2, 4, 8, 16, …}; `gpus/rtx6000-pro.md` §4 states *"multi-node LLM serving is not a
supported design point for this card"*, so 16 is shown for completeness only.

### 1.1 Weights, and the +16.99 GB that decides everything

```
checkpoint      527,273,322,840 B = 527.27 GB = 491.06 GiB
base checkpoint 510,286,023,000 B = 510.29 GB  delta +16,987,299,840 B = +16.99 GB (+3.33%)
Engram tables   202,758,032,400 B = 202.76 GB = 188.83 GiB
compute weights 324,515,290,440 B = 324.52 GB = 302.23 GiB
base compute    307,527,990,600 B = 307.53 GB
usable 0.90 86.40 GB = 80.47 GiB | 0.95 91.20 GB = 84.94 GiB

min GPUs, Engram in HBM      : 6.399  -> topology step 8
min GPUs, Engram host/NVMe   : 3.938  -> topology step 4
base ckpt,  Engram host/NVMe : 3.732  -> topology step 4
base ckpt,  Engram in HBM    : 6.193  -> topology step 8
```

The 527.27 GB figure is the **tensor payload** measured from the 48 shard headers
([architecture.md §1.1](./architecture.md)). **Do not size from
`model.safetensors.index.json`** — it still carries the *base* checkpoint's
`total_size = 510,286,023,000` and will under-allocate by 17 GB
([architecture.md §1.1](./architecture.md), verification log #4). METHODOLOGY §8 pins
527.27 GB and this document agrees; there is no disagreement to record on the weight side.

### 1.2 Fit table over the allowed GPU counts

`activation_ws` is `est.` at the top of METHODOLOGY §3's 2–6 GB band (4 GB at ≤ 4 GPUs,
5 GB at ≥ 8) because mHC carries four parallel residual copies
([architecture.md §2](./architecture.md), `hc_mult: 4`).

```
 n Engram            W/GPU GB      GiB  AWS  KV/GPU@.90     @.95   agg@.90
 1 HBM-resident        527.27   491.06    4     -444.87  -440.07   -444.87
 1 host RAM / NVMe     324.52   302.23    4     -242.12  -237.32   -242.12
 2 HBM-resident        263.64   245.53    4     -181.24  -176.44   -362.47
 2 host RAM / NVMe     162.26   151.11    4      -79.86   -75.06   -159.72
 4 HBM-resident        131.82   122.77    4      -49.42   -44.62   -197.67
 4 host RAM / NVMe      81.13    75.56    4        1.27     6.07      5.08
 8 HBM-resident         65.91    61.38    5       15.49    20.29    123.93
 8 host RAM / NVMe      40.56    37.78    5       40.84    45.64    326.68
16 HBM-resident         32.95    30.69    5       48.45    53.25    775.13
16 host RAM / NVMe      20.28    18.89    5       61.12    65.92    977.88
```

| GPUs | Engram | Fits? | Fabric | Note |
|---:|---|:--|---|---|
| 1, 2 | either | ❌ | — | negative KV budget on every variant |
| 4 | HBM-resident | ❌ | — | −49.42 GB |
| **4** | **pinned host RAM / NVMe** | ✅ **barely** | PCIe Gen5, one chassis | **1.27 GB KV/GPU at 0.90**, 6.07 GB at 0.95 |
| 8 | HBM-resident | ✅ | PCIe Gen5, one chassis | 15.49 GB/GPU — the only shape that keeps Engram in HBM |
| **8** | **pinned host RAM / NVMe** | ✅ **recommended** | PCIe Gen5, one chassis | 40.84 GB/GPU |
| 16 | either | ✅ | **two chassis, standard NIC** | ruled out by `gpus/rtx6000-pro.md` §4 |

**The headline arithmetic of this whole document.** At the 4-GPU shape that both
measured base-checkpoint deployments actually run, the base leaves **5.52 GB** of KV per
GPU and this checkpoint leaves **1.27 GB** — the +3.33 % checkpoint size eats
**77 %** of the KV budget, because the budget is a small difference of two large
numbers. At `--gpu-memory-utilization 0.95` it is 10.32 GB vs 6.07 GB, a 41 % loss.
That is the real cost of NVFP4 on this card, and it is nowhere near 3.33 %.

**Correction to [`gpus/rtx6000-pro.md`](../../gpus/rtx6000-pro.md) §9g, stated per
METHODOLOGY §8.** That document says of the base checkpoint *"DeepSeek-V4.1-Flash needs at
least 8 cards"*, computed with the Engram tables resident in HBM. With the host/NVMe
offload that vLLM's own recipes make mandatory elsewhere, and that both measured 4-GPU
RTX PRO 6000 deployments of the base checkpoint use, resident weights fall to 324.52 GB
here and the minimum is **4**. §9g also says of this checkpoint *"Do not reach for the
NVFP4 build on this card"* — this document agrees with that conclusion and supplies the
arithmetic behind it.

### 1.3 Max concurrency

`num_key_value_heads = 1` ([architecture.md §2](./architecture.md)): there is exactly one
CSA2 latent KV stream per cache-owning layer, so **plain TP cannot shard the KV cache —
every rank holds all of it**, and the *per-GPU* budget binds, not the aggregate.
DP-attention/DCP would shard it, but `FLASHINFER_MLA_SPARSE_DSV41` reports **DCP ❌**
([flash-attention.md §6.2](../../cross-cutting/flash-attention.md)), so it does not today.

```
Per-sequence KV cost (MiB)
dtype                       8K       32K      128K     1024K
FP4 890 (shipped)         9.53     30.39    113.83    892.58
FP8 1650                 15.47     54.14    208.83   1652.58
BF16 3200                30.38    105.38    405.38   3205.38

max_concurrency = floor(KV budget PER GPU / per-seq cost)  [TP, KV replicated]
 n Engram    util  budget GB dtype                     8K       32K      128K     1024K
 4 host      0.90       1.27 FP4 890 (shipped)        127        39        10         1
 4 host      0.90       1.27 FP8 1650                  78        22         5   infeas.
 4 host      0.90       1.27 BF16 3200                 39        11         2   infeas.
 4 host      0.95       6.07 FP4 890 (shipped)        607       190        50         6
 4 host      0.95       6.07 FP8 1650                 374       106        27         3
 4 host      0.95       6.07 BF16 3200                190        54        14         1
 8 host      0.90      40.84 FP4 890 (shipped)      4,085     1,281       342        43
 8 host      0.90      40.84 FP8 1650               2,517       719       186        23
 8 host      0.90      40.84 BF16 3200              1,281       369        96        12
 8 host      0.95      45.64 FP8 1650               2,813       803       208        26
 8 HBM       0.90      15.49 FP8 1650                 955       272        70         8
 8 HBM       0.90      15.49 BF16 3200                486       140        36         4
16 host      0.90      61.12 FP8 1650               3,768     1,076       279        35
16 host      0.90      61.12 BF16 3200              1,918       553       143        18

--- same, BASE checkpoint for contrast (4 GPU, host Engram) ---
 4 host      0.90       5.52 FP4 890 (shipped)        552       173        46         5
 4 host      0.90       5.52 FP8 1650                 340        97        25         3
 4 host      0.90       5.52 BF16 3200                173        49        12         1
 4 host      0.95      10.32 FP8 1650                 636       181        47         5
```

`fixed_state_per_seq` = **2,703,360 B (2.58 MiB)** for the FP8 SWA ring and
**5,638,656 B (5.38 MiB)** for the BF16 reference container
([architecture.md §5.3](./architecture.md)), at **S = 1** ⚠️ **TO BE VERIFIED** — no
engine doc states an `S` for V4.1-Flash, and a DSpark-speculating runtime plausibly
allocates extra ring slots for the 5-token drafted block. It moves `max_concurrency` by
< 1 % at 8K and not at all at 1M.

**Read the 4-GPU FP8 rows against the base's.** At `0.90` this checkpoint serves **78
concurrent sequences at 8K** where the base serves 340, **5 at 128K** where the base
serves 25, and **cannot fit one sequence at 1M** where the base fits 3. The
`infeasible (KV)` verdict at 1M/4 GPU is a direct consequence of the +16.99 GB.

**The FP4 890 B/token rows are aspirational on this GPU.** FlashInfer PR #4955
(*"feat(sm120): add NVFP4 sparse MLA support for DeepSeek V4 Flash"*, **merged
2026-09-07**, SM120/SM121) means the kernel exists, and it was measured on an **RTX PRO
5000 Blackwell** at 1.41–1.67× prefill / 1.31× decode / ~9 % end-to-end
([flash-attention.md §9.5, §11](../../cross-cutting/flash-attention.md)). But vLLM's
`FLASHINFER_MLA_SPARSE_DSV41` SM12x branch still **requires** one of
`fp8`/`fp8_e4m3`/`fp8_ds_mla`, so no engine hands the kernel an NVFP4 dtype.
Independently, a community patch series
([danielwoz/vllm-dspark-nvfp4](https://github.com/danielwoz/vllm-dspark-nvfp4)) implements
a true E2M1 KV cache for **DeepSeek-V4**-Flash on RTX PRO 6000/SM120 and reports
*"~1.54M tokens (vs ~592K with fp8)"* and *"243–262 tok/s (+25–34 % vs fp8's ~195 tok/s)"*
— i.e. the upside is real and measured on this GPU family, for the V4 sibling, outside
any released engine. Plan with the FP8 column.

### 1.4 Tensors that cannot be sharded

Present in full on every rank whatever the parallelism
([architecture.md §3.1, §3.3](./architecture.md)):

| Tensor group | Why it replicates | Bytes |
|---|---|---:|
| MoE routers `ffn.gate` (BF16 + FP32 bias/`bias_vl`) | every rank must score all 384 experts before dispatch | 0.157 GB |
| mHC `hc_attn_fn` / `hc_ffn_fn` (**FP32**, 24×20480) | produce 24 scalars consumed by the *next* sublayer | 0.157 GB |
| KV compressors `attn.compressor.*` (BF16 `wkv`, **FP32** `wgate`) | feed the single shared latent | 0.037 GB |
| CSA2 indexer `wk` / `weights_proj` / `k_norm` (BF16) | tiny per-head reduction | 0.045 GB |
| RMSNorms, attention sinks, image tokens | elementwise / per-head scalars | 0.0004 GB |
| **Subtotal replicated** | | **≈ 0.40 GB/rank** |
| **The CSA2 latent KV cache itself** | `num_key_value_heads = 1` → no head to split under TP | §1.3 |
| Engram tables, if HBM-resident and not row-sharded | 24 random single-row gathers per token per Engram layer | 202.76 GB |

0.40 GB/rank is noise. **The KV replication is not**: it is the whole of §1.3.
`ParallelEngramEmbedding` in the reference `inference/model.py` does shard the Engram
tables by row, but on this GPU they leave HBM entirely (§5), so the question is moot.

**Multi-node fabric needed?** No, at 4 or 8 GPUs — one PCIe Gen5 chassis. At 16 it needs
two chassis over a standard NIC with no GPU fabric, which `gpus/rtx6000-pro.md` §4 rules
out. Even inside one chassis, NCCL hangs on the first collective unless **IOMMU and ACS
are disabled in BIOS** (`gpus/rtx6000-pro.md` §11 gotcha 10).

---

## 2. What runs on this GPU for this model

sm_120 is the *consumer/prosumer* Blackwell die (GB202). It has **no `tcgen05` MMA and no
TMEM**, a **99 KB** shared-memory ceiling against Hopper/B200's ~228 KB, and **no NVLink**
(`gpus/rtx6000-pro.md` §1, §4). Everything below follows from those three facts.

| Optimization | Status | Kernel / flag | Expected effect |
|---|---|---|---|
| **Attention: FA2** | **unsupported for this operator** | — | This model's language attention is a sparse gather over ≤ 640 entries (128 SWA + top-512), not a FlashAttention shape. FA2 matters only for the 0.49 B ViT. |
| **Attention: FA3 / FA4** | **unsupported** | — | Both need `tcgen05`, which sm_120 lacks. Upstream FA4 *has* a `FlashAttentionForwardSm120`, but it *"uses SM80 MMA with SM120 SMEM capacity"*, has **no paged KV, no block sparsity, no SplitKV, no FP8**, and **vLLM refuses FA4 on 12.x outright** ([flash-attention.md §3, §9.5](../../cross-cutting/flash-attention.md)). |
| **Attention: FlashMLA / FlashMLA-sparse / `FLASHMLA_SPARSE_DSV41`** | **unsupported** | — | gated `capability.major in [9, 10]`. `FLASHMLA_MEGA_ATTN_DSV41` is `major == 10` only. *"FlashMLA does not run on A100, on RTX PRO 6000, or on MI355X — full stop"* ([flash-attention.md §7](../../cross-cutting/flash-attention.md)). |
| **Attention: `FLASHINFER_MLA_SPARSE_DSV41`** | **native** | vLLM default on SM12x; **an FP8 KV dtype is mandatory** (`fp8` / `fp8_e4m3` / `fp8_ds_mla`) plus a FlashInfer build carrying the SM120 sparse-MLA decode API | The working V4.1 sparse path here. Head size 512, block 128 ([flash-attention.md §6.2, §9.5](../../cross-cutting/flash-attention.md)). |
| **Attention: `FLASHINFER_MLA_SPARSE_SM120`** | **native** | 12.x, KV `auto`/`fp8`/`fp8_e4m3`/`fp8_ds_mla`, block 64 or 256 | dedicated SM120 sparse kernel. |
| **Attention: B12X sparse-MLA** | **native** (community images) | `--attention-backend B12X_MLA_SPARSE` | What every working RTX PRO 6000 DeepSeek-V4/V4.1 stack actually runs. |
| **DSA indexer top-k** | **native** | FlashInfer `top_k_varlen`; 0.6.18 adds *"SM120/121 also picks up top-k 192 and 256"*; PR #4955 lists *"primary top-k 128 or 512"* | V4.1 uses `index_topk: 512`, so 512 is in the supported set. ⚠️ **TO BE VERIFIED** end-to-end on this card. |
| **TileLang DSA sparse-MLA** | **unsupported** | — | needs 202–206 KB of dynamic shared memory; the device allows **101,376 B**, returning `invalid argument` (`gpus/rtx6000-pro.md` §1, §5c). |
| **Linear-attention kernels** | **n/a** | — | V4.1-Flash has no linear-attention layers. |
| **Weight format: NVFP4 routed experts (W4A4, gs16)** | **⚠️ dequant by default; native only as an unvalidated opt-in** | default → Marlin W4A16; opt-in `--moe-backend flashinfer_b12x` (vLLM) / `--fp4-gemm-backend`-family flags | See the box below. This is the decisive row. |
| **Weight format: FP8 / MXFP8 32×32 UE8M0 (attention, shared experts, Engram, indexer)** | **native** ⚠️ | CUTLASS SM120 FP8 | 207 GB of the checkpoint. **`DeepGEMM does not support sm_120`** (`arch_major=12` → `DG_HOST_UNREACHABLE`), so DeepSeek's fused Mega-Gate / Mega-mHC / Mega-MoE kernels never run (`gpus/rtx6000-pro.md` §7). TRT-LLM's sm120 row omits **block-scaled** FP8 entirely ([quantization-formats.md §5.1](../../cross-cutting/quantization-formats.md)) — ⚠️ whether a first-class 32×32 block-scaled FP8 GEMM exists on sm_120 is **TO BE VERIFIED**; SGLang has open work (#39978, #39872, #39065). |
| **Weight format: MTP/DSpark experts, still MXFP4** | **⚠️ a reproduced failure mode on this GPU** | requires the loader to route draft experts to `Mxfp4MoEMethod`, not the NVFP4 method | **Measured**: on 2× RTX PRO 6000 with the V4-Flash NVFP4 sibling, *"the loader incorrectly routed draft experts through the NVFP4 dequantization path, destroying scale information and producing 'noise' with ~0 % token acceptance"* [src](https://github.com/hikarioyama/dsv4-flash-nvfp4-sm120). This is [architecture.md §7.3](./architecture.md)'s highest-risk open question, confirmed in the field. |
| **KV-cache quant: FP8** | **native, and mandatory** | `--kv-cache-dtype fp8` / `fp8_e4m3` | 1,650 B/token. The SM12x branch of `FLASHINFER_MLA_SPARSE_DSV41` **requires** an FP8 dtype. |
| **KV-cache quant: NVFP4 (the shipped 890 B/token cache)** | **⚠️ kernel exists, no released engine routes to it** | FlashInfer PR #4955 (merged 2026-09-07, SM120/121) | 1.85× KV capacity if it opens: 374 → 607 concurrent at 8K on 4 GPUs at 0.95. Community patch series measures **~1.54 M vs ~592 K KV tokens and 243–262 vs ~195 tok/s** on the V4 sibling on this GPU [src](https://github.com/danielwoz/vllm-dspark-nvfp4). TRT-LLM cannot do it at all: `QE4m3KvE2m1` has no SM120/121 variant. |
| **Prefix caching** | **native** | `--enable-prefix-caching` | ⚠️ NVIDIA's own vLLM recipe for this checkpoint sets **`--no-enable-prefix-caching`** ([architecture.md §8.2](./architecture.md)) with no stated reason. On this GPU it is an 8–11× warm-TTFT lever (§3.4) and should be turned back on. |
| **Speculative decoding: DSpark (block 5)** | **native, load-bearing, and at risk** | `--speculative-config '{"method":"dspark","num_speculative_tokens":5,...}'` | NVIDIA: *"DSpark tensors are preserved, but speculative decoding was not exercised in the reported validation"* ([architecture.md §7.2](./architecture.md)). On the V4-Flash NVFP4 sibling on 2× RTX PRO 6000, MTP/DSpark is **+38 % single-stream (108.8 → 150.6 tok/s)** once the MXFP4 routing patch is applied, with *"acceptance 58–94 %, mean accept length 2.16–2.88"* [src](https://github.com/hikarioyama/dsv4-flash-nvfp4-sm120). Without the patch: ~0 %. |
| **Speculative decoding: MTP / EAGLE** | **n/a** | — | V4.1's speculation *is* DSpark; there is no separate EAGLE path. |
| **EP / TP** | **native** | `--enable-expert-parallel`, `--tensor-parallel-size 4` | `gpus/rtx6000-pro.md` §4 measures TP=4 over PCIe collapsing to 6–7 tok/s on a *dense-all-reduce* model; a 384-expert top-6 MoE dispatches far less per token, and the base pair demonstrably reaches 740–830 tok/s at TP4+EP4. |
| **DP-attention / DCP** | **unsupported for this model's KV** | — | `FLASHINFER_MLA_SPARSE_DSV41` reports **DCP ❌** ([flash-attention.md §6.2](../../cross-cutting/flash-attention.md)). This is what forces the replicated-KV rows in §1.3. |
| **PD disaggregation** | **unsupported** | — | vLLM declares `"pd_cluster": {"rtx_pro_6000_8x": "unsupported"}` for the V4-Flash sibling; no V4.1 profile exists. Dynamo has no sm_120 statement at all (`gpus/rtx6000-pro.md` §7), and PCIe-only KV transfer is the wrong fabric. [serving-optimizations.md §3.5](../../cross-cutting/serving-optimizations.md): *"disaggregation is a thousand-GPU problem."* |
| **CUDA graphs** | **native for decode, and the single largest knob** | `--compilation-config '{"cudagraph_mode":"FULL_AND_PIECEWISE"}'`; disable prefill graphs | Without them this model on 8× RTX PRO 6000 measures **2.3–4.0 tok/s** [src](https://github.com/vllm-project/vllm/issues/56892); `gpus/rtx6000-pro.md` §11 gotcha 8 records the same cliff on the V4 sparse path (**~5 → 30–35 tok/s**). Infatoshi's V4-NVFP4 ladder shows it directly: eager **17.8** → piecewise **77.1** → FULL_AND_PIECEWISE **109.3** tok/s. |
| **Multimodal encoder placement** | **⚠️ untested for this checkpoint on this GPU** | `--mm-encoder-tp-mode data`, or `--language-model-only` to drop the 0.49 B ViT | NVIDIA's vLLM recipe for this checkpoint is **text-only** (`--language-model-only`). On the base checkpoint on 4× RTX PRO 6000, single-image at 1,024 image tokens passed, multi-image was unresolved and a **six-frame video test failed** [src](https://github.com/0xSero/deepseek-v4.1-flash-4x-rtx-pro-6000). |

> **The decisive row, in full — what the NVFP4 experts actually execute on sm_120.**
>
> The GPU *has* NVFP4 tensor cores: `mma.sync.aligned.…block_scale` at **1,920 TFLOPS
> dense** (`gpus/rtx6000-pro.md` §3c). The problem has never been the silicon; it is the
> **MoE grouped GEMM**. Three independent statements:
>
> 1. **METHODOLOGY §8** pins this card as *"NVFP4 MoE grouped-GEMM broken → Marlin
>    W4A16."*
> 2. **[`quantization-formats.md` §9.6](../../cross-cutting/quantization-formats.md)**
>    puts this checkpoint × RTX PRO 6000 at **`dequant ⚠️ᵉ`**, with note (e) stating the
>    disagreement explicitly and concluding: *"Planning value: assume dequant until
>    measured."* The same table puts the **base MXFP4 checkpoint** × RTX PRO 6000 at
>    **`native W4A8`**.
> 3. **`gpus/rtx6000-pro.md` §6a** updates the picture: `compute_120f` under CUDA 13.0
>    fixes correctness and reaches **39.0 tok/s vs Marlin's 46–49** on Qwen3.5-397B (a
>    ~20 % gap, not 3×), and vLLM main ships an opt-in native SM12x fused MoE
>    (`--moe-backend flashinfer_b12x`) that is *"excluded only from auto-selection"*
>    because *"a ModelOpt NVFP4 checkpoint resolves as W4A16"* while
>    `FlashInferExperts._supports_quant_scheme` lists only the W4A4 pair for capability
>    ≥ 100.
>
> **Resolution for this pair.** All three are simultaneously true and they say the same
> thing operationally: *by default this checkpoint's experts run Marlin W4A16 on
> sm_120*, and the native path is reachable only by opting in, on a patched build, with
> no published validation for **V4.1**. The two field reports on the **V4-Flash NVFP4**
> sibling on this GPU both had to hand-patch vLLM and they disagree on which path wins:
> hikarioyama measures **B12X native 108.8 vs Marlin 63.8 tok/s** (native 1.71× faster),
> Infatoshi measures **Marlin 202.7 vs FlashInfer-CUTLASS 193.2 tok/s** (Marlin 1.05×
> faster). Those are consistent once you separate the backends — B12X native > Marlin >
> FlashInfer-CUTLASS — and the ordering matches `gpus/rtx6000-pro.md` §6b's Marlin-92 vs
> FI-CUTLASS-74 on Nemotron. **⚠️ TO BE VERIFIED for V4.1-NVFP4: none of it.**
>
> **Why this matters more here than on any other GPU in the repo.** On B200/B300/GB300
> the NVFP4 build gains native W4A4 and the base keeps native W4A8 — a real, if
> unmeasured, trade. On sm_120 the base keeps **native W4A8** via
> `b12x_fused_moe` / `--moe-runner-backend flashinfer_mxfp4`
> ([quantization-formats.md §9.6 note c](../../cross-cutting/quantization-formats.md),
> FlashInfer 0.6.18 shipping *"MXFP4 on Blackwell RTX PRO and DGX Spark"*), and the
> NVFP4 build **gives that up** for a dequant fallback. It is the only GPU where
> requantising to NVFP4 moves the checkpoint *backwards* on the format-support matrix
> and *upwards* in size at the same time.

---

## 3. Throughput and latency

### 3.1 MBU / MFU assumptions, stated before any number

METHODOLOGY §4's band is MBU 0.5–0.7 for first-gen Blackwell software;
`gpus/rtx6000-pro.md` §9b narrows it to **0.50–0.65 on sm_120** and §9e gives MFU
**0.20–0.35 at FP4 / 0.25–0.40 at FP8**. This model does not reach either band on this
card, and rather than assume one I **calibrate κ = measured / roofline(MBU = 1, A = 1)**
against the six published measurements of the *base* checkpoint on this *same* GPU,
using this document's own byte model so the two are commensurable.

Byte model (METHODOLOGY §4 MoE distinct-expert form, inputs from
[architecture.md §6.3](./architecture.md)):

```
distinct_experts(b)  = 384 × (1 − (1 − 6/384)^b)          per layer
one NVFP4 expert     = 19,906,584 B        (base MXFP4: 18,800,640 B)
per-layer non-expert = 174,931,968 B       × 40 layers
once per step        = Engram wkv 315,043,840 B + BF16 LM head 1,323,827,200 B
Engram row gather    = 12,672 B per token (scattered; over PCIe when offloaded)
kv_read(ctx)/seq     = 38×512×528 + 4×16,384×132 + 330×ctx + 2,703,360   (FP8 KV entries)
bytes_step(b,ctx)    = 40×(distinct(b)×EXP + 174,931,968) + 315,043,840 + 1,323,827,200
                       + b×(kv_read(ctx) + 12,672)
```

```
kappa = measured / roofline(MBU=1, A=1), base MXFP4 checkpoint, 4x RTX PRO 6000 Workstation (1,792 GB/s)
source                             b     ctx     meas  GB/step  roofline  kappa  MBU@A=2.5
local-inference-lab C1 ctx~0       1     256    256.6    13.17     544.3  0.471      0.189
local-inference-lab C8 agg         8     256    830.8    42.99    1333.8  0.623      0.249
0xSero C1 2K in                    1    2048    226.8    13.17     544.2  0.417      0.167
0xSero C8 2K in                    8    2048    729.1    43.00    1333.6  0.547      0.219
0xSero C8 128K in                  8  131072    736.3    43.34    1323.2  0.556      0.223
0xSero C8 500K in                  8  500000    599.7    44.31    1294.1  0.463      0.185
kappa band 0.417-0.623, mean 0.513
```

κ folds MBU and the DSpark accepted length together, so **achieved MBU = κ / A ≈
0.17–0.25** at the measured accepted length 2.44–2.55. That is **a third of the sm_120
planning band**; `gpus/rtx6000-pro.md` §9b independently derives ~0.26 achieved MBU for a
small-expert MoE on this card, so it is what sm_120 does on MoE decode, not an outlier.
(The base-pair document quotes κ = 0.569–0.834 against a byte model with an 18.75 GB
static read; this document's static read is **8.64 GB**, matching
[architecture.md §6.3](./architecture.md)'s 12.49 GiB total at batch 1 exactly. The two
κ bands describe the same measurements through different denominators and must not be
mixed — that is why κ is re-derived here.)

**The NVFP4 penalty, isolated.** Identical everything except the expert bytes:

```
NVFP4 vs MXFP4 expert bytes: 19,906,584 vs 18,800,640 = 1.0588x (+5.88%)
  b=  1: NVFP4    13.45 GB/step vs MXFP4    13.18 -> throughput -1.97%
  b=  8: NVFP4    45.09 GB/step vs MXFP4    43.08 -> throughput -4.46%
  b= 32: NVFP4   130.71 GB/step vs MXFP4   123.99 -> throughput -5.14%
  b= 64: NVFP4   204.88 GB/step vs MXFP4   194.09 -> throughput -5.27%
  b=128: NVFP4   277.82 GB/step vs MXFP4   263.10 -> throughput -5.30%
  b=256: NVFP4   317.28 GB/step vs MXFP4   300.60 -> throughput -5.26%
```

**−2 % to −5.3 % of decode throughput, before any kernel-path penalty**, purely because
NVFP4's group-16 scale layout is one byte per 32 weights larger than MXFP4's group-32
layout and decode is bandwidth-bound. Add the Marlin-vs-native factor and the gap widens
to 1.7× (§3.5).

Prefill is **not** modelled from an MFU here. The base pair measures prefill MFU at
**0.044** — the encoder is dominated by indexer scans, Engram random gathers and the mHC
Sinkhorn, not by the expert GEMM — so changing the expert GEMM's precision barely moves
it. §3.4 therefore uses the base pair's measured prefill rates directly, ⚠️ **TO BE
VERIFIED** for this checkpoint.

### 3.2 Estimated decode, 4 GPUs (Engram host-offloaded, FP8 KV, TP replicated)

`agg tok/s = κ × batch × (n × 1.597e12) / bytes_step`; `TPOT = bytes_step / (n × BW × κ)`.
"Marlin agg" applies the only published same-model same-GPU Marlin/native ratio,
**63.8 / 108.8 = 0.586** [src](https://github.com/hikarioyama/dsv4-flash-nvfp4-sm120).
`feas@.95` is METHODOLOGY §3's consistency rule against §1.3's caps at
`--gpu-memory-utilization 0.95`.

```
  caps @util 0.90 : S1 4K/512=123  S2 32K/1K=21  S3 128K/2K=5
  caps @util 0.95 : S1 4K/512=589  S2 32K/1K=103  S3 128K/2K=27
   b scenario     GB/step         tok/s/GPU         agg tok/s         TPOT ms        Marlin agg  <=50ms?  feas@.95
   1 S1 4K/512      13.44      50-74            198-296          3.4-5.0          116-174            yes       yes
   1 S2 32K/1K      13.45      50-74            198-296          3.4-5.0          116-174            yes       yes
   1 S3 128K/2K     13.48      49-74            198-295          3.4-5.1          116-173            yes       yes
   8 S1 4K/512      45.02     118-177           473-707         11.3-16.9         278-415            yes       yes
   8 S2 32K/1K      45.09     118-177           473-706         11.3-16.9         277-414            yes       yes
   8 S3 128K/2K     45.36     117-175           470-702         11.4-17.0         276-412            yes       yes
  32 S1 4K/512     130.42     163-244           654-976         32.8-49.0         383-573            yes       yes
  32 S2 32K/1K     130.73     163-244           652-974         32.8-49.1         382-571            yes       yes
  32 S3 128K/2K    131.77     162-242           647-966         33.1-49.5         379-567            yes INFEAS(27)
  64 S1 4K/512     204.28     209-312           835-1247        51.3-76.7         489-731             NO       yes
  64 S2 32K/1K     204.90     208-311           832-1243        51.5-76.9         488-729             NO       yes
  64 S3 128K/2K    207.00     206-308           824-1230        52.0-77.7         483-722             NO INFEAS(27)
 128 S1 4K/512     276.63     308-460          1233-1841        69.5-103.8        723-1080            NO       yes
 128 S2 32K/1K     277.87     307-458          1227-1833        69.8-104.3        720-1075            NO INFEAS(103)
 128 S3 128K/2K    282.06     302-452          1209-1806        70.9-105.9        709-1059            NO INFEAS(27)
 256 S1 4K/512     314.90     541-809          2166-3235        79.1-118.2       1270-1897            NO       yes
 256 S2 32K/1K     317.37     537-803          2149-3210        79.7-119.1       1260-1882            NO INFEAS(103)
 256 S3 128K/2K    325.76     523-782          2093-3127        81.9-122.3       1228-1834            NO INFEAS(27)

4 GPU S1 4K/512: largest b at TPOT<=50ms (kappa lo) = 32; KV cap@.95 = 589; binding = 32
4 GPU S2 32K/1K: largest b at TPOT<=50ms (kappa lo) = 32; KV cap@.95 = 103; binding = 32
4 GPU S3 128K/2K: largest b at TPOT<=50ms (kappa lo) = 32; KV cap@.95 = 27; binding = 27
```

**S1/S2 interactive ceiling: concurrency 32** (TPOT-bound). **S3 interactive ceiling: 27**
(KV-bound — and only 5 at `0.90`). Every row above C32 fails the 50 ms SLO at the
pessimistic κ and is carried only for the S4 max-throughput operating point.

### 3.3 Estimated decode, 8 GPUs

```
  caps @util 0.90 : S1 4K/512=3962  S2 32K/1K=698  S3 128K/2K=183
  caps @util 0.95 : S1 4K/512=4427  S2 32K/1K=780  S3 128K/2K=205
   b scenario     GB/step         tok/s/GPU         agg tok/s         TPOT ms        Marlin agg  <=50ms?  feas@.95
   1 S1 4K/512      13.44      50-74            396-592          1.7-2.5          233-347            yes       yes
   8 S1 4K/512      45.02     118-177           947-1415         5.7-8.4          555-829            yes       yes
   8 S3 128K/2K     45.36     117-175           940-1404         5.7-8.5          551-823            yes       yes
  32 S1 4K/512     130.42     163-244          1307-1953        16.4-24.5         767-1145           yes       yes
  32 S3 128K/2K    131.77     162-242          1294-1933        16.6-24.7         759-1133           yes       yes
  64 S1 4K/512     204.28     209-312          1669-2494        25.7-38.3         979-1462           yes       yes
  64 S3 128K/2K    207.00     206-308          1647-2461        26.0-38.9         966-1443           yes       yes
 128 S1 4K/512     276.63     308-460          2465-3683        34.8-51.9        1446-2160          marg       yes
 128 S3 128K/2K    282.06     302-452          2418-3612        35.4-52.9        1418-2118          marg       yes
 256 S1 4K/512     314.90     541-809          4331-6471        39.6-59.1        2540-3794          marg       yes
 256 S3 128K/2K    325.76     523-782          4187-6255        40.9-61.1        2455-3668          marg INFEAS(205)

8 GPU S1 4K/512: largest b at TPOT<=50ms (kappa lo) = 114; KV cap@.95 = 4427; binding = 114
8 GPU S2 32K/1K: largest b at TPOT<=50ms (kappa lo) = 112; KV cap@.95 = 780; binding = 112
8 GPU S3 128K/2K: largest b at TPOT<=50ms (kappa lo) = 108; KV cap@.95 = 205; binding = 108
```

⚠️ **These 8-GPU rows are the least trustworthy numbers in the document.** The only
published 8-GPU measurement of V4.1-Flash on this card is **2.3–4.0 tok/s**
[src](https://github.com/vllm-project/vllm/issues/56892) — three orders of magnitude
below — because CUDA-graph capture is unusable there. The roofline says 8 GPUs should
roughly double 4-GPU throughput once that is fixed; nobody has shown it.

### 3.4 TTFT

Anchored on the base pair's two **measured** prefill rates on this GPU, Server-Edition
scaled: **19,278 tok/s** (B12X + pinned-RAM Engram, 4 GPU) and **7,087 tok/s** (NVMe
Engram). ⚠️ Inherited, not measured for this checkpoint; justified in §3.1 by the 0.044
prefill MFU. The 90 %-hit column is `TTFT(0) × (1 − h)` per
[serving-optimizations.md §1.2](../../cross-cutting/serving-optimizations.md).

```
S1 4K    B12X + pinned-RAM Engram     0% hit    0.21 s   90% hit    0.02 s
S1 4K    NVMe Engram                  0% hit    0.58 s   90% hit    0.06 s
S2 32K   B12X + pinned-RAM Engram     0% hit    1.70 s   90% hit    0.17 s
S2 32K   NVMe Engram                  0% hit    4.62 s   90% hit    0.46 s
S3 128K  B12X + pinned-RAM Engram     0% hit    6.80 s   90% hit    0.68 s
S3 128K  NVMe Engram                  0% hit   18.49 s   90% hit    1.85 s
```

**Engram placement is a 2.7× TTFT factor** and costs 188.83 GiB of host RAM. It is the
largest single lever on the input leg, and it is unchanged by the NVFP4 requantisation —
the Engram tables were never converted.

### 3.5 Measured — every published number for this pair or its nearest proxy

**There is no measurement of `nvidia/DeepSeek-V4.1-Flash-NVFP4` on any RTX PRO 6000.**
Confirmed 2026-09-19: no MLPerf row (V4.1-Flash is not an MLPerf model), no InferenceX
row (its hardware set is `b200, b300, gb200, gb300, h100, h200, mi300x, mi325x, mi355x`
— no RTX), no vLLM recipe (`recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/rtx_pro_6000.json`
→ HTTP 404), and NVIDIA's card publishes **no throughput or latency figure of any kind
for this checkpoint on any GPU** ([architecture.md §10.2](./architecture.md)).

The table below is therefore the **nearest published proxies**, in decreasing order of
relevance. Rows A–C are the same architecture family at the same precision on the same
GPU (`nvidia/DeepSeek-V4-Flash-NVFP4`, the V4 sibling); rows D–E are this exact model at
its *native* MXFP4 precision on this GPU; row F is the failure case.

| # | Model / precision | GPUs | Engine | Config | Measured | Source |
|---|---|---|---|---|---|---|
| **A** | **`nvidia/DeepSeek-V4-Flash-NVFP4`** (256 experts, ~157 GB) | **2× RTX PRO 6000** SM120 | vLLM `voipmonitor/vllm:chthonic-consecration-…-b12x0ff2847-pr20-cu132` | **B12X native NVFP4**, TP2 | **108.8 tok/s** C1 MTP-off, **150.6** MTP-on (**+38 %**); ~252 → ~328 tok/s at C4; acceptance **58–94 %**, accept length 2.16–2.88; 1,048,576-token single context, 1,925,540-token KV pool | [hikarioyama](https://github.com/hikarioyama/dsv4-flash-nvfp4-sm120) |
| **A′** | same | same | same | **Marlin W4A16** (`VLLM_TEST_FORCE_FP8_MARLIN=1`), no MTP | **63.8 tok/s** C1; 210.1 agg at C4; 364.0 agg at C8; KV pool **307,855 tokens** (fp8). *"MARLIN is weight-only FP4 dequant — slower decode than the B12X native path"* | same |
| **B** | **`DeepSeek-V4-Flash-0731-NVFP4`** (~176 GB) | **2× RTX PRO 6000 Workstation** | vLLM `0.26.1rc1.dev303+g74295e3bd` + **13 patches**, flashinfer 0.6.15.post1 | TP2, `--kv-cache-dtype fp8 --block-size 256` | ladder: eager **17.8** → piecewise graphs **77.1** → tuned GEMM **94.8** → FULL_AND_PIECEWISE **109.3** → +DSpark k=3 **193.2** → **+Marlin W4A16 MoE 202.7** (recommended). Prefill **4,339 tok/s** avg on a 504,381-token request; **66 tok/s at 500K depth**; max validated concurrency **4** (*"c=8 crashes due to flashinfer fallback bug"*) | [Infatoshi](https://github.com/Infatoshi/dsv4-flash-2x-rtxpro6000s) |
| **C** | DeepSeek-V4-Flash **FP4+FP8** | 2× RTX PRO 6000 | vLLM | FP8 KV | **106.1 tok/s** single-user @1K; **96.3** @1024K; peak **367.8 tok/s @ C8**, 1K ctx; prefill peak **13,036 tok/s**; TTFT 1.7 s @1K / 221.9 s @1024K; ITL 9 ms; peak **865 W** | [Millstone AI](https://www.millstoneai.com/inference-benchmark/deepseek-v4-flash-fp4fp8-2x-rtx-pro-6000-blackwell) |
| **D** | **DeepSeek-V4-Flash, native MXFP4 W4A4** | **4× RTX PRO 6000 Max-Q** | SGLang fork `feat/sm120-mxfp4-w4a4-moe` + FlashInfer fork `launch_sm120_moe(quant_mode="mxfp4")` | TP4, FP8 KV | c1: 2K **57.6 tok/s** (TTFT 604 ms, TPOT 16.8 ms); 8K **49.8**; 1M **10.4** (TTFT 6.46 s, TPOT 90.2 ms). Concurrency sweep: **756 tok/s @c64** 2K, 444 @c32 4K, 263 @c40 8K, 163 @c104 16K, 79 @c40 32K, 40 @c16 64K | [ambientlight](https://github.com/ambientlight/rtx-pro-6000-bench/blob/main/docs/DEPLOY-MXFP4-W4A4-DEEPSEEK-V4-FLASH-SM120.md) |
| **E** | **`deepseek-ai/DeepSeek-V4.1-Flash` (base, MXFP4)** | **4× RTX PRO 6000 Workstation** | vLLM/B12X and SGLang-derived | TP4/DCP1, RAM or NVMe Engram, DSpark | **256.59 tok/s** C1, **830.82** C8 agg, **20,234 tok/s** 32K prefill (RAM Engram); 226.8 / 729.1 / 736.3 @128K / 599.7 @500K (NVMe Engram); DSpark acceptance 78.9–86.4 % | [local-inference-lab](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/deepseek-v4.1-flash.md), [0xSero](https://github.com/0xSero/deepseek-v4.1-flash-4x-rtx-pro-6000) |
| **F** | `deepseek-ai/DeepSeek-V4.1-Flash` | **8× RTX PRO 6000 Max-Q** | stock `vllm/vllm-openai:deepseekv41-flash-0909`, `--enforce-eager` | TP8, FP8 KV | ***"Avg generation throughput: 2.3–4.0 tokens/s"*** at 3 concurrent. *"CUDA graph capture is not usable on SM120 for V4.1-Flash"*; DSpark *"~72–87 % of drafted tokens are rejected"* | [vLLM #56892](https://github.com/vllm-project/vllm/issues/56892) |
| **G** | `DeepSeek-V4-Flash` + **NVFP4 E2M1 KV** patch | RTX PRO 6000 SM120 | vLLM + flashinfer 0.6.13+cu132, `ModelType::DSV4_NVFP4` | 360 B/token page | KV pool **~1.54 M tokens vs ~592 K at fp8** (1.47× tokens/GiB); decode **243–262 tok/s (+25–34 % vs fp8's ~195)**; HumanEval 90.2 % / MBPP 94 %; needle-recall green through 1,030,039 tokens | [danielwoz](https://github.com/danielwoz/vllm-dspark-nvfp4) |
| **H** | `DeepSeek-V4.1-Flash` Engram placement A/B | 2× RTX PRO 6000 | patched vLLM | — | *"pinned Engram in RAM: ~280 GB host RAM, ~113 tok/s"* · *"naive/eager NVMe Engram: ~12 GB RSS, ~26 tok/s"* · *"NVMe prestage + CUDA graphs: ~18 GB RSS, ~80 tok/s"* | [dbirks/home-k8s#127](https://github.com/dbirks/home-k8s/issues/127) |

The same issue states the conclusion this document reaches by arithmetic, for the base
checkpoint: *"The stock checkpoint already stores the large routed-expert block in FP4,
so a conventional 'make an NVFP4 quant and halve everything' approach does not solve the
96GB problem"*, with the breakdown *"routed + DSpark experts: ~259.5 GiB, already FP4 ·
Engram tables: ~183.1 GiB FP8 · conventional GPU-resident vLLM recipe: ~614 GB minimum
GPU memory"* [src](https://github.com/dbirks/home-k8s/issues/127).

### 3.6 Why estimate and measurement differ

| Gap | Size | Explanation |
|---|---|---|
| Achieved MBU 0.17–0.25 vs the 0.50–0.65 sm_120 band | ~2.5× | `gpus/rtx6000-pro.md` §9b derives ~0.26 for a small-expert MoE here independently. Causes, none isolated: **no DeepGEMM on sm_120** so the fused Mega-* kernels never run; the **99 KB** shared-memory ceiling forcing small tiles; SM80-era `mma.sync` with no warp specialisation; PCIe all-reduce on every TP step. ⚠️ no profile published. |
| Prefill MFU 0.044 vs the 0.20–0.35 FP4 band | ~6× | The CED encoder is not GEMM-dominated at these lengths: the indexer's growing scan plus the Engram module's **24 random single-row gathers per token per Engram layer** dominate. The same GPU reaches MFU 0.30–0.40 on a 3 B-active NVFP4 MoE with dense TRT-LLM kernels (`gpus/rtx6000-pro.md` §9e) — so this is the model, not the silicon. |
| Row A (108.8 tok/s C1) vs this document's 4-GPU C1 estimate (198–296 agg) | — | Row A is a **different, smaller** model (V4-Flash NVFP4, 256 experts, ~157 GB) on **2** GPUs. Normalising by GPU count and expert-read size is not defensible across two MoE geometries; the row is evidence that the NVFP4 path *runs and is coherent* on sm_120, not a throughput transfer. |
| Row A′ vs Row A: Marlin 0.586× native | 1.71× | The only same-model, same-GPU, same-build Marlin/native A/B published for a DeepSeek NVFP4 checkpoint. Used as the "Marlin agg" column in §3.2–3.3. ⚠️ Row B measures the opposite ordering against a *different* baseline (FlashInfer-CUTLASS, not B12X) and is consistent once the three backends are ranked B12X > Marlin > FI-CUTLASS. |
| Row F (2.3–4.0 tok/s, 8 GPU) vs §3.3 | ~1000× | CUDA-graph capture, full stop. Row B's own ladder reproduces it in miniature: eager 17.8 → graphs 109.3 tok/s. |
| DSpark broken (§2) vs working | 2.5× | If the MXFP4 draft experts are misrouted through the NVFP4 method, acceptance goes to ~0 % and the effective accepted length falls from ~2.5 to 1. Every §3.2 row divides by 2.5: S1 C32 becomes **262–390 agg tok/s**, S4 C256 becomes **866–1,294**. |
| NVFP4 vs MXFP4 expert bytes | −2 % to −5.3 % | §3.1. Pure bandwidth; unavoidable; independent of kernel path. |

---

## 4. Cost

Prices, per METHODOLOGY §6/§8, from
[`cross-cutting/cloud-pricing.md` §5.10 — RTX PRO 6000 Blackwell (Server Edition)](../../cross-cutting/cloud-pricing.md)
only, never a neighbouring GPU's row:

| Tier | $/GPU-hour | Row cited |
|---|---:|---|
| **Cheapest reputable on-demand** | **$1.80** | Nebius, "RTX PRO 6000", 1–8 GPUs (§5.10, §4.3) ⚠️ the listing does not state which edition |
| Cheapest **SE-confirmed** on-demand | $1.85 | Hyperstack, "RTX PRO 6000 **SE**", 1–8 GPUs (§5.10, §4.5) — the only rate card that says SE |
| **Cheapest hyperscaler on-demand** | **$4.143** | AWS `g7e.48xlarge`, 8× RTX PRO 6000 Blackwell SE (§5.10, §3.1) |
| **1-year reserved** | **$1.30** | Hyperstack reserved, RTX PRO 6000 SE (§5.10, §9) |
| (context) GCP `g4-standard-384` on-demand / 3-yr resource CUD | $4.500 / $1.979 | §5.10, §3.2 |
| (context) CoreWeave RTX PRO 6000 High Memory 8× / spot | $2.500 / $1.386 | §5.10, §4.1 |

`high ÷ low = 2.30×` for this GPU (cloud-pricing.md §5.13), so every figure is a band.

### 4.1 $/1M output and input

```
=== $/1M OUTPUT tokens ===
operating point                                   agg tok/s     Nebius Hyperstack        AWS Hyperstack
                                                                  $1.80      $1.85     $4.143      $1.30
S1 interactive, 4 GPU, C32 [kappa-lo]                   654      3.058      3.143      7.039      2.209
S1 interactive, 4 GPU, C32 [kappa-hi]                   976      2.049      2.106      4.717      1.480
S1 interactive, 4 GPU, C32, MARLIN [kappa-lo]           383      5.222      5.367     12.019      3.771
S1 interactive, 4 GPU, C32, MARLIN [kappa-hi]           573      3.490      3.587      8.034      2.521
S4 max-throughput, 4 GPU, C256 [kappa-lo]             2,166      0.923      0.949      2.125      0.667
S4 max-throughput, 4 GPU, C256 [kappa-hi]             3,235      0.618      0.635      1.423      0.447
S4 max-throughput, 4 GPU, C256, MARLIN [kappa-lo]     1,270      1.575      1.619      3.625      1.137
S4 max-throughput, 4 GPU, C256, MARLIN [kappa-hi]     1,897      1.054      1.084      2.427      0.761
S1 interactive, 8 GPU, C114 (TPOT 50.0 ms) [k-lo]     2,281      1.754      1.803      4.036      1.267
S4, 8 GPU, C256 [kappa-lo]                            4,331      0.924      0.949      2.126      0.667
S4, 8 GPU, C256 [kappa-hi]                            6,471      0.618      0.635      1.423      0.446

=== $/1M INPUT tokens (prefill), 4 GPU ===
B12X + pinned-RAM Engram                             19,278     0.1037     0.1066     0.2388     0.0749
NVMe Engram                                           7,087     0.2822     0.2900     0.6495     0.2038
```

The 8-GPU S4 row has the same per-token cost as 4 GPUs because the roofline scales
linearly in GPU count and so does the bill: **there is no economy of scale on this card,
only a latency and capacity win.**

### 4.2 Blended, METHODOLOGY §6 verbatim

`blended = 0.75 × (0.5 × input + 0.5 × 0.10 × input) + 0.25 × output`, at the S1
operating point, with our-own-serving cached input at the §6 default of **10 % of
uncached prefill cost** (stated, not measured, per §6).

```
=== blended 75% in (50% cached @10%) / 25% out ===
operating point                                        $1.80      $1.85     $4.143      $1.30
S1 4 GPU C32 native  [kappa-lo]                       0.8073     0.8297     1.8582     0.5831
S1 4 GPU C32 native  [kappa-hi]                       0.5551     0.5705     1.2776     0.4009
S1 4 GPU C32 MARLIN  [kappa-lo]                       1.3483     1.3857     3.1033     0.9738
S1 4 GPU C32 MARLIN  [kappa-hi]                       0.9154     0.9408     2.1069     0.6611
S4 4 GPU C256 native [kappa-lo]                       0.2736     0.2812     0.6298     0.1976
S4 4 GPU C256 native [kappa-hi]                       0.1974     0.2028     0.4542     0.1425
S4 4 GPU C256 MARLIN [kappa-lo]                       0.4365     0.4486     1.0047     0.3152
S4 4 GPU C256 MARLIN [kappa-hi]                       0.3064     0.3149     0.7052     0.2213
S1 8 GPU C114 native [kappa-lo]                       0.4812     0.4946     1.1076     0.3475
S4 8 GPU C256 native [kappa-lo]                       0.2737     0.2813     0.6299     0.1976
```

The **8-GPU recommended shape is the one to quote**: it is the only configuration that
holds TPOT ≤ 50 ms at a concurrency (114) large enough to amortise the weight read, and
it costs **$1.754–$4.036 per 1M output** against the 4-GPU shape's $3.058–$7.039 — a 1.7×
improvement bought entirely by not being KV-starved. Its 8-GPU input leg assumes prefill
scales linearly from the 4-GPU measurement (19,278 → 38,556 tok/s) ⚠️ **TO BE VERIFIED**,
and it is the shape with the open CUDA-graph bug (§6 risk 4).

### 4.3 Effect of speculative decoding and prefix caching

- **DSpark is already priced in, and it is the largest lever on this pair.** κ folds in
  the measured accepted length 2.44–2.55 on the base checkpoint (0xSero acceptance
  78.9–86.4 %) and 2.16–2.88 on the V4-NVFP4 proxy (acceptance 58–94 %). Turn it off, or
  break it via the MXFP4 draft-expert misrouting, and every output figure above
  **multiplies by ~2.5**: S1 C32 at $1.80 goes from **$3.06** to **$7.65/1M output**;
  S4 C256 from $0.923 to **$2.31**. ⚠️ Distinguish these acceptance numbers from
  synthetic benchmark constants (e.g. the DSpark 3.51 used in the MI355X recipe is a
  synthetic figure, not a measurement); everything quoted here is an engine-reported
  acceptance on real traffic.
- **Prefix caching moves the input leg, which is already the cheap one.** At h = 90 % the
  input leg drops 10×, but the blended figure only falls from **$0.8073 → $0.7793/1M
  (−3.5 %)** at $1.80/κ-lo, because the 75/25 mix is dominated by the output term at
  these throughputs. On this pair prefix caching is a **TTFT** optimisation (6.80 s →
  0.68 s at 128K), not a cost one. ⚠️ **NVIDIA's own vLLM recipe for this checkpoint
  disables it** ([architecture.md §8.2](./architecture.md)); turn it on.
- **Engram in pinned RAM vs NVMe is a 2.7× input-cost lever**: $0.2822 vs $0.1037 per 1M
  input at $1.80. 188.83 GiB of host RAM is cheaper than that gap, and the dbirks A/B
  (row H) shows it is also a 1.4–4.3× *decode* lever.
- **Switching to the base MXFP4 checkpoint is a bigger lever than all of the above
  combined**: it recovers the native W4A8 expert path (up to 1.71× by row A/A′), the
  5.3 % expert-byte penalty, and 4.3× the 4-GPU KV budget.

### 4.4 Versus the vendor API, and break-even

DeepSeek first-party `deepseek-flash`, off-peak
([architecture.md §11.1](./architecture.md)): **output $0.60/M, input cache-miss $0.15/M,
cache-hit $0.003/M**; peak is exactly 2×. Its blended 75/25 at its own cached rate is
**$0.2074/1M**. Third-party FP4 endpoints go lower on input still: Relace **$0.13 in /
$0.52 out** ([architecture.md §11.2](./architecture.md)).

```
=== break-even aggregate output tok/s to match DeepSeek API $0.60/1M output ===
4 GPU @ $1.80 (Nebius)           $  7.20/h ->     3,333 agg tok/s (     833/GPU)
4 GPU @ $1.85 (Hyperstack SE)    $  7.40/h ->     3,426 agg tok/s (     856/GPU)
4 GPU @ $4.143 (AWS g7e)         $ 16.57/h ->     7,672 agg tok/s (   1,918/GPU)
4 GPU @ $1.30 (Hyperstack res.)  $  5.20/h ->     2,407 agg tok/s (     602/GPU)
8 GPU @ $1.80                    $ 14.40/h ->     6,667 agg tok/s (     833/GPU)
8 GPU @ $1.30                    $ 10.40/h ->     4,815 agg tok/s (     602/GPU)
```

At the S1 interactive point (654–976 agg tok/s on 4 GPUs) this pair reaches **20–29 %**
of break-even at $1.80 — i.e. it costs 3.4–5.1× the API price for output.

```
=== break-even UTILISATION vs the API's $0.2074/1M blended ===
operating point               $1.80 blended  util needed  $1.30 blended  util needed
S1 4GPU C32 native k-lo              0.8073        never         0.5831        never
S1 4GPU C32 native k-hi              0.5551        never         0.4009        never
S1 4GPU C32 Marlin k-lo              1.3483        never         0.9738        never
S1 4GPU C32 Marlin k-hi              0.9154        never         0.6611        never
S4 4GPU C256 native k-lo             0.2736        never         0.1976          95%
S4 4GPU C256 native k-hi             0.1974          95%         0.1425          69%
S4 4GPU C256 Marlin k-lo             0.4365        never         0.3152        never
S4 4GPU C256 Marlin k-hi             0.3064        never         0.2213        never
```

**Read the table as a whole.** There is **no interactive operating point, at any published
price, on either kernel path, that breaks even against DeepSeek's own list price.** The
only cells that break even at all are max-throughput, no-SLO, native-path, optimistic-κ
configurations at 69–95 % sustained utilisation — at a concurrency (256) **8× beyond
anything measured for this model on this GPU** (row B's ceiling is C4 before a crash;
row E's is C8). The reasons to run this pair are sovereignty, data residency, or the
vision path. They are not price, and the NVFP4 build makes the price worse than the base
checkpoint does.

**Input is the one leg where self-hosting wins**: $0.1037/M against the API's $0.15/M,
because CED makes a prompt token cost half a decode token in FLOPs
([architecture.md §6.1](./architecture.md): 15.09 vs 31.51 GFLOP/token).

---

## 5. Scaling and deployment shape

### 5.1 Single node vs multi-node

**Single node, always.** 4 GPUs is the minimum and is what every working
RTX PRO 6000 DeepSeek-V4.1 deployment uses; **8 GPUs in one chassis is the shape to
recommend for this checkpoint** because §1.2 shows 4 GPUs leaves 1.27 GB of KV at 0.90 —
a 78-sequence 8K server that cannot serve one 1M-context request. Sixteen GPUs needs two
chassis over a standard NIC and is ruled out by `gpus/rtx6000-pro.md` §4.

Chassis reality: Lenovo qualifies the passively-cooled Server Edition at **2× at 600 W
or 4× at a 450 W cap** in the SR675 V3 line, and `gpus/rtx6000-pro.md` §11 gotcha 12
notes *"8× in a server is a general-market configuration, not a universally-qualified
one"*. The 0xSero base-checkpoint run caps cards at **275 W** and still reaches 729 tok/s
at C8, consistent with §8d's finding that decode-bound work is nearly power-insensitive
on this card. Fix IOMMU/ACS in BIOS before benchmarking anything multi-GPU.

### 5.2 When PD disaggregation or wide-EP pays — they do not, here

- **PD disaggregation: no.** vLLM declares `"pd_cluster": {"rtx_pro_6000_8x": "unsupported"}`
  for the V4-Flash sibling and has no V4.1 profile at all;
  [serving-optimizations.md §3.5](../../cross-cutting/serving-optimizations.md) quotes a
  **20–30 % performance drop from disaggregation on small or untuned workloads** and the
  rule *"disaggregation is a thousand-GPU problem."* At 4–8 PCIe GPUs with no NVLink and
  no NIXL-class fabric, the KV transfer is paid per request and the gain never arrives.
  The architecture would suit it (prefill activates 8 B, decode 16 B — TokenSpeed runs a
  GB300 1P1D CI job for the *base* checkpoint), but not on this fabric.
- **Wide-EP: does not apply.** DeepEP/EPLB target NVL72-scale expert parallelism. At EP4
  the 384 experts split 96 per rank, already past the point where expert-read saturation
  matters ([architecture.md §6.3](./architecture.md): batch 32 touches 152/384 experts).
- **What does pay**, in order: (1) **CUDA graphs on decode** (17.8 → 109.3 tok/s measured,
  row B; ~1000× on the 8-GPU eager failure, row F); (2) **Engram in pinned host RAM**
  (26 → 113 tok/s, row H; 2.7× prefill); (3) **DSpark with the MXFP4 draft-expert routing
  patch** (+38 %, row A); (4) **prefix caching** (8–11× warm TTFT); (5) **using the base
  MXFP4 checkpoint instead** (§2).

### 5.3 Launch commands

**A. The vendor command for this exact checkpoint — GB300 only, quoted verbatim** from
[the model card](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md)
(*"Use `vllm/vllm-openai:deepseekv41-flash-0909` on four GB300 GPUs"*). **It is not an
sm_120 command** and will land on Marlin here:

```bash
vllm serve nvidia/DeepSeek-V4.1-Flash-NVFP4 \
    --tensor-parallel-size 4 \
    --data-parallel-size 1 \
    --tokenizer-mode deepseek_v41 \
    --reasoning-parser deepseek_v41 \
    --language-model-only \
    --max-model-len 1048576 \
    --max-num-seqs 32 \
    --max-num-batched-tokens 8192 \
    --enable-chunked-prefill \
    --no-enable-prefix-caching \
    --model-loader-extra-config '{"enable_multithread_load": true, "num_threads": 128}'
```

and the SGLang one (*"Use `lmsysorg/sglang:dev-cu13-dsv41` on four GB300 GPUs. The tested
build was SGLang commit `da64c5cbb8cf6bfd39be19da43573fdfd484c43a`"*; *"The tested build
automatically selects `flashinfer_trtllm_routed` for the NVFP4 experts"* — **a backend
that does not exist on sm_120**, since trtllm-gen has no SM120/121 cubins and NVIDIA has
*"no plan to SM120/121"*, [TensorRT-LLM #11799](https://github.com/NVIDIA/TensorRT-LLM/issues/11799)):

```bash
python -m sglang.launch_server \
    --model-path nvidia/DeepSeek-V4.1-Flash-NVFP4 \
    --served-model-name DeepSeek-V4.1-Flash-NVFP4 \
    --host 127.0.0.1 --port 30000 \
    --tp 4 \
    --context-length 1048576 \
    --reasoning-parser deepseek-v41 \
    --tool-call-parser deepseekv41 \
    --chunked-prefill-size 4096 \
    --max-running-requests 16 \
    --log-level info
```

**B. The nearest command that actually runs an NVFP4 DeepSeek checkpoint on this GPU**,
from the V4-Flash NVFP4 kit — two serve scripts, one per backend
[src](https://github.com/hikarioyama/dsv4-flash-nvfp4-sm120):
`serve_b12x_tp2.sh` (B12X native NVFP4, MTP working) and `serve.sh`
(`VLLM_TEST_FORCE_FP8_MARLIN=1`, weight-only FP4 dequant), image
`voipmonitor/vllm:chthonic-consecration-f1190eab-b12x0ff2847-pr20-cu132`, plus two
mount-overlay patches: *"`patches/quant_config.py` routes draft experts (layer index ≥
`num_hidden_layers`) to `Mxfp4MoEMethod`"* and *"`patches/nvfp4.py` widens the oracle
clamp gate for SILU activation."* ⚠️ `gpus/rtx6000-pro.md` §11 gotcha 17 warns
**`VLLM_TEST_FORCE_FP8_MARLIN=1` causes CUDA driver errors in spawned workers — use the
`--moe-backend marlin` CLI flag instead**; that warning was raised on a different model,
and this kit uses the env var successfully. Note the conflict.

**C. The nearest *official* vLLM sm_120 command**, verbatim from
[`recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash/hw/rtx_pro_6000_8x.json`](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash/hw/rtx_pro_6000_8x.json)
— **for the V4-Flash sibling at FP8, not this checkpoint**:

```bash
vllm serve deepseek-ai/DeepSeek-V4-Flash-0731 \
  --trust-remote-code \
  --kv-cache-dtype fp8 \
  --block-size 256 \
  --enable-expert-parallel \
  --tensor-parallel-size 8 \
  --attention_config.use_fp4_indexer_cache False \
  --moe-backend auto \
  --tokenizer-mode deepseek_v4 \
  --tool-call-parser deepseek_v4 \
  --enable-auto-tool-choice \
  --reasoning-parser deepseek_v4 \
  --speculative-config '{"method":"dspark","num_speculative_tokens":7,"draft_sample_method":"probabilistic"}'
```

with the recipe's own sm_120 caveats, verbatim: *"sm_120 cannot use the SM100 FP4 indexer
cache or `deep_gemm_mega_moe`. The generated command already sets
`--attention_config.use_fp4_indexer_cache False` and `--moe-backend auto`."*
For this checkpoint substitute `--tokenizer-mode deepseek_v41`, the `deepseek_v41`
parsers, `"num_speculative_tokens": 5` (`dspark_block_size: 5`), and add
`--compilation-config '{"cudagraph_mode":"FULL_AND_PIECEWISE"}'`,
`--enable-prefix-caching` and the Engram host-offload flag. **Untested as written** ⚠️.

### 5.4 Warm-up and loading time

```
full 527.27 GB                           1.76 min @ 5 GB/s   0.73 min @12 GB/s   0.44 min @20 GB/s
compute weights 324.52 GB                1.08 min @ 5 GB/s   0.45 min @12 GB/s   0.27 min @20 GB/s
Engram 202.76 GB -> pinned host RAM      0.68 min @ 5 GB/s   0.28 min @12 GB/s   0.17 min @20 GB/s
```

PCIe Gen5 ×16 host→GPU is **not** the bottleneck: 324.52 GB over four ×16 links at
64 GB/s is a **1.3 s** floor. Real wall-clock is dominated by NVMe reads, safetensors
parsing and kernel JIT. Two checkpoint-specific hazards:

- **Shards 47 and 48 are 101.54 GB each** (the two Engram tables). A naive
  single-threaded `safetensors` load stalls on them; that is exactly why NVIDIA's own
  recipe passes `--model-loader-extra-config '{"enable_multithread_load": true, "num_threads": 128}'`
  ([architecture.md §1.1](./architecture.md)). Keep it.
- Set a long engine-ready timeout (`VLLM_ENGINE_READY_TIMEOUT_S=3600`). On sm_120 add
  JIT time: CUDA < 13.2 with glibc ≥ 2.41 **fails to compile JIT kernels at all**
  (C23 `rsqrt` `noexcept` mismatch, `gpus/rtx6000-pro.md` §11 gotcha 14), and
  `TRITON_PTXAS_PATH` must point at the system `ptxas`.

---

## 6. Risks and open questions

**Blocking / decision-changing**

1. ⚠️ **No engine, vendor or community, has been shown serving this exact checkpoint on
   sm_120.** NVIDIA tested GB300/TP4 only; `quantization-formats.md` §9.6 assigns the
   cell `dequant ⚠️` with *"assume dequant until measured."* Everything in §3 and §4 is an
   estimate of an unvalidated configuration.
2. ⚠️ **NVFP4 backbone experts + MXFP4 MTP/DSpark experts in one process.**
   [architecture.md §7.3, §12.8](./architecture.md) calls this *"the most likely place for
   this checkpoint to break."* It **has broken**, on this GPU, on the V4-Flash NVFP4
   sibling: the loader routed draft experts through the NVFP4 path, *"destroying scale
   information and producing 'noise' with ~0 % token acceptance"*, fixed only by a
   mount-overlay patch routing them to `Mxfp4MoEMethod`
   [src](https://github.com/hikarioyama/dsv4-flash-nvfp4-sm120). Since DSpark carries
   ~2.5× of this pair's throughput, silently losing it is a 2.5× cost regression that
   produces no error message. **Verify acceptance rate on day one.**
3. ⚠️ **Which MoE backend actually gets selected, and whether it is correct.** Auto-selection
   lands on Marlin because a ModelOpt NVFP4 checkpoint resolves as **W4A16** while
   `FlashInferExperts._supports_quant_scheme` lists only the W4A4 pair for capability
   ≥ 100 (`gpus/rtx6000-pro.md` §6a). Two silent traps push users there with no warning:
   `flashinfer-python` / `flashinfer-cubin` version drift, and a stale system `nvcc`
   picked up when `CUDA_HOME` is unset. The historical failure mode of the *wrong* native
   path is not a crash — it is `"  ,   ,  (!!!!!!!!!!!"` output
   ([CUTLASS #3096](https://github.com/NVIDIA/cutlass/issues/3096)). **Run a correctness
   smoke test, not just a throughput test.**
4. ⚠️ **CUDA-graph capture on SM120 for V4.1-Flash is reported unusable** at 8 GPUs
   ([vLLM #56892](https://github.com/vllm-project/vllm/issues/56892), opened 2026-09-14,
   no maintainer response as of this pass), and graphs are worth ~1000× on that path.
   The 4-GPU community images work around it; the 8-GPU shape this document *recommends*
   on capacity grounds is the shape with the open bug.

**Kernel and precision**

5. ⚠️ Whether a first-class **32×32 block-scaled FP8 GEMM** exists on sm_120 for the 207 GB
   of FP8/MXFP8 tensors. TRT-LLM's sm120 row omits block-scaled FP8
   ([quantization-formats.md §5.1](../../cross-cutting/quantization-formats.md)); SGLang has
   open work (#39978, #39872, #39065); **DeepGEMM does not support `arch_major=12`** at all.
6. ⚠️ **The shipped 890 B/token FP4 KV cache is unreachable through any released engine
   here.** The kernel exists (FlashInfer PR #4955, merged 2026-09-07, SM120/121) and a
   community patch measures 1.47× tokens/GiB and +25–34 % decode on the V4 sibling on this
   GPU, but vLLM's SM12x branch still *requires* an FP8 dtype. This is 1.85× of §1.3's
   concurrency sitting behind a dtype gate. Track vLLM's
   `vllm/models/deepseek_v41/nvidia/flashinfer_sparse.py`.
7. ⚠️ Whether `index_topk = 512` resolves end-to-end on sm_120. PR #4955 lists *"primary
   top-k 128 or 512"* as supported and FlashInfer 0.6.18 adds SM120/121 top-k 192/256, so
   it should — unverified for V4.1.
8. ⚠️ **trtllm-gen will never ship SM120/121 cubins** (NVIDIA: *"no plan to SM120/121 so
   far"*), so SGLang's `flashinfer_trtllm_routed` — the backend NVIDIA's own tested build
   selects for these experts — is structurally unavailable here.
9. ⚠️ **Server vs Workstation Edition.** No cloud aggregator distinguishes 1,597 from
   1,792 GB/s, and every proxy measurement in §3.5 is Workstation/Max-Q silicon. Confirm
   the edition before trusting any rented benchmark; it is an 11 % TPOT swing.
10. ⚠️ **Dense-FLOPS disagreement** between `gpus/rtx6000-pro.md` §3c (1,920 FP4) and
    `flash-attention.md` §2 / METHODOLOGY §8 (2,000 FP4), 4 %. Immaterial here — the
    binding constraint is bandwidth, not FLOPS — but stated rather than picked silently.

**Checkpoint-specific**

11. ⚠️ `model.safetensors.index.json` still reports the **base** checkpoint's
    `total_size = 510,286,023,000`. Any loader that sizes an allocation from it
    under-allocates by 17 GB.
12. ⚠️ **`config.json` contradicts itself on activation scales**:
    `config_groups.group_0.input_activations.dynamic = false` versus the sibling key
    `activation_scheme: "dynamic"` ([architecture.md §1.2](./architecture.md)). Which one
    an engine honours — and therefore whether activation scales are recomputed per batch —
    is unresolved, and on a W4A4 path it is an accuracy question, not a cosmetic one.
13. ⚠️ **18 of 46,080 projection entries used fallback activation scales** (0.04 %). Whether
    they sit on hot layers is unknown.
14. ⚠️ **`S` (engine state slots per request) is assumed 1** for the 2.58 MiB SWA ring.
    A DSpark-speculating runtime plausibly allocates more for the 5-token block. Moves
    `max_concurrency` < 1 % at 8K, nothing at 1M.
15. ⚠️ **Multimodal is untested for this checkpoint on this GPU**, and NVIDIA's own vLLM
    recipe is `--language-model-only`. On the base checkpoint on 4× RTX PRO 6000, a
    six-frame video test **failed**; *"Do not treat video as supported by this release."*

**Accuracy of the executed precision**

16. NVIDIA's accuracy table is a **wash**, presented as a wash: NVFP4 wins 4 of 6
    benchmarks and loses 2, all within ±1.5 points
    ([architecture.md §10.1](./architecture.md)). The defensible claim is *"group-16 NVFP4
    does not cost accuracy versus group-32 MXFP4"*, and the card itself notes *"lossless
    weight conversion does not imply identical inference outputs."*
17. **On this GPU, that accuracy-neutrality is bought at a real cost and no benefit.** The
    default execution path here is Marlin W4A16 — NVFP4 weights dequantised to BF16 before
    the MMA — which is arithmetically *closer* to the source than either 4-bit tensor-core
    path, so the group-16 refinement buys nothing that the base MXFP4 checkpoint would not
    also buy through the same dequant. Infatoshi states the equivalence directly for the
    V4 sibling: *"Marlin runs W4A16 (bf16 activations): mathematically identical to serving
    the original MXFP4 checkpoint natively"* — ⚠️ a strong claim, and not exactly true
    across two different scale layouts, but the direction is right. Meanwhile the base
    checkpoint's experts **do** reach the sm_120 tensor cores natively at W4A8.
18. ⚠️ **W4A4 activation quantisation is untested on sm_120 for any DeepSeek checkpoint.**
    If the B12X native path is used, activations are FP4 — a precision the published
    accuracy evals were produced on GB300 `flashinfer_trtllm_routed`, not on an SM12x
    CuTe-DSL kernel. No eval exists for the sm_120 path.

**What would change the verdict**

A measured A/B of `nvidia/DeepSeek-V4.1-Flash-NVFP4` against
`deepseek-ai/DeepSeek-V4.1-Flash` on 8× RTX PRO 6000 Server Edition, same image, same
flags, with DSpark acceptance and a GSM8K/HumanEval pass reported for both. Until that
exists, §1's arithmetic stands on its own: **+16.99 GB of weights, −77 % of the 4-GPU KV
budget, −5.3 % of decode bandwidth, and the loss of the native expert GEMM.** Use the
base checkpoint.

---

## Sources

**This checkpoint and the base model**
- https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4
- https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md
- https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/blob/main/config.json
- https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/resolve/main/model.safetensors.index.json
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/README.md
- https://api-docs.deepseek.com/quick_start/pricing

**Measurements on this GPU (community, first-hand)**
- https://github.com/hikarioyama/dsv4-flash-nvfp4-sm120 — V4-Flash **NVFP4** on 2× RTX PRO 6000, B12X native vs Marlin, MTP/MXFP4 routing fix
- https://github.com/Infatoshi/dsv4-flash-2x-rtxpro6000s — V4-Flash-0731-**NVFP4** on 2× RTX PRO 6000 WS, 13-patch vLLM, full optimisation ladder
- https://github.com/danielwoz/vllm-dspark-nvfp4 — true **NVFP4 E2M1 KV cache** for DeepSeek-V4 sparse-MLA on SM120
- https://github.com/ambientlight/rtx-pro-6000-bench/blob/main/docs/DEPLOY-MXFP4-W4A4-DEEPSEEK-V4-FLASH-SM120.md — native **MXFP4 W4A4** fused MoE on 4× RTX PRO 6000
- https://github.com/local-inference-lab/rtx6kpro/blob/master/models/deepseek-v4.1-flash.md — **V4.1-Flash base** on 4× RTX PRO 6000
- https://github.com/0xSero/deepseek-v4.1-flash-4x-rtx-pro-6000 — **V4.1-Flash base** on 4× RTX PRO 6000, 45-case grid
- https://github.com/dbirks/home-k8s/issues/127 — V4.1-Flash Engram placement A/B, why an NVFP4 quant does not solve the 96 GB problem
- https://www.millstoneai.com/inference-benchmark/deepseek-v4-flash-fp4fp8-2x-rtx-pro-6000-blackwell
- https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash/discussions/28

**Engine and kernel status**
- https://github.com/vllm-project/vllm/issues/56892 — SM120 CUDA-graph collapse on V4.1-Flash
- https://github.com/vllm-project/vllm/issues/31085 — MXFP4/NVFP4 backend selection on SM120, `--moe-backend flashinfer_b12x`
- https://github.com/vllm-project/vllm/issues/38971 — Marlin vs FlashInfer-CUTLASS on SM120
- https://github.com/NVIDIA/cutlass/issues/3096 — NVFP4 MoE grouped GEMM on SM120, `compute_120f` fix
- https://github.com/NVIDIA/TensorRT-LLM/issues/11799 — no trtllm-gen SM120/121 cubins; no `QE4m3KvE2m1` SM12x kernel
- https://github.com/flashinfer-ai/flashinfer/pull/4955 — NVFP4 sparse MLA for SM120/121, merged 2026-09-07
- https://github.com/sgl-project/sglang/issues/39302 · /39807 — SM120 shared-memory ceiling, FP8 prefill kernel gap
- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash/hw/rtx_pro_6000_8x.json
- https://nvidia.github.io/TensorRT-LLM/models/supported-models.html

**Hardware and price**
- https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/ — 96 GB GDDR7, **1597 GB/s**
- https://lenovopress.lenovo.com/lp2263-thinksystem-nvidia-rtx-pro-6000-blackwell-server-edition-pcie-gen5-gpu

**Repo-local (all numbers inherited from these are cited inline by section)**
- [`research/METHODOLOGY.md`](../../METHODOLOGY.md) §1–§4, §6, §8
- [`research/models/deepseek41fnvfp4/architecture.md`](./architecture.md) §1.1, §1.2, §2, §3.3, §4, §5, §6.1–6.3, §7, §8.2–8.4, §10.1–10.2, §11, §12
- [`research/models/deepseek41f/rtx6000-pro.md`](../deepseek41f/rtx6000-pro.md) — the base-checkpoint pair, source of the κ calibration measurements and the prefill anchors
- [`research/gpus/rtx6000-pro.md`](../../gpus/rtx6000-pro.md) §1–§5, §6a–6d, §7, §9a–9g, §11, §12
- [`research/cross-cutting/flash-attention.md`](../../cross-cutting/flash-attention.md) §2, §3, §6.2, §7, §9.5, §11
- [`research/cross-cutting/quantization-formats.md`](../../cross-cutting/quantization-formats.md) §5.1, §9.1, §9.2, §9.6
- [`research/cross-cutting/inference-engines.md`](../../cross-cutting/inference-engines.md) §3.8
- [`research/cross-cutting/serving-optimizations.md`](../../cross-cutting/serving-optimizations.md) §1.2, §1.5, §3.5
- [`research/cross-cutting/cloud-pricing.md`](../../cross-cutting/cloud-pricing.md) §5.10, §5.13, §9

---

## Audit log (2026-09-19)

Independent `python3` recomputation of every derived table in this document against
METHODOLOGY.md's formulas and the stated inputs from
[`architecture.md`](./architecture.md) §3–§6 and
[`gpus/rtx6000-pro.md`](../../gpus/rtx6000-pro.md) §2, §3, §8, §9. **Result: no cell was
wrong by more than 5 %; nothing was changed.**

Recomputed and verified exact (or within sub-0.5 % rounding of the printed 2-decimal
figures):

1. **§1.1–§1.2 fit** — weight/Engram/compute-weight byte splits, `usable_hbm` at 0.90/0.95,
   the four `min GPUs` ratios (6.399 / 3.938 / 3.732 / 6.193), and the full 10-row
   `n / Engram / W-GPU / KV@.90 / KV@.95 / agg@.90` table. All cells matched exactly.
2. **§1.3 max concurrency** — per-sequence KV bytes at FP4/FP8/BF16 × {8K,32K,128K,1024K}
   (confirmed the BF16 row correctly switches to the 5,638,656 B BF16 SWA-ring fixed state,
   not the FP8 ring's 2,703,360 B), and every `floor(budget/per-seq)` cell for the 4-GPU,
   8-GPU (host and HBM Engram), 16-GPU and base-checkpoint-contrast rows, at both 0.90 and
   0.95 utilisation. All matched exactly, including the `infeasible (KV)` calls.
3. **§3.1 κ calibration table** — reproduced `bytes_step(b, ctx)` from the stated MoE
   distinct-expert formula for all six (local-inference-lab / 0xSero) rows, confirmed the
   `roofline = b × (n × BW_workstation) / bytes_step` and `κ = meas / roofline` arithmetic,
   and the stated band (0.417–0.623) and mean (0.513).
4. **§3.1 NVFP4-vs-MXFP4 isolated penalty table** — confirmed the six `GB/step` pairs match
   the S2 (32K) column of §3.2/§3.3's own decode table exactly, and that the stated
   `throughput Δ%` uses `(mx − nv) / nv` (throughput ∝ 1/bytes), not `/mx`; reproduces
   −1.97 % … −5.26 % exactly.
5. **§3.2 / §3.3 decode throughput** — recomputed `bytes_step(b, ctx)` for
   b ∈ {1,8,32,64,128,256} × {S1,S2,S3} on 4 and 8 GPUs, then `tok/s/GPU`, `agg tok/s`
   (`κ × b × n × BW / bytes_step`), `TPOT`, and the 0.586 Marlin-ratio column. Also
   recomputed the `caps @util 0.90/0.95` header rows and confirmed they use
   **`ctx = input + output` tokens** (e.g. S1 = 4096+512 = 4608), not the bare input length —
   this resolves what first looked like an 8–9 % gap and is not a bug. Recomputed the
   "largest b at TPOT ≤ 50 ms (κ-lo)" figures (32 / 32 / 32 at 4 GPU; 114 / 112 / 108 at
   8 GPU) by direct search — all matched.
6. **§3.4 TTFT** — `ctx / prefill_rate` and the 90 %-hit `× (1 − h)` reduction for both
   Engram placements × {S1,S2,S3}. All six pairs matched exactly.
7. **§4.1 $/1M output and input** — `n_gpus × price / (agg_tok/s × 3600) × 1e6` against
   every price tier (Nebius $1.80, Hyperstack $1.85, AWS $4.143, Hyperstack reserved $1.30)
   for the native, Marlin, S1 and S4, 4-GPU and 8-GPU rows, plus the two input-cost rows.
   All matched exactly.
8. **§4.2 blended cost** — `0.4125 × input + 0.25 × output` (the expansion of
   `0.75 × (0.5 × input + 0.5 × 0.10 × input) + 0.25 × output`) against every row; all
   matched exactly, including that 8-GPU input cost is invariant to GPU count (linear
   scaling cancels, as the prose states).
9. **§4.4 break-even output tok/s** — `cost_per_hour × 1e6 / (0.60 × 3600)` for all six
   rows; matched exactly.
10. **§0 verdict** — cross-checked items 4 and 5's dollar ranges against the §4.1/§4.2
    tables they cite (8-GPU/C114, 4-GPU/C32, 4-GPU Marlin, native and blended, S4
    max-throughput). All matched.
11. **§5.4 warm-up/loading times** — `bytes / bandwidth` at 5/12/20 GB/s for the full
    checkpoint, compute-weights-only and Engram-only splits, and the 4×PCIe-Gen5 floor
    (324.52 GB ÷ 256 GB/s = 1.3 s). All matched.
12. Spot-checked unit hygiene (GB 10⁹ vs GiB 2³⁰ labelled correctly throughout), that the
    dense (not sparse) FP4/FP8/BF16 TFLOPS from `gpus/rtx6000-pro.md` §3c are the ones in
    use, and that per-GPU vs aggregate figures are not swapped anywhere checked (the
    `agg@.90` and `agg tok/s` columns are consistently `per-GPU × n_gpus`).

**Not independently re-derivable from the required source set** (external field
measurements cited by URL in §3.5, and the base-checkpoint prefill/κ source numbers that
live in `models/deepseek41f/rtx6000-pro.md`, which was out of scope for this pass) — taken
as given, as the document itself already marks them `est.`/⚠️ where appropriate.

No contradictions with the foundation docs were found beyond the ones this document
already states explicitly and does not silently resolve (Server- vs Workstation-Edition
source silicon, the 1,920 vs 2,000 dense-FP4 TFLOPS spread between `gpus/rtx6000-pro.md`
§3c and METHODOLOGY §8, and the correction this document itself logs against
`gpus/rtx6000-pro.md` §9g's "needs at least 8 cards" claim). No edits were made to any
number, table, or prose in this document.

**2026-09-19, final consistency pass.** §0 item 5 quoted **"$0.62–$2.13"** for S4
max-throughput, mixing §4.1's `kappa-hi` Nebius cell ($0.618) with its `kappa-lo` AWS cell
($2.126) — two different calibration scenarios in one band. §0 item 4's interactive range
uses `kappa-lo` throughout, so item 5 was recut to the same scenario: **$0.92–$2.13**
(`kappa-lo`, matching `matrix/pairs.json`'s already-correct $0.924–$2.126), with the
`kappa-hi` reading ($0.62–$1.42) kept alongside for reference. `pairs.json` did not change.
