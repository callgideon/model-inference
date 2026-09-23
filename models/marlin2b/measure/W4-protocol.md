# W4 measurement protocol — predeclared before any W4 number

Task W4 (`research/plan/18-marlin-backend-first.md` §W4, slices W4.a/W4.b). This file is the
**pre-registration** of the engine-tuning measurement: the candidates, the cache control, the
cell sequence and every criterion that decides adoption are fixed **here**, and committed
before the scripts that measure them (`candidate.sh`, `parity.py`) and the code that applies
them (`decide.py`). Git order is the proof. A criterion is never edited after a W4 result
exists; a change is an amendment appended to the log at the end, with the reason.

The one source of the numbers is `decide.py`; the table in §5 quotes its constants and
`tests/w/test_w4.py::test_engine_opt__the_protocol_states_the_criteria_decide_applies` holds
the two equal. Nothing here is a target or an SLO: where no owner target exists a row is a
**W4 engineering criterion, provisional (P-18)** (`research/workloads/marlin-sop.md` §5.2,
`models/marlin2b/results/E1B-protocol.md` §4). The only numbers in this file that are
measured are W3's, quoted from the committed sweep and labelled `meas.`.

## 1. Identity

| Axis | Value |
|---|---|
| GPU / host | the pilot box `i-0e8449a4ffca29bab`, `g6e.2xlarge`, 1× L40S 46,068 MiB (`meas.` 2026-09-23, `research/plan/evidence/w/box/inventory-20260923T0319Z.txt`) |
| Image | `vllm/vllm-openai@sha256:4cbfd34aac145fd1870381c030131c7f868fcad45448f401ecdb5fd4ed020b42`, vLLM build `a8d1aa9c99b8698a2a78b611b7a10c30e6b3995b` (`serve.sh`, `serving-version.json` `runtime_image`) |
| Model bytes | the served-bytes digests in `serving-version.json` `model` (shards, tokenizer, chat template, config, generation config) at this commit; `candidate.sh` does not re-hash them (W3's `inventory.sh` does) |
| Launch | the checkout's `serve.sh` pinned flags at `ENGINE_MAX_NUM_SEQS=32` (the sweep, not the engine, is the cap; W3 rule), media sent inline (`video_b64`), so `PROCESSING_CACHE_DIR` is unset and `--allowed-local-media-path` is absent; plus the candidate's flags from §2, appended |
| Preprocessing | profile `v1` (`marlin-sop.md` §1.5): `frames = clamp(round(2 × duration_s), 4, 240)` rounded up to even, `size = {shortest_edge 4096, longest_edge frames × 200,704}` |
| Workload | the licensed corpus `models/marlin2b/corpus/manifest.json` `--subset full` (64 clips, 2–112 s, 16 geometries), seed `20260922`, output mix `--max-tokens 128,512,1024`, `--retries 0` (bench's default), closed loop at c = 1, 2, 4, 8, 16, 32 with `n = max(64, 4c)` — W3's `measure/concurrency.sh`, E1B cell L1 |
| Parity set | §4, at c = 1, including the three 120 s `sop-synth-v1` clips (`models/marlin2b/corpus-synth/manifest.json`) |

## 2. Bounded matrix: one change against a paired baseline each

The flags are the ones verified in vLLM's source at the pinned build (§3). Nothing else is a
candidate; quantization, speculative decoding, kernels and any preprocessing change are out
(18 §W4 acceptance).

| Candidate | Flags appended to the pinned launch | Paired baseline | Runs when |
|---|---|---|---|
| **E0** | none — today's pinned flags | — | always |
| **E1** | `--max-num-batched-tokens 32768` | E0 | always |
| **E2** | none: W3's rule (§5) applied to E1's sweep by `decide.py` | — | with E1 |
| **E3** | `--max-num-batched-tokens 32768 --api-server-count 2` | E1 | only if E0 or E1 shows, at some level c ≥ 8, `peak_running < c` with the median GPU utilisation of that level below 90 % (`decide.py` prints `e3_trigger`) |

Why 32768 for E1: it is `--max-model-len`, so the encoder cache (§3) then holds any single
video item a prompt can hold at all — the value follows from a pinned setting, not from an
estimate of the worst clip. The profile's own worst case is below it: the pinned-processor
arithmetic of §6 bounds a 120 s item at 23,520 video tokens over every geometry, and the four
112 s corpus clips were refused at 20,160–21,504 (`meas.`, the engine's text in the
coordinator record). The flag changes no preprocessing input; it also sets the chunked-prefill token
budget per step (2,048 → 32,768), which can move prefill chunk boundaries and therefore
greedy output numerics — that is what the parity gate (§4) and the tail and starvation
criteria (§5) are for.

## 3. Flags checked in vLLM's source at `a8d1aa9c99b8698a2a78b611b7a10c30e6b3995b`

Read-only (a blobless fetch of that one commit from `github.com/vllm-project/vllm`). Paths
are relative to that tree.

**Where the 16384-token encoder cache comes from.** `MultiModalBudget` sizes it once at
start-up (`vllm/multimodal/encoder_budget.py:16-44, 123`): the largest per-item token count
any enabled modality can produce, from `get_mm_max_tokens_per_item`
(`vllm/model_executor/models/qwen2_vl.py:868-875`). For the image modality that is the
checkpoint's image `size.longest_edge` 16,777,216 px ÷ (16 × 2)² = **16,384**
(`qwen2_vl.py:965-1026`, `research/models/marlin2b/preprocessor_config.json`); for video it
is the processor default `longest_edge` 25,165,824 ÷ 2 (temporal patch) ÷ 1,024 = 12,288
(`qwen3_vl.py:1014-1036`, `research/models/marlin2b/processor_config.json`). So the image
item is the largest ("profiled with 1 image item", `vllm/v1/worker/gpu_model_runner.py:6518-6525`),
and `encoder_cache_size = max(scheduler.encoder_cache_size, 16384)`
(`vllm/v1/core/encoder_cache_manager.py:337-344`), where `scheduler.encoder_cache_size` is set
to `max_num_batched_tokens` (`vllm/config/scheduler.py:291-292`; the field is "not currently
configurable", `:145-149`), whose default on a GPU under 70 GiB for the API server is 2,048
(`vllm/engine/arg_utils.py:2766-2771`). The refusal the sweep hit is the input processor's
check of each placeholder's embedding count against that size
(`vllm/v1/engine/input_processor.py:63-64, 509-519`). Profile v1's per-request
`mm_processor_kwargs` never enter that start-up computation.

| Flag | `file:line` at the pin | Default here | Effect | Changes preprocessing numerics? |
|---|---|---|---|---|
| `--max-num-batched-tokens N` | `vllm/engine/arg_utils.py:1592-1597`; default `:2766-2771`; `vllm/config/scheduler.py:291-292`; `vllm/v1/core/encoder_cache_manager.py:337-344` | 2,048 (L40S, API server) | encoder cache and encoder compute budget become `max(N, 16384)`; also the per-step chunked-prefill token budget | **no** (no processor input); scheduling changes can move greedy output → parity gate. **E1's flag** |
| `--limit-mm-per-prompt '{"video": {"count", "num_frames", "width", "height"}}'` | `vllm/config/multimodal.py:38-44, 127-145`; Qwen3-VL applies them only downward: `vllm/model_executor/models/qwen3_vl.py:1137-1148, 1207-1222` (`min(...)`, "exceeds model's maximum … will be ignored") | `{"video":1,"image":4}` (served) | shrinks the profiled dummy only; **cannot raise** the budget above 16,384 | no (profiling only). Not usable |
| `--mm-processor-kwargs` (server-level) | `vllm/config/multimodal.py:163-172`; CLI `arg_utils.py:1397`; merged into every request `vllm/multimodal/processing/context.py:293-309`; read by Qwen3-VL's budget `qwen3_vl.py:1014-1036` | unset | a larger `size.longest_edge` raises the profiled video item, and so the budget | **yes**: it becomes the processor default for any request that does not send `size` — a separate serving version with a parity gate (18 "API compatibility and optimization boundaries"), not a tuning knob. Not used |
| `--mm-processor-cache-gb` / `--mm-processor-cache-type` | `vllm/config/multimodal.py:177-191`; CLI `arg_utils.py:1400-1404` | 4 GiB, `lru` | caches processed media by content hash; `0` disables | no (a hit returns the same processed item). Not used: §4's per-level restart is the cache control |
| `--api-server-count N` | `vllm/entrypoints/launchers/cli_args.py:417-424`; defaulting `vllm/entrypoints/cli/serve.py:105-127` | 1 (`data_parallel_size`) | N front-end processes; each runs media preprocessing on its own single-worker executor (`vllm/renderers/base.py:115-118, 133-136`), so N preprocessing threads | no (same processor). **E3's flag** |
| `--renderer-num-workers N` | `vllm/engine/arg_utils.py:984`; `vllm/config/model.py:375-383` | 1 | tokenization/template pool only: media preprocessing stays on the single-worker executor ("must stay single-worker", `renderers/base.py:115-118`) | no. Not used (no effect on video) |
| `--skip-mm-profiling` | `vllm/config/multimodal.py:243`; `gpu_model_runner.py:6490` | off | skips the encoder memory profile only; the budget above is computed regardless | no; removes a memory safety margin. Not used |

## 4. Cache control and the cell sequence

**A restart before every level.** W3's sweep replays the same 64 clips at every level, so
after its first level the engine had a warm multimodal processor cache and a prefix cache
holding every clip (E1B §5.1; W4's own failure oracle "identical cached prompts"). Here each
configuration runs, in order, with the engine restarted by `candidate.sh` before each block:

1. **gate** (one engine start): `capability.sh` (the cancellation probe), then `parity.py`
   at c = 1 on the parity set below;
2. **one level per engine start** for c = 1, 2, 4, 8, 16, 32: a fresh engine, then
   `concurrency.sh` with `LEVELS=c ENGINE_STATE=restarted`, so every sweep cell is labelled
   `--engine-state restarted` and starts with empty caches.

Every compared cell pair therefore has the same state (`restarted`), and `decide.py` refuses
to compare cells whose bench `profile` differs (engine state, seed, forms, output mix, clip
profile). Residual cache effect, declared: at c = 32, `n = 128` sends each of the 64 clips
twice, so up to half of that level's requests can hit the caches; `decide.py` prints the
repeat count per level. Start-to-ready is recorded for every start (OPS-RECOVER input;
I2B's `ENGINE_READY_S`).

**Parity set** (`parity.py PARITY_SET`), c = 1, `temperature 0`, `max_tokens` 256, streamed,
both EOS ids as `stop_token_ids`, the clip's manifest prompt, and profile v1's
`mm_processor_kwargs` computed from the clip's duration exactly as the worker's
`budget_kwargs` does:

| Role | Clips |
|---|---|
| short | `c039-bbb1080p30-1080-square` (2 s) |
| long | `c024-bbb1080p30-512-square` (72 s, the longest clip E0 accepted in the W3 sweep, densest geometry) |
| the four 112 s clips E0 refused | `c012-bbb1080p30-1024x768-4x3`, `c025-tos720p-2560x1080-ultrawide-rot180`, `c038-sintel1080p-480x854-portrait`, `c051-tos720p-360p-16x9` |
| the 120 s worst case (duration) | `sop09-120s-640x360`, `sop10-120s-854x480-step_spans_segment_boundary`, `sop11-120s-1280x720` |

A clip missing from the cache is a `missing` row, and its parity is `unknown`, never a pass.

## 5. Criteria (the constants of `decide.py`)

**W3's predeclared rule, verbatim** (`research/plan/evidence/w/W3-d8a7878.md`): `c*` = the
smallest level with `F(c) = 0`, `W(c) = 0` and `T(c) ≥ 0.9 × max T`; then
`ENGINE_MAX_NUM_SEQS = min(c*, floor(X))` and `WORKER_CONCURRENCY = ENGINE_MAX_NUM_SEQS`; if
no level qualifies the setting stays 8 ⚠️. `T` = accepted requests/s, `F` = failed attempts
(from the raw rows, every class), `W` = peak `vllm:num_requests_waiting` from the 2 s samples,
`X` = the engine's "Maximum concurrency for 32,768 tokens per request". A blank, missing or
unparseable sample is `unknown`, never 0; a waiting total from a sweep log without W3's
`corpus=` line (the script before `82a7dbf`, whose regex also summed
`num_requests_waiting_by_reason`) is exact only when it is 0.

| Criterion | `decide.py` constant | Value | Label and test |
|---|---|---|---|
| W3 rule: throughput fraction | `T_FRACTION` | 0.9 | W3's engineering rule (verbatim above) |
| Error rate over the sweep | `MAX_FAILURE_RATE` | 0.01 | E1B §4 "<1 % platform-caused failures", provisional (P-18): failed ÷ scheduled over every level, rejections reported and not counted |
| Sample sufficiency for a p95 | `P95_MIN_ACCEPTED` | 60 | method (E1B §3): fewer accepted samples → the p95 is `unknown` |
| Severe tail: TTFT p95 at every level ≤ c* | `MAX_TTFT_P95_S` | 30.0 | W4 engineering criterion, provisional (P-18): half the worker's `TTFT_TIMEOUT_S` 60 (R20/R29) |
| Severe tail: request latency p95 at every level ≤ c* | `MAX_LATENCY_P95_S` | 150.0 | W4 engineering criterion, provisional (P-18): half `GENERATION_TIMEOUT_S` 300 |
| Short-job class: clip duration at most | `SHORT_CLASS_MAX_S` | 9.0 | W4 engineering criterion, provisional (P-18): 25 of the 64 corpus clips |
| Short-job class: levels pooled | `SHORT_CLASS_LEVELS` | 8, 16, 32 | the levels with contention; ≥ 60 accepted short samples when every short clip is accepted |
| Short-job starvation: candidate TTFT p95 at most ratio × baseline + slack | `STARVATION_RATIO` | 1.5 | W4 engineering criterion, provisional (P-18); paired baseline required |
| Short-job starvation slack | `STARVATION_SLACK_S` | 1.0 | as above |
| Memory growth, GPU: c = 32 second-pass maximum over first-pass maximum | `MAX_GPU_GROWTH_MIB` | 256 | W4 engineering criterion, provisional (P-18); c = 32 is the only cell that repeats its clips, so the only one where growth is not first-touch allocation |
| Memory growth, host (engine container) | `MAX_HOST_GROWTH_MIB` | 512 | as above |
| Memory growth: samples needed per half | `MIN_GROWTH_SAMPLES` | 4 | fewer → `unknown` |
| Newly accepted clip: text and timestamp tokens per temporal group, at least | `OVERHEAD_PER_GROUP_MIN` | 8 | W4 engineering criterion, provisional (P-18): the W3 sweep's 20 accepted clips of ≥ 24 groups at c = 1 carry 9.181–9.808 (`meas.`, `decide.py --overheads`) |
| Newly accepted clip: … at most | `OVERHEAD_PER_GROUP_MAX` | 12 | as above |
| E3 trigger: lowest level | `E3_MIN_LEVEL` | 8 | §2 |
| E3 trigger: median GPU utilisation below (%) | `E3_MAX_GPU_UTIL` | 90 | §2 |

The **failure-oracle disqualifiers**, each a measurable test in `decide.py`:

| Disqualifier | Test | Needs |
|---|---|---|
| OOM | an engine that stops running during a cell (`candidate.sh`'s `engine_running=no`), or "out of memory" in a cell's engine error lines | the candidate run's logs; none → `unknown` |
| memory growth | the two growth rows above, GPU from `samples.tsv`, host from `candidate.sh`'s `host-mem-c32.tsv`; across cells: not applicable under a per-level restart (no state survives a cell) — the cross-time test is the soak, E1B L7, pending I2B | c = 32 samples |
| short-job starvation | the pooled short-class TTFT p95, candidate against the paired baseline | a baseline; both p95s supported |
| cancellation | `capability.sh`'s `probe=cancellation result=pass` on the candidate engine | the gate's `capability.txt` |
| usage drift | for every clip both accept (sweep and parity rows), identical `prompt_tokens` | a baseline |
| output drift | §4's parity set: clips both accept — equal `prompt_tokens` and equal content sha256, or, if the hashes differ, equal non-empty caption-event lists (the `<start-end>` spans in order: the declared fallback, `marlin-sop.md` §5.2); clips only the candidate accepts — the baseline's own refusal names the item's embedding count, that count equals the pinned processor arithmetic (`decide.py video_tokens`), and the candidate's `prompt_tokens` exceed it by the per-group overhead band above. Their event-level parity against `reference.py` is E1B's L8 cell, **pending E1B** | a baseline parity run |
| severe tail | the two tail rows above | supported p95s |
| overload masking | every bench row has `retried_requests = 0` and accepted + rejected + failed + cancelled = scheduled | the bench rows |

**Adoption.** `decide.py` prints `adopt ENGINE_MAX_NUM_SEQS=<n>` only when W3's rule yields
`c*`, `X` is known, and every row above is `pass`; any `fail` or `unknown` adopts nothing,
and the verdict names the criterion and its row. A set-aside (clips excluded from every
count) is allowed only as an explicit `--set-aside <ids>` argument and is printed with the
verdict; it is never the default. It is how the admitted workload under an admission cap
(P-20 option B) is expressed.

## 6. P-20: the decision rule

- **Option A (E1)** keeps profile v1's published 120 s capability and is preferred **only
  if** E1 passes every criterion in §5 against E0 (and E2 yields `c*`).
- **Option B** caps admission (`MAX_VIDEO_SECONDS`, read by M's `MediaProfile.pinned` and
  the legacy `media/video.py:204`): a capability change, S2M's `max_video_seconds` record
  and coordinator configuration.
- **Interim ceiling** until A is deployed: the largest duration whose worst-case video item
  fits the served budget of 16,384, from `decide.py --ceiling 16384` — the pinned processor
  arithmetic (the `smart_resize` bound of `transformers`' Qwen3-VL video processor as vLLM's
  `qwen3_vl.py:936-998` counts it) over every geometry, allowing the processor to sample up
  to two frames fewer than the budget's frame count. The arithmetic is checked against the
  W3 sweep: it reproduces the three embedding counts the engine refused (20,160 / 21,168 /
  21,504) and leaves a per-group overhead that depends on the frame count only for all 60
  accepted clips (`decide.py --overheads`).

## 7. Invalidators

E1B protocol §5 applies in full (repeated/warm cells behind a cold claim; undersampled
tails; hidden rejections; queue growth or rising memory; a changed profile, prompt set, seed
or output mix between compared cells; any accuracy claim, P-07; wall-clock phase timings; a
cost figure without P-19's ⚠️). In addition: a level whose `concurrency.sh` refused or whose
engine was not freshly started (`engine_state` not `restarted`) is not compared; a candidate
run without a `restored=` line leaves the box state unknown — the coordinator checks the unit
before anything else, and the run's numbers are still read by these rules.

## Log

- 2026-09-23: predeclared (W4 phase A, base `a237d6f`), before `candidate.sh`, `parity.py`
  and `decide.py` exist and before any W4 measurement. The W3 sweep's numbers
  (`sweep-20260923T050411Z`) informed the choice of candidates and the overhead band; no
  W4 cell has run.
- 2026-09-23, amendment 1 (review `W4-review-db12a5a.json`, before any W4 cell; no criterion
  value changed). **Correction to the entry above (HON-4):** `decide.py`'s processor
  arithmetic and `--overheads` helper were drafted and run on the W3 sweep before this file
  was committed (06:05:35Z; the `decide.py` commit `58e2659` followed at 06:11:16Z), which is
  where §2's and §5's quoted figures come from; "before `decide.py` exists" is wrong. The git
  order proves only that no W4 script was committed first and that no W4 cell existed.
  **Wording (D8, D9, HON-5, HON-6), read the body with these:** (a) §2/§6 "over every
  geometry" means every geometry with both sides ≥ 32 px (the stdlib port has no branch for
  a smaller side) and an aspect the processor accepts; (b) the 82 s interim ceiling holds
  only when the source has at least F − 2 frames at the budget's rate (about 2 fps or more;
  a source with far fewer frames gets larger frames and can exceed the budget below 82 s -
  a free `engine_error`, not a wrong answer); (c) §5's overhead row and §6: the per-group
  overhead depends on the frame count **and the prompt text** (at 4 frames the W3 sweep's
  residuals are 39-48 tokens across prompts), and the band 9.181-9.808 is over 140 accepted
  rows, 20 distinct clips of ≥ 24 groups seen at every level, not "20 clips at c = 1".
  **Decision rule, made explicit in `decide.py` (D1, D2, D5, D6, D7; §4/§5/§7 already said
  so):** a level not labelled `restarted`, or any of the six levels missing, makes W3's rule,
  the tail and the paired criteria `unknown`; raw rows that do not reconcile with the bench
  row's attempts/accepted/failed make overload masking `fail` and the error rate `unknown`,
  and unrecorded retries `unknown`; `floor(X) < 1` adopts nothing; the baseline must be the
  predeclared one (E1 against E0, E3 against E1, from each run's `candidate=` line) and never
  the candidate's own run.
- 2026-09-23, amendment 2 (confirmation `W4-confirm-b0a44a0.json`, before any W4 cell; no
  criterion value changed). Amendment 1's decision-rule sentence overstated the missing-level
  case (DEC-R2-3). As `decide.py` applies it: a level not labelled `restarted` makes W3's
  rule, the tail and the paired criteria `unknown`; a level missing from the candidate makes
  W3's rule `unknown` (so nothing is adopted); and the paired criteria are `unknown` unless
  both the candidate and the baseline have all six levels (DEC-R2-4). The tail is taken
  over the levels present at or below `c*`. Overload masking also requires the bench row's
  own `denominators.retried_requests` to be recorded and zero.
