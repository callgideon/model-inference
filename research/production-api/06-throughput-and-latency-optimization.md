# Throughput and latency optimization for the video-VLM workload

> **Implementation amendment — 2026-09-20.** The current implementation authority is [the unified plan](../plan/README.md), especially [contracts](../plan/01-contracts.md) and [durable protocols](../plan/02-durable-protocols.md). The text below is historical research where it conflicts with those documents. Pilot is free with promotional holds/settlement; ordinary chat never automatically returns 202; PG owns jobs, admission, leases, output journal and terminal accounting. Memory/Valkey queues are rebuildable indices. Admission stages immutable input and commits job/hold/outbox before acknowledgment. Output commits before relay, terminal success after settlement; no retry after publication. Existing A0 fixes are preserved, not repeated. Per-request context is not aggregate concurrency; pixel area 200704 is an area limit, not a 448px long edge. Old Lua/layout/schema snippets require contract tests and must not be copied verbatim. New launch, ownership and test gates are in the plan package.


**Research date: 2026-09-20.** Follows [`research/METHODOLOGY.md`](../METHODOLOGY.md): every
non-trivial claim carries an inline `[src]` link or the marker **⚠️ TO BE VERIFIED** with the
estimation method stated. `meas.` = measured in this repo or published with a source; `est.` =
derived from a formula on stated inputs; `calc.` = arithmetic run in `python3` during this pass on
numbers that are themselves cited.

