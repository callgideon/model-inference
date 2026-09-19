# Methodology — GPU sizing, throughput and cost model

Every document under `research/` uses these formulas so numbers are comparable
across GPUs and models. Research date: **2026-09-19**. Anything not backed by a
cited source is marked **⚠️ TO BE VERIFIED** with the estimation method stated.

## Legend

| Marker | Meaning |
|---|---|
| `[src]` + URL | Sourced from a primary document (datasheet, model card, engine docs, vendor blog, MLPerf). |
| **⚠️ TO BE VERIFIED** | Best estimate; no primary source found, or sources conflict. Method stated inline. |
| `est.` | Derived from formulas below, not measured. |
| `meas.` | Published measurement; source cited. |

Dense vs. sparse TFLOPS are always stated separately. Vendor marketing sheets
often quote sparse; we plan with **dense**.

## 1. Weight memory

```
weights_bytes = Σ_over_tensors (n_elements × bytes_per_element) + scale/metadata overhead
```

Bytes per parameter (corrected 2026-09-19 after fact-check; earlier drafts of
this file understated FP4 overheads):

| Format | bytes / param | overhead | scale layout |
|---|---|---|---|
| BF16 / FP16 | 2.0 | — | — |
| FP8 E4M3, 128×128 2-D tile scale (DeepSeek-V3 style) | 1.0002 | +0.02 % | one FP32 per 16,384 elems |
| FP8 E4M3, 32×32 tile, UE8M0 scale (DeepSeek-V4.1) | 1.001 | +0.1 % | one byte per 1,024 elems |
| MXFP8 | 1.03125 | +3.1 % | one E8M0 per 32 |
| **NVFP4** | **0.5625** | **+12.5 %** | E2M1 + one FP8 E4M3 per 16 elems, + one FP32 per tensor |
| **MXFP4** | **0.53125** | **+6.25 %** | E2M1 + one E8M0 per 32 elems |
| INT4 W4A16 (AWQ/GPTQ g128) | 0.5195 | +3.9 % | FP16 scale + 4-bit zero per 128 (0.53 if the zero-point is stored FP16) |

Mixed-precision checkpoints: compute bytes **per tensor group** (routed experts,
attention, shared experts, dense MLPs, embeddings / lm_head, Engram tables,
MTP / DSpark heads) with that group's own format, then sum. Never apply one flat
bytes/param to a mixed checkpoint. The checkpoint's `model.safetensors.index.json`
`total_size` (bytes) is the ground truth to reconcile against; the
`quantization_config.ignore` list says which groups stay BF16/FP8.

Units: do all memory arithmetic in bytes; report GiB (2^30) unless a column is
explicitly labelled GB (10^9). Vendor HBM figures are used **as deployed** (the
GPU doc's §2 usable-device-memory figure from nvidia-smi / the cloud instance
spec), not physical stack capacity — see §8 for the pinned values.

MoE: `total_params = attention + shared_experts + n_routed × expert_params + embeddings + lm_head + extras`;
`active_params = attention + shared + top_k × expert_params + embeddings/lm_head (per token) + extras`.
Show the arithmetic from `config.json`, then compare to the model card claim.

## 2. KV cache and per-sequence state

Per token, per layer:

| Attention type | bytes / token / layer |
|---|---|
| MHA / GQA | `2 × n_kv_heads × head_dim × B` |
| MLA (DeepSeek-V3 style) | `(kv_lora_rank + qk_rope_head_dim) × B` (latent + rope key cached; no separate V) |
| Sliding window (W) | as above but capped at `W` tokens per sequence |
| Cross-layer KV sharing | only *source* layers hold cache; sharing layers = 0 |
| KV compression ratio r | divide by `r` for compressed layers |
| Linear attention / DeltaNet / KDA / Mamba | **0 per token**; instead a fixed per-sequence state `n_heads × d_k × d_v × B_state` (+ conv state `n_heads × d × kernel × B`) |

`B` = bytes per KV element: BF16 2, FP8 1, NVFP4/INT4 0.5 (only where the
engine supports quantized KV on that GPU).

```
kv_bytes_per_token = Σ_layers bytes(layer)
fixed_state_per_seq = S × (recurrent state + conv state + sliding-window ring buffers)
kv_total(ctx, n_seq) = n_seq × (ctx × kv_bytes_per_token + fixed_state_per_seq)
```

