# nvidia/DeepSeek-V4.1-Flash-NVFP4 — GPU selection guide

> Research date **2026-09-19**. Consolidated from [`architecture.md`](architecture.md) and the
> eight pair documents ([h100](h100.md) · [h200](h200.md) · [b200](b200.md) · [b300](b300.md) ·
> [gb300](gb300.md) · [a100](a100.md) · [rtx6000-pro](rtx6000-pro.md) · [mi355x](mi355x.md)),
> which were written and audited in parallel. Formulas, markers and pinned inputs follow
> [`research/METHODOLOGY.md`](../../METHODOLOGY.md). **No number here is new** — every figure is
> linked to the pair document it came from. Where two pair docs disagree on an input, the
> disagreement is stated and adjudicated against METHODOLOGY §8 (see
> [Open questions](#open-questions)).

## Summary

1. **What it needs:** 527.27 GB of weights — *larger* than the 510.29 GB base `deepseek-ai/DeepSeek-V4.1-Flash` by +16.99 GB (+3.33 %) — plus 890 B/token of KV and a 2.58–2.77 MiB SWA ring per sequence ([architecture.md §1.1, §3.4, §5.1–5.3](architecture.md)).
2. **What runs it today:** only **Blackwell datacentre silicon**. GB300 is the single validated pair (NVIDIA tested this checkpoint, TP4, vLLM + SGLang) ([gb300.md §0](gb300.md)); B200 and B300 execute NVFP4 natively but neither is vendor-validated ([b200.md §0](b200.md), [b300.md §0](b300.md)).
3. **Best for interactive serving: 4 × GB300, TP4** — 4,034 out tok/s/GPU at TPOT 15.9 ms, the only configuration with a vendor recipe and the only one where `nvfp4_ds_mla` gives the architectural 890 B/token cache ([gb300.md §3.2, §4.2](gb300.md)).
4. **Best cost per token: 4 × B200, TP4** — $0.048–$0.112 per 1M output at the KV ceiling, $0.565–$1.319 at the defensible concurrency-256 point, on the cheapest Blackwell GPU-hour with a `verified` vLLM recipe ([b200.md §4.2, §4.4](b200.md)).
5. **Cheapest viable:** 8 × RTX PRO 6000 Server Edition at $1.30–$1.80/GPU-h is the lowest sticker price that fits ([rtx6000-pro.md §4](rtx6000-pro.md)) — but it is unvalidated, defaults to a Marlin dequant path, and 4 GPUs leave 1.27 GB of KV. On evidence, **4 × B200 is the cheapest configuration anyone should actually deploy**.
6. **Not runnable:** **A100** (no `sm_80` attention backend for `DeepseekV41ForCausalLM` in any engine, [a100.md §0](a100.md)); **MI355X** (CDNA4 implements MXFP4, not NVFP4; the only reachable path is per-step BF16 emulation, [mi355x.md §0, §2](mi355x.md)).
7. **Runnable but not recommended:** **H100 / H200** load it only through Marlin W4A16 — 4-bit storage kept, FP4 math lost — with an open correctness bug (vLLM #49070) and a Marlin tile-alignment hazard at TP8 ([h100.md §0, §6](h100.md), [h200.md §0, §6.1](h200.md)).
8. **The finding that governs everything:** NVIDIA publishes **no throughput, TTFT or TPOT figure for this checkpoint on any GPU**, and its accuracy table is a wash (4 wins / 2 losses, all within ±1.5 points) ([architecture.md §0, §10.1–10.2](architecture.md)). What it buys is group-16 accuracy neutrality on Blackwell, not memory and not speed.
9. **What it costs:** +5.5–5.9 % decode bytes per step at any batch ≥ 32, on a decode that is memory-bound on every GPU studied — a direct 2–5.7 % TPOT penalty with no compensating term, everywhere ([gb300.md §3.4](gb300.md), [b200.md §6.1](b200.md), [b300.md §0](b300.md), [h200.md §3.5](h200.md), [mi355x.md §3.1](mi355x.md)).
10. **Therefore: all eight pair documents independently recommend the base MXFP4 checkpoint over this one.** Use `nvidia/DeepSeek-V4.1-Flash-NVFP4` only if you specifically want NVIDIA's calibration and its "all 16,986,931,200 weight blocks passed lossless conversion" audit behind a Blackwell-only fleet.

## Cross-GPU comparison

All rows are `est.` unless the pair doc marks otherwise. Max-concurrency figures use each pair
doc's **binding** reading (KV replicated per TP rank, since `num_key_value_heads = 1`), at the
KV dtype that GPU can actually execute. Interactive = S1 (4K in / 512 out, TPOT ≤ 50 ms);
max-throughput = S4 (no SLO).

| GPU | Runnable today (engine) | Min GPUs | Recommended | Executed weight format | Attention kernel | Max conc @8K | @128K | Interactive tok/s/GPU (TPOT) | Max-thr tok/s/GPU | $/1M out interactive (low–high) | $/1M out max-thr (low–high) | Blended $/1M | Confidence |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **GB300 NVL72** | ✅ **yes — the only validated pair.** vLLM `deepseekv41-flash-0909`; SGLang `dev-cu13-dsv41` @ `da64c5cb` ([gb300.md §0](gb300.md)) | 4 | **4 × TP4** (8 × TP8 for KV headroom; rack = 18 replicas of 4) | **native NVFP4 W4A4** (58 % of bytes) + MXFP8/FP8 + MXFP4 MTP | `FLASHMLA_MEGA_ATTN_DSV41` → `nvfp4_ds_mla`, **890 B/token** | 11,113 | 947 | **4,034** (15.9 ms) | 14,060 (18.2 ms)ᵃ | **$1.24** (OCI $18, low=high); $0.159–$0.344 at SA anchors | $0.356; $0.046–$0.099 | $0.331; $0.042–$0.092 | estimate |
| **B300 HGX / DGX / p6-b300** | ⚠️ loads and executes NVFP4 natively on `sm_103`, but **no vendor validation**, not in vLLM's recipe catalogue, 3 `sm_103` kernel faults on record ([b300.md §0, §6.2](b300.md)) | **4** (2 with Engram off-GPU) | **4 × TP4/EP4**; two replicas per 8-GPU node | **native NVFP4 W4A4** | `FLASHMLA_MEGA_ATTN_DSV41` / `_SPARSE_` / FlashInfer sparse, 890 B/token | 10,199 | 869 | **481** (49.9 ms)ᵇ | 9,531 (53.7 ms) | **$4.27** (Hyperstack $7.40) – **$8.66** (OCI $15.00) | $0.216–$0.437 | $1.16–$2.35 | estimate |
| **B200 HGX** | ✅ yes (vLLM `main`/nightly, SGLang `main`) — **but B200 is not validated for *this* checkpoint**; only the V4 NVFP4 builds are in the recipe catalogue ([b200.md §0](b200.md)) | **4** (TP4) | 4 × TP4 (throughput) or **8 × TP8** (≥ 32K context) | **native NVFP4 W4A4** | `FLASHMLA_MEGA_ATTN_DSV41`; SGLang `trtllm_mla` preferred (1.2× prefill / 1.45× decode) | 2,444 (TP4) / 8,887 (TP8) | 208 / 757 | **2,947** (21.7 ms) @ conc 256 | 34,705 (25.6 ms)ᵃ | **$0.565** ($6.00 Hyperstack) – **$1.319** ($14.00 OCI) | $0.048–$0.112 | $0.149–$0.346 | estimate |
| **H200 SXM** | ❌ not supported. Loads only via explicit Marlin opt-in (`--moe-backend marlin --kv-cache-dtype fp8_ds_mla`); vLLM nightly / SGLang v0.5.20 ([h200.md §0](h200.md)) | 8 | 8 × TP8, one HGX node | **BF16** — NVFP4 dequantised in the Marlin epilogue (W4A16) | `FLASHMLA_SPARSE_DSV41` (SM90); Mega-Attn absent → **FP8 KV 1,650 B/token** | 3,103 | 232 | **1,094** (29.2 ms) @ conc 256ᶜ | 4,461 (57.4 ms) | **$1.01** ($3.99 Hyperstack) – **$2.01** ($7.912 AWS); res1y $0.71 | $0.25–$0.49 | $0.28–$0.56 | estimate |
| **H100 SXM 80 GB** | ❌ not runnable in any supported sense. Marlin path has an **open** correctness bug (vLLM #49070); NVIDIA scopes the checkpoint to Blackwell ([h100.md §0, §6](h100.md)) | 8 | 8 × TP8 + **`--engram-config '{"cpu_offload":true}'`** (43× more concurrency at 128K) | **BF16** — NVFP4 → Marlin W4A16 | `FLASHMLA_SPARSE_DSV41` (block 64); ⚠️ SM90 sparse-decode contested. KV **890 B/token** per METHODOLOGY §8 | 2,546 | 217 | **964** (33.2 ms) @ batch 256 | 4,283 (59.8 ms) | **$0.784** (res1y $2.72) – **$1.983** ($6.880 AWS) | $0.176–$0.446 | $0.216–$0.546 | estimate |
| **RTX PRO 6000 Server Ed.** | ⚠️ runnable in principle, **validated nowhere** on `sm_120` for this checkpoint ([rtx6000-pro.md §0](rtx6000-pro.md)) | **4** (Engram in pinned host RAM) | **8**, TP+EP, PCIe Gen5, Engram in host RAM | **Marlin W4A16 by default**; native only via opt-in `--moe-backend flashinfer_b12x` | `FLASHINFER_MLA_SPARSE_DSV41` / `_SM120` / B12X; **FP8 KV mandatory** | 2,517 (8 GPU) / 78 (4 GPU) | 186 / 5 | **285** (50.0 ms) @ 8 GPU C114 | 541–809 @ 8 GPU C256 | **$1.754** ($1.80 Nebius) – **$4.036** ($4.143 AWS) | $0.62–$2.13 | $0.48–$1.11 | estimate |
| **MI355X** | ❌ not runnable as a production config. SGLang has no `DeepseekV41` gfx950 support (PR #39186 closed); vLLM-ROCm reduces to `EMULATION` ([mi355x.md §0, §2](mi355x.md)) | 4 | 4 × TP4 (two replicas per UBB baseboard) | **BF16** — NVFP4 nibbles unpacked by a Triton kernel **every forward step** | `ROCM_AITER_MLA_SPARSE` / `ROCM_FLASHMLA_SPARSE_DSV4`; **FP8 KV 1,650 B/token** | 7,371 | 552 | **1,230** (26.0 ms)ᵈ | 2,137 (29.9 ms)ᵈ | **$1.94** (OCI $8.60, low=high); $0.32 at colo `est.` | $1.12; $0.18 colo | $0.50–$0.77 | **not-runnable** |
| **A100 SXM 80 GB** | ❌ not runnable. **No `sm_80` attention backend exists** in any engine; all three DSV4.1 backends reject CC 8 ([a100.md §0, §6.1](a100.md)) | 8 | 8 × TP8+EP8 (or TP4×DP2) + Engram CPU offload | **BF16** — Marlin W4A16, **activation scales discarded** | none upstream; community fork uses Triton BF16 sparse-MLA. **FP8 KV 1,650 B/token** | 13,364ᵉ | 1,001ᵉ | **380** (42.1 ms) @ C128ᶠ | 380 (scheduler-capped)ᶠ | **$1.162** ($1.59 RunPod) – **$2.508** ($3.431 AWS); res1y $0.994 | same (cap, not ceiling) | $0.33–$0.76 | **not-runnable** |

ᵃ Roofline ceiling at the KV limit — 14–200× beyond any published concurrency for this model, and
NVIDIA's own commands cap at 16–32. Treat as a ceiling, not a plan ([b200.md §4.2](b200.md),
[gb300.md §4.2](gb300.md)).
ᵇ **The B300 and GB300 rows are not comparable, and this is the largest methodological
inconsistency across the eight docs.** [b300.md §3.1](b300.md) calibrates MBU to **0.054–0.215**
from two measured anchors and runs **without speculation**; [gb300.md §3.1](gb300.md) plans at
**MBU 0.70** per `gpus/gb300.md` §9.4. Same silicon family, ~8× apart. See
[Open questions](#open-questions) #2.
ᶜ H200's demonstrated ceiling is concurrency 64 (455 tok/s/GPU, TPOT 17.6 ms); past 128 the
published stack goes to TPOT 130 ms and TTFT 200 s ([h200.md §3.4, §4.2](h200.md)).
ᵈ Excludes the per-step dequantisation pass entirely — this is the cost of the *bytes*, not of the
executed kernel. AMD's in-kind datum for MXFP8 emulation is "conc1 ~1.3 tok/s"
([mi355x.md §3.4, §3.5](mi355x.md)).
ᵉ [a100.md §1.3](a100.md) computes max-concurrency with the **aggregated** (`× n_gpus`) formula and
flags replication as its own risk #7. Under the replicated reading, divide by 8.
ᶠ Measured proxy: the **base MXFP4** checkpoint on 8 × A800; this checkpoint is ≈ 6 % worse
([a100.md §3.4, §3.6](a100.md)).

## Optimization impact

From each pair doc's §2 (what runs) and §4 (what it is worth).

| Optimization | GB300 | B300 | B200 | H200 | H100 | RTX PRO 6000 | MI355X | A100 |
|---|---|---|---|---|---|---|---|---|
| **Attention kernel generation** | `FLASHMLA_MEGA_ATTN_DSV41` native — the fused kernel that unlocks the FP4 cache ([gb300.md §2](gb300.md)) | same family on `sm_103`; ⚠️ a filed `flash_mla_sparse_fwd` device-assert on B300, closed unfixed ([b300.md §6.2](b300.md)) | native; SGLang `trtllm_mla` **+1.2× prefill / +1.45× decode** over FlashMLA ([b200.md §2](b200.md)) | `_SPARSE_` only; **Mega-Attn has no SM90 instantiation** → loses the fused launch win *and* the FP4 KV dtype ([h200.md §2](h200.md)) | same; SM90 sparse **decode** contested (FlashMLA README says SM100-only, vLLM admits major 9) ([h100.md §2](h100.md)) | FlashInfer SM120 sparse / B12X; **FlashMLA does not run at all** ([rtx6000-pro.md §2](rtx6000-pro.md)) | AITER sparse MLA; **no published gfx950 sparse-MLA TFLOPS number**; DSA indexer kernel ⚠️ unconfirmed ([mi355x.md §2](mi355x.md)) | **none** — all three DSV4.1 backends reject CC 8 ([a100.md §2](a100.md)) |
| **Weight format executed** | native **W4A4**, 15,000 TFLOPS dense FP4 | native W4A4, 13,500 dense | native W4A4, 9,000 dense; measured grouped-MoE FP4 GEMM reaches only **11–14 % of peak** ([b200.md §3.1](b200.md)) | **W4A16 BF16** at 989.5 TFLOPS — memory win kept, compute win lost | same; **+2.0–5.7 % TPOT vs the base for identical math** ([h100.md §1.4](h100.md)) | **Marlin W4A16 by default**; the base keeps *native* MXFP4 W4A8 here — the only GPU where requantising moves you **backwards** ([rtx6000-pro.md §2](rtx6000-pro.md)) | **BF16 emulation, dequant every forward step**; AMD: "better positioned as an accuracy reference… than a production serving deployment" ([mi355x.md §2](mi355x.md)) | Marlin W4A16, **activation scales dropped** — the W4A4 calibration is inert ([a100.md §2](a100.md)) |
| **KV quantisation** | **890 B/token, architectural, not a flag** (`kv_cache_quant_algo: null`) | 890 B/token | 890 B/token — the reason the model is viable on a 180 GB part | **1,650 B/token FP8** (1.85×); `nvfp4_ds_mla` is `major == 10` only | **890 B/token** per METHODOLOGY §8 and h100.md's pool reconciliation — ⚠️ contested, see Open questions #1 | 1,650 B FP8 forced; the SM120 NVFP4 KV kernel **exists** (FlashInfer #4955) but no engine routes to it — 1.85× capacity behind a dtype gate | 1,650 B FP8; NVFP4 KV ❌ on gfx950 | 1,650 B FP8; sm_80 Triton cannot emit `fp8e4nv`, the fork hand-rolls E4M3 |
| **Speculative decoding (DSpark γ=5)** | ships, **never exercised by NVIDIA**; on GB300 R1 data MTP gave **−0.9 % throughput / +87 % TPS-user** → a latency feature, not a cost one ([gb300.md §4.4](gb300.md)) | measured **2.6–2.7 accepted/step** on the base at conc 1 (kern); ÷2.65 on $/1M output ([b300.md §4.3](b300.md)) | back-solved **1.3–2.1 accepted/step** from the InferenceX gap; goes negative above batch ~64 ([b200.md §3.4, §4.5](b200.md)) | V4 DSpark **crashed on SM90** (vLLM #47648); V4.1 MTP evidently works, rate unpublished | sensitivity only; ÷a on cost, a unmeasured ([h100.md §4.1](h100.md)) | **+38 % measured** on the V4 NVFP4 sibling — *after* patching the MXFP4 draft-expert misrouting; unpatched acceptance is **~0 %** ([rtx6000-pro.md §2](rtx6000-pro.md)) | adaptive verification **must be false** on ROCm; the whole interactive envelope rests on the **synthetic** 3.51 constant ([mi355x.md §4.5](mi355x.md)) | **≈2.6× aggregate, measured** on the sm_80 fork — the dominant optimisation on this GPU ([a100.md §4.4](a100.md)) |
| **Prefix caching** | ⚠️ **off in NVIDIA's own recipe.** Moves blended < 10 % at S1 but ~10× on the 100:1-input agentic workload the model targets ([gb300.md §4.4](gb300.md)) | 97.5 % hit → input costs **12.3 %** of uncached ([b300.md §4.3](b300.md)) | 97.6 % hit → input $0.0172 → **$0.00209**/1M ([b200.md §4.5](b200.md)) | ~**8×** on input cost at 97.6 % hit; the single largest lever here ([h200.md §4.5](h200.md)) | measured H100 rows run at 95–98 % hit; a 128K prefix reloads in **1.4 ms** vs a 2.097 PFLOP recompute ([h100.md §4.1](h100.md)) | an **8–11× warm-TTFT** lever (6.80 s → 0.68 s at 128K); barely moves blended cost ([rtx6000-pro.md §4.3](rtx6000-pro.md)) | InferenceX MI355X traces measure **99.7–100 % hit**; SGLang on MI350X must `--disable-radix-cache` and forfeits it ([mi355x.md §4.5](mi355x.md)) | on in the working sm_80 recipe (unlike NVIDIA's); 90 % hit takes 128K TTFT 6.50 s → 0.65 s ([a100.md §2](a100.md)) |
| **EP / PD disaggregation** | wide-EP: 5.3 experts/GPU at EP72 is in NVIDIA's band, but **DeepEP publishes no SM103 row** and the one published EP run on this family was 8× behind EP1. PD: TokenSpeed runs a GB300 **1P1D CI job — for the base repo** ([gb300.md §5.2–5.3](gb300.md)) | EP ≤ 8; DeepEP+DP-attn is exactly what tripped SGLang #25574 on B300. PD: aggregate below ~4 nodes ([b300.md §5.2](b300.md)) | DP+EP (`single_node_dep`) is **infeasible** — 214 GB of non-expert weights replicate per DP rank ([b200.md §1.2](b200.md)) | EP8+DP-attention would multiply 128K concurrency by 8 — **unpublished** for this architecture ([h200.md §5.1](h200.md)) | **EP = 8 costs ~8×** on H100 (measured); vLLM #47769 is an open Marlin-MoE-under-EP illegal access. Run EP=1 ([h100.md §3.2](h100.md)) | EP4 fine; **PD declared `unsupported`** by vLLM for the sibling; PCIe is the wrong fabric ([rtx6000-pro.md §5.2](rtx6000-pro.md)) | wide-EP architecturally unavailable (no 32–72 GPU coherent domain); **Dynamo has no ROCm support** ([mi355x.md §5.2](mi355x.md)) | **EP is required, not optional** (the fp8 block does not divide under plain TP); wide-EP impossible ([a100.md §2](a100.md)) |

## Cost vs vendor API

Break-even utilisation = our blended (or output) cost at 100 % utilisation ÷ the API price.
**> 100 % means it can never win at that operating point.** DeepSeek first-party `deepseek-flash`:
**$0.60 / $1.20 per 1M output** (off-peak / peak), **$0.15 / $0.30** uncached input, **$0.003 /
$0.006** cached — cached input is **2 % of uncached**, a 50× discount
([architecture.md §11.1](architecture.md)). Cheapest of 22 OpenRouter endpoints on output is
**DeepInfra $0.42** — and it is an **FP8** provider ([architecture.md §11.2](architecture.md)).

| GPU | Interactive (S1) vs $0.60 off-peak | Max-throughput (S4) vs $0.60 | Verdict |
|---|---|---|---|
| **GB300** | **206.6 %** at OCI $18.00 — unreachable. **26.5 %** at the SemiAnalysis volume anchor ($2.31) | 59.3 % at OCI; **7.6 %** at the anchor | Cannot beat the API on any *rented* GB300. At owned/amortised cost ($2.31–$2.57/GPU-h) it clears at 8–30 % utilisation ([gb300.md §4.5](gb300.md)) |
| **B300** | needs **712 %** of the S1 point at $7.40 (269 % with DSpark at 2.65) — impossible | **36 %** at Hyperstack $7.40; 73 % at OCI $15.00 | Wins only in batch-oriented service, never low-latency. On blended the API is 2.4–11× cheaper ([b300.md §4.4](b300.md)) |
| **B200** | break-even at **94 % utilisation** at conc 256 / $6.00; 2.2× worse at OCI $14.00 | **8.0 %** (low) / 18.7 % (high) at the KV ceiling | The only GPU whose *interactive* point gets within touching distance — but at a concurrency nobody has published ([b200.md §4.6](b200.md)) |
| **H200** | **135.9 %** (low $3.99) / 95.0 % (res1y $2.79) / 269.4 % (high) | **43.7 %** (low) / 30.6 % (res1y) | Never at an interactive SLO on rented H200s. S4 clears only on reserved capacity, at 32× the demonstrated batch ([h200.md §4.6](h200.md)) |
| **H100** | **104.1 %** (res1y $2.72) / 122.5 % (low $3.20) / 263.3 % (high $6.88) | **30.9–78.1 %** | Loses to DeepSeek's own off-peak API at every price tier interactively ([h100.md §4.2](h100.md)) |
| **RTX PRO 6000** | **never**, at any published price, on either kernel path | only native-path / optimistic-κ / C256 at **69–95 %** sustained | Reasons to run it are sovereignty and data residency, not price ([rtx6000-pro.md §4.4](rtx6000-pro.md)) |
| **MI355X** | **31 %** of break-even at OCI $8.60 — the API is 3.2× cheaper | 54 % at OCI; **325 %** at the $1.42 colo `est.` | Wins only on owned hardware. And the `est.` figures are for a path that does not run ([mi355x.md §4.6](mi355x.md)) |
| **A100** | **193.7 %** (low) / 165.7 % (res1y) / 418.0 % (high) | same (scheduler-capped) | Never on rented A100s. At the $0.58/GPU-h owned estimate it reaches $0.42/1M output — exactly DeepInfra's price, at 100 % utilisation ([a100.md §4.5](a100.md)) |

**The structural reading.** DeepSeek's $0.60 output price is reachable at roughly on-prem
amortised silicon cost and at no published rental rate — consistent across
[gb300.md §4.5](gb300.md), [b300.md §4.4](b300.md) and [mi355x.md §4.6](mi355x.md), which reach it
independently. On **blended** cost the API wins everywhere by 2.4–11×, because its 2 %-of-uncached
cached-input rate is a discount a self-hoster cannot match; that discount is itself affordable
*because* the architecture puts KV at 890 B/token. And the NVFP4 build makes every one of these
comparisons 2–7.5 % worse than the base checkpoint would ([gb300.md §4.5](gb300.md),
[mi355x.md §4.6](mi355x.md)).

## Recommendation

**For this repo's node types — 8 × B300 primary, AWS p6 family:**

1. **Run the base `deepseek-ai/DeepSeek-V4.1-Flash`, not this checkpoint.** On B300 the NVFP4
   build is ~5.7 % more decode bytes per step at saturated batch and +4.25 GB/GPU at TP4, for an
   accuracy wash; it is absent from vLLM's recipe catalogue, and the base has a published B300
   profile, an Engram-offload path, community `sm_103a` kernels and portability to MI355X
   ([b300.md §0, §6.1](b300.md), [gb300.md §3.4](gb300.md)). Reach for the NVFP4 build only if
   NVIDIA's group-16 calibration audit is a requirement in itself.
2. **Shape: two independent TP4/EP4 replicas per 8-GPU node** (GPUs 0–3 and 4–7), which is how
   kern documents the HGX B300 case and what the topology rewards. TP4 leaves 104 GB/GPU of KV —
   869 concurrent 128K sequences under the conservative replicated reading, an order of magnitude
   past what the engines will schedule ([b300.md §1.4, §5.1](b300.md)).
3. **Never plan TP2 at 268 GB/GPU.** 2 × 268 GB against 527.27 GB of weights is `infeasible (KV)`
   before the 0.90 usable factor. vLLM's own B300 command builder defaults to TP2 *with*
   `--engram-config '{"cpu_offload":true}'` — the offload is load-bearing there, not an
   optimisation, and the recipe's own prose says to pass `--tensor-parallel-size 4` for
   interactivity ([b300.md §1.2, §5.3](b300.md)).
4. **Raise the concurrency caps.** NVIDIA's `--max-running-requests 16` / `--max-num-seqs 32` are
   one to two orders of magnitude below what the memory allows; vLLM's B300 profile already uses
   `--max-num-seqs 256` and is the better starting point ([b300.md §5.3](b300.md)).
5. **Turn prefix caching back on** and keep `--model-loader-extra-config '{"enable_multithread_load": true, "num_threads": 128}'` (shards 47–48 are 101.54 GB each).
6. **On AWS p6:** `p6-b300.48xlarge` is 2,144 GB/node = 268 GB/GPU, with 30.72 TB of local NVMe and
   4 TB host RAM — enough to stage the checkpoint locally and to hold the 202.76 GB of Engram
   tables pinned if you take the offload route ([b300.md §5.4](b300.md)). `p6-b200.48xlarge`
   (8 × B200, 180 GB) is the cost-optimal alternative and is the one GPU with a `verified` vLLM
   recipe for this architecture ([b200.md §5.3](b200.md)). Note AWS publishes **no 1-year RI** for
   `p6-b300.48xlarge`, and its on-demand list ($17.802/GPU-h) is 2.4× Hyperstack's
   ([b300.md §4](b300.md)).

**Benchmark first, in this order:**

| # | Experiment | Why it comes first |
|---|---|---|
| 1 | **Paired NVFP4 vs base MXFP4 A/B**, one TP4 replica, same image, same flags | The single highest-value experiment in the whole tree: nobody anywhere has run it, and it decides which checkpoint to deploy. "Two containers, one tray, one afternoon" ([gb300.md §6.1](gb300.md), [b300.md §6.1](b300.md)) |
| 2 | **`gpu_kv_cache_usage_pct` and `kv_cache_pool_tokens` at TP4 and TP8** | Settles whether the single latent KV stream is replicated or sharded — the two readings differ by **4×** in max concurrency at 128K and change every TPOT and cost figure ([b300.md §6.3 #11](b300.md), [b300.md audit log](b300.md)) |
| 3 | **MBU calibration at batch 1 / 32 / 128 / 256** | [b300.md](b300.md) calibrates 0.054–0.215; [gb300.md](gb300.md) plans 0.70. One measured sweep on your own node replaces an 8× planning uncertainty |
| 4 | **DSpark acceptance rate** (`vllm:spec_decode_num_{accepted,draft}_tokens_total`) | The only measurement is 2.6–2.7 on the *base* at concurrency 1; the 3.51 in circulation is a **synthetic benchmark constant**. Cost scales as 1/a ([b300.md §4.3](b300.md), [mi355x.md §4.5](mi355x.md)) |
| 5 | **Prefix caching on vs off**, at your real cache-hit rate | NVIDIA's recipe disables it with no stated reason; at 97.5 % hit the input leg costs 12.3 % of uncached ([b300.md §4.3](b300.md)) |
| 6 | **A correctness smoke test before any throughput test** | NVFP4 backbone + MXFP4 DSpark experts in one process is validated by nobody, and its failure modes are silent (~0 % acceptance; garbage, not a crash) ([architecture.md §7.3](architecture.md), [rtx6000-pro.md §6 #2–3](rtx6000-pro.md)) |

## Open questions

Consolidated ⚠️ **TO BE VERIFIED** items across all eight pair docs, deduplicated and ordered by
impact on the deployment decision.

**Tier 1 — changes which checkpoint or which shape you deploy**

1. **No paired NVFP4-vs-base benchmark exists, on any GPU.** Every "NVFP4 is 2–5.7 % slower"
   conclusion in this tree is arithmetic on byte counts plus a memory-bound classification — both
   solid, neither measured. NVIDIA's card contains zero throughput figures
   ([gb300.md §6.1](gb300.md), [b300.md §6.1](b300.md), [b200.md §6.3 #1](b200.md)).
2. **The B300 and GB300 pair docs disagree ~8× on achieved MBU** (0.054–0.215 calibrated vs 0.70
   planned) and therefore on every throughput and $/token figure for the same silicon family.
   Neither is wrong on its own terms; the tree has no measured B300 anchor for this checkpoint
   ([b300.md §3.1](b300.md), [gb300.md §3.1](gb300.md)).
3. **Is the compressed latent KV replicated per TP rank or sharded?** `num_key_value_heads = 1`
   argues replicated, and [h100.md §3.2](h100.md), [h200.md §1.4](h200.md) and
   [b200.md §3.3](b200.md) each reconcile a measured KV pool to the replicated reading. But
   [architecture.md §5.6](architecture.md) publishes the aggregated reading, [a100.md §1.3](a100.md)
   uses it, and [b300.md](b300.md)'s own §3.2 roofline silently divides the KV read by TP4 while its
   §1.3 argues replication. **4× on max concurrency**, and no engine source states it in words.
4. **KV bytes/token on Hopper: 890 or 1,650?** METHODOLOGY §8 pins **890 B** and says the FP4 cache
   is dequantised in software, so it is not Blackwell-only. [h100.md §1](h100.md) agrees and
   corroborates it by reconciling a measured 7,957,495-token pool (which *cannot* close at 1,650 B
   against an 8.35 GB budget). [h200.md §1.4](h200.md) deviates to **1,650 B**, citing
   `flash-attention.md` §16.1 and its own 31.0 M-token pool reconciliation. **The foundation doc
   sides with h100.md**; h200.md's concurrency figures are therefore conservative by up to 1.85×.
   Only affects Hopper, which is not recommended anyway.
5. **NVFP4 backbone experts + MXFP4 MTP/DSpark experts in one process is validated by nobody, on
   any GPU** — and it has already broken in the field on `sm_120` for the V4 sibling (draft experts
   routed through the NVFP4 path, ~0 % acceptance, no error message). This is the most likely place
   for the checkpoint to break ([architecture.md §7.3](architecture.md),
   [rtx6000-pro.md §2, §6 #2](rtx6000-pro.md)).
6. **Nobody has published tuned concurrency settings.** `--max-running-requests 16` /
   `--max-num-seqs 32` against a 947–11,113-seat KV budget. Every throughput figure above
   concurrency 32 in every pair doc extrapolates past the largest configuration anyone has run
   ([gb300.md §6.4 R17](gb300.md), [b300.md §5.3](b300.md)).

**Tier 2 — changes the numbers, not the decision**

7. **Fixed per-sequence state: 2,703,360 B or 2,906,112 B?** METHODOLOGY §8 pins **2.77 MiB**
   (43 layers, incl. the 3 MTP rings); [architecture.md §5.3](architecture.md) derives 2,703,360 B
   from the 40 backbone layers. h200/b200/b300/gb300/mi355x use METHODOLOGY's; h100/rtx6000-pro use
   architecture.md's. Worth ≤ 2 % at 8K, ~0 % at 1M. kern is the only engine that documents its
   allocation and uses **S = 2** (5.81 MB/seq) ([b300.md §1.1](b300.md)).
8. **GB300 capacity basis: 279 GB or 288 GB?** [gb300.md §1.1](gb300.md) uses 279 per
   `gpus/gb300.md` §2.1; [architecture.md §5.6](architecture.md)'s GB300 row was computed from 288.
   ~7 % on every concurrency figure at 4 GPUs. METHODOLOGY §8 writes "288 GB (≈ 279 usable)", so
   the pair doc is right for sizing and architecture.md's row is optimistic.
9. **B200 capacity — RESOLVED, closed.** 180 GB decimal is the planning basis; "183 GB each" in
   vLLM PR #56686 is an `nvidia-smi` MiB total labelled as GB ([gpus/b200.md §2](../../gpus/b200.md)).
   Sensitivity retained: 1.7 % of capacity, **11 %** of the TP4 KV budget
   ([b200.md §1.1, §6.3 #8](b200.md)).
10. **A100: 80 GB pinned vs 85.90 GB from `nvidia-smi`** — 47.2 GB per node, **2.8×** the entire
    no-offload KV budget ([a100.md §1.1](a100.md)).
11. **Activation workspace.** METHODOLOGY's 2–6 GB band is too narrow: [b200.md §3.3](b200.md)
    measures the implied workspace rising 4.0 → 9.8 GB from concurrency 1 to 128, and
    [h200.md §1.1](h200.md) plans 8 GiB because the sparse indexer allocates a
    `[max-num-batched-tokens × max-model-len]` logits buffer at start-up.
12. **Which HF checkpoint each InferenceX row actually served.** The API exposes `precision: "fp4"`
    but not the repo id. The B300 rows use the exact container NVIDIA's card names — suggestive,
    not proof — while the B300 TP2 rows' KV pool only closes against 288 GB physical, not the pinned
    268 ([architecture.md §12.14](architecture.md), [b300.md §1.4, §6.3 #14–15](b300.md)).
13. **Do production kernels bound the four full-pool indexers?** At 1M context they cost 21.47
    GFLOP/token — more than the entire encoder forward pass — and the reference implementation does
    not bound them. Would make 1M prefill ~2× faster ([architecture.md §6.2](architecture.md),
    [gb300.md §6.2 R8](gb300.md), [mi355x.md §6 #9](mi355x.md)).
14. **Why does NVIDIA's own vLLM command pass `--no-enable-prefix-caching`?** Unstated everywhere.
    The likely culprit is an SWA-Bounded-Replay interaction — and the DP-attention shape that would
    fix the replicated-KV problem is the same shape SGLang excludes bounded replay from
    ([gb300.md §6.4 R16](gb300.md)).
15. **Engram CPU offload is unmeasured on every GPU.** It is load-bearing on H100 (43× concurrency
    at 128K), mandatory for TP2 on B300, and the best offload target in the fleet on GB300 (Grace
    LPDDR5X at 900 GB/s C2C) — yet nobody has measured what the 12,672 B/token of scattered gather
    costs ([h100.md §6 #12](h100.md), [gb300.md §6.3 R12](gb300.md)).

**Tier 3 — checkpoint packaging and correctness hygiene**

16. **`model.safetensors.index.json` still carries the base checkpoint's
    `metadata.total_size = 510,286,023,000`.** Any loader sizing an allocation from it
    under-allocates by 16.99 GB — cosmetic on a 268 GB card, fatal on an 80 GB one with a 2.09 GB/rank
    KV budget ([architecture.md §1.1](architecture.md), [a100.md §5.4](a100.md)).
17. **The shipped `config.json` contradicts itself on activation scaling:**
    `input_activations.dynamic = false` against a sibling `activation_scheme: "dynamic"`. On a W4A4
    checkpoint this is an accuracy question, not a cosmetic one — and moot on every W4A16 fallback
    path ([architecture.md §1.2](architecture.md)).
18. **18 of 46,080 projection entries used fallback activation scales** (0.04 %); which layers they
    sit on is unpublished. Inert on Hopper/Ampere (the scales are discarded), live on Blackwell
    ([architecture.md §10.1](architecture.md), [h200.md §6.3 #18](h200.md)).
19. **Calibration was 1,024 samples at sequence length 512** for a model whose headline is a
    1,048,576-token context — and AA-LCR, the one long-context benchmark in NVIDIA's table, is the
    one NVFP4 **loses** ([b300.md §6.4](b300.md)).
20. **Why NVIDIA left the 188.83 GiB Engram tables at FP8.** NVFP4 would save ≈ 92 GB and drop the
    checkpoint to ≈ 435 GB, which would make TP2 feasible at 268 GB/GPU with no offload at all.
    Community repos have done it (LibertAIDAI 429.4 GB, msuiche 414.9 GB) with **no published
    accuracy data** ([architecture.md §4.1, §9.1](architecture.md), [b200.md §6.5](b200.md)).
21. **Engram `NgramHashState` per-sequence size is not stated anywhere**, so every
    `fixed_state_per_seq` in this tree omits it ([architecture.md §12.17](architecture.md)).
22. **The vision tower is unexercised.** Every published recipe for this checkpoint passes
    `--language-model-only`; on `sm_120` a six-frame video test on the *base* checkpoint **failed**
    ([rtx6000-pro.md §6 #15](rtx6000-pro.md), [mi355x.md §6 #19](mi355x.md)).
23. **Neither engine has shipped this architecture in a numbered release** — vLLM `main` /
    nightly, SGLang `main` / `dev-dsv41`. vLLM's tracking issue #57448 lists 16 open items of kernel
    work still landing; the performance picture will move ([gb300.md §6.4 R13](gb300.md)).
24. **TensorRT-LLM has no V4.1 entry at all**, and every MLPerf GB300 record is TRT-LLM — so this
    model is absent from the stack that produces GB300's best published numbers. NVIDIA Dynamo has
    no V4.1 recipe either ([architecture.md §8.1, §12.11–12.12](architecture.md)).

## Sources

Every figure above is drawn from these documents; section references are inline throughout.

**This model**
- [`architecture.md`](architecture.md) — checkpoint bytes and shard shape (§1.1), quantisation config (§1.2), component inventory (§3.3), why it grew (§3.4), weight memory and Engram headroom (§4–4.2), KV derivation and fit (§5.1–5.6), compute profile (§6.1–6.4), DSpark (§7), engine matrix and vendor recipes (§8), variants (§9), published benchmarks and accuracy (§10), vendor API pricing (§11), open questions (§12)

**Pair documents (one per GPU)**
- [`h100.md`](h100.md) — 8 × H100 SXM 80 GB, TP8 + Engram CPU offload; Marlin W4A16; the 890-B-on-Hopper pool reconciliation; vLLM #49070 / #47769
- [`h200.md`](h200.md) — 8 × H200 SXM, TP8; FP8 KV; the Marlin tile-alignment hazard at TP8; the 8 × H200 measured sweep
- [`b200.md`](b200.md) — 4/8 × B200 HGX; native NVFP4 W4A4; the KV-pool evidence for replicated KV; the W4A4-vs-W4A8 open question
- [`b300.md`](b300.md) — **this repo's primary node type**; 4 × TP4 on an 8-GPU HGX B300; calibrated MBU; the three `sm_103` kernel faults; kern's HGX-B300 manifest
- [`gb300.md`](gb300.md) — the only vendor-validated pair; 4 × TP4, 8 × TP8 and the full NVL72 rack; wide-EP and PD analysis
- [`a100.md`](a100.md) — 8 × A100 SXM 80 GB; not runnable upstream; the community `sm_80` fork measurements
- [`rtx6000-pro.md`](rtx6000-pro.md) — 4/8 × RTX PRO 6000 Blackwell Server Edition; the Marlin-vs-B12X question; the MXFP4 draft-expert misrouting failure
- [`mi355x.md`](mi355x.md) — 4 × MI355X; NVFP4 is not native on CDNA4; the `quark_mxfp4` requant path and why it is unavailable for this architecture

**Foundation**
- [`research/METHODOLOGY.md`](../../METHODOLOGY.md) — §1 bytes/param, §2 KV and fixed state, §3 fit and the `infeasible (KV)` rule, §4 roofline and MBU/MFU bands, §6 cost model and scenarios S1–S4, §7 what must never happen, §8 pinned GPU / model / price inputs (the adjudicator for every disagreement listed above)
- The base checkpoint's own tree: [`research/models/deepseek41f/`](../deepseek41f/) — the comparator this entire document is written against