**Scope.** This document is about making *one request* faster and *one GPU* do more of them, for
the Marlin-2B video-VLM workload as it exists today: one L40S on an AWS `g6e.2xlarge`, vLLM behind
`apps/infrx-api/gateway.py` behind Caddy. Queueing, admission, autoscaling and cold start are the
subject of the sibling documents in this directory and of
[`research/scaling/03`](../scaling/03-concurrency-and-admission-control.md),
[`05`](../scaling/05-autoscaling-and-predictive-scaling.md),
[`06`](../scaling/06-cold-start.md) and [`10`](../scaling/10-blueprint.md) — **linked, not
repeated**. The generic serving-optimization catalogue (prefix caching, speculation, parallelism,
disaggregation) is [`research/cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md);
this document only touches those where the *video* workload changes the answer.

**The one-sentence result.** On the measured box the GPU runs at **5.8 % of its dense BF16 rate**
during steady-state video captioning (§1.3, `calc.`) while its four physical CPU cores are the
binding constraint, so every recommendation below is ordered by how much CPU work it removes from
the request path — not by how much GPU work it makes cheaper.

**Files this document proposes changing**, in the order they appear:
`apps/infrx-api/gateway.py`, `models/marlin2b/serve.sh`,
`apps/infrx-api/deploy/marlin2b-vllm.service`, `apps/infrx-api/deploy/marlin2b-gateway.service`,
`apps/infrx-api/deploy/Caddyfile`, `models/marlin2b/bench.py`, and the instance type in
`models/marlin2b/README.md` §"Dev box".

---

## 1. The measured profile, and where the time goes

### 1.1 What was actually measured

Four rows, one GPU, 2026-09-19, from
[`models/marlin2b/results/notes.md`](../../models/marlin2b/results/notes.md) and
[`models/marlin2b/README.md`](../../models/marlin2b/README.md) §Results. AWS `g6e.2xlarge`
(1 × L40S 48 GB, 8 vCPU), `vllm/vllm-openai:nightly` pulled 2026-09-19, `--max-model-len 32768`,
`--max-num-seqs 32`, clips sent as base64 `data:` URLs by `bench.py`.

| # | clip (source) | c | prompt tok | TTFT p50 | TPOT p50 | clips/s | out tok/s |
|---|---|---:|---:|---:|---:|---:|---:|
| A | `sample-10s.mp4` 1080p, 5.5 MB | 1 | 2,061 | 0.77 s | 6 ms | 0.50 | 100 |
| B | `sample-10s.mp4` 1080p, 5.5 MB | 8 | 2,061 | 3.35 s | 8 ms | 1.57 | 310 |
| C | same clip, processor-default budget | 8 | 12,221 | 3.70 s | 8 ms | 1.47 | 290 |
| D | `Big_Buck_Bunny_360_10s_1MB.mp4` 360p, 1 MB | 8 | 1,928 | 0.66 s | 7 ms | 3.58 | 760 |

Three facts fall straight out of the table and are the whole reason this document exists:

1. **B vs C — 5.9× the prompt tokens costs 6 % of the throughput.** 12,221 tokens instead of 2,061
   moves 1.57 → 1.47 clips/s. The language model is not the bottleneck at this operating point.
2. **B vs D — same token count, 2.3× the throughput.** 1,928 vs 2,061 prompt tokens, but a 1 MB
   360p source runs at 3.58 clips/s against a 5.5 MB 1080p source's 1.57. The difference is
   entirely *source pixels and source bytes*, which the model never sees: both are resized to the
   same 448 × 448-equivalent grid before the vision tower.
3. **A vs B — TTFT grows 4.4× from c=1 to c=8**, 0.77 → 3.35 s, while TPOT grows only 1.33×
   (6 → 8 ms). Whatever is congesting is in front of the token loop, not inside it.

⚠️ **Caveat carried forward from `notes.md` finding 5 and not yet closed:** every request in every
row used the *same* clip. vLLM's multimodal processor cache is keyed on a hash of the multimodal
item and is on by default at 4 GiB [src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/multimodal.py),
and vLLM's prefix-cache block hashes incorporate the multimodal item hash as an "extra hash"
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/design/prefix_caching.md), so
rows B–D may be quoting a *fully cached* decode/preprocess path. The distinct-clip rerun is §7's
first experiment and every number in §1.2–§1.3 should be re-derived after it. `bench.py` also
sends one un-counted warm-up request before the measured run (`await one()` then `rows.clear()`),
so the 18 s first-kwargs penalty of §6.6 is excluded from every row by construction.

⚠️ **Second caveat, opened 2026-09-20 and load-bearing for §1.4 and §2.2: rows A–D were almost
certainly measured against vLLM directly, not through the gateway.** `bench.py`'s endpoint is
`ap.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://localhost:8000/v1"))`
([`bench.py`](../../models/marlin2b/bench.py)) — port **8000 is `marlin2b-vllm.service`**; the
gateway is on **8001** ([`marlin2b-gateway.service`](../../apps/infrx-api/deploy/marlin2b-gateway.service)).
Nothing in [`notes.md`](../../models/marlin2b/results/notes.md) or
[`README.md`](../../models/marlin2b/README.md) records `BASE_URL` being overridden, and the
corroborating detail is that the rows were taken with `--mm-kwargs auto`, which only has an effect
when the gateway is bypassed — `gateway.py` unconditionally overwrites
`body["mm_processor_kwargs"] = budget_kwargs(secs)`. The README's separate "~3.8 s end to end
(TTFT ~3.2 s, dominated by downloading and decoding the 5.5 MB source twice)" figure **is** a
gateway-path number, but it was measured on 2026-09-20 from another AWS host and is not row B.
**Consequence: the gateway stages Q1–Q5 of §1.4 were not on the measured path**, so the 2.69 s
row-B/row-D TTFT gap of §1.2 sits entirely inside vLLM's own fetch → decode → resize → processor
chain (Q5–Q7), and §2.2's *dedup* half wins none of it (its *transcode* half still does). Settling
this is a one-line check — record the base URL in the `bench.jsonl` row (§7.1) and re-run — and it
must be done before §2.2's expected effect is quoted to anyone.

⚠️ Related: `models/marlin2b/results/bench.jsonl`, which `notes.md` cites as the home of these rows
and which §7.3 proposes extending, **is not present in the repository** (`results/` contains only
`notes.md`, and no `.gitignore` rule excludes it). Rows A–D therefore cannot be re-derived, re-cut
or audited from the tree as it stands; they exist only as the two hand-written tables. Commit the
JSONL.

### 1.2 Reconstructing the per-request time budget

`bench.py` is a closed-loop harness: `c` workers each send the next request as soon as the previous
finishes ([`models/marlin2b/bench.py`](../../models/marlin2b/bench.py) `worker()`). So
`clips/s = c / mean_request_latency`, and `mean_request_latency ≈ TTFT + output_tokens × TPOT`.
Output tokens per clip = `out_tok_s / clips_s`. `calc.`:

| row | out tok/clip | decode time | TTFT | reconstructed latency | implied clips/s | measured |
|---|---:|---:|---:|---:|---:|---:|
| A (1080p, c=1) | 200 | 1.20 s | 0.77 s | 1.97 s | 0.51 | 0.50 |
| B (1080p, c=8) | 197 | 1.58 s | 3.35 s | 4.93 s | 1.62 | 1.57 |
| D (360p, c=8) | 212 | 1.49 s | 0.66 s | 2.15 s | 3.73 | 3.58 |

Every row closes to within **4.2 %** (A 1.5 %, B 3.4 %, D 4.2 % — `calc.` 2026-09-20; "within
4 %" was a rounding of the D row), so the model `latency = TTFT + N × TPOT` is sound and the
percentile mixing (p50 TTFT against a mean rate) is not hiding anything large. Read off it:

- **At c=1, 39 % of the request is TTFT and 61 % is decode.** A single user's experience is
  dominated by generating ~200 tokens at 6 ms each.
- **At c=8 on 1080p, 68 % of the request is TTFT.** Adding concurrency did not improve the box's
  ability to start requests; it built a queue in front of the token loop.
- **The 1080p penalty is 2.69 s of TTFT per request** (3.35 − 0.66 at the same concurrency and
  effectively the same prompt length). That is the number to attack.

**How much CPU is that?** If the CPU stage is the binding resource and it saturates the 8 hardware
threads, CPU-seconds per clip ≈ `8 / clips_s`: **5.1 thread-seconds for 1080p, 2.2 for 360p**
(`est.`, assumes full saturation and linear scaling — the assumption §7's core-sweep tests). The
**~2.9 thread-seconds of difference per 10-second clip** is decode-and-resize of 1080p frames plus
the larger transfer, and it is the single largest removable cost in the system.

### 1.3 The roofline: what the GPU is actually doing

Marlin's per-request compute is fully specified in
[`research/models/marlin2b/architecture.md`](../models/marlin2b/architecture.md) §6.1 and §6.4:
**3.764 GFLOP per language token** plus `24,576 · T²` for the 6 full-attention layers, and
**272.2 GFLOP per video frame** in the vision tower. A 10.1 s clip at the training budget is
`clamp(2 × 10.1, 4, 240) = 20` frames and 2,061 prompt tokens (measured). `calc.`:

```
ViT          20 frames × 272.2 GFLOP  =  5.44 TFLOP
LLM prefill  2,061 × 3.764 GFLOP      =  7.76 TFLOP  (linear)
           + 24,576 × 2,061²          =  0.10 TFLOP  (attention, 1.3 % of the linear prefill
                                                     term, 0.8 % of the total — `calc.`; corrected
                                                     2026-09-20 from "5 % of prefill")
                                        ---------
total prefill compute per request     = 13.31 TFLOP
```

The L40S is **362.05 TFLOPS dense BF16** (the NVIDIA page prints "362.05 | 733*" with `*` = with
sparsity) [src](https://www.nvidia.com/en-us/data-center/l40s/). So:

| assumed MFU | GPU prefill time per 10 s clip | share of the 0.77 s TTFT at c=1 |
|---:|---:|---:|
| 0.25 | 0.147 s | 19 % |
| 0.35 | 0.105 s | 14 % |
| 0.50 | 0.074 s | 10 % |

**81–90 % of TTFT at concurrency 1 is not GPU prefill.** (`est.` on the MFU band; the exact MFU is
§7's `--profile` experiment. Even the pessimistic 0.25 leaves 0.62 s unaccounted.)

Decode, by contrast, is already near its roofline. Bytes per decode step are
`3.7637 GB of weights + batch × ctx × 12,288 B` ([`architecture.md` §6.2](../models/marlin2b/architecture.md)),
L40S memory bandwidth is **864 GB/s** [src](https://www.nvidia.com/en-us/data-center/l40s/). `calc.`:

| row | batch | bytes/step | measured TPOT | achieved | **MBU** |
|---|---:|---:|---:|---:|---:|
| A | 1 | 3.789 GB | 6 ms | 631.5 GB/s | **0.731** |
| B | 8 (nominal) | 3.967 GB | 8 ms | 495.8 GB/s | 0.574 |

MBU 0.73 at batch 1 is squarely in the band this repo assumes for healthy decode
(`architecture.md` §6.5 uses 0.75 for H100/H200/A100). **The decode path on L40S is not broken and
is not the thing to optimise.**

The row-B MBU of 0.574 is the tell. If all 8 requests were decoding concurrently at 8 ms/token the
box would emit **1,000 output tok/s**; it emits 310. `calc.`: the decode batch averages
`310 × 0.008 = 2.5` requests, not 8. **Five and a half of the eight in-flight requests are, at any
instant, not in the token loop at all** — they are downloading, probing, transferring, decoding or
resizing.

Aggregate GPU demand at the measured steady state, `calc.`:

```
prefill:  13.31 TFLOP × 1.57 clips/s = 20.9 TFLOPS  =  5.8 % of 362.05 dense BF16
decode:   475 GB/s sustained          =             55 % of 864 GB/s, at batch 2.5
```

**We are renting an L40S and using 5.8 % of its arithmetic.** That is the headline for §2 and §5.

### 1.4 The stages of a request, and the two that happen twice

Tracing [`apps/infrx-api/gateway.py`](../../apps/infrx-api/gateway.py) `chat()` → `video_seconds()`
→ vLLM:

| # | stage | where it runs today | cost driver |
|---|---|---|---|
| Q1 | auth (`api_keys` lookup) | gateway, cached 60 s | Supabase RTT on a miss; ~0 warm |
| Q2 | **download or base64-decode the clip** | **gateway** (`video_seconds`) | source bytes |
| Q3 | write to a `NamedTemporaryFile`, `ffprobe` | gateway, thread pool | one `fork`+exec per request |
| Q4 | compute `mm_processor_kwargs`, forward the **original URL** | gateway | — |
| Q5 | **download or base64-decode the clip again** | **vLLM API server** | source bytes |
| Q6 | decode + resize frames (`opencv` → FFmpeg) | vLLM API server, CPU | **source pixels × frames** |
| Q7 | HF processor: normalize, patchify, tensorize | vLLM API server, CPU | frames × 200,704 px |
| Q8 | IPC of the pixel tensor to the engine core | vLLM, msgspec by default | tensor bytes |
| Q9 | vision encoder (ViT) | GPU | 272.2 GFLOP/frame |
| Q10 | LLM prefill | GPU | 3.764 GFLOP/token |
| Q11 | LLM decode | GPU, bandwidth-bound | 3.76 GB/step |
| Q12 | SSE relay, `<think>` strip, usage row | gateway | ~0 |

**Q2 and Q5 are the same work done twice.** ⚠️ **But not on the measured path** — see §1.1's
second caveat: rows A–D appear to have gone straight to vLLM on `:8000`, so Q1–Q5 never ran for
them and none of the §1.2 numbers price this defect. It is still a real defect on the *public*
path, which is what the README measured. `models/marlin2b/README.md` already says so —
"~3.8 s end to end (TTFT ~3.2 s, dominated by downloading and decoding the 5.5 MB source twice,
gateway and vLLM)". `video_seconds()` downloads the bytes into a temp file, probes it, and then
`os.unlink`s the file in a `finally` block, discarding everything it just paid for, while
`body["messages"]` still carries the original `video_url` for vLLM to fetch again. Fixing this is
§2.3 and it is the cheapest large win available.

Two smaller defects in the same function, both latency-relevant under concurrency:

- `base64.b64decode(url.split(",", 1)[1])` and `f.write(data)` run **on the event loop** of a
  single-worker uvicorn (`ExecStart=… --workers 1` in
  [`marlin2b-gateway.service`](../../apps/infrx-api/deploy/marlin2b-gateway.service)). A 64 MB
  `data:` URL — the limit `MAX_VIDEO_MB` allows — blocks every other in-flight request for the
  duration of the decode and the write. `bench.py` sends base64 by default for local files, so this
  is on the measured path.
- `probe_seconds` goes to `run_in_executor(None, …)`, the default `ThreadPoolExecutor`, which on
  CPython 3.12 sizes itself at `min(32, os.cpu_count() + 4)` = **12 threads on this 8-vCPU box** —
  more concurrent `ffprobe` forks than there are hardware threads, against a `MAX_INFLIGHT` of 16.

### 1.5 The instrumentation to add

Nothing above is directly observable today. `usage.jsonl` records `ttft_s` and `wall_s` per request
and nothing between them. Three layers, cheapest first.

**(a) Gateway stage timers — 12 lines, no dependencies.** Record monotonic deltas around each stage
and emit them in the existing usage row and as a `Server-Timing` response header, which every
browser devtools panel renders natively and which is a W3C spec, not a convention
[src](https://www.w3.org/TR/server-timing/).

```python
# gateway.py — replace the bare `usage` dict with staged timings
t = {}                                  # stage -> seconds
def mark(name, t0): t[name] = round(time.perf_counter() - t0, 4)

# ... around each stage:
s = time.perf_counter(); data = await fetch(url);      mark("fetch", s)
s = time.perf_counter(); secs = await probe(path);     mark("probe", s)
s = time.perf_counter(); blob = await transcode(path); mark("xcode", s)
# upstream call already measured as ttft_s / wall_s

headers = {"Inference-Id": rid,
           "Server-Timing": ", ".join(f"{k};dur={v*1000:.1f}" for k, v in t.items())}
```

Add the same keys to the `usage.jsonl` line. Do **not** add them to `usage_events` yet — the
Supabase table is the customer-facing billing record (`apps/README.md` §6) and stage timings are
operator data; put them in the local JSONL and scrape from there.

**(b) vLLM's own Prometheus metrics.** vLLM exports, among others,
`vllm:time_to_first_token_seconds`, `vllm:request_queue_time_seconds`,
`vllm:request_prefill_time_seconds`, `vllm:request_decode_time_seconds`,
`vllm:num_requests_running` / `_waiting`, `vllm:kv_cache_usage_perc`,
`vllm:prefix_cache_queries` / `_hits` and `vllm:request_time_per_output_token_seconds`
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/design/metrics.md). Two notes
from that doc that matter when reconciling against `bench.py`:

- `vllm:inter_token_latency_seconds` is *per streamed output event*, while
  `vllm:request_time_per_output_token_seconds` is `(e2e − TTFT) / (output_tokens − 1)` per request —
  "statistics can differ. Use `vllm:request_time_per_output_token_seconds` for request-level TPOT"
  (ibid.). `bench.py` computes the latter; compare like with like.
- `vllm:request_queue_time_seconds` is the *engine's* queue, not the gateway's `MAX_INFLIGHT` gate.
  The gateway's wait is invisible to vLLM and must come from (a).

vLLM's metrics live on `/metrics` of the OpenAI server, which is bound to `127.0.0.1:8000` today.
Leave it there and scrape locally; do not expose it through Caddy.

**The single most valuable derived metric** is the decode duty cycle from §1.3, computable entirely
from vLLM counters as a Prometheus recording rule:

```promql
# average decode batch occupancy: how many of the in-flight requests are actually generating
record: marlin:decode_batch_occupancy
expr: rate(vllm:generation_tokens_total[1m])
      * histogram_quantile(0.5, rate(vllm:request_time_per_output_token_seconds_bucket[5m]))

# the CPU-starvation signal: requests in flight at the gateway vs requests generating
record: marlin:cpu_stall_ratio
expr: (gateway_inflight - marlin:decode_batch_occupancy) / gateway_inflight
```

At the measured row B, `decode_batch_occupancy ≈ 2.5` and `cpu_stall_ratio ≈ 0.69`. **Alert above
0.5** — it means the box is paying for a GPU to wait on ffmpeg.

**(c) A torch profile, once, not continuously.** vLLM supports `--profiler-config '{"profiler":
"torch", "torch_profiler_dir": "./vllm_profile"}'` and `vllm bench serve --profile`, with traces
read in <https://ui.perfetto.dev/>; the docs are explicit that "vLLM end-users should never turn on
profiling as it will significantly slow down the inference" and that only a few requests should be
sent while profiling [src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/contributing/profiling.md).
Use it once to settle the two open questions this document cannot answer from the outside: the real
ViT MFU on Ada, and whether the Gated-DeltaNet prefill path falls back to FLA Triton (§3.6).

---

## 2. The CPU side

### 2.1 vLLM's own CPU rule, against the box we rent

vLLM publishes a minimum: for `N` GPUs there are at least `2 + N` processes (1 API server, 1 engine
core, N workers), the minimum is `2 + N` **physical** cores, and — verbatim — *"If your system has
hyperthreading enabled, then 1 vCPU = 1 hyperthread = 1/2 physical CPU core, so you need
`2 x (2 + N)` minimum vCPUs"*
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/configuration/optimization.md).
The same section warns that "CPU underprovisioning particularly impacts input processing throughput
— tokenization, chat template rendering, and multi-modal data loading all run on CPU" and that "if
you observe that GPU utilization is lower than expected, CPU contention may be the bottleneck."

AWS publishes `g6e.2xlarge` as **8 vCPUs, 4 physical cores, 2 threads per core, AMD EPYC 7R13,
1 × NVIDIA L40S** [src](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html). So:

| | value |
|---|---|
| vLLM minimum for N=1 | 3 physical cores / **6 vCPU** |
| `g6e.2xlarge` provides | 4 physical cores / 8 vCPU |
| also running on those cores | `gateway.py` (uvicorn), `ffprobe` per request, Caddy, dockerd, the DLAMI agent |

We are **one physical core above vLLM's stated floor**, and we spend that core plus change on a
second full copy of the download-and-probe work (§1.4). The default media-loading thread count
compounds it: *"By default, 8 CPU threads are used in each API server to load media items (e.g.
images) from request data"*, with an explicit instruction to tune `VLLM_MEDIA_LOADING_THREAD_COUNT`
when scaling out API servers (ibid.). Eight media threads plus twelve gateway executor threads
(§1.4) on four cores is a thread count chosen by three defaults that have never met.

### 2.2 Transcode once, in the gateway, and hand vLLM the result

**The change.** `video_seconds()` already has the bytes on local disk. Instead of unlinking them,
normalise the clip to what the model will actually consume and pass *that* inline as a `data:` URL.
The model never sees more than 200,704 px per frame at 2 fps
([`architecture.md` §6.3](../models/marlin2b/architecture.md)), so anything above ~480p and above
2 fps in the source is pixels decoded and then thrown away.

```bash
# one ffmpeg pass: 2 fps, longest edge 448, H.264, no audio, faststart
ffmpeg -nostdin -v error -threads 2 -i in.mp4 \
       -vf "fps=2,scale='if(gt(iw,ih),448,-2)':'if(gt(iw,ih),-2,448)'" \
       -an -c:v libx264 -preset veryfast -crf 28 -pix_fmt yuv420p \
       -movflags +faststart out.mp4
```

Three properties of that command are load-bearing and each is a knob, not a constant:

- **`-threads 2`.** FFmpeg's own default is `min(cpu_count + 1, 16)` — vLLM documents exactly that
  default for its TorchCodec backend's `num_ffmpeg_threads`, where `0` "relies on the FFmpeg
  default, which is `min(cpu_count + 1, 16)`"
  [src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/multimodal_inputs.md).
  On a 4-core box with up to 16 concurrent requests, letting each ffmpeg take 9 threads is how you
  get a load average of 140. Cap it low and get parallelism from *requests*, not from *frames*.
- **`fps=2` before `scale`.** Filter order decides whether you resize 300 frames or 20. Put the
  decimation first.
- **`-crf 28` / `-preset veryfast`.** The output is consumed by a model that downsamples to a
  28 × 28 patch grid; it is not a deliverable. ⚠️ **TO BE VERIFIED** that CRF 28 at 448 px is
  quality-neutral for Marlin's timestamp accuracy — §7 experiment 5 is the A/B.

**Then stop making vLLM re-fetch.** Replace the request's `video_url` with the transcoded bytes and
attach a stable cache key:

```python
body_part["video_url"]["url"] = "data:video/mp4;base64," + b64(out_bytes)
body_part["uuid"] = content_hash           # vLLM's optional multimodal cache key
```

The `uuid` field on a multimodal content part is documented and optional
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/multimodal_inputs.md);
supplying the content hash of the *normalised* clip means two requests for the same video hit
vLLM's processor cache even when the callers passed different URLs for it.

**Do the base64 and the ffmpeg run off the event loop.** Both belong in a bounded executor sized to
the core count, not the default 12-thread pool:

```python
# gateway.py, module scope
CPU = ThreadPoolExecutor(max_workers=int(os.environ.get("XCODE_WORKERS", "3")))
```

`3` = physical cores − 1, leaving one for the event loop. On `g6e.4xlarge` (8 cores) set 7.

**Expected effect, `est.`:** removes Q5 entirely (one download instead of two), and replaces Q6's
1080p decode-and-resize inside vLLM with a 448 p decode. Row B should move toward row D — the
2.69 s TTFT gap of §1.2 is the size of the prize. It will not reach D exactly, because the gateway
still pays the 1080p decode once. **⚠️ TO BE VERIFIED by §7 experiment 2**; do not quote a number
until it is measured.

⚠️ **Revised 2026-09-20.** The two halves of this change have different evidence behind them, and
§1.1's second caveat separates them. The **transcode** half is what the 2.69 s gap measures: rows B
and D differ only in source pixels and source bytes, both decoded inside vLLM, and normalising to
448 p attacks exactly that. The **dedup** half (Q2/Q5) is *not* measured by that gap at all if rows
A–D bypassed the gateway, and its size is bounded instead by the README's separate gateway-path
figure (~3.8 s end to end vs ~2 s at c=1 on the direct path). Both are still worth doing and the
ordering is unchanged, but the headline "2.69 s is the size of the prize" belongs to the transcode
alone. Experiment 2 must therefore run **through the gateway** (`BASE_URL=http://localhost:8001/v1`)
and record it, or it measures neither change.

### 2.3 Hardware decode with NVDEC on the L40S

The L40S has **3 NVDEC engines** and supports hardware decode of H.264, HEVC, VP9 and AV1
[src](https://developer.nvidia.com/video-encode-and-decode-gpu-support-matrix-new) — but **corrected
2026-09-20**, not uniformly in 8- *and* 10-bit. Re-reading the decode table (header: `BOARD | FAMILY
| NVDEC Generation | # OF CHIPS | # OF NVDEC/CHIP | Total # of NVDEC | codecs…`), the L40S row is
`Ada Lovelace | 5th Gen | 1 | 3 | 3` and then **H.264 4:2:0 8-bit `YES`, H.264 10-bit and all H.264
4:2:2 `NO`**, HEVC 4:2:0 and 4:4:4 `YES` at 8/10/12-bit, HEVC 4:2:2 `NO`, VP9 `YES` at 8/10/12-bit,
AV1 `YES` at 8/10-bit (ibid.). §2.2's `-c:v libx264 -pix_fmt yuv420p` output therefore stays on the
supported path; a 10-bit H.264 source does **not**, and would fall back to CPU. The same page's
product page states as "NVENC/NVDEC: 3x | 3x (includes AV1 encode and decode)"
[src](https://www.nvidia.com/en-us/data-center/l40s/). There are two ways to reach them.

**(a) Inside vLLM, via the `pynvvideocodec` backend.** vLLM ships four video backends: `opencv`
(default, CPU), `torchcodec` (CPU), `pynvvideocodec` (GPU/NVDEC) and `deepstream` (GPU/NVDEC, for
streaming sources); "the two CPU backends are ultimately backed by FFmpeg"
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/multimodal_inputs.md).
vLLM's own launch post measured this: *"At 8xH100, GPU-based video decoding provides more than
double the throughput compared to the CPU-based video decoder"* against a "CPU-based OpenCV+FFMPEG"
baseline, and *"CPU-based video decoding can quickly become a bottleneck, maxing out CPU cores even
with just 2 or 4 GPUs"* [src](https://vllm.ai/blog/2026-09-18-pynvvideocodec). Their serve line:

```bash
vllm serve Qwen/Qwen3-VL-8B-Instruct --dtype bfloat16 --max-model-len 32768 \
  --max-num-seqs 1024 --max-num-batched-tokens 32768 --api-server-count 4 \
  --renderer-num-workers 4 --async-scheduling --mm-ipc-gpu-memory-gb 2 \
  --media-io-kwargs '{"video":{"backend":"pynvvideocodec","min_frames":16,"max_frames":16,"hw_decoders":2}}'
```

Two hard requirements come with it, both documented:

> ⚠️ **"[CUDA Multi-Process Service (MPS)] is required when using this backend. Video decoding runs
> in the API server process while model serving runs in the engine process, so multiple CUDA
> processes share the same GPU. Configure and start MPS before starting vLLM."**
> [src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/multimodal_inputs.md)

and a positive `--mm-ipc-gpu-memory-gb`, which "vLLM carves out of the memory available to the KV
cache and uses it to bound concurrent frontend decode allocations. If the budget is exhausted,
decode work waits instead of consuming the engine's VRAM headroom" (ibid.). `hw_decoders` defaults
to 2 and "cannot be overridden per request" because the VRAM is reserved at startup (ibid.).

That MPS requirement is why §4 exists: **turning on NVDEC forces the MPS decision**, independently
of whether we ever want two engines on one GPU.

**(b) Outside vLLM, in the gateway's ffmpeg.** NVIDIA's own FFmpeg guide gives the decode-side
flags, `-hwaccel cuda -hwaccel_output_format cuda`, with `scale_cuda` or `scale_npp` to keep frames
in GPU memory, and notes that aggregate transcode performance matches the hardware's theoretical
rate only when "running multiple parallel encode/decode sessions on the hardware"
[src](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/ffmpeg-with-nvidia-gpu/index.html).

**Decision rule.**

| situation | choice |
|---|---|
| single GPU, gateway and vLLM co-located, no MPS today | **(b) first**: `-hwaccel cuda` in the gateway's ffmpeg is one flag and no MPS — but it still opens a second CUDA context on the GPU, so measure whether it disturbs the engine before shipping. If it does, fall back to CPU ffmpeg at `-threads 2` and buy cores instead (§2.4). |
| CPU is measurably the bottleneck *after* §2.2 and the dedup | **(a)**: enable MPS, `--media-io-kwargs '{"video":{"backend":"pynvvideocodec","hw_decoders":2}}'`, `--mm-ipc-gpu-memory-gb 1`. Budget the KV loss: 1 GiB off a pool that already only needs 0.287 GiB per concurrent 2-minute video ([`architecture.md` §8.5](../models/marlin2b/architecture.md)) is affordable at our concurrency. |
| multi-GPU node later (`g6e.12xlarge`, 4 × L40S, 48 vCPU) | **(a)**, definitively — this is exactly the "bottleneck on CPU utilization before 4 GPUs" case vLLM measured. |

⚠️ **TO BE VERIFIED:** the L40S's *concurrent decode session* limit. The support matrix lists 3
NVDEC engines but the "max concurrent sessions" column was not extracted in this pass; NVIDIA
historically restricts concurrent *encode* sessions on GeForce and not on professional cards, and
the decode-side policy for L40S is unconfirmed here. Resolve before sizing `hw_decoders` above 2.

### 2.4 The right vCPU : GPU ratio

Exact on-demand prices, us-east-1, Linux, pulled from AWS's own price sheet on **2026-09-20** — the
same primary source [`cloud-pricing.md`](../cross-cutting/cloud-pricing.md) §3.1 uses
[src](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json).
Core counts and GPU counts from the instance-type spec page
[src](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html).

| instance | GPU | vCPU | **phys. cores** | RAM | instance store | $/hr | $/core-hr |
|---|---|---:|---:|---:|---|---:|---:|
| `g6e.xlarge` | 1 × L40S | 4 | 2 | 32 GiB | 1 × 250 GB NVMe | 1.8610 | 0.9305 |
| **`g6e.2xlarge`** (today) | 1 × L40S | 8 | **4** | 64 GiB | 1 × 450 GB NVMe | **2.24208** | 0.5605 |
| **`g6e.4xlarge`** | 1 × L40S | 16 | **8** | 128 GiB | 1 × 600 GB NVMe | **3.00424** | **0.3755** |
| `g6e.8xlarge` | 1 × L40S | 32 | 16 | 256 GiB | 2 × 450 GB NVMe | 4.52856 | 0.2830 |
| `g6e.12xlarge` | 4 × L40S | 48 | 24 | 384 GiB | 2 × 1900 GB NVMe | 10.49264 | 0.4372 |

The marginal cost of the step we care about, `calc.`: `g6e.4xlarge − g6e.2xlarge = $0.76216/hr` for
**+4 physical cores and +64 GiB**, a 34 % price increase for a 100 % increase in the resource that
is measurably binding. If throughput scales even 1.5× with it, $/clip falls.

`calc.`, at today's measured rates and the linear-CPU-scaling hypothesis (capped at row D's
3.58 clips/s, where CPU stops being the constraint):

| instance | clips/s (1080p sources) | clips/hour | **$ / 1,000 clips** |
|---|---:|---:|---:|
| `g6e.2xlarge` | 1.57 `meas.` | 5,652 | **$0.397** |
| `g6e.2xlarge`, 360p sources | 3.58 `meas.` | 12,888 | **$0.174** |
| `g6e.4xlarge` | 3.14 `est.` (2×, capped) | 11,304 | **$0.266** |
| `g6e.8xlarge` | 3.58 `est.` (capped) | 12,888 | **$0.351** |

**Decision rule.** Move the box to **`g6e.4xlarge`** and stop there. `g6e.8xlarge` buys cores the
GPU cannot feed and gets *worse* per clip. Note that `g6e.4xlarge` also **quadruples** EBS
throughput (8,000 vs 2,000 Mbps baseline — corrected 2026-09-20 from "doubles"; 8,000/2,000 = 4×,
and the burst ceiling goes 5,000 → 8,000 Mbps, i.e. `g6e.4xlarge` is at baseline what
`g6e.2xlarge` could only burst to) and quadruples network baseline (20 Gbps flat vs 5 Gbps
burstable to 20) [src](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html), both of which matter for
Q2/Q5. ⚠️ The 2× scaling figure is a hypothesis, not a measurement — §7 experiment 3 tests it by
constraining cores with `taskset`/cgroups on the current box *before* paying for the bigger one.

**Also relevant to §5 and to the capacity problem in `CLAUDE.md`:** `g6e` capacity in us-east-1 is
scarce enough that only AZ `1d` had any. A size change is a new capacity request. Reserve or use an
On-Demand Capacity Reservation for the target size before terminating the current instance.

### 2.5 A separate transcode fleet

**Do not build one yet.** The ladder is: (1) stop doing the work twice, (2) transcode to 448p once,
(3) buy cores, (4) NVDEC, (5) separate fleet. Steps 1–3 are a day's work and address a measured 2.69 s.

When it *does* become right — the signal is `marlin:cpu_stall_ratio` staying above 0.5 *after* §2.2
and a core upgrade, or the arrival of a second model on the same box — the shape is not a new
service. It is **the gateway split in two**: the same `gateway.py`, run on cheap CPU instances with
`XCODE_WORKERS` high and no GPU, writing normalised clips to S3 and posting the S3 key to the GPU
box. That reuses every line of auth, budget, usage and `<think>` handling already written and
tested. The two properties that make it work:

- The normalised clip is **~40× smaller** than the source in the measured case (5.5 MB 1080p → a
  20-frame 448 p H.264 clip; ⚠️ exact ratio TBV in §7), so the S3 round trip costs less than the
  original upload did.
- S3 and EC2 in the same region is the intended path; AWS's EC2 pricing page carries a "Data
  Transfer within the same AWS Region" section [src](https://aws.amazon.com/ec2/pricing/on-demand/).
  ⚠️ The exact same-region S3↔EC2 rate was not extracted in this pass — confirm before sizing.

The alternative, a vLLM **disaggregated encoder** instance, is documented and real —
*"Independent, fine-grained scaling"*, *"Lower time-to-first-token"*, *"Cross-process reuse and
caching of encoder outputs"*, reference pathway `ExampleConnector` with `NixlConnector` examples,
shapes `E→PD` and `E→P→D`
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/disagg_encoder.md) —
but it separates the **ViT**, which §1.3 shows is 5.44 of 13.31 TFLOP and running on an idle GPU.
It does not separate the ffmpeg. **It solves a problem we do not have.**
[`research/scaling/03` §6.5](../scaling/03-concurrency-and-admission-control.md) reaches the same
verdict for pure-video traffic: *"Pure video traffic → keep it in-process and spend the effort on
hardware decode instead."*

### 2.6 Caching

Four distinct caches, only two of which are ours to build.

**(a) The normalised-clip cache (ours, new, highest value).** Key = SHA-256 of the *source* bytes;
value = the transcoded 448 p clip plus its duration. Local first: the box has a **450 GB NVMe
instance store** at `/opt/dlami/nvme` [src](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html).
An LRU directory there costs nothing and turns a repeat request into a `stat` + read.

```python
# gateway.py — ~15 lines, no dependency
key = hashlib.sha256(data).hexdigest()
path = f"{CLIP_CACHE}/{key[:2]}/{key}.mp4"
if not os.path.exists(path):
    await loop.run_in_executor(CPU, transcode, src, path)   # atomic rename inside
```

This is the whole reason to hash: it also becomes the `uuid` handed to vLLM (§2.2) and the cache
key for a future S3 tier. **Size the LRU by disk, evict by atime, and never let it be the reason a
request fails** — on any cache error, transcode and carry on.

⚠️ **Privacy note, and it is not optional.** A content-addressed cache shared across tenants means
customer A's upload can be served to customer B from cache, and cache *timing* leaks whether a clip
has been seen before. vLLM has the matching primitive for its own caches — `cache_salt` in the
request body, which "is injected into the hash of the first block, ensuring that only requests with
the same salt can reuse cached KV blocks. This prevents timing-based attacks where an adversary
could infer cached content by observing latency differences"
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/design/prefix_caching.md).
**Set `cache_salt = org_id` in `gateway.py`, and key the clip cache on `(org_id, sha256)`.** Both
are one-line changes and both are the difference between a cache and an incident.

