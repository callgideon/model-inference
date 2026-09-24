# LONGCLIP — shelved 2026-09-24 (the cap stays 82 s)

**Status: shelved before any code change.** The coordinator stopped the lane at 18:59Z
after the user decided to keep `MAX_VIDEO_SECONDS` at 82 s for now. Branch `codex/longclip`
from `4db74b6`. This note is the only file. None of deliverables 1–7 exists: no candidate
file, no `--set` value set, no Caddyfile change, no long-clip corpus, no measurement script,
no P-20/P-23 entries, no tests and no mutants. No box, AWS or hosted contact. No containers
were created. The vLLM source below was read read-only at
`a8d1aa9c99b8698a2a78b611b7a10c30e6b3995b` (the codeload tarball; line numbers are that
commit's). Nothing below was measured.

## Findings from reading, recorded so a restart does not repeat the work

1. **The engine flag is W4's E1, unchanged.** `--max-num-batched-tokens 32768`
   (`measure/candidate.sh` `e1`) sets `encoder_cache_size = max(max_num_batched_tokens,
   max_tokens_per_mm_item)` (`vllm/config/scheduler.py:291-292`,
   `vllm/v1/core/encoder_cache_manager.py:336-342`). The default is 2,048 on a GPU under
   70 GiB (`vllm/engine/arg_utils.py:2766-2771`), so the budget today is the 16,384-token image
   item. Profile v1 bounds any clip, 1200 s included, at 23,520 video tokens (finding 3), so
   E1 admits it. W4 phase B measured E1 at clips up to 120 s and did not adopt it:
   `w3_rule`/`memory_growth` failed on E0 and E1 alike (`W4-phaseB-20260923T2155Z.md`).
2. **`/tokenize` does not apply the encoder-cache check.** The refusal
   (`video item with N embedding tokens exceeds the pre-allocated encoder cache size`) is raised
   only on the generate path (`vllm/v1/engine/input_processor.py:509-519`).
   `create_tokenize` (`vllm/entrypoints/serve/tokenize/serving.py:57-124`) only renders and
   counts. So on today's engine a 1200 s clip **prepares** (the worker's `/tokenize` count and
   its 120..23,520 video-token check both pass). It then fails at the chat as an SSE error,
   which is the "admitted then refused" case this lane was meant to prevent. Protocol step (i)
   would need to record a `/tokenize` success and a chat refusal, not a `/tokenize` refusal.
3. **Past 120 s the engine does not sample at 0.2 fps; the loader decides.** For
   `Qwen3VLVideoProcessor` the connector selects the `qwen3_vl` loader
   (`vllm/multimodal/media/connector.py:584-591`, `vllm/multimodal/video.py:356-405`). The
   loader pre-samples `int(total/orig_fps × 2)` frames, clamped to [4, **768**], and ignores the
   worker's `mm_processor_kwargs`. If it samples fewer than all the frames it sets
   `do_sample_frames=False` (`video.py:181-194`), and the HF processor then keeps every
   pre-sampled frame. Profile v1's `max_frames` 240 is **not** applied. The pixel budget
   (`size.longest_edge` = 240 × 200,704) still caps the total, so each frame shrinks instead.
   Worked example, not measured: a 1200 s clip at 30 fps gives **768 frames (0.64 fps)**.
   At 640×360 each frame resizes to 160×320, which is 384 groups × 50 = 19,200 video tokens.
   A source at 2 fps or below has all its frames decoded (`do_sample_frames=True`), and the HF
   processor then re-samples it to 240 frames (0.2 fps). The same check reproduces W4's
   measured 21,600 for the 120 s 640×360 clip (240 frames, 320×576). Up to 384 s a clip at
   30 fps keeps 2 fps with smaller frames. P-23's framing ("1200 s → one frame per 5 s")
   holds only for sources at 2 fps or below.
4. **The engine decodes the whole clip.** The OpenCV reader `grab()`s every frame up to the
   last sampled index (`vllm/multimodal/video_decoders/opencv.py:231-279`). It holds the
   sampled frames at **source** resolution (768 × W × H × 3 bytes: about 0.53 GB at 360p and
   about 4.8 GB at 1080p per request, est.). `load_file` reads the whole file into memory
   (`vllm/multimodal/media/video.py:195-201`). This happens twice per job (`/tokenize`, then
   the chat; TOKCOST finding 3), on the single multimodal thread.
5. **Byte and time caps that a long clip would hit.**
   - The edge `request_body max_size 96MiB` and the `@oversized` matcher also cover
     `PUT /v1/uploads/{h}`, so the `infrx-upload:` form is capped at 96 MiB whatever
     `MAX_MEDIA_BYTES` says.
   - That PUT's whole body must arrive within `INTAKE_TIMEOUT_S` (30 s), and it is buffered in
     the gateway (`routes/uploads.py:128-140`, `intake.read_body`).
   - The `video_url` fetch runs in the admission request path under `MEDIA_FETCH_TIMEOUT_S`
     (20 s total) and is buffered in memory, as are M's `prepare` and the worker's cache load.
   - A sync request that sends nothing for 600 s is cut by the edge (`read_timeout 600s`).
   - `TRANSCODE_*` is read nowhere (profile v1 does not transcode).
   - Real bitrate anchor: the corpus source `bbb1080p30.mp4` is 634.6 s at 276,134,947 B, which
     is 3.48 Mbit/s, so 1200 s at that rate is about 522 MB.
6. **Lease rules.**
   - The preparation lease is already renewed by a background task every
     `PREPARATION_LEASE_TTL_S`/3 up to the phase deadline (`worker/preparation.py`), so a long
     preparation needs only a larger `PREPARATION_TIMEOUT_S`.
   - The inference heartbeat is **event-driven** (`worker/attempt.py:374-390`). Raising
     `TTFT_TIMEOUT_S` to or above `LEASE_TTL_S` (120 s) would let a long, silent prefill lose
     its lease. A long-clip value set must keep `TTFT_TIMEOUT_S` well below `LEASE_TTL_S`, or
     raise both, or add the background renewal the code's own `ponytail:` comment names.

## Not done

Everything in the brief: deliverables 1–7, the runs and the mutants. If the lane is
resumed, start from findings 2–6. The candidate stays E1. The value set must satisfy these
coupled bounds:
- the edge cap and timeout, split per route;
- `INTAKE_TIMEOUT_S` for uploads;
- `MEDIA_FETCH_TIMEOUT_S` + `PREPARATION_TIMEOUT_S` + queue + generation < the edge's
  `read_timeout` for sync requests;
- `TTFT_TIMEOUT_S` < `LEASE_TTL_S`.

The fixtures need a source above 2 fps (for example 30 fps) to exercise the 768-frame loader
path, and one at 2 fps or below for the re-sample path.

## Verification log

- 2026-09-24: Shelved at the coordinator's instruction (the user keeps 82 s). Findings are from
  reading the source only. Nothing measured, nothing run, no containers, nothing pushed.
