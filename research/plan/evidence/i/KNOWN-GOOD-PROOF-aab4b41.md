# KNOWN-GOOD-PROOF (track I8): the rollback targets on the D10 schema

Lane `codex/known-good-proof`, base `b0cc5090`. Implementation head `aab4b41` (driver, tests,
mutants); the record, README and evidence commit follows it. Everything ran task-locally: the
PostgreSQL/Valkey of block `d10` (55442/55469, containers `infrx-d10-*`) and one container
`infrx-d10-migrated` with no published port (reached on its bridge IP, now removed). The hosted
database, the box, AWS, SSM and every secret were left alone.

## Problem (rehearsal at e607b705)

Once the window applies 0019+ to hosted, `infra/rollout/known-good.py` found no target:
`bda1586` and `4226315` carry migrations only through 0018 and had no `schema_proof`.

Before (this checkout, `--set MAX_VIDEO_SECONDS --set WORKER_CONCURRENCY`):

| target | --applied 0018 | 0022 | 0023 |
|---|---|---|---|
| bda1586 | KNOWN-GOOD (0) | NOT-KNOWN-GOOD `migrations` (1) | NOT-KNOWN-GOOD `migrations` (1) |
| 4226315 | KNOWN-GOOD (0) | NOT-KNOWN-GOOD `migrations` (1) | NOT-KNOWN-GOOD `migrations` (1) |

## Candidate schema

`git merge --no-ff codex/d10-followup` (c584f54a, 0022) gave 8ab5895a, and
`git merge --no-ff codex/door-revoke` (1d0a418d, 0023) gave c99286f0. File hashes: 0022 sha256
`1f44b4ae…` (matches the D10-FOLLOWUP verdict) and 0023 sha256 `0ea64faa…`.
`codex/door-revoke` later moved to acc53094, but that commit adds evidence only:
`git log 1d0a418d..acc53094 -- apps/app/supabase/migrations` is empty, so the proven 0023 is
still that branch's current 0023.

Migrated stand-in for hosted, built along the window's path: plain `postgres:16.14` (the
harness's digest), then `infrx/state/supabase_shim.sql`, then the CLI history table. Next,
`deploy/migrate.py` `plan` and `apply --expect` for 0001-0018 (digest 6995aef8…). Finally the
window's 0019-0023 went onto that 0018 database: pending 0019..0023, digest d393cdbe…, result
`applied: 0019, 0020, 0021, 0022, 0023`, exit 0.

## Method

`infra/runbooks/schema_proof.py <target>` builds a scratch tree and runs the old tests in it:

- It unpacks `git archive <target> apps/infrx-api` into a scratch dir.
- It puts the candidate's `apps/app/supabase/migrations` in place of the target's own.
- It copies in the candidate's `infrx/contracts/tasklocal.py`. This is the port registry only.
  The old registry has no `d10`, so its harness would fall back to the shared 55432.
- It runs `uv sync --frozen --all-extras` in the scratch tree.
- It runs EVERY one of the target's own `tests/d/test_*.py` (mutation runners excluded), each as
  its own pytest process, with `INFRX_D_TASK=d10`, `INFRX_D2_VALKEY_PORT=55469` and
  `INFRX_D2_VALKEY_CONTAINER=infrx-d10-valkey`.

The old harness has no mode that reuses an existing, already-migrated database. Instead,
`migrations.sql_for()` applies whatever sits at `<root>/apps/app/supabase/migrations`. Its own
template database is therefore 0001-0023, and the old PgJobStore, PgStreamStore, catalog and
operations SQL all run on that catalog. With `SCHEMA_PROOF_DSN` set to the stand-in above, the
driver also checks, read-only, that the database's history matches the candidate's files byte
for byte.

The driver also writes a probe (`PROBE` in the driver) into the scratch tree only. It covers the
one runtime path that no old suite walks end to end:

