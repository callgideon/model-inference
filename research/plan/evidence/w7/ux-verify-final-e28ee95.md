# ux-verify-final (UX-11 final acceptance) — evidence at e28ee95

Lane ux-verify-final of wave 7 (batch 3). Branch `codex/w7-ux-verify-final`, base `49114933`, run SHA
`e28ee95c` (slices 52f4d384 runner + matrix, 4bb73c63 e2e guard, e28ee95c a11y pass + screenshots);
`4f513de8` changes one comment only. Keys: `l4` (PG 57503), `lab-v1m` (PG 57513, the V1M stack's own
key) and `t2i` (ClickHouse 57540/57541: `infrx-t2i-clickhouse`, pinned image, started for the run and
removed after). No hosted service, box, AWS/SSM/S3, Vercel or secret; no product file committed; the
real E3A suite untouched and no screenshot enabled there. Report:
[`research/design/v1/acceptance/acceptance-e28ee95.md`](../../../design/v1/acceptance/acceptance-e28ee95.md).

## Changed paths (all owned)

- `apps/app/tests/ux/matrix/run-matrix.mjs` — real parts (`real: {env, needs, ports?, prepare?}`) run only
  with `--real`, one file at a time, after `prepare` once per app; not requested or a declared port closed
  = BLOCKED naming what is needed (never FAIL); `directives()` keeps each TODO/SKIP reason, so a NOT RUN
  cell names its cause; a TODO `FAIL[<WR>]` case is FAIL while it fails and PASS once fixed.
- `apps/app/tests/ux/matrix/matrix.json` — every directory part narrowed by `match` to its journey; real
  parts: lab-e2e journeys (T07, T09, T10, T11, T12), the V1M stack (T07), N4-J01 (T10); UX-10's part is its
  suite path (unblocks itself on merge); row 98 BLOCKED[lab-judge-runs]; causes updated to the base
  (AP-05/AP-09 merged, switches off); a11y parts (T02); fixture register 40 covered, 1 gap (UX-10).
- `apps/app/tests/ux/matrix/runner.test.ts` (UXV-M01..M06), `run-mutants.mjs` (27 mutants),
  `a11y-probe.js` (the browser-side probe), `a11y.check.ts` (UXV-A01..A05);
  `apps/lab/tests/ux/matrix/a11y.check.ts` (UXV-L01..L04), `run-mutants.mjs` (7 mutants).
- `apps/lab/tests/e2e/evaluate/stack.test.ts` — the E02–E05 gate: NOT RUN[SR-AP10-1] while the catalog
  is not carried, NOT RUN[WR-UXVF-2] while the journey composes fakes over carried ports.
- `research/design/v1/acceptance/` — the report, `matrix-e28ee95.json`, `screens-uxvf/` (10 synthetic PNGs).

Why `.check.ts`: console-test/lab-test run files in parallel and a second `next dev` on UX-00's harness
stops with "Another next dev server is already running" (measured: 5/5 UXV-A cases failed beside
navigation.test.ts). The a11y suites run in the matrix and their mutant runners (ponytail comment names
the upgrade: a matrix-owned harness dir).

## Red before, green after

| Slice | Red | Green |
|---|---|---|
| Runner | `node --test tests/ux/matrix/runner.test.ts` before the implementation: exit 1, `does not provide an export named 'directives'`; UXV-M05 red until matrix.json was updated; UXV-M06 red when a fixed TODO FAIL case still failed | 6/6 + UXM 9/9 |
| a11y probe | first UXV-A01: the planted unnamed button had no box (not judged), the Tab walk ran into next dev's overlay; then the planted dialog's fade-in hid its motion (opacity 0) | probe judges motion on boxed elements; 4/4 + A05 TODO |
| Lab a11y | UXV-L01: the import spec textarea is taller than a 200% viewport (whole-rect check) | judged on its visible part (WCAG 2.4.11 "not entirely hidden"); 4/4 |
| E02–E05 guard | with WR-UXVF-1 applied transiently, the old gate would run E02–E05 over the route suite's fakes | now NOT RUN[WR-UXVF-2] |

## Commands (run SHA e28ee95c, clean tree, unless stated)

