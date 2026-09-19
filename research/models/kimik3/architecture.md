# moonshotai/Kimi-K3 — architecture and inference profile

> Research date **2026-09-19**. Formulas, legend and rules per
> [`research/METHODOLOGY.md`](../../METHODOLOGY.md). Dense vs sparse TFLOPS are
> separated; SXM vs PCIe are separated; marketing claims are labelled.
>
> **Collection-method note for this document.** The session-wide `WebSearch`
> budget (200 calls) was exhausted by sibling agents before this agent issued a
> single query, so the ≥12 exploratory searches the task template asks for could
> **not** be run. Everything below is instead sourced from *direct fetches of
> primary documents* (HF API, HF raw files, HF safetensors headers over HTTP
> range requests, vLLM recipe JSON API, SGLang cookbook, Moonshot blog and
> pricing pages). Consequence: **§10 may be missing third-party benchmarks
> (MLPerf, InferenceMAX, SemiAnalysis) that exist but were not discoverable
> without search** — see §12.
>
> **Adversarial fact-check pass, 2026-09-19.** An independent agent re-verified
> the 20 most load-bearing claims against freshly-fetched primary sources and
> recomputed every derivation from `config.json` + METHODOLOGY. Results,
> including 9 corrections (one of them a wrong *support* claim — TensorRT-LLM
> **does** support Kimi-K3), are in
> **[Verification log (2026-09-19)](#verification-log-2026-09-19)** at the end of
> this document. Corrections are applied inline and marked.
>
> **Correction to the task brief.** The brief asked to check "the README claim
> that it fits one 8×B300 node (2304 GB)". The model card makes **no such
> claim** — [README §5 Deployment](https://huggingface.co/moonshotai/Kimi-K3/raw/main/README.md)
> only lists vLLM / SGLang / TokenSpeed. The single-node-B300 claim comes from
> the engines, and the node figure is **2144 GB**, not 2304 GB. See §5.4.

---

## 1. Identity

| Field | Value | Source |
|---|---|---|
| HF repo | `moonshotai/Kimi-K3` | [HF API](https://huggingface.co/api/models/moonshotai/Kimi-K3) |
| Repo SHA (as fetched) | `f831ab66814297da540d832a5235f8e904f29d06` | [HF API](https://huggingface.co/api/models/moonshotai/Kimi-K3) |
| `lastModified` | `2026-09-02T02:22:41.000Z` | [HF API](https://huggingface.co/api/models/moonshotai/Kimi-K3) |
| `createdAt` / release | HF API returns no `createdAt` for this repo. vLLM recipe `date_added` = **2026-07-27**; SGLang cookbook says "full model weights are scheduled to release by **July 27, 2026**"; RedHatAI FP8 variant `Release Date: 2026-07-27` | [vLLM recipe JSON](https://recipes.vllm.ai/moonshotai/Kimi-K3.json), [SGLang cookbook](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3), [RedHatAI card](https://huggingface.co/RedHatAI/Kimi-K3-FP8-BLOCK) |
| License | `other` / `license_name: kimi-k3` — the **Kimi K3 License** | [LICENSE](https://huggingface.co/moonshotai/Kimi-K3/raw/main/LICENSE) |
| Gated? | **No** (`"gated": false`, `"private": false`, `"disabled": false`) | [HF API](https://huggingface.co/api/models/moonshotai/Kimi-K3) |
| `pipeline_tag` | `image-text-to-text` | [HF API](https://huggingface.co/api/models/moonshotai/Kimi-K3) |
| Architecture class | `KimiK3ForConditionalGeneration` (custom code, `trust_remote_code` required) | [config.json](https://huggingface.co/moonshotai/Kimi-K3/raw/main/config.json) |
| Downloads / likes (2026-09-19) | 2,197,706 / 11,423 | [HF API](https://huggingface.co/api/models/moonshotai/Kimi-K3) |

### 1.1 License — the commercial conditions that matter for serving

The Kimi K3 License is permissive (MIT-shaped) with two riders
([LICENSE](https://huggingface.co/moonshotai/Kimi-K3/raw/main/LICENSE)):

- **§2 Model-as-a-Service threshold.** If you operate an MaaS business (giving a
  third party inference/fine-tuning access "in a manner that allows such third
  party to exercise meaningful control over the inputs, parameters, or training
  data") and aggregate revenue **exceeds US$20 M over any consecutive 12
  months**, you "must enter into a separate agreement with Moonshot AI before
  using the Software … for any commercial purpose."
- **§3 Attribution threshold.** Products with **>100 M MAU** or **>US$20 M
  monthly revenue** must display "Kimi K3" prominently in the UI.
- **§4** exempts purely internal use, and end-user products where model
  capability is embedded in a feature rather than exposed as an API.

For a self-hosted internal deployment neither rider binds. For a token-resale
business, §2 is a hard gate.

### 1.2 Files and on-disk size (from the HF API tree, `recursive=true`)

Enumerated over the paginated tree API; 121 entries, 120 files.

| Group | Files | Bytes | GB (10⁹) | GiB (2³⁰) |
|---|---:|---:|---:|---:|
| `*.safetensors` (weights) | 96 | 1,560,936,091,448 | 1560.94 | 1453.74 |
| `model.safetensors.index.json` | 1 | 59,764,096 | 0.0598 | 0.0557 |
| `tiktoken.model` | 1 | 2,795,286 | 0.0028 | 0.0026 |
| `*.py` (modeling/config/processor/tokenizer) | 8 | 186,932 | — | — |
| `README.md` | 1 | 45,261 | — | — |
| `config.json` | 1 | 7,006 | — | — |
| other (LICENSE, yaml evals, png, gitattributes, 4 small json) | 12 | ~97,000 | — | — |
| **Repo total** | **120** | **1,560,998,988,078** | **1561.00** | **1453.79** |

Shard sizes: min 92,289,328 B, max/median 16,990,916,912 B (≈17.0 GB), first
shard 2,341,216,112 B, last 802,448,352 B.
Source: [HF tree API](https://huggingface.co/api/models/moonshotai/Kimi-K3/tree/main?recursive=true).

**Practical download note:** 1.56 TB at 10 Gbit/s ≈ 21 min of pure wire time;
the vLLM Blackwell recipe therefore pins `--load-format fastsafetensors` and
raises `VLLM_ENGINE_READY_TIMEOUT_S=3600`
([vLLM recipe](https://recipes.vllm.ai/moonshotai/Kimi-K3.json)).

### 1.3 Checkpoint dtype and quantisation scheme

`text_config.quantization_config`
([config.json](https://huggingface.co/moonshotai/Kimi-K3/raw/main/config.json)):

```json
"format": "mxfp4-pack-quantized",
"quant_method": "compressed-tensors",
"quantization_status": "compressed",
"config_groups": {"group_0": {"format": "mxfp4-pack-quantized",
  "targets": ["Linear"],
  "weights": {"num_bits": 4, "group_size": 32, "strategy": "group",
              "symmetric": true, "type": "float", "observer": "minmax",
              "scale_dtype": "torch.uint8", "dynamic": false}}},
"ignore": ["re:.*self_attn.*", "re:.*shared_experts.*",
           "re:.*mlp\\.(gate|up|gate_up|down)_proj.*", "re:.*lm_head.*",
           "re:.*vision_tower.*", "re:.*mm_projector.*"],
"kv_cache_scheme": null
```

- **MXFP4, group size 32, symmetric, E8M0 (`uint8`) block scales.** This is the
  OCP MX FP4 format: one shared 8-bit power-of-two exponent per 32 elements.
- `kv_cache_scheme: null` — **the checkpoint ships no FP8 KV scaling factors.**
  TokenSpeed's docs state this explicitly
  ([TokenSpeed recipes](https://lightseek.org/tokenspeed/recipes/models)); FP8 KV
  is therefore a runtime-calibrated / dynamic-scale choice per engine, not a
  checkpoint property.
- Top-level `dtype: "bfloat16"` — everything in the `ignore` list stays BF16.
- Model card: "MXFP4 weights / MXFP8 activations (**quantization-aware
  training**)", applied "from the SFT stage onward"
  ([README §4](https://huggingface.co/moonshotai/Kimi-K3/raw/main/README.md)).
  This is materially different from post-training quantisation: the weights were
  *trained* at MXFP4, so the BF16 "original" this was quantised from does not
  exist as a released artefact. **There is no BF16 Kimi-K3 checkpoint.**

### 1.4 Tokenizer and chat template

| Field | Value |
|---|---|
| Class | `tokenization_kimi.TikTokenTokenizer` (custom, `auto_map`) |
| Vocab size | `163840` (`text_config.vocab_size`); model card says "160K" |
| Merges file | `tiktoken.model`, 2,795,286 B |
| BOS / EOS / PAD / UNK | `[BOS]` 163584 / `[EOS]` 163585 / `[PAD]` 163839 / `[UNK]` 163838 |
| **Generation EOS** | `163586` = `<|end_of_msg|>` — **not** `[EOS]`. Both `config.json` `eos_token_id` and `generation_config.json` use 163586. |
| `generation_config.json` | `{"max_length": 1048576, "eos_token_id": 163586}` — that is the entire file |
| `model_max_length` | `1e30` sentinel (unset) |

Sources: [tokenizer_config.json](https://huggingface.co/moonshotai/Kimi-K3/raw/main/tokenizer_config.json),
[generation_config.json](https://huggingface.co/moonshotai/Kimi-K3/raw/main/generation_config.json).

**There is no `chat_template` field in `tokenizer_config.json`** (verified:
zero occurrences). Chat rendering lives in
[`encoding_k3.py`](https://huggingface.co/moonshotai/Kimi-K3/raw/main/encoding_k3.py),
whose docstring reads: *"Kimi K3 XTML encoding helpers. This module keeps chat
rendering in Python."* The format is a tag language ("XTML") built on four
structural specials:

| Token | ID | Role |
|---|---:|---|
| `<\|open\|>` | 163587 | open tag |
| `<\|close\|>` | 163588 | close tag |
| `<\|sep\|>` | 163589 | attribute/segment separator |
| `<\|end_of_msg\|>` | 163586 | end of message (also the EOS used for generation) |
| `[start_header_id]` / `[end_header_id]` | 163590 / 163591 | role header |
| `[EOT]` | 163593 | end of turn |
| `<\|media_begin\|>` `<\|media_content\|>` `<\|media_end\|>` `<\|media_pad\|>` | 163602–163605 | image/media framing; `media_placeholder_token_id = 163605` |
| `<osagent_mode>` | 163649 | computer-use / OS-agent mode |

Chat-template features that have inference consequences:

1. **Thinking is always on and must be echoed back.** "Kimi K3 always has
   thinking enabled, and will return `reasoning_content`… For multi-turn
   conversations and tool calls, Kimi K3 requires the complete assistant message
   returned by the API to be passed back to `messages` as-is — including
   `reasoning_content` and `tool_calls`, not just `content`"
   ([README §6](https://huggingface.co/moonshotai/Kimi-K3/raw/main/README.md)).
   This is *preserved-thinking-history* mode: **reasoning tokens accumulate in
   the prompt across turns**, so effective input length per agentic turn grows
   much faster than for a model that discards thinking. Budget prefill
   accordingly.
2. **`reasoning_effort` ∈ `{low, high, max}`, default `max`** — a top-level
   request field, not a sampling parameter
   ([README §6](https://huggingface.co/moonshotai/Kimi-K3/raw/main/README.md)).
   Output-token volume (and therefore cost) is dominated by this knob.
3. **Tool-call arguments are canonicalised.** `encoding_k3.py` exports
   `normalize_tool_arguments`, `deep_sort_dict` and `normalize_message` — tool
   JSON is deep-sorted before rendering. Relevant to **prefix caching**: two
   semantically identical tool definitions that differ in key order will
   normalise to the same prefix, so cache hits survive client-side dict
   reordering.
4. **Known defect:** "K3 occasionally emit a tool-call format its own parser
   doesn't expect. Suggest to run do schema validation and retry"
   ([vLLM recipe notes](https://recipes.vllm.ai/moonshotai/Kimi-K3)).
5. Recommended sampling (fixed by the model, informational):
   `temperature=1.0, top_p=0.95, presence_penalty=0, frequency_penalty=0`
   ([SGLang cookbook](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3)).
   The model card's eval footnote uses `top_p=0.95` for single-step tasks and
   `top_p=1.0` for agentic tasks.

---

## 2. Architecture from `config.json`

Local copy: [`research/models/kimik3/config.json`](./config.json); upstream
[config.json](https://huggingface.co/moonshotai/Kimi-K3/raw/main/config.json).

### 2.1 Top level

| Field | Value | Meaning |
|---|---|---|
| `model_type` | `kimi_k3` | wrapper: vision tower + `kimi_linear` LM |
| `architectures` | `KimiK3ForConditionalGeneration` | |
| `dtype` | `bfloat16` | dtype of everything not MXFP4-packed |
| `image_placeholder` | `<\|kimi_image_placeholder\|>` | |
| `media_placeholder_token_id` | `163605` | `<\|media_pad\|>` |
| `tie_word_embeddings` | `false` | embed and lm_head are separate 1.17 B tensors each |
| `text_config.model_type` | `kimi_linear` | the LM backbone type |
| `text_config.architectures` | `KimiLinearForCausalLM` | |

### 2.2 Language backbone (`text_config`)

| Field | Value | Notes |
|---|---|---|
| `num_hidden_layers` | 93 | |
| `hidden_size` | 7168 | |
| `intermediate_size` | 33792 | **dense** MLP width — used only by layer 0 |
| `first_k_dense_replace` | 1 | layer 0 is a dense MLP; layers 1–92 are MoE |
| `moe_layer_freq` | 1 | every non-dense layer is MoE → 92 MoE layers |
| `num_attention_heads` / `num_key_value_heads` | 96 / 96 | |
| `hidden_act` | `situ` | **SiTU** = Sigmoid Tanh Unit, gated (SiTU-GLU) |
| `activation_situ_beta` / `activation_situ_linear_beta` | 4.0 / 25.0 | SiTU shape params |
| `rms_norm_eps` | 1e-05 | |
| `max_position_embeddings` | **1,048,576** | 1 M |
| `vocab_size` | 163840 | |
| `num_nextn_predict_layers` | **0** | **no MTP head in the checkpoint** — see §7 |
| `attn_res_block_size` | 12 | Attention Residuals block granularity |
| `transformers_version` | 4.56.2 | |

### 2.3 Attention: 69 KDA + 24 Gated-MLA, interleaved 3:1

`linear_attn_config`:

```json
"full_attn_layers": [4,8,12,16,20,24,28,32,36,40,44,48,52,56,60,64,68,72,76,80,84,88,92,93],
"kda_layers": [1,2,3,5,6,7,9,10,11, …, 89,90,91],
"num_heads": 96, "head_dim": 128,
"short_conv_kernel_size": 4, "use_full_rank_gate": true, "gate_lower_bound": -5.0
```

**These indices are 1-based.** `configuration_kimi_k3.py` resolves them as
`(layer_idx + 1) in kda_layers`
([source](https://huggingface.co/moonshotai/Kimi-K3/raw/main/configuration_kimi_k3.py)),
so config layer 1 = checkpoint `layers.0`. Consequences:

- Checkpoint `layers.0` = **dense MLP + KDA attention**.
- Full attention lands on checkpoint layers 3, 7, 11, …, 87, 91, **92** — i.e.
  every 4th layer, plus the **last two layers are both full attention**
  (config 92 and 93 are adjacent). The final layer sees global attention.
- Counts verified against the checkpoint: 69 tensors named `self_attn.q_proj`
  (KDA) and 24 named `self_attn.q_a_proj` (MLA). ✔

**KDA layers (69).** Kimi Delta Attention — a gated delta-rule linear attention.
Per layer, from the safetensors headers:

| Tensor | Shape | dtype | Role |
|---|---|---|---|
| `q_proj` / `k_proj` / `v_proj` | (12288, 7168) | BF16 | 96 heads × 128 |
| `q_conv1d` / `k_conv1d` / `v_conv1d` | (12288, 1, 4) | **F32** | causal short conv, kernel 4 |
| `f_a_proj` / `f_b_proj` | (128, 7168) / (12288, 128) | BF16 | rank-128 forget-gate factorisation |
| `b_proj` | (96, 7168) | BF16 | per-head β (delta-rule write strength) |
| `A_log` | (128,) | F32 | decay parameterisation |
| `dt_bias` | (12288,) | F32 | per-channel Δ bias |
| `o_norm` | (128,) | F32 | gated output RMSNorm |

Kernels come from **`flash-linear-attention` (`fla`)**:
`from fla.ops.kda import chunk_kda, fused_recurrent_kda`, plus
`fla.modules.ShortConvolution` and `FusedRMSNormGated`
([modeling_kimi_linear.py](https://huggingface.co/moonshotai/Kimi-K3/raw/main/modeling_kimi_linear.py)).
`chunk_kda` is the chunked (prefill) form, `fused_recurrent_kda` the decode form.
TokenSpeed makes this a hard dependency: "Requires `pip install
flash-linear-attention` for KDA layers on NVIDIA"
([TokenSpeed](https://lightseek.org/tokenspeed/recipes/models)).

**MLA layers (24) — NoPE, output-gated.** `kv_lora_rank: 512`,
`q_lora_rank: 1536`, `qk_nope_head_dim: 128`, `qk_rope_head_dim: 64`,
`v_head_dim: 128`, `mla_use_nope: true`, `mla_use_output_gate: true`.

| Tensor | Shape | Check |
|---|---|---|
| `q_a_proj` | (1536, 7168) | → q_lora_rank |
| `q_b_proj` | (18432, 1536) | 18432 = 96 × (128 nope + 64 "rope") |
| `kv_a_proj_with_mqa` | (576, 7168) | 576 = 512 latent + 64 rope-slot |
| `kv_b_proj` | (24576, 512) | 24576 = 96 × (128 nope + 128 v) |

**`mla_use_nope: true` is load-bearing.** `modeling_kimi_linear.py` contains
`assert self.use_nope` followed by `self.rotary_emb = None` — **no rotary
embedding is ever applied**. The 64 `qk_rope_head_dim` channels still exist and
are still cached (they are a shared MQA-style key broadcast across heads:
`k_rot.expand(...)`), but carry no positional rotation. All positional
information comes from the 69 KDA layers' causal convolution + recurrence.

Downstream consequences worth stating plainly:

- **No RoPE scaling, no YaRN, no `rope_theta` on the main model.** The 1 M
  context is native, not extended. (The *DSpark draft* model does use YaRN-16 —
  §7.)
- Long-context quality is a property of KDA, not of a positional-encoding hack.
- Nothing in the serving stack needs a `--rope-scaling` flag.

**Gated MLA:** `g_proj` (12288, 7168) and `o_proj` (7168, 12288) exist on **all
93 layers** (93 tensors each) — the output gate is shared machinery across both
attention types. 12288 = 96 × 128 = `num_heads × v_head_dim`.

### 2.4 MoE: "Stable LatentMoE" — experts live in a 3584-d latent space

| Field | Value |
|---|---|
| `num_experts` | 896 |
| `num_experts_per_token` | 16 |
| `num_shared_experts` | 2 |
| `moe_intermediate_size` | 3072 |
| **`routed_expert_hidden_size`** | **3584** |
| `latent_moe_use_norm` | true |
| `moe_router_activation_func` | `sigmoid` |
| `topk_method` | `noaux_tc` |
| `use_grouped_topk` | true, `num_expert_group` 1, `topk_group` 1 |
| `moe_renormalize` | true |
| `routed_scaling_factor` | 1.0 |

This is the architecturally distinctive part and the checkpoint confirms it:

```
block_sparse_moe.routed_expert_down_proj.weight  (3584, 7168)  BF16   hidden 7168 -> latent 3584
block_sparse_moe.experts.{0..895}.w1.weight_packed (3072, 1792) U8    latent 3584 -> 3072   [MXFP4]
block_sparse_moe.experts.{0..895}.w3.weight_packed (3072, 1792) U8    latent 3584 -> 3072   [MXFP4]
block_sparse_moe.experts.{0..895}.w2.weight_packed (3584, 1536) U8    3072 -> latent 3584   [MXFP4]
block_sparse_moe.routed_expert_norm.weight        (3584,)       BF16
block_sparse_moe.routed_expert_up_proj.weight     (7168, 3584)  BF16   latent 3584 -> hidden 7168
```

(1792 packed bytes × 2 values/byte = 3584 logical; 1536 × 2 = 3072. ✔)

**Why this matters for inference:** every routed expert is a 3072×3584 GLU pair
instead of a 3072×7168 one — **half the expert parameters for the same expert
count**. The hidden↔latent projections (51.4 M params/layer) are paid *once per
layer*, not once per expert. At 896 experts that trade is overwhelmingly
favourable: it is what makes a 896-expert / top-16 model land at 2.78 T instead
of ~5.4 T.

**Shared experts (2) are NOT in the latent space.** `shared_experts.gate_proj`
(6144, 7168), `up_proj` (6144, 7168), `down_proj` (7168, 6144) — full hidden
width, fused pair (6144 = 2 × 3072), and **BF16** (matched by the `ignore`
regex `re:.*shared_experts.*`). They are dense work on every token.

**Router:** `gate.weight` (896, 7168) BF16 + `gate.e_score_correction_bias`
(896,) **F32** — the `noaux_tc` (auxiliary-loss-free) bias-corrected top-k of
DeepSeek-V3 lineage. Blog adds "**Quantile Balancing**" for expert allocation
([Moonshot blog](https://www.kimi.ai/blog/kimi-k3)) — ⚠️ **TO BE VERIFIED**
whether Quantile Balancing is training-time only or has an inference-time
component; the checkpoint exposes only the standard bias tensor, which suggests
training-time only.

Blog wording re-fetched verbatim 2026-09-19: "**Quantile Balancing derives
expert allocation directly from router-score quantiles, eliminating heuristic
updates and a sensitive balancing hyperparameter**". "Eliminating heuristic
updates" is the aux-loss-free *bias-update rule*, which runs during training —
so the training-time-only reading is now supported by the source, not just
inferred from the tensor inventory. The marker stays because the blog never
states it explicitly and the tech report is unparsed (§12 Q13).

### 2.5 Attention Residuals (AttnRes) — the special mechanism

Per-layer tensors present on all 93 layers:

```
self_attention_res_norm.weight (7168,)   BF16
self_attention_res_proj.weight (1, 7168) BF16     <- rank-1 projection
mlp_res_norm.weight            (7168,)   BF16
mlp_res_proj.weight            (1, 7168) BF16     <- rank-1 projection
```
plus model-level `output_attn_res_norm.weight` (7168,) and
`output_attn_res_proj.weight` (1, 7168).

`attn_res_block_size: 12`. Total cost: **4,021,248 params (0.00014 % of the
model)** — a rank-1 gate per sublayer per layer. The blog describes AttnRes as
"selectively retrieves representations across depth rather than accumulating
them uniformly" ([Moonshot blog](https://www.kimi.ai/blog/kimi-k3)).

**Inference consequence:** it is a cross-depth read, which means an engine
cannot treat layers as fully independent. SGLang exposes a dedicated knob,
`SGLANG_K3_ATTN_RES_MODE=jit`, and sets it on **every Hopper cell** (H100 and
H200 High-Throughput) but on no Blackwell cell
([SGLang cookbook](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3)) —
i.e. AttnRes needs a JIT-compiled path where there is no hand-written kernel.

Also model-specific in vLLM: `VLLM_KIMI_K3_SHARD_SP_SHARED_EXPERT=1` and
`VLLM_SSM_CONV_STATE_LAYOUT=DS` in the PD-prefill profile, and
`VLLM_ENABLE_K3_LATENT_MOE_TAIL_FUSION=1` in the RedHatAI FP8 recipe
([vLLM PD strategy](https://recipes.vllm.ai/moonshotai/Kimi-K3/strategies/pd_cluster.json),
[RedHatAI FP8 card](https://huggingface.co/RedHatAI/Kimi-K3-FP8-BLOCK)).

### 2.6 Vision encoder — MoonViT-V2

`vision_config`:

| Field | Value |
|---|---|
| `vt_num_hidden_layers` | 27 |
| `vt_hidden_size` | 1024 |
| `vt_intermediate_size` | 4096 |
| `vt_num_attention_heads` | 12 |
| `qkv_hidden_size` | 1536 |
| `patch_size` | 14 |
| `merge_kernel_size` | (2, 2) — 4 patches → 1 LLM token |
| `merge_type` | `sd2_tpool` |
| `mm_projector_type` | `patchmergerv2`, `mm_hidden_size` 1024, `text_hidden_size` 7168 |
| `pos_emb_type` | `divided_fixed`, init 64×64 spatial + 4 temporal, bilinear interpolation |
| `norm_type` | `rmsnorm`; `activation_func` `gelu_pytorch_tanh`; no biases |
| `_attn_implementation` | **`flash_attention_2`** |

Note the ViT asymmetry: `wqkv` is (4608, 1024) = 3 × 1536 while `wo` is
(1024, 1536) — the attention inner width is 1536, wider than the 1024 residual
stream (12 heads × 128).

`preprocessor_config.json` media limits
([source](https://huggingface.co/moonshotai/Kimi-K3/raw/main/preprocessor_config.json)):
`in_patch_limit: 65536` (→ 16,384 LLM tokens max per image),
`patch_limit_on_one_side: 512`, `in_patch_limit_each_frame: 16384`,
`in_patch_limit_video: 655360`, `sample_fps: 8.0`,
`temporal_merge_kernel_size: 4`, `timestamp_mode: "hh:mm:ss.fff"`,
`image_mean/std = 0.5`.

⚠️ **Video is in the preprocessor but not in the open serving contract.** The
config carries video params and the model card reports Video-MME 90.0 / MMVU
82.1, but SGLang states: "The open-source K3 serving contract currently supports
**image input only** — its processor rejects video and audio input"
([SGLang cookbook](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3)).
The model card's own summary table lists Modality as "Text, Image".

---

## 3. Parameter count

Derived from the **actual safetensors headers**, not from config arithmetic:
the header of each of the 96 shards was fetched with an HTTP `Range` request
(first 8 bytes = little-endian header length, then the JSON header), giving
exact dtype and shape for all **497,220 tensors**.

```
dtype census: U8 494,592 tensors | BF16 2,122 | F32 506
storage bytes: 1,560,860,324,864 across tensors (repo total 1,560,936,091,448 incl. headers)
```

### 3.1 Totals

```python
# MXFP4 weight_packed tensors hold 2 logical values per byte; weight_scale tensors are metadata.
packed_params = sum(nelem(t)*2 for t in tensors if t.endswith("weight_packed"))
bf16_params   = sum(nelem(t)   for t in tensors if dtype=="BF16")
f32_params    = sum(nelem(t)   for t in tensors if dtype=="F32")
```

| Class | Params | |
|---|---:|---|
| MXFP4-packed (routed experts only) | **2,722,740,830,208** | 2.7227 T — 97.943 % |
| BF16 | **57,179,884,544** | 57.180 B |
| F32 | **11,122,432** | 11.12 M |
| **TOTAL** | **2,779,931,837,184** | **2.7799 T** |

Model card claims **2.8 T** → ✔ (2.7799 T rounds to 2.8 T).
MXFP4 block-scale bytes (not parameters): **85,085,650,944 B = 85.09 GB**.

### 3.2 Per-component breakdown

| Component | Params | Share | dtype |
|---|---:|---:|---|
| MoE routed experts (92 × 896 × 3 × 3072 × 3584) | 2,722,740,830,208 | 97.943 % | **MXFP4** |
| KDA attention (69 layers) | 18,462,995,712 | 0.664 % | BF16 (+F32 conv/A_log/dt_bias/o_norm) |
| MoE shared experts (92 layers) | 12,155,092,992 | 0.437 % | BF16 |
| `g_proj` (93 layers) | 8,191,475,712 | 0.295 % | BF16 |
| `o_proj` (93 layers) | 8,191,475,712 | 0.295 % | BF16 |
| MoE latent up+down proj (92 layers) | 4,726,980,608 | 0.170 % | BF16 |
| MLA attention (24 layers) | 1,344,847,872 | 0.048 % | BF16 |
| `embed_tokens` (163840 × 7168) | 1,174,405,120 | 0.042 % | BF16 |
| `lm_head` (163840 × 7168) | 1,174,405,120 | 0.042 % | BF16 |
| dense MLP, layer 0 (3 × 7168 × 33792) | 726,663,168 | 0.026 % | BF16 |
| MoE router gates (92 × 896 × 7168) | 590,872,576 | 0.021 % | BF16 (+F32 bias) |
| **`vision_tower`** | **401,214,464** | 0.014 % | BF16 |
| `mm_projector` | 46,144,512 | 0.002 % | BF16 |
| norms + AttnRes rank-1 projections | 4,021,248 | 0.0001 % | BF16 |
| `routed_expert_norm` (92 × 3584) | 329,728 | ~0 | BF16 |
| **Sum of rows above** | **2,779,931,754,752** | 100 % | (delta vs total: **82,432**) |

**Correction (2026-09-19 fact-check).** The rows above sum to 2,779,931,754,752,
not to the 2,779,931,837,184 total: the missing **82,432** params are the 92 F32
`gate.e_score_correction_bias` vectors (92 × 896), which the "MoE router gates
(92 × 896 × 7168)" row excludes by construction. Add them and the delta is 0.
Totals cross-checked against the HF safetensors index
([HF API `?expand[]=safetensors`](https://huggingface.co/api/models/moonshotai/Kimi-K3?expand[]=safetensors):
U8 2,722,740,830,208 · BF16 57,179,884,544 · F32 11,122,432 · total
2,779,931,837,184 ✔).

**Vision encoder: 401,214,464 params.** Model card claims **401 M** → ✔ exact.

### 3.3 Active parameters per token

```python
exp        = 3*3072*3584            =    33,030,144   # one routed expert
kda_layer  = 3*12288*7168 + 128*7168 + 12288*128 + 96*7168 + 3*12288*4 + 12288 + 128 + 128
                                    =   267,579,648
mla_layer  = 1536*7168 + 18432*1536 + 576*7168 + 24576*512 + 1536 + 512
                                    =    56,035,328
g_plus_o   = 2*12288*7168           =   176,160,768   # every layer
moe_layer  = 16*exp + 2*7168*3584 + 3*7168*6144 + 896*7168 + 3584
           = 528,482,304 + 51,380,224 + 132,120,576 + 6,422,528 + 3,584
                                    =   718,409,216
dense0     = 3*7168*33792           =   726,663,168

attention_total = 69*kda_layer + 24*mla_layer + 93*g_plus_o
                = 18,462,995,712 + 1,344,847,872 + 16,382,951,424 = 36,190,795,008
active_no_head  = attention_total + 92*moe_layer + dense0 + norms(4,021,248)
                = 103,015,127,296
active_+lm_head = 103,015,127,296 + 163840*7168 = 104,189,532,416
active_+embed   = 105,363,937,536
```

| Definition | Value |
|---|---:|
| Active, excluding embed and lm_head | 103.02 B |
| **Active, including lm_head (standard convention)** | **104.19 B** |
| Active, including lm_head and embedding lookup | 105.36 B |

**Model card claims 104 B activated parameters** → ✔ matches the
`active + lm_head` convention exactly.

### 3.4 The number that drives everything downstream

Of the 104.19 B active parameters:

| | Params | Share |
|---|---:|---:|
| MXFP4 (16 routed experts × 92 layers) | 48,620,371,968 | **46.7 %** |
| BF16 (attention, shared experts, latent projections, dense layer 0, lm_head) | 55,569,160,448 | **53.3 %** |

**The model is 97.9 % MXFP4 by total size but only 46.7 % MXFP4 by active
parameters.** MXFP4 buys capacity; it does **not** buy a 4× decode speedup,
because a majority of the per-token work is still BF16. This single fact
explains most of §6 and most of the observed batch-1 latency.

### 3.5 Non-quantised tensors (the `ignore` list, resolved against the checkpoint)

Everything below is **BF16 or F32** in the shipped checkpoint:

| Regex in `quantization_config.ignore` | Resolves to | Params |
|---|---|---:|
| `re:.*self_attn.*` | all KDA + MLA + `g_proj` + `o_proj` on 93 layers | 36,190,795,008 |
| `re:.*shared_experts.*` | 92 × (gate/up/down at 6144 width) | 12,155,092,992 |
| `re:.*mlp\.(gate\|up\|gate_up\|down)_proj.*` | layer-0 dense MLP only | 726,663,168 |
| `re:.*lm_head.*` | `lm_head` | 1,174,405,120 |
| `re:.*vision_tower.*` | MoonViT-V2 | 401,214,464 |
| `re:.*mm_projector.*` | projector | 46,144,512 |
| (not matched by any regex, but not `Linear`) | `embed_tokens`, router gates **+ F32 router bias**, all norms, AttnRes projections, `routed_expert_up/down_proj`, `routed_expert_norm` | 6,496,691,712 |
| | **Total non-MXFP4** | **57,191,006,976** (57,179,884,544 BF16 + 11,122,432 F32) |

(Corrected 2026-09-19: the previous figures, 6,496,451,328 and 57,190,766,592,
were 240,384 short of §3.1's own BF16 + F32 sum. Recomputed from `config.json`:
embed 1,174,405,120 + router gate 590,872,576 + router bias 82,432 + norms/AttnRes
4,021,248 + latent up/down 4,726,980,608 + `routed_expert_norm` 329,728 =
6,496,691,712.)

F32 tensors specifically (506 of them, 44.5 MB): `{q,k,v}_conv1d` (69 × 3),
`dt_bias` (69), `A_log` (69), `o_norm` (69), `gate.e_score_correction_bias` (92).
These are tiny but must not be silently downcast — the conv kernels and decay
parameterisation are numerically sensitive.

---

## 4. Weight memory by dtype

Per METHODOLOGY §1. Split experts (2.7227 T params) from everything else
(57.19 B params) because only the experts are quantised.

Bytes/element used: BF16 2 · FP8 E4M3 blockwise 1 (+~3 % scales) ·
**MXFP4 0.5 + 1 B E8M0 per 32 = 0.53125** ·
**NVFP4 0.5 + 1 B FP8-E4M3 per 16 = 0.5625** · INT4 g128 ≈ 0.53.

| Scheme | Experts (GB) | Non-expert (GB) | **Total GB** | **GiB** | /8 GPUs (GB) | Notes |
|---|---:|---:|---:|---:|---:|---|
| **Native MXFP4 ckpt (as shipped)** | 1446.5 | 114.4 | **1560.9** | **1453.7** | 195.1 | matches measured 1560.94 GB ✔ |
| MXFP4 experts + FP8 non-expert | 1446.5 | 58.9 | **1505.4** | 1402.0 | 188.2 | ≈ `amd/Kimi-K3-Quark-MXFP4-AttnFP8` (measured 1505.84) ✔ |
| NVFP4 g16 experts + FP8 non-expert | 1531.5 | 58.9 | **1590.5** | 1481.3 | 198.8 | ≈ `nvidia/Kimi-K3-NVFP4` (measured 1609.92) |
| NVFP4 g16 experts + BF16 non-expert | 1531.5 | 114.4 | **1645.9** | 1532.9 | 205.7 | ≈ `RedHatAI/Kimi-K3-NVFP4` (measured 1646.07) ✔ |
| All FP8 E4M3 block-128 (+3 %) | 2804.4 | 58.9 | **2863.4** | 2666.7 | 357.9 | ≈ `RedHatAI/Kimi-K3-FP8-BLOCK` (measured 2819.94; that repo leaves attention BF16) |
| **All BF16 (full dequantisation)** | 5445.5 | 114.4 | **5559.9** | **5178.0** | 695.0 | no such checkpoint exists |
| INT4 AWQ/GPTQ g128 (+~3 %) | 1443.1 | 114.4 | 1557.5 | 1450.5 | 194.7 | **hypothetical — no INT4 checkpoint published** |

Measured HF repo sizes (safetensors only), for cross-check:

| Repo | GB | GiB | Δ vs native |
|---|---:|---:|---:|
| `moonshotai/Kimi-K3` (MXFP4) | 1560.94 | 1453.74 | — |
| `amd/Kimi-K3-Quark-MXFP4-AttnFP8` | 1505.84 | 1402.44 | −3.5 % |
| `nvidia/Kimi-K3-NVFP4` | 1609.92 | 1499.36 | +3.1 % |
| `RedHatAI/Kimi-K3-NVFP4` | 1646.07 | 1533.03 | +5.5 % |
| `RedHatAI/Kimi-K3-FP8-BLOCK` | 2819.94 | 2626.29 | +80.7 % |

Source: HF tree API per repo.

**Two counter-intuitive results worth stating:**

1. **NVFP4 is *bigger* on disk than MXFP4 for this model** (+49 GB to +85 GB).
   NVFP4's group size is 16 with a 1-byte FP8 scale (6.25 % overhead) vs MXFP4's
   group 32 with a 1-byte E8M0 scale (3.125 %). You adopt NVFP4 for Blackwell
   tensor-core throughput and accuracy, **not** for capacity.
2. **Dequantising to BF16 costs 3.56×** (1561 → 5560 GB) and is not a thing
   anyone does; Hopper instead keeps MXFP4 on disk and dequantises *in the
   kernel* via Marlin (W4A16) — see §8.

**Scale-overhead arithmetic, shown:**
```
MXFP4 experts : 2,722,740,830,208 × 0.5      = 1,361,370,415,104 B (packed)
                2,722,740,830,208 / 32 × 1 B =    85,085,650,944 B (E8M0 scales)  [measured exactly ✔]
                                               = 1,446,456,066,048 B = 1446.46 GB
non-expert    : 57,179,884,544 × 2 + 11,122,432 × 4 = 114,403,858,816 B = 114.40 GB
                                        total  = 1,560,859,924,864 B ≈ 1560.86 GB
```

---

## 5. KV cache and per-sequence state

Kimi-K3 is a **hybrid** model, so it has two independent memory pools. SGLang
calls them the *MLA KV pool* (paged, per token) and the *KDA state pool*
(worst-case reserved, per request) and splits static memory between them with
one flag, `--mamba-full-memory-ratio`.

### 5.1 The authoritative formula

SGLang's own sizing calculator, extracted verbatim from the cookbook page
source ([SGLang cookbook](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3)).

✅ **RE-FETCHED AND REPRODUCED 2026-09-19 (sweep).** The earlier fact-check
marked every SGLang-sourced figure UNVERIFIABLE because the cookbook renders as a
React deploy configurator and the numbers are absent from the *visible* HTML.
They are not absent from the *served* HTML: `curl -sL <cookbook URL>` returns a
968 KB document whose embedded RSC/JS island carries the calculator source, the
`config.cells` array, `supportedHardware` and every benchmark cell verbatim
(`curl -sL https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3 |
grep -o 'tokens_per_sec_per_gpu'` → 19 hits). All 18 speed cells of §10.1, the
`slots` expression behind §5.3, the 101/68/91/60 caps of §5.6, the §8.4 flag
sets, the §10.2 GB200 prefill figures, the §10.3 AITER timings and the §10.4
"+72 %" DCP line were re-extracted from that payload and match this document
character for character. **The ⚠️ markers previously carried at §5.1 and §10.1
are removed; verification-log row 18 is superseded.**

```js
const ssmBytes = ssmDtype === `float32` ? 4 : 2;
const kvBytes  = kvDtype  === `fp8_e4m3` ? 1 : 2;
const stateBytesPerSlot   = 69 * (96/attnTp * 128 * 128 * ssmBytes
                                + 3 * 3 * (96/attnTp) * 128 * 2);
const kvBytesPerToken     = 24 * (512 + 64) * kvBytes;
const draftKvBytesPerToken = specOn ? 1400 : 0;
const ratio = (slots + drafts) * stateBytesPerSlot
            / ((kvBytesPerToken/dcp + draftKvBytesPerToken) * length);
```

This matches the METHODOLOGY §2 derivation term for term:
`24 × (kv_lora_rank + qk_rope_head_dim) × B` for MLA, and
`n_heads × d_k × d_v × B_state` + conv state for the linear layers. Note the
conv state is **always 2 bytes** (bf16) and covers `kernel_size − 1 = 3`
positions for each of q/k/v.

### 5.2 MLA KV — per token

```
kv_bytes_per_token = 24 layers × (512 + 64) elements × B
  BF16 (B=2): 24 × 576 × 2 = 27,648 B  = 27.0 KiB / token
  FP8  (B=1): 24 × 576 × 1 = 13,824 B  = 13.5 KiB / token
```

For scale: a DeepSeek-V3-class model caches on **61** MLA layers — 61 × 576 × B =
**70,272 / 35,136 / 17,568 B per token at BF16 / FP8 / FP4** (METHODOLOGY §2,
same pinned figures as the DeepSeek-R1/V3 docs). Kimi-K3 caches on **24**,
because 69 of 93 layers are linear. That is a **2.5× reduction in per-token KV**
(70,272 / 27,648 = 2.54) relative to an all-MLA model of the same depth, before
any FP8.

⚠️ **NVFP4/INT4 KV: not offered.** No engine surveyed exposes a 4-bit KV option
for this model; `--kv-cache-dtype` choices are `auto`/`bf16` and `fp8`/`fp8_e4m3`
only (vLLM, SGLang, TokenSpeed). The checkpoint carries
`kv_cache_scheme: null`.

### 5.3 KDA per-sequence state — the real constraint

```
state_bytes_per_slot(attnTp=1) = 69 × (96 × 128 × 128 × ssmBytes + 9 × 96 × 128 × 2)
  float32 (default): 69 × (6,291,456 + 221,184) = 449,372,160 B = 428.6 MiB / slot
  bfloat16         : 69 × (3,145,728 + 221,184) = 232,316,928 B = 221.6 MiB / slot
```

and a request holds **more than one slot**. SGLang's `S` (slots per request):

| `--mamba-radix-cache-strategy` | S | float32 state / request | bfloat16 |
|---|---:|---:|---:|
| `extra_buffer` (default) | 5 | **2.25 GB** | 1.16 GB |
| `extra_buffer_lazy` | 4 | 1.80 GB | 0.93 GB |
| `no_buffer` | 3 | 1.35 GB | 0.70 GB |
| radix cache disabled | 1 | 0.45 GB | 0.23 GB |
| PD **decode** role (chunk cache) | 1 | 0.45 GB | 0.23 GB |

Source, re-extracted from the cookbook payload 2026-09-19 (§5.1): `slots =
pdRole===decode ? 1 : radixOff ? 1 : strategy===no_buffer ? 3 :
3 − (skipLock?1:0) + (overlapOff || strategy===extra_buffer_lazy ? 1 : 2)` — which
yields 5 / 4 / 3 / 1 / 1 exactly as tabulated.

`SGLANG_OPT_MAMBA_SKIP_DECODE_LOCK=1` frees one more slot. Under speculative
decoding add `D = block_size + 1 = 8` **more** slots (unless
`--enable-linear-replayssm-spec` folds them into a per-slot ring, returning
D to 0) — i.e. DSPARK at default block 7 roughly **2.6×** the state bill.

**The KDA state pool does not shard with DP, EP or DCP.** SGLang is explicit:
"The KDA state pool is the concurrency ceiling — DP, EP, and DCP do not shard
it; only **attention-TP width**, **SSM dtype**, and **cache strategy** change the
per-GPU bill." Divide by `attnTp`, and nothing else.

### 5.4 Totals at 8K / 32K / 128K / 1M (per request, attnTp = 1, no DCP)

| Context | MLA KV BF16 | MLA KV FP8 | FP8 KV + state (f32, S=5) | FP8 KV + state (bf16, S=4) |
|---:|---:|---:|---:|---:|
| 8,192 | 216 MiB | 108 MiB | **2,251 MiB** | 994 MiB |
| 32,768 | 864 MiB | 432 MiB | **2,575 MiB** | 1,318 MiB |
| 131,072 | 3,456 MiB | 1,728 MiB | **3,871 MiB** | 2,614 MiB |
| 1,048,576 | 27,648 MiB | 13,824 MiB | **15,967 MiB** | 14,710 MiB |

**Read the crossover.** Below ~**163 K tokens at FP8 KV** (~**81 K at BF16 KV**),
the fixed KDA state costs more than the entire KV cache. At 8 K the state is
**95 % of the per-request bill**.

*(Corrected 2026-09-19: this said "~78 K tokens", which matches neither column.
Recomputed: state at S=5/float32 = 5 × 449,372,160 = 2,246,860,800 B; divided by
13,824 B/token (FP8 KV) = **162,533 tokens**; by 27,648 B/token (BF16 KV) =
**81,267 tokens**. The table's shaded columns are FP8, so 163 K is the figure
that goes with it — the state-bound regime is wider than stated, which
strengthens, not weakens, the conclusion below.)*
This inverts the usual MoE sizing intuition completely:

- Short-context, high-concurrency chat traffic is **state-bound**, not KV-bound.
  FP8 KV buys you almost nothing there; `--mamba-ssm-dtype bfloat16` and
  `extra_buffer_lazy` buy you everything.
- Long-context traffic is KV-bound as usual, and FP8 KV + DCP are the levers.

SGLang says the same in one line: "`bfloat16` state buys admission, `fp8` KV
buys context."

### 5.5 Effect of sliding window / compression / cross-layer sharing / sparse attention

| Mechanism | Present in Kimi-K3? | Effect |
|---|---|---|
| Sliding window | **No** — no `sliding_window` field in `text_config`. The DSPARK *draft* model uses `sliding_attention` layers, the target does not. | — |
| KV compression | **Yes, structurally** — MLA's 512-d latent + 64 rope slot replaces 96 × (192 + 128) = 30,720 per-head elements. Ratio **53.3×** on the 24 full-attention layers. | Already counted above |
| Cross-layer KV sharing | **No** cross-layer sharing between MLA layers | — |
| "Layer-type sharing" | **Yes, effectively** — 69 of 93 layers hold **zero** per-token cache | 2.5× reduction vs all-MLA at the same depth |
| Sparse / indexer attention (DeepSeek-DSA style) | **No.** No indexer tensors, no `index_*` config fields, no sparse-attention flags in any engine recipe. | Full-attention layers are dense-causal |
| Attention-type asymmetry | 69 KDA (O(1) per token) + 24 MLA (O(T)) | see §6 |

⚠️ **The HF `transformers` reference path is not memory-viable at long context.**
`modeling_kimi_linear.py` materialises full per-head keys and values
(`key_states = cat(k_pass, k_rot)` → 96 × 192, `value_states` → 96 × 128) and
calls `past_key_values.update(key_states, value_states, ...)`. That is
**30,720 elem/token/layer × 24 = 1.47 MB/token at BF16** — 53× the latent form,
≈ 1.5 TB at 1 M tokens. Production engines use the absorbed/latent MLA cache
(512 + 64). Use `transformers` for correctness reference only.

### 5.6 Does it fit one 8-GPU B300 node? — the brief's question, checked

First, the capacity number. **Two primary sources disagree:**

| Source | B300 per-GPU HBM | 8-GPU node |
|---|---:|---:|
| [vLLM recipe hardware profile](https://recipes.vllm.ai/moonshotai/Kimi-K3/hw/b300.json) — "NVIDIA B300 SXM 268 GB HBM3e · 8-GPU HGX B300 node (Blackwell Ultra)", `vram_gb: 2144` | **268 GB** | **2144 GB** |
| [SGLang cookbook](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3) — the fully-data-parallel shape is "**288 GB GPUs only**", in a list where B300 is a member | **288 GB** | **2304 GB** |

✅ **RESOLVED 2026-09-19 against NVIDIA datasheets — both numbers are right, for
different products.** The 268-vs-288 split is an **HGX/DGX B300 vs GB300 NVL72**
distinction, exactly as suspected:

| NVIDIA product | Source | Total GPU memory | Per GPU |
|---|---|---:|---:|
| **HGX B300** (8× Blackwell Ultra SXM) | [nvidia.com/data-center/hgx](https://www.nvidia.com/en-us/data-center/hgx/) — "Total GPU memory: 2.1 TB" | **2.1 TB** | **≈268 GB** |
| **DGX B300** (8× Blackwell Ultra SXM) | [nvidia.com/data-center/dgx-b300](https://www.nvidia.com/en-us/data-center/dgx-b300/) — "Total GPU Memory: 2.1 TB" | **2.1 TB** | **≈268 GB** |
| **GB300 NVL72** (72× Blackwell Ultra) | [nvidia.com/data-center/gb300-nvl72](https://www.nvidia.com/en-us/data-center/gb300-nvl72/) — "20 TB HBM3e" (re-fetched 2026-09-19) | **20 TB** | **288 GB physical, ≈ 279 usable** |

*(Corrected 2026-09-19 sweep: this row previously read "20 TB ÷ 72 = 288 GB".
20 TB ÷ 72 = **277.8 GB**, which is NVIDIA's rounded **usable** aggregate; the
**288 GB physical / ≈ 279 usable** split is the pinned pair in
[METHODOLOGY §8](../../METHODOLOGY.md) and
[cross-cutting/cloud-pricing.md](../../cross-cutting/cloud-pricing.md), sourced
to Lenovo's GB300 NVL72 product guide. The 268-vs-288 conclusion is unaffected.)*

So vLLM's `vram_gb: 2144` / "NVIDIA B300 SXM 268 GB HBM3e · 8-GPU HGX B300 node"
is correct for the **HGX/DGX B300 node this section sizes**
([b300.json, re-fetched 2026-09-19](https://recipes.vllm.ai/moonshotai/Kimi-K3/hw/b300.json)),
and SGLang's "288 GB GPUs" refers to the **GB300 / MI355X** class. The task
brief's 2304 GB is the MI355X node (8 × 288 GB). **Plan with 268 GB on HGX B300;
288 GB is not upside on that SKU, it is a different SKU.**

**The fit, at 268 GB/GPU:**

```
weights      1560.9 GB / 8 = 195.1 GB/GPU          = 72.8 % of a 2144 GB node
mem-fraction-static 0.85 -> 227.8 GB/GPU static    ->  32.7 GB/GPU free (262 GB/node)
mem-fraction-static 0.92 -> 246.6 GB/GPU static    ->  51.5 GB/GPU free (412 GB/node)
```

**Yes — it fits on one 8×B300 node, and both engines make that their default.**
vLLM's `recommended_command` is `hardware: b300, strategy: single_node_tp,
node_count: 1, --tensor-parallel-size 8`; SGLang's only **Verified** cells are
`b300 1×8 Unified`. For contrast, RedHatAI report "**Kimi-K3 does not fit on a
single GB300 node**" — a GB300 NVL4 *tray* is 4 × 288 = 1152 GB < 1561 GB
([RedHatAI DSpark card](https://huggingface.co/RedHatAI/Kimi-K3-speculator.dspark)).

**KV headroom — is there actually room to serve?** Estimated concurrency
(METHODOLOGY §3), `attnTp = 8`, float32 state, S = 5, FP8 KV:

```
state/slot @ attnTp8 = 69 × (12 × 128 × 128 × 4 + 9 × 12 × 128 × 2) = 56,171,520 B = 53.6 MiB
per request          = 5 × 53.6 MiB + ctx × 13,824 B / DCP
```

| mem-frac | ctx | DCP | per request | **max concurrency (est.)** |
|---:|---:|---:|---:|---:|
| 0.85 | 8 K | 1 | 375.8 MiB | 82 |
| 0.85 | 8 K | 8 | 281.3 MiB | **110** |
| 0.85 | 32 K | 8 | 321.8 MiB | 96 |
| 0.85 | 128 K | 8 | 483.8 MiB | 64 |
| 0.92 | 8 K | 8 | 281.3 MiB | 174 |
| 0.92 | 128 K | 8 | 483.8 MiB | 101 |
| 0.85 | 1 M | 8 | 1,996 MiB | **15** |

*(Corrected 2026-09-19 sweep: the 1 M row read **16**. Recomputed in bytes —
free/GPU = 0.85 × 268e9 − 1,560.9e9/8 = 32.6875e9 B; per request/GPU =
5 × 56,171,520 + 1,048,576 × 13,824/8 = 2,092,796,928 B; ratio **15.62**, and
METHODOLOGY §3 takes `floor`, so **15**. Every other row already floors
correctly: 82.94 / 110.80 / 96.86 / 64.43 / 174.39 / 101.40.)*

**Validation against a published measurement** (re-extracted from the cookbook
payload 2026-09-19, §5.1 — verbatim: "The KDA state pool still clamps admission
below that (101 / 68 / 91 / 60 concurrent requests for MXFP4 NOSPEC / MXFP4
DSPARK / NVFP4 NOSPEC / NVFP4 DSPARK)"). SGLang publishes the actual
admitted-request caps for the B300 1×8 *Balanced* cell (`--tp-size 8
--dcp-size 8 --mem-fraction-static 0.85`): **101 / 68 / 91 / 60** concurrent
requests for MXFP4-NOSPEC / MXFP4-DSPARK / NVFP4-NOSPEC / NVFP4-DSPARK. My
estimate for the matching row is **110 vs measured 101 — +9 %**. The model is
sound; the residual is activation workspace and CUDA-graph pools. Note also
that MXFP4→DSPARK drops the cap 101 → 68 (−33 %), consistent with D = 8 extra
verify slots.

**Bottom line on headroom:** one 8×B300 node holds the weights with ~32.7 GB/GPU
to spare at the default `mem-fraction-static 0.85` (195.1 GB/GPU of weights
against a 227.8 GB/GPU static budget — arithmetic re-checked in bytes
2026-09-19), which supports roughly **100 concurrent requests at 8 K–32 K** and
**15 at 1 M**. Serving 1 M
context at any meaningful concurrency needs more than one node.

### 5.7 DCP — the only lever that shards MLA KV

`--dcp-size 8` (SGLang) / `--decode-context-parallel-size 8` (vLLM) shards the
otherwise TP-replicated MLA KV across the TP ranks. SGLang quantifies it:
"Deduplicates the attention-TP group's MLA KV: **concurrency ceiling +72 % at the
same engine throughput, ~1.8× ITL**." Pick it for "Context ≥ ~16 K, or
per-replica concurrency past 128."

Caveats, all from the SGLang cookbook:
- DCP is force-incompatible with `--enable-symm-mem` (disabled for decode-graph
  correctness).
- L3 HiCache (Mooncake) **drops the DCP flags** — storage keys are not
  `dcp_rank`-aware yet.
- "Don't use EP with an a2a backend: a2a buffers reclaim the KV that DCP buys."
- In vLLM, DCP size must divide TP size, so it is scoped to the TP8 single-node
  profile, and it pairs `TOKENSPEED_MLA` decode with `TRTLLM_RAGGED` prefill.

---

## 6. Compute profile

### 6.1 FLOPs per token (GEMM)

```
2 × active_params = 2 × 104,189,532,416 = 208.4 GFLOP / token
  routed experts (MXFP4-eligible) : 2 × 48,620,371,968 =  97.2 GFLOP  (47 %)
  BF16/FP8 (attn, shared, latent, dense, lm_head) : 111.1 GFLOP  (53 %)
```

This 47/53 split is the number to carry into every GPU comparison: **a GPU with
FP4 tensor cores can accelerate at most 47 % of Kimi-K3's per-token FLOPs at
FP4 rates.** The remaining 53 % runs at BF16 or FP8 rates. Amdahl caps the FP4
speedup at 1/(0.53 + 0.47/k); even with k → ∞ that is **1.89×**.

### 6.2 Prefill attention FLOPs — where KDA pays

Causal, MLA layers only (KDA layers are O(T), not O(T²)):
`attn_flops(T) = 24 × 2 × 96 × (192 + 128) × T² / 2`

| T | MLA attention | GEMM (2·A·T) | attention share |
|---:|---:|---:|---:|
| 8,192 | 0.05 PFLOP | 1.71 PFLOP | 2.8 % |
| 32,768 | 0.79 PFLOP | 6.83 PFLOP | 10.4 % |
| 131,072 | 12.67 PFLOP | 27.31 PFLOP | 31.7 % |
| 1,048,576 | 810.65 PFLOP | 218.50 PFLOP | **78.8 %** |

Counterfactual with all 93 layers full-MLA: 3141.3 PFLOP at 1 M instead of
810.6 — **KDA removes 74 % of the quadratic attention FLOPs at every context
length**. That is the architectural reason a 1 M window is tractable here.

But note the honest reading: **at 1 M context, prefill is still 79 % attention.**
KDA makes 1 M possible, not cheap. A full 1 M prefill is ~1.03 EFLOP.

### 6.3 Bytes read per decode step (METHODOLOGY §4, MoE distinct-expert)

METHODOLOGY §4: `bytes_per_decode_step(batch) = weights_read(batch) + Σ_seq
kv_read(seq_ctx)`. For a hybrid model the second term has **two** parts — the
paged MLA KV, which scales with `batch × ctx × kv_bytes_per_token`, and the KDA
recurrent state, which scales with `batch` alone (one live slot per sequence is
read and rewritten every step; the other `S−1` slots are radix buffers and are
not touched per step).

```
distinct_experts(b) = 896 × (1 − (1 − 16/896)^b)       # uniform-routing estimate
expert_bytes        = 33,030,144 × 0.53125 = 17.55 MB  # per expert, incl. E8M0 scales
non_expert_bytes    = 114.4 GB − 2.35 GB (embed is a gather) = 112.0 GB, read every step
kv_read(b, ctx)     = b × ctx × 13,824 B                        # FP8 KV, aggregate over the node
state_read(b)       = b × 449,372,160 B                         # fp32 KDA state, 1 live slot/seq
```

At **ctx = 8,192, FP8 KV, fp32 state** (the measured operating point of §10.1):

| Batch | Distinct experts/layer | Expert bytes | Non-expert | MLA KV | KDA state | **Total** | vs batch 1 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 16.0 | 25.8 GB | 112.0 GB | 0.11 GB | 0.45 GB | **138.4 GB** | 1.00× |
| 8 | 120.3 | 194.2 GB | 112.0 GB | 0.91 GB | 3.59 GB | **310.7 GB** | 2.24× |
| 32 | 392.6 | 633.8 GB | 112.0 GB | 3.62 GB | 14.38 GB | **763.9 GB** | 5.52× |
| 64 | 613.2 | 989.9 GB | 112.0 GB | 7.25 GB | 28.76 GB | **1138.0 GB** | 8.22× |
| 128 | 806.7 | 1302.4 GB | 112.0 GB | 14.50 GB | 57.52 GB | **1486.4 GB** | 10.74× |
| 256 | 887.1 | 1432.1 GB | 112.0 GB | 28.99 GB | 115.04 GB | **1688.2 GB** | 12.19× |
| 512 | 895.9 | 1446.3 GB | 112.0 GB | 57.98 GB | 230.08 GB | **1846.4 GB** | 13.34× |

*(Corrected 2026-09-19 sweep: the previous table carried **no** KV or state term
at all — a flat `weights-only` per-step constant, which METHODOLOGY §3 names as a
known bug class. Recomputed in `python3`: the expert and non-expert columns are
unchanged; the totals and the `vs batch 1` ratios changed.)*

⚠️ **Feasibility.** On the single 8×B300 node sized in §5.6, `max_concurrency`
at 8 K with DCP8 is **110** (`mem-fraction-static 0.85`) or **174** (0.92). The
**batch 256 and 512 rows are therefore not reachable on one node** — they are
printed here because the byte curve is what motivates multi-node / multi-replica
shapes, not because a single node can run them. Read them as aggregate-across-
replicas arithmetic.

**Expert saturation happens at batch ≈ 128.** By batch 128 the step already
touches 90 % of all 896 experts per layer; past 256 the *weight* curve is flat.
Beyond that point Kimi-K3's weight traffic behaves like a **dense 1.56 TB
model**, and additional batching is nearly free in weight bytes — the classic MoE
arithmetic-intensity ramp, just unusually steep because 896 experts with top-16
saturate fast. What is *not* free past that point is the KDA state: it is
strictly linear in batch and by batch 512 it is 230 GB/step, 12 % of the bill and
growing while the expert term has stopped.

**At batch 1, 81 % of the bytes are the BF16 non-expert weights** (112.0 of
138.4 GB). Again: MXFP4 is a capacity win, not a latency win.

### 6.4 Implied bandwidth on the published B300 measurements

Using the measured SGLang B300 1×8 MXFP4 NOSPEC cells (§10) and the byte table
above:

| Cell | Concurrency | Bytes/step | TPOT | Aggregate BW | Per GPU |
|---|---:|---:|---:|---:|---:|
| Low-Latency | 1 | 138.4 GB | 8.51 ms | 16.27 TB/s | **2.03 TB/s** |
| Low-Latency | 16 | 483.3 GB | 19.47 ms | 24.82 TB/s | **3.10 TB/s** |
| Balanced (DCP8) | 64 | 1138.0 GB | 40.19 ms | 28.31 TB/s | **3.54 TB/s** |

*(Recomputed 2026-09-19 sweep with the KV + KDA-state terms §6.3 was missing;
was 137.9 / 474.4 / 1102.0 GB → 2.03 / 3.05 / 3.43 TB/s per GPU.)*

✅ B300 per-GPU HBM bandwidth **8.0 TB/s** — the value pinned as-deployed in
[METHODOLOGY §8](../../METHODOLOGY.md) for HGX B300, and independently
corroborated (re-fetched 2026-09-19) by NVIDIA's GB300 NVL72 "up to 576 TB/s"
aggregate over 72 Blackwell Ultra GPUs = 8.0 TB/s per GPU
([GB300 NVL72 datasheet](https://www.nvidia.com/en-us/data-center/gb300-nvl72/)).
Note the corroboration crosses SKUs — GB300 NVL72 is a *different part* from the
HGX B300 sized here (§5.6) and its capacity figures are never merged with HGX
B300's; METHODOLOGY §8 happens to pin both at 8.0 TB/s.
At 8 TB/s the implied MBU is **25 % / 39 % / 44 %** — below the
METHODOLOGY planning band of 0.5–0.7 for first-gen Blackwell software, which is
consistent with SGLang's own "Pre-release" / "Final Verification In Progress"
labelling and with 93 sequential layers of small per-GPU GEMMs under TP8.

### 6.5 Vision encoder FLOPs

```
per ViT token = 27 layers × (wqkv 1536×1024 + wo 1024×1536 + fc0 4096×1024 + fc1 1024×4096)
              = 311,427,072 params  ->  0.623 GFLOP / patch (2 × params)
```
Plus the projector: 4096×4096 + 7168×4096 = 46,137,344 params →
0.092 GFLOP per *merged* token.

| Image | Patches (14 px) | LLM tokens (after 2×2 merge) | ViT GEMM |
|---|---:|---:|---:|
| 448 × 448 | 1,024 | 256 | 0.638 TFLOP |
| 896 × 896 | 4,096 | 1,024 | 2.551 TFLOP |
| 1344 × 1344 | 9,216 | 2,304 | 5.740 TFLOP |
| max (`in_patch_limit`) | 65,536 | **16,384** | 40.83 TFLOP |

Reference point: 1,024 LLM tokens of *text* prefill cost 2 × 104.19 G × 1024
= **213 TFLOP**. The same 1,024 tokens delivered as one 896×896 image cost
2.55 TFLOP of ViT **plus** the same 213 TFLOP of LLM prefill. **The vision tower
is ~1.2 % of the cost of the tokens it produces** — it is never the bottleneck.
(ViT attention FLOPs are excluded above; at 4,096 patches they add
27 × 2 × 4096² × 1536 ≈ 1.4 TFLOP, still small.)

Serving notes: the ViT shards complete images across TP ranks
(`--mm-encoder-tp-mode data` in vLLM's AMD/TokenSpeed recipes; SGLang says "MM
encoder DP … Built in … leave `--mm-enable-dp-encoder` unset"). ViT CUDA-graph
capture (`SGLANG_VIT_ENABLE_CUDA_GRAPH=1`) is **recommended off**: "The win is
confined to the encoder — no reliable end-to-end TTFT/TPOT gain in full-model
serving." `--language-model-only` (vLLM) / `--language-only` skips the tower
entirely for text workloads.

---

## 7. Speculative decoding and MTP

### 7.1 What the checkpoint ships: nothing

`num_nextn_predict_layers: 0`. There is **no MTP / nextn head in
`moonshotai/Kimi-K3`** — verified against the tensor inventory (no
`model.layers.93.*`, no `nextn`, no `eh_proj`, no `shared_head`). Every
speculative option is a **separately trained external draft model**.

This is a meaningful difference from DeepSeek-V3/R1-lineage checkpoints, which
ship an MTP layer in-band.

### 7.2 DSPARK — the algorithm both engines use

DSPARK is a parallel-draft speculator: it "extends the **DFlash** parallel-draft
backbone with a **Markov logit-bias head** and a **per-position confidence
head**" ([RadixArk card](https://huggingface.co/RadixArk/Kimi-K3-DSpark)). The
draft reads **auxiliary hidden states** from several target layers, so it is
tightly coupled to the target's depth.

Published drafts:

| Repo | Params | Size | Draft architecture | Aux target layers | Block | Trained ctx | Framework |
|---|---:|---:|---|---|---:|---:|---|
| [`RadixArk/Kimi-K3-DSpark`](https://huggingface.co/RadixArk/Kimi-K3-DSpark) | 2,249,289,601 | 4.50 GB | 5 full-attention Qwen3-style GQA layers, hidden 7168, 64 Q / 16 KV heads, head_dim 64, intermediate 14336 | `[7, 23, 51, 67, 83]` | **7** | 65,536 (YaRN-16 → 1,048,576) | SpecForge (SGLang) |
| [`RedHatAI/Kimi-K3-speculator.dspark`](https://huggingface.co/RedHatAI/Kimi-K3-speculator.dspark) | 4,744,900,481 | 9.49 GB | `DSparkDraftModel`, hidden 7168, head_dim 64, intermediate 14336, sliding+full layers, markov_rank 256, confidence head | `[24, 48, 72, 88, 92]` | **8** | 8,192 | Speculators (vLLM) |

Both are BF16, Apache-2.0 (RedHatAI) — note the **draft is Apache-2.0 while the
target is under the Kimi K3 License**.

Also published, not benchmarked here: `Inferact/Kimi-K3-DSpark`,
`Inferact/Kimi-K3-DSpark-Block5`, `lightseekorg/kimi-k3-dspark`,
`lightseekorg/kimi-k3-eagle3-mla`, `modal-labs/Kimi-K3-DFlash`,
`lightseekorg/kimi-k3-dflash2`.

### 7.3 Measured acceptance lengths (the only real ones published)

`RadixArk/Kimi-K3-DSpark`, `acc_len` = SGLang's histogram-native request
acceptance length, averaged within question then equally across questions.
Verification width = 1 current + 7 draft tokens, so the ceiling is **8.0**.

| Dataset | Questions | acc_len | % of ceiling |
|---|---:|---:|---:|
| HumanEval | 164 | **5.5121** | 69 % |
| GSM8K | 1,319 | **5.4176** | 68 % |
| MBPP | 257 | **5.1980** | 65 % |
| SWE-Rebench | 50 | **4.6594** | 58 % |
| **RULER V2 1M (MK/MV/QA)** | 150 | **4.2553** | 53 % |
| MATH500 | 500 | **4.1329** | 52 % |
| MT-Bench | 80 | **3.9342** | 49 % |
| AIME26 | 30 | **2.9893** | 37 % |

RULER V2 partition detail: MK 4.4658, MV 4.3081, QA 3.9919, at actual prompt
lengths of 1,000,432–1,047,925 tokens. **Acceptance holds up at 1 M context** —
only ~23 % below the GSM8K figure.

AIME26 by output length (the interesting non-monotonicity):

| Output bucket | Q | Actual range | acc_len |
|---|---:|---|---:|
| 0–1K | 13 | 192–885 | 3.1310 |
| 1–2K | 5 | 1,359–1,828 | 2.5773 |
| 2–4K | 6 | 2,210–3,732 | 2.5632 |
| 4–8K | 4 | 5,187–7,750 | 2.7174 |
| 32K+ | 2 | 54,545–224,703 | **4.9194** |

Hard maths reasoning is the **worst** case (2.6–3.1), i.e. exactly the
`reasoning_effort=max` traffic Kimi-K3 is built for. The 32K+ bucket (n=2) is
not a reliable signal.

### 7.4 Measured end-to-end speedup

From SGLang's B300 1×8 Verified cells (ISL 8192 / OSL 1024, §10):

| Cell | Metric | NOSPEC | DSPARK | Speedup |
|---|---|---:|---:|---:|
| Low-Latency MXFP4, conc 1 | TPOT | 8.51 ms | **2.84 ms** | **3.00×** |
| Low-Latency MXFP4, conc 16 | TPOT | 19.47 ms | **9.88 ms** | **1.97×** |
| Balanced MXFP4, conc 64 | TPOT | 40.19 ms | **24.47 ms** | **1.64×** |
| Low-Latency NVFP4, conc 1 | TPOT | 10.12 ms | **3.24 ms** | **3.12×** |
| Balanced NVFP4, conc 64 | TPOT | 41.55 ms | **22.17 ms** | **1.87×** |

The 3.00× → 1.97× → 1.64× decay with batch is exactly METHODOLOGY §5's
"when it stops helping" — as batch grows, decode leaves the bandwidth-bound
regime (§6.3) and the extra verify FLOPs stop being free.

⚠️ **Critical caveat, stated by SGLang itself:** "DSPARK cells pin the
acceptance length via the serve env `SGLANG_SIMULATE_ACC_LEN=4.5` — they report
what block size 7 delivers **at that acceptance**, not a measured acceptance
rate for this workload… **no measured acceptance rate exists for a real workload
yet** — measure against the same recipe running NOSPEC before adopting." The
simulated 4.5 sits between RadixArk's MATH500 (4.13) and SWE-Rebench (4.66), so
it is a defensible mid-point, but **these are not end-to-end measured
speedups.**

### 7.5 Cost of speculation in memory

- **State:** D = block + 1 = 8 extra KDA slots per request. Measured effect on
  the B300 Balanced cap: **101 → 68 concurrent (−33 %)** for MXFP4.
  `--enable-linear-replayssm-spec` folds these into a per-slot ring, returning
  D → 0 (RadixArk's own recipe sets it).
- **Draft KV:** ~1,400 B/token, **replicated on every rank** (not DCP-sharded).
  Negligible without DCP; under DCP8 it is "the same order as the sharded MLA
  share" (13,824/8 = 1,728 B) — so DCP8 + DSPARK roughly **doubles** effective
  per-token KV.
- **Draft weights:** 4.5 GB (RadixArk) or 9.5 GB (RedHatAI) replicated per rank.
- **Scheduler:** "an unset `--max-running-requests` resets to **48** under spec"
  — Balanced DSPARK explicitly adds `--max-running-requests 256`.

### 7.6 Engine configuration

**vLLM** ([recipe JSON](https://recipes.vllm.ai/moonshotai/Kimi-K3.json)), opt-in
feature `spec_decoding`:
```
--speculative-config '{"model":"RedHatAI/Kimi-K3-speculator.dspark", "num_speculative_tokens":8,
                       "method": "dspark", "draft_sample_method": "probabilistic",
                       "rejection_sample_method": "block"}'
```
AMD override adds `--max-num-seqs 128`. NPU override uses
`"num_speculative_tokens":7, "enforce_eager":true`. Supported on strategies
`single_node_tp`, `multi_node_tp`, `multi_node_tep`, `multi_node_dep`,
`multi_node_tp_dp`, `pd_cluster`.

**SGLang** ([RadixArk card](https://huggingface.co/RadixArk/Kimi-K3-DSpark)):
```
--speculative-algorithm DSPARK \
--speculative-draft-model-path RadixArk/Kimi-K3-DSpark \
--speculative-dspark-block-size 7 \
--speculative-draft-attention-backend trtllm_mha \
--enable-linear-replayssm-spec
```
"Leave `--speculative-draft-attention-backend` unset" is the cookbook's advice;
RadixArk pin `trtllm_mha`. DSPARK **requires `pp_size == 1`**, so on B200/GB200
(which default to pipelined shapes) enabling it re-lays the GPUs flat:
PP2×TP8 → TP16, PP2×DCPEP8 → DCPEP16. **DSPARK is not offered on B200** in the
Deploy matrix for this reason.

**Other algorithms.** SGLang's `--speculative-algorithm` accepts Eagle/MTP,
DSPARK, DFLASH and N-gram. "**DFLASH has no published draft checkpoint**"
(cookbook) — though `modal-labs/Kimi-K3-DFlash` and
`lightseekorg/kimi-k3-dflash2` exist on HF; ⚠️ **TO BE VERIFIED** whether either
is the one the cookbook means. `lightseekorg/kimi-k3-eagle3-mla` suggests an
EAGLE-3 draft exists; no evals found.

⚠️ Spec × EP × DP-attention "is validated only at 8-GPU EP8 × DP2 (full GSM8K) —
**experimental** at these scales" (SGLang large-scale presets).

---

## 8. Engine support matrix

### 8.1 Minimum versions and images

| Engine | Min version | Image | Source |
|---|---|---|---|
| **vLLM** | **0.27.1+** | CUDA: **`vllm/vllm-openai:latest`** (CUDA 13 / cu130 **only**, no `-cu129` tag) — *corrected 2026-09-19 from `vllm/vllm-openai:kimi-k3`, which the recipe JSON does not emit and which contradicted §8.3 in this same document*; ROCm `vllm/vllm-openai_rocm:kimi-k3` | [recipe JSON, re-fetched 2026-09-19](https://recipes.vllm.ai/moonshotai/Kimi-K3.json) |
| **SGLang** | **v0.5.17** is the first numbered release carrying `kimi_k3.py` ([inference-engines.md §2.2](../../cross-cutting/inference-engines.md)); the cookbook states no floor of its own; recipes validated on the public `sgl-project/sglang` **`kimi-k3` branch**; measured on **`v0.5.18 @ 71de97b2`** (NVIDIA) and **`v0.5.19 @ 12771786`** (ROCm) | `lmsysorg/sglang:latest`; ROCm `lmsysorg/sglang-rocm:v0.5.19-rocm720-mi35x-20260910`; NVFP4 `lmsysorg/sglang:dev-dev-kimi-k3-nvfp4`; NPU `quay.io/ascend/sglang:main-cann9.0.0-a3` | [cookbook](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3) |
| **TokenSpeed** | not stated | — | [recipes](https://lightseek.org/tokenspeed/recipes/models) |
| **transformers** | `transformers_version: 4.56.2` in config; `trust_remote_code=True` required | — | config.json |
| **TensorRT-LLM** | ✅ **CORRECTED 2026-09-19 — Kimi-K3 *is* supported.** The TRT-LLM supported-models matrix lists `KimiK3ForConditionalGeneration` ("Kimi-K3", example `moonshotai/Kimi-K3`) and `KimiLinearForCausalLM` ("Kimi-K3 (text decoder)"). **"Kimi K3 is only supported on NVIDIA Blackwell GPUs (`SM100` family)"** — so no TRT-LLM path on H100/H200/A100. | — | [TRT-LLM supported models](https://nvidia.github.io/TensorRT-LLM/models/supported-models.html) |
| **NVIDIA Dynamo** | ⚠️ **No Kimi-K3-specific recipe found.** vLLM's PD/KV-store strategies use NIXL and Mooncake directly. | — | see §12 |
| **vLLM Ascend** | v0.23.0 tutorial exists (HTTP 200) | `quay.io/ascend/...` | [tutorial](https://docs.vllm.ai/projects/ascend/en/v0.23.0/tutorials/models/Kimi-K3.html) |

**Current engine releases, re-verified 2026-09-19** via
`curl -sL https://api.github.com/repos/<org>/<repo>/releases/latest`, matching
[cross-cutting/inference-engines.md](../../cross-cutting/inference-engines.md):
**vLLM `v0.29.0`, published 2026-09-09**; **SGLang `v0.5.20`, published
2026-09-18**; **TensorRT-LLM `v1.2.1` stable** (1.3.0rcNN is the pre-release
line). The floors in the table above are *minimums from the recipes*, not
current versions — nothing here is pinned to a superseded release.

**Host requirement (vLLM):** "The host needs an **r580+ NVIDIA driver**; on a
CUDA 12.9 (r575) host, upgrade the driver or build vLLM from the K3 branch
against cu129 PyTorch yourself."

The model card recommends exactly three engines: vLLM, SGLang, TokenSpeed
([README §5](https://huggingface.co/moonshotai/Kimi-K3/raw/main/README.md)).

### 8.2 Hardware support — what each engine claims

| GPU | vLLM `meta.hardware` | vLLM recipe exists | SGLang `supportedHardware` | Min GPUs (vLLM) |
|---|---|---|---|---:|
| B300 (HGX, 268 GB) | **verified** | ✔ `/hw/b300.json` | ✔ | 8 (1 node) |
| GB300 NVL4 (288 GB) ⚠️ **TO BE VERIFIED** — 288 GB/GPU is confirmed for GB300 ([NVL72 datasheet](https://www.nvidia.com/en-us/data-center/gb300-nvl72/): 20 TB ÷ 72), but the **"NVL4" tray SKU name** is not on any NVIDIA page fetched; NVIDIA documents GB300 as NVL72 | **verified** | ✔ | ✔ | 8 (2 trays) |
| B200 (180 GB) | **verified** | ✔ | ✔ | 16 (2 nodes) |
| GB200 NVL4 (192 GB) | **verified** | ✔ | ✔ | 16 (4 trays) |
| H200 (141 GB) | **verified** | ✔ | ✔ | 16 (2 nodes) |
| H100 (80 GB) | *not listed in `meta.hardware`* | ✔ `/hw/h100.json` | ✔ | **32 (4 nodes)** |
| MI355X (288 GB) | **verified** | ✔ | ✔ | 8 (1 node) |
| MI350X | — | (via mi355x cell) | ✔ | 8 (1 node) |
| MI325X (256 GB) | — | ✔ | ✗ | 8 (1 node) |
| MI300X (192 GB) | — | ✔ | ✗ | 16 (2 nodes) |
| Ascend 910C / A3 | **verified** | ✔ | ✔ (`a3`) | 64 |
| **A100** | **✗** | **✗ (404)** | **✗** | — |
| **RTX PRO 6000 Blackwell** | **✗** | **✗ (404)** | **✗** | — |
| L40S / GH200 | ✗ | ✗ (404) | ✗ | — |

Verified by direct fetch: `https://recipes.vllm.ai/moonshotai/Kimi-K3/hw/{a100,
rtx_pro_6000, rtx6000, l40s, gh200}.json` → **404** for all five, while
`/hw/{b300,h100,h200,b200,gb200,gb300,mi300x,mi325x,mi355x}.json` → 200.
SGLang's `config.supportedHardware` array is literally
`[b300, gb300, b200, gb200, h200, h100, mi350x, mi355x, a3]`.

#### A100 — not supported, and structurally so

Three independent blockers, none of which is a missing-flag problem:

1. **Capacity.** 1561 GB of weights needs 20 × 80 GB A100 at 100 % occupancy,
   i.e. **32 GPUs (4 nodes)** with any KV at all — 16 is 97.6 GB/GPU, and 24,
   though the bytes divide, is not a constructible TP size (7168 ∤ 24;
   `gcd(96 heads, 7168) = 32`) nor a member of METHODOLOGY §3's topology set
   ([a100.md §0.2, §1.1](./a100.md)). That is already H100's configuration, on a
   slower fabric.
2. **No FP4/FP8 tensor cores.** SM80 has neither. The MXFP4 experts must
   dequantise to BF16/FP16. Marlin's W4A16 kernels do target SM80, but neither
   engine ships or tests an A100 cell.
3. **No FlashMLA.** Both Hopper cells pin `--attention-backend FLASHMLA` /
   `--decode-attention-backend flashmla`; FlashMLA builds for **SM90 and SM100
   only — there is no SM80 target**
   ([FlashMLA requirements](https://github.com/deepseek-ai/FlashMLA): "SM90 /
   SM100", CUDA 12.8+, 12.9+ for SM100). *(Corrected 2026-09-19 from "FlashMLA
   is an SM90 kernel" — it is not Hopper-only; the A100 conclusion is unchanged
   because SM80 is excluded either way.)*

Conclusion: **Kimi-K3 on A100 is unsupported as of 2026-09-19.** Not "slow" —
unsupported. Do not plan for it.

#### RTX PRO 6000 Blackwell — not supported

The RTX PRO 6000 Blackwell has FP4 tensor cores and 96 GB GDDR7, so the
*arithmetic* is a fit, but: (a) no recipe in any engine; (b) 1561 GB ÷ 96 GB =
17 GPUs minimum, and workstation/server SKUs have no NVLink domain of that size
— TP17+ over PCIe for a 93-layer model with per-step all-reduces is not viable;
(c) the vLLM recipes site *does* offer RTX PRO 6000 cells for other models (17
occurrences in the site chrome), so its absence here is a deliberate exclusion,
not an oversight. **Unsupported.**

Specs, **corrected 2026-09-19 sweep — the edition matters.** The page this
document cited,
[NVIDIA's RTX PRO 6000 Blackwell *Workstation* page](https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000/),
was re-fetched: it says "Workstation Edition" twelve times, carries
**1,792 GB/s**, and **contains no Server Edition bandwidth figure at all**. The
part a datacentre would actually rack is the **Server Edition at 1,597 GB/s**
([NVIDIA RTX PRO 6000 Blackwell Server Edition](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/),
pinned in [METHODOLOGY §8](../../METHODOLOGY.md) and
[gpus/rtx6000-pro.md](../../gpus/rtx6000-pro.md) — the Server Edition is 11 %
slower than Workstation). Confirmed on both editions: **96 GB GDDR7 with ECC**,
"4,000 TOPS — theoretical FP4 TOPS **using sparsity**" (**≈ 2,000 dense FP4
TFLOPS**, METHODOLOGY §8), and **no NVLink** on the SKU — which is argument (c)
above, now sourced rather than asserted.

The bandwidth is the other killer: 1.597 TB/s is **5.0× below** B300's 8 TB/s,
so even with capacity solved, one decode step (§6.3: 138.4 GB at batch 1) needs
138.4 GB / (17 × 1.597 TB/s) = **5.1 ms at 100 % MBU**, i.e. ~10 ms at a
realistic MBU, over PCIe all-reduces — `est.`, METHODOLOGY §4. *(Was "1.79 TB/s
… 4.5× below … 4.5 ms", computed from the Workstation figure.)*

### 8.3 vLLM — launch recipes, quoted verbatim

All commands below are the exact `command` / `head_command` strings emitted by
[the recipe JSON API](https://recipes.vllm.ai/moonshotai/Kimi-K3.json).

**B300 1×8 — `recommended_command`** (`single_node_tp`, `docker_image:
vllm/vllm-openai:latest`; env `VLLM_ALLREDUCE_USE_FLASHINFER=1
VLLM_ENGINE_READY_TIMEOUT_S=3600 VLLM_USE_V2_MODEL_RUNNER=1
VLLM_USE_RUST_FRONTEND=1`):

```bash
vllm serve moonshotai/Kimi-K3 \
  --trust-remote-code \
  --gpu-memory-utilization 0.95 \
  --tensor-parallel-size 8 \
  --load-format fastsafetensors \
  --no-enable-flashinfer-autotune \
  --max-model-len 1048576 \
  --kv-cache-dtype fp8 \
  --attention-config '{"use_prefill_query_quantization":true,"mla_prefill_backend":"TOKENSPEED_MLA"}' \
  --enable-prefix-caching \
  --attention-backend TOKENSPEED_MLA \
  --prefix-match-unit 128 \
  --enable-auto-tool-choice \
  --tool-call-parser kimi_k3 \
  --reasoning-parser kimi_k3
```

**H100 4×8 = TP32** (head node; workers identical with `--node-rank {1,2,3}
--headless`). Env adds `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`:

```bash
vllm serve moonshotai/Kimi-K3 \
  --trust-remote-code \
  --tensor-parallel-size 32 \
  --nnodes 4 \
  --node-rank 0 \
  --master-addr $HEAD_IP \
  --gpu-memory-utilization 0.97 \
  --max-num-seqs 5 \
  --max-model-len 32768 \
  --moe-backend marlin \
  --disable-custom-all-reduce \
  --no-enable-flashinfer-autotune \
  --max-num-batched-tokens 4096 \
  --attention-backend FLASHMLA \
  --enable-auto-tool-choice \
  --tool-call-parser kimi_k3 \
  --reasoning-parser kimi_k3
```

**H200 2×8 = TP16** — byte-identical to the H100 command except
`--tensor-parallel-size 16 --nnodes 2`.

**B200 2×8 = TP16 / GB200 4×4 = TP16 / GB300 2×4 = TP8** — all use the Blackwell
override block verbatim (`fastsafetensors`, `--max-model-len 1048576`,
`--kv-cache-dtype fp8`, `TOKENSPEED_MLA` both sides, `--prefix-match-unit 128`)
with the TP size and `--nnodes` adjusted.

**MI355X / MI325X 1×8 = TP8** (`vllm/vllm-openai_rocm:kimi-k3` — *corrected
2026-09-19 from `vllm/vllm-openai-rocm:latest`; the recipe JSON emits the
underscore-and-tag form*; env
`VLLM_ROCM_USE_AITER=1 SAFETENSORS_FAST_GPU=1 VLLM_ROCM_USE_AITER_MOE_SITUV2=1
VLLM_USE_BREAKABLE_CUDAGRAPH=0`):

```bash
vllm serve moonshotai/Kimi-K3 \
  --trust-remote-code \
  --tensor-parallel-size 8 \
  --load-format auto \
  --gpu-memory-utilization 0.95 \
  --mm-encoder-tp-mode data \
  --max-num-seqs 128 \
  --max-num-batched-tokens 4096 \
  --compilation-config '{"cudagraph_mode":"FULL_DECODE_ONLY","custom_ops":["+fused_rms_norm_gated"]}' \
  --enable-auto-tool-choice \
  --tool-call-parser kimi_k3 \
  --reasoning-parser kimi_k3
```

Other vLLM strategies (all on B300 2×8 unless noted):

| Strategy | Distinguishing flags |
|---|---|
| `multi_node_tep` | `--enable-expert-parallel --tensor-parallel-size 16 --nnodes 2` |
| `multi_node_dep` | `--enable-expert-parallel --data-parallel-hybrid-lb --data-parallel-size 16 --data-parallel-size-local 8 --data-parallel-address $HEAD_IP` (+ `--data-parallel-start-rank 8` on the worker). **Unsupported on H100/H200/Ascend.** |
| `multi_node_tp_pp` | `--tensor-parallel-size 8 --pipeline-parallel-size 2 --gpu-memory-utilization 0.90 --max-num-batched-tokens 8192`; env adds `NCCL_CUMEM_ENABLE=1 VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS=1800` |
| `pd_cluster` | prefill: `--kv-transfer-config '{"kv_connector":"NixlConnector","kv_role":"kv_producer","kv_load_failure_policy":"fail"}' --tensor-parallel-size 8 --enable-expert-parallel --max-num-batched-tokens 16384 --no-disable-hybrid-kv-cache-manager`; env `VLLM_SSM_CONV_STATE_LAYOUT=DS VLLM_KIMI_K3_SHARD_SP_SHARED_EXPERT=1 UCX_TLS=rc,cuda_copy VLLM_NIXL_SIDE_CHANNEL_PORT=5557` |
| `kv_store_{centralized,distributed}_mooncake` | appends `--kv-transfer-config '{"kv_connector":"MooncakeStoreConnector","kv_role":"kv_both"}'`; env `MOONCAKE_CONFIG_PATH=$… PYTHONHASHSEED=0` |
| `decode_context_parallelism` (opt-in) | `--decode-context-parallel-size 8 --dcp-comm-backend a2a --attention-backend TOKENSPEED_MLA --attention-config '{"use_prefill_query_quantization":true,"mla_prefill_backend":"TRTLLM_RAGGED"}'` — TP8 single-node only |
| `text_only` (opt-in) | `--language-model-only` |

### 8.4 SGLang — per-platform topologies and flags

Extracted from the cookbook's Deploy-panel cell definitions.

| Platform | Nodes | Low-Latency | Balanced | High-Throughput |
|---|---|---|---|---|
| **B300** | 1×8 | `--tp-size 8 --mem-fraction-static 0.85` | `--tp-size 8 --dcp-size 8 --mem-fraction-static 0.85` | + `--disable-custom-all-reduce` |
| **GB300** | 2×4 | `--tp-size 8` | `--tp-size 8 --dcp-size 8` | same |
| **B200** | 2×8 | `--tp-size 8 --pp-size 2 --disable-flashinfer-autotune --watchdog-timeout 3600` | + `--dcp-size 8 --ep-size 8 --moe-runner-backend flashinfer_mxfp4 --decode-attention-backend cutedsl_mla --chunked-prefill-size 8192` | same as Balanced |
| **GB200** | 4×4 | `--tp-size 16` | `--tp-size 16 --dcp-size 16` | same |
| **H200** | 2×8 (4×8 on HT) | `--tp-size 16 --ep-size 16 --moe-runner-backend marlin --decode-attention-backend flashmla --enable-symm-mem --mem-fraction-static 0.85` | same | `--tp-size 32 --ep-size 32 … --mem-fraction-static 0.90 --mamba-radix-cache-strategy extra_buffer_lazy --dist-timeout 3600` |
| **H100** | 4×8 | `--tp-size 32 --ep-size 32 --moe-runner-backend marlin --decode-attention-backend flashmla --mem-fraction-static 0.85 --dist-timeout 3600` | same | + `--mamba-radix-cache-strategy extra_buffer_lazy` |
| **MI350X / MI355X** | 1×8 | — | `--tp-size 8` ROCm/AITER | — |
| **Ascend A3** | 4×8 (32 cards / 64 dies) | — | `--attention-backend ascend --device npu --quantization modelslim --dtype bfloat16 --tp-size 64 --enable-dp-attention --dp-size 4 --enable-dp-lm-head --mem-fraction-static 0.78 --max-mamba-cache-size 64 --moe-a2a-backend deepep` | PD-mixed Unified only; DSPARK baked in |

Hopper env (both H100 and H200 HT): `SGLANG_K3_ATTN_RES_MODE=jit`,
`SGLANG_MOE_FUSED_GATE_RADIX=1`, `SGLANG_ENABLE_TP_MEMORY_INBALANCE_CHECK=0`,
`PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`, `NCCL_CUMEM_ENABLE=1`.

**Low-HBM VLM profile**, quoted verbatim:
```bash
sglang serve \
  --trust-remote-code \
  --model-path moonshotai/Kimi-K3 \
  --tp-size 8 \
  --context-length 65536 \
  --enable-symm-mem \
  --mem-fraction-static 0.82 \
  --mm-feature-transport cpu \
  --reasoning-parser kimi_k3 \
  --tool-call-parser kimi_k3 \
  --host 0.0.0.0 \
  --port 30000
```

**32-GPU Peak-Throughput preset** (B200/B300 4×8; GB200/GB300 8×4 with
`--nnodes 8`), quoted verbatim:
```bash
SGLANG_OPT_DEEPGEMM_MEGA_MOE_NUM_MAX_TOKENS_PER_RANK=20480 \
sglang serve \
  --trust-remote-code \
  --model-path moonshotai/Kimi-K3 \
  --tp-size 32 --ep-size 32 \
  --enable-dp-attention --dp-size 4 --enable-dp-lm-head \
  --nnodes 4 --node-rank <rank> --dist-init-addr <node0-ip>:20000 \
  --moe-a2a-backend megamoe --moe-runner-backend deep_gemm \
  --kv-cache-dtype fp8_e4m3 \
  --mamba-ssm-dtype bfloat16 \
  --mamba-radix-cache-strategy extra_buffer_lazy \
  --mem-fraction-static 0.92 \
  --reasoning-parser kimi_k3 --tool-call-parser kimi_k3 \
  --host 0.0.0.0 --port 30000
```
Scaling table: 16 GPUs → tp/ep 16, dp 2; 32 → 32/4; 64 → 64/8.

**PD router**, quoted verbatim:
```bash
python3 -m sglang_router.launch_router \
  --pd-disaggregation \
  --prefill http://<prefill-host>:30000 8998 \
  --decode http://<decode-host>:30100 \
  --host 0.0.0.0 --port 8000 \
  --disable-circuit-breaker \
  --health-check-interval-secs 999999
```

### 8.5 TokenSpeed — launch recipes

From [lightseek.org/tokenspeed/recipes/models](https://lightseek.org/tokenspeed/recipes/models):

```bash
# NVIDIA 8x B300, TP8 + EP8 (recommended)
tokenspeed serve moonshotai/Kimi-K3 \
  --served-model-name kimi-k3 --trust-remote-code \
  --max-model-len 32768 --kv-cache-dtype fp8 \
  --tensor-parallel-size 8 --mm-encoder-tp-mode data --ep-size 8 \
  --moe-backend flashinfer_trtllm --gpu-memory-utilization 0.94 \
  --max-num-seqs 32 --disable-kvstore --host 0.0.0.0 --port 8000
```
```bash
# AMD 8x gfx950
tokenspeed serve moonshotai/Kimi-K3 \
  --served-model-name kimi-k3 --trust-remote-code \
  --max-model-len 8192 --kv-cache-dtype fp8 \
  --tensor-parallel-size 8 --mm-encoder-tp-mode data --enable-expert-parallel \
  --attention-backend mla --moe-backend auto --gpu-memory-utilization 0.92 \
  --max-num-seqs 32 --disable-kvstore --host 0.0.0.0 --port 8000
```
Caveats: `pip install flash-linear-attention` required for KDA on NVIDIA; fused
MoE path requires Blackwell, else `--moe-backend triton`; preserve
`media_proc_cfg.in_patch_limit=65536`; max context 32 K (NVIDIA) / 8 K (AMD
current validation); checkpoint has no FP8 KV scales; a separate BF16 cache is
kept for the DSpark draft.

### 8.6 The MXFP4 question, resolved per GPU

**Does the GPU have MXFP4 tensor cores, and if not, what happens?**

| GPU | MXFP4 native | What the engines actually do | Max context in the shipped recipe |
|---|---|---|---|
| **B300 / GB300 / B200 / GB200** (Blackwell) | Yes | **FlashInfer MXFP4 (trtllm-gen SiTU) runner, W4A8.** SGLang: "Leave `--moe-runner-backend` unset on Blackwell: FlashInfer MXFP4 (W4A8, official trtllm-gen SiTU kernels) is selected". vLLM emits `deep_gemm_mega_moe` for cross-node-NVLink DEP. | **1,048,576** |
| **H100 / H200** (Hopper) | **No** | **Marlin W4A16** — `--moe-backend marlin` (vLLM) / `--moe-runner-backend marlin` (SGLang). MXFP4 weights stay packed in HBM and are **dequantised inside the kernel** to BF16 for the MMA. Attention: `FLASHMLA` / `flashmla`. | **32,768** (vLLM Hopper override) |
| **MI355X / MI350X** (CDNA4 gfx950) | Yes (FP4) | **AITER SiTU v2**, two paths: `AITER_SITUV2_A8W4=1` (GU-interleaved preshuffled layout, the shipped default) or `AITER_SITUV2_A4W4=1` (generic separated shuffle). | ⚠️ not pinned in the SGLang cell |
| **MI325X / MI300X** (CDNA3) | No | vLLM emits the same ROCm command; ⚠️ **TO BE VERIFIED** which MoE path runs — `VLLM_ROCM_USE_AITER_MOE_SITUV2=1` is set but the a4w4 FlyDSL path is documented as gfx950-only. MI300X needs 2 nodes regardless. | — |
| **Ascend 910C / A3** | N/A | Separate **W4A8** checkpoint (`Eco-Tech/Kimi-K3-w4a8`, 1.49 TB) + `--quantization modelslim`. | — |

**So: yes, Hopper "must dequantise", and yes, it is implemented — via Marlin.**
The cost shows up not as a missing feature but as a collapsed operating
envelope. The vLLM Hopper override, verbatim:

```
--gpu-memory-utilization 0.97   --max-num-seqs 5   --max-model-len 32768
--moe-backend marlin   --disable-custom-all-reduce   --no-enable-flashinfer-autotune
--max-num-batched-tokens 4096   --attention-backend FLASHMLA
```

`--max-num-seqs 5`. Five concurrent sequences, at 32 K context, on **32 H100s**.
That is the model saying it does not want to be here. The cause is §5.3: at
attnTp = 32 the state is 13.4 MiB/slot (× S=5 = 67 MiB/request), but H100 has
only **0.97 × 80 − 1,560.9/32 = 28.8 GB/GPU** of headroom and needs most of it for
Marlin dequantisation workspace and activation buffers. SGLang's note is blunt:
H100 has the "**least post-weight headroom (80 GB)**".
*(Corrected 2026-09-19 sweep: this read "80 − 1561/32 = 31 GB/GPU of headroom at
0.97 occupancy" — it quoted a 0.97 occupancy but never applied it. 0.97 × 80 =
77.6 GB static, less 48.8 GB of weights = **28.8 GB**.)*

### 8.7 AMD — the ROCm path in detail

SGLang MI35x cell emits `SGLANG_USE_AITER=1 SGLANG_AITER_K3_OPT=1
AITER_FLYDSL_FORCE=1 AITER_SITUV2_A8W4=1`. Knobs on top:

| Env var | Default | Effect |
|---|---|---|
| `AITER_SITUV2_A8W4=1` | unset | SiTU v2 MoE, A8W4 activation quant, GU-interleaved preshuffled weights. **Shipped default.** |
| `AITER_SITUV2_A4W4=1` | unset | A4W4 on the generic separated shuffle layout. "Numerically correct but **slower than A8W4 (530.8 vs 537.3 tok/s median output on 8×MI35x)**." Setting both → A8W4 wins. |
| `SGLANG_K3_KDA_FUSED_BACKEND=aiter` | unset | Fuses `f_b` + conv + recurrent state update + gated RMSNorm into one gfx950 FlyDSL kernel. **Measured: 9.20 → 8.38 µs/layer (−8.9 %) on a 69-layer graph, GSM8K 1319 at 0.950.** |
| `SGLANG_K3_FLYDSL_SOURCE` | `auto` | `sglang` (vendored) or `aiter` |
| `SGLANG_MLA_DECODE_TUNE=1` | — | gfx950 MLA decode geometry |

Requires the `20260903`-or-later ROCm daily image and AITER at/past
`ROCm/aiter#4534` (FlyDSL 0.3.0). The fused KDA backend is fail-closed: it arms
only if the flag is exactly `aiter` **and** the gfx950 FlyDSL kernels import,
and re-validates shapes/dtypes/strides/state-indices/output-buffers on every
decode step, falling back to the stock path rather than erroring.

**vLLM's AMD note contradicts SGLang's default.** vLLM says
`VLLM_ROCM_USE_AITER_MOE_SITUV2=1` "enables AITER's SiTUv2 **a4w4** FlyDSL MoE
path (vllm-project/vllm#53940)" and explicitly warns "**Do not set
`AITER_SITUV2_A8W4=1`**; AITER checks that flag first and it would override
a4w4." SGLang ships A8W4 as the performance default and measures it faster.
⚠️ **TO BE VERIFIED** — these two are giving opposite advice; benchmark both on
your build.

### 8.8 Prefix caching — the `--prefix-match-unit 128` gotcha

This is Kimi-K3-specific and worth reproducing, from the vLLM recipe notes:

> K3 is a hybrid model: its MLA attention layers and KDA (Mamba-like) layers form
> two KV-cache groups under the hybrid KV-cache manager. With prefix caching on,
> vLLM **pads the attention block size up to match the Mamba state page**, so both
> groups resolve to one (large) block size and the default prefix-cache hit
> boundary lands on that block — **very coarse**. The Blackwell profile sets
> `--prefix-match-unit 128`: the inflation formula always yields a multiple of
> 128, so 128 divides whatever block size results, and it aligns with the MLA
> kernel's native block boundary, giving finer prefix-hit granularity.

It is a no-op without prefix caching or a KV connector, and safe under all TP
layouts. **Without it, prefix-cache hit rates on a hybrid model can be far worse
than expected** — and Moonshot's own API reports ">90 % cache hit rate in coding
workloads" ([blog](https://www.kimi.ai/blog/kimi-k3)), so this flag is directly
load-bearing for matching their economics.

SGLang's equivalent is HiCache: "K3's hybrid HiCache tiers the paged MLA KV
**and** the KDA/mamba state across L1 (GPU) / L2 (host) / L3 (Mooncake)". On DCP
recipes, L1+L2 keeps DCP but **L3 drops the DCP flags** (storage keys are not
`dcp_rank`-aware yet), reverting MLA KV to TP-replicated and shrinking
per-request capacity.

### 8.9 Known issues and open caveats

| Issue | Source |
|---|---|
| "K3 occasionally emit a tool-call format its own parser doesn't expect. Suggest to run do schema validation and retry." | vLLM recipe notes |
| `mlx5dv_reg_dmabuf_mr` errno 524 → "NCCL error: unhandled system error". Set `NCCL_DMABUF_ENABLE=0` to fall back to `nvidia_peermem` (must be loaded). | vLLM recipe notes |
| MNNVL (GB200/GB300 NVL): add `NCCL_MNNVL_ENABLE=1 NCCL_CUMEM_ENABLE=1 NCCL_NVLS_ENABLE=1`. | vLLM recipe notes |
| RDMA KV transfer requires `UCX_TLS="rc,cuda_copy"`. | vLLM recipe notes |
| "DeepGEMM MegaMoE is **not compatible with cross-node RDMA**" — NVLink only. | vLLM recipe notes |
| Rust frontend is default; "Switch to Python if you encounter unsupported features or compatibility issues." | vLLM recipe notes |
| SGLang: "Outside the two verified B300 1×8 Unified cells, **no cell has a serving round in this exact shape** — treat those as starting points to verify." | SGLang cookbook |
| SGLang: "**Accuracy has not been re-measured on any cell** — re-measure before you rely on one." | SGLang cookbook |
| SGLang PD: "The positional `8998` after `--prefill` must match `--disaggregation-bootstrap-port`, or **only the decode worker registers**." | SGLang cookbook |
| SGLang PD decode: "Keep `--disaggregation-decode-extra-slots` pinned: unpinned it defaults to twice the batch below 32 requests and **zero above**." | SGLang cookbook |
| SGLang: setting any one of the three attention knobs on Blackwell non-DCP recipes "**cancels the auto-resolution for the others**". | SGLang cookbook |
| SGLang: explicit `tokenspeed_mla` "**force-rewrites `--kv-cache-dtype` to fp8**"; default `cutedsl_mla` serves either. | SGLang cookbook |
| Video/audio input rejected by the open-source processor. | SGLang cookbook |
| Tool calling "Not yet supported on the Ascend A3 Series". | SGLang cookbook |

---

## 9. Available quantised variants

| Repo | Format | Safetensors GB | Validator | Published evals |
|---|---|---:|---|---|
| [`moonshotai/Kimi-K3`](https://huggingface.co/moonshotai/Kimi-K3) | **MXFP4** g32 (QAT), attention/shared/dense/lm_head/vision BF16 | 1560.94 | Moonshot AI | Full model-card suite (§10) |
| [`nvidia/Kimi-K3-NVFP4`](https://huggingface.co/nvidia/Kimi-K3-NVFP4) | ModelOpt `MIXED_PRECISION`: experts **NVFP4** g16 (W4A4), attention **`FP8_PB_WO`**; router + `routed_expert_up/down_proj` left alone | 1609.92 | **NVIDIA** (ModelOpt 0.45.0 ⚠️ **TO BE VERIFIED** — the re-fetched `config.json` carries no producer/ModelOpt version field; the quant scheme itself *is* confirmed: NVFP4 `group_size: 16` W4A4 on `block_sparse_moe.experts`/`mlp.experts` for layers 1–92, `FP8_PB_WO` on attention across all 93 layers) | none in card |
| [`RedHatAI/Kimi-K3-NVFP4`](https://huggingface.co/RedHatAI/Kimi-K3-NVFP4) | **NVFP4** W4A4 on MoE experts only; "attention, shared experts, and other non-quantized layers keep their original precision" | 1646.07 | **Red Hat AI** (LLM Compressor) | **GPQA Diamond 91.0 vs 93.5 → 97.33 % recovery** |
| [`RedHatAI/Kimi-K3-FP8-BLOCK`](https://huggingface.co/RedHatAI/Kimi-K3-FP8-BLOCK) | **FP8** W8A8, block-wise weight scales + dynamic group-wise activation scales; model-free PTQ, no calibration data | 2819.94 | **Red Hat AI** | none in card |
| [`amd/Kimi-K3-Quark-MXFP4-AttnFP8`](https://huggingface.co/amd/Kimi-K3-Quark-MXFP4-AttnFP8) | MXFP4 experts + **FP8 attention** (AMD Quark) | 1505.84 | **AMD** | ⚠️ not fetched |
| `Eco-Tech/Kimi-K3-w4a8` (ModelScope) | **W4A8** for Ascend, 1.49 TB | ~1490 | Ascend ecosystem | — |
| [`unsloth/Kimi-K3-GGUF`](https://huggingface.co/unsloth/Kimi-K3-GGUF) | GGUF (llama.cpp), 494,840 downloads | — | Unsloth (community) | — |
| [`mgoin/Kimi-K3-pruned75`](https://huggingface.co/mgoin/Kimi-K3-pruned75) | expert-pruned | — | community | — |
| `pipenetwork/Kimi-K3-REAP{73,80}-MLX-mxfp4-q8`, `mlx-community/Kimi-K3-mlx-reap160-2bit`, `runrunway/Kimi-K3-REAP-448experts`, `0xTank/Kimi-K3-IQ1S-REAP568-64K-4XSPARKS` | **REAP** expert-pruned + MLX/GGUF 2-bit | — | community | — |
| [`inference-optimization/Kimi-K3-0.40B`](https://huggingface.co/inference-optimization/Kimi-K3-0.40B) + `-MXFP4` | 0.40 B distill/proxy | — | community | — |
| `tiny-random/kimi-k3`, `yujiepan/kimi-k3-tiny-random` | CI smoke-test stubs | — | community | — |

Speculator drafts: see §7.2 (`RadixArk/Kimi-K3-DSpark` 4.50 GB,
`RedHatAI/Kimi-K3-speculator.dspark` 9.49 GB, `lightseekorg/kimi-k3-dspark`,
`lightseekorg/kimi-k3-eagle3-mla`, `modal-labs/Kimi-K3-DFlash`,
`lightseekorg/kimi-k3-dflash2`, `Inferact/Kimi-K3-DSpark{,-Block5}`).

Sizes are HF tree-API sums over `*.safetensors`.

**Engine recognition of variants.** The vLLM recipes index lists three K3
entries: `moonshotai/Kimi-K3`, `RedHatAI/Kimi-K3-NVFP4` and
`Eco-Tech/Kimi-K3-w4a8`, the latter two marked `derived_from:
moonshotai/Kimi-K3` ([models.json](https://recipes.vllm.ai/models.json)). The
RedHatAI NVFP4 recipe is a straight substitution of the model ID into the B300
TP8 command and carries the same `min_vllm_version: 0.27.1` and the same
`meta.hardware` verification set. Its own card, however, notes it "requires
[vllm-project/vllm#50500](https://github.com/vllm-project/vllm/pull/50500)" and
suggests `--load-format instanttensor`. SGLang requires a different image for
NVFP4: `lmsysorg/sglang:dev-dev-kimi-k3-nvfp4`.

**The one real accuracy datapoint:** GPQA Diamond **93.5 → 91.0 (97.33 %
recovery)** for RedHatAI NVFP4. Note that a 2.5-point GPQA drop is *not* small
for a frontier model, and it is the only published quantisation-accuracy
measurement found for any K3 variant. ⚠️ No accuracy data at all for
`nvidia/Kimi-K3-NVFP4`, `RedHatAI/Kimi-K3-FP8-BLOCK`, or the AMD Quark build.

---

## 10. Published benchmarks

### 10.1 Serving throughput and latency — SGLang, B300 1×8 and MI350X 1×8

The only end-to-end serving measurements found. Extracted from the SGLang
cookbook's Deploy-panel data
([source](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3)).

**Conditions, quoted:** "measured on `v0.5.18 @ 71de97b2` with
`--random-range-ratio 1.0`, `--warmup-requests 64`, `--flush-cache`, at
**ISL 8192 / OSL 1024**." MI350X rows are `v0.5.19 @ 12771786`.

**Metric definition — verified, not assumed.** `tokens_per_sec_per_gpu`
reproduces exactly as
`concurrency / (TTFT + OSL × TPOT) × (ISL + OSL) / 8`, i.e. it counts
**input + output** tokens. Checked on all 12 cells; 11 match to ±0.3 %. So I
also give output-only throughput, which is what a $/output-token model needs.

| HW | Strategy | Quant | Spec | Conc | TTFT (ms) | TPOT (ms) | tok/s/GPU (pub.) | **node output tok/s** | tok/s/user |
|---|---|---|---|---:|---:|---:|---:|---:|---:|
| B300 | Low-Latency | MXFP4 | none | 1 | 378 | 8.51 | 127 | 113 | 117.5 |
| B300 | Low-Latency | MXFP4 | DSPARK | 1 | 389 | **2.84** | 351 | 311 | **352.1** |
| B300 | Low-Latency | NVFP4 | none | 1 | 369 | 10.12 | 107 | 95 | 98.8 |
| B300 | Low-Latency | NVFP4 | DSPARK | 1 | 380 | 3.24 | 313 | 277 | 308.6 |
| B300 | Low-Latency | MXFP4 | none | 16 | 3539 | 19.47 | 785 | 698 | 51.4 |
| B300 | Low-Latency | MXFP4 | DSPARK | 16 | 3942 | 9.88 | 1319 | 1165 | 101.2 |
| B300 | Low-Latency | NVFP4 | none | 16 | 3387 | 20.94 | 742 | 660 | 47.8 |
| B300 | Low-Latency | NVFP4 | DSPARK | 16 | 3765 | 9.77 | 1345 | 1190 | 102.4 |
| B300 | Balanced (DCP8) | MXFP4 | none | 64 | 11635 | 40.19 | 1395 | 1241 | 24.9 |
| B300 | Balanced (DCP8) | MXFP4 | DSPARK | 64 | 12038 | 24.47 | **1987** | **1767** | 40.9 |
| B300 | Balanced (DCP8) | NVFP4 | none | 64 | 11063 | 41.55 | 1373 | 1222 | 24.1 |
| B300 | Balanced (DCP8) | NVFP4 | DSPARK | 64 | 11664 | 22.17 | 1946 ⚠️ | 1907 | 45.1 |
| MI350X | Balanced | MXFP4 | none | 1 | 590 | 18.55 | 58 | 52 | 53.9 |
| MI350X | Balanced | MXFP4 | DSPARK | 1 | 604 | 5.39 | 186 | 167 | 185.5 |
| MI350X | Balanced | MXFP4 | none | 16 | 6331 | 32.80 | 462 | 411 | 30.5 |
| MI350X | Balanced | MXFP4 | DSPARK | 16 | 6035 | 15.32 | 864 | 764 | 65.3 |
| MI350X | Balanced | MXFP4 | none | 64 | 18665 | 70.39 | 813 | 723 | 14.2 |
| MI350X | Balanced | MXFP4 | DSPARK | 64 | 23720 | 31.45 | 913 | 812 | 31.8 |

⚠️ The **B300 / Balanced / NVFP4 / DSPARK / conc 64** row is internally
inconsistent: its TTFT+TPOT imply 2,146 tok/s/GPU, but 1,946 is published
(−9.3 %). Every other row reproduces. Treat that cell as suspect.

*(Corrected 2026-09-19: the two MI350X conc-1 "node output tok/s" entries read
55 and 170; recomputing `concurrency / (TTFT + OSL × TPOT) × OSL` from the same
row's TTFT/TPOT gives **52.3** and **167.2**. Every other cell in the column
reproduces to ±0.3 %. The derived ratios in point 3 below use the published
tok/s/GPU column and are unaffected.)*

✅ **RE-VERIFIED 2026-09-19 (sweep) — banner removed.** All **18** cells were
re-extracted from the cookbook's served payload (see §5.1 for the method) and
match this table exactly: hardware, strategy, quant, spec, concurrency,
`ttft_ms`, `tpot_ms`, `tokens_per_sec_per_gpu` and the `sglang_version` stamps
(`v0.5.18 @ 71de97b2` for B300, `v0.5.19 @ 12771786` for MI350X). The table is
now reproducible by a second party with one `curl`. What remains true is that
these are **SGLang's own published measurements**, not independent ones, and
that the DSPARK rows pin acceptance (§7.4).

**What this table says:**

1. **DSPARK is the single largest lever**, worth 1.6–3.4× on TPOT and 1.4–3.2×
   on throughput — but see §7.4's caveat that acceptance was *simulated*.
2. **NVFP4 is slower than MXFP4 without speculation** on B300 (conc 1: 10.12 vs
   8.51 ms TPOT, −16 %; conc 64: 41.55 vs 40.19, −3 %) and roughly a wash with
   it. Given NVFP4 also costs +49 GB on disk (§4) and −2.5 GPQA points (§9),
   **there is no measured case for NVFP4 over the native MXFP4 checkpoint on
   B300 as of 2026-09-19.** That may simply mean the FlashInfer MXFP4 trtllm-gen
   kernels are more mature than the NVFP4 path.
3. **MI350X is roughly half a B300** at matched settings: conc 64 DSPARK 913 vs
   1987 tok/s/GPU (0.46×); conc 1 NOSPEC 58 vs 127 (0.46×). Consistent ratio
   across the sweep. MI355X numbers are not published separately.
4. **TTFT is large and grows almost linearly with concurrency** (378 ms at 1 →
   3.5 s at 16 → 11.6 s at 64 for an 8 K prompt). At 8 K ISL the node is prefill-
   saturated well before decode is. This is the argument for PD disaggregation.

### 10.2 Prefill scaling — GB200 4×4

Quoted from the SGLang cookbook, re-fetched verbatim from the served payload
2026-09-19 (§5.1): "measured on GB200 at ISL 8192 / concurrency 32, PP16 × TP1
reached 4550 prefill tok/s/GPU vs 3596 (PP8 × TP2), 2407 (TEP16), and 1652
(TP16)":

| Shape | Prefill tok/s/GPU |
|---|---:|
| **PP16 × TP1** (deep PP) | **4550** |
| PP8 × TP2 | 3596 |
| PP8 × TP2 (paired with PP2×TP8 decode) | 2919 |
| TEP16 | 2407 |
| TP16 | 1652 |

"Below concurrency ~8 the pipeline cannot fill and **TEP16 leads instead (1947
vs 1227)** — use `--tp-size 16 --ep-size 16` there."

Deep PP wins because "`--tp-size 1` is also what buys context: above TP1 the MLA
KV is replicated across the TP ranks, so TP2 × PP8 holds roughly **half** the
tokens of TP1 × PP16 for the same memory."

### 10.3 Kernel-level measurement — AMD fused KDA decode

"Measured on a 69-layer graph the fused boundary is **9.20 → 8.38 µs/layer
(−8.9 %)**, with GSM8K 1319 at **0.950**."
(`SGLANG_K3_KDA_FUSED_BACKEND=aiter`, gfx950.)
Also: A4W4 vs A8W4 MoE, **530.8 vs 537.3 tok/s median output on 8×MI35x**.
Both quotes re-fetched verbatim from the cookbook payload 2026-09-19 (§5.1).

### 10.4 Large-scale sweep

"The fully data-parallel extreme (`--dp-size` = GPU count, attention-TP 1) — the
shape behind the **64-GPU sweep's ~3K tok/s per GPU** — is not a preset: 288 GB
GPUs only, radix forced off, no head-to-head against the preset shape."

Preset trade, quantified: "Peak Capacity (+DCP8) … Deduplicates the
attention-TP group's MLA KV: **concurrency ceiling +72 % at the same engine
throughput, ~1.8× ITL**."

Moonshot's own guidance: "deploying Kimi K3 on **supernode configurations with
64 or more accelerators**" ([blog](https://www.kimi.ai/blog/kimi-k3)). The blog
also mentions a chip-design case study achieving "over **8,700 tokens/s decode
throughput in simulation**" — *that is a result Kimi-K3 produced as an agent
designing a chip, not a measurement of Kimi-K3's own serving throughput.* Do not
cite it as a K3 inference number.

### 10.5 MLPerf / InferenceMAX / SemiAnalysis

⚠️ **None found.** No MLPerf Inference submission, InferenceMAX entry, or
SemiAnalysis analysis for Kimi-K3 was located. Given the WebSearch outage (see
header note) **this is a negative result from non-exhaustive search, not a
confirmed absence.** Kimi-K3 released 2026-07-27, which is plausibly too recent
for an MLPerf round. See §12.

### 10.6 Quality benchmarks (context, not inference)

Model card headline scores, `reasoning_effort=max`, `temperature=1.0`
([README §3](https://huggingface.co/moonshotai/Kimi-K3/raw/main/README.md)).
Four are mirrored into the repo's machine-readable `.eval_results/*.yaml`:

| Benchmark | Kimi K3 | Best competitor quoted | `.eval_results` |
|---|---:|---|---|
| GPQA Diamond | 93.5 | GPT-5.6 Sol 94.1 | ✔ `gpqa.yaml` 93.5 |
| HLE-Full (no tools / tools) | 43.5 / 56.0 | Claude Fable 5 53.3 / 63.0 | ✔ `hle.yaml` 56 |
| DeepSWE | 67.5 | GPT-5.6 Sol 73.0 | ✔ `deep-swe.yaml` 67.5 |
| Terminal-Bench 2.1 | 88.3 | GPT-5.6 Sol 88.8 | — |
| FrontierSWE | 81.2 | Claude Fable 5 86.6 | — |
| SWE-Marathon | **42.0** | Claude Opus 4.8 40.0 | — |
| BrowseComp | **91.2** | GPT-5.6 Sol 90.4 | — |
| MCPMark-Verified | **94.5** | GPT-5.6 Sol 92.9 | — |
| APEX-Agents | 41.0 | Claude Fable 5 43.3 | ✔ `apex-agents.yaml` 41 |
| Toolathlon-Verified | 76.5 | Claude Fable 5 77.9 | ✔ (dated 2026-08-20) |
| OmniDocBench | **91.1** | Claude Fable 5 89.8 | — |
| Video-MME (w. sub) | **90.0** | GPT-5.6 Sol 89.5 | — |

**One footnote with a direct serving implication:** "BrowseComp. We adopt a
**context-compaction strategy triggered at 300K tokens**. When evaluated with
the full 1M-token context window and no context management, Kimi K3 achieves
**90.4**" (vs 91.2 compacted). Compaction at 300 K *beat* the full 1 M window —
so 1 M context is a capability, not automatically the right operating point,
and serving at 300 K costs far less (§5.4).

---

## 11. Vendor API pricing

**Moonshot AI official**, `kimi-k3` on
[platform.kimi.ai](https://platform.kimi.ai/docs/pricing); the same three
numbers appear in the [launch blog](https://www.kimi.ai/blog/kimi-k3).

| Model | Input (cache **hit**) | Input (cache **miss**) | Output | Context |
|---|---:|---:|---:|---:|
| **kimi-k3** | **$0.30 / MTok** | **$3.00 / MTok** | **$15.00 / MTok** | 1,048,576 |
| kimi-k2.7-code | $0.19 | $0.95 | $4.00 | 262,144 |
| kimi-k2.7-code-highspeed | $0.38 | $1.90 | $8.00 | 262,144 |
| kimi-k2.6 | $0.16 | $0.95 | $4.00 | 262,144 |

Prices exclude tax. No cache-storage fee documented. Cached input is **10.0 % of
uncached** — this is Moonshot's *own published* ratio, which is what
METHODOLOGY §6 requires for a vendor-API comparison (the generic 10 % there is
the separate own-serving default; the two coincide here, which is luck, not a
rule). The same row appears in
[cross-cutting/serving-optimizations.md §1.5](../../cross-cutting/serving-optimizations.md)
(Kimi K3: $3.00 miss / $0.30 hit / $15.00 out, 10× discount, no write fee), and
both pricing URLs were re-fetched 2026-09-19
([/docs/pricing](https://platform.kimi.ai/docs/pricing),
[/docs/pricing/chat-k3](https://platform.kimi.ai/docs/pricing/chat-k3)) —
$0.30 / $3.00 / $15.00 present on both. Access via `platform.kimi.ai` model
`kimi-k3`, with OpenAI- and
Anthropic-compatible endpoints ([README §5](https://huggingface.co/moonshotai/Kimi-K3/raw/main/README.md)).

⚠️ Third-party *API resellers* (Together, Fireworks, DeepInfra, Groq, Baseten,
Novita…) are not enumerated here — they are API prices, which live in
[cross-cutting/serving-optimizations.md §1.5](../../cross-cutting/serving-optimizations.md),
not in this document. One sourced datapoint from that file worth carrying:
OpenRouter reports a **92 % cache hit rate on Kimi K3 traffic**, which makes the
cache-hit column, not the miss column, the realistic one for agentic traffic.
⚠️ **TO BE VERIFIED** — per-reseller K3 list prices.

### 11.1 Cost sanity anchor

Using the measured B300 1×8 cells from §10.1 (ISL 8192 / OSL 1024, so 8:1
input:output — a reasonable agentic mix) and Moonshot list prices. This gives the
**break-even $/GPU-hour** at which self-hosting matches buying the API, at 100 %
utilisation:

| Cell | node input tok/s | node output tok/s | API revenue/h (8 GPUs) | **break-even $/GPU-h** | with 100 % cache hits |
|---|---:|---:|---:|---:|---:|
| Low-Latency, conc 1 | 901 | 113 | $15.81 | **$1.98** | $0.88 |
| Low-Latency, conc 16 | 5,583 | 698 | $97.98 | **$12.25** | $5.46 |
| Balanced (DCP8), conc 64 | 9,932 | 1,241 | $174.30 | **$21.79** | $9.72 |
| Balanced + DSPARK, conc 64 | 14,133 | 1,767 | $248.10 | **$31.01** | $13.83 |

`est.` — derived, not measured; assumes 100 % utilisation, no failures, list
price, and no margin.

**The B300 GPU-hour side is sourced, not open.** From
[cross-cutting/cloud-pricing.md](../../cross-cutting/cloud-pricing.md) (use its
rows only, never a neighbouring GPU's): HGX B300 on-demand runs **$7.40/GPU-h**
(Hyperstack, cheapest neocloud) to **$15.00/GPU-h** (Oracle OCI
`BM.GPU.B300.8`, the only hyperscaler with a public on-demand B300 list price);
AWS `p6-b300.48xlarge` is $142.416/instance-h = **$17.802/GPU-h**; cheapest
committed is DigitalOcean 12-month at **$7.94/GPU-h**. Against those rows:

- **At conc 1 you would need B300 capacity at under $2/GPU-h to break even** —
  a factor of 3.7 below the cheapest published rate ($7.40). Low-latency
  single-stream self-hosting of Kimi-K3 is economically irrational versus the
  API, and no price tier rescues it.
- **At conc 64 NOSPEC the break-even is $21.79/GPU-h**, which already clears
  every published on-demand B300 rate including OCI's $15.00 and AWS's $17.80.
  **With DSPARK it is $31.01** — roughly 2× OCI and 4× Hyperstack. On list
  prices and at full utilisation, self-hosting wins at concurrency 64.
- **Prefix caching moves the break-even by 2.24×** in the wrong direction, and
  that is what decides the case. If your traffic looks like Moonshot's (">90 %
  cache hit rate in coding workloads"), the break-even falls to **$9.72
  (NOSPEC) / $13.83 (DSPARK)** — still above Hyperstack's $7.40, but **below
  OCI's $15.00**. So the honest reading is: self-hosting beats the API at
  conc 64 on neocloud pricing, and loses to it on hyperscaler pricing once the
  customer's traffic is as cacheable as Moonshot's.
- These are **agentic-mix** numbers. Output-heavy traffic (long
  `reasoning_effort=max` chains, low ISL) shifts the balance toward
  self-hosting, because output is 5× the price of uncached input while costing
  the same bytes.

---

## 12. Open questions — consolidated ⚠️ TO BE VERIFIED

**Method-level**

1. **The ≥12 WebSearch queries could not be run** — session budget exhausted by
   sibling agents before this agent's first call. Everything here is from direct
   primary-document fetches. Sections most likely incomplete as a result:
   §10.5 (MLPerf / InferenceMAX / SemiAnalysis), §11 (third-party API pricing),
   §8.1 (TensorRT-LLM and Dynamo status).

**Hardware**

2. ~~**B300 per-GPU HBM capacity: 268 GB or 288 GB?**~~ **ANSWERED 2026-09-19 —
   both, for different SKUs.** HGX B300 / DGX B300 / AWS p6-b300 are
   **268 GB/GPU = 2,144 GB per 8-GPU node** (as-deployed, pinned by
   [METHODOLOGY §8](../../METHODOLOGY.md); NVIDIA's "2.1 TB" nameplate is the
   rounded-down form). GB300 NVL72 is a **different part**: **288 GB/GPU
   physical, ≈ 279 usable**, whose 72-GPU aggregate NVIDIA prints as "20 TB"
   (20 TB ÷ 72 = 277.8 GB, i.e. the *usable* figure — see the correction in
   §5.6; the earlier "20 TB ÷ 72 = 288" was arithmetically wrong even though the
   288 conclusion was right). The two are never merged. §5.6's concurrency
   numbers, which used 268 GB, stand as published except the 1 M row (16 → 15,
   floor).
3. **B300 HBM bandwidth ANSWERED: ≈8 TB/s per GPU** (GB300 NVL72 "up to
   576 TB/s" ÷ 72 —
   [source](https://www.nvidia.com/en-us/data-center/gb300-nvl72/)), so §6.4's
   MBU figures of 25 / 38 / 43 % are no longer conditional. ⚠️ **Still TO BE
   VERIFIED: B200 / GB300 / MI355X per-GPU bandwidth**, and whether the
   reduced-capacity HGX B300 part also runs at the full 8 TB/s.
4. **Dense vs sparse FP4/FP8/BF16 TFLOPS per GPU** — mostly out of scope here
   (cross-cutting GPU docs); §6.1's 47/53 FLOP split is the input they need.
   One datapoint collected in passing, and it is a dense one:
   [HGX B300 / DGX B300](https://www.nvidia.com/en-us/data-center/dgx-b300/)
   quote FP4 Tensor Core **"144 PFLOPS | 108 PFLOPS"** (sparse | dense) for the
   8-GPU system = **13.5 PFLOPS dense FP4 per B300**; HGX B200 is
   "144 | 72 PFLOPS" = **9.0 PFLOPS dense FP4 per B200**. Per METHODOLOGY §7,
   note that the same tables quote FP8 (72 PFLOPS) and BF16 (36 PFLOPS)
   **with sparsity**, so halve them for planning. Spelled out per GPU and
   **dense**, matching the pinned table in
   [METHODOLOGY §8](../../METHODOLOGY.md): HGX B300 **FP4 13,500 / FP8 4,500 /
   BF16 2,250** TFLOPS; B200 **9,000 / 4,500 / 2,250**; GB300 NVL72 — the other
   SKU, §5.6 — **15,000 / 5,000 / 2,500**; MI355X **MXFP4 (and MXFP6) 10,100 /
   FP8 5,000 / BF16 2,500**; H100 and H200 **FP8 1,979 / BF16 989.5, no FP4**;
   RTX PRO 6000 Server Edition **≈ 2,000 dense FP4** (4,000 is the sparse
   number). Never quote the sparse column.
5. **MI300X / MI325X (CDNA3) MoE path.** vLLM ships a recipe with
   `VLLM_ROCM_USE_AITER_MOE_SITUV2=1`, but the a4w4 FlyDSL path is documented as
   gfx950-only. What actually runs on CDNA3? Not in SGLang's supported list.

**Engines**

6. ~~**TensorRT-LLM: any Kimi-K3 support?**~~ **ANSWERED 2026-09-19: yes.** The
   TRT-LLM supported-models matrix lists `KimiK3ForConditionalGeneration` and
   `KimiLinearForCausalLM` with `moonshotai/Kimi-K3` as the example, restricted
   to **Blackwell `SM100` only**
   ([source](https://nvidia.github.io/TensorRT-LLM/models/supported-models.html)).
   The premise that it was absent from every matrix was wrong; only the model
   card and the two *other* engines omit it. Remaining open sub-question:
   ⚠️ **TO BE VERIFIED** — no TRT-LLM *performance* numbers or launch recipe for
   K3 were located, so this is a support claim, not a throughput claim.
   Separately, `TRTLLM_RAGGED` and `trtllm_mla`/`trtllm_mha` in the vLLM/SGLang
   recipes remain *kernel backends inside* those engines, which is still not the
   same as TRT-LLM serving the model.
7. **NVIDIA Dynamo: any Kimi-K3 recipe?** Not found. vLLM's PD and KV-store
   strategies use NIXL and Mooncake directly.
8. **vLLM vs SGLang contradict each other on the AMD MoE path** (§8.7): vLLM says
   set `VLLM_ROCM_USE_AITER_MOE_SITUV2=1` for a4w4 and explicitly *not*
   `AITER_SITUV2_A8W4=1`; SGLang ships A8W4 as the default and measures it
   faster (537.3 vs 530.8 tok/s). Benchmark both.
9. **MI355X has no published serving numbers** — only MI350X. The cookbook
   treats them as one cell.
10. **H100/H200 have no published throughput at all.** Only topology and flags.
    Given `--max-num-seqs 5` and `--max-model-len 32768`, a Hopper deployment
    needs its own measurement before anyone commits 32 GPUs to it.
11. **Max context on MI350X/MI355X is not pinned** in the SGLang cell; TokenSpeed
    caps AMD at 8,192 ("current validation").
12. **What does `--moe-backend marlin` actually cost on Hopper?** Marlin's W4A16
    dequant-in-kernel throughput for MXFP4 g32 specifically (vs its usual INT4
    g128) is unmeasured here.

**Model / checkpoint**

13. **Is "Quantile Balancing" inference-relevant?** The blog names it; the
    checkpoint exposes only the standard `e_score_correction_bias`, suggesting
    training-time only. Confirm from the tech report
    (`github.com/MoonshotAI/Kimi-K3/blob/main/k3_tech_report.pdf`, HTTP 200,
    **not parsed** — PDF).
14. **Per-Head Muon, SiTU exact form, AttnRes exact formulation** — named in the
    blog, defined only in the tech report (not parsed).
15. **`attn_res_block_size: 12`** — what does 12 index over? 93 layers is not a
    multiple of 12.
16. **Video support.** The preprocessor has full video config
    (`sample_fps: 8.0`, `temporal_merge_kernel_size: 4`,
    `in_patch_limit_video: 655360`) and the card reports Video-MME 90.0, but
    SGLang says the open serving contract "rejects video and audio input". Is
    video available only through the hosted API?
17. **Accuracy of the MXFP4 checkpoint vs an unquantised reference.** QAT from
    the SFT stage means no BF16 reference exists. The only quantisation-accuracy
    datapoint found is RedHatAI NVFP4's GPQA 91.0 vs 93.5 — and 93.5 is itself
    the MXFP4 model.
18. **No evals for** `nvidia/Kimi-K3-NVFP4`, `RedHatAI/Kimi-K3-FP8-BLOCK`,
    `amd/Kimi-K3-Quark-MXFP4-AttnFP8`.

**Speculative decoding**

19. **No measured DSPARK acceptance rate for any real serving workload.**
    SGLang's published speedups pin `SGLANG_SIMULATE_ACC_LEN=4.5`. RadixArk's
    acc_len table is offline/static evaluation. Measure NOSPEC vs DSPARK on your
    own traffic before committing.
20. **"DFLASH has no published draft checkpoint"** (SGLang) yet
    `modal-labs/Kimi-K3-DFlash` and `lightseekorg/kimi-k3-dflash2` exist. Which
    is authoritative?
21. **EAGLE-3:** `lightseekorg/kimi-k3-eagle3-mla` exists; no evals, no engine
    recipe found.
22. **The two DSPARK drafts use different aux-layer sets** (`[7,23,51,67,83]` vs
    `[24,48,72,88,92]`) and different block sizes (7 vs 8). They are not
    interchangeable between engines.

**Benchmarks**

23. **The B300 / Balanced / NVFP4 / DSPARK / conc 64 cell is internally
    inconsistent** (−9.3 % vs its own TTFT/TPOT). Reported upstream? Unknown.
24. **No published numbers at 32 K / 128 K / 1 M context** for any hardware.
    Every serving measurement found is ISL 8192 / OSL 1024. The METHODOLOGY §4
    grid (4K/32K/128K × batch 1–256) cannot be filled from published data — only
    modelled, as in §5.6 and §6.3.
25. **No published numbers for B200, GB200, GB300, H100, H200, MI355X, Ascend**
    at all beyond the GB200 prefill-shape comparison (§10.2).

---

## Sources

Every URL fetched for this document.

**Model repository (moonshotai/Kimi-K3)**
- https://huggingface.co/moonshotai/Kimi-K3
- https://huggingface.co/api/models/moonshotai/Kimi-K3
- https://huggingface.co/api/models/moonshotai/Kimi-K3/tree/main?recursive=true
- https://huggingface.co/moonshotai/Kimi-K3/raw/main/README.md
- https://huggingface.co/moonshotai/Kimi-K3/raw/main/config.json
- https://huggingface.co/moonshotai/Kimi-K3/raw/main/generation_config.json
- https://huggingface.co/moonshotai/Kimi-K3/raw/main/tokenizer_config.json
- https://huggingface.co/moonshotai/Kimi-K3/raw/main/preprocessor_config.json
- https://huggingface.co/moonshotai/Kimi-K3/raw/main/LICENSE
- https://huggingface.co/moonshotai/Kimi-K3/raw/main/configuration_kimi_k3.py
- https://huggingface.co/moonshotai/Kimi-K3/raw/main/modeling_kimi_linear.py
- https://huggingface.co/moonshotai/Kimi-K3/raw/main/encoding_k3.py
- https://huggingface.co/moonshotai/Kimi-K3/raw/main/.eval_results/{moonshotai__Kimi-K3,gpqa,hle,deep-swe,apex-agents}.yaml
- https://huggingface.co/moonshotai/Kimi-K3/resolve/main/model-{00001..00096}-of-000096.safetensors (HTTP Range headers only — tensor dtype/shape inventory)

**Moonshot AI**
- https://www.kimi.ai/blog/kimi-k3 (302 from https://www.kimi.com/blog/kimi-k3)
- https://platform.kimi.ai/docs/pricing
- https://platform.kimi.ai/docs/guide/kimi-k3-quickstart (referenced by the card)
- https://github.com/MoonshotAI/Kimi-K3 (HTTP 200, not parsed)
- https://github.com/MoonshotAI/Kimi-K3/blob/main/k3_tech_report.pdf (HTTP 200, **not parsed**)

**vLLM**
- https://recipes.vllm.ai/moonshotai/Kimi-K3
- https://recipes.vllm.ai/moonshotai/Kimi-K3.json
- https://recipes.vllm.ai/moonshotai/Kimi-K3/hw/{b300,h100,h200,b200,gb200,gb300,mi300x,mi325x,mi355x}.json
- https://recipes.vllm.ai/moonshotai/Kimi-K3/strategies/{multi_node_tp,multi_node_tep,multi_node_tp_pp,multi_node_dep,pd_cluster,kv_store_centralized_mooncake,kv_store_distributed_mooncake}.json
- https://recipes.vllm.ai/moonshotai/Kimi-K3/hw/{a100,rtx_pro_6000,rtx6000,l40s,gh200}.json → **404** (negative result)
- https://recipes.vllm.ai/RedHatAI/Kimi-K3-NVFP4.json
- https://recipes.vllm.ai/models.json
- https://docs.vllm.ai/projects/ascend/en/v0.23.0/tutorials/models/Kimi-K3.html (HTTP 200)
- https://github.com/vllm-project/vllm/pull/50500 (referenced)
- https://github.com/vllm-project/vllm/pull/53940 (referenced)

**SGLang**
- https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3
- https://github.com/sgl-project/SpecForge/ (referenced)

**TokenSpeed**
- https://lightseek.org/tokenspeed/recipes/models
- https://github.com/lightseekorg/tokenspeed (referenced)

**Quantised variants and speculators**
- https://huggingface.co/api/models/nvidia/Kimi-K3-NVFP4
- https://huggingface.co/nvidia/Kimi-K3-NVFP4/raw/main/config.json
- https://huggingface.co/RedHatAI/Kimi-K3-NVFP4/raw/main/README.md
- https://huggingface.co/RedHatAI/Kimi-K3-FP8-BLOCK/raw/main/README.md
- https://huggingface.co/api/models/RedHatAI/Kimi-K3-speculator.dspark
- https://huggingface.co/RedHatAI/Kimi-K3-speculator.dspark/raw/main/README.md
- https://huggingface.co/RedHatAI/Kimi-K3-speculator.dspark/raw/main/config.json
- https://huggingface.co/RadixArk/Kimi-K3-DSpark/raw/main/README.md
- https://huggingface.co/RadixArk/Kimi-K3-DSpark/raw/main/config.json
- https://huggingface.co/api/models/{nvidia/Kimi-K3-NVFP4,RedHatAI/Kimi-K3-FP8-BLOCK,RedHatAI/Kimi-K3-NVFP4,amd/Kimi-K3-Quark-MXFP4-AttnFP8,RedHatAI/Kimi-K3-speculator.dspark,RadixArk/Kimi-K3-DSpark}/tree/main?recursive=true
- https://huggingface.co/api/models?search=Kimi-K3&limit=60&sort=downloads (variant census)
- https://www.modelscope.cn/models/Eco-Tech/Kimi-K3-w4a8 (HTTP 200, not parsed)

**Fetched by the 2026-09-19 fact-check pass** (new to this document)
- https://www.nvidia.com/en-us/data-center/hgx/ (HGX B300 / B200 memory + FP4 dense)
- https://www.nvidia.com/en-us/data-center/dgx-b300/ (DGX B300, 2.1 TB / 8 GPUs)
- https://www.nvidia.com/en-us/data-center/gb300-nvl72/ (20 TB / 72, 576 TB/s)
- https://www.nvidia.com/en-us/data-center/h200/ (141 GB, 4.8 TB/s)
- https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000/
- https://rocm.docs.amd.com/en/latest/reference/gpu-arch-specs.html (MI355X/MI350X/MI325X/MI300X)
- https://nvidia.github.io/TensorRT-LLM/models/supported-models.html (**K3 is supported, SM100 only**)
- https://github.com/deepseek-ai/FlashMLA (SM90 **and** SM100)
- https://huggingface.co/api/models/moonshotai/Kimi-K3?expand[]=safetensors (dtype param census)

**Fetched by the 2026-09-19 systemic sweep** (new to this document)
- https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3 (**served HTML payload** — calculator source, `config.cells`, all 18 speed cells, `supportedHardware`; resolves verification-log row 18)
- https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/ (Server Edition **1,597 GB/s**)
- https://platform.kimi.ai/docs/pricing/chat-k3 ($0.30 / $3.00 / $15.00, second page)
- https://api.github.com/repos/{vllm-project/vllm,sgl-project/sglang,NVIDIA/TensorRT-LLM}/releases/latest (v0.29.0 2026-09-09 · v0.5.20 2026-09-18 · v1.2.1)
- Re-fetched for citation integrity: https://recipes.vllm.ai/moonshotai/Kimi-K3/hw/b300.json (`vram_gb: 2144`, "268 GB HBM3e"), https://huggingface.co/api/models/moonshotai/Kimi-K3?expand[]=safetensors (2,779,931,837,184), https://nvidia.github.io/TensorRT-LLM/models/supported-models.html (`KimiK3ForConditionalGeneration`, SM100 only), https://www.nvidia.com/en-us/data-center/gb300-nvl72/ (20 TB, 576 TB/s), https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000/ (**Workstation** Edition — the mis-cited page)

**Sibling documents this sweep reconciles against**
- [`research/METHODOLOGY.md`](../../METHODOLOGY.md) §1–§3, §6, §8
- [`research/cross-cutting/cloud-pricing.md`](../../cross-cutting/cloud-pricing.md) (B300 $/GPU-h rows)
- [`research/cross-cutting/serving-optimizations.md`](../../cross-cutting/serving-optimizations.md) §1.5 (cached-input economics)
- [`research/cross-cutting/inference-engines.md`](../../cross-cutting/inference-engines.md) §2.1–§2.3 (engine releases)
- [`research/gpus/rtx6000-pro.md`](../../gpus/rtx6000-pro.md), [`research/gpus/b300.md`](../../gpus/b300.md)

**Referenced by the model card but not fetched**
- https://github.com/fla-org/flash-linear-attention (`fla.ops.kda`)
- https://github.com/kvcache-ai/Mooncake
- https://github.com/vllm-project/speculators
- https://github.com/vllm-project/llm-compressor

---

## Verification log (2026-09-19)

Adversarial re-check. Method: every claim below was tested against a **freshly
fetched primary source located independently** (the document's own citation was
not trusted until opened), or **recomputed with `python3` from
`research/models/kimik3/config.json` + [`METHODOLOGY.md`](../../METHODOLOGY.md)**.
`WebSearch` was again unavailable (session budget 200/200 exhausted, same as the
original run), so discovery was by direct URL; `WebFetch` worked throughout.

**Verdict counts: 26 CONFIRMED · 9 CORRECTED · 5 UNVERIFIABLE.**

### Hardware

| # | Claim | Verdict | Source |
|---:|---|---|---|
| 1 | B300 per-GPU HBM = 268 GB, 8-GPU node = 2144 GB (§5.6, §8.2) | **CONFIRMED** for HGX/DGX B300 — "Total GPU Memory: 2.1 TB" over 8× Blackwell Ultra SXM. The competing 288 GB is **also correct, for GB300 NVL72** (20 TB ÷ 72). The §5.6 ⚠️ is now resolved, not removed. *[2026-09-19 sweep: the parenthetical arithmetic is wrong — 20 TB ÷ 72 = **277.8 GB**, NVIDIA's rounded **usable** figure. GB300 NVL72 is **288 GB physical / ≈ 279 usable** per METHODOLOGY §8. Verdict unchanged.]* | https://www.nvidia.com/en-us/data-center/hgx/ · https://www.nvidia.com/en-us/data-center/dgx-b300/ · https://www.nvidia.com/en-us/data-center/gb300-nvl72/ |
| 2 | B300 per-GPU HBM bandwidth assumed 8 TB/s (§6.4) | **CONFIRMED** — GB300 NVL72 "up to 576 TB/s" ÷ 72 = 8.0 TB/s/GPU. §6.4's MBU 25/38/43 % is no longer conditional. | https://www.nvidia.com/en-us/data-center/gb300-nvl72/ |
| 3 | B200 = 180 GB (§8.2) | **CONFIRMED** — HGX B200 "Total GPU memory 1.4 TB" ÷ 8 = 180 GB. | https://www.nvidia.com/en-us/data-center/hgx/ |
| 4 | H200 = 141 GB (§8.2) | **CONFIRMED** — H200 SXM "141GB", 4.8 TB/s. | https://www.nvidia.com/en-us/data-center/h200/ |
| 5 | H100 = 80 GB, 640 GB/node (§8.2, §8.6) | **CONFIRMED** — vLLM h100 profile: `vram_gb 640`, "NVIDIA H100 80GB NVLink (8-GPU node)". | https://recipes.vllm.ai/moonshotai/Kimi-K3/hw/h100.json |
| 6 | MI355X = 288 GB; MI325X = 256 GB; MI300X = 192 GB (§8.2) | **CONFIRMED** — ROCm arch-spec table gives 288 / 288 / 256 / 192 **GiB** for MI355X / MI350X / MI325X / MI300X. | https://rocm.docs.amd.com/en/latest/reference/gpu-arch-specs.html |
| 7 | RTX PRO 6000 Blackwell has FP4 tensor cores, 96 GB GDDR7, and no NVLink domain (§8.2) | **CONFIRMED**, and extended: 96 GB GDDR7 ECC, **1,792 GB/s**, "4,000 TOPS — theoretical FP4 TOPS using sparsity" (≈2,000 dense), no NVLink on the SKU. *[2026-09-19 sweep — **wrong edition**: that URL is the **Workstation Edition** page ("Workstation Edition" ×12) and carries no Server Edition figure. The server part is **1,597 GB/s** (METHODOLOGY §8, gpus/rtx6000-pro.md). §8.2 recomputed; 96 GB / no-NVLink / ≈2,000 dense FP4 stand.]* | https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000/ · https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/ |
| 8 | "GB300 **NVL4**" tray SKU (§5.6, §8.2) | **UNVERIFIABLE** — the 288 GB/GPU figure is confirmed, but no NVIDIA page fetched names a GB300 NVL4; NVIDIA documents GB300 as **NVL72**. ⚠️ marker added. | https://www.nvidia.com/en-us/data-center/gb300-nvl72/ |
| 9 | Dense FP4 TFLOPS per Blackwell GPU (§12 Q4, previously deferred) | **CONFIRMED (new data)** — DGX/HGX B300 FP4 "144 PFLOPS \| 108 PFLOPS" sparse\|dense ÷ 8 = **13.5 PFLOPS dense/B300**; HGX B200 "144 \| 72" ÷ 8 = **9.0 PFLOPS dense/B200**. Their FP8 (72) and BF16 (36) rows are **sparse** — halve per METHODOLOGY §7. | https://www.nvidia.com/en-us/data-center/dgx-b300/ · https://www.nvidia.com/en-us/data-center/hgx/ |

### Kernels, engines, format support

| # | Claim | Verdict | Source |
|---:|---|---|---|
| 10 | "TensorRT-LLM: **no Kimi-K3 support found**" (§8.1, §12 Q6) | **CORRECTED** — *no support* → **supported**. The TRT-LLM matrix lists `KimiK3ForConditionalGeneration` ("Kimi-K3", example `moonshotai/Kimi-K3`) and `KimiLinearForCausalLM` ("Kimi-K3 (text decoder)"), with "Kimi K3 is only supported on NVIDIA Blackwell GPUs (`SM100` family)". **Most consequential error in the document**: it removed a third Blackwell serving path from the engine matrix and left §12 Q6 open. | https://nvidia.github.io/TensorRT-LLM/models/supported-models.html |
| 11 | "FlashMLA is an **SM90** kernel" (§8.2, A100 blocker #3) | **CORRECTED** — SM90 → **SM90 and SM100** (CUDA 12.8+, 12.9+ for SM100); dense decode is SM90-only, dense prefill SM100-only. The A100/SM80 conclusion is unchanged. | https://github.com/deepseek-ai/FlashMLA |
| 12 | vLLM CUDA image `vllm/vllm-openai:kimi-k3` (§8.1) | **CORRECTED** — → **`vllm/vllm-openai:latest`**. The recipe JSON emits `latest`, and §8.3 of this same document already quoted `latest`, so §8.1 contradicted §8.3. | https://recipes.vllm.ai/moonshotai/Kimi-K3.json |
| 13 | vLLM ROCm image `vllm/vllm-openai-rocm:latest` (§8.3) | **CORRECTED** — → **`vllm/vllm-openai_rocm:kimi-k3`** (underscore, pinned tag), which is what the recipe emits and what §8.1 already stated. | https://recipes.vllm.ai/moonshotai/Kimi-K3.json |
| 14 | vLLM `min_vllm_version 0.27.1`, `date_added 2026-07-27`, H100 absent from `meta.hardware` (§1, §8.1, §8.2) | **CONFIRMED** — all three; `meta.hardware` = H200, B200, B300, GB200, GB300, MI355X, Ascend 910C. | https://recipes.vllm.ai/moonshotai/Kimi-K3.json |
| 15 | B300 recipe: TP8, 1 node, `--max-model-len 1048576`, `--kv-cache-dtype fp8`, `TOKENSPEED_MLA`, prefix caching (§8.3, §8.6) | **CONFIRMED** verbatim, incl. `vram_gb: 2144` and "NVIDIA B300 SXM 268 GB HBM3e · 8-GPU HGX B300 node (Blackwell Ultra)". | https://recipes.vllm.ai/moonshotai/Kimi-K3/hw/b300.json |
| 16 | Hopper override: TP32 / 4 nodes / `--max-num-seqs 5` / `--max-model-len 32768` / `--moe-backend marlin` / `FLASHMLA` / `--gpu-memory-utilization 0.97` (§8.6 — the "model saying it does not want to be here" claim) | **CONFIRMED** verbatim, every flag. | https://recipes.vllm.ai/moonshotai/Kimi-K3/hw/h100.json |
| 17 | RTX PRO 6000 has no vLLM recipe (HTTP 404) (§8.2) | **CONFIRMED** — `/hw/rtx_pro_6000.json` returns HTTP 404. | https://recipes.vllm.ai/moonshotai/Kimi-K3/hw/rtx_pro_6000.json |
| 18 | All SGLang-sourced numbers: the JS sizing calculator (§5.1), slot table (§5.3), 101/68/91/60 caps (§5.6), Deploy-panel flags (§8.4), the entire §10.1 serving table, §10.2 GB200 prefill, §10.3 AMD kernel timings, "+72 %" DCP (§10.4) | **UNVERIFIABLE** — the cookbook URL resolves and does contain a `KimiK3MambaRatioCalculator` component (so the calculator is real), but it is a client-rendered React configurator whose data payload is absent from the served HTML; neither the page nor its `.md` variant exposes any of these figures. **Not contradicted — simply not reproducible by a second party.** ⚠️ markers added at §5.1 and §10.1. *[**SUPERSEDED 2026-09-19 sweep → CONFIRMED.** The payload is in the served HTML, just not in the rendered text: `curl -sL <URL>` yields 968 KB containing the calculator source, `config.cells`, `supportedHardware` and all 18 speed cells verbatim. Re-extracted and matched character for character; the ⚠️ markers at §5.1 and §10.1 are removed.]* | https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3 |

### Model, checkpoint, quantisation

| # | Claim | Verdict | Source |
|---:|---|---|---|
| 19 | Parameter census: total **2,779,931,837,184**, BF16 57,179,884,544, F32 11,122,432, MXFP4-packed 2,722,740,830,208 (§3.1) | **CONFIRMED to the digit** against HF's own safetensors index — and the doc's `nelem × 2` unpacking reconciles exactly: packed bytes 1,361,370,415,104 × 2 = 2,722,740,830,208; + E8M0 scales 85,085,650,944 + BF16 114,359,769,088 + F32 44,489,728 = 1,560,860,324,864 B, matching the 1,560,936,091,448 B shard total less headers. | https://huggingface.co/api/models/moonshotai/Kimi-K3?expand[]=safetensors |
| 20 | Active params **104,189,532,416** and the 46.7 % / 53.3 % MXFP4-vs-BF16 split (§3.3, §3.4, §6.1) | **CONFIRMED** — recomputed from `config.json`: `exp` 33,030,144 · `kda_layer` 267,579,648 · `mla_layer` 56,035,328 · `g_plus_o` 176,160,768 · `moe_layer` 718,409,216 · `dense0` 726,663,168 → 69·kda + 24·mla + 93·gpo = 36,190,795,008; +92·moe +dense0 +norms = 103,015,127,296; +lm_head = **104,189,532,416**. MXFP4 active 48,620,371,968 = **46.67 %**. Model card's "104B" ✔. | local `config.json` + `python3` |
| 21 | §3.2 per-component table "(delta vs total: **0**)" | **CORRECTED** — rows sum to **2,779,931,754,752**; delta **82,432**, being the 92 F32 `gate.e_score_correction_bias` vectors excluded by the router row's `92 × 896 × 7168` formula. | `python3` recompute |
| 22 | §3.5 "Total non-MXFP4 **57,190,766,592**" | **CORRECTED** → **57,191,006,976**, and the unmatched bucket 6,496,451,328 → **6,496,691,712**. The old figures were 240,384 short of §3.1's own BF16 + F32 sum. | `python3` recompute |
| 23 | Model card headline claims: 2.8 T total, 104 B activated, 401 M vision encoder, 1,048,576 context, "MXFP4 weights / MXFP8 activations" QAT, engines = vLLM/SGLang/TokenSpeed, Modality "Text, Image" (§1, §3, §8.1, §2.6) | **CONFIRMED** — all seven, verbatim. | https://huggingface.co/moonshotai/Kimi-K3/raw/main/README.md |
| 24 | 93 layers = 69 KDA + 24 full, 1-based indices, last **two** layers both full attention, `num_nextn_predict_layers: 0` (§2.2, §2.3, §7.1) | **CONFIRMED** — `full_attn_layers` and `kda_layers` partition 1…93 with no overlap and no gap; tail is [84, 88, **92, 93**] → 0-based [83, 87, **91, 92**], i.e. adjacent final layers. | local `config.json` |
| 25 | RedHatAI NVFP4 accuracy: GPQA Diamond 93.5 → 91.0, 97.33 % recovery; W4A4 on MoE experts only (§9) | **CONFIRMED** — card states exactly this, plus the `vllm#50500` requirement and `--load-format instanttensor`. 91.0/93.5 = 97.326 % ✔. | https://huggingface.co/RedHatAI/Kimi-K3-NVFP4/raw/main/README.md |
| 26 | `nvidia/Kimi-K3-NVFP4` scheme: NVFP4 g16 experts + `FP8_PB_WO` attention (§9) | **CONFIRMED** — group_0 = 4-bit float, `group_size 16`, W4A4 on `block_sparse_moe.experts`/`mlp.experts` layers 1–92; group_1 = `FP8_PB_WO` on attention, all 93 layers. | https://huggingface.co/nvidia/Kimi-K3-NVFP4/raw/main/config.json |
| 27 | "ModelOpt **0.45.0**" as the validator version (§9) | **UNVERIFIABLE** — the re-fetched `config.json` carries no producer or ModelOpt version field. ⚠️ added; the *scheme* is confirmed (row 26). | https://huggingface.co/nvidia/Kimi-K3-NVFP4/raw/main/config.json |
| 28 | Weight-memory table (§4): MXFP4 @ 0.53125 B/param → experts 1446.46 GB, non-expert 114.40 GB, total **1560.86 GB**; NVFP4 @ 0.5625 → 1531.54 GB; all-BF16 5559.89 GB; dequant cost **3.56×** | **CONFIRMED** — every cell recomputed and matches to the stated precision; 5559.89 / 1560.86 = 3.562. | `python3` recompute |

### KV, state, throughput, cost

| # | Claim | Verdict | Source |
|---:|---|---|---|
| 29 | `kv_bytes_per_token` = 24 × 576 × B → 27,648 B (BF16) / 13,824 B (FP8) (§5.2) | **CONFIRMED** — METHODOLOGY §2 MLA row applied to `kv_lora_rank 512` + `qk_rope_head_dim 64` over the 24 full-attention layers. | `config.json` + METHODOLOGY §2 |
| 30 | KDA state/slot = 449,372,160 B float32 (428.6 MiB) / 232,316,928 B bf16 (221.6 MiB); attnTp8 → 56,171,520 B (53.6 MiB) (§5.3, §5.6) | **CONFIRMED** — 69 × (96·128·128·4 + 9·96·128·2) and the ÷8 head-split both reproduce exactly. | `python3` recompute |
| 31 | §5.4 crossover "below ~**78 K tokens** the state costs more than the KV cache" | **CORRECTED** → **~163 K at FP8 KV** (2,246,860,800 / 13,824 = 162,533) or **~81 K at BF16 KV** (÷ 27,648 = 81,267). The table's columns are FP8, so 163 K is the matching figure. 78 K matched neither. The "95 % at 8 K" claim in the same paragraph **is** correct (2142.8/2250.8). | `python3` recompute |
| 32 | §5.6 concurrency estimates 82 / 110 / 96 / 64 / 174 / 101 / 16, and the "+9 % vs measured 101" validation | **CONFIRMED** as arithmetic — all reproduce from 268 GB × mem-frac − 195.1 GB weights, per GPU. One cell corrected: the 1 M / DCP8 per-request bill reads 2,008 MiB, recomputes to **1,996 MiB** (5 × 53.57 + 1728); max concurrency 16 unchanged. The *measured* 101/68/91/60 it is validated against is row 18 (UNVERIFIABLE). *[2026-09-19 sweep: the 1 M cap is **15**, not 16 — 32.6875e9 / 2,092,796,928 = 15.62, and METHODOLOGY §3 floors. And row 18 is now CONFIRMED, so the 101/68/91/60 validation is sourced.]* | `python3` recompute |
| 33 | §6.2 prefill attention FLOPs (0.05 / 0.79 / 12.67 / 810.65 PFLOP) and "KDA removes **74 %** of quadratic attention FLOPs" | **CONFIRMED** — `24 × 96 × 320 × T²` reproduces every row; 1 − 24/93 = 74.2 %; the 93-layer counterfactual 810.65 × 93/24 = 3141.3 ✔; 1 M total 1.03 EFLOP ✔. | `python3` recompute |
| 34 | §6.3 decode byte table (137.9 / 306.2 / 745.9 / 1102.0 / 1414.4 / 1544.2 GB) and "81 % of batch-1 bytes are BF16 non-expert" | **CONFIRMED** — `896 × (1 − (1 − 16/896)^b)` × 92 × 17.547 MB + 112.1 GB reproduces every row to ±0.1 GB; 112.1/137.9 = 81.3 %. *[2026-09-19 sweep: confirmed for the **weights** term, but the table was missing METHODOLOGY §4's `Σ_seq kv_read(seq_ctx)` entirely — no MLA-KV and no KDA-state bytes. §6.3 now carries both at ctx 8 K and §6.4's implied bandwidths were recomputed.]* | `python3` recompute + METHODOLOGY §4 |
| 35 | §10.1 `tokens_per_sec_per_gpu` identity and the derived "node output tok/s" column | **CORRECTED (2 cells)** — the identity `conc/(TTFT + OSL·TPOT) × (ISL+OSL)/8` reproduces on 17 of 18 rows (the 18th being the B300 NVFP4/DSPARK/conc-64 cell the document already flags at −9.3 %, which I independently reproduce: implied 2,146 vs published 1,946). But the derived "node output tok/s" column read **55** and **170** on the two MI350X conc-1 rows where the same rows' TTFT/TPOT give **52.3** and **167.2**. Fixed. | `python3` recompute |
| 36 | §11 Moonshot pricing: $0.30 cache-hit / $3.00 cache-miss input / $15.00 output, 1,048,576 ctx (and the k2.7/k2.6 rows) | **CONFIRMED** on **two** independent pages — the pricing docs and the launch blog ("Pricing is $0.30/MTok for cache-hit input, $3.00/MTok for cache-miss input, and $15.00/MTok for output"). Cached input = exactly 10.0 % of uncached, matching METHODOLOGY §6's default. | https://platform.kimi.ai/docs/pricing · https://www.kimi.ai/blog/kimi-k3 |
| 37 | §11.1 break-even $/GPU-h: $1.98 / $12.25 / $21.79 / $31.01, and "prefix caching moves break-even by 2.24×" | **CONFIRMED** as arithmetic — recomputed from the §10.1 rows and list prices; all four match, and 31.01/13.83 = 2.24. Inputs are §10.1 (row 18, UNVERIFIABLE), so the *formula* is sound and the *inputs* are not independently sourced. | `python3` recompute + METHODOLOGY §6 |
| 38 | Blog claims: ">90 % cache hit rate in coding workloads"; "64 or more accelerators"; the 8,700 tok/s chip-design figure being an **agent** result, not a K3 serving number (§8.8, §10.4) | **CONFIRMED** verbatim, including the context that makes the 8,700 figure a simulated-chip result ("the chip closes timing at 100 MHz and sustains over 8,700 tokens/s decode throughput in simulation"). The document's warning not to cite it as a K3 inference number is correct. | https://www.kimi.ai/blog/kimi-k3 |
| 39 | DSPARK draft `RadixArk/Kimi-K3-DSpark`: 5 layers, hidden 7168, 64 Q / 16 KV heads, head_dim 64, intermediate 14336, aux layers `[7,23,51,67,83]`, block **7**, trained ctx 65,536 with YaRN-16 → 1,048,576 (§7.2) | **CONFIRMED** — every field, incl. `rope_type: yarn`, `factor: 16.0`, `original_max_position_embeddings: 65536`. | https://huggingface.co/RadixArk/Kimi-K3-DSpark/raw/main/config.json |
| 40 | §7.3 acceptance-length percentages of the 8.0 ceiling | **CONFIRMED** as arithmetic (all eight rows). The underlying `acc_len` values are RadixArk-published and were **not** re-fetched — treat as sourced-but-unre-verified. | `python3` recompute |

### Not re-checked

Marked here so the gap is explicit rather than implied: §1.1 licence thresholds,
§1.4 tokenizer/special-token IDs, §2.5 AttnRes tensor inventory, §2.6 preprocessor
limits, the per-shard safetensors header census (§3 preamble), TokenSpeed's recipes
page (§8.5), the AMD Quark and Ascend W4A8 variant sizes (§9), and §10.6 quality
benchmarks. These rest on the original run's fetches.

---

## Sweep log (2026-09-19)

Systemic correction pass against the amended
[`METHODOLOGY.md`](../../METHODOLOGY.md) (§1 bytes-per-param and units, §2 state
slots, §3 consistency/floor rule, §6 cached tokens, §8 pinned inputs). Format:
**section · old → new · reason · source**.

| # | Section | Old → New | Reason | Source |
|---:|---|---|---|---|
| 1 | §5.1 | "⚠️ TO BE VERIFIED — source not independently re-fetchable" → ✅ re-fetched and reproduced | The cookbook's data payload *is* in the served HTML (968 KB, embedded RSC/JS island); only the rendered text lacks it. Calculator source, `config.cells`, `supportedHardware` and all 18 speed cells extracted with one `curl`. | [SGLang cookbook](https://docs.sglang.io/cookbook/autoregressive/Moonshotai/Kimi-K3) |
| 2 | §5.2 | added: DeepSeek-V3-class MLA KV = **70,272 / 35,136 / 17,568 B per token** at BF16 / FP8 / FP4; "2.5×" shown as 70,272 / 27,648 = 2.54 | METHODOLOGY §2 consistency with the DeepSeek docs — the comparison was qualitative only. | METHODOLOGY §2 + `config.json` |
| 3 | §5.3 | added the cookbook's `slots` expression verbatim | Makes the S = 5 / 4 / 3 / 1 table reproducible rather than asserted; METHODOLOGY §2 requires `S` to be taken from a cited engine doc. | cookbook payload |
| 4 | §5.6 | GB300 NVL72 "20 TB ÷ 72 = **288 GB**" → "**288 GB physical, ≈ 279 usable**; 20 TB ÷ 72 = 277.8 GB is the *usable* aggregate" | The division was wrong even though the 288 conclusion was right. METHODOLOGY §8 pins 288 physical / ≈279 usable and forbids merging HGX B300 with GB300 NVL72. | METHODOLOGY §8 · [GB300 NVL72](https://www.nvidia.com/en-us/data-center/gb300-nvl72/) · cloud-pricing.md |
| 5 | §5.6 | max concurrency at 1 M / DCP8: **16 → 15** | 32.6875e9 B free/GPU ÷ 2,092,796,928 B per request = 15.62; METHODOLOGY §3 takes `floor`. All other rows already floored correctly. | `python3` recompute |
| 6 | §5.6 | bottom line "about 16 at 1 M" → "**15 at 1 M**"; "~32 GB/GPU" → "~32.7 GB/GPU", with the 195.1 vs 227.8 GB/GPU arithmetic spelled out | Follows #5; the brief asked for the "195 GB/GPU with ~33 GB/GPU free" claim to be confirmed — it holds in bytes (0.85 × 268e9 − 1,560.9e9/8 = 32.69e9). | `python3` recompute |
| 7 | §5.6 | validation paragraph now quotes the cookbook's caps sentence verbatim | The 101/68/91/60 caps were previously second-hand; now sourced. | cookbook payload |
| 8 | §6.3 | decode-step byte table: **weights-only → weights + MLA KV + KDA state**; totals 137.9/306.2/745.9/1102.0/1414.4/1544.2/1558.4 → **138.4/310.7/763.9/1138.0/1486.4/1688.2/1846.4 GB**; ratios 1.00–11.30× → **1.00–13.34×** | METHODOLOGY §4 requires `weights_read + Σ_seq kv_read`; the table carried a flat per-step constant with no KV term at all — the exact bug class METHODOLOGY §3 flags. KV = `b × ctx × 13,824`; state = `b × 449,372,160` (one live slot/seq). | METHODOLOGY §3–§4 · `python3` recompute |
| 9 | §6.3 | added feasibility note: batch 256 / 512 exceed `max_concurrency(8 K)` = 110 (0.85) / 174 (0.92) on one 8×B300 node | METHODOLOGY §3 consistency rule — the rows are kept as aggregate-across-replica arithmetic, flagged rather than silently read as single-node. | METHODOLOGY §3 + §5.6 |
| 10 | §6.3 | saturation paragraph now separates weight traffic (flat past 256) from KDA state (linear in batch, 230 GB/step at 512) | Follows #8; the old "additional batching is free in bytes" was only true of the weights term. | `python3` recompute |
| 11 | §6.4 | implied bandwidth 16.21 / 24.36 / 27.42 TB/s → **16.27 / 24.82 / 28.31 TB/s**; per GPU 2.03 / 3.05 / 3.43 → **2.03 / 3.10 / 3.54**; MBU 25/38/43 % → **25/39/44 %** | Recomputed from the corrected §6.3 totals. | `python3` recompute |
| 12 | §6.4 | 8 TB/s now cited to METHODOLOGY §8's pinned HGX B300 value, with the GB300 ÷72 figure demoted to corroboration and the cross-SKU inference called out | METHODOLOGY §8 pins per-SKU values; deriving an HGX B300 number from a GB300 NVL72 datasheet is exactly the SKU merge §8 forbids. | METHODOLOGY §8 · [GB300 NVL72](https://www.nvidia.com/en-us/data-center/gb300-nvl72/) |
| 13 | §8.1 | SGLang floor "not stated" → **v0.5.17** (first numbered release with `kimi_k3.py`) | Sibling doc has the probed floor; "not stated" understated what is known. | [inference-engines.md §2.2](../../cross-cutting/inference-engines.md) |
| 14 | §8.1 | added current releases: **vLLM v0.29.0 (2026-09-09), SGLang v0.5.20 (2026-09-18), TRT-LLM v1.2.1 stable** | Checklist item 12 — re-verified today via the releases API; confirms nothing in this doc is pinned to a superseded release and no off-by-one date is present. | `api.github.com/repos/*/releases/latest` |
| 15 | §8.2 (RTX PRO 6000) | **1,792 GB/s → 1,597 GB/s** (Server Edition); decode step 4.5 ms → **5.1 ms**; "4.5× below B300" → **5.0× below**; ~9 ms → ~10 ms at realistic MBU | The cited NVIDIA page is the **Workstation Edition** ("Workstation Edition" ×12 on re-fetch) and contains no Server Edition bandwidth. METHODOLOGY §8 pins Server Edition at 1,597 GB/s. Also restated FP4 as **≈2,000 dense** (4,000 is sparse). | METHODOLOGY §8 · [Server Edition](https://www.nvidia.com/en-us/data-center/rtx-pro-6000-blackwell-server-edition/) · [gpus/rtx6000-pro.md](../../gpus/rtx6000-pro.md) |
| 16 | §8.6 | H100 headroom "80 − 1561/32 = **31 GB/GPU** at 0.97 occupancy" → "0.97 × 80 − 1,560.9/32 = **28.8 GB/GPU**" | The sentence quoted a 0.97 occupancy and then never applied it. Also added the per-request state figure (13.4 MiB/slot × S=5 = 67 MiB). | `python3` recompute · METHODOLOGY §2–§3 |
| 17 | §10.1 | "⚠️ TO BE VERIFIED — the whole table" → ✅ re-verified, banner removed | All 18 cells (hw, strategy, quant, spec, conc, `ttft_ms`, `tpot_ms`, `tokens_per_sec_per_gpu`, version stamps) re-extracted from the payload and matched exactly. | cookbook payload |
| 18 | §10.2, §10.3 | added "re-fetched verbatim from the served payload"; §10.2 now quotes the full sentence (4550 / 3596 / 2407 / 1652) | Same resolution as #1/#17 applied to the remaining SGLang-only tables. | cookbook payload |
| 19 | §11 | cached-input wording: "exactly the METHODOLOGY §6 default assumption" → Moonshot's **own published** ratio (the §6 vendor rule), noting the coincidence with the 10 % own-serving default; both pricing URLs re-fetched; cross-linked to serving-optimizations.md §1.5 | METHODOLOGY §6 keeps vendor-published ratios and the own-serving default strictly separate. | METHODOLOGY §6 · [serving-optimizations.md §1.5](../../cross-cutting/serving-optimizations.md) · [platform.kimi.ai](https://platform.kimi.ai/docs/pricing/chat-k3) |
| 20 | §11 | "Third-party hosts … **not surveyed — WebSearch outage** ⚠️" → pointer to serving-optimizations.md §1.5 plus the sourced OpenRouter **92 % K3 cache-hit** datapoint; ⚠️ narrowed to per-reseller list prices | Checklist item 7 — an outage is not a price statement; the sourced material exists in a sibling doc. | [serving-optimizations.md §1.5](../../cross-cutting/serving-optimizations.md) |
| 21 | §11.1 | "⚠️ **$/GPU-hour for B300 is not sourced in this document**" → sourced rows: **$7.40/GPU-h Hyperstack**, **$15.00 OCI `BM.GPU.B300.8`**, **$17.802 AWS p6-b300**, **$7.94 DigitalOcean 12-mo** | Checklist item 7 — replace the unsourced statement with cloud-pricing.md rows, never a neighbouring GPU's row. (Note $7.40 is Hyperstack's **B300**, not a GB300 rate.) | [cloud-pricing.md](../../cross-cutting/cloud-pricing.md) |
| 22 | §11.1 | conclusions rewritten against those prices: conc 1 break-even $1.98 is 3.7× under the cheapest published rate; conc 64 ($21.79 NOSPEC / $31.01 DSPARK) clears every published on-demand rate; at Moonshot-like cache hit rates ($9.72 / $13.83) self-hosting still beats Hyperstack but **loses to OCI $15.00** | The old bullets ("~$31/GPU-h … in the neighbourhood of real pricing", "roughly $14/GPU-h") were hand-waves once real rows exist. Break-even figures themselves re-derived and unchanged. | `python3` recompute · cloud-pricing.md |
| 23 | §12 Q2 | GB300 "20 TB over 72 (288 GB/GPU)" → 268 GB HGX (2,144 GB/node) vs GB300 **288 physical / ≈279 usable**; added the 1 M floor correction | Same as #4/#5, propagated to the open-questions summary so §0/§12-style claims match the tables. | METHODOLOGY §8 |
| 24 | §12 Q4 | added the full pinned **dense** per-GPU table (B300 13,500/4,500/2,250 · B200 9,000/4,500/2,250 · GB300 15,000/5,000/2,500 · MI355X 10,100/5,000/2,500 · H100/H200 1,979/989.5 · RTX PRO 6000 ≈2,000) | Checklist item 3 — the doc had only the two FP4 datapoints it happened to collect; sparse columns are now explicitly excluded. | METHODOLOGY §8 |
| 25 | Verification log rows 1, 7, 18, 32, 34 | bracketed *[2026-09-19 sweep: …]* notes appended | Earlier corrections are preserved verbatim; the sweep records where a verdict is superseded (row 18 UNVERIFIABLE → CONFIRMED), where the arithmetic inside a confirmed verdict was wrong (rows 1, 32) and where a source was the wrong edition (row 7) or an incomplete formula (row 34). | this sweep |
| 26 | Sources | added the sweep's fetches and a sibling-document reconciliation list | Traceability for every change above. | — |

**Checked and found already correct** (no edit): §1.2/§3/§4 checkpoint totals
(1,560,936,091,448 B re-confirmed against the HF safetensors index — 1,560.9 GB,
matching METHODOLOGY §8's pin); MXFP4 at **0.53125** and NVFP4 at **0.5625**
B/param with the mixed-precision sum done per tensor group (§4); HGX B300
**268 GB / 2,144 GB per node** and B200 **180 GB** (§5.6, §8.2); §5.4 KV/state
totals and the 163 K / 81 K crossover; §5.6 concurrency rows other than 1 M;
§7.4's DSPARK caveat; §10.1's Moonshot price rows; §11.1 break-even arithmetic.

**Not applicable to this document** (checklist items skipped silently elsewhere,
listed here for completeness): the DeepSeek-V4.1-Flash MXFP4-vs-NVFP4 and
890 B/token items, the TokenSpeed DeepSeek recipe item, the DSpark **3.51**
synthetic-acceptance label (this model's figure is `SGLANG_SIMULATE_ACC_LEN=4.5`,
already labelled as pinned-not-measured in §7.4), the B200-vs-H200 DeepSeek-R1
gap, and the sliding-window / `n_kv_heads` KV items (Kimi-K3 has no sliding
window and MLA has no per-head divisor). **Scope:** no per-(model, GPU) fit /
throughput / cost tables were added — those belong in
`research/models/kimik3/<gpu>.md`, written in the next phase.
