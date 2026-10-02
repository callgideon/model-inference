# lab-followups — UX-08 (WR-UX08-3 JudgeRecords) + AP-10 housekeeping at 5ba5197

- Lane: lab-followups (wave 7 batch 4). Branch `codex/w7-lab-followups`, worktree `.claude/worktrees/codex-w7-lab-followups`.
- Base 29b9df84. Code head 5ba51971 (slice 1 42c797dc, slice 2 5ba51971); this evidence commit follows. Key: none (unit suites only;
  no Docker, nothing hosted, no switch, no migration, no client regeneration).
- Environment: Linux; `pnpm --dir apps/lab install --frozen-lockfile`, `pnpm --dir apps/app install --frozen-lockfile` (the UX harness
  needs the App's Playwright), `make api-env`; lockfiles unchanged.

## Done
- **Slice 1 — WR-UX08-3** (`apps/lab/lib/services/judge/records.ts`, new). `readJudgeRecords(workspace, api = sessionApi())` reads
  `GET /lab/v1/judge/configs?limit=100`, `/runs?limit=100` and `/budgets` (as `packages/api-client/src/lab.ts` names them; each door
  runs as the session user, developer+, 0064) for the guarded workspace only, in parallel. It fails closed: a misconfigured Lab, any
  refusal or lost answer of ANY one list, or an answer it cannot read whole (not a page, a non-PROVIDER_USD or inexact amount, an
  unknown run/calibration state, a fractional count, a text flag/version, an interval that is not two numbers, a broken row) is
  `null`, which `JudgeRecords` renders as `ServiceState unavailable` — never one list without the others, never empty. Absent optional
  `reserved`/`settled`/`agreement`/`interval` read as null (the schema's optionals). A configs or runs list with `next_cursor` sets
  `more`, and the section says "More configurations or runs exist than are shown here: the first 100 of each are listed."
  (`ponytail:` one page each; the routes key on id, not recency.)
  - `judge/page.tsx`: `const records = await readJudgeRecords(workspace);` → `<JudgeRecords records={records} />` (viewers still stop
    before any read). `judge/view.ts`: its copied AP-08 field types now come from records.ts (one definition; `RUN_STATE` `satisfies`
    the run-state union). `records.tsx`: the unavailable copy no longer says "not listed until…", plus the `more` note.
- **Slice 2 — AP-10 housekeeping.** `infrx/lab/evaluation/__init__.py`: removed the unused `NO_CATALOG` and the stale "honest 503
  (SR-AP10-1)" docstring lines (0066's `infrx.lab_eval_catalog` is carried since merge #100). `infrx/contracts/openapi/inventory.py`:
  a comment on the stand-in smoke CONSUMERS entry — it holds only while LAB_HOSTING is off (with `rt.lab_hosting`, AP-05's routes own
  the path; `lab_control.py`, WR-AP05-2). The inventory artifact is unchanged (comment only).

## Red run (tests first)
- `node --test tests/ux/evaluations/records.test.ts tests/ux/evaluations/judge.test.ts` before slice 1: exit 1 —
  `ERR_MODULE_NOT_FOUND …/lib/services/judge/records.ts` and UX08-J05 failed (the page still passed `records={null}`); 6 pass, 2 fail.

## Checks (exit codes; cwd repo root unless stated)
| command | exit | result |
|---|---|---|
| `make lab-test` | 0 | 395 tests: 385 pass, 0 fail, 10 skipped (real-service gates LAB_*_REAL / LAB_E2E_REAL: NOT RUN, key none) |
| `make lab-lint` | 0 | 0 errors; 1 warning, pre-existing (`tests/c/judge/runs.test.ts:45` `_` unused; not owned) |
| `make lab-typecheck` | 0 | clean |
| `make lab-build` | 0 | compiled; `/judge` built (ƒ) |
| `cd apps/lab && node tests/ux/evaluations/run-mutants.mjs` | 0 | 25 cases all named; 65 mutants, 65 killed |
| `cd apps/lab && node tests/c/judge/run-mutants.mjs` | 0 | 19 cases all named; 74 mutants, 74 killed |
| `make api-lint` | 0 | All checks passed |
| `make api-typecheck` | 0 | pyright 457 errors (baseline 458; no new) |
| `cd apps/infrx-api && uv run --frozen pytest -q tests/contracts/test_openapi_export.py tests/ap10 -k 'not test_mutants'` | 0 | 43 passed, 27 skipped (tests/ap10 PG cases: NOT RUN, they need the ap10 key, which api-improve-4 holds; this slice deletes an unreferenced constant and docstring lines only) |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS (918 links, 510 documents) |

## Cases and mutants
- New: UX08-K01..K05 (`tests/ux/evaluations/records.test.ts`, added to the runner's SUITE) with UX08-X48..X65.
- Re-anchored: UX08-J05 now asserts the page's `readJudgeRecords(workspace)` read and `records={records}`, and the `more` note;
  UX08-X37 ("the page claims empty records it never read") now mutates `records={records}` to `records ?? {empty}`.

## Deviations
- `JudgeRecords` also lists budgets (`GET /lab/v1/judge/budgets`): the section already rendered them (UX-08) and the door is
  developer+, like configs and runs. The tests live in `tests/ux/evaluations/` (owned), on its existing Makefile-wired runner.

## Wiring / schema requests
- None. Optional, not owned: `apps/lab/tests/c/judge/runs.test.ts:45` unused `_` (lint warning, pre-existing).

## Open / remaining
- Real-route proof of the three list reads is the lab-e2e (l4) / lab-observe gates' (not run: key none).
- Production: the Lab build needs `LAB_JUDGE_API` + the Lab unit's actors on and `LAB_API_URL` (unchanged deploy order); until then
  the section reads unavailable.
- Readable judge model/rubric selectors (`/lab/v1/judge/models`, `/rubrics`) for the config form remain UX-08's open item.

## Estimate
optimistic 0.5 h / likely 1 h / pessimistic 2 h remaining (review + merge), confidence high. Basis: both slices done and green
(~1.5 h spent of 2/3/6); remaining is review findings and the merge check.
