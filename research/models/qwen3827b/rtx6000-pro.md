# Qwen/Qwen3.8-27B on NVIDIA RTX PRO 6000 Blackwell Server Edition 96GB GDDR7 (PCIe only, no NVLink; sm_120)

**Research date: 2026-09-19.** Follows [`research/METHODOLOGY.md`](../../METHODOLOGY.md) — §1 weight
memory, §2 KV/state and the `S` slot multiplier, §3 fit and the consistency rule, §4 roofline,
§6 cost and the standard scenarios S1–S4, §8 pinned inputs. Legend: `[src]` + URL = primary
source, `meas.` = published measurement, `est.` = derived here with the formula shown,
**⚠️ TO BE VERIFIED** = no primary source, method stated inline. All dense TFLOPS. All memory
arithmetic in bytes, reported as GB (10⁹) unless a column says GiB. Every table below was
produced by `python3`; the script's output is pasted verbatim.

Inputs come from the fact-checked documents and are cited by path + section rather than
re-derived: [`models/qwen3827b/architecture.md`](./architecture.md),
[`gpus/rtx6000-pro.md`](../../gpus/rtx6000-pro.md),
[`cross-cutting/flash-attention.md`](../../cross-cutting/flash-attention.md),
[`cross-cutting/quantization-formats.md`](../../cross-cutting/quantization-formats.md),
[`cross-cutting/inference-engines.md`](../../cross-cutting/inference-engines.md),
[`cross-cutting/serving-optimizations.md`](../../cross-cutting/serving-optimizations.md),
[`cross-cutting/cloud-pricing.md`](../../cross-cutting/cloud-pricing.md).

---

## 0. Verdict

1. **Runnable today, verified by both engines.** vLLM ≥ 0.17.0 (`"rtx_pro_6000": "verified"`,
   `nvfp4_nvidia` variant) and SGLang ≥ 0.5.9 (`rtx6000` in `supportedHardware`, all five
   checkpoints, GSM8K 94.01–95.00 % across 16 overlay combinations on v0.5.19) —
   [inference-engines.md §3.8](../../cross-cutting/inference-engines.md), current releases vLLM 0.29.0 / SGLang 0.5.20.
2. **Min 1 GPU, recommended 1 GPU, TP1, scale by replication.** The model fits at every
   precision; TP over PCIe Gen5 costs ~85 % overhead on this card
   ([gpus/rtx6000-pro.md §4](../../gpus/rtx6000-pro.md)) and `num_key_value_heads = 4` caps clean KV sharding at TP4 anyway.
3. **Weight format actually executed: NVFP4 W4A4, natively.** `nvidia/Qwen3.8-27B-NVFP4`
   (21.92 GB) is *dense*-only quantisation, so it misses sm_120's broken MoE grouped-GEMM path
   entirely — [quantization-formats.md §9.6 note (l)](../../cross-cutting/quantization-formats.md). FP8 KV, bf16 GDN state.
4. **Interactive (S1, TPOT ≤ 50 ms): $0.24–0.56 per 1M output tokens** at bs=64, TPOT 31 ms,
   2,052 tok/s/GPU; **max throughput (S4): $0.19–0.44** at bs=111, TPOT 43 ms, 2,610 tok/s/GPU.
   Blended (75 % in / 50 % of it cached / 25 % out) **$0.087–0.199 per 1M**.
5. **Confidence: `estimate`.** Decode and prefill are calibrated against five published bs=1
   measurements on this card and land within 3 % of two of them, but **no multi-concurrency
   benchmark exists for this pair anywhere**, and every published measurement was taken on
   1,792 GB/s (Workstation) silicon, not the Server Edition's 1,597 GB/s.

---

## 1. Fit

### 1.1 Inputs, and where the documents disagree

| Input | Value used | Source / disagreement |
|---|---|---|
| Capacity as deployed | **96 GB = 96e9 B = 89.41 GiB**; usable `× 0.90` = **86.40e9 B (80.47 GiB)** | METHODOLOGY §3; [gpus/rtx6000-pro.md §2](../../gpus/rtx6000-pro.md) |
| HBM bandwidth | **1,597 GB/s** (Server Edition) | METHODOLOGY §8; NVIDIA SE page verbatim "Memory Bandwidth: 1597 GB/s". The Workstation Edition's 1,792 GB/s is a different SKU — **11 % of TPOT**, and no marketplace listing distinguishes them ([gpus/rtx6000-pro.md §2, §11 gotcha 20](../../gpus/rtx6000-pro.md)) |
| Dense BF16 / FP8 / FP4 TFLOPS | **480 / 960 / 1,920** | **Disagreement, stated per METHODOLOGY §8.** [gpus/rtx6000-pro.md §3c](../../gpus/rtx6000-pro.md) derives 480/960/1,920 from the implied 2,494 MHz boost; [flash-attention.md §2](../../cross-cutting/flash-attention.md) prints **500 / 1,000 / 2,000** by halving NVIDIA's sparse "1/2/4 PFLOPS"; METHODOLOGY §8 pins "≈ 2,000 dense (4,000 sparse)". The spread is ≤ 4.2 % and moves no conclusion. This document uses the GPU doc's 480/960/1,920 because it is the internally consistent per-SM derivation, and notes that using 500/1,000/2,000 makes every prefill number 4 % faster. |
| Activation workspace | **4.00e9 B** per GPU | [gpus/rtx6000-pro.md §9d](../../gpus/rtx6000-pro.md). Defensible here because both RTX PRO 6000 recipes pin `--chunked-prefill-size 2048`, which is the smallest prefill chunk in the repo. |
| Checkpoint bytes | BF16 **55.563** / FP8 **30.870** / NVFP4 **21.923** / INT4 **19.453** GB | METHODOLOGY §8; reconciled to the HF file tree in [architecture.md §4](./architecture.md) |
| KV / token | **65,536 B BF16, 32,768 B FP8** (16 full-attention layers only) | METHODOLOGY §8; [architecture.md §5.1](./architecture.md), independently published by SGLang |
| GDN state / slot | **153,944,064 B fp32** (config default) / **78,446,592 B bf16** | METHODOLOGY §8; [architecture.md §5.2](./architecture.md) |
| `S` state slots per request | **5** (SGLang `extra_buffer` default), **1** floor with `--disable-radix-cache` | METHODOLOGY §2; [architecture.md §5.4](./architecture.md). vLLM's `S` is **⚠️ TO BE VERIFIED** — unpublished |

### 1.2 Tensors that cannot be sharded — read this before any TP plan

Under tensor parallelism on this model, four groups do **not** shrink with `n`:

1. **All normalisation tensors** — `input_layernorm`, `post_attention_layernorm`,
   `self_attn.q_norm`/`k_norm` (`[256]` each), the GDN `[128]` norm, `A_log`, `dt_bias`, and the
   final norm. 660,480 params = **1.3 MB BF16**, replicated on every rank. Negligible in bytes,
   but it is why "weights ÷ n" is never exactly right.
2. **The vision tower (460.73 M params, 0.921 GB BF16, un-quantised in *every* published build
   — [architecture.md §3.2](./architecture.md)).** The vLLM RTX PRO 6000 recipe pins
   `--mm-encoder-tp-mode data`, i.e. the encoder is **data-parallel = fully replicated per rank**
   [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B/hw/rtx_pro_6000.json). At TP8 that is 7.4 GB of
   duplicated weights across the node. `--language-model-only` deletes it entirely for text-only
   traffic and is measured to buy **+49 % KV pool** at 32 K on a 5090
   ([serving-optimizations.md §7.3](../../cross-cutting/serving-optimizations.md)).
3. **The KV cache beyond TP=4.** `num_key_value_heads = 4`. At TP ≤ 4 each rank owns one KV head
   group and KV memory divides cleanly. At TP=8 the 4 KV heads must be **replicated across pairs
   of ranks**, so aggregate KV storage stops falling and the node holds 2× the bytes for the same
   pool. This is also the DCP ceiling: `TP // n_kv_heads = TP // 4`
   ([serving-optimizations.md §7.3](../../cross-cutting/serving-optimizations.md)).
4. **The GDN recurrent state shards only to TP=16** (`linear_num_key_heads = 16`); below that it
   divides by `n`. It cannot be split along a position axis at all — there is none — so
   context-parallel schemes do not apply to the 48 linear layers
   ([serving-optimizations.md §5.3](../../cross-cutting/serving-optimizations.md)).

### 1.3 Fit table over the GPU counts this topology allows: {1, 2, 4, 8}

PCIe Gen5 ×16 only, no NVLink, no NVSwitch. Lenovo qualifies **2 cards at 600 W or 4 at the
450 W cap** in the SR675 V3; "8× in a server" is a general-market configuration, not a
universally qualified one ([gpus/rtx6000-pro.md §4, §11 gotcha 12](../../gpus/rtx6000-pro.md)).
**No multi-node row exists: "Multi-node LLM serving is not a supported design point for this
card"** ([gpus/rtx6000-pro.md §4](../../gpus/rtx6000-pro.md)) — and it is never needed, because
one card holds the model at every precision.

```
TABLE 1 - weights per GPU, KV budget, topology  (usable 86.40e9 B, workspace 4.00e9 B)
ViT replicated on every rank (vLLM RTX PRO 6000 recipe pins --mm-encoder-tp-mode data);
norms/q_norm/k_norm/A_log/dt_bias replicated = 0.0013 GB, folded in.

fmt     n parallelism   W/GPU GB  act GB  KVbudget/GPU GB  agg KV GB
BF16    1 TP1 (replica)     55.56    4.00            26.84      26.84
BF16    2 TP2 over PCIe     28.24    4.00            54.16     108.31
BF16    4 TP4 over PCIe     14.58    4.00            67.82     271.27
BF16    8 TP8 over PCIe      7.75    4.00            74.65     597.18
FP8     1 TP1 (replica)     30.87    4.00            51.53      51.53
FP8     2 TP2 over PCIe     15.90    4.00            66.50     133.01
FP8     4 TP4 over PCIe      8.41    4.00            73.99     295.96
FP8     8 TP8 over PCIe      4.67    4.00            77.73     621.87
NVFP4   1 TP1 (replica)     21.92    4.00            60.48      60.48
NVFP4   2 TP2 over PCIe     11.42    4.00            70.98     141.95
NVFP4   4 TP4 over PCIe      6.17    4.00            76.23     304.91
NVFP4   8 TP8 over PCIe      3.55    4.00            78.85     630.82
INT4    1 TP1 (replica)     19.45    4.00            62.95      62.95
INT4    2 TP2 over PCIe     10.19    4.00            72.21     144.42
INT4    4 TP4 over PCIe      5.56    4.00            76.84     307.38
INT4    8 TP8 over PCIe      3.24    4.00            79.16     633.29
```

Max concurrency per METHODOLOGY §3,
`max_concurrency(ctx) = floor( kv_budget_per_gpu / (ctx × kv_B / min(n,4) + S × state_B / n) )`.
Cells that cannot hold one sequence print `infeas`, never a fraction.

