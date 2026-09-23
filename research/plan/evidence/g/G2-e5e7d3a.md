# G2 — Synchronous chat and persistent SSE relay (track G)

## Task and status

- **G2** (manifest test ids `API-MODES`, `API-STREAM`, `DUR-OUTPUT`; also DUR-ADMIT's G half,
  F-BASE, M-FAILCLOSED, DUR-RLS for `/readyz`). Implementer: Claude Opus 5.5, lane
  `codex/g2-chat-relay`, worktree `.claude/worktrees/codex-g2`.
- **Status: implemented (fakes).** Not integrated. Integration is blocked by D5 (settlement,
  the psycopg `CatalogDirectory`, the cancel cause, G6B's PostgreSQL adapters), by D4 (the real
  `StreamStore` and its terminal journal event), by M (no durable object store adapter), and by
  **integration request W-new**, which is blocking. Without W-new, W1's engine settles every
  validated request that carries `stream` or `max_tokens` as `platform_error`; this lane's
  in-process harness found it. Pending inputs: P-01, P-18, P-04. No push, no box, no hosted
  project, no Valkey or PostgreSQL of this lane's own. `TASK_PORTS["g2"]` is registered, but no
  drill used it.

## Source

| | SHA |
|---|---|
| Dispatch base (`origin/claude/backend-impl` incl. "TASK_PORTS registers d4" 9c1c6ed) | `740bebf` |
| F2P wire-in merged on `claude/backend-impl` | `1baf0b8` |
| Merge of that into this lane (`git merge --no-ff origin/claude/backend-impl`) | `1c42822` |
| F cancel-cause merged on `claude/backend-impl` (`6803d2f`, `0024da2`), merged into this lane | `a16d237` |
| Implementation SHA (last code/test commit) | `e5e7d3a` |
| Head | the evidence commit that adds this file, on top of `e5e7d3a` |

