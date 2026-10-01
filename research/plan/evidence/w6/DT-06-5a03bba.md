# W6 pgrestore-tests — DT-06: the executed half of the role pre-creation (5a03bba)

Lane `pgrestore-tests`, branch `codex/w6-pgrestore-tests`, base `2add8e0a`, test head `5a03bbaf`.
Key: the recovery harness's D form — `INFRX_I3B_PG=d INFRX_D_TASK=d3 INFRX_D1_IMAGE=supabase`
(container `infrx-d3-postgres-supabase`, 127.0.0.1:55434; never d1/55432). There is no `i3b`
tasklocal key (`local_services("i3b")` = {}: track `i` is fake-only), so the lane used `d3`, the key
the earlier I3B evidence used for this harness (and no W6 lane holds). Every container was removed
at process exit (`docker ps -a | grep d3` = 0 after each run).

## Changed paths

| path | change |
|---|---|
| `tests/integration/backend/recovery/test_restore.py` | +55 lines: `test_i3b_bk00c_a_dump_naming_missing_migration_roles_restores_into_a_fresh_target` (+ a docstring bullet) |
| `tests/integration/backend/recovery/mutants_i3b.py` | +32 lines: i3bm40c, i3bm40d, i3bm40e, i3bm40f, i3bm40g (layer 2, `-k bk00c`) + two anchor constants |
| `infra/runbooks/pgrestore.py` | **unchanged** — no seam needed: the case drives `pg.dump`/`pg.restore`/`pg.precreate_roles` through the real client container |

## The case (bk00c, layer 2, needs_pg)

Why the existing bk01 never exercised the pre-creation: roles are cluster-level, and bk01's source
(E2's database or the D task database, migrated through 0059) lives in the target's cluster, so
`infrx_lab_control` etc. always exist and `precreate_roles` skips them all. bk00c therefore uses two
`infrx_*` roles no cluster carries (`infrx_bk00c_lab_control`, `infrx_bk00c_runtime`):

1. scratch source + target from the template; the superuser creates the two roles; `postgres`
   creates `public.bk00c`, a policy `TO infrx_bk00c_lab_control` and a grant `TO infrx_bk00c_runtime`;
2. `pg.dump` the source; drop the source database and both roles (the target's cluster now lacks them —
   a fresh target, as hosted's restore target is);
3. `pg.restore` into the target: succeeds; the policy names the role; the grant is there; the only
   new `pg_roles` rows are exactly the two roles with `rolcanlogin = false`; every pre-existing row
   (oid, name, rolcanlogin — anon, authenticated, service_role, …) is unchanged;
4. `pg.precreate_roles` again: returns the two names, raises nothing, and `pg_roles` is identical (same oids).
5. finally: the roles are dropped (after `scratch` drops both databases).

## Commands (exit, counts)

| command | exit | result |
|---|---|---|
| `mutants_i3b.py --layer all` at base 2add8e0a (D form, d3) — BEFORE | 1 | 106 mutants: 101 killed, 1 control survived, 4 problems (i3bm117, i3bm33 no-cases; i3bm114 survived; i3bm54 baseline-red) — all pre-existing |
| `pytest test_restore.py -k bk00` (no stack) | 0 | 2 passed, 1 skipped (bk00c: no stack) |
| `pytest test_restore.py -k bk00` (D form) | 0 | 3 passed, 22.7 s |
| GAP (seam red): i3bm40c and i3bm40e selected against the pre-existing `bk01_a` only (D form) | — | **both SURVIVED** (1 passed each): nothing before this lane caught a LOGIN role or a skipped pre-creation |
| `mutants_i3b.py --layer all --only i3bm40b..g` (D form) | 0 | 6/6 killed: c AssertionError (rolcanlogin), d DuplicateObject on the rerun, e/f pg_restore RuntimeError (role does not exist), g IndeterminateDatatype (the DO-block bind), b bk00b |
| `pytest test_mutant_list_i3b.py` | 0 | 1 passed (anchors occur once, selectors name a case) |
| `pytest test_restore.py -k bk -rs` (D form) | 0 | 30 passed, 1 skipped (bk03: needs E2's compose stack), 209 s |
| `pytest tests/integration/backend/recovery/` (no stack) | 1 | 43 passed, 35 skipped, 1 failed: `test_runbooks.py::test_e4c_rb09…` (rollout.md "### W7" lacks `0001-<last>`; pre-existing doc drift, file not touched here) |
| `pytest tests/integration/test_run.py` | 1 | 52 passed, 1 failed: `test_every_mutant_anchor_occurs_as_declared_on_the_checkout` — stale `e3bm62` (E's list, pre-existing, not touched here); no i3b anchor is stale |
| `mutants_i3b.py --layer all` at 5a03bbaf (D form, d3) — AFTER | 1 | 111 mutants: **106 killed** (+5: i3bm40c–g), 1 control survived, the same 4 pre-existing problems; 0 new survivors |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS (963 links across 435 documents) |

## Deviations

- Role names are `infrx_bk00c_*`, not `infrx_lab_control` itself: in a shared cluster the migrated
  source always carries the real role, which is the reason the gap existed. Same parser and code path.
- Two mutants beyond the brief's three: i3bm40d (existence check dropped; the audit's) and i3bm40g
  (the cfb674fa DO-block form: the "DO-block regression impossible" claim, now runner-visible).
- Key d3 (no `i3b` tasklocal key exists).

## Open items (pre-existing, not this lane's paths)

- i3bm114 survives on the D form (rc10b's 2 cases pass under the mutant); i3bm117/i3bm33 have no
  case on the D form (rc08b/rc06 need E2's stack); i3bm54 baseline-red (rb05 red in the mutant copy).
- rb09 (rollout.md W7 section) and `e3bm62` stale anchor fail on the base.

## Wiring requests

None. (`make api-mutants` does not carry mutants_i3b.py — it runs in `tests/integration/run.py`'s
mutation stage; not changed.)

## Estimate

Remaining 0.25 / 0.5 / 1.5 h (review round; pessimistic if the coordinator wants the E2 kept-stack
form re-run instead of the D form), confidence high.