| Command | Exit | Result |
|---|---|---|
| `node apps/app/tests/ux/matrix/run-matrix.mjs --real --out …` | 1 | FAIL (WR-UXVF-3 only): parts 26 PASS / 1 FAIL / 9 BLOCKED; journeys PASS T01 T05 T07 T09 T10, FAIL T02, BLOCKED T03 T04 T06 T08 T11 T12 T13; fixtures 40/1/0 |
| base `node apps/app/tests/ux/matrix/run-matrix.mjs` (49114933, before this lane) | 3 | BLOCKED: 27 parts, 18 PASS, 9 BLOCKED (directory parts un-narrowed; no real parts) |
| `INFRX_D_TASK=l4 make lab-e2e` (base and run SHA) | 0 / 0 | 24 tests: 20 pass, 0 fail, 0 skipped, 4 todo (E02–E05 NOT RUN[SR-AP10-1]) |
| `LAB_V1M_REAL=1 INFRX_D_TASK=lab-v1m node --test tests/v/list/stack.test.ts` (t2i ClickHouse) | 0 | 6/6 pass |
| `cd apps/app && node tests/ux/matrix/run-mutants.mjs` | 0 | 10 cases named; 27 mutants, 27 killed |
| `cd apps/lab && node tests/ux/matrix/run-mutants.mjs` | 0 | 4 cases named; 7 mutants, 7 killed |
| `cd apps/app && node tests/ux/run-mutants.mjs --only UXM-X01..X16` | 0 | 24 cases named; 16/16 killed (run-matrix.mjs edits left every UXM find unique) |
| `cd apps/lab && node tests/e2e/run-mutants.mjs` | 0 | 17/17 killed, 4 cases named |
| `LAB_E2E_REAL=1 LAB_E2E_BUILT=1 INFRX_D_TASK=l4 node tests/e2e/run-mutants.mjs --only E2E-S13..S17` | 0 | 1 killed (S13 by E01), 4 not run (cases NOT RUN), 0 survivors |
| `make console-test` | 0 | 589 tests, 587 pass, 0 fail, 2 skipped |
| `make lab-test` | 0 | 365 tests, 355 pass, 0 fail, 10 skipped |
| `make console-lint` / `console-typecheck` | 0 / 0 | 0 errors (2 pre-existing warnings) / clean |
| `make lab-lint` / `lab-typecheck` / `lab-build` | 0 / 0 / 0 | clean |
| `tests/integration/test_makefile_mutant_lists.py` | 1 | 7 failed: the new Lab runner is not in `lab-mutants` yet; 7 passed with WR-UXVF-4 applied transiently |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS (916 links / 501 docs) |
| `make api-lint` / `api-typecheck` | n/a | no Python committed (pyright baseline 458 unaffected) |

## Wiring requests

- **WR-UXVF-1** (`apps/infrx-api/infrx/lab/evaluation/__init__.py` `Catalog.catalog` + `infrx/state/lab_data.py`
  `PgLabDataStore.eval_catalog`; owner api-improve-3): call 0066's `infrx.lab_eval_catalog` instead of
  raising SR-AP10-1's 503. Patch: `ux-verify-final-e28ee95-WR-UXVF-1.patch`. Composed test:
  `cd apps/lab && LAB_E2E_BUILT=1 LAB_E2E_REAL=1 INFRX_D_TASK=l4 node --test tests/e2e/evaluate/stack.test.ts`
  — measured with the patch: `world.composed.catalog` true, E02–E05 move to NOT RUN[WR-UXVF-2], and E01
  (anchored to the 503) fails; so it lands **with** WR-UXVF-2, plus an ap10 case + mutant for the call.
- **WR-UXVF-2** (`apps/lab/tests/e2e/evaluate/backend.py`, the lab-e2e harness; owner by the coordinator,
  ~2–3 h): `journey = dataclasses.replace(gateway, **{port: fake for port not carried})`; seed one
  `ready_private` dev serving pair so the real catalog lists servings; `/_test/settle` stores B2's report
  through `PgLabDataStore.put_eval_report` on the real experiment; update `stand_ins` (the journey's guard
  reads its "experiments/catalog/B3 ledger: the route suite's fakes" prefix); re-anchor E01 to the answered
  catalog. Composed test: `INFRX_D_TASK=l4 make lab-e2e` with E02–E05 PASS over the carried ports.
- **WR-UXVF-3** (`apps/app/app/globals.css`, UX-00's file): a `prefers-reduced-motion: reduce` rule
  (animation/transition 0.01 ms, one iteration). Patch (with the UXV-A05 todo removed and its mutant X28
  replacing X24, which the rule makes unkillable): `ux-verify-final-e28ee95-WR-UXVF-3.patch`. Composed test:
  `cd apps/app && node --test tests/ux/matrix/a11y.check.ts` (5/5, measured) and
  `node tests/ux/matrix/run-mutants.mjs --only UXV-X28` (killed by UXV-A05, measured).
- **WR-UXVF-4** (`Makefile`): `cd apps/app && node tests/ux/matrix/run-mutants.mjs` in `console-mutants`,
  `cd apps/lab && node tests/ux/matrix/run-mutants.mjs` in `lab-mutants`. Patch:
  `ux-verify-final-e28ee95-WR-UXVF-4.patch`. Composed test: `tests/integration/test_makefile_mutant_lists.py`
  7 passed (measured with the patch).
- After merges: re-point the row-98 part in `matrix.json` (`blocked` → its suite path + `match`); UX-10's
  part needs nothing. Rerun `node apps/app/tests/ux/matrix/run-matrix.mjs --real` with `infrx-t2i-clickhouse`
  up (docker run line in the report's §1 environment).

## Open items

- T03/T04/T06 real variants (e4b `make app-e2e`, operator window), T08 candidate engine, T12 P-10, T13
  participants: BLOCKED with owners in the report §5. `make api-lifecycle`: api-lifecycle-3's.
- The browser a11y pass covers the harness screens (shell, primitives, overlays, import wizard), not the
  production pages; a screen-reader/manual pass is NOT RUN (needs a human tester).

## Proposed ruling (unnumbered)

A UX acceptance cell reads a known, reported product defect from a suite as `# TODO FAIL[<WR>]` (FAIL in
the matrix while it fails, PASS once fixed) and a waiting dependency as `# TODO NOT RUN[<id>]` (BLOCKED with
that reason); a real gate is a matrix part that runs only on request on its own task-local key and is
BLOCKED naming its prerequisite when absent.

## Estimate (remaining for UX-11)

optimistic 1 h, likely 2 h, pessimistic 5 h; confidence medium; basis: every local slice is green and
measured; remaining is the merge review, re-running the matrix after UX-10/row 98 and WR-UXVF-1/2/3 land
(~10 min per run), and the coordinator-owned real/production cells, which are outside this estimate.
