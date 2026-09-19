# NemoStation/Marlin-2B — GPU selection guide

Research date **2026-09-19**. Every number below is taken from one of the eight pair documents or
from [`architecture.md`](architecture.md), and is linked to its source. Formulas and the S1–S4
scenario definitions are [`research/METHODOLOGY.md`](../../METHODOLOGY.md) §1–§8. No number in this
document is new; where two pair docs disagree, §"Where the pair docs disagree" says which is right
and why.

## Summary

1. **What it needs: almost nothing.** 4.426 GB of BF16 weights (tie honoured), 12,288 B/token of KV across only 6 of 24 layers, and an 18.63 MiB fixed Gated-DeltaNet state per sequence ([architecture.md §4, §5.1–§5.2](architecture.md)). It fits on every GPU in the pinned set with 94–98 % of the card unused.
2. **Every pinned GPU runs it today — none of them run it *as shipped*.** `MarlinForConditionalGeneration` is in no engine registry; vLLM ≥ 0.29.0 loads the weights after one documented flag, `--hf-overrides '{"architectures":["Qwen3_5ForConditionalGeneration"]}'` ([architecture.md §8.1](architecture.md)). `transformers >= 5.7.0` + `trust_remote_code=True` is the only *first-class* path.
3. **Parallelism is settled before it is asked: TP1 on one GPU, scale by replicas.** `num_key_value_heads: 2` caps clean KV sharding at TP2 and engines replicate KV heads above it — on A100, TP8 delivers 602 concurrent sequences/GPU against TP1's 2,086 ([a100.md §1.3](a100.md), [mi355x.md §1.3](mi355x.md)). No fabric is used at any scale.
4. **Best for interactive serving: B200.** 3.60 ms TPOT at concurrency 256, 71,040 output tok/s/GPU, 0.86 s end-to-end for one 2-minute clip at batch 1, and it is one of only three cards that get FA4's dedicated `head_dim 256` kernel ([b200.md §3.2, §4.2](b200.md); [architecture.md §10.3](architecture.md)). B300 is 3 % faster for 23 % more money with identical BF16 rate and bandwidth ([b300.md §6 #25](b300.md)).
5. **Best cost per token: B200 on paper, H100 in practice.** B200 leads at **$0.432 per 1,000 two-minute captions** and $0.0195/1M output at max throughput ([b200.md §4.3, §4.5](b200.md)); H100 is within 7 % at **$0.461** ([h100.md §4.4](h100.md)) with a measured 8×H100 reference deployment for this exact workload. Reserved H200 at `res1y` $2.79 beats both at **$0.38/1k videos** ([architecture.md §11.2](architecture.md)).
6. **Cheapest viable: RTX PRO 6000 Blackwell SE.** $1.80/GPU-h on-demand → $0.585/1k videos; at `res1y` $1.30 → **$0.423/1k videos**, beating every on-demand row except a reserved H200 ([rtx6000-pro.md §4.4, §4.6](rtx6000-pro.md)). A100 is the cheaper *card* ($1.59/h) and the worse deal ($0.696/1k videos).
7. **Worst choices, and why: GB300 and MI355X.** GB300 costs $18.00/GPU-h — the only published rate anywhere — for a model that uses none of NVL72's distinguishing features, and ranks last on $/1k videos ([gb300.md §6 #22](gb300.md)). MI355X is $8.60 from one seller, runs 18 of 24 layers on the slowest GDN path in the stack, gets no FlashQLA and no FA3/FA4, and is the only pair rated `speculative` rather than `estimate` ([mi355x.md §0](mi355x.md)).
8. **Not runnable at all:** speculative decoding (the checkpoint ships **zero** `mtp` tensors — [architecture.md §7.1](architecture.md)); any FP8/NVFP4/MXFP4 weight path (no such checkpoint exists, public or private — [architecture.md §9](architecture.md)); the FA4 hd256 kernel on A100/H100/H200/RTX PRO 6000/MI355X (gated to CC 10/11); INT8 on B300/GB300 (sm_103a lacks `tcgen05.mma .kind::i8`, ~1/30 rate, vLLM raises at the first forward — [gb300.md §2](gb300.md)); INT4 on MI355X (Marlin/Machete are CUDA-only; Triton dequant is *slower* than BF16 — [mi355x.md §2](mi355x.md)); vLLM's `--model-impl transformers` fallback (disqualified four times over — [architecture.md §8.2](architecture.md)); and 1 M context (`max_position_embeddings` is 262,144 with `rope_type: default` — [architecture.md §5.3](architecture.md)).
9. **The three facts that dominate every decision.** (a) The **240-frame cap** bounds every video request at ~23,560 prefill tokens no matter how long the clip is, so concurrency is a constant and a 10-minute clip costs what a 2-minute one does — at 0.40 effective fps ([architecture.md §6.3](architecture.md)). (b) At fleet scale **every GPU is prefill-bound**, the exact inverse of the batch-1 picture where 71–85 % of wall time is decode — so prefill FLOPs and frame count are the levers, and speculation and weight quantisation are not. (c) **Prefix caching is worth structurally zero**: the cacheable scaffold is < 0.2 % of a request, `--mamba-cache-mode=all` is a hard error, and `align` mode is experimental ([architecture.md §5.5](architecture.md)).
10. **Confidence: `estimate` everywhere except MI355X (`speculative`).** There is **no published throughput or latency measurement of Marlin-2B on any hardware** ([architecture.md §10.2](architecture.md)); every throughput figure below is a roofline, and the remap that makes it run on vLLM has never been executed by anyone in this repo.

## Cross-GPU comparison

One row per GPU. **Interactive** is each pair doc's S1 operating point; **max-throughput** is its S4
point. `$/1M out` bands are `low`–`high` from [`cloud-pricing.md` §5.14](../../cross-cutting/cloud-pricing.md)
planning rows. Concurrency is per GPU at BF16 KV.

