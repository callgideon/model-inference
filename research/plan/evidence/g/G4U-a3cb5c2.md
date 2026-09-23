# G4U — owned upload HTTP adapter (track G)

## Task and status

| Field | Value |
|---|---|
| Task | **G4U** (track G), `.claude/handoff/wave3/G4U.md` items 1–8; manifest `research/plan/tasks.json` id `G4U`; acceptance `09-amendment-workstreams.md` §G4U |
| Owner / session | implementation agent (Claude Opus 5.5, 1M context), wave 3 |
| Status | **implemented** against fakes and M3's in-process `MediaUploads` over `InMemoryObjectStore`. Not mounted (coordinator, request (a)); not integrated; no E3B upload journey has run |
| Oracles | MEDIA-SEC (G's half: oversized/chunked body, bounded bytes/time, projection, enablement) and DUR-RLS (route half: identity from the key row, tenant scope) — **fake-level only**. DUR-RLS's DB half is D2's `0010` checks, re-run read-only below |

## Source

| Field | Value |
|---|---|
| Base SHA | `740bebf8ad713a1ac0b9d47701c9278f01cbdeef` (`origin/claude/backend-impl`, contains `9c1c6ed` "TASK_PORTS registers d4"; fast-forwarded from the brief's `3160a1c`) |
| Implementation SHA | `a3cb5c2` (items `867e7ca..43dddc9`, then `a3cb5c2`: a comment marking the cut-off ceiling, no behaviour change); this report is committed on top |
| Branch / worktree | `codex/g4u-upload-adapter` / `.claude/worktrees/codex-g4u` |
| Integrated SHA | none (coordinator) |

| Item | Commit | Named killing cases | Mutants (all killed) |
|---|---|---|---|
| 1 router + enablement | `867e7ca` (router module, list scaffolding) | `test_media_sec__no_store_mounts_no_upload_route`, `test_media_sec__the_routes_mount_over_the_runtime_store_and_its_shared_slots` | `mounted_without_a_store`, `error_handlers_not_installed`, `runtime_store_ignored`, `runtime_slots_ignored` |
| 2 `POST /v1/uploads` | `679ed9b` | `test_dur_rls__each_audience_creates_in_its_own_org`, `test_dur_rls__an_operator_key_owns_no_upload`, `test_dur_rls__an_unauthenticated_caller_never_makes_us_buffer_an_upload`, `test_dur_rls__the_body_names_no_org_and_nothing_the_contract_lacks`, `test_media_sec__the_ticket_carries_exactly_the_frozen_fields`, `test_media_sec__no_store_field_outside_the_frozen_ticket_leaves` | `tenant_is_the_key_id`, `operator_owns_uploads`, `create_reads_before_identity`, `tenant_from_the_body`, `ticket_not_rendered_through_the_wire_model`, `created_is_not_201` |
| 3 `PUT /v1/uploads/{handle}` | `6c56ae3` | `test_media_sec__a_chunked_upload_over_the_cap_stops_reading`, `…a_slow_upload_is_cut_at_the_deadline`, `…large_uploads_hold_a_shared_slot_until_stored`, `…a_malformed_handle_is_not_found_before_any_byte`, `…the_destination_takes_only_media_types`, `…the_destination_is_write_once_over_http`, `test_dur_rls__another_orgs_upload_is_the_unknown_handles_404` | `destination_cap_is_the_request_cap`, `destination_deadline_stretched`, `destination_claims_no_slot`, `slot_never_released`, `slot_released_before_the_store`, `handle_grammar_unchecked`, `destination_type_unchecked`, `destination_reads_before_identity`, `destination_unguarded`, `stored_is_not_204`, `destination_under_another_id` |
| 4 `POST …/complete` | `27c8181` | `test_media_sec__completion_projects_the_ref`, `…completion_takes_no_fields`, `…completion_refusals_leave_in_the_envelope` (+ the item 2/3 identity and tenant cases, which drive completion too) | `completion_renders_the_ref`, `completion_fields_ignored`, `completion_unguarded`, `completion_reads_before_identity`, `completion_under_another_id` |
| 5 M3/G2 seam | `57fadba` | `test_dur_rls__a_completed_upload_is_usable_only_by_its_org` | `tenant_is_the_key_id`, `completion_under_another_id` |
| 6 HTTP smoke | `b64dc3f` | `test_media_sec__the_upload_handshake_uses_only_the_frozen_names` | `undeclared_path` |
| relocation | `2f8380f` | — (no case or mutant changed; see Changes) | — |
| 7 list | `43dddc9` (+ every item above) | `test_media_sec__a_control_body_is_bounded` | `control_body_unbounded`; default subset one per item |
| ceiling comment | `a3cb5c2` | — (comment only; no anchor moved) | — |
| 8 evidence | this file | — | — |

## What the routes do

`register(app, rt, store=None, large_bodies=None, new_request_id=ids.new_request_id)`
installs the envelope handlers, then mounts nothing and returns `None` unless a store is
passed or `rt.media_store` exists. The slots are `large_bodies`, else `rt.large_bodies`,
else a new `intake.LargeBodies()`. One `AuthResolver(rt)` per router (it keeps the pilot
R51 refusal). Every handler runs under `intake.guard`, so every answer, success included,
carries `Inference-Id`, and every refusal is the fixed-message envelope.

| Route | Order | Answer |
|---|---|---|
| `POST /v1/uploads` | request id → `AuthResolver.context` (headers only) → operator audience `forbidden` (the `catalog.CALLABLE` rule, same message) → `check_content_type` → `read_body(MAX_CONTROL_BYTES=4096, intake_timeout_s, rt.clock)` → `decode_utf8` → `parse_object` → `store.create_upload(auth.org_id, body)` | 201 `wire.UploadCreated.model_validate(…).model_dump(mode="json")` |
| `PUT /v1/uploads/{handle}` | request id → identity + audience → `UPLOAD_HANDLE_RE.fullmatch` else `not_found` (never echoed) → media type ∈ `store.fetcher.allowed_mime` else `unsupported_media` → slot from the shared `LargeBodies` → `read_body(max_media_bytes, intake_timeout_s, rt.clock, large=slot)` → `store.put_upload(auth.org_id, handle, data, mime)` → release in `finally` | 204, empty body |
| `POST /v1/uploads/{handle}/complete` | request id → identity + audience → grammar → bounded read (4096), empty or `{}` else `invalid_request` (R17) → `store.finalize_upload(auth.org_id, handle)` | 200 `wire.UploadCompleted.of(handle, finalized, ref)`; the `MediaRef` never leaves (R47) |

The store's own checks (closed constraint set, per-upload cap, write-once,
finalize-once, expiry, `(org, handle)` scope, digest, container probe) are not repeated in
the route; `tests/m/test_uploads.py` proves them (M3 at `e2188f3`).

## Requirement coverage

| Test ID | Invariant (the exact case) |
|---|---|
| MEDIA-SEC (enablement, 09) | No store → every upload path, and a wrong method, is a 404 `not_found` envelope with `Inference-Id` (`…no_store_mounts_no_upload_route`); `register(app, rt)` finds `rt.media_store` and shares `rt.large_bodies` (`…mount_over_the_runtime_store_and_its_shared_slots`) |
| MEDIA-SEC (bounded bytes) | A chunked destination body over `MAX_MEDIA_BYTES` is 413 after at most one chunk past the cap, nothing stored, slot released (`…a_chunked_upload_over_the_cap_stops_reading`, with `max_media_bytes=4096` and a 16 KiB body); a create or complete body over 4096 bytes is 413 after at most one chunk (`…a_control_body_is_bounded`) |
| MEDIA-SEC (bounded time) | A body arriving 20 s per chunk on the app's clock is 504 `deadline_exceeded`, nothing stored, slot released (`…a_slow_upload_is_cut_at_the_deadline`) |
| MEDIA-SEC (per-process bound) | With the one slot held, a large PUT is 429 `capacity_exhausted` + `Retry-After`, before its body is read when it declares its length and after at most the threshold plus one chunk when it is chunked (round 2 R4); otherwise the slot is held while the store takes the bytes (a store spy sees `in_flight == 1`) and `in_flight == 0` after (`…large_uploads_hold_a_shared_slot_until_stored`) |
| MEDIA-SEC (handle) | `upl_short`, `infrx-upload:upl_…`, `<org>:upl_…`, `upl_……`, `upl_…%2F..`, `..%2Fupl_…` on PUT and complete: 404 envelope equal (modulo request id) to an unknown well-formed handle's, no body byte read, not echoed (`…a_malformed_handle_is_not_found_before_any_byte`) |
| MEDIA-SEC (type) | `application/json`, `text/plain`, `video/mp4x`, no type → 400 `unsupported_media`, no byte read, nothing stored (`…the_destination_takes_only_media_types`) |
| MEDIA-SEC (immutability over HTTP) | Same bytes twice 204/204; other bytes 409 `state_conflict`; PUT after complete 409; after the window 410 `upload_expired` / `gone_error` (R22) (`…the_destination_is_write_once_over_http`); complete before any byte 400 and still completable; digest mismatch 400 `unsupported_media`, retry 409 (stays refused); oversize behind the destination 413 (`…completion_refusals_leave_in_the_envelope`); completion fields 400 and still completable (`…completion_takes_no_fields`) |
| MEDIA-SEC / R47 / R61(1) | Ticket keys equal `fixtures/v1/upload_created.json`'s, `destination_ref == "infrx-upload:" + upload_handle`, `expires_at` RFC 3339 `Z` (`…the_ticket_carries_exactly_the_frozen_fields`); a store answering extra fields (org, object key) → 500 `internal_error`, neither value in the body (`…no_store_field_outside_the_frozen_ticket_leaves`); completion keys equal `fixtures/v1/upload_completed.json`'s (incl. `media`), `ref.storage_ref` and the org absent from the bytes, retry body identical (`…completion_projects_the_ref`) |
| DUR-RLS (route half, R66) | Consumer and provider dev keys create in their own orgs (`…each_audience_creates_in_its_own_org`); an operator key is 403 on all three routes, creates nothing and leaves a same-org consumer upload untouched (`…an_operator_key_owns_no_upload`); no key / unknown / revoked → 401 on all three routes with zero body bytes read (`…an_unauthenticated_caller_never_makes_us_buffer_an_upload`); `org_id`/`purpose`/`filename`/`sha256`/`content_type` → 400, nothing created (`…the_body_names_no_org_and_nothing_the_contract_lacks`); org B's PUT/complete on A's handle equals the unknown-handle envelope and A's upload stays untouched and usable (`…another_orgs_upload_is_the_unknown_handles_404`) |
| DUR-RLS / R82 (M3/G2 seam) | After create → PUT → complete over HTTP with A's key: `validate.check_video_ref({"url": destination_ref})` accepts it; `resolve_owned(ORG_A, handle)` is the finalized ref; `resolve_owned(ORG_B, handle)` and an unfinalized handle are `not_found` (`…a_completed_upload_is_usable_only_by_its_org`) |
| E1B limit 5 / G1R limit 6 (server side) | httpx `ASGITransport` client using only `upload_handle`, `destination_ref`, `media` and the literal paths: create → PUT → complete, then a chat body with `{"type":"video_url","video_url":{"url":"infrx-upload:upl_…"}}` passes `validate.check_messages` (`…the_upload_handshake_uses_only_the_frozen_names`) |
| DUR-RLS (DB half) | D2's `checks_media` (RLS on, no browser grants, tenant-checked touch, finalize once) — cited, re-run read-only on both images below; not duplicated |

## Environment

Local, fakes only. Linux 7.0.0-1010-aws; Python 3.12.3 (`uv 0.11.8`, `uv sync --frozen`);
fastapi 0.141.1, starlette 1.6.0, httpx 0.28.1, pydantic 2.13.5. Docker 29.6.2 used only by
D2's harness for the two read-only `tests/d` runs (`infrx-d1-postgres`, port 55432, the
default D lock; removed at exit, none left) and by Q's harness for `tests/q`. This lane's
containers: `infrx-g4u-valkey` (127.0.0.1:55467, pinned valkey digest; used for the first
private runs from 06:48Z to 07:10Z, when nothing listened on 55467; removed with `docker rm -f -v`. That port has since become D5's Valkey, so it should not be reused) and `infrx-q3-valkey-55487`, which Q's harness
started under `INFRX_Q_VALKEY_PORT=55487` and removed at exit. No other lane's container
was touched.
No network, cloud, hosted Supabase or pilot-box access. Console deps for `make check` were
installed with `pnpm install --frozen-lockfile --offline` (lockfile unchanged).

## Commands and results

From `apps/infrx-api` unless noted; output tails are quoted from the logs.

UTC times from the logs. The tree is `43dddc9` for runs that started before 05:33:14Z, and
`a3cb5c2` (the same code plus one comment) for runs that started after it. No seed is used.

| Command | Exit | Tail (quoted) |
|---|---|---|
| `make api-env` | 0 | `+ zstandard==0.25.0` (pinned `uv sync --frozen --all-extras`) |
| `uv run --frozen pytest -q tests/g/uploads/test_uploads.py` (05:30Z) | 0 | `21 passed in 0.50s` |
| `uv run --frozen pytest -q tests/g tests/m tests/contracts` (05:30–05:34Z) | 0 | `1787 passed, 2 warnings in 243.23s (0:04:03)` |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/uploads/test_uploads_mutants.py` (05:34–05:35Z) | 0 | `31 passed in 70.73s (0:01:10)` (28 mutants, each killed, plus well-formedness, coverage and the pristine baseline) |
| `uv run --frozen python -m tests.g.uploads.uploads_mutants` | 0 | `28/28 killed` |
| `uv run --frozen pytest -q tests/g/uploads/test_uploads_mutants.py` (default subset) | 0 | `10 passed in 19.90s` (7 mutants + 3 list tests) |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/test_mutants.py` (G's shared list, 06:50–06:58Z) | 0 | `199 passed in 493.71s (0:08:13)` |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/m/test_mutants.py` (06:58–07:08Z) | 0 | `242 passed in 575.21s (0:09:35)` |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/contracts/test_mutants.py` (07:08–07:17Z) | 0 | `304 passed in 544.50s (0:09:04)` |
| legacy-first: `uv run --frozen pytest -q --ignore=tests/d` (05:35–05:48Z) | 0 | `2631 passed, 2 warnings in 787.94s (0:13:07)` |
| track-first, shared Q server: `uv run --frozen pytest -q tests/g tests/j tests/m tests/q tests/t tests/w tests/i tests/test_app_factory.py tests/test_gateway_auth.py tests/test_inflight.py tests/test_media.py tests/contracts` (05:48Z) | 143 (stopped at 06:50Z) | 43% after 62 min; `tests/q/test_reconcile.py` failing on its Valkey cases (`FFF.......F..FF.F.F.F…`). **Attribution:** the run used the shared `infrx-q3-valkey` on 55462, Q3's server, which other lanes were using at the same time (codex-g2, codex-w4 and infrx-impl all had `tests/q` runs going). Those lanes' SIGKILL drills (`vkharness.kill_and_restart`) kill and restart that shared container. Stopped; not a G4U failure |
| track-first, `INFRX_Q_VALKEY_PORT=55467` against `infrx-g4u-valkey`, which this lane started (06:51–07:02Z) | 1 | `2 failed, 2629 passed, 2 warnings in 693.47s (0:11:33)`: 2631 tests, the same count as legacy-first. Failed: `test_q3_drill__dr13_shape_the_rebuild_after_a_sigkill_comes_from_postgresql` and `test_q3_drill__valkey_sigkilled_under_queued_and_running_traffic_loses_no_job` (`RuntimeError: docker kill infrx-q3-valkey-55467: … No such container`). **Attribution: the harness's naming rule.** When the port is overridden, `tests/q/vkharness.py` derives `CONTAINER = f"infrx-q3-valkey-{PORT}"`, and the SIGKILL drills `docker kill` that name. The pre-started `infrx-g4u-valkey` was already listening, so the harness never started its own container and the drills had nothing to kill |
| `INFRX_Q_VALKEY_PORT=55467 uv run --frozen pytest -q -rfEs tests/q` (same server) | 1 | `2 failed, 190 passed in 47.27s` (the same two drills, same cause) |
| `INFRX_Q_VALKEY_PORT=55487 uv run --frozen pytest -q -rfEs -p no:cacheprovider tests/q` (07:10–07:11Z; `infrx-g4u-valkey` removed first, port 55487 free per `ss -ltn`; the harness started `infrx-q3-valkey-55487` itself and removed it at exit) | 0 | `192 passed in 49.64s`, both drills included |
| DUR-RLS DB half, plain image: `uv run --frozen pytest -q tests/d/test_admission.py::test_media__uploads_finalize_once_and_objects_delete_only_when_idle` (05:30Z, first attempt, no `HarnessBusy`) | 0 | `1 passed in 2.96s` |
| the same with `INFRX_D1_IMAGE=supabase` (05:30Z, first attempt) | 0 | `1 passed in 6.39s` |
| `INFRX_Q_VALKEY_PORT=55487 make -k check`, attempt 0 (07:17Z; head `a3cb5c2`; its log header reads "attempt 1"; renamed to `make-check-busy-0.log`) | stopped | api-test: `272 failed, 2723 passed, 25 xfailed, 2 warnings in 794.63s (0:13:14)`. **Attribution: `HarnessBusy`.** The shared D lock `/tmp/infrx-d1-postgres-55432.lock` was held by codex-g2's run, so every `tests/d` case needing it refused and nothing was altered. Stopped during api-mutants, along with its orphaned pytest; not counted |
| `INFRX_Q_VALKEY_PORT=55487 make -k check`, attempt 1 (07:46Z; head `a3cb5c2`; started once the D postgres lock had no live holder) | not completed (stopped at 09:15Z at the coordinator's instruction) | **api-test:** `7 failed, 2985 passed, 3 skipped, 24 xfailed, 1 xpassed, 2 warnings in 1192.34s (0:19:52)` → `make: *** [Makefile:13: api-test] Error 1`. The 7 failures are all `tests/d/test_outbox_relay.py::…[valkey]`, raising `tests.d.vkstore.HarnessBusy: another run holds /tmp/infrx-d2-valkey-55463.lock; nothing was altered`. **Attribution: `HarnessBusy`** on the shared D2 Valkey lock, which the coordinator's gate held at the time; contention, not code (see the rerun below). The 3 skips, 24 xfails and 1 xpass are all in `tests/d`: legacy-first, which excludes `tests/d`, had none, and the Makefile's pytest prints no skip reasons, so the three are not identified. **api-mutants:** reached 75% of the combined list with no failure, then was terminated (`make: *** [Makefile:19: api-mutants] Terminated`) when this lane's retry loop and its children were stopped. `console-test`, `console-lint`, `console-typecheck`, `console-mutants` and `bench-test`: **not run** (the stop came first). This lane's own lists ran separately above (G, M, contracts and G4U all green); the Q lists ran green inside `tests/q` on 55487 |
| `INFRX_Q_VALKEY_PORT=55487 uv run --frozen pytest -q -rfEs -p no:cacheprovider tests/d/test_outbox_relay.py` (08:43–08:44Z; no live holder on the D locks) | 0 | `20 passed in 56.98s`: attempt 1's 7 reds, green once the lock was free |
| `make integration INTEGRATION_ARGS="--layer 1 --canary"` | not run | nothing under `tests/integration` changed (expected unchanged) |

Warnings: the 2 warnings in the ordering runs are dependency deprecations, one from
`fastapi/testclient.py` (starlette's httpx testclient) and one from `starlette/testclient.py`
(an anyio `BlockingPortal` alias). They appear in both orderings, and neither comes from a
G4U file.

## Failure drill

No durable state exists in this lane (M3's store is in process). The drills are the
refusal cases: a body cut at the cap, a body cut at the deadline, a capacity refusal, a
foreign org, an expired window. Each asserts the store after: no object at
`store.upload_key(org, handle)`, the upload still `created` (and later completable where
the case says so), and `slots.in_flight == 0`. Retries: a same-bytes PUT is 204 again; a
completed upload's retry answers the identical body; a refused completion stays refused
(409). Cleanup: nothing to clean; the D harness removed its container.

## Artifacts

Code and tests in the commits above. Logs are local only (`.claude-logs/`, not committed);
no credential, signed URL or customer content in any of them (tests use
`tests/g/support.TOKEN` and suffixes of it; logs carry codes and request ids).

## Changes

New files only:

- `apps/infrx-api/infrx/gateway/routes/uploads.py`
- `apps/infrx-api/tests/g/uploads/test_uploads.py`
- `apps/infrx-api/tests/g/uploads/uploads_mutants.py`
- `apps/infrx-api/tests/g/uploads/test_uploads_mutants.py`
- `research/plan/evidence/g/G4U-a3cb5c2.md`

**Path deviation (recorded):** the brief names `tests/g/test_uploads.py`,
`tests/g/uploads_mutants.py` and `tests/g/test_uploads_mutants.py`. At those paths
`tests/g/test_mutants.py::test_every_case_is_covered_by_a_mutant` fails, because
`tests/g/mutants.py::_definitions` claims every `tests/g/test_*.py` case for G's list (G2's
file, not editable here). The suite therefore lives in `tests/g/uploads/`, outside that glob,
as `tests/g/ops` does (`2f8380f`). No contract, composition root, config, migration,
Makefile, manifest, lockfile or console file changed. Migration: none. Rollback: revert the
commits; nothing is mounted.

**Brief deviations (recorded):**

1. `check_structure` is not called on the create/complete bodies: the 4096-byte cap bounds
   the parse (the structure count exists for 96 MiB chat bodies), `parse_object` already
   answers deep nesting as 400, and the call would be an unkillable line (R32).
2. The PUT slot is released after `store.put_upload`, not before it: the bytes are this
   process's until the store has them, so the per-process bound covers the write too
   (`slot_released_before_the_store`, which inserts `slot.release()` before the store call, is
   killed by the invariant's own assertion; round 2 H1 corrected the first edit, which died
   by a crash).
3. The operator refusal applies to PUT and complete as well as create: an operator key
   shares its org with consumer keys (`OPERATOR_ROW`), and "owns no upload" has to hold
   on every route.
4. A retry of a digest-refused completion is 409 `state_conflict`, not a second 400: M3
   aborts a failed completion and `_still_open` refuses it. The case asserts that.
5. The `UploadCreated` re-validation is kept though the M3 store already returns exactly
   the frozen dump (its `Timestamp` serialises to RFC 3339 in python mode, so "the store
   returns python-mode datetimes" is not so today): it is the boundary against a durable
   store that answers a row. Proven by the leaky-store case, not by the key-set case (a
   mutant there was equivalent and survived at first; `ticket_not_rendered_through_the_wire_model`
   now names the leaky-store case).
6. The runner uses the shared default copy (`Runner(name="g4u", targets=(SUITE_FILE,))`):
   the cases need no repository-shaped layout and live in one file, so `files_for`,
   `targets_for` and `layout` would be dead parameters.

## Limits

1. **In-process upload state.** Upload records live in `MediaUploads.uploads` (M3 limit 1);
   a restart forgets every open upload, and G2's acceptor must stage from the same
   instance (request (a)). Durable rows are M's follow-up over D2's `0010`.
2. **Per-process slot bound.** `LargeBodies` bounds large bodies per process only; a
   second worker has its own. The PUT holds 64 MiB resident at most once per slot.
3. **No real object store and no signed URL.** The destination is this authenticated route;
   bytes travel through the gateway. An S3 adapter or presigned destination is M's (M1
   limit 2).
4. **Timeout.** The PUT deadline is `INTAKE_TIMEOUT_S` (30 s ⇒ ≥ ~2.2 MB/s for 64 MiB). A
   separate name only with a measured need (§7 default). Since round 2 the store call has its
   own `INTAKE_TIMEOUT_S` after the read, so a PUT can hold a slot for at most two deadlines.
5. **No per-upload early cut-off.** The route stops at `MAX_MEDIA_BYTES`; the upload's own
   smaller `max_bytes` is enforced by `put_upload` after the read. An early cut needs a
   public cap read on `MediaUploads` (optional M request).
6. **Not smoked end to end.** Unmounted: no E3B upload journey, no Caddy edge, no bench.
   `bench.py`/`client_example.py` still speak the old DTO (request (c)).
7. No per-org upload quota or idempotency key (not in the frozen contract).
8. **Shared harnesses.** `tests/q` on the default port shares `infrx-q3-valkey` (55462) with
   every lane that runs it, so another lane's SIGKILL drill fails this lane's run. A private
   port works only if Q's harness starts the container itself (see the Results rows and
   request (e)). The D locks (postgres 55432, d2 valkey 55463) serialise `make check` across
   lanes (`HarnessBusy`).

## Handback

§7 defaults taken: statuses 201 create / 204 PUT / 200 complete; no `org_suspended` check
in G (R33 lists admission, not upload creation; the store/admission refuses use); PUT
deadline `INTAKE_TIMEOUT_S`.

Next unblocked: E3B phase 2's `video_upload × {sync, sse, async}` journeys after request
(a) and G2's acceptor (they also need D4/G3/D5).

Integration requests:

**(a) Coordinator, `apps/infrx-api/infrx/gateway/app.py`** — mount after the ingress, over
one store and one slot pool:

```diff
-from .routes import chat, health, models
+from .routes import chat, health, models, uploads
@@
-ROUTERS = (health, models, chat)
+ROUTERS = (health, models, chat, uploads)   # cutover: (health, models, ingress, uploads)
```

and at the cutover composition (wherever `IngressDeps` is built for `rt.ingress`):

```python
rt.large_bodies = intake.LargeBodies()
rt.media_store = MediaUploads(objects, limits=rt.settings.pilot, ...)   # M's composition
rt.ingress = IngressDeps(..., large_bodies=rt.large_bodies,
                         accept=<G2 acceptor staging from rt.media_store>)
```

`uploads.register` mounts nothing while `rt.media_store` is absent, so the `ROUTERS` line
is safe before M's composition lands. The store **must** be the instance G2's acceptor
stages from (upload records are in process).

**(b) Coordinator, `Makefile`** — `api-mutants` gains G4U's list:

```diff
-... tests/g/test_mutants.py tests/g/ops/test_mutants.py tests/i/test_mutants.py ...
+... tests/g/test_mutants.py tests/g/ops/test_mutants.py tests/g/uploads/test_uploads_mutants.py tests/i/test_mutants.py ...
```

**(c) E (E1B follow-up / E3B phase 2), `models/marlin2b/bench.py::upload`** — to the frozen
shape (`client_example.py` inherits it):

```diff
-    """POST /v1/uploads -> PUT to the returned constrained destination ->
-    POST /v1/uploads/{handle}/complete. Contracts v1 shape; unverified until M3/G4."""
+    """POST /v1/uploads -> PUT /v1/uploads/{upload_handle} -> POST .../complete, in the
+    frozen contract's names (wire.UploadCreated / UploadCompleted); G4U serves them."""
@@
     r = await client.post(cfg["base"] + "/uploads", headers=cfg["headers"],
-                          json={"purpose": "video", "filename": os.path.basename(path),
-                                "bytes": len(body), "sha256": digest, "content_type": mime})
+                          json={"bytes": len(body), "digest": "sha256:" + digest,
+                                "accepted_mime": [mime]})
     row["upload_status"] = r.status_code
     if r.status_code >= 400:
         raise UploadFailed()
-    created = r.json()
-    dest = created.get("upload") or created
-    put = await client.request(dest.get("method", "PUT"), dest["url"], content=body,
-                              headers={"content-type": mime, **(dest.get("headers") or {})})
+    # Allowlisted before it is used in a path: `upl_` + 22..64 (contracts/ids.py).
+    handle = allow(r.json().get("upload_handle"), HANDLE_OK, cfg["key"], fallback=None)
+    if handle is None:
+        raise UploadFailed()
+    put = await client.put(f"{cfg['base']}/uploads/{handle}", content=body,
+                           headers={**cfg["headers"], "content-type": mime})
     row["upload_status"] = put.status_code
     if put.status_code >= 400:
         raise UploadFailed()
-    done = await client.post(f"{cfg['base']}/uploads/{created['handle']}/complete", headers=cfg["headers"],
-                             json={"sha256": digest, "bytes": len(body)})
+    done = await client.post(f"{cfg['base']}/uploads/{handle}/complete", headers=cfg["headers"])
     row["upload_status"] = done.status_code
     if done.status_code >= 400:
         raise UploadFailed()
-    # The handle goes back out in the request body and into the resume state, so it is
-    # allowlisted like any other server string: `upl_` + 22..64 (contracts/ids.py).
-    handle = allow((done.json() or {}).get("handle", created["handle"]), HANDLE_OK, cfg["key"],
-                   fallback=None)
-    if handle is None:
-        raise UploadFailed()
     return handle
```

(`mime` must be one of `video/mp4`, `video/webm`, `video/quicktime`, or the create is 400.)

**(d) G1R/G6B owners, once (a) lands** — drop "specified, not served" for uploads (jobs keep
it until G3): `apps/infrx-api/tests/g/test_client_smoke.py:8`,
`apps/infrx-api/client_example.py:16`, `apps/infrx-api/README.md:174`,
`research/workloads/marlin-sop.md:560` (the `infrx-upload:` row) and D12 at `:698`.

**Notes for the G and M owners.** G2: the coverage rule in `tests/g/mutants.py::_definitions`
claims every `tests/g/test_*.py` case, which is why this suite lives in `tests/g/uploads/`. If
G2 ever widens that glob to recurse, it has to exclude `tests/g/uploads/`, which has its own
list. G2's acceptor must stage from `rt.media_store`, the same instance (request (a)). M: the
PUT stops reading at `MAX_MEDIA_BYTES`. An early cut at the upload's own `max_bytes` needs a
public cap read, e.g. `MediaUploads.cap_of(org_id, handle) -> int` (optional, Limits 5). The
store's `create_upload` already returns the frozen dump, so a durable adapter must keep
returning exactly those keys, or the route answers 500 rather than leak (the leaky-store
case).

**(e) Q owner / coordinator, `apps/infrx-api/tests/q/vkharness.py`** (harness line; optional).
Let a lane name its private server in its own namespace, so a pre-started
`infrx-<task>-valkey` is the container the SIGKILL drills kill:

```diff
-CONTAINER = (_SERVICE.container if PORT == _SERVICE.host_port
-             else f"{_SERVICE.container}-{PORT}")
+CONTAINER = os.environ.get("INFRX_Q_VALKEY_CONTAINER") or (
+    _SERVICE.container if PORT == _SERVICE.host_port else f"{_SERVICE.container}-{PORT}")
```

Until then, a lane's `make check` should export `INFRX_Q_VALKEY_PORT=<a free port>` and let
the harness start and remove `infrx-q3-valkey-<port>` itself (this report's runs used 55487;
it is not in `TASK_PORTS` — register it for g4u if lanes should reserve it).

## Round 2 (review `G4U-review-f3c8055.json`: fix_required)

The review is `research/plan/evidence/g/G4U-review-f3c8055.json` on `claude/backend-impl`.
The tenant lens passed. The fix round is on this branch, one commit per finding, with no
rebase, reset, amend, push or Docker. The round-2 implementation SHA is `6ad81ba`.

| Finding | Commit | Change | Named case → mutant (all killed) |
|---|---|---|---|
| R1/H3 (blocking) | `176c76f` | none in code; the control-body deadline was untested | `test_media_sec__a_slow_control_body_is_cut_at_the_deadline`: create and complete get a body arriving 20 s per chunk on the app's clock → 504 `deadline_exceeded`, nothing created or finalized → `control_deadline_stretched` |
| H1 (blocking) | `0a87585` | `slot_released_before_the_store` is now the brief's order (`slot.release()` inserted before the store call). The old edit moved the store call into `finally`, still before the release, and died only by an `UnboundLocalError` on the 429 path, which the guard turned into a 500 | `…large_uploads_hold_a_shared_slot_until_stored` dies at `AssertionError: the slot was not held while the store took the bytes` |
| T1 | `30a03d4` | create renders the validated JSON dump only if `upload_handle` matches `UPLOAD_HANDLE_RE` and `destination_ref == "infrx-upload:" + upload_handle`; otherwise `internal_error` | `test_media_sec__no_store_value_outside_the_frozen_ticket_leaves` (a store writing an object key with the org into `destination_ref`; a store issuing `upl_x`) → `ticket_destination_unchecked`, `ticket_handle_unchecked`; `ticket_not_rendered_through_the_wire_model` re-anchored on the validation line (`ticket = created`) |
| T3 | `5fc011d` | the routes' guard wraps `intake.guard` and sets `Connection: close` on every answer ≥ 400, including the pre-read 403 operator, 404 handle and 400 media-type refusals. `intake.py` is unchanged | `test_media_sec__every_refusal_closes_the_connection` → `refusals_keep_the_connection` |
| H2 | `a4b0387` | `destination_unguarded` and `completion_unguarded` (dropping `@guarded` made `request_id` a query parameter, so every call answered 422) are replaced by `refusals_not_translated`: the translation is gone and a minted request id is still passed | dies on the store's typed refusal escaping the route: `Conflict` in `…the_destination_is_write_once_over_http`, `InvalidRequest` in `…completion_refusals_leave_in_the_envelope` |
| T4 | `6d520f5` | tests only | `…the_body_names_no_org_and_nothing_the_contract_lacks` also creates with `?org_id=ORG_B`, `x-org-id` and `x-infrx-org` → 201 in ORG_A. `…another_orgs_upload_is_the_unknown_handles_404` sends org B's PUT and complete naming ORG_A in the query and both headers → still the unknown-handle envelope. Mutants: `create_org_from_query`, `put_org_from_header`, `complete_org_from_header` (the reviewer's R12/R13 shape) |
| R2 | `ef2d08f` | `slots = large_bodies or rt.large_bodies or rt.ingress.large_bodies or LargeBodies()` | `test_media_sec__without_a_runtime_pool_uploads_count_against_the_ingress_pool` → `ingress_pool_ignored` |
| R3 | `5ec093c` | `store.put_upload` runs under a further `intake_timeout_s` (`asyncio.wait_for`), not whatever remains of the read's deadline, so a PUT holds a slot for at most two deadlines; a timeout becomes `deadline_exceeded`, and the slot is still released in `finally` | `test_media_sec__a_hung_store_is_cut_at_the_deadline` (a store 1 s slow against a 0.2 s deadline: 504, nothing stored, `in_flight == 0`; this uses real time because `wait_for` has no injectable clock) → `store_call_undeadlined` |
| R4 | `6ad81ba` | wording: with every slot taken, a PUT is 429 before its body is read when it declares its length, and after at most the threshold plus one chunk when it is chunked. Now asserted | `…large_uploads_hold_a_shared_slot_until_stored` (a chunked PUT: 429, `read <= threshold + 30`), killed by that case's existing mutants |

**Recorded as requests, not code**

- **T2 + R5 → M request.** Add a public `MediaUploads.check_open(org_id, handle) -> int` that
  runs `_owned` and `_still_open` and returns the upload's `max_bytes`. The PUT would call it
  before claiming the slot and would then read at most `min(max_bytes, MAX_MEDIA_BYTES)`.
  **Cost today:** the write-once, tenant, finalized and expired refusals on PUT are decided
  only after the whole body is read. A foreign, unknown, finalized or expired handle costs a
  full read of up to `MAX_MEDIA_BYTES` (64 MiB), bounded by the cap, the deadline and one
  shared slot. The 404 stays identical for a foreign and an unknown handle, so it gives away
  nothing about existence.
- **H4.** The Docker-backed rows (legacy-first, the track-first runs, the `tests/d` DB-half
  runs and `make check`) are this lane's own runs. The coordinator's gate confirms them;
  the review, which used no Docker, did not reproduce them.

**Round-2 runs** (head `6ad81ba`; tails quoted from `.claude-logs/r2/`)

| Command (head `6ad81ba`) | Exit | Tail (quoted) |
|---|---|---|
| `uv run --frozen pytest -q tests/g/uploads/test_uploads.py` (09:52Z) | 0 | `26 passed in 0.73s` |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/uploads/test_uploads_mutants.py` (09:52–09:54Z) | 0 | `39 passed in 97.60s (0:01:37)` (36 mutants plus 3 list tests) |
| `uv run --frozen python -m tests.g.uploads.uploads_mutants` | 0 | `36/36 killed` |
| `uv run --frozen python -m tests.g.uploads.uploads_mutants --list` | 0 | `36 mutants over 26 named cases` (killed = declared) |
| `uv run --frozen pytest -q tests/g tests/m tests/contracts` (09:55–10:00Z) | 0 | `1792 passed, 2 warnings in 282.15s (0:04:42)` (1787 + the 5 new cases) |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/test_mutants.py` (G's list and coverage rule, 10:00–10:10Z) | 0 | `199 passed in 611.42s (0:10:11)` |

Not re-run in round 2: the orderings, `tests/d`, `tests/q` and `make check` (the fixes touch only
`gateway/routes/uploads.py` and `tests/g/uploads/`; Docker was not used in this round, per the
coordinator's instruction).

## Confirmation fold-ins (`G4U-confirm-962b2b1.json`: pass, 0 blocking)

All nonblocking items are in one commit, `73dfaaf`. The only code change is the G5 wording
of a comment.

| Item | Change | Case → mutant (all killed) |
|---|---|---|
| C1/G1/G2 | `…no_store_value_outside_the_frozen_ticket_leaves` gains a destination that keeps the scheme but not the value (`infrx-upload:<org>/<handle>`) and a valid handle with a suffix (`<handle>/<org>`, with a destination to match). Both are 500, and the org is absent | `ticket_destination_prefix_only` (`startswith`), `ticket_handle_prefix_match` (`match` for `fullmatch`) |
| C2 | the 500 answers in that case carry `Connection: close` | `refusals_keep_the_connection` now also names that case |
| G4 | `…every_refusal_closes_the_connection` asserts the 201, 204 and 200 answers do not close | `successes_close_the_connection` |
| C3 | `test_dur_rls__the_router_reads_no_org_from_the_query_or_headers`: a structural check that `uploads.py` reads only `request.path_params["handle"]` and `request.headers.get("content-type")` from the request, and no query or cookie | named by `create_org_from_query`, `put_org_from_header`, `complete_org_from_header` |
| G5 | wording only: the store call gets a further `intake_timeout_s` (comment at the call and the R3 row above) | — |
| G3 | none: this is request (a). The coordinator applies it at the cutover, putting one `LargeBodies` on both `rt.large_bodies` and `IngressDeps.large_bodies`, with `rt.media_store` shared with G2's acceptor | — |

**Runs** (head `73dfaaf`; tails quoted from `.claude-logs/r3/`)

| Command | Exit | Tail |
|---|---|---|
| `uv run --frozen pytest -q tests/g/uploads/test_uploads.py` (10:35Z) | 0 | `27 passed in 1.01s` |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/uploads/test_uploads_mutants.py` (10:35–10:37Z) | 0 | `42 passed in 135.96s (0:02:15)` (39 mutants plus 3 list tests) |
| `uv run --frozen python -m tests.g.uploads.uploads_mutants` | 0 | `39/39 killed` |
| `uv run --frozen python -m tests.g.uploads.uploads_mutants --list` | 0 | `39 mutants over 27 named cases` (killed = declared) |

## Verification log

- 2026-09-23: Report written at implementation SHA `a3cb5c2` on base `740bebf`; all tails
  quoted from `.claude-logs/`; status implemented (fakes and in-process store), not
  integrated. `make check` did not complete: attempt 1 was stopped during api-mutants at the
  coordinator's instruction, and its only reds were `HarnessBusy`, rerun green. The lane's
  own retry loop and its children were stopped; this lane's containers (`infrx-g4u-valkey`,
  `infrx-q3-valkey-55487`) were removed and no container of this lane is left.
- 2026-09-23: Round 2 (review `G4U-review-f3c8055.json`, fix_required): blocking R1/H3 and H1
  plus T1, T3, H2, T4, R2, R3 and R4 fixed in `176c76f..6ad81ba`, one commit each. T2/R5 filed
  as an M request, H4 left to the coordinator's gate. Deviation 2's parenthetical and the 429
  wording are corrected in place. The round-2 runs are quoted from `.claude-logs/r2/`.
- 2026-09-23: Confirmation `G4U-confirm-962b2b1.json` passed. Its nonblocking C1–C3 and G4–G5 are
  folded into `73dfaaf`, and G3 is request (a), which the coordinator applies at the
  cutover. The focused suite and the list were re-run at `73dfaaf` (39/39 killed).
