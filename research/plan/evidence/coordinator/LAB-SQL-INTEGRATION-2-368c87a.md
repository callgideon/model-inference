# LAB-SQL-INTEGRATION-2 — the consuming lanes on lab-sql-lw3's real stores (LW4, post-launch Lab)

- Base `6ab5ee47` (merge batch #16: 0041 C2-RPC, 0042 D8 ledgers, 0043 reads/judge-runs door/SR-R1-1; ALL LOCAL-ONLY) · code head `368c87ab` · branch `codex/w5-lab-sql-integration-2`, worktree `.claude/worktrees/codex-w5-lab-sql-integration-2`.
- Continuation: items 1–7 were committed by a previous implementer (cut by a login expiry before the evidence); this session re-read each commit critically, reran every suite on its key and every mutant list, decided item 8 (WR-N3-4) and wrote this file. No inherited commit needed a fix (review notes per item below).
- Commits, one per item: `d7a682a2` WR-C2-5 · `6652e7da` WR-P1-D8 · `cc4a17fa` WR-P3-D8 · `3d668b5b` WR-P3-R184 · `38fa9a7b` WR-B3-D8 · `d91cce76` WR-P2-D8 · `5567585d` WR-J3-D8 · `368c87ab` WR-R1-3 · (evidence commit) WR-N3-4 = decision NOT a plain swap, no code.
- Task-local only: lab-c2 (PG 57505 + MinIO `infrx-lab-c2-s3` 57506, this key's own exited container restarted), p1 57527, p3 57530/57531, b3 57522, p2 57528/57529, j2 57511, r1 57532/57533, n3 57518. No migration, no `infrx/state/lab_*.py`, no composition root, no `apps/lab`, no `infrx/lab/control`, nothing hosted. Everything touched is Lab-only; nothing is reachable from the launched App/API (no composition root changed; `lab_submission`/`lab_content` stay absent = OFF outside the tests' own databases).

## Changed paths (all owned by this lane's items)
- `infrx/pipelines/teachers/__init__.py` (P2): `collect` records per-item failures with `ledger.record_failures(run_id, failures)` (D8 0042 `lab_teacher_failures`, append-only) before importing labels; `TeacherWiring.ledger` is D8's `PgTeacherLedger`.
- `infrx/judge/calibration/{report.py,__init__.py}` (J3): `publish(ledger, results, feedback, *, provider_org_id, org_id, judge_model, rubric_version)` = the report job: the grantor's report, its `calibration()` stored with `PgJudgeLedger.put_calibration` under (provider, grantor, judge model, rubric version).
- Tests: `tests/content/{world_pg.py (new), world.py, test_content.py, conftest.py, mutants.py}`; `tests/p/annotations/test_annotations_pg.py`; `tests/p/training/{test_training.py (+1), test_training_services.py (+1, swap), world.py (fake R184), mutants.py (+1, 1 re-declared)}`; `tests/b/checkpoints/test_checkpoints_pg.py`; `tests/p/teachers/{fakes.py, test_teachers.py, test_teachers_pg.py (new), mutants.py (+1)}`; `tests/j/calibration/{test_calibration.py (+1), test_calibration_pg.py (new), mutants.py (+3)}`; `tests/r/routing/test_routing_pg.py (new)`.

## Per item: seam check · real-PG counts · mutants
Seam checks 1–7 are the previous implementer's recorded red runs (commit messages + scratch notes), re-verified here by reading the diffs; counts are this session's reruns at `368c87ab`.

| item | seam check (red before) | real-PG rerun (this session, exit 0) | mutants |
|---|---|---|---|
| WR-C2-5 | pg world first runs red on world bugs (missing column order, job-handle collision), then 22/22; SQL mutants applied in memory (scratch plugin): `c2_redeem_retention_ignored` → `retention_shrunk[pg]` fails; `c2_redeem_any_user` → `redeemable_by_the_user[pg]`; `c2_held_after_expiry` → `ttl[pg]`, `live_ref_holds[pg]` | `INFRX_D_TASK=lab-c2 INFRX_C2_S3=1 pytest -q tests/content`: **74 passed** (22 cases × memory/s3/pg + 8), 0 skipped | 34 mutants, `test_mutants.py` 38 passed; runner copies `-m 'not s3 and not pg'` (the pg param would mis-declare `self_no_op` under the key) |
| WR-P1-D8 | case asserts `infrx.lab_label_events` rows; on `FakeLabelLog`: `AssertionError {}` | `INFRX_D_TASK=p1 pytest -q tests/p/annotations`: **17 passed** (incl. `{label: 7, assign: 6, review: 6}`) | 73, 75 passed (no new fake case: the PG file is outside the runner, T2I/G8) |
| WR-P3-D8 | on `FakeRunLedger`: 2 failed (`None == 'cancelled'`; `(None, []) != ('submitted', [('held', 1)])`) | `INFRX_D_TASK=p3 pytest -q tests/p/training`: **19 passed** (TCP protocol server 57531) | 78, 80 passed |
| WR-P3-R184 | new fake case red on the old fake: `DID NOT RAISE StateConflict`; PG twin first run `Forbidden` (operator profile absent in D7's seed → the test inserts it) | in the 19 above: `test_p3_pg_an_ambiguous_run_ends_only_on_an_operators_written_confirmation` (hold kept while ambiguous; non-operator Forbidden, blank ref InvalidRequest; operator move fails + releases in one transaction; confirmation kept on the event) | new `p3_not_found_released` (dies StateConflict) + `p3_not_found_is_failed` re-declared (dies Forbidden), both name the R184 case |
| WR-B3-D8 | on the fake ledger: 2 failed (`[(0,)] != [(1,)]`) | `INFRX_D_TASK=b3 pytest -q tests/b/checkpoints`: **11 passed** | 37, 39 passed |
| WR-P2-D8 | fake seam `[] == [...]` red; PG carry case red without `record_failures` (scratch edit, reverted) | `INFRX_D_TASK=p2 pytest -q tests/p/teachers`: **13 passed** (teacher fake 57529) | 40, 42 passed; new `failures_unrecorded` |
| WR-J3-D8 | fake seam `ImportError: publish`; PG door case red without the `await` (scratch): `uncalibrated != calibrated` | `INFRX_D_TASK=j2 pytest -q tests/j/calibration`: **15 passed** (door: developer+admin see; viewer/consumer/foreign/anon 42501; foreign provider none; latest wins) | 35, 37 passed; new `job_stores_nothing`, `job_grantor_is_the_provider`, `job_reports_every_org` |
| WR-R1-3 | SQL mutants in memory (scratch plugin): `q_eligible_revoked` → canary case fails (revoked subject still on candidate); `q_record_rewritten` → killed; `q_active_to_service`, `q_active_unpinned` survive (D8's own `tests/d` checks own them) | `INFRX_D_TASK=r1 pytest -q tests/r/routing`: **22 passed** (Router over `PgRoutingReleases` on the `infrx_runtime` login) | 24, 26 passed (PG file outside the runner) |
| WR-N3-4 | see below: a reader-only swap FAILS OPEN | `INFRX_D_TASK=n3 pytest -q tests/n/lineage`: **9 passed** (unchanged code) | 34, 36 passed |

Every mutant list: `INFRX_MUTANTS=all uv run --frozen pytest -q <track>/test_mutants.py` (with the track's key; r1's with none, its runner never touches PG), exit 0, **0 survivors**; `well_formed`/`every_case` green in each. New fake-world cases and their mutants: `test_p3_an_ambiguous_run_ends_only_on_an_operators_written_confirmation` (`p3_not_found_released`, `p3_not_found_is_failed`), `test_j3__the_report_job_stores_its_configurations_calibration` (`job_*` × 3); P2's changed collect case (`failures_unrecorded`). New PG cases (`*_pg.py`, `test_training_services.py`) sit outside the runners by the T2I/G8 pattern; their oracles are those mutants plus the scratch red runs above.

`ruff check` over all 22 changed files: clean.

## WR-N3-4 — decision: NOT a plain swap (no code; recorded)
`lineage.blocked/permitted` cannot simply read `PgSampleRestrictions.blocked/permitted`:
1. **The writers are elsewhere.** N3 writes its restrictions as objects: `select` writes `content_until` into `lab/<p>/lineage/samples/<id>.json`, and `tombstone`/`reconcile` write `lab/<p>/lineage/tombstones/<id>.json` (`_stone`). 0041's `lab_blocked_samples` reads only `infrx.lab_sample_tombstones`/`lab_sample_bounds`, filled only by `lab_tombstone_samples`/`lab_bound_samples`, which nothing in N3 calls. Swapping only the readers makes every tombstone and bound invisible.
   **Seam check (scratch test on n3, deleted, not committed):** `test_n3_pg_selection_revocation_and_tombstones` followed by `PgSampleRestrictions(connector(dsn))` after reconcile + re-grant → `lineage.permitted = set()` but `PgSampleRestrictions.permitted = ['b8c6fe57-…']`, `blocked = {}`: the tombstoned sample is resurrected (1 failed, `AssertionError: reader-only swap resurrects the tombstoned sample`).
2. **Signatures differ.** N3's `blocked(objects, *, sample_ids, now)` takes sample ids and a caller clock (`status`, `export_evidence` pass ids; `export_evidence` has no dataset ref in hand until it reads the export record); 0041 takes a `dataset_ref` and uses `infrx.now()`. `permitted(store, objects, …, now)` has three callers in `infrx/datasets/versions` (N2) that pass `objects`.
3. So the change is a port move across N3 + N2 (a `restrictions` port threaded through `select` → `bound`, `_stone` → `tombstone`, `blocked/permitted/status/export_evidence` → the PG reads; the object tombstones kept or migrated), the lab_pipelines route composition and two mutant lists — the datasets lane's work, not an integration swap. Filed as **WR-N3-5** (below). The ponytail ceiling in `lineage.blocked` (a listing per read) stands until then.

## Critical review of the inherited commits (no fix needed)
- C2 world: `_Jobs.__setitem__` edits `infrx.jobs` under `session_replication_role = replica` (test-only; the fake's dict semantics: the world moves a job's model/age). Each case gets its own template copy; the DB clock is `infrx_test.freeze/advance`.
- P2: `collect` now REQUIRES `record_failures` on its ledger: a teacher composition wired with plain `PgJudgeLedger` would raise `AttributeError` at the first per-item failure → WR-P2-D8-C (composition-2).
- J3: `publish` has no production caller yet (the report job's scheduling is composition-2's) → WR-J3-D8-C.
- P3 R184 fake mirrors 0042 (operator + non-blank `confirmation_ref`; hold released in the same move; no release while ambiguous).
- R1: the real half runs on the `infrx_runtime` login (`set session authorization`), never `service_role`; each case stops its release in `finally`.

## Full suite
| command | exit | result |
|---|---|---|
| `INFRX_D_TASK=r1 INFRX_D2_VALKEY_CONTAINER=infrx-r1-valkey INFRX_D2_VALKEY_PORT=57533 make api-test` (1:29:11) | 2 | **6434 passed, 150 skipped, 9 xfailed, 46 failed, 0 errors**; none in tests/content, p, b, j, r, n |
| — 43 × `tests/i/test_mutants.py` | | known: its pristine baseline fails under a non-default key (`test_ops_steps.py` monitor env/SSM cases) — recorded, as briefed |
| — 1 × `tests/i/lab/test_lab_packaging.py::test_i2l__the_live_lab_edge_…` + 2 × `tests/i/lab/test_mutants.py` (`lab_unit_part_of_the_gateway`, `control_public_bind`: broken_runner on that baseline) | | port race: `OSError: [Errno 98] Address already in use` on the e3l upstream port (free at the `_free` check, taken by another agent before bind). Rerun alone: `tests/i/lab/test_lab_packaging.py` 11 passed; the two mutants 2 passed (killed) |
| (previous implementer, default key, 17:46) `make api-test` | 2 | 50 failed/8 errors: +7 `test_outbox_relay[valkey]` (foreign `infrx-d2-valkey`) and 8 tests/i pooler/observe errors — avoided here by the r1 key's own Valkey |

Isolation: foreign `infrx-d2-valkey`, `infrx-d1-postgres`, `infrx-t2f-*`, `infrx-m5-s3`, `infrx-q3-valkey`, `infrx-d2-postgres` and the `infrx-e5l_*` volumes were not touched. `infrx-b3-postgres` present after the run carries another checkout's label (`…/scratchpad/m17`, created 18:55Z after this lane's b3 run finished) — foreign, not touched. This lane's `infrx-lab-c2-s3` is stopped again (as found); every pgharness container this run created was removed at exit.

## Wiring requests
- **WR-P2-D8-C (composition-2)** the teacher composition passes `ledger=PgTeacherLedger(connect)` (not `PgJudgeLedger`) into `TeacherWiring`; `collect` calls `record_failures`. Proof: `tests/p/teachers/test_teachers_pg.py` (p2).
- **WR-J3-D8-C (composition-2)** the judge report job calls `infrx.judge.calibration.publish(PgJudgeLedger(connect), results, feedback, provider_org_id=…, org_id=<grantor>, judge_model=…, rubric_version=…)` per configuration; the Lab's `public.lab_judge_runs` door shows the latest. Proof: `tests/j/calibration/test_calibration_pg.py` (j2).
- **WR-R1-3-C (composition-2, flag OFF)** R1's admission hook composes `Router(PgRoutingReleases(<infrx_runtime login connector>), ShadowRunner)`, behind its default-OFF flag; never `service_role`. Proof: `tests/r/routing/test_routing_pg.py` (r1).
- **WR-P3-D8-C / WR-B3-D8-C / WR-P1-D8-C (composition-2)** the training/checkpoint/annotation compositions use `PgRunLedger` (with `lab_submission` + a named USD payer budget), `PgCheckpointLedger`, `PgLabelLog` respectively. Proofs: `test_training_services.py` (p3), `test_checkpoints_pg.py` (b3), `test_annotations_pg.py` (p1).
- **WR-C2-2 (unchanged, composition)** `Retention(..., holds=ContentAccess(PgContentRefs(connect), retention).holds)`; proof now `tests/content` pg world (lab-c2).
- **WR-LSI2-MK (Makefile, optional)** a `lab-sql-integration` target running the keyed real-store suites (`INFRX_D_TASK=lab-c2 INFRX_C2_S3=1 … tests/content`, `p1 tests/p/annotations/test_annotations_pg.py`, `p3 tests/p/training/test_training_services.py`, `p2 tests/p/teachers/test_teachers_pg.py`, `b3 tests/b/checkpoints/test_checkpoints_pg.py`, `j2 tests/j/calibration/test_calibration_pg.py`, `r1 tests/r/routing/test_routing_pg.py`); no mutant-list line is needed — all eight lists are already on `Makefile:21` (api-mutants).
- **WR-N3-5 (datasets lane, new task)** move N3's restrictions to 0041: `select` → `PgSampleRestrictions.bound`, `_stone` (tombstone/reconcile) → `.tombstone`, `blocked/permitted/status/export_evidence` → `.blocked/.permitted` (dataset-ref keyed; `export_evidence` via the export record's `dataset_ref`), N2's three `permitted` callers re-threaded; a backfill of existing object tombstones/bounds; the scratch seam above becomes its fail-first test.

## Proposed ruling (unnumbered)
An external run in `ambiguous` ends only by a platform operator's move to `failed` carrying the provider's written confirmation reference; that move releases the run's PROVIDER_USD hold in the same transaction, and no other path releases a hold while its run is ambiguous (R184 as 0042 implements it; stated for P3's fake and composition).