| GPU | Runnable today (engine) | Min GPUs | Recommended | Weight format executed | Attention kernel | Max conc @8K | @128K | @23.5K (video) | Interactive tok/s/GPU · TPOT | Max-thr tok/s/GPU | $/1M out interactive | $/1M out max-thr | Blended $/1M | Conf. |
|---|---|---:|---|---|---|---:|---:|---:|---|---:|---|---|---|---|
| [**b200**](b200.md) | vLLM 0.29.0 + `--hf-overrides` (NGC 26.08) | 1 | 1 × TP1, 8 DP/node | BF16 | **FA4 + dedicated hd256 kernel**; FlashInfer GDN (CUDA ≥ 13) | 1,275 | 94 | **496** | 71,040 · **3.60 ms** (b=256) | 85,544 (b=1,024) | $0.0235–0.0547 | **$0.0195–0.0455** | $0.0095–0.0221 | estimate |
| [**b300**](b300.md) | vLLM ≥ 0.29.0 + remap | 1 | 1 × TP1, 8 DP/node | BF16 | FA4 + hd256 + `sm103a` path; FlashInfer GDN | 1,934 | 142 | **753** | 73,808 · **3.47 ms** (b=256) | 93,271 (b=3,327) | $0.0278–0.0565 | $0.0220–0.0447 | $0.0106–0.0215 | estimate |
| [**h100**](h100.md) | vLLM 0.29.0 + remap | 1 | 1 × TP1, 8 DP/node | BF16 | **FA3** (740 TFLOPS `meas.`); FlashInfer GDN prefill | 528 | 38 | 206 | 37,549 · 22.21 ms (conc 834) | 37,549 (S1 = S4) | $0.0237–0.0509 | $0.0237–0.0509 | **$0.0095–0.0203** | estimate |
| [**h200**](h200.md) | vLLM 0.29.0 + remap | 1 | 1 × TP1, 8 DP/node | BF16 | FA3; FlashInfer GDN prefill | 983 | 72 | 383 | 33,565 · 7.63 ms (b=256) | 37,903 (b=1,551) | $0.0330–0.0655 | $0.0292–0.0580 | $0.0127–0.0251 | estimate |
| [**mi355x**](mi355x.md) | vLLM-ROCm ≥ 0.27.0 / SGLang-ROCm 0.5.15+ — **remap never run on ROCm** | 1 | 1 × TP1, 8 DP/node | BF16 | CK FA2 / `ROCM_AITER_FA` (hd256 ✅); **no FA3/FA4, no FlashQLA**; GDN = Triton both phases | 2,086 | 153 | 811 | 36,232 · 7.07 ms (+1.0 ms ROCm floor) | 41,304 (b=3,292) | $0.066 (one price) | $0.058 | $0.0236 | **speculative** |
| [**rtx6000-pro**](rtx6000-pro.md) | `transformers` first-class; vLLM 0.29.0 + remap | 1 | 1 × TP1, N replicas | BF16 | **FA2** (cc 12.0 fails the `major==10` gate); **FlashQLA lists SM120** ✅; B12X backend ⚠️ | 646 | 47 | 251 | 13,434 · 19.06 ms (b=256) | 15,886 (b=1,019) | $0.0372–0.0857 | $0.0315–0.0724 | $0.0135–0.0311 | estimate |
| [**a100**](a100.md) | vLLM ≥ 0.17.0 / SGLang ≥ 0.5.9 + remap | 1 | 1 × TP1, 8 DP/node | BF16 (no FP8/FP4 silicon) | **FA2**; **no FlashQLA (SM80)** → GDN *prefill* on Triton/FLA; GDN *decode* fused CUDA ✅ | 562 | 41 | 218 | 20,011 · 12.79 ms (b=256) | 23,450 (b=887) | $0.0221–0.0476 | $0.0188–0.0406 | $0.0112–0.0242 | estimate |
| [**gb300**](gb300.md) | vLLM `main`/nightly + remap | 1 | 1 × TP1, 72 DP/rack | BF16 | FA4 + hd256; FlashInfer GDN | 2,016 | 148 | 785 | 70,403 · 3.64 ms (b=256) | 70,403 (b=256 grid max) | **$0.226**† | **$0.214**† | $0.0680 | estimate |

† **GB300's two cost cells are not comparable with the rest of the row set.** [gb300.md §4.2](gb300.md)
prices on its §3.5 *sustained* (serial prefill + decode) rate of 22,164 out tok/s/GPU; every other
pair doc prices `$/1M output` on the *decode-only* rate. Re-derived on the common basis with
METHODOLOGY §6's formula from gb300.md's own §3.2 figure —
`$18.00 ÷ (70,403 × 3600) × 1e6` — GB300's interactive `$/1M out` is **$0.071**, still the most
expensive card in the set and for the reason its own §6 #22 gives: $18.00/GPU-h is 2.4× B300's
`low` and the rack's distinguishing features (NVL72 domain, wide-EP, 279 GB, FP4 cores) are all
inert on a dense 2 B model.

**The unit that actually bills is $/1,000 two-minute captions**, and on it the ordering is
unambiguous (all at `low`, 768 output tokens, sustained prefill+decode):

| Rank | GPU | $/1k videos | videos/h/GPU | source |
|---:|---|---:|---:|---|
| 1 | b200 | **$0.432** | 13,881 | [b200.md §4.5](b200.md) |
| 2 | h100 | $0.461 | 6,946 | [h100.md §4.4](h100.md) |
| 3 | b300 | $0.487 | 15,193 | [b300.md §4.3](b300.md) |
| 4 | h200 | $0.541 | 7,372 | [h200.md §4.3](h200.md) |
| 5 | rtx6000-pro | $0.585 | 3,074 | [rtx6000-pro.md §4.4](rtx6000-pro.md) |
| 6 | a100 | $0.696 | 2,285 | [a100.md §4.4](a100.md) |
| 7 | mi355x | $0.863 | 9,968 | [mi355x.md §4.4](mi355x.md) |
| 8 | gb300 | $1.085 | 16,596 | [gb300.md §4.3](gb300.md) |

Reserved tiers reorder this materially: `h200` at `res1y` $2.79 → **$0.378/1k**
([h200.md §4.3](h200.md)) and `rtx6000-pro` at `res1y` $1.30 → **$0.423/1k**
([rtx6000-pro.md §4.4](rtx6000-pro.md)), both beating every on-demand row.

## Optimization impact

Drawn from each pair doc's §2 (what runs) and §4 (what it is worth). **Every row is BF16-executed**,
so none of the weight-format cells is reachable without someone first producing a checkpoint that
does not exist.

