# Marlin-2B SOP inference launch profile (S2M)

**Task S2M**, 2026-09-22. This document pins what the Marlin endpoint actually serves at
launch: the artifact, the processor and preprocessing profile, the request/response
surface, the caps, and a client recipe for a large recorded-video dataset. It is the
resolution artifact for [P-06](../plan/15-pending-inputs.md) and the input register for
[P-07](../plan/15-pending-inputs.md) and [P-18](../plan/15-pending-inputs.md).

Conventions are [`research/METHODOLOGY.md`](../METHODOLOGY.md) §"How to read it": `meas.`
is a measurement with a source, `est.` is derived, **⚠️ TO BE VERIFIED** is an unknown
with the method that would close it. Every value below cites a file and line, an HF API
call made on 2026-09-22, or a committed measurement. **No GPU run, no cloud operation and
no paid call was made for this document.**

**What this document is not.** It is not evidence that the endpoint is deployed, that the
pinned configuration has been served end to end, or that Marlin answers SOP questions
correctly. Three of the surfaces it specifies (`/v1/jobs`, `/v1/uploads`, the pilot
ingress) are **not mounted** in the tree it describes — see §4.

---

## 1. Artifact pin

### 1.1 Identity

| Field | Value | Source |
|---|---|---|
| HF repo | `NemoStation/Marlin-2B` | `models/marlin2b/model.env:3` (`HF_REPO`) |
| Commit (revision to pin) | **`fd111fca4fc7897876fb0d7e9df22ca5ac8ab965`** | HF API `GET /api/models/NemoStation/Marlin-2B` → `.sha`, unauthenticated, `meas.` 2026-09-22; matches [`architecture.md` §1.1](../models/marlin2b/architecture.md) recorded 2026-09-19 |
| Created / last modified | 2026-05-13T16:23:12Z / 2026-05-30T08:55:53Z | same API call, `.createdAt` / `.lastModified` |
| Gated | `auto` (self-serve form; `HF_TOKEN` required to read any file) | `.gated`; `models/marlin2b/model.env:8` `GATED=yes`; confirmed 2026-09-22: `GET /resolve/main/config.json` → **401** unauthenticated |
| Files | 15, `usedStorage` 5,475,843,667 B; weights 5,443,677,224 B across two shards | `.siblings` (15 names, identical set to [`FILES.md`](../models/marlin2b/FILES.md)); byte counts from `FILES.md` (HF API tree, 2026-09-19) |
| Licence | Apache-2.0 | `.cardData.license`; [`MODEL_CARD.md`](../models/marlin2b/MODEL_CARD.md) front-matter |
| Base model | `Qwen/Qwen3.5-2B` (finetune) | `.tags` `base_model:finetune:Qwen/Qwen3.5-2B` |
| Declared `architectures` | `["MarlinForConditionalGeneration"]` | `.config.architectures`; [`config.json`](../models/marlin2b/config.json) |
| `model_type` | `qwen3_5` | idem |
| `auto_map` | `{"AutoModelForCausalLM": "modeling_marlin.MarlinForConditionalGeneration"}` | idem |
| Checkpoint dtype | BF16, unquantised, all 618 tensors | [`architecture.md` §1.1](../models/marlin2b/architecture.md) from `model.safetensors.index.json` |
| Hosted vendor API | **none** — `inferenceProviderMapping` is `null` | `.inferenceProviderMapping`, `meas.` 2026-09-22 |

`SIZE_GB=5` in `model.env:9` is the decimal free-space check, not a weight figure; the
weights are 5.4437 GB / 5.0698 GiB ([`architecture.md` §1.2](../models/marlin2b/architecture.md)).

### 1.2 Engine loading pin

`MarlinForConditionalGeneration` is in no engine registry. The supported load is the
architecture remap, and it is **one flag, not a port** — the class is a pure subclass of
`Qwen3_5ForConditionalGeneration` with an unmodified forward
([`architecture.md` §8.1](../models/marlin2b/architecture.md), disagreement #6 in
[`models/marlin2b/README.md`](../models/marlin2b/README.md)):

```
--hf-overrides '{"architectures":["Qwen3_5ForConditionalGeneration"]}'
```

`models/marlin2b/serve.sh:37` ships exactly that, plus `--max-model-len 32768` (`serve.sh:27,38`),
`--dtype bfloat16`, `--limit-mm-per-prompt '{"video":1,"image":4}'` (`serve.sh:40`),
`--gpu-memory-utilization 0.90`, `--served-model-name marlin2b` (`serve.sh:36`), on
`IMAGE=vllm/vllm-openai:nightly` (`serve.sh:23`).

Consequences of the remap that the launch profile must re-supply itself, because the
remapped loader does not carry Marlin's custom code
([`architecture.md` §1.3, §1.4](../models/marlin2b/architecture.md);
[`models/marlin2b/README.md`](../../models/marlin2b/README.md) "Why two paths"):

1. **Both EOS ids.** `config.eos_token_id` is `248046`, `config.text_config.eos_token_id`
   is `248044`, and [`generation_config.json`](../models/marlin2b/generation_config.json)
   lists both `[248044, 248046]`. An engine reading only the top-level id misses a stop
   token and runs to the output ceiling.
2. **The `<think>` prefix strip.** The model emits `<think>` at the start of every
   response ([`MODEL_CARD.md`](../models/marlin2b/MODEL_CARD.md) "Notes on output"); the
   vendor helpers strip it, a raw `generate()`/vLLM caller must.
   `apps/infrx-api/infrx/worker/reasoning.py` is the boundary-independent filter that
   replaces the legacy per-chunk regex in `gateway/routes/chat.py:12`.
3. **The canonical prompts** (§1.4) — the helpers' text is not sent by the engine.
4. **The Path A frame/pixel budget** (§1.5) — neither vLLM nor the vendor
   `apply_chat_template` applies it by default.

**Not available on any hardware, so not in the profile:** speculative decoding
(`mtp_num_hidden_layers: 1` is declared but the weight map ships **zero** `mtp` tensors —
[`architecture.md` §7.1](../models/marlin2b/architecture.md)); any FP8, NVFP4, MXFP4 or
AWQ weight path — **no such checkpoint exists**
([`architecture.md` §9](../models/marlin2b/architecture.md) "Notably absent"); prefix caching as a
throughput lever (the cacheable scaffold is <0.2 % of a video request and
`--mamba-cache-mode=all` raises — [§5.5](../models/marlin2b/architecture.md)).

**Quantised builds do exist, and are out of profile.** Correcting an earlier draft of this
document, which said no quantised checkpoint existed at all:
[`architecture.md` §9](../models/marlin2b/architecture.md) lists an **author-published
MLX 8-bit** build (`NemoStation/Marlin-2B-MLX-8bit`, Apple Silicon only, so irrelevant to
CUDA serving) plus community **GPTQ INT4 W4A16** (`prasannaJagadesh/marlin-2B-GPTQ-4BITS`),
**SDNQ INT8** (`tintwotin/Marlin-2B-SDNQ-int8`) and **GGUF** (`jadeonrails/marlin-2b-gguf`)
builds. None enters the launch profile, for reasons that are facts rather than preferences:
**not one publishes any accuracy measurement** — no CaReBench, DREAM-1K or TimeLens score and
no perplexity delta ([§9](../models/marlin2b/architecture.md); open question 33 in
[`models/marlin2b/README.md`](../models/marlin2b/README.md)) — which for a model whose product
is second-precise timestamps is an unmeasured risk landing on exactly the digits that matter;
the INT4 path is separately blocked on the target hardware by vLLM #35924 (GDN's `in_proj_ba`
output dim = `num_v_heads` = 16, below `GPTQ_MARLIN_MIN_THREAD_N` = 64, so the Marlin kernel
raises during weight loading **even at TP1**); and INT8 is a non-starter on B300/GB300
(`sm_103a` lacks `tcgen05.mma .kind::i8`). Adopting any of them is a new serving version with
its own parity and quality evidence (OPT-PARITY), never a configuration change.

### 1.3 Tokenizer and processor pin

| Field | Value | Source |
|---|---|---|
| Vocab | 248,320, `tie_word_embeddings: true` | [`config.json`](../models/marlin2b/config.json) |
| `eos_token` / `pad_token` | `<\|im_end\|>` / `<\|endoftext\|>`; `bos_token` and `unk_token` `null` | HF API `.config.tokenizer_config`, `meas.` 2026-09-22 |
| Vision token ids | image 248056, video 248057, vision start/end 248053/248054 | [`config.json`](../models/marlin2b/config.json) |
| `tokenizer.json` | 19,989,325 B; served-bytes sha256 `06b9509352d2af50381ab2247e083b80d32d5c0aba91c272ca9ff729b6a0e523` (`meas.` pilot box, 2026-09-22); registry oid ⚠️ see below | `meas.` 2026-09-22: `GET /api/models/NemoStation/Marlin-2B/tree/main` returns `.lfs.oid` for this file, 64 hex characters, `pointerSize` 133. The oid **is** the sha256 of the contents, so no token is needed to obtain it |
| `chat_template.jinja` | 7,755 B, **sha256 `273d8e0e683b885071fb17e08d71e5f2a5ddfb5309756181681de4f5a1822d80`** | `meas.` 2026-09-22: HF API `.config.chat_template_jinja` is the whole template (7,755 UTF-8 bytes, exactly the `FILES.md` size) and needs no gate. Emits `<\|vision_start\|><\|video_pad\|><\|vision_end\|>`, carries a `<tool_call>` block the platform refuses (§2.3) |
| Processor class | `Qwen3VLProcessor` | [`processor_config.json`](../models/marlin2b/processor_config.json) |
| Video processor | `Qwen3VLVideoProcessor` | idem |
| Image processor | `Qwen2VLImageProcessor` / `Qwen2VLImageProcessorFast` | `processor_config.json` / [`preprocessor_config.json`](../models/marlin2b/preprocessor_config.json) — the two files disagree on the Fast suffix; immaterial for a video-only profile, recorded so nobody "fixes" one silently |
| Normalisation | mean/std `[0.5,0.5,0.5]`, `rescale_factor` 1/255, `resample` 3 (bicubic), `do_convert_rgb` true | `processor_config.json` |
| Patch geometry | `patch_size` 16, `temporal_patch_size` 2, `merge_size` 2 | `processor_config.json`, `config.json` `vision_config` |
| Runtime requirement | `transformers >= 5.7.0`, `torch >= 2.11.0`, `torchcodec`, `qwen-vl-utils >= 0.0.14`, `av`, `pillow` | [`MODEL_CARD.md`](../models/marlin2b/MODEL_CARD.md) "System requirements"; `model.env:10` records `transformers>=5.7.0, torchcodec` |

