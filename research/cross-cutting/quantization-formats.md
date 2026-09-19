# Quantization formats and per-GPU hardware acceleration

Research date: **2026-09-19**. Follows [`research/METHODOLOGY.md`](../METHODOLOGY.md) —
markers `[src]`, **⚠️ TO BE VERIFIED**, `est.`, `meas.`; dense vs sparse TFLOPS always
separated; SXM vs PCIe vs GB-superchip always separated; marketing claims labelled.

GPUs in scope: A100, H100, H200, B200, B300 (HGX/DGX/p6-b300), GB300 NVL72,
RTX PRO 6000 Blackwell (sm_120), MI355X. Checkpoints in scope:
`deepseek-ai/DeepSeek-V4.1-Flash`, `nvidia/DeepSeek-V4.1-Flash-NVFP4`,
`moonshotai/Kimi-K3`, `Qwen/Qwen3.8-27B` (+ its FP8 / NVFP4 / INT4 variants),
`NemoStation/Marlin-2B`.

**Scope.** This document answers *which formats run, and how fast the kernel is* per
(format, GPU). It deliberately carries **no per-(model, GPU) fit / throughput / cost
tables** — those live in `research/models/<exp>/<gpu>.md` and are written in the next
phase. §9.6's checkpoint × GPU matrix is a **format-support** matrix (native / requant /
dequant / not runnable), not a sizing table.

---

## 1. Conventions and the one distinction that matters

Three questions are routinely conflated. Keep them apart:

| Question | What it decides |
|---|---|
| **Storage** — how many bits per weight on disk / in HBM | Fit: how many GPUs you need |
| **Compute** — what the tensor core actually multiplies | Speed: prefill TFLOPS, large-batch decode |
| **Kernel** — which code path the engine picks | Whether you get either of the above |

A checkpoint stored in 4 bits does **not** imply 4-bit tensor-core math. On Hopper an
MXFP4 checkpoint is unpacked to BF16 or FP8 in registers before the MMA — you keep the
memory saving (decode wins, it is bandwidth-bound) and lose the compute saving (prefill
loses, it is FLOP-bound). This is the single most common sizing error in this space,
and §5 is a table of exactly which case each (format, GPU) pair lands in.

Notation for weight/activation precision: **W4A16** = 4-bit weights, 16-bit activations
(weight-only, dequantise-before-MMA). **W4A8** = 4-bit weights, 8-bit activations.
**W4A4** = both 4-bit (full FP4 tensor-core path). **W8A8** = both 8-bit.

---

## 2. Format definitions

### 2.1 Element encodings

| Element | Bits | S/E/M | Representable magnitudes | Max |
|---|---|---|---|---|
| BF16 | 16 | 1/8/7 | — | 3.4e38 |
| FP16 | 16 | 1/5/10 | — | 65504 |
| FP8 E4M3 | 8 | 1/4/3 | — | 448 |
| FP8 E5M2 | 8 | 1/5/2 | — | 57344 |
| FP6 E3M2 | 6 | 1/3/2 | — | 28 |
| FP6 E2M3 | 6 | 1/2/3 | — | 7.5 |
| FP4 E2M1 | 4 | 1/2/1 | 0, 0.5, 1, 1.5, 2, 3, 4, 6 | 6 |
| INT8 | 8 | — | −128…127 | 127 |
| INT4 | 4 | — | −8…7 | 7 |

