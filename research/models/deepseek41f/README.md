# deepseek-ai/DeepSeek-V4.1-Flash — GPU selection guide

Research date: **2026-09-19**. This document consolidates
[`architecture.md`](./architecture.md) and the eight per-GPU pair documents
([h100](./h100.md) · [h200](./h200.md) · [b200](./b200.md) · [b300](./b300.md) ·
[gb300](./gb300.md) · [a100](./a100.md) · [rtx6000-pro](./rtx6000-pro.md) ·
[mi355x](./mi355x.md)), which were written and audited in parallel. Formulas, markers
(`meas.` / `est.` / **⚠️**) and planning defaults follow
[`research/METHODOLOGY.md`](../../METHODOLOGY.md). **Every number below is a link to the
pair document that produced it; nothing new is introduced here.**

---

## Summary

1. **What it needs:** 510.29 GB of checkpoint ([architecture.md §4](./architecture.md)), of which **202.76 GB (188.83 GiB) is two Engram hash tables** read by 48 random 264-byte gathers per token — capacity and random-access latency, not bandwidth ([architecture.md §4.1](./architecture.md)). Active params are **7.89 B prefill / 16.13 B decode** (CED halves prefill), and global KV is **890 B/token** with the FP4 cache ([architecture.md §3.3, §5.1](./architecture.md)).
2. **What runs it today:** **vLLM `main`/nightly on every GPU here** — `min_vllm_version 0.30.0` does not exist, latest release is 0.29.0, so you are pinning a nightly container ([architecture.md §8.1](./architecture.md)). vLLM marks `h100/h200/b200/gb200/gb300/b300/mi350x` **verified**; **A100 and RTX PRO 6000 are absent** and run only on community forks ([architecture.md §8.2](./architecture.md)). TensorRT-LLM has no V4.1 support at all.
3. **Best for interactive serving: 8× HGX B300 at TP4** — **1,373 decode-only tok/s/GPU at 23.3 ms TPOT, measured** ([b300.md §3.3, §4.2](./b300.md)), the highest of any GPU on the common InferenceX harness, and the only Blackwell part whose throughput does **not** collapse at concurrency 128 on 100K-token prompts ([b300.md §3.2](./b300.md)).
4. **Best for cost per token: B300 run as four independent TP2 replicas per node** — **2,656 tok/s/GPU measured, $0.77/1M output** at Hyperstack $7.40 ([b300.md §4.2–§4.3, §5.1](./b300.md)). On paper 8× B200 at Hyperstack $6.00 is cheaper ($0.30–$0.36/1M, [b200.md §4.1](./b200.md)) but that rests on a batch-1024 extrapolation past B200's measured capacity cliff.
5. **Cheapest viable node: 4× RTX PRO 6000 at $7.20/hr** ([rtx6000-pro.md §4](./rtx6000-pro.md)) — the smallest and cheapest configuration that serves the native checkpoint at all, on a community image. Cheapest measured **$/1M output** outside Blackwell is **8× A100 at $1.16–$2.51** ([a100.md §4.2](./a100.md)), also community-only and measured on A800.
6. **Hopper is the compatibility answer, never the economic one.** H100 delivers **1/12.9 of a 2-GPU B300** on the identical benchmark ([h100.md §3.4](./h100.md)); H200 is **3.6× slower per GPU than B200** ([h200.md §6.4](./h200.md)). Both lose the FP4 expert GEMM, the 890 B/token KV cache and the fused mega-attention kernel at once — all three are `sm_10x` hardware gates, not software gaps.
7. **What is not runnable:** 1 or 2 GPUs of anything; TP4 on H100 (8.88 GB/GPU short even with Engram on the host, [h100.md §1.2](./h100.md)); TP4 on H200 without Engram offload (1.07 GB/GPU over — **vLLM ships this profile anyway**, [h200.md §1.2](./h200.md)); TP2 on B200 (short by 0.1 GB even offloaded, [b200.md §1.2](./b200.md)); TP2 on MI355X with Engram resident ([mi355x.md §1.2](./mi355x.md)); and INT8 anywhere on B300/GB300 ([b300.md §2](./b300.md)).
8. **The executed weight format is not what the datasheet implies.** On Blackwell the MXFP4 experts run **W4A8** against MXFP8 activations — the **4,500 TFLOPS FP8 line, not the 13,500 FP4 line** — which is why B300 ≡ B200 to within 1.5 % at every concurrency B200 can hold ([b300.md §2.1, §3.2](./b300.md)). B300 wins on *capacity*, worth +245 % at concurrency 128.
9. **The precision costs nothing measurable.** GSM8K `em_strict` is 0.9704–0.9757 across H100 Marlin W4A16, B200/B300/GB300 native FP4 and MI355X CK a8w4 — every cell inside one standard error ([h100.md §3.4](./h100.md), [gb300.md §3.4a](./gb300.md), [mi355x.md §3.4ii](./mi355x.md)).
10. **Self-hosting loses to DeepSeek's own API on output price on every rented GPU in this roster.** It wins on *input*-heavy agentic traffic, where the measured per-total-token cost is **$0.018–$0.042/1M** ([b200.md §4.1](./b200.md)) and **$0.0198/1M** on B300 ([b300.md §4.3](./b300.md)) — an order of magnitude under the API. See [Cost vs vendor API](#cost-vs-vendor-api).

---

## Cross-GPU comparison

One row per GPU. `meas.` cells are published measurements; `est.` cells are the pair
document's calibrated model. Concurrency columns are each document's **binding** reading —
the MLA latent has `num_key_value_heads: 1`, so under plain TP the KV cache is **replicated
on every rank** and does not multiply by GPU count.