1. admit, prepare, claim;
2. `complete` (the old code's terminalize);
3. `get_owned`, then `read_result(org, outcome.result_ref)`, the gateway's own call
   (jobs.py:295 and relay.py:389);
4. another organization's read gets `not_found`, and wallet drift is zero.

## Result: PASS through 0023, both targets

```
SCHEMA_PROOF_DSN=<stand-in> apps/infrx-api/.venv/bin/python infra/runbooks/schema_proof.py <sha> --work <scratch>
```

| target | schema | suites | passed | deselected (SHAPE) | xfailed | failed | exit |
|---|---|---|---|---|---|---|---|
| bda15866e5700f3856d7142580da842fba9bbd23 | PASS | 26 PASS / 0 FAIL | 387 | 10 | 5 | 0 | 0 |
| 422631591845fbd66b590c73d5ff4150318d9d7a | PASS | 26 PASS / 0 FAIL | 387 | 10 | 5 | 0 | 0 |

Per-suite counts, the same for both targets (`x` = xfailed):

| suite | passed | suite | passed |
|---|---|---|---|
| adapter_units | 23 | leases | 12 |
| admission | 12 | operations_pg | 13 |
| catalog_pg | 37 + 1x | operations_units | 8 |
| composition_pg | 2 | outbox_relay | 20 |
| credit_jobstore_conformance | 7 + 3x | pgharness | 7 |
| credit_schema | 17 | schema_postgres | 17 |
| e3b_drills | 10 | schema_proof_probe | 1 |
| jobstore_conformance | 75 + 1x | settle | 19 |
| journal | 20 | settle_races | 10 |
| journal_races | 7 | settle_units | 7 |
| journal_units | 6 | signup | 21 |
| lease_races | 6 | store_requests | 3 |
| lease_units | 7 | streamstore_conformance | 20 |

`composition_pg` runs both regimes: `create_app` from settings, the startup probes, and
`/readyz` 200 over the pool. `outbox_relay` runs against the fake, memory and Valkey backends.

### The 10 cases that fail on 0001-0023 (findings, skipped by name, not passes)

First run: the whole old `tests/d` (mutants excluded) in one process at bda1586 on 0001-0023
gave `10 failed, 386 passed, 5 xfailed`. The same 10 cases plus the probe on the target's OWN
0001-0018 gave `11 passed`, so 0019-0023 causes each failure.

| case | cause | runtime path? |
|---|---|---|
| test_credit_schema: d1r_leaves_0001_0005_unchanged, credit_privileges, rerun_d1r_twice | They list the old catalog or re-apply an old migration; 0021 grants `infrx_runtime`/`infrx_monitor` | no |
| test_credit_schema: credit_units__no_conversion | The old scanner flags 0021's `public.consumer_jobs`, which reads USD and CREDIT columns together | no: a new function the old code never calls |
| test_schema_postgres: mutation_boundary, execute_surface | They list the old grants and EXECUTE surface | no |
| test_admission: media__uploads_finalize_once | The test's own `UPDATE infrx.media_uploads` violates 0019's `media_uploads_finalized_is_the_receipt` | no: the old runtime keeps uploads in memory (`media/uploads.py`) and never writes the table |
| test_settle: credit_spend__sql_settle_equals_v2 | It validates terminalize's raw doc strictly (`TerminalOutcome.model_validate`), and 0020 adds `result_expires_at` | no: the runtime reads only `_OUTCOME_FIELDS` (`jobstore._outcome`) |
| test_admission: results__write_once_owner_read; test_store_requests: put_result__write_once | They read a result before the job has an outcome, which 0020 refuses with `result_pending` | no: the runtime reads only a committed outcome's ref, and the probe proves that read PASSES |

Harness defect, found and fixed in the driver: the old `tests/d/vkstore.py` defaults to D2's
Valkey (`infrx-d2-valkey` on 55463). Two driver runs before the fix got
`test_outbox_relay` 7 failed / 13 passed on both targets, all in the Valkey-backend cases; the
same suite passed 20/20 when rerun on its own. Every run before the fix (the first whole-suite
run, the first two driver runs and the diagnosis reruns) therefore used D2's port 55463, which
is outside this lane. No `infrx-d2-valkey` was running, and the harness's lock and label guards
refuse a foreign container, but this is recorded as a lane-rule lapse. The committed driver
passes the task's own Valkey, and the proof table above comes from that fixed driver.

## Not proven (also in the README paragraph and the record's `not_proven`)

1. **A migration after 0023, or different 0022/0023 bytes.** Rerun the driver and add a new
   `through`. `known-good.py <t> --applied 0024` is NOT-KNOWN-GOOD (table below).
2. **The old release on the `infrx_runtime` login.** 0023 revokes `admit(jsonb)` and
   `claim_preparation(jsonb)` from that login on purpose; its header says a pre-W5 runtime there
   gets 42501 on every admission. The old releases never used that login, because 0021 creates
   it. A revert must run on the target's own env file, which R2 restores from the backup.
3. **Hosted rows written before the window.** The suites use fresh rows; the 0019-0023
   backfills are covered only by D10's own upgrade tests.
4. **The Supabase image** (`INFRX_D1_IMAGE=supabase`, a multi-GB pull). The proof ran on plain
   PostgreSQL plus the shim.
5. **The old worker as a running process.** Its store calls go through the same PgJobStore the
   suites and the probe drive. The `tests/g` and `tests/w` compositions use fakes, so they would
   add nothing on PostgreSQL.

## After (record carries `schema_proof` through 0023)

```
python3 infra/rollout/known-good.py <sha> --applied NNNN --set MAX_VIDEO_SECONDS --set WORKER_CONCURRENCY
```

| target | 0018 | 0022 | 0023 | 0024 |
|---|---|---|---|---|
| bda1586 | KNOWN-GOOD 0 | KNOWN-GOOD 0 (proven 0019-0022) | KNOWN-GOOD 0 | NOT-KNOWN-GOOD 1 (`migrations`) |
| 4226315 | KNOWN-GOOD 0 | KNOWN-GOOD 0 | KNOWN-GOOD 0 | NOT-KNOWN-GOOD 1 |

This is rollback.md step 1 without `--bundles`:

```
known-good.py --list --applied 0023 --set S3_MEDIA_BUCKET --set MAX_VIDEO_SECONDS \
    --set WORKER_CONCURRENCY --set LARGE_BODY_LIMIT --set DATABASE_POOL_MAX_SIZE
```

It gives 27af05a NOT-KNOWN-GOOD (preparation, migrations, record), 4226315 KNOWN-GOOD and
bda1586 KNOWN-GOOD, exit 0. `--bundles` (S3) was not run because it is outside this lane.

## Tests and checks

| command | result |
|---|---|
| `uv run --frozen --no-sync pytest -q tests/i/test_known_good_proof.py` at aab4b41 (no record entries yet) | 1 failed (`the_record_proves_both_targets`: `set() == {'4226315','bda1586'}`), 2 passed: fails before |
| the same command after the record change | 3 passed |
| `INFRX_D_TASK=d10 uv run --frozen --no-sync pytest -q tests/i --deselect <the 6 i8 cases>` | 224 passed, 6 deselected, 1 xfailed; exit 0 |
| `uv run --frozen --no-sync tests/i/mutants.py known_good_proof_ignores_through known_good_record_unproven schema_proof_trusts_moved_statements known_good_assumes_additive` | 4/4 killed |
| `python3 -m py_compile infra/runbooks/schema_proof.py` | ok |
| `schema_proof.py bda1586` / `schema_proof.py fff…f` | exit 2 / exit 2 |
| `python3 research/plan/scripts/validate_plan.py` | exit 0 |

Not run, because they need the i8 block (the pooler stack):

- test_observe `durable_truth_reads_…_through_the_pooler`
- test_pooler `the_computed_budget_…`, `transaction_scoped_patterns_…` and
  `the_composed_runtime_pool_…`
- test_privilege_probe `the_least_privilege_login_passes_…`
- test_rollback_drill `the_settlement_check_passes_either_regime_…`

None of them touches a path this lane changed.

New cases and the mutant that kills each:

| case | what it checks | mutant |
|---|---|---|
| `test_ops_recover__a_schema_proof_reaches_exactly_its_through` | With a proof through 0022 the target is KNOWN-GOOD at 0018 and 0022 and NOT at 0023 unless a 0023 proof exists; with no proof it is NOT at 0022 | `known_good_proof_ignores_through` |
| `…the_record_proves_both_targets_on_the_candidate_schema` | Both known-good targets carry a proof through at least 0022 | `known_good_record_unproven` |
| `…the_proof_driver_refuses_a_bad_target_and_a_moved_history` | A short or unknown sha exits 2. The history is compared with the files by version and by byte, and CLI-split rows pass only when each statement is part of the file | `schema_proof_trusts_moved_statements` |

## Record note

`known-good.json` says "append entries; never rewrite one", but `known-good.py` reads the FIRST
entry for a sha, so a second entry for the same sha would never be read. The `schema_proof`
object is therefore added to the two existing entries, and no existing field changed.

## Coordinator follow-up (hosted, not this lane)

After step 6 applies 0019-0023, run:

```
SCHEMA_PROOF_DSN=<hosted, read-only> schema_proof.py <sha> --suite tests/d/test_adapter_units.py
```

It prints `PASS schema history = the candidate's files 0001-0023` only if hosted holds exactly
the proven bytes. The suite part still runs task-locally.

## Estimate

Remaining for this lane: 0 h. Coordinator's hosted history check: 0.1/0.2/0.5 h
(optimistic/likely/pessimistic), confidence medium, because it depends on whether the pooler is
reachable from the coordinator host.
