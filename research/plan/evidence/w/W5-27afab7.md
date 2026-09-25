# W5 — execution readiness and bounded worker recovery (lane evidence, head 27afab7)

Task W5 (consumer v1 wave 4; brief `research/plan/consumer-v1/02-runtime.md` §W5; test ids
ADMISSION-READY, DUR-FENCE, OPS-RECOVER; closes the W half of RV-05) plus coordinator
update 1 item (2): S3 finding F4 (no runtime producer of the reconciliation gauges).

- Branch `codex/w5-readiness`, worktree `.claude/worktrees/codex-w5-readiness`.
- Base `dff31efc`; code head **`27afab76`** (this file and the update JSON are committed on top).
- Taken in, not authored here (identical content to their lanes' commits, so they merge clean):
  `29bb7c39` and `96076fc8` = the coordinator's task-local port patches (`git am`; w5 Valkey
  moved to 55491 - never started by this lane), `47b6ad20` = F2C
  `2d5e4743` (F2C.a), `668acdff`/`0c3d515b`/`96f45ae8` = F2C `10c03d82`/`3a21e0bf`/`9bf7a95f`
  (F2C.b, F2C.a findings, F2C.d acceptance transcripts) - cherry-picked onto THIS branch only.
- Isolation: task `w5` (`INFRX_D_TASK=w5`: `infrx-w5-postgres` on 55445, database
  `infrx_w5`); no hosted DB, box, AWS or GPU was touched. Incident, reported: one run at about
  22:39Z of `tests/w/test_w5.py` without `INFRX_D_TASK` took the free d1 lock
  (`/tmp/infrx-d1-postgres-55432.lock`, written by this checkout) and its harness created and
  then removed its own labelled `infrx-d1-postgres` container; E2C's run had already released
  the lock (the harness refuses a held lock and never touches a foreign container), so nothing
  of E2C's was used or changed. Every later run set `INFRX_D_TASK=w5`.

## Changed paths (owned)

| Path | What |
|---|---|
| `apps/infrx-api/infrx/worker/preparation.py` | item 1 readiness barrier (`readiness=`, `claims`, `_ready`, `_manifest`); item 3 `PERMANENT`, `_end` (fenced `fail_preparation`), `most_video_tokens`, `ENCODER_CACHE_TOKENS`, `PreparationResult.ended` |
| `apps/infrx-api/infrx/worker/service.py` | F4: `WorkerService(reconciliation=)`, `_reconciled` after each successful `recover`, `PgReconciliation` + `RECONCILIATION_SQL` (read-only) |
| `apps/infrx-api/tests/w/test_w5.py` | 21 cases, 32 with parameters (items 1-3, E3C s04/s05, F4, acceptance replay, 2 PostgreSQL) |
| `apps/infrx-api/tests/w/w5_mutants.py`, `test_w5_mutants.py` | the W5 list: 25 service-free + 2 PostgreSQL mutants |
| `apps/infrx-api/tests/w/test_prep_worker.py`, `prep_worker_mutants.py` | `Prep.admit(ready=)` models the admission's manifest; `not_found` -> `not_claimable` for "not ready"; the video bound's expected numbers; 6 re-anchored mutants |
| `research/plan/evidence/w/W5-box-warmup.sh` | item 4 box script (for the coordinator, after run3) |
| `research/plan/evidence/w/W5-wiring-27afab7.patch` | the wiring request below, as a patch |

## What each item does