`S` = number of state slots the engine allocates per request for hybrid /
linear-attention models (SGLang allocates 5 for Kimi-K3 → 2.25 GB per request;
vLLM's Mamba-style cache allocates ≥ 1 plus speculative slots). Take `S` from
the engine doc cited in the model's architecture.md §5 or mark it ⚠️. The dtype
of ring buffers / recurrent state is engine-specific (fp32 vs bf16 vs fp8) —
state which one and mark the alternative.

Report `kv_bytes_per_token` at BF16 and FP8, the fixed state per sequence, and
totals at 8K / 32K / 128K / 1M context.

## 3. Fit

```
usable_hbm      = hbm_capacity × 0.90                 (CUDA context, fragmentation, workspace)
per_gpu_weights = weights_bytes / n_gpus  (TP/EP)     (+ replicated non-sharded tensors; note them)
activation_ws   ≈ 2–6 GB per GPU at 8K ctx, more with long prefill chunks and CUDA graphs
kv_budget       = usable_hbm − per_gpu_weights − activation_ws
max_concurrency(ctx) = floor( kv_budget × n_gpus / (ctx × kv_bytes_per_token + fixed_state) )
```

Minimum GPUs = smallest `n_gpus` (from the set {1,2,4,8,16,32,72…} that the
node/rack topology allows) where `kv_budget ≥ 0` and at least one request at
the target context fits. State the parallelism (TP / PP / EP / DP, attention-DP)
and whether it needs multi-node (and which fabric).

Consistency rule: a throughput or cost table may only contain batch /
concurrency rows that are ≤ `max_concurrency(ctx)` at that GPU count. Larger
rows are printed as `infeasible (KV)`, never as numbers. Decode KV-read bytes
must scale with `batch × ctx × kv_bytes_per_token` (a flat per-step constant is
a bug the fact-check found twice).

## 4. Throughput and latency (roofline)

Decode is memory-bandwidth bound at low/moderate batch; prefill is compute bound.

```
bytes_per_decode_step(batch) = weights_read(batch) + Σ_seq kv_read(seq_ctx)
  dense model:  weights_read = all weights (regardless of batch)
  MoE:          weights_read ≈ attn + shared + (distinct experts hit) × expert_bytes
                distinct_experts(batch) ≈ n_experts × (1 − (1 − top_k/n_experts)^batch)   (uniform-routing estimate)
decode_step_time ≈ max( bytes_per_decode_step / (HBM_BW × MBU),
                        2 × active_params × batch / (peak_FLOPS × MFU) ) + comm_overhead
tokens_per_s_per_replica = batch / decode_step_time
TPOT (time per output token) = decode_step_time
```

`pairs.json`'s `output_tokens_per_s_per_gpu` is this decode-only rate; a sustained
prefill+decode rate, where a document computes one, goes in a separate
`sustained_output_tokens_per_s_per_gpu` field and is never compared against a
decode-only cell.

```
prefill_time(T tokens) ≈ 2 × active_params × T / (peak_FLOPS(dtype) × MFU)
                        + attention_flops(T) / (peak_FLOPS × MFU_attn)
  attention_flops(T) = Σ_full_attn_layers 4 × n_q_heads × head_dim × T² (dense causal ~½ of that)
                       (sliding window: 4 × n_q_heads × head_dim × T × W; sparse top-k: × T × k)
TTFT ≈ prefill_time(prompt − cached_prefix) + queueing + first decode step
```

Planning defaults (state if you deviate): MBU 0.6–0.8 for decode on H100/H200,
0.5–0.7 on Blackwell first-gen software, 0.4–0.6 on MI355X/ROCm; MFU 0.35–0.5 for
BF16 prefill, 0.25–0.4 for FP8, 0.2–0.35 for FP4; comm overhead ~5–15% for TP=8
over NVLink, 20–40% for multi-node TP over InfiniBand. Use published
measurements (MLPerf, InferenceMAX, vendor blogs) in preference to these defaults
whenever they exist, and cite them.

Report a table at batch/concurrency ∈ {1, 8, 32, 64, 128, 256} × context ∈
{4K in/512 out, 32K in/1K out, 128K in/2K out}: tokens/s per GPU, TPOT, TTFT.

## 5. Optimizations to evaluate per (model, GPU)

1. **Attention kernel** — FA2 / FA3 / FA4 / FlashInfer / FlashMLA / TRT-LLM /
   AITER-CK (AMD); which one actually runs on this GPU for this model's
   attention type, its achieved TFLOPS, and whether sparse/indexer attention
   (DeepSeek DSA) has a kernel on this GPU.
2. **Weight quantization** — native checkpoint dtype vs. what the GPU accelerates
   (BF16 / FP8 / NVFP4 / MXFP4 / INT8 / INT4-W4A16 via Marlin/Machete). Speedup
   vs. BF16, accuracy delta (cite evals), engine flags.
3. **KV cache quantization** — FP8 / NVFP4 KV, effect on max concurrency.
4. **Prefix caching** — hit-rate scenarios 0 / 50 / 90 %; effect on TTFT and
   prefill cost; KV offload to host/NVMe (LMCache, Dynamo KV manager).
5. **Speculative decoding** — MTP / nextn / DSpark / EAGLE; acceptance rate,
   effective tokens per step, when it stops helping (large batch).
6. **Parallelism** — TP / EP / DP-attention / PP; wide-EP (DeepEP, EPLB) on
   NVL72; disaggregated prefill/decode (Dynamo, SGLang PD).
7. **CUDA graphs / torch.compile / chunked prefill / continuous batching** — assume on.

## 6. Cost

```
cost_per_hour            = n_gpus × price_per_gpu_hour (on-demand, reserved, spot — table each)
cost_per_1M_output_tokens = cost_per_hour / (aggregate_output_tokens_per_s × 3600) × 1e6
cost_per_1M_input_tokens  = cost_per_hour / (aggregate_prefill_tokens_per_s × 3600) × 1e6
blended_cost(in:out ratio, cache hit rate) = weighted by tokens
```

Cached input tokens: for **our own serving cost** a cache hit skips prefill
compute and costs only the KV load / reuse — assume 10 % of the uncached prefill
cost unless measured, and say so. For **vendor API comparisons** use the
vendor's published cached-input ratio (DeepSeek ≈ 2.1 %, Anthropic ≈ 2.5 %,
Alibaba 10–25 %; see research/cross-cutting/serving-optimizations.md §1.5),
never a generic 10 %.