Lane commits (`git log --oneline 740bebf..e5e7d3a`, G2's own):
`1babe5e`, `2b7a26f`, `bbd8669`, `aa72053` (WIP) → `42edf1f` (item 1), `8736ffa` (item 2),
`d7461f6` (item 3), `2ba5ec1` (item 4), `fc18982` (item 5), `d890e2c` (item 6), `665857f`
(item 5 follow-up), `e5e7d3a` (item 4: the R21 cancel causes).

## What was built

`apps/infrx-api/infrx/gateway/routes/relay.py` holds the `Relay`. `Relay.accept` is
`IngressDeps.accept` and runs, in this order:

1. Explicit async is refused (G3's); nothing durable happens before the refusal.
2. M2's `prepare_request`, then `stage` on its return value (S2M D1).
3. Admission, dispatched on the regime: `admit` for `legacy_usd`, `admit_credit` for `credit`.
   The `request` and `idem` go through as handed.
4. For a fresh CREDIT admission: the pinned card must be `ACTIVE_RATE_CARD_VERSION`, and the
   pinned serving revision must still serve what was asked (G1R Limit 2). A refusal here
   cancels the job that was just admitted.
5. `attach(job_id, refs)`. The job's org comes from the store's admission (R55; `Relay.job_org`
   is M's `job_org`).

Any other dependency failure is a typed 503 with `Retry-After`. A replay sets
`Idempotency-Replayed: true`, answers for the same job, and re-runs nothing. `Inference-Id` is
the job's request id, so a replay carries the original id.

The answer is an ASGI response that owns the job from acceptance until the job is terminal:

- **`_Answer` (sync).** Nothing is sent until `get_owned*` returns the committed outcome. The
  poll is bounded with backoff (marked `ponytail`; LISTEN/NOTIFY is the upgrade path). The
  outcome is then answered as the result object (`read_result`, `chat_success_nonstream`
  shape) or as its envelope.
  - A disconnect, an error, or the wait itself being cancelled all cancel the job durably. The
    cancel is shielded, so the handler's own cancellation cannot abort it.
  - The wait is bounded by the stored deadline plus a grace (R29/R79, no skew margin). Past the
    bound, a completion that won the race is answered; the gateway's own cancel is a 504 with
    state `cancelled`; a cancel it could not confirm claims no state.
- **`_Stream` (SSE).** Headers go out only after durable acceptance. The first frame is
  `infrx.progress` with `job_handle` and `request_id` and has no `id`.
  - After that, every model frame is a chunk read back through `StreamStore.read_owned` (bounded
    pages), and only `visible` text is relayed (R58/R80). Keepalives are SSE comments every
    `SSE_KEEPALIVE_S`.
  - The stream ends on the terminal journal event (R30). Because D3's terminalizations write no
    terminal event yet, it also ends on `get_owned*`'s committed outcome once the journal holds
    nothing more; the stream always drains before ending. Errors after the headers are an
    `infrx.error` frame, then `[DONE]` once a terminal commit is known.
  - Cancels: a disconnect cancels durably, including a client gone before the first byte (there
    is no generator `finally` involved). A replay gap or an expired journal also cancels,
    reported with its own code. A journal read that fails for want of the database is retried,
    and a failed read is never taken for an empty journal.
  - If the process stops (the task is cancelled from outside) after the identity frame went
    out, the job is left to its worker.
- **`Relay.cancel(org, handle, cause=)`** is D3 request 2's contract as rendered: the committed
  outcome wins; a foreign handle is `not_found` like an unknown one; after `already_terminal`
  the outcome is read with `get_owned*`. Every cancel names its R21 cause on the merged
  cancel-cause port:
  - a client that left (including a send that failed) is `client_disconnected`;
  - the sync or stream bound is `sync_deadline`;
  - everything else is `client_cancelled`.

  Until D5's 0018, `PgJobStore` refuses the two new causes (`UnsupportedParameter`,
  `param="cause"`, before any SQL). The relay then cancels with the default cause: the job is
  never orphaned and the caller never sees that 400. This interim is marked `ponytail` and D5
  lifts it.

`apps/infrx-api/infrx/gateway/pilot.py` provides `build_ingress_deps(rt, catalog=, stream=,
objects=, jobs=None, index=None)`, which the coordinator's `create_app` calls at the cutover.
It refuses to build without the three adapters that do not exist yet: catalog, stream, objects.
Otherwise it builds:

- `PgJobStore` over a psycopg pool. The pool's `configure` runs `set role service_role` and the
  deployment's statement timeout (D2 request 7).
- The `Relay`. Its regime comes from `DeploymentSettings.accounting_regime` and its card from
  `PilotSettings.active_rate_card_version`, both read from settings now that the wire-in has
  merged.
- One `MediaUploads` as `rt.media_store`, and one `LargeBodies` from
  `LARGE_BODY_LIMIT`/`LARGE_BODY_THRESHOLD_BYTES` as `rt.large_bodies`, shared with G4U's
  router.
- The Q3 `Reconciler` over `ValkeyScheduler` (caps from `PilotSettings.max_index_*`), with a
  unique worker id per process.
- The gateway `Registry`, plus a call to `silence_transport_logs()`.
- `REQUIRED_CHECKS` as cached `Probe`s. The first answer is asked on the probe's own thread at
  registration; the lifetime task refreshes it afterwards. Journal = D4's `usage()`;
  price_source = the served model's approved card, which in the CREDIT regime must be the
  active one.

`pilot.lifespan` opens the pool, runs the reconciler and the probe refresh, and stops both on
shutdown.

`apps/infrx-api/infrx/gateway/routes/ingress.py`:

- `/readyz` answers a direct loopback peer only. It reuses `observe/route.py`'s rule, so a peer
  that sent a proxy header gets the unknown-path 404; this unblocks I2B's `wait_ready`.
- `assert_route_table(app)` is new (G1R review C4).
- The chat endpoint is named as the ingress's, so the route table (and E3B dr17's module
  check) sees `infrx.gateway.routes.ingress`.

## Per item

| Item | Commit(s) | Killing cases | Mutants (tests/g/mutants.py) | Oracle |
|---|---|---|---|---|
| 1 Acceptor | `1babe5e`, `42edf1f` | `test_dur_admit__admission_takes_the_prepared_record_never_the_validation_record`, `test_api_modes__explicit_async_is_refused_before_anything_durable`, `test_api_modes__a_capability_drift_after_validation_cancels_the_admitted_job`, `test_api_modes__a_card_this_deployment_did_not_approve_is_refused_and_released`, `test_dur_admit__an_outage_at_acceptance_is_a_retryable_503_with_no_side_effects`, `test_dur_output__a_lost_answer_is_replayed_by_key_with_one_settlement` | new: `admits_the_validation_record`, `stages_the_validation_record`, `staged_refs_never_attached`, `attach_without_the_jobs_org`, `async_admitted`, `pin_drift_ignored`, `unapproved_card_admitted`, `refused_admission_left_running`, `outage_is_a_500`, `replay_header_dropped`, `replay_names_a_new_job`, `replay_reaccepted` | DUR-ADMIT (G), API-MODES |
| 2 Sync wait | `8736ffa` | `test_api_modes__success_is_answered_only_after_the_terminal_commit`, `…__a_sync_disconnect_cancels_durably`, `…__a_sync_wait_cancelled_from_outside_still_cancels_the_job`, `…__a_sync_timeout_cancels_and_answers_the_deadline`, `…__a_timeout_whose_cancel_fails_claims_no_state`, `…__a_timeout_that_races_a_committed_result_returns_the_result`, `…__server_timing_and_the_registry_count_what_the_gateway_saw` | new: `success_before_terminal_commit`, `result_not_read`, `sync_disconnect_not_cancelled`, `sync_wait_orphans_on_cancellation`, `cancel_unshielded`, `timeout_leaves_job_running`, `unconfirmed_cancel_claims_a_state`, `timeout_hides_committed_result`, `server_timing_dropped`, `rejections_uncounted`, `acceptances_uncounted` | API-MODES, DUR-OUTPUT |
| 3 SSE relay | `d7461f6` | `test_api_stream__the_first_frame_names_the_job_before_any_model_output`, `…__keepalives_are_comments_never_data`, `…__only_visible_text_reaches_the_wire`, `…__done_is_sent_only_after_the_terminal_commit`, `…__a_disconnect_mid_stream_cancels_durably`, `…__a_client_gone_before_the_first_byte_still_cancels`, `…__a_replay_gap_ends_the_stream_honestly_and_cancels`, `…__without_a_terminal_event_the_committed_outcome_ends_the_stream`, `…__a_journal_read_that_fails_is_retried_not_the_end`, `…__an_upstream_error_is_an_honest_terminal_error`, `…__a_gateway_restart_mid_stream_leaves_the_job_to_its_worker` | new: `progress_frame_without_handle`, `keepalive_as_data`, `keepalive_never_sent`, `relay_reads_raw`, `stream_ends_on_an_empty_page`, `done_without_its_cursor`, `usage_frame_dropped`, `post_header_error_as_status`, `stream_disconnect_not_cancelled`, `unstarted_stream_leaks`, `gap_reported_as_unknown`, `journal_outage_ends_the_stream`, `no_done_after_an_ended_job`, `outcome_fallback_ignored`, `restart_cancels_a_named_stream` | API-STREAM, DUR-OUTPUT |
| 4 Cancellation rendering and R21 causes | `2ba5ec1`, `e5e7d3a` | `test_api_modes__a_foreign_or_unknown_handle_is_not_found_and_changes_nothing`, `…__a_replay_of_a_cancelled_job_is_rendered_cancelled_never_failed`, `…__a_credit_job_that_expires_unclaimed_is_rendered_expired`, `…__already_terminal_on_cancel_reads_the_committed_outcome`, `…__a_timeout_that_races_a_committed_result_returns_the_result`, `…__a_store_that_cannot_record_the_cause_still_cancels[disconnect/deadline]`, plus the disconnect/timeout cases of items 2 and 3 (now asserting the recorded cause) | new: `held_unknown_rendered_as_its_settlement`, `cancel_rendered_as_failure`, `expired_rendered_failed`, `foreign_cancel_swallowed`, `already_terminal_not_read`, `disconnect_cause_dropped`, `deadline_cause_dropped`, `stream_disconnect_cause_dropped`, `send_failure_cause_dropped`, `cause_fallback_missing`; re-anchored at `e5e7d3a`: `sync_disconnect_not_cancelled`, `timeout_leaves_job_running`, `stream_disconnect_not_cancelled`, `unstarted_stream_leaks`, `foreign_cancel_swallowed` | API-MODES (D3 request 2), R21 |
| 5 Composition, readiness, route table | `aa72053`, `fc18982`, `665857f` | `test_f_base__a_pilot_is_not_built_without_its_durable_adapters`, `…__the_composed_pilot_admits_in_the_configured_regime`, `…__one_large_body_bound_and_one_media_store_per_process`, `…__readiness_probes_are_cached_answers_the_lifetime_refreshes`, `…__the_pool_sets_the_service_role_on_every_connection`, `…__the_lifespan_runs_the_dispatch_relay_until_shutdown`, `…__exactly_one_chat_route_and_it_is_the_ingress`, `test_dur_rls__readiness_is_protected`, `test_f_base__readiness_explains_component_state_to_a_direct_loopback_peer` (renamed from `…_to_an_authenticated_caller`) | new: `pilot_built_without_catalog`, `pilot_built_without_stream`, `pilot_built_without_objects`, `regime_not_read`, `active_card_not_read`, `two_large_body_bounds`, `media_store_not_shared`, `probe_never_refreshed`, `probe_asked_on_every_read`, `service_role_not_set`, `reconciler_not_run`, `relay_worker_id_shared`, `readyz_open_behind_proxy` (in `observe/route.py`), `second_chat_route_tolerated`, `chat_route_names_intake`; re-anchored: `readiness_is_public` (now the loopback gate); case renamed for `readiness_hides_the_components` | F-BASE, M-FAILCLOSED, DUR-RLS |
| 6 Matrix + DUR-OUTPUT drills | `bbd8669`, `d890e2c` | `test_api_modes__the_sync_and_sse_matrix_answers_from_committed_state[text/video_url × sync/stream]`, `test_dur_output__a_kill_before_the_first_committed_chunk_is_retried_to_one_output`, `…__a_kill_after_the_first_committed_chunk_is_never_regenerated`, `…__a_lost_answer_is_replayed_by_key_with_one_settlement`, `test_api_stream__an_upstream_error_is_an_honest_terminal_error`, `test_dur_admit__an_outage_at_acceptance_is_a_retryable_503_with_no_side_effects`, `test_api_stream__a_gateway_restart_mid_stream_leaves_the_job_to_its_worker` | new: `prepublication_loss_not_retried`, `published_output_regenerated` (both `contracts/fakes/state.py`), plus the item 1–3 mutants that name these cases | API-MODES, API-STREAM, DUR-OUTPUT |

**Brief example names that were not used.**

- `relays_uncommitted_chunk` is structural. The relay has no byte source other than
  `read_owned`: W2's `relay=` hook is not used, because the pilot worker runs in another
  process. No single edit in the relay creates a second source, so no mutant was written. The
  matrix and SSE cases assert that the wire equals the committed journal.
- `replay_readmits` became `replay_reaccepted`.
- `done_before_terminal_commit` became `stream_ends_on_an_empty_page` and
  `done_without_its_cursor`.
- `cancel_error_on_completed` is covered by `timeout_hides_committed_result` and
  `already_terminal_not_read`.
- `held_unknown_rendered_failed` became `held_unknown_rendered_as_its_settlement` and
  `cancel_rendered_as_failure`.
- `expired_rendered_billed` became `expired_rendered_failed`. The case also asserts that the
  CREDIT hold is released and that there is no settlement.

## Requirement coverage

| Test id | Invariant | Cases (all in `apps/infrx-api/tests/g/`) |
|---|---|---|
| API-MODES | No surprise 202; async refused before any side effect | `test_relay_sync.py::…explicit_async_is_refused_before_anything_durable` |
| API-MODES | A sync success follows the terminal commit; the body is the committed result | `…success_is_answered_only_after_the_terminal_commit`, matrix `[*-sync]` |
| API-MODES | A disconnect, a timeout or a cancelled wait cancels durably, so no sync execution is orphaned | `…a_sync_disconnect_cancels_durably`, `…a_sync_wait_cancelled_from_outside_still_cancels_the_job`, `…a_sync_timeout_cancels_and_answers_the_deadline` |
| API-MODES | Durable state wins over a timeout, and an unconfirmed cancel claims no state | `…a_timeout_that_races_a_committed_result_returns_the_result`, `…a_timeout_whose_cancel_fails_claims_no_state` |
| API-MODES | A replay is the same job, marked, never re-run | `test_relay_matrix.py::…a_lost_answer_is_replayed_by_key_with_one_settlement` |
| API-STREAM | Split `<think>` in the journal's `raw` never reaches the wire; the real `ReasoningFilter` path is covered too | `test_relay_sse.py::…only_visible_text_reaches_the_wire` |
| API-STREAM | Upstream errors before and after the headers give an honest terminal error, with no inflight leak | `test_relay_matrix.py::…an_upstream_error_is_an_honest_terminal_error[pre/post × sync/stream]` |
| API-STREAM | Keepalive and progress are distinct from model chunks | `…keepalives_are_comments_never_data`, `…the_first_frame_names_the_job_before_any_model_output` |
| DUR-OUTPUT | Retry happens only before publication; one output | `test_relay_matrix.py::…a_kill_before_the_first_committed_chunk_is_retried_to_one_output` |
| DUR-OUTPUT | No regeneration after publication; committed prefix plus honest error; hold `held_unknown` | `…a_kill_after_the_first_committed_chunk_is_never_regenerated` |
| DUR-OUTPUT | Lost terminal ack: the same durable replay, one settlement, no early success | `…a_lost_answer_is_replayed_by_key_with_one_settlement`, `…done_is_sent_only_after_the_terminal_commit` |
| DUR-OUTPUT | Physical connection loss keeps a recoverable identity | `…a_gateway_restart_mid_stream_leaves_the_job_to_its_worker` |
| DUR-SETTLE (G half) / R21 | A disconnect is recorded `client_disconnected` and the sync bound `sync_deadline`; a store that cannot yet record the cause still cancels (interim) | `…a_sync_disconnect_cancels_durably`, `…a_sync_timeout_cancels_and_answers_the_deadline`, `test_relay_sse.py::…a_disconnect_mid_stream_cancels_durably`, `…a_client_gone_before_the_first_byte_still_cancels`, `…a_store_that_cannot_record_the_cause_still_cancels` |
| DUR-ADMIT (G) | Admission and staging take the prepared record; an outage at accept leaves nothing behind | `…admission_takes_the_prepared_record_never_the_validation_record`, `…an_outage_at_acceptance_is_a_retryable_503_with_no_side_effects` |
| F-BASE / M-FAILCLOSED | The pilot is composed from real adapters or refused; one route handler; one bound per process | `test_composition.py` (7 cases) |
| DUR-RLS | Readiness serves a direct loopback peer only | `test_startup.py::test_dur_rls__readiness_is_protected` |

Every new case is named by at least one killed mutant; `test_every_case_is_covered_by_a_mutant`
passes.

## Environment

- Linux 7.0.0-1010-aws; Python 3.12.3; uv 0.11.8; starlette 1.6.0, fastapi 0.141.1,
  httpx 0.28.1, pydantic 2.13.5; Docker 29.6.2 (used only by `make check`'s shared D harness).
- Classification: local development host, fakes only. No engine or GPU was used; vLLM is W's
  scripted `FakeUpstream` behind the real `VllmEngine`.
- The in-process world is `tests/g/relay_support.World`. It contains:
  - the contract `FakeJobStore` and `FakeStreamStore` (legacy regime), or the wire-in's
    `credit_jobstore_factory` (CREDIT regime);
  - M's real `MediaUploads` over `InMemoryObjectStore`, with the fetcher on an
    `httpx.MockTransport` and an injected probe;
  - W's real `AttemptRunner`;
  - one injected clock.

## Commands and results

All commands run from `apps/infrx-api` unless noted. Counts are quoted from the output.

`make api-env` (repository root), 2026-09-23 ~05:15Z: exit 0 (`uv sync --frozen --all-extras`).

G focused suite at `e5e7d3a`:
```
uv run --frozen pytest -q -p no:cacheprovider tests/g --ignore=tests/g/test_mutants.py --ignore=tests/g/ops/test_mutants.py
385 passed, 2 warnings in 3.42s
```
The base, `1c42822` after the merge, had `333 passed`; `665857f` had `383 passed, 2 warnings in
3.31s` (EXIT=0, 2026-09-23T06:23:19Z).

The whole G mutant list, detached, at `e5e7d3a` (2026-09-23T06:53:05Z → 07:04:00Z):
```
INFRX_MUTANTS=all uv run --frozen pytest -q -p no:cacheprovider tests/g/test_mutants.py
264 passed in 654.68s (0:10:54)
EXIT=0
```
That is 257 mutants (192 before G2, 65 new) plus the list's well-formedness, coverage and runner
self-tests. The same list at `d890e2c`, before the cause work: `259 passed in 706.31s
(0:11:46)`, EXIT=0.

`tests/contracts` quick at `665857f` (2026-09-23T06:27:21Z). The 3 failures are the D harness's
`HarnessBusy`, not a contract case:
```
FAILED tests/contracts/v2/test_v1_projection_pg.py::test_every_pre_cutover_usage_row_reads_as_a_legacy_usd_dto
FAILED tests/contracts/v2/test_v1_projection_pg.py::test_project_v1_usage_reads_the_raw_rows_to_the_same_dtos
FAILED tests/contracts/v2/test_v1_projection_pg.py::test_the_legacy_statement_is_a_rollout_hold_never_a_credit_figure
3 failed, 1004 passed in 21.03s
EXIT=1
```
The D harness was held by codex-e3b2 at the time (`another run holds
/tmp/infrx-d1-postgres-55432.lock (pid 1120259 checkout …/codex-e3b2)`). At `e5e7d3a`,
`tests/contracts` runs inside both orderings below, where these three pass (the harness was
free) and no contracts case fails.

Both orderings at `e5e7d3a`, with `INFRX_Q_VALKEY_PORT=55486` (the coordinator's private Q
Valkey port). Two earlier attempts produced no counts:
- the one at `665857f` was stopped once the cancel-cause merge superseded it;
- the one at `e5e7d3a` on the shared `infrx-q3-valkey` went 30 minutes without finishing its
  first ordering and was stopped (the coordinator reports cross-lane kills of that container).
```
legacy-first  (tests/test_app_factory.py tests/test_gateway_auth.py tests/test_inflight.py tests/test_media.py
               tests/contracts tests/g tests/i tests/j tests/m tests/q tests/t tests/w), 2026-09-23T07:26:18Z
EXIT=1
=========== 37 failed, 2646 passed, 2 warnings in 607.33s (0:10:07) ============
track-first   (the same paths, legacy files last), 2026-09-23T07:36:29Z
EXIT=1
=========== 37 failed, 2646 passed, 2 warnings in 606.15s (0:10:06) ============
```
In each ordering the 37 failures are the same pre-existing red:
- 36 (legacy-first) / 36 (track-first) × `tests/i/test_mutants.py::test_mutant_is_killed[...]`, each `broken_runner` with "pristine
  baseline: the unmutated tree fails the list's own cases … test_backend_deploy__the_config_schema_is_every_name_the_runtime_reads";
- that case itself: FAILED tests/i/test_packaging.py::test_backend_deploy__the_config_schema_is_every_name_the_runtime_reads.

Owner: coordinator/I (Limit 12). Nothing G2 touched fails in either order.

`make -k check` from the repository root at `e5e7d3a`, run last, detached (`setsid nohup`), with
`INFRX_Q_VALKEY_PORT=55486`. It started once the default D harness lock was free, at
2026-09-23T07:47:04Z, and ended at 09:24:30Z with `EXIT=2`. `-k` was used so that every target
reports. Per target:

| Target | Tail (quoted) | Attribution |
|---|---|---|
| `api-test` | `313 failed, 2735 passed, 26 xfailed, 2 warnings in 846.78s (0:14:06)` | 276 are `HarnessBusy`: codex-g4u took `/tmp/infrx-d1-postgres-55432.lock` between the wrapper's check and this run's `tests/d` (all of `tests/d` plus the 3 PG projection cases; 215 failure sections contain it, and 276 `E … HarnessBusy` lines). 37 are the pre-existing I red (Limit 12). None is in `tests/g`. |
| `api-mutants` | `618 failed, 1797 passed in 4778.14s (1:19:38)` | 427 `tests/d/test_migration_mutants.py` + 8 `tests/d/test_signup.py` are `HarnessBusy` (435 lines). 182 `tests/i/test_mutants.py` are `broken_runner` from the pre-existing I baseline. The last is `tests/w/test_w3_mutants.py[sigint_not_handled]`: `broken_runner`, `TimeoutExpired`, an artifact of detached execution (below). **The G list, `tests/g/ops`, contracts, m, q, j, t and the rest of w: no failure.** |
| `console-test` | `# tests 289` / `# pass 288` / `# fail 1` (`lib/utils.test.ts`) | This worktree has no `apps/app/node_modules` (`WARN Local package.json exists, but node_modules missing`); G2 touches no console file. |
| `console-lint` | `sh: 1: eslint: not found` | same: `node_modules` absent |
| `console-typecheck` | `ERR_PNPM_RECURSIVE_EXEC_FIRST_FAIL Command "next" not found` (Error 254) | same |
| `console-mutants` | `199 mutants: 199 killed …`, `40/40 mutants killed …`, `64 mutants, 64 killed, 0 not killed`, `104 mutants: 104 killed …` | green |
| `bench-test` | `1 failed, 66 passed in 9.57s` (`test_a_second_ctrl_c_cannot_lose_the_summary`: `SIGINT` handler is `SIG_IGN`) | An artifact of detached execution: a background job inherits `SIGINT` ignored. Re-run in the foreground: `67 passed in 9.08s`. |

The two `SIGINT` items were re-run in the foreground, 2026-09-23T09:25:30Z:
```
== bench-test in the foreground
67 passed in 9.08s
sigint_not_handled (foreground): 1/1 killed
```

The D suite on the plain image was retried under the lock-wait loop once the harness was free
(`dretry.sh`, the brief's HarnessBusy rule):

- **Attempt 1** (2026-09-23T09:26:20Z; the lock holder's pid was dead) ran and ended
  `276 failed, 92 passed, 26 xfailed in 169.34s (0:02:49)`. Every D failure is
  `tests.d.pgharness.ForeignContainer: refusing to use infrx-d1-postgres: another checkout's run
  (…/codex-g4u) - this run will not start, stop or delete it.` That container was created
  2026-09-23 09:00:45 UTC by codex-g4u's run and was left in place.
- **Attempt 2** waited, polling every 60 s from 09:30:19 to 10:00:24, for the lock and that
  container to clear. They never did (the last poll: `D harness busy: … codex-e3b2; container:
  infrx-d1-postgres 2026-09-23 09:00:45`), and it was stopped.

**`tests/d` on the plain image, and the D mutant lists: not run** — blocked by another lane's
container, which this lane may not touch. G2 touches no SQL and no D code. The `tests/d` numbers
belong to D's own harness runs.

The supabase-image pass of `tests/d`: not run (not touched; D's harness).

## Failure drills (store state asserted, not only HTTP)

| Drill | Injection | Durable state after | Retry / duplicate behaviour |
|---|---|---|---|
| Kill before the first committed chunk | Worker claims (gen 1) and never appends; lease TTL passes; `recover()` | requeued, then gen 2 by `worker-b`: `succeeded`, journal generations `{2}`, one `usage_projection` | one output, relayed once (sync and SSE) |
| Kill after the first committed chunk | Hand lease appends `Two people`, TTL passes, `recover()` | `failed / lost_after_publication`, debit 0, hold `unknown`, journal generations `{1}` | never regenerated; SSE relays `Two people`, then `stream_interrupted`, then `[DONE]`; sync answers 500 `internal_error` |
| Lost terminal ack plus retry with the same key | `FailurePlan.crash_after_commit("complete")`; the client retries with the same `Idempotency-Key` | one job, ledger unchanged by the retry, one `usage_projection` | 200, same body, `Idempotency-Replayed: true`, same `Inference-Id` |
| Upstream error before or after the engine's headers | FakeUpstream `engine_error_pre_headers` / `engine_error_post_headers` | `failed`, debit 0 | sync 500; SSE 200 with the committed prefix, then `infrx.error` `stream_interrupted`, then `[DONE]`. A refusal before the gateway's headers (no credit) is a 402 JSON envelope even on a stream |
| DB outage at accept | `FailurePlan.fail("admit", ConnectionError(<DSN>))` | no job, journal total 0, reserved 0 | 503 `dependency_unavailable` with `Retry-After`, DSN not echoed |
| Object store outage at accept | `put_if_absent` raises | same as the DB outage | same |
| Index outage at accept | not injected; nothing at acceptance touches the index | — | the outbox is PostgreSQL's; Q3's relay delivers it later |
| Gateway restart mid-stream | the relay's task is cancelled after the identity frame and one delta | job still `running`; the worker completes it | a new process (`World.restart()`) answers the same key's retry with the whole journal and `[DONE]` at the terminal cursor |
| Mid-stream journal read failure | `FailurePlan.fail("read_owned", on_call=2, ConnectionError)` | `succeeded` | retried, completes normally, DSN not echoed |

Cleanup: the drills are in-process; no container, file or port was used outside pytest's
temporary directories. The suite runs used the shared D harness on 55432, and the Q harness
started and removed `infrx-q3-valkey-55486` itself. Afterwards `docker ps -a` shows no container
of this lane's, and the scratch tree copies were deleted. No `infrx-g2-*` container was ever
created.

## Artifacts

The logs are in the session scratch directory and are not durable; each tail is quoted here:
`g2logs/g-mutants2.log` (and `g-mutants.log` at `d890e2c`), `orderings3.log` with the verbose
`legacy-first.v.log`/`track-first.v.log`, `cut2-*.log`, `cut-*.log` (the first application, at
`665857f`), `base-integration-pytest.log`, `cutover.diff` (sha256 `465b425f…`), `W-new.diff`
(sha256 `4e91aac7…`), `wnew2.log`, `check.log` and `dretry.log`. No
credential, customer content or signed URL appears in any of them. The DSN-shaped strings in
cases are fake and are asserted absent from responses.

## Changes (owned paths only)

- New: `infrx/gateway/routes/relay.py`, `infrx/gateway/pilot.py`, `tests/g/relay_support.py`,
  `tests/g/test_relay_sync.py`, `tests/g/test_relay_sse.py`, `tests/g/test_relay_matrix.py`,
  `tests/g/test_composition.py`, and this evidence file.
- Modified:
  - `infrx/gateway/routes/ingress.py`: `/readyz` loopback gate, `assert_route_table`, chat
    endpoint module.
  - `tests/g/test_startup.py`: readiness cases now use a loopback client.
  - `tests/g/test_catalog.py`: the operator case no longer uses `/readyz`, which is not a
    tenant surface any more.
  - `tests/g/mutants.py`: G2's 65 mutants plus 2 re-anchored or renamed.
- `tests/g/support.py`: not edited. G4U's imports are untouched.
- No migration and no SQL. No other lane's files; the cutover and W-new are diffs handed over,
  not committed.
- Rollback: revert `42edf1f..e5e7d3a` (nothing mounts `relay`/`pilot` before the cutover).

## Limits

1. **Fakes only.** Stores: the contract `FakeJobStore`/`FakeStreamStore` and the wire-in's CREDIT
   fake. Catalog: `FakeCatalogDirectory`. Object store: `InMemoryObjectStore`. The real
   `PgJobStore` path is built by `pilot.build_ingress_deps` (pool, configure) but never
   connected here. Real-store evidence belongs to the coordinator's composition and to E3B
   phase 2.
2. **D4 / D5 / catalog / object store are pending.** `build_ingress_deps` refuses without
   `catalog`, `stream` and `objects`, so the cutover as handed over cannot start a gateway
   until those adapters exist. That is the fail-closed state, not a bug.
   - The journal probe calls `stream.usage()`, D4's API, which the contract fake lacks; the
     tests add it.
   - D4's terminal-event trigger is expected. Until it lands, the SSE relay ends on
     `get_owned*` (the fallback is tested with `jobs.stream = None`).
3. **The cancel cause, interim on PostgreSQL (R21).** The relay passes the true cause, and the
   fake records it. `PgJobStore` refuses `client_disconnected`/`sync_deadline` until D5's
   0018, so on PostgreSQL the relay falls back to `client_cancelled` and the recorded cause is
   wrong until then. No money moves: a cancel settles debit 0 or `held_unknown`.
   - The relay's own non-client stops (a replay gap, an expired journal, a store failure, a
     sync wait cancelled by the process) record `client_cancelled`. `CANCEL_CAUSES` has no
     platform cause, and only a disconnect and the deadline were asked for.
   - A refusal after admission (capability drift, an unapproved card) also records
     `client_cancelled` and settles `released_free`.
4. **Worker timings are not carried.** `Server-Timing` holds only the gateway's `prepare`
   phase. The worker's `prefill`/`generate`/`journal`/`persist`/`settle` need a durable carrier
   across processes (W3 request 9, owner D/W3). They are pending, never invented.
5. **Video by upload is G4U's.** Those matrix cells are pending on G4U; the relay stages any
   store-produced ref.
6. **CREDIT execution.** The matrix and drills run the legacy regime end to end. A CREDIT job
   cannot be claimed by W's runner until WorkV2 (0016 MY-3). The CREDIT cases cover admission,
   the pin and card recheck, the release and the expiry rendering.
7. **W-new is blocking.** W1's `VllmEngine.check_parameters` refuses `stream`, `max_tokens`
   and `max_completion_tokens`. The frozen `normalized_request.json` fixture carries these in
   `parameters`, and G1R's validator emits them, so every validated SSE or capped request would
   settle `platform_error`. The harness runs the engine behind `RecordKeysDropped`, a test-only
   application of W-new's effect. W-new itself was applied, run and reverted (below).
8. **Relay and media state are in process.** M's staging indexes (`refs`, `by_job`, uploads)
   live in one process (M1/M3 limits), so the gateway and M's preparation must share a process
   until M persists them. The relay's `_attaching` map is bounded by in-flight accepts.
9. **The poll is a poll.** Sync and SSE each poll the store (50 ms growing to 1 s). With
   PostgreSQL that is about one query per open stream per second. LISTEN/NOTIFY is the upgrade
   and is marked `ponytail`. The first-progress and keepalive latencies are unmeasured
   (P-18).
10. **Replays re-prepare.** A replay re-fetches or re-stages media before admission reports
    the replay: it is idempotent (write-once, content-addressed) but wasteful.
11. **Graceful shutdown semantics.** Under `--timeout-graceful-shutdown`, uvicorn cancels open
    SSE tasks. A named stream's job is left to its worker; a sync wait's job is cancelled,
    because a sync caller holds no identity.
12. **Pre-existing reds, not G2's** (quoted under results):
    - `tests/i/test_packaging.py::test_backend_deploy__the_config_schema_is_every_name_the_runtime_reads`
      fails on the merged base `1c42822`. The wire-in added `ACCOUNTING_REGIME`,
      `ACTIVE_RATE_CARD_VERSION` and `PROVIDER_DEV_ALLOCATION_CEILING_CREDIT` to `from_env`
      without preflight schema entries. That case also makes the shared I mutant runner's
      pristine baseline fail, so the whole I list is `broken_runner` on the base. Owner:
      coordinator/I.
    - `tests/integration/backend/recovery`: `test_i3b_mutant_list_is_well_formed` (anchor
      `i3bm57` is stale after W3's `loop.py`) and
      `test_i3b_rc10_a_rollout_rollback_is_pending_on_the_deploy_scripts` (I2B's `rollback.sh`
      exists) fail on the branch without the cutover. Owner: I3B/E3B.
    - `tests/contracts/v2/test_v1_projection_pg.py` (3 cases) hit `HarnessBusy`: codex-e3b2
      held the D harness (port 55432).

## Handback

**Next unblocked:**

- **G3** (jobs/status/result/events/DELETE; stacked on G2). It can reuse `Relay.cancel`
  (the committed outcome, `not_found` for a foreign handle), `Relay.pump` (replay from a
  cursor; parse `Last-Event-ID` with `Cursor.parse`) and `relay._refusal` for rendering.
- **E3B phase 2** journey bodies. See request E3B2 below.

### integration_requests

**1. Cutover (coordinator), applied, run and reverted in this worktree.** `git status` was
clean afterwards: `git status --short` printed nothing at `665857f`, and the worktree is clean at `e5e7d3a`. The diff of the modified
files follows; the retirements are:

```
git rm apps/infrx-api/gateway.py apps/infrx-api/tests/conftest.py apps/infrx-api/tests/test_app_factory.py \
       apps/infrx-api/tests/test_gateway_auth.py apps/infrx-api/tests/test_inflight.py apps/infrx-api/tests/test_media.py
```

(R48 / S2M D10: the legacy shim and its tests retire together. `tests/conftest.py` only served
`test_gateway_auth.py`.)

```diff
diff --git a/apps/infrx-api/deploy/Dockerfile b/apps/infrx-api/deploy/Dockerfile
index 0302dad..e33c6aa 100644
--- a/apps/infrx-api/deploy/Dockerfile
+++ b/apps/infrx-api/deploy/Dockerfile
@@ -29,10 +29,9 @@ COPY pyproject.toml uv.lock ./
 # and judge stay out until their services are deployed. uv itself does not ship.
 RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
     uv sync --frozen --no-dev --extra state --extra scheduling
-COPY gateway.py ./
 COPY openrouter ./openrouter
 COPY infrx ./infrx
 COPY deploy/preflight.py deploy/migrate.py ./deploy/
-RUN python -m compileall -q infrx gateway.py deploy
+RUN python -m compileall -q infrx deploy
 
 USER infrx-gateway:infrx
diff --git a/apps/infrx-api/deploy/Dockerfile.dockerignore b/apps/infrx-api/deploy/Dockerfile.dockerignore
index 704a740..b12df91 100644
--- a/apps/infrx-api/deploy/Dockerfile.dockerignore
+++ b/apps/infrx-api/deploy/Dockerfile.dockerignore
@@ -2,7 +2,6 @@
 *
 !pyproject.toml
 !uv.lock
-!gateway.py
 !openrouter/
 !infrx/
 !deploy/preflight.py
diff --git a/apps/infrx-api/deploy/marlin2b-gateway.service b/apps/infrx-api/deploy/marlin2b-gateway.service
index 02eaca0..1fb5312 100644
--- a/apps/infrx-api/deploy/marlin2b-gateway.service
+++ b/apps/infrx-api/deploy/marlin2b-gateway.service
@@ -27,7 +27,8 @@ ExecStart=/usr/bin/docker run --rm --init --name infrx-gateway --network host \
   --env-file /etc/marlin2b-gateway.env \
   -v /var/lib/infrx/usage:/var/lib/infrx/usage \
   -v ${PROCESSING_CACHE_DIR}:${PROCESSING_CACHE_DIR} \
-  ${INFRX_IMAGE} uvicorn gateway:app --host 127.0.0.1 --port 8001 --workers 1 \
+  ${INFRX_IMAGE} uvicorn --factory infrx.gateway.app:create_app --host 127.0.0.1 --port 8001 \
+  --workers 1 \
   --timeout-graceful-shutdown 110
 # Drain: uvicorn stops accepting and finishes in-flight requests for up to 110 s, docker
 # waits 120 s before SIGKILL, and systemd waits longer than docker, because a systemd
diff --git a/apps/infrx-api/infrx/config.py b/apps/infrx-api/infrx/config.py
index 77acc3d..317c394 100644
--- a/apps/infrx-api/infrx/config.py
+++ b/apps/infrx-api/infrx/config.py
@@ -12,7 +12,6 @@ environment. Nothing in the F1 path reads them, so adding them changes no
 existing behaviour.
 """
 import dataclasses
-import logging
 import math
 import os
 from dataclasses import dataclass, field
@@ -224,10 +223,8 @@ def runtime_mode(settings) -> str:
 def validate_runtime(settings):
     """r1 R44: the one hook `create_app` calls. Returns the mode it validated.
 
-    * unset (`INFRX_MODE` absent) - **legacy F1 behaviour, exactly as before**, logged
-      once as `legacy`. F1 preserved behaviour by rule, so the legacy `gateway:app`
-      entry point must keep working untouched; G1 replaces this branch with a refusal at
-      cutover, in the same change in which I2's installer writes `INFRX_MODE=pilot`.
+    * unset (`INFRX_MODE` absent) - refuses to start (R44, F2.2 carryover 14): the G2
+      cutover retired the legacy F1 entry point, and I2's installer always writes a mode.
     * `dev` / `test` - explicit, and no further requirement.
     * `pilot` - requires authentication **and** metering configuration, and refuses the
       shared `GATEWAY_API_KEY` (R51), or a typed `RuntimeMisconfigured` naming the setting
@@ -264,9 +261,7 @@ def validate_runtime(settings):
         raise RuntimeMisconfigured(
             mode, detail="ACTIVE_RATE_CARD_VERSION must not carry surrounding whitespace")
     if mode == MODE_UNSET:
-        logging.getLogger("infrx").info(
-            "INFRX_MODE is unset: serving legacy F1 behaviour (mode=legacy)")
-        return "legacy"
+        raise RuntimeMisconfigured(mode, ("INFRX_MODE",))
     if mode not in MODES:
         raise RuntimeMisconfigured(mode, detail="must be one of " + ", ".join(MODES))
     if mode == "pilot":
diff --git a/apps/infrx-api/infrx/gateway/app.py b/apps/infrx-api/infrx/gateway/app.py
index 0f8282d..c179a4d 100644
--- a/apps/infrx-api/infrx/gateway/app.py
+++ b/apps/infrx-api/infrx/gateway/app.py
@@ -13,13 +13,16 @@ from ..auth.keys import Auth
 from ..config import from_env, validate_runtime
 from ..media.video import Media
 from ..usage import Usage
-from .routes import chat, health, models
+from . import pilot
+from .routes import health, ingress, models
 
 # The composition root's router list, fixed and documented (r1 R44). A track's router is
 # a module exposing `register(app, rt)`; the coordinator adds it here on an integration
 # request, which is why the list is a literal rather than a discovery walk - an
 # import-time scan would let a half-finished track mount itself on the public gateway.
-ROUTERS = (health, models, chat)
+# G2 cutover: the metered ingress in place of the legacy chat route. `health` stays: Caddy
+# proxies the public `/health` to it (deploy/Caddyfile), and the edge hides what it echoes.
+ROUTERS = (health, models, ingress)
 
 
 def upstream_client(settings):
@@ -51,20 +54,25 @@ class Runtime:
         self.app = None
 
 
-def create_app(settings=None, client=None, sb=None, clock=time.time):
+def create_app(settings=None, client=None, sb=None, clock=time.time, **adapters):
     """The FastAPI app. `settings` defaults to the process environment; the
-    upstream and Supabase clients and the clock are injectable for tests.
+    upstream and Supabase clients and the clock are injectable for tests, and so are the
+    pilot's adapters (`pilot.build_ingress_deps`: catalog, stream, objects, jobs, index).
 
     r1 R44: one coordinator-owned hook, `config.validate_runtime`, decides whether this
-    configuration may serve at all. With `INFRX_MODE` unset it logs `legacy` and changes
-    nothing, so the F1 entry point keeps its behaviour; `pilot` without authentication or
-    metering, and any unrecognised mode, refuse to start.
+    configuration may serve at all: an unset `INFRX_MODE`, `pilot` without authentication or
+    metering, and any unrecognised mode refuse to start. The G2 cutover: `rt.ingress` is the
+    pilot composition, the route table serves `/v1/chat/completions` from the ingress alone,
+    and nothing FastAPI would publish by itself (docs, schema, slash redirects) is served.
     """
     rt = Runtime(from_env() if settings is None else settings, client, sb, clock)
     rt.mode = validate_runtime(rt.settings)
-    app = FastAPI()
+    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=pilot.lifespan)
+    app.router.redirect_slashes = False
     app.state.runtime = rt
     rt.app = app
+    rt.ingress = pilot.build_ingress_deps(rt, **adapters)
     for module in ROUTERS:
         module.register(app, rt)
+    ingress.assert_route_table(app)
     return app
diff --git a/apps/infrx-api/tests/contracts/test_config_and_imports.py b/apps/infrx-api/tests/contracts/test_config_and_imports.py
index 8f31ed6..901dce3 100644
--- a/apps/infrx-api/tests/contracts/test_config_and_imports.py
+++ b/apps/infrx-api/tests/contracts/test_config_and_imports.py
@@ -171,12 +171,32 @@ def test_bounds_a_zero_would_disable_are_refused(name):
 
 
 # --- r1 R44: the runtime mode, through the composition root ---------------------
-# These build apps with `create_app()` and injected settings. They never import the
-# legacy `gateway` shim: it mutates process state at import and directories collect
-# before the top-level legacy files, so importing it here would reorder the suite (R48).
+# These build apps with `create_app()` and injected settings. Since the G2 cutover the
+# app is the pilot composition, so its adapters are injected too (`_adapters`).
 def _app(env, **clients):
     from infrx.gateway.app import create_app
-    return create_app(config.from_env(env), client=object(), sb=object(), **clients)
+    return create_app(config.from_env(env), client=object(), sb=object(), **_adapters(),
+                      **clients)
+
+
+def _adapters():
+    """The pilot composition's adapters as the contract fakes (G2 cutover): D5's catalog,
+    D4's journal and a durable object store have no real adapter in this tree, and a test
+    opens no PostgreSQL pool and no Valkey client."""
+    import asyncio
+
+    from infrx.contracts.conformance.v2_fakes import fake_v2_harness
+    from infrx.contracts.fakes.factories import credit_jobstore_factory
+    from infrx.media.store import InMemoryObjectStore
+    from infrx.scheduling.memory import MemoryScheduler
+    harness = credit_jobstore_factory()
+    catalog = fake_v2_harness().catalog
+    catalog.move_alias(config.from_env({}).model_id,
+                       catalog.aliases["nemostation/marlin-2b@2026-09-01"])
+    stream = harness.extra["stream"]
+    stream.usage = lambda: asyncio.sleep(0, {})       # D4's journal readiness answer
+    return {"catalog": catalog, "stream": stream, "objects": InMemoryObjectStore(),
+            "jobs": harness.port, "index": MemoryScheduler(harness.clock.now)}
 
 
 def _validated_as_cutover(env):
@@ -195,19 +215,16 @@ AUTHENTICATED = {"SUPABASE_URL": "https://example.supabase.co",
 METERED = {"DATABASE_URL": "postgresql:///x"}
 
 
-def test_an_unset_mode_is_the_legacy_f1_behaviour():
-    """R44: while `INFRX_MODE` is unset the app starts exactly as F1's did, and says so.
-
-    F1 preserved behaviour by rule, so the legacy entry point cannot start refusing;
-    G1 replaces this branch with "unset -> refuse" at cutover.
-    """
+def test_an_unset_mode_refuses_to_start():
+    """R44 / F2.2 carryover 14, inverted at the G2 cutover: the legacy F1 entry point is
+    retired, so an unset `INFRX_MODE` is a refusal naming the setting, before anything
+    mounts - the installer always writes a mode (I0)."""
     assert config.runtime_mode(config.from_env({})) == ""
-    assert config.validate_runtime(config.from_env({})) == "legacy"
-    app = _app({})
-    assert app.state.runtime.mode == "legacy"
-    # and the F1 routes are mounted, i.e. "legacy" is the whole app, not a stub
-    paths = {route.path for route in app.routes}
-    assert {"/health", "/v1/models", "/v1/chat/completions"} <= paths
+    with pytest.raises(config.RuntimeMisconfigured) as caught:
+        config.validate_runtime(config.from_env({}))
+    assert caught.value.missing == ("INFRX_MODE",)
+    with pytest.raises(config.RuntimeMisconfigured, match="INFRX_MODE"):
+        _app({})
 
 
 def test_pilot_refuses_to_start_unauthenticated_or_unmetered():
@@ -239,9 +256,12 @@ def test_a_pilot_startup_error_never_echoes_a_value():
 def test_pilot_starts_with_authentication_and_metering():
     env = {"INFRX_MODE": "pilot", **METERED, **AUTHENTICATED}
     assert _validated_as_cutover(env) == "pilot"
-    # ...and never on today's composition, which serves chat through the legacy route.
-    with pytest.raises(config.RuntimeMisconfigured, match="legacy route"):
-        _app(env)
+    # ...and on the composition root itself, which serves chat through the metered ingress.
+    app = _app(env)
+    assert app.state.runtime.mode == "pilot"
+    served = [route.endpoint.__module__ for route in app.routes
+              if getattr(route, "path", "") == "/v1/chat/completions"]
+    assert served == ["infrx.gateway.routes.ingress"]
 
 
 def test_pilot_refuses_the_shared_legacy_key(caplog):
@@ -316,7 +336,7 @@ def test_the_router_list_is_fixed_and_uses_the_register_protocol():
     half-finished track mount itself on the public gateway."""
     from infrx.gateway import app as composition_root
     assert [module.__name__.rsplit(".", 1)[-1] for module in composition_root.ROUTERS] == \
-        ["health", "models", "chat"]
+        ["health", "models", "ingress"]
     for module in composition_root.ROUTERS:
         assert callable(getattr(module, "register"))
 
@@ -600,8 +620,11 @@ def test_a_credit_deployment_needs_an_approved_rate_card():
         corrected = {**env, "ACTIVE_RATE_CARD_VERSION": "rc_marlin2b_2026_09_provisional"}
         if mode == "pilot":
             assert _validated_as_cutover(corrected) == "pilot"
-        else:
+        elif mode:
             assert _app(corrected)
+        else:                   # the card passes; an unset mode is refused since the cutover
+            with pytest.raises(config.RuntimeMisconfigured, match="requires INFRX_MODE"):
+                _app(corrected)
         # F2P review M-6/CFG-2: whitespace is not a card (refused as missing), and a padded
         # name is refused at startup rather than served (`validate_pilot` is off this path).
         with pytest.raises(config.RuntimeMisconfigured, match="requires ACTIVE_RATE_CARD_VERSION"):
@@ -611,11 +634,12 @@ def test_a_credit_deployment_needs_an_approved_rate_card():
                        " rc_marlin2b_2026_09_provisional"):
             with pytest.raises(config.RuntimeMisconfigured, match="ACTIVE_RATE_CARD_VERSION must not"):
                 _app({**env, "ACTIVE_RATE_CARD_VERSION": padded})
-    assert _app({"ACCOUNTING_REGIME": "legacy_usd"}) is not None
+    assert _app({"INFRX_MODE": "dev", "ACCOUNTING_REGIME": "legacy_usd"}) is not None
     # F2P confirmation CONF-N2: whitespace-only is unset in every regime, so a legacy
     # deployment starts; a padded card is refused in every regime (the rule is the text's).
     try:
-        started = _app({"ACCOUNTING_REGIME": "legacy_usd", "ACTIVE_RATE_CARD_VERSION": "  "})
+        started = _app({"INFRX_MODE": "dev", "ACCOUNTING_REGIME": "legacy_usd",
+                        "ACTIVE_RATE_CARD_VERSION": "  "})
     except config.RuntimeMisconfigured as refused:
         raise AssertionError(f"a whitespace-only card was read as a card: {refused}") from None
     assert started is not None
@@ -667,8 +691,11 @@ def test_a_bad_deployment_value_refuses_before_anything_mounts(mode, setting):
     # about the value and not about the mode (pilot: as the cutover composes it).
     if mode == "pilot":
         assert _validated_as_cutover({**env, setting: "64"}) == "pilot"
-    else:
+    elif mode:
         assert _app({**env, setting: "64"}) is not None
+    else:                       # the value passes; an unset mode is refused since the cutover
+        with pytest.raises(config.RuntimeMisconfigured, match="requires INFRX_MODE"):
+            _app({**env, setting: "64"})
 
 
 def test_a_zero_index_cap_refuses_to_start():
diff --git a/apps/infrx-api/tests/g/mutants.py b/apps/infrx-api/tests/g/mutants.py
index 8c3a243..f6af9e2 100644
--- a/apps/infrx-api/tests/g/mutants.py
+++ b/apps/infrx-api/tests/g/mutants.py
@@ -866,21 +866,21 @@ MUTANTS: tuple[Mutant, ...] = (
        V, '    if match.group("mime").lower() not in allowed_mime:', "    if True:",
        "test_api_auth__the_headless_quickstart_is_served_over_both_media_forms",
        "test_dur_cap__the_headless_client_retries_denied_capacity_with_its_own_key"),
-    # These two edit files G does not own, in the temporary copy only: they are the
-    # cutover itself, and they say exactly which cases pin today's behaviour.
-    _m("composition_root_mounts_the_ingress", "G1 mounts nothing until the cutover",
-       "gateway/app.py", "ROUTERS = (health, models, chat)",
-       "from .routes import ingress as _ingress\nROUTERS = (health, models, chat, _ingress)",
-       "test_f_base__the_composition_root_still_mounts_only_the_legacy_routers"),
-    # R44/item 14: while `INFRX_MODE` is unset the legacy entry keeps F1 behaviour, so the
-    # honest kill is the refusal this mutant introduces. G2/I0 retire this mutant at cutover.
-    _m("unset_mode_refuses", "an unset INFRX_MODE is still legacy behaviour",
-       "config.py", '        return "legacy"',
-       '        raise RuntimeMisconfigured(mode, detail="INFRX_MODE must be set")',
-       "test_f_base__an_unset_mode_is_still_legacy_behaviour",
-       "test_f_base__the_composition_root_still_mounts_only_the_legacy_routers",
-       "test_f_base__registering_the_ingress_never_replaces_the_legacy_chat_route",
-       dies_by=("RuntimeMisconfigured",)),
+    # The cutover itself (G2 item 5), in files G does not own, in the temporary copy only:
+    # the retired `unset_mode_refuses` / `composition_root_mounts_the_ingress` inverted.
+    _m("composition_root_mounts_the_legacy_route", "chat is served by the ingress only",
+       "gateway/app.py", "ROUTERS = (health, models, ingress)",
+       "from .routes import chat as _chat\nROUTERS = (health, models, _chat, ingress)",
+       "test_f_base__the_composition_root_serves_chat_through_the_metered_ingress_only"),
+    _m("composition_root_publishes_docs", "the pilot app publishes no docs or schema",
+       "gateway/app.py",
+       "    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=pilot.lifespan)",
+       "    app = FastAPI(lifespan=pilot.lifespan)",
+       "test_f_base__the_composition_root_serves_chat_through_the_metered_ingress_only"),
+    _m("unset_mode_starts_legacy", "an unset INFRX_MODE refuses to start (R44, item 14)",
+       "config.py", '        raise RuntimeMisconfigured(mode, ("INFRX_MODE",))',
+       '        return "legacy"',
+       "test_f_base__an_unset_mode_refuses_to_start"),
     # === G2 item 1: durable acceptance (DUR-ADMIT G half, API-MODES) =====================
     _m("admits_the_validation_record", "admission takes prepare_request's record (S2M D1)",
        R, "            else self.jobs.admit(prepared, idem)",
diff --git a/apps/infrx-api/tests/g/support.py b/apps/infrx-api/tests/g/support.py
index bcc27f3..4e00bc3 100644
--- a/apps/infrx-api/tests/g/support.py
+++ b/apps/infrx-api/tests/g/support.py
@@ -2,13 +2,9 @@
 
 Two app shapes, both built without importing the legacy `gateway` shim (r1 R48):
 
-* `cutover_app()` - the app the cutover produces: the ingress router mounted on a
-  bare FastAPI with a real `Runtime`, i.e. `create_app` minus the legacy routers.
-  Registering the ingress on top of the legacy chat route would leave that route in
-  charge of `/v1/chat/completions` (Starlette matches in registration order), which
-  would test the wrong handler.
-* `legacy_app()` - `create_app()` itself, for the cases about what the composition
-  root does today.
+* `cutover_app()` - the ingress router mounted on a bare FastAPI with a real
+  `Runtime`, with the `IngressDeps` a case chooses (`create_app` itself composes the
+  pilot's own, `test_startup.pilot_app`).
 """
 from __future__ import annotations
 
@@ -27,7 +23,7 @@ from infrx.contracts.conformance.v2_fakes import fake_v2_harness
 from infrx.contracts.v2 import fixtures as v2fix
 from infrx.contracts.limits import DEFAULTS
 from infrx.gateway import app as composition
-from infrx.gateway.app import Runtime, create_app
+from infrx.gateway.app import Runtime
 from infrx.gateway.routes import health, ingress, models
 
 CHAT_PATH = ingress.CHAT_PATH
@@ -157,11 +153,6 @@ def cutover_app(config=None, *, sb=None, clock=None, seen=None, ingress_deps=Non
     return app, mounted
 
 
-def legacy_app(config=None):
-    return create_app(config if config is not None else Settings(usage_log=USAGE_LOG),
-                      client=upstream(), sb=supabase(), clock=lambda: 1_790_000_000.0)
-
-
 def recorder():
     """An `accept` that records what the ingress handed it and answers 202."""
     from fastapi.responses import JSONResponse
diff --git a/apps/infrx-api/tests/g/test_mutants.py b/apps/infrx-api/tests/g/test_mutants.py
index fad15d3..3eddc06 100644
--- a/apps/infrx-api/tests/g/test_mutants.py
+++ b/apps/infrx-api/tests/g/test_mutants.py
@@ -21,7 +21,7 @@ FULL_RUN = os.environ.get("INFRX_MUTANTS", "").lower() in ("all", "1", "true")
 # way - the subset only changes how long `make api-test` takes.
 SUBSET = ("body_cap_removed", "anonymous_request_accepted", "unhandled_exception_text_leaks",
           "unsupported_parameters_ignored", "pilot_starts_unreachable", "key_cache_unbounded",
-          "input_ceiling_ignores_the_output", "unset_mode_refuses",
+          "input_ceiling_ignores_the_output", "unset_mode_starts_legacy",
           # One per blocking finding of review round 1, so the default suite would
           # have caught each of them.
           "recursion_error_escapes", "envelope_render_unprotected", "messages_unbounded",
diff --git a/apps/infrx-api/tests/g/test_startup.py b/apps/infrx-api/tests/g/test_startup.py
index 2e769e3..1e10ca7 100644
--- a/apps/infrx-api/tests/g/test_startup.py
+++ b/apps/infrx-api/tests/g/test_startup.py
@@ -1,12 +1,12 @@
 #!/usr/bin/env python3
 """`M-FAILCLOSED`: what `pilot` refuses to start without, what health says, and what
-the composition root still does today.
+the composition root does since the G2 cutover.
 
 `O-FAILOPEN` is the hazard these cases exist for: an install run that loses a
 parameter read must not be able to publish an ingress that authenticates nobody and
 meters nothing. Two independent refusals cover it - `config.validate_runtime` at the
 composition root, and this router refusing to register - and the last group pins
-that the legacy entry point is still exactly what F1 left behind.
+that the composition root serves chat through the metered ingress alone.
 """
 import asyncio
 
@@ -17,6 +17,9 @@ from infrx.config import RuntimeMisconfigured, Settings, validate_runtime
 from infrx.contracts import errors, wire
 from infrx.gateway import app as composition
 from infrx.gateway.routes import chat, health, ingress, models
+from infrx.scheduling.memory import MemoryScheduler
+
+from . import relay_support
 
 from . import support
 
@@ -113,14 +116,32 @@ def test_f_base__a_readiness_probe_that_raises_is_unavailable_not_a_500():
     assert "postgresql" not in response.text
 
 
-# --- the composition root, unchanged until the cutover ----------------------------
-def test_f_base__the_composition_root_still_mounts_only_the_legacy_routers():
-    """G1 adds modules; it mounts none. The cutover integration request replaces
-    `chat` with `ingress` here and nowhere else (r1 R44)."""
-    assert composition.ROUTERS == (health, models, chat)
-    paths = {route.path for route in support.legacy_app().routes if hasattr(route, "path")}
-    assert {"/v1/chat/completions", "/health", "/v1/models"} <= paths
-    assert ingress.HEALTH_PATH not in paths and ingress.READY_PATH not in paths
+# --- the composition root, since the G2 cutover ------------------------------------
+def pilot_app(config=None):
+    """`create_app` as the cutover composes it, over the contract fakes for the adapters
+    that have no real implementation yet (D5's catalog, D4's journal, M's object store)."""
+    world = relay_support.World()
+    world.stream.usage = lambda: asyncio.sleep(0, {})
+    return composition.create_app(
+        config if config is not None else support.settings(), client=support.upstream(),
+        sb=support.supabase(), clock=world.now_s, catalog=world.catalog, stream=world.stream,
+        objects=world.objects, jobs=world.jobs,
+        index=MemoryScheduler(world.clock.now))
+
+
+def test_f_base__the_composition_root_serves_chat_through_the_metered_ingress_only():
+    """The cutover (r1 R44): `ROUTERS` is (health, models, ingress) - `health` stays, Caddy
+    proxies the public `/health` to it - the one chat handler is the ingress's, and nothing
+    FastAPI would publish by itself (docs, schema, slash redirects) is served."""
+    assert composition.ROUTERS == (health, models, ingress)
+    app = pilot_app()
+    paths = {route.path for route in app.routes if hasattr(route, "path")}
+    assert {"/v1/chat/completions", "/health", "/v1/models", ingress.HEALTH_PATH,
+            ingress.READY_PATH} <= paths
+    assert not paths & {"/docs", "/redoc", "/openapi.json"}
+    assert app.router.redirect_slashes is False
+    (route,) = [r for r in app.routes if getattr(r, "path", "") == support.CHAT_PATH]
+    assert route.endpoint.__module__ == ingress.__name__
 
 
 def test_api_auth__a_pilot_never_serves_chat_through_the_legacy_route():
@@ -132,42 +153,27 @@ def test_api_auth__a_pilot_never_serves_chat_through_the_legacy_route():
     from unittest import mock
 
     config = support.settings()                     # a complete pilot configuration
-    with pytest.raises(RuntimeMisconfigured) as raised:
-        composition.create_app(config, client=support.upstream(), sb=support.supabase())
-    message = str(raised.value)
-    assert "legacy route" in message
-    assert "service-role" not in message and "infrx_g1" not in message
-    with support.as_cutover():
-        assert validate_runtime(config) == "pilot"
-    for routers in ((health, models, chat, ingress), (health, models)):
+    assert validate_runtime(config) == "pilot"      # the composition root is the cutover's
+    assert pilot_app(config).state.runtime.mode == "pilot"
+    for routers in ((health, models, chat), (health, models, chat, ingress), (health, models)):
         with mock.patch.object(composition, "ROUTERS", routers):
-            with pytest.raises(RuntimeMisconfigured):
+            with pytest.raises(RuntimeMisconfigured) as raised:
                 validate_runtime(config)
+            message = str(raised.value)
+            assert "legacy route" in message
+            assert "service-role" not in message and "infrx_g1" not in message
     assert validate_runtime(support.settings("dev")) == "dev"
-    assert validate_runtime(Settings()) == "legacy"
-
-
-def test_f_base__an_unset_mode_is_still_legacy_behaviour():
-    """R44's unset branch: `create_app()` with no `INFRX_MODE` logs `legacy` and
-    changes nothing, and the legacy chat route still answers without a key. G1 flips
-    this to a refusal at cutover, with I2's installer."""
-    assert validate_runtime(Settings()) == "legacy"
-    app = support.legacy_app()
-    assert app.state.runtime.mode == "legacy"
-    response = TestClient(app).post("/v1/chat/completions", json=support.BODY)
-    assert response.status_code == 200, response.text
 
 
-def test_f_base__registering_the_ingress_never_replaces_the_legacy_chat_route():
-    """Mounted next to the legacy route, the legacy route keeps its path: the cutover
-    is a change to `ROUTERS`, not something a track can do by also registering."""
-    app = support.legacy_app()
-    rt = app.state.runtime
-    rt.mode = "dev"
-    ingress.register(app, rt, support.deps(checks=BOTH_OK))
-    tc = TestClient(app)
-    assert tc.post("/v1/chat/completions", json=support.BODY).status_code == 200   # legacy
-    assert tc.get(support.HEALTH_PATH).json() == {"status": "ok"}                  # new routes live
+def test_f_base__an_unset_mode_refuses_to_start():
+    """R44's unset branch, inverted at the cutover (F2.2 carryover 14): the legacy F1
+    entry point is retired, so no `INFRX_MODE` is a startup refusal naming the setting, and
+    the installer never writes one (I0)."""
+    with pytest.raises(RuntimeMisconfigured) as raised:
+        validate_runtime(Settings())
+    assert raised.value.missing == ("INFRX_MODE",)
+    with pytest.raises(RuntimeMisconfigured, match="INFRX_MODE"):
+        pilot_app(Settings(usage_log=support.USAGE_LOG))
 
 
 # --- what FastAPI would answer by itself (review r1 item 7) ------------------------
@@ -233,8 +239,8 @@ def test_f_base__register_reads_its_deps_from_the_runtime():
 def test_f_base__the_ingress_refuses_to_start_without_a_catalog():
     """G1R: a model name means only what the trusted catalog says. With no catalog the
     ingress could only guess (the old served-map fallback copied the name through), so
-    it refuses to register, in every mode."""
-    for mode in ("pilot", "dev", "test", ""):             # "" = unset, legacy
+    it refuses to register, in every mode (an unset one refuses before, since the cutover)."""
+    for mode in ("pilot", "dev", "test"):
         with pytest.raises(RuntimeMisconfigured) as raised:
             support.cutover_app(support.settings(mode), ingress_deps=support.deps(catalog=None))
         assert "catalog" in str(raised.value)
diff --git a/apps/infrx-api/tests/i/mutants.py b/apps/infrx-api/tests/i/mutants.py
index d80aa08..a2fa7eb 100644
--- a/apps/infrx-api/tests/i/mutants.py
+++ b/apps/infrx-api/tests/i/mutants.py
@@ -143,12 +143,11 @@ MUTANTS: tuple[Mutant, ...] = (
     _m("composition_gate_removed", "pilot is refused while the pilot routers are absent",
        P, '    if mode == "pilot" and ingress not in composition.ROUTERS:',
        "    if False:",
-       "test_deploy_failclosed__pilot_is_refused_while_the_runtime_is_not_composed"),
+       "test_deploy_failclosed__pilot_passes_the_composition_gate_once_the_ingress_is_composed"),
     _m("probe_verdict_ignored", "a runtime refusal stops the install",
        P, '        if not verdict["ok"]:\n            report(verdict["problems"])\n'
           "            return REFUSED",
        '        if not verdict["ok"]:\n            report(verdict["problems"])',
-       "test_deploy_failclosed__pilot_is_refused_while_the_runtime_is_not_composed",
        "test_deploy_failclosed__the_probe_that_says_nothing_is_a_refusal"),
     _m("unparsable_verdict_passes", "a probe that answers nothing is not a pass",
        P, '        return {"ok": False, "python": None, "mode": cfg.mode, "warnings": [],',
@@ -271,13 +270,10 @@ MUTANTS: tuple[Mutant, ...] = (
        "infrx/contracts/limits.py", 'MODES = ("dev", "test", "pilot")',
        'MODES = ("dev", "test", "pilot", "prod")',
        "test_deploy_failclosed__the_manifest_is_the_only_source_of_env_keys"),
-    _m("unset_mode_refuses", "an unset INFRX_MODE is still legacy behaviour (F2.2 item 14)",
-       "infrx/config.py", '        return "legacy"',
-       '        raise RuntimeMisconfigured(mode, detail="INFRX_MODE must be set")',
-       "test_deploy_failclosed__an_unset_mode_is_unreachable_from_the_installer",
-       # the defect IS the raise: `validate_runtime(Settings())` refusing instead of
-       # answering "legacy" (the shared rule makes that kill mode explicit)
-       dies_by=("RuntimeMisconfigured",)),
+    _m("unset_mode_starts_legacy", "an unset INFRX_MODE refuses to start (F2.2 item 14)",
+       "infrx/config.py", '        raise RuntimeMisconfigured(mode, ("INFRX_MODE",))',
+       '        return "legacy"',
+       "test_deploy_failclosed__an_unset_mode_is_unreachable_from_the_installer"),
 )
 
 
diff --git a/apps/infrx-api/tests/i/test_install.py b/apps/infrx-api/tests/i/test_install.py
index b9a5eca..1d8ef92 100644
--- a/apps/infrx-api/tests/i/test_install.py
+++ b/apps/infrx-api/tests/i/test_install.py
@@ -189,22 +189,33 @@ def test_deploy_failclosed__an_unset_or_unknown_mode_installs_nothing(
     assert made.argv == "", "a parameter was read for an unusable mode"
 
 
-def test_deploy_failclosed__pilot_is_refused_while_the_runtime_is_not_composed(
+def test_deploy_failclosed__pilot_passes_the_composition_gate_once_the_ingress_is_composed(
         tmp_path, monkeypatch, capsys):
-    """Item 3 of the brief: pilot mode is only written when the pilot routers are
-    composed. `infrx/gateway/app.py` still mounts `(health, models, chat)`, so a
-    correct pilot configuration with every parameter present is *still* refused - and
-    that is the current, intended state until the G2 cutover. The engine image is
-    pinned here so the *other* pilot prerequisite is not what refuses the install."""
+    """Item 3 of the brief, inverted at the G2 cutover (I0 request 3): `ROUTERS` composes
+    the metered ingress, so the installer's composition gate no longer refuses a correct
+    pilot configuration. What still refuses it here is named and is not the composition
+    (this host's interpreter, W3's worker entry); the engine image is pinned so the other
+    pilot prerequisite is not what refuses the install either."""
     made = support.stubs(tmp_path, monkeypatch)
     cfg = support.config(tmp_path, mode="pilot",
                          serve_script=support.serve_script(tmp_path, image=support.PINNED))
     before = cfg.env_file.read_bytes()
-    assert preflight.apply(cfg) == preflight.REFUSED
-    unchanged(cfg, before, made.systemctl_calls)
+    verdict = preflight.apply(cfg)
     message = capsys.readouterr().err
-    assert "pilot routers to be composed" in message  # preflight's own phrase (G1R review C2)
+    assert "pilot routers to be composed" not in message  # preflight's own phrase (C2)
+    if verdict == preflight.REFUSED:
+        unchanged(cfg, before, made.systemctl_calls)
     assert MARKER not in message
+    # The gate itself still stands: a runtime that lost the ingress is refused by name.
+    from unittest import mock
+
+    from infrx.gateway import app as composition
+    from infrx.gateway.routes import chat, health, models
+    staged = tmp_path / "pilot.env"
+    staged.write_text(cfg.env_file.read_text() if cfg.env_file.exists() else "INFRX_MODE=pilot\n")
+    with mock.patch.object(composition, "ROUTERS", (health, models, chat)):
+        verdict = preflight.probe(staged, "pilot")
+    assert any("pilot routers to be composed" in problem for problem in verdict["problems"])
 
 
 def test_deploy_failclosed__the_staged_bytes_are_what_the_runtime_validates(tmp_path,
diff --git a/apps/infrx-api/tests/i/test_prereqs.py b/apps/infrx-api/tests/i/test_prereqs.py
index a0ecbdb..f5d5671 100644
--- a/apps/infrx-api/tests/i/test_prereqs.py
+++ b/apps/infrx-api/tests/i/test_prereqs.py
@@ -50,13 +50,14 @@ def test_deploy_failclosed__the_manifest_is_the_only_source_of_env_keys():
 
 
 def test_deploy_failclosed__an_unset_mode_is_unreachable_from_the_installer():
-    """F2.2 item 14 stays open on purpose. `validate_runtime` still maps an unset
-    `INFRX_MODE` to legacy behaviour - G2 inverts that, and G's `unset_mode_refuses`
-    mutant keeps guarding it until then - but no install run can produce an unset mode,
-    because `INFRX_MODE` is a required manifest key and `""` is not a valid mode."""
-    from infrx.config import Settings, validate_runtime
-
-    assert validate_runtime(Settings()) == "legacy"
+    """F2.2 item 14, closed at the G2 cutover: `validate_runtime` refuses an unset
+    `INFRX_MODE` (G's `unset_mode_starts_legacy` mutant guards it), and no install run can
+    produce one either, because `INFRX_MODE` is a required manifest key and `""` is not a
+    valid mode."""
+    from infrx.config import RuntimeMisconfigured, Settings, validate_runtime
+
+    with pytest.raises(RuntimeMisconfigured, match="INFRX_MODE"):
+        validate_runtime(Settings())
     mode_key = next(key for key in preflight.MANIFEST if key.env == "INFRX_MODE")
     assert mode_key.required_in == preflight.MODES
     assert preflight.shape_problem(mode_key, "") is not None
```

Results with the cutover applied, at `e5e7d3a` plus the diff:

Verified on a copy of `e5e7d3a` with the diff applied (`git archive HEAD` + `patch -p1`, the
worktree's venv). `git apply --check` of the same file passes on the worktree at `e5e7d3a`.
The diff was first applied, run and reverted in the worktree itself at `665857f`, with the same
results; its SHA-256 is `465b425fcc90482ef5e0e739fe1f5625ebd9dc20631642765ff5cbe88acfc67f`.

G suite:
```
2026-09-23T06:53:55Z
384 passed, 2 warnings in 4.30s
EXIT=0
2 passed, 21 deselected in 0.12s
```
The last line is `tests/g/test_mutants.py -k "well_formed or every_case"`.

The cutover's G mutants: the three new or inverted ones, the three dr17 ones, and the readiness
and route-table ones:
```
composition_root_mounts_the_legacy_route: 1/1 killed
composition_root_publishes_docs: 1/1 killed
unset_mode_starts_legacy: 1/1 killed
pilot_serves_the_legacy_route: 1/1 killed
legacy_route_beside_the_ingress: 1/1 killed
no_metered_ingress_accepted: 1/1 killed
readiness_is_public: 1/1 killed
second_chat_route_tolerated: 1/1 killed
chat_route_names_intake: 1/1 killed
```

`tests/i`. The one failure is pre-existing (Limit 12):
```
FAILED tests/i/test_packaging.py::test_backend_deploy__the_config_schema_is_every_name_the_runtime_reads
1 failed, 99 passed in 33.37s
EXIT=1
```

The I mutants renamed by the diff. Each was applied alone and its named case run, because the
shared I runner's pristine baseline fails on the pre-existing case above:
```
composition_gate_removed (deploy/preflight.py): exit 1: 1 failed, 99 deselected in 1.20s
    FAILED tests/i/test_install.py::test_deploy_failclosed__pilot_passes_the_composition_gate_once_the_ingress_is_composed
unset_mode_starts_legacy (infrx/config.py): exit 1: 1 failed, 99 deselected in 0.15s
    FAILED tests/i/test_prereqs.py::test_deploy_failclosed__an_unset_mode_is_unreachable_from_the_installer
probe_verdict_ignored (deploy/preflight.py): exit 1: 1 failed, 99 deselected in 0.45s
    FAILED tests/i/test_install.py::test_deploy_failclosed__the_probe_that_says_nothing_is_a_refusal
```

`tests/contracts` (the 3 failures are `HarnessBusy`, and the lock holder was this lane's own
orderings run):
```
FAILED tests/contracts/v2/test_v1_projection_pg.py::test_every_pre_cutover_usage_row_reads_as_a_legacy_usd_dto
FAILED tests/contracts/v2/test_v1_projection_pg.py::test_project_v1_usage_reads_the_raw_rows_to_the_same_dtos
FAILED tests/contracts/v2/test_v1_projection_pg.py::test_the_legacy_statement_is_a_rollout_hold_never_a_credit_figure
3 failed, 1009 passed in 18.75s
EXIT=1
HarnessBusy: another run holds /tmp/infrx-d1-postgres-55432.lock (pid 2149263 checkout /home/rey/workspace/rey/code/model-inference/.claude/worktrees/codex-g2)
```

`tests/integration` at layer 1. The first attempt, `make integration INTEGRATION_ARGS="--layer
1 --canary"` at `665857f` + diff, ended `EXIT=2`: the runner's `make api-test` step hit its
own 1800 s subprocess timeout and `run.py` raised `TimeoutExpired`, which is an E-harness
limit. It was re-run with `--only-suites`, which runs the same `pytest tests/integration` step
without the canonical make targets. Result: `[ok] engine` (8 cases); `[FAIL] suites`
(`tests/integration`: 121 passed, 15 failed, 66 skipped); `[ok] mutants` (67, 65 killed,
2 controls survived); `[ok] canary`; `EXIT=2`. The same pytest step, run directly:
```
cut-over tree:  15 failed, 121 passed, 66 skipped, 2 warnings in 26.58s
branch (no diff): 2 failed, 122 passed, 78 skipped, 2 warnings in 26.60s
```
The two failures on the branch are pre-existing, owned by I3B/E3B:
`test_i3b_mutant_list_is_well_formed` (anchor `i3bm57` is stale after W3's loop change) and
`test_i3b_rc10_a_rollout_rollback_is_pending_on_the_deploy_scripts` (I2B's `rollback.sh`
exists). The 13 failures the cutover adds are the intended ones:
- 10 journey cells (`test_backend_journey[*]` and the dataset resume), `dr11` and `rc03`,
  which all "fail the day `ingress` enters `ROUTERS`";
- `dr17`, whose body calls `create_app(settings)` without the pilot's adapters, so the
  cutover's `build_ingress_deps` refuses it before the route check.
All of them are E3B phase 2's bodies (request E3B2).

Checklist for applying it:

- **Adapters.** Apply it only when D4, D5 and M's adapters exist, and default
  `build_ingress_deps`'s `stream`/`catalog`/`objects` to `PgStreamStore(connect)` and D5's
  catalog on the same pool, plus the S3 adapter. Until then `create_app()` refuses in every
  mode, which is fail-closed.
- **W-new** must land first (request 2).
- **I2B's unit** changes in this diff: `uvicorn --factory infrx.gateway.app:create_app`.
- **Environment file.** `CONSOLE_CURSOR_SECRET` belongs on the console's deployment checklist,
  not the gateway's (F2R-B IR-5). The pilot env also needs `VALKEY_URL`, `ACCOUNTING_REGIME`
  and `ACTIVE_RATE_CARD_VERSION` (credit), `PROCESSING_CACHE_DIR` and `DATABASE_POOL_*`.
- **Docs.** Update `apps/infrx-api/README.md` (lines 14, 22, 24, 76 name `gateway.py`) and
  `CLAUDE.md` l.71; coordinator-owned.
- **Known consequence.** The legacy health handler asks the engine and echoes exception text,
  which the edge hides. It stays because Caddy proxies `/health` to it.
- **E3B.** On apply, E3B phase 1's `dr11`, its six sync/SSE journey cells and I3B's `rc03`
  turn from PENDING to FAIL (`stack.ingress_is_mounted()`), so the cutover must land with E3B
  phase 2's bodies (request E3B2).

**2. W-new (W, blocking), verified then reverted.** Diff:

```diff
diff --git a/apps/infrx-api/infrx/worker/engine.py b/apps/infrx-api/infrx/worker/engine.py
index 9a7000e..d69c293 100644
--- a/apps/infrx-api/infrx/worker/engine.py
+++ b/apps/infrx-api/infrx/worker/engine.py
@@ -92,9 +92,14 @@ REFUSED_PARAMETERS = frozenset({
     "guided_grammar", "structured_outputs",                        # 01: structured output too
     "logprobs", "top_logprobs", "echo", "best_of", "logit_bias",
     "price_snapshot",                                              # r1 R45: prices are never a request field
-    "max_tokens", "max_completion_tokens",                         # the ceiling is the record's, not a parameter
-    "stream", "stream_options", "model",                           # the transport and the engine are ours
+    "stream_options", "model",                                     # the transport and the engine are ours
 })
+# What the ingress's validated record carries in `parameters` although the record itself
+# consumed it (the frozen `normalized_request` fixture): `stream` became `execution_mode`,
+# `max_tokens`/`max_completion_tokens` became `max_output_tokens`. Never forwarded - the
+# transport and the ceiling are ours - and never refused: refusing them settled every
+# validated streaming or capped request `platform_error` (G2 integration request W-new).
+CONSUMED_PARAMETERS = frozenset({"stream", "max_tokens", "max_completion_tokens"})
 # Consumed here and never forwarded: `prepared_request` puts the tenant in it.
 INTERNAL_PARAMETERS = frozenset({"tenant_salt"})
 
@@ -510,7 +515,7 @@ class VllmEngine:
         refused explicitly, and r1 R45 refuses a client-supplied price)."""
         forwarded: dict[str, Any] = {}
         for name, value in (parameters or {}).items():
-            if name in INTERNAL_PARAMETERS:
+            if name in INTERNAL_PARAMETERS or name in CONSUMED_PARAMETERS:
                 continue
             if name == "n":
                 if value != 1:
diff --git a/apps/infrx-api/tests/w/test_engine.py b/apps/infrx-api/tests/w/test_engine.py
index 63e9547..aeda43a 100644
--- a/apps/infrx-api/tests/w/test_engine.py
+++ b/apps/infrx-api/tests/w/test_engine.py
@@ -482,7 +482,7 @@ def test_api_stream__unsupported_options_are_refused_explicitly():
     for name, value in (("tools", [{"type": "function"}]), ("tool_choice", "auto"),
                         ("response_format", {"type": "json_object"}), ("guided_json", {}),
                         ("logprobs", True), ("n", 2), ("price_snapshot", {"price_version": "x"}),
-                        ("max_tokens", 4096), ("stream", False), ("who_knows", 1)):
+                        ("stream_options", {}), ("who_knows", 1)):
         upstream, engine, held, _prepared = drive()
         prepared = text_prepared(Box(), parameters={name: value})
         with pytest.raises(errors.UnsupportedParameter) as refused:
@@ -495,6 +495,10 @@ def test_api_stream__unsupported_options_are_refused_explicitly():
     body = engine.upstream_body(text_prepared(Box(), parameters={"temperature": 0.2, "seed": 7,
                                                                 "n": 1}))
     assert body["temperature"] == 0.2 and body["seed"] == 7 and body["n"] == 1
+    # and what the record consumed (G2 W-new) is neither refused nor forwarded as asked
+    body = engine.upstream_body(text_prepared(Box(), parameters={"stream": False,
+                                                                "max_tokens": 4096}))
+    assert body["stream"] is True and body["max_tokens"] == 256
 
 
 def test_api_stream__the_output_ceiling_is_validated_and_enforced():
```

With it applied and the harness shim removed, the numbers were:

- at `665857f` (in the worktree, then reverted): `tests/w` quick `115 passed in 23.49s`;
  G2's four relay files `50 passed`; W mutant `unknown_parameters_forwarded` `1/1 killed`.
- at `e5e7d3a` (on a copy):
```
2026-09-23T07:01:32Z
117 passed in 25.87s
52 passed, 2 warnings in 0.95s
unknown_parameters_forwarded: 1/1 killed
consumed_parameters_refused (proposed W mutant): 1 failed, 42 deselected in 0.25s
```

W should add a mutant: `if name in INTERNAL_PARAMETERS or name in CONSUMED_PARAMETERS:` →
`if name in INTERNAL_PARAMETERS:` (killed by `test_api_stream__unsupported_options_are_refused_explicitly`,
typed `UnsupportedParameter`). When it lands, G replaces
`engine=RecordKeysDropped(upstream.engine())` with `engine=upstream.engine()` in
`tests/g/relay_support.py` and deletes the shim.

Alternative, if the coordinator prefers G to change: G1R's validator stops putting `stream`,
`max_tokens` and `max_completion_tokens` in `parameters`. That would diverge from the frozen
fixture, so W-new is recommended.

**3. D-new, done by F cancel-cause (`6803d2f`) and consumed at `e5e7d3a`.** What remains is
D5's: 0018, so that `infrx.cancel` records the cause and `PgJobStore.cancel` stops refusing
it. Nothing in the relay changes then; its interim fallback simply stops firing. D5 should
consider a platform cancel cause for the relay's non-client stops (Limit 3).

**4. D5:** the psycopg `CatalogDirectory` over 0007 (still G1R request 2b), on the same pool,
with each probe path usable from a fresh loop. `pilot.Probe`'s first answer runs on its own
thread.

**5. D4 (confirm at handback, not a new request):**

- The terminal journal event for every terminalization (D4 item 3's trigger). The relay keeps
  its `get_owned*` fallback until then.
- `PgStreamStore.usage()` is the journal probe's body.
- `read_owned` raises `ReplayGap`/`JournalExpired`/`InvalidCursor` as typed errors.

**6. Coordinator:**

- `TASK_PORTS["g2"] = {"valkey": 55466}`: **done** at `9c1c6ed`; this lane used no Valkey.
- `gc_outbox` daily timer (D2 request 7, remainder): coordinator or I.
- Worker composition (give workers `rt.lifetime.reconciler.index`; Q3 request 2).
- `MediaCollector` in the lifespan (M3 request 3, needs `PROCESSING_CACHE_MAX_BYTES` and
  `MEDIA_GC_INTERVAL_S`).

**7. I3B:**

- Metric families for `Reconciler.metrics` (`outbox_lag_s`, `missing_index`, …). The registry
  has none, so the pilot exposes them as `rt.lifetime.reconciler.metrics`.
- Counting the ingress's own pre-acceptance refusals (auth, validation) is one `inc` in the
  chat handler's guard path. The relay counts only what reaches the acceptor.

**8. E3B2 (tests/integration, E3B phase 2)**, what the mounted route does, for the journey and
drill bodies:

- **sync:** 200 only after the terminal commit.
  - Body: `chatcmpl-<request_id>`, content from `read_result`, and usage.
  - Headers: `Inference-Id` = the job's request id; `Server-Timing` `prepare;dur=…`;
    `Idempotency-Replayed: true` on a replay.
- **SSE:**
  - First frame `event: infrx.progress` with `{"job_handle","request_id","phase":"accepted"}`
    and no `id`.
  - Then `id: <g>-<s>` frames: engine progress, and deltas as `chat.completion.chunk` with
    `delta.content` (`role` on the first).
  - Then a usage frame (no id), and `id: <terminal cursor>` `data: [DONE]`.
  - Failures: an `infrx.error` envelope (`stream_interrupted`, `state_conflict`,
    `deadline_exceeded`, `replay_gap`, `journal_expired`, `status_unknown`) and `[DONE]` only
    when a terminal commit is known.
- **dr11 (disconnect mid-stream):** the job is cancelled in the store and the hold is
  `unknown` if the job published. The worker's next append is `already_terminal`.
- **rc03 (gateway restart):** a stream cancelled by the process after its identity frame
  leaves the job running; the same key's retry replays the journal from the start.
- **dr17:** the ingress's route endpoint module is `infrx.gateway.routes.ingress`
  (`chat.__module__` is set).

**9. G1R request 6: carried.** `tests/g/credit.py` (G1R's stand-in) still drives
`test_admission.py`. G2's own CREDIT cases already run on the wire-in's `FakeJobStore`
(`credit_jobstore_factory`); swapping `test_admission.py` over is a G follow-up. "G1R's own
factories should run `V2_SUITES`" (wire-in request) is also carried, because G has no store
factory of its own.

**§5 requests: done or carried.**

| Request | Status |
|---|---|
| G1R 1 | done (acceptor; `build_ingress_deps`) |
| G1R Limit 2 | done |
| G1R S5/C4 | done (`assert_route_table`, called by `create_app` in the cutover) |
| G1R 6 | carried (#9) |
| D3 2 + delta | done; the causes are passed (`e5e7d3a`), with the PostgreSQL interim until D5's 0018 (#3) |
| D2 7 | done (pool + configure); `gc_outbox` timer carried (#6) |
| Q3 2 | done (Reconciler, unique id, lifespan stop); metrics mapping to I3B (#7); worker re-pointing carried (#6) |
| W2 6 | not used: the pilot worker is another process, so the journal is the only byte source; `already_terminal` handled via `get_owned*` |
| W3 9 | gateway phase only; worker timings carried |
| I3B 3 | done in the acceptor; ingress-refusal count carried (#7) |
| I2B 7 / E1B 5 | done (`Server-Timing` names from `metrics.PHASES`, `Idempotency-Replayed`) |
| I2B `wait_ready` | done (loopback `/readyz`) |
| I0 3 | done (`silence_transport_logs`; the tests/i inversions are in the cutover; module path unchanged) |
| M2 5 | done |
| M2 Limit 11 | not taken: the validator always emits `media=()` and `prepare_request` overwrites it |
| M3 3 | `MediaUploads` as `rt.media_store`: done; collector carried (#6) |
| F2R-A IR-A5 | done (cache dir, `LargeBodies` from deployment); spool segment size is T's composition, carried |
| F2R-B IR-5 | on the checklist above |
| F2P wire-in | regime dispatch, active card and `max_index_*` (ValkeyScheduler reads `PilotSettings`): done; `V2_SUITES` carried |
| E3B phase 1 | #8 |
| S2M D1 | done |
| S2M D10 / D12 | in the cutover diff |

## Verification log

- 2026-09-23: Written at implementation SHA `e5e7d3a` from the runs quoted above. Every count is
  copied from command output. "Implemented (fakes)" is the claimed status; nothing here is
  integrated or live-verified.
