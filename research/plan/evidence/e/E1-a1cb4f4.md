# E1 — Distinct corpus and authenticated benchmark client (review round 1)

## Task and status

- **Task:** E1 (track E, verification). Corpus + benchmark client tooling for the
  PERF-PILOT and MEDIA-PARITY oracles. This task builds the tooling those oracles
  need; it does not run a load test and makes no performance claim.
- **Status:** **implemented, not integrated.** No live GPU, gateway, AWS, Supabase
  or `*.callbill.ai` endpoint was contacted. Every HTTP request in this report went
  to an in-process `httpx.MockTransport` fake gateway.
- **Owner/session:** Claude Opus 5 implementation session, worktree
  `.claude/worktrees/codex-e1`.
- **Supersedes:** [`E1-38b07b5.md`](E1-38b07b5.md), which is kept intact with a
  banner naming the four false statements this round fixed.

## Source

| Field | Value |
|---|---|
| Base SHA | `25b9829` (docs: reviewed parallel implementation handoffs) |
| Implementation SHA | `a1cb4f4` (review fixes) on top of `38b07b5`, `1c7d96d` |
| Documentation SHA | `f702fc0` (notes.md correction + superseded-report banner) |
| Evidence SHA | this commit (separate, follows the commits it cites) |
| Branch / worktree | `codex/e1-corpus-bench` in `.claude/worktrees/codex-e1` |
| Integrated SHA | none — coordinator integrates into `claude/infrx-impl` |

## Review findings and root-cause fixes

The four blocking findings of `/.claude/handoff/E1-review.json`, each fixed at its
root cause with a regression test that fails on the old behaviour.

| # | Finding | Root cause | Fix | Regression test |
|---|---|---|---|---|
| 1 | `source_upscaled` false for 12 of the 28 upscaled clips | flag computed as `derived.height > source.height`: one axis, and the **post**-rotation axis | `upscales(recipe, probed_source)` compares the recipe's pre-rotation target against the probed source on both axes (`force_original_aspect_ratio=increase` scales by `max(W/sw, H/sh)`); `validate_manifest` fails on any disagreement | `test_source_upscaled_is_true_for_every_upscaled_axis`, `test_validator_rejects_a_mislabelled_or_mis_flagged_clip` |
| 2 | ids/labels/README/evidence claimed coverage the media lacked | `ROTATE_AT` was keyed by `i % 16`, i.e. by **geometry**, so all four clips of geometries 4/9/14 were transposed; no 1080×1920 and no 480×1920 file existed. "5 fps" was never in `FPS` at all | rotation is keyed by clip band (`transpose_for(i)`, band `c016`–`c031`), so one clip per rotated geometry rotates and the unrotated portrait/tall clips survive — in the fast subset too; a rotated clip carries `-rot90cw`/`-rot180`/`-rot90ccw` in its id; 12 clips rebuilt, re-probed, re-hashed; the false 5 fps claim corrected to the real set | `test_clip_ids_and_labels_match_the_probed_pixels`, `test_validator_rejects_a_mislabelled_or_mis_flagged_clip` |
| 2b | no validator rule tied a label to the pixels | labels were never checked against probed geometry | `validate_manifest` now requires: `geometry_label` is a known geometry, equals the recipe scale, is the id's suffix (with the rotation suffix when transposed), probed `width`/`height` equal the recipe scale after transposition, probed `aspect` equals that geometry's aspect — plus corpus-level rules for an extreme-tall (≤0.3), an extreme-wide (≥3.0) and a low-fps (≤10) clip | same two tests |
| 3 | key reachable via `error.code`; key prefix via truncate-then-scrub; signed upload URL via httpx exception text | scrubbing was applied per field, after truncation, and `put.raise_for_status()` quotes the full signed URL | `redact(text, key)` replaces the key **and** strips every URL query string; `dump_line()` is the only way a row or summary becomes text, so each sink (raw JSONL, stdout, `bench.jsonl`, stderr) redacts the whole serialised blob; the error body is redacted before parsing **and** before truncation; `error_code` is capped at 120 chars; the upload PUT raises status-only, never the destination | `test_server_controlled_fields_cannot_leak_the_key_or_a_signed_url` (canary key + canary `X-Amz-Signature` URL), `test_api_key_never_appears_in_any_output` |
| 4 | `distinct_clips` counted `text` slots that send no media | counted `clip_id` over every final row | rows carry `media_sent`, set when a media ref actually goes out; `distinct_clips` counts those over **all attempts**, `distinct_clips_scheduled` keeps the schedule-level number | `test_distinct_clips_counts_only_clips_whose_media_was_sent` |

