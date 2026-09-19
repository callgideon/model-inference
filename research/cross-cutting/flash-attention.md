# Attention kernels across GPUs — cross-GPU reference

**Research date: 2026-09-19.** Follows `research/METHODOLOGY.md`: every number carries an
inline `[src]` link or is marked **⚠️ TO BE VERIFIED** with the estimation method stated.
Dense and sparse TFLOPS are never mixed. SXM and PCIe are never mixed.

This document answers one question: *for a given (model, GPU, engine) triple as of
2026-09-19, which attention kernel actually runs, is it the optimized path, and how fast
is it?* Weight quantization, MoE kernels, KV-cache sizing and cost are covered in the
sibling documents under `research/gpus/`, `research/models/` and `research/matrix/`.

---

## 1. GPU roster, SM numbers and family semantics

Kernel gating in every engine is written against CUDA compute capability, not product
name. Getting the mapping right is the whole ballgame.

| GPU | Arch | Compute cap. | `major` | CUDA 13 "family" | Notes |
|---|---|---|---|---|---|
| A100 SXM / PCIe | Ampere | 8.0 | 8 | 8.x | [src](https://github.com/flashinfer-ai/flashinfer#gpu-support) |
| H100 SXM / PCIe / NVL | Hopper | 9.0 | 9 | 9.x | [src](https://github.com/flashinfer-ai/flashinfer#gpu-support) |
| H200 SXM / NVL | Hopper | 9.0 | 9 | 9.x | same SM as H100; more HBM [src](https://www.nvidia.com/en-us/data-center/h200/) |
| H800 SXM5 | Hopper | 9.0 | 9 | 9.x | export variant; FlashMLA's reference part |
| B200 SXM (HGX B200) | Blackwell | 10.0 | 10 | 10.x | [src](https://github.com/flashinfer-ai/flashinfer#gpu-support) |
| B300 (Blackwell Ultra) | Blackwell | 10.3 | 10 | 10.x | `sm_103` / `sm_103a` [src](https://github.com/flashinfer-ai/flashinfer#gpu-support) |
| GB300 NVL72 | Blackwell | 10.3 | 10 | 10.x | 72× B300 + 36 Grace [src](https://www.nvidia.com/en-us/data-center/gb300-nvl72/) |
| RTX PRO 6000 Blackwell | Blackwell | 12.0 | 12 | 12.x | same family as RTX 50-series [src](https://github.com/flashinfer-ai/flashinfer#gpu-support) |
| DGX Spark (GB10) | Blackwell | 12.1 | 12 | 12.x | listed alongside RTX 50 series [src](https://github.com/flashinfer-ai/flashinfer#gpu-support) |
| Jetson Thor | Blackwell | 11.0 | 11 | 11.x | this is what `SM110` in FA4/vLLM code means [src](https://github.com/flashinfer-ai/flashinfer#gpu-support) |
| MI355X / MI350X | CDNA4 | `gfx950` | — | — | [src](https://github.com/ROCm/aiter) |
| MI300X / MI325X | CDNA3 | `gfx942` | — | — | [src](https://github.com/ROCm/aiter) |

**Why "family" matters.** vLLM implements CUDA-13 family semantics:
`is_device_capability_family(100)` returns true when `capability // 10 == 10`, i.e. for
**both** `sm_100` (B200) **and** `sm_103` (B300/GB300)
[src](https://github.com/vllm-project/vllm/blob/main/vllm/platforms/interface.py).
Upstream FlashAttention-4 does the same thing with `arch // 10`
[src](https://github.com/Dao-AILab/flash-attention/blob/main/flash_attn/cute/interface.py).
So almost every "SM100" claim in this document silently includes B300/GB300 — that is a
real behaviour, not sloppy wording, and it is called out where it is *not* true.

**A trap:** `SM110` in FA4 and vLLM source is **Jetson Thor**, not a datacenter part. Code
paths gated on `arch // 10 in [10, 11]` include Thor, not RTX PRO 6000.

### 1.1 Peak dense reference (only for "% of peak" arithmetic)

Full spec work lives in `research/gpus/`. These are the denominators used below.

All figures **dense**, per GPU, matching the pinned values in `research/METHODOLOGY.md` §8.

| GPU | Dense BF16 | Dense FP8 | Dense FP4 | Source / method |
|---|---|---|---|---|
| A100 SXM 80GB | 312 | none (INT8 624 TOPS) | none | **Verified 2026-09-19**: NVIDIA A100 page lists BFLOAT16 Tensor Core "312 TFLOPS \| 624 TFLOPS\*", footnote "\* With sparsity" — 312 *is* the dense figure [src](https://www.nvidia.com/en-us/data-center/a100/) |
| H100 / H200 / H800 SXM | 989.5 | 1,979 | none | H200 SXM BF16 Tensor Core = 1,979 TFLOPS **with sparsity** [src](https://www.nvidia.com/en-us/data-center/h200/); dense = ÷2 |
| B200 SXM (HGX B200) | 2,250 | 4,500 | 9,000 | **Verified 2026-09-19**: HGX B200 FP16/BF16 Tensor Core = 36 PFLOPS for 8 GPUs, footnote "Specification in Sparse. Dense is ½ sparse spec shown" → 36000/8/2 = 2,250 dense per GPU [src](https://www.nvidia.com/en-us/data-center/hgx/). Cross-check: FA4's 1,613 TFLOPS at "71% hardware utilization" implies 1613/0.71 ≈ 2,272 [src](https://lambda.ai/blog/flashattention-4-gives-the-nvidia-blackwell-platform-its-most-optimized-attention-kernel-yet) |
| **HGX B300** (8-GPU air-cooled) | **2,250** | **4,500** | **13,500** | **Corrected 2026-09-19**: HGX B300 FP16/BF16 = 36 PFLOPS sparse for 8 GPUs, *the same as HGX B200* → 2,250 dense per GPU; FP4 is listed in explicit sparse\|dense form "144 PFLOPS \| 108 PFLOPS" → 13,500 dense per GPU [src](https://www.nvidia.com/en-us/data-center/hgx/). Blackwell Ultra's 1.5× uplift is **FP4-only**; BF16 and FP8 are unchanged from B200. **Do not use 2,500 for an HGX B300.** |
| B300 in **GB300 NVL72** | 2,500 | 5,000 | 15,000 | Rack FP16/BF16 = 360 PFLOPS, footnote "All Tensor Core specifications are with sparsity unless otherwise noted" [src](https://www.nvidia.com/en-us/data-center/gb300-nvl72/); dense per GPU = 360/72/2 × 1000 = 2,500 (`est.`). FP4 dense = 1,080 PFLOPS ("Without sparsity") / 72 = 15,000. The 2,500 vs 2,250 gap is the NVL72 board power/clock bin, exactly as GB200 vs HGX B200. |
| RTX PRO 6000 Blackwell **Server Edition** | **500** `est.` | 1,000 `est.` | **2,000** `est.` | **Corrected 2026-09-19 (wrong SKU), arithmetic re-corrected 2026-09-19 (sweep)**: the previously cited page is the **Workstation Edition** product page — re-fetched, it describes the Workstation part ("1792 GB/sec", "4000" AI TOPS footnoted *"Theoretical FP4 TOPS using sparsity"*, 125 TFLOPS FP32, 600 W) and carries **no Server Edition figure at all** [src](https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000/). The Server Edition is the part in this roster; its page, fetched this pass, prints **96 GB GDDR7, 1597 GB/s**, FP32 120 TFLOPS, BF16 "1 PFLOPS", FP8 "2 PFLOPS", FP4 "4 PFLOPS", of which the PFLOPS rows are **sparse**; dense = ½, i.e. **500 / 1,000 / 2,000** [src](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/), [src](https://lenovopress.lenovo.com/lp2263.pdf), reconciliation in [gpus/rtx6000-pro.md §3c](../gpus/rtx6000-pro.md). FP4 2,000 dense matches METHODOLOGY §8 ("≈ 2,000 dense, 4,000 sparse"); the earlier 480 / 960 / 1,920 was a clock-derived estimate that did not match its own stated ½-of-sparse derivation. Workstation Edition, for contrast, is 503.8 / 1,007.6 / 2,015.2 dense at **1,792 GB/s**. ⚠️ `est.` — the dense rows are halved sparse rows, not a published dense datasheet row. |
| MI355X (`gfx950`) | **2,500** | 5,000 | 10,100 (MXFP4 **and** MXFP6) | **Corrected 2026-09-19**: 2,510 (derived from an unopenable flopper.io page) → **2,500**, the value pinned in METHODOLOGY §8. ⚠️ **Citation corrected in this sweep**: the ROCm CDNA4 architecture page was opened and carries **only** "VRAM (GiB): 288", "Architecture: CDNA4", "LLVM target name: gfx950" — **no FLOPS row at all** [src](https://rocm.docs.amd.com/en/latest/reference/gpu-arch-specs.html), so it cannot be the source for 2,500. AMD's MI355X GPU datasheet does state BF16 2.5 PFLOPS dense / 5 PFLOPS sparse and FP8 5 / 10.1 PFLOPS, matching the pinned values, but `amd.com` timed out again on both the product page and the PDF this pass [src](https://www.amd.com/content/dam/amd/en/documents/instinct-tech-docs/product-briefs/amd-instinct-mi355x-gpu-brochure.pdf) — so the peak is still **second-hand ⚠️ TO BE VERIFIED**, now with the right repo-wide denominator. All MI355X percentages below are computed against 2,500. |

Also fixed for GB300 NVL72, quoted rack-level and **with sparsity unless footnoted**:
FP4 1,440 PFLOPS, FP8/FP6 720 PFLOPS, FP16/BF16 360 PFLOPS, TF32 180 PFLOPS, INT8 24 POPS,
GPU memory 20 TB at up to 576 TB/s, NVLink 130 TB/s, fast memory 37 TB (20 TB HBM + 17 TB
LPDDR5X at 14 TB/s), 72 Blackwell Ultra GPUs + 36 Grace CPUs
[src](https://www.nvidia.com/en-us/data-center/gb300-nvl72/). **Resolved 2026-09-19**: the
second FP4 figure next to 1,440 is **1,080 PFLOPS, footnoted "Without sparsity"** — so
FP4 dense = 1,080/72 = **15 PFLOPS per B300**. Cross-checked against HGX B300, which lists
FP4 as "144 PFLOPS | 108 PFLOPS" in explicit sparse|dense form → 108/8 = 13.5 PFLOPS dense
per GPU, exactly 2,250/2,500 of the NVL72 figure [src](https://www.nvidia.com/en-us/data-center/hgx/).
The two parts scale together at 1.111×, which is the clock bin, not an architectural
difference.

---

## 2. FlashAttention 1 / 2 / 3 / 4 — per-architecture support

### 2.1 What the upstream repo says

| Version | Stated target | Datatypes | Exact wording |
|---|---|---|---|
| FA1 | Ampere-era | fp16 | superseded; not packaged separately in the current repo |
| FA2 | "Ampere, Ada, or Hopper GPUs (e.g., A100, RTX 3090, RTX 4090, H100)" | fp16, bf16 (bf16 requires Ampere+) | [src](https://github.com/Dao-AILab/flash-attention) |
| FA3 | "FlashAttention-3 is optimized for Hopper GPUs (e.g. H100)" | "FP16 / BF16 forward and backward, FP8 forward"; CUDA ≥ 12.3 | [src](https://github.com/Dao-AILab/flash-attention) |
| FA4 | "written in CuTeDSL and optimized for Hopper and Blackwell GPUs (e.g. H100, B200)" | see §2.2 | [src](https://github.com/Dao-AILab/flash-attention) |

FA2 head dims: "All head dimensions up to 256"; head dim > 192 backward needs A100/H100
[src](https://github.com/Dao-AILab/flash-attention).

There is **no** maintainer-published FA4 support matrix. Asked directly in
[issue #2376](https://github.com/Dao-AILab/flash-attention/issues/2376), Tri Dao replied
on 2026-03-21: *"Eventually we'll cover Sm80-Sm120. Rn some features are implemented for
some arch but not others (hence many PRs). This is changing rapidly. You can look at the
tests to see what is tested (currently Sm90 - Sm100)"* and, in follow-up, *"The tests will
indicate what features are implemented / tested on which architecture."*
[src](https://github.com/Dao-AILab/flash-attention/issues/2376). **So the dispatcher and
the test file are the ground truth, not the README.**

### 2.2 FA4 dispatch, read from `flash_attn/cute/interface.py` (main, 2026-09)

Verbatim gates [src](https://github.com/Dao-AILab/flash-attention/blob/main/flash_attn/cute/interface.py):

| Gate | Code | Meaning |
|---|---|---|
| Forward arch set | `assert arch // 10 in [8, 9, 10, 11, 12]` | sm_8x, sm_9x, sm_10x (100 **and 103**), sm_11x (Thor), sm_12x |
| Backward arch set | `assert arch // 10 in [9, 10, 11, 12]` | **no SM80 backward** |
| FP8 | `assert arch // 10 == 10, "FP8 is only supported on SM100 (compute capability 10.x) for FA4 CuTe."` | FP8 attention is **SM100-family only** |
| Kernel classes | `FlashAttentionForwardSm80 / Sm90 / Sm100 / Sm120`, `FlashAttentionBackwardSm80/Sm90/Sm100/Sm120` | four separate forward implementations |
| MLA forward | `FlashAttentionMLAForwardSm100`, selected only under `arch // 10 in [10, 11]` when `qv is not None` | MLA-absorbed path is Blackwell-DC/Thor only |
| Sparse MLA backward | `FlashAttentionSparseMLABackwardSm100` + `dQdQvGemmKernel` + `dKGemmKernel` | added in beta19: *"[Cute,Bwd,Sm100] add sparse MLA (Deepseek v4) backward kernels"* [src](https://github.com/Dao-AILab/flash-attention/releases) |
| hd256 2-CTA | `use_dedicated_hd256_kernel = arch // 10 in [10, 11] and head_dim == 256 and head_dim_v == 256` → `BlackwellFusedMultiHeadAttentionForward` | dedicated Blackwell head-dim-256 kernel |
| Learnable sink bwd | `assert arch // 10 in [9, 10, 11], "Learnable sink backward is supported on SM90 and SM100/SM110"` | |
| SM120 restrictions | `assert not use_block_sparsity, "Block sparsity not supported on SM 12.0"`; `assert page_table is None, "Paged KV not supported on SM 12.0 in this PR"`; `assert not is_split_kv, "SplitKV not supported on SM 12.0 in this PR"` | **paged KV is unavailable** on sm_120 in FA4 upstream |
| SM120 comment | `# SM120 (Blackwell GeForce / DGX Spark): uses SM80 MMA with SM120 SMEM capacity` | sm_120 runs the *Ampere* MMA path, not tcgen05 |
| SM80/SM120 threads | `if arch // 10 in [8, 12]: num_threads = 128` | 4 warps, i.e. the FA2-class shape |

Head-dim validation, from `_validate_head_dims` in the same file:

| Arch | Allowed (head_dim, head_dim_v) |
|---|---|
| SM90 (cc 9) | both in `[8, 256]`, divisible by alignment |
| SM100/SM110 (cc 10, 11) | both in `[8, 128]`, **or** `(192, 128)` "for DeepSeek", **or** `(256, 256)` hd256 kernel, **or** the MLA-absorbed shapes `head_dim ∈ {64, head_dim_v}` with `head_dim_v == 512` |
| SM80 / SM120 | not validated by this function (`if arch // 10 not in [8, 12]`) |

**`sm_103` status, precisely.** `sm_103` is *not* a separate dispatch branch — it falls
into the `arch // 10 == 10` bucket and therefore gets every SM100 kernel including FP8 and
MLA. On top of that, PR #2696 *"add tcgen.ld.red support to sm103a arch"* merged
2026-07-11 adds a B300-specific tensor-memory reduction path, explicitly *"Inspired by
hao-ai-lab/flash-attention-fp4"*, shipped in `fa4-v4.0.0.beta22`
[src](https://github.com/Dao-AILab/flash-attention/pull/2696). So **B300/GB300 is a
first-class FA4 target with one extra instruction path B200 does not use.**

**`sm_120` status, precisely.** Upstream FA4 *does* have `FlashAttentionForwardSm120` /
`FlashAttentionBackwardSm120`, actively maintained (beta21: *"Fix CuTe SM120 compile-time
argument handling"*, *"[NVIDIA][CuTe,Fwd,sm120] Implement Pack-GQA on SM120 (+ graceful
SplitKV fallback)"*; beta30: *"[CuTe, SM80/SM120] Guard invalid varlen forward tiles"*)
[src](https://github.com/Dao-AILab/flash-attention/releases). But it is the SM80 MMA path
with no block sparsity, no paged KV, no SplitKV, no FP8. And critically — see §2.3 —
**vLLM refuses to use FA4 on sm_120 at all.**

Test coverage (`tests/cute/test_flash_attn.py`) defines `IS_SM90 / IS_SM100 / IS_SM110 /
IS_SM120` and skips heavily: SplitKV is unsupported on SM90 and SM120; `hdim > 192`
backward xfails on SM90; SM100's hd256 2-CTA kernel skips `learnable_sink`, local
attention, softcap and deterministic mode
[src](https://github.com/Dao-AILab/flash-attention/blob/main/tests/cute/test_flash_attn.py).

Release cadence: `fa4-v4.0.0.beta31` published **2026-09-16**, three days before this
document's date — FA4 is still on beta tags, not a 4.0.0 GA
[src](https://github.com/Dao-AILab/flash-attention/releases).

### 2.3 What vLLM's bundled `vllm_flash_attn` actually allows

This differs from upstream and is what you get in production
[src](https://github.com/vllm-project/vllm/blob/main/vllm/vllm_flash_attn/flash_attn_interface.py):

```
FA2: current_platform.has_device_capability(80)               → cc ≥ 8.0
FA3: current_platform.is_device_capability_family(90)         → 9.x only
FA4: is_device_capability_family(90) or (100) or (110)        → 9.x, 10.x, 11.x
     "FA4 is only supported on devices with compute capability 9.x, 10.x, or 11.x"
```

**FA4 is explicitly not offered on 12.x in vLLM.** RTX PRO 6000 Blackwell therefore falls
back to FA2 for the FlashAttention backend, despite upstream having an SM120 kernel.

Default version selection [src](https://github.com/vllm-project/vllm/blob/main/vllm/v1/attention/backends/fa_utils.py):

```
major == 9  and FA3 available  → FA3     # "Hopper (SM90): prefer FA3"
major == 10 and FA4 available  → FA4     # "Blackwell (SM100+, restrict to SM100 for now)"
else                           → FA2
major >= 10 and version == 3   → FA4 if available else FA2
```

Overridable with `--attention-config.flash_attn_version={2,3,4}`
[src](https://docs.vllm.ai/en/latest/design/attention_backends/). Documented default:
*"Default is FA4 on SM100+ (Blackwell), FA3 on SM90 (Hopper), FA2 otherwise."*

Automatic downgrades to FA2 that bite in practice, all from `fa_utils.py`:
ALiBi (any version); `VLLM_BATCH_INVARIANT=1` with FA4 (*"FA4 currently uses
batch-shape-dependent scheduling heuristics on SM100+, which breaks batch invariance"*);
and on Blackwell, **any `head_size > 128` that is not `(256,256)` or `(192,128)`** —
*"FA4 on Blackwell does not support head_size=%d due to TMEM capacity limits, defaulting
to FA version 2."* The hd256 kernel additionally falls back to FA2 for attention sinks,
logit soft capping, quantized KV, a block size not a multiple of 128, mm_prefix
bidirectional attention, R-SWA, or DCP.

FA3 → FA4 *upgrades* on SM90 happen for `head_size > 256`, Diff-KV with sinks, and
diffusion models needing per-sequence causal masking.

vLLM's published backend table, auto-generated from
`AttentionBackend.validate_configuration()`
[src](https://docs.vllm.ai/en/latest/design/attention_backends/):

| Backend | Version | Dtypes | KV dtypes | Block sizes | Head sizes | Sink | Non-causal | DCP | Compute cap. |
|---|---|---|---|---|---|---|---|---|---|
| FLASH_ATTN | FA2 | fp16, bf16 | auto, float16, bfloat16 | %16 | Any | ❌ | ✅ | ✅ | ≥ 8.0 |
| FLASH_ATTN | FA3 | fp16, bf16 | + fp8, fp8_e4m3 | %16 | Any | ✅ | ✅ | ✅ | 9.x |
| FLASH_ATTN | FA4 | fp16, bf16 | + fp8, fp8_e4m3 | %16 | Any | ✅ | ✅ | ✅ | ≥ 10.0 |

Note the table says "≥10.0" for FA4 while the runtime gate in `vllm_flash_attn` is
"9.x, 10.x, 11.x" — the table describes eligibility of the *backend configuration*, the
interface function describes what the compiled wheel contains. On sm_120 the config check
passes and the version selector then hands you FA2. ⚠️ **TO BE VERIFIED**: whether a
locally built `vllm_flash_attn` with `FLASH_ATTENTION_ARCH=120` changes this; upstream FA4
has `FLASH_ATTENTION_ARCH` as an override env var
[src](https://github.com/Dao-AILab/flash-attention/blob/main/flash_attn/cute/interface.py)
but vLLM's `_is_fa4_supported` gate is independent of it.

### 2.4 SGLang's FA3 / FA4

SGLang exposes `fa3` and `fa4` as first-class `--attention-backend` values. From its
support matrix [src](https://docs.sglang.io/advanced_features/attention_backend.html):

| Backend | Native page > 1 | FP8 KV | FP4 KV | Spec topk=1 | Spec topk>1 | Sliding window | Multimodal |
|---|---|---|---|---|---|---|---|
| FA3 | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ |
| FA4 | 128 (fixed) | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ |
| FlashInfer | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | ❌ |
| Triton | emulated | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| TRTLLM MHA | 16/32/64 | ✅ | ✅ | ✅ | ❌ | ✅ | ❌ |
| AITER (ROCm) | ✅ | ✅ | ❌ | ✅ | ✅ | ✅ | ✅ |
| Wave (ROCm) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

Note the inversion vs vLLM: **SGLang lists FA4 as FP4-KV-capable and FP8-KV-incapable**,
while vLLM lists FA4 KV dtypes as `auto, float16, bfloat16, fp8, fp8_e4m3`. These are
different integrations of the same kernel family and both statements are as published.
⚠️ **TO BE VERIFIED**: reconcile — most likely SGLang's "FP4 KV" column refers to the
`nvfp4_ds_mla`-style compressed cache path and its "FP8 KV ❌" to the plain per-tensor FP8
path not being wired for FA4 in SGLang yet.

SGLang defaults [src](https://docs.sglang.io/advanced_features/attention_backend.html):
Hopper → `fa3` (CUDA 12.3+); Blackwell → `trtllm_mha` for MHA models, `flashinfer` for
MLA with `trtllm_mla` auto-selected for DeepSeek-V3-class; everything else → `flashinfer`,
falling back to `triton`.

### 2.5 FA3 / FA4 measured performance

| Kernel | GPU | Config | Measured | % of dense BF16 peak (`est.`) | Source |
|---|---|---|---|---|---|
| FA4 forward | B200 | BF16, hd 128, seq 1k–32k, causal & non-causal | **1,613 TFLOPS**, stated "71% hardware utilization" | 71.7% (1613 ÷ 2250) | [src](https://arxiv.org/html/2603.05451v1), [src](https://lambda.ai/blog/flashattention-4-gives-the-nvidia-blackwell-platform-its-most-optimized-attention-kernel-yet) |
| FA4 vs cuDNN 9.13 | B200 | same | **up to 1.3×** (corrected from "1.1–1.3×"; the paper states only an upper bound, and notes cuDNN absorbed FA4 techniques from v9.13–9.14 so newer cuDNN is comparable) | — | [src](https://arxiv.org/html/2603.05451v1) |
| FA4 vs Triton | B200 | same | **up to 2.7×** (corrected from "2.1–2.7×"; the 2.1× lower bound appears in neither the paper abstract nor the Lambda write-up) ⚠️ | — | [src](https://arxiv.org/html/2603.05451v1) |
| FA4 custom variants fwd vs Triton | GB200 NVL72 | dense/causal 1.6–3.2×, ALiBi 1.2–2.1×, doc-mask up to 2.7×, sliding window 1.4–2.1× → **1.2–3.2× overall** | 1.2–3.2× | — | [src](https://lambda.ai/blog/flashattention-4-gives-the-nvidia-blackwell-platform-its-most-optimized-attention-kernel-yet) |
| FA4 custom variants bwd vs Triton | GB200 NVL72 | dense/causal 1.85–2.3×, ALiBi 1.9–2.9×, doc-mask up to 3×, sliding window **1.8**–2.2× | **1.8–3×** (corrected from 1.85–3×; sliding-window backward starts at 1.8×) | — | [src](https://lambda.ai/blog/flashattention-4-gives-the-nvidia-blackwell-platform-its-most-optimized-attention-kernel-yet) |
| FA3 forward | H100 | BF16, hd 128 | **~740 TFLOPS**, "75% utilization" | 74.8% (740 ÷ 989.5) | [src](https://lambda.ai/blog/flashattention-4-gives-the-nvidia-blackwell-platform-its-most-optimized-attention-kernel-yet) |

The FA4 paper tests head dims 64, 128 and (192,128) and covers causal, MHA/MQA/GQA, varlen
and deterministic backward; it makes **no FP8 or FP4 attention claim** and publishes no
paged/sparse/MLA numbers [src](https://arxiv.org/html/2603.05451v1). FP8 FA4 exists in the
code (SM100-only) and has had accuracy fixes as recently as beta23/beta24 (*"Fix FP8 e4m3
accuracy: make max_offset dtype-aware to avoid P saturation"*, *"Numeric tweaks to fp8"*)
[src](https://github.com/Dao-AILab/flash-attention/releases) — treat FP8 FA4 as
**young**, and re-validate accuracy on your own evals.

No published FA4 number on **B300/GB300** was found. ⚠️ **TO BE VERIFIED** — estimation
method if you need a planning figure: scale the B200 1,613 TFLOPS by the dense BF16 peak
ratio 2500/2250 = 1.11× → **~1,790 TFLOPS** `est.`, which assumes the `sm103a`
`tcgen.ld.red` path buys nothing extra and that clocks hold.
**Correction 2026-09-19: that 1.11× applies only to a B300 in a GB300 NVL72.** An
air-cooled **HGX B300 has the same 2,250 TFLOPS dense BF16 peak as B200**
[src](https://www.nvidia.com/en-us/data-center/hgx/), so the scale factor there is 1.0× and
the planning figure is **~1,613 TFLOPS**, identical to B200. Blackwell Ultra's 1.5× uplift
is in FP4, not in BF16 — a BF16 attention kernel sees no Ultra bonus at all.

---

## 3. FlashInfer

FlashInfer is *"a library and kernel generator for inference … unified APIs for attention,
GEMM, and MoE operations with multiple backend implementations including
FlashAttention-2/3, cuDNN, CUTLASS, and TensorRT-LLM"*, claiming *"Support for SM75
(Turing) and later (through Blackwell)"* and *"FP8 and FP4 quantization for attention,
GEMM, and MoE operations"* [src](https://github.com/flashinfer-ai/flashinfer). Its own
caveat: *"Not all features are supported across all compute capabilities."*

Attention feature list: decode/prefill/append kernels, *"MLA Attention: Native support for
DeepSeek's Multi-Latent Attention"*, cascade attention for shared prefixes,
*"Sparse Attention: Block-sparse and variable block-sparse patterns"*, and POD-Attention
(fused prefill+decode) [src](https://github.com/flashinfer-ai/flashinfer).

Current release line: `v0.6.18` (2026-08-29), `v0.6.18.post1` (2026-09-05), with
`v0.7.0rc3` (2026-09-16) in RC [src](https://github.com/flashinfer-ai/flashinfer/releases).

### 3.1 Attention-relevant release history, 2026

| Version | Date | Attention-relevant content |
|---|---|---|
| v0.6.14 | 2026-07-02 | *"head_dim=512 attention for Gemma 4's global layers now runs on SM120/121"*; FMHAv2 gains *"head_dim 256/512 + sliding-window masks"*; CuTe-DSL GDN rewrite, *"~20–25% GDN prefill speedup"*; *"New sparse-MLA paged-attention kernels extend the DeepSeek-V4 and DeepSeek-V3.2/GLM-5.1 families onto SM120/121"*; SM90 gains *"native FP8 KV cache support"* [src](https://flashinfer.ai/releases/) |
| v0.6.15 | 2026-07-17 | *"DeepSeek-class MLA decode extends onto Blackwell B300 (SM103) with cluster-aware CUTLASS `split_kv`"*; CuTe-DSL GQA decode adds sliding-window and attention-sink masking; Video Sparse Attention into the block-sparse API; SM120 context-parallel GDN; *"KDA recurrent-decode kernels are also optimized for lower decode latency"*; TRTLLM-GEN MoE gains *"hash-based DeepSeek-V4 routing (`hash_topk`)"* [src](https://github.com/flashinfer-ai/flashinfer/releases/tag/v0.6.15) |
| v0.6.16 | 2026-07-31 | MiniMax Sparse Attention (*"a proxy/top-k indexer plus sparse prefill, decode, and combine"*) onto SM120/121 [src](https://flashinfer.ai/releases/) |
| v0.6.17 | 2026-08-11 | Blackwell decode covers *"Kimi K3's MLA geometry — 96 global query heads against one KV head"*; MiniMax-M3 sparse attention on SM120/121 [src](https://flashinfer.ai/releases/) |
| v0.6.18 | 2026-08-29 | *"completes support for NVIDIA Rubin (SM107)"* incl. *"trtllm-gen FMHA for SM107, including sparse compression and FP16 softmax"*; **FP8 Heavily Compressed Attention (HCA) decode for SM100/SM103** via `trtllm_batch_decode_sparse_mla_dsv4(..., backend="cute-dsl")`, *"taking arbitrary sliding-window row order including ring rotation and wraparound while keeping the compressed cache paged"*; new `flashinfer.top_k_varlen` (Blackwell radix kernel + guess-verify-refine warm-started from the previous step's indices + CUTLASS fallback for any GPU); *"SM120/121 also picks up top-k 192 and 256"*; `fused_kda_decode` [src](https://github.com/flashinfer-ai/flashinfer/releases/tag/v0.6.18) |

Open/in-flight work specifically naming our models:
PR #4955 *"feat(sm120): add NVFP4 sparse MLA support for DeepSeek V4 Flash"*
[src](https://github.com/flashinfer-ai/flashinfer/pull/4955) and
PR #4982 *"feat(MSA): Add NVFP4 paged-KV MSA sparse decode and prefill for SM100/SM103"*
[src](https://github.com/flashinfer-ai/flashinfer/pull/4982).

**Resolved 2026-09-19 — both are MERGED, not open:**

| PR | Status | Detail |
|---|---|---|
| #4955 | **merged 2026-09-07** | SM120/SM121. Adds native NVFP4 quantized KV as an opt-in alternative to the default FP8, with streaming prefill and grouped split-K decode kernels. Measured **on an RTX PRO 5000 Blackwell** (not a 6000): prefill 1.41–1.67×, decode 1.31×, ~9% end-to-end serving throughput. Supported config: "16/32/64/128 query heads, primary top-k 128 or 512, primary page size 64, and optional extra-cache page size 2 or 64" [src](https://github.com/flashinfer-ai/flashinfer/pull/4955) |
| #4982 | **merged 2026-09-18** | SM100/SM103, NVFP4 KV as packed uint8 with e4m3 block scales; decode + prefill without materializing dense copies; adds an `out=` parameter to the decode API; **prefill requires CUDA 13.0+** [src](https://github.com/flashinfer-ai/flashinfer/pull/4982) |

The #4955 merge is the consequential one: **FlashInfer does now have an NVFP4 sparse-MLA
kernel for sm_120**, which contradicts the flat "no NVFP4 on RTX PRO 6000" reading. See
§11 for what vLLM's dtype gate still does with it.

### 3.2 FlashInfer as seen through vLLM's backend table

[src](https://docs.vllm.ai/en/latest/design/attention_backends/)

| Backend / variant | KV dtypes | Block sizes | Head sizes | Sink | DCP | Compute cap. |
|---|---|---|---|---|---|---|
| FLASHINFER — Native | auto, float16, bfloat16, fp8, fp8_e4m3, fp8_e5m2, **nvfp4_4over6** | 16–1024 | 64, 128, 256, 512 | ❌ | ✅ | **8.x–9.x** |
| FLASHINFER — XQA | same | 16–1024 | 64, 128, 256, 512 | ❌ | ✅ | **9.0 only** |
| FLASHINFER — trtllm-gen | + **nvfp4** | 16–1024 | 64, 128, 256, 512 | ✅ | ✅ | **10.x** |
| FLASHINFER_MLA | auto, fp16, bf16, fp8, fp8_e4m3 | 32, 64 | Any | ❌ | ✅ | 10.x |
| FLASHINFER_MLA_SPARSE | auto, fp16, bf16, fp8, fp8_e4m3 | 32, 64 | Any | ❌ | ✅ | 10.x |
| FLASHINFER_MLA_SPARSE_SM90 | auto, bfloat16, fp8, fp8_e4m3 | %64 | 512, 576 | ❌ | ❌ | **9.x** |
| FLASHINFER_MLA_SPARSE_SM120 | auto, fp8, fp8_e4m3, fp8_ds_mla | 64, 256 | Any | ❌ | ❌ | **12.x** |
| FLASHINFER_MLA_SPARSE_DSV4 | auto, bfloat16, fp8, fp8_e4m3, fp8_ds_mla | 256 | 512 | ✅ | ❌ | **10.x, 12.x** |
| FLASHINFER_MLA_SPARSE_DSV41 | auto, bfloat16, fp8, fp8_e4m3, fp8_ds_mla | 128 | 512 | ✅ | ❌ | **10.x, 12.x** |

vLLM's FlashInfer footnote: *"FlashInfer Native is the regular FlashInfer path. XQA is the
SM90 decode path exposed through FlashInfer's TRTLLM decode API. trtllm-gen is used on
SM100 and supports sinks. Disable XQA/trtllm-gen via
`--attention-config.use_trtllm_attention=0`."*
[src](https://docs.vllm.ai/en/latest/design/attention_backends/)

Note the **priority inversion between architectures**: for standard attention there are
two priority lists, one headed by `FLASHINFER` and one headed by `FLASH_ATTN`
[src](https://docs.vllm.ai/en/latest/design/attention_backends/). In practice FlashInfer
leads on Blackwell, FlashAttention leads on Hopper.

---

## 4. TensorRT-LLM attention kernels

### 4.1 XQA

*"XQA is an optimization specifically for MQA/GQA in the generation phase."* Support
matrix as published: *"FP16 / BF16 compute data type"*, *"FP16 / BF16 / FP8 / INT8 KV cache
data type"*, *"Paged KV cache (8 / 16 / 32 / 64 / 128 tokens per block)"*. Default enabled;
`--disable_xqa` turns it off; `TRTLLM_FORCE_XQA=1` forces it where the config allows
[src](https://nvidia.github.io/TensorRT-LLM/advanced/gpt-attention.html).

XQA is surfaced through FlashInfer as the **SM90-only** decode path in vLLM
[src](https://docs.vllm.ai/en/latest/design/attention_backends/) and through SGLang as
*"TRTLLM MHA (XQA backend) (Optimized for SM90 and SM120, e.g., H20, H200, 5090). Note
that TRTLLM XQA backend only works well for pagesize 64."*
[src](https://docs.sglang.io/advanced_features/attention_backend.html)

### 4.2 FMHA

Context-phase kernel. Two variants: vanilla MHA for short sequences, Flash-Attention
algorithm for long. **FP8 context FMHA is *"only supported on Ada and Hopper"*** and is
enabled with `use_fp8_context_fmha = enable`, combinable with `use_paged_context_fmha`
[src](https://nvidia.github.io/TensorRT-LLM/advanced/gpt-attention.html). Sliding window is
implemented by treating *"kv cache as a circular buffer"* bounded by
`max_attention_window_size`. Chunked context *"splits the context into several chunks"*
and requires FMHA paged KV-cache.

⚠️ **TO BE VERIFIED**: the `gpt-attention` page above is the legacy TRT-LLM flow and its
"Ada and Hopper" FP8-FMHA statement predates Blackwell. The newer trtllm-gen FMHA
generation *is* the Blackwell/Rubin path and is exposed through FlashInfer (§3), where
SM100 and SM107 support is explicit. Do not read "only Ada and Hopper" as a statement
about trtllm-gen on B200/B300.

### 4.3 MLA

The PyTorch-flow attention page lists three backends — `VanillaAttention` (*"a reference
implementation designed primarily for inflight batching and linear KV cache support"*),
`FlashInferAttention` (*"performance-optimized and supports both inflight batching and
paged KV cache"*, FP8 quantization of inputs and KV cache, RoPE fusion) and
`TrtllmAttention` (*"serves as the default backend and supports all the features available
in the Flashinfer backend while being further optimized"*, plus fused QKV input and FP8
output) [src](https://nvidia.github.io/TensorRT-LLM/torch/attention.html). That page
contains **no** per-architecture matrix and **no** MLA-specific section.

The TRT-LLM MLA kernels are most visible through the serving engines:
- vLLM MLA **prefill** backend `TRTLLM_RAGGED`: fp16/bf16, compute cap **10.x**, shapes
  `(qk_nope=128, qk_rope=64, v=128)` or `(qk_nope=192, qk_rope=64, v=256)` only
  [src](https://docs.vllm.ai/en/latest/design/attention_backends/). On Blackwell the
  auto-selection order for MLA prefill is *"TRT-LLM Ragged, FlashInfer, then TokenSpeed
  MLA; for (qk_nope_head_dim=192, qk_rope_head_dim=64, v_head_dim=256) TRT-LLM Ragged is
  tried before FlashAttention. On other GPUs, only FlashAttention is considered."*
- SGLang `trtllm_mla`: native page sizes {32, 64}, FP8 KV ✅, FP4 KV ✅, chunked prefix
  cache ✅, spec topk=1 ✅, topk>1 ❌
  [src](https://docs.sglang.io/advanced_features/attention_backend.html)

---

## 5. FlashMLA (DeepSeek)

*"FlashMLA is DeepSeek's library of optimized attention kernels, powering the DeepSeek-V3
and DeepSeek-V3.2-Exp models."* It ships sparse kernels (token-level sparse prefill;
token-level sparse decode with FP8 KV cache) and dense kernels (prefill, decode)
[src](https://github.com/deepseek-ai/FlashMLA).

### 5.1 Support matrix, verbatim from the repo

| Kernel | GPU architecture | MLA mode | Supported models |
|---|---|---|---|
| Dense Decoding | SM90 | MQA | DeepSeek V3 / V3.1 |
| Sparse Decoding | SM90 & SM100 | MQA | DeepSeek V3.2 / V4 / V4.1 [2] |
| Dense Prefill | SM100 | MHA | DeepSeek V3 / V3.1 / V3.2 |
| Sparse Prefill | SM90 & SM100 | MQA | DeepSeek V3.2 / V4 / V4.1 |
| Fused Norm RoPE Attn RoPE Cast | SM100 | MQA | DeepSeek V4 / V4.1 |

*"[2] Sparse Decoding for DeepSeek V4.1 is only available on SM100"*
[src](https://github.com/deepseek-ai/FlashMLA). Requirements: *"SM90 / SM100 … CUDA 12.8
and above (CUDA 12.9+ is required for SM100 kernels)"*. "MQA mode" = `head_dim_k` 576
(V3/V3.1/V3.2) or **512 (V4/V4.1)** with `head_dim_v` = 512; "MHA mode" = `head_dim_k`
192/128 with `head_dim_v` 128.

**There is no `gfx9xx`, no SM120 and no SM80 row.** FlashMLA does not run on A100, on
RTX PRO 6000, or on MI355X — full stop.

The V4.1 kernels landed **2026-09-10**: *"Release of DeepSeek v4.1's Attention Kernels:
We've released attention kernels for DeepSeek-V4.1, including both prefill and decoding
(with FP8 or FP4 KV cache). We've also released a fused-norm-rope-attn-rope-cast kernel
which fuses Q-norm (only used in V4, not V4.1), Q-RoPE, core attention, O-RoPE
(conjugate), and cast-to-fp8, while retaining the same performance."*
[src](https://github.com/deepseek-ai/FlashMLA) — nine days before this document's date.

### 5.2 FlashMLA measured TFLOPS and GB/s

All numbers verbatim from the repo's Performance section
[src](https://github.com/deepseek-ai/FlashMLA); % of peak is `est.` using §1.1.

| Kernel | GPU | Measured | % of dense BF16 peak (`est.`) |
|---|---|---|---|
| Dense MLA decoding, memory-bound config | H800 SXM5, CUDA 12.8 | **3,000 GB/s** | ~89% of 3.35 TB/s HBM3 ⚠️ (H800 HBM BW not re-verified this pass) |
| Dense MLA decoding, compute-bound config | H800 SXM5, CUDA 12.8 | **660 TFLOPS** | 66.7% |
| Sparse MLA decoding (FP8 KV, BF16 matmul), compute-bound | H800 SXM5, CUDA 12.8 | **410 TFLOPS** | 41.4% |
| Sparse MLA decoding | B200 | **up to 700 TFLOPS** | 31.1% |
| Sparse MLA prefill forward | H800 SXM5, CUDA 12.8 | **640 TFLOPS** | 64.7% |
| Sparse MLA prefill forward | B200, CUDA 12.9 | **up to 1,450 TFLOPS** | 64.4% |
| Dense MHA prefill forward (SM100) | B200 | **up to 1,460 TFLOPS** "as reported by NVIDIA" | 64.9% |
| Dense MHA prefill backward (SM100) | B200 | **1,000 TFLOPS** "as reported by NVIDIA" | 44.4% |
| Fused norm+RoPE+attn+RoPE+cast, prefill | B200 | **up to 1,430 TFLOPS** | 63.6% |
| Fused norm+RoPE+attn+RoPE+cast, decode | B200 | **up to 670 TFLOPS** | 29.8% |

Two things to read off this table. **(a)** Sparse MLA prefill holds ~64% of peak on both
Hopper and Blackwell — the Blackwell kernel is well-tuned, this is not a port. **(b)**
Sparse MLA *decode* falls from 41% of peak on H800 to 31% on B200: Blackwell's tensor
cores outgrew what the decode kernel can feed. Decode on Blackwell is the weaker half of
the FlashMLA story and that shows up directly in interactive-SLO planning.

The fused mega kernel matters more than its headline number suggests: it removes a chain
of small launches (Q-norm, Q-RoPE, O-RoPE, FP8 cast) at "the same or even slightly higher
TFlops", at the cost of *"having to permute the Q_b and Wv weights in advance"*
[src](https://github.com/deepseek-ai/FlashMLA). At decode batch sizes, launch overhead is
often the real cost.

No published **B300/GB300** FlashMLA number exists. ⚠️ **TO BE VERIFIED** — planning
estimate: scale B200 by 2500/2250 = 1.11× → sparse prefill **~1,610 TFLOPS**, sparse
decode **~780 TFLOPS** `est.`, assuming the kernel is peak-limited rather than
memory/schedule-limited (which the decode drop above suggests it may not be — treat the
decode estimate as an upper bound).
**Corrected 2026-09-19: that 1.11× is a GB300-NVL72-only scale factor.** For an air-cooled
**HGX B300** the dense BF16 peak is 2,250 — identical to B200
[src](https://www.nvidia.com/en-us/data-center/hgx/) — so the planning figures there are
simply B200's: sparse prefill ~1,450, sparse decode ~700.

---

## 6. DeepSeek Sparse Attention (DSA) and its V4.1 successor (CSA2)

### 6.1 The mechanism

DSA as introduced with V3.2-Exp: a *lightning indexer* scores the KV cache per query, the
top-k positions are selected, and attention runs only over those. FlashMLA's sparse
kernels are the reference implementation and *"achieve up to 640 TFlops during prefilling
and 410 TFlops during decoding"*
[src](https://github.com/deepseek-ai/FlashMLA).

DeepSeek-V4.1-Flash replaces DSA with **Compressed Sparse Attention 2 (CSA2)**, described
on the model card as assigning *"each attention layer one of three static modes — Full,
Reindex, or Reuse — to share main KV and indexer K across layers and reuse Top-K
sparse-attention indices. In the decoder, a Hierarchical Sparse Indexer further restricts
later indexing layers to a candidate pool constructed by the first Full Mode layer,
bounding deeper indexer cost independently of context length. Combined with FP4 main KV
caching (E2M1 format, one E4M3 scale per 16 channels), these designs reduce the global KV
cache footprint to 890 bytes per token"*
[src](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash).

The config in this repo pins the numbers: `index_topk: 512`, `index_n_heads: 32`,
`index_head_dim: 128`, `index_source_layer_ids: [2, 8, 14, 20, 24, 28, 32, 36]`,
`kv_source_layer_ids: [2, 8, 14, 20]`, `candidate_source_layer_id: 20`,
`candidate_topk_blocks: 2048`, `candidate_block_size: 8`, `sliding_window: 128`
(`research/models/deepseek41f/config.json`). vLLM's recipe describes the same shape:
*"the indexer scores those latents and keeps the best 512 per query, pre-filtered by a
candidate stage that selects 2048 blocks of 8"*, with a *"128-token sliding window"* in
every layer and *"Four layers (2, 8, 14, 20) actually compress their own KV"*
[src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash).

### 6.2 Kernel availability per GPU, per engine

**vLLM.** Five named backends exist for the V4/V4.1 sparse path
[src](https://github.com/vllm-project/vllm/blob/main/vllm/v1/attention/backends/registry.py):
`FLASHMLA_SPARSE_DSV4`, `FLASHINFER_MLA_SPARSE_DSV4`, `ROCM_FLASHMLA_SPARSE_DSV4`, and for
4.1 specifically `FLASHMLA_SPARSE_DSV41`, `FLASHINFER_MLA_SPARSE_DSV41`,
`FLASHMLA_MEGA_ATTN_DSV41`. The registry comment is explicit about why 4.1 got its own
names: *"Separate names from DSV4 so a V4.1 model never resolves the V4.0 backend classes
through this enum."*

Gating, read from source
[src](https://github.com/vllm-project/vllm/blob/main/vllm/models/deepseek_v41/sparse_mla.py),
[src](https://github.com/vllm-project/vllm/blob/main/vllm/models/deepseek_v41/nvidia/flashinfer_sparse.py),
[src](https://github.com/vllm-project/vllm/blob/main/vllm/models/deepseek_v41/nvidia/flash_mla_mega_attn.py):

| Backend | Compute cap. gate | Kernel block size | KV dtypes | Head size |
|---|---|---|---|---|
| `FLASHMLA_SPARSE_DSV41` | `capability.major in [9, 10]` | **64 on SM90**, 128 otherwise | `auto`, `fp8_ds_mla`, `fp8` | 512 (*"DeepSeek V4 layout: 448 NoPE + 64 RoPE = 512"*) |
| `FLASHINFER_MLA_SPARSE_DSV41` | `capability.major in [10, 12]` | 128 | SM10x: `auto`/`bfloat16`/`fp8`/`fp8_e4m3` (**rejects `fp8_ds_mla`** — *"SM10x uses the plain per-tensor FP8 KV layout"*); SM12x: **requires** one of `fp8`/`fp8_e4m3`/`fp8_ds_mla` and a FlashInfer build with the SM120 sparse-MLA decode API | 512 |
| `FLASHMLA_MEGA_ATTN_DSV41` | `capability.major == 10` — *"SM100 only; the kernel has no SM90 instantiation"*; runtime check returns *"FlashMLA mega attention requires sm_10x GPUs."* | 128 | + **`nvfp4_ds_mla`** (*"V4.1 fp8 SWA cache + NVFP4 compressed cache"*) | 512 |

Default selection: *"default on NVIDIA is `FLASHINFER_MLA_SPARSE_DSV4` on SM12x and
`FLASHMLA_SPARSE_DSV4` on other supported CUDA architectures"*
[src](https://docs.vllm.ai/en/latest/design/attention_backends/). For the generic (V3.2-era)
sparse MLA path: *"For sparse MLA, FP8 KV cache always prefers `FLASHINFER_MLA_SPARSE`.
With BF16 KV cache, `FLASHINFER_MLA_SPARSE` is preferred for low query-head counts (<= 16),
while `FLASHMLA_SPARSE` is preferred otherwise."*

**AMD.** `ROCM_FLASHMLA_SPARSE_DSV4` and `ROCM_AITER_MLA_SPARSE` exist; the latter's table
row shows fp16/bf16 with `auto`/fp8/fp8_e4m3 KV, block sizes `1, %16`, Sparse ✅, DCP ❌
[src](https://docs.vllm.ai/en/latest/design/attention_backends/). The V4.1 recipe warns
that on ROCm *"the ROCm sparse SWA backend only supports uniform-batch CUDA graphs"*
(requiring `VLLM_USE_BREAKABLE_CUDAGRAPH=1`) and that
`DeepseekV41IndexerBackend.supports_device_cpu_query_lens_mismatch()` is False, so
*"adaptive verification"* must be disabled with `enable_adaptive_verification:false`
[src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash).

**Intel XPU** also has `XPU_MLA_SPARSE` (head size 576, sparse ✅)
[src](https://docs.vllm.ai/en/latest/design/attention_backends/) — noted for completeness.

**SGLang.** The DSA backend (`--attention-backend dsa`, deprecated alias `nsa`) dispatches
to sub-backends [src](https://docs.sglang.io/advanced_features/attention_backend.html):

| Sub-backend | Prefill | Decode | Notes |
|---|---|---|---|
| `flashmla_sparse` | ✅ | ✅ | *"Default prefill on Hopper and Blackwell (BF16)"* |
| `flashmla_sparse_q8` | ✅ | ❌ | *"Native FP8 (q8×kv8) sparse prefill on Hopper (SM90); requires --kv-cache-dtype fp8_e4m3"* |
| `flashmla_kv` | ✅ | ✅ | *"Default for FP8 on Hopper (prefill + decode)"* |
| `flashmla_auto` | ✅ | ❌ | picks by KV dtype |
| `fa3` | ✅ | ✅ | *"Default decode on Hopper (BF16)"* |
| `trtllm` | ✅ | ✅ | *"Default decode on Blackwell (BF16); default for FP8 on Blackwell"* |
| `tilelang` | ✅ | ✅ | *"Default on AMD (ROCm)"* |
| `aiter` | ✅ | ✅ | AMD-specific, requires the `aiter` package |

### 6.3 The top-k / indexer kernel itself

The indexer's top-k selection is a separate kernel and a separate bottleneck.

- **FlashInfer** shipped `flashinfer.top_k_varlen` in 0.6.18: *"a Blackwell radix kernel, a
  guess-verify-refine kernel that warm-starts from the previous step's indices, and a
  CUTLASS fallback for any GPU. SM120/121 also picks up top-k 192 and 256."*
  [src](https://github.com/flashinfer-ai/flashinfer/releases/tag/v0.6.18)
- **SGLang** ships "Lightning TopK", which *"replaces global sort with radix-select
  reduction"* and *"reduces small-batch top-k latency to about 15 us"*, plus a "Flash
  Compressor" that *"can reach up to 80% of peak memory bandwidth and more than 10x over a
  naive PyTorch pipeline"* [src](https://www.lmsys.org/blog/2026-04-25-deepseek-v4/)
- **FlashInfer HCA** (v0.6.18) gives SM100/SM103 an FP8 *Heavily Compressed Attention*
  decode path: `trtllm_batch_decode_sparse_mla_dsv4(..., backend="cute-dsl")`
  [src](https://github.com/flashinfer-ai/flashinfer/releases/tag/v0.6.18)

⚠️ **TO BE VERIFIED**: no published per-GPU top-k kernel latency table exists. The 15 µs
SGLang figure carries no GPU or batch-size label in the source.

---

## 7. Triton attention

Triton is the universal fallback and the *only* backend in vLLM's table with
`Compute Cap. = Any` for full-feature standard attention
[src](https://docs.vllm.ai/en/latest/design/attention_backends/):

| Backend | Dtypes | KV dtypes | Block sizes | Head sizes | Sink | Non-causal | MM Prefix | Compute cap. |
|---|---|---|---|---|---|---|---|---|
| TRITON_ATTN | fp16, bf16, fp32 | auto, float16, bfloat16, fp8, fp8_e4m3, fp8_e5m2, **int4_per_token_head, int8_per_token_head, fp8_per_token_head** | %16 | Any | ✅ | ✅ | ✅ | **Any** |
| TRITON_MLA | fp16, bf16 | auto, fp16, bf16, fp8, fp8_e4m3 | %16 | Any | ❌ | ✅ | ❌ | **Any** |
| TRITON_FLASHINFER | — | fp16/bf16 + FP8 KV | 64-token kernel pages | 256/512 | — | — | ✅ | Blackwell composite |
| TRITON_FLASH_ATTN | — | — | — | — | — | — | ✅ | Hopper composite |

Two composites are worth knowing because they run automatically on multimodal models:
on Hopper `TRITON_FLASH_ATTN` uses *"Triton when the current queries need bidirectional
image attention and FlashAttention for causal text prefills and decode, with a shared KV
cache. The causal child must resolve to FA4."*; on Blackwell `TRITON_FLASHINFER` does the
same with FlashInfer, supporting *"head dimensions 256/512, FP16/BF16 and FP8 KV cache,
and 64-token kernel pages with a head-major cache layout"*, but
*"Context parallelism, R-SWA, attention sinks, and adaptive verification are not supported
by this composite"* [src](https://docs.vllm.ai/en/latest/design/attention_backends/).

Performance: the FA4 paper measures FA4 at **2.1–2.7× Triton** on B200 for dense attention
[src](https://arxiv.org/html/2603.05451v1), and 1.2–3.2× on custom masking variants
[src](https://lambda.ai/blog/flashattention-4-gives-the-nvidia-blackwell-platform-its-most-optimized-attention-kernel-yet).
Treat Triton attention as roughly **35–50% of the hand-written kernel** on Blackwell —
correct everywhere, fast nowhere.

---

## 8. AMD: CK FlashAttention, AITER, Triton FA

### 8.1 Upstream FlashAttention's ROCm support

*"ROCm version has two backends. There is composable_kernel (ck) which is the default
backend and a Triton backend. They provide an implementation of FlashAttention-2."* CK
backend supports *"MI200x, MI250x, MI300x, MI355x, and RDNA 3/4 GPUs"* and requires
*"ROCm 6.0 and above"*. The Triton implementation *"supports AMD's CDNA (MI200, MI300) and
RDNA GPUs using fp16, bf16, and fp32 datatypes … causal masking, variable sequence
lengths, arbitrary Q/KV sequence lengths and head sizes, MQA/GQA, dropout, rotary
embeddings, ALiBi, paged attention, and FP8 (via the Flash Attention v3 interface).
**Sliding window attention is currently a work in progress.**"* The Triton kernels *"are
provided by the aiter package, included as a git submodule at `third_party/aiter`"*
[src](https://github.com/Dao-AILab/flash-attention).

**The ceiling here is FA2.** There is no FA3 and no FA4 on any AMD GPU. Set
`FLASH_ATTENTION_TRITON_AMD_AUTOTUNE="TRUE"` for peak throughput at a warm-up cost
[src](https://github.com/Dao-AILab/flash-attention).

ROCm work is still landing in the FA repo: beta21 *"[AMD ROCm] Enable RDNA backward and
adopt CK unified workspace"*, beta25 *"[ROCm] Fix CK varlen_fwd binding argument mismatch"*
[src](https://github.com/Dao-AILab/flash-attention/releases).

### 8.2 AITER

AITER is AMD's kernel library, *"AI Tensor Engine for ROCm"*. Hardware support as
published [src](https://github.com/ROCm/aiter):

| GPU | Architecture | Status |
|---|---|---|
| MI300X | gfx942 (CDNA3) | "Fully supported" |
| MI325X | gfx942 (CDNA3) | "Fully supported" |
| MI350 | gfx950 (CDNA4) | "Supported" |
| **MI355X** | **gfx950 (CDNA4)** | **"Supported"** |
| Pro W7900 | gfx1100 (RDNA3) | "Experimental" |
| AI Max/Max Pro 400/300 | gfx1151 (RDNA3.5) | "Experimental" |
| Radeon AI PRO R9700 | gfx1201 (RDNA4) | "Experimental" |

Kernel families: *"MHA, MLA, Paged Attention, Fused MoE, GEMM, RMSNorm, RoPE+KVCache"*.
Backend split: *"On RDNA, Triton and most FlyDSL kernels run, as do most HIP kernels (norm,
RoPE, quant, activation, plus some GEMM/attention). **Most CK and ASM kernels are
CDNA-only.**"* Headline speedup claims: *"MLA decode kernel up to 17x"* and *"MHA prefill
kernel up to 14x"* — **marketing claims**, baseline unstated
[src](https://github.com/ROCm/aiter).

Note the CDNA4 status word is *"Supported"*, not *"Fully supported"* — AMD's own matrix
distinguishes MI355X from MI300X/MI325X. That gap is visible in the open PR queue.

### 8.3 gfx950 attention kernels in flight (as of 2026-09-19)

These are the concrete gfx950 attention numbers, and **all four PRs were open, not merged**,
when checked on 2026-09-19:

| PR | Kernel | State | Measured |
|---|---|---|---|
| [#5403](https://github.com/ROCm/aiter/pull/5403) | ASM FMHA forward, BF16, hd=256, varlen/group mode, gfx950 | **open** | *"Non-causal: 14% faster than CK — 497 TFLOPS vs 436 TFLOPS (b=8, s=5121, 18 heads). Causal: 17% faster than CK — 509 TFLOPS vs 436 TFLOPS (same config)"* — **measured on MI350X, not MI355X** (corrected 2026-09-19; the PR body names MI350X for the b=8, nh=18, s=5121, hd=256, bf16 run) [src](https://github.com/ROCm/aiter/pull/5403) |
| [#5376](https://github.com/ROCm/aiter/pull/5376) | ASM FMHA backward, BF16, hd=256, fused dKdV+dQ, gfx950 | **open** | *"676 TFLOPS on MI355X"* — confirmed MI355X, b=16, s=2560, and the PR also claims a 3.3× speedup over its baseline [src](https://github.com/ROCm/aiter/pull/5376) |
| [#5577](https://github.com/ROCm/aiter/pull/5577) | Pure-Triton FP8 FlashAttention v2, gfx942 + gfx950 | **open** | *"gfx942 uses float8e4b8/float8e5b16, gfx950 uses float8e4nv/float8e5"*; *"The USE_FP8=True path is not yet tested."* |
| [#5556](https://github.com/ROCm/aiter/pull/5556) | FlyDSL gfx950 FP8 paged-prefill attention, asymmetric head dim, varlen | **open** | — |

Derived % of peak, recomputed 2026-09-19 against the **2,500** TFLOPS MI355X dense BF16
denominator from §1.1 (METHODOLOGY §8; was 2,510):

| Kernel | TFLOPS | % of peak (`est.`) |
|---|---|---|
| AITER ASM fwd bf16 hd256 non-causal | 497 | 19.9% ⚠️ **on MI350X** — dividing an MI350X measurement by an MI355X peak understates it; MI350X's own peak was not obtained |
| AITER ASM fwd bf16 hd256 causal | 509 | 20.4% ⚠️ same MI350X caveat |
| CK fwd bf16 hd256 (baseline) | 436 | 17.4% ⚠️ same MI350X caveat |
| AITER ASM bwd bf16 hd256 | 676 | 27.0% (MI355X, so this one is a like-for-like ratio) |

Coverage gaps the PRs themselves declare, which is the honest picture of MI355X attention:
the hd256 ASM forward supports only group/varlen THD mode, BF16, MHA (*"GQA/MQA falls back
to CK"*), contiguous packed tensors, and **not** dense BSHD, dropout, ALiBi, bias, sink,
or `logits_soft_cap` [src](https://github.com/ROCm/aiter/pull/5403). The backward supports
only non-causal, a16 atomics, BF16, and MHA
[src](https://github.com/ROCm/aiter/pull/5376).

AMD also has an **open issue** admitting MLA decode on MI355X has never been benchmarked
properly: *"For MLA models (DeepSeek-R1, Kimi-K2.5, GLM-5), we were incorrectly
benchmarking decode attention using standard SDPA instead of the MLA decode kernel. This
gave misleading results."* — open since 2026-03-26
[src](https://github.com/ROCm/aiter/issues/2493). **There is no published MI355X-vs-B300
MLA decode comparison as of 2026-09-19.**

### 8.4 AMD backends visible in the engines

vLLM [src](https://docs.vllm.ai/en/latest/design/attention_backends/):

| Backend | KV dtypes | Block sizes | Head sizes | Sink | MM prefix |
|---|---|---|---|---|---|
| ROCM_AITER_FA | auto, fp16, bf16, fp8, fp8_e4m3, fp8_e5m2 | 16, 32 | 64, 128, 256 | ✅ | ❌ |
| ROCM_AITER_UNIFIED_ATTN | same | %16 | Any | ✅ | ✅ |
| ROCM_ATTN | auto, fp16, bf16, fp8, fp8_e4m3, fp8_e5m2 | %16 | 32…256 | ❌ | ✅ |
| ROCM_AITER_MLA | auto, fp16, bf16, fp8, fp8_e4m3, fp8_e5m2 | %1 | Any | ❌ | ❌ |
| ROCM_AITER_TRITON_MLA | auto | Any | Any | ❌ | ❌ |
| ROCM_AITER_MLA_SPARSE | auto, fp16, bf16, fp8, fp8_e4m3 | 1, %16 | Any | ❌ (Sparse ✅) | ❌ |

All ROCm rows show `Compute Cap. = N/A` — the CUDA capability column does not apply, so
**vLLM's table tells you nothing about gfx942 vs gfx950 differences**. That distinction
lives in AITER's own dispatch.

SGLang lists AITER with native paging, FP8 KV ✅, FP4 KV ❌, spec topk=1 and topk>1 ✅,
sliding window ✅, multimodal ✅; and `Wave` (ROCm) with everything ❌ except native paging
[src](https://docs.sglang.io/advanced_features/attention_backend.html). SGLang recommends
`triton` for the full-attention layers of hybrid GDN models on ROCm.

Engine flag: `VLLM_ROCM_USE_AITER=1` is required, not optional, in the published recipes
[src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash).

---

## 9. Per-GPU kernel support matrices

Legend: **Supported?** = the kernel runs at all. **Optimized?** = it is the architecture's
tuned path (not an emulation, not the SM80 MMA fallback, not a wrapper-level page
emulation). Measured numbers carry their own source.

### 9.1 A100 (sm_80)

| Kernel | Supported? | Optimized? | Measured fwd TFLOPS / % peak |
|---|---|---|---|
| FA2 | ✅ [src](https://github.com/Dao-AILab/flash-attention) | ✅ — A100 is FA2's design target | ⚠️ **TO BE VERIFIED** (no FA2-on-A100 number re-fetched this pass) |
| FA3 | ❌ — *"FA3 is only supported on devices with compute capability 9.x"* [src](https://github.com/vllm-project/vllm/blob/main/vllm/vllm_flash_attn/flash_attn_interface.py) | — | — |
| FA4 (upstream) | ✅ forward only — `arch // 10 in [8, …]`; backward asserts `[9,10,11,12]` [src](https://github.com/Dao-AILab/flash-attention/blob/main/flash_attn/cute/interface.py) | ❌ — SM80 MMA path, 128 threads | ⚠️ untested upstream (*"currently Sm90 - Sm100"*) [src](https://github.com/Dao-AILab/flash-attention/issues/2376) |
| FA4 (vLLM wheel) | ❌ — gate is 9.x/10.x/11.x | — | — |
| FlashInfer native | ✅ — compute cap 8.x–9.x row [src](https://docs.vllm.ai/en/latest/design/attention_backends/) | ✅ | — |
| FlashInfer trtllm-gen / XQA | ❌ (10.x / 9.0 only) | — | — |
| FlashMLA (any) | ❌ — SM90/SM100 only [src](https://github.com/deepseek-ai/FlashMLA) | — | — |
| DSA / DSV4 / DSV41 sparse | ❌ — all gates start at major 9 | — | — |
| TRITON_ATTN / TRITON_MLA | ✅ Any | ❌ | — |
| FP8 attention (Q/K/V) | ❌ | — | — |
| FP8 KV cache attention | ✅ via TRITON_ATTN / FLASHINFER native; ❌ via FlashAttention (FA3/FA4 only) | ❌ | — |

SGLang default on A100: `flashinfer`
[src](https://docs.sglang.io/advanced_features/attention_backend.html).

### 9.2 H100 / H200 / H800 (sm_90)

| Kernel | Supported? | Optimized? | Measured |
|---|---|---|---|
| FA2 | ✅ | ❌ (superseded) | — |
| **FA3** | ✅ — 9.x only | ✅ **the Hopper path** | **~740 TFLOPS fwd BF16, "75% utilization"** [src](https://lambda.ai/blog/flashattention-4-gives-the-nvidia-blackwell-platform-its-most-optimized-attention-kernel-yet); 74.8% of 989.5 `est.` |
| FA4 | ✅ — `FlashAttentionForwardSm90`, in both upstream and vLLM's gate | ⚠️ partially — tested on SM90, but hd>192 backward xfails, SplitKV unsupported [src](https://github.com/Dao-AILab/flash-attention/blob/main/tests/cute/test_flash_attn.py) | ⚠️ **TO BE VERIFIED** — no published FA4-on-H100 TFLOPS |
| FA3 FP8 forward | ✅ *"FP8 forward"* [src](https://github.com/Dao-AILab/flash-attention) | ✅ | — |
| FA4 FP8 | ❌ — *"FP8 is only supported on SM100"* [src](https://github.com/Dao-AILab/flash-attention/blob/main/flash_attn/cute/interface.py) | — | — |
| FlashInfer native | ✅ (8.x–9.x) | ✅ | — |
| FlashInfer XQA | ✅ — **9.0 only** | ✅ decode path | — |
| FlashInfer trtllm-gen | ❌ (10.x) | — | — |
| FlashInfer FP8 KV (SM90 native) | ✅ since v0.6.14 [src](https://flashinfer.ai/releases/) | ✅ | — |
| FlashInfer GDN prefill | ✅ — `is_device_capability(90)`, no further constraints [src](https://github.com/vllm-project/vllm/blob/main/vllm/model_executor/layers/mamba/gdn/qwen_gdn_linear_attn.py) | ✅ (JIT warm-up cost) | — |
| FlashInfer fused KDA decode | ❌ — `(10,0)` or `(10,3)` only | — | — |
| Native fused KDA decode (vLLM) | ✅ — `is_device_capability(90)` [src](https://github.com/vllm-project/vllm/blob/main/vllm/models/kimi_k3/nvidia/kda.py) | ✅ | — |
| **FlashMLA dense decode** | ✅ SM90, MQA | ✅ | **660 TFLOPS** compute-bound / **3,000 GB/s** memory-bound, H800 SXM5 CUDA 12.8 [src](https://github.com/deepseek-ai/FlashMLA); 66.7% of peak `est.` |
| **FlashMLA sparse prefill** | ✅ SM90 | ✅ | **640 TFLOPS** [src](https://github.com/deepseek-ai/FlashMLA); 64.7% `est.` |
| **FlashMLA sparse decode (FP8 KV)** | ✅ SM90 — but **V3.2/V4 only; V4.1 sparse decode is SM100-only** [src](https://github.com/deepseek-ai/FlashMLA) | ✅ | **410 TFLOPS** [src](https://github.com/deepseek-ai/FlashMLA); 41.4% `est.` |
| FlashMLA dense prefill (MHA) | ❌ — SM100 only | — | — |
| FlashMLA mega-attn (V4.1) | ❌ — *"the kernel has no SM90 instantiation"* [src](https://github.com/vllm-project/vllm/blob/main/vllm/models/deepseek_v41/sparse_mla.py) | — | — |
| `FLASHMLA_SPARSE_DSV41` (vLLM) | ✅ — major 9 allowed, **block size 64** | ✅ | — |
| `FLASHINFER_MLA_SPARSE_DSV41` | ❌ — majors [10, 12] | — | — |
| `FLASHINFER_MLA_SPARSE_SM90` | ✅ — 9.x, head sizes 512/576, block %64 | ✅ | see §9.9 for the H100 perf caveat |
| TRTLLM XQA / `trtllm_mha` | ✅ SGLang: *"Optimized for SM90 and SM120"*, page size 64 | ✅ | — |
| HPC_ATTN (Tencent hpc-ops) | ✅ — *"Only supported on NVIDIA Hopper GPUs (e.g. H20, H200) … requires a block size of 64"* [src](https://github.com/vllm-project/vllm/blob/main/vllm/v1/attention/backends/registry.py) | ✅ niche (head_dim 128, q/kv group 4 or 8) | — |
| FP8 KV cache attention | ✅ — `fa_version == 3 and capability family 90` [src](https://github.com/vllm-project/vllm/blob/main/vllm/v1/attention/backends/fa_utils.py) | ✅ | — |
| NVFP4 KV cache attention | ❌ on the FlashAttention path; `nvfp4_ds_mla` is SM100-only | — | — |

H200 differs from H100 only in HBM (141 GB @ 4.8 TB/s, 700 W SXM
[src](https://www.nvidia.com/en-us/data-center/h200/)); **every kernel gate above is
identical**. H800 is the FlashMLA reference part and is likewise sm_90.

### 9.3 B200 (sm_100)

| Kernel | Supported? | Optimized? | Measured |
|---|---|---|---|
| FA2 | ✅ | ❌ | — |
| FA3 | ❌ — *"Cannot use FA version 3 on Blackwell platform"* [src](https://github.com/vllm-project/vllm/blob/main/vllm/v1/attention/backends/fa_utils.py) | — | — |
| **FA4** | ✅ **default in vLLM on major 10** | ✅ **the Blackwell path** (tcgen05, TMA, TMEM, CuTe DSL) | **1,613 TFLOPS fwd BF16 hd128, "71% utilization"** [src](https://arxiv.org/html/2603.05451v1); 71.7% of 2,250 `est.` |
| FA4 FP8 | ✅ — the only arch where the assert passes | ⚠️ young (accuracy fixes in beta23/24) | — |
| FA4 hd256 2-CTA kernel | ✅ `arch // 10 in [10, 11]`, **requires block size 128** | ✅ | no softcap/sink/block-sparsity/local attention/deterministic [src](https://github.com/Dao-AILab/flash-attention/blob/main/flash_attn/cute/interface.py) |
| FA4 MLA-absorbed fwd (`FlashAttentionMLAForwardSm100`) | ✅ | ✅ | — |
| FA4 sparse MLA backward (DeepSeek V4) | ✅ since beta19 [src](https://github.com/Dao-AILab/flash-attention/releases) | ✅ | training-side; irrelevant to inference |
| FlashInfer trtllm-gen FMHA | ✅ — 10.x, **sinks ✅, `nvfp4` KV ✅** [src](https://docs.vllm.ai/en/latest/design/attention_backends/) | ✅ | — |
| FlashInfer native / XQA | ✅ / ❌ (XQA is 9.0) | — | — |
| FLASHINFER_MLA, FLASHINFER_MLA_SPARSE | ✅ 10.x | ✅ | — |
| CUTLASS_MLA | ✅ 10.x, block size 128 | ✅ | — |
| TOKENSPEED_MLA | ✅ 10.x, **FP8 KV required** | ✅ | — |
| **FlashMLA sparse prefill** | ✅ SM100 | ✅ | **up to 1,450 TFLOPS**, CUDA 12.9 [src](https://github.com/deepseek-ai/FlashMLA); 64.4% `est.` |
| **FlashMLA sparse decode** | ✅ SM100 (incl. V4.1) | ⚠️ relatively weaker than Hopper's | **up to 700 TFLOPS** [src](https://github.com/deepseek-ai/FlashMLA); 31.1% `est.` |
| **FlashMLA dense MHA prefill** | ✅ SM100 | ✅ | **1,460 TFLOPS fwd / 1,000 TFLOPS bwd**, "as reported by NVIDIA" [src](https://github.com/deepseek-ai/FlashMLA); 64.9% / 44.4% `est.` |
| **FlashMLA fused norm-RoPE-attn-RoPE-cast** | ✅ SM100, V4/V4.1 | ✅ | **1,430 TFLOPS prefill / 670 TFLOPS decode** [src](https://github.com/deepseek-ai/FlashMLA) |
| `FLASHMLA_MEGA_ATTN_DSV41` | ✅ major == 10, **`nvfp4_ds_mla` KV** [src](https://github.com/vllm-project/vllm/blob/main/vllm/models/deepseek_v41/sparse_mla.py) | ✅ | — |
| `FLASHINFER_MLA_SPARSE_DSV41` | ✅ major 10; rejects `fp8_ds_mla` (wants plain per-tensor FP8) | ✅ | — |
| FlashInfer HCA FP8 decode (DSV4) | ✅ SM100 since 0.6.18 [src](https://github.com/flashinfer-ai/flashinfer/releases/tag/v0.6.18) | ✅ | — |
| FlashInfer fused KDA decode | ✅ — `(10, 0)` [src](https://github.com/vllm-project/vllm/blob/main/vllm/models/kimi_k3/nvidia/kda.py) | ✅ | **1.33× the vLLM fused kernel at one row, table geomean 1.13×** [src](https://github.com/flashinfer-ai/flashinfer/releases/tag/v0.6.18) |
| FlashInfer / CuteDSL GDN prefill | ✅ — family 100 + `head_k_dim == 128` + CUDA ≥ 13 | ✅ | *"~20–25% GDN prefill speedup"* from the CuTe-DSL rewrite [src](https://flashinfer.ai/releases/) |
| FP8 KV cache attention | ✅ — `fa_version == 4 and family 100` | ✅ | — |
| NVFP4 KV cache attention | ✅ — `nvfp4` (trtllm-gen), `nvfp4_ds_mla` (mega-attn), `nvfp4_4over6` (FlashInfer native) | ✅ | — |

### 9.4 B300 / GB300 (sm_103)

Everything in §9.3 applies — `sm_103` satisfies every `major == 10` /
`is_device_capability_family(100)` / `arch // 10 == 10` gate. Additions and known
differences:

| Item | Status |
|---|---|
| FA4 `sm103a` `tcgen.ld.red` path | ✅ merged 2026-07-11, shipped in `fa4-v4.0.0.beta22` [src](https://github.com/Dao-AILab/flash-attention/pull/2696) — a B300-only instruction path |
| FlashInfer DeepSeek MLA decode on SM103 | ✅ since v0.6.15, *"with cluster-aware CUTLASS `split_kv`"* [src](https://github.com/flashinfer-ai/flashinfer/releases/tag/v0.6.15) |
| FlashInfer HCA FP8 sparse MLA decode | ✅ SM100 **and SM103** since v0.6.18 [src](https://github.com/flashinfer-ai/flashinfer/releases/tag/v0.6.18) |
| FlashInfer NVFP4 paged-KV sparse decode/prefill | ✅ PR #4982 targets SM100/SM103 and **merged 2026-09-18** (corrected — was "merge status unconfirmed"); prefill needs **CUDA 13.0+** [src](https://github.com/flashinfer-ai/flashinfer/pull/4982) |
| FlashInfer W4A16 weight-only NVFP4 | ✅ B200 **and B300** since v0.6.18 [src](https://github.com/flashinfer-ai/flashinfer/releases/tag/v0.6.18) — MoE, not attention, but it is what makes the FP4 checkpoints load |
| SGLang FlashInfer GDN prefill | ✅ *"On SM100/SM103 with CUDA 13+"* [src](https://docs.sglang.io/advanced_features/attention_backend.html) |
| FlashInfer fused KDA decode | ✅ — the gate is `compute_capability in ((10, 0), (10, 3))`, i.e. **B200 and B300 explicitly, nothing else** [src](https://github.com/vllm-project/vllm/blob/main/vllm/models/kimi_k3/nvidia/kda.py) |
| Published FA4 / FlashMLA TFLOPS on B300 | **none found** — ⚠️ **TO BE VERIFIED**; scale B200 by 2500/2250 = 1.11× for planning (`est.`) **only for a GB300 NVL72 B300**. An air-cooled **HGX B300 has the same 2,250 dense BF16 peak as B200** [src](https://www.nvidia.com/en-us/data-center/hgx/), so scale by 1.0× there. Corrected 2026-09-19. |
| Dense BF16 peak: HGX B300 vs GB300 NVL72 | **2,250 vs 2,500 TFLOPS** — not the same part in practice. Blackwell Ultra's uplift over B200 is FP4-only (13.5 → 15 PFLOPS dense FP4 per GPU); BF16 is unchanged at the HGX board power [src](https://www.nvidia.com/en-us/data-center/hgx/) |

GB300 NVL72 adds nothing at the kernel level over a standalone B300; what it changes is the
NVLink domain (130 TB/s, 20 TB GPU memory across 72 GPUs
[src](https://www.nvidia.com/en-us/data-center/gb300-nvl72/)) and therefore the viable
parallelism, not the attention kernel. The vLLM recipes do call out
`NCCL_MNNVL_ENABLE=1`, `NCCL_CUMEM_ENABLE=1`, `NCCL_NVLS_ENABLE=1` for GB300/GB200
[src](https://recipes.vllm.ai/moonshotai/Kimi-K3).

### 9.5 RTX PRO 6000 Blackwell (sm_120)

The most constrained NVIDIA part in this roster. `major == 12` fails almost every
datacenter-Blackwell gate.

| Kernel | Supported? | Optimized? | Notes |
|---|---|---|---|
| FA2 | ✅ (cc ≥ 8.0) | ❌ | **this is what you actually get from `FLASH_ATTN` in vLLM** |
| FA3 | ❌ | — | 9.x only |
| **FA4 in vLLM** | ❌ | — | *"FA4 is only supported on devices with compute capability 9.x, 10.x, or 11.x"* [src](https://github.com/vllm-project/vllm/blob/main/vllm/vllm_flash_attn/flash_attn_interface.py) |
| FA4 upstream (`FlashAttentionForwardSm120`) | ✅ | ❌ — *"uses SM80 MMA with SM120 SMEM capacity"* | no block sparsity, **no paged KV**, no SplitKV, no FP8 [src](https://github.com/Dao-AILab/flash-attention/blob/main/flash_attn/cute/interface.py) |
| FA4 hd256 2-CTA | ❌ — `arch // 10 in [10, 11]` | — | — |
| FlashInfer native | ⚠️ — vLLM's table lists FLASHINFER Native at **8.x–9.x** and trtllm-gen at **10.x**, leaving 12.x uncovered for standard attention [src](https://docs.vllm.ai/en/latest/design/attention_backends/) | — | ⚠️ **TO BE VERIFIED**: FlashInfer's own README lists SM 12.0/12.1 as supported architectures [src](https://github.com/flashinfer-ai/flashinfer) — the gap is vLLM's wiring, not FlashInfer's |
| `FLASHINFER_MLA_SPARSE_SM120` | ✅ — 12.x, bf16, KV `auto`/`fp8`/`fp8_e4m3`/`fp8_ds_mla`, **block sizes 64 or 256** | ✅ dedicated SM120 kernel | [src](https://docs.vllm.ai/en/latest/design/attention_backends/) |
| `FLASHINFER_MLA_SPARSE_DSV4` / `DSV41` | ✅ — `capability.major in [10, 12]`, and **on SM12x an FP8 KV dtype is mandatory** (`fp8`/`fp8_e4m3`/`fp8_ds_mla`) plus a FlashInfer build carrying the SM120 sparse-MLA decode API [src](https://github.com/vllm-project/vllm/blob/main/vllm/models/deepseek_v41/nvidia/flashinfer_sparse.py) | ✅ | **this is the vLLM default on SM12x** [src](https://docs.vllm.ai/en/latest/design/attention_backends/) |
| `FLASHMLA_SPARSE_DSV41` | ❌ — `major in [9, 10]` | — | — |
| `FLASHMLA_MEGA_ATTN_DSV41` | ❌ — `major == 10` | — | no NVFP4 compressed cache on this GPU |
| FlashMLA (any) | ❌ — SM90/SM100 only | — | — |
| CUTLASS_MLA / FLASHINFER_MLA / TOKENSPEED_MLA | ❌ — all 10.x | — | — |
| B12X backend | ✅ — *"supports causal decoder attention on NVIDIA SM120 and SM121 GPUs"*, bf16, KV `auto` only, head sizes 64/128/192/256, sinks ✅ [src](https://docs.vllm.ai/en/latest/design/attention_backends/) | ✅ niche | `uv pip install "vllm[b12x]"` |
| TRITON_ATTN | ✅ Any | ❌ | the practical general-purpose fallback |
| TRTLLM XQA (`trtllm_mha` decode) | ✅ per SGLang: *"Optimized for SM90 and SM120"*, page size 64 [src](https://docs.sglang.io/advanced_features/attention_backend.html) | ✅ decode only | |
| FlashInfer GDN prefill | ✅ — family 120 + `head_k_dim == 128` + CUDA ≥ 13 [src](https://github.com/vllm-project/vllm/blob/main/vllm/model_executor/layers/mamba/gdn/qwen_gdn_linear_attn.py) | ✅ | CuteDSL GDN is **not** offered here: *"The in-tree CuteDSL kernel targets SM100 only, so it stays off here."* |
| Native fused KDA decode | ✅ — `is_device_capability_family(120)` [src](https://github.com/vllm-project/vllm/blob/main/vllm/models/kimi_k3/nvidia/kda.py) | ✅ | but FlashInfer fused KDA decode ❌ (10.0/10.3 only) |
| FP8 KV cache attention via FlashAttention | ❌ — requires FA3@9.x or FA4@10.x | — | use FlashInfer/Triton/TRTLLM paths instead |
| NVFP4 KV cache attention | ✅ **at the kernel level** — PR #4955 *"feat(sm120): add NVFP4 sparse MLA support for DeepSeek V4 Flash"* **merged 2026-09-07** for SM120/SM121 (corrected 2026-09-19 — was "merge status unconfirmed") [src](https://github.com/flashinfer-ai/flashinfer/pull/4955) | ✅ dedicated NVFP4 streaming prefill + grouped split-K decode; measured **on an RTX PRO 5000 Blackwell** at 1.41–1.67× prefill / 1.31× decode / ~9% end-to-end vs the FP8 default | supported config is 16/32/64/128 query heads, top-k 128 or 512, page size 64. ⚠️ **But vLLM's `FLASHINFER_MLA_SPARSE_DSV41` SM12x branch still *requires* one of `fp8`/`fp8_e4m3`/`fp8_ds_mla`** [src](https://github.com/vllm-project/vllm/blob/main/vllm/models/deepseek_v41/nvidia/flashinfer_sparse.py) — the kernel exists but vLLM does not yet hand it an NVFP4 dtype. ⚠️ **TO BE VERIFIED**: whether SGLang or a vLLM nightly newer than this pass exposes it |

SGLang's hybrid-GDN constraint for this GPU, verbatim: *"Blackwell SM120 (e.g., RTX PRO
6000 Blackwell): `triton` or `flashinfer` for prefill/full attention; `trtllm_mha` is
supported for `--decode-attention-backend` only."*
[src](https://docs.sglang.io/advanced_features/attention_backend.html)

vLLM's DeepSeek-V4-Flash recipe for 8× RTX PRO 6000 states the caveats plainly: *"Cannot
use SM100 FP4 indexer cache or `deep_gemm_mega_moe`"*, `--attention_config.use_fp4_indexer_cache False`,
and *"DSpark variant requires vLLM nightly + FlashInfer build with SM120 sparse-MLA decode
kernel. Verified without speculative decoding only."*
[src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash)

### 9.6 MI355X (gfx950, CDNA4)

| Kernel | Supported? | Optimized? | Measured |
|---|---|---|---|
| FA2 CK backend | ✅ — MI355x named [src](https://github.com/Dao-AILab/flash-attention) | ✅ (default ROCm backend) | **436 TFLOPS** fwd bf16 hd256 b=8 s=5121 18 heads (as the baseline in PR #5403) [src](https://github.com/ROCm/aiter/pull/5403) — ⚠️ **that run is on MI350X, not MI355X** (corrected 2026-09-19); 17.4% of the 2,500 MI355X denominator `est.`, so the percentage is a cross-part mix |
| FA2 Triton backend (from `third_party/aiter`) | ✅ — CDNA + RDNA, fp16/bf16/fp32, paged attention, FP8 via the FA3 interface; *"Sliding window attention is currently a work in progress"* [src](https://github.com/Dao-AILab/flash-attention) | ❌ | — |
| FA3 / FA4 | ❌ — CUDA-only | — | — |
| **AITER ASM FMHA fwd bf16 hd256** | ⚠️ **open PR, not merged 2026-09-19** [src](https://github.com/ROCm/aiter/pull/5403) | ✅ when merged | **497 TFLOPS non-causal / 509 causal** (14% / 17% over CK) — ⚠️ **measured on MI350X**, not MI355X (corrected 2026-09-19) |
| **AITER ASM FMHA bwd bf16 hd256** | ⚠️ **open PR** [src](https://github.com/ROCm/aiter/pull/5376) | ✅ when merged | **676 TFLOPS** on MI355X (b=16, s=2560), non-causal only |
| AITER Triton FP8 FA2 (gfx942 + gfx950) | ⚠️ **open PR**; *"The USE_FP8=True path is not yet tested."* [src](https://github.com/ROCm/aiter/pull/5577) | ❌ | — |
| AITER FlyDSL FP8 paged-prefill | ⚠️ **open PR** [src](https://github.com/ROCm/aiter/pull/5556) | — | — |
| AITER MLA decode (`mla_decode_fwd`, ASM) | ✅ exists [src](https://github.com/ROCm/aiter/issues/2493) | ⚠️ | **no published number** — AMD's own issue says earlier MLA benchmarks were wrong |
| `ROCM_AITER_MLA_SPARSE` (vLLM) | ✅ — Sparse ✅, block sizes `1, %16`, KV auto/fp8/fp8_e4m3 [src](https://docs.vllm.ai/en/latest/design/attention_backends/) | ⚠️ | — |
| `ROCM_FLASHMLA_SPARSE_DSV4` | ✅ registered [src](https://github.com/vllm-project/vllm/blob/main/vllm/v1/attention/backends/registry.py) | ⚠️ | — |
| SGLang `tilelang` DSA | ✅ — *"Default on AMD (ROCm)"* [src](https://docs.sglang.io/advanced_features/attention_backend.html) | ✅ | — |
| SGLang `aiter` DSA | ✅ | ✅ | — |
| GDN linear attention | ✅ via AITER Triton kernels (`rocm_aiter_ops.are_gdn_triton_kernels_available()`) [src](https://github.com/vllm-project/vllm/blob/main/vllm/model_executor/layers/mamba/gdn/qwen_gdn_linear_attn.py); SGLang says *"AMD (ROCm): `triton` recommended"* | ❌ | — |
| Native fused KDA decode | ❌ — gated on CUDA capability 90/100/120 | — | Triton KDA path only |
| FP8 KV cache attention | ✅ — `--kv-cache-dtype fp8_e4m3`, plus `VLLM_ROCM_USE_AITER=1` [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash) | ✅ | — |
| NVFP4 KV cache attention | ❌ — NVFP4 is an NVIDIA format; SGLang's AITER row shows FP4 KV ❌ [src](https://docs.sglang.io/advanced_features/attention_backend.html) | — | — |
| `torch.compile` | ❌ for DeepSeek-V4.1 — *"Torch.compile unsupported on AMD"* [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash) | — | — |

MI300X/MI325X (gfx942) are *"Fully supported"* in AITER vs MI355X's *"Supported"*
[src](https://github.com/ROCm/aiter), and the vLLM MI325X recipe is visibly more
constrained (*"Only TP1 validated at 4K context"*, `--enforce-eager`,
`--moe-backend triton_unfused`) [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash).

### 9.7 Consolidated kernel × GPU grid

✅ supported and optimized · 🟡 supported but not the tuned path · ❌ unsupported ·
⚠️ conditional/unverified (see the per-GPU section)

| Kernel | A100 | H100/H200/H800 | B200 | B300/GB300 | RTX PRO 6000 | MI355X |
|---|---|---|---|---|---|---|
| FA2 | ✅ | 🟡 | 🟡 | 🟡 | 🟡 | ✅ (CK) |
| FA3 | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| FA4 (vLLM wheel) | ❌ | ✅ | ✅ | ✅ | ❌ | ❌ |
| FA4 (upstream, fwd) | 🟡 | ✅ | ✅ | ✅ | 🟡 | ❌ |
| FA4 FP8 attention | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ |
| FA4 hd256 2-CTA | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ |
| FlashInfer native | ✅ | ✅ | ✅ | ✅ | ⚠️ | ❌ |
| FlashInfer XQA | ❌ | ✅ | ❌ | ❌ | ⚠️ (via TRTLLM MHA) | ❌ |
| FlashInfer trtllm-gen FMHA | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ |
| FlashInfer MLA / MLA_SPARSE | ❌ | ⚠️ (SM90 variant) | ✅ | ✅ | ⚠️ (SM120 variant) | ❌ |
| FlashMLA dense decode | ❌ | ✅ | ❌ | ❌ | ❌ | ❌ |
| FlashMLA dense prefill (MHA) | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ |
| FlashMLA sparse prefill | ❌ | ✅ | ✅ | ✅ | ❌ | ❌ |
| FlashMLA sparse decode | ❌ | ✅ (V3.2/V4) | ✅ | ✅ | ❌ | ❌ |
| FlashMLA mega-attn (V4.1) | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ |
| DSV41 sparse (vLLM, any backend) | ❌ | ✅ (FlashMLA) | ✅ | ✅ | ✅ (FlashInfer, FP8 KV required) | ⚠️ (ROCm) |
| TRTLLM MLA | ❌ | ⚠️ | ✅ | ✅ | ❌ | ❌ |
| Triton attention | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| AITER CK / ASM FMHA | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ / ⚠️ |
| FP8 KV attention | 🟡 (Triton/FI) | ✅ | ✅ | ✅ | 🟡 | ✅ |
| NVFP4 KV attention | ❌ | ❌ | ✅ | ✅ | ⚠️ | ❌ |
| FlashInfer fused KDA decode | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ |
| Native fused KDA decode (vLLM) | ❌ | ✅ | ✅ | ✅ | ✅ | ❌ |
| FlashInfer GDN prefill | ❌ | ✅ | ✅ | ✅ | ✅ | ❌ |
| CuteDSL GDN prefill | ❌ | ❌ | ✅ | ✅ | ❌ | ❌ |

---

## 10. FP8 attention (Q/K/V in FP8)

Distinct from FP8 *KV cache*: here the Q·Kᵀ and P·V matmuls themselves run in FP8.

| Path | Where it runs | Notes |
|---|---|---|
| FA3 FP8 forward | **sm_90 only** | *"FP16 / BF16 forward and backward, FP8 forward"* [src](https://github.com/Dao-AILab/flash-attention) |
| FA4 FP8 | **sm_10x only** | `assert arch // 10 == 10, "FP8 is only supported on SM100 (compute capability 10.x) for FA4 CuTe."` [src](https://github.com/Dao-AILab/flash-attention/blob/main/flash_attn/cute/interface.py). Descale tensors `q_descale`/`k_descale`/`v_descale` are required and rejected for non-FP8 inputs. Accuracy fixes as late as beta18/23/24 [src](https://github.com/Dao-AILab/flash-attention/releases) |
| FlashMLA sparse decode | sm_90, sm_100 | *"uses an FP8 KV cache while performing the matrix multiplication in bfloat16"* [src](https://github.com/deepseek-ai/FlashMLA) — **not** an FP8 matmul |
| SGLang `flashmla_sparse_q8` | sm_90 | *"Native FP8 (q8×kv8) sparse prefill on Hopper (SM90); requires --kv-cache-dtype fp8_e4m3"* [src](https://docs.sglang.io/advanced_features/attention_backend.html) — a genuine FP8×FP8 attention |
| TRT-LLM FP8 context FMHA | *"only supported on Ada and Hopper"* (legacy flow) [src](https://nvidia.github.io/TensorRT-LLM/advanced/gpt-attention.html) | see the §4.2 caveat about trtllm-gen |
| FlashInfer HCA | SM100/SM103 | *"FP8 Heavily Compressed Attention (HCA)"* [src](https://github.com/flashinfer-ai/flashinfer/releases/tag/v0.6.18) |
| AITER Triton FP8 FA2 | gfx942 + gfx950 | ⚠️ open PR, FP8 path untested [src](https://github.com/ROCm/aiter/pull/5577) |
| vLLM `flash_attn_supports_quant_query_input()` | everything except XPU [src](https://github.com/vllm-project/vllm/blob/main/vllm/v1/attention/backends/fa_utils.py) | quantized *query* input plumbing |

⚠️ **TO BE VERIFIED**: no published speedup for FP8 attention vs BF16 attention on any of
these GPUs was found. The naive expectation is ~2× on the attention matmuls alone, which
translates to far less end-to-end because softmax, RoPE and the memory movement do not
shrink. Do not plan on 2×.

---

## 11. FP8 / NVFP4 KV-cache attention

| KV dtype name | Meaning | Backends / arch |
|---|---|---|
| `fp8`, `fp8_e4m3`, `fp8_e5m2` | plain per-tensor FP8 KV | almost everything; FlashAttention path requires FA3@9.x or FA4@10.x [src](https://github.com/vllm-project/vllm/blob/main/vllm/v1/attention/backends/fa_utils.py) |
| `fp8_ds_mla` | DeepSeek MLA-specific FP8 compressed record | `FLASHMLA_SPARSE`, `FLASHMLA_SPARSE_DSV41` (9.x, 10.x), `FLASHINFER_MLA_SPARSE_SM120` (12.x), `FLASHINFER_MLA_SPARSE_DSV41` on **12.x only** — SM10x *rejects* it [src](https://github.com/vllm-project/vllm/blob/main/vllm/models/deepseek_v41/nvidia/flashinfer_sparse.py) |
| `nvfp4_ds_mla` | *"V4.1 fp8 SWA cache + NVFP4 compressed cache"* | **`FLASHMLA_MEGA_ATTN_DSV41` only, `major == 10` only** [src](https://github.com/vllm-project/vllm/blob/main/vllm/models/deepseek_v41/sparse_mla.py); also listed for `FLASHMLA_SPARSE` [src](https://docs.vllm.ai/en/latest/design/attention_backends/) |
| `nvfp4` | NVFP4 paged KV | FLASHINFER **trtllm-gen**, compute cap 10.x [src](https://docs.vllm.ai/en/latest/design/attention_backends/) |
| `nvfp4_4over6` | NVFP4 variant | FLASHINFER Native (8.x–9.x), XQA (9.0), trtllm-gen (10.x) [src](https://docs.vllm.ai/en/latest/design/attention_backends/) |
| `int4_per_token_head`, `int8_per_token_head`, `fp8_per_token_head` | per-token-head quant | **TRITON_ATTN only**, any compute cap [src](https://docs.vllm.ai/en/latest/design/attention_backends/) |
| `turboquant_k8v4`, `turboquant_4bit_nc`, `turboquant_k3v4_nc`, `turboquant_3bit_nc` | TURBOQUANT backend | any compute cap, decoder-only [src](https://docs.vllm.ai/en/latest/design/attention_backends/) |

SGLang's FP4-KV column [src](https://docs.sglang.io/advanced_features/attention_backend.html):
FA4 ✅, Triton ✅, Torch SDPA ✅, FlexAttention ✅, TRTLLM MHA ✅; FlashInfer ❌, FA3 ❌,
AITER ❌. On the MLA side: FlashInfer MLA ✅, FlashMLA ✅, Cutlass MLA ✅, TRTLLM MLA ✅,
FA4 ✅; CuteDSL MLA ❌, TokenSpeed MLA ❌, FA3 ❌, Triton ❌.

**The headline consequence for this repo:** DeepSeek-V4.1-Flash's compressed latents are
*"trained to be stored in FP4"*, giving *"890 bytes per token, about a quarter of
V4-Flash"* and *"under 1 GB of global KV"* for a full 1M-token prompt
[src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash),
[src](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash). Realising that FP4 cache in
vLLM requires `nvfp4_ds_mla`, which requires `FLASHMLA_MEGA_ATTN_DSV41`, which requires
`major == 10`. **B200/B300/GB300 get the 890 B/token cache; H100/H200, RTX PRO 6000 and
MI355X do not.** That is the single most consequential kernel-availability fact in this
document.

⚠️ **Amended 2026-09-19 — the RTX PRO 6000 half of that sentence is now only true of
vLLM, not of the hardware.** FlashInfer PR #4955 merged on **2026-09-07** and ships an
NVFP4 sparse-MLA KV path for **SM120/SM121**
[src](https://github.com/flashinfer-ai/flashinfer/pull/4955). The remaining blocker is
purely vLLM's dtype gate: `FLASHINFER_MLA_SPARSE_DSV41` on SM12x still *requires* one of
`fp8`/`fp8_e4m3`/`fp8_ds_mla`
[src](https://github.com/vllm-project/vllm/blob/main/vllm/models/deepseek_v41/nvidia/flashinfer_sparse.py).
So the correct statement is: *sm_120 has an NVFP4 compressed-KV kernel as of 2026-09-07,
but no serving engine verified in this pass routes to it.* Re-check vLLM's SM12x dtype
list before repeating the flat "RTX PRO 6000 cannot do NVFP4 KV" claim — the answer is
engine-version-dependent from now on, not architectural. H100/H200 and MI355X are
unchanged: still no NVFP4 KV path at all.

**Resolved 2026-09-19 (sweep) — the 890 B/token figure IS reproducible, byte for byte.**
An earlier pass of this document reported a 720-vs-890 gap; that was an incomplete
derivation which counted only the **main** KV and dropped the indexer K cache.
`research/models/deepseek41f/architecture.md` §5.1 derives it in full under METHODOLOGY §2,
from `research/models/deepseek41f/config.json` and the reference `model.py`. Cache-owning
layers are exactly `kv_source_layer_ids = [2, 8, 14, 20]` — layers 2/8/14 at
`compress_ratio` 2, layer 20 at ratio 1 — and **each owns two caches**:

```
main KV   (compress_kv_cache, 512-d, NVFP4-style E2M1 + one E4M3 scale per 16):
  per entry = 512 × 0.5 B + (512/16) × 1 B = 288 B
  3 layers at r=2 → 3 × 144 = 432 ; layer 20 at r=1 → 288      = 720 B/token

indexer K (indexer.k_cache, 128-d, MXFP4 E2M1 + one E8M0 scale per 32):
  per entry = 128 × 0.5 B + (128/32) × 1 B =  68 B
  3 layers at r=2 → 3 ×  34 = 102 ; layer 20 at r=1 →  68      = 170 B/token
                                                                 -----------
GLOBAL KV                                                        890 B/token
```

Recomputed in `python3` this pass: 720 + 170 = **890**, exactly DeepSeek's published figure.
The 0.5625 B/element shorthand of METHODOLOGY §1 is a *weight* rate; the KV record here is
0.5 B/element plus its own scale stride, and the two scale strides differ between the main
(per 16, E4M3) and indexer (per 32, E8M0) caches — which is why a single flat rate misses
the indexer term. Other KV dtypes for the same layout, from the same section:
**FP8 1,650 B/token (1.85×), BF16 3,200 B/token (3.60×)**. The 890 B/token number is the
**FP4-KV figure**, and per this section's kernel gating that cache layout is reachable only
on sm_10x — so it is an as-deployed Blackwell-DC number, not a universal property of the
model. Fixed per-sequence SWA state is separate: 2.77 MiB (FP8 ring buffer, engine-optimal)
to 5.38 MiB (BF16 reference implementation) ⚠️, see architecture.md §5.3.

---

## 12. Paged attention

| Backend | Native paging | Supported block/page sizes |
|---|---|---|
| FLASH_ATTN (FA2/3/4) | ✅ | multiples of 16; **128 forced** for the FA4 Blackwell hd256 kernel [src](https://docs.vllm.ai/en/latest/design/attention_backends/) |
| FA4 upstream on sm_120 | ❌ | *"Paged KV not supported on SM 12.0 in this PR"* [src](https://github.com/Dao-AILab/flash-attention/blob/main/flash_attn/cute/interface.py) |
| FLASHINFER (all variants) | ✅ | 16, 32, 64, 128, 256, 512, 1024 |
| FLASHMLA | ✅ | **64** |
| CUTLASS_MLA | ✅ | **128** |
| FLASHINFER_MLA / TOKENSPEED_MLA / TRTLLM MLA | ✅ | **32 or 64** |
| CuteDSL MLA (SGLang) | ✅ | 32 or 64, decode-only |
| `FLASHMLA_SPARSE_DSV41` | ✅ | **64 on SM90, 128 elsewhere** [src](https://github.com/vllm-project/vllm/blob/main/vllm/models/deepseek_v41/sparse_mla.py) |
| `FLASHINFER_MLA_SPARSE_DSV41` | ✅ | **128** |
| `FLASHINFER_MLA_SPARSE_DSV4` | ✅ | **256** |
| `FLASHINFER_MLA_SPARSE_SM120` | ✅ | **64 or 256** |
| TRTLLM MHA / XQA | ✅ | 16 / 32 / 64 (XQA also 8 and 128 in the legacy flow) |
| HPC_ATTN | ✅ | **64** |
| Ascend | ✅ | **128** |
| TRITON_ATTN | emulated at the wrapper | %16 |
| ROCM_AITER_FA | ✅ | 16, 32 |
| ROCM_AITER_MLA | ✅ | %1 |

SGLang states the emulation rule explicitly: *"Many backends that do not natively operate
on pages can emulate page_size > 1 at the wrapper layer by expanding page tables to
per-token indices"*, and lists the backends that **cannot** be reduced: *"TRTLLM MHA
(16/32/64), TRTLLM MLA (32/64), CuteDSL MLA (32/64), FlashMLA (64), Cutlass MLA (128),
Ascend (128), HPC-Ops (64)"*
[src](https://docs.sglang.io/advanced_features/attention_backend.html). It also spells out
the prefix-cache interaction: *"Use page_size = 1 for maximum prefix reuse (token-level
matching). Note that higher page sizes generally improve attention kernel performance."*

FA4 has had paged-KV correctness work as recently as beta23: *"Add paged-KV block_table
bounds check in mha_fwd_kvcache"* [src](https://github.com/Dao-AILab/flash-attention/releases).

---

## 13. Sliding window, R-SWA and attention sinks

| Backend | Sliding window | Attention sink |
|---|---|---|
| FA2 | ✅ | ❌ |
| FA3 | ✅ | ✅ |
| FA4 | ✅ (fixed in beta30: *"Fix `seqlen_k_loaded` treating a window bound of 0 as unbounded (sliding window)"*) [src](https://github.com/Dao-AILab/flash-attention/releases) | ✅, incl. learnable sink backward on SM90/SM100/SM110 |
| FA4 hd256 Blackwell kernel | ❌ — *"does not support local attention yet"* [src](https://github.com/Dao-AILab/flash-attention/blob/main/tests/cute/test_flash_attn.py) | ❌ |
| FlashInfer trtllm-gen | ✅ | ✅ |
| FlashInfer native / XQA | ✅ | ❌ (per vLLM's table) |
| FlashInfer CuTe-DSL GQA decode | ✅ since v0.6.15, *"adds sliding-window and attention-sink masking"* [src](https://github.com/flashinfer-ai/flashinfer/releases/tag/v0.6.15) | ✅ |
| FlashInfer FMHAv2 | ✅ *"head_dim 256/512 + sliding-window masks"* since v0.6.14 [src](https://flashinfer.ai/releases/) | — |
| FlashInfer HCA decode | ✅ *"arbitrary sliding-window row order including ring rotation and wraparound"* [src](https://github.com/flashinfer-ai/flashinfer/releases/tag/v0.6.18) | — |
| TRITON_ATTN | ✅ | ✅ |
| TRT-LLM | ✅ — *"kv cache as a circular buffer"*, `max_attention_window_size` [src](https://nvidia.github.io/TensorRT-LLM/advanced/gpt-attention.html) | — |
| AITER (SGLang row) | ✅ | ✅ (vLLM ROCM_AITER_FA row) |
| ROCm Triton FA2 | ⚠️ *"Sliding window attention is currently a work in progress"* [src](https://github.com/Dao-AILab/flash-attention) | — |
| `FLASHINFER_MLA_SPARSE_DSV4` / `DSV41` | ✅ (the V4 pipeline is *"compressor + SWA + indexer"*) | ✅ Sink column is ✅ [src](https://docs.vllm.ai/en/latest/design/attention_backends/) |
| TRITON_FLASHINFER composite | ❌ R-SWA [src](https://docs.vllm.ai/en/latest/design/attention_backends/) | ❌ |

`supports_rswa()` returns True for the FlashAttention backend in vLLM
[src](https://github.com/vllm-project/vllm/blob/main/vllm/v1/attention/backends/flash_attn.py);
R-SWA is one of the features that forces an FA4-hd256 → FA2 fallback.

---

## 14. GQA / MLA / hybrid-linear attention in vLLM and SGLang, per GPU

### 14.1 GQA / MHA / MQA

Standard territory; all backends in §9 handle it. The one nuance worth recording is
**Pack-GQA**, an FA4 optimization that packs the query heads of a GQA group into one tile.
It landed on SM120 only in beta21 (*"Implement Pack-GQA on SM120 (+ graceful SplitKV
fallback)"*) and got a predication fix in beta31 (*"Fix PackGQA predication for padded head
dimensions"*) [src](https://github.com/Dao-AILab/flash-attention/releases). It is
auto-selected and explicitly **disabled** for the Blackwell hd256 kernel
(`pack_gqa = False`) [src](https://github.com/Dao-AILab/flash-attention/blob/main/flash_attn/cute/interface.py).

### 14.2 MLA

vLLM splits MLA into prefill and decode backends
[src](https://docs.vllm.ai/en/latest/design/attention_backends/):

*Prefill* (`-ac.mla_prefill_backend`): `FLASH_ATTN` (any compute cap, but only for
`(qk_nope,qk_rope,v)` ∈ {(128,64,128), (192,64,256), (64,64,128), (256,0,256)}),
`TRTLLM_RAGGED` (10.x, first two shapes), `FLASHINFER` (10.x, (128,64,128) only),
`TOKENSPEED_MLA` (10.x, (128,64,128) only).

*Decode* priority order: `FLASHINFER_MLA` → `TOKENSPEED_MLA` → `CUTLASS_MLA` →
`FLASH_ATTN_MLA` → `FLASHMLA` → `TRITON_MLA` → `FLASHINFER_MLA_SPARSE` → `FLASHMLA_SPARSE`.

| Decode backend | Compute cap. | Block sizes | Sparse |
|---|---|---|---|
| FLASHINFER_MLA | 10.x | 32, 64 | ❌ |
| TOKENSPEED_MLA | 10.x | 32, 64 | ❌ (FP8 KV required) |
| CUTLASS_MLA | 10.x | 128 | ❌ |
| FLASH_ATTN_MLA | 9.x | %16 | ❌ |
| FLASH_ATTN_MLA_SPARSE | 9.x | 64 | ✅ |
| FLASHMLA | 9.x–10.x | 64 | ❌ |
| FLASHMLA_SPARSE | 9.x–10.x | 64, head size 576 | ✅, KV `bfloat16`/`fp8_ds_mla`/**`nvfp4_ds_mla`** |
| TRITON_MLA | **Any** | %16 | ❌ |
| ROCM_AITER_MLA / _SPARSE / _TRITON_MLA | N/A (ROCm) | %1 / `1,%16` / Any | ❌ / ✅ / ❌ |
| CPU_MLA / AMX_MLA | N/A | 16 / %32, head 576 | ❌ |

`flash_attn_supports_mla()` in vLLM additionally requires
`is_device_capability_family(90)` — **FlashAttention-based MLA is Hopper-only**
[src](https://github.com/vllm-project/vllm/blob/main/vllm/v1/attention/backends/fa_utils.py).

### 14.3 Hybrid linear attention — Gated DeltaNet (GDN)

vLLM registers GDN under a separate `MambaAttentionBackendEnum` (`GDN_ATTN`, alongside
`MAMBA1`, `MAMBA2`, `SHORT_CONV`, `LINEAR`)
[src](https://github.com/vllm-project/vllm/blob/main/vllm/v1/attention/backends/registry.py).
The prefill kernel is chosen by `_resolve_gdn_prefill_backend`, whose docstring is the
per-GPU truth
[src](https://github.com/vllm-project/vllm/blob/main/vllm/model_executor/layers/mamba/gdn/qwen_gdn_linear_attn.py):

> FlashInfer's GDN prefill kernel is chosen when: `requested in ["flashinfer", "auto"]`;
> `platform == cuda`; one of the following: **Hopper (SM90) — no further constraints;
> Blackwell (SM10.x) with `head_k_dim == 128`, `cuda_runtime >= 13`; Blackwell (SM12.x)
> with `head_k_dim == 128`, `cuda_runtime >= 13`.**
> In-tree CuteDSL GDN prefill kernel is chosen when: "cutedsl" is requested (opt-in only);
> **Blackwell (SM10.x) with `head_k_dim == 128`.**

with the code comment on the SM12x branch: *"The in-tree CuteDSL kernel targets SM100 only,
so it stays off here."*

| GPU | GDN prefill default | CuteDSL GDN | GDN decode (`VLLM_GDN_DECODE_KERNEL`) |
|---|---|---|---|
| A100 | Triton/FLA | ❌ | fused CUDA kernel ✅ (`has_device_capability(80)`) |
| H100/H200 | **FlashInfer** (JIT warm-up warned) | ❌ | fused CUDA ✅ |
| B200/B300 | **FlashInfer** (needs `head_k_dim == 128`, CUDA ≥ 13) | ✅ opt-in | fused CUDA ✅ |
| RTX PRO 6000 | **FlashInfer** (same conditions) | ❌ | fused CUDA ✅ |
| MI355X | Triton/FLA via AITER Triton kernels | ❌ | Triton |

The fused CUDA GDN decode kernel needs *"a BF16 GDN model with K=V=128, SiLU or sigmoid
gating, non-interleaved GQA layout, BF16 convolution cache, BF16 or FP32 recurrent state,
and a GPU with compute capability 8.0+"* plus `torch.ops._C.fused_gdn_decode_post_conv_mtp`
being built; otherwise it silently falls back to Triton
[src](https://github.com/vllm-project/vllm/blob/main/vllm/model_executor/layers/mamba/gdn/qwen_gdn_linear_attn.py).

SGLang exposes `--linear-attn-backend` (default `triton`) with per-phase overrides
`--linear-attn-decode-backend` / `--linear-attn-prefill-backend`, and auto-selects
FlashInfer for GDN prefill *"On SM100/SM103 with CUDA 13+ … when the per-phase override is
unset, the base linear-attention backend is Triton, recurrent state is BF16, key/value
head dimensions are 128, dynamic chunking and page-major KV layout are disabled, and
`--chunked-prefill-size` is between 1 and 8192"*
[src](https://docs.sglang.io/advanced_features/attention_backend.html).

SGLang's GDN backend table:

| Backend | Decode | Prefill/Extend | Spec decoding (target verify) |
|---|---|---|---|
| Triton (CUDA) | ✅ | ✅ | ✅ |
| Triton (AMD/ROCm) | ✅ | ✅ | ✅ |
| Triton (NPU) | ✅ | ✅ | ❌ |
| Triton (CPU) | ✅ | ✅ | ❌ |
| CuTe DSL (CUDA only) | ✅ | ❌ | ❌ |
| FlashInfer (CUDA, SM90/SM100/SM103) | ✅ | ✅ | ✅ linear chain; tree falls back to Triton |

And the **full-attention** layers of a hybrid GDN model are separately constrained in
SGLang: *"Blackwell SM120: `triton` or `flashinfer` for prefill/full attention;
`trtllm_mha` is supported for `--decode-attention-backend` only. Other Blackwell variants
(including SM100 B200/GB200): `triton`, `trtllm_mha`, or `fa4` only. NPU (Ascend):
`ascend` only. AMD (ROCm): `triton` recommended. Other CUDA (Hopper, Ampere, etc.):
auto-selection works."*
[src](https://docs.sglang.io/advanced_features/attention_backend.html)

### 14.4 Hybrid linear attention — Kimi Delta Attention (KDA)

vLLM's KDA lives in `vllm/models/kimi_k3/nvidia/kda.py` with three decode backends
(`native`, `flashinfer`, `triton`) resolved by `resolve_kda_decode_backend`
[src](https://github.com/vllm-project/vllm/blob/main/vllm/models/kimi_k3/nvidia/kda.py):

**Native fused CUDA KDA decode** requires all of:
`num_heads ∈ {12, 24, 48, 96}`, `head_dim == 128`, `conv_width == 4`, `num_spec == 0`,
BF16 input and conv state, **FP32 recurrent state**, `torch.ops._C.fused_kda_decode` built,
and `is_device_capability(90) or is_device_capability_family(100) or
is_device_capability_family(120)` — comment: *"SM90 is architecture-specific; SM10x and
SM12x use family binaries."*

**FlashInfer fused KDA decode** requires:
`compute_capability in ((10, 0), (10, 3))` — **B200 and B300 only** —
`num_heads ∈ {12, 24, 32, 48, 96}`, `head_dim == 128`, `conv_width == 4`, `num_spec == 0`,
BF16 input and conv state, recurrent state FP32 **or BF16**, and a non-dim-first conv state.

| GPU | Native fused KDA decode | FlashInfer fused KDA decode | Fallback |
|---|---|---|---|
| A100 | ❌ | ❌ | Triton |
| H100/H200 | ✅ | ❌ | Triton |
| B200 | ✅ | ✅ | — |
| B300/GB300 | ✅ | ✅ (the `(10,3)` case is explicit) | — |
| RTX PRO 6000 | ✅ (family 120) | ❌ | — |
| MI355X | ❌ (CUDA-only gate) | ❌ | Triton |

FlashInfer's own claim for the kernel: *"`fused_kda_decode` folds Kimi K3's width-four
depthwise causal convolution"* into a single SM100 kernel, measuring
**1.33× the vLLM fused kernel at one row (table geomean 1.13×)** under CUDA Graphs, for
head dim 128 with 12/24/48/96 heads
[src](https://github.com/flashinfer-ai/flashinfer/releases/tag/v0.6.18). Note this is
FlashInfer-vs-vLLM-native, **not** vs Triton — the Triton gap is much larger and
unpublished (⚠️ **TO BE VERIFIED**).

Kimi K3's *full-attention* (Gated MLA) layers are separate: FlashInfer v0.6.17 added
Blackwell decode for *"Kimi K3's MLA geometry — 96 global query heads against one KV
head"* [src](https://flashinfer.ai/releases/). vLLM's published Kimi-K3 recipe reaches for
`TOKENSPEED_MLA` on both sides:
`--attention-backend TOKENSPEED_MLA` and
`--attention-config '{"use_prefill_query_quantization":true,"mla_prefill_backend":"TOKENSPEED_MLA"}'`,
with `FLASHINFER` and `TRTLLM_RAGGED` as the alternatives, plus `--prefix-match-unit 128`
*"for prefix caching alignment with MLA kernel boundaries"*
[src](https://recipes.vllm.ai/moonshotai/Kimi-K3). `TOKENSPEED_MLA` is **10.x-only and
requires FP8 KV** [src](https://docs.vllm.ai/en/latest/design/attention_backends/) — so
that recipe is a Blackwell recipe.

---

## 15. Relative acceleration between GPUs

All ratios computed in `python3` from the cited measurements; the denominators are the
§1.1 peaks.

### 15.1 Like-for-like: dense BF16 forward attention, head dim 128

| Comparison | Ratio | Basis |
|---|---|---|
| **B200 FA4 ÷ H100 FA3** | **2.18×** | 1,613 ÷ 740 [src](https://arxiv.org/html/2603.05451v1), [src](https://lambda.ai/blog/flashattention-4-gives-the-nvidia-blackwell-platform-its-most-optimized-attention-kernel-yet) |
| Underlying dense BF16 peak ratio B200 ÷ H100 | 2.27× | 2,250 ÷ 989.5 (⚠️ B200 peak) |
| Kernel efficiency B200 vs H100 | 0.96× | 71% ÷ 75% utilization — **FA4 on Blackwell is slightly *less* efficient than FA3 on Hopper**; the win is all silicon |
| B300-in-GB300-NVL72 FA4 ÷ H100 FA3 | **~2.42×** `est.` ⚠️ | scaled from B200 by 2500/2250; no measurement exists |
| **HGX B300** FA4 ÷ H100 FA3 | **2.18×** `est.` ⚠️ | corrected 2026-09-19: HGX B300 dense BF16 = 2,250, the same as B200 [src](https://www.nvidia.com/en-us/data-center/hgx/), so the scale factor is 1.0× and the ratio is B200's |

### 15.2 Sparse MLA (the DeepSeek path)

| Comparison | Ratio | Basis |
|---|---|---|
| Sparse MLA **prefill** B200 ÷ H800 | **2.27×** | 1,450 ÷ 640 [src](https://github.com/deepseek-ai/FlashMLA) |
| Sparse MLA **decode** B200 ÷ H800 | **1.71×** | 700 ÷ 410 [src](https://github.com/deepseek-ai/FlashMLA) |
| Sparse MLA decode efficiency B200 vs H800 | 0.75× | 31.1% vs 41.4% of peak `est.` |

Prefill tracks the peak-FLOPS ratio almost exactly; **decode does not**. If your workload
is decode-dominated and MLA-shaped — which DeepSeek-V4.1-Flash and Kimi-K3 both are — the
Blackwell uplift over Hopper is closer to **1.7×** than to the 2.2–2.3× the datasheets
suggest.

### 15.3 MI355X

| Comparison | Ratio | Caveat |
|---|---|---|
| AITER ASM ÷ CK on **MI350X**, hd256 non-causal | **1.14×** | 497 ÷ 436 [src](https://github.com/ROCm/aiter/pull/5403); **open PR**. Corrected 2026-09-19: the PR names **MI350X**, not MI355X — the *ratio* still holds (same part both sides), only the part label was wrong |
| AITER ASM ÷ CK on **MI350X**, hd256 causal | **1.17×** | 509 ÷ 436, same PR, same MI350X correction |
| **MI350X** CK (436, hd256) ÷ H100 FA3 (740, hd128) | 0.59× | ⚠️ **not like-for-like** — different head dim, different kernel generation, different batch/sequence config, **and now a different AMD part than the one this section is about**. This ratio is **not** a valid MI355X-vs-H100 attention claim. |
| MI355X vs B200/B300 attention | — | ⚠️ **TO BE VERIFIED** — no like-for-like published comparison exists. AMD's own tracking issue for the MLA-decode comparison remains open [src](https://github.com/ROCm/aiter/issues/2493) |

**The honest summary for MI355X.** Its best published attention numbers are ~500–680
TFLOPS at head dim 256, from **unmerged** PRs, at ~20–27% of the 2,500 TFLOPS dense BF16
peak (METHODOLOGY §8; percentages recomputed 2026-09-19 — was 2,510), on a
kernel that falls back to CK for GQA/MQA, dense BSHD layout, softcap, sinks and bias.
⚠️ **Corrected 2026-09-19: of those, only the 676 TFLOPS backward figure is actually an
MI355X measurement — the 497/509/436 forward figures are MI350X.** B200
runs FA4 at 1,613 TFLOPS at head dim 128 with full feature coverage. Do not plan an MI355X
attention-bound deployment on parity assumptions.

### 15.4 What does not exist

⚠️ **TO BE VERIFIED**, all of them:
- Any FA4 measurement on H100 (so FA3→FA4 on Hopper is unquantified).
- Any FA4 or FlashMLA measurement on B300/GB300.
- Any attention TFLOPS number for RTX PRO 6000 Blackwell.
- Any MI355X ↔ B200/B300 like-for-like attention comparison.
- Any FP8-attention vs BF16-attention speedup on any GPU.
- Any per-GPU indexer/top-k kernel latency table.

---

## 16. What each of this repo's five models actually uses

### 16.1 DeepSeek-V4.1-Flash (`deepseek41f`) and the NVFP4 variant (`deepseek41fnvfp4`)

From `research/models/deepseek41f/config.json` and the model card
[src](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash):

| Property | Value | Kernel consequence |
|---|---|---|
| Architecture | `DeepseekV41ForCausalLM`, Causal Encoder-Decoder: 40 layers = 20 encoder + 20 decoder | decoder global KV projected from final encoder hidden states |
| `num_attention_heads` | 64 | |
| `num_key_value_heads` | **1** | MQA-mode MLA — matches FlashMLA's *"MQA … `head_dim_k` = 512 (for DeepSeek V4/V4.1) with `head_dim_v` = 512"* [src](https://github.com/deepseek-ai/FlashMLA) |
| `head_dim` | **512** | matches `get_supported_head_sizes() -> [512]`; vLLM comment: *"DeepSeek V4 layout: 448 NoPE + 64 RoPE = 512"* |
| `qk_rope_head_dim` | 64 | |
| `sliding_window` | **128** | every layer carries a 128-token SWA component [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash) |
| `compress_ratios` | 43 entries (40 layers + 3 MTP): **20× ratio 1, 18× ratio 2, 5× ratio 0** (of the first 40: 20×1, 18×2, 2×0) | vLLM maps 0→`SWAONLY`, 1→`C1A`, 2→`C2A`; *"Ratio-1 and ratio-2 layers both attend over indexer topk indices into a shared compressed cache but differ in compressed page block size (`block_size // ratio`), so each needs its own FlashMLA tile-scheduler plan"* [src](https://github.com/vllm-project/vllm/blob/main/vllm/models/deepseek_v41/sparse_mla.py) |
| `kv_source_layer_ids` | `[2, 8, 14, 20]` | *"Four layers (2, 8, 14, 20) actually compress their own KV"* [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash) |
| `index_source_layer_ids` | `[2, 8, 14, 20, 24, 28, 32, 36]` | 8 indexing layers |
| `index_topk` / `index_n_heads` / `index_head_dim` | **512** / 32 / 128 | top-512 tokens per query |
| `candidate_topk_blocks` / `candidate_block_size` / `candidate_source_layer_id` | 2048 / 8 / 20 | hierarchical candidate pre-filter |
| KV storage | FP4 E2M1, one E4M3 scale per 16 channels → **890 bytes/token** global = **720 main + 170 indexer**, byte-exact (derivation in [models/deepseek41f/architecture.md §5.1](../models/deepseek41f/architecture.md); reproduced in §11 above). FP8 equivalent 1,650 B/token, BF16 3,200 | the 890 B/token figure **is the FP4-KV figure** and requires `nvfp4_ds_mla` → `FLASHMLA_MEGA_ATTN_DSV41` → **sm_10x only**; elsewhere budget 1,650 B/token at FP8 |
| `num_nextn_predict_layers` / DSpark | 3 / `dspark_block_size: 5`, targets layers 37–39 | recipe: *"DSpark-only method, drafts 5-token blocks"* |

**Backend per GPU:**

| GPU | vLLM backend that actually runs | FP4 compressed KV? | Notes |
|---|---|---|---|
| A100 | **none** — every DSV41 backend requires major ≥ 9 | ❌ | **not deployable** |
| H100 / H200 | `FLASHMLA_SPARSE_DSV41`, **block size 64** | ❌ — `fp8_ds_mla` at best | FlashMLA sparse *decode* for V4.1 is *"only available on SM100"* [src](https://github.com/deepseek-ai/FlashMLA) — ⚠️ conflicts with vLLM's `major in [9, 10]` gate; see Open questions |
| B200 | `FLASHMLA_MEGA_ATTN_DSV41` (best) or `FLASHMLA_SPARSE_DSV41` or `FLASHINFER_MLA_SPARSE_DSV41`, block 128 | ✅ `nvfp4_ds_mla` | `--attention_config.use_fp4_indexer_cache True` [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash) |
| B300 / GB300 | same as B200 (`major == 10`) | ✅ | recipe default *"TP2 for B300 … for high interactivity, deploy using TP4"* [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash); NVFP4 checkpoint *"tested with this NVFP4 checkpoint on GB300"* [src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4) |
| RTX PRO 6000 | `FLASHINFER_MLA_SPARSE_DSV41` — **FP8 KV dtype mandatory**, needs a FlashInfer build with the SM120 sparse-MLA decode API | ❌ **via vLLM**; the FlashInfer SM120 NVFP4 sparse-MLA kernel itself merged 2026-09-07 [src](https://github.com/flashinfer-ai/flashinfer/pull/4955) — see §11 | *"Cannot use SM100 FP4 indexer cache"*; `--attention_config.use_fp4_indexer_cache False` [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash) |
| MI355X | `ROCM_FLASHMLA_SPARSE_DSV4` / `ROCM_AITER_MLA_SPARSE`, `--moe-backend aiter`, `VLLM_ROCM_USE_AITER=1` | ❌ | *"the ROCm sparse SWA backend only supports uniform-batch CUDA graphs"* → `VLLM_USE_BREAKABLE_CUDAGRAPH=1`; adaptive verification must be off; *"Torch.compile unsupported on AMD"* [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash) |

SGLang path: `--attention-backend dsa`, sub-backends `flashmla_sparse` (default prefill on
Hopper and Blackwell, BF16), `fa3` (default decode Hopper BF16), `trtllm` (default decode
Blackwell BF16 and default for FP8 on Blackwell), `tilelang` (default on ROCm)
[src](https://docs.sglang.io/advanced_features/attention_backend.html). ⚠️ **TO BE
VERIFIED**: that table is documented for **V3.2**; whether the same sub-backend names and
defaults apply to V4.1's CSA2 topology in SGLang was not confirmed.

**TokenSpeed path (added 2026-09-19, sweep).** TokenSpeed **does** ship a
DeepSeek-V4.1-Flash recipe, and it uses **its own attention backend, not any of the above**:
a dedicated **FlatKV** backend with a four-group KV cache (global KV chains, SWA rows,
compressor tails), the last two declared *replayable* so a prefix hit re-feeds the cached
prefix's last 128 tokens; the CED decoder (layers 20–39) forces
`prefill_graph=False` / `--disable-prefill-graph` while decode CUDA graphs are unaffected.
There is a per-commit **GB300 Slurm 1P1D CI** job (TP4 prefill + TP4 decode)
[src](https://lightseek.org/tokenspeed/recipes/models), details in
[cross-cutting/inference-engines.md §2.5](inference-engines.md). Any earlier reading that
TokenSpeed has no V4.1-Flash recipe is wrong.

**The NVFP4 *weight* variant (`deepseek41fnvfp4`) does not change any of the above.**
Its routed experts are re-quantized from MXFP4 to NVFP4; the attention tensors and the KV
layout are untouched, so every backend, block size and KV dtype in the table above applies
unchanged, and it carries no published attention speedup over the base checkpoint
(it is larger on disk — 527.27 GB vs 510.29 GB — and accuracy-neutral; METHODOLOGY §8 and
[models/deepseek41fnvfp4/architecture.md](../models/deepseek41fnvfp4/architecture.md)).

The NVFP4 checkpoint (`nvidia/DeepSeek-V4.1-Flash-NVFP4`, released **2026-09-16**,
quantized with `nvidia-modelopt v0.47.0rc0`) declares
*"Supported Hardware Microarchitecture Compatibility: NVIDIA Blackwell"* and nothing else,
with vLLM and SGLang as the runtimes, both tested on GB300
[src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4). That is a weight-quant
statement, but it lines up exactly with the attention-kernel story: this model wants sm_10x.

### 16.2 Kimi-K3 (`kimik3`)

From `research/models/kimik3/config.json` and the model card
[src](https://huggingface.co/moonshotai/Kimi-K3):

| Property | Value | Kernel consequence |
|---|---|---|
| Layers | **93** = **69 KDA + 24 Gated MLA** (verified by counting `kda_layers` and `full_attn_layers`) | two entirely separate kernel stacks per forward pass |
| `full_attn_layers` | `[4, 8, …, 88, 92, 93]` — every 4th, plus a doubled tail at 92/93 | |
| MLA geometry | `num_attention_heads` 96, `num_key_value_heads` 96, `kv_lora_rank` **512**, `qk_nope_head_dim` 128, `qk_rope_head_dim` 64, `v_head_dim` 128, `q_lora_rank` 1536 | FlashInfer v0.6.17: *"Blackwell decode covers Kimi K3's MLA geometry — 96 global query heads against one KV head"* [src](https://flashinfer.ai/releases/) |
| `mla_use_nope` / `mla_use_output_gate` | true / true | gated MLA |
| KDA geometry | `num_heads` 96, `head_dim` **128**, `short_conv_kernel_size` **4**, `use_full_rank_gate` true | hits **every** condition of both fused KDA decode kernels: heads ∈ {12,24,48,96} ✅, head_dim 128 ✅, conv_width 4 ✅ |
| Quantization | `mxfp4-pack-quantized`, group 32, with `ignore: ["re:.*self_attn.*", "re:.*shared_experts.*", …]` | **attention weights stay BF16** — MXFP4 is MoE-only |
| MTP | `num_nextn_predict_layers: 0` | ⚠️ note this contradicts using KDA's `num_spec == 0` requirement favourably: with no MTP head in the config, `num_spec == 0` holds and the fused KDA decode kernels stay eligible |
| Vision tower | `"_attn_implementation": "flash_attention_2"` | the ViT explicitly asks for FA2 |

**Attention kernel path per GPU** (both stacks in one place, from §9, §14.2 and §14.4 —
this is the row the `research/models/kimik3/<gpu>.md` pair docs should cite):

| GPU | Gated-MLA layers (24) — prefill / decode | KDA layers (69) — decode | Vision tower |
|---|---|---|---|
| A100 | `TRITON_MLA` both phases (the only `Any`-capability MLA backend) | Triton (fused CUDA KDA decode needs cc ≥ 9.0) | FA2 |
| H100 / H200 | `FLASH_ATTN_MLA` (9.x) or `FLASHMLA` (9.x–10.x), block 64 | **native fused CUDA** (`is_device_capability(90)`) | FA2 |
| B200 | `TOKENSPEED_MLA` (10.x, **FP8 KV required**) per the vLLM recipe; `FLASHINFER_MLA` / `CUTLASS_MLA` / `TRTLLM_RAGGED` as alternates | **FlashInfer fused** (`(10,0)`) — 1.33× at one row, geomean 1.13× vs native — or native | FA2 |
| B300 / GB300 | same as B200 (`sm_103` satisfies every `major == 10` gate) | **FlashInfer fused** (`(10,3)` is explicit) or native | FA2 |
| RTX PRO 6000 | `TRITON_MLA` only — `TOKENSPEED_MLA`, `CUTLASS_MLA`, `FLASHINFER_MLA` are all 10.x | **native fused CUDA** (family 120); FlashInfer fused ❌ | FA2 |
| MI355X | `ROCM_AITER_MLA` (or `ROCM_AITER_TRITON_MLA`), `VLLM_ROCM_USE_AITER=1` | Triton (the fused kernels are CUDA-capability-gated) | FA2 |

Flags this path needs: `--kv-cache-dtype fp8_e4m3` (mandatory for `TOKENSPEED_MLA`) and
`--prefix-match-unit 128` for MLA prefix-cache alignment
[src](https://recipes.vllm.ai/moonshotai/Kimi-K3).

**MLA layers per GPU:** on Blackwell vLLM's recipe uses `TOKENSPEED_MLA` for both prefill
and decode, which is 10.x-only and FP8-KV-required
[src](https://recipes.vllm.ai/moonshotai/Kimi-K3),
[src](https://docs.vllm.ai/en/latest/design/attention_backends/). On Hopper the available
MLA decode backends are `FLASH_ATTN_MLA` (9.x) and `FLASHMLA` (9.x–10.x); on RTX PRO 6000
and MI355X, `TRITON_MLA` (Any) and `ROCM_AITER_MLA` respectively.

⚠️ **TO BE VERIFIED**: whether `KimiK3ForConditionalGeneration`'s MLA layers are wired to
the generic MLA backend registry at all, or to a model-private backend the way DeepSeek-V4.1
is. vLLM's `vllm/models/kimi_k3/` has both `nvidia/` and `amd/` branches
[src](https://github.com/vllm-project/vllm/blob/main/vllm/models/kimi_k3/__init__.py),
which suggests model-private wiring.

### 16.3 Qwen3.8-27B (`qwen3827b`)

From `research/models/qwen3827b/config.json` and the model card
[src](https://huggingface.co/Qwen/Qwen3.8-27B):

| Property | Value | Kernel consequence |
|---|---|---|
| Layers | 64, `full_attention_interval: 4` → **48 linear_attention + 16 full_attention** (verified from `layer_types`) | *"Hidden Layout: 16 × (3 × (Gated DeltaNet → FFN) → 1 × (Gated Attention → FFN))"* |
| Full attention | `num_attention_heads` 24, `num_key_value_heads` 4 → GQA 6:1; **`head_dim` 256**; `partial_rotary_factor` 0.25 → RoPE dim 64; `attn_output_gate: true` | |
| GDN | `linear_num_key_heads` 16, `linear_num_value_heads` **48**, `linear_key_head_dim` **128**, `linear_value_head_dim` 128, `linear_conv_kernel_dim` **4**, `mamba_ssm_dtype: float32` | `linear_key_head_dim == 128` satisfies the FlashInfer-GDN gate on SM10x/SM12x |
| Context | 262,144 native, 1M via YaRN | |
| MTP | `mtp_num_hidden_layers: 1` | `--speculative-config '{"method":"mtp","num_speculative_tokens":3}'` [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) |

**`head_dim = 256` is the whole story for this model's full-attention layers.**

| GPU | Full-attention kernel | Why |
|---|---|---|
| A100 | FA2 | FA2 supports head dims to 256 |
| H100 / H200 | **FA3** | SM90 allows `head_dim` 8–256 [src](https://github.com/Dao-AILab/flash-attention/blob/main/flash_attn/cute/interface.py) |
| B200 / B300 | **FA4 hd256 2-CTA kernel** — `BlackwellFusedMultiHeadAttentionForward` — but **only** if block size is a multiple of 128, and with no sink, no softcap, no R-SWA, no DCP, no quantized KV, no mm_prefix. Otherwise vLLM silently drops to **FA2** [src](https://github.com/vllm-project/vllm/blob/main/vllm/v1/attention/backends/fa_utils.py) | `head_dim ∈ {256×256, 192×128}` are the only >128 shapes FA4 allows on Blackwell |
| RTX PRO 6000 | FA2, or FlashInfer (head sizes include 256), or Triton | FA4 unavailable in vLLM on 12.x |
| MI355X | CK FA2 (hd 256 supported), or the AITER ASM hd256 kernel **if merged** — but that kernel is *"MHA only (nhead_q == nhead_k); GQA/MQA falls back to CK"* [src](https://github.com/ROCm/aiter/pull/5403), and Qwen3.8 is 24:4 GQA → **falls back to CK** | |

This is a concrete, costly trap: on Blackwell, pinning `--block-size 64` on Qwen3.8-27B
turns FA4 off and gives you FA2 — vLLM's doc says a pinned block size *"not a multiple of
128 instead makes FlashAttention ineligible for such models, which is an error if the
backend was requested explicitly"*
[src](https://docs.vllm.ai/en/latest/design/attention_backends/). And on MI355X, the one
tuned hd256 attention kernel AMD has is MHA-only, so this model does not get it.

The GDN layers follow §14.3 exactly: FlashInfer GDN prefill on H100/H200/B200/B300/RTX PRO
6000 (all satisfy `head_k_dim == 128`; Blackwell also needs CUDA ≥ 13), CuteDSL opt-in on
B200/B300 only, Triton on A100 and MI355X.

Published serve commands are terse and consistent with the above:
GB300 TP1 `--kv-cache-dtype fp8` on the NVFP4 checkpoint; GB300 TP4 on the FP8 checkpoint;
RTX 5090 TP2 NVFP4; note *"MXFP4 does not load on Nvidia devices … Use NVFP4 quantization
on Nvidia instead"* [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B).

### 16.4 Marlin-2B (`marlin2b`)

`NemoStation/Marlin-2B` is **gated** and was not fetchable in this pass. From the repo's
own metadata: *"GATED. Qwen3.5-2B based video model; transformers>=5.7.0, torchcodec"*
(`marlin2b/model.env`). The base model's config is public
[src](https://huggingface.co/Qwen/Qwen3.5-2B):

| Property | Qwen3.5-2B value | Kernel consequence |
|---|---|---|
| Layers | 24, `full_attention_interval: 4` → 18 linear + **6 full attention** | same hybrid shape as Qwen3.8 |
| Full attention | `num_attention_heads` 8, `num_key_value_heads` 2 → GQA 4:1; **`head_dim` 256**; `attn_output_gate: true` | **identical head-dim-256 situation to §16.3** |
| GDN | `linear_num_key_heads` 16, `linear_num_value_heads` **16** (Qwen3.8 has 48), `linear_key_head_dim` **128**, `linear_conv_kernel_dim` 4 | `head_k_dim == 128` → FlashInfer GDN prefill eligible on the same GPUs |
| `hidden_size` / `intermediate_size` | 2048 / 6144 | |
| `tie_word_embeddings` | true | |
| MTP | `mtp_num_hidden_layers: 1` | |

**Attention kernel path per GPU** (inherited from §16.3, since the shapes are identical —
`head_dim` 256 full attention, `linear_key_head_dim` 128 GDN; the pair docs at
`research/models/marlin2b/<gpu>.md` should cite this row, with the ⚠️ below attached):

| GPU | Full-attention layers (6, hd 256, GQA 4:1) | GDN layers (18) — prefill / decode | Video/vision stack |
|---|---|---|---|
| A100 | FA2 (hd ≤ 256) | Triton-FLA / fused CUDA GDN decode (cc 8.0+) | FA2 ⚠️ unread config |
| H100 / H200 | **FA3** | FlashInfer GDN prefill / fused CUDA decode | ⚠️ |
| B200 | **FA4 hd256 2-CTA**, block size a multiple of **128**, else silent FA2 | FlashInfer GDN (CUDA ≥ 13) or CuteDSL opt-in / fused CUDA decode | ⚠️ |
| B300 / GB300 | same as B200 | same as B200 | ⚠️ |
| RTX PRO 6000 | FA2 / FlashInfer / Triton — FA4 is refused on 12.x by vLLM | FlashInfer GDN prefill (family 120, CUDA ≥ 13) / fused CUDA decode | ⚠️ |
| MI355X | CK FA2 — the AITER ASM hd256 kernel is MHA-only and this model is 8:2 GQA | Triton via AITER | ⚠️ |

⚠️ **TO BE VERIFIED**: everything above is the **base** `Qwen/Qwen3.5-2B` config, not
Marlin-2B's. Marlin-2B is a video model built on it; its vision/temporal stack may add
attention layers with different head dims and its own kernel requirements. Accept the
Hugging Face licence and export `HF_TOKEN` to read `config.json` before relying on this
row. At ~5 GB it fits one GPU everywhere in this roster, so the interesting question is
purely which kernel it gets, not whether it fits.

---

## 17. Practical selection cheat-sheet

| Model | A100 | H100/H200 | B200 | B300/GB300 | RTX PRO 6000 | MI355X |
|---|---|---|---|---|---|---|
| DeepSeek-V4.1-Flash | **not deployable** | `FLASHMLA_SPARSE_DSV41`, blk 64, FP8 KV | `FLASHMLA_MEGA_ATTN_DSV41`, blk 128, **NVFP4 KV** | same as B200 | `FLASHINFER_MLA_SPARSE_DSV41`, blk 128, FP8 KV **mandatory** | `ROCM_*_SPARSE_DSV4` + AITER, FP8 KV, breakable CUDA graphs |
| Kimi-K3 (MLA layers) | `TRITON_MLA` | `FLASHMLA` / `FLASH_ATTN_MLA` | `TOKENSPEED_MLA` (FP8 KV) | `TOKENSPEED_MLA` | `TRITON_MLA` | `ROCM_AITER_MLA` |
| Kimi-K3 (KDA layers) | Triton | native fused CUDA | **FlashInfer fused** | **FlashInfer fused** | native fused CUDA | Triton |
| Qwen3.8-27B (full attn, hd256) | FA2 | **FA3** | **FA4 hd256, block size 128** | FA4 hd256, blk 128 | FA2 / FlashInfer / Triton | CK FA2 (ASM kernel is MHA-only) |
| Qwen3.8-27B (GDN layers) | Triton | FlashInfer | FlashInfer (or CuteDSL) | FlashInfer (or CuteDSL) | FlashInfer | Triton/AITER |
| Marlin-2B | as Qwen3.8 ⚠️ | as Qwen3.8 ⚠️ | as Qwen3.8 ⚠️ | as Qwen3.8 ⚠️ | as Qwen3.8 ⚠️ | as Qwen3.8 ⚠️ |

Flags that are not optional, collected:
`VLLM_ROCM_USE_AITER=1` (all AMD) ·
`VLLM_USE_BREAKABLE_CUDAGRAPH=1` (DeepSeek-V4.1 on ROCm) ·
`--attention_config.use_fp4_indexer_cache True/False` (SM100 vs SM120) ·
`--block-size` a multiple of 128 for FA4 hd256 ·
`--kv-cache-dtype fp8_e4m3` required by `TOKENSPEED_MLA` and by SGLang's `flashmla_sparse_q8` ·
CUDA ≥ 13 for FlashInfer GDN prefill on Blackwell ·
`--prefix-match-unit 128` for Kimi-K3 MLA prefix caching.

---

## Open questions

Every ⚠️ item in this document, consolidated.

**Hardware peaks (blocks every "% of peak" number here)**
1. ~~A100 dense BF16 matrix TFLOPS — not re-fetched from an NVIDIA datasheet this pass; 312 assumed.~~ **RESOLVED 2026-09-19**: NVIDIA's A100 page states "312 TFLOPS | 624 TFLOPS\*" with "\* With sparsity" — 312 dense is correct [src](https://www.nvidia.com/en-us/data-center/a100/).
2. ~~B200 SXM dense BF16 — 2,250 assumed.~~ **RESOLVED 2026-09-19**: HGX B200 FP16/BF16 = 36 PFLOPS sparse for 8 GPUs, dense = ½ → 2,250 per GPU [src](https://www.nvidia.com/en-us/data-center/hgx/).
3. ~~RTX PRO 6000 Blackwell — no dense BF16 figure obtained; NVIDIA product page returned HTTP 404.~~ **PARTLY RESOLVED 2026-09-19**: the Workstation page loads fine (the 404 claim was wrong) and gives 4000 AI TOPS (FP4 sparse), 125 TFLOPS FP32, 96 GB GDDR7, **1,792 GB/s** — but that is the **Workstation Edition** [src](https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000/). **Closed further in the 2026-09-19 sweep**: the **Server Edition** page — the part in this roster — was fetched and prints 96 GB GDDR7, **1597 GB/s**, FP32 120 TFLOPS, BF16 1 PFLOPS, FP8 2 PFLOPS, FP4 4 PFLOPS [src](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/) → dense **500 / 1,000 / 2,000** `est.` (½ of sparse), matching METHODOLOGY §8's "FP4 ≈ 2,000 dense". ⚠️ What remains open is only that NVIDIA publishes no *explicitly dense* tensor row for either SKU. **Never quote 1,792 GB/s for the Server Edition.**
4. MI355X dense BF16 — was 2,510, derived from an 8-GPU MXFP4 platform figure of 80.5 PFLOPS whose sparsity status is unknown; now **2,500**, the METHODOLOGY §8 pin, and all percentages here are computed against it. ⚠️ **Still second-hand as of the 2026-09-19 sweep, and the citation had to be corrected**: the ROCm CDNA4 architecture page was opened and carries only VRAM 288 GiB / CDNA4 / gfx950 — **no FLOPS row**, so it cannot be the source METHODOLOGY §8 attributes the peak to; flopper.io still returns HTTP 403 and both `amd.com` MI355X pages (product page and GPU-brochure PDF) timed out again. AMD's MI355X datasheet content (BF16 2.5 PFLOPS dense / 5 sparse, FP8 5 / 10.1, FP4 10.1 dense, 288 GB HBM3E, 8 TB/s) agrees with the pin but could not be opened directly this pass. **Disagreement to carry forward:** METHODOLOGY §8 says "peaks confirmed via ROCm CDNA4 page"; this document's own fetch of that page shows it contains no peaks. The value is right, the attribution is not.
5. ~~GB300 NVL72's second FP4 figure.~~ **RESOLVED 2026-09-19**: FP4 = 1,440 PFLOPS with sparsity | **1,080 PFLOPS without** → 15 PFLOPS dense FP4 per B300; cross-checked by HGX B300's explicit "144 PFLOPS | 108 PFLOPS" sparse|dense pair → 13.5 dense per GPU [src](https://www.nvidia.com/en-us/data-center/gb300-nvl72/), [src](https://www.nvidia.com/en-us/data-center/hgx/).
6. H800 SXM5 HBM bandwidth — needed to turn FlashMLA's 3,000 GB/s into a % of peak. ⚠️ **Still open 2026-09-19**: NVIDIA publishes no H800 page and the TechPowerUp entry returned HTTP 403. The "~89% of 3.35 TB/s" figure in §5.2 rests on an unsourced 3.35 TB/s assumption.
6b. **NEW 2026-09-19**: HGX B300 (2,250 dense BF16) and GB300 NVL72 B300 (2,500) are different operating points of the same silicon. Every "B300" number in this document, and in `research/gpus/b300.md` and `research/gpus/gb300.md`, needs to say which one it means. Blackwell Ultra's gain over B200 is FP4-only.

**FlashAttention**
7. FA4 on sm_120: upstream has `FlashAttentionForwardSm120`, but vLLM's `_is_fa4_supported` refuses 12.x. Does a locally built `vllm_flash_attn` (or `FLASH_ATTENTION_ARCH=120`) change this, and is the SM80-MMA SM120 path ever faster than FA2 in practice?
8. No published FA4 TFLOPS on **H100** — the FA3→FA4 delta on Hopper is unquantified.
9. No published FA4 TFLOPS on **B300/GB300** — does the `sm103a` `tcgen.ld.red` path buy anything beyond the clock/peak ratio?
10. FA4 FP8 attention: accuracy was being fixed as late as beta23/beta24. Is FP8 FA4 production-safe on B200/B300 as of 2026-09-19, and what is its speedup over BF16 FA4?
11. FA4 is still on `4.0.0.betaNN` tags. Is there a GA commitment or a stability guarantee?

**vLLM / SGLang disagreements**
12. SGLang lists FA4 as **FP4 KV ✅ / FP8 KV ❌**; vLLM lists FA4 KV dtypes as `fp8, fp8_e4m3`. Reconcile — most likely the two integrations wire different cache layouts.
13. vLLM's backend table gives FA4 "Compute Cap. ≥10.0" while its runtime gate is "9.x, 10.x, 11.x". Confirm which wins for an SM90 deployment that requests FA4 explicitly.
14. vLLM's table shows `FLASHINFER` Native at 8.x–9.x and trtllm-gen at 10.x, leaving **12.x with no FlashInfer standard-attention row**, even though FlashInfer's own README lists SM 12.0/12.1 as supported. Is this a vLLM wiring gap or a genuine kernel gap?

**DeepSeek-V4.1 sparse path**
15. **Direct conflict:** FlashMLA's README says *"Sparse Decoding for DeepSeek V4.1 is only available on SM100"*, while vLLM's `DeepseekV4SparseMLABackend.supports_compute_capability` returns `capability.major in [9, 10]` and sets a 64-token block size specifically for SM90. Does V4.1 sparse *decode* actually run on H100/H200, or does vLLM fall back to something else at runtime?
16. ~~Merge status of FlashInfer PR #4955 and PR #4982.~~ **RESOLVED 2026-09-19 — both merged**: #4955 on **2026-09-07** (SM120/SM121 NVFP4 sparse MLA, benchmarked on RTX PRO 5000 Blackwell at 1.41–1.67× prefill / 1.31× decode) [src](https://github.com/flashinfer-ai/flashinfer/pull/4955); #4982 on **2026-09-18** (SM100/SM103 NVFP4 paged-KV sparse decode+prefill, prefill needs CUDA 13.0+) [src](https://github.com/flashinfer-ai/flashinfer/pull/4982). Follow-on question: vLLM's `FLASHINFER_MLA_SPARSE_DSV41` SM12x branch still demands an FP8 dtype, so #4955's kernel is not yet reachable from vLLM — track when that gate opens.
17. SGLang's DSA sub-backend table is documented for **V3.2**. Do the same sub-backends and defaults apply to V4.1's CSA2 (three static modes, hierarchical indexer, per-layer compress ratios 0/1/2)?
18. No per-GPU latency data for the indexer / top-k kernel. SGLang's "about 15 µs" figure carries no GPU or batch-size label.
19. Is there any FP4-compressed-KV path for V4.1 outside `FLASHMLA_MEGA_ATTN_DSV41` on sm_10x? If not, the 890 B/token figure is a Blackwell-DC-only property and every other GPU's KV budget must be recomputed. **Half-answered 2026-09-19 (sweep)**: the recomputation figure now exists — the same layout at FP8 is **1,650 B/token** and at BF16 **3,200 B/token** (§11, [architecture.md §5.2](../models/deepseek41f/architecture.md)), so H100/H200, RTX PRO 6000 and MI355X should be budgeted at 1,650 B/token, not 890. The kernel question itself (FlashInfer PR #4955's SM120 NVFP4 path, unreachable from vLLM's dtype gate) stays open.

**Linear attention**
20. FlashInfer fused KDA decode is gated on `(10,0)` or `(10,3)` exactly — not the 10.x family. Confirm this is deliberate (it excludes any future 10.x part).
21. The 1.33×/1.13× KDA figure is FlashInfer-vs-vLLM-native. What is the gap vs the **Triton** path that A100 and MI355X are stuck with?
22. No GDN or KDA kernel numbers on AMD at all.

**AMD**
23. All four gfx950 attention PRs (#5403, #5376, #5577, #5556) were **open** on 2026-09-19. Re-check merge status before planning on 497/509/676 TFLOPS. ⚠️ **And note the part mix-up corrected 2026-09-19**: #5403's 497/509/436 TFLOPS are measured on **MI350X**, only #5376's 676 TFLOPS is MI355X. MI350X's own dense BF16 peak was never obtained, so those three numbers currently have no valid denominator.
24. AMD's own MLA-decode micro-benchmark issue (#2493, open since 2026-03-26) admits earlier MI355X MLA numbers were measured with SDPA instead of the MLA kernel. **There is no trustworthy published MI355X MLA decode number.**
25. AITER's FP8 FlashAttention Triton kernel ships with *"The USE_FP8=True path is not yet tested."*
26. AITER lists MI355X as *"Supported"* vs MI300X/MI325X *"Fully supported"* — enumerate what is actually missing on CDNA4.

**TensorRT-LLM**
27. The `gpt-attention` doc's *"FP8 Context FMHA … only supported on Ada and Hopper"* predates Blackwell; the trtllm-gen FMHA generation clearly supports SM100/SM107 via FlashInfer. Find the current per-architecture TRT-LLM FMHA matrix.
28. The PyTorch-flow attention doc has no MLA section and no architecture matrix at all.

**Models**
29. Marlin-2B is gated; the §16.4 table is the **base Qwen3.5-2B** config. Its video/temporal stack may add attention layers with different head dims.
30. Kimi-K3's MLA layers: does vLLM route them through the public MLA backend registry, or through a model-private backend under `vllm/models/kimi_k3/nvidia/`?
31. Kimi-K3 config has `num_nextn_predict_layers: 0` while the model card describes MTP-style deployment — confirm whether speculative decoding is used, since `num_spec != 0` disables **both** fused KDA decode kernels.

**Cross-cutting**
32. No FP8-attention-vs-BF16-attention speedup published on any GPU in this roster.
33. No like-for-like MI355X ↔ B200/B300 attention comparison exists anywhere found.

---

## Sources

- https://github.com/Dao-AILab/flash-attention
- https://github.com/Dao-AILab/flash-attention/blob/main/flash_attn/cute/interface.py
- https://github.com/Dao-AILab/flash-attention/blob/main/tests/cute/test_flash_attn.py
- https://github.com/Dao-AILab/flash-attention/issues/2376
- https://github.com/Dao-AILab/flash-attention/releases
- https://github.com/Dao-AILab/flash-attention/pull/2696
- https://arxiv.org/html/2603.05451v1
- https://lambda.ai/blog/flashattention-4-gives-the-nvidia-blackwell-platform-its-most-optimized-attention-kernel-yet
- https://github.com/deepseek-ai/FlashMLA
- https://github.com/flashinfer-ai/flashinfer
- https://flashinfer.ai/releases/
- https://github.com/flashinfer-ai/flashinfer/releases
- https://github.com/flashinfer-ai/flashinfer/releases/tag/v0.6.15
- https://github.com/flashinfer-ai/flashinfer/releases/tag/v0.6.18
- https://github.com/flashinfer-ai/flashinfer/pull/4955
- https://github.com/flashinfer-ai/flashinfer/pull/4982
- https://docs.vllm.ai/en/latest/design/attention_backends/
- https://github.com/vllm-project/vllm/blob/main/docs/design/attention_backends.md
- https://github.com/vllm-project/vllm/blob/main/vllm/v1/attention/backends/registry.py
- https://github.com/vllm-project/vllm/blob/main/vllm/v1/attention/backends/fa_utils.py
- https://github.com/vllm-project/vllm/blob/main/vllm/v1/attention/backends/flash_attn.py
- https://github.com/vllm-project/vllm/blob/main/vllm/v1/attention/backends/gdn_attn.py
- https://github.com/vllm-project/vllm/blob/main/vllm/v1/attention/backends/linear_attn.py
- https://github.com/vllm-project/vllm/blob/main/vllm/vllm_flash_attn/flash_attn_interface.py
- https://github.com/vllm-project/vllm/blob/main/vllm/platforms/interface.py
- https://github.com/vllm-project/vllm/blob/main/vllm/models/deepseek_v41/sparse_mla.py
- https://github.com/vllm-project/vllm/blob/main/vllm/models/deepseek_v41/nvidia/flashinfer_sparse.py
- https://github.com/vllm-project/vllm/blob/main/vllm/models/deepseek_v41/nvidia/flash_mla_mega_attn.py
- https://github.com/vllm-project/vllm/blob/main/vllm/models/kimi_k3/__init__.py
- https://github.com/vllm-project/vllm/blob/main/vllm/models/kimi_k3/nvidia/kda.py
- https://github.com/vllm-project/vllm/blob/main/vllm/model_executor/layers/mamba/gdn/qwen_gdn_linear_attn.py
- https://github.com/vllm-project/vllm/issues/56564
- https://github.com/vllm-project/vllm/issues/54059
- https://github.com/vllm-project/vllm/issues/57149
- https://docs.sglang.io/advanced_features/attention_backend.html
- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash
- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash
- https://recipes.vllm.ai/moonshotai/Kimi-K3
- https://recipes.vllm.ai/Qwen/Qwen3.8-27B
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash
- https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4
- https://huggingface.co/moonshotai/Kimi-K3
- https://huggingface.co/Qwen/Qwen3.8-27B
- https://huggingface.co/Qwen/Qwen3.5-2B
- https://github.com/ROCm/aiter
- https://github.com/ROCm/aiter/pull/5403
- https://github.com/ROCm/aiter/pull/5376
- https://github.com/ROCm/aiter/pull/5577
- https://github.com/ROCm/aiter/pull/5556
- https://github.com/ROCm/aiter/pull/5618
- https://github.com/ROCm/aiter/issues/2493
- https://github.com/ROCm/aiter/issues/3442
- https://nvidia.github.io/TensorRT-LLM/advanced/gpt-attention.html
- https://nvidia.github.io/TensorRT-LLM/torch/attention.html
- https://www.nvidia.com/en-us/data-center/gb300-nvl72/
- https://www.nvidia.com/en-us/data-center/h200/
- https://www.lmsys.org/blog/2026-04-25-deepseek-v4/
- https://flopper.io/gpu/amd-instinct-mi355x-oam
- https://www.amd.com/content/dam/amd/en/documents/instinct-tech-docs/product-briefs/amd-instinct-mi355x-gpu-brochure.pdf (fetch timed out again 2026-09-19; not opened for any number)
- https://rocm.docs.amd.com/en/latest/reference/gpu-arch-specs.html — opened 2026-09-19: carries **only** VRAM 288 GiB / CDNA4 / gfx950, **no FLOPS row**; it is not a valid source for the MI355X peak
- https://www.nvidia.com/en-us/data-center/a100/
- https://www.nvidia.com/en-us/data-center/hgx/
- https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000/ (Workstation Edition — 1,792 GB/s; **not** the roster part)
- https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/ (Server Edition — 96 GB GDDR7, **1,597 GB/s**, 1/2/4 PFLOPS sparse)
- https://lenovopress.lenovo.com/lp2263.pdf
- https://lightseek.org/tokenspeed/recipes/models
- https://arxiv.org/abs/2603.05451
- https://raw.githubusercontent.com/Dao-AILab/flash-attention/main/flash_attn/cute/interface.py
- https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/vllm_flash_attn/flash_attn_interface.py

---

## Verification log (2026-09-19)

Adversarial re-check. Every claim below was re-sourced by opening a primary document
independently of the citation already in the text; every derivation was recomputed with
`python3` from `research/METHODOLOGY.md` and the local `config.json` files. **42 claims
checked: 29 CONFIRMED, 10 CORRECTED, 3 UNVERIFIABLE.**

### Hardware peaks and datasheets

| # | Claim | Verdict | Source opened |
|---|---|---|---|
| 1 | A100 SXM 80GB dense BF16 = 312 TFLOPS | **CONFIRMED** — page states "312 TFLOPS \| 624 TFLOPS\*", "\* With sparsity". The ⚠️ marker was unnecessary and is removed. | https://www.nvidia.com/en-us/data-center/a100/ |
| 2 | H100/H200/H800 SXM dense BF16 = 989.5 (÷2 of 1,979 sparse) | **CONFIRMED** — H200 SXM FP16/BF16 = "1,979 TFLOPS", with-sparsity footnote present | https://www.nvidia.com/en-us/data-center/h200/ |
| 3 | H200 = 141 GB @ 4.8 TB/s, up to 700 W SXM | **CONFIRMED** — "141GB", "4.8TB/s", "Up to 700W (configurable)". (H200 NVL is 600 W / 1,671 TFLOPS sparse — do not mix.) | https://www.nvidia.com/en-us/data-center/h200/ |
| 4 | B200 SXM dense BF16 = 2,250 | **CONFIRMED** — HGX B200 FP16/BF16 = 36 PFLOPS for 8 GPUs, "Specification in Sparse. Dense is ½ sparse spec shown" → 36000/8/2 = 2,250. ⚠️ marker removed. | https://www.nvidia.com/en-us/data-center/hgx/ |
| 5 | B300 dense BF16 = 2,500 | **CORRECTED** 2,500 (all B300) → **2,250 for HGX B300, 2,500 only for a B300 inside GB300 NVL72**. HGX B300 FP16/BF16 = 36 PFLOPS sparse / 8 GPUs, identical to HGX B200. Cascades into §2.5, §5.2, §9.4 and §15.1, all amended. | https://www.nvidia.com/en-us/data-center/hgx/ |
| 6 | GB300 NVL72 rack: FP8/FP6 720 PF, BF16 360 PF, TF32 180 PF, INT8 24 POPS, 20 TB @ 576 TB/s, NVLink 130 TB/s | **CONFIRMED** verbatim; also 72 GPUs + 36 Grace CPUs, 37 TB fast memory, 17 TB LPDDR5X @ 14 TB/s (added) | https://www.nvidia.com/en-us/data-center/gb300-nvl72/ |
| 7 | "second FP4 figure next to 1,440 rendered ambiguously; do not treat either as dense" | **CORRECTED** — resolved, not ambiguous: **1,440 PFLOPS with sparsity, 1,080 PFLOPS "Without sparsity"** → 15 PFLOPS dense FP4/GPU. Cross-checked against HGX B300's explicit "144 PFLOPS \| 108 PFLOPS" → 13.5 dense/GPU, the same 1.111× board-power ratio as 2,250 vs 2,500. Open question 5 closed. | https://www.nvidia.com/en-us/data-center/gb300-nvl72/ + https://www.nvidia.com/en-us/data-center/hgx/ |
| 8 | "RTX PRO 6000 NVIDIA product page 404'd on fetch; no dense BF16 figure" | **CORRECTED** — the page loads normally; the 404 claim was wrong. It gives 4000 AI TOPS (FP4, sparse), 125 TFLOPS FP32, 96 GB GDDR7, 1,792 GB/s, 600 W. Dense BF16 `est.` = 4000/2/4 = **500 TFLOPS**, cross-checked by 4 × FP32 = 500. Still `est.` (no published dense BF16 tensor figure), so ⚠️ retained on the derivation. | https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000/ |
| 9 | MI355X dense BF16 ≈ 2,510 | **UNVERIFIABLE** — the cited flopper.io page now returns **HTTP 403**; `amd.com/.../mi355x.html`, `.../mi350.html` and `.../mi300/mi355x.html` each **timed out** (3 attempts); ROCm's `gpu-arch-specs` page carries only 288 GiB and 256 CU, no FLOPS. The number has no openable source as of this pass. Marker strengthened in §1.1 and Open question 4. | flopper.io (403), amd.com (timeout), https://rocm.docs.amd.com/en/latest/reference/gpu-arch-specs.html |
| 10 | H800 SXM5 HBM bandwidth 3.35 TB/s (denominator for "~89%") | **UNVERIFIABLE** — NVIDIA publishes no H800 page; TechPowerUp returned **HTTP 403**. Existing ⚠️ in §5.2 is correct and stays. | techpowerup.com (403) |

### FlashAttention

| # | Claim | Verdict | Source opened |
|---|---|---|---|
| 11 | arXiv 2603.05451 is a real FA4 paper | **CONFIRMED** — "FlashAttention-4: Algorithm and Kernel Pipelining Co-Design for Asymmetric Hardware Scaling", Zadouri, Hoehnerbach, Shah, Liu, Thakkar, Dao; submitted 2026-03-05 | https://arxiv.org/abs/2603.05451 |
| 12 | FA4 = 1,613 TFLOPS fwd BF16 on B200 at "71% hardware utilization" | **CONFIRMED** in both sources; Lambda specifies **HGX B200**, which is the part whose 2,250 dense peak the 71.7% is computed against — the denominator is right | https://arxiv.org/html/2603.05451v1 + https://lambda.ai/blog/flashattention-4-gives-the-nvidia-blackwell-platform-its-most-optimized-attention-kernel-yet |
| 13 | FA4 vs cuDNN 9.13 = 1.1–1.3× | **CORRECTED** → "**up to 1.3×**". Both sources state only an upper bound. The paper additionally notes cuDNN absorbed FA4 techniques from v9.13–9.14, so newer cuDNN is comparable — a material caveat that was missing. | https://arxiv.org/html/2603.05451v1 |
| 14 | FA4 vs Triton = 2.1–2.7× on B200 | **CORRECTED** → "**up to 2.7×**"; the 2.1× lower bound appears in neither the paper nor the Lambda post. §7's "35–50% of the hand-written kernel" inherits that unverified lower bound — flagged, text left intact. | https://arxiv.org/html/2603.05451v1 |
| 15 | FA4 custom variants vs Triton, GB200 NVL72: fwd 1.2–3.2×, bwd 1.85–3× | **CORRECTED** — fwd range confirmed (dense/causal 1.6–3.2, ALiBi 1.2–2.1, doc-mask ≤2.7, SWA 1.4–2.1); **bwd is 1.8–3×, not 1.85–3×** (sliding-window backward is 1.8–2.2×). Per-variant breakdown added. | https://lambda.ai/blog/flashattention-4-gives-the-nvidia-blackwell-platform-its-most-optimized-attention-kernel-yet |
| 16 | FA3 = ~740 TFLOPS fwd BF16 on H100 at "75% utilization" | **CONFIRMED** verbatim | https://lambda.ai/blog/flashattention-4-gives-the-nvidia-blackwell-platform-its-most-optimized-attention-kernel-yet |
| 17 | FA4 dispatch gates in `flash_attn/cute/interface.py` — fwd `arch // 10 in [8,9,10,11,12]`, bwd `[9,10,11,12]`, FP8 `assert arch // 10 == 10` with the quoted message, hd256 2-CTA condition, MLA-fwd Blackwell/Thor-only, learnable-sink bwd `[9,10,11]`, SM120 no block-sparsity / no paged KV, SM80+SM120 = 128 threads, `_validate_head_dims` per-arch pairs, `FLASH_ATTENTION_ARCH` override | **CONFIRMED** — every gate and every quoted string matches the file on `main`. (One wording nuance: the SplitKV restriction reads `assert num_splits == 1, "SM120 forward only supports num_splits=1"` rather than the quoted `is_split_kv` form; same restriction.) | https://raw.githubusercontent.com/Dao-AILab/flash-attention/main/flash_attn/cute/interface.py |
| 18 | vLLM's bundled gate: FA2 ≥ cc 8.0, FA3 family 90, FA4 family 90/100/110 with the message *"FA4 is only supported on devices with compute capability 9.x, 10.x, or 11.x"* | **CONFIRMED** — all three conditions and all three message strings match verbatim. The §9.5 conclusion (RTX PRO 6000 falls back to FA2) stands. | https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/vllm_flash_attn/flash_attn_interface.py |

### Engines

| # | Claim | Verdict | Source opened |
|---|---|---|---|
| 19 | vLLM backend table: FA2 ≥8.0 / FA3 9.x / FA4 ≥10.0, KV dtypes and sink columns as printed | **CONFIRMED** row for row | https://docs.vllm.ai/en/latest/design/attention_backends/ |
| 20 | vLLM FLASHINFER rows: Native 8.x–9.x, XQA 9.0, trtllm-gen 10.x with `nvfp4`; Native/XQA carry `nvfp4_4over6` | **CONFIRMED** — including that 12.x has **no** FlashInfer standard-attention row (open question 14 stands as a genuine gap) | https://docs.vllm.ai/en/latest/design/attention_backends/ |
| 21 | *"Default is FA4 on SM100+ (Blackwell), FA3 on SM90 (Hopper), FA2 otherwise"* and the hd256/block-128 rule | **CONFIRMED** verbatim, including that FA4 hd256 on Blackwell "does not support logit soft capping, attention sinks, mm_prefix/R-SWA masking, DCP, or windowed encoder attention" | https://docs.vllm.ai/en/latest/design/attention_backends/ |
| 22 | §3.2 row `FLASHINFER_MLA_SPARSE_DSV41` | **CORRECTED** — the row had **one cell too many** (a stray leading `bf16`), which shifted every column: it was rendering the KV-dtype list in the "Block sizes" column. Fixed to 10.x/12.x, block 128, head 512, sink ✅, DCP ❌, matching the published table. | https://docs.vllm.ai/en/latest/design/attention_backends/ |
| 23 | FlashMLA support matrix (5 rows) + footnote *"Sparse Decoding for DeepSeek V4.1 is only available on SM100"* | **CONFIRMED** verbatim, including "no gfx9xx / SM120 / SM80 row". The §16.1 conflict with vLLM's `major in [9, 10]` gate is real and open question 15 stands. | https://github.com/deepseek-ai/FlashMLA |
| 24 | FlashMLA requirements: SM90/SM100, CUDA ≥ 12.8 (12.9+ for SM100) | **CONFIRMED** verbatim (PyTorch ≥ 2.0 also stated) | https://github.com/deepseek-ai/FlashMLA |
| 25 | FlashMLA perf: 3,000 GB/s and 660 TFLOPS dense decode (H800 SXM5, CUDA 12.8); 410 sparse decode; 640 sparse prefill; 700 sparse decode B200; 1,450 sparse prefill B200 CUDA 12.9; 1,460 / 1,000 MHA prefill fwd/bwd B200; 1,430 / 670 fused | **CONFIRMED** — all ten numbers, all GPU labels and both CUDA versions match | https://github.com/deepseek-ai/FlashMLA |
| 26 | MQA mode `head_dim_k` 576 (V3/V3.1/V3.2) or 512 (V4/V4.1) with `head_dim_v` 512; MHA mode 192/128 → 128; V4.1 kernels released 2026-09-10 | **CONFIRMED** verbatim | https://github.com/deepseek-ai/FlashMLA |
| 27 | FlashInfer supports SM75+ "through Blackwell"; A100 8.0, H100/H200 9.0, B200 10.0, B300 10.3, Jetson Thor 11.0, RTX 50 / DGX Spark 12.0/12.1 | **CONFIRMED** — the whole §1 compute-capability table, including that SM110 is Jetson Thor and not a datacenter part | https://github.com/flashinfer-ai/flashinfer |
| 28 | FlashInfer v0.6.18 (2026-08-29): Rubin SM107 completion, FP8 HCA decode for **SM100/SM103**, `flashinfer.top_k_varlen`, `fused_kda_decode` at **1.33× vLLM fused, geomean 1.13×**, W4A16 extending to B200 and B300 | **CONFIRMED** — every quoted phrase and both KDA numbers match the release notes | https://github.com/flashinfer-ai/flashinfer/releases/tag/v0.6.18 |
| 29 | FlashInfer PR #4955 — "merge status unconfirmed" | **CORRECTED** → **merged 2026-09-07**, SM120/**SM121**, NVFP4 KV opt-in over the default FP8, streaming prefill + grouped split-K decode; measured on an **RTX PRO 5000 Blackwell** (not a 6000): prefill 1.41–1.67×, decode 1.31×, ~9% end-to-end; config limited to 16/32/64/128 query heads, top-k 128 or 512, page size 64. This is the first attention perf data point on any RTX PRO Blackwell part in the document. | https://github.com/flashinfer-ai/flashinfer/pull/4955 |
| 30 | FlashInfer PR #4982 — "merge status unconfirmed" | **CORRECTED** → **merged 2026-09-18**, SM100/SM103, NVFP4 KV as packed uint8 with e4m3 block scales, decode + prefill without dense materialization, `out=` added to the decode API, **prefill requires CUDA 13.0+** | https://github.com/flashinfer-ai/flashinfer/pull/4982 |
| 31 | SGLang matrix: FA4 = native page 128, **FP8 KV ❌, FP4 KV ✅**; FA3 FP8 ✅/FP4 ❌; TRTLLM MHA 16/32/64; AITER FP4 ❌; Wave all ❌ | **CONFIRMED** row for row — the vLLM/SGLang inversion in §2.4 is real and open question 12 stands | https://docs.sglang.io/advanced_features/attention_backend.html |
| 32 | SGLang defaults (Hopper → `fa3` on CUDA 12.3+; Blackwell → `trtllm_mha`; else `flashinfer` → `triton`) and *"TRTLLM XQA backend only works well for pagesize 64"* | **CONFIRMED** verbatim | https://docs.sglang.io/advanced_features/attention_backend.html |
| 33 | AITER PR #5403: 497 / 509 / 436 TFLOPS on **MI355X**, open | **CORRECTED** → the PR body names **MI350X** for the b=8, nh=18, s=5121, hd=256, bf16 run. State (open) and the limitation list (THD/varlen only, MHA-only via `nhead_q == nhead_k`, no dense BSHD, dropout, alibi, bias, sink, `logits_soft_cap`, no FP16) all confirmed. The 1.14×/1.17× *ratios* survive (same part both sides); the MI355X *attribution* and every %-of-MI355X-peak derived from them do not. | https://github.com/ROCm/aiter/pull/5403 |
| 34 | AITER PR #5376: 676 TFLOPS on MI355X, open, non-causal only, BF16, MHA-only, a16 atomics | **CONFIRMED** — MI355X is correct here; adds b=16, s=2560 and a claimed 3.3× over baseline | https://github.com/ROCm/aiter/pull/5376 |

### Models

| # | Claim | Verdict | Source opened |
|---|---|---|---|
| 35 | DeepSeek-V4.1-Flash: CSA2 with Full/Reindex/Reuse modes, Hierarchical Sparse Indexer, FP4 main KV in E2M1 with one E4M3 scale per 16 channels, **890 bytes/token** global KV | **CONFIRMED** verbatim on the model card; also 552B total / 8B prefill-active / 16B decode-active, 1 shared + 384 routed experts, 6 activated | https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash |
| 36 | "Causal Encoder-Decoder: 40 layers = 20 encoder + 20 decoder", decoder global KV projected from final encoder hidden states | **CONFIRMED** on the model card (it is *not* in `config.json`, which only has `num_hidden_layers: 40` — the split is card-only) | https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash |
| 37 | 890 B/token reproduced from `config.json` under METHODOLOGY §2 | ~~**UNVERIFIABLE**~~ — recomputed: the four `kv_source_layer_ids` (2, 8, 14, 20) at `head_dim` 512 × 0.5625 B/element (FP4 + one E4M3 per 16), with `compress_ratios` 2/2/2/1, give 144+144+144+288 = **720 B/token**. The residual ~170 B/token is unexplained anywhere in this repo. Flagged inline in §11. **→ SUPERSEDED 2026-09-19 (sweep): CONFIRMED.** That derivation counted only the *main* KV; each cache-owning layer also owns an **indexer K cache** (128-d, MXFP4 E2M1 + one E8M0 per 32 = 68 B/entry → **170 B/token**). 720 + 170 = **890**, byte-exact. See [models/deepseek41f/architecture.md §5.1](../models/deepseek41f/architecture.md) and the corrected §11. | recomputed from `research/models/deepseek41f/config.json`; `research/models/deepseek41f/architecture.md` §5.1 |
| 38 | `deepseek41f` config values: `head_dim` 512, 64 heads, 1 KV head, `qk_rope_head_dim` 64, `sliding_window` 128, `index_topk` 512 / `index_n_heads` 32 / `index_head_dim` 128, `kv_source_layer_ids` [2,8,14,20], 8 `index_source_layer_ids`, candidate 2048/8/layer 20, `num_nextn_predict_layers` 3, `dspark_block_size` 5, targets [37,38,39] | **CONFIRMED** — all 14 fields match the local file exactly. (448 NoPE + 64 RoPE = 512 also checks out.) | `research/models/deepseek41f/config.json` |
| 39 | `compress_ratios`: 43 entries = 20×1, 18×2, 5×0; of the first 40, 20×1, 18×2, 2×0 | **CONFIRMED** — recounted in `python3`: `{0: 5, 1: 20, 2: 18}` over 43, `{0: 2, 1: 20, 2: 18}` over the first 40 | recomputed from `research/models/deepseek41f/config.json` |
| 40 | Kimi-K3: 93 layers = **69 KDA + 24 Gated MLA**; `full_attn_layers` every 4th plus 92/93; MLA 96/96 heads, `kv_lora_rank` 512, 128/64/128, `q_lora_rank` 1536; KDA 96 heads, head_dim 128, conv 4, full-rank gate; `mxfp4-pack-quantized` group 32 with `ignore` covering `self_attn`/`shared_experts`; `num_nextn_predict_layers: 0`; vision `flash_attention_2` | **CONFIRMED** — counted `len(kda_layers) == 69`, `len(full_attn_layers) == 24`, sum 93; every geometry field matches; the `ignore` list also excludes `mlp.(gate\|up\|gate_up\|down)_proj`, `lm_head`, `vision_tower`, `mm_projector`, so MXFP4 is **routed-experts-only**, slightly narrower than "MoE-only" | `research/models/kimik3/config.json` |
| 41 | Qwen3.8-27B: 64 layers = **48 linear + 16 full**; 24/4 heads; `head_dim` 256; `partial_rotary_factor` 0.25; GDN 16 key / 48 value heads, 128/128 dims, conv 4, `mamba_ssm_dtype` float32; 262,144 ctx; `mtp_num_hidden_layers` 1 | **CONFIRMED** — `Counter(layer_types) == {'linear_attention': 48, 'full_attention': 16}`; all other fields match | `research/models/qwen3827b/config.json` |
| 42 | Every "% of dense BF16 peak" and every cross-GPU ratio in §2.5, §5.2, §8.3, §9.x and §15 | **CONFIRMED** — all 15 percentages and all 9 ratios recomputed in `python3` and agree to the printed precision (71.7, 74.8, 66.7, 41.4, 64.7, 64.4, 31.1, 64.9, 44.4, 63.6, 29.8, 19.8, 20.3, 17.4, 26.9; 2.18, 2.27, 0.96, 2.27, 1.71, 0.75, 1.14, 1.17, 0.59; and the 1.111× scalings → 1,792 / 1,611 / 778 / 2.42). **The arithmetic is sound throughout; where numbers are wrong it is the inputs, not the derivations.** | recomputed per `research/METHODOLOGY.md` §1–§4 |

### What changed in this document

Ten edits: §1.1 peak table (A100 and B200 ⚠️ cleared with sources; HGX B300 split out at 2,250;
RTX PRO 6000 given a sourced ~500 `est.`; MI355X marker strengthened), §1.1 GB300 FP4
footnote resolved, §2.5 speedup ranges tightened to what the sources actually say,
§2.5/§5.2/§9.4/§15.1 B300 scaling split into HGX vs NVL72, §3.1 both FlashInfer PRs marked
merged with dates, §3.2 misaligned table row repaired, §8.3/§9.6/§15.3 MI350X-vs-MI355X
attribution corrected, §9.5 and §11 amended for the merged SM120 NVFP4 kernel, §11 given
the 720-vs-890 B/token gap, and Open questions 1/2/3/5/16/23 updated with resolutions.
Nothing was deleted and no prose was restyled.

---

## Sweep log (2026-09-19)

Systemic correction pass against the amended `research/METHODOLOGY.md` (§1 units, §2 state
slots, §3 consistency, §6 scenarios, §8 pinned inputs). Format: **section · old → new ·
reason · source**. Nothing was deleted, no prose was restyled, and no correction made by the
earlier fact-checker was undone.

| # | Section | Old → New | Reason | Source |
|---|---|---|---|---|
| 1 | §1.1 peak table, RTX PRO 6000 Server Edition | dense **480 / 960 / 1,920** → **500 / 1,000 / 2,000** `est.` | The cell's own derivation says "NVIDIA prints BF16 1 PFLOPS / FP8 2 PFLOPS / FP4 4 PFLOPS … dense = ½", which is 500 / 1,000 / 2,000, not 480 / 960 / 1,920 (those were an unlabelled clock-derived estimate). METHODOLOGY §8 pins RTX PRO 6000 FP4 at **≈ 2,000 dense (4,000 sparse)**. Recomputed in `python3`: 1000/2, 2000/2, 4000/2. | [Server Edition page](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/), fetched this pass |
| 2 | §1.1 peak table, RTX PRO 6000 Server Edition | no bandwidth stated, citation pointed at a malformed `…-server-edition.md` URL → **96 GB GDDR7, 1,597 GB/s** stated and the URL fixed | Checklist item 8: 1,597 GB/s is the **Server** Edition; **1,792 GB/s is the Workstation Edition** and must never be quoted for the roster part. Both figures now labelled by SKU. | [Server Edition page](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/); [Workstation page](https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000/) |
| 3 | §1.1 peak table, MI355X | "2,500 … from AMD's ROCm CDNA4 architecture page" → **2,500 pinned, citation corrected to ⚠️ second-hand** | Citation-integrity check (checklist 13): the ROCm page was opened and carries **only** "VRAM (GiB): 288 / CDNA4 / gfx950" — **no FLOPS row**, so it cannot source the peak. This matches the document's own verification-log entry #9 and contradicts METHODOLOGY §8's "peaks confirmed via ROCm CDNA4 page"; the disagreement is now stated in-doc, per METHODOLOGY §8's "state the disagreement" rule. Value unchanged (2,500), all MI355X percentages unchanged (19.9 / 20.4 / 17.4 / 27.0, re-verified in `python3`). | [ROCm gpu-arch-specs](https://rocm.docs.amd.com/en/latest/reference/gpu-arch-specs.html), opened 2026-09-19; AMD MI355X datasheet content (BF16 2.5 PFLOPS dense) corroborates but `amd.com` timed out again |
| 4 | §11 (FP8 / NVFP4 KV-cache attention) | "⚠️ the 890 B/token figure … is **not** reproduced … 720 B/token … Someone must close that 720-vs-890 gap" → **full byte-exact derivation, 720 main + 170 indexer = 890** | Checklist item 5 / finding (a). The old derivation counted only the main `compress_kv_cache` and dropped the per-layer **indexer K cache** (128-d, MXFP4 E2M1 + one E8M0 per 32 = 68 B/entry → 170 B/token). Recomputed in `python3`: 720 + 170 = 890 exactly. Also records the FP8 (1,650 B/token) and BF16 (3,200 B/token) equivalents and notes that 890 is the **FP4-KV (Blackwell-kernel) figure**, and that the fixed SWA state is separate (2.77 MiB FP8 / 5.38 MiB BF16 ⚠️). | [models/deepseek41f/architecture.md §5.1–§5.3](../models/deepseek41f/architecture.md); `research/models/deepseek41f/config.json` |
| 5 | Verification log row #37 | "**UNVERIFIABLE** (720 B/token)" → struck and marked **SUPERSEDED → CONFIRMED** with the indexer term | Same as #4; the row is annotated, not rewritten, so the original fact-check remains readable. | as #4 |
| 6 | Open question 19 | unchanged question → **half-answered**: non-sm_10x GPUs budget **1,650 B/token** (FP8), not 890 | Follows from #4: the "recompute every other GPU's KV budget" half now has a number; the kernel half (FlashInfer #4955 unreachable from vLLM's dtype gate) stays open. | [architecture.md §5.2](../models/deepseek41f/architecture.md) |
| 7 | §16.1 property table, "KV storage" row | "FP4 E2M1 … → 890 bytes/token global" → adds "**= 720 main + 170 indexer**, byte-exact" + FP8/BF16 equivalents + "890 **is the FP4-KV figure**" | Same correction propagated to where the pair docs will read it. | as #4 |
| 8 | §16.1 (new paragraph) | silent on TokenSpeed → **TokenSpeed ships a DeepSeek-V4.1-Flash recipe** with its own **FlatKV** attention backend (four-group KV cache, replayable SWA/compressor groups, `--disable-prefill-graph` for the CED decoder, per-commit GB300 Slurm 1P1D CI) | Checklist item 10. An attention-kernel document that lists vLLM, SGLang and TRT-LLM backends for this model but omits the third engine's *own* attention backend is incomplete. | [cross-cutting/inference-engines.md §2.5](inference-engines.md) (corrected); [lightseek.org TokenSpeed recipes](https://lightseek.org/tokenspeed/recipes/models) |
| 9 | §16.1 (new paragraph) | silent on the NVFP4 variant's kernel path → explicit: **NVFP4 re-quantizes routed experts only**, attention tensors and KV layout untouched, every backend/block-size/KV-dtype row applies unchanged, **no published attention speedup**, checkpoint is **larger** (527.27 GB vs 510.29 GB) | Checklist item 14 — pre-empts the common wrong inference that "NVFP4 build" implies a faster attention path. | METHODOLOGY §8; [models/deepseek41fnvfp4/architecture.md](../models/deepseek41fnvfp4/architecture.md) |
| 10 | §16.2 (Kimi-K3) | prose sentence "KDA decode backend per GPU: A100 → Triton; …" → **per-GPU kernel-path table** covering Gated-MLA prefill/decode, KDA decode and the vision tower, plus the two mandatory flags | Finding (d): the `research/models/kimik3/<gpu>.md` pair docs need one citable row per GPU covering **both** kernel stacks, not just KDA. Content is re-stated from §9, §14.2 and §14.4 — no new claims. | §9.1–§9.6, §14.2, §14.4; [recipes.vllm.ai/moonshotai/Kimi-K3](https://recipes.vllm.ai/moonshotai/Kimi-K3) |
| 11 | §16.4 (Marlin-2B) | no per-GPU kernel path at all → **per-GPU kernel-path table** (full-attention hd256 + GDN + vision), each row carrying the existing ⚠️ that the config is the base Qwen3.5-2B | Finding (d). Marlin-2B was the only repo model with no kernel-path row; the ⚠️ is attached to every cell rather than being dropped. | §16.3, §14.3; [huggingface.co/Qwen/Qwen3.5-2B](https://huggingface.co/Qwen/Qwen3.5-2B) |
| 12 | Open question 3 | "PARTLY RESOLVED … 1,792 GB/s" → resolved for the **Server** Edition (1,597 GB/s, 500/1,000/2,000 dense), with an explicit "never quote 1,792 for the Server Edition" | Checklist item 8; the question was resolved against the wrong SKU's page. | as #2 |
| 13 | Open question 4 | "no openable source at all … largest unresolved hole" → **2,500 pinned**, citation defect named, METHODOLOGY §8 disagreement recorded | as #3 | as #3 |
| 14 | Sources | added the Server Edition page, the Lenovo press datasheet and the TokenSpeed recipes page; labelled the Workstation page as **not** the roster part; annotated the ROCm page as carrying no FLOPS; de-duplicated the ROCm entry | Keeps the source list honest about which page supports which number. | — |

### Checked and found already correct (no edit)

- **Checklist 2 (capacities as deployed)** — the document quotes no per-GPU HBM capacity that conflicts with METHODOLOGY §8; H200 141 GB and RTX PRO 6000 96 GB are right, and the only "20 TB / 72 GPUs" figure is a verbatim NVL72 rack quote.
- **Checklist 3 (dense FLOPS)** — §1.1 re-verified line by line against [nvidia.com/hgx](https://www.nvidia.com/en-us/data-center/hgx/) this pass: HGX B200 FP4 "144 PFLOPS | 72 PFLOPS" → 9,000 dense; **HGX B300** FP4 "144 PFLOPS | 108 PFLOPS" → 13,500 dense with FP16/BF16 36 PFLOPS sparse → **2,250 dense, identical to B200**; GB300 NVL72 2,500 / 5,000 / 15,000. A100 312, H100/H200 989.5 / 1,979, MI355X 2,500 / 5,000 / 10,100 all match METHODOLOGY §8.
- **Checklist 3 / finding (b)** — the HGX-B300-vs-GB300-NVL72 split was already propagated by the earlier fact-checker into §2.5, §5.2, §9.4 and §15.1; re-checked, no single-"B300"-peak percentage or ratio remains.
- **Finding (c)** — MI355X percentages were already on the 2,500 denominator; recomputed in `python3` (497→19.9 %, 509→20.4 %, 436→17.4 %, 676→27.0 %), all agree to the printed precision.
- **Checklist 12 (release versions/dates)** — all engine releases named here re-verified via the GitHub API: FlashInfer `v0.6.14` 2026-07-02, `v0.6.15` 2026-07-17, `v0.6.16` 2026-07-31, `v0.6.17` 2026-08-11, `v0.6.18` 2026-08-29, `v0.6.18.post1` 2026-09-05, `v0.7.0rc3` 2026-09-16; FlashAttention `fa4-v4.0.0.beta31` 2026-09-16. **No off-by-one**; every date in §3, §3.1 and §2.2 is exact.
- **Checklist 13 (citation integrity, five most load-bearing)** — (1) [nvidia.com/hgx](https://www.nvidia.com/en-us/data-center/hgx/) → HGX B300 BF16 2,250 dense **CONFIRMED**; (2) [FlashMLA README](https://github.com/deepseek-ai/FlashMLA) → the five-row support matrix and all ten performance numbers **CONFIRMED** verbatim; (3) [vLLM attention backends](https://docs.vllm.ai/en/latest/design/attention_backends/) → FA2 ≥8.0 / FA3 9.x / FA4 ≥10.0, FlashInfer Native 8.x–9.x / XQA 9.0 / trtllm-gen 10.x, `FLASHINFER_MLA_SPARSE_DSV41` = 10.x+12.x, block 128, head 512 **CONFIRMED** (the earlier fact-checker's repair of that shifted row is right); (4) [`flash_attn/cute/interface.py`](https://raw.githubusercontent.com/Dao-AILab/flash-attention/main/flash_attn/cute/interface.py) fetched raw → fwd `[8,9,10,11,12]`, bwd `[9,10,11,12]`, `"FP8 is only supported on SM100 (compute capability 10.x) for FA4 CuTe."`, `"Block sparsity not supported on SM 12.0"`, `"Paged KV not supported on SM 12.0 in this PR"`, `"SM120 forward only supports num_splits=1"`, `"Learnable sink backward is supported on SM90 and SM100/SM110"`, `arch // 10 in [8, 12]: num_threads = 128`, `FLASH_ATTENTION_ARCH` override — **all CONFIRMED**; (5) [ROCm gpu-arch-specs](https://rocm.docs.amd.com/en/latest/reference/gpu-arch-specs.html) → **FAILED**, page does not contain the claimed MI355X FLOPS figure — corrected in rows 3 and 13 above. Bonus: FlashInfer PR **#4955 merged 2026-09-07** and **#4982 merged 2026-09-18**, AITER PR **#5403 and #5376 still open**, all four re-confirmed via the GitHub API.
- **Checklist 1, 6, 7, 9, 11, 15** — not applicable to this document: it contains no GiB/GB memory-budget arithmetic, no throughput/cost/batch rows subject to the `max_concurrency(ctx)` feasibility rule, no prices, no B200-vs-H200 DeepSeek-R1 claim, no DSpark acceptance-length figure, and no per-(model, GPU) fit/throughput/cost tables. Per checklist item 15 those tables stay out; §16.2 and §16.4 point at `research/models/<exp>/<gpu>.md` instead.
