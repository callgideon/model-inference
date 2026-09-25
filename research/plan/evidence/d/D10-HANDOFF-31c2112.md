# D10 — durable uploads, execution eligibility, safe cleanup and result read authority

| | |
|---|---|
| Task | D10 (consumer v1 wave, program 22); test ids UPLOAD-RESTART, RETENTION-DURABLE, ADMISSION-READY, RESULT-EXPIRY, CREDIT-CUTOVER, DUR-RLS; closes RV-02/RV-03/RV-05/RV-11 at the database, RV-09's login half |
| Branch / worktree | `codex/d10-durable` / `.claude/worktrees/codex-d10-durable` |
| Base | `dff31efc` |
| Head (code) | `31c2112c (D10.a dee59ed8, D10.b 13aeb6d3, D10.c f44499ea, M5 findings 31c2112c)` |
| Carried (identical content, merges cleanly) | coordinator's tasklocal patch `79e4897e` (= bfb3a8af); F2C-L `2d5e4743..1b9c7411` (8 commits: contracts/v2/lifecycle.py, conformance/lifecycle.py + acceptance.py, fakes, records.TerminalOutcome.result_expires_at) |
| Migrations | `0019_upload_readiness.sql`, `0020_content_lifecycle.sql`, `0021_read_authority.sql` (0019 was the next free number; 0020/0021 follow). 0001-0018 untouched. |
| Isolation | `INFRX_D_TASK=d10`: `infrx-d10-postgres` 127.0.0.1:55442 (DB `infrx_d10*`), `infrx-d10-valkey` 55469, `infrx-d10-postgrest` / network `infrx-d10-net` (no host port). No hosted DB, box, AWS or S3. |
| Status | **HANDOFF (session limit)**: slices a, b, c committed and green (§3); D10.d evidence below is complete except the two runs listed under "Unverified". Not integrated (G7/W5/M5/M6 wire the ports; wiring requests §8). |

## 1. What exists now (per slice)