**(b) vLLM's multimodal processor cache (theirs, on by default, tune it).** "Multi-modal processor
caching is automatically enabled to avoid repeatedly processing the same multi-modal inputs";
`mm_processor_cache_gb` defaults to **4 GiB** and the total footprint is
`mm_processor_cache_gb × (api_server_count + data_parallel_size)`
[[docs](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/configuration/optimization.md),
[source](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/multimodal.py)].
This cache is host RAM, and it is what makes `notes.md` finding 5 suspect. With 64 GiB on the box
today, 4 GiB is fine; raise it to 8–16 GiB on `g6e.4xlarge` **only if** §7's distinct-clip run shows
real repeat traffic. If API-server scale-out is used (§3), note that it "disables multi-modal IPC
caching … This does not impact multi-modal processor caching" (ibid.) and that the footprint then
multiplies by `api_server_count`.

**(c) vLLM's prefix cache (theirs, worth ~nothing here).**
[`architecture.md` §5.5](../models/marlin2b/architecture.md) is unambiguous: vLLM **refuses to
start** Qwen3.5 with `--mamba-cache-mode=all` (`raise NotImplementedError`), `align` mode is
"currently experimental", and the realistic hit rate is ~0 % because "every request carries a
different video" and the only cacheable span is a 30–40-token scaffold — **< 0.2 % of the request**.
Do not budget for it. §3.9 covers the one exception.

**(d) The HTTP layer (ours, trivial).** The gateway fetches customer URLs with a fresh
`httpx.AsyncClient` **per request** (`async with httpx.AsyncClient(...) as c` inside
`video_seconds`), so every fetch pays a fresh TCP + TLS handshake to the origin. Hoist it to module
scope alongside the existing `client` and `sb` clients; httpx pools connections per client. For a
customer serving clips from one CDN this removes ~2 RTT per request.

---

## 3. vLLM tuning for Marlin-2B on the L40S

Current command, from [`models/marlin2b/serve.sh`](../../models/marlin2b/serve.sh) plus the
`--max-num-seqs 32` that [`marlin2b-vllm.service`](../../apps/infrx-api/deploy/marlin2b-vllm.service)
appends:

```
--served-model-name marlin2b --hf-overrides '{"architectures":["Qwen3_5ForConditionalGeneration"]}'
--max-model-len 32768 --gpu-memory-utilization 0.90 --limit-mm-per-prompt '{"video":1,"image":4}'
--dtype bfloat16 --max-num-seqs 32
```

### 3.1 `max-num-batched-tokens` is also the encoder budget — and it is unset

vLLM's scheduler config is explicit and the coupling is not configurable:

```python
self.max_num_encoder_input_tokens = self.max_num_batched_tokens
self.encoder_cache_size          = self.max_num_batched_tokens
# TODO (ywang96): Make this configurable.
```
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/scheduler.py)

So on a video fleet `max_num_batched_tokens` is simultaneously the prefill chunk size, the encoder
compute budget and the encoder cache size — the point
[`research/scaling/03` §2.7](../scaling/03-concurrency-and-admission-control.md) makes. We do not
set it, so we inherit whatever `EngineArgs.create_engine_config` computes for this model and never
see it in the unit file.

**Set it explicitly.** A 2-minute clip is ~23,560 prompt tokens
([`architecture.md` §6.3](../models/marlin2b/architecture.md)) and that is the maximum any request
can be, because the 240-frame cap bounds it. vLLM's own guidance: "Smaller values (e.g. 2048)
achieve better ITL… Higher values achieve better TTFT… For optimal throughput, we recommend setting
`max_num_batched_tokens > 8192` especially for smaller models on large GPUs"
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/configuration/optimization.md).

| candidate | consequence |
|---|---|
| 8,192 | a 2-min clip is chunked into 3 prefill steps; ITL protected; encoder cache small |
| **16,384** | **recommended starting point** — above vLLM's 8,192 throughput floor, 2-min clips still chunk, encoder cache is 16 K tokens ≈ 167 frames of budget |
| 32,768 | = `max_model_len`; the largest clip prefills in one step, best TTFT, worst ITL for anyone decoding at the time |

Start at **16,384** and sweep it in §7. Note the compile-cache interaction: `max_num_batched_tokens`
and `max_num_seqs` are both inputs to the `torch.compile` cache hash — "LoRA creates static buffers
based on max_num_batched_tokens… Inductor decides whether using 32-bit or 64-bit indexing integer
based on the data sizes" (`scheduler.py`, above) — so every change to either is a full recompile on
the next boot. Pin them before you bake a compile cache (§3.5).

### 3.2 `max-num-seqs`: 32 is fine; the gateway's 16 is the real limit

Capacity is not the constraint. [`architecture.md` §8.5](../models/marlin2b/architecture.md) sizes
`ctx × 12,288 + 19,537,920` bytes per sequence — **0.287 GiB per concurrent 2-minute video** — and
records 205 concurrent 2-minute videos on an 80 GB H100. Scaling the same formula to the L40S's
48 GB at `gpu_memory_utilization 0.90`, minus 4.426 GB of weights and a 4 GiB activation workspace,
`est.` ≈ **110 concurrent 2-minute videos**, or **~770** at the 10-second operating point
(`calc.` 2026-09-20 on the §3 budget of 34.48 GB: 2,061 × 12,288 + 19,537,920 = 44.86 MB per seat.
The ~1,300 printed here before dropped the 19,537,920 B per-sequence GDN state, which is 44 % of a
short-clip seat and only 6 % of a 2-minute one — which is why the 2-minute figure was unaffected).

Against that, `--max-num-seqs 32` is already 3× below the KV cap and `MAX_INFLIGHT=16` in the
gateway is 7× below it. **That is the correct direction** — it is exactly the conclusion
[`research/scaling/03` §6.4](../scaling/03-concurrency-and-admission-control.md) reaches for video
("admit on prefill throughput, not on KV… `max_num_seqs` set well below the KV cap") — but the two
numbers should be set from one model, not independently. §1.3 gives the model: at c=8 the decode
batch is 2.5 and `cpu_stall_ratio` is 0.69, so **16 is already past the knee on this box**.

Two newer knobs are worth knowing, both in `scheduler.py` (above):

- `max_num_queued_reqs` — "Maximum number of requests that can be in-flight (waiting or running)…
  enforced in the API server process and counts in-flight requests across all DP ranks". This is
  vLLM's own version of `MAX_INFLIGHT`, one layer closer to the truth.
- `max_num_queued_tokens` — "Maximum total prompt tokens of requests currently in the prefill
  phase… new requests are rejected with HTTP 503." This is the prefill-backlog admission control
  that `research/scaling/03` §3.2 derives by hand. **⚠️ Do not turn it on without coordinating with
  the queueing design in this directory** — a 503 from vLLM would surface through
  `gateway.py`'s `except Exception` as a 502, which is wrong for a retryable condition.

`policy: SchedulerPolicy = "fcfs"` is the default (ibid.); `priority` exists. Leave it FCFS —
priority scheduling is a product decision, not a throughput one.

### 3.3 Chunked prefill: on by default, leave it on

`enable_chunked_prefill: bool = True` (`scheduler.py`), and V1 "prioritizes decode requests. It
batches all pending decode requests before scheduling any prefill operations"
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/configuration/optimization.md).
That ordering is exactly what a mixed video fleet wants: it stops a 23.5 K-token prefill from
stalling eight streams mid-caption. There is one interaction to set deliberately —
`disable_chunked_mm_input`, which when `False` (the default) "lets a multimodal item be split across
steps, which is better for ITL and worse for encoder-cache behaviour"
([`research/scaling/03` §6.5](../scaling/03-concurrency-and-admission-control.md)). Leave the
default; revisit only if §7 shows encoder-cache thrash.

`long_prefill_token_threshold` (default `0` = disabled, `scheduler.py`) caps how long a prefill may
be before it is treated as "long". With every request bounded at 23.5 K there is no long tail to
protect against. Leave it.

### 3.4 `gpu-memory-utilization`, and the memory NVDEC wants back

`0.90` today. If §2.3(a) is adopted, `--mm-ipc-gpu-memory-gb 1` takes 1 GiB out of the KV pool
(§2.3). On a 48 GB card serving a 4.4 GB model at ≤ 1,300 concurrent short clips, that is
invisible. Keep `0.90`, add the IPC budget, and **do not** raise utilisation to compensate — the
headroom is what keeps the encoder's varlen activation burst from OOMing.

One startup-time knob worth taking: `--kv-cache-memory`. "On startup, vLLM logs the exact
`--kv-cache-memory` value that reproduces the current allocation. Passing it back on the next boot
skips the memory-profiling measurement and the CUDA-graph memory estimation pass"
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/configuration/optimization.md).
The same doc warns it is "only valid on the same GPU with the same initial free memory" — so it is
safe for a pinned instance type and **unsafe to bake into an AMI shared across GPU types**. Cold
start is [`research/scaling/06`](../scaling/06-cold-start.md)'s topic; the knob is noted here
because it is a *latency* lever for the first request after a scale-up.

### 3.5 CUDA graphs and the compile cache

**Decoder graphs.** The default is `FULL_AND_PIECEWISE` — "full CUDA Graph for uniform decode,
piecewise CUDA Graphs for others; generally the most performant setting, especially for low latency
with small models"
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/design/cuda_graphs.md). Marlin
is a small model. Leave it. The optimization levels are `-O0`…`-O3`, with `-O2` the default
("additional compilation ranges, additional fusions, FULL_AND_PIECEWISE cudagraphs") and `-O3`
"currently equal to `-O2`"
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/configuration/optimization.md).
**There is no `-O3` win to collect today.**

**Encoder (ViT) graphs — this is the one to try.** vLLM captures the vision encoder forward as its
own CUDA graphs, independently of the decoder, and the stated motivation is ours exactly: *"Vision
encoder inference incurs CUDA kernel launch overhead on the host side. The overhead is more
significant when the batch size is small or image size is small."*
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/design/cuda_graphs_multimodal.md)
Marlin runs 448 × 448 frames — small images, and at our concurrency, small batches. Better still,
the compatibility matrix in that document lists **`Qwen3_5ForConditionalGeneration` ✅ for images
and ✅ for video** — and `Qwen3_5ForConditionalGeneration` is precisely what `--hf-overrides` makes
Marlin be.

```bash
# add to serve.sh
--compilation-config '{"cudagraph_mm_encoder": true}'
```

`cudagraph_mm_encoder` defaults to `False`; budgets auto-infer as power-of-2 levels from the model's
own range, overridable with `encoder_cudagraph_token_budgets` (ibid.). At 20 frames ×  392 ViT
patches = 7,840 patches per 10 s clip, the auto budgets should bracket us without tuning.

⚠️ **TO BE VERIFIED, and it is the load-bearing caveat of this section:** that document's
Model × Hardware matrix has columns for **NV Blackwell, NV Ampere, AMD MI300X, AMD MI350X/MI355X**
— **there is no Ada (SM 8.9) column**, and the tested-backends note names Blackwell and MI350X only.
The L40S is SM 8.9. Encoder CUDA graphs on L40S are **untested upstream**; treat this as an
experiment with a measurable pass/fail (§7 experiment 4), not a recommendation. Also note video
graphs "are automatically disabled when video token pruning (EVS or VidCom2) is enabled" (ibid.) —
so §3.8's pruning idea and this are mutually exclusive.

**The compile cache.** vLLM persists `torch.compile` artifacts under `VLLM_CACHE_ROOT` (default
`~/.cache/vllm`), "you can directly copy the whole `~/.cache/vllm/torch_compile_cache` directory in
your deployment scenario to save a great amount of compilation time"
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/design/torch_compile.md), and
`VLLM_FORCE_AOT_LOAD=1` makes a cache miss fail loudly instead of silently recompiling
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/configuration/optimization.md).
Today `serve.sh` runs `docker run --rm` with **no cache volume mounted**, so every restart pays full
compilation — part of the "~2–3 min including torch.compile" boot. One line fixes it:

```bash
-v /opt/dlami/nvme/vllm-cache:/root/.cache/vllm \
```

The cache is invalidated by "any change to the model, config, relevant `VLLM_*` environment
variables, torch build, or GPU model" (ibid.) — which is why §3.1 says pin `max_num_batched_tokens`
*before* baking it. Cold-start strategy is [`research/scaling/06`](../scaling/06-cold-start.md);
this is just the line that makes it possible.

### 3.6 FP8 on Ada: three different questions, three different answers

The L40S is Ada (SM 8.9) and does have FP8 tensor cores: "FP8 Tensor Core: 733 | 1,466*" with `*` =
with sparsity, i.e. **733 TFLOPS dense**, 2.02× the BF16 rate
[src](https://www.nvidia.com/en-us/data-center/l40s/). vLLM's support matrix confirms **llm-compressor
FP8 (W8A8) ✅ on Ada** and Marlin-kernel FP8 ✅ on Ada
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/quantization/README.md).
Three separate propositions follow, and they do not have the same answer.

