# M1-L2 — the durable media object store: `S3ObjectStore`

## Task and status

| | |
|---|---|
| Task | M1-L2 (track M, lane of the Marlin backend program): the S3-backed `ObjectStore` the pilot needs to start. Closes M1 limit 2 / M3 limit 5 ("no real object store") |
| Owner / session | Claude Opus 5.5 (1M context) implementation session, 2026-09-23 |
| Status | **implemented**; conformance-proven against a real S3-compatible store (MinIO, the E2 stack's `s3` service). **Not run against AWS S3 or on the pilot box**: the instance-role credential path, the bucket, its IAM policy and its region are the coordinator's (integration request 1) |

## Source

| | |
|---|---|
| Base SHA | `43fe900` (`codex/m1l2-object-store` as assigned = the cutover lane's item 2 on the integration head + D5) |
| Implementation SHA | `eae07a9` (the last commit touching code or tests; this report follows it) |
| Branch / worktree | `codex/m1l2-object-store` in `.claude/worktrees/codex-objstore`. Nothing pushed. `origin/codex/cutover-mount` is merged on top after this report (its head at that time is named in the merge commit) |
| Integration target | `claude/backend-impl` (coordinator only) |

| Item | Commit | What |
|---|---|---|
| 1 adapter + settings | `7760bcf` | `infrx/media/s3.py`; `S3_MEDIA_PREFIX`/`S3_ENDPOINT_URL` in `config.DeploymentSettings` with startup validation; preflight `TUNABLE`; mutant list `tests/m/s3_mutants.py` joins `make api-mutants` |
| 2 conformance | `847c207` | `tests/m/test_s3.py`: the port's cases on `InMemoryObjectStore` **and** `S3ObjectStore`; 13 S3 mutants |
| 3 wiring | `eae07a9` | `pilot.object_store` builds and probes the store from settings; preflight asks the bucket (pilot `apply`) and refuses an image without botocore; Dockerfile adds the `traces` extra; G's test and mutant follow; 08 §5.1 rows; 9 mutants |
| 4 evidence | this file | |

`git diff --stat 43fe900..eae07a9`: 13 files, +821/−23 (`infrx/media/s3.py` 126 lines, `tests/m/test_s3.py` 400, `tests/m/s3_mutants.py` 156; `pilot.py` +27/−, `preflight.py` +30, `config.py` +18).

## What was built

`S3ObjectStore(client, bucket, prefix)` answers the six operations of `media.store.ObjectStore` exactly as `InMemoryObjectStore` does, one S3 call each:

| Port operation | S3 call | Semantics (same as the reference) |
|---|---|---|
| `put_if_absent(key, data, type)` | PutObject `If-None-Match: *`, `x-amz-checksum-sha256` | True if written; **412 → False** (write-once is the server's atomic answer, never a HEAD then a PUT) |
| `head(key)` | HeadObject `ChecksumMode=ENABLED` | the SHA-256 **the server verified on write**, as `sha256:<hex>`; 404 → None; an object stored without our checksum → `"sha256:"` (present, equal to no digest) |
| `get(key)` | GetObject | the bytes; `NoSuchKey` → None |
| `describe(key)` | HeadObject | `(ContentLength, ContentType)`; 404 → None |
| `keys(prefix)` | ListObjectsV2 (paginator) | sorted keys under `prefix`, the store's own prefix stripped (so a listing names keys the store takes) |
| `delete(key)` | DeleteObject | absent is not an error |

Every key is `S3_MEDIA_PREFIX + key`. A 404/`NoSuchKey` is absent only for a one-object read (`head`, `get`, `describe`) and for `delete` (fix round A3: `put_if_absent` and `keys` have no "absent" answer, so a 404 there is an error). Any other failure — 403, NoSuchBucket, throttling, 5xx, a body that breaks mid-read, a transport error, a concurrent conditional write's 409 — raises `DependencyUnavailable` (503, `Retry-After`), which the relay and the upload routes already render. The store never says "not there" or "not written" when it could not ask. `head` reports only a whole-object SHA-256 (fix round A7). botocore blocks, so every call runs in `asyncio.to_thread` (botocore clients are thread-safe), two attempts in all (fix round A4); it is imported on first use, so `import infrx` still loads no track dependency.

**Configuration.** `S3_MEDIA_BUCKET` (contract data, 08 §5) names the bucket; `S3_MEDIA_PREFIX` (default `infrx/`, path segments each ending in `/`) and `S3_ENDPOINT_URL` (unset = AWS S3; `http(s)://host[:port]` only, addressed path-style — MinIO in tests) are deployment settings (08 §5.1), validated by `validate_deployment` in every mode. Credentials and region are never settings: botocore's chain reads them (environment, then the instance role — the box's gateway runs `--network host`, so IMDSv2 answers at hop limit 1).

**Startup (fail closed).** `pilot.object_store` — in every mode, as before — refuses an unset bucket naming `S3_MEDIA_BUCKET`; set, it connects and calls HeadBucket before anything else is built. Any failure (unreachable, 403, 404, no credentials, botocore missing) is `RuntimeMisconfigured` naming the setting and the S3 error code or exception class — never the bucket or the endpoint (a path-style URL in botocore's message carries both). The in-memory store is never a fallback.

**Preflight.** The runtime probe runs in the image with `--network none`, so it cannot ask S3; it now refuses a pilot image without botocore. `apply` in pilot mode asks `aws s3api head-bucket --bucket <S3_MEDIA_BUCKET> --region <region> [--endpoint-url …]` from the host with the credentials the units use, before the file is replaced: no bucket, or a refused HeadBucket, is an install refusal naming the setting and the error code. The Dockerfile's sync adds `--extra traces` so botocore is in the runtime image.

## Dependency decision

**botocore**, used directly. `boto3`/`botocore` 1.43.98 are pinned in `uv.lock` only through the `traces` extra; boto3 is **not** a runtime dependency (not core, and the runtime image installed only `state` + `scheduling`). botocore is pinned, so no new dependency and no lock change; botocore rather than boto3 because the client API is the same and boto3 adds a layer the adapter does not use. Not a stdlib SigV4 client: the box authenticates with its instance role (IMDSv2 token, rotating session credentials), and signing plus credential refresh is a security path botocore already implements and the lock already pins. The one deployment change is the image's `--extra traces` (clickhouse-connect comes along, unimported until T deploys); a dedicated `media = ["botocore"]` extra would need a lock regeneration, which is the coordinator's (integration request 4).

## Conformance cases and mutants

`tests/m/test_s3.py`. The fixtures `objects`/`pair` parametrize every port case over `memory` (the reference `InMemoryObjectStore`, must pass unchanged) and `s3` (`S3ObjectStore` on a real S3-compatible store). The S3 half reads `INFRX_M_S3_ENDPOINT` and `INFRX_M_S3_BUCKET` and otherwise **skips, typed with its owner**. Each S3 case writes under its own `test/m1l2/<uuid>/` prefix (08 §8's object prefix), **emptied after the case** (fix round A1; the cleanup refuses any prefix outside `test/m1l2/`). **Credentials (fix round A1):** with `INFRX_M_S3_LOCAL_CREDS=1` (MinIO) the E2 literals are the only credentials botocore can find and the bucket (default `infrx-m1l2`) is created; without it botocore's own chain is untouched — the environment, then the instance role — and no bucket is created, so the same file runs on the box against AWS (Integration request 1). The no-Docker cases always use local literals and dead local endpoints or botocore's Stubber, and never reach AWS.

| Case | Stores | Oracle |
|---|---|---|
| `test_a_round_trip_returns_the_bytes_their_size_type_and_digest` | both | 1 B and a `MAX_MEDIA_BYTES` (64 MiB) body: bytes, `(size, type)`, digest |
| `test_put_is_write_once` | both | same bytes again / other bytes: False, original bytes, type and digest stay |
| `test_a_missing_key_is_absent_to_every_operation` | both | head/get/describe None, listing empty, delete not an error |
| `test_delete_removes_exactly_one_object` | both | the sibling survives; a second delete is not an error |
| `test_a_listing_is_exactly_its_prefix_and_names_keys_the_store_takes` | both | `media/`, `uploads/`, `""`; every listed key round-trips |
| `test_two_stores_never_see_each_others_objects` | both (s3: two sibling prefixes of one bucket) | no read, list, block or delete across prefixes; the raw object is under the prefix |
| `test_an_upload_at_its_byte_cap_finalizes_and_one_byte_over_is_refused_unread` | both | size bound: exactly `max_bytes` finalizes; one byte over is `request_too_large` with **no download** |
| `test_the_collector_keeps_a_live_jobs_media_and_collects_the_rest` | both | M3's sweep through the store: a closed destination goes at once, an orphan after the grace, a live job's source never |
| `test_a_denied_store_is_an_error_never_absence` | s3 | a 403 on each of the six operations is `dependency_unavailable` |
| `test_an_object_stored_without_our_checksum_is_present_and_matches_no_digest` | s3 | a foreign object is present, matches no digest, is not overwritten |
| `test_an_unreachable_store_is_an_error_never_absence` | no Docker | a transport failure is `dependency_unavailable` |
| `test_the_store_settings_refuse_a_value_that_cannot_place_an_object` / `…_are_read_from_the_environment` | no Docker | prefix and endpoint shapes; names, never values |
| `test_create_app_from_settings_stages_into_the_configured_bucket` | s3 | `create_app` (store not injected) composes `S3ObjectStore` on the bucket and prefix; an upload's bytes land there |
| `test_create_app_refuses_to_start_when_the_bucket_does_not_answer` [unset, unreachable, missing(s3)] | | `RuntimeMisconfigured` naming `S3_MEDIA_BUCKET`, not the bucket or endpoint |
| `test_a_pilot_install_asks_the_bucket_before_replacing_the_file`, `test_a_pilot_install_without_a_bucket_is_refused`, `test_the_pilot_runtime_probe_refuses_an_image_without_botocore` | no Docker | preflight |

Mutant list `tests/m/s3_mutants.py` (shared runner, R83 shape; a layout that also copies `deploy/`; `Runner.env` passes only the two `INFRX_M_S3_*` names). **27 mutants, all killed** (`INFRX_MUTANTS=all`, endpoint exported). The brief's six named mutants are `s3_key_without_the_prefix` (wrong prefix), `s3_put_not_conditional` (non-idempotent put), `s3_any_error_is_absence` (missing-ref error swallowed), `s3_probe_skipped`, `s3_size_over_by_one`/`s3_size_under_by_one`, and `s3_listing_ignores_its_prefix` (the sweep deletes live refs).

| Commit | Mutant | File | Invariant | Killed by | Needs |
|---|---|---|---|---|---|
| `7760bcf` | `s3_prefix_unchecked` | `config.py` | S3_MEDIA_PREFIX is path segments ending in / or startup refuses | `test_the_store_settings_refuse_a_value_that_cannot_place_an_object` | – |
| `7760bcf` | `s3_prefix_prefix_matched` | `config.py` | the whole prefix is checked, not its first segment | same | – |
| `7760bcf` | `s3_endpoint_unchecked` | `config.py` | S3_ENDPOINT_URL is a scheme and an authority or startup refuses | same | – |
| `7760bcf` | `s3_endpoint_prefix_matched` | `config.py` | an endpoint with a path, query or credentials refuses | same | – |
| `7760bcf` | `s3_transport_failure_is_absence` | `s3.py` | a store that does not answer is an error, never absent | `test_an_unreachable_store_is_an_error_never_absence` | – |
| `847c207` | `s3_key_without_the_prefix` | `s3.py` | every object lives under S3_MEDIA_PREFIX (4 occurrences, one edit) | `test_two_stores_never_see_each_others_objects` | S3 |
| `847c207` | `s3_put_not_conditional` | `s3.py` | put_if_absent is write-once (If-None-Match: *) | `test_put_is_write_once`, `…without_our_checksum…` | S3 |
| `847c207` | `s3_any_error_is_absence` | `s3.py` | only a 404 is absent; a refused call is an error | `test_a_denied_store_is_an_error_never_absence` (+ write-once, no-checksum) | S3 |
| `847c207` | `s3_absence_is_an_error` | `s3.py` | a missing object is None, not a failure | `test_a_missing_key_is_absent_to_every_operation`, isolation | S3 |
| `847c207` | `s3_precondition_is_an_error` | `s3.py` | an occupied key is False, not a failure | write-once, no-checksum | S3 |
| `847c207` | `s3_digest_not_asked_for` | `s3.py` | head answers the SHA-256 the server measured | round trip, write-once, collector | S3 |
| `847c207` | `s3_checksum_not_sent` | `s3.py` | the PutObject carries the SHA-256 the server checks | round trip, write-once, collector | S3 |
| `847c207` | `s3_unchecksummed_object_is_absent` | `s3.py` | an object without our checksum is present | `…without_our_checksum…` | S3 |
| `847c207` | `s3_size_over_by_one` | `s3.py` | describe reports the stored size, not one more | `test_an_upload_at_its_byte_cap_finalizes_and_one_byte_over_is_refused_unread` (+ round trip, write-once) | S3 |
| `847c207` | `s3_size_under_by_one` | `s3.py` | describe reports the stored size, not one less | same (the one-over upload is downloaded: `gets` moves) | S3 |
| `847c207` | `s3_content_type_dropped` | `s3.py` | describe reports the stored content type | round trip, write-once | S3 |
| `847c207` | `s3_listing_ignores_its_prefix` | `s3.py` | a listing is only its prefix (the sweep never deletes a live ref) | `test_the_collector_keeps_a_live_jobs_media_and_collects_the_rest`, listing | S3 |
| `847c207` | `s3_listing_keeps_the_store_prefix` | `s3.py` | a listing names keys the store takes | collector, listing, isolation, delete | S3 |
| `eae07a9` | `s3_probe_skipped` | `pilot.py` | a bucket that does not answer HeadBucket refuses startup | `test_create_app_refuses_to_start_when_the_bucket_does_not_answer` | – |
| `eae07a9` | `s3_refusal_echoes_the_failure` | `pilot.py` | the refusal names the S3 error, never the endpoint or the bucket | same | – |
| `eae07a9` | `s3_prefix_not_composed` | `pilot.py` | the composed store uses S3_MEDIA_PREFIX | `test_create_app_from_settings_stages_into_the_configured_bucket` | S3 |
| `eae07a9` | `s3_bucket_not_asked_at_install` | `preflight.py` | a pilot install asks the bucket before replacing the file | `test_a_pilot_install_without_a_bucket_is_refused` | – |
| `eae07a9` | `s3_install_bucket_optional` | `preflight.py` | a pilot install without S3_MEDIA_BUCKET is refused | both install cases | – |
| `eae07a9` | `s3_install_refusal_ignored` | `preflight.py` | a refused HeadBucket is an install refusal | `test_a_pilot_install_asks_the_bucket_before_replacing_the_file` | – |
| `eae07a9` | `s3_install_endpoint_dropped` | `preflight.py` | the install asks the endpoint the gateway will use | same | – |
| `eae07a9` | `s3_install_refusal_echoes_the_bucket` | `preflight.py` | the install refusal never names the bucket | same | – |
| `eae07a9` | `s3_image_without_botocore` | `preflight.py` | the pilot probe refuses an image without botocore | `test_the_pilot_runtime_probe_refuses_an_image_without_botocore` | – |

G's list: `objects_from_settings_in_memory` re-anchored on the new code (returning an in-memory store before the S3 connect) and still killed; `objects_unset_in_memory`, `injected_objects_replaced`, `composition_root_adapters_not_from_settings`, `given_stores_replaced` still killed.

## Runs

The S3 service: only the E2 stack's `s3` service, namespace `e2` (`infrx-e2-s3`, `127.0.0.1:55500`, MinIO `RELEASE.2025-09-07T16-13-09Z` by digest), started through `tests/integration/harness.py`'s `compose("up", "-d", "--no-build", "s3")` after `assert_nothing_foreign()` (no `infrx-e2-*` container existed). No PostgreSQL, no Valkey, nothing on 55432.

| Command (from `apps/infrx-api`) | Result |
|---|---|
| `INFRX_M_S3_ENDPOINT=http://127.0.0.1:55500 uv run --frozen pytest -q tests/m/test_s3.py` | `28 passed in 10.09s` |
| `uv run --frozen pytest -q -rs tests/m/test_s3.py` (no endpoint) | `16 passed, 12 skipped in 4.84s` — each skip `M1-L2 (owner: M): no S3-compatible endpoint …` |
| `INFRX_M_S3_ENDPOINT=… uv run --frozen pytest -q tests/m --ignore=tests/m/test_mutants.py --ignore=tests/m/test_s3_mutants.py` (both stores) | `371 passed in 25.76s` |
| `INFRX_M_S3_ENDPOINT=… INFRX_MUTANTS=all uv run --frozen pytest -q tests/m/test_s3_mutants.py` | `28 passed in 105.95s` (27 mutants + the list check) |
| `INFRX_MUTANTS=all uv run --frozen pytest -q -rs tests/m/test_s3_mutants.py` (no endpoint) | `14 passed, 14 skipped in 57.13s` — the 13 no-Docker mutants killed, each S3 mutant `SKIPPED … M1-L2 (owner: M): no S3-compatible endpoint …` |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/test_mutants.py -k '<the five object-store mutants> or well_formed or anchor'` | `8 passed, 313 deselected in 67.38s` |
| `uv run --frozen pytest -q tests/g` (without the four mutant files; G4U uploads, relay, composition) | `509 passed, 2 warnings in 27.42s` |
| `uv run --frozen pytest -q tests/i` | `144 passed in 160.16s` |
| `uv run --frozen pytest -q tests/contracts --ignore=tests/contracts/test_mutants.py` | `1 failed, 1022 passed in 28.30s` — the failure is `test_cancel_cause.py::test_dur_settle__before_0018_the_pg_store_refuses_a_cause_it_cannot_record` (`DID NOT RAISE UnsupportedParameter`), **pre-existing**: it fails identically on the base `43fe900` in a detached scratch worktree (`1 failed, 1 deselected`); D5's 0018 changed `PgJobStore.cancel` and the test was not updated. Not this lane's |
| `DOCKER_HOST=unix:///nonexistent/docker.sock INFRX_M_S3_ENDPOINT=http://127.0.0.1:55500 make api-test` (at `eae07a9`) | `23 failed, 2902 passed, 591 skipped, 2 warnings in 1454.63s (0:24:14)`. The 23 are **one** pre-existing failure and its fallout: `test_cancel_cause.py::test_dur_settle__before_0018…` (above), plus 22 in `tests/contracts/test_mutants.py` (19 subset mutants and 3 runner self-tests), every one refused by the R83(b) pristine baseline naming that same test (`pristine baseline: the unmutated tree fails the list's own cases … test_cancel_cause.py::test_dur_settle__before_0018…`). No other failure; the M1-L2, G, I and M suites pass. The Docker suites were skipped (limit 11) |

## Review fix round (`M1L2-review-e1bb54f.json`: fix_required, A1–A2 blocking, A3–A7 folded in)

MinIO for this round: a private container `infrx-m1l2-minio` on `127.0.0.1:55661` (same image digest and E2 literals; labelled with this checkout; removed at the end). Namespace e2 was not used.

| Finding | Commit | Case(s) | Mutant(s), all killed |
|---|---|---|---|
| A1 the box run cannot use the instance role; writes are never removed | `0d682cd` | `test_the_s3_cases_keep_the_environments_credentials_unless_told_to_use_local_ones` (no Docker), `test_what_a_case_writes_is_removed_after_it` (S3) | `s3_local_flag_ignored`, `s3_cleanup_skipped` (S3) |
| A2 three arms of the error-vs-absent rule unkillable | `db72ddb` | `test_a_conflict_or_a_broken_body_is_an_error_and_a_404_is_absent` (Stubber: 409 on put, a short body mid-get, 404s on reads), `test_a_store_on_a_missing_bucket_reads_writes_and_lists_nothing` (S3) | `own_nosuchbucket_is_absent` (S3), `own_body_failure_is_absent`, `own_conflict_is_not_written` |
| A3 a 404 from put/list read as absent | `5b2f251` | `test_a_404_on_a_write_or_a_listing_is_an_error_not_absence` (Stubber) | `own_404_absent_everywhere`; `s3_any_error_is_absence` re-anchored |
| A4 ~2 min per call / ~3 min per install against a silent endpoint | `7805a94` | `test_a_failing_call_is_tried_twice_and_no_more` (a local 503 server sees exactly two HEADs), `test_a_pilot_install_waits_for_the_bucket_a_bounded_time` | `own_retries_count_retries`, `own_install_waits_unbounded` |
| A5 prefixes `""`, `./`, `../`, `a/../b/` accepted (in code) | `2277c17` | settings case gains `""`, `./`, `../`, `infrx/../other/`, `infrx/./`; `test_a_store_built_in_code_refuses_the_prefixes_the_settings_refuse` | `own_prefix_may_be_root`, `own_prefix_dot_segments`, `own_store_takes_any_prefix` |
| A6 listing past one page unproven | `eedc8f0` | `test_a_listing_past_one_page_names_every_key` (1001 keys, S3) | `own_listing_first_page_only` (S3) |
| A7 a composite checksum reported as a digest | `0c24660` | `test_only_a_whole_object_sha256_is_a_digest` (Stubber, six HeadObject answers) | `own_composite_checksum_is_a_digest`, `own_checksum_decoded_leniently`, `own_checksum_length_unchecked`; `s3_unchecksummed_object_is_absent` re-anchored |

The list is now **42 mutants** (17 need S3), all killed on MinIO. The runner passes `INFRX_M_S3_LOCAL_CREDS` through.

| Command (from `apps/infrx-api`, `INFRX_M_S3_ENDPOINT=http://127.0.0.1:55661 INFRX_M_S3_LOCAL_CREDS=1` unless stated) | Result |
|---|---|
| `uv run --frozen pytest -q tests/m/test_s3.py` | `38 passed in 11.44s` |
| same, endpoint and flag unset | `23 passed, 15 skipped in 4.21s` |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/m/test_s3_mutants.py` | `43 passed in 162.71s (0:02:42)` (42 mutants + the list check) |
| same, endpoint and flag unset (`-rs`) | `26 passed, 17 skipped in 85.97s` — the 25 no-Docker mutants killed, each S3 mutant skipped naming its owner |
| `uv run --frozen pytest -q tests/m --ignore=tests/m/test_mutants.py --ignore=tests/m/test_s3_mutants.py` | `381 passed in 28.24s` |
| `uv run --frozen pytest -q tests/g` (without the four mutant files) | `509 passed, 2 warnings in 11.42s` |
| `uv run --frozen pytest -q tests/contracts --ignore=tests/contracts/test_mutants.py` | `1 failed, 1022 passed in 25.15s` — the same pre-existing `test_cancel_cause` failure |
| `uv run --frozen pytest -q tests/i/test_install.py tests/i/test_packaging.py` | `64 passed in 16.92s` |

## Verifier follow-up (`M1L2-verify-0b9fc50.json`: PASS, four nonblocking fixture items folded in)

| Item | Commit | Case | Mutant (killed) |
|---|---|---|---|
| V1 the cleanup's registration unpinned | `c558ada` | `test_what_a_case_writes_is_removed_after_it` asserts the store is registered and runs the fixture's own teardown (`empty_what_was_written`), not `remove_prefix` | `own_v_cleanup_unregistered` (S3) |
| V2 the cleanup guard an `assert` on `startswith("test/m1l2/")` | `a782b67` | `test_the_cleanup_empties_only_one_cases_own_prefix` (no Docker: `infrx/`, bare `test/m1l2/`, another run's, a nested and a non-m1l2 prefix refused before any call reaches the bucket); the guard is now `fullmatch(r"test/m1l2/[0-9a-f]{32}/")` with a `raise`; the pair fixture uses two sibling case prefixes | `own_v_cleanup_guard_dropped` |
| V3 CreateBucket without the flag unpinned | `0808aa2` | `test_no_bucket_is_created_without_local_credentials` (Stubber: nothing queued without the flag, one CreateBucket with it) | `own_v_bucket_created_without_flag` |
| V4 the timeout refusal's text unpinned | `e430b7d` | the bounded-wait case passes `S3_ENDPOINT_URL` and asserts neither the bucket nor the endpoint host is named | `own_v_timeout_refusal_names_bucket` |

Runs on the private MinIO (`127.0.0.1:55661`, `INFRX_M_S3_LOCAL_CREDS=1`): `tests/m/test_s3.py` `40 passed in 13.46s` (no endpoint: `24 passed, 16 skipped`); `INFRX_MUTANTS=all tests/m/test_s3_mutants.py` `47 passed in 231.83s` (**46 mutants**, 18 need S3, plus the list check). An unmutated run leaves the bucket empty (counted: 0 objects after `40 passed`); the only leftovers after the full mutant list (3 objects) are written by mutated copies by design - `s3_key_without_the_prefix` writes outside any prefix, the two cleanup mutants leak their case's object - and the box never runs mutants.

## Limits

1. **No lifecycle, encryption or versioning settings.** The adapter sets none (no SSE/KMS header, no object lock, no lifecycle rule); the bucket's configuration and the instance role's permission on it are the coordinator's (request 1).
2. **HeadBucket proves `s3:ListBucket` only.** A role without PutObject/GetObject/DeleteObject on the prefix starts, and every write answers 503 `dependency_unavailable`. Upgrade: a write-and-delete round trip under `<prefix>probe/` at startup.
3. **A deleted bucket reads as "absent" to `head`/`describe`** (pinned by `test_a_store_on_a_missing_bucket_reads_writes_and_lists_nothing`, fix round A2). HeadObject's 404 has no body, so a missing key and a missing bucket are the same answer there; `get`, `put_if_absent`, `keys` and `delete` name NoSuchBucket and raise 503. Resolution then refuses (`not_found`), so nothing is served from it, but that refusal says the wrong thing. Startup's HeadBucket catches it at the next restart.
4. **A 409 `ConditionalRequestConflict`** (a concurrent conditional write to the same key) is a retryable 503, not retried in process (pinned by the Stubber case, fix round A2); the client's retry takes the 412 path. Marked `ponytail:` in `s3.py`.
5. **`keys()` materializes the whole listing** (M3 limit 5, unchanged). Pagination is proven past one page (1001 keys, fix round A6).
6. **Index state is still in process memory** (M3 limit 1): the objects now outlive the process, the refs, uploads and idle times do not. After a restart an object is unreachable by its handle and the collector (once something starts it) deletes it after the grace. D2's durable media rows are the fix; until then, one gateway process per prefix.
7. **Region.** botocore reads `AWS_REGION`/`AWS_DEFAULT_REGION` or its config; the preflight env schema cannot carry either, so a bucket outside the client's default region needs the unit to supply one. The account's buckets are in us-east-1 ⚠️ TO BE VERIFIED on the box.
8. **Proven against MinIO, not AWS S3.** Conditional PutObject (`If-None-Match: *`) and the SHA-256 checksum on HeadObject are documented AWS S3 features; this lane never called AWS. ⚠️ TO BE VERIFIED by the box run in Integration request 1, which the fixture now supports (fix round A1): `INFRX_M_S3_ENDPOINT=https://s3.us-east-1.amazonaws.com INFRX_M_S3_BUCKET=<bucket>` with `INFRX_M_S3_LOCAL_CREDS` **unset**, so the instance role is used.
9. **The instance-role credential path is not exercised**: every case uses environment literals. The gateway and worker units run `--network host`, so IMDSv2 answers the container at hop limit 1.
10. **Time per S3 call (fix round A4, measured by the review).** Two attempts in all (`total_max_attempts=2`; the first round's `max_attempts: 3` counted retries, i.e. 4 attempts, 123 s against an endpoint that accepts and never answers). Worst case per call, startup's HeadBucket included, is about a minute (2 × 30 s read + backoff); a refused connection costs about 1 s. An install waits at most `BUCKET_PROBE_TIMEOUT_S` = 60 s for the host's `aws s3api head-bucket` (it took 182 s before). A G4U upload whose `wait_for` times out keeps its worker thread, and the PutObject and its body (up to 64 MiB), for up to about a minute after the large-body slot is released; the write may still land, which is safe (a retry takes the 412 path and compares digests).
11. **`make api-test` ran with the Docker-backed suites skipped** (`DOCKER_HOST` pointed at no socket): D's harness binds 55432, forbidden to this lane, and the D2 Valkey container belongs to the E4B lane. Those suites were skipped visibly, not run.

## Integration requests

1. **Coordinator (box, names only).** The media bucket: `preflight.py apply --mode pilot … --set S3_MEDIA_BUCKET=<bucket>` (optionally `--set S3_MEDIA_PREFIX=<prefix>/`; leave `S3_ENDPOINT_URL` unset for AWS). The instance role needs `s3:ListBucket` on the bucket (HeadBucket, ListObjectsV2) and `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` on `<bucket>/<prefix>*` (default `infrx/*`) **and on `<bucket>/test/m1l2/*` for the verification run**. Region us-east-1, or `AWS_REGION` in the units. Bucket encryption/lifecycle as the coordinator decides (limit 1). Then, on the box, from `apps/infrx-api` with `INFRX_M_S3_LOCAL_CREDS` unset: `INFRX_M_S3_ENDPOINT=https://s3.us-east-1.amazonaws.com INFRX_M_S3_BUCKET=<bucket> uv run --frozen pytest -q tests/m/test_s3.py` (limit 8). Every case empties its own `test/m1l2/<uuid>/` prefix afterwards; nothing else in the bucket is touched.
2. **Cutover lane and E3B phase-3 lane.** Drop `objects=` injection wherever a composition built from settings now works (`create_app` builds and probes the store itself); keep injecting in unit tests. If the cutover's preflight items touch `apply`, keep `bucket_problems` on the pilot path.
3. **G owner.** `tests/g/test_composition.py::test_f_base__create_app_never_stages_into_process_memory` now points a set bucket at a dead local endpoint with local credentials (it would otherwise call AWS), and `objects_from_settings_in_memory` is re-anchored. Behaviour asserted is unchanged: every mode refuses without an answering bucket.
4. **I owner / coordinator.** The Dockerfile's sync adds `--extra traces`; preflight gained `bucket_problems` and the botocore probe; `TUNABLE` gained `S3_MEDIA_PREFIX`, `S3_ENDPOINT_URL`. A `media = ["botocore"]` extra, at the next lock regeneration, would keep clickhouse-connect out of the image.
5. **Coordinator (gate).** `make api-mutants` now runs `tests/m/test_s3_mutants.py` (42 mutants after the fix round); its 17 S3 mutants skip visibly without an endpoint. To run them: MinIO (the E2 stack's `s3` service, or any MinIO with the E2 literals) and `INFRX_M_S3_ENDPOINT=http://127.0.0.1:<port> INFRX_M_S3_LOCAL_CREDS=1` (about 3 minutes for all 42).
6. **D2.** The durable media rows (M3 request 1) are what make the durable objects addressable across restarts (limit 6).
7. **Coordinator.** Nothing starts `MediaCollector.run` yet (M3); with a real store, orphaned objects now accumulate until it does.
8. **D5/contracts owner.** The stale `test_cancel_cause.py::test_dur_settle__before_0018…` (fails on the base, see Runs).

## Proposed ruling (next free number R95; not numbered here)

> **The media object store (M1-L2).** The `ObjectStore` port's durable adapter is `infrx.media.s3.S3ObjectStore` on `S3_MEDIA_BUCKET` under `S3_MEDIA_PREFIX` (08 §5.1); no mode stages media into process memory. (1) Write-once is the server's conditional PutObject (`If-None-Match: *`), never a read then a write. (2) `head` is the whole-object SHA-256 the server verified on write (`x-amz-checksum-sha256`); an object without one (none, composite, malformed) is present and equal to no digest. (3) A 404 is "absent" only to a one-object read (head, get, describe) and to delete; to a write or a listing it is an error. Any other failure (403, NoSuchBucket, throttling, 5xx, a body that breaks mid-read, transport, a 409 conditional-write conflict) is `dependency_unavailable`, never "absent" or "not written". HeadObject's bodiless 404 cannot tell a missing bucket from a missing key; startup's HeadBucket is what refuses a missing bucket. (4) The gateway refuses to start unless the bucket answers HeadBucket with the process's own credentials; credentials and region come only from the environment's chain (the instance role on the box), never from settings text. A pilot install asks the same from the host before replacing the env file, and every refusal names the setting and the error code, never the bucket or the endpoint. (5) On one bucket, no deployment's prefix may be a prefix of another's: each collector lists and sweeps its whole prefix. (6) Until D2's durable media rows exist, one gateway process per prefix.

## Verification log

- 2026-09-23: Report written at `eae07a9` after items 1–3; the cutover merge and `make api-test` are appended below.
- 2026-09-23: `make api-test` at `eae07a9` finished: `23 failed, 2902 passed, 591 skipped` - the pre-existing `test_cancel_cause` failure and 22 contracts-mutant pristine-baseline refusals it causes (Runs). Next: merge `origin/codex/cutover-mount`.
- 2026-09-23: Review fix round on `M1L2-review-e1bb54f.json` (A1–A7, one commit each, `0d682cd`..`0c24660`); Limits 3, 4, 5, 8 and 10, the conformance paragraph, Integration requests 1 and 5 and proposed ruling (2)/(3) corrected in place (the fixture text in the first round said no case could reach AWS, and Limit 8 claimed a box run the fixture could not do).
- 2026-09-23: Verifier follow-up V1–V4 (`c558ada`..`e430b7d`) folded in after `M1L2-verify-0b9fc50.json` (PASS, nonblocking); 46 mutants, all killed.