| Slice | Commit | What |
|---|---|---|
| D10.a | `dee59ed8` | **0019**: upload tickets on 0010's `media_uploads` (server-measured receipt written once, finalized source content row + generation, abort reason; the `upload_*` boundary is the only writer — 0010's platform DML is revoked); `content_objects` (one row per stored object, generation, tenant-prefix-bound key); `job_media.content_id/generation` (the manifest); `job_readiness` (the marker); `admit_ready` (admit + expectation R69 + pinned capability + manifest + marker, ONE transaction, zero media included); `claim_preparation_ready` (no lease without the marker; 0012's `claim_preparation` stays the previous runtime's door); `readiness_cutover_check`. `infrx/state/lifecycle.py` `PgLifecycle` implements F2C.a's `UploadRepository`, `ReadinessStore`, `ContentLifecycle`. |
| D10.b | `13aeb6d3` | **0020**: leased, fenced deletion claims per generation; `content_referenced` (the one liveness rule, persisted rows only); `content_candidates/claim/tombstone/acknowledge_delete`; database content (`job_results/<id>`, `jobs/<id>`) registered by triggers where written and SCRUBBED inside `acknowledge_delete`; 0014's `job_results_immutable` now runs a guard allowing only that scrub; 0011's `jobs_admission_record_guard` allows only a terminal job's one scrub; `read_result` answers from the persisted expiry (`result_pending` / `result_expired`, never empty text); `register_existing_database_content` (operator, bounded). 0019 fixes: legacy pinned capability; discovered orphan payload/prepared rows. |
| D10.c | `f44499ea` | **0021**: `job_admission` outcome carries `result_expires_at`; NOT VALID `jobs_success_has_result_expiry`; P-22 `resolve_usd_revision` + `admit_legacy_usd` (canonical revision, `requested_model` verbatim; provenance constraint widened for that one column); `reconcile` operation-id lock; `require_feature` FOR SHARE (G8); `public.consumer_jobs` / `consumer_job_result`; `infrx_runtime` / `infrx_monitor` logins; browser key scope re-asserted. `catalog.py` (the listing's card), `jobstore.connector` (pooler-safe), `operations._typed` (23514). |
| D10.d | this file | evidence, below. |

Migration ids and sha256 (0001-0018 untouched; `git diff dff31efc HEAD -- apps/app/supabase/migrations/00{01..18}*` is empty):

| File | sha256 | lines |
|---|---|---|
| `0019_upload_readiness.sql` | `18d017d40bb71aea6400a5fbd458eb9cd181482f7765a46df1d8b85dc9912bd3` | 980 |
| `0020_content_lifecycle.sql` | `06a5dfde0c342ef4666a715e411915fd5e192f2791424a29efbaa66b710e2ec4` | 493 |
| `0021_read_authority.sql` | `e0d64ae0d519c6a29b853c43efee5e09808b279d7c1fb218ef454afee81c63c5` | 536 |

Each is additive and re-runnable (applied twice in `test_upgrade_d10`, and the D1R re-run check). Each file's header states its lock order and its own rollback.

## 2. The shared decisions, answered

| Decision (01 "Shared decisions") | Answer |
|---|---|
| Where admission/ready completion becomes atomic; old in-flight jobs | ONE phase: `admit_ready` writes job, hold, reservations, outbox, idempotency mapping, manifest and marker in one transaction; nothing else writes a marker. `claim_preparation_ready` refuses an unmarked preparing job (`not_ready`) and leaves R29 to end one past its deadline (released free, 0016's reaper). No backfill (F2C-L D1). Cutover: pause admission (the regime flags), wait for `infrx.readiness_cutover_check()` → `ready: true` (every unmarked preparing job prepared by the previous worker or reaped), start the new runtime, resume. Rollback to the previous runtime needs nothing undone. |
| Which persisted reference protects each object; attach/delete serialization | `content_referenced` (0020): a manifest row (`job_media.content_id/generation`) of a job running or inside `settled_at + retention_s` (retention captured at admission in `job_readiness`); the object's own job (a result exactly until `result_expires_at`); an open ticket's destination; a finalized, unexpired ticket's source; any running job naming the key (previous runtime / discovered). Admission holds the content row FOR SHARE, `tombstone` takes it FOR UPDATE and rechecks inside: proven both orders (§4). |
| What survives content expiry; late retry | The job row, idempotency mapping (and its tombstone expiry), outcome, usage, settlement, ledger, holds, digests and sizes; only `job_results.body` and `jobs.request_record`'s messages/parameters are emptied (`content_scrubbed`), plus the object-store bytes M6 deletes. A late read is `result_expired`; a replay answers the terminal outcome with its persisted expiry; nothing regenerates. |
| UTC/time authority, expiry boundary, deletion lag | `infrx.now()` (transaction time) for every instant; windows persisted once from the adapter's configuration (`expires_at`, `eligible_at`, a claim's `expires_at`, `retention_s`, `result_expires_at`). Live while `now < instant`, equality has passed (tested at `instant - 1µs` / `instant`). Deletion lag objective: P-25 (not invented). |
| Backfill cost/locking; rolling compatibility | No readiness backfill. `register_existing_database_content(limit, grace_s)` pages pre-0020 content rows (idempotent). The previous runtime runs unchanged on 0019-0021 (full tests/d with the old adapters, §3; `test_upgrade_d10` prepares its in-flight jobs). |
| Idempotency key on a refused admission (coordinator update 3) | A refusal raises inside the admission transaction, so no job, hold, outbox or idempotency row exists: the key binds only to a committed job. Proven: same request and key refused (`upload_not_finalized`) then admitted after the cause is fixed, then replayed (`check_ready_refusals`); two connections, first attempt rolled back → the waiting second admits afresh, first committed → the second replays (`check_ready_races`). |

## 3. Commands and results (exit codes, counts)

All `cd apps/infrx-api`, `INFRX_D_TASK=d10 INFRX_D2_VALKEY_CONTAINER=infrx-d10-valkey INFRX_D2_VALKEY_PORT=55469` (the reserved lane services; postgres:16.14 digest `33f923b0…` unless stated).

| Command | Head | Exit | Result |
|---|---|---|---|
| `make api-env` | base | 0 | pinned env |
| `uv run --frozen pytest -q tests/d` (baseline) | `79e4897e` (base + tasklocal) | 1 | 664 passed, 5 xfailed, **1 failed** (`test_pgharness` decoy: d2's decoy 55473 = g8's reserved Valkey) — fixed in `dee59ed8` |
| `pytest -q tests/d` | `dee59ed8` (a) | 1 | 694 passed, 13 xfailed, 2 failed (decoy bind at 56442 lost to an ephemeral port - moved decoys below 32768) |
| `pytest -q tests/d` (full, default mutant subset incl. all D10 mutants) | `f44499ea` (c) | **0** | **791 passed, 1 skipped (PostgREST test: Supabase image only), 8 xfailed, 0 failed** (1171 s) |
| `pytest -q tests/d/test_ready.py test_lifecycle_conformance.py test_jobstore_conformance.py test_credit_jobstore_conformance.py test_admission.py test_settle.py test_reads.py` | `31c2112c` | **0** | 195 passed, 7 xfailed; F2C.d transcripts 24/24 after normalization (9 raw differences, representation only) |
| other tracks on the D10 schema: `contracts/test_cancel_cause.py g/ops/test_publication.py g/ops/test_cli.py m/test_pilot_media.py w/test_worker_main.py contracts/v2/test_v1_projection_pg.py g/test_composition.py w/test_prep_worker.py q` | `31c2112c` | **0** | 324 passed, 7 skipped |
| D10 + moved mutants (`-k d10_ …`), default subset | `f44499ea` | 0 | all killed (40 D10 + 27 moved/split) — part of the full run above |
| I8 `privilege_probe.py --role infrx_runtime --allow-functions D10-runtime-functions.txt` (plain image, dev DB) | `f44499ea` 0021 | 0 | **PASS**, 62 checks (4 identity, 17 must-deny, 41 functions) |

**Unverified at handoff (next steps 1-3):** `INFRX_MUTANTS=all` over the D lists (was running; the progress line showed 3 `F` by 29% — names not yet known; rerun and triage, most likely an older mutant whose check now meets a D10 guard first); the Supabase-image runs (`INFRX_D1_IMAGE=supabase`) of `test_ready test_content test_reads test_lifecycle_conformance test_upgrade_d10 test_postgrest_d10` and the probe on that image (script: scratch `supabase_probe.py`, same as the plain one with `_sb` for the login).


## 4. Races (two connections, committed state, lock waits observed)

Each schedule: connection A opens a transaction and runs its call; B is started and must be seen waiting on a lock (`pg_stat_activity.wait_event_type = 'Lock'`); A commits; B's answer is asserted. Test clock frozen; equality boundaries asserted at `instant - 1µs` and `instant`.

| Race | Where | Outcome asserted | Negative control (mutant) |
|---|---|---|---|
| admission ∥ same key (commit) | `check_ready_races` | B replays the same job and marker | `d2_admission_lock_dropped` (D2) |
| admission ∥ same key (A rolls back) | `check_ready_races` | B admits afresh; one job | — (the key binds only to a commit) |
| finalize ∥ finalize | `check_ready_races` | B waits on the ticket, answers the same ticket; one content row; other bytes `bytes_changed` | `d10_receipt_rewritable` |
| complete ∥ admit | `check_ready_races` | admission waits for the completion, admits the finalized source | `d10_admission_does_not_hold_the_ticket` |
| admit ∥ abort | `check_ready_races` | abort waits, then `upload_not_open` | `d10_admission_does_not_hold_the_ticket` |
| tombstone ∥ admit (delete first) | `check_content_races` | admission waits, `content_retiring`, footprint unchanged | `d10_retiring_source_admitted` |
| admit ∥ tombstone (attach first) | `check_content_races` | tombstone waits, `reference_live`; object live | `d10_tombstone_skips_the_reference_recheck` |
| claim ∥ claim | `check_content_races` | one claim; B `claim_held` | — |
| complete ∥ cancel | D5's `test_settle_races` (unchanged, green on 0019-0021) | completion wins or cancel answers the committed outcome | D5's |
| expire ∥ read | `check_content_scrub`, `check_result_expiry_persisted`, `check_consumer_reads` | readable at `expires - 1µs`, `result_expired` at `expires`; scrub refused before | `d10_result_expiry_from_config`, `d10_scrub_before_the_expiry` |
| reconcile ∥ reconcile (same op id) | `check_reconcile_race` | same request: B replays; other request: B `idempotency_conflict`; ONE audit row | `d10_reconcile_race_untyped` (raw 23505 without the lock) |
| admission ∥ regime freeze (G8) | `check_flag_freeze_race` | the flag UPDATE waits for the admission in flight; a later admission is maintenance | `d10_freeze_races_the_admission` |

## 5. Role matrix (real roles, JWTs, PostgREST)

| Principal | How exercised | Result |
|---|---|---|
| anon | `set local role anon`; PostgREST with no token | `consumer_jobs` 42501 / HTTP 401; no `infrx` object reachable |
| authenticated (individual A) | JWT sub both claim forms (`checks._jwt`); PostgREST with an HS256 JWT | own jobs only, paged over identical timestamps without loss or repeat; own available result; `result_expired` at the persisted expiry |
| authenticated (individual B) | same | A's request id → no rows; A's result → `not_found` |
| authenticated (no wallet) | random subject | zero rows (C0 renders onboarding) |
| authenticated writing key scope | `update public.api_keys set audience` / PostgREST PATCH | 42501 / HTTP 401-403; column privileges on audience/user_id/provider_org_id/endpoint_id absent for anon/authenticated |
| service_role | functions | the D10 boundary callable; the D10 internals not; `media_uploads` read-only |
| infrx_runtime | LOGIN in the lane's container; `set_role=False` connector | the whole F2C.a lifecycle suite (26 cases) and the CREDIT JobStore suite pass with ONLY its grants; `reconcile`, `public.credit_ledger`, wallet writes → 42501; `statement_timeout` = its role default 15s; I8's `privilege_probe.py` PASS (plain image; the Supabase-image probe is next step 3) |
| infrx_monitor | catalog assertions | column-scoped reads of exactly `durable.py`'s columns, `default_transaction_read_only=on`; no `request_record`, no result body, no hold amount |

## 6. Upgrade: 0018 history → 0019-0021 (`test_upgrade_d10`)

Seeded on 0001-0018: USD grants, a settled USD job with a stored result, a USD job preparing and one queued, a settled CREDIT job, a held-unknown CREDIT job, a CREDIT job preparing, the hosted W7c/W7e USD rows, and two keyed legacy admissions (one per pilot spelling). Then 0019-0021 applied, then applied again.

| Compared | Before | After |
|---|---|---|
| tables | 48 | 50 (+`content_objects`, `job_readiness`); every other row count equal; 0 markers invented |
| exact sums (USD ledger/reserved, CREDIT ledger/reserved, both ledgers, active holds by unit, USD usage cost) | `49.99987000/0.01474560`, `20499.11200000/29.49120000`, `49.99987000`, `20499.11200000`, `0.01474560/4`, `29.49120000/2`, `0.00013000` | identical |
| job identity/money digest (ids, org/key/wallet, pins, price/snapshot, hold, state/cause/settlement, debit, result ref/expiry, hashes, request record) | sha256 of every job row's identity/money columns | identical |
| grants | — | only change: `media_uploads` platform DML revoked (by design); every function redefined keeps its ACL; the two new logins' grants are asserted separately |
| re-apply of 0019-0021 | — | no change |
| previous runtime | — | 0012's door claims and prepares both preparing jobs; no wallet drift |
| USD in flight (CREDIT-CUTOVER) | keyed admissions priced `pv_marlin2b_usd_2026_09` / `…_r1` | the same keys replay their own jobs and snapshots |
| P-22 | `nemostation/marlin-2b` → `pv_marlin2b_usd_2026_09`, `@2026-09-01` → `…_r1` | both → `pv_marlin2b_usd_2026_09_r1`, `model_revision = nemostation/marlin-2b@2026-09-01`, `requested_model` verbatim; 9 other spellings refused; the pre-catalog literal path unchanged |
| pre-0020 database content | — | `register_existing_database_content` registered 10 rows, then 0 |

## 7. Failure oracles (§D10.d) → executable negative controls

All are migration mutants in the default subset (`tests/d/d10_mutants.py`, `ALWAYS`): each builds a database from one edited migration and runs the named check; only an assertion from that check counts as a kill.

| Oracle | Mutants (all killed) |
|---|---|
| remove the ready predicate | `d10_claim_without_the_marker` |
| omit an upload constraint | `d10_receipt_rewritable`, `d10_finalized_without_its_source`, `d10_ticket_writable_by_the_platform`, `d10_unfinalized_upload_admitted` |
| bypass the tenant join | `d10_manifest_of_another_tenant`, `d10_consumer_reads_any_tenant`, `d10_register_outside_the_prefix` |
| skip the live-reference check | `d10_tombstone_skips_the_reference_recheck`, `d10_claim_skips_the_reference_check`, `d10_candidates_include_referenced`, `d10_legacy_running_job_unprotected`, `d10_open_upload_destination_unprotected`, `d10_retention_recomputed_to_zero` |
| delete a renewed claim | `d10_renewed_claim_deleted`, `d10_superseded_ack_accepted` |
| re-create a tombstoned object with the same identity | `d10_tombstoned_key_recreated`, `d10_old_generation_ack_retires_the_new`, `d10_register_other_bytes` |
| infer expiry from current config | `d10_result_expiry_from_config`, `d10_outcome_without_its_expiry`, `d10_success_without_expiry`, `d10_scrubbed_result_reads_empty`, `d10_consumer_result_past_expiry`, `d10_scrub_before_the_expiry`, `d10_running_request_scrubbed` |
| browser writes audience/provider scope | `d10_browser_writes_key_scope`, `d10_boundary_callable_by_browsers`, `d10_protocol_callable_by_browsers`, `d10_monitor_reads_customer_content`, `d10_runtime_role_reconciles` |
| duplicate reconcile audit | `d10_reconcile_race_untyped` |
| (expectation / capability / pricing / freeze) | `d10_expectation_ignored`, `d10_capability_unchecked`, `d10_legacy_capability_unchecked`, `d10_retiring_source_admitted`, `d10_admission_does_not_hold_the_ticket`, `d10_usd_priced_by_spelling`, `d10_requested_model_dropped`, `d10_freeze_races_the_admission` |

Moved with superseded bodies (anchors now in the D10 file, still killed): 0019 `d2_finalized_upload_rewritable`, `d2_finalized_upload_deleted_early`; 0020 `d2_admitted_request_rewritable`, `d2_result_read_across_tenants`, `d2_result_ref_shape_loose`; 0021 `d1r_missing_flag_row_is_open`, ten `d2_usd_*`/`d2_withdrawn_price_still_charged`/`d2_zero_usd_hold_written`, `d2_readmits_a_request_uuid` (split: `_credit` stays on 0011), five `d5_reconcile_*`. Code mutant `card_not_effective_checked` re-anchored to the new card query.

Failed-then-passed (recorded): the exported suite's cases are the seam (the previous `MediaUploads` loses every ticket on reconstruction, RV-02; no marker existed, RV-05); `PgLifecycle._raise_refusal` treated an aborted ticket's `refusal` string as an envelope (TypeError) — found by `test_abort_is_durable_idempotent_and_final`, fixed; `test_the_catalog_directory_reads_the_listing_card` FAILED on `dff31efc`'s `catalog.py` ("a minted card repriced the listing: rc_minted_newer"), passes; `test_pgharness` decoy test FAILED on `dff31efc`+tasklocal (d2's decoy 55473 = g8's Valkey), passes; every race above fails with its negative control.

## 8. Wiring requests (not applied; owners named)

1. **G7** — `infrx/gateway/routes/relay.py` `Relay.admit`: replace `self.jobs.admit_credit(prepared, idem)` / `self.jobs.admit(prepared, idem)` **and** `_admitted`'s card/capability recheck and `media.attach` with ONE call `self.lifecycle.admit_ready(prepared, idem, AdmissionExpectation(accounting_regime=<regime>, rate_card_version=self.active_rate_card_version or None))` → `(admission, readiness)`; `_resume` is then unnecessary for marked jobs (a replay returns the recorded pair). `infrx/gateway/routes/jobs.py` `Jobs.result_expiry` → `outcome.result_expires_at` and `v2.lifecycle.read_outcome(outcome, await store.db_now())` everywhere a result's availability is decided; `read_result` answers `ResultPending` / `ResultExpired` typed. Proof: `tests/d/test_ready.py::test_crash_after_commit_a_new_process_finds_the_same_job` and the lifecycle suite, on the composed app.
2. **W5** — `infrx/worker/preparation.py` `PreparationRunner.run/_media`: `lease = await lifecycle.claim_preparation(job_id, worker_id)` (`claim_preparation_ready`; `not_ready` is `NotClaimable`, a lost claim) and the sources from `(await lifecycle.readiness(job_id)).sources[*].ref` — delete the `ATTACH_WAIT_S` polling. G8's `pgworld.settle` likewise calls `claim_preparation_ready` for `admit_ready` jobs.
3. **M5** — `infrx/media/uploads.py` over `PgLifecycle` (UploadRepository): `create` → `create(org, UploadConstraints.parse(body, …))`; PUT → write the destination, then `acknowledge_put(org, handle, bytes=<measured>, digest=<measured>)`; finalize → re-read, probe, write the content-addressed source, `complete(org, handle, source_ref)` (a failed check comes back as the typed refusal and the ticket is already aborted); an M probe refusal → `abort(org, handle, LifecycleRefusal.media_refused)`; `resolve_owned` → `resolve`. Before writing ANY object (source, payload envelope, prepared, destination): `register(ContentIdentity(..., origin=written))`; `content_retiring` means wait and retry.
4. **M6** — `infrx/media/retention.py` over `PgLifecycle` (ContentLifecycle): candidates → claim → tombstone → object delete (only while the claim has ≥ one store request timeout left; key under the adapter's own prefix; generation key `key.g<n>` as M6 already does) → `acknowledge_delete`; a `location = database` tombstone needs no external step (the ack scrubs). Unknown keys under the prefix → `register(origin=discovered)`; payload/prepared discovered with no job are accepted (0019). `DependencyUnavailable` = retain and report. Real-adapter factory for M6's `d10` world: `infrx.state.pgtesting.make_lifecycle_factory(fresh_database, dsn_for, seed_sql, *, upload_window_s, grace_s, claim_ttl_s, retention_s, connect_for)` (keywords `upload_ttl_s/grace_s/claim_ttl_s/retention_s` per call), adapter `infrx.state.lifecycle.PgLifecycle`. Claim TTL default 300 s.
5. **Coordinator composition** — `infrx/gateway/pilot.py`, worker `__main__`: build ONE `PgLifecycle(connect, limits=settings.pilot, upload_window_s=…, grace_s=…, claim_ttl_s=…, retention_s=…)` on the existing pool's `connect` (no new pool; I8's budget) and hand it to relay, worker and media; `configure_connection` per WR-I8-1 (no session SET on 6543; none at all with the dedicated login) — `jobstore.connector(dsn, set_role=None|False)` already does its half.
6. **I8** — provision `alter role infrx_runtime login password '<secret>'` and `alter role infrx_monitor login password '<secret>'` from the secret store; `DATABASE_URL` → infrx_runtime, `MONITOR_DATABASE_URL` → infrx_monitor; `privilege_probe.py --role infrx_runtime --allow-functions research/plan/evidence/d/D10-runtime-functions.txt`; the cutover gate `select infrx.readiness_cutover_check()`; `validate constraint jobs_success_has_result_expiry` once `select count(*) from infrx.jobs where state='succeeded' and result_expires_at is null` is 0; `select infrx.register_existing_database_content('{"limit":1000,"grace_s":<CONTENT_GRACE_S>}')` until 0.
7. **G8** — `operations/transition.py` `_QUERIES["readiness"] = "select infrx.readiness_cutover_check()"`; `spent` → `public.consumer_jobs` pages (settled `charged` in CREDIT) or a D10 aggregate on request; the transition inventory SQL may move into `infrx/state` (D is willing to own it — say the word).
8. **C0/U4** — `rpc('consumer_jobs', {p_after, p_limit ≤ 100, p_request_id})` with the user's session (cursor = the last row's `cursor`); `rpc('consumer_job_result', {p_request_id})` for owned output within the persisted expiry (`result_expired` / `result_pending` / `not_found` typed).
9. **tasklocal** (coordinator) — every lane port lies in the kernel's ephemeral range (32768-60999): measured twice, another lane's outgoing connection took `127.0.0.1:55442` as its local port and the container bind failed. `tests/d/pgharness.py` now retries a bounded time; the cause is removed by lane ports below 32768 or `net.ipv4.ip_local_reserved_ports`.