**(a) FP8 *weights*. Possible, low value, and there is no checkpoint.**
[`architecture.md` §9](../models/marlin2b/architecture.md) inventories every published Marlin-2B
variant and concludes: *"Notably absent: ❌ no NVFP4, MXFP4, AWQ or FP8 checkpoint"* — the only
quantised builds are MLX 8-bit (Apple Silicon), a community GPTQ INT4, an SDNQ INT8 and a GGUF, and
*"Evaluations of the quantised variants: none published… For a model whose value is second-precise
timestamps, INT4 numeric error landing on the timestamp digits is a specific, plausible and entirely
unmeasured risk."* We would have to quantise it ourselves with llm-compressor and then validate
timestamps.

And the payoff is small. Weight reads fall from 3.764 GB to ≈ 2.391 GB per decode step
([`architecture.md` §6.2](../models/marlin2b/architecture.md)) — a 36 % cut in the weight term —
but §1.3 measured decode at MBU 0.73 contributing **61 % of request latency at c=1** (1.20 s of
1.97 s) and **32 % at c=8**, where TTFT is the 68 % — corrected 2026-09-20; the sentence here
before attached the c=8 split to c=1, contradicting §1.2's own "39 % TTFT / 61 % decode" at c=1.
The 22 % saving computed below is on the c=1 request and is unaffected. `calc.`: at batch 1, ctx 2,061, the step falls
3.789 → 2.416 GB, TPOT 6 → ~3.8 ms, and a 200-token caption saves 0.44 s of a 1.97 s request —
**22 %**, for a bespoke quantisation with unvalidated timestamp accuracy. Against §2.2's 2.69 s,
this is not where to spend the week. **Verdict: not now. Revisit after the CPU work, and only with
a timestamp eval in hand.**

**(b) FP8 *KV cache*. No.** `kv_cache_dtype="fp8_e4m3"` is "supported on CUDA 11.8+"
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/quantization/quantized_kvcache.md),
so it would run. But KV is not scarce: §3.2 puts the cap at ~110 concurrent 2-minute videos against
a `max_num_seqs` of 32 and a gateway limit of 16. Halving a pool we use 15 % of buys nothing, and
the doc's own note that per-attention-head quantisation "requires the calibration pathway provided
by llm-compressor" makes even the accuracy-safe version work. **Verdict: no.**

**(c) FP8 *ViT attention*. Supported on paper, and the numbers say it would be a regression.**
vLLM's `--mm-encoder-attn-dtype fp8` explicitly lists `qwen3_5` among supported architectures — our
remapped architecture — but the measured end-to-end encoder table is the answer
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/quantization/fp8_vit_attn.md):

| resolution | BF16 median | FP8 median | speedup |
|---|---:|---:|---:|
| HD (720×1280) | 31.77 ms | 36.39 ms | **0.87×** |
| FullHD (1080×1920) | 57.99 ms | 58.73 ms | ~same |
| QHD (1440×2560) | 131.83 ms | 122.30 ms | 1.08× |
| 4K (2160×3840) | 543.44 ms | 460.31 ms | 1.18× |

"Crossover is around FullHD with 3 images/request. At QHD and above, FP8 wins" (ibid.), and
"Smaller images may see no speedup due to quantization overhead (3 quantization kernel launches +
un-padding)". **Marlin runs 448 × 448 — far below HD, on the wrong side of the crossover by two
steps.** It also requires "FlashInfer cuDNN backend with cuDNN >= 9.17.1", and the measurements are
GB200/GB300. **Verdict: no, and the reasoning is a number, not a hunch.**

**The kernel question that actually matters on Ada, and is unresolved.** Marlin has 18 Gated-DeltaNet
layers of 24. Qwen ships FlashQLA for them, with a claimed "2–3× forward speedup" over FLA Triton,
and its supported architectures are **"SM90, SM100, SM103, SM120 or SM121"**
([`architecture.md` §8.3](../models/marlin2b/architecture.md), quoting the
[FlashQLA README](https://github.com/QwenLM/FlashQLA)). **SM89 is not on that list.** If the L40S
falls back to the FLA Triton path on 18 of 24 layers, that is a 2–3× penalty on the GDN forward —
and GDN forward is *prefill*, which is the 68 % we care about. This is the most likely candidate for
the 0.62 s of §1.3's unexplained TTFT that is not video handling. ⚠️ **TO BE VERIFIED — resolve with
§1.5(c)'s torch profile before any instance decision in §5.**

### 3.7 Encoder batching and `mm-encoder-tp-mode`

`mm_encoder_tp_mode` defaults to `"weights"`; `"data"` does batch-level DP across TP ranks and vLLM
reports "improve[d] the throughput and TTFT by around 10 % for `tensor_parallel_size=8`" with
"another 40 % improvement" for encoders using "hardware-unoptimized Conv3D operations"
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/configuration/optimization.md).
**We run TP=1**, where `"data"` has nothing to split across. It is a no-op today and becomes
relevant only if a future model needs TP>1. `architecture.md` §8.5 settles parallelism for Marlin
in one line: TP1, scale by replicas.

The encoder *batching* that does apply is the greedy bin-packing inside the encoder CUDA-graph
manager (§3.5): "sorts images by output token count (smallest first) and greedily packs as many
images as possible into each sub-batch"
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/design/cuda_graphs_multimodal.md).
That only engages with `cudagraph_mm_encoder: true`, which is another reason to run experiment 4.

### 3.8 Two CPU-saving flags hiding in the multimodal config

Both are in `vllm/config/multimodal.py`
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/multimodal.py) and both
target exactly the CPU stage §2 is about:

- **`mm_device_do_normalize`** (default `True`): *"Move the do_normalize computation in the mm
  preprocessing to before the ViT, and let the device do it, so that CPU computation can be saved."*
  Already on by default in current `main`. **Verify it is on in the pinned nightly** — if the image
  we run predates it, that alone is CPU work moved off the critical path for free.
- **`mm_processor_device`** (`"auto"` by default, foldable to `"cuda"`): sets
  `mm_processor_kwargs["device"]`, running the HF multimodal processor on the accelerator instead of
  the CPU. For a workload whose bottleneck is *"tokenization, chat template rendering, and
  multi-modal data loading … on CPU"*, moving the processor to the GPU is directly on-target. ⚠️
  **TO BE VERIFIED** that the Qwen3.5 video processor path supports a CUDA device and that it does
  not fight the engine for the GPU — §7 experiment 4b.

