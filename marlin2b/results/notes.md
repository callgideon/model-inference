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
6. **Reference path is slow without kernels**: 35 s per caption with the
   pure-PyTorch fallbacks for `causal_conv1d` / `flash-linear-attention`;
   `causal-conv1d` failed to build (no nvcc in the DLAMI env). Not needed
   for serving; vLLM has its own kernels.

## Cost sketch (measured throughput, on-demand g6e.2xlarge ≈ $2.24/h)

At concurrency 8: 1.57 clips/s × 10.1 s = 15.9 video-seconds per second
→ one GPU-hour processes ~57 video-hours → **≈ $0.04 per video-hour** of
dense captioning (≈ 200 output tokens per 10 s clip). Treat as an upper
bound on efficiency until the multi-clip caveat above is closed.

## Open

- Find mode through vLLM returned captions in the first runs because the
  client sent the wrong prompt; fixed to import the constants (retest).
- Distinct-clip benchmark; long clips (2 min = 240 frames ≈ 23.5K tokens).
- Same runs on B300/H200 once available (the research pair docs are
  estimates; these L40S numbers are the first measurements).
