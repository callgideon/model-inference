# LAB-SQL-LW7: WR-C6-VARIANTS + WR-C6-REQUEUE (task L3, lab-sql)

Lane `lab-sql-lw7`, branch `codex/w5-lab-sql-lw7`, worktree
`.claude/worktrees/codex-w5-lab-sql-lw7`, base `993d481c` (merge #50). Code head `21ca7882`.
Tasklocal keys: `l3` (PG 57502, `infrx-l3-postgres`) for every real-PG run and `make api-test`;
`l4` (PG 57503) for the E4 subset so both ran at once. No `l3sql` key exists in
`infrx/contracts/tasklocal.py`; `l3` is task L3's own key (never d1/55432, r1/r2, e8l, dlab).
Nothing hosted, no AWS/Vercel/box, no App/Lab file changed, every Lab switch still OFF
(no composition root touched).

## Commits (one per step)

| step | commit | what |
|---|---|---|
| 1 | `5237bfe7` | `0055_lab_variants_requeue.sql` (LOCAL-ONLY, R151/R201), `PgLabVariants` (new `infrx/state/lab_variants.py`), `PgLabImportJobs.requeue`, `tests/l3sql/test_lw7.py` (5 checks), `tests/d/test_code_mutants_lw7.py` (19 SQL mutants), the harness pin, the Makefile api-mutants line |
| 2 | `2ab48576` | `tests/l3sql/test_lw7_routes_pg.py` (optimizations route on real PG), `tests/l3sql/test_lw7_units.py` + `mutants.py`/`test_mutants.py` (4 Python mutants), the pilot wiring patch (evidence only) |
| 3 | `119155a4` | `POST imports/{id}/requeue` in `lab_datasets.py`, `tests/g/lab_datasets` requeue case + 5 route mutants, the real-PG requeue route test |
| 3b | `21ca7882` | ruff F401 in `tests/l3sql/test_mutants.py` |

Changed paths (15): `Makefile` (the one lw7 line), `apps/app/supabase/migrations/0055_lab_variants_requeue.sql`,
`apps/infrx-api/infrx/state/{lab_variants.py (new), lab_data.py (requeue port only)}`,
`apps/infrx-api/infrx/gateway/routes/lab_datasets.py` (requeue route only),
`apps/infrx-api/tests/d/test_code_mutants_lw7.py`, `apps/infrx-api/tests/l3sql/{test_lw7,test_lw7_units,test_lw7_routes_pg,mutants,test_mutants}.py`,
`apps/infrx-api/tests/g/lab_datasets/{test_lab_datasets,mutants}.py`, `tests/integration/test_harness.py` (pin),
`research/plan/evidence/l/LAB-SQL-LW7-*`.

## What 0055 does

- **WR-C6-VARIANTS** `lab_optimization_variants {provider_org_id}` -> `[{variant_ref,
  base_serving_ref, variant_serving_ref, changes, comparison}]`: the provider's `lab:variant`
  records (0029), oldest first; `comparison` is the NEWEST stored `infrx.variant_comparison.1`
  (0040: outcome = the verdict, reasons, `report_digest` = the report ref, performance,
  `optimization_claimed`, capabilities) plus its `comparison_digest`, or null. Another
  provider's variants never appear (R227); an unknown provider or none is `[]`.
  "The newest B4 experiment comparing the pair" in the brief is 0040's comparison row (R3's
  record that names the variant and its B2 report); no B4 experiment row names a variant.
