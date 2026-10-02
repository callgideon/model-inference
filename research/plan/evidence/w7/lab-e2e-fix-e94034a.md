# lab-e2e-fix (wave 7 batch 2, UX-11: register row 90, the Lab e2e gate) at e94034a

- Base 41456309, branch `codex/w7-lab-e2e-fix`, key l4 (PG 57503). Code head e94034a8 (slices e671fa58, badc1d59, e94034a8).
- Changed paths (all owned): `apps/lab/tests/e2e/rollout/backend.py`, `apps/lab/tests/e2e/evaluate/backend.py`, `apps/lab/tests/e2e/evaluate/stack.test.ts`, `apps/lab/tests/e2e/run-mutants.mjs`, `apps/lab/tests/n/backend.py`, `apps/lab/tests/n/journey.test.ts`. No product code, no infrx-api file, no Makefile.

## Slice 1: E2E-R04 (the approval answered `conflict`)

- Cause: a stale harness seam, not a product regression. Merge #84 (5badef01, api-L1 A1) moved `control_serving` into `infrx.lab.compose`, and `lab.workers` imports it by name. The rollout backend still patched `pilot.control_serving`, which is now only a re-export. `rollout decide` then reached the real L3 alias and printed `the proposal was decided; the alias did not converge (rerun emergency-rollback): not_found: no listed alias on this endpoint`, so the door answered `conflict`. This dates from #84, not from #85–#89. The gate was dead between #84 and #90 (KeyError), so R04 never ran in that window. No bisect run was needed: the stderr line and the import site are direct evidence.
- Fix: patch `lab_workers.control_serving`, which is the namespace `decide_proposal` resolves. The stale `pilot` import is removed.
- Red, then green: `LAB_E2E_BUILT=1 LAB_E2E_REAL=1 INFRX_D_TASK=l4 node --test tests/e2e/rollout/stack.test.ts` gave exit 1 at the base (R04 not ok; pass 4, fail 2) and exit 0 after the fix (pass 6, fail 0).

## Slice 2: E2E-E (j10)

- The base's `KeyError 'run_id'` and `KeyError (uuid,'')` tracebacks were a cascade. E01's stale assertion (the page-wide `REFUSAL_COPY.unavailable`) failed before E01 switched to the journey composition. E02 then saw no form, and E03/E04 posted the doors with an empty run/experiment. The fakes' call shapes are current.
- E01 is re-anchored to UX-08's honest states. On the gateway's own composition the records are read (no page-wide refusal; "No experiments yet." / "No runs yet."). The launch reads "Comparisons cannot be launched right now", is never "Nothing to compare yet", and offers no Queue form. The case is renamed in `run-mutants.mjs`, and S13 still names it.
- `carried()` is api-improve-2's probe: the hunk is byte-identical to `codex/w7-api-improve-2` e38be3a8 (`git diff 41456309...codex/w7-api-improve-2 -- apps/lab/tests/e2e/evaluate/backend.py | git apply`). Its test and mutants live on that branch (`tests/ap10/test_e2e_probe.py`, ap10 list), so either merge order is clean.
- E02–E05 (the launch form) run only when `world.composed.catalog` is true. Otherwise each is reported as `# TODO NOT RUN[SR-AP10-1]: the composed catalog answers 503 until 0066's lab_eval_catalog` and runs nothing. TODO is used, not SKIP: `gate.py`'s `missing` treats a skipped case as a red suite (test_e5l_runner's `{"skipped": 1}` red case), while a TODO with no body changes no pass, fail or skip count. The cell is NOT RUN through the record's `composed`. Check: `gate.missing` on the head's evaluate run (pass 2, skipped 0, record `catalog: false`) gives `['catalog']`, which is j10 NOT RUN.
- Runs on l4:
  - A, the new journey with the OLD probe (`is not None`): exit 0, 6/6 pass. E02–E05 pass over the route suite's fake catalog, and the record says the catalog is carried. That is the false PASS which `carried()` removes.
  - B, the new journey with `carried()`: exit 0, pass 2, todo 4, skipped 0. The record is `{store: true, experiments: true, catalog: false, ledger: true}`.
- The stack mutant list (`run-mutants.mjs`) parses `# TODO NOT RUN[...]`. A mutant whose every case is NOT RUN is printed `not run <id> — its cases are NOT RUN[SR-AP10-1]` and is not judged or counted as a survivor. A TODO case is still required to be named.

## Slice 3: WR-AP10C-3 (N4-J01 on the corrected import-resume oracle)