Nonblocking notes applied in the same commit:

| Note | Fix |
|---|---|
| retries hide rejections and shorten latency | summary carries `attempt_status_counts`, `attempt_outcomes` and `denominators.{attempts, rejected_attempts, failed_attempts, retried_requests}`; the final row of a retried request carries `request_send_s` (first attempt) and `request_latency_s` spanning every attempt and every retry wait, and the `latency_s` percentiles are computed from it |
| a 200 stream ending without a terminator counted accepted | rows carry `finish_reason` and `stream_complete`; no `[DONE]`, no `finish_reason` and no usage ⇒ `failed`/`truncated_stream` |
| `build.py plan` reset built sources to unfetched | `plan` carries source `sha256`/`bytes`/`probed`/`status` forward when the URL is unchanged; `validate` fails when a built clip's source is unpinned; `build` reports `sha256_mismatch` instead of silently re-pinning changed source bytes; `build` re-derives any clip not already `built` even when a stale file sits at its path |
| coordinated omission; base64 on the event loop | open-loop rows carry `schedule_lag_s` and `latency_from_scheduled_s`; the summary gains a `schedule_lag_s` block (with `max`) and a `percentiles.latency_from_scheduled_s` block; base64 encoding and the upload read+digest run in `asyncio.to_thread` |
| tautological open-loop test | the schedule test now asserts measured behaviour: max send lag < 0.1 s against a 0.25 s fake, `send_s ≥ scheduled_s`, `latency_from_scheduled_s ≥ latency_s` |
| real `load_corpus` untested | `test_real_load_corpus_reads_the_committed_manifest` loads the committed manifest through the real loader with a temporary `$CORPUS_CACHE` |
| NASA STEVE licence; ffmpeg tarball alias | the source's `note` and the corpus README record the Alberta Aurora Chasers third-party-photography caveat, the item page, and that `tarball_url` is the unversioned alias with the old-releases fallback |

Not applied: mirroring the ffmpeg tarball (needs an authorized S3 write — see
Handback); swapping the NASA source for NASA-produced footage (would rebuild 16
clips for a caveat that is documented and not redistributed).

## Requirement coverage

Test IDs are the planned oracles; what is demonstrated is the **client and corpus
behaviour** each needs, not the oracle itself (both need a live pilot).

