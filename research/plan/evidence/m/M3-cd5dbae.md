# M3: owned uploads, expiry and orphan collection

## Task and status

| | |
|---|---|
| Task | M3 (track M, media). Oracles: MEDIA-SEC, TRACE-TENANT |
| Owner / session | Claude Opus 5.5 (1M context) implementation session, 2026-09-22 |
| Status | **implemented**. Everything ran against fakes, injected collaborators, an in-memory object store and a local filesystem cache. It is **not integrated**: no route calls `create_upload`/`put_upload`/`finalize_upload`, nothing starts `MediaCollector.run`, no durable upload rows exist (D2), no T/J code calls `read_for_reuse`, and there is no real object store. No GPU, cloud, container or network operation was performed. |

## Source

| | |
|---|---|
| Base SHA | `9c87f62` (head of `claude/backend-impl`) |
| Implementation SHA | `cd5dbae` |
| Commits | `c4856f8` item 1 (create/put/finalize plus the additive store port), `eeb9e46` item 2 (upload-to-job references), `2f8f482` item 3 (GC), `97a8fbd` item 4 (consented resolver), `6dcd909` item 5 (conformance), `cd5dbae` the mutation list. This report is the only later commit and contains no code. |
| Branch / worktree | `codex/m3-owned-uploads` in `.claude/worktrees/codex-m3`. Nothing was pushed. |

## What was built

`git diff --stat 9c87f62..cd5dbae`:

```
 apps/infrx-api/infrx/media/consent.py  |  57 ++++
 apps/infrx-api/infrx/media/gc.py       | 168 +++++++++++
 apps/infrx-api/infrx/media/store.py    |  23 ++
 apps/infrx-api/infrx/media/uploads.py  | 249 ++++++++++++++++
 apps/infrx-api/tests/m/mutants.py      | 240 ++++++++++++++-
 apps/infrx-api/tests/m/test_consent.py | 111 +++++++
 apps/infrx-api/tests/m/test_gc.py      | 320 ++++++++++++++++++++
 apps/infrx-api/tests/m/test_mutants.py |  19 +-
 apps/infrx-api/tests/m/test_uploads.py | 530 +++++++++++++++++++++++++++++++++
 9 files changed, 1712 insertions(+), 5 deletions(-)
```

* `infrx/media/uploads.py`: `MediaUploads(MediaPreparation)` is the complete `ports.MediaStore` (M1 staging, M2 preparation and M3 uploads). It adds `create_upload`, `put_upload` (the call G4U makes), `finalize_upload`, a stricter `resolve_owned`, and `stage`/`attach` overrides that record last use.
* `infrx/media/gc.py`: `MediaCollector(store, is_live=…, grace_s=…, max_cache_bytes=…)`. `sweep()` runs one pass and `run(interval_s, sleep=…)` is the schedule hook.
* `infrx/media/consent.py`: `read_for_reuse(store, org_id, handle, consent, *, purpose, captured_at, now) -> (MediaRef, bytes)`.
* `infrx/media/store.py`: additive only. `ObjectStore` gains `describe` (size and content type, S3 HeadObject), `keys(prefix)` (ListObjectsV2) and `delete` (DeleteObject), with in-memory implementations. No existing line changed.

Design points a reviewer should check first:

1. **A finalized upload is stored at M2's source key.** The verified bytes are copied write-once to `media/<org>/<profile>/<digest16>/source`, which is the key `prepare` rebuilds. So an upload is prepared like any other source (this closes M2 limit 10). The destination `uploads/<org>/<handle>` is scratch space; overwriting it after finalize cannot reach a job.
2. **The destination is modelled as overwritable.** The conformance `put_object` hook and the tests' `arrive()` write the destination behind the store's back (an S3 presigned PUT can do this at any time). `put_upload`, the path through our own HTTP adapter, is stricter still: write-once and capped.
3. **Upload window = `PROCESSING_CACHE_TTL_S` (7 d)**, the same as the shared fake and the conformance case. No `UPLOAD_WINDOW_S` exists; see integration request 3.
4. **The GC grace is idle time, not age.** Each object key carries `idle_since`. It is renewed by `stage`, `attach` and `finalize`, and on every pass that finds the object referenced by a live job. An object that no pass has seen gets the full grace from first sight. This means no object-store timestamps are needed, and a failed stage's blob (written, never indexed) is covered by the same rule.