| GPU | Runnable today (engine) | Min GPUs | Recommended + parallelism | Executed weight format | Attention kernel | Max conc @8K | @128K | Interactive tok/s/GPU · TPOT | Max-thr tok/s/GPU | $/1M out interactive (low–high) | $/1M out max-thr | Blended $/1M | Confidence |
|---|---|---:|---|---|---|---:|---:|---|---:|---|---|---|---|
| **[H100](./h100.md)** 80 GB | vLLM nightly ✅ verified (SGLang runs, **8× slower**) | **8** | 8 · TP8/EP1, `--engram-config cpu_offload` **mandatory** | MXFP4 → **Marlin W4A16** (BF16 math) | `FLASHMLA_SPARSE_DSV41`, block 64 sm_90; **no** `MEGA_ATTN` | **799** ᴮ | **59** ᴮ | 404 · 19.8 ms `est.` — *meas.* **89.8 · 9.1 ms** @conc 20 | 924 `est.` | **$2.20–$4.73** `est.` — *meas.* $8.41–$21.28 | $0.96–$2.07 | $0.60–$1.28 `est.` | `meas.` fit + agentic envelope; `est.` grid |
| **[H200](./h200.md)** 141 GB | vLLM nightly ✅ verified (SGLang **15.7× slower**) | 4 (only with Engram offload) | **8 · TP8**, Engram **resident** | MXFP4 → **Marlin W4A16** | same sm_90 backend; **no** `MEGA_ATTN` | **3,557** ᶠ | **266** ᶠ | 860 · 37.2 ms `est.` — *meas.* **186.4 · 29.6 ms** @conc 64 | 2,418 `est.` ⚠️ | **$1.29–$2.56** `est.` — *meas.* $5.95–$11.79 | $0.46–$0.91 ⚠️ | $0.38–$0.75 | `est.` |
| **[B200](./b200.md)** 180 GB | vLLM nightly ✅ verified (SGLang **8.4× slower**) | 4 | 8 · TP8 (± `--enable-expert-parallel`) | **MXFP4 native W4A8** → FP8 line (4,500 TF) | FlashMLA sparse + **`MEGA_ATTN_DSV41`** + **FP4 KV**, SM100 | 9,007 ᴾ / **4,160** ᴱ | 768 ᴾ / **296** ᴱ | **3,604 · 22.2 ms** (meas.-anchored, batch 640) | 4,696 `est.` | **$0.46–$1.08** | $0.35–$0.83 | **$0.19–$0.44** | `est.` + 2 direct anchors |
| **[B300](./b300.md)** 268 GB | vLLM ✅ + SGLang ✅ both `verified` | **2** (TP2, Engram offload *arithmetically* required) | **4 · TP4** interactive · **4× TP2 replicas** for throughput | **MXFP4 W4A8** — *not* the 13,500 TF FP4 line | `FLASHMLA_SPARSE`/`MEGA_ATTN` (sm_103 passes `major==10`) | **11,184** ᴾ | **953** ᴾ | **1,373 · 23.3 ms** `meas.` (1,798 · 4.45 ms @c32) | **2,656** `meas.` (TP2) | **$1.50–$3.04** | **$0.77–$1.57** | **$0.48–$0.97** | **`measured`** |
| **[GB300](./gb300.md)** 279 GB usable | vLLM ✅ verified + SGLang `main` | 4 resident (2 with Engram→Grace) | 4 · TP4, one compute tray; **18 replicas per rack** | **MXFP4 W4A8 native + FP4 KV native** | **`MEGA_ATTN_DSV41`** | **11,193** ᴾ (16,164 offloaded) | **954** ᴾ (1,378) | **887 · 25.1 ms** `meas.` @conc 128 | 1,242 `est.` | **$5.64** — one price only | $4.03 | **$1.72** | **`measured`** conc 1–128 |
| **[A100](./a100.md)** 80 GB | ⚠️ **community fork only** (`vllm-backport` + 24-file SM80 patch set); vLLM-unverified | **8** | 8 · **TP4×DP2 + EP8**, Engram→host | MXFP4 → **Marlin W4A16** + FP8 → **W8A16** | custom SM80 candidate-MQA (community); **no FlashMLA** | **12,886** ᶠ | **965** ᶠ | **380 · 42.1 ms** `meas.` (on **A800**) | 380 — saturates at 128 | **$1.16–$2.51** ($0.99 res1y) | same as interactive | **$0.31–$0.78** | `meas.` on A800, community stack |
| **[RTX PRO 6000 SE](./rtx6000-pro.md)** 96 GB | ⚠️ **community images only**; stock vLLM = **2.3–4.0 tok/s**; no official recipe (404) | **4** | 4 · TP4+EP4, **Engram→pinned host RAM** | **MXFP4 native W4A8** (b12x) ⚠️ *disputed* | `FLASHINFER_MLA_SPARSE_DSV41` / `B12X_MLA_SPARSE` | **336** ᶠ | **25** ᶠ | **185 · 10.8 ms** meas.-equiv @C8 | 755 `est.` ⚠️⚠️ | **$2.70–$6.22** | $0.66–$1.53 ⚠️ | $0.72–$1.65 | `meas.` C1/C8 on **Workstation** SKU, scaled |
| **[MI355X](./mi355x.md)** 288 GB | vLLM ROCm nightly ✅ (`mi350x: verified`; SGLang gfx950 PR **closed**) | 4 | 4 · TP4, `--moe-backend aiter`; Engram **resident** | **MXFP4 native CK a8w4** + FP8 E4M3 OCP | `ROCM_AITER_MLA_SPARSE`; **DSA indexer kernel ⚠️ unconfirmed** | **7,649** ᶠ | **573** ᶠ | 1,291 · 24.8 ms `est.` — *meas.* **259 @c32** | 2,241 `est.` | **$1.85** — one price only | $1.07 | $0.48–$0.75 | `est.`, calibrated on a 6-point sweep |

**KV dtype basis for the concurrency columns** — this is the single most consequential
per-GPU difference, and it is a *kernel* gate, not an architecture one:
ᴾ **FP4, 890 B/token** (the shipped record; reachable only through `FLASHMLA_MEGA_ATTN_DSV41`, gated `capability.major == 10`) ·
ᴱ B200 at the **engine-realised ≈2,340 B/token** the pools actually imply ([b200.md §1.3](./b200.md)) ·
ᶠ **FP8, 1,650 B/token** ·
ᴮ **BF16, 3,200 B/token**, inferred on H100 from the measured KV pool ([h100.md §1.4](./h100.md)).

### Where the pair documents disagree, and which is right

