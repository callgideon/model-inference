# Qwen/Qwen3.8-27B — GPU selection guide

**Research date: 2026-09-19.** Consolidates [`architecture.md`](./architecture.md) and the eight
per-GPU pair documents ([h100](./h100.md), [h200](./h200.md), [b200](./b200.md), [b300](./b300.md),
[gb300](./gb300.md), [a100](./a100.md), [rtx6000-pro](./rtx6000-pro.md), [mi355x](./mi355x.md))
under [`research/METHODOLOGY.md`](../../METHODOLOGY.md). Every number below is carried from one of
those documents and linked to it; nothing new is derived here. Legend as in METHODOLOGY:
`est.` derived, `meas.` published measurement, **⚠️** unverified.

## Summary

1. **What it needs.** 27.78 B dense params (55.56 GB BF16 / 30.87 FP8 / 21.92 NVFP4 / 19.80 MXFP4 / 19.45 INT4), **64 KiB/token KV at BF16 or 32 KiB at FP8 from only 16 of 64 layers**, plus a **fixed 78.45 MB (bf16) recurrent-state slot** for the 48 Gated-DeltaNet layers × **`S` = 5 slots per running request** (SGLang's default) = **392.2 MB per request**, the [METHODOLOGY §8](../../METHODOLOGY.md) pin ([architecture.md §5](./architecture.md); 153.94 MB fp32 per slot is the alternate, `S` = 1 only with `--disable-radix-cache`).
2. **It fits one GPU everywhere.** Minimum is 1 GPU in every precision on every card in this survey; TP is only ever a KV-headroom or latency instrument, and multi-node fabric is never needed ([b300 §1.2](./b300.md), [gb300 §1.3](./gb300.md), [h200 §1.5](./h200.md)).
3. **What can run it today.** Vendor-verified: **H200** (SGLang v0.5.19), **GB300** (vLLM + SGLang, all five checkpoints), **RTX PRO 6000 Blackwell** (both engines, GSM8K 94.01–95.00 %). Supported-but-unverified: **H100, B200, B300, MI355X**. Unlisted by anyone and ⚠️ unproven end-to-end: **A100** ([h200 §0](./h200.md), [gb300 §0](./gb300.md), [rtx6000-pro §0](./rtx6000-pro.md), [b300 §0](./b300.md), [a100 §0](./a100.md)).
4. **Best for interactive serving: B300 (HGX, 268 GB), TP1, NVFP4 + FP8 KV + bf16 state** — 12,463 tok/s/GPU at **20.5 ms TPOT** and **$0.165–0.334 per 1M output**, holding 396 concurrent 4.6 K sequences; it is also this repo's primary node ([b300 §3.2, §4](./b300.md)).
5. **Best cost per token: B200 NVFP4 at $0.150 per 1M output** (bs 242, TPOT 21.8 ms, $6.00/GPU-hr Hyperstack) — with **B300 within 2 % at $0.153** and *cheaper blended* ($0.060 vs $0.063), so on the repo's own hardware there is no reason to buy B200 for this model ([b200 §4.1](./b200.md), [b300 §4](./b300.md)).
6. **Cheapest viable: RTX PRO 6000 Blackwell Server Edition, $0.192–0.441 per 1M output** at $1.80–4.14/GPU-hr — one 96 GB PCIe card, verified on both engines, 2,610 tok/s/GPU. A100 rents cheaper per hour but lands at $0.218 and is unverified ([rtx6000-pro §4.2](./rtx6000-pro.md), [a100 §4.2](./a100.md)).
7. **The binding constraint is capacity, not latency, on every GPU.** On H200, B200, B300, GB300, MI355X and A100 the KV+state pool runs out *before* the 50 ms TPOT budget does, so the interactive and max-throughput operating points nearly coincide ([h200 §4.2](./h200.md), [gb300 §3.3](./gb300.md), [mi355x §3.4](./mi355x.md), [a100 §4.2](./a100.md)).
8. **Two flags move more than any GPU choice does:** `--mamba-ssm-dtype bfloat16` (+57–60 % concurrency at 8 K, and on SM100/SM103 it is the *gate* on the FlashInfer GDN prefill kernel) and `--kv-cache-dtype fp8_e4m3` (+40 % concurrency) ([h100 §1.3](./h100.md), [b300 §1.1](./b300.md), [rtx6000-pro §1.3](./rtx6000-pro.md)).
9. **What is not runnable, and why:** MXFP4 on any NVIDIA GPU (*"does not load on Nvidia devices"*); INT8/W8A8 on B300/GB300 (no `tcgen05.mma .kind::i8`; vLLM hard-errors at the first forward pass); NVFP4 on A100 (`no kernel image`) and its FP8 checkpoint likely crashes at boot ([vllm#55008](https://github.com/vllm-project/vllm/issues/55008)); FA3 on Blackwell, FA4 on sm_120, FlashInfer full-attention for hybrid-GDN on SM100/SM103; TRT-LLM for multi-turn (KV cache reuse = **No**); wide-EP (no experts) and PD disaggregation (no published layout, and ⚠️ it is unknown whether the GDN state survives a NIXL handover).
10. **Confidence is `estimate` on all eight pairs.** **No throughput, TPOT or TTFT measurement of this model on any datacentre GPU exists anywhere** — not in MLPerf, not in InferenceX (`200 []`), not in either engine's cookbook. Everything published is single-stream or ≤ 8-concurrent consumer/workstation Blackwell ([architecture.md §10.1](./architecture.md)).

## Cross-GPU comparison

All rows: **TP1** unless stated, FP8 KV, **bf16** GDN state, `S` = 5 (SGLang's shipped default),
scenario S1 = 4 K in / 512 out. `$/1M` low–high are the `cloud-pricing.md` §5.14 planning tiers
cited by each pair doc. Concurrency columns are `max_concurrency(ctx)` per METHODOLOGY §3.

| GPU | Runnable today (engine) | Min GPUs | Recommended | Executed weight format | Attention kernel | Max conc @8K | @128K | Interactive tok/s/GPU · TPOT | Max-tput tok/s/GPU | $/1M out interactive | $/1M out max-tput | Blended $/1M | Conf. |
|---|---|---:|---|---|---|---:|---:|---|---:|---|---|---|---|
| **[B300 HGX 268 GB](./b300.md)** | vLLM 0.29.0 `hw/b300.json`; **not** in verified set; SGLang no B300 cell (GB300 = same sm_103 **is** verified) | 1 | **8 × TP1 replicas/node** | **NVFP4 W4A4** (MLPs + `lm_head`) + FP8 (attn/GDN) + BF16 (ViT, MTP, norms) | **`triton` or `fa4`** — `trtllm_mha` hangs on SM103, FlashInfer illegal for hybrid-GDN | **325** | **45** | **12,463 · 20.5 ms** (bs 256) | 13,461 (bs 384) | **$0.165–0.334** | **$0.153–0.309** | **$0.060–0.122** | est. |
| **[GB300 NVL72 279 GB](./gb300.md)** | vLLM **`"gb300": "verified"`** TP1; SGLang v0.5.19 **verified, all 5 checkpoints** (DFlash2 `in-progress`) | 1 | 1 per replica; 72 replicas/rack | NVFP4 W4A4 + FP8 | FA4 hd256 2-CTA (or `triton`); **not** FlashInfer | **340** | **48** | 14,411 · 28.7 ms (bs 414) | 14,411 (same point) | $0.347 | $0.347 | $0.134 | est. |
| **[B200 HGX 180 GB](./b200.md)** | vLLM 0.29.0 `hw/b200.json`, `b200` in fp8/nvfp4/int4; **not** verified; SGLang no B200 cell | 1 | 8 × TP1 replicas/node | NVFP4 W4A4 (`Inferact`, 26.38 GB is the b200-blessed build) | **`trtllm_mha`** — FA4 hd256 ⚠️ broken for this family on SGLang v0.5.19 | 199 (205 at the 21.92 GB pin) | 28 | 8,989 · 14.2 ms (bs 128) | 11,082 (bs 242) | $0.185–0.433 | **$0.150–0.351** | $0.063–0.148 | est. |
| **[MI355X 288 GB](./mi355x.md)** | vLLM *generated* `hw/mi355x.json` TP1, `vllm/vllm-openai-rocm:latest`; **not** verified; SGLang has **no AMD cell** | 1 | 8 × TP1 replicas/node | **MXFP4 W4A4** — `amd/Qwen3.8-27B-Quark-AWQ-MXFP4`, **19.798 GB** `meas.` | **CK FlashAttention-2** — AITER's tuned hd256 ASM kernel is MHA-only, GQA falls back | **356** | **50** | 11,312 · 38.3 ms (bs 433) | 12,225 (bs 1,025, `S`=1) | $0.211 (one price) | $0.195 | **$0.080** | est. (±10 %) |
| **[H200 SXM 141 GB](./h200.md)** | **SGLang v0.5.19 `verified`** (only datacentre-Hopper cell run by anyone) + vLLM `hw/h200.json` TP1 | 1 | **TP1 × 8 replicas**; TP4 × 2 for throughput ⚠️ | FP8 E4M3 block-128 | FlashInfer (`fa3` documented alternative) | 138 | 19 | 6,953 · 24.2 ms (bs 168) · derated 4,867 · 34.5 ms | 6,953 (same point) | $0.159–0.452 | $0.159–0.452 | $0.082–0.196 | est. |
| **[H100 SXM 80 GB](./h100.md)** | vLLM `hw/h100.json` + `h100` in fp8/int4; **no verified cell on either engine** | 1 | **2 × TP2** (4 × TP2 per node) | FP8 E4M3 block-128 | **FA3** | 56 (TP1) · 159 (TP2) | 7 (TP1) · 21 (TP2) | 4,027 · 15.9 ms (TP2, bs 128) | 4,739 (TP2, bs 193) | $0.221–0.475 | $0.188–0.403 | $0.089–0.192 | est. |
| **[RTX PRO 6000 SE 96 GB](./rtx6000-pro.md)** | **vLLM `verified`** (`nvfp4_nvidia`) + **SGLang ≥0.5.9 verified**, all 5 checkpoints | 1 | **1, TP1** — TP4 over PCIe measured 6–7 tok/s | NVFP4 W4A4, natively (dense model dodges the sm_120 MoE-GEMM bug) | FlashInfer FA2-class; `triton` fallback | 91 | 12 | 2,051 · 31.2 ms (bs 64) | 2,610 (bs 111) | $0.244–0.561 | $0.192–0.441 | $0.087–0.199 | est. (bs=1-calibrated) |
| **[A100 SXM 80 GB](./a100.md)** | ⚠️ **nobody publishes a profile** — `hw/a100.json` 404, no vLLM variant lists `a100`, no SGLang cell | 1 | TP1 × 8 replicas | **INT4 W4A16 via Marlin** (BF16 is the only *native* format) | **FA2** (230 TFLOPS, 73 % of peak) | 73 | 10 | 2,030 · 45.3 ms (bs 92) | 2,030 (compute ceiling) | $0.218–0.469 | $0.218–0.469 | $0.126–0.271 | est. (1 % vs one sm_86 point) |

**Where the pair docs disagree with each other or with a foundation doc** (METHODOLOGY §8 and
`cloud-pricing.md` §5.14 are the tiebreakers):

| Disagreement | Resolution |
|---|---|
| **An MXFP4 checkpoint for this model.** [architecture.md §9.2](./architecture.md) and its open question 14 say *"no MXFP4 checkpoint exists"*. [mi355x §1.1](./mi355x.md) read the safetensors header of `amd/Qwen3.8-27B-Quark-AWQ-MXFP4` and reconciled **19,797,976,544 B** to the byte. | **mi355x.md is right.** The gap closed 2026-08-26. architecture.md §4's MXFP4 `est.` of 21.349 GB (which assumed the NVFP4 layer map) should be replaced with the measured 19.798 GB, and §8.4's "plan FP8 or Quark INT4 on MI355X" re-pointed at MXFP4. |
| **GPU-hour prices.** architecture.md §11.3 uses H200 $4.29 (Crusoe), RTX PRO 6000 $1.85 (Hyperstack), A100 $1.60 (Hyperstack). The pair docs use $3.99 (Hyperstack), $1.80 (Nebius), $1.59 (RunPod Secure). | **The pair docs are right** — `cloud-pricing.md` §5.14's `low` tier is the cheapest *reputable* on-demand rate by name. The deltas are 0.6–7 % and change no ranking; they make architecture.md's H200 and RTX PRO 6000 $/token ~7 % and ~3 % high. |
| **GB300/B300 prefill rate.** architecture.md §11.3 applies MFU to the *full* 15,000 / 13,500 FP4 peak (68,685 / 61,816 tok/s). [gb300 §3.1](./gb300.md) and [b300 §3.1](./b300.md) blend it: only **71.7 % of GEMM params are FP4**, the rest FP8 → effective 9,584 / 2,365 TFLOP/s, giving 43,501 / 44,671 tok/s. | **The pair docs are right for a mixed checkpoint** (METHODOLOGY §1: never apply one flat rate to a mixed build). It moves GB300's $/1M input from $0.073 to $0.115. It also caps B300's FP4 advantage over B200 at a **1.20× arithmetic ceiling**, matching the 1.05–1.17× measured on the same architecture family. |
| **B200 HBM bandwidth.** METHODOLOGY §8 / `gpus/b200.md` pin **7.7 TB/s**; `cloud-pricing.md` §2.1 tabulates 8.0. | METHODOLOGY §8 wins; [b200 §3.1](./b200.md) states the deviation and gives the ×1.039 conversion. |
| **A100 capacity basis.** METHODOLOGY §8 pins **80 GB decimal**; `gpus/a100.md` §2 works in 80 GiB (85.90 GB) and marks its own `nvidia-smi` figure ⚠️. | [a100 §1.0](./a100.md) plans on 80 GB decimal per METHODOLOGY. The 7.4 % difference moves 8 K INT4 concurrency 73 → 81. |
| **Which NVFP4 export on B200/B300.** METHODOLOGY §8 pins the 21.92 GB `nvidia`/`RadixArk` build; vLLM's recipe declares that repo's `supported_hardware` as `rtx_pro_6000` + `dgx_spark_gb10` only, and blesses **`Inferact` (26.38 GB)** for `b200`/`b300`. | Unresolved — **⚠️ open question**. [b200 §1.1](./b200.md) and [b300 §5](./b300.md) plan with `Inferact` (−3 % KV budget) and print the 21.92 GB row alongside. |
| **The ×0.7 derate.** architecture.md §10.2 prescribes *"derate ~30 %"* from one RTX 5090 point. | **Do not apply it blindly.** [a100 §3.4](./a100.md) shows the roofline is *conservative* on Ampere (exact at bs=1 once speculation is accounted for, 1.19× conservative at bs=64); [mi355x §3.2](./mi355x.md) back-solves MBU from a real AMD run and finds it **9 % conservative**; [rtx6000-pro §3.4](./rtx6000-pro.md)'s measurements *beat* it at bs=1. The derate stands where no per-GPU calibration exists (H100, H200, B200, B300, GB300). |

**Resolved 2026-09-19 — the GDN state-slot clash is no longer a disagreement.** What used to be
the "H100 `S` and state dtype" row here (`gpus/h100.md` 87/8 vs architecture.md §10.2 56/7) is now
pinned in [METHODOLOGY §8](../../METHODOLOGY.md): **`S` = 5 slots × 78,446,592 B bf16 = 392.2 MB
per request**, SGLang's shipped default and the same convention §8 already pinned for Kimi-K3's
KDA state. Every `gpus/<gpu>.md` Qwen3.8-27B row was recut to it, so the 56 / 7 in
[h100 §1.1](./h100.md) and in `gpus/h100.md` §2 are now the same number. Open question **#2**
below (vLLM's unpublished `S`) is what is left of it.

## Optimization impact

From each pair doc's §2 (what runs) and §4 (what it costs). "n/a" means the mechanism does not
exist for this model, not that it is unsupported.

| Optimization | A100 sm_80 | H100/H200 sm_90 | B200 sm_100 | B300/GB300 sm_103 | RTX PRO 6000 sm_120 | MI355X gfx950 |
|---|---|---|---|---|---|---|
| **Attention kernel generation** (16 GQA layers, hd 256) | **FA2** — 230 TFLOPS, 73 % of peak; the one thing *not* handicapped here | **FA3** — ~740 TFLOPS BF16 / ~1.2 PF FP8; FlashInfer is what the verified H200 cell pins; `fa3` *"slightly faster at bs=1"* | **`trtllm_mha`.** FA4 hd256 ⚠️ **fails** for this family on SGLang v0.5.19 (`head_dim=256 does not support seqused_q/seqused_k`); FlashInfer is **illegal** for hybrid-GDN on SM100 | FA4 hd256 2-CTA **or** `triton`. GB300's doubled SFU gives a `meas.` 1.35× FMHA on a 128 K prefill vs GB200. `trtllm_mha` ⚠️ hangs at high concurrency on SM103 and its documented workaround (FlashInfer) is illegal here | FlashInfer **FA2-class** (`fa2` on SM12x), Ampere-style MMA in 99 KB shared memory. Plan MFU_attn 0.20–0.35 — but a `meas.` 256 K TTFT implies ~0.6 | **CK FA2** ~436 TFLOPS (17 % of peak, and that figure is MI350X). AITER's tuned hd256 ASM kernel is **MHA-only → GQA falls back to CK** |
| **GDN prefill** (48 of 64 layers) | **Triton/FLA only** — vLLM's FlashInfer gate skips sm_80. ⚠️ no sm_80 throughput published | vLLM's gate is `capability(90)` *"no further constraints"*, but SGLang says the fast path is SM100/103-only — ⚠️ **the two engines' docs conflict**; read the boot log | **FlashInfer GDN fast path auto-engages** (family 100 + hd_k 128 ✅ + CUDA ≥13 + **BF16 state**): *"~20–25 % GDN prefill speedup"*. CuteDSL GDN prefill is B200/B300-exclusive | Same fast path; **`--mamba-ssm-dtype bfloat16` is the gate, not just a memory saving**. SGLang's GB300 cells pin `--chunked-prefill-size 2048` ✅ | vLLM's gate admits family 120 + hd_k 128; SGLang runs Triton. ⚠️ same conflict, worth ~20–25 % of prefill | **Triton tier for both halves** — FlashInfer GDN and the fused CUDA GDN decode kernel are CUDA-gated. Any MI355X-vs-Blackwell comparison compares an unoptimised GDN path to an optimised one |
| **Weight format** | **INT4 W4A16 (Marlin)** — capacity only; BF16 tops out at 527 tok/s/GPU because it runs out of KV at bs 23. **No format raises A100's 2,030 tok/s compute ceiling** except INT8 W8A8 (624 TOPS, ⚠️ no first-party build) | **FP8 is the format.** 4-bit *loses*: Marlin/Machete dequant to BF16, so W4A16 lands at **6,206 vs FP8's 6,953 tok/s/GPU** on H200 while buying 10–13 % concurrency. On H100 INT4 wins output ($0.181) and loses input ($0.124 vs $0.083) | **NVFP4 W4A4 native**, 9,000 dense TFLOPS ≈ 2× FP8. INT4 runs on the **BF16** line — a memory trick, not a speed one | NVFP4 native at 13,500/15,000 TFLOPS — **but only 71.7 % of GEMM FLOPs reach it**, so the B300-over-B200 ceiling is **1.20×**, and 1.05–1.17× `meas.` on the family. Quantisation buys almost nothing in *fit*: BF16→NVFP4 is +19 % concurrency on a 241 GB budget. **INT8 is undeployable** | **NVFP4 W4A4 native** (the dense path dodges sm_120's broken MoE grouped GEMM) — worth **19 % of the output-token bill** vs FP8. ⚠️ two silent traps route you to Marlin W4A16 with no warning | **MXFP4 W4A4 native**, 10.1 PF = 2× FP8, 4× BF16. Worth **−8 % on output** but **−36 % on input** vs FP8 (prefill is compute-bound). NVFP4 is not native; INT4 is Triton-dequant with no published MFMA rate — both worse than either |
| **KV quantisation (FP8 E4M3)** | ✅ storage-only (no FA3 ⇒ dequant to FP16 before the MMA). ⚠️ **four repo docs disagree on whether it works at all**; if it does not, halve every FP8-KV column | **Not optional on 80 GB**: 39 → 56 seats at 8 K on one H100. FA3 also runs attention *itself* in FP8. ⚠️ a `meas.` **×1.6 TTFT regression on hd-256 shapes** may apply | ✅ native; 141 → 199 concurrent at 8 K. NVIDIA's own serve line pairs NVFP4 weights with FP8 KV | ✅ native; 231 → 325 at 8 K (+41 %). ⚠️ the `nvidia/` export ships **no** `kv_cache_scheme`, so `auto` leaves it BF16 — pin the flag | ✅ native; 65 → 91 at 8 K (+40 %). `meas.` *"for capacity, not speed — it doesn't improve tok/s and costs ~9 % at 82 K depth"* | ✅ native (`fp8_e4m3`); +18 % at 8 K. **The published MI355X command does not set it — add it.** FP4 KV ❌ on the AITER row |
| **KV quantisation (NVFP4/INT4)** | ❌ no FP4 KV kernels on sm_80 | ❌ unsupported on sm_90 | ⚠️ **TO BE VERIFIED** — `CacheDType` includes `nvfp4`; no checkpoint, recipe or eval for this model | ⚠️ FlashInfer's NVFP4 paged-KV merged for SM100/103 on **2026-09-18**, one day before this research date — untried. Would take KV to 16 KiB/token (~600 conc. @8 K) | ⚠️ plan as unavailable — the SM120 NVFP4-KV kernel (FlashInfer #4955) is **sparse-MLA-only**, a geometry this model lacks | ❌ NVFP4 is an NVIDIA format; the AITER row shows FP4 KV ❌ |
| **GDN state dtype** (`--mamba-ssm-dtype bfloat16`) | **+60 % concurrency @8 K** (46 → 73 INT4). Purely memory/accuracy — unlocks no kernel here | **+60 % @8 K on H100** (35 → 56); the single biggest lever on an 80 GB card. Purely memory/accuracy on sm_90 | **+58 % @8 K** (126 → 199) **and** it unlocks the GDN prefill fast path | **Load-bearing twice**: +19 % concurrency *and* the FlashInfer GDN prefill gate. At bs 256 it cuts state traffic from **41 % to 21 % of the decode step** | **+57 % @8 K** (58 → 91). Memory/accuracy only on sm_120 | **The highest-leverage flag on this GPU**: fp32@`S`=5 costs **3.0×** the concurrency of bf16@`S`=1. Memory/accuracy only on gfx950 |
| **Speculative decoding** | ⚠️ **an open sm_80-only speculation fault is reported for this exact model.** Modelled range $0.09–0.22/1M out — "2× cheaper" to "no change". A100 is compute-bound at the operating point, which is where `vcr → 1` | Latency feature, not a cost feature. `meas.` on 1×H200 (⚠️ secondary): DFlash2 **3.43× at c=1, 2.27–2.85× at c=8, 1.01–1.45× at c=32**; the S1 point is c=168. Same-family H100 proxy: **+32 % at bs 8, +58 % at bs 32** | MTP stays bandwidth-bound to bs 128 (`batch × D` < the 1,169 ridge point); DFlash2 (D=8) collapses 4.36× → 2.54× by bs 128. Costs **37 % of concurrency** (242 → 153) unless `--enable-linear-replayssm-spec` | **The one measured acceptance-vs-batch curve in the tree**, on B300 for the same family: **1.55× @c4 → 1.36× @c32 → 1.21× @c128 → 1.15× @c256**. MTP is free (the 0.425 B head ships in the checkpoint). $0.165 → $0.143 at bs 256. **Turn it on; turn it off only if you measure it hurting** | ⚠️ **vLLM's `hardware_overrides` sets γ=5 for this card; the only user sweep on it measured γ=5 catastrophic (23 tok/s, acceptance 2.32) and γ=2 optimal (62.2 vs 46.8 unspec.)**. DFlash2 `meas.` 240–335 tok/s single-stream. D=8 makes bs 64 infeasible | **A latency product, not a cost product.** `meas.` on the shape-identical Qwen3.5-27B: DFlash2 block=16 **460 tok/s mxfp4 / 396 bf16**, acceptance **10.377**, up to **5.02×** — and still **25× more expensive per token** than simply filling 288 GB with batch |
| **Prefix caching** | Native. TTFT **1,605 → 164 ms** at 90 % hit. ⚠️ A100-specific downside: vLLM `meas.` **−36.7 % throughput** with *zero* prefix overlap | **A capacity purchase, not a free optimisation** on 80 GB — it is what forces `S` = 5 and bs 128 instead of ~290. On H200 it is the larger of the two levers: input cost ÷5.3 at 90 % hit | Input $0.0494 → **$0.0041** at 90 % hit (32 K prompts). Costs 2.4× concurrency at short context, 1.28× at 32 K. **Keep it on** | Sustained output **3,856 → 5,940 → 10,245 tok/s/GPU** across 0/50/90 % hit — a 90 %-hit workload gets **2.7× the sustained output on identical hardware**. Blended $0.0757 → $0.0478 | Input cost ÷9.5 at 90 % hit (S1), ÷6.3 at S3 (the quadratic attention survives the cache). Worth it for agentic traffic, **not** for one-shot | Input **$0.0667 → $0.0127** at 90 % hit (a hit costs 10 % of a miss, METHODOLOGY §6); blended $0.080 → $0.062. **Above ~10 % hit rate the cache wins; keep `S` = 5.** ⚠️ SGLang forces `--disable-radix-cache` on its *DeepSeek* ROCm recipes — unknown whether that generalises |
| **EP / wide-EP / DeepEP / EPLB** | **n/a — dense model, no experts.** METHODOLOGY §4's `distinct_experts(batch)` collapses to "all weights, always" on every GPU. NVL72's single clearest architectural argument (holding a 256-expert MoE's routed experts once) **does not apply to this pair at all** ([gb300 §2](./gb300.md)) | ← | ← | ← | ← | ← |
| **PD disaggregation** | ❌ no Dynamo recipe; 200 Gb/s IB; *"a thousand-GPU problem"* | ⚠️ no P/D layout, but H100/H200 profiles **do** expose Mooncake KV-store strategies — a cross-replica prefix cache is the shape worth evaluating | ❌ `compatible_strategies: ["single_node_tp"]` only | ❌ same; the rack machinery (Dynamo/NIXL/KVBM) exists but nobody has wired this model to it | ❌ Dynamo has no sm_120 support statement; LMCache wheels carry no sm_120 cubins | ❌ Dynamo has no documented ROCm support |
| | **The open question under all six PD cells: ⚠️ whether the GDN recurrent state transfers over NIXL/Mooncake at all. A handoff that moves KV but not the 48 recurrent states is silently *wrong*, not slow** ([gb300 §6 #11](./gb300.md)) | | | | | |

## Cost vs vendor API

Qwen Cloud list, the model author's own service ([architecture.md §11.1](./architecture.md)):
**$0.50 / 1M input · $3.00 / 1M output · $0.05 explicit-cache read** (exactly 10 % of input).
Under METHODOLOGY §6's blend — 75 % input, half of it cached at the *vendor's own* cached rate,
25 % output — that is **$0.9563 / 1M**. The real third-party market (OpenRouter FP8 cluster) is
$0.20–0.30 in / $2.20–2.55 out, blending to ~$0.72; the cheapest listed endpoint is Darkbloom FP4
at $0.10 / $1.80, blending to $0.4913 ([architecture.md §11.2](./architecture.md)).

**Break-even utilisation** = the fraction of the rented hour the GPUs must actually be producing
tokens at the S1 operating point for self-hosting to match the API. Blended-vs-blended unless the
pair doc published only the output-side figure.

| GPU | Blended $/1M self-hosted | vs Qwen Cloud ($0.9563) | vs OpenRouter FP8 (~$0.72) | vs Darkbloom FP4 ($0.4913) | Output-token gross margin vs $3.00 |
|---|---|---:|---:|---:|---|
| [B300](./b300.md) low $7.40 / high $15.00 | $0.060 / $0.122 | **6.3 % / 12.8 %** | ~8 % / 17 % | ~12 % / 25 % | **9× – 18×** |
| [B200](./b200.md) low $6.00 / high $14.00 | $0.063 / $0.148 | 6.6 % / 15.5 % | ~9 % / 21 % | ~13 % / 30 % | 6.9× – **16.2×** |
| [MI355X](./mi355x.md) $8.60 (single price) | $0.080 | 8.4 % (**7.0 %** on output alone) | 11 % (9.6 %) | 16 % (11.7 %) | 14.2× |
| [H200](./h200.md) low $3.99 / high $7.912 (derated) | $0.099 / $0.196 | **10.3 % / 20.5 %** | 13.6 % / 27.0 % | 20.1 % / 39.8 % | 6.6× – 13.2× |
| [RTX PRO 6000](./rtx6000-pro.md) low $1.80 / high $4.143 | $0.087 / $0.199 | **9.1 % / 20.8 %** | 12 % / 28 % | 18 % / 41 % | 5.3× – 12.3× (17.0× reserved) |
| [H100](./h100.md) low $3.20 / high $6.88 | $0.089 / $0.192 | **9.3 % / 20.0 %** | 12.4 % / 26.7 % | 18.1 % / 39.0 % | 6.3× – 13.6× |
| [GB300](./gb300.md) $18.00 (single price) | $0.134 | **14.0 %** (11.6 % on output alone) | 19 % | 27 % | 8.6× |
| [A100](./a100.md) low $1.59 / high $3.431 | $0.126 / $0.271 | **13.2 % / 28.4 %** | 19.1 % / 41.2 % | 26 % / 55 % | 6.4× – 16.1× |

Three things to read out of this table rather than off it:

- **Output tokens self-host decisively; input tokens do not.** Self-hosted input is $0.035–0.373
  per 1M against an API floor of $0.10 and a *cached*-read floor of $0.024–0.05. Against a
  cache-heavy agentic workload priced at the vendor's cached rate, the input-side advantage nearly
  vanishes — Qwen's cached $0.05 is only 1.2× B200's uncached $0.041 ([b200 §4.4](./b200.md)).
  **And `reasoning_effort` defaults to `xhigh` with a recommended 262 K reasoning budget**, so the
  do-nothing configuration is the most output-heavy — and therefore the most self-host-favourable —
  one ([architecture.md §1.4](./architecture.md)).
- **Break-even is so low everywhere that the GPU choice is not a cost question, it is a capability
  and risk question.** Every row clears at ≤ 21 % utilisation against the model author's own list
  price. Even GB300 at the single most expensive published GPU-hour in the survey pays for itself
  at 14 %.
- **Only Novita and Alibaba serve the full 1 M window.** If you need 1 M context the API comparison
  narrows to two providers — and self-hosting it needs NVFP4 weights + FP8 KV and a
  Blackwell-Ultra-class capacity (B300 holds 6 concurrent 1 M sequences per GPU, GB300 6, MI355X 6;
  **no GPU in the survey holds even one at BF16 weights with BF16 KV**).

## Recommendation

### Primary: 8 × B300 (HGX, 268 GB/GPU, 2,144 GB/node)

**Run eight independent TP1 replicas, one per GPU, behind a router.** That is 8 × 325 = **2,600
concurrent 8 K sequences per node** against TP8's 2,011, with zero NVLink traffic in the decode
loop and eight independent failure domains ([b300 §1.3, §5](./b300.md)). Use TP only to raise the
single-request context ceiling (TP8 holds 27 concurrent 1 M sequences vs TP1's 6).

Start from vLLM's own B300 profile and the NVFP4 variant it blesses for `b300`
([b300 §5](./b300.md), verbatim from `recipes.vllm.ai`):

```bash
vllm serve Inferact/Qwen3.8-27B-NVFP4 \
  --tensor-parallel-size 1 \
  --max-model-len 262144 \
  --kv-cache-dtype fp8 \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice --tool-call-parser qwen3_xml
# + --speculative-config '{"method":"mtp","num_speculative_tokens":3}'
```

Under SGLang, the GB300 cell is the nearest published `sm_103` recipe (it deliberately omits
`--attention-backend flashinfer`, which is illegal for hybrid-GDN on SM100/SM103), plus two
additions that are **in no published B300 recipe** and are this analysis's recommendations:

```
--trust-remote-code --model-path <checkpoint> --kv-cache-dtype fp8_e4m3
--mem-fraction-static 0.85 --chunked-prefill-size 2048
--reasoning-parser qwen3 --tool-call-parser qwen3_coder
--mamba-ssm-dtype bfloat16          # unlocks the FlashInfer GDN prefill kernel; cuts state traffic 41% -> 21%
--attention-backend triton          # or fa4; NOT trtllm_mha until sglang#21904's SM103 fix is confirmed
```

**Three things that will bite and are not in any recipe.** (a) `--reasoning-parser qwen3` is
effectively mandatory — without it the whole `<think>` block lands in `message.content`. (b) The
vLLM/SGLang tool-call parsers disagree (`qwen3_xml` vs `qwen3_coder`) for the *same* chat template.
(c) Budget **3–10 minutes to READY**, not seconds: SGLang measures ~6.5 min to load the 18 BF16
shards and advises 10 minutes before calling a boot hung.

### Secondary: AWS p6 family

`p6-b300.48xlarge` is the same 8 × 268 GB node at **$17.802/GPU-hr** — above OCI's `BM.GPU.B300.8`
$15.00 `high` tier and 2.4× Hyperstack's $7.40 `low`, so **price, not silicon, decides the p6-b300
verdict**: at $7.40 a B300 is 34.1 GB of HBM per $/hr (1.7× H100's); at $17.802 it is 15.1 GB, well
*below* H100 ([b300 §6](./b300.md)). `p6-b200.48xlarge` at $14.242 is the same story on 180 GB.
Neither `b200` nor `b300` is in either engine's verified set, so on AWS you inherit the same
unproven-pair risk as on a neocloud without the neocloud price. **Use p6 when you already hold the
capacity reservation; otherwise the B300 economics live at the `low` tier.**

If the traffic is 128 K-dominated or the fleet is latency-tiered, note that the repo's own H200 and
RTX PRO 6000 rows are the only **vendor-verified** pairs — an H200 replica at $3.99/GPU-hr is the
lowest-risk way to get a real number for this model, and `p5en.48xlarge` is its AWS equivalent.

### What to benchmark first, in this order

Each of these decides a number that currently rests on a roofline:

1. **`sglang.bench_serving --dataset-name random --random-input-len 4096 --random-output-len 512`
   at concurrency {1, 8, 32, 64, 128, 256, 396}** on one B300, then back out MBU from
   [b300 §3.1](./b300.md)'s byte model. This replaces *every* throughput and cost number above.
2. **Read the mamba-cache allocation out of the vLLM boot log and divide by 78,446,592.** That is
   vLLM's `S`. Every fit cell and every feasibility verdict in all eight pair docs is
   SGLang-specific until this is known.
3. **Read the selected attention and GDN backends out of the server log.** On SM103 the failure
   modes are silent: `--block-size` not a multiple of 128 drops FA4 → FA2; `trtllm_mha` hangs at
   ≥ 256 concurrent; and on B200 the SGLang auto-selector has been measured costing **2.6×** on
   this architecture family ([sglang#39515](https://github.com/sgl-project/sglang/issues/39515)).
4. **A/B `--mamba-ssm-dtype bfloat16` against `float32`** on throughput *and* on a full
   1,319-question GSM8K run. SGLang calls bf16 state *"an accuracy gate"* and has measured fp32
   winning on one precision and losing on another.
5. **Sweep MTP γ and read `vllm:spec_decode_num_{accepted,draft}_tokens_total`** — *"throughput
   alone cannot distinguish a working drafter from one that loaded and was ignored."* B300 has the
   only measured acceptance-vs-batch curve in the tree (for the MoE sibling); get one for this
   checkpoint.
6. **A/B `--use-replayssm` at bs 256, ctx 8 K.** It targets 41 % of the decode step at the S1
   operating point and nobody has published a number for it on any GPU.
7. **Score GSM8K and MMMU-Pro on the exact checkpoint you ship.** NVIDIA's GB300-tested NVFP4 table
   exists (≤ 1.5 pt loss, two benchmarks *above* BF16) but the SGLang 202-cell sweep covers
   SM120/SM121 only, and AMD's MXFP4 build publishes GSM8K and nothing else — **no multimodal eval
   exists for any 4-bit build of a model whose headline feature is vision.**

## Open questions

Consolidated ⚠️ **TO BE VERIFIED** across all eight pair docs, deduplicated, ordered by how much
each one would move a decision. **24 items.**

### Would change which GPU you buy, or how much capacity you plan

| # | Question | Impact | Settle it |
|---|---|---|---|
| 1 | **Nobody has published a throughput, TPOT or TTFT number for this model on any datacentre GPU.** MLPerf: no submission. InferenceX: `200 []`. Both engines' cookbooks: single-stream or ≤8-concurrent consumer Blackwell only | Every cell in the two tables above is `est.` This is the parent of every other item | `sglang.bench_serving` / `vllm bench serve` at the METHODOLOGY §4 grid, on any one of the eight |
| 2 | **vLLM's state-slot count `S` is unpublished.** SGLang publishes 5/4/3/1 by strategy; vLLM exposes `--mamba-cache-mode align` and `--enable-mamba-fine-grained-prefix-cache`, both implying >1, and no slot count. vLLM is the *only* engine with a published command on B200, B300, MI355X and A100 | 1.5–2× swing in every concurrency and cost figure. All eight docs flag it | Boot at a fixed `--max-num-seqs`; divide the reported mamba-cache allocation by 78,446,592 |
| 3 | **MBU on a hybrid-GDN model is unmeasured on every GPU.** Every MBU band in use comes from pure-attention dense models (Llama-3.3-70B, Llama-2-70B). The GDN recurrent update is a bandwidth-bound non-GEMM that reads *and writes* 2 × 78.45 MB per sequence per step — 24–41 % of a large-batch step | If its achieved bandwidth is worse than a GEMM's, every TPOT above is optimistic. B300/GB300 MBU has never been published at all | Measure TPOT at two batches; solve for MBU from the §3.1 byte model |
| 4 | **GDN prefill MFU is unpublished on every architecture** — and 48 of 64 layers run it | The weakest assumption in every TTFT and `$/1M input` figure in the survey | Time a known-length prefill; back out MFU from [architecture.md §6.2](./architecture.md) |
| 5 | **GB300 and MI355X each rest on one published price** (OCI $18.00 and $8.60; no reserved tier for either). A ⚠️ $2.95 MI355X quote circulates from a secondary source — at that rate MI355X becomes the **cheapest row in the survey** ($0.072/1M out), ahead of B200 | The MI355X verdict is *"a procurement question, not a silicon question"*. GB300's $18.00-vs-$2.31 anchor spread is 7.8× — larger than any technical uncertainty in that document | Get signed quotes (TensorWave/Crusoe for MI355X; CoreWeave/Together/Nebius for GB300) |
| 6 | **Which GDN prefill path actually runs on sm_90 and sm_120.** vLLM's gate is `capability(90)` / family 120 with *"no further constraints"*; SGLang documents the fast path as SM100/SM103-only. The two engines' own docs conflict | Worth ~20–25 % of prefill on H100/H200 and RTX PRO 6000 | Read the selected linear-attention backend from the server log; A/B `--linear-attn-backend triton` |
| 7 | **A100's long-context decode cliff.** llama.cpp measures **35.6 → 1.4 tok/s past ~80 K KV positions on this exact model** (a 24× collapse, open issue) | If an analogous threshold exists in the vLLM sm_80 path, every A100 S3 row is wrong by an order of magnitude | Sweep decode rate against KV position past 80 K |
| 8 | **KV-head replication at TP > 4 is derived, not verified.** `num_key_value_heads = 4`, so TP8 is assumed to replicate each head ×2 | If TP8 does *not* behave this way, every TP8 concurrency column is wrong by 2× | Boot TP4 and TP8 at fixed `--max-model-len`; TP8's per-GPU KV pool should be ~2× TP4's |
| 9 | **Whether the GDN recurrent state transfers over NIXL / Mooncake, or offloads with vLLM's verified `offloading_cpu`/`offloading_fs`** | A cross-replica or PD handoff that moves KV but not the 48 recurrent states is silently **wrong**, not slow. And offloading only KV leaves the state pool as the ceiling — which it *is* below ~12 K context | Enable the connector; check whether remote hits re-run the recurrent layers. Enable offload; measure `max_running_requests` at fixed ctx |
| 10 | **Prefix-hit-rate → TTFT curve is unpublished for any hybrid-GDN model**, where a hit must also restore a recurrent-state checkpoint | Prefix caching is the largest cost lever on B300/GB300/H200/MI355X, and its 0/50/90 % numbers are all FLOP-scaling `est.` | `bench_serving` with a synthetic shared-prefix dataset at three reuse rates |

### Would change the configuration on a chosen GPU

| # | Question | Impact | Settle it |
|---|---|---|---|
| 11 | **ReplaySSM's measured gain is unpublished for this model on any GPU** | It directly attacks 24–41 % of the decode step, and on B200 it is the difference between 153 and 242 concurrent requests under MTP | A/B `--use-replayssm` / `--enable-linear-replayssm-spec` at bs 128–256, ctx 8 K |
| 12 | **Acceptance-vs-batch crossover for MTP/DFlash2 on this checkpoint** | Up to 3.9× on output cost at low batch, ~0 at the capacity ceiling. [b300 §3.3](./b300.md) has the only measured curve in the tree — for the 397 B MoE sibling — and [b200 §3.4](./b200.md) *disagrees with* [architecture.md §7.4](./architecture.md) about where the crossover is | Sweep bs at fixed ctx reading `vllm:spec_decode_num_{accepted,draft}_tokens_total` |
| 13 | **vLLM's `num_speculative_tokens: 5` override for the RTX PRO 6000 is contradicted by the only user sweep on the card** — γ=5 measured 23 tok/s at acceptance 2.32; γ=2 measured 62.2 vs 46.8 unspeculated | A 2.7× throughput swing, and the shipped default is the wrong starting point | Sweep γ ∈ {1,2,3,5} with the acceptance counters |
| 14 | **An open sm_80-only speculation fault is reported for this exact model** (A100/A30/CMP 170HX, two owners mid-bisect) | Speculation is the largest cost lever on A100 ($0.09 vs $0.22/1M out) and it is the one thing reported broken there | Reproduce; bisect |
| 15 | **`Qwen/Qwen3.8-27B-FP8` likely fails at boot on sm_80** — [vllm#55008](https://github.com/vllm-project/vllm/issues/55008), open: CUTLASS FP8 is selected on Ampere and asserts at profile-run where `MarlinFP8ScaledMMLinearKernel` is correct | This is *why* INT4 and not FP8 is the A100 recommendation, despite FP8 being better-supported everywhere else | Boot it; or wait for the SM89+ gate |
| 16 | **Does FP8 KV work on A100 at all?** Four repo documents disagree: `gpus/a100.md` and `quantization-formats.md` say yes (storage-only), `flash-attention.md` says ✅ via Triton/FlashInfer but ❌ via FlashAttention, `inference-engines.md` says plainly **no**, and architecture.md §8.4 says the pool would be BF16 | If unavailable, halve every A100 FP8-KV column: 8 K INT4 concurrency 73 → 52, the operating point bs 92 → 73 | One boot with `--kv-cache-dtype fp8_e4m3` and a KV-pool read |
| 17 | **FA4 vs FP8 KV on Blackwell is an unresolved either/or in vLLM.** The FA4 hd256 2-CTA kernel excludes quantized KV; every published recipe pins FP8 KV; the fallback is a **silent** drop to FA2 | A silent ~2× attention regression with no error, on the 16 layers that do quadratic work | Read the selected backend from the log; A/B `--kv-cache-dtype auto` vs `fp8` on TTFT at 128 K |
| 18 | **`trtllm_mha` hangs at high concurrency on SM103** ([sglang#21904](https://github.com/sgl-project/sglang/issues/21904), reproduced at 512 concurrent) — **and its documented workaround, `--attention-backend flashinfer`, is illegal for hybrid-GDN models on SM100/SM103** | Confirm your build routes to `triton` or `fa4`; re-test at concurrency ≥ 256 | Read the log; load-test |
| 19 | **Which NVFP4 export is right on B200/B300.** METHODOLOGY §8 pins `nvidia`/`RadixArk` at 21.92 GB; vLLM's recipe scopes that repo to `rtx_pro_6000` + `dgx_spark_gb10` and blesses `Inferact` (26.38 GB) for `b200`/`b300` — while `quantization-formats.md` records the `nvidia` export *was* tested on GB300 | 4.46 GB of KV budget, and possibly a different quantised-layer map | Load both; compare reported weight bytes, KV pool and GSM8K |
| 20 | **RTX PRO 6000 Server vs Workstation Edition bandwidth** (1,597 vs 1,792 GB/s — 11 % of TPOT). Every published measurement of this model on "RTX PRO 6000" is consistent with Workstation silicon, and no marketplace listing distinguishes the SKUs | Decode is bandwidth-bound by ~450×. The SE's memory clock is locked at `[0,0]`, so the viral "454 tok/s" is physically unreachable on SE | `nvidia-smi -q -d CLOCK` plus a streaming-read microbenchmark before accepting any rented card |
| 21 | **SGLang-ROCm's AITER `--mem-fraction-static × 0.85` multiplier above 8 K context** is documented on the 2.4 T MoE sibling's page only | If it applies to a dense GDN model too, every MI355X concurrency cell drops **17 %** (356 → 297 at 8 K) | Read the allocated pool at >8 K ctx under SGLang-ROCm |
| 22 | **Whether radix caching is forced off on ROCm.** SGLang forces `--disable-radix-cache` on its DeepSeek-V4.1 MI350X recipes; nothing is documented for this model | If it generalises to the AITER path, `S` drops to 1 and every prefix is re-prefilled — the entire prefix-caching cost case evaporates | Boot SGLang-ROCm with radix cache on and read the mamba-cache strategy |
| 23 | **Whether either engine chunks the ViT attention at maximum resolution.** The vision tower has **no window attention**: a 16.78 Mpx image is 65,536 patch tokens, 590 TFLOP, 91 % of it quadratic, and an unchunked FP16 score matrix would be **8.6 GB** | Multimodal traffic could OOM a box that serves text fine — and this surfaces *later* on a 268 GB card than on a 96 GB one, i.e. in production | Send a 16.78 Mpx image; watch peak memory |
| 24 | **Accuracy of the 4-bit builds on anything but GSM8K.** NVIDIA's NVFP4 table is now populated (GB300-tested, ≤1.5 pt, two benchmarks above BF16) but the 202-cell SGLang sweep covers SM120/SM121 only; **AMD's MXFP4 build publishes GSM8K and nothing else** — no MMLU-Pro, no GPQA, no long-context, and **no multimodal eval for a vision model**. W4A4 quantises activations, and AWQ calibration was done at `seq_len 512` for a 262 K-context model | The recommended format on B200/B300/GB300/RTX PRO 6000 (NVFP4) and on MI355X (MXFP4) is 4-bit *activations*, not just weights | Run MMMU-Pro and a long-context retrieval task on the exact checkpoint before shipping vision traffic |

Two lower-stakes items that recur in every document and are recorded here rather than numbered:
the **tool-call parser conflict** (`qwen3_xml` in vLLM vs `qwen3_coder` in SGLang, same chat
template — a silent failure where calls land in `message.content`), and **cold-start time**, which
is unmeasured on every datacentre GPU; the only published figure is DGX Spark's ~6.5 minutes for
the 18 BF16 shards, two orders of magnitude above the raw-NVMe floor.

## Sources

Every claim above is carried from one of these, cited inline by relative link:

- [`METHODOLOGY.md`](../../METHODOLOGY.md) — §1 weight memory, §2 KV/state and the slot multiplier `S`, §3 fit and the consistency rule, §4 roofline and planning MBU/MFU, §6 cost and scenarios S1–S4, §8 pinned GPU/model/price inputs
- [`architecture.md`](./architecture.md) — §1.4 chat template and `preserve_thinking`, §3 parameter count, §4 weight memory by dtype, §5 KV and GDN state, §6 compute profile, §7 MTP/DFlash2/DSpark, §8 engine support, §9 quantised variants and evals, §10 published benchmarks, §11 API pricing, §12 open questions
- [`h100.md`](./h100.md) — §1.1 inputs and the resolved `S`/state-dtype pin, §1.3 concurrency, §1.5 node shapes, §2 kernel matrix, §3.2 throughput grid, §3.3 the Qwen-3.5-397B proxy, §4.2 cost, §4.5 break-even, §6 risks
- [`h200.md`](./h200.md) — §1.3 fit, §1.5 what the table hides, §2 kernel matrix, §3.2 grid, §3.3 the DFlash2 H200 provenance conflict, §3.4 why W4A16 is *slower* than FP8 here, §4 cost, §5.1 node shapes, §6 risks
- [`b200.md`](./b200.md) — §1.1 which NVFP4 build, §1.2 fit, §2 kernel matrix, §3.2 grid, §3.4 speculation and the ridge point, §3.5 proxies, §4 cost, §6 risks
- [`b300.md`](./b300.md) — §1.1 inputs, §1.3–1.4 fit, §2 kernel matrix, §3.1 the blended prefill rate and the 1.20× ceiling, §3.2 grid, §3.3 the measured B300 MTP curve and B300-vs-B200 ratios, §4 cost, §5 deployment shape and launch commands, §6 risks
- [`gb300.md`](./gb300.md) — §1.3 fit over the rack, §1.4 where the budget goes, §2 kernel matrix, §3 throughput, §4 cost and break-even, §6 risks, Appendix A (reconciliation with architecture.md)
- [`a100.md`](./a100.md) — §1.0 the 80 GB/80 GiB basis, §1.3 concurrency, §2 kernel matrix, §3.2 grid and the compute ceiling, §3.4 the sm_86 calibration, §4 cost, §6 risks
- [`rtx6000-pro.md`](./rtx6000-pro.md) — §1.2 what cannot be sharded, §1.3 fit, §2 kernel matrix, §3.2 grid, §3.3 nine published measurements, §3.4 where est. and meas. disagree, §3.5 speculation, §4 cost, §6 risks
- [`mi355x.md`](./mi355x.md) — §1.1 the MXFP4 checkpoint reconciled byte-for-byte, §1.2–1.5 fit, §2 kernel matrix, §3.2 the AMD Qwen3.5-27B measured anchor, §3.3 grid, §4 cost, §6 risks and the three documents it corrects
- Foundation docs cited *through* the pair documents, never re-derived here: [`gpus/`](../../gpus/) (a100, h100, h200, b200, b300, gb300, rtx6000-pro, mi355x), [`cross-cutting/flash-attention.md`](../../cross-cutting/flash-attention.md), [`cross-cutting/quantization-formats.md`](../../cross-cutting/quantization-formats.md), [`cross-cutting/inference-engines.md`](../../cross-cutting/inference-engines.md), [`cross-cutting/serving-optimizations.md`](../../cross-cutting/serving-optimizations.md), [`cross-cutting/cloud-pricing.md`](../../cross-cutting/cloud-pricing.md), [`cross-cutting/inferencex-api.md`](../../cross-cutting/inferencex-api.md)