Standard scenarios (so matrices can be built across docs):

| id | prompt / output | SLO | use |
|---|---|---|---|
| S1 | 4K in / 512 out | TPOT ≤ 50 ms | interactive |
| S2 | 32K in / 1K out | TPOT ≤ 50 ms | long-context interactive |
| S3 | 128K in / 2K out | none | very long context |
| S4 | 4K in / 512 out | none | max-throughput batch |
| blended | 75 % input (50 % cached) / 25 % output | S1 operating point | $/1M blended |

Report cost at the concurrency that hits an SLO of TPOT ≤ 50 ms (interactive)
and at max-throughput (batch-oriented), for each GPU-hour price tier. Compare
to the model vendor's public API price per 1M tokens as a sanity check.

## 7. What must never happen

- No invented benchmark numbers. Estimates are labelled `est.` with the formula.
- No silent sparse-TFLOPS. No silent PCIe-vs-SXM mixing.
- If a model is not supported by an engine on a GPU as of 2026-09-19, say so;
  do not assume it will be.

## 8. Pinned inputs (converged values from the fact-checked docs)

Use these unless the cited doc's verification log says otherwise; if a document
disagrees, state the disagreement and cite both. All TFLOPS are **dense**.

### GPUs

| GPU (slug) | HBM / GPU as deployed | HBM BW | BF16 | FP8 | FP4 | notes |
|---|---|---|---|---|---|---|
| H100 SXM5 (`h100`) | 80 GB | 3.35 TB/s | 989.5 | 1,979 | none | no FP4/FP6/MX hardware; NVFP4/MXFP4 checkpoints run only as Marlin W4A16 (memory win, not speed) — [gpus/h100.md](gpus/h100.md) |
| H200 SXM (`h200`) | 141 GB | 4.8 TB/s | 989.5 | 1,979 | none | same die as H100 — [gpus/h200.md](gpus/h200.md) |
| B200 HGX (`b200`) | 180 GB (not 192) | 7.7 TB/s (SKU sheet; NVIDIA "up to 8") | 2,250 | 4,500 | 9,000 | sm_100; vLLM PR #56686 reports 183 GB (`nvidia-smi` MiB mislabelled); 180 GB stays the planning basis, see [gpus/b200.md §2](gpus/b200.md) |
| B300 HGX / DGX / AWS p6-b300 (`b300`) | 268 GB (2,144 GB per 8-GPU node; DGX B300 262.5) — **not 288** | 8.0 TB/s | 2,250 | 4,500 | 13,500 | sm_103; 1.5× B200 is FP4-only, BF16/FP8 unchanged; INT8 ≈ dead — [gpus/b300.md](gpus/b300.md) |
| GB300 NVL72, per B300 (`gb300`) | 288 GB (≈ 279 usable) | 8.0 TB/s | 2,500 | 5,000 | 15,000 | higher-clocked than HGX B300; 72-GPU NVLink domain — [gpus/gb300.md](gpus/gb300.md) |
| A100 SXM4 80 GB (`a100`) | 80 GB | 2.04 TB/s | 312 | none (INT8 624 TOPS) | none | sm_80; FP8 checkpoints run W8A16 only; FP4 checkpoints do not run. **Decimal-GB basis** (`usable_hbm` = 0.90 × 80 GB = 72.0 GB = 67.06 GiB/GPU, 576 GB per 8-GPU node); `nvidia-smi`'s 81,920 MiB (80 GiB = 85.90 GB) is an **unsourced binary reading, ⚠️ TO BE VERIFIED, not the planning basis** — resolved 2026-09-19, see [gpus/a100.md §2](gpus/a100.md) — [gpus/a100.md](gpus/a100.md) |
| RTX PRO 6000 Blackwell Server (`rtx6000-pro`) | 96 GB GDDR7 | 1.6 TB/s (Server Ed. 1,597 GB/s; Workstation 1,792) | see doc §3 | see doc §3 | ≈ 2,000 (4,000 sparse) | sm_120: no FA3/FA4/tcgen05; NVFP4 MoE grouped-GEMM broken → Marlin W4A16 (FlashInfer PR #2898 may have closed the MXFP4 path — ⚠️, see quantization-formats.md §9.6); whether the product-page FP4 figure is dense or sparse is unresolved ⚠️ (quantization-formats.md open question 1); PCIe only — [gpus/rtx6000-pro.md](gpus/rtx6000-pro.md) |
| MI355X (`mi355x`) | 288 GB | 8.0 TB/s | 2,500 | 5,000 | 10,100 (MXFP4 and MXFP6) | gfx950; NVFP4 not native (SGLang requants to MXFP4, vLLM dequants); BF16/FP8 from the ROCm CDNA4 page, 10.1 PF FP4/FP6 from AMD's product page (10.0 on ROCm page, 10,066 measured by SemiAnalysis); amd.com was unreachable for WebFetch on research day, so the AMD brochure remains ⚠️ — [gpus/mi355x.md](gpus/mi355x.md) |

### Models

| Model (exp) | params | checkpoint bytes | active params | KV / token | fixed state / seq | notes |
|---|---|---|---|---|---|---|
| DeepSeek-V4.1-Flash (`deepseek41f`) | 763.2 B total = 552.4 B backbone + 196.6 B Engram tables + 14.2 B DSpark | 510.29 GB (index.json; HF usedStorage 510.31 GB incl. non-safetensors) | 7.89 B prefill / 16.11 B decode | **890 B/token ONLY on sm_100/sm_103** (`nvfp4_ds_mla` → `FLASHMLA_MEGA_ATTN_DSV41`, `capability.major == 10`) — i.e. B200/B300/GB300; **1,650 B/token FP8 elsewhere**; **3,200 B/token BF16 on H100 under vLLM's shipped SM90 profile** (measured, [models/deepseek41f/h100.md §1.4](models/deepseek41f/h100.md)). The 890 B layout itself is architectural and byte-exact (720 main + 170 indexer, [cross-cutting/flash-attention.md §11](cross-cutting/flash-attention.md), [models/deepseek41f/architecture.md §5.1](models/deepseek41f/architecture.md)); what is Blackwell-gated is the *kernel* that executes it ([cross-cutting/quantization-formats.md §9.1](cross-cutting/quantization-formats.md): *"890 B/token is the FP4-KV figure and it presumes the Blackwell FP4-KV kernel"*). ⚠️ FlashInfer PR #4955 opened an SM120 NVFP4 sparse-MLA path that vLLM's dtype gate still refuses — re-check per engine version | sliding-window ring **2,906,112 B = 2.77 MiB** (43 rings, MTP on; 2,703,360 B = 2.58 MiB with MTP off) at FP8; 5.38 MiB at the BF16 reference impl ⚠️ | experts are **MXFP4** (E8M0/32), non-expert FP8 32×32 UE8M0; Engram = ~183 GiB random-gather tables — [models/deepseek41f/architecture.md](models/deepseek41f/architecture.md) |
| DeepSeek-V4.1-Flash-NVFP4 (`deepseek41fnvfp4`) | same | 527.27 GB (491 GiB; HF usedStorage 527.31 GB) — **larger** than the base | same | **890 B/token ONLY on sm_100/sm_103** (`nvfp4_ds_mla` → `FLASHMLA_MEGA_ATTN_DSV41`, `major == 10`); **1,650 B/token FP8 elsewhere**; **3,200 B/token BF16 on H100 under vLLM's shipped SM90 profile** (measured, [models/deepseek41f/h100.md §1.4](models/deepseek41f/h100.md)) — identical KV layout to the base, same kernel gate ([cross-cutting/flash-attention.md §11](cross-cutting/flash-attention.md), [cross-cutting/quantization-formats.md §9.1](cross-cutting/quantization-formats.md)). ⚠️ FlashInfer PR #4955 opened an SM120 NVFP4 sparse-MLA path that vLLM's dtype gate still refuses — re-check per engine version | same | only 58 % of bytes are NVFP4 (routed experts, gs16); Engram stays FP8 (188.8 GiB); accuracy-neutral, no published speedup vs base ⚠️ — [models/deepseek41fnvfp4/architecture.md](models/deepseek41fnvfp4/architecture.md) |
| Qwen3.8-27B (`qwen3827b`) | 27.78 B (LM 26.90 B) | BF16 55.56 GB / FP8 30.87 / NVFP4 21.92 / INT4 19.45 | 27.78 B (dense) | 64 KiB BF16 / 32 KiB FP8 (16 full-attention layers only) | GDN state 78.4 MB (bf16) per slot × S = 5 (SGLang default) = 392.2 MB per request; alternates: 153.9 MB fp32 per slot, and S = 1 only with `--disable-radix-cache`; vLLM's S is ⚠️ unpublished | 48 linear-attention layers — [models/qwen3827b/architecture.md](models/qwen3827b/architecture.md) |
| Kimi-K3 (`kimik3`) | 2,779.9 B total | 1,560.9 GB on disk | 104.19 B | MLA 13.5 KiB/token FP8 (24 of 93 layers) | KDA 428.6 MiB per slot × S=5 (SGLang) = 2.25 GB per request | 97.9 % MXFP4 by bytes but 46.7 % of active params → FP4 speedup capped ≈ 1.89×; fits 8×B300 (2,144 GB) at 195 GB/GPU — [models/kimik3/architecture.md](models/kimik3/architecture.md) |
| Marlin-2B (`marlin2b`) | 2.21 B unique (2.72 B on disk, tied embedding duplicated) | 5.444 GB BF16 | 2.21 B | 12 KiB/token BF16 (6 GQA layers) | GDN 18.63 MiB per seq | no MTP weights shipped; video: 2 fps, ≤ 240 frames, 200,704 px/frame — [models/marlin2b/architecture.md](models/marlin2b/architecture.md) |

*Pin log — 2026-09-19 (gap `C7-qwen38-gdn-state-slots`): the Qwen3.8-27B fixed-state cell was resolved from "153.9 MB fp32 **or** 78.4 MB bf16 per slot, no `S`" to `S = 5 × 78,446,592 B (bf16) = 392.2 MB per request` — SGLang's shipped default ([models/qwen3827b/architecture.md](models/qwen3827b/architecture.md) §5.4, `--mamba-radix-cache-strategy extra_buffer`; `config.json`'s `mamba_ssm_dtype: float32` is a runtime declaration SGLang overrides, §1.2) and the same convention this table already pins for Kimi-K3's KDA state; recut downstream in [gpus/h100.md](gpus/h100.md), [h200](gpus/h200.md), [a100](gpus/a100.md), [b200](gpus/b200.md), [gb300](gpus/gb300.md), [rtx6000-pro](gpus/rtx6000-pro.md), [mi355x](gpus/mi355x.md) and [matrix/fit-matrix.md](matrix/fit-matrix.md).*

