# Qwen/Qwen3.8-27B — architecture and inference profile

**Research date: 2026-09-19.** Follows [`research/METHODOLOGY.md`](../../METHODOLOGY.md) — its
formulas (§1 weight memory, §2 KV/state, §3 fit, §4 roofline, §6 cost), the
`[src]` / **⚠️ TO BE VERIFIED** / `est.` / `meas.` legend, and the rules that dense and
sparse TFLOPS are never mixed and SXM is never mixed with PCIe.

> **One-line summary.** A 27.78 B-parameter *dense* hybrid vision-language model: 48 of its 64
> layers are Gated DeltaNet linear attention with **zero per-token KV**, and only 16 are GQA
> full-attention. That makes it 4× cheaper per KV token than an all-attention 64-layer model
> of the same shape, but it buys that with a **fixed 153.9 MB recurrent state per slot**, and
> SGLang allocates **`S` = 5 slots per running request** by default (METHODOLOGY §2) — so the
> real figure is 769.7 MB per request at fp32, or 392.2 MB at bf16. That state is the binding
> constraint below ~4.7 K tokens of context per slot (~23.5 K at `S` = 5), it halves max
> concurrency on every GPU in §10.2, and it is what makes several otherwise-plausible batch
> sizes infeasible in §11.3. It fits one GPU in every precision, from
> BF16 on an H200 to NVFP4 on a 32 GB RTX 5090.

---

## 1. Identity