## Requirement coverage

Test ids are pytest functions in `apps/infrx-api/tests/m/`. The mutant that each claim makes killable is named in `tests/m/mutants.py`.

### Item 1: upload create and finalize (`c4856f8`)

| Oracle | Test id | Exact invariant (killing mutants) |
|---|---|---|
| MEDIA-SEC | `test_create_issues_an_opaque_handle_and_a_constrained_destination` | R61(1): `destination_ref == "infrx-upload:" + handle` exactly (no org, no `?`); the handle matches `ids.UPLOAD_HANDLE_RE`; the window is exactly `PROCESSING_CACHE_TTL_S` (`destination_carries_the_org`) |
| MEDIA-SEC | `test_create_refuses_constraints_the_caller_shapes` (21 cases) | cap over `MAX_MEDIA_BYTES`, 0, −1, `"4096"`, 1.5, `True`; a bad declared `bytes`; a string, empty, `None`, number or non-allow-listed accept list; a malformed digest; unknown keys; a non-dict. Each is `invalid_request` and creates no record (`unknown_constraints_ignored`, `upload_cap_unbounded`, `byte_count_accepts_bool`, `declared_size_unvalidated`, `accepted_mime_not_allowlisted`, `empty_accept_list_allowed`, `declared_digest_unvalidated`) |
| MEDIA-SEC | `test_a_handle_source_that_repeats_itself_is_refused` | a repeated handle never takes over an existing upload (`duplicate_handle_accepted`) |
| MEDIA-SEC | `test_put_upload_is_bounded_and_write_once` | oversize bytes are refused before storage; the same bytes again are a no-op; other bytes are `state_conflict` (`put_uncapped`) |
| MEDIA-SEC | `test_a_completed_upload_accepts_no_more_bytes` | no destination write after finalize (`closed_upload_reopens`) |
| MEDIA-SEC | `test_finalize_measures_the_bytes_that_arrived` | size, digest, type and **duration** come from the stored bytes (M2 probe), and the object sits at the content-addressed source key (`declared_type_trusted`, `upload_not_at_the_source_key`) |
| MEDIA-SEC | `test_the_container_wins_over_the_declared_content_type` | a WebM uploaded as `video/mp4` to an mp4-only upload is refused (`declared_type_trusted`) |
| MEDIA-SEC | `test_bytes_the_probe_refuses_abort_the_upload` | a probe refusal is final and no media object is written (`probe_refusal_not_final`) |
| MEDIA-SEC | `test_a_retry_returns_the_same_completed_handle` | idempotent completion: same ref, no new object (`finalize_not_idempotent`) |
| MEDIA-SEC | `test_concurrent_finalizes_verify_and_copy_once` | two racing finalizes give one download and one ref (`finalize_unlocked`) |
| MEDIA-SEC | `test_a_second_finalize_with_other_bytes_is_a_conflict` | a destination overwritten after completion gives a conflict; the completed ref and its object are unchanged (`refinalize_ignores_a_changed_destination`) |
| MEDIA-SEC | `test_an_oversize_object_is_refused_without_being_downloaded` | the HEAD size refuses the object with `gets == 0` (`head_size_unchecked`, `describe_misreports_the_size`) |
| MEDIA-SEC | `test_a_head_that_understates_the_size_is_caught_by_the_bytes` | the bytes, not the HEAD, are the size authority (`byte_size_unchecked`) |
| MEDIA-SEC | `test_a_declared_size_is_verified`, `test_a_declared_digest_is_verified` | declared size and sha256 are checked against the bytes, and a mismatch aborts (`declared_size_unverified`, `declared_digest_unverified`) |
| MEDIA-SEC | `test_an_unaccepted_type_is_refused` | a measured type outside the upload's accept list is refused (`accepted_type_unchecked`) |
| MEDIA-SEC | `test_a_refused_upload_stays_refused` | an aborted upload stays aborted after corrected bytes (`refusal_not_recorded`, `closed_upload_reopens`) |
| MEDIA-SEC | `test_an_expired_window_is_upload_expired_and_stays_closed`, `test_the_window_is_open_until_it_closes` | R22: `upload_expired` at `expires_at` exactly; afterwards the upload takes neither a retry nor bytes (`upload_window_never_closes`, `upload_window_excludes_its_end`) |
| MEDIA-SEC | `test_another_org_cannot_put_finalize_or_resolve`, `test_an_unknown_handle_is_not_found` (4) | another tenant's handle, an unknown handle, a path or a non-string gives `not_found` on every operation (`upload_owner_unchecked`) |
| MEDIA-SEC | `test_nothing_uploaded_yet_is_not_a_refusal` | finalizing with nothing uploaded is a 400 that leaves the upload open |
| MEDIA-SEC | `test_finalizing_never_replaces_what_the_handle_already_names` | a handle that already names other content in the tenant keeps it (`finalize_replaces_named_content`) |
| MEDIA-SEC | `test_a_probe_that_times_out_leaves_the_upload_open` | a platform-side deadline (R21) does not abort the customer's upload |