| Disagreement | Resolution |
|---|---|
| **Is the 890 B/token FP4 KV reachable off Blackwell?** [METHODOLOGY §8](../../METHODOLOGY.md) and [architecture.md §5.2](./architecture.md) say yes — *"FP4 reduces storage rather than accelerates matrix multiplication … this is why the FP4 KV works identically on Hopper and Ampere."* Five pair docs say no. | **Both are right about different things, and the pair docs govern deployment.** The *architecture* needs no FP4 tensor core; no *engine* exposes the layout off `sm_10x`, because `nvfp4_ds_mla` lives only on `FLASHMLA_MEGA_ATTN_DSV41` (`major == 10`). [h100.md §1.4](./h100.md) settles it empirically: the same engine, same image, same week gives B200 a pool that **excludes** 1,650 B/token arithmetically, and H100 a pool consistent only with 3,200. **Plan Blackwell at 890, everything else at 1,650 (H100: 3,200).** |
| **B200's engine-realised KV cost: 890 or ≈2,340 B/token?** [b200.md §1.3](./b200.md) derives ≈2.3 KB/token from two same-image capacity deltas and a block-count check; [h200.md §1.4](./h200.md), [b300.md §1.4](./b300.md) and [gb300.md §1.4](./gb300.md) all reconcile their absolute pools at 890. | **Not a contradiction — two different quantities.** 890 B is the architectural record ([architecture.md §5.1](./architecture.md), reproduced to the byte against DeepSeek's published figure); the delta method measures the *paged allocation* cost. [rtx6000-pro.md §1.2](./rtx6000-pro.md) independently measures a **1.33–1.54× paging overhead** over the architectural figure from three reported pools, which is the mechanism. b200.md's own ⚠️ calls this its largest open item; treat 890 as the floor and size with the doc's own pessimistic column. |
| **MXFP4 on RTX PRO 6000: native W4A8 or Marlin W4A16?** [METHODOLOGY §8](../../METHODOLOGY.md) and `gpus/rtx6000-pro.md` §9g say Marlin; `quantization-formats.md` §4.2/§9.6 say native. | **Both true of different builds** ([rtx6000-pro.md §2](./rtx6000-pro.md)). Stock vLLM's `get_mxfp4_backend()` matches only `is_device_capability_family(100)` → Marlin — and stock vLLM is also the build that produces 2.3–4.0 tok/s. The measured community images select `b12x` and log no Marlin warning. **No A/B is published.** |
| **RTX PRO 6000 minimum: 4 or 8 cards?** `gpus/rtx6000-pro.md` §9g computes 8 from the resident checkpoint. | **4**, with the Engram offload that vLLM itself makes mandatory on H100 and B300: `307.53e9 / (86.40e9 − 4e9) = 3.73` ([rtx6000-pro.md §1.1](./rtx6000-pro.md)). Both published measurements run on four cards. |
| **Is A100 deployable at all?** `gpus/a100.md` §9, `inference-engines.md` §3.2 and `flash-attention.md` §16.1 all say no. | **Correct about every vendor-verified engine, wrong as a statement about the world** ([a100.md, headline](./a100.md)). A community fork plus a 24-file patch set runs the checkpoint on SM80 with a published sweep and four evals — on **A800**, not A100. |
| **B200 prefill: 123,750 or 9,267 tok/s/GPU?** `gpus/b200.md` §9.3 gives the former as an `est.` row. | **9,267, measured** from vLLM PR #56686's actual chunked-prefill step times ([b200.md §3.1](./b200.md)). The GEMM-only formula excludes the O(N²) indexer scan, top-k, Engram gathers and mHC mixing that dominate this model's prefill. `gpus/b200.md` §9.3 now carries the correction (gap X5, 2026-09-19). |
| **B300 workspace: 2–6 GB or 44–58 GB?** METHODOLOGY §3 suggests the former. | **44–58 GB/GPU on this pair**, derived by two independent routes from measured KV pools ([b300.md §1.4](./b300.md)) — the 4-copy mHC residual, an 8,192-token prefill batch and a 26-size CUDA-graph ladder. Plan with 50 GB. |

---

## Optimization impact

Drawn from each pair document's §2 (what runs) and §4 (what it is worth). **⚠️ = the effect
is stated but not measured for this pair on this GPU.**