```
TABLE 2 - max concurrency per GPU count.  S = SGLang state slots per request.

--- BF16 ---
 n  S state     BF16KV 8K   BF16KV 32K  BF16KV 128K BF16KV 1024K     FP8KV 8K    FP8KV 32K   FP8KV 128K  FP8KV 1024K
 1  5  fp32            20            9            2       infeas           25           14            5       infeas
 1  5  bf16            28           10            2       infeas           40           18            5       infeas
 1  1  bf16            43           12            3       infeas           77           23            6       infeas
 2  5  fp32            82           37           11            1          104           58           21            3
 2  5  bf16           116           42           12            1          163           73           23            3
 2  1  bf16           176           48           12            1          312           94           24            3
 4  5  fp32           207           92           28            3          261          147           53            7
 4  5  bf16           291          106           30            3          410          185           57            7
 4  1  bf16           440          121           31            3          782          235           62            7
 8  5  fp32           323          117           33            4          457          204           63            8
 8  5  bf16           407          127           33            4          642          235           66            8
 8  1  bf16           518          136           34            4          970          268           68            8

--- FP8 ---
 n  S state     BF16KV 8K   BF16KV 32K  BF16KV 128K BF16KV 1024K     FP8KV 8K    FP8KV 32K   FP8KV 128K  FP8KV 1024K
 1  5  fp32            39           17            5       infeas           49           27           10            1
 1  5  bf16            55           20            5       infeas           77           35           10            1
 1  1  bf16            83           23            5       infeas          148           44           11            1
 2  5  fp32           101           45           14            1          128           72           26            3
 2  5  bf16           143           52           14            1          201           90           28            3
 2  1  bf16           216           59           15            1          383          115           30            3
 4  5  fp32           226          101           31            4          285          160           58            8
 4  5  bf16           318          116           32            4          447          201           63            8
 4  1  bf16           480          132           34            4          853          256           67            8
 8  5  fp32           337          122           34            4          475          213           66            8
 8  5  bf16           424          132           35            4          669          244           69            8
 8  1  bf16           539          142           36            4         1010          279           71            9

--- NVFP4 ---
 n  S state     BF16KV 8K   BF16KV 32K  BF16KV 128K BF16KV 1024K     FP8KV 8K    FP8KV 32K   FP8KV 128K  FP8KV 1024K
 1  5  fp32            46           20            6       infeas           58           32           11            1
 1  5  bf16            65           23            6       infeas           91           41           12            1
 1  1  bf16            98           27            6       infeas          174           52           13            1
 2  5  fp32           108           48           15            2          136           77           28            4
 2  5  bf16           152           55           15            2          214           96           30            4
 2  1  bf16           230           63           16            2          409          123           32            4
 4  5  fp32           233          104           32            4          293          165           60            8
 4  5  bf16           328          120           33            4          461          207           65            8
 4  1  bf16           495          136           35            4          878          264           69            8
 8  5  fp32           342          124           35            4          482          216           67            9
 8  5  bf16           430          134           35            4          678          248           70            9
 8  1  bf16           547          144           36            4         1025          283           72            9

--- INT4 ---
 n  S state     BF16KV 8K   BF16KV 32K  BF16KV 128K BF16KV 1024K     FP8KV 8K    FP8KV 32K   FP8KV 128K  FP8KV 1024K
 1  5  fp32            48           21            6       infeas           60           34           12            1
 1  5  bf16            67           24            7       infeas           95           42           13            1
 1  1  bf16           102           28            7       infeas          181           54           14            1
 2  5  fp32           110           49           15            2          139           78           28            4
 2  5  bf16           155           56           16            2          218           98           30            4
 2  1  bf16           234           64           16            2          416          125           33            4
 4  5  fp32           235          105           32            4          296          166           60            8
 4  5  bf16           330          121           34            4          465          209           65            8
 4  1  bf16           499          138           35            4          886          266           70            8
 8  5  fp32           343          125           35            4          484          217           67            9
 8  5  bf16           431          135           36            4          681          249           70            9
 8  1  bf16           549          144           36            4         1029          284           73            9
```

**Cross-checks against the two documents that already carry a row for this pair.**

| Source | Their cell | This table | Verdict |
|---|---|---|---|
| [architecture.md §10.2](./architecture.md) — 1 GPU, FP8 KV, bf16 state, S=5/S=1 | BF16 **40 / 77** @8K; NVFP4 **91 / 174** @8K | 40/77 and 91/174 | ✅ exact |
| [gpus/rtx6000-pro.md §9g](../../gpus/rtx6000-pro.md) — 1 GPU, S=1, fp32 state, **BF16 KV** for the BF16 row | BF16 **38**, FP8 **122**, NVFP4 **143**, INT4 **149** @8K | 38 / 122 / 143 / 149 on the same assumptions | ✅ exact. Note the GPU doc's FP8/NVFP4/INT4 rows use **FP8 KV** and its BF16 row uses **BF16 KV** — not a single consistent assumption across the row set; this document's Table 2 separates them. |

**Three readings of Table 2 that matter.**

- **At 8 K the GDN state pool, not the KV cache, sets concurrency.** One fp32 slot is worth
  4,698 FP8-KV tokens; at `S = 5` that is **23,490 tokens per request** — five times an 8 K
  prompt. `--mamba-ssm-dtype bfloat16` alone takes NVFP4/FP8-KV/8K from **58 → 91** (+57 %), and
  `--disable-radix-cache` (S=1) takes it to **174**. Both are free in memory and neither is free
  in behaviour: S=1 forfeits every prefix-cache hit ([architecture.md §5.6](./architecture.md)),
  and bf16 state is *"an accuracy gate"* SGLang tells you to validate.
- **Adding GPUs buys sub-linear KV.** 1 → 4 cards multiplies the aggregate KV budget by 5.0×
  (60.5 → 304.9 GB) because the weights stop dominating, but 4 → 8 buys only 2.07× because the
  weight term is already small and **KV heads replicate past TP4**. Combined with the measured
  6–7 tok/s TP4 collapse over PCIe ([gpus/rtx6000-pro.md §4](../../gpus/rtx6000-pro.md)),
  **there is no throughput reason to run TP > 1 on this pair, and a marginal capacity reason at
  most.**
- **1 M context needs two cards and NVFP4.** One sequence at 1 M with FP8 KV and bf16 state is
  **34.75 GB**; at BF16 KV it is **69.11 GB**, which exceeds every single-card KV budget in
  Table 1. So: **1 M on one card is FP8-KV-only, one sequence, NVFP4 or INT4 weights.** Two cards
  give 3–4 concurrent 1 M sequences.

```
TABLE 3 - per-sequence bytes (1 GPU), GB
      ctx   FP8KV+bf16 S=5   FP8KV+fp32 S=5   BF16KV+bf16 S=5  FP8KV+bf16 S=1
     4608            0.543            0.921            0.694           0.229
     8192            0.661            1.038            0.929           0.347
    33792            1.500            1.877            2.607           1.186
   133072            4.753            5.130            9.113           4.439
   262144            8.982            9.360           17.572           8.668
  1048576           34.752           35.129           69.112          34.438
```

---

## 2. What runs on this GPU for this model

The one-sentence version: **this is the friendliest possible model for sm_120, because every
kernel it needs is a *dense* kernel and every broken path on this card is a *sparse* or *grouped*
one.** No MoE grouped GEMM, no MLA, no DSA indexer, no FP4 KV requirement.

