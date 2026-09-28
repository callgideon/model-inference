# DATASETS-LW5 — WR-N3-5: N3's sample restrictions move from object storage to 0041 (task N3)

- Base `1b2fab07` · code head `92d0f9e1` · branch `codex/w5-datasets-lw5`, worktree `.claude/worktrees/codex-w5-datasets-lw5`.
- Commits: `d27fac21` fail-first seam (red) · `d4da39ea` implementation + cases + mutants · `92d0f9e1` docstring (drops an ordering claim no mutant held) · (this evidence commit).
- Keys: n3 (PG 57518; 57519 used only for this key's own Valkey during `make api-test`) and n2 (57516). No migration, no `infrx/state/lab_*.py`, no `infrx/pipelines/**`, no composition root, no `apps/lab`, no `infrx/lab/control`, nothing hosted. Lab-only; nothing reachable from the launched App/API changed.

## Changed paths (owned)
- `apps/infrx-api/infrx/datasets/lineage/__init__.py`
- `apps/infrx-api/tests/n/imports/world.py` (`FakeSampleRestrictions`, carried by `FakeLabStore.restrictions`)
- `apps/infrx-api/tests/n/lineage/{world.py,test_lineage.py,test_lineage_pg.py,mutants.py}`

## What changed
- `select` writes every sample's content bound with `restrictions.bound` (0041 `lab_sample_bounds`) after the loop and before `register_source`/`publish`. The trace entry keeps `content_until` as lineage.
- The tombstone push (`tombstone`) and the pull (`reconcile`) write through `restrictions.tombstone` (0041 `lab_sample_tombstones`: permanent, the first reason stands, D7 stamps the time). The push sends sorted trace-marker ids in pages of `limit` and stops at the first page that stoned anything (`more` = ids left after that page). Object tombstones are no longer written.
- `permitted` = 0041 `lab_permitted_samples` (D7's accessible samples less tombstoned or bound-expired ones, on D7's clock) less the trace samples whose `content_until` the **caller's `now`** has passed. A sample is denied when either clock passes its bound, so a caller-supplied `now` is still honored.
- `blocked` = 0041 `lab_blocked_samples(dataset_ref)` limited to the ids asked for, plus `content_expired` at the caller's `now`. `status` and `export_evidence` read it per dataset ref: for `status` that is its own ref, for `export_evidence` it is the export record's `dataset_ref`. Evidence now lists only the items that export delivered.
- `backfill(objects, *, provider_org_id, restrictions)` is new. It is a one-shot, idempotent move: each object `tombstones/<id>.json` goes to `restrictions.tombstone` grouped by reason, and each trace entry's `content_until` goes to `restrictions.bound`. A rerun stones nothing new, and the same bounds are sent again and accepted without change. The result is its log: `{stones, tombstoned, bounded}`.
- `restrictions_of(store)` returns `store.restrictions` when the store has that attribute (the fakes). Otherwise it returns `PgSampleRestrictions(store._connect)`, built from the D7 store's own connection. A store with neither raises `AttributeError`, so the gate fails closed.
- N2's three `lineage.permitted` callers (`derive`, `export`, `read_part`) needed **no re-thread**: `permitted(store, objects, ref, *, provider_org_id, purpose, now)` keeps its exact signature and gets its port from `store`.

## Signatures (the HARD CONSTRAINT)
- Unchanged: `permitted`, `select`, `status` (a store is in hand; the port comes from it). pipelines-lineage's P1/P2/P3 calls to `lineage.permitted` need no re-thread.
- Changed by one keyword-only addition: `tombstone`, `reconcile` and `export_evidence` take `restrictions`, and `blocked` takes `restrictions` and `dataset_ref`. None of them has a D7 store or a dataset ref in hand, so the port cannot be derived from their existing arguments. They have no caller outside `infrx/datasets` and `tests/n`: grep over `infrx/` and `tests/` shows versions only calling `permitted`. `tombstone` keeps `at`, which is no longer stored (0041 stamps `tombstoned_at` on D7's clock, R7). **Deviation, recorded.**

## Seam checks (fail-first, commit d27fac21 at base code)
| test | red at 1b2fab07 | green at 92d0f9e1 |
|---|---|---|
| `INFRX_D_TASK=n3 pytest -q tests/n/lineage/test_lineage_pg.py` (the lab-sql-integration-2 scratch seam, now committed: after reconcile + re-grant, `lineage.permitted == set()` and `PgSampleRestrictions.blocked` names the sample `grant_not_current`, `.permitted == []`; plus `lab_sample_bounds` holds the bound after select) | 1 failed: `AssertionError: the tombstone never reached 0041 — assert {} == {'6a4d6013-…': 'grant_not_current'}`; the bound assertion also failed (`[] == [(…, 2026-12-19 11:00Z)]`) | 2 passed (+ `test_n3_pg_backfill_moves_object_restrictions_once`) |
| fake twin `tests/n/lineage/test_lineage.py` (SELECT: `restrictions.bounds`; REVOKE: `restrictions.blocked(derived)` after reconcile) | 2 failed (`{} == {…bounds}`; restrictions.blocked `{}`) | 10 passed |

## New and changed cases, each named by a mutant (`tests/n/lineage/mutants.py`, 34 → 45)
- `test_n3_a_bound_passed_on_either_clock_denies` (new): `n3_bound_unrecorded`, `n3_gate_ignores_tombstones`, `n3_gate_ignores_caller_clock`, `n3_expiry_not_immediate` (retargeted from EXPIRY: D7's clock now expires that case too), `n3_status_ignores_caller_clock`.
- `test_n3_backfill_moves_the_object_restrictions_into_d7_once` (new): `n3_backfill_skips_stones`, `n3_backfill_reason_lost`, `n3_backfill_skips_bounds`, `n3_gate_ignores_tombstones`.
- REVOKE (+ export evidence of the post-revocation export is `[]`): `n3_evidence_unshipped`, `n3_reconcile_stone_unwritten`.
- NARROW: `n3_reconcile_stone_unwritten`, `n3_reconcile_reports_old_stones`.
- FANOUT: `n3_push_unpaged` (re-targeted), `n3_push_stops_at_done` (new).
- Re-targeted to the new code: `n3_content_bound_wrong`, `n3_gate_ignores_tombstones`, `n3_reason_lost`.
- PG cases (`test_lineage_pg.py`) sit outside the runner (the T2I/G8 pattern); their oracle is the recorded red run above.

## Commands
| command | exit | result |
|---|---|---|
| `uv run --frozen pytest -q tests/n/lineage/test_lineage.py` | 0 | 10 passed |
| `INFRX_D_TASK=n3 uv run --frozen pytest -q tests/n/lineage/test_lineage_pg.py` | 0 | 2 passed |
| `INFRX_D_TASK=n3 INFRX_MUTANTS=all uv run --frozen pytest -q tests/n` (at d4da39ea; 92d0f9e1 changes only the docstring) | 0 | **198 passed, 0 skipped** (7:26): lineage 45 mutants + imports + versions lists, all killed, 0 survivors; `well_formed`/`every_case` green; the PG files for imports, versions and lineage ran on n3's container |
| `INFRX_D_TASK=n2 INFRX_MUTANTS=all uv run --frozen pytest -q tests/n` (same head) | 0 | **198 passed, 0 skipped** (7:27) |
| `uv run --frozen pytest -q tests/p tests/b tests/g/lab_evaluations tests/g/lab_pipelines -k 'not mutant'` (fakes inherit `FakeLabStore.restrictions`) | 0 | 120 passed, 20 skipped |
| `uv run --frozen ruff check infrx/datasets tests/n` | 0 | clean |
| `INFRX_D_TASK=n3 INFRX_D2_VALKEY_CONTAINER=infrx-n3-valkey INFRX_D2_VALKEY_PORT=57519 make api-test` (2:12:35) | 2 | **6470 passed, 157 skipped, 9 xfailed, 0 failed, 8 errors**. All 8 errors are `tests/i` (`test_pooler.py` ×5, `test_observe.py`, `test_privilege_probe.py`, `test_rollback_drill.py`): `BlockingIOError [Errno 11]` at `tests/i/pooler.py:110` because another agent held the shared i8 flock (`/tmp/infrx-i8-postgres-*.lock`). These are outside this lane, the same class as lab-sql-integration-2's recorded 8 pooler/observe errors. No failures in tests/n, p, b, g, content, j, r. `tests/i/test_mutants.py` did not fail in this run (the known non-default-key baseline did not trigger). |

Isolation: the foreign `infrx-d1-postgres`, `infrx-d2-postgres`, `infrx-d2-valkey`, `infrx-t2f-*` and `infrx-b3-postgres` (status Created, another checkout's) containers and the `infrx-e5l_*` volumes were not touched. Every n2/n3 container this lane's runs created was gone after they exited.

## Wiring requests
- **WR-DS5-1 (lab-sql, optional)**: give `PgLabDataStore` a `restrictions` property returning `PgSampleRestrictions(self._connect)`, so that `lineage.restrictions_of` stops reaching the private `_connect`. The patch is 3 lines in `infrx/state/lab_data.py` (import inside the property to avoid a cycle). Proof: `tests/n/lineage/test_lineage_pg.py` (n3) is unchanged and green.
- **WR-DS5-2 (composition-2 / the N3 hooks, flag OFF)**: callers of the push, the pull and the export evidence pass `restrictions=PgSampleRestrictions(connect)` on the same login as D7's store. That covers T3's deletion hook, the L2 revocation hook, the reconcile job and the export-evidence route. Proof: `test_lineage_pg.py`.
- **WR-DS5-3 (coordinator/operator, one-shot)**: run `lineage.backfill(objects, provider_org_id=P, restrictions=PgSampleRestrictions(connect))` once per provider that has object-era tombstones, before the WR-N3-5 code serves reads. Record the returned `{stones, tombstoned, bounded}`. Today nothing is hosted (Lab local-only), so there is nothing to move in production. Proof: `test_n3_backfill_…` (fake) and `test_n3_pg_backfill_…` (n3).
- **WR-DS5-4 (pipelines-lineage, note)**: `lineage.permitted` reads `store.restrictions` on fake stores. Fakes that subclass `tests/n/imports/world.FakeLabStore` inherit it; that covers P1, P3, B1 and B3. P2's `tests/p/teachers/fakes.py::Store` does not subclass it, so when P2 moves to `lineage.permitted` that fake needs `self.restrictions = FakeSampleRestrictions(self)` or the call raises `AttributeError` (it fails closed). No Makefile change: all three `tests/n/*/test_mutants.py` lists are already on `Makefile:21`.

## Proposed ruling (unnumbered; next free R193 is merge #20's)
N3's sample restrictions (permanent tombstones and write-once content bounds) have one authority: D7's 0041 tables. A sample is denied when its bound has passed on either D7's clock or the reading caller's clock. The object tombstones from before WR-N3-5 move once, through the idempotent `backfill`, never through a migration.

## Open issues
- `restrictions_of` depends on `PgLabDataStore._connect` until WR-DS5-1 lands.
- `_expired` still lists the provider's trace entries once per read (a ponytail ceiling). Removing it needs a bounds read in 0041, which is a lab-sql item if datasets outgrow the listing.
- `backfill` sends one call per reason and one call for all bounds (ponytail; it pages when a provider outgrows one request).

## Estimate (remaining for this lane)
optimistic 0.5 h / likely 1 h / pessimistic 3 h, confidence medium. Basis: done and proven on n2/n3 with 0 survivors. What remains is one review round (LAB-SQL-INTEGRATION-2 took one fix round) and the wiring requests, which belong to other lanes. Actual cost of this lane: about 3.5 h, most of it the 2 h 12 min `make api-test`, inside the 3/5/10 h estimate.