| Test ID | Invariant demonstrated now | Case |
|---|---|---|
| MEDIA-PARITY | A clip's id, `geometry_label`, recipe and **probed** width/height/aspect agree; the corpus really contains 1080×1920 portrait, 480×1920 (1:4) tall and 1920×480 (4:1) wide, in the fast subset as well; rotated clips exist and name their rotation | `test_clip_ids_and_labels_match_the_probed_pixels`, `build.py validate` |
| MEDIA-PARITY | `source_upscaled` is true for exactly the clips upscaled on either axis, so upscaled media cannot be quoted as native-resolution parity evidence ("no upsize") | `test_source_upscaled_is_true_for_every_upscaled_axis` |
| MEDIA-PARITY | A mislabelled clip, a rotated clip hiding its rotation, a wrong upscale flag and a built clip from an unpinned source all fail validation | `test_validator_rejects_a_mislabelled_or_mis_flagged_clip` |
| MEDIA-PARITY | 64 built clips, 32-clip fast subset, distinct by derived sha256 **and** recipe, non-overlapping source segments, 16 geometries, 11 aspect ratios, durations 2–112 s inside the 120 s cap | `test_manifest_validates_and_has_the_required_scale`, `test_clips_are_distinct_by_content_and_by_recipe`, `test_diversity_the_perf_oracle_needs` |
| MEDIA-PARITY | Every source carries a licence, licence URL, licence-evidence URL read on the page and an attribution; an empty licence field fails validation | `test_every_source_carries_verified_licence_fields`, `test_unbuilt_clips_are_never_counted_as_built` |
| MEDIA-PARITY | Corrupt-media fixtures exist with real hashes and expected failure classes; an unbuilt clip can never be counted as built or keep derived metadata | `test_negative_fixtures_exist_for_media_failure_cases`, `test_unbuilt_clips_are_never_counted_as_built` |
| PERF-PILOT | The API key is read from the environment only, sent as `Bearer`, and cannot reach stdout, the raw JSONL, `bench.jsonl`, the summary or stderr through **any** server-controlled field — including `error.code` and an error body long enough that truncating first would leave a usable prefix; a signed upload destination never reaches output; a key on argv is refused | `test_server_controlled_fields_cannot_leak_the_key_or_a_signed_url`, `test_api_key_never_appears_in_any_output`, `test_historical_cli_still_parses_and_refuses_command_line_keys` |
| PERF-PILOT | Rejections and failures are counted apart from accepted requests at **request and attempt level**, so a retried 429 stays in the denominators; request latency includes every attempt and retry wait | `test_retried_rejections_stay_visible_and_latency_covers_every_attempt`, `test_rejections_and_failures_are_counted_apart_from_accepted` |
| PERF-PILOT | A seeded open-loop schedule is reproducible and independent of response latency, and the driver is measured to keep up (send lag < 0.1 s against a 0.25 s fake), with latency also reported from the scheduled arrival | `test_schedule_is_deterministic_and_independent_of_latency` |
| PERF-PILOT | `distinct_clips` reflects media actually sent, so a run's cold-path coverage cannot be overstated by text-form slots | `test_distinct_clips_counts_only_clips_whose_media_was_sent` |
| PERF-PILOT | A 200 whose stream ends without `[DONE]`, `finish_reason` or usage is failed/truncated, not accepted; TTFT comes from the first content delta; tokens come only from authoritative usage | `test_truncated_200_stream_is_failed_not_accepted`, `test_ttft_comes_from_first_content_delta_and_tokens_from_usage` |
| PERF-PILOT | A percentile the sample count cannot support is refused: p99 ≥300 accepted samples, p95 ≥60, p50 ≥6 | `test_percentiles_are_suppressed_when_samples_cannot_support_them` |
| PERF-PILOT | The historical CLI (positional video, `-c`, `-n`, `--max-tokens`, `--label`, `--out` default, `MM_KWARGS`) still parses and still sends `mm_processor_kwargs` on the direct target only | `test_historical_cli_still_parses_and_refuses_command_line_keys` |
| F-BASE | The existing `apps/infrx-api/tests` baseline still passes on this branch (23 cases) — the item this report's predecessor had to mark "not run" | command table below |

Not covered by this task: any server-side behaviour. MEDIA-PARITY's frame/token
budget and pixel-area assertions and PERF-PILOT's latency distributions require
M3/G4 and an allocated GPU (E4).

## Environment

| Item | Value |
|---|---|
| Host | Ubuntu 24.04.4 LTS, kernel 7.0.0-1010-aws, 16 vCPU, **local dev box, no GPU** |
| Python | 3.12.3 in `.claude/venvs/e1` (httpx 0.28.1, pytest 9.1.1 only); baseline suite in `.claude/venvs/infrx-api` |
| Media tooling | pinned static `ffmpeg 7.0.2-amd64-static` (johnvansickle.com); tarball sha256 `abda8d77…cf67`, ffmpeg `e7e7fb30…eb99`, ffprobe `4f231a19…450d` — both binaries re-hashed this session and matching. No system ffmpeg exists on this host and none was installed; no docker container or image was created or touched |
| Classification | **local only.** No AWS CLI call, no `*.callbill.ai`, no Supabase, no paid provider, no deployment, no push |
| Corpus cache | `$CORPUS_CACHE=.claude/corpus-cache` (git-ignored): 2.11 GB sources, 206 MB clips+negatives, 202 MB tools |
| Seeds | manifest order is stable; dry runs used `--seed 7`, tests `--seed 11`/`42` |
| Canaries | key `sk-CANARY-e1-dryrun-DO-NOT-LOG-…` (throwaway 40-character literal that authenticates nothing; no real key exists on this host) and signed URL `https://bucket.invalid/…?X-Amz-Signature=CANARYSIG…&X-Amz-Credential=CANARYCRED` |

## Commands

Environment variable **names** only: `CORPUS_CACHE`, `MARLIN_API_KEY`,
`PYTHONDONTWRITEBYTECODE`. `PY = .claude/venvs/e1/bin/python`, cwd = the E1
worktree. Key-bearing runs are shown as `MARLIN_API_KEY=<canary>`.

