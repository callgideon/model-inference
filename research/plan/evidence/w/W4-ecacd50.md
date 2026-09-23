# W4 — Measured Marlin serving tuning, phase A (protocol, scripts, analysis, P-20 record)

| Field | Value |
|---|---|
| Task | W4 (track W), oracles ENGINE-OPT, PERF-ENVELOPE, MEDIA-PARITY, OPS-RECOVER; phase A only (coordinator ruling 3) |
| Status | **implemented** (phase A). Measurement **pending the coordinator's box run** of `candidate.sh` (E0, E1; E3 only if triggered) in a logged maintenance window; nothing measured by this lane, nothing adopted, nothing integrated. The pilot box was not contacted |
| Owner / session | Claude Opus 5.5 session, worktree `.claude/worktrees/codex-w4`, branch `codex/w4-measured-tuning` |
| Base SHA | `a237d6f36b4ba371c50578ca330938db2d005072` (`origin/claude/backend-impl`, "coordinator: TASK_PORTS registers d5 …"; the fast-forward was a no-op: the branch already pointed there) |
| Protocol commit (before any script) | `4614392` — `models/marlin2b/measure/W4-protocol.md`; the first script commit is `58e2659` |
| Implementation SHA | `ecacd50` (commits `4614392`, `58e2659`, `28af39c`, `6b6465a`, `ecacd50`); this report is committed after it |
| Integrated SHA | none — the coordinator integrates |
| Classification | local development environment only; fakes, stubs and the committed W3 sweep |

## Per item