- **WR-C6-REQUEUE** `lab_import_jobs.requeued_from uuid unique` (one successor per failed job)
  and `lab_import_requeue {job_id, new_job_id, provider_org_id, actor}`: the provider's
  `failed` job -> a NEW `queued` job with the same spec, the requeuer as `created_by` and as
  the spec's `actor`, naming its predecessor; the failed row stays failed (R243). A replay
  (or a concurrent double click: `on conflict do nothing`, then the successor is read) answers
  the one successor; queued/running/succeeded is `state_conflict` naming the state; another
  provider's or an unknown id `not_found`; no actor `invalid_request`; a new id another job
  already holds `state_conflict`. `new_job_id` is one argument beyond the brief's
  `(job_id, provider_org_id, actor)`: the gateway must copy the upload's rows to the new id
  BEFORE the job is claimable (`imports.work` reads `rows_key(provider, job_id)`), so the
  gateway chooses the id (uuid5 of the failed id: a retry is the same id, writes nothing).

## The routes

- `/lab/v1/optimizations` is already mounted by `lab_releases.py` over `records.variants`;
  the 503 lives in `pilot.ReleaseRecords.variants` (a composition root, not this lane's). No
  `lab_optimizations.py` was created (it would duplicate the mounted route). Proven by mounting
  `lr.register` over `PgLabVariants` on real PG; the pilot change is the wiring patch below.
- `POST /lab/v1/providers/{p}/datasets/imports/{id}/requeue` (in `lab_datasets.py`, behind
  `LAB_DATASETS`, off): acting provider first (developer+, WR-N-2), rows copied to the uuid5 id,
  then the RPC; answers `shown(new job)` (`import_id` = the new id, `running`).
  ponytail: the copy re-reads the upload (<= 64 MiB) through the gateway.

## Fail-first (recorded before each implementation)

| seam | red at the previous commit | green |
|---|---|---|
| `INFRX_D_TASK=l3 pytest tests/l3sql/test_lw7.py` (no 0055) | **5 failed**: `42883 function infrx.lab_optimization_variants(jsonb) does not exist`, `... lab_import_requeue(jsonb) does not exist`, `'PgLabImportJobs' object has no attribute 'requeue'` | 5 passed |
| optimizations route over `pilot.ReleaseRecords` (the base's composition) | **1 failed**: `503 {"refusal":"unavailable"}` | over `PgLabVariants`: 200 listing; a provider with none `{"data": []}` |
| `tests/g/lab_datasets` requeue case (no route) | **1 failed**: `assert 404 == 409` | 8 passed |

## Commands (exit codes, counts)

| # | command (cwd `apps/infrx-api` unless noted) | exit | result |
|---|---|---|---|
| 1 | `INFRX_D_TASK=l3 uv run --frozen pytest -q tests/l3sql/test_lw7.py tests/d/test_code_mutants_lw7.py` | 0 | **27 passed** (5 checks + 3 list meta + 19 SQL mutants killed, 0 survivors) |
| 2 | `INFRX_MUTANTS=all uv run --frozen pytest -q tests/l3sql/test_lw7_units.py tests/l3sql/test_mutants.py` | 0 | **8 passed** (2 units, 2 meta, 4 Python mutants killed) |
| 3 | `INFRX_D_TASK=l3 uv run --frozen pytest -q tests/l3sql/test_lw7_routes_pg.py` | 0 | **2 passed** (optimizations + requeue on real PG with the datasets role's pass) |
| 4 | `uv run --frozen pytest -q tests/g/lab_datasets/test_lab_datasets.py` | 0 | **8 passed** |
| 5 | `INFRX_MUTANTS=all uv run --frozen pytest -q tests/g/lab_datasets/test_mutants.py` | 0 | **24 passed** (21 mutants incl. 5 new `requeue_*`, 0 survivors) |
| 6 | root: `.venv/bin/python -m pytest -q tests/integration/test_harness.py -k migration_set` | 0 | 1 passed (0055 pinned after 0053; lab-live's 0054 slots in at merge) |
| 7 | E4, switches OFF: `INFRX_D_TASK=l4 uv run --frozen pytest -q -rs tests/g tests/w tests/contracts tests/i/test_packaging.py` | 0 | **2829 passed, 28 skipped, 0 failed** (22m48s; base 2828 + the 1 new lab_datasets case; skips key/stack-scoped as COMPOSITION-6) |
| 8 | root: `INFRX_D_TASK=l3 make api-test` | 0 | **6778 passed, 184 skipped, 10 xfailed, 0 failed** (1h31m) |
| 8b | the Makefile line: `INFRX_MUTANTS=all INFRX_D_TASK=l3 uv run --frozen pytest -q tests/d/test_code_mutants_lw7.py tests/l3sql/test_mutants.py` | 0 | **28 passed** (19 SQL + 4 Python mutants killed, 5 list meta) |
| 9 | WR patch applied, then reverted: `pytest tests/g/test_startup.py -k lab_releases`; `INFRX_MUTANTS=all pytest tests/g/test_mutants.py -k "lab_releases or well_formed or every_case"` | 0 / 0 | 1 passed / **19 passed** (3 new variants mutants killed, 2 re-cut anchors) |
| 10 | `uv run --frozen ruff check tests/l3sql tests/g/lab_datasets tests/d/test_code_mutants_lw7.py infrx/state infrx/gateway/routes/lab_datasets.py` | 0 | clean (the package's 13 other findings are pre-existing, none in these paths) |

Containers: `infrx-l3-postgres` was created and removed by this lane's harness runs; the
`infrx-l4-postgres` seen afterwards carries `codex-w5-lab-sql-lw8`'s label (started 16:19Z, after
this lane's E4 finished) and was left alone.

Process note: the scratchpad is shared by every lane of this workflow; my first detached run's
logs (`scratchpad/e4.log`, `api-test.log`) were clobbered by another lane writing the same
names, so both runs were killed and rerun under `scratchpad/lw7-sql/`. Only the reruns count.

## SQL mutants (tests/d/test_code_mutants_lw7.py, all killed on l3)

Variants: `lw7_variants_browser`, `_any_provider` (R227), `_other_records`, `_newest_first`,
`_identities_swapped`, `_oldest_comparison`, `_any_comparison`, `_digest_lost`.
Requeue: `lw7_requeue_browser`, `_any_provider`, `_any_state`, `_state_unnamed`,
`_predecessor_lost`, `_actor_kept`, `_creator_kept`, `_not_idempotent`, `_taken_id_answered`,
`_actor_unnamed`, `_successor_unmarked`.
`check_the_requeue_store_composes` (the port's round trip) is covered by the Python list, as
stated in `test_every_case_is_covered_by_a_mutant`.
Python (tests/l3sql/mutants.py): `lw7_variants_other_provider`, `_dropped`,
`lw7_requeue_new_id_dropped`, `_actor_dropped`.
Route (tests/g/lab_datasets/mutants.py): `requeue_rows_not_copied`, `_rows_other_provider`,
`_id_random`, `_other_provider`, `_actor_not_session`.

## Wiring requests

**WR-LW7-1 (coordinator; pilot.py composition + tests/g):** `/lab/v1/optimizations` still
answers 503 through `pilot.ReleaseRecords.variants`. Exact patch:
`research/plan/evidence/l/LAB-SQL-LW7-wiring-variants.patch` (144 lines):
`ReleaseRecords(d9, store, objects, variants)` with `variants()` delegating to 0055's
`PgLabVariants(connect)` on the gateway pool; `tests/g/test_startup.py` asserts the port is
`PgLabVariants` on the pool and the listing is the page provider's; `tests/g/mutants.py`
replaces `lab_releases_variants_invented` with `lab_releases_variants_absent`,
`_other_provider`, `_off_the_pool` and re-cuts `lab_releases_records_absent` /
`lab_releases_other_objects` to the new two-line call. Verified applied (row 9) then reverted.

**WR-LW7-2 (the Lab's `apps/lab`, not this lane's): "import again".** The views' copy
promises "import again under the same import id to resume", which R243's durable queue no
longer does (a re-POST replays the failed job). Proposed diff:

```diff
--- apps/lab/lib/services/datasets/port.ts
   importJob(provider: string, importId: string): Promise<Result<ImportJob>>;
+  /** WR-C6-REQUEUE: a failed import again as a new job (its id is the answer's importId). */
+  requeue(provider: string, importId: string): Promise<Result<ImportJob>>;
@@ httpDatasets
     importJob: (p, importId) => call(p, `/imports/${id(importId)}`, parseJob),
+    requeue: (p, importId) => call(p, `/imports/${id(importId)}/requeue`, parseJob, {}),
@@ offlineDatasets
-  return { preview: down, startImport: down, importJob: down, versions: down, ...
+  return { preview: down, startImport: down, importJob: down, requeue: down, versions: down, ...
--- apps/lab/lib/services/datasets/views.ts
-  ... fix the upload or its mapping, and import again under the same import id to resume.`
+  ... fix the upload or its mapping, and start a new import (a new import id).`
-  detail: `${job.error ?? "The import stopped."} Importing again under the same import id resumes from the staged rows.`
+  detail: `${job.error ?? "The import stopped."} "Import again" starts a new import of the same upload; this one stays failed.`
```

plus an "Import again" button on `app/(provider)/datasets/imports/[id]/page.tsx` for a `failed`
job only, posting through `requeue` and redirecting to the returned `importId`. A `rejected`
job is not offered it (the same rows reject again; ponytail: an `accept_rejects` override
on the requeue when a provider asks to publish the accepted rows).

**WR-LW7-3 (the Lab + R3; blocks the optimizations page reading 0055):** the Lab's
`http.ts` `VARIANT` requires `base: IDENTITY, variant: IDENTITY` (engine, version, hardware,
quantization, capabilities; mutant R4-X113). R3 never stores the two `Identity` objects:
`register` hashes them into the serving refs and `store` publishes only the refs and
`changes`. 0055 therefore returns both serving refs, `changes`, and the comparison's
`capabilities`; with the current Lab adapter the page reads "unavailable". Either
(a) R3's `store` persists both identities (a lab-sql table + an R3 write, the complete fix),
or (b) the Lab reads `base`/`variant` as `nul(IDENTITY)` and shows the serving refs and
`changes` when absent (drop/re-cut R4-X113). Proposed: (b) now, (a) when R3 is next touched.

**WR-LW7-4 (Makefile, applied in my owned line):**
`cd $(API) && INFRX_MUTANTS=all INFRX_D_TASK=l3 uv run --frozen pytest -q tests/d/test_code_mutants_lw7.py tests/l3sql/test_mutants.py`.
The `tests/l3sql` PG suites skip visibly without Docker (the requeue route test also needs
`INFRX_D_TASK` set, as N1's world does) and use their own databases `_lw7`, `_lw7r`, `_n1`.

## Ruling proposals (propose, never number)

1. **Requeue identity.** A failed import is requeued as a new job whose id is chosen by the
   gateway (uuid5 of the failed id) so its rows exist before it is claimable; one successor
   per failed job (`requeued_from` unique); the requeuer is the new job's actor. (Extends R243.)
2. **Variant listing.** `/lab/v1/optimizations` lists each variant with its newest stored
   comparison (0040) or null; a provider with none reads `[]` (200), never 503.

## Open issues

- WR-LW7-3: the Lab cannot parse the listing until base/variant identities are resolved.
- `GET imports/{id}` does not show `requeued_from` (the requeue's answer names the new id; the
  Lab redirects to it). Add it to `lab_import_job_json` when a page needs the chain.
- The migration number is 0055 by the brief; renumber at merge if 0054 (lab-live) moves.

## Estimate (remaining, including one verify round)

optimistic 0.5 h / likely 1.5 h / pessimistic 4 h, confidence medium. Basis: the code, tests and
mutants are done; what remains is the coordinator applying WR-LW7-1 (verified patch) and a
verify round on the merge (E4 ~23 min + the lw7 mutant line ~1 min); WR-LW7-2/3 are Lab-lane
work (~2-4 h there, not counted here), analogue D10-0025 at 1/2/5.
