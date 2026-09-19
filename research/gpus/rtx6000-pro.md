# NVIDIA RTX PRO 6000 Blackwell — inference reference

**Research date: 2026-09-19.** Follows `research/METHODOLOGY.md` (formulas §1–§6, legend, dense-vs-sparse rule).

Primary target of this document: **RTX PRO 6000 Blackwell Server Edition** (96 GB GDDR7, PCIe Gen5, **no NVLink**).
Variants covered: Workstation Edition (600 W) and Max-Q Workstation Edition (300 W).

> **The single most important fact in this document.** This GPU is `sm_120`
> (compute capability 12.0), the *consumer/prosumer* Blackwell die (GB202). It is
> **not** `sm_100` (B200/GB200) or `sm_103` (B300/GB300). `sm_120` has **no
> `tcgen05` MMA and no TMEM**. Every kernel written against `tcgen05` —
> FlashAttention-3, FlashAttention-4, CUTLASS FMHA SM100, the whole trtllm-gen
> cubin family — **cannot run here at all**, not "runs slower". What it *does*
> have is `mma.sync.aligned.…block_scale`, i.e. real FP4/FP6/FP8 block-scaled
> tensor cores driven by Ampere-style warp MMA. The gap between "the silicon can
> do NVFP4" and "the serving stack can do NVFP4 correctly and fast" is the
> dominant planning risk on this card as of 2026-09-19.

---

## 1. Identity and variants