| Optimization | a100 | h100 | h200 | b200 | b300 | gb300 | rtx6000-pro | mi355x |
|---|---|---|---|---|---|---|---|---|
| **Attention kernel gen** (6 of 24 layers, `head_dim 256`) | FA2 only. **No FlashQLA (SM80 unlisted)** → GDN *prefill* on Triton/FLA, penalty ⚠️ unquantified, bracketed 1.0–2.0× ([a100.md §4.5](a100.md)). **Corrects [architecture.md §8.3](architecture.md): the penalty is prefill-only — GDN decode gets the fused CUDA kernel at cc ≥ 8.0** | **FA3**, measured **740 TFLOPS BF16 fwd ≈ 75 % of dense peak**; attention is 18 ms of a 408 ms video prefill ([h100.md §2](h100.md)) | FA3, same sm_90 silicon. FA4 on sm_90 is beta and **not a safe production default** ([h200.md §2](h200.md)) | **FA4 dedicated hd256 kernel** — written for exactly this shape. Requires `--block-size 128` or it **silently falls back to FA2**. `mm_prefix` checked and does **not** disqualify ([b200.md §2](b200.md)) | FA4 hd256 + the `sm103a` `tcgen.ld.red` path; 2× attention SFU (5 → 10.7 TeraExp/s) — worth ≤ a few % since attention is 13.3 % of prefill FLOPs ([b300.md §2](b300.md)) | FA4 hd256, 2-CTA. Same `--block-size 128` requirement ([gb300.md §2](gb300.md)) | **FA2** — cc 12.0 fails both the `9` and `10` branches. **FA4 hd256 unsupported: the single biggest kernel loss for this model.** But it is the *only* non-Hopper/Blackwell-DC card FlashQLA lists (SM120) ([rtx6000-pro.md §2](rtx6000-pro.md)) | CK FA2 / `ROCM_AITER_FA` (hd256, FP8-KV, 3.61× over `ROCM_ATTN` on a different model). AITER's tuned hd256 ASM kernel is **MHA-only → Marlin's 8:2 GQA falls back to CK, 436 TFLOPS = 17.4 % of peak** ([mi355x.md §2](mi355x.md)) |
| **Weight format** | No FP8/FP4 silicon. INT4 GPTQ runs on the *Marlin kernel* (3.87× weight read to batch 16–32); **INT8 is the only format that raises the prefill ceiling** (624 TOPS) ([a100.md §2](a100.md)) | FP8/INT8/INT4 all native; Machete W4A16. INT4 cuts decode weight read 3.76 → 1.73 GB with **no prefill speedup** ([h100.md §2](h100.md)) | Same; Machete +29–32 % token throughput vs prior kernels. 1,979 dense FP8 TFLOPS sit idle ([h200.md §2](h200.md)) | **NVFP4 silicon stranded** — no checkpoint. INT4 W4A16 runs on the **BF16** 2,250 TFLOPS line and is "not the right choice on B200" ([b200.md §2](b200.md)) | **B300's entire FP4 differentiator is unused.** **INT8 is actively harmful** (187.5 TOPS = 0.08× BF16). INT4 GPTQ hits **vLLM #35924** — `num_v_heads = 16 < GPTQ_MARLIN_MIN_THREAD_N 64`, failing **even at TP1** ([b300.md §2, §6 #5](b300.md)) | **INT8 is a non-starter**: `tcgen05.mma .kind::i8` is absent for `sm_103a`; vLLM raises `Int8 not supported` **after the weights have loaded** ([gb300.md §2](gb300.md)) | Dense NVFP4 GEMM **works** here (the broken grouped-GEMM path needs an MoE; this model is dense), so a Marlin NVFP4 build would be a safe sm_120 target — nobody has made one. INT4 Marlin is "the de-facto fastest and most correct path on this GPU today" ([rtx6000-pro.md §2](rtx6000-pro.md)) | MXFP4 (10,100 TF) and FP8 (5,000 TF) native, **no checkpoint**. **INT4 is a net loss**: Marlin/Machete are CUDA-only, gfx950 gets a Triton dequant slower than BF16, and AMD publishes no INT4 MFMA rate at all ([mi355x.md §2](mi355x.md)) |
| **KV quantisation (FP8 E4M3)** | **The single highest-leverage flag on the card.** Storage-only (no FA3 ⇒ no FP8 attention math, no per-head scales). **1.55–1.87× decode**, concurrency 218 → 411 ([a100.md §2, §3.2](a100.md)) | **+72 % seats** (206 → 387 video), 834 → 1,328 at 4K, **73,632 tok/s = +96 %**. **Two live caveats:** hd256 prefill regression (gemma-4-E2B TTFT ×1.6) and **open vLLM #54035** — FP8-KV + FA3 on Hopper gives a decode/prefill logprob mismatch, max Δ ≈ 0.56 ([h100.md §2, §6](h100.md)) | 1.72× @8K / 1.88× @video / 1.99× @128K concurrency. **Not a cost lever here** — ~+3 % throughput at the video operating point, because the hybrid schedule already removed 75 % of the KV ([h200.md §4.4](h200.md)) | **1.83–1.95× decode, +88 % concurrency (496 → 934).** **But quantized KV forces the hd256 kernel back to FA2** — the central engineering trade on this card. b200.md argues take the FP8 KV; **neither side is measured** ([b200.md §3.5, §6.1 #2](b200.md)) | 753 → **1,417** concurrent videos (+88 %) ([b300.md §2](b300.md)) | 785 → **1,477** (1.88×). *"~0 % on cost, +88 % on concurrency"* — every scenario is prefill-bound ([gb300.md §4.4](gb300.md)) | 1.83× decode, 1.88× concurrency — **but sustained videos/h moves only 3,074 → 3,359 (+9 %)** because the fleet is prefill-bound. ⚠️ whether the math runs in FP8 or dequantises on read is **unresolved on sm_120** ([rtx6000-pro.md §3.4, §6.2 #9](rtx6000-pro.md)) | **+69.7 % output tok/s at the video point** (KV is 82 % of bytes moved), 750 → 1,417 concurrency, and it makes S3 @ b=256 legal. Fleet rate 7,396 → 8,862 videos/h, $1.163 → $0.970/1k ([mi355x.md §3.4, §4.3](mi355x.md)) |
| **Speculative decoding (MTP / EAGLE / DSpark)** | **Unavailable on every GPU.** `config.json` declares `mtp_num_hidden_layers: 1`; the 618-name weight map has **zero** `mtp` tensors, and base − Marlin = **60,828,160 params = exactly one MTP module** ([architecture.md §7.1](architecture.md)). No EAGLE drafter exists; DSpark is a DeepSeek component. This fixes METHODOLOGY §2's `S` at **1**. | ← | ← | ← | ← | ← | Calibration of what is given up: InferenceX measured the **MTP decay curve on this exact card** — 1.90× at conc 1 → **1.17× at conc 64** ([rtx6000-pro.md §3.5](rtx6000-pro.md)) | Calibration: AMD measured **DFlash block=16 on Qwen3.5-27B, one MI355X, up to 5.02× over autoregressive**, acceptance 10.377 — a same-family, same-GPU demonstration of a lever Marlin cannot pull ([mi355x.md §2](mi355x.md)) |
| **Prefix caching** | **Structurally zero on every GPU.** `--mamba-cache-mode=all` is a raised `NotImplementedError`; `align` is *"currently experimental"*; the cacheable scaffold is **< 0.2 %** of a 23,560-token request ([architecture.md §5.5](architecture.md)). Leaving APC on with no overlap **cost −36.7 % throughput / +25.0 % TPOT on A100** ([a100.md §2](a100.md)) | **vLLM #40696 (open)**: vLLM sets the hybrid block size to the mamba page size, **528 tokens** — a 40-token scaffold fills no block, so the hit rate is **exactly 0**; measured QPS fell 200 → <100 across that boundary ([h100.md §4.5](h100.md)) | Even at a hypothetical 90 % hit the **video row saturates at 46 % of miss cost**, because the ViT re-runs on every new clip ([h100.md §4.5](h100.md)) | The methodology blend is **24–31 % optimistic** for this pair ([b200.md §4.4](b200.md)) | Budget the 0 % row ([b300.md §4.5](b300.md)) | The METHODOLOGY cached-input term is *"charged but never earned"* — realistic blended is **+14 %** ([gb300.md §4.4](gb300.md)) | The assumption flatters this pair by **26 %**: $0.0135 blended vs $0.0170 honest ([rtx6000-pro.md §4.5](rtx6000-pro.md)) | METHODOLOGY's blended figure **understates by 25 %**: $0.0236 vs $0.0294 ([mi355x.md §4.3](mi355x.md)) |
| **EP / PD / EPD** | **EP is inapplicable on every GPU — the model is dense** (`mlp_only_layers: []`, no `num_experts`, no router; [architecture.md §2.2](architecture.md)). **PD is a net loss everywhere**: *"out above ~1000 GPUs; inward below — −20–30 % on small/untuned"*, and a 1-GPU replica has nothing to split. **EPD is the only one worth a thought** — the ViT is **39 % of prefill compute** — and it is unmeasured on every platform. | SGLang + Mooncake; the only published EPD number is **+25 % throughput on a GB200 *image* workload**, no H100 video datum ([h100.md §5.1](h100.md)) | ← | At 1 GPU per replica *"there is nowhere to send the encoder"* ([b200.md §5.2](b200.md)) | TRT-LLM lists *EPD Disaggregated Serving = Yes* for `Qwen3_5ForConditionalGeneration` — but TRT-LLM support for the Qwen3.5 hybrid is itself ⚠️ unverified ([b300.md §5.1](b300.md)) | Worth it only if a **CPU-starved tray** (4 GPUs : 2 Grace CPUs) can hand video decode to dedicated nodes ([gb300.md §5.1](gb300.md)) | The right "disaggregation" here is **EPD-shaped and CPU-side**: move video decode onto NVDEC ([rtx6000-pro.md §5.1](rtx6000-pro.md)) | Dynamo has **no documented ROCm support** at all ([mi355x.md §2](mi355x.md)) |

