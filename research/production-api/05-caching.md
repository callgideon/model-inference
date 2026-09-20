# Caching at every layer

**Research date: 2026-09-20.** Scope: every cache that sits between a paying
customer's HTTP request and a token coming back out of vLLM, for the system we
run *today* — one `g6e.2xlarge` (L40S 48 GB, 8 vCPU) in `us-east-1d`, Caddy →
[`apps/infrx-api/gateway.py`](../../apps/infrx-api/gateway.py) `:8001` → vLLM
`:8000`, serving `NemoStation/Marlin-2B` at `https://marlin2b.callbill.ai`,
with the console at `https://app.callbill.ai` (Vercel + Supabase).

Follows [`research/METHODOLOGY.md`](../METHODOLOGY.md): every non-trivial claim
carries an inline `[src](url)` or the marker **⚠️ TO BE VERIFIED** with the
reasoning stated. `est.` = derived arithmetic on cited inputs, `meas.` = measured
in this repo or published.

**Tooling note.** This session's `WebSearch` budget was exhausted (200/200) before
this document began, so the 15-query search sweep the task template asks for could
not be run. Every source below was instead fetched directly from its primary
document with `WebFetch` or `curl` — vLLM's own docs and `main`-branch source,
AWS's own documentation and price sheets, NVIDIA's own docs — which is the
stronger half of the requirement. Where a claim would normally have been found by
search and could not be confirmed from a primary doc, it is marked ⚠️ rather than
asserted.

---

## TL;DR — the decision rules

1. **Prefix caching (APC) is worth ~1–2 % on distinct clips and ~100 % of prefill
   on an exact repeat.** Leave it on, but stop expecting anything from it: the
   video hash is folded into the hash of *every* block from the first placeholder
   onward (§1.1), so two different clips share nothing past the chat-template
   preamble. This confirms [`matrix/cost-matrix.md` §7.2](../matrix/cost-matrix.md)'s
   "≈ 0 % for video" from the mechanism, not just from the measurement.
2. **The multimodal processor cache does not cache the thing that is slow.** It
   is keyed on media that has *already been fetched and decoded* (§1.4). Our
   bottleneck — HTTP fetch + container demux + frame decode of a 1080p source —
   happens before the key exists. vLLM cannot cache it; the gateway must.
3. **Transcode once, at ingest, to ≤480p, and cache the result by content hash.**
   Measured in-repo: 3.58 clips/s from a 1 MB 360p source vs 1.57 clips/s from a
   5.5 MB 1080p source at the same token budget — **2.28×**, and 56 % of the
   1080p per-clip cost is source-resolution work the model never sees
   ([`models/marlin2b/results/notes.md`](../../models/marlin2b/results/notes.md) §7).
   This is the single largest cache-shaped win available and it does not need a
   cache hit to pay: it pays on the *first* request.
