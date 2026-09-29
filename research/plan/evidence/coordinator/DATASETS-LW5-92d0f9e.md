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

## Fix round (handback d814d434; code head 0d695a7b)
Findings: 0-DS5-R1, 1-DS5-R1 (the tip's callers of `reconcile`/`tombstone`/`export_evidence` break on the new required `restrictions`; the worker swallows the TypeError), 0-DS5-R2, 1-DS5-R2 (pipelines-lineage's `lineage._stone` callers and P2's port-less fake Store break: 20 tests/p failures in the combined tree).

**Correction.** The earlier "Signatures" section said `tombstone`, `reconcile` and `export_evidence` had no caller outside `infrx/datasets` and `tests/n`. That was true at base 1b2fab07 but false at the tip. The callers are `infrx/lab/workers/__main__.py:297` (`_datasets.reconcile_all`, tip 8cbe913f), composition-3 979819b2 `__main__.py:156` (`lineage_push` → `tombstone`), `tests/integration/lab_improve/lab_world.py:517` (`reconcile`), `scenarios_faults.py:84` (`export_evidence`), and pipelines-lineage a609f368 `tests/p/annotations/world.py:116` and `tests/p/teachers/test_teachers.py:99` (`_stone`). The deviation is withdrawn: every public signature is now base-compatible.

**Fix (no caller has to re-thread; no WR is merge-blocking).**
- `restrictions` is optional again (default None) on `tombstone`, `reconcile`, `export_evidence` and `blocked`, and `blocked`'s `dataset_ref` is optional too. `permitted`, `select` and `status` are unchanged. A caller-supplied `now` is still honored.
- `reconcile` without `restrictions` resolves `restrictions_of(directory)`. The worker's `PgAccessStore(connect)` carries `_connect`, so the Lab worker's reconcile reaches 0041 with no change to `__main__.py`.
- `_stone(objects, provider, entry, reason, at, *, restrictions=None)` is restored with its base signature. It writes the write-once object record (the first reason stands) and, when given `restrictions`, also 0041's row. It returns True when either write was new. `tombstone` and `reconcile` stone through it.
- Every gate (`blocked`, and through it `permitted`, `status` and `export_evidence`) honors the object records, deny-only: the object reason wins because it is always the first write. A caller without the database (composition-3's push, `export_evidence`, P1/P2's `_stone`) therefore can neither miss a tombstone nor undo one. 0041 stays D7's copy for `lab_permitted_samples` and the SQL-side reads. `backfill` still moves the object-era records into 0041.
- `restrictions_of(store)` returns `store.restrictions`, else `PgSampleRestrictions(store._connect)`, else None. None means the object-era gate: D7's `accessible_samples` less the object tombstones and the caller-clock expiry, which covers P2's fake Store. Production stores always carry `_connect`.

**Tests first.** Commit c9d61792 was red at d814d434: `test_n3_callers_without_the_port_still_deny_for_good` failed with AttributeError `_stone`. The case covers the tip's shapes (P1/P2 `_stone`, composition's push `tombstone`, the worker's `reconcile` and `export_evidence`, each without `restrictions`, plus a store with no port) and checks that a re-grant resurrects nothing, that 0041 gets every stone through the directory's port, and that the evidence names every delivered item now denied. In the same commit, `test_lineage_pg.py` makes its reconcile call in the worker's shape (no `restrictions`), and `PgSampleRestrictions.blocked` still names the sample. `test_n3_backfill_…` now asserts that the object stone is denied at once but absent from 0041 until backfill.

**Mutants (45 → 52; every named case notices, 0 survivors).**
- New: `n3_gate_skips_denial`, `n3_push_port_dropped`, `n3_stone_skips_0041`, `n3_stone_skips_object`, `n3_reconcile_port_dropped`, `n3_reconcile_port_unresolved`, `n3_portless_store_refused` (dies by TypeError), `n3_gate_ignores_object_stones`.
- Retargeted to the new code: `n3_bound_unrecorded`, `n3_gate_ignores_tombstones`, `n3_reason_lost`, `n3_push_unpaged`, `n3_push_stops_at_done`, `n3_reconcile_reports_old_stones`, `n3_backfill_skips_bounds`.
- `n3_no_lineage_entry` now declares a TypeError death, because the push stones from the trace entry.
- Removed or renamed: `n3_gate_ignores_caller_clock` became `n3_gate_skips_denial`, and `n3_reconcile_stone_unwritten` became `n3_reconcile_port_dropped` plus `n3_stone_skips_0041`.

| command (fix round) | exit | result |
|---|---|---|
| `uv run --frozen pytest -q tests/n/lineage/test_lineage.py` at c9d61792 (red) | 1 | 1 failed (the new case), 10 passed |
| `INFRX_D_TASK=n3 INFRX_MUTANTS=all uv run --frozen pytest -q tests/n` at 0d695a7b | 0 | **205 passed** (6:59); lineage 52 mutants, 0 survivors; the PG files ran on n3 |
| `INFRX_D_TASK=n2 INFRX_MUTANTS=all uv run --frozen pytest -q tests/n` at 0d695a7b | 0 | **205 passed** (7:02) |
| `uv run --frozen ruff check infrx/datasets tests/n` | 0 | clean |
| Scratch clone: tip 8cbe913f + pipelines-lineage a609f368 + this lane (clean merges): `pytest -q tests/p tests/n tests/w tests/b tests/g/lab_evaluations tests/g/lab_pipelines -k 'not mutant'` | 1 | 15 failed, 452 passed, 38 skipped. The **same 15 failures as tip+pipelines-lineage alone** (zero diff in the failure list). **0 failures in tests/p** (was 20). The 15 are 7 tests/w `*_pg` cases that also fail on the bare tip (no task key; the default d1 key is a foreign container) and 8 `tests/g/lab_pipelines` cases (`lab route failed: TypeError`) that pipelines-lineage brings on its own. Not this lane's; recorded for the coordinator. |
| Same clone + composition-3 979819b2 (clean): `pytest -q tests/w tests/n tests/p -k 'not mutant and not _pg'` | 0 | 388 passed, 1 skipped |
| Same clone: the worker's `reconcile(directory, retention, objects, provider_org_id=p, after=None)` and composition-3's `tombstone(objects, …, reason="deleted", at=…)` called directly | 0 | no TypeError: `{'checked': 0, …, 'next': None}`, `{'tombstoned': 0, 'more': False}` |

`make api-test` was not rerun in the fix round. The change is confined to `infrx/datasets/lineage` and `tests/n`, and the suites that import it (n, p, w, b, g/lab_*) were run above, including on the combined tree. The earlier full run is recorded above. No docker was touched other than n2/n3's own containers, which were gone after each run; the foreign containers and volumes were left alone.

**Wiring requests, revised.**
- WR-DS5-1 is unchanged (optional).
- WR-DS5-2 is **no longer merge-blocking**: callers may pass `restrictions=PgSampleRestrictions(connect)` to write 0041 at push time. Without it, the object record denies at once and the next reconcile writes 0041.
- WR-DS5-3 is unchanged.
- WR-DS5-4 is **withdrawn**: `_stone` and a port-less store work as they did at base, so pipelines-lineage needs no change and the two lanes merge in either order.
- New, WR-DS5-5 (composition, optional): add a tests/w case that awaits one real `reconcile_all` pass (`test_lab_workers.py:356` monkeypatches `lineage.reconcile`) so the call shape is proven in the worker itself.

**Proposed ruling (amended, unnumbered).** 0041 is D7's authority for N3's SQL-side gate (`lab_permitted_samples`). Every tombstone also keeps its write-once object record, which every lineage gate honors deny-only, so a caller without the database can neither miss nor undo a denial. A bound passed on either clock denies.

**Open issue (new).** `blocked` lists the provider's object tombstones once per read again, the base's ponytail ceiling. The upgrade path is to retire the object reads once every caller passes `restrictions` (WR-DS5-2) and `backfill` has run (WR-DS5-3).