**The optimization that is not on METHODOLOGY §5's list and outranks everything on it: hardware
video decode.** vLLM measured *"more than double the throughput"* on 8×H100 video captioning with
the PyNvVideoCodec/NVDEC backend versus the CPU decoder, with CPU decode bottlenecking *"even with
just 2 or 4 GPUs"* — on a **16-frame** workload, against Marlin's 240 ([h100.md §2](h100.md)).
NVDEC counts: A100 5, H100/H200 7, B200/GB200 7, RTX PRO 6000 4; **B300 has no published row**
([b300.md §6 #11](b300.md)), and on ROCm the answer is `rocDecode` with **no published fps figure
and no documented torchcodec integration** ([mi355x.md §2](mi355x.md)).

## Cost vs vendor API

**There is no vendor API to compare against.** All eight pair docs re-confirm
[architecture.md §11.1](architecture.md): HF `inferenceProviderMapping` for `NemoStation/Marlin-2B`
is **`{}`** — empty; the base `Qwen/Qwen3.5-2B` has one provider (`featherless-ai`, task
`conversational`, **text only, not video**); the author runs a Gradio demo at `vlm.nemostation.com`
with no token-priced endpoint and monetises through custom fine-tuning by email. **METHODOLOGY §6's
vendor sanity check cannot be executed for this pair, and no pair doc invents a comparator.**

Break-even is therefore parametric. Per METHODOLOGY §6,
`U = (self-host $/1M output) ÷ (API price per 1M output)`. Using each GPU's **max-throughput**
`$/1M out` at the `low` tier from the table above:

| GPU | $/1M out (max-thr, `low`) | U vs a $0.10/1M API | U vs $0.30/1M | U vs $1.00/1M |
|---|---:|---:|---:|---:|
| a100 | $0.0188 | 18.8 % | 6.3 % | 1.9 % |
| b200 | $0.0195 | 19.5 % | 6.5 % | 2.0 % |
| b300 | $0.0220 | 22.0 % | 7.3 % | 2.2 % |
| h100 | $0.0237 | **23.7 %** | **7.9 %** | **2.4 %** |
| h200 | $0.0292 | 29.2 % | 9.7 % | 2.9 % |
| rtx6000-pro | $0.0315 | 31.5 % | 10.5 % | 3.2 % |
| mi355x | $0.058 | 58.0 % | 19.3 % | 5.8 % |
| gb300 | $0.214 † | 214 % | 71.3 % | 21.4 % |

The h100 row is quoted directly from [h100.md §4.6](h100.md) (23.67 % / 7.89 % / 2.37 %), which
prints the same arithmetic; the other rows apply the same formula to the same column. † gb300's cell
is on the sustained basis (see the comparison-table footnote); on the decode basis it is $0.071 and
U vs $0.10 is 71 %.

**Read it as: a single rented H100 at $3.20/hr beats a $0.30/1M-output API as soon as you keep the
card busy 7.9 % of the time** ([h100.md §4.6](h100.md)). The pair docs reach the same verdict from
other directions — [mi355x.md §4.5](mi355x.md): *"any pricing above ~$0.01/video from a hypothetical
hosted Marlin would be beaten by a single self-hosted GPU at under 0.1 % utilisation — the break-even
is so far below any realistic load that the question is not a business question."*
[b300.md §4.6](b300.md): every general-purpose VLM API charges $0.40–$50 per 1M output, so
self-hosting wins at any plausible utilisation.

**The exception is GB300**, where the $18.00 rate makes the question real: [gb300.md §4.5](gb300.md)
computes that self-hosting needs **68 % sustained utilisation to beat a $0.10/1M API** on its
blended figure, and **136 % — i.e. never — to beat $0.05/1M**.

**The break-evens that do matter are against hardware, not APIs:**

- **Owning vs renting.** RTX PRO 6000 colo-amortised is $0.877/GPU-h at 100 % utilisation; owning beats Nebius on-demand above ~49 % utilisation, does **not** beat Hyperstack reserved until ~68 %, and **never** beats Nebius preemptible $0.95 ([rtx6000-pro.md §4.6](rtx6000-pro.md)). B200 colo is $2.09–$3.35/GPU-h against $6.00 rented — break-even ≈ **11 months of continuous utilisation** ([b200.md §4.6](b200.md)).
- **A100 vs H100.** H100 costs 2.01× more per GPU-hour and delivers **2.88× the sustained video throughput**, so **H100 is cheaper per video than A100 at both tiers**. A100's case is capital already owned, or the **$0.70–$1.00/GPU-h all-in colo rate for used silicon**, which puts 2-minute videos at **$0.37–$0.53/1,000** and beats every rented tier on any GPU ([a100.md §4.6](a100.md)).
- **Scale reality check.** 1 M two-minute captions/month is ~66 GPU-hours on one B300 (**$487** at `low`) or ~144 GPU-hours on one H100 (**$461**). [b300.md §4.6](b300.md) states it plainly: *"the engineering time to stand up the `--hf-overrides` path and validate quality costs more than a year of that GPU bill."*

## Recommendation

**For this repo's actual nodes: run it on the 8×B300 you already have, as 8 independent TP1
replicas. Do not buy anything for it.**

**What to run.** One vLLM process per GPU, `--tensor-parallel-size 1`, a plain HTTP load balancer in
front. That is 8 replicas × 753 concurrent 2-minute videos = **6,024 node-wide**
([b300.md §1.3](b300.md)) and ~121,500 captions/hour per node at 768 output tokens. The ConnectX-8
fabric, the NVLink domain and every FP4 tensor core on the node are **100 % idle for this pair** —
which is fine when the node is already there and is the reason
[b300.md §6 #25](b300.md) says *"run Marlin-2B on B300 only because the node is already there for
something else; never buy one for it."*

**How to run it** — the B300 command, with every flag attributed in
[b300.md §5.2](b300.md):

```shell
vllm serve NemoStation/Marlin-2B --port 8000 \
  --tensor-parallel-size 1 \
  --hf-overrides '{"architectures": ["Qwen3_5ForConditionalGeneration"]}' \
  --max-model-len 32768 \
  --mamba-cache-mode=align \
  --block-size 128 \
  --media-io-kwargs '{"video": {"num_frames": -1}}' \
  --mm-processor-cache-type shm
```

Non-negotiables, each of which fails silently or loudly if skipped:

- **`--mamba-cache-mode=align`** is mandatory — `"all"` raises `NotImplementedError` for Qwen3.5 ([architecture.md §5.5](architecture.md)).
- **`--block-size 128`** is what turns on the FA4 hd256 kernel; any other value **silently falls back to FA2** ([b300.md §5.2](b300.md)).
- **CUDA ≥ 13 in the container.** The FlashInfer GDN prefill kernel — 18 of 24 layers — requires it; on a CUDA 12.x image it silently drops to Triton/FLA ([b300.md §6 #10](b300.md)).
- **Verify the wheel carries `sm_103` cubins**: `cuobjdump --list-elf <lib>.so | grep sm_103`. An `sm_100a`-only wheel is the #1 cause of "works on B200, dies on B300" ([b300.md §6 #8](b300.md)).
- **Re-supply what the remap discards**: both EOS ids `[248044, 248046]` (an engine reading only `config.eos_token_id` misses one and runs to `max_new_tokens=2048` — the expensive failure mode), the `<think>`-prefix strip, the three canonical prompts, and the Path A frame/pixel budget ([architecture.md §1.3, §1.4, §6.3](architecture.md)).
- **Lower `--max-cudagraph-capture-size` below 512** if `causal_conv1d_update` asserts at startup ([architecture.md §8.7](architecture.md)).
- **Do not use the INT4 GPTQ build on B300.** vLLM #35924: GDN's `in_proj_ba` has output dim = `num_v_heads` = **16**, below `GPTQ_MARLIN_MIN_THREAD_N = 64`, so the Marlin kernel raises during weight loading **even at TP1** ([b300.md §6 #5](b300.md)). BF16 is unaffected.

**On AWS p6.** `p6-b300.48xlarge` is the same silicon at a hyperscaler rate — $15.00 (OCI) / $17.80
(AWS list) against Hyperstack's $7.40 ([b300.md §4.1](b300.md)) — so the node-already-there argument
does not transfer to renting one. If p6 capacity is what is available, **`p6-b200.48xlarge` is the
better buy for this model**: identical BF16 rate and bandwidth within 4 %, the same FA4 hd256 kernel,
and the best $/1k-videos figure in the set ([b200.md §4.5](b200.md), [b300.md §6 #25](b300.md)).
Note that AWS, GCP and OCI sell B200 **only in 8-GPU shapes**, while Lambda, RunPod Secure and
Hyperstack sell single B200s — so a 1–2 replica deployment should go to a neocloud on availability
grounds as well as price ([b200.md §5.1](b200.md)). If the fleet is steady and committed, the
cheapest correct answer in the whole set is **reserved H200 at $2.79/GPU-h → $0.378/1k videos**
([h200.md §4.3](h200.md)).

**What to benchmark first, in this order.** Each item closes a specific blocking open question and
none takes more than an afternoon:

1. **Load the checkpoint under `--hf-overrides` on one B300 and generate ten captions.** Diff them against `transformers` + `trust_remote_code=True` on the same clips. This closes open questions **1** and **3** simultaneously — whether all 618 tensor names map onto vLLM's `Qwen3_5ForConditionalGeneration` loader (including how it reconciles the materialised `lm_head.weight` against `tie_word_embeddings: true`), and whether serving preserves caption and timestamp quality. Everything else is downstream of this.
2. **Count the prefill tokens vLLM actually produces for one fixed 2-minute clip.** Path A is 23,560, Path B is 12,288 — a **1.914× gap**, and `qwen-vl-utils` bypasses the HF processor's `size` ([architecture.md §6.3](architecture.md)). Every throughput and cost number in all nine documents assumes Path A. If your deployment silently lands on Path B, prefill halves and quality drops with no error raised. This is the largest single source of error in the entire analysis.
3. **Time a 240-frame decode: torchcodec on the host CPU vs `pynvvideocodec` NVDEC.** This is unmeasured everywhere consulted, vLLM's own data says CPU decode saturates before 4 GPUs on a *16-frame* workload, and it is the most likely reason a real deployment misses these numbers by more than any modelling error. On B300 there is an extra wrinkle: NVIDIA's own table has **no B300 NVDEC row** — confirm with `nvidia-smi -q | grep -i decoder` on the real silicon ([b300.md §6 #11](b300.md)).
4. **Two runs: `--kv-cache-dtype auto --block-size 128` vs `--kv-cache-dtype fp8_e4m3`, compared on end-to-end videos/hour *and* TTFT.** FP8 KV is +88 % concurrency and 1.83–1.95× decode, but it forces the FA4 hd256 kernel back to FA2, and FP8 KV is separately measured to **regress prefill by ×1.6 on `head_dim = 256` shapes**. Since the fleet is prefill-bound, FP8 KV could *lower* sustained throughput while raising decode throughput. **Measure TTFT, not just TPOT** ([b200.md §6.1 #2](b200.md), [h100.md §6](h100.md)).
5. **Profile one prefill and read off which GDN kernel ran.** 18 of 24 layers ride on it, the CUDA-13 gate is silent, and the FlashQLA-vs-FlashInfer-vs-Triton spread measured at Marlin's exact GDN geometry is **1.22×–3.31×** ([b200.md §3.4](b200.md)). This is the largest unquantified term in every throughput table here.
6. **Run TimeLens-Bench mIoU before and after any quantisation** — KV or weights. Not one published Marlin quantisation reports any accuracy measurement at all, and for a model whose product is second-precise timestamps, numeric error landing on the timestamp digits is a specific, plausible and entirely unmeasured failure mode ([architecture.md §9](architecture.md)).

**One benchmark worth running even though it changes no deployment: B300 vs H200, identical vLLM
builds, one 2-minute clip end to end.** [b200.md §3.4](b200.md) found the Gated-DeltaNet prefill
kernel at Marlin's exact geometry (`h_qk = h_v = 16`, `d = 128`, TP1) measures **1.00–1.16× of
Hopper on GB200 — with one row at 0.69×, i.e. slower** — against a 1.6× bandwidth ratio and a 2.3×
BF16 FLOPS ratio, on 75 % of this model's layers. That is the single most important measured fact in
the whole set and it is the mechanism behind vLLM #18725's B200 ≈ H200 anti-result. If it holds
end-to-end, the Blackwell rows in every table above are the most optimistic ones.

## Where the pair docs disagree

The eight pair docs were written and audited in parallel. Six substantive disagreements exist; each
is resolved here against the foundation docs, and **none changes a deployment decision.**

1. **GDN state traffic in the decode-byte model.** [h200.md §3.1](h200.md) and [mi355x.md §3.1](mi355x.md) add `batch × 2 × 19,537,920 B` per decode step for the recurrent + conv state read-modify-write; the other six docs follow [architecture.md §6.2](architecture.md) and omit it. **h200.md and mi355x.md are physically right and METHODOLOGY-literal wrong**: METHODOLOGY §4's `bytes_per_decode_step` is `weights_read + Σ kv_read` and §2 counts the fixed state as *capacity*, not bandwidth — but 18 layers really do read and write a 1,048,576-element fp32 state per sequence per step. It is material: **+22.8 % to +57.3 % on S1 TPOT at batch 32–256**, +9.6 % to +12.9 % at video context ([h200.md §3.1](h200.md)), and at short context and high batch it is **35–40 % of all bytes moved** ([mi355x.md §1.5](mi355x.md)). **Consequence for the comparison table: the h200 and mi355x throughput rows are conservative relative to the other six.** Whether the fused kernel reads and writes the full state or updates in place is ⚠️ unverified; the 2× assumption is the conservative one.
2. **A100 usable HBM: 72.0 GiB or 67.06 GiB.** [a100.md §1.1](a100.md) reads `nvidia-smi`'s 81,920 MiB as 80 **GiB** and lands on 72.0 GiB usable; [architecture.md §8.5](architecture.md) reads METHODOLOGY §8's pinned "80 GB" as decimal and lands on 67.06 GiB. **architecture.md is right per the pinned table** — METHODOLOGY §8 pins 80 GB and `gpus/a100.md` itself flags the 81,920 MiB figure as ⚠️ TO BE VERIFIED with no primary NVIDIA source. a100.md prints both; the spread is 517 → 570 seats at 8K, **±5 %**.
3. **Resident weights: 4.426 GB or 5.444 GB.** [a100.md §1.1](a100.md) plans with 5.444 GB (as shipped, `lm_head` materialised); the other seven plan with 4.426 GB. **[architecture.md §4](architecture.md) is right** — it names 4.426 GB "the number to plan with" and 2.722 B params "a storage artefact". a100.md tabulates both. Difference: 1.017 GB, **1.4 % of concurrency at 8K**. Whether vLLM honours the tie is itself open question 1.
4. **The 5,920-token (30 s video) concurrency column in [architecture.md §8.5](architecture.md) is systematically ~4.4 % high.** [b200.md §1.2](b200.md) re-derived that column for every GPU from each row's own stated `kv_budget` and found a uniform +4.4 % (b200 1,734 printed vs 1,660 derived; a100 715 vs 685); [rtx6000-pro.md §1.3](rtx6000-pro.md) independently found the same for its own cell (878 vs 841) but reported it as a one-cell anomaly. **b200.md's diagnosis is right and its explanation is the better one** — the column was produced with a slightly different context length, not a per-row error. Every other column, including the 23,520 one a video fleet actually lives in, reproduces exactly. **Use the pair docs' recomputed 5,920 figures.**
5. **H100 KV budget, three readings.** 59.21 GiB ([h100.md §1.1](h100.md), 4.426 GB weights + 4 **GB** workspace), 58.93 GiB ([architecture.md §8.5](architecture.md), 4 **GiB** workspace), 58.26 GiB (`gpus/h100.md` §2, 5.444 GB weights). **All three are correct for what they measure**; the spread is 528/520/520 seats at 8K and 206/205/202 at video context — **≤ 2 %**. h100.md plans on 59.21 GiB and prints the others.
6. **"No engine supports it" vs "one flag, not a port."** [`inference-engines.md` §6.5](../../cross-cutting/inference-engines.md) concludes *"Engine support: none, on any engine"*; [architecture.md §8.1](architecture.md) concludes *"true of the weights and false of the checkpoint as shipped… It costs one flag, not a port."* **Both are true at their own dates, and architecture.md supersedes**: the engines doc was written **without gate access** and did not consider `--hf-overrides`; architecture.md was written **with** it and establishes from the real `modeling_marlin.py` that the class is `class MarlinForConditionalGeneration(Qwen3_5ForConditionalGeneration):` with, verbatim, *"The forward pass is not modified."* All eight pair docs cite both and follow architecture.md. **But do not discard the caution: nobody has run it.**

Two further methodological differences worth knowing before comparing cells across rows:

- **MBU is not uniform.** [architecture.md §10.3](architecture.md) uses 0.75 for Hopper; [h100.md §3.1](h100.md) uses **0.65** (measured InferenceX KV-inclusive MBU, 66.7 % at TP2) and [a100.md §3.1](a100.md) uses **0.70** with a **measured-calibrated 0.47** printed alongside. [rtx6000-pro.md §3.1](rtx6000-pro.md) additionally splits MFU_attn to **0.25** where architecture.md folds attention into a flat 0.40. Each deviation is justified from its own GPU doc and printed with a sensitivity sweep.
- **Realisation haircuts differ.** [h200.md §3.3](h200.md) derives **0.60–0.67** from MLPerf Llama-3.1-8B on 1×H200; [b300.md §3.5](b300.md) derives **0.28–0.45** from RunPod's B300 runs; [a100.md §3.4](a100.md) derives **0.47/0.70 = 0.67** from vLLM #24728. **The roofline rows in the comparison table carry no haircut.** Apply the per-GPU factor from its own doc before quoting any of them externally — on B300 that takes the S1 figure from 93,271 to **28,000–42,000 output tok/s/GPU**.

## Open questions

Consolidated ⚠️ **TO BE VERIFIED** items across all nine documents, deduplicated and ordered by how
much resolving them would change the decision. **34 distinct items.**

### Blocking — nothing downstream is safe until these are closed

1. **The `--hf-overrides` remap has never been run end to end, on any GPU.** All 618 tensor names must map onto vLLM's `Qwen3_5ForConditionalGeneration` loader. Evidence is strong — pure subclass, unmodified forward, documented API, and `prasannaJagadesh/marlin-2B-GPTQ-4BITS` already ships the remapped `architectures` string — but nobody has loaded it. Sub-question: how vLLM reconciles the materialised `lm_head.weight` with `tie_word_embeddings: true`, which is also the 1.017 GB fork in disagreement #3. Encouraging: vLLM #36275 shows the Qwen3.5 failure mode is a weight-*name* mismatch (`model.layers.*` vs `language_model.model.layers.*`) and Marlin's map carries the **multimodal** naming, i.e. the right side of that bug ([b300.md §6 #1](b300.md)).
2. **Path A vs Path B video token budget — 23,520 vs 12,288, a 1.914× gap.** `qwen-vl-utils` runs its own `smart_resize` and bypasses the HF processor's `size`; the gated `processor_config.json` **confirms the conflict rather than resolving it** (`max_frames: 768`, `longest_edge: 25165824`). Serving through vLLM/SGLang likely gives the model half the frames or half the resolution it was trained on — **a silent quality regression, not an error**. Every number in all nine documents assumes Path A ([architecture.md §6.3, §12 #1](architecture.md)).
3. **Does serving through vLLM preserve caption and grounding quality at all?** Four independent places to diverge: the fixed training prompt, the `<think>` prefix artefact, the two-element EOS list `[248044, 248046]`, and the bespoke preprocessing path. **Zero engine-vs-`transformers` comparisons exist** ([architecture.md §12 #3](architecture.md)).
4. **torchcodec / video-decode throughput for 240 frames at 448×448 is unmeasured everywhere.** vLLM's own data says CPU decode bottlenecks before 4 GPUs on a *16-frame* workload; Marlin's canonical path is 15× heavier. The GPU roofline in every §3 may be describing a resource that is never the bottleneck ([architecture.md §12 #6](architecture.md)).
5. **No measured throughput or latency for Marlin-2B exists on any hardware.** No Performance section on the card, `model-index: null`, not an MLPerf model, no InferenceX rows (re-confirmed by pulling `/api/v1/availability` in three separate pair docs). The demo Space's `@spaces.GPU(duration=75…180)` values are **ZeroGPU quota reservations on a time-shared A10G, not measurements** ([architecture.md §10.2](architecture.md)).
6. **Does the 240-frame cap destroy "second-precise timestamps" on long video?** A 10-minute clip is sampled at **0.40 fps** and an hour at **0.067 fps** for identical cost. Unmeasured, and it is a quality cliff priced as a saving ([architecture.md §6.3, §12 #4](architecture.md)).

### High-impact — these move the numbers, not the go/no-go

7. **Which GDN kernel each engine actually dispatches, and where it sits in the measured spread.** vLLM's `_resolve_gdn_prefill_backend` picks FlashInfer on SM90 and SM10.x/12.x; SGLang carries its own `triton_gdn_fused_proj`; FlashQLA lists SM90/100/103/120/121 and **nothing in either engine calls it**. At Marlin's exact GDN geometry the measured spread is **1.22×–3.31×** ([b200.md §3.4](b200.md)). This applies to **18 of 24 layers** and is the largest single uncertainty in every throughput table.
8. **Blackwell may buy nothing on 75 % of this model.** [b200.md §3.4](b200.md)'s GB200-vs-H200 comparison at Marlin's exact shape measures the GDN prefill kernel at **1.00–1.16× of Hopper, with one row at 0.69× (slower)**, against a 1.6× bandwidth and 2.3× FLOPS ratio. vLLM #18725 independently shows B200 ≈ H200 on QwQ-32B. **Method: one 2-minute clip end to end on B300 and H200 with identical builds, before signing anything.**
9. **The GDN state read+write term** — 1× or 2× per step, and whether the fused kernel updates in place. Disagreement #1 above; it is worth **+22.8 %–57.3 %** on S1 TPOT ([h200.md §6 #5](h200.md)).
10. **FA4 hd256 vs FP8 KV — which wins, and is either measured?** Neither is. FP8 KV forces the hd256 kernel back to FA2; the kernel touches 6 of 24 layers and 13.3 % of prefill FLOPs, so [b200.md §3.5](b200.md) argues FP8 KV by a wide margin — *"take the FP8 KV; give up the kernel B200 was the only GPU to offer"* — but flags it as the one experiment to run first ([b200.md §6.1 #2](b200.md)).
11. **FP8 KV at `head_dim 256` carries two live, measured hazards on Hopper.** (a) gemma-4-E2B, the only published hd256 FP8-KV datum, shows the **TTFT coefficient rising ×1.6** — and this workload is prefill-bound. (b) **vLLM #54035 is open**: FP8-KV + FA3 on Hopper gives a systematic decode/prefill logprob mismatch, max Δ ≈ 0.56, with the issue's own advice being to avoid it *"if logprob accuracy is critical"* — which it is, for a timestamp-grounding model ([h100.md §6](h100.md)).
12. **No FA4 hd256 TFLOPS figure exists on any GPU.** The published 1,613 TFLOPS / 71 % utilisation figure is hd**128**. B200/B300's one Marlin-specific hardware advantage is entirely unpublished ([b200.md §6.2 #6](b200.md)).
13. **MBU and MFU for a *2 B* model are unmeasured on every GPU in the set.** At batch 1 the roofline step is 0.79–0.96 ms across ~170 kernels; per-step scheduler, sampler and launch overhead is a **majority** of that, not a rounding error. The nearest GB300 proxy runs 4.5 ms at concurrency 1 on a model with 9× the active params ([gb300.md §6 #8](gb300.md)); MI355X carries an explicit `+1.0 ms` ROCm floor column ([mi355x.md §3.1](mi355x.md)). **Every low-batch row in every document is a ceiling that will not be reached.**
14. **Whether the fused CUDA GDN decode kernel is actually built in the wheel you install.** vLLM falls back to Triton **silently** if `torch.ops._C.fused_gdn_decode_post_conv_mtp` is missing ([h100.md §6 #12](h100.md)).
15. **FlashInfer GDN prefill requires CUDA ≥ 13.** On a CUDA 12.x image, 18 of 24 layers drop to Triton/FLA with no error — and official vLLM images default to **cu128** ([rtx6000-pro.md §6.4 #16](rtx6000-pro.md), [b300.md §6 #10](b300.md)).
16. **Triton GDN autotuner OOM on non-SM90 GPUs.** vLLM #36598: `_forward_core` returns early during the V1 profile run, so the autotuner never runs before KV-cache allocation and **the first real request OOMs**. Fix proposed in PR #43047, merge status unconfirmed. **A100 is squarely in the affected class, and this is the most likely way an A100 deployment dies on its first production request** ([a100.md §6 #3](a100.md)).
17. **SGLang video ingestion for `qwen3_5` is unconfirmed.** The class is exported and subclasses `Qwen3VLForConditionalGeneration`, but the SGLang multimodal docs page 404'd on both domains. This matters more than it looks: several of the measured proxies in the pair docs are SGLang runs ([h200.md §6 #11](h200.md)).
18. **MI355X specifically: video + GDN + ROCm is unverified in combination.** vLLM's ROCm verification for Qwen3.5 covers the **397 B MoE text** path, not a 2 B hybrid **video** path. Plus a live crash: GatedDeltaNet + dp-attention on MI355X/ROCm 7.2 throws `HIP invalid configuration argument` in `chunk_gated_delta_rule_fwd_*`, **closed with no workaround beyond disabling dp-attention** ([mi355x.md §6 #2, #4](mi355x.md)).
19. **The ROCm GDN prefill penalty is estimated, not measured.** The ×1.37 used throughout mi355x.md is aiter#5606's `−27 % TTFT` inverted, measured on an **80 B MoE**; on an 18-of-24-layer 2 B model the true penalty is plausibly larger. Two open PRs would remove most of it — aiter#5606 (fused gfx950 GDN prefill, 2.1–3.8× kernel) and vllm#51406 (unblocking the fused QK-norm+RoPE+gate kernel on ROCm). **Watch both** ([mi355x.md §6 #7](mi355x.md)).
20. **The FA4 hd256 kernel's `mm_prefix` disqualifier.** `supports_mm_prefix()` returns `is_fa_version_supported(4)`, and if the model's video prefix uses bidirectional attention, B300's signature kernel may never engage. [b200.md §2](b200.md) checked vLLM's `qwen3_5.py` and `qwen3_vl.py` and found **no `mm_prefix` string**, which partially closes it; [b300.md §6 #9](b300.md) still says **verify by profiling, not by reading flags**.
21. **INT4 GPTQ is broken by a live vLLM GDN bug at TP1.** vLLM #35924: GDN's `in_proj_ba` output dim = `num_v_heads`; below `GPTQ_MARLIN_MIN_THREAD_N = 64` the Marlin kernel raises during weight loading. **Marlin-2B has 16** — below the threshold even at TP1. Closed by PR #36329; **not verified against this checkpoint** ([b300.md §6 #5](b300.md), [a100.md §6 #16](a100.md)).
22. **`.multi_find` production cost is unmeasured and it is a trap.** N sequential `ffmpeg -c:v libx264` re-encodes (frame-accurate, not stream-copy) plus **N full prefills** — CPU work no roofline sees, multiplying the phase that is already the limiter. Its own docstring warns `.find` *"always emits some span"*, so later spans are low-confidence GPU spend ([architecture.md §1.4, §12 #11](architecture.md)).

### Configuration and engine details

23. **`--mamba-ssm-cache-dtype bfloat16`**, if honoured, halves the GDN state to 9.63 MiB/seq — worth ~2 % concurrency at video context, **~30 % at 4K**, and it cuts the state traffic that is 35–40 % of a high-batch decode step. Unverified on any platform ([b300.md §6 #21](b300.md), [mi355x.md §6 #14](mi355x.md)).
24. **GDN conv-state width**, `kernel` vs `kernel − 1`: 18.84 vs 18.63 MiB/seq, **+1.1 %, immaterial** ([architecture.md §12 #12](architecture.md)).
25. **NVFP4 KV at `head_dim 256`.** FlashInfer exposes `nvfp4_kv_cache_full_dim` and merged NVFP4 paged-KV sparse decode/prefill for SM100/SM103 on 2026-09-18; **hd256 support is unconfirmed** on every Blackwell doc ([b200.md §6.2 #11](b200.md), [gb300.md §6 #9](gb300.md)).
26. **GDN state-slot scaling at 750–3,327 concurrent sequences** — that is 14–62 GB of recurrent state read-modify-written every decode step, and the fused kernel's behaviour there is the biggest unknown in the high-batch rows ([b300.md §6 #20](b300.md)).
27. **`--max-num-seqs` in practice.** The KV-ceiling rows are a capacity limit, not a reachable setting; no engine is configured for thousands of concurrent sequences by default, and vLLM's older default of 256 would leave two-thirds of an H100's KV pool idle ([h200.md §6 #10](h200.md), [b300.md §3.5](b300.md)).
28. **Engines reserve more than METHODOLOGY's 10 %.** SGLang's published GB300 config holds back **25 %** (`mem_fraction_static = 0.75`), which would cut every GB300 concurrency figure by ~17 %; SGLang under AITER on MI355X uses 0.85 above 8K context ([gb300.md §6 #12](gb300.md), [mi355x.md §6 #12](mi355x.md)).
29. **Peak ViT activation footprint at 240 frames.** 94,080 ViT tokens in one varlen forward is real transient memory, per *concurrent prefill*, and nobody has published it. Method: sweep `--max-num-batched-tokens`, watch `nvidia-smi` peak reserved ([b300.md §1.4](b300.md)).
30. **TensorRT-LLM support for the Qwen3.5 hybrid/GDN path: no evidence either way** — and every clean single-GPU H200 MLPerf number is a TRT-LLM number, so the fastest measured Hopper stack is the one whose support is unknown ([h200.md §6 #12](h200.md)).
31. **RTX PRO 6000 platform specifics:** whether FP8 KV runs the attention math in FP8 or dequantises on read on the SM12x GQA path (sources conflict three ways); whether the B12X backend works on a hybrid-GDN VLM and whether it is mutually exclusive with `--kv-cache-dtype fp8` (it is `auto`-only); and whether a cu130 official image ships at all ([rtx6000-pro.md §6.2 #9–#10, §6.4 #16](rtx6000-pro.md)).
32. **B300's NVDEC configuration is not published.** NVIDIA's Dynamo table lists B200/GB200 at 7 engines and **has no B300 row**. For a *video* model on a GPU whose vendor cut INT8 by 24× and FP64 by 30× to buy FP4 throughput, confirm the media engines survived before building a captioning fleet on them ([b300.md §6 #11](b300.md)).

### Commercial and evaluation

33. **Zero accuracy evaluations exist for any Marlin-2B variant, quantised or not.** Not one of GPTQ-INT4, SDNQ-INT8, MLX-8bit or GGUF publishes a CaReBench / DREAM-1K / TimeLens score or a perplexity delta. The model's own quality claims are **vendor marketing with no numeric table**, `model-index: null`, no third-party reproduction — and ActivityNet, Charades and TimeLens appear in **both** the training-source list and the evaluation list with no stated contamination control ([architecture.md §9, §10.1](architecture.md)). **You would have no baseline to regress against.**
34. **No hosted Marlin-2B API exists anywhere**, so METHODOLOGY §6's vendor sanity check cannot be run; Gemini's video-understanding price was not retrieved, so the card's *"fraction of the cost"* claim is uncomputed; and **`gb300` and `mi355x` each rest on exactly one published price** (OCI $18.00 and $8.60) with no reserved tier and no second source, while **RTX PRO 6000 renters usually cannot tell whether they got the Server Edition (1,597 GB/s) or the Workstation Edition (1,792 GB/s)** — an 11 % swing in every number ([architecture.md §11.1, §12 #22, #25](architecture.md), [rtx6000-pro.md §6.4 #24](rtx6000-pro.md)).

## Sources

**This directory** — every claim above links to one of these:

- [`architecture.md`](architecture.md) — the model-side source of truth (gated `config.json`, `model.safetensors.index.json`, `modeling_marlin.py` fetched and vendored): §1.3 EOS ids, §1.4 the three inference modes, §2.2 the hybrid layer schedule, §3 parameters, §4 weight memory, §5.1–§5.2 KV and GDN state, §5.5 prefix caching, §6.1–§6.5 compute and the video token budget, §7 MTP, §8 engines and kernels, §9 quantised variants, §10 estimates, §11 cost, §12 open questions
- [`h100.md`](h100.md) · [`h200.md`](h200.md) · [`b200.md`](b200.md) · [`b300.md`](b300.md) · [`gb300.md`](gb300.md) · [`a100.md`](a100.md) · [`rtx6000-pro.md`](rtx6000-pro.md) · [`mi355x.md`](mi355x.md) — the eight pair documents, each with its own §0 verdict, §1 fit, §2 optimization matrix, §3 throughput, §4 cost, §5 deployment shape, §6 risks, Sources and Audit log
- [`MODEL_CARD.md`](MODEL_CARD.md) · [`FILES.md`](FILES.md) · [`marlin_gb300.py`](marlin_gb300.py) — vendored inputs and the GB300 derivation script

**Foundation documents** referenced by the pair docs and cross-checked above:

- [`research/METHODOLOGY.md`](../../METHODOLOGY.md) §1–§8 — all formulas, the S1–S4 scenarios, the blended definition, the pinned GPU and model tables, and §3's consistency rule (no batch row above `max_concurrency`)
- [`research/gpus/`](../../gpus/): [`a100.md`](../../gpus/a100.md) · [`h100.md`](../../gpus/h100.md) · [`h200.md`](../../gpus/h200.md) · [`b200.md`](../../gpus/b200.md) · [`b300.md`](../../gpus/b300.md) · [`gb300.md`](../../gpus/gb300.md) · [`rtx6000-pro.md`](../../gpus/rtx6000-pro.md) · [`mi355x.md`](../../gpus/mi355x.md)
- [`research/cross-cutting/flash-attention.md`](../../cross-cutting/flash-attention.md) §9.1–§9.7 (per-GPU kernel grids), §14.3 (GDN backends), §16.3–§16.4 (the Marlin rows), §17
- [`research/cross-cutting/quantization-formats.md`](../../cross-cutting/quantization-formats.md) §8.1–§8.4 (KV quant per GPU), §9.6 (checkpoint × GPU matrix, note *o*)
- [`research/cross-cutting/inference-engines.md`](../../cross-cutting/inference-engines.md) §6.5 (**written pre-gate; superseded by architecture.md §8.1 — see disagreement #6**), §7.1–§7.3
- [`research/cross-cutting/serving-optimizations.md`](../../cross-cutting/serving-optimizations.md) §1.1–§1.5, §2.2–§2.5, §3.5, §4.2–§4.3, §6.1–§6.3, §7.4
- [`research/cross-cutting/cloud-pricing.md`](../../cross-cutting/cloud-pricing.md) **§5.14 planning prices — the only price source any document above uses**, cited by row name
- [`research/cross-cutting/inferencex-api.md`](../../cross-cutting/inferencex-api.md) — the retrieval recipe, and the confirmation that **no Marlin rows exist**

**Naming collision, stated once for anyone grepping issue trackers:** "Marlin" is also the name of
vLLM's INT4 W4A16 GEMM kernel, and on several GPUs above that kernel is what a quantised Marlin-2B
would run on. Searches for "vllm marlin" return the kernel, not this model
([architecture.md §8.7](architecture.md)).
