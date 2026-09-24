# PREP-WORKER (I2B-R5) — the product preparation loop in `python -m infrx.worker`

| Field | Value |
|---|---|
| Task | **PREP-WORKER (I2B-R5)**: the second loop of `python -m infrx.worker` that prepares every admitted job (`prepare_dispatch` → `claim_preparation` → M's `prepare` → the engine's exact prompt count → `prepared`). The gap it closes (brief, verified by the coordinator): no product process called `JobStore.prepared(...)`, so under the cutover every admission stayed `preparing` |
| Status | **implemented**, and the early review of `c035ee4` (`PREP-WORKER-review-c035ee4.json`, verdict PASS) folded in: L1-L8, J-F1, J-F2, J-F3 each with a case and a mutant, J-F4 attributed (below). **Not integrated, not deployed**: nothing ran on the pilot box, AWS or hosted Supabase |
| Owner/session | Opus 5.5 implementation session, 2026-09-24 |
| Base SHA | `01103c9` (integration head `865d352` + the I2B-R4 worker branch `e540963`) |
| Implementation SHA | **`4f7e32a`** (this report is committed after it) |
| Branch / worktree | `codex/prep-worker` in `.claude/worktrees/codex-prepw` |
| Oracles | `BACKEND-JOURNEY`, `OPS-RECOVER`, `DUR-FENCE`, `API-STREAM` (usage = the engine's count) |

## What was built

**`apps/infrx-api/infrx/worker/preparation.py`** — `PreparationRunner`, `WorkerLoop`'s runner
for `prepare_dispatch` candidates. One attempt per candidate:

1. `claim_preparation(job_id, worker_id)`: the fenced preparation lease (R46/R52). A lost
   claim (a candidate offered twice - the index is a hint, 02 §4 - or a job that moved on)
   is answered and logged at INFO, never a dead runner.
2. `load_work(lease)`: **the preparation read path already exists.** The brief assumed
   `load_work` is inference-only; it is not: 0016's `infrx.load_work` and 0018's
   `infrx.load_work_credit` fence with `fence_lease(p_lease, array['preparation',
   'inference'], …)` and F's fake `_fence_for_work` takes either kind (the `Work` record's own
   docstring: "`prepared_refs` … empty on a preparation lease, which is what fills them"). It
   is now pinned by two exported conformance cases (item 1). In the CREDIT regime the runner's
   `jobs` is I2B-R4's `CreditWork`, so the read is `load_work_credit`.
3. Media, only when the request carries any: a bounded wait (10 s, polled every 50 ms) for
   the admitting gateway's **durable** attach (`PgAttachments`, R99 (c): it lands just after
   the admission commits - the relay's `_admitted` runs the CREDIT rechecks, then `attach`),
   then M's `MediaPreparation.prepare(job_id, profile)` over `pilot.object_store(settings)`
   and `ProcessingCache(PROCESSING_CACHE_DIR)`: the staged object re-read and re-probed, the
   prepared object written once, the file materialized in the shared cache.
4. **The count** (below), then `prepared(lease, refs, prompt_tokens=count)`, logged at INFO:
   `prepared <job>: <n> prompt tokens (engine /tokenize, <ms> ms)`.

The lease is renewed every third of `PREPARATION_LEASE_TTL_S` while the attempt runs (R52;
the store never renews past `preparation_deadline_at`). A typed refusal of a renewal only
stops renewing (the fence then refuses `prepared`); an untyped one (the store unreachable)
propagates out of the runner - crash-only, as everything untyped.