**1. Durable execution eligibility, text and media alike.** `PreparationRunner` now waits
(bounded, `ATTACH_WAIT_S`) for the job's committed manifest before anything is prepared,
counted or stored - for EVERY job, not only one carrying media. With F2C.a's `ReadinessStore`
wired (`readiness=`, D10's `PgLifecycle`) it claims through the store's marker-gated
`claim_preparation` (`PreparationRunner.claims`) and reads `readiness(job_id)`: `None` is NOT
READY (the previous runtime's job, an admission that never completed), never read as ready
from the attach record or the request (the cutover rule, no backfill); `sources == ()` is a
completed EMPTY manifest (text-only: prepared). Without a `ReadinessStore` (a pre-D10
composition) the manifest is the durable attach record (R99 (c)), where a PostgreSQL text job
has none: it fails closed. Refusal: `not_claimable` (F2C's `not_ready` code). Legacy and CREDIT
jobs are treated identically (parametrized; F2C's transcripts cover both regimes).

**2. Crash boundaries** (DUR-FENCE / OPS-RECOVER; existing fences and reconciliation, no new
engine promise): accepted-but-never-ready ends `preparation_failed` within
`1 + MAX_PREPUBLICATION_RETRIES` refused attempts, hold released once, engine never asked; a
lost queue wakeup is recovered from the durable dispatch row and a double redelivery runs the
job once (one generation, one debit at the admitted price); a preparation that dies before its
object, before or after its `prepared` commit is prepared once by the next process (one
content-addressed prepared object, one count, one inference dispatch; a committed `prepared` is
never prepared again); engine output not yet journalled when the process dies is never
relayed or billed (generation 2 alone in the journal/relay/debit), published output is never
regenerated (`lost_after_publication`, engine not re-asked, no debit); a cancel during
preparation (`prepared` refused typed, not queued, hold released once) or after queueing
(never claimed, engine never asked) settles once. Valkey stays a wakeup: every recovery path
reads the PostgreSQL-authority outbox/lease state.

**3. Temporary versus permanent.** A PERMANENT refusal - `unsupported_media` (codec, container,
over the deployed 82 s cap, past the encoder budget), `request_too_large`,
`context_length_exceeded` - ends the job at the FIRST attempt through the store's fenced
`fail_preparation(lease, cause)` (`invalid_media`, or `preparation_failed` for the context):
released free, hold released once, no preparation lease left to lapse and no redispatch. A
temporary one (tokenizer down, object missing, not ready) keeps the bounded lapse/requeue path
and is never ended early. A permanent refusal racing a client cancel is answered
(`already_terminal`) and settles once (the client's cause). Not duration alone: the `/tokenize`
video bound is now the pinned processor's worst case at the clip's budget
(`most_video_tokens`, parity with W4's `decide.worst_tokens` at every duration 1-120 s, 12.5,
81.5, 82.25), and W4's `ceiling_s(16384)` equals the deployed 82 s cap; the old bound
`longest_edge // 2048` refused the maximum-geometry answer at the cap (16,154 video tokens >
16,072) as `dependency_unavailable`, cycling three attempts into `preparation_failed`. A video
item past the served encoder budget (16,384, `serving-version.json` `engine_limits`) is refused
`unsupported_media` before it is queued (P-23: `/tokenize` does not apply that check; the chat
route would refuse it after queueing).

**4. Warmup/readiness.** No worker code: warmup is explicit operator work, never automatic, and
`/readyz` stays "engine up + pool running", never an end-to-end claim.
`W5-box-warmup.sh` records model load (restart -> engine `/health`), worker `/readyz`,
processor warmup (engine-direct `/tokenize` of an operator clip at the pinned budget) and engine
warmup (engine-direct chat, `max_tokens` 1) - no gateway, no key, no job: they cannot consume a
grant (a self-check refuses any plan in which they would, and `/readyz` `claimed` and
`prepare_claimed` must stay 0) - then the first valid request through the gateway with the
OPERATOR's own test key on never-seen bytes (a trailing `free` box of random bytes: processing
cache and TOKCOST memo miss, the cache file checked before/after, the worker log's
`engine /tokenize`), then the same bytes (hits, `memo of engine /tokenize`). Restart only; the
qualified engine configuration is untouched. `bash -n` and `DRY_RUN=1` (the plan + self-check)
pass here; the run itself needs the pinned engine.

**S3 F4.** The worker's reaper tick is the periodic reconciliation pass: after each successful
`recover`, `WorkerService` publishes `record_reconciliation` - drift rows of
`infrx.wallet_reconciliation` + `infrx.credit_wallet_reconciliation`, unknown-usage holds of
both regimes, and the sweep's own `unsettleable` set - on the worker's `/metrics`. A failed
read counts a reap error and publishes nothing (the last pass stands). Metric names unchanged.

## Commands (all from `apps/infrx-api`, `INFRX_D_TASK=w5` unless noted)

| Command | Exit | Result |
|---|---|---|
| `make api-env` (worktree root) | 0 | pinned env |
| `uv run --frozen pytest -q tests/w` at base `dff31efc` (no `INFRX_D_TASK`) | 1 | 257 passed, 9 skipped, 1 failed: `a_count_past_postgresql_int` - `HarnessBusy` on d1 (E2C held it) |
| `uv run --frozen pytest -q tests/w -rs` at `d04526b9` | 0 | 291 passed, 10 skipped (8 need a local MinIO: no S3 port for w5; 2 empty mutant parameter sets) |
| `INFRX_D_TASK=w5 uv run --frozen pytest -q tests/w -rs` at `27afab76` | - | NOT COMPLETED at handoff (session limit; no failure seen in its partial output). Last complete full run: `d04526b9` 291 passed, 10 skipped; every W5/prep case and all W mutant lists rerun green after |
| `INFRX_MUTANTS=all ... tests/w/test_w5_mutants.py -rs` at `27afab76` | 0 | 31 passed, 1 skipped: 25/25 service-free and 1/1 PostgreSQL mutants killed; the D10-dependent PostgreSQL mutant skips visibly here ("D10's PgLifecycle is not on this tree") |
| the same, `-k postgresql`, on a scratch copy = `16ff5771` + D10.a `dee59ed8` (`state/lifecycle.py`, `state/pgtesting.py`, `0019`) | 0 | 2 passed: both PostgreSQL mutants killed |
| `INFRX_MUTANTS=all ... tests/w/test_prep_worker_mutants.py tests/w/test_w5_mutants.py` at `da8fa206` | 0 | 104 passed, 8 skipped (PREP-WORKER's S3 list) |
| `INFRX_MUTANTS=all ... tests/w/test_{mutants,loop_mutants,w3_mutants,w4_mutants,worker_main_mutants,prep_worker_mutants,w5_mutants}.py -rs` at `16ff5771` | 0 | 551 passed, 13 skipped (4 + 8 need a local MinIO; 1 needs D10's adapter), 0 survived, 55 min |
| wiring check: `git apply --check` of `W5-wiring-27afab7.patch` on `27afab76` | 0 | applies |
| on `16ff5771` + the patch + D10.a (the patched files are unchanged since): `pytest tests/w/test_prep_worker.py tests/w/test_prep_worker_mutants.py -k "composes_the_preparation_pool or main_readiness_not_wired or main_reconciliation_not_wired or well_formed or anchor"` | 0 | 5 passed: the composed worker carries `PgLifecycle` and `PgReconciliation`; both wiring mutants killed |
| `bash -n W5-box-warmup.sh`; `DRY_RUN=1 bash W5-box-warmup.sh` | 0 | syntax ok; plan printed, self-check passed |

## Failed-then-passed regressions (each run against the worker without the change first)

| Case | Before (worker without the change) | After |
|---|---|---|
| `test_w5_ready__a_text_job_is_never_prepared_before_its_durable_manifest[legacy,credit]` | FAIL: the text job was prepared at once (`cause='prepared'`) | pass |
| `test_w5_ready__a_late_postcheck_decides_before_anything_is_prepared[refuses]` | FAIL: prepared (`/tokenize` asked) before the gateway's refusal cancelled it | pass |
| `test_w5_ready_pg__the_worker_prepares_only_what_admit_ready_marked` (on `16ff5771` + D10.a, real PostgreSQL) | FAIL: the pre-W5 worker prepared the previous runtime's unmarked job (`cause='prepared'`, count 1337) | pass |
| `test_w5_ready__a_video_job_is_never_claimed_before_its_marker[legacy,credit]` (E3C s04) | FAIL: the pre-W5 worker claimed the lease, waited 10 s for the attach, then `not_found` - the claim-before-attach E3C measured | pass |
| `test_w5_refuse__a_permanent_refusal_ends_the_job_once[over_the_cap,over_the_context]` | FAIL: refused but not ended (lease left to lapse) | pass |
| `test_w5_refuse__a_clip_past_the_encoder_budget_is_refused_before_it_is_queued` | FAIL: prepared and queued (21,952 video tokens) | pass |
| `test_w5_refuse__the_maximum_geometry_at_the_cap_is_prepared` | FAIL: `dependency_unavailable: the engine counted 16154 video tokens; the pinned profile makes 82..16072` | pass |
| `test_w5_reconcile__each_reaper_tick_publishes_the_reconciliation_gauges` | FAIL: no producer (`ImportError`: no `PgReconciliation`/`reconciliation=`) | pass |

Positive controls and crash-boundary cases that passed first time (their oracles are mutants):
`the_d1_marker_is_what_the_worker_reads`, `no_marker_is_never_legacy_ready`,
`an_unready_job_ends_at_its_preparation_deadline_released_once`, the five `test_w5_crash__`
cases, `a_temporary_failure_is_retried...`, `the_video_bound_is_the_processors_worst_case...`,
`a_permanent_refusal_racing_a_cancel...`, the acceptance replay, the PostgreSQL views case.
Negative control for RV-05: mutant `w5_readiness_barrier_media_only` (the pre-W5 media-only
wait) is killed by the text canary; `w5_no_marker_read_as_legacy_ready` by
`no_marker_is_never_legacy_ready`; `w5_permanent_refusal_left_to_lapse` is "unsupported media
cannot loop". Equivalent edit left out: sampling one frame fewer instead of two in
`most_video_tokens` (the budget's frame count is even, so one fewer always dominates).

## E3C s04/s05 (coordinator update 4) - each observation and the worker case that closes it

| E3C observation on the base tree | Worker case on this branch |
|---|---|
| s04: a text job prepared, run and debited while its acceptance was held | `test_w5_ready__a_text_job_is_never_prepared_before_its_durable_manifest[legacy,credit]`; `..._no_marker_is_never_legacy_ready[text]` |
| s04: a late rejection was charged too | `test_w5_ready__a_late_postcheck_decides_before_anything_is_prepared[refuses]` (nothing counted, released free once) |
| s04: a VIDEO job claimed its preparation lease before its attach | `test_w5_ready__a_video_job_is_never_claimed_before_its_marker[legacy,credit]` (claim refused `not_ready`: no lease, 0 attempts); PostgreSQL: `test_w5_ready_pg__the_worker_prepares_only_what_admit_ready_marked` (0 attempts on the unmarked job, D10.a copy) |
| s04: a permanent preparation refusal not terminal after 45 s | `test_w5_refuse__a_permanent_refusal_ends_the_job_once[over_the_cap,over_the_context]` (terminal at the first attempt) + `..._a_clip_past_the_encoder_budget...` - needs D10's `fail_preparation` on PostgreSQL (wiring 3) |
| s05: a video that lost its attach -> `preparation_failed` after 3 attempts | `test_w5_crash__accepted_but_never_ready_is_bounded_and_released_once[text,video]` (pre-D10 door) and `test_w5_ready__an_unready_job_ends_at_its_preparation_deadline_released_once[text,video]` (D1: never claimed, ends at the deadline) |
| s05: the other six crash points recover once | `test_w5_crash__a_lost_queue_wakeup...`, `..._a_preparation_that_dies_mid_way_is_prepared_once[3]`, `..._engine_output_before_the_journal_commit...[unpublished]`, `..._a_cancel_during_preparation_or_before_the_claim...` |
| s05: after publication `lost_after_publication`, never regenerated, no charge | `test_w5_crash__engine_output_before_the_journal_commit_is_never_relayed_or_billed[published]` |

The s04 video-claim observation and the text observations are the base tree's behavior with
no marker; on the merged tree they turn green only with wiring 1 (the runner's `readiness=`),
D10's 0019/`PgLifecycle` and G7's `admit_ready` together (wiring 4).

## Wiring requests (coordinator-owned files; not applied here)

1. **`apps/infrx-api/infrx/worker/__main__.py` `compose()`** - the exact patch is
   `W5-wiring-27afab7.patch` (with its test and two mutants):
   `PreparationRunner(..., readiness=PgLifecycle(connect, limits=limits))` (D10's
   `infrx.state.lifecycle.PgLifecycle`) and `WorkerService(..., reconciliation=
   PgReconciliation(connect))`; `tests/w/test_prep_worker.py::..._composes_the_preparation_pool`
   asserts both; `prep_worker_mutants.py` gains `main_readiness_not_wired` and
   `main_reconciliation_not_wired`. Verified on `16ff5771` + patch + D10.a (5 passed above).
2. **`Makefile` `api-mutants`**: add `tests/w/test_w5_mutants.py` after
   `tests/w/test_prep_worker_mutants.py` (in the same patch).
3. **F2C port + D10 PostgreSQL: `JobStore.fail_preparation(lease, cause) -> TerminalOutcome`**
   (R104's pending third candidate). Semantics as `tests/w/test_w5.py::FailPreparation` drafts
   them: fenced on the preparation lease like `prepared` (generation, owner, state, expiry, R29
   deadlines first); `cause` in {`invalid_media`, `preparation_failed`} else `invalid_request`;
   ends `failed` with no usage, settled by R21 (released free), preparation capacity, journal
   reservation and hold released in that transaction, one usage projection; `stale_lease` /
   `already_terminal` refusals change nothing; CREDIT jobs through the same door. Until it
   exists the worker logs and takes the bounded lapse path for a permanent refusal.
4. **G7 (gateway admission) must call `admit_ready`** for text and media (D1, one phase). With
   this branch's barrier and wiring 1, a job admitted without a marker is never prepared: the
   PostgreSQL round-trip cases (`test_prep_worker_pg__*`, `test_worker_main_pg__*`, E3B's pilot
   box) go green only when G7's `admit_ready`, D10's 0019/`PgLifecycle` and wiring 1 land
   together. Merge them as one integration step (and on the box only with 0019 applied).
5. **E/I8: scrape the worker's `/metrics`** (`127.0.0.1:WORKER_HEALTH_PORT`, 8002) for
   `infrx_reconciliation_drift`, `infrx_holds_unknown`, `infrx_unsettleable_jobs`: certify's
   soak `reconciled_at_end` (`certify.py:1150`) reads them from `--metrics-url` (the gateway);
   it must read the worker endpoint (`--worker-metrics-url`) for these series, and Prometheus
   must scrape both targets for the ReconciliationDrift/UnsettleableJobs rules.
6. **M5/D10 note**: `MediaPreparation.prepare` still reads the attach record (`attached`) for a
   video's sources; D10.a writes `job_media` rows in `admit_ready`, which `PgAttachments.get`
   reads - confirm on the merged tree with a video `admit_ready` job (not covered here: the
   integrated case is text-only).

## Proposed ruling text (the coordinator numbers them; no number is hardcoded in code/tests)

- **Readiness barrier (worker).** A preparation worker prepares a job only after its committed
  execution-ready marker: it claims through the `ReadinessStore`'s marker-gated
  `claim_preparation` and reads the manifest from `readiness(job_id)`. `None` is not ready for
  text and media alike and is never read as ready from any other record (the attach record, the
  request); `sources == ()` is a completed empty manifest. The wait is bounded
  (`ATTACH_WAIT_S`; `not_claimable` after it). Without a `ReadinessStore` (a pre-D10
  composition) the durable attach record is the manifest, where a zero-media job has none: it
  fails closed.
- **fail_preparation.** As wiring request 3. A preparation worker calls it only for a permanent
  refusal: `unsupported_media` and `request_too_large` -> `invalid_media`,
  `context_length_exceeded` -> `preparation_failed`; every other refusal lapses its lease.
- **R105 video bound (amendment).** For a video, the count must hold between one
  `video_token_id` per two-frame patch of the budget and the most the pinned processor gives any
  geometry at that budget (up to two frames fewer sampled than the budget's count, rounded per
  two-frame group - W4's `decide.worst_tokens`); outside that it is `dependency_unavailable`. A
  video item needing more than the served engine's encoder budget
  (`serving-version.json` `engine_limits.encoder_cache_tokens`, 16,384) is `unsupported_media`
  (permanent).
- **Reconciliation gauges.** The worker's reaper tick publishes the reconciliation pass after
  each successful `recover` (the names above, on the worker's `/metrics`); a failed read
  publishes nothing and counts a reap error.

## What needs the pinned engine (not run here)

`W5-box-warmup.sh` on the pilot box after certification run3: model load, processor/engine
warmup, the first cold-cache request and the warm repeat, each recorded apart; the checks
`warmup_claimed_nothing`, `fresh_digest_not_cached`, `first_request_count_source`,
`cold_cache_filled`, `second_request_count_source`. Also the E3C round trip in CREDIT mode on
the merged tree (G7 + D10 + wiring 1): crash-after-admit, delayed/rejected postchecks,
alias/card change and preparation racing cancellation on the real gateway.

## Open issues

- `WorkerLoop.results` keeps every `AttemptResult` (with its visible text) for the process's
  life: unbounded memory over a long soak (`loop.py`, W2). Not changed here (drain reads
  `results[seen:]`); a bounded deque plus a drain-local list is the fix - flag for W/M6.
- `context_length_exceeded` ends as `preparation_failed`: there is no over-context cause in
  `TerminalCause` (F2C, if an actionable customer cause is wanted).
- A source far below 2 fps (fewer than the budget's frames - 2) can exceed the worst-case bound
  (W4's `decide.worst_tokens` holds only above that): such a clip is `dependency_unavailable`
  (temporary, bounded to `preparation_failed`), not mis-counted. Not measured on the box.
- The PostgreSQL MinIO cases of `tests/w` (8) were not run: task `w5` has no S3 port.
- `ENCODER_CACHE_TOKENS` is a copy of the qualified engine's budget; a new serving version with
  another `--max-num-batched-tokens` must change it (and re-derive the cap).

## Estimate (remaining, W5 only)

Optimistic 3 h, likely 6 h, pessimistic 12 h; confidence medium. Basis: code and local proof
are done; left are applying wiring 1-2, the `fail_preparation` port/adapter from F2C/D10, the
merged-tree PostgreSQL round trip with G7's `admit_ready` (and fixing what it finds), and one
box run of `W5-box-warmup.sh` (about 1 h of operator time after run3).

## Handoff (2026-09-25, session limit)

- Done, committed: item 1 `53dc95ae`, `245dcb75`, `16ff5771`; item 2 `a8017a6b`, `27afab76`
  (E3C s04/s05); S3 F4 `39bf0151`; item 3 `da8fa206`; item 4 `d04526b9` (box script).
- Mid-flight: the final full `tests/w` run at `27afab76` did not complete (see the table); the
  all-W mutant lists completed at `16ff5771` (551 passed, 13 skipped, 0 survived) and the W5 list
  at `27afab76` (31 passed, 1 skipped).
- Next: rerun `INFRX_D_TASK=w5 uv run --frozen pytest -q tests/w -rs`; apply wiring 1-2
  (`W5-wiring-27afab7.patch`) with D10's `PgLifecycle`; F2C/D10 `fail_preparation`; G7
  `admit_ready`; merged-tree PostgreSQL round trip; `W5-box-warmup.sh` on the box after run3.

## Log

- 2026-09-25: W5 lane evidence at code head `27afab76` (items 1-4, S3 F4, F2C.d replay, E3C s04/s05 cases).
- 2026-09-25 (after the handoff commit): the final full run completed - `INFRX_D_TASK=w5 uv run --frozen pytest -q tests/w -rs` at `27afab76`: exit 0, 296 passed, 11 skipped (8 need a local MinIO, 1 needs D10's `PgLifecycle`, 2 empty mutant parameter sets), 761 s.