### Item 2: upload-to-job references (`eeb9e46`)

| Oracle | Test id | Exact invariant (killing mutants) |
|---|---|---|
| MEDIA-SEC | `test_stage_uses_the_finalized_record_not_the_callers_copy` | a request's upload ref with forged bytes, duration, mime and storage_ref stages as the store's record |
| MEDIA-SEC | `test_stage_refuses_an_unfinalized_upload` | an unfinalized upload is refused (400/404) and no payload is written |
| MEDIA-SEC | `test_resolve_refuses_an_upload_that_is_not_finalized_even_if_its_handle_is_indexed` | a squatted handle is not a finalized upload (`unfinalized_upload_resolves`) |
| MEDIA-SEC | `test_stage_refuses_another_orgs_upload` | a cross-org ref gives `not_found`, whether it names the owner or claims the handle as its own |
| MEDIA-SEC | `test_an_upload_whose_object_changed_is_not_staged` (replaced, deleted) | the object behind a finalized upload is re-checked at use (`changed_upload_object_resolves`) |
| MEDIA-PARITY | `test_an_uploaded_clip_is_prepared_like_any_source` | M2 limit 10 closed: stage, attach and prepare an upload, get the prepared artifact, and `local_uri` opens the same bytes under the cache root |

`stage` accepts upload refs only through the existing `self.refs` index plus the upload's state (no line of `store.py` was rewritten). F2R item 4, which refuses caller-made url/inline refs that were never materialized, is lane A's and is **not** done here.

### Item 3: collection (`2f8f482`, tightened in `cd5dbae`)

