# NemoStation/Marlin-2B on NVIDIA RTX PRO 6000 Blackwell Server Edition 96GB GDDR7 (PCIe only, no NVLink; sm_120)

Research date: **2026-09-19**. Formulas, legend, scenarios and rules per
[`research/METHODOLOGY.md`](../../METHODOLOGY.md). Model facts from
[`architecture.md`](architecture.md); hardware facts from
[`research/gpus/rtx6000-pro.md`](../../gpus/rtx6000-pro.md); kernel, format, engine, caching and
price facts from the five cross-cutting documents, cited inline by path + section. Every
derived number is labelled `est.` and produced by
[`scratchpad/marlin_rtx.py`](#appendix--the-arithmetic) — the script's own output tables are
pasted below without retyping.

---

## 0. Verdict

1. **Runnable today, yes — but only `transformers` runs *this checkpoint*.** `transformers >= 5.7.0`
   + `trust_remote_code=True` loads `MarlinForConditionalGeneration` via `auto_map` on sm_120 with
   nothing special ([architecture.md §8.4](architecture.md#84-engine-matrix)); **vLLM 0.29.0** serves
   the same weights only after `--hf-overrides '{"architectures":["Qwen3_5ForConditionalGeneration"]}'`,
   which is documented API but **⚠️ never validated end-to-end** on any GPU, let alone this one
   ([architecture.md §8.1](architecture.md#81-what-vllm-compatible-concretely-means);
   [cross-cutting/inference-engines.md §6.5](../../cross-cutting/inference-engines.md) still records
   "no engine supports it" — see §6 for the disagreement).
2. **Min 1 GPU, recommended 1 GPU, TP=1.** 4.43 GB of weights on a 96 GB card. Scale by
   **replicas (DP), never TP** — this card has no NVLink and TP over PCIe Gen5 collapses
   (measured 6–7 tok/s at TP=4 vs 46–49 at TP2+PP2, [gpus/rtx6000-pro.md §4](../../gpus/rtx6000-pro.md#4-interconnect-and-topology)).
3. **Weight format actually executed: BF16, natively, all 618 tensors** — no quantisation needed
   and none published for this model in any FP4 format
   ([cross-cutting/quantization-formats.md §9.6](../../cross-cutting/quantization-formats.md#96-checkpoint--gpu-format-support-matrix), note ᵒ).
4. **Interactive (S1, 4K in / 512 out, TPOT ≤ 50 ms at batch 256): $0.037–0.086 per 1M output
   tokens**, blended $0.0135–0.0311 per 1M. **Max-throughput (S4, batch 1,019 = KV ceiling):
   $0.032–0.072 per 1M output.** Video framing: **$0.59–1.35 per 1,000 two-minute clips**,
   **$0.00029–0.00067 per video-minute**. Prices are `planning price · rtx6000-pro · low` $1.80 /
   `· high` $4.143 / `· res1y` $1.30 ([cloud-pricing.md §5.14](../../cross-cutting/cloud-pricing.md#514-planning-prices--the-three-rows-every-other-doc-cites)).
5. **Confidence: `estimate`.** Zero published measurements exist for Marlin-2B on any hardware
   ([architecture.md §10.2](architecture.md)), and zero for any 2B hybrid-GDN video model on sm_120.
   Every throughput number here is roofline. The nearest *measured* anchors on this exact card are a
   397B MoE (InferenceX, §3.5) and a Qwen3-4B dense (§3.5) — neither is this model.

---

## 1. Fit

### 1.1 Inputs, stated before use

| Input | Value | Source / disagreement |
|---|---:|---|
| HBM as deployed | **96 GB** (89.41 GiB) | METHODOLOGY §8; [gpus/rtx6000-pro.md §2](../../gpus/rtx6000-pro.md#2-memory-and-bandwidth) — the card is 96 GB (10⁹), **not** 96 GiB |
| usable at 0.90 | 86.40 GB / 80.47 GiB | METHODOLOGY §3 |
| HBM bandwidth | **1,597 GB/s** | Server Edition. **Not 1,792** — that is the Workstation Edition, and aggregators quote it against SE silicon ([gpus/rtx6000-pro.md §2, §11 #20](../../gpus/rtx6000-pro.md#2-memory-and-bandwidth)) |
| BF16 / FP8 / FP4 dense | 480 / 960 / 1,920 TFLOPS | [gpus/rtx6000-pro.md §3c](../../gpus/rtx6000-pro.md#3c-server-edition--nvidia-publishes-mixed-densesparse-here-is-the-reconciliation). NVIDIA's printed 1 / 2 / 4 PFLOPS are the **sparse** figures |
| Weights, tie honoured | **4.4265 GB / 4.1225 GiB** | [architecture.md §4](architecture.md#4-weight-memory-by-dtype) — "the number to plan with" |
| Weights, tie ignored | **5.4436 GB / 5.0697 GiB** | [architecture.md §3.2](architecture.md#32-the-two-legitimate-parameter-counts). **⚠️ Disagreement to record:** METHODOLOGY §8 pins "5.444 GB BF16" and [gpus/rtx6000-pro.md §9g](../../gpus/rtx6000-pro.md#9g-the-four-repo-models-on-this-card--fit-and-kv-only) sizes with 5.44 GB; architecture.md §4 plans with 4.426 GB. Both are correct for what they measure — §8's is the **on-disk** figure (the duplicated `lm_head`), §4's is **resident with the tie honoured**. Whether vLLM honours the tie on a checkpoint that materialises `lm_head.weight` is ⚠️ unverified ([architecture.md §8.1](architecture.md)), so **both rows are shown below**; the difference is 1.02 GB and moves 8 K concurrency by 1.4 % |
| Decode weight read | **3.7637 GB** | language tower + `lm_head` only; the 331 M-param ViT is idle after prefill ([architecture.md §6.2](architecture.md#62-bytes-read-per-decode-step)) |
| KV / token | **12,288 B BF16 · 6,144 B FP8** | 6 of 24 layers are full attention, GQA 2 KV heads × head_dim 256 ([architecture.md §5.1](architecture.md#51-kv-cache--only-6-of-24-layers-have-one)) |
| Fixed state / seq | **19,537,920 B = 18.63 MiB**, `S = 1` | 18 Gated-DeltaNet layers, fp32 recurrent + BF16 conv ([architecture.md §5.2](architecture.md#52-fixed-per-sequence-state--the-gdn-recurrence)). `S = 1` because no MTP weights ship ([architecture.md §7.1](architecture.md)) — unlike Kimi-K3's `S = 5` |
| Activation workspace | **4 GiB (4.295 GB)** | METHODOLOGY §3 band is 2–6 GB; 4 GiB matches [architecture.md §8.5](architecture.md#85-sizing-parallelism-and-per-gpu-caveats) and is generous for a 2B model, tight for a 240-frame ViT prefill chunk |

```
KV budget (tie honoured) = 86.40e9 − 4.4265e9 − 4.295e9 = 77.679e9 B = 72.34 GiB
KV budget (tie ignored)  = 86.40e9 − 5.4436e9 − 4.295e9 = 76.661e9 B = 71.40 GiB
max_concurrency(ctx)     = floor( budget × n_gpus / (ctx × kv_bytes_per_token + 19,537,920) )
```

### 1.2 Fit over the GPU counts this topology allows

Topology set is **{1, 2, 4, 8, 16, …}**. There is no 18/36/72 step — that is GB300 NVL72; this card
has **no NVLink and no NVSwitch**, and
[gpus/rtx6000-pro.md §4](../../gpus/rtx6000-pro.md#4-interconnect-and-topology) states multi-node LLM
serving "is not a supported design point". **Every GPU count above 1 is a replica count, not a shard
count** — the parallelism column below says `TP=1 × n` for that reason.

```
GPUs | agg KV budget GB |       8K bf16 |        8K fp8 |      32K bf16 |       32K fp8 |     128K bf16 |      128K fp8 |       1M bf16 |        1M fp8
-------------------------------------------------------------------------------------------------------------------------------------------------------
   1 |             77.7 |           646 |         1,111 |           183 |           351 |            47 |            94 |             6 |            12
   2 |            155.4 |         1,292 |         2,223 |           367 |           703 |            95 |           188 |            12 |            24
   4 |            310.7 |         2,584 |         4,447 |           735 |         1,406 |           190 |           376 |            24 |            48
   8 |            621.4 |         5,169 |         8,894 |         1,471 |         2,813 |           381 |           753 |            48 |            96
  16 |           1242.9 |        10,339 |        17,788 |         2,943 |         5,627 |           762 |         1,506 |            96 |           192

  same, with the tie NOT honoured (loader keeps the duplicated lm_head, 5.444 GB), 1 GPU:
   1 |             76.7 |           637 |         1,097 |           181 |           347 |            47 |            92 |             5 |            11
```

| GPUs | Parallelism | Weights / GPU (incl. replicated tensors) | Activation workspace / GPU | KV budget / GPU | Multi-node fabric? |
|---:|---|---:|---:|---:|---|
| 1 | **TP=1** (recommended) | 4.427 GB (all tensors; nothing sharded) | 4 GiB | **77.68 GB** | no |
| 2 | TP=1 × 2 replicas (**DP**) | 4.427 GB each | 4 GiB | 77.68 GB each | no |
| 4 | TP=1 × 4 replicas | 4.427 GB each | 4 GiB | 77.68 GB each | no |
| 8 | TP=1 × 8 replicas (one PCIe chassis) | 4.427 GB each | 4 GiB | 77.68 GB each | no — but see the 8-card qualification note below |
| 16 | TP=1 × 16 replicas (2 chassis) | 4.427 GB each | 4 GiB | 77.68 GB each | **no GPU fabric needed** — replicas share nothing; a plain NIC + load balancer suffices |

`est.` throughout. The 8-card row carries a hardware caveat that is not about this model: Lenovo
qualifies only **2× at 600 W or 4× at the 450 W cap** in the SR675 V3 line, and the card is
**passively cooled**, so "8× in a server" is a general-market configuration and not a universally
qualified one ([gpus/rtx6000-pro.md §11 #12](../../gpus/rtx6000-pro.md#11-known-gotchas)).

### 1.3 Video-native contexts — the only ones a video fleet actually sees

The 240-frame cap bounds every video request at ~23.5 K tokens regardless of clip length
([architecture.md §6.3](architecture.md#63-video-token-budget-per-request)), so concurrency is a
**constant**, not a distribution:

```
   ctx   5,920 (30 s video)             : BF16 KV   841   FP8 KV  1,389
   ctx  11,800 (60 s)                   : BF16 KV   472   FP8 KV    843
   ctx  23,520 (2 min and every longer) : BF16 KV   251   FP8 KV    473
```

**⚠️ Discrepancy with [architecture.md §8.5](architecture.md#85-sizing-parallelism-and-per-gpu-caveats),
recorded per METHODOLOGY §8.** That table prints **878** concurrent 30-second videos on this card;
recomputing from its own stated 72.3 GiB budget gives **841**. Its other four `rtx6000-pro` cells
(251 / 183 / 47 / 23) reproduce exactly. The 5,920 cell is the odd one out and **841** is used here.

### 1.4 What cannot be sharded — stated explicitly, as required

Nothing here *needs* sharding, but the question is asked because it determines whether TP is even an
option if someone reaches for it. From the 618-name weight map in
[architecture.md §2.2 / §3.3](architecture.md#22-text-tower--hybrid-linearfull-attention-dense-no-moe):

| Tensor group | Shardable under TP? | Consequence |
|---|---|---|
| `self_attn.k_proj` / `v_proj` — **`num_key_value_heads = 2`** | **Only up to TP=2.** | At **TP > 2 vLLM replicates the KV heads across ranks**, so the 12,288 B/token cost is paid *per rank*: TP=4 costs 2× the aggregate KV of TP=2 for the same sequences. A hard argument against TP here even before PCIe enters the picture. |
| `self_attn.q_proj` (8 heads, `attn_output_gate` doubles it to 4096 wide) | up to TP=8 | — |
| `self_attn.q_norm` / `k_norm` `[256]` | **No — replicated** | 3,072 params, immaterial |
| `linear_attn.*` GDN heads (16 key / 16 value, head_dim 128) | up to TP=16 by head | the 18.63 MiB recurrent state shards with them |
| `linear_attn.conv1d.weight` `[6144,1,4]` — depthwise | shards with the channel split only | 442,368 params |
| `linear_attn.A_log`, `dt_bias` `[16]`, `linear_attn.norm` `[128]` | **No — replicated** | SSM scalars, 576 + 2,304 params |
| `input_layernorm`, `post_attention_layernorm`, `model.norm` | **No — replicated** | 100,352 params |
| `embed_tokens` / `lm_head` `[248320, 2048]` | vocab-parallel, but | 508.6 M params = **23 % of the model**; vLLM keeps embeddings replicated in most configs, so TP shrinks the per-GPU weight footprint far less than 1/n |
| **Entire 331 M-param ViT** | encoder is run **data-parallel**, not TP | `--mm-encoder-tp-mode data` is the consistent recommendation across every multimodal model in this repo ([serving-optimizations.md §6.2](../../cross-cutting/serving-optimizations.md#62-encoder-parallelism)) |

**Conclusion: the non-shardable replicated set is ~509 M params (the embedding) plus ~0.5 M of norms
and SSM scalars.** Sharding a 4.43 GB model to save 3.3 GB per card, over a fabric that is measured
to cost ~85 % overhead, is strictly worse than running one replica per card. `min_gpus = 1`,
`recommended_gpus = 1`.

---

## 2. What runs on this GPU for this model

The card is `sm_120` / cc **12.0** — the GB202 prosumer Blackwell die, **not** sm_100 (B200) or
sm_103 (B300/GB300). It has **no `tcgen05` MMA and no TMEM**, so every kernel written against
`tcgen05` cannot run at all, and `major == 10` gates fail
([gpus/rtx6000-pro.md, opening note](../../gpus/rtx6000-pro.md)). It also exposes only
**101,376 B (99 KB)** of opt-in shared memory per block against Hopper/B200's ~228 KB
([gpus/rtx6000-pro.md §1](../../gpus/rtx6000-pro.md#device-limits-that-bite-kernels)).

| Optimization | Status | Kernel / flag | Expected effect |
|---|---|---|---|
| **Full-attention kernel, 6 layers, `head_dim = 256`, GQA 8:2** | **native (FA2 class)** | vLLM `FLASH_ATTN` → **FA2** (`device_capability.major == 12` matches neither the `9` nor the `10` branch); or FlashInfer `backend="fa2"`, which `determine_attention_backend()` returns for SM12x | Works at hd 256 — `supports_head_size` passes for `head_size <= 256`. But it is an **SM80-era `mma.sync` kernel in 99 KB of SMEM**, not a warp-specialised one. [flash-attention.md §9.5](../../cross-cutting/flash-attention.md#95-rtx-pro-6000-blackwell-sm_120) · [architecture.md §8.3](architecture.md#83-attention-kernels-per-gpu--the-head_dim-256-story) |
| FA3 | **unsupported** | — | SM90 only. |
| **FA4 hd256 2-CTA kernel** | **unsupported — and this is the single biggest kernel loss for this model** | `uses_fa4_hd256_kernel()` requires `capability.major in (10, 11)`; vLLM additionally refuses FA4 on 12.x outright | Marlin hits `head_size == 256` **exactly**, which is precisely what the dedicated B200/B300 kernel exists for. This card gets the generic FA2 path instead. [architecture.md §8.3](architecture.md#83-attention-kernels-per-gpu--the-head_dim-256-story) · [flash-attention.md §9.5](../../cross-cutting/flash-attention.md#95-rtx-pro-6000-blackwell-sm_120) |
| FA4 upstream `FlashAttentionForwardSm120` | ⚠️ **TO BE VERIFIED** | exists, but *"uses SM80 MMA with SM120 SMEM capacity"*, **no paged KV**, no SplitKV, no FP8 | unusable for a paged-KV server today. [flash-attention.md §9.5](../../cross-cutting/flash-attention.md#95-rtx-pro-6000-blackwell-sm_120) |
| **B12X backend** (vLLM `uv pip install "vllm[b12x]"`) | **native, niche, opt-in** | *"supports causal decoder attention on NVIDIA SM120 and SM121 GPUs"*, bf16, **head sizes 64/128/192/256**, KV `auto` only | The one SM12x-**specific** attention backend, and it covers hd 256. ⚠️ **TO BE VERIFIED for this model** — no report of B12X on a hybrid-GDN VLM. [flash-attention.md §9.5](../../cross-cutting/flash-attention.md#95-rtx-pro-6000-blackwell-sm_120) |
| Triton attention | native (fallback) | `TRITON_ATTN` | always available, never the tuned path |
| trtllm-gen FMHA / `trtllm_mha` prefill | **unsupported** | no SM120/121 cubins; NVIDIA: *"no plan to SM120/121 so far"*, issue closed 2026-07-16 | SGLang: *"`trtllm_mha` is supported for `--decode-attention-backend` only"* on SM120. [gpus/rtx6000-pro.md §5b](../../gpus/rtx6000-pro.md#5b-flashinfer--the-actual-supported-attention-path-on-sm_120) |
| FlashMLA / FlashInfer-MLA / sparse DSA indexer | **not applicable** | — | Marlin has **no MLA and no sparse/indexer attention** ([architecture.md §2.5](architecture.md#25-special-mechanisms-summary)). The card's worst area is one this model never touches. |
| Sliding-window / attention sinks | **not applicable** | — | no `sliding_window` field; the 6 full layers are **global** |
| **Linear-attention (GDN) prefill, 18 layers** | **native** | vLLM `_resolve_gdn_prefill_backend` selects **FlashInfer** on *"Blackwell (SM12.x) with `head_k_dim == 128`, `cuda_runtime >= 13`"*. Marlin's `linear_key_head_dim` is **128** ✅ | **requires CUDA ≥ 13** — on CUDA 12.8 this silently drops to Triton/FLA. The in-tree CuteDSL GDN kernel is explicitly off: *"targets SM100 only, so it stays off here."* [flash-attention.md §14.3](../../cross-cutting/flash-attention.md#143-hybrid-linear-attention--gated-deltanet-gdn) |
| **GDN decode, 18 layers** | **native** | fused CUDA `fused_gdn_decode_post_conv_mtp`, gated on `has_device_capability(80)` | needs K=V=128 ✅, BF16 conv cache ✅, **BF16 or FP32 recurrent state** ✅ (`mamba_ssm_dtype: float32`), else silent Triton fallback. [flash-attention.md §14.3](../../cross-cutting/flash-attention.md#143-hybrid-linear-attention--gated-deltanet-gdn) |
| **FlashQLA (Qwen's GDN kernel)** | **native (SM120 listed)** | FlashQLA README: *"SM90, SM100, SM103, **SM120** or SM121"*, CUDA ≥ 12.8, PyTorch ≥ 2.8; claims *"2–3× forward speedup"* vs FLA Triton | **This card qualifies where A100 and MI355X do not.** ⚠️ **TO BE VERIFIED** whether vLLM/SGLang actually dispatch to FlashQLA rather than their own kernels — SGLang carries its own `triton_gdn_fused_proj`. [architecture.md §8.3](architecture.md#83-attention-kernels-per-gpu--the-head_dim-256-story) |
| **Weight format — BF16 checkpoint** | **native execution, no conversion** | 480 TFLOPS dense BF16 tensor cores | Marlin-2B is **BF16 in all 618 tensors**; [quantization-formats.md §9.6](../../cross-cutting/quantization-formats.md#96-checkpoint--gpu-format-support-matrix) marks the cell **native** with note ᵒ: *"Format support is total and uninteresting for a BF16 checkpoint."* **None of this card's FP4 pathologies apply** — the broken NVFP4 MoE grouped-GEMM path ([gpus/rtx6000-pro.md §6a](../../gpus/rtx6000-pro.md#6a-the-nvfp4-moe-grouped-gemm-problem-read-this-before-quoting-nvfp4-numbers)) needs an MoE, and this model is **dense**. |
| NVFP4 / MXFP4 weights | **no checkpoint exists** | — | ⚠️ [architecture.md §9](architecture.md#9-available-quantised-variants): *"no NVFP4, MXFP4, AWQ or FP8 checkpoint"*. Dense NVFP4 GEMM on sm_120 *does* work ([gpus/rtx6000-pro.md §6a](../../gpus/rtx6000-pro.md#6a-the-nvfp4-moe-grouped-gemm-problem-read-this-before-quoting-nvfp4-numbers): *"Dense (non-MoE) FP4 GEMM works correctly"*), so a Marlin NVFP4 build would be a **safe** sm_120 target — nobody has made one. |
| INT4 W4A16 (`prasannaJagadesh/marlin-2B-GPTQ-4BITS`) | **dequant path, published checkpoint exists** | vLLM **Marlin W4A16 kernel** — *"the de-facto fastest and most correct path on this GPU today"* | Saves **1.7 GB/step** of decode read ⇒ b=1 TPOT 4.23 → **2.10 ms**, but only **+5 %** at b=128 (§3.4). ⚠️ **no accuracy eval published for any Marlin-2B quantisation** ([architecture.md §9](architecture.md#9-available-quantised-variants)). Beware the name collision: "Marlin" is also the kernel. [gpus/rtx6000-pro.md §6](../../gpus/rtx6000-pro.md#6-quantization-format-support) |
| INT8 W8A16 (`tintwotin/Marlin-2B-SDNQ-int8`) | dequant path, checkpoint exists | Marlin / CUTLASS | same shape of trade as INT4, less of it |
| **KV-cache quant — FP8 E4M3** | **native (storage), ⚠️ path-dependent for the math** | `--kv-cache-dtype fp8_e4m3` | **1.88× concurrency, 1.83× decode throughput at video context** (§3.4). Works on the **generic GQA path** — which is what this model is. The SM120 FP8 rejection in [gpus/rtx6000-pro.md §5d](../../gpus/rtx6000-pro.md#5d-fp8-attention) is specific to `trtllm_mha` + MLA, and *"the sanctioned XQA/FMHA_v2 fallback on SM12x is BF16-only"* — so ⚠️ **whether the attention math runs in FP8 or the cache is dequantised on read is unverified here**; budget the capacity win, not necessarily the speed win. [quantization-formats.md §8.1](../../cross-cutting/quantization-formats.md#81-what-each-engine-exposes) marks the sm120 FP8-KV cell ✅ |
| KV-cache quant — NVFP4 | **unsupported on the generic path** | — | [quantization-formats.md §8.1](../../cross-cutting/quantization-formats.md#81-what-each-engine-exposes) marks `nvfp4` "✅ ⚠️" for this card, but that entry rests on the **sparse-MLA** PR #4955 SM120 kernel; TRT-LLM has **no `QE4m3KvE2m1` SM12x variant** at all. For a plain GQA model treat NVFP4 KV as **not available**. [gpus/rtx6000-pro.md §11 #5](../../gpus/rtx6000-pro.md#11-known-gotchas) |
| **Prefix caching** | **⚠️ constrained *and* worthless for this workload** | vLLM **raises** on `mamba_cache_mode == "all"` for Qwen3.5 — must pass `--mamba-cache-mode=align`, which the recipe itself calls *"currently experimental"* | Realistic hit rate for video captioning is **~0 %**: the only cacheable span is a ~30–40-token scaffold, **< 0.2 %** of a 23,520-token request, and the video precedes it. `.multi_find` re-trims the clip, so each pass has a different prefix. [architecture.md §5.5](architecture.md#55-prefix-caching--a-hard-constraint-and-a-workload-that-cannot-use-it) |
| **Speculative decoding (MTP / nextn / EAGLE / DSpark)** | **unsupported — no draft weights ship** | `config.json` declares `mtp_num_hidden_layers: 1`; the 618-name weight map contains **zero** `mtp` tensors, and the base−Marlin delta is exactly one 60,828,160-param MTP module | vLLM *registers* `Qwen3_5MTP`, so the engine support exists and the weights do not. The base Qwen3.5-2B vLLM recipe offers `{"method":"mtp","num_speculative_tokens":1}` as an opt-in feature — **it does not apply to Marlin**. DSpark: n/a, no DeepSeek component. [architecture.md §7](architecture.md#7-speculative-decoding-and-mtp) · [recipes.vllm.ai/Qwen/Qwen3.5-2B.json](https://recipes.vllm.ai/Qwen/Qwen3.5-2B.json) |
| **EP** | **not applicable** | — | dense model, no experts |
| **TP** | supported, **actively harmful here** | `--tensor-parallel-size` | see §1.4 (KV replication at TP>2) and §1.2 (PCIe). Use TP=1. |
| **DP / attention-DP** | **native, and the right answer** | one replica per card + a load balancer | measured on this card: SGLang `dp=4` on Qwen3.6-35B-A3B gave **1,408 tok/s @8 concurrent, 3,190 @16** where TP=4 collapsed. [gpus/rtx6000-pro.md §4](../../gpus/rtx6000-pro.md#4-interconnect-and-topology) |
| **PD disaggregation (Dynamo / SGLang PD)** | **⚠️ TO BE VERIFIED, and almost certainly not worth it** | — | *"No sm_120-specific support or exclusion statement was found"* for Dynamo; *"Given that Dynamo's disaggregated prefill/decode design assumes a fast KV transfer fabric and this card has PCIe only, expect disaggregation to be bandwidth-limited here."* At 4.43 GB weights, a PD split costs a whole extra replica's weights to move 288 MB of KV per request over PCIe. [gpus/rtx6000-pro.md §7](../../gpus/rtx6000-pro.md#nvidia-dynamo) |
| **CUDA graphs** | **native — and load-bearing** | on by default; `--max-cudagraph-capture-size` | Known Qwen3.5 failure: `causal_conv1d_update` asserts when *"cuda graph capture size is larger than mamba cache size"* — lower the capture size (default 512). On this card CUDA graphs are documented as *"the single largest perf knob"* on at least one path (5 → 30–35 tok/s). [architecture.md §8.7](architecture.md#87-open-engine-issues) · [gpus/rtx6000-pro.md §11 #8](../../gpus/rtx6000-pro.md#11-known-gotchas) |
| **Chunked prefill / continuous batching** | native, assume on | `--chunked-prefill-size` / `--max-num-batched-tokens` | A real sm_120 report needed **`--max-num-batched-tokens 2096`** to clear *"a Mamba cache alignment assertion error"* on a Qwen3.5 hybrid VLM ([HF discussion, RTX 5090 = sm_120](https://huggingface.co/Qwen/Qwen3.5-35B-A3B-GPTQ-Int4/discussions/3)) — directly transferable. |
| **Multimodal encoder placement** | **native, data-parallel** | `--mm-encoder-tp-mode data`, `--mm-processor-cache-type shm` | At TP=1 the flag is a no-op, but it is the correct setting the moment anyone adds ranks. `--language-model-only` is the wrong lever here — this is a **video** workload, the ViT is 39 % of prefill compute. [serving-optimizations.md §6.2](../../cross-cutting/serving-optimizations.md#62-encoder-parallelism) · [architecture.md §6.4](architecture.md) |
| **Hardware video decode (NVDEC)** | **native — 4× NVENC / 4× NVDEC / 4× JPEG on this card** | vLLM PyNvVideoCodec backend; `--mm-ipc-gpu-memory-gb`, `"hw_decoders":2` in `--media-io-kwargs`; **CUDA MPS required** | The one optimization that is *specific to this pair* and probably matters most. vLLM measured *"more than double the throughput"* at 8×H100 vs CPU decode, and *"CPU-based video decoding can quickly become a bottleneck, maxing out CPU cores even with just 2 or 4 GPUs"* [src](https://vllm.ai/blog/2026-09-18-pynvvideocodec). ⚠️ not measured on sm_120. |

---

## 3. Throughput and latency

### 3.1 Assumptions, stated up front per METHODOLOGY §4

| Parameter | Value used | Justification and the disagreement |
|---|---:|---|
| **MBU (decode)** | **0.60** central; band **0.50–0.65** | METHODOLOGY §4 gives 0.5–0.7 for first-gen Blackwell software. [gpus/rtx6000-pro.md §9b](../../gpus/rtx6000-pro.md#9b-decode-upper-bound--bandwidth--bytes-moved) narrows it for sm_120: *"with Marlin dequant in the loop and no NVLink, use MBU 0.50–0.65 as the planning band and treat 0.70 as optimistic."* [architecture.md §10.3](architecture.md) uses **0.60** for this card. 0.60 is used here so the numbers line up across the model's GPU docs; §3.4 gives the full sensitivity. |
| **MFU (prefill GEMM)** | **0.40** | [architecture.md §10.3](architecture.md) uses 0.40. Consistent with the measured dense-BF16 cuBLAS ceiling of **74.7 % of peak** on the 600 W part ([gpus/rtx6000-pro.md §3d](../../gpus/rtx6000-pro.md#3d-sanity-check-against-a-measured-dense-gemm)). |
| **MFU (attention term)** | **0.25** | [gpus/rtx6000-pro.md §5e](../../gpus/rtx6000-pro.md#5e-measured-attention-throughput): *"Use MFU_attn 0.20–0.35 BF16 for planning, lower than the METHODOLOGY default for Hopper"* — because the kernel is SM80-era, SMEM is 43 % of Hopper's, and there is no warp-specialised pipeline. **⚠️ This is a deviation from architecture.md §10.3**, which folds attention into a single 0.40. At 4 K prompts it changes TTFT by 2 %; at 128 K it changes it by 35 %. Both figures are shown. |
| Peak dense | BF16 **480 TFLOPS**, BW **1.597 TB/s** | [gpus/rtx6000-pro.md §3c, §2](../../gpus/rtx6000-pro.md#3c-server-edition--nvidia-publishes-mixed-densesparse-here-is-the-reconciliation) |
| Decode model | `max( (3.7637e9 + batch×ctx×12,288) / (BW×MBU), 2×1,881,825,088×batch / (480e12×0.40) )` | METHODOLOGY §4. **Dense model ⇒ all language weights read every step regardless of batch.** KV read scales with `batch × ctx`. |
| Prefill model | `2×1,881,825,088×T/(480e12×0.40) + 24,576·T²/(480e12×0.25) [+ frames×0.2722e12/(480e12×0.40)]` | METHODOLOGY §4; attention constant and ViT per-frame cost from [architecture.md §6.1, §6.4](architecture.md#61-flops-per-token--text) |

At every point on these tables the **bandwidth term dominates the FLOP term by 4–8×** — at b=256,
4 K context, 19.06 ms of bandwidth against 5.02 ms of compute. This model on this card is a pure
bandwidth problem, which is exactly the card's weakest axis (1,597 GB/s is **0.48× an H100**,
[gpus/rtx6000-pro.md §12a](../../gpus/rtx6000-pro.md#a-decode-bandwidth)).

### 3.2 Estimated decode — the standard scenario grid (1 GPU, BF16 weights + BF16 KV)

Script output, verbatim. Per METHODOLOGY §3's consistency rule, any `batch > max_concurrency(ctx)`
is printed as `infeasible (KV)` and never as a number.

```
S1  4K in / 512 out   (ctx at end of generation = 4,608; max_concurrency = 1019)
   batch |   TPOT ms |  agg tok/s |  tok/s/GPU | bw-bound ms | flop-bound ms
       1 |      3.99 |        251 |        251 |        3.99 |          0.02
       8 |      4.40 |      1,818 |      1,818 |        4.40 |          0.16
      32 |      5.82 |      5,499 |      5,499 |        5.82 |          0.63
      64 |      7.71 |      8,301 |      8,301 |        7.71 |          1.25
     128 |     11.49 |     11,138 |     11,138 |       11.49 |          2.51
     256 |     19.06 |     13,434 |     13,434 |       19.06 |          5.02

S2  32K in / 1K out   (ctx at end of generation = 33,792; max_concurrency = 178)
   batch |   TPOT ms |  agg tok/s |  tok/s/GPU | bw-bound ms | flop-bound ms
       1 |      4.36 |        229 |        229 |        4.36 |          0.02
       8 |      7.39 |      1,082 |      1,082 |        7.39 |          0.16
      32 |     17.80 |      1,798 |      1,798 |       17.80 |          0.63
      64 |     31.66 |      2,021 |      2,021 |       31.66 |          1.25
     128 |     59.40 |      2,155 |      2,155 |       59.40 |          2.51
     256 |                                              infeasible (KV)

S3  128K in / 2K out   (ctx at end of generation = 133,120; max_concurrency = 46)
   batch |   TPOT ms |  agg tok/s |  tok/s/GPU | bw-bound ms | flop-bound ms
       1 |      5.63 |        177 |        177 |        5.63 |          0.02
       8 |     17.58 |        455 |        455 |       17.58 |          0.16
      32 |     58.56 |        546 |        546 |       58.56 |          0.63
      64 |                                              infeasible (KV)
     128 |                                              infeasible (KV)
     256 |                                              infeasible (KV)
```

`est.` **tok/s per GPU equals aggregate tok/s because `n_gpus = 1`.** At 2 or more cards, both the
concurrency and the aggregate scale linearly and TPOT is unchanged — that is the whole point of
replication.

**⚠️ S3 is a text-only scenario for this model.** The 240-frame cap means a video request can never
reach 128 K tokens ([architecture.md §6.3](architecture.md#63-video-token-budget-per-request)); S3 is
reachable only with a long text prompt, and `max_position_embeddings` is 262,144 with
`rope_type: default`, so 1 M is out of spec entirely.

### 3.3 Estimated TTFT

```
   prompt T |  GEMM ms |  attn ms |  total ms |  TTFT@0% ms | TTFT@90% hit ms | attn share
      4,096 |     80.3 |      3.4 |      83.7 |        87.7 |            12.0 |      4.1%
     32,768 |    642.3 |    219.9 |     862.2 |       866.6 |            70.8 |     25.5%
    131,072 |   2569.3 |   3518.4 |    6087.8 |      6093.4 |           297.7 |     57.8%

  prefill throughput (1 GPU):  T=4,096 -> 48,921 tok/s | T=32,768 -> 38,004 | T=131,072 -> 21,530
```

`est.` **TTFT@0 % is an isolated prefill on an idle GPU** plus one decode step. **The 90 % column is
arithmetic only and must not be planned on** — §2 explains that this workload's realistic prefix-cache
hit rate is ~0 %, and that vLLM will not even start Qwen3.5 with `--mamba-cache-mode=all`.

At high concurrency TTFT is queueing-dominated, not prefill-dominated. Worst-case admission burst
(all `B` requests arriving at once, prefill at 48,921 tok/s):

```
  concurrency    1: 0.08 s |    8: 0.67 s |   32: 2.68 s |   64: 5.36 s
  concurrency  128: 10.72 s |  256: 21.43 s |  512: 42.87 s
```

`est.` This is why §4 reports the S1 operating point at **batch 256** rather than the
TPOT-maximising 512.

### 3.4 Sensitivity — MBU band, weight format, KV format

```
  MBU 0.50: video-ctx b=128 TPOT  51.04 ms, agg   2,508 tok/s
  MBU 0.55: video-ctx b=128 TPOT  46.40 ms, agg   2,758 tok/s
  MBU 0.60: video-ctx b=128 TPOT  42.54 ms, agg   3,009 tok/s   <- planning point
  MBU 0.65: video-ctx b=128 TPOT  39.26 ms, agg   3,260 tok/s
  MBU 0.70: video-ctx b=128 TPOT  36.46 ms, agg   3,511 tok/s   (optimistic per gpus §9b)

  weight-format levers at video ctx 23,520 (read sizes from architecture.md §6.2):
  BF16            (3.764 GB) b=  1: TPOT  4.23 ms  agg   236 | b=  8:  6.34 ms / 1,262 | b=128: 42.54 ms / 3,009
  FP8 lang linears(2.391 GB) b=  1: TPOT  2.80 ms  agg   358 | b=  8:  4.91 ms / 1,630 | b=128: 41.10 ms / 3,114
  INT4 W4A16 g128 (1.726 GB) b=  1: TPOT  2.10 ms  agg   476 | b=  8:  4.21 ms / 1,898 | b=128: 40.41 ms / 3,168

  FP8 KV at b=128, video ctx: TPOT 23.23 ms vs BF16 42.54 ms -> 1.83x decode, 1.88x concurrency
  KV-read overtakes weight-read at batch = 13.02  ->  batch 14 at video context
```

`est.` **Read this table before reaching for a quantised checkpoint.** Quantising *weights* is worth
**2.0× at batch 1** and **5 %** at batch 128, because past batch ~14 at video context the step is
dominated by KV traffic that weight quantisation does not touch. **Quantising the KV is worth 1.83×
at batch 128** and nearly doubles concurrency. For a batch video fleet the ordering is unambiguous:
`--kv-cache-dtype fp8_e4m3` first, weight quantisation a distant second, and only if the
(⚠️ entirely unmeasured) timestamp accuracy survives.

### 3.5 Measured — what actually exists

**For this pair: nothing.** No throughput or latency measurement of Marlin-2B on any hardware exists
([architecture.md §10.2](architecture.md)): the card has no performance section, HF `model-index` is
`null`, it is not an MLPerf model, and InferenceX carries no Marlin rows. The author's Gradio Space
`@spaces.GPU(duration=75/180)` values are **ZeroGPU quota reservations, not measurements**, on a
time-shared A10G — do not cite them.

For **this GPU**, the nearest published rows, ordered by how transferable they are:

| Measurement | Model | Engine / precision | Config | Result | Transferability | Source |
|---|---|---|---|---|---|---|
| **InferenceX, the only benchmark-suite rows on this card** | Qwen-3.5-397B-A17B | SGLang, fp4, TP=4 | 8 K in / 1 K out, conc 1 / 4 / 16 / 64 | out tok/s/GPU **18.33 / 49.68 / 107.60 / 164.63** (MTP off); **34.80 / 81.14 / 133.83 / 191.80** (MTP on) | **Low** — 397B MoE over PCIe TP4. Useful for one thing only: the **measured MTP decay curve on sm_120** (1.90× → 1.63× → 1.24× → 1.17×) | [InferenceX API](https://inferencex.semianalysis.com/api/v1/benchmarks?model=Qwen-3.5-397B-A17B), `hardware=rtx6000pro`, 2026-07-30; retrieval recipe in [inferencex-api.md §3](../../cross-cutting/inferencex-api.md) |
| Small-dense decode at low concurrency | Qwen3-4B | ⚠️ engine and precision unspecified | short ctx, TP1, conc 8 | **955.7** (Max-Q 300 W) / **958.9** tok/s (600 W) | **Medium** — closest published small-dense point. If BF16 (8 GB) this implies **MBU ≈ 0.535** on a 1,792 GB/s Workstation part. If FP8 it implies 0.27. ⚠️ the precision is not stated, so the calibration is weak | [gpus/rtx6000-pro.md §10c](../../gpus/rtx6000-pro.md#10c-independent--community-measurements) |
| Hybrid-GDN VLM with **video input**, on sm_120 | Qwen3.5-35B-A3B-GPTQ-Int4 | vLLM cu130-nightly, `gptq_marlin`, `--kv-cache-dtype fp8`, `--enable-prefix-caching` | **RTX 5090 32 GB** (also sm_120), 131 K ctx, single stream | **~194–197 tok/s sustained**; *"Using FLASHINFER attention backend"*; image and video *"works out of the box"*; needed **`--max-num-batched-tokens 2096`** to clear a *"Mamba cache alignment assertion error"* | **Highest available** — same SM, same Qwen3.5 hybrid-GDN family, video path exercised, FlashInfer selected. Different card (1,792 GB/s, 32 GB) and a 3B-active MoE, so the tok/s does not transfer; **the flags and the failure mode do** | [HF discussion](https://huggingface.co/Qwen/Qwen3.5-35B-A3B-GPTQ-Int4/discussions/3) |
| Single-GPU 4-bit decode vs datacenter parts | GLM-4.5-Air-AWQ-4bit | vLLM, 8 K ctx, FP8 KV | 1 GPU, conc 256–512 | **3,140 tok/s** vs **H100 2,987** | **Medium** — establishes that at TP=1 this card beats an H100 despite half the bandwidth, because it avoids a parallelism split | [gpus/rtx6000-pro.md §10c](../../gpus/rtx6000-pro.md#10c-independent--community-measurements) |
| Video decode is the real bottleneck | Qwen3-VL-8B-Instruct | vLLM + PyNvVideoCodec | **8×H100**, not this card | GPU (NVDEC) decode gives *"more than double the throughput"* vs CPU; *"CPU-based video decoding can quickly become a bottleneck, maxing out CPU cores even with just 2 or 4 GPUs"* | **High conceptually, zero numerically** | [vLLM blog](https://vllm.ai/blog/2026-09-18-pynvvideocodec) |
| Small-model llama.cpp on this card | none under 8 B | llama.cpp | Max-Q WS | *"No models under 8B parameters"*; *"No vision-language models were successfully tested — several failed to load, including qwen3-vl:32b"* | negative result, worth recording | [vaditaslim.com](https://www.vaditaslim.com/blog/ai/local-llm-benchmarks-rtx-pro-6000) |
| Engine-level: does a recipe exist for the **base** model? | `Qwen/Qwen3.5-2B` | vLLM | — | Recipe exists (`min_vllm_version 0.17.0`, TP1, 262 K ctx, `--trust-remote-code`), but the **only** verified hardware entries are `arc_pro_b60` / `arc_pro_b70`; `…/hw/rtx_pro_6000.json` returns **404** | the base model has no vendor-verified profile on this card either | [recipes.vllm.ai/Qwen/Qwen3.5-2B.json](https://recipes.vllm.ai/Qwen/Qwen3.5-2B.json) |

### 3.6 Why estimate and measurement would diverge — the gaps I expect

1. **The roofline ignores the kernel generation.** Everything above assumes the bandwidth term is
   achievable at MBU 0.60. On sm_120 the full-attention layers run an **SM80-era FA2 kernel in
   99 KB of shared memory** with no warp specialisation, and 6 of 24 layers is enough for that to
   show. Expect real TPOT **above** the table, not below — this is the main reason to plan at the
   0.50–0.55 end of the MBU band rather than 0.60.
2. **FlashQLA / FlashInfer GDN dispatch is unconfirmed.** 18 of 24 layers are Gated DeltaNet. If
   CUDA is < 13, or `head_k_dim` resolution fails, or the engine uses its own Triton kernel,
   those 18 layers lose the claimed 2–3× on the forward pass
   ([architecture.md §8.3](architecture.md#83-attention-kernels-per-gpu--the-head_dim-256-story)).
   That hits **prefill**, which §4 shows is the sustained-throughput limiter.
3. **Video I/O is outside the roofline entirely.** torchcodec decode throughput for 240 frames at
   448×448 is unmeasured everywhere consulted, and vLLM's own measurement says CPU decode saturates
   before 4 GPUs. A fleet built on this card without NVDEC will very likely measure
   **video-decode-bound**, not GPU-bound, and the tables above will look optimistic by a factor
   nobody has published.
4. **Scheduler overhead at batch ≥ 256 on a 2B model.** At b=256 the step is 19 ms and the model is
   2B; per-step Python/scheduler cost in vLLM is not in the roofline. CUDA graphs mitigate it, and
   the `causal_conv1d_update` capture-size assertion is the documented way this goes wrong.
5. **Dense-model roofline is *exact* in one respect that MoE rooflines are not:** `weights_read` has
   no `distinct_experts(batch)` term to get wrong. The METHODOLOGY §4 MoE estimator does not apply
   at all here ([architecture.md §2.2](architecture.md#22-text-tower--hybrid-linearfull-attention-dense-no-moe):
   `active_params == total_params`).

---

## 4. Cost

### 4.1 Prices used — cited by provider + instance name

Per METHODOLOGY §8 and the repo rule, taken from
[`cloud-pricing.md` §5.14](../../cross-cutting/cloud-pricing.md#514-planning-prices--the-three-rows-every-other-doc-cites),
`rtx6000-pro` row, and from nowhere else:

| Tier | $/GPU-hour | Row |
|---|---:|---|
| `planning price · rtx6000-pro · low` | **$1.80** | **Nebius**, "RTX PRO 6000", 1–8 GPUs ([§5.10](../../cross-cutting/cloud-pricing.md#510-rtx-pro-6000-blackwell-server-edition)) |
| `planning price · rtx6000-pro · high` | **$4.143** | **AWS `g7e.48xlarge`**, 8× RTX PRO 6000 Blackwell SE, us-east-1 ([§5.10](../../cross-cutting/cloud-pricing.md#510-rtx-pro-6000-blackwell-server-edition)) |
| `planning price · rtx6000-pro · res1y` | **$1.30** | **Hyperstack**, "RTX PRO 6000 **SE**", reserved ([§5.10](../../cross-cutting/cloud-pricing.md#510-rtx-pro-6000-blackwell-server-edition)) |

`high ÷ low = 2.30×` ([§5.14](../../cross-cutting/cloud-pricing.md#514-planning-prices--the-three-rows-every-other-doc-cites)),
above the 2× threshold, so **every cost below is quoted as a band, never a point**. Spot exists
(Nebius preemptible $0.95, GCP $1.743) but is not a planning price. Hyperstack is the **only** vendor
rate card that says "SE" explicitly — the rest do not distinguish Server (1,597 GB/s) from
Workstation (1,792 GB/s), an **11 % TPOT swing** you are not told about
([gpus/rtx6000-pro.md §8a](../../gpus/rtx6000-pro.md#8a-cloud--on-demand)).

### 4.2 Cost at the S1 interactive operating point (TPOT ≤ 50 ms)

```
S1 interactive, 4K in / 512 out, TPOT <= 50 ms:
   batch |  TPOT ms | out tok/s |  in tok/s | $/1M out low | $/1M out high | $/1M in low | $/1M blended low | blended high
       1 |     3.99 |       251 |    48,921 |       1.9935 |        4.5883 |      0.0102 |           0.5026 |       1.1568  <= SLO
       8 |     4.40 |     1,818 |    48,921 |       0.2750 |        0.6330 |      0.0102 |           0.0730 |       0.1680  <= SLO
      32 |     5.82 |     5,499 |    48,921 |       0.0909 |        0.2093 |      0.0102 |           0.0269 |       0.0620  <= SLO
      64 |     7.71 |     8,301 |    48,921 |       0.0602 |        0.1386 |      0.0102 |           0.0193 |       0.0444  <= SLO
     128 |    11.49 |    11,138 |    48,921 |       0.0449 |        0.1033 |      0.0102 |           0.0154 |       0.0355  <= SLO
     256 |    19.06 |    13,434 |    48,921 |       0.0372 |        0.0857 |      0.0102 |           0.0135 |       0.0311  <= SLO
     512 |    34.18 |    14,978 |    48,921 |       0.0334 |        0.0768 |      0.0102 |           0.0126 |       0.0289  <= SLO
    1024 | infeasible (KV)   (max_concurrency at ctx 4,608 = 1,019)
```

**S1 operating point: batch 256.** TPOT 19.06 ms, **13,434 output tok/s per GPU**, TTFT 88 ms
isolated. Batch 512 also satisfies TPOT ≤ 50 ms and is 11 % cheaper per output token, but its
worst-case admission burst is 43 s (§3.3) — it is not an interactive point in any useful sense.
Batch 256 is also the top of METHODOLOGY §4's standard grid, so it is the cross-document-comparable
choice.

| Metric at S1 (batch 256, 1 GPU) | low $1.80 | high $4.143 | res1y $1.30 |
|---|---:|---:|---:|
| $ / 1M **output** tokens | **$0.0372** | **$0.0857** | $0.0269 |
| $ / 1M **input** tokens | $0.0102 | $0.0235 | $0.0074 |
| **Blended** (75 % in, half of it cached at 10 % cost / 25 % out) | **$0.0135** | **$0.0311** | $0.0098 |

Blended formula, verbatim from METHODOLOGY §6:
`0.75 × (0.5 × in + 0.5 × in × 0.10) + 0.25 × out`. The 10 % cached-input factor is METHODOLOGY §6's
**self-serving** assumption, not a vendor ratio — and for this workload it is **generous**, since the
real prefix-cache hit rate is ~0 % (§2). At a true 0 % hit rate the blended figure rises to
**$0.0170 low / $0.0391 high**.

### 4.3 Cost at S4 max-throughput and at S2 / S3

```
S4 max-throughput, 4K in / 512 out, no TPOT SLO:
  batch   256: TPOT   19.06 ms |   13,434 out tok/s | $/1M out low $0.0372 high $0.0857 | blended low $0.0135 high $0.0311
  batch   512: TPOT   34.18 ms |   14,978 out tok/s | $/1M out low $0.0334 high $0.0768 | blended low $0.0126 high $0.0289
  batch 1,019: TPOT   64.14 ms |   15,886 out tok/s | $/1M out low $0.0315 high $0.0724 | blended low $0.0121 high $0.0278   <- KV ceiling

S2 32K in / 1K out:
  b=  8: TPOT    7.39 ms | out   1,082 tok/s | in 38,004 tok/s | $/1M out low $0.4622 | $/1M in low $0.0132 | blended low $0.1210
  b= 32: TPOT   17.80 ms | out   1,798 tok/s | in 38,004 tok/s | $/1M out low $0.2780 | $/1M in low $0.0132 | blended low $0.0749
  b= 64: TPOT   31.66 ms | out   2,021 tok/s | in 38,004 tok/s | $/1M out low $0.2474 | $/1M in low $0.0132 | blended low $0.0673
  b=128: TPOT   59.40 ms | out   2,155 tok/s | in 38,004 tok/s | $/1M out low $0.2320 | $/1M in low $0.0132 | blended low $0.0634
  b=256: infeasible (KV)  (max_conc 178)

S3 128K in / 2K out:
  b=  8: TPOT   17.58 ms | out     455 tok/s | in 21,530 tok/s | $/1M out low $1.0991 | $/1M in low $0.0232 | blended low $0.2843
  b= 32: TPOT   58.56 ms | out     546 tok/s | in 21,530 tok/s | $/1M out low $0.9149 | $/1M in low $0.0232 | blended low $0.2383
  b= 64 / 128 / 256: infeasible (KV)  (max_conc 46)
```

`est.` **S4 max-throughput point: batch 1,019 (the KV ceiling at 4,608 ctx), 15,886 out tok/s/GPU,
$0.0315–0.0724 per 1M output tokens.** S4 buys only **18 % more throughput than S1** for 3.4× the
TPOT — the Pareto curve is flat past batch 256 because the step is already KV-read dominated.

### 4.4 The cost model that actually matters: per video, per video-minute, per request

`.caption()` defaults to `max_new_tokens = 2048`; `.find()` caps at 64; the author's demo Space uses
768 ([architecture.md §6.5](architecture.md)). Sustained throughput per
[architecture.md §11.2](architecture.md#112-self-hosted-cost)'s method,
`sustained = 1/(1/prefill_bound + 1/decode_bound)`:

```
  capped operating point: 240 frames, 23,560 prefill tokens, 167.6 TFLOP total prefill
  prefill time = 916 ms  (ViT 340 + LLM GEMM 462 + LLM attn 114)

  b=128,  768 out tok: prefill-bound  3,931 v/h | decode-bound 14,106 v/h | sustained 3,074 v/h
  b=128, 2048 out tok: prefill-bound  3,931 v/h | decode-bound  5,290 v/h | sustained 2,255 v/h
  b= 64,  768 out tok: prefill-bound  3,931 v/h | decode-bound 12,913 v/h | sustained 3,014 v/h
  b=256,  768 out tok: prefill-bound  3,931 v/h | decode-bound 14,789 v/h | sustained 3,106 v/h
```

**Every row is prefill-bound**, by 3.6×. This reproduces
[architecture.md §11.2](architecture.md#112-self-hosted-cost)'s 3,183 videos/h for this card to
within 3 % (the small gap is this document's lower MFU_attn, §3.1).

| Planning point (b=128, 768 output tokens, **3,074 videos/h/GPU**) | low $1.80 | high $4.143 | res1y $1.30 |
|---|---:|---:|---:|
| $ / 1,000 two-minute videos | **$0.585** | **$1.348** | $0.423 |
| $ / video (request) | $0.000585 | $0.001348 | $0.000423 |
| $ / video-minute (2-min clip) | **$0.000293** | **$0.000674** | $0.000211 |

**$ per video-minute by duration** — the 240-frame cap makes long clips almost free per minute, and
that is a quality statement as much as a cost one:

```
     30 s:  60 frames,   12,407 videos/h -> low $0.000290/video-min   high $0.000668/video-min
     60 s: 120 frames,    6,319 videos/h -> low $0.000285/video-min   high $0.000656/video-min
    2 min: 240 frames,    3,073 videos/h -> low $0.000293/video-min   high $0.000674/video-min
   10 min: 240 frames,    3,073 videos/h -> low $0.000059/video-min   high $0.000135/video-min   (0.40 fps!)
   60 min: 240 frames,    3,073 videos/h -> low $0.000010/video-min   high $0.000022/video-min   (0.067 fps!)
```

`est.` Per-request token accounting, for anyone billing on tokens:
`frames = clamp(2 fps × duration, 4, 240)`; `98 LLM tokens/frame` (= 200,704 px ÷ 2,048);
`392 ViT patches/frame`; prompt = `frames × 98 + ~40` scaffold; output ≤ 2,048.

```
    duration | frames | eff fps | vid tokens | prefill tok | ViT TFLOP | LLM TFLOP |  TTFT s
         2 s |      4 |   2.000 |        392 |         432 |      1.09 |      1.63 |   0.014
        30 s |     60 |   2.000 |      5,880 |       5,920 |     16.33 |     23.14 |   0.208
        60 s |    120 |   2.000 |     11,760 |      11,800 |     32.66 |     47.83 |   0.430
       2 min |    240 |   2.000 |     23,520 |      23,560 |     65.33 |    102.31 |   0.916
      10 min |    240 |   0.400 |     23,520 |      23,560 |     65.33 |    102.31 |   0.916
      60 min |    240 |   0.067 |     23,520 |      23,560 |     65.33 |    102.31 |   0.916

  end-to-end one video at batch 1 (latency, not throughput):
      64 output tokens (.find):     0.916 s prefill + 0.271 s decode = 1.186 s  ( 3,034 videos/h/GPU)
     768 output tokens (Space):     0.916 s + 3.252 s                = 4.168 s  (   864 videos/h/GPU)
   2,048 output tokens (.caption):  0.916 s + 8.689 s                = 9.605 s  (   375 videos/h/GPU)
```

**`.multi_find` is billed differently and it is a trap.** It re-encodes the tail of the clip with
`ffmpeg -c:v libx264` (frame-accurate, not stream-copy) and re-runs a **full prefill per hit**, so an
`N`-event call costs `N × (ffmpeg re-encode + 0.916 s prefill + decode)` — N sequential requests plus
CPU video work the GPU roofline does not see
([architecture.md §1.4](architecture.md#14-what-the-model-actually-does)). On this card, where the
host is already the likely video-decode bottleneck, that CPU term is the one to measure first.

### 4.5 Effect of the two named levers

| Lever | Effect on this pair | $ effect |
|---|---|---|
| **Speculative decoding** | **Unavailable.** No MTP weights ship; no EAGLE draft head exists for Marlin ([architecture.md §7.1](architecture.md)). Even if one existed, §4.4 shows every operating point is **prefill-bound**, and speculation cannot touch prefill. The measured MTP decay on *this card* (1.90× at conc 1 → **1.17× at conc 64**, InferenceX, §3.5) says the gain would be ~15 % at the batch this fleet runs at. | **$0.00** today; ≤ 5 % on sustained videos/h even in the best case, because decode is only 22 % of the serial budget |
| **Prefix caching** | **~0 % hit rate** (§2). The cacheable scaffold is < 0.2 % of a request. `--mamba-cache-mode=align` is mandatory *and* experimental. The one real case — several questions about the *same* video — is what `align` exists for, and is **not** what `.multi_find` does. | Blended cost with the METHODOLOGY 10 %-cached assumption is **$0.0135**; at the honest 0 % it is **$0.0170**, i.e. the assumption flatters this pair by **26 %** |
| *(for contrast)* **FP8 KV cache** | the lever that *does* work: 1.83× decode at b=128, 1.88× concurrency (§3.4) | shifts decode-bound from 14,106 to ~25,800 v/h — but the fleet is prefill-bound, so **sustained videos/h moves only 3,074 → 3,359 (+9 %)** and $/1k videos falls $0.585 → $0.536 `est.` |

### 4.6 Comparison to the vendor API — there isn't one

[architecture.md §11.1](architecture.md#111-there-is-no-hosted-marlin-api) establishes this
definitively: HF Inference Providers returns **`"inferenceProviderMapping": {}`** for
`NemoStation/Marlin-2B`; the base Qwen3.5-2B has one text-only provider; the author runs a Gradio
demo and sells services by email. **There is no vendor $/1M-token anchor, so METHODOLOGY §6's API
sanity check cannot be performed and no break-even-vs-API utilisation can be computed.**
⚠️ TO BE VERIFIED whether Fireworks / Together / Replicate / Baseten / DeepInfra / SiliconFlow host
it. Three substitutes for the missing anchor:

1. **Break-even against owning the card.** [gpus/rtx6000-pro.md §8c](../../gpus/rtx6000-pro.md#8c-colo-amortised-gpu-hour-8-server-edition-node)
   puts colo-amortised cost at **$0.877/GPU-h at 100 % utilisation** ($1.096 at 80 %, $1.461 at 60 %).
   Against Nebius on-demand $1.80, **owning wins above ~49 % utilisation** `est.` — but it does
   **not** beat Hyperstack reserved $1.30 until ~68 %, and never beats Nebius preemptible $0.95.
   For a batch captioning fleet that can absorb preemption, **spot is the right answer** and the
   capex case is weak.
2. **Break-even against the other GPUs in the pinned set**, same method, `low` tier
   ([architecture.md §11.2](architecture.md#112-self-hosted-cost)):
   ```
     b200         $6.00/h  15,015 v/h -> $0.400/1k videos
     h100         $3.20/h   6,899 v/h -> $0.464/1k videos
     h200         $3.99/h   7,318 v/h -> $0.545/1k videos
     rtx6000-pro  $1.80/h   3,074 v/h -> $0.586/1k videos   <- this pair
     a100         $1.59/h   2,394 v/h -> $0.664/1k videos
   ```
   **The cheap card does not win on $/video.** It is 2.24× slower than an H100 for a 1.78× lower
   price. It wins only on $/1M *output* tokens at long generations, and on $/GB-hour
   ([cloud-pricing.md §10.1](../../cross-cutting/cloud-pricing.md#101-gb-hour-hbm-capacity):
   $0.0188/GB-h at `low`, the cheapest in the pinned set) — neither of which is this workload's
   binding cost.
3. **Where it *does* win: reserved.** At `res1y` $1.30 this pair lands at **$0.423/1k videos**,
   beating every on-demand row in the list above except a reserved H200. If the fleet is steady and
   committed, this is a genuinely competitive card for this model.

---

## 5. Scaling and deployment shape

### 5.1 Single node, and nothing else

**One card, one replica, N replicas behind a load balancer.** Weights 4.43 GB, one 2-minute video
0.287 GiB of KV + state, 251 concurrent videos per card. There is no sizing question here. The
deployment questions that remain are all about the *host*, not the GPU:

- **PD disaggregation: no.** [gpus/rtx6000-pro.md §7](../../gpus/rtx6000-pro.md#nvidia-dynamo) —
  Dynamo has no sm_120 statement either way, and its design assumes a fast KV fabric this card does
  not have. Splitting a 4.43 GB model across a prefill role and a decode role doubles the weight
  footprint to move ~288 MB of KV per request over PCIe Gen5, for a workload that is already
  prefill-bound. The right "disaggregation" for this pair is **EPD-shaped and CPU-side**: move video
  decode onto NVDEC and off the host CPU.
- **Wide-EP: not applicable.** Dense model, no experts.
- **Pipeline parallel: not applicable** at 1 GPU. (It is the correct fallback *if* a model forces
  multi-GPU on this card — TP2+PP2 measured 46–49 tok/s against TP4's 6–7 — but Marlin never forces
  it. [gpus/rtx6000-pro.md §4](../../gpus/rtx6000-pro.md#what-no-nvlink-actually-costs--measured))
- **Fix IOMMU/ACS in BIOS before benchmarking anything multi-card**, even replicas, or NCCL hangs
  on the first collective ([gpus/rtx6000-pro.md §11 #10](../../gpus/rtx6000-pro.md#11-known-gotchas)).
- **Buy Max-Q or cap to 450 W** unless the workload is prefill-heavy — decode is within 0.3 % between
  the 300 W and 600 W Workstation parts, and a published 4×600 W video run **failed a thermal health
  gate** ([gpus/rtx6000-pro.md §8d, §11 #13](../../gpus/rtx6000-pro.md#8d-power)). **This workload
  *is* prefill-heavy** (§4.4: prefill-bound by 3.6×), so the 600 W part is the right call here — one
  of the few cases in that document where it is.

### 5.2 Launch commands

**Path 1 — `transformers`, the only first-class path for this checkpoint.** Verbatim from
[`MODEL_CARD.md`](MODEL_CARD.md) via [architecture.md §8.6](architecture.md#86-launch-recipes):

```bash
pip install "transformers>=5.7.0" "torch>=2.11.0" torchcodec "qwen-vl-utils>=0.0.14" av pillow
```
```python
import torch
from transformers import AutoModelForCausalLM

marlin = AutoModelForCausalLM.from_pretrained(
    "NemoStation/Marlin-2B",
    trust_remote_code=True,
    dtype=torch.bfloat16,
    device_map={"": "cuda"},
)
marlin.compile()  # optional — wraps torch.compile, faster after first call

result = marlin.caption("video.mp4")                                 # max_new_tokens=2048
span   = marlin.find("video.mp4", event="a person enters the room")  # max_new_tokens=64
hits   = marlin.multi_find("video.mp4", event="a door opens", max_events=5)
```

The author's own reference deployment sets `attn_implementation="sdpa"` — **not** FlashAttention —
and exports the Path A preprocessing environment explicitly; on this card SDPA is a defensible
default given §2's FA2-class kernel story:

```python
os.environ.setdefault("FORCE_QWENVL_VIDEO_READER", "torchcodec")
os.environ.setdefault("VIDEO_MAX_PIXELS", "200704")
os.environ.setdefault("FPS", "2.0")
os.environ.setdefault("FPS_MAX_FRAMES", "240")
os.environ.setdefault("FPS_MIN_FRAMES", "4")
```

**Path 2 — vLLM with the architecture remap.** Quoted from
[architecture.md §8.1](architecture.md#81-what-vllm-compatible-concretely-means), with the sm_120
and video flags added from the sources named beside each:

```bash
vllm serve NemoStation/Marlin-2B --port 8000 --tensor-parallel-size 1 \
  --hf-overrides '{"architectures": ["Qwen3_5ForConditionalGeneration"]}' \
  --max-model-len 32768 --mamba-cache-mode=align \
  --media-io-kwargs '{"video": {"num_frames": -1}}'
```

`--hf-overrides` is documented vLLM API for exactly this remapping — the supported-models page gives
the same shape for other models
([docs.vllm.ai supported_models](https://docs.vllm.ai/en/latest/models/supported_models.html)) — and
`hf_overrides` is a first-class `ModelConfig` field. Flags to add **on this card specifically**:

| Flag | Why, and the source |
|---|---|
| `--attention-backend flashinfer` | SGLang's SM120 guidance, quoted verbatim in [inference-engines.md §3.8](../../cross-cutting/inference-engines.md#38-rtx-pro-6000-blackwell-server-edition-sm120-96-gb-gddr7-1597-gbs): *"SM120/SM121 (RTX PRO 6000 Blackwell, RTX 5090, DGX Spark): use `--attention-backend flashinfer`; `trtllm_mha` is SM100-only."* For vLLM the equivalent is to let it pick FA2/FlashInfer and **not** to force an FA4 path. |
| `--kv-cache-dtype fp8_e4m3` | 1.88× concurrency, 1.83× decode at video context (§3.4); it is the flag in SGLang's own RTX PRO 6000 cell for Qwen3.8-27B ([inference-engines.md §3.8](../../cross-cutting/inference-engines.md#38-rtx-pro-6000-blackwell-server-edition-sm120-96-gb-gddr7-1597-gbs)) |
| `--max-num-batched-tokens 2096` (or similar) | a real sm_120 Qwen3.5-hybrid run needed it to clear *"a Mamba cache alignment assertion error"* [src](https://huggingface.co/Qwen/Qwen3.5-35B-A3B-GPTQ-Int4/discussions/3) |
| `--max-cudagraph-capture-size <512` | the documented fix for `causal_conv1d_update` asserting when *"cuda graph capture size is larger than mamba cache size"* ([architecture.md §8.7](architecture.md#87-open-engine-issues)) |
| `--mm-encoder-tp-mode data`, `--mm-processor-cache-type shm` | vLLM Qwen3.5 recipe multimodal flags ([architecture.md §8.6](architecture.md#86-launch-recipes)) |
| `--mm-ipc-gpu-memory-gb <N>` + **CUDA MPS** + `"hw_decoders":2` | NVDEC video decode; *"CUDA MPS is essential for good performance with multi-process high concurrency work such as bulk VLM inference"* [src](https://vllm.ai/blog/2026-09-18-pynvvideocodec) |
| `--mem-fraction-static 0.85` / `--gpu-memory-utilization 0.90` | field reports on this card run 0.85–0.95; §1 plans at 0.90 |
| **CUDA ≥ 13** in the container | **mandatory** for FlashInfer GDN prefill on SM12x ([flash-attention.md §14.3](../../cross-cutting/flash-attention.md#143-hybrid-linear-attention--gated-deltanet-gdn)); also avoids the `glibc ≥ 2.41` + CUDA < 13.2 JIT failure ([gpus/rtx6000-pro.md §11 #14](../../gpus/rtx6000-pro.md#11-known-gotchas)). Official vLLM images default to **cu128** — ⚠️ whether a cu130 image ships is unverified. |

**What the remap costs you**, and must be re-implemented client-side: `.caption()` / `.find()` /
`.multi_find()`, the automatic `<think>`-prefix strip, the output parsers, and the Path A env-var
preprocessing defaults. **What you must re-supply:** **both** EOS ids `[248044, 248046]` (an engine
reading only `config.eos_token_id` misses one), the Path A frame/pixel budget, and the `<think>`
strip ([architecture.md §1.3, §8.1](architecture.md#13-tokenizer-vocab-chat-template)).

**Path 3 — llama.cpp**, verbatim from the GGUF repo (which has **265,785 downloads, 54× the original
repo**):

```bash
llama-mtmd-cli -m marlin-2b-text.gguf --mmproj marlin-2b.gguf --video input.mp4 \
  -p "Describe the scene and events."
```

**Do not use `--model-impl transformers` as the vLLM escape hatch.** It is disqualified three times
over by vLLM's own caveats: *"Vision-language models currently accept only image inputs"* (this is a
video model), no hybrid/linear-attention, no Mamba-style SSM
([architecture.md §8.2](architecture.md#82-the-vllm-transformers-fallback-does-not-rescue-this)).

### 5.3 Warm-up and loading

| Stage | Time | Basis |
|---|---:|---|
| Checkpoint read from NVMe, 5.4437 GB at 5 GB/s | **1.09 s** | `est.` — 5.4437 / 5 |
| … at 10 GB/s | **0.54 s** | `est.` |
| … at 20 GB/s | **0.27 s** | `est.` |
| First HF download (cold) | ~5.48 GB over the network + gate acceptance | [architecture.md §4](architecture.md#4-weight-memory-by-dtype) `usedStorage` |
| CUDA context + engine init + CUDA-graph capture | **⚠️ TO BE VERIFIED** — dominates the above; not measured for any model on this card | — |
| `marlin.compile()` first call | **⚠️ TO BE VERIFIED** — torch.compile warm-up, one-off | [`MODEL_CARD.md`](MODEL_CARD.md) |
| FlashInfer GDN JIT warm-up | **⚠️ TO BE VERIFIED** — vLLM warns about it on the Hopper path; unmeasured on SM12x | [flash-attention.md §14.3](../../cross-cutting/flash-attention.md#143-hybrid-linear-attention--gated-deltanet-gdn) |

**The weight load is a rounding error.** Sub-second from NVMe. Anything you measure above ~30 s to a
serving replica is engine init, JIT and graph capture, not I/O — and on this card the JIT path is the
one with the documented `glibc`/`ptxas`/`nvcc` landmines
([gpus/rtx6000-pro.md §11 #14–#16](../../gpus/rtx6000-pro.md#11-known-gotchas)). Budget container
image pull, not checkpoint download, as the cold-start cost.

---

## 6. Risks and open questions

### 6.1 Blocking — resolve before committing

1. ⚠️ **Does the checkpoint load under vLLM's `Qwen3_5ForConditionalGeneration` after
   `--hf-overrides`?** Strong evidence (pure subclass with an unmodified forward, confirmed in the
   gated `modeling_marlin.py`; a third party already ships the remap; documented API), but **not
   verified end to end on any GPU**, let alone sm_120. Sub-question: how vLLM handles the
   materialised `lm_head.weight` against `tie_word_embeddings: true` — which is also the 1.02 GB
   fork in §1.1. [architecture.md §8.1](architecture.md#81-what-vllm-compatible-concretely-means)
2. ⚠️ **Disagreement across this repo about whether *any* engine supports this model.**
   [inference-engines.md §6.5 / §8](../../cross-cutting/inference-engines.md#65-nemostationmarlin-2b--no-engine-supports-it)
   states flatly *"Engine support: none, on any engine … `transformers` + `trust_remote_code=True`
   only"*, and its RTX PRO 6000 row reads "No engine at all". That document was written **without
   gated access**; [architecture.md §8.1](architecture.md#81-what-vllm-compatible-concretely-means),
   written **with** it, sharpens the finding to *"true of the weights and false of the checkpoint as
   shipped … It costs one flag, not a port."* Both are cited here; the practical reading is that
   `transformers` is the only **validated** runtime today and vLLM is a one-flag, one-afternoon
   validation away.
3. ⚠️ **Video token budget: Path A (23,520 tokens) vs Path B (12,288).** `qwen-vl-utils` bypasses the
   HF processor's `size`, and the two paths differ by **1.914×**. An engine ingesting video through
   the HF video processor — i.e. vLLM/SGLang — gives the model **half the frames or half the
   resolution** the training path did. Every number in §3 and §4 assumes **Path A**; a vLLM
   deployment that silently lands on Path B halves its prefill cost *and* its quality, with no error
   raised. [architecture.md §6.3](architecture.md#63-video-token-budget-per-request)
4. ⚠️ **Does FlashInfer GDN prefill actually engage on SM12x for this head config, and does anything
   dispatch to FlashQLA?** 18 of 24 layers ride on this. The gate is `family 120` +
   `head_k_dim == 128` + **`cuda_runtime >= 13`**; a cu128 container silently falls back to Triton.
   [flash-attention.md §14.3](../../cross-cutting/flash-attention.md#143-hybrid-linear-attention--gated-deltanet-gdn)
   · [architecture.md §8.3](architecture.md#83-attention-kernels-per-gpu--the-head_dim-256-story)

### 6.2 Performance unknowns specific to this pair

5. ⚠️ **No measurement of Marlin-2B exists on any hardware**, and none of any 2B hybrid-GDN video
   model on sm_120. Everything in §3 is roofline. The closest measured proxy is a Qwen3.5 hybrid VLM
   on an **RTX 5090** (same SM, different card) at ~194–197 tok/s single-stream.
6. ⚠️ **MBU on sm_120 for this shape is unmeasured.** The 0.60 planning value sits mid-band;
   [gpus/rtx6000-pro.md §9b](../../gpus/rtx6000-pro.md#9b-decode-upper-bound--bandwidth--bytes-moved)
   would prefer 0.50–0.65 and the one small-dense datapoint implies ~0.535 with an unstated
   precision. Every §3/§4 number scales inversely; at MBU 0.50 the video-context b=128 aggregate
   falls **17 %** to 2,508 tok/s.
7. ⚠️ **No attention-only TFLOPS measurement exists on sm_120 at all**
   ([gpus/rtx6000-pro.md §5e](../../gpus/rtx6000-pro.md#5e-measured-attention-throughput)). The
   MFU_attn 0.25 used here is the midpoint of an estimated band, and it is the largest single source
   of error in the 128 K TTFT row (57.8 % of that prefill is attention).
8. ⚠️ **torchcodec / NVDEC decode throughput for 240 frames at 448×448 is unmeasured everywhere**,
   and vLLM's own blog says CPU decode saturates before 4 GPUs. **This is the most likely reason a
   real deployment misses the §4 numbers**, and it is a host-side problem the GPU roofline cannot
   see.
9. ⚠️ **Whether FP8 KV runs the attention math in FP8 or dequantises on read, on the SM12x GQA
   path.** Sources conflict: FlashInfer PR #2559 passes FP8 e4m3 on SM121a for the DeepSeek
   geometry; SGLang #39807 says *"FMHAv2 has no FP8 (e4m3) prefill kernel on SM120/SM121"*; the
   TRT-LLM `strings` dump shows only `…bf16…sm120…` FMHA symbols. Plan the **capacity** win as real
   (1.88×) and the **speed** win (1.83×) as ⚠️.
10. ⚠️ **B12X attention backend on a hybrid-GDN VLM.** It is the one SM12x-native attention backend
    and it covers `head_size 256`, but KV dtype `auto` **only** — so it and `--kv-cache-dtype fp8`
    may be mutually exclusive. Untested for this model class.
    [flash-attention.md §9.5](../../cross-cutting/flash-attention.md#95-rtx-pro-6000-blackwell-sm_120)

### 6.3 Accuracy caveats of the executed precision

11. **BF16 is the checkpoint's native precision, so there is no precision risk from the executed
    format** — this is the one thing this pair gets for free, and it is why §2's long list of sm_120
    FP4 pathologies is irrelevant here.
12. ⚠️ **If you quantise, you are the first person to evaluate it.** *"Evaluations of the quantised
    variants: none published … Not one repo reports a CaReBench / DREAM-1K / TimeLens score, a
    perplexity delta or any accuracy measurement"*
    ([architecture.md §9](architecture.md#9-available-quantised-variants)). For a model whose entire
    value is **second-precise timestamps**, INT4 numeric error landing on the timestamp digits is a
    specific, plausible and entirely unmeasured failure mode. §3.4 shows the payoff at fleet batch is
    **5 %**. Do not take that trade blind.
13. ⚠️ **FP8 KV accuracy on this geometry is unmeasured.** vLLM's general guidance is *"at most 1–2
    points of accuracy degradation"* with calibrated scales, and the default (all scales 1.0) is
    **not** the recommended tier
    ([quantization-formats.md §8.2, §8.3](../../cross-cutting/quantization-formats.md#82-fp8-kv-is-not-just-storage)).
    A calibration run with llm-compressor is cheap; timestamp regression testing is the part nobody
    has a harness for.
14. **Quality cliff, not a precision one: the 240-frame cap.** A 10-minute clip is sampled at
    **0.40 fps** and an hour at **0.067 fps** for exactly the same cost. The §4.4 "$0.00001 per
    video-minute at 60 minutes" row is arithmetically true and operationally a warning: you are
    paying almost nothing because the model is seeing almost nothing. Chunk long videos client-side.
    [architecture.md §6.3](architecture.md#63-video-token-budget-per-request)
15. ⚠️ **Serving through vLLM/SGLang may not preserve caption and grounding quality** even when it
    runs. The model was trained with a fixed prompt, a `<think>` prefix artefact, a two-element EOS
    list and a specific preprocessing path; each is a place an engine diverges. No
    engine-vs-transformers quality comparison exists.

### 6.4 Known engine and platform issues that will bite on this card

16. ⚠️ **Official vLLM images default to cu128**; CUDA ≥ 13 is required for FlashInfer GDN prefill on
    SM12x, and CUDA < 13.2 with glibc ≥ 2.41 **cannot JIT-compile kernels at all** (C23 `rsqrt`
    `noexcept` mismatch). Whether a cu130 official image ships as of 2026-09-19 is unverified.
    [gpus/rtx6000-pro.md §7, §11 #14](../../gpus/rtx6000-pro.md#11-known-gotchas)
17. **`--mamba-cache-mode=all` is a hard error** for Qwen3.5 in vLLM; `align` is mandatory and
    *"currently experimental"*. [architecture.md §8.7](architecture.md#87-open-engine-issues)
18. **`causal_conv1d_update` assertion** when the CUDA-graph capture size exceeds the mamba cache
    size — lower `--max-cudagraph-capture-size` from its 512 default.
19. **`use_cache: false` in `config.json`** at both levels (the base config has `true`); a direct
    `forward()` caller who does not pass `use_cache=True` **silently gets no KV cache** and every
    number in §3 becomes meaningless. `generation_config.json` overrides it back for `generate()`.
    [architecture.md §2.1](architecture.md#21-top-level)
20. **Split EOS ids.** `config.eos_token_id = 248046`, `config.text_config.eos_token_id = 248044`,
    `generation_config` lists both. An engine reading only the top-level id misses a stop token.
21. ⚠️ **`TRITON_PTXAS_PATH` and stale `nvcc`.** Triton 3.5.1 bundles a CUDA 12.8 `ptxas` that does
    not know `sm_121a`; an unset `CUDA_HOME` silently picks up a stale system `nvcc`. Both are
    documented silent-downgrade traps on this card.
    [gpus/rtx6000-pro.md §5f, §6a](../../gpus/rtx6000-pro.md#5f-triton-and-sageattention)
22. ⚠️ **LMCache PyPI wheels ship no sm_120 cubins and no PTX** → `cudaErrorNoKernelImageForDevice`;
    must be built from source. Only matters if you reach for KV offload — which §2 says you should
    not, since the hit rate is ~0 %.
    [gpus/rtx6000-pro.md §7](../../gpus/rtx6000-pro.md#supporting-libraries)
23. **Name collision when searching issue trackers: "Marlin" is also vLLM's INT4 W4A16 GEMM kernel**,
    and on *this* card it is the recommended MoE backend for unrelated models. `--moe-backend marlin`
    has nothing to do with this model. [architecture.md §8.7](architecture.md#87-open-engine-issues)
24. ⚠️ **Which edition you are actually renting.** Only Hyperstack's rate card says "SE". RunPod's
    spec sheet quotes 1,792 GB/s, which is Workstation silicon. That is an 11 % swing in every
    number in §3. **Confirm before benchmarking.**
    [gpus/rtx6000-pro.md §2, §8a](../../gpus/rtx6000-pro.md#2-memory-and-bandwidth)
25. ⚠️ **No hosted API and no Gemini video price retrieved**, so §4.6's break-even is against
    hardware, not against a competitor's list price.
26. ⚠️ **The 5,920-token concurrency cell in
    [architecture.md §8.5](architecture.md#85-sizing-parallelism-and-per-gpu-caveats) (878) does not
    reproduce from its own stated budget** (841). Recorded in §1.3; the other four cells reproduce.

**⚠️ count: 26 items above carry a ⚠️ or an explicit disagreement marker.**

---

## Sources

**This repo (numbers taken as inputs, per the brief)**
- [`research/METHODOLOGY.md`](../../METHODOLOGY.md) — §1 bytes/param, §2 KV and fixed state, §3 fit
  and the consistency rule, §4 roofline and MBU/MFU bands, §6 scenarios S1–S4 and the blended
  definition, §8 pinned inputs
- [`research/models/marlin2b/architecture.md`](architecture.md) — §1.2–1.4 files and modes, §2
  config, §3 parameters, §4 weight memory, §5 KV and GDN state, §6 compute and video token budget,
  §7 MTP, §8 engines and kernels, §9 quantised variants, §10 benchmarks, §11 cost, §12 open questions
- [`research/gpus/rtx6000-pro.md`](../../gpus/rtx6000-pro.md) — §1 identity and device limits, §2
  memory, §3c dense reconciliation, §3d measured GEMM, §4 interconnect, §5 attention kernels, §6
  formats, §7 engines, §8 pricing and colo, §9 roofline, §10c community measurements, §11 gotchas,
  §12 comparison hooks
- [`research/cross-cutting/flash-attention.md`](../../cross-cutting/flash-attention.md) — §9.5 sm_120
  matrix, §9.7 consolidated grid, §14.3 GDN backends, §16.4 Marlin-2B row, §17 cheat-sheet
- [`research/cross-cutting/quantization-formats.md`](../../cross-cutting/quantization-formats.md) —
  §8.1–8.3 KV quantisation, §9.5 cross-model summary, §9.6 checkpoint × GPU matrix (note ᵒ)
- [`research/cross-cutting/inference-engines.md`](../../cross-cutting/inference-engines.md) — §3.8
  RTX PRO 6000, §6.5 Marlin-2B, §7.1–7.3 multimodal serving, §8 master matrix
- [`research/cross-cutting/serving-optimizations.md`](../../cross-cutting/serving-optimizations.md) —
  §1.5 cache economics, §6.1–6.3 multimodal and video token budgets, §7.4 Marlin-2B worked example
- [`research/cross-cutting/cloud-pricing.md`](../../cross-cutting/cloud-pricing.md) — §5.10 RTX PRO
  6000 provider rows, §5.14 planning prices, §10.1 $/GB-hour
- [`research/cross-cutting/inferencex-api.md`](../../cross-cutting/inferencex-api.md) — §3 slug map
  and the `rtx6000pro` hardware filter recipe

**Fetched live for this document (2026-09-19)**
- https://inferencex.semianalysis.com/api/v1/benchmarks?model=Qwen-3.5-397B-A17B — the only
  InferenceX rows on `rtx6000pro`; 16 rows, SGLang fp4 TP4, 8K/1K, conc 1/4/16/64, MTP on and off
- https://recipes.vllm.ai/Qwen/Qwen3.5-2B.json — vLLM recipe for Marlin's **base** model
  (`min_vllm_version 0.17.0`, TP1, 262 K ctx, `spec_decoding` and `text_only` opt-ins, verified
  hardware `arc_pro_b60` / `arc_pro_b70` only)
- https://recipes.vllm.ai/Qwen/Qwen3.5-2B/hw/rtx_pro_6000.json — **HTTP 404**: no vendor-verified
  RTX PRO 6000 profile for the base model
- https://recipes.vllm.ai/models.json — recipe catalogue; **zero** `Marlin` entries
- https://vllm.ai/blog/2026-09-18-pynvvideocodec — NVDEC video decode in vLLM; Qwen3-VL-8B on
  8×H100; *"more than double the throughput"*; `--mm-ipc-gpu-memory-gb`, CUDA MPS, `"hw_decoders":2`
- https://huggingface.co/Qwen/Qwen3.5-35B-A3B-GPTQ-Int4/discussions/3 — a Qwen3.5 hybrid-GDN VLM with
  video on **RTX 5090 (sm_120)**: vLLM cu130-nightly, FLASHINFER backend, `--kv-cache-dtype fp8`,
  **`--max-num-batched-tokens 2096`** for the Mamba cache alignment assertion, ~194–197 tok/s
- https://www.vaditaslim.com/blog/ai/local-llm-benchmarks-rtx-pro-6000 — RTX PRO 6000 Max-Q llama.cpp
  sweep; **no models under 8 B**, **no VLM completed** (`qwen3-vl:32b` failed to load)
- https://github.com/voipmonitor/rtx6kpro (benchmarks/results.md) — RTX PRO 6000 sweep; no models
  under 4 B, no vision or video models
- https://github.com/Cobdog/llama-video — llama.cpp temporal video captioning for Qwen3.5 GGUF;
  defaults 2.0 fps, **64-frame cap** (not 240); **no Marlin/NemoStation mention, no benchmarks**
- https://www.databasemart.com/blog/vllm-gpu-benchmark-pro6000 — **HTTP 403**, not retrievable

**Searched and found nothing** (recorded so the next pass does not repeat it): Marlin-2B throughput
on any GPU; Marlin-2B GGUF tok/s; any 2B hybrid-GDN video model on sm_120; MLPerf or InferenceMAX
rows for RTX PRO 6000 (⚠️ per [gpus/rtx6000-pro.md §10a](../../gpus/rtx6000-pro.md#10a-mlperf-inference));
a hosted Marlin-2B API at any price.

---

## Appendix — the arithmetic

Every table in §1, §3 and §4 is the verbatim stdout of one script, run with `python3`:

`/tmp/claude-1000/-home-rey-workspace-rey-code-model-inference/66272832-fdba-4ff7-91ef-967c9a758714/scratchpad/marlin_rtx.py`

Its pinned inputs, in one block, so the derivations can be re-run against changed assumptions:

```python
HBM = 96e9; BW = 1.597e12; BF16 = 480e12          # gpus/rtx6000-pro.md §2, §3c
MBU = 0.60; MFU_GEMM = 0.40; MFU_ATTN = 0.25      # §3.1 of this document
P_UNIQUE = 2_213_241_664; P_DISK = 2_721_801_024  # architecture.md §3.2
P_LANG   = 1_881_825_088                          # decode read set (ViT idle) — §3.3/§6.2
KV_BF16  = 12_288; KV_FP8 = 6_144                 # architecture.md §5.1
GDN      = 19_537_920                             # 18.63 MiB/seq, S = 1 — §5.2
ACT_WS   = 4 * 2**30                              # METHODOLOGY §3 band 2–6 GB
ATTN_C   = 24_576                                 # attention_flops(T) = 24576·T²  — §6.1
VIT_PER_FRAME = 0.2722e12                         # 272.2 GFLOP/frame — §6.4
P_LOW, P_HIGH, P_RES = 1.80, 4.143, 1.30          # cloud-pricing.md §5.14, rtx6000-pro row
```

---

## Audit log (2026-09-19)

Independent numerical audit against [`METHODOLOGY.md`](../../METHODOLOGY.md) (formulas), model
inputs from [`architecture.md`](architecture.md) §3–6, and GPU inputs from
[`gpus/rtx6000-pro.md`](../../gpus/rtx6000-pro.md) §2, §3, §8, §9. Every derived table in this
document was recomputed from scratch with `python3` (independent of the appendix script), using
the pinned inputs above:

- **§1.1–1.3 Fit** — `usable_hbm`, `kv_budget` (tie-honoured and tie-ignored), the pooled
  `max_concurrency(ctx)` table across {1,2,4,8,16} GPUs × {8K,32K,128K,1M} × {BF16,FP8} KV, and the
  three video-native-context concurrency figures (841/1,389, 472/843, 251/473). All reproduced
  exactly (the 1M-token column uses `n × floor(single-GPU value)` rather than the pooled formula,
  a documented and immaterial choice — max Δ 15 sequences at n=16, <1%).
- **§3.2–3.4 Decode roofline, TTFT, sensitivity** — `decode_step_time`, `tokens/s`, TPOT for S1/S2/S3
  at every batch row; prefill GEMM/attention/total time and throughput at T = 4,096 / 32,768 /
  131,072; the admission-burst queueing table; the MBU 0.50–0.70 sweep; the BF16/FP8-linears/INT4
  weight-format sweep; the FP8-KV 1.83×/1.88× levers; the KV-overtakes-weights crossover (batch
  13.02). All reproduced exactly.
- **§4.2–4.4 Cost** — `cost_per_1M = n_gpus × price / (tokens/s × 3600) × 1e6` and the blended
  formula (`0.75×(0.5×in + 0.5×in×0.10) + 0.25×out`) at every batch row of S1/S2/S3/S4, at all three
  price tiers ($1.80 / $4.143 / $1.30, cross-checked against `cloud-pricing.md` via
  `gpus/rtx6000-pro.md` §8a — correct rows, not a neighbouring GPU's); the video economics
  (prefill-bound / decode-bound / sustained videos/h, $/1,000 videos, $/video-minute by duration,
  the per-request token/TFLOP/TTFT table, and the FP8-KV-lever recompute). All reproduced exactly
  or within ≤2% (e.g. §4.5's FP8-KV sustained-videos/h figure, already marked `est.`), well inside
  the 5% correction threshold.
- **Unit and consistency checks performed**: GB (10⁹) vs GiB (2³⁰) used correctly and distinguished
  throughout; HBM bandwidth is the Server Edition 1,597 GB/s (not the Workstation 1,792 GB/s)
  everywhere it appears; TFLOPS are dense (480/960/1,920 BF16/FP8/FP4), never the sparse
  1/2/4 PFLOPS NVIDIA prints; all throughput/cost tables are per-GPU (`n_gpus = 1`), consistent with
  their "1 GPU" labelling; KV bytes/token (12,288 BF16 / 6,144 FP8) and the 19,537,920 B GDN fixed
  state match `architecture.md` §5.1–5.2 exactly; §0's verdict figures ($0.037–0.086, blended
  $0.0135–0.0311, $0.032–0.072, $0.59–1.35 / $0.00029–0.00067 per video-minute) all match their
  source tables in §4.

**Result: no arithmetic errors ≥5% found. No edits were made to any number in this document.**

**Contradiction with a foundation doc, already flagged in-document (not newly found, verified
correct handling):** [architecture.md §8.5](architecture.md#85-sizing-parallelism-and-per-gpu-caveats)
prints **878** concurrent 30-second-video requests for `rtx6000-pro`; recomputing from this
document's own §1.1 KV budget (77.679 GB) and architecture.md's own KV-cache formula gives **841**
— confirmed again in this audit. §1.3 and §6.4 item 26 of this document already record the
disagreement and use 841 rather than silently reconciling it; this audit found no reason to change
that call.
