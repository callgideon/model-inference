# W5-ADMIT-WIRING — W5 wiring request 4, gateway side (codex/w5-merge, 2026-09-25)

Base `3439c50c` (W5-MERGE, blocked on admit_ready). Head at time of checks `f5784d0c`. Task-local only:
`INFRX_D_TASK=w5` (postgres 55445, valkey 55491) and MinIO `infrx-w5-s3` on 127.0.0.1:55497
(`pgsty/minio@sha256:b6bfe723…`, `--pull never`, E2 local literals; the test cases create their own bucket). The container
was removed when the checks finished. Nothing was pushed. No hosted DB, box, AWS or SSM was used.

## Commits
| SHA | What |
|---|---|
| `e1bcc662` | `git merge c037a8f8` (claude/consumer-v1 integration tip: G8 lock-bound, E3C phase 2, contracts-anchor, runtime-login, M6 phase 1 retention) |
| `d58139f3` | the wiring: relay.py + pilot.py |
| `1d587970` | tests/g: `test_relay_readiness.py` (18 cases), `relay_support.World(readiness=True)`, and 5 G mutants |
| `f5784d0c` | pilot.py: moved the readiness key back ahead of `"jobs"`, so the `given_stores_replaced` anchor matches again |

## Merge conflicts (step 0)
- `Makefile` (api-mutants line), the only conflict. Resolved as a union: ours (`tests/w/test_w5_mutants.py`) plus theirs
  (`tests/m/test_retention_mutants.py`). No conflict touched any F2C cherry-picked path or `contracts/tasklocal.py`, so no
  ours-resolution was needed. Everything else auto-merged.

## Design
- `Relay` gets two new fields, `readiness` (a `ReadinessStore`) and `expectation` (an `AdmissionExpectation`). When
  `readiness` is set, `Relay.admit` calls `readiness.admit_ready(prepared, idem, expectation)` for text and media in both
  regimes. That is one transaction: admission, the checks against the pins, the media→content resolution, the manifest and
  the marker. A refusal is its typed `DomainError`, which the existing lifecycle table maps (`expectation_mismatch`→400
  `invalid_request`, `not_found`→404, `upload_expired`→410, `content_retiring`→503). No job and no hold exist after a
  refusal. The replay semantics are unchanged: R91 lookup first. A replay that `admit_ready` answers
  (`admission.replayed`) is attached as before, and `_ready` is ignored, so a pre-W5 job that replays with `None` is fine.
- `Relay.__post_init__` fails closed. When a readiness store is set, the expectation must be an `AdmissionExpectation` for
  the relay's own regime and, for CREDIT, for exactly `active_rate_card_version`. Anything else is a `ValueError` at
  construction.
- `pilot.adapters_from_env` composes the single `PgLifecycle` as both `lifecycle` and `readiness`, but only when it also
  builds the `PgJobStore` (they share one database). An injected job store gets no env readiness.
- `pilot.admission_readiness(rt, jobs, readiness)` builds the expectation from settings: the regime is
  `ACCOUNTING_REGIME`, and for CREDIT the card is `ACTIVE_RATE_CARD_VERSION` (R69; legacy names none). A CREDIT regime with
  no card raises `RuntimeMisconfigured(ACTIVE_RATE_CARD_VERSION)`. A `PgJobStore` with no readiness store raises
  `RuntimeMisconfigured`, in every mode, so there is never a silent `jobs.admit` fallback on PostgreSQL. A readiness object
  that is not a `ReadinessStore` is also refused.
- The pre-D10 `jobs.admit` door survives only for compositions over the contract fakes that have no readiness store
  (a marked `ponytail:` in `admission_readiness`). The existing g/m relay cases use that door on purpose
  (`World(readiness=False)` is the default, as the docstring says). New cases use `World(readiness=True)`, where the
  fake's preparation claim goes through `FakeLifecycle.claim_preparation`, which is W5's barrier.
- Why a separate `readiness=` instead of reusing `lifecycle=`: tests/m (not owned) inject a `FakeLifecycle(jobs=None)`
  as the ticket authority alongside fake job stores. Making the lifecycle the readiness store would break them, and a
  lifecycle is not coherent with a job store from another database.

