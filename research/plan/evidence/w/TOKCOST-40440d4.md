# TOKCOST — cutting the per-video-job preparation latency without weakening R105

| Field | Value |
|---|---|
| Task | **TOKCOST**: the preparation loop's exact-count step (`POST /tokenize` on the real vLLM engine, `infrx/worker/preparation.py`) took **16.8 s for a 5 s clip** on the pilot box (worker journal: `prepared 5c4e15c4…: 1211 prompt tokens (engine /tokenize, 16803 ms)`; the whole request 20.5 s, text jobs' tokenize 4–6 ms). Cut it without weakening R105 (the stored count is the engine's tokenization of the exact body, or none) or R104 (the count travels with `prepared`) |
| Status | **implemented** (item 1, the count memo); items (b), (c), (d) investigated - (b) needs no code and has a serve.sh finding (no flag can do it on the pinned build), (c) and (d) found nothing worth changing. **Not integrated, not deployed**: nothing ran on the pilot box, AWS or hosted Supabase |
| Owner/session | Opus 5.5 implementation lane (TOKCOST), 2026-09-24 |
| Base SHA | `2d4a88b` (origin/main) |
| Implementation SHA | **`40440d4`** (item 1 `4ffa351` + its I2B-R4 stub follow-up `40440d4`; this report is committed after it) |
| Branch / worktree | `codex/tokcost` in `.claude/worktrees/codex-tokcost` |
| Oracles | R104, R105, `BACKEND-JOURNEY` (E3B's journeys: the count is the engine's) |

## What the engine does (b): read at the pinned build, measured nothing

The pinned image is `vllm/vllm-openai@sha256:4cbfd34a…` = vLLM commit
`a8d1aa9c99b8698a2a78b611b7a10c30e6b3995b` (`serving-version.json` `vllm_build_commit`). The
source was read at that commit (raw GitHub); line numbers are that commit's.

1. **`/tokenize` always skips the chat route's multimodal cache - hard-coded, no request field
   or flag changes it.** `vllm/entrypoints/serve/tokenize/serving.py:84-92`
   (`create_tokenize`, `TokenizeChatRequest`): `online_renderer.preprocess_chat(...,
   skip_mm_cache=True)`; the completion form (`:94-99`) likewise. `TokenizeChatRequest`
   (`serve/tokenize/protocol.py:46-114`) has no cache field (`media_io_kwargs`,
   `mm_processor_kwargs`, `chat_template_kwargs`, `tools`, nothing else).
2. **With `skip_mm_cache` the renderer uses a second processor with its own cache.**
   `vllm/renderers/base.py:165-176` builds `_readonly_mm_processor` over
   `mm_registry.processor_only_cache_from_config(config)` ("so that tokenize-only requests
   don't pollute the sender cache"), and `base.py:856-859` picks it whenever
   `skip_mm_cache`. `vllm/multimodal/registry.py:316-325` returns a **new**
   `MultiModalProcessorOnlyCache` for it whatever `--mm-processor-cache-type` says, so the
   tokenize cache and the chat route's sender cache (`lru`, `config/multimodal.py:177,189`:
   4 GiB) are two objects: no flag makes one serve the other. `--mm-processor-cache-gb 0`
   removes the read-only processor (tokenize then uses the chat processor with no cache at
   all) - worse, not shared.
3. **Even a cache hit does not skip the decode.** The clip is fetched and decoded while the
   messages are parsed, before any processor or cache sees it:
   `vllm/entrypoints/chat_utils.py:1386-1395` (`_video_with_uuid_async`: `fetch_video_async`
   whenever a URL is given) → `multimodal/media/connector.py:460-462` (a `file://` URL,
   `_load_file_url` in the global thread pool) → `multimodal/media/video.py:195-201`
   (`load_file` reads the whole file and decodes it). The processor cache
   (`multimodal/processing/processor.py:1415-1449`, `_cached_apply_hf_processor`) is consulted
   only after that, keyed by a hash of the decoded frames (or a per-part `uuid`), and saves
   only the HF processor step. A part with `video_url: null` and a `uuid` skips the fetch,
   but that is a different body (R105 forbids it for the count) and fails when the entry was
   evicted.
4. **Consequence.** On this build a video job is decoded twice (tokenize, then the chat
   route), and nothing in serve.sh or the request can make the second one free. What the
   engine already does for us: a second `/tokenize` of the same clip - even with another
   prompt - hits the read-only processor cache (the HF processor step is skipped; the decode
   is not).
5. **The 16.8 s is not explained by the source.** Both routes run the same parse/decode
   (`chat_utils`, `connector`, `video.py`) and the same processor code on the same
   single-worker `_mm_executor` (`base.py:117`, "must stay single-worker") with the same
   torch thread count (`set_default_torch_num_threads()`: `OMP_NUM_THREADS`, else 1;
   `utils/torch_utils.py:295-310`, serve.sh sets none). Yet on the pilot the chat route's own
   decode + processing + prefill + 96 output tokens fit in the ~3 s left of the 20.5 s
   request, and the concurrency sweep's c = 1 video TTFT p50 was 0.68 s. So the tokenize call
   was ~5-25x the same work on the chat route. Hypotheses, for the coordinator to measure
   (request 1): **H1** a first-real-video cold path (the warmup `warmup_mm` runs dummy inputs
   through both processors, `base.py:263-300`, but no real decode: the first file decode and
   its library loads land on the first video request - here the tokenize); **H2** queueing on
   the single `_mm_executor` behind another multimodal request; **H3** the smoke's clip (a
   public sample mp4, resolution unknown here) decoding slowly - H3 alone does not explain
   the asymmetry, since the chat route decoded the same file again.