| UTC | Command | Exit | Result |
|---|---|---|---|
| 2026-09-20T19:17:48Z | `$PY models/marlin2b/corpus/build.py plan` | 0 | `planned 64 clips (32 fast), 4 negatives`; 12 clips dropped to `unbuilt`, all 4 source pins preserved (the previous behaviour reset them) |
| 19:18:00 → 19:20:12Z | `$PY models/marlin2b/corpus/build.py build --jobs 6` | 0 | `built 64/64 clips, 4/4 negatives` — 12 clips re-derived with the pinned ffmpeg, all 64 re-probed and re-hashed |
| 19:26:25Z | `$PY models/marlin2b/corpus/build.py validate` | 0 | `0 error(s)` |
| 19:26:25 → 19:26:28Z | `$PY models/marlin2b/corpus/build.py verify` | 0 | `verified 72 file(s), 0 missing, 0 error(s)` (every built clip re-hashed and re-probed) |
| 19:26:35Z | `$PY -m pytest models/marlin2b/tests -q -p no:cacheprovider` (key env vars unset) | 0 | `21 passed in 4.99s` |
| 19:26:40Z | `$PY models/marlin2b/tests/test_bench.py` | 0 | `12 passed` (plain script, no pytest) |
| 19:26:45Z | `$PY models/marlin2b/tests/test_corpus.py` | 0 | `9 passed` (plain script) |
| 19:27:32 → 19:27:40Z | `MARLIN_API_KEY=<canary> $PY models/marlin2b/bench.py --corpus models/marlin2b/corpus/manifest.json --subset fast --rate 4 --requests 40 --seed 7 --target gateway --forms video_b64,text,upload --out <scratch>/bench.jsonl --raw <scratch>/raw.jsonl --dry-run-transport fake_gateway:transport` | 0 | 40 accepted / 0 rejected / 0 failed; **24 distinct clips** (`distinct_clips_scheduled` 32, 27 of 40 rows sent media); 24 cold / 3 warm; schedule lag p50 2.2 ms, max 117.5 ms; 6 p95/p99 entries suppressed |
| 19:27:57Z | `grep -R -F <canary key> <scratch>` and the same for its first 12 characters | 1 | no match in `stdout.txt`, `stderr.txt`, `bench.jsonl`, `raw.jsonl` — `grep -c` 0 in each, and 0 for `x-amz|signature` |
| 19:28:12Z | same bench command with `--forms upload --requests 6` against a scratch hostile transport handing out a canary signed URL and rejecting the PUT with 403 (two variants) | 1 | 6/6 `failed`, `error_message` = `upload PUT rejected with HTTP 403 (signed destination withheld)`; `grep -R` for the key, its 12-char prefix and `CANARYSIG\|CANARYCRED\|X-Amz-Signature` all exit 1. Exit 1 is the client's "no accepted requests" code, i.e. the intended outcome |
| 19:28:28Z | same, against transports that echo the key into `error.code` and into a 600+ byte non-JSON error body | 1 | 6/6 `rejected`; `error_codes` = `{"bad_key:[redacted-key]": 6}`; `grep -R` for the key **and** for its first 8/12/16/20 characters all exit 1 |
| 19:28:47Z | second identical seeded dry run (`--seed 7`, new output paths) | 0 | `(seq, clip_id, form, scheduled_s, cold, media_sent, prompt_kind)` identical for all 40 requests; `distinct_clips` 24 both runs |
| 19:29Z | independent re-hash of all 72 cached files (clips, negatives, sources) against the manifest, plus both pinned binaries | 0 | `72 files re-hashed independently, 0 mismatches`; ffmpeg and ffprobe match their pins |
| 19:30:48Z | `.claude/venvs/infrx-api/bin/python -m pytest apps/infrx-api/tests -q -p no:cacheprovider` | 0 | `23 passed, 2 warnings in 0.48s` — the predecessor report's "not run" item, now run |

`models/marlin2b/results/bench.jsonl` is byte-identical to its committed state
after every run above (`git diff --quiet` on it exits 0): dry-run output went to
the session scratchpad.

## Results

