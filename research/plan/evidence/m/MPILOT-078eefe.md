# MPILOT — the pilot's two media gaps: upload refs at admission, prepared media across processes

## Task and status

| | |
|---|---|
| Task | M PILOT-MEDIA lane (track M). Two product gaps E3B phase 3 found driving the mounted gateway: GAP 1 (a chat/job naming a finalized `infrx-upload:` ref is 400) and GAP 2 (the worker process cannot resolve prepared media). Oracles MEDIA-SEC, MEDIA-PARITY, DUR-RECOVER (media half) |
| Owner / session | implementation agent (Claude Opus 5.5, 1M context), 2026-09-23 |
| Status | **implemented**, in process and on the D harness's PostgreSQL (see Runs for what ran where). **Not integrated**; nothing ran on the pilot box. No live-state claim. |

## Source

| | |
|---|---|
| Base SHA | `6cb8ebe` (the cutover lane's head: ROUTERS mounted, adapters from settings) |
| Implementation SHA | `078eefe` (the last commit touching `apps/infrx-api/`; this report is committed on top) |
| Branch / worktree | `codex/m-pilot-media` / `.claude/worktrees/codex-mpilot`. Nothing pushed. |
| Merge | `git fetch origin && git merge --no-ff --no-edit origin/codex/cutover-mount` → `8418832` (the cutover had moved to `f7d9b03`, including M1-L2's `S3ObjectStore`). One conflict, the Makefile `api-mutants` line: both lists kept (`tests/m/test_pilot_mutants.py` and `tests/m/test_s3_mutants.py`). `gateway/pilot.py` merged cleanly. Post-merge runs in Runs rows 13–15. This report is committed on top of the merge. |

## The gaps, as found and as fixed

**GAP 1.** `MediaUploads.prepare_request` → `MediaPreparation._materialize_all` →
`MediaStaging.materialize` accepted only http(s)/`data:` sources, so the third form R61(1)
defines — `infrx-upload:upl_<id>`, which `validate.py` lets through — was refused
`invalid_request` at admission. Only `stage` knew upload refs (through `resolve_owned`), and
G4U's tests stopped at `validate.check_messages`.

Fix: `MediaUploads.materialize` resolves an `infrx-upload:` source with `resolve_owned` — the
rule `stage` already applies to an upload ref, so admission and staging cannot disagree:
owner (another org's handle is `not_found`, R61(1)), finalized (open → `not_found`, or
`invalid_request` when the handle is otherwise indexed), the object at its content-addressed
`source` key still carrying the finalized digest (else `not_found`). The ref is the upload's
own (kind `upload`, verified digest, measured duration) at the same key a fetched or `data:`
URL of the same bytes gets. Its size is spent from the request's `MAX_MEDIA_BYTES` budget like
any source (`413`). New rule (proposed ruling below): `resolve_owned` also refuses an own
upload past its `expires_at` (`410 upload_expired`), in the real store and the fake alike.

**GAP 2.** Two process-local indexes stood between the gateway's preparation and the worker:
M's attach (`by_job`) and the processing-cache index (`ProcessingCache.entries`). The worker
gets its prepared refs from PostgreSQL (`load_work` → `jobs.prepared_refs`, D2), but
`local_uri(ref)` asked an empty in-memory index → `not_found` → `platform_error`.

Fix, two halves:

* **The cache is lookup-able on disk by content hash** (no new format — M4's layout already
  names the file from the key): `ProcessingCache.get(org, digest, profile, mime)` finds a miss
  at the path the key builds (`<root>/<org>/<profile>/<digest16>/source.<ext>`), trusts it
  only if the bytes hash to the key's digest, and counts its life from the file's mtime, which
  `put` now sets to the entry's `stored_at` (so the disk says what the index said, on any
  clock). `local_uri` passes the ref's type. An entry found on disk carries no measurement,
  so `prepare` re-measures it rather than trusting absent facts. The worker mounts the
  directory read-only (I2B's unit) and needs nothing else.
* **The attach is durable — no migration.** `infrx/media/attachments.py` `PgAttachments`
  records the attach in D2's 0003 relations: `infrx.staged_media` (each ref as the store
  described it) and `infrx.job_media` (`role='source'`, `position`), one transaction,
  write-once (a handle recorded with other content, or a job bound to other refs, is
  `conflict`); R55 by the composite foreign key `(job_id, org_id) → infrx.jobs` (a ref of
  another org → `not_found`). `MediaStaging(attachments=)` writes it first, then its
  in-process copy; `attached(job_id)` reads this process's copy, else the durable one;
  `prepare` and the relay's `_resume` bound-gate read `attached`. `pilot.adapters_from_env`
  composes `PgAttachments` on the pool; `build_ingress_deps(attachments=)` hands it on.

Why no migration: 0003's `staged_media`/`job_media` are exactly "the sources a job was
admitted with" (their header says so), `service_role` holds DML on them (0004), and their
FKs already encode R52/R55. What 0003 cannot say is "attached with **no** media" — see Limits 1.

## Per-item table

| Item | Commits | Cases (tests/m/test_pilot_media.py unless noted) | Mutants → kill |
|---|---|---|---|
| 1 upload refs at admission | `409c459` | `test_mpilot__a_chat_naming_a_finalized_upload_is_prepared_from_the_store`, `…another_orgs_upload_is_not_found_at_admission`, `…an_unfinalized_upload_is_refused_at_admission`, `…an_upload_past_its_window_is_upload_expired_at_admission`, `…an_upload_over_the_media_bound_is_refused_at_admission`, `…an_upload_whose_object_changed_is_refused_at_admission[replaced\|deleted]`, `…an_upload_named_in_a_job_over_the_mounted_gateway` (create_app: POST /v1/uploads → PUT → complete → POST /v1/jobs 202; the admitted ref's digest = sha256(clip), key content-addressed, staged and attached; 404 other org, 404 uncompleted, 410 window closed, 413 tightened bound — none admitted, none staged) | `upload_ref_not_resolved` (the in-process case dies on the typed `invalid_request`; e2e `AssertionError` on the 400 envelope), `admission_ignores_the_owner` (`Failed: DID NOT RAISE NotFound`; e2e: a payload staged for the other org), `admission_accepts_an_unfinalized_upload` (the squatted handle reaches the object recheck: `NotFound: the object for upload …` where `invalid_request` is claimed), `admission_accepts_an_expired_upload` (`DID NOT RAISE UploadExpired`; e2e 202 where 410), `admission_skips_the_size_bound` (`DID NOT RAISE RequestTooLarge`; e2e 202 where 413), `admission_resolves_a_changed_object` (`DID NOT RAISE NotFound`, both params) — `tests/m/pilot_mutants.py` |
| 2 durable attach + disk lookup | `dd54251`, `52d3356`, `6b7cd1c`, `6e4343e`, `1382750`, `078eefe` | `test_mpilot__a_second_process_resolves_the_attach_and_the_local_file`, `…a_cache_file_that_is_not_the_hash_is_not_served`, `…a_cache_file_past_its_life_is_not_served_by_another_process`, `…a_worker_runs_a_video_job_prepared_in_another_process` (G2's world admits a video_url chat; W's real AttemptRunner + VllmEngine run it with `local_uri` from a process that prepared nothing; the engine gets `file://<root>/<org>/v1/…`; 200), `test_mpilot_pg__a_second_process_resolves_the_attach_and_the_local_file`, `…pg__each_job_reads_back_its_own_refs_in_order`, `…pg__an_attach_is_write_once_and_tenant_bound`, `…the_pilot_composition_records_the_attach_on_its_pool` (create_app from settings: `PgAttachments` on the job store's connect; injected stores: none) | memory: `attach_not_persisted`, `attach_not_read_back` (`assert None == (MediaRef(…),)`: the second process reads no attach), `prepare_reads_only_this_process` (`NotFound: no staged media for job …` in the second process), `local_uri_misses_the_disk` (the second process's `local_uri` is `not_found`; the worker e2e answers 500 `internal_error`), `index_rebuilt_without_the_hash` (`DID NOT RAISE NotFound` for other bytes at the path), `stale_entry_served_after_expiry` and `put_leaves_the_file_time` (`DID NOT RAISE NotFound` in the third process past the life), `disk_entry_trusted_for_its_facts` (declared `AttributeError: 'NoneType' object has no attribute 'mime'` at `prepare`), `attach_record_not_composed` (the composed store's `attachments` is None: the case's first `isinstance` assertion). PostgreSQL (D harness, `INFRX_D_TASK=d4`): `attach_not_persisted_pg` (`assert None == (MediaRef(…),)`; the exported attach case fails too), `another_jobs_refs_returned` and `attach_order_lost` (the second job's attach is refused `Conflict: … already attached to other media` — `put`'s read-back sees the other job's rows / the reversed order), `rebind_to_other_refs_accepted` and `recorded_content_unchecked` (`DID NOT RAISE Conflict`), `foreign_job_not_translated` (declared `psycopg.errors.ForeignKeyViolation: … "job_media" violates foreign key constraint`) |
| 3 conformance | `9b9e2c9`, `40ae99c`, `6e4343e` (on PostgreSQL) | exported `media_sec__an_upload_is_usable_only_within_its_window`, `media_parity__an_attach_outlives_the_process_that_made_it` (new optional hook `reopened()`); fake passes (its `reopened` is itself; its window rule came with item 1); `MediaUploads` 11/11 (`tests/m/test_uploads.py`); `MediaPreparation` passes the attach case, skips the window case on `create_upload` (M3's); `MediaStaging` skips both (M3's, M2's) | `tests/contracts/mutants.py`: `upload_used_past_its_window`, `prepare_serves_the_first_attach` (new), `prepare_falls_back_to_any_job` (now also named by the attach case) — killed under a conformance-only runner (see Runs) |
| 4 evidence | this commit | — | — |

Re-anchored (same defect, moved line): `cache_hit_stands_in_for_the_durable_artifact`
(tests/m/mutants.py), `inflight_replay_rechecked_after_attach` (tests/g/mutants.py),
`inflight_async_replay_rechecked` (tests/g/jobs/jobs_mutants.py) — all still killed.
`tests/m/test_gc.py::test_a_live_jobs_input_is_never_collected`'s last line now asserts the
live job's ref is still indexed instead of `resolve_owned` after 12× the window (which the
window rule answers 410 for a *new* use).

## Runs

Every run: `apps/infrx-api`, a lane-private `TMPDIR`, `INFRX_D_TASK=d4 INFRX_D2_VALKEY_PORT=55465
INFRX_D2_VALKEY_CONTAINER=infrx-mpilot-valkey INFRX_Q_VALKEY_PORT=55494`. Logs in the lane's
scratch (`scratchpad/mpilot/`). Tails copied from the command output.

| # | Command | Head | Tail |
|---|---|---|---|
| 1 | `make api-test` (detached, 21:34Z) | `078eefe` | `23 failed, 3474 passed, 1 skipped, 5 xfailed, 2 warnings in 1864.42s (0:31:04)`, `exit=2`. The 23 FAILED ids are exactly those `pytest tests/contracts/test_mutants.py tests/contracts/test_cancel_cause.py` fails on a clean `6cb8ebe` checkout (`23 failed, 18 passed in 25.47s`; `comm -23` of the two sorted FAILED lists is empty): the cancel-cause case and 22 contracts mutants the shared runner refuses on its pristine baseline. None is MPILOT's. |
| 2 | `pytest tests/m -k "not _pg"` | `40ae99c` | `385 passed, 4 deselected in 80.35s`, exit 0 |
| 3 | tests/g quick: `uploads/test_uploads.py test_relay_sync.py test_relay_sse.py test_relay_recovery.py test_relay_matrix.py jobs/test_jobs.py test_composition.py` | `52d3356` | `176 passed, 2 warnings in 2.43s`, exit 0 |
| 4 | tests/contracts quick: `test_conformance.py test_fixtures.py test_config_and_imports.py` | `52d3356` | `627 passed in 6.08s`, exit 0 |
| 5 | `pytest tests/w -k "not mutant_is_killed"` | `52d3356` | `152 passed, 28 deselected in 163.80s`, exit 0 |
| 6 | `pytest tests/m/test_pilot_media.py -k _pg` (D harness, d4) | `6e4343e` | `4 passed, 12 deselected in 5.02s` |
| 7 | `INFRX_MUTANTS=all pytest tests/m/test_pilot_mutants.py` (15 memory + 6 PostgreSQL mutants, d4) | `078eefe` | `26 passed in 86.78s`, exit 0 |
| 8 | `INFRX_MUTANTS=all pytest tests/m/test_mutants.py` (M1–M4) | `40ae99c` | `242 passed in 655.52s`, exit 0 (`media/*.py` unchanged after `52d3356`) |
| 9 | `INFRX_MUTANTS=all pytest tests/g/test_mutants.py` | `078eefe` | `321 passed in 1049.25s`, exit 0 (at `40ae99c`: `1 failed, 320 passed` - `given_stores_replaced` misdeclared by this lane's `pilot.py` edit, fixed in `1382750`) |
| 10 | `INFRX_MUTANTS=all pytest tests/g/jobs/test_jobs_mutants.py` | `078eefe` | `85 passed in 241.25s`, exit 0 (both re-anchored relay mutants killed) |
| 11 | `INFRX_MUTANTS=all pytest tests/g/uploads/test_uploads_mutants.py` | `40ae99c` | `42 passed in 104.60s`, exit 0 (the router is untouched) |
| 12 | the three contracts mutants MPILOT touches, under a `Runner` aimed at `tests/contracts/test_conformance.py` alone | `52d3356` | `prepare_falls_back_to_any_job killed 2 failed`, `prepare_serves_the_first_attach killed 2 failed`, `upload_used_past_its_window killed 1 failed` |

| 13 | merged head: `pytest tests/m tests/g/uploads/test_uploads.py tests/g/test_relay_sync.py tests/g/test_relay_recovery.py tests/g/jobs/test_jobs.py tests/g/test_composition.py tests/contracts/test_conformance.py tests/contracts/test_config_and_imports.py` + the G/G3/I lists' meta-tests (`-k "not mutant_is_killed and not pristine and not runner_cannot"`), d4 | `8418832` | `963 passed, 12 skipped, 108 deselected, 2 warnings in 69.51s`, exit 0 (the 12 skips are M1-L2's S3 cases: no `INFRX_M_S3_ENDPOINT`) |
| 14 | merged head: `INFRX_MUTANTS=all pytest tests/m/test_pilot_mutants.py` (d4) | `8418832` | `26 passed in 94.75s`, exit 0 |
| 15 | merged head: `INFRX_MUTANTS=all pytest tests/g/test_mutants.py` | `8418832` | `329 passed in 905.07s`, exit 0 |

Not run: `tests/d` beyond what `make api-test` runs (no migration, so no D-focused run);
`make api-mutants` as a whole (its contracts list is refused at this base, row 1); nothing
on the pilot box or with a real object store.

## Limits

1. **A zero-ref attach is not durable.** 0003's `job_media` names refs, so a text job's attach
   has no row and another process answers `attached(job) is None` — the same as "never
   attached". Today preparation runs in the attaching process (E3B's emulation; no
   preparation worker exists), so nothing reads it elsewhere. A preparer in another process
   would never open a text job; the fix is a zero-ref marker (a `0019` — e.g. an
   `attached_at` row per job), not taken here because nothing needs it yet.
2. **`_resume`'s staged-payload witness is still this process's**, so G2's B2 retry window
   after a gateway restart is not lifted: making the payload witness durable too would
   recheck — and could cancel — a running *text* job after a restart (Limit 1). The gate now
   reads the durable attach; behaviour is unchanged in every reachable case.
3. **Upload records are still in process** (`MediaUploads.uploads`, M3's limit): an upload is
   usable only through the gateway process that finalized it; 0010's rows are M's follow-up.
4. **The shared object store is exercised only in memory here.** M1-L2's `S3ObjectStore`
   arrived with the merge (`create_app` builds it from settings); with it a second process
   shares the source objects and could `prepare` as well as resolve. No MPILOT case ran
   against it (no S3 endpoint on this host; M1-L2's own S3 cases skip the same way): the
   `_pg` and `reopened` cases share one `InMemoryObjectStore`, standing in for it.
5. **The collector** (`gc.MediaCollector`) still protects media from this process's
   `by_job`/`prepared_by_job`; it is not composed anywhere yet (G2 request 6). When it is, it
   should read live jobs' refs from `PgAttachments` and `jobs.prepared_refs`. `staged_media`
   rows accumulate (nothing deletes them; `job_media` cascades with the job).
6. **The disk lookup's hash is the source digest**, which is right because profile `v1`'s
   prepared artifact is the source bytes (`_prepared_bytes`); a transcoding profile needs the
   prepared digest beside the file (marked `ponytail:`). It costs one read + sha256 per
   process per entry, synchronously on the caller's loop: 85–95 ms for 64 MiB (page-cached,
   this host, 5 runs), then indexed.
7. **On the worker's read-only mount** an expired file is refused but cannot be removed there;
   the gateway's `sweep`/collector removes it.

## Integration requests

* **E3B phase 3 lane.** Drop `stack.OWNERS["M3-U1"]` and the `video_upload` pending in
  `test_journey.py` (gap 1 is fixed at the route: see the mounted-gateway case). Drop the
  M3-U2 pending / `INFRX_E3B_EMBED_WORKER` requirement for video: `pilotbox.worker()`'s own
  `MediaPreparation(InMemoryObjectStore(), cache=ProcessingCache(PROCESSING_CACHE_DIR))`
  now resolves `local_uri` for media the gateway prepared. The emulated preparation can
  keep waiting on `media.by_job` in the gateway process (or `await media.attached(job)`).
  With `create_app` building the stores from settings, the attach is written to
  `infrx.staged_media`/`infrx.job_media`.
* **Cutover lane.** No settings change. `gateway/pilot.py` gains three lines
  (`adapters_from_env` composes `"attachments": PgAttachments(connect)` above the jobs line,
  so G's `given_stores_replaced` anchor is unchanged; `build_ingress_deps(attachments=None)`
  passes it to `MediaUploads`); merged with `f7d9b03` without a conflict.
* **D.** No migration, no schema or privilege change, so no pgstate rows. New: the first
  production writer of 0003's `staged_media`/`job_media` (service_role DML, 0004).
* **W / I2B-R4** (`python -m infrx.worker`): pass
  `local_uri=MediaPreparation(objects, cache=ProcessingCache(PROCESSING_CACHE_DIR)).local_uri`
  to `VllmEngine`.
* **Coordinator — proposed ruling (next free R95):** *Upload references at use and the durable
  attach.* (a) An `infrx-upload:upl_<id>` source in a chat or job body is resolved at
  admission by the media store, never fetched, by the rule `stage` applies to an upload ref
  (`resolve_owned`): the caller's organization's (another's or unknown: `404 not_found`),
  finalized (else `not_found`, or `invalid_request` when the handle is otherwise indexed), its
  object at the content-addressed `source` key still carrying the finalized digest (else
  `not_found`), its size spent from the request's `MAX_MEDIA_BYTES` budget (`413`). (b) The
  upload window bounds use as well as completion: from `expires_at` on, resolution and staging
  of the upload are `410 upload_expired`, in the fake and every adapter. (c) `attach` is
  durable: it records the job's source refs where another process reads them (on PostgreSQL,
  0003's `staged_media`/`job_media`), and `prepare(job_id)` reads that record; the
  mediastore conformance hook `reopened()` is another process's view of the same store.
* **Coordinator — pre-existing, not MPILOT's:** at `6cb8ebe`,
  `tests/contracts/test_cancel_cause.py::test_dur_settle__before_0018_the_pg_store_refuses_a_cause_it_cannot_record`
  fails (DID NOT RAISE UnsupportedParameter: 0018 now records the cause), so the shared
  CONTRACTS runner refuses its pristine baseline and every `tests/contracts/test_mutants.py`
  mutant it runs reports `broken_runner` (22 in the default subset; reproduced on a clean
  `6cb8ebe` checkout).
* **Coordinator — the `_pg` cases and the PG mutant list use the D harness as `tests/d`
  does:** `INFRX_D_TASK` picks the task's container and port (default `d1`, 55432); they
  skip visibly without Docker. `make api-mutants` now includes `tests/m/test_pilot_mutants.py`
  (its PostgreSQL list runs only with `INFRX_MUTANTS=all` and Docker).

## Incidents (honest record)

* 20:41:03–20:41:08Z: one run of the `_pg` cases without `INFRX_D_TASK` exported made the D
  harness create, use and destroy its default `infrx-d1-postgres` on **55432** (docker
  events: create/start 20:41:03, kill/destroy 20:41:08). No other container existed under
  that name; nothing else was touched. Every later run exported `INFRX_D_TASK=d4`.
* d4 (`infrx-d4-postgres`, 55435) was held by the `infrx-impl` checkout's `make api-mutants`
  gate run (label `…/worktrees/infrx-impl`) from before this lane's PG cases were ready until
  about 21:23Z (the container was gone at the 21:23:09Z poll; its run had ended); the harness would have refused it (ForeignContainer) and nothing of it was used.
* The shared scratchpad's `env.sh` (created 04:52Z by an earlier session, referenced by no
  script found there) was overwritten at 20:42Z by this lane; its previous content is lost.
  This lane's files moved to `scratchpad/mpilot/`.

## Verification log

- 2026-09-23: report written at `078eefe` (MPILOT lane); the cutover merged in at `8418832` (rows 13–15).