E2M1 "encode[s] the values ±0, ±0.5, ±1, ±1.5, ±2, ±3, ±4, and ±6"
[src](https://arxiv.org/pdf/2509.25149v1) — **16 representable values total**, which is
why the *scale* carries almost all of the information in a 4-bit format.

Scale encodings: **UE8M0** = unsigned 8-bit, pure power-of-two exponent, no mantissa
(exact, cheap, coarse). **E4M3** = FP8 with 3 mantissa bits (finer, but its own limited
exponent range forces a second-level FP32 tensor scale). **FP32** = per-tensor only.

### 2.2 Block/scale layouts

| Format | Element | Scale | Block | Block axis | 2nd-level scale | Standard |
|---|---|---|---|---|---|---|
| FP8 per-tensor | E4M3 / E5M2 | FP32 | whole tensor | — | — | de facto |
| FP8 per-channel | E4M3 | FP32 | one row | out | — | de facto |
| FP8 128×128 block | E4M3 | FP32 | 128×128 tile | 2-D | — | DeepSeek-V3 |
| FP8 32×32 block UE8M0 | E4M3 | UE8M0 | 32×32 tile | 2-D | — | **DeepSeek-V4.x**, §9.1 |
| MXFP8 | E4M3 / E5M2 | UE8M0 | 32 | K (1-D) | — | [OCP MX v1.0](https://www.opencompute.org/documents/ocp-microscaling-formats-mx-v1-0-spec-final-pdf) |
| MXFP6 | E3M2 / E2M3 | UE8M0 | 32 | K (1-D) | — | OCP MX v1.0 |
| **MXFP4** | E2M1 | UE8M0 | **32** | K (1-D) | — | OCP MX v1.0 |
| **NVFP4** | E2M1 | **E4M3** | **16** | K (1-D) | **FP32 per tensor** | NVIDIA proprietary |
| INT8 W8A8 | INT8 | FP32 | per-tensor or per-channel + per-token act | — | — | SmoothQuant |
| INT4 W4A16 | INT4 | FP16 (+4-bit zp) | 128 (typ.) | K | — | AWQ / GPTQ |
| GGUF Q4_K | 4-bit + 6-bit sub-scales | FP16 super-scale | 32 in super-block of 256 | K | yes (2-level) | llama.cpp |

NVFP4's canonical definition, verbatim from NVIDIA: "Each block contains sixteen
contiguous FP4 elements … along with a single FP8 scale factor … A per-tensor FP32
scale factor (not shown) is also applied."
[src](https://arxiv.org/pdf/2509.25149v1), and the blog restates it as
"4 bits (1 sign, 2 exponent, 1 mantissa)", block size 16, E4M3 scale, FP32 second level
[src](https://developer.nvidia.com/blog/introducing-nvfp4-for-efficient-and-accurate-low-precision-inference/).

NVIDIA's own Blackwell tensor-core table (paper Table 1)
[src](https://arxiv.org/pdf/2509.25149v1):

| Format | Element | Scale | Block |
|---|---|---|---|
| MXFP8 | E5M2/E4M3 | UE8M0 | 32 |
| MXFP6 | E3M2/E2M3 | UE8M0 | 32 |
| MXFP4 | E2M1 | UE8M0 | 32 |
| NVFP4 | E2M1 | E4M3 | 16 |

The same Table 1 carries a fourth column this document previously omitted — **"Speedup
vs. BF16"**: MXFP8 2× and MXFP6 2× on both GB200 and GB300; MXFP4 and NVFP4 **4× on
GB200, 6× on GB300** [src](https://arxiv.org/pdf/2509.25149v1). That is NVIDIA's own
confirmation of the dense ratios in §4.1 (10000/2500 = 4×, 15000/2500 = 6×) and of §6.4's
point that Blackwell FP6 runs at FP8 rate, not FP4 rate.

### 2.3 Effective bits per parameter (computed)

Scale overhead is real memory. Computed with `python3`:
`bits = element_bits + scale_bits / block_elements`.

| Format | bits/param | × BF16 | bytes/param | Scale overhead |
|---|---:|---:|---:|---|
| BF16 | 16.000 | 1.000 | 2.0000 | 0 |
| FP8 per-tensor | 8.000 | 0.500 | 1.0000 | ~0 |
| FP8 128×128 block, FP32 scale | 8.002 | 0.500 | 1.0002 | +0.02 % |
| **FP8 32×32 block, UE8M0** | **8.008** | 0.500 | 1.0010 | +0.1 % |
| MXFP8 (block-32, E8M0) | 8.250 | 0.516 | 1.0312 | +3.1 % |
| MXFP6 (block-32, E8M0) | 6.250 | 0.391 | 0.7812 | +4.2 % |
| **NVFP4** (block-16, E4M3) | **4.500** | 0.281 | 0.5625 | **+12.5 %** |
| **MXFP4** (block-32, E8M0) | **4.250** | 0.266 | 0.5312 | **+6.25 %** |
| INT8 per-channel | 8.000 | 0.500 | 1.0000 | ~0 |
| INT4 g128 (fp16 scale + 4-bit zp) | 4.156 | 0.260 | 0.5195 | +3.9 % |
| GGUF Q4_K_M (weighted mix) | ≈4.85 | 0.303 | 0.6062 | ⚠️ approximate |

(Corrected 2026-09-19: the 128×128 row previously read 8.016 bits / +0.2 %. One FP32
scale per 128×128 = 16,384 elements is 32/16384 = 0.00195 bits/param, so the format is
**8.002 bits / +0.02 %** — an order of magnitude less overhead than stated. Recomputed
with `python3`; no external source needed.)

(Updated 2026-09-19: METHODOLOGY §1 previously stated FP8 32×32 as "~+6 %" and 128×128 as
"~+3 %" — both were the *1-D* MX reading. METHODOLOGY's amended §1 bytes-per-param table
now carries **1.001 (+0.1 %)** for the DeepSeek-V4.1 32×32 UE8M0 tile and **1.0002
(+0.02 %)** for the DeepSeek-V3 128×128 FP32 tile, matching this table. No disagreement
remains; see Open question 20, now closed.) The DeepSeek checkpoint uses **2-D 32×32
tiles**, one scale per 1,024 elements, verified against the shipped safetensors headers in
§9.1. Use +0.1 % for DeepSeek-V4.x weights and +3.1 % for true OCP MXFP8.

One residual disagreement with METHODOLOGY §1, stated per its own rule: METHODOLOGY lists
INT4 W4A16 (AWQ/GPTQ g128) at **≈ 0.53 B/param (+6 %)**; recomputed here it is
4 + (16 + 4)/128 = **4.15625 bits = 0.5195 B/param (+3.9 %)** for an FP16 scale plus a
4-bit zero-point per 128. METHODOLOGY's 0.53 would follow from an FP16 scale *and* an
FP16 zero-point. Both are cited; the pinned Qwen3.8-27B INT4 total in METHODOLOGY §8
(19.45 GB) is the reconciliation target in §9.4.

NVFP4 costs **5.9 % more HBM than MXFP4** for the same weights (4.500 vs 4.250 bits).
That is not rounding noise at trillion-parameter scale — see §9.2, where it is 16 GiB.

---

## 3. NVFP4 vs MXFP4, in full

Both are E2M1. Everything else differs.

| Property | NVFP4 | MXFP4 |
|---|---|---|
| Block size | 16 | 32 |
| Block scale | FP8 E4M3 (fractional) | E8M0 (power-of-two) |
| Per-tensor scale | FP32 | none |
| Bits/param | 4.500 | 4.250 |
| Owner | NVIDIA proprietary | OCP open standard |
| Runs natively on | Blackwell (SM100/103/120) only | Blackwell **and** AMD CDNA4 |
| Tensor-core area cost | ~12 % relative overhead for the 16-wide block ⚠️ single-source | baseline |

The AMD-side statement of the same table is definitive on portability: "The block sizes
and scale encodings differ, an NVFP4 tensor can't be fed directly into an MXFP4 serving
backend."
[src](https://rocm.blogs.amd.com/software-tools-optimization/nvfp4-to-mxfp4/README.html)

### 3.1 Why NVFP4 is more accurate

NVIDIA's mechanism, verbatim: "by reducing the block size from 32 to 16 elements, NVFP4
narrows the dynamic range within each block … Second, block scale factors are stored in
E4M3 rather than UE8M0, trading some exponent range for additional mantissa bits. Third,
an FP32 scale is applied at the [tensor level]"
[src](https://arxiv.org/pdf/2509.25149v1).

The two-level scheme exists because E4M3 has too little exponent range to cover a whole
tensor: "(1) a [per-tensor FP32 scale normalises the tensor] so that block scale factors
can be represented in E4M3, then (2) a per-block E4M3 scale moves the values within a
block into FP4 representable range" [src](https://arxiv.org/pdf/2509.25149v1).

**The headline quantified result** (**8B** hybrid Mamba-Transformer trained on 1T tokens,
controlled pretraining comparison — §5 of the paper, *not* the 12B/10T main run):
"MXFP4 has a relative error of around 2.5 % compared to 1.5 % for NVFP4 …
MXFP4 matches NVFP4 loss when trained on **36 % more tokens** (i.e., using 1.36T instead
of 1T tokens)" [src](https://arxiv.org/pdf/2509.25149v1). Verbatim setup from the same
section: "We consider an 8-billion parameter (8B) model based on the hybrid
Mamba-Transformer architecture. The model is trained on 1 trillion tokens with the same
dataset as used for the 12B model." (Corrected 2026-09-19: this document previously
attributed the ablation to the 12B model.)

Caveat that matters for this repo: that is a **pretraining** result with round-to-nearest
weights. Kimi-K3 is MXFP4 by **quantization-aware training** from the SFT stage
[src](https://huggingface.co/moonshotai/Kimi-K3), and DeepSeek-V4.1-Flash ships MXFP4
experts natively. QAT largely erases the format gap — see the measured evals in §7, where
converting DeepSeek's MXFP4 experts to NVFP4 moves benchmarks by **+0.40 points mean**
and converting Kimi-K3's by **−0.10 points mean**. Neither is a format win; both are noise.
Treat the 36 % figure as a statement about *PTQ/pretraining headroom*, not about what you
gain by transcoding a QAT MXFP4 checkpoint to NVFP4 today.

### 3.2 Why NVFP4 checkpoints do not run natively on AMD

Three independent hardware reasons, all structural:

1. **Block size.** CDNA4's scaled MFMA instruction
   `v_mfma_scale_f32_16x16x128_f8f6f4` "supports 6- and 4-bit operands directly, applies
   per-block scales, and accumulates in FP32"
   [src](https://rocm.blogs.amd.com/artificial-intelligence/w4a6-quant-mm/README.html) —
   with the OCP block granularity of 32. A 16-element block has no instruction.
2. **Scale encoding.** The matrix core consumes a UE8M0 power-of-two scale. An E4M3
   fractional scale would need a multiply the hardware does not perform.
3. **Second-level scale.** MXFP4 has no per-tensor scale concept in the instruction at
   all; NVFP4's FP32 tensor scale has nowhere to live.

So the practical options on MI355X are:

- **Online requantization (recommended).** SGLang's `--quantization quark_mxfp4`
  "detects NVFP4 format and stream[s] each weight tensor through a requantization step"
  at load, dequantising NVFP4→BF16→MXFP4 layer by layer; "Requantization occurs once at
  load time with no per-request overhead", costing 10–55 s
  [src](https://rocm.blogs.amd.com/software-tools-optimization/nvfp4-to-mxfp4/README.html).
  SGLang documents `quark_mxfp4` as AMD CDNA4/MI355X-only, "Converts BF16 or NVFP4 to
  MXFP4 at load" [src](https://docs.sglang.io/advanced_features/quantization.html).
  Measured cost: GSM8K recovery **98.7–100.7 %** across the blog's **five** models
  (MiniMax-M2.7 0.918→0.924 = 100.7 %, Qwen3.5-397B-A17B 0.954→0.945 = 99.1 %,
  **Qwen3.5-397B-A17B-V2 0.954→0.941 = 98.7 %**, **Kimi-K2.6 0.939→0.930 = 99.0 %**,
  DeepSeek-R1 0.958→0.950 = 99.2 %)
  [src](https://rocm.blogs.amd.com/software-tools-optimization/nvfp4-to-mxfp4/README.html)
  — re-fetched and transcribed row by row on 2026-09-19; the earlier fact-check narrowed
  this to "99.1–100.7 %, exactly three models", which missed the V2 and Kimi-K2.6 rows of
  Appendix A. The blog's own prose rounds to "recovers over 99 %", which its 98.7 % row
  contradicts — use the table;
  steady-state throughput within ±1 %
  (MiniMax-M2.7 3516.0 → 3491.2 tok/s, −0.7 %; Qwen3.5-397B-A17B 2963.2 → 2974.4, +0.4 %;
  DeepSeek-R1 2799.0 → 2773.2, −0.9 %) — same source.
  **You are running MXFP4, not NVFP4**, and inherit MXFP4's accuracy, not NVFP4's.
- **Petit emulation.** SGLang exposes `petit_nvfp4`, "Enables NVFP4 on ROCm via Petit",
  scoped to MI250 / MI300X / MI325X
  [src](https://docs.sglang.io/advanced_features/quantization.html) — i.e. the
  *pre*-CDNA4 parts with no FP4 matrix core at all. This is dequantise-to-half emulation
  for memory savings, not acceleration. ⚠️ **TO BE VERIFIED**: whether `petit_nvfp4`
  is selected or beneficial on MI355X, where `quark_mxfp4` is the documented path.
- **Do nothing** and take a load failure or a silent slow fallback.

### 3.3 What MXFP4 does on each GPU

MXFP4 is the portable format, but "portable" ranges from full-rate tensor cores to a
software unpack:

| GPU | MXFP4 execution | Path |
|---|---|---|
| A100 (SM80) | dequantise to FP16/BF16, W4A16 | Marlin ⚠️ see §5 note |
| H100 / H200 (SM90) | **W4A16** default: unpack to BF16 in registers, BF16 MMA | Marlin, or FlashInfer SM90 CUTLASS runner |
| H100 / H200 (SM90) | **W4A8** opt-in: MXFP4 weights × FP8 activations | FlashInfer ≥ 0.6.18 "Humming" kernels |
| H100 / H200 (SM90) | **lossless re-encode to block-FP8** opt-in: 2× the weight memory, FP8 tensor cores | vLLM `VLLM_DSV4_FP4_DEQUANT=1` |
| B200 / B300 / GB300 (SM100/103) | **native FP4 tensor core**, W4A8 (MXFP8 act) or W4A4 | FlashInfer trtllm-gen, CUTLASS, DeepGEMM MegaMoE |
| RTX PRO 6000 (SM120) | **native FP4 tensor core** | FlashInfer `b12x_fused_moe`, `flashinfer_mxfp4` |
| MI355X (CDNA4 gfx950) | **native FP4 matrix core** via scaled MFMA, A8W4 or A4W4 | AITER FlyDSL / Composable Kernel |
| MI300X / MI325X (CDNA3) | dequantise to half precision on the fly | vLLM Quark fused kernel |

The Hopper story is explicit in SGLang's DeepSeek-V4 cookbook: "Original FP4 checkpoints
— run the MoE experts with W4A16 kernels (Marlin or the FlashInfer SM90 CUTLASS runner)
… With FlashInfer >= 0.6.18 you can instead select the W4A8 path — MXFP4 weights with
FP8 activations via FlashInfer's Humming kernels — by adding
`--flashinfer-mxfp4-moe-precision fp8` … Both work on H100 and H200; FP4 is the only
option for H100 (no FP8 path)"
[src](https://docs.sglang.io/cookbook/autoregressive/DeepSeek/DeepSeek-V4).

The CDNA3 story is explicit in vLLM's Quark doc: "A simulation of the matrix
multiplication execution in MXFP4/MXFP6 can be run on devices that do not support OCP MX
operations natively (e.g. AMD Instinct MI325, MI300 and MI250), dequantizing weights from
FP4/FP6 to half precision on the fly, using a fused kernel … to benefit from the ~2.5-4x
memory savings"
[src](https://github.com/vllm-project/vllm/blob/main/docs/features/quantization/quark.md).

---

## 4. Per-GPU hardware capability

### 4.1 Peak dense tensor-core throughput

All figures **dense** unless the column says otherwise. Vendor tables mostly publish
sparse; dense is sparse ÷ 2 where the footnote says so.

| GPU | Arch / SM | BF16 dense TFLOPS | FP8 dense TFLOPS | FP6 dense | FP4 dense TFLOPS | INT8 dense TOPS | HBM | BW |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| A100 80GB SXM | Ampere SM80 | 312 | **none** | none | **none** | 624 | 80 GB HBM2e | 2.039 TB/s |
| A100 80GB PCIe | Ampere SM80 | 312 | none | none | none | 624 | 80 GB HBM2e | 1.935 TB/s |
| H100 SXM | Hopper SM90 | 989.5 | 1979 | none | **none** | 1979 | 80 GB HBM3 | 3.35 TB/s |
| H100 PCIe | Hopper SM90 | 756.5 ⚠️ | 1513 ⚠️ | none | none | 1513 ⚠️ | 80 GB HBM2e ⚠️ | 2.0 TB/s ⚠️ |
| H200 SXM | Hopper SM90 | 989.5 | 1979 | none | **none** | 1979 | 141 GB HBM3e | 4.8 TB/s |
| H200 NVL | Hopper SM90 | 835.5 | 1670.5 | none | none | 1670.5 | 141 GB HBM3e | 4.8 TB/s |
| B200 (HGX / DGX B200) | Blackwell SM100 | 2250 `est.` | 4500 | 4500 | **9000** | ⚠️ n/p | 180 GB HBM3e | 8 TB/s |
| B200 (GB200 superchip) | Blackwell SM100 | 2500 | 5000 | 5000 | **10000** | 5000 | 186 GB HBM3e | 8 TB/s |
| **B300 (HGX / DGX / AWS p6-b300)** | Blackwell Ultra SM103 | 2250 | 4500 | 4500 | **13500** | ⚠️ n/p | **268 GB** HBM3e (2,144 GB / 8-GPU node) | 8.0 TB/s |
| **GB300 NVL72, per B300** | Blackwell Ultra SM103 | 2500 | 5000 | 5000 | **15000** | 166.7 | **288 GB** HBM3e (≈ 279 usable) | 8.0 TB/s |
| RTX PRO 6000 BW SE | Blackwell SM120 | 500 ⚠️ | 1000 ⚠️ | ⚠️ n/p | **≈2000** (4000 sparse) | ⚠️ n/p | 96 GB **GDDR7** | 1.597 TB/s |
| MI355X | CDNA4 gfx950 | 2500 | 5000 | **10100** | **10100** | 5000 | 288 GB HBM3e | 8.0 TB/s |

**Blackwell Ultra has two bins too, and this document previously merged them.**
(Corrected 2026-09-19 per METHODOLOGY §8.) The HGX/DGX B300 and AWS `p6-b300` part is
**268 GB per GPU / 2,144 GB per 8-GPU node** (DGX B300 262.5 GB) and its 1.5× uplift over
B200 is **FP4-only**: BF16 and FP8 are unchanged from the HGX B200 bin, so 2,250 / 4,500 /
**13,500**. The **GB300 NVL72** part is the higher-clocked bin — **288 GB per GPU
(≈ 279 usable)** and 2,500 / 5,000 / **15,000**. Both are 1.5× *their own* B200 bin
(9,000 → 13,500 and 10,000 → 15,000). Never quote 288 GB, 270 GB or 2,304 GB for an
HGX B300 node, and never quote 15,000 TFLOPS for one.

Derivations and caveats:

- **A100**: "BFLOAT16 Tensor Core 312 TFLOPS | 624 TFLOPS\*", "INT8 Tensor Core 624 TOPS
  | 1248 TOPS\*", footnote "\*With sparsity"
  [src](https://www.nvidia.com/en-us/data-center/a100/). **No FP8 and no FP4 tensor
  cores exist on Ampere.** ⚠️ **TO BE VERIFIED (citation only)**: the sentence "No
  pre-Blackwell NVIDIA GPU (H100, H200, A100) has FP4 tensor cores" was **not found** on
  the cited llm-compressor page when re-read on 2026-09-19 (neither the rendered doc nor
  `docs/guides/compression_schemes.md` in the repo contains it) — the *substance* is
  confirmed by TensorRT-LLM's hardware matrix, whose Ampere and Hopper rows list no FP4
  entry at all [src](https://nvidia.github.io/TensorRT-LLM/features/quantization.html),
  and by llm-compressor scoping NVFP4/MXFP4/MXFP8 to "NVIDIA Blackwell (SM100) GPUs or
  later" [src](https://docs.vllm.ai/projects/llm-compressor/en/latest/guides/compression_schemes/).
- **H200**: FP8 3958 TFLOPS and BF16 1979 TFLOPS are the published SXM numbers, and the
  footnote reads "With sparsity" for TF32/BF16/FP16/FP8/INT8
  [src](https://www.nvidia.com/en-us/data-center/h200/) → dense = half.
- **B200 has two distinct clock bins.** DGX/HGX B200: "FP4 Tensor Core: 144 PFLOPS |
  72 PFLOPS\*", "\*Shown in sparse | dense" for 8 GPUs → **9 PFLOPS dense per GPU**
  [src](https://www.nvidia.com/en-us/data-center/dgx-b200/). GB200 superchip (2 GPUs):
  "NVFP4 Tensor Core 40 | 20 PFLOPS", footnote "Specification in sparse | dense" →
  **10 PFLOPS dense per GPU** [src](https://www.nvidia.com/en-us/data-center/gb200-nvl72/).
  Never mix these. The commonly quoted "B200 = 9000 vs B300 = 15000 TFLOPS FP4"
  comparison [src](https://www.tomshardware.com/pc-components/gpus/nvidia-announces-blackwell-ultra-b300-1-5x-faster-than-b200-with-288gb-hbm3e-and-15-pflops-dense-fp4)
  silently pits an HGX B200 against a GB300-bin B300.
- **GB300 NVL72** table: "FP4 Tensor Core 1440 PFLOPS [with sparsity]; 1080 PFLOPS
  [without]", "FP8/FP6 Tensor Core 720 PFLOPS", "FP16/BF16 Tensor Core 360 PFLOPS",
  footnotes "All Tensor Core specifications are with sparsity unless otherwise noted"
  and "Without sparsity" [src](https://www.nvidia.com/en-us/data-center/gb300-nvl72/).
  Divide by 72: dense FP4 = 1080/72 = **15 PFLOPS**; dense FP8 = (720/2)/72 =
  **5 PFLOPS**; dense BF16 = (360/2)/72 = **2.5 PFLOPS**. **These are the NVL72 bin
  only.** The HGX/DGX B300 and AWS `p6-b300` bin is 268 GB per GPU at 2,250 / 4,500 /
  13,500 dense (METHODOLOGY §8) — the FP4 row is 1.5× the HGX B200's 9,000, and the BF16
  and FP8 rows do not move at all. The same table's "INT8 Tensor
  Core 24 POPS" (sparse) gives dense INT8 = (24/2)/72 = **166.7 TOPS** per GPU — verified
  2026-09-19, the ⚠️ previously on that cell is removed. Note this is **1/30th** of the
  FP8 rate: Blackwell Ultra cuts INT8 as hard as it cuts FP64, so INT8 W8A8 is not a
  Blackwell-Ultra strategy. (FP64 100 TFLOPS/rack = 1.39 TFLOPS/GPU, same table.)
- **RTX PRO 6000 Blackwell Server Edition**: "FP4 Tensor Core 4 PFLOPS", "FP8 Tensor
  Core 2 PFLOPS", "FP16 | BF16 Tensor Core 1 PFLOP", 96 GB GDDR7, 1597 GB/s, up to 600 W
  [src](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/).
  Re-fetched 2026-09-19: the page does contain `1597`, `4 PFLOPS`, `2 PFLOPS`,
  `234 TFLOPS` and `96 GB`. **1,597 GB/s is the Server Edition**; the 1,792 GB/s figure
  that circulates is the **Workstation Edition** and must not be used here. (The NVIDIA
  *model card* does not carry a bandwidth figure at all — a sibling doc cited it for one;
  cite this product page instead.) METHODOLOGY §8 pins the dense FP4 rate at **≈ 2,000
  TFLOPS (4,000 is the sparse number)**, i.e. it adopts the halving below as settled.
  NVIDIA does **not** label these sparse or dense on the product page. RTX-class
  Blackwell marketing has consistently quoted *sparse* AI TOPS, so the table above halves
  them. ⚠️ **TO BE VERIFIED** — if these are already dense, every RTX PRO 6000 compute
  figure in this repo doubles. *New corroboration (2026-09-19):* the same page lists
  **"TF32 Tensor Core 234 TFLOPS"** — an unrounded number that is exactly 2× the part's
  FP32 rate, i.e. a **dense** TF32 figure. A dense BF16 of 2× dense TF32 would be
  ~468 TFLOPS, which cannot round to the page's "1 PFLOP" FP16|BF16 unless that figure is
  sparse. So the halving is the right reading, and the dense BF16 is ~468–500 rather than
  exactly 500 [src](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/). Also note GDDR7 at 1.6 TB/s is **5× less bandwidth** than
  a B200; on a decode-bound MoE this GPU is bandwidth-starved long before FLOPS matter.
- **MI355X** (AMD's own datasheet, two-column "peak | W/SPARSITY")
  [src](https://www.amd.com/content/dam/amd/en/documents/instinct-tech-docs/product-briefs/amd-instinct-mi355x-gpu-brochure.pdf):
  FP16 MATRIX 2.5166 PF | 5.0332 PF; BFLOAT16 2.5166 | 5.0332; INT8 MATRIX 5.0332 POPS |
  10.0664; MXFP8 5.0332 | **N/A**; OCP-FP8 5.0332 | 10.0664; MXFP6 10.0663 | **N/A**;
  MXFP4 10.0663 | **N/A**. 288 GB HBM3E, 8 TB/s, 1400 W TBP, 256 CUs, 1024 matrix cores.
  **AMD's own datasheet lists no sparse figure for MXFP4** — the widely-repeated
  "20.1 PFLOPS FP4" is not in it. **The table above now carries METHODOLOGY §8's pinned
  dense peaks — BF16 2,500 / FP8 5,000 / INT8 5,000 / MXFP6 10,100 / MXFP4 10,100**
  (changed 2026-09-19 from the brochure's unrounded 2516.6 / 5033.2 / 10066.3, which
  remain the carried-over read quoted in this bullet). The difference is ≤ 0.7 % and
  below the MBU/MFU uncertainty in every downstream calculation; use the pinned values so
  sibling docs agree.
  ⚠️ **TO BE VERIFIED (re-verification failed 2026-09-19)**: the brochure PDF,
  `amd.com/en/products/accelerators/instinct/mi350.html` and AMD's MI355X press release
  all timed out on re-fetch, so none of the MI355X throughput figures above could be
  re-confirmed against a primary source today. What *was* re-confirmed independently:
  **256 compute units (32 per XCD)** and **288 GiB** memory, from AMD's own ROCm
  hardware-specs table [src](https://rocm.docs.amd.com/en/latest/reference/gpu-arch-specs.html)
  — which lists neither bandwidth nor peak FLOPS. Treat the 2516.6 / 5033.2 / 10066.3
  figures and the "no sparse MXFP4" claim as carried over from the earlier read, not
  re-verified. (Note also GiB vs GB: ROCm says 288 **GiB**, the table above says 288 GB.)

### 4.2 Format support matrix — silicon

✅ native tensor/matrix core · ➖ software (unpack / emulate) · ❌ not usable

| Format | A100 | H100 | H200 | B200 | B300/GB300 | RTX PRO 6000 | MI355X |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| BF16 / FP16 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| FP8 E4M3 / E5M2 | ❌ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ (OCP-FP8) |
| FP8 128×128 block | ❌ | ✅ | ✅ | ✅¹ | ✅¹ | ✅ | ✅ |
| FP8 32×32 block UE8M0 | ❌ | ➖² | ➖² | ✅ | ✅ | ✅ | ✅ |
| MXFP8 (block-32 UE8M0) | ❌ | ➖ | ➖ | ✅ | ✅ | ✅ | ✅ |
| MXFP6 | ❌ | ➖ | ➖ | ✅ | ✅ | ⚠️ n/p | ✅ |
| **MXFP4** | ➖ | ➖ | ➖ | ✅ | ✅ | ✅ | ✅ |
| **NVFP4** | ➖ | ➖ | ➖ | ✅ | ✅ | ✅ | ❌³ |
| INT8 W8A8 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| INT4 W4A16 (AWQ/GPTQ) | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖⁴ |
| GGUF k-quants | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ | ➖ |

1. "FP8 block wise scaling GEMM kernels for sm100/103 are using MXFP8 recipe (E4M3
   act/weight and UE8M0 act/weight scale), which is slightly different from SM90 FP8
   recipe" [src](https://nvidia.github.io/TensorRT-LLM/features/quantization.html) —
   i.e. a DeepSeek-V3-style FP32-scaled 128×128 checkpoint is re-expressed, not run
   bit-identically, on Blackwell.
2. Hopper FP8 tensor cores are fine; only the **UE8M0 32×32 scale layout** needs
   software fixup. Engines handle it, but it is not the SM90 native recipe.
3. See §3.2. MI355X can *serve* NVFP4 checkpoints only by converting them to MXFP4 at
   load.
4. INT4 weight-only is always a dequantise-then-MMA scheme on every GPU listed; there is
   no INT4 tensor core on any of them. Marlin/Machete are CUDA-only, so AMD gets the
   Triton/AITER a16w4 path instead.

### 4.3 What B300/GB300 gains over B200

Compare like-for-like bins — HGX against HGX, NVL72 against NVL72. (Table split
2026-09-19; it previously merged the two Ultra bins into one "B300 / GB300" column with
288 GB and 15,000 TFLOPS, which over-states an HGX B300 node by 1,152 GB and 12,000
TFLOPS per 8 GPUs.)

| Axis | B200 HGX | **B300 HGX / DGX / p6-b300** | Δ | B200 (GB200 bin) | **GB300 NVL72** | Δ |
|---|---:|---:|---|---:|---:|---|
| **Dense NVFP4/MXFP4 TFLOPS** | 9000 | **13500** | **+50 %** | 10000 | **15000** | **+50 %** |
| Dense FP8 TFLOPS | 4500 | 4500 | **0 %** | 5000 | 5000 | **0 %** |
| Dense BF16 TFLOPS | 2250 | 2250 | **0 %** | 2500 | 2500 | **0 %** |
| Attention-layer compute (SFU) | 1× | **up to 2×** | +100 % | 1× | **up to 2×** | +100 % |
| HBM / GPU | 180 GB | **268 GB** | +48.9 % | 186 GB | **288 GB** | +54.8 % |
| HBM / 8-GPU node | 1,440 GB | **2,144 GB** | +48.9 % | 1,488 GB | 2,304 GB (8 of 72) | +54.8 % |
| HBM bandwidth | 8 TB/s | 8.0 TB/s | 0 % | 8 TB/s | 8.0 TB/s | 0 % |
| FP64 | full | cut | −97 % ⚠️ | full | cut | −97 % ⚠️ |
| TDP | 1000–1200 W | 1400 W | +17–40 % | 1200 W | 1400 W | +17 % |

NVIDIA, verbatim: "Blackwell Ultra pushes that to 15 petaFLOPS—a 1.5x increase compared
to Blackwell GPU" and "SFU throughput has been doubled for key instructions used in
attention, delivering up to 2x faster attention-layer compute compared to Blackwell GPUs"
[src](https://developer.nvidia.com/blog/inside-nvidia-blackwell-ultra-the-chip-powering-the-ai-factory-era/).

**The planning consequence.** B300 is not "1.5× a B200". It is 1.5× *only when you are
FP4-compute-bound*, plus up to 2× on the attention kernel, plus **+48.9 % HBM on the
HGX/DGX/p6-b300 bin (180 → 268 GB) or +54.8 % on the GB300 NVL72 bin (186 → 288 GB)**.
On an FP8
workload B300 and B200 have identical peak math and identical bandwidth — you are paying
for capacity and attention, and nothing else. Two corollaries for this repo:

- For **Kimi-K3** (1,560.9 GB = 1,453.7 GiB = **1.42 TiB** MXFP4) and
  **DeepSeek-V4.1-Flash** (510.29 GB = 475.2 GiB), the capacity is the reason the model
  fits at all at small TP; the FP4 uplift is secondary. Use the right bin: **268 GB/GPU
  (2,144 GB per 8-GPU HGX/DGX B300 or AWS `p6-b300` node)**, or **288 GB/GPU (≈ 279
  usable) on GB300 NVL72** (METHODOLOGY §8). Per-GPU fit at each TP is not computed
  here — see `research/models/<exp>/<gpu>.md`.
- For **long-context, sparse-attention** models — DeepSeek-V4.1-Flash's CSA2 indexer,
  Kimi-K3's KDA — the 2× SFU claim targets exactly the softmax/exp-heavy inner loops
  those architectures lean on. ⚠️ **TO BE VERIFIED**: no published end-to-end measurement
  isolating the SFU uplift for CSA2 or KDA as of 2026-09-19.

The FP64 collapse (≈37 → ≈1.25 TFLOPS per third-party reporting
[src](https://www.tomshardware.com/pc-components/gpus/nvidia-announces-blackwell-ultra-b300-1-5x-faster-than-b200-with-288gb-hbm3e-and-15-pflops-dense-fp4))
is irrelevant to inference but disqualifies B300 for mixed HPC duty. ⚠️ NVIDIA's own
GB300 NVL72 page lists FP64 at 100 TFLOPS for the rack (1.39 TFLOPS/GPU), corroborating
the direction.

---

## 5. Kernel and engine matrix

Which code actually runs, per format per GPU.

| Format | A100 SM80 | H100/H200 SM90 | B200 SM100 | B300/GB300 SM103 | RTX PRO 6000 SM120 | MI355X gfx950 |
|---|---|---|---|---|---|---|
| FP8 W8A8 per-tensor | ❌ | cuBLASLt / CUTLASS | CUTLASS / cuBLASLt | same | CUTLASS | hipBLASLt + AITER |
| FP8 block-scale | ❌ | DeepGEMM, FlashInfer `flashinfer_deepgemm` | DeepGEMM, MegaMoE | same | CUTLASS | hipBLASLt / AITER |
| MXFP8 | ❌ | ➖ upconvert | FlashInfer trtllm-gen, `--linear-backend flashinfer_trtllm` | same | FlashInfer SM12x MXFP8 MoE | AITER MFMA |
| **MXFP4 W4A16** | Marlin ⚠️ | **Marlin** (default), FlashInfer SM90 CUTLASS | (not used) | (not used) | FlashInfer SM12x W4A16 | AITER `aiter_triton_mxfp4_bf16` (a16w4) |
| **MXFP4 W4A8** | ❌ | FlashInfer ≥0.6.18 **Humming**, `--flashinfer-mxfp4-moe-precision fp8` | **FlashInfer trtllm-gen SiTU** (default) | same | FlashInfer `b12x_fused_moe` | **AITER SiTU v2 A8W4 FlyDSL** (default) + Composable Kernel a8w4 |
| **MXFP4 W4A4** | ❌ | ❌ | SGLang `--enable-w4a4-mxfp4-megamoe` | same | ⚠️ n/p | AITER `AITER_SITUV2_A4W4=1` |
| **NVFP4 W4A4** | ➖ Marlin W4A16 fallback | ➖ Marlin W4A16 fallback | CUTLASS / FlashInfer `flashinfer_trtllm` / `flashinfer_cutlass` / `flashinfer_cutedsl` / cuDNN | same | FlashInfer SM12x NVFP4 fused-MoE (since PR #2898) | ❌ → requantise to MXFP4 |
| NVFP4 W4A16 | Marlin | Marlin | CuTe-DSL `quant_mode='w4a16'`, Marlin | same | FlashInfer W4A16 | ❌ |
| INT8 W8A8 | CUTLASS | CUTLASS | CUTLASS | CUTLASS | CUTLASS | hipBLASLt |
| INT4 W4A16 AWQ/GPTQ | Marlin | **Machete** (Hopper-tuned) or Marlin | Marlin | Marlin | Marlin | Triton (Marlin is CUDA-only) |
| GGUF | llama.cpp MMQ / vLLM GGUF kernels | same | same | same | same | ROCm MMQ |

Sources for the cells that are not obvious:

- **Marlin is the universal NVFP4 fallback.** "On GPUs without a supported native FP4
  GEMM kernel, vLLM falls back to weight-only (W4A16) execution via Marlin and logs a
  warning; this may reduce throughput for compute-heavy workloads"
  [src](https://github.com/vllm-project/vllm/blob/main/docs/features/quantization/modelopt.md).
  SGLang says the same: `modelopt_fp4` is "NVIDIA (SM80+) … SM100+ native FP4; Marlin
  fallback on Ampere/Hopper"
  [src](https://docs.sglang.io/advanced_features/quantization.html).
- **Machete** is the Hopper-specific successor to Marlin: built on CUTLASS 3.5.1, uses
  `wgmma` where "Marlin used outdated `mma`, losing ~37 % peak throughput on Hopper",
  handles w4a16/w8a16 compressed-tensors and GPTQ, and upconverts weights "to 16-bit in
  registers … using bit shifts and masking operations"
  [src](https://developers.redhat.com/articles/2024/10/14/introducing-machete-mixed-input-gemm-kernel).
- **vLLM backend selection** is now a first-class flag: `--linear-backend` with values
  `cutlass`, `flashinfer_cutlass`, `flashinfer_cutedsl`, `flashinfer_trtllm`,
  `flashinfer_cudnn`, `marlin`, plus per-scheme override
  `--kernel-config '{"linear_backend_per_quant":{"nvfp4_w4a16":"humming"}}'`. This
  replaces the deprecated `VLLM_NVFP4_GEMM_BACKEND`
  [src](https://github.com/vllm-project/vllm/blob/main/docs/features/quantization/README.md).
- **SM120 was a real gap and is now closed.** trtllm-gen modules "filter to
  `supported_major_versions=[10]`", so RTX PRO 6000 fell back to Marlin, "approximately
  13 % to 28 % slower than optimized CUTLASS kernels depending on prompt length"; issue
  #2847 was closed by PR #2898
  [src](https://github.com/flashinfer-ai/flashinfer/issues/2847). FlashInfer 0.6.18
  ships "MXFP4 on Blackwell RTX PRO and DGX Spark" with `b12x_fused_moe` accepting
  `quant_mode="mxfp4"` [src](https://flashinfer.ai/releases/).
- **MI355X MoE kernels**: vLLM's `--moe-backend aiter` "lets vLLM select the Composable
  Kernel a8w4 experts"; naming `aiter_triton_mxfp4_bf16` instead "pins the Triton W4A16
  `_moe_gemm_a16w4` kernel"
  [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash). SGLang's A4W4 knob is
  `AITER_SITUV2_A4W4=1`, A8W4 is `AITER_SITUV2_A8W4=1`
  [src](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3).
- ⚠️ **TO BE VERIFIED (A100 row)**: vLLM's published hardware matrix has no Blackwell
  column at all and ends at Hopper, and lists "Marlin (GPTQ/AWQ/FP8/FP4) — Turing ✅\*,
  Ampere ✅, Ada ✅, Hopper ✅" with the footnote "Turing does not support Marlin MXFP4"
  [src](https://github.com/vllm-project/vllm/blob/main/docs/features/quantization/README.md).
  The table is visibly stale. Whether Marlin MXFP4 is functional *and performant* on
  SM80 for a 4-bit MoE checkpoint is untested here; assume it loads and is slow.

### 5.1 Engine-level format support

SGLang's `--quantization` values with hardware scoping
[src](https://docs.sglang.io/advanced_features/quantization.html):

| Value | Hardware | Online | Offline | Note |
|---|---|:--:|:--:|---|
| `fp8` | NVIDIA, AMD, Ascend (WIP) | ✓ | ✓ | AITER or Triton on AMD |
| `mxfp4` | NVIDIA, AMD (CDNA3/4), Ascend | ✓ | ✓ | W4A4 for MoE; online dual-level for dense |
| `blockwise_int8` | NVIDIA, AMD | — | ✓ | Triton |
| `w8a8_int8` | NVIDIA, AMD | — | ✓ | per-channel/token |
| `w8a8_fp8` | NVIDIA, AMD | — | ✓ | |
| `awq` / `awq_marlin` | NVIDIA, AMD / **NVIDIA only** | — | ✓ | "Marlin backend exclusive to CUDA" |
| `gptq_marlin` | **NVIDIA only** | — | ✓ | plain `gptq` removed from NVIDIA/AMD |
| `compressed-tensors` | NVIDIA, AMD, Ascend (partial) | — | ✓ | Kimi-K3's format |
| `quark` / `quark_mxfp4` | NVIDIA+AMD / **AMD CDNA4 only** | ✓ | — | `quark_mxfp4` converts BF16 **or NVFP4** → MXFP4 at load |
| `quark_int4fp8_moe` | AMD CDNA3/4 only | ✓ | — | online INT4→FP8 MoE |
| `modelopt` / `modelopt_fp8` | NVIDIA SM90+ | — | ✓ | |
| `modelopt_fp4` | NVIDIA SM80+ | — | ✓ | **SM100+ native; Marlin fallback on Ampere/Hopper** |
| `nvfp4_online` | NVIDIA SM100+ | ✓ | — | per-token FP32 act scales; MoE only |
| `petit_nvfp4` | AMD MI250/MI300X/MI325X | — | ✓ | NVFP4 on ROCm via Petit |
| `gguf` | NVIDIA, Ascend | — | ✓ | CUDA kernels in sgl-kernel |
| `auto-round` | NVIDIA, AMD, Ascend | ✓ | ✓ | |

TensorRT-LLM's hardware matrix
[src](https://nvidia.github.io/TensorRT-LLM/features/quantization.html):

| Arch | Supported |
|---|---|
| Blackwell sm100/103 | NVFP4, MXFP4, FP8 per-tensor, FP8 block-scaling, FP8 rowwise ⚠️, FP8 KV cache, **NVFP4 KV cache**, W4A8/W4A16 AWQ & GPTQ |
| Blackwell sm120 | NVFP4, MXFP4, FP8 per-tensor, FP8 KV cache |
| Hopper | FP8 per-tensor, FP8 block-scaling, FP8 rowwise, FP8 KV cache, W4A8/W4A16 AWQ & GPTQ |
| Ada | FP8 per-tensor, FP8 KV cache, W4A8/W4A16 AWQ & GPTQ |
| Ampere | FP8 KV cache, **W4A16 only** AWQ & GPTQ (no W4A8, no FP8 GEMM) |

Two amendments after re-reading the matrix on 2026-09-19: Ada and Ampere are **separate
rows** and Ampere has no FP8 per-tensor GEMM and no W4A8 — it is W4A16-only, which
matters for every A100 plan in this repo. And **"FP8 rowwise" appears only in the Hopper
row**, not in sm100/103, on the re-read ⚠️ **TO BE VERIFIED** (kept in the table above
rather than deleted, flagged pending a third read).

Note sm120 **lacks FP8 block-scaling** in TRT-LLM's matrix — relevant because
DeepSeek-V4.x's non-expert weights are block-scaled FP8. ⚠️ **TO BE VERIFIED** whether
that blocks DeepSeek-V4.1-Flash on RTX PRO 6000 under TRT-LLM (SGLang serves it there
via a different path, §9.1).

LLM Compressor's minimum hardware
[src](https://docs.vllm.ai/projects/llm-compressor/en/latest/guides/compression_schemes/):

| Scheme | Group | Scale dtype | Min hardware | Calibration |
|---|---|---|---|---|
| W8A8-FP8 | per-channel / per-tensor | — | Ada+ ⚠️ | no (RTN) |
| W8A8-FP8_BLOCK | 128×128 ⚠️ (source says group 128) | — | Hopper+ ⚠️ | no |
| W8A8-INT8 | per-channel / per-group | — | Turing+ ⚠️ | yes (GPTQ/AWQ/static act) |
| W4A16 / W8A16 | per-group | — | any GPU | yes (GPTQ, AWQ) |
| **NVFP4** | 16 | `float8_e4m3fn` | **SM100 (Blackwell)+** | **yes** (activation global scales) |
| **MXFP4** | 32 | E8M0 (uint8) | SM100+, cross-platform per OCP | no (RTN) |
| **MXFP8** | 32 | E8M0 (uint8) | SM100+, cross-platform per OCP | no (RTN) |

⚠️ **TO BE VERIFIED (2026-09-19)**: the three "Min hardware" cells marked above
(Ada+ / Hopper+ / Turing+) could **not** be found on re-reading either the rendered page
or `docs/guides/compression_schemes.md` in the llm-compressor repo — both render the
FP8/INT8 rows without a hardware column. What *is* on the page is the SM100+ scoping of
NVFP4 / MXFP4 / MXFP8 and the note that MXFP4 is "cross-platform compatible via the OCP
MX spec"; those three rows are confirmed. Treat the pre-Blackwell minima as
folk-knowledge until sourced.

"[I]f running inference on a machine that is < SM100, vLLM will not run activation
quantization, only weight-only quantization" — **corrected citation**: this sentence is
on llm-compressor's *W4A4-FP4 example* page
[src](https://docs.vllm.ai/projects/llm-compressor/en/latest/examples/quantization_w4a4_fp4/),
not on the compression-schemes page this document previously attributed it to. That
sentence is the whole W4A4-vs-W4A16 story in one line.

---

## 6. Measured throughput

Every number below is published and cited. Nothing here is extrapolated.

### 6.1 FP8 → NVFP4, same model, Blackwell vs Hopper

GLM-5 (744B, ~40B active), SGLang v0.5.12, EAGLE MTP `--speculative-num-steps 3`,
ISL 8192 / OSL 1024 [src](https://inferencex.semianalysis.com/blog/b200-glm5-nvfp4-vs-h200-fp8-3-6x-perf-per-dollar):

| Config | Concurrency | tok/s/GPU | tok/s/user | $/M tok |
|---|---:|---:|---:|---:|
| H200 FP8, TP8 | 4 | 347.9 | 84.49 | $1.13 |
| H200 FP8, TP8 | 8 | 489.7 | 59.82 | $0.80 |
| H200 FP8, TP8 | 32 | 851.9 | 24.90 | $0.46 |
| **B200 NVFP4, TP4** | 4 | **1038.7** | 121.22 | **$0.52** |
| **B200 NVFP4, TP4** | 32 | **3037.3** | 43.99 | **$0.18** |
| **B200 NVFP4, TP4** | 128 | **4115.5** | 17.63 | **$0.13** |

Prices assumed **by the source**: H200 SXM $1.41/GPU/hr, B200 SXM $1.95/GPU/hr. These are
the blog's own assumptions, quoted as published — they are **not** repo planning prices
and must not be reused as such. The sourced on-demand rows for the same silicon in
[cloud-pricing.md](cloud-pricing.md) are **OCI H200 $10.00 and B200 $14.00 per GPU-hour**
(`BM.GPU.H200.8` / `BM.GPU.B200.8`), with neocloud rates 2.2–2.4× lower (e.g. Hyperstack
HGX B200 $8.60, HGX H200 $6.31). The `$/M tok` column below therefore scales by ~7× at
OCI list; the *ratio* between rows, which is what this table is cited for, is unaffected.
Headline: **3.65× better
performance per dollar** at 80 tok/s/user. The decomposition is the useful part:
**generation step alone** (B200 FP8 vs H200 FP8) = 1.22×; **precision step**
(FP8 → NVFP4) is **2.98× on its own**, and the two multiply: 1.22 × 2.98 = 3.64 ≈ 3.65.
So **2.98× of the 3.65× is the format, and only 1.22× is the silicon.**
(Corrected 2026-09-19: this paragraph previously read 2.98× as the *combined* factor and
attributed ~2.4× to the format. The source states the decomposition as a product —
"1.22x (generation step) × 2.98x (precision step)" — and gives the headline as $0.29/M
vs $1.06/M tokens at 80 tok/s/user
[src](https://inferencex.semianalysis.com/blog/b200-glm5-nvfp4-vs-h200-fp8-3-6x-perf-per-dollar).)

### 6.2 MXFP4 on Hopper: the dequantisation trade

vLLM PR #53709, DeepSeek-V4 MXFP4 routed experts losslessly re-encoded to block-FP8
(E4M3, 128×128 scales) at load, H20, TP=4
[src](https://github.com/vllm-project/vllm/pull/53709):

| Phase | Baseline (Marlin W4A16) | `VLLM_DSV4_FP4_DEQUANT=1` (FP8 TC) | Speedup |
|---|---:|---:|---:|
| Prefill 2048 tok | 7020 tok/s | 10752 tok/s | **1.53×** |
| Prefill 4096 tok | 8129 tok/s | 11882 tok/s | 1.46× |
| Prefill 8192 tok | 8396 tok/s | 11623 tok/s | 1.38× |
| Decode 32 conc., 2k out | 1642 tok/s | 1856 tok/s | 1.13× |
| Decode 32 conc., 10k out | 1637 tok/s | 1841 tok/s | 1.12× |

Cost: "FP8 experts use 2x the weight memory vs MXFP4", cutting KV cache from
**83.9 → 53.7 GiB per GPU** (5.70M → 3.65M tokens). The PR states plainly: "Hopper has
no FP8-tensor-core MoE path for these checkpoints today."

This table is the cleanest available proof of §1's thesis. On Hopper, 4-bit storage buys
memory; 8-bit compute buys **1.4–1.5× prefill**; you cannot have both, and the decode
gain is only 1.12–1.13× because decode was never FLOP-bound.

### 6.3 A8W4 vs A4W4 on MI355X

Kimi-K3, SGLang, 8× MI35x
[src](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3):
`AITER_SITUV2_A8W4=1` (GU-interleaved preshuffled layout, the shipped default) reaches
**537.3 tok/s** median output; `AITER_SITUV2_A4W4=1` (generic separated shuffle layout)
is "Numerically correct but slower" at **530.8 tok/s** — a 1.2 % deficit.

Counter-intuitive but real: **4-bit activations are not automatically faster than 8-bit
activations.** The weight layout and the quantise/dequantise overhead around the GEMM
dominate at this shape. The same source records the AITER fused KDA decode boundary at
9.20 → 8.38 µs/layer (−8.9 %) with GSM8K 1319 at 0.950 — i.e. kernel fusion moved the
needle roughly 7× more than the activation format did.

### 6.4 MXFP6 on MI355X

MI355X is the only GPU in scope with a native 6-bit path, and AMD positions it as the
accuracy-recovery tier between MXFP4 and FP8
[src](https://rocm.blogs.amd.com/artificial-intelligence/w4a6-quant-mm/README.html):

- W_MXFP4_A_MXFP6 "exceeds FP8 hipBLASLt on all four shapes" tested; tuned, it lands
  "within 3.6–15.7 % of MXFP4 depending on shape".
- Offline Llama-3.1-8B: **85.0k vs 83.3k total tok/s** (W4A6 vs FP8).
- Accuracy, Llama-3.1-8B GSM8K: **MXFP4 62.55 % → W4A6 76.4 % → FP8 80.44 %**.
- Qwen3.6-27B AIME26: W4A6 **85.8 %** vs FP8 86.7 %, "within about 1 point".

That GSM8K jump (62.55 → 76.4) is the strongest published evidence that **PTQ MXFP4 on a
dense model is genuinely lossy**, and that a 6-bit activation recovers most of it at FP8-
beating throughput. There is no NVIDIA equivalent: Blackwell has FP6 tensor cores at the
same rate as FP8 (not FP4 rate), so the trade is far less attractive on NVIDIA.
⚠️ **TO BE VERIFIED**: no published W4A6 numbers for any model in this repo.

### 6.5 Rack-scale and cross-vendor

[src](https://inferencex.semianalysis.com/blog/inferencex-v2-nvidia-blackwell-vs-amd-vs-hopper):

- DeepSeek R1 **FP4** on GB300 NVL72: up to **100× better perf** than H100; GB200 NVL72
  FP4 up to **98×** vs H100 FP8 at 116 tok/s/user. (Rack vs single-node — marketing-shaped
  comparison, quoted as published.)
- DeepSeek R1 **FP8** single node: "MI355X is competitive with its counterpart B200".
- DeepSeek R1 **FP4**: "MI355X gets absolutely mogged by Nvidia's B200"; and with all
  three of disaggregated prefill + wide EP + FP4 enabled, "AMD's performance is currently
  not competitive".
- MTP is worth more than the format: DeepSeek R1 FP4 on B200 Dynamo TRT-LLM goes
  **$0.251 → $0.057 per million total tokens** with MTP, "~21x price decrease".

**Read this as a software gap, not a silicon gap.** MI355X's dense MXFP4 peak
(**10,100 TFLOPS = 10.1 PFLOPS**, METHODOLOGY §8) is within 1 % of a GB200-bin B200's
NVFP4 peak (10,000 TFLOPS) — and *below* an HGX B300's 13,500 and a GB300's 15,000. The FP4
deficit is composability — the same source notes MI355X vLLM ran on "an outdated fork
(vLLM 0.10.1)". Any MI355X FP4 number older than ~a quarter is not worth planning on.

---

## 7. Accuracy

### 7.1 Direct MXFP4 → NVFP4 transcode, same base model

The rarest and most useful kind of evidence: identical weights, one format change.

**DeepSeek-V4.1-Flash**, vLLM on GB300, `temperature=1.0, top_p=0.95,
reasoning_effort=100`; GPQA/AA-LCR 16 repeats, IFBench 5
[src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4):

| Benchmark | MXFP4 (source) | NVFP4 | Δ |
|---|---:|---:|---:|
| GPQA Diamond | 91.035 | 91.288 | +0.253 |
| AA-LCR | 78.563 | 78.438 | −0.125 |
| SciCode | 54.401 | 55.843 | **+1.442** |
| IFBench | 76.667 | 77.267 | +0.600 |
| MMMU-Pro | 74.046 | 73.699 | −0.347 |
| Terminal-Bench 2.1 | 81.60 | 82.16 | +0.560 |
| **mean** | | | **+0.397** |

**Kimi-K3**, original (MXFP4 experts + BF16 attention) vs NVFP4 (NVFP4 experts + FP8
128×128 attention), `temperature=1.0, top_p=0.95`, 65,536 max gen
[src](https://huggingface.co/nvidia/Kimi-K3-NVFP4):

| Benchmark | Original | Kimi-K3-NVFP4 | Δ |
|---|---:|---:|---:|
| GPQA Diamond | 93.21 | 92.77 | −0.44 |
| SciCode | 58.38 | 58.58 | +0.20 |
| MMMU-Pro | 80.63 | 79.83 | −0.80 |
| AA-LCR | 75.00 | 75.06 | +0.06 |
| IFBench | 74.40 | 74.93 | +0.53 |
| Terminal-Bench 2.1 | 80.34 | 80.20 | −0.14 |
| **mean** | | | **−0.098** |

**Conclusion: on QAT-native MXFP4 checkpoints, transcoding to NVFP4 is accuracy-neutral
(+0.40 and −0.10 points mean, both inside run-to-run variance).** Choose the format for
kernel availability and memory, not accuracy. Note the Kimi-K3 NVFP4 conversion also drops
attention from BF16 to FP8, so its −0.10 is a slightly harder test and still passes.

### 7.2 PTQ against a high-precision baseline

- DeepSeek-R1-0528, FP8 vs NVFP4, "1 % or less accuracy degradation"
  [src](https://developer.nvidia.com/blog/introducing-nvfp4-for-efficient-and-accurate-low-precision-inference/):
  MMLU-PRO 85 → 84; GPQA Diamond 81 → 80; AIME 2024 89 → 91; Math-500 98 → 98;
  LiveCodeBench 77 → 76.
- DeepSeek-V4-Flash, own baseline vs NVFP4, `temperature=1.0, top_p=1.0`
  [src](https://huggingface.co/nvidia/DeepSeek-V4-Flash-NVFP4):
  GPQA Diamond 0.894 → 0.891; AA-LCR 0.658 → 0.655; τ²-Bench Telecom 0.943 → 0.942;
  SciCode 0.481 → 0.481; IFBench 0.788 → **0.795**.
- MI355X online NVFP4→MXFP4 requantization: GSM8K recovery **98.7–100.7 %** across five
  models (re-transcribed 2026-09-19, §3.2); GPQA-Diamond-CoT and AIME25 agree with the
  NVFP4-emulation reference verbatim: "Online NVFP4 to MXFP4 requant and NVFP4 emulation
  agree within seed noise across all task questions, with final averaged accuracy score
  **within ±0.05** of each other … Re-running the same configuration under different
  random seeds moves the score by roughly 5–10 % on these small and high variance
  benchmarks"
  [src](https://rocm.blogs.amd.com/software-tools-optimization/nvfp4-to-mxfp4/README.html).
  (The earlier fact-check flagged the ±0.05 figure as not locatable and added a ⚠️; it is
  in the "Advanced Reasoning Benchmarks" section, under Figure 3. Marker removed.)
- Dense PTQ MXFP4 is the outlier: Llama-3.1-8B GSM8K **62.55 %** vs FP8 80.44 %
  [src](https://rocm.blogs.amd.com/artificial-intelligence/w4a6-quant-mm/README.html).
  MoE-with-QAT ≠ dense-with-RTN. Do not generalise the MoE results to a dense model
  you quantise yourself.
- Calibration matters. `nvidia/Qwen3.8-27B-NVFP4` used 2048 samples through the
  ModelOpt **Local-Hessian** algorithm; `nvidia/DeepSeek-V4.1-Flash-NVFP4` used 1024
  samples (512 each from cnn_dailymail and Nemotron-Post-Training-Dataset-v2, seqlen 512,
  batch 4, seed 0); `nvidia/Kimi-K3-NVFP4` used **none** ("The MXFP4-to-NVFP4 expert
  conversion used `input_scale=1.0`"). Three different recipes, three different risk
  profiles.

### 7.3 Training-side evidence (context, not an inference claim)

12B hybrid Mamba-Transformer, 10T tokens, NVFP4 vs FP8
[src](https://arxiv.org/pdf/2509.25149v1): relative loss error "consistently below 1 %"
during the stable phase, "slightly above 1.5 %" as the LR decays. Downstream accuracy
"largely unaffected", with coding the one soft spot. Method: 2-D block scaling of
weights, Random Hadamard transforms (16×16) on weight-gradient GEMM inputs, stochastic
rounding for gradients / RNE for weights and activations, and selected layers kept in
BF16.

---

## 8. KV-cache quantization

### 8.1 What each engine exposes

vLLM's `CacheDType` literal is now far wider than the docs suggest
[src](https://github.com/vllm-project/vllm/blob/main/vllm/config/cache.py):

```
auto, float16, bfloat16,
fp8, fp8_e4m3, fp8_e5m2, fp8_inc, fp8_ds_mla,
nvfp4, nvfp4_ds_mla, nvfp4_4over6,
int4_per_token_head, int8_per_token_head, fp8_per_token_head,
turboquant_k8v4, turboquant_4bit_nc, turboquant_k3v4_nc, turboquant_3bit_nc
```

Notable: **`nvfp4` and `nvfp4_ds_mla` are first-class KV dtypes**, and `nvfp4_4over6`
"uses the NVFP4 layout and selects between max/6 and max/4 scales per 16 values by
minimizing squared reconstruction error" — i.e. a reconstruction-optimal variant of the
NVFP4 block scale, applied to KV rather than weights. Same source documents portability:
"CUDA 11.8+ supports fp8 (=fp8_e4m3) and fp8_e5m2. ROCm (AMD GPU) supports fp8
(=fp8_e4m3)."

| KV dtype | A100 | H100/H200 | B200/B300/GB300 | RTX PRO 6000 | MI355X |
|---|:--:|:--:|:--:|:--:|:--:|
| BF16/FP16 | ✅ | ✅ | ✅ | ✅ | ✅ |
| `fp8_e4m3` | ✅ store | ✅ **FP8 attention math** (FA3) | ✅ (FlashInfer) | ✅ | ✅ (AITER) |
| `fp8_e5m2` | ✅ store | ✅ | ✅ | ✅ | ⚠️ n/p |
| INT8 | ✅ | ✅ | ✅ | ✅ | ⚠️ n/p |
| **`nvfp4`** | ❌ | ❌ | ✅ | ✅ ⚠️ | ❌ |

"Hardware FP4 tensor core acceleration requires Blackwell. On H100, vLLM can load
NVFP4-quantized model weights via Marlin software fallback, but `--kv-cache-dtype nvfp4`
for KV storage acceleration is a Blackwell-only feature"
[src](https://docs.vllm.ai/en/latest/features/quantization/quantized_kvcache/).
TRT-LLM corroborates: NVFP4 KV cache appears only in the sm100/103 row
[src](https://nvidia.github.io/TensorRT-LLM/features/quantization.html). ⚠️ **TO BE
VERIFIED**: whether NVFP4 KV works on sm120 — TRT-LLM's sm120 row lists FP8 KV only,
while community reports claim RTX PRO 6000 NVFP4 KV in vLLM. Test before relying on it.

### 8.2 FP8 KV is not just storage

"the entire attention computation (the QK and ScoreV matrix multiplications) [runs] in
FP8 … the KV cache itself is quantized; attention operations use quantized values rather
than dequantizing first"
[src](https://vllm-project.github.io/2026/04/22/fp8-kvcache.html). vLLM's doc adds:
"When using the Flash Attention 3 backend with FP8 KV cache, attention operations are
also performed in the quantized (FP8) domain. In this configuration, queries are
quantized to FP8 in addition to keys and values"
[src](https://github.com/vllm-project/vllm/blob/main/docs/features/quantization/quantized_kvcache.md).

Measured [src](https://vllm-project.github.io/2026/04/22/fp8-kvcache.html):

| Model | Output throughput | Total runtime | Median ITL |
|---|---:|---:|---:|
| Llama-3.1-8B | **+14.9 %** | −13.0 % | **−14.8 %** |
| gpt-oss-20b | +4.8 % | −4.6 % | — |

"decode cost … reduced to 54 % of its BF16 counterpart in the best cases". Accuracy:
"at most 1-2 points of accuracy degradation" on reasoning tasks for Qwen; long-context
recovers "97-98 % of the baseline AUC@128k" for Llama-3.3-70B and "94-98 %" for MoE
models, scaling to 1M tokens.

### 8.3 Scale calibration and layer skipping

Three calibration tiers
[src](https://github.com/vllm-project/vllm/blob/main/docs/features/quantization/quantized_kvcache.md):
default (all scales 1.0, `kv_cache_dtype="fp8"`); llm-compressor dataset calibration
(**recommended**); and per-attention-head scales (`q_scale=[num_heads]`,
`k/v_scale=[num_kv_heads]`) which are "currently available **only with the Flash
Attention backend**" and need llm-compressor.

Critically, **not every layer tolerates it**: "Some attention layer types (e.g.
sliding-window) are more sensitive to KV-cache quantization", handled by
`--kv-cache-dtype-skip-layers sliding_window` (or explicit indices). Both models in this
repo with sliding-window or hybrid attention — DeepSeek-V4.1-Flash (128-token SWA on
every layer) and Kimi-K3 (KDA + MLA) — are exactly the shapes that flag exists for.

A trap worth naming: a checkpoint's own `kv_cache_scheme` can **override an omitted**
`--kv-cache-dtype`, so "I didn't set it, therefore it's BF16" is false
[src](https://github.com/waired-ai/waired-agent/issues/1441) ⚠️ third-party issue, not
an upstream doc — verify against your build.

### 8.4 SGLang

`--kv-cache-dtype fp8_e4m3` "halves KV bytes per token; under PD both roles must match at
connect"; under DCP, "Explicit `tokenspeed_mla` force-rewrites `--kv-cache-dtype` to fp8;
the default `cutedsl_mla` serves either dtype"; and for large-scale Kimi-K3 presets,
"`--kv-cache-dtype fp8_e4m3` is load-bearing — bf16 KV does not fit 128 requests per
replica" [src](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3).

---

## 9. The repo's checkpoints

### 9.1 `deepseek-ai/DeepSeek-V4.1-Flash` — what `expert_dtype: "fp4"` actually means

**Answer: OCP MXFP4** — E2M1 elements, one UE8M0 scale per 32 elements along K. Not
NVFP4, not a DeepSeek-private format. Derived from three independent primary sources that
agree.

**(a) DeepSeek's own reference implementation**
[src](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/inference/model.py):

```python
fp8_block_size = 32  # one fp8 scale per 32x32 weight block / 32 activations
fp4_block_size = 32  # one fp4 scale per 32 elements along K
scale_fmt = "ue8m0"
scale_dtype = torch.float8_e8m0fnu
...
expert_dtype: Literal["fp4"] | None = "fp4"
```

and `kernel.py` defines `FP4 = "float4_e2m1fn"`, `FE8M0 = "float8_e8m0fnu"`
[src](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/inference/kernel.py).

**(b) The shipped safetensors headers** (read via HTTP range request on
`model-00008-of-00048.safetensors`, layer 5):

| Tensor | dtype | shape | Reading |
|---|---|---|---|
| `ffn.experts.N.w1.weight` | **I8** | [2304, 2560] | 2560 bytes = 5120 packed E2M1 nibbles |
| `ffn.experts.N.w1.scale` | **F8_E8M0** | [2304, 160] | 5120/32 = 160 → **1-D block-32, MXFP4 exactly** |
| `ffn.experts.N.w2.weight` | I8 | [5120, 1152] | logical [5120, 2304] |
| `ffn.experts.N.w2.scale` | F8_E8M0 | [5120, 72] | 2304/32 = 72 ✓ |
| `attn.wkv.weight` | **F8_E4M3** | [512, 5120] | |
| `attn.wkv.scale` | **F8_E8M0** | [16, 160] | 512/32 × 5120/32 → **2-D 32×32 tiles** |
| `ffn.shared_experts.w1.weight` | F8_E4M3 | [2304, 5120] | with [72, 160] E8M0 scale |
| `ffn.gate.weight` | BF16 | [384, 5120] | router stays BF16 |
| `attn_norm.weight` | BF16 | [5120] | |
| `hc_attn_fn` | F32 | [24, 20480] | hyper-connection coefficients |

**(c) Third-party confirmation.** vLLM's recipe: "Routed expert weights are MXFP4;
everything else is MXFP8 block-quantized, UE8M0 scales throughout. Embedding and LM head
are BF16" [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash). NVIDIA's card:
"converts the ordinary routed MoE experts from source **MXFP4** to NVFP4"
[src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4).

**A correction vLLM's prose glosses over.** The non-expert weights are **not** OCP MXFP8.
OCP MXFP8 is a 1-D block of 32 along K. DeepSeek uses a **2-D 32×32 tile** per scale
(scale shape [out/32, in/32]) — the successor to DeepSeek-V3's 128×128 FP32-scaled
blocks, with the scale narrowed to UE8M0. The *activations* are true MXFP8: `act_quant(x,
block_size=32, scale_fmt="ue8m0")` produces `S[M, N/32]`, 1-D block-32 with UE8M0 —
OCP-compliant. So the checkpoint is:

- routed experts: **OCP MXFP4** (1-D, 32, E8M0) — 543.6B logical params
- attention / shared experts / dense projections / Engram tables: **FP8 E4M3 with UE8M0
  scales on 32×32 2-D tiles** (a DeepSeek format, 8.008 bits/param)
- activations: **OCP MXFP8** → the expert GEMM is **W4A8 in MX formats**
- embeddings, LM head, routers, norms, hyper-connection coefficients: BF16/FP32

Byte accounting, published breakdown
[src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash):

| Component | Entries | Stored |
|---|---:|---:|
| Routed + DSpark experts (MXFP4) | 557.2B | 259.5 GiB |
| Engram tables (FP8) | 196.6B | 183.1 GiB |
| Attention, dense projections, routers (FP8) | 7.4B | 6.9 GiB |
| Embedding + LM head (BF16), norms (FP32) | 2.0B | 3.9 GiB |
| UE8M0 block scales | 23.6B | 21.9 GiB |
| **Total** | | **475.3 GiB** |

Independent check: the shipped `model.safetensors.index.json` reports
`total_size = 510,286,023,000` B = **475.2 GiB** — matches to 0.02 %.

My own arithmetic on the routed experts alone: 3 × 2304 × 5120 = 35.389M params/expert ×
384 × 40 = **543.6B params**; at 4.25 bits = **268.9 GiB**; at BF16 it would be 1012 GiB.

**KV cache is already FP4 in the base checkpoint.** "Combined with **FP4 main KV caching**
(E2M1 format, one E4M3 scale per 16 channels), these designs reduce the global KV cache
footprint to **890 bytes per token**"
[src](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) — re-fetched 2026-09-19, the
card carries both phrases verbatim. **890 B/token is the FP4-KV figure and it presumes the
Blackwell FP4-KV kernel** (720 B main + 170 B indexer, METHODOLOGY §8); it is *not* a
dtype-independent constant. Other KV dtypes are enumerated in
[models/deepseek41f/architecture.md](../models/deepseek41f/architecture.md) §5 — quote that
table, not 890, when planning a non-Blackwell GPU. `model.py` confirms:
"Compressed KV uses groups of 16 with E4M3 scales; the indexer uses 32 with E8M0." That
is an **NVFP4-shaped KV layout baked into the architecture**, not a serving option — so
`--kv-cache-dtype` has much less headroom here than on a normal model, and a non-Blackwell
GPU must emulate the FP4 KV path.

**Per-GPU execution of this checkpoint:**

| GPU | Expert GEMM | Engine + flag |
|---|---|---|
| B200/B300/GB200/GB300 | **native MXFP4 W4A8** | SGLang `--moe-runner-backend flashinfer_mxfp4` (auto-selected); vLLM default. SGLang adds `--enable-w4a4-mxfp4-megamoe` for W4A4 MegaMoE (high-throughput recipe only) |
| H100 / H200 | **W4A16 Marlin** (default) → or W4A8 via `--flashinfer-mxfp4-moe-precision fp8` (FlashInfer ≥0.6.18 Humming) → or `VLLM_DSV4_FP4_DEQUANT=1` re-encode to block-FP8 (2× weight memory, 1.4–1.5× prefill) | |
| H100/H200 alt. | pre-repackaged FP8 checkpoints `sgl-project/DeepSeek-V4-{Flash,Pro}-FP8` "unlock DP-attention + DeepEP and richer parallelism"; SM90 all-FP8 MegaMoE via `SGLANG_DSV4_FP4_EXPERTS=0` | ⚠️ V4 checkpoints; V4.1 equivalents **TO BE VERIFIED** |
| RTX PRO 6000 (sm120) | "runs Flash only with the FlashInfer MXFP4 MoE runner" | `--moe-runner-backend flashinfer_mxfp4` |
| MI355X | **native MXFP4** via Composable Kernel a8w4 | vLLM `--moe-backend aiter` + `VLLM_ROCM_USE_AITER=1 VLLM_ROCM_USE_AITER_MOE=1`; `aiter_triton_mxfp4_bf16` pins the slower a16w4 Triton path instead |
| A100 | ⚠️ not a listed target in any vendor recipe | — |

Verified MI355X server command (InferenceX #3058, commit 559ef756, 4× MI355X)
[src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash):

```bash
export VLLM_ROCM_USE_AITER=1 VLLM_ROCM_USE_AITER_MOE=1
export AITER_TRITON_LOG_LEVEL=ERROR VLLM_USE_BREAKABLE_CUDAGRAPH=1
vllm serve deepseek-ai/DeepSeek-V4.1-Flash \
  --tensor-parallel-size 4 --language-model-only \
  --tokenizer-mode deepseek_v41 --reasoning-parser deepseek_v41 \
  --moe-backend aiter --gpu-memory-utilization 0.9 \
  --speculative-config '{"method":"dspark","num_speculative_tokens":5,...}' \
  --max-model-len 1048576 --max-num-seqs 128 \
  --max-cudagraph-capture-size 1024 --max-num-batched-tokens 16384
```

AMD-specific gotchas from the same source: `VLLM_USE_BREAKABLE_CUDAGRAPH=1` is mandatory
("DeepSeek-V4.1-Flash does not support torch.compile, and the ROCm sparse SWA backend only
supports uniform-batch CUDA graphs"), and DSpark must run with
`enable_adaptive_verification:false` because "vLLM currently refuses the true flag" on
ROCm.

### 9.2 `nvidia/DeepSeek-V4.1-Flash-NVFP4`

Released 2026-09-16, quantized with `nvidia-modelopt v0.47.0rc0`, source revision
`dba1be0a40aa45a94ad051997016db3960a90277`
[src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4).

What changed, verbatim: "This model converts the ordinary routed MoE experts from source
MXFP4 to NVFP4 weights and activations (W4A4), with group size 16. The converted
projections are `w1`, `w2`, and `w3` for 384 experts across 40 layers. Attention, shared
experts, vision, Engram lookup tables, MTP/DSpark, and other excluded components retain
their source precision, including MXFP8 where applicable."

The `config.json` in this repo confirms it: `"moe_quant_algo": "NVFP4"`, `"group_size":
16`, `"quant_algo": "MIXED_PRECISION"`, `"ignore": ["*.attn.*",
"*.ffn.shared_experts.*", "head", "mtp.*"]`, `"kv_cache_quant_algo": null`, and all 40
`layers.N.ffn.experts` entries listed individually with `group_size: 16`. Note
`weight_block_size: [32,32]` and `scale_fmt: "ue8m0"` **survive** in the config — they
describe the FP8 remainder, not the experts.

Size: "The finer NVFP4 scale layout increases checkpoint size from approximately 476 GiB
to **492 GiB**. The export contains 48 safetensors shards." Verified by computation:
543.6B expert params × (1/16 − 1/32) **bytes** of extra scale = 0.03125 B/param ×
543,581,798,400 = 16.99 GB = **+15.82 GiB** — matching the
stated +16 GiB exactly. This is the 5.9 % NVFP4 tax from §2.3, made concrete.
Reconciled to METHODOLOGY §8's pinned checkpoint total: **527.27 GB = 491.06 GiB**
(the card's "492 GiB" is that number rounded up). Use 527.27 GB / 491.1 GiB in sizing.
NVFP4 here is **0.5625 B/param** (+12.5 %, §2.3) — Open question 20 is closed.

The card's block count is an exact cross-check on §9.1's parameter arithmetic:
16,986,931,200 MXFP4 blocks × 32 elements = **543,581,798,400** = precisely
3 × 2304 × 5120 × 384 × 40. The two independent routes agree to the last digit.

Conversion quality: "All 16,986,931,200 weight blocks passed lossless conversion,
preserving dequantized weight values with signed-zero canonicalization. Activation scales
were calibrated; 18 of 46,080 projection entries used fallback scales. Lossless weight
conversion does not imply identical inference outputs."

**Engine status as of 2026-09-19:**

| Engine | Status | Evidence |
|---|---|---|
| **SGLang** | ✅ validated | "commit `da64c5cbb8cf6bfd39be19da43573fdfd484c43a`. This configuration passed loading, generation, reasoning and tool-call parsing, and image-input smoke tests." Image `lmsysorg/sglang:dev-cu13-dsv41`, 4× GB300, TP4. "The tested build automatically selects `flashinfer_trtllm_routed` for the NVFP4 experts." |
| **vLLM** | ✅ validated, **and it is the engine the published accuracy table was produced with** | "Both runtimes have been tested with this NVFP4 checkpoint on GB300. The benchmark results below were obtained with vLLM." Image `vllm/vllm-openai:deepseekv41-flash-0909`, 4× GB300, TP4, text-only |
| **TensorRT-LLM** | ⚠️ **not listed** | The card's "Supported Runtime Engine(s)" names only vLLM and SGLang. TRT-LLM supports NVFP4 + NVFP4 KV on sm100/103 generically, but there is **no published DeepSeek-V4.1 support claim**. **TO BE VERIFIED** |
| Hardware | **Blackwell only** | "Supported Hardware Microarchitecture Compatibility: NVIDIA Blackwell" |

So the task brief's "validated on SGLang per NVIDIA" is correct but understated: **vLLM
is equally validated and is the engine that produced the numbers.**

Caveats the card states: "DSpark tensors are preserved, but speculative decoding was not
exercised in the reported validation"; "The vLLM example is text-only; image serving with
vLLM requires a multimodal configuration"; "Thinking is off by default in this SGLang
build" — send `reasoning_effort: "max"`.

SGLang launch [src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4):

```bash
python -m sglang.launch_server --model-path nvidia/DeepSeek-V4.1-Flash-NVFP4 \
  --tp 4 --context-length 1048576 \
  --reasoning-parser deepseek-v41 --tool-call-parser deepseekv41 \
  --chunked-prefill-size 4096 --max-running-requests 16
```

vLLM launch (4× GB300, TP4): `--tokenizer-mode deepseek_v41 --reasoning-parser
deepseek_v41 --language-model-only --max-model-len 1048576 --max-num-seqs 32
--max-num-batched-tokens 8192 --enable-chunked-prefill --no-enable-prefix-caching`.

**Should you use it over the MXFP4 base?** On Blackwell, `flashinfer_trtllm_routed` is a
well-trodden NVFP4 path and the accuracy is +0.40 points mean (§7.1) — i.e.
**accuracy-neutral**, not an accuracy win. **NVIDIA publishes no throughput or latency
comparison against the MXFP4 base for this checkpoint**, so there is no speedup to claim
either; the card's benchmark section reports accuracy only. Against that: +16
GiB of weights (527.27 GB vs 510.29 GB), Blackwell-lock (worthless on MI355X without a re-conversion round trip),
and the base checkpoint already has first-class Blackwell, Hopper, sm120 **and** MI355X
recipes. For a Blackwell-only fleet it is a reasonable default; for a mixed fleet the base
MXFP4 checkpoint is the one artifact that runs everywhere.

### 9.3 `moonshotai/Kimi-K3`

2.8T params, 896 routed experts (16 active) + 2 shared, 93 layers, KDA + Gated MLA,
1,048,576 context. "Kimi K3 applies quantization-aware training from the SFT stage
onward, using **MXFP4 weights with MXFP8 activations** for broad hardware compatibility"
[src](https://huggingface.co/moonshotai/Kimi-K3).

`quantization_config` from the repo config:

```json
"format": "mxfp4-pack-quantized",
"quant_method": "compressed-tensors",
"quantization_status": "compressed",
"weights": {"num_bits": 4, "group_size": 32, "type": "float",
            "strategy": "group", "symmetric": true,
            "scale_dtype": "torch.uint8", "observer": "minmax"},
"input_activations": null, "output_activations": null,
"ignore": ["re:.*self_attn.*", "re:.*shared_experts.*",
           "re:.*mlp\\.(gate|up|gate_up|down)_proj.*",
           "re:.*lm_head.*", "re:.*vision_tower.*", "re:.*mm_projector.*"]
```

`scale_dtype: torch.uint8` is E8M0 stored as a byte; `input_activations: null` means the
checkpoint carries **no activation quantization parameters** — activation precision is the
serving engine's choice (hence A8W4 vs A4W4 being a runtime env var on MI355X, §6.3).

Verified against the shipped safetensors header (shard 20 of 96):

| Tensor | dtype | shape | Reading |
|---|---|---|---|
| `block_sparse_moe.experts.N.w1.weight_packed` | **U8** | [3072, 1792] | logical [3072, 3584] |
| `block_sparse_moe.experts.N.w1.weight_scale` | **U8** | [3072, 112] | 3584/32 = 112 → **MXFP4, group 32** ✓ |
| `block_sparse_moe.experts.N.w2.weight_packed` | U8 | [3584, 1536] | logical [3584, 3072] |
| `block_sparse_moe.experts.N.w2.weight_scale` | U8 | [3584, 96] | 3072/32 = 96 ✓ |
| `block_sparse_moe.routed_expert_up_proj.weight` | **BF16** | [7168, 3584] | LatentMoE projection |
| `block_sparse_moe.routed_expert_down_proj.weight` | **BF16** | [3584, 7168] | |
| `block_sparse_moe.shared_experts.{gate,up,down}_proj` | **BF16** | 6144 intermediate | ignored by design |
| `self_attn.{q_a,q_b,kv_a,kv_b,o,g}_proj.weight` | **BF16** | | ignored by design |
| `block_sparse_moe.gate.weight` | BF16 | [896, 7168] | |

So the **Stable LatentMoE** shape is explicit: routed experts operate in a 3584-dim latent
(`routed_expert_hidden_size`) with 3072 intermediate, entered and left via BF16 7168↔3584
projections. Only the 3-matrix expert core is 4-bit.

Arithmetic: 3 × 3072 × 3584 = 33.03M params/expert × 896 × 92 MoE layers = **2.723T
params** at 4.25 bits (0.53125 B/param) = **1,446.2 GB = 1,346.6 GiB**. Measured repo
safetensors total = **1,560.9 GB = 1,453.7 GiB** (96 shards, HF API; the pinned
checkpoint total in METHODOLOGY §8), leaving ~107 GiB of BF16 attention, shared experts,
LatentMoE projections, embeddings and norms. At BF16 the experts alone would be
**5,071.5 GiB** — which is exactly why the repo README's note holds: it fits one **8×B300
node (2,144 GB, i.e. 268 GB/GPU on HGX/DGX B300 and AWS `p6-b300`)** only because the
checkpoint is natively MXFP4. (Corrected 2026-09-19: this sentence previously read
"2304 GB HBM". 8 × 288 GB = 2,304 GB is the **GB300 NVL72** die figure, not an HGX B300
node; the README was corrected to 2,144 GB and METHODOLOGY §8 pins the same.)

**Per-GPU execution:**

| GPU | Path | Source |
|---|---|---|
| B200/B300/GB200/GB300 | **native MXFP4 W4A8**, FlashInfer trtllm-gen SiTU. "Leave `--moe-runner-backend` unset on Blackwell: FlashInfer MXFP4 (W4A8, official trtllm-gen SiTU kernels) is selected" | SGLang cookbook |
| H100 / H200 | **Marlin W4A16** — "H100/H200 pin Marlin", "Marlin + FlashMLA". H100 needs 4×8 = TP32/EP32; "least post-weight headroom (80 GB)" | SGLang cookbook |
| RTX PRO 6000 (sm120) | ⚠️ **not a listed platform**. FlashInfer 0.6.18 ships SM12x MXFP4 MoE, but 96 GB × 8 = 768 GB cannot hold 1454 GiB | **TO BE VERIFIED** |
| MI355X / MI350X | **native MXFP4** via AITER SiTU v2. vLLM: `VLLM_ROCM_USE_AITER_MOE_SITUV2=1` enables the "a4w4 FlyDSL MoE path"; "Unset it or set 0 for the default a16w4 path". SGLang emits `SGLANG_USE_AITER=1 SGLANG_AITER_K3_OPT=1 AITER_FLYDSL_FORCE=1 AITER_SITUV2_A8W4=1` | vLLM recipe + SGLang cookbook |
| Ascend 950 | separate W4A8 checkpoint `Eco-Tech/Kimi-K3-w4a8` (ModelScope), 1.49 TB | vLLM recipe |
| A100 | ❌ not viable at any TP in this repo's scope | — |

A contradiction worth flagging: vLLM's recipe says `VLLM_ROCM_USE_AITER_MOE_SITUV2=1`
selects **a4w4** and warns "Do not set `AITER_SITUV2_A8W4=1`; AITER checks that flag first
and it would override a4w4" [src](https://recipes.vllm.ai/moonshotai/Kimi-K3), while
SGLang's cookbook ships `AITER_SITUV2_A8W4=1` as "The performance default the cell ships"
and measures A8W4 *faster* (537.3 vs 530.8 tok/s)
[src](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3). Both are
internally consistent — the two engines simply default to opposite ends of the same
trade. ⚠️ **TO BE VERIFIED**: which default wins on your traffic; measure both.

**`nvidia/Kimi-K3-NVFP4` exists** (released 2026-08-14, `nvidia-modelopt v0.45.0`, 30,311
downloads) [src](https://huggingface.co/nvidia/Kimi-K3-NVFP4). "The source MXFP4
routed-expert weights were converted to NVFP4 using `input_scale=1.0`, while the supported
attention projection weights in KDA and MLA were quantized to 128×128 per-block FP8" —
i.e. it also drops attention from BF16 to FP8, which is where most of its memory saving
comes from. Validated on **8× B300** for both vLLM (`--quantization modelopt_mixed
--moe-backend flashinfer_trtllm`, requires a bundled `sitecustomize.py` patch pending
vLLM PRs #50617 / #52406 / #52405) and SGLang (`lmsysorg/sglang:dev-dev-kimi-k3-nvfp4`,
pending SGLang PR #35077). SGLang's note is emphatic: "`--moe-runner-backend
flashinfer_trtllm` is required rather than optional … `flashinfer_cutlass` has no SiTU
kernel for the routed experts."

The practical concurrency cost of NVFP4 on this model is published: B300 1×8 KDA state
pool admission caps are **101 / 68 / 91 / 60** concurrent requests for MXFP4-NOSPEC /
MXFP4-DSPARK / NVFP4-NOSPEC / NVFP4-DSPARK
[src](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3) — NVFP4 costs
**~10 % of peak concurrency** on both spec settings, consistent with its larger weights
leaving less room for state.

Why those caps are so low is the **state-slot multiplier** of METHODOLOGY §2, not the KV
cache: Kimi-K3's KDA layers hold a fixed per-sequence state of **428.6 MiB per slot**, and
SGLang allocates **S = 5** slots per request → **2.25 GB per request** before a single KV
token is stored. 101 concurrent requests is therefore ~227 GB of pure recurrent state
across the node. Any concurrency figure for this model that multiplies the per-slot state
by 1 instead of S is wrong by 5×.

### 9.4 `Qwen/Qwen3.8-27B`

Dense 27B VLM, 64 layers, hidden 5120, FFN 17408, hybrid `16 × (3 × Gated DeltaNet → 1 ×
Gated Attention)`, head_dim 256 with 24 Q / 4 KV heads, 262,144 native context extensible
to 1M, MTP head [src](https://huggingface.co/Qwen/Qwen3.8-27B). Base repo is **BF16** —
the config in this repo has `"dtype": "bfloat16"` and **no `quantization_config`**.

Weight footprint. **Corrected 2026-09-19:** this table previously applied a flat
bytes/param to a round "27B", giving BF16 50.3 GiB. Both inputs were wrong. The model is
**27.78 B params (LM 26.90 B)**, and every quantized variant here is a *mixed-precision*
checkpoint — NVIDIA's NVFP4 build quantizes the MLPs and `lm_head` and leaves the
self-attention and linear-attention layers at FP8 — so a flat rate is the wrong method
(METHODOLOGY §1). The values below are METHODOLOGY §8's pinned per-tensor-group totals,
converted to GiB with `python3`:

| Format | GB (10⁹) | GiB (2³⁰) | Source |
|---|---:|---:|---|
| BF16 (base repo) | **55.56** | **51.74** | pinned, METHODOLOGY §8 |
| FP8 block-128 (`Qwen/Qwen3.8-27B-FP8`) | **30.87** | **28.75** | pinned, METHODOLOGY §8 |
| NVFP4 + FP8 attention (`nvidia/Qwen3.8-27B-NVFP4`) | **21.92** | **20.42** | pinned, METHODOLOGY §8 |
| MXFP4 + FP8 attention (hypothetical) | ≈ 21.19 | ≈ 19.73 | `est.` — same tensor group at 0.53125 instead of 0.5625 B/param; no such checkpoint exists |
| INT4 g128 (AWQ/GPTQ) | **19.45** | **18.11** | pinned, METHODOLOGY §8 |

The pinned totals imply a quantized tensor group of **23.4–24.7 B params** (84–89 % of the
model) with the remainder at BF16 — consistent across all three pins to ±5 %, which is the
cross-check that they were summed per group rather than flat-rated. Fit, minimum GPU count
and KV budget per GPU are sizing questions and live in
`research/models/qwen3827b/<gpu>.md`, not here.

Available quantized variants as of 2026-09-19:

| Repo | Format | Recipe | Notes |
|---|---|---|---|
| `Qwen/Qwen3.8-27B-FP8` | FP8 | "fine-grained fp8 quantization with block size of 128" | first-party; "performance metrics are nearly identical to those of the original model"; "compatible with Hugging Face Transformers, vLLM, SGLang, TokenSpeed" |
| `nvidia/Qwen3.8-27B-NVFP4` | **mixed NVFP4 + FP8** | "NVFP4 quantization was applied to the MLP layers and language model head (`lm_head`), while FP8 quantization was applied to the self-attention and linear-attention layers"; ModelOpt **Local-Hessian**, 2048 calibration samples from Nemotron-Post-Training-Dataset-v3; `nvidia-modelopt v0.48.0`, released 2026-09-08; tested on **GB300** with vLLM | Blackwell only |
| `unsloth/Qwen3.8-27B-FP8`, `unsloth/Qwen3.8-27B-NVFP4` | FP8 / NVFP4 | "Unsloth Dynamic V3.0" | ⚠️ third-party |
| `RadixArk/Qwen3.8-27B-NVFP4` | NVFP4 W4A4 | ModelOpt "mixed NVFP4 W4A4 recipe" | ⚠️ third-party |
| `QUASAR-QAT/Qwen3.8-27B-QUASAR-NVFP4` | NVFP4 | **QAT** via QUASAR | ⚠️ third-party, unverified |
| `huginnfork/Qwen3.8-27B-FP8` | FP8 E4M3 | "MLPs only, while the entire self_attn path, linear_attn block, vision tower, lm_head and MTP head stay in bf16" | ⚠️ third-party |
| `esatapedico/Qwen3.8-27B-NVFP4-MTP-GGUF` | GGUF | — | ⚠️ third-party |

⚠️ **TO BE VERIFIED**: no first-party AWQ or GPTQ Qwen3.8-27B repo was found. Qwen has
historically shipped AWQ variants a few weeks after release; assume none exists today and
produce one with llm-compressor (W4A16, group 128) if you need it. Also: no NVFP4 or FP8
accuracy table is published on `nvidia/Qwen3.8-27B-NVFP4` — it lists the benchmark *names*
(GPQA Diamond, Terminal-Bench, AA-LCR, MMMU-Pro, SciCode, IFBench) without scores.

NVIDIA's own serve line [src](https://huggingface.co/nvidia/Qwen3.8-27B-NVFP4):

```bash
vllm serve nvidia/Qwen3.8-27B-NVFP4 --port 8000 --kv-cache-dtype fp8_e4m3
```

Note NVFP4 weights paired with **FP8** (not NVFP4) KV — even NVIDIA does not reach for
NVFP4 KV here.

**The format choice for this model is unusually easy.** At 27B dense, BF16 already fits
one H100/H200/B200/MI355X. Quantization buys concurrency (more KV budget), not fit. On
Hopper and A100, FP8 (Hopper) or INT8/W4A16 (A100) are the only paths with real
acceleration. On Blackwell, NVFP4 W4A4 gets the full 4× FP4 tensor-core rate — this is the
one model in the repo where the compute gain is uncomplicated, because there is no MoE
routing to hide behind.

### 9.5 Cross-model summary

| | DeepSeek-V4.1-Flash | …-NVFP4 | Kimi-K3 | Qwen3.8-27B | Marlin-2B |
|---|---|---|---|---|---|
| Base precision | mixed | mixed | mixed | **BF16** | **BF16** |
| Expert/MLP weights | **MXFP4** g32 E8M0 | **NVFP4** g16 E4M3 | **MXFP4** g32 E8M0 (QAT) | BF16 | BF16 (dense, no MoE) |
| Attention weights | FP8 32×32 UE8M0 | FP8 32×32 UE8M0 | **BF16** | BF16 | BF16 |
| Activations | MXFP8 (A8) | NVFP4 (A4) | MXFP8 (A8) | BF16 | BF16 |
| Shared experts | FP8 | FP8 (ignored) | BF16 (ignored) | — | — |
| Embed / LM head | BF16 | BF16 | BF16 (ignored) | BF16 | BF16 (untied copy on disk) |
| KV cache | **FP4 E2M1, E4M3/16** native, **890 B/tok with the Blackwell FP4-KV kernel** | same | MLA (24 of 93 layers) + KDA state × **S = 5** | GQA (16 full-attn layers) + GDN state | GQA (6 layers) + GDN state |
| Checkpoint size | **510.29 GB / 475.2 GiB** | **527.27 GB / 491.1 GiB** | **1,560.9 GB / 1,453.7 GiB** | **55.56 GB / 51.74 GiB** | **5.444 GB / 5.070 GiB** |
| Runs natively on | B*, RTX PRO 6000, MI355X | **Blackwell only** | B*, MI355X | everything | everything |
| Hopper path | Marlin W4A16 / W4A8 / FP8 re-encode | Marlin W4A16 | **Marlin W4A16** | **FP8 native** | BF16 native |
| A100 path | ❌ | ❌ | ❌ | BF16 / INT8 / W4A16 | BF16 native |

All checkpoint sizes are METHODOLOGY §8's pinned totals (GB from `index.json` /
HF API `usedStorage`; GiB = GB × 10⁹ / 2³⁰, recomputed with `python3`).
Marlin-2B's blocker is not precision — it is
`architectures: ["MarlinForConditionalGeneration"]`, which no engine registers; see
[models/marlin2b/architecture.md](../models/marlin2b/architecture.md) §8.

### 9.6 Checkpoint × GPU format-support matrix

Added 2026-09-19 — the Scope note at the top of this document has referenced this section
since the first draft, but it was never written. **This is a format-support matrix, not a
sizing table.** It answers one question per cell: *does the checkpoint's stored format
reach a tensor/matrix core on this GPU, and if not, what does the engine do instead?* It
says nothing about whether the model **fits**, at what TP, or how fast it runs — those are
per-(model, GPU) questions and live in **`research/models/<exp>/<gpu>.md`**, written in the
next phase. Several cells below are marked native on a GPU the model cannot fit on at any
GPU count in scope; that is not a contradiction, it is the point of separating the two
questions.

Cell vocabulary:

| Cell | Meaning |
|---|---|
| **native** | The stored format feeds the tensor/matrix core directly. Full memory *and* compute win. |
| **requant** | Converted to a different 4-bit format once at load, then native. Memory win kept, compute win kept, **you inherit the target format's accuracy** (§3.2). |
| **dequant** | Unpacked to BF16/FP8 in registers before the MMA (W4A16 / W8A16). **Memory win kept, compute win lost** (§1). |
| **❌** | Does not run, or is not a listed target in any vendor recipe. |

| Checkpoint (stored format) | A100 SM80 | H100 SM90 | H200 SM90 | B200 SM100 | B300 HGX/DGX/p6 SM103 | GB300 NVL72 SM103 | RTX PRO 6000 SM120 | MI355X gfx950 |
|---|---|---|---|---|---|---|---|---|
| **`deepseek-ai/DeepSeek-V4.1-Flash`** — MXFP4 g32 experts + FP8 32×32 UE8M0 rest | dequant ⚠️ᵃ | dequant (Marlin W4A16); W4A8 or FP8 re-encode opt-in ᵇ | dequant (Marlin W4A16); W4A8 or FP8 re-encode opt-in ᵇ | **native** W4A8 | **native** W4A8 | **native** W4A8 | **native** W4A8 ⚠️ᶜ | **native** W4A8 (AITER CK a8w4) |
| **`nvidia/DeepSeek-V4.1-Flash-NVFP4`** — NVFP4 g16 experts + FP8/MXFP8 rest | dequant ⚠️ᵈ | dequant (Marlin W4A16) ⚠️ᵈ | dequant (Marlin W4A16) ⚠️ᵈ | **native** W4A4 | **native** W4A4 | **native** W4A4 (validated) | dequant ⚠️ᵉ | requant → MXFP4 ⚠️ᶠ |
| **`moonshotai/Kimi-K3`** — MXFP4 g32 experts + BF16 attention | ❌ ᵍ | dequant (Marlin W4A16) | dequant (Marlin W4A16) | **native** W4A8 | **native** W4A8 | **native** W4A8 | **native** W4A8 ⚠️ʰ | **native** W4A8 (AITER SiTU v2) |
| **`Qwen/Qwen3.8-27B`** — BF16 | **native** | **native** | **native** | **native** | **native** | **native** | **native** | **native** |
| **`Qwen/Qwen3.8-27B-FP8`** — FP8 E4M3, block-128 | dequant (W8A16) ⁱ | **native** | **native** | **native** ʲ | **native** ʲ | **native** ʲ | **native** ⚠️ᵏ | **native** (OCP-FP8) |
| **`nvidia/Qwen3.8-27B-NVFP4`** — NVFP4 g16 MLP + `lm_head`, FP8 attention | dequant ⚠️ᵈ | dequant (Marlin W4A16) | dequant (Marlin W4A16) | **native** W4A4 | **native** W4A4 | **native** W4A4 (tested) | **native** W4A4 ˡ | requant → MXFP4 |
| **Qwen3.8-27B INT4 W4A16** (AWQ/GPTQ g128) ⚠️ᵐ | dequant (Marlin) | dequant (Machete) | dequant (Machete) | dequant (Marlin) | dequant (Marlin) | dequant (Marlin) | dequant (Marlin) | dequant (Triton a16w4) ⁿ |
| **`NemoStation/Marlin-2B`** — BF16, all 618 tensors | **native** ᵒ | **native** ᵒ | **native** ᵒ | **native** ᵒ | **native** ᵒ | **native** ᵒ | **native** ᵒ | **native** ᵒ |

a. Ampere has no FP8 GEMM (TRT-LLM's Ampere row is W4A16-only, §5.1), so the FP8 32×32
   non-expert weights run W8A16 and the MXFP4 experts run Marlin W4A16. The checkpoint's
   native FP4 KV path must also be emulated (§9.1). **Not a listed target in any vendor
   recipe** — assume it loads and is slow (Open question 9).
b. `--flashinfer-mxfp4-moe-precision fp8` (FlashInfer ≥ 0.6.18 Humming) gives W4A8;
   `VLLM_DSV4_FP4_DEQUANT=1` re-encodes experts to block-FP8 for FP8 tensor cores at 2×
   the weight memory and 1.4–1.5× prefill (§6.2). Both keep the checkpoint's 4-bit
   *storage* only in the first case.
c. SGLang serves it via `--moe-runner-backend flashinfer_mxfp4`. TRT-LLM's sm120 row omits
   FP8 block-scaling, which the non-expert weights need — Open question 4.
d. NVFP4 falls back to Marlin W4A16 on Ampere/Hopper (§5), but the model card scopes the
   checkpoint to "NVIDIA Blackwell". Format-runnable, vendor-unsupported.
e. **Disagreement, stated per METHODOLOGY §8.** METHODOLOGY §8 pins RTX PRO 6000 as
   "NVFP4 MoE grouped-GEMM broken → Marlin W4A16"; this document's §5 cites FlashInfer
   PR #2898 closing the SM120 fused-MoE gap. The two are reconcilable only if the fix
   landed for MXFP4 `b12x_fused_moe` and not for the NVFP4 routed-expert path. Planning
   value: **assume dequant** until measured.
f. `quark_mxfp4` converts NVFP4 → MXFP4 at load (10–55 s, §3.2) — but this specific
   checkpoint is not among the models AMD validated the path on.
g. Not viable at any TP in this repo's scope (§9.3).
h. Format is supported (FlashInfer 0.6.18 ships SM12x MXFP4 MoE) but Kimi-K3 is not a
   listed platform for sm120 in any recipe (Open question 8).
i. Ampere has FP8 *storage* but no FP8 tensor core; W8A16 only.
j. Re-expressed to Blackwell's MXFP8 recipe (E4M3 + UE8M0 scales), not run bit-identically
   — §4.2 footnote 1.
k. FP8 per-tensor is native on sm120; **block-scaled** FP8 is absent from TRT-LLM's sm120
   row (§5.1). Engine-dependent.
l. The sm120 NVFP4 caveat in note (e) is a *MoE grouped-GEMM* problem. Qwen3.8-27B is
   dense, so the plain NVFP4 linear path applies and this cell is native.
m. ⚠️ **No first-party AWQ/GPTQ repo exists** as of 2026-09-19 (Open question 14). This
   row describes a checkpoint you would produce yourself with llm-compressor (W4A16,
   group 128).
n. Marlin and Machete are CUDA-only; AMD gets the Triton/AITER `a16w4` path (§4.2
   footnote 4). There is no INT4 tensor core on **any** GPU in scope.
o. Format support is total and uninteresting for a BF16 checkpoint. Marlin-2B's real
   blocker is engine registration, not precision — see §9.5.

`nvidia/Kimi-K3-NVFP4` is not a repo experiment and has no row; its per-GPU story is the
`…-Flash-NVFP4` row with Blackwell-only validation on 8× B300 (§9.3).

---

## 10. Engine flag reference

### 10.1 vLLM

| Goal | Flag |
|---|---|
| FP8 W8A8 | `--quantization fp8` (or checkpoint-detected) |
| ModelOpt NVFP4 | `--quantization modelopt_fp4`; mixed → `--quantization modelopt_mixed` |
| ModelOpt MXFP8 | `--quantization modelopt_mxfp8`; pair `--linear-backend flashinfer_trtllm` on SM100 |
| Pick NVFP4 GEMM backend | `--linear-backend {cutlass,flashinfer_cutlass,flashinfer_cutedsl,flashinfer_trtllm,flashinfer_cudnn,marlin}` (replaces `VLLM_NVFP4_GEMM_BACKEND`) |
| Per-scheme backend override | `--kernel-config '{"linear_backend_per_quant":{"nvfp4_w4a16":"humming"}}'` |
| MoE backend | `--moe-backend {flashinfer_trtllm,aiter,aiter_triton_mxfp4_bf16,…}` |
| Quark MXFP4/MXFP6 | `--quantization quark` (checkpoint-detected) |
| FP8 KV | `--kv-cache-dtype fp8` \| `fp8_e4m3` \| `fp8_e5m2` |
| NVFP4 KV (Blackwell) | `--kv-cache-dtype nvfp4` \| `nvfp4_4over6` \| `nvfp4_ds_mla` |
| Protect sensitive layers | `--kv-cache-dtype-skip-layers sliding_window` (or indices) |
| Hopper MXFP4→FP8 re-encode | `VLLM_DSV4_FP4_DEQUANT=1` |
| ROCm AITER | `VLLM_ROCM_USE_AITER=1 VLLM_ROCM_USE_AITER_MOE=1`; K3 a4w4: `VLLM_ROCM_USE_AITER_MOE_SITUV2=1` |
| ROCm CUDA-graph workaround | `VLLM_USE_BREAKABLE_CUDAGRAPH=1` |

### 10.2 SGLang

| Goal | Flag |
|---|---|
| Quantization | `--quantization {fp8,mxfp4,blockwise_int8,w8a8_int8,w8a8_fp8,awq,awq_marlin,gptq_marlin,compressed-tensors,quark,quark_mxfp4,modelopt_fp8,modelopt_fp4,nvfp4_online,petit_nvfp4,gguf,auto-round}` |
| MoE runner | `--moe-runner-backend {flashinfer_mxfp4,flashinfer_trtllm,flashinfer_trtllm_routed,flashinfer_cutlass,deep_gemm,b12x}` |
| Hopper MXFP4 W4A8 | `--flashinfer-mxfp4-moe-precision fp8` (FlashInfer ≥ 0.6.18) |
| Blackwell W4A4 MegaMoE | `--enable-w4a4-mxfp4-megamoe` (high-throughput recipe only) |
| Force all-FP8 experts on SM90 | `SGLANG_DSV4_FP4_EXPERTS=0` + FP8 checkpoint |
| Shared-expert fusion as MXFP4 | `--enable-flashinfer-mxfp4-moe-shared-expert-fusion` ⚠️ flag name from prose |
| FP4 indexer (DeepSeek-V4) | `--enable-deepseek-v4-fp4-indexer` |
| FP8 KV | `--kv-cache-dtype fp8_e4m3` |
| Mamba/SSM state dtype | `--mamba-ssm-dtype bfloat16` |
| AMD K3 MoE | `SGLANG_USE_AITER=1 SGLANG_AITER_K3_OPT=1 AITER_FLYDSL_FORCE=1 AITER_SITUV2_{A8W4,A4W4}=1` |
| AMD MoRI dispatch dtype | `SGLANG_MORI_DISPATCH_DTYPE=mxfp8` |

### 10.3 Measured flag effects worth knowing

- **Shared-expert fusion on Blackwell** (GB200 TP4, DeepSeek-V4): routing the shared
  expert "as one extra MXFP4 expert through the same trtllm-gen MoE kernel … (~4 fewer
  kernel launches and 2 fewer stream syncs per MoE layer). The shared expert is
  requantized from FP8 to MXFP4 at load time. Measured …: gsm8k and AIME25 accuracy on
  par with the unfused baseline; **Mean TTFT −13 % to −21 % and P99 ITL −15 % to −53 %**
  at QPS 1-8 with neutral throughput"
  [src](https://docs.sglang.io/cookbook/autoregressive/DeepSeek/DeepSeek-V4). A format
  *downgrade* (FP8 → MXFP4) that is a large latency win, because it removes launches.
- **Hopper FP8-checkpoint route beats format purity**: converted FP8 checkpoints "unlock
  DP-attention + DeepEP and richer parallelism (e.g. Pro TP=16 across 2 nodes)" — same
  source. On Hopper, the parallelism you unlock is usually worth more than the bits you
  save.

---

## 11. Choosing a format

Ordered by the first question that actually binds.

1. **Does the model fit at all?** Kimi-K3 at BF16 is ~5.4 TB of experts — MXFP4 is not an
   optimization, it is the only reason the model exists in the open. Take the native
   format.
2. **Is the checkpoint's native format natively accelerated on your GPU?** If yes, stop.
   Transcoding a QAT 4-bit checkpoint gains ~0.1–0.4 accuracy points (§7.1) and costs you
   portability.
3. **Are you prefill/compute-bound or decode/bandwidth-bound?** Compute-bound on Hopper
   with a 4-bit checkpoint → spend memory to get FP8 tensor cores (1.4–1.5×, §6.2).
   Bandwidth-bound → keep the 4 bits; the FP8 route costs you 36 % of your KV budget for
   a 1.12× decode gain.
4. **Mixed fleet?** MXFP4 is the only format in this document that runs natively on both
   Blackwell and CDNA4. NVFP4 is a Blackwell commitment.
5. **Quantizing it yourself, from BF16?** NVFP4 with calibration (Local-Hessian or
   equivalent) on Blackwell; FP8 block-128 on Hopper; INT8 W8A8 or W4A16-AWQ on Ampere.
   Do **not** RTN a dense model to MXFP4 and ship it — §6.4's GSM8K 62.55 % is what that
   looks like.
6. **KV cache**: turn on `fp8_e4m3` with calibrated scales and skip sliding-window layers
   before you reach for 4-bit KV. It is +15 % throughput and −15 % ITL for 1–2 accuracy
   points (§8.2), and it is available on every GPU here except A100-with-FP8-math.

---

## Open questions

All **⚠️ TO BE VERIFIED** items, consolidated.

1. **RTX PRO 6000 Blackwell: are the published TFLOPS sparse or dense?** NVIDIA's product
   page gives FP4 4 PFLOPS / FP8 2 PFLOPS / FP16 1 PFLOP with no sparsity footnote. This
   document halves them by analogy with RTX-class marketing convention. If they are
   already dense, every RTX PRO 6000 compute figure here doubles. *Method: find a
   Blackwell workstation architecture whitepaper or a measured CUTLASS GEMM roofline.*
   **Status 2026-09-19:** the page was re-fetched — it contains `1597`, `4 PFLOPS`,
   `2 PFLOPS`, `1 PFLOP`, `234 TFLOPS` and `96 GB`, and the string "sparsity" **does not
   appear anywhere on it**, so the ambiguity is real and unresolved at the source.
   METHODOLOGY §8 nevertheless pins **≈ 2,000 TFLOPS dense FP4 (4,000 sparse)** for
   planning; use that, and keep this question open for the measurement.
2. **RTX PRO 6000 INT8 TOPS and FP6 support** are not published.
3. **NVFP4 KV cache on sm120.** TRT-LLM's sm120 row lists FP8 KV only; vLLM's
   `CacheDType` is architecture-agnostic; a community report claims it works on RTX PRO
   6000. *Method: run `--kv-cache-dtype nvfp4` on an sm120 device and read the backend
   dispatch log.*
4. **DeepSeek-V4.1-Flash on RTX PRO 6000 under TensorRT-LLM.** TRT-LLM's sm120 row omits
   FP8 block-scaling, which the checkpoint's non-expert weights need. SGLang serves it
   there via `flashinfer_mxfp4`; TRT-LLM status unknown.
5. **TensorRT-LLM support for `nvidia/DeepSeek-V4.1-Flash-NVFP4`.** The model card lists
   only vLLM and SGLang as supported runtimes. No published TRT-LLM claim exists for this
   architecture as of 2026-09-19.
6. **V4.1-specific SGLang cookbook details.** The SGLang DeepSeek-V4.1 cookbook page
   exists in navigation but is JS-rendered and returned 404 to direct fetch. The Hopper
   W4A16/W4A8/FP8-MegaMoE guidance quoted in §3.3 and §9.1 comes from the **DeepSeek-V4**
   page; the quantization shape is the same family but the V4.1-specific flags and the
   existence of `sgl-project/DeepSeek-V4.1-Flash-FP8` are unconfirmed.
7. **Kimi-K3 A8W4 vs A4W4 default conflict.** vLLM's recipe steers to a4w4
   (`VLLM_ROCM_USE_AITER_MOE_SITUV2=1`, and "Do not set `AITER_SITUV2_A8W4=1`"); SGLang
   ships a8w4 and measures it faster (537.3 vs 530.8 tok/s). Measure on your traffic.
8. **Kimi-K3 on sm120.** Not a listed platform in any recipe; 8× RTX PRO 6000 = 768 GB
   cannot hold 1454 GiB regardless.
9. **MXFP4 via Marlin on A100 (SM80).** vLLM's hardware matrix says Marlin covers
   GPTQ/AWQ/FP8/FP4 on Ampere, with the footnote scoping only Turing out of MXFP4. Whether
   a 4-bit MoE checkpoint loads *and is usable* on A100 is untested here. The matrix
   itself has no Blackwell column and is visibly stale.
10. **`petit_nvfp4` on MI355X.** SGLang scopes it to MI250/MI300X/MI325X. Whether it is
    selectable, or ever preferable to `quark_mxfp4`, on CDNA4 is unknown.
11. **B300's 2× SFU attention uplift, measured.** NVIDIA's claim is architectural. No
    published end-to-end measurement isolates it for DeepSeek-V4.1-Flash's CSA2 indexer or
    Kimi-K3's KDA.
12. **B300 FP8 peak, conflicting sources — and the two bins must not be merged.**
    NVIDIA's GB300 NVL72 table gives 720 PFLOPS FP8/FP6 sparse for the rack → **5,000
    TFLOPS dense per GPU on the NVL72 bin**, unchanged from the GB200-bin B200; the
    HGX/DGX/`p6-b300` bin is **4,500**, unchanged from the HGX B200 (METHODOLOGY §8).
    Third-party reporting claims 7 PFLOPS for "B300" without naming a bin. This document
    uses NVIDIA's table and METHODOLOGY's pinned split. *Method: CUTLASS FP8 GEMM roofline
    on each bin.* (Re-fetched 2026-09-19: the GB300 page still reads "720 PFLOPS" FP8/FP6,
    "360 PFLOPS" FP16/BF16, "1440 | 1080" FP4, "24 POPS" INT8, "100 TFLOPS" FP64.)
13. **MI355X sparse MXFP4/MXFP6.** AMD's datasheet lists **N/A** in the W/SPARSITY column
    for MXFP8, MXFP6 and MXFP4; third-party pages quote 20.1 PFLOPS FP4 sparse. Assume no
    structured-sparsity path for 4/6-bit on CDNA4 until AMD documents one.
14. **Qwen3.8-27B first-party AWQ/GPTQ.** None found. Also no published accuracy table on
    `nvidia/Qwen3.8-27B-NVFP4` — benchmark names only, no scores.
15. **GGUF NVFP4 (type 40) and MXFP4 (type 39) kernel maturity.** Both are now defined in
    `GGMLQuantizationType`
    [src](https://raw.githubusercontent.com/ggml-org/llama.cpp/master/gguf-py/gguf/constants.py),
    but whether llama.cpp uses Blackwell FP4 tensor cores for them or dequantises is
    unverified. Same for vLLM's GGUF loader.
16. **W4A6 (MXFP4 weights × MXFP6 activations) on any repo model.** AMD's dense-model
    results are strong (§6.4); no MoE or repo-model numbers exist.
17. **Kimi-K3 exact non-expert byte split.** Computed 1347 GiB of MXFP4 experts against a
    measured 1453.7 GiB total leaves ~107 GiB unattributed across BF16 attention, shared
    experts, LatentMoE projections, embeddings, vision tower and norms. A full per-shard
    header sweep would close this; only one shard was read.
18. **`nvidia/Kimi-K3-NVFP4` upstream readiness.** Both engines still need out-of-tree
    code: vLLM a bundled `sitecustomize.py` pending PRs #50617 / #52406 / #52405, SGLang a
    preview image pending PR #35077. Re-check before committing a fleet.
19. **Checkpoint `kv_cache_scheme` overriding an omitted `--kv-cache-dtype`.** Sourced
    from a third-party issue tracker, not upstream docs. Verify on your build.
20. ~~**METHODOLOGY §1's FP8 scale overheads are both the 1-D reading.**~~ **CLOSED
    2026-09-19.** METHODOLOGY §1 was amended the same day and now carries the
    bytes-per-param table this document derives: FP8 128×128 FP32-scaled **1.0002
    (+0.02 %)**, FP8 32×32 UE8M0 **1.001 (+0.1 %)**, MXFP8 1.03125, **NVFP4 0.5625
    (+12.5 %)**, **MXFP4 0.53125 (+6.25 %)**, plus the mixed-precision rule that a
    checkpoint is summed per tensor group and reconciled to `index.json` `total_size`.
    Every NVFP4 size in this document uses 0.5625 B/param (§2.3, §9.2, §9.4). One
    residual disagreement remains and is stated in §2.3: METHODOLOGY's INT4 g128 row
    (≈ 0.53 B/param) vs this document's recomputed 0.5195.

---

## Sources

- https://developer.nvidia.com/blog/introducing-nvfp4-for-efficient-and-accurate-low-precision-inference/
- https://arxiv.org/abs/2509.25149 — *Pretraining Large Language Models with NVFP4* (NVIDIA)
- https://arxiv.org/pdf/2509.25149v1
- https://developer.nvidia.com/blog/inside-nvidia-blackwell-ultra-the-chip-powering-the-ai-factory-era/
- https://www.nvidia.com/en-us/data-center/a100/
- https://www.nvidia.com/en-us/data-center/h200/
- https://www.nvidia.com/en-us/data-center/gb200-nvl72/
- https://www.nvidia.com/en-us/data-center/gb300-nvl72/
- https://www.nvidia.com/en-us/data-center/dgx-b200/
- https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/
- https://www.tomshardware.com/pc-components/gpus/nvidia-announces-blackwell-ultra-b300-1-5x-faster-than-b200-with-288gb-hbm3e-and-15-pflops-dense-fp4
- https://www.amd.com/content/dam/amd/en/documents/instinct-tech-docs/product-briefs/amd-instinct-mi355x-gpu-brochure.pdf
- https://www.amd.com/en/products/accelerators/instinct/mi350.html
- https://rocm.blogs.amd.com/software-tools-optimization/nvfp4-to-mxfp4/README.html
- https://rocm.blogs.amd.com/artificial-intelligence/w4a6-quant-mm/README.html
- https://www.opencompute.org/documents/ocp-microscaling-formats-mx-v1-0-spec-final-pdf
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/inference/model.py
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/inference/kernel.py
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/model.safetensors.index.json
- https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4
- https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/hf_quant_config.json
- https://huggingface.co/nvidia/DeepSeek-V4-Flash-NVFP4
- https://huggingface.co/moonshotai/Kimi-K3
- https://huggingface.co/nvidia/Kimi-K3-NVFP4
- https://huggingface.co/Qwen/Qwen3.8-27B
- https://huggingface.co/Qwen/Qwen3.8-27B-FP8
- https://huggingface.co/nvidia/Qwen3.8-27B-NVFP4
- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash
- https://recipes.vllm.ai/moonshotai/Kimi-K3
- https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3
- https://docs.sglang.io/cookbook/autoregressive/DeepSeek/DeepSeek-V4
- https://docs.sglang.io/advanced_features/quantization.html
- https://docs.vllm.ai/en/latest/features/quantization/
- https://github.com/vllm-project/vllm/blob/main/docs/features/quantization/README.md
- https://github.com/vllm-project/vllm/blob/main/docs/features/quantization/modelopt.md
- https://github.com/vllm-project/vllm/blob/main/docs/features/quantization/quark.md
- https://github.com/vllm-project/vllm/blob/main/docs/features/quantization/quantized_kvcache.md
- https://docs.vllm.ai/en/latest/features/quantization/quantized_kvcache/
- https://github.com/vllm-project/vllm/blob/main/vllm/config/cache.py
- https://github.com/vllm-project/vllm/pull/53709
- https://vllm-project.github.io/2026/04/22/fp8-kvcache.html
- https://docs.vllm.ai/projects/llm-compressor/en/latest/guides/compression_schemes/
- https://docs.vllm.ai/projects/llm-compressor/en/latest/examples/quantization_w4a4_fp4/
- https://nvidia.github.io/TensorRT-LLM/features/quantization.html
- https://nvidia.github.io/Model-Optimizer/guides/_choosing_quant_methods.html
- https://nvidia.github.io/Model-Optimizer/announcements/local-hessian.html
- https://github.com/NVIDIA/Model-Optimizer
- https://flashinfer.ai/releases/
- https://github.com/flashinfer-ai/flashinfer/issues/2847
- https://developers.redhat.com/articles/2024/10/14/introducing-machete-mixed-input-gemm-kernel
- https://raw.githubusercontent.com/ggml-org/llama.cpp/master/gguf-py/gguf/constants.py
- https://inferencex.semianalysis.com/blog/b200-glm5-nvfp4-vs-h200-fp8-3-6x-perf-per-dollar
- https://inferencex.semianalysis.com/blog/inferencex-v2-nvidia-blackwell-vs-amd-vs-hopper
- https://github.com/SemiAnalysisAI/InferenceX
- https://github.com/waired-ai/waired-agent/issues/1441 (third-party issue tracker)

---

## Verification log (2026-09-19)

Adversarial re-check of this document. Every claim below was tested against a source
fetched independently of the document's own citation, or recomputed from scratch with
`python3` / the repo `config.json` files. **40 claims checked: 26 CONFIRMED, 8 CORRECTED,
6 UNVERIFIABLE** (a few land in two buckets — a confirmed number carrying an
unverifiable citation).

### CONFIRMED

1. **A100 80GB — BF16 312 dense, INT8 624 TOPS dense, 80 GB HBM2e, 2.039 TB/s SXM /
   1.935 TB/s PCIe.** Page reads "BFLOAT16 Tensor Core 312 TFLOPS | 624 TFLOPS*",
   "INT8 Tensor Core 624 TOPS | 1248 TOPS*", "* With sparsity"; no FP8 or FP4 row exists.
   — https://www.nvidia.com/en-us/data-center/a100/
2. **H100 SXM — BF16 989.5 dense, FP8 1979 dense, INT8 1979 dense, 80 GB, 3.35 TB/s.**
   Page: BF16/FP16 1,979, FP8 3,958, INT8 3,958 TOPS, all "With sparsity" → halve.
   — https://www.nvidia.com/en-us/data-center/h100/
3. **H200 SXM 989.5 / 1979 / 1979 dense, 141 GB, 4.8 TB/s; H200 NVL 835.5 / 1670.5 /
   1670.5 dense.** Page: SXM 1,979 / 3,958 / 3,958; NVL 1,671 / 3,341 / 3,341, sparse.
   — https://www.nvidia.com/en-us/data-center/h200/
4. **B200 HGX bin — FP4 9000 dense, FP8 4500 dense, 180 GB, 8 TB/s.** "FP4 Tensor Core:
   144 PFLOPS | 72 PFLOPS*" / "*Shown in sparse | dense"; "FP8 Tensor Core: 72 PFLOPS**"
   / "**Dense performance is ½ sparse spec shown"; "1,440 GB total, 64 TB/s" ÷ 8.
   — https://www.nvidia.com/en-us/data-center/dgx-b200/
5. **B200 GB200 bin — FP4 10000, FP8 5000, INT8 5000, BF16 2500 dense; 186 GB, 8 TB/s.**
   Superchip column (2 GPUs): NVFP4 "40 | 20 PFLOPS", FP8/FP6 20 PFLOPS sparse, INT8
   20 POPS sparse, FP16/BF16 10 PFLOPS sparse, "372 GB HBM3E | 16 TB/s".
   — https://www.nvidia.com/en-us/data-center/gb200-nvl72/
6. **B300/GB300 — FP4 15000, FP8 5000, BF16 2500 dense; 288 GB; 8 TB/s; FP64
   1.39 TFLOPS/GPU.** Rack table: FP4 "1440 | 1080 PFLOPS", FP8/FP6 720, FP16/BF16 360,
   FP64 100 TFLOPS, "20 TB | Up to 576 TB/s", footnotes "All Tensor Core specifications
   are with sparsity unless otherwise noted" / "Without sparsity"; ÷ 72.
   — https://www.nvidia.com/en-us/data-center/gb300-nvl72/
7. **RTX PRO 6000 Blackwell SE — FP4 4 PFLOPS, FP8 2 PFLOPS, FP16|BF16 1 PFLOP, 96 GB
   GDDR7, 1597 GB/s, up to 600 W, and no sparsity footnote anywhere on the page.**
   Confirms the document's caveat rather than resolving it. New datum: "TF32 Tensor Core
   234 TFLOPS" (§4.1 note).
   — https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/
8. **NVFP4 = E2M1 / block 16 / E4M3 scale / FP32 per-tensor; MXFP4 = E2M1 / block 32 /
   UE8M0; E2M1 encodes ±0, ±0.5, ±1, ±1.5, ±2, ±3, ±4, ±6.** Verified against the paper
   text (Table 1, Figure 1 caption, §2), not the blog.
   — https://arxiv.org/pdf/2509.25149v1
9. **Bits-per-parameter table, all rows except the 128×128 one.** Recomputed:
   NVFP4 4.500 (+12.5 %), MXFP4 4.250 (+6.25 %), MXFP6 6.250 (+4.17 %), MXFP8 8.250
   (+3.125 %), FP8 32×32 UE8M0 8.008 (+0.098 %), INT4 g128 4.156 (+3.91 %); NVFP4/MXFP4
   = 1.0588 → **+5.88 %**. Arithmetic only.
10. **DeepSeek-V4.1-Flash: 890 bytes/token KV, "FP4 main KV caching (E2M1 format, one
    E4M3 scale per 16 channels)", 40 layers, 1 shared + 384 routed experts, top-6,
    1M context.** All verbatim on the card. (Card also gives 552B backbone params, 8B
    active prefill / 16B decode — not currently stated in this document.)
    — https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash
11. **DeepSeek-V4.1-Flash checkpoint = 510,286,023,000 B = 475.24 GiB, 48 shards.**
    Read from the index file directly.
    — https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/model.safetensors.index.json
12. **The vLLM byte-accounting table (557.2B/259.5 GiB, 196.6B/183.1 GiB, 7.4B/6.9 GiB,
    2.0B/3.9 GiB, 23.6B/21.9 GiB) and the MXFP4/MXFP8/UE8M0 prose.** Recomputed: the
    component sum is 510.18 GB = 475.15 GiB, within 0.02 % of the index file. The 23.6B
    scale count only closes if the Engram tables carry **1-D block-32** scales
    (17.41B expert + 6.14B Engram + 0.007B attention = 23.56B) — a detail worth keeping.
    — https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash
13. **Routed-expert arithmetic: 3 × 2304 × 5120 = 35.389M/expert × 384 × 40 =
    543,581,798,400 params; 268.9 GiB at 4.25 bits; 1012.5 GiB at BF16.** Recomputed, and
    independently pinned by the NVFP4 card's "16,986,931,200 weight blocks" × 32.
14. **NVFP4 conversion tax: +15.82 GiB, 476 → 492 GiB.** Recomputed from
    (1/16 − 1/32) bytes/param × 543.58B.
15. **nvidia/DeepSeek-V4.1-Flash-NVFP4 — modelopt v0.47.0rc0, 48 shards, 476→492 GiB,
    16,986,931,200 blocks lossless, 18 of 46,080 fallback scales, the full six-benchmark
    accuracy table, "vLLM and SGLang", "NVIDIA Blackwell".** Every figure matches.
    46,080 = 384 × 40 × 3 ✓.
    — https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4
16. **Kimi-K3 — 2.8T params, 93 layers, 896 experts, 16/token, 2 shared, 1,048,576
    context, MXFP4 weights / MXFP8 activations via QAT.** Card also states **104B
    activated** and the layer split **69 KDA + 24 Gated MLA** (93 total), neither
    currently in §9.3.
    — https://huggingface.co/moonshotai/Kimi-K3
17. **Kimi-K3 expert arithmetic: 3 × 3072 × 3584 = 33.03M/expert × 896 × 92 = 2.7227T;
    1347.1 GiB at 4.25 bits; 5071.5 GiB at BF16.** Recomputed;
    92 MoE layers follows from `first_k_dense_replace: 1` in the repo config.
    **Superseded 2026-09-19:** this entry's "8 × B300 = 2304 GB" is wrong — 2,304 GB is
    8 × the GB300 NVL72 die figure. An 8-GPU HGX/DGX B300 or AWS `p6-b300` node is
    **2,144 GB (268 GB/GPU)**, per METHODOLOGY §8 and the corrected repo README. The
    arithmetic in the rest of the entry is unaffected.
18. **nvidia/Kimi-K3-NVFP4 — modelopt v0.45.0, released 2026-08-14, `input_scale=1.0`,
    KDA/MLA attention projections to 128×128 per-block FP8, 8× B300, the six-benchmark
    table, `flashinfer_trtllm` required, PRs #50617/#52406/#52405 and SGLang #35077.**
    All match.
    — https://huggingface.co/nvidia/Kimi-K3-NVFP4
19. **Qwen3.8-27B — 27B, 64 layers, hidden 5120, FFN 17408, 24 Q / 4 KV heads at
    head_dim 256, DeltaNet 48 V / 16 QK heads at 128, 16 × (3 linear → 1 full),
    262,144 native → 1M, MTP, BF16 with no `quantization_config`.** Card and the repo
    `config.json` agree on every field.
    — https://huggingface.co/Qwen/Qwen3.8-27B
20. **Qwen3.8-27B weight footprints 50.3 / 25.2 / 14.1 / 13.4 / 13.1 GiB.** Recomputed
    (BF16 50.29, FP8 b128 25.19, NVFP4 14.14, MXFP4 13.36, INT4 g128 13.06).
21. **vLLM PR #53709 — every number.** Prefill 7020→10752 (1.53×), 8129→11882 (1.46×),
    8396→11623 (1.38×); decode 1642→1856 (1.13×), 1637→1841 (1.12×); H20, TP=4;
    "FP8 experts use 2x the weight memory vs MXFP4"; KV 83.9 → 53.7 GiB, 5.70M → 3.65M
    tokens; "On SM90 only Marlin W4A16 passes, so Hopper has no FP8-tensor-core MoE path
    for these checkpoints today."
    — https://github.com/vllm-project/vllm/pull/53709
22. **AMD W4A6 blog — every number in §6.4.** `v_mfma_scale_f32_16x16x128_f8f6f4`
    description; "exceeds FP8 hipBLASLt on all four tested shapes … within 3.6–15.7 % of
    MXFP4"; Llama-3.1-8B 85.0k vs 83.3k tok/s; GSM8K 62.55 / 76.42 / 80.44;
    Qwen3.6-27B AIME W4A6 85.8 vs FP8 86.7. (Source adds the MXFP4 baseline of **80.0**,
    which §6.4 omits — the W4A6 gain over MXFP4 is +5.8 points, not the +13.9 the GSM8K
    row might suggest by analogy.)
    — https://rocm.blogs.amd.com/artificial-intelligence/w4a6-quant-mm/README.html
23. **ROCm NVFP4→MXFP4 blog — block-size/scale-encoding portability statement, the
    requantization mechanism, 10–55 s load cost, and all three throughput pairs**
    (MiniMax-M2.7 3516.0→3491.2, Qwen3.5-397B-A17B 2963.2→2974.4,
    DeepSeek-R1 2799.0→2773.2).
    — https://rocm.blogs.amd.com/software-tools-optimization/nvfp4-to-mxfp4/README.html
24. **vLLM `CacheDType` literal.** Every value listed in §8.1 is present, plus the
    ROCm/CUDA portability comment verbatim.
    — https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/cache.py
25. **TRT-LLM: sm120 lacks FP8 block-scaling; sm100/103 is the only row with NVFP4 KV
    cache; the MXFP8-recipe sentence.** All three verbatim.
    — https://nvidia.github.io/TensorRT-LLM/features/quantization.html
26. **SemiAnalysis GLM-5 table — GLM-5 744B/~40B active, SGLang v0.5.12, ISL 8192 /
    OSL 1024, H200 $1.41/GPU/hr, B200 $1.95/GPU/hr, and the quoted concurrency rows
    (H200 4 → 347.9/84.49/$1.13 and 32 → 851.9/24.90/$0.46; B200 4 → 1038.7/121.22/$0.52
    and 32 → 3037.3/43.99/$0.18).**
    — https://inferencex.semianalysis.com/blog/b200-glm5-nvfp4-vs-h200-fp8-3-6x-perf-per-dollar

### CORRECTED

27. **§2.3 FP8 128×128 block, FP32 scale: 8.016 bits / 1.0020 B / +0.2 % →
    8.002 bits / 1.0002 B / +0.02 %.** One FP32 scale per 16,384 elements is
    32/16384 = 0.00195 bits/param. The old figure over-stated the overhead 8×.
    Source: arithmetic (`python3`); format definition per DeepSeek-V3 / OCP.
28. **§3.1 "12B hybrid Mamba-Transformer" → "8B … trained on 1 trillion tokens".**
    The 2.5 %-vs-1.5 % / 36 %-more-tokens ablation is §5 of the paper and uses an 8B
    model; the 12B/10T run is the separate main NVFP4-vs-FP8 result quoted in §7.3.
    Verbatim: "We consider an 8-billion parameter (8B) model based on the hybrid
    Mamba-Transformer architecture. The model is trained on 1 trillion tokens with the
    same dataset as used for the 12B model."
    — https://arxiv.org/pdf/2509.25149v1
29. **§6.1 decomposition: "precision step supplies the rest to a 2.98× combined factor …
    2.4× of the 3.65× is the format" → 1.22× × 2.98× = 3.64 ≈ 3.65; the format is the
    2.98×, the silicon is the 1.22×.** The source states it as a product. This inverts
    the paragraph's conclusion in degree (the format contributes far more than the
    document claimed), not in direction.
    — https://inferencex.semianalysis.com/blog/b200-glm5-nvfp4-vs-h200-fp8-3-6x-perf-per-dollar
30. ~~**§3.2 and §7.2 GSM8K recovery "98.7–100.7 %" → "99.1–100.7 %".**~~
    **REVERTED 2026-09-19 — this correction was itself wrong.** Appendix A of the blog
    was re-fetched and transcribed row by row: it lists **five** models, not three —
    MiniMax-M2.7 0.918→0.924 (100.7 %), Qwen3.5-397B-A17B 0.954→0.945 (99.1 %),
    **Qwen3.5-397B-A17B-V2 0.954→0.941 (98.7 %)**, **Kimi-K2.6 0.939→0.930 (99.0 %)**,
    DeepSeek-R1 0.958→0.950 (99.2 %). The original **98.7–100.7 %** range was correct and
    is restored in §3.2 and §7.2. (The blog's prose says "recovers over 99 %", which its
    own 98.7 % row contradicts — the table is the citable artifact.)
    — https://rocm.blogs.amd.com/software-tools-optimization/nvfp4-to-mxfp4/README.html
31. **§5.1 citation for "less than SM 100 … only weight-only quantization": moved from
    the compression-schemes guide to the W4A4-FP4 example page**, where the sentence
    actually appears ("if running inference on a machine that is < SM100, vLLM will not
    run activation quantization, only weight-only quantization").
    — https://docs.vllm.ai/projects/llm-compressor/en/latest/examples/quantization_w4a4_fp4/
32. **§5.1 TRT-LLM matrix: "Ada, Ampere" split into two rows; Ampere is W4A16-only with
    no FP8 per-tensor GEMM.** The matrix lists Ampere as "FP8 KV Cache, W4A16 AWQ,
    W4A16 GPTQ" — no W4A8, no FP8 GEMM. Consequential for every A100 plan here.
    — https://nvidia.github.io/TensorRT-LLM/features/quantization.html
33. **§4.1 B300/GB300 INT8 `166.7 ⚠️` → `166.7` (marker removed).** Derivable from the
    rack table: INT8 24 POPS sparse ÷ 2 ÷ 72 = 166.7 TOPS. Worth stating explicitly that
    this is 1/30th of the FP8 rate.
    — https://www.nvidia.com/en-us/data-center/gb300-nvl72/
34. **§9.2 "× (1/16 − 1/32) bits of extra scale" → bytes.** The arithmetic was right
    (0.03125 B/param → +15.82 GiB); only the unit word was wrong.

### UNVERIFIABLE (marker added)

35. **All MI355X peak-throughput figures (BF16 2516.6, FP8 5033.2, MXFP6/MXFP4 10066.3,
    INT8 5033.2 dense) and the "AMD lists no sparse MXFP4" claim.** The brochure PDF,
    `amd.com/.../instinct/mi350.html`, `.../mi355x.html` and AMD's MI355X press release
    **all timed out** on 2026-09-19. Only 256 CUs (32/XCD) and 288 GiB were re-confirmed,
    from AMD's ROCm hardware-specs table — which publishes neither bandwidth nor FLOPS.
    Marker added in §4.1.
    — https://rocm.docs.amd.com/en/latest/reference/gpu-arch-specs.html
36. **"No pre-Blackwell NVIDIA GPU (H100, H200, A100) has FP4 tensor cores" as a
    *quotation*.** Not present on the cited llm-compressor page (checked both the
    rendered doc and `docs/guides/compression_schemes.md` in the repo). The claim itself
    is true and is now sourced to TRT-LLM's matrix instead; the quote is flagged.
37. **llm-compressor "Min hardware" values Ada+ / Hopper+ / Turing+ for
    W8A8-FP8 / FP8_BLOCK / INT8.** Neither the rendered page nor the repo markdown has a
    hardware column for those rows. The SM100+ scoping of NVFP4/MXFP4/MXFP8 and the
    W4A16 "any GPU" row *are* confirmed. Markers added.
38. **"FP8 rowwise" in TRT-LLM's sm100/103 row.** The re-read places it only in the
    Hopper row. Flagged rather than deleted pending a third read.
39. ~~**"±0.05 accuracy points" for GPQA-Diamond-CoT / AIME25 under MI355X online
    requantization.**~~ **RESOLVED 2026-09-19 — the figure is on the page and the ⚠️ was a
    false negative.** It is in the "Advanced Reasoning Benchmarks" section under Figure 3,
    verbatim: "Online NVFP4 to MXFP4 requant and NVFP4 emulation agree within seed noise
    across all task questions, with final averaged accuracy score **within ±0.05** of each
    other (illustrated in Figure 4)." Marker removed from §7.2 and the quote tightened to
    the source's wording.
    — https://rocm.blogs.amd.com/software-tools-optimization/nvfp4-to-mxfp4/README.html
40. **H100 PCIe row: memory type and bandwidth.** NVIDIA's current H100 page publishes
    only SXM and NVL; the PCIe variant (80 GB, 2.0 TB/s) is no longer listed there, and
    the H100 datasheet PDF redirects to the site root. The PCIe part is widely documented
    as **HBM2e**, not HBM3 as this table read — changed and marked ⚠️ pending a
    reachable NVIDIA datasheet.

### Not a finding, but flagged for the orchestrator

- **The paper's Table 1 has a "Speedup vs. BF16" column this document dropped**
  (MXFP8 2× / MXFP6 2× on both parts; MXFP4 and NVFP4 4× on GB200, **6× on GB300**).
  Restored in §2.2. It is NVIDIA's own confirmation of §4.1's dense ratios and of §4.3's
  "B300 is 1.5× only when FP4-compute-bound".
- **METHODOLOGY §1 is wrong twice on FP8 scale overhead**, not once — see Open
  question 20 as amended.

---

## Sweep log (2026-09-19)

Systemic correction sweep against the amended [`METHODOLOGY.md`](../METHODOLOGY.md)
(§1 bytes-per-param table + mixed-precision rule, §2 state-slot multiplier S, §6 standard
scenarios, §8 pinned GPU / model / price inputs). Format: **section · old → new · reason ·
source**. Items in the systemic checklist that this document did not trigger — decode
KV-read scaling, `infeasible (KV)` rows, engine release versions/dates, the DSpark
acceptance length, the TokenSpeed V4.1 recipe, the B200-vs-H200 DeepSeek-R1 FP8 ratio,
`not retrievable` price placeholders — were checked by grep and are absent; nothing was
changed for them.

| # | Section | Old → New | Reason | Source |
|---|---|---|---|---|
| 1 | §2.3 note | "METHODOLOGY §1 states FP8 32×32 as ~+6 %" → METHODOLOGY §1 amended, carries 1.0002 / 1.001 / 0.5625 / 0.53125; no disagreement remains | METHODOLOGY §1 was amended the same day; the note described a superseded upstream state | [METHODOLOGY §1](../METHODOLOGY.md) |
| 2 | §2.3 note | *(added)* residual INT4 g128 disagreement: METHODOLOGY ≈ 0.53 B/param vs recomputed 0.5195 | METHODOLOGY §8's "state the disagreement and cite both" rule | arithmetic (`python3`), METHODOLOGY §1 |
| 3 | §3.2 | GSM8K recovery "99.1–100.7 %, three models" → **"98.7–100.7 %, five models"** with all five rows transcribed | Citation re-fetch: Appendix A lists MiniMax-M2.7, Qwen3.5-397B-A17B, **Qwen3.5-397B-A17B-V2 (98.7 %)**, **Kimi-K2.6 (99.0 %)**, DeepSeek-R1. The earlier fact-check's correction was itself wrong | [ROCm NVFP4→MXFP4 blog](https://rocm.blogs.amd.com/software-tools-optimization/nvfp4-to-mxfp4/README.html), re-fetched HTTP 200 |
| 4 | §7.2 | same recovery range; **⚠️ TO BE VERIFIED removed** from the "±0.05 accuracy points" claim, replaced with the verbatim sentence | Citation re-fetch: the figure is in "Advanced Reasoning Benchmarks" under Figure 3; the earlier ⚠️ was a false negative | same blog |
| 5 | §4.3 bullet | "Kimi-K3 (1.45 TiB)" → **1,560.9 GB = 1,453.7 GiB = 1.42 TiB**; "the 288 GB" → **268 GB/GPU (2,144 GB/node) HGX B300 vs 288 GB (≈279 usable) GB300 NVL72** | Units in bytes reported as GiB; capacities as deployed, bins not merged | METHODOLOGY §8 |
| 6 | §4.3 prose | "plus 55 % more HBM" → **+48.9 % (180→268 GB HGX bin) / +54.8 % (186→288 GB NVL72 bin)** | Same; the single figure silently used the NVL72 bin for both | METHODOLOGY §8, §4.3 table |
| 7 | §6.1 | *(added)* the $1.41 / $1.95 GPU-hr rates labelled as **the source's own assumptions**, with the sourced OCI on-demand rows (H200 $10.00, B200 $14.00) and neocloud comparison | Prices may only come from `cloud-pricing.md`; the blog's assumed rates were readable as repo planning prices | [cloud-pricing.md](cloud-pricing.md) (`BM.GPU.H200.8`, `BM.GPU.B200.8`, Hyperstack HGX rows) |
| 8 | §6.5 | MI355X dense MXFP4 peak "10.07 PFLOPS" → **10,100 TFLOPS (10.1 PFLOPS)**; added that it is below B300's 13,500 and GB300's 15,000 | Dense peaks pinned; the comparison previously stopped at the GB200-bin B200 | METHODOLOGY §8 |
| 9 | §9.1 | 890 B/token stated flatly → **"the FP4-KV figure, presuming the Blackwell FP4-KV kernel (720 main + 170 indexer)"**, with a pointer to the model's own KV-dtype table | 890 is not a dtype-independent constant; §5 of the checklist | METHODOLOGY §8; [DeepSeek-V4.1-Flash card](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) re-fetched (both phrases verbatim) |
| 10 | §9.2 | card's "492 GiB" → reconciled to the pinned **527.27 GB = 491.06 GiB**; Open question 20 noted closed | Reconcile to METHODOLOGY §8 pinned checkpoint totals; NVFP4 = 0.5625 B/param | METHODOLOGY §8; arithmetic (`python3`) |
| 11 | §9.2 | "Should you use it" → added **accuracy-neutral, and NVIDIA publishes no throughput/latency comparison vs the MXFP4 base**; +16 GiB restated as 527.27 vs 510.29 GB | Checklist item 14 — no speedup may be implied for the NVFP4 build | [NVFP4 card](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4) (accuracy table only) |
| 12 | §9.3 | "fits one 8×B300 node (**2304 GB** HBM)" → **2,144 GB (268 GB/GPU on HGX/DGX B300 and AWS `p6-b300`)**, with the misquote flagged | 2,304 GB is 8 × the GB300 NVL72 die figure; the repo README was already corrected | METHODOLOGY §8; repo `README.md` L52–55 |
| 13 | §9.3 | expert bytes restated as **1,446.2 GB = 1,346.6 GiB**; total as **1,560.9 GB = 1,453.7 GiB** | Bytes → GiB convention; reconcile to pinned checkpoint total | METHODOLOGY §1, §8; arithmetic (`python3`) |
| 14 | §9.3 | *(added)* the KDA state-slot multiplier: **428.6 MiB × S = 5 → 2.25 GB per request**, ≈227 GB of state at the published 101-request cap | METHODOLOGY §2: per-sequence recurrent state is multiplied by the engine's slot count | METHODOLOGY §2, §8; SGLang Kimi-K3 cookbook |
| 15 | §9.4 | weight-footprint table **50.3 / 25.2 / 14.1 / 13.4 / 13.1 GiB (flat rate on "27B")** → pinned mixed-precision totals **55.56 / 30.87 / 21.92 / 19.45 GB = 51.74 / 28.75 / 20.42 / 18.11 GiB**, MXFP4 kept as `est.` | Flat bytes/param applied to a mixed-precision checkpoint, and the wrong param count (27 B vs 27.78 B) | METHODOLOGY §1 mixed-precision rule, §8 pinned totals; arithmetic (`python3`) |
| 16 | §9.4 | "Fits 1×" column removed, replaced with a pointer to `research/models/qwen3827b/<gpu>.md` | Scope: no per-(model, GPU) fit tables in cross-cutting docs | checklist item 15 |
| 17 | §9.5 | checkpoint sizes in GiB only → **GB and GiB**, all four reconciled to pinned totals; **Marlin-2B column added** (5.444 GB / 5.070 GiB, BF16, 618 tensors) | Units convention; the repo has five checkpoints and the table covered four | METHODOLOGY §8; [models/marlin2b/architecture.md](../models/marlin2b/architecture.md) §1.2 |
| 18 | §9.5 | KV row: added FP4-KV qualifier, MLA layer count (24 of 93), and **× S = 5** for KDA | METHODOLOGY §2 and §8 | METHODOLOGY §2, §8 |
| 19 | **§9.6** | *(section did not exist)* → **checkpoint × GPU format-support matrix added**: 8 checkpoint rows × 8 GPUs, cells native / requant / dequant / ❌, 15 footnotes | The Scope note has referenced §9.6 since the first draft; the pair docs will cite it. Format-support only — fit/throughput/cost explicitly deferred to `research/models/<exp>/<gpu>.md` | §3.3, §4.2, §5, §5.1, §9.1–§9.4 of this document; METHODOLOGY §8 |
| 20 | §9.6 note (e) | *(new)* disagreement recorded: METHODOLOGY §8 pins sm120 NVFP4 MoE grouped-GEMM as broken → Marlin W4A16, while §5 cites FlashInfer PR #2898; planning value = assume dequant | METHODOLOGY §8's "state the disagreement and cite both" rule | METHODOLOGY §8; [FlashInfer #2847](https://github.com/flashinfer-ai/flashinfer/issues/2847) |
| 21 | Open Q1 | *(status added)* page re-fetched: contains `1597`, `4 PFLOPS`, `2 PFLOPS`, `234 TFLOPS`, `96 GB`, and **no occurrence of "sparsity"**; METHODOLOGY §8 pins ≈ 2,000 dense for planning | Citation integrity + pinned dense FLOPS. 1,597 GB/s is the Server Edition (1,792 is Workstation) — already correct in §4.1, re-confirmed | [RTX PRO 6000 Server Edition page](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/), re-fetched HTTP 200 |
| 22 | Open Q12 | "GB300 table implies 5 PFLOPS dense FP8 per GPU" → **bins separated: NVL72 5,000, HGX/DGX/`p6-b300` 4,500**; re-fetched rack figures listed | Never merge the two Ultra bins | [GB300 NVL72 page](https://www.nvidia.com/en-us/data-center/gb300-nvl72/) re-fetched: "1440 \| 1080", "720 PFLOPS", "360 PFLOPS", "24 POPS", "100 TFLOPS"; METHODOLOGY §8 |
| 23 | Open Q20 | open → **CLOSED**, with the settled NVFP4 0.5625 / MXFP4 0.53125 / FP8 tile figures and the one residual INT4 disagreement | METHODOLOGY §1 amended upstream 2026-09-19 | [METHODOLOGY §1](../METHODOLOGY.md) |
| 24 | Verification log #17 | *(superseded note appended)* "8 × B300 = 2304 GB" → 2,144 GB | Same bin error as #12; the rest of the entry's arithmetic stands | METHODOLOGY §8 |
| 25 | Verification log #30 | *(marked REVERTED)* the 99.1–100.7 % correction is withdrawn; 98.7–100.7 % restored with all five rows | The earlier correction missed two table rows | ROCm blog, re-fetched |
| 26 | Verification log #39 | *(marked RESOLVED)* the ±0.05 ⚠️ is withdrawn; the sentence is on the page | False negative on the earlier re-read | ROCm blog, re-fetched |

### Citation integrity spot-check (checklist item 13)

Five most load-bearing citations re-fetched by `curl` on 2026-09-19, all **HTTP 200**, and
the specific claim string searched for in the fetched body:

| # | Citation | Claim tested | Result |
|---|---|---|---|
| 1 | [arxiv.org/pdf/2509.25149v1](https://arxiv.org/pdf/2509.25149v1) | Table 1 "Speedup vs. BF16" column (MXFP8 2×/2×, MXFP6 2×/2×, MXFP4 4×/6×, NVFP4 4×/6×); E2M1 value set; "2.5 % vs 1.5 %" and "36 % more tokens" on an **8B** model | ✅ all present verbatim — §2.2 and §3.1 stand, including the earlier fact-check's 12B→8B correction |
| 2 | [RTX PRO 6000 Blackwell Server Edition](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/) | `1597`, `4 PFLOPS`, `2 PFLOPS`, `1 PFLOP`, `234 TFLOPS`, `96 GB`; presence of a sparsity footnote | ✅ figures present; **"sparsity" absent from the page** → the dense/sparse ambiguity is genuine (Open Q1). Confirms 1,597 GB/s is the Server Edition |
| 3 | [GB300 NVL72](https://www.nvidia.com/en-us/data-center/gb300-nvl72/) | FP4 "1440 \| 1080", FP8/FP6 720, FP16/BF16 360, INT8 24 POPS, FP64 100 TFLOPS, "Without sparsity" | ✅ all present → dense per-GPU 15,000 / 5,000 / 2,500 / 166.7 confirmed for the **NVL72 bin only** |
| 4 | [DeepSeek-V4.1-Flash card](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) | "890 bytes per token", "FP4 main KV caching", "E4M3 scale per 16 channels" | ✅ all three verbatim. Note the card itself never says "MXFP4" — that identification rests on `model.py`, the safetensors headers and the vLLM recipe (§9.1a–c), which is how §9.1 already sources it |
| 5 | [ROCm NVFP4→MXFP4 blog](https://rocm.blogs.amd.com/software-tools-optimization/nvfp4-to-mxfp4/README.html) | Appendix A recovery table; "±0.05"; "can't be fed directly"; 10–55 s load cost; the three throughput pairs | ⚠️→✅ **two earlier findings overturned** (sweep rows 3, 4, 25, 26): the table has five rows not three, and the ±0.05 sentence exists. Portability quote, load cost and throughput pairs all confirmed |

No citation in this document was found to point at a page lacking its claim. The
RTX PRO 6000 bandwidth figure here was already sourced to the **product page**, not the
NVIDIA model card — the miscitation the sweep brief flagged in a sibling doc is not
present here.