- **Corpus (probed, not intended):** 64/64 clips and 4/4 negatives built; 64
  distinct sha256 values; **16** distinct (w,h) pairs; 11 distinct aspect ratios
  (`1:4, 9:16, 240:427, 4:3, 71:40, 1:1, 16:9, 12:5, 64:27, 427:240, 4:1`); probed
  aspect ratio spans **0.25 (480×1920) to 4.0 (1920×480)**; three clips carry real
  rotated pixels and say so in their ids (`c020-…-rot90cw`, `c025-…-rot180`,
  `c030-…-rot90ccw`); durations 2.0–112.0 s over 13 values inside the 120 s cap;
  frame rates **{10, 15, 24, 30, 60}**; 16 prompts; **28** clips flagged
  `source_upscaled`, equal to the 28 that are genuinely upscaled on either axis.
  The fast subset (clips 0–31) contains the 1080×1920 portrait (`c004`) and the
  480×1920 tall (`c014`) clips as well as a rotated clip, so the extremes reach the
  client without `--subset full`.
- **Rebuild honesty:** the 12 clips whose recipe or id changed were re-derived by
  the pinned ffmpeg and re-probed; the other 52 were re-hashed and re-probed
  unchanged. `verify` and an independent sha256 pass then agreed on all 72 files.
  Three now-unreferenced files from the old ids were deleted from the cache, which
  is disposable and git-ignored.
- **Secret handling (measured, not asserted):** four hostile-server shapes — key in
  `error.code`, key past the truncation point of a non-JSON body, signed upload URL
  in a rejected PUT, key echoed in an upstream exception — produced **zero**
  occurrences of the canary key, of any prefix of it down to 8 characters, or of
  the signed-URL canaries in stdout, stderr, the raw JSONL or `bench.jsonl`.
- **Denominator honesty:** the seeded run reports `distinct_clips: 24` where the
  predecessor reported 32; 13 of 40 slots were `text` and sent no media, and 27
  media requests covered 24 distinct clips. With `--retries 1` and a scripted 429,
  the summary shows `accepted: 2, rejected: 0` at request level **and**
  `attempt_status_counts {"429": 1, "200": 2}`, `rejected_attempts: 1`,
  `retried_requests: 1`, with `request_latency_s > latency_s` on the retried row.
- **Open-loop honesty:** send lag p50 2.2 ms / max 117.5 ms at 4 req/s over 40
  requests; in tests, max lag stayed below 0.1 s against a 0.25 s fake TTFT, and
  the summary carries `latency_from_scheduled_s` beside `latency_s`.

## Failure drill

Injections actually exercised (all local; E1 owns no database, queue or money
path, so there is no durable state to inspect):

| Injection | Durable state before/after | Observed behaviour |
|---|---|---|
| Server echoes the API key into `error.code` | n/a | Row `rejected`, code stored as `bad_key:[redacted-key]`; summary `error_codes` key redacted; no leak in any sink |
| Server returns a 600+ byte non-JSON body with the key past every truncation point | n/a | Body redacted **before** parsing and truncation; not even an 8-character prefix of the key survives |
| Upload destination is a signed URL and the PUT returns 403 | n/a | `RuntimeError` with status only; the URL, its signature and its credential never reach a row. Repeated with the URL also present in the `POST /uploads` response |
| Upstream exception text echoes the bearer header | n/a | Row `failed`, key replaced by `[redacted-key]` |
| Scripted 429 then success with `--retries 1` | n/a | Both attempts kept in the raw JSONL; the retry's success does not erase the 429 from the attempt-level counts; latency spans both attempts |
| 200 stream with content but no `[DONE]`, `finish_reason` or usage | n/a | `failed` / `truncated_stream`, `stream_complete: false`, exit code 1 — previously counted as accepted |
| 200 stream with `finish_reason` but no usage | n/a | `accepted`, `accepted_without_usage: 2`, token fields null, `out_tok_per_s` 0.0 (never a chunk-count estimate) |
| Scripted 429/402/500/503 | n/a | 429+402 `rejected`, 500+503 `failed`, none accepted; `Retry-After` captured; latency denominator stays at the accepted requests |
| Mislabelled clip, rotated clip with its suffix stripped, falsified `source_upscaled`, source `sha256` cleared (manifest copies in memory) | manifest unchanged on disk | Each produces a specific `validate_manifest` error; `build.py validate` on the real manifest reports 0 |
| Source bytes changed after pinning (code path added this round) | n/a | `build` reports `status: sha256_mismatch` and refuses to re-pin; previously it overwrote the pin silently |
| Corrupt-media fixtures | n/a | Built with real hashes (truncated 65,536 B, `.mkv` holding mp4 bytes, 0 B, 20 B text). They are inputs for a future server-side drill; **no server has rejected them yet** |