## 9. Proposed rulings (coordinator numbers them) and configuration names

- **Readiness is one-phase.** Only the admission transaction writes the execution-ready marker, for zero media too; a preparation lease requires it; no backfill; a runtime cutover pauses admission until no preparing job lacks a marker (`readiness_cutover_check`).
- **A refusal binds no idempotency key.** Any refusal before the admission commits leaves no job, hold, outbox or mapping; the same key may be retried once the cause is fixed and then replays that job.
- **PostgreSQL decides deletion.** Content rows, leased fenced claims, the reference recheck inside the tombstone, generation-checked acknowledgement; database content is deleted by scrubbing inside the acknowledgement; the external delete is issued only while the claim has at least one store request timeout left; unreachable authority, ambiguous ownership or a live lease retain.
- **Discovered orphans.** A payload or prepared object discovered with no row may name no job (owner = its key's organization); only a running job naming the key protects it. (F2C's `ContentIdentity` validator needs the same exception.)
- **Persisted expiry is the only read authority** for results (store, consumer reads, scrub); a success with none reads `unavailable`.
- **Dedicated logins** carry their bounds as role defaults and hold no role membership or BYPASSRLS; each direct read has a policy for that role alone.
- **08 §5 names (values are P-25):** `UPLOAD_WINDOW_S` (ticket window, persisted `expires_at`), `CONTENT_GRACE_S` (content eligibility delay, persisted `eligible_at`), `DELETION_CLAIM_TTL_S` (> the object store's request timeout; M6 ≥ 300), `SERVING_CONTENT_RETENTION_S` (sources/payload/prepared after settlement, captured per job at admission). `RESULT_TTL_S` is the existing `result_ttl_s`, persisted at settlement.

## 10. Open issues

- **F2C.d transcripts:** `replay(PgLifecycle)` raw differs in 9/24 cases, all in four representation classes (a digest of the harness's raw request id; reservation order; the R30 result reference; candidate-page order among equal instants and D10's database content rows the fake does not model). After normalizing exactly those, 24/24 transcripts match with exact relative instants (`test_the_versioned_acceptance_transcripts_replay_exactly`). Proposed to F2C-L as normalization; not a behaviour gap.
- **F2C `ContentIdentity`** refuses a discovered payload/prepared row with no job (0019 accepts it for M6) — until F2C amends the validator, `PgLifecycle` would fail to decode such a row in a candidate page.
- **P-25** retention/grace/claim values and the deletion-lag objective are configuration (§9), not invented.
- **Pre-0018 successes** (no persisted expiry) read `unavailable` → 410 by design (none expected hosted).
- The residual lease risk (a sweeper pausing between its lease check and the store receiving the delete) is bounded by M6's generation keys (`key.g<n>`), which make a delayed delete unable to reach re-created bytes.
- Not run here: hosted apply, the box, AWS (coordinator's).

## 11. Handoff: next steps, in order

1. Rerun `INFRX_MUTANTS=all uv run --frozen pytest -q tests/d/test_migration_mutants.py tests/d/test_code_mutants*.py tests/d/test_signup.py` (≈15-30 min under load); triage any survivor/setup error (expected cause: an older check now tripping a D10 guard first — fix the check's fixture, as done for `jobs_success_has_result_expiry` in `checks.py`/`checks_leases.py`).
2. `INFRX_D1_IMAGE=supabase` runs of the D10 suites and `test_postgrest_d10.py` (needs the pinned PostgREST image, present locally); record counts here.
3. The Supabase-image privilege probe for `infrx_runtime` (and a column-read probe for `infrx_monitor`).
4. Append the final counts to this file's successor `D10-<head7>.md` and hand back.

Nothing is mid-edit: the tree is clean at `31c2112c`.

## 12. Remaining effort

Optimistic 2 h, likely 4 h, pessimistic 8 h (confidence medium): the three runs above plus triage; integration effort is the wiring lanes' (G7/W5/M5/M6/I8/G8/C0), estimated there. Basis: all D10 SQL, adapters and checks are committed and green on the plain image; the Supabase image has passed every earlier D suite, and 0019-0021 use no image-specific feature except role creation (`create role … nologin noinherit`, allowed to Supabase's `postgres`).

## 13. Addendum: the `INFRX_MUTANTS=all` run finished (head `31c2112c`)

`INFRX_MUTANTS=all pytest -q tests/d/test_migration_mutants.py tests/d/test_code_mutants*.py tests/d/test_signup.py`: exit 1, **720 passed, 4 failed** (1487 s). All four are **survivors** (the check ran and passed with the mutant applied); none is a setup error:

| Mutant | Anchor | Likely cause (to confirm) |
|---|---|---|
| `d1r_usd_job_may_carry_pins` | 0006 `jobs_regime_fixes_provenance` (`num_nulls(wallet_id, model_id, requested_model, …)`) | superseded: 0021 replaces the constraint with `jobs_regime_fixes_provenance_v2`; move the mutant to the v2 body (superseded-body pattern, as for the 22 already moved) |
| `d1r_credit_job_may_carry_usd_price` | 0006, same constraint (`else price_version is null and price_snapshot is null`) | same as above |
| `d2_readmits_a_request_uuid_credit` | 0011 `admission_idempotency` credit replay | 0021's redefinitions or 0019 `admit_ready` (replay first) may shadow the 0011 body; check which body runs and re-anchor, or strengthen the check to use the credit path |
| `d2_finalized_upload_rewritable` | 0019 `media_uploads_guard` (`if old.state <> 'created' and row(new.*) is distinct from row(old.*)`) | the check's rewrite is probably refused earlier by 0019's constraints (`finalized_is_the_receipt`, receipt once), so the guard line is not the killer; make the check rewrite a column no constraint covers (e.g. `mime`) |

Next step 1 (§11) is now: fix these four (re-anchor or strengthen the check), then rerun only `-k` for the four names plus the default subset.

## Verification log

- 2026-09-25: HANDOFF written at the coordinator's session-limit notice; head `31c2112c`; unverified items listed in §3/§11.
- 2026-09-25: addendum §13: the full mutant run finished (720 passed, 4 survivors named with likely causes).