**Artifact content digests — registry claim needs `HF_TOKEN`; served bytes measured.** Two
corrections to earlier drafts, established by the coordinator on 2026-09-22: (1) the public
tree API does publish `.lfs.oid` for this repository, but because the repository is **gated**
an unauthenticated call returns each oid **masked as 64 `*` characters** (confirmed from two
different hosts, including the pilot box itself: the response body, not any local filter, is
what carried the asterisks — the "secret-redaction filter" reading in the previous draft was
wrong). With `HF_TOKEN` exported (an approved account, as `CLAUDE.md` requires for the
download) the same call returns the real oids, and for LFS the oid **is** the sha256 of the
contents. (2) The bytes actually served were hashed read-only on the pilot box
(`sha256sum` under `/opt/dlami/nvme/marlin2b`, files dated 2026-09-19 22:52–22:53 UTC):

```bash
# registry claim (needs HF_TOKEN; unauthenticated → oids are '*'*64)
curl -sS -H "Authorization: Bearer $HF_TOKEN" https://huggingface.co/api/models/NemoStation/Marlin-2B/tree/main \
  | python3 -c 'import json,sys; [print(e["path"], e["lfs"]["oid"], e["lfs"]["size"]) for e in json.load(sys.stdin) if e.get("lfs")]'
# served bytes (on the serving host)
sha256sum model-00001-of-00002.safetensors model-00002-of-00002.safetensors tokenizer.json
```

| File | Bytes (`.lfs.size` = on-disk size, confirmed) | Served-bytes sha256 (`meas.` pilot box, 2026-09-22) | Registry `.lfs.oid` |
|---|---:|---|---|
| `model-00001-of-00002.safetensors` | 4,999,157,736 | `5d78fa4dbd856dc89c01b99ffa92072fe31b8a1e6b31e87893734c80304983b7` | ⚠️ needs `HF_TOKEN` |
| `model-00002-of-00002.safetensors` | 444,519,488 | `01d40ec9ccf4c2ad8e755604468dd6ee4a5c6551553e5739a03beb4c0673d0db` | ⚠️ needs `HF_TOKEN` |
| `tokenizer.json` | 19,989,325 | `06b9509352d2af50381ab2247e083b80d32d5c0aba91c272ca9ff729b6a0e523` | ⚠️ needs `HF_TOKEN` |

`chat_template.jinja` on the box hashes to the same `273d8e0e…` as the template returned by the
model API, so the box holds the registry's template byte for byte. **Action for W3/I2B:** run
the authenticated command above on the serving host, confirm each oid equals the served-bytes
column, and record the equality in the serving-version record; a mismatch is a fail-stop. The
remaining ⚠️ after that is the runtime **image** digest (`serve.sh` pins the moving tag
`vllm/vllm-openai:nightly`), which no registry read can supply.

### 1.4 Prompt/harness pin (mode surface)

The prompts are constants in the gated `modeling_marlin.py` under a "Canonical
training-time prompts — DO NOT EDIT" banner
([`architecture.md` §1.4](../models/marlin2b/architecture.md)):