## Open issues
- `tests/i/test_mutants.py` baseline under non-default keys (43) and the e3l port race in `test_lab_packaging.py` (skip-if-held is check-then-bind) are outside this lane.
- WR-N3-4 not done by design (above).

## Estimate (remaining for this lane)
optimistic 0.5 h / likely 1 h / pessimistic 3 h, confidence medium. Basis: all eight items proven on their keys with 0 survivors; remaining is one review round (LAB-SQL-INTEGRATION took one ACCEPT_WITH_FIXES) plus the coordinator's C2/D8 flips; WR-N3-5 is a separate datasets-lane task (est. 3/5/10 h, 1.5× N3's own lineage slice).

## Fix round (code head cc0df9c4)
Findings 0-LSI2-F1 and 1-LSI2-1 (the same defect): d91cce76 made P2's `collect` send every failure to `record_failures` before import and settle; a teacher-returned id that is not a UUID (raw provider output, `judge/submit.py:289-291`) made 0042's `(f->>'sample_id')::uuid` refuse the whole batch (`invalid_request`), so no label imported, the run never settled, its PROVIDER_USD hold stayed and every poll raised again. The evidence's "none needed a fix" for WR-P2-D8 was wrong.

- Fix (`infrx/pipelines/teachers/__init__.py`): `collect` logs only failures whose sample id parses as a UUID (`_is_uuid`); every failure is still returned in `Collected.failures`. No migration edit.
- Fake (`tests/p/teachers/fakes.py`): `TeacherLedger.record_failures` mirrors 0042 — refuses the whole batch with `InvalidRequest` on a non-UUID sample id or a reason outside `^[a-z][a-z_]{0,63}$`.
- Fake case `test_p2__a_provider_id_that_is_no_sample_id_never_blocks_the_import_or_the_settlement` (fail-first: red at 368c87ab), named by the new mutant `foreign_id_jams_the_collect`; `failures_unrecorded` retargeted to the `logged` line.
- PG twin `test_p2_pg_a_provider_id_that_is_no_sample_id_never_jams_the_collect` (p2): with the guard removed it fails (`1 failed, 1 passed`); with it, the good label imports (1 accepted), `failures == (("junk-id","not_sent"),)`, D8's log for that run is empty, state `completed`, the second collect returns the same run.

| command | exit | result |
|---|---|---|
| `uv run --frozen pytest -q tests/p/teachers` | 0 | 19 passed, 2 skipped |
| `INFRX_D_TASK=p2 uv run --frozen pytest -q tests/p/teachers/test_teachers_pg.py` | 0 | 2 passed (guard removed: 1 failed) |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/p/teachers/test_mutants.py` | 0 | 41 mutants, 43 passed, 0 survivors |
| `uv run --frozen ruff check infrx/pipelines/teachers tests/p/teachers` | 0 | clean |

Only P2's suite is touched; the other seven items and `make api-test` stand as recorded above. Isolation: p2 key only; no foreign container touched.