`renderer_num_workers` (default **1**) is the third: *"Number of worker threads in the renderer
thread pool… to parallelize tokenization, chat template rendering, and multimodal preprocessing
across concurrent requests"*
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/model.py). vLLM's own
video-captioning recipe runs `--renderer-num-workers 4` alongside `--api-server-count 4`
[src](https://vllm.ai/blog/2026-09-18-pynvvideocodec). **On 4 physical cores, raise it to 2, not 4**,
and only after §2.2 has removed the duplicate work — otherwise it is more threads contending for the
same cores. On `g6e.4xlarge`, 4 is right.

`video_pruning_rate` / `video_pruning_method` ("evs" = Efficient Video Sampling, or "vidcom2")
prune a fraction of video tokens (ibid., `multimodal.py`). This would cut both ViT and prefill work
proportionally. **Do not use it on Marlin.** The model's product is second-precise timestamps;
dropping frames is dropping exactly the signal being sold, and no evaluation exists. It is also
mutually exclusive with video CUDA graphs (§3.5).

### 3.9 The one prefix-caching case that is real

`architecture.md` §5.5 names it: *"The one exception is a batch job asking several different
questions about the same video, which is exactly what `--mamba-cache-mode=align` exists for."*
That is not a hypothetical for a paid API — it is a plausible product: upload once, ask five
questions. If that shape appears in traffic, the lever is `--mamba-cache-mode=align` plus the
normalised-clip cache of §2.6(a) — the clip is already on NVMe and already hashed. It is
"currently experimental" per the vLLM Qwen3.5 recipe (ibid.), so gate it behind a flag and measure
output parity against the uncached path before exposing it.

### 3.10 The concrete diff

```diff
--- a/models/marlin2b/serve.sh
+++ b/models/marlin2b/serve.sh
 exec docker run --rm --name "marlin2b-$PORT" --gpus "\"device=$GPU\"" --ipc=host \
   -p "${BIND:-127.0.0.1}:$PORT:8000" \
   -v "$WEIGHTS:/model:ro" \
+  -v "${VLLM_CACHE:-/opt/dlami/nvme/vllm-cache}:/root/.cache/vllm" \
   -e VLLM_LOGGING_LEVEL="${VLLM_LOGGING_LEVEL:-INFO}" \
   "$IMAGE" /model \
   --served-model-name marlin2b \
   --hf-overrides '{"architectures":["Qwen3_5ForConditionalGeneration"]}' \
   --max-model-len "$MAX_MODEL_LEN" \
+  --max-num-batched-tokens "${MAX_BATCHED_TOKENS:-16384}" \
+  --renderer-num-workers "${RENDERER_WORKERS:-2}" \
   --gpu-memory-utilization "${GPU_MEM:-0.90}" \
   --limit-mm-per-prompt '{"video":1,"image":4}' \
   --dtype bfloat16 \
   "$@"
```

Everything else in §3 — encoder CUDA graphs, `mm_processor_device`, NVDEC, `--api-server-count` —
stays behind an environment variable or a `"$@"` override until §7 measures it. **Two flags and a
volume mount** is the whole safe change.

---

## 4. Two replicas on one GPU? No — but turn MPS on anyway

**The question.** 4.426 GB of weights on a 48 GB card
([`architecture.md` §8.5](../models/marlin2b/architecture.md)) leaves room for ten copies. Would two
or four vLLM instances behind a local load balancer beat one?

**The answer for the language model: no.** Continuous batching already extracts the benefit that
multiple processes would be chasing. §1.3 measured MBU 0.73 at batch 1 and **5.8 % of dense BF16 at
the steady state** — the GPU is not saturated by kernels, it is *starved* by the CPU. A second
engine would contend for the same four cores, duplicate 4.4 GB of weights, duplicate the 4 GiB
activation workspace, split the KV pool, double the ~2–3-minute boot, and halve every batch that the
scheduler could otherwise have merged. **Adding processes to a CPU-bound system is the classic way
to make it slower.**

There is no sharing mechanism that changes this:

- **Time-slicing** (the default, no configuration) gives each context the whole GPU in turn with a
  context switch between. It adds switching cost and removes batching; strictly worse than one
  engine.
- **MPS** removes the context switch — "a lightweight runtime service, designed to transparently
  enable co-operative CUDA multi-process and multi-application workflows", with "improved GPU
  utilization and reduced GPU context switching"
  [src](https://docs.nvidia.com/deploy/pdf/CUDA_Multi_Process_Service_Overview.pdf) — but it still
  cannot merge two engines' batches, and it adds a real failure mode: *"A fatal GPU fault generated
  by a Volta MPS client process will be contained within the subset of GPUs shared between all
  clients… will be reported to all the clients running on the subset of GPUs in which the fatal
  fault is contained, without indicating which client generated the error. Note that it is the
  responsibility of the affected clients to exit after being informed of the fatal GPU fault."*
  (ibid.). One bad request can take down every co-tenant process on the card.
- **MIG** does not exist on Ada — it is an **Ampere-and-newer** datacenter feature (A100/A30, then
  H100 and Blackwell; corrected 2026-09-20 from "a Hopper/Blackwell datacenter feature"). The
  conclusion is unchanged: the L40S is not a MIG part.

**Decision rule.**

| condition | replicas per GPU |
|---|---|
| one model, CPU-bound (**today**) | **1**, and buy cores |
| one model, GPU-bound at `max_num_seqs` with KV to spare | still 1 — raise `max_num_seqs` first |
| one model, GPU-bound *and* `max_num_seqs` already above the point where p99 TTFT breaks the SLO | 1 engine, scale out to a second **instance** |
| two *different* models sharing a card (e.g. Marlin + a small reranker) | 2 engines under MPS with `CUDA_MPS_ACTIVE_THREAD_PERCENTAGE` partitioning — the only case where it is right |

**But turn MPS on regardless, for a different reason.** §2.3(a) requires it: vLLM's
`pynvvideocodec` backend runs decode in the API-server process and serving in the engine process,
"so multiple CUDA processes share the same GPU. Configure and start MPS before starting vLLM"
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/multimodal_inputs.md).
So the MPS decision is forced by hardware video decode, not by replica count.

If MPS goes in, take the provisioning knobs with it. `CUDA_MPS_ACTIVE_THREAD_PERCENTAGE` "sets the
portion of the available threads that can be used by the client contexts", with the documented
strategy being "uniformly partition the available threads equally to each MPS client processes
(i.e., set active thread percentage to 100 % / n)" and the documented caveat that "it could restrict
performance for clients that could occasionally make use of idle resources"
[src](https://docs.nvidia.com/deploy/pdf/CUDA_Multi_Process_Service_Overview.pdf). For a decoder
client that should never crowd out the engine, an **asymmetric** split is right — cap the API-server
client low and leave the engine at 100 % — not the uniform `100/n`. The same document notes that
from driver r610 "partial error isolation is supported when static SM partitioning is enabled",
which "trades some MPS flexibility for isolation" (ibid.); the box runs driver 595
(`models/marlin2b/README.md`), so **that isolation is not available to us today**. Factor it in:
today, MPS means a decoder fault can be reported to the engine.

Operationally MPS is a daemon (`nvidia-cuda-mps-control -d`) that must start before vLLM and outlive
it — i.e. a fourth systemd unit in `apps/infrx-api/deploy/`, ordered `Before=marlin2b-vllm.service`.
That is the real cost of §2.3(a), and it is why §2.3's decision rule tries the gateway-side ffmpeg
path first.

---

## 5. Instance alternatives

### 5.1 The candidates, with exact prices and exact core counts

Prices: AWS on-demand price sheet, us-east-1, Linux, fetched **2026-09-20**
[src](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json).
Specs: [src](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html). GPU figures: NVIDIA
product pages, dense where the page prints a sparsity footnote.

| instance | GPU | GPU mem | mem BW | dense BF16 | NVDEC | vCPU | cores | $/hr |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `g5.2xlarge` | A10G | 22 GiB usable | ⚠️ TBV | ⚠️ TBV | ⚠️ TBV | 8 | 4 | 1.2120 |
| `g5.4xlarge` | A10G | 22 GiB | ⚠️ TBV | ⚠️ TBV | ⚠️ TBV | 16 | 8 | 1.6240 |
| `g6.2xlarge` | L4 | 22 GiB | 300 GB/s | 121 TFLOPS | **4** | 8 | 4 | 0.9776 |
| `g6.4xlarge` | L4 | 22 GiB | 300 GB/s | 121 TFLOPS | 4 | 16 | 8 | 1.3232 |
| **`g6e.2xlarge`** (today) | **L40S** | 44 GiB | **864 GB/s** | **362.05** | **3** | 8 | 4 | **2.24208** |
| **`g6e.4xlarge`** | L40S | 44 GiB | 864 GB/s | 362.05 | 3 | 16 | 8 | **3.00424** |
| `g6e.8xlarge` | L40S | 44 GiB | 864 GB/s | 362.05 | 3 | 32 | 16 | 4.52856 |
| `g7e.2xlarge` | RTX PRO 6000 SE | 96 GB | 1,597 GB/s | 480 `est.` | **4** | 8 | 4 | 3.36312 |
| `p5.4xlarge` | H100 80 GB | 80 GiB | 3,350 GB/s | 989.5 | **7** | 16 | 8 | 6.88 |

L40S: 48 GB GDDR6 ECC, 864 GB/s, "BFLOAT16 Tensor Core: 362.05 | 733\*" (\* with sparsity), "FP8
Tensor Core: 733 | 1,466\*", "NVENC/NVDEC: 3x | 3x", 350 W, PCIe Gen4 x16
[src](https://www.nvidia.com/en-us/data-center/l40s/). L4: 24 GB, 300 GB/s, "485 teraFLOPs" FP8 and
"242 teraFLOPS" BF16 **with sparsity** so 242.5 / 121 dense, "2 | 4 | 4" NVENC | NVDEC | JPEG, 72 W
[src](https://www.nvidia.com/en-us/data-center/l4/); the decode matrix independently gives L4 "1"
NVDEC chip with **"4" decoders**, against the L40S's 3
[src](https://developer.nvidia.com/video-encode-and-decode-gpu-support-matrix-new). H100 and RTX PRO
6000 SE figures are the pinned ones from
[`cloud-pricing.md` §2.1](../cross-cutting/cloud-pricing.md). The two NVDEC cells left ⚠️ here were
**resolved 2026-09-20** from the decode table of the same support matrix: **H100 SXM/PCIe/NVL =
`Hopper | 4th Gen | 1 chip | 7 NVDEC/chip | 7`** and **RTX PRO 6000 Blackwell Server Edition =
`Blackwell | 6th Gen | 1 | 4 | 4`**
[src](https://developer.nvidia.com/video-encode-and-decode-gpu-support-matrix-new). ⚠️ **A10G
(`g5`) specs remain unconfirmed and are now known to be unresolvable from this source: the matrix
lists `NVIDIA A10` (Ampere, 5th-Gen NVDEC, 1 × 2 = 2 decoders) but has no `A10G` row at all**, so
the `g5` row's memory-bandwidth, TFLOPS and NVDEC cells need a different primary source.

**The 20-word summary of that table for this workload: the L4 has more video decoders than the L40S
and costs 44 % as much.**

### 5.2 Throughput per dollar, and why the usual ranking inverts

`calc.`, from the measured rates and the exact prices:

| configuration | clips/s | clips/hour | **$ / 1,000 clips** | basis |
|---|---:|---:|---:|---|
| `g6e.2xlarge`, 1080p sources, today | 1.57 | 5,652 | **$0.397** | `meas.` |
| `g6e.2xlarge`, 360p sources | 3.58 | 12,888 | **$0.174** | `meas.` |
| `g6e.4xlarge`, 1080p, if CPU scales 2× | 3.14 | 11,304 | **$0.266** | `est.` |
| `g6e.8xlarge`, 1080p, capped by the GPU-side rate | 3.58 | 12,888 | **$0.351** | `est.` |

Two readings, and the second is the important one.

1. **Source resolution is worth 2.3× on cost per clip on identical hardware.** $0.397 → $0.174. No
   GPU change delivers that. §2.2 is the highest-ROI item in this entire document.
2. **Moving up the g6e ladder beats moving across GPU families, until the CPU stops binding.** You
   cannot rank L4 against L40S against H100 on a workload that is using 5.8 % of the L40S's
   arithmetic — the ranking would be measuring CPU, and all three families ship the same
   `AMD EPYC 7R13` at the same vCPU:core ratio for the sizes in question
   [src](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html).

**So the honest statement is a decision rule, not a ranking:**

| when | move to | why |
|---|---|---|
| **now** | `g6e.4xlarge` | doubles the binding resource for +34 % price; §2.4 |
| after §2 lands and `cpu_stall_ratio` < 0.3, if the GPU is *still* under 30 % busy | **`g6.4xlarge` (L4)** | 121 dense BF16 TFLOPS is **5.8×** the 20.9 TFLOPS we actually use (`calc.` 2026-09-20: 121 / 20.9 = 5.79; the "~21×" printed here before divided 121 by the *percentage* 5.8, not by 5.8 % of 362.05 — the L4 headroom over this workload is real but is 5.8×, not 21×); 300 GB/s still gives TPOT ≈ 3.789 GB / (300 GB/s × 0.73) = **17 ms** `calc.`, i.e. a 200-token caption takes 3.4 s of decode instead of 1.2 s. **That is the trade**: 56 % cheaper per hour, ~2.8× slower decode. Right for a batch/async tier, wrong for an interactive one. |
| interactive SLO tightens (TTFT < 1 s p95, caption < 2 s) | stay on L40S, or `p5.4xlarge` | H100's 3,350 GB/s is 3.88× the L40S, so TPOT ≈ 1.5 ms `calc.` and a 200-token caption decodes in 0.3 s. At $6.88/hr it needs ≥ 3.07× the L40S throughput to break even on $/clip — plausible **only** once the CPU path is fixed, and `p5.4xlarge` has 8 physical cores, the same as `g6e.4xlarge`. |
| Marlin is joined by Qwen3.8-27B | **not this family** | 27B dense at 55.56 GB BF16 ([`serving-optimizations.md`](../cross-cutting/serving-optimizations.md)) exceeds the L4's 22 GiB and crowds a 44 GiB L40S; that is the `g7e`/`p5` conversation, and it is a different document's. |

**A capacity constraint overrides all of this.** `CLAUDE.md` records that `g6e` in us-east-1 needed
AZ retries and only `1d` had capacity, while `p4d`/`p5`/`p6-b200`/`p6-b300` are offered. `g6`
(L4) is a much higher-volume family than `g6e` and is likely easier to get — ⚠️ **TO BE VERIFIED**
by an actual `RunInstances` dry-run per AZ, which is a five-minute check and worth doing before any
migration is planned. Whatever the target, get an On-Demand Capacity Reservation for it *before*
terminating what we have.

**What is not in this table and should be:** an L40S row in
[`research/models/marlin2b/`](../models/marlin2b/README.md). The eight pair documents cover A100,
B200, B300, GB300, H100, H200, MI355X and RTX PRO 6000 — **there is no `l40s.md`**, and the
repo-wide open question 5 in [`research/scaling/README.md`](../scaling/README.md) records that
"every Qwen3.8-27B and Marlin-2B row is `confidence: estimate`… no throughput, TPOT or TTFT
measurement exists for either on any datacentre GPU". That is no longer true: rows A–D of §1.1 are
the first measurements of this model anywhere, and they are on a GPU with no pair document.
**Write `research/models/marlin2b/l40s.md` from them.**

---

## 6. Latency for interactive use

### 6.1 What the targets should be, from the measurements

| metric | today, c=1 | today, c=8 (1080p) | proposed SLO | basis |
|---|---:|---:|---|---|
| TTFT p50 | 0.77 s | 3.35 s | **< 1.0 s** | reachable once Q5 is removed and sources are 448 p (row D is 0.66 s at c=8) |
| TTFT p95 | ⚠️ not recorded | ⚠️ not recorded | **< 2.5 s** | `bench.py` computes `ttft_p95`; it is not in the README table — put it there |
| TPOT p50 | 6 ms | 8 ms | **< 10 ms** | already met; MBU 0.73 |
| full caption (200 tok) | 1.97 s | 4.93 s | **< 2.5 s p50** | TTFT 1.0 + 200 × 8 ms = 2.6 s |
| `find` / grounding | 0.4–0.8 s `meas.` | — | **< 1.0 s p95** | 12 output tokens (`notes.md` finding 6) |

The `find` path deserves its own SLO precisely because it is 12 output tokens: it is *pure TTFT*,
and every millisecond saved in §2 lands on it directly. It is also the natural interactive demo.

### 6.2 Streaming is already right; two details are not

Caddy's config already carries `flush_interval -1`
([`Caddyfile`](../../apps/infrx-api/deploy/Caddyfile)), which Caddy documents as "low-latency mode
… disables response buffering completely"
[src](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy) — without it SSE arrives in
chunks and TTFT is a lie. ⚠️ **Corrected 2026-09-20: that last clause is false for this stack.** The
same page says the option "is ignored and responses are flushed immediately to the client" when the
response carries `Content-Type: text/event-stream` — which `gateway.py`'s
`StreamingResponse(gen(), media_type="text/event-stream", …)` always sets. So `-1` buys nothing for
our SSE path. It is still worth keeping for a second documented property: `-1` "does not cancel the
request to the backend even if the client disconnects early" (ibid.) — which is *exactly* the
abandoned-stream hazard in the next bullet, and means Caddy is actively holding the upstream
generation open. `read_timeout 600s` matches the gateway's `httpx.Timeout(600, …)`. Two gaps:

- **`write_timeout` is unset** and Caddy's default for both read and write is "no timeout" (ibid.).
  A client that opens a stream and stops reading holds a gateway slot against `MAX_INFLIGHT`
  forever. Set `write_timeout 600s` explicitly so the number is visible and finite.
  ⚠️ **Corrected 2026-09-20: `write_timeout` is the wrong knob for that failure.** Both are
  *transport* (backend-facing) timeouts — "`read_timeout` is the maximum duration to wait for the
  next read from the backend… `write_timeout` is the maximum duration to wait for the next writes
  to the backend. Default: No timeout" (ibid.) — so neither bounds a client that stops reading. The
  knob that does is **`stream_timeout`**: "a duration value after which streaming requests such as
  WebSockets will be forcibly closed at the end of the timeout… Default: no timeout" (ibid.), with
  `stream_close_delay` controlling config-reload behaviour. Set `stream_timeout` (e.g. `900s`) to
  cap an abandoned stream; keep `write_timeout 600s` only as the visible backend bound.
- **Client disconnects.** `gateway.py`'s `gen()` decrements `inflight` in a `finally`, so the
  gateway's own accounting is safe — but nothing tells vLLM to abort the generation, so the GPU
  keeps producing tokens for a socket nobody is reading. `research/scaling/README.md` open question
  12 is exactly this: "Client-disconnect → engine-abort mapping is undocumented across
  vLLM/SGLang/TRT-LLM — on agentic traffic with high abandonment this is directly wasted, unmeasured
  GPU time." For a paid API it is also a **billing** question: the usage row is written from
  `log(status, first, u)` in the `finally`, with whatever `usage` arrived before the disconnect.
  ⚠️ Resolve before launch; it is a correctness issue, not an optimisation.

`stream_interval` (default 1, `scheduler.py`) buffers streamed tokens; at 6–8 ms TPOT, 1 is right —
do not raise it to save syscalls at the cost of perceived latency.

### 6.3 The `<think>` strip costs nothing and is worth checking

`gateway.py` compiles `THINK = re.compile(r"^\s*<think>(?:.*?</think>)?\s*", re.S)` once and applies
it with `count=1` only until `stripped` is set — correct and O(1) per stream. The subtle case is
already handled (`stripped = not c.lstrip().startswith("<think>") or c2 != c`). No change. Noted
here only because it sits on the first-token path, which is the path §6.1 is optimising.

### 6.4 Connection reuse, HTTP/2, TLS

Three hops, three different answers:

- **Client → Caddy.** Caddy's automatic HTTPS gives HTTP/2 to the client. For an SSE workload with
  one stream per request, HTTP/2's multiplexing is worth little; **TLS session resumption and
  keep-alive are worth a lot** to a client issuing many calls. The actionable item is on the
  *client* side and belongs in the docs page (`apps/app/(console)/docs`): tell callers to reuse one
  `httpx`/`openai` client, because the Python SDK does by default and hand-rolled `requests` loops
  do not.
- **Caddy → gateway.** Loopback. `keepalive` defaults to `2m` with "no limit" on
  `keepalive_idle_conns` [src](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy).
  Leave it.
- **Gateway → vLLM.** A module-scope `httpx.AsyncClient` with `base_url=UPSTREAM` — already pooled.
  Correct. The one that is *not* pooled is the customer-URL fetch, §2.6(d).

`lb_try_duration` is worth flagging for the queueing work happening elsewhere in this directory:
Caddy will hold a client "for up to this long while the load balancer tries to find an available
upstream host", default zero (ibid.). Once there is more than one GPU box, that is a queue
primitive that exists for free at the proxy — mentioned here so the queue design can decide whether
to use it or bypass it, not proposed as a design.

### 6.5 Regional placement, and the only number that matters

The gateway is in us-east-1d with an Elastic IP and no CDN. For a **video** API the relevant latency
is not the control-plane RTT, it is where the *bytes* are:

- Callers who pass an **`https://` URL**: the gateway fetches it, so the latency is
  us-east-1 → wherever the clip lives. If customers store clips in S3 in another region, they pay a
  cross-region fetch on every request. **Document that clips should live in `us-east-1`**, and say
  so on the docs page — it is the highest-leverage sentence we can write for customer-perceived
  latency and it costs nothing.
- Callers who pass a **`data:` URL** (what `bench.py` does, and what `MAX_VIDEO_MB=64` allows): the
  clip travels over the customer's own uplink to us-east-1 and then through base64's 4/3 expansion.
  A 5.5 MB clip becomes **7.3 MB on the wire** `calc.`. `g6e.2xlarge`'s network baseline is
  5 Gbps burstable to 20 [src](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html), so
  ingress is not the constraint at our rate — but the customer's upstream usually is.
  **Recommend URL over base64 in the docs**, and once §2.2 lands, base64 is strictly worse (it
  blocks the event loop, §1.4).

**Do not add a second region yet.** A single-region GPU with `g6e` capacity constraints is already
the hard problem; multi-region is a queueing-and-capacity decision for
[`research/scaling/05`](../scaling/05-autoscaling-and-predictive-scaling.md), not a throughput one.
The cheap partial win is to accept an **S3 pre-signed URL or an S3 key** as an input form alongside
`video_url`, so a customer already in us-east-1 gets an intra-region fetch and we get a stable cache
key for free (§2.6(a)).

### 6.6 Warm caches, and what "warm" means here

Four caches must be warm for the SLOs in §6.1 to hold after a restart or a scale-up, and they warm
at very different speeds:

| cache | warms in | how to pre-warm |
|---|---|---|
| `torch.compile` artifacts | ~2–3 min cold, ~0 warm | mount the volume (§3.5); copy it into the AMI |
| CUDA graphs (decoder + encoder) | seconds, at capture time | nothing to do; it is part of boot |
| vLLM multimodal processor cache | per distinct clip | nothing — it is content-keyed and useless on first sight |
| gateway auth cache (60 s TTL) | first request per key | nothing; 60 s |
| **the first request with a new `mm_processor_kwargs` set** | **~18 s** `meas.` | **send one** |

That last row is from `notes.md` finding 4 — *"The first request with a new kwargs set pays ~18 s"* —
and it is a live production hazard, because `budget_kwargs()` in `gateway.py` produces a **different
kwargs dict for every distinct clip duration**: `longest_edge = frames × 200,704` where
`frames = clamp(round(2 × seconds), 4, 240)` rounded to even. That is **119 distinct kwargs sets**
across the allowed 0–120 s range `calc.`, and the first request at each new duration may pay the
18 s penalty.

⚠️ **TO BE VERIFIED and high priority:** whether that 18 s is a one-time-per-process cost per
distinct kwargs set (a processor-rebuild or a recompile), or a one-time cost overall. If it is
per-set, the first user to send a 37-second clip after a restart waits 18 seconds. Two mitigations,
both cheap: **(a)** quantise the budget — round `frames` up to a multiple of 8, 16 or 32, cutting
119 sets to **30, 15 or 8** respectively, or **(b)** pre-warm all of them at boot with a tiny
synthetic clip per bucket. Do (a) regardless; it also makes the encoder CUDA-graph budgets of §3.5
hit more often.

⚠️ **Corrected 2026-09-20, twice, in that mitigation.** (i) The set counts were off by one step:
`frames` is already even and spans 4…240, so ceiling to a multiple of **8** leaves 30 sets, **16**
leaves 15 and **32** leaves 8 (`calc.`) — the text here said "15 or 8" for 8 or 16. (ii) The
"≤ 7 % token cost" is wrong in the direction that matters. `longest_edge = frames × 200,704` is a
whole-clip pixel budget, so inflating `frames` inflates tokens roughly proportionally, and the
overhead is `ceil(f)/f − 1`: ceiling to 8 costs **+100 % at the 4-frame floor** (a 2 s clip),
+33 % at 12 frames, and only falls under 7 % from **86 frames (≈ 43 s) upward**; ceiling to 16 is
worse still. Either accept the cost on short clips — they are cheap in absolute terms, 4 → 8 frames
is ~4 KB of extra prompt — or bucket the *duration* non-uniformly (fine steps below 30 s, coarse
above) instead of rounding frames uniformly. The 18 s penalty is the reason to do it either way.

---

## 7. The benchmark plan

`models/marlin2b/bench.py` is a good closed-loop harness with four limitations that make it unable
to answer any question in this document: **one clip repeated** (so every cache hits), **one
duration**, **one input form** (base64 for local files), and **closed-loop only** (so it measures
capacity, never queueing under an arrival rate).

### 7.1 What to change in `bench.py`

Four surgical changes, roughly 40 lines:

```python
ap.add_argument("videos", nargs="+")                 # was: one positional `video`
ap.add_argument("--rate", type=float, default=0)     # >0 = open-loop Poisson arrivals
ap.add_argument("--form", choices=("b64","url"), default="b64")
ap.add_argument("--distinct", action="store_true")   # never reuse a clip within a run
```

- **Distinct clips.** Round-robin the corpus and, with `--distinct`, ensure each worker sends a clip
  no other worker has sent. This is the *only* way to separate genuine preprocessing cost from
  vLLM's processor cache, and it is `notes.md` finding 5's unresolved caveat.
- **Open-loop arrivals.** With `--rate λ`, sleep `random.expovariate(λ)` between submissions instead
  of waiting for a completion. `research/scaling/03` §7.2 names closed-loop-only testing as "the one
  mistake everyone makes": a closed-loop harness can never show a queue growing, because it stops
  offering load exactly when the system slows down. Every SLO in §6.1 is an open-loop claim.
- **Input form.** `--form url` serves the corpus from a local HTTP server so the URL path (Q2/Q5)
  is exercised; `--form b64` keeps today's behaviour. The two differ by the whole duplicate-download
  bug.
- **Record the stage timings.** Read the `Server-Timing` header (§1.5a) and put its fields in the
  `bench.jsonl` row. Without that, none of the experiments below can attribute a change.

One caveat if `vllm bench serve` is used instead: its `RandomMultiModalDataset` does support video
("Video: supported via synthetic RGB data", buckets keyed `(height, width, num_frames)` with
`num_frames > 1` meaning video)
[src](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/benchmarks/datasets/datasets.py),
but **synthetic RGB noise does not compress like real H.264** — it will exercise the ViT and the LLM
honestly and the *decoder* dishonestly, which is precisely the stage we are measuring. Use it for
GPU-side sweeps, use `bench.py` with real clips for everything else.

### 7.2 The corpus

Ten to twenty real clips, on NVMe, committed by *hash and URL* rather than by bytes:

| bucket | clips | duration | resolution | why |
|---|---:|---|---|---|
| short / low-res | 4 | 5–10 s | 360p | the row-D baseline |
| short / high-res | 4 | 5–10 s | 1080p | the row-B baseline |
| medium | 4 | 30–60 s | mixed 720p/1080p | 60 s = 120 frames, half the cap |
| at the cap | 3 | 120 s | 1080p | ~23.5 K tokens, the worst case |
| past the cap | 2 | 300 s | 1080p | tests the 240-frame clamp and the 0.4 fps quality cliff |
| pathological | 2 | 10 s | 4K, and a VFR file | the ones that page you |

### 7.3 The experiments, in order, each with a pass criterion

| # | experiment | change under test | pass criterion |
|---|---|---|---|
| **1** | **Distinct clips vs repeated clips**, c=8, short/high-res bucket | nothing — baseline | Establishes the honest baseline. If distinct-clip throughput is **> 20 % below** 1.57 clips/s, `notes.md` finding 5's caveat is confirmed and **every number in §1 is restated** before anything else ships. |
| **2** | **Dedup + transcode in the gateway** (§2.2) | `gateway.py` | TTFT p50 at c=8 on 1080p sources **≤ 1.5 s** (from 3.35 s), and throughput **≥ 2.5 clips/s** (from 1.57). Caption text unchanged on every corpus clip vs the pre-change run (exact-match on event boundaries, per `notes.md` finding 1's method). |
| **3** | **Core sweep**, on the *current* box | `taskset`/cgroup the whole stack to 2, 3, 4 cores | Throughput must be **super-linear-to-linear** in cores (≥ 0.8× slope). A flat slope means cores are *not* the constraint and §2.4's `g6e.4xlarge` recommendation is withdrawn before any money is spent. |
| **4a** | **Encoder CUDA graphs** (§3.5) | `--compilation-config '{"cudagraph_mm_encoder": true}'` | Boots on SM 8.9 at all (the matrix has no Ada column), then TTFT p50 improves **≥ 5 %** at c=1. Any output divergence = revert. |
| **4b** | **`mm_processor_device` = cuda** (§3.8) | `--mm-processor-device cuda` | `cpu_stall_ratio` falls **≥ 0.1 absolute** with no TPOT regression. |
| **5** | **Transcode quality A/B** | CRF 23 / 28 / 32 at 448 p | Event boundaries identical to the 1080p-source run on all 20 clips, and `find` timestamps within **± 0.5 s**. Pick the highest CRF that passes. |
| **6** | **`max_num_batched_tokens` sweep** (§3.1) | 8,192 / 16,384 / 32,768 | At the cap bucket (120 s clips), pick the value with the best TTFT p95 **subject to** TPOT p95 ≤ 12 ms. |
| **7** | **Open-loop SLO run** | `--rate` swept from 0.5 to 4.0 clips/s | Report the **maximum λ at which TTFT p95 < 2.5 s and no request errors**. That number, not `clips/s` at fixed `c`, is what the capacity and autoscaling work in this directory should consume. |
| **8** | **Mixed-duration run** | corpus proportions 40/30/20/10 across the buckets | No starvation: the p95 latency of the *short* bucket must not exceed **2×** its isolated p95 when 120 s clips are 10 % of traffic. This is the head-of-line-blocking test, and chunked prefill (§3.3) is what should make it pass. |
| **9** | **NVDEC** (§2.3), only if 2+3 leave `cpu_stall_ratio` > 0.5 | MPS + `pynvvideocodec` | Throughput **≥ 1.3×** experiment 2's result, with no engine faults over a 1-hour soak. |
| **10** | **One-hour soak at 0.8 × λ\*** from experiment 7 | the whole stack | Zero 5xx, zero engine restarts, `usage_failed.jsonl` empty, RSS flat, NVMe clip cache bounded. |

Record every run as a `bench.jsonl` row with the stage timings, the exact git SHA, the vLLM image
digest, and the instance ID. `serve.sh` pins `vllm/vllm-openai:nightly` by tag, which is not a pin —
**capture the digest per run** or experiments 1 and 9 will not be comparable.

### 7.4 What "success" means overall

The project-level pass criterion, stated once so the experiments can ladder to it:

> On `g6e.4xlarge`, with 1080p sources sent as URLs, sustain **λ = 3 clips/s** open-loop with
> **TTFT p95 < 2.5 s**, **full-caption p95 < 4 s**, **zero dropped requests**, and
> **$ / 1,000 clips ≤ $0.30** — `calc.`: 3 clips/s × 3600 = 10,800 clips/hr at $3.00424/hr =
> **$0.278/1k**.

That is a 1.9× throughput improvement and a 30 % cost-per-clip improvement over the measured
baseline, from CPU-side work alone, with the GPU still mostly idle and available for the next model.

---

## Implications for our system

In order. Each line names the file that changes and what evidence gates it.

1. **Instrument before optimising.** Add stage timers and a `Server-Timing` header to
   `apps/infrx-api/gateway.py` (§1.5a), scrape vLLM's `/metrics` locally, and add the
   `marlin:cpu_stall_ratio` recording rule (§1.5b). Everything below is unverifiable without it.
   *No prerequisite.*
2. **Rerun the benchmark with distinct clips** (§7 experiment 1) and restate §1 if it moves.
   `models/marlin2b/bench.py`, `models/marlin2b/results/notes.md`. *Blocks trusting any number in
   this document.*
3. **Stop downloading every clip twice.** `apps/infrx-api/gateway.py`: keep the bytes
   `video_seconds()` already fetched, transcode once to 2 fps / 448 px with `ffmpeg -threads 2`, and
   pass the result inline with a `uuid` cache key (§2.2). Move base64-decode and ffmpeg into a
   bounded `ThreadPoolExecutor` sized to cores − 1. **This is the largest single win available**; the
   measured gap it attacks is 2.69 s of TTFT.
4. **Add the content-addressed clip cache on NVMe, keyed `(org_id, sha256)`, and set
   `cache_salt = org_id` on every upstream request** (§2.6a). The salt is not an optimisation — it
   is the thing that stops one tenant's cache from serving or timing-leaking to another.
5. **Two flags and a volume in `models/marlin2b/serve.sh`** (§3.10):
   `--max-num-batched-tokens 16384`, `--renderer-num-workers 2`, and mount
   `/opt/dlami/nvme/vllm-cache:/root/.cache/vllm` so restarts stop recompiling.
6. **Set `write_timeout 600s` in `apps/infrx-api/deploy/Caddyfile`** and resolve the
   client-disconnect → engine-abort → billing question (§6.2). Correctness, not throughput.
7. **Quantise the video budget** in `gateway.py`'s `budget_kwargs()` — round frames up to a multiple
   of 8 — collapsing 119 distinct `mm_processor_kwargs` sets to **30** (corrected 2026-09-20; 15
   needs a multiple of 16) and bounding the measured 18 s first-request penalty (§6.6). Note the
   token cost is up to +100 % on the shortest clips, not the ≤ 7 % §6.6 claimed — see §6.6.
8. **Run the core sweep (experiment 3), then move to `g6e.4xlarge`** if the slope justifies it
   (§2.4). Reserve capacity before terminating; `g6e` in us-east-1 is scarce. Update the dev-box
   section of `models/marlin2b/README.md` and `CLAUDE.md`.
9. **Try encoder CUDA graphs and `mm_processor_device=cuda`** behind env vars (experiments 4a/4b).
   `Qwen3_5ForConditionalGeneration` is listed as supporting video encoder graphs; Ada is not listed
   as a tested platform, so this is an experiment with a revert path.
10. **Only then consider NVDEC**, which forces MPS, a fourth systemd unit in
    `apps/infrx-api/deploy/`, and a fault-isolation regression on driver 595 (§2.3, §4).
11. **Write `research/models/marlin2b/l40s.md`.** Rows A–D are the first measured Marlin-2B numbers
    on any GPU and they sit on a card with no pair document; repo open question 5 in
    `research/scaling/README.md` should be partially closed by them.
12. **Do not** build a transcode fleet (§2.5), a disaggregated encoder (§2.5), FP8 weights (§3.6a),
    FP8 KV (§3.6b), FP8 ViT attention (§3.6c), video token pruning (§3.8), or a second vLLM replica
    per GPU (§4). Each is argued against with a number, and each becomes right at a stated
    condition.

---

## Open questions

⚠️ Consolidated. Each is load-bearing for at least one recommendation above.

1. **Are §1's numbers cache-inflated?** Every measured row reused one clip; vLLM's processor cache
   (4 GiB default) and multimodal-aware prefix-cache hashing may have absorbed decode and preprocess
   cost. §7 experiment 1. *Gates §1.2, §1.3, §2.4, §5.2 and the whole cost model.*
2. **Does the Gated-DeltaNet prefill path fall back to FLA Triton on SM 8.9?** FlashQLA lists
   "SM90, SM100, SM103, SM120 or SM121" and not SM89, with a claimed 2–3× forward speedup over
   Triton, on **18 of Marlin's 24 layers**. This is the leading candidate for the ~0.62 s of TTFT
   at c=1 that §1.3 cannot attribute to video handling. Resolve with `--profiler-config` (§1.5c).
   *Gates §5's instance rule and any TTFT SLO.*
3. **Is the measured 18 s first-request penalty per distinct `mm_processor_kwargs` set, or
   once per process?** `budget_kwargs()` generates 119 distinct sets across the allowed duration
   range. If per-set, it is a user-visible production hazard today. *Gates implication 7.*
4. **Do encoder CUDA graphs work on Ada at all?** vLLM's Model × Hardware matrix for
   `cudagraph_mm_encoder` has NV Blackwell, NV Ampere and AMD columns — no Ada — and names Blackwell
   and MI350X as tested. *Gates experiment 4a.*
5. **Does `mm_processor_device=cuda` support the Qwen3.5 video processor path, and does it contend
   with the engine?** The flag is documented as a device override with no per-model support matrix.
   *Gates experiment 4b.*
6. **What is the L40S's concurrent NVDEC *session* limit?** The support matrix gives 3 decoder
   engines; the max-concurrent-sessions column was not extracted, and NVIDIA's historical session
   caps are encode-side and product-tier-dependent. *Gates sizing `hw_decoders` above 2 (§2.3).*
   **Partially resolved 2026-09-20:** the matrix's **decode** tables have **no** max-concurrent-
   sessions column at all (their header runs `BOARD | FAMILY | NVDEC Generation | # OF CHIPS | # OF
   NVDEC/CHIP | Total # of NVDEC |` then straight into codecs); the "Max # of concurrent sessions"
   column exists only on the **encode** tables, where L40 and L40S both read `Unrestricted`
   [src](https://developer.nvidia.com/video-encode-and-decode-gpu-support-matrix-new). So NVIDIA
   publishes no decode-session cap for this part in this document — ⚠️ the question is now "is
   there a cap anywhere else (Video Codec SDK docs, driver release notes)", and until that is
   answered `hw_decoders` above 2 is still unsized.
7. **Is CRF 28 at 448 p quality-neutral for timestamp accuracy?** No published evaluation of any
   quantised or re-encoded Marlin input exists (`architecture.md` §9 says so for weights; nothing
   exists for inputs). *Gates §2.2's transcode parameters — experiment 5.*
8. **Does CPU throughput scale linearly with cores on this stack?** §2.4's `g6e.4xlarge`
   recommendation and the $0.266/1k figure both assume it. Testable for free with `taskset` before
   any instance is launched. *Gates implication 8.*
9. **What happens to an in-flight vLLM generation when the client disconnects?** Undocumented
   across engines (`research/scaling/README.md` OQ 12) and, for a paid API, also a billing question:
   the usage row is written from whatever `usage` arrived before the disconnect. *Gates launch, not
   just throughput.*
10. **Is `g6` (L4) capacity in us-east-1 easier to get than `g6e`?** The whole batch-tier argument in
    §5.2 assumes we can actually launch the instance. A per-AZ `RunInstances` dry-run settles it in
    five minutes.
11. **What is the same-region S3 ↔ EC2 data-transfer rate?** The section exists on AWS's EC2 pricing
    page but the rate was not extracted in this pass. *Gates §2.5's transcode-fleet sizing and §6.5's
    pre-signed-URL input form.*
12. **A10G (`g5`) specs, and NVDEC counts for H100 and RTX PRO 6000 SE**, were not confirmed here;
    the corresponding cells in §5.1 are ⚠️. *Gates completing that table.*
13. **Which preprocessing path was Marlin trained on?** `architecture.md` §6.3 records two paths
    disagreeing by 1.914× (`qwen-vl-utils` 23,520 tokens vs the HF processor's 12,288), calls the
    HF path "a silent quality regression, not an error", and says the gated files cannot settle it.
    Our gateway implements the 23,520-token path via `size.longest_edge`; `notes.md` finding 1
    reports event-level parity with the vendor helper, which is evidence but not proof. *Gates
    nothing in this document directly, but it sets the token budget every number here is scaled by.*

14. **Did rows A–D go through `gateway.py` at all?** `bench.py` defaults to
    `http://localhost:8000/v1`, which is vLLM, not the gateway on `:8001`, and nothing records an
    override (§1.1, second caveat). If they did not, §1.4's Q1–Q5 are unmeasured, the 2.69 s gap is
    a pure in-vLLM video-handling cost, and the dedup half of §2.2 has no measured size. *Gates
    §1.4, §2.2's headline, §7 experiment 2's setup, and implication 3's "largest single win"
    ranking.*
15. **Where is `models/marlin2b/results/bench.jsonl`?** `notes.md` cites it and §7.3 extends it, but
    it is absent from the tree and not ignored. Until it is committed, rows A–D cannot be audited
    or recut. *Gates reproducibility of everything in §1.*

---

## Sources

### Primary — vLLM (all fetched 2026-09-20)

- Scheduler config, the `max_num_batched_tokens` / encoder-budget coupling, `max_num_queued_reqs`,
  `max_num_queued_tokens`, `async_scheduling`, `stream_interval`, the compile-cache hash inputs —
  [`vllm/config/scheduler.py`](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/scheduler.py)
- Multimodal config: `mm_processor_cache_gb`, `mm_processor_cache_type`, `mm_device_do_normalize`,
  `mm_processor_device`, `mm_encoder_tp_mode`, `mm_encoder_attn_dtype`, `mm_ipc_gpu_memory_gb`,
  `video_pruning_rate`/`_method`, `mm_tensor_ipc`, `media_io_kwargs` —
  [`vllm/config/multimodal.py`](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/multimodal.py)
- `renderer_num_workers` semantics —
  [`vllm/config/model.py`](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/model.py);
  CLI wiring — [`vllm/engine/arg_utils.py`](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/engine/arg_utils.py)
- Optimization and tuning: optimization levels `-O0`–`-O3`, chunked prefill and
  `max_num_batched_tokens` guidance, preemption, batch-level DP for encoders, API-server scale-out
  and `VLLM_MEDIA_LOADING_THREAD_COUNT`, multimodal processor/IPC caching and the placement table,
  **CPU resources for GPU deployments** (`2 + N` processes, `2 × (2 + N)` vCPUs), `--kv-cache-memory`,
  `VLLM_FORCE_AOT_LOAD` —
  [`docs/configuration/optimization.md`](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/configuration/optimization.md)
- Video backends (`opencv` / `torchcodec` / `pynvvideocodec` / `deepstream`), `num_ffmpeg_threads`
  and the FFmpeg default `min(cpu_count + 1, 16)`, `hw_decoders`, the **MPS requirement** for
  NVDEC, `--mm-ipc-gpu-memory-gb`, the optional `uuid` cache key, HTTP fetch timeouts —
  [`docs/features/multimodal_inputs.md`](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/multimodal_inputs.md)
- Prometheus metric names and the ITL-vs-TPOT distinction —
  [`docs/design/metrics.md`](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/design/metrics.md)
- `torch.compile` cache directory and invalidation, CUDA-graph capture, `cudagraph_capture_sizes` —
  [`docs/design/torch_compile.md`](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/design/torch_compile.md)
- `CUDAGraphMode` modes and the `FULL_AND_PIECEWISE` default —
  [`docs/design/cuda_graphs.md`](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/design/cuda_graphs.md)
- Vision-encoder CUDA graphs: motivation, the Model × Feature matrix listing
  `Qwen3_5ForConditionalGeneration` ✅ image / ✅ video, the Model × Hardware matrix (no Ada column),
  `cudagraph_mm_encoder`, budget generation, greedy packing, video support, the EVS/VidCom2
  exclusion —
  [`docs/design/cuda_graphs_multimodal.md`](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/design/cuda_graphs_multimodal.md)
- Prefix caching with multimodal inputs (image-hash "extra hash") and `cache_salt` —
  [`docs/design/prefix_caching.md`](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/design/prefix_caching.md)
- Quantization support matrix (llm-compressor FP8 W8A8 ✅ on Ada; Ada = SM 8.9) —
  [`docs/features/quantization/README.md`](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/quantization/README.md)
- FP8 ViT encoder attention: `qwen3_5` support, the resolution crossover table, the accuracy table,
  the calibrate-once workflow —
  [`docs/features/quantization/fp8_vit_attn.md`](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/quantization/fp8_vit_attn.md)
- FP8 KV cache: `fp8_e4m3` / `fp8_e5m2`, per-head calibration via llm-compressor,
  `--kv-cache-dtype-skip-layers` —
  [`docs/features/quantization/quantized_kvcache.md`](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/quantization/quantized_kvcache.md)
- Disaggregated encoder: the three benefits verbatim, `ExampleConnector` / `NixlConnector`, `E→PD`
  and `E→P→D` —
  [`docs/features/disagg_encoder.md`](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/disagg_encoder.md)
- Profiling: `--profiler-config`, `vllm bench serve --profile`, the "never in production" warning —
  [`docs/contributing/profiling.md`](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/contributing/profiling.md)
- `RandomMultiModalDataset` video support and `bucket_config` semantics —
  [`vllm/benchmarks/datasets/datasets.py`](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/benchmarks/datasets/datasets.py)
- GPU video decoding launch post: ">2× throughput at 8×H100", "maxing out CPU cores even with just
  2 or 4 GPUs", the OpenCV+FFMPEG baseline, the full serve command —
  [vllm.ai/blog/2026-09-18-pynvvideocodec](https://vllm.ai/blog/2026-09-18-pynvvideocodec)

### Primary — NVIDIA

- L40S specifications: 48 GB GDDR6 ECC, 864 GB/s, BF16 "362.05 | 733\*", FP8 "733 | 1,466\*",
  "NVENC/NVDEC 3x | 3x", 350 W, PCIe Gen4 x16 —
  [nvidia.com/en-us/data-center/l40s](https://www.nvidia.com/en-us/data-center/l40s/)
- L4 specifications: 24 GB, 300 GB/s, FP8 485 / BF16 242 TFLOPS with sparsity, "2 | 4 | 4"
  NVENC/NVDEC/JPEG, 72 W — [nvidia.com/en-us/data-center/l4](https://www.nvidia.com/en-us/data-center/l4/)
- Video encode/decode GPU support matrix: L40S "1" NVDEC chip / "3" decoders, L4 "1" / "4", codec
  coverage incl. AV1 —
  [developer.nvidia.com/video-encode-and-decode-gpu-support-matrix-new](https://developer.nvidia.com/video-encode-and-decode-gpu-support-matrix-new)
- Multi-Process Service: definition, memory protection and error containment, execution-resource
  provisioning, `CUDA_MPS_ACTIVE_THREAD_PERCENTAGE` and the `100 %/n` strategy,
  `CUDA_MPS_PINNED_DEVICE_MEM_LIMIT`, static SM partitioning and partial error isolation from
  driver r610 —
  [CUDA Multi-Process Service Overview (PDF), Release 615](https://docs.nvidia.com/deploy/pdf/CUDA_Multi_Process_Service_Overview.pdf)
- FFmpeg with NVIDIA GPUs: `-hwaccel cuda`, `-hwaccel_output_format cuda`, `scale_npp` / `scale_cuda`,
  parallel sessions, `CUDA_DEVICE_MAX_CONNECTIONS` —
  [docs.nvidia.com/video-technologies/video-codec-sdk/13.0/ffmpeg-with-nvidia-gpu](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.0/ffmpeg-with-nvidia-gpu/index.html)

### Primary — AWS and other

- EC2 on-demand price sheet, us-east-1, Linux, fetched 2026-09-20 (exact `g5`/`g6`/`g6e`/`g7e`/`p5`
  hourly prices) —
  [b0.p.awsstatic.com … US East (N. Virginia)/Linux/index.json](https://b0.p.awsstatic.com/pricing/2.0/meteredUnitMaps/ec2/USD/current/ec2-ondemand-without-sec-sel/US%20East%20(N.%20Virginia)/Linux/index.json)
- EC2 accelerated-computing instance specifications: physical cores, threads per core, CPU model,
  GPU count and memory, network and EBS baselines, instance-store NVMe —
  [docs.aws.amazon.com/ec2/latest/instancetypes/ac.html](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html)
- EC2 on-demand pricing page (data-transfer sections; 100 GB/month free egress) —
  [aws.amazon.com/ec2/pricing/on-demand](https://aws.amazon.com/ec2/pricing/on-demand/)
- Caddy `reverse_proxy`: `flush_interval -1` as low-latency mode, transport timeouts and their
  defaults, `keepalive 2m`, `versions` default `1.1 2`, `least_conn`, `lb_try_duration` —
  [caddyserver.com/docs/caddyfile/directives/reverse_proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)
- `Server-Timing` header — [W3C Server Timing](https://www.w3.org/TR/server-timing/)
- FlashQLA supported architectures ("SM90, SM100, SM103, SM120 or SM121") and the 2–3× claim —
  [github.com/QwenLM/FlashQLA](https://github.com/QwenLM/FlashQLA), quoted via
  [`architecture.md` §8.3](../models/marlin2b/architecture.md)

### This repository

- [`models/marlin2b/results/notes.md`](../../models/marlin2b/results/notes.md) — the eight findings
  and the four measured rows; the 18 s first-kwargs penalty; the 1080p-vs-360p result
- [`models/marlin2b/README.md`](../../models/marlin2b/README.md) — the results table, the video
  budget, the "downloads the source twice" note
- [`models/marlin2b/serve.sh`](../../models/marlin2b/serve.sh),
  [`models/marlin2b/bench.py`](../../models/marlin2b/bench.py)
- [`apps/infrx-api/gateway.py`](../../apps/infrx-api/gateway.py),
  [`apps/infrx-api/README.md`](../../apps/infrx-api/README.md),
  [`apps/infrx-api/deploy/`](../../apps/infrx-api/deploy/) (units, `Caddyfile`, `install.sh`)
- [`apps/README.md`](../../apps/README.md) — console/gateway spec, `usage_events` schema
- [`research/models/marlin2b/architecture.md`](../models/marlin2b/architecture.md) — §5.5 prefix
  caching, §6.1 FLOPs/token, §6.2 decode bytes, §6.3 video token budget, §6.4 ViT FLOPs/frame,
  §6.5 decode cost, §8.3 attention/GDN kernels, §8.5 sizing, §9 quantised variants
- [`research/models/marlin2b/README.md`](../models/marlin2b/README.md) — the eight-GPU comparison
  (no L40S row)
- [`research/cross-cutting/serving-optimizations.md`](../cross-cutting/serving-optimizations.md) —
  §6 multimodal, §7.4 Marlin-2B
- [`research/cross-cutting/cloud-pricing.md`](../cross-cutting/cloud-pricing.md) — price source of
  truth and GPU spec table
- [`research/scaling/03-concurrency-and-admission-control.md`](../scaling/03-concurrency-and-admission-control.md)
  §2.7, §6.4, §6.5, §7.2; [`05`](../scaling/05-autoscaling-and-predictive-scaling.md);
  [`06`](../scaling/06-cold-start.md); [`08`](../scaling/08-reliability-and-operations.md);
  [`10`](../scaling/10-blueprint.md); [`README`](../scaling/README.md) open questions 5 and 12

### Arithmetic run in this pass

All `calc.` figures were computed in `python3` from the cited inputs: prefill FLOPs per 10 s clip
(13.31 TFLOP) and its MFU band; decode MBU 0.731 at batch 1 and 0.574 at batch 8; the latency
reconstruction table (§1.2, closing to within 4 % of all three measured rates); the decode-batch
occupancy of 2.5 and `cpu_stall_ratio` of 0.69; aggregate GPU demand of 20.9 TFLOPS = 5.8 % of dense
BF16; the $/core-hour and $/1,000-clips tables; the FP8-weights TPOT estimate (6 → 3.8 ms); the L4
and H100 TPOT estimates (17 ms, 1.5 ms) at MBU 0.73; the 119 distinct `mm_processor_kwargs` sets;
and base64's 5.5 MB → 7.3 MB expansion.

---

## Verification log (2026-09-20)

Adversarial fact-check of this document against primary sources and against this repository's own
code and measurements. 25 claims were selected for consequence — AWS limits and prices, vLLM flags
and metric names, NVIDIA hardware figures, the arithmetic every recommendation rests on, and the
statements this document makes about `apps/infrx-api/` and `models/marlin2b/`. Several of the 25
are bundles that had to be checked cell by cell (the nine AWS prices, the twenty-odd vLLM flag
defaults, the forty-odd `calc.` figures), so they resolve into **36 verdict lines: 22 CONFIRMED,
12 CORRECTED, 2 UNVERIFIABLE**. Twelve edits were made to the body above; nothing was removed.

Method: every vLLM source was fetched as raw text from `raw.githubusercontent.com/vllm-project/vllm/main`
and grepped, not summarised; NVIDIA and AWS pages were fetched and the relevant table rows extracted
verbatim; the AWS price sheet was parsed as JSON (it is gzip-encoded despite the `.json` extension);
the MPS PDF was converted with `pdftotext`; all arithmetic was rerun in `python3`. Repository claims
were checked by reading `apps/infrx-api/gateway.py`, `apps/infrx-api/deploy/*`,
`models/marlin2b/{serve.sh,bench.py,README.md}`, `models/marlin2b/results/notes.md` and
`research/models/marlin2b/architecture.md`.

### CONFIRMED

| # | claim | verdict |
|---|---|---|
| 1 | L40S: 48 GB GDDR6 ECC, 864 GB/s, "BFLOAT16 Tensor Core TFLOPS 362.05 I 733*", "FP8 Tensor Core 733 I 1,466*", "NVENC I NVDEC 3x l 3x (includes AV1 encode and decode)", 350 W, PCIe Gen4 x16 | **CONFIRMED** verbatim — [nvidia.com/en-us/data-center/l40s](https://www.nvidia.com/en-us/data-center/l40s/). 733 / 362.05 = 2.024, so §3.6's "2.02× the BF16 rate" holds. |
| 2 | L4: 24 GB, 300 GB/s, BF16 242 teraFLOPS*, FP8 485 teraFLOPs*, "NVENC I NVDEC I JPEG decoders 2 I 4 I 4", 72 W ⇒ 121 / 242.5 dense | **CONFIRMED** verbatim — [nvidia.com/en-us/data-center/l4](https://www.nvidia.com/en-us/data-center/l4/). §5.1's "the L4 has more video decoders than the L40S" (4 vs 3) stands on both the product pages and the decode matrix. |
| 3 | Decode support matrix: L40S `1` NVDEC chip / `3` decoders; L4 `1` / `4` | **CONFIRMED** — decode table rows `NVIDIA L40S \| Ada Lovelace \| 5th Gen \| 1 \| 3 \| 3` and `NVIDIA L4 \| Ada Lovelace \| 5th Gen \| 1 \| 4 \| 4`, [support matrix](https://developer.nvidia.com/video-encode-and-decode-gpu-support-matrix-new). (The codec sub-claim on the same row **was** corrected — see C2.) |
| 4 | `g6e.2xlarge` = 8 vCPU, **4 physical cores**, 2 threads/core, AMD EPYC 7R13, 1 × L40S, 64 GiB RAM, 1 × 450 GB NVMe | **CONFIRMED** to the cell — [docs.aws.amazon.com/ec2/latest/instancetypes/ac.html](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html). The whole §2.4 ladder checks: `xlarge` 4/2/32 GiB/250 GB, `4xlarge` 16/8/128 GiB/600 GB, `8xlarge` 32/16/256 GiB/2×450 GB, `12xlarge` 48/24/384 GiB/2×1900 GB + 4 × L40S. §5.1's core counts for `g5.2/4xlarge` (A10G, 2nd-Gen EPYC 7R32), `g6.2/4xlarge` (L4), `g7e.2xlarge` (Intel Xeon Emerald Rapids, RTX PRO Server 6000, 96 GiB) and `p5.4xlarge` (1 × H100 80 GiB, 16 vCPU / 8 cores, EPYC 7R13) are all correct. |
| 5 | The nine us-east-1 Linux on-demand prices in §2.4 and §5.1 | **CONFIRMED to the cent** against the cited price sheet (fetched 2026-09-20, `last-modified: Fri, 18 Sep 2026`): g5.2xl 1.2120, g5.4xl 1.6240, g6.2xl 0.9776, g6.4xl 1.3232, g6e.xl 1.8610, g6e.2xl 2.24208, g6e.4xl 3.00424, g6e.8xl 4.52856, g6e.12xl 10.49264, g7e.2xl 3.36312, p5.4xl 6.88. Every derived cell recomputes: $/core-hr column, the $0.76216 step, "34 % price increase", all four $/1,000-clips rows ($0.397 / $0.174 / $0.266 / $0.351), "44 % as much" for the L4, "56 % cheaper per hour", "≥ 3.07× to break even" on `p5.4xlarge`, and §7.4's $0.278/1k at 3 clips/s. |
| 6 | §5.2: "all three families ship the same `AMD EPYC 7R13` at the same vCPU:core ratio" | **CONFIRMED** for the three families actually being ranked (g6 / g6e / p5), all EPYC 7R13 at 2 threads/core. (`g5` is 2nd-Gen EPYC 7R32 and `g7e` is Intel, but neither is in that comparison.) |
| 7 | vLLM's CPU rule: `2 + N` processes, `2 + N` **physical** cores, and verbatim *"If your system has hyperthreading enabled, then 1 vCPU = 1 hyperthread = 1/2 physical CPU core, so you need `2 x (2 + N)` minimum vCPUs"*, plus the two "CPU underprovisioning" quotes | **CONFIRMED** verbatim — [`docs/configuration/optimization.md`](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/configuration/optimization.md). So is the "By default, 8 CPU threads are used in each API server to load media items" warning and the `VLLM_MEDIA_LOADING_THREAD_COUNT` instruction, and the "API server scale-out disables multi-modal IPC caching … This does not impact multi-modal processor caching" note. §2.1's "3 physical cores / 6 vCPU" minimum for N=1 is right. |
| 8 | §3.1: `max_num_encoder_input_tokens = encoder_cache_size = max_num_batched_tokens`, not configurable | **CONFIRMED** — [`vllm/config/scheduler.py`](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/scheduler.py) lines 291–292, with each field carrying `# TODO (ywang96): Make this configurable.` and `NOTE: This is not currently configurable.` One nit, not corrected: the code block as printed in §3.1 places the TODO comment *after* the two assignments, whereas upstream it sits above each field declaration. Substance unaffected. The `max_num_batched_tokens` compile-hash quotes (LoRA static buffers, Inductor 32- vs 64-bit indexing) are verbatim from the same file. |
| 9 | §3.1/§3.2/§3.3 scheduler defaults and docstrings: `> 8192` throughput guidance, `enable_chunked_prefill: bool = True`, `policy: SchedulerPolicy = "fcfs"`, `stream_interval` default 1, `long_prefill_token_threshold` default 0, `disable_chunked_mm_input: bool = False`, and the `max_num_queued_reqs` / `max_num_queued_tokens` docstrings including "new requests are rejected with HTTP 503" | **CONFIRMED** verbatim. §3.2's warning about a vLLM 503 surfacing as a gateway 502 is correct against `gateway.py`'s `except Exception` handler. |
| 10 | §3.4/§3.5: `-O2` default and `-O3` "currently equal to `-O2`"; `--kv-cache-memory` and its "only valid on the same GPU with the same initial free memory"; `VLLM_FORCE_AOT_LOAD=1`; `VLLM_CACHE_ROOT` default `~/.cache/vllm` and "you can directly copy the whole `~/.cache/vllm/torch_compile_cache` directory"; `FULL_AND_PIECEWISE` as the default | **CONFIRMED** verbatim — optimization.md, [`docs/design/torch_compile.md`](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/design/torch_compile.md), [`docs/design/cuda_graphs.md`](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/design/cuda_graphs.md). The `FULL_AND_PIECEWISE` quote is truncated before "or MoEs, but also requires the most memory and takes the longest to capture" — worth knowing, since capture time is part of the cold start §3.5 is trying to cut. |
| 11 | §3.5/§3.7 encoder CUDA graphs: the motivation quote; `Qwen3_5ForConditionalGeneration` ✅ image / ✅ video; the Model × Hardware matrix having columns for **NV Blackwell, NV Ampere, AMD MI300X, AMD MI350X/MI355X and no Ada column**; tested-backends note naming Blackwell and MI350X; `cudagraph_mm_encoder` default `False`; power-of-2 auto budgets and `encoder_cudagraph_token_budgets`; the greedy bin-packing quote; "Video CUDA graphs are automatically disabled when video token pruning (EVS or VidCom2) is enabled" | **CONFIRMED** verbatim — [`docs/design/cuda_graphs_multimodal.md`](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/design/cuda_graphs_multimodal.md). The §3.5 caveat (Ada untested upstream) is exactly right and is the correct reading of an absent column. |
| 12 | §2.6(b)/§3.8 multimodal config: `mm_processor_cache_gb` default **4** GiB and the `× (api_server_count + data_parallel_size)` footprint; `mm_device_do_normalize` default `True` with its quote; `mm_processor_device` folding into `mm_processor_kwargs["device"]`; `video_pruning_rate` / `_method` ("evs" = Efficient Video Sampling); `mm_tensor_ipc` default `direct_rpc` = "msgspec serialization via RPC" (§1.4 Q8) | **CONFIRMED** — [`vllm/config/multimodal.py`](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/multimodal.py). |
| 13 | §3.8: `renderer_num_workers` default **1**, with the "parallelize tokenization, chat template rendering, and multimodal preprocessing across concurrent requests" quote | **CONFIRMED** — [`vllm/config/model.py`](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/config/model.py) line 375. Undocumented here and worth noting: `model.py` raises if `renderer_num_workers > 1` while the processor cache is on **for pooling models** — not our runner type, so §3.10's `--renderer-num-workers 2` is safe. |
| 14 | §2.2/§2.3 multimodal I/O: four video backends with `opencv` the default; "the two CPU backends are ultimately backed by FFmpeg"; `num_ffmpeg_threads` `0` (default) "relies on the FFmpeg default, which is `min(cpu_count + 1, 16)`"; `hw_decoders` default 2 and "cannot be overridden per request"; the **MPS requirement** quote; `--mm-ipc-gpu-memory-gb` and its KV-cache carve-out; the optional `uuid` field on a multimodal content part | **CONFIRMED** verbatim — [`docs/features/multimodal_inputs.md`](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/multimodal_inputs.md). Note `num_ffmpeg_threads` is documented as TorchCodec-only, which §2.2 states correctly. |
| 15 | §2.3(a)/§3.8: the vLLM launch-post quotes — ">2× throughput at 8×H100", "maxing out CPU cores even with just 2 or 4 GPUs", the OpenCV+FFMPEG baseline, the serve line with `--api-server-count 4 --renderer-num-workers 4` | **CONFIRMED** verbatim — [vllm.ai/blog/2026-09-18-pynvvideocodec](https://vllm.ai/blog/2026-09-18-pynvvideocodec) (September 18, 2026, NVIDIA NVCV Team). The §2.3 decision-rule row quoting "bottleneck on CPU utilization before 4 GPUs" matches the post's Figure 2 caption. The post's serve line also carries `--mm-processor-kwargs '{"size":{"shortest_edge":65536,"longest_edge":9437184}}'`, omitted here; it is the same whole-clip-budget mechanism `gateway.py`'s `budget_kwargs()` uses, and is the closest thing to third-party confirmation that our approach is the intended one. |
| 16 | §3.6(c): the FP8 ViT end-to-end table (31.77/36.39 = 0.87×, 57.99/58.73, 131.83/122.30 = 1.08×, 543.44/460.31 = 1.18×), the crossover sentence, the "3 quantization kernel launches + un-padding" quote, `qwen3_5` in the supported list, cuDNN ≥ 9.17.1 | **CONFIRMED** verbatim — [`docs/features/quantization/fp8_vit_attn.md`](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/features/quantization/fp8_vit_attn.md). One imprecision left uncorrected: §3.6(c) says "the measurements are GB200/GB300", but the **end-to-end** table is GB200 only (Qwen3-VL-30B-A3B, 3 images/request); GB300 appears only in the core-kernel table. The verdict (no, we are two steps below the crossover) is unaffected. |
| 17 | §1.5(b) Prometheus metric names and the ITL-vs-TPOT distinction | **CONFIRMED** — all of `vllm:time_to_first_token_seconds`, `vllm:request_queue_time_seconds`, `vllm:request_prefill_time_seconds`, `vllm:request_decode_time_seconds`, `vllm:num_requests_running`/`_waiting`, `vllm:kv_cache_usage_perc`, `vllm:prefix_cache_queries`/`_hits`, `vllm:request_time_per_output_token_seconds`, `vllm:generation_tokens_total` exist in [`docs/design/metrics.md`](https://raw.githubusercontent.com/vllm-project/vllm/main/docs/design/metrics.md), and the "(end-to-end latency - TTFT) / (number of output tokens - 1)" definition and the "Use `vllm:request_time_per_output_token_seconds` for request-level TPOT" guidance are verbatim. `bench.py` computes `(t1 - first) / max(n-1, 1)`, i.e. the request-level definition — §1.5's "compare like with like" is correct. The `marlin:decode_batch_occupancy` recording rule is dimensionally sound (tok/s × s/tok) and reproduces 310 × 0.008 = 2.48 at row B. |
| 18 | §2.6(a)/§1.4: the `cache_salt` quote and "Extra hashes … multi-modality input hashes … and cache salts"; §3.6(b) `fp8_e4m3` "Supported on CUDA 11.8+" and the per-head llm-compressor calibration note; §3.6 intro's llm-compressor FP8 (W8A8) ✅ Ada and Marlin ✅ Ada, with "Ada refers to SM 8.9"; §1.5(c) "vLLM end-users should never turn on profiling", `--profiler-config`, `vllm bench serve --profile`, `ui.perfetto.dev`; §2.5's three disaggregated-encoder benefits verbatim plus `ExampleConnector` / `NixlConnector` / `E->PD` / `E->P->D`; §4's MPS quotes (lightweight runtime service; the Volta fatal-fault containment paragraph; "uniformly partition the available threads equally … 100% / n"; "could restrict performance for clients that could occasionally make use of idle resources"; "Starting with Driver version r610, partial error isolation is supported when static SM partitioning is enabled"; "This mode trades some MPS flexibility for isolation"), against **Multi-Process Service, Release 615, Sep 09 2026**; §6.2/§6.4 Caddy defaults (`keepalive` 2m, `keepalive_idle_conns` "No limit", `versions` default `1.1 2`, `lb_try_duration` default zero, read/write timeouts "No timeout") | **CONFIRMED** verbatim, each against its cited primary source. |
| 19 | §1.4: CPython's default `ThreadPoolExecutor` is `min(32, os.cpu_count() + 4)` ⇒ **12 threads** on an 8-vCPU box | **CONFIRMED** empirically on CPython 3.12.3: a bare `ThreadPoolExecutor()` reports `_max_workers = 20` on a 16-CPU host, i.e. `cpu_count + 4`; the 8-vCPU value is 12. `asyncio`'s `run_in_executor(None, …)` constructs exactly that pool. |
| 20 | Every repository claim about `gateway.py`, the unit files and the Caddyfile | **CONFIRMED** line by line: base64-decode and `f.write(data)` on the event loop; `--workers 1` in `marlin2b-gateway.service`; `probe_seconds` via `run_in_executor(None, …)`; `os.unlink` in the `finally`; `body["messages"]` still carrying the original `video_url`; a fresh `httpx.AsyncClient` per fetch inside `video_seconds`; the module-scope pooled `client`/`sb`; `MAX_INFLIGHT = 16`, `MAX_VIDEO_MB = 64`, `MAX_VIDEO_SECONDS = 120`; `inflight` decremented in `gen()`'s `finally`; the `THINK` regex and the `stripped = not c.lstrip().startswith("<think>") or c2 != c` guard; `Caddyfile` carrying `flush_interval -1` and `read_timeout 600s` and no write-side timeout; `serve.sh` mounting no cache volume; `--max-num-seqs 32` appended by the unit file. §6.6's **119 distinct `mm_processor_kwargs` sets** recomputes exactly from `budget_kwargs()` over 0–120 s (even frame counts 4…240). |
| 21 | Every figure this document imports from `research/models/marlin2b/architecture.md` | **CONFIRMED** against that file: 3.764 GFLOP/token (§6.1), `24,576 · T²` (§6.1), 272.2 GFLOP/frame (§6.4), 12,288 B/token KV (§5.2), 19,537,920 B = 18.63 MiB GDN state per sequence (§5.3), 3,763,650,176 B = 3.764 GB weights read per decode step and ≈ 2.391 GB at FP8 (§6.2), 4.426 GB BF16 resident (§4), 0.287 GiB per 2-minute sequence and **205** concurrent on an 80 GB H100 (§8.5), MBU 0.75 for H100/H200/A100 (§6.5), ~23,560 tokens for a 2-minute clip (§6.3), the `--mamba-cache-mode=all` `NotImplementedError` and the "< 0.2 % of a 23,520-token request" prefix-cache verdict (§5.5), the "one exception is a batch job asking several different questions about the *same* video" sentence (§5.5), the "Notably absent: ❌ no NVFP4, MXFP4, AWQ or FP8 checkpoint" and "Evaluations of the quantised variants: none published" quotes (§9), and the FlashQLA `"SM90, SM100, SM103, SM120 or SM121"` list with the 2–3× claim (§8.3). §1.1's measured rows match `models/marlin2b/README.md` §Results and `results/notes.md` findings 3–7 exactly, including the 18 s first-kwargs penalty and the 0.4–0.8 s `find` latency. |
| 22 | §1.2 and §1.3 arithmetic | **CONFIRMED** (one rounding corrected, C11): 200/197/212 output tokens per clip; 1.20/1.58/1.49 s decode; 1.97/4.93/2.15 s reconstructed latency; 39 % / 68 % TTFT shares; 2.69 s gap; 5.1 and 2.2 thread-seconds and the 2.9 s difference; 5.444 + 7.758 TFLOP; 13.31 TFLOP total; 0.147/0.105/0.074 s at MFU 0.25/0.35/0.50 and the 19/14/10 % shares; the 0.62 s residual; 3.789 and 3.967 GB/step; 631.5 and 495.8 GB/s; **MBU 0.731 and 0.574**; 1,000 tok/s at 8 × 8 ms; decode batch 2.5 and `cpu_stall_ratio` 0.69; 20.9 TFLOPS = **5.77 %** of 362.05; 478 GB/s = 55 % of 864 at batch 2.5 (printed as 475, within rounding). §3.2's 110-seat L40S estimate also holds at AWS's 44 GiB reading (109) as well as at 48 GB (112). §3.6(a)'s 36 % weight cut, 2.416 GB step, 3.8 ms TPOT, 0.44 s and 22 % all recompute. §5.2's 17 ms L4 TPOT, 3.4 s caption, 1.5 ms H100 TPOT, 3.88× bandwidth ratio and 2.8× decode ratio all recompute. §6.5's 5.5 → 7.3 MB base64 expansion recomputes. §7.4's 1.9× and 30 % recompute. |

### CORRECTED

| # | § | was | is | source |
|---|---|---|---|---|
| C1 | §1.3 | attention term "5 % of prefill" | **1.3 %** of the linear prefill term, **0.8 %** of the 13.31 TFLOP total | `calc.`: 24,576 × 2,061² = 0.1044 TFLOP against 7.758 TFLOP linear |
| C2 | §2.3 | L40S decodes "H.264, HEVC, VP9 and AV1 in 8- and 10-bit" | L40S decode row is **H.264 4:2:0 8-bit only** (H.264 10-bit and all H.264 4:2:2 are `NO`); HEVC 4:2:0/4:4:4 8-10-12-bit, VP9 8-10-12-bit, AV1 8/10-bit are `YES`; HEVC 4:2:2 is `NO` | [support matrix](https://developer.nvidia.com/video-encode-and-decode-gpu-support-matrix-new), decode table |
| C3 | §2.4 | `g6e.4xlarge` "doubles EBS throughput (8,000 vs 2,000 Mbps baseline)" | **quadruples** it; 8,000/2,000 = 4×, and 8,000 Mbps baseline is above `g6e.2xlarge`'s 5,000 Mbps *burst* ceiling | [AWS instance types](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html) EBS table: `g6e.2xlarge 2000.00 / 5000.00`, `g6e.4xlarge 8000.00` |
| C4 | §3.2 | "~1,300 at the 10-second operating point" | **~770** | `calc.`: the ~1,300 omits the 19,537,920 B per-sequence GDN state, which is 44 % of a 2,061-token seat |
| C5 | §3.6(a) | decode "contributing 32 % of request latency at c=1 … while §1.2 showed TTFT is the 68 %" | **61 % at c=1**, 32 % at c=8; the 68 % TTFT share is the c=8 figure | §1.2's own table — this sentence contradicted it |
| C6 | §4 | "MIG … is a Hopper/Blackwell datacenter feature" | MIG is **Ampere-and-newer** (A100/A30, then H100, Blackwell); conclusion (L40S is not a MIG part) unchanged | NVIDIA MIG product history |
| C7 | §5.1 | `g7e.2xlarge` NVDEC "⚠️ TBV"; `p5.4xlarge` NVDEC "⚠️ TBV" | **RTX PRO 6000 Blackwell Server Edition = 4**; **H100 SXM/PCIe/NVL = 7** | decode table, same support matrix |
| C8 | §5.2 | L4's "121 dense BF16 TFLOPS is ~21× the 5.8 %-of-362 we use" | **5.8×** (121 / 20.9 TFLOPS); the 21× divided 121 by the *percentage* | `calc.` |
| C9 | §6.2 | `flush_interval -1` is what makes SSE stream — "without it SSE arrives in chunks and TTFT is a lie" | Caddy **ignores** `flush_interval` and flushes immediately when the response is `Content-Type: text/event-stream`, which `gateway.py` always sets. Keep `-1` for its *other* documented property — it "does not cancel the request to the backend even if the client disconnects early" — which is directly the §6.2 abandoned-stream hazard | [Caddy `reverse_proxy`](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy) |
| C10 | §6.2 | "Set `write_timeout 600s`" to bound a client that opens a stream and stops reading | wrong knob: `read_timeout`/`write_timeout` are **backend-transport** timeouts ("wait for the next read *from the backend*" / "next writes *to the backend*"). The client-side cull is **`stream_timeout`** (default: no timeout) | ibid. |
| C11 | §1.2 | reconstruction "closes to within 4 %" | within **4.2 %** (A 1.5 %, B 3.4 %, D 4.2 %) | `calc.` |
| C12 | §6.6 + implication 7 | round frames "to a multiple of 8 or 16, cutting 119 sets to 15 or 8 at a ≤ 7 % token cost" | ceiling to **8 → 30 sets**, **16 → 15**, **32 → 8**; and the cost is **up to +100 %** at the 4-frame floor, falling under 7 % only from ~86 frames (≈ 43 s) up | `calc.` over the 119 even frame counts 4…240 |

### UNVERIFIABLE — and both are about this repository, not about the sources

| # | claim | why |
|---|---|---|
| U1 | That the §1.1 rows A–D, and therefore §1.2's 2.69 s gap and §1.4's Q2/Q5 duplicate-download framing, describe the **gateway** path | `bench.py`'s `--base-url` defaults to `http://localhost:8000/v1`, which is vLLM; the gateway is `:8001`. No record of a `BASE_URL` override exists, and the rows' use of `--mm-kwargs auto` only has an effect when the gateway is bypassed (`gateway.py` overwrites `mm_processor_kwargs` unconditionally). Flagged inline at §1.1, §1.4 and §2.2 and raised as open question 14. This is the single highest-consequence finding of this pass: if the rows are vLLM-direct, the dedup half of §2.2 — implication 3's "largest single win available" — has no measured size at all, while the transcode half keeps the whole 2.69 s. |
| U2 | Rows A–D themselves | `models/marlin2b/results/bench.jsonl`, cited by `notes.md` as their home, is **absent from the tree** and is not gitignored. Nothing in this document's §1 can be recut or audited from the repository. Raised as open question 15. |

### Left standing deliberately

- Open question 6 is now **partially resolved**: NVIDIA's decode tables carry no max-concurrent-sessions column at all (the column exists only on the encode tables, where L40/L40S read `Unrestricted`), so the absence noted in §2.3 is a property of the source, not of the extraction. The sizing question for `hw_decoders > 2` is still open and now needs a different document.
- §5.1's A10G row stays ⚠️ and is now known to be unanswerable from the cited matrix: it lists `NVIDIA A10` (Ampere, 5th-Gen NVDEC, 2 decoders) and has **no `A10G` row**.
- §5.1 prints the L40S as "44 GiB" while §3.2 and §4 size against "48 GB". Both are defensible (48 GB decimal = 44.7 GiB; AWS reports 44 GiB usable) and the seat count is insensitive to the difference (109 vs 112 at 2-minute context), so no edit was made — but the document should pick one and say which.
- Every ⚠️ **TO BE VERIFIED** already present in §2.2 (CRF 28), §2.3 (NVDEC sessions), §2.5 (S3 rate, 40× ratio), §3.5 (Ada encoder graphs), §3.6 (FlashQLA on SM89), §3.8 (`mm_processor_device`), §5.1 (A10G), §5.2 (`g6` capacity) and §6.6 (18 s penalty scope) was re-read and none was found to be resolvable from a primary source in this pass. They stay.

### Implementation-plan amendment log — 2026-09-20

Documentation reconciliation only: incorporated the unified plan and review corrections above. Historical measurements and previous verification entries remain unchanged; new implementation/live tests are pending.