## Fails before / passes after
- PG round trips (MinIO, `-k _pg__` over test_prep_worker.py + test_worker_main.py). Pre-wiring relay/pilot on the merged
  tree: **5 failed, 3 passed** (the 5 are those named in W5-merge-09d83a9.md, worker log `not_claimable`). Wired:
  **8 passed** (twice: at 1d587970 and at f5784d0c).
- tests/g new cases: under mutant `w5_marker_skipped` (the relay back on `jobs.admit`), **14 of 14 named cases fail**.
  Wired: 18 passed.

## Commands (serial)
| Command | Exit | Result |
|---|---|---|
| `INFRX_D_TASK=w5 INFRX_M_S3_ENDPOINT=… INFRX_M_S3_LOCAL_CREDS=1 uv run --frozen pytest -q -rxXs -p no:cacheprovider tests/w` (1d587970) | 0 | 308 passed, 3 skipped. The 3 skips are the mutant-list placeholders (empty parameter set). All 7 MinIO round trips ran, and the 5 listed cases passed |
| same env + `INFRX_MUTANTS=all` tests/w/test_w5_mutants.py test_prep_worker_mutants.py test_worker_main_mutants.py | 0 | 145 passed, with no broken_runner and no survivor |
| `INFRX_D_TASK=w5 uv run --frozen pytest -q -p no:cacheprovider tests/g tests/m tests/contracts` (no deselect, no MinIO) | 0 | 2550 passed, 27 skipped. All 27 skips are S3-gated M suites (test_s3 15, upload_restart_stack 9, s3_mutants 2, pilot_mutants placeholder 1). With MinIO, `tests/m/test_upload_restart_stack.py tests/m/test_s3.py` gave 50 passed. The G8 straddle case passed with no deselect |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/test_mutants.py` (1d587970) | 1 | 397 passed, 1 failed: `given_stores_replaced` was misdeclared (my readiness line had split its anchor). Fixed in f5784d0c and rerun: `python -m tests.g.mutants given_stores_replaced` → killed. The anchor audit over all 391 G mutants found 0 misanchored. The 5 new mutants were all killed |
| `INFRX_D_TASK=w5 uv run --frozen pytest -q tests/d/test_ready.py tests/d/test_composition_pg.py tests/g/test_composition.py` (+ tests/g/test_relay_readiness.py) at f5784d0c | 1 | 57 passed, 1 failed: `test_f_base__create_app_composes_the_pilot_from_settings_on_postgresql[legacy_usd]` (`unreachable at startup: price_source`). This failure is **pre-existing**: it fails the same way on e1bcc662 without this wiring |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS |

## New G mutants (tests/g/mutants.py), all killed
`w5_marker_skipped` (relay → `jobs.admit`), `w5_expectation_without_card` (CREDIT expectation with no card; dies by
`RuntimeMisconfigured`), `w5_pg_store_without_readiness`, `w5_readiness_store_unchecked`,
`w5_relay_expectation_unchecked`.

## Open issues / merge-order hazards
1. **URL-fetched video is refused `not_found` (404) by admit_ready on this tree.** `MediaPreparation.materialize` (store.py)
   writes a fetched source without registering a content row, so `admit_ready` cannot resolve it (probed with
   `World(readiness=True)` + `rs.VIDEO`: 404 in both regimes, 0 jobs). Uploaded video works. M6 phase 2
   (`codex/m6-phase2-merge`, "writers register every object") registers fetched sources in store.py. It must merge
   together with, or before, this wiring reaches the box. Not changed here because infrx/media is not owned.
2. A PostgreSQL video `admit_ready` → worker round trip is still not covered (W5 note 6). The integrated PG cases are text.
   The tests/m upload stack drills inject fake job stores, so they stay on the old door.
3. The `_admitted` CREDIT card/capability recheck is now redundant after `admit_ready`. It was kept for the `_resume` path.
4. Pre-existing: the `test_composition_pg[legacy_usd]` price_source failure (not this lane).

## Proposed ruling text
- **Admission readiness (gateway).** Every gateway admission composed over PostgreSQL goes through
  `ReadinessStore.admit_ready(request, idem, expectation)`. The expectation is composition-root configuration:
  `ACCOUNTING_REGIME`, and for CREDIT exactly `ACTIVE_RATE_CARD_VERSION`. A PostgreSQL job store without a readiness
  store, a CREDIT runtime with no card, or a relay whose expectation is not its own regime/card refuses to start. A
  refusal from `admit_ready` is answered as its lifecycle-table error, with no job and no hold. `jobs.admit` remains only
  for compositions over the contract fakes.

## Estimate
Remaining for W5 + wiring 4: merge this branch together with M6 phase 2 (hazard 1), then the E3C rerun. Optimistic 0.5 h,
likely 1.5 h, pessimistic 4 h. Confidence medium. Basis: all W PG round trips and mutant lists are green here, and the only
known gap is the M6 phase-2 source registration.

## Fix round (2026-09-25, the one fix round: 0-W5AW-1, 0-W5AW-2, 2-W5W-A1)

Base `f18c72f7`. Code head `fba75f6e` (one commit on top). The evidence commit follows it. Changed paths, all owned:
`tests/g/relay_support.py`, `tests/g/test_relay_readiness.py`, `tests/g/test_relay_readiness_pg.py` (new), `tests/g/mutants.py`.
No product code changed. Nothing pushed. Services: `INFRX_D_TASK=w5` (postgres 55445, valkey 55491) and the MinIO
`infrx-w5-s3` on 127.0.0.1:55497 (the `pgsty/minio@sha256:b6bfe723…` container that was already running under that name).
I removed it at the end. All real-service runs were serial.

**Not done: the merge.** `git merge 9e986016` into `codex/w5-merge` was denied by this session's permission system
("Modify Shared Resources"). I did not retry it and did not look for another way to do it. The merge is the coordinator's
to do. `git merge-tree --write-tree HEAD 9e986016` and `… HEAD cd10f098` both report zero conflicts.

### 0-W5AW-1 / 2-W5W-A1: the marker commits before the relay's attach
- **Reproduced.** On `f18c72f7`, the new case `test_w5_admit__the_worker_prepares_a_video_the_moment_its_marker_commits[upload-*]`
  runs `World.prepare` (claim through the readiness store, then M's `prepare`) inside a wrapped `admit_ready`. It got
  `{'prepared': 'not_found', 'manifest': ['upl_fake…1']}` in both regimes: 2 failed. This matches the reviewers' probe.
- **Root cause: the in-memory World only. PostgreSQL has no such window.** In 0019, `admit_ready` calls `bind_source` for
  each source (`0019_upload_readiness.sql:890`). `bind_source` inserts the job's 0003 `job_media` rows (`:780`) in the
  same transaction as the marker insert (`:893`). The worker's `MediaPreparation.prepare` reads `self.attached(job_id)`
  (`media/prepare.py:419`), which is `by_job` and then `PgAttachments.get` (`media/store.py:344`, wired at
  `worker/__main__.py:132`). `PgAttachments.get` returns exactly those rows. The relay's later attach is
  `PgAttachments.put` over rows that are already bound: the same handles and digests, so it is a no-op. The World
  composed M's adapter with no durable attach record (`attachments=None`), so `attached` saw only the relay's in-process
  attach. So the reviewers' statement that "the PG round trip passes only because the attach wins the race" is refuted
  by the PG case below.
- **Fix (tests/g).** `relay_support.ManifestAttachments` makes the World's attach record the `FakeLifecycle` manifest, as
  0019 makes `job_media` the manifest. Its `put` behaves like `PgAttachments.put` over bound rows: the same handles and
  digests in order are a no-op, and anything else is a `conflict`. `World(readiness=False)` is unchanged.
- **Regressions.**
  - fake, `…the_moment_its_marker_commits[upload-legacy_usd|upload-credit]`: 2 failed before, 2 passed after.
  - PostgreSQL, `test_w5_pg__a_claim_the_moment_the_marker_commits_prepares_the_manifest[upload]`: wraps the composed
    gateway's `PgLifecycle.admit_ready` (`app.state.runtime.relay.readiness`). Inside the window, before the relay's
    attach, it runs the worker's own `PreparationRunner` from `infrx.worker.__main__.compose(from_env(box.settings))`
    (a marker-gated claim, M over `PgAttachments` and MinIO, and the fake vLLM `/tokenize`). The results: `attached` ==
    the one-source manifest, `cause='prepared'` with prompt tokens, `POST /v1/jobs` 202, and the row is `queued`. It
    passed on the lane tree with no product change.
  - Oracle, run on scratch only and not committed: the same case with the job's `job_media` rows deleted inside that
    window gave `PreparationResult(refusal='not_found', detail='not_found: no staged media for job …')`, the reviewers'
    symptom, and the case failed.
  - Mutant: the new fake and PG cases are named under `w5_marker_skipped`. `python -m tests.g.mutants w5_marker_skipped`
    → **killed** (16 failed, 4 skipped, 2 xfailed; the 4 skips are the PG cases, because the runner's copy inherits no
    service env). With the w5 services, in a scratch copy with that mutant applied, the PG file gave 2 failed (round trip:
    the job never ends, `AssertionError: None`; window case: `the relay did not admit through admit_ready`) and 2 xfailed.

### 0-W5AW-2: URL-fetched video is 404 on the lane tree
- **Reproduced** on `f18c72f7`: `…the_moment_its_marker_commits[fetched-*]` 404 `not_found` at admission (2 failed). PG
  `test_w5_pg__…[fetched]` 404 on `POST /v1/jobs` (2 failed with `--runxfail`).
- **Committed (W5 note 6).** The PG video round trip `test_w5_pg__the_worker_process_runs_a_video_admit_ready_admitted`
  covers the upload and fetched (`data:`) shapes: `create_app` gateway → `POST /v1/jobs` → `python -m infrx.worker`
  behind the marker → settled. It checks a one-source readiness doc, `succeeded/completed/settled`, no `refused` or
  `not claimed` line in the worker log, and a clean SIGTERM drain. The fetched shape of every new case has a strict,
  conditional merge-order tripwire: `M6_PHASE2 = hasattr(MediaStaging, "_register")` (the M6 phase-2 writer
  registration, 25826ab9). Without it, the fetched cases must fail by assertion (xfail strict). With it, the marker is
  inert and they must pass.
- **Verified on the scratch merge** of `f18c72f7` with `9e986016` (the verifier's clone, merge `d588af2c`, plus these
  three files): `tests/g/test_relay_readiness.py tests/g/test_relay_readiness_pg.py` gave **26 passed** (22 fake + 4 PG,
  fetched included). The verifier's `test_zz_probe_race.py` + `test_zz_urlvideo.py` gave 6 passed there (the lane tree:
  race 2 passed with this World, url 2 failed 404).
- **Status: not fixed on this branch.** The merge was denied (above). The coordinator merges `codex/w5-merge` only
  together with, or after, an integration tip that contains 25826ab9.

### Commands (fix round; the counts below are mine)
| Command | Exit | Result |
|---|---|---|
| `uv run --frozen pytest -q tests/g/test_relay_readiness.py -k moment_its_marker` at f18c72f7 + the new case, before the World fix | 1 | 4 failed: upload `not_found` ×2, fetched 404 ×2 |
| same, after the World fix | 0 | 20 passed, 2 xfailed (whole file) |
| `INFRX_D_TASK=w5 INFRX_M_S3_ENDPOINT=http://127.0.0.1:55497 INFRX_M_S3_LOCAL_CREDS=1 uv run --frozen pytest -q -rxX tests/g/test_relay_readiness_pg.py` | 0 | 2 passed, 2 xfailed (3 of 4 runs; see the note) |
| same env, `tests/w` (`-rxXs`) | 0 | 308 passed, 3 skipped (the 3 empty mutant parameter sets); the 7 MinIO round trips ran |
| same env + `INFRX_MUTANTS=all` over `tests/w/test_w5_mutants.py test_prep_worker_mutants.py test_worker_main_mutants.py` | 0 | 145 passed: no broken_runner and no survivor (the rerun; see the note) |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/test_mutants.py` (no service env) | 0 | 398 passed: every G mutant killed, and the list is well-formed and covers every case |
| `INFRX_D_TASK=w5 uv run --frozen pytest -q tests/g tests/m tests/contracts` (no deselect, no MinIO) | 0 | 2552 passed, 31 skipped, 2 xfailed (was 2550/27: +2 fake passes, +4 PG skips without MinIO, +2 fetched xfails) |
| `INFRX_D_TASK=w5 uv run --frozen pytest -q tests/d/test_ready.py tests/d/test_composition_pg.py tests/g/test_composition.py tests/g/test_relay_readiness.py` | 1 | 59 passed, 2 xfailed, 1 failed: the pre-existing `test_composition_pg[legacy_usd]` `price_source` |
| `python -m tests.g.mutants w5_marker_skipped` | 0 | killed (16 failed, 4 skipped, 2 xfailed) |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS (rerun after this section was written) |

**Note, the first serial batch (load average 80+ on this 16-core host, other sessions' suites running).** In that batch
the W mutant lists gave `4 failed, 141 passed`. My log tail showed two of the failures:
`test_worker_main_mutants.py::test_pg_mutant_is_killed[main_pool_not_opened]` and `[pilotbox_worker_not_awaited]`. The
PG readiness file run right after them gave `4 errors` in 2.9 s at fixture setup; the error text was cut off by my
`tail`. I reran both in order (the mutant lists, then the PG file right after) at load 23-30: 145 passed, then 2 passed
/ 2 xfailed. I did not reproduce either failure. No test in either list was changed by this round.

### Proposed ruling correction (R132 draft, the gateway sentence)
The draft says the gateway writes the marker through `admit_ready` "for text and media in both regimes (W5 WR-4 / E3C
F-3)". The implementation matches that, and the draft omits two facts it relies on. Proposed replacement for the
gateway sentence:
"The gateway admits every request through `ReadinessStore.admit_ready(request, idem, AdmissionExpectation)`, for text
and media in both regimes. The expectation is composition-root configuration and never a request field:
`ACCOUNTING_REGIME`, and for CREDIT exactly `ACTIVE_RATE_CARD_VERSION`. In that one transaction the job's source rows
(0003 `job_media`, the attach record M's `prepare` reads) commit together with the manifest and the marker, so the
barrier never opens on a job whose sources the preparing worker cannot read. The relay's later attach is a write-once
no-op. A PostgreSQL job store without a readiness store, a CREDIT runtime without a card, or a relay whose expectation
names another regime or card refuses to start. `JobStore.admit` and `CreditJobStore.admit_credit` never write a marker,
and the gateway never falls back to them on PostgreSQL."

### Wiring requests (new; outside the owned paths)
- **WR-F1 (W5, `infrx/worker/preparation.py:116`, comment only).** The `ATTACH_WAIT_S` comment "The attach lands just
  after the admission commits" describes the pre-D10 path. Patch: `# Pre-D10 only: the attach lands just after the
  admission commits. With a ReadinessStore, 0019 commits the manifest's job_media rows with the marker.` Test: none
  (comment).
- **WR-F2 (F2C, `contracts/fakes/lifecycle.py`, optional).** Give `FakeLifecycle` the attach view (`get`/`put` over
  `d.readiness`, as `relay_support.ManifestAttachments` does), so every composition over the fake has 0019's property.
  Test: `tests/g/test_relay_readiness.py::…the_moment_its_marker_commits` with the World using it.
- **Not requested:** preparing from `readiness(job_id).sources` inside the worker (the reviewers' first option). On
  PostgreSQL those are the same rows as `attached`. The PG window case is the tripwire if a later migration ever
  separates them.
- **Merge order (coordinator):** `codex/w5-merge` goes in together with, or after, 25826ab9. The fetched tripwire makes
  a wrong order fail loudly.

### Estimate
Remaining: the coordinator's merge with the integration tip (zero conflicts by merge-tree), then the E3C rerun.
Optimistic 0.5 h, likely 1 h, pessimistic 3 h. Confidence medium. Basis: every owned suite and mutant list above was
rerun here, and the post-merge result was checked on the scratch merge.
