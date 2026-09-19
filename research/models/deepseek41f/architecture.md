# deepseek-ai/DeepSeek-V4.1-Flash — architecture and inference profile

Research date: **2026-09-19**. Formulas, markers and planning defaults follow
[`research/METHODOLOGY.md`](../../METHODOLOGY.md).

> **Method note.** The session-wide `WebSearch` budget (200 calls) was already
> exhausted by sibling agents in this parallel program before this document
> started, so discovery was done with one search plus direct `WebFetch`/`curl`
> against primary sources. Every quantitative claim below is either (a) fetched
> from a primary document and linked inline, or (b) **derived** by me from
> `config.json` and DeepSeek's own reference implementation with the arithmetic
> shown, or (c) marked **⚠️ TO BE VERIFIED**.
>
> **Two independent verifications of this document's method:** the parameter
> arithmetic in §3 reproduces DeepSeek's published "8B active during prefill /
> 16B during decode" to 7.89B / 16.13B, and the KV arithmetic in §5 reproduces
> DeepSeek's published "890 bytes per token" **exactly**, to the byte. Those two
> agreements are the reason the rest of the derived numbers here should be
> treated as `est.` rather than guesswork.

---

## 1. Identity

| Field | Value | Source |
|---|---|---|
| HF repo | `deepseek-ai/DeepSeek-V4.1-Flash` | [HF API](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash) |
| License | **MIT** (repo *and* weights) | [model card](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) |
| Created | `2026-09-10T02:17:58Z` | [HF API](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash) |
| Last modified | `2026-09-10T08:18:10Z` | [HF API](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash) |
| Commit SHA | `dba1be0a40aa45a94ad051997016db3960a90277` | [HF API](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash) |
| Gated? | **No** (`gated: false`, `private: false`) | [HF API](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash) |
| Pipeline tag | `image-text-to-text` (natively multimodal) | [HF API](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash) |
| Library | `transformers` (declares `transformers_version: 5.6.0`) | [`config.json`](./config.json) |
| Downloads / likes | 429,865 / 3,227 as of fetch | [HF API](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash) |
| `usedStorage` | **510,310,271,922 B = 510.31 GB = 475.27 GiB** | [HF API](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash) |

### 1.1 File tree and on-disk size

Computed from the recursive HF tree API
(`curl -sL 'https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash/tree/main?recursive=true'`):

| Extension | Files | Bytes | GB |
|---|---:|---:|---:|
| `.safetensors` | 48 | 510,296,708,312 | 510.30 |
| `.json` | 11 | 13,853,498 | 0.014 |
| `.pdf` (tech report) | 1 | 1,809,802 | 0.002 |
| `.py` (reference impl + encoder) | 9 | 180,500 | — |
| `.md` | 4 | 31,236 | — |
| `.patch` (1), `.png` (2), `.jpeg` (2), `.txt` (7), `.sh` (1), `.gitattributes` (1), `LICENSE` (1) | 15 | 770,217 | — |
| **Total (95 entries = 88 files + 7 directories)** | | **510,313,353,565** | **510.31 GB / 475.27 GiB** |

(Re-derived 2026-09-19 from the same recursive tree API; the rows above now sum to the
total exactly. The earlier "13 files / 337,181 B" row omitted `.gitattributes` and
`LICENSE` and under-counted the image files.)

48 weight shards, most ≈ 7.39 GB. Notable non-weight payloads: `DeepSeek_V41_Tech_Report.pdf`,
`encoding/encoding.py` (prompt-format reference, 37 KB), `inference/model.py`
(61.5 KB readable reference implementation), `inference/kernel.py` (TileLang kernels),
`evaluation/dsh-minimal.patch`.

### 1.2 Checkpoint dtype / quantisation scheme

`config.json` declares:

```json
"quantization_config": {
  "quant_method": "fp8", "activation_scheme": "dynamic",
  "weight_block_size": [32, 32], "scale_fmt": "ue8m0", "expert_dtype": "fp4"
}
```

I read the safetensors headers of **all 48 shards** by HTTP range request and
aggregated 96,085 tensors. The real on-disk dtype mix:

| safetensors dtype | Elements | Bytes | GB | What it is |
|---|---:|---:|---:|---|
| `I8` | 278,585,671,680 | 278,585,671,680 | 278.59 | **packed FP4 experts** — 2 × E2M1 per byte |
| `F8_E4M3` | 204,015,223,296 | 204,015,223,296 | 204.02 | Engram tables + dense/attention weights |
| `F8_E8M0` | 23,563,015,184 | 23,563,015,184 | 23.56 | **UE8M0 block scales** |
| `BF16` | 1,976,441,856 | 3,952,883,712 | 3.95 | embeddings, lm_head, vision tower, norms |
| `F32` | 42,307,282 | 169,229,128 | 0.17 | mHC coefficients, gate biases, attn sinks |
| **Total** | | **510,286,023,000** | **510.29** | |

Two format facts that the one-word label "fp4" hides, and that matter for GPU selection:

- **Routed experts are MXFP4, not NVFP4.** `layers.N.ffn.experts.N.w1.weight` has shape
  `(2304, 2560)` in `I8` for a logical `(2304, 5120)` weight (2 values per byte), and its
  companion `.scale` has shape `(2304, 160)` in `F8_E8M0` → **one E8M0 scale per 32 elements
  along K**. That is the OCP MXFP4 layout. The tech report confirms the choice and the
  motive: *"We adopt the OCP-standard MXFP4 format … to support as many hardware platforms
  as possible, despite the higher accuracy of alternative formats in our experiments"*
  ([tech report §2.4.4](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/blob/main/DeepSeek_V41_Tech_Report.pdf)).
- **Dense/attention FP8 uses true 32×32 blocks.** `layers.N.attn.wq_b.weight` is
  `(32768, 1280)` `F8_E4M3` with `.scale` `(1024, 40)` → 32768/1024 = 32, 1280/40 = 32. Scale
  overhead is only 1/1024 ≈ **+0.1 %**, which is exactly METHODOLOGY §1's pinned row for
  "FP8 E4M3, 32×32 tile, UE8M0 scale (DeepSeek-V4.1)" = 1.001 B/param. (The generic
  "+6 % for 32×32 blocks" this bullet used to argue against is no longer in METHODOLOGY §1 —
  that table was amended 2026-09-19.) Use **+0.1 %** for this checkpoint's dense tensors and
  **+6.25 %** (0.53125 B/param) for its MXFP4 experts.
- **Engram tables are FP8, block-scaled by 32.** `layers.N.engram.embed.weight` is
  `(384006168, 256)` `F8_E4M3` with `.scale` `(384006168, 8)` → one E8M0 per 32 channels.
  Confirmed by the tech report: *"Both the embedding tables and the key/value projections use
  FP8 precision."*

### 1.3 Tokenizer, vocab and chat template

| Item | Value | Source |
|---|---|---|
| `vocab_size` | 129,280 | [`config.json`](./config.json) `text_config` |
| BOS / EOS / PAD | id 0 `<｜begin▁of▁sentence｜>` / 1 `<｜end▁of▁sentence｜>` / 2 | [`config.json`](./config.json), [HF API](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash) |
| Image token id | 129,264 (`<｜deepseek_image｜>`) | [`config.json`](./config.json) |
| DSpark noise token id | 128,799 | [`config.json`](./config.json) |
| Engram compressed vocab | 99,092 (normalised token classes) | [`config.json`](./config.json) |
| **Jinja chat template** | **None shipped.** | [model card](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) |

> *"This release does not include a Jinja-format chat template."*
> ([model card](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash))