| Optimization | Status | Kernel / flag | Expected effect |
|---|---|---|---|
| **Attention kernel — 16 GQA layers** | **native (FA2-class)** | SGLang `--attention-backend flashinfer` (FlashInfer's FA2 backend; `determine_attention_backend()` returns `"fa2"` for SM12x); Triton is the fallback. SGLang verbatim: *"SM120/SM121 (RTX PRO 6000 Blackwell, RTX 5090, DGX Spark): `triton` or `flashinfer` for prefill/full attention; `trtllm_mha` is supported for `--decode-attention-backend` only"* [flash-attention.md §9.5](../../cross-cutting/flash-attention.md) | Works, head_dim 256 supported. **Not** the tuned Blackwell path: Ampere-class `mma.sync` MMA in 99 KB of shared memory. Plan `MFU_attn 0.20–0.35`, below the Hopper default ([gpus/rtx6000-pro.md §5e](../../gpus/rtx6000-pro.md)) |
| **FA3** | **unsupported** | — | 9.x only. Never will run here. |
| **FA4** | **unsupported in vLLM**; upstream SM120 kernel exists but is SM80-MMA with **no paged KV** | vLLM: *"FA4 is only supported on devices with compute capability 9.x, 10.x, or 11.x"* [flash-attention.md §9.5](../../cross-cutting/flash-attention.md) | Irrelevant — paged KV is mandatory for a server. |
| **FlashMLA / FlashInfer MLA / CUTLASS MLA / TokenSpeed MLA** | **unsupported**, and **not needed** | all gate on major 9/10 | Qwen3.8-27B is GQA, not MLA. METHODOLOGY's MLA row does not apply ([architecture.md §5.5](./architecture.md)). |
| **Sparse / DSA indexer attention** | **N/A — the model has none** | no indexer tensors, no `nsa`/`dsa` config | The whole SM120 sparse-MLA disaster (TileLang 202 KB vs 99 KB shared memory, GLM-5.x NoPE unrunnable) is simply off the table for this pair. |
| **Linear-attention (GDN) prefill** | **native** — and here the two engines **disagree** | vLLM: FlashInfer GDN prefill gate is `family 120 + head_k_dim == 128 + CUDA ≥ 13` ✅, and this model's `linear_key_head_dim = 128` ✅ [flash-attention.md §9.5](../../cross-cutting/flash-attention.md). SGLang: the FlashInfer GDN fast path *"On SM100/SM103 with CUDA 13+"* only, so SM120 runs the **Triton** linear-attn prefill path ([architecture.md §8.4](./architecture.md)) | **Disagreement recorded per METHODOLOGY §8.** On vLLM you may get the CuTe-DSL-derived GDN prefill (~20–25 % faster prefill); on SGLang you get Triton. Note vLLM's in-tree *CuteDSL* GDN kernel is explicitly SM100-only: *"The in-tree CuteDSL kernel targets SM100 only, so it stays off here."* **⚠️ TO BE VERIFIED** which path a given build actually selects — read the boot log. |
| **Linear-attention (GDN) decode** | **native** (Triton) | `--linear-attn-backend triton` | O(1) per token; the cost is bandwidth, not FLOPs (§3.1). |
| **Weight format — NVFP4 W4A4** | **native execution, not dequant** | `nvidia/Qwen3.8-27B-NVFP4`; vLLM selects `FlashInferCutlassNvFp4LinearKernel for NVFP4 GEMM` on sm120 — *"a cutlass path, not an emulation fallback"* [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B). [quantization-formats.md §9.6 note (l)](../../cross-cutting/quantization-formats.md): *"The sm120 NVFP4 caveat … is a MoE grouped-GEMM problem. Qwen3.8-27B is dense, so the plain NVFP4 linear path applies and this cell is native."* | **1,920 dense TFLOPS FP4** on the 64 MLPs + `lm_head` (18.38 B params), FP8 on the attention/GDN projections. Effective mixed GEMM peak **1,525 TFLOPS** `est.` ⚠️ The "real kernel" quote is from the recipe's **2× RTX 5090** section, not its RTX PRO 6000 section — same SM version, different board, **no NVFP4 GEMM measurement published on the 6000 itself** ([inference-engines.md §4](../../cross-cutting/inference-engines.md)). |
| **Weight format — NVFP4 via Marlin (the trap)** | **dequant W4A16 if auto-selection misfires** | vLLM resolves ModelOpt NVFP4 checkpoints as **W4A16** for the *MoE* path and auto-selects Marlin; two silent traps push SM120 users there — `flashinfer-python`/`flashinfer-cubin` version drift and a stale `nvcc` with `CUDA_HOME` unset ([gpus/rtx6000-pro.md §6a](../../gpus/rtx6000-pro.md)) | Memory win kept, **compute win lost**. On a dense model this should not trigger, but it is the single most likely way to silently lose 40 % of prefill. Check the boot log for the selected linear kernel. |
| **Weight format — FP8 E4M3 block-128** | **native** | `Qwen/Qwen3.8-27B-FP8`; CUTLASS SM120 FP8. **DeepGEMM does NOT support sm_120** (`arch_major=12` → `DG_HOST_UNREACHABLE`), but vLLM *"auto-disables DeepGemm for `model_type=qwen3_5_text` on Blackwell and falls back to CUTLASS, so it loads unaided"* ([architecture.md §8.3](./architecture.md)) | 960 dense TFLOPS. ⚠️ [quantization-formats.md §9.6 note (k)](../../cross-cutting/quantization-formats.md): FP8 *per-tensor* is native on sm120, but **block-scaled** FP8 is absent from TRT-LLM's sm120 row — engine-dependent. vLLM/SGLang are fine. |
| **Weight format — MXFP4** | **unsupported (would be Marlin dequant)** | vLLM: *"MXFP4 does not load on Nvidia devices … Use NVFP4 quantization on Nvidia instead"* ([architecture.md §4](./architecture.md)) | Moot — **no MXFP4 Qwen3.8-27B checkpoint exists** ([architecture.md §9.2](./architecture.md)). |
| **Weight format — INT4 W4A16 (AWQ/GPTQ/Marlin)** | **dequant** | `RedHatAI/Qwen3.8-27B-INT4`, `--quantization compressed-tensors`; Marlin unpacks to BF16 and runs on the 480 TFLOPS BF16 tensor cores | Smallest checkpoint (19.45 GB → highest concurrency, 95 @8K) but the **slowest prefill of any format on this card** (2,630 tok/s vs NVFP4's 8,046 `est.`) because it forfeits the FP4 tensor core. Decode-only workloads may still prefer it. |
| **KV cache — FP8 E4M3** | **native** | `--kv-cache-dtype fp8_e4m3` (SGLang RTX PRO 6000 cell) / `--kv-cache-dtype fp8` (vLLM). The RadixArk NVFP4 builds declare `kv_cache_quant_algo: FP8` so `auto` already selects it; **the NVIDIA export ships no `kv_cache_scheme`, so `auto` leaves its pool BF16** — pin it explicitly ([architecture.md §5.5](./architecture.md)) | Halves KV bytes/token. Measured **1.19×**, not 2×, more KV *tokens* on a 5090, because the state pool does not shrink with KV dtype ([serving-optimizations.md §7.3](../../cross-cutting/serving-optimizations.md)). On this card at 8 K/NVFP4 it is 65 → 91 concurrent (+40 %). |
| **KV cache — NVFP4 / INT4** | **unsupported for this model** | The `QE4m3KvE2m1` FMHA kernel has no SM120/121 variant ([gpus/rtx6000-pro.md §6](../../gpus/rtx6000-pro.md)). FlashInfer PR #4955 added NVFP4 KV for SM120 but **only on the sparse-MLA DeepSeek-V4 path** ([flash-attention.md §9.5](../../cross-cutting/flash-attention.md)) — a geometry this model does not have. SGLang's MHA matrix shows FP4 KV on FA4/Triton/Torch/Flex/TRTLLM-MHA but **not FlashInfer**, which is the backend every recipe pins ([architecture.md §5.5](./architecture.md)) | **⚠️ TO BE VERIFIED but plan as unavailable.** Would take KV to 16 KiB/token and roughly double the 8 K concurrency. GQA with only 4 KV heads has little redundancy to spare; validate accuracy if you try it via `--attention-backend triton`. |
| **Prefix caching (radix / APC)** | **native, and it is what `S = 5` pays for** | SGLang RadixAttention on by default, `--mamba-radix-cache-strategy extra_buffer`; vLLM APC + `--mamba-cache-mode align` + `--enable-mamba-fine-grained-prefix-cache` | A hit restores **both** the KV block and a GDN state checkpoint (~154 MB read ≈ 0.12 ms at 1,278 GB/s — negligible). `preserve_thinking: true` (default) is what makes multi-turn agent traffic a strict prefix extension ([architecture.md §5.6](./architecture.md)). Cost: 4 extra state slots per request, i.e. half your concurrency at 8 K. |
| **Speculative decoding — MTP (in-checkpoint)** | **native** | vLLM `--speculative-config '{"method":"mtp","num_speculative_tokens":5}'` — **5, not the default 3, is this card's `hardware_overrides` value** [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B/hw/rtx_pro_6000.json); SGLang `--speculative-algorithm EAGLE --speculative-num-steps 3 --speculative-eagle-topk 1 --speculative-num-draft-tokens 4` | Both NVFP4 builds leave the MTP head **BF16 (0.849 GB)**, so every draft step reads 0.85 GB. `meas.` 150 tok/s at bs=1 with NVFP4+MTP (vLLM 0.27.1) vs ~59 `est.` without — see §3.3. **⚠️** SM120 MTP under FlashInfer needs a build newer than `0.6.15.post1` (prefill `plan` must accept `uniform_q_len`); otherwise `--attention-backend triton`. |
| **Speculative decoding — DFlash2** | **native**, best published acceptance | vLLM ≥ 0.28.0 `{"method":"dflash","model":"incoai/Qwen3.8-27B-DFlash2","num_speculative_tokens":7}`; SGLang `--speculative-algorithm DFLASH --speculative-num-draft-tokens 8`. Draft is 3.85 GB BF16 | Mean acceptance **4.80** across five benchmarks ([architecture.md §7.2](./architecture.md), inco.ai). `meas.` **240+ tok/s** single-stream on one RTX PRO 6000 with `RadixArk/…-NVFP4-BF16-LMHead` + DFlash2 [src](https://github.com/MiaAI-Lab/Qwen3.8-27B-RTX-6000-PRO-SGLang-DSpark). Costs `D = 8` extra state slots. |
| **Speculative decoding — DSpark** | native but **do not** | `--speculative-algorithm DSPARK --speculative-draft-model-path RadixArk/Qwen3.8-27B-DSpark` | Weakest published acceptance (mean 3.62), licensed `other`, tightest memory pins. *"For this model there is no configuration in which DSpark is the right first choice"* ([architecture.md §7.3](./architecture.md)). Its 3.51 MBPP figure is a **real measurement** for this model — not the synthetic MI355X constant of the same value. |
| **EP / wide-EP / DeepEP / EPLB** | **N/A** | — | Dense model, no experts. |
| **TP** | supported, **strongly discouraged** | `--tensor-parallel-size n` | TP4 over PCIe measured **6–7 tok/s** where TP2+PP2 gave 46–49 on the same build ([gpus/rtx6000-pro.md §4](../../gpus/rtx6000-pro.md)) — ~85 % overhead. TP2 adds ~2.6 ms/step of all-reduce at bs=64 by pure PCIe arithmetic (`128 all-reduces × 1.31 MB / 64 GB/s`) `est.`, on top of latency and sync. |
| **DP-attention / replication** | **native, and the right answer** | one process per GPU, load-balance in front | The only parallelism this pair should use. Measured precedent on this card: SGLang `dp=4` gave **1,408 tok/s @8 concurrent and 3,190 @16** on a 35 B MoE ([gpus/rtx6000-pro.md §10c](../../gpus/rtx6000-pro.md)). |
| **PP** | supported, unnecessary | `--pipeline-parallel-size` | The PCIe-friendly split when a model does not fit; this one fits. |
| **PD disaggregation (Dynamo / SGLang PD / NIXL)** | **unsupported in practice** | vLLM's `compatible_strategies` for this model is `["single_node_tp"]` — **no P/D layout is published** ([architecture.md §8.1](./architecture.md)). Dynamo has no sm_120 support statement at all ([gpus/rtx6000-pro.md §7](../../gpus/rtx6000-pro.md)) | **⚠️ TO BE VERIFIED** whether the GDN recurrent state transfers over NIXL at all. Also LMCache's PyPI wheels carry **no sm_120 cubins and no PTX** → `cudaErrorNoKernelImageForDevice`; build from source. Irrelevant anyway: PD is a thousand-GPU problem and this is a one-GPU model. |
| **CUDA graphs** | **native and important** | on by default; `--cuda-graph-max-bs-decode` (SGLang) / `--max-cudagraph-capture-size` (vLLM) | Not the 32 GB 5090 trap — at 96 GB there is headroom, and `--enforce-eager` is **not** needed here ([inference-engines.md §6.4](../../cross-cutting/inference-engines.md)). Size the capture to cover `max_num_seqs × (1 + num_speculative_tokens)`. Note capture allocates **outside** `--gpu-memory-utilization` ([serving-optimizations.md §4.2](../../cross-cutting/serving-optimizations.md)). |
| **Chunked prefill** | **native, mandatory tuning** | `--chunked-prefill-size 2048` (both RTX PRO 6000 recipes) | SGLang verbatim: *"decode steps stall behind each prefill chunk on hybrid GDN models, and 8192-token chunks stall them ~600ms at a time"* ([architecture.md §8.4](./architecture.md)). This is why the H200 recipe uses 32 K chunks and this card uses 2 K. |
| **ReplaySSM** | ⚠️ **TO BE VERIFIED** | vLLM `--use-replayssm --replayssm-buffer-len 16` (needs `mamba_cache_mode` `none`/`align`, Triton or FlashInfer mamba backend, **non-speculative decode**); SGLang `--enable-linear-replayssm-spec` sets `D = 0` | Directly attacks the state write-back that is 24 % of the decode step at bs=128/8K ([architecture.md §6.3](./architecture.md)). **No measured gain published for Qwen3.8-27B.** |
| **Multimodal encoder placement** | **native, DP** | vLLM RTX PRO 6000 recipe pins `--mm-encoder-tp-mode data`; `--language-model-only` drops the tower entirely [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B/hw/rtx_pro_6000.json) | At TP1 "DP encoder" is a no-op. The lever that matters is `--language-model-only` for text traffic: **+0.921 GB of KV budget** and skips 0.46 B of loading. ⚠️ Whether either engine chunks the ViT's full bidirectional attention at the 16.78 Mpx maximum (an unchunked 65,536² FP16 score matrix is 8.6 GB) is **TO BE VERIFIED** ([architecture.md §6.4](./architecture.md)) — and on this card the 99 KB shared-memory ceiling makes a Hopper-tuned ViT kernel a launch failure, not a slowdown. |
| **TensorRT-LLM** | ⚠️ **avoid for this pair** | `Qwen3_5ForConditionalGeneration` is in the PyTorch-backend multimodal matrix, but **KV cache reuse = No** for hybrid recurrent models without an explicit snapshot policy, and the field report is *"Qwen 3.5/3.6 + NVFP4 ❌ (GDN plugin pending, #11674)"* ([architecture.md §8.4](./architecture.md), [gpus/rtx6000-pro.md §7](../../gpus/rtx6000-pro.md)) | trtllm-gen FMHA cubins do not exist for SM12x and **NVIDIA has no plan to ship them**. |

---

## 3. Throughput and latency

### 3.1 Assumptions (METHODOLOGY §4), and how they were calibrated

`decode_step_time ≈ max( bytes_per_decode_step / (HBM_BW × MBU), 2 × active_params × batch / (peak × MFU) )`.

**Bytes per decode step.** This model needs METHODOLOGY §4's formula *extended*, because the GDN
state is read **and written back** every step ([architecture.md §6.3](./architecture.md)):

```
bytes_per_decode_step(batch, ctx) = W_decode + batch × ctx × kv_bytes_per_token
                                              + batch × 2 × state_bytes     (read + write)
W_decode = checkpoint − vision tower − MTP head        (neither is read in text decode)
         = 21.923 − 0.921 − 0.849 = 20.153 GB   (NVFP4)
         = 30.870 − 0.921 − 0.478 = 29.471 GB   (FP8)
         = 55.563 − 0.921 − 0.849 = 53.793 GB   (BF16)
```
Note the `S` asymmetry: `S` multiplies **memory** (§1) but not **bandwidth** — only the live slot
is touched each step; the other `S − 1` are prefix checkpoints sitting idle.

**MBU — calibrated, not assumed.** METHODOLOGY §4's Blackwell default is 0.5–0.7 and
[gpus/rtx6000-pro.md §9b](../../gpus/rtx6000-pro.md) narrows it to *"0.50–0.65 on sm_120, treat
0.70 as optimistic"*. **That band was set on MoE workloads** — the same document backs out
MBU ≈ 0.26 on active bytes for a small-expert MoE, where expert GEMMs are tiny and launch-bound.
A dense 27 B model is the opposite case: one contiguous weight stream per step. Five published
bs=1 measurements on this card, inverted:

```
MBU calibration against the published bs=1 measurements
vLLM FP8, MTP off, HF-FP8 disc#9          bytes/step 29.66 GB -> 1388 GB/s = MBU 0.869 (SE 1597) / 0.775 (WS 1792)
vLLM FP8 (Qwen3.6-27B proxy) Millstone    bytes/step 29.70 GB -> 1369 GB/s = MBU 0.857 (SE 1597) / 0.764 (WS 1792)
SGLang BF16+EAGLE accept 3.0, HF#160      bytes/step 54.22 GB -> 1410 GB/s = MBU 0.883 (SE 1597) / 0.787 (WS 1792)
vLLM NVFP4+MTP accept 2.5, vaditaslim     bytes/step 20.44 GB -> 1227 GB/s = MBU 0.768 (SE 1597) / 0.685 (WS 1792)
SGLang NVFP4+DFlash2 accept 4.3, MiaAI    bytes/step 20.44 GB -> 1141 GB/s = MBU 0.715 (SE 1597) / 0.637 (WS 1792)
```

The three unspeculated / low-γ rows cluster at **0.86–0.88 against the Server Edition's
1,597 GB/s**, which is implausibly high — but at the **Workstation Edition's 1,792 GB/s** they
land at **0.76–0.79**, which matches the ~82 % effective device-memory traffic independently
measured on Workstation silicon ([gpus/rtx6000-pro.md §8d](../../gpus/rtx6000-pro.md)).
**⚠️ Every published Qwen3.8-27B measurement on "RTX PRO 6000" is Workstation-bandwidth silicon**
— HF discussion #160 states its card's bandwidth as *"~1.79 TB/s"* outright, and no source names
a Server Edition. **This document plans the Server Edition at MBU 0.80 → 1,278 GB/s effective**,
and every table shows the MBU 0.65 sensitivity. If you rent from a marketplace that does not
distinguish the SKUs, assume Workstation and add 12 %.

**MFU.** [gpus/rtx6000-pro.md §9e](../../gpus/rtx6000-pro.md) gives 0.25–0.40 FP8 / 0.20–0.35 FP4
on sm_120, and MFU_attn 0.20–0.35 BF16 (§5e). Adopted:

| Term | Peak used | MFU | Effective | Basis |
|---|---:|---:|---:|---|
| GEMM, BF16 | 480 TFLOPS | 0.34 | 163.2 | back-solved from the Millstone FP8 TTFT below |
| GEMM, FP8 | 960 | 0.34 | 326.4 | **calibrated**: Qwen3.6-27B FP8 on 1× RTX PRO 6000, `meas.` TTFT **170 ms** at 1 K context; this model gives **170.5 ms** `est.` at MFU 0.34 — inside the doc's 0.25–0.40 band |
| GEMM, NVFP4 mixed | **1,525** `est.` | 0.30 | 457.5 | 0.636 × 1,920 (MLPs + `lm_head`) + 0.269 × 960 (attn/GDN projections, FP8) + 0.095 × 480 (BF16 remainder), shares from [architecture.md §3.2](./architecture.md) |
| GEMM, INT4 W4A16 | 480 (BF16 after Marlin dequant) | 0.30 | 144.0 | no FP4 tensor path is used |
| **Attention** | **480 BF16** | 0.25 | 120.0 | **attention runs BF16 on this card whatever the weight format**: SGLang states *"FMHAv2 has no FP8 (e4m3) prefill kernel on SM120/SM121"* and the only SM120 FMHA_v2 symbols in TRT-LLM are `..._bf16_..._sm120_...` ([gpus/rtx6000-pro.md §5d](../../gpus/rtx6000-pro.md)) |

Prefill, per [architecture.md §6.2](./architecture.md):
`prefill_s(T, h) = 2 × 26.896e9 × (1−h)T / (peak_eff × MFU) + 196,608 × (T² − (hT)²) / (480e12 × 0.25)`.
The GDN recurrence adds 0.302 GFLOP/token (0.56 % of the GEMM term, [architecture.md §6.1](./architecture.md))
and is ignored; even at 10 % kernel efficiency it is under 6 % of a 4 K prefill.

**Independent check on the NVFP4 MFU (not used to fit it):** at T = 512 the model predicts
**61 ms** TTFT; vaditaslim measures **63 ms** on vLLM 0.27.1 NVFP4 at an unstated prompt length
[src](https://www.vaditaslim.com/blog/ai/qwen3.8-27b-two-rigs). ✅

### 3.2 Estimated grid — 1× RTX PRO 6000 SE, NVFP4 weights, FP8 KV, bf16 GDN state, S = 5

Context = input + output. Rows above `max_concurrency(ctx)` print **infeasible (KV)** per
METHODOLOGY §3. TTFT is the **isolated** single-request prefill (see the caveat below the table).

```
TABLE 4 - throughput / latency, 1x RTX PRO 6000 SE, NVFP4 + FP8 KV + bf16 GDN state, S=5, MBU 0.80
scen    bs      ctx  bytes/step GB   TPOT ms  tok/s/GPU  agg tok/s  TTFT h=0 ms  TTFT h=.9 ms  max conc
S1       1     4608          20.46     16.02       62.4       62.4          509            53       111
S1       8     4608          22.62     17.70       56.5      451.9          509            53       111
S1      32     4608          30.01     23.49       42.6     1362.5          509            53       111
S1      64     4608          39.86     31.20       32.1     2051.5          509            53       111
S1     128     4608                          infeasible (KV)                                         111
S1     256     4608                          infeasible (KV)                                         111
S2       1    33792          21.42     16.76       59.7       59.7         5612           720         40
S2       8    33792          30.27     23.69       42.2      337.7         5612           720         40
S2      32    33792          60.61     47.44       21.1      674.6         5612           720         40
S2      64    33792                          infeasible (KV)                                          40
S2     128    33792                          infeasible (KV)                                          40
S2     256    33792                          infeasible (KV)                                          40
S3       1   133120          24.67     19.31       51.8       51.8        43559          6889         12
S3       8   133120          56.30     44.07       22.7      181.5        43559          6889         12
S3      32   133120                          infeasible (KV)                                          12
S3      64   133120                          infeasible (KV)                                          12
S3     128   133120                          infeasible (KV)                                          12
S3     256   133120                          infeasible (KV)                                          12
```

```
TABLE 5 - same grid, FP8 weights (the conservative choice if you do not trust NVFP4 W4A4)
scen    bs      ctx  bytes/step GB   TPOT ms  tok/s/GPU  agg tok/s  TTFT h=0 ms  TTFT h=.9 ms  max conc
S1       1     4608          29.78     23.31       42.9       42.9          703            73        94
S1       8     4608          31.93     25.00       40.0      320.1          703            73        94
S1      32     4608          39.32     30.78       32.5     1039.7          703            73        94
S1      64     4608          49.18     38.49       26.0     1662.7          703            73        94
S1     128     4608                          infeasible (KV)                                          94
S1     256     4608                          infeasible (KV)                                          94
S2       1    33792          30.74     24.06       41.6       41.6         7160           874        34
S2       8    33792          39.58     30.98       32.3      258.2         7160           874        34
S2      32    33792          69.93     54.73       18.3      584.7         7160           874        34
S2      64…256                               infeasible (KV)                                          34
S3       1   133120          33.99     26.60       37.6       37.6        49749          7508        10
S3       8   133120          65.62     51.36       19.5      155.8        49749          7508        10
S3      32…256                               infeasible (KV)                                          10
```

```
TABLE 11 - S4 max-throughput point (bs = max_concurrency at ctx 4,608) and MBU sensitivity
  NVFP4  MBU 0.80  bs=111  bytes 54.33 GB  TPOT 42.5 ms  agg 2610.3 tok/s
  NVFP4  MBU 0.65  bs=111  bytes 54.33 GB  TPOT 52.3 ms  agg 2120.9 tok/s
  FP8    MBU 0.80  bs= 94  bytes 58.41 GB  TPOT 45.7 ms  agg 2056.0 tok/s
  FP8    MBU 0.65  bs= 94  bytes 58.41 GB  TPOT 56.3 ms  agg 1670.5 tok/s

S1 SLO point (largest grid batch with TPOT <= 50 ms), sensitivity to MBU
  NVFP4  MBU 0.80: bs=64  TPOT 31.20 ms  agg 2051.5 tok/s
  NVFP4  MBU 0.65: bs=64  TPOT 38.40 ms  agg 1666.8 tok/s
  NVFP4  MBU 0.60: bs=64  TPOT 41.60 ms  agg 1538.6 tok/s
  FP8    MBU 0.80: bs=64  TPOT 38.49 ms  agg 1662.7 tok/s
  FP8    MBU 0.65: bs=64  TPOT 47.37 ms  agg 1351.0 tok/s
  FP8    MBU 0.60: bs=32  TPOT 41.04 ms  agg  779.7 tok/s   <- the SLO breaks at bs=64
```

Prefill rates at T = 4,096, `h = 0` (`est.`): **NVFP4 8,046 · FP8 5,830 · BF16 2,973 ·
INT4-Marlin 2,630 tok/s**. A full 262 K prefill is **143 s**; a full 1 M prefill is **1,925 s
(32 minutes)** — at 1 M, 79 % of the FLOPs are quadratic attention running on a BF16 FA2-class
kernel. **1 M context on this card is a prefix-cache-or-nothing proposition.**

**Four caveats on the TTFT columns.**

1. They are **isolated single-request** prefill times. At concurrency `c` with
   `--chunked-prefill-size 2048`, a 4 K prompt is two chunks interleaved with decode; queueing,
   not arithmetic, sets the observed TTFT. The one measured anchor is Millstone's
   **170 ms at 1 K, 70.0 s at 256 K** (Qwen3.6-27B FP8, 1× RTX PRO 6000, no prefix caching, no
   speculation) — against this model's 143 s `est.` at 262 K on NVFP4, so the measured card is
   roughly 2× faster than the SE roofline predicts at long context. See §3.4.
2. The `h = 0.9` column keeps the full quadratic attention over the *whole* context while
   skipping 90 % of the GEMM, which is why it is not simply 10 % of the `h = 0` column at long
   context (S3: 43.6 s → 6.9 s, a 6.3× not 10× reduction).
3. A prefix hit on this model also restores a **GDN state checkpoint**: ~154 MB at fp32, or
   **0.12 ms** at 1,278 GB/s effective. Negligible against any real prefill — and this is exactly
   what the `S = 5` slot reservation buys.
4. TTFT under a mixed batch is what `--chunked-prefill-size 2048` is defending: SGLang's own
   guidance is that 8 K chunks *"stall [decode] ~600 ms at a time"* on hybrid GDN models.

### 3.3 Measured — everything published for this pair or its nearest proxy

| # | Model | GPU (edition) | Engine | Precision / KV | Config | Result | Source |
|---|---|---|---|---|---|---|---|
| 1 | **Qwen3.8-27B** | RTX PRO 6000 (⚠️ edition unstated) | vLLM | FP8 / FP8 KV | bs=1, **no speculation** | **46.8 tok/s** | [HF Qwen3.8-27B-FP8 disc #9](https://huggingface.co/Qwen/Qwen3.8-27B-FP8/discussions/9) |
| 2 | **Qwen3.8-27B** | same | vLLM | FP8 / FP8 KV | bs=1, MTP γ=1 / **γ=2** / γ=3 / γ=5 | **56.3 / 62.2 / 59.6 / 23** tok/s; γ=5 acceptance **2.32** | same |
| 3 | **Qwen3.8-27B** | RTX PRO 6000, **"~1.79 TB/s" ⇒ Workstation** | SGLang (flashinfer) | BF16 / FP8 KV, fp32 state | `--mem-fraction-static 0.9 --speculative-algorithm EAGLE --speculative-num-steps 3 --chunked-prefill-size 2048`, `--max-running-requests 8`, 262 K verified | **77–80 tok/s** warm (avg 77.6, peak 80.3); accept rate 0.94–1.00, **accept length 2.5–3.45**; KV pool **356,241 tokens (11.4 GB FP8)**; state cache **~9.1 GB**; weights 51.05 GB; static 78 / 86.4 GB; cold start **34.8 s**; warm e2e 0.88 s / 36 tok | [HF Qwen3.8-27B disc #160](https://huggingface.co/Qwen/Qwen3.8-27B/discussions/160) |
| 4 | **Qwen3.8-27B** | RTX PRO 6000 (edition ⚠️) | vLLM 0.20.1 | BF16 | bs=1, 256 K | **85 tok/s**, TTFT 110–160 ms | [vaditaslim](https://www.vaditaslim.com/blog/ai/qwen3.8-27b-two-rigs) |
| 5 | **Qwen3.8-27B** | same | vLLM 0.27.1 | **NVFP4** | bs=1, 256 K | **150 tok/s** raw (84 tok/s answer-only, ~½ the output is hidden reasoning), **TTFT 63 ms** | same |
| 6 | **Qwen3.8-27B** | 1× RTX PRO 6000 96 GB | SGLang `lmsysorg/sglang:qwen38-27b` + DFlash2 backport (PR #35371, 2026-08-19) | `RadixArk/…-NVFP4-BF16-LMHead` (23.6 GB) + `z-lab/Qwen3.8-27B-DFlash2` (3.8 GB, block 8) / FP8 KV | `--speculative-algorithm DFLASH --speculative-num-draft-tokens 8 --context-length 262144 --kv-cache-dtype fp8_e4m3 --max-running-requests 8 --mem-fraction-static 0.90` | **240+ tok/s** single stream; **KV pool ~1.7 M tokens (55 GB)**; 8 concurrent | [MiaAI-Lab/Qwen3.8-27B-RTX-6000-PRO-SGLang-DSpark](https://github.com/MiaAI-Lab/Qwen3.8-27B-RTX-6000-PRO-SGLang-DSpark) |
| 7 | **Qwen3.8-27B** | RTX PRO 6000 **Server Edition** (explicitly) | SGLang, pinned | NVFP4 / FP8 vs bf16 KV | DFlash2 block sweep 8/10/12/14/16, `--enable-torch-compile`, `--mem-fraction-static 0.85`; c1/c4/c8 + 82 K probes | baseline **149.8** → torch.compile **167.8** (+12 %) → best cell **335 tok/s**. Viral "454 tok/s" decomposed as `335 × ~1.1 (W4A4) × ~1.2 (memory OC)`; **the Server Edition's memory clock is locked at `[0,0]`, so the last ~120 tok/s is physically unreachable on SE silicon.** FP8 KV is *"for capacity, not speed — it doubles concurrent contexts per GPU but doesn't improve tok/s and costs a little at depth"* (~9 % at 82 K) | [HelixML](https://helix.ml/blog/chasing-454-toks-qwen38-rtx-pro-6000) |
| 8 | **Qwen3.8-27B** | 1× RTX PRO 6000 | `memra` (custom engine ⚠️) | nvfp4+q5_k | 262 K | **260 tok/s** p50 with spec, **TTFT 156 ms** | [HF disc #101](https://huggingface.co/Qwen/Qwen3.8-27B/discussions/101) |
| 9 | **Proxy: Qwen3.6-27B** (same `qwen3_5` family, dense 27 B hybrid) | 1× RTX PRO 6000 | vLLM | FP8, **BF16 KV**, no prefix cache, no spec | c=1…5, 1 K–256 K ctx, 1,024 out | per-user **46.1 tok/s @1 K → 30.4 @256 K**; ITL **22 ms** single-user → 164 ms (256 K, c=3); **TTFT 170 ms @1 K → 70.0 s @256 K**; peak system **189.3 tok/s @c=5**; capacity **29 concurrent @8 K, 3 @32 K, 1 @64 K** | [Millstone AI](https://www.millstoneai.com/inference-benchmark/qwen3-6-27b-fp8-1x-rtx-pro-6000-blackwell) |
| — | **MLPerf** | — | — | — | — | **No Qwen3.8-27B submission, and no RTX PRO 6000 row of any kind** ⚠️ ([gpus/rtx6000-pro.md §10a](../../gpus/rtx6000-pro.md)) | |
| — | **InferenceMAX / InferenceX** | — | — | — | — | **Qwen3.8-27B returns 0 rows; the RTX PRO 6000 has Qwen3.5 rows only** (METHODOLOGY §8) | |

### 3.4 Where the estimate and the measurement disagree, and why

| Cell | `est.` | `meas.` | Reconciliation |
|---|---:|---:|---|
| FP8, bs=1, no spec | 42.9 tok/s (TPOT 23.3 ms) | **46.8** (#1), **46.1** on the 3.6 proxy (#9) | `est.` is **9 % low**. Both measurements imply MBU 0.86 against 1,597 GB/s or 0.77 against 1,792. The cards were almost certainly Workstation Edition. On a true SE, expect ~42–43 tok/s. |
| NVFP4, bs=1, no spec | 62.4 tok/s | none published unspeculated | The closest is HelixML's **149.8 tok/s** *with* DFlash2 (#7): `62.4 × 2.4` accept ≈ 150. ✅ |
| NVFP4 + MTP γ=5, bs=1 | 129 tok/s at accept 2.5 (§3.5) | **150** (#5) | 16 % low — same MBU story, plus vLLM 0.27.1 may hold a higher acceptance than 2.5. |
| NVFP4 + DFlash2, bs=1 | 116 tok/s at accept 4.3 | **240+** (#6), **335** best cell (#7) | **The estimate is 2–2.9× low, and this is the largest gap in the document.** Two causes, both real: (a) my verify-step model charges `γ × 0.85 GB` for MTP-style sequential drafting, but **DFlash2 is a block-diffusion drafter that proposes all 8 tokens in one pass**, so the draft cost is ~1 pass, not 7 — correcting that alone gives ~190 tok/s; (b) `--enable-torch-compile` is worth a measured **+12 %** (#7). **The model under-predicts block drafters; do not use §3.5's DFlash2 row as a ceiling.** |
| BF16, bs=1, +EAGLE accept ~3.0 | 22.3 × 3.0 = 67 tok/s | **77–80** (#3), **85** (#4) | 13–21 % low; same bandwidth-edition story. |
| TTFT, FP8, T = 1 K | 170.5 ms | **170** (#9) | ✅ exact — but this is the datum MFU 0.34 was fitted to, so it is a consistency check, not an independent one. The independent one is NVFP4 T=512: **61 `est.` vs 63 `meas.`** ✅ |
| TTFT, 256–262 K | 143 s (NVFP4) / 187 s (FP8 `est.`) | **70.0 s** (#9, FP8, Qwen3.6-27B) | **The estimate is 2.7× pessimistic at long context.** The attention term is 49 % of a 262 K prefill and I charged it at MFU_attn 0.25 on 480 BF16 TFLOPS; the measurement implies ~0.6, i.e. the FlashInfer FA2 path on sm_120 is considerably better than [gpus/rtx6000-pro.md §5e](../../gpus/rtx6000-pro.md)'s 0.20–0.35 planning band for a 256-dim head. ⚠️ **TO BE VERIFIED** — this is the single largest calibration error in the document and it moves every long-context TTFT and every `$/1M input` figure at S2/S3. |
| Concurrency ceiling at 8 K | **91** (NVFP4, S=5, bf16 state) / 77 (FP8) | **29** (#9, FP8, **BF16 KV**, fp32 state, and vLLM's unpublished `S`) | Reconciles: at FP8 weights + **BF16** KV + fp32 state + S=5, Table 2 gives **39**. The remaining gap is vLLM's `S` (⚠️ unpublished) and its `gpu_memory_utilization` default of 0.90 vs my 0.90 with a 4 GB workspace. Millstone's 29 is a *conservative* real number and is the best sanity bound in this document. |

**Everything published is bs=1 or ≤ 8 concurrent.** There is no multi-concurrency throughput
curve for this pair. Tables 4, 5 and 11 are `est.` and should be replaced with
`sglang.bench_serving --dataset-name random --random-input-len 4096 --random-output-len 512
--max-concurrency {1,8,32,64,111}` output before anyone commits capacity.

### 3.5 Speculative decoding at the S1 operating point

Verify step reads the weights once and verifies `γ+1` positions; draft steps read the drafter.
Both NVFP4 builds leave the MTP head **BF16 (0.849 GB)** and DFlash2 ships a **3.85 GB** draft.

```
TABLE 6 - speculative decoding, NVFP4 + FP8 KV, ctx 4,608, MBU 0.80        (est.)
method                                    bs    TPOT/user   agg tok/s   per-user tok/s
none                                       1      16.02 ms       62.4            62.4
none                                       8      17.70 ms      451.9            56.5
none                                      32      23.49 ms     1362.5            42.6
MTP gamma=5 (vLLM RTX PRO 6000 override)   1       7.74 ms      129.3           129.3
MTP gamma=5                                8       8.41 ms      951.3           118.9
MTP gamma=5                               32      10.72 ms     2984.1            93.3
MTP gamma=2 (measured best, accept 1.33)   1      13.04 ms       76.7            76.7
MTP gamma=2                               32      18.66 ms     1715.1            53.6
DFlash2 gamma=7/8 (accept 4.30)            1       8.63 ms      115.9           115.9   <- 2x low, see 3.4
DFlash2 gamma=7/8                         32      11.91 ms     2687.8            84.0
DSpark gamma=7 (accept 3.62)               1      10.04 ms       99.6            99.6
DSpark gamma=7                            32      13.93 ms     2297.1            71.8
```

**Read the γ=5 row against the measurement.** vLLM's `hardware_overrides` sets
`num_speculative_tokens: 5` for this card, but the one published sweep on this card found γ=5
**catastrophic** — 23 tok/s at acceptance 2.32 — and **γ=2 optimal at 62.2 tok/s**, versus 46.8
with no speculation (HF FP8 disc #9). The mechanism is exactly METHODOLOGY §5.5's: past the
acceptance-length knee, each extra draft step costs a full 0.85 GB MTP read for a token that is
usually rejected. **⚠️ The vLLM recipe's γ=5 default for the RTX PRO 6000 is contradicted by the
only user sweep on the card. Sweep γ ∈ {1,2,3} before trusting it.**

**And speculation costs concurrency.** The verify intermediates take `D` extra state slots per
request ([architecture.md §5.4](./architecture.md)): `D = 4` for MTP at the recommended 3/1/4,
**`D = 8` for DFlash2**. At `S + D = 13` the 8 K NVFP4 concurrency ceiling falls from 91 to
**~43**, and at ctx 4,608 DFlash2 at bs=64 becomes **infeasible (KV)**. `--enable-linear-replayssm-spec`
sets `D = 0` and is the flag that makes speculation and concurrency coexist on this model — ⚠️ no
measurement of it exists for this pair.

---

## 4. Cost

### 4.1 Prices used, cited by provider + instance

From [cloud-pricing.md §5.10](../../cross-cutting/cloud-pricing.md) and its §5.14 planning table.
**No neighbouring GPU's row is used anywhere in this section.**

| Tier | Provider + instance | $/GPU-hour |
|---|---|---:|
| **Cheapest reputable on-demand** (`low`) | **Nebius, "RTX PRO 6000", 1–8 GPUs** | **$1.80** |
| Second on-demand | Hyperstack, "RTX PRO 6000 **SE**", 1–8 — the only rate card that names the Server Edition | $1.85 |
| **Cheapest hyperscaler on-demand** (`high`) | **AWS `g7e.48xlarge` (8×, us-east-1)** | **$4.143** |
| Hyperscaler, 1 GPU | AWS `g7e.2xlarge` | $3.363 |
| Hyperscaler alt. | GCP `g4-standard-384` (8×) $4.500 · Oracle OCI "RTX PRO 6000" (B112613) $4.50 · **Azure: no SKU** | — |
| **1-year reserved** (`res1y`) | **Hyperstack "RTX PRO 6000 SE" reserved** | **$1.30** |
| 1-yr alt. | GCP `g4-standard-384` resource CUD 1 y $3.105 / 3 y $1.979 | — |
| Spot (not planned against) | Nebius preemptible $0.95 · CoreWeave spot $1.386 / $1.195 · GCP spot $1.743 · AWS spot $2.506 | — |
| Owned (context only) | [gpus/rtx6000-pro.md §8c](../../gpus/rtx6000-pro.md) colo-amortised **$0.877/GPU-h @100 % util**, $1.096 @80 %, $1.461 @60 %; [cloud-pricing.md §9](../../cross-cutting/cloud-pricing.md) runs the same model at **$0.962** | — |

**Disagreement recorded.** [architecture.md §11.3](./architecture.md) priced this card at
**$1.85 (Hyperstack)**; [cloud-pricing.md §5.14](../../cross-cutting/cloud-pricing.md)'s planning
table names **$1.80 (Nebius)** as the `low`. The 2.8 % gap moves nothing; this document uses
$1.80 so the cross-GPU matrix stays consistent with the planning table, and notes that
Hyperstack is the only vendor whose rate card actually says "SE". `high ÷ low` for this GPU is
**2.30×** — quote the band, never a point.

### 4.2 Cost at the two operating points (1 GPU, NVFP4, FP8 KV, bf16 state, S = 5, MBU 0.80)

```
TABLE 7 - cost per 1M output tokens
operating point                                bs  TPOT ms  agg tok/s  $/1M out low  $/1M out high  $/1M out res1y
S1  TPOT<=50ms (interactive)                   64    31.20     2051.5         0.244          0.561           0.176
S4  max feasible batch (no SLO)               111    42.50     2610.3         0.192          0.441           0.138
S2  TPOT<=50ms                                 32    47.44      674.6         0.741          1.706           0.535
S3  no SLO (max feasible)                       8    44.07      181.5         2.754          6.340           1.989

cost per 1M INPUT tokens (prefill, h=0)
scenario  prefill tok/s  $/1M in low  $/1M in high  $/1M in res1y
S1                 8046        0.062         0.143          0.045
S2                 5839        0.086         0.197          0.062
S3                 3009        0.166         0.382          0.120
```

At the MBU 0.65 sensitivity the S1 output cost becomes **$0.300 / $0.690 / $0.217** and S4
becomes **$0.236 / $0.543 / $0.170** — i.e. the whole verdict shifts ~23 % but changes no ranking.
On FP8 weights instead of NVFP4, S1 is **$0.301 / $0.692 / $0.217** — **NVFP4 is worth 19 % of
the output-token bill** on this card, purely from the smaller weight stream.

### 4.3 Blended $/1M tokens — METHODOLOGY §6 definition, verbatim

75 % input (50 % of it cached at ~10 % of uncached cost) / 25 % output, at the S1 operating point:
`blended = 0.75 × (0.5 × in + 0.5 × 0.1 × in) + 0.25 × out = 0.4125 × in + 0.25 × out`.

```
TABLE 8 - blended $/1M tokens
S1 (TPOT<=50ms, bs=64)  low    in 0.062  out 0.244  ->  blended 0.087
S1                      high   in 0.143  out 0.561  ->  blended 0.199
S1                      res1y  in 0.045  out 0.176  ->  blended 0.063
S4 (bs=111)             low    in 0.062  out 0.192  ->  blended 0.074
S4                      high   in 0.143  out 0.441  ->  blended 0.169
S4                      res1y  in 0.045  out 0.138  ->  blended 0.053
```

### 4.4 Effect of speculative decoding and prefix caching on cost

```
TABLE 9 - speculation at S1 (ctx 4,608). S+D counts state slots, so feasibility moves too.
method        bs    S+D   TPOT/user   agg tok/s   $/1M out low   $/1M out high
none           8      5     17.70 ms      451.9          1.106           2.547
none          32      5     23.49 ms     1362.5          0.367           0.845
none          64      5     31.20 ms     2051.5          0.244           0.561
MTP g=5        8     10      8.41 ms      951.3          0.526           1.210
MTP g=5       32     10     10.72 ms     2984.1          0.168           0.386
MTP g=5       64     10     19.39 ms     3300.8          0.151           0.349
DFlash2 g=7    8     13      9.02 ms      886.7          0.564           1.298
DFlash2 g=7   32     13     11.91 ms     2687.8          0.186           0.428
DFlash2 g=7   64      -            infeasible (KV) at S+D=13
```

**Speculation is worth up to 38 % of the output-token cost at bs=64** ($0.244 → $0.151 at the
`low` tier, MTP γ=5 at its *assumed* accept 2.5) — but the measured γ=5 acceptance on this card
was **2.32 at bs=1 and produced 23 tok/s**, so treat this whole table as an upper bound and
⚠️ **TO BE VERIFIED**: no acceptance-vs-batch curve exists for this model on any GPU
([architecture.md §7.4](./architecture.md)). METHODOLOGY's crossover estimate says speculation
stops paying around **bs ≈ 100 at 8 K / bs ≈ 25 at 32 K** on FP8 weights — which lands squarely
inside the feasible range, so at S2 you should expect it to *hurt*.

```
prefix caching: $/1M input by hit rate (NVFP4, low tier)
S1 h=0.0  8,046 tok/s  $0.0621      S2 h=0.0  5,839  $0.0856      S3 h=0.0  3,009  $0.1662
S1 h=0.5 15,668 tok/s  $0.0319      S2 h=0.5 10,095  $0.0495      S3 h=0.5  4,549  $0.1099
S1 h=0.9 76,727 tok/s  $0.0065      S2 h=0.9 45,540  $0.0110      S3 h=0.9 19,026  $0.0263
```

At S1 a 90 % hit rate cuts input cost **9.5×**; at S3 only **6.3×**, because the quadratic
attention over the full context survives the cache. Note the second-order cost: prefix caching
is what forces `S = 5`, which **halves** the 8 K concurrency (91 → 174 with `--disable-radix-cache`),
and lower concurrency raises the *output* cost. On this pair the trade is worth it for agentic
traffic (`preserve_thinking: true` makes each turn a strict prefix extension) and is **not** worth
it for one-shot traffic with no prefix overlap, where vLLM's APC was measured at **−36.7 %
throughput** on zero-overlap prompts ([serving-optimizations.md §1.2](../../cross-cutting/serving-optimizations.md), 2024-era software, ⚠️).

### 4.5 Versus the vendor API, and break-even

From [architecture.md §11](./architecture.md): **Qwen Cloud list $0.50 in / $3.00 out /
$0.05 explicit-cache read**; the real market (OpenRouter fp8 cluster) is **$0.20–0.30 in /
$2.20–2.55 out**, with Darkbloom's fp4 tier as low as **$0.10 / $1.80**.

```
TABLE 10 - margin and break-even
S1 low    $/1M out 0.244  ->  12.3x Qwen Cloud's $3.00,  10.5x OpenRouter's $2.55
S1 high   $/1M out 0.561  ->   5.3x                    ,   4.5x
S1 res1y  $/1M out 0.176  ->  17.0x                    ,  14.5x

Blended (75/25, 50% cached), self-hosted vs Qwen Cloud's own blended $0.956/1M:
  low    $0.087/1M  ->  break-even at  9.05 % GPU utilisation
  high   $0.199/1M  ->  break-even at 20.84 % GPU utilisation
  res1y  $0.063/1M  ->  break-even at  6.54 % GPU utilisation
```

**Break-even utilisation is the number to quote.** A single Nebius RTX PRO 6000 at $1.80/h pays
for itself against Qwen Cloud's list price if it is busy **9 % of the time** at the S1 operating
point; on Hyperstack reserved, **6.5 %**; even at AWS's $4.143 it is **21 %**. Against the
cheapest third-party API (Darkbloom fp4, $1.80/1M out) the output-side margin at `low` is still
**7.4×** and break-even is 13.6 %.

Two honest deductions from that headline:

- **Derate for the SE bandwidth question.** At MBU 0.65 the `low` blended cost rises to ~$0.104
  and break-even to ~10.9 %. Still trivially favourable.
- **Derate for reality.** [architecture.md §10.2](./architecture.md) found the same roofline
  over-predicts by ~30 % on the one point where a measurement existed (an RTX 5090). Here the
  opposite is true — the measurements **beat** the roofline at bs=1 — but they are all bs=1 and
  all possibly Workstation silicon. A 30 % derate puts the `low` S1 output cost at **$0.35/1M**
  and break-even at **12 %**. The conclusion survives any derate you care to apply.

For comparison across this repo: [architecture.md §11.3](./architecture.md) priced this card at
**$0.412/1M output at bs=64** and called bs=128 infeasible. This document lands at **$0.244**
because (a) it uses `W_decode` rather than the full checkpoint, (b) it uses the calibrated
MBU 0.80 rather than 0.60, and (c) it uses the bf16 GDN state, which makes bs=64 comfortable
rather than marginal. Both documents agree bs=128 at 8 K is infeasible at `S = 5`.

---

## 5. Scaling and deployment shape

### 5.1 The shape

**One GPU, one replica, one process. Scale horizontally.** This is the clearest case in the repo:
the model fits at 21.92 GB on a 96 GB card, TP over PCIe costs ~85 % on this hardware, and
`num_key_value_heads = 4` means TP > 4 stops saving KV memory anyway. A load balancer in front of
N single-GPU replicas beats every alternative. The measured precedent on this card — SGLang
`dp=4` on a 35 B model reaching **3,190 tok/s at 16 concurrent** vs TP4's collapse to 6–7 tok/s —
is unambiguous ([gpus/rtx6000-pro.md §4, §10c](../../gpus/rtx6000-pro.md)).

**When would TP ever pay here?** Only to buy KV capacity you cannot get another way: a single
1 M-context sequence needs 34.75 GB of KV+state, which fits one card at NVFP4, but **two or more
concurrent 1 M sequences need TP2**. That is the entire case. And even then, replication with a
shorter per-replica context window is usually the better answer.

**PD disaggregation and wide-EP: never, for this pair.** No experts (no EP). vLLM's
`compatible_strategies` is `["single_node_tp"]` and no P/D layout is published
([architecture.md §8.1](./architecture.md)); PD is measured **−20–30 % on small or untuned
deployments** and only pays above ~1,000 GPUs ([serving-optimizations.md §4.3](../../cross-cutting/serving-optimizations.md)); and this card has PCIe
only, which is the worst possible KV-transfer fabric. ⚠️ Whether the GDN recurrent state survives
a NIXL handover at all is unverified.

**Before you benchmark anything multi-GPU:** disable **IOMMU and ACS in BIOS**, or NCCL hangs on
the first collective on this card ([gpus/rtx6000-pro.md §11 gotcha 10](../../gpus/rtx6000-pro.md)).

### 5.2 Launch commands, quoted from engine docs

**vLLM — RTX PRO 6000, TP1** [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B/hw/rtx_pro_6000.json)
(quoted via [inference-engines.md §6.4](../../cross-cutting/inference-engines.md)):

```bash
VLLM_USE_RUST_FRONTEND=1 vllm serve Qwen/Qwen3.8-27B \
  --max-num-seqs 8 --tensor-parallel-size 1 \
  --enable-auto-tool-choice --tool-call-parser qwen3_xml \
  --reasoning-parser qwen3 --mm-encoder-tp-mode data
# + optional: --speculative-config '{"method":"mtp","num_speculative_tokens":5}'
```

The `nvfp4_nvidia` variant declares `supported_hardware: ["rtx_pro_6000","dgx_spark_gb10"]` —
**disjoint from the Inferact variant's list**, i.e. `nvidia/Qwen3.8-27B-NVFP4` is the NVFP4 build
vLLM targets *at this card specifically*
([inference-engines.md §6.4](../../cross-cutting/inference-engines.md)). NVIDIA's own serve line
for it [src](https://huggingface.co/nvidia/Qwen3.8-27B-NVFP4):

```bash
vllm serve nvidia/Qwen3.8-27B-NVFP4 --port 8000 --kv-cache-dtype fp8_e4m3
```

Note NVIDIA pairs NVFP4 weights with **FP8** KV, not NVFP4 KV — even they do not reach for FP4 KV
here ([quantization-formats.md §9.4](../../cross-cutting/quantization-formats.md)).

**SGLang — RTX PRO 6000 (SM120), all five checkpoints**
[src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md) (quoted via
[inference-engines.md §3.8](../../cross-cutting/inference-engines.md)):

```bash
sglang serve --trust-remote-code --model-path <checkpoint> \
  --kv-cache-dtype fp8_e4m3 --mem-fraction-static 0.85 \
  --attention-backend flashinfer --chunked-prefill-size 2048 \
  --reasoning-parser qwen3 --tool-call-parser qwen3_coder \
  --host 0.0.0.0 --port 30000
```

**Recommended production line for this pair** (this document's synthesis; every flag traceable to
one of the two recipes above or to a numbered section here):

```bash
sglang serve --trust-remote-code \
  --model-path nvidia/Qwen3.8-27B-NVFP4 \
  --kv-cache-dtype fp8_e4m3 \            # §2: NVIDIA export ships no kv_cache_scheme; pin it
  --mamba-ssm-dtype bfloat16 \           # §1.3: 58 -> 91 concurrent at 8K
  --mem-fraction-static 0.85 \
  --attention-backend flashinfer \       # §2: trtllm_mha is decode-only on SM120
  --chunked-prefill-size 2048 \          # §2: 8K chunks stall decode ~600 ms
  --max-running-requests 64 \            # §3.2 S1 operating point; 111 for max throughput
  --reasoning-parser qwen3 \             # §6: non-optional in practice
  --tool-call-parser qwen3_coder
# text-only traffic: add vLLM's --language-model-only equivalent (+0.92 GB KV budget)
# long context: SGLANG_ALLOW_OVERWRITE_LONGER_CONTEXT_LEN=1 + the YaRN --json-model-override-args
#   from architecture.md §8.5, and set factor to match your real context, not 4.0
```

**Toolchain preconditions that are failure modes, not warnings**
([gpus/rtx6000-pro.md §1, §11](../../gpus/rtx6000-pro.md)): CUDA **13.2+** if the host glibc is
≥ 2.41 (a C23 `rsqrt` `noexcept` mismatch kills *all* JIT kernel compilation below 13.2);
`TRITON_PTXAS_PATH` pointed at the system `ptxas`; the `sgl-kernel` **cu130** wheel; FlashInfer
newer than 0.6.15.post1 if you want MTP under the FlashInfer backend; and never
`VLLM_TEST_FORCE_FP8_MARLIN=1` (CUDA driver errors in spawned workers — use `--moe-backend marlin`
if you ever need it, which on a dense model you do not).

### 5.3 Warm-up and loading

```
NVMe load time at 5 / 10 / 20 GB/s
  BF16    55.56 GB ->  11.1 s /  5.6 s /  2.8 s
  FP8     30.87 GB ->   6.2 s /  3.1 s /  1.5 s
  NVFP4   21.92 GB ->   4.4 s /  2.2 s /  1.1 s
  INT4    19.45 GB ->   3.9 s /  1.9 s /  1.0 s
```

**Do not plan against those numbers.** Two measurements say the real figure is dominated by
everything except NVMe:

- SGLang reports the BF16 checkpoint takes **~6.5 minutes** to load its 18 shards from NVMe on a
  DGX Spark, and advises budgeting **~10 minutes to READY** before calling a boot hung
  ([architecture.md §1.1](./architecture.md)). The shard layout is awkward — alternating
  ~3.98 GB / ~2.11 GB from shard 6 on, one full-attention block plus three GDN blocks per pair.
- On this card specifically, HF discussion #160 measures a **34.8 s cold start** (first request
  after boot) on top of load, for the BF16 + EAGLE configuration.

Add CUDA-graph capture and, on any build that JIT-compiles FlashInfer kernels, a first-call
compile cost. **Budget 2–10 minutes to first token on a cold replica** depending on checkpoint
and whether kernels are cached; NVFP4 at 21.92 GB is the fastest of the four to load and is
another reason to prefer it. Keep a warm pool; do not autoscale this on a 30-second horizon.

---

## 6. Risks and open questions

### 6.1 The five that would change a decision

1. **⚠️ Server Edition vs Workstation Edition bandwidth — the biggest one.** Every published
   Qwen3.8-27B measurement on "RTX PRO 6000" is consistent with **1,792 GB/s** silicon, and HF
   discussion #160 says so explicitly. The Server Edition is **1,597 GB/s** — 11 % less, and
   decode here is bandwidth-bound by ~450× (ridge point 1,202 FLOP/byte at FP4 vs an arithmetic
   intensity of 2.67). No marketplace listing distinguishes the SKUs; Vast.ai's *Server Edition*
   page itself prints "1.79 TB/s". **Settle it:** `nvidia-smi -q -d CLOCK` plus a streaming-read
   microbenchmark before accepting any rented card. HelixML independently confirms the practical
   consequence: the Server Edition's memory clock is **locked at `[0,0]`**, so the ~1.2× memory
   overclock behind the viral 454 tok/s figure is unreachable on SE silicon.
2. **⚠️ Long-context prefill MFU is 2.7× better than planned.** Millstone measures 70.0 s TTFT at
   256 K (FP8, Qwen3.6-27B) against this document's 187 s `est.` — implying MFU_attn ≈ 0.6 on the
   FlashInfer FA2 path at head_dim 256, well above [gpus/rtx6000-pro.md §5e](../../gpus/rtx6000-pro.md)'s
   0.20–0.35 band. Every S2/S3 TTFT and `$/1M input` figure here is pessimistic by up to 2.7×.
   **Settle it:** time a single 128 K prefill on the actual card and back out MFU from
   [architecture.md §6.2](./architecture.md).
3. **⚠️ vLLM's `num_speculative_tokens: 5` override for this card is contradicted by the only
   user sweep on it.** The recipe's `hardware_overrides` says 5; HF FP8 discussion #9 measured
   **23 tok/s at γ=5 (acceptance 2.32)** and **62.2 at γ=2**. On a 2.7× throughput swing, the
   default is the wrong starting point. **Settle it:** sweep γ ∈ {1,2,3,5} reading
   `vllm:spec_decode_num_{accepted,draft}_tokens_total` — *"throughput alone cannot distinguish a
   working drafter from one that loaded and was ignored."*
4. **⚠️ vLLM's state-slot count `S` is unpublished.** SGLang publishes 5/4/3/1 by strategy; vLLM
   publishes none, and `--mamba-cache-mode align` plus `--enable-mamba-fine-grained-prefix-cache`
   both imply > 1. **Every fit cell and every feasibility verdict in §1 and §3 is engine-specific
   until this is known.** Millstone's measured 29-concurrent ceiling at 8 K on vLLM is the only
   empirical bound. **Settle it:** boot at a fixed `--max-num-seqs`, read the reported mamba-cache
   allocation from the startup log, divide by 78.45 MB.
5. **⚠️ The NVFP4 "real kernel" claim is transferred from an RTX 5090, not measured on this card.**
   vLLM's statement that it selects `FlashInferCutlassNvFp4LinearKernel` on sm120 appears in the
   recipe's **2× RTX 5090** section. Same SM version, different board, and no NVFP4 GEMM
   measurement is published on the RTX PRO 6000 itself
   ([inference-engines.md §4](../../cross-cutting/inference-engines.md)). The failure mode is
   silent: two known traps (`flashinfer-python`/`flashinfer-cubin` version drift, and a stale
   system `nvcc` when `CUDA_HOME` is unset) route SM120 users onto Marlin W4A16 with **no warning**
   — you keep the memory win and lose ~40 % of prefill. **Settle it:** read the boot log for the
   selected linear kernel, and A/B prefill tok/s against the FP8 checkpoint.

### 6.2 Open, lower-stakes

6. **⚠️ Which GDN prefill path runs.** vLLM's gate admits family 120 with `head_k_dim == 128`
   (✅ this model) and CUDA ≥ 13; SGLang's fast path is documented as SM100/SM103 only. The two
   documents disagree ([flash-attention.md §9.5](../../cross-cutting/flash-attention.md) vs
   [architecture.md §8.4](./architecture.md)). Worth ~20–25 % of prefill.
7. **⚠️ Whether `--chunked-prefill-size 2048` is still right at 96 GB.** The guidance comes from
   32 GB-card experience; a 4 K or 8 K chunk might be affordable here, and the GLM-5.3 sweep in
   [serving-optimizations.md §4.1](../../cross-cutting/serving-optimizations.md) shows
   16384 → 2048 costing **64 % of throughput at c=128**. This may be the largest untested lever
   on the card.
8. **⚠️ ReplaySSM (`--use-replayssm`) gain is unmeasured for this model.** It targets the state
   write-back that is 24 % of the decode step at bs=128/8 K.
9. **⚠️ No NVFP4/INT4 KV path.** Would roughly double 8 K concurrency. FlashInfer's SM120 NVFP4-KV
   kernel (PR #4955, merged 2026-09-07) is sparse-MLA-only and does not apply to GQA.
10. **⚠️ Tool-call parser conflict, unresolved.** vLLM pins `qwen3_xml`, SGLang pins
    `qwen3_coder`, for the same chat template ([architecture.md §1.4](./architecture.md)).
11. **⚠️ ViT chunking at maximum resolution.** An unchunked 65,536-patch score matrix is 8.6 GB,
    and this card's **99 KB shared-memory ceiling** turns a Hopper-tuned ViT kernel into a launch
    failure rather than a slowdown ([gpus/rtx6000-pro.md §1, §11 gotcha 6](../../gpus/rtx6000-pro.md)).
    Video at the card-recommended 224 K-token setting is a far larger encoder job than anything
    the LM does and is entirely untested on sm_120.
12. **⚠️ TensorRT-LLM on this pair.** `Qwen 3.5/3.6 + NVFP4 ❌ (GDN plugin pending, #11674)`, and
    KV-cache reuse is **No** for hybrid recurrent models without an explicit snapshot policy.
    Treat TRT-LLM as unavailable here.
13. **⚠️ Passive cooling and a narrow qualification list.** The Server Edition is passively cooled
    and Lenovo qualifies only 2× at 600 W or 4× at the 450 W cap in the SR675 V3; a published
    4×600 W run failed a thermal health gate at 92 °C. For decode-bound serving the 300 W Max-Q is
    within 0.3 % of the 600 W part on Workstation silicon — **cap to 450 W unless your workload is
    prefill-heavy.**
14. **⚠️ Supply.** Card list price rose **+87 % in 18 months** on GDDR7 scarcity ($8,565 → $16,000);
    Vast.ai's Server Edition page showed "No current offers" on 2026-09-19; Lambda, Scaleway and
    Azure do not carry the SKU; and the part is export-**"Controlled"**. The cost case in §4 is
    excellent and the procurement case is the constraint.

### 6.3 Accuracy caveats of the executed precision

- **NVFP4 on this model has no published accuracy table.** `nvidia/Qwen3.8-27B-NVFP4` lists the
  benchmark *names* (GPQA Diamond, Terminal-Bench, AA-LCR, MMMU-Pro, SciCode, IFBench) **without
  scores** ([quantization-formats.md §9.4](../../cross-cutting/quantization-formats.md)). What
  does exist is SGLang's full-1319-question GSM8K sweep across all 16 overlay combinations on this
  card: **94.01–95.00 %**, inside the 93.18–95.15 % band across all 202 cells
  ([architecture.md §9.3](./architecture.md)). That is a real accuracy gate for this card, but it
  is one benchmark.
- **The nearest published quantisation evals are INT4, not NVFP4**: RedHatAI INT4 recovers
  98.5–101.1 % of BF16 across six benchmarks; AMD Quark AWQ INT4 recovers 97.7–100.9 % on GSM8K
  and 95.6 % on Wikitext perplexity ([architecture.md §9.3](./architecture.md)).
- **bf16 GDN state is an accuracy knob, not just a memory one.** SGLang calls it *"an accuracy
  gate"* and shows it is not a one-way speed trade either (NVFP4+EAGLE: fp32 152.9 vs bf16 144.5
  tok/s/user; FP8+EAGLE: fp32 106.3 vs bf16 116.1) — *"measure both for your quantization."*
  This document's §1 recommends bf16 state for the +57 % concurrency; validate it on your task.
- **FP8 KV** is universally recommended and measured at ~9 % decode cost at 82 K depth (HelixML).
  There is no accuracy evaluation of FP8 KV on this model specifically.
- **`--enable-linear-replayssm-spec` with an explicit non-fp32 `--mamba-ssm-dtype`** logs a
  state-drift warning at boot; SGLang's bf16+EAGLE cells run with that warning and account for it
  in their validation ([architecture.md §8.6](./architecture.md)).

**⚠️ count for this document: 14 numbered open items in §6, plus the in-table markers in §2
(NVFP4-on-6000 provenance, FP8 block-scaling on TRT-LLM, NVFP4 KV, GDN prefill path, PD/NIXL,
ReplaySSM, ViT chunking, MTP FlashInfer version) and the §1 marker on vLLM's `S`.**

---

## Sources

**Repo documents (inputs, already fact-checked)**
- [research/METHODOLOGY.md](../../METHODOLOGY.md) §1–§4, §6, §8
- [research/models/qwen3827b/architecture.md](./architecture.md) §1.1, §1.4, §3.2, §4, §5.1–§5.6, §6.1–§6.4, §7.2–§7.4, §8.1–§8.6, §9.2–§9.3, §10.1–§10.2, §11.1–§11.3
- [research/gpus/rtx6000-pro.md](../../gpus/rtx6000-pro.md) §1–§5, §6, §6a, §7, §8a–§8d, §9a–§9g, §10a–§10d, §11, §12
- [research/cross-cutting/flash-attention.md](../../cross-cutting/flash-attention.md) §2, §9.3–§9.5
- [research/cross-cutting/quantization-formats.md](../../cross-cutting/quantization-formats.md) §9.4, §9.5, §9.6 (notes d, e, k, l)
- [research/cross-cutting/inference-engines.md](../../cross-cutting/inference-engines.md) §2.1–§2.3, §3.8, §4, §6.4, §7.1–§7.2, §8
- [research/cross-cutting/serving-optimizations.md](../../cross-cutting/serving-optimizations.md) §1.2, §1.3, §1.5, §2.3, §2.5–§2.7, §4.1–§4.4, §5.3, §5.4, §7.3
- [research/cross-cutting/cloud-pricing.md](../../cross-cutting/cloud-pricing.md) §5.10, §5.14, §9

**Engine and vendor primary**
- https://recipes.vllm.ai/Qwen/Qwen3.8-27B
- https://recipes.vllm.ai/Qwen/Qwen3.8-27B.json
- https://recipes.vllm.ai/Qwen/Qwen3.8-27B/hw/rtx_pro_6000.json
- https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md
- https://docs.sglang.io/advanced_features/attention_backend.html
- https://huggingface.co/nvidia/Qwen3.8-27B-NVFP4
- https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/README.md
- https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/
- https://lenovopress.lenovo.com/lp2263-thinksystem-nvidia-rtx-pro-6000-blackwell-server-edition-pcie-gen5-gpu

**Measurements fetched for this document (2026-09-19)**
- https://huggingface.co/Qwen/Qwen3.8-27B/discussions/160 — BF16 + SGLang + EAGLE on RTX PRO 6000: 77–80 tok/s, accept 2.5–3.45, KV pool 356,241 tokens, cold start 34.8 s
- https://huggingface.co/Qwen/Qwen3.8-27B-FP8/discussions/9 — FP8 + vLLM: 46.8 (no spec) / 56.3 (γ=1) / **62.2 (γ=2)** / 59.6 (γ=3) / 23 (γ=5, acceptance 2.32) tok/s
- https://huggingface.co/Qwen/Qwen3.8-27B/discussions/101 — 260 tok/s p50, TTFT 156 ms, custom engine ⚠️
- https://helix.ml/blog/chasing-454-toks-qwen38-rtx-pro-6000 — **Server Edition**: 149.8 → 167.8 (torch.compile) → 335 best cell; memory clock locked `[0,0]`; FP8 KV is capacity not speed (~9 % cost at 82 K)
- https://www.vaditaslim.com/blog/ai/qwen3.8-27b-two-rigs — vLLM 0.20.1 BF16 85 tok/s TTFT 110–160 ms; vLLM 0.27.1 NVFP4 **150 tok/s, TTFT 63 ms**
- https://github.com/MiaAI-Lab/Qwen3.8-27B-RTX-6000-PRO-SGLang-DSpark — SGLang + DFlash2 on 1× RTX PRO 6000: **240+ tok/s**, KV pool ~1.7 M tokens, full launch flags
- https://www.millstoneai.com/inference-benchmark/qwen3-6-27b-fp8-1x-rtx-pro-6000-blackwell — **proxy** Qwen3.6-27B FP8, 1× RTX PRO 6000: 46.1 tok/s/user @1 K, ITL 22 ms, TTFT 170 ms @1 K / 70.0 s @256 K, 29 concurrent @8 K, peak 189.3 tok/s @c=5
- https://github.com/alangeb/sglang-rtxpro6000 · https://github.com/jpezzulli/sglang-rtxpro6000 — SM120 SGLang runtime builds for this checkpoint (524 K context, HiCache/NIXL) ⚠️ not independently verified
- https://forums.developer.nvidia.com/t/optimized-qwen3-8-flash-next-on-1x-rtx-pro-6000-171-tok-s-524k-and-hicache-nixl-persistence/381722 — ⚠️ a *different* checkpoint (Qwen3.8 Flash-Next), listed only so the next pass does not mistake it for this pair

---

## Audit log (2026-09-19)

Numerical audit, `python3`, against [`METHODOLOGY.md`](../../METHODOLOGY.md), the cited sections
of [`architecture.md`](./architecture.md) §3–6 and [`gpus/rtx6000-pro.md`](../../gpus/rtx6000-pro.md)
§2, §3, §8, §9, and this document's own §1.1 inputs.

**Recomputed and verified exact (or within the stated `python3` rounding) against the printed
values — no changes needed:**

- Table 1 (weights/GPU, KV budget/GPU, aggregate KV, all 4 formats × {1,2,4,8} GPUs), using the
  vision-tower/norms replication rule stated in the table header.
- Table 2 (max concurrency, all 4 formats × 12 `(n, S, state dtype)` rows × 8 context/KV-dtype
  cells = 384 cells) via `max_concurrency(ctx) = floor(kv_budget/GPU / (ctx×kv_B/min(n,4) +
  S×state_B/n))`. 383/384 cells matched exactly; one cell (NVFP4, n=4, S=1, bf16, FP8KV, 8K)
  printed 878 against a recomputed 879 — a sub-1-count floating-point rounding artifact, not a
  >5% error, left as printed.
- Table 3 (per-sequence bytes at 6 context points × 4 KV/state combinations) — all 24 values
  match exactly, confirming the table consistently uses `ctx = 133,072` for its "128 K" row.
  **Note (not a >5% error, not fixed):** this is 48 tokens short of the S3 scenario's actual
  `131,072 + 2,048 = 133,120` (METHODOLOGY §6, used correctly in Tables 4/5's S3 rows). The
  discrepancy moves every affected cell by ≤0.04%.
- Tables 4 and 5 (throughput/latency grid, NVFP4 and FP8, 3 scenarios × 6 batch sizes each) via
  the stated `bytes_per_decode_step` → `decode_step_time = max(bandwidth term, compute term)` →
  `TPOT`/`tok/s` roofline, cross-checked against Table 2's `max_concurrency` for every
  `infeasible (KV)` cell. All 36 values (bytes/step, TPOT, tok/s/GPU, agg tok/s, max conc) match.
- Prefill/TTFT: recomputed `prefill_s(T, h)` for all 4 weight formats × 5 context points × `h ∈
  {0, 0.9}` from the stated GEMM+attention formula and the §3.1 effective-peak table. All TTFT
  cells in Tables 4–5, the §3.2 prose figures (8,046/5,830/2,973/2,630 tok/s at T=4,096; 143 s at
  262 K; 1,925 s at 1 M), and the prefix-caching table's 9 tok/s and $/1M-input cells (§4.4) match
  to the last printed digit.
- Table 11 (S4 max-throughput point and S1 SLO-sensitivity sweep at MBU 0.80/0.65/0.60, including
  the FP8/MBU-0.60 case where the SLO-feasible batch drops from 64 to 32) — all values match.
- Table 7 (cost per 1M output and input tokens) via `cost = n_gpus × price / (tok/s × 3600) ×
  1e6` at all three price tiers ($1.80 / $4.143 / $1.30, verified against
  `cross-cutting/cloud-pricing.md` §5.10/§5.14/§9 — no neighbouring-GPU row used) — all 21 values
  match.
- Table 8 (blended cost) and Table 10 (margin/break-even, recomputed from the unrounded Table 7
  inputs rather than their 3-decimal printed values) — all values match to within the stated
  rounding, including the `~9.05%` / `~20.84%` / `~6.54%` break-even figures that only reconcile
  once the unrounded blended cost is used.
- Table 9's `TPOT`/`agg tok/s` → `$/1M out` arithmetic (given the table's own `agg tok/s`
  inputs) — matches for every row.
- The §3.1 MBU-calibration table (5 published bs=1 measurements inverted to an implied HBM
  bandwidth) — recomputed `BW = bytes/step ÷ (accept_length / measured tok/s)` for the three
  speculative rows and `BW = bytes/step ÷ (1000/measured tok/s)` for the two non-speculative
  rows; all 5 implied-bandwidth and MBU values match within ≤0.5%.
- §0's verdict numbers (bs=64/TPOT 31 ms/2,052 tok/s/$0.24–0.56; bs=111/TPOT 43 ms/2,610
  tok/s/$0.19–0.44; blended $0.087–0.199) all trace to Tables 7/8/11 exactly.
- Spot-checked unit handling throughout: usable HBM (86.40 GB = 80.47 GiB), weight bytes, and KV
  bytes are consistently GB (10⁹); no GB/GiB conflation found. Dense (not sparse) TFLOPS are used
  throughout (480/960/1,920 Server Edition dense), matching METHODOLOGY §8 and
  `gpus/rtx6000-pro.md` §3c. Prices matched to `cloud-pricing.md` with no cross-GPU substitution.

**Fixed:**

1. **Table 9, `S+D` column, DFlash2 rows (arithmetic inconsistency, ~8% off the document's own
   stated input).** The table printed `S+D = 12` for all three DFlash2 rows and the
   `infeasible (KV)` annotation. This document states `D = 8` for DFlash2 explicitly in three
   other places — §2's optimization table ("Costs `D = 8` extra state slots"), §3.5 ("`D = 8` for
   DFlash2"), and §4.4 ("at `S + D = 13` the 8 K NVFP4 concurrency ceiling falls from 91 to
   ~43") — so with `S = 5` the correct value is `S + D = 13`, not 12. Changed `12 → 13` in all
   three occurrences (Table 9's three DFlash2 rows). Recomputed `max_concurrency` at `S = 13` (NVFP4, n=1,
   ctx=4,608, bf16 state, FP8 KV) = 51, vs. 55 at the erroneous `S = 12` — both exceed the
   printed rows' batch sizes (8, 32), so no feasibility verdict or `TPOT`/`agg tok/s`/cost value
   in the table changes; only the mislabelled slot count was wrong.

**Reviewed, within the 5% tolerance, not changed:**

- The §2 optimization table's aside "150 tok/s ... vs `~59` `est.` without" — the document's own
  computed NVFP4/bs=1/no-spec baseline elsewhere (§3.2 Table 4, §3.4) is 62.4 tok/s, ~5.4% above
  the `~59`. Both figures are explicitly hedged (`~`, `est.`) and the `~59` is not clearly tied to
  the same context (the 150 tok/s measurement is at 256 K, not S1's 4,608); left as is rather than
  overwritten with an unverified substitute.
- The §3.1 "Effective mixed GEMM peak" for NVFP4 (1,525 TFLOPS `est.`, from stated shares
  0.636/0.269/0.095). Recomputing the shares from the decode-active-parameter breakdown in
  `architecture.md` §3 (NVFP4-quantised 18.384 B / FP8 7.214 B / BF16 remainder 1.298 B, all as a
  fraction of the 26.896 B decode-active total) gives 0.684/0.268/0.048 and a re-weighted peak of
  1,593 TFLOPS — a 4.3% difference, under the fix threshold. It is also inert: the compute term of
  `decode_step_time` is never the binding `max()` term at any batch size in this document (it is
  2–3 orders of magnitude below the bandwidth term throughout), so this input error, even
  uncorrected, changes no printed TPOT, throughput, or cost figure.

**Contradictions with foundation docs:** none found. Every disagreement this document carries
(TFLOPS 480/960/1,920 vs. `flash-attention.md`'s 500/1,000/2,000; GDN prefill-path support between
vLLM and SGLang; the $1.80-vs-$1.85 price-tier pick; SE-vs-WS bandwidth on the published
measurements) is one the document already states explicitly with both sides cited, per
METHODOLOGY §8 — none of those is silently resolved one way. No new disagreement with
`METHODOLOGY.md`, `architecture.md` §3–6, or `gpus/rtx6000-pro.md` §2/§3/§8/§9 was found.
