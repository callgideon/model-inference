# NemoStation/Marlin-2B — architecture and inference profile

Research date: **2026-09-19**. Formulas, legend and rules per [`research/METHODOLOGY.md`](../../METHODOLOGY.md).

> **Revision note (read first).** The previous revision of this document was written **without gated
> access** to `NemoStation/Marlin-2B`. It reconstructed the architecture from an ungated byte-level
> mirror plus the public model card, and flagged the reconstruction with ⚠️ markers throughout.
>
> **Gate access has since been granted.** The repo's own `config.json`, `generation_config.json`,
> `preprocessor_config.json`, `processor_config.json`, `README.md` (as `MODEL_CARD.md`),
> `modeling_marlin.py`, `chat_template.jinja` and `model.safetensors.index.json` were fetched
> directly, and the config files are now **vendored next to this file**, so every derivation below
> is reproducible offline like the sibling model docs.
>
> Headline: **the mirror was faithful.** Every architectural and parameter claim in the inferred
> version reproduces exactly against the gated original. The substantive corrections in this
> revision are *hardware and pricing* corrections applied repo-wide, plus two genuinely new findings
> from the gated files (a third inference mode, `.multi_find`; and the 240-frame cap's consequence
> for long video). Full change list in the **Verification log** at the end.

---

## 1. Identity

### 1.1 Repository facts