| | **Server Edition** | **Workstation Edition** | **Max-Q Workstation Edition** |
|---|---|---|---|
| Marketing name | RTX PRO 6000 Blackwell Server Edition | RTX PRO 6000 Blackwell | RTX PRO 6000 Blackwell Max-Q |
| GPU die | GB202 [src](https://www.nvidia.com/content/dam/en-zz/Solutions/design-visualization/quadro-product-literature/NVIDIA-RTX-Blackwell-PRO-GPU-Architecture-v1.0.pdf) | GB202 | GB202 |
| Architecture | NVIDIA Blackwell (RTX / GeForce-class Blackwell) | same | same |
| Compute capability | **sm_120 / cc 12.0** [src](https://github.com/flashinfer-ai/flashinfer/issues/2577) | **sm_120 / cc 12.0** [src](https://blog.us.fixstars.com/what-kind-of-gpu-is-the-nvidia-rtx-pro-6000-blackwell-max-q/) | **sm_120 / cc 12.0** [src](https://blog.us.fixstars.com/what-kind-of-gpu-is-the-nvidia-rtx-pro-6000-blackwell-max-q/) |
| SMs | 188 | 188 | 188 |
| CUDA cores | 24,064 [src](https://lenovopress.lenovo.com/lp2263.pdf) | 24,064 | 24,064 |
| Tensor cores | 752 (5th gen) [src](https://lenovopress.lenovo.com/lp2263.pdf) | 752 (5th gen) | 752 (5th gen) |
| RT cores | 188 (4th gen) | 188 (4th gen) | 188 (4th gen) |
| Boost clock | ~2,494 MHz **est.** (derived: FP32 120 TFLOPS ÷ 24,064 ÷ 2) | 2,617 MHz [src](https://www.nvidia.com/content/dam/en-zz/Solutions/design-visualization/quadro-product-literature/NVIDIA-RTX-Blackwell-PRO-GPU-Architecture-v1.0.pdf) | 2,280 MHz [src](https://www.nvidia.com/content/dam/en-zz/Solutions/design-visualization/quadro-product-literature/NVIDIA-RTX-Blackwell-PRO-GPU-Architecture-v1.0.pdf) |
| Memory | 96 GB GDDR7 ECC | 96 GB GDDR7 ECC | 96 GB GDDR7 ECC |
| Memory bandwidth | **1,597 GB/s** — re-verified verbatim 2026-09-19 on NVIDIA's own Server Edition page ("Memory Bandwidth: 1597 GB/s") [src](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/) and on Lenovo's HTML product guide ("96 GB of GDDR7 memory … 1597 GB/s of memory bandwidth") [src](https://lenovopress.lenovo.com/lp2263-thinksystem-nvidia-rtx-pro-6000-blackwell-server-edition-pcie-gen5-gpu). (The `lp2263.pdf` cited elsewhere in this doc carries the same figure but is not machine-extractable; quote the HTML guide.) | **1,792 GB/s** [src](https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000/) | **1,792 GB/s** [src](https://www.nvidia.com/content/dam/en-zz/Solutions/design-visualization/quadro-product-literature/NVIDIA-RTX-Blackwell-PRO-GPU-Architecture-v1.0.pdf) |
| TDP | 600 W; NVIDIA prints "Up to 600W (configurable)" and Lenovo "600 W (can also be power capped to 450 W to support increased density)" [src](https://lenovopress.lenovo.com/lp2263.pdf) [src](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/). The often-quoted **400 W** floor is in neither source — ⚠️ TO BE VERIFIED | 600 W [src](https://www.nvidia.com/content/dam/en-zz/Solutions/design-visualization/quadro-product-literature/NVIDIA-RTX-Blackwell-PRO-GPU-Architecture-v1.0.pdf) | 300 W [src](https://www.nvidia.com/content/dam/en-zz/Solutions/design-visualization/quadro-product-literature/NVIDIA-RTX-Blackwell-PRO-GPU-Architecture-v1.0.pdf) |
| Cooling / form factor | **Passive**, 4.4″H × 10.5″L, dual-slot FHFL (air); single-slot FHXL liquid variant [src](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition.md) | Active double-flow-through, 5.4″H × 12.0″L dual-slot | Active, 4.4″H × 10.5″L dual-slot |
| Host interface | PCIe 5.0 ×16 | PCIe 5.0 ×16 | PCIe 5.0 ×16 |
| **NVLink** | **No** [src](https://lenovopress.lenovo.com/lp2263.pdf) | No | No |
| MIG | Up to 4 instances @ 24 GB ("Universal MIG", graphics + AI) [src](https://lenovopress.lenovo.com/lp2263.pdf) | **Supported** — 1× 96 GB / 2× 48 GB / 4× 24 GB (whitepaper Table 3, "RTX PRO 6000 Blackwell Workstation Edition & Max-Q Edition") [src](https://www.nvidia.com/content/dam/en-zz/Solutions/design-visualization/quadro-product-literature/NVIDIA-RTX-Blackwell-PRO-GPU-Architecture-v1.0.pdf) | **Supported** — same table, same 1/2/4 configurations [src](https://www.nvidia.com/content/dam/en-zz/Solutions/design-visualization/quadro-product-literature/NVIDIA-RTX-Blackwell-PRO-GPU-Architecture-v1.0.pdf) |
| vGPU | vPC / vApps / RTX vWS [src](https://lenovopress.lenovo.com/lp2263.pdf) | — | — |
| Display | 4× DisplayPort 2.1b, **disabled by default** on Server Edition [src](https://lenovopress.lenovo.com/lp2263.pdf) | 4× DP 2.1 | 4× DP 2.1 |
| Video engines | 4× NVENC (9th gen), 4× NVDEC (6th gen), 4× JPEG; 4:2:2 H.264/HEVC | same | same |
| NVIDIA part no. | 900-2G153-2700-030 (Lenovo 4X67B09287) [src](https://lenovopress.lenovo.com/lp2263.pdf) | — | — |

**Die-level (all three share it)** [src](https://www.nvidia.com/content/dam/en-zz/Solutions/design-visualization/quadro-product-literature/NVIDIA-RTX-Blackwell-PRO-GPU-Architecture-v1.0.pdf):
750 mm², 92.2 B transistors, TSMC 4N NVIDIA custom, 12 GPCs / 94 TPCs, 128 CUDA cores + 4 tensor cores per SM,
**L2 = 128 MB**, register file 48,128 KB, L1/shared 128 KB per SM, 192 ROPs, PCIe Gen 5.

**Release dates.** Announced at GTC on **2025-03-18**; Server Edition availability from **May 2025** via cloud
providers and OEMs (Cisco, Dell, HPE, Lenovo, Supermicro) [src](https://blogs.nvidia.com/blog/rtx-pro-6000-blackwell-server-edition).

**Export control.** Lenovo marks the part "Controlled — not offered in certain markets, as determined by the US
Government" [src](https://lenovopress.lenovo.com/lp2263.pdf). Plan procurement accordingly.

### Driver / CUDA minimums

| Item | Requirement | Source |
|---|---|---|
| CUDA toolkit (minimum for Blackwell) | **12.8** | vLLM GPU install docs state "NVIDIA Blackwell GPUs (B200, GB200) require a minimum of CUDA 12.8" [src](https://docs.vllm.ai/en/latest/getting_started/installation/gpu.html) |
| CUDA toolkit (practical for sm_120 kernels) | **13.0–13.2+** | FlashInfer/FA CuTeDSL SM12x work states "it is needed cutlass v4.4 and cuda 13.1 minimum … we will jump in pytorch directly to CUDA 13.2, so sm12x will be full compatible soon" [src](https://github.com/Dao-AILab/flash-attention/issues/1987) |
| Drivers observed in the wild | 580.126.09 (Linux), 582.16 / 596.72 (Windows+WSL2) | [src](https://github.com/flashinfer-ai/flashinfer/issues/2577) [src](https://github.com/NVIDIA/cutlass/issues/3096) [src](https://github.com/sgl-project/sglang/issues/39302) |
| PyTorch | 2.9–2.13 with `cu128` / `cu130` builds; note PyTorch builds before ~2.10 cap at cc 12.0 and warn on GB10's cc 12.1 | [src](https://github.com/flashinfer-ai/flashinfer/issues/2577) |
| glibc ≥ 2.41 gotcha | CUDA toolkits **< 13.2** fail to compile JIT kernels (C23 `rsqrt` `noexcept` mismatch); needs a 13.2+ toolkit or the pip CUDA wheels | [src](https://github.com/sgl-project/sglang/issues/39807) |

**On `sm_120` vs `sm_120a` vs `sm_120f`.** `a` is an *architecture-specific* nvcc target that unlocks
arch-only PTX (including the block-scaled MMA); `-arch=sm_90a` generates `sm_90a,compute_90,compute_90a`
[src](https://docs.nvidia.com/cuda/cuda-compiler-driver-nvcc/index.html). FlashInfer gates on
`is_sm120a_supported()` / `is_sm121a_supported()` for cc 12.0 / 12.1 devices [src](https://github.com/flashinfer-ai/flashinfer/pull/2559).
A `compute_120f` "family" target is reported to work under CUDA 13.0 and to be what finally produced correct
NVFP4 MoE output on this card [src](https://github.com/NVIDIA/cutlass/issues/3096); the nvcc guide fetched
here does not document the `f` suffix — **⚠️ TO BE VERIFIED** against a CUDA 13.x release note.
Field reports of "`compute_120a` segfaults on RTX PRO 6000" [src](https://github.com/NVIDIA/cutlass/issues/3096)
are empirically real but the stated cause ("the GPU reports 12.0, not 12.0a") conflates a compiler target with a
device attribute — treat the *symptom* as sourced, the *explanation* as unverified.

### Device limits that bite kernels

| Attribute | Value | Why it matters |
|---|---|---|
| `cudaDevAttrMaxSharedMemoryPerBlockOptin` | **101,376 B (99 KB)** — verified empirically; requests of 128/160/198/202 KB return `invalid argument` | Hopper and B200 expose ~228 KB. Every attention kernel tuned against a 228 KB budget fails to launch here. [src](https://github.com/sgl-project/sglang/issues/39302) |
| `sharedMemPerMultiprocessor` | 102,400 B | same |
| L2 | 128 MB | unusually large; helps small-batch decode locality |

---

## 2. Memory and bandwidth

| | Server Edition | Workstation / Max-Q |
|---|---|---|
| Capacity | 96 GB GDDR7 with ECC | 96 GB GDDR7 with ECC |
| Interface | 512-bit | 512-bit |
| Pin data rate | ~25 Gbps (implied: 1597 ÷ 64 B) `est.` | 28 Gbps [src](https://www.nvidia.com/content/dam/en-zz/Solutions/design-visualization/quadro-product-literature/NVIDIA-RTX-Blackwell-PRO-GPU-Architecture-v1.0.pdf) |
| Bandwidth | **1,597 GB/s** | **1,792 GB/s** |
| Capacity mechanism | 96 GB on a 512-bit bus requires clamshell GDDR7 placement; reported as "96 GB of GDDR7 in a clamshell design" [src](https://www.thundercompute.com/blog/nvidia-rtx-pro-6000-pricing) | same |

**The Server Edition is 11 % slower in memory bandwidth than the Workstation Edition** (1597 vs 1792 GB/s) for
the same 96 GB. This is a real and frequently-missed distinction: cloud pages and price-comparison sites
routinely quote 1,792 GB/s or "1.79 TB/s" while renting Server Edition silicon
([RunPod](https://www.runpod.io/gpu-models/rtx-pro-6000), [Vast.ai RTX-PRO-6000-S](https://vast.ai/pricing/gpu/RTX-PRO-6000-S)).
Since decode is bandwidth-bound, **always confirm which edition you are renting** — it is an 11 % TPOT swing.

### Usable capacity (METHODOLOGY §3)

All arithmetic below is done in **bytes** and reported in both units (METHODOLOGY §1). The
vendor capacity is **96 GB = 96 × 10⁹ B = 89.41 GiB** — the card is *not* 96 GiB, and the
11 GiB gap is larger than most people's activation workspace, so the distinction is not
pedantic.

```python
cap        = 96e9                     # 96 GB GDDR7, vendor figure, as deployed
usable_090 = cap * 0.90 = 86.40e9 B   # = 80.47 GiB   conservative planning number
usable_095 = cap * 0.95 = 91.20e9 B   # = 84.94 GiB   aggressive; --gpu-memory-utilization 0.95
                                      #               is what the field reports actually use
```

Field-observed occupancy, useful as a sanity check: Nemotron-3-Nano-Omni-30B-A3B NVFP4 on TRT-LLM at 8 K
context reports **88 GB VRAM** resident on a 96 GB card [src](https://github.com/elsung/blackwell-llm-toolkit/blob/main/bench/results.md)
(⚠️ **TO BE VERIFIED** — a re-read of that results table on 2026-09-19 confirmed the throughput rows but did not
surface an explicit VRAM column),
and Nemotron 3 Super NVFP4 (120 B) at `--gpu-memory-utilization 0.95` leaves
**16.79 GiB (Marlin) / 18.37 GiB (FlashInfer-CUTLASS)** of KV cache
[src](https://github.com/vllm-project/vllm/issues/38971) — i.e. 17–19 % of the card as KV after weights and
workspace, consistent with the 0.90–0.95 factors above.

Per 8-GPU node: **768 GB (715.3 GiB)** raw, **691.2 GB (643.7 GiB)** usable at 0.90. That is more aggregate
memory than an 8×H100 node (640 GB) and a substantial fraction of an 8×B200 node
(8 × 180 GB **as deployed**, METHODOLOGY §8 = **1,440 GB**) [src](https://www.nvidia.com/en-us/data-center/hgx/).

---

## 3. Compute per data type

All figures **per GPU**. Source for the whitepaper columns: NVIDIA RTX Blackwell PRO GPU Architecture v1.0,
Appendix A, footnote 2 = "Effective TOPS / TFLOPS using the Sparsity Feature"
[src](https://www.nvidia.com/content/dam/en-zz/Solutions/design-visualization/quadro-product-literature/NVIDIA-RTX-Blackwell-PRO-GPU-Architecture-v1.0.pdf).

### 3a. Workstation Edition (600 W, boost 2,617 MHz) — fully published

| Data type | Dense | Sparse (2:4) | HW-accelerated? | Notes |
|---|---|---|---|---|
| FP64 | **1.97 TFLOPS** `est.` (126.0 ÷ 64) | n/a | Non-tensor, 1/64 rate | Not in the whitepaper table; derived from the Max-Q datasheet figure 1.71 = 109.7/64 [src](https://blog.us.fixstars.com/what-kind-of-gpu-is-the-nvidia-rtx-pro-6000-blackwell-max-q/). **Do not plan HPC FP64 on this card.** |
| FP32 (non-tensor) | **126.0 TFLOPS** | n/a | CUDA cores | Whitepaper Appendix A Table 4 value; NVIDIA's WS **product page prints 125 TFLOPS** [src](https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000/). 126.0 = 24,064 × 2 × 2.617 GHz and is the figure the rest of this table is consistent with; the 125 appears to be a marketing round-down. |
| FP16/BF16 (non-tensor) | 126.0 TFLOPS | n/a | CUDA cores | |
| INT32 (non-tensor) | 126.0 TOPS | n/a | CUDA cores | |
| TF32 tensor | **251.9 TFLOPS** | 503.8 | Yes, 5th-gen TC | 512 FLOP/SM/clk |
| FP16 tensor (FP16 acc) | **503.8 TFLOPS** | 1,007.6 | Yes | 1024 FLOP/SM/clk |
| FP16 tensor (FP32 acc) | **503.8 TFLOPS** | 1,007.6 | Yes | **No FP32-accumulate penalty** (unlike GeForce SKUs) |
| BF16 tensor (FP32 acc) | **503.8 TFLOPS** | 1,007.6 | Yes | |
| FP8 tensor (FP16 or FP32 acc) | **1,007.6 TFLOPS** | 2,015.2 | Yes, 2nd-gen FP8 Transformer Engine | 2048 FLOP/SM/clk |
| FP6 tensor | ⚠️ **TO BE VERIFIED** | ⚠️ | **Yes** (silicon), rate unpublished | "RTX Blackwell adds new support for FP4 **and FP6** Tensor Core operations" [src](https://www.nvidia.com/content/dam/en-zz/Solutions/design-visualization/quadro-product-literature/NVIDIA-RTX-Blackwell-PRO-GPU-Architecture-v1.0.pdf), but **no FP6 rate is published in Appendix A**. Estimation method: on datacenter Blackwell FP6 runs at the FP8 rate; assume **= FP8 (1,007.6 dense)** until measured. CUTLASS ships MXFP6 SM120 kernels [src](https://docs.nvidia.com/cutlass/4.3.2/CHANGELOG.html), which is consistent with real HW support. |
| FP4 / NVFP4 / MXFP4 tensor (FP32 acc) | **2,015.2 TFLOPS** | 4,030.4 ("4000 AI TOPS") | Yes | 4096 FLOP/SM/clk. NVIDIA's headline "4000 AI TOPS" on the WS product page **is the sparse FP4 number**. |
| INT8 tensor | **1,007.6 TOPS** | 2,015.2 | Yes | |
| INT4 tensor | **not listed** ⚠️ TO BE VERIFIED | — | **No published path** | Appendix A has no INT4 row for GB202 (Ampere-era tables did). Estimation method: assume **no INT4 tensor path**; route INT4 checkpoints through W4A16 dequant kernels (Marlin), which is what every engine actually does here anyway. |

### 3b. Max-Q (300 W, boost 2,280 MHz) — fully published

| Data type | Dense | Sparse |
|---|---|---|
| FP64 | 1.71 TFLOPS [src](https://blog.us.fixstars.com/what-kind-of-gpu-is-the-nvidia-rtx-pro-6000-blackwell-max-q/) | n/a |
| FP32 / FP16 / BF16 non-tensor | 109.7 | n/a |
| TF32 tensor | 219.5 | 438.9 |
| FP16 / BF16 tensor | **438.9** | 877.9 |
| FP8 tensor | **877.9** | 1,755.7 |
| FP4 tensor | **1,755.7** | 3,511.4 |
| INT8 tensor | **877.9 TOPS** | 1,755.7 |

### 3c. Server Edition — NVIDIA publishes *mixed* dense/sparse; here is the reconciliation

NVIDIA's Server Edition page lists: FP32 120 TFLOPS, TF32 234 TFLOPS, FP16 1 PFLOPS, BF16 1 PFLOPS,
FP8 2 PFLOPS, FP4 4 PFLOPS [src](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition.md)
[src](https://lenovopress.lenovo.com/lp2263.pdf). Applying the whitepaper's per-SM rates at the clock implied by
FP32 = 120 TFLOPS (boost ≈ 2,494 MHz):

```python
boost = 120e12 / (24064 * 2)          # 2.494 GHz
TF32  = 188 * 512  * boost = 240.0 TFLOPS dense
BF16  = 188 * 1024 * boost = 480.0 TFLOPS dense
FP8   = 188 * 2048 * boost = 960.0 TFLOPS dense
FP4   = 188 * 4096 * boost = 1920.0 TFLOPS dense
```

| Data type | **Dense (plan with this)** | Sparse | What NVIDIA prints | Verdict |
|---|---|---|---|---|
| FP64 | **1.88 TFLOPS** `est.` (120/64) | n/a | — | derived |
| FP32 non-tensor | **120 TFLOPS** | n/a | 120 TFLOPS | dense |
| TF32 tensor | **240 TFLOPS** `est.` | 480 `est.` | **234 TFLOPS** | NVIDIA's 234 ≈ the *dense* figure (within 2.5 % of the derived 240). Inconsistent with the PFLOPS rows below. |
| FP16 / BF16 tensor | **480 TFLOPS** `est.` | 960 `est.` | **"1 PFLOPS"** | 1 PFLOPS ≈ **sparse** (960, rounded up) |
| FP8 tensor | **960 TFLOPS** `est.` | 1,920 `est.` | **"2 PFLOPS"** | **sparse** |
| FP6 tensor | ⚠️ assume 960, TO BE VERIFIED | ⚠️ | — | see 3a |
| FP4 / NVFP4 / MXFP4 tensor | **1,920 TFLOPS** `est.` | 3,840 `est.` | **"4 PFLOPS"** | **sparse** |
| INT8 tensor | **960 TOPS** `est.` | 1,920 `est.` | not listed | derived |
| INT4 tensor | not listed ⚠️ TO BE VERIFIED | — | — | |

**Read this table before quoting anything.** "RTX PRO 6000 does 4 PFLOPS of FP4" is a 2:4-structured-sparsity
number. No production LLM serving path uses 2:4 sparsity. Plan with **1,920 TFLOPS dense FP4 / 960 dense FP8 /
480 dense BF16** on the Server Edition. METHODOLOGY §8 pins this card at **≈ 2,000 dense FP4 (4,000 sparse)**,
which the 1,920 (SE) / 2,015.2 (WS) pair above is consistent with.

**Citation-integrity note (re-fetched 2026-09-19).** NVIDIA's Server Edition page prints
"FP4 Tensor Core: 4 PFLOPS · FP8 Tensor Core: 2 PFLOPS · FP16 | BF16 Tensor Core: 1 PFLOP ·
TF32 Tensor Core: 234 TFLOPS · Single-Precision Performance (FP32): 120 TFLOPS" and **carries no sparsity
footnote at all** [src](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/) —
unlike the Workstation product page, which does. So the "these are sparse" verdict in the table above is an
*inference*, not a quoted footnote. The inference is nonetheless forced: the Server Edition is clocked
**below** the Workstation Edition (2,494 vs 2,617 MHz), and the whitepaper's Workstation dense FP4 is
2,015.2 TFLOPS, so a Server Edition cannot reach 4 PFLOPS dense. `research/cross-cutting/cloud-pricing.md`
§10.3 flags the same ambiguity and makes the same ÷2 assumption; the two docs agree.

### 3d. Sanity check against a measured dense GEMM

A community benchmark measured **dense BF16 GEMM at 376.44 TFLOPS on the 600 W part and 268.14 TFLOPS on the
300 W Max-Q** [src](https://aurorainfra.ai/blog/rtx-pro-6000-blackwell-max-q-vs-600w-benchmarks) (secondary
source, methodology not fully disclosed). Against the whitepaper dense peaks that is **74.7 %** (600 W: 376.44 /
503.8) and **61.1 %** (Max-Q: 268.14 / 438.9) of peak — a plausible cuBLAS efficiency band, and further
confirmation that the "1 PFLOPS" figure is sparse. The same source measures FP32 at 72.34 / 51.55 TFLOPS,
a 1.40× ratio matching the BF16 1.40× ratio, i.e. both variants are cleanly clock-limited.

---

## 4. Interconnect and topology

| Property | Value | Source |
|---|---|---|
| NVLink | **None on any variant** | [src](https://lenovopress.lenovo.com/lp2263.pdf) |
| NVSwitch | None | — |
| Host interface | PCIe 5.0 ×16 | [src](https://lenovopress.lenovo.com/lp2263.pdf) |
| PCIe bandwidth | **64 GB/s per direction, 128 GB/s bidirectional** | [src](https://www.nvidia.com/en-us/data-center/h100/) (same Gen5 ×16 link) |
| Per-GPU interconnect vs B200 | B200 NVLink = 1.8 TB/s per GPU → **14.1× the PCIe Gen5 link** | [src](https://www.nvidia.com/en-us/data-center/hgx/) |
| Per-GPU interconnect vs H100 SXM | H100 NVLink = 900 GB/s per GPU → **7.0×** | [src](https://www.nvidia.com/en-us/data-center/h100/) |
| Typical node | 8× dual-slot passive cards in a PCIe Gen5 server; Lenovo supports only 2× (CBK8) or 4× (CHWT, 450 W-capped) in the SR675 V3 line | [src](https://lenovopress.lenovo.com/lp2263.pdf) |
| Inter-node | Standard NIC (no dedicated GPU fabric). Multi-node LLM serving is not a supported design point for this card. | — |
| P2P (`cudaDeviceCanAccessPeer`) | Works, but **NCCL hangs on the first collective** if IOMMU/ACS are enabled; disabling IOMMU and ACS in BIOS resolves it. Workaround: `NCCL_P2P_DISABLE=1` (slow). Observed on driver 580.126.09 / NCCL 2.28.9 & 2.29.7 / CUDA 13.0. | [src](https://forums.developer.nvidia.com/t/nccl-p2p-hang-on-dual-rtx-pro-6000-blackwell-workstation-edition-wrx90e-sage-se/365048) |

### What "no NVLink" actually costs — measured

This is the single largest architectural consequence and it is well documented:

| Config | Model | Result | Source |
|---|---|---|---|
| **TP=4** (pure tensor parallel, PCIe) | Qwen3.5-397B-A17B-NVFP4, vLLM 0.17.0 | **6–7 tok/s** | [src](https://github.com/flashinfer-ai/flashinfer/issues/2577) |
| **TP=2 + PP=2** | same model, same build | **46–49 tok/s** — ~7× better | [src](https://github.com/flashinfer-ai/flashinfer/issues/2577) |
| TP=2 + **EP=2** | same | 1.4–2.6 tok/s (worst) | [src](https://discuss.vllm.ai/t/sm120-rtx-pro-6000-nvfp4-moe-performance-report-qwen3-5-397b/2536) |
| TP=4, Marlin W4A16, no MTP | same | 50.5 tok/s (best measured) | [src](https://discuss.vllm.ai/t/sm120-rtx-pro-6000-nvfp4-moe-performance-report-qwen3-5-397b/2536) |
| **DP=4** (SGLang, attention-DP) | Qwen3.6-35B-A3B-NVFP4 | 1,408 tok/s @ 8 concurrent; **3,190 tok/s @ 16 concurrent** | [src](https://github.com/sgl-project/sglang/issues/39807) |
| 8-GPU TP | GLM-4.5-Air-AWQ-4bit, vLLM | 15,713 tok/s aggregate; H200 8× reaches ~2× that | [src](https://www.cloudrift.ai/blog/benchmarking-rtx6000-vs-datacenter-gpus) |
| 8-GPU TP | GLM-4.6-FP8 (~640 GB) | H100 8× is **3×** faster, H200 8× is **4×** faster | [src](https://www.cloudrift.ai/blog/benchmarking-rtx6000-vs-datacenter-gpus) |

**Planning rules that follow.**
1. Prefer **replication (DP) over TP** wherever the model fits in one card's 96 GB. One card = one replica is
   the sweet spot of this GPU.
2. When a model needs >1 card, prefer **pipeline parallel (PP) or expert/data parallel over PCIe** to TP.
   PP moves one activation tensor per micro-batch per stage boundary; TP moves an all-reduce per layer.
3. Use METHODOLOGY §4's "20–40 % multi-node TP over InfiniBand" comm-overhead band as the *floor* for TP over
   PCIe Gen5 here, not the 5–15 % NVLink band. The measured 6–7 vs 46–49 tok/s gap above is ~85 % overhead.
4. Fix IOMMU/ACS in BIOS before benchmarking anything multi-GPU, or every number you collect is wrong.

---

## 5. Attention kernel support

### 5a. FlashAttention family

| Kernel | Builds on sm_120? | Runs? | Optimized? | Evidence |
|---|---|---|---|---|
| **FA2** (`flash-attn`, C++/CUTLASS) | Yes in principle — FA2 uses `mma.sync.aligned.m16n8k16`, which sm_120 has | Yes, but the upstream README only claims "Ampere, Ada, or Hopper", and users hit CUDA errors on RTX PRO 6000 as of 2025-11 | **No.** "fa2 don't support fp8 qkv and performance is not good for blackwell" | [src](https://github.com/Dao-AILab/flash-attention/blob/main/README.md) [src](https://github.com/Dao-AILab/flash-attention/issues/1987) |
| **FA3** (`hopper/`) | **No** | **No** | — | README: "FlashAttention-3 is optimized for Hopper GPUs (e.g. H100) … Requirements: **H100 / H800 GPU**, CUDA >= 12.3" [src](https://github.com/Dao-AILab/flash-attention/blob/main/README.md) |
| **FA4** (`flash-attn-4`, CuTeDSL) | **No** | **No** | — | README: "optimized for Hopper and Blackwell GPUs (e.g. **H100, B200**)" [src](https://github.com/Dao-AILab/flash-attention/blob/main/README.md). FA4 is built on `tcgen05`/TMEM, which sm_120 does not have [src](https://github.com/sgl-project/sglang/issues/15342) |
| **FA "hybrid 2/3/4" CuTeDSL for sm_12x** | In flight, **not merged** | Prototype works on DGX Spark | — | PR #2222 (draft, **closed unmerged** 2026-02-21): "With cutlass v4.4.0.dev we starting to get native behavior and support on sm12x devices … The behavior is similar to sm89 MMA, without wargroup like Sm90, but with TMA. **Remember sm12x not has tcgen05** due die space (rt cores) and DLSS algorithm." [src](https://github.com/Dao-AILab/flash-attention/pull/2222) |
| **FA SM120 explicit backend** | PR #2268, **closed unmerged** as of 2026-03-12 | Validated on SM121a (GB10) | — | Fwd + bwd + varlen + split-KV (FlashDecoding) + paged KV, D=64/128, BF16/FP16. "SM120 uses SM80-era MMA instructions (`mma.sync.aligned.m16n8k16`) with 99 KB shared memory." Subclasses `FlashAttentionForwardSm80` with `arch=80`. Paged KV done at Python level because "SM80 swizzled SMEM layout is incompatible with `PagedKVManager`'s tiled copy". [src](https://github.com/Dao-AILab/flash-attention/pull/2268) |

**Bottom line on FlashAttention: as of 2026-09-19 there is no merged, first-class FlashAttention backend for
sm_120.** The maintainer-adjacent explanation is definitive: "FA2 agnostic to arch. FA3 hopper and sm80 in some
cases. **FA4 B200/B300**" [src](https://github.com/Dao-AILab/flash-attention/issues/1987). The practical path is
FlashInfer's FA2 backends (below), not `flash-attn`.

### 5b. FlashInfer — the actual supported attention path on sm_120

| Path | Status | Evidence |
|---|---|---|
| **FA2 backends (`backend="fa2"`)** | **Works. This is the recommendation.** `determine_attention_backend()` returns `"fa2"` for SM12x. | [src](https://github.com/flashinfer-ai/flashinfer/pull/2560) |
| **CUTLASS FMHA (SM100)** | **Explicitly guarded off SM12x** — `supported_major_versions` changed from `[10, 11, 12]` to `[10, 11]`, with an error pointing users to `backend='fa2'`. Merged 2026-03-13. Reason: sm_120 lacks `SM100_MMA_F16BF16_SS/TS` and `SM100_MMA_F8F6F4_SS/TS` (`tcgen05`). | [src](https://github.com/flashinfer-ai/flashinfer/pull/2560) |
| **trtllm-gen FMHA** | **Not available and not planned.** No SM120/SM121 cubins exist; `fmhaRunner.cuh` hard-asserts `mSM == kSM_100 \|\| mSM == kSM_103`. NVIDIA: "TRTLLM-Gen is designed mainly considering support of sm100/103. I believe we have **no plan to SM120/121** so far." Issue closed 2026-07-16. | [src](https://github.com/NVIDIA/TensorRT-LLM/issues/11799) |
| **`fmha_v2` prefill (incl. DeepSeek MLA geometry)** | **Works** on SM120 and SM121. BF16 and FP8 e4m3 both PASS with no NaN on GB10 (batch=2, seq=64, heads=128, qk=192, vo=128). | [src](https://github.com/flashinfer-ai/flashinfer/pull/2559) |
| **`fmha_v2` standard attention (hd 64/128, BF16/FP16)** | PR #2561, **not merged**. Compiles Ampere HMMA (`sm_mma=80`) with `SEPARATE_Q_K_V`. Validated: BF16 hd=128 causal max_diff 0.0039; FP16 hd=128 non-causal 0.00049; GQA 32:8 PASS. | [src](https://github.com/flashinfer-ai/flashinfer/pull/2561) |
| **XQA decode** | **Works** (no NaN) on SM12x; NVIDIA names XQA and FMHA_v2 as the sanctioned consumer-Blackwell fallbacks. | [src](https://github.com/flashinfer-ai/flashinfer/pull/2560) [src](https://github.com/NVIDIA/TensorRT-LLM/issues/11799) |
| **CuTe-DSL prefill (`cute_dsl_prefill_sm120`)** | PR #2561, not merged; wraps CUTLASS `FlashAttentionForwardSm120`. BF16 hd=128 causal max_diff 0.0020. | [src](https://github.com/flashinfer-ai/flashinfer/pull/2561) |
| **Paged KV** | Supported through the FA2/FlashInfer paged path; FA's own sm_120 PR resolves page tables in Python rather than in-kernel. | [src](https://github.com/Dao-AILab/flash-attention/pull/2268) |

### 5c. MLA, FlashMLA, and DeepSeek sparse attention (DSA / lightning indexer)

This is the weakest area on sm_120 and it is worth quoting the failure verbatim. On vLLM with a
576-head-dim MLA model:

```
ValueError: No valid attention backend found for cuda with head_size: 576, dtype: torch.bfloat16,
kv_cache_dtype: auto, block_size: 64, use_mla: True, has_sink: False, use_sparse: True. Reasons:
 {FLASH_ATTN_MLA:  [sparse not supported, compute capability not supported,
                    FlashAttention MLA not supported on this device],
  FLASHMLA:        [sparse not supported, compute capability not supported,
                    vllm._flashmla_C is not available …],
  FLASHINFER_MLA:  [sparse not supported, compute capability not supported],
  TRITON_MLA:      [sparse not supported],
  FLASHMLA_SPARSE: [compute capability not supported]}
```
[src](https://github.com/Dao-AILab/flash-attention/issues/1987)

| Kernel | Status on sm_120 | Evidence |
|---|---|---|
| **FlashMLA** | **Not supported** (compute capability rejected; `vllm._flashmla_C` not built) | [src](https://github.com/Dao-AILab/flash-attention/issues/1987) |
| **FlashInfer MLA** | Not supported for the sparse path | same |
| **Triton MLA** | **Works** as the dense-MLA fallback | same |
| **Triton sparse MLA (DeepSeek V4/DSA)** | **Works**, gated behind `VLLM_TRITON_MLA_SPARSE=1`; CUDA graphs must be explicitly enabled with `VLLM_TRITON_MLA_SPARSE_ALLOW_CUDAGRAPH=1`, which is the difference between ~5 tok/s and 30–35 tok/s | [src](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash/discussions/28) |
| **TileLang DSA sparse-MLA** | **Fails on shared memory.** The default tile needs 202–206 KB; the device allows **99 KB**. `tvm.error.InternalError: Failed to set the allowed dynamic shared memory size to 206848`. Shrinking `block_I` to 16 gets to ~84 KB but then TileLang's `LayoutInference` fails (`Layout infer conflict between m_i and alpha`). Filed with a dead-`O_shared` 64 KB waste identified. | [src](https://github.com/sgl-project/sglang/issues/39302) |
| **NoPE MLA (GLM-5.x, `qk_rope_head_dim=0`)** | **Unrunnable on SM120 as of 2026-09-13** — every `--dsa-prefill-backend`/`--dsa-decode-backend` choice fails; with `--kv-cache-dtype fp8_e4m3` it is unrunnable "by construction" | [src](https://github.com/sgl-project/sglang/issues/39302) |
| **SGLang SM120 DSA work in flight (Sept 2026)** | Very active: #39914 "SM120 candidate fallback and extra KV pages", #39872 "[DSV4.1][SM120] Tune block-FP8 decode for RTX PRO 6000", #39492 "read FlashInfer's dispatch envelope (0.7.0)", #39288 "zero-initialise the 64-token page-split scratch so masked candidates never gather NaN from slot 0", #39235 "decode pads q to 64 heads for an SM90 constraint", #38969 "fall back to Triton per call when FlashInfer lacks the prefill shape" | [src](https://github.com/sgl-project/sglang/issues) |

### 5d. FP8 attention

**Sources conflict — record both.**

- FlashInfer PR #2559 tests `fmha_v2_prefill_deepseek` with **FP8 e4m3** on SM121a: "PASS, no NaN"
  [src](https://github.com/flashinfer-ai/flashinfer/pull/2559).
- SGLang PR #39807 (2026-09-16) states flatly: "**FMHAv2 has no FP8 (e4m3) prefill kernel on SM120/SM121**, and
  ModelOpt NVFP4 checkpoints commonly declare `"kv_cache_quant_algo": "FP8"`, so `--kv-cache-dtype auto` selects
  one and the run dies inside CUDA-graph capture." The fix rejects `trtllm_mha` + FP8 KV at construction
  [src](https://github.com/sgl-project/sglang/issues/39807).
- TRT-LLM side: the `QE4m3KvE2m1` (FP8 query, NVFP4 KV) FMHA kernel exists for SM100a/SM103a only; there is
  **no SM120/SM121 variant**, so NVFP4 KV cache is structurally impossible on this card through TRT-LLM
  [src](https://github.com/NVIDIA/TensorRT-LLM/issues/11799).

Most likely reconciliation (**⚠️ TO BE VERIFIED**): the DeepSeek-geometry FP8 prefill kernel exists, the
*general* `trtllm_mha` FP8 prefill path does not. **Plan for FP8 KV cache working on the generic GQA path
(it is what every cloudrift/community run uses via `--kv-cache-dtype fp8`) and NOT working on the MLA/DSA
path.** FP8 KV is used successfully in published 8-GPU runs [src](https://www.cloudrift.ai/blog/benchmarking-rtx6000-vs-datacenter-gpus)
and on DeepSeek-V4-Flash via the vLLM `ds4-sm120-preview` fork with `fp8_e4m3` KV at **85 %** utilisation giving
~1.97 M tokens of KV across 8 cards [src](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash/discussions/28).

Corroborating the SGLang side of the conflict: the TRT-LLM `strings` dump in
[TensorRT-LLM#11799](https://github.com/NVIDIA/TensorRT-LLM/issues/11799) shows the only SM120 FMHA_v2 symbols
present are `run_fmha_v2_flash_attention_**bf16**_*_sm120_*` — i.e. the sanctioned XQA/FMHA_v2 fallback on
SM12x is **BF16-only**, which is consistent with "no FP8 e4m3 prefill kernel on SM120/SM121".

### 5e. Measured attention throughput

**No published attention-only TFLOPS measurement on sm_120 was found.** ⚠️ **TO BE VERIFIED.**
Estimation ceiling: the measured dense BF16 GEMM efficiency (74.7 % of peak on the 600 W part
[src](https://aurorainfra.ai/blog/rtx-pro-6000-blackwell-max-q-vs-600w-benchmarks)) is the *upper* bound;
an Ampere-MMA-class FA2 kernel in 99 KB of shared memory will land well below it. Use
**MFU_attn 0.20–0.35 BF16** for planning, lower than the METHODOLOGY default for Hopper, because (a) the
kernel is SM80-era, (b) shared memory is 43 % of Hopper's, (c) there is no warp-specialised producer/consumer
pipeline (no `wgmma`, no `tcgen05`).

### 5f. Triton and SageAttention

- The Triton-side prerequisite for the MXFP4 path on SM120 landed via
  [triton-lang/triton#8498](https://github.com/triton-lang/triton/pull/8498) — titled
  "**[NVIDIA] Enable TMA gather4 on sm_120 and sm_121**", merged 2025-10-24. It is the fix that vLLM's
  `mxfp4.py` comment points at, not an "MXFP4 support" PR as such
  [src](https://github.com/triton-lang/triton/pull/8498) [src](https://github.com/vllm-project/vllm/issues/31085).
  Measured **slower than Marlin**:
  140 tok/s vs 185 tok/s on gpt-oss-120b, RTX PRO 6000 Max-Q 300 W
  [src](https://github.com/vllm-project/vllm/issues/31085).
- `TRITON_PTXAS_PATH` must point at the system `ptxas`; the one bundled with Triton 3.5.1 is CUDA 12.8 and
  does not know `sm_121a` [src](https://github.com/sgl-project/sglang/issues/15342).
- SageAttention3 had a working SM120 fallback in SGLang's diffusion path (added 2025-12-19, commit `1e5824880`)
  that was **removed 2026-01-03** (`dcacc492d`), leaving SM120 on `TORCH_SDPA`
  [src](https://github.com/sgl-project/sglang/issues/15342). New work re-adding SM120 Sage for sparse attention
  is open as of 2026-09-18 (#40116).

---

## 6. Quantization format support

| Format | Native tensor core? | Kernels actually used on sm_120 | Measured speedup vs BF16 | vLLM | SGLang | TRT-LLM |
|---|---|---|---|---|---|---|
| **BF16 / FP16** | Yes — 480 TFLOPS dense (SE) | cuBLAS / CUTLASS SM120 | 1.0× baseline | ✅ | ✅ | ✅ |
| **FP8 E4M3 block-scaled (W8A8)** | Yes — 960 TFLOPS dense (SE), 2nd-gen FP8 TE | CUTLASS SM120 FP8; **DeepGEMM does NOT support sm_120** (`arch_major=12` unsupported → `DG_HOST_UNREACHABLE` crash) [src](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash/discussions/28) | ~2× peak FLOPS vs BF16; no end-to-end sm_120 A/B published ⚠️ TO BE VERIFIED | ✅ | ✅ (SM120 blockwise-FP8 decode GEMM still being tuned: #39978, #39872, #39065) | ✅ |
| **MXFP8 (E8M0 scales)** | Yes (CUTLASS SM120 mixed-input blockscaled, 4.2.0) [src](https://docs.nvidia.com/cutlass/4.3.2/CHANGELOG.html) | FlashInfer CUTLASS MXFP8 — explicitly selected on SM120 in SGLang as of 2026-09-17 (#39961/#39967) | ⚠️ TO BE VERIFIED | ⚠️ | ✅ (DSv4.1 path) | ⚠️ |
| **NVFP4 (E2M1 + UE4M3 per-16 scales)** | **Yes** — `mma.sync.aligned.…block_scale`, 1,920 TFLOPS dense (SE) | CUTLASS 3.9.0 added SM120 blockscaled **dense + sparse** GEMM and **grouped GEMM** with NVFP4 (2025-04-24); 4.2.0 added SM120 mixed-input blockscaled grouped GEMM (2025-09-15) [src](https://docs.nvidia.com/cutlass/4.3.2/CHANGELOG.html) | **Dense GEMM: works.** Standalone kernel measures **62–129 TFLOPS, 1.0–2.4× vs BF16 `F.linear`** at inference batch sizes — 2.4× at M=256, **1.0× (parity) at M=512**, 1.4–1.7× at M=2048 (measured on **DGX Spark GB10 / SM121, 128 GB unified LPDDR5x at 273 GB/s** — a severely memory-starved part, so this is not an RTX PRO 6000 measurement) [src](https://github.com/VincentKaufmann/fp4-cuda-kernel). **MoE grouped GEMM: see 6a** — no longer simply "broken". | ⚠️ partial | ⚠️ partial (#39807 open) | ✅ for supported archs |
| **MXFP4 (E2M1 + E8M0 per-32 scales)** | CUTLASS ships SM120 MXFP4 kernels [src](https://docs.nvidia.com/cutlass/4.3.2/CHANGELOG.html), **but** a hands-on report states "MXFP4 weights (E2M1 + E8M0 scales) **can't be used directly on SM121 tensor cores** … SM121 uses NVFP4 with UE4M3 scale factors and a specific interleaved layout (`SfKMajorAtom`). Any MXFP4 model checkpoints need re-quantization" [src](https://github.com/VincentKaufmann/fp4-cuda-kernel) — **conflict, ⚠️ TO BE VERIFIED** | In vLLM: **falls back to Marlin.** `get_mxfp4_backend()` only matches `is_device_capability_family(100)`; the Triton branch was gated to `(9,0) <= cc < (11,0)`. Log: "Your GPU does not have native support for FP4 computation … Weight-only FP4 compression will be used leveraging the Marlin kernel." Open since 2025-12-20, still open (bumped 2026-05-18). [src](https://github.com/vllm-project/vllm/issues/31085) | Marlin **beats** Triton on gpt-oss-120b: 185 vs 140 tok/s (Max-Q 300 W) [src](https://github.com/vllm-project/vllm/issues/31085) | ⚠️ Marlin fallback | ⚠️ | ⚠️ |
| **INT8 (W8A8)** | Yes — 960 TOPS dense (SE) | CUTLASS / Marlin | ⚠️ TO BE VERIFIED | ✅ | ✅ | ✅ |
| **INT4 W4A16 (AWQ / GPTQ via Marlin / Machete)** | No INT4 tensor path published (see §3a); Marlin dequantises to FP16/BF16 and runs on the BF16 tensor cores | **Marlin is the de-facto fastest and most correct path on this GPU today** | Marlin W4A16 vs broken-CUTLASS-NVFP4: **50.5 vs 6–7 tok/s** (Qwen3.5-397B, 4× GPU) [src](https://discuss.vllm.ai/t/sm120-rtx-pro-6000-nvfp4-moe-performance-report-qwen3-5-397b/2536); vs FlashInfer-CUTLASS NVFP4: **92 vs 74 tok/s** on Nemotron 3 Super 120B [src](https://github.com/vllm-project/vllm/issues/38971) | ✅ `--moe-backend marlin` | ✅ | n/a |
| **FP6 / MXFP6** | HW support claimed, rate unpublished (§3a); CUTLASS SM120 MXFP6 kernels exist | — | ⚠️ TO BE VERIFIED | ⚠️ | ⚠️ | ⚠️ |
| **KV cache: FP8 E4M3** | Yes | works on GQA models (`--kv-cache-dtype fp8`); **rejected** on the SM120 `trtllm_mha`/MLA path (§5d) | +2× KV capacity | ✅ | ⚠️ path-dependent | ⚠️ |
| **KV cache: NVFP4** | **No** — the `QE4m3KvE2m1` FMHA read kernel has no SM120/SM121 variant | — | — | ❌ | ❌ | ❌ [src](https://github.com/NVIDIA/TensorRT-LLM/issues/11799) |

### 6a. The NVFP4 MoE grouped-GEMM problem (read this before quoting NVFP4 numbers)

**Symptom.** NVFP4 MoE checkpoints load, serve, and generate tokens — but the expert GEMM returns numerically
wrong results:

```
Prompt: "What is the capital of Kentucky?"
Output: "  ,   ,  (!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
```
[src](https://github.com/NVIDIA/cutlass/issues/3096)

**Scope.** "**Dense (non-MoE) FP4 GEMM works correctly** — the issue is specifically in the **grouped GEMM**
path used by MoE expert computations." All 80 TMA-warp-specialised grouped-GEMM tactics fail at initialisation,
and the FlashInfer autotuner then falls back to slow tactics
[src](https://github.com/NVIDIA/cutlass/issues/3096) [src](https://discuss.vllm.ai/t/sm120-rtx-pro-6000-nvfp4-moe-performance-report-qwen3-5-397b/2536).

**What was tried and failed** [src](https://github.com/NVIDIA/cutlass/issues/3096): GDC/PDL barriers via
`-DCUTLASS_GDC_ENABLED` (compiled, still garbage); FP32 amax in `nvfp4_utils.cuh` (not the root cause);
`KernelPtrArrayTmaWarpSpecializedPingpong` (**segfault**); forcing `compute_120a` gencode (**segfault**).
Also tried and failed: a CUTLASS **4.4.1** upgrade (same broken SM120 grouped block-scaled templates).
What eventually produced *correct* native FP4 MoE output: patched FlashInfer 0.6.5 SM120 capability checks +
CuTe-DSL arch restrictions + `compute_120f` under CUDA 13.0. **Correction (2026-09-19):** the frequently-quoted
**14.6 tok/s is the `compute_120a` / CUDA 12.8 result**, where the TMA warp-specialised tactics still fail and the
autotuner falls back to slow ones. `compute_120f` under CUDA 13.0 **nearly triples that to 39.0 tok/s**
(4-user concurrent: 6.9 → 18.2 tok/s/user) against Marlin's 46–49 — a ~20 % gap, not a 3× one
[src](https://github.com/NVIDIA/cutlass/issues/3096). The issue was closed **2026-04-14** with that fix.

**Update — a native SM120 NVFP4 MoE backend now exists in vLLM, as an opt-in.** On real RTX PRO 6000 hardware
(vLLM main, cu130 + FlashInfer 0.6.12, `nvidia/Qwen3.6-35B-A3B-NVFP4`, 2026-06-11), `--moe-backend flashinfer_b12x`
selects `FLASHINFER_B12X` (FlashInfer's SM12x CuTe-DSL fused MoE), the engine initialises and generation is
coherent. It is simply **not auto-selected**: `oracle/nvfp4.py` excludes B12X "until the upstream CUTLASS SM121 MMA
op guard is resolved". Moreover the reason auto-selection lands on Marlin for these checkpoints is **scheme
coverage, not the architecture** — a ModelOpt NVFP4 checkpoint resolves as **W4A16**, and
`FlashInferExperts._supports_quant_scheme` only lists the W4A4 pair `(kNvfp4Static, kNvfp4Dynamic)` for
capability ≥ 100. Two silent traps push SM120 users onto Marlin with no warning: `flashinfer-python` /
`flashinfer-cubin` version drift, and a stale system `nvcc` picked up when `CUDA_HOME` is unset
[src](https://github.com/vllm-project/vllm/issues/31085).

**A separate, real bug in the reproducer.** The widely-circulated FlashInfer repro
([#2577](https://github.com/flashinfer-ai/flashinfer/issues/2577)) computed the scale in BF16:
`a_scale = (448.0 * 6.0) / a.abs().max()`. With `.float()` added, `mm_fp4` produces correct results on
**both** `cutlass` and `cudnn` backends on RTX PRO 6000 [src](https://github.com/flashinfer-ai/flashinfer/issues/2577).
The `trtllm` backend still raises `BackendSupportedError: mm_fp4 does not support backend 'trtllm' with
capability 120`. **So: dense NVFP4 GEMM on sm_120 was never broken; the repro was.** The MoE grouped path is
genuinely broken.

**Engine-side fixes that landed.** vLLM PR [#33417](https://github.com/vllm-project/vllm/pull/33417) added
sm120-family support to `FlashInferExperts._supports_current_device()`
[src](https://github.com/flashinfer-ai/flashinfer/issues/2577). vLLM added `--moe-backend` so the backend is
selectable [src](https://github.com/vllm-project/vllm/issues/38971). SGLang PR
[#39807](https://github.com/sgl-project/sglang/issues/39807) (open, CI red as of 2026-09-16) routes SM12x to
`FLASHINFER_CUTLASS` instead of the SM100-only `FLASHINFER_TRTLLM`.

### 6b. The backend trade, quantified

| Backend | Single-user tok/s | KV cache available | Output correctness | Source |
|---|---|---|---|---|
| Marlin W4A16 (vLLM 0.17.1 default) | **~92** | 16.79 GiB / 732,160 tokens / 14.27× concurrency @262K | Correct | [src](https://github.com/vllm-project/vllm/issues/38971) |
| FLASHINFER_CUTLASS (vLLM 0.19.0 default) | ~74 (**−20–25 %**) | 18.37 GiB / 798,720 tokens / 15.62× (**+9 %**) | Degraded / garbage depending on model | [src](https://github.com/vllm-project/vllm/issues/38971) |

Model: Nemotron 3 Super NVFP4 120B, `--gpu-memory-utilization 0.95`, `--max-num-seqs 512`.
**Marlin trades 9 % of KV capacity for 20–25 % of decode throughput and correctness.** Take the trade.

### 6c. Working recipe (community-validated)

```bash
python -m vllm.entrypoints.openai.api_server \
  --model nvidia/Qwen3.5-397B-A17B-NVFP4 \
  --tensor-parallel-size 2 --pipeline-parallel-size 2 \
  --moe-backend marlin \
  --max-model-len 32768 --gpu-memory-utilization 0.95
```
4× RTX PRO 6000 Blackwell, vLLM 0.17.0 → 46–49 tok/s single user, ~148 tok/s at 4 concurrent, ~212 tok/s at
8 concurrent. "**Do NOT use the `VLLM_TEST_FORCE_FP8_MARLIN=1` env var** — it causes CUDA driver errors in
spawned workers. The `--moe-backend marlin` CLI flag works correctly."
[src](https://github.com/flashinfer-ai/flashinfer/issues/2577)

### 6d. MTP / speculative decoding caution

On this card, MTP **hurt** on one NVFP4 MoE and **helped** on another — do not assume:

| Model / stack | MTP off | MTP on | Δ |
|---|---|---|---|
| Qwen3.5-397B-A17B-NVFP4, vLLM, 4 GPU | **50.5 tok/s** | 39–40 tok/s | **−22 %** ("activation distribution mismatch") [src](https://discuss.vllm.ai/t/sm120-rtx-pro-6000-nvfp4-moe-performance-report-qwen3-5-397b/2536) |
| Qwen3.6-35B-A3B-NVFP4, SGLang, 1 GPU | 246 tok/s | **286–297 tok/s** | **+16–21 %** [src](https://github.com/sgl-project/sglang/issues/39807) |
| Qwen3.6-27B Q6_K_XL, llama.cpp PR #22673 | 53.62 tok/s | **110.45 tok/s** | **+106 %** (quality identical across all eval axes) [src](https://github.com/elsung/blackwell-llm-toolkit/blob/main/bench/results.md) |
| MiniMax-M2.7-REAP-172B (MoE), llama.cpp ngram-cache | 117.02 | 115.96 | −0.9 % — "**Ngram speculation is only worth it for dense models**" [src](https://github.com/elsung/blackwell-llm-toolkit/blob/main/bench/results.md) |

---

## 7. Inference engine support and maturity (as of 2026-09-19)

### vLLM

| | |
|---|---|
| Runs on sm_120 | **Yes.** cc ≥ 7.5 supported generally; Blackwell needs CUDA ≥ 12.8 [src](https://docs.vllm.ai/en/latest/getting_started/installation/gpu.html) |
| sm_120 kernels present | `csrc/quantization/fp4/nvfp4_scaled_mm_sm120_kernels.cu`, `nvfp4_blockwise_moe_kernel.cu` (`ENABLE_NVFP4_SM120`); commits `e50209864` "[Kernel] Add NVFP4 MoE CUTLASS support for SM120", `c0dfc8948` "SM120 / NVFP4: add device guard and runtime SM dispatch"; `cutlass_scaled_mm_supports_fp4(120) → True` [src](https://github.com/vllm-project/vllm/issues/31085) |
| Version timeline seen in the field | 0.13.0 (Dec 2025) → 0.17.0/0.17.1 → 0.19.0 (FLASHINFER_CUTLASS becomes NVFP4 MoE default on SM120) → 0.20.1/0.20.2. **These are the versions the cited sm_120 reports ran on, not the current release.** |
| **Current release (re-verified 2026-09-19)** | **vLLM 0.29.0** (PyPI `vllm` 0.29.0; GitHub release 2026-09-09) — nine minor releases past the newest build any sm_120 report above used. Re-check every sm_120 claim in this section against 0.29.0 before acting on it. [src](https://pypi.org/pypi/vllm/json) [src](../cross-cutting/inference-engines.md) |
| Known open issues | **#31085** MXFP4 backend selection excludes SM120 → Marlin fallback (open since 2025-12-20, bumped 2026-05-18). **#27471** spurious NVFP4A16 warnings. **#27542** gpt-oss-120b on RTX PRO 6000. PR **#57456** adds sm_120 tuned configs for batch-invariant persistent matmul. |
| Fix that landed | `--moe-backend` CLI flag (answer to #38971, 2026-04-05) [src](https://github.com/vllm-project/vllm/issues/38971); PR #33417 sm120-family in `FlashInferExperts` |
| Containers | Official images default to cu128; users repeatedly ask for a cu130 image [src](https://github.com/vllm-project/vllm/issues/31085) — **⚠️ TO BE VERIFIED** whether one ships as of 2026-09-19 |

### SGLang

| | |
|---|---|
| Runs on sm_120 | **Yes**, and it is the most actively developed sm_120 stack right now |
| Versions seen | 0.5.19 with FlashInfer 0.6.18, torch 2.13.0+cu130, tilelang 0.1.12, nvcc 13.4 [src](https://github.com/sgl-project/sglang/issues/39302) |
| **Current release (re-verified 2026-09-19)** | **SGLang 0.5.20**, released **2026-09-18** (PyPI `sglang` 0.5.20) — the `0.5.19` above is **one release stale**; the DSA/SM120 issues cited in §5c were filed against 0.5.19 and several may already be closed in 0.5.20. [src](https://pypi.org/pypi/sglang/json) [src](../cross-cutting/inference-engines.md) |
| Containers | `lmsysorg/sglang:deepseek-v4-blackwell` ⚠️ **TO BE VERIFIED** — the DeepSeek-V4-Flash discussion actually recommends the **vLLM** image `ununnilium/vllm-ds4-sm120:20260618` (branch `ds4-sm120-preview`), because "official vLLM lacks SM120 support"; no SGLang image is named there [src](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash/discussions/28). **`sgl-kernel` cu130 wheel required** for SM121a cubins (the cu128 wheel only carries `sm_120a`) [src](https://github.com/sgl-project/sglang/issues/15342) |
| Open SM120 work (Sept 2026) | #40175 fp8_fa_sm120 FP8 attention backend · #40173 hybrid NVFP4/MXFP4 draft experts via CUTLASS MoE on SM90/SM120 · #40116 SM120 Sage for sub-block sparse attention · #39978 optimise SM120 FP8 blockwise decode GEMM · #39914 DeepSeek-V4.1 SM120 candidate fallback · #39872 tune block-FP8 decode for RTX PRO 6000 · #39807 NVFP4 serving for SM120/121 · #39492 FlashInfer 0.7.0 dispatch envelope · #39302 GLM-5.x NoPE MLA unrunnable · #39288 sparse-MLA NaN from slot 0 · #39235 q padded to 64 heads for an SM90 constraint · #39215 DCP for GLM-5.2 on SM120 with MTP · #39065/#39063 SM120 DeepGEMM grouped FP8 requantisation · #38969 Triton per-call fallback when FlashInfer lacks the prefill shape [src](https://github.com/sgl-project/sglang/issues) |
| Known gaps | Diffusion pipeline `get_attn_backend_cls_str()` still forces SM120 → `TORCH_SDPA` (#15342, closed stale 2026-04-19); NVFP4 multimodal compressed-tensors layer-strip bug [src](https://github.com/elsung/blackwell-llm-toolkit/blob/main/bench/results.md) |

### TensorRT-LLM

| | |
|---|---|
| Runs on sm_120 | **Yes** — verified in the field on v1.2.1 and v1.3.0rc13 [src](https://github.com/elsung/blackwell-llm-toolkit) |
| **Current release (re-verified 2026-09-19)** | **1.2.1 stable** (PyPI `tensorrt-llm` 1.2.1) with **1.3.0rc27** uploaded 2026-09-17. The field reports above used **rc13**; fourteen rc tags have shipped since, and §9e shows a 2.6× prefill swing from a single rc bump — pin an rc explicitly. [src](https://pypi.org/pypi/tensorrt-llm/json) [src](../cross-cutting/inference-engines.md) |
| trtllm-gen FMHA / batched-GEMM cubins | **Not available, not planned.** "trtllm-gen does not have support for SM120/121 yet. And SM100 kernels cannot run on SM120 directly. You may use **XQA or FMHA_v2** as fallback." Issue closed 2026-07-16. [src](https://github.com/NVIDIA/TensorRT-LLM/issues/11799) |
| MoE backend | Must set `moe_config.backend: CUTLASS` — "TRTLLM backend is B200-specific" [src](https://github.com/elsung/blackwell-llm-toolkit) |
| Mamba-hybrid knobs (undocumented) | `kv_cache_config.enable_block_reuse: false`, `mamba_ssm_cache_dtype: float32` [src](https://github.com/elsung/blackwell-llm-toolkit) |
| Model coverage on sm_120 | NemotronH + NVFP4 ✅ (rc13+) · Qwen3-VL + NVFP4 ✅ · Qwen 3.5/3.6 + NVFP4 ❌ (GDN plugin pending, #11674) · Gemma 4 + NVFP4 ⚠️ AutoDeploy path only (PR #12710) [src](https://github.com/elsung/blackwell-llm-toolkit/blob/main/bench/results.md) |
| Practical note | rc tags are weeks ahead of stable for sm_120 model handlers — check rc tags before writing a custom handler [src](https://github.com/elsung/blackwell-llm-toolkit/blob/main/bench/results.md) |
| NVFP4 KV cache | **Impossible** on SM120/121: no `QE4m3KvE2m1` FMHA kernel; the C++ dispatcher hits contradictory assertions (`attentionOp.cpp:2793` forces `use_fp8_context_fmha=true`, then `fmhaDispatcher.cpp:70` rejects `Kv=e2m1`) [src](https://github.com/NVIDIA/TensorRT-LLM/issues/11799) |

### NVIDIA Dynamo

**No sm_120-specific support or exclusion statement was found.** ⚠️ **TO BE VERIFIED.** Given that Dynamo's
disaggregated prefill/decode design assumes a fast KV transfer fabric and this card has PCIe only, expect
disaggregation to be bandwidth-limited here; treat it as unvalidated on sm_120.

### Supporting libraries

| Library | sm_120 status |
|---|---|
| **DeepGEMM** | **Not supported.** `arch_major=12` unsupported; triggers `DG_HOST_UNREACHABLE` / "Unknown SF transformation" crashes. `support_deep_gemm()` must exclude cc 12.0. [src](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash/discussions/28) SGLang has open work on SM120 grouped-FP8 DeepGEMM weight requantisation (#39065/#39063). |
| **LMCache** | PyPI `lmcache==0.4.4-cu13` ships sm_70/75/80/86/89/90/100 cubins and **no sm_120, no PTX** → `cudaErrorNoKernelImageForDevice` in `lmc_ops.multi_layer_kv_transfer`. Must build from source (~1.5 min). Verify with `cuobjdump --list-elf … \| head -3` expecting `sm_120.cubin`. [src](https://github.com/elsung/blackwell-llm-toolkit) |
| **llama.cpp** | Works. MTP PR #22673; DeepSeek-V4 via `cchuter/feat/v4-port-cuda`; experimental SM120 CUTLASS MoE prefill for MXFP4/NVFP4 in [ggml-org/llama.cpp#26704](https://github.com/ggml-org/llama.cpp/pull/26704) |
| **CUTLASS** | SM120 since **3.9.0** (2025-04-24): blockscaled dense + sparse GEMM, NVFP4/MXFP8/MXFP6, grouped GEMM with NVFP4. SM121 and SM120 mixed-input blockscaled grouped GEMM since **4.2.0** (2025-09-15). [src](https://docs.nvidia.com/cutlass/4.3.2/CHANGELOG.html) |
| **FlashInfer** | 0.6.3/0.6.4/0.6.5 buggy for SM120 NVFP4; SM12x guards merged in 0.6.x (PR #2560, 2026-03-13); 0.6.18 and 0.7.0 referenced by SGLang SM120 work |

---

## 8. Pricing

### 8a. Cloud — on-demand

**⚠️ REBUILT 2026-09-19.** This section previously priced the card off a third-party aggregator
(computeprices.com), which produced a "cheapest on-demand $0.59–0.95/GPU-h" headline that no vendor rate card
supports. METHODOLOGY §6 and the repo rule require rows from
[`research/cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) §5.10 only — each of those rows
was pulled from the **vendor's own price page or price API** on 2026-09-19. The aggregator numbers are kept
below the table as a clearly-labelled secondary cross-check, not as prices.

| Provider | Instance / listing | GPUs | $/GPU-hour on-demand | Reserved 1 y / 3 y | Spot | Source |
|---|---|---|---|---|---|---|
| **Nebius** | RTX PRO 6000 | 1–8 | **$1.80** | ⚠️ commitment discounts advertised, no rate card | **$0.95** (preemptible) | cloud-pricing.md §4.3 · [Nebius prices](https://nebius.com/prices) |
| **Hyperstack** | RTX PRO 6000 **SE** | 1–8 | **$1.85** | **$1.30** (reserved) | — | cloud-pricing.md §4.5 · [Hyperstack](https://www.hyperstack.cloud/gpu-pricing) |
| **RunPod** Secure | RTX PRO 6000 | 1 | **$2.09** | — | — | cloud-pricing.md §4.4 · [RunPod](https://www.runpod.io/pricing) |
| **RunPod** Community | RTX PRO 6000 | 1 | $1.69 | — | — | same — third-party hosts, no SLA; do not size a production replica against it |
| **CoreWeave** | RTX PRO 6000 (High Memory) | 8 | **$2.500** | contact sales | $1.386 | cloud-pricing.md §4.1 · [CoreWeave](https://www.coreweave.com/pricing) |
| **CoreWeave** | RTX PRO 6000 (Standard) | 8 | contact sales | contact sales | $1.195 | same |
| **AWS** | `g7e.48xlarge` | 8 | **$4.143** | N/A | $2.506 | cloud-pricing.md §3.1 · AWS price sheet, us-east-1 |
| **AWS** | `g7e.2xlarge` | 1 | $3.363 | — | — | same |
| **Google Cloud** | `g4-standard-384` | 8 | **$4.500** | $3.105 (resource CUD 1 y) / **$1.979** (3 y) | $1.743 | cloud-pricing.md §3.2 · GCP accelerator price sheet |
| **Google Cloud** | `g4-standard-48` | 1 | $4.500 | $3.105 / $1.979 | $1.743 | same |
| **Oracle OCI** | RTX PRO 6000 (B112613) | — | **$4.50** | ⚠️ | ⚠️ | cloud-pricing.md §3.3 · OCI public price-list API |
| **Azure** | — | — | **No SKU** | — | — | cloud-pricing.md §5.10 |
| **Lambda** | — | — | **Not offered** | — | — | [src](https://computeprices.com/providers/lambda) |
| **Scaleway** | — | — | **Not offered** — their Blackwell SKU is B300-SXM | — | — | [src](https://www.scaleway.com/en/pricing/gpu/) |

**Cheapest reputable on-demand: Nebius $1.80** (Hyperstack $1.85, RunPod Secure $2.09, CoreWeave $2.50).
**Cheapest hyperscaler: AWS $4.143. Cheapest committed: GCP 3-yr resource CUD $1.979 / Hyperstack reserved
$1.30. Cheapest spot: Nebius $0.95 / GCP $1.743.** This is the only Blackwell-generation GPU with a normal,
self-serve on-demand price at *every* hyperscaler that carries it.

**The $0.59–$1.30 figures this section used to print were aggregator rows, and three of them were
vendor *reserved or spot* rates mislabelled as on-demand:** "Nebius $0.950" is Nebius's **preemptible** rate
(on-demand $1.80), "CoreWeave $1.20" is near CoreWeave's **spot** $1.195 (on-demand $2.500), and
"Hyperstack $1.30" is Hyperstack's **reserved** rate (on-demand $1.85). GPU Outlet $0.590, Verda $0.930/$0.949,
PowerGPU $1.04, AtmosCompute $1.10, Lium $1.19, 1Legion $1.19, Spheron $1.20–$2.27 and the
getdeploying "$2.19 market median across 39 providers" have **no vendor-page corroboration in
cloud-pricing.md** and are **⚠️ TO BE VERIFIED**; treat them as marketplace chatter, not planning inputs.
Vast.ai's RTX-PRO-6000-**S** page showed **"No current offers"** on 2026-09-19
[src](https://vast.ai/pricing/gpu/RTX-PRO-6000-S), so its $0.93 / $0.16-spot figures remain unsupported.

**Caveat on the marketplace rows:** aggregator listings do **not** distinguish Server Edition (1,597 GB/s) from
Workstation Edition (1,792 GB/s). Vast.ai is the only one that separates S / WS / Max-Q — and its S page
itself prints "1.79 TB/s", i.e. Workstation bandwidth against Server silicon. RunPod's own spec sheet quotes
1,792 GB/s [src](https://www.runpod.io/gpu-models/rtx-pro-6000), which is WS silicon. Hyperstack is the one
vendor rate card that says "SE" explicitly.

### 8b. Purchase

| Item | Price | Date | Source |
|---|---|---|---|
| Launch MSRP (Workstation) | **$8,565** | 2025-03 | [src](https://www.thundercompute.com/blog/nvidia-rtx-pro-6000-pricing) |
| NVIDIA marketplace list | **$16,000** (+87 % in ~18 months) | 2026-09 | [src](https://videocardz.com/newz/nvidia-raises-rtx-pro-6000-blackwell-price-to-16000-now-87-above-original-msrp) |
| Prior raise | $13,250 (+55 %) | earlier 2026 | [src](https://www.tomshardware.com/pc-components/gpus/nvidia-raises-rtx-pro-6000-blackwell-gpu-pricing-to-usd13-250-55-percent-increase-over-msrp-in-a-years-time) |
| Retailer range | $15,999 – $18,850 | 2026-09-18 | [src](https://www.thundercompute.com/blog/nvidia-rtx-pro-6000-pricing) |
| **Server Edition**, Newegg | **$14,999** ⚠️ **TO BE VERIFIED** — a re-read on 2026-09-19 found **no Server-Edition-specific price** on that page; it lists Newegg **$15,999+**, B&H **$15,999+** and a used/refurbished band of **$14,980–$18,850** (the $14,980 low is the nearest figure) | 2026-09 | [src](https://www.thundercompute.com/blog/nvidia-rtx-pro-6000-pricing) |
| **Server Edition**, 19-listing average | **$17,016** ⚠️ **TO BE VERIFIED** — no multi-listing average and no listing count appears on the cited page | 2026-09 | [src](https://www.thundercompute.com/blog/nvidia-rtx-pro-6000-pricing) |
| Cause cited | GDDR7 memory shortage; 96 GB clamshell design is "acutely sensitive to GDDR7 supply constraints" | 2026-09 | [src](https://www.thundercompute.com/blog/nvidia-rtx-pro-6000-pricing) |

**Disagreement with `cloud-pricing.md` §9 — state it, do not silently pick one (METHODOLOGY §8).**
This document plans with the **$16,000** NVIDIA marketplace list (videocardz, 2026-09, "+87 % above original
MSRP"). [`research/cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) plans with NVIDIA list
**$13,250** (Tom's Hardware, "+55 % vs launch MSRP ~$8,548") and a street average of **$14,758 / Newegg
$14,999 / Amazon $19,999**. The two are consistent as a *sequence* — $8,565 launch → $13,250 → $16,000 — so
the gap is a freshness gap, not a factual one. **Use $16,000 for a purchase decision made today and
$13,250 when reconciling against cloud-pricing.md's TCO tables**, and note the ±20 % that follows.
The $14,999 Newegg figure the row above marks ⚠️ *does* appear in cloud-pricing.md §9, but as a listing for
the card generally — **not** as a Server-Edition-specific price, which is what this document had claimed and
what the verification log (#61) correctly could not find.

### 8c. Colo-amortised $/GPU-hour (8× Server Edition node)

```python
card      = 16_000          # $ per GPU, NVIDIA marketplace 2026-09
n         = 8
chassis   = 35_000          # ⚠️ TO BE VERIFIED: 8-slot PCIe Gen5 server, 2S CPU, 2 TB RAM, NVMe, NICs
capex     = card*n + chassis            # $163,000
hours     = 3*365*24                    # 26,280 h over 3 years
kw        = (600*n + 1200)/1000 * 1.35  # 8.10 kW at PUE 1.35, 1.2 kW host overhead
power_3y  = kw * hours * 0.10           # $21,287 at $0.10/kWh
total     = capex + power_3y            # $184,287
$/GPU-h   = total / (n * hours)         # $0.877 at 100% utilisation
```

| Scenario | $/GPU-hour |
|---|---|
| 600 W, 100 % utilisation | **$0.877** `est.` |
| 600 W, 80 % utilisation | **$1.096** `est.` |
| 600 W, 60 % utilisation | **$1.461** `est.` |
| **450 W slot-capped**, 100 % utilisation (6.48 kW, $17,029 power) | **$0.856** `est.` |

All chassis, PUE, and $/kWh inputs are **⚠️ TO BE VERIFIED** assumptions; the card price and TDP are sourced.

**Headline, recomputed against §8a's vendor rates (was computed against the withdrawn aggregator rows).**
Owned hardware at **$0.877/GPU-h (100 % util.)** beats every *on-demand* rate in §8a by a wide margin —
2.1× cheaper than Nebius $1.80, 2.8× cheaper than CoreWeave $2.50, 4.7× cheaper than AWS $4.143 — and still
wins at 60 % utilisation ($1.461 vs $1.80). What it does **not** beat is committed and preemptible capacity:
Hyperstack reserved **$1.30** and GCP 3-yr CUD **$1.979** are in the same band, and Nebius preemptible
**$0.95** / CoreWeave spot **$1.195** / GCP spot **$1.743** undercut or match it with no capex and no
utilisation risk. The previous sentence ("breaks even … only at high utilisation, and loses outright to
Vast.ai spot. Against the $2.19 market median it is ~2.5× cheaper") rested on aggregator prices that
cloud-pricing.md does not carry, and is withdrawn.

Cross-check: [`cloud-pricing.md`](../cross-cutting/cloud-pricing.md) §9 runs the same colo model with
different inputs (8 × $13,250 card + ~$30 k platform = ~$140 k, ~6.5 kW) and lands at **$0.962/GPU-h**
amortised. The 10 % spread against the $0.877 above is entirely the card price and chassis assumption.
Either figure supports the same conclusion.

### 8d. Power

| Variant | TGP | Perf/W from the measured community A/B |
|---|---|---|
| Server Edition | 600 W, cappable to 450 W (Lenovo SKU); "400 W" floor ⚠️ TO BE VERIFIED (§1) | — |
| Workstation | 600 W | 589.9 GFLOP/J on dense BF16 |
| **Max-Q** | **300 W** | **873.7 GFLOP/J** — **+48 %** efficiency; "Max-Q used 26.5–27.8 % less energy per output token" [src](https://aurorainfra.ai/blog/rtx-pro-6000-blackwell-max-q-vs-600w-benchmarks) |

**Read the two GFLOP/J numbers carefully:** in the cited run they are **node** figures measured over a
ten-minute dense-compute phase on **eight 300 W Max-Q cards vs four 600 W cards**, not a single-card A/B
[src](https://aurorainfra.ai/blog/rtx-pro-6000-blackwell-max-q-vs-600w-benchmarks). The per-card dense-BF16
ratio implied by §3d is smaller: `376.44/600 = 627 GFLOP/J` vs `268.14/300 = 894 GFLOP/J`, i.e. **+43 %**,
not +48 %. The direction of the conclusion is unchanged. The same source also notes both parts reached
**~1,465 GB/s effective device-memory traffic**, i.e. ~82 % of the 1,792 GB/s peak.

> **Which 1,792 GB/s is which.** Every number in this §8d comes from an A/B between the **Workstation
> Edition (600 W)** and the **Max-Q Workstation Edition (300 W)**. Both are 1,792 GB/s parts, so 1,792 is the
> correct peak to divide by *here*, and the ~82 % MBU figure is a Workstation-silicon result. It is **not**
> transferable to the Server Edition, whose peak is **1,597 GB/s** (§1, §2). Every roofline in §9 uses 1,597.
> Applying the same 82 % efficiency to the Server Edition gives ~1,310 GB/s effective, `est.`

For **decode-bound** serving the 300 W part is nearly free performance: short-context TP1 at concurrency 8 on
Qwen3-4B measured **955.7 (300 W) vs 958.9 (600 W) tok/s, +0.3 %** — because decode is bandwidth-bound and both
**Workstation-class** parts have the same 1,792 GB/s
[src](https://aurorainfra.ai/blog/rtx-pro-6000-blackwell-max-q-vs-600w-benchmarks). A Server Edition run of the
same workload should land ~11 % lower (1,597/1,792) — `est.`, not measured.
The 600 W part only pulls ahead where compute matters: long-context TP4 32 K (+8.5 %) and DeepSeek-V4 TP2 C64
decode (+7–13 %).

---

## 9. Roofline numbers for inference

### 9a. Ridge point (arithmetic intensity) — Server Edition, 1,597 GB/s

| dtype | Dense TFLOPS | Ridge point (FLOP/byte) | bytes/FLOP |
|---|---|---|---|
| TF32 | 240.0 | 150.3 | 0.00665 |
| BF16 / FP16 | 480.0 | 300.6 | 0.00333 |
| FP8 / INT8 | 960.0 | 601.1 | 0.00166 |
| NVFP4 / MXFP4 | 1,920.0 | 1,202.3 | 0.00083 |

Interpretation: at FP4 you need **>1,200 FLOP per byte moved** before the card is compute-bound. A decode step
at batch 1 has an arithmetic intensity of ~2 FLOP/byte. **Decode on this GPU is bandwidth-bound by a factor of
~600× at FP4.** Quantising weights below FP8 buys you *capacity and bandwidth*, not compute, in decode.
Compare: on the Workstation Edition (1,792 GB/s, 2,015 TFLOPS FP4) the FP4 ridge point is **1,124 FLOP/byte** —
slightly friendlier.

### 9b. Decode upper bound = bandwidth ÷ bytes moved

`tokens/s ≈ BW × MBU / weights_read_bytes`, with **BW = 1,597e9 B/s (Server Edition — §1, §2)**; weights-only,
add KV reads at long context. METHODOLOGY §4's band for first-gen Blackwell software is MBU 0.5–0.7. On sm_120,
with Marlin dequant in the loop and no NVLink, **use MBU 0.50–0.65** as the planning band and treat 0.70 as
optimistic. On a Workstation Edition, scale every row by 1,792/1,597 = **+12.2 %**.

**⚠️ RECOMPUTED 2026-09-19 — the "Weights" column was asserted without arithmetic, and three of its seven
rows were GiB values printed under a GB heading.** Each row is now derived from the model's own geometry and
METHODOLOGY §1's bytes-per-param table, in bytes, and reported in **GB (10⁹ B)** with the GiB in brackets.
Rows whose numbers changed carry the superseded value.

| Model / footprint (1 GPU unless noted) | Weights, derived (GB) | GiB | MBU 0.55 | MBU 0.70 | was |
|---|---|---|---|---|---|
| 8 B BF16 | `8e9 × 2.0` = **16.00** | 14.90 | **54.9** tok/s | 69.9 | 16 GB ✔ |
| 8 B FP8 (32×32 UE8M0, +0.1 %) | `8e9 × 1.001` = **8.01** | 7.46 | **109.7** | 139.6 | 8 GB ✔ |
| 30 B-A3B AWQ-4bit (Qwen3-30B-A3B) | `(30.53e9 − 0.622e9) × 0.53 + 0.622e9 × 2` = **17.10** | 15.92 | **51.4** | 65.4 | 17 GB ✔ (51.7/65.8) |
| 70 B FP8 | `70e9 × 1.001` = **70.07** | 65.26 | **12.5** | 16.0 | 70 GB ✔ |
| gpt-oss-120b MXFP4 | `114.66e9 × 0.53125 + 2.13e9 × 2.0` = **65.17** | 60.69 | **13.5** | 17.2 | **61 GB → that was 60.69 GiB** (14.4/18.3) |
| Qwen3-235B-A22B NVFP4, 2 GPU | `(235.09e9 − 1.245e9) × 0.5625 + 1.245e9 × 2` = 134.03 → **67.01/GPU** | 62.41 | **13.1** | 16.7 | **63 GB/GPU → that was 62.41 GiB** (13.9/17.7) |
| Qwen3.5-397B-A17B NVFP4, 4 GPU | `397e9 × 0.5625` = 223.31 → **55.83/GPU** ⚠️ floor | 51.99 | **15.7** | 20.0 | **52.5 GB/GPU → that was 51.99 GiB** (16.7/21.3) |

Derivations, in full:

```python
# gpt-oss-120b — config.json re-fetched 2026-09-19: 36 layers, hidden 2880, 128 experts,
# vocab 201,088, head_dim 64, 8 KV heads, quantization_config.quant_method = "mxfp4" with
# modules_to_not_convert = [self_attn, mlp.router, embed_tokens, lm_head]  -> those stay BF16.
experts = 36 * (128*2880*(2*2880) + 128*2880*2880) = 114.662e9   # MXFP4 @ 0.53125 B/param
rest    = 116.789e9 - 114.662e9 = 2.127e9                        # attn + router + embeddings, BF16 @ 2.0
bytes   = 114.662e9*0.53125 + 2.127e9*2.0 = 6.5168e10 B = 65.17 GB = 60.69 GiB
# cross-check: the published checkpoint is ~60.7 GiB. The old "61 GB" was that GiB number.
```
```python
# Qwen3-30B-A3B AWQ INT4 g128 — 48 L, hidden 2048, vocab 151,936, tie_word_embeddings=False
emb   = 2 * 151936 * 2048 = 0.622e9         # embed + lm_head stay BF16 (AWQ ignores them)
bytes = (30.53e9 - emb)*0.53 + emb*2.0 = 1.710e10 B = 17.10 GB = 15.92 GiB
```
NVFP4 rows use METHODOLOGY §1's **0.5625 B/param** (E2M1 + one FP8 E4M3 per 16, **+12.5 %**), not the 0.53
a flat-4-bit assumption would give — which is exactly why the two multi-GPU rows moved. The Qwen3.5-397B row
is marked ⚠️ **floor**: its per-tensor-group split is not published here, and every real ModelOpt NVFP4
checkpoint keeps embeddings/`lm_head`/attention at BF16 or FP8, so the true figure is **higher** than
55.83 GB/GPU, never lower. METHODOLOGY §1 forbids applying one flat rate to a mixed checkpoint; this row does,
knowingly, for lack of the group table.

**Reality check against measurement.** For a *dense* read of all weights these bounds are conservative for MoE,
because MoE decode only reads the experts actually hit (METHODOLOGY §4). Qwen3.5-397B-A17B NVFP4 on 4 GPUs
measures **50.5 tok/s** [src](https://discuss.vllm.ai/t/sm120-rtx-pro-6000-nvfp4-moe-performance-report-qwen3-5-397b/2536)
against a **15.7–20.0 tok/s** "read everything" bound — i.e. at MBU 0.70 the step actually moves
`1597e9 × 0.70 / 50.5 = 22.1e9` B out of 55.83e9, so **~40 % of the parameter bytes are touched per step at
batch 1**. (Unchanged conclusion; the bound itself moved from 16.7–21.3.)
Qwen3.6-35B-A3B-NVFP4 measures **246 tok/s** on one card
[src](https://github.com/sgl-project/sglang/issues/39807): at 3 B active in FP4 (~1.7 GB active + attention),
`1597e9 × 0.55 / 1.7e9 ≈ 517` — so the achieved MBU on the *active* bytes is ~0.26, which is the honest number
for a small-expert MoE where the expert GEMMs are tiny and launch-bound.

### 9c. KV cache bytes per token (METHODOLOGY §2)

| Model geometry | BF16 | FP8 |
|---|---|---|
| Llama-3.3-70B — 80 L, 8 KV heads, 128 head_dim (GQA) | `80×2×8×128×2` = 327,680 B = **320.0 KiB/token** | 163,840 B = 160.0 KiB |
| gpt-oss-120b — 36 L, 8 KV heads, 64 head_dim, **but only 18 full-attention layers** | `18×2×8×64×2` = 36,864 B = **36.0 KiB/token** + fixed **4.5 MiB/seq** | 18,432 B = 18.0 KiB/token + 2.25 MiB/seq |
| gpt-oss-120b, naive "all 36 layers" (the number to *stop* quoting) | 73,728 B = 72.0 KiB/token | 36.0 KiB |
| DeepSeek-R1 / V3 MLA — 61 L × (512 + 64) | `61×576×2` = **70,272 B/token** = 68.6 KiB | 35,136 B = 34.3 KiB (FP4: 17,568 B) |
| Qwen3-30B-A3B — 48 L, 4 KV heads, 128 head_dim (GQA) | `48×2×4×128×2` = 98,304 B = **96.0 KiB/token** | 49,152 B = 48.0 KiB |

The MLA row's 70,272 / 35,136 / 17,568 B per token at BF16 / FP8 / FP4 are METHODOLOGY §8's pinned
DeepSeek-R1/V3 figures; this document reproduces them, so there is no disagreement to record. Note that
**FP4 KV is not reachable on sm_120 through any FMHA path** (§5d, §6): the `QE4m3KvE2m1` kernel has no
SM120/SM121 variant. Plan MLA models here at the **FP8** column. The one documented exception is a model that
dequantises its own FP4 cache in software rather than reading it from a tensor core — see §9g.

**Correction (2026-09-19) — gpt-oss-120b is a sliding-window hybrid.** Its `config.json` has
`sliding_window: 128` and `layer_types` alternating **18 `sliding_attention` / 18 `full_attention`** layers
[src](https://huggingface.co/openai/gpt-oss-120b/raw/main/config.json). Per METHODOLOGY §2 the sliding layers
are capped at `W` tokens per sequence, so they contribute a *fixed* per-sequence state, not a per-token cost:
`18 × 128 × 2 × 8 × 64 × B` = **4.5 MiB BF16 / 2.25 MiB FP8 per sequence**. Only the 18 full-attention layers
scale with context: **36.0 KiB/token BF16, 18.0 KiB/token FP8.** The 72 KiB/token figure double-counts and is
wrong for any context beyond 128 tokens.

**Qwen3-30B-A3B geometry**, for the `30 B-A3B` rows used throughout this document: 48 layers,
`num_key_value_heads = 4`, `head_dim = 128` → `48 × 2 × 4 × 128 × 2` = **96.0 KiB/token BF16**
[src](https://huggingface.co/Qwen/Qwen3-30B-A3B/raw/main/config.json).

### 9d. Max concurrency (1 GPU, usable 86.40e9 B = 80.47 GiB, 4e9 B activation workspace)

**⚠️ Recomputed twice. The first pass (fact-check, 2026-09-19) fixed an off-by-`n_kv_heads` division; this
pass re-ran every row against §9b's derived weight bytes, which moved the gpt-oss-120b row again.** All values
are `floor(max_concurrency(ctx))` per METHODOLOGY §3. A row that cannot fit **one** sequence at that context
is printed as `infeasible (KV)`, never as a fraction (METHODOLOGY §3 consistency rule).

| Model | Weights (GB) | KV budget (GB) | 8 K ctx | 32 K ctx | 128 K ctx | previously printed |
|---|---|---|---|---|---|---|
| 70 B FP8 (GQA, FP8 KV @160 KiB/tok) | 70.07 | 12.33 | **9 seqs** | **2** | **infeasible (KV)** (0.57) | 74 / 18 / 4.6 — **8× too high**; then 9 / 2.3 / 0.6 |
| gpt-oss-120b MXFP4 (FP8 KV, 18 KiB/tok + 2.25 MiB/seq, §9c) | **65.17** | **17.23** | **112 seqs** | **28** | **7** | 567 / 142 / 35 → 140 / 35 / 8.8 (used 61 GB of weights, which was 60.69 **GiB**) |
| gpt-oss-120b, same but naive 36 KiB/tok (no sliding-window cap) | 65.17 | 17.23 | 57 seqs | 14 | 3 | 71 / 18 / 4.4 |
| Qwen3-30B-A3B AWQ-4bit (BF16 KV @96 KiB/tok) | 17.10 | 65.30 | **81 seqs** | **20** | **5** | 1,040 / 260 / 65 — used a KV of ~7.5 KiB/tok that no 30B-A3B geometry produces |
| Qwen3-30B-A3B AWQ-4bit (FP8 KV @48 KiB/tok) | 17.10 | 65.30 | **162 seqs** | **40** | **10** | — (41 at 32 K was a rounding-up error: 40.5 floors to 40) |

Worked example for the first row, straight from METHODOLOGY §3, in bytes:
`kv_budget = 86.40e9 − 70.07e9 − 4e9 = 12.33e9 B`; one 8 K sequence at 160 KiB/token costs
`8192 × 163,840 = 1.342e9 B`; `12.33e9 / 1.342e9 = 9.19` → **9 sequences**, not 74. The original row implicitly
used **20 KiB/token** (=160 ÷ 8), and the gpt-oss row **4.5 KiB/token** (=36 ÷ 8) — the same
off-by-`n_kv_heads` mistake in both, which METHODOLOGY §2 explicitly forbids (`n_kv_heads` is a *multiplier*).
**A 70 B FP8 model on one 96 GB card is a ~9-concurrent-request server at 8 K, and cannot serve even one
request at 128 K.** That is the single most decision-relevant number in this document.

The gpt-oss-120b sliding-window correction (§9c) is worth restating as a ratio, because it cuts both ways:
capping the 18 `sliding_attention` layers at `W = 128` **doubles** achievable concurrency (112 vs 57 at 8 K)
versus the naive all-36-layer accounting — but the honest weight figure (65.17 GB, not 60.69 GiB mislabelled
as GB) takes 20 % of that back (112 vs the 140 the first correction pass printed). Both effects are in the
table above.

`config.json` for both geometries was re-fetched on 2026-09-19 and confirms the inputs:
gpt-oss-120b has `sliding_window: 128`, `num_key_value_heads: 8`, `head_dim: 64` and a `layer_types` array of
exactly **18 `sliding_attention` + 18 `full_attention`**
[src](https://huggingface.co/openai/gpt-oss-120b/raw/main/config.json); Qwen3-30B-A3B has
`num_hidden_layers: 48`, `num_key_value_heads: 4`, `head_dim: 128`, `sliding_window: null`
[src](https://huggingface.co/Qwen/Qwen3-30B-A3B/raw/main/config.json).

Cross-check: the measured Nemotron 3 Super NVFP4 120 B run reports 732,160–798,720 KV tokens on one card at
`--gpu-memory-utilization 0.95` [src](https://github.com/vllm-project/vllm/issues/38971); DeepSeek-V4-Flash FP8
KV at **85 %** utilisation across 8 cards reports **~1.97 M tokens** [src](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash/discussions/28).

### 9e. Prefill (GEMM-only, METHODOLOGY §4)

`prefill_tok/s ≈ peak_FLOPS(dtype) × MFU / (2 × active_params)`. On sm_120, use **MFU 0.25–0.40 for FP8 and
0.20–0.35 for FP4** (below the METHODOLOGY Hopper band, for the shared-memory and MMA-generation reasons in §5e).

| Model | Active params | dtype | MFU 0.30 | MFU 0.40 |
|---|---|---|---|---|
| 70 B dense | 70 B | BF16 | 1.0 k tok/s | 1.4 k |
| 70 B dense | 70 B | FP8 | 2.1 k | 2.7 k |
| gpt-oss-120b | 5.1 B | FP4 | 56.5 k | 75.3 k |
| 30 B-A3B | 3 B | FP4 | 96.0 k | 128.0 k |

**Measured prefill on this card** (single-card, TRT-LLM 1.3.0rc13, NVFP4, 5,426-token prompt):
**62,407 tok/s** on Nemotron-3-Nano-Omni-30B-A3B [src](https://github.com/elsung/blackwell-llm-toolkit/blob/main/bench/results.md).
That sits right inside the MFU 0.30–0.40 band for a ~3 B-active FP4 MoE — **the FP4 prefill path on sm_120 is
real and healthy when the kernels are the dense/TRT-LLM-CUTLASS ones.** Contrast the 24,094 tok/s on the
non-Omni variant with TRT-LLM 1.2.1 — **a 2.6× prefill swing from an rc-tag bump alone.**

### 9f. Observed MFU / MBU ranges

| Workload | Observed | Source |
|---|---|---|
| Dense BF16 GEMM (600 W) | **74.7 % of dense peak** (376.44 / 503.8 TFLOPS) | [src](https://aurorainfra.ai/blog/rtx-pro-6000-blackwell-max-q-vs-600w-benchmarks) |
| Dense BF16 GEMM (300 W Max-Q) | **61.1 % of dense peak** (268.14 / 438.9) | same |
| FP32 non-tensor GEMM (600 W) | 57.4 % (72.34 / 126.0) | same |
| NVFP4 dense GEMM, standalone (SM121/GB10, **not** RTX PRO 6000) | **62–129 TFLOPS**; **1.0–2.4×** vs BF16 `F.linear` (2.4× at M=256, **1.0× parity at M=512**, 1.4–1.7× at M=2048); **0.7× at M=4096** where BF16 cuBLAS wins | [src](https://github.com/VincentKaufmann/fp4-cuda-kernel) |
| FP4 prefill MFU, 3 B-active MoE | ~0.30–0.40 `est.` from 62,407 tok/s | derived |
| Decode MBU, small-expert MoE | ~0.26 `est.` on active bytes | derived (§9b) |
| Long-context decode, DeepSeek-V4 @64 K+, 8 GPU | **3–5 tok/s** — sparse-decode kernels prioritised correctness over speed on SM120 | [src](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash/discussions/28) |

### 9g. The four repo models on this card — fit and KV only

Same inputs as §9d: usable `96e9 × 0.90 = 86.40e9 B` per GPU, 4e9 B activation workspace, `floor()` on
concurrency, weights and KV from METHODOLOGY §8. **Throughput and cost per (model, GPU) are out of scope for a
GPU document** — they live in `research/models/<exp>/rtx6000-pro.md` and are written in the next phase:
[`models/deepseek41f/rtx6000-pro.md`](../models/deepseek41f/rtx6000-pro.md) ·
[`models/qwen3827b/rtx6000-pro.md`](../models/qwen3827b/rtx6000-pro.md) ·
[`models/kimik3/rtx6000-pro.md`](../models/kimik3/rtx6000-pro.md) ·
[`models/marlin2b/rtx6000-pro.md`](../models/marlin2b/rtx6000-pro.md).

| Model | min GPUs | weights/GPU | KV budget/GPU | KV + fixed state per seq | 8 K | 32 K | 128 K |
|---|---|---|---|---|---|---|---|
| **Qwen3.8-27B** BF16 (55.56 GB) | **1** | 55.56 GB | 26.84 GB | 64 KiB/tok + 153.9 MB GDN | **38** | 11 | 3 |
| **Qwen3.8-27B** FP8 (30.87 GB) | **1** | 30.87 GB | 51.53 GB | 32 KiB/tok + 153.9 MB | **122** | 41 | 11 |
| **Qwen3.8-27B** NVFP4 (21.92 GB) | **1** | 21.92 GB | 60.48 GB | 32 KiB/tok + 153.9 MB | **143** | 49 | 13 |
| **Qwen3.8-27B** INT4 W4A16 (19.45 GB) | **1** | 19.45 GB | 62.95 GB | 32 KiB/tok + 153.9 MB | **149** | 51 | 14 |
| **Marlin-2B** BF16 (5.444 GB) | **1** | 5.44 GB | 76.96 GB | 12 KiB/tok + 18.63 MiB GDN | **640** | 182 | 47 |
| **DeepSeek-V4.1-Flash** MXFP4 (510.29 GB) | **8** | 63.79 GB | 18.61 GB (148.9 GB aggregate) | 1,650 B/tok FP8 KV + 2.77 MiB SWA | **9,068** | 2,613 | 679 |
| **Kimi-K3** MXFP4 (1,560.9 GB) | **19** ⚠️ | — | — | 13.5 KiB/tok FP8 MLA + 2.25 GB KDA | **does not fit an 8-card box** | | |

**Qwen3.8-27B and Marlin-2B fit on one card, comfortably.** Both are the shape this GPU was built for
(§10b: Lenovo's own scoping is "fewer than 70B parameters", re-verified verbatim 2026-09-19). Three details
matter here and nowhere else in this document:

- Qwen3.8-27B is **48 Gated-DeltaNet linear-attention layers + 16 full-attention layers**, so only 16 layers
  cache per token (`16 × 2 × 4 × 256 × 2` = 65,536 B = 64 KiB BF16). The price is a **fixed 153.9 MB fp32
  recurrent state per slot** — METHODOLOGY §2's `S` multiplier. The rows above assume **`S = 1`**
  ⚠️ **TO BE VERIFIED**: vLLM's Mamba-style cache allocates ≥ 1 plus speculative slots, and the state may be
  bf16 (78.4 MB) rather than fp32. At `S = 2` the 8 K BF16 row drops from 38 to 36; the fixed state only
  becomes decision-relevant at high concurrency and short context.
- Qwen3.8-27B NVFP4 is one of the few NVFP4 checkpoints that is **safe on sm_120**: it quantises
  `lm_head` + the 64 layers' `mlp.{gate,up,down}_proj`, which are **dense** GEMMs — and dense NVFP4 GEMM on
  sm_120 was never broken (§6a). The broken path is MoE *grouped* GEMM, and this model has no MoE.
  Two caveats survive: vLLM still resolves ModelOpt NVFP4 checkpoints as **W4A16** and auto-selects Marlin
  (§6a), so the NVFP4 row buys **capacity, not speed**, without `--moe-backend`-style opt-in; and TRT-LLM's
  Qwen 3.5/3.6 + NVFP4 handler is blocked on a pending GDN plugin (§7, #11674).
- Marlin-2B's video path is the binding case, not text: a 240-frame / 2-minute clip at 2 fps is **23,520
  tokens**, giving `floor(76.96e9 / (23520 × 12,288 + 19.54e6))` = **249 concurrent video requests** on one
  card. Nothing about this model stresses a 96 GB card.

**DeepSeek-V4.1-Flash needs at least 8 cards, over PCIe, on Marlin W4A16.** `510.29e9 / (86.40e9 − 4e9)` =
**6.19**, so the smallest topology step that fits is **8** (METHODOLOGY §3 allows {1,2,4,8,…}). Four things
make this a bad deployment on this GPU even though it technically fits:

1. **The routed experts are MXFP4** (E2M1 + one E8M0 per 32), **not NVFP4** — non-expert tensors are FP8 with
   32×32 UE8M0 scales (METHODOLOGY §8). On sm_120 vLLM's `get_mxfp4_backend()` matches only
   `is_device_capability_family(100)`, so MXFP4 **falls back to Marlin W4A16** (§6) — the same fallback that
   beats Triton 185 vs 140 tok/s on gpt-oss-120b, but a dequant path, not a tensor-core FP4 path.
2. **NVIDIA's NVFP4 build is not a fix.** It is **527.27 GB — larger than the 510.29 GB base** (only 58 % of
   its bytes are NVFP4; the Engram tables stay FP8), it is **accuracy-neutral with no published speedup over
   the base**, and on sm_120 it routes into exactly the MoE grouped-GEMM path that §6a documents as broken by
   default. **Do not reach for the NVFP4 build on this card.**
3. **The 890 B/token KV figure does not apply by default.** METHODOLOGY §8 pins 890 B/token (720 main +
   170 indexer) as the **FP4-KV** figure and labels the kernel Blackwell-only, which on sm_120 would be
   unreachable — §5d and §6 confirm there is no `QE4m3KvE2m1` SM12x kernel, so NVFP4 KV is structurally
   impossible through TRT-LLM here. **Disagreement to record (METHODOLOGY §8):**
   [`models/deepseek41f/architecture.md`](../models/deepseek41f/architecture.md) §5.2 quotes the DeepSeek
   report saying the FP4 cache is *dequantised before attention* — *"FP4 reduces storage rather than
   accelerates matrix multiplication … allows us to use a more accurate format without requiring native
   matrix-multiplication support"* — and concludes "**this is why the FP4 KV works identically on Hopper and
   Ampere**". If that software-dequant path is what the engine implements, 890 B/token is reachable on
   sm_120 too. The table above plans with the **FP8 column (1,650 B/token, 1.85×)** because that is the
   dtype this card is certain to support; at 890 B/token the 8 K row would be 14,605 instead of 9,068.
4. **KV is never the binding constraint for this model — weights and PCIe are.** Even the conservative FP8-KV
   row admits thousands of concurrent sequences, which is exactly what vLLM's own recipe summary says
   ("Weights and batch size, not cache, set the capacity limit"). What binds is that ~183 GiB of the
   checkpoint is **Engram random-gather tables** (≈ 22.9 GiB per GPU across 8), and that 8-way EP/TP on this
   card runs over **PCIe Gen5 with no NVLink** — where §4's measurements show TP collapsing to 6–7 tok/s and
   PP/DP being the only workable split. Treat the fit as arithmetic, not as a recommendation.

**Kimi-K3 does not fit an 8-card box, and it is not close.** `1,560.9e9 / (86.40e9 − 4e9)` = **18.94** → at
least **19 cards** (topology step: 32). Eight cards offer `8 × 82.40e9` = **659.2 GB** of weight-capable
memory against 1,560.9 GB needed — **short by 901.7 GB, i.e. 2.4× the box**. The row above prices a
hypothetical 20-card deployment only to show why it is not a design point: per-GPU KV budget collapses to
**4.36 GB**, and SGLang allocates **S = 5** KDA state slots per request at 428.6 MiB each = **2.25 GB per
request** (METHODOLOGY §8), so a single in-flight request consumes half the budget of a card; the fit is
36 / 32 / 21 sequences at 8 K / 32 K / 128 K, on a 20-card PCIe cluster spanning three chassis with no GPU
fabric. §4 already states multi-node LLM serving is **not a supported design point for this card**. Kimi-K3
belongs on 8×B300 (2,144 GB per node, 195 GB/GPU — METHODOLOGY §8), not here.

---

## 10. Published LLM inference benchmarks

### 10a. MLPerf Inference

| Round | Submitter | System | Models | Result |
|---|---|---|---|---|
| **v6.0** (April 2026) | **Nebius** | Systems actually described in that post: **NVIDIA HGX B200 (8 GPU), HGX B300 (8 GPU), GB300 NVL72 (1, 8 and 72 GPU)**. ⚠️ **CORRECTED 2026-09-19:** a re-read found **no 8× RTX PRO 6000 Server Edition MLPerf node and no VR200 NVL72 preview** in this post. RTX PRO 6000 appears only as a "See also" link to a separate post, *Introducing NVIDIA RTX PRO 6000 Blackwell Server Edition on Nebius* | DeepSeek R1, Qwen3-VL 235B, gpt-oss 120B, server + offline | **No RTX PRO 6000 MLPerf result of any kind in this source.** ⚠️ **TO BE VERIFIED against the MLCommons v6.0 results table** — treat "Nebius submitted RTX PRO 6000 to MLPerf v6.0" as unsupported until it appears there. [src](https://nebius.com/blog/posts/mlperf-inference-v6-0-results) |
| v6.0 scope | MLCommons | — | adds gpt-oss 120B text-gen, WAN-2.2 text-to-video, a VLM benchmark, DLRMv3, YOLOv11 | — |
| v6.1 | AMD, Lambda, Nebius | — | agentic + VLM benchmarks; Vera Rubin NVL72 | No RTX PRO 6000 row identified ⚠️ TO BE VERIFIED |

**InferenceMAX (SemiAnalysis): no RTX PRO 6000 results identified as of 2026-09-19.** ⚠️ **TO BE VERIFIED.**
InferenceMAX coverage is B200 / GB200 / H200-class (e.g. B200 at 60,000 tok/s/GPU on gpt-oss with TRT-LLM;
GB200 NVL72 at 26 k prefill / 13 k decode tok/s/GPU on DeepSeek R1 with SGLang)
[src](https://newsletter.semianalysis.com/p/inferencemax-open-source-inference)
[src](https://www.lmsys.org/blog/2025-10-14-sa-inference-max/).

### 10b. Vendor claims (marketing — labelled as such)

| Claim | Baseline | Source |
|---|---|---|
| "**up to 5× higher LLM inference throughput** for agentic AI" | vs L40S | [src](https://blogs.nvidia.com/blog/rtx-pro-6000-blackwell-server-edition) — **marketing claim**, no model, batch, or precision disclosed |
| "nearly 7× faster" genomics; "3.3×" text-to-video; "~2×" recommenders; ">2×" rendering | vs L40S / prior gen | same — **marketing claims** |
| "up to 5× the performance of the previous generation for LLM inference with support for FP4" | 5th-gen Tensor Cores | [src](https://lenovopress.lenovo.com/lp2263.pdf) — **marketing claim** |
| Lenovo's own positioning: suited to "LLM Fine-Tuning, Inference, and RAG workloads with **fewer than 70B parameters**" | — | [src](https://lenovopress.lenovo.com/lp2263.pdf) — useful, honest scoping from the OEM |

### 10c. Independent / community measurements

| Model | Engine + version | Precision | GPUs | Concurrency | Throughput | TTFT / TPOT | Source |
|---|---|---|---|---|---|---|---|
| Nemotron-3-Nano-**Omni**-30B-A3B | TRT-LLM 1.3.0rc13 | NVFP4 modelopt | 1 (WS) | 1 | **269.81 tok/s** decode; **62,407 tok/s prefill** @5,426 tok | **TTFT 24 ms** | [src](https://github.com/elsung/blackwell-llm-toolkit/blob/main/bench/results.md) |
| same | same | same | 1 | 4 | 705 tok/s aggregate | — | same |
| Nemotron-3-Nano-30B-A3B | TRT-LLM 1.2.1 | NVFP4 | 1 | 1 | 249.15 tok/s; 24,094 prefill | TTFT 29 ms | same |
| same | same | same | 1 | 4 | 682 tok/s | — | same |
| Qwen3.6-35B-A3B-NVFP4 | SGLang (PR #39807) | NVFP4 | 1 | 1 | **246 tok/s** (MTP off) / **286–297** (MTP on) | — | [src](https://github.com/sgl-project/sglang/issues/39807) |
| same | SGLang, dp=4 | NVFP4 | 4 | 8 / 16 | **1,408 / 3,190 tok/s** aggregate | — | same |
| Qwen3.5-397B-A17B-NVFP4 | vLLM 0.17.0, TP2+PP2 | NVFP4 → Marlin W4A16 | 4 | 1 / 4 / 8 | **46–49 / ~148 / ~212 tok/s** | — | [src](https://github.com/flashinfer-ai/flashinfer/issues/2577) |
| same | vLLM, TP4, Marlin, no MTP | same | 4 | 1 | **50.5 tok/s** (best correct) | — | [src](https://discuss.vllm.ai/t/sm120-rtx-pro-6000-nvfp4-moe-performance-report-qwen3-5-397b/2536) |
| same | vLLM, TP4, FlashInfer-CUTLASS default | NVFP4 | 4 | 1 | **6–7 tok/s, garbage output** | — | same |
| Nemotron 3 Super 120B | vLLM 0.17.1 (Marlin) vs 0.19.0 (FI-CUTLASS) | NVFP4 | 1 | 1 | **92 vs 74 tok/s** | — | [src](https://github.com/vllm-project/vllm/issues/38971) |
| gpt-oss-120b | vLLM, Marlin vs Triton | MXFP4 | 1 (**Max-Q 300 W**) | 1 | **185 vs 140 tok/s** | — | [src](https://github.com/vllm-project/vllm/issues/31085) |
| DeepSeek-V4-Flash | SGLang `deepseek-v4-blackwell` ⚠️ (see §7 — the source names a vLLM image) | FP8 W8A8 + FP8 KV | 8 | chat | **37–49 tok/s** decode ⚠️ **TO BE VERIFIED** — the source's own figures are **30–35 tok/s** (vLLM, CUDA graphs on) and **~5 tok/s** (CUDA graphs off); ~2,000 tok/s prefill @16–32 K also not located on re-read | latency 1.5–5.6 s ⚠️ TBV | [src](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash/discussions/28) |
| same, EAGLE spec-dec (2 draft) | same | same | 8 | 1 | 45.6 tok/s avg | — | same |
| same, 64 K+ context | same | same | 8 | 1 | **3–5 tok/s** | — | same |
| same, user report | SGLang, 400 K ctx | FP8 | 4 | 1 | 105 peak tok/s decode | — | same |
| DeepSeek-V4-Flash IQ2_XXS-XL | llama.cpp (`cchuter/feat/v4-port-cuda`) | GGUF ~2-bit, 78.6 GB | 1 | 1 | 31 tok/s; 233 tok/s prefill | TTFT 189 ms | [src](https://github.com/elsung/blackwell-llm-toolkit/blob/main/bench/results.md) |
| MiniMax-M2.7-REAP-172B-A10B | llama.cpp master | Q3_K_S GGUF | 1 | 1 | 117 tok/s; 2,547 prefill; **196 K ctx** | TTFT 23 ms | same |
| MiniMax-M2.7-REAP-172B | vLLM 0.20.1 + LMCache→Optane | W4A16 AutoRound | 1 | 1 | 20–24 tok/s flat from 64 K→**154 K ctx** | — | same |
| Qwen3-Coder-30B-A3B-Instruct-AWQ | ⚠️ engine unspecified | AWQ | 1 | **400** | **~8,400 tok/s**; ~$0.02/M tok at $0.59/h | — | [src](https://www.spheron.network/blog/rent-nvidia-rtx-pro-6000/) (secondary, CloudRift Oct 2025) |
| GLM-4.5-Air-AWQ-4bit | vLLM, 8 K ctx, FP8 KV | AWQ 4-bit | 1 | 256–512 | **3,140 tok/s** (vs **H100 2,987**) | — | [src](https://www.cloudrift.ai/blog/benchmarking-rtx6000-vs-datacenter-gpus) |
| GLM-4.5-Air-AWQ-4bit | vLLM, TP8 | AWQ 4-bit | 8 | 256–512 | 15,713 tok/s (H200 ≈ 2×) | — | same |
| Qwen3-Coder-480B-A35B-AWQ (320 GB) | vLLM, TP8 | AWQ 4-bit | 8 ⚠️ **TO BE VERIFIED** — the source table labels this row **4×** for all three GPUs | 256–512 | H100 **+31 %**, H200 **+100 %** vs PRO 6000 ⚠️ TO BE VERIFIED (only the $/M-token row was recoverable on re-read) | — | same |
| GLM-4.6-FP8 (~640 GB) | vLLM, TP8 | FP8 | 8 | 256–512 | H100 **3×**, H200 **4×** vs PRO 6000 | TTFT 8,804 ms; **TPOT 180.76 ms**; P99 ITL 1,479 ms | same |
| Qwen3-4B (short ctx, TP1) | ⚠️ engine unspecified | ⚠️ | 1 | 8 | 955.7 (Max-Q) / 958.9 (600 W) tok/s | — | [src](https://aurorainfra.ai/blog/rtx-pro-6000-blackwell-max-q-vs-600w-benchmarks) |
| Qwen3-4B 32 K, TP4 | same | ⚠️ | 4 | — | 1,404.4 (Max-Q) / 1,523.3 (600 W) | — | same |
| DeepSeek-V4 dense, TP2 C64 decode | same | ⚠️ | 2 | 64 | 2,262.9 (Max-Q) / 2,549.5 (600 W) aggregate — ⚠️ **TO BE VERIFIED**: these two numbers were **not found** on re-read. The source reports this workload at node scale: **8× 300 W Max-Q (dual TP4 engines) 6,410.2 tok/s vs 4× 600 W (dual TP2 engines) 4,817.0 tok/s** — different GPU counts, so not a per-card A/B | — | same |

### 10d. Cost per million tokens (published)

Measured by CloudRift on their own hardware and pricing, 8 K context, `--kv-cache-dtype fp8`
[src](https://www.cloudrift.ai/blog/benchmarking-rtx6000-vs-datacenter-gpus):

| Model | PRO 6000 | H100 | H200 | L40S |
|---|---|---|---|---|
| GLM-4.5-Air-AWQ-4bit, 1 GPU | **$0.18** | $0.25 | $0.17 | $0.29 |
| GLM-4.5-Air-AWQ-4bit, 8 GPU | $0.30 | $0.31 | $0.26 | — |
| Qwen3-480B-AWQ, 8 GPU | $1.03 | $1.01 | $0.78 | — |
| GLM-4.6-FP8, 8 GPU | **$1.72** | $0.76 | $0.72 | — |

**This table is the whole argument in four rows.** Single-GPU, 4-bit, model-fits-in-96 GB → the RTX PRO 6000 is
the cheapest per token of anything listed. Eight-GPU, FP8, model needs all-reduce → it is **2.3–2.4× more
expensive per token than H100/H200** because PCIe replaces NVLink.

---

## 11. Known gotchas

**Kernel availability (the big ones)**
1. **FA3 and FA4 will never run here.** Any stack that hard-codes "Blackwell → FA3/FA4" crashes, as SGLang's
   diffusion path did [src](https://github.com/sgl-project/sglang/issues/15342). Route to FlashInfer FA2.
2. **trtllm-gen FMHA and batched-GEMM cubins do not exist for SM12x and NVIDIA has no plan to ship them**
   [src](https://github.com/NVIDIA/TensorRT-LLM/issues/11799). Everything that depends on those cubins
   (FlashInfer `backend="trtllm"`, `VLLM_USE_FLASHINFER_MOE_FP4=1`) is unavailable.
3. **NVFP4 MoE grouped GEMM produces garbage** through the *default* CUTLASS/FlashInfer path built with
   `compute_120`/`compute_120a`. Dense NVFP4 GEMM is fine [src](https://github.com/NVIDIA/cutlass/issues/3096).
   Since §6a's update this is no longer the whole story: `compute_120f` under CUDA 13.0 fixes correctness *and*
   reaches 39.0 tok/s, and vLLM main has an opt-in native backend (`--moe-backend flashinfer_b12x`). Marlin is
   still the safe default, but "NVFP4 MoE is broken on sm_120" is now an over-statement — read §6a before quoting it.
4. **DeepGEMM is unsupported** (`arch_major=12`) and crashes with `DG_HOST_UNREACHABLE`
   [src](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash/discussions/28).
5. **NVFP4 KV cache is structurally impossible** — no `QE4m3KvE2m1` SM12x kernel
   [src](https://github.com/NVIDIA/TensorRT-LLM/issues/11799).
6. **99 KB shared memory ceiling.** Kernels tuned for Hopper/B200's ~228 KB fail to launch, not fall back.
   TileLang DSA sparse-MLA needs 202 KB and dies [src](https://github.com/sgl-project/sglang/issues/39302).
7. **LMCache PyPI wheels have no sm_120 cubins and no PTX** → `cudaErrorNoKernelImageForDevice`; build from
   source [src](https://github.com/elsung/blackwell-llm-toolkit).
8. **CUDA graphs are the single largest perf knob on the sparse-MLA path**: 5 tok/s without,
   30–35 with (`VLLM_TRITON_MLA_SPARSE_ALLOW_CUDAGRAPH=1`)
   [src](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash/discussions/28).
9. **GLM-5.x NoPE MLA (`qk_rope_head_dim=0`) is unrunnable** on SM120 as of 2026-09-13
   [src](https://github.com/sgl-project/sglang/issues/39302).

**Interconnect and topology**
10. **Enable-P2P-first.** NCCL hangs on the first collective unless **IOMMU and ACS are disabled in BIOS**
    [src](https://forums.developer.nvidia.com/t/nccl-p2p-hang-on-dual-rtx-pro-6000-blackwell-workstation-edition-wrx90e-sage-se/365048).
    `NCCL_P2P_DISABLE=1` unblocks but is slow.
11. **TP over PCIe collapses.** TP=4 gave 6–7 tok/s where TP2+PP2 gave 46–49 on the same build
    [src](https://github.com/flashinfer-ai/flashinfer/issues/2577).

**Power and thermal**
12. Server Edition is **passively cooled** — it is entirely dependent on chassis airflow, and Lenovo qualifies
    it in only a narrow server list (2× at 600 W, 4× at the 450 W cap in SR675 V3)
    [src](https://lenovopress.lenovo.com/lp2263.pdf). "8× in a server" is a general-market configuration, not a
    universally-qualified one.
13. A published 4×600 W Wan2.2 video run **failed a thermal health gate**
    [src](https://aurorainfra.ai/blog/rtx-pro-6000-blackwell-max-q-vs-600w-benchmarks). For decode-bound
    serving the 300 W Max-Q is within 0.3 % of the 600 W part — **buy Max-Q or cap to 450 W unless your
    workload is prefill-heavy.**

**Toolchain**
14. **CUDA < 13.2 + glibc ≥ 2.41 = no JIT kernels at all** (C23 `rsqrt` `noexcept` mismatch)
    [src](https://github.com/sgl-project/sglang/issues/39807).
15. **`TRITON_PTXAS_PATH`** must point at the system `ptxas`; Triton 3.5.1 bundles a CUDA 12.8 one
    [src](https://github.com/sgl-project/sglang/issues/15342).
16. **pip CUDA wheels are runtime-only** (`lib/` not `lib64/`, `libcudart.so.13` with no unversioned dev
    symlink) → `cannot find -lcudart` at link time [src](https://github.com/sgl-project/sglang/issues/39807).
17. **`VLLM_TEST_FORCE_FP8_MARLIN=1` causes CUDA driver errors in spawned workers** — use the
    `--moe-backend marlin` flag instead [src](https://github.com/flashinfer-ai/flashinfer/issues/2577).
18. **rc tags matter more than usual.** Prefill on the same model family swung 24,094 → 62,407 tok/s between
    TRT-LLM 1.2.1 and 1.3.0rc13 [src](https://github.com/elsung/blackwell-llm-toolkit/blob/main/bench/results.md).

**Memory / fragmentation**
19. GPU memory is frequently not released on a failed start; `ps aux | grep vllm | xargs kill -9`
    [src](https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash/discussions/28).
20. The **Server vs Workstation bandwidth difference (1,597 vs 1,792 GB/s) is 11 % of your decode throughput**
    and no aggregator distinguishes them (§2).

**Cloud quota realities**
21. Server Edition is **export-"Controlled"** [src](https://lenovopress.lenovo.com/lp2263.pdf) and primarily
    sold through OEM channels; retail availability is limited
    [src](https://www.thundercompute.com/blog/nvidia-rtx-pro-6000-pricing).
22. Vast.ai's RTX-PRO-6000-S listing showed **"No current offers"** on 2026-09-19
    [src](https://vast.ai/pricing/gpu/RTX-PRO-6000-S) — marketplace supply of the *Server* Edition
    specifically is thin; most marketplace inventory is WS/Max-Q.
23. Lambda and Scaleway do not list this SKU at all
    [src](https://computeprices.com/providers/lambda) [src](https://www.scaleway.com/en/pricing/gpu/).
24. Card prices rose **+87 % in 18 months** on GDDR7 supply; a purchase TCO model built on the $8,565 MSRP is
    off by ~2× [src](https://videocardz.com/newz/nvidia-raises-rtx-pro-6000-blackwell-price-to-16000-now-87-above-original-msrp).

---

## 12. Comparison hooks

### (a) Decode bandwidth

The Server Edition's **1,597 GB/s is 0.48× an H100 SXM (3.35 TB/s [src](https://www.nvidia.com/en-us/data-center/h100/))
and 0.21× a B200** (METHODOLOGY §8 pins B200 at **7.7 TB/s**, the Lenovo SKU sheet figure `research/gpus/b200.md`
plans with; NVIDIA's DGX B200 page implies 8.0 TB/s from "64 TB/s HBM3e bandwidth" across 8 GPUs
[src](https://www.nvidia.com/en-us/data-center/dgx-b200/) and states no per-GPU figure directly — a ~4 % spread
that gives 0.21× either way); the Workstation Edition's 1,792 GB/s is 0.53× / 0.23×.
Since decode is bandwidth-bound at every practical batch size on this card (§9a: the FP4 ridge point is
1,202 FLOP/byte), **per-GPU decode throughput on a weight-bound model is roughly half an H100's and a fifth of a
B200's, full stop.** What flips the comparison is *capacity*: 96 GB vs H100's 80 GB means a model that needs
TP=2 on H100 often needs TP=1 here, and TP=1 has no interconnect tax — which is exactly why the measured
single-GPU GLM-4.5-Air-AWQ number is **3,140 tok/s on the PRO 6000 versus 2,987 on an H100 SXM**
[src](https://www.cloudrift.ai/blog/benchmarking-rtx6000-vs-datacenter-gpus), an inversion of the raw-bandwidth
ratio. The rule: **this GPU wins on decode whenever avoiding a parallelism split is worth more than 2× the
bandwidth, and loses badly the moment the model forces multi-GPU** (the same benchmark family shows H100 3× and
H200 4× faster on 8-GPU GLM-4.6-FP8).

### (b) FP8 / FP4 prefill

Dense FP8 on the Server Edition is **960 TFLOPS, exactly 0.49× an H100 SXM's 1,979 dense**
(H100's published 3,958 is with sparsity [src](https://www.nvidia.com/en-us/data-center/h100/)) and
**0.21× a B200's 4,500 dense** (from HGX B200's 72 PFLOPS sparse per 8 GPUs
[src](https://www.nvidia.com/en-us/data-center/hgx/)). Dense FP4 is **1,920 TFLOPS versus B200's 9,000 dense —
0.21×** — and **versus H100's zero**, because Hopper has no FP4 tensor path at all. So against H100 the FP4
story is qualitatively different rather than quantitatively worse: an NVFP4 checkpoint that H100 must run as
FP8 (or dequantise) runs natively here at 2× the FP8 rate, which is why the measured 62,407 tok/s FP4 prefill
on a 3 B-active MoE [src](https://github.com/elsung/blackwell-llm-toolkit/blob/main/bench/results.md) is
competitive per dollar. Against B200 the comparison is brutal and one-sided: B200 has 4.7× the dense FP4
throughput, `tcgen05`-based FA4, working trtllm-gen cubins, working NVFP4 grouped GEMM, and NVFP4 KV cache —
**every one of which is unavailable on sm_120.** The honest summary is that this card has **21 % of a B200's FP4
compute and something closer to 10–15 % of its usable FP4 *software***, and the gap is kernel maturity, not
silicon.

### (c) Memory per dollar

This is where the card justifies itself — but **⚠️ RECOMPUTED 2026-09-19** against vendor rate cards, because
the previous version divided a $2.19 aggregator "market median" and a $0.93 marketplace quote into 96 GB and
compared them against ⚠️-flagged H100/B200 rates from a different source. METHODOLOGY §6 requires
same-source rows, so every rental figure below is a **same-provider** comparison drawn from
[`cloud-pricing.md`](../cross-cutting/cloud-pricing.md).

**Rental — GB-hours per dollar, same provider, on-demand:**

| Provider | RTX PRO 6000 (96 GB) | H100 SXM (80 GB) | B200 (180 GB) | vs H100 | vs B200 |
|---|---|---|---|---|---|
| CoreWeave | $2.500 → **38.4** | $6.155 → 13.0 | $8.600 → 20.9 | **2.95×** | 1.83× |
| Hyperstack | $1.85 → **51.9** | $3.20 → 25.0 | $6.00 → 30.0 | **2.08×** | 1.73× |
| RunPod Secure | $2.09 → **45.9** | $3.49 → 22.9 | $6.79 → 26.5 | **2.00×** | 1.73× |
| AWS on-demand | $4.143 → **23.2** | $6.880 → 11.6 | $14.242 → 12.6 | **1.99×** | 1.83× |

So the real advantage is **1.7–3.0× VRAM per dollar** (≈2.0× vs H100 at three of four providers, ≈1.8× vs
B200 at all four), not the **2.4–3.2×** this section used to claim from mismatched sources. The claim is
smaller, tighter, and now survives a same-provider test — which the old one did not.

**Purchase:** 96 GB at the $16,000 NVIDIA marketplace list = **6.0 GB per $1,000**; at cloud-pricing.md's
$13,250 list figure it is **7.25 GB/$1k** (see §8b for why the two coexist) — versus roughly 3.2 GB/$1k for an
80 GB H100 at a ~$25,000 street price and 4.5 GB/$1k for a **180 GB** B200 (as deployed, METHODOLOGY §8) at
~$40,000 (**both street prices ⚠️ TO BE VERIFIED** — cloud-pricing.md carries no H100/B200 street row).

**What the capacity actually buys, re-derived from §9d and §9g.** "Cheapest way to put a model into GPU
memory" is true and is not the same claim as "cheapest way to *serve* it", and the concurrency table is what
separates them:

- **MoE and hybrid-attention models: the thesis holds.** gpt-oss-120b MXFP4 fits in 65.17 GB and, because
  18 of its 36 layers are sliding-window-capped at `W = 128`, leaves room for **112 concurrent sequences at
  8 K** on one card. Qwen3.8-27B at FP8/NVFP4/INT4 gives **122–149**. Marlin-2B gives **640**. These are real
  serving densities on a single card with no interconnect tax.
- **Dense 70 B-class models: the thesis is much weaker than 96 GB suggests.** A 70 B FP8 checkpoint is
  70.07 GB, leaving a 12.33 GB KV budget — **9 concurrent requests at 8 K, 2 at 32 K, and not one at 128 K**
  (§9d). The card *holds* the model; it barely *serves* it. Lenovo's own "fewer than 70B parameters"
  guidance, re-verified verbatim on 2026-09-19, is the honest reading of this row.
- **400 B-class MoE across four cards** still works (Qwen3.5-397B NVFP4 at 55.83 GB/GPU, measured
  46–50 tok/s with TP2+PP2 + Marlin), but only with PP or DP — TP over PCIe collapses to 6–7 tok/s (§4).

The investment thesis, restated to match the arithmetic: **the RTX PRO 6000 Blackwell is the cheapest way to
put a quantised MoE or hybrid-attention model of up to ~120 B parameters into GPU memory at real concurrency,
and a poor way to serve a dense 70 B model at long context**, provided you accept half an H100's decode
bandwidth, no NVLink, and a kernel stack that in September 2026 still routes its best NVFP4 path through a
W4A16 dequant fallback.

---

## Sources

**NVIDIA primary**
- https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition.md
- https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/
- https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000/
- https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000-family/
- https://www.nvidia.com/content/dam/en-zz/Solutions/design-visualization/quadro-product-literature/NVIDIA-RTX-Blackwell-PRO-GPU-Architecture-v1.0.pdf
- https://blogs.nvidia.com/blog/rtx-pro-6000-blackwell-server-edition
- https://www.nvidia.com/en-us/data-center/h100/
- https://www.nvidia.com/en-us/data-center/hgx/
- https://docs.nvidia.com/cutlass/4.3.2/CHANGELOG.html
- https://docs.nvidia.com/cuda/cuda-compiler-driver-nvcc/index.html
- https://forums.developer.nvidia.com/t/nccl-p2p-hang-on-dual-rtx-pro-6000-blackwell-workstation-edition-wrx90e-sage-se/365048
- https://developer.nvidia.com/displaymodeselector

**OEM primary**
- https://lenovopress.lenovo.com/lp2263.pdf
- https://lenovopress.lenovo.com/lp2263-thinksystem-nvidia-rtx-pro-6000-blackwell-server-edition-pcie-gen5-gpu

**Kernel / engine repositories and issue trackers (primary)**
- https://github.com/Dao-AILab/flash-attention/blob/main/README.md
- https://github.com/Dao-AILab/flash-attention/issues/1987
- https://github.com/Dao-AILab/flash-attention/pull/2222
- https://github.com/Dao-AILab/flash-attention/pull/2268
- https://github.com/flashinfer-ai/flashinfer/issues/2577
- https://github.com/flashinfer-ai/flashinfer/issues/2555
- https://github.com/flashinfer-ai/flashinfer/pull/2559
- https://github.com/flashinfer-ai/flashinfer/pull/2560
- https://github.com/flashinfer-ai/flashinfer/pull/2561
- https://github.com/NVIDIA/TensorRT-LLM/issues/11799
- https://github.com/NVIDIA/TensorRT-LLM/issues/11386
- https://github.com/NVIDIA/TensorRT-LLM/issues/10241
- https://github.com/NVIDIA/cutlass/issues/3096
- https://github.com/NVIDIA/cutlass/issues/2820
- https://github.com/vllm-project/vllm/issues/31085
- https://github.com/vllm-project/vllm/issues/38971
- https://github.com/vllm-project/vllm/issues/27471
- https://github.com/vllm-project/vllm/issues/27542
- https://github.com/vllm-project/vllm/pull/33417
- https://github.com/vllm-project/vllm/pull/57456
- https://github.com/sgl-project/sglang/issues/15342
- https://github.com/sgl-project/sglang/issues/39302
- https://github.com/sgl-project/sglang/issues/39807
- https://github.com/sgl-project/sglang/issues (SM120 issue/PR listing, 2026-09-19: #40175, #40173, #40116, #39978, #39967, #39961, #39914, #39872, #39868, #39492, #39288, #39235, #39215, #39065, #39063, #38970, #38969, #38792)
- https://github.com/triton-lang/triton/pull/8498
- https://github.com/ggml-org/llama.cpp/pull/26704
- https://docs.vllm.ai/en/latest/getting_started/installation/gpu.html
- https://discuss.vllm.ai/t/sm120-rtx-pro-6000-nvfp4-moe-performance-report-qwen3-5-397b/2536
- https://discuss.vllm.ai/t/support-for-rtx-6000-blackwell-96gb-card/1707
- https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash/discussions/28
- https://forums.developer.nvidia.com/t/your-gpu-does-not-have-native-support-for-fp4-computation-but-fp4-quantization-is-being-used/355494
- https://forums.developer.nvidia.com/t/psa-state-of-fp4-nvfp4-support-for-dgx-spark-in-vllm/353069

**Community benchmark repositories (first-hand, reproducible)**
- https://github.com/elsung/blackwell-llm-toolkit
- https://github.com/elsung/blackwell-llm-toolkit/blob/main/bench/results.md
- https://github.com/VincentKaufmann/fp4-cuda-kernel
- https://github.com/lna-lab/blackwell-geforce-nvfp4-gemm

**Benchmarks and analysis (secondary)**
- https://nebius.com/blog/posts/mlperf-inference-v6-0-results
- https://nebius.com/blog/posts/mlperf-inference-v6-1-results
- https://newsletter.semianalysis.com/p/inferencemax-open-source-inference
- https://inferencex.semianalysis.com/blog/inferencemax-open-source-inference-benchmarking
- https://www.lmsys.org/blog/2025-10-14-sa-inference-max/
- https://www.cloudrift.ai/blog/benchmarking-rtx6000-vs-datacenter-gpus
- https://www.cloudrift.ai/blog/benchmarking-rtx-gpus-for-llm-inference
- https://aurorainfra.ai/blog/rtx-pro-6000-blackwell-max-q-vs-600w-benchmarks
- https://blog.us.fixstars.com/what-kind-of-gpu-is-the-nvidia-rtx-pro-6000-blackwell-max-q/
- https://www.hardware-corner.net/rtx-pro-6000-blackwell-flashattention-4/
- https://elevata.io/en/nvfp4-inference-blackwell-sm120-gpus-what-worked
- https://research.colfax-intl.com/cutlass-tutorial-hardware-supported-block-scaling-with-nvidia-blackwell-gpus/
- https://acecloud.ai/blog/rtx-pro-6000-llm-inference/
- https://en.wikipedia.org/wiki/Blackwell_(microarchitecture)

**Pricing**
- https://computeprices.com/gpus/rtx-pro-6000
- https://computeprices.com/providers/lambda
- https://computeprices.com/providers/nebius
- https://getdeploying.com/gpus/nvidia-rtx-pro-6000
- https://www.thundercompute.com/blog/nvidia-rtx-pro-6000-pricing
- https://www.runpod.io/gpu-models/rtx-pro-6000
- https://vast.ai/pricing/gpu/RTX-PRO-6000-S
- https://vast.ai/pricing/gpu/RTX-PRO-6000-WS
- https://vast.ai/pricing/gpu/RTX-PRO-6000-MAX-Q
- https://www.scaleway.com/en/pricing/gpu/
- https://verda.com/products (formerly https://datacrunch.io/products, HTTP 301)
- https://www.spheron.network/gpu-rental/rtx-pro-6000/
- https://www.spheron.network/blog/rent-nvidia-rtx-pro-6000/
- https://www.tomshardware.com/pc-components/gpus/nvidia-raises-rtx-pro-6000-blackwell-gpu-pricing-to-usd13-250-55-percent-increase-over-msrp-in-a-years-time
- https://videocardz.com/newz/nvidia-raises-rtx-pro-6000-blackwell-price-to-16000-now-87-above-original-msrp
- https://videocardprices.com/card/nvidia-rtx-pro-6000-blackwell/

---

## Verification log (2026-09-19)

Adversarial re-check of the 20+ most consequential claims. Every source below was opened independently —
the document's own citation was never taken on trust. Derivations were re-run with `python3` against
METHODOLOGY §1–§6 and, where a model geometry was involved, against the upstream `config.json`.

### Silicon and datasheet claims

| # | Claim | Verdict | Source |
|---|---|---|---|
| 1 | Server Edition: 96 GB GDDR7, 512-bit, **1,597 GB/s**, PCIe 5.0 ×16, up to 600 W configurable, air dual-slot FHFL / liquid single-slot FHXL | **CONFIRMED** | https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/ · https://lenovopress.lenovo.com/lp2263.pdf |
| 2 | Workstation / Max-Q: 96 GB GDDR7 ECC, 512-bit, 28 Gbps, **1,792 GB/s** | **CONFIRMED** (Appendix A Table 4) | NVIDIA RTX Blackwell PRO GPU Architecture whitepaper v1.0 |
| 3 | Server Edition published perf: FP32 120 TFLOPS, TF32 234, FP16/BF16 1 PFLOPS, FP8 2 PFLOPS, FP4 4 PFLOPS | **CONFIRMED** verbatim on both NVIDIA and Lenovo | as #1 |
| 4 | Workstation dense/sparse per dtype: FP32 126.0 · TF32 251.9/503.8 · FP16+BF16 503.8/1007.6 · FP8 1007.6/2015.2 · FP4 2015.2/4030.4 · INT8 1007.6/2015.2 | **CONFIRMED** verbatim | whitepaper Appendix A Table 4 |
| 5 | Max-Q dense/sparse: FP32 109.7 · TF32 219.5/438.9 · FP16/BF16 438.9/877.9 · FP8 877.9/1755.7 · FP4 1755.7/3511.4 · INT8 877.9/1755.7 | **CONFIRMED** verbatim | whitepaper Appendix A Table 4 |
| 6 | Footnote 2 = "Effective TOPS / TFLOPS using the Sparsity Feature" (so "4000 AI TOPS" is sparse FP4) | **CONFIRMED** verbatim, and independently by the WS product page's "Theoretical FP4 TOPS using sparsity" | whitepaper · https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000/ |
| 7 | Die-level: 188 SMs, 24,064 CUDA cores, 752 TC (5th gen), 188 RT (4th gen), 12 GPC / 94 TPC, 750 mm², 92.2 B transistors, **L2 131,072 KB = 128 MB**, register file 48,128 KB, L1/shared 24,064 KB (128 KB/SM), 192 ROPs, TSMC 4N, PCIe Gen 5, TGP 600 W / 300 W | **CONFIRMED** every field | whitepaper Appendix A Table 4 |
| 8 | Boost clocks 2,617 MHz (WS) / 2,280 MHz (Max-Q) | **CONFIRMED** | whitepaper Appendix A |
| 9 | No FP6 rate and no INT4 row published in Appendix A; "RTX Blackwell adds new support for FP4 **and FP6** Tensor Core operations, and the new Second-Generation FP8 Transformer Engine" | **CONFIRMED** verbatim — the absence of both rows is real, so the §3a `⚠️ TO BE VERIFIED` markers are correctly placed | whitepaper p. 25 |
| 10 | "No FP32-accumulate penalty" | **CONFIRMED** — Appendix A lists FP16-acc and FP32-acc rows at identical values for both FP16 and FP8 | whitepaper Appendix A |
| 11 | Lenovo: NVLink **No**; MIG up to 4 @ 24 GB; vGPU vPC/vApps/RTX vWS; 4× DP 2.1b disabled by default; "Controlled" export status; 600 W cappable to 450 W; part 4X67B09287 / 900-2G153; "up to 5x … LLM inference with support for FP4"; "fewer than 70B parameters" scoping | **CONFIRMED** every field, verbatim | https://lenovopress.lenovo.com/lp2263.pdf |
| 12 | MIG on **Workstation and Max-Q** was marked "not advertised ⚠️" | **CORRECTED** not advertised → **supported, 1×96 / 2×48 / 4×24 GB**; whitepaper Table 3 is titled "RTX PRO 6000 Blackwell Workstation Edition & Max-Q Edition" | whitepaper Table 3 |
| 13 | Server Edition TDP "configurable **400**–600 W" | **CORRECTED → UNVERIFIABLE** — NVIDIA says "Up to 600W (configurable)", Lenovo "600 W (can also be power capped to 450 W)". No source states a 400 W floor; now marked ⚠️ | as #1 |
| 14 | Workstation FP32 = 126.0 TFLOPS | **CONFIRMED with a caveat now recorded in §3a** — whitepaper says 126.0, NVIDIA's WS product page prints **125**. 126.0 is what the rest of the table is internally consistent with (24,064 × 2 × 2.617 GHz) | whitepaper · WS product page |
| 15 | `cudaDevAttrMaxSharedMemoryPerBlockOptin` = **101,376 B (99 KB)**, `sharedMemPerMultiprocessor` = 102,400; requests of 131072/163840/202752/206848 return `invalid argument` | **CONFIRMED** verbatim, including the minimal-CUDA-program verification | https://github.com/sgl-project/sglang/issues/39302 |

### Kernel, format and engine support claims

| # | Claim | Verdict | Source |
|---|---|---|---|
| 16 | FA2 = "Ampere, Ada, or Hopper GPUs"; FA3 = "H100 / H800 GPU, CUDA >= 12.3"; FA4 = "optimized for Hopper and Blackwell GPUs (e.g. H100, B200)" | **CONFIRMED** verbatim — FA3/FA4 genuinely exclude sm_120 | https://github.com/Dao-AILab/flash-attention/blob/main/README.md |
| 17 | "FA2 agnostic to arch. FA3 hopper … FA4 B200/B300"; "fa2 don't support fp8 qkv and performance is not good for blackwell"; the 576-head-dim MLA `ValueError` backend-rejection block | **CONFIRMED** verbatim | https://github.com/Dao-AILab/flash-attention/issues/1987 |
| 18 | FA PR #2222 draft **closed unmerged 2026-02-21**; FA PR #2268 **closed unmerged 2026-03-12** | **CONFIRMED** both states and both dates | https://github.com/Dao-AILab/flash-attention/pull/2222 · /pull/2268 |
| 19 | FlashInfer PR #2559 merged; #2560 **merged 2026-03-13** (CUTLASS FMHA guarded off SM12x); #2561 **not merged** | **CONFIRMED** — #2559 merged 2026-02-19, #2560 merged 2026-03-13, #2561 closed unmerged 2026-03-14 | https://github.com/flashinfer-ai/flashinfer/pull/2559 · /2560 · /2561 |
| 20 | trtllm-gen FMHA: "trtllm-gen does not have support for SM120/121 yet. And SM100 kernels cannot run on SM120 directly. You may use XQA or FMHA_v2 as fallback."; "TRTLLM-Gen is designed mainly considering support of sm100/103. I believe we have no plan to SM120/121 so far."; issue **closed 2026-07-16**; `fmhaRunner.cuh` asserts `mSM == kSM_100 \|\| mSM == kSM_103`; `QE4m3KvE2m1` exists for SM100a/SM103a only; `attentionOp.cpp:2793` / `fmhaDispatcher.cpp:70` contradiction | **CONFIRMED** — every quote verbatim, both from NVIDIA collaborator `pengbowang-nv`, closure date exact | https://github.com/NVIDIA/TensorRT-LLM/issues/11799 |
| 21 | vLLM MXFP4 falls back to Marlin: `get_mxfp4_backend()` matches only `is_device_capability_family(100)`; Triton branch gated `(9,0) <= cc < (11,0)`; the "Your GPU does not have native support for FP4 computation…" warning; `nvfp4_scaled_mm_sm120_kernels.cu`, `nvfp4_blockwise_moe_kernel.cu` (`ENABLE_NVFP4_SM120`), commits `e50209864` / `c0dfc8948`; `cutlass_scaled_mm_supports_fp4(120) → True`; open since 2025-12-20, bumped 2026-05-18; Marlin 185 vs Triton 140 tok/s on gpt-oss-120b Max-Q 300 W | **CONFIRMED** every element, verbatim | https://github.com/vllm-project/vllm/issues/31085 |
| 22 | Triton PR #8498 "Triton MXFP4 support for SM120", merged 2025-10-24 | **CORRECTED** — merge date 2025-10-24 is right, but the PR is titled "**[NVIDIA] Enable TMA gather4 on sm_120 and sm_121**". It is the prerequisite vLLM's `mxfp4.py` comment points at, not an MXFP4-support PR | https://github.com/triton-lang/triton/pull/8498 |
| 23 | Marlin vs FLASHINFER_CUTLASS on Nemotron 3 Super NVFP4 120 B: **92 vs 74 tok/s**, KV **16.79 vs 18.37 GiB**, **732,160 vs 798,720** tokens, **14.27× vs 15.62×** @262K, at `--gpu-memory-utilization 0.95 --max-num-seqs 512`; `--moe-backend` is the answer (2026-04-05) | **CONFIRMED** every number | https://github.com/vllm-project/vllm/issues/38971 |
| 24 | TP=4 → 6–7 tok/s vs TP=2+PP=2 → 46–49 / ~148 / ~212 tok/s; the `.float()` scale bug in the repro; `VLLM_TEST_FORCE_FP8_MARLIN=1` causes CUDA driver errors in spawned workers; vLLM PR #33417 | **CONFIRMED** verbatim; PR #33417 merged 2026-01-31 | https://github.com/flashinfer-ai/flashinfer/issues/2577 |
| 25 | NVFP4 MoE fix chain produced correct output "at **14.6 tok/s** vs Marlin's 46–49" | **CORRECTED 14.6 → 39.0 tok/s.** 14.6 is the `compute_120a` / CUDA 12.8 result; `compute_120f` / CUDA 13.0 reaches **39.0 tok/s** single-user and 18.2 tok/s/user at 4 concurrent. Issue closed 2026-04-14. The failed-attempt list (GDC barriers, FP32 amax, Pingpong segfault, `compute_120a` segfault) is **CONFIRMED**, plus an unlisted CUTLASS 4.4.1 attempt that also failed | https://github.com/NVIDIA/cutlass/issues/3096 |
| 26 | "NVFP4 MoE grouped GEMM is broken; Marlin is the only correct path" | **CORRECTED (material omission).** On RTX PRO 6000 with vLLM main (2026-06-11), `--moe-backend flashinfer_b12x` runs FlashInfer's SM12x CuTe-DSL fused MoE with coherent output; it is excluded only from *auto*-selection. Auto-selection reaches Marlin because ModelOpt NVFP4 checkpoints resolve as **W4A16** while `FlashInferExperts._supports_quant_scheme` lists only the W4A4 pair — a scheme-coverage gap, not an architecture one | https://github.com/vllm-project/vllm/issues/31085 |
| 27 | CUTLASS SM120 support since **3.9.0 (2025-04-24)**: blockscaled dense + sparse GEMM, NVFP4 grouped GEMM, MXFP8/MXFP6 mixed input; SM121 and SM120 mixed-input blockscaled grouped GEMM since **4.2.0 (2025-09-15)** | **CONFIRMED** including both release dates | https://docs.nvidia.com/cutlass/4.3.2/CHANGELOG.html |
| 28 | MXFP4 conflict: "MXFP4 weights (E2M1 + E8M0 scales) can't be used directly on SM121 tensor cores. SM121 uses NVFP4 with UE4M3 scale factors and a specific interleaved layout (`SfKMajorAtom`)" + requantization requirement | **CONFIRMED** verbatim — the conflict with the CUTLASS changelog is real, so the ⚠️ marker stands | https://github.com/VincentKaufmann/fp4-cuda-kernel |
| 29 | Standalone NVFP4 kernel: "85–129 TFLOPS, 1.4–2.4× vs BF16 `F.linear`" | **CORRECTED → 62–129 TFLOPS, 1.0–2.4×** (2.4× at M=256, **1.0× parity at M=512**, 1.4–1.7× at M=2048, 0.7× at M=4096). Also clarified that the part is a **DGX Spark GB10 at 273 GB/s**, not an RTX PRO 6000 | https://github.com/VincentKaufmann/fp4-cuda-kernel |
| 30 | SGLang env for the DSA work: sglang 0.5.19, FlashInfer 0.6.18, torch 2.13.0+cu130, tilelang 0.1.12, nvcc 13.4; TileLang needs 202–206 KB (`Failed to set the allowed dynamic shared memory size to 206848`), `block_I=16` → ~84 KB then `LayoutInference` conflict; GLM-5.x NoPE MLA (`qk_rope_head_dim=0`) unrunnable on SM120 | **CONFIRMED** every element (nvcc is 13.4.59) | https://github.com/sgl-project/sglang/issues/39302 |
| 31 | SGLang: "FMHAv2 has no FP8 (e4m3) prefill kernel on SM120/SM121"; glibc ≥ 2.41 C23 `rsqrt` JIT breakage; Qwen3.6-35B-A3B-NVFP4 246 tok/s MTP-off, 286–297 MTP-on, dp=4 1,408 @8 and 3,190 @16 concurrent | **CONFIRMED** every number | https://github.com/sgl-project/sglang/issues/39807 |
| 32 | vLLM PR #57456 (sm_120 tuned configs) and llama.cpp PR #26704 (SM120 CUTLASS MoE prefill) are open | **CONFIRMED** — both still OPEN on 2026-09-19 | https://github.com/vllm-project/vllm/pull/57456 · https://github.com/ggml-org/llama.cpp/pull/26704 |

### Recomputed derivations (METHODOLOGY §1–§6)

| # | Derivation | Verdict |
|---|---|---|
| 33 | §3c Server Edition dense: implied boost `120e12/(24064×2) = 2.4934 GHz`; TF32 240.0 · BF16 480.0 · FP8 960.0 · FP4 1,920.0 TFLOPS | **CONFIRMED** — reproduces to 4 significant figures; the "1/2/4 PFLOPS" NVIDIA prints are indeed the sparse doubles |
| 34 | §3d efficiency: 376.44/503.8 = 74.7 %; 268.14/438.9 = 61.1 %; FP32 72.34/126.0 = 57.4 %; BF16 and FP32 both scale 1.40× between parts | **CONFIRMED** |
| 35 | §9a ridge points 150.3 / 300.6 / 601.1 / 1,202.3 FLOP/byte (SE) and 1,124.6 (WS FP4) | **CONFIRMED** |
| 36 | §9b decode bounds, all seven rows at MBU 0.55 / 0.70 | **CONFIRMED** to the printed precision |
| 37 | §9b MBU back-out: `1597e9 × 0.55 / 1.7e9 ≈ 517`, achieved MBU on active bytes ≈ 0.26 | **CONFIRMED** |
| 38 | §9c KV bytes/token: Llama-3.3-70B 320.0 KiB BF16 / 160.0 FP8; DeepSeek MLA 61×(512+64) = 68.6 KiB | **CONFIRMED** |
| 39 | §9c gpt-oss-120b "72.0 KiB/token" | **CORRECTED → 36.0 KiB/token BF16 (18.0 FP8) + a fixed 4.5 MiB/seq (2.25 FP8)**. `config.json` has `sliding_window: 128` with **18 `sliding_attention` + 18 `full_attention`** layers; METHODOLOGY §2 caps sliding layers at W tokens. https://huggingface.co/openai/gpt-oss-120b/raw/main/config.json |
| 40 | §9d max concurrency, 70 B FP8 row: "74 / 18 / 4.6 seqs" | **CORRECTED → 9 / 2.3 / 0.6.** `12.4e9 / (8192 × 163,840) = 9.24`. The old row used 20 KiB/token = 160 ÷ 8 — **exactly 8× optimistic** |
| 41 | §9d gpt-oss-120b row: "567 / 142 / 35 seqs" | **CORRECTED → 140 / 35 / 8.8** with the sliding-window state applied (71 / 18 / 4.4 on the naive 36 KiB/token basis). Old row used 4.5 KiB/token = 36 ÷ 8 — the same 8× error |
| 42 | §9d "30 B-A3B AWQ (BF16 KV, ~7.5 KiB/tok) → 1,040 / 260 / 65" | **CORRECTED → 81 / 20 / 5** at the real Qwen3-30B-A3B geometry (48 L, 4 KV heads, 128 head_dim = **96.0 KiB/token** BF16). The arithmetic was self-consistent; its 7.5 KiB/token input matches no 30B-A3B config. https://huggingface.co/Qwen/Qwen3-30B-A3B/raw/main/config.json |
| 43 | §9e prefill, all four rows at MFU 0.30 / 0.40 | **CONFIRMED** exactly |
| 44 | §8c colo TCO: capex $163,000; 26,280 h; 8.10 kW; power $21,287; total $184,287; **$0.877/GPU-h**; 80 % → $1.096; 60 % → $1.461; 450 W → 6.48 kW, $17,029, **$0.856** | **CONFIRMED** every figure (inputs remain assumptions, as the section already states) |
| 45 | §12a/§12b ratios: 1597/3350 = 0.48; 1597/8000 = 0.20; 960/1979 = 0.49; 960/4500 = 0.21; 1920/9000 = 0.21; 9000/1920 = 4.7 | **CONFIRMED** |
| 46 | §12c memory per dollar: 6.0 and 6.4 GB/$1k; H100 3.2; B200 4.5; 43.8 / 80.0 / 103.2 GB-h/$; H100 32.0; B200 32.7 | **CONFIRMED** arithmetic (the street prices carry their own ⚠️, correctly) |
| 47 | §8d perf/W "+48 %" | **CORRECTED in context** — 873.7 vs 589.9 GFLOP/J are **node** figures (8× Max-Q vs 4× 600 W), not a per-card A/B. Per-card from §3d: 894 vs 627 GFLOP/J = **+43 %**. Conclusion unchanged |

### Comparison-target and market claims

| # | Claim | Verdict | Source |
|---|---|---|---|
| 48 | H100 SXM: 3.35 TB/s, NVLink 900 GB/s, PCIe Gen5 128 GB/s, FP8 3,958 TFLOPS "*with sparsity" → **1,979 dense** | **CONFIRMED**, including that the doc correctly de-sparsifies | https://www.nvidia.com/en-us/data-center/h100/ |
| 49 | B200: FP4 "144 PFLOPS \| 72 PFLOPS" per 8-GPU HGX → **9,000 TFLOPS dense per GPU**; NVLink 1.8 TB/s per GPU; 1.4 TB total HGX memory | **CONFIRMED** | https://www.nvidia.com/en-us/data-center/hgx/ |
| 50 | B200 per-GPU bandwidth "8 TB/s ⚠️" | **CONFIRMED and de-flagged** — DGX B200 publishes "64 TB/s HBM3e bandwidth" across 8 GPUs → 8.0 TB/s. Noted that `research/gpus/b200.md` plans with 7.7 TB/s from the Lenovo SKU sheet (~4 % spread) | https://www.nvidia.com/en-us/data-center/dgx-b200/ |
| 51 | CloudRift: GLM-4.5-Air 1-GPU **3,140 vs H100 2,987**; 8-GPU **15,713**; $/M-token $0.18/$0.25/$0.17/$0.29, $0.30/$0.31/$0.26, $1.03/$1.01/$0.78, $1.72/$0.76/$0.72; 8 K context, `--kv-cache-dtype fp8`, 1000 in/1000 out, concurrency 256–512 | **CONFIRMED** every number and the test configuration | https://www.cloudrift.ai/blog/benchmarking-rtx6000-vs-datacenter-gpus |
| 52 | Qwen3-Coder-480B row labelled "8 GPU" | **CORRECTED → UNVERIFIABLE** — the source table labels that model **4×** for all three GPU types; and the "+31 % / +100 %" throughput deltas were not recoverable on re-read. Both now ⚠️ | as #51 |
| 53 | Aurora Infra A/B: BF16 GEMM 376.44 / 268.14 TFLOPS, FP32 72.34 / 51.55, 873.7 / 589.9 GFLOP/J, "26.5–27.8 % less energy per output token", Qwen3-4B TP1 C8 955.7 vs 958.9, 32 K TP4 1,404.4 vs 1,523.3, Wan2.2 4×600 W thermal health-gate failure | **CONFIRMED** every number; the thermal event is "GPU 3 reached 92 °C and recorded software thermal slowdown" | https://aurorainfra.ai/blog/rtx-pro-6000-blackwell-max-q-vs-600w-benchmarks |
| 54 | "DeepSeek-V4 dense TP2 C64: 2,262.9 (Max-Q) / 2,549.5 (600 W)" | **UNVERIFIABLE** — not found in the source, which reports 8× Max-Q **6,410.2** vs 4× 600 W **4,817.0** tok/s for that workload. Now ⚠️ | as #53 |
| 55 | elsung toolkit: Nemotron-3-Nano-Omni-30B-A3B **269.81 tok/s / 62,407 prefill @5,426 tok / TTFT 24 ms** on TRT-LLM 1.3.0rc13; non-Omni **249.15 / 24,094 / TTFT 29 ms** on 1.2.1; Qwen3.6-27B MTP **53.62 → 110.45** (2.06×) | **CONFIRMED** every number; all runs are single-card **Workstation Edition**, no TP | https://github.com/elsung/blackwell-llm-toolkit/blob/main/bench/results.md |
| 56 | "88 GB VRAM resident at 8 K context" | **UNVERIFIABLE** — re-read confirmed the throughput rows but surfaced no VRAM column. Now ⚠️ | as #55 |
| 57 | DeepSeek-V4-Flash on SM120: DeepGEMM `DG_HOST_UNREACHABLE` / arch_major=12; **~1.97 M KV tokens across 8 cards**; CUDA graphs **~5 → 30–35 tok/s**; EAGLE **45.6 tok/s**; 64 K+ **3–5 tok/s**; `ps aux \| grep vllm \| xargs kill -9` | **CONFIRMED** | https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash/discussions/28 |
| 58 | That KV figure was at "96 % utilisation" | **CORRECTED → 85 %** | as #57 |
| 59 | `lmsysorg/sglang:deepseek-v4-blackwell` is the recommended DeepSeek-V4 SM120 container | **CORRECTED → UNVERIFIABLE** — the source recommends the vLLM image `ununnilium/vllm-ds4-sm120:20260618` (branch `ds4-sm120-preview`) and names no SGLang image. The "37–49 tok/s decode" row is likewise unsupported (source says 30–35). Both now ⚠️ | as #57 |
| 60 | Pricing: launch MSRP **$8,565** (2025-03-18); NVIDIA marketplace **$16,000**; retail $15,999–$18,850; GDDR7 shortage + clamshell as the cause; AWS G7e $3.36, GCP $4.50, Oracle $4.50, Azure $5.50, Modal $3.03, RunPod $2.09 | **CONFIRMED** | https://www.thundercompute.com/blog/nvidia-rtx-pro-6000-pricing |
| 61 | Server-Edition-specific "$14,999 Newegg" and "$17,016 across 19 listings" | **UNVERIFIABLE** — neither figure, nor any Server-Edition price or listing count, appears on the cited page. Nearest is a used/refurb band of $14,980–$18,850. Both now ⚠️ | as #60 |
| 62 | computeprices rows: GPU Outlet $0.590, Verda $0.930, Nebius $0.950, PowerGPU $1.04, AtmosCompute $1.10, Lium $1.19, 1Legion $1.19 (24-month), CoreWeave $1.20, Spheron $1.20–$2.27, Hyperstack $1.30, RunPod $1.64–$2.09, GCP $1.74–$4.50 | **CONFIRMED** | https://computeprices.com/gpus/rtx-pro-6000 |
| 63 | Vast.ai RTX-PRO-6000-S at "$0.93 listed / $0.16 spot", and "No current offers" | "No current offers" **CONFIRMED**; the $0.93 and $0.16 figures **UNVERIFIABLE** — the page shows no price at all (third-party quotes are $1.48–$2.00). Now ⚠️. The page also lists the S variant at **1.79 TB/s**, which independently proves §2's point that aggregators publish Workstation bandwidth for Server silicon | https://vast.ai/pricing/gpu/RTX-PRO-6000-S |
| 64 | MLPerf v6.0: Nebius submitted "a cost-efficient node with 8 RTX PRO 6000 Blackwell Server Edition GPUs" alongside GB300 NVL72, HGX B300, HGX B200 and a **VR200 NVL72 preview** | **CORRECTED** — the post describes HGX B200, HGX B300 and GB300 NVL72 (1/8/72 GPU) only. **No RTX PRO 6000 node and no VR200 NVL72 preview** appear in it; RTX PRO 6000 is a "See also" link. Treat the submission claim as unsupported pending the MLCommons results table | https://nebius.com/blog/posts/mlperf-inference-v6-0-results |

**Totals: 64 claims checked — 45 CONFIRMED, 12 CORRECTED, 7 UNVERIFIABLE.**

The two corrections that change a sizing decision are **#40–#42** (the §9d concurrency table was 8× optimistic
on two rows and used an impossible KV geometry on the third) and **#39** (gpt-oss-120b is a sliding-window
hybrid, so its per-token KV cost is half what was printed). The correction that changes a *platform* judgement
is **#25/#26**: native NVFP4 MoE on sm_120 reaches 39 tok/s with `compute_120f`, and vLLM main ships an opt-in
native backend — the gap to Marlin is ~20 %, not the 3× this document previously implied.

---

## Sweep log (2026-09-19)

Systemic sweep against the amended `research/METHODOLOGY.md` (§1 bytes-per-param and mixed-precision rule,
units convention, §2 state-slot multiplier `S`, §3 consistency rule, §6 standard scenarios, §8 pinned inputs).
Every recomputed table was re-run with `python3`. Nothing the earlier fact-checker corrected was reverted;
where this pass moved one of its numbers again, the row carries the whole chain.

### Units and arithmetic (checklist 1, 4, 5)

| Section | old → new | reason | source |
|---|---|---|---|
| §2 usable capacity | `96 GB × 0.90 = 86.4 GB` → `cap = 96e9 B; usable_090 = 86.40e9 B = 80.47 GiB` (and 0.95 → 91.20e9 B = 84.94 GiB) | METHODOLOGY §1: do memory arithmetic in bytes, report GiB unless the column says GB. The card is 96 GB = 89.41 GiB, not 96 GiB | METHODOLOGY §1 |
| §2 node totals | `768 GB raw / 691 GB usable` → `768 GB (715.3 GiB) / 691.2 GB (643.7 GiB)`; "8×B200 node (1.4 TB)" → "8 × 180 GB **as deployed** = 1,440 GB" | units; B200 capacity pinned at 180 GB | METHODOLOGY §1, §8 |
| §9b "Weights" column | asserted without arithmetic → every row derived in bytes from the model's own geometry, printed as GB with GiB alongside | fact-checker flagged the column as unsupported | METHODOLOGY §1 |
| §9b gpt-oss-120b | `61 GB` → **65.17 GB (60.69 GiB)** | the old value was the **GiB** figure under a GB heading. Derived: `114.662e9 × 0.53125 (MXFP4) + 2.127e9 × 2.0 (BF16 attn/router/embeddings)` | [gpt-oss-120b config.json](https://huggingface.co/openai/gpt-oss-120b/raw/main/config.json), re-fetched; METHODOLOGY §1 MXFP4 = 0.53125 |
| §9b Qwen3-235B-A22B NVFP4 | `63 GB/GPU` → **67.01 GB/GPU (62.41 GiB)** | same GiB/GB slip, compounded by using ~0.53 B/param where NVFP4 is **0.5625** (+12.5 %) | METHODOLOGY §1 |
| §9b Qwen3.5-397B-A17B NVFP4 | `52.5 GB/GPU` → **55.83 GB/GPU (51.99 GiB)**, marked ⚠️ floor | same slip + NVFP4 rate; marked a floor because the per-tensor-group split is unpublished and METHODOLOGY §1 forbids a flat rate on a mixed checkpoint | METHODOLOGY §1 |
| §9b 30 B-A3B AWQ | `17 GB` (unchanged) → derivation added: `(30.53e9 − 0.622e9) × 0.53 + 0.622e9 × 2 = 17.10 GB` | embeddings + untied `lm_head` stay BF16 | [Qwen3-30B-A3B config.json](https://huggingface.co/Qwen/Qwen3-30B-A3B/raw/main/config.json) (`tie_word_embeddings: false`) |
| §9b tok/s columns | 14.4/18.3, 13.9/17.7, 16.7/21.3, 51.7/65.8 → **13.5/17.2, 13.1/16.7, 15.7/20.0, 51.4/65.4** | follows from the corrected weight bytes; `BW = 1,597e9` stated explicitly | METHODOLOGY §4 |
| §9b reality check | "against a 16.7–21.3 tok/s bound" → "against a **15.7–20.0** tok/s bound"; ~40 % of bytes touched re-derived as `1597e9 × 0.70 / 50.5 = 22.1e9 / 55.83e9` | consistency with the corrected row; conclusion unchanged | METHODOLOGY §4 |
| §9c | added the byte-level arithmetic to every row; DeepSeek MLA row relabelled **DeepSeek-R1 / V3** with `61×576×2 = 70,272 B/token` and the FP8 35,136 / FP4 17,568 pair | METHODOLOGY §8 pins those three values; this doc reproduces them, so no disagreement to record | METHODOLOGY §2, §8 |
| §9c | added: FP4 KV is unreachable on sm_120 through any FMHA path, so plan MLA models at the FP8 column | `QE4m3KvE2m1` has no SM120/SM121 variant (§5d, §6) | [TensorRT-LLM#11799](https://github.com/NVIDIA/TensorRT-LLM/issues/11799) |

### Feasibility and the concurrency table (checklist 5, 6)

| Section | old → new | reason | source |
|---|---|---|---|
| §9d gpt-oss-120b MXFP4 | `140 / 35 / 8.8` → **112 / 28 / 7** | the fact-checker's sliding-window fix was right but ran on the 61 GB weight figure, which was 60.69 GiB; at the derived 65.17 GB the budget falls 21.4 → 17.23 GB | §9b derivation |
| §9d gpt-oss-120b naive row | `71 / 18 / 4.4` → **57 / 14 / 3** | same weight correction | §9b derivation |
| §9d 70 B FP8 | `9 / 2.3 / 0.6` → **9 / 2 / infeasible (KV)** | METHODOLOGY §3 consistency rule: a row that cannot fit one sequence is printed `infeasible (KV)`, never a fraction. Weights refined 70 → 70.07 GB (FP8 32×32 UE8M0 = 1.001 B/param) | METHODOLOGY §1, §3 |
| §9d Qwen3-30B-A3B FP8-KV | `162 / 41 / 10.2` → **162 / 40 / 10** | 32 K value was rounded up; `65.30e9 / 1.611e9 = 40.54` floors to 40 | METHODOLOGY §3 (`floor`) |
| §9d all rows | fractional 128 K values → integers or `infeasible (KV)`; header now states the budget in bytes | consistency rule | METHODOLOGY §3 |
| §9d | added the re-fetched `config.json` confirmations for both geometries (gpt-oss 18 `sliding_attention` + 18 `full_attention`, `sliding_window: 128`, 8 KV heads, head_dim 64; Qwen3-30B-A3B 48 L / 4 KV heads / 128 head_dim) | the sliding-window and `n_kv_heads` fixes are the two most decision-relevant corrections in the doc and were resting on a single citation each | both config.json files, re-fetched 2026-09-19 |

### Bandwidth provenance (checklist 8)

| Section | old → new | reason | source |
|---|---|---|---|
| §1 bandwidth row | cited `lp2263.pdf` alone → now cites NVIDIA's Server Edition page ("Memory Bandwidth: 1597 GB/s") **and** Lenovo's HTML product guide, with a note that the PDF is not machine-extractable | citation integrity: the figure must be quotable from a page that actually renders it | [NVIDIA SE page](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/) · [Lenovo HTML guide](https://lenovopress.lenovo.com/lp2263-thinksystem-nvidia-rtx-pro-6000-blackwell-server-edition-pcie-gen5-gpu) |
| §8d | (no 1,792 figure removed) → added a blockquote stating that **every** number in §8d is Workstation/Max-Q silicon, where 1,792 GB/s is the correct peak, and that it is not transferable to the Server Edition; added the Server-Edition `est.` (~1,310 GB/s effective at the same 82 % MBU, and ~11 % lower Qwen3-4B decode) | the fact-checker asked that 1,597 be used in every roofline and that prior uses of 1,792 be identified. §9a/§9b already used 1,597; §8d legitimately uses 1,792 and now says so | §1, §2 |
| §9b | formula line now names `BW = 1,597e9 B/s (Server Edition)` and gives the WS scaling factor (+12.2 %) | the roofline's bandwidth input was implicit | METHODOLOGY §4 |

### Dense-vs-sparse and pinned inputs (checklist 2, 3)

| Section | old → new | reason | source |
|---|---|---|---|
| §3c | added a citation-integrity note: NVIDIA's SE page **carries no sparsity footnote**, so "these are sparse" is an inference, not a quote — forced because the SE is clocked below the WS whose whitepaper dense FP4 is 2,015.2 TFLOPS | the doc asserted "sparse" as though quoted; re-fetch shows the footnote exists only on the Workstation page | [NVIDIA SE page](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/), re-fetched |
| §3c | added the METHODOLOGY §8 cross-reference: this card is pinned at ≈ 2,000 dense FP4 (4,000 sparse), consistent with 1,920 SE / 2,015.2 WS | pinned-input reconciliation | METHODOLOGY §8 |
| §12a | `0.20× a B200 (8.0 TB/s per GPU)` → **`0.21× a B200`**, with METHODOLOGY §8's pinned **7.7 TB/s** as the primary figure and the DGX-derived 8.0 TB/s as the noted alternative; WS 0.22× → 0.23× | METHODOLOGY §8 pins B200 at 7.7 TB/s; `1597/7700 = 0.207`, `1792/7700 = 0.233` | METHODOLOGY §8 |
| §2, §12c | B200 capacity used as 180 GB throughout | METHODOLOGY §8: B200 = 180 GB, not 192 | METHODOLOGY §8 |

### Prices (checklist 7)

| Section | old → new | reason | source |
|---|---|---|---|
| §8a | 24-row computeprices.com/thundercompute aggregator table → **rebuilt from `cross-cutting/cloud-pricing.md` §5.10 vendor rows only**; aggregator figures demoted to a labelled ⚠️ cross-check | METHODOLOGY §6 / repo rule: price rows come from cloud-pricing.md only | [cloud-pricing.md](../cross-cutting/cloud-pricing.md) §3.1–3.3, §4.1, §4.3–4.5, §5.10 |
| §8a Nebius | `$0.950 on-demand` → **$1.80 on-demand, $0.95 preemptible** | the $0.95 is Nebius's *preemptible* rate presented as on-demand | cloud-pricing.md §4.3 · [Nebius prices](https://nebius.com/prices) |
| §8a CoreWeave | `$1.20 on-demand` → **$2.500 on-demand (High Memory 8×), $1.386 spot, $1.195 spot (Standard)** | the $1.20 is near CoreWeave's *spot* rate, not on-demand | cloud-pricing.md §4.1 · [CoreWeave](https://www.coreweave.com/pricing) |
| §8a Hyperstack | `$1.30 on-demand` → **$1.85 on-demand, $1.30 reserved** | the $1.30 is Hyperstack's *reserved* rate | cloud-pricing.md §4.5 |
| §8a GCP | `$1.74 / $4.50 conflicting ⚠️` → **$4.500 on-demand; $3.105 CUD 1 y; $1.979 CUD 3 y; $1.743 spot** — conflict resolved, the $1.74 was the spot rate | the two "conflicting" figures were on-demand and spot from the same price sheet | cloud-pricing.md §3.2 (GCP accelerator price sheet) |
| §8a AWS | `G7e ⚠️ TBV $3.36` → **`g7e.48xlarge` $4.143 (8×), `g7e.2xlarge` $3.363 (1×), spot $2.506** | ⚠️ replaced with the sourced AWS price-sheet rows | cloud-pricing.md §3.1 |
| §8a Oracle / Azure | `$4.50 ⚠️ TBV` / `$5.50 ⚠️ TBV` → **OCI $4.50 (SKU B112613)** / **Azure: no SKU** | ⚠️ replaced with the sourced OCI price-list-API row; Azure carries no RTX PRO 6000 SKU | cloud-pricing.md §3.3, §5.10 |
| §8a headline | "cheapest on-demand ~$0.59–0.95/GPU-h" → **"cheapest reputable on-demand: Nebius $1.80"** (+ cheapest hyperscaler $4.143, committed $1.979/$1.30, spot $0.95/$1.743) | the old headline had no vendor-page corroboration anywhere in cloud-pricing.md | cloud-pricing.md §5.10 |
| §8b | added an explicit disagreement note: this doc plans at **$16,000** (videocardz 2026-09) vs cloud-pricing.md's **$13,250** list / $14,758 street; and clarified that $14,999 Newegg exists in cloud-pricing.md but **not** as a Server-Edition-specific price | METHODOLOGY §8: state the disagreement and cite both; preserves verification-log #61 | METHODOLOGY §8 · cloud-pricing.md §9 |
| §8c conclusion | "breaks even against the cheapest on-demand clouds (~$0.59–0.95/GPU-h) only at high utilisation, and loses outright to Vast.ai spot. Against the $2.19 market median it is ~2.5× cheaper" → **withdrawn and replaced**: $0.877/GPU-h beats every on-demand rate (2.1× vs Nebius, 4.7× vs AWS) and still wins at 60 % utilisation, but does **not** beat Hyperstack reserved $1.30, GCP 3-yr CUD $1.979 or Nebius preemptible $0.95 | the old conclusion rested on withdrawn aggregator prices; cross-check against cloud-pricing.md §9's $0.962/GPU-h added | cloud-pricing.md §5.10, §9 |
| §12c rental | "market median $2.19 → 43.8 GB-h/$", "CoreWeave $1.20 → 80", "Verda $0.93 → 103", "H100 ~$2.50/h ⚠️", "B200 ~$5.50/h ⚠️" → **same-provider table** (CoreWeave 38.4 / 13.0 / 20.9; Hyperstack 51.9 / 25.0 / 30.0; RunPod 45.9 / 22.9 / 26.5; AWS 23.2 / 11.6 / 12.6) | METHODOLOGY §6 forbids mixing a neighbouring source's row; all six inputs now come from one provider per line | cloud-pricing.md §4.1, §4.4, §4.5, §3.1 |
| §12c headline | **"2.4–3.2× advantage in VRAM per dollar"** → **"1.7–3.0×"** (≈2.0× vs H100, ≈1.8× vs B200) | direct consequence of the same-provider recomputation | as above |
| §12c purchase | added the 7.25 GB/$1k figure at cloud-pricing.md's $13,250; B200 labelled **180 GB as deployed** | pinned inputs; disagreement stated rather than silently picked | METHODOLOGY §8 |

### Engine versions (checklist 12)

| Section | old → new | reason | source |
|---|---|---|---|
| §7 vLLM | timeline ended at `0.20.1/0.20.2` with no current-release row → added **vLLM 0.29.0** (2026-09-09) and a note that the sm_120 reports are nine minor releases stale | re-verified: PyPI `vllm` reports 0.29.0 (GitHub releases API was rate-limited) | [pypi.org/pypi/vllm/json](https://pypi.org/pypi/vllm/json) · [inference-engines.md](../cross-cutting/inference-engines.md) |
| §7 SGLang | `0.5.19` quoted as current → added **SGLang 0.5.20, released 2026-09-18**, flagged as the off-by-one-release pattern; the §5c DSA/SM120 issues were filed against 0.5.19 | re-verified via PyPI | [pypi.org/pypi/sglang/json](https://pypi.org/pypi/sglang/json) · inference-engines.md |
| §7 TensorRT-LLM | field versions only → added **1.2.1 stable / 1.3.0rc27 (2026-09-17)**, with the warning that the reports used rc13 and §9e shows a 2.6× prefill swing per rc bump | re-verified via PyPI; matches inference-engines.md §2.3 | [pypi.org/pypi/tensorrt-llm/json](https://pypi.org/pypi/tensorrt-llm/json) · inference-engines.md §2.3 |

### The four repo models (new §9g)

| Section | old → new | reason | source |
|---|---|---|---|
| §9g (new) | section did not exist → fit, KV bytes/token and max concurrency at 8 K / 32 K / 128 K for **Qwen3.8-27B** (BF16/FP8/NVFP4/INT4), **Marlin-2B**, **DeepSeek-V4.1-Flash** and **Kimi-K3** | requested by the fact-checker; scoped to fit + KV only — **throughput and cost stay out of GPU docs** and the section links to `research/models/<exp>/rtx6000-pro.md` for them | METHODOLOGY §3, §8 |
| §9g Qwen3.8-27B | — → 1 card at every precision; 38 / 122 / 143 / 149 seqs at 8 K for BF16 / FP8 / NVFP4 / INT4; `S = 1` assumed for the 153.9 MB GDN state and marked ⚠️ | METHODOLOGY §2 requires `S` to be taken from the engine doc or marked; vLLM's Mamba cache is ≥ 1 plus speculative slots | METHODOLOGY §2, §8 · models/qwen3827b/architecture.md |
| §9g Qwen3.8-27B NVFP4 | — → noted as **safe on sm_120** (dense GEMM, no MoE grouped GEMM), but capacity-only in practice because vLLM resolves ModelOpt NVFP4 as W4A16 → Marlin | §6a: dense NVFP4 GEMM on sm_120 was never broken; the grouped path is | §6a |
| §9g Marlin-2B | — → 1 card, 640 seqs at 8 K, and **249 concurrent 240-frame video requests** at 23,520 tokens | the video path is the binding case for this model | models/marlin2b/architecture.md |
| §9g DeepSeek-V4.1-Flash | — → **≥ 8 cards** (`510.29e9 / 82.40e9 = 6.19` → next topology step 8), 63.79 GB/GPU, 18.61 GB KV budget/GPU | METHODOLOGY §3 | METHODOLOGY §3, §8 |
| §9g DeepSeek-V4.1-Flash | — → stated that routed experts are **MXFP4 (E2M1 + E8M0/32), not NVFP4**, and that NVIDIA's NVFP4 build is **527.27 GB — larger than the 510.29 GB base**, accuracy-neutral, **no published speedup**, and routes into the broken sm_120 grouped-GEMM path | checklist item 14; this is the first place in the doc the V4.1 formats appear, so it is stated rather than corrected | METHODOLOGY §8 |
| §9g DeepSeek-V4.1-Flash KV | — → planned at the **FP8 column, 1,650 B/token** (9,068 seqs at 8 K), with METHODOLOGY §8's 890 B/token FP4 figure and `models/deepseek41f/architecture.md` §5.2's contrary claim (software dequant → "works identically on Hopper and Ampere") both recorded as a disagreement | METHODOLOGY §8: if a document disagrees, state it and cite both. Matters acutely here because sm_120 has no NVFP4-KV FMHA kernel | METHODOLOGY §8 · models/deepseek41f/architecture.md §5.2 · TensorRT-LLM#11799 |
| §9g Kimi-K3 | — → **does not fit an 8-card box**: needs `1,560.9e9 / 82.40e9 = 18.94` → ≥ 19 cards; 8 cards give 659.2 GB against 1,560.9 GB, short by **901.7 GB (2.4× the box)**; the illustrative 20-card row shows `S = 5` KDA slots × 428.6 MiB = **2.25 GB per request** eating half a card's 4.36 GB budget | METHODOLOGY §2 `S` multiplier and §3 minimum-GPU rule; §4 already rules out multi-node on this card | METHODOLOGY §2, §3, §8 · models/kimik3/architecture.md |

### Citation integrity — the five most load-bearing citations, re-fetched (checklist 13)

| # | Claim | Result |
|---|---|---|
| 1 | **1,597 GB/s** Server Edition bandwidth — the single number every roofline in §9 divides by | **CONFIRMED verbatim** on NVIDIA's own page: "Memory Bandwidth: 1597 GB/s", plus "GPU Memory: 96 GB GDDR7 · FP4 Tensor Core: 4 PFLOPS · FP8: 2 PFLOPS · FP16 \| BF16: 1 PFLOP · TF32: 234 TFLOPS · FP32: 120 TFLOPS". **New finding: the page carries no sparsity footnote** — recorded in §3c. [NVIDIA SE page](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/) |
| 2 | Lenovo: 96 GB GDDR7, 1,597 GB/s, NVLink **No**, MIG up to 4 @ 24 GB, 600 W cappable to 450 W, export "Controlled", "fewer than 70B parameters" | **CONFIRMED verbatim, every field.** Caveat recorded in §1: the cited `lp2263.pdf` is not machine-extractable — quote the HTML product guide instead. [Lenovo HTML guide](https://lenovopress.lenovo.com/lp2263-thinksystem-nvidia-rtx-pro-6000-blackwell-server-edition-pcie-gen5-gpu) |
| 3 | gpt-oss-120b is a sliding-window hybrid (the §9c/§9d correction that halves its per-token KV) | **CONFIRMED**: `sliding_window: 128`, `layer_types` = exactly 18 `sliding_attention` + 18 `full_attention`, `num_key_value_heads: 8`, `head_dim: 64`, and `quantization_config.quant_method: "mxfp4"` with `modules_to_not_convert` = self_attn / router / embed_tokens / lm_head — which is what the new §9b weight derivation assumes. [config.json](https://huggingface.co/openai/gpt-oss-120b/raw/main/config.json) |
| 4 | Qwen3-30B-A3B geometry (the §9d row that was 12× optimistic before the fact-check) | **CONFIRMED**: 48 layers, `num_key_value_heads: 4`, `head_dim: 128`, `sliding_window: null`, `vocab_size: 151936`, `tie_word_embeddings: false` → 96.0 KiB/token BF16, and the untied `lm_head` justifies the 0.622e9 BF16 term in the AWQ derivation. [config.json](https://huggingface.co/Qwen/Qwen3-30B-A3B/raw/main/config.json) |
| 5 | trtllm-gen FMHA has no SM120/121 cubins — load-bearing for §5b, §5d, §6, §7, §9c and gotcha #2/#5 | **PARTIALLY CONFIRMED on re-fetch.** Verbatim: "The trtllm-gen FMHA … kernels used by flashinfer lack pre-compiled cubins for SM120 (RTX PRO 6000) and SM121 (DGX Spark GB10, RTX 5090)" and `FLASHINFER_CHECK(mSM == kSM_100 \|\| mSM == kSM_103, "Unsupported architecture");`. The `QE4m3KvE2m1` architecture list, the "no plan to SM120/121" quote and the 2026-07-16 closure date sit further down a long thread that this fetch truncated — **verification-log entry #20 confirmed all of them and is left standing**, not reverted. [TensorRT-LLM#11799](https://github.com/NVIDIA/TensorRT-LLM/issues/11799) |

### Checklist items that did not apply to this document

TokenSpeed (10), the DSpark 3.51 synthetic acceptance length (11), the MI355X and GB300/B300 capacity and
price corrections (2, 7), the B200-vs-H200 DeepSeek-R1 FP8 gap (9) and the DeepSeek-V4-Flash API output price
(7) appear nowhere in this document — skipped silently per the sweep instructions, and listed here only so the
next pass need not re-check them. Item 15 (no per-(model, GPU) throughput/cost tables in GPU docs) was
honoured: §9g carries fit and KV only and points at `research/models/<exp>/rtx6000-pro.md` for the rest.
