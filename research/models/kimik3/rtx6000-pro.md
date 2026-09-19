# moonshotai/Kimi-K3 on NVIDIA RTX PRO 6000 Blackwell Server Edition 96GB GDDR7 (PCIe only, no NVLink; sm_120)

> Research date **2026-09-19**. Formulas, legend and rules per
> [`research/METHODOLOGY.md`](../../METHODOLOGY.md). Pinned inputs from
> METHODOLOGY §8. Dense TFLOPS only. Server Edition (1,597 GB/s) and Workstation
> Edition (1,792 GB/s) are kept separate throughout — the distinction is an 11 %
> TPOT swing and **every published measurement of this pair was taken on
> Workstation Edition silicon**.
>
> **The headline correction this document makes to the rest of the repo.**
> [`gpus/rtx6000-pro.md` §9g](../../gpus/rtx6000-pro.md) says *"Kimi-K3 does not
> fit an 8-card box, and it is not close … at least 19 cards (topology step:
> 32)"*, and
> [`cross-cutting/inference-engines.md` §3.8](../../cross-cutting/inference-engines.md)
> and [`cross-cutting/quantization-formats.md` §9.6](../../cross-cutting/quantization-formats.md)
> both record Kimi-K3 on sm_120 as **unsupported / not a listed platform**.
> All three statements are correct about *vendor* support and remain true on
> 2026-09-19 (re-probed below). They are **incomplete** about the field: a
> qualified, receipted, reproducible **16 × RTX PRO 6000 Blackwell** deployment
> of the official `moonshotai/Kimi-K3` MXFP4 checkpoint exists, has been serving
> production traffic, and publishes machine-readable throughput receipts. It
> gets under 96 GB/GPU by requantising the dense projections to **MXFP8 online
> at load**, which the repo's arithmetic did not model. §1 and §6 state the
> disagreement in full.

---

## 0. Verdict

1. **Runnable today — but not on any vendor stack.** vLLM `0.29.0` and SGLang `0.5.20` both refuse: `https://recipes.vllm.ai/moonshotai/Kimi-K3/hw/rtx_pro_6000.json` → **HTTP 404** (re-probed 2026-09-19), `meta.hardware` = `{h200,b200,b300,gb200,gb300,mi355x,ascend_910c}`, SGLang `supportedHardware` = `[b300,gb300,b200,gb200,h200,h100,mi350x,mi355x,a3]` — no `rtx6000`, zero occurrences of `sm120` in the cookbook payload. What runs is the **community `local-inference-lab` fork** (vLLM tree `6e843eb` + B12X `2d466e3`, CUDA 13.3 / PyTorch 2.13 / FlashInfer 0.6.18, image `voipmonitor/vllm@sha256:32ff8027…`, [receipt r38](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/validation/kimi-k3-upstream-aligned-r38-20260918.json)).
2. **Plan 32 cards under the repo convention; 16 only with the community MXFP8 overlay (unverified).**
   *(**Resolved 2026-09-19**, gap `X7-kimik3-rtx6000-pro-16-vs-19-32-gpus`; this item previously read "Minimum 16 GPUs, recommended 16".)*
   METHODOLOGY §3's 0.90-usable / 4 GB-workspace convention puts the native
   checkpoint at `ceil(1,560,860,324,864 / (86.40e9 − 4e9))` = **19 cards** →
   first legal topology step **32** ([gpus/rtx6000-pro.md §9g](../../gpus/rtx6000-pro.md)).
   **32 is the planning figure**, because METHODOLOGY §7 forbids sizing on an
   engine path that is not supported as of 2026-09-19 and the 16-card fit rests
   on an **unverified online MXFP8 weight-only overlay from an unaffiliated
   community fork**, not a vendor recipe (§1.2, §6.1–§6.2). Two labelled
   alternatives are carried, not promoted: **16 cards** TP16 + DCP16 in one
   chassis behind 4 × PCIe Gen5 switches — the receipted community deployment,
   every table below it intact — and **8 cards** with the community 2-bit
   `lukealonso/Kimi-K3-QSRT-K2` requant (757.53 GB), at concurrency 1. Be clear
   about what 32 costs: it is two chassis with **no GPU fabric between them**
   (§5.1), so the 32-card shape is an arithmetic planning figure, not a
   qualified deployment either — on this card Kimi-K3 has no shape that is both
   convention-compliant and demonstrated.
3. **Weight format actually executed: MXFP4 → W4A16 dequant-in-kernel** (B12X SM12x fused MoE, `B12X_W4A16_*` flags; upstream vLLM lands on Marlin W4A16 for the same reason), **plus an online MXFP8 weight-only overlay on the KDA/MLA `q/k/v/b/f_a` projections and the vision tower** — without that overlay 1,560.86 GB ÷ 16 = 97.55 GB/GPU does not fit a 96 GB card at all. **No native FP4 tensor-core MoE path.**
4. **Cost at the 32-card planning figure** (§3.7, all `est.`, $1.80 Nebius –
   $4.143 AWS per GPU-hour → **$57.60 – $132.58/h** for 32 cards):
   **Interactive (S1, TPOT ≤ 50 ms): $27.24 – $62.69 per 1M output tokens**
   (conc 29, 18.36 tok/s/GPU, TPOT 49.4 ms); **max-throughput (S4, conc 465 at
   the METHODOLOGY §3 KV cap): $5.10 – $11.73**, a roofline and not a forecast.
   Blended 75/25 at S1: **$7.70 – $17.72**.
   **The 16-card community-overlay case, retained:** S1 **$36.08 – $83.05**
   (conc 11, 13.86 tok/s/GPU), with DFlash speculation $18.32 – $42.16;
   S4 (conc 12) **$34.89 – $80.31**; blended **$9.91 – $22.81** (§4).
   Moonshot's own API is **$15.00/1M output** — self-hosting this pair loses to
   the API on list price at every published GPU-hour rate and at both card
   counts unless speculation is on *and* utilisation is high (§4).
5. **Confidence: `estimate`, anchored on `meas.`** — batch-1 decode (55.801 / 122.695 / 155.069 tok/s), prefill (3,861.7 tok/s @ 8K) and the 1.46 M-token KV pool are *measured with receipts* on 16 Workstation-Edition cards; everything at concurrency > 1 and everything at Server-Edition bandwidth is derived from a model calibrated on those points. **15 items are ⚠️ TO BE VERIFIED** (§6).

---

## 1. Fit

### 1.1 Inputs and the topology ladder

