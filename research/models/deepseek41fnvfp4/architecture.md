# nvidia/DeepSeek-V4.1-Flash-NVFP4 — architecture and inference profile

> Research date **2026-09-19**. Formulas, legend and markers follow
> [`research/METHODOLOGY.md`](../../METHODOLOGY.md).
> Base-checkpoint architecture is analysed in
> [`research/models/deepseek41f/architecture.md`](../deepseek41f/architecture.md);
> **this document covers only what the NVFP4 requantisation changes**, plus the
> weight-byte and KV-byte arithmetic re-done for *this* checkpoint.
>
> Every number below is either (a) measured by me from the checkpoint's own
> safetensors headers / config, with the arithmetic shown, (b) `[src]`-linked to
> a primary document, or (c) marked **⚠️ TO BE VERIFIED**.

## 0. Bottom line up front

State this plainly, because every sizing decision in this repo that reaches for
"the NVFP4 build" inherits it:

1. **This checkpoint is 527.27 GB — *larger* than the 510.29 GB base
   `deepseek-ai/DeepSeek-V4.1-Flash`**, by 16,987,299,840 B (+3.33 %). NVFP4 is a
   *finer* scale layout than the base's MXFP4, not a smaller one: one extra scale
   byte per 32 weights (§3.4).
2. **Only 58.0 % of its bytes are NVFP4** (the routed backbone experts).
   38.5 % is an FP8/MXFP8 Engram table no FP4 kernel touches, and attention,
   shared experts, MTP/DSpark, routers, vision and norms all keep their source
   precision (§3.3).
3. **No speedup over the base is published anywhere.** NVIDIA's card contains no
   tok/s, TTFT or TPOT figure of any kind — re-fetched 2026-09-19, zero
   occurrences of "speedup", "throughput", "tokens per second" or "performance"
   [src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md).
   What it publishes is an accuracy wash (§10.1) and a lossless-conversion audit.
4. Therefore the case for this build over the base is **accuracy-neutrality at
   group-16 on Blackwell**, not memory and not speed. On MI355X it is strictly
   worse than the base (§8.4). On Hopper/Ampere neither runs natively.

---

## 1. Identity