*Pin log — 2026-09-19 (gap `C5-890b-fp4-kv-kernel-gate`): both DeepSeek-V4.1-Flash rows' KV/token cell was resolved from "890 B/token … so it is **not** Blackwell-only" to a kernel-gated pin. The 890 B layout is **architectural and byte-exact** (720 main + 170 indexer — [cross-cutting/flash-attention.md](cross-cutting/flash-attention.md) §11, [models/deepseek41f/architecture.md](models/deepseek41f/architecture.md) §5.1), but it is **executed only where vLLM's `nvfp4_ds_mla` resolves**, i.e. `FLASHMLA_MEGA_ATTN_DSV41` gated on `capability.major == 10` → sm_100/sm_103 (B200, B300, GB300). Everywhere else budget the same layout at **FP8 1,650 B/token**; on H100 under vLLM's shipped SM90 profile the **measured** KV pool implies **BF16 3,200 B/token** ([models/deepseek41f/h100.md](models/deepseek41f/h100.md) §1.4 — the only empirical determination in the tree, and it outranks the roofline). The deleted clause rested on the DeepSeek report's *storage-not-FLOPs* argument, which is true of the **format** and says nothing about which **kernel** ships; [cross-cutting/quantization-formats.md](cross-cutting/quantization-formats.md) §9.1 states the same ("890 B/token is the FP4-KV figure and it presumes the Blackwell FP4-KV kernel"). Recut downstream in [models/deepseek41fnvfp4/h100.md](models/deepseek41fnvfp4/h100.md), [gpus/h100.md](gpus/h100.md) §2, [gpus/h200.md](gpus/h200.md) §2, [matrix/fit-matrix.md](matrix/fit-matrix.md) §1/§6.3, [matrix/gpu-optimizations.md](matrix/gpu-optimizations.md) §4.3, [matrix/pairs.json](matrix/pairs.json) and [README.md](README.md) open questions 10/15.*