| Input | Value | Source |
|---|---|---|
| Capacity as deployed | **96 GB = 96 × 10⁹ B = 89.41 GiB** (not 96 GiB) | METHODOLOGY §8; [gpus/rtx6000-pro.md §2](../../gpus/rtx6000-pro.md) |
| `usable_hbm` (METHODOLOGY §3, 0.90) | 86.40 × 10⁹ B = 80.47 GiB | METHODOLOGY §3 |
| Activation workspace | 4 × 10⁹ B baseline | [gpus/rtx6000-pro.md §9d](../../gpus/rtx6000-pro.md) convention |
| Checkpoint | **1,560.86 GB** (experts MXFP4 1,446.46 + non-expert BF16/F32 114.40) | METHODOLOGY §8; [architecture.md §4](architecture.md) |
| MLA KV/token | 27,648 B BF16 · **13,824 B FP8** (24 of 93 layers × 576 elem) | [architecture.md §5.2](architecture.md) |
| KDA state/slot @ attn-TP 1 | **449,372,160 B = 428.6 MiB** fp32 (221.6 MiB bf16) | [architecture.md §5.3](architecture.md) |
| `S` slots/request | **5** SGLang `extra_buffer` default; **1** for vLLM's no-spec mamba cache ⚠️ | METHODOLOGY §8; [architecture.md §5.3](architecture.md) |
| Interconnect | PCIe Gen5 ×16, **no NVLink**; P2P 53–56 GB/s uni, NCCL all-reduce bus BW **37.6–41.7 GB/s** at 8 ranks | [gpus/rtx6000-pro.md §4](../../gpus/rtx6000-pro.md); [rtx6kpro PCIe bandwidth](https://github.com/local-inference-lab/rtx6kpro/blob/master/hardware/pcie-bandwidth.md) |

**The topology ladder is `{1, 2, 4, 8, 16, 32}`, and 24 is not on it.** Tensor
parallelism must divide `hidden_size = 7168 = 2¹⁰ × 7`, the 96 attention heads,
and the MoE latent width 3584. `7168 / 24` is not an integer, so TP24 is
excluded; TP12 divides 7168 only as 597.33 → also excluded for the standard
row/column split, although the community runtime states its *generic* B12X
collectives "support TP8 and TP12" for other shapes
([production-runtime](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/production-runtime.md)).
TP16 (6 heads/rank, 448 hidden/rank, 224 latent/rank) and TP32 (3 heads/rank)
are clean. This card has **no NVLink domain of any size**, so every count above
1 is PCIe TP.

### 1.2 The three weight variants, computed

```python
# METHODOLOGY §1, in bytes, per tensor group
experts   = 2_722_740_830_208 * 0.53125        # MXFP4 = 0.5 + 1 B E8M0 / 32
          = 1_446_456_066_048 B = 1446.46 GB
nonexpert = 57_179_884_544 * 2 + 11_122_432 * 4
          =   114_404_258_816 B =  114.40 GB
TOTAL     = 1_560_860_324_864 B = 1560.86 GB = 1453.66 GiB   ✔ matches METHODOLOGY §8
```

| Variant | What changes | Total | /16 GPUs | /32 GPUs |
|---|---|---:|---:|---:|
| **MXFP4 native, as shipped** | — | **1,560.86 GB** | **97.55 GB** ✗ | 48.78 GB |
| **+ MXFP8 dense overlay (field)** | KDA/MLA `q,k,v,b,f_a` (18.34 B params) + vision tower + mm_projector → MXFP8 @ 1.03125 B/param; saves **18.20 GB** | **1,542.66 GB** | **96.42 GB** ✗ | 48.21 GB |
| MXFP8 overlay ceiling (all non-expert) | every non-expert tensor → MXFP8; saves 55.39 GB | 1,505.47 GB | **94.09 GB** | 47.05 GB |
| **`lukealonso/Kimi-K3-QSRT-K2`** (community) | routed experts requantised to **2.00 bit/param**, non-expert linears MXFP8 | **757.53 GB** | 47.35 GB | 23.67 GB |

`est.` — the overlay rows are derived; the field runtime states only *which
tensors* it converts ("The server converts the target MLA `q_proj`, `k_proj`,
`v_proj`, `b_proj`, and `f_a_proj` tensors to MXFP8 during loading",
[redhat-dspark-dcp16](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/redhat-dspark-dcp16.md)),
not the resulting byte count. The QSRT-K2 row is measured: HF `usedStorage`
757,527,330,254 B with `safetensors.parameters` covering only the 57.19 B
non-expert params (F8_E4M3 40.35 B + BF16 16.83 B + F32 11.1 M), so the routed
experts occupy 757.53 − 75.3 = **682.2 GB for 2.7227 T params = 2.00 bit/param**
([HF API](https://huggingface.co/api/models/lukealonso/Kimi-K3-QSRT-K2?expand[]=safetensors&expand[]=usedStorage)).

**Read the ✗ marks.** At 16 GPUs the native checkpoint needs 97.55 GB per card
against a 96 GB card — it does not fit **before any KV cache, at 100 %
occupancy**. The field-observed overlay gets to 96.42 GB, which still does not
fit. Only pushing the overlay further — or accepting that vLLM's `InstantTensor`
loader plus `expandable_segments:True` reclaims enough — closes the last
~0.5–2.5 GB. The community receipt reports
**`model_memory_gib_per_rank = 90.42`**
([r38](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/validation/kimi-k3-upstream-aligned-r38-20260918.json));
90.42 GiB = **97.09 GB**, which *also* exceeds the 96 GB nameplate, so that
field number's unit is internally inconsistent — ⚠️ **TO BE VERIFIED** (§6).
Planning value (**revised 2026-09-19, gap `X7`**): **plan 32 cards** — the
topology step above METHODOLOGY §3's 19-card floor (§0.2, §3.7). **16 cards is
the field-demonstrated floor of the community overlay path only**, and reaching
it assumes the non-expert tensors are *all* requantised, i.e. the 94.09 GB/GPU
row — which is one rung beyond what the fork's own documentation claims it
converts (⚠️ §6.2).

### 1.3 Fit table (METHODOLOGY §3)

`kv_budget = 0.90 × 96e9 − weights/n − 4e9`, in bytes, per GPU.

| n | Parallelism | Weights/GPU (native) | Weights/GPU (MXFP8 overlay) | Act. ws | KV budget/GPU | Multi-node? |
|---:|---|---:|---:|---:|---:|---|
| 1 | — | 1,560.86 GB | 1,542.66 GB | 4 GB | **−1,478.5 GB** | — |
| 2 | TP2 | 780.43 | 771.33 | 4 | **−698.0** | no |
| 4 | TP4 | 390.22 | 385.66 | 4 | **−307.8** | no |
| 8 | TP8 (+DCP8) | 195.11 | 192.83 | 4 | **−112.7** | no |
| 16 | **TP16 + DCP16** | 97.55 | 96.42 | 4 | **−15.2** | no (1 chassis, 4 switches) |
| 32 | TP32 + DCP32 | 48.78 | 48.21 | 4 | **+33.6** | **yes** — 2 chassis, **no GPU fabric** |
| 64 | TP64 | 24.39 | 24.10 | 4 | +58.0 | yes, 4+ chassis |

```
min_gpus (METHODOLOGY §3) = ceil(1_560_860_324_864 / (86.40e9 − 4e9)) = ceil(18.94) = 19
                          -> first allowed topology step = 32
raw-capacity floor (96e9 B/GPU, zero KV, zero workspace):
   native        1560.86 / 96 = 16.26  -> 17 GPUs
   overlay A     1542.66 / 96 = 16.07  -> 17 GPUs
   overlay ceil  1505.47 / 96 = 15.68  -> 16 GPUs
   QSRT-K2 2-bit  757.53 / 96 =  7.89  ->  8 GPUs
```

**That `16` in the overlay-ceiling row is the whole story of this pair.** The
native checkpoint cannot be served on 16 of these cards. The checkpoint with
every non-expert tensor dropped to MXFP8 can, with 1.9 GB/GPU left over, which
is exactly the band the field runtime allocates
(`KV_CACHE_MEMORY_BYTES=1325000000` research profile, `900000000` production).

### 1.4 Max concurrency

Aggregate cost per request (METHODOLOGY §2–§3), with DCP = n so the MLA KV is
position-sharded and the KDA state sharded by attention-TP width:

```
per_request_aggregate = ctx × kv_bytes_per_token  +  S × 449_372_160 B
   (the KDA term is n-independent in aggregate: n × state_per_slot(attnTp=n) = 449.37 MB)
max_concurrency(ctx) = floor( n × kv_budget_per_gpu / per_request_aggregate )
```

**MXFP4 native + MXFP8 overlay, S = 5 (SGLang):**

| n | aggregate KV budget | 8 K (BF16/FP8) | 32 K | 128 K | 1 M |
|---:|---:|---:|---:|---:|---:|
| 8 | **infeasible (weights)** | — | — | — | — |
| 16 | **infeasible (weights)** | — | — | — | — |
| 32 | 1,094.1 GB | **442 / 463** | 347 / 405 | 186 / 269 | 35 / 65 |
| 64 | 3,730.9 GB | 1,508 / 1,580 | 1,183 / 1,381 | 635 / 919 | 119 / 222 |

**`lukealonso/Kimi-K3-QSRT-K2` 2-bit, S = 5:**

| n | aggregate KV budget | 8 K (BF16/FP8) | 32 K | 128 K | 1 M |
|---:|---:|---:|---:|---:|---:|
| 8 | **infeasible (weights)** (−12.29 GB/GPU) | — | — | — | — |
| 12 | 231.3 GB | 93 / 97 | 73 / 85 | 39 / 56 | 7 / 13 |
| 16 | 560.9 GB | **226 / 237** | 177 / 207 | 95 / 138 | 17 / 33 |
| 32 | 1,879.3 GB | 759 / 796 | 596 / 696 | 320 / 463 | 60 / 112 |

**What the field actually admits, and why it is nothing like these numbers.**
The qualified 16-GPU profiles report a **physical FP8 KV pool of 950,000 –
1,460,937 tokens** and an engine admission cap of **1 sequence** (research
profiles) or **12 sequences** (the Frank2 production profile)
([source-locked receipt](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/validation/source-locked-runtime-20260816.json),
[r38](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/validation/kimi-k3-upstream-aligned-r38-20260918.json)).
1,460,937 tokens ÷ 8,192 = 178 sequences of *KV*, but the engine admits 12. The
binding constraint on this GPU is **not the KV cache** — it is CUDA-graph
capture pools, the B12X MoE scratch and the AttnRes prefill workspace competing
for the last ~2 GB per card. The failure mode is recorded verbatim in the same
receipt as an `unsupported` profile: *"CUDA Graph warmup exceeds available GPU
memory with `MAX_NUM_SEQS=8`, `MAX_NUM_BATCHED_TOKENS=4096`, and a 1.9 or
1.95 GB target KV allocation per GPU."*

So the honest concurrency ceilings for this pair are:

| Context | KV-token cap (1.46 M pool, DCP16) | Engine/workspace cap (measured) | **Binding** |
|---:|---:|---:|---:|
| 8 K | 178 | 12 | **12** |
| 32 K | 44 | 12 | **12** |
| 128 K | 11 | 12 | **11** |
| 1 M | 1 | 12 | **1** |

### 1.5 Tensors that cannot be sharded — state them explicitly

| Tensor / pool | Shards with | Does **not** shard with | Cost |
|---|---|---|---|
| **KDA recurrent + conv state** | attention-TP width only | DP, EP, DCP — SGLang: *"The KDA state pool is the concurrency ceiling — DP, EP, and DCP do not shard it"* ([architecture.md §5.3](architecture.md)) | 449.37 MB aggregate per slot; × S |
| **MLA KV (24 layers)** | DCP (position-sharded) | plain TP — **replicated on every TP rank without DCP** | 13,824 B/token FP8 aggregate under DCP16 → 864 B/token/GPU |
| **AttnRes rank-1 projections + all RMSNorm weights** | nothing — **fully replicated** | TP, EP, DCP | 4,021,248 params × 2 B = **8.04 MB/GPU** (negligible, but it is real) |
| **F32 router bias** `gate.e_score_correction_bias` | nothing | — | 82,432 × 4 B = 0.33 MB/GPU |
| **Speculative draft weights** | replicated | TP | **4.50 GB** (Inferact/RadixArk DSpark) or **9.49 GB** (RedHatAI) per rank |
| **Speculative draft KV** | replicated, DCP world size forced to 1 | DCP — vLLM PR #310 explicitly normalises "A replicated draft KV group executes with DCP world size 1 and rank 0" ([redhat-dspark-dcp16](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/redhat-dspark-dcp16.md)) | ~1,400 B/token/rank |
| **B12X MoE route workspace** | per-rank, caller-owned | — | **278.51 MiB/rank bounded** (1,014.51 MiB unbounded) at a 4,096-token chunk ([full-mxfp4-p4096-prefill](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/full-mxfp4-p4096-prefill.md)) |

On a 96 GB card with ≈2 GB of post-weight headroom, the 4.50 GB DSpark draft
**does not fit alongside the target at 16 GPUs without giving back KV** — and
the receipts show exactly that: the DSpark profile's KV pool is 1,016,293 tokens
against target-only's 1,058,823–1,460,937.

---

## 2. What runs on this GPU for this model

sm_120 is the *consumer/prosumer* Blackwell die (GB202). It has real
FP4/FP6/FP8 block-scaled tensor cores via `mma.sync.aligned.…block_scale`, but
**no `tcgen05` MMA and no TMEM**, and only **99 KB** of opt-in shared memory per
block against Hopper/B200's ~228 KB
([gpus/rtx6000-pro.md §1, §3](../../gpus/rtx6000-pro.md)). Every kernel written
against `tcgen05` — FA3, FA4, CUTLASS FMHA SM100, the whole trtllm-gen cubin
family — cannot run here at all.

| Optimization | Status | Kernel / flag | Expected effect |
|---|---|---|---|
| **MLA prefill (24 gated-MLA layers)** | **native** (community fork) / **native, unoptimised** (upstream) | B12X dense MLA + **FlashAttention-2 MLA prefill** (community); upstream falls to `TRITON_MLA` — `TOKENSPEED_MLA`, `CUTLASS_MLA`, `FLASHINFER_MLA` are all `major == 10` gated ([flash-attention.md §9.5, §16.2](../../cross-cutting/flash-attention.md)) | the vLLM Kimi-K3 recipe's `--attention-backend TOKENSPEED_MLA` is **10.x-only and FP8-KV-required**, so the shipped recipe is unusable verbatim here |
| **MLA decode** | **native** | B12X SM12x CuTe-DSL MLA + DCP all-to-all (community); `trtllm_mha` XQA is decode-only and SGLang says *"Optimized for SM90 and SM120"* ([flash-attention.md §9.5](../../cross-cutting/flash-attention.md)) | works; no FlashMLA anywhere on sm_120 |
| **FlashMLA / FA3 / FA4 / trtllm-gen FMHA** | **unsupported** | FlashMLA is SM90/SM100 only; FA3 is 9.x; vLLM refuses FA4 on 12.x; NVIDIA: *"no plan to SM120/121"* for trtllm-gen ([flash-attention.md §9.5](../../cross-cutting/flash-attention.md), [TRT-LLM #11799](https://github.com/NVIDIA/TensorRT-LLM/issues/11799)) | −; FA2-class Ampere MMA is the ceiling |
| **KDA prefill (69 linear layers)** | **native** | `fla.ops.kda.chunk_kda` via Triton; the community runtime pins **Triton KDA prefill** ([full-mxfp4-p4096-prefill](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/full-mxfp4-p4096-prefill.md)) | works; `pip install flash-linear-attention` is a hard dependency |
| **KDA decode — native fused CUDA** | **native** | `torch.ops._C.fused_kda_decode`, gate `is_device_capability_family(120)`; K3's geometry (96 heads, head_dim 128, conv 4, `num_spec == 0`) satisfies every condition ([flash-attention.md §14.4](../../cross-cutting/flash-attention.md)) | the one kernel where sm_120 is a **first-class** target |
| **KDA decode — FlashInfer `fused_kda_decode`** | **unsupported** | gate is `compute_capability in ((10,0),(10,3))` — **B200/B300 only** ([flash-attention.md §14.4](../../cross-cutting/flash-attention.md)) | loses the 1.33×/geomean-1.13× FlashInfer win B300 gets |
| **Sparse / DSA indexer attention** | **n/a for this model** | Kimi-K3 has no indexer tensors and no sparse-attention flags ([architecture.md §5.5](architecture.md)) | — |
| **Weight format: MXFP4 routed experts** | **emulated (dequant)** — W4A16 | community: B12X SM12x fused MoE with `B12X_W4A16_PREFILL_FUSED_SUM=1`, `B12X_W4A16_STABLE_ROUTE_PACK=1`; upstream vLLM: `get_mxfp4_backend()` matches only `is_device_capability_family(100)` → **Marlin W4A16** ([quantization-formats.md §9.6](../../cross-cutting/quantization-formats.md), [vLLM #31085](https://github.com/vllm-project/vllm/issues/31085)) | MXFP4 buys **capacity, not compute**; weights stay 4-bit in GDDR7, dequantised to BF16 inside the MMA |
| **Weight format: non-expert BF16 → MXFP8 online** | **native (silicon)**, **required (capacity)** | community loader converts KDA/MLA `q,k,v,b,f_a` + vision tower at load | the only reason 16 cards fit at all (§1.2) |
| **NVFP4 (`nvidia/` or `RedHatAI/Kimi-K3-NVFP4`)** | **⚠️ do not** | dense NVFP4 GEMM on sm_120 is fine, but **MoE grouped GEMM is the broken path** by default ([gpus/rtx6000-pro.md §6a](../../gpus/rtx6000-pro.md)); the NVFP4 builds are also **+49 to +85 GB larger** than MXFP4 and measure **slower on B300** ([architecture.md §4, §10.1](architecture.md)) | strictly worse on this card on every axis |
| **KV-cache quant: FP8 E4M3** | **native** | `--kv-cache-dtype fp8`; the field runtime's target KV is FP8 on all profiles | 27,648 → 13,824 B/token; the only KV dtype worth planning |
| **KV-cache quant: NVFP4 / INT4** | **unsupported** | no `QE4m3KvE2m1` FMHA variant for SM120/121; checkpoint carries `kv_cache_scheme: null`; no engine exposes 4-bit KV for this model ([architecture.md §5.2](architecture.md), [gpus/rtx6000-pro.md §5d](../../gpus/rtx6000-pro.md)) | — |
| **Prefix caching** | **native** | `ENABLE_PREFIX_CACHING=1`; vLLM's K3 hybrid retention (PR #45845 interval, #47782 Marconi-style) and `--prefix-match-unit 128` for MLA block alignment ([serving-optimizations.md §1.3](../../cross-cutting/serving-optimizations.md), [architecture.md §8.8](architecture.md)) | measured **9–25 % TTFT reduction** on a B300 node; **off by default for K3** in vLLM |
| **Host KV offload** | **native** | `HOST_KV_BACKEND=native KV_OFFLOADING_SIZE=32` — one shared 32 GiB mmap across all 16 ranks; **not** LMCache ([native-host-kv-offload](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/native-host-kv-offload.md)) | receipted at 1,221,083,136 CPU→GPU B for one restored prefix |
| **Speculative decoding — DSpark** | **native, measured** | `Inferact/Kimi-K3-DSpark`; **122.695 tok/s vs 55.801 = 2.20×** at batch 1, acceptance 0.4149, 3.905 emitted tokens/target cycle | best measured ×-factor after DFlash; costs 4.5 GB/rank + draft KV |
| **Speculative decoding — DFlash** | **native, measured** | `modal-labs/Kimi-K3-DFlash`; **155.069 tok/s = 2.78×**, acceptance 0.6188, 5.332 tokens/cycle; text-only | the single largest lever on this pair |
| **Speculative decoding — RedHatAI DSpark (BF16)** | **native, conditional** | requires vLLM PR #310 replicated-draft-KV DCP normalisation; without it greedy block acceptance silently collapses **20.7 % → 0.84 %**; **128 K+ context unsupported** in the qualified memory profile | a genuine silent-correctness trap |
| **Speculative decoding — EAGLE / MTP** | **n/a** | `num_nextn_predict_layers: 0` — no MTP head ships in the checkpoint ([architecture.md §7.1](architecture.md)) | — |
| **TP (tensor parallel)** | **native, expensive** | TP16 over PCIe Gen5; **vLLM's PCIe custom all-reduce is unavailable at world size 16**: *"Supported world sizes: [2, 4, 6, 8]"* ([16-GPU rig](https://github.com/local-inference-lab/rtx6kpro/blob/master/hardware/asrockrack-turin-cpayne-16gpu.md)) | 93 layers × 2 all-reduces × NCCL's ~24 µs small-message floor = **4.5 ms of pure latency per decode step** (§3) |
| **DCP (decode-context parallel)** | **native, mandatory** | `DCP_SIZE=16`; the only lever that shards the otherwise TP-replicated MLA KV | SGLang measures **+72 % concurrency ceiling at ~1.8× ITL** on B300; here it is what makes a 1 M-token pool possible |
| **EP (expert parallel)** | **⚠️ TO BE VERIFIED / avoid** | no EP in any qualified profile; SGLang's own warning is *"Don't use EP with an a2a backend: a2a buffers reclaim the KV that DCP buys"*, and all-to-all over PCIe is worse than all-reduce | — |
| **DP-attention** | **unsupported at this scale** | the model does not fit in one card, so there is nothing to replicate | — |
| **PP (pipeline parallel)** | **⚠️ not qualified** | the GPU doc's own measurement says TP2+PP2 beats TP4 by **7×** on PCIe for a 400 B model ([gpus/rtx6000-pro.md §4](../../gpus/rtx6000-pro.md)), and SGLang measures PP16×TP1 as the best *prefill* shape on GB200 ([architecture.md §10.2](architecture.md)) — but no K3 PP profile exists on this card, and DSpark requires `pp_size == 1` | the largest un-taken optimization for this pair (§6) |
| **PD disaggregation** | **⚠️ unsupported in practice** | needs a fast KV-transfer fabric; this card has PCIe only and no dedicated GPU fabric; [gpus/rtx6000-pro.md §7](../../gpus/rtx6000-pro.md) flags Dynamo as untested here | — |
| **CUDA graphs** | **native, and the binding constraint** | `--compilation-config '{"cudagraph_mode":"FULL_AND_PIECEWISE","cudagraph_capture_sizes":[1..12]}'` | capture pools are what caps concurrency at 12, not KV |
| **Multimodal encoder (MoonViT-V2, 401 M)** | **native** | `ENABLE_VISION=1`; ViT asks for `flash_attention_2`; encoder weights also go MXFP8 in the overlay | receipted: 4 images / 65,232 patches / 16,448 prompt tokens in **10.70 s**, HTTP 200, zero restarts. Vision OOM'd until vLLM #459 made the MoonViT RoPE products in-place (peak 236.25 → 118.125 MiB) |
| **Video input** | **unsupported** | *"The open-source K3 serving contract currently supports image input only — its processor rejects video and audio input"* ([architecture.md §2.6](architecture.md)) | — |

---

## 3. Throughput and latency

### 3.1 Assumptions, stated (METHODOLOGY §4)

Rather than quote a flat MBU, this section splits the decode step into a memory
term and a **collective term**, because on a 16-way PCIe TP group with no
NVLink the collective term is not a rounding error — it is a quarter of the step.

```python
decode_step(batch, ctx) = max( bytes_per_step / (n × BW × MBU),
                               2 × active_params × batch / (n × 480e12 × MFU_dec) )
                        + 93 layers × 2 all-reduce × max(24.1 µs, batch × 7168 × 2 B / 35e9)

bytes_per_step = distinct_experts(b) × 92 × 17.55 MB          # MoE, uniform-routing est.
               + 93.85 GB                                      # non-expert, MXFP8 overlay, minus embed
               + b × ctx × 13_824 B                            # FP8 MLA KV
               + b × 449_372_160 B                             # live KDA slot per sequence
distinct_experts(b) = 896 × (1 − (1 − 16/896)^b)
```

| Parameter | Value | Justification |
|---|---|---|
| `BW` | **1.597e12 B/s** (Server Edition) | METHODOLOGY §8. The measured rig is Workstation Edition at 1.792e12 — every estimate here is **11 % slower** than that rig by construction. |
| `MBU` | **0.312** `est.` — **calibrated**, not assumed | Back-solved from the measured TP16 batch-1 point (below). METHODOLOGY's band for first-gen Blackwell software is 0.5–0.7 and [gpus/rtx6000-pro.md §9b](../../gpus/rtx6000-pro.md) narrows it to 0.50–0.65 on sm_120; **neither is reachable for this model** — 93 sequential layers of small per-rank GEMMs under TP16 W4A16 dequant. |
| `MFU_dec` | 0.10 | Derived from the measured prefill MFU (0.103, below); decode is never compute-bound here anyway. |
| NCCL small-message latency | **24.1 µs** | measured, 8-rank NCCL on this card class ([rtx6kpro PCIe](https://github.com/local-inference-lab/rtx6kpro/blob/master/hardware/pcie-bandwidth.md): 256 B 24.6 µs, 1 KB 24.1 µs, 8 KB 24.2 µs) |
| All-reduce bus BW at 16 ranks | **35e9 B/s** `est.` ⚠️ | measured 37.6–41.7 GB/s at **8** ranks; 16 ranks is worse and loses the custom-AR path entirely |
| Prefill rate | fitted `1/rate = 2.555e-4 + 3.914e-10 × T` | least squares on the three **measured** 16-GPU points (8,192 / 32,768 / 65,535 → 3,861.7 / 3,732.5 / 3,554.4 tok/s), then × 2494/2617 for the Server Edition's lower clock |

**Calibration, shown:**

```
measured: 55.801 tok/s at batch 1, TP16/DCP16, 256-token prompt, 16 × WS cards
          -> TPOT = 17.92 ms
bytes/step(b=1, ctx=256) = 25.83 (experts) + 93.85 (non-expert) + 0.004 (KV) + 0.449 (state)
                         = 120.1 GB
memory time at MBU 1.0   = 120.1e9 / (16 × 1.792e12) =  4.19 ms
collective floor         = 93 × 2 × 24.1 µs          =  4.48 ms   (25 % of the whole step!)
residual memory time     = 17.92 − 4.48              = 13.44 ms
=> calibrated MBU = 4.19 / 13.44 = 0.312
   (a naive single-term fit would report MBU = 4.19 / 17.92 = 0.234)
```

That 4.48 ms is the price of having no NVLink, and it is **unavoidable at
TP16** because vLLM disables its PCIe custom all-reduce above world size 8. At
TP8 the same 186 collectives would cost 93 × 2 × 9.2 µs = **1.71 ms** with
`luke`'s custom kernel — a 2.6× reduction in the comm term, which is the single
strongest argument for the 8-GPU QSRT-K2 shape (§5).

### 3.2 Bytes per decode step

| Batch | Distinct experts/layer | Expert GB | Non-expert GB | MLA KV GB (8 K) | KDA state GB | **Total GB** |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 16.0 | 25.83 | 93.85 | 0.11 | 0.45 | **120.2** |
| 8 | 120.3 | 194.17 | 93.85 | 0.91 | 3.59 | **292.5** |
| 32 | 392.6 | 633.82 | 93.85 | 3.62 | 14.38 | **745.7** |
| 64 | 613.2 | 989.91 | 93.85 | 7.25 | 28.76 | **1,119.8** |
| 128 | 806.7 | 1,302.36 | 93.85 | 14.50 | 57.52 | **1,468.2** |
| 256 | 887.1 | 1,432.10 | 93.85 | 28.99 | 115.04 | **1,670.0** |

At batch 1, **78 % of the bytes are the non-expert weights.** MXFP4 is a
capacity win, not a latency win — and on this card, where the MoE runs W4A16
anyway, it is *only* a capacity win.

### 3.3 Estimated matrix — 16 × Server Edition, TP16 + DCP16 (**community-overlay case**, not the planning figure — see §3.7)

Feasibility per METHODOLOGY §3: rows are marked `infeasible (KV)` against the
measured 1,460,937-token FP8 pool, and flagged when they exceed the measured
12-sequence engine admission cap. `TTFT` assumes the field's 4,096-token
scheduler chunk, so aggregate prefill rate is flat in concurrency and
`TTFT(C) ≈ C × prompt / 3,707 tok/s`.

**S1 — 4 K in / 512 out (ctx 4,608). KV-token cap 317, engine cap 12.**

| Conc | TPOT ms | out tok/s/GPU | agg tok/s | TTFT 0 % hit | TTFT 90 % hit | |
|---:|---:|---:|---:|---:|---:|---|
| 1 | 19.6 | 3.19 | 51.1 | 1.11 s | 0.11 s | |
| 8 | 41.2 | 12.15 | 194.4 | 8.84 s | 0.88 s | |
| **11** | **49.6** | **13.86** | **221.7** | **12.16 s** | **1.22 s** | ← S1 operating point (TPOT ≤ 50 ms) |
| **12** | **52.3** | **14.33** | **229.3** | 13.27 s | 1.33 s | ← S4 operating point |
| 32 | 97.9 | 20.43 | 326.9 | 35.36 s | 3.54 s | beyond measured engine cap `est.` |
| 64 | 145.0 | 27.58 | 441.3 | 70.72 s | 7.07 s | beyond measured engine cap `est.` |
| 128 | 193.2 | 41.40 | 662.4 | 141.45 s | 14.14 s | beyond measured engine cap `est.` |
| 256 | 227.5 | 70.32 | 1,125.2 | 282.90 s | 28.29 s | beyond measured engine cap `est.` |

**S2 — 32 K in / 1 K out (ctx 33,792). KV-token cap 43, engine cap 12.**

| Conc | TPOT ms | out tok/s/GPU | agg tok/s | TTFT 0 % | TTFT 90 % |
|---:|---:|---:|---:|---:|---:|
| 1 | 19.6 | 3.19 | 51.0 | 9.23 s | 0.92 s |
| 8 | 41.6 | 12.03 | 192.5 | 73.81 s | 7.38 s |
| 32 | — | — | — | — | — beyond engine cap; 99.5 ms `est.` |
| 64 / 128 / 256 | **infeasible (KV)** | | | | |

**S3 — 128 K in / 2 K out (ctx 133,120). KV-token cap 10, engine cap 12.**

| Conc | TPOT ms | out tok/s/GPU | agg tok/s | TTFT 0 % | TTFT 90 % |
|---:|---:|---:|---:|---:|---:|
| 1 | 19.8 | 3.16 | 50.5 | 42.20 s | 4.22 s |
| 8 | 42.9 | 11.65 | 186.3 | 337.58 s | 33.76 s |
| 32 / 64 / 128 / 256 | **infeasible (KV)** | | | | |

**A 1 M-token prompt takes 733 s (12.2 min) of prefill** on this configuration
at a single request, `est.` from the fitted curve — and only one such request
fits. Kimi-K3's own model card observes that context compaction at 300 K *beat*
the full 1 M window on BrowseComp (91.2 vs 90.4, [architecture.md §10.6](architecture.md)),
which on this hardware is not a quality argument but a survival one.

### 3.4 Measured — this exact pair, on this exact GPU

Every row below is `meas.` with a machine-readable receipt. **Caveat carried
into every row: the rig is 16 × RTX PRO 6000 Blackwell _Workstation_ Edition**
(1,792 GB/s, observed clocks 2,595–2,880 MHz, `active_pstate P1`), stated
verbatim in [qsrt-k2-tp16-dcp8](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/qsrt-k2-tp16-dcp8.md).
A Server Edition should land ~11 % lower on decode.

**Decode — 256-token prompt, temperature 0, post-warmup medians**
([r36 receipt](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/validation/kimi-k3-upstream-aligned-r36-20260822.json)):

| Profile | GPUs / shape | Runs | Emitted tok/s | Acceptance | Target cycles/s | tok / cycle | Effective TPOT | Physical target-KV tokens |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| Target-only (no spec) | 16, TP16/DCP16 | 3 | **55.801** | n/a | n/a | 1.000 | 17.92 ms | 1,058,823 |
| **Inferact DSpark** | 16, TP16/DCP16 | 7 | **122.695** | 0.41494 | 31.423 | 3.905 | 8.15 ms | 1,016,293 |
| **modal-labs DFlash** | 16, TP16/DCP16 | 7 | **155.069** | 0.61880 | 29.085 | 5.332 | 6.45 ms | 1,022,624 |
| Target-only, 2,048-chunk | 16, TP16/DCP16 | — | 52.950 | n/a | n/a | — | 18.89 ms | 1,460,937 |
| DSpark, 2,048-chunk | 16, TP16/DCP16 | — | 104.209 | 0.35706 | 29.676 | 3.499 | 9.60 ms | 1,057,049 |
| DFlash, 2,048-chunk | 16, TP16/DCP16 | — | 91.097 | 0.31050 | 28.664 | 3.174 | 10.98 ms | 1,048,576 |
| **QSRT-K2 2-bit, target-only** | **8**, TP8/DCP8 | — | **43.676** | n/a | n/a | — | 22.90 ms | 1,072,139 |
| QSRT-K2, target-only | 16, TP16/**DCP4** | — | 20.29 | n/a | n/a | — | 49.29 ms | — |

Sources: [source-locked-runtime-20260816](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/validation/source-locked-runtime-20260816.json),
[r36](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/validation/kimi-k3-upstream-aligned-r36-20260822.json),
[qsrt-k2-tp16-dcp8](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/qsrt-k2-tp16-dcp8.md).

**Prefill — 16 × WS, TP16/DCP16, 4,096-token scheduler chunks, prefix caching
and external KV off, unique cache salt**
([full-mxfp4-p4096 receipt](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/validation/full-mxfp4-p4096-20260819.json)):

| Prompt tokens | TTFT (s) | tok/s aggregate | tok/s/GPU | Runs |
|---:|---:|---:|---:|---:|
| 8,192 | 2.121 | **3,861.73** | 241.4 | 6 |
| 32,768 | 8.779 | **3,732.49** | 233.3 | 3 |
| 65,535 | 18.437 | **3,554.45** | 222.2 | 3 |

Implied prefill MFU, computed: 8,192 tokens = `2 × 104.19 G × 8192` = 1.707 PFLOP
GEMM + 0.049 PFLOP MLA attention = 1.757 PFLOP in 2.121 s = **828.0 TFLOPS
aggregate = 51.75 TFLOPS/GPU = MFU 0.103** against the Workstation Edition's
503.8 TFLOPS dense BF16 (0.108 against the Server Edition's 480).

**Closest published proxy for concurrency scaling** — Kimi-K2.6 (same vendor,
same MLA+MoE family, INT4/FP8-KV, Eagle3 MTP γ=3) on the **same 16-GPU RTX PRO
6000 rig**, aggregate decode tok/s
([16-GPU rig page](https://github.com/local-inference-lab/rtx6kpro/blob/master/hardware/asrockrack-turin-cpayne-16gpu.md)):

| ctx \ conc | 1 | 8 | 32 | 128 |
|---|---:|---:|---:|---:|
| 0 (TP16) | 129.6 | 639.3 | 1,399.5 | 2,959.6 |
| 16 K | 119.9 | 448.0 | 659.6 | — |
| 64 K | 84.8 | 214.9 | — | — |
| 128 K | 61.8 | 120.3 | — | — |
| **0 (TP8, same rig)** | 125.0 | 528.3 | 1,167.0 | 2,510.3 |

### 3.5 Why estimate and measurement differ — and where each is wrong

1. **My batch-1 estimate is the calibration point, so it agrees by
   construction** (19.6 ms SE vs 17.92 ms WS — the 9 % gap is exactly the
   1,792/1,597 bandwidth ratio applied to the memory term only). This is not
   independent validation.
2. **My concurrency > 12 rows are unreachable today and should be read as a
   roofline, not a forecast.** The engine caps admission at 12 sequences
   because CUDA-graph capture plus the B12X MoE scratch does not fit in the
   ~2 GB of post-weight headroom. The Kimi-K2.6 proxy shows what the *silicon*
   can do when weights leave room (2,960 tok/s at conc 128) — Kimi-K3's 1.56 TB
   checkpoint is what forecloses it.
3. **The published concurrency scaling is much better than mine at high batch,
   and much worse at 128 K.** K2.6 at conc 128/ctx 0 reaches 2,960 tok/s; my
   K3 model predicts 662 tok/s at the same batch. K2.6 has ~32 B active
   parameters against K3's 104 B and one-third the weight bytes, so the ratio is
   plausible, but ⚠️ the gap is untested for K3.
4. **The measured MBU (0.312 with comm split out, 0.234 naive) is far below
   METHODOLOGY's 0.5–0.7 planning band and below
   [gpus/rtx6000-pro.md §9b](../../gpus/rtx6000-pro.md)'s 0.50–0.65 sm_120 band.**
   The GPU doc's own field observation — "Decode MBU, small-expert MoE ~0.26
   `est.` on active bytes" — is the closer figure. This document's 0.312 should
   replace the 0.50–0.65 band for *this model* on *this card*.
5. **Measured prefill MFU 0.103 is one-third of the GPU doc's 0.25–0.40 FP8 /
   0.20–0.35 FP4 band and one-quarter of METHODOLOGY's 0.35–0.50 BF16 band.**
   Causes, in order: W4A16 dequant in the expert GEMM; 16 experts of 896 hit per
   token so the grouped GEMM is thin; 4,096-token chunks; 186 PCIe collectives
   per forward. The band in the GPU doc was derived from a **3 B-active dense-ish
   NVFP4 MoE** (62,407 tok/s on Nemotron-3-Nano-Omni); it does not transfer to a
   104 B-active model.
6. **Speculation is worth more here than on B300, not less.** DFlash's 2.78×
   and DSpark's 2.20× at batch 1 exceed SGLang's B300 DSPARK figure (3.00× at
   conc 1, but that B300 number pins acceptance synthetically at
   `SGLANG_SIMULATE_ACC_LEN=4.5` — see below). Because decode here is
   *latency*-bound as much as bandwidth-bound (4.48 ms of the 17.92 ms step is
   collectives), amortising 186 all-reduces over 3.9–5.3 emitted tokens is
   worth more than it is on NVLink.

### 3.6 Speculative-decoding acceptance: measured vs synthetic

METHODOLOGY requires this distinction and it changes the conclusion.

| Source | Figure | Kind |
|---|---|---|
| **This pair, 16 × RTX PRO 6000, Inferact DSpark** | `draft_acceptance_rate_median = 0.41494`, `emitted_tokens_per_target_cycle = 3.905`, 7 runs | **measured, receipted** |
| **This pair, modal-labs DFlash** | `0.61880`, `5.332 tokens/cycle`, 7 runs | **measured, receipted** |
| SGLang B300 DSPARK cells | TPOT 2.84 / 9.88 / 24.47 ms | **synthetic acceptance** — SGLang pins it with `SGLANG_SIMULATE_ACC_LEN=4.5`; *"no measured acceptance rate exists for a real workload yet"* ([architecture.md §7.4](architecture.md)) |
| RadixArk DSpark `acc_len` | 5.51 HumanEval → 2.99 AIME26, ceiling 8.0 | measured, but **on a different stack and different GPUs** |
| Kimi-K2.6 Eagle3 on the same 16-GPU rig | 0.284–0.542 across ctx × conc | measured, proxy model |

**The RTX PRO 6000 receipts are, as of 2026-09-19, the only end-to-end
*measured* acceptance figures for Kimi-K3 speculation on any GPU** that are
published with the throughput they produced. That is a genuinely useful thing
for a card the vendors do not support.

### 3.7 The 32-card planning figure (added 2026-09-19, gap `X7`)

§0.2 resolves the planning count to **32**. Nothing here is measured — 32 cards
is two chassis with no GPU fabric (§5.1) and no one has run it. These are the
numbers `matrix/pairs.json` and `matrix/cost-matrix.md` carry, computed with
`python3` from the *same* §3.1 model, changing only `n` 16 → 32 and dropping the
MXFP8 overlay (unnecessary at 48.78 GB/GPU):

```python
# non-expert decode bytes, native BF16: 114.40 GB total − 2.35 GB embedding
#   (§3.1's 93.85 GB = 114.40 − 18.20 overlay saving − 2.35 embed; 163,840 × 7168 × 2 B = 2.35 GB)
NONEXPERT = 112.05e9      # instead of 93.85e9
n = 32;  kv_budget/GPU = 0.90×96e9 − 1_560_860_324_864/32 − 4e9 = 33.62e9 B
                        -> 1,075.94 GB aggregate   (weights 48.78 GB/GPU)
```

| Quantity | 32 cards (planning) | 16 cards (community overlay) |
|---|---:|---:|
| Weights/GPU | **48.78 GB** ✔ | 97.55 GB ✗ native / 96.42 GB ✗ overlay A / 94.09 GB overlay ceiling |
| KV budget/GPU | **+33.62 GB** | −15.2 GB (native) |
| Max concurrency @ 8 K, FP8 KV, `S`=5 | **455** | 12 (engine/workspace cap, not KV) |
| Max concurrency @ 128 K | **265** | 11 |
| **S1** (TPOT ≤ 50 ms) | **conc 29** · TPOT 49.4 ms · 18.36 tok/s/GPU · 587.4 agg · TTFT 16.02 s @ 0 % hit | conc 11 · 49.6 ms · 13.86 · 221.7 |
| **S4** (no SLO, KV-capped at ctx 4,608) | conc 465 · TPOT 148.1 ms · 98.10 tok/s/GPU · 3,139.2 agg | conc 12 · 52.3 ms · 14.33 · 229.3 |
| $/1M output, S1 | **$27.24 – $62.69** | $36.08 – $83.05 |
| $/1M output, S4 | $5.10 – $11.73 | $34.89 – $80.31 |
| $/1M input | $2.16 – $4.97 (unchanged: node cost and prefill both scale ×2) | $2.16 – $4.97 |
| Blended 75/25 at S1 | **$7.70 – $17.72** | $9.91 – $22.81 |

**Three caveats that make these softer than the 16-card rows, not harder.**
(a) The collective term keeps §3.1's 24.1 µs NCCL floor and 35 GB/s bus
bandwidth, which were estimated at **16** ranks in one chassis — a real TP32
spanning two boxes over Ethernet would be far worse, so every row above is an
**upper bound**. (b) `MBU = 0.312` was calibrated at TP16 and is reused here.
(c) The S4 row drops the **measured 12-sequence engine admission cap**, because
that cap was a consequence of ~2 GB/card of post-weight headroom (§1.4) which
does not exist at 33.62 GB/card — but no one has measured the replacement, so
S4 is a roofline. This is also what makes the 32-card concurrency figures
**comparable with the other 39 cells** in `matrix/fit-matrix.md` §2, which are
all KV-derived; the 16-card 12/11 pair never was (fit-matrix §6.7).

---

## 4. Cost

> **Scope of this section (2026-09-19, gap `X7`).** Everything in §4 is the
> **16-card community-overlay case**, kept intact because it is the only shape
> with receipts. The **32-card planning figures** that `pairs.json` and
> `matrix/cost-matrix.md` carry are in **§3.7**; at 32 cards the node is
> $57.60/h (Nebius) – $132.58/h (AWS) and S1 lands at $27.24 – $62.69/1M output.

### 4.1 Prices used (cite the rows)

From [`cross-cutting/cloud-pricing.md` §5.10](../../cross-cutting/cloud-pricing.md),
row `rtx6000-pro` / RTX PRO 6000 Blackwell (Server Edition) — **never a
neighbouring GPU's row**:

| Tier | $/GPU-hour | Row |
|---|---:|---|
| **Cheapest reputable on-demand** | **$1.80** | Nebius, "RTX PRO 6000", 1–8 GPUs |
| **Cheapest hyperscaler on-demand** | **$4.143** | AWS `g7e.48xlarge`, 8 × RTX PRO 6000 Blackwell SE ($33.1443/instance-h) |
| **Cheapest committed (1 y)** | **$1.30** | Hyperstack "RTX PRO 6000 SE" reserved (its on-demand is $1.85) |
| (context) GCP 3-y resource CUD | $1.979 | `g4-standard-384` |
| (context) cheapest spot | $0.95 | Nebius preemptible |

Node cost at 16 GPUs: **$28.80/h** (Nebius) · **$66.29/h** (AWS) · **$20.80/h**
(Hyperstack reserved). Note that 16 cards is **two** AWS `g7e.48xlarge`
instances with no GPU path between them — AWS cannot actually host this shape,
so the "$4.143 hyperscaler" column is a price ceiling for sizing, not a
deployable option (§5).

### 4.2 Cost per 1M tokens

Aggregate prefill at a 4 K prompt: **3,707 tok/s** (`est.`, the measured 16 × WS
curve × 2494/2617 for the SE clock).

| Operating point | agg out tok/s | $/1M **output** low | hyperscaler | 1-y reserved | $/1M **input** low | hyperscaler |
|---|---:|---:|---:|---:|---:|---:|
| **S1 interactive** — conc 11, TPOT 49.6 ms | 221.7 | **$36.08** | $83.05 | $26.06 | **$2.16** | $4.97 |
| **S4 max-throughput** — conc 12, TPOT 52.3 ms | 229.3 | **$34.89** | $80.31 | $25.20 | $2.16 | $4.97 |
| conc 1, no spec (max interactivity) | 51.1 | $156.55 | $360.33 | $113.07 | $2.16 | $4.97 |
| conc 1, **DFlash ×2.78 (measured)** | 142.1 | $56.30 | $129.58 | $40.67 | $2.16 | $4.97 |
| conc 11, **DFlash ×1.97 `est.`** | 436.7 | **$18.32** | $42.16 | $13.23 | $2.16 | $4.97 |

The ×1.97 decay factor at conc ≈ 11 is taken from SGLang's measured B300 DSPARK
decay curve (3.00× at conc 1 → 1.97× at conc 16 → 1.64× at conc 64,
[architecture.md §7.4](architecture.md)) applied to the **measured** 2.78× DFlash
figure's regime — ⚠️ the decay curve itself is not measured on this card.

### 4.3 Blended, 75 % input (50 % cached at ~10 % of uncached cost) / 25 % output

METHODOLOGY §6 verbatim; for *our own serving cost* a cache hit is charged at
10 % of uncached prefill.

| Operating point | low ($1.80) | hyperscaler ($4.143) | reserved ($1.30) |
|---|---:|---:|---:|
| **S1 interactive, no spec** | **$9.91** | **$22.81** | $7.16 |
| S1 interactive + DFlash ×1.97 `est.` | $5.47 | $12.59 | $3.95 |
| S4 max-throughput | $9.61 | $22.13 | $6.94 |

### 4.4 Effect of prefix caching and speculation

- **Prefix caching is the biggest single cost lever on the input side and it is
  off by default for K3 in vLLM** ([serving-optimizations.md §1.1](../../cross-cutting/serving-optimizations.md)).
  Measured on a B300 node, K3's hybrid KDA prefix checkpoints cut TTFT by
  **9–25 %** ([vLLM K3 performance blog](https://vllm.ai/blog/2026-09-13-kimi-k3-performance-optimization)).
  On this card TTFT is the dominant **latency** term (12.16 s at the S1
  operating point against a 49.6 ms TPOT), so a 90 % hit rate turns that 12.16 s
  into **1.22 s** — a 10× user-visible improvement. Its effect on *cost* is
  small, and the arithmetic is worth showing because it is easy to overstate:
  the input side is only $0.89 of the $9.91 blended figure, so raising the hit
  rate from METHODOLOGY's fixed 50 % to 90 % moves blended cost to
  **$9.33** `est.` (−6 %). **On this pair prefix caching buys latency; only
  speculation buys cost.**
- **Speculation is the biggest lever on the output side**: DFlash's measured
  2.78× at batch 1 is worth **2.78× on $/1M output** at the interactive point,
  and ~1.97× `est.` at conc 11 — $36.08 → $18.32.
- **The two compete for the same memory.** DSpark costs 4.50 GB/rank of draft
  weights plus a replicated draft KV; the receipts show the KV pool falling from
  1,460,937 to 1,016,293 tokens when it is enabled. On a card with 2 GB of
  headroom that trade is not free — and RedHatAI's 9.49 GB draft is marked
  **"128 K+ context unsupported"** in its qualified profile for exactly this
  reason.

### 4.5 Versus the vendor API, and versus other GPUs

Moonshot list price ([architecture.md §11](architecture.md),
[platform.kimi.ai](https://platform.kimi.ai/docs/pricing/chat-k3)): **$3.00/1M
input (miss) · $0.30/1M input (hit) · $15.00/1M output.**

| | $/1M output, this pair | vs API $15.00 |
|---|---:|---|
| S1 interactive, no spec, Nebius $1.80 | $36.08 | **2.4× the API price** |
| S4 max-throughput, Nebius $1.80 | $34.89 | 2.3× |
| S1 + DFlash ×1.97 `est.`, Nebius | $18.32 | 1.2× |
| S1 + DFlash, Hyperstack reserved $1.30 | $13.23 | **0.88× — the only configuration that beats list** |
| conc 1, no spec | $156.55 | 10.4× |

**Break-even $/GPU-hour** at an 8:1 input:output token mix (METHODOLOGY §6's
sanity check, computed at the same output rate):

| Point | all cache-miss | all cache-hit |
|---|---:|---:|
| conc 11, no spec | **$1.95/GPU-h** | $0.87/GPU-h |
| conc 11, DFlash ×1.97 `est.` | **$3.83/GPU-h** | $1.71/GPU-h |
| conc 1, no spec | $0.45/GPU-h | $0.20/GPU-h |

**Break-even utilisation** against Nebius $1.80 (node $28.80/h), no spec:
**92.5 %** on all-cache-miss traffic, and **> 100 % (207 %) — i.e.
unreachable** once traffic is as cacheable as Moonshot's own ("> 90 % cache hit
rate in coding workloads"). With DFlash it falls to **47 %** (miss) / **105 %**
(hit). Translation: *self-hosting Kimi-K3 on RTX PRO 6000 only makes economic
sense against the API if (a) speculation is on, (b) the fleet runs near
saturation, and (c) your traffic is not cache-heavy.* If your traffic **is**
cache-heavy, the API's $0.30 cached-input price beats anything you can build on
this card.

**The cross-GPU comparison is the number to act on.** Using SGLang's measured
B300 node output rates ([architecture.md §10.1](architecture.md)) and
[cloud-pricing.md](../../cross-cutting/cloud-pricing.md) rows:

| Configuration | tok/s/GPU | $/GPU-h | **$/1M output** |
|---|---:|---:|---:|
| 8 × B300, Balanced DCP8, MXFP4, DSPARK, conc 64 | 220.9 | $7.40 (Hyperstack) | **$9.31** |
| 8 × B300, Balanced DCP8, MXFP4, no spec, conc 64 | 155.1 | $7.40 | **$13.25** |
| 8 × MI350X, Balanced, no spec, conc 64 | 90.4 | $8.60 (OCI MI355X row) | $26.43 |
| **16 × RTX PRO 6000 SE, conc 12, no spec** | **14.33** | **$1.80 (Nebius)** | **$34.89** |
| 8 × B300, no spec, conc 64 | 155.1 | $15.00 (OCI) | $26.86 |

**The RTX PRO 6000 is 4.11× cheaper per GPU-hour than a B300 and still 2.63×
more expensive per output token**, because it needs 2× the GPU count and
delivers **10.8× less throughput per GPU**. On input tokens the gap is worse:
$2.16/1M here against ~$0.36/1M on an 8 × B300 node at $7.40 (`est.` from the
SGLang conc-64 TTFT). This is the same conclusion
[gpus/rtx6000-pro.md §10d](../../gpus/rtx6000-pro.md) reaches from CloudRift's
own measurements — "Eight-GPU, FP8, model needs all-reduce → it is 2.3–2.4×
more expensive per token than H100/H200 because PCIe replaces NVLink" — and
Kimi-K3 is the most extreme instance of it in this repo.

---

## 5. Scaling and deployment shape

### 5.1 Single node vs multi-node

**Single chassis, 16 cards, or nothing.** The qualified deployment is one
host: ASRockRack GENOAD24QM32 (single-socket SP5) + EPYC 9575F Turin + **4 ×
c-payne Microchip Switchtec PM50100 Gen5 switches**, 4 GPUs per switch, all
four switches on *distinct* root complexes, IOMMU off, ACS Request-Redirect
cleared on every bridge, driver 595.58.03 open
([rig page](https://github.com/local-inference-lab/rtx6kpro/blob/master/hardware/asrockrack-turin-cpayne-16gpu.md)).
Two constraints kill anything larger:

1. **`cudaDeviceEnablePeerAccess` is capped at 8 peers per GPU per process**, so
   a full 16 × 16 P2P mesh in one process is impossible; NCCL/IPC handles are
   required past 8 GPUs (same source).
2. **There is no GPU fabric between chassis.** [gpus/rtx6000-pro.md §4](../../gpus/rtx6000-pro.md)
   states it flatly: *"Multi-node LLM serving is not a supported design point
   for this card."* TP32 across two boxes over Ethernet, for a 93-layer model
   with 186 all-reduces per token, is not a deployment — it is a benchmark of
   your NIC.

So METHODOLOGY §3's "min 19 → topology step 32" is arithmetically correct and
operationally unqualified. **Revised 2026-09-19 (gap `X7`): 32 is nevertheless
the planning figure** (§0.2, §3.7), because the alternatives are worse on
METHODOLOGY's own terms — METHODOLOGY §7 forbids sizing on an engine path that
is not supported, and both 16 (unverified MXFP8 overlay, unaffiliated fork) and
8 (2-bit community requant, concurrency 1) are exactly that. The honest summary
of this pair is that **no shape on this card is both convention-compliant and
demonstrated**: 32 has the capacity and no fabric, 16 has the fabric and an
unverified fit. The demonstrated ladder is still **16 (native MXFP4 + MXFP8
overlay) or 8 (2-bit community requant)**, and every measured table in this
document is on that ladder.

### 5.2 When PD disaggregation or wide-EP pays — it does not, here

- **PD disaggregation**: needs a fast KV-transfer fabric between prefill and
  decode pools. This card has PCIe Gen5 to the host and nothing else; Dynamo has
  no sm_120 statement ([gpus/rtx6000-pro.md §7](../../gpus/rtx6000-pro.md)).
  The *case* for it is strong — prefill saturates long before decode (TTFT
  12.2 s vs TPOT 49.6 ms at conc 11) — but there is no mechanism. ⚠️
- **Wide-EP**: SGLang's own warning applies doubly on PCIe — *"Don't use EP with
  an a2a backend: a2a buffers reclaim the KV that DCP buys"*
  ([architecture.md §5.7](architecture.md)). With 2 GB/GPU of headroom there is
  no KV to reclaim.
- **Pipeline parallel is the un-taken opportunity.** On this card PP beat TP by
  **7×** for a 400 B MoE ([gpus/rtx6000-pro.md §4](../../gpus/rtx6000-pro.md):
  TP4 6–7 tok/s vs TP2+PP2 46–49 tok/s), and SGLang measured `PP16 × TP1` as
  the best **prefill** shape for Kimi-K3 on GB200 (4,550 vs 1,652 tok/s/GPU for
  TP16, [architecture.md §10.2](architecture.md)) — precisely because PP moves
  one activation per stage boundary instead of an all-reduce per layer, which is
  what this card is bad at. No K3 PP profile exists on RTX PRO 6000, and DSpark
  requires `pp_size == 1`, so taking it means giving up the 2.2–2.78× speculation
  win. ⚠️ **TO BE VERIFIED**: whether `PP16 × TP1` + no speculation beats
  `TP16 + DCP16` + DFlash on this rig. It is the single most valuable experiment
  for this pair.

### 5.3 Launch commands (quoted)

**There is no vendor launch command for this pair.** `vllm serve` from
[the official recipe](https://recipes.vllm.ai/moonshotai/Kimi-K3.json) uses
`--attention-backend TOKENSPEED_MLA` and `--attention-config
'{"mla_prefill_backend":"TOKENSPEED_MLA"}'`, both **10.x-only**
([flash-attention.md §9.5](../../cross-cutting/flash-attention.md)), so it fails
on sm_120. What follows is the community-qualified launch, quoted verbatim from
[`models/kimi-k3/production-runtime.md`](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/production-runtime.md).

**Target-only production profile (the deployed one, 12 sequences, vision on):**

```bash
docker run -d \
  --name kimi-k3-production-nospec \
  --restart unless-stopped \
  --gpus all --network host --ipc=host \
  --ulimit memlock=-1 --ulimit stack=67108864 \
  -v /root/.cache/huggingface:/root/.cache/huggingface:ro \
  -v "$CACHE_DIR":/cache/jit:rw \
  -e CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:?select 16 GPU UUIDs}" \
  -e HOST=0.0.0.0 -e PORT=30001 -e TP_SIZE=16 -e DCP_SIZE=16 \
  -e MAX_MODEL_LEN=950000 -e MAX_NUM_BATCHED_TOKENS=4096 \
  -e MAX_NUM_SEQS=12 -e KV_CACHE_MEMORY_BYTES=900000000 \
  -e ENABLE_PREFIX_CACHING=1 -e HOST_KV_BACKEND=native \
  -e KV_OFFLOADING_SIZE=32 -e ENABLE_VISION=1 \
  -e B12X_MOE_WORKSPACE_TOKEN_LIMIT=4096 \
  -e B12X_W4A16_PREFILL_FUSED_SUM=1 \
  -e B12X_W4A16_STABLE_ROUTE_PACK=1 \
  -e PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True \
  --entrypoint /usr/local/bin/serve-kimi-k3-full-mxfp4-nospec-ii \
  "$IMAGE" --host 0.0.0.0 --port 30001 \
    --max-num-scheduled-tokens 4096 \
    --speculative-config None \
    --compilation-config '{"mode":0,"cudagraph_mode":"FULL_AND_PIECEWISE","cudagraph_capture_sizes":[1,2,3,4,5,6,7,8,9,10,11,12],"pass_config":{"fuse_allreduce_rms":true}}'
```

with
`IMAGE=voipmonitor/vllm@sha256:32ff80279164365b21cdb420d7a22101be704df42b66db2c17d64d5e0558f240`
(tag `…-vllm6e843eb-b12x2d466e3-cu133-torch213-20260918-r38`).

**DSpark profile** — identical but `MAX_NUM_SEQS=1`,
`KV_CACHE_MEMORY_BYTES=1325000000`, `MAX_NUM_BATCHED_TOKENS=4102`,
`MAMBA_BLOCK_SIZE=12288`, `B12X_MOE_WORKSPACE_TOKEN_LIMIT=4096`, and no
`--speculative-config None`. Two operational warnings are quoted verbatim in
the source: *"Do not set `NCCL_GRAPH_FILE` to an empty value"* and *"Do not run
`nvidia-smi dmon` during performance qualification. Concurrent NVML polling has
reproduced a persistent target-cycle reduction on an unchanged control
process."*

**Why `MAX_NUM_BATCHED_TOKENS=4102`, not 4096**: *"Speculative scheduling
subtracts six draft slots from the batch-token budget for one active sequence…
A batch-token value of 4,096 permits only 4,090 prompt tokens, which divides an
8,192-token prompt into three scheduler iterations instead of two."* That one
off-by-six is worth **+11.92 % prefill** at 8 K.

**The 8-GPU shape** (2-bit `lukealonso/Kimi-K3-QSRT-K2`, TP16/DCP8 documented,
TP8/DCP8 qualified at concurrency 1):

```bash
docker run -d --name kimi-k3-qsrt-nospec --gpus all --network host --ipc=host \
  --shm-size=32g --ulimit memlock=-1 --ulimit stack=67108864 \
  --ulimit nofile=1048576:1048576 -e PORT=8001 \
  -v /root/.cache/huggingface:/root/.cache/huggingface:ro \
  -v /mnt/kimi-k3-cache/source-locked-r2/qsrt-tp16-nospec:/cache/jit \
  --entrypoint /usr/local/bin/serve-kimi-k3-qsrt-nospec \
  voipmonitor/vllm@sha256:9230c19c6b16ca6216613360619b0cca2356dba65c2297c99817750b3f9e4b83
```

### 5.4 Warm-up and loading time

| Path | Figure |
|---|---|
| **Measured weight load** | **395.497 s (6.6 min)** for the full checkpoint, `max_weight_load_seconds` in the [r38 receipt](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/validation/kimi-k3-upstream-aligned-r38-20260918.json) — **3.95 GB/s effective**, using `InstantTensor 0.1.9` |
| Pure wire time at 5 GB/s NVMe | 5.2 min |
| Pure wire time at 10 GB/s NVMe | 2.6 min |
| Pure wire time at 20 GB/s NVMe | 1.3 min |
| First-cold-start download at 10 Gbit/s | ≈ 21 min ([architecture.md §1.2](architecture.md)) |
| JIT/graph warm-up | **on top of the above** — B12X CuTe-DSL, Triton, torch-inductor and FlashInfer caches are all mounted per-profile (`/cache/jit`); the docs insist on a **separate writable JIT cache per profile** because "The profiles compile different graph shapes and generated kernels" |

At 3.95 GB/s the loader is **not** NVMe-bound on a decent array — it is bound by
the MXFP8 conversion happening during load ("converts the supported target and
draft dense projections to MXFP8 before allocating the physical KV cache").
Budget **8–12 minutes** from `docker run` to `/health` on a warm JIT cache, and
considerably more on a cold one. vLLM's own K3 recipe sets
`VLLM_ENGINE_READY_TIMEOUT_S=3600` for a reason.

---

## 6. Risks and open questions

**Everything marked ⚠️ TO BE VERIFIED in this document (15 items):**

1. **`model_memory_gib_per_rank = 90.42` in the r38 receipt is unit-inconsistent.**
   90.42 GiB = 97.09 GB, which exceeds the 96 GB nameplate. Either the field
   value is GB mislabelled as GiB, or the overlay is broader than the documented
   five tensor names. *Method: read the vLLM startup log's "model weights take
   X GiB" line directly on the rig.*
2. **The exact MXFP8 overlay coverage.** The docs name `q_proj, k_proj, v_proj,
   b_proj, f_a_proj` plus vision; my arithmetic says that is not enough to fit
   16 cards (96.42 GB/GPU). *Method: dump the loaded parameter dtypes per module.*
3. **All measurements are Workstation Edition, not Server Edition.** 1,792 vs
   1,597 GB/s and 2,617 vs ~2,494 MHz boost. Every Server-Edition number here is
   a scaled estimate. At least one rig in the same community also reports
   **overclocked GDDR7**. *Method: re-run the 256-token decode protocol on an
   SE box.*
4. **`S` (KDA state slots per request) under vLLM.** METHODOLOGY §8 pins
   SGLang's `S=5`; vLLM's mamba cache is ≥ 1 plus speculative slots and the
   community runtime sets `MAMBA_BLOCK_SIZE=12288`, which is a different
   accounting entirely. My §1.4 tables use S=5; the 16-GPU field configuration
   is only consistent with S≈1.
5. **All-reduce bus bandwidth at 16 ranks** is estimated at 35 GB/s from the
   measured 37.6–41.7 GB/s at **8** ranks. The 16-rank path also loses vLLM's
   custom all-reduce entirely.
6. **Concurrency above 12 is untested for this model on this card.** Every row
   at conc 32/64/128/256 in §3.3 is roofline arithmetic against an engine that
   currently refuses to admit them.
7. **The DSpark decay curve (3.00× → 1.97× → 1.64×) is SGLang's B300 curve**,
   and SGLang's own DSPARK cells pin acceptance synthetically. Applying it to
   the measured 2.78× DFlash figure at conc 11 is an extrapolation.
8. **`PP16 × TP1` on this card is unmeasured** and is the most likely large win
   (§5.2). It is mutually exclusive with DSpark (`pp_size == 1`).
9. **Prefix-caching effectiveness on this pair is unmeasured.** The 9–25 % TTFT
   figure is from a **B300** node. The K3-specific `--prefix-match-unit 128`
   gotcha ([architecture.md §8.8](architecture.md)) has no sm_120 confirmation.
10. **Accuracy of the executed precision is not measured for the official
    checkpoint on this card.** The only accuracy data points that exist are:
    (a) `Fluffy/Kimi-K3-W4A16-RTN` (an INT4 RTN requant, 1,411.74 GB, run on a
    single A100 with host offload at **0.91–0.96 tok/s**) scoring **OCRBench
    0.879 vs official 0.89** and **GPQA Diamond 0.843 vs official 0.935** — but
    at *low* reasoning effort, which the authors say is "not like-for-like"
    ([HF card](https://huggingface.co/Fluffy/Kimi-K3-W4A16-RTN)); and (b) the
    2-bit QSRT-K2 requant losing **3.00–4.00 percentage points** on AA-LCR
    against the official MXFP4 on the same 16-GPU rig (254/300 → 245/300 under
    a frozen K3 judge, 249/300 → 237/300 under GPT-5.6 Sol; McNemar p = 0.122
    and p = 0.029; bootstrap 95 % CIs `[-0.0600, -0.0033]` and `[-0.0767,
    -0.0067]`). **The 8-GPU shape costs 3–4 points of capability.**
11. **`B12X_W4A16_PREFILL_FUSED_SUM=1` is not bitwise-deterministic.** The
    bounded FP32 route reduction changes the order of additions: cosine
    similarity 1.0, max BF16 absolute difference 0.015625, mean KLD 0.003136,
    top-1 agreement **98.42 %** over 1,024 contexts / 2,096,128 scored positions.
    The paired sentinel control's 95 % interval `[-0.000111, +0.000257]` crosses
    zero, so no fidelity loss is *detected* — but "Bitwise identity and bitwise
    repeat determinism are unsupported."
12. **The community fork is not upstream.** Its vLLM source lock composes ~20
    unmerged PRs (`414, 295, 294, 320, 413, 422, 310, 415, 418, 419, 459, 460,
    463, 464, 467, 468, 469, 471, 473`) plus B12X `227, 238, 239, 241`. Upstream
    merge disposition is tracked in
    [rtx6kpro issue #75](https://github.com/local-inference-lab/rtx6kpro/issues/75).
    There is no vendor support path, no CVE process, and no guarantee the next
    vLLM release keeps any of it working.
13. **TP12 viability.** The production doc says the generic B12X collectives
    "support TP8 and TP12, but those Kimi-K3 topologies require separate
    full-model qualification"; my arithmetic says TP12 does not divide 7168
    cleanly for the standard row/column split.
14. **NVFP4 MoE on sm_120.** [METHODOLOGY §8](../../METHODOLOGY.md) pins it as
    broken → Marlin W4A16; [quantization-formats.md §5](../../cross-cutting/quantization-formats.md)
    cites FlashInfer PR #2898 possibly closing the SM120 fused-MoE gap. The two
    are recorded as a disagreement there and neither is resolved for Kimi-K3.
    Moot in practice: the NVFP4 K3 builds are larger and slower even on B300.
15. **Whether `KimiK3ForConditionalGeneration`'s MLA layers reach the generic
    vLLM MLA backend registry at all**, or a model-private backend
    ([flash-attention.md §16.2](../../cross-cutting/flash-attention.md)); the
    existence of `vllm/models/kimi_k3/nvidia/` and `/amd/` suggests private
    wiring, which is why the community fork patches `kimi_k3/nvidia/model.py`
    directly.

**Known engine issues that bite this pair specifically:**

| Issue | Consequence | Source |
|---|---|---|
| vLLM PCIe custom all-reduce refuses world size 16 (*"Supported world sizes: [2, 4, 6, 8]"*) | 186 collectives/step at NCCL's 24 µs floor = 4.48 ms/step, 25 % of TPOT | [16-GPU rig](https://github.com/local-inference-lab/rtx6kpro/blob/master/hardware/asrockrack-turin-cpayne-16gpu.md) |
| RedHatAI DSpark draft under DCP without PR #310 | greedy block acceptance silently drops **20.7 % → 0.84 %**; numerically silent | [redhat-dspark-dcp16](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/redhat-dspark-dcp16.md) |
| CUDA-graph warmup OOM at `MAX_NUM_SEQS=8` on TP8/DCP8 | the 8-GPU shape is a concurrency-1 server | [source-locked receipt](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/validation/source-locked-runtime-20260816.json) |
| `nvidia-smi dmon` during a run | persistent target-cycle reduction on an unchanged control process | [production-runtime](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/production-runtime.md) |
| NCCL P2P hangs with IOMMU/ACS enabled (driver 580.126.09, NCCL 2.28.9/2.29.7) | fix BIOS before benchmarking or every number is wrong | [gpus/rtx6000-pro.md §4](../../gpus/rtx6000-pro.md) |
| BAR1 defaulting to 256 MB; headless display mode **required** for 16-GPU setups | cripples P2P | [rtx6kpro PCIe](https://github.com/local-inference-lab/rtx6kpro/blob/master/hardware/pcie-bandwidth.md) |
| `cudaDeviceEnablePeerAccess` capped at 8 peers/GPU/process | no 16×16 mesh in one process | [16-GPU rig](https://github.com/local-inference-lab/rtx6kpro/blob/master/hardware/asrockrack-turin-cpayne-16gpu.md) |
| K3 emits tool-call formats its own parser rejects | schema-validate and retry | [vLLM recipe notes](https://recipes.vllm.ai/moonshotai/Kimi-K3) |
| Thinking is always on and must be echoed back across turns | reasoning tokens accumulate in the prompt; prefill cost per agentic turn grows fast | [architecture.md §1.4](architecture.md) |

**Accuracy caveats of the executed precision.** The routed experts are MXFP4
**by quantisation-aware training** — "there is no BF16 Kimi-K3 checkpoint"
([architecture.md §1.3](architecture.md)) — so running them as W4A16 dequant is
*numerically* the intended precision; the dequant costs speed, not accuracy. The
two things that *do* move accuracy on this card are (a) the online MXFP8 overlay
on dense projections, for which **no accuracy measurement exists at all** ⚠️, and
(b) the bounded FP32 route reduction (item 11 above). The 3–4 point AA-LCR loss
(item 10) belongs to the 8-GPU 2-bit path, not to the 16-GPU official path.

**Licence risk.** The Kimi K3 License §2 requires a separate agreement with
Moonshot AI for any Model-as-a-Service business exceeding **US$20 M** of
aggregate revenue over 12 months ([architecture.md §1.1](architecture.md)).
Self-hosting for internal use is exempt; token resale is gated.

---

## Sources

**Repo documents (fact-checked; numbers taken as inputs):**
- [`research/METHODOLOGY.md`](../../METHODOLOGY.md) — §1 bytes/param, §2 KV and fixed state, §3 fit and the feasibility rule, §4 roofline, §6 scenarios and blended definition, §8 pinned inputs
- [`research/models/kimik3/architecture.md`](architecture.md) — §1.1 licence, §1.2 file sizes, §1.3 MXFP4 QAT, §2.3 KDA/MLA split, §3.3–§3.4 active params, §4 weight memory, §5.2–§5.3 KV and KDA state, §5.5 no sparse attention, §5.7 DCP, §6.3 decode bytes, §7 DSpark/DFlash, §8.2 hardware support, §8.6 the MXFP4 question per GPU, §8.8 prefix caching, §10.1 SGLang B300/MI350X cells, §10.2 GB200 prefill shapes, §10.6 quality, §11 vendor API pricing
- [`research/gpus/rtx6000-pro.md`](../../gpus/rtx6000-pro.md) — §1 sm_120 identity and 99 KB shared memory, §2 capacity and 1,597 GB/s, §3c dense-vs-sparse reconciliation, §4 no-NVLink cost, §5a–§5f kernels, §6/§6a quantization and the NVFP4 MoE problem, §7 engines, §8a prices, §9b–§9g rooflines and the four repo models, §10d published $/M token
- [`research/cross-cutting/flash-attention.md`](../../cross-cutting/flash-attention.md) — §9.5 sm_120 kernel matrix, §14.4 KDA decode backends and gates, §16.2 Kimi-K3 attention path per GPU
- [`research/cross-cutting/quantization-formats.md`](../../cross-cutting/quantization-formats.md) — §9.6 Kimi-K3 per-GPU execution path, §2.1/§10.3 RTX PRO 6000 dense figures, open questions 1 and 8
- [`research/cross-cutting/inference-engines.md`](../../cross-cutting/inference-engines.md) — §3.8 RTX PRO 6000 model coverage, SGLang SM120 attention guidance
- [`research/cross-cutting/serving-optimizations.md`](../../cross-cutting/serving-optimizations.md) — §1.1/§1.3 prefix caching for hybrid state, §1.5 vendor cached-input economics, §2.2–§2.3 DSpark, §7.2 Kimi-K3 1M worked example
- [`research/cross-cutting/cloud-pricing.md`](../../cross-cutting/cloud-pricing.md) — §5.10 RTX PRO 6000 Blackwell (Server Edition) price rows, §8 low/high/reserved summary

**Primary sources fetched or probed on 2026-09-19:**
- [vLLM Kimi-K3 recipe JSON](https://recipes.vllm.ai/moonshotai/Kimi-K3.json) — `meta.hardware`, `strategy_min_gpus`, `min_vllm_version 0.27.1`; `hw/rtx_pro_6000.json` and `hw/rtx6000.json` → **HTTP 404**, `hw/b300.json` → 200
- [SGLang Kimi-K3 cookbook](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3) — served payload, `supportedHardware:[b300,gb300,b200,gb200,h200,h100,mi350x,mi355x,a3]`, zero `sm120` occurrences
- [HF API `lukealonso/Kimi-K3-QSRT-K2`](https://huggingface.co/api/models/lukealonso/Kimi-K3-QSRT-K2?expand[]=safetensors&expand[]=usedStorage) — `usedStorage` 757,527,330,254 B, per-dtype parameter census
- [HF API `Fluffy/Kimi-K3-W4A16-RTN`](https://huggingface.co/api/models/Fluffy/Kimi-K3-W4A16-RTN?expand[]=safetensors&expand[]=usedStorage) — `usedStorage` 1,411,738,133,329 B, I32 2,730,302,066,688 packed params
- [`Fluffy/Kimi-K3-W4A16-RTN` model card](https://huggingface.co/Fluffy/Kimi-K3-W4A16-RTN) — A100 host-offload run, 0.91–0.96 tok/s, OCRBench 0.879, GPQA-D 0.843

**Community measurements (`local-inference-lab/rtx6kpro`, receipted, not vendor-validated):**
- [`models/kimi-k3/README.md`](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/README.md)
- [`models/kimi-k3/production-runtime.md`](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/production-runtime.md) — qualified 16-GPU runtime, launch commands, 55.801/122.695/155.069 tok/s table, MXFP8 overlay statement
- [`models/kimi-k3/full-mxfp4-p4096-prefill.md`](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/full-mxfp4-p4096-prefill.md) — prefill 3,861.7/3,732.5/3,554.4 tok/s, 1,057,049-token KV, 278.51 MiB bounded MoE scratch, KLD fidelity suite
- [`models/kimi-k3/qsrt-k2-tp16-dcp8.md`](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/qsrt-k2-tp16-dcp8.md) — "16 NVIDIA RTX PRO 6000 Blackwell **Workstation Edition** GPUs", TP16/DCP4 20.29 tok/s
- [`models/kimi-k3/redhat-dspark-dcp16.md`](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/redhat-dspark-dcp16.md) — MXFP8 conversion list, 20.7 % → 0.84 % acceptance collapse
- [`models/kimi-k3/native-host-kv-offload.md`](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/native-host-kv-offload.md)
- [`models/kimi-k3/aa-lcr-official-mxfp4-vs-qsrt-k2.md`](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/aa-lcr-official-mxfp4-vs-qsrt-k2.md) — −3.00/−4.00 pp, McNemar and bootstrap
- Receipts: [r36](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/validation/kimi-k3-upstream-aligned-r36-20260822.json) · [r38](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/validation/kimi-k3-upstream-aligned-r38-20260918.json) · [source-locked-20260816](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/validation/source-locked-runtime-20260816.json) · [full-mxfp4-p4096-20260819](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k3/validation/full-mxfp4-p4096-20260819.json)
- [`hardware/asrockrack-turin-cpayne-16gpu.md`](https://github.com/local-inference-lab/rtx6kpro/blob/master/hardware/asrockrack-turin-cpayne-16gpu.md) — 16-GPU 4-switch topology, Kimi-K2.6 TP8 vs TP16 concurrency matrix, custom-allreduce world-size limit
- [`hardware/pcie-bandwidth.md`](https://github.com/local-inference-lab/rtx6kpro/blob/master/hardware/pcie-bandwidth.md) — P2P 53–56 GB/s, NCCL all-reduce 37.6–41.7 GB/s, custom-vs-NCCL latency table
- [`benchmarks/results.md`](https://github.com/local-inference-lab/rtx6kpro/blob/master/benchmarks/results.md) and [`models/kimi-k25.md`](https://github.com/local-inference-lab/rtx6kpro/blob/master/models/kimi-k25.md) — Kimi-K2.5 8-GPU proxy

**Vendor / engine references:**
- [Moonshot pricing](https://platform.kimi.ai/docs/pricing/chat-k3) — $3.00 / $0.30 / $15.00 per 1M
- [vLLM K3 performance blog](https://vllm.ai/blog/2026-09-13-kimi-k3-performance-optimization) — 9–25 % TTFT from KDA prefix checkpoints (B300)
- [TensorRT-LLM #11799](https://github.com/NVIDIA/TensorRT-LLM/issues/11799) — no trtllm-gen SM120/121, no plan
- [vLLM #31085](https://github.com/vllm-project/vllm/issues/31085) — MXFP4 backend selection excludes SM120 → Marlin
- [rtx6kpro issue #75](https://github.com/local-inference-lab/rtx6kpro/issues/75) — upstream merge disposition

**Negative results, explicitly:** no MLPerf submission, no InferenceMAX entry,
no SemiAnalysis analysis and no vendor blog covers Kimi-K3 on RTX PRO 6000
Blackwell as of 2026-09-19. The InferenceX/InferenceMAX API carries RTX PRO 6000
rows for Qwen3.5 only ([METHODOLOGY §8](../../METHODOLOGY.md)). The
`local-inference-lab/rtx6kpro` wiki's `models/` directory contains `kimi.md`,
`kimi-k25.md`, `kimi-k26*.md`, `kimi-k27-code*.md` **and** `kimi-k3/` — the
last of which is the only published body of Kimi-K3-on-sm_120 measurement found.

---

## Audit log (2026-09-19)

- **2026-09-19 — gap `X7-kimik3-rtx6000-pro-16-vs-19-32-gpus` RESOLVED → 32 cards.**
  This document's headline verdict (a qualified 16-GPU deployment) contradicted
  [`gpus/rtx6000-pro.md` §9g](../../gpus/rtx6000-pro.md)'s standard-convention
  floor of `ceil(1,560,860,324,864 / (86.40e9 − 4e9))` = **19 cards → topology
  step 32** (recomputed exact with `python3`). Resolved **to 32** as the
  planning figure, per METHODOLOGY §7 (do not size on an engine path that is
  not supported as of 2026-09-19): the 16-card fit depends on an **unverified
  online MXFP8 weight-only overlay from an unaffiliated community fork**, not a
  vendor recipe. The 16-card case is **retained in full** as a clearly labelled
  community-overlay alternative — §3.3's matrix, §3.4's receipts and all of §4
  are unchanged and now scoped as such. Added **§3.7** with the 32-card
  arithmetic (weights 48.78 GB/GPU, KV budget +33.62 GB/GPU, 455 @ 8 K / 265 @
  128 K FP8-KV `S`=5, S1 conc 29 · 49.4 ms · 18.36 tok/s/GPU · $27.24–$62.69,
  S4 conc 465 · $5.10–$11.73 roofline, blended $7.70–$17.72), all recomputed
  with `python3` from §3.1's own model at `n` = 32 with the overlay dropped.
  Restated §0.2 and §0.4, revised §1.2's planning value and §5.1's ladder
  paragraph. Recut downstream in [`matrix/pairs.json`](../../matrix/pairs.json)
  (new `support_caveat`), [`matrix/cost-matrix.md`](../../matrix/cost-matrix.md)
  §2/§3/§4, [`matrix/fit-matrix.md`](../../matrix/fit-matrix.md) §1/§2/§6.7 and
  [`README.md`](README.md). **Honest residual:** 32 cards is two chassis with no
  GPU fabric (§5.1), so no shape on this card is both convention-compliant and
  demonstrated; §3.7's rows are upper bounds that reuse the 16-rank collective
  constants and the TP16-calibrated MBU.

Independent numerical audit, `python3`, against METHODOLOGY.md,
`architecture.md` §3–6 and `gpus/rtx6000-pro.md` §2/§3/§8/§9. Every derived
table in this document was recomputed from first principles (params/bytes in
`config.json`-derived counts, not read off the doc's own prose) and checked
against the stated formulas. **No number was found wrong by more than 5 %; no
edits were made to the body of this document.**

Recomputed and verified exact (or within sub-1 % rounding):

- §1.2 weight-byte arithmetic: experts (2,722,740,830,208 × 0.53125 =
  1,446.456 GB), non-expert (57,179,884,544 × 2 + 11,122,432 × 4 = 114.404 GB),
  total 1,560.860 GB; the MXFP8-overlay row (18,790,787,072 params ×
  (2.0 − 1.03125), 18.204 GB saved → 1,542.657 GB); and the "ceiling" overlay
  row, confirmed to convert **only the BF16 non-expert params** (F32 tensors
  stay F32) — 59.011 GB non-expert, 1,505.467 GB total, 55.393 GB saved —
  reproducing the document's 1,505.47 / 55.39 to the third decimal once that
  F32-exempt assumption is made explicit.
- §1.3 fit table: `kv_budget/GPU = 0.90×96e9 − weights/n − 4e9` for both the
  native and MXFP8-overlay weight totals at n ∈ {1,2,4,8,16,32,64} — every
  cell matches (native: −1,478.5 … +58.0 GB; overlay: −1,460.3 … +58.3 GB).
- §1.4 max-concurrency tables: `floor(n×kv_budget/GPU / (ctx×kv_bytes/tok +
  S×449,372,160))` for both the native+overlay (n=32,64) and QSRT-K2 2-bit
  (n=8,12,16,32) scenarios, S=5, at BF16 (27,648 B/tok) and FP8 (13,824 B/tok)
  KV, ctx ∈ {8K,32K,128K,1M} — all 32 cells match exactly, including the
  QSRT-K2 n=8 "infeasible (weights) (−12.29 GB/GPU)" row.
- §3.1 decode-step calibration: bytes/step at b=1/ctx=256 (25.83 + 93.85 +
  0.004 + 0.449 = 120.1 GB), memory time at MBU 1.0 (4.19 ms), the 93×2-collective
  floor at 24.1 µs (4.48 ms), residual (13.44 ms) and the back-solved MBU
  (4.19/13.44 = 0.312) — all reproduce exactly from the stated 16×WS/1.792 TB/s
  inputs.
- §3.2 bytes-per-decode-step table: `distinct_experts(b) = 896×(1−(1−16/896)^b)`
  and the resulting expert/non-expert/KV/state GB columns for b ∈
  {1,8,32,64,128,256} — all six rows match to the stated precision.
- §3.3 S1/S2/S3 TPOT, tok/s/GPU, aggregate tok/s and TTFT tables: recomputed
  `decode_step(b,ctx)` with the calibrated MBU 0.312 / MFU_dec 0.10 at Server
  Edition BW (1.597e12 B/s), and the least-squares prefill-rate fit
  (`1/rate = a + b·T` on the three measured 16×WS points, a=2.5551e-4,
  b=3.9143e-10) scaled by 2494/2617 for the Server Edition clock — every TPOT,
  throughput and TTFT cell (0 % and 90 % prefix-cache hit) reproduces to
  within 1 %, including the fitted 3,707 tok/s aggregate prefill rate and the
  733 s / 12.2 min 1 M-token prefill figure.
- §4.2–§4.3 cost tables: `cost_per_1M = n_gpus×price/(tokens/s×3600)×1e6` for
  every S1/S4/conc-1/DFlash row at all three price tiers ($1.80 / $4.143 /
  $1.30), and the blended-cost formula (`0.4125×input_cost + 0.25×output_cost`,
  derived from METHODOLOGY §6's 75/25 split with a 50 % cache-hit rate at 10 %
  of uncached cost) for every blended row, plus the 90 %-hit-rate sensitivity
  ($9.91 → $9.33) — all reproduce exactly.
- §4.5 break-even $/GPU-hour and break-even-utilisation figures: reproduce
  exactly once the input-token rate is taken as **8× the realized output
  throughput at that operating point** (the S1 scenario's own 4,096:512 = 8:1
  prompt:output ratio), not the theoretical max prefill throughput — e.g. conc
  11 no-spec: revenue/hour = (8×221.7×$3.00 + 221.7×$15.00)/1e6×3600 = $31.13,
  ÷16 GPUs = $1.95/GPU-h (matches); all-cache-hit $0.87/GPU-h, DFlash
  $3.83/$1.71, conc-1 $0.45/$0.20, and break-even utilisation 92.5 % / 207 % /
  47 % / 105 % all reproduce exactly on the same basis.
- §4.5 cross-GPU cost table (B300/MI355X/RTX rows): `cost = n×price/(tok/s/GPU
  × n × 3600)×1e6` from the stated tok/s/GPU and $/GPU-h — all five rows
  ($9.31, $13.25, $26.43, $34.89, $26.86) reproduce exactly.
- §0 verdict figures cross-checked against the tables they summarize (cost
  ranges, batch-1 decode/prefill measurements, KV pool size, GPU count/topology
  step) — all match.
- Cross-checked pinned inputs — weight bytes, MLA KV bytes/token (27,648 BF16 /
  13,824 FP8), KDA state (449,372,160 B/slot, S=5), dense (not sparse) TFLOPS
  (480 BF16 / 960 FP8 / 1,920 FP4 Server Edition), HBM BW (1.597 TB/s SE),
  usable-HBM convention (0.90×cap), and cloud-pricing rows ($1.80 / $4.143 /
  $1.30) — against `architecture.md` §3–6 and `gpus/rtx6000-pro.md` §2, §3, §8,
  §9. All consistent; dense TFLOPS (never the 1/2/4 PFLOPS sparse marketing
  figures) are used throughout §3 and §9's roofline arithmetic.

**Contradiction with a foundation doc (already disclosed in this document's
own header and §5.1, restated here per the audit brief):** this document's
verdict — a qualified 16-GPU deployment — contradicts
[`gpus/rtx6000-pro.md` §9g](../../gpus/rtx6000-pro.md), which states Kimi-K3
"does not fit an 8-card box, and it is not close … at least 19 cards (topology
step: 32)" under METHODOLOGY §3's standard 0.90-usable/4 GB-workspace
convention. Both are arithmetically self-consistent on their own terms — the
GPU doc's 19-card figure is METHODOLOGY-standard (`ceil(1,560.86e9 /
(86.4e9−4e9)) = 19`, verified above) and the 16-GPU figure depends on an
unverified online MXFP8 weight-only overlay (§1.2, §6 items 1–2) sourced from
an unaffiliated community fork, not a vendor recipe. Neither this audit nor the
document itself resolves the disagreement; it is recorded, not adjudicated,
per METHODOLOGY §8's rule.

**No numbers in this document were changed.** All recomputation matched the
stated tables within normal rounding tolerance (≤ 1 %, and exact to 3+
significant figures in most cases).