Cleanup: hostile transports live only in the session scratchpad (never in the
repository); dry-run output went to the scratchpad; no repository state outside the
owned paths changed.

## Artifacts

| Artifact | Location | Digest / size |
|---|---|---|
| Corpus manifest (committed) | `models/marlin2b/corpus/manifest.json` | sha256 `5799ad267aa9a5699c36da331b7cf9b8114a9d98ff9735342b226189262ace46`, 77,172 bytes (was `a71bb9b8…0a3d`) |
| Built media (not committed, git-ignored) | `$CORPUS_CACHE/clips`, `$CORPUS_CACHE/negatives` | 206 MB, 68 files, each sha256 in the manifest |
| Sources (not committed) | `$CORPUS_CACHE/sources` | 2.11 GB, 4 files, each sha256 in the manifest |
| Pinned ffmpeg/ffprobe | `$CORPUS_CACHE/tools/ffmpeg-7.0.2-amd64-static` | sha256 as pinned above, re-verified this session |
| Dry-run summaries + raw JSONL, hostile transports | session scratchpad only | 40 rows per run; contain no key, no key prefix, no signed URL, no customer content |

No credentials, signed URLs, customer prompts or media were written to the
repository or to any report.

## Changes

Owned paths only.

- `models/marlin2b/bench.py` — `redact()`/`dump_line()` sink redaction, upload PUT
  error without the destination, `media_sent`/`finish_reason`/`stream_complete`/
  `schedule_lag_s`/`request_send_s`/`request_latency_s`/`latency_from_scheduled_s`
  row fields, attempt-level counts, corrected `distinct_clips`, truncated-stream
  classification, `asyncio.to_thread` for base64 and upload reads. Historical CLI,
  `--out` default and `MM_KWARGS` behaviour unchanged.
- `models/marlin2b/corpus/build.py` — `upscales()`, `derived_geometry()`,
  `LABEL_GEOMETRY`, `transpose_for()`/`clip_id()`, label/geometry/aspect and
  coverage validation rules, `plan` source-pin carry-forward, `build` re-derive of
  non-`built` clips and `sha256_mismatch` guard, NASA caveat, ffmpeg alias note.
- `models/marlin2b/corpus/manifest.json` — replanned and rebuilt (12 clips
  re-derived, 3 ids changed, all 64 re-probed and re-hashed).
- `models/marlin2b/corpus/README.md` — coverage section rewritten to describe the
  probed files, NASA caveat, ffmpeg alias, corrected sizes and gaps.
- `models/marlin2b/tests/{fake_gateway.py,test_bench.py,test_corpus.py}` — hostile
  server knobs and 8 new/extended cases (13 → 21).
- `models/marlin2b/results/notes.md` — appended a review-round-1 correction; the
  earlier E1 section and all four historical rows are intact, `bench.jsonl`
  untouched.
- `research/plan/evidence/e/` — this report; `E1-38b07b5.md` gained a superseded
  banner and an appended verification-log entry, nothing removed.

**Summary-schema change for consumers (E4, I):** `distinct_clips` now means
"distinct clips whose media was sent"; `denominators` gained four attempt-level
keys; `percentiles.latency_s` is request-level (spans retries);
`percentiles.latency_from_scheduled_s`, `schedule_lag_s`, `attempt_status_counts`,
`attempt_outcomes` and `distinct_clips_scheduled` are new; rows gained
`media_sent`, `finish_reason`, `stream_complete`, `schedule_lag_s`,
`request_send_s`, `request_latency_s`, `latency_from_scheduled_s`. Historical
`bench.jsonl` rows predate all of these keys and stay readable.

No migration, no deployment, no schema. Rollback is `git revert a1cb4f4` plus a
`plan`+`build` to restore the previous (defective) manifest; the cache is
disposable and rebuildable.

**Contract assumptions unchanged from the predecessor report** (upload handshake
shape, OpenAI-style error bodies, `Inference-Id`/`Retry-After`/`Server-Timing`
headers) and still need G/M confirmation.

## Limits

- **Nothing here is measured performance.** No GPU, no vLLM, no gateway was
  contacted; every timing is fake-gateway latency on a 16-vCPU CPU host.