### Benchmarks

InferenceX / InferenceMAX rows are re-fetchable via the API described in
[cross-cutting/inferencex-api.md](cross-cutting/inferencex-api.md): the `model`
parameter is the display name (e.g. `DeepSeek-V4.1-Flash`, `Kimi-K3`), and
`hardware` is not a filter (filter client-side). No A100 rows exist there; the
RTX PRO 6000 has Qwen3.5 rows only; Qwen3.8-27B returns 0 rows.

### Prices

Use [cross-cutting/cloud-pricing.md](cross-cutting/cloud-pricing.md) rows only
— never a neighbouring GPU's row. OCI is the only hyperscaler with public
on-demand B300 ($15), GB300 ($18) and MI355X ($8.60) prices; hyperscalers run
2.2–2.4× neocloud rates for identical silicon.

---

## Resolution log

- **2026-09-19 — B200 HBM, GB vs GiB: CLOSED at 180 GB decimal.** The §8 B200 row now records that vLLM PR #56686 reports 183 GB (an `nvidia-smi` MiB total mislabelled as GB) and that 180 GB stays the planning basis; see [gpus/b200.md §2](gpus/b200.md). Downstream: [models/deepseek41f/b200.md](models/deepseek41f/b200.md) §1.4/§6.1, [models/deepseek41fnvfp4/b200.md](models/deepseek41fnvfp4/b200.md) §1.1/§6.3, [models/deepseek41fnvfp4/README.md](models/deepseek41fnvfp4/README.md) item 9 and `matrix/pairs.json` updated. No numeric value in this tree changes.
- **2026-09-19 — A100 HBM, GB vs GiB (Gap C1): CLOSED at 80 GB decimal.** `usable_hbm` = 0.90 × 80 GB = **72.0 GB = 67.06 GiB/GPU** (576 GB per 8-GPU node) is the single planning basis; `nvidia-smi`'s 81,920 MiB (80 GiB = 85.90 GB) is an unsourced binary reading kept only as a labelled ⚠️ **TO BE VERIFIED** +7.4 % sensitivity — see [gpus/a100.md §2](gpus/a100.md). Downstream: [gpus/a100.md](gpus/a100.md) §2/§9/§11, [models/marlin2b/a100.md](models/marlin2b/a100.md) §1.1/§1.3/§6 and [its README](models/marlin2b/README.md), the stale "unresolved" boxes in [qwen3827b/a100.md](models/qwen3827b/a100.md) §1.0, [kimik3/a100.md](models/kimik3/a100.md) §1.0, [deepseek41f/a100.md](models/deepseek41f/a100.md) §1 and [deepseek41fnvfp4/a100.md](models/deepseek41fnvfp4/a100.md) §1.1, plus `matrix/pairs.json` and [matrix/fit-matrix.md](matrix/fit-matrix.md). Marlin-2B on A100 moves 562 → **517** seats at 8K.