| Oracle | Test id | Exact invariant (killing mutants) |
|---|---|---|
| acceptance | `test_a_live_jobs_input_is_never_collected` | a live job's source **and** prepared artifact survive 3 × 2·grace, including with `grace_s=0` (`liveness_ignored`, `prepared_artifact_unprotected`, `protection_is_only_the_grace`, `keys_ignore_the_prefix`) |
| acceptance | `test_input_is_collected_after_the_job_ends_and_the_grace_passes` | after a job that stayed live longer than the grace ends, its input is kept for one full grace and then removed. Afterwards the job's attachments, the ref and the upload record are gone (`idle_not_renewed_while_live`, `dead_job_attachments_kept`, `collected_upload_still_finalizes`) |
| acceptance | `test_a_lookup_that_fails_collects_nothing` | fail closed: a liveness lookup that raises aborts the pass before any deletion or upload-state change (`lookup_failure_fails_open`) |
| acceptance | `test_a_failed_stage_blob_is_eventually_removed` | an unindexed blob survives until grace−1 and is removed at grace (`grace_ignored`, `orphans_never_collected`) |
| MEDIA-SEC | `test_media_staged_for_a_request_never_admitted_is_removed` | never-admitted staging is collected, and a collected materialized ref no longer resolves (`collected_ref_still_resolves`) |
| MEDIA-SEC | `test_recent_use_renews_the_grace`, `test_attach_is_a_use`, `test_a_finalize_is_a_use` | use renews the grace (`stage_is_not_a_use`, `attach_is_not_a_use`, `finalize_is_not_a_use`) |
| MEDIA-SEC | `test_upload_destinations_live_exactly_as_long_as_the_upload_is_open` | open destinations stay; finalized, refused and record-less destinations are removed; the finalized copy stays (`open_destination_collected`, `closed_destination_kept`) |
| MEDIA-SEC | `test_a_lapsed_upload_window_is_closed_and_its_bytes_removed` | a pass expires lapsed windows (`lapsed_window_left_open`) |
| MEDIA-SEC | `test_a_refused_upload_record_is_dropped_after_the_grace` | a refused record is kept for exactly the grace after expiry and then dropped (`refused_record_kept_forever`, `refused_record_dropped_early`) |
| MEDIA-SEC | `test_the_sweep_runs_the_processing_cache_expiry` | `ProcessingCache.sweep` runs on every pass and the file is deleted (`cache_expiry_not_run`) |
| MEDIA-SEC | `test_the_cache_cap_evicts_idle_entries_and_never_a_live_jobs` | M2 limit 8: the byte cap evicts the oldest idle entry first and never a live job's entry, even when the cap is unreachable (`cache_cap_ignored`, `cache_cap_evicts_live_entries`, `cache_cap_evicts_newest_first`) |
| MEDIA-SEC | `test_stray_cache_files_are_removed_after_the_grace` | M2 limit 7 (partly): files no index entry names (another process's, or a `.part`) are removed after the grace, and indexed files never are (`stray_files_removed_at_once`, `indexed_files_treated_as_stray`) |
| ops | `test_run_sweeps_on_a_schedule_and_survives_a_failed_pass` | the schedule hook sleeps its interval, logs a failed pass, keeps going and then collects (`failed_pass_stops_the_schedule`) |

### Item 4: consented reuse (`97a8fbd`)

| Oracle | Test id | Exact invariant (killing mutants) |
|---|---|---|
| TRACE-TENANT | `test_consented_reuse_returns_the_verified_bytes` (trace, judge) | a current, consented read returns the ref and its bytes |
| TRACE-TENANT | `test_another_orgs_consent_never_widens_the_tenant` | another org's consent, or another org asking for this org's handle, gives `not_found` (`consent_org_unchecked`) |
| TRACE-TENANT | `test_trace_reuse_needs_current_full_trace_consent` (minimal, off, revoked, not yet effective) | `consent_missing` in each case (`trace_mode_unchecked`, `trace_consent_currency_unchecked`) |
| TRACE-TENANT | `test_trace_consent_is_not_evaluation_consent` | trace consent does not open judge reuse (`judge_consent_unchecked`) |
| TRACE-TENANT | `test_evaluation_consent_is_not_retroactive` | R56: content captured before `effective_at` is not judged (`judge_window_unchecked`) |
| TRACE-TENANT | `test_content_expires_logically_at_its_retention` | `result_expired` at exactly `content_retention_days` after capture (`retention_unchecked`, `retention_excludes_its_end`) |
| TRACE-TENANT | `test_the_bytes_returned_are_the_refs_bytes` (replaced, deleted) | the bytes handed to T or J hash to the ref's digest (`reused_bytes_unverified`) |
| TRACE-TENANT | `test_an_unknown_purpose_is_refused` | only `trace` and `judge` are accepted (`unknown_purpose_allowed`) |

The consent record is the existing `records.ConsentSnapshot`, and the judge half reuses `judge.sampling.check_consent`/`within_consent_window`, so no new consent shape was introduced. The resolver takes no "as of" time for authorization (R74). `captured_at` only bounds the R56 window and the retention.

### Item 5: conformance (`6dcd909`)

`test_the_exported_conformance_suite_runs_every_upload_case` asserts the partition. Output quoted verbatim from `uv run --frozen pytest -q tests/m/test_uploads.py -k conformance -s`:

```
mediastore conformance against infrx.media.uploads.MediaUploads:
  blocked: not_found: not_found: the staged object for media upl_conformancefixture0000000000000000001 is gone media_parity__staging_is_content_addressed_and_tenant_namespaced
  pass                               media_sec__a_foreign_media_reference_is_not_staged
  pass                               media_sec__a_partial_request_stages_nothing
  pass                               media_sec__a_refused_upload_stays_refused
  pass                               media_sec__an_expired_upload_window_says_so
  pass                               media_sec__an_upload_is_owned_verified_and_immutable
  pass                               media_sec__another_org_cannot_resolve_or_finalize
  pass                               media_sec__oversize_and_unsupported_uploads_are_refused
  pass                               media_sec__staging_never_replaces_an_existing_object
```

**Result: 8 pass and 0 skip.** The six cases M2 skipped for want of `create_upload` now run and pass. `media_parity__staging_is_content_addressed_and_tenant_namespaced` **stays pinned as blocked** on F2R item 4 (lane A) with M2's exact refusal. `builders.media()` names objects nobody materialized, so it stays blocked until F2R-A merges. The conformance adapter is `MediaUploads` with M1's `facts` (the declared type and no duration). The cases upload labelled non-container bytes (`b"0123456789"` as `video/mp4`), which M2's probe would refuse for a reason the case is not testing. That finalize consults the probe is proven separately by `test_the_container_wins_over_the_declared_content_type` and `test_finalize_measures_the_bytes_that_arrived`. M2's own partition test in `test_prepare.py`, against `MediaPreparation` (still 2 pass / 6 skip / 1 blocked), is unchanged and still passes.

## Environment

| | |
|---|---|
| Host | `Linux 7.0.0-1010-aws x86_64`, local development worktree |
| Python | `3.12.3 (main, Aug 31 2026, 10:18:26) [GCC 13.3.0]`, `uv 0.11.8`, venv from `make api-env` (exit 0) |
| Pinned libraries | `pytest 8.4.2`, `httpx 0.28.1`, `pydantic 2.13.5` |
| Node / Docker | not used: no console file was touched and no container was created (tests/d uses D's own harness; see below) |
| Clocks | injected: `FakeClock` (uploads, GC) and a float clock (processing cache). No sleep anywhere; `run()` is driven by an injected `sleep`. |

## Commands and results

All commands run from `apps/infrx-api` on 2026-09-22 UTC. No seed is involved.

| # | Command | Exit | Output tail |
|---|---|---|---|
| 1 | `make api-env` (repo root) | 0 | `+ zstandard==0.25.0` |
| 2 | `uv run --frozen pytest -q tests/m/test_uploads.py tests/m/test_gc.py tests/m/test_consent.py` (18:38Z) | 0 | `85 passed in 0.40s` |
| 3 | `uv run --frozen pytest -q tests/m --deselect tests/m/test_mutants.py::test_mutant_is_killed` | 0 | `314 passed, 17 deselected in 6.74s` (the 17 subset mutants are covered by row 9) |
| 4 | `uv run --frozen pytest -q tests/contracts` | 0 | `1006 passed in 37.66s` |
| 5 | `uv run --frozen pytest -q tests/w tests/g` (18:38:23Z) | 0 | `336 passed, 2 warnings in 37.96s` |
| 6 | legacy-first: `pytest -q tests/test_app_factory.py tests/test_gateway_auth.py tests/test_inflight.py tests/test_media.py tests/m --deselect …::test_mutant_is_killed` | 0 | `343 passed, 17 deselected, 2 warnings in 8.24s` |
| 7 | track-first: the same files with `tests/m` first | 0 | `343 passed, 17 deselected, 2 warnings in 10.28s` |
| 8 | `uv run --frozen pytest -q --ignore=tests/d --deselect tests/m/test_mutants.py::test_mutant_is_killed` (18:39:21Z) | 0 | `2205 passed, 17 deselected, 2 warnings in 229.36s (0:03:49)` |
| 9 | `INFRX_MUTANTS=all uv run --frozen pytest -q tests/m/test_mutants.py` (run detached, log below) | 0 | `212 passed in 286.30s (0:04:46)` |
| 10 | `uv run --frozen pytest -q tests/d` | 1 | `69 failed, 7 passed in 6.17s`, every failure `HarnessBusy: another run holds /tmp/infrx-d1-postgres-55432.lock (pid 536740 checkout …/worktrees/codex-d1r)`. This is not an M3 failure and is **not a pass**. A retry loop was left waiting for the lock; see "tests/d" below. |

Mutation list composition, quoted from `python -c "from tests.m import mutants; …"`:

```
total 209
media/consent.py 9
media/fetch.py 42
media/gc.py 22
media/prepare.py 38
media/probe.py 23
media/store.py 36
media/uploads.py 32
media/video.py 7
```

Row 9's 212 is 209 mutants plus the three meta-tests. There are **no survivors, no misdeclared anchors and no broken-runner verdicts**. The 65 M3 mutants (uploads 32, gc 22, consent 9, store 2) were also run individually before the full run: 65 `killed`, 0 other. All anchors match the current source. `upload_cap_unbounded`'s anchor was first ambiguous (the default and the bound shared text), and that was fixed before the run. `test_the_mutation_list_covers_the_owned_modules` now asserts an M3 floor of 50 on its own. The default subset gained one mutant per new file plus the two acceptance pins (`lookup_failure_fails_open`, `refusal_not_recorded`). **No kill in the M3 list relies on a crash.** Every M3 mutant fails a named assertion.

**`make check` / `make api-test` / `make api-mutants`: not run as written.** They collect `tests/d`, which could not get the shared Postgres harness (row 10). Rows 8 and 9 together are `make api-test` minus `tests/d` plus this track's share of `make api-mutants`. The Makefile already lists `tests/m/test_mutants.py`, so no Makefile change is needed. Console, bench and integration targets were not run because nothing under `apps/app`, `models/` or `tests/integration` was touched.

### tests/d

M3 changes no file that `tests/d` imports. The retry loop (`$SCRATCH/d.sh`, 60 × 30 s) re-runs `tests/d` once D1R releases the lock. Its outcome is **not recorded in this report**; see the handback message.

## Failure drill

| Drill | Injection point | State after | Proof |
|---|---|---|---|
| destination overwritten after finalize | `seed()` on `uploads/<org>/<handle>` | re-finalize returns `state_conflict`; the ref, the index and the source object are unchanged | `test_a_second_finalize_with_other_bytes_is_a_conflict` |
| two concurrent finalizes | `asyncio.gather` with a real probe thread in between | one download (`gets == 1`), one ref | `test_concurrent_finalizes_verify_and_copy_once` |
| HEAD that lies | `describe()` reports 1 byte | the bytes check refuses and the upload is aborted | `test_a_head_that_understates_the_size_is_caught_by_the_bytes` |
| probe hang | async probe awaiting forever, `PROBE_TIMEOUT_S=0.01` | `deadline_exceeded`; the upload stays `created` and finalizes on retry | `test_a_probe_that_times_out_leaves_the_upload_open` |
| source replaced or deleted behind a finalized upload | `seed()` or `delete()` on the source key | `stage` and `resolve_owned` return `not_found`; `read_for_reuse` returns `not_found` | `test_an_upload_whose_object_changed_is_not_staged`, `test_the_bytes_returned_are_the_refs_bytes` |
| job store unreachable during GC | `is_live` raises | the pass raises before any deletion; objects and upload states are unchanged; `run()` logs it and continues | `test_a_lookup_that_fails_collects_nothing`, `test_run_sweeps_on_a_schedule_and_survives_a_failed_pass` |
| failed-stage blob | an unindexed object under `media/` | kept until grace−1, removed at grace | `test_a_failed_stage_blob_is_eventually_removed` |
| leftover cache file after a restart | a `.part` file under the cache root, absent from the index | kept for the grace, then removed; indexed files untouched | `test_stray_cache_files_are_removed_after_the_grace` |

## Artifacts

No binary artifact was produced. The clips are `tests/m/support.mp4`/`webm` (generated in-process). The cache writes only under pytest's `tmp_path`. The mutant logs are in the session scratchpad and are not committed. No credential, customer content or signed URL appears anywhere.

## Changes

Owned paths only: `infrx/media/uploads.py` (new), `infrx/media/gc.py` (new), `infrx/media/consent.py` (new), `infrx/media/store.py` (additive: three port methods and their in-memory implementations), `tests/m/{test_uploads,test_gc,test_consent}.py` (new), `tests/m/{mutants,test_mutants}.py`, and this report. No contract, config, composition root, SQL, route, Makefile, lockfile or console file was touched. **Migration / rollback:** none (no schema). Feature-disabling means constructing `MediaPreparation` instead of `MediaUploads` and not starting the collector. The uploads then answer `NotImplementedError` again, and orphans accumulate as they did before this task.

## Limits

1. **All upload and GC state is in process** (`uploads`, `idle_since`, the refs index). A restart forgets upload records and idle times: the GC then deletes every `uploads/` destination (no record) and restarts every media object's grace from first sight. More than one process sharing an object store would delete each other's open destinations. **D2 rows are required before more than one process** (integration request 1).
2. **Delete/restage race.** A `stage` that reuses an object idle for the whole grace, in the milliseconds while the collector's `delete` is in flight, can index a ref whose object then vanishes. `prepare` then answers `not_found` (M2's HEAD check), so the job fails loudly rather than running on something else. The durable fix is a conditional delete against D2's `last_used_at`. Owner: D2/M.
3. **The processing-cache index is still in process** (M2 limit 7). What is addressed: stray files from another process or a crash are removed after the grace. What is not: the index is not shared, so a second worker re-materializes. Owner: I/D after the pilot.
4. **Conformance ran with M1's `facts`**, not the probe (explained under item 5). The probe path is proven by the M suite only.
5. **No real object store.** `describe`/`keys`/`delete` are new port operations. The S3 adapter needs HeadObject (ContentLength and ContentType), ListObjectsV2 (paginated) and DeleteObject. `keys()` returns a full list, and at pilot scale a paginated iterator is the upgrade. Pending, same as M1 limit 2.
6. **`gc.py` calls `ProcessingCache._remove`** (the private file-plus-index removal in `prepare.py`, a file M3 does not own) for capacity eviction. Adding a public `evict(key)` in `prepare.py` would remove this.
7. **The upload window is `PROCESSING_CACHE_TTL_S` (7 d)**, as the fake and the conformance case expect. A shorter window needs a setting (integration request 3).
8. **`payloads/` (staged request bodies) are not collected here.** Their retention follows the job and result lifecycle (JobStore/D), not media.
9. **F2R item 4 is not done** (lane A). `stage` still accepts caller-made url/inline refs that were never materialized, and the pinned conformance case is its symptom.
10. **The HTTP body bound is G4U's.** `put_upload` checks `len(data)` against the cap, but the route must stop reading at `max_bytes + 1` before calling it, or a hostile body is buffered first.
11. **`read_for_reuse` returns the whole object in memory** (≤ 64 MiB). A streaming variant is left for T/J if they need one.
12. **TRACE-TENANT is covered only for the media half.** Query-parameter override and trace-row tenancy belong to C/T.
13. **`tests/d` did not run in this session** (row 10): not a pass.

## Handback

**Next unblocked:** G4U (mount the upload routes on the call surface below); D2 (upload rows); T/J wiring of `read_for_reuse`. M's remaining media work is gated on F2R-A (item 4 and the pinned case) and on the first GPU/ffmpeg host (M2 limits 2 and 5).

**integration_requests** (coordinator-owned paths; none were changed here):

1. **D2: durable upload rows and media last-use.** Suggested table `media_uploads`:
   - `handle text primary key check (handle ~ '^upl_[A-Za-z0-9_-]{22,64}$')`
   - `org_id uuid not null` (RLS by org; every lookup is `(org_id, handle)`)
   - `state upload_state not null` (enum `created|finalized|aborted|expired`)
   - `max_bytes bigint not null check (max_bytes between 1 and MAX_MEDIA_BYTES)`, `declared_bytes bigint null check (declared_bytes >= 1)`, `declared_digest text null check (declared_digest ~ '^sha256:[0-9a-f]{64}$')`, `accepted_mime text[] not null check (cardinality(accepted_mime) > 0)`
   - `created_at timestamptz not null default now()`, `expires_at timestamptz not null`, `finalized_at timestamptz null`
   - finalized facts: `digest text null`, `bytes bigint null`, `mime text null`, `duration_s double precision null`, `storage_ref text null`, `profile_version text null`, all non-null iff `state='finalized'` (check)
   - `aborted_reason text null` (operator only)
   - index `(state, expires_at)` for the GC.

   Finalize-once becomes `UPDATE media_uploads SET state='finalized', … WHERE org_id=$1 AND handle=$2 AND state='created' AND now() < expires_at RETURNING *`, which replaces the in-process lock. A second table `media_objects(storage_ref text primary key, org_id uuid not null, last_used_at timestamptz not null)` replaces `idle_since`, with the collector's delete made conditional on `last_used_at` (limit 2).
2. **G4U: call surface** (`store` is the app's `MediaUploads`; `auth.org_id` comes from the key, never the body):
   - `POST /v1/uploads` → `await store.create_upload(auth.org_id, {"max_bytes"?, "bytes", "digest": "sha256:<hex>", "accepted_mime": [content_type]})` → returns the `wire.UploadCreated` dump (`upload_handle`, `destination_ref="infrx-upload:upl_…"`, `max_bytes`, `accepted_mime`, `state`, `expires_at`).
   - `PUT` on the destination → read the body bounded at `max_bytes + 1`, then `await store.put_upload(auth.org_id, handle, body, content_type)`.
   - `POST /v1/uploads/{handle}/complete` → `ref = await store.finalize_upload(auth.org_id, handle)` → `wire.UploadCompleted.of(handle, UploadState.finalized, ref)` (never the ref itself: `storage_ref` must not leave the server, R47).

   Errors map straight to the envelope: `invalid_request`, `unsupported_media`, `request_too_large`, `not_found`, `state_conflict`, `upload_expired`, `deadline_exceeded`. **Shape mismatch to resolve:** `models/marlin2b/bench.py` sends `{purpose, filename, bytes, sha256, content_type}`, reads `created["handle"]` and `created["upload"]["url"/"method"/"headers"]`, and expects `handle` in the completion body. The contract DTO says `upload_handle`/`destination_ref`. G4U or F must pick one; the handle grammar is identical (`upl_` + 22..64).
3. **Composition root and config (F2R/coordinator):** construct `infrx.media.uploads.MediaUploads(objects, cache=…, limits=settings.pilot, fetcher=…, job_org=…)` in place of `MediaPreparation`, and start `MediaCollector(store, is_live=<JobStore non-terminal read>, max_cache_bytes=settings.processing_cache_max_bytes).run(settings.media_gc_interval_s)` in the lifespan, cancelled on shutdown. New settings: `MEDIA_GC_INTERVAL_S` (suggest 300), `PROCESSING_CACHE_MAX_BYTES` (host NVMe sizing, I2), and optionally `UPLOAD_WINDOW_S` (today 7 d = `PROCESSING_CACHE_TTL_S`).
4. **JobStore (D2/F):** an async `is_live(job_id) -> bool` (non-terminal; unknown gives False) for the collector.
5. **Contracts fake (coordinator):** `FakeMediaStore.create_upload` returns `destination_ref=f"infrx-upload:{org_id}:{handle}"`, the org-qualified spelling R61(1) forbids. The conformance case only checks the prefix, so the fake passes its own suite while violating the ruling. Suggest `f"infrx-upload:{handle}"` plus an exact-form assertion in `media_sec__an_upload_is_owned_verified_and_immutable`.
6. **T/J:** capture and judge submission read media only through `infrx.media.consent.read_for_reuse(store, org_id, handle, <current ConsentSnapshot>, purpose=…, captured_at=…, now=…)`.

## Verification log

- 2026-09-22: Authored from the runs above at implementation SHA `cd5dbae`. Every count and quoted line is command output. No live, paid, cloud, container or GPU operation was performed, and no network request was made from any test.