## Item 1 (a): the count memo (`4ffa351`, `40440d4`)

`infrx/worker/preparation.py`:

* **`CountMemo`** keeps the engine's **checked** count of a video body in the worker process:
  `get`/`put` on an `OrderedDict`, at most `MEMO_ENTRIES` = 1024 (least recently used out
  first; a key and an int, ~200 bytes each) and none older than `ttl_s` since the engine
  answered - the product's is `PROCESSING_CACHE_TTL_S` (7 days), so the memo never outlives
  the retention of the media it counted.
* **`memo_key(work, prepared, ask)`** = sha256 of the canonical JSON of: the job's
  `model_revision`; its pinned `serving_version_id` in the CREDIT regime (`CreditWork` now
  carries it on `PreparedWork`: a CREDIT job's requested model can be an alias, which moves
  to another serving revision, so `model_revision` alone is not the revision); every prepared
  media digest at its profile (`<sha256>@<profile>`, the whole digest: the file path carries
  16 hex of it); and **the exact `/tokenize` body** (`tokenize_body`, the function
  `engine_prompt_tokens` itself asks with: the served model, the messages
  `VllmEngine.upstream_body` rebuilt with the tenant's own `file://<root>/<org>/<profile>/
  <digest16>/source.<ext>`, the generation prompt, the pinned `mm_processor_kwargs`).
* **`PreparationRunner._prepare`**: for a job with media, the key is built after M's
  `prepare` (the local file exists) and before the count; a hit is the count, a miss asks
  the engine (`_ask`, the unchanged `engine_prompt_tokens` call - E3B's `e3bm79` anchor is
  kept byte for byte) and memoizes the answer only after every check passed (an unchecked,
  failed or timed-out answer raises before `put`). A text job is never memoized: 4-6 ms on
  the pilot, and the worker holds no digest of a text prompt. The INFO line says which:
  `prepared <job>: <n> prompt tokens (engine /tokenize, <ms> ms)` or `… (memo of engine
  /tokenize, <ms> ms)` - a hit is never reported as an engine latency.

**Why R105 holds.** R105: "the serving engine's own tokenization of the exact chat body the
attempt will send". A memoized count is exactly that: the engine's answer, after the same
checks, to a `/tokenize` body equal byte for byte (canonical JSON) to the one this attempt
would send, for media whose whole digests are equal (the bytes are the same: the cache path
is content-addressed, replaced only by a rename of the same bytes, and re-verified by digest
when another process wrote it),
under the same serving revision. vLLM's tokenization of a byte-identical body over identical
bytes is deterministic (template rendering; frame sampling from the container's own
metadata, `np.linspace`, `qwen3_vl.py:1076-1100`), so asking again returns the same number
at 16.8 s. "Or there is none" is unchanged: nothing is memoized that the engine did not
answer and the checks did not pass. **R104** is unchanged: the count still reaches the store
only with `prepared(lease, refs, prompt_tokens=count)` (the store's own bounds apply to a
memoized count exactly as to a fresh one).

**Why it never answers for another serving revision.** Twice over: (1) the key carries the
job's revision (`model_revision`, and in CREDIT the pinned `serving_version_id`) - the
`revision` variant and the key case below; (2) the memo lives in the worker process, and
`infrx-worker.service` is `PartOf=marlin2b-vllm.service` (pinned by `tests/i/test_packaging.py`
and I's mutant), while "a running engine keeps its old image, flags and mounts until it
restarts" (`install.sh`): a new serving revision needs an engine restart, which systemd
propagates to the worker, which empties the memo. Limit 2 names the one gap.

**Never across tenants.** The `file://` path in the body names the organization, so two
organizations sending the same clip and prompt have two keys (the `organization` variant).

## Items (c) and (d): no needless serialization found

* **(c) Overlap `/tokenize` with the attach wait or the staging - not possible in this
  loop.** The count's body names the clip by its local file (`file://` under
  `PROCESSING_CACHE_DIR`, R61 (2)), which only M's `prepare` writes; `prepare` needs the
  durable attach to know which staged object is the job's (R99 (c)). So the order attach →
  prepare → tokenize is a data dependency, not a needless serialization. Inside M's
  `prepare` the durable `prepared` object is written (S3 HEAD + PUT) before the local file;
  writing the local file first would let the tokenize overlap that PUT (the "durable before
  `prepared`" rule would still hold: `prepared` is called after both). The gain is one S3 PUT
  of the clip (~0.1-1 s est. for ≤ 64 MiB in-region, ⚠️ TO BE VERIFIED) against 16.8 s, in
  M's file: request 3, not done here.
* **(d) The attach poll** (`ATTACH_POLL_S` = 50 ms, first check immediate) adds at most 50 ms
  when the attach is late - negligible against the count. The worker loop's idle poll and the
  relay's drain are also 50 ms (`loop.py:62`, `reconcile.py:65`). No change.
* **Not done (ponytail):** a single-flight for two identical bodies in flight at once (both
  miss and both ask; a duplicate submitted concurrently is rare - E1B's protocol cycles
  distinct clips); a durable memo (Limit 1).

## Expected saving on the box (the coordinator measures)

| Job | Before | After | Reasoning |
|---|---|---|---|
| video, the same body again (same organization, clip bytes, prompt, revision) within the worker's life, 7 days and the last 1024 video bodies | one `/tokenize` = 16.8 s measured for a 5 s clip | ~1 ms (a hash and a dict lookup; 0.7-1.4 ms locally) | the whole engine call is skipped, and the engine's single multimodal thread does one decode + processing less, which the chat route's preprocessing of other jobs no longer queues behind (H2) |
| video, first time (or another prompt, clip, org, revision) | 16.8 s | unchanged | R105: only the engine may count a body it has not counted; the engine's own read-only processor cache already skips the HF step for a known clip with a new prompt (finding 4) |
| text | 4-6 ms | unchanged | never memoized |
| a job re-prepared by the same worker after an attempt whose count arrived but whose `prepared` did not (its lease lost meanwhile) | 16.8 s again | ~1 ms | the retry's body is the same body; rare - an attempt refused before the count (the tokenizer down, a drain during `/tokenize`) memoized nothing and asks again |

Where repeats happen: the dataset/bench clients re-sending a corpus (E1B's protocol runs the
same clips at every concurrency level, the same prompt), a client's retries under a new key,
the journeys. **E1B/certify through the gateway must count memo hits separately** (`journalctl
-u infrx-worker | grep 'memo of engine /tokenize'`): with repeats, per-job preparation latency
no longer includes the engine's count, and a first-seen clip still does.

Local measurement (E2's fake engine in process, its `/tokenize` held `tokenize_delay_s`; the
`Prep` world of `tests/w/test_prep_worker.py`; one full attempt: claim, `load_work`, M's
`prepare`, the count, `prepared`; `$L/measure-1s.log`, `$L/measure-0s.log`):

| Attempt | held 1.0 s | held 0 s | `/tokenize` calls |
|---|---|---|---|
| video, first | 1003.6 ms | 2.9 ms | 1 |
| video, the same body again | 1.1 ms | 0.8 ms | 1 |
| video, the same body a third time | 0.7 ms | 0.7 ms | 1 |
| video, the same clip, another prompt | 1003.1 ms | 1.3 ms | 2 |
| video, that other prompt again | 1.4 ms | 0.7 ms | 2 |
| text, first | 1002.2 ms | 0.7 ms | 3 |
| text, the same body again | 1002.7 ms | 0.8 ms | 4 |

## Per item: commit → case → mutant (death line, measured with the runner's own copy)

Cases in `tests/w/test_prep_worker.py`; mutants in `tests/w/prep_worker_mutants.py` `MUTANTS`
(the Makefile `api-mutants` line's `test_prep_worker_mutants.py`). Death lines from the
shared runner's copy (`_copy`, `_prepare`, `_pytest --tb=line`; `$L/deaths.log`), path
prefix `tests/w/test_prep_worker.py`:

| Item (commit) | Mutant | Case | Death |
|---|---|---|---|
| 1 memo (`4ffa351`) | `prep_memo_never_consulted` | `a_repeated_video_body_is_counted_by_the_engine_once` | `:317: AssertionError: [{'add_generation_prompt'…` (two `/tokenize` calls) |
| 1 | `prep_memo_never_filled` | same | `:317: AssertionError: [{'add_generation_prompt'…` |
| 1 | `prep_memo_holds_text` | same `[text]` | `:317: AssertionError: [{'add_generation_prompt'…` (one call for two text jobs) |
| 1 | `prep_memo_hit_logged_as_the_engine` | same `[video]` | `:322: AssertionError: ('memo of engine /tokenize', …` |
| 1 | `prep_memo_key_without_the_body` | `a_memo_answers_only_its_own_body_media_and_revision[prompt,organization]` | `:349: AssertionError: (PreparationResult(…` (stored 1337, not the engine's 1000) |
| 1 | `prep_memo_key_without_the_model_revision` | same `[revision]` | `:349: AssertionError: (PreparationResult(…` |
| 1 | `prep_memo_key_without_the_serving_pin` | `the_memo_key_names_the_media_digests_and_the_credit_revision` | `:378: AssertionError: ['17e45f31451…` (three keys, not four) |
| 1 | `prep_memo_key_without_the_media_digests` | same | `:378: AssertionError: ['82991ea77…` |
| 1 | `prep_memo_key_without_the_media_profile` | same | `:378: AssertionError: ['b55d4623f…` |
| 1 | `main_credit_work_drops_the_serving_revision` | same | `:367: AssertionError: PreparedWork(…` (`serving_version_id` None) |
| 1 | `prep_memo_never_expires` | `a_stale_memo_is_asked_again` | `:397: assert (1337, 1337) == (1337, 1000)` |
| 1 | `prep_memo_expiry_exclusive` | same | `:397: assert (1337, 1337) == (1337, 1000)` |
| 1 | `prep_memo_ttl_not_the_retention` | same | `:386: AssertionError: assert inf == 604800.0` |
| 1 | `prep_memo_unbounded` | `the_memo_is_bounded_least_recently_used_first` | `:409: assert (1, 2, 3, 3) == (1, None, 3, 2)` |
| 1 | `prep_memo_evicts_the_recently_used` | same | `:409: assert (None, 2, 3, 2) == (1, None, 3, 2)` |

`40440d4` (item 1's follow-up) changes no product line: I2B-R4's composition case
(`test_worker_main__the_composition_is_the_pilots_stores_and_settings`) hand-builds a
`load_work_credit` answer, which now carries the `pins` every `WorkV2` has, and asserts the
pin reaches the v1-shaped work (`serving_version_id == "sv"`); I2B-R4's whole list is green
at it (R7).

E3B's journey (`tests/integration/backend/test_journey.py`, E3B's file): every journey cell
sends the same clip and prompt, so after the first video cell the box's worker answers a
video body from its memo and the fake engine's `/tokenize` counter does not move. The cell's
`"the worker never asked /tokenize"` assertion now also accepts, **for a video cell only**,
the worker's own log line `prepared <job>: 1337 prompt tokens (memo of engine /tokenize, `
(`counted_by_the_memo`). The text cells still require a `/tokenize` call, and
`prepared_by_the_worker` still requires stored = usage = 1337 for every cell; `e3bm79`
(`count = 1200` in the engine call) is unaffected (text-sync).

## Runs (UTC 2026-09-24; env names only; `$L` = `/tmp/claude-1000/tokcost-tmp/logs`)

Lane env for every run that needs services: `INFRX_D_TASK=d6 INFRX_D2_VALKEY_PORT=55471
INFRX_D2_VALKEY_CONTAINER=infrx-tokcost-valkey INFRX_Q_VALKEY_PORT=55472
INFRX_M_S3_ENDPOINT=http://127.0.0.1:55724 INFRX_M_S3_LOCAL_CREDS=1`, `AWS_*` unset,
`TMPDIR=/tmp/claude-1000/tokcost-tmp`; MinIO `infrx-tokcost-minio` (the E2 literals and the
brief's image digest) on 127.0.0.1:55724.

| # | Command (from the API root unless `repo$`) | At | Exit | Tail (log sha256 prefix) |
|---|---|---|---|---|
| R0 | `pytest -q tests/w/test_prep_worker.py -k "not _pg__"` | base `2d4a88b` | 0 | `36 passed, 4 deselected in 5.08s` (the baseline) |
| R1 | lane env `pytest -q -rfEs tests/w` (whole) | `4ffa351` | 1 | `4 failed, 260 passed, 2 skipped in 629.24s`: I2B-R4's `test_worker_main__the_composition_is_the_pilots_stores_and_settings` - its hand-built `load_work_credit` answer had no `pins` (`AttributeError` at `__main__.py:99`), and the three `test_worker_main_mutants.py` cases whose pristine baseline is that case → fixed in `40440d4` (`4e90d92d`) |
| R2 | the same | **`40440d4`** | 0 | **`264 passed, 2 skipped in 659.89s`** (the 2: the lists' empty PG parametrizations) (`321ea408`) |
| R3 | lane env `INFRX_MUTANTS=all pytest -q -rfEs tests/w/test_prep_worker_mutants.py` (my list, all: 65 service-free = PREP-WORKER's 50 + these 15, and the 8 PostgreSQL) | `40440d4` | 0 | **`78 passed in 611.20s`** (`67a5f2fc`) |
| R3a | `python -m tests.w.prep_worker_mutants <the 15>` | `4ffa351` | 0 | `15/15 killed` |
| R3b | death lines (`$S/deaths.py`: the runner's `_copy`, `_prepare`, `_pytest --tb=line`) | `40440d4` | - | 15/15 assertion deaths, the table above (`17fc421a`) |
| R4 | repo$ `INFRX_E2_NAMESPACE=e4b run.py --layer 2 --keep` | `40440d4` | 0 | `exit 0 (all stages passed)`: preflight, services, migrate, rls (731) (`a84611fb`) |
| R5 | repo$ e4b `pytest -q -rfEs tests/integration/backend/test_journey.py` | `40440d4` | 0 | **`13 passed, 3 skipped in 47.06s`** (the 3: the stage's PostgREST, layer 3 only); the box worker's log: 11 `(engine /tokenize, …)` lines and **5 `(memo of engine /tokenize, 0 ms)`** - the five video cells after the first (`f338338d`; the worker log copied to `$L/R5-journey-worker.log`) |
| R6 | repo$ e4b `tests/integration/mutants.py --layer 2 --only e3bm79` | `40440d4` | 0 | `e3bm79` **killed** (`test_backend_journey[text-sync]`, `test_journey.py:204: AssertionError`) (`fbda1264`) |
| R7 | lane env `INFRX_MUTANTS=all pytest -q -rfEs tests/w/test_worker_main_mutants.py` (I2B-R4's list: its test and `__main__.py` changed) | `40440d4` | 0 | `22 passed in 112.53s` (13 + 4 PostgreSQL mutants killed) |
| R8 | anchors: every mutant of every `tests/<track>` list anchored in `state/jobstore.py`, `worker/__main__.py`, `worker/preparation.py` (105), and E3B's list in them (5) | `40440d4` | - | 0 moved (`$S/anchors.py`) |
| R9 | `$S/measure.py 1.0` / `0` (the local measurement above) | `40440d4` | 0 | the table above (`8d7c78c5` / `d4a8435a`) |

`$S` = `/tmp/claude-1000/tokcost-tmp/scripts`. Not run: `make api-test` whole (the change is
the preparation runner, `PreparedWork`'s optional field and `CreditWork`; every suite that
reads them is in `tests/w`, `tests/d/test_lease_units.py` constructs `PreparedWork` without
the field) and the layer-3 gate (the journeys and `e3bm79` ran on their own, R5/R6).

## Limits

1. **In-process only.** Each worker process has its own memo, empty at start; a second worker
   process (or a restart) counts a body again. A durable memo (Valkey, with a TTL) would need
   an engine-level serving identity in the key that a worker can read - today the worker
   knows only the job's revision, and outside CREDIT that is `model_revision`, which is not
   an artifact identity (R62); in-process, the `PartOf=` lifetime is what closes that gap
   (Limit 2). Request 4.
2. **The legacy regime's revision is `model_revision`.** A job with no pinned
   `serving_version_id` (legacy USD, which the pilot smoke ran) keys on the public
   `model_revision` string, which a new serving revision may keep. The memo then relies on
   the worker's lifetime ⊆ the engine's (`PartOf=`). ⚠️ TO BE VERIFIED on the box: that
   systemd's automatic restart of the engine (`Restart=always` after a crash) also restarts
   the worker - a `systemctl restart` does (PartOf) - and the gap only matters if the
   checkout under `/home/ubuntu/model-inference` was changed to another serve.sh pin while the
   engine kept running and then crashed. The CREDIT regime's key carries the pin and has no
   such gap.
3. **First-seen video bodies are not faster.** R105 requires the engine's count of every new
   body; the engine decodes each video twice per job on the pinned build (finding 4), and no
   serve.sh flag changes that. The 16.8 s itself is unexplained (finding 5; request 1).
4. **A body is exact.** The same clip with another prompt, another organization or another
   revision is counted again (by design); the engine's read-only processor cache then skips
   its HF step, but not the decode.
5. **Two identical bodies in flight** both miss and both ask (no single-flight).
6. **The journeys' `/tokenize` assertion is relaxed for video cells** (E3B's file) to "asked,
   or this job's count was the memo's" - read from the box worker's log, the one place the
   memo's use is recorded.

## Requests

1. **Coordinator (box) - split the 16.8 s** before deciding anything engine-side, with the
   exact body from the journal (`file://` under `PROCESSING_CACHE_DIR`, the pinned
   `mm_processor_kwargs`; `engine_prompt_tokens` builds it): (i) `POST 127.0.0.1:8000/tokenize`
   twice in a row (cold vs warm; the second hits the read-only processor cache, so first −
   second ≈ the HF processor step and the second ≈ fetch + decode + hash); (ii) the same body
   as a chat with `max_tokens: 1`, twice; (iii) the engine log around the smoke (engine start,
   `Readonly multi-modal warmup completed in …`, the tokenize) for H1; (iv) the clip's
   resolution and frame rate (the probe's facts in `request_record->'media'`) for H3; (v) after deploying this branch, the same clip + prompt
   twice through the gateway: the second journal line reads `(memo of engine /tokenize, <1 ms)`.
2. **Coordinator / W - serve.sh: no change proposed now.** No flag or request field lets
   `/tokenize` share the chat route's cache on the pinned build (finding 2), and a cache would
   not skip the decode (finding 3). Only if (1) shows the warm `/tokenize` itself is slow and
   single-threaded is there a candidate: `OMP_NUM_THREADS` in the engine container (the
   processor runs with `set_default_torch_num_threads()` = `OMP_NUM_THREADS` or 1) or
   `--media-io-kwargs '{"video": {…decoder…}}'` (marlin-sop D13's list) - each a **new serving
   version** under W3's rules (`serving-version.json`, `tests/w/test_serving.py`,
   `check_pinned_launch`), measured on the protocol clips first, and the token counts must be
   re-checked, since a different decoder can report another frame count.
3. **M - optional overlap:** in `MediaPreparation.prepare`, materialize the local cache file
   before the durable `prepared` PUT (still both before `prepared`), so a caller may start the
   count while the PUT runs; for profile `v1` the prepared object is the source bytes, so a
   server-side copy would replace the GET + PUT. Worth ≤ one PUT per first-seen clip.
4. **W / I - a durable memo** only with an engine-level serving identity the worker can read
   (e.g. the installer writing `serving-version.json`'s `engine_options_digest`, image digest
   and weight digests into the worker's environment, compared against the engine's `/version`
   at startup).
5. **E3B - merge the journey change** (`counted_by_the_memo`, video cells only); no E3B anchor
   moved (`e3bm79`'s anchor in `preparation.py` is byte for byte the same).

## Ruling candidate (proposed, not numbered; next free is R106)

- **A preparation count may be the memo of the engine's answer to the same body.** A
  preparation worker may store, instead of asking again, the serving engine's checked count
  for a byte-identical `/tokenize` body (canonical JSON), over media with the same whole
  digests and profile, for the same serving revision (the job's `model_revision` and its
  pinned `serving_version_id` when it has one), held only in the process that asked, never
  past `PROCESSING_CACHE_TTL_S`; a text body is always asked; a use of the memo is logged as
  such. Anything else is asked, and R105's checks and fail-closed rule apply to every answer
  before it is memoized.

## Verification log

- 2026-09-24: Authored at `40440d4` from the runs above (logs under `$L`, sha256 prefixes
  quoted). vLLM source read at `a8d1aa9c` (raw GitHub, read-only). Containers: MinIO
  `infrx-tokcost-minio` (55724), the D harness's `infrx-d6-postgres`/`infrx-tokcost-valkey`
  (55437/55471, created and removed by the harness), the e4b stack (`infrx-e4b-*`,
  56800-56899: up for R4-R6, then `harness.down()`). No AWS, hosted Supabase or pilot-box
  contact; nothing pushed.
- 2026-09-24: After the last run: `infrx-tokcost-minio` removed; no `infrx-d6-*`,
  `infrx-tokcost-*`, `infrx-e4b-*` or `infrx-q3-valkey-55472` container and no process of this
  worktree left running.
