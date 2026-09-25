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