| Field | Value | Source |
|---|---|---|
| HF repo | `NemoStation/Marlin-2B` | [HF API](https://huggingface.co/api/models/NemoStation/Marlin-2B) |
| Author | NemoStation | HF API |
| License | Apache-2.0 | [`MODEL_CARD.md`](MODEL_CARD.md) front-matter |
| Created / last modified | 2026-05-13 16:23:12 UTC / 2026-05-30 08:55:53 UTC | HF API |
| Commit SHA | `fd111fca4fc7897876fb0d7e9df22ca5ac8ab965` | HF API |
| Pipeline tag | `video-text-to-text` | [`MODEL_CARD.md`](MODEL_CARD.md) |
| `architectures` | `["MarlinForConditionalGeneration"]` | [`config.json`](config.json) |
| `model_type` | `qwen3_5` | [`config.json`](config.json) |
| `auto_map` | `{"AutoModelForCausalLM": "modeling_marlin.MarlinForConditionalGeneration"}` | [`config.json`](config.json) |
| `transformers_version` | `5.7.0` | [`config.json`](config.json) |
| Base model | `Qwen/Qwen3.5-2B` (finetune) | HF API tags |
| Gated | `auto` — self-serve form; **access granted 2026-09-19** | HF API cardData |
| Checkpoint dtype | **BF16, unquantised — all 618 tensors** | `model.safetensors.index.json` |
| Papers referenced | arXiv [2501.00513](https://arxiv.org/abs/2501.00513) (CaReBench), [2407.00634](https://arxiv.org/abs/2407.00634) (Tarsier), [2512.14698](https://arxiv.org/abs/2512.14698) (TimeLens) | [`MODEL_CARD.md`](MODEL_CARD.md) |
| Recipe paper | ⚠️ **not published** — card says "Recipe paper coming soon" | [`MODEL_CARD.md`](MODEL_CARD.md) |

### 1.2 Files and on-disk size

From [`FILES.md`](FILES.md) (HF API tree) and the checkpoint index.

| File | Bytes | Note |
|---|---:|---|
| `model-00001-of-00002.safetensors` | 4,999,157,736 | LFS |
| `model-00002-of-00002.safetensors` | 444,519,488 | LFS |
| `tokenizer.json` | 19,989,325 | |
| `model.safetensors.index.json` | 55,830 | `total_parameters` + `total_size` + 618-name weight map |
| `modeling_marlin.py` | 23,098 | custom code (`trust_remote_code`) |
| `chat_template.jinja` | 7,755 | |
| `config.json` | 2,819 | |
| `processor_config.json` / `preprocessor_config.json` / `generation_config.json` | 1,191 / 390 / 137 | |
| **Weights subtotal** | **5,443,677,224** | **5.4437 GB / 5.0698 GiB** |

**Exact reconciliation, read from the index rather than inferred:**

```
index.json metadata.total_size       = 5,443,602,048 B   ← tensor payload only
shard bytes on disk                  = 5,443,677,224 B
difference                           =        75,176 B   ← safetensors JSON headers (2 shards)
index.json metadata.total_parameters = 2,213,241,664     ← unique (tie honoured)
5,443,602,048 / 2 B                  = 2,721,801,024     ← elements actually stored
2,721,801,024 − 2,213,241,664        =   508,559,360     ← the duplicated embedding table
```

The index is internally inconsistent in the usual HF way: `total_size` counts the **stored** tensors
(embedding duplicated) while `total_parameters` reports the **unique** count. Both are correct for
what they measure; §3 uses each where it belongs.

### 1.3 Tokenizer, vocab, chat template

- **Vocab 248,320** (padded), `tie_word_embeddings: true` — [`config.json`](config.json).
- **EOS is split across files.** `config.eos_token_id = 248046` (top level) but
  `config.text_config.eos_token_id = 248044`, while
  [`generation_config.json`](generation_config.json) lists **both**: `"eos_token_id": [248044, 248046]`.
  An engine that reads only `config.eos_token_id` misses one stop id. Confirmed against the real
  files; pass both to any engine you drive directly (§8.1, §12 #18).
- **Vision token ids**: `image_token_id 248056`, `video_token_id 248057`,
  `vision_start_token_id 248053`, `vision_end_token_id 248054`.
- **Chat template** (7,755 B Jinja): interleaved `image` / `video` / `text` content; emits
  `<|vision_start|><|image_pad|><|vision_end|>` and `<|vision_start|><|video_pad|><|vision_end|>`;
  raises on media inside a system message; optional `add_vision_id` prefixes `Picture N: ` /
  `Video N: `; ships an XML-in-`<tool_call>` tool-calling block.
- **`<think>` artefact.** The card states the model "emits a `<think>` token at the start of every
  response (an artifact of training with `add_non_thinking_prefix=True`)". `modeling_marlin.py`
  strips it with three regexes (`_THINK_BLOCK`, `_THINK_PREFIX`, `_THINK_CLOSE`); raw `generate()`
  callers must strip it themselves.

### 1.4 What the model actually does

Video **understanding**, not generation: dense Scene + Event captions with second-precise timestamps,
and natural-language → `(start, end)` temporal grounding. The prompts are frozen in
`modeling_marlin.py` under a "Canonical training-time prompts — DO NOT EDIT" banner:

| Mode | Method | Prompt (verbatim, gated `modeling_marlin.py`) | Default `max_new_tokens` |
|---|---|---|---:|
| Caption | `.caption(path)` | `"Provide a spatial description of this clip followed by time-ranged events.\nFor each event, give the time range as <start - end> and a short description."` | **2048** |
| Find | `.find(path, event=…)` | `'Identify the timestamps during which "{event}" takes place. Output the time range as "From <start> to <end>." (numbers in seconds).'` | 64 |
| Multi-find | `.multi_find(path, event, max_events)` | repeats `.find` on ffmpeg-trimmed tails, remapping each span back to global time | per `.find` |

A fourth, "**Multichunk reasoning** (limited in this checkpoint)" mode is documented as reachable
only through a raw prompt, not the helpers.

**`.multi_find` is new information from the gated file** — it is absent from the mirror's
`modeling_marlin.py` and accounts for the entire 5,837-byte size delta the previous revision flagged
as unexamined (§12 #20, now closed). It has a real cost consequence: it re-encodes the remaining tail
of the video with `ffmpeg -c:v libx264` (frame-accurate, not stream-copy) and re-runs a **full
prefill per hit**. Budget an `N`-event `multi_find` as `N × (ffmpeg re-encode + full prefill +
decode)` — N sequential requests plus CPU video work the GPU roofline does not see. Its own docstring
warns that `.find` "always emits *some* span", so `max_events` must be a tight expected maximum and
later spans are lower confidence.

---

## 2. Architecture from config.json

Source of truth: [`config.json`](config.json) (gated original, 2,819 B). The diff against
[`base-config.qwen3.5-2b.json`](base-config.qwen3.5-2b.json) is **only**: class names
(`MarlinForConditionalGeneration` vs `Qwen3_5ForConditionalGeneration`), the added top-level
`eos_token_id`/`pad_token_id`, `dtype` annotations, `use_cache`, `transformers_version`, and the
`auto_map`. **`text_config` and `vision_config` are byte-for-byte the Qwen3.5-2B config.** Marlin is
a pure fine-tune: same shapes, same layer schedule, same rope, same vision tower.

### 2.1 Top level

| Field | Value | Why it matters |
|---|---|---|
| `architectures` | `["MarlinForConditionalGeneration"]` | not a registered vLLM/SGLang architecture — §8.1 |
| `model_type` | `qwen3_5` | the backbone is stock Qwen3.5 |
| `dtype` | `bfloat16` | |
| `tie_word_embeddings` | `true` | …yet `lm_head.weight` is materialised on disk (§3.2) |
| `use_cache` | **`false`** (top level *and* `text_config`) | the base config has `true` at both; `generation_config.json` overrides back to `true`. A direct `forward()` caller who does not pass `use_cache=True` silently gets no KV cache. |
| `eos_token_id` / `pad_token_id` | 248046 / 248044 | `text_config.eos_token_id` is 248044 — §1.3 |
| `image_token_id` / `video_token_id` | 248056 / 248057 | |

### 2.2 Text tower — hybrid linear/full attention, **dense** (no MoE)

| Field | Value |
|---|---|
| `hidden_size` | 2048 |
| `num_hidden_layers` | 24 |
| `intermediate_size` | 6144 (SwiGLU, `hidden_act: silu`) |
| `mlp_only_layers` | `[]` — every layer carries a full MLP |
| **MoE fields** | **absent** — no `num_experts`, no router, no `moe_intermediate_size` |
| `vocab_size` | 248,320 |
| `max_position_embeddings` | **262,144** |
| `rms_norm_eps` | 1e-6 |
| `mamba_ssm_dtype` | `float32` ← drives the GDN state size in §5.2 |

**`active_params == total_params`.** Every distinct-expert term in METHODOLOGY §4 collapses to the
dense case for this model.

**Layer schedule** — `layer_types` is written out explicitly (24 entries), with
`full_attention_interval: 4`:

```
idx : 0  1  2  3  | 4  5  6  7  | 8  9 10 11 | 12 13 14 15 | 16 17 18 19 | 20 21 22 23
type: L  L  L  F  | L  L  L  F  | L  L  L  F | L  L  L  F  | L  L  L  F  | L  L  L  F
      L = linear_attention (Gated DeltaNet)   F = full_attention (gated GQA)
```

→ **18 linear-attention layers, 6 full-attention layers.** The weight map confirms it independently:
`self_attn.*` tensors exist for exactly 6 layer indices, `linear_attn.*` for exactly 18. The base
card states the same shape in prose: *"Hidden Layout: 6 × (3 × (Gated DeltaNet → FFN) → 1 × (Gated
Attention → FFN))"*.

**Full-attention layers (6 of 24) — gated GQA:**

| Field | Value | Consequence |
|---|---|---|
| `num_attention_heads` | 8 | |
| `num_key_value_heads` | 2 | GQA 4:1 |
| `head_dim` | **256** | unusually large — drives the kernel story (§8.3) and pays for itself in KV (§5.1) |
| `attention_bias` | `false` | no bias tensors; the weight map has none |
| `attn_output_gate` | **`true`** | `q_proj` emits `2 × 8 × 256 = 4096`; half is the output gate |
| q/k norms | present, `[256]` | per-head RMSNorm on Q and K |
| `partial_rotary_factor` | 0.25 | RoPE on **64 of 256** dims (base card: "Rotary Position Embedding Dimension: 64" ✅) |
| `rope_parameters.rope_theta` | 10,000,000 | |
| `rope_parameters.rope_type` | `default` — **no YaRN/NTK entry** | 262,144 is native, not extended |
| `rope_parameters.mrope_section` | `[11, 11, 10]` (sums to 32 = 64/2) | **M-RoPE**: temporal / height / width axes |
| `rope_parameters.mrope_interleaved` | `true` | |

M-RoPE's temporal axis is what makes "second-precise timestamps" expressible at all: the position
encoding tracks frame time, so an event can be grounded to a wall-clock second.

**Linear-attention layers (18 of 24) — Gated DeltaNet:**

| Field | Value |
|---|---|
| `linear_num_key_heads` / `linear_num_value_heads` | 16 / 16 |
| `linear_key_head_dim` / `linear_value_head_dim` | 128 / 128 |
| `linear_conv_kernel_dim` | 4 |
| key_dim = value_dim | 16 × 128 = **2048** |

The upstream Qwen3.5 config class defaults to `linear_num_value_heads: 32` and
`num_key_value_heads: 4`
[src](https://raw.githubusercontent.com/huggingface/transformers/main/src/transformers/models/qwen3_5/configuration_qwen3_5.py);
**the 2B checkpoint overrides both.** Anything reasoning from class defaults will be wrong.

Tensor-level structure, read from the weight map — Qwen3.5 ships the GDN input projections **split**,
unlike Qwen3-Next which fuses them (vLLM comments on exactly this):

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

### 2.3 MTP head — declared, not shipped

`config.json` declares `mtp_num_hidden_layers: 1` and `mtp_use_dedicated_embeddings: false`.
**`model.safetensors.index.json` contains zero tensors matching `mtp`** across all 618 names, and the
base↔Marlin parameter delta is *exactly* one MTP module (§3.3). This is now read from the checkpoint,
not inferred — the ⚠️ the previous revision carried here is removed. Consequences in §7.

### 2.4 Vision encoder

| Field | Value |
|---|---|
| `model_type` | `qwen3_5_vision` (the base config says `qwen3_5` — cosmetic, and the only vision-side diff) |
| `depth` | 24 blocks |
| `hidden_size` / `intermediate_size` | 1024 / 4096 |
| `num_heads` | 16 → head_dim **64** |
| `hidden_act` | `gelu_pytorch_tanh` |
| `patch_size` | 16 |
| `temporal_patch_size` | **2** (two frames fold into one patch grid) |
| `spatial_merge_size` | **2** (2×2 post-ViT merge) |
| `in_channels` | 3 |
| `num_position_embeddings` | 2304 (= 48²), learned, interpolated |
| `out_hidden_size` | 2048 (matches text hidden) |
| `deepstack_visual_indexes` | **`[]` — DeepStack disabled** |

`deepstack_visual_indexes: []` is worth dwelling on. Qwen3-VL's DeepStack injects multi-scale visual
features at several LLM depths; with an empty list vLLM computes `deepstack_num_level = 0` and
`multiscale_dim = 0`, so the injection path is inert
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/qwen3_5.py).
Visual tokens enter **only** at the embedding layer — cheaper, and it makes the merger the sole
vision→text bottleneck.

The tower is a **plain pre-LN ViT with bias everywhere** — `attn.qkv.bias`, `attn.proj.bias`,
`mlp.linear_fc{1,2}.bias`, `norm1`/`norm2` weight **and** bias (LayerNorm, not RMSNorm, unlike the
text tower). All confirmed tensor-by-tensor from the weight map.

**Merger**: `norm(1024) → linear_fc1(4096→4096) → GELU → linear_fc2(4096→2048)`; the 4096 input is
`spatial_merge_size² × hidden = 4 × 1024`.

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

### 3.1 Method — exact, from the checkpoint index

`model.safetensors.index.json` (55,830 B, fetched with the gated token) supplies the authoritative
totals and the full 618-name tensor manifest:

```
metadata.total_parameters = 2,213,241,664
metadata.total_size       = 5,443,602,048 B
weight_map                = 618 entries, all BF16
```

Per-tensor shapes are re-derived from `config.json` and checked against the manifest's names and
counts (1 `lm_head`, 1 `embed_tokens`, 6 × `self_attn.{q,k,v,o}_proj` + `{q,k}_norm`,
18 × `linear_attn.{in_proj_qkv,in_proj_z,in_proj_a,in_proj_b,conv1d,norm,out_proj,A_log,dt_bias}`,
24 × `mlp.{gate,up,down}_proj` + 2 layernorms, 1 `model.norm`, 24 ViT blocks × 12 tensors,
patch/pos embed, 6 merger tensors). The reconstruction lands on `total_parameters` **exactly**, so no
tensor is unaccounted for.

### 3.2 The two legitimate parameter counts

```
elements stored on disk        = 2,721,801,024   (= total_size / 2)
minus one embedding copy       =   508,559,360   (248,320 × 2,048)
unique / mathematical params   = 2,213,241,664   ← equals index metadata.total_parameters
```

Both `lm_head.weight [248320, 2048]` **and** `model.language_model.embed_tokens.weight
[248320, 2048]` are in the manifest despite `tie_word_embeddings: true`. **1.017 GB of the 5.444 GB
download is a duplicate of the embedding table.** A loader honouring the tie keeps 2,213,241,664
resident; one that does not keeps 2,721,801,024.

> **Report the model as 2.213 B parameters.** The 2.722 B figure is a storage artefact.

### 3.3 Full breakdown — text tower vs vision tower vs MTP head

Arithmetic run with `python3` from `config.json` alone:

```python
H, L, V, I   = 2048, 24, 248320, 6144
nh, hd, nkv  = 8, 256, 2            # full attention
lk = lv      = 16                   # GDN heads
lkd = lvd    = 128                  # GDN head dims
conv_k       = 4

mlp          = 3*H*I                                    # 37,748,736

# full-attention layer (attn_output_gate ⇒ q_proj is 2×)
q  = H*(nh*hd*2)     #  8,388,608
k  = H*(nkv*hd)      #  1,048,576
v  = H*(nkv*hd)      #  1,048,576
o  = (nh*hd)*H       #  4,194,304
qn = kn = hd         #        256 each
full_layer = q+k+v+o+qn+kn + mlp + 2*H                  # 52,433,408

# Gated DeltaNet layer
key_dim = lk*lkd     # 2048
val_dim = lv*lvd     # 2048
in_qkv  = H*(key_dim*2 + val_dim)        # 12,582,912   (q‖k‖v)
in_z    = H*val_dim                      #  4,194,304
in_b    = in_a = H*lv                    #     32,768 each
conv    = (key_dim*2 + val_dim)*conv_k   #     24,576
gnorm   = lvd                            #        128
outp    = val_dim*H                      #  4,194,304
A_log = dt_bias = lv                     #         16 each
lin_layer = (…) + mlp + 2*H                             # 58,814,624
```

| Component | Count | ×N | Total |
|---|---:|---:|---:|
| Full-attention layers | 52,433,408 | 6 | **314,600,448** |
| GDN linear layers | 58,814,624 | 18 | **1,058,663,232** |
| Final `model.norm.weight` | 2,048 | 1 | 2,048 |
| **Language, excl. embeddings** | | | **1,373,265,728** |
| Embedding table (one copy) | 508,559,360 | 1 | 508,559,360 |
| **TEXT TOWER TOTAL** | | | **1,881,825,088** (85.0 %) |
| ViT patch_embed (3×2×16×16→1024 + bias) | 1,573,888 | 1 | 1,573,888 |
| ViT pos_embed (2304×1024) | 2,359,296 | 1 | 2,359,296 |
| ViT blocks | 12,596,224 | 24 | **302,309,376** |
| ViT merger (norm + 4096→4096 + 4096→2048, all biased) | 25,174,016 | 1 | 25,174,016 |
| **VISION TOWER TOTAL** | | | **331,416,576** (15.0 %) |
| **MTP HEAD** | 60,828,160 | **0** | **0 — not shipped** |
| **UNIQUE TOTAL** | | | **2,213,241,664** ✅ = `index.metadata.total_parameters` |
| *(+ duplicate `lm_head`)* | | | *2,721,801,024* = `total_size / 2` |

Per-ViT-block detail (×24): `attn.qkv [3072,1024]`+bias, `attn.proj [1024,1024]`+bias,
`mlp.linear_fc1 [4096,1024]`+bias, `mlp.linear_fc2 [1024,4096]`+bias, `norm1`/`norm2` weight+bias —
12,596,224 each. (The ViT uses LayerNorm, so the **biases** are real parameters; an RMSNorm
assumption comes out 47,104 low.)

**MTP head size, reconstructed:** one full-attention decoder layer + two pre-FC norms + the
`4096→2048` fc + a final norm = `52,433,408 + 2×2048 + 4096×2048 + 2048 = 60,828,160`. The base
`Qwen/Qwen3.5-2B` carries 2,274,069,824 params, and
`2,274,069,824 − 2,213,241,664 = 60,828,160` — **exact to the parameter**. The head was dropped
during fine-tuning and the config field left behind.

### 3.4 Active parameters

Dense ⇒ **active = total = 2,213,241,664**. Per phase, the weights actually touched differ:

| Phase | Weights touched | Params |
|---|---|---:|
| Text decode step | language layers + `lm_head` matmul (embedding is a gather, ~0 bytes) | **1,881,825,088** |
| Text prefill, per token | same | 1,881,825,088 |
| Vision encode, per ViT patch | vision tower only | 331,416,576 |

### 3.5 Against the model-card claim

The card says "2B params". 2,213,241,664 unique is a **10.7 % understatement** — normal rounding for
a model named after its base, which markets "2B" against 2,274,069,824 itself. No contradiction; just
be precise in capacity planning.

### 3.6 Non-quantisable tensors

There is **no `quantization_config`** in Marlin's `config.json`, so METHODOLOGY §1's "`ignore` field
is the source of truth" has no field to read. The split below is derived structurally — every tensor
that is not a 2-D linear weight in the language tower — and validated against two independently
published quantisations (§9), which agree **to the parameter**.

| Category | Params | Quantisable? |
|---|---:|---|
| Language 2-D linear weights | **1,372,717,056** | ✅ |
| `linear_attn.conv1d.weight` (18 × 24,576) | 442,368 | ❌ depthwise conv |
| `input_layernorm` + `post_attention_layernorm` (48 × 2048) | 98,304 | ❌ |
| `linear_attn.norm` (18 × 128) | 2,304 | ❌ |
| `model.norm` | 2,048 | ❌ |
| `self_attn.q_norm` + `k_norm` (12 × 256) | 3,072 | ❌ |
| `linear_attn.A_log` + `dt_bias` (36 × 16) | 576 | ❌ SSM |
| **Language non-quantisable subtotal** | **548,672** | |
| Embedding / `lm_head` | 508,559,360 | ❌ in every published recipe |
| **Entire vision tower** | 331,416,576 | ❌ in every published recipe |

**Independent confirmation.** [`tintwotin/Marlin-2B-SDNQ-int8`](https://huggingface.co/api/models/tintwotin/Marlin-2B-SDNQ-int8)
reports `"I8": 1,372,717,056` — identical to the derived figure.
[`NemoStation/Marlin-2B-MLX-8bit`](https://huggingface.co/api/models/NemoStation/Marlin-2B-MLX-8bit)
reports `"BF16": 331,965,248` = exactly `331,416,576 + 548,672`, and `"U32": 1,881,276,416` = exactly
`1,372,717,056 + 508,559,360`. Two vendors, two toolchains, the same split.

---

## 4. Weight memory by dtype

Per METHODOLOGY §1. **Units: GB = 10⁹, GiB = 2³⁰; both columns shown, neither implied.** Overheads:
FP8 E4M3 128-block ⇒ 1.03125 B/param; NVFP4 ⇒ 0.5625; MXFP4 ⇒ 0.53125; INT4 W4A16 g128 ⇒ 0.5195.

All quantised rows touch **only the 1,372,717,056 language linear weights** and keep embeddings,
`lm_head`, norms, conv1d, SSM params and the whole vision tower in BF16 — the recipe every published
Marlin quantisation actually uses (§3.6).

| Scheme | GB | GiB | vs BF16 resident | Notes |
|---|---:|---:|---:|---|
| **Native checkpoint on disk** (`lm_head` duplicated) | **5.444** | **5.070** | +23.0 % | `index.total_size` + 75,176 B headers |
| **BF16 resident** (tie honoured) | **4.426** | **4.122** | — | **the number to plan with** |
| **FP8 E4M3** (128-blk scales) | **3.097** | **2.884** | −30.0 % | |
| FP8 incl. embeddings | 2.604 | 2.425 | −41.2 % | rare; hurts quality on a 248 k vocab |
| **INT8 W8A16** | **3.097** | **2.884** | −30.0 % | ✅ published checkpoint exists |
| **NVFP4** (E4M3 scale /16 + FP32 tensor scale) | **2.453** | **2.285** | −44.6 % | ⚠️ **no public checkpoint** |
| MXFP4 (E8M0 scale /32) | 2.410 | 2.245 | −45.6 % | ⚠️ no public checkpoint |
| **INT4 W4A16 g128 (GPTQ/AWQ)** | **2.394** | **2.230** | −45.9 % | ✅ published checkpoint exists |

**Estimate-vs-reality check** (repo `usedStorage` includes tokenizer/config, ~+20–42 MB):

| Repo | Predicted | Actual `usedStorage` | Δ |
|---|---:|---:|---|
| `NemoStation/Marlin-2B` BF16 | 5.444 GB | 5.476 GB | +0.032 ✅ |
| `prasannaJagadesh/marlin-2B-GPTQ-4BITS` | 2.394 GB | 2.418 GB | +0.024 ✅ |
| `tintwotin/Marlin-2B-SDNQ-int8` | 3.097 GB | 3.117 GB | +0.020 ✅ |
| `NemoStation/Marlin-2B-MLX-8bit` | ~2.6 GB (MLX packs differently) | 2.683 GB | ✅ |

**The headline: this model fits in 4.43 GB.** Weight memory is a non-issue on every GPU in the pinned
set, down to a 16 GB laptop card. Quantisation below FP8 buys **bandwidth**, not capacity — and §6.2
shows even that stops mattering above batch ≈ 14 at video context.

---

## 5. KV cache and per-sequence state

Per METHODOLOGY §2. This is the part most likely to be got wrong by anyone who assumes "24-layer
transformer".

### 5.1 KV cache — only 6 of 24 layers have one

GQA: `bytes/token/layer = 2 × n_kv_heads × head_dim × B`.

```
per full-attention layer, BF16 : 2 × 2 × 256 × 2 = 2,048 B
× 6 full-attention layers                        = 12,288 B/token = 12.00 KiB/token
per layer FP8   : 2 × 2 × 256 × 1   = 1,024 B  →  6,144 B/token =  6.00 KiB/token
per layer NVFP4 : 2 × 2 × 256 × 0.5 =   512 B  →  3,072 B/token =  3.00 KiB/token
```

The 18 GDN layers cache **0 bytes per token**. The `head_dim: 256` that makes the kernel story
awkward (§8.3) is fully paid back here: 2 KV heads × 6 layers is tiny even at 256-wide heads.

For scale: a same-size dense 24-layer GQA model with `head_dim 128` and 8 KV heads would cost
`2×8×128×2×24 = 98,304 B/token` — **exactly 8× more**. The hybrid schedule is an 8× KV win.

### 5.2 Fixed per-sequence state — the GDN recurrence

Linear-attention layers hold a constant-size state instead of a per-token cache:

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

METHODOLOGY §2's `S` (state slots per request) is **1** here — there is no speculative draft to
allocate extra slots for (§7), unlike Kimi-K3's `S = 5` under SGLang.

⚠️ **Still TO BE VERIFIED (engine-side, not config-side):** (a) whether vLLM stores the conv state at
`kernel` or `kernel − 1` width — the `kernel` variant costs 49,152 B/layer ⇒ **18.84 MiB/seq,
+1.1 %**, immaterial to any sizing decision; (b) whether `--mamba-ssm-cache-dtype bfloat16` is
honoured for this model, which would halve the recurrent state to **9.63 MiB/seq**.

**The crossover:**

```
19,537,920 B ÷ 12,288 B/token = 1,590 tokens
```

**Below ~1.6 K tokens the fixed GDN state costs more than the KV cache.** A fleet of many short text
requests is *state*-bound, not KV-bound. Above that — which is every video request — KV dominates.

### 5.3 Totals per sequence

`kv_total(ctx) = ctx × 12,288 + 19,537,920`.

| Context | KV BF16 | KV FP8 | + GDN state | **Total (BF16)** |
|---|---:|---:|---:|---:|
| **5,920 (30 s video)** | 69.4 MiB | 34.7 MiB | 18.63 MiB | **0.086 GiB** |
| 8 K | 96.0 MiB | 48.0 MiB | 18.63 MiB | **0.112 GiB** |
| **23,520 (2-min / 240-frame video)** | **275.6 MiB** | 137.8 MiB | 18.63 MiB | **0.287 GiB** |
| 32 K | 384.0 MiB | 192.0 MiB | 18.63 MiB | **0.393 GiB** |
| 128 K | 1,536.0 MiB | 768.0 MiB | 18.63 MiB | **1.518 GiB** |
| 262,144 (native max) | 3,072.0 MiB | 1,536.0 MiB | 18.63 MiB | **3.018 GiB** |
| 1 M | 12,288.0 MiB | 6,144.0 MiB | 18.63 MiB | **12.018 GiB** |

⚠️ **1 M is out of spec.** `max_position_embeddings` is 262,144 and `rope_type` is `default` with no
scaling config — confirmed against the gated `config.json`. The 1 M row is arithmetic only; running
there needs YaRN/NTK the checkpoint does not ship, with unknown quality. Do not plan on it.

### 5.4 Effect of the optional mechanisms

| Mechanism | Present? | Effect here |
|---|---|---|
| Sliding window | ❌ | the 6 full layers are **global**; no `W` cap |
| Cross-layer KV sharing | ❌ | all 6 cache independently |
| KV compression (ratio r) | ❌ | |
| Sparse / indexer attention | ❌ | no DSA-style kernel needed or available |
| **Hybrid linear attention** | ✅ | **the dominant effect: 75 % of layers cache nothing per token** |
| FP8 KV | engine feature | halves the table above; vLLM FlashAttention supports it when `flash_attn_supports_kv_cache_dtype` passes |
| NVFP4 KV | engine feature | FlashInfer exposes `nvfp4_kv_cache_full_dim` — ⚠️ unverified at `head_dim 256` |

### 5.5 Prefix caching — a hard constraint, and a workload that cannot use it

vLLM **refuses to start** Qwen3.5 with full Mamba prefix caching:

```python
if cache_config.mamba_cache_mode == "all":
    raise NotImplementedError(
        "Qwen3.5 currently does not support 'all' prefix caching, "
        "please use '--mamba-cache-mode=align' instead"
    )
```
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/qwen3_5.py),
and the vLLM Qwen3.5 recipe notes *"Prefix caching for Mamba cache align mode is currently
experimental"* [src](https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3.5.html).

**Why this matters more for Marlin than for a text model:** METHODOLOGY §5.4 asks for hit-rate
scenarios at 0 / 50 / 90 %. For Marlin the realistic hit rate is **~0 %**. The canonical prompts are
fixed but the *video* precedes and surrounds them, and every request carries a different video; the
only cacheable span is the ~30–40-token scaffold, i.e. **< 0.2 % of a 23,520-token request**. Prefix
caching is not a lever on this workload — do not budget for it. The one exception is a batch job
asking several different questions about the *same* video, which is exactly what
`--mamba-cache-mode=align` exists for. `.multi_find` is **not** that case: it re-trims the video, so
each pass has a different prefix.

---

## 6. Compute profile

### 6.1 FLOPs per token — text

```
FLOPs/token (decode or per-token prefill)
  = 2 × (language non-embed + lm_head)
  = 2 × (1,373,265,728 + 508,559,360)
  = 2 × 1,881,825,088
  = 3.764 GFLOP/token
```

Attention term, **6 full layers only**, causal (≈ ½ the dense-square count):

```
attention_flops(T) = 6 × 4 × n_q_heads × head_dim × T² / 2
                   = 6 × 4 × 8 × 256 × T² / 2
                   = 24,576 · T²
```

| T | linear part | attention part | total | attn share |
|---:|---:|---:|---:|---:|
| 5,920 (30 s video) | 22.28 TFLOP | 0.86 TFLOP | **23.14 TFLOP** | 3.7 % |
| 8,192 | 30.83 TFLOP | 1.65 TFLOP | **32.48 TFLOP** | 5.1 % |
| **23,520 (2-min video)** | 88.52 TFLOP | 13.60 TFLOP | **102.12 TFLOP** | 13.3 % |
| 131,072 | 493.31 TFLOP | 422.21 TFLOP | **915.52 TFLOP** | 46.1 % |
| 262,144 | 986.62 TFLOP | 1,688.85 TFLOP | **2,675.47 TFLOP** | 63.1 % |

The quadratic term only takes over past ~128 K. At Marlin's actual operating point (23.5 K) the model
is 87 % GEMM — **a throughput problem, not an attention problem.** And because the frame cap bounds
context at ~23.5 K (§6.3), the 128 K and 262 K rows are reachable only with text prompts, never with
video.

GDN layers add `≈ 4 × n_v_heads × d_k × d_v = 1,048,576 FLOP/token/layer × 18 = 18.9 MFLOP/token` —
0.5 % of the linear term, and in practice bandwidth- and kernel-bound (chunked scan, state
read-modify-write) rather than FLOP-bound. Treat GDN prefill cost as a kernel-efficiency question
(§8.3), not a roofline one.

### 6.2 Bytes read per decode step

Dense case: **all weights read every step, regardless of batch.**

```
weights_read (BF16) = 1,881,825,088 × 2 = 3,763,650,176 B = 3.764 GB
  (the vision tower is NOT read during decode — 331 M params idle after prefill)
weights_read (FP8 language linears, BF16 lm_head)  ≈ 2.391 GB
weights_read (INT4 W4A16 g128, BF16 lm_head)       ≈ 1.726 GB

kv_read(batch, ctx) = batch × ctx × 12,288 B
```

Total bytes per decode step = `3.764 GB + batch × ctx × 12,288`, recomputed cleanly:

| batch | ctx 5,920 (30 s) | ctx 8 K | ctx 23,520 (2 min) | ctx 128 K |
|---:|---:|---:|---:|---:|
| 1 | 3.837 GB | 3.865 GB | 4.053 GB | 5.374 GB |
| 8 | 4.346 GB | 4.570 GB | 6.077 GB | 16.66 GB |
| 32 | 6.091 GB | 6.987 GB | 13.02 GB | 54.13 GB |
| 128 | 13.07 GB | 16.65 GB | 40.78 GB | 205.8 GB |
| 256 | 22.38 GB | 29.53 GB | 77.80 GB | 408.0 GB |

*(Three cells of the previous revision's table did not reproduce from its own formula — 16.36 / 28.99
/ 81.4 GB. They are regenerated above and now do.)*

At the video context, **KV read overtakes weight read at batch ≈ 14** (`3.7637 / 0.289014 = 13.02`,
so batch 14 is the first batch where KV read exceeds weight read). Past that the model behaves like a
KV-streaming workload and HBM bandwidth is the only thing that matters — which is why quantising
weights below FP8 buys little at the operating batch.

### 6.3 Video token budget per request

**The formula, from the real preprocessing files.** `patch_size 16`, `temporal_patch_size 2`,
`spatial_merge_size 2`, so the patch embedding is a 3-D conv over `(2 frames, 16, 16)` — a temporal
**pair** of frames produces one patch grid:

```
frames                = clamp(FPS × duration, FPS_MIN_FRAMES, FPS_MAX_FRAMES)
                      = clamp(2.0 × duration_s, 4, 240)
ViT patches per temporal pair = pixels_per_frame ÷ (16 × 16)
LLM tokens per temporal pair  = ViT patches ÷ (spatial_merge² = 4)

at VIDEO_MAX_PIXELS = 200,704 px/frame (≈ 448 × 448):
  ViT patches per temporal pair = 200,704 ÷ 256 = 784
  LLM tokens per temporal pair  = 784 ÷ 4       = 196
  ⇒ 392 ViT patches per frame, 98 LLM tokens per frame
  ⇒ at 2 fps:  784 ViT patches per second of video, 196 LLM tokens per second of video
```

Equivalently and more usefully: **`LLM tokens = total_pixels ÷ 2048`** (verified against the base
card's own worked example: it recommends `longest_edge: 469,762,048` and calls that "224k video
tokens"; `469,762,048 ÷ 2048 = 229,376 = 224 × 1024` ✅).

**Prefill tokens per request** (add ~30–40 tokens of chat scaffold + instruction prompt):

| Video duration | 2 fps frames | **capped frames** | effective fps | LLM video tokens | ViT patches | **prefill tokens** | KV (BF16) |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2 s (min) | 4 | 4 | 2.0 | 392 | 1,568 | ~430 | 4.6 MiB |
| **30 s** | 60 | 60 | 2.0 | **5,880** | 23,520 | **~5,920** | 68.9 MiB |
| 60 s | 120 | 120 | 2.0 | 11,760 | 47,040 | ~11,800 | 137.8 MiB |
| **2 min** | 240 | **240 (cap)** | 2.0 | **23,520** | 94,080 | **~23,560** | 275.6 MiB |
| **10 min** | 1,200 | **240 (cap)** | **0.40** | **23,520** | 94,080 | **~23,560** | 275.6 MiB |
| 60 min | 7,200 | 240 (cap) | 0.067 | 23,520 | 94,080 | ~23,560 | 275.6 MiB |

**The 240-frame cap is the single most important serving fact about this model.** Prefill length is
**bounded at ~23.5 K tokens no matter how long the video is** — a 10-minute clip costs exactly the
same prefill as a 2-minute one. That is excellent for capacity planning (every request is the same
size, so `max_concurrency` is a constant, §8.5) and a **quality cliff for long video**: past 2 minutes
the sampler silently drops below 2 fps, giving the model 0.40 fps of a 10-minute clip and 0.067 fps
of an hour. "Second-precise timestamps" on a 10-minute video are being interpolated from frames
2.5 s apart. Chunk long videos client-side — which is effectively what `.multi_find`'s ffmpeg
trimming does — rather than feeding them whole.

**⚠️ TO BE VERIFIED — two preprocessing paths disagree by 1.91×, and the real config confirms the
conflict rather than resolving it.**

| Path | Source | Per-request video budget |
|---|---|---|
| **A — `qwen-vl-utils` env vars** (what `.caption()`/`.find()` use; `modeling_marlin.py` sets them via `os.environ.setdefault` and its module docstring calls them the required environment; [`MODEL_CARD.md`](MODEL_CARD.md) calls them the training-time setup) | `VIDEO_MAX_PIXELS=200704` **per frame**, `FPS=2.0`, `FPS_MAX_FRAMES=240`, `FPS_MIN_FRAMES=4`, `FORCE_QWENVL_VIDEO_READER=torchcodec` | 240 × 200,704 = 48,168,960 px ⇒ **23,520 tokens** |
| **B — HF `Qwen3VLVideoProcessor`** ([`processor_config.json`](processor_config.json)) | `fps: 2`, `min_frames: 4`, **`max_frames: 768`**, `size: {longest_edge: 25165824, shortest_edge: 4096}` **for the whole video** | 25,165,824 ÷ 2048 = **12,288 tokens** |

`48,168,960 ÷ 25,165,824 = 1.914`. `qwen-vl-utils` runs its own `smart_resize` and bypasses the
processor's `size`. An engine ingesting video through the HF video processor (vLLM/SGLang) therefore
gives the model **half the frames or half the resolution** the training-time path did — a silent
quality regression, not an error. **Plan on Path A's 23,520 tokens**, and treat any vLLM deployment as
needing `--media-io-kwargs` / `mm_processor_kwargs` tuning to reproduce it. Resolving which path the
model was trained on requires running both and comparing output; the gated files cannot settle it.

**Image budget** ([`preprocessor_config.json`](preprocessor_config.json)): `size.longest_edge
16,777,216` ⇒ `16,777,216 ÷ 1024 = 16,384` image tokens max; `shortest_edge 65,536` ⇒ 64 tokens min.

### 6.4 Vision-encoder FLOPs per frame

ViT attention scope, resolved from source: vLLM builds `cu_seqlens` as
`np.repeat(patches_per_frame, grid_thw[:, 0]).cumsum()`
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/qwen3_vl.py)
— each temporal group is its **own varlen attention segment**. Attention is per-frame-pair, not
global across the video. That is a ~120× saving at 240 frames and is why the ViT stays cheap.

```
per ViT patch (linear)       : 2 × 12,596,224 × 24 blocks = 604.6 MFLOP
per temporal-pair attention  : 4 × 1024 × 784² × 24       =  60.42 GFLOP
merger, per output LLM token : 2 × 25,174,016             =  50.35 MFLOP
```

**Per frame:** `392 × 604.6 M + ½ × 60.42 G + 98 × 50.35 M` = `237.0 + 30.2 + 4.9` =
**272.2 GFLOP/frame = 0.272 TFLOP/frame.**

| Input | ViT linear | ViT attention | Merger | **Total** |
|---|---:|---:|---:|---:|
| 1 image / 2 frames (784 patches) | 0.474 TFLOP | 0.060 TFLOP | 0.010 TFLOP | **0.544 TFLOP** |
| 60 frames (30 s) | 14.22 | 1.81 | 0.30 | **16.33 TFLOP** |
| **240 frames (2 min or longer)** | **56.89** | **7.25** | **1.18** | **65.32 TFLOP** |

*(Recomputed cleanly; the previous revision carried 0.547 / 16.40 / 65.61, ~0.45 % high, and said so
without regenerating them.)*

Against the LLM prefill's 102.12 TFLOP for the same clip, the vision encoder is **39 % of total
prefill compute** — substantial, and pure dense GEMM at `hidden 1024`, which runs at high MFU
everywhere. **Total prefill per 2-minute (or longer) video: 167.4 TFLOP.**

### 6.5 Decode cost for up to 2048 output tokens

`.caption()` defaults to **`max_new_tokens=2048`** (confirmed in the gated `modeling_marlin.py` and
[`MODEL_CARD.md`](MODEL_CARD.md)); `.find()` caps at 64; the author's demo Space uses 768. Decode is
bandwidth-bound at batch 1, and the KV term grows as the output extends the context:

`decode_step_time(n) ≈ (3.764 GB + (23,520 + n) × 12,288 B) / (HBM_BW × MBU)`

| GPU (pinned, METHODOLOGY §8) | TPOT at token 1 | TPOT at token 2048 | **768 tokens** | **2048 tokens** |
|---|---:|---:|---:|---:|
| `rtx6000-pro` RTX PRO 6000 SE (1.597 TB/s, MBU 0.60) | 4.23 ms | 4.26 ms | 3.25 s | **8.69 s** |
| `a100` A100 SXM (2.04, 0.75) | 2.65 ms | 2.67 ms | 2.04 s | **5.44 s** |
| `h100` H100 SXM (3.35, 0.75) | 1.61 ms | 1.63 ms | 1.24 s | **3.31 s** |
| `h200` H200 SXM (4.8, 0.75) | 1.13 ms | 1.14 ms | 0.87 s | **2.31 s** |
| `mi355x` MI355X (8.0, 0.50) | 1.01 ms | 1.02 ms | 0.78 s | **2.08 s** |
| `b200` B200 HGX (7.7, 0.60) | 0.88 ms | 0.89 ms | 0.67 s | **1.80 s** |
| `b300` B300 HGX (8.0, 0.60) | 0.84 ms | 0.85 ms | 0.65 s | **1.73 s** |
| `gb300` GB300, per GPU (8.0, 0.60) | 0.84 ms | 0.85 ms | 0.65 s | **1.73 s** |

`est.` per METHODOLOGY §4. **TPOT is essentially flat across the whole 2048-token generation**
(+0.8 %), because 2048 extra tokens add only 25 MB of KV against a 4.05 GB step. The practical read:
a full-length `.caption()` at batch 1 is a **1.7–8.7 second** job, dominated by decode, while the
entire video prefill (§6.4) is under a second everywhere.

---

## 7. Speculative decoding and MTP

### 7.1 What the checkpoint ships: nothing

| Evidence | Finding |
|---|---|
| `config.json` | declares `mtp_num_hidden_layers: 1`, `mtp_use_dedicated_embeddings: false` |
| `model.safetensors.index.json` (618 names) | **zero** tensors matching `mtp` |
| Base − Marlin parameter delta | `2,274,069,824 − 2,213,241,664 = 60,828,160` |
| Reconstructed MTP module | `52,433,408 + 2×2048 + 4096×2048 + 2048 = ` **`60,828,160`** |
| Match | **exact, to the parameter** |

The MTP head was dropped during fine-tuning; the config field was left behind. Read from the
checkpoint index, not inferred.

### 7.2 What that costs you

The base model's own recommended speculative recipes **do not work on Marlin**:

```shell
# Qwen/Qwen3.5-2B — works on the BASE model, NOT on Marlin-2B
vllm serve Qwen/Qwen3.5-2B --port 8000 --tensor-parallel-size 1 --max-model-len 262144 \
  --speculative-config '{"method":"qwen3_next_mtp","num_speculative_tokens":2}'

python -m sglang.launch_server --model-path Qwen/Qwen3.5-2B --port 8000 --tp-size 1 \
  --mem-fraction-static 0.8 --context-length 262144 \
  --speculative-algo NEXTN --speculative-num-steps 3 --speculative-eagle-topk 1 \
  --speculative-num-draft-tokens 4
```
[src](https://huggingface.co/Qwen/Qwen3.5-2B/raw/main/README.md) — both verbatim.

vLLM registers `Qwen3_5MTP` / `Qwen3_5MoeMTP` in `qwen3_5_mtp`
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/registry.py),
so the *engine* support exists — the *weights* do not. This also fixes METHODOLOGY §2's `S` at **1**
state slot per request (§5.2).

### 7.3 Published acceptance rates

⚠️ **TO BE VERIFIED — none found.** The vLLM recipe mentions `{"method": "mtp",
"num_speculative_tokens": 1}` for latency-optimised Qwen3.5-397B and states *"MTP-1 speculative
decoding for AMD GPUs is under development"*
[src](https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3.5.html) but publishes no acceptance
numbers. Do not assume a figure.

### 7.4 Is speculation even the right lever here?

Largely **no**:

- `.find()` caps at 64 tokens; `.caption()` defaults to 2048 but the demo Space uses 768.
- For `.find()`, **prefill dominates absolutely**: 167.4 TFLOP of prefill against 64 decode steps.
  Speculation cannot touch prefill.
- Speculation stops helping at large batch, and a video-captioning fleet is a batch workload by
  nature (§10.3) — exactly the regime where MTP contributes least.

**Recommendation:** treat MTP as unavailable and unneeded. If single-request `.caption()` latency
becomes binding, the levers in order are: (1) an EAGLE/EAGLE-3 draft head trained against Marlin —
none exists; ⚠️ unverified whether vLLM's `Eagle3Qwen3vlForCausalLM` registry entry could be adapted;
(2) re-attach the base model's MTP module and re-tune it; (3) a faster GPU (§6.5 — 768 tokens is
1.24 s on an H100). **DSpark:** not applicable, no DeepSeek-family component.

---

## 8. Engine support matrix

### 8.1 What "vLLM-compatible" concretely means

[`MODEL_CARD.md`](MODEL_CARD.md) claims, verbatim: *"2B params, vLLM- and swift-deploy-compatible,
runs on a single consumer GPU."* The precise, checkable answer:

**vLLM will not load `MarlinForConditionalGeneration` as shipped.** Its registry (re-fetched
2026-09-19) contains `Qwen3_5ForConditionalGeneration`, `Qwen3_5MoeForConditionalGeneration`,
`Qwen3_5ForCausalLM`, `Qwen3_5MoeForCausalLM`, `Qwen3_5MTP`, `Qwen3_5MoeMTP` and `ColQwen3_5` —
**and no `Marlin*` model entry**
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/registry.py).
SGLang likewise exports only
`EntryClass = [Qwen3_5MoeForConditionalGeneration, Qwen3_5ForConditionalGeneration]`
[src](https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/models/qwen3_5.py).
`auto_map` / `trust_remote_code` does **not** help: vLLM matches the `architectures` string against
its own registry, it does not execute the HF custom class.

**You must point vLLM at the Qwen3_5 class — and there is a supported flag for exactly that, so no
file editing is required:**

```shell
vllm serve NemoStation/Marlin-2B --port 8000 --tensor-parallel-size 1 \
  --hf-overrides '{"architectures": ["Qwen3_5ForConditionalGeneration"]}' \
  --max-model-len 32768 --mamba-cache-mode=align \
  --media-io-kwargs '{"video": {"num_frames": -1}}'
```

`--hf-overrides` is documented vLLM API for precisely this remapping — the supported-models page
gives the same shape for other models
(`--hf-overrides '{"architectures": ["Molmo2ForConditionalGeneration"], …}'`)
[src](https://docs.vllm.ai/en/latest/models/supported_models.html) — and `hf_overrides` is a
first-class `ModelConfig` field applied to the loaded HF config
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/model.py).

**This is safe because Marlin's own code says the forward pass is unmodified.** From the gated
`modeling_marlin.py`, verbatim:

> "This module subclasses the upstream `Qwen3_5ForConditionalGeneration` (native in
> `transformers >= 5.7.0`) and adds two convenience methods … **The forward pass is not modified:**
> we only add chat-template + generate + post-processing wrappers."

