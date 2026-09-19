# deepseek-ai/DeepSeek-V4.1-Flash on NVIDIA RTX PRO 6000 Blackwell Server Edition 96GB GDDR7 (PCIe only, no NVLink; sm_120)

Research date: **2026-09-19**. Formulas, markers, scenarios and planning defaults follow
[`research/METHODOLOGY.md`](../../METHODOLOGY.md). Model inputs from
[`architecture.md`](./architecture.md); GPU inputs from
[`gpus/rtx6000-pro.md`](../../gpus/rtx6000-pro.md); prices from
[`cross-cutting/cloud-pricing.md`](../../cross-cutting/cloud-pricing.md) §5.10.

> **Read this before any number below.** Every published measurement of this exact
> (model, GPU) pair was taken on **Workstation Edition or Max-Q Workstation Edition**
> silicon — 1,792 GB/s, 2,015.2 TFLOPS dense FP4. This document's target is the
> **Server Edition** — **1,597 GB/s, 1,920 TFLOPS dense FP4**
> ([gpus/rtx6000-pro.md §1, §2, §3c](../../gpus/rtx6000-pro.md)). Decode is
> bandwidth-bound on this card, so Server-Edition figures are the measured ones scaled by
> **1,597/1,792 = 0.8912** (−10.9 %); prefill is compute-bound, scaled by
> **1,920/2,015.2 = 0.9528** (−4.7 %). Both scalings are labelled `est.` wherever used.
> `gpus/rtx6000-pro.md` §2 makes the same point: *"always confirm which edition you are
> renting — it is an 11 % TPOT swing."*

---

## 0. Verdict

