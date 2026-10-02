# lab-catalog-carry (UX-11 / AP-10 follow-up: WR-UXVF-1 + WR-UXVF-2) — evidence at 56f5041

Lane lab-catalog-carry of wave 7 (batch 3 additions). Branch `codex/w7-lab-catalog-carry`, base `b74d02e0`
(merge #98 on 13c15154), code head `56f5041f` (slice 1 `a292c678`, slice 2 `56f5041f`). Keys: `ap10`
(the ap10 PostgreSQL case) and `l4` (PG 57503, lab-e2e and the stack mutants). No hosted service, box,
AWS/SSM/S3, Vercel or secret; no migration; nothing enabled (no switch touched). ux-verify-final
(`8b26e601`, the source of WR-UXVF-1/2) is not on this base; its stack.test.ts guard is re-done here.

## Changed paths (all owned)

- `apps/infrx-api/infrx/lab/evaluation/__init__.py` — `Catalog.catalog` only: WR-UXVF-1's patch as
  written (`return await self.store.eval_catalog(provider_org_id=...)`). `NO_CATALOG` and the module
  docstring's SR-AP10-1 sentence are left as they are (outside `Catalog.catalog`; #99 edits this module):
  the constant is now unused - a one-line cleanup for whoever owns the module next.
- `apps/infrx-api/infrx/state/lab_data.py` — `PgLabDataStore.eval_catalog` only (0066's `lab_eval_catalog`).
- `apps/infrx-api/tests/ap10/` — `test_evaluation_ports.py`: the catalog case is now
  `test_ap10_the_catalog_listing_is_0066s_under_the_asking_provider_and_the_evaluator_is_d7s` (the
  asking provider's listing passed through; a store failure propagates), the routes case reads the
  catalog 200 for an authorized member and 503 when the store fails; `test_evaluation_ports_pg.py`: the
  catalog on real SQL (the seeded dataset, harness, evaluator and l2's ready private dev serving for NEMO;
  none of them for OTHER; a dead database 503); `test_e2e_probe.py`: j10's `carried` over the real
  `Catalog` (a listing store carried, a 503 store not); `mutants.py`: `ap10_catalog_guessed_empty` re-anchored
  and `ap10_catalog_failure_as_empty` added (both killed by the catalog and routes cases).
- `apps/lab/tests/e2e/evaluate/backend.py` — WR-UXVF-2: (1) `journey = dataclasses.replace(gateway,
  **{port: fake for the ports not carried})`; (2) l3's S2 serving version + its dev revision D2 inserted
  READY PRIVATE beside l2's, so 0066's catalog offers a pair (`world.servings` = the catalog's refs);
  (3) `/_test/settle` stores B2's report through `PgLabDataStore.put_eval_report` with the experiment's
  run refs and protocol digest (0066's `lab_experiments` reads it back); (4) `stand_ins` names the swapped
  ports ("<ports>: the route suite's fakes (not carried)") only when some are swapped.
- `apps/lab/tests/e2e/evaluate/stack.test.ts` — E02–E05 run only when `composed.catalog` and no stand-in
  names the route suite's fakes (NOT RUN[SR-AP10-1] / NOT RUN[WR-UXVF-2] otherwise); (5) E01 re-anchored:
  "as the gateway composes LAB_EVALS, the records are listed and the launch offers the provider's own
  catalog" (form present, dataset and both servings offered, defaults the first serving).
- `apps/lab/tests/e2e/stack.py` — not touched: the guard reads `stand_ins` in stack.test.ts.

## Red before, green after

| Slice | Red | Green |
|---|---|---|
| 1 unit | `pytest tests/ap10/test_evaluation_ports.py`: 2 failed (catalog case: `DependencyUnavailable ... SR-AP10-1`; routes: catalog `503 != 200`) | 8 passed (+ probe 1) |
| 1 PG | implementation reversed: `INFRX_D_TASK=ap10 pytest tests/ap10/test_evaluation_ports_pg.py -k catalog`: 1 failed `503 == 200` | 4/4 passed |
| 2 e2e | slice 1 on, harness at base: `LAB_E2E_BUILT=1 LAB_E2E_REAL=1 INFRX_D_TASK=l4 node --test tests/e2e/evaluate/stack.test.ts` exit 1 (E01 anchored to the 503 fails; E02/E03 fail over the fakes) | exit 0, 6 pass, 0 todo |
| 2 guard | transient `composed["ledger"] = False` in backend.py | exit 0: E01 PASS, E02–E05 `# TODO NOT RUN[WR-UXVF-2]: ... ledger: the route suite's fakes (not carried)`; record `composed.ledger=false` |

## Commands (head 56f5041f, clean tree unless stated)

| Command | Exit | Result |
|---|---|---|
| `uv run --frozen pytest -q tests/ap10/test_evaluation_ports.py tests/ap10/test_e2e_probe.py` | 0 | 9 passed |
| `INFRX_D_TASK=ap10 uv run --frozen pytest -q tests/ap10/test_evaluation_ports_pg.py` | 0 | 4 passed |
| `INFRX_MUTANTS=all uv run --frozen pytest -q tests/ap10/test_mutants.py` | 0 | 39 passed (every mutant killed incl. the 2 catalog ones and j10's; every-case green) |
| `INFRX_D_TASK=l4 make lab-e2e` | 0 | 24 tests: 24 pass, 0 fail, 0 skipped, 0 todo (E02–E05 PASS over the carried ports) |
| `make lab-test` | 0 | 390 tests: 380 pass, 0 fail, 10 skipped (first run 18 fail: apps/app not installed - `@playwright/test` missing; rerun after `pnpm --dir apps/app install --frozen-lockfile`) |
| `make lab-lint` / `make lab-typecheck` | 0 / 0 | clean |
| `cd apps/lab && node tests/e2e/run-mutants.mjs` | 0 | harness list 17/17 killed, 4 cases named; stack list not run (no LAB_E2E_REAL) |
| `LAB_E2E_REAL=1 INFRX_D_TASK=l4 node tests/e2e/run-mutants.mjs --only E2E-S13..S17,E2E-S25` with WR-LCC-1 + WR-LCC-2 applied transiently | 0 | 20 cases named; 6 mutants, 6 killed (S13 by E05, S25 by E01, S14 E02, S15 E03, S16 E04, S17 E05), 0 not run |
| same, WR-LCC-1 only (first attempt) | 1 | every mutant "SURVIVED (failed only: the parent)": the copy's webpack build fails, `releases/page.ts: TS2344` (WR-LCC-2) |
| WR-LCC-2 transient: `make lab-typecheck`; `node tests/ux/releases/run-mutants.mjs --only UX10-X60,UX10-X63` | 0 / 0 | clean; 20 cases named, 2/2 killed |
| `make api-lint` | 0 | All checks passed |
| `make api-typecheck` | 0 | pyright 457 errors (baseline 458) |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS (917 links / 504 docs) |

## Wiring requests

- **WR-LCC-1** (`apps/lab/tests/e2e/run-mutants.mjs`, lab-mutants' runner): E01's new name in `S.e01`;
  S13 ("an unavailable evaluation service reads as empty lists") names E05 (no 503 on the carried
  composition, so E01 no longer sees one); new **E2E-S25** "an answered catalog reads as offering nothing"
  (`app/(provider)/evaluations/view.ts` `needs.filter((k) => catalog.value[k].length === 0)` →
  `needs.filter(() => true)`, cases E01, E02). Patch: `lab-catalog-carry-56f5041-WR-LCC-1.patch`. Lands
  WITH this branch (without it the stack list reports `no mutant names "E2E-E01 ..."`). Composed test:
  `cd apps/lab && LAB_E2E_REAL=1 INFRX_D_TASK=l4 node tests/e2e/run-mutants.mjs --only E2E-S13,E2E-S14,E2E-S15,E2E-S16,E2E-S17,E2E-S25`
  → 6/6 killed (measured, with WR-LCC-2).
- **WR-LCC-2** (product defect from merge #98, UX-10, `apps/lab/app/(provider)/releases/page.tsx` +
  `loading.tsx`): `page.tsx` exports `PURPOSE`, which Next's webpack build rejects
  (`.next/types/app/(provider)/releases/page.ts(14,13): error TS2344 ... OmitWithTag<...>`); the Turbopack
  `pnpm build` passes, so `make lab-e2e` is green, but every Lab stack mutant (built with `--webpack` in a
  copy) fails to build and "survives". Fix: the constant moves to `releases/purpose.ts`, imported by both.
  Patch: `lab-catalog-carry-56f5041-WR-LCC-2.patch`. Composed test: the WR-LCC-1 command above (6/6
  killed; before it 0/5) and `node tests/ux/releases/run-mutants.mjs` (UX10-X60/X63 killed, measured).
- **E6L j10** (`tests/integration/lab_evaluate/runner.py` SCENARIOS j10 `lanes: ["SR-AP10-1"]`, the
  scenarios_eval.py docstring, test_e6l_runner.py pins): with this branch the record's `composed` is all
  true, so `e2e.missing` is empty and j10 is bound for real; rerun `make lab-evaluate --only j10` (its own
  compose ports, not this lane's key) and re-word the SR-AP10-1 notes. Not run here.
- **matrix.json** (UX-11): the lab-e2e T-parts that read E02–E05 NOT RUN now PASS; rerun
  `node apps/app/tests/ux/matrix/run-matrix.mjs --real` after merge. ux-verify-final's stack.test.ts guard
  (8b26e601, not on this base) conflicts textually with this file: take this branch's (same rule, any
  swapped port rather than one fixed prefix).

## Open items

- `NO_CATALOG` constant unused, module docstring sentence stale (outside the owned function).
- Proposed ruling (unnumbered): a Lab e2e journey composes the unit's own ports and swaps a fake in only
  for a port the unit does not carry; any swap is named in `stand_ins` and keeps the cases that need the
  port NOT RUN.

## Estimate (remaining for this follow-up)

optimistic 0.5 h, likely 1 h, pessimistic 3 h; confidence medium; basis: both slices green and measured on
l4/ap10; remaining is the merge (WR-LCC-1 with it, WR-LCC-2 anywhere), the stack.test.ts conflict with
ux-verify-final, and the E6L j10 / matrix reruns (~10–20 min each).