| Optimization | H100 | H200 | B200 | B300 | GB300 | A100 | RTX PRO 6000 | MI355X |
|---|---|---|---|---|---|---|---|---|
| **Attention kernel generation** | `SPARSE_DSV41` only (blk 64). **No `MEGA_ATTN` fusion** — the leading candidate for the measured **2.11× roofline gap** ([h100.md §3.5a](./h100.md)) | same; at bs=1 the model is **launch-bound, not bandwidth-bound** — 2.78 ms vs a 1.68 ms roofline ([h200.md §3.4](./h200.md)) | **`MEGA_ATTN` native.** 700 TF sparse decode / 1,450 sparse prefill ([b200.md §2](./b200.md)) | `sm_103` satisfies `major==10`; "SM100 only" is a family statement, closed by FlashMLA PR #221 ([b300.md §2](./b300.md)) | `MEGA_ATTN` native; `est.` ~780/1,610 TF by 1.11× scaling from B200 ⚠️ ([gb300.md §2](./gb300.md)) | **custom community SM80 kernels**; the TileLang math is portable, a *tuned* kernel is what is missing ([a100.md §2](./a100.md)) | `FLASHINFER_MLA_SPARSE`; TileLang DSA **dies** on the 99 KB shared-memory ceiling ([rtx6000-pro.md §2](./rtx6000-pro.md)) | AITER sparse runs; **no gfx950 DSA indexer kernel is confirmed** — decides 1M viability ⚠️ ([mi355x.md §2](./mi355x.md)) |
| **Weight format executed** | Marlin W4A16 dequant — **memory win, no FLOPs win**; accuracy-neutral ([h100.md §2, §3.4](./h100.md)) | same; expert GEMMs run at **BF16 989.5 TF**, not FP8's 1,979 ([h200.md §2](./h200.md)) | **native W4A8** on the FP8 line; no dequant anywhere in the path ([b200.md §6.4](./b200.md)) | **W4A8 → B300's entire 1.5× FP4 advantage is unavailable on the default path** ([b300.md §2.1](./b300.md)) | native W4A8; NVFP4 build validated here but **no speedup published, +17 GB** ([gb300.md §6.4](./gb300.md)) | Marlin W4A16 **+ FP8→W8A16**; nothing executes 4- or 8-bit math ([a100.md §2](./a100.md)) | native W4A8 via b12x ⚠️ disputed (§2 box) | **native CK a8w4**; the only target where the checkpoint's own format is native end to end ([mi355x.md §0](./mi355x.md)) |
| **KV quantization** | **BF16 3,200 B/tok** — 3.6× the advertised cost, the most expensive Hopper gap. `fp8_ds_mla` opt-in = **+66 % seats** ⚠️ untested ([h100.md §2](./h100.md)) | **FP8 1,650 B/tok**; FP4 unreachable, 1.85× the cache ([h200.md §2](./h200.md)) | **FP4 890 B/tok native** ⚠️ but pools imply ≈2,340 ([b200.md §1.3](./b200.md)) | **FP4 890 B/tok native**; pool arithmetic reconciles at 890 ([b300.md §1.4](./b300.md)) | **FP4 890 B/tok native**; 1M ctx = 890 MiB/seq ([gb300.md §2](./gb300.md)) | FP8 **storage-only** — no FA3, so K/V dequantise to FP16 pre-MMA and per-head scales are unavailable ([a100.md §2](./a100.md)) | FP8 **mandatory** by the SM12x dtype gate; NVFP4 KV kernel **exists** (FlashInfer PR #4955) but no engine routes to it ([rtx6000-pro.md §2](./rtx6000-pro.md)) | FP8 1,650; **no gfx950 FP4-KV kernel** ([mi355x.md §1.1](./mi355x.md)) |
| **Speculative decoding (DSpark γ=5)** | **2.82 measured** on sm_90 (H20); per-position 0.72/0.52/0.30/0.18/**0.096** — positions 4–5 nearly wasted ([h100.md §4.4](./h100.md)) | unmeasured; only a **lower bound `a ≥ 1.15`** derived from an otherwise-impossible MBU ([h200.md §3.4](./h200.md)) | every published row uses the **synthetic constant 3.51** ([b200.md §6.2](./b200.md)) | **3.13× output per byte**; removing it takes $/1M from $1.50 → $4.69 ([b300.md §4.4](./b300.md)) | `est.` 3.07×; but MTP on GB300 gave DeepSeek-R1 **−0.9 % TPS/GPU, +87 % TPS/user** — speculation buys *latency* here ([gb300.md §4.4](./gb300.md)) | inside every measured number and **not recovering launch overhead** — `k` > 1 at every batch ([a100.md §4.3](./a100.md)) | **~2.5× and the largest cost lever**, but acceptance is reported three incompatible ways ([rtx6000-pro.md §6.9](./rtx6000-pro.md)) | **adaptive verification refused on ROCm**; without DSpark the TPOT ≤ 50 ms ceiling falls to **batch 39** ([mi355x.md §4.5](./mi355x.md)) |
| **Prefix caching** | **93.8 % hit at conc 20**; the conc-24→28 **cliff is prefix-cache eviction**, not a Marlin knee — output collapses 4.6× ([h100.md §3.5b](./h100.md)) | 93.3 % hit; only **−20 % blended** — a TTFT lever (2,142 → 206 ms), not a cost lever ([h200.md §4.3](./h200.md)) | 95.1–97.5 % hit; cached input **$0.018/1M** vs $0.180 uncached ([b200.md §4.2](./b200.md)) | 90.2–97.0 % hit → effective input **$0.036/1M** ([b300.md §4.4](./b300.md)) | 93.5–97.5 % hit → **6.3× input-cost reduction, the single largest lever on this pair** ([gb300.md §4.4](./gb300.md)) | **5.3× cheaper input** at h=0.90 — but ⚠️ on *unstructured* traffic APC measured **−36.7 % throughput** on A100 ([a100.md §4.3](./a100.md)) | 87 % of 18.5M real agentic prompt tokens served from cache; warm prefill **8–11×** ([rtx6000-pro.md §2](./rtx6000-pro.md)) | **99.7–100 % hit**. SGLang on MI350X must `--disable-radix-cache` and forfeits it entirely ([mi355x.md §4.5](./mi355x.md)) |
| **EP / PD disaggregation** | **wide-EP impossible** (8-GPU NVLink domain); PD needs 2 nodes; ⚠️ #51326 corrupts output at TP8+EP ([h100.md §5.2–5.3](./h100.md)) | EP8 is SGLang's shape and it measures **15.7× slower** than vLLM TP8 — the EP argument is theoretical here ([h200.md §5.2](./h200.md)) | EP ≤ 8 in-node; PD verified only on **GB200 NVL4, text-only** ([b200.md §5.1](./b200.md)) | wide-EP *"runs badly"* on an 8-GPU domain; **four TP2 replicas beat one TP8 by 1.48×** ([b300.md §5.1](./b300.md)) | **wide-EP does not pay**: 384 experts ∤ 72, and **MoE TP4 beat EP4 by 2.22 %** at bs=1 ([gb300.md §5.2](./gb300.md)) | EP8 over NVSwitch native; **custom EP8 AG/RS = +1.52 %**; PD impossible (4 GPUs cannot hold the model) ([a100.md §2, §5](./a100.md)) | PD declared **`unsupported`**; EP4 already past expert-read saturation ([rtx6000-pro.md §5.2](./rtx6000-pro.md)) | Dynamo has **no ROCm support**; the CED split is a textbook PD candidate that this part cannot exercise ([mi355x.md §5.2](./mi355x.md)) |
| **Engram placement** | **host offload mandatory**; 48 random gathers/token over PCIe; a 5.3 ms/step hole at batch 1 is unexplained ⚠️ ([h100.md §2, §3.5a](./h100.md)) | **resident — the one thing H200 buys over H100** ([h200.md §1.2](./h200.md)) | resident at TP8, or host UVA (what the benchmarks use); host offload gave **+36 % KV capacity** on GB300 ([b200.md §2](./b200.md)) | **offload arithmetically required at TP2**; at TP4 the tables **shard and stay resident** ([b300.md §1.4](./b300.md)) | **900 GB/s Grace C2C → 10.8 µs/step. The one platform where the offload is genuinely free** ([gb300.md §5.3](./gb300.md)) | mandatory offload; buys **24× the KV budget** (1.03 → 24.64 GiB/GPU on the resolved 80 GB decimal basis; 5×, 6.0 → 29.6, on the demoted GiB one) ([a100.md §1.3](./a100.md)) | **RAM vs NVMe = 2.7× prefill**, the largest single lever after CUDA graphs ([rtx6000-pro.md §3.4](./rtx6000-pro.md)) | **resident, no offload needed at TP4** — the clearest capacity win of the 288 GB part ([mi355x.md §2](./mi355x.md)) |

Three cross-cutting findings the table cannot hold:

- **CUDA graphs are worth ~200× on RTX PRO 6000** — 740–830 tok/s with, **2.3–4.0 tok/s** without ([rtx6000-pro.md §2](./rtx6000-pro.md)). On ROCm `VLLM_USE_BREAKABLE_CUDAGRAPH=1` is mandatory or capture dies ([mi355x.md §2](./mi355x.md)).
- **Kernel work dominates hardware choice at low batch.** SGLang moved 4× GB300 from **35.2 → 873.63 tok/s** at bs=1 in days, by kernels alone ([gb300.md §3.4b](./gb300.md)). vLLM-ROCm cut **14,020 → 2,850 kernel launches per decode step**, 85 % of them in the mHC block ([mi355x.md §3.2](./mi355x.md)). Every number in this guide has a shelf life measured in weeks.
- **Reasoning effort is worth more than any kernel flag.** Effort 25 → 100 lifts the 8-benchmark average 67.1 → 76.3 % at *"roughly 2.5× more output tokens"*; **effort 60–80 recovers most of it at under half the budget** ([architecture.md §10.5](./architecture.md)). vLLM defaults thinking **on at effort 50**, SGLang defaults it **off**, and the DeepSeek API maps the labels differently again — a silent 2.5× cost difference between engines ([b300.md §6.19](./b300.md)).

---

## Cost vs vendor API

DeepSeek first-party `deepseek-flash` = DeepSeek-V4.1-Flash, per 1M tokens, off-peak / peak
([architecture.md §11](./architecture.md)): **output $0.60 / $1.20 · input cache-miss
$0.15 / $0.30 · input cache-hit $0.003 / $0.006.** The cached-input ratio is **2 %**, not
METHODOLOGY's generic 10 % — use the vendor's own ratio for API comparisons, giving an API
blended price of **$0.2074/1M** at the 75/25 mix.

**Break-even utilisation** = `our $/1M ÷ API $/1M` at the stated operating point. Above
100 % means *no utilisation, however high, makes self-hosting cheaper*.

| GPU | Price row used | Interactive break-even vs $0.60/M | Max-throughput break-even | Where it *does* close |
|---|---|---|---|---|
| **[H100](./h100.md) §4.5** | $3.20 Hyperstack – $6.88 AWS p5 | **undefined — the node cannot reach the required throughput at 100 % utilisation.** Measured misses by **16.5×–35.5×** | misses by 1.4× (res1y) to 3.4× (high) | only against DeepSeek's **peak** rate: 80 % at the low tier, on a batch-256 extrapolation |
| **[H200](./h200.md) §4.4** | $3.99 – $7.912 AWS p5en | **146 %** (low) / **102 %** (res1y) — unreachable; measured **579 %** | **66 %** (low) / **46 %** (res1y) ⚠️ extrapolated | **colo-amortised $1.50/GPU-hr → 55 %.** Owning the silicon is what makes this work |
| **[B200](./b200.md) §4.3** | $5.10 res – $6.00 – $14.00 OCI | 66 % ($5.10) / 77 % ($6.00) / **180 % — impossible** at OCI | **50 % / 59 % / 138 %** | cheapest reputable neocloud at **half to three-quarters** utilisation — needs a continuously-loaded fleet |
| **[B300](./b300.md) §4.5** | $7.40 Hyperstack – $15.00 OCI | **never at any rented rate** (1,373 tok/s/GPU vs the 3,426 Hyperstack needs) | never | **owned-at-scale TCO $2.26/GPU-hr → 76 %**; CoreWeave spot $4.48 vs peak rates → 75.5 % |
| **[GB300](./gb300.md) §4.5** | **$18.00 OCI — the only published rate anywhere** | **939 % — impossible.** 9.4× short of the 8,333 tok/s/GPU needed | — | SemiAnalysis volume $2.31 → **121 %** (still impossible); **5-yr on-prem $1.75 → 91 %** ⚠️ |
| **[A100](./a100.md) §4.4** | $1.59 RunPod – $3.431 AWS p4de | **194 %** (low) / **166 %** (res1y) off-peak — never | same (saturates at C128) | **peak** API rates: 97 % (low), **83 % (res1y)**. Owned used fleet ≈$0.58/GPU-hr → beats the API off-peak |
| **[RTX PRO 6000](./rtx6000-pro.md) §4.3** | $1.80 Nebius – $4.143 AWS g7e | **never** — 2.5–3.5× the API at 100 % utilisation; **22 % of break-even** on measured throughput | **~100 %** at $1.80, **~73 %** at $1.30 reserved ⚠️ at 32× the measured concurrency | nowhere defensible; run it for sovereignty, not price |
| **[MI355X](./mi355x.md) §4.6** | **$8.60 OCI — `low` and `high` are the same row** | **32 % of break-even** — the API is 3.1× cheaper; measured point 6.5 % | 56 % | **colo `est.` $1.42/GPU-hr → 196 % (S1) and 341 % (S4)** — self-host wins comfortably on owned hardware |

**The pattern is consistent across eight independent analyses: DeepSeek's $0.60/1M output
is below what a rented GPU of any kind can deliver.** It is reachable only at
owned-or-amortised cost ($1.42–$2.31/GPU-hr), and it inverts entirely on input-heavy
agentic traffic — B300 serves **$0.0198/1M per total token** at 95.6 % cache hit
([b300.md §4.3](./b300.md)) and B200 **$0.018–$0.042/1M** ([b200.md §4.1](./b200.md)),
an order of magnitude *under* the API. The reasons to self-host this model are data
residency, the MIT licence on both repo and weights, freedom from the 2,500-request
concurrency cap, and 1M context at fixed cost — not $/token.

---

## Recommendation

**For this repo's node types — 8× B300 primary, AWS p6 family.**

**What to run.** The primary node maps directly onto the best-measured configuration in the
whole roster.

- **Interactive / agentic serving: two TP4 servers per 8× B300 node.** vLLM's own recipe says it: *"The command builder defaults to TP2 for B300. **For high interactivity, deploy the model using TP4**"* ([b300.md §5.2](./b300.md)). At TP4 the Engram tables **shard and stay resident** — skip `--engram-config '{"cpu_offload":true}'`, which is only required at TP2, and avoid the PCIe gather path that cost the DGX Spark deployment 5.9–7.8 ms/step ([b300.md §1.4](./b300.md)).
- **Batch / throughput serving: four independent TP2 replicas per node.** 2,656 vs 1,798 decode-only tok/s/GPU — a **1.48× throughput-per-GPU win** — at the cost of long-prompt TTFT, which blew up to 35.2 s on 72K prompts at c128. **Cap prompt length on TP2 replicas or route long-context traffic to a TP4 server** ([b300.md §5.1](./b300.md)).
- **Do not cross nodes.** 2,144 GB holds the checkpoint four times over. Wide-EP *"runs badly"* on an 8-GPU domain and PD disaggregation *"is a thousand-GPU problem"* with a measured 20–30 % penalty on small deployments ([b300.md §5.1](./b300.md)).
- **Do not reach for the NVFP4 build.** It is +17 GB, accuracy-neutral, validated only on GB300, and has **no published speedup on any hardware** ([b300.md §6.3](./b300.md), [gb300.md §6.4](./gb300.md)).

Launch, verbatim from vLLM's B300 profile with the TP4 change and DSpark added
([b300.md §5.2](./b300.md)):

```bash
# image: vllm/vllm-openai:nightly  (pin the digest; nothing is in a numbered release)
VLLM_ENGINE_READY_TIMEOUT_S=3600 VLLM_USE_RUST_FRONTEND=1 \
vllm serve deepseek-ai/DeepSeek-V4.1-Flash \
  --tokenizer-mode deepseek_v41 \
  --tensor-parallel-size 4 \
  --max-cudagraph-capture-size 8190 \
  --max-num-batched-tokens 8192 \
  --max-num-seqs 256 \
  --tool-call-parser deepseek_v41 --enable-auto-tool-choice \
  --reasoning-parser deepseek_v41 \
  --mm-encoder-tp-mode data \
  --speculative-config '{"method":"dspark","num_speculative_tokens":5,"draft_sample_method":"probabilistic","rejection_sample_method":"block","enable_adaptive_verification":true}'
```

**On the AWS p6 family.** `p6-b300.48xlarge` is the same silicon at **$17.802/GPU-hr list**
versus Hyperstack's $7.40 — a 2.4× hyperscaler premium that moves interactive cost from
$1.50 to $3.60 per 1M output ([b300.md §4.1, §4.3](./b300.md)). It carries
**8 × 3.84 TB local NVMe and 4 TB system RAM**, so the whole checkpoint plus the Engram
tables stage locally ([b300.md §5.3](./b300.md)); spot at $5.591 and Capacity Blocks at
$14.04 are the rows to negotiate against. `p6-b200.48xlarge` at $14.242 is the fallback and
runs the same stack at TP4/TP8 ([b200.md §4](./b200.md)) — but note B200's **capacity cliff
at concurrency 128 on 100K-token prompts** (TTFT 48 s, 29 % of B300's output), which is
precisely the agentic shape this repo cares about ([b200.md §3.4b](./b200.md)). On the
Hopper p5 family, expect **1/12.9 of a 2-GPU B300** ([h100.md §3.4](./h100.md)).

**What to benchmark first, in order of expected payoff.**

1. **DSpark acceptance on your own traffic.** `vllm:spec_decode_num_{accepted,draft}_tokens_total`. Every cost figure in this guide rides on it, the widely-quoted **3.51 is a synthetic benchmark constant** the harness is *told* to assume, and the only real measurement of this checkpoint anywhere is a community **3.57** on different silicon with a 1.88–5.92 spread by workload type ([architecture.md §7.3](./architecture.md)). vLLM says so outright: *"measure it on your own traffic before sizing a deployment around it."*
2. **TP4 batch 32 versus batch 128 at your SLO.** [b300.md's own audit](./b300.md) flags that decode-only throughput **peaks at batch 32** (1,798 tok/s/GPU, TPOT 4.45 ms) and *falls* at 64/128, i.e. batch 32 dominates batch 128 on both throughput and latency while both clear the 50 ms SLO. The document's cost tables are built on c128. **This is a free 1.3× if it reproduces.**
3. **TP2 versus TP4 versus four TP2 replicas**, on your real prompt-length distribution. The 1.48× TP2 advantage and the 35 s TP2 TTFT blow-up are both measured; which one you get depends entirely on your prompt mix.
4. **Whether the expert GEMM is really W4A8.** `cuobjdump -sass` on the expert GEMM, and an A/B of SGLang's `--enable-w4a4-mxfp4-megamoe`. If W4A4 works, B300's 13,500 TFLOPS FP4 line — its **entire** advantage over B200 — becomes available, and nobody has measured it on any GPU for this model ([b300.md §6.1–6.2](./b300.md)).
5. **Prefix-cache hit rate and the eviction cliff.** The B300 rows measure 90–97 %; H100's identical harness shows output collapsing **4.6×** when the pool can no longer hold retained prefixes ([h100.md §3.5b](./h100.md)). Instrument `server_gpu_cache_hit_rate` against concurrency and find your own knee before it finds you.
6. **Reasoning effort 60 / 75 / 100 on your eval set.** A 2.5× output-token swing for the last ~9 points of Pass@1 ([architecture.md §10.5](./architecture.md)) — and vLLM and SGLang disagree on the default.
7. **Vision, if you need it.** **Every published measurement of this model on every GPU in this roster is text-only**, usually `--language-model-only`, for a model whose pipeline tag is `image-text-to-text`.

---

## Open questions

Consolidated from all eight pair documents' **⚠️ TO BE VERIFIED** lists, deduplicated and
ordered by impact on a B300 deployment decision. **24 items.**

### Blocking a sizing decision

1. **DSpark acceptance is unmeasured on every GPU in this roster.** 3.51 is a synthetic harness constant; 5.5 in the SGLang GB300 post is an explicit simulation; **3.57 (DGX Spark, GB10) is the only real measurement of this checkpoint**, on different silicon. Swings $/1M output by up to 3.5×, and on MI355X it is the *sole* reason any batch above 39 meets the interactive SLO. ([architecture.md §7.3](./architecture.md); every pair doc.)
2. **Is the expert GEMM W4A8 or W4A4?** If W4A8 (the inference from PTX `.kind` families plus engine labelling), B300's entire 1.5× FP4 advantage over B200 is unavailable on the default path — which the B300 ≡ B200 measurement corroborates. Settle with `cuobjdump -sass`. ([b300.md §2.1, §6.1](./b300.md).)
3. **The 5× gap between roofline and measurement at batch 128 is unexplained.** No vendor or engine has published a kernel-level profile of CSA2 decode on any GPU. Measured MFU is 0.0217 (decode) / 0.0307 (prefill) against METHODOLOGY's 0.25–0.40 bands — an 8–18× over-prediction that is an **architecture** property, not a B300 one (MI355X shows ~12.6×, H100 2.11×, H200 3–10×). ([b300.md §3.5](./b300.md), [architecture.md §12.9](./architecture.md).)
4. **Max-concurrency: replicated or sharded KV?** `num_key_value_heads: 1` means the MLA latent cannot split under plain TP, and every pair document takes the replicated reading as binding — a **4–8× difference**. [b300.md §1.4](./b300.md), [h100.md §1.4](./h100.md) and [h200.md §1.3](./h200.md) each confirm it from measured pools; `serving-optimizations.md` §7.1 still lists it open. DCP/DP-attention would change it and is **never exercised for this model on any GPU**.
5. **The fixed per-sequence SWA state: 2.77 MiB or 5.38 MiB?** The reference implementation stores fp8-rounded values in a **BF16 container** and drops the scales. **1.94× on a term that dominates below ~6.3 K context.** No vLLM or SGLang document states the dtype. ([architecture.md §5.3](./architecture.md).)
6. **B200's engine-realised KV cost (≈2,340 B/token) is undecomposed** — FP8 by default, SWA inside the paged pool, or block padding? Moves every B200 max-concurrency figure by 2.6×. RTX PRO 6000 independently measures a 1.33–1.54× paging overhead, which is the likeliest mechanism. ([b200.md §1.3, §6.5](./b200.md), [rtx6000-pro.md §1.2](./rtx6000-pro.md).)
7. **Nothing is in a numbered release.** `min_vllm_version: 0.30.0` does not exist (latest 0.29.0); SGLang is a preview image; the measured GB300 runs used **three different images in six days**. You are pinning a nightly digest in production, with no Blackwell CI behind it (vLLM's own RFC: CUDA CI covers L4 and H100 only). ([architecture.md §8.1](./architecture.md), [b300.md §6.12–6.13](./b300.md).)

### Material to cost and capacity

8. **No 1M-context measurement exists on any hardware.** At 1M the four Full-mode indexer layers scan 170 B/token every step — 5.70 GiB/step at batch 32 — and dominate everything else. vLLM's own warning for the sibling model: *"1M context is advertised, not measured."* H100 seats **7 sequences per node** at BF16 KV and would need ~1.1 s/step for one. ([architecture.md §12.7](./architecture.md), [h100.md §6.7](./h100.md), [gb300.md §6.1](./gb300.md).)
9. **The B300 interactive operating point may be suboptimal.** [b300.md's audit log](./b300.md) flags that batch 32 dominates batch 128 on both throughput *and* latency while both clear the SLO; the cost tables are built on c128. Worth ~1.3×.
10. **Whether NVFP4 beats MXFP4 on Blackwell.** NVIDIA published accuracy parity, a +17 GB checkpoint, and **no throughput comparison whatsoever**, on any hardware. Its GB300 validation did not exercise DSpark. ([gb300.md §6.4](./gb300.md), [b200.md §6.22](./b200.md).)
11. **Does `--enable-w4a4-mxfp4-megamoe` help on B300?** The only documented route to 13,500 TFLOPS with this checkpoint; unmeasured everywhere. ([b300.md §6.2](./b300.md).)
12. **No prefill throughput measurement isolates this model on most GPUs.** MI355X's input cost carries a **20× band** ($0.032–$0.692/1M) because every published run sits at 94–100 % cache hit; A100's is 2.9× wide. ([mi355x.md §4.3](./mi355x.md), [a100.md §6.10](./a100.md).)
13. **The B300 workspace figure (44–58 GB/GPU) is derived, never printed by an engine log.** Two independent routes agree, but it is inferred from measured KV pools. ([b300.md §6.23](./b300.md).)
14. **No measurement at the S1/S2/S3 shapes on B300 or GB300.** All 15 B300 rows and all 8 GB300 rows are agentic traces with 72K–335K prompts at 90–97 % cache hit. The decode-rate transfer to 4K prompts is argued (the KV term is 2–4 % of the step) but not measured. ([b300.md §6.9](./b300.md).)
15. **`S = 1` state slots assumed everywhere.** If an engine allocates ring-buffer slots for the 5 DSpark draft positions, multiply the fixed state. ([architecture.md §5.3](./architecture.md).)

### Accuracy and correctness

16. **SWA Bounded Replay has no published accuracy delta on any hardware.** DeepSeek says it *"barely compromises response quality"* and gives no number — and it is the mechanism that makes the 890 B/token cache and the 93–97 % prefix-cache economics work, so on agentic traffic you are almost certainly running it. The best evidence available: LMSYS measured **identical AIME 2026 pass@1** (453/480) with replay off and on, on 4× GB300. ([architecture.md §2.3](./architecture.md), [b200.md §6.21](./b200.md).)
17. **FP8/FP4 KV accuracy under long context is unevaluated.** `quantization-formats.md` §8.3 warns *"sliding-window layers are more sensitive to KV-cache quantization"* — and **every one of this model's 43 layers carries a 128-token SWA component.** `--kv-cache-dtype-skip-layers sliding_window` exists for exactly this. No eval isolates it. ([b300.md §6.5](./b300.md), [h200.md §6.3](./h200.md).)
18. **Output is not bitwise stable.** `--enable-deterministic-inference` is **refused** on SGLang's `dsv4` backend; two default kernels are shape-guarded to a single token. ([b300.md §6.17](./b300.md).)
19. **NVFP4 MoE on Hopper has live correctness bugs** — garbage output at conc 1 and a `fused_marlin_moe` illegal memory access at conc ≈ 8 (vLLM #49070), on exactly the Marlin FP4 MoE path this model uses. Separately, **#51326** corrupts output on 8×H100 at TP8 + expert parallelism; every working measured H100 row runs `decode_ep: 1`. ([h100.md §6.13–6.14](./h100.md).)

### Unmeasured paths

20. **Vision is unmeasured on every GPU in this roster**, for a model whose pipeline tag is `image-text-to-text`. A max-resolution image costs ≈18.86 TFLOP — as much as ~1,200 prefill tokens — for 1,024 tokens of context. On RTX PRO 6000 the **six-frame video test FAILED** outright and multi-image is unresolved; on A100 the vision tower is an open blocker. ([architecture.md §6.4](./architecture.md), [rtx6000-pro.md §6.16](./rtx6000-pro.md), [a100.md §6.15](./a100.md).)
21. **PD disaggregation is verified only on GB200 NVL4, text-only**, for a model whose inverted prefill:decode ratio (7.89 B vs 16.13 B active) makes it a textbook candidate. DSpark must run in both pools or the transferred KV is incompatible; SGLang cannot combine PD with speculation at all. ([gb300.md §6.9](./gb300.md), [b200.md §5.1](./b200.md).)
22. **How the Engram tables partition under DP-attention.** If they replicate, **vLLM's own published `single_node_dep` GB300 command does not fit** without `cpu_offload`, and even DP8 leaves less headroom than the activation workspace needs. No engine document states it. On vLLM-ROCm the sharding policy is undocumented entirely — if it replicates, MI355X TP4 does not fit. ([gb300.md §1.5](./gb300.md), [mi355x.md §6.4](./mi355x.md).)
23. **Why SGLang is 8–16× slower than vLLM on H100, H200 and B200** on identical hardware, traces and harness, in the same week. Cause unpublished. It kills the wide-EP argument on Hopper until resolved. ([h200.md §6.3](./h200.md), [b200.md §3.4a](./b200.md), [h100.md §3.5c](./h100.md).)
24. **No gfx950 DSA indexer kernel is confirmed, and no MLPerf result exists for this model on any hardware.** AITER ships blockwise sparse Sage attention but no primary source confirms a DSA indexer kernel — which decides MI355X's 1M-context viability. Separately, the widely-repeated *"MI355X is 14.8× worse per dollar than an H200"* claim **does not survive the harness's own rows**, which put it at 1.9× H200 on throughput and ~2.2× *better* per dollar. ([mi355x.md §3.4vi, §6.2](./mi355x.md).)

---

## Sources

Every claim in this document is linked inline to the pair document that produced it. The
pair documents carry the primary sources, the `python3` derivations and their own audit
logs.

**This tree**
- [`research/METHODOLOGY.md`](../../METHODOLOGY.md) — §1 bytes/param, §2 KV, §3 fit, §4 roofline, §6 cost, §8 pinned inputs
- [`architecture.md`](./architecture.md) — §1.2 checkpoint dtypes, §2 architecture, §3.3 active params, §4/§4.1 weight memory and Engram, §5.1–5.4 KV, §6 compute profile, §7 DSpark, §8 engine matrix, §9 quantised variants, §10 published benchmarks, §11 API pricing, §12 open questions
- Pair documents: [h100.md](./h100.md) · [h200.md](./h200.md) · [b200.md](./b200.md) · [b300.md](./b300.md) · [gb300.md](./gb300.md) · [a100.md](./a100.md) · [rtx6000-pro.md](./rtx6000-pro.md) · [mi355x.md](./mi355x.md)
- GPU foundation docs: [`gpus/h100.md`](../../gpus/h100.md) · [`gpus/h200.md`](../../gpus/h200.md) · [`gpus/b200.md`](../../gpus/b200.md) · [`gpus/b300.md`](../../gpus/b300.md) · [`gpus/gb300.md`](../../gpus/gb300.md) · [`gpus/a100.md`](../../gpus/a100.md) · [`gpus/rtx6000-pro.md`](../../gpus/rtx6000-pro.md) · [`gpus/mi355x.md`](../../gpus/mi355x.md)
- Cross-cutting: [`flash-attention.md`](../../cross-cutting/flash-attention.md) · [`quantization-formats.md`](../../cross-cutting/quantization-formats.md) · [`inference-engines.md`](../../cross-cutting/inference-engines.md) · [`serving-optimizations.md`](../../cross-cutting/serving-optimizations.md) · [`cloud-pricing.md`](../../cross-cutting/cloud-pricing.md) · [`inferencex-api.md`](../../cross-cutting/inferencex-api.md)

**Measured data behind the comparison table**, all re-fetched by the pair documents on
2026-09-19: the [InferenceX / SemiAnalysis benchmark API](https://inferencex.semianalysis.com/api/v1/benchmarks?model=DeepSeek-V4.1-Flash)
(98 rows on the same agentic-trace harness, covering H100/H200/B200/B300/GB200/GB300/MI3xx)
and its [evaluations endpoint](https://inferencex.semianalysis.com/api/v1/evaluations);
[vLLM PR #56686](https://github.com/vllm-project/vllm/pull/56686) (8× B200 TP8);
[InferenceX PR #3058](https://github.com/SemiAnalysisAI/InferenceX/pull/3058) and
[#3068](https://github.com/SemiAnalysisAI/InferenceX/pull/3068) (MI355X);
the [SGLang GB300 kernel campaign](https://www.sglang.io/blog/deepseek-v4.1-flash-kernel-optimization);
[LMSYS](https://www.lmsys.org/blog/2026-09-10-deepseek-v41);
[Tokha233/deepseek-v4.1-flash-a100-turbo](https://github.com/Tokha233/deepseek-v4.1-flash-a100-turbo) (A800);
[local-inference-lab/rtx6kpro](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/deepseek-v4.1-flash.md)
and [0xSero](https://github.com/0xSero/deepseek-v4.1-flash-4x-rtx-pro-6000) (RTX PRO 6000);
[vLLM recipes](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash) and its per-hardware
profiles; [DeepSeek API pricing](https://api-docs.deepseek.com/quick_start/pricing).

**No MLPerf Inference result exists for DeepSeek-V4.1-Flash on any hardware** — the model
was released 2026-09-10 ([architecture.md §10](./architecture.md)).

---

## Sweep log

- **2026-09-19 — gap `C1-a100-capacity-basis` CLOSED at 80 GB decimal, A100 row recut.** `usable_hbm` = 0.90 × 80 GB = 72.0 GB = 67.06 GiB/GPU is now the single A100 planning basis tree-wide; `nvidia-smi`'s 81,920 MiB = 80 GiB = 85.90 GB is kept only as a labelled ⚠️ TO BE VERIFIED +7.4 % sensitivity ([METHODOLOGY §8](../../METHODOLOGY.md#gpus), [gpus/a100.md §2](../../gpus/a100.md)). [`a100.md`](./a100.md) had planned on the GiB reading and was recomputed: the A100 row's concurrency moves **15,473 → 12,886** @8K and **1,159 → 965** @128K, and Engram host-offload goes from a 5× to a **24×** KV-budget lever. **No throughput, latency, cost or fit verdict changed** — the measured operating point is batch 128, far under either cap, and the 8-GPU minimum stands.

- **2026-09-19, final consistency pass.** §Cross-GPU's B200 row quoted `$0.39–$1.08` / `$0.30–$0.83`, picking the `res1y` tier as the band's `low` end — inconsistent with [b200.md §0](./b200.md) (itself just corrected the same way) and with `matrix/pairs.json`, which was already correct at $0.462–$1.079. Recut to **$0.46–$1.08** / **$0.35–$0.83**, blended **$0.19–$0.44**.