- Baseline tool limits, now recorded rather than implied: the client measures
  latency from the send and, in open loop, from the scheduled arrival, but cannot
  see server-side queue/prep/decode phases without `Server-Timing`; `cold`/`warm`
  is client-side first-use, not engine multimodal-cache truth; `upload_s` is
  wall-clock only; base64 and upload encoding run in a thread, so a run at high
  rate with 35 MB clips is bounded by CPU and by the GIL, not by the server alone
  (max observed send lag 117.5 ms at 4 req/s with the fast subset).
- The upload and `video_url` forms remain unexercised against a real server;
  `video_url` needs a publicly reachable media host, which must not be created for
  this corpus without re-checking CC-BY redistribution obligations.
- Corpus gaps: no container display-matrix rotation (ffmpeg 7's mp4 muxer drops
  `-metadata rotate=`); single codec/container family (H.264/MP4); no audio; lowest
  frame rate 10 fps and no variable frame rate; three of four sources are animated
  films; segments are cuts of four films rather than 64 independent productions.
- The NASA STEVE source very likely embeds third-party stills (documented caveat,
  nothing redistributed). `FFMPEG_PIN.tarball_url` is an unversioned alias: on a
  fresh host after an upstream release, the pinned hash will refuse the download
  and the corpus is unbuildable until the tarball is fetched from old-releases by
  hand. Owner: E (mirror requires an authorized S3 write — not done).
- `models/marlin2b/tests/` is not in track E's literal planned-ownership list and
  its discovery by the shared test command is F2's decision, not proven here.
- The percentile rule (≥3 samples beyond the quantile) is a judgement call recorded
  in the summary as `percentile_rule`.
- The `verify --rederive` path and the `build` `sha256_mismatch` path were
  exercised by the review and by construction respectively; only the latter is
  covered by a repository test (via `validate`), not by an end-to-end build.

### How E4 should run this

Unchanged from the predecessor report, with three additions: quote
`distinct_clips` (media sent) rather than the scheduled count when arguing a run
was not warm-cache-only; read `denominators.rejected_attempts` alongside
`rejected` whenever `--retries` is non-zero; and treat `schedule_lag_s.max` as the
validity check on an open-loop cell — if it approaches the mean inter-arrival time,
the client, not the server, is the bottleneck and the cell must be rerun with a
lower rate or pre-encoded media.

## Handback

- **Next unblocked task:** E2 (pinned integration services and fault harness) —
  start dependencies E1 and F2, so it waits on F2. `fake_gateway.py` now also
  scripts a truncated stream, a rejected signed-URL upload and key-echoing error
  bodies; E2 must add prefill stall, midstream stall, cancellation race and abrupt
  exit.
- **Coordinator wiring requested (do not let me make these changes):**
  1. Acknowledge `models/marlin2b/corpus/` and `models/marlin2b/tests/` as E1's
     home (they are outside track E's literal path list and collide with nothing),
     and let F2 decide discovery and the Python pin for them.
  2. Confirm or correct the three contract assumptions with G and M.
  3. Ask G to emit `Server-Timing` (queue/prep/decode) on chat responses.
  4. For I: the corpus cache is ~2.5 GB per host and rebuildable from the manifest;
     do not bake it into an image. An authorized mirror of the pinned ffmpeg
     tarball (e.g. the existing S3 weights bucket) would remove the alias risk —
     E1 did not touch any cloud resource.
  5. A stray git-ignored `.claude/corpus-cache` (~483 MB: one source, six clips and
     the tools) exists **inside** this worktree from an early run and can be
     deleted; the canonical cache is the repository-root one.
- **Unresolved findings:** none blocking. For M: MEDIA-PARITY will still need
  phone-style display-matrix rotation fixtures that this corpus cannot produce with
  the pinned mp4 muxer.

## Verification log

- 2026-09-20 (round 1 fixes, `a1cb4f4`/`f702fc0`): all four blocking review
  findings fixed at their root cause with regression tests; 12 clips rebuilt with
  the pinned ffmpeg so the corpus really contains the portrait, extreme-tall and
  rotated media its ids claim; 72 cached files re-hashed independently with 0
  mismatches; 21 tests passed under pytest and 12+9 as plain scripts; the
  `apps/infrx-api` baseline passed (23). Four hostile-server shapes produced zero
  canary-key, key-prefix or signed-URL occurrences in any output path. No live,
  GPU, cloud, paid or deployment operation was performed; PERF-PILOT and
  MEDIA-PARITY remain unproven oracles awaiting M3/G4 and E4. Status is
  **implemented**, never integrated.