- `tests/n/backend.py` now composes the production `infrx.gateway.routes.lab_datasets.router` over 0051's `PgLabImportJobs`, with D7 (`PgLabDataStore`) and L2 (`LabAccess`/`PgAccessStore`). The in-memory job dict and the proposed router copy are gone. The datasets pool's own pass (`imports.work`, which lab.workers' `import_jobs` runs) is driven by `/_test/pass`. `crash_after_puts` makes the worker die mid-staging; `lose` deletes the upload first. `/_test/lapse` moves the DB clock past `IMPORT_LEASE_S`.
- The journey asserts:
  - An interrupted import reads `running` (not failed), and `again` is false.
  - Nothing is claimed while the lease holds.
  - After the lapse, the same id publishes once with 5 accepted, and a re-POST is the same job.
  - A lost upload makes the import `failed` for good, and `importView` offers Import again.
  - A re-POST of the failed id answers `failed` and queues nothing.
  - `requeueImport` returns a new id that publishes, and is read under that id.
  - The failed id stays `failed`.
  - The rest of the journey (versions, splits, export, revocation) is unchanged and green.
- Red, then green: `LAB_N_REAL=1 INFRX_D_TASK=l4 node --test tests/n/journey.test.ts`. At the base, with the old oracle: exit 0 (1 pass). New journey against the old backend: exit 1 (`pass: {"detail":"Not Found"}`). New backend: exit 0 (1 pass). A transient manual mutant in `lab_datasets.shown` (a failed job read as running) gives exit 1; it was restored and is not committed.
- N4-J01 needs LAB_N_REAL, so it is in no runner-visible mutant list (as at the base). The production route's decisions are killed by infrx-api's own lists.

## Commands (head e94034a8 unless stated)

| Command | Exit | Result |
|---|---|---|
| `INFRX_D_TASK=l4 make lab-e2e` (base log, coordinator m90) | 2 | 24 tests: 16 pass, 8 fail |
| `INFRX_D_TASK=l4 make lab-e2e` | 0 | 24 tests: 20 pass, 0 fail, 0 skipped, 4 todo (E02–E05 NOT RUN[SR-AP10-1]) |
| `make lab-test` | 0 | 304: 290 pass, 0 fail, 14 skipped (first run 9 UX00 fails: apps/app not installed, `@playwright/test is missing`; after `cd apps/app && pnpm install --frozen-lockfile`, green) |
| `make lab-lint` / `make lab-typecheck` | 0 / 0 | clean |
| `node tests/n/run-mutants.mjs` | 0 | 45 mutants, 45 killed; 27 cases all named |
| `node tests/e2e/run-mutants.mjs` (harness list) | 0 | 17 mutants, 17 killed; 4 cases all named |
| `LAB_E2E_REAL=1 INFRX_D_TASK=l4 node tests/e2e/run-mutants.mjs --only E2E-S10,S12,S13,S14,S15,S16,S17` | 0 | 7 mutants: 3 killed (S10 by R04, S12 by R03, S13 by E01), 0 not killed, 4 not run (S14–S17: their cases are NOT RUN[SR-AP10-1]); no FAIL problem (every passing and TODO case named) |
| `uv run --frozen ruff check` on the three Python harness files | 0 | All checks passed |
| `make api-lint` | 0 | All checks passed |
| `make api-typecheck` | 0 | pyright 458 errors (baseline 458; none new; no infrx-api file touched) |

## Wiring requests

None required. No product defect was found: R04 was a harness seam, and E01 was a stale expectation.

## Open items

- Un-gating E02–E05 once SR-AP10-1 (0066) makes the catalog carried: the journey composition (`dataclasses.replace(gateway, experiments=…, ledger=…, catalog=…)`) still swaps in the route suite's fakes for all three ports, including the ones the unit already carries. At that point E02–E05 must run over the composed ports. The settle door then writes the real experiment, and the fakes stand in only for ports that are not carried. Otherwise the j10 gate would PASS over fakes. This is the lane that lands 0066's catalog plus a lab-e2e follow-up; nothing is dormant here now.
- `tests/integration/lab_evaluate/scenarios_eval.py` j10's NOT RUN labels (WR-B4-2/WR-LAB2-2/WR-B3-1) are stale. That is api-improve-2's WR-AP10C-2 and is not owned here.
- api-improve-2 is not on the base. Its identical `carried()` hunk merges cleanly; its WR-AP10C-3 is done here.

## Proposed ruling (unnumbered)

A Lab e2e case that waits on a named product gap is reported `# TODO NOT RUN[<id>]` with no body, never SKIP and never a pass over a stand-in the production composition does not use. The gate's NOT RUN comes from the suite record's `composed`. A stack mutant whose every case is NOT RUN is reported `not run`, never killed.

## Estimate (remaining)

- 0.5 / 1 / 2 h (optimistic / likely / pessimistic), confidence high.
- Basis: every slice is green on l4. What remains is the coordinator's merge review and the lab-e2e rerun at merge. The un-gating follow-up when 0066 lands is about 2–3 h (the journey composition switched to the carried ports, the settle door on the real experiments store).
