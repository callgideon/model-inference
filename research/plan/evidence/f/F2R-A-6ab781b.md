# F2R-A — Python contract carryovers (items 2, 3, 4, 5, 9) + coordinator additions 1–9

| Field | Value |
|---|---|
| Task / status | F2R lane A. **implemented** (fake + concrete W/T/M1 adapters; nothing live, nothing integrated) |
| Session | resumed lane agent after coordinator restart; previous agent's work preserved as WIP `48d61b9` and continued on top (no rebase/reset/amend) |
| Source | base `ec6c548`, branch `codex/f2r-contract-carryovers`, worktree `.claude/worktrees/codex-f2r`, implementation head `6ab781b` (this report is committed after it) |
| Integrated SHA | none — coordinator merges into `claude/backend-impl` |

## Environment

Linux 7.0.0-1010-aws (dev host), Python 3.12.3 (`apps/infrx-api/.venv`, `make api-env` → `uv sync --frozen --all-extras`, exit 0), uv 0.11.8, Node v22.23.1, Docker 29.6.2 (not used; no container created). Local only. No cloud, hosted DB, pilot host or paid provider touched.

## Carryover item → commit → killing test

| Item | Commit(s) | What changed | Killing test / mutant |
|---|---|---|---|
| 9 shared mutation runner | `48d61b9` (WIP, previous agent), `d976b79`, `d7790c6`, `7c7c635`, `6ab781b` | one runner in `tests/contracts/mutants.py` (`Mutant`/`Outcome`/`Result`/`Runner`/`run_mutant`/`main`/`assertion_kill`); m, q, j, w, t, g lists delegate (`shared.run_mutant(m, RUNNER)`), `tests/d/migration_mutants.py` delegates the kill rule (`assertion_kill`). Anchor ×0/×2 ⇒ `misdeclared`; `compile()` failure ⇒ `broken_runner`; undeclared non-assertion, non-`DomainError` death ⇒ `broken_runner`; timeout ⇒ `broken_runner`; private `PYTHONPYCACHEPREFIX`/`TMPDIR` per subprocess; pyproject travels with the copy | self-tests in `tests/contracts/test_mutants.py`: `test_the_runner_cannot_report_a_false_kill` (syntax, no-op survivor, missing anchor, no case, **two anchors**, **undeclared crash**), `test_the_same_defect_is_a_kill_once_its_exception_is_declared`, `test_a_mutant_that_makes_its_case_hang_is_not_a_kill`, `test_every_subprocess_gets_its_own_cache_and_temporary_directory`, `test_the_in_process_kill_rule_is_the_same_rule` (new, D's rule without Docker); each track keeps its own self-tests |
| 2 engine `{visible, raw}` | `41cd2a0` | `FakeEngine` scripts `visible` beside `raw` (`SPLIT_REASONING_VISIBLE`); both exported engine cases read `raw` and assert `visible` think-free and payload keys exactly `{visible, raw}`; W: `content` alias deleted, `PAYLOAD_COPIES` 3→2, comment corrected (empty payload measures 23 B), alias assertion dropped | `engine_fake_visible_is_raw` → `api_stream__reasoning_delimiters_split_across_chunks`; `engine_fake_keeps_the_content_alias` → `api_stream__canonical_events_end_with_authoritative_usage`; W `visible_and_raw_collapsed`, `event_bytes_sized_for_ascii` re-anchored and killed. Both adapters (shared fake, `VllmEngine`) pass the exported suite |
| 3 production trace base | `34a3b84` | new `infrx/contracts/traces_accounting.py` (`TraceCaptureBase`, `TraceSinkBase`: clock required, no crash, no failure hooks); `FakeTraceSink` adds `FakeClock` default, `FailurePlan`, `crash()`; `SpoolTraceSink` subclasses the base and imports nothing from `contracts.fakes`; spool crash drill + failure hooks moved to `tests/t` `DrillSpool`; metadata reserve charged `max(declared, len(compact_bytes(envelope)))`; frozen case `trace_bounds__metadata_exhaustion_drops_with_counters` revised; `QUEUED_PAYLOAD_MAX_BYTES` removed — reviewer scenario (4,000 under-declared 20 KB envelopes against a stalled writer) now stops at the 8 MiB reserve with `metadata_budget` | `metadata_charged_as_declared`, `metadata_charged_as_serialized` → the revised case; T `the_queue_counts_only_rows` (retargeted to the charge) → `test_the_queue_is_bounded_in_bytes_as_well_as_in_rows`; T `the_production_sink_acquires_the_fake` → `test_the_production_sink_carries_no_test_hook`; T `a_clockless_sink_is_built` (retargeted, `dies_by=AttributeError` declared) → `test_a_sink_without_a_clock_refuses_to_exist`. Three T mutants of the crash body retired (it is test code now) |
| 4 store-produced refs only | `0f6ade2` | fake + M1 `store.stage` url/inline branch = `refs.get`-or-`not_found` (digest must match); caller-ref indexing (`pending`/clash) deleted; tenant guard = org namespace of every lookup (redundant `ref.org_id` check removed); fake `attach` binds only indexed refs; harness hook `materialized(org_id, ref)` + `builders.materialized()`; canonical requests carry one `video_url` part per ref in order (`builders.message_content`); `ports.MediaStore.stage` documents that `duration_s` is written by preparation (M2). `store.py` hunk touches only `stage` — applies cleanly on M2's merged file (verified, below) | forged/foreign/never-materialized refused at stage **and** attach in `media_sec__a_partial_request_stages_nothing`: `staging_takes_a_ref_it_never_made`, `attach_takes_a_ref_it_never_made`, `staging_overwrites_content` (re-anchored), `staging_ignores_the_org` (retargeted to handle-anywhere lookup); M: `stage_takes_a_ref_it_never_made` → `test_stage_takes_only_refs_this_store_materialized`, `stage_accepts_a_foreign_reference` retargeted. Four M mutants of deleted code retired |
| 5 candidate port, UUIDv4 samples | `7c787c4` | `TraceCandidate`, `CandidateSource`, `RateTable`, `FINISH_REASON_LENGTH`, `NO_OUTPUT_STATUS` moved to `contracts/ports.py` (J re-exports); `JudgeRun.sample_ids: tuple[UuidStr, ...]` + uniqueness validator; `records.JudgeSample` `{sample_id, rubric_version, request_id|null, scores}` | `judge_run_duplicate_sample_ids`, `judge_run_sample_ids_any_string` → `test_judge_run_sample_ids_are_unique_lowercase_uuid4`; `judge_sample_request_id_optional` → `test_the_judge_sample_dto_is_the_consoles_four_fields`; 8 J mutants repointed to `contracts/ports.py`, killed |
| 6, 7 | lane B (merged) | — | — |

### Coordinator additions

| # | Commit | Change | Test / mutant |
|---|---|---|---|
| 1 | `2afead8` | `JobStore.admit` docstring: refuse `deadline_at <= db_now`, else `min(caller, db_now + budgets)` | docstring (behaviour already pinned by existing cases) |
| 2 | `cbdc1e3` | `test_the_money_context_is_pinned` (prec ≥ 40; traps = {InvalidOperation, DivisionByZero, Overflow}); comment on `dur_admit__a_deadline_must_be_one_the_store_can_keep` (D2 runs it on the movable DB clock). Note: the file is `infrx/contracts/money.py` (there is no `infrx/money.py`) | `money_context_default_precision`, `money_context_traps_nothing` killed |
| 3 | `74ababa` | `MediaRef.local_path: str \| None = None` | data only |
| 4 | `5350832` | `PROCESSING_CACHE_DIR` = `PilotSettings.processing_cache_dir: str = ""`, read by `config.pilot_from_env`; `validate_pilot` refuses a set root (this and `TRACE_SPOOL_DIR`) that is relative or whitespace-padded. **Placed in `limits.PilotSettings`, not `config.DeploymentSettings`**: lane B's deployment reader refuses empty values, and empty must mean "no local cache" | `relative_root_accepted` → `test_a_filesystem_root_is_unset_or_absolute` |
| 5 | `bfced4d` | `video/mpeg` dropped from `DEFAULT_ALLOWED_VIDEO_MIME`; M's data-URL ingress refuses it (`test_a_data_url_must_be_base64_and_an_allowed_video_type`) | `mpeg_allowed_again` → `test_the_default_video_allow_list_has_no_mpeg` |
| 6 | `6c6c034` | `ports.MediaPreparation.prepare_request(org_id, request) -> NormalizedRequest` as a **separate protocol** (adding it to `MediaStore` would break every `MediaStore` adapter's protocol check); `prepare(job_id, profile)` unchanged | protocol only |
| 7 | `0f6ade2` | item 4's one-line `store.stage` change, in M's file under the coordinator's delegation | above; pinned-blocked case passes against M2 after merge (IR-A2) |
| 8 | — | IR-6 DEPLOY-01…05: anchors live in lane B's `config.py` (not in this base), so they are delivered as IR-A1, **verified killed on the trial merge** | 5/5 killed (DEPLOY-04 needs `dies_by=("RuntimeMisconfigured",)`) |
| 9 | `7c787c4`, `58b925c` | `records.AccountingRegime` (legacy_usd\|pilot), `JudgeSample` Python spelling; 08 §5 `FETCH_*` → `MEDIA_FETCH_*` (`limits.py:64-66`) | `accounting_regime_respelled` → `test_enum_values_are_frozen[AccountingRegime]`; parity move is IR-A3 |

| R61 (1) (M3-review ruling) | `33a5782` | upload reference `infrx-upload:upl_<id>` in the fake, the frozen `upload_created.json` (the one sanctioned v1 fixture byte change, noted in the guard test), contracts README, fixture guard (exact form) and the `upload_handle_is_guessable` anchor; `media_sec__an_upload_is_owned_verified_and_immutable` asserts the exact form | `upload_reference_names_the_org` killed; `upload_handle_is_guessable` killed. Not changed: the fixture's `accepted_mime` still lists `video/mpeg` (a caller-constrained list, not the default; ruling covered only the reference byte) |

Post-trial-merge fixes on this lane (found by merging onto the moving integration head, see below): `6ab781b` Q's `run_mutant(…, paths=)` restored (Q2's Valkey list calls it; the WIP had dropped it), `7c7c635` W's `run_mutant(mutant, suite)` kept for W2's loop list, and `b4521c1` the squat branch of `media_sec__staging_never_replaces_an_existing_object` returns for content-addressed stores (M1/M2/M3 cannot hold a tenant object under an upload handle), the fake still runs it.

AuditAction vocabulary drift (console `grant|suspension_set|entitlements_set|calibration_label` vs D1 `admin_grant|admin_set_suspension|admin_set_entitlements|calibration_label`) and the non-null `AuditEntry.after`/`idempotency_key` vs nullable columns: **noted, not changed** (coordinator ruling). Python has no `AuditEntry` DTO, so "nullable audit target on both halves" has no Python half to freeze.

### Transfers / closures (unchanged from the 15-pending-inputs table)
1, 8, 10, 12, 15 closed in the audit (tests preserved; money context now pinned, item 8). 6, 7 lane B. 11 → C0. 13 → J2 (P-10). 14 → G2/I0. 16 → E2R.

## Docs
`58b925c`: 08 §10 rulings **R79** (settled R-3 clamp), **R80** (`{visible, raw}`, settled usage wording), **R81** (metadata charge, neutral accounting base), **R82** (store-produced refs), **R83** (one mutation runner) — numbered R63–R67 on this lane (after the WIP's R61–R63) and renumbered R79–R83 by the coordinator at the merge `e1a33d1`, because R63–R78 already existed on the integration branch; §5 spool segment 2, `MEDIA_FETCH_*` names, `PROCESSING_CACHE_DIR` row; verification-log entry. `TRACE_SPOOL_SEGMENT_BYTES` is lane B's §5.1 row (not duplicated).

## Commands and results (exit codes quoted from output)

| Command (in `apps/infrx-api` unless noted) | Exit | Tail |
|---|---|---|
| `make api-env` (root) | 0 | `Checked 41 packages` |
| `uv run --frozen pytest -q -p no:cacheprovider --ignore=tests/d` | 0 | `1532 passed, 2 warnings in 287.64s` |
| legacy-first: `pytest -q tests/test_app_factory.py tests/test_gateway_auth.py tests/test_inflight.py tests/test_media.py tests/contracts tests/g tests/j tests/m tests/q tests/t tests/w` | 0 | `1532 passed, 2 warnings in 245.59s` |
| track-first: `pytest -q tests/w tests/t tests/q tests/m tests/j tests/g tests/contracts tests/test_*.py` | 0 | `1532 passed, 2 warnings in 262.08s` |
| `pytest -q tests/d/test_migration_mutants.py -k "well_formed or cannot_report"` | 0 | `2 passed, 49 deselected` (rest of tests/d not run: base predates the harness fix, per brief) |
| `make bench-test` (root) | 0 | `40 passed in 4.66s` |
| per-list focused runs during the work: `python -m tests.t.mutants` | 0 | `82/82 killed` |
| `python -m tests.m.mutants` (after item 4) | 1 → fixed | `76/77 killed; survived: ['stage_accepts_a_foreign_reference']` → retargeted, then `3/3 killed` on the changed ones |
| full shared list, detached: `INFRX_MUTANTS=all pytest -q tests/contracts/test_mutants.py tests/{m,q,j,w}/test_mutants.py tests/t/test_trace_mutants.py tests/g/test_mutants.py > f2r-a-mutants-final.log` at `d7790c6` | see below | see below |

List sizes at head (`--list`): contracts 278 (277 was a miscount; review 16), m 77, j 113, w 149, t 82, q 54, g 157.

**The earlier detached run against the WIP (`48d61b9`)**: `7 failed, 953 passed in 2211.08s` — the seven (`w: event_bytes_sized_for_ascii, visible_and_raw_collapsed`; `t: unsynced_bytes_survive_a_crash, a_crash_keeps_its_promise_count, crash_keeps_the_record_count, the_queue_counts_only_rows, a_clockless_sink_is_built`) were not WIP defects: that run collected its lists at start and copied the live tree per mutant while items 2 and 3 moved those anchors. Each was re-anchored, retargeted or retired (above) and killed in focused runs.

**Final full shared run at `d7790c6`** (`f2r-a-mutants-final.log`, started 2026-09-22T18:30:41Z, all seven lists: contracts, m, q, j, w, t, g): `967 passed in 2201.58s (0:36:41)` — no FAILED line.
**Touched lists re-run at `b4521c1`** after the R61 alignment and the W runner parameter (`f2r-a-mutants-final2.log`, 2026-09-22T19:31:04Z, contracts + w + m): `535 passed in 1052.62s (0:17:32)`. `6ab781b` changes only Q's `run_mutant` signature for a non-default path: `pytest -q tests/q` `61 passed`.
Full suite at `b4521c1`: `uv run --frozen pytest -q -p no:cacheprovider --ignore=tests/d` → `1532 passed, 2 warnings in 249.78s`, exit 0.

Not run: console targets (no `apps/app` file touched); `make integration --layer 1 --canary` (no `tests/integration` file touched); `make check` as a whole (its parts above; console parts not applicable).

## Trial merge onto `claude/backend-impl` (read-only: `git merge-tree`, extracted to scratch)

Against integration head `6fbf133` (M3 and W2 merged): textual conflicts only in `tests/m/test_mutants.py` (take backend's lines, `"stage_indexes_as_it_goes"` → `"stage_takes_a_ref_it_never_made"`, `78 + 30` → `77 + 30`), `tests/w/mutants.py` (take this lane's `run_mutant(mutant, suite=SUITE)` delegating to the shared runner) and `research/plan/08-contracts-v1-encoding.md` (keep backend's rows then this lane's five, now R79–R83; keep §5.1; take this lane's `spool segment **2**` line; keep both log blocks). With those resolutions and IR-A1…A9 applied on the scratch tree: `pytest -q --ignore=tests/d` → all green except the Q Valkey runner signature, now fixed on this lane (`6ab781b`; `tests/q/test_valkey_mutants.py` `7 passed` on the tree); `INFRX_MUTANTS=all` over contracts, m, w, w-loop, t, j → `7 failed, 945 passed` before IR-A9, the seven being M2 mutants the stricter shared rule now classifies (below), each then killed with IR-A9 applied (`5/5` and `3/3`); DEPLOY-01…05 `5/5 killed`; parity `28 passed`; M2's and M3's exported mediastore suites: parity case **passes** against `MediaPreparation` and `MediaUploads`, no blocked case.

## Integration requests

- **IR-A1** (`tests/contracts/mutants.py`, after merge; lane B IR-6): insert before the money-context block:
  ```python
  Mutant(name="DEPLOY-01", invariant="an empty deployment value is refused, not defaulted", file="config.py",
         old='        if raw.strip() == "":', new="        if False:", cases=("test_an_empty_deployment_value_is_refused",)),
  Mutant(name="DEPLOY-02", invariant="a deployment bound a zero would disable is refused", file="config.py",
         old="        if getattr(deployment, name) <= 0:", new="        if getattr(deployment, name) < 0:",
         cases=("test_a_deployment_bound_a_zero_would_disable_is_refused",)),
  Mutant(name="DEPLOY-03", invariant="a bad deployment value refuses before anything mounts", file="config.py",
         old='    validate_deployment(getattr(settings, "deployment", DEPLOYMENT_DEFAULTS), mode)\n', new="",
         cases=("test_a_bad_deployment_value_refuses_before_anything_mounts",)),
  Mutant(name="DEPLOY-04", invariant="the pool bounds are ordered (min == max is valid)", file="config.py",
         old="    if deployment.database_pool_min_size > deployment.database_pool_max_size:",
         new="    if deployment.database_pool_min_size >= deployment.database_pool_max_size:",
         cases=("test_the_pool_bounds_must_be_ordered",), dies_by=("RuntimeMisconfigured",)),
  Mutant(name="DEPLOY-05", invariant="a short console cursor secret is refused", file="config.py",
         old="    if _configured(secret) and len(secret) < MIN_CONSOLE_CURSOR_SECRET_CHARS:",
         new="    if _configured(secret) and len(secret) < 1:",
         cases=("test_a_short_cursor_secret_is_refused_without_echoing_it",)),
  ```
  DEPLOY-04's honest kill is the typed refusal of the equal case (declared); update the "Exactly **two** mutants declare one" comment to three.
- **IR-A2** (`tests/m/test_prepare.py`, M2's, after merge): give `conformance_factory` the hook `"materialized": materialized(adapter)` with
  ```python
  def materialized(adapter):
      async def hook(org_id, ref):
          seconds = 5.0 + int(fetch.digest_of(ref.handle.encode())[7:15], 16) % 97 / 10
          return await adapter.materialize(org_id, data_url(support.mp4(seconds=seconds)))
      return hook
  ```
  replace `PENDING_F2R` with `PARITY = "media_parity__staging_is_content_addressed_and_tenant_namespaced"`, and assert passes `== {*M1_CASES, PARITY}` and no `blocked` outcome. (Distinct clip lengths per handle matter: equal content gives equal digest-derived handles, and the relabelled-foreign-ref check would then resolve to the tenant's own object.)
- **IR-A3** (`tests/contracts/test_parity_console.py`, lane B's): add `"ACCOUNTING_REGIMES": records.AccountingRegime` to `SHARED_ENUMS`; delete `UNPAIRED_CONSOLE_ENUMS`, its introducing comment, and `test_the_console_only_vocabularies_are_recorded`.
- **IR-A4** (`apps/infrx-api/README.md:47`): drop `,video/mpeg` from the documented `ALLOWED_VIDEO_MIME` default.
- **IR-A5** (G composition): pass `pilot.processing_cache_dir` to M2's `ProcessingCache` and `DeploymentSettings.trace_spool_segment_bytes` to `SpoolTraceSink(segment_max_bytes=…)`; call `validate_pilot` (roots) from startup if not already.
- **IR-A6** (`tests/w/test_loop.py`, W2's): the `R58Engine` shim is obsolete (the shared `FakeEngine` now emits `{visible, raw}`); delete the class and use `FakeEngine(...)` directly at its three uses. Verified: `tests/w/test_loop.py` `45 passed` on the trial merge (with the shim still reading `content`, `test_ops_recover__the_shared_fake_engine_drives_the_same_loop` fails with an empty answer).
- **IR-A7** (`tests/m/test_uploads.py`, M3's): (a) `test_finalizing_never_replaces_what_the_handle_already_names`, `test_resolve_refuses_an_upload_that_is_not_finalized_even_if_its_handle_is_indexed`, `test_another_orgs_open_upload_state_does_not_leak` stage a builder ref under an upload handle, which R82 now refuses; seed the squat directly (`adapter.refs[(org, handle)] = squat`) - a content-addressed store can only reach that state from outside; (b) `conformance_factory` gains `"materialized"` (a real clip via `adapter.materialize(org, "data:video/mp4;base64,…")`, length derived from the handle as in IR-A2; add `import base64`); (c) delete `PENDING_F2R` and assert every case passes. Verified on the trial merge.
- **IR-A8** (`tests/w/loop_mutants.py` + `tests/w/test_loop_mutants.py`, W2's): the shared `Mutant` spells the declared kill mode `dies_by`; `_m(..., allowed_errors=...)` should pass `dies_by=tuple(allowed_errors)`, and the test reads `mutant.dies_by`. Verified: `test_loop_mutants.py` `11 passed`.
- **IR-A9** (`tests/m/mutants.py` + `tests/m/test_prepare.py`, M2's): under R83 seven M2 mutants were not honest kills. Declare the crash that *is* the defect: `zero_timescale_not_refused` `dies_by=("ZeroDivisionError",)`, `video_track_read_by_position` `("KeyError",)`, `ebml_uint_width_unchecked` `("OverflowError", "ValueError")`, `video_part_shape_unchecked` `("AttributeError",)`, `probe_deadline_removed` `("TimeoutError",)`; and make two test lookups assertion-shaped: in `test_prepare_persists_the_artifact_and_a_file_the_engine_can_open` and `test_a_prepared_artifact_that_is_gone_is_written_again`, `adapter.objects.objects[ref.storage_ref][1] == CLIP` → `adapter.objects.objects.get(ref.storage_ref, (None, None))[1] == CLIP`. Verified: all seven killed on the trial merge.
- **IR-A10** (new lists on the integration branch, not in this base): `tests/i/mutants.py` and `tests/contracts/v2/mutants_v2.py` still carry their own runner copies; delegate them to `tests/contracts/mutants.py` (`Runner(targets=…)`) as the other lists now do.

## Failure drill
Item 3: the reviewer's under-declared-envelope scenario with a writer blocked mid-batch (`test_the_queue_is_bounded_in_bytes_as_well_as_in_rows`): queued+in-flight serialized bytes ≤ `in_memory_metadata_bytes` ≤ reserve, every refusal counted `metadata_budget`, no `queue_full`; after unblocking, counters return to zero and the room is reusable. Item 4: `test_a_fault_at_the_payload_write_stages_nothing_and_the_retry_completes_it` — payload write fails, no payload recorded, object store unchanged; retry stages exactly the store's ref.

## Limits
- Fake/local only; no real DB clock, spool on real disk under load, or real M2 cache on the pilot host.
- `builders.request` now emits one `video_url` part per ref, but neither the fake nor M1's `stage` enforces part/ref pairing (W's `messages_for` does, at the engine). Enforcing it at `stage` is a small follow-up if wanted.
- The fake's `materialized` keeps the builder's `duration_s` as a stand-in for M2's measurement.
- The base serializes an envelope once more in the fake path to compute the charge; the spool passes the size it already has. An unserializable envelope is charged its declared size in the base (the writing sink refuses it earlier).
- Ruling numbers: this lane's five rulings are R79–R83 (renumbered at the merge; the lane-authored comments were updated in the post-merge follow-ups).

## Handback
Next unblocked: F2P from the accepted F2R commit. What the v1 Python base now guarantees: a delta payload is exactly `{visible, raw}` in the fake and W's adapter; trace accounting is one neutral implementation with a required clock, and no production class carries a test hook; the metadata reserve bounds real queued bytes; a store stages and attaches only media it produced; the candidate port, price-source port and judge sample shape are shared contracts with UUIDv4 unique sample ids; one mutation runner decides what a kill is for every Python list.

## Post-merge follow-ups (branch `codex/f2r-int-followups`, base `e1a33d1`)

Worktree `.claude/worktrees/codex-f2r-int`. Red at base, confirmed first: `pytest -q tests/w/test_loop_mutants.py` → `TypeError: Mutant.__init__() got an unexpected keyword argument 'allowed_errors'` (collection error), and `tests/m/test_uploads.py` `4 failed, 57 passed` (the three squat tests plus the pinned-blocked conformance case).

| Item | Commit | Change | Proof |
|---|---|---|---|
| IR-A8 | `b5b32c6` | W2 loop list: `allowed_errors` → shared `dies_by` (list + its test) | `tests/w/test_loop_mutants.py` `11 passed` (was a collection error) |
| IR-A6 | `a05c095` | `R58Engine` shim deleted; `FakeEngine` used directly at its three uses | `tests/w/test_loop.py` `45 passed` |
| IR-A2 | `3385b48` | M2 `conformance_factory` gains `materialized` (clip length derived from the handle); `PENDING_F2R` → `PARITY`; passes `== {*M1_CASES, PARITY}`, no `blocked` | parity case prints `pass` against `MediaPreparation`; `tests/m/test_prepare.py` `49 passed` |
| IR-A7 | `90bdf8d` | M3: the three squats seeded directly (`adapter.refs[(org, handle)] = …`); `materialized` hook; every mediastore case passes | `tests/m/test_uploads.py` `61 passed` |
| IR-A9 | `d03ebef` | five M2 `dies_by` declarations; two `objects[...]` lookups → `.get(..., (None, None))` | the seven + two neighbours `9/9 killed` |
| IR-A10 | `b6e7359`, `9d699dd` | `tests/i/mutants.py` and `tests/contracts/v2/mutants_v2.py` delegate to the shared runner. The shared `Runner` gains `layout` (I's repo-shaped copy: `apps/infrx-api/{infrx,tests,deploy}` + `models/marlin2b/serve.sh`, `package=""`); the subprocess gets `HOME` inside the copy; the shared module makes `infrx` importable when a list runs as a script (both script entry points were broken since the shared `_domain_deaths` import). Under the shared rule five mutants declare their kill mode: I `unset_mode_refuses` (`RuntimeMisconfigured`), `newline_injection_accepted`/`padding_accepted`/`shape_unchecked` (`TypeError`: the refusal-message case reads `"…" in shape_problem(...)`, which crashes on the missing refusal), v2 `the_pin_hardcodes_a_rate_card_version` (`ValidationError` from the admission's own pin/card validator) | I self-tests incl. "a no-op edit cannot kill the case that reads the real engine script" pass; v2 self-tests pass |
| IR-A1 | `83144ff` | DEPLOY-01…05 in the contracts list; DEPLOY-04 `dies_by=("RuntimeMisconfigured",)`; the "exactly N declare" comment updated | `5/5 killed` |
| IR-A3 | `fab0ec7` | `ACCOUNTING_REGIMES: records.AccountingRegime` in `SHARED_ENUMS`; `UNPAIRED_CONSOLE_ENUMS` + its test removed | `test_parity_console.py` `28 passed` |
| IR-A4 | `10159a3` | README `ALLOWED_VIDEO_MIME` default (line 47) and the type bullet (line 92) without mpeg | doc |
| rulings | `83b6bc2` | lane-authored `R63…R67` → `R79…R83` in 11 files and this report (not `tasklocal.py`/`vkharness.py`'s R63, the coordinator's Valkey ruling) | `grep` clean except the historical renumbering sentence |
| merge note | `40a8b67` | `upload_created.json` `accepted_mime` drops `video/mpeg`; the fixture guard pins it exactly | `test_fixtures.py` pass |
| review 11 | `4b444f2` | `trace_bounds__metadata_exhaustion_drops_with_counters` probe and both offers declare `metadata_bytes=0`; mutant `metadata_charge_skips_zero` | killed |
| review 12 | `b7008d2` | W mutant `content_alias_reintroduced` | killed by `test_f_contract__the_real_adapter_passes_the_exported_engine_suite` |
| review 13 | `e0664f5` | R83 amendment in the shared runner: (a) an `AssertionError` raised under `infrx/` (not `contracts/conformance/`) is undeclared unless in `dies_by` — `replay_returns_a_row_of_another_kind` retargeted (the case checks the label-side replay first, where the missing operation check returns the wrong row to the case), `drop_reason_falls_back_on_truthiness` declares `AssertionError` (the base's `_drop` guard); (b) `pristine(cases, runner)`: once per list+runner per process, the union of the list's named cases runs unmutated and a failure refuses every mutant of that list (`run_mutant` finds the list as the loaded module `MUTANTS` holding the mutant; one-off self-test mutants have none); (c) `_RAN` counts `error(s)` and pytest runs with `-rfE` — without `E` a fixture error listed no id and read as **survived** | new self-tests: `test_an_assertion_inside_the_package_is_not_the_cases_observation`, `test_a_list_whose_cases_fail_unmutated_is_refused`, `test_a_fixture_error_with_no_test_run_is_a_broken_runner` |
| review 14 | `7532424` | 08 §5 `spool segment **2**` restored; log entry | doc |
| review 15 | `bf7af62` | `EXT_MIME` drops `.mpeg`/`.mpg`; pinned in `test_the_default_video_allow_list_has_no_mpeg`; mutant `mpeg_extension_mapped_again` | killed |
| review 16 | `85cbc7d` | "contracts 277" → 278 above | doc |

Not changed here: IR-A5 (G composition, G2's). `PROCESSING_CACHE_DIR` is `PilotSettings.processing_cache_dir` (`infrx/contracts/limits.py:103`, default `""` = no local cache), read by `config.pilot_from_env` and validated by `validate_pilot` (`PATH_SETTINGS`, unset or absolute); no production code reads it yet (`prepare.py` builds `ProcessingCache("")` by default) — that is what W3/G2 wire.

### Commands and results at `9d699dd` (in `apps/infrx-api` unless noted; tails quoted from the logs)

`make api-env` (root) was needed first: this worktree's venv had been synced without extras by a bare `uv run`, and the first full run failed only `test_the_extras_are_installed_so_the_check_is_meaningful` (`1 failed, 2296 passed, 53 skipped`); after `make api-env` (exit 0) the extras import.

| Command | Exit | Tail |
|---|---|---|
| `uv run --frozen pytest -q -p no:cacheprovider --ignore=tests/d` | 0 | `2353 passed, 2 warnings in 447.19s` |
| legacy-first (`tests/test_*.py tests/contracts tests/g tests/i tests/j tests/m tests/q tests/t tests/w`) | 0 | `2353 passed, 2 warnings in 452.60s` |
| track-first (`tests/w tests/t tests/q tests/m tests/j tests/i tests/g tests/contracts tests/test_*.py`) | 0 | `2353 passed, 2 warnings in 444.27s` |
| `pytest tests/contracts` / `tests/m` / `tests/w` / `tests/i` / `tests/g` / `tests/q` | 0 each | `1022 passed` / `337 passed` / `124 passed` / `82 passed` / `320 passed, 2 warnings` / `113 passed` |
| `INFRX_MUTANTS=all python -m tests.contracts.mutants DEPLOY-01 … DEPLOY-05` | 0 | `5/5 killed` |
| `INFRX_MUTANTS=all pytest` per list (sequential, one process each): contracts, m, q, j, w, w-loop, t, g, g/ops | 0 each | `304 passed` / `215 passed` / `61 passed` / `124 passed` / `162 passed` / `69 passed` / `84 passed` / `164 passed` / `68 passed` |
| same, `tests/i/test_mutants.py`, `tests/contracts/v2/test_mutants_v2.py` (after the `9d699dd` declarations; before them `3 failed, 52 passed` and `1 failed, 70 passed`) | 0 each | `55 passed in 116.58s` / `71 passed in 105.96s` |
| same, `tests/q/test_valkey_mutants.py` | 1 | `6 failed, 76 passed` — all six fail identically on the base `e1a33d1` (checked on a `git archive` copy with this venv): Q2's list meets the shared rule; not fixed here (Q's file), see IR below |
| same, `tests/d/test_migration_mutants.py` | 1 | `83 failed, 1 passed` — every one `HarnessBusy` (the D port's lock was held by another checkout); D's list uses `assertion_kill`, which nothing here changed |
| `make api-mutants` (root, detached) | 2 | `92 failed, 1451 passed in 3301.00s (0:55:00)`: 83 D mutants + D's runner self-test = `HarnessBusy` (lock held by `codex-a1`); 9 Q2 Valkey — the six above plus `rebuild_does_not_count_what_it_indexed`, `the_state_snapshot_expires_visibilities` (`RuntimeError@vkharness.py`) and `the_waiting_age_is_reported_in_microseconds` (`CalledProcessError`), harness errors that passed in the per-list run minutes earlier (shared Valkey host under other lanes' load) |
| `make bench-test` (root) | 0 | `67 passed in 7.34s` |
| `ruff check` on every changed `.py` | — | 14 findings, all pre-existing (34 in the same files at base); none added |

### Integration requests (post-merge)
- **IR-A11 (Q2, `tests/q/valkey_mutants.py`)**: under the shared rule six mutants are not honest kills (identical at `e1a33d1`): `a_claimed_candidate_stays_claimable`, `acknowledge_does_not_remember_the_candidate`, `bytes_are_never_returned` swap a Redis command for one with the wrong arity (`ZCARD`/`SCARD`/`HSTRLEN` with extra arguments), so the Lua script dies with `ResponseError` before any invariant is observed — the edit should remove the effect instead (e.g. drop the `ZREM`/`SADD`/`HINCRBY` line); `a_filtered_claim_moves_the_kind_state` (`ResponseError`) and `a_flow_is_deleted_while_it_still_holds_work` (`AttributeError@valkey.py`) crash likewise; `an_unset_valkey_url_is_not_refused` dies by the client's own `ValueError` parsing an empty URL — declare it (`dies_by=("ValueError",)`) or assert the refusal type.
- **IR-A12 (I, `tests/i/test_prereqs.py:72-79`)**: `"…" in preflight.shape_problem(...)` should read `(preflight.shape_problem(...) or "")`, after which the three `TypeError` declarations in `tests/i/mutants.py` can go.
- **IR-A13 (G6B, `tests/g/ops/mutants.py`)**: still carries its own runner copy (`run_mutant` with `--tb=no`, so no death is classified); delegate to the shared runner like the others (not in this lane's file list).
- IR-A5 (G composition) unchanged.