**`WorkerService`** (W3's file) gains an optional second `WorkerLoop` (`preparation`,
`preparation_concurrency` runners): started with the inference pool, in `serve()`'s
crash-only wait set and in `_died()` (a dead preparation runner ends the process, exit 1),
drained beside the inference pool (a task next to the directly awaited inference drain, so
W3's drain order is unchanged) with its own bound **`PREPARATION_LEASE_TTL_S`**
and logged on its own line (`drained preparation: N finished, M released [...], ended
[...]`; the inference line is byte-identical, so rc08b, I2B-R4's drain case and
`rehearse.sh`'s grep are unchanged). `/readyz`'s body gains `preparing` and
`prepare_claimed`.

**`python -m infrx.worker`** composes it from the same pieces as the inference pool: the
same index (`scheduler`), the same work doors (`CreditWork` in CREDIT), the same
`VllmEngine`, the `MediaPreparation` it already built for `local_uri` (now with
`attachments = PgAttachments(connect)`), `PREPARATION_CONCURRENCY` runners. It now refuses
to start unless `PROCESSING_CACHE_DIR` is **writable** (the worker materializes media there)
and unless `PREPARATION_CONCURRENCY` is positive (`config.MUST_BE_POSITIVE`; the installer
refuses `0` first, `preflight.TUNABLE_SHAPES` `positive_int`, as `WORKER_CONCURRENCY`).

### The count rule (exact, as the engine counts it)

* The count is **vLLM's own `POST /tokenize`** on `UPSTREAM`, asked with exactly what the
  chat route will be sent: `{"model": <served name>, "messages": <VllmEngine.upstream_body's
  rebuilt messages - the local `file://` video URL included>, "add_generation_prompt":
  true}` plus, for a video, the body's pinned `mm_processor_kwargs` (the frame/pixel budget
  from the measured duration). Built by `VllmEngine.upstream_body` itself on the prepared
  request, so the two cannot drift.
* The answer is the count **only if** it arrives within `PREPARATION_TIMEOUT_S` (the engine
  client's own read timeout is 600 s); `count` is an integer ≥ 0 (not a bool); `tokens` is
  present with exactly `count` entries (vLLM always answers both); and for a video the
  number of `video_token_id`s
  (248057, `research/models/marlin2b/config.json`) lies in **[patches, 196 × patches]**, where
  `196 × patches = size.longest_edge // 2048` - the pinned budget `models/marlin2b/tokens.py`
  measured (marlin-sop.md §1.5: 196 tokens per two-frame patch, `tokens = total_pixels ÷
  2048`). One placeholder per video is a tokenizer that skipped the multimodal processor;
  more than the budget is an engine not running the pinned profile. tokens.py CHECKS the
  engine's answer; nothing replaces it.
* **Anything else is `dependency_unavailable`**: `/tokenize` unreachable, non-2xx, not JSON,
  no count, a failed check. The attempt then prepares nothing - **never a guess** - the lease
  is left to lapse, `recover` requeues the job with a fresh `prepare_dispatch` (R93), and the
  retries are bounded by `MAX_PREPUBLICATION_RETRIES` (three claims) and
  `preparation_deadline_at`, past which the store settles it `preparation_failed`, released
  free (R29).
* The count reaches the job as `jobs.prepared_prompt_tokens` (0012 refuses one past
  `max_input_tokens`: `context_length_exceeded`, the same path), W2's runner's
  `count_prompt_tokens`, and the settled usage is the engine's own (D5). On E2's fake engine
  the two are one number by construction (its `/tokenize` answers the count it reports as
  usage) and the cases below set it to 1337 so no constant can pass.

### Failure paths (item 3 of the brief)

| Failure | What happens | Proved by |
|---|---|---|
| fetch/stage/probe refusal in `prepare` (`not_found`, `unsupported_media`, `dependency_unavailable`) | typed refusal, nothing prepared, lease left to lapse → `recover` requeues (R93) → bounded retries → `preparation_failed` per R29 (there is no port operation that fails a preparation, so the store's own retry bound and deadline end it) | `a_refused_attempt_is_requeued_when_its_lease_lapses`, `a_tokenizer_that_cannot_count_prepares_nothing` |
| no durable attach within 10 s | `not_found`, same path | `a_job_whose_attach_never_lands_prepares_nothing` |
| a media ref another organization owns | `not_found` from the engine's view (`prepared_request`) before anything is counted or recorded on the job | `a_media_ref_of_another_org_is_not_found` |
| `/tokenize` unavailable or implausible | `dependency_unavailable`, same path | `the_engines_answer_is_checked_and_never_guessed` (14), `a_tokenizer_that_cannot_count_prepares_nothing` |
| lease lapse (a slow or dead worker) | `recover` (every worker's reaper, every 10 s) inserts a fresh `prepare_dispatch` row; the gateway's relay indexes it | `_pg__sigterm_releases_…` (the next process prepares it) |
| SIGTERM | the preparation pool stops claiming; an attempt still running at `PREPARATION_LEASE_TTL_S` is **released** (cancelled, never prepared - W2's vocabulary: its lease is left to lapse and the store decides); no job is left `preparing` behind a dead lease | `the_service_prepares_and_drains_its_preparation_pool`, `_pg__sigterm_releases_a_preparation_and_the_next_worker_prepares_it` |
| an untyped error in a preparation runner, or in its lease renewal | crash-only, as the inference pool: `serve()` drains and returns, exit 1, `Restart=always` | `a_dead_preparation_runner_ends_the_service`, `an_untyped_renewal_failure_is_not_swallowed` |
| a candidate offered twice (relay redelivery, reconciler repair) | the second claim is `not_claimable`, answered; no runner dies, the job is prepared once | `a_candidate_offered_twice_is_a_lost_claim_not_a_dead_runner` |
| a tokenizer that never answers | `dependency_unavailable` at `PREPARATION_TIMEOUT_S`, same path | `a_tokenizer_that_never_answers_is_bounded_by_the_budget` |
| a count past PostgreSQL `int` (a misbehaving engine) | `context_length_exceeded`, typed, before SQL (0012 would raise an untyped 22003) | `_pg__a_count_past_postgresql_int_is_context_length_exceeded`, the conformance case's `2**31` |

### The deploy consequence (item 5)

The worker unit mounted the media root **read-only** and the root was created `2750` for the
gateway's uid: preparation in the worker could not have written the processing cache on the
box (a green local run would have hidden it). `infrx-worker.service` now mounts
`PROCESSING_CACHE_DIR` read-write and the gateway and engine units create it `2770` (the
worker's uid 10002 is in group 10000); I's packaging case and mutants follow. In the product
nothing else writes the cache (the gateway's `prepare` was only ever called by the pilot
box's emulation).

### The contract reconcile (item 1) - a ruling candidate

`ports.JobStore.prepared(lease, media)` lacked the count `PgJobStore.prepared` already took.
Now `prepared(lease, media=(), *, prompt_tokens=None)` on the port; F's fake stores it (a
bool/non-integer is `invalid_request`, below 0 or above `max_input_tokens` is
`context_length_exceeded`, both with nothing changed; the bound is inclusive, as 0012's) and
hands it to `Work.prompt_tokens`/`WorkV2.prompt_tokens`. The port's `heartbeat` docstring
said a preparation lease is refused; R52, the fake and 0016 all renew it - the text now says
so. Text below, for numbering.

### The pilot box (item 4)

`tests/integration/backend/pilotbox.py`: `PROMPT_TOKENS`, the `Prepare` class and the
gateway process's preparation `WorkerLoop` (its `prepared(lease, refs,
prompt_tokens=PROMPT_TOKENS)`) are deleted; the box's worker process prepares. I2B-R4's
`tests/w/test_worker_main.py` round trip and drain case emulated preparation the same way
(`Box.prepare`); both now run the real loop behind the gateway's relay (`Box.relay()`: Q3's
`Reconciler`, which the gateway's lifespan runs and httpx's ASGI transport does not).
`git grep "\.prepared("`: in `infrx/` the only caller outside the contracts' conformance is
`worker/preparation.py`; every other call is a test harness driving the store (tests/d,
tests/q, tests/g's relay support, W2's loop cases, E3B/I3B's store-level drills and
`recoverykit.World`), none of them the pilot path. Since the review (J-F1) the box's fake
engine counts **1337** (`pilotbox.ENGINE_PROMPT_TOKENS`, not the retired 1200) and every
journey cell and the resume's succeeded jobs assert that the preparation attempt's worker is
the worker that ran the job, the stored count is the usage's, and `/tokenize` was asked
(`e3bm79` kills a constant count on the box). **e3bm74
retired**: the journey's video cells no longer cross processes through the cache (the
process that prepares runs the job); M's `local_uri_misses_the_disk`
(`test_mpilot__a_worker_runs_a_video_job_prepared_in_another_process`) keeps the disk lookup
proved.

## The early review of `c035ee4`, folded in

`research/plan/evidence/w/PREP-WORKER-review-c035ee4.json` (two Opus lenses + refuters):
verdict PASS, no confirmed blocking finding; J-F1 refuted as blocking (coverage), L1/L2
downgraded. Every item the coordinator listed is in this branch, each with its case and a
killed mutant (commits `bdf8d6c`, `3b18658`, `ee66943`, `336527e`; then `30680b8` - two
check answers only the count-shape check refuses, once `tokens` became required - and
`4f7e32a` - the inference drain starts synchronously again, which two W3 mutants,
`stop_does_not_await_the_pool` and `run_undoes_an_early_drain`, needed under
`INFRX_MUTANTS=all`):

| Finding | What changed | Case | Mutant |
|---|---|---|---|
| L1 lost claim untested (hm5) | nothing in the code (it was right); INFO line for a lost claim | `a_candidate_offered_twice_is_a_lost_claim_not_a_dead_runner` | `prep_lost_claim_kills_the_runner` |
| L2 default cadence untested (hm4) | the `Prep` world no longer injects the cadence unless a case names it | `the_default_renewal_keeps_a_long_preparation_alive` | `prep_renewal_cadence_slower_than_the_lease` |
| L3 `PREPARATION_CONCURRENCY=0` a crash loop | `config.MUST_BE_POSITIVE` + `preflight.TUNABLE_SHAPES` `positive_int` | `a_zero_preparation_pool_refuses_startup_by_name` (exit 2, named; preflight refuses `0`, accepts `2`) | `main_zero_preparation_pool_accepted`, `preflight_zero_preparation_pool_accepted` |
| L4 a count ≥ 2**31 untyped on PostgreSQL | `PgJobStore.prepared` refuses `> 2**31-1` as `context_length_exceeded`; `engine_prompt_tokens` requires `tokens`; the conformance case adds `2**31` and asserts the type of any answer | `_pg__a_count_past_postgresql_int_is_context_length_exceeded`, `dur_fence__prepared_stores_the_exact_prompt_count_once` (both images), `the_engines_answer_is_checked…[no-tokens]` | `pg_prepared_count_past_int_untyped`, `prep_tokens_optional` |
| L5 lower drain bound unpinned (hm7) | the service case's third job answers 0.3 s after the drain starts (bound 0.5 s) and ends `prepared` | `the_service_prepares_and_drains_its_preparation_pool` | `service_preparation_drain_released_at_once` |
| L6 `ATTACH_WAIT_S` unexercised (hm8) | the video case's late attach is waited for by the product default | `a_video_job_is_prepared_from_its_durable_attach` | `prep_attach_wait_default_zero` |
| L7 an untyped renewal failure swallowed | re-raised after the attempt (crash-only); a typed one only stops renewing | `an_untyped_renewal_failure_is_not_swallowed` | `prep_untyped_renewal_swallowed` |
| L8 count = usage by construction | fake fault `tokenize_count`; Limits 1 and 9 | `_pg__a_tokenize_count_that_disagrees_with_the_usage_settles_at_the_usage` (stored 1000, settled at 1337) | `fake_tokenize_count_override_ignored` |
| J-F1 journeys blind to who prepared | the box's engine counts 1337; every journey cell and the resume's succeeded jobs assert the preparing worker = the executing worker, stored count = usage = 1337, `/tokenize` asked | `test_backend_journey[*]`, `…dataset_client_resume` | E3B `e3bm79` (`count = 1200`) |
| J-F2 no per-preparation log | INFO line per prepared job (count, latency) | text case (caplog), `_pg__…prepares_and_runs…` (the worker log) | `prep_preparation_unlogged`, `prep_preparation_unlogged_on_postgresql` |
| J-F3 README said heartbeat refuses | README R46 row aligned with R52; the older bullet marked superseded | - (text) | - |
| J-F4 E4B full list: 2 survivors | none here: pre-existing at `01103c9` (the reviewer measured both at base); Limit 10 | - | - |

## Per item: commit → case → mutant (death line, measured with the runner's own copy)

Cases are `tests/w/test_prep_worker.py` (40 items: 36 service-free, 4 `_pg__`) unless named.
Lists: `tests/w/prep_worker_mutants.py` - **50 service-free `MUTANTS` + 8 `PG_MUTANTS`**
(`test_prep_worker_mutants.py`, on the Makefile `api-mutants` line after
`test_worker_main_mutants.py`); **7** fake-store mutants in F's `tests/contracts/mutants.py`
(R32: every exported case is covered there); **3** in I's `tests/i/mutants.py`; **1** in
E3B's `tests/integration/mutants.py` (`e3bm79`). Every death below is an assertion (the
shared runner's rule), measured at `4f7e32a` (the F rows at `336527e`, whose conformance
code is final) by applying each mutant to the runner's own copy and reading pytest's
`--tb=line` FAILURES line; the whole lists' verdicts are R17-R19.

| Item (commit) | Mutant | Case(s) | Death (file:line: message) |
|---|---|---|---|
| 1 contract, F's list (`bd10d47`, `49c7a00`, `3b18658`) | `fake_prepared_drops_the_count` | `dur_fence__prepared_stores_the_exact_prompt_count_once`, `credit_prepare__the_count_reaches_the_credit_work` | infrx/contracts/conformance/jobs.py:1066: AssertionError: None |
| 1 contract, F's list (`bd10d47`, `49c7a00`, `3b18658`) | `fake_prepared_count_unbounded` | `dur_fence__prepared_stores_the_exact_prompt_count_once` | infrx/contracts/conformance/jobs.py:1055: AssertionError: prompt_tokens=-1 was stored |
| 1 contract, F's list (`bd10d47`, `49c7a00`, `3b18658`) | `fake_prepared_count_bound_exclusive` | `dur_fence__prepared_stores_the_exact_prompt_count_once` | infrx/contracts/conformance/jobs.py:1063: AssertionError: the inclusive bound 30720 was refused: context_length_exceeded |
| 1 contract, F's list (`bd10d47`, `49c7a00`, `3b18658`) | `fake_prepared_count_untyped` | `dur_fence__prepared_stores_the_exact_prompt_count_once` | infrx/contracts/conformance/jobs.py:1055: AssertionError: prompt_tokens=True was stored |
| 1 contract, F's list (`bd10d47`, `49c7a00`, `3b18658`) | `fake_refused_count_changes_state` | `dur_fence__prepared_stores_the_exact_prompt_count_once` | infrx/contracts/conformance/jobs.py:1059: AssertionError: (-1, <JobState.queued: 'queued'>) |
| 1 contract, F's list (`bd10d47`, `49c7a00`, `3b18658`) | `fake_load_work_drops_the_count` | `dur_fence__prepared_stores_the_exact_prompt_count_once` | infrx/contracts/conformance/jobs.py:1066: AssertionError: None |
| 1 contract, F's list (`bd10d47`, `49c7a00`, `3b18658`) | `fake_credit_work_drops_the_count` | `credit_prepare__the_count_reaches_the_credit_work` | infrx/contracts/conformance/v2_contracts.py:1078: AssertionError: None |
| 1 contract (`bd10d47`) | `port_prepared_count_positional` | `prepared_takes_a_keyword_count_on_every_adapter` | tests/w/test_prep_worker.py:164: AssertionError: JobStore.prepared |
| 3 runner (`e351b12`, `c035ee4`, `30680b8`) | `prep_count_not_stored` | `a_text_job_is_queued_with_the_engines_own_count`, `a_video_job_is_prepared_from_its_durable_attach` | tests/w/test_prep_worker.py:189: AssertionError: assert (None == 1337) |
| 3 runner (`e351b12`, `c035ee4`, `30680b8`) | `prep_count_guessed` | `a_text_job_is_queued_with_the_engines_own_count` | tests/w/test_prep_worker.py:187: AssertionError: PreparationResult(job_id='00000001-0000-4000-8000-000000000001', cause='prepared', refusal=… |
| 3 runner (`e351b12`, `c035ee4`, `30680b8`) | `prep_tokenize_without_the_generation_prompt` | `a_text_job_is_queued_with_the_engines_own_count` | tests/w/test_prep_worker.py:190: AssertionError: assert [{'messages':...: 'marlin2b'}] == [{'add_genera...: 'marlin2b'}] |
| 3 runner (`e351b12`, `c035ee4`, `30680b8`) | `prep_tokenize_without_the_video_budget` | `a_video_job_is_prepared_from_its_durable_attach` | tests/w/test_prep_worker.py:219: AssertionError: PreparationResult(job_id='00000001-0000-4000-8000-000000000001', cause=None, refusal='depen… |
| 3 runner (`e351b12`, `c035ee4`, `30680b8`) | `prep_tokenizer_errors_untyped` | `the_engines_answer_is_checked_and_never_guessed` | tests/w/test_prep_worker.py:322: AssertionError: ('unavailable', <class 'httpx.HTTPStatusError'>) |
| 3 runner, tokenizer bound (`fe2a82a`) | `prep_tokenizer_wait_unbounded` | `a_tokenizer_that_never_answers_is_bounded_by_the_budget` | tests/w/test_prep_worker.py:405: AssertionError: timed out |
| 3 runner, tokenizer bound (`fe2a82a`) | `prep_tokenizer_bound_not_the_budget` | `a_tokenizer_that_never_answers_is_bounded_by_the_budget` | tests/w/test_prep_worker.py:405: AssertionError: timed out |
| 3 runner (`e351b12`, `c035ee4`, `30680b8`) | `prep_count_shape_unchecked` | `the_engines_answer_is_checked_and_never_guessed` | tests/w/test_prep_worker.py:322: AssertionError: ('bool-with-its-token', True) |
| 3 runner (`e351b12`, `c035ee4`, `30680b8`) | `prep_tokens_disagreement_ignored` | `the_engines_answer_is_checked_and_never_guessed` | tests/w/test_prep_worker.py:322: AssertionError: ('disagreeing', 3) |
| review L4 (`3b18658`/`bdf8d6c`) | `prep_tokens_optional` | `the_engines_answer_is_checked_and_never_guessed` | tests/w/test_prep_worker.py:322: AssertionError: ('no-tokens', <class 'AttributeError'>) |
| 3 runner (`e351b12`, `c035ee4`, `30680b8`) | `prep_video_count_unchecked` | `the_engines_answer_is_checked_and_never_guessed`, `a_tokenizer_that_cannot_count_prepares_nothing` | tests/w/test_prep_worker.py:322: AssertionError: ('below-one-per-patch', 40) |
| 3 runner (`e351b12`, `c035ee4`, `30680b8`) | `prep_video_ceiling_dropped` | `the_engines_answer_is_checked_and_never_guessed` | tests/w/test_prep_worker.py:322: AssertionError: ('past-the-budget', 3000) |
| 3 runner (`e351b12`, `c035ee4`, `30680b8`) | `prep_video_floor_one_placeholder` | `the_engines_answer_is_checked_and_never_guessed` | tests/w/test_prep_worker.py:322: AssertionError: ('below-one-per-patch', 40) |
| 3 runner (`e351b12`, `c035ee4`, `30680b8`) | `prep_video_bounds_exclusive` | `a_video_count_inside_the_pinned_budget_is_the_count` | tests/w/test_prep_worker.py:334: AssertionError: (13, <class 'infrx.contracts.errors.DependencyUnavailable'>) |
| 3 runner (`e351b12`, `c035ee4`, `30680b8`) | `prep_media_skipped` | `a_video_job_is_prepared_from_its_durable_attach` | tests/w/test_prep_worker.py:219: AssertionError: PreparationResult(job_id='00000001-0000-4000-8000-000000000001', cause=None, refusal='not_f… |
| 3 runner (`e351b12`, `c035ee4`, `30680b8`) | `prep_attach_not_awaited` | `a_video_job_is_prepared_from_its_durable_attach` | tests/w/test_prep_worker.py:219: AssertionError: PreparationResult(job_id='00000001-0000-4000-8000-000000000001', cause=None, refusal='not_f… |
| review L6 (`3b18658`/`bdf8d6c`) | `prep_attach_wait_default_zero` | `a_video_job_is_prepared_from_its_durable_attach` | tests/w/test_prep_worker.py:219: AssertionError: PreparationResult(job_id='00000001-0000-4000-8000-000000000001', cause=None, refusal='not_f… |
| 3 runner (`e351b12`, `c035ee4`, `30680b8`) | `prep_attach_wait_unbounded` | `a_job_whose_attach_never_lands_prepares_nothing` | tests/w/test_prep_worker.py:244: AssertionError: timed out |
| 3 runner (`e351b12`, `c035ee4`, `30680b8`) | `prep_foreign_ref_counted` | `a_media_ref_of_another_org_is_not_found` | tests/w/test_prep_worker.py:264: AssertionError: PreparationResult(job_id='00000005-0000-4000-8000-000000000005', cause=None, refusal='forbi… |
| 3 runner (`e351b12`, `c035ee4`, `30680b8`) | `prep_no_renewal` | `the_lease_is_renewed_while_preparation_runs` | tests/w/test_prep_worker.py:433: AssertionError: PreparationResult(job_id='00000001-0000-4000-8000-000000000001', cause=None, refusal='stale… |
| review L2 (`3b18658`/`bdf8d6c`) | `prep_renewal_cadence_slower_than_the_lease` | `the_default_renewal_keeps_a_long_preparation_alive` | tests/w/test_prep_worker.py:461: AssertionError: PreparationResult(job_id='00000001-0000-4000-8000-000000000001', cause=None, refusal='stale… |
| review L7 (`3b18658`/`bdf8d6c`) | `prep_untyped_renewal_swallowed` | `an_untyped_renewal_failure_is_not_swallowed` | tests/w/test_prep_worker.py:491: AssertionError: assert PreparationResult(job_id='00000001-0000-4000-8000-000000000001', cause='prepared', r… |
| review L1 (`3b18658`/`bdf8d6c`) | `prep_lost_claim_kills_the_runner` | `a_candidate_offered_twice_is_a_lost_claim_not_a_dead_runner` | tests/w/test_prep_worker.py:593: AssertionError: [('prepared', '')] |
| review J-F2 (`3b18658`/`bdf8d6c`) | `prep_preparation_unlogged` | `a_text_job_is_queued_with_the_engines_own_count` | tests/w/test_prep_worker.py:195: AssertionError: [] |
| 3 runner (`e351b12`, `c035ee4`, `30680b8`) | `prep_renewal_outlives_the_attempt` | `a_refused_attempt_is_requeued_when_its_lease_lapses` | tests/w/test_prep_worker.py:381: AssertionError: timed out |
| 3 runner (`e351b12`, `c035ee4`, `30680b8`) | `prep_refusal_prepares_anyway` | `a_refused_attempt_is_requeued_when_its_lease_lapses`, `a_tokenizer_that_cannot_count_prepares_nothing`, `a_job_whose_attach_never_lands_prepares_nothing` | tests/w/test_prep_worker.py:245: AssertionError: assert (<JobState.queued: 'queued'> is <JobState.preparing: 'preparing'>) |
| 3 runner (`e351b12`, `c035ee4`, `30680b8`) | `prep_refusal_escapes` | `a_tokenizer_that_cannot_count_prepares_nothing` | tests/w/test_prep_worker.py:355: AssertionError: <class 'infrx.contracts.errors.DependencyUnavailable'> |
| 2 fake `/tokenize` (`1d374d8`) | `fake_tokenize_not_routed` | `a_text_job_is_queued_with_the_engines_own_count` | tests/w/test_prep_worker.py:187: AssertionError: PreparationResult(job_id='00000001-0000-4000-8000-000000000001', cause=None, refusal='depen… |
| 2 fake `/tokenize` (`1d374d8`) | `fake_tokenize_count_is_not_the_usage` | `a_text_job_is_queued_with_the_engines_own_count` | tests/w/test_prep_worker.py:187: AssertionError: PreparationResult(job_id='00000001-0000-4000-8000-000000000001', cause='prepared', refusal=… |
| 2 fake `/tokenize` (`1d374d8`) | `fake_tokenize_video_unexpanded` | `a_video_job_is_prepared_from_its_durable_attach` | tests/w/test_prep_worker.py:219: AssertionError: PreparationResult(job_id='00000001-0000-4000-8000-000000000001', cause=None, refusal='depen… |
| 2 fake `/tokenize` (`1d374d8`) | `fake_tokenize_down_ignored` | `a_tokenizer_that_cannot_count_prepares_nothing` | tests/w/test_prep_worker.py:356: AssertionError: PreparationResult(job_id='00000001-0000-4000-8000-000000000001', cause='prepared', refusal=… |
| 3 service/composition (`e351b12`, `4f7e32a`) | `service_preparation_not_started` | `the_service_prepares_and_drains_its_preparation_pool` | tests/w/test_prep_worker.py:560: AssertionError: (False, {'claimed': 0, 'engine': 'up', 'failures': 0, 'in_flight': 0, ...}) |
| 3 service/composition (`e351b12`, `4f7e32a`) | `service_preparation_not_drained` | `the_service_prepares_and_drains_its_preparation_pool` | tests/w/test_prep_worker.py:561: AssertionError: (True, 'timed out', 5.000543233938515) |
| 3 service/composition (`e351b12`, `4f7e32a`) | `service_preparation_drain_unbounded` | `the_service_prepares_and_drains_its_preparation_pool` | tests/w/test_prep_worker.py:561: AssertionError: (True, 'timed out', 5.000266369897872) |
| review L5 (`3b18658`/`bdf8d6c`) | `service_preparation_drain_released_at_once` | `the_service_prepares_and_drains_its_preparation_pool` | tests/w/test_prep_worker.py:562: AssertionError: (<JobState.preparing: 'preparing'>, <JobState.preparing: 'preparing'>) |
| 3 service/composition (`e351b12`, `4f7e32a`) | `service_preparation_drain_unlogged` | `the_service_prepares_and_drains_its_preparation_pool` | tests/w/test_prep_worker.py:564: AssertionError: ['drained: 1 finished, 0 released [], ended []'] |
| 3 service/composition (`e351b12`, `4f7e32a`) | `service_readiness_hides_preparation` | `the_service_prepares_and_drains_its_preparation_pool` | tests/w/test_prep_worker.py:561: AssertionError: (False, DrainReport(finished=1, released=0, claimed=0, ended=(), released_jobs=()), 0.50082… |
| 3 service/composition (`e351b12`, `4f7e32a`) | `service_readiness_hides_prepared` | `the_service_prepares_and_drains_its_preparation_pool` | tests/w/test_prep_worker.py:560: AssertionError: (True, {'claimed': 0, 'engine': 'up', 'failures': 0, 'in_flight': 0, ...}) |
| 3 service/composition (`e351b12`, `4f7e32a`) | `service_dead_preparation_runner_ignored` | `a_dead_preparation_runner_ends_the_service` | tests/w/test_prep_worker.py:618: AssertionError: DrainReport(finished=1, released=0, claimed=0, ended=(), released_jobs=()) |
| 3 service/composition (`e351b12`, `4f7e32a`) | `service_serve_ignores_the_preparation_pool` | `a_dead_preparation_runner_ends_the_service` | tests/w/test_prep_worker.py:618: AssertionError: timed out |
| 3 service/composition (`e351b12`, `4f7e32a`) | `main_preparation_pool_absent` | `the_worker_composes_the_preparation_pool` | tests/w/test_prep_worker.py:636: AssertionError: None |
| 3 service/composition (`e351b12`, `4f7e32a`) | `main_preparation_concurrency_ignored` | `the_worker_composes_the_preparation_pool` | tests/w/test_prep_worker.py:639: AssertionError: assert (<infrx.scheduling.memory.MemoryScheduler object at 0x733dee4454c0> is <infrx.schedu… |
| 3 service/composition (`e351b12`, `4f7e32a`) | `main_preparation_bypasses_the_credit_doors` | `the_worker_composes_the_preparation_pool` | tests/w/test_prep_worker.py:640: assert (<infrx.state.jobstore.PgJobStore object at 0x78252bb48e30> is <infrx.worker.__main__.CreditWork obj… |
| 3 service/composition (`e351b12`, `4f7e32a`) | `main_attach_not_durable` | `the_worker_composes_the_preparation_pool` | tests/w/test_prep_worker.py:642: assert (<infrx.media.store.InMemoryObjectStore object at 0x730110150230> is <infrx.media.store.InMemoryObje… |
| 3 service/composition (`e351b12`, `4f7e32a`) | `main_preparation_on_the_inference_kind` | `the_worker_composes_the_preparation_pool` | tests/w/test_prep_worker.py:638: AssertionError: assert <OutboxKind.inference_dispatch: 'inference_dispatch'> is <OutboxKind.prepare_dispatc… |
| 3 service/composition (`e351b12`, `4f7e32a`) | `main_preparation_own_index` | `the_worker_composes_the_preparation_pool` | tests/w/test_prep_worker.py:639: AssertionError: assert (<infrx.scheduling.valkey.ValkeyScheduler object at 0x7a0962d746b0> is <infrx.schedu… |
| review L3 (`3b18658`/`bdf8d6c`) | `main_zero_preparation_pool_accepted` | `a_zero_preparation_pool_refuses_startup_by_name` | tests/w/test_prep_worker.py:671: AssertionError: (2, "2026-09-24 05:59:55,122 INFO botocore.credentials Found credentials in environment var… |
| review L3 (`3b18658`/`bdf8d6c`) | `preflight_zero_preparation_pool_accepted` | `a_zero_preparation_pool_refuses_startup_by_name` | tests/w/test_prep_worker.py:674: AssertionError: [] |
| 3 service/composition (`e351b12`, `4f7e32a`) | `main_media_root_read_only_accepted` | `a_media_root_the_worker_cannot_write_refuses_startup` | tests/w/test_prep_worker.py:659: AssertionError: assert (WorkerService(loop=WorkerLoop(scheduler=<infrx.scheduling.memory.MemoryScheduler ob… |
| 3 PostgreSQL (`e351b12`) | `main_preparation_pool_absent_on_postgresql` | `_pg__the_worker_process_prepares_and_runs_an_admitted_job` | tests/w/test_prep_worker.py:731: AssertionError: (None, '2026-09-24 06:36:31,866 INFO botocore.credentials Found credentials in environment … |
| 3 PostgreSQL (`e351b12`) | `main_preparation_bypasses_the_credit_doors_on_postgresql` | `_pg__the_worker_process_prepares_and_runs_an_admitted_job` | tests/w/test_prep_worker.py:731: AssertionError: (None, '2026-09-24 06:37:40,574 INFO botocore.credentials Found credentials in environment … |
| 3 PostgreSQL (`e351b12`) | `prep_count_guessed_on_postgresql` | `_pg__the_worker_process_prepares_and_runs_an_admitted_job` | tests/w/test_prep_worker.py:733: AssertionError: 2026-09-24 06:38:49,286 INFO botocore.credentials Found credentials in environment variable… |
| 3 PostgreSQL (`e351b12`) | `service_preparation_drain_unbounded_on_postgresql` | `_pg__sigterm_releases_a_preparation_and_the_next_worker_prepares_it` | tests/w/test_prep_worker.py:774: AssertionError: (-9, 60.00766505114734, "2026-09-24 06:38:58,096 INFO botocore.credentials Found credential… |
| review L4 (`3b18658`/`bdf8d6c`) | `pg_prepared_count_past_int_untyped` | `_pg__a_count_past_postgresql_int_is_context_length_exceeded` | tests/w/test_prep_worker.py:829: AssertionError: <class 'psycopg.errors.NumericValueOutOfRange'> |
| review L8 (`3b18658`/`bdf8d6c`) | `fake_tokenize_count_override_ignored` | `_pg__a_tokenize_count_that_disagrees_with_the_usage_settles_at_the_usage` | tests/w/test_prep_worker.py:803: AssertionError: assert ('succeeded',...', 1337, 1, 0) == ('succeeded',...', 1000, 1, 0) |
| review J-F2 (`3b18658`/`bdf8d6c`) | `prep_preparation_unlogged_on_postgresql` | `_pg__the_worker_process_prepares_and_runs_an_admitted_job` | tests/w/test_prep_worker.py:737: AssertionError: 2026-09-24 06:40:22,124 INFO botocore.credentials Found credentials in environment variable… |
| 3 PostgreSQL (`e351b12`) | `prep_renewal_outlives_a_drain` | `_pg__sigterm_releases_a_preparation_and_the_next_worker_prepares_it` | tests/w/test_prep_worker.py:774: AssertionError: (-9, 60.007785448338836, '2026-09-24 06:40:31,117 INFO botocore.credentials Found credentia… |
| 5 deploy (`1be0866`) | `worker_reads_media_only`, `media_root_created_by_docker`, `media_root_not_group_writable` (I's list) | `tests/i/test_packaging.py::…runtime_containers_run_unprivileged_and_bounded`, `…the_engine_takes_its_settings_from_the_validated_file` | `3/3 killed` (`1 failed, 21 deselected` each, R12) |
| review J-F1 (`ee66943`) | E3B `e3bm79` (preparation.py `count = 1200`) | `test_backend_journey[text-sync]` | killed in the gate's mutants stage (R20: 252 mutants, 248 killed + 4 controls, 0 not killed) |

## Runs (UTC 2026-09-24; env names only; `$L` = `/tmp/claude-1000/prepw-tmp/logs`)

Lane env for every PostgreSQL run: `INFRX_D_TASK=d5 INFRX_D2_VALKEY_PORT=55467
INFRX_D2_VALKEY_CONTAINER=infrx-d5-valkey INFRX_Q_VALKEY_PORT=55498
INFRX_M_S3_ENDPOINT=http://127.0.0.1:55781 INFRX_M_S3_LOCAL_CREDS=1`, `AWS_*` unset,
`TMPDIR=/tmp/claude-1000/prepw-tmp`; MinIO `infrx-prepw-minio` (the brief's image digest) on
127.0.0.1:55781.

| # | Command (from the API root unless `repo$`) | At | Exit | Tail (log sha256 prefix) |
|---|---|---|---|---|
| R1 | `pytest -q tests/contracts/test_conformance.py tests/contracts/v2/test_conformance_v2.py` (F's fakes) | item 1 tree | 0 | `231 passed in 4.74s` |
| R2 | lane env `pytest -q tests/d/test_jobstore_conformance.py tests/d/test_credit_jobstore_conformance.py -k "prompt_count or count_reaches or load_work_is_fenced"` | item 1 tree | 0 | `3 passed, 83 deselected in 3.30s` |
| R3 | lane env `INFRX_MUTANTS=all pytest -q tests/w/test_prep_worker_mutants.py -k "not pg_mutant"` (the first full list, then 39+7) | `e351b12` tree | 0 | `51 passed, 5 deselected in 177.98s` (`83246cda`) |
| R4 | repo$ `INFRX_E2_NAMESPACE=e4b run.py --layer 2 --keep` | `1d374d8`+ | 0 | `exit 0 (all stages passed)`: preflight/services/migrate/rls (731) (`dbbaf513`) |
| R5a | repo$ e4b `pytest -q tests/integration/backend/test_journey.py …dr11 …rc03 …rc08b` | `4f924fa` tree | 1 | `3 failed, 3 passed, 3 skipped, 10 errors` - every one `fake vLLM … 56880: address already in use` (P-21: TIME-WAIT `127.0.0.1:56880 -> 56832/56732`) (`ae5d3ae2`) |
| R5b | the same, the ports free again | `4f924fa` tree | 0 | **`16 passed, 3 skipped in 57.31s`** (the 3: the stage's PostgREST, layer 3 only); every worker log `drained preparation: 2 finished, 0 released`, no refused preparation (`8daa18df`) |
| R6 | lane env `INFRX_MUTANTS=all pytest -q tests/w/test_prep_worker_mutants.py -k pg_mutant` | `e351b12`+ | 0 | `5 passed, 51 deselected in 329.08s` (`41ea831d`) |
| R7 | lane env conformance (R2's files, whole) plain / `INFRX_D1_IMAGE=supabase` | `49c7a00` | 0 / 0 | `82 passed, 4 xfailed in 71.12s` / `82 passed, 4 xfailed in 450.80s` (the 4: D's pre-existing PENDING) |
| R8 | lane env `PYTEST_ADDOPTS=-rfEs pytest -q` (make api-test) | `c035ee4` tree | 1 | `1 failed, 3683 passed, 3 skipped, 5 xfailed in 1954.48s`: `test_every_case_is_covered_by_a_mutant` (my conformance cases' mutants were in my list, not F's) → fixed in `49c7a00` (`664c55f1`) |
| R9 | repo$ `REHEARSAL_NS=infrx-prepw deploy/rehearse.sh` | `49c7a00` | 0 | `REHEARSAL PASSED`, 48 PASS / 0 FAIL, `teardown: nothing infrx-prepw-* left` (`67e9f083`) |
| R10a | `pytest -q tests/contracts …` **without the lane env** (Limit 8) | `49c7a00` | 1 | `3 failed, 1109 passed` (`test_v1_projection_pg.py` on d1) |
| R10b | lane env `pytest -q tests/contracts` | `49c7a00` | 0 | `1077 passed in 82.19s` |
| R11 | repo$ gate `INFRX_E2_NAMESPACE=e4b INFRX_Q_VALKEY_PORT=55498 run.py --layer 3 --canary --only-suites` | `49c7a00` | 1 | backend **188 passed / 0 pending / 0 failed**; mutants **251: 247 killed + 4 controls, 0 not killed**; suites FAIL only on 2 `test_fake_vllm.py` cases, `56882`/`56886: address already in use` (P-21) (`18ffd783`) |
| R12 | `python -m tests.i.mutants worker_reads_media_only media_root_created_by_docker media_root_not_group_writable` | `1be0866` tree | 0 | `3/3 killed` |
| R13 | `python -m tests.contracts.mutants fake_prepared_drops_the_count … fake_credit_work_drops_the_count` | `49c7a00` | 0 | `7/7 killed` |
| R14 | the gate at `fe2a82a` - stopped by me in its mutants stage for the review fold-in | `fe2a82a` | - | backend 188/0/0, **suites ok** (no P-21 that time) (`9deebcbc`) |
| R15 | the gate (R11's command) | **`336527e`**, clean start and end | 1 | backend **188/0/0** (the journeys' new provenance asserts); mutants **252: 248 killed + 4 controls, 0 not killed** (`e3bm79` killed); suites FAIL only `test_the_server_log_is_never_left_behind_in_the_temp_directory` - `56886: address already in use` (P-21) (`87b3aa12`) |
| R16 | the gate again | `336527e` | 1 | same; suites FAIL only `test_killing_the_engine_process_is_a_transport_failure_and_it_restarts` - `56882: address already in use` (P-21) (`0a8feb69`) |
| R17 | lane env `pytest -q -rfEs tests/w` (whole, PostgreSQL cases included) | **`4f7e32a`** | 0 | **`255 passed, 2 skipped in 636.73s`** (the 2: the empty PG-mutant parametrizations) (`2e2c8c80`) |
| R18 | lane env `INFRX_MUTANTS=all pytest -q tests/w/test_prep_worker_mutants.py tests/w/test_w3_mutants.py tests/w/test_worker_main_mutants.py -k "not pg_mutant"` | `4f7e32a` | 0 | **`160 passed in 521.78s`**: mine 50/50, W3 85/85 (its 9 moved anchors), I2B-R4 13/13 (`e3d208c9`) |
| R19 | the same `-k pg_mutant` | `4f7e32a` | 0 | **`12 passed in 428.05s`**: mine 8/8, I2B-R4 4/4 (the round trip and drain now run the real loop) (`11e48023`) |
| R20 | lane env `PYTEST_ADDOPTS=-rfEs pytest -q` (make api-test) | `4f7e32a` | 0 | **`3694 passed, 3 skipped, 5 xfailed in 1840.31s`** (skips: the three lists' empty PG parametrizations) (`0b7d8b47`) |
| R21 | lane env conformance (R7) plain / supabase | `4f7e32a` | 0 / 0 | **`82 passed, 4 xfailed in 69.03s`** / **`82 passed, 4 xfailed in 428.92s`** (`99714406`/`9a8999a5`) |
| R22 | lane env `INFRX_D1_IMAGE=supabase pytest -q tests/w/test_prep_worker.py tests/w/test_worker_main.py -k _pg__` | `4f7e32a` | 0 | `8 passed, 53 deselected in 43.69s` (`d5ac0642`) |
| R23 | repo$ `REHEARSAL_NS=infrx-prepw deploy/rehearse.sh` | `4f7e32a` | 0 | **`REHEARSAL PASSED`, 48 PASS / 0 FAIL**, release `4f7e32a`, runtime image `sha256:562c9196…`; step 4b `drained: 10 finished, 0 released` + `drained preparation: 2 finished, 0 released`; `teardown: nothing infrx-prepw-* left` (`c9206e8f`) |
| R24 | death lines (`deaths.py`: the shared runner's copy, `_prepare`, `_pytest`) memory / F / pg | `4f7e32a` (F: `336527e`) | - | 50/50, 7/7, 8/8 assertion deaths (`da686733`, `9220a5c5`, `c1d69ebb`) |
| R25 | repo$ gate `INFRX_E2_NAMESPACE=e4b INFRX_Q_VALKEY_PORT=55498 run.py --layer 3 --canary --only-suites --report …` | **`4f7e32a`**, clean start and end | 1 | preflight/services/migrate/rls (731)/engine/canary/teardown PASS; **backend PASS `{passed 188, pending 0, failed 0}`**; **mutants PASS `{252, killed 248, controls_survived 4, not_killed 0, problems null}`** (e3bm79 killed); suites FAIL on ONE case, `test_fake_vllm.py::test_killing_the_engine_process_is_a_transport_failure_and_it_restarts` - `56882: address already in use` (P-21); 1104.9 s (`fb981ab7`) |
| R26 | the gate again | `4f7e32a` | 1 | 127.0.0.1:56880 held by another socket for the whole run: the journeys errored at setup (`backend {passed 178, failed 10}` - the ten journey cases), `engine` FAIL, rc03/rc08b and `e2m54` the same bind error; nothing else failed (`1d9f63a2`) |
| R27 | the gate a third time | `4f7e32a`, clean start and end | 1 | **suites PASS `{passed 302, skipped 3}`**; backend PASS `{188, 0, 0}`; engine/canary/teardown PASS; mutants `{252, killed 245, controls 4, not_killed 3}` - `e2m54` "baseline-red" and `e3bm78`/`e3bm79` "no-cases", every one the fake vLLM failing to bind (`fake_vllm.py:484: RuntimeError`, P-21); both were killed in R25; 1094.0 s (`f019184b`) |


**The gate at the implementation SHA, read together (R25-R27):** no single run exited 0
or 3 - each lost a fake-vLLM port on this host (P-21: the e4b block's fixed ports sit inside
the ephemeral range, and a long-lived client socket - another lane's, or a stack process's own
pool - can hold one for minutes, as in R26). Every stage passed in at least one run at
`4f7e32a`, clean at start and end: backend 188/0/0 (R25, R27), suites 302 (R27), mutants
252 with 0 not killed (R25), engine and canary (R25, R27). The only failures in all three are
`address already in use` on 56880/56882/56886; I2B-R4's gate at `fb8babb` (its R11b) failed
the same way. Integration request 7.

## Limits

1. **Real vLLM's `/tokenize` for a video is ⚠️ TO BE VERIFIED on the box.** The review
   (L8) read the pinned nightly's source: `create_tokenize` with a `TokenizeChatRequest` goes
   through `online_renderer.preprocess_chat` - the chat path, with `mm_processor_kwargs` as
   prompt extras and `skip_mm_cache=True` - so it *should* return the expanded video
   placeholders and the same count as the chat usage. Unmeasured. If it does not expand,
   **every video job fails preparation `dependency_unavailable`** (the check above) -
   fail-closed by design, never an under-count - until the engine's answer is right or the
   rule changes (integration request 1). The tokenize pass skips the multimodal cache, so a
   video is decoded twice per job (latency ⚠️ TO BE VERIFIED). Text is exact on any vLLM
   (template tokenization is what the chat route runs).
2. **A text job has no durable acceptance marker.** The relay's `_admitted` runs the CREDIT
   rechecks (active card, serving revision, capability) *after* the admission commits and
   *before* `attach`; a video job's preparation waits for the durable attach, but 0003 cannot
   record an attach of no media (R99 (c), Limit 1), so a text job may be prepared - and even
   run - before its recheck finishes. If the recheck refuses, the gateway's `cancel` still
   ends the job through the store's fences (no debit unless completion already won, which a
   millisecond recheck against a prepare → index → claim → generate chain does not lose in
   practice). The emulation waited for the in-process `by_job` of every job, text included.
   Upgrade: R99's "0019 (a zero-ref marker)" (integration request 2).
3. **A permanent refusal is retried.** No port operation fails a preparation, so a clip the
   probe refuses, a count past the ceiling or an adapter refusal of the request costs up to
   three claims and `PREPARATION_LEASE_TTL_S` each (~90 s) before `preparation_failed` (R29);
   a job the adapter would refuse (e.g. a parameter G1 let through) now ends
   `preparation_failed` rather than with the attempt's own cause. The third ruling candidate.
4. **The count costs one engine round trip per job** (text: a template tokenization; video:
   whatever the engine does to count, possibly a second decode of the clip). Not measured.
5. **The media root is writable by the gateway and the worker** (`2770`); least privilege
   would make the worker its only writer (owner 10002, gateway `:ro`) - integration request 3.
   Nothing sweeps the processing cache in the product (M3's collector is composed nowhere;
   pre-existing): the 7-day retention holds only on lookup (`ProcessingCache.get`).
6. **`/readyz` does not wait for the preparation pool** (it counts it); a dead preparation
   runner is a death (`live: false`, exit 1), as an inference runner is.
7. **P-21 on this host**: the e4b block's fixed fake-vLLM ports were taken by ephemeral
   client sockets: the first journey run (R5a, 56880) errored at setup, the rerun passed;
   in the gate, one or two `test_fake_vllm.py` suites cases (56882/56886) failed so in R11,
   R15, R16 and R25, the journeys and three mutants in R26/R27; the suites passed in R14 and
   R27. Nothing else failed in any gate run.
8. **Rule breach, reported:** two service-free runs of `tests/contracts` were started without
   the lane env (R10a); `tests/contracts/v2/test_v1_projection_pg.py` then used the D
   harness's default task **d1** (`infrx-d1-postgres` on 127.0.0.1:55432), created and removed
   by the harness (no d1 container existed; the harness refuses a foreign one). Re-run on d5
   (R10b). No other lane's container was touched.
9. **"Exact and engine-derived" is as strong as the fake engine locally.** E2's fake answers
   `/tokenize` with the number its chat route reports as usage, by construction (it ignores
   the messages); what pins the body the loop sends is the exact-dict assertion of the text
   case and the part/`mm_processor_kwargs` assertions of the video case. When the two
   disagree (the `tokenize_count` fault) the job settles at the engine's usage and only the
   context check sees the stored count (`_pg__…disagrees_with_the_usage…`). The real check is
   the box run (E1B/certify; integration request 1).
10. **E4B's full mutant list has two survivors** (`engine_target_skips_to_ledger`,
   `published_digest_read_from_the_record`, `INFRX_MUTANTS=all
   tests/integration/backend/test_e4b_mutants.py`): pre-existing at `01103c9`, measured there
   by the reviewer (J-F4); this lane changed nothing E4B's list covers. Owner: E4B.

## Integration requests

1. **Coordinator / W / E4B (box; E1B/certify) - the real-engine check of the count** before
   the pilot serves: for one text job and one 10 s clip through the deployed gateway and
   worker, `select prepared_prompt_tokens from infrx.jobs` equals the engine's
   `usage.prompt_tokens` (`public.usage_events.prompt_tokens`) - and, directly, `POST
   127.0.0.1:8000/tokenize` with the body `engine_prompt_tokens` builds (`file://` under
   `PROCESSING_CACHE_DIR`, the pinned `mm_processor_kwargs`) against the same chat with
   `max_tokens: 1`: `jq '.count, ([.tokens[] | select(. == 248057)] | length)'`. Equal and the
   pads in [10, 1960]: the rule holds as written. Unexpanded: a W/M decision before any
   video job can be prepared. Record the double decode's latency (Limit 1).
2. **D / M - a durable zero-ref attach marker** (R99 Limit 1's 0019), so the preparation
   runner can wait for every job's acceptance, text included (Limit 2).
3. **I - least-privilege media root**: owner 10002, gateway mount `:ro` (the gateway writes
   nothing there since this lane); `tests/i` follows.
4. **E3B / I3B - merge this branch's `tests/integration` changes**: the pilot box without the
   emulation, e3bm74 retired, `fake_vllm.py`'s `/tokenize` (no E2 anchor moved).
5. **W - W3's anchors moved with the code** (`tests/w/w3_mutants.py`: 8 anchors in
   `service.py`, same edits; `stop_ignores_its_bound` is W3's original again) and I2B-R4's
   `main_cache_dir_unchecked` (writable): W3 85/85 and I2B-R4 13+4/17 killed here (R18/R19).
6. **Coordinator - rollout checklist**: after install, `journalctl -u infrx-worker | grep
   'prepared '` shows one INFO line per job (`prepared <job>: <n> prompt tokens (engine
   /tokenize, <ms> ms)`); `grep 'preparation of .* refused'` is empty on a healthy run (a
   WARNING names a refused job and its code; `not claimed` at INFO is a duplicate candidate
   and harmless); a stop logs both `drained:` and `drained preparation:`.
7. **E - P-21 in the gate's suites stage**: `tests/integration/test_fake_vllm.py`'s
   fixed-port cases (e4b: 56882 - `test_killing_the_engine_process_…_and_it_restarts`, which
   rebinds the port right after SIGKILLing its server - and 56886) lost their port in every
   gate run here but one, to sockets still holding it under the suites stage's load (client
   sockets in the ephemeral range 32768-60999, or the killed server's own connections); they
   pass alone (`17 passed`), and I2B-R4's R11b gate failed on the same case. A port outside
   the ephemeral range, or `port=0` with the chosen port read back, would end it.
8. **E4B - the two full-list survivors** (Limit 10), pre-existing at `01103c9`.

## Ruling candidates (proposed, not numbered; next free is R104)

- **Preparation's exact count travels with `prepared`.** `JobStore.prepared(lease, media=(),
  *, prompt_tokens=None)` (and `CreditJobStore`'s, the same operation): `prompt_tokens` is
  preparation's exact prompt count as the serving engine counts it, stored once with the refs
  and handed to the lease holder as `Work.prompt_tokens` / `WorkV2.prompt_tokens`; a bool or a
  non-integer is `invalid_request`, a count below 0 or above the job's `max_input_tokens`
  (inclusive) is `context_length_exceeded`, and a refused call changes nothing. `None`
  stores no count. A preparation lease reads its request through `load_work` /
  `load_work_credit` (the preparation read path, R46). The port's `heartbeat` renews a
  preparation lease as R52 says, never past `preparation_deadline_at`. Exported conformance:
  `dur_fence__prepared_stores_the_exact_prompt_count_once`,
  `credit_prepare__the_count_reaches_the_credit_work`.
- **A preparation count is the engine's, or there is none.** The count a preparation worker
  stores is the serving engine's own tokenization of the exact chat body the attempt will
  send; an answer it cannot check against the pinned profile (for a video: fewer
  `video_token_id`s than two-frame patches, or more than the profile's budget) is
  `dependency_unavailable` and the job is not prepared. A preparation worker never estimates.
- **(for decision, not implemented) A definitive preparation refusal may end the job.** A
  fenced `fail_preparation(lease, cause)` (or `prepared` refusing with a terminalization, as
  R39's shape) would let a preparation worker settle `preparation_failed` / `invalid_media`
  at once for a refusal no retry can change, instead of three lapsed leases (Limit 3).

## Verification log

- 2026-09-24: Authored at `4f7e32a` from the runs above (logs under `$L`, sha256 prefixes
  quoted), after folding in the review of `c035ee4`. Containers: `infrx-prepw-minio`
  (55781), the D harness's `infrx-d5-postgres`/`infrx-d5-valkey` (55436/55467, removed by the
  harness; one `infrx-d5-postgres` I left by stopping a superseded API-suite run was replaced
  by the harness's next run), the e4b stack (`infrx-e4b-*`, 56800-56899, torn down by each
  gate), the rehearsal's `infrx-prepw-*` (removed by its teardown). Runs I stopped myself,
  each superseded by a later one at a later SHA: two gates on a dirty or pre-fix tree, the
  gate at `fe2a82a` in its mutants stage (R14), an API-suite run whose tree changed under it.
  Plus the d1 slip (Limit 8). No AWS, hosted Supabase or pilot-box contact; nothing pushed.
