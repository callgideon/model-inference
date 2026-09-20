# marlin2b results — notes

Measured 2026-09-19 on AWS g6e.2xlarge (1× L40S 48 GB, 8 vCPU), vLLM
`vllm/vllm-openai:nightly` pulled 2026-09-19, transformers 5.17.0 on the
reference path. Clip: `sample-10s.mp4` (10.1 s, 1920×1080). Rows in
`bench.jsonl`.

## Findings

1. **vLLM loads Marlin-2B with `--hf-overrides` and matches the vendor
   path.** Caption events are identical across transformers `.caption()`,
   vLLM at the processor default, and vLLM at the training budget:
   `<0.0-1.5>` still, `<1.5-4.5>` bus passes, `<4.5-10.1>` still. Scene
   prose differs in wording only.
2. **Neither path honours the model card's video budget by default.** The
   card says 2 fps, ≤240 frames, 200,704 px/frame (28×28 grid, 196 tokens
   per 2-frame patch). On transformers 5.17 the helper's
   `apply_chat_template` uses the processor default (`size.longest_edge`
   = 25,165,824 px for the whole clip), giving grid `[10, 52, 92]` =
   11,960 video tokens for this clip; vLLM reproduces that (12,221 prompt
   tokens). The env vars in `modeling_marlin.py` (`VIDEO_MAX_PIXELS` …) only
   affect qwen-vl-utils, which this code path does not call.
3. **`size.longest_edge` is a whole-clip pixel budget.** Setting it to
   `frames × 200,704` (frames = clamp(2 fps × duration, 4, 240)) reproduces
   the training grid: 2,061 prompt tokens here. `cap_pixels_per_frame=true`
   does not give the training grid (861 tokens) and its first call took 18 s.
   `smoke.py`/`bench.py` compute the budget from the clip duration
   (`--mm-kwargs auto`).
4. **The first request with a new kwargs set pays ~18 s**; steady-state
   TTFT is 0.77 s at concurrency 1 regardless of 2K vs 12K prompt tokens, so
   video decode/encode, not prefill, sets TTFT on this box.
5. **Throughput at concurrency 8 is the same at 2K and 12K tokens**
   (1.57 vs 1.47 clips/s), which says the bottleneck is per-request video
   handling (base64 upload, decode, vision encoder) rather than the LM.
   Caveat: every request used the same clip, so vLLM's multimodal
   processor cache may have absorbed decode cost; rerun with distinct clips
   before quoting preprocessing cost.
6. **Find mode works through vLLM** once the client sends the real
   `GROUNDING_PROMPT_TEMPLATE` (imported from `modeling_marlin.py`): "From
   1.5 to 4.5." for *a white bus drives past* on `sample-10s.mp4`, which is
   the caption's own event boundary; "From 1.5 to 3.5." for *the rabbit
   yawns* on the Big Buck Bunny intro. 12 output tokens, 0.4–0.8 s wall.
7. **Per-request video handling is the bottleneck, not the LM.** Same
   token budget (~1,930–2,061 prompt tokens), concurrency 8:
   `Big_Buck_Bunny_360_10s_1MB.mp4` (1 MB, 360p) 3.58 clips/s, TTFT p50
   0.66 s; `sample-10s.mp4` (5.5 MB, 1080p) 1.57 clips/s, TTFT p50 3.35 s.
   Decode and resize of 1080p source frames on 8 vCPUs, plus the base64
   upload, cost more than 2K tokens of prefill on the L40S. Pre-transcoding
   inputs to ≤480p (the model never sees more than 448×448 anyway) is the
   first optimization to try; the second is more vCPUs per GPU.
8. **Reference path is slow without kernels**: 35 s per caption with the
   pure-PyTorch fallbacks for `causal_conv1d` / `flash-linear-attention`;
   `causal-conv1d` failed to build (no nvcc in the DLAMI env). Not needed
   for serving; vLLM has its own kernels.

## Cost sketch (measured throughput, on-demand g6e.2xlarge ≈ $2.24/h)

