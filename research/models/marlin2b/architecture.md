# NemoStation/Marlin-2B — architecture and inference profile

Research date: **2026-09-19**. Formulas, legend and rules per [`research/METHODOLOGY.md`](../../METHODOLOGY.md).

> **Research-conditions note (read first).** Two constraints shaped this document and are
> disclosed up front rather than buried in §12:
>
> 1. **`WebSearch` was unavailable** — the session-wide budget (200/200 calls) was already
>    exhausted by sibling agents before this agent ran. All evidence below therefore comes from
>    **direct `WebFetch` / `curl` against primary URLs** (HF API, HF raw files, GitHub raw source,
>    vendor spec pages). Nothing here is search-discovered. The practical cost is in §10 and §11:
>    third-party blog benchmarks, InferenceMAX/SemiAnalysis pages and provider price lists that
>    would normally be found by search are absent, and are marked ⚠️ TO BE VERIFIED.
> 2. **The canonical repo is gated** (`gated: "auto"`). `https://huggingface.co/NemoStation/Marlin-2B/raw/main/*`
>    returns HTTP 401 for every file. The rendered **model-card page is public** and was fetched, and an
>    **ungated byte-level mirror** — [`lunahr/Marlin-2B-ungated`](https://huggingface.co/lunahr/Marlin-2B-ungated)
>    — was used for `config.json`, `modeling_marlin.py`, `processor_config.json` and the raw
>    safetensors headers. The mirror is verified against the gated original in §1.3; where it
>    differs, that is stated.
>
> **Adversarial verification pass, 2026-09-19.** An independent fact-check re-derived every
> parameter, KV, state, FLOP and cost figure in this document with `python3` from `config.json`,
> and independently fetched primary sources for the hardware, kernel and pricing claims rather than
> trusting the citations below. **22 claims checked: 15 confirmed, 7 corrected, 0 unfounded.** The
> corrections are marked inline and listed in full in the **Verification log** at the end. The
> largest was B300/GB300 prefill throughput (§10.3, §11.2). WebSearch was exhausted for the
> verification pass too, so the search-dependent gaps in §10.2 and §11.1 remain open — but several
> items previously blamed on WebSearch (GPU-hour prices, the NVFP4 DeepSeek checkpoint, RTX PRO
> 6000 compute capability) turned out to be resolvable by direct fetch and are now resolved.

---

## 1. Identity

### 1.1 Repository facts

| Field | Value | Source |
|---|---|---|
| HF repo | `NemoStation/Marlin-2B` | [HF API](https://huggingface.co/api/models/NemoStation/Marlin-2B) |
| Author | NemoStation | [HF API](https://huggingface.co/api/models/NemoStation/Marlin-2B) |
| License | Apache-2.0 | [HF API cardData](https://huggingface.co/api/models/NemoStation/Marlin-2B) |
| Created | **2026-05-13 16:23:12 UTC** | `createdAt`, [HF API](https://huggingface.co/api/models/NemoStation/Marlin-2B) |
| Last modified | 2026-05-30 08:55:53 UTC | `lastModified`, [HF API](https://huggingface.co/api/models/NemoStation/Marlin-2B) |
| Commit SHA | `fd111fca4fc7897876fb0d7e9df22ca5ac8ab965` | [HF API](https://huggingface.co/api/models/NemoStation/Marlin-2B) |
| Pipeline tag | `video-text-to-text` | [HF API](https://huggingface.co/api/models/NemoStation/Marlin-2B) |
| Architectures | `["MarlinForConditionalGeneration"]` | [HF API](https://huggingface.co/api/models/NemoStation/Marlin-2B) |
| `model_type` | `qwen3_5` | [HF API](https://huggingface.co/api/models/NemoStation/Marlin-2B) |
| `auto_map` | `{"AutoModelForCausalLM": "modeling_marlin.MarlinForConditionalGeneration"}` | [HF API](https://huggingface.co/api/models/NemoStation/Marlin-2B) |
| Base model | `Qwen/Qwen3.5-2B` (finetune) | [HF API tags](https://huggingface.co/api/models/NemoStation/Marlin-2B) |
| Gated | `auto` — self-serve form (name, affiliation, use case) | [HF API cardData](https://huggingface.co/api/models/NemoStation/Marlin-2B) |
| Downloads (30 d) | 4,912 | [HF API](https://huggingface.co/api/models/NemoStation/Marlin-2B) |
| Likes | 593 | [HF API](https://huggingface.co/api/models/NemoStation/Marlin-2B) |
| Checkpoint dtype | **BF16, unquantised — all 618 tensors** | safetensors header, §3.1 |
| Papers referenced | arXiv [2501.00513](https://arxiv.org/abs/2501.00513) (CaReBench), [2407.00634](https://arxiv.org/abs/2407.00634) (Tarsier), [2512.14698](https://arxiv.org/abs/2512.14698) (TimeLens) | [model card](https://huggingface.co/NemoStation/Marlin-2B) |
| Recipe paper | ⚠️ **Not published.** Card says "✏️ Recipe paper coming soon." | [model card](https://huggingface.co/NemoStation/Marlin-2B) |

### 1.2 Files and on-disk size (HF API tree)

Retrieved from [`/api/models/NemoStation/Marlin-2B/tree/main`](https://huggingface.co/api/models/NemoStation/Marlin-2B/tree/main) — the tree endpoint is public even though the file bodies are gated.

| File | Bytes | Note |
|---|---:|---|
| `model-00001-of-00002.safetensors` | 4,999,157,736 | LFS |
| `model-00002-of-00002.safetensors` | 444,519,488 | LFS |
| `tokenizer.json` | 19,989,325 | full vocab + merges inline |
| `model.safetensors.index.json` | 55,830 | |
| `modeling_marlin.py` | 23,098 | custom code (`trust_remote_code`) |
| `README.md` | 11,222 | |
| `LICENSE` | 11,358 | Apache-2.0 |
| `chat_template.jinja` | 7,755 | |
| `config.json` | 2,819 | |
| `processor_config.json` | 1,191 | |
| `tokenizer_config.json` | 1,165 | |
| `NOTICE` | 618 | |
| `preprocessor_config.json` | 390 | |
| `generation_config.json` | 137 | |
| `.gitattributes` | 2,020 | |
| **Weights subtotal** | **5,443,677,224** | **5.444 GB / 5.070 GiB** |
| `usedStorage` (whole repo) | 5,475,843,667 | 5.476 GB [src](https://huggingface.co/api/models/NemoStation/Marlin-2B) |

Weight-byte cross-check: `2,721,801,024 BF16 tensor elements × 2 B = 5,443,602,048 B`; the
5,443,677,224 B on disk leaves **75,176 B** of safetensors JSON headers across two shards —
consistent, and the headers were read directly (§3.1), which confirms it exactly.

### 1.3 Mirror verification

| Metric | `NemoStation/Marlin-2B` (gated) | `lunahr/Marlin-2B-ungated` | Match |
|---|---:|---:|---|
| `safetensors.parameters.BF16` | 2,721,801,024 | 2,721,801,024 | ✅ |
| `safetensors.total` | 2,213,241,664 | 2,213,241,664 | ✅ |
| `model-00001…safetensors` bytes | 4,999,157,736 | 4,999,157,736 | ✅ |
| `model-00002…safetensors` bytes | 444,519,488 | 444,519,488 | ✅ |
| `config.json` bytes | 2,819 | 2,819 | ✅ |
| `tokenizer.json` bytes | 19,989,325 | 19,989,325 | ✅ |
| `chat_template.jinja` bytes | 7,755 | 7,755 | ✅ |
| `modeling_marlin.py` bytes | 23,098 | **17,261** | ❌ differs |
| `README.md` bytes | 11,222 | **10,878** | ❌ differs (front-matter rewritten) |

**Verdict:** the **weights, config, tokenizer and chat template are bit-for-bit identical**
(sizes and HF-computed parameter counts all match). The mirror's `modeling_marlin.py` is
5,837 bytes smaller and its README front-matter was rewritten (`base_model:` repointed to
`NemoStation/Marlin-2B`). Every architectural, parameter and preprocessing claim in this
document rests only on the identical files; claims drawn from the mirror's `modeling_marlin.py`
are labelled as such and were cross-checked against the public card text, which agrees on every
overlapping point (prompt behaviour, env-var defaults, `<think>` stripping, method signatures).

Sources: [gated API](https://huggingface.co/api/models/NemoStation/Marlin-2B) ·
[mirror API](https://huggingface.co/api/models/lunahr/Marlin-2B-ungated) ·
[mirror tree](https://huggingface.co/api/models/lunahr/Marlin-2B-ungated/tree/main)

### 1.4 Tokenizer, vocab, chat template

- **Vocab size 248,320** (padded), tied LM head — `config.json` [src](https://huggingface.co/lunahr/Marlin-2B-ungated/raw/main/config.json); the base card states "Token Embedding: 248320 (Padded)" and "LM Output: 248320 (Tied to token embedding)" [src](https://huggingface.co/Qwen/Qwen3.5-2B/raw/main/README.md).
- **Special tokens** (from `tokenizer_config` in the HF API): `eos_token` `<|im_end|>`, `pad_token` `<|endoftext|>`, `bos_token` `null`, `unk_token` `null` [src](https://huggingface.co/api/models/NemoStation/Marlin-2B).
- **Generation-time EOS is a two-element list**: `eos_token_id: [248044, 248046]` [src](https://huggingface.co/lunahr/Marlin-2B-ungated/raw/main/generation_config.json). Engines that read only `config.eos_token_id` will pick up `248046` (top level) or `248044` (`text_config`) and **miss the other stop id** — see §12.
- **Vision token ids**: `image_token_id 248056`, `video_token_id 248057`, `vision_start_token_id 248053`, `vision_end_token_id 248054` [src](https://huggingface.co/lunahr/Marlin-2B-ungated/raw/main/config.json).
- **Chat template** (Jinja, 7,755 B, identical to the Qwen3.5-2B template of the same byte length): supports interleaved `image` / `video` / `text` content items; emits `<|vision_start|><|image_pad|><|vision_end|>` and `<|vision_start|><|video_pad|><|vision_end|>`; raises on images/videos inside a system message; optional `add_vision_id` prefixes `Picture N: ` / `Video N: `; ships an XML-in-`<tool_call>` **tool-calling** block. [src](https://huggingface.co/api/models/NemoStation/Marlin-2B)
- **Thinking artefact**: the card states the model "emits a `<think>` token at the start of every response (an artifact of training with `add_non_thinking_prefix=True`)". `.caption()`/`.find()` strip it; raw `generate()` callers must strip it themselves. [src](https://huggingface.co/NemoStation/Marlin-2B)

### 1.5 What the model actually does

Quoting the card verbatim [src](https://huggingface.co/NemoStation/Marlin-2B):

> "Marlin is a 2B video VLM tuned for the two questions developers actually like ask their videos:
> **what** is happening, and **when?** It produces structured Scene + Event captions with
> second-precise timestamps, and resolves natural-language queries to span-grounded (start, end)
> ranges in the video."

It is **video *understanding*, not video generation**. Two modes, each with a frozen
training-time prompt (`modeling_marlin.py` marks them "Canonical training-time prompts — DO NOT EDIT"):

| Mode | Method | Prompt (verbatim) | Default `max_new_tokens` | Output |
|---|---|---|---:|---|
| Caption | `.caption(path)` | `"Provide a spatial description of this clip followed by time-ranged events.\nFor each event, give the time range as <start - end> and a short description."` | 2048 | `Scene: <para>` + `Events: <X.X - Y.Y> <desc>` |
| Find | `.find(path, event=…)` | `'Identify the timestamps during which "{event}" takes place. Output the time range as "From <start> to <end>." (numbers in seconds).'` | 64 | `From X.X to Y.Y.` → `(start, end)` tuple |

A third, "**Multichunk reasoning** (limited in this checkpoint)" mode is documented as reachable
only through a raw prompt, not the helpers [src](https://huggingface.co/NemoStation/Marlin-2B).

Prompts and defaults from [mirror `modeling_marlin.py`](https://huggingface.co/lunahr/Marlin-2B-ungated/raw/main/modeling_marlin.py); mode semantics corroborated by the public card.

---

## 2. Architecture from config.json

Source of truth: [`config.json` (mirror, byte-identical to gated original)](https://huggingface.co/lunahr/Marlin-2B-ungated/raw/main/config.json).
Independently corroborated by the base-model card's architecture block [src](https://huggingface.co/Qwen/Qwen3.5-2B/raw/main/README.md)
and by the actual tensor manifest read out of the safetensors headers (§3.1).

### 2.1 Top level

| Field | Value | Why it matters |
|---|---|---|
| `architectures` | `["MarlinForConditionalGeneration"]` | **Not a registered vLLM/SGLang architecture.** See §8.1 — this one string is the single biggest deployment obstacle. |
| `model_type` | `qwen3_5` | The *backbone* is stock Qwen3.5. |
| `dtype` | `bfloat16` | |
| `transformers_version` | `5.7.0` | Matches the card's `transformers >= 5.7.0` floor. |
| `tie_word_embeddings` | `true` | …yet `lm_head.weight` is **also materialised on disk**. See §3.2. |
| `use_cache` | **`false`** (both top level and `text_config`) | Overridden to `true` by `generation_config.json`. A direct `forward()` caller that does not pass `use_cache=True` gets no KV cache — a real footgun. |
| `image_token_id` / `video_token_id` | 248056 / 248057 | |
| `vision_start/end_token_id` | 248053 / 248054 | |
| `eos_token_id` / `pad_token_id` | 248046 / 248044 | conflicts with `text_config.eos_token_id = 248044`; see §12 |

### 2.2 Text tower — hybrid linear/full attention, **dense** (no MoE)

| Field | Value |
|---|---|
| `hidden_size` | 2048 |
| `num_hidden_layers` | 24 |
| `intermediate_size` | 6144 (SwiGLU, `hidden_act: silu`) |
| `mlp_only_layers` | `[]` — every layer carries a full MLP |
| **MoE fields** | **absent** — no `num_experts`, no `moe_intermediate_size`, no router |
| `vocab_size` | 248,320 |
| `max_position_embeddings` | **262,144** |
| `rms_norm_eps` | 1e-6 |
| `mamba_ssm_dtype` | `float32` ← drives the GDN state size in §5.2 |

**There is no MoE routing and no shared/routed expert split.** `active_params == total_params`.
Every "distinct-expert" formula in METHODOLOGY §4 collapses to the dense case for this model.

**Layer schedule** — `layer_types` (24 entries) and `full_attention_interval: 4`:

```
idx : 0  1  2  3  | 4  5  6  7  | 8  9 10 11 | 12 13 14 15 | 16 17 18 19 | 20 21 22 23
type: L  L  L  F  | L  L  L  F  | L  L  L  F | L  L  L  F  | L  L  L  F  | L  L  L  F
      L = linear_attention (Gated DeltaNet)   F = full_attention (gated GQA)
```

→ **18 linear-attention layers, 6 full-attention layers.** The transformers config class derives
exactly this from the interval: `"linear_attention" if bool((i + 1) % interval_pattern) else "full_attention"`
[src](https://raw.githubusercontent.com/huggingface/transformers/main/src/transformers/models/qwen3_5/configuration_qwen3_5.py).
The base card states the same shape in prose: `"Hidden Layout: 6 × (3 × (Gated DeltaNet → FFN) → 1 × (Gated Attention → FFN))"`
[src](https://huggingface.co/Qwen/Qwen3.5-2B/raw/main/README.md).

**Full-attention layers (6 of 24) — "Gated Attention", GQA:**

| Field | Value | Consequence |
|---|---|---|
| `num_attention_heads` | 8 | |
| `num_key_value_heads` | 2 | GQA 4:1 |
| `head_dim` | **256** | **Unusually large.** Drives the whole kernel story in §8.3. |
| `attention_bias` | `false` | |
| `attn_output_gate` | **`true`** | `q_proj` emits `2 × n_heads × head_dim = 4096`; half is the output gate. Confirmed by tensor shape `[4096, 2048]` (§3.1). |
| q/k norms | present, shape `[256]` | per-head RMSNorm on Q and K |
| `partial_rotary_factor` | 0.25 | RoPE applied to **64 of 256** dims — base card: "Rotary Position Embedding Dimension: 64" ✅ |
| `rope_parameters.rope_theta` | 10,000,000 | |
| `rope_parameters.rope_type` | `default` — **no YaRN/NTK scaling entry** | 262,144 is native, not extended |
| `rope_parameters.mrope_section` | `[11, 11, 10]` (sums to 32 = 64/2) | **M-RoPE**: temporal / height / width position axes |
| `rope_parameters.mrope_interleaved` | `true` | |

M-RoPE is what makes "second-precise timestamps" expressible: the temporal axis of the position
encoding tracks frame time, so the model can ground an event to a wall-clock second.

**Linear-attention layers (18 of 24) — Gated DeltaNet:**

| Field | Value |
|---|---|
| `linear_num_key_heads` | 16 |
| `linear_num_value_heads` | 16 |
| `linear_key_head_dim` | 128 |
| `linear_value_head_dim` | 128 |
| `linear_conv_kernel_dim` | 4 |
| key_dim = value_dim | 16 × 128 = **2048** |

Note the base **Qwen3.5 config class defaults** are `linear_num_value_heads: 32` and
`num_key_value_heads: 4` [src](https://raw.githubusercontent.com/huggingface/transformers/main/src/transformers/models/qwen3_5/configuration_qwen3_5.py);
the 2B checkpoint overrides both to 16 and 2. Anything that reasons from defaults will be wrong.

Tensor-level structure (read from the safetensors header, §3.1) — Qwen3.5 ships the GDN input
projections **split**, unlike Qwen3-Next which fuses them. vLLM comments on exactly this:
`"Qwen3.5 ships the GDN in_proj checkpoints separately (qwen3-next …)"`
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/qwen3_5.py):

```
linear_attn.in_proj_qkv.weight  [6144, 2048]   q‖k‖v, 2048 each
linear_attn.in_proj_z.weight    [2048, 2048]   output gate z
linear_attn.in_proj_b.weight    [  16, 2048]   β (delta-rule write strength), per v-head
linear_attn.in_proj_a.weight    [  16, 2048]   α (decay), per v-head
linear_attn.conv1d.weight       [6144, 1, 4]   depthwise short conv over q‖k‖v — no bias tensor
linear_attn.norm.weight         [ 128]         RMSNormGated over value_head_dim
linear_attn.out_proj.weight     [2048, 2048]
linear_attn.A_log               [  16]
linear_attn.dt_bias             [  16]
```

### 2.3 MTP / next-token-prediction head

`config.json` **declares** `mtp_num_hidden_layers: 1` and `mtp_use_dedicated_embeddings: false`.
**The weights are not in the checkpoint.** Zero tensors match `mtp` in the 618-tensor manifest,
and the arithmetic in §3.3 shows the Marlin↔base parameter delta is *exactly* one MTP module.
This is the single most consequential finding for deployment planning — see §7.

### 2.4 Vision encoder

| Field | Value |
|---|---|
| `model_type` | `qwen3_5_vision` (base config says `qwen3_5` — cosmetic) |
| `depth` | 24 blocks |
| `hidden_size` | 1024 |
| `intermediate_size` | 4096 |
| `num_heads` | 16 → head_dim **64** |
| `hidden_act` | `gelu_pytorch_tanh` |
| `patch_size` | 16 |
| `temporal_patch_size` | **2** (two frames fold into one patch grid) |
| `spatial_merge_size` | **2** (2×2 post-ViT merge) |
| `in_channels` | 3 |
| `num_position_embeddings` | 2304 (= 48²) learned, interpolated |
| `out_hidden_size` | 2048 (matches text hidden) |
| `deepstack_visual_indexes` | **`[]` — DeepStack disabled** |

`deepstack_visual_indexes: []` is worth dwelling on. Qwen3-VL's DeepStack injects
multi-scale visual features at several LLM depths; with an empty list, vLLM computes
`deepstack_num_level = 0` and `multiscale_dim = 0`, so the injection path is inert
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/qwen3_5.py).
Visual tokens enter **only** at the embedding layer. That makes the model cheaper and makes
the merger the sole vision→text bottleneck.

Vision tower is a **plain pre-LN ViT with bias everywhere** (`qkv.bias`, `proj.bias`,
`mlp.linear_fc{1,2}.bias`, `norm1/norm2` weight **and** bias) — i.e. LayerNorm, not RMSNorm,
unlike the text tower. Confirmed tensor-by-tensor in §3.1.

**Merger**: `norm(1024) → linear_fc1(4096→4096) → GELU → linear_fc2(4096→2048)`. The 4096
input is `spatial_merge_size² × hidden = 4 × 1024`.

### 2.5 Special mechanisms summary

| Mechanism | Present? | Note |
|---|---|---|
| MoE routing | ❌ | dense |
| MLA | ❌ | plain GQA on the 6 full layers |
| Sliding-window attention | ❌ | no `sliding_window` field; the 6 full layers are **global** |
| Sparse / indexer attention (DSA) | ❌ | |
| Cross-layer KV sharing | ❌ | each full layer owns its cache |
| KV compression | ❌ | |
| Hybrid linear attention (GDN) | ✅ | 18/24 layers |
| MTP / nextn | **declared, weights absent** | §7 |
| M-RoPE (3-axis) | ✅ | `[11,11,10]`, interleaved |
| Attention output gate | ✅ | doubles `q_proj` |
| Partial RoPE | ✅ | 64/256 dims |
| Vision encoder | ✅ | 24-block ViT, DeepStack off |

---

## 3. Parameter count

### 3.1 Method — exact, not estimated

The safetensors **header** of each shard was read directly over HTTP range requests
(`curl -r 0-7` for the little-endian u64 header length, then `curl -r 8-…` for the JSON),
giving every tensor's name, shape and dtype without downloading 5.4 GB:

```
model-00001-of-00002.safetensors  header 53,600 B   __metadata__: {"format": "pt"}
model-00002-of-00002.safetensors  header 21,560 B   __metadata__: {"format": "pt"}
→ 618 tensors, dtype counter: {BF16: 618}, Σ elements = 2,721,801,024
```

That Σ equals HF's `safetensors.parameters.BF16` exactly. Source:
[shard 1](https://huggingface.co/lunahr/Marlin-2B-ungated/resolve/main/model-00001-of-00002.safetensors) ·
[shard 2](https://huggingface.co/lunahr/Marlin-2B-ungated/resolve/main/model-00002-of-00002.safetensors).

### 3.2 The two legitimate parameter counts

```
Σ tensor elements on disk      = 2,721,801,024
minus one embedding copy       =   508,559,360   (248,320 × 2,048)
unique / mathematical params   = 2,213,241,664   ← equals HF safetensors.total exactly
```

Both `lm_head.weight [248320, 2048]` **and** `model.language_model.embed_tokens.weight
[248320, 2048]` are present in the manifest, despite `tie_word_embeddings: true`. The
checkpoint therefore stores 508,559,360 redundant parameters — **1.017 GB of the 5.444 GB
download is a duplicate of the embedding table.** A loader that honours the tie keeps
2,213,241,664 resident; one that does not keeps 2,721,801,024.

> **Report the model as 2.213 B parameters.** The 2.722 B figure is a storage artefact.

### 3.3 Full breakdown, derived then verified

Arithmetic run with `python3` from `config.json` alone, then checked against the manifest:

```python
H, L, V, I   = 2048, 24, 248320, 6144
nh, hd, nkv  = 8, 256, 2            # full attention
lk = lv      = 16                   # GDN heads
lkd = lvd    = 128                  # GDN head dims
conv_k       = 4

mlp          = 3*H*I                                    # 37,748,736

# full-attention layer (attn_output_gate ⇒ q_proj is 2×)
q = H*(nh*hd*2)      #  8,388,608
k = H*(nkv*hd)       #  1,048,576
v = H*(nkv*hd)       #  1,048,576
o = (nh*hd)*H        #  4,194,304
qn = kn = hd         #      256 each
full_layer = q+k+v+o+qn+kn + mlp + 2*H                  # 52,433,408

# Gated DeltaNet layer
key_dim = lk*lkd     # 2048
val_dim = lv*lvd     # 2048
in_qkv  = H*(key_dim*2 + val_dim)   # 12,582,912   (q‖k‖v)
in_z    = H*val_dim                 #  4,194,304
in_b    = H*lv                      #     32,768
in_a    = H*lv                      #     32,768
conv    = (key_dim*2 + val_dim)*conv_k   # 24,576
gnorm   = lvd                       #        128
outp    = val_dim*H                 #  4,194,304
A_log, dt_bias = lv, lv             #    16 + 16
lin_layer = (…all of the above…) + mlp + 2*H            # 58,814,624
```

| Component | Count | ×N | Total | Verified against manifest |
|---|---:|---:|---:|---|
| Full-attention layers | 52,433,408 | 6 | **314,600,448** | ✅ exact |
| GDN linear layers | 58,814,624 | 18 | **1,058,663,232** | ✅ exact |
| Final `model.norm.weight` | 2,048 | 1 | **2,048** | ✅ exact |
| **Language, excl. embeddings** | | | **1,373,265,728** | ✅ exact |
| Embedding table (one copy) | 508,559,360 | 1 | **508,559,360** | ✅ exact |
| **Text tower total** | | | **1,881,825,088** | ✅ exact |
| ViT patch_embed (3×2×16×16→1024 + bias) | 1,573,888 | 1 | 1,573,888 | ✅ exact |
| ViT pos_embed (2304×1024) | 2,359,296 | 1 | 2,359,296 | ✅ exact |
| ViT blocks | 12,596,224 | 24 | **302,309,376** | ✅ exact |
| ViT merger (norm + 4096→4096 + 4096→2048, all biased) | 25,174,016 | 1 | 25,174,016 | ✅ exact |
| **Vision tower total** | | | **331,416,576** | ✅ exact |
| **UNIQUE TOTAL** | | | **2,213,241,664** | ✅ = HF `safetensors.total` |
| *(+ duplicate `lm_head`)* | | | *2,721,801,024* | ✅ = HF `parameters.BF16` |

Per-ViT-block detail (24×): `attn.qkv [3072,1024]`+bias, `attn.proj [1024,1024]`+bias,
`mlp.linear_fc1 [4096,1024]`+bias, `mlp.linear_fc2 [1024,4096]`+bias, `norm1`/`norm2`
weight+bias — 12,596,224 params each.

A first-pass estimate that assumed RMSNorm (weight-only) in the ViT came out 47,104 low; the
manifest showed LayerNorm **biases**. That residual is now zero — every figure above is read,
not inferred.

### 3.4 Active parameters

Dense model ⇒ **active = total = 2,213,241,664** for any token that goes through the whole stack.
For planning, the per-token compute set differs by phase:

| Phase | Weights actually touched | Params |
|---|---|---:|
| Text decode step | language layers + `lm_head` matmul (embedding is a gather, ~0 bytes) | **1,881,825,088** |
| Text prefill, per token | same | 1,881,825,088 |
| Vision encode, per ViT token | vision tower only | 331,416,576 |

### 3.5 Comparison to the model-card claim

The card says "2B params" and the HF tag is `2B`. Against 2,213,241,664 unique that is a **10.7 %
understatement** — normal rounding for a model named after its base. The base `Qwen/Qwen3.5-2B`
carries 2,274,069,824 [src](https://huggingface.co/api/models/Qwen/Qwen3.5-2B) and likewise
markets "2B". No contradiction; just be precise in capacity planning.

### 3.6 Non-quantised tensors

There is **no `quantization_config`** in Marlin's `config.json`, so METHODOLOGY §1's
"`ignore` field is the source of truth" has no field to read. The list below is therefore derived
structurally — every tensor that is not a 2-D linear weight in the language tower — and then
**validated against two independently published quantisations** (§9), which agree to the parameter.

| Category | Params | Quantisable? |
|---|---:|---|
| Language 2-D linear weights | **1,372,717,056** | ✅ yes |
| `linear_attn.conv1d.weight` (18 × 24,576) | 442,368 | ❌ depthwise conv |
| `input_layernorm` (24 × 2048) | 49,152 | ❌ |
| `post_attention_layernorm` (24 × 2048) | 49,152 | ❌ |
| `linear_attn.norm` (18 × 128) | 2,304 | ❌ |
| `model.norm` | 2,048 | ❌ |
| `self_attn.k_norm` (6 × 256) | 1,536 | ❌ |
| `self_attn.q_norm` (6 × 256) | 1,536 | ❌ |
| `linear_attn.A_log` (18 × 16) | 288 | ❌ SSM decay |
| `linear_attn.dt_bias` (18 × 16) | 288 | ❌ SSM |
| **Language non-quantisable subtotal** | **548,672** | |
| Embedding / `lm_head` | 508,559,360 | ❌ in every published recipe |
| **Entire vision tower** | 331,416,576 | ❌ in every published recipe |

**Independent confirmation.** [`tintwotin/Marlin-2B-SDNQ-int8`](https://huggingface.co/api/models/tintwotin/Marlin-2B-SDNQ-int8)
reports `"I8": 1,372,717,056` — **identical to the derived figure, to the parameter**.
[`NemoStation/Marlin-2B-MLX-8bit`](https://huggingface.co/api/models/NemoStation/Marlin-2B-MLX-8bit)
reports `"BF16": 331,965,248` = **exactly** `331,416,576 (vision) + 548,672 (language
non-quantisable)`, and `"U32": 1,881,276,416` = **exactly** `1,372,717,056 (language linears)
+ 508,559,360 (embedding table)` — i.e. MLX quantises the language linears *and* the embeddings,
and the two counts sum to 2,213,241,664. Two vendors, two toolchains, the same split, reproduced
to the parameter.

---

## 4. Weight memory by dtype

Per METHODOLOGY §1. Scale overheads: FP8 block-128 ⇒ +4 B FP32 scale per 128 elements;
NVFP4 ⇒ 0.5 B + 1 B E4M3 per 16 elements (+6.25 %) + one FP32 tensor scale;
MXFP4 ⇒ 0.5 B + 1 B E8M0 per 32 elements (+3.1 %); INT4 g128 ⇒ 0.5 B + g128 scale/zero.

All rows quantise **only the 1,372,717,056 language linear weights** and keep embeddings,
`lm_head`, norms, conv1d, SSM params and the whole vision tower in BF16 — the recipe every
published Marlin quantisation actually uses (§3.6).

| Scheme | GB | GiB | Notes |
|---|---:|---:|---|
| **Native checkpoint on disk** (lm_head duplicated) | **5.444** | **5.070** | 2,721,801,024 BF16 tensors |
| **BF16 resident** (tie honoured) | **4.426** | **4.122** | 2,213,241,664 × 2 B — *the number to plan with* |
| FP8 E4M3 (128-blk scales) | 3.097 | 2.884 | −30.0 % vs BF16 resident |
| FP8 incl. embeddings | 2.604 | 2.425 | rare; hurts quality on a 248 k vocab. *(Corrected 2026-09-19: the earlier 2.588 / 2.410 omitted the +4 B-per-128 block scale on the 508,559,360-param embedding table. `(1,372,717,056 + 508,559,360) × 1.03125 + 331,965,248 × 2 = 2,603,996,800 B` per METHODOLOGY §1.)* |
| **NVFP4** (E4M3 scale /16 + FP32 tensor scale) | **2.454** | **2.285** | ⚠️ **no public NVFP4 checkpoint exists** |
| MXFP4 (E8M0 scale /32) | 2.410 | 2.245 | ⚠️ no public checkpoint |
| **INT4 g128 (GPTQ/AWQ)** | **2.394** | **2.230** | ✅ published checkpoint exists |
| INT8 | 3.075 | 2.864 | ✅ published checkpoint exists |

**Estimate-vs-reality check** (repo `usedStorage`, which includes tokenizer/config, ~+22 MB):

| Repo | Predicted | Actual `usedStorage` | Δ |
|---|---:|---:|---|
| `NemoStation/Marlin-2B` BF16 | 5.444 GB | 5.476 GB | +0.032 (tokenizer etc.) |
| `prasannaJagadesh/marlin-2B-GPTQ-4BITS` | 2.394 GB | **2.418 GB** | +0.024 ✅ |
| `tintwotin/Marlin-2B-SDNQ-int8` | 3.075 GB | **3.117 GB** | +0.042 ✅ |
| `NemoStation/Marlin-2B-MLX-8bit` | ~2.6 GB (MLX packs differently) | 2.683 GB | ✅ |

Sources: [GPTQ](https://huggingface.co/api/models/prasannaJagadesh/marlin-2B-GPTQ-4BITS) ·
[SDNQ](https://huggingface.co/api/models/tintwotin/Marlin-2B-SDNQ-int8) ·
[MLX](https://huggingface.co/api/models/NemoStation/Marlin-2B-MLX-8bit).
The formulas reproduce the published artefacts to <1.5 %.

**The headline: this model fits in 4.4 GB.** Weight memory is a non-issue on every GPU in scope,
down to a 16 GB laptop card. Quantisation below FP8 buys you *bandwidth*, not *capacity*.

---

## 5. KV cache and per-sequence state

This is the most interesting part of the model, and the part most likely to be got wrong by
anyone who assumes "24-layer transformer".

### 5.1 KV cache — only 6 of 24 layers have one

Per METHODOLOGY §2, GQA: `bytes/token/layer = 2 × n_kv_heads × head_dim × B`.

```
per layer, BF16  : 2 × 2 × 256 × 2 = 2,048 B
× 6 full-attention layers          = 12,288 B/token  = 12.00 KiB/token
per layer, FP8   : 2 × 2 × 256 × 1 = 1,024 B  → 6,144 B/token =  6.00 KiB/token
per layer, NVFP4 : 2 × 2 × 256 × .5=   512 B  → 3,072 B/token =  3.00 KiB/token
```

The 18 GDN layers cache **0 bytes per token**. The `head_dim: 256` that makes the kernel story
awkward (§8.3) is fully paid back here: even at 256-wide heads, 2 KV heads × 6 layers is tiny.

For scale: a same-size dense 24-layer GQA model with `head_dim 128`, 8 KV heads would cost
`2×8×128×2×24 = 98,304 B/token` — **8× more**. Marlin's hybrid schedule is an 8× KV win.

### 5.2 Fixed per-sequence state — the GDN recurrence

Per METHODOLOGY §2, linear-attention layers hold a constant-size state instead:

```
recurrent state : n_v_heads × d_k × d_v = 16 × 128 × 128 = 262,144 elements
                  config mamba_ssm_dtype = "float32"  ⇒ × 4 B = 1,048,576 B/layer
conv state      : conv channels × (kernel − 1) = 6,144 × 3 = 18,432 elements
                  model dtype BF16                    ⇒ × 2 B =    36,864 B/layer
                                                        per layer = 1,085,440 B
× 18 linear layers                                  = 19,537,920 B = 18.63 MiB / sequence
```

vLLM derives exactly these from config via
`MambaStateShapeCalculator.gated_delta_net_state_shape(tp_size, linear_num_key_heads,
linear_num_value_heads, linear_key_head_dim, linear_value_head_dim, linear_conv_kernel_dim,
num_spec)` and the dtype via `MambaStateDtypeCalculator.gated_delta_net_state_dtype(...)`
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/qwen3_5.py).

⚠️ **TO BE VERIFIED:** (a) whether vLLM stores the conv state at `kernel` or `kernel − 1` width
(the `kernel` variant costs 49,152 B/layer ⇒ **18.84 MiB/seq, +1.1 %** — *corrected 2026-09-19;
the earlier "19.84 MiB/seq, +6 %" was wrong: `(1,048,576 + 49,152) × 18 = 19,759,104 B = 18.84 MiB`,
i.e. only +221,184 B over the `kernel − 1` figure*); (b) whether
`--mamba-ssm-cache-dtype bfloat16` is honoured for this model, which would halve the recurrent
state to **9.63 MiB/seq**. Both are engine-configurable and neither changes any conclusion.

**Key consequence — the crossover:**

```
19,537,920 B ÷ 12,288 B/token = 1,590 tokens
```

**Below ~1.6 K tokens the fixed GDN state costs more than the KV cache.** A fleet of many short
requests is *state*-bound, not KV-bound. Above that — which is every video request — KV dominates.

### 5.3 Totals per sequence

| Context | KV BF16 | KV FP8 | + GDN state | **Total (BF16)** |
|---|---:|---:|---:|---:|
| 8 K | 96.0 MiB | 48.0 MiB | 18.63 MiB | **0.112 GiB** |
| **23,520 (240-frame, 2-min video)** | **275.6 MiB** | 137.8 MiB | 18.63 MiB | **0.287 GiB** |
| 32 K | 384.0 MiB | 192.0 MiB | 18.63 MiB | **0.393 GiB** |
| 128 K | 1,536.0 MiB | 768.0 MiB | 18.63 MiB | **1.518 GiB** |
| 262,144 (native max) | 3,072.0 MiB | 1,536.0 MiB | 18.63 MiB | **3.018 GiB** |
| 1 M | 12,288.0 MiB | 6,144.0 MiB | 18.63 MiB | **12.018 GiB** |

⚠️ **1 M is out of spec.** `max_position_embeddings` is 262,144 and `rope_type` is `default`
with **no scaling config**. The 1 M row is arithmetic only; running there needs YaRN/NTK that the
checkpoint does not ship, with unknown quality. Do not plan on it.

### 5.4 Effect of the optional mechanisms

| Mechanism | Present? | Effect here |
|---|---|---|
| Sliding window | ❌ | The 6 full layers are **global**. No `W` cap. |
| Cross-layer KV sharing | ❌ | all 6 cache independently |
| KV compression (ratio r) | ❌ | |
| Sparse / indexer attention | ❌ | no DSA-style kernel needed or available |
| **Hybrid linear attention** | ✅ | **the dominant effect: 75 % of layers cache nothing per token** |
| FP8 KV | engine feature | halves the table above; supported by vLLM FlashAttention when `flash_attn_supports_kv_cache_dtype` passes [src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/attention/backends/flash_attn.py) |
| NVFP4 KV | engine feature | FlashInfer exposes `nvfp4_kv_cache_full_dim` [src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/attention/backends/flashinfer.py) — ⚠️ unverified for `head_dim 256` |

### 5.5 Prefix caching caveat — hard constraint

vLLM **refuses to start** Qwen3.5 with full Mamba prefix caching:

```python
if cache_config.mamba_cache_mode == "all":
    raise NotImplementedError(
        "Qwen3.5 currently does not support 'all' prefix caching, "
        "please use '--mamba-cache-mode=align' instead"
    )
```
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/qwen3_5.py)

And the vLLM Qwen3.5 recipe notes *"Prefix caching for Mamba cache align mode is currently
experimental"* [src](https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3.5.html).

**Why this matters more for Marlin than for a text model:** METHODOLOGY §5.4 asks for prefix-hit
scenarios at 0 / 50 / 90 %. For Marlin the realistic hit rate is **~0 %**. Both canonical prompts
put the *video* before/around a fixed instruction, and every request carries a different video —
there is no shared prefix to hit. The only cacheable span is the ~30-token system/prompt scaffold,
i.e. **<0.2 % of a 23.5 K-token request**. Prefix caching is not a lever on this workload; do not
budget for it. (A batch job that asks *several different questions about the same video* is the
one exception, and is exactly the case `--mamba-cache-mode=align` is needed for.)

---

## 6. Compute profile

### 6.1 FLOPs per token — text

Dense model, so METHODOLOGY §4's MoE distinct-expert term is inapplicable
(`distinct_experts(batch)` ≡ all weights, every batch size).

```
FLOPs/token (decode or per-token prefill)
  = 2 × (language non-embed + lm_head)
  = 2 × (1,373,265,728 + 508,559,360)
  = 2 × 1,881,825,088
  = 3.764 GFLOP/token
```

Attention term, **6 full layers only**, causal (≈½ the dense-square count):

```
attention_flops(T) = 6 × 4 × n_q_heads × head_dim × T² / 2
                   = 6 × 4 × 8 × 256 × T² / 2
                   = 24,576 · T²
```

| T | linear part | attention part | total | attn share |
|---:|---:|---:|---:|---:|
| 8,192 | 30.83 TFLOP | 1.65 TFLOP | **32.48 TFLOP** | 5.1 % |
| **23,520** (2-min video) | 88.52 TFLOP | 13.60 TFLOP | **102.12 TFLOP** | 13.3 % |
| 131,072 | 493.31 TFLOP | 422.21 TFLOP | **915.52 TFLOP** | 46.1 % |
| 262,144 | 986.62 TFLOP | 1,688.85 TFLOP | **2,675.47 TFLOP** | 63.1 % |

The quadratic term only takes over past ~128 K. At Marlin's actual operating point (23.5 K) the
model is 87 % GEMM — **it is a throughput problem, not an attention problem.**

GDN layers add `≈ 4 × n_v_heads × d_k × d_v = 1,048,576 FLOP/token/layer × 18 = 18.9 MFLOP/token`
— 0.5 % of the linear term, and in practice **bandwidth- and kernel-bound rather than FLOP-bound**
(chunked scan, state read-modify-write). Treat GDN prefill cost as a kernel-efficiency question
(§8.3), not a roofline one.

### 6.2 Bytes read per decode step

Per METHODOLOGY §4, dense case: **all weights read every step, regardless of batch.**

```
weights_read (BF16) = 1,881,825,088 × 2 = 3,763,650,176 B = 3.764 GB
  (vision tower is NOT read during decode — 331 M params idle after prefill)
weights_read (FP8 language linears, BF16 lm_head) ≈ 2.391 GB
weights_read (INT4 g128, BF16 lm_head)            ≈ 1.726 GB

kv_read(batch, ctx) = batch × ctx × 12,288 B
```

| batch | ctx 8 K | ctx 23,520 (video) | ctx 128 K |
|---:|---:|---:|---:|
| 1 | 3.865 GB | 4.053 GB | 5.374 GB |
| 32 | 6.987 GB | 12.99 GB | 55.3 GB |
| 128 | 16.36 GB | 40.8 GB | 209.9 GB |
| 256 | 28.99 GB | 81.4 GB | 415.8 GB |

At the video context, **KV read overtakes weight read at batch ≈ 14**. Past that the model behaves
like a KV-streaming workload and HBM bandwidth is the only thing that matters.
(Checked 2026-09-19: `3.7637 / 0.289014 = 13.02`, so batch 14 is the first batch at which KV read
exceeds weight read ✅.)

⚠️ **TO BE VERIFIED — three cells in the table above do not reproduce from the stated formula.**
Recomputing `3.764 + batch × ctx × 12,288` gives **16.65** GB (not 16.36) at b=128/8 K, **29.53**
(not 28.99) at b=256/8 K, and **77.75** (not 81.4) at b=256/23,520. Every other cell reproduces
exactly. The discrepancies are 1.8–4.7 % and change no conclusion, but the three cells should be
regenerated rather than trusted. Likewise §6.4's ViT totals recompute ~0.45 % low
(240 frames: 65.32 vs the 65.61 TFLOP stated); the stated figure is kept because everything
downstream is built on it and the gap is inside `est.` tolerance.

### 6.3 Video token budget

Two independent preprocessing paths exist and **they disagree**. Both are documented here because
picking the wrong one silently changes your token bill by 2×.

**Path A — `qwen-vl-utils` (what `.caption()`/`.find()` actually use).** `modeling_marlin.py`
sets these via `os.environ.setdefault`, and the card documents them as the training-time setup
[src](https://huggingface.co/NemoStation/Marlin-2B):

| Env var | Default | Effect |
|---|---|---|
| `FORCE_QWENVL_VIDEO_READER` | `torchcodec` | decoder backend |
| `VIDEO_MAX_PIXELS` | `200704` | ≈ 448×448 per frame |
| `FPS` | `2.0` | sample rate |
| `FPS_MAX_FRAMES` | `240` | ≈ 2 min of video |
| `FPS_MIN_FRAMES` | `4` | floor |

```
patches/frame-slot = 200,704 px ÷ (16×16)          = 784 ViT tokens
LLM tokens per 2-frame temporal group = 784 ÷ 2²   = 196
⇒ 98 LLM tokens per frame, 392 ViT tokens per frame
```

*(Corrected 2026-09-19: the per-frame ViT figure was stated as 196; 784 ViT tokens cover a
**2-frame** temporal group, so it is 392 per frame. The table below was already right —
240 frames × 392 = 94,080 — the prose line was not.)*

| Frames | Video seconds @2 fps | LLM tokens | ViT tokens | KV (BF16) |
|---:|---:|---:|---:|---:|
| 4 (min) | 2.0 | 392 | 1,568 | 4.6 MiB |
| 60 | 30.0 | 5,880 | 23,520 | 68.9 MiB |
| 120 | 60.0 | 11,760 | 47,040 | 137.8 MiB |
| **240 (max)** | **120.0** | **23,520** | **94,080** | **275.6 MiB** |
| 768 | 384.0 | 75,264 | 301,056 | 882.0 MiB |

**Path B — the HF `Qwen3VLVideoProcessor`** in `processor_config.json`
[src](https://huggingface.co/lunahr/Marlin-2B-ungated/raw/main/processor_config.json):
`fps: 2`, `min_frames: 4`, **`max_frames: 768`**, `size: {longest_edge: 25,165,824, shortest_edge: 4096}`.

The Qwen token formula is `video_tokens = total_pixels ÷ 2048` (verified against the base card's
own worked example: it recommends `longest_edge: 469,762,048` and states this corresponds to
"224k video tokens"; `469,762,048 ÷ 2048 = 229,376 = 224 × 1024` ✅
[src](https://huggingface.co/Qwen/Qwen3.5-2B/raw/main/README.md)). So:

```
Marlin video budget : 25,165,824 ÷ 2048 = 12,288 video tokens
Marlin image budget : 16,777,216 ÷ 1024 = 16,384 image tokens
```

**The conflict:** Path A's 240 frames × 200,704 px = 48,168,960 px ⇒ 23,520 tokens, which is
**1.91× over** Path B's 12,288-token cap. `qwen-vl-utils` does its own `smart_resize` and bypasses
the processor's `size`. An engine that ingests video through the HF video processor (vLLM/SGLang)
will therefore give the model **half the frames or half the resolution** the training-time path
did — a silent quality regression, not an error.

⚠️ **TO BE VERIFIED — and this is the single most important open item in the document.** Which
path the model was trained on (Path A, almost certainly, given `modeling_marlin.py` calls it the
training-time setup) and whether serving through vLLM's video pipeline degrades caption/grounding
quality. Testing this requires the gated weights. Until then, **plan on Path A's 23,520 tokens**
and treat any vLLM deployment as needing `--media-io-kwargs` / `mm_processor_kwargs` tuning to
reproduce it.

### 6.4 Vision encoder FLOPs

ViT attention scope, resolved from source: vLLM builds `cu_seqlens` as
`np.repeat(patches_per_frame, grid_thw[:, 0]).cumsum()`
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/qwen3_vl.py).
Each temporal group is its **own varlen attention segment** — attention is **per-frame-group, not
global across the video**. That is a ~120× saving at 240 frames and is why the ViT stays cheap.

```
per ViT token (linear)    : 2 × 12,596,224 × 24 blocks = 604.6 MFLOP
per frame-group attention : 4 × 1024 × 784² × 24       =  60.42 GFLOP
merger, per output token  : 2 × 25,174,016             =  50.3 MFLOP
```

| Input | ViT linear | ViT attention | Merger | **Total** |
|---|---:|---:|---:|---:|
| 1 image / 2 frames (784 ViT tok) | 0.476 TFLOP | 0.060 TFLOP | 0.010 TFLOP | **0.547 TFLOP** |
| 60 frames (30 s) | 14.295 | 1.813 | 0.296 | **16.40 TFLOP** |
| **240 frames (2 min)** | **57.178** | **7.251** | **1.184** | **65.61 TFLOP** |

**Per frame: ≈ 0.273 TFLOP.** Against the LLM prefill's 102.12 TFLOP for the same 2-min clip, the
vision encoder is **39 % of total prefill compute** — substantial, and it is pure dense GEMM at
`hidden 1024`, which runs at high MFU on everything. Total prefill for a 2-min video:
**167.7 TFLOP**.

---

## 7. Speculative decoding and MTP

### 7.1 What the checkpoint ships: **nothing**

| Evidence | Finding |
|---|---|
| `config.json` | declares `mtp_num_hidden_layers: 1`, `mtp_use_dedicated_embeddings: false` |
| Tensor manifest (618 tensors) | **zero** tensors matching `mtp` |
| Base − Marlin parameter delta | `2,274,069,824 − 2,213,241,664 = 60,828,160` |
| Reconstructed MTP module size | `52,433,408 (1 full-attn decoder layer) + 2×2048 (pre-FC norms) + 4096×2048 (fc) + 2048 (norm) = ` **`60,828,160`** |
| Match | **exact, to the parameter** |

The MTP head was **dropped during fine-tuning** — a config field was left behind, the weights were
not. The 2.213 B unique-parameter count and this 60,828,160 delta are mutually confirming.

### 7.2 What that costs you

The base model's own recommended speculative recipes **will not work on Marlin**:

```shell
# Qwen/Qwen3.5-2B — works on the BASE model, NOT on Marlin-2B
vllm serve Qwen/Qwen3.5-2B --port 8000 --tensor-parallel-size 1 --max-model-len 262144 \
  --speculative-config '{"method":"qwen3_next_mtp","num_speculative_tokens":2}'

python -m sglang.launch_server --model-path Qwen/Qwen3.5-2B --port 8000 --tp-size 1 \
  --mem-fraction-static 0.8 --context-length 262144 \
  --speculative-algo NEXTN --speculative-num-steps 3 --speculative-eagle-topk 1 \
  --speculative-num-draft-tokens 4
```
[src](https://huggingface.co/Qwen/Qwen3.5-2B/raw/main/README.md) — both quoted verbatim.

Passing `--speculative-config '{"method":"qwen3_next_mtp", …}'` against Marlin weights will fail
to find the MTP tensors. vLLM registers `Qwen3_5MTP` / `Qwen3_5MoeMTP` in `qwen3_5_mtp`
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/registry.py),
so the *engine* support exists — the *weights* do not.

### 7.3 Published acceptance rates / speedups

⚠️ **TO BE VERIFIED — none found.** No acceptance rate or speedup figure for Qwen3.5 MTP on any
engine was locatable from the primary sources reachable without WebSearch. The vLLM recipe
mentions `{"method": "mtp", "num_speculative_tokens": 1}` for latency-optimised Qwen3.5-397B and
states *"MTP-1 speculative decoding for AMD GPUs is under development"*
[src](https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3.5.html), but publishes no
acceptance numbers. Do not assume a figure.

### 7.4 Is speculation even the right lever here?

Largely **no**, for this workload:

- Marlin's outputs are short and structured. `.find()` caps at **64 tokens**; `.caption()` defaults
  to 2048 but produces "as much detail as it sees fit" [src](https://huggingface.co/NemoStation/Marlin-2B).
  The demo Space uses 768 [src](https://huggingface.co/spaces/HappyPablo/marlin-2b-video-understanding/raw/main/app.py).
- For `.find()`, **prefill dominates absolutely**: 167.7 TFLOP of prefill against 64 decode steps.
  Speculative decoding cannot touch prefill.
- METHODOLOGY §5.5 notes speculation stops helping at large batch. A video-captioning fleet is a
  batch workload by nature (§10.3), so it runs in exactly the regime where MTP contributes least.

**Recommendation:** treat MTP as unavailable and unneeded. If single-request `.caption()` latency
ever becomes the binding constraint, the available levers are, in order: (1) an EAGLE/EAGLE-3 draft
head trained against Marlin — none exists, ⚠️ TO BE VERIFIED whether the
`Eagle3Qwen3vlForCausalLM` entry in vLLM's registry
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/registry.py)
could be adapted; (2) re-attaching the base model's MTP module and re-tuning it; (3) just using a
faster GPU (§10.2 — it is a 1.7 s job on an H100).

**DSpark:** not applicable. No DeepSeek-family component in this model.

---

## 8. Engine support matrix

### 8.1 The central problem: one string in `config.json`

Marlin declares `architectures: ["MarlinForConditionalGeneration"]`. vLLM's registry contains
`Qwen3_5ForConditionalGeneration`, `Qwen3_5MoeForConditionalGeneration`, `Qwen3_5ForCausalLM`,
`Qwen3_5MoeForCausalLM`, `Qwen3_5MTP`, `Qwen3_5MoeMTP` — **and no `MarlinForConditionalGeneration`**
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/registry.py).
SGLang likewise exports `EntryClass = [Qwen3_5MoeForConditionalGeneration, Qwen3_5ForConditionalGeneration]`
[src](https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/models/qwen3_5.py).

**But the model is Qwen3.5 underneath, exactly.** Its own custom code says so, verbatim:

> "This module subclasses the upstream `Qwen3_5ForConditionalGeneration` (native in
> `transformers >= 5.7.0`) and adds two convenience methods … **The forward pass is not modified:**
> we only add chat-template + generate + post-processing wrappers."
>
> — [`modeling_marlin.py`](https://huggingface.co/lunahr/Marlin-2B-ungated/raw/main/modeling_marlin.py) (mirror)

and the class declaration is literally
`class MarlinForConditionalGeneration(Qwen3_5ForConditionalGeneration):`.

**Therefore the fix is to rewrite one string.** Set
`"architectures": ["Qwen3_5ForConditionalGeneration"]` and the checkpoint loads on vLLM's and
SGLang's native Qwen3.5 paths — losing only `.caption()`/`.find()`, which are prompt wrappers you
reimplement client-side from the two canonical prompts in §1.5.

**This is not speculation — someone already shipped it.** The published GPTQ quantisation
`prasannaJagadesh/marlin-2B-GPTQ-4BITS` has
`"architectures": ["Qwen3_5ForConditionalGeneration"]` while keeping
`auto_map → modeling_marlin.MarlinForConditionalGeneration`
[src](https://huggingface.co/api/models/prasannaJagadesh/marlin-2B-GPTQ-4BITS).
An independent third party performed exactly this rewrite.

⚠️ **TO BE VERIFIED:** that tensor names map 1:1 onto vLLM's `Qwen3_5ForConditionalGeneration`
loader, and how vLLM handles the extra untied `lm_head.weight` against `tie_word_embeddings: true`
(most likely benign — it loads the explicit tensor). Needs the gated weights to confirm end to end.

### 8.2 The vLLM Transformers fallback does **not** rescue this

`--model-impl transformers` is the usual escape hatch for unregistered architectures. It is
**unusable here**, on three independent grounds. Quoting the docs' unsupported list
[src](https://docs.vllm.ai/en/latest/models/supported_models.html#transformers):

- ❌ "Hybrid/linear attention mechanisms" — Marlin is 18/24 Gated DeltaNet
- ❌ "Mamba-style state space models" — the GDN recurrent state is exactly that
- ❌ vision-language support is **"image inputs only currently"** — Marlin is a *video* model
- ❌ (also) no quantization schemes, no alternative attention backends

Any one of these is disqualifying. All four apply.

### 8.3 Attention kernels per GPU — the `head_dim: 256` story

This is where the GPUs actually differentiate, and it is entirely driven by the 6 full-attention
layers being 256-wide.

**FlashAttention version selection in vLLM** [src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/attention/backends/fa_utils.py):

```python
if device_capability.major == 9 and is_fa_version_supported(3):
    fa_version = 3     # Hopper (SM90): prefer FA3
elif device_capability.major == 10 and is_fa_version_supported(4):
    fa_version = 4     # Blackwell (SM100+, restrict to SM100 for now): prefer FA4
else:
    fa_version = 2     # Fallback to FA2
```

**head_dim 256 is accepted by every FA version** — `supports_head_size` returns `True` for
`head_size <= 256` (and up to 512 under FA4)
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/attention/backends/flash_attn.py).
Upstream FA2 confirms *"All head dimensions up to 256"*
[src](https://github.com/Dao-AILab/flash-attention). So no GPU is *blocked*. But:

**There is a dedicated FA4 head-dim-256 kernel, and it is Blackwell-only:**

```python
def uses_fa4_hd256_kernel(head_size, head_size_v=None) -> bool:
    """Return whether FA4 uses its dedicated hd256 kernel."""
    if head_size != 256: return False
    if head_size_v is not None and head_size_v != 256: return False
    capability = current_platform.get_device_capability()
    return capability is not None and capability.major in (10, 11)

FA4_HD256_PAGE_SIZE = 128
```
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/attention/backends/fa_utils.py)

Marlin hits `head_size == 256` **exactly**. On SM100/SM11x it gets a purpose-built kernel; on
everything else it does not. The kernel additionally requires the **KV block size to be a multiple
of 128** — `"Larger blocks are split into 128-token kernel pages"` — and falls back if
`mm_prefix` bidirectional attention, R-SWA, softcap, sinks, or `decode_context_parallel_size > 1`
are in play. **`mm_prefix` is a live concern for a VLM**; `supports_mm_prefix()` returns
`is_fa_version_supported(4)`.

**GDN kernels — FlashQLA, and the Ampere cliff.** Qwen ships a dedicated Gated-DeltaNet kernel:

| Property | Value | Source |
|---|---|---|
| Supported archs | **"SM90, SM100, SM103, SM120 or SM121"** | [FlashQLA README](https://github.com/QwenLM/FlashQLA) |
| Requirements | "CUDA 12.8 or above", "PyTorch 2.8 or above" | same |
| Speedup vs FLA Triton 0.5.0 | **"2-3× forward speedup"**, "2× backward speedup" | same |
| Tuned for | "head configurations used by the Qwen3.5 / Qwen3.6 family" | same |
| Benchmark files | `benchmark/benchmark_results_H200.txt`, `…_GB200.txt` | same |

**SM80 (A100) is not on that list, and neither is AMD.** A100 and MI355X fall back to the FLA
Triton path — `flash-linear-attention`, which states its implementations are
*"platform-agnostic and verified on NVIDIA, AMD, and Intel hardware"*
[src](https://github.com/fla-org/flash-linear-attention) and which added a
*"[FlashQLA] backend for Gated DeltaNet"* in 2026-07 (NVIDIA-only). So the A100/MI355X penalty on
75 % of Marlin's layers is roughly the quoted 2–3× on the GDN forward pass.

⚠️ **TO BE VERIFIED:** the exact FlashQLA benchmark numbers (the `benchmark/…H200.txt` and
`…GB200.txt` files could not be retrieved — the GitHub API rate-limited and raw paths 404'd) and
whether vLLM/SGLang actually dispatch to FlashQLA rather than their own Triton GDN kernels for
this config. SGLang carries its own `triton_gdn_fused_proj` with a
`qwen3_5_gdn_prefill_projection_views` path and an `SGLANG_ENABLE_GDN_DECODE_FUSED_PROJ_CONV`
env flag [src](https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/models/qwen3_5.py),
so it may not use FlashQLA at all.

**Per-GPU kernel summary:**

| GPU | CC | FA version (vLLM) | FA4 hd256 kernel | FlashQLA (GDN) | Notes |
|---|---|---|---|---|---|
| A100 SXM/PCIe | 8.0 | **FA2** | ❌ | ❌ **not supported** | worst kernel story; no FP8 either |
| H100 SXM/PCIe/NVL | 9.0 | **FA3** | ❌ (SM90 has an hd512 path instead) | ✅ SM90 | solid, well-trodden |
| H200 SXM/NVL | 9.0 | **FA3** | ❌ | ✅ SM90 | + FlashQLA H200 benchmarks exist |
| B200 SXM | 10.0 | **FA4** | ✅ | ✅ SM100 | best-supported |
| B300 / GB300 | 10.x | **FA4** | ✅ | ✅ SM100/SM103 | best-supported |
| **RTX PRO 6000 Blackwell** | **12.0** ✅ confirmed [src](https://developer.nvidia.com/cuda-gpus) | **FA2** (major 12 ≠ 9, 10) | ❌ (`major in (10,11)` only) | ✅ SM120 (forward pass, added v0.1.2) | **Blackwell silicon, Ampere-era attention path in vLLM** |
| MI355X | gfx950 | n/a — `get_flash_attn_version` returns `None` on ROCm | ❌ | ❌ | AITER / Triton; see §8.6 |

The RTX PRO 6000 row is the non-obvious one and is easy to get wrong: it is a Blackwell card, but
its compute capability is **12.0**, so vLLM's `major == 10` test fails and it drops to **FA2** —
while simultaneously *qualifying* for FlashQLA, which lists SM120 explicitly. Mixed story.

✅ **RESOLVED 2026-09-19 (was ⚠️ TO BE VERIFIED).** NVIDIA's own CUDA GPU compute-capability table
lists **both** "NVIDIA RTX PRO 6000 Blackwell Workstation Edition" and "NVIDIA RTX PRO 6000
Blackwell Server Edition" under **compute capability 12.0**
[src](https://developer.nvidia.com/cuda-gpus) (the same table puts H100/H200 at 9.0 and B200 at
10.0, confirming the other rows). The FA2 conclusion therefore **stands**: `major == 10` fails, and
`uses_fa4_hd256_kernel`'s `major in (10, 11)` fails too. No hardware test needed.

⚠️ **TO BE VERIFIED — which edition you are renting.** Both editions are cc 12.0, but they are
**not** the same bandwidth: Workstation Edition is 1,792 GB/s
[src](https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000/),
Server Edition is **1,597 GB/s** (−11 %) per `research/gpus/rtx6000-pro.md`
[src](https://lenovopress.lenovo.com/lp2263.pdf). Clouds rent Server Edition (AWS `g7e` is listed as
"RTX PRO 6000 Blackwell SE" in `research/cross-cutting/cloud-pricing.md`), so every RTX PRO 6000
decode row in §10.3 — which assumes 1,792 GB/s — is **~11 % optimistic for rented silicon**.

### 8.4 Engine matrix

| Engine | Supports Marlin? | Min version | Path |
|---|---|---|---|
| **transformers** | ✅ **yes, the only first-class path** | **`>= 5.7.0`** (card), `torch >= 2.11.0`, `torchcodec`, `qwen-vl-utils >= 0.0.14`, `av`, `pillow` | `trust_remote_code=True` → `MarlinForConditionalGeneration` via `auto_map` |
| **vLLM** | ⚠️ **only after rewriting `architectures`** | main / nightly (base card: *"vLLM from the main branch … is required for Qwen3.5"*) | native `qwen3_5.py`, `IsHybrid`, tower marked `{"image", "video"}` |
| **SGLang** | ⚠️ same rewrite; **no video support confirmed** | main (*"SGLang from the main branch … is required for Qwen3.5"*) | `qwen3_5.py`, `Qwen3_5ForConditionalGeneration` |
| **TensorRT-LLM** | ⚠️ **no evidence of Qwen3.5 hybrid support found** | — | ⚠️ TO BE VERIFIED |
| **Dynamo** | ⚠️ inherits whatever vLLM/SGLang backend does | — | ⚠️ TO BE VERIFIED |
| **llama.cpp** | ✅ **yes — GGUF exists and is heavily used** | ⚠️ version unknown | `llama-mtmd-cli … --video` |
| **MLX (Apple)** | ✅ official 8-bit build by the author | ⚠️ unknown | §9 |
| **ms-swift** | ✅ claimed by the card | ⚠️ unknown | card: "swift-deploy-compatible" |

`transformers` main is at `5.18.0.dev0`
[src](https://raw.githubusercontent.com/huggingface/transformers/main/src/transformers/__init__.py),
so the `>= 5.7.0` floor is long satisfied. `qwen3_5` is a **native** transformers architecture
(`src/transformers/models/qwen3_5/` exists upstream), which is why the card notes
*"`transformers >= 5.7.0` (for native `qwen3_5` architecture)"*.

### 8.5 Launch recipes

**transformers — quoted verbatim from the model card** [src](https://huggingface.co/NemoStation/Marlin-2B):

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
```

```python
result = marlin.caption("video.mp4")
print(result["scene"])
for ev in result["events"]:
    print(f"<{ev['start']:.1f} - {ev['end']:.1f}> {ev['description']}")

result = marlin.find("video.mp4", event="a person enters the room")
print(result["span"])       # (14.3, 18.2) tuple in seconds, or None on parse failure
print(result["format_ok"])  # True if output matched the trained format
```

**The author's own reference deployment** — the Gradio Space, verbatim
[src](https://huggingface.co/spaces/HappyPablo/marlin-2b-video-understanding/raw/main/app.py):

```python
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
os.environ.setdefault("FORCE_QWENVL_VIDEO_READER", "torchcodec")
os.environ.setdefault("VIDEO_MAX_PIXELS", "200704")
os.environ.setdefault("FPS", "2.0")
os.environ.setdefault("FPS_MAX_FRAMES", "240")
os.environ.setdefault("FPS_MIN_FRAMES", "4")
...
model = AutoModelForCausalLM.from_pretrained(
    MODEL_ID,
    trust_remote_code=True,
    dtype=torch.bfloat16,
    attn_implementation="sdpa",
    low_cpu_mem_usage=True,
)
model = model.to("cuda").eval()
```

Note `attn_implementation="sdpa"` — **not** `flash_attention_2`. The reference deployment does not
use FlashAttention at all, and runs on **ZeroGPU A10G** (`"hardware": {"current": "zero-a10g"}`
[src](https://huggingface.co/api/spaces/HappyPablo/marlin-2b-video-understanding)). That is the
author's own evidence for "runs on a single consumer GPU".

**vLLM — base-model recipes** (verbatim, `Qwen/Qwen3.5-2B`; substitute a Marlin checkpoint whose
`architectures` has been rewritten per §8.1) [src](https://huggingface.co/Qwen/Qwen3.5-2B/raw/main/README.md):

```shell
uv pip install vllm --torch-backend=auto --extra-index-url https://wheels.vllm.ai/nightly
vllm serve Qwen/Qwen3.5-2B --port 8000 --tensor-parallel-size 1 --max-model-len 262144
```

Video frame control is vLLM-only, per the base card:

```
--media-io-kwargs '{"video": {"num_frames": -1}}'
# then per-request:  extra_body={"mm_processor_kwargs": {"fps": 2, "do_sample_frames": True}}
```
> "This feature is currently supported only in vLLM. By default, `fps=2` and `do_sample_frames=True`."

Multimodal and Mamba flags from the vLLM Qwen3.5 recipe
[src](https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3.5.html):
`--mm-encoder-tp-mode data`, `--mm-processor-cache-type shm`, `--mamba-cache-mode=align`.

**SGLang — base-model recipe** (verbatim) [src](https://huggingface.co/Qwen/Qwen3.5-2B/raw/main/README.md):

```shell
uv pip install 'git+https://github.com/sgl-project/sglang.git#subdirectory=python&egg=sglang[all]'
python -m sglang.launch_server --model-path Qwen/Qwen3.5-2B --port 8000 --tp-size 1 \
  --mem-fraction-static 0.8 --context-length 262144
```

**llama.cpp** (verbatim from the GGUF repo) [src](https://huggingface.co/jadeonrails/marlin-2b-gguf/raw/main/README.md):

```bash
llama-mtmd-cli \
  -m marlin-2b-text.gguf \
  --mmproj marlin-2b.gguf \
  --video input.mp4 \
  -p "Describe the scene and events."
```

### 8.6 Per-GPU caveats

**Sizing first, because it settles the question.** Marlin needs **4.43 GB of weights** and
**0.287 GiB per concurrent 2-minute video**. Parallelism is irrelevant: `--tensor-parallel-size 1`
on everything, including a 24 GB A10G. The base card's own recipes use `--tp-size 1` /
`--tensor-parallel-size 1` for the 2B. No PP, no EP, no multi-node, no fabric question.

**Max concurrent sequences** (usable = 0.90 × HBM per METHODOLOGY §3; weights 4.43 GB resident
+ vision tower; 4 GiB activation workspace):

| GPU | HBM | free for KV | @8 K | **@23.5 K (2-min video)** | @32 K | @128 K | @262 K |
|---|---:|---:|---:|---:|---:|---:|---:|
| A10G | 24 GB | 11.9 GB | 98 | **38** | 28 | 7 | 3 |
| L40S | 48 GB | 33.5 GB | 278 | **108** | 79 | 20 | 10 |
| A100 SXM 80 GB | 80 GB | 62.3 GB | 517 | **201** | 147 | 38 | 19 |
| H100 SXM 80 GB | 80 GB | 62.3 GB | 517 | **201** | 147 | 38 | 19 |
| RTX PRO 6000 | 96 GB | 76.7 GB | 637 | **248** | 181 | 47 | 23 |
| H200 SXM | 141 GB | 117.2 GB | 974 | **379** | 277 | 71 | 36 |
| B200 SXM | 180 GB | 152.3 GB | 1,266 | **493** | 360 | 93 | 46 |
| HGX B300 | **262.5 GB** | 226.5 GB | 1,884 | **734** | 536 | 138 | 69 |
| GB300 NVL72 | **279 GB** | 241.4 GB | 2,007 | **782** | 571 | 148 | 74 |
| MI355X | 288 GB | 249.5 GB | 2,075 | **808** | 590 | 153 | 76 |

`est.` per METHODOLOGY §3. Capacity is never the binding constraint above 24 GB.

*(Corrected 2026-09-19.)* The B300/GB300 row previously read "~288 GB / 249.5 GB free / 808
concurrent". **288 GB is the physical HBM3E stack capacity, not what either shipping part exposes.**
NVIDIA's HGX page publishes "Total Memory 2.1 TB" for 8× Blackwell Ultra ⇒ **262.5 GB/GPU**
[src](https://www.nvidia.com/en-us/data-center/hgx/); GB300 NVL72 publishes only the rack figure
(20 TB ÷ 72 = 277.8 GB), with SemiAnalysis at 278 GB usable and Lambda at 279 GB — this doc plans
with 279 GB, matching `research/gpus/gb300.md`. MI355X's 288 GB **is** correct and is the exposed
capacity [src](https://rocm.docs.amd.com/en/latest/reference/gpu-arch/mi350.html). Note also that
the free-for-KV column is computed from the **5.444 GB untied on-disk** weight figure, not the
4.426 GB tied-resident figure quoted in the prose above — i.e. it is ~1 GB conservative on every
row. None of this changes the conclusion: capacity is not the binding constraint.

**Per-GPU notes:**

- **A100 (SM80)** — no FP8 tensor cores; BF16 only. **No FlashQLA** (SM80 unlisted) ⇒ GDN runs on
  FLA Triton, paying the 2–3× kernel penalty on 18 of 24 layers. FA2 only. Works, but is the
  weakest per-dollar Ampere story for *this specific* architecture.
- **H100 / H200 (SM90)** — FA3 + FlashQLA. The best-trodden path. FP8 available. H200's 4.8 TB/s
  [src](https://www.nvidia.com/en-us/data-center/h200/) vs H100's 3.35 TB/s
  [src](https://www.nvidia.com/en-us/data-center/h100/) is a straight 1.43× on decode, which is
  bandwidth-bound here.
- **B200 / B300 / GB300 (SM100/10x)** — FA4 **with the dedicated hd256 kernel**, FlashQLA, NVFP4
  hardware. Constraint: KV block size must be a multiple of `FA4_HD256_PAGE_SIZE = 128`.
- **RTX PRO 6000 Blackwell (SM120)** — 96 GB GDDR7 @ 1792 GB/s, 600 W
  [src](https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000/).
  FlashQLA ✅ (SM120 listed), FA4 hd256 ❌ (`major in (10,11)`), vLLM FA version ⇒ FA2. GDDR7
  bandwidth is ~2.7× below H200, which shows directly in §10.2.
- **MI355X (ROCm)** — `get_flash_attn_version` returns `None` on ROCm outright
  (*"ROCm doesn't use vllm_flash_attn"*)
  [src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/attention/backends/fa_utils.py);
  AITER/Triton backends instead. FlashQLA ❌. The vLLM recipe confirms Qwen3.5 configurations
  *"have been verified on 8x H200 GPUs and 8x MI300X/MI355X GPUs"* and that
  *"MTP-1 speculative decoding for AMD GPUs is under development"*
  [src](https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3.5.html) — but that verification
  is for the **397B MoE text path**, ⚠️ **not** for the 2B hybrid **video** path.
- **L40S / L4 / A10G (mention)** — see §10.4.

### 8.7 Open issues

| Issue | Status | Source |
|---|---|---|
| `mamba_cache_mode == "all"` rejected for Qwen3.5 | **hard error**, use `--mamba-cache-mode=align` | [vLLM qwen3_5.py](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/qwen3_5.py) |
| Mamba `align` prefix caching | "currently experimental" | [vLLM recipe](https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3.5.html) |
| `causal_conv1d_update` assertion when "cuda graph capture size is larger than mamba cache size" | known; workaround = lower `--max-cudagraph-capture-size` (default 512) | [vLLM recipe](https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3.5.html) |
| MTP-1 spec-dec on AMD | "under development" | [vLLM recipe](https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3.5.html) |
| `MarlinForConditionalGeneration` unregistered | see §8.1 | [vLLM registry](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/registry.py) |
| Transformers backend cannot run hybrid/SSM/video | see §8.2 | [vLLM docs](https://docs.vllm.ai/en/latest/models/supported_models.html#transformers) |
| Path A vs Path B video token mismatch | see §6.3 | this document |
| `use_cache: false` in `config.json` | see §2.1 | [config.json](https://huggingface.co/lunahr/Marlin-2B-ungated/raw/main/config.json) |
| Split EOS ids | see §1.4 | [generation_config.json](https://huggingface.co/lunahr/Marlin-2B-ungated/raw/main/generation_config.json) |

⚠️ Naming collision worth flagging for anyone grepping issue trackers: **"Marlin" is also the name
of vLLM's INT4 W4A16 GEMM kernel.** Searches for "vllm marlin" return the kernel, not this model.

---

## 9. Available quantised variants

All figures from the HF API. Every `total` below equals Marlin's 2,213,241,664 unique params,
confirming each is a faithful quantisation of the same checkpoint — **with one exception, corrected
2026-09-19**: [`tintwotin/Marlin-2B-SDNQ-int8`](https://huggingface.co/api/models/tintwotin/Marlin-2B-SDNQ-int8)
reports `safetensors.total: 2,223,966,016`, which is `2,213,241,664 + 10,724,352` — SDNQ stores its
dequantisation scales as **F32 tensors that HF counts as parameters**. The three dtype counts
(`I8 1,372,717,056`, `BF16 840,524,608`, `F32 10,724,352`) still reproduce the §3.6 split exactly;
only the blanket "every total equals 2,213,241,664" was wrong.

| Repo | Format | Reported tensors | On-disk | Who | Notes |
|---|---|---|---:|---|---|
| [`NemoStation/Marlin-2B`](https://huggingface.co/api/models/NemoStation/Marlin-2B) | BF16 | `BF16: 2,721,801,024` | 5.476 GB | **author** | gated; reference |
| [`NemoStation/Marlin-2B-MLX-8bit`](https://huggingface.co/api/models/NemoStation/Marlin-2B-MLX-8bit) | MLX 8-bit | `U32: 1,881,276,416`, `BF16: 331,965,248` | **2.683 GB** | **author** | ungated. Quantises language **incl. embeddings**; vision + 548,672 norm/SSM params stay BF16. Apple Silicon only. 55 downloads. |
| [`junwatu/Marlin-2B-MLX-8bit`](https://huggingface.co/api/models/junwatu/Marlin-2B-MLX-8bit) | MLX 8-bit | — | — | community | duplicate of the above; 12 downloads |
| [`prasannaJagadesh/marlin-2B-GPTQ-4BITS`](https://huggingface.co/api/models/prasannaJagadesh/marlin-2B-GPTQ-4BITS) | **GPTQ 4-bit** | `I32: 1,371,537,408`, `BF16: 841,704,256` | **2.418 GB** | community | `quantization_config: {bits: 4, format: "gptq", quant_method: "gptq"}`. **Rewrites `architectures` → `Qwen3_5ForConditionalGeneration`** (§8.1). 65 downloads. |
| [`tintwotin/Marlin-2B-SDNQ-int8`](https://huggingface.co/api/models/tintwotin/Marlin-2B-SDNQ-int8) | SDNQ INT8 | `I8: 1,372,717,056`, `BF16: 840,524,608`, `F32: 10,724,352` | **3.117 GB** | community | `I8` count is **exactly** the derived quantisable set (§3.6). 10 downloads. |
| [`jadeonrails/marlin-2b-gguf`](https://huggingface.co/api/models/jadeonrails/marlin-2b-gguf) | **GGUF** | `gguf.total: 2,390,384,448`, `architecture: "qwen35"`, `context_length: 262144` | 5.461 GB | community | `marlin-2b-text.gguf` 4.79 GB + `marlin-2b.gguf` (mmproj) 668 MB. **265,785 downloads — 54× the original repo.** Last updated 2026-07-27. |
| [`lunahr/Marlin-2B-ungated`](https://huggingface.co/api/models/lunahr/Marlin-2B-ungated) | BF16 (mirror) | identical | 5.464 GB | community | §1.3 |
| [`deAPI-ai/marlin-2b`](https://huggingface.co/api/models/deAPI-ai/marlin-2b) | ⚠️ unknown | — | — | community | 11 downloads |

**GGUF split, cross-validated.** `marlin-2b.gguf` is 668,226,592 B; the derived vision tower at
F16 is `331,416,576 × 2 = 662,833,152 B` ⇒ +5.4 MB of GGUF metadata. ✅ It is the vision tower.
`marlin-2b-text.gguf` is 4,792,827,296 B; the text tower with **untied** embed + output at F16 is
`(1,881,825,088 + 508,559,360) × 2 = 4,780,768,896 B` ⇒ +12 MB metadata. ✅ And HF's
`gguf.total: 2,390,384,448` equals `1,881,825,088 + 508,559,360` **to the parameter**, which is a
third independent confirmation of the §3.3 text-tower derivation.

**Notably absent:**

- ❌ **No NVFP4 checkpoint.** Relevant to the premise that B300/GB300 unlock NVFP4 throughput
  (§10.5).
- ❌ **No MXFP4, no AWQ, no FP8 checkpoint.**
- ❌ **No EAGLE/MTP draft head** (§7).

**Evaluations of the quantised variants: ⚠️ TO BE VERIFIED — none published.** Not one of the six
repos reports a CaReBench / DREAM-1K / TimeLens score, a perplexity delta, or any accuracy
measurement. For a model whose value is *second-precise timestamps*, INT4 numeric error landing on
the timestamp digits is a specific, plausible and **entirely unmeasured** risk. Treat every
quantised variant as unvalidated until you run the benchmarks yourself.

---

## 10. Published benchmarks

### 10.1 Quality (published by the author, no raw numbers given)

From the card [src](https://huggingface.co/NemoStation/Marlin-2B):

| Benchmark | Claim | Form |
|---|---|---|
| **CaReBench** ([2501.00513](https://arxiv.org/abs/2501.00513)) | "Tops the CaReBench leaderboard"; closes the gap to the Gemini-2.5-Flash teacher "to within 0.21 / 0.43 of 10" | marketing claim + figure |
| **DREAM-1K** ([2407.00634](https://arxiv.org/abs/2407.00634)) | "sits between Tarsier-34B and Gemini-1.5-Pro" | marketing claim + figure |
| **TimeLens-Bench** ([2512.14698](https://arxiv.org/abs/2512.14698)) | "beats Qwen2.5-VL-7B by **+6.4 mIoU** and matches Gemini-2.0-Flash" | marketing claim + figure |

⚠️ **These are vendor marketing claims, labelled as such per METHODOLOGY §7.** The card presents
results as a three-panel PNG (`release_marlin_3up.png`) with **no numeric table**, no
`model-index` in the HF metadata (`"model-index": null`), and no third-party reproduction found.
The single quantitative figure — +6.4 mIoU — is stated without an absolute baseline. The card also
concedes the frontier: *"Specialised 7B+ models on these benchmarks (TimeLens-7B/8B, MiMo-VL,
Time-R1) still carry the upper frontier"*.

**Training provenance, relevant to trusting the numbers** [src](https://huggingface.co/NemoStation/Marlin-2B):
~400 K clip-level annotations from ActivityNet, LSMDC, Charades, Charades-Ego, TREC-VTT,
WebVid-10M, HC-STVG, VidSTG, TimeLens, densely re-annotated by **Gemini-3-Flash in thinking mode**;
two-stage SFT → **SimPO**, judged by Gemini-3-Flash; trained "on a single H100". Note the overlap
between the training sources and the evaluation benchmarks (ActivityNet, Charades, TimeLens all
appear in both lists) — the card does not state how contamination was controlled.

### 10.2 Performance — **no published measurement exists**

⚠️ **TO BE VERIFIED, comprehensively.** Exhaustive checking found **zero** throughput or latency
measurements for Marlin-2B on any hardware:

| Source checked | Result |
|---|---|
| Model card | No Performance / Speed / Hardware / Limitations section at all. Headings are: Key features, Examples, Model & training, Evaluation, Quickstart, Caption mode, Find mode, System requirements, Video preprocessing, Capabilities, Training data, Advanced, Notes on output. Only perf-adjacent sentence: *"runs on a single consumer GPU"*. |
| HF `model-index` | `null` |
| MLPerf Inference | Not an MLPerf model |
| InferenceMAX / SemiAnalysis | ⚠️ unreachable — WebSearch budget exhausted |
| vLLM / SGLang benchmark pages | no Marlin entry found |
| FlashQLA benchmark files | 404 / rate-limited |

**The one real-world timing signal** is the author's demo Space, and it must be read carefully
[src](https://huggingface.co/spaces/HappyPablo/marlin-2b-video-understanding/raw/main/app.py):

```python
@spaces.GPU(duration=_estimate_duration)
def _analyze_gpu(...): ...

def _estimate_duration(video, task, event_query, max_caption_tokens, temperature, *a, **kw):
    if task == "Find":
        return 75
    tokens = int(max_caption_tokens or 768)
    return min(180, 90 + max(0, tokens - 512) // 12)
```

These are **ZeroGPU quota reservations (75–180 s), not measurements.** They bound a cold-start +
video-download + torchcodec-decode + inference round trip on a time-shared A10G. They are *not*
model latency and must not be cited as such. The Space does emit a real measured figure at runtime
(`f"{task} completed in {elapsed:.1f}s on GPU"`), but it is not recorded anywhere retrievable.

### 10.3 Estimated performance (`est.`, METHODOLOGY §4)

All rows below are **roofline estimates**, not measurements, computed as
`decode_step_time ≈ max(bytes_per_step / (HBM_BW × MBU), 2 × active × batch / (peak × MFU))`.
MBU/MFU per METHODOLOGY §4 planning defaults: 0.75/0.40 Hopper & Ampere, 0.60/0.35 Blackwell
datacenter, 0.65/0.40 RTX PRO 6000 & Ada, 0.50/0.30 MI355X.

**GPU spec status — largely resolved 2026-09-19.** H100 (80 GB, 3.35 TB/s
[src](https://www.nvidia.com/en-us/data-center/h100/)), H200 (141 GB, 4.8 TB/s
[src](https://www.nvidia.com/en-us/data-center/h200/)) and RTX PRO 6000 Workstation (96 GB GDDR7,
1,792 GB/s, 600 W [src](https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000/))
are directly sourced. **A100** (80 GB HBM2e, 2,039 GB/s, 312 TFLOPS dense BF16) and **MI355X**
(288 GB HBM3E, 8 TB/s, **2.5 PF dense BF16 / 5.0 PF sparse**
[src](https://rocm.docs.amd.com/en/latest/reference/gpu-arch/mi350.html)) are now **confirmed** and
match the bandwidths implied by the rows below. GB300's 8 TB/s is derived from the sourced rack
figure (*"Up to 576 TB/s"* across *"72x NVIDIA Blackwell Ultra GPUs"*; 20 TB ÷ 72 ⇒ ~278 GB/GPU)
[src](https://www.nvidia.com/en-us/data-center/dgx-gb300/).

⚠️ **Still TO BE VERIFIED:** (a) the **B200** bandwidth these rows imply (~8.4 TB/s at MBU 0.60)
is above *both* published figures — 7.7 TB/s on the Lenovo HGX B200 SKU sheet and 8.0 TB/s derived
from NVIDIA's DGX B200 "64 TB/s ÷ 8"; `research/gpus/b200.md` plans with **7.7 TB/s**, which would
make every B200/B300 decode row here ~9 % optimistic. (b) **L40S and A10G** bandwidth (the rows
imply ~750 and ~520 GB/s against published 864 and 600 GB/s) — these rows are *pessimistic*, not
optimistic. Defer to `research/gpus/` for authoritative per-GPU specs; the relative ordering here is
robust to these corrections because decode is bandwidth-bound.

**Also note:** NVIDIA's published H100/H200 tensor figures are the **sparse** column
(H100 SXM *"BF16 Tensor Core: 1,979 teraFLOPS (with sparsity*)"*
[src](https://www.nvidia.com/en-us/data-center/h100/)). Per METHODOLOGY, all compute below uses
**dense** — 989.5 TFLOPS BF16 for H100/H200.

**Decode, 2-minute video context (23,520 tokens), BF16 weights + BF16 KV** — TPOT / aggregate tok/s:

| GPU | b=1 | b=8 | b=32 | b=64 | b=128 | b=256 |
|---|---|---|---|---|---|---|
| A10G | 10.4 ms / 96 | 15.6 ms / 514 | 33.4 ms / 959 | 57.1 ms / 1,121 | 104.5 ms / 1,225 | 199.4 ms / 1,284 |
| L40S | 7.2 ms / 139 | 10.8 ms / 739 | 23.2 ms / 1,381 | 39.6 ms / 1,615 | 72.6 ms / 1,764 | 138.4 ms / 1,849 |
| RTX PRO 6000 | 3.5 ms / 287 | 5.2 ms / 1,534 | 11.2 ms / 2,865 | 19.1 ms / 3,349 | 35.0 ms / 3,658 | 66.8 ms / 3,835 |
| A100 SXM | 2.7 ms / 377 | 4.0 ms / 2,014 | 8.5 ms / 3,761 | 14.6 ms / 4,397 | 26.7 ms / 4,803 | 50.8 ms / 5,035 |
| H100 SXM | 1.6 ms / 620 | 2.4 ms / 3,308 | 5.2 ms / 6,179 | 8.9 ms / 7,224 | 16.2 ms / 7,891 | 30.9 ms / 8,273 |
| H200 SXM | 1.1 ms / 888 | 1.7 ms / 4,740 | 3.6 ms / 8,853 | 6.2 ms / 10,350 | 11.3 ms / 11,306 | 21.6 ms / 11,853 |
| MI355X | 1.0 ms / 987 | 1.5 ms / 5,267 | 3.3 ms / 9,837 | 5.6 ms / 11,500 | 10.2 ms / 12,562 | 19.4 ms / 13,170 |
| B200 SXM | 0.8 ms / 1,184 | 1.3 ms / 6,320 | 2.7 ms / 11,804 | 4.6 ms / 13,800 | 8.5 ms / 15,075 | 16.2 ms / 15,804 |
| B300 / GB300 | 0.8 ms / 1,184 | 1.3 ms / 6,320 | 2.7 ms / 11,804 | 4.6 ms / 13,800 | 8.5 ms / 15,075 | 16.2 ms / 15,804 |

**Decode, 8 K text context:**

| GPU | b=1 | b=8 | b=32 | b=64 | b=128 | b=256 |
|---|---|---|---|---|---|---|
| A10G | 9.9 ms / 101 | 11.7 ms / 683 | 17.9 ms / 1,787 | 26.2 ms / 2,446 | 42.7 ms / 2,998 | 75.7 ms / 3,381 |
| L40S | 6.9 ms / 145 | 8.1 ms / 983 | 12.4 ms / 2,573 | 18.2 ms / 3,522 | 29.6 ms / 4,318 | 52.6 ms / 4,868 |
| RTX PRO 6000 | 3.3 ms / 301 | 3.9 ms / 2,040 | 6.0 ms / 5,336 | 8.8 ms / 7,304 | 14.3 ms / 8,955 | 25.4 ms / 10,097 |
| A100 SXM | 2.5 ms / 396 | 3.0 ms / 2,678 | 4.6 ms / 7,006 | 6.7 ms / 9,590 | 10.9 ms / 11,757 | 19.3 ms / 13,256 |
| H100 SXM | 1.5 ms / 650 | 1.8 ms / 4,399 | 2.8 ms / 11,511 | 4.1 ms / 15,755 | 6.6 ms / 19,317 | 11.8 ms / 21,779 |
| H200 SXM | 1.1 ms / 932 | 1.3 ms / 6,303 | 1.9 ms / 16,493 | 2.8 ms / 22,575 | 4.6 ms / 27,678 | 8.2 ms / 31,205 |
| MI355X | 1.0 ms / 1,035 | 1.1 ms / 7,004 | 1.7 ms / 18,325 | 2.6 ms / 25,083 | 4.2 ms / 30,753 | 7.4 ms / 34,673 |
| B200 SXM | 0.8 ms / 1,242 | 1.0 ms / 8,405 | 1.5 ms / 21,990 | 2.1 ms / 30,100 | 3.5 ms / 36,904 | 6.2 ms / 41,607 |
| B300 / GB300 | 0.8 ms / 1,242 | 1.0 ms / 8,405 | 1.5 ms / 21,990 | 2.1 ms / 30,100 | 3.5 ms / 36,904 | 6.2 ms / 41,607 |

**METHODOLOGY §4's SLO of TPOT ≤ 50 ms is met by every GPU here at every batch size up to 128** —
including the A10G. This model does not have a latency problem.

**End-to-end, one 2-minute video, batch 1** (240 frames → ViT 65.6 TFLOP + LLM prefill
102.1 TFLOP + 768 decode tokens):

| GPU | ViT | LLM prefill | Decode 768 tok | **Total** | videos/h/GPU @b=1 |
|---|---:|---:|---:|---:|---:|
| A10G | 1.312 s | 2.042 s | 7.981 s | **11.34 s** | 318 |
| L40S | 0.453 s | 0.705 s | 5.542 s | **6.70 s** | 537 |
| RTX PRO 6000 | 0.328 s | 0.511 s | 2.672 s | **3.51 s** | 1,025 |
| A100 SXM | 0.526 s | 0.818 s | 2.035 s | **3.38 s** | 1,065 |
| H100 SXM | 0.166 s | 0.258 s | 1.239 s | **1.66 s** | 2,165 |
| H200 SXM | 0.166 s | 0.258 s | 0.865 s | **1.29 s** | 2,794 |
| MI355X | 0.087 s | 0.136 s | 0.778 s | **1.00 s** | 3,594 |
| B200 SXM | 0.083 s | 0.130 s | 0.648 s | **0.86 s** | 4,179 |
| B300 / GB300 | 0.083 s | 0.130 s | 0.648 s | **0.86 s** | 4,181 |

⚠️ **CORRECTED 2026-09-19 — the B300/GB300 prefill columns were wrong** (previously 0.050 s /
0.078 s / **0.78 s** / 4,638). They implied ~3,749 TFLOPS dense BF16 for B300, i.e. they carried
Blackwell Ultra's **FP4** 1.5× uplift over B200 across to BF16. It does not apply. NVIDIA's HGX
page publishes B300 **FP16/BF16 Tensor Core at 36 PFLOPS *with sparsity* for 8 GPUs ⇒ 2,250
TFLOPS dense per GPU — byte-identical to HGX B200's 36 PFLOPS sparse**
[src](https://www.nvidia.com/en-us/data-center/hgx/). The 1.5× is FP4-only (108 vs 72 PFLOPS dense
per node); `research/gpus/b300.md` says the same ("BF16 / FP16 | 2,250 TFLOPS | Unchanged vs
B200"). **B300 and B200 have identical BF16 prefill and identical 8 TB/s decode, so their rows are
identical throughout this document.** The decode tables above were already correct; only prefill
and everything derived from it (§11.2) were not.

**Read this table carefully: 71–75 % of the time is decode of 768 caption tokens at batch 1.**
Prefill — the whole video, encoder included — is under a second on every datacenter GPU. The
workload is *decode*-dominated at batch 1, which means **batching is the entire optimisation**, and
the roofline says batching works: aggregate throughput rises 12–13× from b=1 to b=128.

⚠️ These estimates carry no correction for the A100/MI355X FlashQLA gap (§8.3), which would slow
their *prefill* columns by an unquantified amount on 18 of 24 layers. The MI355X row in particular
should be treated as optimistic.

**Sanity check against the only real-world anchor:** the A10G row predicts 11.3 s of pure GPU work.
The Space reserves 75–180 s. The gap is video download, torchcodec decode, ZeroGPU cold start and
time-sharing — all plausible, and consistent rather than contradictory. But it does mean **end-to-end
service latency for this model may be dominated by video I/O and decode, not by the GPU at all.**
⚠️ TO BE VERIFIED: torchcodec decode throughput for 240 frames at 448×448 — likely the real
bottleneck in a production pipeline, and entirely absent from every source consulted.

### 10.4 Cheaper GPUs — is a small card the right target?

**Yes, with one qualification, and it is not the obvious one.**

Weights are 4.43 GB. Even a **16 GB** card holds the model plus ~40 concurrent 2-minute videos.
The gating resource is *bandwidth*, not capacity — and since aggregate throughput scales with
bandwidth while GPU price does not, the cheap cards win on throughput-per-dollar (§11.2).

The qualification: **at batch 1 the small cards are slow enough to matter** (A10G 11.3 s, L40S 6.7 s
per 2-min video). For an interactive demo that is visible latency. For a batch captioning pipeline
— which is what this model is *for* — it is irrelevant, because you run at b=64+ where the cheap
card's aggregate throughput is what counts.

- **L4 (24 GB, ~300 GB/s)** ⚠️ specs TO BE VERIFIED — ~half the A10G's bandwidth. Viable for
  low-volume batch; 72 W makes it the density/efficiency play. The encoder work is dense GEMM at
  `hidden 1024` and runs fine.
- **L40S (48 GB)** — ~108 concurrent videos, 1,849 tok/s at b=256. Has FP8 tensor cores (Ada),
  unlike the A100. Probably the sweet spot for cost-conscious batch captioning ⚠️ pending a price.
- **A10G (24 GB)** — the author's own reference hardware. Proven to work; slowest here.
- **RTX PRO 6000 Blackwell (96 GB)** — 248 concurrent videos, and FlashQLA covers SM120 so the GDN
  layers are fast. But vLLM drops it to **FA2** (§8.3) and its 1,792 GB/s is 2.7× below H200.
  A strong *capacity* play, a mediocre *bandwidth* play. Best fit: workstation / on-prem / air-gapped
  deployments where 96 GB in one PCIe slot is the requirement.

**The counterpoint nobody should skip:** at b=128 an **H200 does 11,306 tok/s vs an A10G's 1,225** —
9.2×. If an H200-hour costs less than 9.2 A10G-hours, the big card is cheaper per video *and*
faster. §11.2 shows the sourced prices point the same way.

### 10.5 Does NVFP4 on B300/GB300 actually help?

The premise in the research brief — B300 runs NVFP4, so it may offer extra performance — deserves a
direct answer for **this** model: **largely no, and for a reason specific to Marlin.**

1. **No NVFP4 checkpoint exists** (§9). Someone must quantise it, and nobody has.
2. **Decode here is bandwidth-bound, not FLOP-bound.** NVFP4 would cut weight bytes 4.43 → 2.45 GB
   (§4), which helps at **low batch only**. At b≥32 with video contexts, KV traffic dominates
   weight traffic (§6.2), and NVFP4 *weights* do nothing for KV.
3. **Prefill is where FP4 FLOPs would pay** — 167.7 TFLOP per video — but that is already
   **0.21 s** on a B300 (`167.7 / (2,250 × 0.35)`). Halving it saves ~0.11 s out of a **0.86 s**
   request. *(Corrected 2026-09-19 along with §10.3: the old "under 0.13 s … out of a 0.78 s
   request" used the wrong B300 BF16 dense figure. The conclusion is unchanged — and note that a
   real NVFP4 prefill would beat halving, since NVFP4 is 13,500 vs 2,250 TFLOPS dense on
   `sm_103`. It still cannot help until a checkpoint exists.)*
4. **The larger B300 lever is not NVFP4 at all — it is the FA4 hd256 kernel** (§8.3), which exists
   precisely because `head_dim == 256`, and which B300 gets and RTX PRO 6000 does not.

✅ **RESOLVED 2026-09-19 (was ⚠️ TO BE VERIFIED).** The brief's "deepseek-4.1-flash from NVIDIA"
**does exist**: [`nvidia/DeepSeek-V4.1-Flash-NVFP4`](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4)
— ungated, author `nvidia`, created 2026-09-16 20:37:42 UTC, `library_name: Model Optimizer`
[src](https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4); it is analysed in
`research/models/deepseek41fnvfp4/architecture.md` in this repo. The earlier statement that "no such
artefact was confirmable" was a research-conditions artefact, not a fact.

**The conclusion is unchanged**, because the second half of the original sentence is the load-bearing
half: that checkpoint says nothing about Marlin, which shares no lineage with DeepSeek. Treat any
NVFP4-for-Marlin performance claim as unfounded until a **Marlin** NVFP4 checkpoint and a
measurement both exist.

---

## 11. Vendor API pricing

### 11.1 There is no hosted Marlin API

| Check | Result |
|---|---|
| HF Inference Providers for `NemoStation/Marlin-2B` | **`"inferenceProviderMapping": {}`** — empty [src](https://huggingface.co/api/models/NemoStation/Marlin-2B?expand[]=inferenceProviderMapping) |
| HF Inference Providers for `Qwen/Qwen3.5-2B` (base) | one provider: `featherless-ai`, task `conversational` [src](https://huggingface.co/api/models/Qwen/Qwen3.5-2B?expand[]=inferenceProviderMapping) — **text only, not video** |
| Author-hosted endpoint | A Gradio demo at `https://vlm.nemostation.com/` [src](https://huggingface.co/NemoStation/Marlin-2B). No documented token-priced API. |
| Author monetisation | Card offers custom fine-tuning and integrations by email — a services motion, not an API price list. |

⚠️ **TO BE VERIFIED:** whether any provider (Fireworks, Together, Replicate, Baseten, DeepInfra,
SiliconFlow) hosts Marlin-2B and at what price. This required WebSearch and could not be checked.

**So there is no vendor $/1M-token anchor for this model.** The cost sanity check must be built
from GPU-hours instead.

### 11.2 Self-hosted cost (the real anchor)

On-demand prices, quoted verbatim from [Lambda GPU Cloud](https://lambda.ai/service/gpu-cloud):
B200 SXM6 `$6.69`/GPU-h (8×), H100 SXM `$3.99` (8×) / `$4.29` (1×), H100 PCIe `$3.29` (1×),
A100 SXM 80 GB `$2.79` (8×), A100 SXM 40 GB `$1.99` (8×), **A10 `$1.29`**, A6000 `$1.09`,
V100 `$0.79`, GH200 `$2.29`.
**Lambda lists no H200, B300/GB300, L40S, L4 or RTX PRO 6000** (re-verified 2026-09-19).

⚠️ **CORRECTED 2026-09-19 — two pricing errors.**
1. **Lambda does list an A10, at `$1.29`/GPU-h.** The original price list omitted it, and the "A10"
   row in the table below was priced at **`$1.09` — which is Lambda's *A6000* price**, a different
   GPU. The row is re-derived at `$1.29` below.
2. **"Prices for H200/B300/GB300/MI355X/RTX PRO 6000/L40S were not retrievable" is wrong.** They
   are in this repo, sourced, in [`research/cross-cutting/cloud-pricing.md`](../../cross-cutting/cloud-pricing.md)
   — including reserved/spot/preemptible tiers, which closes METHODOLOGY §6's three-tier
   requirement. Oracle Cloud publishes a flat per-GPU-hour list price for **every** GPU in this
   table [src](https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?limit=2000&currencyCode=USD),
   and Nebius publishes on-demand + preemptible for most of them [src](https://nebius.com/prices).
   The ⚠️ cells below are filled from OCI, tagged `(OCI)`.

**Do not compare an OCI-priced row against a Lambda-priced row.** The two providers are not at the
same price level for identical silicon — OCI lists H100 SXM at `$10.00`/GPU-h against Lambda's
`$3.99`, a 2.5× gap. The single-provider table that follows is the one to reason about.

Throughput model: `sustained = 1/(1/prefill_bound + 1/decode_bound)` — serial prefill+decode on one
GPU, prefill at 167.7 TFLOP/video, decode-bound taken at b=128 from §10.3 over 768 output tokens.

| GPU | $/GPU-h | prefill-bound videos/h | decode-bound videos/h @b=128 | **sustained videos/h** | **$/1k videos** | **$/video-minute** | **$/1M output tok** |
|---|---:|---:|---:|---:|---:|---:|---:|
| A10 | $1.29 | 1,073 | 5,741 | 904 | $1.43 | $0.00071 | $0.293 |
| A100 SXM 80 GB | $2.79 | 2,679 | 22,512 | 2,394 | $1.17 | $0.00058 | $0.161 |
| H100 PCIe | $3.29 | 6,490 | 22,082 | 5,016 | $0.66 | $0.00033 | $0.194 |
| **H100 SXM** | **$3.99** | 8,495 | 36,987 | **6,908** | **$0.58** | **$0.00029** | **$0.140** |
| **B200 SXM** | **$6.69** | 16,902 | 70,662 | **13,639** | **$0.49** | **$0.00025** | **$0.123** |
| H200 SXM | $10.00 (OCI) | 8,495 | 52,996 | 7,321 | $1.37 | $0.00068 | $0.246 |
| B300 / GB300 | $15.00 / $18.00 (OCI) | **16,902** | 70,662 | **13,639** | $1.10 / $1.32 | $0.00055 / $0.00066 | $0.276 / $0.332 |
| MI355X | $8.60 (OCI) | 16,097 | 58,885 | 12,641 | $0.68 | $0.00034 | $0.190 |
| RTX PRO 6000 | $4.50 (OCI) | 4,293 | 17,147 | 3,433 | $1.31 | $0.00066 | $0.342 |
| L40S | $3.50 (OCI) | 3,108 | 8,267 | 2,259 | $1.55 | $0.00077 | $0.551 |

`est.` throughout — throughput from §10.3 roofline, prices sourced. Prefill/decode interleaving is
approximated as serial; a real engine with continuous batching overlaps them and would land
somewhat better.

⚠️ **CORRECTED 2026-09-19 — the B300/GB300 row.** It previously read `28,170` prefill-bound and
`20,141` sustained, carrying the wrong dense-BF16 figure corrected in §10.3. **B300 = B200 at BF16
(2,250 TFLOPS dense) and at bandwidth (8 TB/s), so its throughput row is now identical to B200's**
[src](https://www.nvidia.com/en-us/data-center/hgx/). This deletes the document's previous
"B300 is the throughput king" result.

**Single-provider (OCI list price) comparison** — the apples-to-apples version, since OCI prices
every GPU here [src](https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?limit=2000&currencyCode=USD):

| GPU | $/GPU-h (OCI) | sustained videos/h | **$/1k videos** | $/1M output tok |
|---|---:|---:|---:|---:|
| **MI355X** | $8.60 | 12,641 | **$0.68** | **$0.190** |
| B200 SXM | $14.00 | 13,639 | $1.03 | $0.258 |
| B300 | $15.00 | 13,639 | $1.10 | $0.276 |
| RTX PRO 6000 | $4.50 | 3,433 | $1.31 | $0.342 |
| GB300 | $18.00 | 13,639 | $1.32 | $0.332 |
| H200 SXM | $10.00 | 7,321 | $1.37 | $0.246 |
| H100 SXM | $10.00 | 6,908 | $1.45 | $0.352 |
| L40S | $3.50 | 2,259 | $1.55 | $0.551 |
| A100 SXM 80 GB | $4.00 | 2,394 | $1.67 | $0.231 |
| A10 | $2.00 | 904 | $2.21 | $0.454 |

⚠️ **TO BE VERIFIED:** MI355X tops this table on price *and* is the only row whose throughput
carries an **unquantified downward correction** — no FlashQLA on ROCm (§8.3), and vLLM's Qwen3.5
ROCm verification covers the 397B MoE *text* path, not this 2B hybrid *video* path (§8.6). Its
$0.68 is an upper bound on what it would actually deliver. Every other ordering in this table is
robust; this one row is not.

**Three conclusions:**

1. **A 2-minute video costs ~$0.0005–0.0012 to caption.** At Lambda H100 SXM on-demand:
   **$0.58 per 1,000 videos.** For a fleet captioning 1 M two-minute videos/month:
   ~145 GPU-hours, **~$580/month** on one H100. This is not a cost-constrained workload.
2. **Bigger GPUs are cheaper per video, not more expensive.** H100 SXM beats A100 SXM on
   $/1k videos ($0.58 vs $1.17) *and* is 2.9× faster. B200 beats H100. The A10's $1.29/h looks cheap
   and delivers the **worst** $/1M output tokens of the Lambda-priced rows ($0.293). §10.4's caution
   is confirmed by sourced prices: **do not reach for the cheap card by reflex.** The OCI
   single-provider table says the same thing more cleanly: A10 is last at $2.21/1k videos, B200/B300
   are ~2× better, and the cheap-card intuition is wrong at every price tier checked.
3. **Prefill, not decode, is the sustained-throughput limiter at scale.** Every row is
   prefill-bound (1,073 vs 5,741 on the A10; 8,495 vs 36,987 on the H100) — the opposite of the
   batch-1 picture in §10.3. That makes **prefill FLOPs the thing to optimise**: FP8/NVFP4 *would*
   help here (§10.5 point 3 applies only to the batch-1 latency case), and so would cutting frames
   or resolution (§6.3).

**Cost comparison to the alternative.** The card positions Marlin as "competitive with Gemini-2.5
at a fraction of the cost". ⚠️ Gemini's video pricing could not be retrieved (WebSearch
unavailable), so the multiple cannot be computed here. The self-hosted figure to compare against is
**$0.00029 per video-minute on an H100**.

---

## 12. Open questions

Consolidated ⚠️ **TO BE VERIFIED** items, ordered by how much they would change a deployment decision.

### Blocking — resolve before committing to an architecture

1. **Video token budget: Path A (23,520 tokens) vs Path B (12,288 tokens)** — §6.3. The
   `qwen-vl-utils` env path and the HF `Qwen3VLVideoProcessor` `size.longest_edge` disagree by
   1.91×. Serving through vLLM/SGLang likely uses Path B and silently halves the frames or
   resolution the model was trained on. **Method to resolve:** run `.caption()` on a fixed clip
   through both paths, compare token counts and output quality. Requires gated weights.
2. **Does the checkpoint load on vLLM's native `Qwen3_5ForConditionalGeneration` after rewriting
   `architectures`?** — §8.1. Strong circumstantial evidence (the class is a pure subclass with an
   unmodified forward; a third party already shipped a GPTQ repo with the rewrite) but **not
   end-to-end verified**. Open sub-question: how vLLM handles the untied `lm_head.weight` against
   `tie_word_embeddings: true`. **Method:** `vllm serve` the mirror with a patched config.
3. **Does serving through vLLM/SGLang preserve caption and grounding quality?** The model was
   trained with a fixed prompt, a `<think>` prefix artefact, a two-element EOS list and a specific
   preprocessing path. Every one of those is a place an engine can diverge. No engine-vs-transformers
   quality comparison exists.
4. ~~**RTX PRO 6000 Blackwell compute capability**~~ — ✅ **RESOLVED 2026-09-19.** NVIDIA's CUDA
   GPU table lists both the Workstation and Server Editions at **compute capability 12.0**
   [src](https://developer.nvidia.com/cuda-gpus), so vLLM gives it FA2 and no FA4 hd256 kernel, as
   §8.3 concluded. **What replaces it as a live question: which *edition* you rent** — Workstation
   is 1,792 GB/s, Server Edition 1,597 GB/s, and clouds rent Server Edition, making every RTX PRO
   6000 decode row in §10.3 ~11 % optimistic.

### High-impact but non-blocking

5. **Any measured throughput or latency for this model on any GPU** — §10.2. Every performance
   number in §10.3–§11.2 is roofline `est.`. Nothing is measured.
6. **torchcodec decode throughput for 240 frames @ 448×448** — §10.3. Plausibly the real
   production bottleneck; completely unmeasured, and absent from every source consulted.
7. **Quality of the quantised variants** — §9. Zero evals published for GPTQ-4bit, INT8, MLX-8bit or
   GGUF. Timestamp precision under INT4 is a specific, plausible, unmeasured failure mode.
8. **FlashQLA's real effect on Marlin** — §8.3. Vendor claims 2–3× forward speedup vs FLA Triton;
   the benchmark files (`benchmark_results_H200.txt`, `…_GB200.txt`) were unreachable, and it is
   unconfirmed that vLLM/SGLang dispatch to FlashQLA at all for this head config rather than their
   own Triton GDN kernels.
9. **A100 and MI355X GDN penalty** — §8.3. Neither SM80 nor ROCm is on FlashQLA's list. The
   magnitude of the resulting prefill penalty on 18 of 24 layers is unquantified; the §10.3 rows for
   both are optimistic by an unknown amount.
10. **MI355X on the 2B hybrid video path.** vLLM's Qwen3.5 verification on MI300X/MI355X covers the
    **397B MoE text** model, not this one. Video + GDN + ROCm is unverified in combination.

### Engine and config details

11. **GDN conv-state width** — `kernel` vs `kernel − 1` in vLLM (§5.2): **18.84 vs 18.63 MiB/seq**
    (corrected 2026-09-19 from "19.84"; the gap is +1.1 %, not +6 %, so this item is now
    immaterial to any sizing decision).
12. **`--mamba-ssm-cache-dtype bfloat16`** — would halve GDN state to 9.63 MiB/seq (§5.2); unverified
    whether honoured for this model.
13. **NVFP4 KV cache at `head_dim 256`** — FlashInfer exposes `nvfp4_kv_cache_full_dim` (§5.4);
    head-dim-256 support unverified.
14. **SGLang video support for `qwen3_5`** — the SGLang multimodal docs page 404'd on both the `.ai`
    and `.io` domains. `Qwen3_5ForConditionalGeneration` is exported and subclasses
    `Qwen3VLForConditionalGeneration`, but video ingestion was not confirmed.
15. **TensorRT-LLM support for Qwen3.5 hybrid/GDN** — no evidence found either way.
16. **Split EOS ids** (§1.4) — `config.eos_token_id` is 248046 (top) / 248044 (`text_config`), while
    `generation_config` lists both. An engine reading only the config may miss a stop token.
17. **`use_cache: false` in `config.json`** (§2.1) — harmless via `generate()`, a footgun via
    `forward()`.
18. **Minimum vLLM and SGLang version numbers.** Both base-model recipes say "main branch required";
    no numbered release is named. llama.cpp's minimum version for `qwen35` + mmproj video is also
    unknown.
19. **Marlin's own `modeling_marlin.py` (23,098 B)** vs the mirror's (17,261 B) — §1.3. The 5,837-byte
    difference is unexamined. All overlapping behaviour agrees with the public card, but the delta
    could contain something material.
20. **MLPerf / InferenceMAX / SemiAnalysis coverage** — not checkable without WebSearch. Marlin is
    almost certainly absent (not an MLPerf model), but Qwen3.5-2B base numbers may exist and would
    transfer well, since the backbone is identical bar the MTP head.

### Commercial

21. **Any hosted API for Marlin-2B and its price** — §11.1. None found; search unavailable.
22. ~~**GPU-hour prices for H200, B300/GB300, MI355X, RTX PRO 6000, L40S, L4**, and reserved/spot
    tiers for everything~~ — ✅ **RESOLVED 2026-09-19.** All of them, plus reserved / spot /
    preemptible tiers, are already sourced in this repo at
    [`research/cross-cutting/cloud-pricing.md`](../../cross-cutting/cloud-pricing.md) (AWS, GCP,
    Azure, OCI, CoreWeave, Nebius, Lambda). §11.2 now carries the OCI single-provider comparison.
    Remaining gap: **L4** is not priced in that doc either, and the §10.4 L4 specs stay
    ⚠️ TO BE VERIFIED.
23. **Gemini video-understanding pricing**, to test the card's "fraction of the cost" claim — §11.2.
24. **Training-data / benchmark contamination** — §10.1. ActivityNet, Charades and TimeLens appear
    in both the training-source list and the evaluation list. The card does not address it.

---

## Sources

Every URL below was fetched during this research on 2026-09-19.

**Model and mirror**
- https://huggingface.co/NemoStation/Marlin-2B
- https://huggingface.co/api/models/NemoStation/Marlin-2B
- https://huggingface.co/api/models/NemoStation/Marlin-2B/tree/main
- https://huggingface.co/api/models/NemoStation/Marlin-2B?expand[]=inferenceProviderMapping
- https://huggingface.co/lunahr/Marlin-2B-ungated
- https://huggingface.co/api/models/lunahr/Marlin-2B-ungated
- https://huggingface.co/api/models/lunahr/Marlin-2B-ungated/tree/main
- https://huggingface.co/lunahr/Marlin-2B-ungated/raw/main/config.json
- https://huggingface.co/lunahr/Marlin-2B-ungated/raw/main/generation_config.json
- https://huggingface.co/lunahr/Marlin-2B-ungated/raw/main/preprocessor_config.json
- https://huggingface.co/lunahr/Marlin-2B-ungated/raw/main/processor_config.json
- https://huggingface.co/lunahr/Marlin-2B-ungated/raw/main/modeling_marlin.py
- https://huggingface.co/lunahr/Marlin-2B-ungated/raw/main/README.md
- https://huggingface.co/lunahr/Marlin-2B-ungated/resolve/main/model-00001-of-00002.safetensors *(header via HTTP range)*
- https://huggingface.co/lunahr/Marlin-2B-ungated/resolve/main/model-00002-of-00002.safetensors *(header via HTTP range)*

**Base model**
- https://huggingface.co/Qwen/Qwen3.5-2B
- https://huggingface.co/api/models/Qwen/Qwen3.5-2B
- https://huggingface.co/api/models/Qwen/Qwen3.5-2B/tree/main
- https://huggingface.co/Qwen/Qwen3.5-2B/raw/main/config.json
- https://huggingface.co/Qwen/Qwen3.5-2B/raw/main/README.md
- https://huggingface.co/Qwen/Qwen3.5-2B/raw/main/preprocessor_config.json
- https://huggingface.co/api/models/Qwen/Qwen3.5-2B?expand[]=inferenceProviderMapping

**Quantised variants**
- https://huggingface.co/api/models/NemoStation/Marlin-2B-MLX-8bit
- https://huggingface.co/api/models/prasannaJagadesh/marlin-2B-GPTQ-4BITS
- https://huggingface.co/api/models/tintwotin/Marlin-2B-SDNQ-int8
- https://huggingface.co/api/models/jadeonrails/marlin-2b-gguf
- https://huggingface.co/api/models/jadeonrails/marlin-2b-gguf/tree/main
- https://huggingface.co/jadeonrails/marlin-2b-gguf/raw/main/README.md
- https://huggingface.co/api/models?filter=base_model:quantized:NemoStation/Marlin-2B
- https://huggingface.co/api/models?filter=base_model:finetune:NemoStation/Marlin-2B

**Reference deployment (Spaces)**
- https://huggingface.co/api/spaces/HappyPablo/marlin-2b-video-understanding
- https://huggingface.co/spaces/HappyPablo/marlin-2b-video-understanding/raw/main/app.py
- https://huggingface.co/spaces/HappyPablo/marlin-2b-video-understanding/raw/main/requirements.txt
- https://huggingface.co/api/spaces/Aravind366/marlin-2b-video-understanding
- https://huggingface.co/api/spaces/davanstrien/prelinger-moments-space
- https://huggingface.co/api/spaces/sophietstrds/video-indexing

**vLLM**
- https://docs.vllm.ai/en/latest/models/supported_models.html
- https://docs.vllm.ai/en/latest/models/supported_models.html#transformers
- https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3.5.html
- https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/registry.py
- https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/qwen3_5.py
- https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/qwen3_vl.py
- https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/attention/backends/fa_utils.py
- https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/attention/backends/flash_attn.py
- https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/attention/backends/flashinfer.py
- https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/attention/backends/triton_attn.py
- https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/attention/backends/rocm_aiter_fa.py

**SGLang**
- https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/models/qwen3_5.py
- https://docs.sglang.ai/supported_models/multimodal_language_models.html *(301 → .io, then 404)*

**transformers**
- https://raw.githubusercontent.com/huggingface/transformers/main/src/transformers/models/qwen3_5/configuration_qwen3_5.py
- https://raw.githubusercontent.com/huggingface/transformers/main/src/transformers/models/qwen3_5/modeling_qwen3_5.py
- https://raw.githubusercontent.com/huggingface/transformers/main/src/transformers/__init__.py

**Kernels**
- https://github.com/Dao-AILab/flash-attention
- https://github.com/QwenLM/FlashQLA
- https://github.com/QwenLM/FlashQLA/blob/main/README.md
- https://raw.githubusercontent.com/fla-org/flash-linear-attention/main/README.md

**Hardware and pricing**
- https://www.nvidia.com/en-us/data-center/h100/
- https://www.nvidia.com/en-us/data-center/h200/
- https://www.nvidia.com/en-us/data-center/dgx-gb300/
- https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000/
- https://www.amd.com/en/products/accelerators/instinct/mi350/mi355x.html *(fetch timed out — MI355X specs remain ⚠️ TO BE VERIFIED)*
- https://lambda.ai/service/gpu-cloud

**Papers referenced by the model card** *(cited by the card; not independently fetched)*
- https://arxiv.org/abs/2501.00513 — CaReBench
- https://arxiv.org/abs/2407.00634 — Tarsier
- https://arxiv.org/abs/2512.14698 — TimeLens

---

## Verification log (2026-09-19)

Independent adversarial fact-check. Every derivation was recomputed with `python3` from the
checkpoint's own `config.json` and METHODOLOGY.md; every sourced claim was re-fetched from a
primary URL located independently, not from this document's citation. **22 claims: 15 CONFIRMED,
7 CORRECTED, 0 UNVERIFIABLE.** `WebSearch` was exhausted (200/200) for this pass as well, so
search-only gaps (§10.2 third-party benchmarks, §11.1 hosted-API prices, Gemini video pricing)
remain open and keep their ⚠️ markers.

| # | Claim (§) | Verdict | Source used |
|---|---|---|---|
| 1 | Repo identity: `createdAt` 2026-05-13T16:23:12Z, sha `fd111fca…`, `gated: auto`, 4,912 downloads, 593 likes, `usedStorage` 5,475,843,667, `architectures: ["MarlinForConditionalGeneration"]`, `model_type qwen3_5` (§1.1) | **CONFIRMED** — every field matches byte for byte | [HF API](https://huggingface.co/api/models/NemoStation/Marlin-2B) |
| 2 | Parameter counts: 2,213,241,664 unique / 2,721,801,024 with duplicated `lm_head`; per-layer 52,433,408 (full) and 58,814,624 (GDN); text tower 1,881,825,088; vision 331,416,576 (§3.3) | **CONFIRMED** — rebuilt from `config.json` alone; every one of the 13 sub-totals reproduces exactly, and the two headline figures equal HF's `safetensors.total` / `parameters.BF16` | [config.json](https://huggingface.co/lunahr/Marlin-2B-ungated/raw/main/config.json) + [HF API](https://huggingface.co/api/models/NemoStation/Marlin-2B) |
| 3 | Quantisable set 1,372,717,056; language non-quantisable 548,672; MLX `BF16 331,965,248` and `U32 1,881,276,416` decompositions (§3.6) | **CONFIRMED** — all four reproduce to the parameter from config-derived arithmetic | [SDNQ API](https://huggingface.co/api/models/tintwotin/Marlin-2B-SDNQ-int8), [MLX API](https://huggingface.co/api/models/NemoStation/Marlin-2B-MLX-8bit) |
| 4 | Base `Qwen/Qwen3.5-2B` = 2,274,069,824 params; MTP delta 60,828,160 reconstructs exactly as one full-attention decoder layer + norms + fc (§7.1) | **CONFIRMED** — HF reports `total: 2274069824`; delta arithmetic verified | [HF API](https://huggingface.co/api/models/Qwen/Qwen3.5-2B) |
| 5 | KV = 12,288 B/token (6 full layers × `2 × 2 × 256 × 2`); 8× cheaper than a `head_dim 128`, 8-KV-head, 24-layer GQA model (§5.1) | **CONFIRMED** — `98,304 / 12,288 = 8.0` exactly | METHODOLOGY §2 + [config.json](https://huggingface.co/lunahr/Marlin-2B-ungated/raw/main/config.json) |
| 6 | GDN fixed state 19,537,920 B = 18.63 MiB/seq; crossover at 1,590 tokens; bf16-cache variant 9.63 MiB (§5.2) | **CONFIRMED** — `19,537,920 / 12,288 = 1,590.0` exactly | METHODOLOGY §2 + config |
| 7 | GDN conv state at `kernel` width = "19.84 MiB/seq, +6 %" (§5.2, §12 #11) | **CORRECTED** 19.84 MiB / +6 % → **18.84 MiB / +1.1 %** — `(1,048,576 + 49,152) × 18 = 19,759,104 B = 18.844 MiB`; the delta is 221,184 B | recomputed from `linear_conv_kernel_dim: 4` in [config.json](https://huggingface.co/lunahr/Marlin-2B-ungated/raw/main/config.json) |
| 8 | Weight memory: BF16 resident 4.426 GB, FP8 3.097, NVFP4 2.453, MXFP4 2.410, INT4 g128 2.394, INT8 3.075 (§4) | **CONFIRMED** — all six reproduce under METHODOLOGY §1 overheads, and predict the published GPTQ (2.418 GB), SDNQ (3.117 GB) and MLX (2.683 GB) artefacts to <1.5 % | METHODOLOGY §1 + [GPTQ API](https://huggingface.co/api/models/prasannaJagadesh/marlin-2B-GPTQ-4BITS) |
| 9 | "FP8 incl. embeddings = 2.588 GB / 2.410 GiB" (§4) | **CORRECTED** 2.588 GB / 2.410 GiB → **2.604 GB / 2.425 GiB** — the row omitted the +4 B-per-128 block scale on the 508 M-param embedding table that every other FP8 row applies | METHODOLOGY §1 |
| 10 | "Every `total` in §9 equals Marlin's 2,213,241,664 unique params" (§9) | **CORRECTED** — false for one repo: `tintwotin/Marlin-2B-SDNQ-int8` reports `total: 2,223,966,016` (= +10,724,352 F32 scale tensors counted as params). The dtype split still validates §3.6 | [SDNQ API](https://huggingface.co/api/models/tintwotin/Marlin-2B-SDNQ-int8) |
| 11 | Compute: 3.764 GFLOP/token; `attention_flops(T) = 24,576·T²`; 102.12 TFLOP at 23,520 ctx with 13.3 % attention share; GDN adds 18.9 MFLOP/token = 0.5 % (§6.1) | **CONFIRMED** — all four T rows reproduce exactly | METHODOLOGY §4 + config |
| 12 | Video budget conflict: Path A 23,520 tokens vs Path B 12,288 (`25,165,824 ÷ 2048`), a 1.91× gap; image cap 16,384 (§6.3) | **CONFIRMED** — `processor_config.json` really does carry `size.longest_edge: 25165824`, `max_frames: 768`, `fps: 2`; `48,168,960 / 25,165,824 = 1.914` | [processor_config.json](https://huggingface.co/lunahr/Marlin-2B-ungated/raw/main/processor_config.json) |
| 13 | "98 LLM tokens per frame, **196 ViT tokens per frame**" (§6.3) | **CORRECTED** 196 → **392** ViT tokens/frame. 784 ViT tokens cover a *2-frame* temporal group. The document's own table (240 frames → 94,080 ViT tokens) already implied 392; the prose line contradicted it | derived from `temporal_patch_size: 2`, `patch_size: 16` in config |
| 14 | vLLM FA dispatch: `major == 9 → FA3`, `major == 10 → FA4`, else FA2; `uses_fa4_hd256_kernel` gated on `capability.major in (10, 11)`; `FA4_HD256_PAGE_SIZE = 128` (§8.3) | **CONFIRMED** — source read directly; quoted code is verbatim and still present at lines 99–105, 224–233, 14 | [vLLM fa_utils.py](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/attention/backends/fa_utils.py) |
| 15 | vLLM registry has `Qwen3_5ForConditionalGeneration` / `…MoeForConditionalGeneration` / `…ForCausalLM` / `…MTP` / `Eagle3Qwen3vlForCausalLM` but **no** `MarlinForConditionalGeneration`; SGLang `EntryClass` likewise; vLLM rejects `mamba_cache_mode == "all"` for Qwen3.5 (§8.1, §8.7) | **CONFIRMED** — grepped all three files; the `NotImplementedError` text is verbatim | [registry.py](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/registry.py), [qwen3_5.py](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/qwen3_5.py), [sglang qwen3_5.py](https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/models/qwen3_5.py) |
| 16 | A third party already rewrote `architectures` → `Qwen3_5ForConditionalGeneration` while keeping the `auto_map` (§8.1) | **CONFIRMED** — GPTQ repo config shows exactly that pairing, with `quantization_config {bits: 4, format: gptq}` and `total: 2,213,241,664` | [GPTQ API](https://huggingface.co/api/models/prasannaJagadesh/marlin-2B-GPTQ-4BITS) |
| 17 | FlashQLA supports "SM90, SM100, SM103, SM120 or SM121", needs CUDA ≥ 12.8 / PyTorch ≥ 2.8, claims 2–3× forward speedup over FLA Triton, tuned for Qwen3.5/3.6 head configs; FA2 accepts "All head dimensions up to 256"; transformers main is 5.18.0.dev0 (§8.3, §8.4) | **CONFIRMED** — all verbatim. *Added nuance:* SM120 support is **forward-pass only** (added in v0.1.2, 2026-07), which is sufficient for inference | [FlashQLA README](https://github.com/QwenLM/FlashQLA), [FA README](https://github.com/Dao-AILab/flash-attention), [transformers `__init__.py`](https://raw.githubusercontent.com/huggingface/transformers/main/src/transformers/__init__.py) |
| 18 | RTX PRO 6000 Blackwell is compute capability 12.0 (§8.3, §12 #4 — was ⚠️ TO BE VERIFIED, flagged "blocking") | **CONFIRMED and RESOLVED** — NVIDIA's own CUDA GPU table lists **both** Workstation and Server Editions at **12.0** (H100/H200 at 9.0, B200 at 10.0). Spec sheet also confirms 96 GB GDDR7 / 1,792 GB/s / 600 W for the Workstation Edition. *New caveat:* Server Edition is 1,597 GB/s, and clouds rent Server Edition | [developer.nvidia.com/cuda-gpus](https://developer.nvidia.com/cuda-gpus), [RTX PRO 6000 specs](https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000/) |
| 19 | H100 SXM 80 GB / 3.35 TB/s and H200 141 GB / 4.8 TB/s; "NVIDIA publishes the sparse column, plan with 989.5 dense BF16"; H200 is 1.43× H100 on decode (§8.6, §10.3) | **CONFIRMED** — both pages state 1,979 teraFLOPS BF16 explicitly footnoted "*With sparsity*" ⇒ 989.5 dense. `4.8 / 3.35 = 1.433`. **MI355X also now confirmed** (288 GB HBM3E, 8 TB/s, 2.5 PF dense / 5.0 PF sparse BF16), closing that ⚠️ | [H100](https://www.nvidia.com/en-us/data-center/h100/), [H200](https://www.nvidia.com/en-us/data-center/h200/), [ROCm MI350 arch](https://rocm.docs.amd.com/en/latest/reference/gpu-arch/mi350.html) |
| 20 | B300/GB300 capacity "~288 GB" and prefill 0.050 s ViT / 0.078 s LLM / 0.78 s total / 4,638 videos/h / 28,170 prefill-bound / 20,141 sustained (§8.6, §10.3, §10.5, §11.2) | **CORRECTED — the largest error in the document.** (a) Capacity: 288 GB is the *physical* stack figure; HGX B300 publishes "Total Memory 2.1 TB" for 8 GPUs ⇒ **262.5 GB/GPU**, GB300 ⇒ **279 GB**. (b) Throughput: the rows implied ~3,749 TFLOPS dense BF16, i.e. Blackwell Ultra's **FP4-only** 1.5× uplift wrongly applied to BF16. HGX B300 publishes FP16/BF16 at **36 PFLOPS sparse per 8 GPUs = 2,250 TFLOPS dense per GPU — identical to B200**. Corrected to 0.083 / 0.130 / **0.86 s** / 4,181 videos/h / 16,902 prefill-bound / **13,639** sustained. This removes the document's previous "B300 is the throughput king" conclusion | [NVIDIA HGX platform page](https://www.nvidia.com/en-us/data-center/hgx/) (cross-checks `research/gpus/b300.md`: "BF16 / FP16 \| 2,250 TFLOPS \| Unchanged vs B200") |
| 21 | Lambda on-demand list; "the A10 row at $1.09"; "H200/B300/GB300/MI355X/RTX PRO 6000/L40S prices not retrievable" (§11.2, §12 #22) | **CORRECTED** — (a) Lambda **does** list an A10, at **$1.29**/GPU-h; the table's "A10" row was priced at $1.09, which is Lambda's **A6000** price. Re-derived: $1.43/1k videos, $0.00071/video-min, $0.293/1M output tokens. (b) The missing prices are sourced in this repo at `research/cross-cutting/cloud-pricing.md`, including reserved/spot tiers — OCI publishes a flat per-GPU-hour price for every GPU in the table. §11.2 now carries a single-provider OCI comparison | [Lambda GPU Cloud](https://lambda.ai/service/gpu-cloud), [OCI price API](https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?limit=2000&currencyCode=USD), [Nebius](https://nebius.com/prices) |
| 22 | "The brief's `deepseek-4.1-flash` NVFP4 artefact was not confirmable" (§10.5) | **CORRECTED** — it exists: [`nvidia/DeepSeek-V4.1-Flash-NVFP4`](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4), author `nvidia`, ungated, created 2026-09-16 20:37:42 UTC, `library_name: Model Optimizer`. The load-bearing half of the original conclusion — that it says nothing about Marlin — is unchanged | [HF API](https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4) |

### Still UNVERIFIABLE after this pass (all already carry ⚠️ markers)

Search-only, and `WebSearch` was exhausted for this pass too: any measured throughput/latency for
Marlin-2B on any hardware (§10.2), hosted-API availability and pricing (§11.1), Gemini video pricing
(§11.2), third-party quantisation evals (§9), FlashQLA's raw benchmark files (§8.3), torchcodec
decode throughput (§10.3), and L4 specs/price (§10.4). Gated-weights-only, and unchanged: Path A vs
Path B quality (§6.3), end-to-end vLLM load after the `architectures` rewrite (§8.1), engine-vs-
transformers quality (§12 #3).

### Notes for the orchestrator

- **This model directory has no `config.json`.** Every sibling (`deepseek41f`, `deepseek41fnvfp4`,
  `kimik3`, `qwen3827b`) ships one next to its `architecture.md`; `research/models/marlin2b/` does
  not. All arithmetic above was re-derived against the mirror's config fetched live. Vendoring it
  would make this document reproducible offline like the others.
- **Cross-document contradiction now fixed here but still worth a sweep:** §11.2 asserted that
  prices it needed were unobtainable while `research/cross-cutting/cloud-pricing.md` in the same
  commit carried them, sourced. Other model documents written under the same exhausted-WebSearch
  conditions may contain the same false "not retrievable" claims.
- **Nothing in this document appears invented.** The model, its mirror, all six quantised variants,
  the base model, FlashQLA, and the vLLM/SGLang/transformers source quotations all exist and match
  verbatim. The error profile is arithmetic slips and one wrong hardware generalisation, not
  fabrication.
