# GPU-specific optimizations — kernels, formats and acceleration by GPU

**Research date: 2026-09-19.** Follows [`METHODOLOGY.md`](../METHODOLOGY.md): every number
here is transcribed from a document in this tree that carries its own `[src]`, or is marked
**⚠️ TO BE VERIFIED**. No number is introduced that is not already in
[`cross-cutting/flash-attention.md`](../cross-cutting/flash-attention.md),
[`cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md),
[`gpus/<gpu>.md`](../gpus/), [`models/<exp>/<gpu>.md`](../models/) or
[`pairs.json`](./pairs.json). Where two documents in this tree disagree, both are cited and
the disagreement is stated. All TFLOPS are **dense**. Every table row links to its source.

This is the direct answer to: *which FlashAttention versions run on which GPU, and which
quantization formats are actually accelerated there.* Sizing, throughput and cost per
(model, GPU) live in the pair docs and in [`pairs.json`](./pairs.json); this document is
about **kernels and formats only**.

**Legend.** ✅ supported *and* the architecture's tuned path · 🟡 supported but not the tuned
path (emulation, SM80-MMA fallback, wrapper-level page emulation, dequant-before-MMA) ·
❌ unsupported · ⚠️ conditional, engine-version-dependent or unverified · `est.` derived,
not measured · `meas.` published measurement.

**GPU bins, because two of these columns are two parts.** HGX/DGX **B300** and **GB300
NVL72** are different clock/capacity bins of `sm_103`: 2,250 / 4,500 / **13,500** dense
BF16/FP8/FP4 TFLOPS at 268 GB per GPU versus 2,500 / 5,000 / **15,000** at 288 GB
(≈ 279 usable) — [METHODOLOGY §8](../METHODOLOGY.md),
[flash-attention.md §1.1](../cross-cutting/flash-attention.md),
[quantization-formats.md §4.1](../cross-cutting/quantization-formats.md). **Blackwell
Ultra's 1.5× uplift over B200 is FP4-only; BF16 and FP8 do not move at all.** Kernel gating
does not distinguish them — `sm_103` satisfies every `major == 10` /
`is_device_capability_family(100)` / `arch // 10 == 10` gate — so the two share every ✅/❌
in §1 and differ only in the rate columns.

---

## 1. Attention kernel matrix

### 1.1 Support and optimization, kernel × GPU

Transcribed from [flash-attention.md §9.1–§9.7](../cross-cutting/flash-attention.md) (the
per-GPU sections and the consolidated grid), §14.3 (GDN) and §14.4 (KDA). B300 and GB300
share one column because every gate they hit is a `major == 10` family gate
([flash-attention.md §9.4](../cross-cutting/flash-attention.md)).

| Kernel | A100 `sm_80` | H100 `sm_90` | H200 `sm_90` | B200 `sm_100` | B300 / GB300 `sm_103` | RTX PRO 6000 `sm_120` | MI355X `gfx950` |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| **FA2** | ✅ (design target) | 🟡 superseded | 🟡 superseded | 🟡 | 🟡 | 🟡 **this is what vLLM's `FLASH_ATTN` gives you** | ✅ (CK backend) |
| **FA3** | ❌ | ✅ **the Hopper path** | ✅ | ❌ *"Cannot use FA version 3 on Blackwell"* | ❌ | ❌ (9.x only) | ❌ (CUDA-only) |
| **FA4** (vLLM wheel) | ❌ | ✅ | ✅ | ✅ **default on major 10** | ✅ + `sm103a` `tcgen.ld.red` path (PR #2696, beta22) | ❌ — *"FA4 is only supported on … 9.x, 10.x, or 11.x"* | ❌ |
| **FA4** (upstream fwd) | 🟡 SM80-MMA path, untested | ✅ | ✅ | ✅ | ✅ | 🟡 `FlashAttentionForwardSm120` = *"SM80 MMA with SM120 SMEM capacity"*; no block sparsity, **no paged KV**, no SplitKV, no FP8 | ❌ |
| **FA4 FP8 attention** | ❌ | ❌ — `assert arch // 10 == 10` | ❌ | ✅ ⚠️ young (accuracy fixes in beta23/24) | ✅ ⚠️ | ❌ | ❌ |
| **FA4 hd256 2-CTA** (`BlackwellFusedMultiHeadAttentionForward`) | ❌ | ❌ | ❌ | ✅ **block size must be a multiple of 128** | ✅ | ❌ (`arch // 10 in [10, 11]`) | ❌ |
| **FlashInfer native** | ✅ (8.x–9.x row) | ✅ | ✅ | ✅ | ✅ | ⚠️ vLLM's table leaves 12.x uncovered for standard attention although FlashInfer's own README lists SM 12.0/12.1 | ❌ |
| **FlashInfer XQA** (TRT-LLM decode) | ❌ | ✅ **9.0 only** | ✅ | ❌ | ❌ | ⚠️ via SGLang `trtllm_mha`, *"Optimized for SM90 and SM120"*, page size 64 | ❌ |
| **FlashInfer trtllm-gen FMHA** | ❌ | ❌ | ❌ | ✅ sinks ✅, `nvfp4` KV ✅ | ✅ | ❌ (`supported_major_versions=[10]`) | ❌ |
| **FlashInfer MLA / MLA_SPARSE** | ❌ | ⚠️ `FLASHINFER_MLA_SPARSE_SM90` only (hd 512/576, block %64) | ⚠️ same | ✅ | ✅ + cluster-aware CUTLASS `split_kv` (v0.6.15) | ⚠️ `FLASHINFER_MLA_SPARSE_SM120`, block 64/256 | ❌ |
| **FlashMLA dense decode** | ❌ | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ |
| **FlashMLA dense prefill (MHA)** | ❌ | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ |
| **FlashMLA sparse prefill** | ❌ | ✅ | ✅ | ✅ | ✅ | ❌ | ❌ |
| **FlashMLA sparse decode** | ❌ | ✅ V3.2/V4 — ⚠️ V4.1 contested, see §8 Q1 | ✅ ⚠️ | ✅ incl. V4.1 | ✅ (empirical; FlashMLA lists no SM103 row) | ❌ | ❌ |
| **FlashMLA mega-attn `FLASHMLA_MEGA_ATTN_DSV41`** (fused norm+RoPE+attn+RoPE+cast; **the only `nvfp4_ds_mla` consumer**) | ❌ | ❌ *"the kernel has no SM90 instantiation"* | ❌ | ✅ `major == 10` | ✅ | ❌ | ❌ |
| **DSA / CSA2 sparse (any vLLM backend)** | ❌ all gates start at major 9 | ✅ `FLASHMLA_SPARSE_DSV41`, block **64** | ✅ block 64 | ✅ block 128 | ✅ block 128 | ✅ `FLASHINFER_MLA_SPARSE_DSV41`, **FP8 KV dtype mandatory** | ⚠️ `ROCM_AITER_MLA_SPARSE` / `ROCM_FLASHMLA_SPARSE_DSV4`, breakable CUDA graphs |
| **Lightning-indexer / top-k kernel** | ❌ | ✅ DeepGEMM FP8 MQA-logits + FlashInfer radix top-k | ✅ | ✅ `flashinfer.top_k_varlen` Blackwell radix / SGLang Lightning TopK | ✅ + FlashInfer HCA FP8 sparse-MLA decode (v0.6.18, SM100 **and** SM103) | ✅ `top_k_varlen` (SM120/121 picked up top-k 192/256 in v0.6.18) | ⚠️ **no primary source** for a gfx950 DSA/indexer kernel |
| **Hybrid linear — GDN prefill (FlashInfer)** | ❌ (Triton/FLA) | ✅ no further constraints | ✅ | ✅ needs `head_k_dim == 128` + CUDA ≥ 13 | ✅ same | ✅ family 120, same conditions | ❌ (AITER Triton) |
| **Hybrid linear — CuteDSL GDN prefill** | ❌ | ❌ | ❌ | ✅ opt-in | ✅ opt-in | ❌ *"targets SM100 only, so it stays off here"* | ❌ |
| **Hybrid linear — GDN decode (fused CUDA)** | ✅ `has_device_capability(80)` | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ Triton |
| **Hybrid linear — KDA decode, native fused CUDA** | ❌ | ✅ `is_device_capability(90)` | ✅ | ✅ | ✅ | ✅ family 120 | ❌ (CUDA-capability gate) |
| **Hybrid linear — KDA decode, FlashInfer `fused_kda_decode`** | ❌ | ❌ | ❌ | ✅ `(10,0)` | ✅ `(10,3)` **explicit** | ❌ | ❌ |
| **TRT-LLM FMHA / MLA** | ❌ | ⚠️ legacy flow; FP8 context FMHA *"only supported on Ada and Hopper"* | ⚠️ same | ✅ trtllm-gen / `trtllm_mla` | ✅ | ❌ no SM120/121 cubins | ❌ |
| **CK FlashAttention-2 / AITER** | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ CK default; ⚠️ the tuned ASM FMHA kernels are **open PRs** |
| **Triton attention** | ✅ correct, ❌ fast | 🟡 | 🟡 | 🟡 | 🟡 | 🟡 the practical general-purpose fallback | 🟡 |
| **FP8 KV-cache attention** | 🟡 store-only (Triton/FlashInfer); ❌ on the FA path | ✅ FA3 runs Q·Kᵀ and P·V in FP8 | ✅ | ✅ FA4 | ✅ | 🟡 not via FlashAttention (needs FA3@9.x or FA4@10.x) | ✅ AITER, `VLLM_ROCM_USE_AITER=1` |
| **NVFP4 KV-cache attention** | ❌ | ❌ | ❌ | ✅ `nvfp4`, `nvfp4_ds_mla`, `nvfp4_4over6` | ✅ | ⚠️ **kernel exists, no engine routes to it** — see §5 | ❌ |

### 1.2 Measured attention TFLOPS and % of dense BF16 peak

Every row is a published measurement with its % of the §1.3 denominator computed in this
tree ([flash-attention.md §2.5, §5.2, §8.3, §9.1–§9.6](../cross-cutting/flash-attention.md);
A100 from [gpus/a100.md §5](../gpus/a100.md)).

| Kernel | GPU | Measured | % of dense BF16 peak | Source doc |
|---|---|---:|---:|---|
| FA2 forward, FP16/BF16 | A100 | **230 TFLOPS** | 73–74 % | [gpus/a100.md](../gpus/a100.md) |
| FA3 forward, BF16 hd128 | H100 | **~740 TFLOPS** (*"75 % utilization"*) | 74.8 % | [flash-attention.md §2.5](../cross-cutting/flash-attention.md) |
| FA4 forward, BF16 hd128 | B200 | **1,613 TFLOPS** (*"71 % hardware utilization"*) | 71.7 % | [flash-attention.md §2.5](../cross-cutting/flash-attention.md) |
| FA4 forward | B300 / GB300 | **none published** ⚠️ — plan 1,613 (HGX B300, scale 1.0×) or ~1,790 `est.` (GB300 NVL72, scale 1.11×) | — | [flash-attention.md §2.5, §9.4](../cross-cutting/flash-attention.md) |
| FA4 vs cuDNN 9.13 / vs Triton | B200 | up to **1.3×** / up to **2.7×** | — | [flash-attention.md §2.5](../cross-cutting/flash-attention.md) |
| FlashMLA dense MLA decode, memory-bound | H800 | **3,000 GB/s** | ~89 % of 3.35 TB/s ⚠️ (H800 BW unsourced) | [flash-attention.md §5.2](../cross-cutting/flash-attention.md) |
| FlashMLA dense MLA decode, compute-bound | H800 | **660 TFLOPS** | 66.7 % | [flash-attention.md §5.2](../cross-cutting/flash-attention.md) |
| FlashMLA **sparse prefill** | H800 | **640 TFLOPS** | 64.7 % | [flash-attention.md §5.2](../cross-cutting/flash-attention.md) |
| FlashMLA **sparse prefill** | B200 | **up to 1,450 TFLOPS** | 64.4 % | [flash-attention.md §5.2](../cross-cutting/flash-attention.md) |
| FlashMLA **sparse decode** (FP8 KV, BF16 matmul) | H800 | **410 TFLOPS** | 41.4 % | [flash-attention.md §5.2](../cross-cutting/flash-attention.md) |
| FlashMLA **sparse decode** | B200 | **up to 700 TFLOPS** | 31.1 % | [flash-attention.md §5.2](../cross-cutting/flash-attention.md) |
| FlashMLA dense MHA prefill fwd / bwd | B200 | **1,460 / 1,000 TFLOPS** | 64.9 % / 44.4 % | [flash-attention.md §5.2](../cross-cutting/flash-attention.md) |
| FlashMLA fused norm-RoPE-attn-RoPE-cast, prefill / decode | B200 | **1,430 / 670 TFLOPS** | 63.6 % / 29.8 % | [flash-attention.md §5.2](../cross-cutting/flash-attention.md) |
| CK FA2 fwd, BF16 hd256 (baseline) | **MI350X**, not MI355X | **436 TFLOPS** | 17.4 % ⚠️ cross-part denominator | [flash-attention.md §8.3](../cross-cutting/flash-attention.md) |
| AITER ASM FMHA fwd, BF16 hd256, non-causal / causal | **MI350X** ⚠️, open PR | **497 / 509 TFLOPS** (1.14× / 1.17× CK) | 19.9 % / 20.4 % ⚠️ | [flash-attention.md §8.3](../cross-cutting/flash-attention.md) |
| AITER ASM FMHA bwd, BF16 hd256 | MI355X, open PR | **676 TFLOPS** | 27.0 % | [flash-attention.md §8.3](../cross-cutting/flash-attention.md) |
| FlashInfer `fused_kda_decode` vs vLLM native fused | B200 / B300 | **1.33×** at one row, geomean **1.13×** | — | [flash-attention.md §14.4](../cross-cutting/flash-attention.md) |
| FlashInfer NVFP4 sparse MLA vs FP8 default | **RTX PRO 5000** ⚠️, not a 6000 | prefill **1.41–1.67×**, decode **1.31×**, ~9 % end-to-end | — | [flash-attention.md §3.1, §9.5](../cross-cutting/flash-attention.md) |
| Any attention TFLOPS at all | **RTX PRO 6000** | **none published** ⚠️ | — | [flash-attention.md §15.4](../cross-cutting/flash-attention.md) |

### 1.3 Denominators used above

| GPU | Dense BF16 | Dense FP8 | Dense FP4 | HBM BW | Doc |
|---|---:|---:|---:|---:|---|
| A100 SXM 80GB | 312 | none (INT8 624 TOPS) | none | 2.04 TB/s | [gpus/a100.md](../gpus/a100.md) |
| H100 SXM | 989.5 | 1,979 | none | 3.35 TB/s | [gpus/h100.md](../gpus/h100.md) |
| H200 SXM | 989.5 | 1,979 | none | 4.8 TB/s | [gpus/h200.md](../gpus/h200.md) |
| B200 HGX | 2,250 | 4,500 | 9,000 | 7.7–8 TB/s | [gpus/b200.md](../gpus/b200.md) |
| B300 HGX/DGX/p6 | 2,250 | 4,500 | **13,500** | 8.0 TB/s | [gpus/b300.md](../gpus/b300.md) |
| GB300 NVL72 (per B300) | 2,500 | 5,000 | **15,000** | 8.0 TB/s | [gpus/gb300.md](../gpus/gb300.md) |
| RTX PRO 6000 Server Ed. | **500** `est.` ⚠️ | 1,000 `est.` | ≈ 2,000 `est.` | 1.597 TB/s | [gpus/rtx6000-pro.md](../gpus/rtx6000-pro.md) |
| MI355X | 2,500 ⚠️ second-hand | 5,000 | 10,100 (MXFP4 **and** MXFP6) | 8.0 TB/s | [gpus/mi355x.md](../gpus/mi355x.md) |

⚠️ **Disagreement, stated per METHODOLOGY §8.** [METHODOLOGY §8](../METHODOLOGY.md) and
[flash-attention.md §1.1](../cross-cutting/flash-attention.md) pin the **Server Edition** at
**500 / 1,000 / 2,000** dense (½ of the product page's sparse PFLOPS rows).
[gpus/rtx6000-pro.md §3](../gpus/rtx6000-pro.md) additionally carries a **Workstation
Edition** whitepaper table at **503.8 / 1,007.6 / 2,015.2** dense (1,792 GB/s) and a
clock-derived Server-Edition estimate of **480 / 960 / 1,920**. The three differ by ≤ 5 %.
Use 500 / 1,000 / 2,000 with 1,597 GB/s for the Server Edition, and never quote 1,792 GB/s
for it. NVIDIA publishes no explicitly-dense tensor row for either SKU — [quantization-formats.md
open question 1](../cross-cutting/quantization-formats.md).

⚠️ MI355X's 2,500 is pinned by METHODOLOGY §8 but
[flash-attention.md §1.1](../cross-cutting/flash-attention.md) records that the ROCm CDNA4
page METHODOLOGY attributes it to *contains no FLOPS row at all*, and `amd.com` timed out on
re-fetch. The value is corroborated by AMD's brochure; the attribution is not.

---

## 2. Attention acceleration, normalised to H100 = 1.0

Two separate normalisations, because **prefill and decode do not scale together** — this is
the single most consequential finding in
[flash-attention.md §15.2](../cross-cutting/flash-attention.md).

### 2.1 Dense BF16 forward attention, head dim 128 (prefill-shaped)

| GPU | Kernel | Measured TFLOPS | **vs H100 FA3** | Basis |
|---|---|---:|---:|---|
| A100 | FA2 | 230 `meas.` | **0.31×** | 230 ÷ 740 ([gpus/a100.md](../gpus/a100.md), [flash-attention.md §2.5](../cross-cutting/flash-attention.md)) |
| **H100** | **FA3** | **740** `meas.` | **1.00×** | reference |
| H200 | FA3 | 740 `meas.` | 1.00× | same die, *"every kernel gate above is identical"* ([flash-attention.md §9.2](../cross-cutting/flash-attention.md)) |
| B200 | FA4 | 1,613 `meas.` | **2.18×** | [flash-attention.md §15.1](../cross-cutting/flash-attention.md) |
| B300 HGX | FA4 | ~1,613 `est.` ⚠️ | **2.18×** `est.` | dense BF16 peak is 2,250, identical to B200 → scale 1.0× ([flash-attention.md §15.1](../cross-cutting/flash-attention.md)) |
| GB300 NVL72 | FA4 | ~1,790 `est.` ⚠️ | **~2.42×** `est.` | B200 scaled by 2,500/2,250 = 1.11× ([flash-attention.md §2.5, §15.1](../cross-cutting/flash-attention.md)) |
| RTX PRO 6000 | **FA2** (vLLM refuses FA4 on 12.x) | **none published** ⚠️ | ⚠️ **unknown** | no attention TFLOPS exists for this part at all ([flash-attention.md §15.4](../cross-cutting/flash-attention.md)); the peak ratio alone is 500 ÷ 989.5 = 0.51×, which is a *ceiling*, not a measurement |
| MI355X | CK FA2 | 436 at **hd256** on **MI350X** ⚠️ | ⚠️ **not a valid ratio** | *"different head dim, different kernel generation, different batch/sequence config, and now a different AMD part"* — [flash-attention.md §15.3](../cross-cutting/flash-attention.md) explicitly refuses this comparison |

**Read the efficiency column, not just the ratio.** FA4-on-Blackwell achieves 71 % of peak
against FA3-on-Hopper's 75 % — **0.96× kernel efficiency**. The entire 2.18× is silicon
([flash-attention.md §15.1](../cross-cutting/flash-attention.md)).

### 2.2 Sparse MLA — the DeepSeek path (V4.1-Flash, and MLA-shaped decode generally)

| Phase | H800 `sm_90` | B200 `sm_100` | **B200 ÷ H800** | Efficiency B200 vs H800 |
|---|---:|---:|---:|---:|
| Sparse MLA **prefill** | 640 TFLOPS (64.7 % peak) | 1,450 TFLOPS (64.4 % peak) | **2.27×** | 1.00× — tracks the peak ratio exactly |
| Sparse MLA **decode** | 410 TFLOPS (41.4 % peak) | 700 TFLOPS (31.1 % peak) | **1.71×** | **0.75×** — the kernel cannot feed Blackwell's tensor cores |

[flash-attention.md §15.2](../cross-cutting/flash-attention.md): *"If your workload is
decode-dominated and MLA-shaped — which DeepSeek-V4.1-Flash and Kimi-K3 both are — the
Blackwell uplift over Hopper is closer to **1.7×** than to the 2.2–2.3× the datasheets
suggest."*

### 2.3 What does not exist

⚠️ **TO BE VERIFIED**, all of them
([flash-attention.md §15.4](../cross-cutting/flash-attention.md)): any FA4 measurement on
H100 (so FA3→FA4 on Hopper is unquantified); any FA4 or FlashMLA measurement on B300/GB300;
**any attention TFLOPS number for RTX PRO 6000 Blackwell**; any like-for-like MI355X ↔
B200/B300 attention comparison — AMD's own MLA-decode tracking issue (open since 2026-03-26)
admits earlier MI355X MLA numbers were taken with SDPA instead of the MLA kernel; any
FP8-attention-vs-BF16-attention speedup on any GPU in this roster; any per-GPU indexer/top-k
kernel latency table.

---

## 3. Quantization format matrix

### 3.1 Silicon support and peak uplift vs BF16

Support cells from
[quantization-formats.md §4.2, §5, §8.1](../cross-cutting/quantization-formats.md); the
uplift is the ratio of that GPU's own dense peaks from §1.3 — i.e. a **ceiling**, not a
measured speedup. Measured speedups are in §3.2, and they are much smaller.

✅ native tensor/matrix core · 🟡 software unpack / emulate (memory win kept, compute win
lost) · ❌ not usable.

| Format | A100 | H100 | H200 | B200 | B300 HGX | GB300 NVL72 | RTX PRO 6000 | MI355X |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| **BF16 / FP16** | ✅ 1.0× | ✅ 1.0× | ✅ 1.0× | ✅ 1.0× | ✅ 1.0× | ✅ 1.0× | ✅ 1.0× | ✅ 1.0× |
| **FP8 E4M3 per-tensor** | ❌ | ✅ **2.0×** | ✅ **2.0×** | ✅ **2.0×** | ✅ **2.0×** | ✅ **2.0×** | ✅ **2.0×** | ✅ **2.0×** (OCP-FP8) |
| **FP8 block-scaled** (128×128 FP32, or 32×32 UE8M0) | ❌ | ✅ 2.0× (128×128 native; 🟡 the 32×32 UE8M0 layout needs software fixup) | ✅ same | ✅ 2.0× — *re-expressed* to the MXFP8 recipe, not run bit-identically | ✅ same | ✅ same | ✅ per-tensor; ⚠️ **block-scaled FP8 is absent from TRT-LLM's sm120 row** | ✅ 2.0× |
| **MXFP8** (block-32 UE8M0) | ❌ | 🟡 upconvert | 🟡 upconvert | ✅ **2.0×** (NVIDIA Table 1) | ✅ 2.0× | ✅ 2.0× | ✅ | ✅ 2.0× |
| **NVFP4** (block-16, E4M3 + FP32) | 🟡 Marlin W4A16 | 🟡 Marlin W4A16 | 🟡 Marlin W4A16 | ✅ **4.0×** (NVIDIA Table 1: 4× on GB200) | ✅ **6.0×** | ✅ **6.0×** (Table 1: 6× on GB300) | ✅ **4.0×** dense GEMM; ⚠️ **MoE grouped GEMM disputed** — see §5 | ❌ → requantise to MXFP4 |
| **MXFP4** (block-32, E8M0) | 🟡 Marlin ⚠️ untested | 🟡 Marlin W4A16 default; W4A8 opt-in (FlashInfer ≥ 0.6.18 Humming) | 🟡 same | ✅ **4.0×** | ✅ **6.0×** | ✅ **6.0×** | ✅ `b12x_fused_moe` / `flashinfer_mxfp4` ⚠️ vLLM `get_mxfp4_backend()` still falls back to Marlin | ✅ **4.04×** (10,100 ÷ 2,500), scaled MFMA |
| **INT8 W8A8** | ✅ **2.0×** (624 TOPS) — *the only compute win on this card* | ✅ 2.0× | ✅ 2.0× | ✅ ⚠️ rate not published | ✅ ⚠️ n/p | ✅ but **0.067×** — dense INT8 is 166.7 TOPS, **1/30th of FP8**; INT8 is not a Blackwell-Ultra strategy | ✅ 960–1,007.6 TOPS `est.` | ✅ 2.0× (5,000 TOPS) |
| **INT4 W4A16** (AWQ/GPTQ g128) | 🟡 Marlin | 🟡 **Machete** (Hopper-tuned; Marlin *"used outdated `mma`, losing ~37 % peak throughput on Hopper"*) | 🟡 Machete | 🟡 Marlin | 🟡 Marlin | 🟡 Marlin | 🟡 Marlin — **and it is the fastest correct path on this card** | 🟡 Triton/AITER `a16w4` (Marlin/Machete are CUDA-only) |
| **FP6 / MXFP6** | ❌ | 🟡 | 🟡 | ✅ **2.0×** — FP6 runs at the **FP8** rate on Blackwell, not the FP4 rate | ✅ 2.0× | ✅ 2.0× | ⚠️ silicon support claimed, **rate unpublished**; assume = FP8 | ✅ **4.04×** (10,100) — **the only GPU here with a 6-bit path at 4-bit rate** |
| **FP8 KV cache** | 🟡 store only | ✅ **attention math runs in FP8** (FA3) | ✅ | ✅ (FA4 / FlashInfer) | ✅ | ✅ | 🟡 not via the FlashAttention path | ✅ (AITER) |
| **NVFP4 KV cache** | ❌ | ❌ | ❌ | ✅ `nvfp4`, `nvfp4_ds_mla`, `nvfp4_4over6` | ✅ + FlashInfer NVFP4 paged-KV sparse decode/prefill merged 2026-09-18 (prefill needs CUDA 13.0+) | ✅ same | ⚠️ **kernel merged 2026-09-07 for SM120/121; no engine verified in this pass routes to it** | ❌ — NVFP4 is an NVIDIA format |

### 3.2 Measured uplift, where a measurement exists

Peak ratios above are ceilings. These are the published end-to-end or kernel measurements in
this tree — note how much smaller they are.

| Change | GPU(s) | Measured | Source doc |
|---|---|---|---|
| FP8 → NVFP4 (GLM-5, 744B), **precision step alone** | H200 → B200 | **2.98×**; the silicon step is only **1.22×**; product 3.64 ≈ the 3.65× headline | [quantization-formats.md §6.1](../cross-cutting/quantization-formats.md) |
| MXFP4 W4A16 → block-FP8 re-encode (`VLLM_DSV4_FP4_DEQUANT=1`), DeepSeek-V4 | H20 (Hopper) | **prefill 1.38–1.53×**, **decode only 1.12–1.13×**; costs 2× weight memory → KV 83.9 → 53.7 GiB/GPU | [quantization-formats.md §6.2](../cross-cutting/quantization-formats.md) |
| A8W4 vs A4W4 MXFP4 (Kimi-K3) | 8× MI35x | **537.3 vs 530.8 tok/s — A8W4 *wins* by 1.2 %** | [quantization-formats.md §6.3](../cross-cutting/quantization-formats.md) |
| W4A6 (MXFP4 × MXFP6) vs FP8, Llama-3.1-8B | MI355X | **85.0k vs 83.3k tok/s**; GSM8K 62.55 % (MXFP4) → 76.4 % (W4A6) → 80.44 % (FP8) | [quantization-formats.md §6.4](../cross-cutting/quantization-formats.md) |
| FP8 KV cache vs BF16 KV | (engine-level) | Llama-3.1-8B **+14.9 %** output throughput, **−14.8 %** median ITL; decode cost to 54 % of BF16 in the best cases | [quantization-formats.md §8.2](../cross-cutting/quantization-formats.md) |
| Shared-expert fusion as one extra MXFP4 expert (DeepSeek-V4) | GB200 TP4 | **TTFT −13 % to −21 %, P99 ITL −15 % to −53 %** at neutral throughput — a format *downgrade* that wins by removing kernel launches | [quantization-formats.md §10.3](../cross-cutting/quantization-formats.md) |
| NVFP4 MoE (broken CUTLASS) vs Marlin W4A16, Qwen3.5-397B | 4× RTX PRO 6000 | **6–7 vs 50.5 tok/s** — Marlin wins by 7× | [gpus/rtx6000-pro.md §6a](../gpus/rtx6000-pro.md) |
| FlashInfer-CUTLASS NVFP4 vs Marlin, Nemotron 3 Super 120B | RTX PRO 6000 | **74 vs 92 tok/s** — *"Marlin trades 9 % of KV capacity for 20–25 % of decode throughput and correctness"* | [gpus/rtx6000-pro.md §6b](../gpus/rtx6000-pro.md) |
| MXFP4 Triton vs Marlin, gpt-oss-120b | RTX PRO 6000 Max-Q | **140 vs 185 tok/s** — Marlin wins | [gpus/rtx6000-pro.md §6](../gpus/rtx6000-pro.md) |
| MXFP4 → NVFP4 transcode accuracy, DeepSeek-V4.1-Flash / Kimi-K3 | GB300 / — | **+0.397** / **−0.098** mean points — accuracy-neutral both ways | [quantization-formats.md §7.1](../cross-cutting/quantization-formats.md) |
| Online NVFP4 → MXFP4 requant at load | MI355X | GSM8K recovery **98.7–100.7 %** across five models; steady-state throughput within ±1 %; 10–55 s load cost | [quantization-formats.md §3.2](../cross-cutting/quantization-formats.md) |

**The rule these measurements encode** ([quantization-formats.md §1](../cross-cutting/quantization-formats.md)):
storage bits decide *fit*, compute bits decide *prefill speed*, and the kernel decides
whether you get either. Decode is bandwidth-bound, so a 4-bit checkpoint keeps almost all of
its value on a GPU with no FP4 tensor core — it is *prefill* that collapses.

---

## 4. Per-model executed format, and what decode actually reads

### 4.1 Executed weight format, 5 models × 8 GPUs

Generated with `python3` from [`pairs.json`](./pairs.json)'s `weight_format_used` field;
each cell links to the pair doc it was transcribed from.

| Model \ GPU | H100 | H200 | B200 | B300 HGX | GB300 NVL72 | A100 | RTX PRO 6000 | MI355X |
|---|---|---|---|---|---|---|---|---|
| **DeepSeek-V4.1-Flash** | [MXFP4 → **dequant** BF16 (Marlin W4A16)](../models/deepseek41f/h100.md) | [MXFP4 → **dequant** BF16 (Marlin / FI SM90 CUTLASS)](../models/deepseek41f/h200.md) | [MXFP4 **native W4A8** (MXFP8 act)](../models/deepseek41f/b200.md) | [MXFP4 **native W4A8** (MXFP8 act)](../models/deepseek41f/b300.md) | [MXFP4 **native W4A8** (MXFP8 act)](../models/deepseek41f/gb300.md) | [MXFP4 → **dequant** BF16 (Marlin) + FP8 → W8A16](../models/deepseek41f/a100.md) | [MXFP4 **native W4A8** (FlashInfer/B12X) ⚠️ disputed vs Marlin](../models/deepseek41f/rtx6000-pro.md) | [MXFP4 **native** (AITER CK a8w4)](../models/deepseek41f/mi355x.md) |
| **…-Flash-NVFP4** | [NVFP4 → **dequant** BF16 (Marlin W4A16)](../models/deepseek41fnvfp4/h100.md) | [NVFP4 → **dequant** BF16 (Marlin W4A16)](../models/deepseek41fnvfp4/h200.md) | [NVFP4 **native W4A4** (gs16)](../models/deepseek41fnvfp4/b200.md) | [NVFP4 **native W4A4** (tcgen05 `.kind::mxf4nvf4`)](../models/deepseek41fnvfp4/b300.md) | [NVFP4 **native W4A4** (`flashinfer_trtllm_routed`)](../models/deepseek41fnvfp4/gb300.md) | [NVFP4 → **dequant** BF16 (Marlin); not runnable (no attn backend)](../models/deepseek41fnvfp4/a100.md) | [NVFP4 → **dequant** Marlin W4A16 (B12X W4A4 opt-in, unvalidated)](../models/deepseek41fnvfp4/rtx6000-pro.md) | [NVFP4 → **BF16 dequant emulation** every step (no requant path)](../models/deepseek41fnvfp4/mi355x.md) |
| **Qwen3.8-27B** | [FP8 E4M3 block-128 **native**](../models/qwen3827b/h100.md) | [FP8 E4M3 block-128 **native**](../models/qwen3827b/h200.md) | [NVFP4 **native** + FP8 attn/GDN](../models/qwen3827b/b200.md) | [NVFP4 W4A4 **native** + FP8 attn/GDN + BF16 ViT](../models/qwen3827b/b300.md) | [NVFP4 W4A4 **native** + FP8 attn/GDN + BF16 ViT](../models/qwen3827b/gb300.md) | [INT4 W4A16 **dequant** (Marlin); BF16 is the only native format](../models/qwen3827b/a100.md) | [NVFP4 W4A4 **native** (dense GEMM, no MoE path to break)](../models/qwen3827b/rtx6000-pro.md) | [MXFP4 W4A4 **native** (Quark-AWQ-MXFP4)](../models/qwen3827b/mi355x.md) |
| **Kimi-K3** | [MXFP4 → **dequant** BF16 (Marlin W4A16)](../models/kimik3/h100.md) | [MXFP4 → **dequant** BF16 (Marlin W4A16, +5.3 % repack)](../models/kimik3/h200.md) | [MXFP4 **native W4A8** (FI trtllm-gen SiTU)](../models/kimik3/b200.md) | [MXFP4 **native W4A8** (FI trtllm-gen SiTU)](../models/kimik3/b300.md) | [MXFP4 **native W4A8** (FI trtllm-gen SiTU)](../models/kimik3/gb300.md) | [MXFP4 → **dequant** BF16 (Marlin) ⚠️ contested / crashes](../models/kimik3/a100.md) | [MXFP4 → **W4A16 dequant** (B12X) + online MXFP8 overlay](../models/kimik3/rtx6000-pro.md) | [MXFP4 **native** (AITER SiTU v2, A8W4 SGLang / A4W4 vLLM)](../models/kimik3/mi355x.md) |
| **Marlin-2B** | [BF16 **native** (only checkpoint that exists)](../models/marlin2b/h100.md) | [BF16 **native**](../models/marlin2b/h200.md) | [BF16 **native** — FP4/FP8 cores unused](../models/marlin2b/b200.md) | [BF16 **native** — the 1.5× FP4 differentiator is unused](../models/marlin2b/b300.md) | [BF16 **native** — FP4 cores unused](../models/marlin2b/gb300.md) | [BF16 **native**](../models/marlin2b/a100.md) | [BF16 **native**](../models/marlin2b/rtx6000-pro.md) | [BF16 **native** — 10.1 PF MXFP4 rate unreachable](../models/marlin2b/mi355x.md) |

**Thirteen of forty cells are a dequant path** (DeepSeek base on H100/H200/A100; the NVFP4
build on H100/H200/A100/RTX PRO 6000/MI355X; Qwen on A100; Kimi-K3 on
H100/H200/A100/RTX PRO 6000). In every one of them the 4-bit checkpoint still
buys the *fit* — which is why Kimi-K3 and DeepSeek-V4.1-Flash exist on Hopper at all — and
buys **zero FLOPS**.

### 4.2 Decode-bandwidth term 1: weights read per decode step

Decode is memory-bound ([METHODOLOGY §4](../METHODOLOGY.md)), so the executed format's real
payoff is the byte count below, not a TFLOPS number. These are the `weights_read` constants
the pair docs derive.

| Model | Weights read per decode step | Format sensitivity | Source |
|---|---|---|---|
| DeepSeek-V4.1-Flash | `18.75 GB static + 40 layers × distinct_experts(batch) × 18,800,640 B` | MXFP4 expert = 18,800,640 B | [deepseek41f/h100.md](../models/deepseek41f/h100.md), [b300.md](../models/deepseek41f/b300.md), [gb300.md](../models/deepseek41f/gb300.md) |
| …-Flash-NVFP4 | same shape, expert = **19,906,584 B** | **+5.88 %** bytes per decode step for identical math (computed: 19,906,584 ÷ 18,800,640 = 1.0588) | [deepseek41fnvfp4/a100.md](../models/deepseek41fnvfp4/a100.md), [b200.md](../models/deepseek41fnvfp4/b200.md), [h100.md](../models/deepseek41fnvfp4/h100.md) |
| Kimi-K3 | `112.0 GB non-expert + distinct_experts(batch) × 17.55 MB × 92 layers` | only **46.7 %** of active params are MXFP4 → Amdahl caps the FP4 speedup at **1.87×** | [kimik3/b200.md](../models/kimik3/b200.md) |
| Qwen3.8-27B | whole checkpoint, batch-independent (dense): **55.56 / 30.87 / 21.92 / 19.45 GB** at BF16 / FP8 / NVFP4 / INT4 | the one model where format maps 1:1 to decode bytes | [METHODOLOGY §8](../METHODOLOGY.md), [qwen3827b/h200.md](../models/qwen3827b/h200.md) |
| Marlin-2B | **3,763,650,176 B** (BF16, dense, batch-independent) | no quantized checkpoint exists | [marlin2b/h100.md](../models/marlin2b/h100.md), [a100.md](../models/marlin2b/a100.md), [h200.md](../models/marlin2b/h200.md) |

### 4.3 Decode-bandwidth term 2: KV bytes per token actually executed

This is where the kernel matrix turns into money. The architectural figure and the executed
figure are **not the same number** on six of eight GPUs for the DeepSeek pair.

| Model | Architectural KV | H100 | H200 | B200 | B300 | GB300 | A100 | RTX PRO 6000 | MI355X |
|---|---|---|---|---|---|---|---|---|---|
| DeepSeek-V4.1-Flash | **890 B/tok** FP4 (720 main + 170 indexer) | **3,200** (BF16) | 1,650 (FP8) | 890 ⚠️ see note | **890** | **890** | 1,650 | 1,650 | 1,650 |
| …-Flash-NVFP4 | 890 B/tok (identical layout) | 1,650 | 1,650 | 890 | **890** | **890** | 1,650 | 1,650 | 1,650 |
| Kimi-K3 | 27,648 B BF16 / **13,824 B FP8** (24 of 93 MLA layers) | 27,648 ⚠️ (log-derived 27,694) | 13,824 FP8 | 13,824 (FP8 **mandatory** — `TOKENSPEED_MLA` force-rewrites it) | 13,824 | 13,824 | 27,648 / 13,824 | 13,824 | 13,824 |
| Qwen3.8-27B | 65,536 B BF16 / 32,768 B FP8 (16 of 64 layers) | 32,768 | 32,768 | 32,768 | 32,768 | 32,768 | 32,768 (store-only, no FA3) | 32,768 | 32,768 |
| Marlin-2B | 12,288 B BF16 / 6,144 FP8 / 3,072 NVFP4 (6 of 24 layers) | 12,288 | 12,288 | 12,288 ⚠️ FP8 KV forces the hd256 FA4 kernel back to FA2 | 12,288 | 12,288 | 12,288 | 12,288 | 12,288 |

Sources per row: [deepseek41f/{h100,h200,b200,b300,gb300,a100,rtx6000-pro,mi355x}.md](../models/deepseek41f/),
[deepseek41fnvfp4/](../models/deepseek41fnvfp4/),
[kimik3/a100.md §KV](../models/kimik3/a100.md) and [kimik3/mi355x.md](../models/kimik3/mi355x.md),
[qwen3827b/a100.md](../models/qwen3827b/a100.md) and [rtx6000-pro.md](../models/qwen3827b/rtx6000-pro.md),
[marlin2b/b200.md](../models/marlin2b/b200.md).

**The decode-bandwidth advantage, stated plainly.** For DeepSeek-V4.1-Flash the FP4
compressed cache is **1.85× fewer KV bytes than FP8 and 3.60× fewer than BF16**
(1,650 ÷ 890 = 1.854; 3,200 ÷ 890 = 3.596, computed here from
[flash-attention.md §11](../cross-cutting/flash-attention.md) and
[deepseek41f/architecture.md §5](../models/deepseek41f/architecture.md)). It requires
`nvfp4_ds_mla` → `FLASHMLA_MEGA_ATTN_DSV41` → `capability.major == 10`. That single gate is
why B200/B300/GB300 read a third of the KV traffic that H100 does at the same context.

⚠️ **Disagreement inside this tree, B200.**
[deepseek41f/b200.md](../models/deepseek41f/b200.md) derives from three independent
observations of vLLM's own KV pool (H100→H200 capacity delta, B200→GB200 delta, and PR
#56686's block count) that the **observed** pool sizes imply **≈ 2,312–2,346 B/token, 2.6×
the architectural 890**, and lists three candidate explanations (vLLM defaulting the main
cache to FP8; the 128-token SWA ring allocated inside the paged pool; block-granularity
padding at `block_size 128`) without resolving which. The B300 and GB300 pair docs quote 890
([b300.md](../models/deepseek41f/b300.md), [gb300.md](../models/deepseek41f/gb300.md)).
Both are cited; plan concurrency from the B200 doc's 2,340 B/token until you have verified
`--kv-cache-dtype` and `use_fp4_indexer_cache` on your build.

**A KV-dtype choice that silently costs you a kernel, on Blackwell only.** For both
head-dim-256 models the FA4 hd256 2-CTA kernel is **mutually exclusive with quantized KV**:
*"Every published recipe pins `--kv-cache-dtype fp8`, so on vLLM you get FA2, not FA4, on
the 16 attention layers. Choosing FA4 costs 2× the KV pool"*
([qwen3827b/b300.md](../models/qwen3827b/b300.md)); Marlin-2B hits the same trade —
*"`--kv-cache-dtype fp8_e4m3` forces the hd256 kernel back to FA2"*
([marlin2b/b200.md](../models/marlin2b/b200.md)). The same kernel also needs `--block-size`
a multiple of 128 or it falls back to FA2 **silently**
([flash-attention.md §16.3](../cross-cutting/flash-attention.md)). This trade does not exist
on H100/H200 (FA3 takes FP8 KV happily) or on RTX PRO 6000 / MI355X (no FA4 at all).

⚠️ **Disagreement inside this tree, H100 vs H200 for DeepSeek-V4.1-Flash.**
[deepseek41f/h100.md](../models/deepseek41f/h100.md) states the KV executes at **BF16 3,200
B/token** on H100, while [h200.md](../models/deepseek41f/h200.md) and
[a100.md](../models/deepseek41f/a100.md) state **FP8 `fp8_ds_mla` 1,650 B/token** on the same
`major == 9` backend. Both are as written; the h100 doc's figure is the conservative one.

---

## 5. The NVFP4 DeepSeek-V4.1-Flash story

### 5.1 What the NVFP4 build actually changes

`nvidia/DeepSeek-V4.1-Flash-NVFP4` (released 2026-09-16, `nvidia-modelopt v0.47.0rc0`)
converts **only the ordinary routed MoE experts** from source MXFP4 to NVFP4 W4A4 at group
size 16 — 384 experts across 40 layers, **58 % of checkpoint bytes**. Attention, shared
experts, vision, the 188.8 GiB Engram lookup tables and the MTP/DSpark experts keep their
source precision, *including MXFP4 on the draft experts*
([quantization-formats.md §9.2](../cross-cutting/quantization-formats.md),
[deepseek41fnvfp4/architecture.md](../models/deepseek41fnvfp4/architecture.md)). **The
attention tensors and the KV layout are untouched**, so every backend, block size and KV
dtype in §1 applies unchanged
([flash-attention.md §16.1](../cross-cutting/flash-attention.md)).

It is **larger**: 527.27 GB / 491.1 GiB versus the base's 510.29 GB / 475.2 GiB — +16 GiB,
which [quantization-formats.md §9.2](../cross-cutting/quantization-formats.md) reproduces
exactly as 543.6 B expert params × (1/16 − 1/32) bytes of extra scale.

### 5.2 What B200 / B300 / GB300 gain over the FP8 base

There are two separate questions and the tree answers them differently.

**(a) MXFP4 base → *FP8 execution*.** Not applicable on Blackwell: the base checkpoint's
MXFP4 experts already run **natively as W4A8** on B200/B300/GB300 with no dequant and no
requant ([deepseek41f/b200.md](../models/deepseek41f/b200.md),
[b300.md](../models/deepseek41f/b300.md), [gb300.md](../models/deepseek41f/gb300.md)). Note
what the pair docs are careful to say: W4A8 executes on the **FP8 line** — 4,500 TFLOPS on
B200/B300 HGX — **not** the 9,000/13,500 TFLOPS FP4 line. The FP4 rate needs W4A4.

**(b) MXFP4 base → NVFP4 build, on Blackwell.** The NVFP4 experts run **W4A4** natively, so
this is the one path that reaches the FP4 tensor-core rate for the expert GEMMs
([deepseek41fnvfp4/b200.md](../models/deepseek41fnvfp4/b200.md),
[b300.md](../models/deepseek41fnvfp4/b300.md),
[gb300.md](../models/deepseek41fnvfp4/gb300.md)). Against that:

| Axis | Finding | Source |
|---|---|---|
| Throughput vs the base | **NVIDIA publishes no throughput or latency comparison against the MXFP4 base for this checkpoint** — the card's benchmark section reports accuracy only | [quantization-formats.md §9.2](../cross-cutting/quantization-formats.md) |
| Decode bytes | **+5.88 % per decode step** (19,906,584 vs 18,800,640 B/expert). [gb300.md](../models/deepseek41fnvfp4/gb300.md) concludes from arithmetic that it is **2–5.7 % slower per decode step** and costs **+4.25 GB/GPU at TP4** | [deepseek41fnvfp4/gb300.md](../models/deepseek41fnvfp4/gb300.md), [h100.md](../models/deepseek41fnvfp4/h100.md) |
| Accuracy | **+0.397 points mean** across GPQA-D / AA-LCR / SciCode / IFBench / MMMU-Pro / Terminal-Bench 2.1 — inside run-to-run variance, i.e. accuracy-neutral | [quantization-formats.md §7.1](../cross-cutting/quantization-formats.md) |
| Validation | Both vLLM and SGLang validated on **4× GB300, TP4**; the published accuracy table was produced with **vLLM** | [quantization-formats.md §9.2](../cross-cutting/quantization-formats.md) |
| Highest risk | **Two MoE quant methods in one process** — NVFP4 backbone experts + MXFP4 draft experts. *"No source confirms any engine has validated NVFP4 backbone + MXFP4 draft together"* | [deepseek41fnvfp4/b300.md](../models/deepseek41fnvfp4/b300.md) |

**Verdict from this tree: on Blackwell the NVFP4 build is a lateral move**, justified only by
NVIDIA's group-16 accuracy audit. The measured evidence for *format* uplift at this scale
comes from a different model — GLM-5 FP8→NVFP4 at **2.98× the precision step**
([quantization-formats.md §6.1](../cross-cutting/quantization-formats.md)) — and that is an
FP8→FP4 transition, not an MXFP4→NVFP4 one. DeepSeek-V4.1-Flash's base is already 4-bit.

### 5.3 What RTX PRO 6000 can do with it

`sm_120` has an NVFP4 tensor core and a working **dense** NVFP4 GEMM
([gpus/rtx6000-pro.md §6](../gpus/rtx6000-pro.md): *"dense NVFP4 GEMM on sm_120 was never
broken; the repro was"*). The problem is the **MoE grouped GEMM**, and the tree contains a
live disagreement:

- [METHODOLOGY §8](../METHODOLOGY.md) and [gpus/rtx6000-pro.md §6a](../gpus/rtx6000-pro.md):
  *"NVFP4 MoE grouped-GEMM broken → Marlin W4A16"*. The symptom is that checkpoints load,
  serve and generate — but the expert GEMM returns numerically wrong output.
- [quantization-formats.md §5, §9.6 note e](../cross-cutting/quantization-formats.md): cites
  FlashInfer PR #2898 closing the SM120 fused-MoE gap, and states the two are reconcilable
  only if the fix landed for MXFP4 `b12x_fused_moe` and **not** for the NVFP4 routed-expert
  path. Its own planning value: **assume dequant until measured.**
- [deepseek41fnvfp4/rtx6000-pro.md](../models/deepseek41fnvfp4/rtx6000-pro.md) executes it
  as **Marlin W4A16 dequant by default**, with native B12X W4A4 as an unvalidated opt-in.
- A native SM120 NVFP4 MoE backend *does* exist in vLLM as `--moe-backend flashinfer_b12x`,
  but *"is simply not auto-selected"*: `oracle/nvfp4.py` excludes B12X *"until the upstream
  CUTLASS SM121 MMA op guard is resolved"*, and a ModelOpt NVFP4 checkpoint resolves as
  **W4A16** anyway because `FlashInferExperts._supports_quant_scheme` only lists the W4A4
  pair for capability ≥ 100 ([gpus/rtx6000-pro.md §6a](../gpus/rtx6000-pro.md)).

So: **RTX PRO 6000 can run the NVFP4 build today, at Marlin W4A16 speed, with the FP8 1,650
B/token KV cache** — not the 890 B/token one. Two measured data points on how much that
costs: Marlin beats broken-CUTLASS NVFP4 by **50.5 vs 6–7 tok/s** and beats
FlashInfer-CUTLASS NVFP4 by **92 vs 74 tok/s**
([gpus/rtx6000-pro.md §6a, §6b](../gpus/rtx6000-pro.md)). Note also the *base* MXFP4
checkpoint is the disputed cell in the other direction —
[deepseek41f/rtx6000-pro.md](../models/deepseek41f/rtx6000-pro.md) records native
`b12x_fused_moe` W4A8 in two measured community builds against METHODOLOGY's Marlin fallback,
with **no A/B published**.

One field-confirmed trap specific to this checkpoint on this card: the loader *"incorrectly
routed draft experts through the NVFP4 dequantization path, destroying scale information and
producing 'noise' with ~0 % token acceptance"* — because the MTP/DSpark experts are still
MXFP4 ([deepseek41fnvfp4/rtx6000-pro.md](../models/deepseek41fnvfp4/rtx6000-pro.md)).

### 5.4 Why H100 / H200 / A100 / MI355X cannot run it

Three different reasons, and only one of them is "no FP4 silicon":

| GPU | Reason | Consequence |
|---|---|---|
| **H100 / H200** (`sm_90`) | **No FP4 tensor core exists on Hopper.** NVFP4 loads only through the universal Marlin **W4A16** fallback: unpacked to BF16 in registers, MMA issued at 989.5 dense TFLOPS | Memory win kept, compute win lost. And it is the *worse* 4-bit choice here: the NVFP4 build is 17 GB larger than the base for identical math ([deepseek41f/h100.md](../models/deepseek41f/h100.md): *"do not use"*). The model card scopes the checkpoint to *"NVIDIA Blackwell"* — format-runnable, vendor-unsupported |
| **A100** (`sm_80`) | No FP8 GEMM **and** no FP4 tensor core; TRT-LLM's Ampere row is **W4A16-only**. Worse, **no attention backend exists**: `FLASHMLA_SPARSE_DSV41` needs CC 9/10, `FLASHINFER_MLA_SPARSE_DSV41` CC 10/12, `FLASHMLA_MEGA_ATTN_DSV41` CC 10 | **Not deployable upstream at all** — the weight format is not even the binding constraint ([deepseek41fnvfp4/a100.md](../models/deepseek41fnvfp4/a100.md)). A community fork substitutes Triton BF16 sparse-MLA + a Triton FP8 MQA-logits indexer |
| **MI355X** (`gfx950`) | **Three structural reasons**, none of them software: CDNA4's `v_mfma_scale_f32_16x16x128_f8f6f4` takes an OCP **block of 32**, so a 16-element block has no instruction; the matrix core consumes a **UE8M0** power-of-two scale, not a fractional E4M3; and MXFP4 has **no per-tensor scale concept** in the instruction, so NVFP4's FP32 tensor scale has nowhere to live ([quantization-formats.md §3.2](../cross-cutting/quantization-formats.md)) | The normal escape is SGLang's `quark_mxfp4` **requant at load** (10–55 s, GSM8K recovery 98.7–100.7 %) — but it is **not available for `DeepseekV41` on gfx950**, so vLLM falls to `Nvfp4QuantizationEmulationTritonExperts`, which **unpacks NVFP4 every forward step** ([deepseek41fnvfp4/mi355x.md](../models/deepseek41fnvfp4/mi355x.md)). Marlin is CUDA-only, so there is no W4A16 fallback either |

All four also lose the 890 B/token KV cache for the same single reason: `nvfp4_ds_mla`
requires `FLASHMLA_MEGA_ATTN_DSV41`, which requires `major == 10`
([flash-attention.md §11](../cross-cutting/flash-attention.md)). On all four, budget
**1,650 B/token** (or 3,200 if the engine lands on BF16 KV — §4.3).

---

## 6. Kimi-K3 MXFP4: Blackwell vs MI355X vs Hopper

The checkpoint is **`mxfp4-pack-quantized`, group 32, E8M0 scales, applied by
quantization-aware training from the SFT stage**, with `ignore` covering
`self_attn`, `shared_experts`, the dense MLPs, `lm_head`, the vision tower and the
mm-projector — so **attention weights stay BF16 and MXFP4 is MoE-only**
([quantization-formats.md §9.3](../cross-cutting/quantization-formats.md),
[flash-attention.md §16.2](../cross-cutting/flash-attention.md)). At BF16 the experts alone
would be **5,071.5 GiB**; MXFP4 is not an optimization here, it is the only reason the model
exists in the open.

| | **Blackwell** (B200 / B300 / GB300) | **MI355X** (`gfx950`) | **Hopper** (H100 / H200) |
|---|---|---|---|
| Expert GEMM | **native MXFP4 W4A8**, FlashInfer trtllm-gen **SiTU** kernels; *"Leave `--moe-runner-backend` unset on Blackwell"* | **native MXFP4** via AITER **SiTU v2 FlyDSL** grouped GEMM on the scaled MFMA path | **Marlin W4A16 — dequant to BF16 in registers.** *"H100/H200 pin Marlin"* |
| Activation precision | W4A8 (MXFP8 activations) | A8W4 default in SGLang (`AITER_SITUV2_A8W4=1`), A4W4 in vLLM (`VLLM_ROCM_USE_AITER_MOE_SITUV2=1`) — the two engines default to opposite ends of the same trade ⚠️ | A16 (BF16) — no activation quantization at all |
| Measured format effect | — | **A8W4 537.3 vs A4W4 530.8 tok/s**: 4-bit activations are *not* automatically faster | **+5.3 % weight bytes** from the Marlin repack (102.75 GB/GPU at TP16 vs 97.56 theoretical) |
| Amdahl ceiling on the FP4 win | only **46.7 % of active params** are MXFP4 → **1.87×** cap ([kimik3/b200.md](../models/kimik3/b200.md)) | the 53.3 % outside the experts stays BF16 → effective dense peak **3,853 TFLOPS/GPU, not 10,100**, and the FP4 speedup caps at **1.54×** over all-BF16 ([kimik3/mi355x.md](../models/kimik3/mi355x.md)) | none — there is no FP4 win to cap |
| MLA attention (24 layers) | `TOKENSPEED_MLA` (10.x-only, **FP8 KV required**), alternates `FLASHINFER_MLA` / `TRTLLM_RAGGED` / `cutedsl_mla` | `ROCM_AITER_MLA` (**1.2–1.5× over `TRITON_MLA`, 1.52× on MI355X at conc 64**) — but AITER MLA prefill needs a 12→16 head zero-pad at TP8 or it silently falls back to Triton (~4–7k → ~13k tok/s node prefill) | `FLASHMLA` SM90 MQA + FA3 prefill |
| KDA linear attention (69 layers) | **FlashInfer `fused_kda_decode`** — gate is literally `compute_capability in ((10,0),(10,3))`, i.e. **B200 and B300/GB300 only** — 1.33× the vLLM native fused kernel at one row, geomean 1.13× | **Triton `fla.ops.kda` only.** Both fused KDA decode kernels are hard-gated on CUDA compute capability. `SGLANG_K3_KDA_FUSED_BACKEND=aiter` recovers **9.20 → 8.38 µs/layer (−8.9 %)** | **native fused CUDA KDA decode** (`is_device_capability(90)`); FlashInfer's faster one is Blackwell-gated |
| Fit | 8× B300 HGX = **2,144 GB** holds the 1,560.9 GB checkpoint at 195 GB/GPU | 288 GB/GPU, 8-GPU node | **H100 needs 4×8 = TP32/EP32**; *"least post-weight headroom (80 GB)"*. Measured resident footprint on H100 is **1.31× the checkpoint share** (59.63 GiB/GPU at TP32 vs 48.78 GB theory) |
| Notable alternative | `nvidia/Kimi-K3-NVFP4` runs natively but is **measurably slower** (TPOT 10.12 vs 8.51 ms at c=1) and **+49 GB**; it also costs ~10 % of peak concurrency (B300 KDA admission caps 101 → 91 no-spec, 68 → 60 with DSpark) | ⚠️ Kimi-K3 is **not a listed sm_120 platform** in any recipe, and 8× 96 GB = 768 GB cannot hold 1,453.7 GiB anyway | `vessl/Kimi-K3-W4AFP8` (requantised INT4-g128 + FP8 activations) measured **+17.9 % throughput** on the same 16× H200 |

Sources: [kimik3/b200.md](../models/kimik3/b200.md),
[b300.md](../models/kimik3/b300.md), [gb300.md](../models/kimik3/gb300.md),
[mi355x.md](../models/kimik3/mi355x.md), [h100.md](../models/kimik3/h100.md),
[h200.md](../models/kimik3/h200.md), [rtx6000-pro.md](../models/kimik3/rtx6000-pro.md),
[a100.md](../models/kimik3/a100.md),
[quantization-formats.md §9.3, §6.3](../cross-cutting/quantization-formats.md),
[flash-attention.md §14.4, §16.2](../cross-cutting/flash-attention.md).

**The one-line read.** Blackwell and MI355X both execute this checkpoint's experts natively
on 4-bit matrix cores, and both are capped near 1.5–1.9× by the BF16 attention half of the
model. The difference between them is **not the MoE kernel — it is KDA**: Blackwell gets a
fused CUDA/FlashInfer decode kernel that CDNA4 is architecturally locked out of, for 69 of
93 layers. Hopper loses on both counts and pays TP32 to fit.

Two other things this tree records about Hopper here. Contested:
[kimik3/a100.md](../models/kimik3/a100.md) notes vLLM #35922 reports the Marlin MoE FP4 path
**crashing on A100** — the sm_80 cell in §4.1 is theory, not a measurement. And on
sm_120, the model only fits at all by adding an **online MXFP8 weight-only overlay** on the
KDA/MLA projections and the vision tower, because 1,560.86 GB ÷ 16 = 97.55 GB/GPU does not
fit a 96 GB card ([kimik3/rtx6000-pro.md](../models/kimik3/rtx6000-pro.md)).

---

## 7. Speculative decoding, per model per GPU

**Read the acceptance figures with the tree's own discipline.** Three numbers that look alike
are not alike ([serving-optimizations.md §2.2–2.3](../cross-cutting/serving-optimizations.md),
and the pair docs below):

- **3.51** in the DeepSeek MI355X recipe is a **synthetic benchmark constant**
  (`"rejection_sample_method":"synthetic","synthetic_acceptance_length":3.51`) — the harness
  is *told* to assume it.
- **3.51** on MBPP for **Qwen3.8-27B DSpark** is a *real per-request mean* for that model.
  Same digits, opposite epistemic status
  ([qwen3827b/b300.md](../models/qwen3827b/b300.md)).
- **4.5** in SGLang's Kimi-K3 B300 speedups and **5.5** in SGLang's GB300 DeepSeek blog are
  **simulations** (`SGLANG_SIMULATE_ACC_LEN`), which SGLang itself labels as not-measured.

| Model / method | A100 | H100 | H200 | B200 | B300 HGX | GB300 NVL72 | RTX PRO 6000 | MI355X |
|---|---|---|---|---|---|---|---|---|
| **DeepSeek-V4.1-Flash — DSpark γ=5** (no MTP: V4.1 dropped it) | native; **no acceptance published** ⚠️ | native; **measured acceptance 2.817** on sm_90 (2 nodes × 8 H20, GSM8K, k=5; per-position 0.724/0.516/0.301/0.180/0.096) → up to **2.82× on output** | native; acceptance **unpublished** ⚠️, only a derived lower bound `a ≥ 1.15` | native, **adaptive verification works**; every published B200 number uses the synthetic 3.51 ⚠️ | native; **2.6–2.7 tokens/step** measured at conc 1 on 4× GB300 (DSpark round 12.2 → 9.4 ms) | native, adaptive verification works; only real measurement of this checkpoint is **3.57 tok/step** (DGX Spark community, range 1.88–5.92) | native and **load-bearing**; acceptance **disputed**: 0xSero medians 74.1–88.4 %, local-inference-lab accepted length **2.443–2.551 of 5**, vLLM #56892 reports the inverse (*"~72–87 % of drafted tokens are rejected"*) | **native but crippled**: `enable_adaptive_verification` **must be false on ROCm**; the entire interactive envelope rests on the synthetic 3.51 — remove it and S1 cost rises **6.9×** |
| **…-Flash-NVFP4 — DSpark** | n/a (model not deployable) | present, **unexercised** — NVIDIA: *"DSpark tensors are preserved, but speculative decoding was not exercised in the reported validation"* | ⚠️ the V4 sibling **crashes on SM90** (vLLM #47648: no working attention-backend combination); V4.1 MTP evidently works where V4 DSpark did not | ⚠️ unexercised for this checkpoint | ⚠️ unexercised; base-checkpoint proxy is 2.6–2.7 tok/step | ⚠️ unexercised | **+38 % single-stream (108.8 → 150.6 tok/s)** on the V4-Flash NVFP4 sibling on 2× RTX PRO 6000, acceptance **58–94 %, accept length 2.16–2.88** — **but only after patching the MXFP4 draft-expert routing; without it ~0 %** | native but crippled (ROCm adaptive-verification block); no acceptance measurement on any GPU for this build |
| **Kimi-K3 — DSpark** (`num_nextn_predict_layers: 0` → **no MTP head ships**; EAGLE not an option except §GB300) | ⚠️ likely unsupported — SGLang pins `trtllm_mha` as the draft backend and `--enable-linear-replayssm-spec` is untested on sm_80 | native; ⚠️ *"nothing extra when the step is 90 % collective latency"* | **native and measured on H200: acceptance ≈ 3.4**, 23.21 ms TPOT at conc 8 vs 60–183 ms no-spec — *"the single largest lever on this GPU"* | native; vLLM **measured on real workloads: macro 4.11**, code 4.96/4.73, math 6.42, creative 2.61 | **TPOT 8.51 → 2.84 ms at c=1 (3.00×), 19.47 → 9.88 at c=16 (1.97×), 40.19 → 24.47 at c=64 (1.64×)** — ⚠️ at a *pinned* `SGLANG_SIMULATE_ACC_LEN=4.5`. Costs the admission cap **101 → 68 (−33 %)** | DSpark-8 plus **EAGLE-3** (`lightseekorg/kimi-k3-eagle3-mla`, the only GB300 recipe shipping it; **no published acceptance** ⚠️). **ReplaySSM is load-bearing**: draft-window memory 512 KB → 16 KB (~32×), verify workspace 16.7 → 0.4 GiB/GPU | **measured on this pair: DSpark 122.695 vs 55.801 tok/s = 2.20×** at batch 1 (acceptance 0.4149, 3.905 tok/cycle); **DFlash 155.069 tok/s = 2.78×** (acceptance 0.6188, 5.332 tok/cycle) — the largest lever here. ⚠️ RedHatAI BF16 DSpark silently collapses **20.7 % → 0.84 %** without vLLM PR #310 | **native after a ROCm fix**: **2.2× single-stream, ~1.7× per-stream at moderate load, +18 % peak aggregate**, moves the peak from c24 to c64. Costs S: 5 → 13 state slots |
| **Qwen3.8-27B — MTP** (in-checkpoint 0.425 B head, γ=3) | ⚠️ **open sm_80-only speculation fault** reported by two owners mid-bisect | native; acceptance **0.771** (FP8) / mean accept length **4.28**; helps below ~bs 100 at 8K | native; acceptance **0.771–0.897** `meas.` (on 5090s, not H200); needs FlashInfer > 0.6.15.post1 | native; mean accept length **4.28**; costs `D = 4` state slots ⚠️ unmeasured on B200 | **verified end-to-end on GB300 at TP4: acceptance 92.2 % BF16 / 84.8 % FP8** on short prompts → mean accepted length 3.56 / 3.18 at γ=3 `est.` | native (SGLang `NEXTN`); accept length 4.28 measured on RTX-class hardware, ⚠️ **no GB300 measurement**; est. 3.1–4.1× at bs ≤ 128 | native, and this card's `hardware_overrides` sets **γ=5, not 3**. `meas.` **150 tok/s at bs=1** with NVFP4+MTP vs ~59 `est.` without. Both NVFP4 builds leave the MTP head **BF16 (0.849 GB)** — every draft step reads 0.85 GB | native; measured on the shape-identical Qwen3.5-27B: acceptance **3.675 (steps=3) → 5.869 (7) → 7.371 (15)**, throughput plateaus ≈ 245 tok/s past budget 8. The MTP head survives AMD's MXFP4 quantisation as BF16 |
| **Qwen3.8-27B — DFlash2 / DSpark** | — | DFlash2 native (vLLM ≥ 0.28.0), best published acceptance **mean 4.80** | **the only H200-attributed measurement: 2.67–3.43× at conc 1, 2.27–2.85× at c=8, 1.01–1.45× at c=32** — at the S1 point (batch 168) expect **≤ 1×** | DSpark mean accept **3.62** — *"never the right first choice here"* | DFlash2 available but **`verificationStatus: "in-progress"` on GB300**, i.e. **not verified** on Blackwell Ultra | — | **DFlash2 `meas.` 240+ tok/s single-stream** on one card | **best measured configuration on this GPU**: on Qwen3.5-27B, **396 tok/s bf16 / 460 tok/s mxfp4** at bs=1, acceptance **10.377** at block=16 (HumanEval), up to **5.02×** over autoregressive; MXFP4 target preserves acceptance (Δ ≤ 0.15) |
| **Marlin-2B — MTP / EAGLE / DSpark** | **unavailable everywhere.** `config.json` declares `mtp_num_hidden_layers: 1` but the index contains **zero `mtp` tensors**; base − Marlin = 60,828,160 params = exactly one missing MTP module. No EAGLE head exists for Marlin; DSpark is DeepSeek-family and not applicable | same | same | same | same | same — *"and it could not help: every scenario is prefill-bound, and speculation cannot touch prefill"* | same — the measured MTP decay **on this card** (1.90× at conc 1 → **1.17× at conc 64**) bounds what is being given up | same — for calibration, AMD measured **DFlash block=16 on Qwen3.5-27B at 460 tok/s mxfp4, acceptance 10.377, up to 5.02×** on one MI355X: a same-family, same-GPU demonstration of the lever Marlin cannot pull |

Sources: the corresponding `research/models/<exp>/<gpu>.md` §2 optimization tables —
[deepseek41f/](../models/deepseek41f/), [deepseek41fnvfp4/](../models/deepseek41fnvfp4/),
[kimik3/](../models/kimik3/), [qwen3827b/](../models/qwen3827b/),
[marlin2b/](../models/marlin2b/) — plus
[serving-optimizations.md §2](../cross-cutting/serving-optimizations.md).

**Three GPU-specific facts worth pulling out.**

1. **ROCm structurally weakens DSpark.** `enable_adaptive_verification` must be `false`
   because `DeepseekV41IndexerBackend.supports_device_cpu_query_lens_mismatch()` is `False`
   and the sparse-SWA backend reports `UNIFORM_BATCH` not `ALWAYS`, and
   `VLLM_USE_BREAKABLE_CUDAGRAPH=1` is mandatory
   ([deepseek41f/mi355x.md](../models/deepseek41f/mi355x.md),
   [flash-attention.md §6.2](../cross-cutting/flash-attention.md)).
2. **Speculation and the fused KDA kernels are mutually exclusive on paper.** Both fused KDA
   decode kernels require `num_spec == 0`
   ([flash-attention.md §14.4](../cross-cutting/flash-attention.md)) — which is why
   Kimi-K3's `num_nextn_predict_layers: 0` matters, and why ReplaySSM (§GB300 cell) is
   load-bearing rather than a nicety.
3. **Blackwell loses the most from turning speculation off, and MI355X loses the most from
   turning it on badly.** The MI355X DeepSeek cell prices removal at **6.9×**; the RTX PRO
   6000 NVFP4 cell prices a mis-routed draft expert at **~0 % acceptance**.

---

## 8. Open questions

Consolidated from the ⚠️ items above; each is already open in the source document named.

**Attention kernels**

1. **Does DeepSeek-V4.1 sparse *decode* actually run on H100/H200?** FlashMLA's README says
   *"Sparse Decoding for DeepSeek V4.1 is only available on SM100"*, while vLLM's
   `supports_compute_capability` returns `major in [9, 10]` and sets a 64-token block size
   specifically for SM90 — and there are working H100/H200 runs with GSM8K 0.971–0.975.
   Direct conflict ([flash-attention.md Q15](../cross-cutting/flash-attention.md),
   [deepseek41f/h100.md](../models/deepseek41f/h100.md),
   [h200.md](../models/deepseek41f/h200.md)).
2. **No attention TFLOPS exists for RTX PRO 6000 at all**, so every §2 row for it is a peak
   ratio, not a measurement ([flash-attention.md Q, §15.4](../cross-cutting/flash-attention.md)).
3. **No FA4 measurement on H100** (FA3→FA4 on Hopper unquantified) and **none on
   B300/GB300** (does `sm103a`'s `tcgen.ld.red` buy anything beyond the clock ratio?).
4. **FA4 on `sm_120`:** upstream has `FlashAttentionForwardSm120`; vLLM's `_is_fa4_supported`
   refuses 12.x. Does a locally built `vllm_flash_attn` (or `FLASH_ATTENTION_ARCH=120`)
   change this, and is the SM80-MMA path ever faster than FA2 in practice?
5. **SGLang lists FA4 as FP4-KV ✅ / FP8-KV ❌; vLLM lists FA4 KV dtypes as `fp8, fp8_e4m3`.**
   Unreconciled ([flash-attention.md Q12](../cross-cutting/flash-attention.md)).
6. **All four gfx950 attention PRs (#5403, #5376, #5577, #5556) were open on 2026-09-19**, and
   three of the four TFLOPS figures are measured on **MI350X**, whose own dense BF16 peak was
   never obtained — so 497/509/436 currently have no valid denominator.
7. **No DSA/hierarchical-indexer kernel is confirmed on gfx950** by any primary source
   ([deepseek41f/mi355x.md](../models/deepseek41f/mi355x.md)).
8. **`sglang#27384`** reports a device-side assert in `flash_mla_sparse_fwd` on B300/`sm_103`
   that is token-exact on B200 ([deepseek41fnvfp4/b300.md](../models/deepseek41fnvfp4/b300.md)).

**Formats**

9. **RTX PRO 6000: are the published TFLOPS sparse or dense?** The Server Edition page
   contains `4 PFLOPS` / `2 PFLOPS` / `1 PFLOP` and the string "sparsity" appears nowhere on
   it. If they are already dense, every RTX PRO 6000 compute figure in this repo doubles
   ([quantization-formats.md Q1](../cross-cutting/quantization-formats.md)).
10. **NVFP4 MoE grouped-GEMM on `sm_120`: broken or fixed?** METHODOLOGY §8 and
    `gpus/rtx6000-pro.md` say broken → Marlin; `quantization-formats.md` cites PR #2898
    closing it. Both may be true of different builds and different quant schemes; **no A/B is
    published**, and the disputed cell also covers the *base* MXFP4 DeepSeek checkpoint
    ([deepseek41f/rtx6000-pro.md](../models/deepseek41f/rtx6000-pro.md)).
11. **NVFP4 KV cache on `sm_120`:** FlashInfer PR #4955 merged 2026-09-07 for SM120/121, but
    vLLM's `FLASHINFER_MLA_SPARSE_DSV41` SM12x branch still *requires* an FP8 dtype, and
    TRT-LLM has no SM120/121 `QE4m3KvE2m1` variant. Engine-version-dependent from now on, not
    architectural ([flash-attention.md §11](../cross-cutting/flash-attention.md),
    [gpus/rtx6000-pro.md §6](../gpus/rtx6000-pro.md)).
12. **MXFP4 via Marlin on A100** — vLLM's hardware matrix claims coverage; whether a 4-bit MoE
    checkpoint loads *and is usable* is untested, and vLLM #35922 reports the Marlin MoE FP4
    path crashing there ([kimik3/a100.md](../models/kimik3/a100.md)).
13. **MI355X sparse MXFP4/MXFP6:** AMD's datasheet lists **N/A** in the sparsity column;
    third-party pages quote 20.1 PFLOPS. Assume no structured-sparsity 4/6-bit path on CDNA4.
14. **Kimi-K3 A8W4 vs A4W4:** vLLM steers to a4w4 and warns *"Do not set
    `AITER_SITUV2_A8W4=1`"*; SGLang ships a8w4 and measures it **faster**. Measure on your
    own traffic ([quantization-formats.md Q7](../cross-cutting/quantization-formats.md)).
15. **B300's 2× SFU attention uplift** is an architectural claim with **no published
    end-to-end measurement** isolating it for CSA2 or KDA — exactly the two kernels it
    targets ([quantization-formats.md §4.3, Q11](../cross-cutting/quantization-formats.md)).
16. **No FP8-attention-vs-BF16-attention speedup is published on any GPU** in this roster.
    *"The naive expectation is ~2× on the attention matmuls alone … Do not plan on 2×."*

**Decode bytes**

17. **DeepSeek-V4.1-Flash observed KV ≈ 2,340 B/token on B200 vs the architectural 890** —
    decomposition unresolved between FP8 default, SWA ring allocation, and block padding
    (§4.3, [deepseek41f/b200.md](../models/deepseek41f/b200.md)).
18. **H100's executed KV dtype for DeepSeek-V4.1-Flash** is stated as BF16 3,200 B/token in
    one pair doc and FP8 1,650 in its siblings (§4.3).

**Speculative decoding**

19. **No DSpark acceptance measurement exists for the NVFP4 checkpoint on any GPU**, and
    NVIDIA states speculation *"was not exercised in the reported validation"*. Every cost
    figure that leans on it is leaning on a synthetic constant.
20. **`num_spec != 0` disables both fused KDA decode kernels.** Confirm what engines actually
    do when Kimi-K3 runs DSpark-8 with ReplaySSM
    ([flash-attention.md Q31, §14.4](../cross-cutting/flash-attention.md)).
21. **The acceptance-vs-batch curve is unpublished for every model here.** Sweep batch size
    reading `vllm:spec_decode_num_{accepted,draft}_tokens_total` before sizing anything on a
    speculation multiplier.

---

## Source documents in this tree

- [`METHODOLOGY.md`](../METHODOLOGY.md) — formulas, legend, §8 pinned GPU/model inputs
- [`cross-cutting/flash-attention.md`](../cross-cutting/flash-attention.md) — §1 SM/family
  semantics, §2 FA2/3/4 dispatch, §3 FlashInfer, §4 TRT-LLM, §5 FlashMLA, §6 DSA/CSA2,
  §8 AMD, §9 per-GPU grids, §10–§11 FP8/NVFP4 attention and KV, §14 MLA/GDN/KDA, §15 ratios
- [`cross-cutting/quantization-formats.md`](../cross-cutting/quantization-formats.md) —
  §2–§3 format definitions, §4 per-GPU silicon, §5 kernel matrix, §6 measured throughput,
  §7 accuracy, §8 KV quant, §9 the repo's checkpoints, §10 engine flags
- [`gpus/*.md`](../gpus/) — per-GPU peaks, measured kernels, known defects
- [`models/<exp>/<gpu>.md`](../models/) — the 40 audited pair analyses
- [`matrix/pairs.json`](./pairs.json) — machine-readable summary; §4.1 is generated from its
  `weight_format_used` field