At concurrency 8: 1.57 clips/s (1080p source) to 3.58 clips/s (360p source)
× 10.1 s = 15.9–36.2 video-seconds per second. Video-seconds per second is
already video-hours per GPU-hour (both sides scale by 3,600), so one GPU-hour
processes **15.9–36.2 video-hours** → at ≈ $2.24/h, **$0.14 (1080p) to $0.06
(360p) per video-hour** of dense captioning (~200 output tokens per 10 s clip).
Two distinct clips only; the vLLM multimodal cache makes repeated identical
clips optimistic on decode cost.

> **Correction 2026-09-20.** This sketch previously read *"16–36
> video-seconds per second → one GPU-hour processes 57–129 video-hours →
> $0.02–0.04 per video-hour"*: the conversion divided by 1,000 instead of
> 3,600, so the throughput was 3.6× too high and the price 3.6× too cheap.
> The corrected figures above are the ones to quote
> ([`research/production-api/01-requirements-and-traffic-model.md`](../../../research/production-api/01-requirements-and-traffic-model.md) §3.8).

## Open

- Long clips (2 min = 240 frames ≈ 23.5K tokens) and clips of mixed
  lengths in one benchmark.
- Same runs on B300/H200 once available (the research pair docs are
  estimates; these L40S numbers are the first measurements).

## E1 tooling (2026-09-20, no new measurements)

Task E1 replaced the single-clip benchmark client and added a distinct-clip
corpus; **no GPU run was made**, so every row above still stands as the only
measured data. Two things change how future rows are produced:

1. **Distinct corpus.** [`../corpus/`](../corpus/README.md) pins 64 licensed,
   non-overlapping clips (plus a 32-clip fast subset and four corrupt-media
   fixtures) with real sha256 hashes, 14 geometries (corrected to 16 below),
   2–112 s durations and one of
   16 prompts each. This is the answer to finding 5's caveat: reruns with
   `--corpus` cannot be absorbed by the multimodal cache the way two repeated
   clips were.
2. **Honest percentiles.** `bench.py` now refuses a percentile the sample count
   cannot support (a reported pN needs >= 3 accepted samples beyond it: p50>=6,
   p95>=60, p99>=300). The four historical rows above report `ttft_p95` and
   `latency_p95` from 5–32 samples; under the new rule those would be suppressed.
   Read them as p50-grade evidence with a tail hint, not as p95 SLOs.

New summary rows also carry accepted/rejected/failed denominators, cold vs warm
TTFT, request form, seed and arrival rate; per-attempt detail goes to a raw JSONL
beside the summary. Legacy CLI flags and the default `--out` path are unchanged,
so this file's history stays comparable.

### E1 review round 1 (2026-09-20, commit `a1cb4f4`, still no measurements)

An independent review found the corpus misdescribed itself and the client could
leak. Corrections to the paragraph above, so nothing here stays wrong:

- The corpus has **16** probed geometries, not 14 (the entry above said 14 while
  rotation had collapsed two geometries onto ones already present).
- Rotation had been applied to all four clips of each rotated geometry, so no real
  1080×1920 portrait and no real 480×1920 (1:4) clip existed despite the ids. Twelve
  clips were rebuilt with the pinned ffmpeg; the corpus now spans aspect 0.25–4.0.
- The frame-rate set is **10/15/24/30/60**; an earlier "5 fps" claim was false.
- `source_upscaled` now flags all **28** upscaled clips (it compared one axis).
- Reading new summary rows: `distinct_clips` counts only clips whose media was
  actually sent (`text` slots send none), `denominators` now also carries
  attempt-level rejected/failed counts so a retry cannot hide a 429, `latency_s`
  percentiles span every attempt of a request, and an open-loop run additionally
  reports `latency_from_scheduled_s` and `schedule_lag_s` (coordinated omission).
  A 200 whose stream ends without `[DONE]`, `finish_reason` or usage is now
  `failed`/`truncated_stream`, not accepted.