| Item | What | Commit | Killing case | Mutants (all killed, list below) |
|---|---|---|---|---|
| 1 protocol | `measure/W4-protocol.md`: identity, the bounded matrix (E0 pinned; E1 `--max-num-batched-tokens 32768`; E2 = W3's rule on E1; E3 conditional `--api-server-count 2`), cache control (a restart before every level), the parity set, every criterion with its `decide.py` constant, the P-20 rule, invalidators | `4614392` (before `58e2659`) | `test_engine_opt__the_protocol_states_the_criteria_decide_applies` (parses §5's table against `decide.py`, §4's parity set against `parity.py`, §2's flags against `candidate.sh`'s table) | `threshold_edited_in_decide_only`, `t_fraction_lowered`, `e1_flag_off_protocol` |
| 2 flags in the pinned source | read-only blobless fetch of `vllm-project/vllm@a8d1aa9c99b8698a2a78b611b7a10c30e6b3995b`; the table below (also the protocol's §3) | `4614392` | none: documented non-code item (brief) | — |
| 3 `candidate.sh` | closed candidate table; refusals; records the engine it found; stop → gate (capability, parity) → one fresh engine per level with `ENGINE_STATE=restarted` → restore on EXIT with `restored=yes\|no` and an args diff; writes only under `$NVME/w4-*`; W3's measurement checkout, never the unit's tree | `28af39c`, `ecacd50` | `test_ops_recover__the_candidate_run_restores_the_engine_it_found` (also on a failing sweep, and a unit that comes back different → `restored=no`, exit 4); `test_ops_recover__the_candidate_run_refuses_in_flight_work_and_unlisted_flags` (10 refusals, nothing stopped, nothing written); `test_ops_recover__the_candidate_run_records_start_to_ready_and_engine_errors` | `restore_trap_removed`, `restore_skipped_on_failure`, `restore_ignores_the_args`, `partof_units_left_stopped`, `levels_share_one_engine`, `levels_labelled_warm`, `in_flight_check_dropped`, `unreadable_metrics_taken_as_idle`, `free_form_flag_accepted`, `unlisted_candidate_runs`, `restart_consent_dropped`, `unit_tree_accepted`, `writes_outside_w4`, `candidate_corpus_not_verified`, `no_container_not_refused`, `checkout_not_checked`, `engine_log_capture_dropped`, `start_to_ready_unrecorded` |
| 3′ `concurrency.sh` (ruling 1: one line) | `--engine-state "${ENGINE_STATE:-warm}"` (default unchanged) | `28af39c` | `test_perf_envelope__the_sweep_labels_the_engine_state_it_is_given` (the real script, stub bench: `warm` unset, `restarted` when given) | `engine_state_ignored` |
| 4 `decide.py` | W3's rule as code, per-level T/F-by-class/W/peaks/p95s/repeats, every item-1 criterion, fail-closed unknowns, explicit `--set-aside`, `e3_trigger`, `--ceiling`, `--overheads` | `58e2659`, `28af39c` | `test_engine_opt__the_050411Z_sweep_has_no_qualifying_level`, `test_engine_opt__the_set_aside_reproduces_the_recorded_c16_only_when_named`, `test_perf_envelope__an_unsupported_tail_or_blank_sample_is_unknown`, `test_perf_envelope__cells_at_different_cache_states_are_not_compared`, `test_engine_opt__a_passing_candidate_is_adopted_and_each_disqualifier_blocks_it` | `failures_counted_over_accepted_only`, `blank_sample_read_as_zero`, `t_fraction_lowered`, `floor_x_ignored`, `set_aside_by_default`, `set_aside_by_default_on_the_command_line`, `pre_fix_waiting_trusted`, `p95_without_sufficiency`, `warm_compared_with_restarted`, `unknown_is_adopted`, and one `<criterion>_not_applied` each for error_rate, overload_masking, severe_tail, oom, memory_growth, cancellation, short_job_starvation, usage_drift, output_drift |
| 5 `parity.py` + the comparison | stdlib client at c = 1 on the predeclared set: prompt/completion tokens, finish reason, content sha256, caption events, the engine's own refusal text; `decide.parity_verdict` | `58e2659`, `28af39c`, `ecacd50` | `test_media_parity__token_or_content_drift_disqualifies`; `test_media_parity__the_parity_client_records_usage_content_and_the_engines_refusal` (a local HTTP engine; the request's `mm_processor_kwargs` equal the worker's `Media.budget_kwargs`) | `hash_comparison_dropped`, `prompt_token_equality_skipped`, `refusal_count_unchecked`, `overhead_band_unchecked`, `missing_candidate_row_ignored`, `smart_resize_rounds`, `frames_not_rounded_to_even`, `refusal_text_not_recorded`, `accepted_without_done`, `parity_sends_no_eos` |
| 6 P-20 record | the section below; `decide.py --ceiling`; the worker-side case | `28af39c` (+ this report) | `test_engine_opt__the_interim_ceiling_fits_every_geometry_and_e1_covers_120_s`; `test_engine_opt__a_deterministic_engine_refusal_settles_once_and_free` (no existing `tests/w` case covered a post-200 error **before any visible chunk**: W1's `engine_error_post_headers` sends one delta first, so the fault `engine_error_before_content` was added to `infrx/worker/fakes.py`, additively) | `ceiling_ignores_the_sampling_edge`, `engine_refusal_untyped` |
| 7 phase B | not in this dispatch (ruling 3) | — | — | — |
| 8 list + evidence | `tests/w/w4_mutants.py`, `tests/w/test_w4_mutants.py`; this report | `6b6465a`, `ecacd50` | `test_the_list_is_well_formed`, `test_every_w4_case_is_covered_by_a_mutant_and_every_named_case_exists` | — |

Commit-message corrections (no amend, by rule): `6b6465a` says "51 mutants"; `--list` printed
52 at that commit and prints 52 now. `ecacd50`'s title says "2 of 54 not killed": that run
had 54 tests, 52 of them mutants, 2 mutants not killed.

## Item 2 — flags checked in vLLM's source at `a8d1aa9c99b8698a2a78b611b7a10c30e6b3995b`

Fetched read-only (`git fetch --depth 1 --filter=blob:none origin a8d1aa9c…` into the
session scratchpad; `git log -1`: `a8d1aa9c99b8698a2a78b611b7a10c30e6b3995b
2026-09-18T21:06:48-07:00 [Bugfix][Multimodal] Frame the multi-modal hash digest input
(#54283)`). No container was used.

**Where 16384 comes from.** `MultiModalBudget` (`vllm/multimodal/encoder_budget.py:16-44, 123`)
takes the largest per-item token count of the enabled modalities from
`get_mm_max_tokens_per_item` (`vllm/model_executor/models/qwen2_vl.py:868-875`). The image
item is 16,777,216 px (the checkpoint's `preprocessor_config.json` `size.longest_edge`) ÷
(16·2)² = 16,384 (`qwen2_vl.py:965-1026`); the video item under the processor default
(`processor_config.json` `longest_edge` 25,165,824) is 12,288 (`qwen3_vl.py:1014-1036`). So
the start-up log's "profiled with 1 image item" (`gpu_model_runner.py:6518-6525`), and
`encoder_cache_size = max(scheduler.encoder_cache_size, 16384)`
(`vllm/v1/core/encoder_cache_manager.py:337-344`), where `scheduler.encoder_cache_size =
max_num_batched_tokens` (`vllm/config/scheduler.py:291-292`; "not currently configurable",
`:145-149`), default 2,048 for the API server on a GPU under 70 GiB
(`vllm/engine/arg_utils.py:2766-2771`). The sweep's refusal is
`vllm/v1/engine/input_processor.py:509-519` (size from `:63-64`). The per-request
`mm_processor_kwargs` never enter it.

| Flag | `file:line` at the pin | Default here | Effect | Changes preprocessing numerics? |
|---|---|---|---|---|
| `--max-num-batched-tokens N` | `vllm/engine/arg_utils.py:1592-1597`; default `:2766-2771`; `vllm/config/scheduler.py:291-292`; `vllm/v1/core/encoder_cache_manager.py:337-344` | 2,048 | encoder cache and encoder compute budget become `max(N, 16384)`; also the per-step chunked-prefill budget | **no** (no processor input); prefill chunking can move greedy output → parity gate. **E1** |
| `--limit-mm-per-prompt` per-modality `{"count","num_frames","width","height"}` | `vllm/config/multimodal.py:38-44, 127-145`; Qwen3-VL applies them only downward, `qwen3_vl.py:1137-1148, 1207-1222` | `{"video":1,"image":4}` | shrinks the profiled dummy only; **cannot raise** the budget | no. Not usable |
| `--mm-processor-kwargs` (server) | `vllm/config/multimodal.py:163-172`; `arg_utils.py:1397`; merged into every request `vllm/multimodal/processing/context.py:293-309`; read by `qwen3_vl.py:1014-1036` | unset | a larger `size.longest_edge` raises the profiled video item | **yes** — the processor default for any request without `size`: a separate serving version with a parity gate, not a knob. Not used |
| `--mm-processor-cache-gb` / `--mm-processor-cache-type` | `vllm/config/multimodal.py:177-191`; `arg_utils.py:1400-1404` | 4 GiB, `lru` | processed-media cache by content hash; `0` disables | no. Not used (a restart per level is the cache control) |
| `--api-server-count N` | `vllm/entrypoints/launchers/cli_args.py:417-424`; defaulting `vllm/entrypoints/cli/serve.py:105-127` | 1 | N front-end processes, each with its own single-worker media executor (`vllm/renderers/base.py:115-118, 133-136`) | no. **E3** (conditional) |
| `--renderer-num-workers N` | `arg_utils.py:984`; `vllm/config/model.py:375-383` | 1 | tokenization/template pool; media stays on the single-worker executor (`renderers/base.py:115-118`) | no. No effect on video; not used |
| `--skip-mm-profiling` | `vllm/config/multimodal.py:243`; `gpu_model_runner.py:6490` | off | skips the encoder memory profile; the budget is computed regardless | no; removes a safety margin. Not used |

The single-worker media executor per API process is the source-level explanation W3's record
called unverified ("peak_running < c: API-server video preprocessing likely holds
requests"); `decide.py` prints `e3_trigger=yes c=[16, 32]` on the W3 sweep (below), which is a
warm sweep and so only a hint - E3 runs only if E0 or E1 trigger it under the protocol.

## P-20 decision record (evidence and proposal; the coordinator decides)

- **Measured input** (`meas.`, the W3 sweep `sweep-20260923T050411Z`, read by `decide.py`
  below): the four 112 s clips fail at every level, `stream_error_event`; the engine's text
  (`… 20160|21168|21504 embedding tokens exceeds the pre-allocated encoder cache size 16384`)
  is the coordinator record's quote of the engine log, in no raw artifact - `candidate.sh`
  now captures such lines per cell and `parity.py` records the refusal text per clip.
- **Cause** (item 2): the encoder cache is sized by the largest profiled item (an image,
  16,384) because `max_num_batched_tokens` is 2,048 here; profile v1's video items are
  larger and are refused, deterministically, whatever the load.
- **Option A — raise the budget (E1, `--max-num-batched-tokens 32768`).** The only flag at
  the pinned build that raises it without touching preprocessing. `decide.py --ceiling 32768`
  prints `ceiling_s=120` (worst case over every geometry at 120 s: `worst_video_tokens=23520`),
  so profile v1's published 120 s is kept. It also changes the prefill chunk size, which is
  why E1 must pass parity, tail, starvation and memory criteria against E0. **Preferred only
  if E1 passes every criterion** (`decide.py <E1> --baseline <E0>` prints `adopt …`). Then
  phase B: the flag as a literal in `serve.sh`, a new `engine_options_digest` and
  `serving_versions` row (R76/R78), and `MAX_VIDEO_SECONDS` back to 120 only once that
  serving version is deployed.
- **Option B — cap admission** (`MAX_VIDEO_SECONDS`, read by `infrx/config.py:99`, M's
  `MediaProfile.pinned` `infrx/media/prepare.py:88`, legacy `infrx/media/video.py:204`): a
  capability change (S2M's `max_video_seconds` record, coordinator configuration). Chosen if
  E1 fails a criterion.
- **Interim ceiling until A is deployed: 82 s** (`decide.py --ceiling 16384` →
  `ceiling_s=82`). Derivation: profile v1's frame budget `F = 2·duration` (even); the
  processor keeps each frame at most `F·200,704 / sampled` pixels (`smart_resize`), and vLLM
  counts `ceil(sampled/2) · (h/16)·(w/16)/4` tokens (`qwen3_vl.py:936-998`); allowing the
  processor to sample up to two frames fewer than `F`, the worst case over every geometry is
  16,154 tokens at 82 s and 16,434 at 83 s (quoted below). 83 s would hold for clips
  sampled at exactly `F` (all 64 corpus clips: `video_tokens(83, 1080, 1080) = 16268`), but
  a 2.44:1 frame (704×288) sampled one frame short at 83 s needs `16434`. **The coordinator's
  applied 72 s is safe with margin; 82 s is the largest derived value.** The arithmetic is
  checked against the box, not assumed: it reproduces the three refusal counts for the four
  112 s clips exactly (`test_media_parity__…`, reading the counts from
  `serving-version.json`), and leaves every accepted clip of ≥ 24 groups a text/timestamp
  overhead of 9.181–9.808 tokens per group (`decide.py --overheads`: `clips=140` rows = 20
  clips × 7 level-passes). It is a stdlib reproduction of `transformers`' `smart_resize`, not
  a run of the image's own processor - the image's code was not read (limit 6).
- **Worker-side presentation** (settled by a case): the refusal arrives as HTTP 200, the
  role chunk, then an SSE error and no visible text;
  `test_engine_opt__a_deterministic_engine_refusal_settles_once_and_free` shows the worker
  settles it once as `engine_error`, debit 0, `released_platform_absorbed` (R21: the
  customer pays nothing; the platform absorbs it), one engine request, nothing relayed, and a
  second pass runs nothing.
- **Proposal:** run E0 and E1; adopt A on `decide.py`'s `adopt` verdict, otherwise B at 82 s.
  Until then keep the applied 72 s or raise it to 82 s (both admit only what this engine
  accepts by the derivation above).

## Requirement coverage

| Test ID | Invariant shown here (fakes, stubs, the committed sweep) | Cases |
|---|---|---|
| ENGINE-OPT | a setting is adopted only when W3's rule yields `c*`, `X` is known and every predeclared criterion passes; any fail or unknown adopts nothing; the protocol and the code carry the same numbers; the matrix is only flags verified in the pinned source | protocol, no-qualifying-level, set-aside, passing-candidate, ceiling, deterministic-refusal |
| PERF-ENVELOPE | denominators from raw attempts (F counts every failed attempt; rejections and retries visible); a p95 needs 60 accepted; a blank sample is unknown; cells at different cache states are never compared; every level runs on a fresh engine labelled `restarted` | unsupported-tail, cache-states, sweep-labels-state, candidate restores |
| MEDIA-PARITY | parity requests carry the worker's profile-v1 budget, both EOS ids, temperature 0; clips both accept compare prompt tokens and content (or caption events); newly accepted clips must carry the pinned processor's count; a missing or refused clip is not a pass | token-or-content drift, parity client |
| OPS-RECOVER | the engine found is restored on any exit (failing sweep included), with `restored=yes` only for the same args and image, healthy, and the PartOf units restarted; start-to-ready recorded for every start; nothing is stopped when a precondition fails | candidate restores / refuses / records |

## Environment

Linux 7.0.0-1010-aws x86_64; Python 3.12.3 (`uv run --frozen`, `apps/infrx-api/.venv`); uv
0.11.8; GNU bash 5.2.21. Docker 29.6.2 is installed; this session created, started, stopped
or removed no container or image. Read-only docker calls did happen: `docker --version`, one
`docker ps -a --filter name=infrx-q3-valkey` (diagnosis, below), and Q's Valkey harness's own
`docker info`/`docker inspect` probes during the orderings. `tests/d` and
`tests/contracts/v2/test_v1_projection_pg.py`, which start D's harness containers, were not
run (dispatch rule "no docker"). No network except item 2's read-only
public git fetch of one vLLM commit. No AWS/SSM call; the pilot box was not contacted.

## Commands and results

Exit status 0 unless stated. Tails are quoted from the logs (session scratchpad `logs/`).

| Command (from `apps/infrx-api` unless a make target) | UTC / head | Exit | Tail |
|---|---|---|---|
| `UV_OFFLINE=1 make api-env` (run twice: the first installed the extras from the uv cache, the immediate re-run is quoted) | 06:26:12Z / `6b6465a` | 0 | `Checked 41 packages in 0.34ms` |
| `uv run --frozen pytest -q -p no:cacheprovider tests/w/test_w4.py` | 06:45:43Z / `ecacd50` | 0 | `14 passed in 51.81s` |
| `uv run --frozen pytest -q -p no:cacheprovider tests/w` | 06:26:29Z / `6b6465a` (test_w4 changed after it only in two case bodies; count unchanged) | 0 | `178 passed in 336.12s (0:05:36)` — base `a237d6f`: `156 tests collected` (a scratch `git archive` export), so +22 = `tests/w/test_w4.py` 14 + `test_w4_mutants.py` 8 (`22 tests collected`) |
| `INFRX_MUTANTS=all uv run --frozen pytest -q -p no:cacheprovider tests/w/test_w4_mutants.py` (detached) | 06:37:07Z / `ecacd50` | 0 | `54 passed in 502.59s (0:08:22)` = 52 mutants killed + 2 list checks. The first full run (06:25:39Z, `6b6465a`) was `2 failed, 52 passed`: `refusal_count_unchecked` survived (masked by the overhead band) and `checkout_not_checked` was `broken_runner` (a 60 s subprocess timeout); both cases fixed in `ecacd50` and both mutants then `[killed]` by `python -m tests.w.w4_mutants refusal_count_unchecked checkout_not_checked no_container_not_refused` → `3/3 killed` |
| `uv run --frozen python -m tests.w.w4_mutants --list \| tail -1` | `ecacd50` | 0 | `52 mutants over 14 named cases` |
| `INFRX_MUTANTS=all … tests/w/test_mutants.py` (W1, detached) | 06:33:31Z / `6b6465a` | 0 | `162 passed in 403.77s (0:06:43)` |
| `INFRX_MUTANTS=all … tests/w/test_loop_mutants.py` (W2, detached) | 06:33:31Z / `6b6465a` | 0 | `69 passed in 199.41s (0:03:19)` |
| `INFRX_MUTANTS=all … tests/w/test_w3_mutants.py` (W3, detached) | 06:33:31Z / `6b6465a` | 0 | `87 passed in 285.76s (0:04:45)` |
| `make bench-test` | 06:32:49Z / `6b6465a` | 0 | `67 passed in 6.30s` |
| legacy-first: `uv run --frozen pytest -q -p no:cacheprovider --ignore=tests/d --ignore=tests/contracts/v2/test_v1_projection_pg.py -k "not valkey"` (detached) | 07:41:12Z / `ecacd50` | 1 | `42 failed, 2507 passed, 90 deselected, 2 warnings, 6 errors in 1637.00s (0:27:16)` — every failure/error pre-existing and outside W (below) |
| track-first: `… pytest -q -p no:cacheprovider tests/g tests/i tests/j tests/m tests/q tests/t tests/w tests/contracts tests/test_app_factory.py tests/test_gateway_auth.py tests/test_inflight.py tests/test_media.py --ignore=tests/contracts/v2/test_v1_projection_pg.py -k "not valkey"` (detached, after the first) | 08:08:32Z / `ecacd50` | 1 | `42 failed, 2507 passed, 90 deselected, 2 warnings, 6 errors in 1665.66s (0:27:45)`; `diff` of the two sorted FAILED/ERROR id lists: `same failing set` — the counts are equal |
| `make check` | — | not run | its `api-test` and `api-mutants` start D's harness containers (`tests/d`, `tests/contracts/v2/test_v1_projection_pg.py`): the dispatch forbids docker |
| console targets | — | not run | no `apps/app` change |

**Pre-existing failures, not W4's.** Reproduced on a scratch `git archive a237d6f` export
with the same venv (so present at the base): the packaging case, one Valkey case
(`test_q3_reconcile__a_consistent_index_is_left_alone`) and the three Q mutants; the rest
share those causes (the I list's pristine baseline includes the packaging case; the Q
reconcile failures and errors print the Valkey message):

- `tests/i/test_packaging.py::test_backend_deploy__the_config_schema_is_every_name_the_runtime_reads`
  → `AssertionError: (['ACCOUNTING_REGIME', 'ACTIVE_RATE_CARD_VERSION', 'PROVIDER_DEV_ALLOCATION_CEILING_CREDIT'], [])`
  (names the F2P wire-in's runtime reads that I's schema does not list; owner I/coordinator).
  It is in the I list's pristine baseline, so every `tests/i/test_mutants.py` mutant is
  `broken_runner` in a full run (36 in the first, time-limited run; 35 under `-k "not
  valkey"`, which also deselects the I mutant `valkey_public_bind`).
- `tests/q/*[valkey]` cases → `RuntimeError: infrx-q3-valkey did not accept connections
  within 90.0s`, and three `tests/q/test_mutants.py` mutants (`an_arriving_tenant_starts_at_zero`,
  `the_dispatch_start_is_not_clamped_to_the_virtual_time`, `the_kind_filter_is_inverted`)
  `broken_runner` on a pristine baseline that includes `tests/q/test_valkey_scheduler.py`.
  A read-only `docker ps -a --filter name=infrx-q3-valkey` shows a container of that name
  created 2026-09-23 05:51:45 UTC (before this session's first suite run) and running, while
  nothing listens on its port 55462 (`ss -ltn`): Q's harness saw it running, did not start or
  create anything, and waited 90 s per case. Environmental; owner Q / whoever holds the
  container. The first legacy-first run hit `timeout 3600` (`EXIT=124`) on those waits,
  so the orderings were re-run with `-k "not valkey"` (the same deselection in both). The
  42 + 6 of each ordering are exactly: 35 `tests/i/test_mutants.py` mutants and
  `tests/i/test_packaging.py::…config_schema…` (the first bullet), 3 `tests/q/test_mutants.py`
  mutants, 3 `tests/q/test_reconcile.py` cases whose names do not say valkey but use it
  (`test_q3_drill__dr13_shape_…`, `test_q3_differential__…` ×2) and the 6 errors of
  `tests/q/test_reconcile_mutants.py` (its Valkey fixture); `infrx-q3-valkey did not accept`
  occurs 9 times in the legacy-first log. No failure is in `tests/w`, `tests/contracts`,
  `tests/g`, `tests/j`, `tests/m` or `tests/t`.

The W3 sweep read by `decide.py` (the golden input of items 4 and 6):

```
### python3 models/marlin2b/measure/decide.py research/plan/evidence/w/box/sweep-20260923T050411Z   @ 2026-09-23T07:51:08Z (head ecacd50)
run=research/plan/evidence/w/box/sweep-20260923T050411Z baseline=None set_aside=none X=80.7 start_to_ready_s=[]
level c=1 state=warm attempts=64 accepted=60 T=0.353 F=4 {'stream_error_event': 4} failed_clips=c012-bbb1080p30-1024x768-4x3,c025-tos720p-2560x1080-ultrawide-rot180,c038-sintel1080p-480x854-portrait,c051-tos720p-360p-16x9 W=0.0 peak_running=1.0 peak_kv=0.00573436921938586 peak_gpu_mib=44251.0 util_median=100.0 ttft_p95=3.046 latency_p95=6.2907 repeats=0
level c=2 state=warm attempts=64 accepted=60 T=0.812 F=4 {'stream_error_event': 4} failed_clips=c012-bbb1080p30-1024x768-4x3,c025-tos720p-2560x1080-ultrawide-rot180,c038-sintel1080p-480x854-portrait,c051-tos720p-360p-16x9 W=0.0 peak_running=2.0 peak_kv=0.007029226785053688 peak_gpu_mib=44251.0 util_median=100.0 ttft_p95=1.7052 latency_p95=5.5045 repeats=0
level c=4 state=warm attempts=64 accepted=60 T=1.301 F=4 {'stream_error_event': 4} failed_clips=c012-bbb1080p30-1024x768-4x3,c025-tos720p-2560x1080-ultrawide-rot180,c038-sintel1080p-480x854-portrait,c051-tos720p-360p-16x9 W=0.0 peak_running=4.0 peak_kv=0.012393636699962962 peak_gpu_mib=45139.0 util_median=100.0 ttft_p95=2.4332 latency_p95=6.3195 repeats=0
level c=8 state=warm attempts=64 accepted=60 T=1.888 F=4 {'stream_error_event': 4} failed_clips=c012-bbb1080p30-1024x768-4x3,c025-tos720p-2560x1080-ultrawide-rot180,c038-sintel1080p-480x854-portrait,c051-tos720p-360p-16x9 W=0.0 peak_running=8.0 peak_kv=0.02219755826859049 peak_gpu_mib=45139.0 util_median=84.5 ttft_p95=3.773 latency_p95=8.5095 repeats=0
level c=16 state=warm attempts=64 accepted=60 T=2.206 F=4 {'stream_error_event': 4} failed_clips=c012-bbb1080p30-1024x768-4x3,c025-tos720p-2560x1080-ultrawide-rot180,c038-sintel1080p-480x854-portrait,c051-tos720p-360p-16x9 W=0.0 peak_running=13.0 peak_kv=0.03311135775064744 peak_gpu_mib=45139.0 util_median=86.0 ttft_p95=7.0806 latency_p95=13.4532 repeats=0
level c=32 state=warm attempts=128 accepted=120 T=2.376 F=8 {'stream_error_event': 8} failed_clips=c012-bbb1080p30-1024x768-4x3,c025-tos720p-2560x1080-ultrawide-rot180,c038-sintel1080p-480x854-portrait,c051-tos720p-360p-16x9 W=unknown peak_running=24.0 peak_kv=0.05937846836847949 peak_gpu_mib=45139.0 util_median=83.0 ttft_p95=11.7668 latency_p95=22.453 repeats=64
w3_rule c*=None threshold=2.1384 setting=None
criterion w3_rule=fail (no level has F=0, W=0 and T>=2.1384)
criterion error_rate=fail (28/448 = 0.0625)
criterion overload_masking=pass (--retries 0)
criterion severe_tail=unknown (no c*)
criterion oom=unknown (no candidate.log (engine exits not recorded))
criterion memory_growth=unknown (c=32 growth unsupported (gpu 0.0, host None))
criterion cancellation=unknown (no capability.sh cancellation line)
criterion short_job_starvation=unknown (no paired baseline)
criterion usage_drift=unknown (no paired baseline)
criterion output_drift=unknown (no paired baseline)
e3_trigger=yes c=[16, 32]
verdict: no setting adopted - failed: ['w3_rule', 'error_rate']; unknown: ['severe_tail', 'oom', 'memory_growth', 'cancellation', 'short_job_starvation', 'usage_drift', 'output_drift']
exit=0

### python3 models/marlin2b/measure/decide.py research/plan/evidence/w/box/sweep-20260923T050411Z --set-aside c012,c025,c038,c051
run=research/plan/evidence/w/box/sweep-20260923T050411Z baseline=None set_aside=c012,c025,c038,c051 X=80.7 start_to_ready_s=[]
w3_rule c*=16 threshold=2.1384 setting=16
criterion w3_rule=pass (c*=16)
criterion error_rate=pass (0/420 = 0.0)
criterion overload_masking=pass (--retries 0)
criterion severe_tail=pass (levels <= 16)
criterion oom=unknown (no candidate.log (engine exits not recorded))
criterion memory_growth=unknown (c=32 growth unsupported (gpu 0.0, host None))
criterion cancellation=unknown (no capability.sh cancellation line)
criterion short_job_starvation=unknown (no paired baseline)
criterion usage_drift=unknown (no paired baseline)
criterion output_drift=unknown (no paired baseline)
e3_trigger=yes c=[16, 32]
verdict: no setting adopted - failed: []; unknown: ['oom', 'memory_growth', 'cancellation', 'short_job_starvation', 'usage_drift', 'output_drift']
exit=0

### python3 models/marlin2b/measure/decide.py --ceiling 16384; python3 models/marlin2b/measure/decide.py --ceiling 32768; python3 models/marlin2b/measure/decide.py --overheads research/plan/evidence/w/box/sweep-20260923T050411Z
duration_s=80 frames=160 worst_video_tokens=15760
duration_s=81 frames=162 worst_video_tokens=16038
duration_s=82 frames=164 worst_video_tokens=16154
duration_s=83 frames=166 worst_video_tokens=16434
duration_s=84 frames=168 worst_video_tokens=16548
ceiling_s=82 budget_tokens=16384
duration_s=118 frames=236 worst_video_tokens=23128
duration_s=119 frames=238 worst_video_tokens=23443
duration_s=120 frames=240 worst_video_tokens=23520
ceiling_s=120 budget_tokens=32768
clips=140 per_group_min=9.181 per_group_max=9.808

### python3 -c 'import decide as d; ...' (the two 83 s cases the ceiling rests on)
video_tokens(83, 704, 288, sampled=165) = 16434
video_tokens(83, 1080, 1080) = 16268
```


## The SSM block for the coordinator (not run here)

W3's helpers `wrap`, `ssm`, `out` (`research/plan/evidence/w/W3-d8a7878.md`, "The measurement
half") are used unchanged; no secret is needed and none is in any command text.

**Preconditions, all coordinator-owned:**

1. A **logged maintenance window** (open item (b)): the pilot's public route in maintenance,
   no traffic. `candidate.sh` refuses with requests in flight but cannot stop new ones from
   arriving, and the candidate engine answers on the same port while it runs.
2. The measurement checkout `/opt/dlami/nvme/w3-checkout` **updated to the integration head
   that contains W4** (a git bundle through the project bucket, as W3's step 1): the 517e09d
   checkout has no `parity.py` and no `ENGINE_STATE` line, and `candidate.sh` refuses it
   ("no checkout: … measure/parity.py").
3. The corpus cache `/opt/dlami/nvme/w3-corpus` verifies (it did for the W3 sweep), plus
   `sop-synth-v1` built into it for the three 120 s parity clips:
   `CORPUS_CACHE=/opt/dlami/nvme/w3-corpus /opt/pytorch/bin/python3 models/marlin2b/corpus-synth/synth.py build`
   (renders with the pinned ffmpeg already in the cache's `tools/`). If its `verify` fails on
   the box's AMD CPU (the per-CPU byte finding), parity still pairs E0 and E1 by the bytes'
   own sha256, so this is recorded, not fatal; if the clips are absent their rows are
   `missing` and `output_drift` stays unknown, so nothing can be adopted.
4. The installed unit is `marlin2b-vllm` (ExecStart in the installed tree, container
   `marlin2b-8000`), as today.

```bash
# E0 then E1 (then E3 only if decide.py prints e3_trigger=yes for E0 or E1). One at a time.
ssm "mkdir -p /opt/dlami/nvme/w4-logs && nohup bash -c \"$(wrap models/marlin2b/measure/candidate.sh 'CANDIDATE=e0 W4_ENGINE_RESTART_OK=1')\" > /opt/dlami/nvme/w4-logs/e0.log 2>&1 &"
ssm "tail -c 6000 /opt/dlami/nvme/w4-logs/e0.log"     # poll until a restored= line
# the same with CANDIDATE=e1 (and e3); artifacts in /opt/dlami/nvme/w4-e1-<utc>/
# after fetching both directories to research/plan/evidence/w/box/w4-*/ (the coordinator's
# route, as for the W3 sweep):
python3 models/marlin2b/measure/decide.py research/plan/evidence/w/box/w4-e1-<utc> \
  --baseline research/plan/evidence/w/box/w4-e0-<utc>
python3 models/marlin2b/measure/decide.py research/plan/evidence/w/box/w4-e0-<utc>   # e3_trigger
```

| Run | Command | Expected duration | Disruption | Rollback | Outputs |
|---|---|---|---|---|---|
| E0 | `CANDIDATE=e0 W4_ENGINE_RESTART_OK=1 bash candidate.sh` | **est.** 25–50 min: 7 engine starts (start-to-ready unmeasured on this box - I2B bounds it at 900 s; est. 2–5 min each) plus the cells (the W3 sweep's c = 1 cell took 170.04 s of bench wall time, `meas.`; its later cells ran on warm caches, 27–74 s, so the cold cells here will take longer, est.) | the installed engine is stopped from `systemctl stop marlin2b-vllm` until `restored=yes`; its PartOf units stop with it | automatic: the EXIT trap stops the candidate, starts the unit and the PartOf units that were active, waits for `/health` and prints `restored=yes` (same args and image) or `restored=no` + diff (exit 4). Manual, if the script is SIGKILLed: `systemctl start marlin2b-vllm <partof units>`, then compare `docker inspect --format '{{json .Args}}' marlin2b-8000` with the `pre_args=` line | `/opt/dlami/nvme/w4-e0-<utc>/`: `candidate.log`, `startup-*.log`, `engine-*.log`, `engine-errors-*.log`, `capability.txt`, `parity.jsonl`, `c<c>.concurrency.log`, `host-mem-c<c>.tsv`, `sweep/w3-L1-*/{bench.jsonl,raw/,samples.tsv}`; the transcript in `/opt/dlami/nvme/w4-logs/e0.log` |
| E1 | `CANDIDATE=e1 …` | est. as E0 | as E0 | as E0 | `/opt/dlami/nvme/w4-e1-<utc>/` |
| E3 (conditional) | `CANDIDATE=e3 …` | est. as E0 | as E0 | as E0 | `/opt/dlami/nvme/w4-e3-<utc>/` |

Whole window **est.** 1–1.7 h for E0 + E1, 1.5–2.5 h with E3. Open item (d): E1B's L8
`reference.py` run needs the GPU with the engine stopped; it could run inside the same
window between two candidates (the unit is stopped there anyway), but that is the
coordinator's call and not part of `candidate.sh`.

## Failure drill

| Injection point | State before → after | Retry / duplicate | Cleanup |
|---|---|---|---|
| any precondition fails (10 cases) | engine running → unchanged; nothing written | none | none needed |
| the sweep fails at a level (`concurrency.sh` exits nonzero) | unit stopped, candidate running → unit restarted, same args/image, PartOf worker active | `level=<c> sweep_exit=<rc>`, exit 3; the run is re-done whole | the EXIT trap |
| the restored engine comes back with other args | → `restored=no healthy=yes` + `args_diff:`, exit 4 | the coordinator inspects before anything else | manual (table above) |
| a candidate engine never becomes healthy | `start=<label> ready=no after <READY_S>s` + the log tail → exit, restore | re-run | the EXIT trap |
| SIGINT/SIGTERM/SIGHUP to the script | trapped to `exit 130` → restore | — | the EXIT trap; SIGKILL cannot be trapped: manual |
| the engine refuses a clip mid-stream (the box's presentation) | worker: one attempt, `engine_error`, debit 0, platform-absorbed | no second attempt | none |

## Artifacts

| Path | Contents |
|---|---|
| `models/marlin2b/measure/W4-protocol.md` | the predeclared protocol (item 1, flag table §3) |
| `models/marlin2b/measure/candidate.sh` | the box script (item 3) |
| `models/marlin2b/measure/decide.py` | the rule, the criteria, `--ceiling`, `--overheads` (item 4) |
| `models/marlin2b/measure/parity.py` | the parity client (item 5) |
| `models/marlin2b/measure/concurrency.sh` | one line: `--engine-state "${ENGINE_STATE:-warm}"` (ruling 1) |
| `apps/infrx-api/infrx/worker/fakes.py` | additive: fault `engine_error_before_content`, `ENCODER_REFUSAL` |
| `apps/infrx-api/tests/w/test_w4.py`, `w4_mutants.py`, `test_w4_mutants.py` | 14 cases, 52 mutants, the list's checks |

Raw logs of this session: the session scratchpad `logs/` (not committed); tails quoted above.

## Changes

Owned paths only: `git diff --stat a237d6f..HEAD` touches `models/marlin2b/measure/`
(four new files + the ruled line), `apps/infrx-api/infrx/worker/fakes.py` (additive),
`apps/infrx-api/tests/w/` (three new files) and this report. No public signature of
`WorkerService`, `WorkerService.reap_once`, `WorkerLoop`, `AttemptRunner(relay=…)` or
`VllmEngine` changed. `serve.sh`, `serving-version.json`, `test_serving.py` and
`w3_mutants.py` are untouched (phase B). Migration: none. Ports: none. Deploy: none;
`candidate.sh` is run by the coordinator only.

## Limits

1. **Nothing is measured here.** Every W4 verdict waits for the coordinator's E0/E1 run;
   the only numbers are W3's sweep read through `decide.py` and the arithmetic of the
   pinned processor. No setting is proposed for adoption.
2. **Direct engine only.** W4.c's gateway trials - sustained, burst, cancellation and soak
   (E1B L2–L5, L7) - are **pending I2B's rollout and G2/D4/D5**; no direct-engine cell stands
   in for them.
3. **Cold cells, one GPU.** Every level runs on a freshly started engine (`restarted`), so
   E0 and E1 compare cold-to-cold; the served steady state is warmer. At c = 32 each clip is
   sent twice (`n = 128` over 64 clips), so up to half of that level can hit the caches
   (`decide.py` prints `repeats`). One L40S; no statement about other GPUs.
4. **Memory growth** is testable only in the c = 32 cell (the only one that repeats its
   clips), GPU from `samples.tsv`, host from the engine container's `docker stats`; across
   cells it is not applicable under a per-level restart. The cross-time test is the soak
   (E1B L7), pending.
5. **Parity is not accuracy** (P-07). Newly accepted clips (the 112 s and 120 s ones) are
   checked for the pinned processor's count and the text overhead band only; their
   event-level parity against `reference.py` is E1B's L8 cell, **pending E1B**.
6. **The processor arithmetic is reproduced, not run from the image.** `decide.py`'s
   `smart_resize` is a stdlib reproduction of `transformers`' Qwen3-VL video resize (vLLM
   imports it, `qwen3_vl.py:46-47`); it is checked against every measured count on the box
   (three refusal counts exactly; 60 accepted clips' prompt tokens with a residual that
   depends on the frame count only), but the image's `transformers` source was not read.
   The frame-sampling margin of the interim ceiling (up to two frames fewer than the
   budget) is an assumption that makes 82 s rather than 83 s the proposal.
7. **Criteria are provisional (P-18)** except W3's rule (a W3 engineering rule) and the
   methods; cost is not computed (P-19).
8. **Checks not run here:** `make check` (its `api-test` and `api-mutants` run `tests/d`
   and D's lists, which start D's harness containers - forbidden by the dispatch; the
   coordinator's `make check` covers them), console targets (no `apps/app` change).

## Handback

**Next unblocked:** the coordinator's box window (E0, E1, conditional E3) with the SSM block
above; then W4 phase B in a later dispatch with the committed outputs.

**Integration requests:**

1. **Coordinator (Makefile)** - add the list to `api-mutants`:
   ```diff
   --- a/Makefile
   +++ b/Makefile
   @@ -16,7 +16,7 @@
    # suite runs a subset; a surviving mutant is a failed suite either way.
    # Track mutant lists join here as their task merges (M1, Q1, J1, W1, T1, D1, G1 — D's list needs Docker and skips visibly without it); each gates on INFRX_MUTANTS.
    api-mutants:
   -	cd $(API) && INFRX_MUTANTS=all uv run --frozen pytest -q tests/contracts/test_mutants.py tests/m/test_mutants.py tests/q/test_mutants.py tests/q/test_valkey_mutants.py tests/q/test_reconcile_mutants.py tests/j/test_mutants.py tests/w/test_mutants.py tests/w/test_loop_mutants.py tests/w/test_w3_mutants.py tests/t/test_trace_mutants.py tests/d/test_migration_mutants.py tests/d/test_code_mutants.py tests/d/test_code_mutants_d3.py tests/d/test_signup.py tests/g/test_mutants.py tests/g/ops/test_mutants.py tests/i/test_mutants.py
   +	cd $(API) && INFRX_MUTANTS=all uv run --frozen pytest -q tests/contracts/test_mutants.py tests/m/test_mutants.py tests/q/test_mutants.py tests/q/test_valkey_mutants.py tests/q/test_reconcile_mutants.py tests/j/test_mutants.py tests/w/test_mutants.py tests/w/test_loop_mutants.py tests/w/test_w3_mutants.py tests/w/test_w4_mutants.py tests/t/test_trace_mutants.py tests/d/test_migration_mutants.py tests/d/test_code_mutants.py tests/d/test_code_mutants_d3.py tests/d/test_signup.py tests/g/test_mutants.py tests/g/ops/test_mutants.py tests/i/test_mutants.py
    
    console-test:
    	cd apps/app && pnpm test
   ```
2. **Coordinator / G2 (P-20)** - interim ceiling **82 s** (`decide.py --ceiling 16384`),
   binding as `MAX_VIDEO_SECONDS` in the coordinator's configuration (read by
   `infrx/config.py:99`, M's `MediaProfile.pinned`, the legacy validator; G2 does not encode
   it, per P-20); the applied 72 s is also safe. Decision A/B: pending E0/E1 through
   `decide.py` (the rule above).
3. **Coordinator (`limits.py` / 08 §5)** - `ENGINE_MAX_NUM_SEQS`, `WORKER_CONCURRENCY`
   defaults: **none now** (no `c*` measured); phase B, from `decide.py`'s `adopt` line.
4. **I2B** - `50-install.sh` `INFRX_SET`: no new value now (the cutover keeps 32 per
   I2B FC-1); `ENGINE_READY_S` (900 s) to be compared with the measured `start_to_ready_s`
   that `decide.py` prints for E0/E1.
5. **D5 / G6B** - a new `serving_versions` row only if phase B changes the flags (R76/R78);
   none now.
6. **Q / coordinator** - admission caps: phase B, derived by Little's law from the measured
   `T(c*)` (accepted/s) times the queue budget (`QUEUE_WAIT_INTERACTIVE_S` 10 s,
   `QUEUE_WAIT_ASYNC_S` 600 s) plus `c*` in service; no number now.
7. **E1B** - L8 parity (`reference.py`) on the clips E1 newly accepts (`c012`, `c025`,
   `c038`, `c051`, `sop09`/`sop10`/`sop11`), possibly inside the same window (open item
   (d)); the per-CPU byte-reproducibility note stands - parity pairs by the bytes' sha256.
8. **Coordinator (box)** - the three preconditions of the SSM block (window, checkout at
   the integration head, `sop-synth-v1` built).

## Verification log

- 2026-09-23: phase A implemented at `ecacd50` on base `a237d6f`; protocol `4614392`
  committed before the first script `58e2659`. No box contact, no container, no measurement.

## Round 2 — fix round for `W4-review-db12a5a.json` (fix_required: 6 blocking, 21 nonblocking)

The base is unchanged (`a237d6f`) and round 1's head was `db12a5a`. Round 2's commits:
- `c79a6e7`: the fixes, their cases and the new mutants.
- `086a127`: the protocol's log amendment 1, plus two cases sharpened after a pre-check of the new mutants.
- This section.

There was no rebase, reset or amend, nothing was pushed, and no box, AWS or container was touched. The round-1 sections above stay as they were; where round 2 corrects them, it says so here.

### Blocking findings

| Finding | Change | Killing case (what it now asserts) | Mutants (all killed) |
|---|---|---|---|
| **BS-1** inactive unit / second run | `candidate.sh`: `systemctl is-active marlin2b-vllm` must report `active` before anything is recorded. `exec 9>$NVME/w4-candidate.lock; flock -n 9` takes a lock that is released at exit; the candidate engine and the host sampler get `9>&-` | `test_ops_recover__the_candidate_run_refuses_in_flight_work_and_unlisted_flags`, cases `unit-inactive` and `second-run` (the test holds the lock with `fcntl.flock`). Each gives exit 2 and `refused:`, with no `systemctl stop/start`, no `docker run/stop/rm`, and nothing under `$NVME/w4-*` except the lock file | `unit_not_active_accepted`, `second_run_not_locked` |
| **BS-2 / HON-2** untested guards | Cases added. The `set +e` in `restore` now carries a comment saying it is load-bearing | Refusal cases: `not-canonical` (`OUT=$NVME/w4-x/../../escape`), `no-execstart` (`systemctl show` prints an empty ExecStart, as it does for a missing unit), `waiting` (running 0, waiting 3.0). Restore cases, each ending in `restored=no` and exit 4: `image` (the unit comes back on another image; prints `image_diff:`), `unhealthy` (same args and image, `/health` never answers; prints `healthy=no`), `start-fails` (`systemctl start` fails, and the report still prints `healthy=no`) | `out_not_canonical` (M1), `restore_ignores_the_image` (M2), `restore_ignores_health` (M3), `restore_under_set_e` (M4), `waiting_not_counted` (M6), `empty_execstart_accepted` (M7) |
| **D1** warm cells adopted | `decide.criteria`: any level whose `profile.engine_state` is not `restarted` makes `w3_rule`, `severe_tail` and the paired criteria `unknown`. `c*` is still printed as a computed figure | `test_perf_envelope__cells_at_different_cache_states_are_not_compared` now also runs one warm level (c = 4) on both sides of otherwise restarted runs. It expects `w3_rule` unknown with "not restarted [4]", and the tail and paired criteria unknown. `passing_pair` is relabelled `restarted`, which is what candidate.sh produces. `test_engine_opt__the_050411Z_sweep_has_no_qualifying_level` asserts that the rule is `unknown` on the warm W3 sweep | `warm_levels_in_the_rule` |
| **D2** truncated raw rows | `Level.reconciled`: the raw rows (before any set-aside) must match the bench row's `attempts`, accepted count and failed count, and accepted + rejected + failed + cancelled must equal `requests`. A mismatch makes overload masking `fail` and the error rate `unknown`. `Level.retries_known`: a raw row without `retries`, or a bench row without `denominators.retried_requests`, makes overload masking `unknown`. The bench row's `retried_requests` is added to the raw retries | `test_engine_opt__a_passing_candidate_is_adopted_and_each_disqualifier_blocks_it`: `rows_lost` (six accepted rows deleted from `raw/c32.jsonl`) gives overload masking `fail`, error rate `unknown`, nothing adopted. `retries_unrecorded` gives `unknown`. The six-failures case now also updates the bench row's counts, so its `fail` comes from reconciled rows | `raw_rows_not_reconciled`, `error_rate_ignores_reconciliation`, `missing_retries_read_as_zero` |
| **D3** three unkillable invariants | New cases only | `test_engine_opt__the_rule_holds_at_its_boundaries`: levels 8 (T = 1.8), 16 (2.0) and 32 (1.9) all qualify, and the case expects `c* = 8`. In the passing case: `oom_line` (a "CUDA out of memory" line in `engine-errors-c16.log`) makes `oom` fail; `gpu_growth` (+300 MiB on the second half of the c = 32 samples) makes `memory_growth` fail | `largest_qualifying_level`, `oom_line_ignored`, `gpu_growth_unchecked` |
| **HON-1** substring flag check | The protocol case now requires each `candidate.sh` flag set to **equal** the protocol's §2 cell. It also checks `decide.LEVELS` against §1 and against `candidate.sh`, and `decide.PAIRS` against §2's baseline column | `test_engine_opt__the_protocol_states_the_criteria_decide_applies` | `e3_flag_dropped` (and `e1_flag_off_protocol`) |

### Nonblocking findings

| Finding | Disposition | Case → mutant |
|---|---|---|
| BS-3 duplicate `run` | The stub docker exits 125 when the name is already in use. The restores case counts **successful** runs (the `runs` file must hold 7) against 7 attempts | restores → `candidate_started_over_the_last_one` |
| BS-4 corpus-synth | `corpus-synth/manifest.json` and `synth.py` are now part of the checkout check. A new `parity.py --check` must find every parity clip in the cache before the stop, otherwise the run is refused with `refused: the parity set is not in the cache`. `synth.py verify` is **recorded** (`synth=` in `candidate.log`), not a refusal: its pinned bytes may not reproduce on the box's CPU (the per-CPU finding), and parity pairs runs by each clip's own sha256 | refusal `parity-missing` → `parity_set_unchecked`; parity client (`--check` exits 1 with 4 of 9 clips present, 0 with all 9) → `parity_check_always_passes` |
| BS-5 environment reaches serve.sh | The launch pins `PORT=8000 GPU=0 WEIGHTS=$NVME/marlin2b VLLM_LOGGING_LEVEL=INFO PROCESSING_CACHE_DIR=` (empty, so no media mount). Every start's `startup-<label>.log` begins with `args=`, the container's own `.Args` | The restores case runs with `PORT=8001 GPU=1 WEIGHTS=<elsewhere> VLLM_LOGGING_LEVEL=DEBUG PROCESSING_CACHE_DIR=<a dir>` and asserts `-p 127.0.0.1:8000:8000`, `device=0`, `$NVME/marlin2b:/model:ro`, `INFO`, and no `--allowed-local-media-path`. The records case asserts the `args=` line → `port_from_the_environment`, `weights_from_the_environment`, `media_root_from_the_environment`, `start_args_unrecorded` |
| BS-6 READY_S | Must match `^[1-9][0-9]*$`; checked alongside the other pure refusals | refusal `ready-s` (`15m`) → `ready_s_unvalidated` |
| BS-7 `__pycache__` in the checkout | `export PYTHONDONTWRITEBYTECODE=1` | records (the stub parity sees the variable) → `bytecode_written_to_the_checkout` |
| BS-8 in-flight check timing | Now the last check: after the corpus and parity checks, just before `$OUT` is created and the unit stopped | the existing in-flight, waiting and metrics-down refusals (mutants unchanged) |
| D4 usage `None` treated as equal | `usage_verdict`: if a clip both sides accepted lacks `prompt_tokens` on either side, the result is `unknown`. `parity_verdict`: an accepted pair without usage is `unknown`. The parity client's rule that an answer without usage is not accepted is now tested | passing `usage_blank` → `blank_usage_compared`; token-or-content drift → `parity_blank_usage_compared`; parity client (a 120 s answer with `[DONE]` but no usage is `failed`) → `accepted_without_usage` |
| D5 floor(X) = 0 adopts 0 | `adopt` now needs `floor(X) >= 1`. The set-aside case uses X = 12.6, which must give 12 | passing `x-below-one` → `setting_below_one_adopted`; set-aside → `floor_x_rounds` |
| D6 unpaired baseline | The baseline may not be the candidate's own run (compared by resolved root). The `(candidate, baseline)` pair, read from each `candidate.log`'s `candidate=` line, must be (e1, e0) or (e3, e1). Otherwise the paired criteria are `unknown` | passing `pair` (a baseline logged as e3) and a report paired with itself → `any_pair_accepted`, `baseline_may_be_the_candidate` |
| D7 missing levels | `w3_rule` is `unknown` unless all of `LEVELS = (1, 2, 4, 8, 16, 32)` are present | passing `level_missing` → `missing_levels_ignored` |
| D8, D9, HON-5 wording | The `decide.py` docstrings now state what `smart_resize` and `worst_tokens` cover: sides ≥ 32 px, and a source with at least F − 2 frames. The protocol's body is not rewritten; its log amendment 1 carries the corrections. **Correction to round 1:** the wording "a residual that depends on the frame count only" (limit 6 and the P-20 record above) is wrong. The residual depends on the frame count and the prompt text: at 4 frames it spans 39–48 tokens across prompts. The band 9.181–9.808 comes from 140 accepted rows, which are 20 distinct clips of ≥ 24 groups seen at every level | — (wording) |
| D10 boundaries | `test_engine_opt__the_rule_holds_at_its_boundaries` pins each edge: T exactly 0.9 × max qualifies and 1.799 does not; 1 % failures is a fail; a tail breach at c* itself fails; of two X lines the smaller is taken (80.70 and 60.10 → 60.1); `c01` does not set aside `c012-…`; a short-class p95 of 3.5 s passes against 1.5 × 2.0 + 1.0; a parity row on different bytes is `unknown` | `threshold_exclusive`, `error_rate_boundary`, `tail_skips_c_star`, `x_is_the_largest`, `set_aside_prefix_without_dash`, `starvation_slack_dropped`, `parity_pairs_by_clip_only` |
| HON-3 refusal assertions | All 17 refusal cases now assert: no `systemctl stop/start`, no `docker run/stop/rm`, nothing under `$NVME/w4-*` except the lock file, and no `escape` or `elsewhere` directory | — (strengthened case) |
| HON-4 protocol log | Appended amendment 1. The calibration helpers ran on the W3 sweep before the protocol commit (protocol at 06:05:35Z, `decide.py` committed at 06:11:16Z). Git order proves only that no W4 script was committed first and that no W4 cell existed | — |
| HON-6 the 82 s ceiling | Stated in the protocol amendment and here: the ceiling holds only for a source with at least F − 2 frames at the budget's rate (about 2 fps or more). A source with far fewer frames gets larger frames and can exceed 16,384 below 82 s. The result is a free `engine_error`, not a wrong answer. Admission does not bound the frame count today; adding that would be M's or S2M's change | — |
| HON-7 abridged block | Round 1's `--set-aside` block above left out its six `level c=` lines (filtered with `grep -v`) without saying so. The round-2 outputs below are unabridged | — |
| HON-8 orderings | Re-run on scratch exports of `a237d6f` and `086a127` in one environment; the diff is below | — |
| HON-9 wording | The refusal case (`test_engine_opt__a_deterministic_engine_refusal_settles_once_and_free`) drives W1's real adapter and the real `AttemptRunner` against `FakeUpstream` and `test_loop.World`'s in-memory store, journal and ledger fakes. The PostgreSQL/Valkey settlement path is covered by D's and Q's suites. The on-wire shape (a role chunk before the SSE error) is inferred from the coordinator's quote and the sweep's raw rows (`http_status` 200, `stream_error_event`, `content_chars` 0). No raw artifact shows whether vLLM actually sends the role chunk first | — |

Findings left as they are, with the reason:
- **Boxsafety reviewer's M10** (the restore's `stop_candidate`): not load-bearing on the box, because the unit's `ExecStartPre=docker rm -f marlin2b-8000` removes the candidate anyway.
- **M11** (record every PartOf unit, not only the active ones): it would start a unit that was not running. Low impact; no case added.
- **Decision reviewer's M13** (no `break` after a stream error): the mutant is equivalent, because the error is recorded and the outcome stays `failed`.
- **The HON-1 refuter's single failure** of the records case at its last assertion (`c16.concurrency.log` starts with `run=w3-L1-stub-c16`): it did not recur in any run here. The assertion now prints the file's head when it fails.

Round 2 adds 40 mutants (52 → 92) and one case (14 → 15). Before `086a127`, a pre-check ran the new mutants in four parallel groups and found two not killed:
- `checkout_not_checked`: the new `parity.py --check` also refused a checkout without `parity.py`, which masked the mutant. The no-checkout case now removes `concurrency.sh` instead, which nothing else checks before the stop.
- `start_args_unrecorded` was `broken_runner`: the case parsed a line it had not asserted.

Both were fixed in `086a127` and re-run: `2/2 killed`.

### Round-2 commands and results

Every run below is at `086a127`, the code this section describes. Tails are quoted from the logs in the session scratchpad.

| Command (from `apps/infrx-api` unless a make target) | UTC | Exit | Tail |
|---|---|---|---|
| `uv run --frozen pytest -q -p no:cacheprovider tests/w/test_w4.py` | 09:31:21Z | 0 | `15 passed in 115.83s (0:01:55)` |
| `uv run --frozen pytest -q -p no:cacheprovider tests/w` | 09:33:40Z | 0 | `179 passed in 510.31s (0:08:30)`. Base `a237d6f` collected 156; the +23 are `test_w4.py` 15 + `test_w4_mutants.py` 8 |
| `INFRX_MUTANTS=all uv run --frozen pytest -q -p no:cacheprovider tests/w/test_w4_mutants.py` (the whole list, detached) | 09:31:01Z | 0 | `94 passed in 1102.37s (0:18:22)`: 92 mutants killed plus the 2 list checks |
| `uv run --frozen python -m tests.w.w4_mutants --list \| tail -1` | — | 0 | `92 mutants over 15 named cases`, the same count the run above killed |
| `INFRX_MUTANTS=all uv run --frozen pytest -q -p no:cacheprovider tests/w/test_w3_mutants.py` (W3, detached) | 09:31:11Z | 0 | `87 passed in 327.57s (0:05:27)` |
| `make bench-test` | 09:33:24Z | 0 | `67 passed in 8.44s` |

**HON-8: both orderings, on base and head, in one environment.** All four runs used the same setup:
- scratch `git archive` exports of `a237d6f` and `086a127`;
- the same venv (`apps/infrx-api/.venv/bin/python`);
- the same `PATH`, with a `docker` shim first that refuses every call, so D's and Q's harnesses skip instead of creating containers;
- the same ignores: `tests/d`, `tests/contracts/v2/test_v1_projection_pg.py`, and the three Q mutant lists whose nested runs get a fixed `PATH=/usr/bin:/bin` and would reach the real docker (`tests/q/test_mutants.py`, `test_valkey_mutants.py`, `test_reconcile_mutants.py`).

There is no `-k` deselection this time, because the Valkey cases skip on the shim. The script is `orderings2.sh <export> <label>` in the session scratchpad:
- legacy-first: `python -m pytest -q -p no:cacheprovider <ignores>`;
- track-first: `… tests/g tests/i tests/j tests/m tests/q tests/t tests/w tests/contracts tests/test_app_factory.py tests/test_gateway_auth.py tests/test_inflight.py tests/test_media.py <ignores>`.

| Run | Started (UTC) | Exit | Tail |
|---|---|---|---|
| base `a237d6f` legacy-first | 09:23:23Z | 1 | `37 failed, 2478 passed, 85 skipped, 2 warnings in 790.73s (0:13:10)` |
| base `a237d6f` track-first | 09:36:38Z | 1 | `37 failed, 2478 passed, 85 skipped, 2 warnings in 670.45s (0:11:10)` |
| head `086a127` legacy-first | 09:42:21Z | 1 | `37 failed, 2501 passed, 85 skipped, 2 warnings in 1009.07s (0:16:49)` |
| head `086a127` track-first | 09:59:18Z | 1 | `37 failed, 2501 passed, 85 skipped, 2 warnings in 1007.57s (0:16:47)` |

`diff` of the sorted `FAILED`/`ERROR` id lists printed `identical` for all three comparisons: base legacy against head legacy, base track against head track, and head legacy against head track.

The 37 failures are all pre-existing and all in I's suite:
- `tests/i/test_mutants.py::test_mutant_is_killed` ×36;
- `tests/i/test_packaging.py::test_backend_deploy__the_config_schema_is_every_name_the_runtime_reads`, I's config-schema case (ACCOUNTING_REGIME, ACTIVE_RATE_CARD_VERSION, PROVIDER_DEV_ALLOCATION_CEILING_CREDIT). It sits in the I list's pristine baseline, which is why the 36 mutants fail with it. Owner: I / coordinator.

Head passes 23 more than base (`test_w4.py` 15 + `test_w4_mutants.py` 8). The 85 skips are the same in all four runs: the Valkey and PostgreSQL harness cases, skipping on the shim.

Round 1's quoted orderings (42 failed / 6 errors) differ because they ran with `-k "not valkey"` while a stale `infrx-q3-valkey` container without its port was up. The coordinator has since removed that container, and this environment makes those cases skip instead of wait.

### decide.py at `086a127`, unabridged

```
### python3 models/marlin2b/measure/decide.py research/plan/evidence/w/box/sweep-20260923T050411Z   @ 2026-09-23T09:42:54Z (head 086a127)
run=research/plan/evidence/w/box/sweep-20260923T050411Z baseline=None set_aside=none X=80.7 start_to_ready_s=[]
level c=1 state=warm attempts=64 accepted=60 T=0.353 F=4 {'stream_error_event': 4} failed_clips=c012-bbb1080p30-1024x768-4x3,c025-tos720p-2560x1080-ultrawide-rot180,c038-sintel1080p-480x854-portrait,c051-tos720p-360p-16x9 W=0.0 peak_running=1.0 peak_kv=0.00573436921938586 peak_gpu_mib=44251.0 util_median=100.0 ttft_p95=3.046 latency_p95=6.2907 repeats=0
level c=2 state=warm attempts=64 accepted=60 T=0.812 F=4 {'stream_error_event': 4} failed_clips=c012-bbb1080p30-1024x768-4x3,c025-tos720p-2560x1080-ultrawide-rot180,c038-sintel1080p-480x854-portrait,c051-tos720p-360p-16x9 W=0.0 peak_running=2.0 peak_kv=0.007029226785053688 peak_gpu_mib=44251.0 util_median=100.0 ttft_p95=1.7052 latency_p95=5.5045 repeats=0
level c=4 state=warm attempts=64 accepted=60 T=1.301 F=4 {'stream_error_event': 4} failed_clips=c012-bbb1080p30-1024x768-4x3,c025-tos720p-2560x1080-ultrawide-rot180,c038-sintel1080p-480x854-portrait,c051-tos720p-360p-16x9 W=0.0 peak_running=4.0 peak_kv=0.012393636699962962 peak_gpu_mib=45139.0 util_median=100.0 ttft_p95=2.4332 latency_p95=6.3195 repeats=0
level c=8 state=warm attempts=64 accepted=60 T=1.888 F=4 {'stream_error_event': 4} failed_clips=c012-bbb1080p30-1024x768-4x3,c025-tos720p-2560x1080-ultrawide-rot180,c038-sintel1080p-480x854-portrait,c051-tos720p-360p-16x9 W=0.0 peak_running=8.0 peak_kv=0.02219755826859049 peak_gpu_mib=45139.0 util_median=84.5 ttft_p95=3.773 latency_p95=8.5095 repeats=0
level c=16 state=warm attempts=64 accepted=60 T=2.206 F=4 {'stream_error_event': 4} failed_clips=c012-bbb1080p30-1024x768-4x3,c025-tos720p-2560x1080-ultrawide-rot180,c038-sintel1080p-480x854-portrait,c051-tos720p-360p-16x9 W=0.0 peak_running=13.0 peak_kv=0.03311135775064744 peak_gpu_mib=45139.0 util_median=86.0 ttft_p95=7.0806 latency_p95=13.4532 repeats=0
level c=32 state=warm attempts=128 accepted=120 T=2.376 F=8 {'stream_error_event': 8} failed_clips=c012-bbb1080p30-1024x768-4x3,c025-tos720p-2560x1080-ultrawide-rot180,c038-sintel1080p-480x854-portrait,c051-tos720p-360p-16x9 W=unknown peak_running=24.0 peak_kv=0.05937846836847949 peak_gpu_mib=45139.0 util_median=83.0 ttft_p95=11.7668 latency_p95=22.453 repeats=64
w3_rule c*=None threshold=2.1384 setting=None
criterion w3_rule=unknown (c*=None is not taken: levels missing [], not restarted [1, 2, 4, 8, 16, 32])
criterion error_rate=fail (28/448 = 0.0625)
criterion overload_masking=pass (--retries 0, every attempt accounted)
criterion severe_tail=unknown (not restarted at c=[1, 2, 4, 8, 16, 32])
criterion oom=unknown (no candidate.log (engine exits not recorded))
criterion memory_growth=unknown (c=32 growth unsupported (gpu 0.0, host None))
criterion cancellation=unknown (no capability.sh cancellation line)
criterion short_job_starvation=unknown (no paired baseline)
criterion usage_drift=unknown (no paired baseline)
criterion output_drift=unknown (no paired baseline)
e3_trigger=yes c=[16, 32]
verdict: no setting adopted - failed: ['error_rate']; unknown: ['w3_rule', 'severe_tail', 'oom', 'memory_growth', 'cancellation', 'short_job_starvation', 'usage_drift', 'output_drift']
exit=0

### python3 models/marlin2b/measure/decide.py research/plan/evidence/w/box/sweep-20260923T050411Z --set-aside c012,c025,c038,c051
run=research/plan/evidence/w/box/sweep-20260923T050411Z baseline=None set_aside=c012,c025,c038,c051 X=80.7 start_to_ready_s=[]
level c=1 state=warm attempts=60 accepted=60 T=0.353 F=0 {} failed_clips=- W=0.0 peak_running=1.0 peak_kv=0.00573436921938586 peak_gpu_mib=44251.0 util_median=100.0 ttft_p95=3.046 latency_p95=6.2907 repeats=0
level c=2 state=warm attempts=60 accepted=60 T=0.812 F=0 {} failed_clips=- W=0.0 peak_running=2.0 peak_kv=0.007029226785053688 peak_gpu_mib=44251.0 util_median=100.0 ttft_p95=1.7052 latency_p95=5.5045 repeats=0
level c=4 state=warm attempts=60 accepted=60 T=1.301 F=0 {} failed_clips=- W=0.0 peak_running=4.0 peak_kv=0.012393636699962962 peak_gpu_mib=45139.0 util_median=100.0 ttft_p95=2.4332 latency_p95=6.3195 repeats=0
level c=8 state=warm attempts=60 accepted=60 T=1.888 F=0 {} failed_clips=- W=0.0 peak_running=8.0 peak_kv=0.02219755826859049 peak_gpu_mib=45139.0 util_median=84.5 ttft_p95=3.773 latency_p95=8.5095 repeats=0
level c=16 state=warm attempts=60 accepted=60 T=2.206 F=0 {} failed_clips=- W=0.0 peak_running=13.0 peak_kv=0.03311135775064744 peak_gpu_mib=45139.0 util_median=86.0 ttft_p95=7.0806 latency_p95=13.4532 repeats=0
level c=32 state=warm attempts=120 accepted=120 T=2.376 F=0 {} failed_clips=- W=unknown peak_running=24.0 peak_kv=0.05937846836847949 peak_gpu_mib=45139.0 util_median=83.0 ttft_p95=11.7668 latency_p95=22.453 repeats=60
w3_rule c*=16 threshold=2.1384 setting=16
criterion w3_rule=unknown (c*=16 is not taken: levels missing [], not restarted [1, 2, 4, 8, 16, 32])
criterion error_rate=pass (0/420 = 0.0)
criterion overload_masking=pass (--retries 0, every attempt accounted)
criterion severe_tail=unknown (not restarted at c=[1, 2, 4, 8, 16, 32])
criterion oom=unknown (no candidate.log (engine exits not recorded))
criterion memory_growth=unknown (c=32 growth unsupported (gpu 0.0, host None))
criterion cancellation=unknown (no capability.sh cancellation line)
criterion short_job_starvation=unknown (no paired baseline)
criterion usage_drift=unknown (no paired baseline)
criterion output_drift=unknown (no paired baseline)
e3_trigger=yes c=[16, 32]
verdict: no setting adopted - failed: []; unknown: ['w3_rule', 'severe_tail', 'oom', 'memory_growth', 'cancellation', 'short_job_starvation', 'usage_drift', 'output_drift']
exit=0

### python3 models/marlin2b/measure/decide.py --ceiling 16384; python3 models/marlin2b/measure/decide.py --ceiling 32768; python3 models/marlin2b/measure/decide.py --overheads research/plan/evidence/w/box/sweep-20260923T050411Z
duration_s=80 frames=160 worst_video_tokens=15760
duration_s=81 frames=162 worst_video_tokens=16038
duration_s=82 frames=164 worst_video_tokens=16154
duration_s=83 frames=166 worst_video_tokens=16434
duration_s=84 frames=168 worst_video_tokens=16548
ceiling_s=82 budget_tokens=16384
duration_s=118 frames=236 worst_video_tokens=23128
duration_s=119 frames=238 worst_video_tokens=23443
duration_s=120 frames=240 worst_video_tokens=23520
ceiling_s=120 budget_tokens=32768
clips=140 per_group_min=9.181 per_group_max=9.808
```

### What changes for the coordinator's box run

The SSM block above still applies. Before the stop, `candidate.sh` now also enforces:
- the unit must be `active`, and no other `candidate.sh` may hold `$NVME/w4-candidate.lock`;
- every parity clip must be in the cache, so precondition 3 (`sop-synth-v1` built) is now checked, not only stated;
- `READY_S`, if set, must be a whole number of seconds.

The candidate engine always serves the unit's weights at `$NVME/marlin2b` on `127.0.0.1:8000`, GPU 0, whatever the SSM environment carries. `decide.py` must now be given the predeclared baseline: `decide.py <w4-e1-*> --baseline <w4-e0-*>`, and `<w4-e3-*> --baseline <w4-e1-*>`.

### Round-2 verification log

- 2026-09-23: fix round for `W4-review-db12a5a.json`; head `086a127` plus this section. Status unchanged: implemented, measurement pending the coordinator's box run.

## Round 3 — confirmation `W4-confirm-b0a44a0.json` (box-safety passed; decision: 2 blocking)

Everything for this round is in one code commit, `9490e7c`, plus this section. There was no rebase, reset, amend or push, and no box, Docker or AWS.

| Finding | Change | Killing case | Mutants (all killed) |
|---|---|---|---|
| **DEC-R2-1** (blocking) only the attempts clause of `Level.reconciled` was killable | Cases only: one per clause | The passing case adds three variants, each checking one reconciliation clause:<ul><li>`failed_miscounted`: six raw accepted rows relabelled `cancelled` while the bench row moves accepted −6 / failed +6.</li><li>`accepted_miscounted`: the bench row's accepted −1 / cancelled +1, raw rows unchanged.</li><li>`requests_miscounted`: the bench row's `rejected` = 1.</li></ul>Each must give overload masking `fail`, error rate `unknown`, and no adoption | `reconcile_ignores_failed`, `reconcile_ignores_accepted`, `reconcile_ignores_requests` |
| **DEC-R2-2** (blocking) the bench row's `denominators.retried_requests` clauses were unkilled | Cases only | Two new variants:<ul><li>`bench_retried` (`retried_requests=1` at c = 2) must give overload masking `fail`.</li><li>`bench_retries_unrecorded` (the key removed) must give `unknown`.</li></ul> | `bench_retries_ignored`, `bench_retries_absent_read_as_zero` |
| DEC-R2-3 wording | Protocol log **amendment 2**: a level not `restarted` makes the rule, the tail and the paired criteria `unknown`; a level missing from the candidate makes the rule `unknown`; the paired criteria need all six levels on both sides; the tail is taken over the levels present at or below `c*` | — | — |
| DEC-R2-4 a baseline short of levels | `decide.criteria`: the paired criteria are `unknown` unless both the candidate and the baseline have all six levels | passing `baseline_level_missing` | `baseline_levels_unchecked` |
| DEC-R2-5 `setting=16` printed next to a rule not taken | The report line now reads `setting=16 (not taken)` when `w3_rule` is not `pass` | the set-aside case asserts it | `setting_printed_as_taken` |
| BX-N1 lock released at exit | The restores case runs a second, clean `candidate.sh` on the same box, which must exit 0 with `restored=yes` | restores | `lock_by_file_existence` |
| BX-N2 `READY_S=0` | New refusal case `ready-s-zero` | refuses | `ready_s_zero_accepted` |
| BX-N3 fd 9 | The stub docker records `/proc/self/fd` on every `run`; the case asserts fd 9 is never inherited | restores | `lock_fd_inherited` |
| BX-N4 stub fidelity | The stub curl answers only `http://127.0.0.1:8000/…`. The stub docker answers only the exact formats, and `{{.Config.Image}}` returns a different value | restores | `health_on_another_port` |
| BX-N5 `ENGINE_MAX_NUM_SEQS` from the environment | The restores case's hostile environment now also sets `ENGINE_MAX_NUM_SEQS=8` | restores | `seqs_from_the_environment` |

All twelve new mutants were pre-checked before the full run with `python -m tests.w.w4_mutants <the 12>`, which printed `12/12 killed`.

**Runs at `9490e7c`.** Tails are quoted from the logs in the session scratchpad (`w4-lane/`).

| Command (from `apps/infrx-api`) | UTC | Exit | Tail |
|---|---|---|---|
| `uv run --frozen pytest -q -p no:cacheprovider tests/w/test_w4.py` | 11:18:57Z | 0 | `15 passed in 112.54s (0:01:52)` |
| `INFRX_MUTANTS=all uv run --frozen pytest -q -p no:cacheprovider tests/w/test_w4_mutants.py` (the whole list, detached) | 11:18:49Z | 0 | `106 passed in 1269.19s (0:21:09)`: 104 mutants killed plus the 2 list checks |
| `uv run --frozen python -m tests.w.w4_mutants --list \| tail -1` | — | 0 | `104 mutants over 15 named cases`, the declared count |
| `INFRX_MUTANTS=all uv run --frozen pytest -q -p no:cacheprovider tests/w/test_w3_mutants.py` (detached) | 11:18:49Z | 0 | `87 passed in 311.37s (0:05:11)` |
| `uv run --frozen pytest -q -p no:cacheprovider tests/w` (detached) | 11:18:49Z | 0 | `179 passed in 548.38s (0:09:08)` |

`decide.py … sweep-20260923T050411Z --set-aside c012,c025,c038,c051` now prints `w3_rule c*=16 threshold=2.1384 setting=16 (not taken)`. The rest of its output is unchanged from round 2's block.

### Round-3 verification log

- 2026-09-23: confirmation round for `W4-confirm-b0a44a0.json` at `9490e7c`. Status unchanged: implemented, measurement pending the coordinator's box run.

## Round 4 — re-confirmation `W4-reconfirm-ba95e42.json` (one blocking gap, tests only)

Commit `ec652e8` (tests and mutants only). **DEC-R3-1:** the passing case's `level_missing` variant (candidate c = 1 relabelled 3) also asserts `usage_drift` `unknown` with "not all six levels". New mutant `paired_levels_candidate_unchecked` drops `missing or`; `baseline_levels_unchecked` stays as the baseline-half mutant. Nonblocking, folded in:
- DEC-R3-N1: the adopted report prints `setting=16` with no suffix. Mutant: `setting_always_printed_not_taken`.
- DEC-R3-N2: the stub docker's `stats` branch records its fds; each stub level waits, bounded, for the first sample; the case asserts fd 9 is absent. Mutant: `sampler_holds_the_lock`.
- DEC-R3-N3: new `attempts_over_requests` variant (one extra rejected raw row, bench `attempts` + 1 and `rejected` = 1) must fail overload masking.
- DEC-R3-N4: `READY_S=00` is refused.

A pre-check of the new and related mutants printed `8/8 killed`. Runs at `ec652e8`:

| Command | Exit | Tail |
|---|---|---|
| `uv run --frozen pytest -q -p no:cacheprovider tests/w/test_w4.py` (12:21:03Z) | 0 | `15 passed in 126.44s (0:02:06)` |
| `INFRX_MUTANTS=all uv run --frozen pytest -q -p no:cacheprovider tests/w/test_w4_mutants.py` (12:21:03Z) | 0 | `109 passed in 1337.98s (0:22:17)`: 107 mutants plus the 2 list checks. `--list` prints `107 mutants over 15 named cases` |