| Field | Value | Source |
|---|---|---|
| HF repo | `Qwen/Qwen3.8-27B` | [src](https://huggingface.co/Qwen/Qwen3.8-27B) |
| License | Apache-2.0 | `license:apache-2.0` in HF API tags [src](https://huggingface.co/api/models/Qwen/Qwen3.8-27B) |
| Gated? | **No** — `"gated": false`, `"private": false` | [src](https://huggingface.co/api/models/Qwen/Qwen3.8-27B) |
| Released | `hf_released: 2026-08-05T08:22:59Z`; `date_added 2026-08-14` | [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) |
| Last modified | `2026-08-14T15:00:01Z`, sha `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0` | [src](https://huggingface.co/api/models/Qwen/Qwen3.8-27B) |
| Pipeline tag | `image-text-to-text`; `library_name: transformers` | [src](https://huggingface.co/api/models/Qwen/Qwen3.8-27B) |
| Downloads / likes | 7,358,662 / 15,686 (snapshot 2026-09-19) | [src](https://huggingface.co/api/models/Qwen/Qwen3.8-27B) |
| Architecture class | `Qwen3_5ForConditionalGeneration`, `model_type: qwen3_5` | [config.json](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/config.json) |
| Written by | `transformers_version: 5.8.0.dev0` | [config.json](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/config.json) |
| Deploy targets advertised | `deploy:azure`, `deploy:sagemaker`, `endpoints_compatible` | [src](https://huggingface.co/api/models/Qwen/Qwen3.8-27B) |

### 1.1 Files and on-disk size (from the HF API file tree)

`GET https://huggingface.co/api/models/Qwen/Qwen3.8-27B/tree/main?recursive=true`
[src](https://huggingface.co/api/models/Qwen/Qwen3.8-27B/tree/main) — 32 files, summed:

| Group | Files | Bytes | GB (10⁹) | GiB (2³⁰) |
|---|---:|---:|---:|---:|
| `model-0000{1..18}-of-00018.safetensors` | 18 | 55,563,006,776 | **55.563** | **51.747** |
| `tokenizer.json` | 1 | 12,809,320 | 0.0128 | — |
| `vocab.json` + `merges.txt` | 2 | 10,076,018 | 0.0101 | — |
| `model.safetensors.index.json` | 1 | 112,216 | — | — |
| README / tokenizer_config / generation_config / config / chat template / LICENSE / preprocessors / crc32 / .gitattributes | 10 | 108,725 | — | — |
| **Repo total** | **32** | **55,586,114,863** | **55.586** | **51.769** |

The index's own `metadata.total_size` is `55,562,855,904` = 27,781,427,952 × 2, i.e. the
**tensor payload only**. The summed safetensors *file* bytes are `55,563,006,776`, 150,872 B
more — the 18 shards' own safetensors JSON headers (~8.4 KB each for 1,199 tensor entries).
Payload and parameter count reconcile exactly, so **there is no padding or duplicated
tensor** in this checkpoint; the 150,872 B gap is header, not data
[src](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/model.safetensors.index.json)
[src](https://huggingface.co/api/models/Qwen/Qwen3.8-27B/tree/main?recursive=true).

Shard layout is unusual and worth knowing before you plan a cold start: shards alternate
~3.98 GB / ~2.11 GB from shard 6 onwards (one full-attention block plus three GDN blocks per
pair). SGLang reports the BF16 checkpoint takes **~6.5 minutes to load its 18 shards from
NVMe** on a DGX Spark, and advises budgeting ~10 minutes to READY before calling a boot hung
[src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md).

### 1.2 Checkpoint dtype

**Every one of the 1,199 tensors is `BF16`.** Verified by reading the safetensors header of
each of the 18 shards over HTTP range requests and tallying `dtype` across all entries:
`{'BF16': 27,781,427,952}` — no FP32 tensor, no quantization metadata, no
`quantization_config` key in `config.json`
[src](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/config.json).

Note that `text_config.mamba_ssm_dtype: "float32"` is a *runtime* declaration about the
recurrent state, not a weight dtype — see §5.

### 1.3 Tokenizer and vocabulary

| Field | Value |
|---|---|
| `vocab_size` | **248,320** (padded; model card says "Token Embedding: 248,320 (Padded)") [src](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/README.md) |
| Type | Byte-level BPE (`vocab.json` + `merges.txt` + fast `tokenizer.json`) |
| `bos_token_id` / `eos_token_id` | 248,044 / 248,044 (config); `generation_config.json` gives `eos_token_id: [248046, 248044]`, `pad_token_id: 248044` [src](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/generation_config.json) |
| `image_token_id` / `video_token_id` | 248,056 / 248,057 |
| `vision_start_token_id` / `vision_end_token_id` | 248,053 / 248,054 |
| `tie_word_embeddings` | **false** — `lm_head` is a separate 1.271 B-parameter tensor |

`generation_config.json` ships `do_sample: true, temperature: 1.0, top_p: 0.95, top_k: 20`
[src](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/generation_config.json).

### 1.4 Chat template features

From `chat_template.jinja` / the `tokenizer_config.chat_template` in the HF API
[src](https://huggingface.co/api/models/Qwen/Qwen3.8-27B):

- **Vision spans.** `<|vision_start|><|image_pad|><|vision_end|>` for images,
  `<|video_pad|>` for video. `add_vision_id` prefixes `Picture N: ` / `Video N: `. The
  template **raises** `'System message cannot contain images.'` — vision content is
  user/assistant-only.
- **Thinking on by default.** `enable_thinking` defaults true; disable per request with
  `chat_template_kwargs: {"enable_thinking": false}`.
- **`reasoning_effort`** ∈ `{xhigh (default), medium, low}`. Anything else raises
  `'Unexpected reasoning effort …'`. The effort is injected as a natural-language
  instruction, e.g. `xhigh` → *"Reasoning effort is set to xhigh. Please think carefully
  through the task, validate key assumptions, consider plausible alternatives…"*
- **`preserve_thinking`** (default **true**) retains thinking blocks from *all* historical
  messages rather than only the latest turn. Qwen explicitly frames this as a serving
  optimisation: *"It also improves KV cache utilization, optimizing inference efficiency in
  both thinking and non-thinking modes."* [src](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/README.md)
  — see §5.6 for why that matters here more than on a normal model.
- **Tool calling** uses the nested `<tool_call><function=…><parameter=…></tool_call>` form,
  which is what `--tool-call-parser qwen3_coder` (SGLang) / `qwen3_xml` (vLLM) decode. The
  Hermes parser reads a *different* payload (bare JSON inside `<tool_call>`) and will silently
  fail to parse [src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md).

⚠️ **TO BE VERIFIED:** vLLM's recipe defaults to `--tool-call-parser qwen3_xml`
[src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) while SGLang's recipe pins
`--tool-call-parser qwen3_coder` [src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md),
for the *same* chat template. Both engines claim theirs is correct. Method to settle it:
send a tool-calling request to each and inspect whether `tool_calls` is populated or the
call lands in `message.content`; treat the parser flag as engine-specific until then.

---

## 2. Architecture from `config.json`

Local copy: [`research/models/qwen3827b/config.json`](./config.json); upstream
[src](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/config.json).

### 2.1 Top level

| Field | Value | Why it matters |
|---|---|---|
| `architectures` | `["Qwen3_5ForConditionalGeneration"]` | The class the engines dispatch on. Note the family name is `qwen3_5`, not `qwen3_8` — the *serving-relevant* architecture is unchanged from Qwen3.5/3.6 [src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md). |
| `model_type` | `qwen3_5` | |
| `language_model_only` | `false` | The vision tower is part of the checkpoint. vLLM's `--language-model-only` skips loading it and hands the VRAM to KV [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B). |
| `tie_word_embeddings` | `false` | Embedding and `lm_head` are two separate 1.271 B tensors — 2.54 GB of BF16 that no quantiser in §9 touches by default. |

### 2.2 Text tower (`text_config`, `model_type: qwen3_5_text`)

| Field | Value | Explanation |
|---|---|---|
| `num_hidden_layers` | **64** | |
| `hidden_size` | **5120** | |
| `intermediate_size` | **17408** | SwiGLU FFN (`hidden_act: silu`, gate + up + down). 17408 = 3.4 × hidden. |
| `vocab_size` | **248320** | |
| `full_attention_interval` | **4** | Every 4th layer is full attention. |
| `layer_types` | 64 entries: `linear_attention ×3` then `full_attention`, repeated 16× | **48 GDN layers : 16 full-attention layers.** The model card calls it "16 × (3 × (Gated DeltaNet → FFN) → 1 × (Gated Attention → FFN))" [src](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/README.md). Layers 3, 7, 11, …, 63 (0-indexed) are the attention layers — confirmed by the tensor map: `self_attn.*` exists on exactly 16 layers, `linear_attn.*` on exactly 48. |
| `max_position_embeddings` | **262144** | Native window. 1 M via YaRN — §8.5. |
| `rms_norm_eps` | 1e-06 | |
| `dtype` | `bfloat16` | |
| `use_cache` | true | |

**Full-attention layers (16 of them), "Gated Attention":**

| Field | Value | Explanation |
|---|---|---|
| `num_attention_heads` | **24** | |
| `num_key_value_heads` | **4** | GQA 24 : 4 = 6:1 ratio. This is the whole KV story — §5. |
| `head_dim` | **256** | Unusually large. 24 × 256 = 6144 ≠ hidden 5120, so `o_proj` is `[5120, 6144]`. |
| `attn_output_gate` | **true** | `q_proj` is `[12288, 5120]` = `2 × 24 × 256` rows: it emits **both** the queries and a per-head output gate. This doubles the q-projection parameters and is the single most-missed term in any hand count of this model (§3). |
| `output_gate_type` | `swish` | The gate is SiLU-applied before multiplying the attention output. |
| `partial_rotary_factor` | **0.25** | RoPE is applied to only the first `0.25 × 256 = 64` dims of each head — the model card's "Rotary Position Embedding Dimension: 64". The other 192 dims per head are NoPE. |
| `rope_parameters.rope_theta` | **10,000,000** | 10 M base, sized for the 262 K native window. |
| `rope_parameters.mrope_interleaved` | true | |
| `rope_parameters.mrope_section` | `[11, 11, 10]` | M-RoPE (multimodal RoPE) splits the 32 rotary *pairs* (64 dims ÷ 2) across temporal / height / width position axes: 11 + 11 + 10 = 32. This is what lets video frames carry a real temporal position. |
| `attention_bias` / `attention_dropout` | false / 0.0 | No QKV biases. |
| q/k norms | `q_norm`, `k_norm` of shape `[256]` per layer | Per-head RMSNorm on Q and K before RoPE (QK-norm). |

**GDN layers (48 of them), Gated DeltaNet linear attention:**

| Field | Value | Explanation |
|---|---|---|
| `linear_num_value_heads` | **48** | V heads. |
| `linear_num_key_heads` | **16** | Q/K heads — 3:1 to the V heads. |
| `linear_key_head_dim` | **128** | |
| `linear_value_head_dim` | **128** | |
| `linear_conv_kernel_dim` | **4** | Short depthwise causal conv over Q‖K‖V before the recurrence. `conv1d.weight` is `[10240, 1, 4]`, where 10240 = 16·128 (q) + 16·128 (k) + 48·128 (v). |
| `mamba_ssm_dtype` | **`float32`** | The declared precision of the recurrent state. Doubles the state pool vs bf16 — §5.2. |
| tensors per layer | `in_proj_qkv [10240,5120]`, `in_proj_z [6144,5120]`, `in_proj_a [48,5120]`, `in_proj_b [48,5120]`, `conv1d [10240,1,4]`, `out_proj [5120,6144]`, `A_log [48]`, `dt_bias [48]`, `norm [128]` | `in_proj_a`/`in_proj_b` are the per-head decay (α) and write-strength (β) gates of the delta rule — 48 scalars each, one per value head. `in_proj_z` is the 6144-wide output gate. `A_log`/`dt_bias` are the learned per-head decay parameters, Mamba-style. |

**The mechanism in one paragraph.** Each GDN layer keeps a per-head matrix state
`S ∈ ℝ^{128×128}` (key-dim × value-dim), 48 heads per layer. Per token it forms q, k, v
through `in_proj_qkv`, runs them through the depth-4 causal conv, then applies the *gated
delta rule*: `S ← S·(diag(α) − β k kᵀ) + β v kᵀ`, and reads out `o = q·S`, gated by
`swish(in_proj_z(x))` and normalised by the `[128]` RMSNorm. Cost is **O(1) per token** in
both time and memory — there is no growing cache — but the state itself is large and must be
read and written every step. That trade is the defining serving property of this model.

### 2.3 MTP head (`mtp_num_hidden_layers: 1`)

| Field | Value |
|---|---|
| `mtp_num_hidden_layers` | **1** |
| `mtp_use_dedicated_embeddings` | **false** — it shares the main `embed_tokens` and `lm_head` |
| Tensors | `mtp.fc [5120,10240]`, `mtp.pre_fc_norm_embedding`, `mtp.pre_fc_norm_hidden`, one full transformer layer (`self_attn` GQA 24/4 hd 256 with the same output gate, plus a 17408 SwiGLU MLP), `mtp.norm` |

`mtp.fc` is `[5120, 10240]` — it concatenates the *normalised token embedding* and the
*normalised last hidden state* (2 × 5120) and projects back to 5120, the canonical
DeepSeek-style MTP/EAGLE fusion. The model card says it was "trained with multiple steps"
[src](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/README.md). See §7.

### 2.4 Vision encoder (`vision_config`)

| Field | Value | Explanation |
|---|---|---|
| `depth` | **27** | 27 ViT blocks. |
| `hidden_size` | **1152** | 16 heads × 72 head dim. |
| `intermediate_size` | **4304** | GELU-tanh MLP (`hidden_act: gelu_pytorch_tanh`), **with biases** throughout (unlike the LM). |
| `num_heads` | 16 | |
| `patch_size` | **16** | |
| `temporal_patch_size` | **2** | The patch-embed conv is 3-D: `[1152, 3, 2, 16, 16]` — 2 frames per temporal patch. A still image is duplicated to fill it. |
| `spatial_merge_size` | **2** | A 2×2 block of patch tokens is concatenated (4 × 1152 = 4608) and passed through the merger. |
| `num_position_embeddings` | 2304 | Learned `pos_embed [2304, 1152]`, interpolated for other resolutions. |
| `out_hidden_size` | **5120** | `merger.linear_fc2` is `[5120, 4608]` — it lands directly in the LM's residual width. |
| `deepstack_visual_indexes` | **`[]`** | **Empty.** Qwen3-VL's "DeepStack" multi-level feature injection is *not* used by this checkpoint, even though the FP8 quantiser's ignore-list still names `deepstack_merger_list.*` modules that do not exist here [src](https://huggingface.co/Qwen/Qwen3.8-27B-FP8/raw/main/config.json). Only the final ViT layer feeds the LM. |
| window attention | **absent** | There is no `window_size` / `fullatt_block_indexes` field, so the ViT runs **full bidirectional attention over every patch**. That is O(N²) in patches and becomes the dominant cost at high resolution — §6.4. |

No `rope_scaling` / `yarn` block is present in the shipped config; long context is opt-in
via `--hf-overrides` (§8.5).

---

## 3. Parameter count

Derived from `config.json` alone, then checked against the *measured* shapes in the
safetensors headers of all 18 shards. Arithmetic (python3, shown):

```
V=248320; H=5120; I=17408; L=64; LF=16; LG=48
nq=24; nkv=4; hd=256                       # full attention
lv=48; lk=16; dk=128; dv=128; K=4          # GDN

embed_tokens = V*H                       = 248320*5120        = 1,271,398,400
lm_head      = V*H                       = 248320*5120        = 1,271,398,400

# MLP (all 64 layers carry one)
MLP/layer    = 3*I*H  = 3*17408*5120                          =   267,386,880
MLP total    = 267,386,880 * 64                               = 17,112,760,320

# Full attention (16 layers).  attn_output_gate=true => q_proj has 2*nq*hd rows.
q_proj  = (2*24*256)*5120 = 12288*5120                        =    62,914,560
k_proj  = (4*256)*5120    =  1024*5120                        =     5,242,880
v_proj  = (4*256)*5120    =  1024*5120                        =     5,242,880
o_proj  = 5120*(24*256)   =  5120*6144                        =    31,457,280
q_norm+k_norm = 256+256                                       =           512
per layer                                                     =   104,858,112
x16                                                           = 1,677,729,792

# Gated DeltaNet (48 layers).  gq = 16*128 + 16*128 + 48*128 = 10240
in_proj_qkv = 10240*5120                                      =    52,428,800
in_proj_z   =  6144*5120                                      =    31,457,280
in_proj_a   =    48*5120                                      =       245,760
in_proj_b   =    48*5120                                      =       245,760
conv1d      = 10240*1*4                                       =        40,960
out_proj    =  5120*6144                                      =    31,457,280
A_log + dt_bias + norm = 48 + 48 + 128                         =           224
per layer                                                     =   115,876,064
x48                                                           = 5,562,051,072

LM norms = (2*64 + 1)*5120                                    =       660,480
------------------------------------------------------------------------------
LANGUAGE MODEL TOTAL                                          = 26,895,998,464   (26.896 B)
```

```
# Vision (27 blocks, all with biases)
patch_embed = 1152*3*2*16*16 + 1152                           =     1,770,624
pos_embed   = 2304*1152                                       =     2,654,208
block       = (3*1152*1152 + 3*1152)   # qkv + bias
            + (1152*1152 + 1152)       # proj + bias
            + (4304*1152 + 4304)       # fc1 + bias
            + (1152*4304 + 1152)       # fc2 + bias
            + 2*(2*1152)               # norm1, norm2 (LayerNorm: weight+bias)
                                                              =    15,239,504
x27                                                           =   411,466,608
merger      = 2*1152                   # merger.norm (over 1152, pre-merge)
            + 4608*4608 + 4608         # linear_fc1 + bias
            + 5120*4608 + 5120         # linear_fc2 + bias
                                                              =    44,838,656
VISION TOTAL                                                  =   460,730,096   (0.461 B)

# MTP (1 layer)
mtp.fc      = 5120*10240                                      =    52,428,800
self_attn   = same shape as a full-attention layer            =   104,858,112
mlp         = 3*17408*5120                                    =   267,386,880
5 norms     = 5*5120                                          =        25,600
MTP TOTAL                                                     =   424,699,392   (0.425 B)
------------------------------------------------------------------------------
GRAND TOTAL = 26,895,998,464 + 460,730,096 + 424,699,392      = 27,781,427,952   (27.781 B)
```

**Check against the checkpoint: delta = 0 on every line.** Summing `n_elements` over all
1,199 tensors in the 18 safetensors headers gives exactly `27,781,427,952`, and the
per-group tallies match the derivation term for term
[src](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/model.safetensors.index.json).

| Group | Params | Share | BF16 bytes |
|---|---:|---:|---:|
| MLP (64 layers) | 17,112,760,320 | 61.6 % | 34.226 GB |
| GDN linear attention (48 layers) | 5,562,051,072 | 20.0 % | 11.124 GB |
| Full attention (16 layers) | 1,677,729,792 | 6.0 % | 3.355 GB |
| `embed_tokens` | 1,271,398,400 | 4.6 % | 2.543 GB |
| `lm_head` | 1,271,398,400 | 4.6 % | 2.543 GB |
| Vision encoder | 460,730,096 | 1.7 % | 0.921 GB |
| MTP head | 424,699,392 | 1.5 % | 0.849 GB |
| LM norms | 660,480 | <0.01 % | 0.001 GB |
| **Total** | **27,781,427,952** | 100 % | **55.563 GB** |

### 3.1 Active parameters

This is a **dense** model — there is no MoE routing, so *every* parameter of the text tower
is read on *every* token. METHODOLOGY §1's MoE decomposition does not apply; the
`distinct_experts(batch)` term in §4 collapses to "all weights, always".

| Scenario | Active params | Note |
|---|---:|---|
| Text decode, no speculation | **26,895,998,464** (26.896 B) | The whole LM. |
| … of which are GEMM weights | 25,624,600,064 (25.625 B) | `embed_tokens` is a gather, not a matmul; `lm_head` *is* a GEMM. |
| Decode with MTP drafting on | 27,320,697,856 (27.321 B) | +0.425 B per draft step. |
| An image/video request (prefill) | 27,356,728,560 (27.357 B) | +0.461 B, but only over the vision tokens. |

**Model card claim vs. derivation.** The card says "Number of Parameters: 27B" under
*Language Model* [src](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/README.md). The LM
alone is 26.896 B → "27B" is the LM rounded, **not** the checkpoint. The checkpoint you
download and hold in VRAM is 27.781 B. Plan with 27.78 B.

### 3.2 Non-quantised tensors (what stays BF16 in every published quant)

Per METHODOLOGY §1, from each checkpoint's own `quantization_config`:

| Checkpoint | Quantised | Left BF16 (`ignore` / `modules_to_not_convert`) |
|---|---|---|
| `Qwen/Qwen3.8-27B-FP8` | All LM linears **including the MTP layer's `self_attn` and `mlp`** (24,699,207,680 params = 17,112,760,320 MLP + 1,677,721,600 self-attn + 5,536,481,280 GDN projections + 372,244,480 MTP) | `lm_head`, `embed_tokens`, all `input_layernorm`/`post_attention_layernorm`, `self_attn.q_norm`/`k_norm`, `linear_attn.{A_log, conv1d, dt_bias, in_proj_a, in_proj_b, norm}`, **the entire vision tower**, `mtp.fc`, `mtp.norm`, `mtp.pre_fc_norm_*`, `mtp.*_layernorm`, `mtp.self_attn.{q,k}_norm` — 882 module patterns [src](https://huggingface.co/Qwen/Qwen3.8-27B-FP8/raw/main/config.json) |
| `RadixArk/Qwen3.8-27B-NVFP4` and `nvidia/Qwen3.8-27B-NVFP4` | **NVFP4 g16:** `lm_head` + all 64 layers' `mlp.{gate,up,down}_proj` (193 tensors). **FP8:** all 48 `linear_attn.{in_proj_qkv, in_proj_z, out_proj}` + all 16 `self_attn.{q,k,v,o}_proj` (208 tensors) | `ignore: ["mtp*", "mtp.layers.0*"]` — **the whole MTP head stays BF16**; plus embeddings, all norms, `in_proj_a`/`in_proj_b`, `conv1d`, `A_log`, `dt_bias`, and the entire vision tower [src](https://huggingface.co/nvidia/Qwen3.8-27B-NVFP4/raw/main/config.json) |
| `RadixArk/…-NVFP4-BF16-LMHead` | Same, minus `lm_head` | `lm_head` additionally BF16 (+1.83 GB) |
| `RedHatAI/Qwen3.8-27B-INT4` | "Only the weights of the linear operators within transformer blocks" | `re:visual.*`, `re:model.visual.*`, `re:.*lm_head`, `re:.*embed_tokens$`, `re:.*linear_attn\.in_proj_a$`, `re:.*linear_attn\.in_proj_b$` [src](https://huggingface.co/RedHatAI/Qwen3.8-27B-INT4/raw/main/README.md) |

**Planning consequence:** a BF16 floor of **6.16 GB** (FP8 recipe) to **4.37 GB** (NVFP4
recipe, which additionally packs `lm_head`) survives every quantisation. On a 32 GB card
that floor is 14–19 % of the budget before a single weight bit is stored.

---

## 4. Weight memory by dtype

Per METHODOLOGY §1: `weights_bytes = Σ (n_elements × bytes_per_element) + scale overhead`.
Per METHODOLOGY §1's bytes-per-param table: **NVFP4 = 0.5625 B/param** (0.5 B + one FP8 E4M3
scale per 16 elements, **+12.5 %**); **MXFP4 = 0.53125 B/param** (0.5 B + one E8M0 per 32,
**+6.25 %**); FP8 block-scaled 128×128 = 1.0002 B/param (1 B + one FP32 per 16,384 elements,
+0.02 %); INT4 g128 symmetric = 0.515625 B/param (0.5 B + one FP16 scale per 128, +3.125 %;
METHODOLOGY's ≈ 0.53 / +6 % row is the asymmetric scale-**and**-zero variant, which these
checkpoints do not use). This is a **mixed-precision** checkpoint in every quantised build, so
the totals below are summed per tensor group, never derived from one flat rate.

| Scheme | Quantised params | Quantised bytes | Scale overhead | BF16 remainder | **Total** | **GiB** | Published disk size |
|---|---:|---:|---:|---:|---:|---:|---|
| **Native BF16** | — | — | — | 27,781,427,952 × 2 | **55.563 GB** | **51.747** | 55.563 GB [tree](https://huggingface.co/api/models/Qwen/Qwen3.8-27B/tree/main) |
| **FP8 block 128×128** | 24,699,207,680 | 24.699 GB | 0.0060 GB | 3.082 B × 2 = 6.164 GB | **30.870 GB** | **28.750** | **30.87 GB** ✅ exact [tree](https://huggingface.co/api/models/Qwen/Qwen3.8-27B-FP8/tree/main) |
| **NVFP4 W4A4 (FP4 head)** | 18.384 B NVFP4 + 7.214 B FP8 | 9.192 + 7.214 = 16.406 GB | 1.149 GB (NVFP4) + 0.002 GB (FP8) | 2.183 B × 2 = 4.366 GB | **21.923 GB** | **20.418** | **21.92 GB** ✅ exact [tree](https://huggingface.co/api/models/nvidia/Qwen3.8-27B-NVFP4/tree/main) |
| **NVFP4 W4A4 (BF16 head)** | as above minus `lm_head` | | | +1.83 GB | **23.751 GB** | **22.120** | **23.75 GB** ✅ exact [tree](https://huggingface.co/api/models/RadixArk/Qwen3.8-27B-NVFP4-BF16-LMHead/tree/main) |
| **MXFP4** (same layer map) `est.` | as NVFP4 | 16.406 GB | 0.575 GB | 4.366 GB | **21.349 GB** `est.` | 19.883 | *No NVIDIA-loadable MXFP4 checkpoint exists* — see below |
| **INT4 W4A16 g128 sym** | 24,326,963,200 | 12.163 GB | 0.380 GB | 6.909 GB | **19.453 GB** | **18.117** | **19.45 GB** ✅ exact [tree](https://huggingface.co/api/models/RedHatAI/Qwen3.8-27B-INT4/tree/main) · AMD Quark 19.51 GB [tree](https://huggingface.co/api/models/amd/Qwen3.8-27B-Quark-AWQ-INT4-W4A16/tree/main) |
| **INT8 W8A16** | | | | | 31.62 GB `meas.` | 29.45 | `lued/Qwen3.8-27B-INT8-W8A16-MTP` [tree](https://huggingface.co/api/models/lued/Qwen3.8-27B-INT8-W8A16-MTP/tree/main) |
| **Ascend W8A8 (ModelSlim INT8)** | | | | | 32.1 GB | 29.9 | vLLM recipe metadata [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) |

Every formula-derived total lands on the published disk size to within rounding. The FP8 row
only reconciles once you notice the MTP layer's `self_attn`/`mlp` **are** quantised (the FP8
`modules_to_not_convert` list names only `mtp.fc`, the MTP norms and the MTP QK-norms):
including them accounts for exactly the 0.372 GB that a naive "MTP stays BF16" assumption
over-counts.

**MXFP4 on NVIDIA: don't.** vLLM's recipe states plainly: *"MXFP4 does not load on Nvidia
devices. The vLLM MXFP4 implementation on Nvidia device is currently missing linear method
support so it doesn't run as intended. Use NVFP4 quantization on Nvidia instead."*
[src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B). MXFP4 remains the right format on
MI350X/MI355X, where it runs at 10.1 PFLOPS dense — but no MXFP4 Qwen3.8-27B checkpoint is
published (§9).

**Conflicting published figures** — resolve in favour of the file tree:

| Claim | Source | Verdict |
|---|---|---|
| "51.7 GiB of weights" (BF16) | vLLM variant metadata [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) | ✅ matches 51.747 GiB |
| "28.7 GiB" (FP8) | vLLM variant metadata | ✅ matches 28.750 GiB |
| "24.6 GiB" (`Inferact/Qwen3.8-27B-NVFP4`) | vLLM variant metadata | ✅ matches 24.57 GiB (26.38 GB tree) |
| "21.9 GiB" (`nvidia/Qwen3.8-27B-NVFP4`) | vLLM variant metadata | ❌ the tree is 21.92 **GB** = 20.42 GiB. The unit label is wrong; the number is right. |
| "identical 21.9 GB on disk" (nvidia export) | SGLang cookbook [src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md) | ✅ correct |
| "FP8 weights ~28.5GB; NVFP4 weights ~16.5GB" | SGLang cookbook "Hardware fit" bullet | ⚠️ **TO BE VERIFIED / likely stale.** 28.5 ≈ 28.75 GiB mislabelled GB is plausible, but **16.5 GB cannot be reconciled with any published NVFP4 tree** (smallest is 21.92 GB). 16.4 GB *is* exactly the quantised-tensor payload before scales and the BF16 remainder — so the bullet probably quotes the packed-weight bytes only. Do not size a 32 GB card against 16.5 GB. |

---

## 5. KV cache and per-sequence state

This is the section that decides every deployment. The model has **two** memory pools, and
METHODOLOGY §2's table covers both rows — "MHA/GQA" for the 16 attention layers and
"Linear attention / DeltaNet / Mamba → **0 per token**, fixed per-sequence state" for the 48
GDN layers.

### 5.1 Per-token KV — only the 16 full-attention layers pay

```
bytes/token/layer = 2 × n_kv_heads × head_dim × B
                  = 2 × 4 × 256 × B
BF16 (B=2): 2 × 4 × 256 × 2 =  4,096 B/token/layer × 16 layers =  65,536 B/token = 64.0 KiB
FP8  (B=1): 2 × 4 × 256 × 1 =  2,048 B/token/layer × 16 layers =  32,768 B/token = 32.0 KiB
NVFP4/INT4 KV (B=0.5):                                           16,384 B/token = 16.0 KiB   ⚠️ see 5.5
```

SGLang publishes the same two numbers independently — *"`kv_bytes_per_token` — 16 attention
layers × GQA 4 × 256 × K+V: 32.8 KB at fp8, 65.5 KB at bf16"*
[src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md) — decimal-KB
spellings of the same 32,768 / 65,536 bytes. ✅

**What the hybrid buys.** A hypothetical all-full-attention version of this model (64 layers
of the same GQA 4 × 256) would cost `64 × 4096 = 262,144` B/token at BF16. The hybrid costs
65,536 — a flat **4× reduction**, exactly `64/16`. At 1 M context that is 68.7 GB vs 274.9 GB
per sequence.

### 5.2 Fixed per-sequence state — the 48 GDN layers

METHODOLOGY §2: `n_heads × d_k × d_v × B_state` + conv state `n_heads × d × kernel × B`.

```
recurrent state = 48 layers × 48 value heads × d_k(128) × d_v(128) × B_state
  float32 (B=4): 48 × 48 × 128 × 128 × 4 = 150,994,944 B = 150.99 MB = 144.0 MiB
  bfloat16(B=2): 48 × 48 × 128 × 128 × 2 =  75,497,472 B =  75.50 MB =  72.0 MiB

conv state      = 48 layers × 10,240 channels × (kernel 4 − 1) × 2 B (bf16)
                = 48 × 10240 × 3 × 2      =   2,949,120 B =   2.95 MB

TOTAL per sequence, float32 state = 153,944,064 B = 153.94 MB = 146.81 MiB
TOTAL per sequence, bfloat16 state =  78,446,592 B =  78.45 MB =  74.81 MiB
```

SGLang publishes **153.9 MB at fp32 and 78.4 MB at bf16** for exactly this geometry
[src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md). ✅ Exact match,
independently derived.

**The crossover.** One GDN state slot is worth, in FP8-KV tokens:

```
float32  state: 153,944,064 / 32,768 = 4,698 tokens
bfloat16 state:  78,446,592 / 32,768 = 2,394 tokens
```

SGLang calls these the `token_equiv` values and quotes **4698 / 2394** verbatim. ✅

> **Below ~4.7 K tokens of context, one state slot costs more memory than the KV cache
> does.** A fleet of short agentic turns is *state*-bound, not KV-bound. A fleet of
> long-document requests is KV-bound. These are two different sizing problems on the same
> model.

These `token_equiv` figures are **per slot**, which is how SGLang publishes them. The
per-*request* crossover is `S × token_equiv` (METHODOLOGY §2): at SGLang's default `S = 5`
that is **23,490 tokens at fp32 state / 11,970 at bf16** — i.e. on a default SGLang
deployment the state pool outweighs the KV pool all the way out to ~23 K context, not ~4.7 K.
See §5.4 for `S`, and §10.2 for what it does to concurrency.

### 5.3 Totals at 8 K / 32 K / 128 K / 1 M, per sequence

`kv_total(ctx, 1) = ctx × kv_bytes_per_token + fixed_state`, with
`fixed_state = S × 153.94 MB` (fp32) or `S × 78.45 MB` (bf16) per METHODOLOGY §2. **The table
below is `S = 1`** — the single-slot floor. Multiply the state term by your engine's `S`
(§5.4; SGLang defaults to 5) before sizing anything: at 8 K / fp32 that turns 0.422 GB into
0.884 GB per sequence.

| Context | FP8 KV + fp32 state | FP8 KV + bf16 state | BF16 KV + fp32 state | BF16 KV + bf16 state |
|---|---:|---:|---:|---:|
| 8,192 | **0.422 GB** | 0.347 GB | 0.691 GB | 0.616 GB |
| 32,768 | **1.228 GB** | 1.152 GB | 2.301 GB | 2.226 GB |
| 131,072 | **4.449 GB** | 4.373 GB | 8.744 GB | 8.669 GB |
| 262,144 (native max) | 8.744 GB | 8.669 GB | 17.334 GB | 17.259 GB |
| 1,048,576 (YaRN max) | **34.514 GB** | 34.438 GB | 68.873 GB | 68.798 GB |

At 8 K the state is 36 % of the per-sequence cost at fp32; at 1 M it is 0.45 %.

### 5.4 Engines multiply the state, not the KV

Both engines reserve **more than one** state slot per running request, because the recurrent
state cannot be paged or recomputed cheaply the way KV can.

SGLang's `S` (state slots per running request)
[src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md):

| `--mamba-radix-cache-strategy` | `S` | Effective state per request (fp32 / bf16) |
|---|---:|---:|
| `extra_buffer` (default) | **5** | 769.7 MB / 392.2 MB |
| `extra_buffer_lazy` | 4 | 615.8 MB / 313.8 MB |
| `no_buffer` | 3 | 461.8 MB / 235.3 MB |
| `--disable-radix-cache` | **1** | 153.9 MB / 78.4 MB |

`SGLANG_OPT_MAMBA_SKIP_DECODE_LOCK=1` frees one slot on the `extra_buffer` paths, and
`extra_buffer` frees one more with the overlap scheduler off. Speculative decoding adds a
`D` term of verify intermediates: `--speculative-num-draft-tokens` for EAGLE/MTP (4 at the
recommended 3/1/4) and DFLASH (8), `--speculative-dspark-block-size + 1` for DSpark (8 for
the published draft), or **0** with `--enable-linear-replayssm-spec`, which moves the verify
intermediates onto a fixed ring.

SGLang's balancing formula. The page states it as `r = (S + D) x token_equiv / L`, with
`token_equiv = state_bytes / kv_bytes_per_token` (§5.2's 4698 / 2394)
[src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md); substituting
`token_equiv` gives the equivalent expanded form used here:

```
ratio = (S + D) x state_bytes / (L x kv_bytes_per_token)      # = (S + D) x token_equiv / L
```

where `L` is the average request length (input + output) and `--mamba-full-memory-ratio`
divides post-weight memory between the worst-case-reserved GDN state pool and the paged KV
pool. SGLang warns the default (0.9) *"over-provisions the KV pool and silently clamps
concurrency"*. The equivalent explicit pin is
`--max-mamba-cache-size = target_concurrency × S`.

vLLM's equivalents are `--mamba-cache-dtype` / `--mamba-ssm-cache-dtype` (`auto` | a dtype),
`--mamba-block-size` (multiple of 8, for the causal-conv1d kernel), `--mamba-cache-mode`
(`none` | `all` | `align`), `--enable-mamba-fine-grained-prefix-cache`, and
`--use-replayssm` / `--replayssm-buffer-len` (default 16)
[src](https://github.com/vllm-project/vllm/blob/main/vllm/config/cache.py).

**Which `S` to plan with (METHODOLOGY §2).** METHODOLOGY §2 defines
`fixed_state_per_seq = S × (recurrent + conv state)` and says to take `S` from the engine doc
cited here. For this model:

| Engine | `S` for hybrid GDN | Basis |
|---|---:|---|
| **SGLang** | **5** (default `extra_buffer`); 4 / 3 / 1 on the other strategies | The cookbook's own slot expression, quoted above [src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md) |
| **vLLM** | ⚠️ **TO BE VERIFIED** — `≥ 1` plus speculative slots, per METHODOLOGY §2; `--mamba-cache-mode align` and `--enable-mamba-fine-grained-prefix-cache` both imply > 1, but vLLM publishes no slot count and `cache.py` exposes none | [src](https://github.com/vllm-project/vllm/blob/main/vllm/config/cache.py) |

**All fit and concurrency arithmetic in §10.2 is therefore given at `S = 5` (SGLang's shipped
default) with the `S = 1` floor alongside it** — the two differ by up to 2× in max concurrency
at short context, which is exactly where this model's state term dominates (§5.2).

**Measured effect of the state dtype**, on an RTX 5090 with no speculation:
**97,280 KV tokens at bf16 state against 68,588 at fp32** — a 42 % larger KV pool for halving
the state precision [src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md).
SGLang is explicit that this is **not** a one-way speed trade: with speculation, fp32
sometimes wins (NVFP4 + EAGLE: 152.9 vs 144.5 tok/s/user) and sometimes loses (FP8 + EAGLE:
106.3 vs 116.1) — *"measure both for your quantization"* — and it advises treating bfloat16
as *"an accuracy gate"* to validate for your workload.

### 5.5 Sliding window, compression, cross-layer sharing, sparse attention

| Mechanism | Present? | Effect here |
|---|---|---|
| Sliding window | **No.** No `sliding_window` / `use_sliding_window` in `text_config`. The 16 attention layers are full causal over the whole context. | KV grows linearly with context, uncapped, to 34.5 GB at 1 M (FP8). |
| KV compression / latent (MLA) | **No.** Plain GQA with a real K and V per layer. | METHODOLOGY §2's MLA row does not apply. |
| Cross-layer KV sharing (YOCO/CLA) | **No.** All 16 attention layers own their cache. | — |
| Sparse / indexer attention (DeepSeek DSA-style) | **No.** No indexer tensors, no `nsa`/`dsa` config. SGLang's `--attention-backend dsa` is DeepSeek-only. | No sparse-attention kernel question to answer for this model on any GPU. |
| **Linear attention as the compression mechanism** | **Yes — this is the model's answer.** 75 % of layers hold O(1) state. | The 4× KV reduction of §5.1, paid for with §5.2. |
| FP8 KV | **Yes**, universally recommended. Both RadixArk NVFP4 checkpoints declare `kv_cache_quant_algo: FP8` / `kv_cache_scheme: {num_bits: 8, type: float}` so `--kv-cache-dtype auto` already selects it; the NVIDIA export ships no `kv_cache_scheme`, so `auto` leaves it BF16 — every SGLang recipe pins `--kv-cache-dtype fp8_e4m3` explicitly to remove the difference [src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md). RedHatAI's INT4 build bakes a static tensor-wise FP8 KV scheme into the checkpoint [src](https://huggingface.co/RedHatAI/Qwen3.8-27B-INT4/raw/main/README.md). | Halves the KV pool; measured on a 1×RTX 5090 at 32 K: **91,022 KV tokens with fp8 vs 76,458 with bf16** [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B). |
| NVFP4 / INT4 KV | ⚠️ **TO BE VERIFIED for this model.** SGLang's MHA backend matrix shows FP4 KV cache on FA4, Triton, Torch-native, FlexAttention and TRTLLM-MHA, but **not** on FlashInfer [src](https://docs.sglang.io/docs/advanced_features/attention_backend.md) — and FlashInfer is the backend every published Qwen3.8-27B recipe pins. No recipe, checkpoint or benchmark for 4-bit KV on this model was found. | Would take KV to 16 KiB/token. Estimation method if you try it: halve every FP8 figure in §5.3 and re-run the §5.4 ratio, then validate accuracy — GQA 4 × 256 with only 4 KV heads has little redundancy to spare. |

### 5.6 Prefix caching interacts badly with recurrent state — and `preserve_thinking` is the fix

Radix/prefix caching on a hybrid model cannot simply re-point at a cached KV block: the GDN
state at the end of a shared prefix must also be **checkpointed**, which is what SGLang's
`extra_buffer` slots and vLLM's `mamba_cache_mode: align` (plus
`--enable-mamba-fine-grained-prefix-cache`) exist to do. That is why `S` is 5 and not 1 by
default. `--disable-radix-cache` drops `S` to 1 and reclaims 4 × 153.9 MB per request — at
the cost of every prefix re-prefill.

This is the mechanical reason Qwen ships `preserve_thinking: true` by default and pitches it
as a *serving* feature: keeping historical thinking blocks makes each agentic turn a strict
extension of the previous prompt, so the radix match runs to the very end of the last turn
and both the KV *and* the GDN checkpoint are reused. Turning `preserve_thinking` off
truncates the shared prefix at the start of the previous thinking block and forces a
re-prefill — and on this architecture a re-prefill also means re-running the 48 recurrent
layers over the whole prefix.

⚠️ **TO BE VERIFIED:** the quantitative prefix-cache hit-rate → TTFT curve for this model
(METHODOLOGY §5.4's 0 / 50 / 90 % scenarios). No engine has published one. Estimation method:
prefill cost scales with uncached tokens per §6.2, but the GDN state checkpoint restore is
an extra ~154 MB read per matched request that a pure-attention model does not pay; at 8 GB/s
effective that is ~19 µs, negligible against any real prefill.

---

## 6. Compute profile

### 6.1 Decode

```
FLOPs/token = 2 × active_params + attention terms
            = 2 × 26,895,998,464 = 53.79 GFLOP   (dense — every layer, every token)
```

Additional terms per token:

| Term | Formula | Value |
|---|---|---|
| GDN recurrent update | ≈ 48 layers × 48 heads × 128 × 128 × 2 × ~4 ops | **0.302 GFLOP** `est.` — 0.56 % of the GEMM term |
| Full-attention dot products at context `C` | `16 layers × 4 (QK+AV, ×2 for MAC) × n_q_heads(24) × head_dim(256) × C` = 0.393 MFLOP × C | see below |

| Context `C` | Attention FLOPs/token | % of the 53.79 GFLOP GEMM term |
|---|---:|---:|
| 8,192 | 3.22 GFLOP | 6.0 % |
| 32,768 | 12.89 GFLOP | 24.0 % |
| 131,072 | 51.54 GFLOP | 95.8 % |
| 1,048,576 | 412.32 GFLOP | 767 % |

Because only 16 layers do this, the crossover where attention arithmetic overtakes the
weights is pushed out to ~128 K — four times further than on an all-attention model of the
same width. **The GDN recurrence contributes essentially nothing to FLOPs and everything to
bandwidth.**

### 6.2 Prefill

```
prefill_FLOPs(T) = 2 × 26.896e9 × T  +  16 × 2 × 24 × 256 × T²      (causal ≈ ½ of the dense form)
```

| `T` | GEMM | Attention | Total | Attention share |
|---:|---:|---:|---:|---:|
| 4,096 | 220.3 TFLOP | 3.3 TFLOP | 223.6 TFLOP | 1.5 % |
| 32,768 | 1,762.7 TFLOP | 211.1 TFLOP | 1,973.8 TFLOP | 10.7 % |
| 131,072 | 7,050.6 TFLOP | 3,377.7 TFLOP | 10,428.3 TFLOP | 32.4 % |
| 262,144 (native max) | 14,101.3 TFLOP | 13,510.8 TFLOP | 27,612.0 TFLOP | 48.9 % |
| 1,048,576 (YaRN max) | 56,405.0 TFLOP | 216,172.8 TFLOP | 272,577.8 TFLOP | **79.3 %** |

At the 262 K native limit a full prefill is already half attention; at 1 M it is
four-fifths. A 1 M-token prefill is 272.6 PFLOP — **~20 seconds of a GB300's entire dense
NVFP4 peak at 100 % MFU**, so realistically ~80–120 s at a plausible 0.2 MFU. Plan long-context
serving around prefix caching and P/D disaggregation, not around cold 1 M prefills.

### 6.3 Bytes read per decode step (METHODOLOGY §4, extended)

METHODOLOGY §4 gives `bytes_per_decode_step = weights_read + Σ_seq kv_read`. **For this model
that is incomplete:** the GDN state must be read *and written back* for every running
sequence, every step. The honest form is:

```
bytes_per_decode_step(batch) = weights + batch × ctx × kv_bytes_per_token
                                       + batch × 2 × state_bytes        ← read + write
  dense model  ⇒ weights_read = all weights, independent of batch
```

Note the asymmetry with §5.3: the `S` multiplier is a **memory** term, not a **bandwidth**
one. Only the live slot is read and written each step; the extra `S − 1` buffers are prefix
checkpoints that sit in HBM untouched. So decode traffic uses `state_bytes` once (×2 for
read+write) regardless of `S`, while the fit arithmetic in §10.2 pays `S ×` for every running
request.

FP8 KV, bf16 state (78.45 MB), GB (10⁹ B):

| Weights | bs | ctx 8 K | ctx 32 K | ctx 128 K |
|---|---:|---:|---:|---:|
| **BF16 55.56 GB** | 1 | 55.99 | 56.79 | 59.99 |
| | 32 | 69.17 | 94.94 | 197.98 |
| | 128 | 110.01 | 213.08 | 625.19 |
| | 256 | 164.45 | 370.61 | 1,194.84 |
| **FP8 30.87 GB** | 1 | 31.30 | 32.10 | 35.30 |
| | 32 | 44.48 | 70.25 | 173.29 |
| | 128 | 85.31 | 188.39 | 600.50 |
| | 256 | 139.75 | 345.91 | 1,170.15 |
| **NVFP4 21.92 GB** | 1 | 22.35 | 23.15 | 26.35 |
| | 32 | 35.53 | 61.30 | 164.34 |
| | 128 | 76.36 | 179.44 | 591.55 |
| | 256 | 130.80 | 336.96 | 1,161.20 |
| **INT4 19.45 GB** | 1 | 19.88 | 20.68 | 23.88 |
| | 32 | 33.06 | 58.83 | 161.87 |
| | 128 | 73.89 | 176.97 | 589.08 |
| | 256 | 128.33 | 334.49 | 1,158.73 |

**Where the state traffic bites.** At bs=128, ctx 8 K, FP8 weights: 30.87 GB weights +
34.36 GB KV + **20.08 GB of pure state read/write** — 24 % of the step. At fp32 state it
would be 39.4 GB, or 39 % of the step. This is the single best argument for
`--mamba-ssm-dtype bfloat16` on short-context, high-concurrency traffic, and it is exactly
what vLLM's **ReplaySSM** decode kernel (`--use-replayssm`) attacks: *"cache recent SSM inputs
and skip the per-step full-state store, writing the checkpoint back only on flush"*
[src](https://github.com/vllm-project/vllm/blob/main/vllm/config/cache.py). It requires
`mamba_cache_mode` `none` or `align`, the Triton or FlashInfer mamba backend, and
non-speculative decode. ⚠️ **TO BE VERIFIED:** no measured speedup for ReplaySSM on
Qwen3.8-27B specifically has been published.

### 6.4 Vision encoder FLOPs

Per-token constants, derived from §2.4 shapes:

```
ViT block linear params = 3·1152² + 1152² + 4304·1152 + 1152·4304 = 15,224,832
FLOPs per patch-token from the 27 blocks = 2 × 15,224,832 × 27     = 0.822 GFLOP
patch_embed                              = 2 × 1,769,472           = 3.54 MFLOP / patch
merger (per merged / LM token)           = 2 × (4608² + 5120·4608) = 89.65 MFLOP
ViT attention (bidirectional, no window) = 4 × 1152 × 27 × N²      = 124,416 × N² FLOP
```

| Image | Pixels | Patch tokens `N` | **LM tokens** | ViT cost | ViT attn share | LM prefill of those tokens | ViT / LM |
|---|---:|---:|---:|---:|---:|---:|---:|
| 448 × 448 | 200,704 | 784 | **196** | 0.74 TFLOP | 10 % | 10.5 TFLOP | 7 % |
| 720p (1280 × 720) | 921,600 | 3,600 | **900** | 4.67 TFLOP | 35 % | 48.4 TFLOP | 10 % |
| 1024 × 1024 | 1,048,576 | 4,096 | **1,024** | 5.56 TFLOP | 37 % | 55.1 TFLOP | 10 % |
| 1080p (1920 × 1080) | 2,073,600 | 8,100 | **2,025** | 15.03 TFLOP | 54 % | 108.9 TFLOP | 14 % |
| Max (16.78 Mpx) | 16,777,216 | 65,536 | **16,384** | **589.9 TFLOP** | **91 %** | 881.3 TFLOP | **67 %** |

**Multimodal token budgets**, from `preprocessor_config.json` and
`video_preprocessor_config.json` [src](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/preprocessor_config.json)
[src](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/video_preprocessor_config.json):

```
tokens_per_image = pixels / (patch² × merge²)                   = pixels / 1,024
tokens_per_video = total_pixels / (patch² × merge² × temporal)  = pixels / 2,048
```

| Setting | Value | LM tokens |
|---|---:|---:|
| image `size.shortest_edge` (min_pixels) | 65,536 | **64** |
| image `size.longest_edge` (max_pixels) | 16,777,216 | **16,384** |
| video `size.longest_edge` **as shipped** | 25,165,824 | **12,288** |
| video `size.longest_edge` **as recommended by the card** | 469,762,048 | **229,376** |

The card's own words: *"It is recommended to set the `longest_edge` parameter in the
video_preprocessor_config file to 469,762,048 (corresponding to 224k video tokens) to enable
higher frame-rate sampling for hour-scale videos"* — and 229,376 = 224 × 1024 exactly
confirms the `/2048` divisor [src](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/README.md).
Default video sampling is `fps=2`, `do_sample_frames=True`; vLLM alone supports overriding it
per request via `mm_processor_kwargs` when launched with
`--media-io-kwargs '{"video": {"num_frames": -1}}'`. The engine-side overrides live in
[vllm#34330](https://github.com/vllm-project/vllm/pull/34330) and
[sglang#18467](https://github.com/sgl-project/sglang/pull/18467).

> **Watch the max-resolution ViT.** With no window attention, a single 16.78 Mpx image costs
> 590 TFLOP in the encoder, 91 % of it quadratic attention — comparable to prefilling ~11 K LM
> tokens. A 224 K-token video at the recommended setting is a *far* larger encoder job than
> anything the LM does. ⚠️ **TO BE VERIFIED:** whether vLLM/SGLang chunk the ViT attention or
> materialise the full `N×N` score matrix at these sizes; no doc found. Estimation method:
> an unchunked `65,536²` FP16 score matrix would be 8.6 GB, so some chunking must exist for
> the recipes to work at all.

---

## 7. Speculative decoding and MTP

### 7.1 What ships in the checkpoint

A **1-layer MTP head** (`mtp_num_hidden_layers: 1`, 424,699,392 params, §3) sharing the main
embedding and `lm_head` (`mtp_use_dedicated_embeddings: false`). It is present in the BF16,
FP8 and INT8 checkpoints. **Both NVFP4 exports explicitly `ignore: ["mtp*", "mtp.layers.0*"]`
— the head is shipped but left BF16**, which is why the NVFP4 builds carry ~0.85 GB of
un-quantised MTP weights.

### 7.2 Published acceptance and speedups

From the DFlash2 launch write-up, mean acceptance length **for Qwen3.8-27B specifically**
[src](https://inco.ai/blog/dflash2/) — *"the model's default sampling and a block size of 8"*:

| Benchmark | MTP | DSpark | DFlash2 |
|---|---:|---:|---:|
| GSM8K | 5.02 | 4.36 | **5.46** |
| MATH-500 | 4.72 | 3.92 | **5.28** |
| HumanEval | 3.91 | 3.30 | **4.39** |
| MBPP | 3.99 | 3.51 | **4.79** |
| MT-Bench | 3.74 | 3.01 | **4.10** |
| **Mean** | **4.28** | **3.62** | **4.80** |

⚠️ **Do not confuse this table's DSpark MBPP 3.51 with the other 3.51 in this research tree.**
Re-fetched 2026-09-19, inco.ai's page really does print 3.51 for DSpark on MBPP, as a measured
per-request mean acceptance length for *this* model [src](https://inco.ai/blog/dflash2/); the
page contains no `rejection_sample_method` and no "synthetic". The identical-looking 3.51 that
appears for **DeepSeek-V4.1-Flash on MI355X** is a *synthetic benchmark constant* —
`"rejection_sample_method":"synthetic","synthetic_acceptance_length":3.51`, a value the harness
is told to assume rather than one it observes
([models/deepseek41f/architecture.md](../deepseek41f/architecture.md) §7.3,
[cross-cutting/serving-optimizations.md](../../cross-cutting/serving-optimizations.md) §2.7).
Different model, different provenance, coincident number; never propagate one as the other.

Marketing claim, labelled as such: DFlash2 delivers *"2.7–3.4× the throughput of
autoregressive decoding"* [src](https://inco.ai/blog/dflash2/). The post **does** name the
engine and the operating point — *"SGLang serves at 2.7–3.4× the throughput of autoregressive
decoding at batch size 1"* — so the gap is narrower than "unattributed": what is missing is
the **GPU**, and a batch-size-1 multiplier says nothing about throughput under load (§7.4).
⚠️ **TO BE VERIFIED** on named hardware at a named concurrency.

From vLLM's own instrumented runs on 2 × RTX 5090 at 262 K context, reading
`vllm:spec_decode_num_{accepted,draft}_tokens_total` [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B):

| Precision | KV tokens at boot | Weights/GPU | **MTP acceptance** |
|---|---:|---:|---:|
| FP8 | 377,456 | 14.28 GiB | **0.771** |
| NVFP4 (`Inferact`) | 445,875 | 12.02 GiB | **0.897** |
| NVFP4 (`unsloth`) | 920,517 | 10.64 GiB | **0.788** |
| NVFP4 on 1 × 5090, 32 K, `--enforce-eager` | 90,112 | — | **0.754** |

vLLM's note is worth repeating: *"Acceptance is measured from
`vllm:spec_decode_num_{accepted,draft}_tokens_total`, since throughput alone cannot
distinguish a working drafter from one that loaded and was ignored."*

From SGLang on a 32 GB RTX 5090, NVFP4 + DFlash2 + bfloat16 state: **4.92 ms median TPOT at an
accept length of 4.29** — *"the best result on this card"*
[src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md).

### 7.3 The four drafters, per engine

| Method | Draft weights | vLLM flag | SGLang flags | Notes |
|---|---|---|---|---|
| **MTP** (in-checkpoint) | 0 extra (0.425 B in the checkpoint) | `--speculative-config '{"method":"mtp","num_speculative_tokens":3}'` — **5** on DGX Spark GB10 and RTX PRO 6000 per vLLM's `hardware_overrides` | `--speculative-algorithm EAGLE --speculative-num-steps 3 --speculative-eagle-topk 1 --speculative-num-draft-tokens 4` | SGLang: *"This recipe was originally documented with `NEXTN`, an alias of `EAGLE` — same algorithm."* `D = 4` in the ratio, or **0** with `--enable-linear-replayssm-spec`. |
| **MTP (Ascend spelling)** | same | `--speculative-config '{"method":"qwen3_5_mtp","num_speculative_tokens":3,"enforce_eager":true}'` | — | vLLM Ascend only. |
| **DSpark** | `RadixArk/Qwen3.8-27B-DSpark`, **3.71 GB** [tree](https://huggingface.co/api/models/RadixArk/Qwen3.8-27B-DSpark/tree/main); `DSparkDraftModel`, based on `RadixArk/Qwen3.8-27B-NVFP4` | not offered in vLLM's recipe | `--speculative-algorithm DSPARK --speculative-draft-model-path RadixArk/Qwen3.8-27B-DSpark --speculative-draft-attention-backend flashinfer` | **Does not take `--speculative-num-draft-tokens`.** Its verify window is `--speculative-dspark-block-size` (γ) **+ 1**; γ is auto-inferred from the draft checkpoint (**7** here, so `D = 8`). Needs a materially higher `--mamba-full-memory-ratio` than no-spec. |
| **DFlash2** | `incoai/Qwen3.8-27B-DFlash2`, **3.85 GB** [tree](https://huggingface.co/api/models/incoai/Qwen3.8-27B-DFlash2/tree/main); `DFlash2DraftModel`, block-diffusion draft. Also mirrored at `z-lab/Qwen3.8-27B-DFlash2` (3.85 GB) | `--speculative-config '{"method":"dflash","model":"incoai/Qwen3.8-27B-DFlash2","num_speculative_tokens":7}'` — **requires vLLM ≥ 0.28.0** ([vllm#52816](https://github.com/vllm-project/vllm/pull/52816)); *"`num_speculative_tokens` must be 7 (block_size 8 minus one)"* | `--speculative-algorithm DFLASH --speculative-draft-model-path incoai/Qwen3.8-27B-DFlash2 --speculative-num-draft-tokens 8` | `D = 8`. The selector projects candidates through the target `lm_head`, **including quantised heads**, so it works on the FP4-head NVFP4 export. |

**DSpark specifics for this model.** It is the *only* one of the four whose draft checkpoint
is derived from a quantised base (`base_model: RadixArk/Qwen3.8-27B-NVFP4`), it is licensed
`other` rather than Apache-2.0, and it is the weakest of the three on every published
acceptance benchmark (mean 3.62 vs MTP's 4.28). On the 32 GB RTX 5090 it also forces the
tightest memory pins — `--mem-fraction-static` 0.88 (bf16) / 0.91 (fp32) / 0.92 on the dense
`lm_head` export, two of them also cutting the prefill chunk to 1024 and 512. **For this
model there is no configuration in which DSpark is the right first choice**; it exists for
parity with the 2.4 T sibling.

**DFlash2 on Ascend NPU** ([sglang#35629](https://github.com/sgl-project/sglang/pull/35629)):
the selector verify falls back to argmax, so *"NPU currently guarantees lossless verification
only for greedy requests; use `temperature=0` and `top_k=1`."* Non-greedy requests log a
warning and both proposal and verification fall back to greedy — the requested sampling
distribution is **not** preserved.

### 7.4 When speculation stops helping

⚠️ **TO BE VERIFIED for this model.** No published acceptance-vs-batch curve exists. Two
model-specific reasons to expect the usual crossover to arrive *earlier* here:

1. Every draft token that is verified must also advance 48 recurrent states, and the verify
   intermediates cost `D` extra state slots per request (§5.4) — 4 to 8 × 78–154 MB. At
   high concurrency that memory is better spent on KV.
2. Decode is already only ~24 % weights-bound at bs=128/ctx 8 K (§6.3), so the arithmetic
   headroom speculation exploits is smaller than on a pure-attention dense model.

Estimation method if you need a number before measuring: speculation pays while
`decode_step_time` is dominated by the batch-independent weights term; from §6.3 that is
roughly `batch × (ctx × 32,768 + 2 × 78.45e6) < weights_bytes`, i.e. **bs ≲ 100 at 8 K
context with FP8 weights**, falling to **bs ≲ 25 at 32 K**.

---

## 8. Engine support matrix

### 8.1 Minimum versions

| Engine | Minimum for this model | Latest as of 2026-09-19 | Source |
|---|---|---|---|
| **transformers** | **≥ 5.8.0** — *"Matches the transformers version config.json was written by (5.8.0), for the Qwen3-VL processor classes. vLLM parses the config with its own `Qwen3_5Config`."* | 5.17.0 [PyPI](https://pypi.org/pypi/transformers/json) | [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) |
| **vLLM** | **0.17.0** (`min_vllm_version`); **≥ 0.28.0** for DFlash2; RTX 5090 cells verified on `0.26.1rc1.dev608+g99a10304d`. Docker `vllm/vllm-openai:qwen38` | 0.29.0 [PyPI](https://pypi.org/pypi/vllm/json) | [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) |
| **SGLang** | every published cell measured on **v0.5.19**; install `uv pip install --prerelease=allow sglang`; Docker `lmsysorg/sglang:latest` | 0.5.20 [PyPI](https://pypi.org/pypi/sglang/json) | [src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md) |
| **TensorRT-LLM** | `Qwen3_5ForConditionalGeneration` is in the PyTorch-backend multimodal matrix | — | [src](https://github.com/NVIDIA/TensorRT-LLM/blob/main/docs/source/models/supported-models.md) |
| **FlashInfer** | **> 0.6.15.post1** for MTP under the FlashInfer backend (`plan` must accept `uniform_q_len`) | 0.6.18.post1 [PyPI](https://pypi.org/pypi/flashinfer-python/json) | [src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md) |
| **TokenSpeed** | recipe published; no minimum stated | — | [src](https://lightseek.org/tokenspeed/recipes/models) |
| **Dynamo** | ⚠️ **TO BE VERIFIED.** No Qwen3.8-27B Dynamo recipe found. vLLM's `compatible_strategies` is `["single_node_tp"]` only — **no P/D-disaggregated layout is published for this model** (its 2.4 T sibling and DeepSeek-V4.1 both have one). Since the model fits one GPU, disaggregation buys little; the open question is whether the GDN state transfers over NIXL at all. | | [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) |

### 8.2 Verified hardware, by engine

| GPU | vLLM recipe | SGLang cookbook | Notes |
|---|---|---|---|
| **A100 (SM80)** | not listed as verified; FP8 and INT4 variants both exclude it from `supported_hardware`… | not listed | **See §8.4 — A100 is the hard case.** |
| **H100 (SM90)** | `fp8` and `int4` variants list `h100` in `supported_hardware`; no launch command published | not listed | Workable, unverified. |
| **H200 (SM90)** | `fp8`, `int4` list `h200` | **verified**, BF16 + FP8, single GPU | The only datacentre-Hopper cell anyone has actually run. |
| **B200 / GB200 (SM100)** | `fp8`, `nvfp4`, `int4` list `b200`/`gb200` | not listed | |
| **B300 (SM100)** | `fp8`, `nvfp4`, `int4` list `b300` | not listed | |
| **GB300 (SM100)** | **verified** (`meta.hardware: gb300: verified`) | **verified**, all 5 checkpoints — but every GB300 cell carries `verificationStatus: spec === "dflash" ? "in-progress" : "verified"`, so **DFlash2 on GB300 is *in-progress*, not verified** [src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md) | GB300 also sits outside the 202-cell v0.5.19 GSM8K sweep, which covers RTX 5090 / RTX PRO 6000 / DGX Spark only |
| **RTX PRO 6000 Blackwell (SM120)** | **verified**, `nvfp4_nvidia` variant | **verified**, all 5 checkpoints | |
| **RTX 5090 (SM120)** | **verified** 1× and 2× | **verified** | |
| **DGX Spark / GB10 (SM121)** | **verified** | **verified**, all 80 configurations | |
| **Ascend 950PR (NPU)** | **verified**, W8A8 and native FP8 | Qwen3.6-27B tutorial exists; no 3.8-27B page | |
| **MI300X / MI325X / MI355X (ROCm)** | `fp8` variant lists `mi300x`, `mi325x`, **`mi355x`** in `supported_hardware` — **but no launch command, no verified cell** | **not listed at all** | **See §8.4.** |

### 8.3 Launch recipes, quoted verbatim

**vLLM — low latency, NVFP4, TP1** [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B):

```bash
vllm serve Inferact/Qwen3.8-27B-NVFP4 \
  --tensor-parallel-size 1 \
  --max-model-len 262144 \
  --kv-cache-dtype fp8 \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice --tool-call-parser qwen3_xml
```

**vLLM — FP8, TP4 (one GB300 tray) for the largest KV cache**:

```bash
vllm serve Qwen/Qwen3.8-27B-FP8 \
  --tensor-parallel-size 4 \
  --max-model-len 262144 \
  --kv-cache-dtype fp8 \
  --reasoning-parser qwen3
```

> *"Add `--speculative-config '{"method":"mtp","num_speculative_tokens":3}'` for MTP."*

**vLLM — 2 × RTX 5090 (consumer Blackwell, sm120), TP2**:

```bash
vllm serve unsloth/Qwen3.8-27B-NVFP4 \
  --tensor-parallel-size 2 \
  --max-model-len 262144 \
  --kv-cache-dtype fp8 \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice --tool-call-parser qwen3_xml
```

> *"**NVFP4 uses the real kernel here.** vLLM selects `FlashInferCutlassNvFp4LinearKernel for
> NVFP4 GEMM` on sm120 — a cutlass path, not an emulation fallback."*
> *"**The block-scaled FP8 checkpoint needs no workaround.** vLLM auto-disables DeepGemm for
> `model_type=qwen3_5_text` on Blackwell and falls back to CUTLASS, so it loads unaided.
> Verified by running *without* `VLLM_USE_DEEP_GEMM=0`: identical 377,456-token KV pool."*

**vLLM — 1 × RTX 5090, NVFP4 needs `--enforce-eager`**:

```bash
vllm serve Inferact/Qwen3.8-27B-NVFP4 \
  --tensor-parallel-size 1 \
  --max-model-len 32768 \
  --kv-cache-dtype fp8 \
  --enforce-eager \
  --reasoning-parser qwen3
```

> *"One card has **31.4 GiB usable**, not 32… Without `--enforce-eager`, startup dies in CUDA
> graph capture with `torch.OutOfMemoryError: Tried to allocate 784.00 MiB`. **Raising or
> lowering `--gpu-memory-utilization` does not help** — 0.80 and 0.93 both leave the same
> 47.06 MiB free, because that budget covers weights and KV while graph capture allocates
> outside it."* KV pool at 32 K: 91,022 tokens with `--enforce-eager` alone; **135,926** adding
> `--language-model-only`; **152,917** also capping `--max-num-seqs 8`; 76,458 with bf16 KV.

**SGLang — H200, FP8 or BF16, single GPU** (from the cookbook's `cells` table, template
placeholders as published) [src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md):

```
--trust-remote-code --model-path {{MODEL_NAME}} --kv-cache-dtype fp8_e4m3
--mem-fraction-static 0.85 --attention-backend flashinfer
--chunked-prefill-size 32768 --max-prefill-tokens 32768
--reasoning-parser qwen3 --tool-call-parser qwen3_coder --host {{HOST_IP}} --port {{PORT}}
```

**SGLang — GB300, all five checkpoints:**

```
--trust-remote-code --model-path {{MODEL_NAME}} --kv-cache-dtype fp8_e4m3
--mem-fraction-static 0.85 --chunked-prefill-size 2048
--reasoning-parser qwen3 --tool-call-parser qwen3_coder --host {{HOST_IP}} --port {{PORT}}
```

**SGLang — RTX PRO 6000 (SM120), all five checkpoints:** same as GB300 but with
`--attention-backend flashinfer` added and `--mem-fraction-static 0.85`.

**SGLang — DGX Spark GB10:** the RTX PRO 6000 recipe at `--mem-fraction-static 0.80`, not 0.85
— *"the unified pool pricing the host's memory too: 0.85 of 128GB leaves ~8GB for the OS —
exactly DGX OS earlyoom's SIGTERM threshold… At 0.85, 15 of the 48 cells were killed that
way… at 0.80 every cell served on every attempt."*

**SGLang — RTX 5090 (32 GB), single-stream envelope:** adds
`--mem-fraction-static 0.9 --max-running-requests 1 --cuda-graph-max-bs-decode 1`, with the
explicit warning that *"on this 32GB card the GDN state pool, not KV, is what runs out first."*

### 8.4 Per-GPU caveats

**A100 (SM80) — the hard case, and it is about kernels, not memory.**
Nothing in the memory arithmetic rules A100 out: BF16 (55.56 GB) fits in 80 GB with 12.4 GB
left for KV+state, and INT4 leaves 48.5 GB. What rules it out in practice:

- **No FP8 tensor core.** Ampere has none; vLLM's own doc states FP8 computation needs compute
  capability ≥ 8.9, so `Qwen/Qwen3.8-27B-FP8` would run W8A16 weight-only at best
  [src](https://github.com/vllm-project/vllm/blob/main/docs/features/quantization/llm_compressor/fp8.md).
  **And FP8 KV is the default in every published recipe** — on A100 the KV pool would be BF16,
  doubling §5.3.
- **No FP4 at all.** NVFP4 checkpoints fail on sm_80 with `no kernel image is available for
  execution on the device` [vllm#35922](https://github.com/vllm-project/vllm/issues/35922).
- **vLLM's `supported_hardware` lists `h100` for both `fp8` and `int4` but never `a100`.**
- SGLang's GDN platform constraints say *"Other CUDA (Hopper, Ampere, etc.): auto-selection
  works; no special constraints"* for the full-attention backend
  [src](https://docs.sglang.io/docs/advanced_features/attention_backend.md) — so the *kernels*
  exist, via Triton for GDN and FA2/Triton for the GQA layers.
- FlashAttention-2 supports head dim up to 256 on Ampere, so the 256-dim attention layers are
  covered [src](https://github.com/Dao-AILab/flash-attention). FA3 is Hopper-only; FA4 is
  Hopper+Blackwell.

**Verdict:** A100 is *runnable* at BF16 or INT4-W4A16 with BF16 KV, via SGLang's Triton GDN
path. It is not verified by anyone, it forfeits FP8 KV, and per §6.3 it reads 55.6 GB of
weights per step at 2.039 TB/s. **⚠️ TO BE VERIFIED end-to-end.**

**H100 / H200 (SM90).** SGLang: *"BF16 and FP8 only — the card has no FP4 tensor cores, so an
NVFP4 checkpoint's MLP would fall back to the Marlin W4A16 weight-only path, and all three
NVFP4 cells are greyed out."* The H200 recipes use **32768-token prefill chunks** because
*"SM90 prefill is fast enough that a big chunk barely stalls decode, unlike the SM120 guidance
below"*, and *"the FlashInfer GDN prefill backend engages by default under them"*.
`--attention-backend fa3` is a valid alternative, *"measured slightly faster at bs=1"*.
H100's 80 GB leaves only 12.4 GB for KV+state at BF16 (§10.2 fit table) — that is 18 concurrent
8 K sequences at SGLang's default `S = 5`, so **use FP8 on H100**, and even then the card tops
out at 56 (§10.2).

**B200 / B300 / GB300 (SM100).** Full NVFP4 at 9,000 (B200) / 13,500 (HGX B300) / 15,000
(GB300 NVL72) dense TFLOPS per GPU
[src](https://www.nvidia.com/en-us/data-center/hgx/) [src](https://www.nvidia.com/en-us/data-center/gb300-nvl72/).
SGLang's constraint for hybrid GDN models on SM100: the *full-attention* backend must be
`triton`, `trtllm_mha`, or `fa4` — **not** FlashInfer
[src](https://docs.sglang.io/docs/advanced_features/attention_backend.md), which is why the
GB300 cells omit `--attention-backend flashinfer` while the SM120 cells include it. SM100 is
also the only place the **FlashInfer GDN prefill fast path** auto-engages: *"On SM100/SM103
with CUDA 13+, SGLang automatically selects FlashInfer for GDN prefill when the per-phase
override is unset, the base linear-attention backend is Triton, recurrent state is BF16,
key/value head dimensions are 128, dynamic chunking and page-major KV layout are disabled, and
`--chunked-prefill-size` is between 1 and 8192."* This model's `linear_key_head_dim` and
`linear_value_head_dim` are both **128** ✅ and the recipes pin
`--chunked-prefill-size 2048` ✅ — so **setting `--mamba-ssm-dtype bfloat16` is what unlocks
the fast GDN prefill kernel on Blackwell**, not just a memory saving.

**RTX PRO 6000 Blackwell (SM120) / RTX 5090 / DGX Spark (SM121).** *"use `--attention-backend
flashinfer`; `trtllm_mha` is SM100-only. MTP with the FlashInfer backend requires a FlashInfer
build whose prefill `plan` accepts `uniform_q_len` (newer than 0.6.15.post1); otherwise run
spec with `--attention-backend triton`."* On SM120 **both** state precisions run the Triton
linear-attn prefill path — the FlashInfer GDN fast path gates on SM100 — so the dtype choice
here is purely memory and accuracy. `--chunked-prefill-size 2048` is mandatory guidance:
*"decode steps stall behind each prefill chunk on hybrid GDN models, and 8192-token chunks
stall them ~600ms at a time."* The Server Edition's 1,597 GB/s (vs the Workstation's 1,792)
is an 11 % TPOT swing — confirm which edition you rent.

**MI355X (CDNA 4, gfx950) — supported in principle, unverified in practice.**

- vLLM's `fp8` variant lists `mi300x`, `mi325x`, **`mi355x`** in `supported_hardware`, but
  publishes **no ROCm launch command and no verified cell** for this model
  [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B).
- SGLang's cookbook page for Qwen3.8-27B has **no AMD cell at all**. Its GDN backend table
  does list **Triton (AMD/ROCm)** as supporting GDN decode, prefill/extend **and** speculative
  target-verify, and the platform note says *"AMD (ROCm): `triton` recommended"* for the
  full-attention backend [src](https://docs.sglang.io/docs/advanced_features/attention_backend.md).
- The strongest positive evidence is the **sibling**: SGLang's Qwen3.8 2.4 T MoE page — the
  same hybrid GDN/GQA family — publishes **verified** `mi300x`, `mi350x` and `mi355x` cells,
  e.g. MI355X MXFP4 single-node: `--tp-size 8 --mem-fraction-static 0.9` with
  `SGLANG_USE_AITER=1`, image `lmsysorg/sglang-rocm:v0.5.17-rocm720-mi35x-20260812`
  [src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8.md). The GDN kernels
  therefore demonstrably run on gfx950.
- **Quantisation on ROCm:** FP8 (via AITER or Triton), AWQ (Triton dequant — *"The faster
  Marlin path is not available"*), MXFP4 (requires CDNA3/CDNA4 + `SGLANG_USE_AITER=1`), W8A8,
  GPTQ, compressed-tensors, Quark, and **`petit_nvfp4`** (NVFP4 on ROCm via
  [Petit](https://github.com/causalflow-ai/petit-kernel), MI250/MI300X) all work; `awq_marlin`,
  `gptq_marlin`, `gguf`, `modelopt_fp8`, `modelopt_fp4` do **not**
  [src](https://docs.sglang.io/docs/hardware-platforms/amd_gpu.md). Since both published NVFP4
  exports are ModelOpt-produced, **`modelopt_fp4` being unsupported on ROCm means the NVFP4
  checkpoints are not directly loadable on MI355X** — and CDNA 4 has no native NVFP4 rate
  anyway [src](https://rocm.blogs.amd.com/software-tools-optimization/nvfp4-mi355/README.html).
- **AMD's own checkpoint exists**: `amd/Qwen3.8-27B-Quark-AWQ-INT4-W4A16` (19.51 GB), produced
  with AMD Quark using *"native `qwen3_5` architecture support for AWQ (contributed
  upstream)"*, and requiring *"a Quark-compatible inference runtime with `W4A16Int4` scheme
  support ([vllm#48606](https://github.com/vllm-project/vllm/pull/48606) and
  [vllm#46110](https://github.com/vllm-project/vllm/pull/46110))"*
  [src](https://huggingface.co/amd/Qwen3.8-27B-Quark-AWQ-INT4-W4A16). AMD would not have
  shipped it without a ROCm path.

**Practical MI355X plan:** `Qwen/Qwen3.8-27B-FP8` (5.0 PFLOPS dense OCP-FP8) or
`amd/Qwen3.8-27B-Quark-AWQ-INT4-W4A16` under SGLang with `SGLANG_USE_AITER=1`,
`--attention-backend triton`, `--linear-attn-backend triton`. 288 GB HBM3E at 8 TB/s makes it
the single highest-concurrency card in §10.2's fit table (356 concurrent 8 K sequences at INT4,
`S = 5`). **⚠️ TO BE VERIFIED end-to-end for
Qwen3.8-27B specifically — no one has published a run.**

**TensorRT-LLM.** `Qwen3_5ForConditionalGeneration` appears in the PyTorch-backend multimodal
matrix [src](https://github.com/NVIDIA/TensorRT-LLM/blob/main/docs/source/models/supported-models.md):

| Feature | Status |
|---|---|
| Overlap Scheduler | Yes |
| CUDA Graph | Yes |
| Chunked Prefill | **Untested** |
| Torch Sampler | Yes |
| **KV Cache Reuse** | **No** |
| Logits Post Processor | Untested |
| EPD Disaggregated Serving | Yes |
| Modality | L + I + V |
| Multimodal Encoder Side Stream | Yes |
| Multimodal Embeddings Cache | Yes |

**KV cache reuse = No is the headline.** TRT-LLM's footnote 19 explains why: *"KV cache reuse
for hybrid recurrent-attention models requires an explicit recurrent-state snapshot policy,
such as `kv_cache_config.mamba_state_config.periodic_snapshot_interval`; the model default
disables reuse when no snapshot policy is configured."* Given §5.6, **TensorRT-LLM is the
wrong engine for agentic multi-turn traffic on this model** unless you configure that policy
yourself. Note also that the text-only sibling `Qwen3_5MoeForCausalLM` gets a full "Yes /
MTP / Yes" row while the dense conditional-generation class does not appear in the text-only
table at all — the 27 B is a multimodal-path-only entry in TRT-LLM.

### 8.5 Long context (YaRN to 1 M) — quoted verbatim from the model card

```shell
VLLM_ALLOW_LONG_MAX_MODEL_LEN=1 vllm serve ... --hf-overrides '{"text_config": {"rope_parameters": {"mrope_interleaved": true, "mrope_section": [11, 11, 10], "rope_type": "yarn", "rope_theta": 10000000, "partial_rotary_factor": 0.25, "factor": 4.0, "original_max_position_embeddings": 262144}}}' --max-model-len 1000000
```

```shell
SGLANG_ALLOW_OVERWRITE_LONGER_CONTEXT_LEN=1 python -m sglang.launch_server ... --json-model-override-args '{"text_config": {"rope_parameters": {"mrope_interleaved": true, "mrope_section": [11, 11, 10], "rope_type": "yarn", "rope_theta": 10000000, "partial_rotary_factor": 0.25, "factor": 4.0, "original_max_position_embeddings": 262144}}}' --context-length 1000000
```

Qwen's warning: *"All the notable open-source frameworks implement static YaRN, which means
the scaling factor remains constant regardless of input length, **potentially impacting
performance on shorter texts.** … It is also recommended to modify the `factor` as needed. For
example, if the typical context length for your application is 524,288 tokens, it would be
better to set `factor` as 2.0."* vLLM adds: *"Note the override is nested under `text_config`
here, where the 2.4T takes it flat."*

### 8.6 Open issues and gotchas

| Issue | Detail |
|---|---|
| `--reasoning-parser qwen3` is effectively mandatory | *"the chat template opens every assistant turn with `<think>`, so without it the entire reasoning block lands in `message.content` and a 2048-token budget can be spent before the answer starts."* [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) |
| MXFP4 on NVIDIA | Does not load (§4). |
| DGX Spark earlyoom kills | `--mem-fraction-static 0.80`, not 0.85 (§8.3). `nvidia-smi` reports `Not Supported` for memory on GB10 (unified) — gate relaunches on `MemAvailable` in `/proc/meminfo`. Docker GPU access is CDI-only (`--device nvidia.com/gpu=all`). |
| `--enable-linear-replayssm-spec` + explicit non-fp32 `--mamba-ssm-dtype` | Auto-selects fp32 state when unset; an explicit non-fp32 value *"logs a state-drift warning at boot"*. SGLang's bf16+EAGLE cells run with that warning, accounted for in their validation. |
| KV offload | vLLM reports `offloading_cpu: verified` and `offloading_fs: verified` for this model [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B). ⚠️ Whether the *GDN state* offloads with it is **TO BE VERIFIED**. |
| Parallelism | vLLM `compatible_strategies: ["single_node_tp"]`. TP only; no published PP, EP (there are no experts), DP-attention or P/D layout. vLLM's supported-models table does list PP ✅ and LoRA ✅ for `Qwen3_5ForConditionalGeneration` [src](https://docs.vllm.ai/en/latest/models/supported_models.html). Since the model fits one GPU in every precision, **replication (DP) over TP is the right default**; TP4 exists only to buy a bigger KV pool. |

---

## 9. Available quantised variants

The ecosystem around this checkpoint is unusually large — an HF search for `Qwen3.8-27B`
returns well over 100 derivative repos [src](https://huggingface.co/api/models?search=Qwen3.8-27B).
The ones that matter for serving:

### 9.1 First-party and engine-validated

| Repo | Format | Weights on disk | Validated by | Downloads |
|---|---|---:|---|---:|
| [`Qwen/Qwen3.8-27B`](https://huggingface.co/Qwen/Qwen3.8-27B) | BF16 | 55.56 GB | Qwen; SGLang H200/GB300/RTX PRO 6000/DGX Spark; vLLM | 7,358,662 |
| [`Qwen/Qwen3.8-27B-FP8`](https://huggingface.co/Qwen/Qwen3.8-27B-FP8) | FP8 E4M3, block 128×128, dynamic activations | 30.87 GB | Qwen; SGLang; vLLM (incl. Ascend 950PR native) | 7,347,861 |
| [`RadixArk/Qwen3.8-27B-NVFP4`](https://huggingface.co/RadixArk/Qwen3.8-27B-NVFP4) | NVFP4 W4A4 g16 MLPs + FP8 attn/GDN, **FP4 `lm_head`** | 21.92 GB | SGLang (RTX PRO 6000, RTX 5090, DGX Spark, GB300) | 2,220,310 |
| [`RadixArk/Qwen3.8-27B-NVFP4-BF16-LMHead`](https://huggingface.co/RadixArk/Qwen3.8-27B-NVFP4-BF16-LMHead) | same body, **BF16 `lm_head`** | 23.75 GB | SGLang — *"every recipe on this page was measured against it"* | 286,753 |
| [`nvidia/Qwen3.8-27B-NVFP4`](https://huggingface.co/nvidia/Qwen3.8-27B-NVFP4) | NVIDIA ModelOpt export; *"identical quantized-layer map… identical tensor set, identical 21.9 GB on disk"* | 21.92 GB | NVIDIA; SGLang re-measured all 16 overlay combos per card on v0.5.19 | 104,346 |
| [`Inferact/Qwen3.8-27B-NVFP4`](https://huggingface.co/Inferact/Qwen3.8-27B-NVFP4) | uniform W4A4 | 26.38 GB | **vLLM's default `nvfp4` variant**; best measured MTP acceptance (0.897) | 1,314,038 |
| [`unsloth/Qwen3.8-27B-NVFP4`](https://huggingface.co/unsloth/Qwen3.8-27B-NVFP4) | mixed-precision NVFP4 (*"FP8 channel-wise alongside the 4-bit groups"*) | 23.42 GB | vLLM 2×5090 — **920,517 KV tokens, roughly twice the `Inferact` build** | 3,421,659 |
| [`RedHatAI/Qwen3.8-27B-INT4`](https://huggingface.co/RedHatAI/Qwen3.8-27B-INT4) | compressed-tensors W4A16 INT4, AWQ+GPTQ, **static FP8 KV baked in** | 19.45 GB | Red Hat; **vLLM's `int4` variant — the only 4-bit path listed for `h100`/`h200`** | 343,260 |
| [`RedHatAI/Qwen3.8-27B-NVFP4`](https://huggingface.co/RedHatAI/Qwen3.8-27B-NVFP4) | NVFP4 | 23.42 GB | Red Hat | 46,875 |
| [`amd/Qwen3.8-27B-Quark-AWQ-INT4-W4A16`](https://huggingface.co/amd/Qwen3.8-27B-Quark-AWQ-INT4-W4A16) | AMD Quark AWQ INT4 g128, BF16 activations | 19.51 GB | AMD | 96,684 |
| [`RadixArk/Qwen3.8-27B-DSpark`](https://huggingface.co/RadixArk/Qwen3.8-27B-DSpark) | DSpark draft (`DSparkDraftModel`), license `other` | 3.71 GB | SGLang | 355,687 |
| [`incoai/Qwen3.8-27B-DFlash2`](https://huggingface.co/incoai/Qwen3.8-27B-DFlash2) | DFlash2 block-diffusion draft, Apache-2.0 | 3.85 GB | vLLM ≥0.28.0, SGLang, llama.cpp, Ollama, oMLX | 413,432 |
| `Eco-Tech/Qwen3.8-27B-w8a8` (ModelScope) | ModelSlim INT8 W8A8 | 32.1 GB | vLLM Ascend 0.23.0 | — |

### 9.2 Community formats

| Family | Representative repos | Notes |
|---|---|---|
| **GGUF** (llama.cpp / Ollama / LM Studio) | [`unsloth/Qwen3.8-27B-GGUF`](https://huggingface.co/unsloth/Qwen3.8-27B-GGUF) (7.63 M downloads, 472 GB of quant levels), [`ggml-org/Qwen3.8-27B-GGUF`](https://huggingface.co/ggml-org/Qwen3.8-27B-GGUF), [`bartowski/Qwen3.8-27B-GGUF`](https://huggingface.co/bartowski/Qwen3.8-27B-GGUF), [`lmstudio-community/Qwen3.8-27B-GGUF`](https://huggingface.co/lmstudio-community/Qwen3.8-27B-GGUF), [`ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF`](https://huggingface.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF) | Many carry `-MTP-` in the name, implying the MTP head is preserved. Several `IQ4-XS … 16GB-VRAM` builds target consumer cards. **Not relevant to datacentre serving**: `gguf` is unsupported on ROCm in SGLang and is not a vLLM/SGLang production path. |
| **MLX** (Apple silicon) | [`lmstudio-community/Qwen3.8-27B-MLX-{4,5,6,8}bit`](https://huggingface.co/lmstudio-community/Qwen3.8-27B-MLX-4bit), [`mlx-community/Qwen3.8-27B-4bit`](https://huggingface.co/mlx-community/Qwen3.8-27B-4bit) (16.05 GB), `mlx-community/Qwen3.8-27B-MTP-4bit` | |
| **AWQ / GPTQ W4A16** | [`cyankiwi/Qwen3.8-27B-AWQ-INT4`](https://huggingface.co/cyankiwi/Qwen3.8-27B-AWQ-INT4) (21.02 GB), [`philbert440/Qwen3.8-27B-W4A16-AWQ`](https://huggingface.co/philbert440/Qwen3.8-27B-W4A16-AWQ) (19.55 GB), `dbirks/Qwen3.8-27B-W4A16-AutoRound`, `abhishekchohan/Qwen3.8-27B-GPTQ-INT4-FP8KV`, `pearsonkyle/Qwen3.8-27B-GPTQ-W4A16`, `SergiioB/Qwen3.8-27B-GPTQ-Int4-sym-G128-MTP-BF16` | The AWQ/GPTQ path is the one that works on **both** Hopper (Marlin) and ROCm (Triton dequant). |
| **INT8** | `lued/Qwen3.8-27B-INT8-W8A16-MTP` (31.62 GB), `Freaksterz/Qwen3.8-27B-SmoothQuant-W8A8-INT8` | |
| **Other NVFP4** | `gittensor-model-hub/Qwen3.8-27B-NVFP4-RTX5090`, `QUASAR-QAT/Qwen3.8-27B-QUASAR-NVFP4`, `sakamakismile/Qwen3.8-27B-MTP-NVFP4`, `neroued/Qwen3.8-27B-nvfp4-NInfer` | |
| **ROCm-targeted GGUF** | `julianmb/Qwen-3.8-27B-ROCmFP4-FAST-GGUF`, `kingjones777/Qwen3.8-27B-ROCmFP4-STRIX-MTP-GGUF` | Community, unvalidated. |

**No MXFP4 checkpoint exists** for this model despite MXFP4 being the fast path on
MI350X/MI355X. That is a real gap for AMD deployment.

### 9.3 Published evaluations of the quantised builds

**RedHatAI INT4** — lm-evaluation-harness / lighteval, 3 seeds averaged (8 for AIME),
recovery vs BF16 [src](https://huggingface.co/RedHatAI/Qwen3.8-27B-INT4/raw/main/README.md):

| Benchmark | BF16 | INT4 | Recovery |
|---|---:|---:|---:|
| IFEval (0-shot, prompt-level strict) | 92.24 % | 91.93 % | 99.67 % |
| MMLU-Pro (exact-match) | 84.46 % | 83.45 % | 98.81 % |
| GSM8K Platinum (strict-match) | 95.75 % | 96.77 % | 101.07 % |
| MATH-500 (pass@1) | 83.73 % | 83.33 % | 99.52 % |
| GPQA Diamond (pass@1) | 89.23 % | 87.88 % | 98.49 % |
| AIME 2025 (pass@1) | 95.42 % | 94.17 % | 98.69 % |

**AMD Quark AWQ INT4 W4A16** [src](https://huggingface.co/amd/Qwen3.8-27B-Quark-AWQ-INT4-W4A16):

| Benchmark | AWQ | BF16 base | Recovery |
|---|---|---|---:|
| GSM8K 5-shot, thinking (flex / strict) | 91.21 % / 90.67 % | 93.33 % / 93.33 % | 97.7 % |
| GSM8K 5-shot, non-thinking | 91.51 % / 90.37 % | 90.67 % / 89.76 % | 100.9 % |
| Wikitext perplexity (greedy) | 8.8250 | 8.4364 | 95.6 % |
| BFCL Overall Acc (single_turn only)* | 24.06 % | 24.38 % | 98.7 % |

\* AMD's own caveat: *"BFCL Overall Acc reflects only `single_turn` categories, not the full
Gorilla-leaderboard formula."* Sub-metrics: Non-Live AST 86.58 % (base 88.52), Live AST
81.57 % (83.05), Relevance 75.00 % (75.00), Irrelevance 72.47 % (72.22).

**SGLang's GSM8K sweep across every NVFP4/FP8/BF16 cell** — full 1319-question GSM8K on each
of 202 cells on v0.5.19, **93.18–95.15 %**; per-card: RTX PRO 6000 94.01–95.00 %, DGX Spark
94.16–95.07 %, RTX 5090 93.93–94.92 %
[src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md).

---

## 10. Published benchmarks

### 10.1 Throughput / latency — what actually exists

**Very little.** SGLang's own note is unusually candid: *"Every cell above… is measured on
v0.5.19. That is 202 cells, each one served and scored on the full 1319-question GSM8K
(93.18-95.15%). The serving envelope behind the pins is ISL 8192 / OSL 1024 at concurrency 1;
**throughput and acceptance-length numbers were not re-taken in that sweep**."*

What *is* published, all from
[SGLang's Qwen3.8-27B cookbook](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md)
unless noted, all `meas.`:

| Hardware | Engine | Precision | Config | Metric |
|---|---|---|---|---|
| RTX 5090 32 GB | SGLang v0.5.19 | NVFP4 | DFlash2, bfloat16 state | **4.92 ms median TPOT, accept length 4.29** — *"the best result on this card"* |
| RTX 5090 32 GB | SGLang v0.5.19 | NVFP4 | EAGLE/MTP, fp32 vs bf16 state | **152.9 vs 144.5 tok/s/user** |
| RTX 5090 32 GB | SGLang v0.5.19 | FP8 | EAGLE/MTP, fp32 vs bf16 state | **106.3 vs 116.1 tok/s/user** |
| RTX 5090 32 GB | SGLang v0.5.19 | NVFP4 | no spec, KV pool size | **97,280 KV tokens at bf16 state vs 68,588 at fp32** |
| 2 × RTX 5090, TP2 | vLLM `0.26.1rc1.dev608` | FP8 | 262 K ctx | 377,456 KV tokens, 14.28 GiB weights/GPU, MTP acceptance 0.771 [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) |
| 2 × RTX 5090, TP2 | vLLM | NVFP4 `Inferact` | 262 K ctx | 445,875 KV tokens, 12.02 GiB/GPU, acceptance **0.897** |
| 2 × RTX 5090, TP2 | vLLM | NVFP4 `unsloth` | 262 K ctx | **920,517 KV tokens**, 10.64 GiB/GPU, acceptance 0.788 |
| 1 × RTX 5090 | vLLM | NVFP4 | 32 K, `--enforce-eager` | 91,022 KV tokens → 135,926 with `--language-model-only` → 152,917 also capping `--max-num-seqs 8`; 76,458 with bf16 KV |
| 1 × Ascend 950PR, TP1 | vLLM Ascend 0.23.0 | ModelSlim INT8 W8A8 | 2048-token completion, 1 request, MTP on | **64 tok/s single-stream decode** [src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) |
| **GPU unstated** ⚠️ | SGLang (named) | unstated | DFlash2 vs autoregressive, **batch size 1** | *"2.7–3.4× the throughput"* — **marketing claim** [src](https://inco.ai/blog/dflash2/) |
| Ascend A3 Series, TP2 | SGLang | BF16 | `--attention-backend ascend --mamba-ssm-dtype bfloat16 --mamba-radix-cache-strategy extra_buffer`, RadixCache disabled both sides | DFlash2 vs baseline; zero-shot GSM8K greedy, `max_new_tokens=2048`, 128 examples, concurrency 1/2/4/8/16 [sglang#35629](https://github.com/sgl-project/sglang/pull/35629) |

**MLPerf Inference:** no Qwen3.8-27B submission found as of 2026-09-19.
**InferenceMAX / InferenceX (SemiAnalysis):** the benchmark covers Kimi K3 2.8T,
DeepSeek V4.1 Flash 552B, GLM 5.3 744B, MiniMax M3 428B and **Qwen 3.5 397B-A17B** across
TPUv7, MI355X, GB300 NVL72, GB200 NVL72, B200, H200, H100 and RTX Pro — **Qwen3.8-27B is not
in it** [src](https://inferencex.semianalysis.com/).

> **Everything published is single-stream or 2-GPU consumer-Blackwell.** There is no
> multi-concurrency throughput curve, no H200/B200/B300/GB300/MI355X throughput number, and no
> TTFT measurement for this model anywhere. The tables in §10.2 are therefore `est.`, not
> `meas.`, and should be replaced with `sglang.bench_serving` output before anyone commits
> capacity.

### 10.2 Roofline estimates (`est.`, METHODOLOGY §3/§4)

> The per-(model, GPU) fit / throughput / cost write-ups live at
> `research/models/qwen3827b/<gpu>.md` (`a100`, `h100`, `h200`, `b200`, `b300`, `gb300`,
> `rtx6000-pro`, `mi355x`) and are written in the next phase. What follows is the
> single-GPU summary this architecture doc owns, not a substitute for those.

**Fit — max concurrency on ONE GPU.** `usable = 0.90 × HBM`, activation workspace 4 GB,
FP8 KV (32,768 B/token), bf16 GDN state 78.45 MB **per slot**, with
`fixed_state_per_seq = S × 78.45 MB` (METHODOLOGY §2). HBM capacities are the METHODOLOGY §8
as-deployed pins. Each cell reads **`S = 5` (SGLang default) / `S = 1`
(`--disable-radix-cache` floor)** — see §5.4; vLLM's `S` is ⚠️ unpublished.

| GPU | HBM (GB) | dtype | Weights (GB) | KV+state budget (GB) | 8 K | 32 K | 128 K | 262 K | 1 M |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| A100 80 GB SXM | 80 | BF16 | 55.56 | 12.4 | 18 / 35 | 8 / 10 | 2 / 2 | 1 / 1 | **0 / 0** |
| | | INT4 | 19.45 | 48.5 | 73 / 139 | 33 / 42 | 10 / 11 | 5 / 5 | 1 / 1 |
| H100 80 GB SXM | 80 | BF16 | 55.56 | 12.4 | 18 / 35 | 8 / 10 | 2 / 2 | 1 / 1 | **0 / 0** |
| | | FP8 | 30.87 | 37.1 | 56 / 107 | 25 / 32 | 7 / 8 | 4 / 4 | 1 / 1 |
| | | INT4 | 19.45 | 48.5 | 73 / 139 | 33 / 42 | 10 / 11 | 5 / 5 | 1 / 1 |
| H200 141 GB SXM | 141 | BF16 | 55.56 | 67.3 | 101 / 194 | 45 / 58 | 14 / 15 | 7 / 7 | 1 / 1 |
| | | FP8 | 30.87 | 92.0 | 139 / 265 | 62 / 79 | 19 / 21 | 10 / 10 | 2 / 2 |
| | | INT4 | 19.45 | 103.4 | 156 / 298 | 70 / 89 | 22 / 23 | 11 / 11 | 2 / 3 |
| B200 180 GB | 180 | FP8 | 30.87 | 127.1 | 192 / 366 | 86 / 110 | 27 / 29 | 14 / 14 | 3 / 3 |
| | | NVFP4 | 21.92 | 136.1 | 205 / 392 | 92 / 118 | 29 / 31 | 15 / 15 | 3 / 3 |
| B300 HGX (268 GB) | 268 | NVFP4 | 21.92 | 215.3 | 325 / 620 | 146 / 186 | 45 / 49 | 23 / 24 | 6 / 6 |
| GB300 NVL72 (279 GB usable) | 279 | NVFP4 | 21.92 | 225.2 | 340 / 649 | 153 / 195 | 48 / 51 | 25 / 25 | 6 / 6 |
| RTX PRO 6000 SE 96 GB | 96 | BF16 | 55.56 | 26.8 | 40 / 77 | 18 / 23 | 5 / 6 | 2 / 3 | **0 / 0** |
| | | NVFP4 | 21.92 | 60.5 | 91 / 174 | 41 / 52 | 12 / 13 | 6 / 6 | 1 / 1 |
| MI355X 288 GB | 288 | FP8 | 30.87 | 224.3 | 339 / 646 | 153 / 194 | 47 / 51 | 24 / 25 | 6 / 6 |
| | | INT4 (Quark AWQ) | 19.45 | 235.7 | 356 / 679 | 160 / 204 | 50 / 53 | 26 / 27 | 6 / 6 |

**The `S` multiplier is not a footnote on this model.** At 8 K context it halves max
concurrency on every card (A100 BF16 35 → 18, H100 FP8 107 → 56, RTX PRO 6000 NVFP4
174 → 91). By 128 K the KV term dominates and the two columns converge. Any capacity plan
built on the `S = 1` column must actually pass `--disable-radix-cache` and accept the
re-prefill cost of §5.6.

Specs (all dense TFLOPS, METHODOLOGY §8): A100 80 GB SXM 2,039 GB/s, BF16 312
[src](https://www.nvidia.com/content/dam/en-zz/Solutions/Data-Center/a100/pdf/nvidia-a100-datasheet-nvidia-us-2188504-web.pdf);
H100 SXM 80 GB / 3.35 TB/s and H200 SXM 141 GB / 4.8 TB/s, both FP8 1,979 / BF16 989.5
[src](https://www.nvidia.com/en-us/data-center/h200/);
B200 180 GB (not 192) / 7.7 TB/s, FP4 9,000 / FP8 4,500 / BF16 2,250
[src](https://lenovopress.lenovo.com/lp2226-thinksystem-nvidia-b200-180gb-1000w-gpu);
**HGX / DGX B300 and AWS p6-b300: 268 GB per GPU = 2,144 GB per 8-GPU node** as deployed
(METHODOLOGY §8, [cloud-pricing.md §2](../../cross-cutting/cloud-pricing.md), from AWS's
published "2144GB HBM3e" node) / 8.0 TB/s, FP4 13,500 / FP8 4,500 / BF16 2,250 — NVIDIA's HGX
page prints "Total Memory 2.1 TB" ÷ 8 = 262.5 GB as a rounded-down nameplate and 144 | 108
PFLOPS FP4 sparse | dense ÷ 8 = 13.5 (re-fetched 2026-09-19)
[src](https://www.nvidia.com/en-us/data-center/hgx/); the 270 GB and 288 GB figures some
neoclouds print for HGX B300 are **wrong as deployed**;
**GB300 NVL72 is a different part and its figures are never merged with HGX B300's**:
288 GB per GPU nominal, **≈ 279 GB usable** (the METHODOLOGY §8 planning basis and the
figure used in this table), 8.0 TB/s, FP4 15,000 / FP8 5,000 / BF16 2,500 — NVIDIA publishes
only the rack figures "20 TB" and "576 TB/s" across 72 GPUs (= 277.8 GB per GPU)
[src](https://www.nvidia.com/en-us/data-center/gb300-nvl72/), Lambda lists 279 GB
[src](https://lambda.ai/blog/lambdas-mlperf-inference-v6.0-hardware-leap-software-maturity-research-breakthrough)
and SemiAnalysis 278 GB usable [src](https://inferencex.semianalysis.com/chips/gb300-nvl72);
that 1.2 GB spread moves no concurrency cell above;
RTX PRO 6000 **Server Edition** 96 GB GDDR7 / **1,597 GB/s** (the Workstation Edition's
1,792 GB/s is a different card), NVFP4 ≈ **2,000 dense** (the 4,000 on NVIDIA's page is the
sparse figure) [src](https://lenovopress.lenovo.com/lp2263.pdf) — Lenovo lp2263 re-fetched
2026-09-19 and it does print "Up to 1597 GB/s";
MI355X 288 GB / 8.0 TB/s, BF16 2,500 / FP8 5,000 / MXFP4 and MXFP6 10,100
[src](https://www.amd.com/en/products/accelerators/instinct/mi350/mi355x.html).
Cross-checked against this repo's [`research/gpus/`](../../gpus/) docs.

**Note the 1 M column.** Only B300, GB300 and MI355X can hold more than three concurrent 1 M
sequences on one GPU, and **no GPU in this table can hold even one 1 M sequence at BF16
weights with BF16 KV** (68.9 GB of KV alone). 1 M context on this model means NVFP4 weights,
FP8 KV, and a Blackwell-Ultra-or-later capacity class.

**Decode roofline** (METHODOLOGY §4,
`decode_step_time ≈ bytes_per_decode_step / (HBM_BW × MBU)`), MBU 0.70 Hopper/Ampere, 0.60
Blackwell, 0.50 ROCm per METHODOLOGY's planning defaults:

Per METHODOLOGY §3's consistency rule, a cell whose batch exceeds `max_concurrency(ctx)` from
the fit table above at `S = 5` is printed as **infeasible (KV)**, not as a number; the fit
table's `S = 1` column says which of them come back with `--disable-radix-cache`.

| GPU | Precision | bs=1 @8 K | bs=32 @8 K | bs=128 @8 K | bs=32 @32 K |
|---|---|---|---|---|---|
| A100 80 GB | INT4 | 13.9 ms / 72 tok/s | 23.2 ms / 1,381 | **infeasible (KV)** — max 73 | 41.2 ms / 776 |
| H100 80 GB | FP8 | 13.3 ms / 75 | 19.0 ms / 1,687 | **infeasible (KV)** — max 56 | **infeasible (KV)** — max 25 |
| H200 141 GB | FP8 | 9.3 ms / 107 | 13.2 ms / 2,417 | 25.4 ms / 5,041 | 20.9 ms / 1,531 |
| B200 180 GB | NVFP4 | 4.8 ms / 207 | 7.7 ms / 4,161 | 16.5 ms / 7,744 | 13.3 ms / 2,412 |
| B300 HGX (268 GB) | NVFP4 | 4.7 ms / 215 | 7.4 ms / 4,323 | 15.9 ms / 8,046 | 12.8 ms / 2,506 |
| GB300 NVL72 (279 GB) | NVFP4 | 4.7 ms / 215 | 7.4 ms / 4,323 | 15.9 ms / 8,046 | 12.8 ms / 2,506 |
| RTX PRO 6000 SE | NVFP4 | 23.3 ms / 43 | 37.1 ms / 863 | **infeasible (KV)** — max 91 | 64.0 ms / 500 |
| MI355X 288 GB | FP8 | 7.8 ms / 128 | 11.1 ms / 2,878 | 21.3 ms / 6,001 | 17.6 ms / 1,822 |

The B300 and GB300 rows are numerically identical **only** because decode here is
bandwidth-bound and both parts run at 8.0 TB/s. They are different parts: 268 vs 279 GB as
deployed, and FP4 13,500 vs 15,000 dense TFLOPS — which separates them in prefill (§11.3) and
in the fit table, not here.

**Sanity check against the one comparable measurement:** RTX 5090 (32 GB GDDR7, 1,792 GB/s —
the consumer card; do not confuse its 1,792 with the RTX PRO 6000 *Workstation* Edition's
identical-looking 1,792, nor either with the Server Edition's 1,597)
at NVFP4 + MTP, bs=1 — SGLang measures **152.9 tok/s/user**. This roofline, applied to the
5090's bandwidth at MBU 0.6 with an MTP accept length of ~4.3, predicts
`(21.92 + 0.27 + 0.16) GB / (1792 GB/s × 0.6) = 20.8 ms` per verify step → `4.3/0.0208 ≈ 207
tok/s`. The model over-predicts by ~35 %, which is the expected shortfall from draft-model
overhead and imperfect acceptance. **Treat every `est.` above as an optimistic ceiling and
derate ~30 %.**

⚠️ **TO BE VERIFIED:** TTFT. METHODOLOGY §4 asks for a TTFT column; computing it requires an
MFU measurement for the GDN prefill kernels, which nobody publishes. Estimation method: from
§6.2, `TTFT ≈ prefill_FLOPs(T) / (peak_FLOPS × MFU)`; at 4 K prompt on an H200 (1,979 dense
FP8 TFLOPS, MFU 0.30): `223.6 TFLOP / 594 TFLOPS = 377 ms`. SGLang's `--chunked-prefill-size
2048` guidance implies prefill chunks stall decode noticeably, so real TTFT under load will be
worse.

---

## 11. Vendor API pricing

### 11.1 Qwen Cloud (the model author's own service), list price

[src](https://www.qwencloud.com/models/qwen3.8-27b) — page `last-modified 2026-09-19`:

| Item | Price per 1 M tokens |
|---|---:|
| Input | **$0.50** |
| Output | **$3.00** |
| Input (implicit cache) | **$0.10** (20 % of input) |
| Explicit cache creation | **$0.625** |
| Explicit cache read | **$0.05** (10 % of input) |

Limits on the same page: Max Input **991 K**, Max Output **131 K**, Max Input (Thinking)
983 K, Max Reasoning **262 K**, Context **1 M**, TPM **5 M**, RPM **5 K**. Built-in tools:
`code_interpreter`, `i2i_search`, `pdf_parsing`, `t2i_search`, `web_extractor`, `web_search`.

The explicit-cache read at exactly 10 % of input price matches METHODOLOGY §6's
*"cached input tokens cost ~10 % of uncached (KV load only)"* assumption — good, though note
that on this model a cache hit also restores a GDN state checkpoint (§5.6), which the pricing
does not distinguish.

### 11.2 Third-party providers

[OpenRouter](https://openrouter.ai/api/v1/models/qwen/qwen3.8-27b/endpoints), fetched
2026-09-19. Aggregate listing for `qwen/qwen3.8-27b`: **$0.214 in / $2.55 out / $0.15
cache-read** per 1 M; a `:free` tier also exists at 262 K context.

| Provider | Context | Quant | Max out | $/1 M in | $/1 M out | $/1 M cache-read |
|---|---:|---|---:|---:|---:|---:|
| Darkbloom | 262,144 | **fp4** | 32,768 | **$0.100** | **$1.800** | — |
| DeepInfra | 262,144 | **bf16** | 235,929 | $0.150 | $1.875 | $0.037 |
| Phala | 262,144 | unknown | 235,929 | $0.199 | $2.075 | $0.042 |
| DekaLLM | 262,144 | unknown | 235,929 | $0.200 | $2.500 | $0.050 |
| Mancer 2 | 262,144 | fp8 | 235,929 | $0.200 | $2.500 | — |
| Reka | 262,144 | fp8 | 131,072 | $0.214 | $2.550 | $0.150 |
| Parasail | 262,144 | fp8 | 235,929 | $0.240 | $2.200 | $0.050 |
| Chutes | 262,144 | fp8 | 65,536 | $0.240 | $2.200 | $0.024 |
| AkashML | 262,144 | fp8 | 131,072 | $0.250 | $2.200 | $0.050 |
| Ionstream | 262,144 | fp8 | 65,536 | $0.280 | $2.550 | $0.100 |
| Io Net | 65,536 | fp8 | 58,982 | $0.300 | $2.800 | $0.180 |
| CoreWeave | 262,144 | fp8 | 235,929 | $0.400 | $3.000 | $0.150 |
| Novita | **1,000,000** | unknown | 131,072 | $0.420 | $3.000 | $0.085 |
| **Alibaba** | **1,000,000** | unknown | 131,072 | $0.425 | $2.550 | $0.085 |
| Cloudflare | 262,144 | unknown | 235,929 | $0.450 | $3.200 | $0.050 |
| Venice | 262,144 | fp8 | 65,536 | $0.450 | $3.200 | — |

Only **Novita and Alibaba serve the full 1 M window** — everyone else stops at the 262 K
native limit, consistent with §8.5's warning that static YaRN hurts short prompts. The fp8
cluster at $0.20–0.30 in / $2.20–2.55 out is the real market price.

### 11.3 Self-hosting cost vs. the API (METHODOLOGY §6)

`cost_per_1M_output = price_per_GPU_hr / (tokens_per_s_per_GPU × 3600) × 10⁶`, using §10.2's
`est.` throughput. Prefill rate = `peak_FLOPS × MFU / FLOPs_per_prefill_token` at 8 K chunks.
GPU-hour prices from this repo's [`cross-cutting/cloud-pricing.md`](../../cross-cutting/cloud-pricing.md)
(on-demand tier): Hyperstack [src](https://www.hyperstack.cloud/gpu-pricing), RunPod
[src](https://www.runpod.io/pricing), Crusoe [src](https://crusoe.ai/cloud/pricing/), and —
for **GB300 and MI355X, which none of those three publish a price for** — Oracle OCI, the
only hyperscaler with a public on-demand price for either
[src](https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?limit=2000&currencyCode=USD).

Every row re-checked against `cloud-pricing.md` §5.14's planning table (2026-09-19 sweep), by
name, never from a neighbouring GPU's row: **A100 80 GB SXM $1.60 Hyperstack · H100 SXM $3.90
Crusoe · H200 SXM $4.29 Crusoe · B200 HGX $6.00 Hyperstack · B300 HGX $7.40 Hyperstack ·
GB300 NVL72 $18.00 OCI `BM.GPU.GB300.4` · RTX PRO 6000 SE $1.85 Hyperstack · MI355X $8.60 OCI
`BM.GPU.MI355X.8`.** Two of these are the cheapest *reputable neocloud* rate while OCI's
hyperscaler list price for the same part is far higher — **OCI publishes B300 at $15.00**
(vs Hyperstack's $7.40) and B200 at $14.00 (vs $6.00); the GB300 and MI355X rows have no
neocloud alternative at all, so they rest on OCI's single published price and every cost
conclusion drawn from them inherits that (`cloud-pricing.md` §5.14 note).

Every batch row is checked against `max_concurrency(8 K)` from §10.2 at `S = 5` (METHODOLOGY
§3's consistency rule); rows that exceed it print **infeasible (KV)** instead of a cost. The
batches shown are METHODOLOGY §4's grid {1, 8, 32, 64, 128, 256}, taken up to the largest
feasible point on each card.

| GPU | Precision | $/GPU-hr | max conc. @8 K (`S`=5) | bs | TPOT `est.` | tok/s/GPU `est.` | **$/1 M output** | prefill tok/s `est.` | **$/1 M input** |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A100 SXM 80 GB | INT4 | $1.60 | 73 | 64 | 32.7 ms | 1,957 | **$0.227** | 2,286 | $0.194 |
| | | | | 128 | — | — | **infeasible (KV)** | | |
| | | | | 256 | — | — | **infeasible (KV)** | | |
| H100 SXM 80 GB | FP8 | $3.90 | 56 | 32 | 19.0 ms | 1,687 | **$0.642** | 10,874 | $0.100 |
| | | | | 128 / 256 | — | — | **infeasible (KV)** | | |
| H200 SXM 141 GB | FP8 | $4.29 | 139 | 64 | 17.3 ms | 3,702 | $0.322 | 10,874 | $0.110 |
| | | | | 128 | 25.4 ms | 5,041 | **$0.236** | | |
| | | | | 256 | — | — | **infeasible (KV)** | | |
| B200 180 GB | NVFP4 | $6.00 | 205 | 128 | 16.5 ms | 7,744 | **$0.215** | 41,211 | $0.040 |
| | | | | 256 | — | — | **infeasible (KV)** | | |
| B300 HGX (268 GB) | NVFP4 | $7.40 | 325 | 128 | 15.9 ms | 8,046 | $0.255 | 61,816 | $0.033 |
| | | | | 256 | 27.3 ms | 9,394 | **$0.219** | | |
| GB300 NVL72 (279 GB) | NVFP4 | **$18.00** | 340 | 128 | 15.9 ms | 8,046 | $0.621 | 68,685 | $0.073 |
| | | | | 256 | 27.3 ms | 9,394 | **$0.532** | | |
| RTX PRO 6000 SE | NVFP4 | $1.85 | 91 | 64 | 51.3 ms | 1,248 | **$0.412** | 9,227 | $0.056 |
| | | | | 128 / 256 | — | — | **infeasible (KV)** | | |
| MI355X 288 GB | FP8 | **$8.60** | 339 | 128 | 21.3 ms | 6,001 | $0.398 | 22,895 | $0.104 |
| | | | | 256 | 34.9 ms | 7,327 | **$0.326** | | |

**What the `S = 5` feasibility check removed, and what `S = 1` gives back.** The previous
version of this table priced A100 INT4 at bs=128/256 ($0.180 / $0.156), H100 FP8 at
bs=128/256 ($0.308 / $0.252), H200 at bs=256 ($0.194), B200 at bs=256 ($0.184) and
RTX PRO 6000 at bs=128/256 ($0.320 / $0.274) — none of which fit at `S = 5`. Dropping to
`S = 1` with `--disable-radix-cache` restores exactly four of them: A100 bs=128 ($0.180,
max 139), H200 bs=256 ($0.194, max 265), B200 bs=256 ($0.184, max 392) and RTX PRO 6000
bs=128 ($0.320, max 174). **A100 bs=256 ($0.156), H100 bs=128 and bs=256, and RTX PRO 6000
bs=256 are infeasible at every state-cache setting** — they were never available, and the
$0.156 headline below was a number for a batch the card cannot run.

**Two prices corrected against `cloud-pricing.md`, and they change the ranking.** An earlier
draft of this table priced GB300 at $7.40 (the Hyperstack **B300 HGX** rate) and MI355X at
$3.45 (Crusoe's **MI300X** rate) — neither vendor sells the part in question. The sourced
on-demand list prices are **Oracle OCI `BM.GPU.GB300.4` at $18.00/GPU-hr** and
**`BM.GPU.MI355X.8` at $8.60/GPU-hr**
[src](https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?limit=2000&currencyCode=USD).
CoreWeave, Crusoe, Azure and GCP all route GB300 and MI355X to "contact sales", and Lambda,
Nebius and RunPod publish neither. ⚠️ **TO BE VERIFIED:** a quote-based secondary figure of
**$2.95/GPU-hr for MI355X** circulates
[src](https://www.spheron.network/blog/amd-mi300x-mi355x-pricing-2026/); at that rate MI355X
would return to $0.112/1 M output and be the cheapest row in the table, so the MI355X verdict
is **entirely a procurement question, not a silicon one**. Do not plan against $2.95 without
a signed quote.

**Reading the table against METHODOLOGY §6's two operating points:**

- **Interactive SLO, TPOT ≤ 50 ms.** Every feasible row clears it except RTX PRO 6000 at
  bs=64 (51.3 ms, just over). **B200 NVFP4 at bs=128 (16.5 ms, $0.215/1 M out) is the
  cheapest SLO-compliant operating point**, with B300 HGX at bs=256 (27.3 ms, $0.219) and
  A100 INT4 at bs=64 (32.7 ms, $0.227) next.
- **Max throughput.** Once infeasible batches are struck, **B200 NVFP4 at $0.215/1 M output
  is the cheapest feasible row**, not A100. A100 INT4's $0.156 headline was a bs=256 row the
  card cannot hold at any `S`; its real floor is $0.227 at bs=64 (or $0.180 at bs=128 if you
  run `--disable-radix-cache`). B300 HGX at $0.219 is within noise of B200 and buys 1.6×
  the feasible concurrency. MI355X lands at **$0.326** — its 288 GB and 8 TB/s make it the
  best fit on paper for a model this size, but at OCI's $8.60/GPU-hr that fit does not reach
  the bill, and the software is unverified anyway (§8.4). **GB300 at $0.532–0.621 is the most
  expensive row in the table**: at $18.00/GPU-hr its 1.9× throughput over H200 does not pay
  for its 4.2× price, and since the model fits one GPU in every precision, a GB300 tray is
  the wrong shape for it regardless (§8.6). H100 FP8 at $0.642 is now the *worst* row, not
  because the silicon is slow but because its 80 GB holds only 56 concurrent 8 K sequences
  once the GDN state pool is paid for — the clearest illustration in this document that on a
  hybrid-GDN model capacity, not bandwidth, sets the cost floor.

**Sanity check against the API.** Self-hosted decode lands at **$0.22–0.64 per 1 M output
tokens** across the feasible rows; the market API price is **$1.80–3.20**, and Qwen's own
list is **$3.00**. Against Qwen's list that is a **4.7× (H100 FP8) to 14× (B200 NVFP4) gross
margin** on output tokens — normal at the cheap end, and it prices in the idle capacity,
reliability and the 1 M context that only two providers offer. On input the gap is much
narrower: $0.03–0.19 self-hosted vs $0.10–0.45 API. **If your traffic is input-heavy
(long documents, agentic context replay), the API is close to fairly priced; if it is
output-heavy (reasoning at `xhigh` effort), self-hosting wins decisively** — and note that
`reasoning_effort: xhigh` is the *default*, with a recommended 262 K reasoning budget.

⚠️ All self-hosted figures are `est.` from §10.2's roofline, which §10.2 shows over-predicts
by ~30 % on the one point where a measurement exists. Derate accordingly (throughput × 0.7,
so cost ÷ 0.7): the honest range is **$0.31–0.92 per 1 M output tokens**.

---

## 12. Open questions — consolidated ⚠️ TO BE VERIFIED

| # | Question | Why it is open | How to settle it |
|---|---|---|---|
| 1 | `qwen3_xml` (vLLM) vs `qwen3_coder` (SGLang) tool-call parser | Both engines pin a different parser for the same chat template (§1.4) | Send a tool-calling request to each; inspect whether `tool_calls` populates |
| 2 | SGLang's *"NVFP4 weights ~16.5 GB"* | Irreconcilable with every published NVFP4 tree (21.92 GB minimum). Probably the packed-weight payload only (§4) | Load the checkpoint and read `nvidia-smi` resident weight bytes |
| 3 | NVFP4/INT4 **KV cache** on this model | FlashInfer — the backend every recipe pins — has no FP4-KV column in SGLang's matrix; no checkpoint or recipe exists (§5.5) | Run `--kv-cache-dtype fp4` on a Triton/FA4/TRTLLM-MHA backend and re-score GSM8K |
| 4 | Prefix-cache hit-rate → TTFT curve (METHODOLOGY §5.4: 0/50/90 %) | Nobody has published one for a hybrid-GDN model, where a hit also restores a recurrent-state checkpoint (§5.6) | `sglang.bench_serving` with a synthetic shared-prefix dataset at three reuse rates |
| 5 | ReplaySSM (`--use-replayssm`) measured gain | The feature exists and directly targets the §6.3 state-write bottleneck, but no number is published for this model | A/B `--use-replayssm` at bs 128, ctx 8 K |
| 6 | Whether engines chunk the ViT attention at max resolution | A 65,536-patch image implies an 8.6 GB score matrix if unchunked (§6.4) | Send a 16.78 Mpx image and watch peak memory |
| 7 | **MI355X end-to-end** | vLLM lists `mi355x` under the FP8 variant's `supported_hardware` but publishes no command; SGLang's 27 B page has no AMD cell, though the 2.4 T sibling has verified MI355X cells (§8.4) | `SGLANG_USE_AITER=1`, `--attention-backend triton`, FP8 or `amd/…-Quark-AWQ-INT4-W4A16`, then GSM8K |
| 8 | **A100 end-to-end** | Fits at BF16/INT4 and the Triton GDN kernels claim Ampere support, but no vendor lists it and FP8 KV is unavailable (§8.4) | Serve `RedHatAI/Qwen3.8-27B-INT4` on SGLang with BF16 KV; expect ~2× the §5.3 KV footprint |
| 9 | H100 / B200 / B300 throughput | `supported_hardware` includes them; nobody has published a single tok/s number on any of them (§10.1) | `sglang.bench_serving --dataset-name random --random-input-len 8192 --random-output-len 1024` |
| 10 | TTFT at any concurrency, on any GPU | Requires a GDN-prefill MFU measurement nobody publishes (§10.2) | Measure, then back out MFU from §6.2 |
| 11 | Acceptance-vs-batch crossover for MTP/DFlash2 | No curve published; §7.4 gives only an estimate | Sweep bs at fixed ctx, reading `vllm:spec_decode_num_{accepted,draft}_tokens_total` |
| 12 | Dynamo / P/D disaggregation support | vLLM's `compatible_strategies` is `single_node_tp` only; unclear whether the GDN state transfers over NIXL (§8.1) | Attempt a 1P1D layout and inspect whether the recurrent state survives handover |
| 13 | Whether vLLM's KV-offload (`offloading_cpu`/`offloading_fs`, both "verified") offloads the **GDN state** too | Offloading only the KV half would leave the state pool as the concurrency ceiling (§8.6) | Enable offload, then measure `max_running_requests` at fixed ctx |
| 14 | MXFP4 checkpoint for AMD | MXFP4 is MI350X/MI355X's 10.1 PFLOPS fast path, and no MXFP4 Qwen3.8-27B checkpoint exists (§9.2) | Quantise with AMD Quark / llm-compressor and publish |
| 15 | DFlash2's *"2.7–3.4×"* claim | Marketing claim; the engine (SGLang) and operating point (batch size 1) *are* named, the **GPU is not**, and a bs=1 multiplier does not survive to serving concurrency (§7.2, §7.4) | Reproduce on a named GPU at a named concurrency |
| 18 | **GB300 and MI355X GPU-hour prices** | Oracle OCI is the *only* public on-demand list price for either ($18.00 and $8.60/GPU-hr); every other vendor is contact-sales, and a $2.95 MI355X quote circulates from a secondary source. The MI355X cost verdict in §11.3 flips on this number alone | Get a signed quote from a neocloud that actually racks MI355X (TensorWave, Crusoe) and a GB300 tray quote; re-run §11.3 |
| 16 | vLLM's `nvfp4_nvidia` "21.9 GiB" unit | The tree is 21.92 GB = 20.42 GiB; the unit label appears wrong (§4) | Cosmetic; file against the recipes repo |
| 19 | **vLLM's state-slot count `S`** for hybrid GDN models | SGLang publishes `S` = 5/4/3/1 by strategy (§5.4); vLLM publishes none, and `--mamba-cache-mode align` + `--enable-mamba-fine-grained-prefix-cache` both imply `S > 1`. Every §10.2 fit cell and every §11.3 feasibility verdict is engine-specific until this is known | Boot vLLM at a fixed `--max-num-seqs`, read the reported mamba-cache allocation from the startup log, divide by 78.45 MB |
| 17 | GB300 `--attention-backend` omission | SGLang's GB300 cells omit the flag while SM120 cells pin `flashinfer` — consistent with the SM100 constraint list, but not stated on the page (§8.4) | Read the server log's selected backend on a GB300 boot |

---

## Sources

**Model and checkpoint**
- https://huggingface.co/Qwen/Qwen3.8-27B
- https://huggingface.co/api/models/Qwen/Qwen3.8-27B
- https://huggingface.co/api/models/Qwen/Qwen3.8-27B/tree/main
- https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/README.md
- https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/config.json
- https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/generation_config.json
- https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/preprocessor_config.json
- https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/video_preprocessor_config.json
- https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/model.safetensors.index.json
- https://huggingface.co/Qwen/Qwen3.8-27B/resolve/main/model-0000{1..18}-of-00018.safetensors (safetensors headers, HTTP range reads)
- https://qwen.ai/blog?id=qwen3.8 (citation target in the model card; page is a JS shell and returned no content on fetch)

**Quantised and draft checkpoints**
- https://huggingface.co/Qwen/Qwen3.8-27B-FP8 · https://huggingface.co/Qwen/Qwen3.8-27B-FP8/raw/main/config.json
- https://huggingface.co/RadixArk/Qwen3.8-27B-NVFP4 · .../raw/main/config.json
- https://huggingface.co/RadixArk/Qwen3.8-27B-NVFP4-BF16-LMHead
- https://huggingface.co/nvidia/Qwen3.8-27B-NVFP4 · .../raw/main/config.json
- https://huggingface.co/Inferact/Qwen3.8-27B-NVFP4
- https://huggingface.co/unsloth/Qwen3.8-27B-NVFP4
- https://huggingface.co/RedHatAI/Qwen3.8-27B-INT4 · .../raw/main/README.md
- https://huggingface.co/RedHatAI/Qwen3.8-27B-NVFP4
- https://huggingface.co/amd/Qwen3.8-27B-Quark-AWQ-INT4-W4A16 · .../raw/main/README.md
- https://huggingface.co/RadixArk/Qwen3.8-27B-DSpark
- https://huggingface.co/incoai/Qwen3.8-27B-DFlash2 · https://huggingface.co/z-lab/Qwen3.8-27B-DFlash2
- https://huggingface.co/lued/Qwen3.8-27B-INT8-W8A16-MTP
- https://huggingface.co/cyankiwi/Qwen3.8-27B-AWQ-INT4 · https://huggingface.co/philbert440/Qwen3.8-27B-W4A16-AWQ
- https://huggingface.co/unsloth/Qwen3.8-27B-GGUF · https://huggingface.co/ggml-org/Qwen3.8-27B-GGUF · https://huggingface.co/bartowski/Qwen3.8-27B-GGUF · https://huggingface.co/lmstudio-community/Qwen3.8-27B-GGUF · https://huggingface.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF
- https://huggingface.co/lmstudio-community/Qwen3.8-27B-MLX-4bit · https://huggingface.co/mlx-community/Qwen3.8-27B-4bit
- https://huggingface.co/api/models?search=Qwen3.8-27B (derivative-repo enumeration)
- https://www.modelscope.cn/models/Eco-Tech/Qwen3.8-27B-w8a8

**Engines**
- https://recipes.vllm.ai/Qwen/Qwen3.8-27B
- https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md
- https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8.md (2.4 T sibling — AMD cells)
- https://docs.sglang.io/docs/advanced_features/attention_backend.md
- https://docs.sglang.io/docs/hardware-platforms/amd_gpu.md
- https://docs.sglang.io/docs/supported-models/generative_models.md
- https://docs.sglang.io/llms.txt
- https://docs.vllm.ai/en/latest/models/supported_models.html
- https://github.com/vllm-project/vllm/blob/main/vllm/config/cache.py
- https://github.com/vllm-project/vllm/blob/main/docs/features/quantization/llm_compressor/fp8.md
- https://github.com/vllm-project/vllm/issues/35922
- https://github.com/vllm-project/vllm/pull/34330 · /pull/52816 · /pull/48606 · /pull/46110
- https://github.com/sgl-project/sglang/pull/18467 · /pull/35629
- https://github.com/vllm-project/vllm-ascend/pull/14852
- https://github.com/NVIDIA/TensorRT-LLM/blob/main/docs/source/models/supported-models.md
- https://github.com/Dao-AILab/flash-attention
- https://github.com/flashinfer-ai/flashinfer
- https://lightseek.org/tokenspeed/recipes/models
- https://pypi.org/pypi/vllm/json · /sglang/json · /transformers/json · /flashinfer-python/json

**Benchmarks**
- https://inco.ai/blog/dflash2/
- https://inferencex.semianalysis.com/ (formerly inferencemax.ai — confirms Qwen3.8-27B is *not* covered)

**Pricing**
- https://www.qwencloud.com/models/qwen3.8-27b
- https://openrouter.ai/api/v1/models/qwen/qwen3.8-27b/endpoints
- https://openrouter.ai/api/v1/models
- https://www.hyperstack.cloud/gpu-pricing · https://www.runpod.io/pricing · https://crusoe.ai/cloud/pricing/

**GPU specifications**
- https://www.nvidia.com/content/dam/en-zz/Solutions/Data-Center/a100/pdf/nvidia-a100-datasheet-nvidia-us-2188504-web.pdf
- https://www.nvidia.com/en-us/data-center/h200/
- https://lenovopress.lenovo.com/lp2226-thinksystem-nvidia-b200-180gb-1000w-gpu
- https://www.nvidia.com/en-us/data-center/hgx/
- https://www.nvidia.com/en-us/data-center/gb300-nvl72/
- https://www.tomshardware.com/pc-components/gpus/nvidia-shares-blackwell-ultras-secrets-nvfp4-boost-detailed-and-pcie-6-0-support
- https://lenovopress.lenovo.com/lp2263.pdf
- https://www.amd.com/en/products/accelerators/instinct/mi350/mi355x.html
- https://rocm.docs.amd.com/en/latest/reference/gpu-arch/mi350.html
- https://rocm.blogs.amd.com/software-tools-optimization/nvfp4-mi355/README.html
- https://github.com/causalflow-ai/petit-kernel
- In-repo, cross-checked: [`research/gpus/`](../../gpus/) (a100, h100, h200, b200, b300, gb300, rtx6000-pro, mi355x), [`research/cross-cutting/cloud-pricing.md`](../../cross-cutting/cloud-pricing.md), [`research/METHODOLOGY.md`](../../METHODOLOGY.md)

---

## Verification log (2026-09-19)

Adversarial re-check of the 20 most consequential claims in this document. Every source was
opened independently — the document's own citation was never taken on trust — and every
derivation was re-run with `python3` from `config.json` and
[`METHODOLOGY.md`](../../METHODOLOGY.md). 26 claims checked: **21 CONFIRMED, 5 CORRECTED,
0 UNVERIFIABLE.**

### CONFIRMED

| # | Claim | Verified against |
|---|---|---|
| 1 | Model exists, ungated, Apache-2.0, `sha 1d4bf0f2…`, `lastModified 2026-08-14T15:00:01Z`, 7,358,662 downloads, `image-text-to-text` | [HF API](https://huggingface.co/api/models/Qwen/Qwen3.8-27B) — likes now read 15,687 vs the document's 15,686 snapshot; drift, not error |
| 2 | **Parameter count 27,781,427,952** and every sub-term (LM 26,895,998,464 · MLP 17,112,760,320 · GDN 5,562,051,072 · full-attn 1,677,729,792 · embed/lm_head 1,271,398,400 each · vision 460,730,096 · MTP 424,699,392 · norms 660,480) | Re-derived from `config.json` with `python3`; **delta 0 on every line**. Tensor count 1,199 confirmed from `weight_map` in [index.json](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/model.safetensors.index.json) |
| 3 | `attn_output_gate: true` ⇒ `q_proj` is `2 × 24 × 256` rows; head_dim 256, GQA 24:4; 48 GDN : 16 full-attention; `partial_rotary_factor 0.25` ⇒ 64 rotary dims; `mrope_section [11,11,10]` = 32 pairs | [config.json](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/config.json) + [model card](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/README.md) ("Rotary Position Embedding Dimension: 64", "16 × (3 × (Gated DeltaNet → FFN) → 1 × (Gated Attention → FFN))", "Number of Parameters: 27B", "248,320 (Padded)") |
| 4 | **KV 65,536 B/token BF16, 32,768 B/token FP8** (`2 × 4 × 256 × B × 16 layers`) | Recomputed; independently published by SGLang as "32.8 KB at fp8, 65.5 KB at bf16" [src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md) |
| 5 | **Per-sequence state 153,944,064 B fp32 / 78,446,592 B bf16** (150,994,944 recurrent + 2,949,120 conv) and `token_equiv` **4698 / 2394** | Recomputed exactly; SGLang publishes "153.9 MB at fp32, 78.4 MB at bf16" and `4698` / `2394` verbatim [src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md) |
| 6 | SGLang `S` = **5 / 4 / 3 / 1** for `extra_buffer` / `extra_buffer_lazy` / `no_buffer` / `--disable-radix-cache`; `D` = 4 (EAGLE/MTP), 8 (DFLASH), γ+1 = 8 (DSpark, γ=7 inferred), 0 with `--enable-linear-replayssm-spec` | The page's own slot expression: `slots = radixOff ? 1 : strategy === "no_buffer" ? 3 : 3 - (skipLock?1:0) + (overlapOff \|\| lazy ? 1 : 2)`; `drafts = !specOn \|\| replaySpec ? 0 : algo === "DSPARK" ? dsparkBlock+1 : …\|\| 4` [src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md) |
| 7 | §4 weight table: BF16 55.563 GB / 51.747 GiB; FP8 30.870 / 28.750; NVFP4 21.923 / 20.418; NVFP4-BF16-head 23.751; INT4 19.453 / 18.117 | Recomputed from METHODOLOGY §1, then checked against live HF file trees: 55.563 / 30.867 / 21.922 / 23.749 / 19.453 GB. Also Inferact 26.381, unsloth 23.418, AMD Quark 19.513, lued INT8 31.616, DSpark 3.715, DFlash2 3.849 — **every published disk size matches** |
| 8 | NVFP4 layer map: 193 NVFP4 tensors (`lm_head` + 64 × 3 MLP = 18.384 B) and 208 FP8 tensors (48 × 3 GDN + 16 × 4 attn = 7.214 B); `ignore: ["mtp*", "mtp.layers.0*"]`; FP8 checkpoint's `modules_to_not_convert` has **882** patterns and does **not** list `mtp.self_attn.{q,k,v,o}_proj` or `mtp.mlp` | [nvidia NVFP4 config.json](https://huggingface.co/nvidia/Qwen3.8-27B-NVFP4/raw/main/config.json), [FP8 config.json](https://huggingface.co/Qwen/Qwen3.8-27B-FP8/raw/main/config.json) — counts re-derived; INT4 quantised set 24,326,963,200 confirmed exactly |
| 9 | §5.3, §6.1, §6.2, §6.3, §6.4 tables (KV totals, 53.79 GFLOP/token, prefill TFLOP, bytes/decode-step, ViT FLOPs and token budgets) | All recomputed with `python3`; agree to rounding. The §6.4 "LM prefill" column is GEMM-only (`2 × 26.896e9 × T`) — consistent within the table |
| 10 | §10.2 fit table and decode roofline, every cell | Recomputed from METHODOLOGY §3/§4 at `usable = 0.90 × HBM`, 4 GB activation, FP8 KV, bf16 state, MBU 0.70/0.60/0.50 — **exact match on all 80 cells**. Implied prefill MFU is a consistent 0.254 on Blackwell/MI355X, 0.304 on Hopper |
| 11 | **H200 SXM 141 GB / 4.8 TB/s; 1,979 dense FP8 TFLOPS** | [nvidia.com/h200](https://www.nvidia.com/en-us/data-center/h200/) publishes 3,958 TFLOPS FP8 **"with sparsity"** ⇒ dense 1,979. No sparse/dense mixing found |
| 12 | **B200 9,000 and HGX B300 13,500 dense NVFP4 TFLOPS/GPU; B300 262.5 GB** | [nvidia.com/hgx](https://www.nvidia.com/en-us/data-center/hgx/): HGX B200 FP4 "144 \| 72 PFLOPS" sparse\|dense ÷ 8 = 9.0; HGX B300 "144 \| 108" ÷ 8 = 13.5; B300 total GPU memory 2.1 TB ÷ 8 = 262.5 GB. **The FLOPS half still stands; the 262.5 GB half was superseded on 2026-09-19** by METHODOLOGY §8's as-deployed pin of **268 GB / 2,144 GB per 8-GPU node** (AWS p6-b300) — NVIDIA's 2.1 TB is a rounded-down nameplate. See the Sweep log |
| 13 | **MI355X 288 GB HBM3E / 8 TB/s; 5.0 PF dense OCP-FP8; 10.1 PF dense MXFP4** | [ROCm MI350 arch](https://rocm.docs.amd.com/en/latest/reference/gpu-arch/mi350.html) ("288GB of total capacity", "up to 8 TB/s", FP8 5.0 PF dense / 10 PF sparse, MXFP6/MXFP4 10 PF dense). AMD's product page rounds the FP4 rate to 10.1; SemiAnalysis measures 10,066 TFLOP/s. The document's 10.1 is AMD's own figure |
| 14 | A100 80 GB SXM **2,039 GB/s**; RTX PRO 6000 Server Edition **1,597 GB/s** vs Workstation 1,792 (11 % gap) | [A100 datasheet](https://www.nvidia.com/content/dam/en-zz/Solutions/Data-Center/a100/pdf/nvidia-a100-datasheet-nvidia-us-2188504-web.pdf), [Lenovo lp2263](https://lenovopress.lenovo.com/lp2263.pdf); cross-checked against [`research/gpus/`](../../gpus/) |
| 15 | **vLLM FP8 needs compute capability ≥ 8.9** (so A100/sm_80 is weight-only at best) | [vllm fp8.md](https://github.com/vllm-project/vllm/blob/main/docs/features/quantization/llm_compressor/fp8.md): *"FP8 computation is supported on NVIDIA GPUs with compute capability >= 8.9 (Ada Lovelace, Hopper, Blackwell)."* |
| 16 | **FA2 forward supports head dim up to 256 on Ampere; FA3 is Hopper-only; FA4 targets Hopper *and* Blackwell** | [Dao-AILab/flash-attention](https://github.com/Dao-AILab/flash-attention): *"FlashAttention-3 is optimized for Hopper GPUs"*; *"FlashAttention-4 is written in CuTeDSL and optimized for Hopper and Blackwell GPUs (e.g. H100, B200)"*; head-dim-256 forward supported on Ampere, backward >192 needs A100/H100 |
| 17 | SGLang MHA matrix: **FP4 KV on FA4, Triton, Torch-native, FlexAttention and TRTLLM-MHA; ❌ on FlashInfer** — the backend every recipe pins | Raw table parsed from [attention_backend.md](https://docs.sglang.io/docs/advanced_features/attention_backend.md). The document's list is exactly right |
| 18 | SGLang hybrid-GDN platform constraints: SM100 ⇒ `triton`/`trtllm_mha`/`fa4` only (not FlashInfer); *"Other CUDA (Hopper, Ampere, etc.): auto-selection works; no special constraints"*; *"AMD (ROCm): `triton` recommended"*; and the six-condition FlashInfer GDN prefill fast path (Triton base, **BF16 state**, k/v head dim 128, chunked-prefill 1–8192) | [attention_backend.md](https://docs.sglang.io/docs/advanced_features/attention_backend.md) — all four quotes verbatim. Note SGLang contradicts *itself*: the cookbook says *"`trtllm_mha` is SM100-only"* while this page allows it on SM120 for `--decode-attention-backend`. The document quotes the cookbook correctly |
| 19 | **TRT-LLM `Qwen3_5ForConditionalGeneration`: KV Cache Reuse = No**, Chunked Prefill Untested, EPD Disagg Yes, Modality L+I+V — and the `mamba_state_config.periodic_snapshot_interval` footnote, verbatim | [TensorRT-LLM supported-models.md](https://github.com/NVIDIA/TensorRT-LLM/blob/main/docs/source/models/supported-models.md) — every cell matches, and `Qwen3_5MoeForCausalLM` does get "Yes / MTP / Yes" |
| 20 | Engine versions: min vLLM **0.17.0**, DFlash2 needs **≥ 0.28.0**; latest transformers **5.17.0**, vLLM **0.29.0**, SGLang **0.5.20**, FlashInfer **0.6.18.post1** | [vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) + PyPI JSON for all four packages, fetched 2026-09-19 |
| 21 | Qwen Cloud list price **$0.50 in / $3.00 out / $0.10 implicit cache / $0.625 cache-create / $0.05 cache-read**; 1 M context, 5 M TPM, 5 K RPM, six built-in tools. OpenRouter: all 16 provider rows, quant labels and cache-read prices | [qwencloud](https://www.qwencloud.com/models/qwen3.8-27b), [OpenRouter endpoints](https://openrouter.ai/api/v1/models/qwen/qwen3.8-27b/endpoints) — exact match on every figure (DeepInfra cache-read is $0.0375, printed as $0.037) |

Also re-confirmed in passing: the DFlash2 acceptance table (5.02/4.72/3.91/3.99/3.74 MTP,
4.36/3.92/3.30/3.51/3.01 DSpark, 5.46/5.28/4.39/4.79/4.10 DFlash2, means 4.28/3.62/4.80)
[src](https://inco.ai/blog/dflash2/); vLLM's RTX 5090 KV-token and acceptance figures
(377,456/0.771, 445,875/0.897, 920,517/0.788) and the MXFP4-on-NVIDIA statement
[src](https://recipes.vllm.ai/Qwen/Qwen3.8-27B); SGLang's 202-cell v0.5.19 GSM8K sweep and
its per-card ranges 94.01–95.00 / 94.16–95.07 / 93.93–94.92 %, the 4.92 ms TPOT at accept
length 4.29, 97,280 vs 68,588 KV tokens, 152.9/144.5 and 106.3/116.1 tok/s/user, the
"~16.5GB / ~28.5GB" hardware-fit bullet, the ~6.5-minute NVMe load and the 600 ms
chunk-stall figure [src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md);
the MI355X MXFP4 cell on the 2.4 T sibling page (`verified: true`, `SGLANG_USE_AITER=1`,
`--tp-size 8 --mem-fraction-static 0.9`, image `lmsysorg/sglang-rocm:v0.5.17-rocm720-mi35x-20260812`)
[src](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8.md); and that Qwen3.8-27B
is absent from InferenceX, whose models are Kimi K3 2.8T, DeepSeek V4.1 Flash 552B, GLM 5.3
744B, MiniMax M3 428B and Qwen 3.5 397B-A17B [src](https://inferencex.semianalysis.com/).

### CORRECTED

| # | Claim | old → new | Source |
|---|---|---|---|
| 22 | **GB300 GPU-hour price (§11.3)** — the single most consequential error in the document. $7.40 is Hyperstack's **B300 HGX** rate; no vendor sells GB300 at it | **$7.40 → $18.00/GPU-hr**; $/1 M output **$0.255 → $0.621**, $/1 M input **$0.030 → $0.073**. GB300 goes from mid-table to the **most expensive row** | [Oracle OCI price API](https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?limit=2000&currencyCode=USD), `BM.GPU.GB300.4` = $18.00; CoreWeave / Crusoe / Azure / GCP are all contact-sales |
| 23 | **MI355X GPU-hour price (§11.3)** — $3.45 is **Crusoe's MI300X** rate, a different generation and a different part | **$3.45 → $8.60/GPU-hr**; $/1 M output **$0.160 → $0.398** (bs=128) and **$0.131 → $0.326** (bs=256), $/1 M input **$0.042 → $0.104**. This **inverts the document's headline conclusion**: MI355X is no longer "the cheapest per token"; A100 INT4 at $0.156 is | [Oracle OCI price API](https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?limit=2000&currencyCode=USD), `BM.GPU.MI355X.8` = $8.60. A $2.95 secondary quote exists [src](https://www.spheron.network/blog/amd-mi300x-mi355x-pricing-2026/) and is now flagged ⚠️ in §11.3 |
| 24 | **Consequences of 22–23**: the cost summary | self-hosted output **"$0.13–0.32" → "$0.16–0.62"**; gross margin **"10–20×" → "4.8× (GB300) to 19× (A100 INT4)"**; derated honest range **"$0.17–0.45" → "$0.22–0.89"** (throughput × 0.7 ⇒ cost ÷ 0.7, applied consistently — the old range used two different multipliers) | Recomputed from the corrected table |
| 25 | **§1.1 file tree.** Repo file count and the safetensors byte total, plus the claim that `metadata.total_size` is "identical to the summed safetensors bytes" | **29 files → 32**; the trailing group **7 → 10**; safetensors bytes **55,562,855,904 → 55,563,006,776**. `total_size` is the *tensor payload* (27,781,427,952 × 2); the file bytes are 150,872 B larger — the 18 shards' JSON headers. GB/GiB columns and the no-padding conclusion are unchanged | [HF tree API, recursive](https://huggingface.co/api/models/Qwen/Qwen3.8-27B/tree/main?recursive=true) + [index.json](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/model.safetensors.index.json) |
| 26 | **FP8 quantised-parameter count (§3.2, §4).** Arithmetic slip of 512 params | **24,699,207,168 → 24,699,207,680** = 17,112,760,320 (MLP) + 1,677,721,600 (16 × self-attn linears) + 5,536,481,280 (48 × GDN projections) + 372,244,480 (MTP self-attn + MLP). Every GB/GiB figure is unaffected (both round to 30.870 GB) | Re-derived from `config.json`; the ignore list is [FP8 config.json](https://huggingface.co/Qwen/Qwen3.8-27B-FP8/raw/main/config.json) |

Two attribution fixes were also applied, with no numeric change: SGLang's ratio formula is
published as `r = (S + D) × token_equiv / L`, not in the expanded form the document labelled
"quoted verbatim" (the two are algebraically identical, and §5.4 now shows both); and the
DFlash2 *"2.7–3.4×"* claim **does** name its engine (SGLang) and operating point (batch
size 1) — only the GPU is missing, so §7.2, §10.1 and open question 15 were narrowed
accordingly. A new ⚠️ was added to the GB300 **279 GB** figure in §10.2: NVIDIA's cited page
publishes only "20 TB" across 72 GPUs (= 277.8 GB); 279 is Lambda's number, 278 is
SemiAnalysis's. And §8.2's GB300 row now records that SGLang marks its GB300 DFlash2 cells
`in-progress`, not `verified`.

### UNVERIFIABLE

None. Every claim selected for this pass resolved to CONFIRMED or CORRECTED against a
primary source. The document's 18 pre-existing ⚠️ **TO BE VERIFIED** markers (§12) were not
re-litigated — they are open by construction, and two more (GB300 memory, GB300/MI355X
pricing) were added by this pass.

---

## Sweep log (2026-09-19)

Systemic checklist pass against the amended [`METHODOLOGY.md`](../../METHODOLOGY.md) (§1
bytes-per-param table and mixed-precision rule, §2 state-slot multiplier `S`, §3 consistency
rule, §6 standard scenarios, §8 pinned GPU / model / price inputs). Every table touched was
recomputed with `python3` and the recomputed values pasted. The Verification log above is
historical and was not undone; where this pass supersedes one of its rows, the row says so
inline.

| Section | old → new | Reason | Source |
|---|---|---|---|
| §4, formula preamble | NVFP4 *"0.5 B + one FP8 E4M3 scale per 16 (**+6.25 %**)"* → **0.5625 B/param, +12.5 %**; MXFP4 *"+3.125 %"* → **0.53125 B/param, +6.25 %**; FP8 128×128 *"+0.024 %"* → **1.0002 B/param, +0.02 %**; INT4 g128 sym stated as **0.515625 B/param** with a note on METHODOLOGY's ≈0.53 asymmetric row; added an explicit "summed per tensor group, never a flat rate" sentence | The overhead percentages were taken against 1.0 B/param instead of the 0.5 B packed base, so NVFP4 and MXFP4 were both understated by 2×. No byte total changed — the table's scale columns (1.149 GB NVFP4, 0.575 GB MXFP4, 0.380 GB INT4) were already correct and still reconcile to the published trees | METHODOLOGY §1 bytes-per-param table; recomputed |
| §5.4 (new sub-block) | — → **"Which `S` to plan with"** table: SGLang `S = 5` default (4 / 3 / 1 on the other strategies), **vLLM ⚠️ TO BE VERIFIED** (`≥ 1` plus speculative slots per METHODOLOGY §2; `cache.py` exposes no slot count) | METHODOLOGY §2 requires `fixed_state_per_seq = S × state`, with `S` taken from the engine doc cited in architecture.md §5 or marked ⚠️. The doc had `S` documented but never applied | [SGLang cookbook](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md) (slot expression, re-fetched); [vllm config/cache.py](https://github.com/vllm-project/vllm/blob/main/vllm/config/cache.py); METHODOLOGY §2 |
| §10.2 fit table | Single set of cells at an unstated `S = 1` → **every cell recomputed as `S = 5` / `S = 1`**, e.g. A100 BF16 @8 K **35 → 18 / 35**, H100 FP8 @8 K **107 → 56 / 107**, H200 FP8 @8 K **265 → 139 / 265**, B200 NVFP4 @8 K **392 → 205 / 392**, RTX PRO 6000 NVFP4 @8 K **174 → 91 / 174**, MI355X INT4 @8 K **679 → 356 / 679** | METHODOLOGY §2/§3: the state term is `S ×` 78.45 MB, and SGLang's shipped default is `S = 5`. At 8 K the multiplier roughly halves concurrency on every card | Recomputed with `python3` from METHODOLOGY §3 (`usable = 0.90 × HBM`, 4 GB activation, FP8 KV 32,768 B/token, bf16 state 78,446,592 B per slot) |
| §10.2 fit table, B300 row | **"B300 HGX (262.5 GB)" → "B300 HGX (268 GB)"**; budget **210.3 → 215.3 GB**; concurrency @8 K/32 K/128 K/262 K/1 M **606 / 182 / 48 / 24 / 6 → 325 / 146 / 45 / 23 / 6 at `S`=5, and 620 / 186 / 49 / 24 / 6 at `S`=1** | METHODOLOGY §8 pins HGX / DGX B300 and AWS p6-b300 at **268 GB per GPU = 2,144 GB per 8-GPU node**; NVIDIA's "2.1 TB" is a rounded-down nameplate and 270 / 288 GB are wrong as deployed | METHODOLOGY §8; [cloud-pricing.md §2](../../cross-cutting/cloud-pricing.md); [AWS p6-b300 launch blog](https://aws.amazon.com/blogs/aws/accelerate-large-scale-ai-applications-with-the-new-amazon-ec2-p6-b300-instances) ("2144GB HBM3e"); [NVIDIA HGX](https://www.nvidia.com/en-us/data-center/hgx/) re-fetched 2026-09-19 |
| §10.2 fit table, GB300 row | *"GB300 NVL72 (279 GB)"* + a standalone ⚠️ **TO BE VERIFIED** on the 279 → **"GB300 NVL72 (279 GB usable)"**, ⚠️ replaced by the METHODOLOGY §8 statement **288 GB per GPU nominal, ≈ 279 GB usable**, with the NVIDIA-rack / Lambda / SemiAnalysis spread kept as corroboration | The figure was not unverified, it was the pinned planning basis; and the doc must not merge HGX B300 and GB300 NVL72 figures | METHODOLOGY §8; [gpus/gb300.md §2](../../gpus/gb300.md); [NVIDIA GB300 NVL72](https://www.nvidia.com/en-us/data-center/gb300-nvl72/) |
| §10.2 fit table, column headers | *"HBM"*, *"Weights"*, *"KV+state budget"* (units only inside cells) → **"HBM (GB)"**, **"Weights (GB)"**, **"KV+state budget (GB)"** | METHODOLOGY §1 units convention: report GiB unless the column says GB. These columns are 10⁹-byte vendor capacities, so they are now labelled GB explicitly | METHODOLOGY §1 |
| §10.2 spec footnote | Bandwidth-only list → **dense TFLOPS added for every part** (A100 BF16 312; H100/H200 FP8 1,979 / BF16 989.5; B200 FP4 9,000 / FP8 4,500 / BF16 2,250; HGX B300 FP4 13,500 / FP8 4,500 / BF16 2,250; GB300 FP4 15,000 / FP8 5,000 / BF16 2,500; RTX PRO 6000 SE ≈ **2,000 dense** with "4,000 is sparse" spelled out; MI355X BF16 2,500 / FP8 5,000 / MXFP4 **and MXFP6** 10,100) | METHODOLOGY §7: no silent sparse TFLOPS. The RTX PRO 6000 dense/sparse split in particular was implied by the doc's own prefill arithmetic but never stated | METHODOLOGY §8; [NVIDIA HGX](https://www.nvidia.com/en-us/data-center/hgx/) ("144 \| 108 PFLOPS" sparse\|dense ÷ 8 = 13.5, re-fetched); [AMD MI355X](https://www.amd.com/en/products/accelerators/instinct/mi350/mi355x.html) |
| §10.2 spec footnote, RTX PRO 6000 | 1,597 GB/s (already correct) → **kept, and the citation re-fetched and confirmed to contain the number**; "Workstation 1,792 is a different card" made explicit | Checklist item 8 and the citation-integrity pass (a sibling doc had cited an NVIDIA model card that does not carry this figure) | [Lenovo lp2263.pdf](https://lenovopress.lenovo.com/lp2263.pdf), re-fetched 2026-09-19: *"Memory Bandwidth — Up to 1597 GB/s"*, *"96 GB GDDR7"* |
| §10.2 decode roofline | Merged row **"B300 / GB300"** → **two rows, "B300 HGX (268 GB)" and "GB300 NVL72 (279 GB)"**, with a note that the numbers coincide only because both run at 8.0 TB/s and that they differ in capacity and FP4 rate | METHODOLOGY §8 / checklist item 2: HGX B300 and GB300 NVL72 figures are never merged | METHODOLOGY §8 |
| §10.2 decode roofline | A100 INT4 bs=128 **"51.8 ms / 2,472"**, H100 FP8 bs=128 **"36.4 ms / 3,518"**, H100 FP8 bs=32 @32 K **"30.0 ms / 1,068"**, RTX PRO 6000 bs=128 **"79.7 ms / 1,606"** → **infeasible (KV)** with the max concurrency printed (73 / 56 / 25 / 91) | METHODOLOGY §3 consistency rule: a throughput table may only carry batch rows ≤ `max_concurrency(ctx)` at that GPU count | Recomputed; §10.2 fit table at `S = 5` |
| §11.3 preamble | Vendor list in prose only → **per-row attribution by name** (A100 $1.60 Hyperstack · H100 $3.90 Crusoe · H200 $4.29 Crusoe · B200 $6.00 Hyperstack · B300 HGX $7.40 Hyperstack · GB300 $18.00 OCI `BM.GPU.GB300.4` · RTX PRO 6000 SE $1.85 Hyperstack · MI355X $8.60 OCI `BM.GPU.MI355X.8`), plus the note that **OCI lists B300 at $15.00 and B200 at $14.00** and that the GB300/MI355X rows rest on a single published seller | Checklist item 7: every remaining price row re-checked against `cloud-pricing.md` after the fact-checker's two neighbouring-row substitutions. **All eight rows matched by name; no further substitution found.** No "not retrievable" / "WebSearch unavailable" statement exists in this document | [cloud-pricing.md §5.14](../../cross-cutting/cloud-pricing.md) planning table and §5.1–5.13 vendor rows, re-read 2026-09-19 |
| §11.3 cost table | Flat bs ∈ {128, 256} grid → **`max conc. @8 K (S=5)` column added** and rows taken up to the largest feasible batch: A100 INT4 **bs=128 $0.180 / bs=256 $0.156 → infeasible (KV)**, new **bs=64 32.7 ms / 1,957 tok/s / $0.227**; H100 FP8 **bs=128 $0.308 / bs=256 $0.252 → infeasible**, new **bs=32 19.0 ms / 1,687 / $0.642**; H200 FP8 **bs=256 $0.194 → infeasible**, new **bs=64 17.3 ms / 3,702 / $0.322**; B200 NVFP4 **bs=256 $0.184 → infeasible**; B300 HGX and GB300 keep 128 and 256 (GB300's **bs=256 27.3 ms / 9,394 / $0.532** added); RTX PRO 6000 **bs=128 $0.320 / bs=256 $0.274 → infeasible**, new **bs=64 51.3 ms / 1,248 / $0.412**; MI355X keeps both | METHODOLOGY §3 consistency rule applied to a cost table; batches taken from METHODOLOGY §4's {1, 8, 32, 64, 128, 256} grid | Recomputed with `python3` from §6.3 bytes-per-step, §10.2 MBU and the `cloud-pricing.md` prices above |
| §11.3, new paragraph | — → **"What the `S = 5` feasibility check removed, and what `S = 1` gives back"**: four rows return under `--disable-radix-cache` (A100 bs=128 $0.180, H200 bs=256 $0.194, B200 bs=256 $0.184, RTX PRO 6000 bs=128 $0.320); **A100 bs=256, H100 bs=128/256 and RTX PRO 6000 bs=256 are infeasible at every `S`** | So the struck rows are auditable rather than silently deleted, and so the reader can see which are an engine-setting away | Recomputed at `S = 1` and `S = 5` |
| §11.3, interactive-SLO bullet | *"H200 FP8 at bs=256 (41.6 ms, $0.194) and B200 NVFP4 at bs=256 (28.3 ms, $0.184) are the sweet spots"* → **"B200 NVFP4 at bs=128 (16.5 ms, $0.215) is the cheapest SLO-compliant operating point"**, then B300 HGX bs=256 ($0.219) and A100 INT4 bs=64 ($0.227) | Both named sweet spots were bs=256 rows that are infeasible at `S = 5` | Recomputed |
| §11.3, max-throughput bullet | *"A100 INT4 at $0.156/1 M output is the cheapest per token … the surprise winner"* → **"B200 NVFP4 at $0.215 is the cheapest feasible row, not A100"**; A100's real floor stated as **$0.227 (bs=64)**, or $0.180 at bs=128 under `--disable-radix-cache`; H100 FP8 **$0.642** named as the worst row, with the capacity-not-bandwidth explanation | The $0.156 headline rested entirely on a batch the card cannot hold at any state-cache setting — METHODOLOGY §3's "§0/§12-style summary claims that rested on such rows are revised" | Recomputed |
| §11.3, API sanity check | self-hosted output **"$0.16–0.62" → "$0.22–0.64"**; gross margin **"4.8× (GB300) to 19× (A100 INT4)" → "4.7× (H100 FP8) to 14× (B200 NVFP4)"**; derated honest range **"$0.22–0.89" → "$0.31–0.92"** | Consequence of the feasibility pass; the ÷0.7 derate is applied consistently, as the fact-checker's row 24 established | Recomputed from the corrected table |
| §7.2 (new ⚠️ note) | — → the DSpark **MBPP 3.51** in the DFlash2 table is inco.ai's *measured* per-request mean acceptance length for Qwen3.8-27B, and is **not** the DeepSeek-V4.1-Flash MI355X `synthetic_acceptance_length: 3.51` benchmark constant; cross-linked both ways | Checklist item 11, applied without over-claiming: re-fetching inco.ai found the 3.51 in a real benchmark table with no `rejection_sample_method` and no "synthetic" anywhere on the page. The coincident value is a cross-doc contamination hazard, so it is labelled as such | [inco.ai/blog/dflash2](https://inco.ai/blog/dflash2/), re-fetched 2026-09-19 (table 4 verbatim: MTP 5.02/4.72/3.91/3.99/3.74, DSpark 4.36/3.92/3.30/**3.51**/3.01, DFlash2 5.46/5.28/4.39/4.79/4.10, means 4.28/3.62/4.80); [models/deepseek41f/architecture.md §7.3](../deepseek41f/architecture.md); [serving-optimizations.md §2.7](../../cross-cutting/serving-optimizations.md) |
| §8.4, H100 paragraph | *"12.4 GB for KV+state at BF16 (§3 fit table)"* → same, but pointing at **§10.2** and quantified: **18 concurrent 8 K sequences at `S = 5`, 56 even on FP8** | Dangling cross-reference (there is no §3 fit table in this document), and the sentence now carries the number that makes the point | Recomputed |
| §8.4, MI355X paragraph | *"the single highest-concurrency card in §3's fit table"* → **"in §10.2's fit table (356 concurrent 8 K sequences at INT4, `S = 5`)"** | Same dangling cross-reference | Recomputed |
| §10.2 (new pointer) | — → one-line pointer to `research/models/qwen3827b/<gpu>.md` for the per-(model, GPU) fit / throughput / cost write-ups | Checklist item 15 — this architecture doc keeps its single-GPU summary and does not grow into the per-GPU docs' territory | METHODOLOGY / research tree layout |
| §0 one-line summary | *"fixed 153.9 MB recurrent state per running sequence … binding constraint below ~4.7 K tokens"* → **"153.9 MB per *slot*, `S` = 5 slots per request on SGLang ⇒ 769.7 MB fp32 / 392.2 MB bf16 per request"**, with the ~23.5 K crossover and the pointer to what `S` does in §10.2 / §11.3 | METHODOLOGY §2: the headline number was the per-slot figure presented as the per-request one, which is the single claim most likely to be lifted out of this document | §5.2, §5.4, recomputed |
| §5.2, crossover callout | *"Below ~4.7 K tokens … the recurrent state costs more memory than the KV cache"* → *"one state **slot** costs more"*, plus the per-request crossover **`S` × token_equiv = 23,490 tokens fp32 / 11,970 bf16 at `S` = 5** | Same per-slot / per-request conflation. SGLang's published 4698 / 2394 are per slot and are left exactly as published | Recomputed; [SGLang cookbook](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md) |
| §5.3, table preamble | — → **"the table below is `S` = 1"**, with the formula written as `S × 153.94 MB` / `S × 78.45 MB` and a worked example (8 K fp32: 0.422 → 0.884 GB at `S` = 5) | METHODOLOGY §2 form of `fixed_state_per_seq`; the table's own numbers are unchanged and correct at `S` = 1 | METHODOLOGY §2 |
| §6.3 (new note) | — → **the `S` multiplier is a memory term, not a bandwidth one**: only the live slot is read/written per step, so decode traffic uses `state_bytes` once (×2) regardless of `S` | Prevents the §10.2 `S` correction from being mis-propagated into §6.3's bytes-per-decode-step table, which is unchanged and correct | METHODOLOGY §2/§4 |
| §12 | — → new open question **19, vLLM's state-slot count `S`** | The ⚠️ raised in §5.4 belongs in the consolidated list, since every §10.2 fit cell and §11.3 feasibility verdict is engine-specific until it is answered | §5.4 |
| Verification log row 12 | *"B300 262.5 GB"* CONFIRMED → **row kept, annotated: the FLOPS half stands, the 262.5 GB half is superseded by METHODOLOGY §8's 268 GB / 2,144 GB per node** | Earlier corrections are not undone, but a superseded figure must not be followed out of the log | METHODOLOGY §8; AWS p6-b300 |

### Re-verified, no change

- **Units (checklist 1).** All memory arithmetic in §4, §5.3, §6.3, §10.2 and §11.3 is done in
  bytes and reported in columns that name their unit; §1.1 and §4 carry both GB (10⁹) and GiB
  (2³⁰) columns and they reconcile. The only unlabelled columns were §10.2's, now fixed. No
  GB/GiB slip found in a KV budget, weight size or concurrency denominator.
- **KV arithmetic (checklist 5).** §6.3's decode bytes already scale as
  `batch × ctx × kv_bytes_per_token` (plus this model's `batch × 2 × state_bytes`), not as a
  flat per-step constant. Nothing divides KV bytes by `n_kv_heads`. This model has **no**
  sliding-window layer (§5.5) and no MLA, so the `W`-cap and MLA rows of METHODOLOGY §2 do not
  apply. KV 65,536 B/token BF16 / 32,768 FP8 re-derived and re-confirmed against SGLang's
  independently published "32.8 KB / 65.5 KB".
- **Checkpoint totals (checklist 4).** BF16 **55.56 GB** matches METHODOLOGY §8's pinned
  Qwen3.8-27B row exactly, as do FP8 30.87 / NVFP4 21.92 / INT4 19.45 against the live HF file
  trees. The mixed-precision sum-per-tensor-group rule was already being followed.
- **B200 = 180 GB** (not 192) throughout — already correct.
- **Engine releases (checklist 12).** Re-verified via
  `curl -sL https://api.github.com/repos/<org>/<repo>/releases/latest` on 2026-09-19:
  vLLM **v0.29.0** (2026-09-09), SGLang **v0.5.20** (2026-09-18), TensorRT-LLM **v1.2.1**
  (PyPI stable 1.2.1), FlashInfer **v0.6.18.post1** (2026-09-05), transformers **v5.17.0**
  (2026-09-09). §8.1's four version cells all match; the document lists no release *dates*, so
  the off-by-one-release date pattern found elsewhere does not occur here.
- **TokenSpeed (checklist 10).** This document makes no claim about TokenSpeed's
  DeepSeek-V4.1-Flash support; its only TokenSpeed row is the Qwen3.8-27B recipe. Nothing to
  fix.
- **Checklist items 9 and 14** (the B200-vs-H200 gap on DeepSeek-R1, and DeepSeek-V4.1-Flash's
  MXFP4 routed experts) do not appear in this document.

### Citation integrity (checklist 13)

The five most load-bearing citations were re-fetched and checked to contain the claim:

| Citation | Claim it carries | Result |
|---|---|---|
| [`config.json`](https://huggingface.co/Qwen/Qwen3.8-27B/raw/main/config.json) | Every shape in §2 and the whole §3 parameter derivation | ✅ re-fetched: 64 layers, hidden 5120, inter 17408, vocab 248320, `full_attention_interval` 4, heads 24/4, `head_dim` 256, `attn_output_gate` true, `partial_rotary_factor` 0.25, GDN 48/16 heads × 128/128, conv kernel 4, `mamba_ssm_dtype` float32, `mtp_num_hidden_layers` 1, `tie_word_embeddings` false, vision depth 27 / 1152 / 4304, `deepstack_visual_indexes` `[]` — all exact |
| [SGLang cookbook](https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.8-27B.md) | §5.2 state 153.9 / 78.4 MB, §5.4 `token_equiv` 4698 / 2394 and the `S` slot expression, §7.2 4.92 ms TPOT at accept 4.29, §10.1 97,280 vs 68,588 KV tokens, the "~16.5GB / ~28.5GB" bullet | ✅ all present verbatim on the live page |
| [vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B) | §7.2 / §10.1 KV-token and acceptance figures, `min_vllm_version` 0.17.0, the MXFP4-on-NVIDIA statement | ✅ 377,456 · 445,875 · 920,517 · 0.771 · 0.897 · 0.788 · 91,022 · 135,926 · 152,917 · 76,458 · `0.17.0` all present |
| [Lenovo lp2263.pdf](https://lenovopress.lenovo.com/lp2263.pdf) | §8.4 / §10.2 RTX PRO 6000 **Server Edition 1,597 GB/s**, 96 GB GDDR7 | ✅ the PDF prints *"1597 GB/s of memory bandwidth"* and *"Memory Bandwidth — Up to 1597 GB/s"*. (This is the citation class that failed in a sibling doc, where an NVIDIA model card was cited for a bandwidth it does not contain; this one holds) |
| [inco.ai DFlash2](https://inco.ai/blog/dflash2/) | §7.2 acceptance table and the "2.7–3.4×" claim | ✅ table 4 matches cell for cell; the claim reads *"SGLang serves at 2.7–3.4× the throughput of autoregressive decoding at batch size 1"* — engine and operating point named, GPU still absent, as §7.2 already says |

Also re-fetched in passing: [NVIDIA HGX](https://www.nvidia.com/en-us/data-center/hgx/)
("FP4 Tensor Core 144 PFLOPS | 108 PFLOPS" for HGX B300 and "144 | 72" for HGX B200,
"Total Memory 2.1 TB"), confirming the dense ÷8 figures of 13,500 and 9,000 and the
rounded-down 262.5 GB nameplate that METHODOLOGY §8 supersedes with 268 GB.