Prompt encoding instead lives in `encoding/encoding.py` (a self-contained Python reference)
and in [`deepseek-recipe`](https://github.com/deepseek-ai/deepseek-recipe), a Rust library
with Python bindings. vLLM implements the format natively behind
`--tokenizer-mode deepseek_v41`.

Chat-template features, from [`encoding/README.md`](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/encoding/README.md):

- Special tokens: `<｜System｜>` (mid-conversation system messages — new in V4.1),
  `<｜User｜>`, `<｜Assistant｜>`, `<｜latest_reminder｜>`, `<think>` / `</think>`, `｜DSML｜`.
- **Tool calls use DSML tag blocks, not JSON fences**: `<｜DSML｜ calls>` with
  `<｜DSML｜ invoke>` / `<｜DSML｜ parameter>` — *note the leading space*, which is the
  breaking change from V4's `<｜DSML｜tool_calls>`. Tool results return inside `<tool_result>`.
- **Numeric reasoning effort 1–100**, rendered as a first-turn system prefix
  `Reasoning Effort: {budget} (range 1-100, the higher the value, the more thorough the reasoning)`.
  Only rendered in thinking mode and only at conversation index 0.
- **Two effort mappings exist and they disagree** — see §8.6. The open-source encoder maps
  `"low"→50, "high"→75, "max"→100` with default `"high"` (75)
  ([`encoding/README.md`](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/encoding/README.md)),
  and the tech report says the public API exposes *"low, high, and max correspond to effort
  values of 50, 75, and 100"*. vLLM's parser maps `low/high/xhigh/max → 25/50/75/100`
  ([vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash)).

Recommended sampling ([model card](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash)):
`temperature 1.0`, `top_p 0.95 or 1.0`, `context_window 1M`, `max_tokens ≥ 256K`.

---

## 2. Architecture from `config.json`

### 2.1 Backbone shape

| Field | Value | Consequence |
|---|---|---|
| `num_hidden_layers` | 40 | 20-layer **causal encoder** + 20-layer **decoder** (CED) |
| `hidden_size` | 5120 | |
| `num_attention_heads` | 64 | |
| `head_dim` | **512** | unusually wide; 64 × 512 = 32,768 q width |
| `num_key_value_heads` | **1** | one shared KV latent per layer (MLA-like) |
| `qk_rope_head_dim` | 64 | only the last 64 channels of the 512 get RoPE |
| `q_lora_rank` | 1280 | Q is low-rank: 5120 → 1280 → 32768 |
| `o_lora_rank` / `o_groups` | 1024 / 8 | output projection is **grouped block-diagonal low-rank**: 32768 → (8 groups × 1024) = 8192 → 5120 |
| `vocab_size` | 129,280 | `tie_word_embeddings: false` → separate 0.66B lm_head |
| `rms_norm_eps` | **1e-20** | unusually tiny; matches the training kernel |
| `hidden_act` / `swiglu_limit` | `silu` / 10.0 | SwiGLU with a two-sided clamp on `up`, one-sided on `gate`, *"straight from training, where they keep fp8/fp4 activations in range"* ([`model.py`](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/inference/model.py)) |
| `max_position_embeddings` | 1,048,576 | |
| `rope_scaling` | YaRN, `factor 16`, `beta_fast 32`, `beta_slow 1`, `original_max_position_embeddings 65536` | 65,536 × 16 = 1,048,576 ✓ |
| `rope_theta` / `compress_rope_theta` | 10,000 / **160,000** | compressed latents rotate at their own θ *"because one latent stands for compress_ratio tokens, so its positions are further apart"* |

### 2.2 Attention type per layer — CSA2 modes

`compress_ratios` has **43 entries** = 40 backbone layers + 3 DSpark/MTP layers:

```
layer:  0  1 | 2 .. 19            | 20 .. 39           | 40 41 42 (MTP)
ratio:  0  0 | 2  (18 layers)     | 1  (20 layers)     | 0  0  0
```

Combined with `kv_source_layer_ids [2,8,14,20]` and
`index_source_layer_ids [2,8,14,20,24,28,32,36]`, and with the Full/Reindex/Reuse
definitions in [tech report §2.3.1](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/blob/main/DeepSeek_V41_Tech_Report.pdf),
the **exact per-layer mode assignment** is:

| Layers | Count | Ratio | CSA2 mode | Owns main KV? | Owns indexer K? | Runs indexer? |
|---|---:|---:|---|---|---|---|
| 0, 1 | 2 | — | **SWA only** (no global attention) | — | — | — |
| **2** | 1 | 2 | **Full** | ✅ | ✅ | ✅ full scan |
| 3–7 | 5 | 2 | **Reuse** | reads L2 | reads L2 | ❌ |
| **8** | 1 | 2 | **Full** | ✅ | ✅ | ✅ full scan |
| 9–13 | 5 | 2 | **Reuse** | reads L8 | reads L8 | ❌ |
| **14** | 1 | 2 | **Full** | ✅ | ✅ | ✅ full scan |
| 15–19 | 5 | 2 | **Reuse** | reads L14 | reads L14 | ❌ |
| **20** | 1 | 1 | **Full** + **candidate source** | ✅ (from encoder output — CED) | ✅ | ✅ full scan + builds pool |
| 21–23 | 3 | 1 | **Reuse** | reads L20 | reads L20 | ❌ |
| **24, 28, 32, 36** | 4 | 1 | **Reindex** | reads L20 | reads L20 | ✅ **pool only** |
| 25–27, 29–31, 33–35, 37–39 | 12 | 1 | **Reuse** | reads L20 | reads L20 | ❌ |

**Totals: 2 SWA-only, 4 Full, 4 Reindex, 30 Reuse.** That matches the tech report's claim
that *"the vast majority of Transformer layers—those whose CSA2 operates in Reuse Mode—execute
with only 15 kernels during prefill and 11 during decode"* (30/40 = 75 %).

Every layer also attends over a **128-token sliding window** of its own raw KV
(`sliding_window: 128`), concatenated with the selected compressed entries into one
`sparse_attn` call:

```python
kv, topk_idxs = self._window_kv(x, freqs_cis, start_pos)
if self.compress_ratio:
    compress_kv, compress_idxs = self._compress_kv(x, qr, start_pos, kv.size(1))
    kv = torch.cat([kv, compress_kv], dim=1)
    topk_idxs = torch.cat([topk_idxs, compress_idxs], dim=-1)
o = sparse_attn(q, kv, self.attn_sink, topk_idxs, self.softmax_scale)
```
— [`inference/model.py`](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/inference/model.py)

So each query sees **128 window + 512 selected = 640 entries**, at *any* context length.
There is also a learned per-head `attn_sink` (F32, shape `(64,)` per layer).

### 2.3 Causal Encoder–Decoder (CED)

Tech report §2.2, Eq. (1):

> *"For the upper half layers (i.e., the decoder, l > L/2), the KV entries are not derived from
> their respective hidden states H_l. Instead, they are projected directly from the hidden state
> of the (L/2)-th layer, H_{L/2} … This design allows CED to compute only the first half of the
> layers during the prefill phase."*

In the released checkpoint this collapses neatly: **layer 20 is the single `kv_source` for
the whole decoder**, and layers 21–39 are Reindex/Reuse over its cache. Prefill complexity
drops from O(NL) to O(NL/2 + n_win·L/2) ≈ **half**.

**⚠️ TO BE VERIFIED:** the tech report's Eq. (1) writes *layer-dependent* projections
`W_l^KV, W_l^Z` for every decoder layer, but the released `config.json` lists only four
`kv_source_layer_ids` and the released tensors contain exactly four `attn.compressor.*`
sets. I read this as CSA2's Reuse mode collapsing the per-layer projections into one shared
one — but I did not find a sentence in the report that states this explicitly.

**Cost of CED:** SWA KV is still computed layer-wise for all 40 layers, so exact decoder SWA
would need replaying `L × n_win` tokens. **SWA Bounded Replay** replays only the last
`n_win = 128` tokens and accepts an approximate state: *"for a replay starting at position s,
a query at position i attends to SWA keys in [max(s, i−W+1), i]"* (tech report §3.2.2).
The report states this *"barely compromises response quality"* — no numbers given.
**⚠️ TO BE VERIFIED:** no published accuracy delta for bounded replay.

### 2.4 Hierarchical Sparse Indexer

| Field | Value |
|---|---|
| `index_n_heads` / `index_head_dim` | 32 / 128 |
| `index_topk` | **512** entries kept per query per layer |
| `candidate_source_layer_id` | 20 |
| `candidate_topk_blocks` / `candidate_block_size` | 2048 / 8 → **16,384-position candidate pool** |

Layer 20 scores every causally-visible compressed position, keeps its own top-512, *and*
picks the 2048 highest-scoring blocks of 8 to form a shared pool. Layers 24/28/32/36
(Reindex) rescore **only those 16,384 positions** with their own query weights. The report,
verbatim (§2.3.2): *"For a fixed candidate-pool size, the number of positions scored per query
by each subsequent indexer is bounded independently of context length. **The first Full Mode
layer still scans the entire causally visible range.** Hierarchical indexing therefore reduces
the cost of later indexer evaluations while retaining the initial full-range pass."*
([tech report](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/blob/main/DeepSeek_V41_Tech_Report.pdf))
The report also scopes the mechanism: the Hierarchical Sparse Indexer *"is used only in the
decoder of CED"*, which is why layers 2/8/14 (encoder Full layers) get no candidate pool.

The indexer runs in FP4 on both sides — `fp4_act_quant(q, 32, True)` and
`fp4_act_quant(k, 32, True)`, E8M0 scales — and its scores are ReLU'd, weighted by a
BF16 `weights_proj`, and summed over heads.

### 2.5 MoE routing

| Field | Value |
|---|---|
| `n_routed_experts` / `n_shared_experts` / `num_experts_per_tok` | 384 / 1 / **6** |
| `moe_intermediate_size` | 2304 |
| `scoring_func` | `sqrtsoftplus` (i.e. `sqrt(softplus(x))` — not softmax, not sigmoid) |
| `topk_method` | `noaux_tc` (auxiliary-loss-free balancing) |
| `norm_topk_prob` / `routed_scaling_factor` | true / 1.5 |
| MoE layers | **all 40** (verified from tensor counts, §3) |

Two routing biases per layer: `gate.bias` **and** `gate.bias_vl`. Tech report §2.1.1:
*"we extend auxiliary-loss-free load balancing by maintaining separate expert-wise correction
biases for text and image tokens."* The bias steers *selection only*; the weights come from
the unbiased scores.

### 2.6 Engram conditional memory

| Field | Value |
|---|---|
| `engram_layer_ids` | **[1, 14]** (both in the encoder half) |
| `engram_num_embeddings` | [384,006,168 ; 384,016,682] rows |
| `engram_head_dim` / `engram_n_heads` | 256 / 8 |
| `engram_max_ngram_size` | 4 → n-gram orders {2, 3, 4} |
| `engram_vocab_size` | 16,000,000 (prime-bucket search start) |
| `engram_compressed_vocab_size` | 99,092 |

Each position is hashed as 3 n-gram orders × 8 heads = **24 hash columns**; each column
fetches one 256-d FP8 row. The 24 rows are flattened (24 × 256 = 6144) and projected by
`engram.wkv` (`25600 × 6144`) into `hc_mult+1 = 5` streams of 5120 — 4 keys (one per
hyper-connection copy) and 1 shared value. A sigmoid gate over the normalised dot product of
stream against key decides how much value to add.

Hashes are computed over a **normalised** token space (NFKC → NFD → strip accents →
lowercase → whitespace collapse), so `" The"`, `"the"` and `"THE"` collide deliberately
([`inference/engram.py`](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/inference/engram.py)).
Look-back stops at sequence start and at any image span, so an n-gram never crosses one.

Tech report §2.4.2: *"Each module uses N-gram orders {2, 3, 4}, with 8 hash heads and a total
embedding dimension of 2048 per order … The modules are placed at layers 1 and 14
(zero-indexed) to balance memory usage across training pipeline stages."*

### 2.7 Single-Pass mHC (hyper-connections)

`hc_mult: 4`, `hc_sinkhorn_iters: 20`, `hc_eps: 1e-6`. The residual stream is carried as
**4 parallel copies**. Each sublayer projects the flattened 4×5120 = 20,480 stream through
`hc_*_fn` of shape `(24, 20480)` in **F32** to get `(2 + 4) × 4 = 24` coefficients, split
into pre / post / combine, with the 4×4 combine matrix made doubly stochastic by 20 Sinkhorn
iterations. Crucially the coefficients a sublayer computes are consumed by the *next* one —
that is the "single-pass" trick that lets the mixing fuse into one kernel (`Mega-mHC`).

Memory consequence: **activations are 4× the usual hidden size** (4 × 5120 × dtype per token
in flight), which is the main reason the recipes cap `--max-num-batched-tokens`.

### 2.8 DSpark draft head (MTP)

| Field | Value |
|---|---|
| `num_nextn_predict_layers` | 3 |
| `dspark_block_size` | **5** tokens drafted per round |
| `dspark_target_layer_ids` | [37, 38, 39] |
| `dspark_markov_rank` | 256 |
| `dspark_n_routed_experts` / `dspark_num_experts_per_tok` | **128 / 3** (smaller MoE than the backbone) |

Three blocks with the same 128-token SWA, `compress_ratio == 0` (asserted in code — the
drafter has **no global attention at all**). Stage 0 has `main_proj` (5120 × 15360) reading
the concatenated attention inputs of layers 37–39. Stage 2 adds a `markov_head`
(embed 129280×256 + head 129280×256) that biases draft-token logits on the previously drafted
token, and a `confidence_head` on `dim + markov_rank = 5376`.

### 2.9 Vision encoder

| Field | Value |
|---|---|
| `num_hidden_layers` / `hidden_size` / `num_attention_heads` | 32 / 1024 / 16 |
| `intermediate_size` | 2816 (stored fused as `w1 (5632,1024)` = gate+up) |
| `patch_size` / `downsample_ratio` | 14 / **3** (3×3 pixel unshuffle → ÷9 tokens) |
| `max_image_tokens` | 1024 LLM tokens per image |
| `min_pixels` | 295,936 (= 544×544); smaller images are upscaled |
| Positional | **2D-RoPE**, `rope_theta 10000` (no absolute embeddings) |
| Norm / act | RMSNorm / SwiGLU; patch embed is a **linear projection, not a conv** (for Muon compatibility) |

Aligner: two-layer MLP `w1 (5120, 9216)` → `w2 (5120, 5120)`; 9216 = 1024 × 9 unshuffled.
Effective max resolution ≈ **1344 × 1344** (96×96 patches ÷ 9 = 1024 tokens).
`max_wh_ratio: null`. **No limit on images per prompt**; the practical cap is `--max-model-len`
([vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash)).

---

## 3. Parameter count

Derived from the **complete tensor inventory** — safetensors headers of all 48 shards, read
by HTTP range request, 96,085 tensors. `I8` tensors are packed FP4, so logical element count
is 2× the stored count; `F8_E8M0` tensors are scales, not parameters.

```python
D, MI, NL, NROUT, TOPK, VOCAB = 5120, 2304, 40, 384, 6, 129280

attn_per_layer = 1280*D + 32768*1280 + 512*D + 8192*4096 + D*8192   # 126,615,552
expert         = 3*D*MI                                              #  35,389,440
```

### 3.1 Totals by component

| Component | Logical params | B | On-disk GB |
|---|---:|---:|---:|
| Routed experts, 40 layers × 384 | 543,581,798,400 | 543.58 | 288.78 |
| **Engram tables (2 × 384M × 256)** | **196,613,849,600** | **196.61** | **202.76** |
| DSpark routed experts, 3 × 128 | 13,589,544,960 | 13.59 | 7.22 |
| Attention, 40 layers | 5,064,696,320 | 5.06 | 5.07 |
| Shared experts, 40 layers | 1,415,577,600 | 1.42 | 1.42 |
| Token embedding | 661,913,600 | 0.66 | 1.32 |
| lm_head | 661,913,600 | 0.66 | 1.32 |
| Vision tower + aligner | 485,268,480 | 0.49 | 0.97 |
| DSpark other (attn, main_proj, norms) | 463,457,890 | 0.46 | 0.48 |
| Engram `wkv` projections | 314,654,720 | 0.31 | 0.31 |
| DSpark shared experts | 106,168,320 | 0.11 | 0.11 |
| Routers (`ffn.gate`) | 78,673,920 | 0.08 | 0.16 |
| DSpark markov head | 66,191,360 | 0.07 | 0.13 |
| DSA indexer (8 layers) | 43,516,416 | 0.04 | 0.05 |
| mHC coefficient projections (F32) | 39,323,760 | 0.04 | 0.16 |
| KV compressors (4 layers) | 18,352,128 | 0.02 | 0.04 |
| Norms / misc | 414,720 | 0.00 | 0.00 |
| **TOTAL** | **763,205,315,794** | **763.21** | **510.29** |

The total **exactly equals** the HF API's reported
`safetensors.total = 763,205,315,794` ([HF API](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash)).

### 3.2 Reconciling with the "552B backbone + 196B Engram" claim

```
total                                     763,205,315,794
− engram tables                       −   196,613,849,600
− all DSpark/MTP (experts+shared+attn+markov)
                                      −    14,225,362,530
= 552,366,103,664  = 552.4 B      ✓  matches "552B backbone parameters"
```

So DeepSeek's **552B backbone** = routed experts + attention + shared experts + embeddings +
lm_head + routers + indexer + mHC + compressors + **vision tower** + **engram `wkv`
projections**, but **excludes** the 196.6B engram embedding tables and the ~14.2B DSpark
drafter. The vLLM recipe states the same partition independently: *"DeepSeek's 552B backbone
figure covers the routed experts, attention and embeddings; the Engram tables, the ~14B
DSpark drafter and the block scales sit outside it"*
([vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash)).

And the engram table claim checks out to three significant figures:
`(384,006,168 + 384,016,682) × 256 = 196,613,849,600` ≈ **196B** ✓ (tech report: *"We allocate
196B Engram parameters evenly across two modules"*).

### 3.3 Active parameters — deriving 8B prefill / 16B decode

```python
moe_per_layer_active = NROUT*D + expert + TOPK*expert    # router + shared + top-6 = 249,692,160
mhc        = 2*24*(4*D)                                  #     983,040  per layer
idx_layer  = 4096*1280 + 32*D                            #   5,406,720  per index-source layer
engram_prj = (3*8*256)*(D*5)                             # 157,286,400  per engram module
head       = VOCAB*D                                     # 661,913,600
```

**Prefill** — CED means only the 20 encoder layers run for prompt tokens; both engram modules
(layers 1 and 14) are in the encoder:

```
20*126,615,552  (attention)        =  2,532,311,040
20*249,692,160  (MoE)              =  4,993,843,200
20*983,040      (mHC)              =     19,660,800
 3*5,406,720    (indexer L2,8,14)  =     16,220,160
                (compressors)      =     18,350,080
 2*157,286,400  (engram wkv)       =    314,572,800
------------------------------------------------------
                                      7,894,958,080 = 7.89 B
```
**vs DeepSeek's claim of 8B active during prefill — ✓ agrees.**

**Decode** — all 40 layers, all 8 indexers, both engrams, plus lm_head once per token:

```
40*126,615,552 = 5,064,622,080
40*249,692,160 = 9,987,686,400
40*983,040     =    39,321,600
 8*5,406,720   =    43,253,760
engram + comp  =   332,922,880
lm_head        =   661,913,600
------------------------------------
                16,129,720,320 = 16.13 B   (15.47 B excluding lm_head)
```
*(Corrected 2026-09-19: the six terms listed above sum to 16,129,720,320, not the
16,111,370,240 previously printed — the earlier total dropped the 18,350,080-param
compressor term that the `engram + comp` line already includes. Re-derived with
`python3` from `config.json`; the prefill total 7,894,958,080 checks out unchanged.)*
**vs DeepSeek's claim of 16B active during decode — ✓ agrees.**

### 3.4 Non-quantised tensors (stay BF16/F32)

These are the tensors that *no* quantisation recipe in the wild touches, and they set the
floor on any low-bit plan:

| Tensor | Shape | dtype | Bytes |
|---|---|---|---:|
| `embed.weight` | (129280, 5120) | BF16 | 1.324 GB |
| `head.weight` | (129280, 5120) | BF16 | 1.324 GB |
| `vision.*` (32 blocks + patch embed + norm) | — | BF16 | 0.823 GB |
| `aligner.w1/w2` (+bias) | (5120,9216), (5120,5120) | BF16 | 0.147 GB |
| `mtp.N.markov_head.embed/head` | (129280, 256) ×2 ×3 | BF16 | 0.132 GB |
| `layers.N.attn.compressor.wkv/wgate/norm` | (512, 5120) | **BF16** | 0.037 GB |
| `layers.N.attn.indexer.wk`, `weights_proj`, `k_norm` | — | BF16 | 0.003 GB |
| `layers.N.ffn.gate.weight` (routers) | (384, 5120) | BF16 | 0.157 GB |
| `layers.N.hc_*_fn / _base / _scale` | (24, 20480) etc. | **F32** | 0.157 GB |
| `layers.N.ffn.gate.bias`, `bias_vl`, `attn.attn_sink` | — | F32 | 0.00004 GB |
| All RMSNorm weights | — | BF16 | 0.0006 GB |
| **Total non-quantised** | | | **≈ 4.11 GB** |

Note `attn.wo_a` is **F8_E4M3 on disk but dequantised to BF16 by `convert.py`**, because it is
applied as a block-diagonal `einsum` rather than a `Linear`. The code says so explicitly:
*"convert.py dequantizes it to bf16; an fp8 grouped GEMM would halve the memory"*
([`model.py`](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/inference/model.py)).
That costs an extra **1.34 GB** at runtime in the reference implementation.

---

## 4. Weight memory by dtype

Per METHODOLOGY §1, with this checkpoint's *measured* scale layouts (not the generic ones).
Overheads are quoted **relative to the unscaled payload**, which is METHODOLOGY §1's
convention; the 2026-09-19 sweep found this paragraph mixing that convention with a
"fraction of one byte" one, which halved both FP4 figures:

| Group | scale layout | bytes / param | overhead vs payload | METHODOLOGY §1 |
|---|---|---:|---:|---|
| Dense / attention FP8 E4M3 | 32×32 2-D tile, one UE8M0 per 1,024 elems | 1.000977 | **+0.098 %** | 1.001, +0.1 % ✓ |
| MXFP4 experts | E2M1 + one E8M0 per 32 | **0.53125** | **+6.25 %** (printed +3.125 % before) | 0.53125, +6.25 % ✓ |
| NVFP4 (NVIDIA build) | E2M1 + one FP8 E4M3 per 16 | **0.5625** | **+12.5 %** (printed +6.25 % before) | 0.5625, +12.5 % ✓ |
| INT4 g32, FP16 scale | one FP16 per 32 | 0.5625 | **+12.5 %** (printed +6.25 % before) | ≈0.53 for g128; g32 is coarser-grained here |

The byte totals below were always computed from the *layouts*, not from these percentages, so
**no total changes** — only the labels were in the wrong convention.

| Scheme | Experts | Engram | Dense | Embed/head/vision | **GB** | **GiB** | Source |
|---|---|---|---|---|---:|---:|---|
| **Native checkpoint (as shipped)** | MXFP4 | FP8 | FP8 32×32 | BF16 | **510.29** | **475.24** | [measured, HF tree](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash) |
| All-BF16 dequantised | BF16 | BF16 | BF16 | BF16 | **1,526.41** | 1,421.58 | `est.` 763.21B × 2 |
| All-FP8 (E4M3, 32×32) | FP8 | FP8 | FP8 | BF16 | **765.76** | 713.17 | `est.` |
| **NVIDIA NVFP4** | **NVFP4 g16** | FP8 | FP8 | BF16 | **527.31** | 491.10 | [measured](https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4) (`usedStorage 527,309,220,165`) |
| ″ reconstructed by formula | | | | | 527.27 | 491.07 | `est.` — **0.007 % from measured ✓** |
| MXFP4 experts **+ MXFP4 engram** | MXFP4 | MXFP4 | FP8 | BF16 | **411.69** | 383.41 | `est.` |
| INT4 g32 experts+engram, dense BF16 | INT4 | INT4 | BF16 | BF16 | 442.84 | 412.43 | `est.`; repo reports **451.7 GB** measured — see below |

Notes:

- **The scale-layout claims in §1.2 are exact, not approximate.** Rebuilding the native
  checkpoint from them —
  `(routed+DSpark)×(0.5+1/32) + engram×(1+1/32) + dense×(1+1/1024) + BF16 + F32` — gives
  **510,286,023,000 B to the byte**, the `total_size` in
  [`model.safetensors.index.json`](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/model.safetensors.index.json).
  Verified independently by reading the shard-3 safetensors header: `layers.0.ffn.experts.0.w1.weight`
  is `I8 [2304, 2560]` with `.scale` `F8_E8M0 [2304, 160]` (one E8M0 per 32 along K = MXFP4), and
  `layers.0.attn.wq_b.weight` is `F8_E4M3 [32768, 1280]` with `.scale` `F8_E8M0 [1024, 40]`
  (true 32×32 2-D blocks = 1 scale per 1024 elements).
- The **NVFP4 conversion makes the checkpoint bigger, not smaller** — 510.29 → 527.31 GB —
  because it swaps one E8M0 per 32 elements for one E4M3 per 16. NVIDIA states this plainly:
  *"The source experts already use four-bit weights. The finer NVFP4 scale layout increases
  checkpoint size from approximately 476 GiB to 492 GiB."*
  ([NVFP4 card](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4)). **The reason to
  convert is Blackwell's native NVFP4 tensor-core path, not capacity.**
- My INT4 estimate (442.84 GB) is 1.9 % under the 451.7 GB the AutoRound repo reports
  ([lvkaokao/…-W4A16-Engram-AutoRound](https://huggingface.co/lvkaokao/DeepSeek-V4.1-Flash-W4A16-Engram-AutoRound));
  the gap is auto_gptq's `qzeros` tensors, which my formula omits.
- vLLM's own accounting of the same checkpoint, for cross-checking
  ([vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash)):
  routed+DSpark experts (MXFP4) 259.5 GiB, Engram tables (FP8) 183.1 GiB, attention/dense/routers
  (FP8) 6.9 GiB, embedding+lm_head+norms 3.9 GiB, UE8M0 block scales 21.9 GiB.
  Sum = 475.3 GiB ✓ consistent with my 475.24 GiB.
- **`vram_minimum_gb: 614`** is vLLM's schema figure = total × 1.2 headroom factor
  ([vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash)).

### 4.1 The Engram table is the real capacity problem

202.76 GB = **188.83 GiB** of the checkpoint — **40 % of it** — is two hash tables that are
accessed by *random single-row gather*, 24 rows per token per module. Bandwidth demand is
trivial (12,672 B/token = **12.38 KiB/token**, §6.3); **capacity and random-access latency** are the problem.

*Two accountings, don't mix them:* **196.61 GB = 183.11 GiB** is the FP8 table payload alone
(what vLLM's table reports, because it lists UE8M0 scales on a separate row), and
**202.76 GB = 188.83 GiB** is payload + its E8M0 scales, i.e. what actually has to be resident
or offloaded. antirez's GGUF card independently quotes the same resident figure —
*"The language GGUFs each include 188.83 GiB of native FP8 Engram tables"*
([antirez/deepseek-v4.1-flash-gguf](https://huggingface.co/antirez/deepseek-v4.1-flash-gguf)),
and the diffbot RTX PRO 6000 recipe budgets *"about 300 GB of free host RAM (the Engram tables
are pinned, ~190 GiB)"*. Where this document says "183 GiB" below, read 188.8 GiB for a
sizing budget.

Three strategies are in production use:

| Strategy | Where | Cost |
|---|---|---|
| Resident in HBM | default on ≥ 2 TB-class nodes | **188.8 GiB** (202.76 GB: FP8 payload + E8M0 scales) of HBM you cannot use for KV |
| **Host/CPU offload** | vLLM `--engram-config '{"cpu_offload":true}'` — the **required** override on **H100** and **B300** ([vLLM recipe schema](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash)) | PCIe random reads per step |
| **On-disk / NVMe streaming** | DeepSeek's own design (*"prefetched from host memory via background RDMA transfers"*, tech report §2.4.2); community NVMe patch for DGX Spark; DwarfStar GGUF `--ssd-streaming` | measured **5.9–7.8 ms/step** over NFS vs **2.8 ms** node-local ([DGX Spark repo](https://github.com/tonyd2wild/DeepSeek-V4.1-Flash-vLLM-DGX-Spark); 48 GB of rows per worker) |

---

## 5. KV cache and per-sequence state

### 5.1 Deriving 890 bytes/token

Per METHODOLOGY §2, only layers that **own** a cache contribute; sharing layers contribute 0,
and a layer with compression ratio *r* contributes 1/*r* per token.

Cache-owning layers are exactly `kv_source_layer_ids = [2, 8, 14, 20]` — layers 2/8/14 at
ratio 2, layer 20 at ratio 1. Each owns **two** caches: main KV (`compress_kv_cache`, 512-d)
and indexer K (`indexer.k_cache`, 128-d).

**Main KV — NVFP4-style E2M1 with one E4M3 scale per 16 channels** (`fp4_act_quant(latent, 16,
True, scale_dtype=torch.float8_e4m3fn)` in `model.py`):

```
per entry = 512 × 0.5 B  +  (512/16) × 1 B  = 256 + 32 = 288 B
layers 2,8,14 (r=2):  3 × 288/2 = 432 B/token
layer 20      (r=1):  1 × 288   = 288 B/token
                      -------------------------
main KV total                  = 720 B/token
```

**Indexer K — MXFP4, E2M1 with one E8M0 scale per 32** (`fp4_act_quant(k, 32, True)`):

```
per entry = 128 × 0.5 B  +  (128/32) × 1 B  = 64 + 4 = 68 B
layers 2,8,14 (r=2):  3 × 68/2 = 102 B/token
layer 20      (r=1):  1 × 68   =  68 B/token
                      ------------------------
indexer total                  = 170 B/token
```

```
GLOBAL KV = 720 + 170 = 890 bytes/token
```

**This is exactly DeepSeek's published figure**, byte for byte
([model card](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash): *"reduce the global KV
cache footprint to 890 bytes per token"*). The report's format choice is deliberate:
*"we select E2M1 with one E4M3 scale per 16 channels, following NVFP4 … but omitting its
second-level global scale"* (§2.4.4).

### 5.2 What the same model would cost at FP8 and BF16

| KV dtype | Main | Indexer | **B/token** | × vs shipped | 1M ctx (GiB units) | 1M ctx (GB units) |
|---|---:|---:|---:|---:|---:|---:|
| **FP4 (shipped, Blackwell-kernel figure)** | 720 | 170 | **890** | 1.00× | **890 MiB** | 933 MB |
| FP8 (E4M3 + E8M0/32) | 1,320 | 330 | 1,650 | 1.85× | 1,650 MiB | 1,730 MB |
| BF16 | 2,560 | 640 | 3,200 | 3.60× | 3,200 MiB | 3,355 MB |

(Corrected 2026-09-19: the "1M ctx" column was labelled **MB** but held **MiB** values —
`1,048,576 × 890 = 933,232,640 B = 890.00 MiB = 933.23 MB`. Per METHODOLOGY §1, memory is
reported in GiB/MiB unless the column says GB; both are now shown.)

The report says FP4 *"nearly halves the storage footprint"* versus V4's FP8 main KV — my
1.85× agrees. Note that **FP4 here buys storage, not FLOPs**: *"FP4 reduces storage rather
than accelerates matrix multiplication. Dequantizing cached values before attention allows us
to use a more accurate format without requiring native matrix-multiplication support"*
(§2.4.4). **This is why the FP4 KV works identically on Hopper and Ampere** — no FP4 tensor
cores needed.

### 5.3 Fixed per-sequence state (sliding-window layers)

Every layer holds a **128-slot ring buffer**, sized independent of context
(`torch.zeros(max_batch_size, args.window_size, self.head_dim)` in `model.py`). The values are
FP8-rounded — `_window_kv` calls `act_quant(kv, fp8_block_size, scale_fmt, scale_dtype, True)`
in place and its docstring says *"The K stays fp8, quantized over the whole post-RoPE vector,
RoPE tail included"* — so at FP8 + one E8M0 scale per 32 the budget is:

```
per layer = 128 slots × (512 × 1 B + (512/32) × 1 B) = 128 × 528 = 67,584 B
40 backbone layers  = 2,703,360 B = 2.58 MiB
 3 DSpark layers    =   202,752 B = 0.19 MiB
-----------------------------------------------
fixed per-seq state = 2,906,112 B = 2.77 MiB
```

**⚠️ TO BE VERIFIED — that 2.77 MiB is an engine-optimal figure, not what the cited code
does.** In the reference implementation the ring buffer inherits
`torch.set_default_dtype(torch.bfloat16)` (`model.py` line 1296), so the *container* is
**BF16** and the in-place `act_quant` call discards the scale tensor it returns: the buffer
holds fp8-rounded values in bf16 storage at `128 × 512 × 2 B = 131,072 B` per layer, i.e.
**5,636,096 B = 5.38 MiB per sequence** across 43 layers — 1.94× the FP8 figure. Plan with
2.77 MiB only if your engine stores the SWA window as true FP8 + E8M0 scales; no vLLM/SGLang
document states the dtype it uses for this buffer. Every "2.77 MiB" downstream (§5.4, §6.3)
carries the same caveat.

**Engine state-slot count `S` (METHODOLOGY §2).** Both figures above are for **`S = 1`** — one
ring-buffer set per running request. No vLLM or SGLang document states an `S > 1` for this
model (unlike Kimi-K3, where SGLang allocates `S = 5`), and the DSpark drafter reuses the
backbone's window rather than taking slots of its own. **⚠️ TO BE VERIFIED:** if a future
engine allocates speculative slots for the 5 DSpark draft positions, multiply by that `S`.

Smaller fixed states also exist: the ratio-2 compressors hold a partial group
(3 layers × 2 buffers × 2 × 512 × 4 B F32 = 24,576 B = 24 KiB), and the reference
`NgramHashState` keeps an `int64` cache of compressed token ids —
`max_seq_len × 8 B` = **8 MiB (8.39 MB) per sequence at 1M context**, ~0.9 % of the global KV.
**⚠️ TO BE VERIFIED:** whether vLLM/SGLang keep that hash cache at int64 or recompute it;
the reference implementation is explicitly *"a readable reference implementation rather than a
production serving engine"*
([`inference/README.md`](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/inference/README.md)).

### 5.4 Totals

`kv_total(ctx, n_seq) = n_seq × (ctx × 890 + fixed_state)`, `S = 1`, with **both** candidate
values of `fixed_state` per §5.3 ⚠️ — 2,906,112 B (2.77 MiB, true FP8 + E8M0/32, engine-optimal)
and 5,636,096 B (5.38 MiB, the reference implementation's BF16 container). Recomputed with
`python3` 2026-09-19:

| Context | Global KV | **+ 2.77 MiB SWA (FP8, engine-optimal)** | **+ 5.38 MiB SWA (BF16 reference) ⚠️** | BF16-KV equivalent | Ratio (global) |
|---|---:|---:|---:|---:|---:|
| 8 K | 6.95 MiB | **9.72 MiB** | **12.33 MiB** | 25.00 MiB | 3.6× |
| 32 K | 27.81 MiB | **30.58 MiB** | **33.19 MiB** | 100.00 MiB | 3.6× |
| 128 K | 111.25 MiB | **114.02 MiB** | **116.62 MiB** | 400.00 MiB | 3.6× |
| 1 M | 890.00 MiB | **892.77 MiB** | **895.38 MiB** | 3,200.00 MiB | 3.6× |

(The "Ratio" column now compares like with like — global FP4 KV vs global BF16 KV, a constant
3.60× = 3,200/890. The earlier 2.6/3.3/3.5/3.6 column divided the *BF16 global* figure by the
*FP4 global + FP8 fixed state*, mixing two different quantities.)

Below ~3.3 K context the fixed 2.77 MiB dominates the per-token cache — worth knowing for
short-prompt, high-concurrency agentic traffic. At the reference implementation's BF16 SWA
buffer (§5.3 ⚠️) the fixed term is 5.38 MiB and the crossover moves out to **~6.3 K**
(`2,906,112 / 890 = 3,265 tokens`; `5,636,096 / 890 = 6,333 tokens`).

vLLM's independent summary agrees: *"a full 1M-token prompt holds under 1 GB of global KV,
plus a fixed 128-token sliding window per layer. Weights and batch size, not cache, set the
capacity limit"* ([vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash)).

### 5.5 Where each compression mechanism's saving comes from

| Mechanism | Saving | Evidence |
|---|---|---|
| **Cross-layer KV sharing** | 38 CSA2 layers → **4** cache owners = **9.5×** | `kv_source_layer_ids [2,8,14,20]` |
| **Sequence compression (ratio 2)** | 2× on 3 of the 4 owners | `compress_ratios` |
| **FP4 main KV** | 1.85× vs FP8, 3.6× vs BF16 | §5.2 |
| **Sliding window (W=128)** | SWA KV becomes O(1), not O(ctx) | `sliding_window: 128` |
| **Sparse top-k (DSA)** | doesn't cut *storage*, cuts *reads* — 512 of N entries | `index_topk: 512` |
| **Hierarchical indexer** | deeper indexers read 16,384, not N | `candidate_topk_blocks × block` |
| **SWA Bounded Replay** | removes SWA KV from the persistent (SSD) tier entirely | tech report §3.2.1 |

Overall DeepSeek claims **≈4× vs V4-Flash and ≈437× vs DeepSeek-V1** on per-token global KV
([model card](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash), Figure 1b) — implying
V4-Flash ≈ 3,560 B/token and V1 ≈ 389 KB/token. **⚠️ TO BE VERIFIED:** those two back-computed
figures are mine, not published.

---

## 6. Compute profile

### 6.1 FLOPs per token (GEMM terms, `2 × active_params`)

| Phase | Active params | **FLOPs/token** |
|---|---:|---:|
| Prefill (encoder half, §3.3) | 7.89 B | **15.79 GFLOP** |
| Decode (all layers + lm_head) | 16.13 B | **32.26 GFLOP** |
| Decode excl. lm_head | 15.47 B | 30.94 GFLOP |

The 2:1 decode:prefill ratio is the whole economic point of CED, and it inverts the usual
picture: **for this model a prefill token costs half a decode token in FLOPs**, before
attention terms. That is what DeepSeek means by *"substantially improving cost efficiency for
input-heavy agentic workloads."*

### 6.2 Attention FLOPs

Per METHODOLOGY §4, adapted to this model's three distinct attention terms.

**Decode, per sequence per step** (`est.`):

| Context | Full-mode indexer scan (4 layers) | Reindex pool (4 × 16,384) | Core attention (38 layers × 640 entries) | **Total** |
|---|---:|---:|---:|---:|
| 8 K | 0.17 GF | 0.54 GF | 3.19 GF | **3.93 GF** |
| 32 K | 0.67 GF | 0.54 GF | 3.19 GF | **4.43 GF** |
| 128 K | 2.68 GF | 0.54 GF | 3.19 GF | **6.44 GF** |
| 1 M | **21.47 GF** | 0.54 GF | 3.19 GF | **25.23 GF** |

Indexer scan = `2 × 32 heads × 128 dim × (3×C/2 + C)` positions. Core attention =
`38 × 2 × 2 × 64 × 512 × 640` — **constant in context length**, which is the DSA payoff.

> **Planning consequence:** at ≤128 K the attention term is ~20 % of the 32.26 GFLOP GEMM
> term and can be ignored. **At 1M it is 25.2 GFLOP — comparable to the entire MoE
> forward** — and it is all in the four Full-mode indexer layers. Whether a given GPU has a
> fast FP4 indexer kernel therefore decides 1M-context decode performance far more than its
> MoE throughput does.

**Prefill attention terms** (`est.`, encoder half only — layers 2/8/14 scan causally):

| N tokens | Indexer O(N²/2) | Selected attention (18 layers × 640) | GEMM term (15.79 GF × N) |
|---|---:|---:|---:|
| 4 K | 0.10 TFLOP | 6.19 TFLOP | 0.065 PFLOP |
| 32 K | 6.60 TFLOP | 49.5 TFLOP | 0.52 PFLOP |
| 128 K | 106 TFLOP | 198 TFLOP | 2.07 PFLOP |
| 1 M | **6,755 TFLOP** | 1,583 TFLOP | 16.6 PFLOP |

At 1M-token prefill the indexer's quadratic term reaches ~6.8 PFLOP — **40 % of the GEMM
term**. Below 128 K it is under 5 %.

### 6.3 Bytes read per decode step (MoE distinct-expert model)

`distinct_experts(batch) = 384 × (1 − (1 − 6/384)^batch)`, per METHODOLOGY §4.
One MXFP4 expert = `35,389,440 × 0.5 + 35,389,440/32` = **18,800,640 B = 18.80 MB**.
Resident non-expert, non-engram-table weights = **18.75 GB** (attention, shared experts,
routers, indexer, mHC, embed, lm_head, vision, DSpark experts, engram projections).

| Batch | Distinct experts / layer | Expert bytes | + static | **Weight bytes / step** |
|---:|---:|---:|---:|---:|
| 1 | 6.0 | 4.51 GB | 18.75 GB | **23.26 GB** |
| 8 | 45.5 | 34.18 GB | 18.75 GB | **52.93 GB** |
| 32 | 152.0 | 114.32 GB | 18.75 GB | **133.07 GB** |
| 64 | 243.8 | 183.38 GB | 18.75 GB | **202.13 GB** |
| 128 | 332.8 | 250.31 GB | 18.75 GB | **269.06 GB** |
| 256 | 377.2 | 283.65 GB | 18.75 GB | **302.40 GB** |

With 384 experts and top-6, **expert-read saturation arrives late**: batch 32 still touches
only 40 % of experts, so throughput scales well up to ~batch 64 and then flattens.
Beyond ~batch 128 the read is essentially the whole expert set (288.8 GB) and further batching
is nearly free in bandwidth terms — the classic wide-MoE regime that rewards large
concurrency and **EP over TP**.

**Engram traffic** is separate and small in bytes but pathological in pattern: 24 rows ×
264 B × 2 modules = **12,672 B/token**, as **48 random gathers per token**. At batch 256 that
is 12,288 random 264-byte reads per step (≈3.2 MB). Resident in HBM it disappears into the
noise; over PCIe to host it does not (§4.1).

**KV bytes read per sequence per decode step** (`est.`, from the reference implementation's
access pattern):

| Term | Bytes |
|---|---|
| SWA windows, all 43 layers (fixed) | 2.77 MiB (5.38 MiB if BF16-stored — §5.3 ⚠️) |
| Sparse main KV: 38 layers × 512 × 288 B | 5.34 MiB |
| Reindex candidate pool: 4 × 16,384 × 68 B | 4.25 MiB |
| **Full-mode indexer scan: 170 × C bytes** | **scales with context** |

| Context | Indexer scan | **Total / seq / step** |
|---|---:|---:|
| 8 K | 1.33 MiB | **13.69 MiB** |
| 32 K | 5.31 MiB | **17.68 MiB** |
| 128 K | 21.25 MiB | **33.62 MiB** |
| 1 M | 170.00 MiB | **182.37 MiB** |

**These are per-sequence figures.** Per METHODOLOGY §3, total decode KV-read bytes for a step
are `batch × (fixed_state + context-scaling terms)` — never a flat per-step constant. Multiply
the "Total / seq / step" column by the batch: at batch 32 that is 438 MiB (8 K), 566 MiB
(32 K), **1.05 GiB (128 K)** and 5.70 GiB (1 M).

> The indexer's **full scan of all 170 B/token of indexer K, every step, in four layers** is
> the single long-context bottleneck. At batch 32 × 128 K that is 1.05 GiB per step of KV
> traffic — on one H200 (4.8 TB/s, METHODOLOGY §8) that is 235 µs at *peak* bandwidth and
> **294–392 µs at the §4 planning MBU of 0.6–0.8**, comparable to the weight read at that
> batch. At 1M it is 5.70 GiB/step and dominates completely.
> **Partly resolved 2026-09-19 (was ⚠️):** the tech report states the design intent
> explicitly — *"The first Full Mode layer still scans the entire causally visible range"*,
> and the Hierarchical Sparse Indexer *"is used only in the decoder of CED"*
> ([tech report §2.3.2](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/blob/main/DeepSeek_V41_Tech_Report.pdf)).
> So the full scan at the encoder Full layers is architectural, not a reference-code
> simplification, and `candidate_source_layer_id: 20` covering only the decoder is consistent.
> **⚠️ STILL TO BE VERIFIED:** whether any production engine block-prunes that scan anyway as
> an optimisation, and no vendor has published measured 1M-context decode step times.

### 6.4 Vision encoder FLOPs

ViT params/layer = `3·1024² + 1024² + 5632·1024 + 1024·2816` = 12,845,056; × 32 = 411.0 M.

| Image | Patches | LLM tokens | GEMM | Self-attn (bidirectional) | Aligner | **Total** |
|---|---:|---:|---:|---:|---:|---:|
| Max ≈1344×1344 | 9,216 | 1,024 | 7.58 TF | 11.13 TF | 0.15 TF | **18.86 TFLOP** |
| Min 544×544 | 1,444 | 160 | 1.19 TF | 0.27 TF | 0.02 TF | **1.48 TFLOP** |

**Attention dominates at full resolution** (9,216 tokens of *dense bidirectional* attention —
no sparsity in the ViT). A max-res image costs ≈18.9 TFLOP, i.e. as much as **≈1,200 prefill
tokens** of the language backbone, while contributing only 1,024 tokens. Two practical
consequences, both reflected in the vLLM recipe:

- `--mm-encoder-tp-mode data` ("Encoder parallel") runs the small ViT data-parallel:
  *"at 32 layers / hidden 1024 the encoder is small enough that TP communication costs more
  than it saves, which can significantly reduce TTFT for multi-image requests"*.
- `--language-model-only` drops the ViT entirely for text workloads and frees its VRAM
  for KV.

DeepSeek's own deployment goes further with **EPD disaggregation** —
*"Encoder–Prefill–Decode disaggregation, enabling vision encoding, prefill, and decoding to
scale independently"* (tech report §3.2).

---

## 7. Speculative decoding and MTP

### 7.1 What ships in the checkpoint

**No MTP module.** V4.1 dropped the V3/V4-style MTP trained alongside the backbone:
*"We omit the MTP module during backbone pre-training and use DSpark for speculative
decoding"* (tech report §2.1). The `num_nextn_predict_layers: 3` field names the three
**DSpark** stages, stored under `mtp.*` in the checkpoint (≈14.2 B parameters, 7.4 GB on disk).
There is **no separate draft repo to download** — `--speculative-config` names the target repo
as its own `model`.

### 7.2 Mechanism

Tech report §2.4.3:

> *"The drafter comprises three Transformer blocks with a sliding attention window of 128
> tokens. A single forward pass through these blocks computes base logits for five draft
> positions in parallel, while a lightweight Markov head models dependencies among the draft
> tokens. A confidence head predicts per-position conditional acceptance probabilities … The
> scheduler combines these estimates with profiled engine throughput curves to dynamically
> select the verification length for each request, aiming to maximize expected system-wide
> token throughput under the current system load."*

Three things distinguish it from EAGLE/MTP for capacity planning:

1. **Semi-autoregressive**: one forward pass emits all 5 draft positions (`block_size 5`),
   with `dspark_noise_token_id 128799` filling positions 1–4 of the draft input.
2. **The drafter is itself a small MoE** — 128 routed experts, top-3 — so it adds
   13.6 B params of weight *read* per drafted step, though only ~3/128 of it is active.
3. **Confidence-scheduled adaptive verification** trades acceptance against batch pressure.
   This is the flag that does not work on ROCm today (§8.5).

Decode active-parameter cost per draft round `est.`: 3 layers × (attention 126.6 M + MoE
[router 128×5120 + shared 35.4 M + 3×35.4 M] + mHC) ≈ **0.52 B**, i.e. ~3.2 % of the 16.13 B
verify step (§3.3, corrected). The drafter is cheap; the cost is the wider verify batch.

### 7.3 Published acceptance rates and speedups

| Source | Hardware / engine | Draft width | Acceptance | Note |
|---|---|---|---|---|
| [InferenceX #3058](https://github.com/SemiAnalysisAI/InferenceX/pull/3058) via [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash) | 4× MI355X, vLLM ROCm | 5 | **3.51 — a *synthetic benchmark constant*, not a measurement** | the throughput arm sets `"rejection_sample_method":"synthetic","synthetic_acceptance_length":3.51`, i.e. the harness *assumes* 3.51 rather than observing it; only the `EVAL_ONLY=true` arm (`"rejection_sample_method":"block"`) does real block rejection, and it publishes no acceptance figure |
| [DGX Spark repo](https://github.com/tonyd2wild/DeepSeek-V4.1-Flash-vLLM-DGX-Spark) | 4× GB10, vLLM TP4 | 5 | **mean 3.57 tok/step** (range 1.88–5.92) | community measurement; *"Counting/code near 6-token maximum; prose/narrative ~2 tokens"* |
| [vLLM V4-Flash-Vision recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp) | GB200 | 3 | 2.99 tok/forward, 66.3 % overall (83.9/66.5/50.7 by position) | **this is V4-Flash-Vision-Exp, a different checkpoint** — listed only as an order-of-magnitude anchor |

**⚠️ TO BE VERIFIED:** DeepSeek publishes **no** acceptance-rate or end-to-end speedup number
for DSpark on V4.1-Flash in the tech report or model card. vLLM's guidance is explicit:
*"Acceptance depends on the workload, so measure it on your own traffic before sizing a
deployment around it."*

The only figure in that table that is an actual *measurement* of this checkpoint is the DGX
Spark community mean of **3.57** tok/step; 3.51 is a synthetic constant and 2.99 is a
different checkpoint. At acceptance length ≈3.5 of 5, the naive decode-throughput multiplier is ~3.5× at batch 1,
falling toward 1× as batch grows and the verify step becomes compute-bound — the standard
speculative crossover. Per §6.3 that crossover should land late here, because expert reads do
not saturate until ~batch 128.

### 7.4 Engine configuration

```json
{"method":"dspark","num_speculative_tokens":5,"draft_sample_method":"probabilistic",
 "rejection_sample_method":"block","enable_adaptive_verification":true}
```
— NVIDIA default, [vLLM recipe schema](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash).
On AMD the last field must be `false` (§8.5). Remember to size CUDA graphs for the widened
batch: *"The benchmark below rounds `128 * (1 + 5) = 768` up to 1024"* for
`--max-cudagraph-capture-size`.

---

## 8. Engine support matrix

### 8.1 Summary

| Engine | Min version | Status 2026-09-19 | Notes |
|---|---|---|---|
| **vLLM** | **0.30.0+, nightly required** — and **0.30.0 does not exist yet**: the latest release is **0.29.0** (2026-09-09), re-verified 2026-09-19 via PyPI | ✅ Production path | Architecture merged to `main` in [PR #56228](https://github.com/vllm-project/vllm/pull/56228); *"any nightly from 2026-09-10 on serves it"* |
| **SGLang** | commit `da64c5cb` / image `lmsysorg/sglang:dev-cu13-dsv41`; **not in any numbered release** (latest is **0.5.20**, 2026-09-18) | ✅ Validated (NVFP4 on GB300) | loading, generation, reasoning/tool-call parsing, image smoke tests ([NVFP4 card](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4)) |
| **TokenSpeed** | no version number published ⚠️ | ✅ **Ships a DeepSeek-V4.1-Flash recipe** | `tokenspeed serve deepseek-ai/DeepSeek-V4.1-Flash --tensor-parallel-size 8 --enable-expert-parallel --moe-backend marlin --dtype bfloat16 --max-model-len 32768 --disable-prefill-graph --disable-kvstore` (+ `--speculative-algorithm DSPARK`). Dedicated **FlatKV** four-group KV backend (global KV chains / SWA rows / compressor tails, the last two *replayable* — SWA bounded replay); the CED decoder forces `--disable-prefill-graph`. Per-commit **GB300 Slurm 1P1D** CI gated at GSM8K ≥ 0.90 — [inference-engines.md §2.5](../../cross-cutting/inference-engines.md), [src](https://lightseek.org/tokenspeed/recipes/models) |
| **transformers** | declares `5.6.0` | ⚠️ Config declares it; **no verified serving run found** | ⚠️ TO BE VERIFIED |
| **TensorRT-LLM** | latest **1.2.1** stable / **1.3.0rc27** pre-release (re-verified 2026-09-19 via PyPI) | ⚠️ **No V4.1-Flash support found as of 2026-09-19** | DeepSeek-**V4** is supported, V4.1 is absent; NVIDIA's own NVFP4 card lists only *"vLLM, SGLang"* as supported runtimes — [inference-engines.md §2.3](../../cross-cutting/inference-engines.md) |
| **Dynamo** | — | ⚠️ **No V4.1-Flash recipe found** | vLLM's own PD-disaggregation path (`vllm-router --vllm-pd-disaggregation` + NIXL) is the documented one |
| **vLLM-Ascend** | — | ✅ Documented, not perf-qualified | [Ascend tutorial](https://docs.vllm.ai/projects/ascend/en/latest/tutorials/models/DeepSeek-V4.1-Flash.html) |
| **ExLlamaV3 (EXL3)** | — | ✅ Community, 2.0–3.5 bpw | §9 |
| **llama.cpp / GGUF** | — | ⚠️ GGUFs exist but for **DwarfStar**, a separate Metal engine | [antirez/deepseek-v4.1-flash-gguf](https://huggingface.co/antirez/deepseek-v4.1-flash-gguf) |
| **DeepSeek reference impl** | `torch>=2.10.0`, `tilelang==0.1.8` | ✅ Readable reference, **not a serving engine** | [`inference/README.md`](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/inference/README.md) |

**No pip wheel serves this architecture** — Docker is mandatory:
*"Serve from the image for your GPU vendor — no pip wheel carries the DeepSeek-V4.1
architecture"* ([vLLM recipe schema](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash)).
NVIDIA: `vllm/vllm-openai:nightly`. AMD: `vllm/vllm-openai-rocm:nightly`.

### 8.2 vLLM hardware verification status (vendor's own table)

From the recipes index payload, verbatim:
`"hardware":{"h100":"verified","h200":"verified","b200":"verified","gb200":"verified","gb300":"verified","b300":"verified","mi350x":"verified"}`
([recipes.vllm.ai](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash)),
`"precisions":["fp8"]`, `"default_hardware":"gb200"`, `"difficulty":"advanced"`.

**A100 and RTX PRO 6000 are absent from vLLM's verified list.** Both have community recipes
only (§8.5).

### 8.3 Base launch command and defaults

Always-on base args and env
([vLLM recipe schema](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash)):

```
--tokenizer-mode deepseek_v41
VLLM_ENGINE_READY_TIMEOUT_S=3600      # "Expect a long first load"
```

Feature flags:

| Feature | Args |
|---|---|
| Tool calling (DSML) | `--tool-call-parser deepseek_v41 --enable-auto-tool-choice` |
| Reasoning | `--reasoning-parser deepseek_v41` |
| Text-only | `--language-model-only` |
| Encoder data-parallel | `--mm-encoder-tp-mode data` (mutually exclusive with text-only) |
| DSpark | see §7.4 |

Default TP per `strategy_overrides.single_node_tp`: **`{"default": 4, "h100": 8, "b300": 2}`**.

### 8.4 Per-GPU recipes and caveats

| GPU | TP | Required overrides | Caveats |
|---|---|---|---|
| **H100 SXM 80 GB** | **8** | `--engram-config '{"cpu_offload":true}'`, `--max-num-batched-tokens 4096`, `--gpu-memory-utilization 0.92`, `VLLM_USE_V2_MODEL_RUNNER=1`, `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` | 8×80 = 640 GB; weights alone are 510 GB, so **the 188.8 GiB Engram table must go to host** — that is why `cpu_offload` is mandatory here. No FP4 tensor cores: MXFP4 experts must be upconverted. |
| **H200 SXM 141 GB** | 4 (default) | none beyond base | 8×141 = 1,128 GB — *"fits one 8-GPU H200 node with room for KV cache"*. Engram stays resident. |
| **B200 180 GB** | 4 (default) | none beyond base | Native NVFP4/MXFP4 tensor cores. |
| **B300 HGX / DGX / p6-b300, 268 GB as deployed** | **2** | `--engram-config '{"cpu_offload":true}'`, explicit `--compilation-config` with 26 cudagraph capture sizes, `--max-cudagraph-capture-size 8190`, `--max-num-batched-tokens 8192`, `--max-num-seqs 256` | **TP2 is the default** (2 of 8 GPUs = **536 GB**, not 576 — METHODOLOGY §8 pins HGX/DGX/p6-b300 at **268 GB/GPU as deployed**, 2,144 GB per node). *"For high interactivity, deploy the model using TP4"*. At TP2, `usable_hbm = 536 × 0.90 = 482 GB` is **27.9 GB short of the 510.29 GB checkpoint**, so `cpu_offload` is not merely advisable here, it is arithmetically required. |
| **GB300 NVL72, 288 GB/GPU (≈279 usable)** | 4 | none beyond base; NVFP4 variant validated here | NVIDIA's NVFP4 checkpoint was tested on **four GB300** with both vLLM and SGLang ([NVFP4 card](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4)). **Do not merge with the HGX B300 row** — GB300 NVL72 is a distinct, higher-clocked part at 288 GB/GPU (METHODOLOGY §8). |
| **GB200 NVL4** | 4 | PD-disagg: `--kernel-config` (disables FlashInfer autotune/JIT/CuTeDSL warmup), `VLLM_DEEP_GEMM_WARMUP=skip`, `--max-num-seqs 32` | vLLM's `default_hardware`. One tray = 768 GB, fits TP4. The verified 1P1D runs were **text-only**. |
| **MI355X / MI350X (gfx950)** | 4 | `--moe-backend aiter`, `--gpu-memory-utilization 0.9`, `VLLM_ROCM_USE_AITER=1`, `VLLM_ROCM_USE_AITER_MOE=1`, `VLLM_USE_BREAKABLE_CUDAGRAPH=1`, `AITER_TRITON_LOG_LEVEL=ERROR` | See §8.5 |
| **A100 / A800 80 GB** | community: TP4×DP2, EP8 | community backport | **Not vLLM-verified.** See §8.5 |
| **RTX PRO 6000 Blackwell 96 GB** | community: TP2 @ 2.0 bpw EXL3 | community sm_120 patches | **Not vLLM-verified.** See §8.5 |

### 8.5 Known open issues, per vendor

**AMD / ROCm** ([vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash)):

- **Adaptive verification is refused.** Two checks in `maybe_create_adaptive_verification_manager`
  fail: `DeepseekV41IndexerBackend.supports_device_cpu_query_lens_mismatch()` is `False`, and
  `DeepseekV41ROCMAiterSparseSWABackend` reports `AttentionCGSupport.UNIFORM_BATCH` rather than
  `ALWAYS`. Set `enable_adaptive_verification:false`. DSpark still drafts 5 tokens/round.
- **`torch.compile` is unsupported for this model**, and the ROCm sparse-SWA backend supports
  only uniform-batch CUDA graphs, so `VLLM_USE_BREAKABLE_CUDAGRAPH=1` is required — *"Without
  breakable CUDA graphs, default `FULL_AND_PIECEWISE` dies at capture."*
- `--moe-backend aiter` (plain name) selects **Composable Kernel a8w4** experts; naming
  `aiter_triton_mxfp4_bf16` instead pins the slower Triton W4A16 `_moe_gemm_a16w4` kernel.
  Measured difference in §10.
- Requires [PR #56503](https://github.com/vllm-project/vllm/pull/56503), which moves the mHC
  delayed-pre block off the eager Torch reference onto AITER.

**NVIDIA:**

- FlashInfer autotune/JIT can be expensive or fail at startup on some builds — GB200 PD recipe
  disables it via `--kernel-config`; `--no-enable-flashinfer-autotune` disables it wholesale.
- Community GB10 (DGX Spark) report: adaptive verification disabled because
  *"padded batches can hang SM120 sparse MLA"*; indexer needed 64-state pages for DeepGEMM
  compatibility; `--block-size 128` required (32/64 rejected)
  ([DGX Spark repo](https://github.com/tonyd2wild/DeepSeek-V4.1-Flash-vLLM-DGX-Spark)).

**A100 / A800 (SM80)** — community only
([StellarVoyager card](https://huggingface.co/StellarVoyager/DeepSeek-V4.1-Flash-A100-A800-SM80),
[GitHub](https://github.com/Tokha233/deepseek-v4.1-flash-a100-turbo)):

> `8× A800 80 GB · TP4×DP2 · EP8 · DSpark5 / FP4 Marlin experts · BF16 dense dispatch · FP8 MLA
> KV / FULL_DECODE_ONLY CUDA Graphs · GPU prefix cache · 1M context`

Built on [`wtdcode/vllm-backport`](https://github.com/wtdcode/vllm-backport/tree/master-v013)
with *"Ampere-specific compatibility and performance patches … Blackwell-only kernels are not
assumed to run on SM80."* MXFP4 experts run through **Marlin** (dequant-to-BF16 W4A16), so
Ampere gets the *capacity* benefit of FP4 weights but none of the FLOPs benefit. Note the
measurements are on **A800**, not A100: *"A100 uses the same SM80 execution path but has not
been independently re-measured here."*

**RTX PRO 6000 Blackwell (sm_120)** — community only
([diffbot card](https://huggingface.co/diffbot/DeepSeek-V4.1-Flash-EXL3-2.0bpw-2x-RTX-PRO-6000)):
requires sm_120 fixes for vLLM *and* FlashInfer plus a custom EXL3 MoE kernel; the full
checkpoint does not fit 2×96 GB, so this path runs a **2.0 bpw EXL3 requantisation**.

**Ascend** ([vLLM-Ascend tutorial](https://docs.vllm.ai/projects/ascend/en/latest/tutorials/models/DeepSeek-V4.1-Flash.html)):
Atlas 800 A3 (2 servers) or A2 (4 servers), `DP4/TP8/EP32`, `--quantization ascend` (W8A8 +
**INT8 Engram storage**), `--compilation-config '{"cudagraph_mode":"FULL_DECODE_ONLY"}'`,
DSpark with `enforce_eager:true`. Explicitly *"No production performance baseline is published
for this configuration"* and *"Production performance qualification and task-level accuracy
evaluation are not complete."*

### 8.6 Launch recipes, quoted verbatim

**SGLang, NVFP4 on 4× GB300** ([NVFP4 card](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4)):

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
*"The tested build automatically selects `flashinfer_trtllm_routed` for the NVFP4 experts.
Include a request-level `reasoning_effort`, such as `"max"`, to enable thinking; thinking is
off by default in this SGLang build."*

**vLLM, NVFP4 on 4× GB300** (same source, image `vllm/vllm-openai:deepseekv41-flash-0909`):

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

**vLLM, 4× MI355X benchmark server** ([vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash),
matching [InferenceX #3058](https://github.com/SemiAnalysisAI/InferenceX/pull/3058) at commit `559ef756`):

```bash
export HIP_VISIBLE_DEVICES="$ROCR_VISIBLE_DEVICES"
export VLLM_ROCM_USE_AITER=1 VLLM_ROCM_USE_AITER_MOE=1
export AITER_TRITON_LOG_LEVEL=ERROR VLLM_USE_BREAKABLE_CUDAGRAPH=1
export OMP_NUM_THREADS=1 VLLM_ENGINE_READY_TIMEOUT_S=3600
export VLLM_USE_RUST_FRONTEND=1 PYTHONUNBUFFERED=1 GPU_COUNT=4
MODEL=deepseek-ai/DeepSeek-V4.1-Flash
SPEC_CONFIG='{"method":"dspark","num_speculative_tokens":5,"draft_sample_method":"probabilistic","rejection_sample_method":"block","enable_adaptive_verification":false}'
# ^ this is the recipe's EVAL_ONLY=true arm (real block rejection; also what vLLM
#   recommends for ordinary serving). Its throughput-benchmark arm instead uses
#   "rejection_sample_method":"synthetic","synthetic_acceptance_length":3.51 —
#   a benchmark constant, not a measurement. [vLLM recipe, verified 2026-09-19]
exec vllm serve "$MODEL" --served-model-name "$MODEL" \
  --host 0.0.0.0 --port "$PORT" --tensor-parallel-size 4 \
  --language-model-only \
  --tokenizer-mode deepseek_v41 \
  --tool-call-parser deepseek_v41 --enable-auto-tool-choice \
  --reasoning-parser deepseek_v41 \
  --moe-backend aiter \
  --gpu-memory-utilization 0.9 \
  --speculative-config "$SPEC_CONFIG" \
  --max-model-len 1048576 \
  --max-num-seqs 128 \
  --max-cudagraph-capture-size 1024 \
  --max-num-batched-tokens 16384 \
  --disable-uvicorn-access-log
```
Docker image pinned to `vllm/vllm-openai-rocm:nightly-eed1f3d0c6043bd494424a22443ee198dd56f657`.

**vLLM-Ascend, Atlas 800 A3, 2 servers**
([tutorial](https://docs.vllm.ai/projects/ascend/en/latest/tutorials/models/DeepSeek-V4.1-Flash.html)):

```bash
vllm serve "$MODEL_PATH" \
  --data-parallel-size 4 --data-parallel-size-local 2 \
  --tensor-parallel-size 8 --enable-expert-parallel \
  --max-model-len 1048576 --max-num-batched-tokens 4096 \
  --quantization ascend \
  --speculative-config '{"method":"dspark","num_speculative_tokens":5,"enforce_eager":true}' \
  --compilation-config '{"cudagraph_mode":"FULL_DECODE_ONLY"}'
```

**Reference implementation, TP8**
([`inference/README.md`](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/inference/README.md)):

```bash
python convert.py --hf-ckpt-path "${HF_CKPT_PATH}" --save-path "${SAVE_PATH}" \
  --model-parallel 8 --expert-dtype fp4 --tokenizer-path "${HF_CKPT_PATH}"
torchrun --nproc-per-node 8 generate.py --ckpt-path "${CKPT_PATH}" --config config.json --interactive
```

### 8.7 Kernel stack

DeepSeek's production kernels (tech report §3.2), none of which is FlashAttention:

> *"the fused-RoPE-attention-RoPE-cast kernel in **FlashMLA**, the **Mega-Gate, Mega-mHC, and
> Mega-MoE** kernels in **DeepGEMM**, the kernels in **TileKernels**, and the TopK kernel in
> **DeepSelect**."*

The open reference implementation uses **TileLang 0.1.8** (`tilelang.jit`) for
`act_quant`, `fp4_act_quant`, `fp8_gemm`, `fp4_gemm`, `hc_split_sinkhorn` and `sparse_attn`,
with `TL_DISABLE_WARP_SPECIALIZED` and `TL_DISABLE_TMA_LOWER` set
([`inference/kernel.py`](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/inference/kernel.py)).

**⚠️ TO BE VERIFIED — flash-attention kernel support.** This model's attention is *not* a
FlashAttention-shaped problem: it is a gather-based `sparse_attn(q, kv, sink, topk_idxs, scale)`
over 640 selected entries with a learned attention sink. I found **no evidence** that
`flash-attn` (FA2/FA3/FA4) supports this operator on any GPU as of 2026-09-19. The kernels
actually observed in the wild are:

| Path | Kernel | Source |
|---|---|---|
| NVIDIA vLLM | `DeepseekV41IndexerBackend`, sparse SWA backend, DeepGEMM, FlashInfer (`trtllm_fp4_block_scale_moe` for NVFP4) | [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash) |
| AMD vLLM | `DeepseekV41ROCMAiterSparseSWABackend`, AITER CK a8w4 MoE | [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash) |
| A100 community | Marlin W4A16 experts, FP8 MLA KV, SM80 candidate MQA | [A100 recipe](https://huggingface.co/StellarVoyager/DeepSeek-V4.1-Flash-A100-A800-SM80) |
| RTX PRO 6000 community | Marlin dense GEMM + custom `xmoe` EXL3 MoE kernel | [diffbot card](https://huggingface.co/diffbot/DeepSeek-V4.1-Flash-EXL3-2.0bpw-2x-RTX-PRO-6000) |

---

## 9. Available quantised variants

| Repo | Format | Size | Validated by | Evals |
|---|---|---|---|---|
| [`deepseek-ai/DeepSeek-V4.1-Flash`](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash) | **MXFP4 experts + FP8 dense/engram + BF16 embed** (native) | 510.31 GB | DeepSeek | model card §Evaluation |
| [`nvidia/DeepSeek-V4.1-Flash-NVFP4`](https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4) | **NVFP4 W4A4 g16** routed experts only; attention/shared/vision/Engram/MTP keep source precision | **527.31 GB** (48 shards) | **NVIDIA**, ModelOpt v0.47.0rc0, tested on **GB300** with vLLM + SGLang | see below |
| [`lvkaokao/…-W4A16-Engram-AutoRound`](https://huggingface.co/lvkaokao/DeepSeek-V4.1-Flash-W4A16-Engram-AutoRound) | INT4 g32 sym (auto_gptq) experts **+ INT4 engram**; dense dequantised to BF16 | **451.7 GB** | Intel AutoRound 0.15.0, verified on **4×H200 TP4** | GSM8K 93.93/94.01 vs baseline 92.87 — *"lossless within noise"* |
| [`lvkaokao/…-MXFP4-Engram-AutoRound`](https://huggingface.co/lvkaokao/DeepSeek-V4.1-Flash-MXFP4-Engram-AutoRound) | MXFP4 incl. engram | ⚠️ not fetched | Intel | ⚠️ |
| [`diffbot/…-EXL3-2.0bpw-2x-RTX-PRO-6000`](https://huggingface.co/diffbot/DeepSeek-V4.1-Flash-EXL3-2.0bpw-2x-RTX-PRO-6000) | EXL3 2.0 bpw routed experts | fits 2×96 GB; 358 GB download | community | HumanEval **91.5 %**; GSM8K-200 **97.5 %** (the card's headline "quality of this pack with thinking off") / **98.5 %** (default-config row of the config table, *"a single run per configuration"*) |
| [`Mia-AiLab/…-EXL3-2.9bpw`](https://huggingface.co/Mia-AiLab/DeepSeek-V4.1-Flash-EXL3-2.9bpw) / [`-3.0bpw`](https://huggingface.co/Mia-AiLab/DeepSeek-V4.1-Flash-EXL3-3.0bpw) | EXL3 | ⚠️ | community | ⚠️ |
| [`coolbho3k/…-EXL3-3bpw`](https://huggingface.co/coolbho3k/DeepSeek-V4.1-Flash-EXL3-3bpw), [`-DSpark-EXL3-3bpw`](https://huggingface.co/coolbho3k/DeepSeek-V4.1-Flash-DSpark-EXL3-3bpw) | EXL3 3 bpw ± drafter | ⚠️ | community | ⚠️ |
| [`antirez/deepseek-v4.1-flash-gguf`](https://huggingface.co/antirez/deepseek-v4.1-flash-gguf) | GGUF for **DwarfStar** (Metal), Q2 and Q4 | Q2 **340.60 GiB**, Q4 **482.98 GiB**, vision 0.90 GiB | community (antirez) | none published |
| [`RedHatAI/DeepSeek-V4.1-Flash`](https://huggingface.co/RedHatAI/DeepSeek-V4.1-Flash) | mirror of native FP8/MXFP4 | 510 GB | Red Hat AI | card is a verbatim copy of DeepSeek's |
| [`LibertAIDAI/…-REAP-256E`](https://huggingface.co/LibertAIDAI/DeepSeek-V4.1-Flash-REAP-256E), [`-272E`](https://huggingface.co/LibertAIDAI/DeepSeek-V4.1-Flash-REAP-272E) | **Expert pruning** 384 → 256 / 272 experts | ⚠️ | community | ⚠️ — no evals found |
| [`mlx-community/…-MLX-4bit`](https://huggingface.co/mlx-community/DeepSeek-V4.1-Flash-MLX-4bit), `-MLX-2bit`, `-DSpark-drafter` | MLX | ⚠️ | community | ⚠️ |
| [`StellarVoyager/…-A100-A800-SM80`](https://huggingface.co/StellarVoyager/DeepSeek-V4.1-Flash-A100-A800-SM80) | **Not weights** — a recipe repo; uses official checkpoint | — | community | §10 |

### 9.1 NVFP4 conversion details and accuracy

NVIDIA converted only the 384 ordinary routed experts across 40 layers (`w1`, `w2`, `w3`)
from source MXFP4 to NVFP4 W4A4 g16. *"Attention, shared experts, vision, Engram lookup
tables, MTP/DSpark, and other excluded components retain their source precision."*
*"All 16,986,931,200 weight blocks passed lossless conversion … 18 of 46,080 projection
entries used fallback scales. Lossless weight conversion does not imply identical inference
outputs."*

Accuracy, vLLM, `temperature=1.0 top_p=0.95 reasoning_effort=100 max_new_tokens=262144`
(GPQA/AA-LCR 16 repeats, IFBench 5, Terminal-Bench 8 trajectories/task):

| Precision | GPQA Diamond | AA-LCR | SciCode | IFBench | MMMU-Pro | Terminal-Bench 2.1 |
|---|---:|---:|---:|---:|---:|---:|
| MXFP4 (source) | 91.035 | 78.563 | 54.401 | 76.667 | 74.046 | 81.60 |
| **NVFP4** | **91.288** | 78.438 | **55.843** | **77.267** | 73.699 | **82.16** |

Within noise on every task — NVFP4 conversion is **accuracy-neutral** here, which is expected
given the source is already 4-bit. The reason to use it is Blackwell's NVFP4 tensor-core
path, at the cost of +17 GB of checkpoint.

**⚠️ TO BE VERIFIED:** DSpark was **not** exercised in NVIDIA's validation
(*"DSpark tensors are preserved, but speculative decoding was not exercised in the reported
validation"*), and the vLLM example is text-only.

---

## 10. Published benchmarks

> **Caveat up front.** Neither DeepSeek's tech report nor its model card publishes a single
> tokens/s, TTFT or TPOT figure. NVIDIA's NVFP4 card publishes accuracy only. **There is no
> MLPerf Inference result for DeepSeek-V4.1-Flash as of 2026-09-19** — the model is 9 days
> old. Every performance number below is either a vendor-adjacent benchmark harness
> (InferenceX/SemiAnalysis) or a community measurement, and each is labelled.

### 10.1 AMD MI355X — vLLM ROCm, TP4, 131K context

From [InferenceX #3058](https://github.com/SemiAnalysisAI/InferenceX/pull/3058) (SemiAnalysis),
measured with `AIPERF_EXPERIMENTAL_FAST=1` (20-minute window — *"absolute numbers aren't
comparable to full-duration runs, only the deltas matter"*). Triton W4A16 → Composable Kernel
a8w4 experts:

| Concurrency | MoE GEMM ms/step | Whole step ms | Gain |
|---:|---|---|---:|
| 1 | 2.72 → 2.30 | 19.11 → 18.31 | 4.2 % |
| 4 | 4.91 → 4.27 | 19.42 → 18.40 | 5.3 % |
| 16 | 9.06 → 7.18 | 28.38 → 25.92 | 8.7 % |

AgentX latency at concurrency 1:

| Metric | Triton | CK | Gain |
|---|---:|---:|---:|
| ITL mean | 4.01 ms | 3.91 ms | 2.5 % |
| TTFT mean | 681 ms | 639 ms | 6.2 % |
| E2E mean | 4.48 s | 4.32 s | 3.6 % |

Accuracy unchanged: `gsm8k strict-match 0.9719 ± 0.0045`, `flexible-extract 0.9712 ± 0.0046`.
Sweep concurrencies 1/2/4/8/16/32, GPU-resident KV, DSpark **synthetic** acceptance length
3.51 (`rejection_sample_method: "synthetic"`, `synthetic_acceptance_length: 3.51`) — a
benchmark constant the harness is *told* to assume, **not a measured acceptance rate** (§7.3).

> **Sanity check against my model:** whole step ≈18.3 ms at batch 1 on 4×MI355X. My §6.3
> weight-read estimate at batch 1 is 23.26 GB. MI355X is **8.0 TB/s per GPU**
> (METHODOLOGY §8), so 4 GPUs at TP4 read 23.26/4 = 5.82 GB each in parallel — an
> **aggregate 32 TB/s**, not 8. At MBU 0.5 that predicts **≈1.45 ms** (1.82 ms at MBU 0.4,
> 1.21 ms at MBU 0.6); KV adds ~34 MiB at batch 1 × 131 K, i.e. nothing. The measured 18.3 ms
> is therefore **≈12.6× my roofline**, not the ≈3× printed here before the 2026-09-19 sweep,
> which had used 8 TB/s as the *aggregate* of four 8 TB/s GPUs. The gap must come from
> non-overlapped Engram gathers, the indexer scan, DSpark draft+verify, or a far lower MBU.
> **⚠️ TO BE VERIFIED** — do not plan from my roofline alone on ROCm.

### 10.2 2× RTX PRO 6000 Blackwell Max-Q (300 W, PCIe, TP2) — EXL3 2.0 bpw

[diffbot card](https://huggingface.co/diffbot/DeepSeek-V4.1-Flash-EXL3-2.0bpw-2x-RTX-PRO-6000),
8 GiB KV cache, 512K context window, CUDA graphs, DSpark on. Community measurement.
**This is a 2.0 bpw requantisation, not the released checkpoint.**

| Configuration | Decode 2K, 1 stream | Decode 2K, 4 streams | Prefill 46K, 1 stream | Decode 46K, 1 stream | Prefill 46K, 4 streams | GSM8K-200 |
|---|---:|---:|---:|---:|---:|---:|
| sm_120 prefill fixes only (eager, Engram on NVMe) | | | 1,805 | 31.5 | | 97.5 % |
| + CUDA graphs, Engram in pinned RAM | | | 4,026 | 68.0 | | 97.5 % |
| + Marlin dense GEMMs | 101.6 | 210.2 | 3,804 | 90.5 | 7,617 | 97.0 % |
| + multi-row prefill MoE kernel | 98.9 | 219.7 | 4,091 | 96.7 | 8,180 | 98.0 % |
| **+ flat decode MoE scheduler (default)** | **113.3** | **277.2** | **4,044** | **116.6** | **8,085** | **98.5 %** |
| + hybrid dense GEMM (opt-in) | 114.1 | 274.4 | 4,260 | 114.9 | 8,503 | 95.5 % |

Real agentic coding workload, 40 min: 184 requests, **18.5M prompt tokens, 87 % served from
prefix cache**, decode 240–254 tok/s across 3–4 streams, no errors or preemptions.

MoE kernel microbenchmark (384 experts, hidden 5120, intermediate 1152 per rank, 2 bpw),
stock ExLlamaV3 `exl3_moe` vs custom `xmoe`:

| Tokens/call | Stock | xmoe | Speedup (uniform routing) | (skewed) |
|---:|---:|---:|---:|---:|
| 6 (DSpark decode, 1 stream) | 523.6 µs | 327.1 µs | 1.60× | 1.95× |
| 12 | 771.3 µs | 564.0 µs | 1.37× | 1.58× |
| 24 | 1,482 µs | 1,066 µs | 1.39× | 1.45× |
| 48 | 2,502 µs | 1,999 µs | 1.25× | 1.32× |
| 1,542 | 9.00 ms | 5.98 ms | 1.51× | |
| 4,096 | 20.4 ms | 14.3 ms | 1.42× | |

In-server effect: MoE share of a decode step **16.1 ms → 8.9 ms**, whole step **28.9 → 23.5 ms**.

> Note the **Engram placement effect in isolation**: moving Engram from NVMe to pinned RAM
> (together with enabling CUDA graphs) took 46K decode from 31.5 → 68.0 tok/s, a **2.16×**.

### 10.3 8× A800-SXM4-80GB (SM80) — community vLLM backport

[StellarVoyager card](https://huggingface.co/StellarVoyager/DeepSeek-V4.1-Flash-A100-A800-SM80).
TP4×DP2, EP8, DSpark5, FP4 Marlin experts, BF16 dense dispatch, FP8 MLA KV, 1M context.

| Workload | Result | Conditions |
|---|---:|---|
| Short coding decode | **3,040 tok/s** | C128, 78–90 input tokens, effort 100, 1,024 output tokens |
| Long-context agent replay | **1,945 tok/s** | C128, 1M service limit, natural EOS, sustained 600 s |
| SWE-bench Verified | **87/100 (87.0 %)** | effort 75, fixed seed42 subset, environment re-evaluation |

A separate effort-100 run over all 500 SWE-bench tasks recorded **406/500 raw, 417/500 after
declared environment re-evaluation**. **Measured on A800, not A100** — A100 uses the same SM80
path but *"has not been independently re-measured here."*

### 10.4 4× NVIDIA DGX Spark (GB10, SM 12.1) — community

[tonyd2wild repo](https://github.com/tonyd2wild/DeepSeek-V4.1-Flash-vLLM-DGX-Spark), vLLM TP4,
ConnectX-7 RoCE, 81.58 GiB weights + 5.15 GiB KV per Spark, Engram rows on node-local NVMe
(48 GB/worker). Included because it is the only public dataset isolating the Engram I/O path.

| Concurrency | Aggregate tok/s | Per-stream decode tok/s | Mean TTFT |
|---|---:|---:|---:|
| C1 | 37.95 | 43.12 | 0.441 s |
| C6 | **131.86** | 25.35 | 0.502 s |

Single-stream decode by category: counting 92.2, code 73.8, tables 55.4, math 50.9,
reasoning 37.8, prose 24.4 tok/s. Cold prefill 902–1,539 tok/s (32K prompt 1,539; 64K 1,194;
93K-token prompt at 1,194 tok/s over 78.17 s). KV pool 1,070,168 tokens; 1M context proven on
a separate boot (1,078,380-token pool).

**Engram I/O, isolated:** *"the workers read their Engram rows from the head's NFS export.
That took 5.9-7.8 ms per step against **2.8 ms** on the head, and every step waited for the
slowest rank"* — so the node-local figure is **2.8 ms**, not the "2–3 ms" range printed here
earlier. Moving to local rows took code decode C1 from 52.4 → **73.8 tok/s** and C6 from
26.3 → **41.5 tok/s**.

Top-k kernel fix for GB10 (`top_k_per_row_decode`) gave **1.6–3.6×** and avoided a 128 KB
shared-memory requirement. Known issue: *"The GPU switches between a fast and a slow state
that nvidia-smi does not show"* — 63 vs 94 ms/step, reproduced in plain PyTorch, isolated to
GPU firmware.

### 10.5 Model quality (for cost-per-quality comparisons)

From the [model card](https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash), max reasoning
effort, `temperature=1.0 top_p=0.95`:

| Benchmark | Opus-5.0 | GPT-5.6 Sol | K3 | DS-V4-Pro | **DS-V4.1-Flash** |
|---|---:|---:|---:|---:|---:|
| GPQA Diamond | 93.4 | **94.1** | 92.9 | 92.4 | 90.9 |
| Terminal-Bench 2.1 | 89.1 | 88.8 | 88.3 | 87.9 | **90.6** |
| DeepSWE v1.1 | 74.0 | 73.0 | 67.5 | 62.7 | **74.2** |
| Codeforces (rating) | — | — | — | 3348 | **3471** |
| HLE w/ tools | 63.6 | — | 59.8 | 60.0 | **63.9** |
| AutomationBench | 50.3 | 45.8 | 46.7 | 43.2 | **54.8** |
| Agent's Last Exam | 28.6 | 26.7 | 27.6 | 25.7 | **31.8** |
| Terminal-Bench 4.0 | **51.8** | 39.9 | 12.6 | 12.4 | 31.2 |
| HLE (Pass@1) | **56.3** | 44.5 | 43.5 | 42.7† | 36.8 (39.1†) |

**Reasoning-effort cost curve** (tech report §5.3.2), directly relevant to cost planning:
raising effort 25 → 100 lifts 8-benchmark average Pass@1 **67.1 → 76.3 %**, DeepSWE
**66.0 → 74.2 %**, Terminal-Bench 2.1 **82.4 → 90.6 %**, at *"roughly 2.5× more output
tokens."* And: *"the 60–80 range already recovers most of the accuracy of the maximum setting
at less than half of its token budget, whereas the final step to effort 100 lengthens agent
trajectories by 1.6–1.8× for only marginal improvements."*

> **Planning rule:** for agentic workloads, effort 60–80 is the cost-efficient operating
> point. Effort 100 costs roughly 2.5× the output tokens of effort 25 for the last ~9 points
> of Pass@1.

---

## 11. Vendor API pricing

DeepSeek first-party, model name **`deepseek-flash`** (= DeepSeek-V4.1-Flash), per 1M tokens
([DeepSeek API pricing](https://api-docs.deepseek.com/quick_start/pricing)):

| | Off-peak | Peak |
|---|---:|---:|
| **Input, cache hit** | **$0.003** | $0.006 |
| **Input, cache miss** | **$0.15** | $0.30 |
| **Output** | **$0.60** | $1.20 |

Context 1M, max output **384K**, concurrency limit **2,500**. Vision ✓, tool calls ✓,
JSON output ✓, Responses API ✓, Anthropic-format API ✓, FIM (non-thinking only) ✓.
Peak hours are 01:00–04:00 and 06:00–10:00 UTC Mon–Fri excluding Chinese public holidays;
off-peak rates are exactly half. Legacy names `deepseek-v4-flash` and
`deepseek-v4-flash-vision-exp` now route here and bill at Flash prices.

Comparison anchor, `deepseek-v4-pro` (same page): cache hit $0.022/$0.044, cache miss
$0.66/$1.32, output $1.98/$3.96 — **V4.1-Flash is 3.3× cheaper on output**, and its
concurrency limit is 5× higher (2,500 vs 500).

**Price disagreement to be aware of (noted 2026-09-19, both sources cited per METHODOLOGY §8).**
[`cross-cutting/serving-optimizations.md` §1.5](../../cross-cutting/serving-optimizations.md)
lists **DeepSeek-V4-Flash cache-miss input at $0.14/M** from `deepseek.ai/pricing`, against the
**$0.15/M** on this page. Both were re-fetched today and both still say what they say. The
figures are not for the same listing: `deepseek.ai/pricing` still carries the **retired**
`deepseek-v4-flash` product row, while `api-docs.deepseek.com` prices the live
`deepseek-flash` model (= DeepSeek-V4.1-Flash), which is what requests to the legacy name are
now billed at. **Use $0.15 for anything about this model**; the $0.14 row is a legacy listing.
Output ($0.60/M off-peak) and cache-hit ($0.003/M) agree exactly across the two pages.

### 11.1 What the cache-hit price implies

The cache-hit price is **1/50th of the cache-miss price** ($0.003 vs $0.15 off-peak). That is
a much steeper discount than METHODOLOGY §6's default assumption of *"cached input tokens cost
~10 % of uncached"* — **use 2 % for this model** when modelling DeepSeek-priced workloads.
The architecture explains it: a cache hit loads 890 B/token of global KV and replays only
`n_win = 128` tokens for SWA (SWA Bounded Replay), so a hit really is almost pure I/O.

### 11.2 Cross-checks against self-hosting

- A 1M-token cached prefix costs **$0.003** on DeepSeek's API and occupies **890 MiB** (933 MB) of KV.
- Output at $0.60/1M off-peak. To beat that self-hosted you need
  `aggregate_output_tokens_per_s ≥ cost_per_hour / (0.60 × 3600 / 1e6)` =
  `cost_per_hour / 0.00216`.

  The notional "$2/GPU-hr" this section used before the 2026-09-19 sweep is replaced by
  sourced on-demand rows from
  [`cross-cutting/cloud-pricing.md`](../../cross-cutting/cloud-pricing.md) §5 (recomputed with
  `python3`; each is the *whole node*, so the break-even is an aggregate across all its GPUs):

  | Node | Provider (cheapest reputable / hyperscaler) | $/GPU-hr | $/hr | Break-even output tok/s |
  |---|---|---:|---:|---:|
  | 8× H200 SXM | Hyperstack | $3.99 | $31.92 | **14,778** |
  | 8× H200 SXM | OCI | $10.00 | $80.00 | 37,037 |
  | 8× B200 HGX | Hyperstack | $6.00 | $48.00 | **22,222** |
  | 8× B200 HGX | OCI | $14.00 | $112.00 | 51,852 |
  | 8× B300 HGX | Hyperstack | $7.40 | $59.20 | **27,407** |
  | 8× B300 HGX | OCI | $15.00 | $120.00 | 55,556 |
  | 4× GB300 NVL72 slice | OCI (only published on-demand GB300 price anywhere) | $18.00 | $72.00 | 33,333 |
  | 8× MI355X | OCI (only published on-demand MI355X price) | $8.60 | $68.80 | 31,852 |

  Note the price rows are **not interchangeable between GPUs**: $7.40 is Hyperstack's **HGX
  B300** rate and is *not* a GB300 price (GB300 is OCI $18.00); $8.60 is OCI's **MI355X**
  rate and is *not* an MI300X price (MI300X is $6.00). At peak DeepSeek rates ($1.20/M output)
  every break-even above halves.

  The A800 measurement in §10.3 (3,040 tok/s on 8 GPUs, short-output coding) sits an order of
  magnitude below even the cheapest row; a Blackwell node at high concurrency is the only
  regime where self-hosting can win on output price alone. Priced per-(model, GPU) comparisons
  are out of scope for this architecture document — they belong in
  `research/models/deepseek41f/<gpu>.md`, written in the next phase.

**⚠️ TO BE VERIFIED:** third-party *API reseller* pricing (OpenRouter, Fireworks, Together,
DeepInfra, Novita) for DeepSeek-V4.1-Flash. `cloud-pricing.md` covers GPU-hour rates, not
per-token reseller rates, and no reseller rate for this model is sourced anywhere in this
research tree. The sourced first-party and competitor per-token table is
[`serving-optimizations.md` §1.5](../../cross-cutting/serving-optimizations.md).

---

## 12. Open questions — consolidated ⚠️ TO BE VERIFIED

**Architecture**

1. Whether each decoder layer has its own `W_l^KV` / `W_l^Z` as tech-report Eq. (1) implies,
   or one shared projection at layer 20 as the released tensors show (§2.3). My reading is
   the latter, via CSA2 Reuse mode, but no sentence states it.
2. Accuracy cost of **SWA Bounded Replay**. The report says it *"barely compromises response
   quality"* and gives no number (§2.3).
3. ~~Whether the **Full-mode indexer scan** at layers 2/8/14 really scans the whole
   causally-visible range.~~ **Resolved 2026-09-19:** the tech report says so outright —
   *"The first Full Mode layer still scans the entire causally visible range"* (§2.3.2), and
   the Hierarchical Sparse Indexer is *"used only in the decoder of CED"*. What remains open
   is only whether a production engine block-prunes it anyway as an optimisation (§6.3).
4. Whether the `NgramHashState` int64 hash cache (8 MB/seq at 1M) is kept at int64,
   narrowed, or recomputed in vLLM/SGLang (§5.3).
4b. **What dtype vLLM/SGLang use for the 128-slot SWA ring buffer.** The reference
   implementation stores fp8-rounded values in a **BF16** container (default dtype is bfloat16)
   and drops the scales, so the fixed per-sequence state there is **5.38 MiB**, not the
   2.77 MiB this document plans with. 1.94× on a term that dominates below ~3–6 K context
   (§5.3, §5.4, §6.3). No engine document states it.

**Performance**

5. **No MLPerf Inference result exists** for this model (released 2026-09-10).
6. **No published throughput/latency numbers from DeepSeek or NVIDIA at all.** NVIDIA's GB300
   validation is accuracy-only. There is no sourced tok/s figure for H100, H200, B200, B300
   or GB300.
7. **No measured 1M-context decode step time on any hardware.** The vLLM V4-Flash-Vision
   recipe's warning applies here too: *"1M context is advertised, not measured."*
8. DSpark acceptance on V4.1-Flash. The only *measurement* is the DGX Spark community mean
   of **3.57** tok/step; the widely quoted **3.51 is a synthetic benchmark constant**
   (`rejection_sample_method: "synthetic"`, `synthetic_acceptance_length: 3.51`), i.e. a value
   the harness is told to assume, not one it observed (§7.3, §8.6, §10.1). DeepSeek publishes
   neither.
9. My roofline under-predicts the measured MI355X step time by **~12.6×** (§10.1, corrected
   2026-09-19 from "~3×" — the earlier figure treated 8 TB/s as the aggregate of four 8 TB/s
   GPUs instead of 32 TB/s). The discrepancy is unexplained and MBU/MFU for this architecture
   is not characterised on any GPU.

**Hardware / kernels**

10. **Does any flash-attention release support this model's `sparse_attn` operator?** I found
    no evidence for FA2/FA3/FA4 on any GPU (§8.7). The operator is a gather over 640 selected
    entries with a learned sink, which is not FlashAttention-shaped.
11. **A100**: vLLM-unverified. The only data is on **A800**, via a community backport, and
    MXFP4 runs through Marlin (W4A16), so Ampere gets no FP4 math benefit.
12. **RTX PRO 6000 Blackwell**: vLLM-unverified; the full checkpoint does not fit 2×96 GB, so
    all public data is on a 2.0 bpw EXL3 requantisation. Whether the native checkpoint runs on
    4× or 8× RTX PRO 6000 is untested.
13. **B300 at TP2** (the vLLM default). Recomputed 2026-09-19 at the METHODOLOGY §8 deployed
    capacity of **268 GB/GPU** (not 288): TP2 = 536 GB, `usable_hbm = 482 GB`, which is
    **27.9 GB short of the 510.29 GB checkpoint** — so the ~66 GB KV headroom printed here
    before the sweep (576 − 510) does not exist. With the mandatory Engram `cpu_offload`,
    resident weights drop to 307.5 GB and the KV budget is **≈163 GB (152 GiB)** after ~12 GB
    of activation workspace. What stays untested is whether the PCIe Engram path sustains that
    concurrency; the recipe itself suggests TP4 *"for high interactivity"*. Per-GPU fit and
    throughput tables live in `research/models/deepseek41f/b300.md` (next phase).
14. **Whether B300/GB300's NVFP4 path is actually faster than MXFP4 here.** NVIDIA shows
    accuracy parity and a bigger checkpoint but publishes **no speedup number** — the premise
    that "B300 can run NVFP4 so it should be faster" is **unverified for this model**.
15. **TensorRT-LLM support**: none found for V4.1 (stable 1.2.1 / pre-release 1.3.0rc27, both
    re-verified 2026-09-19); DeepSeek-**V4** is supported, V4.1 is absent. NVIDIA's own card
    lists only vLLM and SGLang.
16. **Dynamo**: no V4.1-Flash recipe found. (**TokenSpeed, by contrast, does ship one** —
    corrected 2026-09-19, §8.1; its version number, licence and cadence remain ⚠️.)
17. **transformers**: `config.json` declares `transformers_version: 5.6.0` but no verified
    `transformers`-native inference run was found; the shipped reference implementation is
    standalone PyTorch + TileLang.
18. **MXFP4-vs-NVFP4-vs-BF16 kernel throughput** for this model's expert shapes
    (5120 × 2304, 384 experts) on each target GPU — not measured anywhere public.

**Ecosystem**

19. Third-party API pricing (§11.2).
20. Evals for the REAP expert-pruned variants (384 → 256/272 experts) — none published.
21. Whether the Engram table can be safely quantised below FP8. Two community repos do INT4
    (AutoRound, `DSV41_ENGRAM_DTYPE=int4`; GSM8K reported lossless) and Ascend does INT8, but
    there is no systematic study.

---

## Sources

- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/README.md
- https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash
- https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash/tree/main?recursive=true
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/blob/main/DeepSeek_V41_Tech_Report.pdf
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/config.json
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/inference/README.md
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/inference/config.json
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/inference/run.sh
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/inference/requirements.txt
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/inference/model.py
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/inference/engram.py
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/inference/kernel.py
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/encoding/README.md
- https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/resolve/main/model-*.safetensors (48 shard headers, HTTP Range)
- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash
- https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4-Flash-Vision-Exp
- https://docs.vllm.ai/projects/ascend/en/latest/tutorials/models/DeepSeek-V4.1-Flash.html
- https://github.com/vllm-project/vllm/pull/56228
- https://github.com/vllm-project/vllm/pull/56503
- https://github.com/vllm-project/vllm/pull/56201
- https://github.com/vllm-project/vllm/pull/54566
- https://github.com/vllm-project/vllm/pull/55107
- https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4
- https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4
- https://github.com/NVIDIA/Model-Optimizer
- https://github.com/SemiAnalysisAI/InferenceX/pull/3058
- https://github.com/SemiAnalysisAI/InferenceX/blob/559ef7560a74d0fb8da64fdfc0c357ad2fc33c4f/benchmarks/single_node/agentic/dsv41flash_fp4_mi355x_vllm_mtp.sh
- https://github.com/tonyd2wild/DeepSeek-V4.1-Flash-vLLM-DGX-Spark
- https://huggingface.co/StellarVoyager/DeepSeek-V4.1-Flash-A100-A800-SM80
- https://github.com/Tokha233/deepseek-v4.1-flash-a100-turbo
- https://github.com/wtdcode/vllm-backport/tree/master-v013
- https://huggingface.co/diffbot/DeepSeek-V4.1-Flash-EXL3-2.0bpw-2x-RTX-PRO-6000
- https://huggingface.co/Mia-AiLab/DeepSeek-V4.1-Flash-EXL3-2.9bpw
- https://huggingface.co/Mia-AiLab/DeepSeek-V4.1-Flash-EXL3-3.0bpw
- https://huggingface.co/coolbho3k/DeepSeek-V4.1-Flash-EXL3-3bpw
- https://huggingface.co/lvkaokao/DeepSeek-V4.1-Flash-W4A16-Engram-AutoRound
- https://huggingface.co/lvkaokao/DeepSeek-V4.1-Flash-MXFP4-Engram-AutoRound
- https://huggingface.co/antirez/deepseek-v4.1-flash-gguf
- https://github.com/antirez/ds4
- https://huggingface.co/RedHatAI/DeepSeek-V4.1-Flash
- https://huggingface.co/LibertAIDAI/DeepSeek-V4.1-Flash-REAP-256E
- https://huggingface.co/LibertAIDAI/DeepSeek-V4.1-Flash-REAP-272E
- https://huggingface.co/mlx-community/DeepSeek-V4.1-Flash-MLX-4bit
- https://huggingface.co/api/models?search=DeepSeek-V4.1-Flash
- https://api-docs.deepseek.com/quick_start/pricing
- https://github.com/deepseek-ai/deepseek-recipe
- https://github.com/intel/auto-round

---

## Verification log (2026-09-19)

Adversarial re-check by an independent agent. Every claim below was re-fetched from a
primary source opened directly (not trusting this document's own citation), or recomputed
with `python3` from `config.json` / the safetensors headers per
[`METHODOLOGY.md`](../../METHODOLOGY.md). **37 claims checked: 27 CONFIRMED (one of them a
previously-open ⚠️ now resolved), 8 CORRECTED, 2 newly marked ⚠️ TO BE VERIFIED. Nothing in
this document was found to be invented** — every benchmark table, price and quoted sentence
traced back to a real primary source, and the two large derivations (parameter count, KV
bytes/token) reproduce DeepSeek's published figures.

### CORRECTED

| # | Claim | Old → New | Source |
|---|---|---|---|
| 1 | §3.3 decode active parameters | **16,111,370,240 (16.11 B) → 16,129,720,320 (16.13 B)**; excl. lm_head 15.45 B → 15.47 B. The six terms the document itself lists sum to 16,129,720,320; the printed total dropped the 18,350,080-param compressor term. Propagated to the §Method note and §6.1. | recomputed from [`config.json`](./config.json) per METHODOLOGY §1; DeepSeek's "16B decode" claim still agrees |
| 2 | §6.1 decode FLOPs/token | **32.22 → 32.26 GFLOP** (excl. lm_head 30.90 → 30.94) | consequence of #1, `2 × active_params` |
| 3 | §1.1 file-tree "other" row | **13 files / 337,181 B → 15 files / 770,217 B** (`.gitattributes` and `LICENSE` were missing, image bytes under-counted). Rows now sum to 510,313,353,565 exactly. | https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash/tree/main?recursive=true |
| 4 | §4.1 Engram table size | **"202.76 GB / 183.1 GiB" → "202.76 GB = 188.83 GiB"** — those were two different quantities presented as one. 183.11 GiB is the FP8 payload alone (196.61 GB, vLLM's row); 188.83 GiB is payload + E8M0 scales, which is what must be resident/offloaded. | https://huggingface.co/antirez/deepseek-v4.1-flash-gguf (*"188.83 GiB of native FP8 Engram tables"*); https://huggingface.co/diffbot/DeepSeek-V4.1-Flash-EXL3-2.0bpw-2x-RTX-PRO-6000 (*"the Engram tables are pinned, ~190 GiB"*) |
| 5 | §2.4 hierarchical-indexer quote | The sentence in quotation marks was a **paraphrase, not verbatim**. Replaced with the report's actual text and added the load-bearing next sentence, *"The first Full Mode layer still scans the entire causally visible range."* | tech report §2.3.2, PDF text extracted from https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/blob/main/DeepSeek_V41_Tech_Report.pdf |
| 6 | §10.4 / §4.1 node-local Engram read | **"2–3 ms" → "2.8 ms"** (repo: *"5.9-7.8 ms per step against 2.8 ms on the head"*) | https://github.com/tonyd2wild/DeepSeek-V4.1-Flash-vLLM-DGX-Spark |
| 7 | §4 NVFP4 reconstructed-by-formula | **527.40 GB / 491.18 GiB / "0.02 % from measured" → 527.27 GB / 491.07 GiB / 0.007 %** | recomputed; measured `usedStorage` 527,309,220,165 from https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4 |
| 8 | §9 diffbot EXL3 GSM8K-200 | **"98.5 %" → "97.5 % (card headline) / 98.5 % (default-config row)"** — the card's summary sentence and its config table disagree, and the card says the column is *"a single run per configuration"*. Download size 358 GB added. | https://huggingface.co/diffbot/DeepSeek-V4.1-Flash-EXL3-2.0bpw-2x-RTX-PRO-6000 |

### New ⚠️ TO BE VERIFIED added

| # | Claim | Finding | Source |
|---|---|---|---|
| 9 | §5.3 fixed per-sequence SWA state = **2.77 MiB**, cited to `torch.zeros(max_batch_size, window_size, head_dim)` in `model.py` | **The cited code does not support the claimed dtype.** That buffer inherits `torch.set_default_dtype(torch.bfloat16)` (`model.py` line 1296), and `_window_kv`'s `act_quant(..., inplace=True)` discards the scale tensor — so the reference implementation holds fp8-rounded values in **BF16** storage: 131,072 B/layer, **5,636,096 B = 5.38 MiB per sequence**, 1.94× the planned figure. 2.77 MiB is engine-optimal (true FP8 + E8M0/32), and no vLLM/SGLang document states the dtype. Marked ⚠️ in §5.3, §5.4, §6.3 and §12.4b. | https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/inference/model.py |
| 10 | §8.6 MI355X launch command "quoted verbatim" | The quoted `SPEC_CONFIG` is the recipe's **`EVAL_ONLY=true`** arm. Its throughput-benchmark arm uses `"rejection_sample_method":"synthetic","synthetic_acceptance_length":3.51` instead. Noted inline so the 3.51 is not read as a measurement. | https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash |

### Resolved (was marked ⚠️, now sourced)

| # | Open question | Resolution | Source |
|---|---|---|---|
| 11 | §6.3 / §12.3 — do the Full-mode indexer layers really scan the whole causally-visible range? | **Yes, architecturally.** *"The first Full Mode layer still scans the entire causally visible range"*, and the Hierarchical Sparse Indexer *"is used only in the decoder of CED"*. Only "does an engine prune it anyway" stays open. | tech report §2.3.2 |

### CONFIRMED

| # | Claim | Source opened |
|---|---|---|
| 12 | HF metadata: `sha dba1be0a…`, created `2026-09-10T02:17:58Z`, modified `2026-09-10T08:18:10Z`, `gated:false`, `pipeline_tag image-text-to-text`, `usedStorage 510,310,271,922`, license MIT | https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash (likes now 3,230 vs 3,227 in-doc — snapshot drift, not an error) |
| 13 | §3.1 total logical parameters **763,205,315,794** and `model.safetensors.index.json` `total_size` **510,286,023,000**, 96,085 tensors, 48 shards | same API + https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/model.safetensors.index.json |
| 14 | §1.2 dtype mix and scale layouts: routed experts are **MXFP4** (`I8 [2304,2560]` + `F8_E8M0 [2304,160]` = 1 scale / 32 along K), dense FP8 is **true 32×32 2-D blocks** (`wq_b F8_E4M3 [32768,1280]` + scale `[1024,40]` → +0.098 %), routers BF16 `[384,5120]` | safetensors header of `model-00003-of-00048.safetensors` read by HTTP Range. Reconstructing the checkpoint from these layouts gives **510,286,023,000 B to the byte** |
| 15 | §5.1 **890 bytes/token** global KV and its whole derivation (main KV E2M1 + one E4M3 per 16 = 288 B/entry; indexer E2M1 + E8M0 per 32 = 68 B; 3 layers at r=2 + layer 20 at r=1) | model card (*"890 bytes per token"*); `model.py` line 760 `fp4_act_quant(latent, 16, True, scale_dtype=torch.float8_e4m3fn)` with the in-code comment *"Compressed KV uses groups of 16 with E4M3 scales; the indexer uses 32 with E8M0"*; tech report §2.4.4 *"E2M1 with one E4M3 scale per 16 channels, following NVFP4 … but omitting its second-level global scale"*. Recomputed: 720 + 170 = 890 ✓ |
| 16 | §3.3 prefill active **7,894,958,080 = 7.89 B**; §3.2 backbone **552,366,103,664**; Engram **196,613,849,600** | recomputed from `config.json`; model card *"552B backbone parameters"*, tech report *"552B backbone parameters and 196B Engram parameters, activating 8B parameters per token during prefill and 16B during decode"* |
| 17 | §3.2 the 552B partition, quoted from vLLM | verbatim: *"DeepSeek's 552B backbone figure covers the routed experts, attention and embeddings; the Engram tables, the ~14B DSpark drafter and the block scales sit outside it."* https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash |
| 18 | §4 vLLM memory table: 259.5 / 183.1 / 6.9 / 3.9 / 21.9 GiB (sum 475.3 GiB) and `vram_minimum_gb: 614` = total × 1.2 | same recipe page, verbatim |
| 19 | §8.2 vLLM verified-hardware payload — `"hardware":{"h100":"verified","h200":"verified","b200":"verified","gb200":"verified","gb300":"verified","b300":"verified","mi350x":"verified"}`, `"precisions":["fp8"]`, `"default_hardware":"gb200"`, `"difficulty":"advanced"`. **A100 and RTX PRO 6000 genuinely absent.** | raw JSON payload of https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash |
| 20 | §8.3/§8.4 TP defaults `{"default":4,"h100":8,"b300":2}`; H100 override `--engram-config '{"cpu_offload":true}' --max-num-batched-tokens 4096 --gpu-memory-utilization 0.92` + `VLLM_USE_V2_MODEL_RUNNER=1`, `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`; B300 override incl. **exactly 26** cudagraph capture sizes, `--max-cudagraph-capture-size 8190`, `--max-num-seqs 256`; H200 *"one 8-GPU H200 node (1128 GB) with room for KV cache"*; GB200 NVL4 tray 768 GB | same payload, verbatim |
| 21 | §8.1 vLLM min version **0.30.0**, `nightly_required: true`, no pip wheel, PR **#56228** merged | PR #56228 = *"[Model] DeepSeek-V4.1-Flash Model Definitions"*, merged **2026-09-10T12:16:26Z** into `main` (GitHub API) |
| 22 | §9 / §9.1 NVFP4 checkpoint: `usedStorage 527,309,220,165` (527.31 GB / 491.09 GiB), 48 shards, ModelOpt **v0.47.0rc0**, *"16,986,931,200 weight blocks"*, *"18 of 46,080 projection entries used fallback scales"*, *"476 GiB to 492 GiB"*, DSpark not exercised, runtimes **vLLM and SGLang only** (supports the "no TensorRT-LLM" finding), SGLang commit `da64c5cb…`, image `lmsysorg/sglang:dev-cu13-dsv41` | https://huggingface.co/nvidia/DeepSeek-V4.1-Flash-NVFP4 + its HF API. The API also shows only the routed experts converted (`U8 543,581,798,400`) with DSpark experts still `I8 13,589,544,960` ✓ |
| 23 | §9.1 accuracy table — MXFP4 91.035 / 78.563 / 54.401 / 76.667 / 74.046 / 81.60 vs NVFP4 91.288 / 78.438 / 55.843 / 77.267 / 73.699 / 82.16, and the eval settings | same card, exact to three decimals |
| 24 | §11 DeepSeek API pricing — `deepseek-flash` = DeepSeek-V4.1-Flash; cache hit $0.003/$0.006, miss $0.15/$0.30, output $0.60/$1.20; context 1M, max output **384K**, concurrency **2,500**; peak 01:00–04:00 and 06:00–10:00 UTC Mon–Fri ex. Chinese holidays; v4-pro $0.022/$0.66/$1.98 off-peak, concurrency 500; legacy names reroute | https://api-docs.deepseek.com/quick_start/pricing — every figure matches. Derived checks: hit is exactly 1/50 of miss ✓; output 3.3× cheaper than v4-pro ✓; §11.2 break-even 7,407 tok/s at $16/hr ✓ |
| 25 | §10.1 MI355X — MoE GEMM 2.72→2.30 / 4.91→4.27 / 9.06→7.18 ms, whole step 19.11→18.31 / 19.42→18.40 / 28.38→25.92 ms (4.2 / 5.3 / 8.7 %); ITL 4.01→3.91, TTFT 681→639, E2E 4.48→4.32 s; gsm8k 0.9719 ± 0.0045 / 0.9712 ± 0.0046; `AIPERF_EXPERIMENTAL_FAST=1`; `aiter` → CK a8w4 vs `aiter_triton_mxfp4_bf16` → Triton `_moe_gemm_a16w4`; image `nightly-eed1f3d0…`; PR #56503 | https://github.com/SemiAnalysisAI/InferenceX/pull/3058 (merged 2026-09-13), read via GitHub API — every number exact |
| 26 | §10.2 RTX PRO 6000 EXL3 table (all six config rows), the `xmoe` microbenchmark (all six token counts), MoE share 16.1→8.9 ms and whole step 28.9→23.5 ms, and the 40-min agentic run (184 requests, 18.5M prompt tokens, 87 % prefix-cache, 240–254 tok/s) | https://huggingface.co/diffbot/DeepSeek-V4.1-Flash-EXL3-2.0bpw-2x-RTX-PRO-6000 — exact |
| 27 | §10.3 A800 — 3,040 tok/s, 1,945 tok/s, 87/100, 406/500 raw & 417/500 re-evaluated, the serving-stack block, *"A100 … has not been independently re-measured here"* | https://huggingface.co/StellarVoyager/DeepSeek-V4.1-Flash-A100-A800-SM80 — exact |
| 28 | §10.4 DGX Spark — C1 37.95 / 43.12 / 0.441 s, C6 131.86 / 25.35 / 0.502 s; per-category 92.2 / 73.8 / 55.4 / 50.9 / 37.8 / 24.4; prefill 902–1,539 (93,335-token prompt in 78.17 s at 1,194); KV pool 1,070,168 and 1,078,380 at 1M; weights 81.58 GiB + KV 5.15 GiB per Spark; DSpark mean **3.57**, range 1.88–5.92; top-k fix 1.6–3.6× and the 128 KB shared-memory limit; `--block-size 128`; *"63 vs 94 ms per step"*; *"padded batches can hang SM120 sparse MLA"*; ConnectX-7 RoCE; 48 GB of Engram rows per worker | https://github.com/tonyd2wild/DeepSeek-V4.1-Flash-vLLM-DGX-Spark — exact |
| 29 | §10.5 model-card benchmark table (all nine rows vs Opus-5.0 / GPT-5.6 Sol / K3 / DS-V4-Pro) and the effort curve: *"Raising the effort from 25 to 100 improves the average Pass@1 on eight reasoning-intensive benchmarks from 67.1% to 76.3%, on DeepSWE v1.1 from 66.0% to 74.2%, and on Terminal-Bench 2.1 from 82.4% to 90.6%, at the cost of roughly 2.5× more output tokens"*; *"the 60–80 range … whereas the final step to effort 100 lengthens agent trajectories by 1.6–1.8× for only marginal improvements"* | model card README.md + tech report §5.3.2 — verbatim |
| 30 | §1.3 reasoning-effort mappings. Both halves of the "they disagree" claim hold: `encoding/README.md` says *"`"low"` → 50, `"high"` → 75, `"max"` → 100. The default is `"high"` (75)"*, the tech report says the public API tiers are 50 / 75 / 100, and vLLM maps `low`/`high`/`xhigh`/`max` → 25/50/75/100. **Note:** the vLLM recipe mis-attributes 25/50/75/100 to *"the open-source prompt encoder"* — the encoder README says 50/75/100. This document's attribution is the correct one. | https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/raw/main/encoding/README.md; tech report §5.3.3; vLLM recipe |
| 31 | §1.3 no Jinja chat template; sampling `temperature 1.0`, `top_p 0.95 or 1.0`, 1M context, `max_tokens ≥ 256K`; §5.5 *"approximately 4-fold and 437-fold reductions relative to DeepSeek-V4-Flash and DeepSeek-V1"* (the back-computed 3,560 B and 389 KB remain correctly marked ⚠️) | model card README.md |
| 32 | §2.x / §7 / §8.7 tech-report quotes: OCP MXFP4 *"to support as many hardware platforms as possible"*; *"Both the embedding tables and the key/value projections use FP8 precision"*; *"15 kernels during prefill and 11 during decode"*; CED Eq. (1) *"not derived from their respective hidden states"* with **layer-dependent** `W_l^KV`/`W_l^Z` (so §2.3's ⚠️ is correctly placed); SWA Bounded Replay *"for a replay starting at position s, a query at position i attends to SWA keys in [max(s, i−W+1), i]"* and *"barely compromises response quality"* with no number (⚠️ correctly placed); DSpark *"three Transformer blocks with a sliding attention window of 128 tokens … Markov head … confidence head"*; modality-specific routing biases; FlashMLA / Mega-Gate, Mega-mHC, Mega-MoE in DeepGEMM / TileKernels / DeepSelect; EPD disaggregation; *"FP4 reduces storage rather than accelerates matrix multiplication"* | PDF text extracted from `DeepSeek_V41_Tech_Report.pdf` (51 pages) — all verbatim |
| 33 | §8.5 Ascend — *"validated with W8A8 weights, INT8 Engram storage, TP8/DP4/EP32, DSpark speculative decoding, and FULL_DECODE_ONLY ACL Graph"*, *"No production performance baseline is published for this configuration"*, *"Production performance qualification and task-level accuracy evaluation are not complete"* | https://docs.vllm.ai/projects/ascend/en/latest/tutorials/models/DeepSeek-V4.1-Flash.html |
| 34 | §8.1 reference impl deps `torch>=2.10.0`, `tilelang==0.1.8`, and *"A readable reference implementation rather than a production serving engine"* | `inference/requirements.txt` and `inference/README.md` |
| 35 | §9 AutoRound INT4 — **451.7 GB**, GSM8K **93.93 / 94.01 vs 92.87**, auto-round **0.15.0**, verified on **4×H200 TP4**, INT4 g32 sym experts + INT4 engram, dense dequantised to BF16, needs PR #56201 + `DSV41_ENGRAM_DTYPE=int4` | https://huggingface.co/lvkaokao/DeepSeek-V4.1-Flash-W4A16-Engram-AutoRound |
| 36 | §9 GGUF — Q2 **340.60 GiB**, Q4 **482.98 GiB**, vision **0.90 GiB**, DwarfStar/Metal, `--ssd-streaming` | https://huggingface.co/antirez/deepseek-v4.1-flash-gguf |
| 37 | §6.2–§6.4 derivations recomputed and reproduced exactly: core attention 3.19 GF (context-independent), indexer scan 0.17/0.67/2.68/21.47 GF, reindex pool 0.54 GF; one MXFP4 expert 18,800,640 B; `distinct_experts` 6.0/45.5/152.0/243.8/332.8/377.2; Engram 24 × 264 × 2 = **12,672 B/token**; KV read 13.69/17.68/33.62/182.37 MiB; ViT 12,845,056 params/layer → 411.0 M, 18.86 / 1.48 TFLOP per image | `python3`, METHODOLOGY §1/§2/§4 |

---

## Sweep log (2026-09-19)

Systemic correction pass against the amended [`METHODOLOGY.md`](../../METHODOLOGY.md)
(§1 bytes-per-param table + units convention, §2 state-slot multiplier `S`, §3 consistency
rule, §6 cached-token rule, §8 pinned GPU / model / price inputs). Nothing from the earlier
**Verification log** was undone. Every recomputed value was produced with `python3` and pasted
back into the table it belongs to.

| Section | Old → New | Reason | Source |
|---|---|---|---|
| §1.2, 3rd bullet | *"far below METHODOLOGY §1's generic +6 % for 32×32 blocks … use 0.1 % … and 3.125 % for its MXFP4 experts"* → *"+0.1 % … and **+6.25 %** (0.53125 B/param)"* | METHODOLOGY §1 no longer carries a generic "+6 % for 32×32"; its DeepSeek-V4.1 row is 1.001 B/param (+0.1 %), and MXFP4 is pinned at 0.53125 B/param = **+6.25 %** relative to the 0.5 B payload | METHODOLOGY §1 |
| §4, header paragraph | one-line list *"dense FP8 +0.098 %; MXFP4 +3.125 %; NVFP4 +6.25 %; INT4 g32 +6.25 %"* → a 4-row table with **MXFP4 +6.25 %, NVFP4 +12.5 %, INT4 g32 +12.5 %** | The paragraph mixed two conventions: dense FP8 was quoted against payload (METHODOLOGY's convention) while the three 4-bit rows were quoted as a fraction of one byte, halving them. **No byte total changed** — the totals were always built from the layouts. Re-derived: `exp×(0.5+1/32) + engram×(1+1/32) + dense×(1+1/1024) + BF16 + F32 = 510,286,023,000 B` exactly, and the NVFP4 formula gives **527.27 GB**, the METHODOLOGY §8 pinned value | METHODOLOGY §1/§8; `model.safetensors.index.json` |
| §4.1 strategy table; §8.4 H100 row | *"183 GiB of HBM"*, *"the 183 GiB Engram table must go to host"* → **188.8 GiB** (202.76 GB, FP8 payload + E8M0 scales) | Applies the earlier fact-check's own instruction (*"where this document says 183 GiB below, read 188.8 GiB for a sizing budget"*) at the two sizing call-sites, instead of leaving the reader to do it | Verification log #4 |
| §4.1 intro | *"≈ 12.7 KB/token"* → **12,672 B/token = 12.38 KiB/token** | METHODOLOGY §1 units: report GiB/MiB/KiB unless the column says GB | METHODOLOGY §1 |
| §5.2 table | column header **"1M ctx"** holding `890 MB / 1,650 MB / 3,200 MB` → two columns, **890 / 1,650 / 3,200 MiB** and **933 / 1,730 / 3,355 MB** | Unit slip: `1,048,576 × 890 = 933,232,640 B`, which is 890.00 **MiB**, not 890 MB. Header row also now names 890 B/token as the **FP4-KV (Blackwell-kernel)** figure per METHODOLOGY §8 | METHODOLOGY §1/§8 |
| §5.3 (new paragraph) | — → explicit **`S = 1`** statement for the SWA ring buffer, with ⚠️ if an engine ever allocates DSpark speculative slots | METHODOLOGY §2 requires the engine state-slot count `S` to be stated or marked ⚠️ (cf. Kimi-K3 `S = 5` on SGLang). No vLLM/SGLang document states `S > 1` here | METHODOLOGY §2 |
| §5.3 | *"24,576 B ≈ 24 KB"*, *"8 MB per sequence at 1M"* → **24 KiB**, **8 MiB (8.39 MB)** | Unit slips; `1,048,576 × 8 B = 8,388,608 B = 8 MiB` | METHODOLOGY §1 |
| §5.4 totals table | single **"+ fixed SWA"** column (2.77 MiB only) → **both** columns: `+ 2.77 MiB SWA (FP8, engine-optimal)` **and** `+ 5.38 MiB SWA (BF16 reference) ⚠️`, i.e. 9.72/12.33, 30.58/33.19, 114.02/116.62, 892.77/895.38 MiB | METHODOLOGY §8 pins the state as the range *2.77 MiB (FP8) – 5.38 MiB (BF16 reference) ⚠️*; a totals table that shows only one half hides the 1.94× that dominates below ~6 K context. Recomputed with `python3` | §5.3 ⚠️; METHODOLOGY §8 |
| §5.4 "Ratio" column | 2.6× / 3.3× / 3.5× / 3.6× → constant **3.6×** | The old column divided BF16 *global* KV by (FP4 global **+** FP8 fixed state) — two different quantities. Like-for-like global ratio is `3,200/890 = 3.60×` at every context | METHODOLOGY §2 |
| §5.4 crossover | *"below ~3 K … crossover moves out to ~6 K"* → **~3.3 K** and **~6.3 K**, with the arithmetic shown | Recomputed: `2,906,112/890 = 3,265` tokens; `5,636,096/890 = 6,333` tokens | `python3` |
| §6.3 KV-read table | — → added *"These are per-sequence figures … multiply by the batch"* with the batch-32 row (438 MiB / 566 MiB / 1.05 GiB / 5.70 GiB) | METHODOLOGY §3 consistency rule: decode KV-read bytes must scale with `batch × ctx × kv_bytes_per_token`, never a flat per-step constant | METHODOLOGY §3 |
| §6.3 callout | *"on an H200 (≈4.8 TB/s) roughly 235 µs"* → *"235 µs at **peak** bandwidth and **294–392 µs** at the §4 planning MBU of 0.6–0.8"* | 235 µs implicitly assumed MBU 1.0. H200 = 4.8 TB/s confirmed against METHODOLOGY §8 | METHODOLOGY §4/§8 |
| §7.2 | *"~3 % of the **16.11 B** verify step"* → *"~3.2 % of the **16.13 B** verify step"* | Surviving instance of the decode active-parameter figure the earlier fact-check corrected everywhere else (Verification log #1) | Verification log #1 |
| §7.3 table, row 1 | *"golden acceptance length 3.51"* → **"3.51 — a *synthetic benchmark constant*, not a measurement"**, with `rejection_sample_method: "synthetic"` spelled out | The throughput arm *tells* the harness to assume 3.51 (`synthetic_acceptance_length`); only the `EVAL_ONLY=true` arm does real block rejection, and it publishes no acceptance figure | [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash); Verification log #10 |
| §7.3, prose | — → *"the only actual measurement in that table is the DGX Spark mean of 3.57"* | 3.51 is synthetic and 2.99 is a different checkpoint (V4-Flash-Vision-Exp) | same |
| §8.1 vLLM row | `0.30.0+, nightly required` → same, **plus** *"0.30.0 does not exist yet; latest release is 0.29.0 (2026-09-09)"* | Release re-verification. GitHub's `releases/latest` API was rate-limited from this host, so PyPI was used: `vllm 0.29.0` | https://pypi.org/pypi/vllm/json; [inference-engines.md §2.1](../../cross-cutting/inference-engines.md) |
| §8.1 SGLang row | commit/image only → **"not in any numbered release (latest is 0.5.20, 2026-09-18)"** | same re-verification: `sglang 0.5.20` | https://pypi.org/pypi/sglang/json |
| §8.1 TensorRT-LLM row | min version `—` → **1.2.1 stable / 1.3.0rc27 pre-release**; status reworded to *"no **V4.1-Flash** support"* (V4 **is** supported) | same re-verification: `tensorrt-llm 1.2.1`. The old wording implied no DeepSeek support at all | https://pypi.org/pypi/tensorrt-llm/json; [inference-engines.md §2.3](../../cross-cutting/inference-engines.md) |
| §8.1 (new row) | TokenSpeed **absent from the matrix** → row added: **ships a DeepSeek-V4.1-Flash recipe** (FlatKV four-group KV backend, `--disable-prefill-graph` for the CED decoder, `--speculative-algorithm DSPARK`, per-commit GB300 Slurm 1P1D CI gated at GSM8K ≥ 0.90) | Omission implied the model has no TokenSpeed path. It does | [inference-engines.md §2.5](../../cross-cutting/inference-engines.md), corrected; [src](https://lightseek.org/tokenspeed/recipes/models) |
| §8.4 B300 row | *"**B300 288 GB** … 2 of 8 GPUs = **576 GB** … weights (510 GB) nearly fill 576 GB"* → *"**268 GB as deployed** … 2 of 8 = **536 GB** … `usable_hbm = 536 × 0.90 = 482 GB` is **27.9 GB short** of the 510.29 GB checkpoint, so `cpu_offload` is arithmetically required"* | METHODOLOGY §8 pins HGX B300 / DGX B300 / AWS p6-b300 at **268 GB/GPU** (2,144 GB/node), not 288. The vLLM recipe states TP2 but publishes no GB figure, so 576 was this document's own arithmetic on the wrong capacity | METHODOLOGY §8; [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash) (*"defaults to TP2 for B300 (2 of the 8 GPUs)"*) |
| §8.4 GB300 row | *"GB300 NVL"* → *"**GB300 NVL72, 288 GB/GPU (≈279 usable)**"* + *"do not merge with the HGX B300 row"* | METHODOLOGY §8 keeps HGX B300 (268 GB) and GB300 NVL72 (288 GB) as distinct parts | METHODOLOGY §8 |
| §10.1 sanity check | *"at a nominal **8 TB/s aggregate** and MBU 0.5 that predicts ≈5.8 ms … measured 18.3 ms"* (≈3×) → *"MI355X is **8.0 TB/s per GPU**, so four at TP4 are an **aggregate 32 TB/s** → **≈1.45 ms** at MBU 0.5 (1.82 at 0.4, 1.21 at 0.6) → measured is **≈12.6×** the roofline"* | The old figure used one GPU's bandwidth as the aggregate of four. MI355X 8.0 TB/s is the METHODOLOGY §8 pinned value | METHODOLOGY §8; recomputed with `python3` |
| §10.1 sweep footer | *"DSpark golden acceptance length 3.51"* → *"DSpark **synthetic** acceptance length 3.51 … a benchmark constant the harness is *told* to assume, **not a measured acceptance rate**"* | same as §7.3 | [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash) |
| §11 (new paragraph) | — → explicit note that [`serving-optimizations.md` §1.5](../../cross-cutting/serving-optimizations.md) prices **DeepSeek-V4-Flash** cache-miss input at **$0.14/M** against **$0.15/M** here, that both were re-fetched today, that the $0.14 row is the **retired** `deepseek-v4-flash` listing on `deepseek.ai/pricing` while $0.15 is the live `deepseek-flash` (= V4.1-Flash) listing, and that **$0.15 is the figure for this model** | METHODOLOGY §8: *"if a document disagrees, state the disagreement and cite both."* Re-fetched `api-docs.deepseek.com/quick_start/pricing`: cache hit $0.003/$0.006, miss **$0.15**/$0.30, output $0.60/$1.20, max output 384K, concurrency 2,500, legacy-name reroute — unchanged from Verification log #24, so that entry is **not** undone | https://api-docs.deepseek.com/quick_start/pricing; serving-optimizations.md §1.5 |
| §11.2 | *"At a **notional $2/GPU-hr** × 8 GPUs = $16/hr, break-even ≈7,400 tok/s"* + *"**⚠️ GPU-hour prices are not sourced in this document**"* → an **8-row sourced table** from `cloud-pricing.md` §5 with break-evens 14,778 (8×H200 Hyperstack $3.99) / 37,037 (OCI $10.00) / 22,222 (8×B200 Hyperstack $6.00) / 51,852 (OCI $14.00) / 27,407 (8×B300 Hyperstack $7.40) / 55,556 (OCI $15.00) / 33,333 (4×GB300 OCI $18.00) / 31,852 (8×MI355X OCI $8.60) output tok/s | METHODOLOGY §6/§8: price rows must come from `cloud-pricing.md`, never a notional number or a neighbouring GPU's row. An explicit guard was added that **$7.40 is Hyperstack's HGX B300 rate and is not a GB300 price** (GB300 = OCI $18.00) and **$8.60 is OCI's MI355X rate, not an MI300X price** ($6.00) — the two substitutions found in sibling documents | [cloud-pricing.md](../../cross-cutting/cloud-pricing.md) §5.5–5.13, §10; recomputed with `python3` |
| §11.2 | *"third-party provider pricing … **was not retrievable** — the WebSearch budget was exhausted"* → *"third-party **API reseller** pricing … `cloud-pricing.md` covers GPU-hour rates, not per-token reseller rates, and no reseller rate for this model is sourced anywhere in this research tree"*, pointing at `serving-optimizations.md` §1.5 for the sourced per-token table | METHODOLOGY §7: a tool-budget excuse is not a sourcing statement. GPU-hour prices **are** now sourced (row above); per-token reseller rates genuinely are not, so the ⚠️ stays but is stated as a gap in the tree, not a tooling failure | [cloud-pricing.md](../../cross-cutting/cloud-pricing.md); [serving-optimizations.md §1.5](../../cross-cutting/serving-optimizations.md) |
| §11.2, first bullet | *"occupies **890 MB** of KV"* → *"**890 MiB** (933 MB)"* | same unit slip as §5.2 | METHODOLOGY §1 |
| §11.2, closing | — → pointer: priced per-(model, GPU) comparisons belong in `research/models/deepseek41f/<gpu>.md`, next phase | Scope rule — no per-(model, GPU) fit / throughput / cost tables are added to this document | task scope rule 15 |
| §12.8 | *"only available as a benchmark constant (3.51) and a community mean (3.57)"* → the 3.51 is named explicitly as **synthetic** (`rejection_sample_method: "synthetic"`), 3.57 as the only measurement | same as §7.3 | [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash) |
| §12.9 | *"under-predicts the measured MI355X step time by **~3×**"* → **~12.6×**, with the cause of the old figure named | consequence of the §10.1 bandwidth correction | METHODOLOGY §8 |
| §12.13 | *"B300 at TP2 leaves **~66 GB** for KV after weights"* → *"at 268 GB/GPU, TP2 = 536 GB, `usable_hbm` 482 GB is **27.9 GB short** of the checkpoint; with the mandatory Engram offload, resident weights 307.5 GB → KV budget **≈163 GB (152 GiB)** after ~12 GB activation workspace"*, + pointer to `research/models/deepseek41f/b300.md` | 66 GB was `576 − 510` on the wrong capacity. Recomputed per METHODOLOGY §3 (`usable = cap × 0.90`, minus per-GPU weights, minus activation workspace). No fit table added here — scope rule 15 | METHODOLOGY §3/§8; `python3` |
| §12.15 | *"TensorRT-LLM support: none found"* → *"none found **for V4.1** (stable 1.2.1 / pre-release 1.3.0rc27, both re-verified); DeepSeek-**V4** is supported"* | release re-verification + precision | https://pypi.org/pypi/tensorrt-llm/json |
| §12.16 | *"Dynamo: no V4.1-Flash recipe found."* → same, **plus** *"TokenSpeed, by contrast, does ship one"* | the §8.1 TokenSpeed addition, reflected in the open-questions list so it is not read as "no third engine" | [inference-engines.md §2.5](../../cross-cutting/inference-engines.md) |

### Checked and left unchanged

| Item | Finding |
|---|---|
| Routed experts are **MXFP4**, not NVFP4; NVIDIA's NVFP4 build is **larger** (527 GB vs 510 GB), accuracy-neutral, **no published speedup** | Already stated correctly in §1.2, §4, §9.1 and §12.14. Re-confirmed against the HF API: base `I8 557,171,343,360`; NVFP4 build `U8 543,581,798,400` (routed experts only) + `I8 13,589,544,960` (DSpark still MXFP4), `usedStorage 527,309,220,165` |
| Checkpoint totals vs METHODOLOGY §8 | 510.29 GB (base) reconstructs to **510,286,023,000 B exactly**; the NVFP4 formula gives **527.27 GB**, the pinned value. Both retained, with measured `usedStorage` shown alongside |
| B200 180 GB, H200 141 GB, H100 80 GB in §8.4 | already the METHODOLOGY §8 deployed capacities |
| Feasibility rule (METHODOLOGY §3) | No throughput or cost row in this document is keyed to a GPU count and a batch, so no row converts to `infeasible (KV)`. §6.3's batch column is bytes-read, not throughput |
| RTX PRO 6000 bandwidth (1,597 GB/s Server Ed.), B200-vs-H200 FP8 gap (1.7–3.0×), dense-vs-sparse TFLOPS | none of these appear in this document; nothing to correct |
| Per-(model, GPU) fit / throughput / cost tables | **None added.** Pointers to `research/models/deepseek41f/<gpu>.md` added in §11.2 and §12.13 instead |

### Citation integrity spot-check

The five most load-bearing citations were re-fetched directly (`curl`) on 2026-09-19 and the
cited page confirmed to contain the claim:

| # | Citation | Claim it carries | Result |
|---|---|---|---|
| 1 | [HF API, `deepseek-ai/DeepSeek-V4.1-Flash`](https://huggingface.co/api/models/deepseek-ai/DeepSeek-V4.1-Flash) | §1 `sha dba1be0a40aa45a94ad051997016db3960a90277`, `usedStorage 510,310,271,922`; §3.1 total **763,205,315,794** | ✅ all three returned verbatim; dtype map `I8 557,171,343,360 / F8_E4M3 204,015,223,296 / BF16 1,976,441,856 / F32 42,307,282` matches §1.2 |
| 2 | [vLLM recipe](https://recipes.vllm.ai/deepseek-ai/DeepSeek-V4.1-Flash) (`.json`) | §4 memory split 259.5 / 183.1 / 6.9 / 3.9 / 21.9 GiB; `vram_minimum_gb: 614`; §8.2 hardware map; §8.4 TP2-on-B300 | ✅ all present verbatim, incl. *"The command builder defaults to TP2 for B300 (2 of the 8 GPUs). For high interactivity, deploy the model using TP4"* and the H200 *"1128 GB"* line. **Note:** the page states no GB figure for B300 — the old "576 GB" was this document's own arithmetic (corrected above) |
| 3 | [HF API, `nvidia/DeepSeek-V4.1-Flash-NVFP4`](https://huggingface.co/api/models/nvidia/DeepSeek-V4.1-Flash-NVFP4) | §4 / §9 NVFP4 checkpoint **527.31 GB**, routed experts only converted | ✅ `usedStorage 527,309,220,165`; `U8 543,581,798,400` (routed experts) with DSpark still `I8 13,589,544,960` — confirms "routed experts only" |
| 4 | [DeepSeek API pricing](https://api-docs.deepseek.com/quick_start/pricing) | all of §11 | ✅ every figure matches, incl. the legacy-name reroute footnote. Drives the new $0.14-vs-$0.15 note |
| 5 | [TokenSpeed recipes](https://lightseek.org/tokenspeed/recipes/models) via [inference-engines.md §2.5](../../cross-cutting/inference-engines.md) | the new §8.1 TokenSpeed row | ✅ page reachable (HTTP 200) and the V4.1-Flash section is quoted in `inference-engines.md` §2.5 with the serve command and the GB300 1P1D CI job |

No citation was found to be pointing at a page that does not contain its claim.