and the class declaration is literally
`class MarlinForConditionalGeneration(Qwen3_5ForConditionalGeneration):` — confirmed in the real
23,098-byte file, whose only additions are `.caption`, `.find`, `.multi_find`, the output parsers and
the `<think>` strippers. **A third party already shipped this remap**:
`prasannaJagadesh/marlin-2B-GPTQ-4BITS` carries `"architectures": ["Qwen3_5ForConditionalGeneration"]`
while keeping the `auto_map`
[src](https://huggingface.co/api/models/prasannaJagadesh/marlin-2B-GPTQ-4BITS).

**vLLM's video support for the target class is real:** the supported-models table lists
`Qwen3_5ForConditionalGeneration` with modalities **T + I<sup>E+</sup> + V<sup>E+</sup>** — text,
image and **video**, multiple per prompt [src](https://docs.vllm.ai/en/latest/models/supported_models.html).

**What you lose with the remap:** `.caption()` / `.find()` / `.multi_find()`, the automatic `<think>`
strip, the output parsers and the env-var preprocessing defaults — all client-side wrappers you
reimplement from the canonical prompts in §1.4. **What you must re-supply:** both EOS ids (§1.3), the
Path A frame/pixel budget (§6.3), and the `<think>`-prefix strip.

⚠️ **Still TO BE VERIFIED (requires actually running it):** that all 618 tensor names map 1:1 onto
vLLM's `Qwen3_5ForConditionalGeneration` loader, and how vLLM handles the materialised
`lm_head.weight` against `tie_word_embeddings: true` (most likely benign — it loads the explicit
tensor). Nothing in the now-available files contradicts it; nothing in them proves it either.

**Verdict on the card's claim:** "vLLM-compatible" is **true of the weights and false of the
checkpoint as shipped**. It costs one flag, not a port. Budget an afternoon of validation, not a week
of engineering.

### 8.2 The vLLM Transformers fallback does not rescue this

`--model-impl transformers` is the usual escape hatch for an unregistered architecture. It is
**unusable here**, on independent grounds, per the docs' own caveats
[src](https://docs.vllm.ai/en/latest/models/supported_models.html):

- ❌ **"Vision-language models currently accept only image inputs"** — Marlin is a *video* model.
- ❌ Hybrid / linear-attention mechanisms — Marlin is 18/24 Gated DeltaNet.
- ❌ Mamba-style state-space models — the GDN recurrent state is exactly that.
- ❌ no quantization schemes, no alternative attention backends.

Any one is disqualifying. Use `--hf-overrides`, not `--model-impl transformers`.

### 8.3 Attention kernels per GPU — the `head_dim: 256` story

This is where the GPUs actually differentiate, and it is entirely driven by the 6 full-attention
layers being 256-wide.

**FlashAttention version selection in vLLM**
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/attention/backends/fa_utils.py):

```python
if device_capability.major == 9 and is_fa_version_supported(3):
    fa_version = 3     # Hopper (SM90): prefer FA3
elif device_capability.major == 10 and is_fa_version_supported(4):
    fa_version = 4     # Blackwell (SM100+, restricted to SM100 for now): prefer FA4
else:
    fa_version = 2     # Fallback to FA2
```

`head_dim 256` is accepted by **every** FA version — `supports_head_size` returns `True` for
`head_size <= 256` (up to 512 under FA4)
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/attention/backends/flash_attn.py),
and upstream FA2 states *"All head dimensions up to 256"*
[src](https://github.com/Dao-AILab/flash-attention). So no GPU is blocked. But:

**There is a dedicated FA4 head-dim-256 kernel, and it is Blackwell-datacenter-only:**

```python
def uses_fa4_hd256_kernel(head_size, head_size_v=None) -> bool:
    """Return whether FA4 uses its dedicated hd256 kernel."""
    if head_size != 256: return False
    if head_size_v is not None and head_size_v != 256: return False
    capability = current_platform.get_device_capability()
    return capability is not None and capability.major in (10, 11)

FA4_HD256_PAGE_SIZE = 128
```

Marlin hits `head_size == 256` **exactly**. On SM100/SM10x it gets a purpose-built kernel; elsewhere
it does not. The kernel additionally requires the **KV block size to be a multiple of 128** —
*"Larger blocks are split into 128-token kernel pages"* — and falls back if `mm_prefix` bidirectional
attention, R-SWA, softcap, sinks or `decode_context_parallel_size > 1` are in play. **`mm_prefix` is
a live concern for a VLM**; `supports_mm_prefix()` returns `is_fa_version_supported(4)`.

**GDN kernels — FlashQLA, and the Ampere/ROCm cliff.** Qwen ships a dedicated Gated-DeltaNet kernel:

| Property | Value | Source |
|---|---|---|
| Supported archs | **"SM90, SM100, SM103, SM120 or SM121"** | [FlashQLA README](https://github.com/QwenLM/FlashQLA) |
| Requirements | "CUDA 12.8 or above", "PyTorch 2.8 or above" | same |
| Speedup vs FLA Triton 0.5.0 | **"2-3× forward speedup"**, "2× backward speedup" | same |
| Tuned for | "head configurations used by the Qwen3.5 / Qwen3.6 family" | same |

**SM80 (A100) is not on that list, and neither is ROCm.** A100 and MI355X fall back to the FLA Triton
path [src](https://github.com/fla-org/flash-linear-attention), so the penalty on **18 of 24 layers**
is roughly the quoted 2–3× on the GDN forward pass.

⚠️ **TO BE VERIFIED:** FlashQLA's raw benchmark files (`benchmark_results_H200.txt`,
`…_GB200.txt` — unreachable, 404/rate-limited) and whether vLLM/SGLang actually dispatch to FlashQLA
rather than their own Triton GDN kernels for this head config. SGLang carries its own
`triton_gdn_fused_proj` with a `qwen3_5_gdn_prefill_projection_views` path and an
`SGLANG_ENABLE_GDN_DECODE_FUSED_PROJ_CONV` env flag, so it may not use FlashQLA at all.

**Per-GPU kernel summary:**

| GPU (pinned slug) | CC | FA version (vLLM) | FA4 hd256 kernel | FlashQLA (GDN) | Notes |
|---|---|---|---|---|---|
| `a100` A100 SXM | 8.0 | **FA2** | ❌ | ❌ **unsupported** | weakest kernel story; no FP8 either |
| `h100` H100 SXM | 9.0 | **FA3** | ❌ (SM90 has an hd512 path instead) | ✅ | best-trodden path |
| `h200` H200 SXM | 9.0 | **FA3** | ❌ | ✅ | + FlashQLA H200 benchmarks exist |
| `b200` B200 HGX | 10.0 | **FA4** | ✅ | ✅ SM100 | best-supported |
| `b300` / `gb300` | 10.x | **FA4** | ✅ | ✅ SM100/SM103 | best-supported |
| `rtx6000-pro` RTX PRO 6000 SE | **12.0** [src](https://developer.nvidia.com/cuda-gpus) | **FA2** (major 12 ≠ 9, 10) | ❌ (`major in (10,11)`) | ✅ SM120 (forward pass, v0.1.2+) | **Blackwell silicon, Ampere-era attention path in vLLM** |
| `mi355x` MI355X | gfx950 | n/a — `get_flash_attn_version` returns `None` on ROCm | ❌ | ❌ | AITER / Triton; see §8.5 |

The RTX PRO 6000 row is the non-obvious one and is easy to get wrong in either direction: Blackwell
silicon, but compute capability **12.0** (NVIDIA's CUDA GPU table lists both the Workstation and
Server Editions at 12.0, with H100/H200 at 9.0 and B200 at 10.0), so vLLM's `major == 10` test fails
and it drops to **FA2** — while simultaneously *qualifying* for FlashQLA, which lists SM120
explicitly.

### 8.4 Engine matrix

| Engine | Supports Marlin? | Min version | Path |
|---|---|---|---|
| **transformers** | ✅ **the only first-class path** | **`>= 5.7.0`**, `torch >= 2.11.0`, `torchcodec`, `qwen-vl-utils >= 0.0.14`, `av`, `pillow` | `trust_remote_code=True` → `MarlinForConditionalGeneration` via `auto_map` |
| **vLLM** | ⚠️ **yes, with `--hf-overrides`** (§8.1) | main / nightly (base card: *"vLLM from the main branch … is required for Qwen3.5"*) | native `qwen3_5.py`, `IsHybrid`, tower marked `{"image", "video"}` |
| **SGLang** | ⚠️ same remap; **video ingestion unconfirmed** | main | `qwen3_5.py`, `Qwen3_5ForConditionalGeneration` |
| **TensorRT-LLM** | ⚠️ **no evidence of Qwen3.5 hybrid support found** | — | ⚠️ TO BE VERIFIED |
| **Dynamo** | ⚠️ inherits vLLM/SGLang backend behaviour | — | ⚠️ TO BE VERIFIED |
| **llama.cpp** | ✅ GGUF exists and is heavily used | ⚠️ version unknown | `llama-mtmd-cli … --video` |
| **MLX (Apple)** | ✅ official 8-bit build by the author | ⚠️ unknown | §9 |
| **ms-swift** | ✅ claimed by the card ("swift-deploy-compatible") | ⚠️ unknown | — |

`transformers` main is at `5.18.0.dev0`, so the `>= 5.7.0` floor is long satisfied. `qwen3_5` is a
**native** transformers architecture (`src/transformers/models/qwen3_5/` exists upstream), which is
why the card notes *"transformers >= 5.7.0 (for native `qwen3_5` architecture)"*.

### 8.5 Sizing, parallelism and per-GPU caveats

**Sizing settles the parallelism question immediately.** Marlin needs **4.43 GB of weights** and
**0.287 GiB per concurrent 2-minute video**. `--tensor-parallel-size 1` on everything. No PP, no EP,
no multi-node, no fabric question — the base card's own recipes use `--tp-size 1` for the 2B.

**Max concurrent sequences** — METHODOLOGY §3, `usable = 0.90 × HBM(as deployed)`, weights 4.426 GB
resident (tie honoured), 4 GiB activation workspace, `ctx × 12,288 + 19,537,920` per sequence. HBM
figures are the METHODOLOGY §8 pinned **as-deployed** values:

| GPU | HBM as deployed | KV budget | @5,920 (30 s) | **@23,520 (2 min)** | @32 K | @128 K | @262 K |
|---|---:|---:|---:|---:|---:|---:|---:|
| `a100` A100 SXM | 80 GB | 58.9 GiB | 715 | **205** | 149 | 38 | 19 |
| `h100` H100 SXM | 80 GB | 58.9 GiB | 715 | **205** | 149 | 38 | 19 |
| `rtx6000-pro` RTX PRO 6000 SE | 96 GB | 72.3 GiB | 878 | **251** | 183 | 47 | 23 |
| `h200` H200 SXM | 141 GB | 110.1 GiB | 1,337 | **383** | 279 | 72 | 36 |
| `b200` B200 HGX | 180 GB | 142.8 GiB | 1,734 | **496** | 363 | 94 | 47 |
| `b300` B300 HGX | **268 GB** | 216.5 GiB | 2,629 | **753** | 550 | 142 | 71 |
| `gb300` GB300 NVL72, per GPU | 288 GB (**279 usable**) | 225.7 GiB | 2,741 | **785** | 574 | 148 | 74 |
| `mi355x` MI355X | 288 GB | 233.3 GiB | 2,832 | **811** | 593 | 153 | 77 |

`est.` **Capacity is never the binding constraint above 24 GB.** And because the frame cap bounds
every video request at ~23.5 K tokens (§6.3), the bolded column is the *only* one a video fleet ever
operates in — concurrency is a constant, not a distribution.

> **Capacity correction applied here, per METHODOLOGY §8.** `b300` is **268 GB as deployed**
> (2,144 GB per 8-GPU node; DGX B300 262.5) — **not 288 GB**, and not the 262.5 the previous revision
> used. **288 GB is the GB300 NVL72 figure** (≈ 279 usable), and separately the correct MI355X
> figure. Every row above is re-derived against the pinned values.

**Per-GPU notes:**

- **`a100` A100 (SM80)** — no FP8 tensor cores, BF16 only, 2.04 TB/s. **No FlashQLA** ⇒ FLA Triton on
  18 of 24 layers. FA2 only. Works; weakest per-dollar story for *this* architecture.
- **`h100` / `h200` (SM90)** — FA3 + FlashQLA, FP8 available. H200's 4.8 TB/s against H100's
  3.35 TB/s is a straight **1.433×** on decode, which is what this workload is bound by.
- **`b200` / `b300` / `gb300` (SM100/10x)** — FA4 **with the dedicated hd256 kernel**, FlashQLA,
  NVFP4 hardware. Constraint: KV block size must be a multiple of `FA4_HD256_PAGE_SIZE = 128`.
  **B300's BF16 is 2,250 TFLOPS dense — identical to B200**; Blackwell Ultra's 1.5× uplift is
  **FP4-only** (13,500 vs 9,000 dense). GB300 is the higher-clocked part at 2,500 BF16 dense, which
  is why its rows differ slightly from B300's.
- **`rtx6000-pro` RTX PRO 6000 Blackwell Server Edition** — 96 GB GDDR7 @ **1,597 GB/s** (Workstation
  Edition is 1,792; **clouds rent Server Edition**, so every row in this document uses 1,597),
  480 TFLOPS BF16 dense per `gpus/rtx6000-pro.md` §3c. FlashQLA ✅ (SM120), FA4 hd256 ❌, vLLM FA
  version ⇒ **FA2**. Strong *capacity* play, mediocre *bandwidth* play: 1,597 GB/s is 3.0× below
  H200. Best fit: workstation / on-prem / air-gapped deployments where 96 GB in one PCIe slot is the
  requirement.
- **`mi355x` MI355X (ROCm)** — `get_flash_attn_version` returns `None` on ROCm outright
  (*"ROCm doesn't use vllm_flash_attn"*); AITER/Triton instead. FlashQLA ❌. vLLM's recipe confirms
  Qwen3.5 *"verified on 8x H200 GPUs and 8x MI300X/MI355X GPUs"* — but that verification is for the
  **397B MoE text** path, ⚠️ **not** this 2B hybrid **video** path.

### 8.6 Launch recipes

**transformers** — from [`MODEL_CARD.md`](MODEL_CARD.md):

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

The author's reference deployment (the Gradio Space) uses `attn_implementation="sdpa"` — **not**
FlashAttention — on **ZeroGPU A10G**, and sets the Path A env vars explicitly:

```python
os.environ.setdefault("FORCE_QWENVL_VIDEO_READER", "torchcodec")
os.environ.setdefault("VIDEO_MAX_PIXELS", "200704")
os.environ.setdefault("FPS", "2.0")
os.environ.setdefault("FPS_MAX_FRAMES", "240")
os.environ.setdefault("FPS_MIN_FRAMES", "4")
```

That Space is the author's own evidence for "runs on a single consumer GPU".

**vLLM** — full command in §8.1. Multimodal/Mamba flags from the vLLM Qwen3.5 recipe:
`--mm-encoder-tp-mode data`, `--mm-processor-cache-type shm`, `--mamba-cache-mode=align`. Per-request
frame control (vLLM-only): `extra_body={"mm_processor_kwargs": {"fps": 2, "do_sample_frames": True}}`.

**SGLang** (base-model recipe; apply the same architecture remap):

```shell
python -m sglang.launch_server --model-path <marlin-with-remapped-architectures> --port 8000 \
  --tp-size 1 --mem-fraction-static 0.8 --context-length 32768
```

**llama.cpp** (verbatim from the GGUF repo):

```bash
llama-mtmd-cli -m marlin-2b-text.gguf --mmproj marlin-2b.gguf --video input.mp4 \
  -p "Describe the scene and events."
```

### 8.7 Open engine issues

| Issue | Status | Source |
|---|---|---|
| `mamba_cache_mode == "all"` rejected for Qwen3.5 | **hard error**; use `--mamba-cache-mode=align` | [vLLM qwen3_5.py](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/qwen3_5.py) |
| Mamba `align` prefix caching | "currently experimental" | [vLLM recipe](https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3.5.html) |
| `causal_conv1d_update` assertion when "cuda graph capture size is larger than mamba cache size" | known; lower `--max-cudagraph-capture-size` (default 512) | same |
| MTP-1 spec-dec on AMD | "under development" | same |
| `MarlinForConditionalGeneration` unregistered | §8.1 — one `--hf-overrides` flag | [vLLM registry](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/registry.py) |
| Transformers backend cannot run hybrid/SSM/video | §8.2 | [vLLM docs](https://docs.vllm.ai/en/latest/models/supported_models.html) |
| Path A vs Path B video token mismatch | §6.3 | this document |
| `use_cache: false` in `config.json` | §2.1 | [`config.json`](config.json) |
| Split EOS ids | §1.3 | [`generation_config.json`](generation_config.json) |

⚠️ Naming collision worth flagging for anyone grepping issue trackers: **"Marlin" is also the name of
vLLM's INT4 W4A16 GEMM kernel.** Searches for "vllm marlin" return the kernel, not this model.

---

## 9. Available quantised variants

All figures from the HF API. Every `total` equals Marlin's 2,213,241,664 unique params **except**
`tintwotin/Marlin-2B-SDNQ-int8`, which reports `2,223,966,016 = 2,213,241,664 + 10,724,352` — SDNQ
stores dequantisation scales as F32 tensors that HF counts as parameters. Its dtype counts still
reproduce the §3.6 split exactly.

| Repo | Format | Reported tensors | On-disk | Who | Notes |
|---|---|---|---:|---|---|
| [`NemoStation/Marlin-2B`](https://huggingface.co/api/models/NemoStation/Marlin-2B) | BF16 | `BF16: 2,721,801,024` | 5.476 GB | **author** | gated; reference |
| [`NemoStation/Marlin-2B-MLX-8bit`](https://huggingface.co/api/models/NemoStation/Marlin-2B-MLX-8bit) | MLX 8-bit | `U32: 1,881,276,416`, `BF16: 331,965,248` | **2.683 GB** | **author** | ungated; quantises language **incl. embeddings**; vision + 548,672 norm/SSM params stay BF16. Apple Silicon only. |
| [`junwatu/Marlin-2B-MLX-8bit`](https://huggingface.co/api/models/junwatu/Marlin-2B-MLX-8bit) | MLX 8-bit | — | — | community | duplicate of the above |
| [`prasannaJagadesh/marlin-2B-GPTQ-4BITS`](https://huggingface.co/api/models/prasannaJagadesh/marlin-2B-GPTQ-4BITS) | **GPTQ INT4 W4A16** | `I32: 1,371,537,408`, `BF16: 841,704,256` | **2.418 GB** | community | `{bits: 4, format: "gptq"}`. **Already remaps `architectures` → `Qwen3_5ForConditionalGeneration`** (§8.1) |
| [`tintwotin/Marlin-2B-SDNQ-int8`](https://huggingface.co/api/models/tintwotin/Marlin-2B-SDNQ-int8) | SDNQ INT8 | `I8: 1,372,717,056`, `BF16: 840,524,608`, `F32: 10,724,352` | **3.117 GB** | community | `I8` count is **exactly** the derived quantisable set (§3.6) |
| [`jadeonrails/marlin-2b-gguf`](https://huggingface.co/api/models/jadeonrails/marlin-2b-gguf) | **GGUF** | `gguf.total: 2,390,384,448`, `architecture: "qwen35"`, `context_length: 262144` | 5.461 GB | community | text 4.79 GB + mmproj 668 MB. **265,785 downloads — 54× the original repo.** |
| [`lunahr/Marlin-2B-ungated`](https://huggingface.co/api/models/lunahr/Marlin-2B-ungated) | BF16 mirror | identical weights | 5.464 GB | community | weights/config/tokenizer/template bit-identical to the gated original; its `modeling_marlin.py` is the pre-`multi_find` version |
| [`deAPI-ai/marlin-2b`](https://huggingface.co/api/models/deAPI-ai/marlin-2b) | ⚠️ unknown | — | — | community | |

**GGUF split, cross-validated.** `marlin-2b.gguf` is 668,226,592 B; the vision tower at F16 is
`331,416,576 × 2 = 662,833,152 B` ⇒ +5.4 MB metadata ✅. `marlin-2b-text.gguf` is 4,792,827,296 B;
the text tower with **untied** embed + output at F16 is `(1,881,825,088 + 508,559,360) × 2 =
4,780,768,896 B` ⇒ +12 MB metadata ✅. And `gguf.total: 2,390,384,448` equals
`1,881,825,088 + 508,559,360` to the parameter — a third independent confirmation of §3.3.

**Notably absent:** ❌ no NVFP4, MXFP4, AWQ or FP8 checkpoint; ❌ no EAGLE/MTP draft head.

**⚠️ Evaluations of the quantised variants: none published.** Not one repo reports a CaReBench /
DREAM-1K / TimeLens score, a perplexity delta or any accuracy measurement. For a model whose value is
*second-precise timestamps*, INT4 numeric error landing on the timestamp digits is a specific,
plausible and entirely unmeasured risk. Treat every quantised variant as unvalidated until you run
the benchmarks yourself.

---

## 10. Benchmarks and estimated performance

### 10.1 Quality — vendor claims only

From [`MODEL_CARD.md`](MODEL_CARD.md):

| Benchmark | Claim | Form |
|---|---|---|
| **CaReBench** ([2501.00513](https://arxiv.org/abs/2501.00513)) | "Tops the CaReBench leaderboard"; closes the gap to the Gemini-2.5-Flash teacher "to within 0.21 / 0.43 of 10" | marketing claim + figure |
| **DREAM-1K** ([2407.00634](https://arxiv.org/abs/2407.00634)) | "sits between Tarsier-34B and Gemini-1.5-Pro" | marketing claim + figure |
| **TimeLens-Bench** ([2512.14698](https://arxiv.org/abs/2512.14698)) | "beats Qwen2.5-VL-7B by **+6.4 mIoU** and matches Gemini-2.0-Flash" | marketing claim + figure |

⚠️ **Vendor marketing, labelled as such per METHODOLOGY §7.** Results are a three-panel PNG with **no
numeric table**, `"model-index": null`, and no third-party reproduction. The one quantitative figure
(+6.4 mIoU) is stated without an absolute baseline. The card concedes the frontier: *"Specialised 7B+
models on these benchmarks (TimeLens-7B/8B, MiMo-VL, Time-R1) still carry the upper frontier."*

**Training provenance, relevant to trusting the numbers:** ~400 K clip-level annotations from
ActivityNet, LSMDC, Charades, Charades-Ego, TREC-VTT, WebVid-10M, HC-STVG, VidSTG and TimeLens,
densely re-annotated by **Gemini-3-Flash in thinking mode**; two-stage SFT → **SimPO**, judged by
Gemini-3-Flash; trained "on a single H100". ActivityNet, Charades and TimeLens appear in **both** the
training-source list and the evaluation list; the card does not state how contamination was
controlled.

### 10.2 Measured performance — none exists

⚠️ **TO BE VERIFIED, comprehensively.** No throughput or latency measurement for Marlin-2B on any
hardware could be located: the card has no Performance/Speed/Hardware section (the only perf-adjacent
sentence is *"runs on a single consumer GPU"*), HF `model-index` is `null`, it is not an MLPerf model,
and InferenceX/InferenceMAX carries no Marlin rows (per
[`cross-cutting/inferencex-api.md`](../../cross-cutting/inferencex-api.md), the RTX PRO 6000 has
Qwen3.5 rows only). Qwen3.5-2B *base* numbers, if they appear there, would transfer well — the
backbone is identical bar the MTP head.

**The one real-world timing signal** is the author's demo Space, and it must be read carefully: its
`@spaces.GPU(duration=...)` values (75 s for Find, up to 180 s for Caption) are **ZeroGPU quota
reservations, not measurements**. They bound cold start + video download + torchcodec decode +
inference on a time-shared A10G. Do not cite them as model latency.

### 10.3 Estimated performance (`est.`, METHODOLOGY §4)

Roofline only: `decode_step_time ≈ max(bytes_per_step / (HBM_BW × MBU), 2 × active × batch /
(peak_dense × MFU))`. **All TFLOPS are dense**, never sparse. Pinned inputs per METHODOLOGY §8:

| GPU | HBM (as deployed) | HBM BW | BF16 dense | MBU | MFU |
|---|---:|---:|---:|---:|---:|
| `a100` A100 SXM | 80 GB | 2.04 TB/s | 312 | 0.75 | 0.40 |
| `rtx6000-pro` RTX PRO 6000 SE | 96 GB | **1.597 TB/s** | 480 `est.` | 0.60 | 0.40 |
| `h100` H100 SXM | 80 GB | 3.35 TB/s | 989.5 | 0.75 | 0.40 |
| `h200` H200 SXM | 141 GB | 4.8 TB/s | 989.5 | 0.75 | 0.40 |
| `b200` B200 HGX | 180 GB | **7.7 TB/s** | 2,250 | 0.60 | 0.40 |
| `b300` B300 HGX | **268 GB** | 8.0 TB/s | **2,250** | 0.60 | 0.40 |
| `gb300` GB300, per GPU | 288 (279 usable) | 8.0 TB/s | 2,500 | 0.60 | 0.40 |
| `mi355x` MI355X | 288 GB | 8.0 TB/s | 2,500 | 0.50 | 0.30 |

Three corrections from the repo-wide fact-check are baked into every row: **B200 plans at 7.7 TB/s**
(the previous revision's rows implied ~8.4 and were ~9 % optimistic); **RTX PRO 6000 plans at
1,597 GB/s Server Edition** (not 1,792 Workstation — the previous rows were ~11 % optimistic for
rented silicon); **B300 BF16 is 2,250 dense, identical to B200** (the 1.5× Blackwell Ultra uplift is
FP4-only). NVIDIA's published H100/H200 "1,979 teraFLOPS BF16" is the **sparse** column; 989.5 dense
is used here, and likewise MI355X 2,500 dense (not 5,000 sparse) and RTX PRO 6000 480 dense (not the
"1 PFLOPS" sparse product-page figure).

**Decode, 2-minute video context (23,520 tokens), BF16 weights + BF16 KV** — TPOT / aggregate tok/s:

| GPU | b=1 | b=8 | b=32 | b=64 | b=128 | b=256 |
|---|---|---|---|---|---|---|
| RTX PRO 6000 SE | 4.2 ms / 236 | 6.3 ms / 1,262 | 13.6 ms / 2,356 | 23.2 ms / 2,755 | 42.5 ms / 3,009 | 81.1 ms / 3,155 |
| A100 SXM | 2.6 ms / 378 | 4.0 ms / 2,015 | 8.5 ms / 3,763 | 14.5 ms / 4,399 | 26.6 ms / 4,805 | `infeasible (KV)` |
| H100 SXM | 1.6 ms / 620 | 2.4 ms / 3,308 | 5.2 ms / 6,179 | 8.9 ms / 7,224 | 16.2 ms / 7,891 | `infeasible (KV)` |
| H200 SXM | 1.1 ms / 888 | 1.7 ms / 4,740 | 3.6 ms / 8,853 | 6.2 ms / 10,350 | 11.3 ms / 11,306 | 21.6 ms / 11,853 |
| MI355X | 1.0 ms / 987 | 1.5 ms / 5,267 | 3.3 ms / 9,837 | 5.6 ms / 11,500 | 10.2 ms / 12,562 | 19.4 ms / 13,170 |
| B200 HGX | 0.9 ms / 1,140 | 1.3 ms / 6,083 | 2.8 ms / 11,362 | 4.8 ms / 13,283 | 8.8 ms / 14,509 | 16.8 ms / 15,212 |
| B300 HGX | 0.8 ms / 1,184 | 1.3 ms / 6,320 | 2.7 ms / 11,804 | 4.6 ms / 13,800 | 8.5 ms / 15,075 | 16.2 ms / 15,804 |
| GB300 (per GPU) | 0.8 ms / 1,184 | 1.3 ms / 6,320 | 2.7 ms / 11,804 | 4.6 ms / 13,800 | 8.5 ms / 15,075 | 16.2 ms / 15,804 |

Per METHODOLOGY §3's consistency rule, b=256 at 23,520 ctx exceeds `max_concurrency` on the 80 GB
cards (205 < 256) and is printed as `infeasible (KV)` rather than as a number. Every other cell is
within budget.

**Decode, 8 K text context:**

| GPU | b=1 | b=8 | b=32 | b=64 | b=128 | b=256 |
|---|---|---|---|---|---|---|
| RTX PRO 6000 SE | 4.0 ms / 248 | 4.8 ms / 1,678 | 7.3 ms / 4,390 | 10.7 ms / 6,009 | 17.4 ms / 7,367 | 30.8 ms / 8,306 |
| A100 SXM | 2.5 ms / 396 | 3.0 ms / 2,679 | 4.6 ms / 7,009 | 6.7 ms / 9,594 | 10.9 ms / 11,763 | 19.3 ms / 13,262 |
| H100 SXM | 1.5 ms / 650 | 1.8 ms / 4,399 | 2.8 ms / 11,511 | 4.1 ms / 15,755 | 6.6 ms / 19,317 | 11.8 ms / 21,779 |
| H200 SXM | 1.1 ms / 932 | 1.3 ms / 6,303 | 1.9 ms / 16,493 | 2.8 ms / 22,575 | 4.6 ms / 27,678 | 8.2 ms / 31,205 |
| MI355X | 1.0 ms / 1,035 | 1.1 ms / 7,004 | 1.7 ms / 18,325 | 2.6 ms / 25,083 | 4.2 ms / 30,753 | 7.4 ms / 34,673 |
| B200 HGX | 0.8 ms / 1,196 | 1.0 ms / 8,089 | 1.5 ms / 21,166 | 2.2 ms / 28,971 | 3.6 ms / 35,520 | 6.4 ms / 40,047 |
| B300 HGX | 0.8 ms / 1,242 | 1.0 ms / 8,405 | 1.5 ms / 21,990 | 2.1 ms / 30,100 | 3.5 ms / 36,904 | 6.2 ms / 41,607 |
| GB300 (per GPU) | 0.8 ms / 1,242 | 1.0 ms / 8,405 | 1.5 ms / 21,990 | 2.1 ms / 30,100 | 3.5 ms / 36,904 | 6.2 ms / 41,607 |

**METHODOLOGY §4's SLO of TPOT ≤ 50 ms is met by every pinned GPU at every batch up to 128.** This
model does not have a latency problem.

**End-to-end, one 2-minute video, batch 1** (240 frames ⇒ ViT 65.32 TFLOP + LLM prefill 102.12 TFLOP
+ 768 decode tokens):

| GPU | ViT | LLM prefill | Decode 768 tok | **Total** | videos/h/GPU @b=1 |
|---|---:|---:|---:|---:|---:|
| RTX PRO 6000 SE | 0.340 s | 0.532 s | 3.252 s | **4.12 s** | 873 |
| A100 SXM | 0.523 s | 0.818 s | 2.037 s | **3.38 s** | 1,066 |
| H100 SXM | 0.165 s | 0.258 s | 1.240 s | **1.66 s** | 2,164 |
| H200 SXM | 0.165 s | 0.258 s | 0.866 s | **1.29 s** | 2,794 |
| MI355X | 0.087 s | 0.136 s | 0.779 s | **1.00 s** | 3,592 |
| B200 HGX | 0.073 s | 0.113 s | 0.674 s | **0.86 s** | 4,184 |
| B300 HGX | 0.073 s | 0.113 s | 0.649 s | **0.84 s** | 4,310 |
| GB300 (per GPU) | 0.065 s | 0.102 s | 0.649 s | **0.82 s** | 4,408 |

**Read this carefully: 71–79 % of batch-1 time is decode of 768 caption tokens.** Prefill — the whole
video, encoder included — is under a second on every datacenter GPU. At the real `.caption()` default
of `max_new_tokens=2048`, decode grows to 1.73–8.69 s (§6.5) and dominates completely. **Batching is
the entire optimisation** at batch 1, and the roofline says it works: aggregate throughput rises
12–13× from b=1 to b=128.

⚠️ These estimates carry **no correction for the A100/MI355X FlashQLA gap** (§8.3), which would slow
their *prefill* columns on 18 of 24 layers by an unquantified amount. Treat both as optimistic.

⚠️ **The real bottleneck may not be the GPU.** torchcodec decode throughput for 240 frames at
448×448 is unmeasured and absent from every source consulted; the demo Space's 75–180 s reservations
against a few seconds of predicted GPU work on an A10G-class card suggest video I/O and decode
dominate end-to-end service latency.

### 10.4 Is a small card the right target?

**Yes for batch, no for interactive** — and the qualification is not the obvious one.

Weights are 4.43 GB; even a 16 GB card holds the model plus ~40 concurrent 2-minute videos. The
gating resource is **bandwidth**, not capacity. But at batch 1 the small cards are slow enough to
matter: the RTX PRO 6000 SE takes 4.12 s per 2-minute video at 768 output tokens and 8.7 s at the
2048 default. For an interactive demo that is visible latency; for a batch captioning pipeline — what
this model is *for* — it is irrelevant, because you run at b≥64 where aggregate throughput counts.

**The counterpoint nobody should skip:** at b=128 an H200 does 11,306 tok/s against the RTX PRO 6000
SE's 3,009 — **3.8×** — while the planning-price gap is only 2.2× ($3.99 vs $1.80 `low`). §11 shows
the big card wins on $/video at both the `low` and `high` tier. **Do not reach for the cheap card by
reflex.**

⚠️ L4, L40S and A10G are **not in the METHODOLOGY §8 pinned set** and are not priced in
[`cross-cutting/cloud-pricing.md`](../../cross-cutting/cloud-pricing.md). They are omitted from every
table in this revision rather than carried at unsourced specs and neighbouring-GPU prices. If a
deployment actually targets them, spec and price them first.

### 10.5 Does NVFP4 on B300/GB300 help this model?

**Largely no, and for a reason specific to Marlin.**

1. **No NVFP4 checkpoint exists** (§9). Someone must quantise it; nobody has.
2. **Decode is bandwidth-bound, not FLOP-bound.** NVFP4 would cut weight bytes 4.43 → 2.45 GB (§4),
   which helps at **low batch only**. At b≥14 with video contexts, KV traffic dominates weight
   traffic (§6.2), and NVFP4 *weights* do nothing for KV.
3. **Prefill is where FP4 FLOPs would pay** — 167.4 TFLOP per video — but that is already **0.19 s**
   on a B300 (`167.4 / (2,250 × 0.40)`). NVFP4 at 13,500 TFLOPS dense on `sm_103` could in principle
   take it toward ~0.04 s, saving ~0.15 s out of a **0.84 s** batch-1 request. Real, small, and
   unavailable.
4. **The larger B300 lever is not NVFP4 at all — it is the FA4 hd256 kernel** (§8.3), which exists
   precisely because `head_dim == 256`, and which `b200`/`b300`/`gb300` get and `rtx6000-pro` does
   not.
5. **§11 shows the FP4 capability is not what you would be paying for.** `b300` at `$7.40` and
   `gb300` at `$18.00` deliver near-identical BF16 throughput; the GB300 premium buys NVLink domain
   size, which a 2 B single-GPU model has no use for.

An NVFP4 checkpoint of a *different* model (e.g. `nvidia/DeepSeek-V4.1-Flash-NVFP4`, analysed in
[`models/deepseek41fnvfp4/architecture.md`](../deepseek41fnvfp4/architecture.md)) says nothing about
Marlin, which shares no lineage with DeepSeek. Treat any NVFP4-for-Marlin performance claim as
unfounded until a **Marlin** NVFP4 checkpoint and a measurement both exist.

---

## 11. Cost

### 11.1 There is no hosted Marlin API

| Check | Result |
|---|---|
| HF Inference Providers for `NemoStation/Marlin-2B` | **`"inferenceProviderMapping": {}`** — empty |
| HF Inference Providers for `Qwen/Qwen3.5-2B` (base) | one provider, `featherless-ai`, task `conversational` — **text only, not video** |
| Author-hosted endpoint | a Gradio demo at `https://vlm.nemostation.com/`; no documented token-priced API |
| Author monetisation | custom fine-tuning and integrations by email — a services motion, not a price list |

⚠️ **TO BE VERIFIED:** whether Fireworks, Together, Replicate, Baseten, DeepInfra or SiliconFlow host
Marlin-2B, and at what price. **There is therefore no vendor $/1M-token anchor** for METHODOLOGY §6's
sanity check; the cost model below is built from GPU-hours only.

### 11.2 Self-hosted cost

**Prices come from [`cross-cutting/cloud-pricing.md`](../../cross-cutting/cloud-pricing.md) §5.14
planning-price rows, cited by name, and from nowhere else** — not from a neighbouring GPU's row, not
re-derived from a provider page, and never described as unobtainable: every pinned GPU **is** priced
there, including reserved and spot tiers. `low` = cheapest reputable on-demand; `high` = cheapest
hyperscaler on-demand; `res1y` = cheapest published 1-year commitment.

Throughput model (METHODOLOGY §6): `sustained = 1/(1/prefill_bound + 1/decode_bound)` — serial
prefill + decode on one GPU, prefill at 167.4 TFLOP/video (§6.4), decode-bound taken at b=128 over
768 output tokens (§10.3). A real engine with continuous batching overlaps the two and lands somewhat
better.

| GPU (slug) | `low` / `high` / `res1y` | sustained videos/h | output tok/s @b=128 | **$/1k videos (low / high)** | $/1M output tok (low / high) |
|---|---|---:|---:|---:|---:|
| `b200` B200 HGX | $6.00 / $14.00 / $5.10 | 15,015 | 14,297 | **$0.40** / $0.93 | $0.117 / $0.272 |
| `h100` H100 SXM | $3.20 / $6.880 / $2.72 | 6,899 | 7,775 | **$0.46** / $1.00 | $0.114 / $0.246 |
| `b300` B300 HGX | $7.40 / $15.00 / $7.94 | 15,143 | 14,854 | **$0.49** / $0.99 | $0.138 / $0.280 |
| `h200` H200 SXM | $3.99 / $7.912 / $2.79 | 7,318 | 11,141 | **$0.55** / $1.08 | $0.099 / $0.197 |
| `rtx6000-pro` RTX PRO 6000 SE | $1.80 / $4.143 / $1.30 | 3,183 | 2,965 | **$0.57** / $1.30 | $0.169 / $0.388 |
| `a100` A100 SXM 80 GB | $1.59 / $3.431 / $1.36 | 2,394 | 4,735 | **$0.66** / $1.43 | $0.093 / $0.201 |
| `mi355x` MI355X | $8.60 / $8.60 / ⚠️ none published | 12,619 | 12,379 | **$0.68** / $0.68 | $0.193 / $0.193 |
| `gb300` GB300 NVL72 | $18.00 / $18.00 / ⚠️ none published | 16,428 | 14,854 | **$1.10** / $1.10 | $0.337 / $0.337 |

`est.` throughout — throughput roofline, prices sourced by name.

**Price caveats that must travel with this table:**

- **`gb300` and `mi355x` have exactly one published seller each (OCI).** `low` and `high` are the
  same number because there is no second price, and neither has a published 1-year commitment. Any
  cost conclusion for those two rests on a single rate.
- **`b300`'s `low` is $7.40 (Hyperstack)**; its cheapest hyperscaler rate is $15.00 (OCI). Do not mix
  a neocloud row against a hyperscaler row — hyperscalers run 2.2–2.4× neocloud rates for identical
  silicon, and `high ÷ low` here is 2.03×.
- **`gb300` is not $7.40** — that is `b300`'s neocloud rate. GB300's only published on-demand price
  anywhere is **$18.00**. **`mi355x` is not $3.45** — its only published price is **$8.60**. Both
  errors appeared in earlier drafts across this repo and are corrected here.
- Reserved tiers, where published, change the ordering materially: `h200` at `res1y` $2.79 drops to
  **$0.38/1k videos**, beating every on-demand row; `rtx6000-pro` at $1.30 drops to **$0.41**.
- ⚠️ `mi355x` is the one row whose *throughput* carries an unquantified downward correction — no
  FlashQLA on ROCm (§8.3), and vLLM's ROCm verification covers the 397B MoE text path, not this one
  (§8.5). Its $0.68 is an upper bound on what it would actually deliver.

**Three conclusions:**

1. **A 2-minute video costs ~$0.0004–0.0014 to caption.** On an `h100` at the `low` planning price,
   **$0.46 per 1,000 videos**. A fleet captioning 1 M two-minute videos/month needs ~145 GPU-hours —
   **~$465/month on one H100.** This is not a cost-constrained workload; engineering time dominates
   the bill.
2. **Bigger GPUs are cheaper per video, not more expensive.** `b200` leads on $/1k videos at both
   price tiers and is 6.3× faster than `a100`. The cheap-card intuition is wrong at every tier
   checked. The exception is $/1M *output* tokens, where `a100` and `h200` lead — if the workload is
   long `max_new_tokens=2048` captions rather than many short videos, optimise for that column
   instead.
3. **Prefill, not decode, is the sustained-throughput limiter at scale** — every row is
   prefill-bound (`h100`: 8,510 prefill-bound vs 36,447 decode-bound), the opposite of the batch-1
   picture. So **prefill FLOPs are the thing to optimise**: FP8/NVFP4 *would* help here (§10.5
   point 3 applies only to batch-1 latency), and so would cutting frames or resolution (§6.3) — at a
   quality cost the 240-frame cap is already imposing on long video.

**Cost comparison to the alternative.** The card positions Marlin as "competitive with Gemini-2.5 at
a fraction of the cost". ⚠️ Gemini's video-understanding price was not retrieved in this pass, so the
multiple is not computed. The self-hosted figure to compare against is **$0.00023 per video-minute on
an `h100` at the `low` planning price.**

---

## 12. Open questions

Consolidated ⚠️ items, ordered by how much they would change a deployment decision. Items the gated
files closed are struck through with the resolution.

### Blocking — resolve before committing to an architecture

1. **Video token budget: Path A (23,520 tokens) vs Path B (12,288 tokens)** — §6.3. The real
   `processor_config.json` **confirms the conflict** (`max_frames: 768`, `longest_edge: 25165824`)
   rather than resolving it: `qwen-vl-utils` bypasses the processor's `size` and the paths differ by
   1.91×. Serving through vLLM/SGLang likely silently halves frames or resolution.
   **Method:** run `.caption()` on a fixed clip through both paths; compare token counts and output.
2. **Does the checkpoint load on vLLM's `Qwen3_5ForConditionalGeneration` after `--hf-overrides`?**
   — §8.1. Strong evidence (pure subclass with unmodified forward, confirmed in the gated
   `modeling_marlin.py`; a third party already ships the remap; `--hf-overrides` is documented API),
   but **not verified end to end**. Sub-question: how vLLM handles the materialised `lm_head.weight`
   against `tie_word_embeddings: true`.
3. **Does serving through vLLM/SGLang preserve caption and grounding quality?** The model was trained
   with a fixed prompt, a `<think>` prefix artefact, a two-element EOS list and a specific
   preprocessing path. Every one is a place an engine can diverge. No engine-vs-transformers quality
   comparison exists.
4. **The 240-frame cap on long video** — §6.3. New in this revision and arguably top of the list for
   anyone pointing this at >2-minute content: a 10-minute video is sampled at 0.40 fps and an hour at
   0.067 fps, while prefill cost stays constant. Whether "second-precise timestamps" survive that is
   unmeasured.

### High-impact but non-blocking

5. **Any measured throughput or latency on any GPU** — §10.2. Every number in §10.3–§11.2 is
   roofline `est.`
6. **torchcodec decode throughput, 240 frames @ 448×448** — §10.3. Plausibly the real production
   bottleneck; absent from every source consulted.
7. **Quality of the quantised variants** — §9. Zero evals for GPTQ-4bit, INT8, MLX-8bit or GGUF.
   Timestamp precision under INT4 is a specific, plausible, unmeasured failure mode.
8. **FlashQLA's real effect on Marlin** — §8.3. Vendor claims 2–3× forward speedup; the benchmark
   files were unreachable, and it is unconfirmed that vLLM/SGLang dispatch to FlashQLA at all for
   this head config rather than their own Triton GDN kernels.
9. **A100 and MI355X GDN penalty** — §8.3. Neither SM80 nor ROCm is on FlashQLA's list; the prefill
   penalty on 18 of 24 layers is unquantified, and the §10.3 rows for both are optimistic.
10. **MI355X on the 2B hybrid video path** — vLLM's ROCm verification covers the 397B MoE *text*
    model. Video + GDN + ROCm is unverified in combination.
11. **`.multi_find` cost in production** — §1.4. N sequential ffmpeg `libx264` re-encodes + N full
    prefills; no measurement, and the re-encode is CPU work the GPU roofline does not see.

### Engine and config details

12. **GDN conv-state width** — `kernel` vs `kernel − 1` in vLLM (§5.2): 18.84 vs 18.63 MiB/seq.
    +1.1 %, immaterial to any sizing decision.
13. **`--mamba-ssm-cache-dtype bfloat16`** — would halve GDN state to 9.63 MiB/seq; unverified
    whether honoured for this model.
14. **NVFP4 KV cache at `head_dim 256`** — FlashInfer exposes `nvfp4_kv_cache_full_dim` (§5.4);
    head-dim-256 support unverified.
15. **SGLang video support for `qwen3_5`** — the SGLang multimodal docs page 404'd on both domains.
    The class is exported and subclasses `Qwen3VLForConditionalGeneration`, but video ingestion was
    not confirmed.
16. **TensorRT-LLM support for Qwen3.5 hybrid/GDN** — no evidence either way.
17. **Minimum vLLM and SGLang version numbers** — both recipes say "main branch required"; no
    numbered release is named. llama.cpp's minimum for `qwen35` + mmproj video is also unknown.
18. ~~**Split EOS ids**~~ — ✅ **RESOLVED.** The real `generation_config.json` lists
    `[248044, 248046]`; `config.eos_token_id` is 248046 and `config.text_config.eos_token_id` is
    248044. The hazard is real and now documented precisely (§1.3): pass **both** ids to any engine
    that reads only the config.
19. ~~**`use_cache: false` in `config.json`**~~ — ✅ **RESOLVED and confirmed** against the gated file,
    at both the top level and inside `text_config`; the base config has `true` at both. Harmless via
    `generate()` (`generation_config.json` sets `use_cache: true`), a footgun via `forward()`.
20. ~~**Marlin's own `modeling_marlin.py` (23,098 B) vs the mirror's (17,261 B)**~~ — ✅ **RESOLVED.**
    The 5,837-byte delta is `multi_find` + `MultiFindResult` + `_video_duration` + `_clip_segment`
    (ffmpeg sub-clipping), their docstrings, and two imports. Nothing else differs: the forward path,
    prompts, parsers and env defaults are identical. §1.4 documents the new method and its cost.
21. ~~**Mirror fidelity**~~ — ✅ **RESOLVED.** The gated `config.json` (2,819 B) is byte-identical to
    the mirror's, and the gated index's `total_parameters` matches the mirror-derived 2,213,241,664
    exactly. Every architectural claim written from the mirror stands.

### Commercial

22. **Any hosted API for Marlin-2B and its price** — §11.1. None found.
23. **Gemini video-understanding pricing**, to test the card's "fraction of the cost" claim — §11.2.
24. **Training-data / benchmark contamination** — §10.1. ActivityNet, Charades and TimeLens appear in
    both the training-source list and the evaluation list; the card does not address it.
25. **`gb300` and `mi355x` rest on a single published price each** (OCI), with no published reserved
    tier — §11.2. Any cost conclusion for those two is one price list away from changing.

---

## Sources

**Vendored in this directory** (fetched from the gated repo 2026-09-19 with an authorised read token):
[`config.json`](config.json) · [`base-config.qwen3.5-2b.json`](base-config.qwen3.5-2b.json) ·
[`generation_config.json`](generation_config.json) ·
[`preprocessor_config.json`](preprocessor_config.json) ·
[`processor_config.json`](processor_config.json) · [`MODEL_CARD.md`](MODEL_CARD.md) ·
[`FILES.md`](FILES.md)

**Fetched from the gated repo, not vendored** (size, not secrecy): `model.safetensors.index.json`
(55,830 B — `total_parameters`, `total_size`, 618-name weight map) · `modeling_marlin.py` (23,098 B)
· `chat_template.jinja` (7,755 B)

**Repo cross-references**
- [`research/METHODOLOGY.md`](../../METHODOLOGY.md) §1–§8 (all formulas, the pinned GPU set)
- [`research/cross-cutting/cloud-pricing.md`](../../cross-cutting/cloud-pricing.md) §5.14 (all prices)
- [`research/gpus/`](../../gpus/): [`a100.md`](../../gpus/a100.md), [`h100.md`](../../gpus/h100.md),
  [`h200.md`](../../gpus/h200.md), [`b200.md`](../../gpus/b200.md), [`b300.md`](../../gpus/b300.md),
  [`gb300.md`](../../gpus/gb300.md), [`rtx6000-pro.md`](../../gpus/rtx6000-pro.md),
  [`mi355x.md`](../../gpus/mi355x.md)
- [`research/cross-cutting/inferencex-api.md`](../../cross-cutting/inferencex-api.md)

**Model, base and variants**
- https://huggingface.co/NemoStation/Marlin-2B · /api/models/… · /api/models/…/tree/main · …?expand[]=inferenceProviderMapping
- https://huggingface.co/Qwen/Qwen3.5-2B · /api/models/… · /raw/main/README.md · /raw/main/config.json
- https://huggingface.co/api/models/NemoStation/Marlin-2B-MLX-8bit
- https://huggingface.co/api/models/prasannaJagadesh/marlin-2B-GPTQ-4BITS
- https://huggingface.co/api/models/tintwotin/Marlin-2B-SDNQ-int8
- https://huggingface.co/api/models/jadeonrails/marlin-2b-gguf · /raw/main/README.md
- https://huggingface.co/api/models/lunahr/Marlin-2B-ungated
- https://huggingface.co/api/spaces/HappyPablo/marlin-2b-video-understanding · /raw/main/app.py

**Engines and kernels**
- https://docs.vllm.ai/en/latest/models/supported_models.html *(`--hf-overrides`, Qwen3_5 T+I+V, Transformers-backend caveats)*
- https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3.5.html
- https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/registry.py
- https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/qwen3_5.py
- https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/qwen3_vl.py
- https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/model.py *(`hf_overrides`)*
- https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/attention/backends/fa_utils.py
- https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/attention/backends/flash_attn.py
- https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/attention/backends/flashinfer.py
- https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/models/qwen3_5.py
- https://raw.githubusercontent.com/huggingface/transformers/main/src/transformers/models/qwen3_5/configuration_qwen3_5.py
- https://github.com/QwenLM/FlashQLA · https://github.com/Dao-AILab/flash-attention · https://github.com/fla-org/flash-linear-attention

**Hardware**
- https://developer.nvidia.com/cuda-gpus *(RTX PRO 6000 Workstation + Server Edition both cc 12.0)*
- https://www.nvidia.com/en-us/data-center/h100/ · /h200/ · /hgx/ · /dgx-gb300/
- https://lenovopress.lenovo.com/lp2263.pdf *(RTX PRO 6000 SE 1,597 GB/s)*
- https://rocm.docs.amd.com/en/latest/reference/gpu-arch/mi350.html

**Papers cited by the card** *(cited by the card; not independently fetched)*: arXiv
[2501.00513](https://arxiv.org/abs/2501.00513), [2407.00634](https://arxiv.org/abs/2407.00634),
[2512.14698](https://arxiv.org/abs/2512.14698).

---

## Verification log

### A. What the gated files changed versus the earlier inferred version

| # | Earlier claim (inferred, from the mirror + card) | Gated-file verdict |
|---|---|---|
| 1 | Architecture reconstructed from `lunahr/Marlin-2B-ungated` | **CONFIRMED exactly.** The gated `config.json` is 2,819 B and byte-identical to the mirror's. Every field in §2 reproduces. The whole "mirror verification" apparatus (old §1.3) is now redundant and is deleted rather than carried forward; the mirror is demoted to a row in §9. |
| 2 | Parameters 2,213,241,664 unique / 2,721,801,024 stored, derived from safetensors headers over HTTP range requests | **CONFIRMED by a better source.** `model.safetensors.index.json` states `total_parameters: 2213241664` and `total_size: 5443602048` outright. The config-derived breakdown (§3.3) lands on both exactly, and `5,443,677,224 − 5,443,602,048 = 75,176 B` of shard headers reconciles the on-disk figure. |
| 3 | Text tower 1,881,825,088 / vision tower 331,416,576 / MTP head absent | **CONFIRMED.** The 618-name weight map contains zero `mtp` tensors; the base−Marlin delta of 60,828,160 reconstructs as exactly one MTP module. ⚠️ removed from §2.3 and §7.1. |
| 4 | `tie_word_embeddings: true` yet `lm_head.weight` materialised | **CONFIRMED** — `lm_head.weight` is the first entry in the weight map. |
| 5 | EOS split across `config` (248046 top / 248044 `text_config`) and `generation_config` (`[248044, 248046]`) | **CONFIRMED** against the real files. Reclassified from ⚠️ to a documented deployment hazard (§1.3, §8.1, §12 #18). |
| 6 | `use_cache: false` at both levels, overridden by `generation_config` | **CONFIRMED**; the base config has `true` at both, so this is a Marlin-specific edit. ⚠️ removed, kept as a footgun note. |
| 7 | Video Path A vs Path B conflict (23,520 vs 12,288 tokens) | **CONFIRMED as a real, unresolved conflict.** The gated `processor_config.json` really carries `max_frames: 768`, `fps: 2`, `size.longest_edge: 25165824`, and `preprocessor_config.json` carries `longest_edge: 16777216`. The config settles the *numbers*, not the *question*, so the ⚠️ stays (§6.3, §12 #1). |
| 8 | "392 ViT tokens per frame" (itself a prior correction from 196) | **CONFIRMED at 392.** `temporal_patch_size: 2` means 784 patches cover a 2-frame group. §6.3 now derives it from first principles and states the per-second figures explicitly: **196 LLM tokens/s, 784 ViT patches/s at 2 fps**, plus prefill tokens for 30 s / 2 min / 10 min. |
| 9 | ⚠️ The 240-frame cap was tabulated but its consequence was not drawn out | **NEW FINDING.** Prefill is **bounded at ~23.5 K tokens for any video ≥ 2 min**; a 10-minute clip is sampled at 0.40 fps and an hour at 0.067 fps for the same cost. Good for capacity planning, a quality cliff for long video. Now §6.3's headline and §12 #4. |
| 10 | `modeling_marlin.py` delta (23,098 vs 17,261 B) "unexamined — could contain something material" | **RESOLVED.** It is `multi_find` + `MultiFindResult` + `_video_duration` + `_clip_segment`. Material for **cost** (N ffmpeg re-encodes + N prefills, §1.4) but not for architecture. ⚠️ removed, §12 #20 closed. |
| 11 | `.caption()` default `max_new_tokens` | **CONFIRMED at 2048** in the gated file and the card. §6.5 is new: decode cost at 768 **and** 2048 output tokens on every pinned GPU, showing TPOT is flat (+0.8 %) across the generation and a full-length caption is 1.7–8.7 s at batch 1. |
| 12 | ViT totals 0.547 / 16.40 / 65.61 TFLOP, with a note that they recompute ~0.45 % low but were kept | **CORRECTED.** Regenerated to **0.544 / 16.33 / 65.32**, per-frame stated as **0.272 TFLOP/frame**, and every downstream figure (prefill 167.4 TFLOP, §10.3, §11.2) rebuilt on them. |
| 13 | Three cells of the decode-bytes table (16.36 / 28.99 / 81.4 GB) did not reproduce from the document's own formula | **CORRECTED.** §6.2 regenerated to 16.65 / 29.53 / 77.80 GB, with a 30-second-video column added. |
| 14 | INT8 weight memory 3.075 GB | **CORRECTED to 3.097 GB** (1.03125 B/param with the 128-block scale, consistent with every other row). Reconciles with SDNQ's published 3.117 GB to +0.020 GB of tokenizer/config, matching the other rows' residuals. |
| 15 | "vLLM-compatible needs rewriting one string in `config.json`" | **SHARPENED.** No file edit is needed: `--hf-overrides '{"architectures": ["Qwen3_5ForConditionalGeneration"]}'` is documented vLLM API, and `hf_overrides` is a first-class `ModelConfig` field. Registry re-fetched 2026-09-19: still no `Marlin*` model entry (the `marlin` hits are the INT4 GEMM kernel). vLLM's supported-models table lists `Qwen3_5ForConditionalGeneration` with **video** (T + I<sup>E+</sup> + V<sup>E+</sup>). §8.1 rewritten around the flag, with what you lose and what you must re-supply. |

### B. Repo-wide corrections applied (METHODOLOGY §8 / cloud-pricing.md)

| # | What was wrong | Correction applied here |
|---|---|---|
| 16 | **B300 capacity 262.5 GB** (old §8.6) | **268 GB as deployed** per METHODOLOGY §8 (2,144 GB per 8-GPU node; DGX B300 262.5). **288 GB is GB300 NVL72** (≈ 279 usable), and separately MI355X. Concurrency re-derived: B300 now **753** concurrent 2-min videos (was 734). |
| 17 | **B200 bandwidth implied ~8.4 TB/s** | **7.7 TB/s** planning figure per METHODOLOGY §8 and `gpus/b200.md`. Every B200 decode row dropped ~4–9 %: b=128 at video ctx is now **14,509** tok/s (was 15,075, which was actually B300's number). **B200 and B300 are no longer identical rows.** |
| 18 | **RTX PRO 6000 at 1,792 GB/s** (Workstation Edition) | **1,597 GB/s Server Edition** — clouds rent SE. Every RTX PRO 6000 row is ~11 % slower: b=128 at video ctx **3,009** tok/s (was 3,658); batch-1 end-to-end **4.12 s** (was 3.51 s). BF16 set to **480 TFLOPS dense** per `gpus/rtx6000-pro.md` §3c (NVIDIA's "1 PFLOPS" is the sparse figure). |
| 19 | **B300 BF16 treated as 1.5× B200** in earlier drafts | Re-affirmed: **2,250 TFLOPS dense, identical to B200.** The 1.5× is FP4-only (13,500 vs 9,000 dense). GB300 is 2,500 BF16 dense (higher-clocked part), which is why its rows differ slightly from B300's. |
| 20 | **Prices quoted verbatim from one provider (Lambda), mixed with OCI rows in the same table** | **Replaced entirely.** §11.2 cites `cloud-pricing.md` §5.14 planning-price rows **by name** (`low` / `high` / `res1y`) and nothing else. No provider page is quoted directly; no two providers are compared in one row. |
| 21 | **"GB300 is $7.40"** | **Wrong — that is `b300`'s neocloud `low`.** GB300's only published on-demand price anywhere is **$18.00 (OCI)**, with no published reserved tier. Stated explicitly in §11.2. |
| 22 | **"MI355X is $3.45"** | **Wrong.** MI355X's only published price is **$8.60 (OCI)**; `low` = `high`; no reserved tier. Stated explicitly in §11.2. |
| 23 | **"Prices … were not retrievable"** phrasing | **Removed from the document.** Every pinned GPU is priced in `cloud-pricing.md`, including reserved and spot tiers. The phrase appears nowhere in this revision. |
| 24 | **Unpinned GPUs (A10G, L40S, L4) carried in every table** at unsourced specs and neighbouring-GPU prices | **Removed.** They are not in METHODOLOGY §8 and not priced in `cloud-pricing.md`; §10.4 says so rather than carrying invented rows. This also removes the previous revision's A10-vs-A6000 price mix-up at the root rather than re-pricing it. |
| 25 | **Sparse/dense TFLOPS hygiene** | All compute in this revision is **dense**: H100/H200 989.5 (not 1,979), MI355X 2,500 (not 5,000), RTX PRO 6000 SE 480 (not "1 PFLOPS"), B200/B300 2,250, GB300 2,500. |
| 26 | **GiB/GB convention** | All memory arithmetic is done in bytes; §4 and §5.3 report **both** GB (10⁹) and GiB (2³⁰) with labelled columns. HBM capacities are **as deployed** per METHODOLOGY §8, never physical stack capacity. |
| 27 | **METHODOLOGY §3 consistency rule not applied to the decode tables** | Applied: b=256 at 23,520 ctx exceeds `max_concurrency` on the 80 GB cards (205 < 256) and is now printed as `infeasible (KV)` instead of a number. |

### C. Still unverifiable after this pass (all carry ⚠️ inline)

**Requires running the model:** Path A vs Path B quality (§6.3), end-to-end vLLM load after
`--hf-overrides` (§8.1), engine-vs-transformers quality (§12 #3), the 240-frame cap's effect on
long-video timestamp precision (§12 #4), `.multi_find` production cost (§12 #11).

**Requires a measurement nobody has published:** any Marlin-2B throughput or latency on any hardware
(§10.2), torchcodec decode throughput (§12 #6), quantised-variant evals (§9), FlashQLA's raw
benchmark files and whether engines dispatch to it (§8.3), the A100/ROCm GDN penalty magnitude
(§12 #9), MI355X on the 2B hybrid video path (§12 #10).

**Commercial:** hosted-API availability and price (§11.1), Gemini video pricing (§11.2), and the fact
that `gb300` and `mi355x` each rest on one published price (§12 #25).

### D. Notes for the orchestrator

- **This directory now vendors its config files**, like every sibling model doc. The one remaining
  gap is `model.safetensors.index.json` (55,830 B) — vendoring it would make the §3 parameter
  derivation fully offline-reproducible; it is currently re-fetched with the gated token.
- **The model-side inference held up completely.** Nothing architectural changed when the gate
  opened. The previous revision's error profile was hardware-spec generalisation and price sourcing,
  not fabrication — a useful calibration for how much to trust a mirror-based reconstruction.
- **Cross-document sweep still worth running.** The corrections in §B (16–27) were repo-wide
  patterns, not Marlin-specific. Other model docs written under the same conditions likely carry
  B300 = 288 GB or 262.5 GB, B200 ≈ 8.4 TB/s, RTX PRO 6000 = 1,792 GB/s, sparse-TFLOPS rows,
  unpinned GPUs at unsourced specs, and "not retrievable" price claims.