4. **Pre-signed S3 PUT URLs replace base64 in the request body.** Base64 inflates
   a 5.5 MB clip to ~7.3 MB on the wire, makes the gateway buffer it in Python,
   and makes every retry re-upload. S3 presigned URLs are valid up to 7 days —
   **but only when signed with IAM *user* credentials**
   [src](https://docs.aws.amazon.com/AmazonS3/latest/userguide/PresignedUrlUploadObject.html).
   Our gateway signs with the **EC2 instance role**, and for those "the presigned
   URL expires when the role session expires, even if you specify a longer
   expiration time […] Valid for the duration of the role credentials (typically
   6 hours)"
   [src](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html).
   The §2.5 design's `expires_in: 3600` is inside that ceiling; 7 days is not
   available to us without a long-lived access key, which is worse. *(corrected
   2026-09-20.)*
5. **No CloudFront.** CloudFront is an egress CDN; our video flow is *ingress*
   from customer-hosted URLs. It would cache nothing we fetch (§2.6).
6. **Exact-match response caching is worth building only for profile B/C
   (§5), is legally straightforward if scoped to one organization, and must be
   scoped to one organization** — the whole industry does it that way: "Different
   organizations never share caches, even if they use identical prompts"
   [src](https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching),
   "Caches are not shared across organizations"
   [src](https://developers.openai.com/api/docs/guides/prompt-caching).
7. **Charge cached responses at the cached-input rate, not free.** The repo's own
   price survey ([`cross-cutting/serving-optimizations.md` §1.5](../cross-cutting/serving-optimizations.md))
   shows the industry band is 5×–47× off input, most commonly **10×**.
8. **Don't add Redis.** At our request rate the auth/price/response caches fit in
   a Python dict plus the 450 GB of NVMe we are already paying for. ElastiCache
   Serverless is $0.084 per GB-hour of stored data plus $0.0023 per million ECPUs
   [src](https://aws.amazon.com/elasticache/pricing/) — but the **1 GB floor is
   Redis OSS / Memcached only**: "Minimum metered data storage: 100 MB per cache
   for ElastiCache Serverless for Valkey and 1 GB per cache for ElastiCache for
   Memcached and ElastiCache for Redis OSS" (same source). On **Valkey** the floor
   is 100 MB ⇒ **≈ $6.13/month**, not $61 — a 10× smaller number than this
   document printed in three places. The conclusion survives (it is still a
   managed dependency and an ECPU bill to replace a `dict` and a directory) but
   the argument must be made on the real figure. *(corrected 2026-09-20.)*

---

## 1. vLLM-level caching

### 1.1 Automatic prefix caching: what it can actually hit on a video prompt

vLLM hashes each KV block as `hash(tuple[components])` where the components are
the parent block's hash, the exact token IDs in the block, and **extra hashes** —
"other values required to make this block unique, such as LoRA IDs, multi-modality
input hashes […], and cache salts"
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/design/prefix_caching.md).
Only full blocks are cached ("We only cache full blocks", same source), and the
default hash algorithm is SHA-256
[src](https://docs.vllm.ai/en/latest/configuration/engine_args.html).

The design doc's own worked example is the load-bearing detail. For a prompt
whose image placeholders start inside block 0, **every block from block 0 onward
carries the image hash**:

```text
Block 0   Parent: None          Tokens: 1, 3, 7493, …, <p>, …, <p>   Extra: <image hash>
Block 1   Parent: Block 0 hash  Tokens: <p>, …, <p>                  Extra: <image hash>
Block 2   Parent: Block 1 hash  Tokens: <p>, …, <p>                  Extra: <image hash>
Block 3   Parent: Block 2 hash  Tokens: <p>, …, <p>, 4               Extra: <image hash>
```
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/design/prefix_caching.md)

Two consequences for us:

- **Only the whole blocks that lie strictly before the first video placeholder
  can be shared between two different clips.** Everything after it is keyed on
  the clip.
- Because block hashes chain through the parent, **text that comes *after* the
  video is unreachable for cross-clip reuse** even though it is byte-identical
  in every request — its parent chain runs through video-keyed blocks.

Our current request shape puts the video first:

```json
"content": [
  {"type": "video_url", "video_url": {"url": "…"}},
  {"type": "text", "text": "Provide a spatial description of this clip followed by time-ranged events.…"}
]
```
([`models/marlin2b/README.md`](../../models/marlin2b/README.md), the published cURL)

So the shared prefix is *only* the chat-template preamble that precedes the
placeholders — the system block and `<|im_start|>user\n` — and the ~34-token
canonical instruction, which sits after the video, buys nothing.

### 1.2 The arithmetic, for Marlin-2B

Measured prompt for a 10.1 s 1080p clip at the training budget: **2,061 prompt
tokens** ([`results/notes.md`](../../models/marlin2b/results/notes.md) §3). The
video accounts for `frames/2 × 196` tokens with `frames = clamp(2 fps × duration,
4, 240)` ([`gateway.py`](../../apps/infrx-api/gateway.py) `budget_kwargs`), i.e.
`20/2 × 196 = 1,960` video tokens, leaving **101 tokens of template + instruction**.

*(Read against the source 2026-09-20: the code is
`frames = int(min(240, max(4, round(seconds * 2.0))))` followed by
`frames += frames % 2` — an **odd frame count is rounded up to the next even
number**, because the model pairs frames into 2-frame temporal patches. Every row
in the table below lands on an even count already, so no number moves; the parity
step matters to §2.2's transcode-parity argument, which reasons about which frames
are selected.)*

| clip length | frames | video tokens | ≈ prompt tokens | text share |
|---|---:|---:|---:|---:|
| 10 s | 20 | 1,960 | 2,061 (meas.) | 4.9 % |
| 30 s | 60 | 5,880 | 5,981 `est.` | 1.7 % |
| 60 s | 120 | 11,760 | 11,861 `est.` | 0.9 % |
| 120 s | 240 | 23,520 | 23,621 `est.` | 0.4 % |

(`est.` rows: 101 text tokens held constant, `frames × 98` video tokens; arithmetic
run in `python3`. The 120 s row matches `serve.sh`'s stated "~23.5K video tokens"
rationale for `--max-model-len 32768`.)

Of those 101 text tokens, only the ones before the first placeholder are
cross-clip cacheable, and only in whole blocks. The preamble length is **⚠️ TO BE
VERIFIED** — it depends on Marlin's `chat_template.json`, which is in a gated
repo — but it is bounded above by 101 − 34 ≈ 67 tokens, so with vLLM's example
block size of 16 that is **at most 4 blocks = 64 tokens, ≈ 3.1 % of a 10 s
prompt and ≈ 0.27 % of a 120 s prompt.** vLLM's `--block-size` doc does not state
the numeric default ("Accepts `None` (meaning 'use default')"
[src](https://docs.vllm.ai/en/latest/configuration/engine_args.html)); the design
doc's worked example assumes 16 and we assume the same ⚠️.

Feed that into the repo's own TTFT model
`TTFT(h) ≈ TTFT(0)·(1−h) + KV_load(h·T) + queueing`
([`cross-cutting/serving-optimizations.md` §1.2](../cross-cutting/serving-optimizations.md))
and the answer is a rounding error — and it is worse than that, because
[`results/notes.md`](../../models/marlin2b/results/notes.md) §4 measured that
**TTFT is the same at 2K and 12K prompt tokens** on this box, i.e. prefill is not
what TTFT is made of. Saving 3 % of a term that is already not the bottleneck is
nothing.

**Where APC *is* worth real money: the exact repeat.** When the same clip arrives
with the same instruction and the same `mm_processor_kwargs`, every block matches
— including the extra hash — and the entire prefill is served from cache. That is
the single case APC covers for us, and it overlaps exactly with the case a
response cache (§3) covers more cheaply. Keep APC on because it is free insurance
on that path; do not budget capacity against it.

**The cost of leaving it on.** The one published no-overlap measurement in the
repo is vLLM 0.6.3 on A100: throughput **−36.7 %**, TPOT **+25.0 %** on random
prompts with no shared prefix
[src](https://blog.squeezebits.com/vllm-vs-tensorrtllm-12-automatic-prefix-caching-38189),
already flagged in [`serving-optimizations.md` §1.2](../cross-cutting/serving-optimizations.md)
as 2024-era software with no 2026 re-measurement. On a CPU-starved 8-vCPU box
(§1.7) a SHA-256 over ~129 blocks per request is not obviously free. **This is a
one-command A/B on our own box** (`--enable-prefix-caching` vs
`--no-enable-prefix-caching`, `bench.py -c 8 -n 32` with *distinct* clips) and it
is on the day-1 list in §6.

**Do not reorder the prompt to chase this.** Putting the text part before the
video part would make the instruction cacheable and lift the ceiling from ~3 % to
~5 % of a 10 s prompt, and less on longer clips. It also changes the prompt the
model was fine-tuned on, which risks the caption-parity check that
[`models/marlin2b/README.md`](../../models/marlin2b/README.md) calls "the first
thing to check on a new engine version". Not worth it.

**Cache salt is the multi-tenant control if we ever want it.** vLLM supports a
per-request `cache_salt` injected into the first block's hash, so "only requests
with the same salt can reuse cached KV blocks […] This prevents timing-based
attacks where an adversary could infer cached content by observing latency
differences"
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/design/prefix_caching.md).
Since our only cross-request APC hit is the exact-repeat case, and a repeat of
*someone else's* clip requires already possessing that clip, the residual leak is
a timing oracle on "has anyone else recently captioned this exact clip". Setting
`cache_salt = org_id` in the gateway closes it for one line of code and costs
nothing measurable at our hit rates. Recommended (§6, item 5).

### 1.3 The multimodal processor cache (`mm_processor_cache_gb`)

> "Multi-modal processor caching is automatically enabled to avoid repeatedly
> processing the same multi-modal inputs in `BaseMultiModalProcessor`."
> [src](https://docs.vllm.ai/en/latest/configuration/optimization.html)

Configuration, verbatim from the engine-args reference
[src](https://docs.vllm.ai/en/latest/configuration/engine_args.html):

| flag | default | meaning |
|---|---|---|
| `--mm-processor-cache-gb` | **4** | GiB of processed multimodal items. "This cache is duplicated for each API process and engine core process, resulting in a total memory usage of `mm_processor_cache_gb * (api_server_count + data_parallel_size)`. […] Set to `0` to disable this cache completely (not recommended)." **Correction (2026-09-20):** the "served uncached" clause is **not** in `engine_args.html`; it is in `optimization.html`, and reads "A processed item larger than this budget is served uncached (with a warning) instead of failing **engine startup**; raise `mm_processor_cache_gb` if you want those items cached" [src](https://docs.vllm.ai/en/latest/configuration/optimization.html). Same meaning, different page, and "engine startup" is the part that was dropped. |
| `--mm-processor-cache-type` | `lru` | `lru` = mirrored LRU cache; `shm` = shared-memory FIFO cache (better when TP > 1 — not our case, TP1) |
| `--mm-hasher-algorithm` | `blake3` | `sha256`/`sha512` for FIPS |
| `--mm-shm-cache-max-object-size-mb` | **128** | per-object cap, `shm` mode only |

**How much of our traffic fits in 4 GiB?** The processed item for a video in the
Qwen-VL layout is `[num_patches, 3 × temporal_patch(2) × 14 × 14]` =
`[video_tokens × 4, 1176]` after the 2×2 spatial merge. At fp32 (`est.`, the
processor's output dtype is **⚠️ TO BE VERIFIED** — bf16 would halve every row):

| clip | video tokens | processed size `est.` | clips per 4 GiB `est.` |
|---|---:|---:|---:|
| 10 s | 1,960 | 35.2 MiB | 116 |
| 30 s | 5,880 | 105.5 MiB | 38 |
| 60 s | 11,760 | 211.0 MiB | 19 |
| 120 s | 23,520 | **422.1 MiB** | 9 |

Two findings fall out:

- At our 120 s limit (`MAX_VIDEO_SECONDS=120`), **one clip is ~10 % of the default
  cache**, so the cache holds a handful of long clips and is thrashed by them.
- A 120 s clip exceeds `--mm-shm-cache-max-object-size-mb`'s 128 MiB default by
  3.3×, so **if we ever switch to `shm` mode the longest clips are silently
  served uncached with a warning**. We are on `lru` (default, TP1) so this does
  not bite today, but it is a trap for the multi-GPU next models.

**Recommendation:** raise `--mm-processor-cache-gb` to **8** and revisit after
measuring `vllm:mm_cache_hits / vllm:mm_cache_queries` (§1.8).

⚠️ **Corrected 2026-09-20 — the original justification here was wrong.** This
document said "8 GiB is ~17 % of 48 GB of HBM but the KV pool has 98.9 % headroom
(§1.6), so it costs nothing real", and then contradicted itself one sentence later.
`mm_processor_cache_gb` is **host RAM, not HBM**, and it does not come out of the
KV pool at all, so §1.6's KV headroom is not the reason it is affordable. The
correct sizing is **8 GiB of the 64 GiB of host memory on a `g6e.2xlarge` =
12.5 %** — and, per the same docs, that budget is charged **per process**:
`mm_processor_cache_gb × (api_server_count + data_parallel_size)`
[src](https://docs.vllm.ai/en/latest/configuration/engine_args.html), with
`optimization.html`'s cache-placement table giving
`mm_processor_cache_gb * data_parallel_size` for `lru` processor caching and
`mm_processor_cache_gb * api_server_count` for the key-replicated and `shm`
variants [src](https://docs.vllm.ai/en/latest/configuration/optimization.html). At
our shape (one API server, DP1, TP1) that is a single 8 GiB copy and the change is
safe — but it is safe because we run one process, not because HBM is free.

### 1.4 What the processor cache does *not* cover — and why our own measurement proves it

This is the most important finding in §1, and it is not in the docs; it is in the
source.

The cache key is built in `ProcessorInputs.get_mm_hashes()`:

```python
hash_factors = {
    "media_io_kwargs": media_io_kwargs,
    "mm_processor_kwargs": mm_processor_kwargs,
}
…
hashes.append(
    hasher.hash_kwargs(
        hash_algorithm,
        model_id=model_id,
        **{modality: item},
        **hash_factors,
    )
)
```
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/multimodal/processing/inputs.py)

and `item` for a video comes from `data_items.get_all_items_for_hash()`, which
yields a `MediaWithBytes` whose `.media` is **already-decoded frames**. The hasher
picks whichever representation is smaller:

```python
if isinstance(obj, MediaWithBytes) and isinstance(obj.media, (np.ndarray, torch.Tensor)):
    frames = obj.media
    if frames.nbytes < len(obj.original_bytes):
        return cls.iter_item_to_bytes("video", frames)
    return cls.iter_item_to_bytes("video", obj.original_bytes)
```
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/multimodal/hasher.py)

**The decode has already happened by the time the key exists.** Therefore the
processor cache saves the HF processor transform — resize, normalise, patchify —
and nothing upstream of it. It does not save the HTTP fetch and it does not save
the container demux/frame decode. ⚠️ This is read off `main` on 2026-09-20 and
inferred from control flow rather than stated in a doc; re-check on the pinned
nightly before relying on it.

**Our own benchmark is the confirmation.** [`results/notes.md`](../../models/marlin2b/results/notes.md)
§7: same token budget (1,928 vs 2,061 prompt tokens), concurrency 8, *the same
clip repeated every request in both runs* — and yet 360p/1 MB gives 3.58 clips/s
against 1080p/5.5 MB's 1.57 clips/s. If the processor cache had absorbed decode,
the two rows would be equal. They are 2.28× apart. The 358 ms/clip difference
(`1000/1.57 − 1000/3.58`, `python3`) is **56 % of the 1080p per-clip cost** and it
is entirely fetch + demux + decode of pixels the model never sees.

Note #5 of `notes.md` worries that "the vLLM multimodal processor cache may have
absorbed decode cost; rerun with distinct clips before quoting preprocessing
cost". The mechanism above says the worry is *half* right: the cache absorbed the
processor transform but not the decode, and the 2.28× gap is the proof. The rerun
with distinct clips is still the right experiment (§6) — it will move the absolute
numbers down, not the ratio.

**Implication:** every remaining cache that matters for Marlin lives in the
gateway, not in vLLM. That is §2.

### 1.5 Client-supplied media UUIDs — the one vLLM lever we are not using

vLLM lets the caller name a media item instead of hashing it:

> "When using multi-modal inputs, vLLM normally hashes each media item by content
> to enable caching across requests. You can optionally pass `multi_modal_uuids`
> to provide your own stable IDs for each item so caching can reuse work across
> requests without rehashing the raw content."
> [src](https://docs.vllm.ai/en/latest/features/multimodal_inputs.html)

On the OpenAI-compatible server this is a `uuid` field on the content part, and
the media payload can be **omitted entirely**:

```json
{"type": "video_url", "video_url": {}, "uuid": "sha256:<clip-hash>|b=<budget-hash>"}
```
[src](https://docs.vllm.ai/en/latest/features/multimodal_inputs.html)

with the documented caveat: "the request will fail if the skipped media doesn't
have a corresponding UUID, or if the UUID fails to hit the cache" (same source).

Three things make this directly usable by our gateway:

1. **The gateway already computes a content hash.** It downloads every clip to a
   temp file for `ffprobe` ([`gateway.py`](../../apps/infrx-api/gateway.py)
   `video_seconds`). Hashing those bytes is one `hashlib` pass over data already
   in memory.
2. **Our `mm_processor_kwargs` are already part of the key**, so the uuid cannot
   collide across budgets: `get_mm_hashes` includes `hash_factors` in the digest,
   and the source comment is explicit — *"Even if a uuid_item is provided, model
   output depends on the current modality's hash factors, so they are taken into
   account."*
   [src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/multimodal/processing/inputs.py).
   Since `budget_kwargs` is a pure function of the clip's duration, and the
   duration is a function of the bytes, the same clip always maps to the same
   budget anyway — but the belt-and-braces is free.
3. **It removes the double transfer.** Today the gateway downloads the clip *and*
   passes the URL through, so vLLM fetches it a second time — the measured
   TTFT ~3.2 s of a 3.8 s end-to-end request is, in the README's own words,
   "dominated by downloading and decoding the 5.5 MB source twice, gateway and
   vLLM" ([`models/marlin2b/README.md`](../../models/marlin2b/README.md)).

**Design:** the gateway sends `uuid` on every request, and *optimistically omits
the payload* when its own media cache (§2) says this clip was processed recently
enough to still be in vLLM's `mm_processor_cache_gb`. On failure it retries once
with the bytes. ⚠️ Whether a uuid miss returns a clean 4xx that is safe to retry,
or something coarser, is **TO BE VERIFIED** against the pinned nightly — this is a
30-second test with `curl` against the local vLLM and is a gate on shipping the
optimistic path. The non-optimistic half (always send `uuid`, always send bytes)
is unconditionally safe and should ship first.

One more warning to respect: "If both multimodal processor caching and prefix
caching are disabled, user-provided `multi_modal_uuids` are ignored"
[src](https://docs.vllm.ai/en/latest/features/multimodal_inputs.html) — another
reason not to turn APC off even though §1.2 says it earns almost nothing.

### 1.6 KV-cache dtype — why fp8 buys us nothing

`--kv-cache-dtype` accepts `auto`, `fp8`, `fp8_e4m3`, `fp8_e5m2`, `nvfp4`, and a
long tail of quantized layouts
[src](https://docs.vllm.ai/en/latest/configuration/engine_args.html). For
Marlin-2B it is pointless, and the arithmetic says so plainly.

Marlin's KV is **12,288 B/token** across 6 GQA layers, plus an 18.63 MiB GDN state
per sequence ([`serving-optimizations.md` §5.1](../cross-cutting/serving-optimizations.md),
pinned by METHODOLOGY §8). With `--gpu-memory-utilization 0.90` on a 48 GB L40S
([`serve.sh`](../../models/marlin2b/serve.sh)):

```
KV pool  ≈ 48e9 × 0.90 − 5.444e9 (weights) − ~2e9 (activations, graphs) ≈ 35.8 GB   est.
         ≈ 2,909,831 tokens  ≈ 1,412 ten-second clips  ≈ 88 full 32,768-token sequences
at MAX_INFLIGHT = 16 × 2,061 tokens: 405 MB  =  1.13 % of the pool
```
(`python3`; the ~2 GB activation/graph allowance is `est.` and **⚠️ TO BE
VERIFIED** — vLLM logs the exact figure at boot and `--kv-cache-memory-bytes`
echoes a reproducible value.)

⚠️ **Two corrections to this block, 2026-09-20 — neither changes the verdict.**

1. **The GDN state is missing from the occupancy line.** Marlin is a hybrid: 6
   full-attention layers carry the 12,288 B/token KV, and the linear-attention
   layers carry a **fixed 18.63 MiB per sequence**, `S = 1` slot, independent of
   context (METHODOLOGY §8; [`01-requirements-and-traffic-model.md` §3.2](01-requirements-and-traffic-model.md)).
   At 16 in flight that is `16 × 19,537,920 B = 313 MB`, so the real in-flight
   footprint is **405 + 313 = 718 MB = 2.01 %** of the pool, not 1.13 %, and the
   headroom is **98.0 %**, not 98.9 %. Every "98.9 %" and "1.1 %" elsewhere in
   this document (§1.3, §5.4, §6) inherits this.
2. **`5.444e9` is the on-disk checkpoint, not the resident weight footprint.**
   METHODOLOGY §8 gives Marlin-2B as "2.21 B unique (2.72 B on disk, tied
   embedding duplicated) | 5.444 GB BF16" — the 5.444 GB counts the duplicated
   tied embedding, which is loaded once. The sibling
   [`01-requirements-and-traffic-model.md` §3.2](01-requirements-and-traffic-model.md)
   uses **4.426 GB BF16 resident** (= 2.21 B × 2 B) for exactly this calculation.
   On the resident figure the pool is **36.77 GB**, 2,992,681 tokens, and the
   16-request footprint is **1.95 %**. The two sibling documents disagree on this
   input and the disagreement is unresolved — 01's is the right one for a
   *memory* calculation. ⚠️ Close it by reading vLLM's boot log, which prints both
   the weight load and the KV pool size, alongside open question 10.

**We use about two percent of the KV cache.** Halving 2.0 % to 1.0 % changes
nothing, and fp8 KV costs accuracy risk and a re-run of the caption-parity check.
Leave `--kv-cache-dtype auto`. Revisit only if `max_num_seqs` ever goes above ~200
or clips get much longer than 120 s — neither is on the roadmap.

The same arithmetic kills a related temptation: `--kv-offloading-size` / LMCache
/ Mooncake (the tiered-KV machinery catalogued in
[`serving-optimizations.md` §1.4](../cross-cutting/serving-optimizations.md)). Those
earn their keep when KV is scarce and prefixes are shared. We have neither
problem. Marlin's "cache tier" is the media cache.

### 1.7 Compile / CUDA-graph cache — a cold-start lever, not a throughput one

vLLM boots in ~2–3 minutes including `torch.compile`
([`models/marlin2b/README.md`](../../models/marlin2b/README.md)). Three
documented mechanisms shorten that:

> "Reuse the compile cache. vLLM persists `torch.compile` artifacts under
> `VLLM_CACHE_ROOT` (default `~/.cache/vllm`), and the cache directory can be
> copied between machines or baked into a container image […] Set
> `VLLM_FORCE_AOT_LOAD=1` to fail loudly instead of silently recompiling when the
> cache misses (any change to the model, config, relevant `VLLM_*` environment
> variables, torch build, or GPU model invalidates it)."
> "Skip memory profiling with `--kv-cache-memory`. On startup, vLLM logs the exact
> `--kv-cache-memory` value that reproduces the current allocation."
> "Serve without CUDA graphs using `--enforce-eager`."
> [src](https://docs.vllm.ai/en/latest/configuration/optimization.html)

For an autoscaling fleet (the sibling autoscaling document, and
[`scaling/06-cold-start.md`](../scaling/06-cold-start.md)) this is the difference
between a scale-up that helps and one that arrives after the burst. **Bake
`VLLM_CACHE_ROOT` into the AMI or the container image, and pin
`--kv-cache-memory-bytes` from the value the first boot logs.** ⚠️ The two vLLM
pages spell this flag differently and this document has used both: the
optimization page says **`--kv-cache-memory`** throughout ("Skip memory profiling
with `--kv-cache-memory`. On startup, vLLM logs the exact `--kv-cache-memory`
value that reproduces the current allocation")
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/configuration/optimization.md),
while the engine-args reference lists **`--kv-cache-memory-bytes`** (accepting
human-readable sizes like `'1k'`, `'2M'`)
[src](https://docs.vllm.ai/en/latest/configuration/engine_args.html). Confirm which
one the pinned nightly accepts before it goes in `serve.sh` — `vllm serve --help`
answers it in one command. `serve.sh` mounts
only the weights today; it needs a second `-v` for the compile cache. Details and
the cold-start budget belong to the cold-start/autoscaling documents; the reason
it appears here is that it *is* a cache, and it is the only one whose miss costs
minutes rather than milliseconds.

One caveat from the same page: `--kv-cache-memory` "is only valid on the same GPU
with the same initial free memory; if a boot OOMs after hardware or co-tenant
changes, remove the flag to re-profile." Since g6e capacity forces us across AZs
and sometimes instance sizes, the installer must treat an OOM at boot as "drop
the flag and retry", not as a hard failure.

**CPU is the constraint that makes all of this worse.** vLLM's own sizing rule:
"for a deployment with N GPUs there are at minimum 2 + N processes […] The
minimum is 2 + N physical cores […] If your system has hyperthreading enabled,
then 1 vCPU = 1 hyperthread = 1/2 physical CPU core, so you need 2 × (2 + N)
minimum vCPUs" [src](https://docs.vllm.ai/en/latest/configuration/optimization.html).
For N = 1 that is **6 vCPU minimum for vLLM alone**, on a box with 8 — leaving 2
for Caddy, the gateway, `ffprobe`, and the gateway's own clip download. The same
page also warns that "by default, 8 CPU threads are used in each API server to
load media items […] consider adjusting `VLLM_MEDIA_LOADING_THREAD_COUNT` to
avoid CPU resource exhaustion." That is the mechanism behind `notes.md` §7 and it
is the reason §2 is worth building.

⚠️ **Corrected 2026-09-20 — that ellipsis hid the condition.** The page reads:
"By default, 8 CPU threads are used in each API server to load media items (e.g.
images) from request data. **If you apply API server scale-out**, consider
adjusting `VLLM_MEDIA_LOADING_THREAD_COUNT` to avoid CPU resource exhaustion"
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/configuration/optimization.md).
The "8 threads by default" half is confirmed; the tuning advice is **scoped to
API-server scale-out**, which we do not run (one API server). So §1.9's
`VLLM_MEDIA_LOADING_THREAD_COUNT=4` is a **reasonable local inference from the
8-vCPU floor, not vLLM guidance for our shape** — and it cuts the parallelism of
the very stage `notes.md` §7 shows is the bottleneck, so it could go the wrong
way. Treat it as an A/B (4 vs the default 8) on the §6 item-1 rerun, not as a
settled change.

### 1.8 Metrics that prove the vLLM caches are doing anything

All exposed on `/metrics` on the vLLM server
[src](https://docs.vllm.ai/en/latest/usage/metrics.html):

| metric | type | what it tells us |
|---|---|---|
| `vllm:prefix_cache_hits` / `vllm:prefix_cache_queries` | Counter (tokens) | the APC hit rate, in **tokens**. §1.2 predicts ≲ 3 % on distinct clips and ~100 % on exact repeats — a bimodal histogram, not a mean |
| `vllm:prompt_tokens_cached` | Counter | cached prompt tokens, local + external |
| `vllm:request_prefill_kv_computed_tokens` | Histogram | "new KV tokens computed during prefill (excluding cached tokens)" — the cleanest per-request view of what APC actually saved |
| `vllm:mm_cache_hits` / `vllm:mm_cache_queries` | Counter (**items**) | the multimodal processor cache hit rate. This is the number that decides `--mm-processor-cache-gb` |
| `vllm:num_requests_waiting` / `..._by_reason` | Gauge | reason label `capacity` vs `deferred` — needed to tell "cache miss made us slow" from "we are simply full" |
| `vllm:time_to_first_token_seconds` | Histogram | the SLO; §1.2 predicts cache work barely moves it |
| `vllm:request_queue_time_seconds` | Histogram | separates queueing from prefill in TTFT |
| `vllm:kv_cache_usage_perc` | Gauge | §1.6 predicts ~1 %; if it ever approaches 1.0 the whole §1.6 conclusion is void |
| `vllm:num_preemptions` | Counter | should be flat zero at our KV headroom; non-zero means §1.6 is wrong |
| `vllm:external_prefix_cache_hits` / `_queries` | Counter | cross-instance KV sharing via a KV connector — zero for us, will matter when there is more than one replica |

Nothing scrapes `/metrics` today. `install.sh` should add a scrape (or, minimally,
the gateway should proxy `/metrics` on an admin-only path) — without it, every
number in §1 stays a prediction.

### 1.9 Concrete changes to `models/marlin2b/serve.sh`

```bash
exec docker run --rm --name "marlin2b-$PORT" --gpus "\"device=$GPU\"" --ipc=host \
  -p "${BIND:-127.0.0.1}:$PORT:8000" \
  -v "$WEIGHTS:/model:ro" \
  -v "${VLLM_CACHE:-/opt/dlami/nvme/vllm-cache}:/root/.cache/vllm" \  # §1.7 compile cache
  -e VLLM_LOGGING_LEVEL="${VLLM_LOGGING_LEVEL:-INFO}" \
  -e VLLM_MEDIA_LOADING_THREAD_COUNT="${MEDIA_THREADS:-4}" \          # §1.7, 8 vCPU box
  "$IMAGE" /model \
  --served-model-name marlin2b \
  --hf-overrides '{"architectures":["Qwen3_5ForConditionalGeneration"]}' \
  --max-model-len "$MAX_MODEL_LEN" \
  --gpu-memory-utilization "${GPU_MEM:-0.90}" \
  --limit-mm-per-prompt '{"video":1,"image":4}' \
  --dtype bfloat16 \
  --mm-processor-cache-gb "${MM_CACHE_GB:-8}" \                       # §1.3, was default 4
  --enable-prefix-caching \                                            # §1.2, explicit not implicit
  --allowed-media-domains "${MEDIA_DOMAINS:-}" \                       # §2.8
  "$@"
```

⚠️ **`--allowed-media-domains ""` is not a documented way to block all outbound
fetch** (verified 2026-09-20). The flag takes a *list of domains* — "If set, only
media URLs that belong to this domain can be used for multi-modal inputs […] You
can provide a list of domains for this arg. For example: `--allowed-media-domains
upload.wikimedia.org github.com www.bogotobogo.com`"
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/multimodal_inputs.md).
What an **empty** value does — deny everything, or parse as unset and allow
everything (the fail-open case, which is the dangerous one) — is not stated
anywhere in the docs, and §2.8/§6 item 4 both depend on the deny reading. **Close
it the same way as the `uuid`-miss question: one `curl` against the local vLLM
with the flag set to an empty string, before shipping.** The safe form that needs
no testing is a single unroutable placeholder domain (e.g.
`--allowed-media-domains invalid.`), which is unambiguously a non-empty list that
matches nothing.

Everything here is additive and reversible. `--kv-cache-dtype` stays `auto`
(§1.6). `--media-io-kwargs '{"video":{"backend":"pynvvideocodec","hw_decoders":2}}'`
is the interesting next flag but belongs in §2.7, behind a measurement.

---

## 2. Media caching — the layer that actually matters

### 2.1 Where the time goes

Restating the measured decomposition, because every decision in this section
follows from it. Concurrency 8, same token budget, `bench.py` on the L40S
([`results/notes.md`](../../models/marlin2b/results/notes.md) §7):

| source | size | clips/s | ms/clip (serialized) | TTFT p50 |
|---|---:|---:|---:|---:|
| `Big_Buck_Bunny_360_10s_1MB.mp4` (360p) | 1 MB | 3.58 | 279 | 0.66 s |
| `sample-10s.mp4` (1080p) | 5.5 MB | 1.57 | 637 | 3.35 s |
| **difference** | 4.5 MB | | **358 (56 %)** | 2.69 s |

And from the public endpoint, measured 2026-09-20 from another AWS host: a 10 s
public-URL clip is ~3.8 s end to end with TTFT ~3.2 s, "dominated by downloading
and decoding the 5.5 MB source twice, gateway and vLLM"
([`models/marlin2b/README.md`](../../models/marlin2b/README.md)).

So the per-request budget at the public endpoint is roughly:

```
fetch (gateway)        ~1 s    ⚠️ est. from the 3.2 s TTFT minus the local 0.77 s
fetch (vLLM, again)    ~1 s    ⚠️ same
demux + decode + resize ~0.36 s  meas. (the 360p/1080p delta)
processor transform     — absorbed by mm_processor_cache on repeats (§1.4)
prefill (2,061 tok)     small — TTFT is flat from 2K to 12K tokens (notes.md §4)
decode (~200 out tok)   ~0.3 s  est. at TPOT 6–8 ms (notes.md table)
```

Three of those five lines are *media handling the gateway can cache or eliminate*.
None of them is the language model.

### 2.2 The transcode-once cache

**Key.** `sha256(clip bytes)` — content-addressed, so it dedups identical uploads
for free (§2.4), survives a URL changing, and never goes stale.

**Value.** The clip transcoded to the grid the model actually sees. Marlin never
looks at more than 448×448 per frame at 2 fps
([`models/marlin2b/README.md`](../../models/marlin2b/README.md) "Video budget"),
so anything above ~480p and 2 fps is decoded and thrown away. Transcode at ingest:

```bash
ffmpeg -nostdin -y -i in.mp4 \
  -vf "fps=2,scale='min(854,iw)':'min(480,ih)':force_original_aspect_ratio=decrease" \
  -an -sn -c:v libx264 -preset veryfast -crf 28 -pix_fmt yuv420p \
  -movflags +faststart out.mp4
```

`-an -sn` drops audio and subtitles (Marlin is video-only, one video per request
— [`gateway.py`](../../apps/infrx-api/gateway.py) rejects `n_video > 1`).
`fps=2` at ingest means vLLM's decoder walks 2 frames per second of source instead
of 25–60, which is where most of the 358 ms goes. `-movflags +faststart` puts the
moov atom first so a partial read can start decoding.

⚠️ **Verify before shipping**: that a 2 fps pre-decimated source still produces
identical captions through `budget_kwargs`. The gateway computes `frames =
clamp(2 fps × duration, 4, 240)` from the *duration*, which pre-decimation does
not change, so the frame count is stable — but the *frames chosen* may differ by
sub-sampling phase. The parity check is `reference.py` vs the transcoded clip on
`sample-10s.mp4`, diffing the event boundaries `<0.0-1.5> / <1.5-4.5> /
<4.5-10.1>` that `notes.md` §1 pins. If phase turns out to matter, drop `fps=2`
and keep only `scale` — that alone captures most of the win, since the 360p-vs-1080p
delta is resolution, not frame rate.

**Expected value.** Upper bound is the measured 2.28× (§1.4). Realistically less,
because the 360p row's source was also 5.5× smaller on the wire; a fair estimate
is "the transcoded clip behaves like the 360p row", i.e. **1.57 → ~3.5 clips/s**,
$0.000397 → $0.000174 per clip at $2.24208/h (`python3`; price below). `est.`,
and §6 makes measuring it item 1.

**This is not really a cache.** The transcode pays on the *first* request, because
the expensive step (decode at source resolution) is replaced by a cheaper one
(decode at 480p) whether or not the clip is ever seen again. The cache is what
stops us re-paying the transcode on the second request. Both halves are worth
having, but the transcode is worth having even at a 0 % hit rate — which matters
a lot for traffic profile A (§5).

### 2.3 Two tiers: local NVMe LRU, then S3

```
request ─▶ gateway
             │  h = sha256(bytes)
             ├─ /opt/dlami/nvme/mediacache/<h[:2]>/<h>.mp4      hit → 0 ms
             ├─ s3://infrx-media/clips/<h>.mp4                  hit → ~50 ms, same-region, free
             └─ miss → fetch original → transcode → write both
```

**Tier 1, local NVMe.** `g6e.2xlarge` ships **450 GB** of instance-store NVMe
([src](https://aws.amazon.com/ec2/instance-types/g6e/), confirmed against the
price sheet's `1 x 450 GB NVMe SSD`), already mounted at `/opt/dlami/nvme` and
already holding weights, samples and logs
([`models/marlin2b/README.md`](../../models/marlin2b/README.md)). It is free:
"There is no additional charge to use the instance store volumes provided for
your instance"
[src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/InstanceStorage.html).
At 2 MB per transcoded clip, 200 GB of cache is **~100,000 clips** — more than a
single box will see in a week at any plausible launch volume.

It is also *ephemeral*, and precisely so: data "persists even if the instance is
rebooted. However, the data does not persist if the instance is stopped,
hibernated, or terminated", and also does not persist on instance-type change,
scheduled retirement, or automatic recovery
[src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instance-store-lifetime.html).
For an autoscaling fleet every new instance starts with an empty tier 1. That is
the entire reason tier 2 exists.

**Eviction.** LRU by `atime` with a byte ceiling, run by a `systemd` timer, not by
the request path. Twenty lines of Python; do not import a cache library for this.
`ponytail:` a timer-driven sweep is O(files) each pass — fine at 10⁵ files,
replace with a size-ordered index if the cache ever exceeds ~10⁶ entries.

**Tier 2, S3.** Same region, reached through a **gateway VPC endpoint** — "There
is no additional charge for using gateway endpoints"
[src](https://docs.aws.amazon.com/vpc/latest/privatelink/gateway-endpoints.html).
Costs, us-east-1, [src](https://aws.amazon.com/s3/pricing/) (fetched 2026-09-20):
S3 Standard **$0.023 per GB-month** (first 50 TB), PUT/COPY/POST/LIST **$0.005
per 1,000**, GET/SELECT **$0.0004 per 1,000**.

At 2 MB per transcoded clip, with a lifecycle expiry
([src](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lifecycle-mgmt.html)):

| volume | TTL | steady-state | storage/mo | PUTs/mo |
|---|---|---:|---:|---:|
| 200k clips/day (profile A) | 7 d | 2.73 TB | $62.89 | $30.00 |
| 200k clips/day | 30 d | 11.7 TB | $269.53 | $30.00 |
| 20k clips/day (profile B) | 7 d | 273 GB | $6.29 | $3.00 |
| 20k clips/day | 30 d | 1.17 TB | $26.95 | $3.00 |
| 2k clips/day (profile C) | 7 d | 27 GB | $0.63 | $0.30 |

(`python3` on the prices above. **Unit basis, made explicit 2026-09-20:** these
rows are computed with **2 MiB** per clip and priced per **GiB** — e.g. the first
row is `200,000 × 7 × 2 MiB = 2,734.4 GiB × $0.023 = $62.89`. That is the right
basis, because S3 bills storage in binary GB, but the column header says "TB"
where it means **TiB**. On a decimal 2 MB / 10⁹-byte basis the same rows are
$64.40 / $276.00 / $6.44 / $27.60 / $0.64 — a ≤ 2.4 % spread that changes no
decision. Prices re-verified against the S3 pricing page 2026-09-20.)

Against $2.24208/hour = **$1,636/month** for one
always-on GPU, the media cache is noise at every volume. Set the lifecycle to
**7 days** and move on; 30 days buys a few more hits on profile B and still costs
less than 2 % of one GPU.

**S3 Express One Zone — resolved 2026-09-20** (this section previously said its
rates "could not be extracted from the pricing page in this session"; they can).
US East (N. Virginia): storage **$0.16 per GB-month**, PUT **$0.005 per 1,000**,
GET **$0.0004 per 1,000** [src](https://aws.amazon.com/s3/pricing/). Storage is
**7.0× S3 Standard**; the request prices are identical. At profile A's 7-day
working set that is $62.89 → **$437.50/month**, or 27 % of one GPU — no longer
noise. So Express One Zone is *not* a drop-in tier 1.5: it is worth it only for a
**hot sub-slice** (say the last 6 hours of clips, ~$16/month at profile A) in
front of Standard, and only once there is more than one instance to share it.
⚠️ Whether its single-AZ durability is acceptable for a cache is moot — a lost
object falls through to a re-fetch — but it is one AZ, and §2.3's whole point is
that tier 1 is already ephemeral.

### 2.4 Dedup of identical uploads

Content addressing gives dedup for free — the same bytes produce the same key, so
the second upload of an identical clip writes nothing. Two mechanisms make it
cheap and race-safe:

**S3 conditional writes.** `If-None-Match: *` on `PutObject`: "If there's no
existing object with the same key name in the bucket, the write operation
succeeds, resulting in a `200 OK` response. If there's an existing object, the
write operation fails, resulting in a `412 Precondition Failed` response. […] If
multiple conditional writes or copies occur for the same object name, the first
write operation to finish succeeds"
[src](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html).
So two concurrent requests for the same new clip both transcode (wasteful but
harmless) and exactly one PUT wins; the loser treats 412 as success. No lock, no
coordination service.

**In-flight dedup inside one gateway.** A `dict[str, asyncio.Future]` keyed on the
hash collapses concurrent requests for the *same* clip onto one fetch+transcode.
⚠️ **`MAX_INFLIGHT = 16` is a moving target** (noted 2026-09-20): the sibling
[`03-request-handling-and-queueing.md`](03-request-handling-and-queueing.md)
**removes it** — "`MAX_INFLIGHT` | **removed** | replaced by the three limits of
§3.2" — in favour of `WORKER_CONCURRENCY = N + B = 10`, and records that "the
current `MAX_INFLIGHT=16` was never measured and is not the same kind of limit
anyway" (it is a *rejection* threshold, not a dispatch gate). Everywhere this
document sizes something against 16 — §1.6's KV occupancy, this paragraph, §2.5's
"what lets `MAX_INFLIGHT` rise", §4.4's "16 concurrent identical requests" — read
it as "the in-flight ceiling, ~10–16", and re-cut against 03 at assembly time.
This is the single highest-value dedup at that ceiling: a customer
firing "caption + find + find + find" on one clip currently pays four downloads
and four decodes. One `dict`, ~10 lines. Do not reach for a library.

Note where dedup *cannot* help: `MAX_VIDEO_MB = 64` means the gateway must read
the whole clip before it knows the hash. The hash is not a pre-filter, it is a
post-fetch key. Pre-signed uploads (§2.5) fix that too, because then the customer
puts the bytes in S3 once and hands us a key.

### 2.5 Pre-signed S3 upload URLs — deleting base64 from the API

Today the gateway accepts a `data:` URL and base64-decodes it in-process
([`gateway.py`](../../apps/infrx-api/gateway.py) `video_seconds`). That is wrong
in four ways at once: base64 inflates the payload ~33 %, the whole clip sits in
Python memory, a retry re-uploads everything, and a 64 MB clip becomes an ~85 MB
request body that Caddy, uvicorn and the event loop all have to move.

**The fix is an ingest endpoint that hands out a pre-signed PUT.** "You may use
presigned URLs to allow someone to upload an object to your Amazon S3 bucket.
Using a presigned URL will allow an upload without requiring another party to
have AWS security credentials or permissions […] If you use the AWS CLI or AWS
SDKs, the expiration time for presigned URLs can be set as high as 7 days"
[src](https://docs.aws.amazon.com/AmazonS3/latest/userguide/PresignedUrlUploadObject.html).

⚠️ **The 7 days does not apply to us** (verified 2026-09-20). That ceiling is for
**IAM user** credentials — "Valid up to 7 days when you're using AWS Signature
Version 4". Our gateway signs with the box's **EC2 instance role**, and for
temporary credentials S3 is explicit: "Can't be valid for longer than the
credentials themselves […] **IAM role credentials used by Amazon EC2 instances** –
Valid for the duration of the role credentials (typically 6 hours)", and the FAQ
adds "For Amazon EC2 instance profiles, metadata credentials rotate periodically
with a maximum validity period of approximately 6 hours"
[src](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html).
The `expires_in: 3600` in the sketch above is comfortably inside that, so the
design is sound as drawn — but the docs and any client SDK must say **1 hour, and
re-issuable**, never "up to 7 days", or a customer that caches an upload URL for a
day gets an opaque `ExpiredToken`. This also means the ingest endpoint must be
cheap to call again; it already is.

```
POST /v1/uploads          {"bytes": 5500000, "sha256": "<hex>"}   ← customer knows both
  → 200 {"upload_url": "https://infrx-media.s3…?X-Amz-…", "media_id": "med_<hash>", "expires_in": 3600}
  → 200 {"media_id": "med_<hash>", "already_uploaded": true}       ← dedup hit, no upload at all

PUT <upload_url>          (raw bytes, straight from the customer to S3)

POST /v1/chat/completions {"messages":[{"role":"user","content":[
    {"type":"video_url","video_url":{"url":"infrx://med_<hash>"}}, {"type":"text",…}]}]}
```

What this buys, in order of size:

1. **The dedup check moves before the upload.** A customer re-captioning a clip
   they already sent transfers zero bytes.
2. **Retries are free.** A 429 or a 503 no longer re-uploads 5.5 MB — the whole
   point of a queueing gateway (the admission-control sibling document) is that
   retries are cheap.
3. **The gateway stops being a file server.** Its per-request memory stops scaling
   with clip size, which is what lets `MAX_INFLIGHT` rise.
4. **Ingest can be transcoded asynchronously**, off the request path, so the first
   inference request on an uploaded clip hits a warm cache.

Keep `video_url` with a public `http(s)` URL working — it is the OpenAI-compatible
shape and some customers genuinely have CDN-hosted clips. Keep `data:` URLs
working for small clips (say ≤ 8 MB) because they are what a `curl` one-liner in
the docs can do. But make the presigned path the documented default for anything
that is not a demo.

⚠️ `POST /v1/uploads` needs a per-org byte quota and a lifecycle expiry on the
upload prefix, or it is a free S3 bucket for strangers. That is admission control,
not caching, and belongs to the sibling document — noted here so it is not lost.

### 2.6 CDN (CloudFront)? No.

CloudFront caches responses *we serve outward*. Our video flow is inbound: the
customer hands us a URL to *their* origin and we fetch it. Putting CloudFront in
front of our API would cache `/v1/chat/completions` responses — which are POSTs
with unique bodies, uncacheable by construction — and would cache nothing about
the customer's clip.

The facts, for the record: the free tier is 1 M requests and 100 GB data transfer
per month, and "Data transfer between CloudFront and your AWS origins is
automatically waived when serving traffic through CloudFront"
[src](https://aws.amazon.com/cloudfront/pricing/). Neither is useful to us,
because neither leg is a leg we have.

The two cases where a CDN would genuinely help, and what to do instead:

- **A customer repeatedly hands us the same slow remote URL.** Our own §2.2 media
  cache already solves this, keyed on content rather than on URL, which is
  strictly better (it also catches the same clip arriving under two URLs).
- **Clients far from us-east-1 uploading large clips.** That is **S3 Transfer
  Acceleration**, not CloudFront — ingress, not egress. ⚠️ Its pricing was not
  verified in this session; revisit only if upload latency shows up in customer
  complaints, which it has not, since we have no customers yet.

So: **no CDN.** Revisit if and when we serve artefacts *out* — rendered clips,
thumbnails, exported results. Today we return JSON and no video content is stored
anywhere ([`apps/README.md`](../../apps/README.md) §4).

### 2.7 The other way to make decode cheap: NVDEC

vLLM supports four video decode backends: `opencv` (default, CPU),
`torchcodec` (CPU, FFmpeg-backed, tunable `num_ffmpeg_threads` and `seek_mode`),
`pynvvideocodec` (GPU/NVDEC) and `deepstream` (GPU)
[src](https://docs.vllm.ai/en/latest/features/multimodal_inputs.html).

vLLM measured the GPU path: on 8×H100, hardware decode gave "more than double the
throughput compared to the CPU-based video decoder", and CPU decode "can quickly
become a bottleneck, maxing out CPU cores even with just 2 or 4 GPUs"
[src](https://vllm.ai/blog/2026-09-18-pynvvideocodec) (2026-09-18). That
description is our box exactly (§1.7: 6 of 8 vCPU are vLLM's floor).

The L40S has the hardware: **3 NVDEC decoders** (Ada Lovelace, 5th-gen NVDEC),
with H.264, HEVC and AV1 decode at 8- **and** 10-bit, and **VP9 at 8-bit only**
[src](https://developer.nvidia.com/video-encode-and-decode-gpu-support-matrix-new)
(re-read 2026-09-20 — this document previously wrote "8/10-bit H.264, HEVC, VP9
and AV1", which overstates VP9; the matrix row marks VP9 10-bit `NO`. Irrelevant
for MP4/H.264 customer clips, but a 10-bit VP9 WebM would fall back to CPU decode
silently, which is exactly the trap §2.7 is trying to avoid).

```bash
--media-io-kwargs '{"video": {"backend": "pynvvideocodec", "hw_decoders": 2}}'
```
with the documented caveat that `hw_decoders` "cannot be overridden per request"
because "vLLM reserves GPU memory for these slots at startup"
[src](https://docs.vllm.ai/en/latest/features/multimodal_inputs.html).

**NVDEC and the transcode cache are complements, not alternatives**, and the order
matters:

- The transcode cache removes the *bytes* (fetch is smaller) and the *pixels*
  (decode is at 480p). It helps whether the decoder is CPU or GPU, and it helps
  the gateway's own `ffprobe` pass too.
- NVDEC removes the *CPU* from the decode path, freeing the cores that vLLM's own
  sizing rule says we are short of.

Do the transcode cache first (it is pure gateway code, no engine risk, and pays at
0 % hit rate); measure; then try NVDEC. ⚠️ Whether `pynvvideocodec` is present in
the pinned `vllm/vllm-openai:nightly` image and whether it handles our
`mm_processor_kwargs` budget identically is **TO BE VERIFIED** — a parity run
against `reference.py` is mandatory, same as any engine change
([`models/marlin2b/README.md`](../../models/marlin2b/README.md)).

The GPU-side transcode variant, if we ever want ingest to be GPU-accelerated too:
```
ffmpeg -y -vsync 0 -hwaccel cuda -hwaccel_output_format cuda -i input.mp4 \
  -vf scale_npp=1280:720 -c:a copy -c:v h264_nvenc -b:v 5M output.mp4
```
[src](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/ffmpeg-with-nvidia-gpu/index.html).
Not recommended on this box — ingest transcode would contend with inference for
the same GPU. It belongs on a separate CPU ingest worker, which is where an
autoscaled fleet should put it anyway.

### 2.8 SSRF: the security bill that comes with fetching customer URLs

The gateway fetches arbitrary customer-supplied URLs today
([`gateway.py`](../../apps/infrx-api/gateway.py) `video_seconds`, `follow_redirects=True`),
and so does vLLM. vLLM's docs are explicit about the risk:

> "When serving multi-modal models, consider setting `--allowed-media-domains` to
> restrict domain that vLLM can access to prevent it from accessing arbitrary
> endpoints that can potentially be vulnerable to Server-Side Request Forgery
> (SSRF) attacks. […] Also, consider setting `VLLM_MEDIA_URL_ALLOW_REDIRECTS=0`
> to prevent HTTP redirects from being followed to bypass domain restrictions.
> This restriction is especially important if you run vLLM in a containerized
> environment where the vLLM pods may have unrestricted access to internal
> networks."
> [src](https://docs.vllm.ai/en/latest/features/multimodal_inputs.html)

This belongs in a caching document because **the media cache is the fix that makes
the restriction affordable.** Once the gateway is the only fetcher (it passes
`uuid` + bytes or `uuid` alone, §1.5), vLLM never needs outbound network at all,
and we can set `--allowed-media-domains` to nothing and `VLLM_MEDIA_URL_ALLOW_REDIRECTS=0`
without breaking the product. The gateway then does its own fetch with the
protections that belong there: deny RFC1918 and link-local after DNS resolution
(the instance metadata endpoint `169.254.169.254` above all — this box has an
instance role that can read SSM SecureStrings), cap redirects, cap body size
(already: `MAX_VIDEO_MB`), cap time.

⚠️ The gateway's current `follow_redirects=True` with no address filtering is an
SSRF hole today. It is a caching-adjacent finding, not a caching finding, but it
is blocking for "give this to real users" and is called out in §Implications.

---

## 3. Response caching

### 3.1 The key

```
response_key = sha256(
    org_id          ,  # isolation — §3.3
    model_id        ,  # 'nemostation/marlin-2b'
    clip_hash       ,  # sha256 of the ORIGINAL bytes (not the transcode — see below)
    normalized_prompt_text ,
    canonical_params           # temperature, top_p, max_tokens, seed, stop, …
)
```

Three details that are easy to get wrong:

- **Key on the original clip bytes, not the transcode.** If we later change the
  transcode ladder, a key on the transcode silently invalidates everything (good)
  but a key on the original keeps serving answers produced under the old ladder
  (bad). Put a **cache epoch** in the key — a constant bumped whenever the
  transcode ladder, the budget function, the model weights or the vLLM version
  change. One integer, one deploy-time bump, and it makes every "did we invalidate
  that?" question answerable.
- **Normalize the prompt, don't trust it.** Strip trailing whitespace, but do
  *not* lowercase or re-order — the prompt is load-bearing and Marlin was tuned on
  exact templates.
- **Canonicalize the params.** `{"temperature": 0}` and `{"temperature": 0.0}` and
  an omitted `temperature` must land on one key, and any param the gateway does
  not recognise must force a miss (fail open to a real inference, never to a wrong
  cached answer).

**Only cache when `temperature == 0` and `n == 1` and no `seed` is set.** At
`temperature > 0` the customer asked for variety and a cache is a bug, not an
optimization.

### 3.2 Determinism: `temperature: 0` is not bitwise reproducible

This is the honest caveat that shapes the product copy.

vLLM ships a batch-invariance mode precisely because the default is not
deterministic: it "ensures that the output of a model is deterministic and
independent of the batch size or the order of requests in a batch", is enabled by
`VLLM_BATCH_INVARIANT=1`, and "may impact performance compared to the default
non-deterministic mode. This trade-off is intentional to guarantee
reproducibility"
[src](https://docs.vllm.ai/en/latest/features/batch_invariance.html) — no
quantified cost given ⚠️.

The commercial APIs say the same about their own caches. OpenAI: "Prompt caching
does not change how the model generates output tokens. The model generates a new
response using the cached prefix, so identical requests are not guaranteed to
produce identical outputs"
[src](https://developers.openai.com/api/docs/guides/prompt-caching).

So there are two coherent products and we must pick one:

| | **A. KV-only caching (what vLLM does)** | **B. Response caching (what §3 proposes)** |
|---|---|---|
| what is reused | prefill work | the finished text |
| output guarantee | none — "not guaranteed to produce identical outputs" | byte-identical by construction |
| honesty required | none | must be disclosed |
| capacity saved | prefill only (tiny for us, §1.2) | **everything** |

B is the one worth building for us, precisely because A saves the part of the
request that is not expensive. But B changes the semantics: a customer who calls
twice and gets the same bytes is getting a cached answer, and if they did not want
that, that is a surprise. Hence §3.4.

Do **not** turn on `VLLM_BATCH_INVARIANT=1` to make A deterministic. It costs
throughput for a guarantee that B gives for free.

### 3.3 Isolation: never across organizations

The industry position is unanimous and should be ours verbatim:

- Anthropic: "Organization and workspace isolation: Caches are isolated between
  organizations. Different organizations never share caches, even if they use
  identical prompts."
  [src](https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching)
- OpenAI: "Caches are not shared across organizations and cannot be reused across
  regional processing boundaries."
  [src](https://developers.openai.com/api/docs/guides/prompt-caching)

**`org_id` goes in the key.** The gateway already has it — `authenticate()`
returns the `api_keys` row with `org_id`
([`gateway.py`](../../apps/infrx-api/gateway.py)), and it is the scoping column in
every table in [`apps/README.md`](../../apps/README.md) §6.

Cross-org sharing would raise the hit rate on profile A (many customers captioning
the same public clips) and is *technically* safe for a deterministic function of
public inputs. It is not worth it: it turns "your video content" into "a shared
index of who has processed what", which is a privacy claim we would have to
defend, for a hit rate we have not measured. If a customer ever asks for it,
Anthropic's shape is the precedent — opt-in, explicit, scoped to a trust group
(the `cache_salt` mechanism of §1.2 is exactly this at the KV layer).

### 3.4 TTL, storage and product surface

| decision | value | why |
|---|---|---|
| TTL | **24 h** | long enough to absorb retries, client bugs and a user re-running a page; short enough that "results may change as the model improves" stays true |
| storage | one Postgres table + the response text, or NVMe + S3 by key | responses are ~200 output tokens ≈ **~1 KB**. A million cached responses is ~1 GB. This does not need infrastructure |
| index | `usage_events` already has `cached boolean not null default false` ([`apps/README.md`](../../apps/README.md) §6) | the schema anticipated this; the column is written `False` unconditionally today |
| invalidation | bump the **cache epoch** on model/engine/ladder change | §3.1; no per-key deletion path needed |
| opt-out | `"cache": {"mode": "bypass"}` in the request body, or an `X-Infrx-Cache: no-store` header | a customer who needs a fresh sample must be able to say so |
| disclosure | `X-Infrx-Cache: hit \| miss \| bypass` response header, plus a docs paragraph | the only honest way to ship B |

The `Inference-Id` header stays unique per request even on a hit, so a customer can
always correlate a specific call with a usage row — the cached answer is the same,
the request is not.

**Legal/product read.** Serving a customer their own earlier answer for the same
input is not a novel act; it is what every API's idempotency layer does, and the
repo's own [`scaling/03` §3.9](../scaling/03-concurrency-and-admission-control.md)
already contemplates idempotency keys. Three constraints make it defensible:
(1) never across orgs (§3.3); (2) never store the clip or prompt content, only
hashes — which preserves the standing promise that "No prompt or video content is
stored anywhere; usage rows are metadata only"
([`apps/README.md`](../../apps/README.md) §4); (3) disclose it in the docs and in a
response header. With those, a cached response is indistinguishable from a fresh
one *by construction*, which is the property the customer actually cares about.

⚠️ Storing the response *text* is a genuine change to the "metadata only" promise,
even scoped to one org. It needs an explicit line in the docs and an operator
decision before it ships. The hashes-only alternative — cache nothing, just dedup
in-flight (§2.4) — is strictly weaker but keeps the promise intact. **This is the
one item in this document that is a product decision, not an engineering one.**

### 3.5 What to charge for a hit

Free is wrong: a cache hit still costs auth, key lookup, storage and the bandwidth
of the answer, and free creates an incentive to hammer the endpoint. The industry
band, from the repo's own price survey (verified 2026-09-19,
[`cross-cutting/serving-optimizations.md` §1.5](../cross-cutting/serving-optimizations.md)):

| vendor | input | cached input | discount |
|---|---:|---:|---:|
| DeepSeek-V4-Flash | $0.14 | $0.0030 | 46.7× |
| Kimi K3 | $3.00 | $0.30 | 10× |
| Claude Opus 5 / Sonnet 5 | $5.00 / $2.00 | $0.50 / $0.20 | 10× |
| gpt-5.6-sol / gpt-5.4 | $4.00 / $2.50 | $0.40 / $0.25 | 10× |
| Alibaba implicit cache | 1× | 0.2× | 5× |

Note that DeepSeek, Moonshot, OpenAI and Alibaba's implicit caches "charge
**nothing to write** — the discount is pure upside", while Anthropic and Alibaba's
explicit cache charge a write premium (same source). Ours is implicit: the
customer never asks for a cache write, so **charging for the write would be
indefensible.**

**Proposal: charge the cached rate on both legs of a hit.** `models` already has a
`cache_usd_per_m` column and the console already renders it
([`apps/README.md`](../../apps/README.md) §6, F3), so this is a price row and a
`cost()` branch, not a schema change:

```
cost_usd = prompt_tokens × (cached ? cache_usd_per_m : input_usd_per_m) / 1e6
         + completion_tokens × (cached ? cache_usd_per_m : output_usd_per_m) / 1e6
```

Set `cache_usd_per_m = input_usd_per_m / 10` — the modal industry discount, and a
round number to explain. The gateway's `cost()` function
([`gateway.py`](../../apps/infrx-api/gateway.py)) currently takes `prices` with two
keys; it needs a third and a `cached` argument.

**And the customer must see it.** Usage tile F5 in [`apps/README.md`](../../apps/README.md)
already lists "cache hit" among the console's tiles — it is currently unfillable
because `usage_events.cached` is always `False`. Writing it truthfully makes that
tile real.

### 3.6 Where the response cache lives

Same shape as §2.3, and for the same reason: `dict` → NVMe → durable store.

```
in-process LRU (dict, ~10k entries, ~10 MB)    ← absorbs the retry storm
  ↓ miss
NVMe /opt/dlami/nvme/respcache/<h[:2]>/<h>.json ← survives a gateway restart
  ↓ miss
Supabase table responses_cache (key, body, epoch, created_at)  ← survives the instance
  ↓ miss
inference
```

Tier 3 only matters once there is more than one GPU instance. Until then it is
`ponytail:` deferrable — ship tiers 1 and 2, add tier 3 when the fleet is > 1.
And do not add Redis for any of it: ElastiCache Serverless is $0.084/GB-hour plus
$0.0023 per million ECPUs [src](https://aws.amazon.com/elasticache/pricing/), i.e.
**≈ $6.13/month at the Valkey 100 MB floor** (the 1 GB floor this document
printed applies to Redis OSS / Memcached only — corrected 2026-09-20), to replace
a `dict` and a directory. Six dollars is not the argument; the managed dependency,
the ECPU meter and the extra failure mode are.

---

## 4. Auth and config caching

### 4.1 The API-key cache (already correct)

[`gateway.py`](../../apps/infrx-api/gateway.py) caches `sha256(key) → api_keys row`
with `KEY_TTL = 60` s for hits and `MISS_TTL = 10` s for misses, patches
`last_used_at` at most once per minute per key, and on a Supabase failure serves
stale cached entries while returning **503** (not 401) for unknown keys. That
design already satisfies [`apps/README.md`](../../apps/README.md) F4 ("a revoked
key gets 401 from the gateway within 60 s") and §4's requirement that the gateway
"must keep serving if Supabase is unreachable for a key already in its cache; new
keys fail closed with 503". The 503-not-401 choice is the right one and the
comment in the code says why: *"never seen this key and Supabase is down: fail
closed, but retryable"* — a 401 would make a customer rotate a key that is fine.

Four gaps, in order of how much they will hurt:

1. **The caches are unbounded.** `_keys` and `_last_used` are plain dicts that grow
   with every distinct token ever presented. A key-guessing scanner turns that into
   an OOM, and the 10 s negative TTL means entries are re-created faster than they
   expire. **Bound them** — an LRU of, say, 10,000 entries. `functools.lru_cache`
   cannot be used directly (entries must expire), so it is a small dict + a deque,
   or `cachetools.TTLCache` if a dependency is acceptable. This is a security bug
   wearing a caching costume.
2. **No stale-while-revalidate.** At TTL expiry the request blocks on Supabase.
   With a 5 s connect/read timeout on `sb`, one slow Supabase call adds up to 5 s
   to a request whose whole budget is ~3.8 s. Serve the stale row immediately and
   refresh in the background when the entry is expired-but-recent (say within
   `KEY_TTL × 5`); fall back to blocking only when it is truly cold.
3. **No single-flight.** Ten concurrent requests on the same newly-expired key
   issue ten identical Supabase lookups. The same `dict[str, asyncio.Future]`
   pattern as §2.4 collapses them to one.
4. **Nothing counts hits, misses or staleness.** Add three counters (§4.5).

### 4.2 Model prices

`get_prices()` re-reads `models` every `PRICE_TTL = 300` s and — correctly — keeps
the last known prices on failure, writing `cost_usd = 0` with a journal warning
only if it has never succeeded. Combined with the documented invariant that "the
row is never dropped over a price"
([`apps/infrx-api/README.md`](../../apps/infrx-api/README.md)), this is the right
failure mode: lose money on a few rows rather than lose the rows.

One improvement worth the five lines: **persist the last-known prices to disk**
next to `usage.jsonl`. A gateway restart during a Supabase outage currently starts
with no prices at all and bills every request at zero until Supabase returns. A
JSON file written on each successful refresh and read at startup closes that
window completely.

§3.5 adds `cache_usd_per_m` to the `select`.

### 4.3 Supabase outage behaviour — the whole table

| dependency | cached? | TTL | behaviour when Supabase is down |
|---|---|---|---|
| `api_keys` (known key) | yes | 60 s | **keeps working** (stale served) ✓ |
| `api_keys` (unknown key) | negative | 10 s | **503 + `Retry-After: 5`** ✓ |
| `api_keys` (revoked, still cached) | yes | 60 s | keeps working up to 60 s ⚠️ accepted: F4's stated bound |
| `models` prices | yes | 300 s | last-known kept; `cost_usd = 0` if never fetched ⚠️ → fix in §4.2 |
| `last_used_at` PATCH | n/a | 60 s | fire-and-forget, failure logged ✓ |
| `usage_events` insert | queued | — | bounded queue 10k, retries 1/3/9 s, spills to `usage_failed.jsonl`, replayed by `deploy/replay_usage.py` ✓ |
| response cache (§3) | proposed | 24 h | **must degrade to inference, never to an error** |

The last row is the new invariant and it is the one to be strict about: a cache is
an optimization, and an optimization that can fail the request is a liability. Any
exception from the response-cache lookup path is caught, counted, and the request
proceeds to real inference.

The same goes for the media cache: an S3 timeout on the tier-2 read must fall
through to fetching the original, not 502.

### 4.4 Negative caching and the thundering herd

Three patterns, in the order they will bite:

- **Negative caching already exists** (`MISS_TTL = 10` s) and that is the right
  shape: short enough that a console-created key works within 10 s, long enough to
  blunt a scanner. Keep it; bound it (§4.1).
- **Thundering herd on expiry.** All three caches (keys, prices, responses) can
  stampede at TTL boundaries. Single-flight fixes keys and prices; the response
  cache needs it too, since 16 concurrent identical requests should produce one
  inference, not sixteen.
- **Jitter the TTLs.** `PRICE_TTL` of exactly 300 s across a future fleet of N
  gateways means N simultaneous Supabase reads every 5 minutes. `300 × (0.9 +
  0.2 × random())` costs one line and removes the synchronisation.

### 4.5 Metrics for the config caches

Counters on the gateway, exported however `/metrics` ends up being served
(§1.8 proposes the gateway proxies vLLM's; the same endpoint can carry its own):

```
gateway_auth_cache{result="hit|miss|stale|error"}
gateway_auth_cache_size
gateway_price_cache{result="fresh|stale|absent"}
gateway_media_cache{tier="mem|nvme|s3|miss"}
gateway_media_cache_bytes
gateway_response_cache{result="hit|miss|bypass|error"}
gateway_inflight_dedup_collapsed_total
gateway_transcode_seconds        (histogram)
gateway_fetch_seconds            (histogram)
```

`gateway_fetch_seconds` and `gateway_transcode_seconds` are the two that decide
whether §2 worked; everything else is hygiene.

---

## 5. Hit-rate scenarios: what caching does to capacity and cost

### 5.1 The three traffic profiles

⚠️ **These definitions are this document's own — and a sibling document does
define three, differently.** *(Corrected 2026-09-20.)* The original text here said
"no such definition exists in the repo as of 2026-09-20"; that was an artefact of
grepping for the phrase *"traffic profile"*. The sibling
[`01-requirements-and-traffic-model.md` §1.7](01-requirements-and-traffic-model.md)
calls them **traffic *scenarios*** and says they are **"Fixed by the program
brief"**:

| | **S1 pilot** | **S2 growth** | **S3 burst** |
|---|---|---|---|
| Peak rate | 1 req/s | 10 req/s | 50 req/s for 5 min |
| Daily volume | ~10 K req/day `est.` | **475,200 req/day** `est.` | 15,000 clips in 300 s |
| GPUs at ρ=0.8, 1080p | 1 | 8 | 40 |

Per that document's own precedence claim, and per this section's stated rule ("if
a sibling document defines them, that one wins"), **S1/S2/S3 win over A/B/C** and
this section is the one that should be re-cut. They are not the same axis: S1–S3
are cut by **arrival rate**, A–C by **repeat structure** (distinct-clip and
same-request percentages), which is the axis a caching document actually needs and
which 01 §1.7 does not provide. The two are close enough to map —
A(batch) ↔ S3(burst), B(interactive) ↔ S2(growth), C(steady) ↔ S1(pilot) — and
the volumes are compatible (S2's 475 K req/day sits between B's 10⁴–10⁵ clips/day
and A's, i.e. **B's volume band is too low by ~5×** against the brief). **Action
at assembly time: keep S1/S2/S3 as the names, and carry the distinct-clip /
repeat-request columns below into 01 §1.7 as added rows rather than maintaining a
second, conflicting taxonomy.** Open question 14 restated accordingly.

| | **A — batch backfill** | **B — interactive product** | **C — steady developer API** |
|---|---|---|---|
| shape | one customer pushes an archive | an app calls per user action | many small callers, mixed |
| volume | 10⁴–10⁶ clips in a burst | 10⁴–10⁵ clips/day, diurnal | 10²–10³ clips/day |
| arrival | saturating, queue-tolerant | spiky, latency-sensitive | Poisson-ish, low |
| distinct clips | ~100 % (each seen once) | ~20–40 % (caption, then 2–4 `find` calls on the same clip) | ~60 % (retries, demos, docs examples) |
| same (clip, prompt, params) | ~0 % | 5–15 % `est.` | 20–40 % `est.` (retries, tutorials, the docs cURL) |
| source resolution | whatever the archive is — often 1080p+ | app-controlled, often already small | anything |
| latency SLO | none (throughput) | TTFT matters | TTFT matters |

The distinct-clip and repeat-request percentages are `est.` — informed guesses
about product shapes, not measurements, and **the single most valuable thing
the first month of real traffic will tell us.**

### 5.2 The per-clip budget, and which cache attacks which line

For a 10 s 1080p clip through the public endpoint, `est.` from §2.1:

| line | ~cost | killed by |
|---|---:|---|
| fetch by gateway | ~1.0 s | §2.2 media cache (hit), §2.5 presigned upload |
| fetch by vLLM (second time) | ~1.0 s | §1.5 `uuid` + bytes-in-body, or `infrx://` resolution |
| demux + decode + resize | 0.36 s meas. | §2.2 transcode (first request too), §2.7 NVDEC |
| processor transform | absorbed on repeat | §1.3 `mm_processor_cache_gb` |
| prefill 2,061 tok | small | §1.2 APC — only on exact repeat |
| decode ~200 tok | ~0.3 s `est.` | **nothing but a response cache** (§3) |

The bottom line is the point of the whole document: **only a response cache
removes the decode**, and decode is the only line that is irreducibly the model.
Everything above it is media plumbing, and media plumbing is where the 2.28× is.

### 5.3 Scenario table

Baseline: 1.57 clips/s at concurrency 8 on 1080p sources = **5,652 clips/GPU-hour**,
$2.24208/h ⇒ **$0.000397/clip** (`python3`; price from the AWS us-east-1 on-demand
sheet pulled 2026-09-20, `g6e.2xlarge` = `2.2420800000`
[src](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json)).

All rows `est.`, built by applying the measured 2.28× transcode ratio and the
profile hit rates of §5.1. Effective throughput = `baseline × transcode_factor /
(1 − response_hit_rate)` for the served-request rate.

| profile | media cache hit | transcoded | response hit | effective clips/GPU-hour | $/clip | vs baseline |
|---|---:|---:|---:|---:|---:|---:|
| **A** batch, today | 0 % | no | 0 % | 5,652 | $0.000397 | 1.00× |
| **A** + transcode at ingest | 0 % | **yes** | 0 % | ~12,888 | $0.000174 | **2.28×** |
| **A** + transcode + dedup within batch | 5 % | yes | 0 % | ~13,566 | $0.000165 | 2.40× |
| **B** interactive, today | 0 % | no | 0 % | 5,652 | $0.000397 | 1.00× |
| **B** + transcode + media cache (60 % repeat clips) | 60 % | yes | 0 % | ~12,888 served, ~1.9× fewer transcodes | $0.000174 | 2.28× |
| **B** + all of the above + response cache at 10 % | 60 % | yes | 10 % | ~14,320 | $0.000157 | 2.53× |
| **C** steady, today | 0 % | no | 0 % | 5,652 | $0.000397 | 1.00× |
| **C** + everything, response hit 30 % | 40 % | yes | 30 % | ~18,411 | $0.000122 | 3.26× |

Read this table twice, because the second reading is the important one:

1. **The transcode column does all the work.** It is worth 2.28× in every profile
   and it does not depend on a hit rate. Every other cache is worth tens of
   percent *on top of* it and only in the profiles where repeats exist.
2. **The response cache is the only lever on profile C**, which is the profile a
   public launch actually starts in — low volume, high repeat, lots of people
   running the docs example. But profile C's absolute volume is so low that 3.26×
   on $0.000122/clip is a rounding error in dollars. **Its value on C is latency
   and headroom, not money**: a cached answer returns in ~10 ms instead of ~3.8 s,
   and it does not consume one of the 16 in-flight slots.
3. **At profile A's volume, 2.28× is one fewer GPU.** 200k clips/day at 5,652/h
   needs 35.4 GPU-hours/day (1.5 instances, i.e. 2); at 12,888/h it needs 15.5
   (0.65, i.e. 1). In a region where "g6e capacity is scarce — only us-east-1d had
   capacity" that is not a cost saving, it is a *feasibility* saving.

### 5.4 The cost of the caches themselves

| cache | recurring cost | basis |
|---|---:|---|
| vLLM APC | $0 (HBM already idle) | §1.6: KV pool **98.0 %** free (was 98.9 % before the GDN-state correction of 2026-09-20) |
| `mm_processor_cache_gb` 4 → 8 | $0 | ⚠️ **not** "same" — corrected 2026-09-20: this is **host RAM**, 8 of 64 GiB (12.5 %) on a `g6e.2xlarge`, charged per API-server/engine process. Free in dollars, not free in memory. §1.3 |
| NVMe tier-1 media cache | $0 | "no additional charge to use the instance store volumes" [src](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/InstanceStorage.html) |
| S3 tier-2 media cache, 7 d TTL | **$0.63 – $92.89/mo** | §2.3 table, profile C → A. *(Basis stated 2026-09-20: the $92.89 top end is §2.3's profile-A **storage $62.89 + PUTs $30.00**; §2.3's table prints those in separate columns, so the two numbers are consistent, not contradictory.)* |
| S3 gateway VPC endpoint | $0 | "no additional charge for using gateway endpoints" [src](https://docs.aws.amazon.com/vpc/latest/privatelink/gateway-endpoints.html) |
| response cache (NVMe + Postgres) | ~$0 | ~1 KB/response; 1 M responses ≈ 1 GB |
| **(rejected) ElastiCache Serverless** | **~$6.13/mo floor** (was printed as $61) | $0.084/GB-hour × 0.1 GB × 730; the Valkey minimum is **100 MB**, the 1 GB minimum is Redis OSS / Memcached [src](https://aws.amazon.com/elasticache/pricing/) — corrected 2026-09-20 |
| one `g6e.2xlarge`, for scale | **$1,636/mo** | $2.24208/h × 730 |

Every cache in this document costs under 6 % of one GPU at the worst profile, and
the transcode alone is worth ~56 % of a GPU. That ratio is the argument.

One hardware note the table implies: if the transcode has to happen on the
inference box, `g6e.4xlarge` (**16 vCPU, $3.00424/h**, +34 % over the 2xlarge) buys
2× the vCPU for the ffmpeg work and doubles the headroom against vLLM's
"6 vCPU minimum for N=1" floor (§1.7). At 2.28× throughput for +34 % price that is
a clear win — **if** the transcode is the thing that is CPU-bound. Better: put
ingest transcode on a separate cheap CPU instance or in the async ingest path
(§2.5) and keep the GPU box doing inference. That decision belongs to the
autoscaling sibling document; the input it needs is in this one.

---

## 6. What to implement first, and the metrics that prove it

Ordered by (measured value) ÷ (risk × effort). Items 1–4 are one sprint.

**1. Measure the baseline honestly — rerun `bench.py` with distinct clips.**
`notes.md` §5 already flags that every benchmark row used the same clip, so the mm
processor cache was hitting. Generate 32 distinct clips (trim a long source at
32 different offsets), rerun `-c 8 -n 32` at 1080p and at 480p. *Proves:* the true
`1.57` and the true transcode ratio. *Metric:* `clips/s`, plus
`vllm:mm_cache_hits / vllm:mm_cache_queries` ≈ 0 on the distinct-clip run (if it
is not ≈ 0, the clips are not distinct). Nothing below should be sized on the
current numbers.

**2. Scrape `/metrics`.** Add vLLM's `/metrics` to a scrape (or proxy it from the
gateway on an admin path) and add the gateway counters of §4.5. *Proves:* nothing
by itself; makes everything else provable. Half a day.

**3. Transcode at ingest + content-hash media cache (NVMe LRU, then S3).**
The 2.28× item. Gateway-only change: hash the bytes it already downloads,
`ffmpeg` to ≤480p @ 2 fps, write both tiers, serve from cache on the next hit,
single-flight concurrent requests for the same hash. *Proves:*
`gateway_media_cache{tier}` hit distribution, `gateway_transcode_seconds`,
`gateway_fetch_seconds`, and clips/s at `-c 8`. *Gate:* caption parity against
`reference.py` on `sample-10s.mp4` — same three event boundaries.

**4. Stop fetching the clip twice: send `uuid` + bytes to vLLM.**
The gateway already has the bytes and the hash; pass them inline with
`"uuid": "<hash>|<epoch>"` instead of passing the URL through. *Proves:* TTFT p50
at the public endpoint should drop from ~3.2 s toward the local 0.77 s. Then set
`--allowed-media-domains` to empty and `VLLM_MEDIA_URL_ALLOW_REDIRECTS=0` (§2.8) —
vLLM no longer needs outbound network. *Metric:*
`vllm:time_to_first_token_seconds` p50/p95.

**5. Bound the auth cache, add single-flight and stale-while-revalidate, set
`cache_salt = org_id`.** Four small changes in `authenticate()` plus one field in
the vLLM request body. *Proves:* `gateway_auth_cache_size` stays bounded under a
key-scanning load test; `gateway_auth_cache{result}` shows `stale` served instead
of blocked during an induced Supabase timeout.

**6. Pre-signed S3 upload endpoint (`POST /v1/uploads`) + `infrx://` media refs.**
Bigger, because it is an API surface and needs docs, quotas and lifecycle rules.
*Proves:* p95 request body size collapses; retry cost goes to ~0;
`already_uploaded: true` rate is the first *real* measurement of clip dedup, which
is the input every `est.` in §5 is waiting on.

**7. Response cache — but only after the operator signs off on §3.4's storage
question.** In-process LRU + NVMe, keyed per §3.1, `X-Infrx-Cache` header,
`usage_events.cached` written truthfully, `cache_usd_per_m` billing. *Proves:*
`gateway_response_cache{result}` hit rate per profile — the number that decides
whether it was worth building.

**8. Raise `--mm-processor-cache-gb` to 8; A/B `--enable-prefix-caching`;
try `pynvvideocodec`.** Engine-flag experiments, each behind a parity check, each
worth a measurement and none worth a guess. *Proves:* `vllm:mm_cache_hits` rate,
clips/s delta, `vllm:prefix_cache_hits` bimodality.

**Not doing, and why** — write these down so they do not get re-proposed:
CloudFront (§2.6, wrong direction), Redis/ElastiCache (§3.6, **$6.13/mo** at the
Valkey floor — corrected 2026-09-20 — plus a managed dependency, to replace a
`dict`), fp8 KV cache (§1.6, we use ~2 % of the pool), LMCache/Mooncake/KV
offload (§1.6, we have neither KV pressure nor shared prefixes), prompt reordering
for APC (§1.2, changes a fine-tuned prompt for ~2 %), `VLLM_BATCH_INVARIANT=1`
(§3.2, pays throughput for a guarantee the response cache gives free).

---

## Implications for our system

In the order the changes should land.

1. **[`models/marlin2b/serve.sh`](../../models/marlin2b/serve.sh)** — add
   `--mm-processor-cache-gb 8`, make `--enable-prefix-caching` explicit, mount a
   persistent `VLLM_CACHE_ROOT` for the compile cache, set
   `VLLM_MEDIA_LOADING_THREAD_COUNT=4` (8 vCPU box, vLLM's own sizing rule says we
   are already short), and — after item 4 below — `--allowed-media-domains` empty
   plus `VLLM_MEDIA_URL_ALLOW_REDIRECTS=0`. §1.9.
2. **[`apps/infrx-api/gateway.py`](../../apps/infrx-api/gateway.py) — the media
   path.** `video_seconds()` grows from "download, probe, delete" into "download,
   hash, probe, transcode, cache, return a local path". The temp file it already
   writes becomes the cache entry. Single-flight on the hash. This is the 2.28×.
   §2.2–2.4.
3. **`gateway.py` — stop the double fetch.** Send the clip to vLLM with a `uuid`
   instead of passing the customer's URL through. Removes ~1 s from TTFT and
   removes vLLM's need for outbound network. §1.5, §2.1.
4. **`gateway.py` — SSRF.** `follow_redirects=True` with no address filtering on
   customer-supplied URLs is a hole today. Resolve first, deny RFC1918 /
   link-local / `169.254.169.254` (this box's role reads SSM SecureStrings), cap
   redirects. Blocking for a public launch. §2.8.
5. **`gateway.py` — `authenticate()`.** Bound `_keys` and `_last_used` (unbounded
   dicts keyed by attacker-supplied tokens), add single-flight, add
   stale-while-revalidate, pass `cache_salt = org_id` to vLLM. §4.1, §1.2.
6. **`gateway.py` — `get_prices()`.** Persist last-known prices to disk so a
   restart during a Supabase outage does not bill everything at zero; add
   `cache_usd_per_m` to the `select`; jitter `PRICE_TTL`. §4.2, §4.4.
7. **`gateway.py` + `/metrics`.** Export the counters of §4.5 and expose or proxy
   vLLM's `/metrics`. Without this, nothing in this document can be confirmed or
   refuted. §1.8.
8. **New: `POST /v1/uploads` + `infrx://` media references**, with a per-org byte
   quota and an S3 lifecycle rule on the upload prefix. Documented as the default
   path for anything larger than a demo; `video_url` and small `data:` URLs keep
   working. §2.5.
9. **New AWS resources:** one S3 bucket (`infrx-media`) with two prefixes
   (`uploads/`, `clips/`) and 7-day expiry, one S3 gateway VPC endpoint (free),
   and an instance-role policy scoped to that bucket. §2.3, §2.5.
10. **[`apps/README.md`](../../apps/README.md) §6 and the console.**
    `usage_events.cached` starts being written truthfully (F5's "cache hit" tile
    becomes real); `models.cache_usd_per_m` gets a value and is used in the cost
    formula; `apps/README.md` §7's cost formula needs the cached branch. §3.5.
11. **[`apps/infrx-api/README.md`](../../apps/infrx-api/README.md) and the docs
    page (F7)** — document the `X-Infrx-Cache` header, the 24 h TTL, the opt-out,
    and the cached price. A response cache that is not disclosed is a bug. §3.4.
12. **An operator decision, not an engineering one:** §3.4's response cache stores
    response *text*, which qualifies the standing "no prompt or video content is
    stored anywhere" promise in [`apps/README.md`](../../apps/README.md) §4. Get
    that decided before item 7 of §6 is built; the hashes-only fallback (in-flight
    dedup only) is available if the answer is no.
13. **Capacity planning input for the sibling autoscaling document:** the transcode
    is worth 2.28×, which at profile-A volumes is the difference between needing
    two `g6e.2xlarge` and one — material in a region where only one AZ had g6e
    capacity. And ingest transcode should run off the GPU box, not on it.

---

## Open questions

Consolidated ⚠️ from every section, with the experiment that closes each.

1. **Every throughput row in this document rests on benchmarks that reused one
   clip.** [`results/notes.md`](../../models/marlin2b/results/notes.md) §5 flags
   it; §1.4 argues the mm cache absorbed the processor transform but not the
   decode, so the *ratio* survives and the *absolutes* do not. **Close it:**
   §6 item 1, `bench.py -c 8 -n 32` with 32 distinct clips at 1080p and 480p.
2. **Marlin's chat-template preamble length is unknown** (gated repo), so the APC
   ceiling of §1.2 is bounded (≤ 67 tokens) rather than known. **Close it:** dump
   the rendered prompt from `reference.py --dump-prompts` and count tokens before
   the first video placeholder.
3. **vLLM's default `--block-size` is not stated in the docs** ("Accepts `None`
   (meaning 'use default')"). §1.2 assumes 16, following the design doc's worked
   example. **Close it:** read the boot log.
4. **The processor-output dtype for video is unverified**, so §1.3's "clips per
   4 GiB" table is a 2× band (fp32 vs bf16). **Close it:** one log line or a
   `vllm:mm_cache_*` observation after setting `--mm-processor-cache-gb 1`.
5. **§1.4's "the mm cache is keyed on already-decoded media" is read off `main`,
   not documented.** It is the premise of the whole media-caching argument.
   **Close it:** confirm against the pinned nightly, or measure directly — send
   the same clip twice and time the second request's fetch+decode.
6. **Whether a `uuid` cache miss with an omitted payload returns a clean,
   retryable 4xx** decides whether §1.5's optimistic bytes-skipping path is safe.
   **Close it:** one `curl` against the local vLLM.
7. **Whether `fps=2` pre-decimation at ingest changes the frames Marlin sees**
   (sub-sampling phase) and therefore the captions. **Close it:** the parity check
   in §6 item 3; fall back to `scale`-only if it fails.
8. **Whether `pynvvideocodec` is present in the pinned `vllm/vllm-openai:nightly`
   image** and honours our `mm_processor_kwargs` identically. **Close it:** set
   the backend, run the parity check.
9. **The 36.7 % APC overhead on no-overlap traffic is a 2024 A100 measurement**
   with no 2026 re-measurement — inherited ⚠️ from
   [`serving-optimizations.md` §1.2](../cross-cutting/serving-optimizations.md).
   On a CPU-starved box it might be real. **Close it:** A/B the flag on our box.
10. **The ~2 GB activation/CUDA-graph allowance** in §1.6's KV arithmetic is a
    guess; the conclusion (we use ~1 % of KV) survives a 5× error, but the number
    should be the one vLLM logs. **Close it:** read `--kv-cache-memory-bytes` from
    the boot log.
11. **The batch-invariance throughput cost is unquantified** in vLLM's docs
    ("may impact performance", no number). Only matters if we ever choose the
    deterministic-inference product over the response-cache product. **Close it:**
    only if that choice comes up.
12. ~~**S3 Express One Zone pricing was not extractable**~~ — **CLOSED
    2026-09-20.** us-east-1: storage **$0.16/GB-month** (7.0× Standard), PUT
    **$0.005/1,000**, GET **$0.0004/1,000**
    [src](https://aws.amazon.com/s3/pricing/). Conclusion recorded in §2.3: it is
    not a drop-in tier 1.5 — at profile A's 7-day working set it is $437.50/month
    against Standard's $62.89 — only a hot sub-slice in front of Standard, once
    there is more than one instance.
13. **S3 Transfer Acceleration pricing likewise**, for the far-from-us-east-1
    upload case. Not urgent — no customers yet.
14. **The three traffic profiles in §5.1 conflict with a sibling that already
    defines three.** *(Restated 2026-09-20 — the original text claimed no sibling
    definition existed; one does.)*
    [`01-requirements-and-traffic-model.md` §1.7](01-requirements-and-traffic-model.md)
    fixes **S1 pilot / S2 growth / S3 burst** from the program brief, cut by
    arrival rate (1 / 10 / 50 req/s). §5.1's A/B/C are cut by repeat structure,
    which 01 does not cover, and B's stated volume band is ~5× below S2's 475 K
    req/day. **Close it:** keep S1/S2/S3 as the names, fold §5.1's distinct-clip
    and repeat-request columns into 01 §1.7 as added rows, and replace the `est.`
    percentages with the first month of real `usage_events`.
15. **Whether storing response text is acceptable** under the "metadata only"
    promise. Product decision, blocking for §6 item 7. **Close it:** ask.
16. **The ~1 s per fetch in §2.1's budget** is inferred by subtracting the
    measured local TTFT (0.77 s) from the measured public-endpoint TTFT (3.2 s)
    and splitting the remainder between two fetches. **Close it:**
    `gateway_fetch_seconds` (§4.5) measures it directly once it exists.
17. **Whether `--allowed-media-domains ""` denies everything or parses as unset
    and allows everything.** *(Opened 2026-09-20, §1.9.)* The docs describe only
    the non-empty-list case; the fail-open reading would silently void §2.8's
    whole SSRF argument. **Close it:** one `curl` against the local vLLM with the
    flag empty — or sidestep it with a non-empty placeholder domain that matches
    nothing.
18. **Whether the resident weight footprint is 4.426 GB or 5.444 GB.** *(Opened
    2026-09-20, §1.6.)* This document uses the on-disk checkpoint size; the
    sibling [`01` §3.2](01-requirements-and-traffic-model.md) uses the unique-param
    figure for the same calculation. **Close it:** vLLM's boot log prints the
    weight load and the KV pool size — the same log line that closes item 10.
19. **Whether `--kv-cache-memory` or `--kv-cache-memory-bytes` is the flag the
    pinned nightly accepts.** *(Opened 2026-09-20, §1.7.)* vLLM's optimization
    page and its engine-args reference disagree. **Close it:** `vllm serve --help`.
20. **Whether `VLLM_MEDIA_LOADING_THREAD_COUNT=4` helps or hurts.** *(Opened
    2026-09-20, §1.7.)* vLLM's advice to lower it is scoped to API-server
    scale-out, which we do not run; halving the media-loading parallelism on the
    stage `notes.md` §7 shows is the bottleneck could go either way. **Close it:**
    A/B 4 vs the default 8 on the §6 item-1 rerun.

---

## Sources

Fetched or `curl`-ed 2026-09-20 unless noted. WebSearch was unavailable this
session (budget exhausted); every source below was retrieved directly from its
primary document.

**vLLM — documentation**
- Automatic prefix caching (feature) — https://docs.vllm.ai/en/latest/features/automatic_prefix_caching.html
- Automatic prefix caching (design), incl. the multimodal hashing example, `cache_salt`, "we only cache full blocks" — https://raw.githubusercontent.com/vllm-project/vllm/main/docs/design/prefix_caching.md (rendered: https://docs.vllm.ai/en/latest/design/prefix_caching.html)
- Engine arguments — `--mm-processor-cache-gb`, `--mm-processor-cache-type`, `--mm-hasher-algorithm`, `--mm-shm-cache-max-object-size-mb`, `--kv-cache-dtype`, `--prefix-caching-hash-algo`, `--media-io-kwargs`, `--block-size`, `--gpu-memory-utilization`, `--max-num-queued-reqs`, `--enable-prefix-caching` — https://docs.vllm.ai/en/latest/configuration/engine_args.html
- Multimodal inputs — `uuid` / `multi_modal_uuids`, cached inputs, video decode backends, `VLLM_VIDEO_FETCH_TIMEOUT`, `--allowed-media-domains`, `VLLM_MEDIA_URL_ALLOW_REDIRECTS` — https://docs.vllm.ai/en/latest/features/multimodal_inputs.html
- Optimization and tuning — multimodal processor/IPC caching, cache placement table, compile cache / `VLLM_CACHE_ROOT` / `VLLM_FORCE_AOT_LOAD`, CPU-core sizing, `VLLM_MEDIA_LOADING_THREAD_COUNT` — https://docs.vllm.ai/en/latest/configuration/optimization.html
- Metrics — `vllm:prefix_cache_hits/queries`, `vllm:mm_cache_hits/queries`, `vllm:prompt_tokens_cached`, `vllm:request_prefill_kv_computed_tokens`, `vllm:num_requests_waiting_by_reason` — https://docs.vllm.ai/en/latest/usage/metrics.html
- Batch invariance — `VLLM_BATCH_INVARIANT=1` — https://docs.vllm.ai/en/latest/features/batch_invariance.html

**vLLM — source (`main`, read 2026-09-20)**
- `vllm/multimodal/processing/inputs.py` — `get_mm_hashes()`, `hash_factors`, uuid-plus-kwargs behaviour — https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/multimodal/processing/inputs.py
- `vllm/multimodal/hasher.py` — `MediaWithBytes` video branch (hash over decoded frames or original bytes), framing, hasher factory — https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/multimodal/hasher.py

**vLLM — blog**
- PyNvVideoCodec / NVDEC video decoding, 8×H100, ">2× throughput", `hw_decoders: 2` (2026-09-18) — https://vllm.ai/blog/2026-09-18-pynvvideocodec

**AWS**
- EC2 G6e instance types — g6e.2xlarge: 1×L40S 48 GB, 8 vCPU, 64 GiB, 450 GB NVMe, up to 20 Gbps — https://aws.amazon.com/ec2/instance-types/g6e/
- EC2 on-demand price sheet, us-east-1, Linux (gzipped JSON, pulled 2026-09-20): `g6e.2xlarge` $2.2420800000/h, `g6e.4xlarge` $3.0042400000/h — https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json
- EC2 instance store — "no additional charge", included in instance cost — https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/InstanceStorage.html
- EC2 instance store data persistence — persists on reboot, not on stop/hibernate/terminate/type-change/retirement/auto-recovery — https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instance-store-lifetime.html
- S3 pricing, US East (N. Virginia) — Standard $0.023/GB-mo (first 50 TB), PUT/COPY/POST/LIST $0.005/1,000, GET/SELECT $0.0004/1,000, DTO tiers 100 GB free / $0.09 / $0.085 / $0.08 — https://aws.amazon.com/s3/pricing/
- S3 presigned URLs for upload — up to 7-day expiry via CLI/SDK — https://docs.aws.amazon.com/AmazonS3/latest/userguide/PresignedUrlUploadObject.html
- S3 presigned URL expiration and signing credentials (added 2026-09-20) — IAM user "valid up to 7 days"; temporary credentials "can't be valid for longer than the credentials themselves"; EC2 instance-profile roles "typically 6 hours" — https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html
- S3 Express One Zone pricing, us-east-1 (added 2026-09-20, closes open question 12) — $0.16/GB-month storage, PUT $0.005/1,000, GET $0.0004/1,000 — https://aws.amazon.com/s3/pricing/
- S3 conditional writes — `If-None-Match: *`, 200 vs 412, first-writer-wins, 409 on concurrent delete — https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html
- S3 lifecycle management — https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lifecycle-mgmt.html
- S3 object integrity / checksums — https://docs.aws.amazon.com/AmazonS3/latest/userguide/checking-object-integrity.html
- VPC gateway endpoints — S3 and DynamoDB, "no additional charge" — https://docs.aws.amazon.com/vpc/latest/privatelink/gateway-endpoints.html
- CloudFront pricing — free tier 1 M requests / 100 GB, AWS-origin transfer waived — https://aws.amazon.com/cloudfront/pricing/
- ElastiCache pricing — Serverless $0.084/GB-hour, $0.0023 per million ECPUs (Valkey and Redis OSS), us-east-1; **minimum metered storage 100 MB per cache for Valkey, 1 GB for Memcached and Redis OSS** (re-read 2026-09-20) — https://aws.amazon.com/elasticache/pricing/
- EC2 on-demand price sheet re-pulled 2026-09-20 for the fact-check: `g6e.2xlarge` $2.2420800000/h, 8 vCPU, 64 GiB, `1 x 450 GB NVMe SSD`; `g6e.4xlarge` $3.0042400000/h, 16 vCPU, 128 GiB (+34.0 %) — all four figures confirmed from the same JSON

**NVIDIA**
- Video encode/decode GPU support matrix — L40S: 3 NVENC, 3 NVDEC, Ada (8th-gen NVENC / 5th-gen NVDEC), H.264 / HEVC / VP9 / AV1 8- and 10-bit decode — https://developer.nvidia.com/video-encode-and-decode-gpu-support-matrix-new
- FFmpeg with NVIDIA GPU — `-hwaccel cuda -hwaccel_output_format cuda`, `scale_npp` / `scale_cuda` — https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/ffmpeg-with-nvidia-gpu/index.html

**Commercial API cache semantics (precedent for §3)**
- Anthropic prompt caching — 5-min / 1-h TTL, write multipliers 1.25× / 2×, "Different organizations never share caches", per-model minimum cacheable lengths — https://platform.claude.com/docs/en/docs/build-with-claude/prompt-caching
- OpenAI prompt caching — "Caches are not shared across organizations", 1,024-token minimum for GPT-5.6+, "identical requests are not guaranteed to produce identical outputs" — https://developers.openai.com/api/docs/guides/prompt-caching

**Inherited measurements (cited via this repo, not re-fetched)**
- vLLM vs TRT-LLM APC study — +13.3 % throughput with a shared prefix, **−36.7 % / +25.0 % TPOT** with none (vLLM 0.6.3, A100) — https://blog.squeezebits.com/vllm-vs-tensorrtllm-12-automatic-prefix-caching-38189, via [`cross-cutting/serving-optimizations.md` §1.2](../cross-cutting/serving-optimizations.md)
- Cached-input price survey (DeepSeek, Moonshot, Anthropic, OpenAI, Alibaba), verified 2026-09-19 — [`cross-cutting/serving-optimizations.md` §1.5](../cross-cutting/serving-optimizations.md)

**This repository**
- [`apps/README.md`](../../apps/README.md) — console + gateway requirements, data model, `usage_events.cached`, `models.cache_usd_per_m`, the "metadata only" promise (§4)
- [`apps/infrx-api/README.md`](../../apps/infrx-api/README.md) — auth cache semantics, usage ingestion, the "never dropped over a price" invariant
- [`apps/infrx-api/gateway.py`](../../apps/infrx-api/gateway.py) — `authenticate()`, `get_prices()`, `video_seconds()`, `budget_kwargs()`, `cost()`
- [`models/marlin2b/README.md`](../../models/marlin2b/README.md) — box layout, public endpoint, video budget, the "downloading and decoding the 5.5 MB source twice" measurement (2026-09-20)
- [`models/marlin2b/results/notes.md`](../../models/marlin2b/results/notes.md) — the eight measured findings; §4 (TTFT flat 2K→12K), §5 (mm-cache caveat), §7 (the 2.28× 360p/1080p gap), cost sketch
- [`models/marlin2b/serve.sh`](../../models/marlin2b/serve.sh) — current vLLM flags
- [`cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md) — §1 prefix/KV caching, §1.5 cache pricing, §5.1 Marlin KV bytes/token, §6 multimodal, §7.4 Marlin verdict
- [`matrix/cost-matrix.md` §7.2](../matrix/cost-matrix.md) — prefix-cache hit rate scenarios; "≈ 0 % for video", the +32 % blended-cost correction
- [`scaling/03-concurrency-and-admission-control.md`](../scaling/03-concurrency-and-admission-control.md) — admission control, §3.9 idempotency keys (do not re-derive here)
- [`scaling/05-autoscaling-and-predictive-scaling.md`](../scaling/05-autoscaling-and-predictive-scaling.md) — scaling policy; the cold cache on every new instance is an input to it
- [`scaling/06-cold-start.md`](../scaling/06-cold-start.md) — compile/CUDA-graph caches in the cold-start budget
- [`scaling/08-reliability-and-operations.md`](../scaling/08-reliability-and-operations.md) — §8 runbooks, including "cache-hit collapse"
- [`scaling/10-blueprint.md`](../scaling/10-blueprint.md) — the bare-metal target this AWS work must stay portable to
- [`platform/05-model-and-inference-optimization.md`](../platform/05-model-and-inference-optimization.md) — the traffic-profile concept §5.1 instantiates

---

## Verification log (2026-09-20)

Adversarial fact-check of this document. 25 consequential claims selected across
AWS limits/prices, vLLM flag names/defaults/metric names, vLLM `main` source,
NVIDIA hardware, vendor cache semantics, in-repo code and measurements, and every
derivation. Each was checked against its **primary** source — the AWS price-sheet
JSON and AWS docs, vLLM's own `main`-branch docs and source files, NVIDIA's
support matrix, the vendors' own caching pages, and this repo's files — and every
derivation was re-run in `python3`. **WebSearch was exhausted in this session too**
(200/200 before the check began — the same constraint the header records for the
drafting session), so, as before, every source below was retrieved directly from
its primary document with `WebFetch` or `curl`. That is the stronger half of the
requirement and it sufficed: all 25 claims resolved against a primary document or
this repo's own files. What it cannot do is *find* a contradicting source nobody
linked — so the three UNVERIFIABLE rows stay unverifiable rather than being
argued away, and item 25's two undocumented behaviours are closed by a `curl`
against our own vLLM, not by more reading.

**Result: 25 checked — 13 CONFIRMED, 9 CORRECTED, 3 UNVERIFIABLE.**
No conclusion in this document is reversed. The transcode-first thesis (§2.2, the
2.28×), the "response cache is the only lever on decode" argument (§5.2), the
CloudFront rejection (§2.6) and the SSRF finding (§2.8) all survive unchanged.

### Confirmed

| # | Claim | Source opened |
|---|---|---|
| 1 | `g6e.2xlarge` = 8 vCPU, 64 GiB, `1 x 450 GB NVMe SSD`, **$2.2420800000/h**; `g6e.4xlarge` **$3.0042400000/h**, 16 vCPU (**+34.0 %**) | AWS on-demand price-sheet JSON, us-east-1 Linux, re-pulled today — all five fields from the same record |
| 2 | S3 Standard **$0.023/GB-mo** (first 50 TB), PUT/COPY/POST/LIST **$0.005/1,000**, GET/SELECT **$0.0004/1,000**, us-east-1 | aws.amazon.com/s3/pricing |
| 3 | S3 conditional write `If-None-Match: *` → `200 OK` if absent, `412 Precondition Failed` if present, "the first write operation to finish succeeds"; plus a `409 Conflict` path on a racing delete, retryable for `PutObject` | AWS S3 conditional-writes doc — verbatim, including the 409 the document already footnoted |
| 4 | Instance store: "There is no additional charge to use the instance store volumes provided for your instance"; persists on reboot, **not** on stop / hibernate / terminate / instance-type change / scheduled retirement / automatic recovery | AWS `InstanceStorage` + `instance-store-lifetime` — the persistence table matches §2.3 item by item |
| 5 | VPC gateway endpoints: "There is no additional charge for using gateway endpoints"; S3 and DynamoDB only | AWS PrivateLink gateway-endpoints doc |
| 6 | CloudFront free tier **1 M requests / 100 GB**; "Data transfer between CloudFront and your AWS origins is automatically waived when serving traffic through CloudFront" | aws.amazon.com/cloudfront/pricing |
| 7 | vLLM engine-arg defaults: `--mm-processor-cache-gb` **4**, `--mm-processor-cache-type` **`lru`**, `--mm-hasher-algorithm` **`blake3`**, `--mm-shm-cache-max-object-size-mb` **128**, `--prefix-caching-hash-algo` **`sha256`**; `--block-size` genuinely does not state a numeric default ("Accepts `None` (meaning 'use default')"); `--kv-cache-dtype` does accept `nvfp4` (18 values incl. `fp8_ds_mla`, `nvfp4_ds_mla`, `turboquant_*`) | vLLM engine-args reference. §1.2's ⚠️ on the block-size default is correctly placed |
| 8 | APC design doc: "extra hashes: … LoRA IDs, multi-modality input hashes …, and cache salts"; "We only cache full blocks"; the four-block worked example with `Extra: <image hash>` on **every** block from block 0; `cache_salt` "injected into the hash of the first block … prevents timing-based attacks where an adversary could infer cached content by observing latency differences" | vLLM `docs/design/prefix_caching.md` on `main` — all four quotes verbatim. §1.1's mechanism argument, the load-bearing claim of the whole document, is exactly right |
| 9 | `inputs.py`'s `hash_factors = {"media_io_kwargs": …, "mm_processor_kwargs": …}` and the comment "Even if a uuid_item is provided, model output depends on the current modality's hash factors, so they are taken into account" | vLLM `vllm/multimodal/processing/inputs.py` on `main` — verbatim. Additional detail worth having: `has_hash_factors` is computed **after** empty factors are filtered out, so a client uuid is used raw only when *both* kwarg dicts are empty. Our gateway always sends `budget_kwargs`, so §1.5's belt-and-braces claim holds by construction |
| 10 | `hasher.py`'s video branch — `MediaWithBytes` whose `.media` is `np.ndarray`/`torch.Tensor`, hashing whichever of decoded frames or `original_bytes` is smaller | vLLM `vllm/multimodal/hasher.py` on `main` — verbatim. §1.4's "the decode has already happened by the time the key exists" is a correct reading of the control flow, and remains the strongest finding in §1 |
| 11 | All 12 metric names in §1.8 exist on `main`: `vllm:prefix_cache_hits`/`_queries`, `vllm:prompt_tokens_cached`, `vllm:request_prefill_kv_computed_tokens`, `vllm:mm_cache_hits`/`_queries`, `vllm:num_requests_waiting_by_reason` (with the `capacity`/`deferred` reason labels §1.8 names), `vllm:time_to_first_token_seconds`, `vllm:request_queue_time_seconds`, `vllm:kv_cache_usage_perc`, `vllm:num_preemptions`, `vllm:external_prefix_cache_hits`/`_queries` | `vllm/v1/metrics/loggers.py` on `main` — every name matched, including the `WAITING_REASON_CAPACITY` / `WAITING_REASON_DEFERRED` constants |
| 12 | vLLM NVDEC blog: "At 8xH100, GPU-based video decoding provides more than double the throughput compared to the CPU-based video decoder" and "CPU-based video decoding can quickly become a bottleneck, maxing out CPU cores even with just 2 or 4 GPUs", published **2026-09-18**; `hw_decoders` defaults to **2**, "cannot be overridden per request" because "vLLM reserves GPU memory for these slots at startup"; four backends `opencv` (default) / `torchcodec` / `pynvvideocodec` / `deepstream` | vllm.ai blog + `docs/features/multimodal_inputs.md`. §2.7's framing is accurate |
| 13 | Org isolation, both vendors, verbatim — Anthropic: "Caches are isolated between organizations. Different organizations never share caches, even if they use identical prompts." OpenAI: "Caches are not shared across organizations and cannot be reused across regional processing boundaries" and "identical requests are not guaranteed to produce identical outputs." Anthropic's 5-min/1-h TTLs and 1.25×/2× write multipliers also confirmed | platform.claude.com + developers.openai.com prompt-caching pages. §3.3's "the industry position is unanimous" stands |

Also re-derived and confirmed in `python3`, with no change: the §1.2 token table
(20/60/120/240 frames → 1,960/5,880/11,760/23,520 video tokens, +101 text →
2,061/5,981/11,861/23,621, text share 4.9/1.7/0.9/0.4 %); the APC ceiling
(64/2,061 = 3.11 %, 64/23,621 = 0.271 %); the §1.3 processed-item table (35.2 /
105.5 / 211.0 / 422.1 MiB and 116 / 38 / 19 / 9 per 4 GiB; 10.3 % of the cache;
3.30× the 128 MiB `shm` cap); §1.4's 358 ms and 56 %, and the 2.28× ratio; the
whole §2.3 S3 cost table and its PUT column; the entire §5.3 scenario table
(5,652 → 12,888 → 13,566 → 14,320 → 18,411 clips/GPU-h and $0.000397 → $0.000122,
2.28×/2.40×/2.53×/3.26×); §5.3's 35.4 vs 15.5 GPU-hours/day; base64's 7.33 MB and
85.3 MB; and $2.24208 × 730 = **$1,636**/month. Marlin's **12,288 B/token** KV and
**18.63 MiB** GDN state match METHODOLOGY §8 and
[`serving-optimizations.md` §5.1](../cross-cutting/serving-optimizations.md); the
`gateway.py` constants (`MAX_INFLIGHT=16`, `MAX_VIDEO_SECONDS=120`,
`MAX_VIDEO_MB=64`, `KEY_TTL=60`, `MISS_TTL=10`, `PRICE_TTL=300`,
`follow_redirects=True` with no address filter) and every `notes.md` measurement
quoted (§4's flat 0.77 s TTFT across 2K↔12K, §5's same-clip caveat, §7's 3.58 vs
1.57 clips/s and 0.66 vs 3.35 s TTFT) are as cited.
[`cost-matrix.md` §7.2](../matrix/cost-matrix.md)'s "hit rate ≈ **0 %** for video"
and its "+32 %" blended correction are real and correctly cited.

### Corrected

| # | Was | Now | Why it matters |
|---|---|---|---|
| 14 | "ElastiCache Serverless … ≈ **$61/month** at the 1 GB floor" (TL;DR 8, §3.6, §5.4, §6) | **≈ $6.13/month** at the **Valkey 100 MB** floor; 1 GB is the Redis OSS / Memcached minimum | The rejection of Redis was argued on a number **10× too large**. The decision does not change, but "don't add Redis, it costs $61/mo" was not a true sentence and would not survive a reviewer checking it |
| 15 | "S3 presigned URLs are valid up to 7 days" (TL;DR 4, §2.5) | 7 days is the **IAM-user** ceiling; a URL signed with this box's **EC2 instance role** dies with the role session, "typically 6 hours" | Directly shapes the §2.5 API contract and its docs. `expires_in: 3600` was already inside the real limit, so the design is sound — but the advertised number was wrong by 168× and would have become a support ticket |
| 16 | "8 GiB is ~17 % of 48 GB of **HBM** but the KV pool has 98.9 % headroom … so it costs nothing real" (§1.3) | `mm_processor_cache_gb` is **host RAM**: 8 of 64 GiB = **12.5 %**, charged per API-server/engine-core process | Self-contradictory as written (the next sentence said "it is host memory"). The KV-headroom justification was simply the wrong argument for this flag |
| 17 | §1.6: "at MAX_INFLIGHT = 16 × 2,061 tokens: 405 MB = **1.13 %** of the pool", "98.9 % headroom" | **718 MB = 2.01 %**, headroom **98.0 %** — the 16 × 18.63 MiB = 313 MB of GDN per-sequence state was omitted | Marlin is a hybrid; leaving out the fixed state is exactly the class of error METHODOLOGY §2 and §3 exist to prevent. Verdict ("fp8 KV buys nothing") is unchanged and survives a 5× error |
| 18 | §1.6 used **5.444 GB** for resident weights | That is the **on-disk** checkpoint (2.72 B params with the tied embedding duplicated). The sibling [`01` §3.2](01-requirements-and-traffic-model.md) uses **4.426 GB resident** for the same calculation | A live 1.018 GB disagreement between two documents in this same directory. Flagged in place and opened as question 18 rather than silently picked |
| 19 | §1.7 quoted "by default, 8 CPU threads … **[…]** consider adjusting `VLLM_MEDIA_LOADING_THREAD_COUNT`" | The elided text is "**If you apply API server scale-out**" — the tuning advice is scoped to scale-out, which we do not run | The ellipsis turned a conditional into vLLM endorsing `=4` on our box. It is a defensible local inference from the 8-vCPU floor, but it throttles the stage `notes.md` §7 identifies as the bottleneck, so it is now an A/B (question 20), not a recommendation |
| 20 | Open question 12: "S3 Express One Zone pricing was not extractable" | **Extractable and extracted:** $0.16/GB-mo, PUT $0.005/1,000, GET $0.0004/1,000, us-east-1 | Closes a question, and the answer flips the advice: 7.0× Standard storage means $437.50/mo at profile A — not a drop-in tier 1.5, only a hot sub-slice |
| 21 | §5.1: "no such definition [of three traffic profiles] exists in the repo as of 2026-09-20" | [`01-requirements-and-traffic-model.md` §1.7](01-requirements-and-traffic-model.md) defines **S1 pilot / S2 growth / S3 burst**, "Fixed by the program brief" | The original grep searched for "traffic profile"; the sibling says "traffic scenarios". By this document's own stated precedence rule, 01 wins and §5.1 is the section to re-cut |
| 22 | §1.3 attributed the "served uncached (with a warning)" sentence to `engine_args.html`; §2.3's cost table labelled GiB figures "TB"; §2.7 gave the L40S "8/10-bit H.264, HEVC, VP9 and AV1"; §1.7 and §1.6/OQ10 used `--kv-cache-memory` and `--kv-cache-memory-bytes` interchangeably | Quote is in `optimization.html` and ends "instead of failing **engine startup**"; the S3 rows are **GiB at 2 MiB/clip** (decimal basis differs ≤ 2.4 %, no decision moves); **VP9 is 8-bit only** on the L40S; the two vLLM pages genuinely spell the flag differently (now question 19) | Four small sourcing/label defects. None changes a number that matters, but each is the kind a reader would trip over while trying to reproduce the work |

### Unverifiable

| # | Claim | Status |
|---|---|---|
| 23 | Marlin's chat-template preamble length; the processor-output dtype for video; whether vLLM's default `--block-size` is 16 | **Correctly already marked ⚠️.** The gated repo blocks the first two; the third is genuinely absent from the docs (re-confirmed above). The bounds this document reasons within (≤ 67 tokens, a 2× fp32/bf16 band, the design doc's worked example) are the right way to handle all three |
| 24 | The **−36.7 % throughput / +25.0 % TPOT** APC overhead on no-overlap traffic | **Inherited, not re-fetched**, as the document states. vLLM **0.6.3 on A100** is ~2 years stale against a 2026 nightly on an L40S and should not be quoted as a current cost. The A/B on our own box (§6 item 8, question 9) is the only thing that settles it — left ⚠️ |
| 25 | Whether `--allowed-media-domains ""` denies all outbound fetch, and whether a `uuid` cache miss with an omitted payload returns a clean retryable 4xx | **Both undocumented; both newly/already gating a ship decision.** The uuid question was already question 6; the empty-domain-list question is **new** (question 17) and matters more, because §2.8's SSRF remediation and §6 item 4 both assume the deny reading, and the fail-open reading would silently void them |

### Structural note

Nothing in §1–§6 was removed. Corrections are inline and dated, with the
superseded text preserved in each "was / now" row above so the diff is auditable.
The four items that should travel to assembly: the ElastiCache figure (it appears
four times), the S1/S2/S3-vs-A/B/C taxonomy conflict, the 4.426-vs-5.444 GB weight
disagreement with `01`, and `MAX_INFLIGHT`'s removal by `03`.