| Field | Value | Source |
|---|---|---|
| HF repo | `nvidia/DeepSeek-V4.1-Flash-NVFP4` | [src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4) |
| Base model | `deepseek-ai/DeepSeek-V4.1-Flash` (MIT) | [src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md) |
| License | MIT | [src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md) |
| Repo created | **2026-09-16 20:37:42 UTC**; last modified 2026-09-17 01:24:11 UTC | [src](https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4) |
| Card "Release Date" | Hugging Face **09/16/2026** | [src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md) |
| Commit sha | `3431dde3247c13b5957f682b1e3c6fcae2566079` | [src](https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4) |
| Gated? | **No** (`"gated": false`, `"private": false`) | [src](https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4) |
| Downloads / likes (2026-09-19) | **1,969** / 65 (HF's `downloads` is a rolling 30-day counter and moves within a day; an earlier pull the same day read 550) | [src](https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4) |
| `library_name` | `Model Optimizer` (not `transformers` — the base repo is `transformers`) | [src](https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4) |
| `pipeline_tag` | `image-text-to-text` (multimodal) | [src](https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4) |
| Architecture class | `DeepseekV41ForCausalLM`, `model_type: deepseek_v41` | [src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/blob/main/config.json) |
| Quantiser | `nvidia-modelopt` **v0.47.0rc0** + "V4.1-specific compatibility changes", source revision `dba1be0a40aa45a94ad051997016db3960a90277` — verified to be the **base repo's own commit sha**, not a ModelOpt revision ([`deepseek-ai/DeepSeek-V4.1-Flash` `sha`](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash) is the same string) | [src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md) |
| Calibration | cnn_dailymail + Nemotron-Post-Training-Dataset-v2, **512 samples each (1,024 total)**, seqlen 512, batch 4, seed 0; Nemotron subsets `stem`,`chat`,`math`,`code` | [src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md) |

### 1.1 Files and on-disk size (measured)

84 files, **48 safetensors shards**. Totalled from the recursive HF tree API
[src](https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4/tree/main):

```
repo total (all files)   527,315,885,158 B = 527.32 GB = 491.10 GiB
48 shard FILES on disk   527,293,384,576 B = 527.29 GB = 491.08 GiB
tensor PAYLOAD in those  527,273,322,840 B = 527.27 GB = 491.06 GiB   <- the card's "approximately 492 GiB"
HF usedStorage           527,309,220,165 B
```

The payload figure is the one every byte table below uses: it is the sum of the
`data_offsets` spans, i.e. it excludes the ~20.1 MB of safetensors JSON headers
that the 48 shard *files* also carry. Re-verified 2026-09-19 against
[the recursive tree API](https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4/tree/main?recursive=true)
(84 files, 48 shards, all-file total and `usedStorage` both exact).

Shard shape is unusual and matters for loading:

| Shards | Size each | Contents |
|---|---|---|
| 1–2 | 0.97 / 1.32 GB | vision encoder, aligner, embeddings |
| 3–42 | ~7.81 GB | the 40 backbone layers (NVFP4 experts + FP8 attention) |
| 43–46 | 1.3–2.7 GB | MTP / DSpark stack |
| **47–48** | **101.54 / 101.54 GB each** | **the two Engram lookup tables** |

> Two 101 GB shards is a real operational hazard: a naive single-threaded
> `safetensors` load stalls on them. This is exactly why the card's vLLM recipe
> passes `--model-loader-extra-config '{"enable_multithread_load": true, "num_threads": 128}'`
> [src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md).

**Gotcha:** `model.safetensors.index.json` still carries the *base* checkpoint's
`metadata.total_size = 510,286,023,000` (475.24 GiB)
[src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/resolve/main/model.safetensors.index.json),
i.e. it was **not updated** after requantisation. Anything that sizes an
allocation from that field will under-allocate by ~17 GB. The true figure is
527.27 GB, measured from the 48 shard headers.

### 1.2 Checkpoint dtype / quantisation scheme

Mixed precision — this is **not** an all-NVFP4 checkpoint:

```json
"quantization_config": {
  "quant_algo": "MIXED_PRECISION", "quant_method": "fp8",
  "moe_quant_algo": "NVFP4", "expert_dtype": "fp4", "group_size": 16,
  "scale_fmt": "ue8m0", "weight_block_size": [32, 32],
  "kv_cache_quant_algo": null,
  "config_groups": {"group_0": {"targets": ["Linear"],
     "weights":            {"num_bits": 4, "type": "float", "group_size": 16, "dynamic": false},
     "input_activations":  {"num_bits": 4, "type": "float", "group_size": 16, "dynamic": false}}},
  "ignore": ["*.attn.*", "*.ffn.shared_experts.*", "head", "mtp.*"],
  "activation_scheme": "dynamic",
  "producer": {"name": "modelopt", "version": "dsv4-nvfp4-experts"}
}
```
[src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/blob/main/config.json),
[src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/hf_quant_config.json)

Both files enumerate `layers.0..39.ffn.experts` explicitly — all 40
backbone MoE layers, `quant_algo: NVFP4`, `group_size: 16` (40 entries in
`hf_quant_config.json`'s `quantized_layers`, verified 2026-09-19). The
exclusion list is spelled `ignore` in `config.json` and `exclude_modules` in
`hf_quant_config.json`; the four patterns are identical in both.

The model card settles what the recipe is: *"converts the ordinary routed MoE
experts from source MXFP4 to NVFP4 **weights and activations (W4A4)**, with group
size 16. The converted projections are `w1`, `w2`, and `w3` for 384 experts
across 40 layers"*
[src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md).
So W4**A4**, not W4A16.

> **Contradiction inside the checkpoint's own config.** `config_groups.group_0.
> input_activations.dynamic = false` says the activation scales are
> static/calibrated, but the sibling top-level key `activation_scheme` is
> `"dynamic"`. Both are in the shipped `config.json`. Which one an engine honours
> — and therefore whether activation scales are recomputed per batch — is
> **⚠️ TO BE VERIFIED**. The card's "Activation scales were calibrated; 18 of
> 46,080 projection entries used fallback scales" favours the static reading.

### 1.3 Tokenizer / chat template

| Field | Value |
|---|---|
| Vocab | 129,280 (`vocab_size`), tokenizer.json 6.37 MB | [src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/blob/main/config.json) |
| BOS / EOS / PAD | `<｜begin▁of▁sentence｜>` (0) / `<｜end▁of▁sentence｜>` (1) / id 2 | [src](https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4) |
| Image token | id **129264** | [src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/blob/main/config.json) |
| DSpark noise token | id **128799** | [src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/blob/main/inference/config.json) |
| Chat template | **No Jinja template ships.** DeepSeek supplies a Python reference encoder (`encoding/encoding.py`, 37 KB, with 5 test-case pairs) and a Rust `deepseek-recipe` toolkit | [src](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/README.md) |
| Template features | multi-turn, tool calling, thinking mode, **numeric `reasoning_effort` 1–100**, mid-conversation system messages, interleaved images | [src](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/README.md) |

The NVFP4 repo carries the full `encoding/` and `inference/` trees verbatim from
the base repo, so prompt encoding is identical. Engines need
`--reasoning-parser deepseek-v41` / `--tool-call-parser deepseekv41` (SGLang) or
`--tokenizer-mode deepseek_v41 --reasoning-parser deepseek_v41` (vLLM)
[src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md).

---

## 2. Architecture from config.json

Unchanged from the base checkpoint — quantisation touches no shape. Cross-ref
[`deepseek41f/architecture.md`](../deepseek41f/architecture.md) for the full
treatment; the fields that drive *this* document's arithmetic:

| Field | Value | What it means here |
|---|---|---|
| `num_hidden_layers` | 40 | 20-layer **causal encoder** + 20-layer **decoder** (CED) |
| `hidden_size` | 5120 | |
| `vocab_size` | 129,280 | `tie_word_embeddings: false` → separate embed + head |
| `max_position_embeddings` | **1,048,576** | YaRN, `factor 16`, `original_max_position_embeddings 65536`, `beta_fast 32`, `beta_slow 1` |
| `num_attention_heads` | 64, `head_dim` **512** | wq_b is 64×512 = 32,768 wide |
| `q_lora_rank` / `o_lora_rank` / `o_groups` | 1280 / 1024 / 8 | low-rank Q **and** block-diagonal grouped output projection |
| `num_key_value_heads` | 1 | a single shared latent KV stream (MLA-like) |
| `qk_rope_head_dim` | 64 | RoPE tail inside the 512-dim head |
| `sliding_window` | **128** | every layer runs a 128-slot SWA ring |
| `compress_ratios` | `[0,0, 2×18, 1×20, 0,0,0]` | layers 0–1 pure SWA; **2–19 pool 2 tokens → 1 KV latent**; 20–39 ratio 1; MTP 40–42 pure SWA |
| `kv_source_layers` | **[2, 8, 14, 20]** | only these 4 layers own a compressed-KV cache; 36 others *read* it — cross-layer KV sharing |
| `index_source_layers` | [2, 8, 14, 20, 24, 28, 32, 36] | 8 layers run the sparse indexer; the rest reuse published Top-K |
| `index_n_heads` / `index_head_dim` / `index_topk` | 32 / 128 / **512** | CSA2 lightning indexer keeps 512 compressed positions per query |
| `candidate_source_layer` / `candidate_topk_blocks` / `candidate_block_size` | 20 / 2048 / 8 | **Hierarchical Sparse Indexer** — layer 20 builds a 2048×8 = 16,384-position candidate pool that layers 24/28/32/36 score instead of the whole context |
| `n_routed_experts` / `n_shared_experts` / `num_experts_per_tok` | 384 / 1 / **6** | |
| `moe_intermediate_size` | 2304 | per-expert w1/w3 [2304,5120], w2 [5120,2304] |
| `scoring_func` / `topk_method` / `routed_scaling_factor` | `sqrtsoftplus` / `noaux_tc` / 1.5 | plus a **separate router bias for image-span tokens** (`ffn.gate.bias_vl`) |
| `hc_mult` / `hc_sinkhorn_iters` | 4 / 20 | Single-Pass mHC: residual stream is 4 parallel copies mixed by a Sinkhorn-normalised 24×20480 matrix per sublayer |
| `engram_layer_ids` / `engram_num_embeddings` / `engram_n_heads` / `engram_head_dim` / `engram_max_ngram_size` | [1, 14] / [384006168, 384016682] / 8 / 256 / 4 | conditional n-gram memory; **24 rows of 256 fetched per token per engram layer** |
| `num_nextn_predict_layers` | 3 | MTP |
| `dspark_*` | block 5, target layers [37,38,39], markov_rank 256, 128 experts top-3 | semi-autoregressive drafting |
| `rms_norm_eps` | **1e-20** | unusually tight; fp32 norm accumulation is mandatory |
| `swiglu_limit` | 10.0 | clamped SwiGLU |
| vision | 32 layers, d=1024, 16 heads, inter 2816, patch 14, 3×3 pixel-unshuffle, max 1024 image tokens, min_pixels 295,936 | DeepSeek-ViT + 2-layer MLP aligner |

**Three attention modes per layer (CSA2)** — from the reference implementation
[`inference/model.py`](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/inference/model.py):

* **Full** (layers 2, 8, 14, 20 — `kv_source_layers`): compress own KV, publish
  the cache and the indexer K.
* **Reindex** (24, 28, 32, 36): read someone else's KV cache, run own indexer
  over the *candidate pool* published by layer 20.
* **Reuse** (everything else): read both the shared KV cache **and** the Top-K
  indices another layer already computed. Zero extra cache, zero indexer cost.

Every layer additionally attends to its own 128-token SWA ring, concatenated
into a single `sparse_attn` call with the compressed positions.

---

## 3. Parameter count

All counts below are **measured from the 48 shard headers** (HTTP range reads of
the safetensors JSON header of every shard, `data_offsets` differenced; packed
`U8`/`I8` FP4 tensors counted as 2 logical values per byte) and then
independently **re-derived from `config.json`**. The two agree.

### 3.1 Derivation from config.json

```python
D, V, MOE, NE, TOPK   = 5120, 129280, 2304, 384, 6
HD, NH, QL, OL, OG    = 512, 64, 1280, 1024, 8

expert = 3*MOE*D                                            # 3 * 2304 * 5120 = 35,389,440
attn   = QL*D + NH*HD*QL + HD*D + OG*OL*(NH*HD//OG) + D*OG*OL
#      = 6,553,600 + 41,943,040 + 2,621,440 + 33,554,432 + 41,943,040 = 126,615,552
gate   = NE*D                                               # 1,966,080
hc     = 2*24*(4*D)                                         # 983,040   (hc_attn_fn + hc_ffn_fn)

per_layer_total  = attn + expert*(384+1) + gate + hc        # 13,754,499,072
per_layer_active = attn + expert*(  6+1) + gate + hc        #    377,290,752
```

| Quantity | Arithmetic | Result |
|---|---|---|
| 40 backbone layers | 40 × 13,754,499,072 | **550,179,962,880** |
| token embedding | 129,280 × 5,120 | 661,913,600 |
| LM head | 129,280 × 5,120 | 661,913,600 |
| Engram `wkv` projections | 2 × (24·256) × (5120·5) | 314,572,800 |
| **Backbone total** | sum | **551,818,362,880 = 551.8 B** — card claims **"552B backbone"** ✅ |
| Engram lookup tables | (384,006,168 + 384,016,682) × 256 | **196,613,849,600 = 196.6 B** — card claims **"196B Engram"** ✅ |
| MTP/DSpark (3 layers × 128 experts + heads) | 3 × 4,693,491,712 + 144,839,936 | 14,225,315,072 = 14.2 B |
| Vision (32 blocks d=1024) + aligner | 32 × 12,845,056 + 602,112 + 73,400,320 | 485,044,224 = 0.485 B |
| norms / sinks / compressor / indexer / misc | — | ≈ 62.7 M |
| **Grand total** | | **763,205,315,794** |

That grand total matches the HF API's `safetensors.total` field
**exactly** (763,205,315,794) [src](https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4)
— i.e. HF counts parameters and excludes scale tensors, same as I do.

### 3.2 Active parameters — and why prefill ≠ decode

```
encoder only (layers 0-19):  20 × 377,290,752 =  7,545,815,040  =  7.55 B
all 40 layers             :  40 × 377,290,752 = 15,091,630,080  = 15.09 B
      + LM head (661,913,600)                 = 15,753,543,680  = 15.75 B
```

Card: **"8B activated during prefill and 16B during decode"**
[src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md). ✅

The mechanism is CED: *"the decoder's global KV cache is projected from the final
encoder hidden states rather than derived from each decoder layer's own hidden
states"* [src](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/README.md).
During prefill you need only the 20 encoder layers over the prompt; the 20
decoder layers run for the tokens you actually emit. **Prefill costs half a
normal 40-layer model's FLOPs per token.** For agentic workloads with
300K-token prompts and 2K-token answers this is the single biggest
cost lever in the architecture.

### 3.3 What is NVFP4 and what is not — component inventory (measured)

| Component | Stored as | Parameters | Bytes | GiB | of which scale bytes |
|---|---|---|---|---|---|
| **Backbone routed experts** (40 × 384 × {w1,w2,w3}) | **NVFP4 E2M1, gs16, E4M3 scales + FP32 per-tensor** | 543,581,798,400 | 305,765,130,240 | **284.77** | 33,974,231,040 |
| Engram lookup tables (2) | FP8 E4M3 + E8M0 per 32 | 196,613,849,600 | 202,758,032,400 | **188.83** | 6,144,182,800 |
| **MTP/DSpark routed experts** (3 × 128) | **MXFP4 kept — not converted** (`I8` packed + E8M0/32) | 13,589,544,960 | 7,219,445,760 | 6.72 | 424,673,280 |
| Backbone attention (wq_a/wq_b/wkv/wo_a/wo_b) | FP8 E4M3, 32×32 block scales | 5,064,901,120 | 5,070,131,200 | 4.72 | 4,945,920 |
| Backbone shared experts (40) | FP8 E4M3 | 1,415,577,600 | 1,416,960,000 | 1.32 | 1,382,400 |
| Token embedding | BF16 | 661,913,600 | 1,323,827,200 | 1.23 | 0 |
| LM head (`head.weight`) | BF16 | 661,913,600 | 1,323,827,200 | 1.23 | 0 |
| Vision encoder + aligner | BF16 | 485,268,480 | 970,536,960 | 0.90 | 0 |
| MTP attn / shared / markov / confidence heads | FP8 + BF16 | 635,817,570 | 713,428,872 | 0.66 | 551,424 |
| Engram `wkv` projections | FP8 | 314,654,720 | 315,043,840 | 0.29 | 307,200 |
| MoE routers (`gate.weight` + fp32 `bias`, `bias_vl`) | BF16 + FP32 | 78,673,920 | 157,409,280 | 0.15 | 0 |
| mHC (`hc_attn_fn`, `hc_ffn_fn`, bases, scales) | **FP32** | 39,323,760 | 157,295,040 | 0.15 | 0 |
| CSA2 indexer (`wq_b` FP8, `weights_proj`/`wk`/`k_norm` BF16) | mixed | 43,516,416 | 45,130,752 | 0.04 | 40,960 |
| KV compressor (`wkv` BF16, `wgate` **FP32**) | mixed | 18,352,128 | 36,704,256 | 0.03 | 0 |
| Norms, attention sinks, image tokens | BF16 / FP32 | 209,920 | 419,840 | 0.00 | 0 |
| **TOTAL** | | **763,205,315,794** | **527,273,322,840** | **491.06** | **40,550,315,024** |

**Non-quantised / non-NVFP4 tensors, explicitly** (this is what
`quantization_config.ignore = ["*.attn.*", "*.ffn.shared_experts.*", "head", "mtp.*"]`
buys you):

1. `*.attn.*` — all attention projections, the compressor, the indexer. Stay
   **FP8 E4M3 with 32×32 UE8M0 block scales** (the base checkpoint's `dtype: "fp8"`).
2. `*.ffn.shared_experts.*` — the always-on shared expert per layer, FP8.
3. `head` — LM head, **BF16** (and `embed.weight` was never a Linear, also BF16).
4. `mtp.*` — the entire MTP/DSpark stack including its 3 × 128 routed experts,
   which therefore **remain MXFP4** (`I8` packed with `F8_E8M0` scales at group 32).
5. Not matched by any pattern but still not NVFP4: **Engram tables (FP8)**,
   routers (BF16), mHC (FP32), vision tower (BF16), all norms.

So: **71.2 % of the model's parameters are NVFP4; 58.0 % of its bytes are NVFP4
expert weights; 38.5 % of its bytes are an FP8 Engram table that no FP4 kernel
ever touches.** (The Engram share was previously stated as 36 %; recomputed,
202,758,032,400 / 527,273,322,840 = **38.45 %**, which is what §4.1 and §10.3
already used.) The card's own wording for the non-converted set is *"Attention,
shared experts, vision, Engram lookup tables, MTP/DSpark, and other excluded
components retain their source precision, **including MXFP8 where applicable**"*
[src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md)
— i.e. the "FP8 + E8M0 per 32" rows in the table above are MXFP8 in NVIDIA's
vocabulary.

Dtype totals cross-check exactly against the HF API's `safetensors.parameters`
[src](https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4):
`U8` 543,581,798,400 (= the NVFP4 backbone experts), `I8` 13,589,544,960 (= the
MTP/DSpark experts still MXFP4-packed), `F8_E4M3` 204,015,223,296, `BF16`
1,976,441,856, `F32` 42,307,282.

### 3.4 Why the checkpoint got *bigger*

Card: *"The finer NVFP4 scale layout increases checkpoint size from approximately
476 GiB to 492 GiB"* and *"All 16,986,931,200 weight blocks passed lossless
conversion"* [src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md).
Both numbers reproduce exactly:

```
routed-expert FP4 values          = 543,581,798,400
MXFP4 blocks (group 32)           = 543,581,798,400 / 32 = 16,986,931,200   <- the card's block count ✅
MXFP4 scale bytes (E8M0, 1 B ea.) =                        16,986,931,200
NVFP4 scale bytes (E4M3, 1 B/16 ) = 543,581,798,400 / 16 = 33,973,862,400
delta                             =                        16,986,931,200 B = 16.99 GB
+ new FP32 per-tensor scales      = 46,080 expert projections (40 layers × 384 experts × {w1,w2,w3})
                                    × 2 (weight_scale_2, input_scale) × 4 B
                                  =                             368,640 B
measured delta (527,273,322,840 - 510,286,023,000) = 16,987,299,840 B  ✅ (= 16,986,931,200 + 368,640)
```

(Previously written as "15,360 expert-projections"; 15,360 is the **expert**
count, and 15,360 × 2 × 4 = 122,880 ≠ 368,640. The projection count is 46,080 —
the figure the card itself uses in *"18 of 46,080 projection entries used
fallback scales"*
[src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md).)

The packed 4-bit payload is byte-identical in size; you pay **exactly one extra
scale byte per 32 weights** to halve the block size from 32 to 16. That is the
whole trade: **+3.33 %** checkpoint size (16,987,299,840 / 510,286,023,000) for
NVFP4's
*"twice as many opportunities to match the local dynamic range of the data"*
[src](https://developer.nvidia.com/blog/introducing-nvfp4-for-efficient-and-accurate-low-precision-inference/).

---

## 4. Weight memory by dtype

Scale overhead per METHODOLOGY §1. Base = 763,205,315,794 real parameters.

| Scheme | Bytes/param | Total GB | Total GiB | Notes |
|---|---|---|---|---|
| **Native checkpoint (as shipped)** | **0.691 avg** | **527.27** | **491.06** | measured; mixed NVFP4 + MXFP8 + MXFP4 + BF16 + FP32. (Was stated as 0.638 — recomputed 527,273,322,840 / 763,205,315,794 = 0.6909.) |
| Base `deepseek-ai/DeepSeek-V4.1-Flash` | **0.669 avg** | 510.29 | 475.24 | same params, MXFP4 experts. (Was 0.619; 510,286,023,000 / 763,205,315,794 = 0.6686.) HF API confirms an identical `safetensors.total` of 763,205,315,794 and `I8` 557,171,343,360 = backbone + MTP experts both MXFP4 [src](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash) |
| All-BF16 (hypothetical) | 2.0 | 1,526.4 | 1,421.6 | never shipped; 9 × GB300 minimum |
| All-FP8 E4M3 + UE8M0/32 | 1.03125 | 787.1 | 733.0 | |
| All-NVFP4 gs16 | 0.5625 | 429.3 | 399.8 | if experts *and* attention *and* Engram were NVFP4 |
| All-MXFP4 gs32 | 0.53125 | 405.5 | 377.6 | |
| All-INT4 AWQ/GPTQ g128 | ≈0.53 | 404.5 | 376.7 | `est.`; no such checkpoint ships for NVFP4 source |

**Read the first two rows together: requantising to NVFP4 made this checkpoint
16.99 GB *bigger*, not smaller.** 527.27 GB vs the base's 510.29 GB. Only 58.0 %
of those bytes are NVFP4 (§3.3), and NVIDIA publishes **no throughput or latency
figure at all** for the result — so this build buys accuracy-neutrality at
group-16, not memory and not speed (§0). If the goal is memory, the base MXFP4
checkpoint is smaller and the community Engram-quantised builds (§9.2) are
smaller still by ~100 GB.

### 4.1 The headroom nobody is using

The shipped checkpoint spends **188.8 GiB (38.5 %) on an FP8/MXFP8 Engram table**
and **4.7 GiB on FP8 attention**. Recomputed from 196,613,849,600 Engram
parameters (was "≈ 96 GiB" / "≈ 99 GiB" / "≈ 395 GiB"; those three figures were
each ~6–10 GiB optimistic):

```
Engram as shipped (FP8 + 1 E8M0/32) = 202,758,032,400 B = 202.76 GB = 188.83 GiB
Engram at NVFP4 gs16 (0.5625 B/p)   = 110,595,290,400 B = 110.60 GB = 103.00 GiB  -> saves 92.16 GB / 85.83 GiB
Engram at MXFP4 gs32 (0.53125 B/p)  = 104,451,107,600 B = 104.45 GB =  97.28 GiB  -> saves 98.31 GB / 91.56 GiB
checkpoint after NVFP4 Engram       = 435.11 GB = 405.23 GiB
checkpoint after MXFP4 Engram       = 428.97 GB = 399.51 GiB
```

So the realistic floor is **≈ 399–405 GiB**, not 395 GiB — still **under
2 × GB300 (2 × 279 = 558 GB) and inside 4 × B200 (720 GB) with ≈ 200 GB of KV
headroom**. NVIDIA did not do this, and the card does not say
why. Plausible reasons: Engram rows are gathered, not GEMM'd, so FP4 buys no
FLOPs; and a 16-value block on a 256-wide row is a big accuracy risk for a pure
memory lookup. **⚠️ TO BE VERIFIED** — no NVIDIA statement found on Engram
quantisation. Note that the community *has* shipped Engram-quantised variants
(§9), so the headroom is real.

### 4.2 Per-GPU weight footprint (TP/EP sharded)

`per_gpu_weights = 527.27 GB / n_gpus` assuming everything shards cleanly. It
does not quite: `embed.weight` (1.32 GB) and the mHC/norm tensors are commonly
replicated, and Engram tables shard by row (`ParallelEngramEmbedding` in
[`inference/model.py`](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/inference/model.py)).

| n_gpus | GB/GPU | Fits on |
|---|---|---|
| 2 | 263.6 | **infeasible (KV) by METHODOLOGY §3** on every part: 91.5 % of GB300's 288 GB nameplate, 98.4 % of HGX B300's 268 GB — no room for the 4 GB activation workspace, let alone KV. The measured InferenceX TP=2 run (§5.6) only closes against 288 GB *physical*, which is the GB300/nameplate figure, not the as-deployed HGX B300 268 GB |
| 4 | 131.8 | GB300 (288 GB), B300 HGX (268 GB), B200 (180 GB), MI355X (288 GB) |
| 8 | 65.9 | + H200, RTX PRO 6000 (96 GB), H100/A100 (80 GB, barely) |
| 16 | 33.0 | comfortable on H100/A100 |

---

## 5. KV cache and per-sequence state

This is the model's marquee feature, and it reproduces from first principles.

### 5.1 Derivation

From [`inference/model.py`](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/inference/model.py)
and [`inference/kernel.py`](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/inference/kernel.py),
there are **three** caches, not one:

| Cache | Where | Shape | Quantisation (from the code) |
|---|---|---|---|
| `window_kv_cache` | **every** layer | `[bsz, window_size=128, head_dim=512]` | `act_quant(kv, 32, "ue8m0", ...)` → **FP8 + 1 E8M0 per 32** |
| `compress_kv_cache` | only `kv_source_layers` = [2,8,14,20] | `[bsz, max_seq_len/ratio, 512]` | `fp4_act_quant(latent, 16, scale_dtype=float8_e4m3fn)` → **NVFP4 E2M1 + 1 E4M3 per 16** |
| `Indexer.k_cache` | only layers that `owns_k` (= `kv_source_layers`) | `[bsz, max_seq_len/ratio, index_head_dim=128]` | `fp4_act_quant(k, 32)` → **FP4 + 1 E8M0 per 32** |

`fp4_act_quant` packs to `torch.float4_e2m1fn_x2` of width `N//2` plus a scale
tensor of width `N//block_size`, so bytes per cached vector are exact:

```
compressed KV vector : 512/2 + 512/16 = 256 + 32  = 288 B  per compressed position
indexer K vector     : 128/2 + 128/32 =  64 +  4  =  68 B  per compressed position
SWA window slot      : 512   + 512/32 = 512 + 16  = 528 B  per token per layer (ring of 128)
```

Only layers 2, 8, 14 (`compress_ratio = 2`) and 20 (`compress_ratio = 1`) write
the growing caches:

| Layer | ratio | compressed positions / token | KV B/tok | indexer B/tok |
|---|---|---|---|---|
| 2 | 2 | 0.5 | 144 | 34 |
| 8 | 2 | 0.5 | 144 | 34 |
| 14 | 2 | 0.5 | 144 | 34 |
| 20 | 1 | 1.0 | 288 | 68 |
| | | | **720** | **170** |

```
kv_bytes_per_token = 720 + 170 = 890 B/token
```

The base model card states the global KV cache footprint is **"890 bytes per
token"** [src](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/README.md).
✅ Exact match, derived independently. The remaining 36 of 40 layers hold **zero**
growing cache — that is the cross-layer sharing.

### 5.2 Bytes/token at other KV precisions

| KV scheme | compressed KV | indexer K | **total B/token** | vs shipped |
|---|---|---|---|---|
| **As shipped: FP4 main KV (gs16) + FP4 indexer (gs32)** | 720 | 170 | **890** | 1.00× |
| FP8 main KV + FP8 indexer (`+ E8M0/32`) | 1,320 | 330 | 1,650 | 1.85× |
| BF16 (no KV quantisation) | 2,560 | 640 | 3,200 | 3.60× |

`kv_cache_quant_algo` is `null` in the checkpoint — the FP4 KV format is **baked
into the architecture**, not an engine flag. There is nothing to turn on and
nothing to turn off.

### 5.3 Fixed per-sequence state (does not grow with context)

```
SWA ring    : 128 slots × 528 B = 67,584 B per layer
            × 40 backbone layers = 2,703,360 B = 2.58 MiB / sequence
            × 43 incl. 3 MTP     = 2,906,112 B = 2.77 MiB / sequence
Compressor  : kv_state + score_state, [bsz, ratio=2, 512] fp32 × 2 × 3 layers
            = 3 × 2 × 2 × 512 × 4 = 24,576 B = 24 KiB / sequence
Engram      : NgramHashState, small rolling hash; size not stated ⚠️ TO BE VERIFIED
```

Per METHODOLOGY §2, `fixed_state_per_seq = S × (ring buffers + recurrent state)`,
where `S` is the engine's per-request slot count. **No engine doc states an `S`
for V4.1-Flash**, so every figure below assumes `S = 1` ⚠️ **TO BE VERIFIED** —
an MTP-speculating runtime plausibly allocates extra ring slots for the drafted
block (`dspark_block_size 5`), which would scale the 2.58 MiB accordingly. It is
a per-sequence constant either way, so it moves `max_concurrency` by <1 % at 8K
and not at all at 1M.

The SWA state is a **ring buffer, not a KV cache**: a fixed 2.58 MiB per
sequence regardless of whether the context is 8K or 1M. The tech report calls
the mechanism **SWA Bounded Replay** — *"reconstructs missing SWA KV states by
replaying only the most recent n_win tokens, avoiding the need to persist SWA KV
to SSD"* [src](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/README.md).
So for prefix-cache/offload purposes you persist only the 890 B/token stream;
the SWA ring is regenerated from the last 128 tokens.

### 5.4 Totals per sequence

`kv_total(ctx) = ctx × 890 + 2,703,360`

| Context | **Shipped (FP4)** | FP8 KV | BF16 KV |
|---|---|---|---|
| 8 K | **9.5 MiB** | 15.5 MiB | 30.0 MiB |
| 32 K | **30.4 MiB** | 54.1 MiB | 105.0 MiB |
| 128 K | **113.8 MiB** | 208.8 MiB | 405.0 MiB |
| 1 M | **892.6 MiB** | 1,652.6 MiB | 3,205.0 MiB |

Sequences per 100 GB of KV budget: **10,005 @ 8K · 3,138 @ 32K · 837 @ 128K · 106 @ 1M**.

### 5.5 Effect of each mechanism, isolated

| Mechanism | Effect on KV |
|---|---|
| Sliding window (W=128) | moves 40 layers' worth of K/V off the growing path entirely → **fixed 2.58 MiB/seq** |
| Cross-layer KV sharing (`kv_source_layers`) | 4 of 40 layers hold cache → **10× reduction** |
| KV compression (`compress_ratios` 2 in the encoder) | encoder sources store ctx/2 positions → 3 of the 4 sources halved |
| FP4 main KV (E2M1 + E4M3/16) | 3.60× vs BF16, 1.85× vs FP8 |
| Sparse attention (`index_topk` 512) | does **not** reduce cache size — reduces *reads* per step (§6) |
| Hierarchical indexer (candidate pool 2048×8) | bounds indexer *compute* independently of context, not cache |

Net vs the previous generation: *"reduce the global KV cache footprint to 890
bytes per token — roughly 1/4 of DeepSeek-V4-Flash"*, and *"approximately 4-fold
and 437-fold reductions relative to DeepSeek-V4-Flash and DeepSeek-V1"*
[src](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/README.md).
(Marketing framing; the 890 B figure itself is verified above.)

### 5.6 Fit table (METHODOLOGY §3)

`usable = capacity × 0.90`, `activation_ws = 4 GB/GPU`, weights = 527.27 GB.

Capacities are the **as-deployed** figures pinned in
[METHODOLOGY §8](../../METHODOLOGY.md): B200 180 GB, **HGX / DGX B300 / AWS
p6-b300 268 GB per GPU = 2,144 GB per 8-GPU node**, **GB300 NVL72 288 GB per GPU
(≈ 279 usable)**. HGX B300 and GB300 NVL72 are different parts and are never
merged. Recomputed 2026-09-19 with `python3`; the whole table moved because the
B300 row previously used 270 GB and the GB300 rows 279 GB.

| GPU | HBM/GPU (as deployed) | Min GPUs to hold weights | KV budget there | Max concurrency @8K / 32K / 128K / 1M |
|---|---|---|---|---|
| GB300 (NVL72) | 288 GB (≈279 usable) | **4** | 493.5 GB (459.6 GiB) | 49,381 / 15,487 / 4,134 / **527** |
| GB300 ×8 | 288 GB | — | 1,514.3 GB (1,410.3 GiB) | 151,519 / 47,520 / 12,687 / 1,617 |
| B300 (HGX / DGX / p6-b300) | **268 GB** | **4** | 421.5 GB (392.6 GiB) | 42,176 / 13,227 / 3,531 / 450 |
| B300 ×8 (one 2,144 GB node) | 268 GB | — | 1,370.3 GB (1,276.2 GiB) | 137,111 / 43,001 / 11,480 / 1,464 |
| B200 | 180 GB | **4** | 104.7 GB (97.5 GiB) | 10,478 / 3,286 / 877 / 111 |
| B200 ×8 | 180 GB | — | 736.7 GB (686.1 GiB) | 73,715 / 23,118 / 6,172 / 787 |
| H200 SXM | 141 GB | **8** | 455.9 GB (424.6 GiB) | 45,618 / 14,307 / 3,819 / 487 |
| H100 SXM 80 GB | 80 GB | **8** | 16.7 GB (15.6 GiB) | 1,673 / 524 / 140 / **17** |
| H100 ×16 | 80 GB | — | 560.7 GB (522.2 GiB) | 56,104 / 17,595 / 4,697 / 599 |
| A100 SXM 80 GB | 80 GB | **8** | 16.7 GB (15.6 GiB) | same as H100 ×8 *(capacity only — see §8, it cannot run this checkpoint)* |
| RTX PRO 6000 Server Ed. (96 GB) | 96 GB | **8** | 131.9 GB (122.9 GiB) | 13,200 / 4,139 / 1,105 / 140 |
| MI355X | 288 GB | **4** | 493.5 GB (459.6 GiB) | 49,381 / 15,487 / 4,134 / 527 |
| MI355X ×8 | 288 GB | — | 1,514.3 GB (1,410.3 GiB) | 151,519 / 47,520 / 12,687 / 1,617 |

All of these are `est.` from the formula, with `usable = capacity × 0.90`,
`activation_ws = 4 GB/GPU` and `max_concurrency(ctx) = floor(kv_budget /
(ctx × 890 B + 2,703,360 B))`, all arithmetic in bytes. **Capacity fit is not the
same as kernel support** — see §8. H100/H200/A100 and MI355X appear in this table
because the *bytes* fit, not because the NVFP4 expert GEMM runs. GB300 and MI355X
share a row of numbers because both are 288 GB at 8 TB/s; they share nothing else.

> **TP=2 is `infeasible (KV)` at the as-deployed B300 figure.** 2 × 268 GB
> = 536 GB against 527.27 GB of weights leaves 8.7 GB before the 0.90 usable
> factor — negative KV budget. It only closes against **288 GB physical per GPU**,
> which is the GB300 NVL72 / die-nameplate figure, not HGX B300's as-deployed
> 268 GB. AWS publishes `p6-b300.48xlarge` at "2,144 HBM3e" GB for 8 GPUs
> [src](https://aws.amazon.com/ec2/instance-types/p6/) (re-fetched and confirmed
> verbatim 2026-09-19) and NVIDIA's DGX B300 page at 2.1 TB (÷8 = 262.5 GB)
> [src](https://www.nvidia.com/en-us/data-center/dgx-b300/), so no HGX/DGX/AWS
> B300 SKU exposes 288 GB. Whatever the InferenceX harness ran on, do not plan a
> TP=2 deployment of this checkpoint on a 268 GB B300.

**Cross-check against a real engine.** The InferenceX B300 TP=2 run (2 GPUs)
reports a KV pool of **32,580,148 tokens** [src](https://inferencex.semianalysis.com/inference/deepseek-v41-flash).
32,580,148 × 890 B = **29.0 GB** — which requires 2 × 288 GB physical HBM minus
527.3 GB of weights minus ~19 GB of runtime overhead, i.e. it is *not*
reproducible at the pinned 268 GB (see the box above). Take it as independent
corroboration of the **890 B/token** figure from a production serving stack, and
as a ⚠️ open question about what SKU it ran on (§12.7). (The larger configs'
pool sizes are far below their memory headroom, so they are evidently capped by
engine configuration rather than capacity — ⚠️ TO BE VERIFIED which setting.)

---

## 6. Compute profile

### 6.1 FLOPs per token (dense term)

```
prefill (encoder only, 20 layers): 2 × 7,545,815,040  = 15.09 GFLOP/token
decode  (40 layers + LM head)    : 2 × 15,753,543,680 = 31.51 GFLOP/token
```

Prefill is **half** the FLOP/token of decode. This is the CED payoff and it
inverts the usual planning intuition: for a 300K-in / 2K-out agentic request,
prefill costs 300,000 × 15.09 GFLOP = 4.53 PFLOP while decode costs
2,000 × 31.51 GFLOP = 0.063 PFLOP.

### 6.2 Attention FLOPs — there is no T² term

```
SWA, all 40 layers      : 4 × 64 × 512 × T × 128  = 0.671 GFLOP per token of T
sparse, 38 layers (r>0) : 4 × 64 × 512 × T × 512  = 2.550 GFLOP per token of T
                                            total = 3.221 GFLOP/token, context-INDEPENDENT
```

Because every layer attends to a 128-slot window plus at most `index_topk = 512`
compressed positions, attention cost per token is **constant in context length**.
A 1M-token prefill has the same per-token attention cost as an 8K one. The cost
that *does* grow is the **indexer scoring**:

| Context | Full-pool indexers (layers 2, 8, 14, 20) | Candidate-pool indexers (24, 28, 32, 36) |
|---|---|---|
| 8 K | 0.17 GFLOP/tok | 0.54 GFLOP/tok |
| 32 K | 0.67 GFLOP/tok | 0.54 GFLOP/tok |
| 128 K | 2.68 GFLOP/tok | 0.54 GFLOP/tok |
| 1 M | **21.47 GFLOP/tok** | **0.54 GFLOP/tok** (flat) |

`est.`, computed as `2 × index_n_heads × index_head_dim × compressed_positions`.
At 1M context the four *full-pool* indexers cost more than the entire encoder
forward pass. The Hierarchical Sparse Indexer is precisely the fix: the four
deeper indexers score a fixed 2048 × 8 = 16,384-position candidate pool and stay
flat. **⚠️ TO BE VERIFIED** whether production kernels also bound the four
full-pool indexers (the reference implementation does not).

### 6.3 Bytes read per decode step (METHODOLOGY §4, MoE distinct-expert model)

NVFP4 expert bytes (measured):
```
one routed expert = 3×(2304×5120/2) packed + 3×(2304×5120/16) E4M3 + 6×4 B FP32
                  = 17,694,720 + 2,211,840 + 24 = 19,906,584 B = 18.98 MiB
```
Per-layer non-expert traffic (FP8 attention + FP8 shared expert + scales + BF16
router + FP32 mHC) = 174,931,968 B = **166.83 MiB**. Plus, once per step:
Engram `wkv` 315,043,840 B = **300.45 MiB** (previously mis-stated as 309.38 MiB)
and the BF16 LM head 1,323,827,200 B = **1,262.50 MiB**.

**KV read per sequence per step** (METHODOLOGY §3 requires this to scale with
`batch × ctx × kv_bytes_per_token`, not a flat per-step constant). For this
architecture the growing term is the *indexer scan*, because the main KV read is
bounded by `index_topk = 512`:

```
full-pool indexer scan (layers 2, 8, 14, 20) = ctx × 170 B            <- grows with ctx
candidate-pool indexers (24,28,32,36)        = 4 × 16,384 × 68 B  =  4,456,448 B
sparse main-KV gather  (40 layers × top-512) = 40 × 512 × 288 B   =  5,898,240 B
SWA ring read          (40 layers × 128)     = 40 × 128 × 528 B   =  2,703,360 B
                                        flat term                 = 13,058,048 B = 12.45 MiB
kv_read(batch, ctx) = batch × (ctx × 170 + 13,058,048) B
```

`distinct_experts(b) = 384 × (1 − (1 − 6/384)^b)`

| Batch | E[distinct experts / layer] | Expert bytes | Weight traffic / step | + KV @32K | + KV @128K | **Total @128K** | Bytes / token @128K |
|---|---|---|---|---|---|---|---|
| 1 | 6.0 | 4.45 GiB | **12.49 GiB** | 0.02 GiB | 0.03 GiB | **12.53 GiB** | 12,826 MiB |
| 8 | 45.5 | 33.71 GiB | 41.75 GiB | 0.14 GiB | 0.26 GiB | 42.02 GiB | 5,378 MiB |
| 32 | 152.0 | 112.73 GiB | **120.77 GiB** | 0.56 GiB | 1.05 GiB | **121.82 GiB** | 3,898 MiB |
| 64 | 243.8 | 180.83 GiB | 188.87 GiB | 1.11 GiB | 2.11 GiB | 190.98 GiB | 3,056 MiB |
| 128 | 332.8 | 246.83 GiB | **254.87 GiB** | 2.22 GiB | 4.21 GiB | **259.09 GiB** | 2,073 MiB |
| 256 | 377.2 | 279.71 GiB | 287.76 GiB | 4.44 GiB | 8.43 GiB | 296.18 GiB | 1,185 MiB |

Recomputed with `python3` 2026-09-19. Every row is feasible on every config in
the roofline below: the binding `max_concurrency` at 128K is 877 (4 × B200), the
smallest of the set (§5.6). KV is **1.6 % of decode traffic at batch 128 / 128K**
— that is the 890 B/token architecture doing its job, but it is not zero and it
is what makes 1M-context batches bite.

Plus **12,672 B/token of scattered Engram gather** (2 layers × 24 rows × (256 B
FP8 + 8 B scale)) — random access, so it burns far more than its byte count
suggests on any GPU whose DRAM prefers streaming.

Roofline decode step time at MBU 0.7, **at 128K context** (32K is ≤0.8 % faster),
`est.`:

| Config | HBM BW/GPU | batch 1 | batch 32 | batch 128 |
|---|---|---|---|---|
| 4 × GB300 | 8.0 TB/s | 0.60 ms | 5.84 ms | 12.42 ms |
| 4 × B300 (HGX) | 8.0 TB/s | 0.60 ms | 5.84 ms | 12.42 ms |
| 4 × B200 | 7.7 TB/s | 0.62 ms | 6.07 ms | 12.90 ms |
| 8 × H200 | 4.8 TB/s | 0.50 ms | 4.87 ms | 10.35 ms |
| 4 × MI355X | 8.0 TB/s | 0.60 ms | 5.84 ms | 12.42 ms |
| 8 × RTX PRO 6000 **Server Edition** | **1.597 TB/s** | 1.50 ms | 14.63 ms | 31.11 ms |

The RTX PRO 6000 row uses the **Server Edition's 1,597 GB/s**
[src](https://lenovopress.lenovo.com/lp2263.pdf) (re-fetched 2026-09-19; the PDF
contains "1597" and "GDDR7" and no 1,792 figure). The 1,792 GB/s number belongs
to the **Workstation Edition** and would understate every latency here by 11 %.
Even at 8 GPUs the part is 2.5× slower per decode step than 4 × B200 — and that
is the *optimistic* bound, before PCIe-only 8-way TP (§8.4).

Measured GB300 TPOT at concurrency 1 is **2.1 ms** and at 128 is **25.1 ms**
(§10) — i.e. ~3.5× and ~2× the roofline. At batch 1 the gap is the usual
small-batch launch overhead (effective MBU ≈ 0.2); at batch 128 it reflects the
MTP verify passes and the indexer work the roofline ignores. The H200 line in
this table is **the wrong answer in practice**: on `sm_90` the NVFP4 experts
cannot execute natively and the path taken is Marlin W4A16 (§8), which is
compute-bound, not bandwidth-bound.

### 6.4 Vision encoder FLOPs

32 blocks, d = 1024, inter = 2816, patch 14, 3×3 pixel unshuffle, **max 1024
image tokens** after downsampling → the ViT runs on 9 × 1024 = 9,216 patches max.

```
per-block params  = 12,845,056
ViT dense FLOPs   ≈ 2 × 411,041,792 × n_patches
                  = 0.822 GFLOP per patch
at 9,216 patches  ≈ 7.58 TFLOP per full-resolution image
+ ViT self-attention (full, not sparse): 32 × 4 × 16 × 64 × 9216² ≈ 11.1 TFLOP
+ aligner 2 × 73,400,320 × 1024 ≈ 0.15 TFLOP
TOTAL ≈ 18.8 TFLOP per max-resolution image           `est.`
```
Then 1,024 image tokens enter the LM at 15.09 GFLOP each = 15.5 TFLOP of prefill.
Total 18.8 + 15.5 = **34.3 TFLOP**, i.e. a full-resolution image costs roughly
**as much as 2,270 text tokens of prefill — ViT included** (34.3 TFLOP ÷ 15.09
GFLOP/token). The previous wording said "2,300 text tokens of prefill, *plus* the
ViT", which double-counts: the ViT is already inside the 2,300. In LM tokens
alone an image is exactly 1,024 tokens; the extra ~1,250-token-equivalent *is*
the ViT. Per-frame video figures: none published
**⚠️ TO BE VERIFIED** (no video pipeline documented in either card).

---

## 7. Speculative decoding and MTP

### 7.1 What this checkpoint ships

| Component | Present? | Precision | Params |
|---|---|---|---|
| 3 MTP layers (`num_nextn_predict_layers: 3`) | ✅ | **MXFP4 experts** (excluded by `ignore: ["mtp.*"]`), FP8 attention | 14.08 B |
| `mtp.main_proj` [5120, 15360] | ✅ | FP8 | 78.6 M |
| `mtp.markov_head.embed` / `.head` [129280, 256] | ✅ | BF16 | 66.2 M ×2 |
| `mtp.confidence_head.proj` [1, 5376] | ✅ | BF16 | 5,376 |
| **MTP total** | | | **14.23 B** |
| MTP active per drafted token (top-3 of 128 + shared) | | | 269.8 M |

DSpark config: `dspark_block_size 5`, `dspark_target_layer_ids [37,38,39]`,
`dspark_markov_rank 256`, `dspark_n_routed_experts 128`,
`dspark_num_experts_per_tok 3`, `dspark_noise_token_id 128799`
[src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/blob/main/config.json).
The base card describes it as *"DSpark speculative decoding (semi-autoregressive
draft generation with confidence-scheduled verification)"*
[src](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/README.md).

The 14.23 B MTP stack costs **6.72 GiB** of the checkpoint in MXFP4 experts alone
— 1.4 % of the download for a feature NVIDIA did not benchmark.

### 7.2 What NVIDIA says about it

> *"DSpark tensors are preserved, but speculative decoding was not exercised in
> the reported validation."*
> [src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md)

That is the complete extent of NVIDIA's statement. **No acceptance rate, no
speedup, no per-engine numbers are published for this checkpoint.**

### 7.3 What is known from elsewhere

* Every one of the 98 InferenceX benchmark rows for DeepSeek-V4.1-Flash carries
  `spec_method: "mtp"` [src](https://inferencex.semianalysis.com/inference/deepseek-v41-flash)
  — so in the vLLM/SGLang configurations that produced §10's numbers, MTP
  speculation **was on**. Acceptance rate is not exposed by that API.
  **⚠️ TO BE VERIFIED**: acceptance rate / accepted-tokens-per-step.
* vLLM registers a dedicated V4.1 draft model class:
  `"DSparkV41DraftModel": ("vllm.models.deepseek_v41", "DSparkDeepseekV4ForCausalLM")`
  [src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/registry.py)
  — distinct from the V4.0 `DSparkDraftModel`, so V4.1 DSpark is first-class in
  vLLM main.
* SGLang main ships `deepseek_v4_dspark.py` and `deepseek_v4_nextn.py`
  [src](https://github.com/sgl-project/sglang/tree/main/python/sglang/srt/models).
  Whether the V4.1 config routes to them is **⚠️ TO BE VERIFIED**.
* **Mixing risk specific to this checkpoint:** the backbone experts are NVFP4
  (needs `sm_100+`) while the DSpark/MTP experts are **MXFP4**. An engine must
  therefore instantiate *two different MoE quantisation methods in one process*.
  Both run on Blackwell; on a Marlin-fallback GPU the two halves take different
  fallback paths. No source confirms any engine has validated this combination
  — **⚠️ TO BE VERIFIED**. This is the most likely place for this checkpoint to
  break.
* Standard MoE speculation economics apply: at batch ≥ 64 the distinct-expert
  count is already 244/384 (§6.3), so drafting extra tokens buys little and
  costs a full verify pass. Expect MTP to help below ~batch 32 and to be worth
  disabling above it — `est.`, not measured for this model.

---

## 8. Engine support matrix

Status as of **2026-09-19**.

### 8.1 Summary

| Engine | Supports `DeepseekV41ForCausalLM`? | Supports **this NVFP4 checkpoint**? | Evidence |
|---|---|---|---|
| **SGLang** | ✅ in `main` — `deepseek_v4.py` carries `is_dsv41`, plus `dsv41_sparse` (`DeepseekV41Compressor`, `DeepseekV41Indexer`), `deepseek_v41_vit.py`, `deepseek_v41_image_processing`, `layers/engram.py` | ✅ **NVIDIA's primary validated runtime** | [src](https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/models/deepseek_v4.py), [src](https://github.com/sgl-project/sglang/tree/main/python/sglang/srt/models) |
| **vLLM** | ✅ in `main` — `"DeepseekV41ForCausalLM": ("vllm.models.deepseek_v41", ...)`, in-tree package `vllm/models/deepseek_v41/` with `sparse_mla.py`, `nvidia/flashinfer_sparse.py`, `amd/rocm.py` | ✅ per the model card (NVIDIA ran its accuracy evals on vLLM) | [src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/registry.py) |
| **TensorRT-LLM** | ❌ **not listed.** The supported-models matrix has `DeepseekV3ForCausalLM`, `DeepseekV32ForCausalLM`, `DeepseekV4ForCausalLM` — **no V4.1 row** | ❌ | [src](https://nvidia.github.io/TensorRT-LLM/models/supported-models.html) |
| **transformers** | Config declares `transformers_version: "5.6.0"`; no modelling code ships in the repo (only the standalone `inference/` reference) | ❌ for serving | [src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/blob/main/config.json) |
| **TokenSpeed** | ✅ — **ships a DeepSeek-V4.1-Flash recipe**: a `tokenspeed serve deepseek-ai/DeepSeek-V4.1-Flash` command, a dedicated FlatKV attention backend and a per-commit **GB300 Slurm 1P1D (disaggregated prefill/decode) CI job**; the aggregated command is TP8/EP with `--moe-backend marlin` | ⚠️ — the recipe names the **base** MXFP4 repo, not `nvidia/…-NVFP4`; no NVFP4 variant of the recipe is published | [src](https://lightseek.org/tokenspeed/recipes/models), cross-ref [`cross-cutting/inference-engines.md` §2.5](../../cross-cutting/inference-engines.md) |
| **NVIDIA Dynamo** | **⚠️ TO BE VERIFIED** — no Dynamo recipe found for DeepSeek-V4.1-Flash | — | — |
| Reference `inference/` | ✅ — tilelang kernels, real `fp8_gemm` + `fp4_gemm`, `--expert-dtype fp4` | ✅ but it is *"a readable reference implementation rather than a production serving engine"* | [src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/inference/README.md) |

**Documentation lag:** vLLM's published supported-models page (developer preview,
2026-09-16) still lists only up to `DeepseekV4ForCausalLM` and has no V4.1 row
[src](https://docs.vllm.ai/en/latest/models/supported_models.html), even though
`main`'s registry has it. Read the registry, not the docs.

### 8.2 Launch recipes — quoted verbatim from the model card

**SGLang.** *"Use `lmsysorg/sglang:dev-cu13-dsv41` on four GB300 GPUs. The tested
build was SGLang commit `da64c5cbb8cf6bfd39be19da43573fdfd484c43a`."*
[src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md)

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

> *"The tested build automatically selects `flashinfer_trtllm_routed` for the
> NVFP4 experts. Include a request-level `reasoning_effort`, such as `"max"`, to
> enable thinking; thinking is off by default in this SGLang build."*

Note `da64c5c…` has `python/sglang/srt/configs/deepseek_v41.py` but **no**
`models/deepseek_v41.py` — V4.1 is handled inside `deepseek_v4.py` behind the
`is_dsv41` flag. Note also `--max-running-requests 16`, which is very
conservative given the KV budget in §5.6.

**vLLM.** *"Use `vllm/vllm-openai:deepseekv41-flash-0909` on four GB300 GPUs."*

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

> *"The vLLM example is text-only; image serving with vLLM requires a multimodal
> configuration."*

Three flags deserve attention:
* `--language-model-only` — the vision tower is disabled. This recipe does **not**
  serve the model's headline multimodal capability.
* `--no-enable-prefix-caching` — prefix caching is **off** in NVIDIA's reference
  command. For the agentic workloads this model targets (97 % cache hit rates in
  §10), turning it on is the single biggest available win. Why the card disables
  it is unstated — **⚠️ TO BE VERIFIED** (likely an interaction with SWA Bounded
  Replay, whose windowed state has to be replayed rather than restored).
* `--max-num-seqs 32` with `--max-model-len 1048576` — 32 × 892.6 MiB = 27.9 GB
  of KV, a small fraction of the **493.5 GB** available on 4 × GB300 (§5.6).

Both recipes: **4 GPUs, tensor parallel, no expert parallelism, no
disaggregation.** NVIDIA validated nothing larger.

### 8.3 What "validated" means here

> *"Both runtimes have been tested with this NVFP4 checkpoint on GB300. The
> benchmark results below were obtained with vLLM; SGLang was validated through
> loading, generation, reasoning/tool-call parsing, and image-input smoke tests."*
> *"Supported Hardware Microarchitecture Compatibility: NVIDIA Blackwell."*
> *"Test Hardware: NVIDIA GB300 (Blackwell)."*
> [src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md)

So: **one GPU model (GB300), one parallelism (TP=4), two engines, accuracy evals
on vLLM only.** Everything else is extrapolation.

### 8.4 Per-GPU verdict

The decisive question is not "does the model run" — it is "does the **NVFP4 W4A4
expert GEMM** run". SGLang's ModelOpt path answers it in code:

```python
# ModelOptFp4LinearMethod
if not get_platform().is_blackwell:
    raise ValueError("ModelOpt NVFP4 native dense GEMM backends require SM100+. "
                     "Use --fp4-gemm-backend marlin on SM80-SM90.")

# ModelOptNvFp4FusedMoEMethod
use_marlin_fallback = (8, 0) <= capability < (10, 0)
if not get_platform().is_blackwell and not use_marlin_fallback:
    raise ValueError("Current platform does not support NVFP4 quantization with the "
                     "selected MoE backend. Please use Blackwell and above, or use "
                     "moe_runner_backend=marlin on SM80+.")
```
[src](https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/layers/quantization/modelopt_quant.py)

vLLM states the same outcome in prose: *"On GPUs without a supported native FP4
GEMM kernel, vLLM falls back to weight-only (W4A16) execution via Marlin and logs
a warning"* [src](https://docs.vllm.ai/en/latest/features/quantization/modelopt.html).

| GPU | CC | NVFP4 native? | Fits 527 GB? | Verdict for **this** checkpoint |
|---|---|---|---|---|
| **GB300 NVL72** (288 GB/GPU, ≈279 usable) | `sm_103` | ✅ 5th-gen tcgen05 block-scaled FP4 | ✅ at 4 GPUs, 493.5 GB KV budget | **The target.** NVIDIA's only tested hardware, and both official recipes are `--tp 4` on it. |
| **B300 HGX / DGX / AWS p6-b300** (**268 GB/GPU**, 2,144 GB/node) | `sm_103` | ✅ same | ✅ at 4 GPUs, 421.5 GB KV budget. **TP=2 is `infeasible (KV)`** at 268 GB (§5.6) | Same silicon family as GB300 but a **different as-deployed capacity and a lower dense FP4 peak** (13,500 vs 15,000 TFLOPS) — never merge the two rows. NVIDIA did not test this SKU. |
| **B200** | `sm_100` | ✅ | ✅ at 4 GPUs, 104.7 GB KV budget | Should work — same `is_blackwell` branch, same FlashInfer trtllm-gen path. Not validated by NVIDIA. InferenceX ran the *base* FP4 checkpoint on B200 successfully (§10). |
| **RTX PRO 6000 Blackwell Server Edition** — the part you would actually rack 8 of: 96 GB GDDR7 ECC, **1,597 GB/s** [src](https://lenovopress.lenovo.com/lp2263.pdf), ≈2,000 TFLOPS dense FP4 (4,000 is the sparse figure). *(The 1,792 GB/s seen elsewhere is the **Workstation Edition** [src](https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000/) — an 11 % gap on a decode-bound workload; every roofline in §6.3 uses 1,597.)* Cross-ref [`research/gpus/rtx6000-pro.md`](../../gpus/rtx6000-pro.md) | `sm_120` | ✅ FP4 tensor cores present; FlashInfer's support table lists SM 12.0/12.1 but names "RTX 50 series, DGX Spark" as the examples and warns *"Not all features are supported across all compute capabilities"* [src](https://github.com/flashinfer-ai/flashinfer) | ✅ at 8 GPUs, 131.9 GB KV | **Plausible but unproven.** SGLang explicitly special-cases SM120 (`if is_cuda() and (not get_platform().is_sm120) and ...` around `scaled_fp4_quant`, and a separate `use_sm120_fp8` GEMM path), so the SM120 kernel coverage is *different*, not merely slower. vLLM has a distinct `FLASHINFER_MLA_SPARSE_SM120` backend but **no DSV4.1-specific SM120 backend** [src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/attention/backends/registry.py). No NVLink → 8-way TP over PCIe for a 527 GB model is a hard sell. **⚠️ TO BE VERIFIED.** |
| **H200** | `sm_90` | ❌ | ✅ at 8 GPUs, 456 GB KV | Runs only via **Marlin W4A16**: weights stay 4-bit in HBM, dequantised to BF16 in the kernel epilogue, MMA issued at BF16 rate. You keep the memory saving, you lose the FP4 math entirely. Also: no FP4 KV-cache kernel on Hopper is documented — and FP4 main KV is architectural here, not optional. **⚠️ TO BE VERIFIED** whether FlashMLA/FlashInfer ship an `sm_90` FP4-KV sparse kernel for V4.1. |
| **H100 80 GB** | `sm_90` | ❌ | Only just — 8 GPUs leaves **16.7 GB** for KV (≈1,673 seqs @8K, **17 @1M**) | Marlin W4A16 as above, *and* capacity-starved. Use 16 GPUs or don't. InferenceX's H100 runs top out at concurrency 20–28 before collapsing (§10). |
| **A100 80 GB** | `sm_80` | ❌ | 8 GPUs, 16.7 GB KV | Marlin fallback is *nominally* allowed (`(8,0) <= capability`), but A100 has **no FP8 tensor cores** either, and 5.07 GB of attention weights plus 202 GB of Engram tables are FP8 E4M3 — those would need BF16 upconversion too. Nobody has demonstrated this. Treat as **not supported**. **⚠️ TO BE VERIFIED.** |
| **MI355X** (288 GB HBM3E, 8 TB/s) | gfx950 | ❌ **CDNA 4 implements MXFP4, not NVFP4** (cross-ref [`research/gpus/mi355x.md`](../../gpus/mi355x.md), which cites AMD's own ROCm blog) | ✅ at 4 GPUs | The *model* runs — vLLM `main` ships `vllm/models/deepseek_v41/amd/rocm.py` with `DeepseekV41ROCMAiterMLAAttention`, `DeepseekV41ROCMAiterSparseSWABackend` and AITER FP8 block-scaled GEMMs [src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/models/deepseek_v41/amd/rocm.py), and InferenceX has MI355X results (§10). But those runs serve the **base MXFP4 checkpoint**, which CDNA 4 executes natively at 10.1 PFLOPS dense. **This NVFP4 checkpoint would have to be requantised NVFP4→MXFP4 at load, or emulated** — i.e. you would convert NVIDIA's conversion back. **There is no reason to use this checkpoint on AMD; use the base one.** Note also that vLLM's attention-backend enum has `ROCM_FLASHMLA_SPARSE_DSV4` but **no `…_DSV41`** entry [src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/attention/backends/registry.py), so V4.1's ROCm backends are registered by the model layer rather than the enum. |

### 8.5 Attention backends available for V4.1

vLLM's enum carries exactly three V4.1-specific entries
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/attention/backends/registry.py):

```python
FLASHMLA_SPARSE_DSV41     = "vllm.models.deepseek_v41.sparse_mla.DeepseekV4FlashMLABackend"
FLASHINFER_MLA_SPARSE_DSV41 = "vllm.models.deepseek_v41.nvidia.flashinfer_sparse.DeepseekV4FlashInferMLASparseBackend"
FLASHMLA_MEGA_ATTN_DSV41  = "vllm.models.deepseek_v41.sparse_mla.FlashMLAMegaAttnBackend"
```
with the comment *"Separate names from DSV4 so a V4.1 model never resolves the
V4.0 backend classes through this enum."* `FLASHMLA_MEGA_ATTN_DSV41` has no V4.0
counterpart — it is presumably the fused Mega-mHC/attention kernel the base card
alludes to ("an efficient Mega-mHC kernel"). **⚠️ TO BE VERIFIED.**

Generic FlashAttention (FA2/FA3/FA4) is **not** the relevant kernel family for
this model's language stack — the language attention is latent + sliding-window +
Top-K sparse and goes through the FlashMLA/FlashInfer sparse path. FA-family
kernels matter only for the ViT.

### 8.6 Open issues

* TensorRT-LLM has no V4.1 entry at all.
* No published multi-node, disaggregated-prefill, wide-EP or expert-parallel
  configuration for **this NVFP4 checkpoint**. Both official recipes are
  `--tp 4`, EP off. *(Corrected: for the **base** MXFP4 repo such a
  configuration does exist — TokenSpeed publishes a GB300 **1P1D
  disaggregated** TP4+TP4 CI job and a TP8/EP aggregated command
  [src](https://lightseek.org/tokenspeed/recipes/models). It has not been
  published against the NVFP4 build.)*
* NVIDIA's vLLM recipe disables prefix caching and the vision tower.
* Backbone NVFP4 + MTP MXFP4 in one process is unvalidated (§7.3).
* SGLang's `--max-running-requests 16` and vLLM's `--max-num-seqs 32` are far
  below what §5.6 says the memory allows; nobody has published the tuned numbers.

---

## 9. Available quantised variants

From the HF model search API [src](https://huggingface.co/api/models?search=DeepSeek-V4.1-Flash)
(**129 repos matched** on a re-query with `limit=200` on 2026-09-19 — the "101"
previously recorded was a default-paged count; sizes are `usedStorage`,
downloads as of 2026-09-19). Every `usedStorage` and download figure in the two
tables below was re-fetched per-repo on 2026-09-19 and matches exactly.

### 9.1 NVFP4 siblings

| Repo | Size | DL | Created | Note |
|---|---|---|---|---|
| **nvidia/DeepSeek-V4.1-Flash-NVFP4** | **527.3 GB** | 1,969 | 2026-09-16 | this document; **larger than the 510.3 GB base** (§0) |
| `s-zaizen/DeepSeek-V4.1-Flash-NVFP4` | 527.3 GB | 1,065 | 2026-09-10 | byte-identical size; predates NVIDIA's by 6 days |
| `AtomicChat/DeepSeek-V4.1-Flash-NVFP4-nvidia` | 527.3 GB | 668 | 2026-09-10 | same size |
| `Solstice-AI/DeepSeek-V4.1-Flash-NVFP4` | 527.3 GB | 73 | 2026-09-12 | same size |
| `LibertAIDAI/DeepSeek-V4.1-Flash-NVFP4` | 429.4 GB | 822 | 2026-09-10 | **98 GB smaller** — almost exactly the Engram-tables-to-FP4 saving computed in §4.1 |
| `msuiche/DeepSeek-V4.1-Flash-NVFP4` | 414.9 GB | 505 | 2026-09-10 | smaller still |

Three community repos land on exactly 527.3 GB, which means they applied the
same group-16 rescale to the same tensor set. NVIDIA's differentiator is not
size — it is the **calibration, the accuracy evals, and the "all 16,986,931,200
blocks passed lossless conversion" audit**. The two smaller repos (429 / 415 GB)
evidently quantise tensors NVIDIA's `ignore` list protects; no evals are
published for either. **⚠️ TO BE VERIFIED** what they quantise and at what cost.

### 9.2 Other formats

| Repo | Format | Size | DL | Relevance |
|---|---|---|---|---|
| `deepseek-ai/DeepSeek-V4.1-Flash` | MXFP4 experts + FP8 | 510.3 GB | 429,865 | the base; **the right choice on MI355X** |
| `RedHatAI/DeepSeek-V4.1-Flash` | FP8 | 510.3 GB | 75 | mirror |
| `lvkaokao/…-MXFP4-Engram-AutoRound` | MXFP4 incl. **Engram** | 412.0 GB | 124 | AutoRound; the Engram-quantised direction |
| `lvkaokao/…-W4A16-Engram-AutoRound` | INT4 GPTQ | 451.7 GB | 124 | the W4A16 route for Hopper/Ampere |
| `diffbot/…-EXL3-2.0bpw-2x-RTX-PRO-6000` | EXL3 2.0 bpw | 358.1 GB | 472 | explicitly targeted at 2 × RTX PRO 6000 |
| `satgeze/…-EXL3-2.0bpw-Ablit-EngramQ4-SM120-Dual-RTX-Pro-6000` | EXL3 + Engram Q4 | **259.8 GB** | 101 | fits 3 × 96 GB; `SM120` in the name |
| `Mia-AiLab/…-EXL3-2.9bpw`, `…-3.0bpw`, `coolbho3k/…-EXL3-3bpw` | EXL3 | — | 1,767 / 159 / 56 | |
| `LibertAIDAI/DeepSeek-V4.1-Flash-REAP-256E` / `-272E` | expert-pruned FP8 | 210.9 / — GB | 287 / 107 | 384 → 256 experts |
| `antirez/deepseek-v4.1-flash-gguf` | GGUF | — | **496,692** | most-downloaded variant of any kind |
| `mlx-community/…-MLX-4bit`, `…-MLX-2bit`, `…-DSpark-drafter` | MLX | — | 155 / 109 / 90 | Apple silicon |
| `StellarVoyager/DeepSeek-V4.1-Flash-A100-A800-SM80` | — | **0 GB** | 0 | empty repo — the `sm_80` gap is unfilled |
| `0xTank/…-vLLM-4x-GB10-Recipe`, `bertholomus/…-DSpark-TP4-4xGB10-Recipe` | recipes | 0 GB | 0 | DGX Spark configs |

**Evals**: NVIDIA publishes evals only for its own repo (§10.1). None of the
community NVFP4/EXL3/GGUF repos in this list publishes an eval table.

---

## 10. Published benchmarks

### 10.1 Accuracy — NVIDIA, on vLLM (the only accuracy data for this checkpoint)

*"Accuracy results obtained with vLLM are shown below, in percent."*
[src](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md)

| Precision | GPQA Diamond | AA-LCR | SciCode | IFBench | MMMU-Pro | Terminal-Bench 2.1 |
|---|---|---|---|---|---|---|
| MXFP4 (source / base) | 91.035 | **78.563** | 54.401 | 76.667 | **74.046** | 81.60 |
| **NVFP4 (this repo)** | **91.288** | 78.438 | **55.843** | **77.267** | 73.699 | **82.16** |
| Δ | +0.253 | −0.125 | **+1.442** | +0.600 | −0.347 | +0.56 |

Settings: `temperature=1.0`, `top_p=0.95`, `reasoning_effort=100`,
`max_new_tokens=262144`; GPQA and AA-LCR 16 repeats, IFBench 5; Terminal-Bench
used a separate agentic configuration with 8 trajectories per task.

**Read this honestly.** NVFP4 wins 4 of 6 and loses 2, all within ±1.5 points on
benchmarks with 5–16 repeats. This is a **wash**, presented as a wash. The
defensible claim is "group-16 NVFP4 does not cost accuracy versus group-32
MXFP4" — not "NVFP4 is more accurate". The card is careful about this elsewhere
too: *"Lossless weight conversion does not imply identical inference outputs."*
Also note: **18 of 46,080 projection entries used fallback activation scales**
— 0.04 % of the calibrated scales failed and were defaulted.

The base card's own instruct numbers (GPQA Diamond 90.9, Terminal-Bench 2.1 90.6)
[src](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/README.md)
do not match NVIDIA's MXFP4 baseline (91.035 / 81.60), so the two eval harnesses
are not comparable. Use NVIDIA's table only for the **MXFP4-vs-NVFP4 delta**.

### 10.2 Throughput / latency

**NVIDIA publishes none for this checkpoint.** The card's only performance
statement is that vLLM produced the accuracy table. There is no tok/s, TTFT or
TPOT figure anywhere in the repo.

**MLPerf Inference**: DeepSeek-V4.1-Flash is **not** an MLPerf benchmark. The
v6.0 results contain DeepSeek-R1 and "Deepseek-S3" only (checked across the
v5.0/v5.1/v6.0 result tables). No MLPerf data exists. **⚠️ TO BE VERIFIED** —
[MLCommons' datacenter benchmark page](https://mlcommons.org/benchmarks/inference-datacenter/)
does not publish the model roster inline, so this negative could not be
re-confirmed against a primary source on 2026-09-19; it rests on the earlier
result-table sweep.

**InferenceX (formerly InferenceMAX, SemiAnalysis)** benchmarks
`DeepSeek V4.1 Flash 552B` across 9 accelerators
[src](https://inferencex.semianalysis.com/inference/deepseek-v41-flash). All 98
rows are `precision: "fp4"`, `benchmark_type: "agentic_traces"`,
`spec_method: "mtp"`, `offload_mode: "off"`, single-node, no disaggregation
(all four re-verified across all 98 rows, 2026-09-19; hardware set is exactly
`b200, b300, gb200, gb300, h100, h200, mi300x, mi325x, mi355x`).
Workload, **recomputed from the per-row `mean_input_tokens` /
`mean_output_tokens_actual` / `theoretical_cache_hit_rate` fields** rather than
from the landing page: **mean input 86K – 307K tokens (median ≈ 155K), mean
output 577 – 1,997 tokens (median ≈ 1,109), prefix cache hit rate 94.3 – 98.3 %
(median 97.6 %).** The previously stated "mean input ≈ 300K tokens" is the
*maximum* row, not the typical one — only the concurrency-1 rows sit near 300K,
because the harness shortens prompts as concurrency rises. Data pulled 2026-09-19 from
`https://inferencex.semianalysis.com/api/v1/benchmarks?model=DeepSeek-V4.1-Flash`.

> ⚠️ **TO BE VERIFIED — which checkpoint.** The API exposes `precision: "fp4"` but
> not the HF repo id. The B300 rows use image `vllm/vllm-openai:deepseekv41-flash-0909`,
> **the exact container NVIDIA's card names**, which is suggestive but not proof.
> The AMD rows almost certainly serve the base MXFP4 checkpoint, since CDNA 4 has
> no NVFP4 path. Treat the table below as **"DeepSeek-V4.1-Flash with 4-bit
> experts"**, not as a measurement of `nvidia/DeepSeek-V4.1-Flash-NVFP4`.

Selected rows (`tput/gpu` counts **all** tokens including cache-hit input;
`out/gpu` is output tokens only; `intvty` = tok/s/user; medians):

| HW | Engine | GPUs (TP) | conc | tput/GPU | out/GPU | intvty | TTFT | TPOT | Date |
|---|---|---|---|---|---|---|---|---|---|
| **B300** | vLLM | 2 (TP2) | 32 | 107,520 | 632 | 165.3 | 0.260 s | 6.0 ms | 09-16 |
| **B300** | vLLM | 2 (TP2) | 64 | **155,835** | **1,158** | 75.5 | 0.331 s | 13.2 ms | 09-16 |
| **B300** | vLLM | 2 (TP2) | 128 | 125,107 | 1,128 | 41.5 | 35.2 s | 24.1 ms | 09-16 |
| **B300** | vLLM | 4 (TP4) | 128 | 103,823 | 898 | 42.9 | 0.500 s | 23.3 ms | 09-16 |
| **GB300** | vLLM | 4 (TP4) | 1 | 6,573 | 43 | 473.9 | 0.335 s | 2.1 ms | 09-16 |
| **GB300** | vLLM | 4 (TP4) | 32 | 57,356 | 334 | 210.1 | 0.326 s | 4.8 ms | 09-16 |
| **GB300** | vLLM | 4 (TP4) | 128 | 102,318 | 887 | 39.8 | 0.452 s | 25.1 ms | 09-16 |
| **GB200** | vLLM | 4 (TP4) | 64 | 84,119 | 610 | 76.1 | 0.392 s | 13.2 ms | 09-15 |
| **B200** | vLLM | 4 (TP4) | 64 | 92,073 | 670 | 98.1 | 0.265 s | 10.2 ms | 09-15 |
| **B200** | SGLang | 4 (TP4, EP4) | 32 | 11,003 | 75 | 60.0 | 0.976 s | 16.7 ms | 09-18 |
| **H200** | vLLM | 8 (TP8) | 32 | 20,814 | 132 | 92.3 | 0.352 s | 10.8 ms | 09-16 |
| **H200** | vLLM | 8 (TP8) | 64 | 22,638 | 186 | 33.8 | 0.524 s | 29.6 ms | 09-16 |
| **H200** | SGLang | 8 (TP8, EP8) | 16 | 1,440 | 8 | 9.7 | 1.639 s | 102.8 ms | 09-18 |
| **H100** | vLLM | 8 (TP8) | 20 | 12,673 | 90 | 109.8 | 0.358 s | 9.1 ms | 09-18 |
| **H100** | vLLM | 8 (TP8) | 28 | 2,816 | 20 | 15.1 | 2.502 s | 66.1 ms | 09-18 |
| **MI355X** | vLLM (ROCm) | 4 (TP4) | 16 | 23,393 | 160 | 155.8 | 0.391 s | 6.4 ms | 09-13 |
| **MI355X** | vLLM (ROCm) | 4 (TP4) | 32 | 40,919 | 259 | 89.9 | 0.481 s | 11.1 ms | 09-13 |
| **MI325X** | vLLM (ROCm) | 8 (TP8) | 32 | 15,288 | 103 | 55.1 | 0.692 s | 18.2 ms | 09-18 |
| **MI300X** | vLLM (ROCm) | 8 (TP8) | 32 | 12,124 | 86 | 41.3 | 0.953 s | 24.2 ms | 09-18 |

What this says:

1. **B300 TP=2 is the best configuration measured** — 155,835 tok/s/GPU at
   concurrency 64, ~1.7× the per-GPU throughput of the same engine at TP=4. Two
   GPUs hold the whole model, so there is no tensor-parallel all-reduce in the
   critical path at all. This is the configuration NVIDIA's own card does *not*
   recommend.
2. **Blackwell vs Hopper is ~4–7×** at matched engine and concurrency
   (B200 TP4 conc 64: 92,073 vs H200 TP8 conc 64: 22,638 tok/s/GPU — and the
   H200 needs twice the GPUs). That gap is FP4 execution plus capacity.
3. **H100 falls off a cliff between concurrency 20 and 28** (12,673 → 2,816
   tok/s/GPU, TPOT 9.1 → 66.1 ms). §5.6 predicts exactly this: 16.7 GB of KV
   budget across 8 GPUs.
4. **SGLang is dramatically behind vLLM on these traces** — B200 SGLang peaks at
   11,003 tok/s/GPU vs vLLM's 92,073, and SGLang collapses past concurrency 32
   (TTFT 713 s at conc 128). SGLang's runs use EP = TP while vLLM's use EP=1;
   the SGLang container is a `dev-dsv41` branch build. **⚠️ TO BE VERIFIED** —
   this is very likely an untuned configuration rather than an engine property,
   but it is what is published. It does mean NVIDIA's *recommended* runtime
   (SGLang) is the one with no competitive published numbers.
5. **MI355X at TP=4 is genuinely competitive with B200** at low concurrency
   (23,393 vs B200's ~28,700 at conc 16) — running MXFP4 natively, which is the
   point of §8.4.
6. **TPOT ≤ 50 ms up to concurrency 128 holds only on B300 and GB300**, not on
   Blackwell generally. Median TPOT at conc 128: B300 TP2 **24.1 ms**, B300 TP4
   **23.3 ms**, GB300 TP4 **25.1 ms** — but GB200 vLLM **65.5 ms**, B200 vLLM
   **105.5 ms**, B200 SGLang **297.2 ms**, GB200 SGLang **419.6 ms**. (The
   earlier claim "met everywhere up to concurrency 128 on Blackwell" is wrong;
   recomputed from `median_tpot` across all 98 rows.) The *other* binding
   constraint is TTFT, which explodes once the KV pool saturates (B300 TP2 conc
   128: 35.2 s; B200 vLLM conc 128: 48.2 s; GB200 SGLang conc 128: 891.4 s —
   all three confirmed from `median_ttft`).

### 10.3 Vendor claims, labelled as such

* NVIDIA: *"Blackwell and Blackwell Ultra GPUs deliver up to 25× and 50× energy
  efficiency gains per token respectively"* vs H100
  [src](https://developer.nvidia.com/blog/introducing-nvfp4-for-efficient-and-accurate-low-precision-inference/)
  — **marketing**, not a DeepSeek-V4.1 measurement.
* NVIDIA: NVFP4 *"reduces the model memory footprint by approximately 3.5× relative
  to FP16, and approximately 1.8× compared to FP8"* (same source) — **marketing
  framing**; for this checkpoint the realised figure is 527.27 GB against a
  hypothetical all-FP8 787.1 GB = **1.49×**, because 38 % of the bytes are an FP8
  Engram table that was never converted.
* DeepSeek: *"approximately 4-fold and 437-fold reductions"* in KV cache
  [src](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/README.md)
  — **vendor claim**, though the underlying 890 B/token reproduces exactly (§5.1).

---

## 11. Vendor API pricing

Cost sanity anchor. All figures **$ per 1M tokens**.

### 11.1 DeepSeek first-party

| Model | Input (cache hit) | Input (cache miss) | Output |
|---|---|---|---|
| `deepseek-flash` | **$0.003 – $0.006** | **$0.15 – $0.30** | **$0.60 – $1.20** |
| `deepseek-v4-pro` | $0.022 – $0.044 | $0.66 – $1.32 | $1.98 – $3.96 |

*"Off-peak rates are half of the peak rates. Peak hours are 01:00 – 04:00 and
06:00 – 10:00 UTC, Monday through Friday, excluding Chinese public holidays."*
Lower figure = off-peak. [src](https://api-docs.deepseek.com/quick_start/pricing)
Re-fetched and confirmed verbatim 2026-09-19 (cache-miss input off-peak **$0.15**,
peak $0.30; output $0.60 / $1.20).

> Do not "correct" the $0.15 to the **$0.14** that
> [`cross-cutting/serving-optimizations.md` §1.5](../../cross-cutting/serving-optimizations.md)
> carries. That row is the *legacy* `deepseek-v4-flash` model name priced from
> `deepseek.ai/pricing`; DeepSeek's own API docs note that name is **retired and
> its requests are now served by DeepSeek-V4.1-Flash and billed at the Flash
> price**, which is the $0.15 above. Same endpoint, two published pages, two
> numbers.

Cached input is **2 % of uncached input** — a 50× discount, far steeper than the
~10 % METHODOLOGY §6 default. At the 97.5 % cache-hit rate of the InferenceX
agentic traces, blended input drops to **$0.0067 – $0.0134 /1M**.

### 11.2 Third-party providers (OpenRouter, 2026-09-19)

[src](https://openrouter.ai/api/v1/models/deepseek/deepseek-v4.1-flash/endpoints) —
22 endpoints, 1M context nearly everywhere. `quant` is the provider's own label.

| Provider | quant | Input | Cached input | Output |
|---|---|---|---|---|
| **DeepInfra** | fp8 | $0.14 | $0.0042 | **$0.42** ← cheapest output of all 22 |
| **Relace** | **fp4** | **$0.13** ← cheapest input | $0.0026 | $0.52 |
| Morph | **unknown** (not fp8) | $0.135 | $0.00405 | $0.54 |
| **DeepSeek** | unknown | $0.15 | $0.003 | $0.60 |
| Sail Research | **fp4** | $0.20 | $0.04 | $0.60 |
| Fireworks | unknown | $0.22 | $0.007 | $0.66 |
| Novita / GMICloud | fp8 | $0.285 | $0.0057 | $1.14 |
| Together / SiliconFlow / Parasail / BaseTen / Modal / Alibaba / DigitalOcean | mixed | $0.30 | $0.006 – $0.03 | $1.20 |
| Venice | fp8 | $0.375 | $0.0075 | $1.50 |

**Correction (2026-09-19): the previous claim "the two cheapest providers on
output are the two on FP4" is false.** Sorted by output price the order is
**DeepInfra $0.42 (`quant: fp8`)**, then Relace $0.52 (`fp4`), then Morph $0.54
(`quant: unknown`, not fp8 as previously recorded). The cheapest output price in
the whole endpoint list belongs to an FP8 provider, and the second FP4 provider
(Sail Research, $0.60) merely ties DeepSeek's own first-party rate. Re-fetched
from [the endpoints API](https://openrouter.ai/api/v1/models/deepseek/deepseek-v4.1-flash/endpoints)
(22 endpoints; 5 not shown above: Wafer, StreamLake, AtlasCloud, Makora, Phala).

What survives: Relace, the cheapest *input* price and one of the two FP4
endpoints, undercuts DeepSeek's own first-party output price by 13 %
($0.52 vs $0.60). That is suggestive but **not** the clean "FP4 is cheapest"
signal previously claimed — `quant` is a self-reported provider label, margins
and hardware are undisclosed, and the correlation does not hold on output price.

### 11.3 Self-hosting cross-check

Using §10.2's measured throughput and on-demand list prices taken **only** from
[`research/cross-cutting/cloud-pricing.md`](../../cross-cutting/cloud-pricing.md)
— never a neighbouring GPU's row. Recomputed with `python3`, METHODOLOGY §6.

| Config | GPUs | Output tok/s (total) | All tok/s (total) | $/GPU-h | Price source | $/1M output | $/1M all tokens |
|---|---|---|---|---|---|---|---|
| B200 vLLM TP4, conc 64 | 4 | 2,680 | 368,292 | **$8.60** | CoreWeave HGX B200, $68.80 per 8-GPU instance | $3.57 | **$0.026** |
| B300 vLLM TP2, conc 128 | 2 | 2,256 | 250,214 | **$15.00** | **OCI `BM.GPU.B300.8`** | $3.69 | $0.033 |
| GB300 vLLM TP4, conc 128 | 4 | 3,548 | 409,272 | **$18.00** | **OCI `BM.GPU.GB300.4`** | $5.64 | $0.049 |
| H200 vLLM TP8, conc 64 | 8 | 1,488 | 181,104 | $6.305 | CoreWeave HGX H200, $50.44/instance | $9.42 | $0.077 |
| H100 vLLM TP8, conc 20 | 8 | 720 | 101,384 | $6.155 | CoreWeave HGX H100, $49.24/instance | $19.00 | $0.135 |

*(Read the two throughput columns together: 2,680 tok/s of **output** inside
368,292 tok/s of **total** traffic on the B200 row — the agentic trace is
overwhelmingly input, which is why the `$/1M output` column looks so bad.)*

**The B300 and GB300 rows are no longer blank.** The previous version priced
them as "contact sales — `—`" because CoreWeave quotes them that way. That is a
CoreWeave fact, not a market fact: **OCI publishes public on-demand list prices
for B300 ($15.00/GPU-h), GB300 ($18.00/GPU-h) and MI355X ($8.60/GPU-h)**, and is
the only hyperscaler that does
([`cloud-pricing.md` §3.4](../../cross-cutting/cloud-pricing.md)). Two figures
other docs in this repo have got wrong: GB300 is **$18.00, not $7.40** (that is
Hyperstack's HGX B300 rate) and MI355X is **$8.60, not $3.45** (that is Crusoe's
MI300X rate). OCI is a hyperscaler and runs 2.2–2.4× neocloud rates for the same
silicon, so the B300/GB300 rows above are an upper bound, not a like-for-like
comparison with the CoreWeave rows beside them.

⚠️ **The B300 TP=2 row is `infeasible (KV)` at the pinned 268 GB/GPU** (§5.6):
2 × 268 GB cannot hold 527.27 GB of weights plus any KV at all. It is priced here
because it is a *measured* InferenceX row, but it is not a configuration to plan
on an HGX/DGX/p6-b300 SKU.

Other CoreWeave North America on-demand rows, re-fetched 2026-09-19: GB200 NVL72
$42.00 per 4-GPU instance = $10.50/GPU-h; RTX PRO 6000 Blackwell (High Memory)
$20.00 per 8-GPU instance = **$2.50/GPU-h** — the cheapest Blackwell-class
GPU-hour CoreWeave lists, and the one §8.4 cannot yet recommend for lack of a
proven `sm_120` path.

Read carefully: the `$/1M output` column looks terrible against the $0.60–1.20
API price because the agentic trace generates only ~600–2,000 output tokens
against 86K–307K input tokens (medians ≈1,100 out / ≈155K in — see the corrected
workload figures in §10.2) — output throughput is not what this workload
optimises. The
`$/1M all tokens` column at **$0.026 on B200** against a blended API price of
roughly $0.01–0.02/1M (97.5 % cache hit) says the public APIs are priced within
roughly 1–2× of on-demand self-hosting cost on this workload — which for a
frontier 552B model is remarkably tight, and consistent with everyone running
4-bit experts on Blackwell. A proper margin analysis belongs in
[`research/matrix/`](../../matrix/), not here.

---

## 12. Open questions (consolidated ⚠️ TO BE VERIFIED)

**Checkpoint / format**
1. Why the Engram tables (188.8 GiB, 38 % of the download) were left at FP8 when
   NVFP4 would save ≈96 GiB and drop the model under 2 × GB300. No NVIDIA
   statement found. Community repos (`LibertAIDAI` 429 GB, `lvkaokao MXFP4-Engram`
   412 GB) appear to have done it — with no published accuracy data.
2. `model.safetensors.index.json` still reports the base model's
   `total_size` (510,286,023,000). Is this a packaging bug, and does any loader
   size an allocation from it?
3. What exactly the 18 of 46,080 fallback activation scales were, and whether
   they sit on hot layers.

**Hardware**
4. Whether this checkpoint runs on **RTX PRO 6000 Blackwell (`sm_120`)** at all.
   FP4 tensor cores exist and FlashInfer lists SM 12.0/12.1, but SGLang branches
   around SM120 for `scaled_fp4_quant` and vLLM has no DSV4.1-specific SM120
   backend. No published run exists.
5. Whether **FlashMLA/FlashInfer ship an `sm_90` (Hopper) FP4-KV sparse kernel**
   for V4.1. The FP4 main KV format is architectural, not optional, so without
   it H100/H200 cannot serve this model at all — regardless of the Marlin W4A16
   weight fallback. The InferenceX H100/H200 rows prove *something* runs there;
   which checkpoint and which KV kernel is unconfirmed.
   *Partial answer found 2026-09-19:* vLLM's backend enum **does** carry a
   generic `FLASHINFER_MLA_SPARSE_SM90 = "…flashinfer_mla_sparse_sm90.
   FlashInferMLASparseSM90Backend"` alongside the SM120 one
   [src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/attention/backends/registry.py).
   So a Hopper sparse-MLA path exists in the enum — but it is *not* one of the
   three `…_DSV41` entries, so whether V4.1's FP4 KV layout resolves to it is
   still **⚠️ TO BE VERIFIED**.
6. Whether **A100 (`sm_80`)** can run it. Marlin fallback is nominally permitted
   at CC ≥ 8.0, but 207 GB of FP8 E4M3 tensors (Engram + attention) have no
   `sm_80` tensor-core path either. `StellarVoyager/DeepSeek-V4.1-Flash-A100-A800-SM80`
   is an **empty repo**.
7. ~~Exact B300/GB300 memory per GPU (270 vs 279 vs 288 GB).~~ **Settled by
   [METHODOLOGY §8](../../METHODOLOGY.md): HGX/DGX B300 and AWS p6-b300 are
   268 GB/GPU (2,144 GB/node, confirmed verbatim on
   [AWS's p6 page](https://aws.amazon.com/ec2/instance-types/p6/) 2026-09-19);
   GB300 NVL72 is 288 GB/GPU (≈279 usable).** 270 GB was CoreWeave's own reported
   slice and is not used anywhere in this document any more. What **remains**
   open is narrower: at 268 GB, **TP=2 is `infeasible (KV)`** (§5.6), yet the
   InferenceX B300 TP=2 row exists and its 32,580,148-token KV pool only closes
   against 288 GB physical. Which SKU that harness actually ran is unresolved,
   and the API does not expose it.

**Engines**
8. Whether any engine has validated **NVFP4 backbone experts + MXFP4 MTP experts
   in one process**. This is the highest-risk untested combination in the
   checkpoint.
9. Why NVIDIA's vLLM recipe sets `--no-enable-prefix-caching` — likely an
   interaction with SWA Bounded Replay, but unstated, and it forfeits the
   largest win available on the target workload.
10. Whether SGLang's poor InferenceX numbers are configuration (EP=TP, dev
    branch, default flags) or an engine limitation. NVIDIA's primary recommended
    runtime currently has no competitive published throughput.
11. TensorRT-LLM V4.1 support: absent from the matrix, no announced timeline.
12. NVIDIA Dynamo: no recipe found; disaggregated prefill/decode would suit the
    CED architecture unusually well (prefill activates 8 B, decode 16 B).
    *Partial answer 2026-09-19:* **TokenSpeed already runs exactly this** — a
    per-commit GB300 Slurm **1P1D** (TP4 prefill + TP4 decode) CI job for
    DeepSeek-V4.1-Flash [src](https://lightseek.org/tokenspeed/recipes/models).
    It targets the **base MXFP4 repo**, so the open question narrows to whether
    a disaggregated recipe exists for the NVFP4 build, and to whether Dynamo
    ever ships one.
13. Whether production kernels bound the four **full-pool indexers** the way the
    reference implementation does not — at 1M context they cost 21.5 GFLOP/token,
    more than the entire encoder forward pass.

**Measurement**
14. Which HF checkpoint each InferenceX row actually served (API does not expose it).
15. MTP/DSpark **acceptance rate** and effective tokens per step. Every
    InferenceX row has `spec_method: "mtp"` but the rate is not exposed, and
    NVIDIA did not exercise speculation at all.
16. Why engine KV pools on the large configs sit far below available memory
    (GB300 ×4: 68.4 M tokens ≈ 61 GB against a **493.5 GB** budget).
17. Engram `NgramHashState` per-sequence size.
18. Video / per-frame vision cost — no video pipeline is documented.

---

## Sources

**Checkpoint and base model**
- https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4
- https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md
- https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4
- https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4/tree/main?recursive=true
- https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/blob/main/config.json
- https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/hf_quant_config.json
- https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/resolve/main/model.safetensors.index.json
- https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/inference/README.md
- https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/inference/config.json
- https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/inference/model.py
- https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/inference/kernel.py
- https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/inference/run.sh
- https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/resolve/main/model-{00001..00048}-of-00048.safetensors (HTTP range reads of the safetensors headers — the measurement basis for §1.1, §3.3, §4)
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/README.md
- https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/blob/main/DeepSeek_V41_Tech_Report.pdf (referenced by the base card; not fetched)
- https://huggingface.co/api/models?search=DeepSeek-V4.1-Flash
- https://github.com/deepseek-ai/deepseek-recipe (referenced by the base card)

**Quantisation tooling**
- https://github.com/NVIDIA/Model-Optimizer
- https://developer.nvidia.com/blog/introducing-nvfp4-for-efficient-and-accurate-low-precision-inference/

**Engines**
- https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/registry.py
- https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/attention/backends/registry.py
- https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/models/deepseek_v41/amd/rocm.py
- https://github.com/vllm-project/vllm/tree/main/vllm/models/deepseek_v41
- https://docs.vllm.ai/en/latest/models/supported_models.html
- https://docs.vllm.ai/en/latest/features/quantization/modelopt.html
- https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/models/deepseek_v4.py
- https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/layers/quantization/modelopt_quant.py
- https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/layers/moe/moe_runner/flashinfer_trtllm.py
- https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/layers/moe/utils.py
- https://github.com/sgl-project/sglang/tree/main/python/sglang/srt/models
- https://raw.githubusercontent.com/sgl-project/sglang/da64c5cbb8cf6bfd39be19da43573fdfd484c43a/python/sglang/srt/configs/deepseek_v41.py
- https://nvidia.github.io/TensorRT-LLM/models/supported-models.html
- https://github.com/flashinfer-ai/flashinfer

**Benchmarks**
- https://inferencex.semianalysis.com/inference/deepseek-v41-flash
- https://inferencex.semianalysis.com/api/v1/benchmarks?model=DeepSeek-V4.1-Flash
- https://inferencex.semianalysis.com/api
- https://github.com/SemiAnalysisAI/InferenceX (benchmark harness referenced by the run URLs)
- MLPerf Inference v5.0 / v5.1 / v6.0 result tables (checked for DeepSeek V4.1 — absent)

**Pricing**
- https://api-docs.deepseek.com/quick_start/pricing
- https://openrouter.ai/api/v1/models/deepseek/deepseek-v4.1-flash/endpoints
- https://www.coreweave.com/pricing
- https://apexapps.oracle.com/pls/apex/cetools/api/v1/products/?limit=2000&currencyCode=USD (OCI public price list — B300 $15.00, GB300 $18.00, MI355X $8.60 per GPU-hour, via cloud-pricing.md §3.4)

**Hardware (sweep 2026-09-19)**
- https://aws.amazon.com/ec2/instance-types/p6/ (`p6-b300.48xlarge` = 2,144 GB HBM3e / 8 GPUs = 268 GB per GPU)
- https://www.nvidia.com/en-us/data-center/dgx-b300/
- https://lenovopress.lenovo.com/lp2263.pdf (RTX PRO 6000 Blackwell **Server Edition**, 1,597 GB/s)
- https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000/ (Workstation Edition, 1,792 GB/s — *not* the rack part)
- https://lightseek.org/tokenspeed/recipes/models (TokenSpeed DeepSeek-V4.1-Flash recipe, GB300 1P1D CI)

**Repo-local cross-references**
- [`research/METHODOLOGY.md`](../../METHODOLOGY.md)
- [`research/models/deepseek41f/architecture.md`](../deepseek41f/architecture.md) (base checkpoint)
- [`research/gpus/gb300.md`](../../gpus/gb300.md), [`research/gpus/b200.md`](../../gpus/b200.md), [`research/gpus/b300.md`](../../gpus/b300.md), [`research/gpus/rtx6000-pro.md`](../../gpus/rtx6000-pro.md), [`research/gpus/mi355x.md`](../../gpus/mi355x.md)
- [`research/cross-cutting/cloud-pricing.md`](../../cross-cutting/cloud-pricing.md) (all GPU-hour prices in §11.3)
- [`research/cross-cutting/inference-engines.md`](../../cross-cutting/inference-engines.md) (engine versions, TokenSpeed recipe)
- [`research/cross-cutting/serving-optimizations.md`](../../cross-cutting/serving-optimizations.md) §1.5 (cached-input ratios)

---

## Verification log (2026-09-19)

Adversarial re-check of the 24 most consequential claims. Every source below was
opened independently — the document's own citation was never taken on trust, and
every derivation was recomputed with `python3` from the local `config.json` and
METHODOLOGY.md. Verdicts: **CONFIRMED** = primary source or recomputation agrees
exactly; **CORRECTED** = old value → new value, fixed in place above;
**UNVERIFIABLE** = no primary source reachable, now carries ⚠️ TO BE VERIFIED.

### Checkpoint identity and size

1. **CONFIRMED** — `safetensors.total = 763,205,315,794`; `usedStorage =
   527,309,220,165`; `createdAt 2026-09-16T20:37:42Z`; `sha 3431dde3…`;
   `gated:false`; 550 downloads / 65 likes; `library_name: Model Optimizer`;
   `pipeline_tag: image-text-to-text`. All exact.
   https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4
2. **CONFIRMED** — 84 files, 48 shards, all-file total **527,315,885,158 B**
   exact; shards 47–48 are 101,537,926,600 and 101,535,150,904 B.
   https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4/tree/main?recursive=true
3. **CORRECTED** (§1.1) — "weights only (48 shards) 527,273,322,840 B" → the 48
   shard **files** sum to **527,293,384,576 B**; 527,273,322,840 B is the tensor
   **payload** (the ~20.1 MB difference is safetensors JSON headers). Label
   fixed; every byte table still uses the payload figure, which is correct.
   Same tree API as above.
4. **CONFIRMED** — `model.safetensors.index.json` in the NVFP4 repo still
   reports `metadata.total_size = 510,286,023,000`, byte-identical to the base
   repo's. Fetched both; the packaging bug is real.
   https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/resolve/main/model.safetensors.index.json
   and https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/resolve/main/model.safetensors.index.json
5. **CONFIRMED** — card text verbatim: "All 16,986,931,200 weight blocks passed
   lossless conversion"; "18 of 46,080 projection entries used fallback scales";
   "from approximately 476 GiB to 492 GiB"; "The export contains 48 safetensors
   shards"; "nvidia-modelopt **v0.47.0rc0**"; calibration "512 samples per
   dataset (1,024 total), sequence length 512, batch size 4, and selection seed
   0"; "Supported Hardware Microarchitecture Compatibility: NVIDIA Blackwell";
   "Test Hardware: NVIDIA GB300 (Blackwell)".
   https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md
6. **CONFIRMED (with new provenance)** — the card's "Source revision:
   `dba1be0a40aa45a94ad051997016db3960a90277`" is the **base repo's commit sha**,
   not a ModelOpt revision. Noted in §1.
   https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash

### Quantisation scheme

7. **CONFIRMED** — `hf_quant_config.json`: `quant_algo: MIXED_PRECISION`,
   `kv_cache_quant_algo: null`, `group_size: 16`, `exclude_modules:
   ["*.attn.*","*.ffn.shared_experts.*","head","mtp.*"]`, **40** entries in
   `quantized_layers`, all `NVFP4`/`gs16`.
   https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/hf_quant_config.json
8. **CORRECTED** (§1.2) — the quoted `quantization_config` blob omitted two keys
   that are in the shipped `config.json`: `"activation_scheme": "dynamic"` and
   `"producer": {"name":"modelopt","version":"dsv4-nvfp4-experts"}`. The first
   **contradicts** the document's inference from `input_activations.dynamic =
   false`; both now shown, and the contradiction marked ⚠️ TO BE VERIFIED. The
   W4A4 conclusion itself is independently **CONFIRMED** by the card's own
   "NVFP4 weights and activations (W4A4)".
   Local `research/models/deepseek41fnvfp4/config.json` + card §"Post Training Quantization".
9. **CORRECTED** (§3.4) — "15,360 expert-projections × 2 × 4 B = 368,640 B" →
   **46,080 expert projections** (40 layers × 384 experts × {w1,w2,w3}).
   15,360 × 2 × 4 = 122,880 ≠ 368,640; 46,080 × 2 × 4 = 368,640. The card's own
   "46,080 projection entries" and "w1, w2, and w3 for 384 experts across 40
   layers" settle it. The measured delta 16,987,299,840 = 16,986,931,200 +
   368,640 is **CONFIRMED** unchanged.
   https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md
10. **CORRECTED** (§3.4) — "+3.2 % checkpoint size" → **+3.33 %**
    (16,987,299,840 / 510,286,023,000). Recomputed.
11. **CONFIRMED** — NVIDIA's NVFP4 blog, verbatim: "applies a fine-grained E4M3
    scaling factor to each 16-value micro-block" with "a second-level FP32
    scalar applied per tensor"; "twice as many opportunities to match the local
    dynamic range of the data"; "reduces the model memory footprint by
    approximately 3.5x relative to FP16, and approximately 1.8x compared to
    FP8"; "Blackwell and Blackwell Ultra GPUs deliver up to 25x and 50x energy
    efficiency gains per token respectively over an NVIDIA H100 Tensor Core
    baseline". §10.3's "marketing, not a DeepSeek-V4.1 measurement" framing
    stands.
    https://developer.nvidia.com/blog/introducing-nvfp4-for-efficient-and-accurate-low-precision-inference/

### Parameter and byte arithmetic (all recomputed with python3)

12. **CONFIRMED** — §3.1's whole derivation reproduces exactly: `expert =
    35,389,440`; `attn = 126,615,552`; `per_layer_total = 13,754,499,072`;
    `per_layer_active = 377,290,752`; 40 layers = 550,179,962,880; backbone
    total 551,818,362,880; Engram tables 196,613,849,600; MTP 14,225,315,072.
    The grand total 763,205,315,794 matches HF's `safetensors.total` exactly.
    Per-dtype cross-check also exact: `U8` 543,581,798,400 = the NVFP4 backbone
    experts, `I8` 13,589,544,960 = the MTP/DSpark experts left MXFP4.
    Local `config.json` + HF API.
13. **CORRECTED** (§4) — bytes/param "0.638 avg" → **0.691**
    (527,273,322,840 / 763,205,315,794 = 0.6909) and "0.619 avg" → **0.669**
    (510,286,023,000 / 763,205,315,794 = 0.6686). Both were ~8 % low. The GB/GiB
    columns they sit beside were already right, so only the ratio was wrong.
14. **CORRECTED** (§3.3) — "36 % of its bytes are an FP8 Engram table" →
    **38.5 %** (202,758,032,400 / 527,273,322,840 = 38.45 %). This also removes
    an internal contradiction: §4.1 and §10.3 already said 38 %.
15. **CORRECTED** (§4.1) — Engram requantisation savings recomputed: NVFP4
    saving "≈ 96 GiB" → **85.83 GiB (92.16 GB)**; MXFP4 saving "≈ 99 GiB" →
    **91.56 GiB (98.31 GB)**; resulting checkpoint "≈ 395 GiB" → **399.5 GiB
    (MXFP4) / 405.2 GiB (NVFP4)**. The qualitative conclusion — under 2 × GB300,
    inside 4 × B200 with ~200 GB KV headroom — survives.
16. **CONFIRMED** — KV arithmetic. 288 B per compressed KV position
    (512/2 + 512/16) and 68 B per indexer K position (128/2 + 128/32) give
    3 × 144 + 288 = 720 and 3 × 34 + 68 = 170 → **890 B/token**, matching the
    base card's "890 bytes per token" exactly. The base card also independently
    confirms the format: "FP4 main KV caching (E2M1 format, one E4M3 scale per
    16 channels)". §5.4 totals, §5.2 alternate precisions, §5.3's 2,703,360 B
    SWA ring and §5.6's whole fit table all reproduce to the last digit.
    https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/README.md
17. **CONFIRMED** — "8B activated during prefill and 16B during decode" is
    verbatim in the base card, and the CED and SWA-Bounded-Replay quotes are
    accurate. §6.1–§6.3 recomputed: 15.09 / 31.51 GFLOP per token, the
    distinct-expert curve at batch 1/8/32/64/128/256 (6.0 / 45.5 / 152.0 /
    243.8 / 332.8 / 377.2), all six bytes-per-step rows, and every roofline
    decode-step time. Same source.
18. **CORRECTED** (§6.4) — "a full-resolution image costs roughly as much as
    2,300 text tokens of prefill, **plus the ViT**" double-counts. 18.8 TFLOP
    (ViT) + 15.5 TFLOP (1,024 image tokens into the LM) = 34.3 TFLOP ÷ 15.09
    GFLOP/token = **≈ 2,270 text tokens, ViT included**. The component FLOP
    figures themselves are correct.

### Hardware and engine support

19. **CONFIRMED** — SGLang's platform guards are quoted verbatim and are still
    in `main`: `modelopt_quant.py:1867-1870` "ModelOpt NVFP4 native dense GEMM
    backends require SM100+. Use --fp4-gemm-backend marlin on SM80-SM90." and
    `:2310-2317` `use_marlin_fallback = (8, 0) <= capability < (10, 0)` …
    "Blackwell and above, or use moe_runner_backend=marlin on SM80+".
    https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/layers/quantization/modelopt_quant.py
20. **CONFIRMED** — vLLM `main` registry has
    `"DeepseekV41ForCausalLM": ("vllm.models.deepseek_v41", "DeepseekV41ForCausalLM")`
    and `"DSparkV41DraftModel": ("vllm.models.deepseek_v41",
    "DSparkDeepseekV4ForCausalLM")`; the attention enum has exactly the three
    `…_DSV41` entries quoted, with the quoted comment, plus
    `ROCM_FLASHMLA_SPARSE_DSV4` and **no** `…_DSV41` ROCm entry;
    `FLASHINFER_MLA_SPARSE_SM120` exists. `vllm/models/deepseek_v41/amd/rocm.py`
    defines `DeepseekV41ROCMAiterMLAAttention` and
    `DeepseekV41ROCMAiterSparseSWABackend` over AITER block-scaled FP8 GEMMs.
    SGLang `deepseek_v4.py` carries `is_dsv41` (9 sites) and imports
    `DeepseekV41Compressor` / `DeepseekV41Indexer` from `dsv4.dsv41_sparse`.
    https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/model_executor/models/registry.py ·
    https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/v1/attention/backends/registry.py ·
    https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/models/deepseek_v41/amd/rocm.py ·
    https://raw.githubusercontent.com/sgl-project/sglang/main/python/sglang/srt/models/deepseek_v4.py
21. **CONFIRMED** — TensorRT-LLM's supported-models matrix lists
    `DeepseekV3ForCausalLM`, `DeepseekV32ForCausalLM`, `DeepseekV4ForCausalLM`
    and **no V4.1 row**; vLLM's published supported-models page likewise stops
    at `DeepseekV4ForCausalLM`, so the "read the registry, not the docs"
    documentation-lag point holds. vLLM's ModelOpt page states verbatim: "On
    GPUs without a supported native FP4 GEMM kernel, vLLM falls back to
    weight-only (W4A16) execution via Marlin and logs a warning".
    https://nvidia.github.io/TensorRT-LLM/models/supported-models.html ·
    https://docs.vllm.ai/en/latest/models/supported_models.html ·
    https://docs.vllm.ai/en/latest/features/quantization/modelopt.html
22. **CORRECTED** (§8.4) — the RTX PRO 6000 row cited the **NVIDIA model card**
    for "96 GB GDDR7, 1.792 TB/s". That card contains **no** RTX PRO 6000
    mention, no GDDR7 and no bandwidth figure of any kind — the citation was
    misattributed. Replaced with NVIDIA's own product page (**Workstation
    Edition: "96 GB GDDR7 with error-correcting code (ECC)", "1792 GB/sec"**)
    and flagged that the rack-relevant **Server Edition is 1,597 GB/s**
    (Lenovo SKU sheet, per this repo's `research/gpus/rtx6000-pro.md`) — an
    11 % bandwidth gap on a decode-bound workload. FlashInfer's SM 12.0/12.1
    listing is **CONFIRMED**, but its example GPUs are "RTX 50 series, DGX
    Spark" and it warns "Not all features are supported across all compute
    capabilities", so §8.4's ⚠️ verdict stands.
    https://www.nvidia.com/en-us/products/workstations/professional-desktop-gpus/rtx-pro-6000/ ·
    https://lenovopress.lenovo.com/lp2263.pdf ·
    https://github.com/flashinfer-ai/flashinfer
23. **CORRECTED → ⚠️ TO BE VERIFIED** (§5.6) — the B300 row's **270 GB** matches
    no primary source. NVIDIA's DGX B300 page gives 2.1 TB / 8 = 262.5 GB, AWS
    P6-B300 gives 2,144 GB / 8 = 268 GB, and the physical stack capacity is
    288 GB. At 262.5 GB the 4-GPU KV budget is 401.7 GB, not 428.7 GB. Marker
    and a caveat block added; this repo's own `research/gpus/b300.md` already
    flagged it, so the two documents now agree.
    https://www.nvidia.com/en-us/data-center/dgx-b300/ ·
    https://aws.amazon.com/ec2/instance-types/p6/
24. **CONFIRMED** — MI355X: 288 GB HBM3E at 8 TB/s, MXFP4 at **10.1 PFLOPS
    dense**, and **CDNA 4 implements MXFP4 but not NVFP4** (NVFP4 checkpoints
    run only by requantisation to MXFP4 at load). §8.4's "use the base
    checkpoint on AMD" verdict is sound.
    https://www.amd.com/en/products/accelerators/instinct/mi350/mi355x.html ·
    https://rocm.blogs.amd.com/software-tools-optimization/nvfp4-mi355/README.html
    (via `research/gpus/mi355x.md`)

### Benchmarks, variants and pricing

25. **CONFIRMED** — the InferenceX API returns exactly **98 rows**, all
    `precision: "fp4"`, `spec_method: "mtp"`, `benchmark_type:
    "agentic_traces"`, `offload_mode: "off"`, `disagg: false`,
    `is_multinode: false`, across exactly 9 accelerators. **Every one of the 19
    rows in §10.2's table matches the API to the digit** (B300 TP2 conc 64:
    155,834 tok/s/GPU, 1,158.1 out/GPU, 75.5 intvty, 0.331 s TTFT, 13.2 ms
    TPOT; GB300 TP4 conc 128: 102,318 / 887.1 / 39.8 / 0.452 / 25.1; H100 conc
    20 → 28: 12,673 → 2,816 and 9.1 → 66.1 ms; etc.), as do all the dates. The
    B300 rows do use image `vllm/vllm-openai:deepseekv41-flash-0909` — exactly
    the container NVIDIA's card names — while GB300/B200/GB200/H100/H200 use
    plain vLLM nightlies and every SGLang row uses `lmsysorg/sglang:dev-dsv41`,
    so §10.2's ⚠️ "which checkpoint" caveat is well placed. §5.6's B300 TP=2 KV
    pool of **32,580,148 tokens** and §12.16's GB300 ×4 pool of **68,443,500**
    are both exact. The "~1.7× TP2 over TP4" claim checks out at 155,834 /
    92,599 = **1.68×**, and "MI355X 23,393 vs B200 ~28,700 at conc 16" is exact.
    https://inferencex.semianalysis.com/api/v1/benchmarks?model=DeepSeek-V4.1-Flash
26. **CORRECTED** (§10.2) — workload characterisation "mean input ≈ 300K
    tokens" → **86K–307K, median ≈ 155K** (300K is the maximum row, hit only at
    concurrency 1); "mean output ≈ 1–2K" → **577–1,997, median ≈ 1,109**;
    "95–98 % prefix cache hit rate" → **94.3–98.3 %, median 97.6 %**
    (`theoretical_cache_hit_rate`). §11.3's prose updated to match. Same API.
27. **CORRECTED** (§10.2, conclusion 6) — "TPOT ≤ 50 ms is met everywhere up to
    concurrency 128 on Blackwell" is **false**. At conc 128 median TPOT is
    24.1 ms (B300 TP2), 23.3 (B300 TP4) and 25.1 (GB300) — but **65.5 ms
    (GB200 vLLM)**, **105.5 ms (B200 vLLM)**, 297.2 (B200 SGLang) and 419.6
    (GB200 SGLang). Restricted to B300/GB300. The three TTFT figures quoted in
    the same bullet (35.2 s / 48.2 s / 891.4 s) are **CONFIRMED** exact. Same API.
28. **CONFIRMED** — every accuracy number in §10.1 is verbatim from the card
    (MXFP4 91.035 / 78.563 / 54.401 / 76.667 / 74.046 / 81.60; NVFP4 91.288 /
    78.438 / 55.843 / 77.267 / 73.699 / 82.16), as are the eval settings
    (`temperature=1.0`, `top_p=0.95`, `reasoning_effort=100`,
    `max_new_tokens=262144`, 16 / 16 / 5 repeats, 8 trajectories) and the
    DSpark sentence "DSpark tensors are preserved, but speculative decoding was
    not exercised in the reported validation." Both launch recipes, both
    container tags, the SGLang commit `da64c5c…`, `flashinfer_trtllm_routed`,
    `--max-running-requests 16`, `--max-num-seqs 32`,
    `--no-enable-prefix-caching`, `--language-model-only` and the
    `enable_multithread_load` loader config are all verbatim.
    https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md
29. **CONFIRMED** — §9's variant table: every repo exists and every
    `usedStorage` matches on a per-repo re-fetch — s-zaizen / AtomicChat /
    Solstice-AI all 527.3 GB, LibertAIDAI 429.4 GB, msuiche 414.9 GB, RedHatAI
    510.3 GB, lvkaokao MXFP4-Engram 412.0 GB and W4A16-Engram 451.7 GB, diffbot
    EXL3 358.1 GB, satgeze 259.8 GB, REAP-256E 210.9 GB, StellarVoyager
    **0 bytes** (the `sm_80` gap really is an empty repo). Download counts match
    too (antirez GGUF 496,692; base 429,865).
    **CORRECTED**: "101 repos matched" → **129** on a `limit=200` re-query.
    https://huggingface.co/api/models?search=DeepSeek-V4.1-Flash
30. **CONFIRMED** — DeepSeek first-party pricing exactly as tabulated
    (`deepseek-flash` $0.003/$0.006 cache-hit, $0.15/$0.30 cache-miss,
    $0.60/$1.20 output; `deepseek-v4-pro` $0.022/$0.044, $0.66/$1.32,
    $1.98/$3.96) and the off-peak sentence is verbatim, including "Peak hours
    are 01:00 - 04:00 and 06:00 - 10:00 UTC". OpenRouter's own DeepSeek endpoint
    carries `overrides` blocks that independently reproduce those exact peak
    windows. The 2 %-of-uncached cached-input ratio and the 97.5 %-hit blended
    $0.0067–$0.0134 are recomputed correct.
    https://api-docs.deepseek.com/quick_start/pricing
31. **CORRECTED** (§11.2) — "**The two cheapest providers on output are the two
    on FP4**" is **false**. Sorted by output price: DeepInfra **$0.42**
    (`quant: fp8`), Relace $0.52 (`fp4`), Morph $0.54. The cheapest output
    endpoint is FP8. Also **CORRECTED**: Morph's `quant` is `unknown`, not
    `fp8` (its $0.135 / $0.00405 / $0.54 prices were right). All 22 endpoints
    and every other price in the table verified.
    https://openrouter.ai/api/v1/models/deepseek/deepseek-v4.1-flash/endpoints
32. **CONFIRMED / refined** (§11.3) — CoreWeave on-demand list: HGX B200
    $68.80 per 8-GPU instance = **$8.60/GPU-h** (exact); HGX H200 $50.44 =
    **$6.31** (doc used $6.30 — a $0.01 rounding, shifts $/1M output 9.40 →
    9.41); HGX H100 $49.24 = **$6.16** (exact); GB300 NVL72 and HGX B300 both
    "contact sales" as stated. Every `$/1M` figure recomputed against
    METHODOLOGY §6 and correct. Newly noted: RTX PRO 6000 Blackwell (High
    Memory) is listed at $20.00 per 8-GPU instance = **$2.50/GPU-h**.
    https://www.coreweave.com/pricing
33. **UNVERIFIABLE** (§10.2) — "DeepSeek-V4.1-Flash is not an MLPerf benchmark".
    MLCommons' datacenter results page does not publish the model roster inline
    and points to an external rules repo, so the negative could not be
    re-confirmed from a primary source. ⚠️ marker added.
    https://mlcommons.org/benchmarks/inference-datacenter/

**Claims checked: 33. Confirmed: 18. Corrected: 14. Unverifiable: 1.** No claim
in this document was found to be invented — every figure traced to a real
source or a reproducible computation. The corrections are arithmetic slips
(§3.3, §3.4, §4, §4.1, §6.4), one misattributed citation (§8.4), one
over-generalised benchmark conclusion (§10.2 #6), one over-stated market signal
(§11.2), and workload/capacity figures quoted at their extremes rather than
their medians (§10.2, §5.6).

---

## Sweep log (2026-09-19)

Systemic sweep against the amended [`research/METHODOLOGY.md`](../../METHODOLOGY.md)
(§1 bytes-per-param and units, §2 state-slot multiplier `S`, §3 consistency rule,
§6 standard scenarios, §8 pinned GPU/model/price inputs). Format: **section ·
old → new · reason · source**. Nothing from the earlier Verification log was
undone; entries 23 and 32 there are superseded by rows 4 and 12 below and are
left in place as the record of that pass.

| # | Section | Old → New | Reason | Source |
|---|---|---|---|---|
| 1 | **§0 (new)** | *(absent)* → "Bottom line up front": 527.27 GB is **larger** than the 510.29 GB base; only **58.0 %** of bytes are NVFP4; **no published speedup** over the base; the case for this build is accuracy-neutrality, not memory or speed | Decision-relevant for the whole repo and previously buried in §3.4/§4. NVIDIA's card re-fetched 2026-09-19 contains **zero** occurrences of "speedup", "throughput", "tokens per second" or "performance" | [NVFP4 card](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md), METHODOLOGY §8 |
| 2 | §1 Identity | Downloads 550 → **1,969** | HF `downloads` is a rolling 30-day counter; re-pulled same day. Noted inline so it is not read as a fixed figure | [HF API](https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4) |
| 3 | §4 | *(no framing)* → explicit note that the first two rows of the dtype table mean requantisation made the checkpoint **16.99 GB bigger**, with a pointer to the smaller base and community Engram-quantised builds | Same decision-relevance as §0; the table stated the fact without drawing it | measured; §3.4 |
| 4 | **§5.6 fit table** | B300 **270 GB → 268 GB** (2,144 GB per 8-GPU node); GB300 **279 → 288 GB** (≈279 usable, stated inline); B300 KV budget 428.7 → **421.5 GB**; GB300 461.1 → **493.5 GB**; GB300 ×8 1,449.5 → **1,514.3 GB**; concurrencies recomputed throughout; **new B300 ×8 row** (1,370.3 GB) | METHODOLOGY §8 pins as-deployed capacities and forbids merging HGX B300 with GB300 NVL72. 270 GB was CoreWeave's reported slice and matched no primary source | METHODOLOGY §8, [AWS p6](https://aws.amazon.com/ec2/instance-types/p6/) ("2,144 HBM3e", re-fetched and confirmed verbatim), [DGX B300](https://www.nvidia.com/en-us/data-center/dgx-b300/) |
| 5 | §5.6 | "±10 % band" caveat → **`infeasible (KV)` verdict for TP=2 at 268 GB**, with the InferenceX TP=2 row flagged as only closing against 288 GB physical | METHODOLOGY §3 consistency rule: a configuration whose KV budget is negative is printed as infeasible, not as a band | METHODOLOGY §3, [InferenceX](https://inferencex.semianalysis.com/inference/deepseek-v41-flash) |
| 6 | §5.6 | *(capacities only)* → GiB shown beside every GB budget; formula and byte-level arithmetic stated | METHODOLOGY §1 units convention | METHODOLOGY §1 |
| 7 | §4.2 | "2 GPUs → GB300 (279 GB) only at ~95 % occupancy" → **`infeasible (KV)` on every part**, with the 91.5 % / 98.4 % occupancy figures spelled out | Same as row 5; 263.6 GB/GPU leaves no activation workspace under the 0.90 rule | METHODOLOGY §3 |
| 8 | **§5.3** | *(no `S`)* → explicit `S = 1` assumption for the SWA ring, marked ⚠️ TO BE VERIFIED, with the MTP-block caveat and the note that it moves `max_concurrency` by <1 % | METHODOLOGY §2 requires the engine's state-slot count to be stated or marked | METHODOLOGY §2 |
| 9 | **§6.3** | *(no KV term at all)* → `kv_read(batch, ctx) = batch × (ctx × 170 + 13,058,048) B` derived from the three caches, plus **+KV @32K / +KV @128K / Total @128K** columns | METHODOLOGY §3: decode KV-read bytes must scale with `batch × ctx × kv_bytes_per_token`; a flat (here, zero) per-step term is the bug the fact-check found twice elsewhere | METHODOLOGY §3; `inference/model.py`, `inference/kernel.py`; §5.1–5.2 |
| 10 | §6.3 | Engram `wkv` **309.38 MiB → 300.45 MiB**; step totals 12.50 → **12.49**, 41.76 → 41.75, 120.78 → **120.77**, 188.88 → 188.87, 254.88 → **254.87** GiB | 315,043,840 B / 2^20 = 300.45, not 309.38; the §3.3 measured row was right and §6.3 was not | measured (§3.3), recomputed `python3` |
| 11 | **§6.3 roofline** | batch 32 5.79 → **5.84** ms, batch 128 12.22 → **12.42** ms (and per-config equivalents); **new 4 × B300 HGX row**; **new 8 × RTX PRO 6000 Server Edition row at 1,597 GB/s** (1.50 / 14.63 / 31.11 ms); context stated as 128K | KV traffic now included; and the fact-checker found 1,792 GB/s attached to a citation that does not contain it. **1,792 is the Workstation Edition**; the rack part is the **Server Edition at 1,597 GB/s** | METHODOLOGY §8, [Lenovo lp2263](https://lenovopress.lenovo.com/lp2263.pdf) (re-fetched 2026-09-19: contains "1597" and "GDDR7", no 1,792) |
| 12 | §6.3 | *(no feasibility statement)* → every batch row confirmed ≤ `max_concurrency(128K)`; binding case named (877 on 4 × B200) | METHODOLOGY §3 consistency rule | §5.6 |
| 13 | **§8.1** | *(TokenSpeed absent)* → new row: TokenSpeed **does** ship a DeepSeek-V4.1-Flash recipe (serve command, FlatKV backend, per-commit GB300 Slurm 1P1D CI), qualified as targeting the **base MXFP4 repo** | Omitting it made the "engine support matrix, status as of 2026-09-19" read as a denial | [TokenSpeed recipes](https://lightseek.org/tokenspeed/recipes/models), [`cross-cutting/inference-engines.md` §2.5](../../cross-cutting/inference-engines.md) |
| 14 | **§8.6** | "No published multi-node, disaggregated-prefill, wide-EP or expert-parallel configuration for this checkpoint" → scoped to **this NVFP4 checkpoint**, with TokenSpeed's GB300 1P1D TP4+TP4 and TP8/EP configurations named for the base | The unqualified claim is false for the base repo | same as row 13 |
| 15 | **§8.4** | Merged "GB300 / B300 `sm_103`" row → **two rows**: GB300 NVL72 (288 GB, FP4 15,000 dense) and B300 HGX/DGX/p6-b300 (268 GB, FP4 **13,500** dense, TP=2 infeasible) | METHODOLOGY §8 forbids merging the two parts; their capacity *and* dense FP4 peak differ | METHODOLOGY §8 |
| 16 | §8.4 | RTX PRO 6000 row reordered to lead with **Server Edition 1,597 GB/s**, ≈2,000 TFLOPS **dense** FP4 (4,000 noted as sparse); Workstation 1,792 demoted to a parenthetical | METHODOLOGY §7 (no silent sparse TFLOPS) and item 8; the Server Edition is the rackable part | METHODOLOGY §8, [Lenovo lp2263](https://lenovopress.lenovo.com/lp2263.pdf) |
| 17 | §8.2 | "a small fraction of the 461 GB available on 4 × GB300" → **493.5 GB** | Follows row 4 | recomputed |
| 18 | **§11.1** | *(no note)* → guard note that the **$0.14** in `serving-optimizations.md` §1.5 is the *retired* `deepseek-v4-flash` name from `deepseek.ai/pricing`, while `deepseek-flash` (= V4.1-Flash) is **$0.15** off-peak | Two published pages, two numbers, same endpoint — re-fetched and confirmed verbatim so a later pass does not "fix" the right one | [DeepSeek API pricing](https://api-docs.deepseek.com/quick_start/pricing) (re-fetched 2026-09-19), [`serving-optimizations.md` §1.5](../../cross-cutting/serving-optimizations.md) |
| 19 | **§11.3** | B300 row "contact sales / — / —" → **$15.00/GPU-h, $3.69 per 1M output, $0.033 per 1M all**; **new GB300 row at $18.00/GPU-h** ($5.64 / $0.049) | Item 7: replace "not retrievable"-class statements with the sourced value. OCI is the only hyperscaler publishing on-demand B300/GB300/MI355X. Explicitly noted: GB300 is **not** $7.40 (Hyperstack B300 HGX) and MI355X is **not** $3.45 (Crusoe MI300X) | [`cloud-pricing.md` §3.4](../../cross-cutting/cloud-pricing.md) (OCI price API) |
| 20 | §11.3 | Totals corrected to exact `per-GPU × n`: B200 2,678 → **2,680** out and 368,294 → **368,292** all; H200 1,491 → **1,488** / 181,103 → **181,104**; H100 718 → **720** / 101,386 → **101,384**; H200 $/1M out 9.40 → **9.42** at the exact $6.305 | Arithmetic; and a **Price source** column added so no row can be read as a neighbouring GPU's rate | recomputed `python3`; [`cloud-pricing.md` §4.1](../../cross-cutting/cloud-pricing.md) |
| 21 | §11.3 | *(none)* → ⚠️ note that the priced **B300 TP=2 row is `infeasible (KV)`** at 268 GB and is listed only because it is a measured row | METHODOLOGY §3 | §5.6 |
| 22 | **§12.7** | "Exact B300/GB300 memory per GPU (270 vs 279 vs 288)" → **settled** (268 / 288), question narrowed to which SKU the InferenceX TP=2 harness ran | METHODOLOGY §8 now pins it; the residual question is about the benchmark, not the hardware | METHODOLOGY §8, [AWS p6](https://aws.amazon.com/ec2/instance-types/p6/) |
| 23 | §12.12 | Dynamo "no recipe found; disaggregation would suit CED" → partial answer added: **TokenSpeed already runs GB300 1P1D** for the base repo; question narrowed to the NVFP4 build | Item 10 | [TokenSpeed recipes](https://lightseek.org/tokenspeed/recipes/models) |
| 24 | §9.1 | nvidia repo DL 550 → **1,969**, row annotated "larger than the 510.3 GB base" | Follows rows 1–2 | [HF API](https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4) |

### Citation integrity spot-check

The five most load-bearing citations were re-fetched from scratch and grepped for
the exact claim text (item 13). All five **CONFIRMED**:

| Citation | Claim it carries | Verdict |
|---|---|---|
| [HF API, nvidia/…-NVFP4](https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4) | `safetensors.total = 763,205,315,794`; per-dtype `U8` 543,581,798,400 / `I8` 13,589,544,960 / `F8_E4M3` 204,015,223,296 / `BF16` 1,976,441,856 / `F32` 42,307,282; `usedStorage` 527,309,220,165; `sha 3431dde3…`; `createdAt 2026-09-16T20:37:42Z` | ✅ exact (only `downloads` moved, see row 2) |
| [NVFP4 model card](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4/raw/main/README.md) | "W4A4"; "476 GiB to 492 GiB"; "16,986,931,200"; "46,080"; "MXFP8 where applicable"; "not exercised in the reported validation"; "8B activated during prefill and 16B during decode" | ✅ all present; **zero** perf claims (§0) |
| [Base card](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/README.md) | "890 bytes per token" | ✅ present |
| [AWS p6 instance types](https://aws.amazon.com/ec2/instance-types/p6/) | `p6-b300.48xlarge` = **"2,144 HBM3e"** GB across 8 GPUs → 268 GB/GPU | ✅ present in the instance table |
| [Lenovo lp2263](https://lenovopress.lenovo.com/lp2263.pdf) | RTX PRO 6000 Blackwell **Server Edition 1,597 GB/s**, GDDR7 | ✅ contains "1597" and "GDDR7"; contains **no** 1,792 figure — confirming the earlier misattribution (Verification log #22) and justifying row 11 |

### Not applicable to this document (checked, no change)

Sparse-vs-dense FLOPS elsewhere in the sweep list (H100/H200 1,979 FP8, A100 312
BF16, MI355X 10,100 MXFP4/MXFP6) — already correct or not quoted here; the
B200-vs-H200 **DeepSeek-R1 FP8** 1.7–3.0× gap — that is a different model and is
not claimed here; the **DSpark acceptance length 3.51** synthetic-benchmark
constant — appears only in the MI355X recipe doc, not here (this document
correctly records that **no** acceptance rate is published for V4.1, §7.2–7.3);
engine **release versions/dates** — this document pins containers and commit
shas rather than numbered releases, which matches
[`inference-engines.md`](../../cross-cutting/inference-engines.md)'s finding that
V4.1-Flash is in **no** numbered vLLM or SGLang release; **per-(model, GPU) fit /
throughput / cost tables** — this *is* a model document, so §5.6, §6.3 and §11.3
belong here, and nothing of that kind was added to a GPU or cross-cutting doc.