| Mode | Prompt text (verbatim, from `architecture.md` §1.4) | Default output cap | Launch status |
|---|---|---:|---|
| Caption | `Provide a spatial description of this clip followed by time-ranged events.\nFor each event, give the time range as <start - end> and a short description.` | 2048 | **supported** — the SOP workload's primary mode |
| Find (grounding) | `Identify the timestamps during which "{event}" takes place. Output the time range as "From <start> to <end>." (numbers in seconds).` | 64 | **supported** |
| Multi-find | `.find` repeated over ffmpeg-trimmed tails, spans remapped to global time | per find | **not a platform feature.** It is a *client* loop: N ffmpeg re-encodes plus N full prefills, and `.find` "always emits some span", so later spans are low-confidence spend ([`architecture.md` §1.4, §12 #11](../models/marlin2b/architecture.md)). A client may implement it with N ordinary requests; the endpoint offers no such operation |
| Multichunk reasoning | raw prompt only, "limited in this checkpoint" | — | **not supported / not advertised** ([`MODEL_CARD.md`](../models/marlin2b/MODEL_CARD.md) "Capabilities") |

Output format the caller must parse: caption mode returns `Scene: <paragraph>` then
`Events: <X.X - Y.Y> <description>` lines; find mode returns `From X.X to Y.Y.`
([`MODEL_CARD.md`](../models/marlin2b/MODEL_CARD.md) "Capabilities"). **The platform does
not parse, validate or schema-check this text** — it is `text` output (§2.4). A structured
event schema is a P-07 input, not a launch feature.

The prompt text is the caller's, not the platform's: `serve.sh`'s header and
`smoke.py:33-60` read the constants out of `modeling_marlin.py` so client and reference
path send identical bytes. Anything else is an untested prompt on a model trained with a
fixed one.

### 1.5 Video preprocessing profile `v1` — the pin, and the 1.91× fork

Two preprocessing paths disagree by **1.914×** and the gated config *confirms* the
conflict rather than resolving it ([`architecture.md` §6.3](../models/marlin2b/architecture.md)):

| Path | Where it comes from | Per-request video budget |
|---|---|---|
| **A — training-time** (`qwen-vl-utils` env vars `modeling_marlin.py` sets: `VIDEO_MAX_PIXELS=200704` **per frame**, `FPS=2.0`, `FPS_MAX_FRAMES=240`, `FPS_MIN_FRAMES=4`, `FORCE_QWENVL_VIDEO_READER=torchcodec`) | [`MODEL_CARD.md`](../models/marlin2b/MODEL_CARD.md) "Video preprocessing" | 240 × 200,704 px ⇒ **23,520 video tokens** |
| B — HF `Qwen3VLVideoProcessor` defaults | [`processor_config.json`](../models/marlin2b/processor_config.json): `fps: 2`, `min_frames: 4`, **`max_frames: 768`**, `size {longest_edge: 25165824, shortest_edge: 4096}` for the **whole** clip | 25,165,824 ÷ 2048 ⇒ 12,288 video tokens |

**Path A is the pin.** Serving at Path B gives the model half the frames or half the
resolution it was trained on — a silent quality regression, not an error.

**Profile `v1`, frozen** (`profile_version: "v1"`, the value `MediaRef.profile_version`
already defaults to — `apps/infrx-api/infrx/contracts/records.py:426`):

| Parameter | Value | Source line |
|---|---:|---|
| `fps` | 2.0 | `infrx/config.py:58` (`Settings.fps`) |
| `min_frames` | 4 | `infrx/config.py:59` |
| `max_frames` | 240 | `infrx/config.py:60` |
| `px_per_frame` | 200,704 (≈448×448) | `infrx/config.py:61` |
| frame count | `frames = clamp(round(fps × duration_s), 4, 240)`, then rounded **up to even** (one temporal patch is 2 frames) | `infrx/media/video.py:113-114` |
| `mm_processor_kwargs` sent to vLLM | `{"fps": 2.0, "min_frames": 4, "max_frames": 240, "size": {"shortest_edge": 4096, "longest_edge": frames × 200704}}` | `infrx/media/video.py:115-116`; applied at `worker/engine.py:579` and, on the legacy path, `gateway/routes/chat.py:50` |
| token math | 196 LLM tokens per 2-frame temporal patch; equivalently **`tokens = total_pixels ÷ 2048`** | [`architecture.md` §6.3](../models/marlin2b/architecture.md) |

`size.longest_edge` is a **whole-clip** pixel budget, not a per-frame one — that is the
single fact that makes the reproduction work, and it was established by measurement:

| Input | Path | Prompt tokens | Grid | Evidence |
|---|---|---:|---|---|
| `sample-10s.mp4` (10.1 s, 1920×1080) | A (`longest_edge = frames × 200,704` = 4,014,080) | **2,061** | `[10,28,28]` | `meas.` 2026-09-19, `models/marlin2b/results/bench.jsonl` row 1, `results/notes.md` findings 2–3 |
| same | B (processor default) | 12,221 | `[10,52,92]` | `meas.` 2026-09-19, `bench.jsonl` row 3 |
| same | `cap_pixels_per_frame=true` | 861 (**not** the training grid; first call 18 s) | — | `results/notes.md` finding 3 |

Caption **events were identical** across `transformers .caption()`, vLLM at Path B and
vLLM at Path A on that clip (`results/notes.md` finding 1) — that is a one-clip
processor-parity observation, not a quality result.

Two open items inside the pin:

- **`shortest_edge` is 4096 in code** (`media/video.py:116`) while
  `models/marlin2b/tokens.py:53-56` also probes `65536` (the *image* floor from
  `preprocessor_config.json`). ⚠️ **TO BE VERIFIED** which value the training path used;
  4096 is the one that produced the measured 2,061-token training grid, so it is the pin.
  Method: run `models/marlin2b/tokens.py` with both variants on one clip and compare
  `video_grid_thw`.
- **The 240-frame cap is a quality cliff priced as a saving.** Prefill is bounded at
  ~23,560 tokens for any duration, so a 10-minute clip costs what a 2-minute one does —
  at 0.40 effective fps, and an hour at 0.067 fps
  ([`architecture.md` §6.3](../models/marlin2b/architecture.md)). "Second-precise
  timestamps" past 2 minutes are interpolated from frames ≥2.5 s apart, **unmeasured**.
  The API cap (§2.5) keeps every accepted request at ≤120 s, i.e. inside 2 fps, so this
  cliff is currently unreachable through the endpoint — and that is a deliberate property
  of the profile, not an accident: **raising `MAX_VIDEO_SECONDS` above 120 changes serving
  quality and is a new serving version, not a configuration tweak.**

### 1.6 Capacity facts that follow from the pin

`est.` from the pinned geometry, not measured on target hardware:

| Duration | frames | video tokens | prefill (incl. scaffold) | KV BF16 (6 of 24 layers) |
|---|---:|---:|---:|---:|
| 2 s (min) | 4 | 392 | ~430 | 4.6 MiB |
| 30 s | 60 | 5,880 | ~5,920 | 68.9 MiB |
| 60 s | 120 | 11,760 | ~11,800 | 137.8 MiB |
| 120 s (API cap) | 240 | 23,520 | ~23,560 | 275.6 MiB |

Three qualifications on that table, none of which changes its conclusion:

- **The scaffold estimate is low.** `architecture.md` §6.3 adds "~30-40 tokens of chat scaffold
  + instruction prompt", but the committed measurements imply more: Path A gave 2,061 prompt
  tokens for 1,960 video tokens (grid `[10,28,28]`), i.e. **101 tokens** of overhead, and
  Path B gave 12,221 for 11,960 (grid `[10,52,92]`), i.e. **261** (`results/notes.md`
  findings 2-3). Budget ~100; it is ~0.4 % of a 120 s request either way.
- **Only 6 of the 24 layers hold a KV cache**, because the schedule is hybrid: 18 Gated-DeltaNet
  layers cache **0 bytes per token** and 6 full-attention layers cost 2,048 B/token each
  (`config.json` `text_config.layer_types`;
  [`architecture.md` §5.1](../models/marlin2b/architecture.md)). That is why the KV column is
  small for a 32 K context.
- **The KV column omits the GDN state.** Every concurrent sequence also holds a **fixed
  18.63 MiB** recurrent + convolution state, independent of context length
  ([`architecture.md` §5.3](../models/marlin2b/architecture.md)): at 120 s that is 275.6 MiB of
  KV **plus** 18.63 MiB of state, and at high concurrency the state is the term that stops
  being negligible.

Source: [`architecture.md` §6.3](../models/marlin2b/architecture.md). `--max-model-len
32768` (`serve.sh:27`) and `MAX_CONTEXT_TOKENS` 32,768
(`infrx/contracts/limits.py:113`) both cover the 120 s worst case plus the 2,048-token
output ceiling with ~7 K to spare. **A 120 s clip plus a 2,048-token answer is 25,608
tokens: the context is sized for the profile, with no room for a second clip** — which is
why `MAX_VIDEO_PARTS = 1` (§2.5) is a capacity fact and not only a taste.

Only measurement that exists for this model on any hardware (`meas.` 2026-09-19, AWS
`g6e.2xlarge`, 1× L40S 48 GB, 8 vCPU, `vllm/vllm-openai:nightly` pulled 2026-09-19,
`models/marlin2b/results/bench.jsonl`, four rows, two clips):

| clip | conc. | prompt tok | TTFT p50 | TPOT p50 | req/s | out tok/s |
|---|---:|---:|---:|---:|---:|---:|
| sample-10s (1080p, 5.5 MB) | 1 | 2,061 | 0.767 s | 6 ms | 0.501 | 100.1 |
| sample-10s | 8 | 2,061 | 3.352 s | 8 ms | 1.569 | 310.4 |
| sample-10s, Path B | 8 | 12,221 | 3.700 s | 8 ms | 1.472 | 289.9 |
| Big Buck Bunny (360p, 1 MB) | 8 | 1,928 | 0.662 s | 7 ms | 3.577 | 760.2 |

Read these as **p50-grade only**: `results/notes.md` "E1 tooling" records that the
committed `ttft_p95`/`latency_p95` come from 5–32 samples and would be suppressed by
`bench.py`'s own sample-sufficiency rule (p95 needs ≥60 accepted samples, p99 ≥300). Both
clips were sent repeatedly, so vLLM's multimodal cache may have absorbed decode cost
(finding 5). The finding that survives: **per-request video handling, not the LM, is the
bottleneck** — same token count, 1.57 clips/s at 1080p versus 3.58 clips/s at 360p
(finding 7).

---

## 2. Endpoint surface pin

Two code paths exist in the tree and they are **not the same contract**. Both are pinned
here because the deployed host runs the first and the launch target is the second.

| | Legacy F1 path (deployed 2026-09-20) | Pilot path (the launch target) |
|---|---|---|
| Mounted | **yes** — `ROUTERS = (health, models, chat)`, `infrx/gateway/app.py:22` | **no** — G1's validator exists (`gateway/routes/validate.py`), G2 mounts it |
| Ingress | `gateway/routes/chat.py` | `gateway/routes/{intake,ingress,validate}.py` |
| Parameters | whole body forwarded to vLLM, no allow-list | closed allow-list (§2.2) |
| Media | fetch → inline base64 `data:` URL to vLLM (`media/video.py:157-206`) | staged, tenant-scoped object refs (`media/store.py`) |
| Auth/metering | single key or Supabase key row, `usage.jsonl` | per-org key, holds/settlement in PostgreSQL |
| Accepted part types | `video_url` **and `input_video`** (`chat.py:38`) | `video_url` only |

`INFRX_MODE` unset ⇒ legacy behaviour is served and logged as `legacy`
(`infrx/config.py:validate_runtime`); `pilot` refuses to start without `DATABASE_URL`,
`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, and refuses to start **with**
`GATEWAY_API_KEY` set (R51). The launch profile is `INFRX_MODE=pilot`.

### 2.1 Routes

| Route | Shape | Status in this tree |
|---|---|---|
| `POST /v1/chat/completions` | OpenAI-style chat; sync JSON or SSE | **mounted** (legacy `chat.py:17`); pilot version pending G2 |
| `GET /v1/models` | model list | **mounted** (`models.py:7`) |
| `GET /health` | readiness | **mounted** (`health.py`) |
| `POST /v1/jobs`, job status/result/events | explicit async extension; also reached by `Prefer: respond-async` | **implemented, not mounted** (G3, on fakes): `routes/jobs.py` serves the 202 (`Relay.on_async`), status, result, events and `DELETE`; `ROUTERS` gains `jobs` at the cutover. Owner G3 |
| `POST /v1/uploads`, PUT destination, `POST /v1/uploads/{handle}/complete` | owned bounded upload | **not implemented.** Contract records (`UploadCreated`/`UploadState`) and the `infrx-upload:` reference form exist; routes are G4U, store operations are M3 (`media/store.py:287-291` raise `NotImplementedError`) |
| `POST /v1/feedback` | feedback | out of scope for this profile |

Ordinary chat **never** silently becomes a 202: the mode is decided by `stream` and
`Prefer`, and `stream` + `respond-async` together is `400 invalid_request`
(`validate.py:348-353`).

### 2.2 Parameters (closed set)

`SUPPORTED` (`validate.py:44-49`) — anything else is `400 unsupported_parameter` **naming
itself**, never silently ignored:

| Parameter | Accepted | Range enforced at ingress |
|---|---|---|
| `model` | string ≤128 chars, must resolve in the served table | else `403 model_not_entitled` (`validate.py:444-448`) |
| `messages` | §2.3 | — |
| `stream` | bool | `validate.py:343` |
| `max_tokens` / `max_completion_tokens` | int, must agree if both given | `1..MAX_OUTPUT_TOKENS` = `1..2048`; default = the ceiling (`validate.py:459-473`) |
| `temperature` | number | `0.0 .. 2.0` (`validate.py:493`) |
| `top_p` | number | `0.0 .. 1.0` |
| `presence_penalty`, `frequency_penalty` | number | `-2.0 .. 2.0` |
| `n` | int | **`1` only** (`validate.py:487-489`) |
| `stop` | string or ≤4 strings, each 1..64 code points | `validate.py:161-173` |
| `seed` | int | `0 .. 2^63-1` |

`UNSUPPORTED`, refused by name (`validate.py:52-54`): `tools`, `tool_choice`, `functions`,
`function_call`, `response_format`, `logit_bias`, `logprobs`, `top_logprobs`,
`parallel_tool_calls`, `price_snapshot`. A `null` value for any parameter is
`400`, not "absent" (`validate.py:481-485`). The engine adapter refuses the same set again
plus `guided_json`/`guided_regex`/`guided_choice`/`guided_grammar`/`structured_outputs`,
`echo`, `best_of`, `stream_options`, `model` (`worker/engine.py:88-95`) and forwards only
`temperature, top_p, top_k, min_p, seed, stop, presence_penalty, frequency_penalty,
repetition_penalty` (`engine.py:83-86`). `top_k`, `min_p` and `repetition_penalty` are
therefore **engine-forwardable but not ingress-accepted** — a documented asymmetry, not a
promise: a caller cannot reach them.

`mm_processor_kwargs` is **not** a caller parameter. It is refused as an unknown key
anywhere in the body and in every message part (`validate.py:476-480` and `check_messages`, `validate.py:253-332`), and
set server-side from the measured duration (§1.5). A caller cannot choose its own frame
budget; that is what makes the profile a pin.

### 2.3 Message shape (allow-list, R58)

Equality of key sets, not "contains" (`validate.py:253-332`, re-checked at the engine
boundary in `engine.py:470-533`):

- message is **exactly** `{role, content}`; `role` ∈ `system`, `user`, `assistant`
  (case-sensitive).
- `content` is a string, or a non-empty list of parts.
- a part is **exactly** `{"type": "text", "text": …}` or
  `{"type": "video_url", "video_url": {"url": …}}`. `image_url`, `input_audio`, `file`,
  `input_video`, an extra key beside `type`, `"Text"`, a message-level `tool_calls` — all
  refused.
- `video_url.url` is one of three forms (`validate.py:209-250`):
  `http(s)://…` (≤8,192 chars), `data:<video mime>;base64,<payload>`, or
  `infrx-upload:upl_<handle>`. A bare `upl_…`, `file:`, `s3:`, a path, userinfo (`@`),
  a backslash, control characters, `U+2028`/`U+2029` are all refused.
- text must be storable: no unpaired surrogates, no `NUL` (`validate.py:144-158`).

The chat template ships a `<tool_call>` block (§1.3) and the base class supports images
(`--limit-mm-per-prompt '{"video":1,"image":4}'`, `serve.sh:40`). **Neither is part of the
launch profile**: images are refused at ingress and again at the engine
(`engine.py:552-553` "the pilot accepts video media only"), tools are refused by name.
The engine's own tolerance is not the platform's contract.

### 2.4 Response modes and output capabilities

| Capability | Value | Source |
|---|---|---|
| Sync JSON | supported | `chat.py`; fixture `contracts/fixtures/v1/chat_success_nonstream.json` |
| Output SSE stream | supported; `stream_options.include_usage` forced on; cursor/`id` = `<generation>-<sequence>`; `Last-Event-ID` resume | 08 §3; `engine.py:564-565`; `chat.py:53` |
| Explicit async | `Prefer: respond-async` → job handle; `Preference-Applied: respond-async` | implemented by G3 (fakes); served once the cutover mounts the jobs router (§2.1) |
| Output modalities | **text only** | `PreparedRequest` has no other output; caption/find return text (§1.4) |
| Tools / function calling | **no** | refused by name in both halves |
| Structured output / JSON schema | **no** | idem |
| Stream *input* (live video) | **no** | [07-api-contracts.md](../platforms/07-api-contracts.md) "Streaming distinctions" §2–3; a separate transport and contract |
| Actions / actuation | **no** | §5.3 |
| Reasoning exposure | `visible` only; one leading `<think>…</think>` stripped; `raw` is internal (trace capture and usage evidence), not relayed and not journalled | **R58** — the guarantee lives in the ruling and in the journal/relay writing `visible` only, **not** in the adapter's payload builder: `_delta_payload` still emits `raw` *and* a transitional `content` alias of it (`engine.py:366`), which F2R item 2 removes. `worker/reasoning.py` is the boundary-independent filter |
| Usage | OpenAI token fields; authoritative only when the last usage object arrives after the final content delta and is consistent | R58; `engine.py:1008-1062` |

### 2.5 Limits

| Limit | Value | Name / source |
|---|---:|---|
| Request bytes | 100,663,296 (96 MiB) | `MAX_REQUEST_BYTES`, `limits.py:57` |
| Decoded media bytes | 67,108,864 (64 MiB) | `MAX_MEDIA_BYTES`, `limits.py:59`; legacy `MAX_VIDEO_MB=64`, `config.py:48` |
| Video duration | **120 s** | `MAX_VIDEO_SECONDS`, `limits.py:60`; enforced on the legacy path at `media/video.py:204` |
| Videos per request | **1** | `MAX_VIDEO_PARTS`, `validate.py:93`; re-checked `engine.py:555-556`; `--limit-mm-per-prompt video:1` |
| Messages per request | 64 | `MAX_MESSAGES`, `validate.py:91` |
| Parts per message | 16 | `MAX_PARTS_PER_MESSAGE`, `validate.py:92` |
| Text, whole request | 131,072 Unicode **code points** (R54) | `MAX_TEXT_CODEPOINTS`, `validate.py:94` |
| URL chars | 8,192 | `MAX_URL_CHARS`, `validate.py:95` |
| Model id chars | 128 | `MAX_MODEL_CHARS`, `validate.py:96` |
| Output tokens | 2,048 | `MAX_OUTPUT_TOKENS`, `limits.py:112` — equal to the vendor caption default |
| Context tokens | 32,768 | `MAX_CONTEXT_TOKENS`, `limits.py:113`; `serve.sh --max-model-len 32768` |
| `Idempotency-Key` | ≤255 chars | `MAX_IDEMPOTENCY_KEY_CHARS`, `limits.py:30` |
| Allowed video MIME | `video/mp4`, `video/webm`, `video/quicktime`, `video/mpeg` | `config.py:30`; extension fallback only when the server declines to declare a type |
| Concurrency caps | 64 active jobs / 16 per org / 8 per key; 8 preparing | `limits.py:107-109`, `max_preparing_jobs` `limits.py:74`; legacy `MAX_INFLIGHT=16` → 429 |
| Engine concurrency | `ENGINE_MAX_NUM_SEQS` 8 — **the pilot setting, not the running engine.** The deployed unit starts `serve.sh --max-num-seqs 32` while `serve.sh` itself passes no such flag, so the engine admits 32 sequences while the platform plans for 8. W3 reconciles (D13) | `limits.py:110` vs `apps/infrx-api/deploy/marlin2b-vllm.service:12` |
| Native position limit | `text_config.max_position_embeddings` = **262,144**, `rope_type: default` with no scaling config | [`config.json`](../models/marlin2b/config.json); [`architecture.md` §5.3](../models/marlin2b/architecture.md). The 32,768 above is a **deployment choice** (`serve.sh:27` and `MAX_CONTEXT_TOKENS`) sized to the 120 s profile, not a model limit |
| Deadlines/budgets (snapshot at admission, R4) | preparation 120 s; queue 10 s sync/stream, 600 s async; generation 300 s; TTFT 60 s; stall 20 s | `limits.py:70-81` |
| Retention | result 24 h; processing cache 7 d; journal chunk 1 h; idempotency 24 h after terminal | `limits.py:97-102` |

Every one of these is **provisional engineering data, not a measured or negotiated SLO**
(`limits.py:49` "Provisional engineering limits until measured"). §5.2 is where they
become predeclared criteria.

### 2.6 Capability record (07-api-contracts.md schema v2), actual values

Filled from the lines above. This replaces the illustrative fixture in
[`07-api-contracts.md`](../platforms/07-api-contracts.md) for Marlin at launch.

```json
{
  "schema_version": 2,
  "api_family": "chat_completions",
  "model_id": "nemostation/marlin-2b",
  "input_modalities": ["text", "video"],
  "output_modalities": ["text"],
  "stream_output": true,
  "stream_input": false,
  "tools": false,
  "structured_output": false,
  "input_schema_ref": "infrx.request.chat.v1",
  "output_schema_ref": "infrx.response.chat.v1",
  "preprocessing_profile_ref": "marlin2b.video.v1",
  "billing_meter": "tokens-v1",
  "response_modes": ["sync", "stream", "async"],
  "modes": ["caption", "find"],
  "limits": {
    "max_videos_per_request": 1,
    "max_video_seconds": 120,
    "max_media_bytes": 67108864,
    "max_request_bytes": 100663296,
    "max_messages": 64,
    "max_parts_per_message": 16,
    "max_text_codepoints": 131072,
    "max_output_tokens": 2048,
    "max_context_tokens": 32768,
    "allowed_video_mime": ["video/mp4", "video/webm", "video/quicktime", "video/mpeg"],
    "max_active_jobs_per_key": 8,
    "max_active_jobs_per_org": 16
  },
  "serving_version": {
    "model_repo": "NemoStation/Marlin-2B",
    "model_commit": "fd111fca4fc7897876fb0d7e9df22ca5ac8ab965",
    "weight_shard_digests": ["5d78fa4dbd856dc89c01b99ffa92072fe31b8a1e6b31e87893734c80304983b7", "01d40ec9ccf4c2ad8e755604468dd6ee4a5c6551553e5739a03beb4c0673d0db"],  // served bytes, meas. pilot box 2026-09-22; registry oid equality ⚠️ needs HF_TOKEN (W3)
    "tokenizer_digest": "06b9509352d2af50381ab2247e083b80d32d5c0aba91c272ca9ff729b6a0e523",  // idem
    "chat_template_sha256": "273d8e0e683b885071fb17e08d71e5f2a5ddfb5309756181681de4f5a1822d80",
    "architecture_override": {"architectures": ["Qwen3_5ForConditionalGeneration"]},
    "eos_token_ids": [248044, 248046],
    "dtype": "bfloat16",
    "quantization": null,
    "speculative_decoding": null,
    "preprocessing_profile": {
      "profile_version": "v1",
      "fps": 2.0,
      "min_frames": 4,
      "max_frames": 240,
      "px_per_frame": 200704,
      "size": {"shortest_edge": 4096, "longest_edge": "frames * 200704"},
      "decoder": "engine default — TO_BE_VERIFIED (torchcodec on the vendor path)"
    },
    "runtime_image": "TO_BE_VERIFIED — serve.sh default is vllm/vllm-openai:nightly, a moving tag; W3 pins a digest",
    "hardware": "TO_BE_VERIFIED — measured only on 1x L40S (g6e.2xlarge)"
  }
}
```

The remaining `TO_BE_VERIFIED` fields are **launch blockers for an honest capability record**,
not cosmetic. Three of the four the first draft listed turned out to be **published and
obtainable without a token** — the two weight-shard digests and the tokenizer digest are
`.lfs.oid` values on the public tree endpoint (§1.3), so the record's owner transcribes them
rather than waiting for gated access; what is left is the **runtime image digest** (`serve.sh`
pins the moving tag `vllm/vllm-openai:nightly`, which no registry read can resolve for us),
the **decoder** actually used by the pinned engine, and the **hardware** the envelope was
measured on. Without the image digest in particular, "pinned serving version" is a label:
two deployments can serve different engine builds under the same record.

---

## 3. SOP client recipe for a large recorded-video dataset

Scope: **thousands of recorded clips, processed once each, resumable.** It uses only the
primitives above. There is no batch API, no server-side dataset object and no new route.

**Specified versus served, stated up front.** Two things this recipe uses are contracts that
do not answer yet in this tree: the `infrx-upload:` media form (§3.3) and
`Prefer: respond-async` with the job status/result routes it implies (§3.5; implemented by
G3 on fakes, not mounted until the cutover). `ROUTERS =
(health, models, chat)` today (§2.1, discrepancy D12), so a sweep run against this tree must
use the `data:` or `http(s)` media form on sync or SSE, and those two rows become executable
when G3 and G4U/M3 land. Everything else below — item keys, idempotency scope, segmentation,
concurrency caps, failure classes, usage reconciliation — is served today.

### 3.1 Identity and idempotency

| Concept | Rule |
|---|---|
| `source_id` | the recording the client owns (one camera/session file). Client-side only. |
| `episode_id` | one procedure attempt inside a source. Client-side only. |
| `segment_index` | 0-based index of a ≤120 s window inside the episode (§3.2) |
| `item_key` | `sha256("<dataset_version>\x1f<source_id>\x1f<episode_id>\x1f<segment_index>\x1f<start_s>-<end_s>\x1f<prompt_version>\x1f<profile_version>")`, first 32 hex |
| `Idempotency-Key` | `sop1.<item_key>` — ≤255 chars (`limits.py:30`). Scope is **org + operation + key** (`IdempotencyRef`, `validate.py:357-367`), so two keys of one org cannot collide across operations and two orgs cannot collide at all |

The platform stores the canonical payload digest with the key
(`validate.payload_digest`, `validate.py:385-404`), so:

- same key, **same** payload ⇒ replay of the original outcome, `Idempotency-Replayed: true`;
- same key, **different** payload ⇒ `409 idempotency_conflict`, no second job;
- re-submitting a `request_id` outside the idempotency path ⇒ `409 state_conflict`, no side
  effects (R6);
- after `IDEMPOTENCY_TTL_S` (24 h post-terminal) a key expires ⇒ `410 idempotency_expired`.

Therefore **`prompt_version` and `profile_version` must be inside `item_key`**: changing
the prompt or the preprocessing profile is a different question about the same clip, and
reusing the key would replay the old answer or 409.

The digest is stable under key reordering and whitespace but **not** under changing the
inline payload by one byte, so a client that re-encodes a segment gets a new payload and
must derive a new `item_key` (the segment bounds and a content digest of the segment
belong in it if the client re-cuts). Recommended: include the segment's own sha256.

### 3.2 Segmentation

Fixed by the profile, not a preference:

- **≤120 s per item** — `MAX_VIDEO_SECONDS`; a longer clip is `400`.
- **≤120 s also keeps 2 fps** — past 240 frames the sampler silently drops below the
  trained rate (§1.5). A 120 s segment is the largest item that is both accepted and
  sampled at the training rate.
- Segment with **documented overlap and boundaries**, and record them: an event crossing a
  boundary is seen twice or truncated. [04-verification.md](../plan/04-verification.md)
  "For Marlin SOP evaluation" requires this, and requires that correlated segments are not
  counted as independent samples.
- One video per request (`MAX_VIDEO_PARTS = 1`): a multi-camera episode is N items, not one
  request.

### 3.3 Transport per item

Pick one media form per item and keep it stable within a run:

| Form | When | Cost |
|---|---|---|
| `infrx-upload:upl_…` | the preferred form for a dataset: the object is staged once, is tenant-scoped, and a retry re-uses it | **specified, not served** — the upload routes are unmounted and `MediaStore.create_upload`/`finalize_upload` raise `NotImplementedError` (§2.1, D12) |
| `data:<mime>;base64,…` | works today; the only form that needs no public hosting | +33 % bytes against the 64 MiB media cap and the 96 MiB body cap; base64 is CPU work the client must do off its event loop (`bench.py:501-503`) |
| `http(s)://…` | the client already hosts the clips publicly | every fetch is re-validated against the SSRF policy; a private/loopback/link-local/CGNAT/NAT64/Teredo address is refused (`media/video.py:18-60`, `media/fetch.py`) |

### 3.4 Bounded concurrency and pacing

- In-flight per key ≤ **8**, per org ≤ **16** (`limits.py:107-109`). A dataset client runs
  at most 8 outstanding requests per key and treats `429 capacity_exhausted` /
  `rate_limited` as normal, honouring `Retry-After` (always present on 429/503).
- Open-loop arrival is the honest shape for a dataset sweep; `bench.py --rate` already
  implements Poisson arrivals and reports `latency_from_scheduled_s`/`schedule_lag_s` so
  coordinated omission is visible (`results/notes.md` E1 review).
- Async items get a 600 s queue budget versus 10 s interactive (`limits.py:77-78`), so a
  bulk sweep belongs on `Prefer: respond-async`, not on sync chat.
- Preparation is separately capped (`MAX_PREPARING_JOBS` 8): a burst of large uploads is
  rejected at admission rather than queued forever.

### 3.5 Explicit async, polling and resume

1. `POST /v1/chat/completions` with `Prefer: respond-async` and the item's
   `Idempotency-Key` ⇒ job handle (`job_` + 32 URL-safe bytes, never derived from the
   request id — 08 §3) plus `Inference-Id` = `request_id`.
2. Persist `(item_key, request_id, job_handle)` **before** the next item. That mapping is
   the resume state; the idempotency key is the backstop if it is lost.
3. Poll job status; read the result while it exists (`RESULT_TTL_S` = 24 h).
4. Resume after a crash: for every item without a terminal outcome, re-send the **same**
   `Idempotency-Key` and the **same** payload. Either the original job's outcome is
   replayed (`Idempotency-Replayed: true`) or, past the 24 h window,
   `410 idempotency_expired` tells the client to re-derive and re-run rather than assume.
5. Streaming resume is `Last-Event-ID: <generation>-<sequence>`; a gap past
   `JOURNAL_CHUNK_TTL_S` (1 h) is `410 replay_gap` — an explicit gap, never silent
   truncation.
6. **No duplicate accepted item:** MARLIN-SOP requires that interruption and resume create
   no second accepted job or charge. The mechanism is entirely the idempotency scope plus
   the payload digest; a client that regenerates keys per attempt defeats it. That is the
   single most important client-side rule in this recipe.

### 3.6 Failure classes and what the client does

| Outcome | Meaning | Client action |
|---|---|---|
| `400 unsupported_media` / `media_fetch_failed` | segment is not a supported video, too large, unreachable, or the URL resolves to a blocked address | quarantine the item with the reason class; never retry unchanged |
| `400 invalid_request` / `unsupported_parameter` | the client built a shape the profile does not serve | fix the client; retrying is pointless |
| `400 context_length_exceeded` | prompt + ceiling > 32,768 | shorten the segment or the output ceiling |
| `401` / `403 forbidden` / `org_suspended` / `model_not_entitled` | credential or entitlement | stop the sweep; do not burn the dataset against 403s |
| `402 insufficient_credit` | wallet exhausted | pause; resume with the same keys after funding |
| `409 idempotency_conflict` | same key, different payload | a client bug: the key is not a function of the payload |
| `410 result_expired` / `journal_expired` / `replay_gap` / `idempotency_expired` | past a retention boundary | re-derive and re-run; record it as a re-run, not a duplicate |
| `413 request_too_large` | body over 96 MiB | re-segment or switch to the upload form |
| `429 capacity_exhausted` / `rate_limited` | bounded honestly | honour `Retry-After`; do not widen concurrency |
| `503 dependency_unavailable`, `504 deadline_exceeded` | platform-side | retry with backoff; **these are platform-absorbed, not billed** (R21) |
| in-stream `status_unknown` | the terminal state is not known to the client | re-read the job; never assume success or failure |

**Denominators.** A sweep report counts accepted, rejected and failed separately, at
attempt level, and a retry must not hide a 429 (`results/notes.md` E1 review). A 200 whose
stream ends without `[DONE]`, `finish_reason` or usage is `failed`/`truncated_stream`, not
accepted.

### 3.7 Exact usage reconciliation

- Billable terminal causes are **only** `completed`, `client_cancelled` and
  `client_disconnected`, and only with **authoritative** usage (R21). Every platform-caused
  cause — `sync_deadline`, `deadline_exceeded`, `queue_wait_expired`, `engine_error`,
  `preparation_failed`, … — is free.
- Usage is authoritative only when the last usage object arrives after the final content
  delta and is consistent with the stream; otherwise it is `unknown` (R58).
- `unknown` usage becomes `held_unknown` with a `reconcile_after` of 24 h
  (`UNKNOWN_USAGE_RECONCILE_S`), then `released_platform_absorbed`. A client reconciling
  its own ledger must therefore expect a small set of items in
  **pending-reconciliation** for up to 24 h and must not treat them as either charged or
  free before that.
- CREDIT is not USD and token fields never carry credit quantities
  ([07-api-contracts.md](../platforms/07-api-contracts.md)); the client reconciles tokens
  from `usage` and credits from the usage resource.
- Rates and the serving revision are pinned at admission; a mid-sweep rate or alias change
  cannot alter an accepted item or its replay (R45/R53, 07-api-contracts "Resolution and
  changes").

### 3.8 Owned synthetic SOP fixture `sop-synth-v1` (description; not built here)

Purpose: exercise the SOP journey and temporal-event plumbing **without any copyrighted,
customer or robotics-vendor data**, and with ground truth that exists by construction.

- **Generated, not collected.** Clips are rendered by the **already-pinned ffmpeg 7.0.2
  amd64-static** build that `models/marlin2b/corpus/build.py` fetches and verifies by
  sha256, with the same bit-exact flags (`-threads 1 -fflags +bitexact
  -flags:v +bitexact`), so every clip's sha256 is a real pin. No media is committed; a
  manifest holds recipes, hashes and probed geometry, exactly as
  `models/marlin2b/corpus/manifest.json` does.
- **Content.** A synthetic "work cell": coloured rectangles as parts, a moving rectangle
  as a gripper/hand, a text overlay naming the current step. No people, no logos, no
  real footage. Generated from `lavfi` sources plus `drawbox` (the pinned ffmpeg 7.0.2 static build
  has no `drawtext` — 486 filters, none of them text; E1B replaces the overlay with a part colour +
  tally, `meas.` 2026-09-22).
- **Scripted procedure.** Each clip is a list of steps with exact start/end seconds. The
  script **is** the ground truth: `{step_id, label, start_s, end_s}`. Because the renderer
  is given those numbers, the labels are not annotations to be trusted — they are inputs.
- **Size and spread.** 12 clips: durations 8 s, 30 s, 60 s and **120.0 s**, the last being the
  240-frame worst case. (An earlier draft used 115 s and called it the worst case, which is
  wrong: `frames = clamp(round(2.0 × duration_s), 4, 240)` then rounded **up to even**
  (`media/video.py:113-114`) makes 115 s **230** frames and 22,540 video tokens. The 240 cap is
  first reached just above **119.25 s** — `round(2 × 119.26) = 239`, bumped to 240 — and Python's
  round-half-to-even makes exactly 119.25 s give 238, so 120.0 s is the clip to build: it is the
  worst case with margin and it is the API cap.) 3–7 steps each (11 clips); one clip with exactly two steps
  0.4 s apart — inside one profile-v1 frame period (0.5 s at 2 fps), which is the exclusive
  resolution threshold the fixture asserts (E1B reading; the earlier "≤1 s" was satisfied incidentally by ordinary spacing), one with a step spanning a segment boundary
  (§3.2 overlap), one 2-step clip where the steps run **out of order** and one where a
  step is **absent** (so a rubric can be shown to punish a hallucinated step — `.find`
  "always emits some span").
- **Not a quality benchmark.** It has no scene richness, no camera motion, no real
  procedure, and it is trivially unlike the training distribution. It proves the API
  journey, the temporal-event plumbing and the evaluator harness. **A score on
  `sop-synth-v1` is never an accuracy claim about real robotics video** (§5).
- Existing licensed corpus for *performance* work stays
  `models/marlin2b/corpus/` (64 distinct clips, 4 CC-BY/public-domain sources, 16
  geometries, 2–112 s, plus four corrupt-media negatives). `sop-synth-v1` complements it;
  it does not replace it.

---

## 4. Discrepancies between the runtime code and the intended contract

Inputs for the M2 / G2 / W3 / M3 / E briefs. Each is a fact about this tree at base
`ec6c548`, with the line that shows it.

| # | Discrepancy | Evidence | Owner |
|---|---|---|---|
| D1 | **`NormalizedRequest.messages` keeps the caller's raw `video_url.url`.** `Validator.normalize` stores `messages=messages`, the validated body's own tuple (`validate.py:509`), and `media=()` (`validate.py:519`). So the accepted record — which is what `MediaStore.stage` makes durable (`store.py:246`) — contains the customer's URL or the whole inline base64 payload. 02 step 1 wants the canonical payload staged with refs, and `payload_digest` already tokenises inline media *for the digest only* (`validate.py:385-404`). | `validate.py:506-524` | G2 (rewrite `{url}` → `{ref}` at acceptance), M2 |
| D2 | **M1 never sets `duration_s`, and the engine refuses without it.** `MediaStaging.materialize` builds a `MediaRef` with no `duration_s` (`store.py:178-181`) and nothing else probes; `MediaRef.duration_s` defaults to `None` (`records.py:427`). `VllmEngine.upstream_body` raises `UnsupportedMedia` unless the video ref carries a positive finite duration (`engine.py:570-578`), because the frame budget is computed from it. **With the real M1 store, every video request fails at the engine.** | `store.py:178`, `engine.py:570-578` | M2 (duration probe during preparation; `PROBE_TIMEOUT_S` 10 s already exists, `limits.py:67`) |
| D3 | **The engine is handed a bare object key as `video_url.url`.** `_part` returns `{"type":"video_url","video_url":{"url": ref.storage_ref}}` (`engine.py:530`), i.e. `media/<org>/<profile>/<digest16>/source` — not something vLLM can open. The legacy path works because it inlines a `data:` URL instead (`media/video.py:201`, `chat.py:45-48`). Nothing in the tree bridges key → bytes for the pilot path. | `engine.py:509-530`, `store.py:141-147` | M2 + W3 (decide: presigned URL, local path mount, or inline; it is a serving-profile decision because it changes decode locality) |
| D4 | **`upload_created.json` fixture's `destination_ref` is org/environment-qualified.** The fixture says `infrx-upload:pilot:upl_…` while `validate.check_video_ref` accepts only `infrx-upload:` + `UPLOAD_HANDLE_RE` (`validate.py:233-241`, `ids.py:27`) and deliberately refuses a qualified form ("an org-qualified reference invites cross-tenant probing", `validate.py:234-236`). The wave-2 handoff §3 records `infrx-upload:upl_<id>` **only** as settled but not yet written into 08 §10. | `contracts/fixtures/v1/upload_created.json`, `validate.py:233-241` | M3 (reconcile), coordinator (record the ruling in 08 §10) |
| D5 | **The frozen `storage_ref` in three v1 fixtures cannot pass the engine's own guard.** `normalized_request.json`, `media_ref.json` and `prepared_request.json` all carry `media/1a1a1a1a/22/source.mp4`. `check_storage_ref` requires a **full lowercase UUID** org segment and refuses anything else as `not_found` (`engine.py:281-295`, `STORAGE_REF_PATTERN` `engine.py:124-127`), and M1 builds `media/<full uuid>/<profile>/<16 hex>/source` (`store.py:141-147`). A fixture that the pinned adapter refuses is not a contract. | fixtures vs `engine.py:124-127` | F (fixture revision) |
| D6 | **`prepared_request.json` has one media ref and zero media parts.** Its single message is `{"role":"user","content":"Describe what happens in this clip."}` with `media` of length 1, so `messages_for` raises "0 media parts but 1 prepared references" (`engine.py:504-506`). R58 requires one media part per staged ref, in order. | fixture vs `engine.py:504-506` | F (fixture revision) |
| D7 | **`payload_ref` shapes disagree.** Fixtures use `payloads/1a1a1a1a/11/request.json`; `Validator` sets `payloads/{org_id}/{request_id}.json` (`validate.py:515`) and `stage` ignores the caller's value and builds `payloads/<org uuid>/<request_id>.json` itself (`store.py:249`). Harmless at runtime (the store wins) but the fixture documents a path nothing produces. | fixtures vs `validate.py:515`, `store.py:249` | F |
| D8 | **`model_revision` is `nemostation/marlin-2b@2026-09-01`** — a date that is neither the artifact's `lastModified` (2026-05-30) nor its commit — and it is **not confined to one or two fixtures**: it appears in **15** of the frozen v1 fixtures, in two Python modules, and in a third, *unprefixed* form (`marlin-2b@2026-09-01`) in three console files. Full set below, because a partial rename would leave two revisions in one tree. The public id `nemostation/marlin-2b` is `Settings.model_id` (`config.py:45`) and the engine's served name is `marlin2b` (`serve.sh:36`, and `chat.py:32` overwrites the body's model with it). Recommend the revision carry the artifact commit, e.g. `nemostation/marlin-2b@fd111fca`. | the 20 files listed in integration request 5, `config.py:45`, `serve.sh:36` | F + G1R (served-model table), D1R (registry rows) |
| D9 | **`MAX_VIDEO_SECONDS` is unenforced on the new media path.** The legacy `Media.prepare_video` refuses over 120 s (`media/video.py:204-205`); `MediaFetcher`/`MediaStaging` enforce bytes (`store.py:173-174`) but never duration, because nothing probes it (D2). So the pilot path has a byte cap and no duration cap. | `store.py:158-190` vs `media/video.py:204` | M2 |
| D10 | **The deployed legacy path accepts a part type the contract does not.** `chat.py:38` treats `input_video` like `video_url` and forwards the whole caller body (including any `mm_processor_kwargs`) to vLLM, with no parameter allow-list. Anything published as "the API" today is looser than the profile in §2. | `chat.py:33-50` | G2 at cutover (the legacy shim and its tests retire together, R48) |
| D11 | **`bench.py` uses `upload://<handle>`**, not `infrx-upload:upl_…` (`bench.py:63`, `bench.py:508`), and sends **no `Idempotency-Key`** at all. The load client therefore cannot exercise the resume/no-duplicate property MARLIN-SOP requires. | `bench.py:63,508`; no `Idempotency-Key` in the file | E1B |
| D12 | **`ROUTERS = (health, models, chat)`** — the pilot ingress, jobs and upload routes are not mounted, so §2.1's async and upload rows are *specified, not served*. Any capability document published before G2/G3/G4U must say so. | `gateway/app.py:22` | G2/G3/G4U |
| D13 | **`serve.sh` pins a moving tag.** `IMAGE=vllm/vllm-openai:nightly` (`serve.sh:23`) cannot be a serving-version pin; the measured rows were taken on "nightly pulled 2026-09-19". Also `serve.sh` does **not** pass the B300 non-negotiables from the research recommendation (`--mamba-cache-mode=align`, `--block-size 128`, `--media-io-kwargs`, `--mm-processor-cache-type shm`) and does not re-supply both EOS ids. **The deployed unit and the platform also disagree on engine concurrency:** `apps/infrx-api/deploy/marlin2b-vllm.service:12` starts `serve.sh --max-num-seqs 32`, while `limits.py:110` plans for 8 and `serve.sh` passes no flag of its own (§2.5). | `serve.sh:23-42` (the file is 42 lines) vs [`research/models/marlin2b/README.md`](../models/marlin2b/README.md) "How to run it" and its non-negotiables list; `deploy/marlin2b-vllm.service:12` | W3 |
| D14 | **`top_k`/`min_p`/`repetition_penalty` asymmetry** between ingress (refused as unknown) and the engine allow-list (forwarded) — §2.2. Harmless today, but a caller reading the engine's list would believe they are available. | `validate.py:44-49` vs `engine.py:83-86` | G1R (decide: add with ranges, or state the refusal) |

---

## 5. Endpoint conformance is not SOP-quality certification

### 5.1 The separation

**Endpoint conformance** — what this profile can be held to, with software and one GPU:
the request/response shapes of §2, the refusals, the caps, idempotent resume with no
duplicate accepted item, exact usage reconciliation, processor/output **parity** between
the `transformers` reference path and the served engine on identical inputs.

**SOP-quality certification** — what the endpoint may **not** claim: that Marlin's
captions and spans are correct for a customer's procedures. It needs a labelled rubric, a
licensed dataset, an episode split and thresholds, none of which exist here.

[04-verification.md](../plan/04-verification.md) MARLIN-SOP is satisfied by the first and
explicitly forbids inferring the second; its "For Marlin SOP evaluation" paragraph adds
that a generic text judge alone cannot certify procedure compliance or physical safety.

### 5.2 P-18 — provisional performance criteria for E1B (**explicitly provisional**)

No workload owner has supplied targets. Everything here is **provisional engineering
criteria, labelled as such, derived only from this repository's own pinned limits and its
single L40S measurement**. None of it is a commitment, an SLO, or a target to tune toward,
and E1B may replace any row with a measured envelope.

| Axis | Provisional criterion | Where it comes from | Status |
|---|---|---|---|
| Representative workload | the 64-clip licensed corpus (`--subset full`) for performance, `sop-synth-v1` for the journey; 2–112 s durations, 16 geometries, 16 prompts, mixed `video_b64`/`video_url`/`upload`/`text` forms, mixed tenants, cold and warm | `models/marlin2b/corpus/README.md`; §3.8 | **provisional (P-18)** |
| Sample sufficiency | a reported pN needs ≥3 accepted samples beyond it: p50 ≥6, p95 ≥60, p99 ≥300 — already enforced by `bench.py` | `results/notes.md` E1 tooling | **method, not a target** — keep |
| Arrival model | open-loop Poisson (`--rate`) **and** fixed concurrency, both reported, with `schedule_lag_s` | `bench.py:346`; `results/notes.md` | **method** — keep |
| Denominators | accepted / rejected / failed counted at attempt level; a retry may not hide a 429; a 200 without `[DONE]`/`finish_reason`/usage is `failed` | `results/notes.md` E1 review | **method** — keep |
| Baseline to beat | the four committed L40S rows, read as p50-grade: TTFT p50 0.767 s @conc 1 and 3.352 s @conc 8 (1080p, 2,061 prompt tokens); 1.569 req/s @conc 8; TPOT p50 6–8 ms | `bench.jsonl` | **`meas.` 2026-09-19, two clips only — not an envelope** |
| Error-rate criterion | **provisional:** <1 % platform-caused failures (5xx, `platform_error`, `engine_error`, `lost_after_publication`) over a sweep, with rejections reported separately and not counted as failures | derived from R21's "platform-caused failures are free" — a platform that absorbs >1 % is paying for its own defects | **provisional (P-18)** |
| Latency criterion | **provisional:** no criterion. The only measured tail is p50-grade on one GPU with two clips. E1B must **measure** p95/p99 on the target before any latency number is written down | §1.6 | **explicitly absent** |
| Throughput criterion | **provisional:** report successful **video-seconds processed per second** together with the clip/frame/output profile; do not quote clips/s without the duration mix. One GPU-hour processed 15.9–36.2 video-hours on L40S (`meas.`, corrected 2026-09-20) | `results/notes.md` cost sketch | **provisional (P-18)** |
| Cost criterion | **provisional:** report cost per successful video-hour at the measured envelope. The committed sketch is $0.06 (360p) – $0.14 (1080p) per video-hour at an **operational** rate of ≈ $2.24/h for the `g6e.2xlarge` dev box. ⚠️ **That rate is an operational figure from `HANDOFF.md:24`, not a priced row: it is not in [`cloud-pricing.md`](../cross-cutting/cloud-pricing.md)**, which carries L40S rows for other vendors (OCI `BM.GPU.L40S.4` $3.50, and $1.09-$1.57 single-card rows) but **no AWS `g6e` row at all**. Repository convention is that prices come only from `cloud-pricing.md`, so publishing a cost figure requires a sourced row being added there first — owner: whoever publishes it; S2M does not edit that file | `results/notes.md` cost sketch; `HANDOFF.md:24` | **provisional (P-18)** |
| Resource criterion | **provisional:** flat host RSS and flat GPU memory over a soak; no growth in queue depth at steady arrival rate; preparation disk bounded | `18-marlin-backend-first.md` "Bounded under load" | **provisional (P-18)** |
| Soak duration | **⚠️ TO BE VERIFIED — no owner input.** A provisional engineering floor of 4 h continuous at the sustainable rate plus one induced restart is proposed so E1B/I3B have something to execute; the real duration is an availability decision | none | **provisional (P-18)** |
| Availability / recovery | **⚠️ TO BE VERIFIED — no owner input.** A single GPU is a single point of failure (`HANDOFF.md` §1). Measure and publish the recovery window; **do not state an availability target** and do not call single-GPU process recovery high availability | `18-marlin-backend-first.md`; P-16 | **absent by decision** |
| Quality/parity criterion | caption **events** (the `<start - end>` spans and their order) must match the `transformers` reference path on the same clips at the same profile; scene prose may differ in wording. This is *parity*, not accuracy | `results/notes.md` finding 1 (one clip, `meas.`) | **provisional (P-18)**, and the only quality gate available without P-07 |

Two hard rules for whoever consumes this table: **do not promote a provisional row to a
target by quoting it without its label**, and **do not tune against a row whose p95 was
never measured**.

### 5.3 P-07 — the SOP certification inputs that are missing

None of these exists in this repository. Each blocks accuracy claims and nothing else;
finite-video inference with disclosed capability limits can launch without them
([15-pending-inputs.md](../plan/15-pending-inputs.md) P-07).

| Input | What is needed | Why it blocks |
|---|---|---|
| Task rubric | versioned, integer `rubric_version` (1–1000, R43); what counts as a correctly identified step, a missed step, a hallucinated step, a mis-ordered step; how partial credit works | without it "SOP verification passed" has no definition |
| Output/event schema | the structured form a customer's system consumes (`{step_id, label, start_s, end_s, confidence?}`) and how it is derived from Marlin's free text (§1.4). The platform emits **text**; the parser is unspecified | a schema is also what makes evaluation automatable |
| Temporal matching tolerance | the ± seconds (or IoU threshold) at which a predicted span counts as matching ground truth, per step class. TimeLens-style mIoU is the vendor's axis; the customer's tolerance is unknown | a timestamp model with no tolerance has no score |
| Ground truth | per-episode step labels with spans, and who produced them; inter-annotator agreement | Marlin was trained with Gemini-generated dense captions and human review on some splits ([`MODEL_CARD.md`](../models/marlin2b/MODEL_CARD.md)); customer ground truth is not derivable from that |
| Dataset rights and access | licence, source-purpose grant, retention/deletion commitments, egress permission (interacts with P-09) | provider membership is not customer-content permission |
| Episode/source identity and split | stable `source_id`/`episode_id`, a grouped split so related segments and same-session duplicates never straddle train/holdout | correlated segments are not independent samples ([04-verification.md](../plan/04-verification.md)) |
| Segmentation policy | the customer's own overlap/boundary convention for long recordings | changes both cost and score |
| Thresholds and promotion rule | the score at which a candidate is acceptable, and on which slices | without it comparisons are descriptive only |
| Contamination control | ActivityNet, Charades and TimeLens appear in **both** the training-source and evaluation lists with no stated control ([`architecture.md` §9, §10.1](../models/marlin2b/architecture.md)) | a public-benchmark score cannot serve as the baseline |
| Baseline | **there is none.** Zero accuracy evaluations exist for any Marlin-2B variant, quantised or not; `model-index: null`; the card's quality claims carry no numeric table ([`models/marlin2b/README.md`](../models/marlin2b/README.md) open question 33) | there is nothing to regress against |

### 5.4 What the "robotics / VLA" context does **not** imply

The lead application is SOP verification over **recorded** robotics video. Stated once,
plainly, because a reader who knows the word "VLA" will otherwise assume otherwise:

- **No actuation.** The endpoint emits text. It produces no action arrays, no joint or
  end-effector commands, no control output, and nothing it returns is safe to drive
  hardware with. Action schemas, clocks, observation age, execution validity and
  emergency/fallback behaviour are a separate contract
  ([07-api-contracts.md](../platforms/07-api-contracts.md); P-14, ROBOT-CONTRACT /
  ROBOT-REPLAY in [04-verification.md](../plan/04-verification.md)).
- **No native live input.** Input is a **finite** clip, bounded at 120 s and one per
  request, fetched or uploaded and prepared before inference. Timestamped overlapping
  windows, freshness/staleness policy, dropped-frame policy and event dedup are the live
  transport's contract (P-13, VIDEO-CONTRACT / VIDEO-CAUSAL), not this one.
- **No stateful session.** Each request is independent; there is no model session, memory
  or cross-request state. Output streaming does not imply either of the other two
  streaming meanings ([07-api-contracts.md](../platforms/07-api-contracts.md) "Streaming
  distinctions").
- **No accuracy claim.** Not on a customer's procedures, not on a public benchmark, and
  not from `sop-synth-v1`. §5.3 lists what would be required.
- **No closed loop.** Nothing here reads back from a robot, and the model's output is not
  a verification verdict — it is a caption or a span that a customer's own rule evaluates.
- **No multi-camera, audio or image path.** One video part; audio is ignored by the
  profile; image input is refused even though the base class supports it (§2.3).

---

## 6. Dispositions

| Input | Disposition |
|---|---|
| **P-06** — exact Marlin artifact/capabilities and finite-video launch limits | **Resolved for the artifact, processor, preprocessing profile, request/response surface and limits** (§1, §2), with four named ⚠️ items that need a host with `HF_TOKEN` and a pinned image: the two weight-shard digests, the tokenizer digest, the runtime image digest, and the `shortest_edge` 4096 vs 65536 question. **Not** resolved as a served configuration: §4 D2/D3/D12 mean the pilot path cannot currently complete a video request. |
| **P-07** — SOP rubric, ground truth, dataset rights, thresholds | **Recorded, unresolved** (§5.3). Owner: provider/product with S2M/N/B. Blocks accuracy claims and dataset promotion decisions only. `sop-synth-v1` (§3.8) lets the software loop and the journey be tested meanwhile. |
| **P-18** — representative workload and performance/availability criteria | **Provisional criteria predeclared and labelled** (§5.2). Latency and availability targets are **deliberately absent**; soak duration carries a proposed engineering floor marked ⚠️. Owner input still required before any of it becomes a target. |

---

## Verification log

- 2026-09-22 (S2M): Authored from read-only inspection of `models/marlin2b/**`,
  `apps/infrx-api/infrx/**`, `research/models/marlin2b/**` and the plan documents at base
  `ec6c548`, plus three unauthenticated Hugging Face API/file probes made on this date
  (`GET /api/models/NemoStation/Marlin-2B` → commit `fd111fca…`, 15 files,
  `inferenceProviderMapping: null`, `chat_template_jinja` 7,755 B sha256 `273d8e0e…`;
  `GET /resolve/main/config.json` → 401, so the gated files remain sourced from the copies
  vendored in `research/models/marlin2b/` on 2026-09-19). **No GPU run, no cloud or
  instance operation, no paid call, and no code change.** The four committed L40S rows of
  2026-09-19 remain the only measurement of this model on any hardware; nothing here
  promotes them to an envelope or a target. Fourteen runtime-versus-contract discrepancies
  are recorded in §4 as inputs for M2/M3/G2/G1R/W3/F/E1B and are **not** fixed by this
  document.

- 2026-09-22 (S2M, independent review pass): Corrected four wrong claims and seven wrong
  citations found by review, and added six verified facts. **Claims corrected:** quantised
  checkpoints *do* exist (an author MLX-8bit plus community GPTQ-INT4, SDNQ-INT8 and GGUF —
  `architecture.md` §9); what is absent is any FP8/NVFP4/MXFP4/AWQ build and any accuracy
  evaluation of any variant, so they are out of profile for stated reasons (§1.2). The three
  artifact content digests are **published and need no token** — `.lfs.oid` on the public tree
  endpoint — not unobtainable as the first draft implied (§1.3, §2.6); this session could not
  transcribe the values because its environment redacts 64-hex strings, so the command and the
  owner are recorded instead. `sop-synth-v1`'s long clip is **120.0 s**, not 115 s: 115 s is
  230 frames and 22,540 tokens, and the 240-frame cap is first reached just above **119.25 s**,
  computed from `media/video.py:113-114` (the review's own "≥119.75 s" is off by half a second —
  the even-bump promotes an odd 239 to 240) (§3.8). The ≈$2.24/h L40S rate is an **operational figure from
  `HANDOFF.md:24`**, not a `cloud-pricing.md` row — that file has no AWS `g6e` row (§5.2).
  **Citations corrected:** `records.py:429`→`:426` (`profile_version`) and `:427`
  (`duration_s`); `tokens.py:47`→`:53-56`; `engine.py:566-567`→`:564-565`;
  `serve.sh:23-45`→`:23-42` (42 lines); `bench.py:501-506`→`:501-503`; D13's link text now
  says `research/models/marlin2b/README.md`, which is where it resolved. The `raw` guarantee is
  attributed to **R58** and the journal/relay, not to `_delta_payload`, which still emits a
  transitional `content` alias of `raw` (`engine.py:366`). **Added:** 6 of 24 layers hold KV and
  the omitted 18.63 MiB/sequence GDN state (§1.6); measured scaffold overhead is 101 tokens on
  Path A and 261 on Path B, not ~40 (§1.6); `max_position_embeddings` is 262,144 and 32,768 is
  a deployment choice (§2.5); the deployed unit runs `--max-num-seqs 32` against a pilot setting
  of 8 (§2.5, D13); §3 now marks which rows are specified-not-served. Review also found D8
  understated: the `@2026-09-01` revision is in 15 fixtures, 2 Python modules and 3 console
  files (20 in total), not the two originally named. No conclusion of §1-§6 is reversed by any
  of this, and still no measurement, GPU run, cloud operation or code change.
- 2026-09-22 (coordinator): corrected the digest method — the gated repository returns `*`×64 oids to unauthenticated tree calls (verified from the pilot box too; no local filter was involved); recorded the served-bytes sha256 of the three files from a read-only `sha256sum` on the pilot box and the chat-template match; registry equality stays ⚠️ until W3 runs the authenticated call. ⚠️ set now: registry oid equality, runtime image digest, engine decoder, measured hardware, `shortest_edge` 4096-vs-65,536.
- 2026-09-22 (coordinator, at the E1B merge): §3.8 amended — `drawtext` absent from the pinned ffmpeg build (colour + tally instead); resolution case is 0.4 s < one 0.5 s frame period; 11 clips at 3–7 steps plus the one 2-step clip.
- 2026-09-23 (G3, lane `codex/g3-jobs`): the explicit-async rows only — §2.1 (`/v1/jobs` row), §2.4 (explicit async row) and the §3 preamble now say *implemented by G3 on fakes, not mounted until the cutover* instead of *not implemented / pending G3*. The upload rows, D12 and every other statement are unchanged; no measurement, GPU run or cloud operation.