1. **Yes, runnable today** — and measured: **vLLM-derived `ghcr.io/local-inference-lab/vllm:karmic-kraken-beta`** (B12X SM12x kernels) and an **SGLang-derived** image (`sglang@sha256:c4ca6511…` + a custom Engram adapter) both serve the **native** checkpoint on **4 GPUs**. Stock `vllm/vllm-openai:deepseekv41-flash-0909` on 8 GPUs collapses to **2.3–4.0 tok/s** because CUDA-graph capture is unusable on SM120 ([vLLM #56892](https://github.com/vllm-project/vllm/issues/56892)); there is **no official vLLM `rtx_pro_6000` recipe** for this model (HTTP 404, re-probed today).
2. **Minimum 4 GPUs, recommended 4 GPUs**, TP4 (+EP4), DCP1, PCIe — the 202.76 GB Engram tables **must** live in host RAM (188.83 GiB pinned) or on NVMe; 8 GPUs is the only way to keep them in HBM but has no working measured stack. 16 GPUs needs two chassis and is not a supported design point for this card.
3. **Weight format actually executed: MXFP4 experts natively (W4A8) via the FlashInfer/B12X SM12x fused-MoE path**, FP8 32×32 UE8M0 dense, BF16 embeddings — **not** Marlin W4A16, which is what METHODOLOGY §8 and `gpus/rtx6000-pro.md` §9g assume. This disagreement is stated and resolved in §2. KV runs **FP8, not the shipped 890 B/token FP4**.
4. **Interactive (S1, TPOT ≤ 50 ms): $2.70–$6.22 per 1M output tokens** at the measured 4-GPU/C8 point (740 tok/s Server-Edition-equivalent), $1.92–$4.42 at the est. C52 point. Blended 75/25 **$0.72–$1.65 /1M**.
5. **Max throughput (S4): $0.66–$1.53 per 1M output** `est.` at 4 GPU/C256 ⚠️ — 32× beyond the largest measured concurrency. **Confidence: measured for the 4-GPU C1/C8 operating points and the fit arithmetic; estimate everywhere above C8.** Against DeepSeek's own API at $0.60/M output and $0.2074/M blended, **self-hosting this pair on this GPU never breaks even at any published rental price.**

---

## 1. Fit

Per METHODOLOGY §3: `usable_hbm = 96e9 × 0.90 = 86.40e9 B = 80.47 GiB`. The card is
96 **GB**, not 96 GiB. Topology set for a PCIe card with no NVLink: {1, 2, 4, 8, 16, …};
`gpus/rtx6000-pro.md` §4 states *"multi-node LLM serving is not a supported design point
for this card"*, so 16 is shown for completeness only.

### 1.1 Weights per GPU and KV budget

Checkpoint = **510,286,023,000 B = 510.29 GB = 475.24 GiB** (`model.safetensors.index.json`
`total_size`, METHODOLOGY §8). Of that, the Engram tables are **202.76 GB = 188.83 GiB**
(FP8 payload + E8M0 scales, architecture.md §4.1), leaving **307.53 GB** of
compute weights if they are offloaded.

| GPUs | Engram placement | Weights/GPU (GB) | (GiB) | Activation WS | KV budget/GPU @0.90 | @0.95 | Aggregate @0.90 | Fits? | Fabric |
|---:|---|---:|---:|---:|---:|---:|---:|:--|---|
| 1 | HBM-resident | 510.29 | 475.24 | 4 GB | **−427.89** | −423.09 | — | ❌ | — |
| 1 | host RAM / NVMe | 307.53 | 286.41 | 4 GB | **−225.13** | −220.33 | — | ❌ | — |
| 2 | HBM-resident | 255.14 | 237.62 | 4 GB | **−172.74** | −167.94 | — | ❌ | PCIe |
| 2 | host RAM / NVMe | 153.76 | 143.20 | 4 GB | **−71.36** | −66.56 | — | ❌ | PCIe |
| 4 | HBM-resident | 127.57 | 118.81 | 4 GB | **−45.17** | −40.37 | — | ❌ | PCIe |
| **4** | **host RAM / NVMe** | **76.88** | **71.60** | 4 GB | **+5.52** | **+10.32** | 22.07 GB | ✅ | PCIe Gen5, single chassis |
| 8 | HBM-resident | 63.79 | 59.41 | 5 GB | +17.61 | +22.41 | 140.91 GB | ✅ | PCIe Gen5, single chassis |
| **8** | **host RAM / NVMe** | **38.44** | **35.80** | 5 GB | **+42.96** | **+47.76** | 343.67 GB | ✅ | PCIe Gen5, single chassis |
| 16 | HBM-resident | 31.89 | 29.70 | 5 GB | +49.51 | +54.31 | 792.11 GB | ✅ | **two chassis, standard NIC — not a supported design point** |
| 16 | host RAM / NVMe | 19.22 | 17.90 | 5 GB | +62.18 | +66.98 | 994.87 GB | ✅ | same |

Activation workspace is `est.` at METHODOLOGY §3's 2–6 GB band, taken at the top of it
because mHC carries **four parallel residual copies** (architecture.md §2.7:
*"activations are 4× the usual hidden size … which is the main reason the recipes cap
`--max-num-batched-tokens`"*). Both measured deployments cap the prefill chunk at **2,048**
tokens; 0xSero reports a 4,096-token chunk *"failed near 399k input"*.

**Correction to [`gpus/rtx6000-pro.md`](../../gpus/rtx6000-pro.md) §9g, stated per
METHODOLOGY §8.** That document computes `510.29e9 / (86.40e9 − 4e9) = 6.19` and concludes
**"DeepSeek-V4.1-Flash needs at least 8 cards"**. That is correct *only if the Engram
tables stay in HBM*. With the host/NVMe offload that vLLM's own recipe makes **mandatory on
H100 and B300** (`--engram-config '{"cpu_offload":true}'`, architecture.md §8.4), resident
weights fall to 307.53 GB and `307.53e9 / (86.40e9 − 4e9) = 3.73` → **4 GPUs**. Both
published measurements of this pair run on **four** cards. Cite both documents; the 4-GPU
figure is the one with measurements behind it.

### 1.2 Max concurrency — and which tensors cannot be sharded

`num_key_value_heads = 1` (architecture.md §2.1). There is exactly **one** CSA2 latent KV
head per cache-owning layer, so **plain TP cannot split the KV cache — every rank holds the
whole thing.** The per-GPU budget binds, not the aggregate. Both measured deployments run
**TP4 / DCP1** (local-inference-lab states `TP4/DCP1` explicitly), i.e. the replicated case.
The sharded (DP-attention / DCP) row is shown because it is the upside if an engine ever
offers decode-context-parallel here — `FLASHINFER_MLA_SPARSE_DSV41` reports **DCP ❌**
([flash-attention.md §3.2](../../cross-cutting/flash-attention.md)), so it does not today.

`max_concurrency(ctx) = floor( budget / (ctx × b_token + fixed_state) )`, fixed state
2.77 MiB (FP8 ring, METHODOLOGY §8; 5.38 MiB at the reference implementation's BF16
container — architecture.md §5.3 ⚠️, used for the BF16 rows).

| GPUs | Engram | KV dtype | mode | 8K | 32K | 128K | 1M |
|---:|---|---|---|---:|---:|---:|---:|
| 4 | host | FP4 890 ⚠️ | **TP (replicated)** | **541** | **172** | **46** | **5** |
| 4 | host | FP4 890 ⚠️ | DP-attn (sharded) | 2,164 | 688 | 184 | 23 |
| **4** | **host** | **FP8 1650** | **TP (replicated)** | **336** | **96** | **25** | **3** |
| 4 | host | FP8 1650 | DP-attn | 1,344 | 387 | 100 | 12 |
| 4 | host | BF16 3200 | TP (replicated) | 173 | 49 | 12 | 1 |
| 4 | host | BF16 3200 | DP-attn | 693 | 199 | 51 | 6 |
| 8 | HBM | FP8 1650 | TP (replicated) | 1,072 | 309 | 80 | 10 |
| 8 | HBM | BF16 3200 | TP (replicated) | 553 | 159 | 41 | 5 |
| 8 | HBM | FP8 1650 | DP-attn | 8,580 | 2,473 | 642 | 81 |
| **8** | **host** | **FP8 1650** | **TP (replicated)** | **2,615** | **754** | **196** | **24** |
| 8 | host | BF16 3200 | TP (replicated) | 1,348 | 388 | 101 | 12 |
| 8 | host | FP8 1650 | DP-attn | 20,926 | 6,032 | 1,568 | 198 |
| 16 | host | FP8 1650 | TP (replicated) | 3,786 | 1,091 | 283 | 35 |
| 16 | host | BF16 3200 | TP (replicated) | 1,952 | 562 | 146 | 18 |

⚠️ The **FP4 890 B/token row is not reachable on sm_120 in any engine verified in this
pass** — see §2. It is printed so the upside is visible if vLLM's SM12x dtype gate opens.

Per-sequence KV cost:

| KV dtype | 8K | 32K | 128K | 1M |
|---|---:|---:|---:|---:|
| FP4 890 + 2.77 MiB | 9.72 MiB | 30.58 MiB | 114.02 MiB | 892.77 MiB |
| **FP8 1650 + 2.77 MiB** | **15.66 MiB** | **54.33 MiB** | **209.02 MiB** | **1,652.77 MiB** |
| BF16 3200 + 5.38 MiB | 30.38 MiB | 105.38 MiB | 405.38 MiB | 3,205.38 MiB |

**Validation — the TP-replicated reading is the one the hardware confirms.** Both 4-GPU
deployments run at `--gpu-memory-utilization`/`MEMORY_FRACTION` **0.95**, which gives a
per-GPU KV budget of **10.32 GB**:

| Reported logical KV tokens | Source | Implied B/token against 10.32 GB **per GPU** | vs architectural FP8 1,650 |
|---:|---|---:|---:|
| 4,714,406 | local-inference-lab, auto-sized | **2,189** | 1.33× |
| 4,200,000 | 0xSero `MAX_TOTAL_TOKENS` cap | 2,457 | 1.49× |
| 4,063,744 | 0xSero peak populated, 8 × 500K | 2,539 | 1.54× |

A 1.33–1.54× paged-allocation overhead over the architectural 1,650 B/token is entirely
plausible (main pages 256 tokens, sliding-window pages 128 tokens, per
local-inference-lab's own serving-defaults table). The **aggregate** reading would imply
8,755 B/token — 5.3× the architectural figure — which nothing explains. **Plan with the TP
row.**

**Tensors that cannot be sharded** (architecture.md §3.1, §3.4) — present in full on every
rank, whatever the parallelism:

| Tensor group | Why it replicates | Bytes |
|---|---|---:|
| Routers `ffn.gate` (BF16, 384×5120 × 40 layers) | every rank must score all 384 experts before dispatch | 0.157 GB |
| mHC coefficient projections `hc_*_fn` (F32, 24×20480) | produce 24 scalars consumed by the *next* sublayer — no useful split | 0.157 GB |
| KV compressors `attn.compressor.*` (BF16, 4 layers) | feed the single shared latent | 0.037 GB |
| DSA indexer `wk` / `weights_proj` / `k_norm` (BF16, 8 layers) | tiny, per-head reduction | 0.003 GB |
| RMSNorm weights; `ffn.gate.bias`, `bias_vl`, `attn.attn_sink` (F32) | elementwise / per-head scalars | 0.001 GB |
| **Subtotal replicated** | | **0.355 GB/rank** |
| `attn.wo_a` | FP8 on disk, **dequantised to BF16 at load** by the reference `convert.py` because it is a block-diagonal `einsum`, not a `Linear` | **+1.34 GB runtime** |
| **The CSA2 latent KV cache itself** | `num_key_value_heads = 1` → no head to split under TP | see table above |
| Engram tables, if HBM-resident and not row-sharded | 24 random single-row gathers per token per module | 202.76 GB |

0.355 GB/rank is noise at these weight sizes. **The KV replication is not noise** — it is
the 4× difference between the TP and DP-attn rows.

**Multi-node fabric needed?** No, at 4 or 8 GPUs — one PCIe Gen5 chassis. At 16 it needs
two chassis over a standard NIC with no GPU fabric; `gpus/rtx6000-pro.md` §4 rules that out,
and §11 gotcha 10 warns NCCL hangs on the first collective unless IOMMU and ACS are
disabled in BIOS even *within* a chassis.

---

## 2. What runs on this GPU for this model

| Optimization | Status | Kernel / flag | Expected effect |
|---|---|---|---|
| **Attention: FA2 / FA3 / FA4** | **unsupported** for this operator | — | This model's attention is `sparse_attn(q, kv, sink, topk_idxs, scale)` over 640 gathered entries — *not* FlashAttention-shaped (architecture.md §8.7). Separately, FA3/FA4 need `tcgen05`, which sm_120 does not have; vLLM refuses FA4 on 12.x outright ([flash-attention.md §9.5](../../cross-cutting/flash-attention.md)). Irrelevant either way. |
| **Attention: FlashMLA / FlashMLA-sparse / `FLASHMLA_SPARSE_DSV41`** | **unsupported** | — | gated `capability.major in [9, 10]`. `FLASHMLA_MEGA_ATTN_DSV41` is `major == 10` only (flash-attention.md §6.2). |
| **Attention: `FLASHINFER_MLA_SPARSE_DSV41`** | **native** | vLLM default backend on SM12x; **an FP8 KV dtype is mandatory** (`fp8` / `fp8_e4m3` / `fp8_ds_mla`), plus a FlashInfer build carrying the SM120 sparse-MLA decode API | The working V4.1 sparse path on this card. Head size 512, block size 128 (flash-attention.md §6.2, §9.5). |
| **Attention: `FLASHINFER_MLA_SPARSE_SM120`** | native | 12.x, KV `auto`/`fp8`/`fp8_e4m3`/`fp8_ds_mla`, block 64 or 256 | dedicated SM120 sparse kernel (flash-attention.md §9.5). |
| **Attention: B12X sparse-MLA** | **native** (community image) | `--attention-backend B12X_MLA_SPARSE`, `--moe-backend b12x`, `--linear-backend b12x` | What both measured 4-GPU stacks actually run. vLLM's own DGX-Spark (GB10, also SM12x) profile for the *V4-Flash* sibling uses exactly these flags. |
| **DSA indexer top-k kernel** | **native** | FlashInfer `top_k_varlen`; *"SM120/121 also picks up top-k 192 and 256"* (FlashInfer 0.6.18) | The 1M-context bottleneck (architecture.md §6.3). vLLM's V4-Flash SM120 profile needs *"a FlashInfer that instantiates the SM120 sparse-MLA decode kernel for `topk=192`"*. **V4.1 uses `index_topk: 512`** — FlashInfer PR #4955 lists *"primary top-k 128 or 512"* as supported, so 512 is covered ⚠️ **TO BE VERIFIED** end-to-end on this card. |
| **TileLang DSA sparse-MLA** | **unsupported** | — | needs 202–206 KB of shared memory; the device allows **101,376 B (99 KB)** and returns `invalid argument` (gpus/rtx6000-pro.md §1, §5c). |
| **Linear-attention kernels** | **n/a** | — | V4.1-Flash has no linear-attention layers. |
| **Weight format: MXFP4 routed experts** | **native W4A8** ⚠️ **disputed** | FlashInfer `b12x_fused_moe` with `quant_mode="mxfp4"` / `--moe-runner-backend flashinfer_mxfp4` | See the disagreement box below. Neither measured deployment reports a Marlin fallback; 0xSero states *"Published mixed precision: FP4 routed experts … No EXL3 conversion or pruning."* |
| **Weight format: FP8 32×32 UE8M0 dense/attention** | **native** (per-tensor FP8 is native on sm_120) ⚠️ | CUTLASS SM120 FP8 | **`DeepGEMM does NOT support sm_120`** (`arch_major=12` → `DG_HOST_UNREACHABLE`), so the Mega-Gate / Mega-mHC / Mega-MoE kernels DeepSeek ships are unavailable (gpus/rtx6000-pro.md §7). TRT-LLM's sm120 row omits **block-scaled** FP8 entirely (quantization-formats.md §5.1) — ⚠️ whether a 32×32 2-D block-scaled FP8 GEMM has a first-class sm_120 kernel is **TO BE VERIFIED**; SGLang has open work on it (#39978, #39872, #39065). |
| **Weight format: NVFP4 build (`nvidia/DeepSeek-V4.1-Flash-NVFP4`)** | **do not use** | — | It is **larger** (527.27 GB vs 510.29 GB), accuracy-neutral, has **no published speedup**, and routes into the sm_120 MoE grouped-GEMM path that produced garbage by default (gpus/rtx6000-pro.md §6a, §9g; architecture.md §9.1, §12.14). Not our pair; listed so nobody reaches for it. |
| **KV-cache quant: FP8** | **native, and mandatory** | `--kv-cache-dtype fp8` / `fp8_e4m3` | 1,650 B/token. The vLLM SM12x branch of `FLASHINFER_MLA_SPARSE_DSV41` **requires** an FP8 dtype. The 8-GPU repro and vLLM's V4-Flash SM120 profile both set it. |
| **KV-cache quant: NVFP4 / the shipped 890 B/token cache** | **⚠️ kernel exists, no engine routes to it** | would need `nvfp4_ds_mla` → `FLASHMLA_MEGA_ATTN_DSV41` → `major == 10` | FlashInfer **PR #4955 merged 2026-09-07** — *"feat(sm120): add NVFP4 sparse MLA support for DeepSeek V4 Flash"*, SM120/SM121, measured **on an RTX PRO 5000** at 1.41–1.67× prefill / 1.31× decode / ~9 % end-to-end. **But vLLM's SM12x dtype list still refuses NVFP4** (flash-attention.md §11). vLLM's V4-Flash SM120 profile hard-sets `--attention_config.use_fp4_indexer_cache False`. Upside if it opens: 1.85× KV capacity (541 vs 336 concurrent at 8K, 4 GPU). |
| **Prefix caching** | **native** | `--enable-prefix-caching`; on by default in the local-inference-lab profile (*"retention interval defaults to 0, not cache disabled"*) | Measured on this GPU family: the diffbot 2-bpw run served **87 % of 18.5M prompt tokens from prefix cache** over a 40-min agentic workload; satindergrewal measures warm-turn prefill on a 12.7K prefix **5.85 s → 0.53–0.67 s (8–11×)**. |
| **Speculative decoding: DSpark** | **native**, block 5 | `--speculative-config '{"method":"dspark","num_speculative_tokens":5,…}'`; `--adaptive-verification` on in the local-inference-lab profile | Works and is load-bearing — §3 shows the achieved MBU is only ~0.22–0.34, so DSpark is doing most of the throughput. **Acceptance is disputed:** 0xSero medians **74.1–88.4 %**, local-inference-lab C1 accepted **length** 2.443–2.551 of 5, and vLLM #56892 reports *"~72–87 % of drafted tokens are rejected"* — the exact inverse. See §6. |
| **Speculative decoding: MTP / EAGLE** | **unsupported** | — | V4.1 ships no MTP head (architecture.md §7.1). The V4-Flash sibling's recipe marks `mtp` **`"unsupported"` on `rtx_pro_6000_8x`** outright. |
| **EP / TP** | native | `--enable-expert-parallel`, `--tensor-parallel-size 4` | TP4+EP4 is the measured shape. `gpus/rtx6000-pro.md` §4: TP=4 over PCIe gave **6–7 tok/s** where TP2+PP2 gave 46–49 on a *different* model — but here TP4+EP4 demonstrably works at 740–830 tok/s, because the 384-expert top-6 MoE dispatches far less all-reduce traffic per token than a dense all-reduce-per-layer model. |
| **DP-attention** | **unsupported for this model's KV** | — | `FLASHINFER_MLA_SPARSE_DSV41` reports **DCP ❌**. This is what forces the replicated-KV row in §1.2. |
| **PD disaggregation** | **unsupported** | — | vLLM's V4-Flash recipe declares `"pd_cluster": {"rtx_pro_6000_8x": "unsupported"}`. `gpus/rtx6000-pro.md` §7 adds that Dynamo has no sm_120 statement at all and that PCIe-only KV transfer would be bandwidth-limited. serving-optimizations.md §3.5: *"disaggregation is a thousand-GPU problem."* |
| **CUDA graphs** | **native for decode, off for prefill** | `--compilation-config '{"cudagraph_mode":"FULL_AND_PIECEWISE"}'`, `--max-cudagraph-capture-size 128`; **prefill graphs disabled** | **The single largest knob on this pair.** With them: 740–830 tok/s at C8. Without them (`--enforce-eager` on the stock image): **2.3–4.0 tok/s** — a ~200× collapse (vLLM #56892). TokenSpeed reaches the same conclusion independently: the CED decoder forces `--disable-prefill-graph` (architecture.md §8.1). |
| **Multimodal encoder placement** | **native, partially validated** | `--mm-encoder-tp-mode data`, or `--language-model-only` to drop the 0.49 B ViT and free its VRAM | 0xSero: single-image at the full **1,024 image tokens/image** passed; **multi-image unresolved**; **six-frame video test FAILED** — *"Do not treat video as supported by this release."* local-inference-lab passes 2/8/16 128×128 images and image history. |

> **Disagreement on the expert GEMM, stated per METHODOLOGY §8 — do not silently pick one.**
> [`METHODOLOGY.md`](../../METHODOLOGY.md) §8 pins this GPU as *"NVFP4 MoE grouped-GEMM
> broken → Marlin W4A16 (FlashInfer PR #2898 may have closed the MXFP4 path — ⚠️)"*, and
> [`gpus/rtx6000-pro.md`](../../gpus/rtx6000-pro.md) §9g says flatly *"MXFP4 falls back to
> Marlin W4A16 … a dequant path, not a tensor-core FP4 path."*
> [`cross-cutting/quantization-formats.md`](../../cross-cutting/quantization-formats.md)
> §4.2 and §9.6 say the opposite for this checkpoint: RTX PRO 6000 SM120 is **`native` W4A8**
> via `b12x_fused_moe` / `--moe-runner-backend flashinfer_mxfp4`, citing FlashInfer issue
> #2847 closed by **PR #2898** and FlashInfer 0.6.18 shipping *"MXFP4 on Blackwell RTX PRO
> and DGX Spark."*
> **Resolution for this pair:** the measured stacks both select `b12x`, both report native
> FP4 routed experts with no requantisation, and neither logs the Marlin warning. The
> `quantization-formats.md` reading is the one with measurements behind it. The
> `gpus/rtx6000-pro.md` §9g reading describes **stock vLLM**, where `get_mxfp4_backend()`
> still matches only `is_device_capability_family(100)` — and stock vLLM is also the build
> that produces 2.3–4.0 tok/s. Both statements are true of different builds. ⚠️ **TO BE
> VERIFIED:** no A/B of `b12x` vs Marlin on this model on this card is published.

---

## 3. Throughput and latency

### 3.1 MBU / MFU assumptions, and why they are lower than METHODOLOGY's band

METHODOLOGY §4's planning band is MBU 0.5–0.7 for first-gen Blackwell software;
`gpus/rtx6000-pro.md` §9b narrows it to **0.50–0.65 on sm_120** and §9e gives MFU
**0.20–0.35 at FP4**, **0.25–0.40 at FP8**. This pair does **not** reach either band.

Method: compute the pure roofline `tokens/s = batch / (bytes_per_step / (BW × n_gpus))` at
**MBU = 1.0 and accepted-length A = 1**, then define
**κ = measured / roofline**. κ folds together MBU and the speculative multiplier, so
**achieved MBU = κ / A**. Bytes per step use METHODOLOGY §4's MoE distinct-expert model with
architecture.md §6.3's inputs (one MXFP4 expert = 18,800,640 B; static non-expert read
18.75 GB; KV read re-derived at FP8 entry sizes: 38×512×528 main + 4×16,384×132 pool +
330×ctx indexer scan + 2.77 MiB SWA, per seq).

| Measured source (all on **Workstation / Max-Q** silicon) | meas. tok/s | roofline @1,792 GB/s | **κ** | MBU at A = 2.5 | Server-Edition equiv. `est.` |
|---|---:|---:|---:|---:|---:|
| local-inference-lab TP4, C1, ctx ≈ 0, RAM Engram | 256.59 | 307.8 | **0.834** | 0.333 | 228.7 |
| local-inference-lab TP4, C8 aggregate, RAM Engram | 830.82 | 1,079.5 | **0.770** | 0.308 | **740.4** |
| 0xSero TP4/EP4, C1, 2K in, NVMe Engram | 226.8 | 307.8 | 0.737 | 0.295 | 202.1 |
| 0xSero TP4/EP4, C8 agg, 2K in | 729.1 | 1,079.6 | 0.675 | 0.270 | 649.8 |
| 0xSero TP4/EP4, C8 agg, 128K in | 736.3 | 1,072.8 | 0.686 | 0.275 | 656.2 |
| 0xSero TP4/EP4, C8 agg, 500K in | 599.7 | 1,053.6 | **0.569** | 0.228 | 534.4 |

**κ = 0.569–0.834, mean 0.712. Achieved MBU = 0.22–0.34** at the reported DSpark accepted
length of 2.44–2.55. That is **roughly half the sm_120 planning band** and a third of
Hopper's. `gpus/rtx6000-pro.md` §9b independently derives ~0.26 achieved MBU for a
small-expert MoE on this card, so the number is not an outlier for the GPU — it is what
sm_120 does on MoE decode.

**Prefill MFU, from measurement** (Server-Edition-scaled, 4 GPU, denominator 7,680 TFLOPS
dense FP4), FLOPs/token = 15.79 GFLOP GEMM (architecture.md §6.1) + the §6.2 attention terms:

| Stack | prefill tok/s (SE `est.`) | TFLOPS/GPU | MFU vs FP4 | vs FP8 | vs BF16 |
|---|---:|---:|---:|---:|---:|
| B12X, RAM Engram, 32K uncached | 19,278 | 84.4 | **0.044** | 0.088 | 0.176 |
| NVMe Engram, 32K | 7,087 | 31.0 | **0.016** | 0.032 | 0.065 |

Even the fast stack is at **4.4 % of dense FP4 peak** — an order of magnitude below the
§9e band. Contrast `gpus/rtx6000-pro.md` §9e's measured 62,407 tok/s prefill on a 3 B-active
NVFP4 MoE (MFU 0.30–0.40): the FP4 prefill path on sm_120 *is* healthy when the kernels are
the dense TRT-LLM/CUTLASS ones. It is **this model's** encoder — indexer full scans, the
Engram gathers, the mHC Sinkhorn, and the absence of DeepGEMM — that costs the other 90 %.

**Tables below therefore use the calibrated κ band 0.57–0.83 rather than a nominal MBU,
and every row above concurrency 8 is `est.` and extrapolated beyond all measurement.**

### 3.2 Estimated decode, 4 GPUs (the measured shape)

Engram host-offloaded, FP8 KV, TP4 replicated KV → `max_concurrency` = 336 (S1), 96 (S2),
25 (S3) at `--gpu-memory-utilization 0.90`. Rows above those are `infeasible (KV)` per
METHODOLOGY §3. (At the 0.95 the deployments actually use, the caps are 629 / 180 / 47.)

| batch | scenario | out tok/s per GPU | aggregate tok/s | TPOT ms | GB/step | TPOT ≤ 50 ms? |
|---:|---|---:|---:|---:|---:|:--|
| 1 | S1 4K/512 | 39.0–57.2 | 156–229 | 4.4–6.4 | 23.3 | yes |
| 1 | S2 32K/1K | 39.0–57.1 | 156–229 | 4.4–6.4 | 23.3 | yes |
| 1 | S3 128K/2K | 39.0–57.1 | 156–228 | 4.4–6.4 | 23.3 | yes |
| **8** | **S1 4K/512** | **136.9–200.5** | **548–802** | **10.0–14.6** | 53.1 | yes |
| 8 | S2 32K/1K | 136.7–200.2 | 547–801 | 10.0–14.6 | 53.2 | yes |
| 8 | S3 128K/2K | 136.0–199.2 | 544–797 | 10.0–14.7 | 53.5 | yes |
| 32 | S1 4K/512 | 217.4–318.3 | 870–1,273 | 25.1–36.8 | 133.8 | yes |
| 32 | S2 32K/1K | 216.9–317.6 | 868–1,271 | 25.2–36.9 | 134.1 | yes |
| 32 | S3 128K/2K | — | **infeasible (KV)** | — | — | — |
| 64 | S1 4K/512 | 285.7–418.4 | 1,143–1,674 | 38.2–56.0 | 203.6 | marginal |
| 64 | S2 32K/1K | 284.9–417.2 | 1,140–1,669 | 38.4–56.2 | 204.2 | marginal |
| 64 | S3 128K/2K | — | **infeasible (KV)** | — | — | — |
| 128 | S1 4K/512 | 427.7–626.4 | 1,711–2,505 | 51.1–74.8 | 272.0 | **NO** |
| 128 | S2 32K/1K | — | **infeasible (KV)** | — | — | — |
| 128 | S3 128K/2K | — | **infeasible (KV)** | — | — | — |
| 256 | S1 4K/512 | 754.7–1,105.2 | 3,019–4,421 | 57.9–84.8 | 308.3 | **NO** |
| 256 | S2 32K/1K | — | **infeasible (KV)** | — | — | — |
| 256 | S3 128K/2K | — | **infeasible (KV)** | — | — | — |

Largest batch holding TPOT ≤ 50 ms at the pessimistic κ = 0.569: **C52 at S1**
(1,042–1,526 tok/s agg), C51 at S2, C51 at S3 (which is above the 25-sequence KV cap at
0.90, so S3's real interactive ceiling is **25**).

### 3.3 Estimated decode, 8 GPUs

Caps: 2,615 (S1) / 754 (S2) / 196 (S3), Engram offloaded, FP8 KV, TP8 replicated.

| batch | scenario | out tok/s per GPU | aggregate tok/s | TPOT ms | TPOT ≤ 50 ms? |
|---:|---|---:|---:|---:|:--|
| 1 | S1 4K/512 | 39.0–57.2 | 312–457 | 2.2–3.2 | yes |
| 8 | S1 4K/512 | 136.9–200.5 | 1,095–1,604 | 5.0–7.3 | yes |
| 32 | S1 4K/512 | 217.4–318.3 | 1,739–2,547 | 12.6–18.4 | yes |
| 64 | S1 4K/512 | 285.7–418.4 | 2,286–3,347 | 19.1–28.0 | yes |
| 64 | S3 128K/2K | 282.0–413.0 | 2,256–3,304 | 19.4–28.4 | yes |
| 128 | S1 4K/512 | 427.7–626.4 | 3,422–5,011 | 25.5–37.4 | yes |
| 128 | S3 128K/2K | 419.5–614.3 | 3,356–4,914 | 26.0–38.1 | yes |
| 256 | S1 4K/512 | 754.7–1,105.2 | 6,038–8,842 | 29.0–42.4 | yes |
| 256 | S3 128K/2K | — | **infeasible (KV)** | — | — |

⚠️ **These 8-GPU rows are the least trustworthy numbers in this document.** The only
published 8-GPU measurement of this pair is **2.3–4.0 tok/s**, three orders of magnitude
below. The gap is entirely CUDA-graph capture (§3.5); if that is fixed, the roofline says
8 GPUs should roughly double 4-GPU throughput, but nobody has shown it.

### 3.4 TTFT

Anchored on the two measured prefill rates rather than an assumed MFU. 4 GPUs,
Server-Edition-scaled (×0.9528).

| Scenario | Stack | prefill tok/s `est.` | TTFT, 0 % prefix hit | TTFT, 90 % hit |
|---|---|---:|---:|---:|
| S1 4K/512 | B12X + RAM Engram | 19,278 | **0.21 s** | 0.02 s |
| S1 4K/512 | NVMe Engram | 7,087 | 0.58 s | 0.06 s |
| S2 32K/1K | B12X + RAM Engram | 19,278 | **1.70 s** | 0.17 s |
| S2 32K/1K | NVMe Engram | 7,087 | 4.62 s | 0.46 s |
| S3 128K/2K | B12X + RAM Engram | 19,278 | **6.80 s** | 0.68 s |
| S3 128K/2K | NVMe Engram | 7,087 | 18.49 s | 1.85 s |

The h = 90 % column is `TTFT(0) × (1−h)` per serving-optimizations.md §1.2, ignoring the
KV-load residual (GPU-resident cache → pointer swap). **The Engram placement is a 2.7×
TTFT factor** — the largest single lever on this pair, and it costs 188.83 GiB of host RAM.

### 3.5 Measured — published numbers for this exact pair

Every row below is a community measurement on **Workstation or Max-Q** silicon. None is a
vendor benchmark: **there is no MLPerf result, no InferenceMAX row, and no official vLLM
`rtx_pro_6000` recipe for DeepSeek-V4.1-Flash** (re-probed 2026-09-19:
`https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/rtx_pro_6000.json` → **HTTP
404**, and the model's hardware map is
`{h100, h200, b200, gb200, gb300, b300, mi350x}` with no RTX entry).

**A. 4× RTX PRO 6000 Workstation, TP4/DCP1, RAM Engram, B12X, DSpark K7 adaptive**
[local-inference-lab/rtx6kpro](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/deepseek-v4.1-flash.md)
— image `ghcr.io/local-inference-lab/vllm:karmic-kraken-beta`, 32 slots, 4,096-token budget,
target temperature 1 / top-p 0.95, context-zero decode = median of three warmed 30 s runs:

| Metric | Community R38 → wheel image | Δ | Server-Edition equiv. `est.` |
|---|---:|---:|---:|
| **C1 output** | 250.87 → **256.59 tok/s** | +2.28 % | **228.7** |
| **C8 aggregate output** | 808.17 → **830.82 tok/s** | +2.80 % | **740.4** |
| **32K prefill (uncached, from client TTFT)** | 20,079 → **20,234 tok/s** | +0.77 % | **19,278** |
| Logical KV tokens | 4,503,190 → **4,714,406** | +4.69 % | — |

Their own caveats, verbatim: the native cache is *"MXFP8 sliding-window payloads, NVFP4
indexed payloads and index/state groups. Do not describe it as a uniform FP8 cache."* And
`--engram-table-memory ram` *"RAM allocation failure does not silently choose disk."*
Breakable prefill (`VLLM_USE_BREAKABLE_CUDAGRAPH=1`) buys **+1.09 % prefill for −16.55 %
logical KV** — off by default.

**B. 4× RTX PRO 6000 @ 275 W, TP4/EP4, NVMe Engram + 64 GiB DDR5 cache, DSpark block 5**
[0xSero/deepseek-v4.1-flash-4x-rtx-pro-6000](https://github.com/0xSero/deepseek-v4.1-flash-4x-rtx-pro-6000)
— SGLang-derived image `sglang@sha256:c4ca6511…`, 8 slots, 8,192 forced output tokens per
request, `MEMORY_FRACTION 0.95`, `CONTEXT_LENGTH 524288`, chunked prefill 2,048, *"four GPUs
capped at 275 W each, connected over PCIe without NVLink"*. Full 45-case grid; excerpt:

| Input tok | C | Prefill tok/s | **Total decode tok/s** | Decode/req | TTFT s | Burst gap p50 ms | DSpark acceptance |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 512 | 1 | 1,943.0 | **200.9** | 200.9 | 0.26 | 23.59 | 78.9 % |
| 512 | 8 | 5,016.9 | **713.5** | 88.8 | 0.81 | 55.14 | 79.2 % |
| 2,048 | 1 | 7,010.3 | **226.8** | 226.8 | 0.29 | 22.96 | 85.3 % |
| 2,048 | 8 | 6,772.6 | **729.1** | 91.0 | 1.66 | 54.41 | 80.9 % |
| 8,192 | 8 | 7,311.2 | **704.5** | 89.9 | 5.32 | 54.85 | 80.2 % |
| 32,768 | 1 | 7,438.7 | **211.5** | 211.5 | 4.40 | 23.14 | 79.7 % |
| 32,768 | 8 | 7,420.9 | **747.7** | 95.0 | 20.20 | 54.37 | 84.3 % |
| 65,536 | 8 | 7,335.8 | **712.3** | 89.2 | 40.49 | — | 86.4 % (C2) |
| 131,072 | 8 | 7,089.5 | **736.3** | 91.3 | 83.39 | — | — |
| 200,000 | 8 | 6,864.8 | **723.6** | 93.2 | 131.55 | — | — |
| 400,000 | 8 | 6,156.5 | **652.5** | 82.3 | 292.54 | — | — |
| 500,000 | 8 | 5,834.8 | **599.7** | 75.1 | 386.09 | — | — |

Capacity gate passed: *"eight distinct 500k inputs completed 8,192 outputs each; peak
**4,063,744 populated tokens**, no prefix reuse or retractions."* Correctness: arithmetic,
schema-constrained JSON and a tool round trip passed; single-image at 1,024 image tokens
passed; **six-frame temporal-colour video test failed**.

**C. 8× RTX PRO 6000 Blackwell Max-Q, stock image, TP8, `--enforce-eager` — the failure case**
[vLLM #56892](https://github.com/vllm-project/vllm/issues/56892), opened 2026-09-14, driver
590.48.01, image `vllm/vllm-openai:deepseekv41-flash-0909`, FP8 checkpoint,
`--kv-cache-dtype fp8 --block-size 128 --gpu-memory-utilization 0.85 --max-num-seqs 16
--max-model-len 524288 --enforce-eager`:

> *"Avg generation throughput: **2.3–4.0 tokens/s**"* for 3 concurrent requests, against
> *"DS-V4-Flash on the same 8× RTX PRO 6000 reaches ~2452 tok/s aggregate."*
> Cause: *"CUDA graph capture is not usable on SM120 for V4.1-Flash"*, so *"every decode
> step pays full Python/dispatch overhead on a 552B-parameter model."* DSpark
> *"~72–87 % of drafted tokens are rejected"* and *"only adds overhead on top of eager mode."*
> No maintainer response as of this pass.

**D. Requantised variants on 2 cards** (not the released checkpoint — listed for the
dequant/requant alternative in §6):

| Build | GPUs | Decode | Prefill | TTFT | Quality |
|---|---|---:|---:|---:|---|
| [diffbot EXL3 2.0 bpw](https://huggingface.co/diffbot/DeepSeek-V4.1-Flash-EXL3-2.0bpw-2x-RTX-PRO-6000) (architecture.md §10.2) | 2× Max-Q 300 W, TP2 | **113.3** tok/s @2K 1-stream, **277.2** @4 streams, **116.6** @46K | 4,044 @46K 1-stream, 8,085 @4 streams | — | GSM8K-200 **97.5 %** (card headline) / 98.5 % (config table); HumanEval 91.5 % |
| [satindergrewal EXL3 2.0 bpw + MXINT4 Engram](https://github.com/satindergrewal/DeepSeek-v4.1-Flash-EXL3-2.0bpw-Ablit-EngramQ4-SM120-Dual-RTX-Pro-6000) | 2×, TP2 over PCIe, vLLM `0.1.dev20904` + `vllm_exl3` | **89.4** tok/s @2K 1-stream; **16.6 agg (2.1/stream)** at 8×46K | 1,622–1,840 @46K | warm 12.7K prefix **5.85 s → 0.53–0.67 s (8–11×)** | GSM8K **96.0 % (48/50)**; Engram 189.1 GiB → **97.6 GiB** |

### 3.6 Why estimate and measurement differ

| Gap | Size | Explanation |
|---|---|---|
| Achieved MBU 0.22–0.34 vs the 0.50–0.65 sm_120 band | ~2× | `gpus/rtx6000-pro.md` §9b derives ~0.26 for a small-expert MoE on this card independently. Attributable causes, none isolated: **no DeepGEMM on sm_120**, so the fused Mega-Gate/Mega-mHC/Mega-MoE kernels DeepSeek ships never run; the **99 KB shared-memory ceiling** (vs Hopper's ~228 KB) forces small tiles; SM80-era `mma.sync` with no warp specialisation; PCIe all-reduce on every TP step with no NVLink. ⚠️ no profile published. |
| Prefill MFU 0.044 vs the 0.20–0.35 FP4 band | ~6× | The encoder half is **not** GEMM-dominated at these lengths: at 32K the indexer's O(N²) scan plus selected attention is 56.1 TFLOP against a 0.52 PFLOP GEMM term (architecture.md §6.2), and the Engram module does **24 random single-row gathers per token per module**. The same GPU hits MFU 0.30–0.40 on a 3 B-active NVFP4 MoE with dense TRT-LLM kernels (`gpus/rtx6000-pro.md` §9e) — so this is the model, not the silicon. |
| Engram on NVMe vs RAM: prefill 7,439 vs 20,234 tok/s | **2.7×** | Direct A/B is not published on RTX PRO 6000, but the DGX Spark study isolates the same effect: node-local Engram reads cost **2.8 ms/step** vs 5.9–7.8 ms over NFS, and moving to local rows took code decode C1 from 52.4 → 73.8 tok/s (architecture.md §10.4). diffbot measured NVMe → pinned RAM (with CUDA graphs) as **2.16×** on 46K decode. |
| Stock vLLM 8-GPU 2.3–4.0 tok/s vs 740–830 on 4 GPUs | **~200×** | CUDA-graph capture, full stop. `gpus/rtx6000-pro.md` §11 gotcha 8 records the same cliff on the V4 sparse-MLA path: **5 tok/s without graphs, 30–35 with**. |
| κ falls 0.69 → 0.57 from 128K to 500K context | −17 % | Exactly the Full-mode indexer scan of architecture.md §6.3: it reads all 330 B/token of FP8 indexer K, every step, in four layers. At C8 × 500K that is ~1.3 GiB/step of pure scan traffic. |
| Measured 4.71M logical KV tokens vs 1,650 B/token architectural | 1.33× | Paged-allocation overhead (main pages 256 tokens, SWA pages 128). Consistent, and small enough to validate the whole KV model — see §1.2. |

---

## 4. Cost

Prices, per METHODOLOGY §6/§8, from
[`cross-cutting/cloud-pricing.md`](../../cross-cutting/cloud-pricing.md) **§5.10, RTX PRO
6000 Blackwell (Server Edition)** — never a neighbouring GPU's row:

| Tier | $/GPU-hour | Row cited |
|---|---:|---|
| **Cheapest reputable on-demand** | **$1.80** | Nebius, "RTX PRO 6000", 1–8 GPUs (§5.10, §4.3) ⚠️ the listing does not state which edition |
| Cheapest **SE-confirmed** on-demand | $1.85 | Hyperstack, "RTX PRO 6000 **SE**", 1–8 GPUs (§5.10, §4.5) — the only rate card that says SE |
| **Cheapest hyperscaler on-demand** | **$4.143** | AWS `g7e.48xlarge`, 8× RTX PRO 6000 Blackwell SE (§5.10, §3.1) |
| **1-year reserved** | **$1.30** | Hyperstack reserved, RTX PRO 6000 SE (§5.10, §9) |
| (context) GCP `g4-standard-384` on-demand / 3-yr CUD | $4.500 / $1.979 | §5.10, §3.2 |

`high ÷ low = 2.30×` for this GPU (cloud-pricing.md §5.13), so every figure below is a band,
not a point.

### 4.1 Cost per 1M tokens

All throughputs Server-Edition-equivalent. Input cost uses the fast-stack prefill
(19,278 tok/s at 4 GPU). Blended = METHODOLOGY §6 verbatim: **75 % input (50 % of it cached
at ~10 % cost) / 25 % output, at the S1 operating point.**

| Operating point | | agg tok/s | $/GPU-h | **$/1M output** | **$/1M input** | **blended $/1M** |
|---|---|---:|---:|---:|---:|---:|
| **S1, MEASURED-equiv: 4 GPU, C8, TPOT 10.8 ms** | `meas.`→`est.` | **740** | $1.80 | **2.701** | 0.1037 | **0.7181** |
| | | | $4.143 | **6.217** | 0.2388 | **1.6528** |
| | | | $1.30 | 1.951 | 0.0749 | 0.5186 |
| S1 est.: 4 GPU, C52 (largest batch at TPOT ≤ 50 ms) | `est.` | 1,042 | $1.80 | 1.919 | 0.1037 | 0.5225 |
| | | | $4.143 | 4.417 | 0.2388 | 1.2027 |
| | | | $1.30 | 1.386 | 0.0749 | 0.3774 |
| **S4 est.: 4 GPU, C256, no SLO** ⚠️ | `est.` | 3,019 | $1.80 | **0.662** | 0.1037 | **0.2084** |
| | | | $4.143 | **1.525** | 0.2388 | **0.4797** |
| | | | $1.30 | 0.478 | 0.0749 | 0.1505 |
| S4 est.: 8 GPU, C256 ⚠️⚠️ | `est.` | 6,038 | $1.80 | 0.662 | 0.1037 | 0.2084 |
| | | | $4.143 | 1.525 | 0.2388 | 0.4797 |

The 8-GPU row has the same per-token cost as 4 GPUs because the roofline scales linearly in
GPU count and so does the bill — there is no economy of scale here, only a latency win.

### 4.2 Effect of speculative decoding and prefix caching

- **DSpark is already priced in.** At accepted length 2.44–2.55 the output leg costs
  **0.39–0.41×** what it would at A = 1: the measured $2.701/M at $1.80 would be
  **$6.59–$6.89/M** without speculation. This is the single largest cost lever on this pair
  — larger than the choice of cloud. ⚠️ But see §6: vLLM #56892 reports the opposite
  acceptance on the stock 8-GPU stack, where DSpark *"only adds overhead."*
- **Prefix caching moves the input leg, which is already small.** At h = 90 % the input leg
  drops 10× but blended cost only falls **$0.7181 → $0.6796/M (−5.4 %)**, because the 75/25
  mix is dominated by the output term at these throughputs. On this pair prefix caching is a
  **TTFT** optimisation (6.80 s → 0.68 s at 128K), not a cost one. The diffbot run on this
  GPU measured **87 % hits on 18.5M real agentic prompt tokens**, so h = 90 % is the
  realistic column, not an aspirational one (serving-optimizations.md §1.5 reaches the same
  conclusion from OpenRouter's 92 % on K3 traffic).
- **Engram in RAM vs NVMe** is a 2.7× input-cost lever: the NVMe stack's input cost is
  **$0.2953/M at $1.80**, 2.8× the RAM stack's $0.1037/M. 188.83 GiB of host RAM is cheaper
  than that gap.

### 4.3 Versus the vendor API, and break-even

DeepSeek first-party `deepseek-flash`, off-peak (architecture.md §11):
**output $0.60/M, input cache-miss $0.15/M, cache-hit $0.003/M.** Peak is exactly 2×.

| | Self-host, best measured (4 GPU, C8, $1.80) | Self-host, best `est.` (4 GPU, C256, $1.80) | **DeepSeek API off-peak** | API peak |
|---|---:|---:|---:|---:|
| $/1M output | $2.701 | $0.662 | **$0.60** | $1.20 |
| $/1M input (uncached) | $0.104 | $0.104 | $0.15 | $0.30 |
| $/1M input (cached) | ~$0.010 | ~$0.010 | **$0.003** | $0.006 |
| **blended 75/25** | **$0.7181** | **$0.2084** | **$0.2074** | $0.4147 |

**Input is the one leg where self-hosting wins**: $0.104/M against the API's $0.15/M, because
CED makes a prompt token cost half a decode token in FLOPs (architecture.md §6.1). Output is
where it loses, by 4.5× at the measured point.

Break-even output throughput to match $0.60/M
(`cost_per_hour / 0.00216`, architecture.md §11.2):

| Config | $/hr | Aggregate tok/s needed | Per GPU | Best measured (740 tok/s on 4 GPU) |
|---|---:|---:|---:|---:|
| 4 GPU @ $1.80 | $7.20 | **3,333** | 833 | **22 %** of break-even |
| 4 GPU @ $4.143 | $16.57 | 7,672 | 1,918 | 10 % |
| 4 GPU @ $1.30 reserved | $5.20 | 2,407 | 602 | 31 % |
| 8 GPU @ $1.80 | $14.40 | 6,667 | 833 | — |
| 8 GPU @ $1.30 reserved | $10.40 | 4,815 | 602 | — |

**Break-even utilisation vs the API on the blended mix** (self-host must be ≤ $0.2074/M):

| Operating point | @$1.80 | @$1.30 reserved |
|---|---|---|
| S1 measured, 4 GPU C8 ($0.7181 / $0.5186) | **never** — 3.5× the API at 100 % utilisation | **never** — 2.5× |
| S1 est., 4 GPU C52 ($0.5225 / $0.3774) | **never** — 2.5× | **never** — 1.8× |
| S4 est., 4 GPU C256 ($0.2084 / $0.1505) ⚠️ | break-even at **~100 %** utilisation | break-even at **~73 %** utilisation |

**Read that last row carefully.** It says that the *only* way self-hosting this model on this
GPU matches DeepSeek's own list price is to run a reserved-price fleet at 73 % sustained
utilisation, at a concurrency **32× higher than anything anyone has measured**, with no SLO
on TPOT. At the concurrency that is actually measured and actually meets an interactive SLO,
self-hosting costs **2.5–3.5× the API** at 100 % utilisation — i.e. it never breaks even.
The reasons to run it here are sovereignty, data residency, or the vision path — not price.

---

## 5. Scaling and deployment shape

### 5.1 Single node vs multi-node

**Single node, 4 GPUs, always.** Eight GPUs in one chassis is arithmetically better (§1) and
is what vLLM's V4-**Flash** sibling recipe verifies (`rtx_pro_6000_8x`: *"Single-node TP8 +
expert parallelism … verified on 8× RTX PRO 6000 (PCIe, no NVLink)"*), but for **V4.1** the
only 8-GPU datapoint is the 2.3–4.0 tok/s eager-mode collapse. Two chassis is ruled out by
`gpus/rtx6000-pro.md` §4.

Practical chassis constraint worth planning for: Lenovo qualifies the passively-cooled Server
Edition at **2× at 600 W or 4× at a 450 W cap** in the SR675 V3 line — *"8× in a server is a
general-market configuration, not a universally-qualified one"* (`gpus/rtx6000-pro.md` §11
gotcha 12). The 0xSero run caps at **275 W per card** and still reaches 729 tok/s at C8,
which is consistent with §8d's finding that decode-bound work is nearly power-insensitive on
this card.

### 5.2 When PD disaggregation or wide-EP pays — it doesn't, here

- **PD disaggregation: don't.** vLLM declares `"pd_cluster": {"rtx_pro_6000_8x":
  "unsupported"}` for the V4-Flash sibling; there is no V4.1 profile at all.
  serving-optimizations.md §3.5 quotes Modular's **20–30 % performance drop from
  disaggregation on small or untuned workloads** and the rule *"disaggregation is a
  thousand-GPU problem."* At 4–8 PCIe GPUs with no NVLink and no NIXL-class fabric, the KV
  transfer cost is paid on every request and the throughput gain does not materialise.
- **Wide-EP: doesn't apply.** EPLB/DeepEP target NVL72-scale expert parallelism. At EP4 the
  384 experts split 96-per-rank, which is already past the point where expert-read
  saturation matters (architecture.md §6.3: batch 32 touches only 40 % of experts).
- **What does pay**, in order: (1) **CUDA graphs on decode** (~200×), (2) **Engram in pinned
  host RAM rather than NVMe** (2.7× prefill, 2.16× decode in the diffbot A/B), (3) **DSpark**
  (~2.5×), (4) **prefix caching** (8–11× warm TTFT).

### 5.3 Launch commands

**A. The measured 4-GPU vLLM/B12X stack**, quoted verbatim from
[local-inference-lab/rtx6kpro](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/deepseek-v4.1-flash.md):

```bash
IMAGE=ghcr.io/local-inference-lab/vllm:karmic-kraken-beta
docker pull "$IMAGE"
docker run -d --name ds41 --init --restart unless-stopped \
  --gpus '"device=0,1,2,3"' --network host --ipc host --shm-size 32g \
  --ulimit memlock=-1 --security-opt seccomp=unconfined \
  -v lil-huggingface:/root/.cache/huggingface -v ds41-runtime:/cache \
  -e PROFILE=ds41-flash -e HARDWARE_PROFILE=rtx-pro-6000-pcie \
  -e TP=4 -e PORT=8000 "$IMAGE"
```

Add `--engram-table-memory ram` after `"$IMAGE"` for the 188.83 GiB pinned-RAM placement
(the default is `disk` via io_uring). Their profile defaults, verbatim: *"TP4/DCP1, DSpark K7
with adaptive verification … B12X attention, MoE and dense; B12X/NCCL communication …
Scheduler 4096 tokens, 32 sequences, one prefill lane … Main / sliding-window pages 256 /
128 tokens … Full-and-piecewise decode graphs, cap 128; breakable prefill off … Prefix cache
enabled."*

**B. The measured 4-GPU SGLang/NVMe stack**, from
[0xSero](https://github.com/0xSero/deepseek-v4.1-flash-4x-rtx-pro-6000):

```bash
git clone https://github.com/0xSero/deepseek-v4.1-flash-4x-rtx-pro-6000.git
cd deepseek-v4.1-flash-4x-rtx-pro-6000 && mkdir -p models state
MEMORY_FRACTION=0.95 MAX_TOTAL_TOKENS=4200000 \
CONTEXT_LENGTH=524288 MAX_RUNNING_REQUESTS=8 \
docker compose up --build -d
```

with `OFFLOAD_MODE=nvme`, `DSV41_CACHE_GIB=64`, DSpark block size 5, prefill chunk 2,048.
Their warning, verbatim: *"A previous 4,096-token chunk configuration failed near 399k input,
so configuration availability is not a stability claim."*

**C. The nearest *official* vLLM command — for the V4-Flash sibling, not this model**,
verbatim from
[`recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash/hw/rtx_pro_6000_8x.json`](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash/hw/rtx_pro_6000_8x.json)
(image `vllm/vllm-openai:nightly`, `VLLM_USE_RUST_FRONTEND=1`):

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
  --reasoning-config '{"reasoning_parser":"deepseek_v4","reasoning_start_str":"","reasoning_end_str":""}' \
  --speculative-config '{"method":"dspark","num_speculative_tokens":7,"draft_sample_method":"probabilistic"}'
```

with the recipe's own sm_120 caveats, verbatim: *"sm_120 cannot use the SM100 FP4 indexer
cache or `deep_gemm_mega_moe`. The generated command already sets
`--attention_config.use_fp4_indexer_cache False` and `--moe-backend auto`. Do not use
`method: mtp` on 0731 — that checkpoint has no MTP head."* For V4.1 substitute
`--tokenizer-mode deepseek_v41`, the `deepseek_v41` parsers, and
`"num_speculative_tokens":5` (`dspark_block_size: 5`, architecture.md §2.8). **Untested as
written** ⚠️.

### 5.4 Warm-up and loading time

| Path | 5 GB/s NVMe | 12 GB/s | 20 GB/s |
|---|---:|---:|---:|
| Full 510.29 GB checkpoint | 1.70 min | 0.71 min | 0.43 min |
| Compute weights only (307.53 GB) | 1.03 min | 0.43 min | 0.26 min |
| Engram 202.76 GB → pinned host RAM | 0.68 min | 0.28 min | 0.17 min |

PCIe Gen5 ×16 host→GPU is **not** the bottleneck: 307.53 GB over 4 links at 64 GB/s is a
**1.2 s** floor. Real wall-clock is dominated by NVMe reads, safetensors parsing and kernel
JIT. Set `VLLM_ENGINE_READY_TIMEOUT_S=3600` — *"Expect a long first load"*
(architecture.md §8.3) — and expect **JIT compilation on first boot** on top of the transfer,
since `gpus/rtx6000-pro.md` §1 records that CUDA toolkits < 13.2 with glibc ≥ 2.41 fail to
compile JIT kernels at all. 0xSero budgets *"about 510 GB of checkpoint storage plus
container/cache headroom"* and requires a filesystem supporting direct I/O for NVMe mode.

Steady-state Engram traffic once loaded: **12,672 B/token/seq** = 99 KiB/step at C8, but as
**384 random 264-byte gathers per step**. Trivial in bytes, pathological in pattern — which
is why RAM beats NVMe by 2.7× on prefill.

---

## 6. Risks and open questions

**⚠️ TO BE VERIFIED — kernel and engine**

1. **No official engine profile exists.** `recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/rtx_pro_6000.json` is **HTTP 404** and the hardware map has no RTX entry (re-probed 2026-09-19). Every measurement in §3.5 is a community build. There is no vendor commitment behind any of it.
2. **MXFP4 native W4A8 vs Marlin W4A16 dequant.** METHODOLOGY §8 and `gpus/rtx6000-pro.md` §9g say Marlin; `quantization-formats.md` §4.2/§9.6 say native `b12x_fused_moe`. Resolved *for the measured builds* in §2, but **no A/B on this model on this card is published**. If it is really Marlin, the FP4 storage win is kept and the FLOPs win is lost — which would explain part of the 0.044 prefill MFU.
3. **Block-scaled FP8 (32×32 UE8M0) on sm_120.** TRT-LLM's sm120 row omits it; DeepGEMM is unsupported (`arch_major=12`); SGLang has three open issues tuning it (#39978, #39872, #39065). ~7 GB of the checkpoint and all of the attention path depends on it.
4. **`index_topk: 512` on the SM120 sparse-MLA decode kernel.** vLLM's V4-Flash sm_120 profile requires a FlashInfer *"that instantiates the SM120 sparse-MLA decode kernel for `topk=192`"*. V4.1 needs 512. FlashInfer PR #4955 lists *"primary top-k 128 or 512"*, so it should be covered — not confirmed end-to-end.
5. **NVFP4 KV (the shipped 890 B/token cache).** The kernel exists on SM120/121 since FlashInfer PR #4955 (merged 2026-09-07, measured on an RTX PRO **5000**), but vLLM's SM12x dtype gate still requires FP8. Re-check before repeating "RTX PRO 6000 cannot do NVFP4 KV" — it is engine-version-dependent now. Upside: 1.85× KV capacity.
6. **CUDA-graph capture on SM120 for V4.1.** [vLLM #56892](https://github.com/vllm-project/vllm/issues/56892) is **open with no maintainer response**. Without graphs this pair is unusable (2.3–4.0 tok/s). Every measurement in §3.5 comes from a build that patched around this; none of those patches is upstream.
7. **The `--gpu-memory-utilization` cliff.** 0xSero: *"Uncapped 0.93 memory fraction: allocated 7.62M logical slots; first generation failed on temporary attention-buffer allocation … reserved slots were not usable capacity."* KV capacity reported at boot is not KV capacity you can use.

**⚠️ TO BE VERIFIED — measurement provenance**

8. **Every measurement is on the wrong SKU.** Workstation (1,792 GB/s) or Max-Q; none on the Server Edition (1,597 GB/s). Every SE figure here is scaled, `est.`, and unconfirmed.
9. **DSpark acceptance is reported three incompatible ways.** 0xSero: medians **74.1–88.4 %** (a *rate*). local-inference-lab: C1 acceptance **2.443–2.551** (a *length* out of 5 ≈ 49–51 %). vLLM #56892: *"~72–87 % of drafted tokens are **rejected**"* — the exact inverse of 0xSero. These cannot all be the same quantity. The §3 calibration assumes length 2.44–2.55; if the true length at C8 is nearer 4.0, the achieved MBU is nearer 0.17 and the estimates above are optimistic. Also recall METHODOLOGY's warning: the widely-quoted **3.51 is a synthetic benchmark constant**, not a measurement (architecture.md §7.3).
10. **No measurement above concurrency 8 exists for this pair.** Both sweeps cap at C8. The entire C32–C256 region of §3.2/§3.3, the S4 operating point, and every cost figure derived from it rest on extrapolating κ by a factor of 32. Engram gathers scale linearly with batch (12,288 random reads/step at C256), which the roofline does not model.
11. **No 8-GPU working measurement exists.** §3.3 is pure extrapolation from 4-GPU κ.
12. **The 2,189 B/token paged overhead** is inferred from one auto-sized allocation, not measured directly.

**⚠️ TO BE VERIFIED — accuracy of the executed precision**

13. **No task-level eval of the native checkpoint on this GPU.** local-inference-lab publishes a 187-test tokenizer/frontend/parser suite and image checks, explicitly *"not general model-quality or repetition tests."* 0xSero passes arithmetic, JSON schema and a tool round trip, and states *"Broader quality testing remains pending"* and *"Long-context answer quality remains unqualified."* The nearest GSM8K numbers on this GPU are for **2-bpw EXL3 requantisations** (97.5–98.5 % and 96.0 %), not the native checkpoint.
14. **MXFP4 accuracy is DeepSeek's own choice, and is fine** — the tech report adopts OCP MXFP4 *"to support as many hardware platforms as possible, despite the higher accuracy of alternative formats in our experiments"* (architecture.md §1.2). The risk here is not the format but whether the sm_120 kernel implements it correctly; `gpus/rtx6000-pro.md` §6a documents an sm_120 FP4 MoE path that **generated coherent-looking garbage** for months.
15. **SWA Bounded Replay** has no published accuracy delta on any hardware (architecture.md §12.2), and this model's CED decoder depends on it.
16. **Video is broken.** 0xSero's six-frame temporal-colour test **failed**: *"Do not treat video as supported by this release."* Multi-image reliability is unresolved. Single-image at 1,024 tokens passes.

**Known engine issues inherited from the GPU**

17. **NCCL hangs on the first collective** unless IOMMU and ACS are disabled in BIOS (`gpus/rtx6000-pro.md` §4, §11 gotcha 10). Fix this before collecting any multi-GPU number.
18. **99 KB shared-memory ceiling** kills every kernel tuned against Hopper/B200's ~228 KB — TileLang's DSA sparse-MLA needs 202 KB and dies.
19. **LMCache PyPI wheels carry no sm_120 cubins and no PTX** → `cudaErrorNoKernelImageForDevice`; build from source if you want KV offload (`gpus/rtx6000-pro.md` §7).
20. **GPU memory is frequently not released on a failed start** — `ps aux | grep vllm | xargs kill -9`.

**Nearest runnable alternatives, if the above is too much risk**

| Alternative | Where it lands |
|---|---|
| **2× RTX PRO 6000 + EXL3 2.0 bpw** (diffbot) | Fits 2 cards, 113 tok/s single-stream, GSM8K-200 97.5 %, 358 GB download. Half the hardware, a requantisation, and a real 40-min agentic run behind it. |
| **2× + EXL3 2.0 bpw + MXINT4 Engram** (satindergrewal) | Engram 189.1 → 97.6 GiB, GSM8K 96.0 %, 1M context, 89.4 tok/s. Best fit-per-card. |
| **The V4-Flash sibling** (`deepseek-ai/DeepSeek-V4-Flash-0731`, 284B/13B) | The **only** DeepSeek model with an *official* `rtx_pro_6000_8x` **verified** vLLM profile, ~2,452 tok/s aggregate on 8 cards. If you need a vendor-supported DeepSeek on this GPU today, this is it — not V4.1. |
| **Move the model, not the GPU** | 4× B300 or a GB300 tray runs V4.1-Flash with the native FP4 KV (890 B/token), DeepGEMM, FlashMLA mega-attn and vendor-verified recipes. Every one of risks 1–6 disappears. |

---

## Sources

**This research tree**
- [`research/METHODOLOGY.md`](../../METHODOLOGY.md) §1–§8
- [`research/models/deepseek41f/architecture.md`](./architecture.md) §1.2, §2.1, §2.7, §2.8, §3.1, §3.3, §3.4, §4.1, §5.1–§5.4, §6.1–§6.3, §7.1–§7.3, §8.1–§8.7, §9, §10.2, §10.4, §11, §12
- [`research/gpus/rtx6000-pro.md`](../../gpus/rtx6000-pro.md) §1, §2, §3c, §4, §5c, §5d, §6, §6a, §7, §8a, §9b, §9e, §9g, §11
- [`research/cross-cutting/flash-attention.md`](../../cross-cutting/flash-attention.md) §3.1, §3.2, §6.2, §9.5, §9.7, §11
- [`research/cross-cutting/quantization-formats.md`](../../cross-cutting/quantization-formats.md) §4.2, §5.1, §8.1, §9.6
- [`research/cross-cutting/inference-engines.md`](../../cross-cutting/inference-engines.md) §3.8, §6.1
- [`research/cross-cutting/serving-optimizations.md`](../../cross-cutting/serving-optimizations.md) §1.2, §1.5, §3.5, §7.1
- [`research/cross-cutting/cloud-pricing.md`](../../cross-cutting/cloud-pricing.md) §3.1, §3.2, §4.3, §4.5, §5.10, §5.13, §9

**Primary, fetched 2026-09-19**
- https://github.com/local-inference-lab/rtx6kpro/blob/master/models/deepseek-v4.1-flash.md
- https://github.com/0xSero/deepseek-v4.1-flash-4x-rtx-pro-6000
- https://github.com/vllm-project/vllm/issues/56892
- https://github.com/satindergrewal/DeepSeek-v4.1-Flash-EXL3-2.0bpw-Ablit-EngramQ4-SM120-Dual-RTX-Pro-6000
- https://huggingface.co/diffbot/DeepSeek-V4.1-Flash-EXL3-2.0bpw-2x-RTX-PRO-6000
- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash.json (hardware map: no RTX entry)
- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/rtx_pro_6000.json (**HTTP 404**)
- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash.json (`rtx_pro_6000_8x: "verified"`; `pd_cluster: unsupported`)
- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash/hw/rtx_pro_6000_8x.json
- https://github.com/flashinfer-ai/flashinfer/pull/4955 (NVFP4 sparse MLA for SM120, merged 2026-09-07)
- https://github.com/flashinfer-ai/flashinfer/issues/2847 · https://github.com/NVIDIA/cutlass/issues/3096 · https://github.com/vllm-project/vllm/issues/31085
- https://api-docs.deepseek.com/quick_start/pricing
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash and its tech report

**Explicitly searched for and not found (2026-09-19)**
- MLPerf Inference: no RTX PRO 6000 submission for any DeepSeek model.
- InferenceMAX / InferenceX: no RTX PRO 6000 rows for DeepSeek-V4.1-Flash (per METHODOLOGY §8, the card has Qwen3.5 rows only).
- Any NVIDIA, DeepSeek or vLLM **vendor** benchmark of this pair.

---

*All arithmetic in this document was produced by `python3` per METHODOLOGY §4/§6; the script
and its raw output are reproducible from the inputs cited in each table. 20 items are marked
**⚠️ TO BE VERIFIED**.*

---

## Audit log (2026-09-19)

Numerical audit against `research/METHODOLOGY.md`, `models/deepseek41f/architecture.md`
§3–§6, `gpus/rtx6000-pro.md` §2/§3/§8/§9, and `cross-cutting/cloud-pricing.md` §5.10, every
table recomputed independently with `python3`.

**Recomputed and confirmed correct (no change):** §1.1 fit table (weights/GPU, KV budget,
aggregate, all 10 rows, both 0.90/0.95); §1.2 max-concurrency table (all 12 rows × 4 contexts
× TP/DP, both HBM and host-offload Engram placements) and its per-sequence KV-cost table; the
"validation" cross-check of implied B/token against reported logical KV tokens (2,189 / 2,457
/ 2,539, all ratios); §3.1's κ/MBU calibration table (roofline, κ, achieved-MBU, and
Server-Edition-equivalent columns for all 6 measured points) and the prefill-MFU table (both
rows, all three dtype denominators); §3.2/§3.3 TPOT-from-aggregate-tok/s arithmetic and GB/step
scaling; §3.4 TTFT (both stacks, all 3 scenarios, 0 %/90 % hit); §3.6's stated ratios (κ drop
17 %, 1.33× paged overhead, 2.7× Engram RAM/NVMe factor); §4.1 cost-per-1M-output,
cost-per-1M-input and blended-cost arithmetic at all three price tiers and all four operating
points (formula `cost = n_gpus × price / (tok/s × 3600) × 1e6`, METHODOLOGY §6); the §4.3
DeepSeek-API blended figures ($0.2074 off-peak / $0.4147 peak, correctly using the model's own
2 % cache-hit ratio per architecture.md §11.1, not METHODOLOGY's generic 10 %); the §4.3
break-even-throughput table (`cost_per_hour / 0.00216`) and the break-even-utilisation ratios;
the DSpark cost-lever arithmetic (0.39–0.41× / $6.59–$6.89 without speculation); §5.4 warm-up
times at all three NVMe speeds; the §0 verdict cost ranges against their source tables. Prices
match `cloud-pricing.md` §5.10 exactly (Nebius $1.80, Hyperstack SE $1.85/$1.30 reserved, AWS
$4.143, GCP $4.500/$1.979). KV bytes/token (890 FP4 / 1,650 FP8 / 3,200 BF16), weight bytes
(510.29 GB checkpoint, 202.76 GB Engram, 307.53 GB compute weights) and active-param FLOPs all
trace to `architecture.md` §3–§6 without discrepancy.

**Fixed: METHODOLOGY §3 consistency-rule violation (KV-infeasible rows printed as numbers).**
§1.2 states the per-scenario `max_concurrency` at 4 GPUs (host Engram, FP8 KV, TP-replicated)
is 336 (S1/8K), 96 (S2/32K), 25 (S3/128K), and at 8 GPUs is 2,615 / 754 / 196 — and
METHODOLOGY §3 requires any batch row above that cap to print as `infeasible (KV)`, never as
a throughput number. The decode tables in §3.2/§3.3 didn't apply this consistently: S3 rows at
batch 32 and 64 (32 and 64 both > the 25-sequence cap) and S2 rows at batch 128 and 256 (both
> the 96-sequence cap) showed fabricated throughput/TPOT figures instead of `infeasible (KV)`,
even though the prose immediately above the §3.2 table (and again in the paragraph below it)
already states these caps and that C51 exceeds them at S3. In the §3.3 8-GPU table, the batch
256 / S3 row (256 > the 196 cap) had the same defect. Fixed by replacing those 5 rows'
throughput/TPOT/GB-step cells with `infeasible (KV)`, matching the pattern already used
correctly for S3 batch 128/256 in §3.2. No other table depends on the removed numbers (the
cost tables in §4 only use S1 at C8/C52/C256, all of which are within their caps), so this
does not change any figure in §0 or §4.

**Checked, left as-is (< 5 % threshold):** §4.2's NVMe-stack input-cost figure ($0.2953/M at
$1.80) computes to $0.2822/M (−4.6 %) when derived from the §3.4-stated Server-Edition NVMe
prefill rate (7,087 tok/s) with the document's own cost formula; the gap is most likely the
choice of underlying raw measurement (the 0xSero C8/2,048-input raw prefill of 6,772.6 tok/s
reproduces $0.2953 almost exactly, vs the C1/32K-input raw prefill of 7,438.7 that scales to
the 7,087 figure used in §3.4) rather than an arithmetic slip, and the deviation is under the
task's 5 % fix threshold, so left unchanged.

**Contradictions with foundation docs:** none found beyond what the document itself already
states and resolves explicitly (per METHODOLOGY §8) — the MXFP4-native-vs-Marlin-W4A16
disagreement with METHODOLOGY §8 / `gpus/rtx6000-pro.md` §9g (§2 disagreement box) and the
4-GPU-vs-8-GPU-minimum correction to `gpus/rtx6000-pro.md` §9g (§1.1). Both are cited, both
sides are named, and neither is silently picked — no action needed.
