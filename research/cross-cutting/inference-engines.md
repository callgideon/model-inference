# Inference engines: support matrix per GPU and per model

**Research date: 2026-09-19.** Follows [`research/METHODOLOGY.md`](../METHODOLOGY.md) —
every number carries an inline `[src](url)` or the marker **⚠️ TO BE VERIFIED** with the
estimation method stated. `est.` = derived from the methodology formulas. `meas.` =
published measurement. Marketing claims are labelled as such. Dense vs. sparse TFLOPS and
SXM vs. PCIe are never silently mixed (this document is about engines, so raw FLOPS live in
the per-GPU documents; where a number appears here it is the engine's own statement).

> **Scope note.** This document answers *"which engine, at which version, on which GPU, will
> actually start and serve model X, with which flags, and what breaks."* Throughput, cost
> and KV-sizing numbers belong to the per-model and per-GPU documents; only engine-reported
> figures appear here.

---

## 0. Executive summary

| Engine | Latest numbered release (2026-09-19) | Released | Verdict for the five repo models |
|---|---|---|---|
| **vLLM** | **0.29.0** [src](https://pypi.org/pypi/vllm/json) / [src](https://github.com/vllm-project/vllm/releases) | 2026-09-09 | Broadest coverage. Kimi-K3 since **v0.27.0**, Qwen3.8-27B (`qwen3_5`) since **v0.17.0** (corrected 2026-09-19; was "≤ v0.24.0"), DeepSeek-V4.1-Flash **only on `main` / nightly** (recipe declares `min_vllm_version: 0.30.0`, `nightly_required: true`) |
| **SGLang** | **0.5.20** [src](https://pypi.org/pypi/sglang/json) / [src](https://github.com/sgl-project/sglang/releases) | 2026-09-18 | Kimi-K3 since **v0.5.17**, Qwen3.8-27B since **v0.5.9** (corrected 2026-09-19; was "≤ v0.5.15"), DeepSeek-V4.1-Flash **not in any numbered release** — preview image `lmsysorg/sglang:dev-dsv41` only |
| **TensorRT-LLM** | **1.2.1** stable; **1.3.0rc27** pre-release (2026-09-17) [src](https://pypi.org/pypi/tensorrt-llm/json) | — | Kimi-K3 supported (**Blackwell SM100 family only**, build-from-source); DeepSeek-**V4** yes, DeepSeek-**V4.1** **absent**; `Qwen3_5ForConditionalGeneration` supported |
| **NVIDIA Dynamo** | **1.5.0** (2026-09-19) [src](https://pypi.org/pypi/ai-dynamo/json); per-model dev tags `v1.6.0-deepseek-v4.1-flash-dev.1`, `v1.5.0-kimi-k3-dev.1` [src](https://github.com/ai-dynamo/dynamo/releases) | — | Orchestration layer over vLLM/SGLang/TRT-LLM. Both DS-V4.1-Flash and Kimi-K3 have **experimental, non-QA-gated** dev builds |
| **TokenSpeed** | version not published on the docs pages ⚠️ **TO BE VERIFIED** | — | Third engine named by **both** the Kimi-K3 and Qwen3.8 model cards. Has K3 + Qwen3.8-27B + DeepSeek-V4 **and DeepSeek-V4.1-Flash** recipes — the V4.1 section ships a `tokenspeed serve deepseek-ai/DeepSeek-V4.1-Flash` command, a dedicated FlatKV attention backend and a per-commit GB300 Slurm 1P1D CI job [src](https://lightseek.org/tokenspeed/recipes/models) |
| **llama.cpp / Ollama** | — | — | Only Qwen3.8 is present in the Ollama library (`27b` tag, vision/tools/thinking) [src](https://ollama.com/library/qwen3.8). Nothing for DS-V4.1, K3 or Marlin-2B |

**The three things that will bite you first:**

1. **DeepSeek-V4.1-Flash is not in any numbered release of vLLM or SGLang.** vLLM `main` carries
   `vllm/models/deepseek_v41/` and SGLang `main` carries `srt/configs/deepseek_v41.py`, but
   neither vLLM `v0.29.0` nor SGLang `v0.5.20` does (re-verified by HTTP probe of the release
   tags on 2026-09-19, §6.1). Plan on nightly/preview images and re-verify weekly. TokenSpeed
   *does* publish a V4.1-Flash recipe, but publishes no version number at all, so "numbered
   release" is not a question that can be asked of it (§2.5).
2. **Kimi-K3 needs ≥ 8 large-HBM Blackwell GPUs (B300 268 GB, GB300 288 GB, MI355X 288 GB),
   16 B200 (180 GB) or GB200 NVL4 (192 GB), or 32 Hopper-80GB.** That is not a tuning choice
   — it is what the 1,680 GB `vram_minimum_gb` forces, and every published recipe lands
   exactly on that ladder (§6.3). Capacities are **as deployed** per METHODOLOGY §8: B200 is
   180 GB, not 192, and HGX/DGX B300 is 268 GB, not 288.
3. **Only one of the five models runs on a single GPU: Qwen3.8-27B.** It is also the only one
   with an RTX PRO 6000 Blackwell profile. DS-V4.1 and Kimi-K3 have **no** RTX PRO 6000
   profile at all, and **none of the three large models has an A100 profile on any engine**
   (verified by 404 on every `a100` recipe endpoint, §3.2).

---

## 1. Method and what "supported" means here

Four levels are used throughout, because "supported" collapses three very different states:

| Level | Meaning | How it was established |
|---|---|---|
| **Released** | The architecture is in a numbered, pip-installable release | HTTP probe of the release tag in the engine's git tree (e.g. `https://raw.githubusercontent.com/sgl-project/sglang/v0.5.20/python/sglang/srt/models/kimi_k3.py` → HTTP 200) |
| **Main only** | Code is on `main`; ships in a nightly/preview container | Same probe against `main` returns 200 but the tag returns 404 |
| **Verified recipe** | The engine project publishes a hardware-specific launch command it claims to have run | `verified: true` in the SGLang cookbook cell, or `"hardware": {"h200": "verified"}` in the vLLM recipe JSON |
| **Absent** | Architecture name does not appear in the engine's model registry or support matrix | grep of `registry.py` / `supported-models.md` |

Registry/tag probes are the strongest available evidence and are quoted with exact URLs in
§6. Everything a vendor *says* about performance is labelled as its claim, not as a
measurement I made.

---

## 2. The engine landscape as of 2026-09-19

### 2.1 vLLM — 0.29.0

Release cadence has been roughly fortnightly through 2026 [src](https://pypi.org/pypi/vllm/json):

| Version | Upload date |
|---|---|
| 0.25.0 | 2026-07-11 |
| 0.26.0 | 2026-07-25 |
| 0.27.0 | 2026-08-10 |
| 0.27.1 | 2026-08-11 |
| 0.28.0 | 2026-08-26 |
| **0.29.0** | **2026-09-09** |

There is **no 0.30.0 on PyPI as of 2026-09-19**, yet the DeepSeek-V4.1-Flash recipe declares
`"min_vllm_version": "0.30.0", "nightly_required": true` [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash.json).
Read that as *"the next release, whenever it lands — until then, nightly."*

**Engine architecture.** The V1 engine is the only engine; the V0 path is gone from the
current docs. Two newer switches matter for the repo models:

| Switch | What it does | Where it is required |
|---|---|---|
| `VLLM_USE_V2_MODEL_RUNNER=1` | Model Runner v2 | Set by the Kimi-K3 Blackwell/Hopper profiles and by the DS-V4.1 **H100** profile [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash.json) |
| `VLLM_USE_RUST_FRONTEND=1` | Rust OpenAI frontend, now the command-builder default | Every recipe emits it; "Switch to Python if you encounter unsupported features or compatibility issues" [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash) |

**Prefix caching** is on by default and block-hash based [src](https://github.com/vllm-project/vllm/blob/main/docs/design/prefix_caching.md).
For hybrid (attention + Mamba/linear) models a new flag appears: `--prefix-match-unit 128`.
The Kimi-K3 recipe explains why — "its MLA attention layers and KDA (Mamba-like) layers form
two KV-cache groups under the hybrid KV-cache manager. With prefix caching on, vLLM pads the
attention block size up to match the Mamba state page, so both groups resolve to one (large)
block size and the default prefix-cache hit boundary lands on that block — very coarse."
[src](https://recipes.vllm.ai/moonshotai/Kimi-K3.json)

**Chunked prefill** is controlled by `--max-num-batched-tokens` (the per-iteration token
budget) and is assumed on. DS-V4.1's own NVFP4 card still passes `--enable-chunked-prefill`
explicitly [src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md).

**CUDA graphs** are configured through `--compilation-config` with `cudagraph_mode` and
`cudagraph_capture_sizes`; the B300 DS-V4.1 profile pins an explicit 26-entry capture ladder
up to 8190 and `--max-cudagraph-capture-size 8190`
[src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/b300.json). Two escape
hatches exist for models that cannot take the default `FULL_AND_PIECEWISE`:
`cudagraph_mode: FULL_DECODE_ONLY` (Kimi-K3 on ROCm and Ascend) and
`VLLM_USE_BREAKABLE_CUDAGRAPH=1` (DS-V4.1 on ROCm — "DeepSeek-V4.1-Flash does not support
`torch.compile`, and the ROCm sparse SWA backend only supports uniform-batch CUDA graphs.
Without breakable CUDA graphs, default `FULL_AND_PIECEWISE` dies at capture."
[src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash)).

**Speculative decoding** is configured with a single `--speculative-config` JSON blob.
Methods relevant to this repo, taken from the vLLM registry's draft-model section
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/registry.py):

| `method` | Registry entry | Used by |
|---|---|---|
| `mtp` | `DeepSeekMTPModel`, `Qwen3_5MTP`, `Qwen3_5MoeMTP`, `Gemma4MTPModel`, … | Qwen3.8-27B (in-checkpoint head) |
| `dspark` | `DSparkDraftModel`, `DSparkV41DraftModel`, `K3DSparkModel`, `Qwen3DSparkModel` | DS-V4.1-Flash, Kimi-K3, Qwen3.8-27B |
| `dflash` | `DFlashQwen3ForCausalLM`, `DFlashLagunaForCausalLM` | Qwen3.8-27B (external `incoai/Qwen3.8-27B-DFlash2`) |
| `eagle`/`eagle3` | `Eagle3LlamaForCausalLM` family | Not used by any repo model |

Note the **DSpark-specific knob** `enable_adaptive_verification` — "On NVIDIA, adaptive
verification scores each (request, position) slot and only high-scoring slots are verified,
up to a startup-profiled compute budget. On AMD, vLLM currently refuses that flag"
[src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash.json).

**Expert parallelism.** `--enable-expert-parallel` with `EP_SIZE = TP_SIZE × DP_SIZE`, and
`--all2all-backend` chooses the collective
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/serving/expert_parallel_deployment.md):

| `--all2all-backend` | Use case |
|---|---|
| `allgather_reducescatter` | Default; works with any EP+DP configuration |
| `deepep_high_throughput` | Multi-node prefill, grouped GEMM continuous layout |
| `deepep_low_latency` | Multi-node decode, CUDA-graph compatible, masked layout |
| `flashinfer_nvlink_one_sided` / `..._two_sided` | MNNVL (GB200/GB300 NVL) systems |

The Kimi-K3 guide adds a `deepep_v2` backend for RDMA and notes it "requires NCCL >= 2.30.4.
PyTorch ships an older NCCL, so you must upgrade it before building or running DeepEP"
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/serving/expert_parallel_deployment.md),
and that "DeepGEMM MegaMoE is not compatible with cross-node RDMA"
[src](https://recipes.vllm.ai/moonshotai/Kimi-K3.json). **pplx** is not named in the current
vLLM EP doc's backend table; it survives as an SGLang `--moe-a2a-backend` option (§2.2).

**Resolved 2026-09-19: `pplx` has been removed from vLLM.** The `All2AllBackend` literal in
`vllm/config/parallel.py` at the `v0.29.0` tag still *accepts* the string, but the validator
immediately discards it:

```python
if self.all2all_backend in ["pplx", "naive"]:
    logger.warning(
        "The '%s' all2all backend has been removed. "
        "Falling back to 'allgather_reducescatter'.", self.all2all_backend)
    self.all2all_backend = "allgather_reducescatter"
```

[src](https://raw.githubusercontent.com/vllm-project/vllm/v0.29.0/vllm/config/parallel.py).
There is no `PplxAll2AllManager` in `vllm/distributed/device_communicators/all2all.py` on that
tag [src](https://raw.githubusercontent.com/vllm-project/vllm/v0.29.0/vllm/distributed/device_communicators/all2all.py).
The same 0.29.0 literal also carries three backends the published docs table omits —
`mori_high_throughput`, `mori_low_latency` and `nixl_ep` (the last backed by
`NixlEPAll2AllManager`) — so treat the docs table as a subset of what the config accepts.

**DP-attention** in vLLM is expressed as `--data-parallel-size` with `--tensor-parallel-size 1`:
"When `TP = 1`: Attention weights are **replicated** across all DP ranks."
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/serving/expert_parallel_deployment.md)

**PD disaggregation and KV connectors.** vLLM labels disaggregated prefill *experimental* and
is explicit that "Disaggregated prefill DOES NOT improve throughput" — it is a TTFT/ITL
tuning and tail-ITL control device. Nine connectors ship
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/disagg_prefill.md):

`ExampleConnector`, **`LMCacheConnectorV1`** (+ `LMCacheMPConnector` multi-process mode with a
standalone `lmcache server`), **`NixlConnector`** (fully async send/recv; backends `UCX`,
`GDS`), `MooncakeConnector`, `MoRIIOConnector` (ROCm only), `MultiConnector` (ordered list),
**`OffloadingConnector`** (CPU + filesystem tiers via `block_size` / `cpu_bytes_to_use`),
`FlexKVConnectorV1`.

A 2026 addition worth knowing for agentic traffic: the decode stage can **reuse the prefill
stage's token ids** via `kv_transfer_params["prompt_token_ids"]`, skipping a second templating
+ tokenization pass — experimental, `/v1/chat/completions` only [same src].

### 2.2 SGLang — 0.5.20

Cadence, also roughly fortnightly [src](https://pypi.org/pypi/sglang/json):
0.5.17 (2026-08-08) → 0.5.18 (2026-08-21) → 0.5.19 (2026-09-04) → **0.5.20 (2026-09-18)**.

**RadixAttention / prefix caching** is the default; `--disable-radix-cache` turns it off (and
is *required* on the DS-V4.1 MI350X recipe, which then makes HiCache unavailable — see §3.9).
Eviction policy and session-aware variants have their own docs pages
[src](https://docs.sglang.io/llms.txt).

**HiCache** tiers KV across L1 (GPU) / L2 (host pinned) / L3 (Mooncake storage). Enabled with
`--enable-hierarchical-cache`, sized by ratio (`--hicache-ratio 2`), with write policies
`write_through` (upstream default), `write_through_selective`, `write_back`
[src](https://docs.sglang.io/cookbook/autoregressive/DeepSeek/DeepSeek-V4_1.md). For hybrid
models it tiers **both** the paged KV and the Mamba/KDA recurrent state
[src](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3.md).

**Expert parallelism.** `--ep-size`, plus `--moe-a2a-backend` with a wider menu than vLLM's:
`deepep`, `mooncake`, `nixl`, `mori` (ROCm), `flashinfer`, `ascend_fuseep`, **`pplx`** (Hopper),
or `none`; and `--moe-runner-backend` (`auto`, `triton`, `deep_gemm`, `cutlass`, `flashinfer_*`,
`marlin`). `--deepep-mode` selects `normal` (prefill throughput) vs `low_latency` (decode,
CUDA-graph-compatible), with `auto` switching at runtime. **EPLB** is `--enable-eplb` plus
`--eplb-*`, computing expert placement from activation statistics with periodic rebalancing.
Constraint: "Most advanced backends require `ep_size = tp_size`; hybrid configurations need
the basic `none` backend." Overlap: `--enable-two-batch-overlap`, `--enable-single-batch-overlap`
[src](https://docs.sglang.io/docs/advanced_features/expert_parallelism.md).

**Speculative decoding.** The generic docs page lists `EAGLE`/`EAGLE3`, `MTP`, `UNO`,
`DFLASH`, `STANDALONE`, `NGRAM` and their constraints — DFLASH "requires `pp_size == 1`;
disables DP attention, overlap scheduler, mixed chunked prefill"; NGRAM is CUDA-only and
incompatible with `--enable-dp-attention`
[src](https://docs.sglang.io/docs/advanced_features/speculative_decoding.md). **`DSPARK` does
not appear on that page**, yet it is the algorithm the DS-V4.1, Kimi-K3 and Qwen3.8-27B
cookbooks all emit (`--speculative-algorithm DSPARK`). Treat the generic page as stale: the
cookbook pages are the live source. There is also an "Adaptive Speculative Decoding" page in
the index [src](https://docs.sglang.io/llms.txt).

**PD disaggregation.** `--disaggregation-mode prefill|decode`,
`--disaggregation-transfer-backend mooncake|nixl|ascend`, `--disaggregation-bootstrap-port`,
`--disaggregation-ib-device` (Mooncake only), `--disaggregation-decode-extra-slots`; fronted by
`python -m sglang_router.launch_router --pd-disaggregation --prefill http://HOST:PORT --decode http://HOST:PORT`
[src](https://docs.sglang.io/docs/advanced_features/pd_disaggregation.md). The generic page
states "**Speculative decoding unsupported** — router processes exactly one prefill/decode pair
per request" and "**Hybrid models incompatible** — KV cache dtype must match across prefill/decode";
the Kimi-K3 cookbook contradicts the second half for K3 specifically (PD moves "**both** the
paged MLA KV and the KDA recurrent state") and the DS-V4.1 cookbook the first half only
partially ("PD and speculative decoding cannot be combined"). ⚠️ **TO BE VERIFIED** — whether
the generic "hybrid models incompatible" line has simply not been updated for K3.

**EPD disaggregation** (encoder / prefill / decode) has its own page and is supported for K3
on the public `kimi-k3` branch: "`--encoder-only` vision role and a `--language-only` prefill
role; add the normal decode role for full EPD"
[src](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3.md).

**Decode context parallelism (DCP)** — `--dcp-size`, `--dcp-comm-backend` — shards the
TP-replicated MLA KV across the TP group. This is SGLang's distinctive lever for hybrid
long-context models and is discussed per-GPU in §3.

**NVFP4 recipes.** SGLang selects `--moe-runner-backend flashinfer_trtllm` for NVFP4 routed
experts, and the cookbook is blunt about the hardware gate: "The `nvidia/Kimi-K3-NVFP4`
checkpoint needs Blackwell: its routed experts run on FlashInfer TRT-LLM NVFP4 kernels (SiTU),
which do not exist for Hopper or AMD."
[src](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3.md)

### 2.3 TensorRT-LLM — 1.2.1 stable / 1.3.0rc27

The TensorRT (graph-compiler) backend is **removed**; PyTorch is the sole execution backend as
of 1.2, and two-model speculative decoding was removed in favour of one-model implementations
[src](https://nvidia.github.io/TensorRT-LLM/release-notes.html). Hardware: Blackwell and
Hopper; Volta removed; B300/GB300 introduced in 1.1; DGX Spark beta in 1.2.

Release channel matters here. PyPI shows stable `1.2.1` alongside a long `1.3.0rcNN` line
running to `1.3.0rc27` (2026-09-17) [src](https://pypi.org/pypi/tensorrt-llm/json). NVIDIA's
own Dynamo builds pin release candidates, not the stable (`1.3.0rc25` for the Gemma-4 build,
`1.3.0rc24` for Nemotron) [src](https://github.com/ai-dynamo/dynamo/releases). Treat
`1.3.0rcNN` as the real target for anything modern.

Feature support for the architectures in this repo, from the PyTorch-backend matrix
[src](https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/docs/source/models/supported-models.md):

| Architecture | Overlap sched. | CUDA graph | Attention DP | Disagg serving | Chunked prefill | Spec decoding | KV reuse | Guided decoding |
|---|---|---|---|---|---|---|---|---|
| `DeepseekV4ForCausalLM` [^11] | Yes | Yes | Yes | **Untested** | Yes | MTP | Yes | Yes |
| `KimiK3ForConditionalGeneration` [^15][^17] | Yes | Yes | Yes | Yes | Yes | **DSpark** | Yes | Yes |
| `Qwen3_5MoeForCausalLM` | Yes | Yes | Yes | Yes | Yes | MTP | Yes | Yes |
| `DeepseekV41…` | — | — | — | — | — | — | — | **Architecture absent from the matrix** |

Footnotes worth quoting verbatim:

- `[^11]` "DeepSeek-V4 is only supported on Blackwell GPUs (`SM100+`)."
- `[^15]` "Kimi K3 is only supported on NVIDIA Blackwell GPUs (`SM100` family) … DEP16
  (`enable_attention_dp: true`) replicates the BF16 non-expert weights on every rank (114 GB)
  on top of the MXFP4 routed experts at 16-way expert parallelism (90 GB), needing 210 GB per
  rank, so it requires GB300-class per-GPU memory. TEP16 (`enable_attention_dp: false`) shards
  those non-expert weights instead and needs 115 GB per rank; it is validated end-to-end on
  GB200 (`SM100`) at 16 GPUs."
- `[^17]` "Kimi K3 has no MTP or EAGLE-3 head, and its DSpark checkpoints are not compatible
  with plain `DFlash`."

Multimodal matrix: `Qwen3_5ForConditionalGeneration` — modality `L + I + V` (language, image,
**video**), chunked prefill *Untested*, KV reuse *No*, EPD disaggregated serving *Yes*; it also
supports the **multimodal encoder side stream** and **multimodal embeddings cache**
optimizations [same src].

`trtllm-serve` is the OpenAI-compatible server; the Kimi-K3 guide instead drives multi-node
jobs through `trtllm-llmapi-launch` under Slurm with an `EXTRA_LLM_API_FILE` YAML
[src](https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/docs/source/deployment-guide/deployment-guide-for-kimi-k3-on-trtllm.md).
**Wide-EP** as a named feature does not appear in the current supported-models doc; what is
documented is `moe_expert_parallel_size` with `enable_attention_dp` (the DEP/TEP distinction
above). ⚠️ **TO BE VERIFIED** — whether "wide-EP" still exists as a distinct TRT-LLM feature
name in 1.3.0rcNN release notes.

### 2.4 NVIDIA Dynamo — 1.5.0

Dynamo is explicitly *not* an engine: "it doesn't replace SGLang, TensorRT-LLM, or vLLM, it
turns them into a coordinated multi-node inference system"
[src](https://github.com/ai-dynamo/dynamo/blob/main/README.md). Backend feature matrix, from
that README:

| Feature | SGLang | TensorRT-LLM | vLLM |
|---|:--:|:--:|:--:|
| Disaggregated serving | ✅ | ✅ | ✅ |
| KV-aware routing | ✅ | ✅ | ✅ |
| SLA-based planner | ✅ | ✅ | ✅ |
| **KVBM** | **🚧** | ✅ | ✅ |
| Multimodal | ✅ | ✅ | ✅ |
| Tool calling | ✅ | ✅ | ✅ |

**KV transfer** goes through **NIXL** — "direct GPU-to-GPU transfer using the optimal available
transport (NVLink, InfiniBand/UCX, etc.)", non-blocking. **KV-aware routing** is done by the
`PrefillRouter` "based on cache overlap scores and load". Backend asymmetry worth planning
around: "**SGLang**: Uses asynchronous transfers via `bootstrap_info`, enabling decode to start
while KV transfer proceeds in parallel. **vLLM & TensorRT-LLM**: Run prefill synchronously;
decode waits for completion"
[src](https://docs.nvidia.com/dynamo/knowledge-base/concepts/system-architecture/disaggregated-serving).
xPyD is runtime-reconfigurable.

**KVBM.** The public docs URL for the KV Block Manager currently resolves to a *KV cache
offloading* page describing three offload backends — **LMCache**, **HiCache** and **FlexKV** —
over "Host and persistent L2 adapters" / "Host, SSD, cloud storage"
[src](https://docs.nvidia.com/dynamo/components/kvbm). The G1/G2/G3/G4 tier naming and
`DYN_KVBM_*` environment variables are **⚠️ TO BE VERIFIED**: they are not present on the page
that serves at that URL today, and `docs/architecture/kvbm_*.md` returns 404 in the repo.

Dynamo's headline results are **vendor claims**, not measurements made here: "**7x** higher
throughput per GPU — DeepSeek R1 on GB200 NVL72 w/ Dynamo vs B200 without ([InferenceX](https://inferencex.semianalysis.com/))";
"**2x** faster time to first token — KV-aware routing, Qwen3-Coder 480B (Baseten)"
[src](https://github.com/ai-dynamo/dynamo/blob/main/README.md).

### 2.5 TokenSpeed — the third engine both model cards name

Neither the DeepSeek nor the NVIDIA card mentions it, but **the Kimi-K3 and Qwen3.8-27B cards
both list TokenSpeed alongside vLLM and SGLang as a recommended engine**
[src](https://huggingface.co/moonshotai/Kimi-K3/raw/main/README.md)
[src](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/README.md). It is also visible inside
vLLM itself: `TOKENSPEED_MLA` is a registered vLLM MLA prefill/decode attention backend
("vLLM 0.27.1 registers three MLA prefill backends — `FLASHINFER`, `TRTLLM_RAGGED` and
`TOKENSPEED_MLA`") [src](https://recipes.vllm.ai/moonshotai/Kimi-K3.json).

Coverage relevant here [src](https://lightseek.org/tokenspeed/recipes/models):

| Model | TokenSpeed recipe? | Notes |
|---|---|---|
| Kimi-K3 | **Yes** — NVIDIA (8× B300, TP8/EP8, `--moe-backend flashinfer_trtllm`) and AMD (8× gfx950, `--attention-backend mla`, Gluon SiTU MoE) | Needs `pip install flash-linear-attention` for the KDA layers on NVIDIA. "The fused MoE path needs a Blackwell GPU (B200/B300); on other NVIDIA platforms use `--moe-backend triton`." On Hopper: "explicitly select `--attention-backend flashmla` or `mla` with `--kv-cache-dtype bfloat16`" |
| Qwen3.8-27B | **Yes** — single GPU, `Qwen/Qwen3.8-27B-FP8`, `--attention-backend trtllm`, self-speculative MTP pointing at the same checkpoint, `--speculative-num-steps 3` | |
| DeepSeek-V4-Flash / V4-Pro | Yes | Requires `tokenspeed-deepgemm>=2.5.0.post20260629` and `tokenspeed-flashmla`; auto-selects `--reasoning-parser deepseek_v31`, `--tool-call-parser deepseek_v4`, `block_size=256`. V4-Flash is 4× B200 (SM100) DP4+EP, `--moe-backend mega_moe`; V4-Pro is 8× B200 TP8, `--moe-backend flashinfer_trtllm` |
| **DeepSeek-V4.1-Flash** | **Yes** — corrected 2026-09-19 | The page has a "DeepSeek V4.1-Flash" section: "DeepSeek V4.1 (`deepseek_v41`) is served by its own **FlatKV** attention backend with a four-group KV cache: the global KV chains, the SWA rows and the compressor tails. The recipe declares the last two *replayable*: they never enter the prefix cache, and a prefix hit re-feeds the cached prefix's last 128 tokens … (SWA bounded replay)". The CED decoder (layers 20–39) forces `prefill_graph=False` / `--disable-prefill-graph`; decode CUDA graphs are unaffected. Published command is `tokenspeed serve deepseek-ai/DeepSeek-V4.1-Flash --tensor-parallel-size 8 --enable-expert-parallel --moe-backend marlin --dtype bfloat16 --max-model-len 32768 --disable-prefill-graph --disable-kvstore`, plus `--speculative-algorithm DSPARK` for same-checkpoint DSpark. There is a per-commit **GB300 Slurm 1P1D CI** job (`deepseek-v4.1-flash-pd-1p1d-dspark-evalscope-gsm8k-gb300-slurm.yaml`, TP4 prefill + TP4 decode on two 4-GPU nodes, gated at GSM8K ≥ 0.90 on 100 samples with EvalScope 1.11.1) |
| Marlin-2B | No — zero occurrences of "Marlin" on the page | |

Containers: `lightseekorg/tokenspeed:latest` (NVIDIA), `lightseekorg/tokenspeed-amd:latest`.
⚠️ **TO BE VERIFIED** — TokenSpeed's version number, licence and release cadence; the recipes
page publishes neither, and the docs site does not expose a changelog at the URLs reachable
from it.

### 2.6 llama.cpp / Ollama (brief, as scoped)

- **llama.cpp**: the top-level README's quickstart example is `llama serve -hf ggml-org/Qwen3.5-0.8B-GGUF`
  [src](https://raw.githubusercontent.com/ggml-org/llama.cpp/master/README.md), i.e. the
  Qwen3.5/3.8 hybrid family is in the GGUF ecosystem at small sizes. No DeepSeek-V4.x,
  Kimi-K3 or Marlin reference appears. For a 552B/2.8T MoE with FP4 experts, engram hash
  tables and a custom sparse indexer, llama.cpp is not a realistic target and should not be
  planned for.
- **Ollama**: `ollama run qwen3.8` exists, tagged `vision`, `tools`, `thinking`, size `27b`,
  2.2M downloads, "Updated 1 month ago"; a sibling `qwen3.8-flash-next` also exists
  [src](https://ollama.com/library/qwen3.8). Nothing for DS-V4.1, Kimi-K3 or Marlin-2B
  [src](https://ollama.com/search?q=qwen3.8).

**Planning rule:** llama.cpp/Ollama are a laptop/edge path for Qwen3.8-27B only. Everything
else in this repo is a datacenter engine problem.

---

## 3. Per-GPU engine support

### 3.1 The roster, and what each engine needs from it

| GPU | CUDA arch / ISA | Per-GPU memory as the recipes model it | FP8 tensor cores | FP4 tensor cores |
|---|---|---|---|---|
| A100 SXM/PCIe | SM80 | 80 GB (40 GB variant exists) | **No** | **No** |
| H100 SXM | SM90a | 80 GB → `"vram_gb": 640` for 8 [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/h100.json) | Yes | No |
| H200 SXM | SM90a | 141 GB → `"vram_gb": 1128` for 8 [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/h200.json) | Yes | No |
| B200 | SM100 | 192 GB per SXM module (SGLang's catalogue says `vram: "192GB"`), but `"vram_gb": 1440` for 8 = **180 GB/GPU**, and the same endpoint's `hardware_profile.description` reads verbatim *"NVIDIA B200 SXM **180 GB** HBM3e · 8-GPU HGX B200 node"* [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/b200.json) — NVIDIA's DGX B200 publishes "GPU Memory 1,440 GB total" [src](https://www.nvidia.com/en-us/data-center/dgx-b200/), and AMD's competitive table lists "B200 SXM5 180GB" [src](https://www.amd.com/en/products/accelerators/instinct/mi350/mi355x.html). **Plan on 180 GB/GPU, not 192** (METHODOLOGY §8). | Yes | Yes |
| B300 (HGX / DGX / AWS p6-b300) | SM103 | 288 GB marketing capacity, but `"vram_gb": 2144` for 8 = **268 GB/GPU**, and the endpoint's `hardware_profile.description` reads verbatim *"NVIDIA B300 SXM **268 GB** HBM3e · 8-GPU HGX B300 node (Blackwell Ultra)"* [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/b300.json) — matching NVIDIA's own DGX B300 "Total GPU Memory 2.1 TB" [src](https://www.nvidia.com/en-us/data-center/dgx-b300/) and HGX B300 "Total Memory 2.1 TB" [src](https://www.nvidia.com/en-us/data-center/hgx/). **Plan on 268 GB/GPU, 2,144 GB per 8-GPU node** (METHODOLOGY §8). Never merge this with the GB300 NVL row below. | Yes | Yes |
| GB200 NVL4 tray | SM100 | 192 GB × 4 = `"vram_gb": 768` [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/gb200.json) | Yes | Yes |
| GB300 NVL4 tray | SM103 | 288 GB × 4 = `"vram_gb": 1152`; description verbatim *"288 GB HBM3e/GPU · 4 GPUs (NVL72 tray unit)"* [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/gb300.json) — the NVL72 per-GPU figure (≈ 279 GB usable), **not** the 268 GB HGX B300 figure | Yes | Yes |
| RTX PRO 6000 Blackwell **Server Edition** | SM120 | 96 GB GDDR7, 1,597 GB/s [src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md) (1,792 GB/s is the **Workstation** Edition — do not mix; METHODOLOGY §8) | Yes | Yes |
| MI355X (CDNA4) | gfx950 | 288 GB → `"vram_gb": 2304` for 8 [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/mi355x.json) | Yes | Yes (a4w4 path) |

The `"vram_gb": 2144` for 8× B300 (268 GB/GPU) and `2304` for 8× MI355X (288 GB/GPU) are the
recipe site's own accounting and are reproduced as-is; they are below/at the marketing
capacity because they already net out some overhead.

**Resolved 2026-09-19 (was ⚠️ TO BE VERIFIED):** `2144` is not a recipe-site invention — it is
NVIDIA's own platform figure. DGX B300 publishes "Total GPU Memory 2.1 TB" for 8× Blackwell
Ultra [src](https://www.nvidia.com/en-us/data-center/dgx-b300/) and the HGX comparison table
gives "Total Memory 2.1 TB" (B300) vs "1.4 TB" (B200)
[src](https://www.nvidia.com/en-us/data-center/hgx/). 2144 / 8 = 268 GB and 1440 / 8 = 180 GB
are therefore the shipping per-GPU capacities; the 288 GB and 192 GB figures are per-module
marketing numbers. Re-verified 2026-09-19 by re-fetching the `hw/*.json` endpoints: their
`hardware_profile.description` strings spell the per-GPU capacity out in words — "B200 SXM
**180 GB** HBM3e", "B300 SXM **268 GB** HBM3e", "GB200 … **192 GB** HBM3e/GPU", "GB300 …
**288 GB** HBM3e/GPU", "H200 SXM **141 GB** HBM3e", "MI355X **288 GB** HBM3e". MI355X's
288 GB is AMD's own datasheet number and needs no adjustment
[src](https://www.amd.com/en/products/accelerators/instinct/mi350/mi355x.html). GB200 NVL4
(768/4 = 192) and GB300 NVL4 (1152/4 = 288) do land on the marketing figure — the NVL tray
accounting differs from the DGX/HGX board accounting, so do not assume one number covers both,
and in particular **never quote 288 GB for an HGX/DGX B300** (METHODOLOGY §8).

### 3.2 A100 (SM80) — unsupported for every model in this repo

There is **no A100 profile** for any of the three large models. Direct evidence:

```
HTTP 404  https://recipes.vllm.ai/Qwen/Qwen3.8-27B/hw/a100.json
HTTP 404  https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/a100.json
HTTP 404  https://recipes.vllm.ai/moonshotai/Kimi-K3/hw/a100.json
```
(probed 2026-09-19; every other GPU in §3.1 returns 200 where listed in §8)

SGLang's supported-hardware arrays exclude it too: DS-V4.1
`supportedHardware: ["h200","b200","b300","gb300","mi350x"]`, Kimi-K3
`["b300","gb300","b200","gb200","h200","h100","mi350x","mi355x","a3"]`, Qwen3.8-27B
`["h200","rtx6000","rtx5090","dgx-spark","gb300"]`
[src](https://docs.sglang.io/cookbook/autoregressive/DeepSeek/DeepSeek-V4_1.md)
[src](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3.md)
[src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md).

**Why:** all five checkpoints are FP8-or-narrower natively (DS-V4.1 FP8 dense + FP4 experts;
K3 MXFP4/MXFP8; Qwen3.8-27B ships BF16/FP8/NVFP4). SM80 has no FP8 tensor cores at all, so
every quantized GEMM would fall back to a weight-only dequant path, and the engines'
selected backends (`dsv4`, `flashinfer_mxfp4`, `flashmla`, `trtllm_mla`, `cutedsl_mla`) are
SM90+ or SM100+. Qwen3.8-27B in **BF16** on A100 is the one arithmetically conceivable case
(≈ 50.1 GiB of weights, est. from `config.json`, §6.4) — but no engine publishes a profile and
the GDN/linear-attention kernels are not claimed for SM80. **Treat A100 as out of scope and do
not size a deployment on it.**

A community SM80 backport exists and has been measured on 8×A800
([models/deepseek41f/a100.md](../models/deepseek41f/a100.md)); it does not change this
scoping — do not size a production deployment on it. That pair is carried as
`supported_now: false` in [`matrix/pairs.json`](../matrix/pairs.json).

### 3.3 H100 (SM90a, 80 GB)

| Model | vLLM | SGLang | TRT-LLM | Notes |
|---|---|---|---|---|
| DS-V4.1-Flash | `"h100": "verified"` [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash.json) | not in `supportedHardware` | No (V4.x is `SM100+`) | vLLM H100 needs **Engram CPU offload**: `--engram-config '{"cpu_offload":true}' --max-num-batched-tokens 4096 --gpu-memory-utilization 0.92`, plus `VLLM_USE_V2_MODEL_RUNNER=1` and `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` |
| Kimi-K3 | `"hardware"` map omits h100, but a `hw/h100.json` profile exists: **4 nodes × 8, TP32** | cookbook cell exists, `verified: false`, **4 nodes × 8, TP32/EP32** | No (SM100 family only) | vLLM H100: `--moe-backend marlin --attention-backend FLASHMLA --max-num-seqs 5 --max-model-len 32768 --gpu-memory-utilization 0.97 --disable-custom-all-reduce` — i.e. a 32-GPU deployment capped at 5 concurrent requests and 32K context |
| Qwen3.8-27B | `hw/h100.json` exists, TP1 | not in `supportedHardware` (H200 is) | `Qwen3_5ForConditionalGeneration` supported | Comfortable |
| Marlin-2B | Absent from all engines (§6.5) | — | — | — |

**H100 is the hardest Hopper target.** The SGLang K3 cookbook calls it out directly: "H100 4×8
— TP32/EP32, Marlin + FlashMLA — SM90a build of the K3 image; pin NCCL/Gloo to the same NIC on
all nodes; **least post-weight headroom (80 GB)**"
[src](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3.md). Required env for
that cell: `NCCL_CUMEM_ENABLE=1`, `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`,
`SGLANG_ENABLE_TP_MEMORY_INBALANCE_CHECK=0`, `SGLANG_K3_ATTN_RES_MODE=jit`,
`SGLANG_MOE_FUSED_GATE_RADIX=1`, `SGLANG_HOST_IP`, `NCCL_SOCKET_IFNAME`, `GLOO_SOCKET_IFNAME`.

### 3.4 H200 (SM90a, 141 GB)

| Model | vLLM | SGLang | TRT-LLM |
|---|---|---|---|
| DS-V4.1-Flash | `"h200": "verified"`, **TP4 single node**, no Engram offload needed | `verified: true`, **TP8/EP8**, `--attention-backend dsv4 --moe-runner-backend flashinfer_mxfp4 --enable-decoder-swa-bounded-replay` | No |
| Kimi-K3 | `"h200": "verified"`, **2 nodes × 8, TP16** | cell exists (`verified: false`), **TP16/EP16 + `--enable-symm-mem`, Marlin + FlashMLA**; High-Throughput widens to **4 nodes, TP32/EP32** at `--mem-fraction-static 0.90` with `--mamba-radix-cache-strategy extra_buffer_lazy` | No |
| Qwen3.8-27B | `hw/h200.json`, TP1 | `verified: true` (BF16 and FP8 cells) | Yes |

The SGLang Qwen3.8-27B page is explicit that **H200 gets no NVFP4**: "BF16 and FP8 only — the
card has no FP4 tensor cores, so an NVFP4 checkpoint's MLP would fall back to the Marlin W4A16
weight-only path, and all three NVFP4 cells are greyed out. The H200 recipes use 32768-token
prefill chunks … `--attention-backend fa3` is a valid alternative, measured slightly faster at
bs=1" [src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md).

**Note the vLLM/SGLang disagreement on DS-V4.1 TP width on H200**: vLLM emits `TP4`, SGLang
emits `TP8/EP8`. Both are "verified" by their own projects. Arithmetic (§6.1) says TP4 on H200
puts 118.8 GiB of weights on a 131 GiB card — inside the card but *outside* a 0.90 utilization
budget, leaving essentially nothing for KV. Prefer SGLang's TP8 shape, or expect to tune
`--gpu-memory-utilization` hard on the vLLM TP4 profile. ⚠️ **TO BE VERIFIED** — whether the
vLLM H200 TP4 profile is validated at a useful context/concurrency or only at boot.

### 3.5 B200 (SM100, 180 GB) / GB200 NVL4 (SM100, 192 GB/GPU)

| Model | vLLM | SGLang | TRT-LLM |
|---|---|---|---|
| DS-V4.1-Flash | `"b200": "verified"`, `"gb200": "verified"` — **TP4** both; GB200 is the recipe's `default_hardware` | `verified: true` on b200 — **TP4/EP4**; DSpark on Low-Latency | No |
| Kimi-K3 | `"b200": "verified"` (2 nodes × 8, **TP16**), `"gb200": "verified"` (4 nodes × 4, **TP16**) | b200 Unified: **PP2 × TP8** Low-Latency, **PP2 × DCP8/EP8** Balanced; gb200 **TP16/DCP16** | **TEP16 validated end-to-end on GB200** [src](https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/docs/source/deployment-guide/deployment-guide-for-kimi-k3-on-trtllm.md) |
| Qwen3.8-27B | `hw/b200.json`, `hw/gb200.json`, TP1 | not in `supportedHardware` | Yes |

The DS-V4.1 vLLM recipe's verified **1P1D disaggregated** layout is a GB200 NVL4 shape: "one
tray (4 GPUs) per role, TP4 in each pool, KV handed over through NIXL, fronted by
`vllm-router --vllm-pd-disaggregation`. Both pools disable FlashInfer autotune plus JIT and
CuTeDSL warmup via `--kernel-config`, skip the DeepGEMM warmup (`VLLM_DEEP_GEMM_WARMUP=skip`),
and cap `--max-num-seqs` at 32. On 8-GPU nodes the same layout becomes TP8 per role. … The
verified GB200 runs (TP4 and 1P1D) were **text-only**."
[src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash)

For MNNVL fabrics add `NCCL_MNNVL_ENABLE=1`, `NCCL_CUMEM_ENABLE=1`, `NCCL_NVLS_ENABLE=1`
[src](https://recipes.vllm.ai/moonshotai/Kimi-K3.json).

SGLang's Kimi-K3 B200 pipeline note is the sharpest parallelism insight on the page: DSPARK
requires `pp_size == 1`, so turning speculation on **re-lays the same 16 GPUs as flat TP16 /
TP16+DCP16+EP16** rather than PP2 × TP8 [src](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3.md).

### 3.6 B300 (SM103, 268 GB as deployed)

| Model | vLLM | SGLang | TRT-LLM |
|---|---|---|---|
| DS-V4.1-Flash | `"b300": "verified"` — the **only** profile using **TP2** (2 of 8 GPUs) plus `--engram-config '{"cpu_offload":true}'` and an explicit 26-size CUDA-graph ladder to 8190. The Engram offload is **load-bearing, not an optimization**: at 268 GB/GPU, TP2 puts 237.6 GiB of weights against a 224.6 GiB 0.90-utilization budget (§6.1) | `verified: true` — TP4/EP4 | No |
| Kimi-K3 | `"b300": "verified"` — **single node, TP8**, the recipe's `default_hardware` | **The only two `verified: true` K3 cells on the whole page** (B300 1×8 Unified Low-Latency and Balanced) | DEP16/TEP8 need "GB300-class per-GPU memory"; B300 at **268 GB as deployed** (not 288) still clears the guide's 210 GB/rank DEP16 figure by 58 GB and its 213 GB TEP8 figure by 55 GB ⚠️ (guide says GB300, does not name B300, and GB300's 288 GB gives 20 GB more slack) |
| Qwen3.8-27B | `hw/b300.json`, TP1 | not in `supportedHardware` | Yes |

vLLM's DS-V4.1 B300 default is **TP2**, and the recipe adds a pointed caveat: "The command
builder defaults to TP2 for B300 (2 of the 8 GPUs). For high interactivity, deploy the model
using TP4: pass `--tensor-parallel-size 4`."
[src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash)

B300 is the sweet spot for Kimi-K3: SGLang says "B300 1×8 — TP8 (+DCP8) — accuracy-first
defaults on Low-Latency and Balanced", and it is the one platform where a serving round on
final weights has been published [same src].

### 3.7 GB300 NVL4 (SM103, 288 GB/GPU × 4; NVL72 per-GPU figure, ≈ 279 usable)

| Model | vLLM | SGLang | TRT-LLM | Notes |
|---|---|---|---|---|
| DS-V4.1-Flash | `"gb300": "verified"`, TP4 | `verified: true`, **TP4/EP4**, `--mem-fraction-static 0.8`, DSpark block 5 | No | SGLang: "On GB300 they resolve to `dsv4` / `flashinfer_mxfp4` / `flashinfer_cutedsl`" |
| **DS-V4.1-Flash-NVFP4** | image `vllm/vllm-openai:deepseekv41-flash-0909`, **4× GB300**, TP4 | image `lmsysorg/sglang:dev-cu13-dsv41`, **4× GB300**, `--tp 4` | No | The NVIDIA card's **only** tested hardware [src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md) |
| Kimi-K3 | `"gb300": "verified"`, 2 nodes × 4, TP8 | 2 nodes, **TP8 / TP8+DCP8**, "MNNVL transport and cuMem auto-detected" | **The validation platform** — "validated on NVIDIA GB300 NVL GPUs"; DEP16 needs 210 GB/rank | |
| Qwen3.8-27B | `"gb300": "verified"`, TP1 | `verified: true` | Yes | |

GB300 is the only GPU where **all five** engine paths for the two DeepSeek checkpoints
converge, and the only one NVIDIA itself benchmarked the NVFP4 build on.

### 3.8 RTX PRO 6000 Blackwell Server Edition (SM120, 96 GB GDDR7, 1,597 GB/s)

| Model | Supported? | Evidence |
|---|---|---|
| **Qwen/Qwen3.8-27B** | **Yes — the only repo model with an RTX PRO 6000 profile** | vLLM `"rtx_pro_6000": "verified"` [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B.json); SGLang `supportedHardware` includes `rtx6000`, `vram: "96GB"` [src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md) |
| DS-V4.1-Flash | No | `HTTP 404 https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/rtx_pro_6000.json` |
| DS-V4.1-Flash-NVFP4 | No | Card names GB300 only |
| Kimi-K3 | No | `HTTP 404 https://recipes.vllm.ai/moonshotai/Kimi-K3/hw/rtx_pro_6000.json` |
| Marlin-2B | No engine at all (§6.5) | |

vLLM launch (TP1, `--max-num-seqs 8`, `--speculative-config '{"method":"mtp","num_speculative_tokens":5}'`
— note **5**, not the default 3, on this card)
[src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B/hw/rtx_pro_6000.json).

SGLang's SM120 guidance is the most actionable text found for this card
[src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md):

> "SM120/SM121 (RTX PRO 6000 Blackwell, RTX 5090, DGX Spark): use `--attention-backend
> flashinfer`; **`trtllm_mha` is SM100-only**. MTP with the FlashInfer backend requires a
> FlashInfer build whose prefill `plan` accepts `uniform_q_len` (newer than 0.6.15.post1);
> otherwise run spec with `--attention-backend triton`."

And the validation claim (SGLang's own, `meas.`): "both SM12x grids have been re-measured
against this export on v0.5.19: all 16 overlay combinations per card serve and score
**94.01–95.00% (RTX PRO 6000)** … on the full 1319-question GSM8K." The RTX PRO 6000 cell
flags: `--kv-cache-dtype fp8_e4m3 --mem-fraction-static 0.85 --attention-backend flashinfer
--chunked-prefill-size 2048 --reasoning-parser qwen3 --tool-call-parser qwen3_coder`.

### 3.9 AMD MI355X (CDNA4, gfx950, 288 GB)

| Model | vLLM | SGLang | TRT-LLM | TokenSpeed |
|---|---|---|---|---|
| DS-V4.1-Flash | `"mi350x": "verified"`; `hw/mi355x.json` exists — **TP4**, `--moe-backend aiter`, image `vllm/vllm-openai-rocm:nightly` | `verified: true` on `mi350x`, image `lmsysorg/sglang:dev-dsv41-mi35x` — **TP4/EP4** | No | No |
| Kimi-K3 | `"mi355x": "verified"` — **single node TP8**, image `vllm/vllm-openai-rocm:latest` | mi350x/mi355x cells (`verified: false`), image `lmsysorg/sglang-rocm:v0.5.19-rocm720-mi35x-20260910` — **TP8** | No (SM100 only) | **Yes** — 8× gfx950, `--attention-backend mla`, Gluon SiTU MoE |
| Qwen3.8-27B | `hw/mi355x.json`, TP1, `vllm/vllm-openai-rocm:latest` | not in `supportedHardware` | Yes (arch-level) | — |

**ROCm-specific blockers, all sourced:**

- **DS-V4.1 on vLLM ROCm** requires `VLLM_USE_BREAKABLE_CUDAGRAPH=1` because "DeepSeek-V4.1-Flash
  does not support `torch.compile`, and the ROCm sparse SWA backend only supports uniform-batch
  CUDA graphs." It also requires `--moe-backend aiter` *by name, not by kernel*: "Naming
  `aiter_triton_mxfp4_bf16` pins the Triton W4A16 `_moe_gemm_a16w4` kernel; the plain name lets
  vLLM select the Composable Kernel a8w4 experts."
  [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash)
- **DSpark adaptive verification is refused on ROCm.** "Two checks in
  `maybe_create_adaptive_verification_manager` fail on ROCm: the indexer helper
  `supports_device_cpu_query_lens_mismatch()` is False, and `DeepseekV41ROCMAiterSparseSWABackend`
  reports `AttentionCGSupport.UNIFORM_BATCH` rather than `ALWAYS`." The generated command sets
  `enable_adaptive_verification:false`; DSpark still drafts 5 tokens per round [same src].
- **SGLang DS-V4.1 on MI350X must run `--disable-radix-cache`**, and consequently "The
  [HiCache] card is not offered on MI350X: the ROCm recipes run `--disable-radix-cache`, and the
  server rejects that alongside `--enable-hierarchical-cache`."
  [src](https://docs.sglang.io/cookbook/autoregressive/DeepSeek/DeepSeek-V4_1.md)
  Required env: `SGLANG_USE_AITER=1 SGLANG_MOE_PADDING=1 AITER_FLYDSL_FORCE_REDUCE=1 ROCM_QUICK_REDUCE_QUANTIZATION=NONE`;
  flags add `--cuda-graph-backend-prefill breakable --cuda-graph-max-bs-prefill 4096`.
- **Kimi-K3 on ROCm** — vLLM: `VLLM_ROCM_USE_AITER_MOE_SITUV2=1` "enables AITER's SiTUv2 a4w4
  FlyDSL MoE path (vllm-project/vllm#53940). Unset it or set `0` for the default a16w4 path. Do
  **not** set `AITER_SITUV2_A8W4=1`; AITER checks that flag first and it would override a4w4."
  vLLM ROCm also runs with `VLLM_USE_BREAKABLE_CUDAGRAPH=0` and
  `--compilation-config '{"cudagraph_mode":"FULL_DECODE_ONLY","custom_ops":["+fused_rms_norm_gated"]}'`
  [src](https://recipes.vllm.ai/moonshotai/Kimi-K3.json).
  SGLang goes the **opposite** way and ships `AITER_SITUV2_A8W4=1` as its default, with a
  measured justification: "A4W4 … Numerically correct but **slower** than A8W4 (530.8 vs 537.3
  tok/s median output on 8×MI35x)" (`meas.`, SGLang's number)
  [src](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3.md). **The two
  engines therefore disagree on the preferred AMD MoE quantization path for the same
  checkpoint** — benchmark both before committing.
- SGLang MI35x K3 also uses `--attention-backend triton` with `--kv-cache-dtype fp8_e4m3`, and
  offers an opt-in fused KDA decode boundary `SGLANG_K3_KDA_FUSED_BACKEND=aiter` needing an
  AITER revision ≥ [ROCm/aiter#4534](https://github.com/ROCm/aiter/pull/4534) (FlyDSL 0.3.0):
  "Measured on a 69-layer graph the fused boundary is 9.20 → 8.38 µs/layer (−8.9%), with GSM8K
  1319 at 0.950" (`meas.`, SGLang's number) [same src].
- **No TensorRT-LLM on AMD, ever** — it is an NVIDIA-only stack.

### 3.10 Container images, consolidated

| Engine | Model | GPU family | Image |
|---|---|---|---|
| vLLM | DS-V4.1-Flash | NVIDIA | `vllm/vllm-openai:nightly` (launch-day `deepseekv41-flash-0909` tag superseded) |
| vLLM | DS-V4.1-Flash | AMD | `vllm/vllm-openai-rocm:nightly` (MI355X benchmark pinned `…:nightly-eed1f3d0c6043bd494424a22443ee198dd56f657`) |
| vLLM | DS-V4.1-Flash-**NVFP4** | GB300 | `vllm/vllm-openai:deepseekv41-flash-0909` |
| vLLM | Kimi-K3 | NVIDIA | `vllm/vllm-openai:kimi-k3` (**CUDA 13 / cu130 only, no `-cu129` tag; host needs r580+ driver**); recipe JSON emits `vllm/vllm-openai:latest` |
| vLLM | Kimi-K3 | AMD | `vllm/vllm-openai_rocm:kimi-k3` (guide) / `vllm/vllm-openai-rocm:latest` (JSON) |
| vLLM | Qwen3.8-27B | NVIDIA | `vllm/vllm-openai:qwen38` |
| vLLM | Qwen3.8-27B | AMD | `vllm/vllm-openai-rocm:latest` |
| vLLM | Qwen3.8-27B | Ascend 950PR | `quay.io/ascend/vllm-ascend:qwen3.8-a5` (W8A8) / `:nightly-main-a5` (FP8) |
| SGLang | DS-V4.1-Flash | H200/B200/B300/GB300 | `lmsysorg/sglang:dev-dsv41` — "**DeepSeek-V4.1 Flash support has not shipped in an SGLang release yet**, so a stock `pip install sglang` cannot serve it" |
| SGLang | DS-V4.1-Flash | MI350X | `lmsysorg/sglang:dev-dsv41-mi35x` |
| SGLang | DS-V4.1-Flash-**NVFP4** | GB300 | `lmsysorg/sglang:dev-cu13-dsv41` (tested build commit `da64c5cbb8cf6bfd39be19da43573fdfd484c43a`) |
| SGLang | Kimi-K3 | NVIDIA | `lmsysorg/sglang:kimi-k3`; NVFP4 variant `lmsysorg/sglang:dev-dev-kimi-k3-nvfp4` (CUDA 13) |
| SGLang | Kimi-K3 | MI350X/MI355X | `lmsysorg/sglang-rocm:v0.5.19-rocm720-mi35x-20260910` |
| SGLang | Kimi-K3 | Ascend A3 | `quay.io/ascend/sglang:main-cann9.0.0-a3` |
| SGLang | Qwen3.8-27B | all | `lmsysorg/sglang:latest` — **the only repo model SGLang serves from a stock tag** |
| Dynamo | DS-V4.1-Flash | GB200 | `nvcr.io/nvidia/ai-dynamo/sglang-runtime:1.6.0-deepseek-v4.1-flash-dev.1` (on `lmsysorg/sglang:dev-dsv41@sha256:e56358a6…`, CUDA 13, NIXL v1.4.0) |
| Dynamo | Kimi-K3 | H200/GB200/GB300 | `nvcr.io/nvidia/ai-dynamo/vllm-runtime:1.5.0-kimi-k3-dev.1` (vLLM v0.28.0 base, NIXL v1.3.1) and `…/sglang-runtime:1.5.0-kimi-k3-dev.1` (SGLang v0.5.17 base, NIXL v1.3.0) |
| TokenSpeed | all | NVIDIA / AMD | `lightseekorg/tokenspeed:latest` / `lightseekorg/tokenspeed-amd:latest` |

---

## 4. Data types: what each engine will actually run, per GPU

| Checkpoint format | A100 | H100/H200 | B200/GB200 | B300/GB300 | RTX PRO 6000 | MI355X |
|---|---|---|---|---|---|---|
| **BF16** | yes | yes | yes | yes | yes | yes |
| **FP8 block-scaled (128×128)** | no HW | yes | yes | yes | yes | yes |
| **FP8 block-scaled 32×32, UE8M0** (DS-V4.1 dense) | no | yes via `dsv4`/`flashinfer_mxfp4` | yes | yes | ⚠️ untested | via AITER |
| **MXFP4** (K3 experts, DS-V4.1 experts) | no | **weight-only fallback only** → SGLang/vLLM pin `marlin` (W4A16) on Hopper | `flashinfer_mxfp4` (trtllm-gen SiTU) | `flashinfer_mxfp4` | ⚠️ not profiled | AITER SiTUv2 (a4w4 or a8w4) |
| **NVFP4 (W4A4)** | no | **no** — "the card has no FP4 tensor cores, so an NVFP4 checkpoint's MLP would fall back to the Marlin W4A16 weight-only path" | `flashinfer_trtllm` | `flashinfer_trtllm` | **yes** — `FlashInferCutlassNvFp4LinearKernel` on sm120 is "a cutlass path, not an emulation fallback". ⚠️ **TO BE VERIFIED** for this exact card: the vLLM recipe makes that statement in its **2× RTX 5090** section ("consumer Blackwell, sm120"), not in the RTX PRO 6000 section. Same SM version, different board — no NVFP4 GEMM measurement is published on RTX PRO 6000 itself [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) | **no** — "which do not exist for Hopper or AMD" |
| **FP8 KV (`fp8_e4m3`)** | no | yes | yes | yes | yes | yes |
| **FP4 KV** | no | no | DS-V4.1 caches main KV in E2M1 natively | same | ⚠️ | ⚠️ |
| **INT4 W4A8 / W8A8** | — | — | — | — | — | Ascend-only variants exist (`Eco-Tech/Kimi-K3-w4a8`, `Eco-Tech/Qwen3.8-27B-w8a8`, `--quantization ascend`) |

Sources: [SGLang Qwen3.8-27B cookbook](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md),
[SGLang Kimi-K3 cookbook](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3.md),
[vLLM Qwen3.8-27B recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B),
[vLLM Kimi-K3 recipe](https://recipes.vllm.ai/moonshotai/Kimi-K3.json).

**The single most important dtype fact for this repo:** NVFP4 is a **Blackwell-only** path, and
that includes the consumer/workstation SM120 parts but excludes Hopper *and* MI355X. B300/GB300
is where `nvidia/DeepSeek-V4.1-Flash-NVFP4` was validated and the only hardware its card names.

---

## 5. Engine feature cross-reference

| Capability | vLLM 0.29 | SGLang 0.5.20 | TensorRT-LLM 1.3.0rcNN | Dynamo 1.5 |
|---|---|---|---|---|
| Prefix caching | on by default; `--prefix-match-unit N` for hybrid models | RadixAttention default; `--disable-radix-cache` to turn off | "KV Cache Reuse" column, per-arch | KV-aware routing exploits it across replicas |
| Hierarchical / offloaded KV | `OffloadingConnector` (CPU + FS), `LMCacheConnectorV1`, `FlexKVConnectorV1` | **HiCache** L1/L2/L3 + Mooncake storage backend | KV Cache Connector API (since 1.1) | KVBM ✅ on vLLM + TRT-LLM, 🚧 on SGLang; offload backends LMCache / HiCache / FlexKV |
| Chunked prefill | `--max-num-batched-tokens` | `--chunked-prefill-size`, `--max-prefill-tokens` | `enable_chunked_prefill: true` | inherited |
| CUDA graphs | `--compilation-config {cudagraph_mode, cudagraph_capture_sizes}`, `--max-cudagraph-capture-size`; `FULL_DECODE_ONLY`; `VLLM_USE_BREAKABLE_CUDAGRAPH` | `--cuda-graph-max-bs-decode/-prefill`, `--cuda-graph-backend-prefill breakable`; Piecewise + Breakable graph docs | `cuda_graph_config.{enable_padding,max_batch_size}` | inherited |
| Speculative decoding | `--speculative-config` JSON: `mtp`, `dspark`, `dflash`, `eagle3` | `--speculative-algorithm EAGLE\|EAGLE3\|MTP\|NEXTN\|DSPARK\|DFLASH\|UNO\|STANDALONE\|NGRAM` | MTP, EAGLE-3, **DSpark** (K3), DFlash (GPT-OSS) | passes through |
| Expert parallel | `--enable-expert-parallel`, `--all2all-backend {allgather_reducescatter, deepep_high_throughput, deepep_low_latency, deepep_v2, flashinfer_nvlink_one_sided/two_sided}` — the 0.29.0 config literal additionally accepts `mori_high_throughput`, `mori_low_latency`, `nixl_ep`, and rejects the removed `pplx`/`naive` (§2.1) | `--ep-size`, `--moe-a2a-backend {deepep, megamoe, mooncake, nixl, mori, flashinfer, pplx, ascend_fuseep, none}`, `--deepep-mode {auto,normal,low_latency}` | `moe_expert_parallel_size` + `enable_attention_dp` (DEP vs TEP) | n/a |
| EPLB | `--enable-eplb` + `--eplb-config` JSON (`window_size`, `step_interval`, `num_redundant_experts`, `use_async`, `policy`, `communicator ∈ {torch_nccl, torch_gloo, pynccl, nixl}`); redundant-expert memory overhead documented [src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/serving/expert_parallel_deployment.md) | `--enable-eplb`, `--eplb-*` | not named ⚠️ **TO BE VERIFIED** | n/a |
| DP-attention | `--data-parallel-size` with `--tensor-parallel-size 1` | `--enable-dp-attention --dp-size N --enable-dp-lm-head` | `enable_attention_dp: true` | n/a |
| Decode context parallel | `--decode-context-parallel-size`, `--dcp-comm-backend a2a` | `--dcp-size`, `--dcp-comm-backend {a2a, fi_a2a}` | not named ⚠️ | n/a |
| PD disaggregation | experimental; 9 connectors; `vllm-router --vllm-pd-disaggregation` | `--disaggregation-mode`, Mooncake/NIXL/Ascend, `sglang-router --pd-disaggregation`; **EPD** too | validated for K3 on GB300, matched DEP16=DEP16 only | first-class; NIXL; xPyD runtime-reconfigurable |
| Multimodal encoder placement | `--mm-encoder-tp-mode data`, `--language-model-only`, `--mm-processor-cache-gb` | `--mm-feature-transport {cpu,…}`, `--mm-enable-dp-encoder`, `SGLANG_VIT_ENABLE_CUDA_GRAPH`, `--encoder-only` role | encoder side stream + embeddings cache (`multimodal_config.*`) | multimodal ✅ on all three backends |

---

## 6. Per-model support

### 6.1 `deepseek-ai/DeepSeek-V4.1-Flash`

**Architecture (from the checkpoint, not from a summary).** `model_type: deepseek_v41`,
architecture `DeepseekV41ForCausalLM`, `transformers_version: 5.6.0`
[src](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/blob/main/config.json) (local copy:
`research/models/deepseek41f/config.json`). 40 layers, hidden 5120, 64 attention heads,
`head_dim 512`, `num_key_value_heads: 1`, `qk_rope_head_dim 64`, `q_lora_rank 1280`,
`o_lora_rank 1024`, `o_groups 8`; 384 routed experts + 1 shared, `num_experts_per_tok: 6`,
`moe_intermediate_size 2304`, `scoring_func: sqrtsoftplus`, `topk_method: noaux_tc`;
`sliding_window: 128`; `compress_ratios` a 43-entry list of 0/1/2; `kv_source_layer_ids: [2,8,14,20]`;
`index_source_layer_ids: [2,8,14,20,24,28,32,36]`, `index_topk: 512`,
`candidate_source_layer_id: 20`, `candidate_topk_blocks: 2048`, `candidate_block_size: 8`;
`hc_mult: 4`, `hc_sinkhorn_iters: 20`; `engram_layer_ids: [1,14]` with
`engram_num_embeddings: [384006168, 384016682]`, `engram_head_dim 256`;
**`num_nextn_predict_layers: 3`**; DSpark block `dspark_block_size: 5`,
`dspark_target_layer_ids: [37,38,39]`, `dspark_n_routed_experts: 128`,
`dspark_num_experts_per_tok: 3`. Quantization: `quant_method: fp8`,
`weight_block_size: [32,32]`, `scale_fmt: ue8m0`, `expert_dtype: fp4`. Vision: 32-layer ViT,
hidden 1024, patch 14, `downsample_ratio 3`, `max_image_tokens: 1024`, `min_pixels: 295936`.

> **Correction to the brief.** The task brief describes this model as having a "DSA indexer"
> and "MTP `num_nextn_predict_layers=3`". The config field is present, but **the engines
> universally state there is no usable MTP path**: "DSpark is the only speculative method for
> this checkpoint. **V4.1 dropped the MTP module** that V3 and V4 trained alongside the
> backbone" [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash), and "there is no
> EAGLE or MTP path for this model, and no `--speculative-num-steps` knob (the draft-token
> count is resolved from the checkpoint)"
> [src](https://docs.sglang.io/cookbook/autoregressive/DeepSeek/DeepSeek-V4_1.md). The model
> card itself calls the attention **CSA2 (Compressed Sparse Attention 2)** with modes Full /
> Reindex / Reuse plus a Hierarchical Sparse Indexer, and describes the topology as a **Causal
> Encoder-Decoder** (20 encoder + 20 decoder layers) with **SWA Bounded Replay**, giving
> "8B parameters per token during prefill and 16B during decode" and a **890 bytes/token**
> global KV footprint [src](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/README.md).
> Do not plan an MTP deployment for this model.
>
> **890 B/token is the FP4-KV figure** (720 B main + 170 B indexer), and FP4 KV needs the
> Blackwell kernel: the vLLM recipe states the compressed latents are "shared across layers and
> **trained to be stored in FP4**, and DeepSeek puts the resulting global KV at 890 bytes per
> token" [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash). On a GPU without the
> FP4-KV path the per-token cost is correspondingly higher — take the per-dtype figures from
> [models/deepseek41f/architecture.md](../models/deepseek41f/architecture.md) §5, never 890 B
> flat (METHODOLOGY §2, §5).

**Release-tag probe (2026-09-19):**

```
HTTP 200  https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/models/deepseek_v41/__init__.py
HTTP 404  https://raw.githubusercontent.com/vllm-project/vllm/v0.29.0/vllm/models/deepseek_v41/__init__.py
HTTP 404  https://raw.githubusercontent.com/vllm-project/vllm/v0.28.0/vllm/models/deepseek_v41/__init__.py
HTTP 200  https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/configs/deepseek_v41.py
HTTP 404  https://raw.githubusercontent.com/sgl-project/sglang/v0.5.20/python/sglang/srt/configs/deepseek_v41.py
HTTP 404  https://raw.githubusercontent.com/sgl-project/sglang/v0.5.19/python/sglang/srt/configs/deepseek_v41.py
```

vLLM `main` registers it as `"DeepseekV41ForCausalLM": ("vllm.models.deepseek_v41", "DeepseekV41ForCausalLM")`
and the DSpark draft as `"DSparkV41DraftModel": ("vllm.models.deepseek_v41", "DSparkDeepseekV4ForCausalLM")`
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/registry.py).
SGLang serves it through the shared `models/deepseek_v4.py` (which carries
`self.is_dsv41 = getattr(config, "model_type", None) == "deepseek_v41"`, the `DeepseekV41Compressor`
/ `DeepseekV41Indexer` from `layers/attention/dsv4/dsv41_sparse`, the `Engram` layer, and
`models/deepseek_v41_vit.py`) plus a `configs/deepseek_v41.py` normalizer
[src](https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/models/deepseek_v4.py).
It is **absent from vLLM's published `supported_models.md`** and from TensorRT-LLM's matrix.

| Engine | Level | Min version | GPUs |
|---|---|---|---|
| vLLM | **Main only** | `min_vllm_version: 0.30.0`, `nightly_required: true` | h100, h200, b200, gb200, b300, gb300, mi350x all `"verified"` |
| SGLang | **Main only** (preview image) | none — "has not shipped in an SGLang release yet" | h200, b200, b300, gb300, mi350x, all `verified: true` |
| TensorRT-LLM | **Absent** | — | (V4, not V4.1, is supported; `SM100+` only) |
| Dynamo | **Experimental dev build** | `v1.6.0-deepseek-v4.1-flash-dev.1` (2026-09-12), SGLang backend only | 8× GB200 |
| TokenSpeed | **Recipe published** (corrected 2026-09-19; no engine version number exists to pin) | — | GB300 (per-commit Slurm 1P1D CI, TP4+TP4); the aggregated command is TP8/EP, `--moe-backend marlin` [src](https://lightseek.org/tokenspeed/recipes/models) |

**Exact launch commands.**

vLLM, GB200 (the recipe's recommended command) [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash.json):

```bash
VLLM_ENGINE_READY_TIMEOUT_S=3600 VLLM_USE_RUST_FRONTEND=1 \
vllm serve deepseek-ai/DeepSeek-V4.1-Flash \
  --tokenizer-mode deepseek_v41 \
  --tensor-parallel-size 4 \
  --tool-call-parser deepseek_v41 \
  --enable-auto-tool-choice \
  --reasoning-parser deepseek_v41 \
  --mm-encoder-tp-mode data
```

vLLM, H100 — note the Engram offload, which is what makes 80 GB cards possible
[src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/h100.json):

```bash
VLLM_ENGINE_READY_TIMEOUT_S=3600 VLLM_USE_V2_MODEL_RUNNER=1 \
PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True VLLM_USE_RUST_FRONTEND=1 \
vllm serve deepseek-ai/DeepSeek-V4.1-Flash \
  --tokenizer-mode deepseek_v41 \
  --engram-config '{"cpu_offload":true}' \
  --max-num-batched-tokens 4096 \
  --gpu-memory-utilization 0.92 \
  --tensor-parallel-size 8 \
  --tool-call-parser deepseek_v41 --enable-auto-tool-choice \
  --reasoning-parser deepseek_v41 --mm-encoder-tp-mode data
```

SGLang, GB300 Low-Latency (verified cell)
[src](https://docs.sglang.io/cookbook/autoregressive/DeepSeek/DeepSeek-V4_1.md):

```bash
sglang serve --trust-remote-code --model-path deepseek-ai/DeepSeek-V4.1-Flash \
  --tp 4 --ep-size 4 --mem-fraction-static 0.8 \
  --speculative-algorithm DSPARK --speculative-dspark-block-size 5 \
  --cuda-graph-max-bs-decode 64 \
  --reasoning-parser auto --tool-call-parser auto \
  --host 0.0.0.0 --port 30000
```

SGLang, H200 Low-Latency — the only cell that pins backends explicitly, plus SWA bounded replay:

```bash
sglang serve --trust-remote-code --model-path deepseek-ai/DeepSeek-V4.1-Flash \
  --tp 8 --ep-size 8 --mem-fraction-static 0.8 \
  --attention-backend dsv4 --moe-runner-backend flashinfer_mxfp4 \
  --enable-decoder-swa-bounded-replay \
  --cuda-graph-max-bs-decode 64 \
  --reasoning-parser auto --tool-call-parser auto --host 0.0.0.0 --port 30000
```

**Memory arithmetic** (recipe's own checkpoint table, re-derived with `python3`):

| Component | Entries | Stored | bytes/entry (computed) |
|---|---:|---:|---:|
| Routed + DSpark experts (MXFP4) | 557.2 B | 259.5 GiB | 0.500 |
| Engram tables (FP8) | 196.6 B | 183.1 GiB | 1.000 |
| Attention, dense projections, routers (FP8) | 7.4 B | 6.9 GiB | 1.001 |
| Embedding + LM head (BF16), norms (FP32) | 2.0 B | 3.9 GiB | 2.094 |
| UE8M0 block scales | 23.6 B | 21.9 GiB | 0.996 |
| **Total** | **786.8 B** | **475.26 GiB = 510.31 GB** | — |

Checkpoint total re-fetched 2026-09-19: `usedStorage` 510,310,271,922 B = **475.26 GiB =
510.31 GB**, reconciling with the 510.29 GB pinned in METHODOLOGY §8 to 0.004 %
[src](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash?expand[]=usedStorage).
`est.` check against the recipe's `vram_minimum_gb: 614`: 475.26 GiB × 1.2 = 570.3 GiB =
**612.4 GB**, matching the stated "schema's 1.2 headroom factor". Recipe text: "roughly 511 GB
on disk (476 GiB)" — agrees to 0.2 %. `[src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash)`

Per-GPU weight share at the shapes the recipes actually emit (`est.`, weights only, no KV or
activation workspace). **Recomputed 2026-09-19 with `python3`** — the previous version of this
table compared GiB of weights against GB of capacity for the B300 and GB300/MI355X rows, which
flipped the B300 verdict. Capacities are the as-deployed GB figures of §3.1 converted to GiB
(268 GB = 249.59 GiB; 288 GB = 268.22 GiB; 192 GB = 178.81 GiB; 180 GB = 167.64 GiB;
141 GB = 131.32 GiB; 80 GB = 74.51 GiB), per METHODOLOGY §1's units rule:

| Shape | GPU capacity | 0.90 × capacity | Weights/GPU | Fits at 0.90 util? |
|---|---|---|---|---|
| **TP2, B300** | 268 GB = **249.59 GiB** | 224.63 GiB | 237.63 GiB | **no** — 13.0 GiB over. This, not "barely yes", is what forces the B300 profile's `--engram-config '{"cpu_offload":true}'`: offloading the 183.1 GiB Engram tables drops the share to 146.1 GiB, which then fits with 78 GiB left for KV |
| TP4, B200 | 180 GB = 167.64 GiB | 150.88 GiB | 118.82 GiB | yes (+32.1 GiB) |
| TP4, GB200 NVL4 | 192 GB = 178.81 GiB | 160.93 GiB | 118.82 GiB | yes (+42.1 GiB) |
| TP4, GB300 / MI355X | 288 GB = 268.22 GiB | 241.40 GiB | 118.82 GiB | yes, comfortably (+122.6 GiB) |
| **TP4, H200** | 141 GB = 131.32 GiB | 118.19 GiB | 118.82 GiB | **no** — 0.6 GiB over |
| TP8, H200 | 141 GB = 131.32 GiB | 118.19 GiB | 59.41 GiB | yes (+58.8 GiB) |
| TP8, H100 | 80 GB = 74.51 GiB | 67.06 GiB | 59.41 GiB | yes on weights (+7.6 GiB), but only usable with `--engram-config '{"cpu_offload":true}'` — 7.6 GiB leaves nothing for KV and activation workspace; offloaded, the share is 36.5 GiB |

This is a weights-only fit check, kept here because the recipes' own TP choices depend on it.
Full per-(model, GPU) fit / throughput / cost tables are **not** in scope for this document —
they live in `research/models/deepseek41f/<gpu>.md` and are written in the next phase.

**Open issues / blockers:**

- Not in any numbered release of vLLM or SGLang (probe above).
- `--enable-deterministic-inference` is **refused** on the SGLang `dsv4` backend, and "output
  is not bitwise stable across batch composition today — two default kernels are shape-guarded
  to a single token" [src](https://docs.sglang.io/cookbook/autoregressive/DeepSeek/DeepSeek-V4_1.md).
- `--enable-decoder-swa-bounded-replay` "is validated on the decode path only, **refuses prompt
  logprobs by design**, is not numerically equivalent to full prefill, and is excluded at launch
  with the prefill CUDA graph and with DP attention" [same src].
- **Do not override backends.** "`--attention-backend`, `--moe-runner-backend` and
  `--fp8-gemm-backend` are selected automatically … Overriding them is the most common cause of
  a disappointing measurement: it leaves the 32-wide ue8m0 blocks on the Triton
  `_w8a8_block_fp8_matmul` fallback, which dominates the decode step and costs most of the
  model's bs=1 throughput" [same src].
- **PD and speculative decoding cannot be combined** (SGLang), and Mooncake needs
  `--device /dev/infiniband:/dev/infiniband --cap-add IPC_LOCK --ulimit memlock=-1` inside the
  container or it silently selects NVLink transport and fails with
  `Requested address ... not found` [same src].
- Reasoning-effort mapping differs between the DeepSeek API and the open prompt encoder: "the
  DeepSeek API maps its `low`/`high`/`max` tiers to 50/75/100, while the open-source prompt
  encoder (and therefore vLLM) maps `low`/`high`/`xhigh`/`max` to 25/50/75/100"
  [src](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash). SGLang additionally defaults
  thinking **off** (`SGLANG_DEFAULT_THINKING=false`) while vLLM defaults it **on at effort 50** —
  a real behavioural fork between the two engines.
- ROCm blockers: §3.9.
- Long load times are expected: `VLLM_ENGINE_READY_TIMEOUT_S=3600` is in the base env.

### 6.2 `nvidia/DeepSeek-V4.1-Flash-NVFP4`

Released **2026-09-16**; quantized with `nvidia-modelopt v0.47.0rc0`, source revision
`dba1be0a40aa45a94ad051997016db3960a90277`
[src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md).

What it changes: "converts the ordinary routed MoE experts from source MXFP4 to NVFP4 weights
and activations (W4A4), with group size 16. The converted projections are `w1`, `w2`, and `w3`
for 384 experts across 40 layers. Attention, shared experts, vision, Engram lookup tables,
MTP/DSpark, and other excluded components retain their source precision, including MXFP8 where
applicable." The `quantization_config.ignore` list confirms:
`["*.attn.*", "*.ffn.shared_experts.*", "head", "mtp.*"]`, `moe_quant_algo: "NVFP4"`,
`quant_algo: "MIXED_PRECISION"`, `group_size: 16`, `kv_cache_quant_algo: null`
(local copy: `research/models/deepseek41fnvfp4/config.json`).

**Size cost, not saving:** "The source experts already use four-bit weights. The finer NVFP4
scale layout **increases** checkpoint size from approximately 476 GiB to **492 GiB**. The export
contains 48 safetensors shards." Measured 2026-09-19: `usedStorage` 527,309,220,165 B =
**491.09 GiB = 527.31 GB**, against the base checkpoint's 475.26 GiB = 510.31 GB — a **+17 GB**
export, reconciling with the 527.27 GB / 510.29 GB pinned in METHODOLOGY §8
[src](https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4?expand[]=usedStorage).
So NVFP4 here buys **W4A4 activation quantization on Blackwell tensor cores**, not footprint.

Two things follow, and both cut against the usual "NVFP4 = the fast FP4" shorthand:

1. **The base checkpoint's routed experts are MXFP4, not NVFP4** — E2M1 with one E8M0 scale per
   32 (0.53125 B/param), per its `quantization_config`; the NVFP4 build is the *derived*
   artefact, at E2M1 + one FP8 E4M3 per 16 (0.5625 B/param, +12.5 %), which is exactly why it is
   larger (METHODOLOGY §1). Any statement that DeepSeek-V4.1-Flash ships NVFP4 experts is wrong.
2. **NVIDIA publishes no speedup for the NVFP4 build over the base.** Re-read 2026-09-19: the
   card contains an accuracy table and nothing else quantitative — **zero** throughput, latency
   or speedup figures against the MXFP4 baseline it names. It is accuracy-neutral (below) and
   larger; treat it as an enabling artefact for W4A4 kernels, not as a measured win, until
   someone publishes a paired benchmark.

**Accuracy (NVIDIA's measurement, `meas.`):**

| Precision | GPQA Diamond | AA-LCR | SciCode | IFBench | MMMU-Pro | Terminal-Bench 2.1 |
|---|---|---|---|---|---|---|
| MXFP4 (source) | 91.035 | 78.563 | 54.401 | 76.667 | 74.046 | 81.60 |
| **NVFP4** | **91.288** | 78.438 | **55.843** | **77.267** | 73.699 | **82.16** |

(`temperature=1.0, top_p=0.95, reasoning_effort=100, max_new_tokens=262144`; GPQA and AA-LCR 16
repeats, IFBench 5; Terminal-Bench 8 trajectories/task.) Within noise — NVFP4 is not an accuracy
regression on this checkpoint.

**Engine support** — narrow and explicit: runtimes **vLLM and SGLang**; microarchitecture
**NVIDIA Blackwell**; test hardware **GB300**. Not in the vLLM recipes catalogue
(`nvidia/DeepSeek-V4-Flash-NVFP4` is listed, `…V4.1…` is not)
[src](https://recipes.vllm.ai/models.json). Not in TensorRT-LLM.

SGLang, 4× GB300 (quoted verbatim from the card):

```bash
python -m sglang.launch_server \
    --model-path nvidia/DeepSeek-V4.1-Flash-NVFP4 \
    --served-model-name DeepSeek-V4.1-Flash-NVFP4 \
    --host 127.0.0.1 --port 30000 \
    --tp 4 \
    --context-length 1048576 \
    --reasoning-parser deepseek-v41 \
    --tool-call-parser deepseekv41 \
    --chunked-prefill-size 4096 \
    --max-running-requests 16 \
    --log-level info
```

> "The tested build automatically selects `flashinfer_trtllm_routed` for the NVFP4 experts.
> Include a request-level `reasoning_effort`, such as `"max"`, to enable thinking; **thinking is
> off by default in this SGLang build**."

vLLM, 4× GB300:

```bash
vllm serve nvidia/DeepSeek-V4.1-Flash-NVFP4 \
    --tensor-parallel-size 4 \
    --data-parallel-size 1 \
    --tokenizer-mode deepseek_v41 \
    --reasoning-parser deepseek_v41 \
    --language-model-only \
    --max-model-len 1048576 \
    --max-num-seqs 32 \
    --max-num-batched-tokens 8192 \
    --enable-chunked-prefill \
    --no-enable-prefix-caching \
    --model-loader-extra-config '{"enable_multithread_load": true, "num_threads": 128}'
```

**Blockers, quoted:** "DSpark tensors are preserved, but **speculative decoding was not
exercised** in the reported validation. The vLLM example is **text-only**; image serving with
vLLM requires a multimodal configuration." Also note `--no-enable-prefix-caching` in NVIDIA's
own command — ⚠️ **TO BE VERIFIED** whether prefix caching is broken for this checkpoint or
simply disabled to make the benchmark deterministic.

### 6.3 `moonshotai/Kimi-K3`

**Architecture.** `model_type: kimi_k3`, `KimiK3ForConditionalGeneration`, text tower
`model_type: kimi_linear` / `KimiLinearForCausalLM`. 2.8T total / 104B active, 93 layers,
**69 KDA + 24 Gated MLA** (`full_attn_layers: [4,8,…,92,93]`), hidden 7168, 96 heads,
`kv_lora_rank 512`, `qk_nope_head_dim 128`, `qk_rope_head_dim 64`, `q_lora_rank 1536`,
`mla_use_nope: true`, `mla_use_output_gate: true`; KDA `head_dim 128`, `num_heads 96`,
`short_conv_kernel_size 4`, `use_full_rank_gate: true`; `attn_res_block_size: 12`;
896 experts top-16 + 2 shared, `moe_intermediate_size 3072`, `routed_expert_hidden_size 3584`,
`hidden_act: situ`; **`num_nextn_predict_layers: 0`**; vocab 160K; context 1,048,576; vision
MoonViT-V2 401M. Quantization `compressed-tensors` / `mxfp4-pack-quantized`, group size 32,
`ignore: ["re:.*self_attn.*","re:.*shared_experts.*","re:.*mlp\\.(gate|up|gate_up|down)_proj.*","re:.*lm_head.*","re:.*vision_tower.*","re:.*mm_projector.*"]`
(local copy: `research/models/kimik3/config.json`). Card: "MXFP4 weights / MXFP8 activations
(quantization-aware training)" [src](https://huggingface.co/moonshotai/Kimi-K3/raw/main/README.md).

**Release-tag probe (2026-09-19):**

```
HTTP 404  .../vllm-project/vllm/v0.26.0/vllm/models/kimi_k3/__init__.py
HTTP 200  .../vllm-project/vllm/v0.27.0/vllm/models/kimi_k3/__init__.py     <-- first vLLM release
HTTP 200  .../vllm-project/vllm/v0.29.0/vllm/models/kimi_k3/__init__.py
HTTP 200  .../sgl-project/sglang/v0.5.17/python/sglang/srt/models/kimi_k3.py  <-- first SGLang release
HTTP 200  .../sgl-project/sglang/v0.5.20/python/sglang/srt/models/kimi_k3.py
```

| Engine | Level | Min version | GPUs verified |
|---|---|---|---|
| vLLM | **Released** | recipe says `0.27.1`; tag probe says first in **0.27.0** | h200, b200, b300, gb200, gb300, mi355x, ascend_910c `"verified"` |
| SGLang | **Released** | v0.5.17 | b300 1×8 Unified Low-Latency/Balanced `verified: true`; **every other cell `verified: false`** ("Final Verification In Progress") |
| TensorRT-LLM | **Released, SM100 family only**, build from source | requires `python3 scripts/build_wheel.py --cuda_architectures 103-real` + `pip install fla-core einops` | GB300 (validated), GB200 (TEP16 validated end-to-end), B200 (functional, CI unit tests) |
| Dynamo | Experimental dev build | `v1.5.0-kimi-k3-dev.1` | H200, GB200, GB300 |
| TokenSpeed | Yes | — | 8× B300 (NVIDIA), 8× gfx950 (AMD) |

**Minimum GPU count is forced by weights, and all three engines agree** (`est.`, from vLLM's
`vram_minimum_gb: 1680` at 0.9 utilization, rounded up to the next power of two):

Recomputed 2026-09-19 with `python3` at the **as-deployed** capacities of §3.1 — the earlier
version of this table merged B300 with GB300 at 288 GB and B200 with GB200 at 192 GB, which
METHODOLOGY §8 forbids. Splitting them does not change any landing point, but B200's raw
requirement moves from 10 to 11:

| GPU | Capacity as deployed | GPUs needed = ⌈1680/(cap×0.9)⌉ | Next power of 2 | What the recipes actually publish |
|---|---:|---:|---:|---|
| B300 (HGX/DGX) | 268 GB | ⌈1680/241.2⌉ = 7 | **8** | vLLM B300 TP8 single node ✔ |
| GB300 NVL | 288 GB | ⌈1680/259.2⌉ = 7 | **8** | GB300 2 nodes × 4, TP8 ✔ |
| MI355X | 288 GB | ⌈1680/259.2⌉ = 7 | **8** | vLLM MI355X TP8 single node ✔ |
| B200 | 180 GB | ⌈1680/162.0⌉ = **11** | **16** | vLLM B200 2 nodes × 8 TP16 ✔ |
| GB200 NVL4 | 192 GB | ⌈1680/172.8⌉ = 10 | **16** | GB200 4 nodes × 4 TP16 ✔ |
| H200 | 141 GB | ⌈1680/126.9⌉ = 14 | **16** | vLLM H200 2 nodes × 8 TP16 ✔ |
| H100 | 80 GB | ⌈1680/72.0⌉ = 24 | **32** | vLLM H100 4 nodes × 8 TP32 ✔ |

This is the cleanest cross-check in the document: an independent arithmetic derivation lands on
exactly the published topology for all seven GPUs, and it survives the switch to as-deployed
capacities.

TRT-LLM's per-rank accounting corroborates from the other direction: DEP16 = 114 GB BF16
non-expert (replicated) on top of 90 GB MXFP4 experts @ EP16, stated as **210 GB/rank**;
TEP16 shards the non-expert weights → **115 GB/rank**; TEP8's 8-way expert share alone is
181 GB → **213 GB/rank**
[src](https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/docs/source/deployment-guide/deployment-guide-for-kimi-k3-on-trtllm.md).
⚠️ **TO BE VERIFIED** — NVIDIA's own footnote `[^15]` and the deployment guide both give
114 + 90 as "210 GB per rank", but **114 + 90 = 204**, and 213 − 181 = 32 ≠ 114/16 = 7.1 for
TEP8 either. The 210/213/115 figures are the guide's *requirements*, not a sum of its own two
components; do not re-derive them arithmetically. Method to close: read the load-time peak
reported by a real DEP16 and TEP8 run.

Sanity check on the expert tensors alone: `90 × 16 = 1440 GB` and `181 × 8 = 1448 GB` —
consistent with each other, and `1440 + 114 = 1554 GB` lands within 0.5 % of the checkpoint's
measured **1,561 GB** (`usedStorage` 1,561,018,243,668 B = 1453.8 GiB)
[src](https://huggingface.co/api/models/moonshotai/Kimi-K3?expand[]=usedStorage), which is the
strongest independent confirmation of the whole weight budget in this document. Note that
1,561 GB of weights against a `vram_minimum_gb` of 1,680 is only a 1.076× headroom factor, not
the 1.2× the DS-V4.1 variant schema uses — ⚠️ **TO BE VERIFIED** whether the K3 recipe applies a
different headroom rule or a different weight basis.

**Exact launch commands.**

vLLM, B300 single node (blackwell profile)
[src](https://recipes.vllm.ai/moonshotai/Kimi-K3/hw/b300.json):

```bash
VLLM_ALLREDUCE_USE_FLASHINFER=1 VLLM_ENGINE_READY_TIMEOUT_S=3600 \
VLLM_USE_V2_MODEL_RUNNER=1 VLLM_USE_RUST_FRONTEND=1 \
vllm serve moonshotai/Kimi-K3 \
  --trust-remote-code --gpu-memory-utilization 0.95 \
  --tensor-parallel-size 8 \
  --load-format fastsafetensors --no-enable-flashinfer-autotune \
  --max-model-len 1048576 --kv-cache-dtype fp8 \
  --attention-config '{"use_prefill_query_quantization":true,"mla_prefill_backend":"TOKENSPEED_MLA"}' \
  --enable-prefix-caching --attention-backend TOKENSPEED_MLA --prefix-match-unit 128 \
  --enable-auto-tool-choice --tool-call-parser kimi_k3 --reasoning-parser kimi_k3
```

vLLM, H100 4 nodes × 8 — note the brutal caps
[src](https://recipes.vllm.ai/moonshotai/Kimi-K3/hw/h100.json):

```bash
vllm serve moonshotai/Kimi-K3 --trust-remote-code \
  --tensor-parallel-size 32 --nnodes 4 --node-rank 0 --master-addr $HEAD_IP \
  --gpu-memory-utilization 0.97 --max-num-seqs 5 --max-model-len 32768 \
  --moe-backend marlin --disable-custom-all-reduce --no-enable-flashinfer-autotune \
  --max-num-batched-tokens 4096 --attention-backend FLASHMLA \
  --enable-auto-tool-choice --tool-call-parser kimi_k3 --reasoning-parser kimi_k3
```

SGLang, B300 1×8 Balanced (one of the two verified cells)
[src](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3.md):

```bash
sglang serve --trust-remote-code --model-path moonshotai/Kimi-K3 \
  --tp-size 8 --dcp-size 8 --mem-fraction-static 0.85 \
  --reasoning-parser kimi_k3 --tool-call-parser kimi_k3 --host 0.0.0.0 --port 30000
```

SGLang large-scale preset, 32 GPUs (4 nodes × 8 on B200/B300):

```bash
SGLANG_OPT_DEEPGEMM_MEGA_MOE_NUM_MAX_TOKENS_PER_RANK=20480 \
sglang serve --trust-remote-code --model-path moonshotai/Kimi-K3 \
  --tp-size 32 --ep-size 32 \
  --enable-dp-attention --dp-size 4 --enable-dp-lm-head \
  --nnodes 4 --node-rank <rank> --dist-init-addr <node0-ip>:20000 \
  --moe-a2a-backend megamoe --moe-runner-backend deep_gemm \
  --kv-cache-dtype fp8_e4m3 --mamba-ssm-dtype bfloat16 \
  --mamba-radix-cache-strategy extra_buffer_lazy --mem-fraction-static 0.92 \
  --reasoning-parser kimi_k3 --tool-call-parser kimi_k3 --host 0.0.0.0 --port 30000
```

TensorRT-LLM, DEP16 config (`EXTRA_LLM_API_FILE`):

```yaml
tensor_parallel_size: 16
enable_attention_dp: true
moe_expert_parallel_size: 16
max_batch_size: 32
max_num_tokens: 8192
max_seq_len: 8192
trust_remote_code: true
disable_overlap_scheduler: false
enable_chunked_prefill: true
cuda_graph_config: {enable_padding: true, max_batch_size: 32}
moe_config: {max_num_tokens: 33024}
```

**Speculative decoding — three engines, three answers:**

| Engine | Draft checkpoint | Tokens |
|---|---|---|
| vLLM | `RedHatAI/Kimi-K3-speculator.dspark`, `{"method":"dspark","num_speculative_tokens":8,"draft_sample_method":"probabilistic","rejection_sample_method":"block"}`; Ascend override uses **7** with `enforce_eager` | 8 (7 on NPU) |
| SGLang | `--speculative-algorithm DSPARK --speculative-draft-model-path RadixArk/Kimi-K3-DSpark --speculative-dspark-block-size 7` (+ `--enable-linear-replayssm-spec`) | block 7 |
| TokenSpeed | "an eight-token verify window uses seven DSpark draft queries" | 7 queries / 8 window |
| TRT-LLM | DSpark; "no MTP or EAGLE-3 head, and its DSpark checkpoints are not compatible with plain `DFlash`" | — |

**⚠️ Note the draft-checkpoint divergence:** vLLM points at `RedHatAI/Kimi-K3-speculator.dspark`
while SGLang points at `RadixArk/Kimi-K3-DSpark`. ⚠️ **TO BE VERIFIED** — whether these are the
same weights re-hosted or genuinely different drafts with different acceptance.

**Open issues / blockers:**

- **SGLang: only 2 of ~24 cells are verified.** "Every other cell is still marked *Final
  Verification In Progress* … **Accuracy has not been re-measured on any cell** — re-measure
  before you rely on one." Also: "The published B300 DSPARK numbers pin the acceptance length
  with `SGLANG_SIMULATE_ACC_LEN`, so **no measured acceptance rate exists for a real workload
  yet**."
- **The KDA state pool is the concurrency ceiling** — "DP, EP, and DCP do not shard it; only
  attention-TP width, SSM dtype, and cache strategy change the per-GPU bill." Sized by
  `--mamba-full-memory-ratio`; under DSPARK the engine holds block-size + 1 = 8 intermediate
  states per request and "an unset `--max-running-requests` resets to 48 under spec."
  In METHODOLOGY §2 terms this is the **state-slot multiplier `S`**: the per-sequence recurrent
  state is never counted once. SGLang's baseline for Kimi-K3 is **S = 5** — 428.6 MiB per slot ×
  5 = **2.25 GB per request** — and DSPARK raises it to S = 8 (block 7 + 1). Size concurrency
  against `S × 428.6 MiB`, not against one slot
  ([METHODOLOGY §2](../METHODOLOGY.md), [models/kimik3/architecture.md](../models/kimik3/architecture.md) §5).
- **CUDA 13 only** on the vLLM image: "no `-cu129` tag, and the K3-enabled wheels are not on the
  cu129 nightly index. The host needs an **r580+** NVIDIA driver."
- "**Tool calling**: K3 occasionally emit a tool-call format its own parser doesn't expect.
  Suggest to run do schema validation and retry."
- DSPARK requires `pp_size == 1` in SGLang; L3 HiCache drops DCP ("storage keys are not
  dcp_rank-aware yet"); "Don't use EP with an a2a backend: a2a buffers reclaim the KV that DCP
  buys."
- `mlx5dv_reg_dmabuf_mr errno 524` → set `NCCL_DMABUF_ENABLE=0` (NCCL 2.28 registers dmabuf by
  default and the kernel/driver may lack support).
- TRT-LLM: build from source required; only matched **DEP16=DEP16** disagg geometry validated at
  scale; `kv_cache_config.tokens_per_block` **must** be 64.
- The SGLang page still carries a pre-release note ("full model weights are scheduled to release
  by July 27, 2026") that the public HF repo has since overtaken — treat dates on that page with
  care.

### 6.4 `Qwen/Qwen3.8-27B`

**Architecture.** `model_type: qwen3_5`, `Qwen3_5ForConditionalGeneration`, text
`qwen3_5_text`; `transformers_version: 5.8.0.dev0`. 64 layers as **16 × (3 × linear_attention +
1 × full_attention)** (`full_attention_interval: 4`); hidden 5120, FFN 17408; Gated Attention
24 Q / 4 KV heads at `head_dim 256` with `partial_rotary_factor 0.25` (64 rotary dims) and
`attn_output_gate: true`; Gated DeltaNet `linear_num_value_heads 48`, `linear_num_key_heads 16`,
`linear_key_head_dim`/`linear_value_head_dim` 128, `linear_conv_kernel_dim 4`,
`mamba_ssm_dtype: float32`; vocab 248,320; `max_position_embeddings: 262144`;
**`mtp_num_hidden_layers: 1`**; mRoPE interleaved `[11,11,10]`, `rope_theta 1e7`; vision tower
depth 27, hidden 1152, patch 16, `spatial_merge_size 2`, `temporal_patch_size 2`,
`out_hidden_size 5120` (local copy: `research/models/qwen3827b/config.json`).

**Parameter count — corrected 2026-09-19, re-verified in this sweep.** The authoritative figure
is the Hub's own safetensors census: **27,781,427,952 parameters (27.78 B), all BF16** →
**51.75 GiB = 55.56 GB**, matching METHODOLOGY §8's pinned 55.56 GB exactly; `usedStorage`
55,623,336,488 B = **51.80 GiB** on disk
[src](https://huggingface.co/api/models/Qwen/Qwen3.8-27B?expand[]=safetensors&expand[]=usedStorage).
A from-`config.json` reconstruction (16 full-attention + 48 linear-attention layers, MLP
3 × 5120 × 17408, gated attention q/k/v/o + output gate, GDN q/k/v/o/gate/conv, untied
248,320-row embedding and head, 27-layer ViT) lands at **27.31 B / 50.9 GiB** `est.` — the
residual 1.7 % is norms, biases and the patch-embed/merger tensors. The earlier **26.87 B /
50.1 GiB** figure in this document was ~3.3 % low. The vLLM recipe's 51.7 GiB BF16 /
28.7 GiB FP8 / 24.6 GiB NVFP4 ladder [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B.json) is
therefore **exact**, not approximate, and the FP8 rung checks out independently at
30,879,676,248 B = 28.76 GiB
[src](https://huggingface.co/api/models/Qwen/Qwen3.8-27B-FP8?expand[]=usedStorage).

| Engine | Level | Min version | GPUs |
|---|---|---|---|
| vLLM | **Released** | recipe says `min_vllm_version: 0.17.0` — **correct, not a legacy value** (corrected 2026-09-19): `qwen3_5.py` is 404 at `v0.16.0` and 200 at `v0.17.0` (2026-03-07) and every release since [src](https://raw.githubusercontent.com/vllm-project/vllm/v0.17.0/vllm/model_executor/models/qwen3_5.py) | `"gb300"`, `"rtx_5090"`, `"rtx_5090_2x"`, `"rtx_pro_6000"`, `"dgx_spark_gb10"`, `"ascend_950pr"` = `"verified"`; h100/h200/b200/b300/gb200/mi3xx profiles exist |
| SGLang | **Released** | `qwen3_5.py` first present at **v0.5.9** (corrected 2026-09-19: 404 at `v0.5.8`, 200 at `v0.5.9`) and every release since [src](https://raw.githubusercontent.com/sgl-project/sglang/v0.5.9/python/sglang/srt/models/qwen3_5.py); docker `lmsysorg/sglang:latest` | `h200`, `rtx6000`, `rtx5090`, `dgx-spark`, `gb300` |
| TensorRT-LLM | **Released** | `Qwen3_5ForConditionalGeneration`, modality `L + I + V` | Blackwell/Hopper |
| TokenSpeed | Yes | `Qwen/Qwen3.8-27B-FP8`, `--world-size 1` | single GPU |
| Ollama | Yes | `ollama run qwen3.8` (`27b`) | consumer |

**Checkpoint matrix** (the one model in this repo with a real quantization menu):

| Checkpoint | Format | Size | Engines / GPUs |
|---|---|---|---|
| `Qwen/Qwen3.8-27B` | BF16 | 51.7 GiB, `vram_minimum_gb: 67` | everything |
| `Qwen/Qwen3.8-27B-FP8` | FP8 blockwise | 28.7 GiB, `vram_minimum_gb: 38` | h100…mi355x, rtx_5090_2x, ascend_950pr |
| `Inferact/Qwen3.8-27B-NVFP4` | NVFP4 W4A4 | 24.6 GiB, `vram_minimum_gb: 32` | b200/b300/gb200/gb300/rtx_5090 |
| `nvidia/Qwen3.8-27B-NVFP4` | ModelOpt NVFP4, FP4 `lm_head` | 21.9 GB on disk (`usedStorage` 21,934,506,600 B = 20.43 GiB — the recipe's "21.9 GiB" is a GB figure mislabelled as GiB) | **`rtx_pro_6000`, `dgx_spark_gb10` only** — corrected 2026-09-19; the recipe's `nvfp4_nvidia` variant declares `supported_hardware: ["rtx_pro_6000","dgx_spark_gb10"]`, *disjoint* from the Inferact variant's `["b200","b300","gb200","gb300","rtx_5090","rtx_5090_2x"]` [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B.json) |
| `RadixArk/Qwen3.8-27B-NVFP4` / `…-NVFP4-BF16-LMHead` | NVFP4 W4A4, FP4 vs BF16 head | BF16 head is ~1.7 GB larger on disk, ~3.2 GB at runtime | SGLang SM12x cells |
| `RedHatAI/Qwen3.8-27B-INT4`, `Eco-Tech/Qwen3.8-27B-w8a8` | INT4 / Ascend W8A8 | — | listed in vLLM catalogue; W8A8 needs `--quantization ascend` |

SGLang's note on the three NVFP4 exports is worth carrying forward verbatim: "NVIDIA's own
export is that same W4A4 body with that same FP4 head: identical quantized-layer map (FP8
attention and GDN projections, NVFP4 MLPs), identical tensor set, identical 21.9 GB on disk. …
The two RadixArk checkpoints declare `kv_cache_quant_algo: FP8`, so SGLang's default
`--kv-cache-dtype auto` already puts their KV pool in `fp8_e4m3`. The NVIDIA export ships no
`kv_cache_scheme`, so `auto` would leave its pool in BF16 instead."
[src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md)

**Launch commands.**

vLLM, RTX PRO 6000 (TP1) [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B/hw/rtx_pro_6000.json):

```bash
VLLM_USE_RUST_FRONTEND=1 vllm serve Qwen/Qwen3.8-27B \
  --max-num-seqs 8 --tensor-parallel-size 1 \
  --enable-auto-tool-choice --tool-call-parser qwen3_xml \
  --reasoning-parser qwen3 --mm-encoder-tp-mode data
# + optional: --speculative-config '{"method":"mtp","num_speculative_tokens":5}'
```

SGLang, RTX PRO 6000 [src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md):

```bash
sglang serve --trust-remote-code --model-path <checkpoint> \
  --kv-cache-dtype fp8_e4m3 --mem-fraction-static 0.85 \
  --attention-backend flashinfer --chunked-prefill-size 2048 \
  --reasoning-parser qwen3 --tool-call-parser qwen3_coder \
  --host 0.0.0.0 --port 30000
```

YarN to 1M, quoted from the model card
[src](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/README.md):

```bash
# vLLM
VLLM_ALLOW_LONG_MAX_MODEL_LEN=1 vllm serve ... --hf-overrides '{"text_config": {"rope_parameters": {"mrope_interleaved": true, "mrope_section": [11, 11, 10], "rope_type": "yarn", "rope_theta": 10000000, "partial_rotary_factor": 0.25, "factor": 4.0, "original_max_position_embeddings": 262144}}}' --max-model-len 1000000
# SGLang
SGLANG_ALLOW_OVERWRITE_LONGER_CONTEXT_LEN=1 python -m sglang.launch_server ... --json-model-override-args '{"text_config": {"rope_parameters": {... same ...}}}' --context-length 1000000
```

Card caveat: "All the notable open-source frameworks implement **static YaRN**, which means the
scaling factor remains constant regardless of input length, **potentially impacting performance
on shorter texts.** … if the typical context length for your application is 524,288 tokens, it
would be better to set `factor` as 2.0."

**Speculative decoding — four options, and they are not interchangeable:**

| Method | vLLM | SGLang |
|---|---|---|
| **MTP** (in-checkpoint, 1 layer) | `{"method":"mtp","num_speculative_tokens":3}` (5 on RTX PRO 6000 and DGX Spark) | `--speculative-algorithm EAGLE --speculative-num-steps 3 --speculative-eagle-topk 1 --speculative-num-draft-tokens 4` ("originally documented with `NEXTN`, an alias of `EAGLE` — same algorithm") |
| **DSpark** | — | `--speculative-algorithm DSPARK --speculative-draft-model-path RadixArk/Qwen3.8-27B-DSpark`; "does **not** take `--speculative-num-draft-tokens`: its verify window is `--speculative-dspark-block-size` (gamma) **+ 1**, and gamma is auto-inferred from the draft checkpoint (7 → D = 8)" |
| **DFlash2** | `{"method":"dflash","model":"incoai/Qwen3.8-27B-DFlash2","num_speculative_tokens":7}`; "Needs vLLM >=0.28.0, which shipped vllm-project/vllm#52816. `num_speculative_tokens` must be 7 (block_size 8 minus one)" | `--speculative-algorithm DFLASH --speculative-draft-model-path incoai/Qwen3.8-27B-DFlash2 --speculative-num-draft-tokens 8` |
| **Ascend MTP** | `{"method":"qwen3_5_mtp","num_speculative_tokens":3,"enforce_eager":true}` | — |

Note vLLM uses **7** for DFlash2 and SGLang **8** for the same draft — the off-by-one is the
anchor token, and both are correct for their own engine's convention.

**`meas.` numbers published by the engines** (their measurements, not mine):

- vLLM, 2× RTX 5090 at 262,144 context [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B):

  | precision | KV tokens | weights/GPU | MTP acceptance |
  |---|---|---|---|
  | FP8 | 377,456 | 14.28 GiB | 0.771 |
  | NVFP4 (Inferact) | 445,875 | 12.02 GiB | 0.897 |
  | NVFP4 (unsloth) | 920,517 | 10.64 GiB | 0.788 |

- SGLang, SM12x sweep on v0.5.19: "202 cells over the five checkpoints, four speculative
  options, two serving tiers and two GDN state dtypes, full 1319-question GSM8K on each,
  **93.18–95.15%**."
- SGLang, GDN state slot cost: "**153.9 MB at `float32`** … and **78.4 MB at `bfloat16`**"; on an
  RTX 5090 with no speculation, "97,280 KV tokens at bf16 against 68,588 at fp32."

**Open issues / blockers:**

- `--reasoning-parser qwen3` is effectively mandatory: "the chat template opens every assistant
  turn with `<think>`, so without it the entire reasoning block lands in `message.content` and a
  2048-token budget can be spent before the answer starts."
- SM120 MTP needs a FlashInfer newer than `0.6.15.post1` (prefill `plan` must accept
  `uniform_q_len`); otherwise `--attention-backend triton`.
- On 32 GB cards, NVFP4 needs `--enforce-eager` on a single RTX 5090 ("Without `--enforce-eager`,
  startup dies in CUDA graph capture with `torch.OutOfMemoryError: Tried to allocate 784.00 MiB`
  … **Raising or lowering `--gpu-memory-utilization` does not help**"). Not an RTX PRO 6000
  problem at 96 GB, but the same class of failure is what the `--max-num-seqs 8` pin guards.
- DGX Spark: `--mem-fraction-static 0.80`, not 0.85 — "0.85 of 128GB leaves ~8GB for the OS —
  exactly DGX OS earlyoom's SIGTERM threshold … 15 of the 48 cells were killed that way."
- `--chunked-prefill-size 2048` on SM12x: "decode steps stall behind each prefill chunk on hybrid
  GDN models, and 8192-token chunks stall them ~600ms at a time."
- `preserve_thinking` is on by default and "improves KV cache utilization"; disabling it changes
  the prefix-cache hit profile of multi-turn agent traffic.
- Ascend DFlash2 caveat: "NPU currently guarantees lossless verification only for greedy
  requests; use `temperature=0` and `top_k=1`."

### 6.5 `NemoStation/Marlin-2B` — no engine supports it

This model is **gated** and cannot be read anonymously: `curl` on
`https://huggingface.co/NemoStation/Marlin-2B/raw/main/README.md` returns *"Access to model
NemoStation/Marlin-2B is restricted. You must have access to it and be authenticated to access
it."* The Hub API still exposes metadata
[src](https://huggingface.co/api/models/NemoStation/Marlin-2B):

| Field | Value |
|---|---|
| `gated` | `"auto"` (accept terms; fields: Full name, Affiliation, intended use) |
| `pipeline_tag` | `video-text-to-text` |
| `library_name` | `transformers` |
| `architectures` | `["MarlinForConditionalGeneration"]` |
| `model_type` | `qwen3_5` |
| `auto_map` | `{"AutoModelForCausalLM": "modeling_marlin.MarlinForConditionalGeneration"}` — **custom remote code** |
| `base_model` | `Qwen/Qwen3.5-2B` (`finetune`) |
| `transformersInfo` | `{"auto_model":"AutoModelForCausalLM","custom_class":"modeling_marlin.MarlinForConditionalGeneration","processor":"AutoProcessor"}` |
| Weight shards | `model-00001-of-00002.safetensors` 4,999,157,736 B + `model-00002-of-00002.safetensors` 444,519,488 B = **5.44 GB** (≈ 2.72 B params at BF16, `est.`) |
| Tags | `video`, `multimodal`, `video-captioning`, `temporal-grounding`, `VLM`, `custom_code`, `license:apache-2.0` |
| Last modified | 2026-05-30 |
| Downloads / likes | 4,912 / 593 |

**Engine support: none, on any engine.**

| Engine | Evidence |
|---|---|
| vLLM | `MarlinForConditionalGeneration` **absent** from `vllm/model_executor/models/registry.py` on `main` (grep); absent from `docs/models/supported_models.md`; absent from the 396-entry recipe catalogue [src](https://recipes.vllm.ai/models.json) |
| SGLang | `python/sglang/srt/models/marlin.py` → HTTP 404 on `main`; absent from the multimodal supported-models page |
| TensorRT-LLM | `MarlinForConditionalGeneration` absent from `docs/source/models/supported-models.md` |
| TokenSpeed / Ollama / llama.cpp | not listed |

Beware a **name collision**: "Marlin" is also the name of a widely-used W4A16 GEMM kernel, and it
appears throughout this document as `--moe-backend marlin` / `--moe-runner-backend marlin` for
Kimi-K3 on Hopper. Those are unrelated to this model.

**Practical path to serving it today:** Hugging Face `transformers` with `trust_remote_code=True`
(the checkpoint's own `modeling_marlin.py`, 23,098 B) plus `AutoProcessor`. The chat template
emits `<|vision_start|><|image_pad|><|vision_end|>` and `<|vision_start|><|video_pad|><|vision_end|>`
and raises `'System message cannot contain images.'` / `'…videos.'` — so the processor handles
both image and video items.

⚠️ **TO BE VERIFIED** — the brief's claims that Marlin-2B requires `transformers>=5.7.0` and
`torchcodec`. The README that would state this is gated and unreadable without accepting the
terms; the repo's file list contains no `requirements.txt`. The `transformers>=5.7.0` floor is
*plausible* by analogy (TRT-LLM's MiniCPM-V 4.6 footnote states "Requires `transformers>=5.7.0`
… The Qwen3.5-hybrid text tower runs in BF16"
[src](https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/docs/source/models/supported-models.md)),
and `torchcodec` is the current `transformers` video-decoding dependency, but neither is
confirmed for this repo. **Accept the gate and re-read the card before committing.**

---

## 7. Multimodal serving specifics

### 7.1 Token budgets

| Model | Image budget | Video | Source |
|---|---|---|---|
| DS-V4.1-Flash | `max_image_tokens: 1024` per image, `min_pixels: 295936`, ViT patch 14 with 3× downsampling aligner. "There is **no limit on images per prompt**." | Not supported | `config.json`; [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash) |
| Kimi-K3 | MoonViT-V2 401M; TokenSpeed: "Preserve the checkpoint's `media_proc_cfg.in_patch_limit=65536`; silently falling back to K2.5's 16384-patch default reduces OCR resolution" | **"The open-source K3 serving contract currently supports image input only — its processor rejects video and audio input."** | [SGLang cookbook](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3.md); [TokenSpeed](https://lightseek.org/tokenspeed/recipes/models) |
| Qwen3.8-27B | vision tower depth 27, patch 16, `spatial_merge_size 2`, `temporal_patch_size 2` | **Yes.** Default `fps=2`, `do_sample_frames=True`; `--media-io-kwargs '{"video": {"num_frames": -1}}'` in vLLM enables per-request `mm_processor_kwargs` fps control (**vLLM only**). For hour-scale video set `video_preprocessor_config.json` `longest_edge` to **469,762,048** (= 224k video tokens) | [model card](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/README.md); [vLLM #34330](https://github.com/vllm-project/vllm/pull/34330) / [SGLang #18467](https://github.com/sgl-project/sglang/pull/18467) |
| Marlin-2B | `<\|image_pad\|>` / `<\|video_pad\|>` placeholders in the chat template | video-text-to-text pipeline tag | [HF API](https://huggingface.co/api/models/NemoStation/Marlin-2B) |

### 7.2 Encoder placement

**vLLM** offers two mutually exclusive switches, both present on the DS-V4.1 and Qwen3.8-27B
recipes:

- `--language-model-only` — "Skip loading the vision encoder for text-only workloads — frees
  VRAM for KV cache." (The verified DS-V4.1 GB200 TP4 and 1P1D runs were text-only.)
- `--mm-encoder-tp-mode data` — "Run the vision encoder in data-parallel mode — avoids TP comm
  overhead on the small encoder." For DS-V4.1: "at 32 layers / hidden 1024 the encoder is small
  enough that TP communication costs more than it saves, which can significantly reduce TTFT for
  multi-image requests." TokenSpeed gives the K3 rule from head count: "The vision encoder has 12
  attention heads. For an 8-way text TP deployment, use `--mm-encoder-tp-mode data` so each rank
  runs the vision encoder at TP1 on a different whole image."

**SGLang** exposes a transport dimension instead of a parallelism one — the processor→scheduler
feature path [src](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3.md):

| Selection | Path |
|---|---|
| Auto · single-node Unified CUDA | CUDA IPC |
| Auto · Unified GB200/GB300 | CUDA VMM when IMEX is available; CPU otherwise |
| Auto · PD or other topologies | CPU |
| `--mm-feature-transport cpu` | CPU, no GPU feature pool |

"CUDA IPC and CUDA VMM reserve up to `SGLANG_MM_FEATURE_CACHE_MB` (1 GiB by default) on the base
GPU and fall back to CPU per tensor when full." For K3 specifically, "MM encoder DP … is built
in. K3 shards complete images across TP ranks, so **leave `--mm-enable-dp-encoder` unset**."

**ViT CUDA graphs**: keep off for general serving. "Enable `SGLANG_VIT_ENABLE_CUDA_GRAPH=1` only
for ViT-only / EPD encoder workloads with recurring image shapes and spare HBM. The win is
confined to the encoder — no reliable end-to-end TTFT/TPOT gain in full-model serving … The
default cache captures after two hits and falls back to eager above 6,144 tokens."

**TensorRT-LLM** has two encoder optimizations, available to `Qwen3_5ForConditionalGeneration`:
`multimodal_config.encoder_side_stream_max_ahead` (prefetch encoder work on a separate CUDA
stream; mutually exclusive with `encoder_cuda_graph`; increases peak GPU memory) and
`multimodal_config.encoder_cache_max_bytes` (cross-request LRU cache of encoder embeddings; "a
request reuses cached embeddings only when **all** of its items hit the cache … mixed-modality
requests bypass the cache")
[src](https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/docs/source/models/supported-models.md).

### 7.3 EPD (encoder / prefill / decode) disaggregation

SGLang supports EPD for Kimi-K3 on the public `kimi-k3` branch (`--encoder-only` vision role +
`--language-only` prefill role + normal decode role). TensorRT-LLM lists *EPD Disaggregated
Serving = Yes* for `Qwen3_5ForConditionalGeneration` and `Qwen3VL*`, *No* for most others.
vLLM does not expose an encoder-only role; its lever is DP-encoder within the replica.

---

## 8. Master matrix — model × GPU × engine

Legend: **R** = in a numbered release · **M** = `main`/nightly/preview image only ·
**V** = vendor-published *verified* profile exists · **—** = no profile / not supported ·
**n/a** = engine does not target that hardware.

### DeepSeek-V4.1-Flash (and its NVFP4 sibling)

| GPU | vLLM | SGLang | TRT-LLM | Dynamo | TokenSpeed | Min GPUs (est.) |
|---|---|---|---|---|---|---|
| A100 | — | — | — | — | — | — |
| H100 | M + V (Engram CPU offload) | — | — | — | — | 8 (TP8) |
| H200 | M + V (TP4 ⚠️) | M + V (TP8/EP8) | — | — | — | 8 (TP8) |
| B200 | M + V (TP4) | M + V (TP4/EP4) | — | — | — | 4 |
| GB200 | M + V (TP4; 1P1D verified) | — | — | **dev build, 8× GB200** | — | 4 |
| B300 | M + V (TP2 default; TP4 advised) | M + V (TP4/EP4) | — | — | — | 2 **only with Engram CPU offload** (§6.1); 4 otherwise |
| GB300 | M + V (TP4) | M + V (TP4/EP4) | — | — | **Yes** — per-commit Slurm 1P1D CI (TP4+TP4) | 4 |
| GB300 + **NVFP4 ckpt** | M + V (TP4) | M + V (TP4) | — | — | — | 4 |
| RTX PRO 6000 | — | — | — | — | — | — |
| MI355X / MI350X | M + V (TP4, `aiter`) | M + V (TP4/EP4, no radix cache) | n/a | — | — | 4 |

### Kimi-K3

| GPU | vLLM | SGLang | TRT-LLM | Dynamo | TokenSpeed | Min GPUs (est.) |
|---|---|---|---|---|---|---|
| A100 | — | — | — | — | — | — |
| H100 | **R** (0.27.0+), profile: 4×8 TP32 | **R** (0.5.17+), cell `verified:false` | — (SM100 only) | — | Hopper path w/ `flashmla`+bf16 KV | **32** |
| H200 | **R** + V, 2×8 TP16 | **R**, `verified:false`, TP16/EP16 | — | **dev build** | — | **16** |
| B200 | **R** + V, 2×8 TP16 | **R**, PP2×TP8 / DCP8+EP8 | **R** (functional, CI) | — | `flashinfer_trtllm` MoE | **16** |
| GB200 | **R** + V, 4×4 TP16 | **R**, TP16/DCP16 | **R**, TEP16 validated e2e | **dev build** | — | **16** |
| B300 | **R** + V, 1×8 TP8 (`default_hardware`) | **R**, 1×8 TP8 — **the only verified cells** | needs GB300-class mem ⚠️ | — | 8× B300 recipe | **8** |
| GB300 | **R** + V, 2×4 TP8 | **R**, TP8/DCP8 | **R** — validation platform (DEP16/TEP16/TEP8) | **dev build** | — | **8** |
| RTX PRO 6000 | — | — | — | — | — | — |
| MI355X | **R** + V, 1×8 TP8 | **R**, TP8 (`verified:false`) | n/a | — | 8× gfx950 recipe | **8** |
| Ascend 910C / A3 | **R** + V (`Eco-Tech/Kimi-K3-w4a8`) | **R** (TP64/DP4 + DeepEP) | n/a | — | — | 4× 910C |

### Qwen3.8-27B

| GPU | vLLM | SGLang | TRT-LLM | TokenSpeed | Ollama | Min GPUs |
|---|---|---|---|---|---|---|
| A100 | — | — | — | — | — | — |
| H100 / H200 | **R**, TP1 | **R** + V (H200; BF16/FP8 only) | **R** | **R** (FP8, world-size 1) | — | **1** |
| B200 / B300 / GB200 | **R**, TP1 | — | **R** | — | — | **1** |
| GB300 | **R** + V, TP1 | **R** + V | **R** | — | — | **1** |
| **RTX PRO 6000** | **R** + V, TP1, MTP-5 | **R** + V, flashinfer, GSM8K 94.01–95.00% | ⚠️ SM120 untested | — | — | **1** |
| RTX 5090 (32 GB) | **R** + V (NVFP4, `--enforce-eager`) | **R** + V (pinned pools) | — | — | — | 1–2 |
| DGX Spark GB10 | **R** + V (`--gpu-memory-utilization 0.8`) | **R** + V (`--mem-fraction-static 0.80`) | beta (1.2) | — | — | **1** |
| MI300X/325X/355X | **R**, TP1 | — | n/a | — | — | **1** |
| Ascend 950PR | **R** + V (W8A8 / FP8) | — | n/a | — | — | **1** |

### Marlin-2B

No engine. `transformers` + `trust_remote_code=True` only. See §6.5.

---

## 9. Choosing an engine — decision rules that fall out of the above

1. **Serving DeepSeek-V4.1-Flash today?** SGLang `dev-dsv41` on GB300/B300 with TP4/EP4, DSpark
   for interactive and speculation off for batch. It is the only stack where the model's own
   kernel set (`dsv4` / `flashinfer_mxfp4` / `flashinfer_cutedsl`) is auto-resolved and where the
   verified-cell matrix covers five GPU families. vLLM nightly is the alternative and the only
   path on H100 (Engram CPU offload). TRT-LLM is not an option (the architecture is absent from
   its matrix). **TokenSpeed is** an option — corrected 2026-09-19: it ships a V4.1-Flash FlatKV
   recipe and a per-commit GB300 1P1D CI job (§2.5) — but it publishes no version number, so you
   are pinning a container tag, not a release.
2. **Serving Kimi-K3 on one 8-GPU large-HBM Blackwell node** (B300 268 GB, GB300 288 GB or
   MI355X 288 GB)**?** SGLang B300 1×8 TP8 — the only two cells in
   the whole cookbook with a published serving round on final weights. With 16 GB200/GB300 and a
   Slurm cluster, TRT-LLM TEP16 is the shape NVIDIA validated end-to-end.
3. **Serving Kimi-K3 on Hopper?** Expect 16 H200 or 32 H100, Marlin W4A16 MoE, FlashMLA decode,
   and — on vLLM H100 — a 5-concurrent-request, 32K-context cap. Budget accordingly; this is a
   capability box, not a throughput box.
4. **Serving Qwen3.8-27B?** One GPU, any Blackwell. RTX PRO 6000 at 96 GB runs BF16 and every
   quantized variant with real NVFP4 kernels. Take MTP (`num_speculative_tokens: 5` on SM120),
   `--attention-backend flashinfer`, `--chunked-prefill-size 2048`.
5. **Need cross-node routing, PD and KV reuse across replicas?** Dynamo over your chosen engine —
   but note KVBM is 🚧 on the SGLang backend, and both DS-V4.1 and K3 are only in *experimental,
   non-QA-gated* dev tags.
6. **Never** plan A100 capacity for any of these models, and never plan RTX PRO 6000 capacity for
   DS-V4.1 or Kimi-K3.

---

## Open questions

Every **⚠️ TO BE VERIFIED** item in this document, consolidated.

| # | Item | Why it is open | How to close it |
|---|---|---|---|
| 1 | ~~**`pplx` as a vLLM all2all backend**~~ **CLOSED 2026-09-19** | **Removed.** `vllm/config/parallel.py` at `v0.29.0` warns "The 'pplx' all2all backend has been removed" and rewrites it to `allgather_reducescatter`; no `PplxAll2AllManager` exists in `all2all.py` on that tag. It survives only in SGLang's `--moe-a2a-backend` menu | Closed — [parallel.py@v0.29.0](https://raw.githubusercontent.com/vllm-project/vllm/v0.29.0/vllm/config/parallel.py) |
| 2 | **"Wide-EP" as a named TRT-LLM feature** | The 1.3 supported-models doc documents `moe_expert_parallel_size` + `enable_attention_dp` (DEP/TEP), not "wide-EP" | Read the `1.3.0rcNN` release notes on nvidia.github.io once 1.3.0 GA ships |
| 3 | **Dynamo KVBM tier naming (G1/G2/G3/G4) and `DYN_KVBM_*` env vars** | `docs.nvidia.com/dynamo/components/kvbm` currently serves a KV-offloading page (LMCache / HiCache / FlexKV) with no tier naming; `docs/architecture/kvbm_*.md` returns 404 in the repo | Fetch the Dynamo 1.5.0 docs bundle or the `lib/kvbm` source tree |
| 4 | **TokenSpeed version, licence and release cadence** | Named as a recommended engine by two of the repo's model cards and registered inside vLLM as `TOKENSPEED_MLA`, but publishes no version on its recipes/docs pages — re-checked 2026-09-19: **zero** occurrences of "version" or "License" on the recipes page. Now higher-stakes than it looked, since TokenSpeed turns out to have a DeepSeek-V4.1-Flash recipe with per-commit GB300 CI (§2.5) | Check `github.com/lightseekorg/tokenspeed` releases and `pip index versions tokenspeed` |
| 5 | **vLLM DS-V4.1 H200 TP4 profile viability** | `est.` weights/GPU = 118.8 GiB vs a 131 GiB card — inside the card but outside a 0.90-utilization budget, leaving ~0 for KV. SGLang emits TP8/EP8 for the same GPU | Boot the vLLM TP4 H200 profile and read back the reported KV pool size |
| 6 | **DS-V4.1-Flash-NVFP4 and prefix caching** | NVIDIA's own vLLM command passes `--no-enable-prefix-caching` without explanation | Run the same command with prefix caching on and check for correctness/startup failure |
| 7 | **Kimi-K3 DSpark draft divergence** | vLLM points at `RedHatAI/Kimi-K3-speculator.dspark`, SGLang at `RadixArk/Kimi-K3-DSpark` | Compare the two repos' `config.json` and tensor lists via the HF API |
| 8 | **AMD MoE quantization for Kimi-K3: a4w4 vs a8w4** | vLLM ships `VLLM_ROCM_USE_AITER_MOE_SITUV2=1` (a4w4) and says *do not* set `AITER_SITUV2_A8W4=1`; SGLang ships `AITER_SITUV2_A8W4=1` as its default and measures a8w4 faster (537.3 vs 530.8 tok/s) | Benchmark both on the same 8× MI355X node with the same trace |
| 9 | **SGLang "hybrid models incompatible with PD"** | The generic PD page says KV dtype must match and hybrid models are incompatible; the K3 cookbook says PD moves both MLA KV and KDA state | Run the K3 PD cells and confirm; the generic page is probably stale |
| 10 | **Marlin-2B's `transformers>=5.7.0` and `torchcodec` requirements** | The README is gated; the repo ships no requirements file | Accept the HF gate, read the card, and inspect `modeling_marlin.py` imports |
| 11 | **Marlin-2B engine roadmap** | Absent from every engine registry and from vLLM's 396-recipe catalogue | Open a vLLM "Request a recipe" issue, or check whether `qwen3_5` + a Qwen3-VL processor override can serve it unmodified |
| 12 | ~~**`"vram_gb": 2144` for 8× B300**~~ **CLOSED 2026-09-19** | Not a recipe-site quirk: NVIDIA's DGX B300 publishes "Total GPU Memory 2.1 TB" and the HGX table "2.1 TB (B300) / 1.4 TB (B200)". 2144/8 = 268 GB and 1440/8 = 180 GB are the shipping per-GPU capacities | Closed — [DGX B300](https://www.nvidia.com/en-us/data-center/dgx-b300/), [DGX B200](https://www.nvidia.com/en-us/data-center/dgx-b200/), [HGX](https://www.nvidia.com/en-us/data-center/hgx/) |
| 13 | ~~**vLLM `min_vllm_version: 0.17.0` for Qwen3.8-27B**~~ **CLOSED 2026-09-19** | Not legacy — it is exact. `qwen3_5.py` returns 404 at `v0.16.0` and 200 at `v0.17.0` (2026-03-07). The earlier "present at v0.24.0" probe result in this document was simply the first tag that had been tried | Closed — [qwen3_5.py@v0.17.0](https://raw.githubusercontent.com/vllm-project/vllm/v0.17.0/vllm/model_executor/models/qwen3_5.py) |
| 18 | **Kimi-K3 `vram_minimum_gb: 1680` vs 1,561 GB of actual weights** | A 1.076× headroom factor, where the DS-V4.1 variant schema uses 1.2× on the same site | Read the recipe site's variant schema, or diff `vram_minimum_gb` against `usedStorage` across several recipes |
| 19 | **TRT-LLM Kimi-K3 per-rank figures do not sum** | The guide gives DEP16 = 114 GB + 90 GB = "210 GB per rank" (114 + 90 = 204) and TEP8 = "213 GB" with a 181 GB expert share | Run DEP16 and TEP8 and read the load-time peak |
| 20 | **NVFP4 W4A4 GEMM on RTX PRO 6000 specifically** | The `FlashInferCutlassNvFp4LinearKernel` "cutlass path, not an emulation fallback" statement is made in the vLLM recipe's **2× RTX 5090** section; same SM120, different board, no measurement published on the PRO 6000 | Boot `nvidia/Qwen3.8-27B-NVFP4` on an RTX PRO 6000 and confirm the selected linear kernel in the startup log |
| 14 | **B300 vs "GB300-class per-GPU memory" for TRT-LLM Kimi-K3 DEP16/TEP8** | The guide names GB300 for the 210/213 GB-per-rank recipes but does not say whether a 288 GB B300 also qualifies | Run TEP8 on 8× B300 and watch the load-time peak |
| 15 | **DS-V4.1 32×32 UE8M0 FP8 blocks on SM120 (RTX PRO 6000)** | No engine publishes an RTX PRO 6000 profile for this model, so the dense-GEMM path on SM120 is untested | Not urgent — the model does not fit on one or two 96 GB cards anyway |
| 16 | **`num_nextn_predict_layers: 3` in the DS-V4.1 config** | Both vLLM and SGLang state V4.1 dropped MTP and that DSpark is the only speculative path, yet the field is present with value 3 | Inspect `vllm/models/deepseek_v41/` for whether the MTP layers are loaded, ignored, or repurposed by DSpark |
| 17 | **SGLang generic speculative-decoding doc omits DSPARK** | Three cookbook pages emit `--speculative-algorithm DSPARK`; the feature doc lists only EAGLE/MTP/UNO/DFLASH/STANDALONE/NGRAM | Read `python/sglang/srt/speculative/` on the v0.5.20 tag for the registered algorithm names |

---

## Sources

Model cards and checkpoint metadata

- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/README.md
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/inference/README.md
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/blob/main/config.json
- https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md
- https://huggingface.co/moonshotai/Kimi-K3/raw/main/README.md
- https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/README.md
- https://huggingface.co/api/models/NemoStation/Marlin-2B
- https://huggingface.co/NemoStation/Marlin-2B/raw/main/README.md (gated — access restricted)
- https://huggingface.co/RadixArk/Qwen3.8-27B-NVFP4
- https://huggingface.co/RadixArk/Qwen3.8-27B-NVFP4-BF16-LMHead
- https://huggingface.co/nvidia/Qwen3.8-27B-NVFP4
- https://huggingface.co/Qwen/Qwen3.8-27B-FP8

vLLM

- https://pypi.org/pypi/vllm/json
- https://github.com/vllm-project/vllm/releases
- https://recipes.vllm.ai/llms.txt
- https://recipes.vllm.ai/models.json
- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash
- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash.json
- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/h100.json
- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/h200.json
- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/b200.json
- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/b300.json
- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/gb200.json
- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/gb300.json
- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/mi355x.json
- https://recipes.vllm.ai/moonshotai/Kimi-K3
- https://recipes.vllm.ai/moonshotai/Kimi-K3.json
- https://recipes.vllm.ai/moonshotai/Kimi-K3/hw/h100.json
- https://recipes.vllm.ai/moonshotai/Kimi-K3/hw/b300.json
- https://recipes.vllm.ai/moonshotai/Kimi-K3/hw/gb300.json
- https://recipes.vllm.ai/Qwen/Qwen3.8-27B
- https://recipes.vllm.ai/Qwen/Qwen3.8-27B.json
- https://recipes.vllm.ai/Qwen/Qwen3.8-27B/hw/rtx_pro_6000.json
- https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/registry.py
- https://raw.githubusercontent.com/vllm-project/vllm/main/docs/models/supported_models.md
- https://raw.githubusercontent.com/vllm-project/vllm/main/docs/serving/expert_parallel_deployment.md
- https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/disagg_prefill.md
- https://raw.githubusercontent.com/vllm-project/vllm/main/docs/design/prefix_caching.md
- https://raw.githubusercontent.com/vllm-project/vllm/main/docs/configuration/optimization.md
- https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/models/deepseek_v41/__init__.py
- https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/models/kimi_k3/__init__.py
- https://raw.githubusercontent.com/vllm-project/vllm/v0.29.0/vllm/model_executor/models/qwen3_5.py
- https://github.com/vllm-project/vllm/pull/34330
- https://github.com/vllm-project/vllm/pull/53940
- https://github.com/vllm-project/vllm/pull/52816
- https://github.com/vllm-project/vllm/pull/56228
- https://github.com/vllm-project/vllm/pull/56503

SGLang

- https://pypi.org/pypi/sglang/json
- https://github.com/sgl-project/sglang/releases
- https://docs.sglang.io/llms.txt
- https://docs.sglang.io/cookbook/autoregressive/DeepSeek/DeepSeek-V4_1.md
- https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3.md
- https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md
- https://docs.sglang.io/docs/advanced_features/speculative_decoding.md
- https://docs.sglang.io/docs/advanced_features/expert_parallelism.md
- https://docs.sglang.io/docs/advanced_features/pd_disaggregation.md
- https://docs.sglang.io/docs/advanced_features/epd_disaggregation.md
- https://docs.sglang.io/docs/advanced_features/hicache.md
- https://docs.sglang.io/docs/advanced_features/hicache_best_practices.md
- https://docs.sglang.io/docs/advanced_features/dcp.md
- https://docs.sglang.io/docs/supported-models/generative_models.md
- https://docs.sglang.io/docs/supported-models/multimodal_language_models.md
- https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/models/kimi_k3.py
- https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/models/deepseek_v4.py
- https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/configs/deepseek_v41.py
- https://raw.githubusercontent.com/sgl-project/sglang/v0.5.20/python/sglang/srt/models/qwen3_5.py
- https://github.com/sgl-project/sglang/tree/kimi-k3
- https://github.com/sgl-project/sglang/pull/18467
- https://github.com/sgl-project/sglang/pull/35629

TensorRT-LLM

- https://pypi.org/pypi/tensorrt-llm/json
- https://nvidia.github.io/TensorRT-LLM/release-notes.html
- https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/docs/source/models/supported-models.md
- https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/docs/source/deployment-guide/deployment-guide-for-kimi-k3-on-trtllm.md
- https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/examples/models/core/deepseek_v4/README.md
- https://github.com/NVIDIA/TensorRT-LLM/pull/18274

NVIDIA Dynamo

- https://pypi.org/pypi/ai-dynamo/json
- https://github.com/ai-dynamo/dynamo/releases
- https://github.com/ai-dynamo/dynamo/releases/tag/v1.6.0-deepseek-v4.1-flash-dev.1
- https://github.com/ai-dynamo/dynamo/releases/tag/v1.5.0-kimi-k3-dev.1
- https://github.com/ai-dynamo/dynamo/releases/tag/v1.5.0-deepseek-v4-pro-0813-dev.1
- https://github.com/ai-dynamo/dynamo/blob/main/README.md
- https://docs.nvidia.com/dynamo/knowledge-base/concepts/system-architecture/disaggregated-serving
- https://docs.nvidia.com/dynamo/components/kvbm
- https://docs.nvidia.com/dynamo/resources/feature-matrix
- https://docs.vllm.ai/projects/ascend/en/v0.23.0/tutorials/models/Kimi-K3.html

Other engines and ecosystem

- https://lightseek.org/tokenspeed/recipes/models
- https://github.com/lightseekorg/tokenspeed
- https://raw.githubusercontent.com/ggml-org/llama.cpp/master/README.md
- https://ollama.com/library/qwen3.8
- https://ollama.com/search?q=qwen3.8
- https://github.com/ROCm/aiter/pull/4534
- https://github.com/NVIDIA/Model-Optimizer
- https://www.modelscope.cn/models/Eco-Tech/Kimi-K3-w4a8
- https://inferencex.semianalysis.com/
- https://www.baseten.co/blog/how-baseten-achieved-2x-faster-inference-with-nvidia-dynamo/

---

## Verification log (2026-09-19)

Adversarial re-check of this document's most consequential claims. Every source below was
fetched independently on 2026-09-19 — the document's own inline citations were **not** trusted;
each URL was opened and read. Derivations were recomputed with `python3` from the local
`config.json` copies and the Hub's safetensors census. 34 claims checked: **20 CONFIRMED,
10 CORRECTED, 4 UNVERIFIABLE**.

### CORRECTED

| # | Claim as written | Corrected to | Source |
|---|---|---|---|
| C1 | TokenSpeed has "**no DeepSeek-V4.1 recipe**" (§0, §2.5, §6.1, §8, §9 rule 1: "TRT-LLM and TokenSpeed are not options") | **TokenSpeed publishes a full DeepSeek V4.1-Flash recipe**: its own FlatKV attention backend with a four-group KV cache, a `tokenspeed serve deepseek-ai/DeepSeek-V4.1-Flash --tensor-parallel-size 8 --enable-expert-parallel --moe-backend marlin --disable-prefill-graph` command, `--speculative-algorithm DSPARK`, and a per-commit **GB300 Slurm 1P1D CI** job (`deepseek-v4.1-flash-pd-1p1d-dspark-evalscope-gsm8k-gb300-slurm.yaml`, TP4 prefill + TP4 decode, gated at GSM8K ≥ 0.90). This was the single most consequential error in the document | https://lightseek.org/tokenspeed/recipes/models |
| C2 | Qwen3.8-27B `est.` parameter count **26.87 B → 50.1 GiB BF16**, "a 3 % gap is expected" | **27,781,427,952 params (all BF16) → 51.75 GiB**; `usedStorage` 51.80 GiB. A full from-`config.json` reconstruction gives 27.31 B / 50.9 GiB, so the old figure was ~3.3 % low and the recipe's 51.7 GiB is *exact*, not approximate | https://huggingface.co/api/models/Qwen/Qwen3.8-27B?expand[]=safetensors&expand[]=usedStorage |
| C3 | vLLM EPLB: "not a named flag in current docs ⚠️" (§5) | **`--enable-eplb` + `--eplb-config`** are fully documented, with a parameter table (`window_size`, `step_interval`, `num_redundant_experts`, `use_async`, `policy`, `communicator`), worked example commands and a redundant-expert memory-overhead formula | https://raw.githubusercontent.com/vllm-project/vllm/main/docs/serving/expert_parallel_deployment.md |
| C4 | "⚠️ whether `pplx` is still a selectable vLLM all2all backend in 0.29.0" (§2.1, open question 1) | **Removed.** `parallel.py@v0.29.0` logs "The 'pplx' all2all backend has been removed" and rewrites it to `allgather_reducescatter`; no `PplxAll2AllManager` in `all2all.py`. The same literal also carries three undocumented backends: `mori_high_throughput`, `mori_low_latency`, `nixl_ep` | https://raw.githubusercontent.com/vllm-project/vllm/v0.29.0/vllm/config/parallel.py |
| C5 | vLLM Qwen3.8-27B: "`qwen3_5.py` probes 200 at **v0.24.0**"; `min_vllm_version: 0.17.0` "looks like a legacy value" (§0, §6.4, open question 13) | **First present at v0.17.0** (404 at `v0.16.0`, 200 at `v0.17.0`, uploaded 2026-03-07). The recipe's `min_vllm_version` is exact, not legacy. v0.24.0 was simply the earliest tag previously probed | https://raw.githubusercontent.com/vllm-project/vllm/v0.17.0/vllm/model_executor/models/qwen3_5.py |
| C6 | SGLang Qwen3.8-27B: "`qwen3_5.py` present at **v0.5.15** and every release since" (§0, §6.4) | **First present at v0.5.9** (404 at `v0.5.8`, 200 at `v0.5.9`) | https://raw.githubusercontent.com/sgl-project/sglang/v0.5.9/python/sglang/srt/models/qwen3_5.py |
| C7 | §3.1: "B200 \| SM100 \| **192 GB** → `vram_gb: 1440` for 8" — internally inconsistent (8 × 192 = 1536) | **180 GB/GPU as deployed.** NVIDIA's DGX B200 publishes "GPU Memory 1,440 GB total"; the HGX table gives B200 "Total Memory 1.4 TB"; AMD's competitive table lists "B200 SXM5 180GB". 192 GB is the per-module marketing figure | https://www.nvidia.com/en-us/data-center/dgx-b200/ · https://www.nvidia.com/en-us/data-center/hgx/ · https://www.amd.com/en/products/accelerators/instinct/mi350/mi355x.html |
| C8 | "⚠️ the exact derivation of `2144` for B300; it implies 268 GB/GPU rather than 288" (§3.1, open question 12) | **NVIDIA's own platform figure.** DGX B300 = "Total GPU Memory 2.1 TB" for 8 GPUs → 268 GB/GPU. Not a recipe-site artefact. GB200/GB300 NVL4 trays *do* land on 192/288, so tray and board accounting differ | https://www.nvidia.com/en-us/data-center/dgx-b300/ · https://www.nvidia.com/en-us/data-center/hgx/ |
| C9 | §6.4 checkpoint matrix: `nvidia/Qwen3.8-27B-NVFP4` engines/GPUs = "**same**" as `Inferact/Qwen3.8-27B-NVFP4` | **Disjoint sets.** The recipe's `nvfp4_nvidia` variant declares `supported_hardware: ["rtx_pro_6000","dgx_spark_gb10"]`; the `nvfp4` (Inferact) variant declares `["b200","b300","gb200","gb300","rtx_5090","rtx_5090_2x"]`. Also, the recipe's "21.9 GiB" is really 21.9 **GB** (`usedStorage` 21,934,506,600 B = 20.43 GiB) | https://recipes.vllm.ai/Qwen/Qwen3.8-27B.json |
| C10 | §6.1 fit table: "TP2, B300 → **237.7 GiB**" | **237.6 GiB** (475.26 / 2). Rounding only; fit verdict unchanged | recomputed from https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash?expand[]=usedStorage |

### CONFIRMED

| # | Claim | Source |
|---|---|---|
| V1 | vLLM latest = **0.29.0**, uploaded **2026-09-09**; **no 0.30.0 exists on PyPI**; cadence table 0.25.0→0.29.0 exact to the day | https://pypi.org/pypi/vllm/json |
| V2 | SGLang latest = **0.5.20**, uploaded **2026-09-18**; 0.5.17 (08-08) → 0.5.18 (08-21) → 0.5.19 (09-04) exact | https://pypi.org/pypi/sglang/json |
| V3 | TensorRT-LLM **1.2.1** stable; **1.3.0rc27** uploaded **2026-09-17** | https://pypi.org/pypi/tensorrt-llm/json |
| V4 | `ai-dynamo` **1.5.0** uploaded **2026-09-19**; dev tags `v1.6.0-deepseek-v4.1-flash-dev.1` published 2026-09-12 and `v1.5.0-kimi-k3-dev.1` 2026-09-09 | https://pypi.org/pypi/ai-dynamo/json · https://api.github.com/repos/ai-dynamo/dynamo/releases/tags/v1.6.0-deepseek-v4.1-flash-dev.1 |
| V5 | DS-V4.1 release-tag probes: vLLM `main` 200 / `v0.29.0` 404; SGLang `main` 200 / `v0.5.20` 404. Kimi-K3: vLLM `v0.26.0` 404 → `v0.27.0` 200; SGLang `v0.5.16` 404 → `v0.5.17` 200 | raw.githubusercontent.com probes, all re-run 2026-09-19 |
| V6 | DS-V4.1 recipe: `vram_minimum_gb: 614`, `min_vllm_version: "0.30.0"`, `nightly_required: true`, `default_hardware: "gb200"`, hardware map `{h100,h200,b200,gb200,gb300,b300,mi350x}` all `"verified"`; `hw/a100.json` and `hw/rtx_pro_6000.json` both HTTP 404 | https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash.json |
| V7 | `vram_gb` values exactly as quoted: h100 640, h200 1128, b200 1440, b300 2144, gb200 768, gb300 1152, mi355x 2304, rtx_pro_6000 96 | the per-GPU `hw/*.json` endpoints |
| V8 | DS-V4.1 weights: **475.26 GiB = 510.3 GB** (`usedStorage` 510,310,271,922 B). The §6.1 component table reproduces the Hub's dtype census exactly: I8 557.17 B @ 0.5 B = 259.5 GiB (experts), F8_E4M3 204.02 B @ 1 B = 190.0 GiB (Engram 183.1 + dense 6.9), BF16 1.976 B + F32 0.042 B = 3.9 GiB, + 21.9 GiB UE8M0 scales | https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash?expand[]=safetensors&expand[]=usedStorage |
| V9 | Per-GPU weight shares recomputed: TP4 = **118.8 GiB**, TP8 = **59.4 GiB**; TP4 on a 131 GiB H200 fails the 0.90 budget (118.8 > 117.9). The vLLM/SGLang TP4-vs-TP8 disagreement on H200 is real | recomputed; https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/h200.json |
| V10 | Kimi-K3 `vram_minimum_gb: 1680`; min-GPU ladder ⌈1680/(cap×0.9)⌉ = 7 / 10 / 14 / 24 for 288 / 192 / 141 / 80 GB → next power of two 8 / 16 / 16 / 32, matching every published topology | recomputed; https://recipes.vllm.ai/moonshotai/Kimi-K3.json |
| V11 | DS-V4.1 model card: "**890 bytes per token**" global KV, "**8B parameters per token during prefill** and **16B during decode**", CSA2 with Full/Reindex/Reuse modes, Causal Encoder-Decoder (20 + 20), SWA Bounded Replay, FP4 E2M1 main KV with one E4M3 scale per 16 channels | https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/README.md |
| V12 | Kimi-K3 card: **2.8T** total, **104B** active, **MoonViT-V2 401M**, "MXFP4 weights / MXFP8 activations (quantization-aware training)", engines vLLM + SGLang + TokenSpeed. Config: 69 KDA + 24 full-attention layers = 93, 896 experts top-16 + 2 shared, vocab 163,840 ("160K") | https://huggingface.co/moonshotai/Kimi-K3/raw/main/README.md · local `config.json`, byte-identical to upstream |
| V13 | NVFP4 card: **476 GiB → 492 GiB**, **48 safetensors shards**, `nvidia-modelopt v0.47.0rc0`, source revision `dba1be0a…`, runtimes vLLM + SGLang, microarchitecture Blackwell, test hardware **GB300**, `--no-enable-prefix-caching` in NVIDIA's own vLLM command, "DSpark tensors are preserved, but speculative decoding was not exercised", "the vLLM example is text-only". Accuracy table matches to 3 decimals on all 12 cells. `usedStorage` 491.09 GiB corroborates the 492 GiB | https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md |
| V14 | TRT-LLM PyTorch matrix rows for `DeepseekV4ForCausalLM` (disagg **Untested**, spec MTP), `KimiK3ForConditionalGeneration` (spec **DSpark**), `Qwen3_5MoeForCausalLM`; footnotes `[^11]`, `[^15]`, `[^17]` verbatim; **no `DeepseekV41…` row anywhere**. Multimodal row `Qwen3_5ForConditionalGeneration` = chunked prefill *Untested*, KV reuse *No*, EPD *Yes*, modality *L + I + V*; both encoder optimizations listed. Deployment guide: `tokens_per_block` must be 64; `--cuda_architectures 103-real`; `pip install fla-core einops`; matched DEP16=DEP16 disagg only; GB300 validated, GB200 TEP16 end-to-end, B200 functional/CI | https://raw.githubusercontent.com/NVIDIA/TensorRT-LLM/main/docs/source/models/supported-models.md · .../deployment-guide-for-kimi-k3-on-trtllm.md |
| V15 | SGLang `supportedHardware` arrays exact: DS-V4.1 `["h200","b200","b300","gb300","mi350x"]`; Kimi-K3 `["b300","gb300","b200","gb200","h200","h100","mi350x","mi355x","a3"]`; Qwen3.8-27B `["h200","rtx6000","rtx5090","dgx-spark","gb300"]`. SGLang's hardware catalogue: rtx6000 96GB, rtx5090 32GB, b300/gb300/mi350x/mi355x 288GB, b200/gb200 192GB, h200 141GB, h100 80GB | the three cookbook pages on docs.sglang.io |
| V16 | SGLang Qwen3.8-27B measurements verbatim: **94.01–95.00 %** (RTX PRO 6000) and 94.16–95.07 % (DGX Spark) over 16 overlay combinations; **202 cells … 93.18–95.15 %** on the full 1319-question GSM8K; GDN state slot **153.9 MB at fp32 / 78.4 MB at bf16**; **97,280 KV tokens at bf16 against 68,588 at fp32**; "the card has no FP4 tensor cores… Marlin W4A16 weight-only path" (stated for **H200/SM90**); `trtllm_mha` SM100-only; FlashInfer newer than `0.6.15.post1` for `uniform_q_len`; NVIDIA export "identical 21.9 GB on disk". Bonus, consistent with METHODOLOGY §2: the page's own `kv_bytes_per_token` = 16 layers × 4 KV heads × 256 × (K+V) = **32.8 kB at fp8 / 65.5 kB at bf16** = exactly **32 KiB / 64 KiB**, the pinned METHODOLOGY §8 figures (the page's kB are 10³; report GiB/KiB per METHODOLOGY §1). Note the 48 linear-attention layers contribute **0 bytes/token** and are billed instead as the GDN per-slot state below | https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md |
| V17 | vLLM Qwen3.8-27B 2× RTX 5090 @ 262,144 ctx table exact: FP8 377,456 tok / 14.28 GiB / 0.771; NVFP4 (Inferact) 445,875 / 12.02 / 0.897; NVFP4 (unsloth) 920,517 / 10.64 / 0.788 | https://recipes.vllm.ai/Qwen/Qwen3.8-27B |
| V18 | SGLang Kimi-K3 AMD/NVFP4 quotes verbatim: A4W4 "Numerically correct but **slower** than A8W4 (530.8 vs 537.3 tok/s median output on 8×MI35x)"; fused KDA decode boundary "9.20 → 8.38 µs/layer (−8.9 %), with GSM8K 1319 at 0.950"; "The `nvidia/Kimi-K3-NVFP4` checkpoint needs Blackwell… which do not exist for Hopper or AMD" (the NVFP4 option is programmatically disabled unless the target is b200/gb200/b300/gb300) | https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3.md |
| V19 | Marlin-2B: shards **4,999,157,736 + 444,519,488 = 5,443,677,224 B = 5.44 GB**, matching the Hub's 2,721,801,024 BF16 params (≈ 2.72 B); `gated: "auto"`; `modeling_marlin.py` = **23,098 B**; no `requirements.txt` in the file list; `MarlinForConditionalGeneration` absent from vLLM's `registry.py` and `supported_models.md`, from TRT-LLM's matrix, and from the **396-entry** recipe catalogue (count verified) | https://huggingface.co/api/models/NemoStation/Marlin-2B?blobs=true · https://recipes.vllm.ai/models.json |
| V20 | Dynamo README feature matrix exact (KVBM 🚧 SGLang / ✅ TRT-LLM / ✅ vLLM, everything else ✅✅✅); "7x higher throughput per GPU … InferenceX" and "2x faster time to first token … Baseten" verbatim. vLLM disagg doc: "Disaggregated prefill DOES NOT improve throughput" and "Now supports 9 types of connectors" with all nine named. vLLM EP doc: `deepep_v2` "requires NCCL >= 2.30.4"; "When `TP = 1`: Attention weights are **replicated** across all DP ranks". vLLM registry: `DeepseekV41ForCausalLM`, `DSparkV41DraftModel → DSparkDeepseekV4ForCausalLM`, `Qwen3_5MTP`, `Qwen3_5MoeMTP`, `Gemma4MTPModel`, `K3DSparkModel` all present. SGLang's generic speculative-decoding page contains **zero** occurrences of `DSPARK` (open question 17's premise holds). llama.cpp quickstart is `llama serve -hf ggml-org/Qwen3.5-0.8B-GGUF` with no DeepSeek-V4/Kimi-K3/Marlin mention. Ollama `qwen3.8`: 2.2M downloads, "Updated 1 month ago", tags vision/tools/thinking, 27b. MI355X = 288 GB HBM3E / 8 TB/s; RTX PRO 6000 = 96 GB GDDR7 / **1,597 GB/s (Server Edition)** — ⚠️ **amended by the 2026-09-19 sweep:** this row originally read 1,792 GB/s, which is the **Workstation** Edition figure and is not a number the NVIDIA model card cited beside it contains. The Server Edition board is the one every recipe in this document targets (METHODOLOGY §8, [gpus/rtx6000-pro.md](../gpus/rtx6000-pro.md)) | github.com/ai-dynamo/dynamo README · vLLM docs · https://docs.sglang.io/docs/advanced_features/speculative_decoding.md · https://ollama.com/library/qwen3.8 · vendor product pages |

### UNVERIFIABLE (newly flagged ⚠️ in-place)

| # | Item | Why |
|---|---|---|
| U1 | TRT-LLM Kimi-K3 per-rank memory: **114 + 90 = 204, not the stated 210**; TEP8's 213 vs a 181 GB expert share does not decompose either | NVIDIA's footnote `[^15]` and the deployment guide both assert the same non-additive figures. They are stated requirements, not derivable sums. Only a real load-time peak measurement can settle them. Open question 19 |
| U2 | Kimi-K3 `vram_minimum_gb: 1680` against **1,561 GB** of measured weights = a 1.076× headroom factor, where the DS-V4.1 variant schema on the same site uses 1.2× | The recipe site publishes no variant schema at any reachable URL (`/hardware.json`, `/schema.json` both 404). Open question 18 |
| U3 | NVFP4 W4A4 on **RTX PRO 6000** specifically | The `FlashInferCutlassNvFp4LinearKernel` "cutlass path, not an emulation fallback" statement is made in the vLLM recipe's **2× RTX 5090** section. Same SM120, different board; no PRO 6000 NVFP4 GEMM measurement is published. Open question 20 |
| U4 | TokenSpeed version, licence and release cadence (pre-existing open question 4) | Re-checked: the recipes page contains **zero** occurrences of "version" or "License". The engine is now known to matter more than the document assumed (it has a V4.1-Flash recipe with per-commit CI), which raises the cost of not being able to pin it |

---

## Sweep log (2026-09-19)

Systemic correction pass against the amended [`research/METHODOLOGY.md`](../METHODOLOGY.md)
(§1 units and bytes-per-param, §2 state-slot multiplier `S`, §8 pinned GPU/model inputs). The
existing Verification log above was **not** undone — every C-row and V-row it records still
stands; this pass adds to it. Recomputed tables were redone with `python3` and the values
pasted back.

| Section | Old → new | Reason | Source |
|---|---|---|---|
| §0, bullet 2 | "≥ 8 Blackwell-288GB GPUs, 16 Blackwell-192GB" → "≥ 8 large-HBM Blackwell (B300 268 GB, GB300 288 GB, MI355X 288 GB), 16 B200 (180 GB) or GB200 NVL4 (192 GB)" | METHODOLOGY §8 capacities are **as deployed**; "Blackwell-288GB" silently merged HGX B300 with GB300 NVL, and "Blackwell-192GB" merged B200 with the GB200 tray | METHODOLOGY §8 |
| §3.1, B200 row | added the endpoint's verbatim `hardware_profile.description` "NVIDIA B200 SXM **180 GB** HBM3e" | Citation integrity: the 180 GB figure was inferred from `1440/8`; the same JSON states it in words, which is a stronger citation | [hw/b200.json](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/b200.json) (re-fetched, HTTP 200) |
| §3.1, B300 row | title "B300" → "B300 (HGX / DGX / AWS p6-b300)"; added verbatim "NVIDIA B300 SXM **268 GB** HBM3e · 8-GPU HGX B300 node"; added "2,144 GB per 8-GPU node" and "never merge with GB300 NVL" | METHODOLOGY §8 pins 268 GB / 2,144 GB per node and forbids merging the two B300 families | [hw/b300.json](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/b300.json) (re-fetched, HTTP 200) |
| §3.1, GB300 row | added "the NVL72 per-GPU figure (≈ 279 GB usable), **not** the 268 GB HGX B300 figure" | Same anti-merge rule | METHODOLOGY §8 |
| §3.1, RTX PRO 6000 row | "RTX PRO 6000 Blackwell \| 96 GB" → "RTX PRO 6000 Blackwell **Server Edition** \| 96 GB GDDR7, **1,597 GB/s**" + Workstation-Edition caveat | METHODOLOGY §8: 1,597 GB/s is the Server Edition; 1,792 is the Workstation Edition | METHODOLOGY §8, [gpus/rtx6000-pro.md](../gpus/rtx6000-pro.md) |
| §3.1, closing note | added the six re-fetched `hardware_profile.description` strings and "never quote 288 GB for an HGX/DGX B300" | Re-verification of the single most reused fact in the document | the six `hw/*.json` endpoints, all HTTP 200 on 2026-09-19 |
| §3.5 heading | "B200 / GB200 NVL4 (SM100, 192 GB)" → "B200 (SM100, 180 GB) / GB200 NVL4 (SM100, 192 GB/GPU)" | One heading was asserting 192 GB for B200 | METHODOLOGY §8 |
| §3.6 heading | "B300 (SM103, 288 GB)" → "B300 (SM103, 268 GB as deployed)" | METHODOLOGY §8 | METHODOLOGY §8 |
| §3.6, DS-V4.1 cell | added "the Engram offload is load-bearing, not an optimization: TP2 puts 237.6 GiB against a 224.6 GiB budget" | Follows from the §6.1 recomputation below | recomputed |
| §3.6, Kimi-K3 TRT-LLM cell | "B300 at 288 GB qualifies" → "B300 at **268 GB as deployed** … clears 210 GB/rank by 58 GB and 213 GB by 55 GB" | METHODOLOGY §8; the verdict survives but the input was wrong | METHODOLOGY §8 + TRT-LLM K3 deployment guide |
| §3.7 heading | "(SM103, 288 GB × 4)" → "(SM103, 288 GB/GPU × 4; NVL72 per-GPU figure, ≈ 279 usable)" | METHODOLOGY §8 distinguishes nominal from usable on GB300 | METHODOLOGY §8 |
| §3.8 heading | "RTX PRO 6000 Blackwell (SM120, 96 GB)" → "… Server Edition (SM120, 96 GB GDDR7, 1,597 GB/s)" | Server vs Workstation Edition | METHODOLOGY §8 |
| §6.1, correction box | added a paragraph labelling **890 B/token as the FP4-KV (Blackwell-kernel) figure** (720 main + 170 indexer), with a pointer to `models/deepseek41f/architecture.md` §5 for other KV dtypes | METHODOLOGY §5: 890 B/token is not dtype-agnostic and must not be applied flat on non-FP4-KV hardware | recipe guide text re-fetched: "trained to be stored in **FP4**, and DeepSeek puts the resulting global KV at 890 bytes per token" — [DeepSeek-V4.1-Flash.json](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash.json) |
| §6.1, checkpoint total | "475.3 GiB = 510 GB" → "**475.26 GiB = 510.31 GB**" + explicit reconciliation to METHODOLOGY §8's 510.29 GB (0.004 %) | METHODOLOGY §1 units rule and §8 reconciliation requirement | `usedStorage` 510,310,271,922 B — [HF API](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash?expand[]=usedStorage) |
| §6.1, `vram_minimum_gb` check | "475.3 × 1.2 = 570 GiB = 612 GB" → "475.26 × 1.2 = 570.3 GiB = **612.4 GB**" | Recomputed from the corrected total | recomputed |
| §6.1, per-GPU fit table | **fully recomputed**; capacity column now carries both units. TP2/B300 **268 GiB → 268 GB = 249.59 GiB**, verdict **"yes, barely" → "no" (13.0 GiB over)**; TP4 GB300/MI355X 268 GiB → 288 GB = 268.22 GiB; TP4 GB200 "179 GiB" → 192 GB = 178.81 GiB; new **TP4 B200** row (180 GB = 167.64 GiB, fits); H200 118.0 → 118.19 GiB budget; H100 75 → 74.51 GiB; added an explicit `0.90 × capacity` column and signed headroom | METHODOLOGY §1: memory arithmetic in bytes, reported in GiB. The old table compared GiB-of-weights against GB-of-capacity on two rows, and the B300 row's verdict flipped once the units matched | recomputed with `python3` from the `usedStorage` byte count and the §3.1 as-deployed capacities |
| §6.1, per-GPU fit table | added a one-line scope pointer: full per-(model, GPU) fit / throughput / cost tables live in `research/models/deepseek41f/<gpu>.md`, next phase | Scope rule — this is a cross-cutting engine document; the weights-only check is kept only because the recipes' TP choices turn on it | task scope rule 15 |
| §6.2 | added measured sizes (**491.09 GiB = 527.31 GB** NVFP4 vs 475.26 GiB = 510.31 GB base, **+17 GB**) reconciling to METHODOLOGY §8's 527.27 / 510.29 GB | METHODOLOGY §1 / §8 | `usedStorage` 527,309,220,165 B — [HF API](https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4?expand[]=usedStorage) |
| §6.2 | added two numbered points: (1) **the base checkpoint's routed experts are MXFP4** (E2M1 + E8M0/32, 0.53125 B/param), **not NVFP4** (E2M1 + FP8 E4M3/16, 0.5625 B/param, +12.5 %) — which is *why* the NVFP4 build is larger; (2) **NVIDIA publishes no speedup** for the NVFP4 build over the base | METHODOLOGY §1 bytes-per-param table; the "NVFP4 = the fast FP4" shorthand was left implicit and is wrong for this checkpoint | card re-read 2026-09-19: "converts the ordinary routed MoE experts **from source MXFP4** to NVFP4"; accuracy table present, **zero** throughput/latency/speedup figures — [NVFP4 card](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md) |
| §6.3, min-GPU ladder | **recomputed and de-merged**: "B300 / GB300 (288 GB) → 7" split into B300 268 GB → ⌈1680/241.2⌉ = 7 and GB300 288 GB → ⌈1680/259.2⌉ = 7; "B200 / GB200 (192 GB) → 10" split into **B200 180 GB → 11** and GB200 NVL4 192 GB → 10; capacity column and the arithmetic added to every row | METHODOLOGY §8 forbids merging B300 with GB300 and B200 with GB200. No landing point moves, but B200's raw requirement was understated as 10 | recomputed with `python3`; `vram_minimum_gb: 1680` re-confirmed in [Kimi-K3.json](https://recipes.vllm.ai/moonshotai/Kimi-K3.json) |
| §6.3, following sentence | "all five GPUs" → "all seven GPUs, and it survives the switch to as-deployed capacities" | Row count changed with the de-merge | — |
| §6.3, KDA-state bullet | added the **state-slot multiplier `S`**: SGLang baseline **S = 5** → 428.6 MiB × 5 = **2.25 GB per request**, rising to S = 8 under DSPARK; "size concurrency against `S × 428.6 MiB`, not one slot" | METHODOLOGY §2/§5: per-sequence recurrent state is multiplied by the engine's slot count. The document described the DSPARK case only and never named the baseline `S` | METHODOLOGY §2 and §8, [models/kimik3/architecture.md](../models/kimik3/architecture.md) §5 |
| §6.4, parameter count | "27,781,427,952 parameters, all BF16 → 51.75 GiB" → "… (27.78 B) … → **51.75 GiB = 55.56 GB**, matching METHODOLOGY §8's pinned 55.56 GB exactly" | METHODOLOGY §1 units convention + §8 reconciliation; re-verified in this sweep | [HF safetensors census](https://huggingface.co/api/models/Qwen/Qwen3.8-27B?expand[]=safetensors&expand[]=usedStorage), re-fetched 2026-09-19 |
| §8, DS-V4.1 matrix, B300 row | Min GPUs "2–4" → "2 **only with Engram CPU offload** (§6.1); 4 otherwise" | The TP2 row does not fit on weights alone once units are matched | recomputed |
| Verification log, V20 | "RTX PRO 6000 = 96 GB GDDR7 / **1792 GB/s**" → "**1,597 GB/s (Server Edition)**", with an explicit amendment note | **Citation-integrity failure found:** 1,792 GB/s is the Workstation Edition and is not contained in the vendor page cited alongside it. The Server Edition is the board every recipe here targets | METHODOLOGY §8, [gpus/rtx6000-pro.md](../gpus/rtx6000-pro.md) |
| Verification log, V16 | "32.8 KB at fp8 / 65.5 KB at bf16" → "32.8 kB / 65.5 kB = exactly **32 KiB / 64 KiB**", plus a note that the 48 linear-attention layers contribute **0 bytes/token** and are billed as GDN per-slot state | METHODOLOGY §1 (report KiB/GiB, not kB/GB) and §2 (linear-attention layers are 0/token) | METHODOLOGY §1, §2, §8 |
| §3.2, after "do not size a deployment on it" *(2026-09-19 amendment)* | *(added)* "A community SM80 backport exists and has been measured on 8×A800 ([models/deepseek41f/a100.md](../models/deepseek41f/a100.md)); it does not change this scoping — do not size a production deployment on it." | Cross-reference for gap X4: the pair doc measures DeepSeek-V4.1-Flash on SM80 via a community `vllm-backport` fork + 24-file patch set on 8× A800. The §3.2 verdict is unchanged; `matrix/pairs.json` was demoted to `supported_now: false` so the matrices stop rendering it as runnable | [models/deepseek41f/a100.md](../models/deepseek41f/a100.md), [matrix/pairs.json](../matrix/pairs.json), METHODOLOGY §7 |

### Checked and left unchanged

- **Engine versions and dates (item 12).** Re-verified against PyPI on 2026-09-19: vLLM
  **0.29.0 @ 2026-09-09** (and the §2.1 cadence table 0.25.0 07-11 / 0.26.0 07-25 / 0.27.0
  08-10 / 0.27.1 08-11 / 0.28.0 08-26 — exact to the day, and **no 0.30.0 exists**); SGLang
  **0.5.20 @ 2026-09-18** (0.5.17 08-08 / 0.5.18 08-21 / 0.5.19 09-04 — exact); TensorRT-LLM
  **1.2.1** stable with **1.3.0rc27 @ 2026-09-17**; `ai-dynamo` **1.5.0 @ 2026-09-19**. **No
  off-by-one-release date pattern in this document.** (`api.github.com` returned HTTP 403
  rate-limited from this host; the PyPI JSON endpoints, which are what the document already
  cites, were used instead.)
- **Prices.** This document contains no GPU-hour or per-token price, and no "not
  retrievable" / "WebSearch unavailable" / "could not be fetched" price statement. Nothing to
  reconcile against [cloud-pricing.md](cloud-pricing.md).
- **Dense vs. sparse FLOPS.** No TFLOPS figure appears in this document by design (§0 scope
  note sends them to the per-GPU docs), so there is nothing to de-sparsify.
- **TokenSpeed / DeepSeek-V4.1-Flash (item 10).** All 21 TokenSpeed mentions audited; the
  fact-checker's C1 correction is intact and **no contrary statement survives** in §0, §2.5,
  §6.1, §8 or §9.
- **DSpark acceptance length 3.51.** Not present in this document.
- **B200-vs-H200 gap on DeepSeek-R1 FP8.** Not present in this document.
- **Feasibility rule (METHODOLOGY §3).** This document contains no throughput or cost table
  with batch/concurrency rows, so no row converts to `infeasible (KV)`.

### Citation spot-check (item 13) — the five most load-bearing citations

Each URL was fetched on 2026-09-19 and read; the claim was confirmed to be **in** the page.

| # | Citation | Claim it carries | Result |
|---|---|---|---|
| 1 | [recipes.vllm.ai/…/DeepSeek-V4.1-Flash.json](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash.json) | `min_vllm_version: 0.30.0`, `nightly_required: true`, `default_hardware: gb200`, `vram_minimum_gb: 614`, all seven hardware entries `"verified"`, the 5-row checkpoint component table, "890 bytes per token" | **CONFIRMED** verbatim, all of it |
| 2 | [recipes.vllm.ai/…/hw/{b200,b300,gb200,gb300,h200,mi355x}.json](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash/hw/b300.json) | `vram_gb` 1440 / 2144 / 768 / 1152 / 1128 / 2304 **and** the per-GPU capacity spelled out in `hardware_profile.description` | **CONFIRMED**, and it strengthens §3.1: the descriptions say "180 GB", "268 GB", "192 GB/GPU", "288 GB/GPU" in words |
| 3 | [lightseek.org/tokenspeed/recipes/models](https://lightseek.org/tokenspeed/recipes/models) | The DeepSeek V4.1-Flash section, the FlatKV four-group KV cache, the `tokenspeed serve` command, the GB300 Slurm 1P1D CI job, "an eight-token verify window uses seven DSpark draft queries", `in_patch_limit=65536` | **CONFIRMED** verbatim. Also re-confirms U4: **zero** occurrences of "version" or "License" on the page, and zero of "Marlin-2B" |
| 4 | [docs.sglang.io/cookbook/…/Kimi-K3.md](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3.md) + [/DeepSeek-V4_1.md](https://docs.sglang.io/cookbook/autoregressive/DeepSeek/DeepSeek-V4_1.md) + [/Qwen3.8-27B.md](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md) | The quoted launch cells | **CONFIRMED** — every quoted flag list matches the page's own `flags:` array exactly (see recipe spot-check below) |
| 5 | [huggingface.co/api/models/Qwen/Qwen3.8-27B?expand[]=safetensors](https://huggingface.co/api/models/Qwen/Qwen3.8-27B?expand[]=safetensors&expand[]=usedStorage) | 27,781,427,952 BF16 params, `usedStorage` 55,623,336,488 B | **CONFIRMED** to the byte |

**One citation failed** and was fixed: the RTX PRO 6000 **1,792 GB/s** in verification-log row
V20, attributed to "vendor product pages" alongside the NVIDIA model card. That is the
Workstation Edition figure; the card does not carry it for the Server Edition board. Corrected
to **1,597 GB/s** above.

### Launch-recipe verbatim spot-check (all five models)

Every recipe URL still resolves (HTTP 200) and every quoted flag string is verbatim:

| Recipe | URL status | Verbatim? |
|---|---|---|
| vLLM DS-V4.1-Flash, GB200 recommended (TP4) | 200 | ✔ matches `recommended_command.command` |
| vLLM DS-V4.1-Flash, H100 (TP8 + Engram offload) | 200 | ✔ matches `hw/h100.json` `command` flag-for-flag |
| vLLM Kimi-K3, B300 1×8 TP8 | 200 | ✔ matches `hw/b300.json`, including `--attention-config '{"use_prefill_query_quantization":true,"mla_prefill_backend":"TOKENSPEED_MLA"}'` and `--prefix-match-unit 128` |
| vLLM Kimi-K3, H100 4×8 TP32 | 200 | ✔ matches `hw/h100.json` `head_command`, including `--max-num-seqs 5 --max-model-len 32768` |
| vLLM Qwen3.8-27B, RTX PRO 6000 TP1 | 200 | ✔ matches `hw/rtx_pro_6000.json`, `--max-num-seqs 8`, `--tool-call-parser qwen3_xml` |
| SGLang DS-V4.1-Flash, GB300 Low-Latency (TP4/EP4, DSPARK block 5) | 200 | ✔ matches the page's `flags:` array |
| SGLang DS-V4.1-Flash, H200 Low-Latency (TP8/EP8, `dsv4` + `flashinfer_mxfp4` + SWA bounded replay) | 200 | ✔ matches |
| SGLang Kimi-K3, B300 1×8 Balanced (TP8 + DCP8, `--mem-fraction-static 0.85`) | 200 | ✔ matches |
| SGLang Qwen3.8-27B, RTX PRO 6000 | 200 | ✔ matches (`fp8_e4m3`, 0.85, `flashinfer`, 2048, `qwen3`/`qwen3_coder`) |
| NVIDIA DS-V4.1-Flash-NVFP4, 4× GB300 (SGLang and vLLM) | 200 | ✔ both commands verbatim from the card |
| TokenSpeed DS-V4.1-Flash | 200 | ✔ the quoted command is a verbatim **subset** of the page's block, which additionally carries `--served-model-name deepseek-v41-flash --trust-remote-code --max-total-tokens 262144 --max-num-seqs 32 --chunked-prefill-size 8192 --max-cudagraph-capture-size 32 --host 0.0.0.0 --port 8000` |
