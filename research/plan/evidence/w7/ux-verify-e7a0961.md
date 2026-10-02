# ux-verify (UX-11 preparation) — evidence at e7a0961

Lane ux-verify of wave 7 (LW7, batch 2). Branch `codex/w7-ux-verify`, base `b05eb6f4`, code head
`e7a0961e` (commits 6c6eadb1 runner + matrix + cases + mutants, d0c2ec0f blocked-part ordering fix,
7d4598b9 acceptance skeleton + typed test, e7a0961e UXM-X04 find). Key: none (fixtures only; no Docker,
hosted service, box, AWS or Vercel). Node 22.23.1, pnpm 9.15.9, Playwright 1.63.0 Chromium (headless).
No screenshots, traces or videos taken anywhere; the real E3A suite (`apps/app/tests/e2e`) untouched.

## Changed paths (all owned)

- `apps/app/tests/ux/matrix/run-matrix.mjs` (new) — the UX-T01–T13 matrix runner: per part `node --test`
  in its app (App or Lab), verdict per part/journey/overall ranked FAIL > INVALID > BLOCKED > PASS; an
  unmerged suite path is BLOCKED naming its owner lane; no case run (none matched or all skipped) is
  BLOCKED, never PASS; a failed case or non-zero exit is FAIL; a dirty tree is INVALID; a dangling fixture
  reference is FAIL. Exit 0/1/3/4. Prints the markdown table; `--out` writes the JSON; `--only T01,…`.
- `apps/app/tests/ux/matrix/matrix.json` (new) — 13 journeys / 27 parts (each with app+paths[+match]+lane,
  or blocked cause+lane) and the 07-handoff required-fixture register (41 cases: 29 committed sources
  with a symbol the runner checks, 12 gaps with owners). No new fixture data: rule 5 (reuse) — the
  register points at the committed states/fakes; gaps are owned by the batch-2/3 feature lanes.
- `apps/app/tests/ux/matrix/matrix.test.ts` (new) — UXM-01..09 (pure; discovered by console-test).
- `apps/app/tests/ux/run-mutants.mjs` — the App UX runner gains the UXM cases and 16 UXM mutants; a
  mutant may carry its own suite (the UXM mutants run only matrix.test.ts, no harness server); the
  every-case guard's baseline runs SUITE + the matrix suite; PREFIX `^UX[ARNM]-`.
- `research/plan/evidence/ux/acceptance-skeleton.md` (new) — run identity, matrix, four states per lane,
  screenshot index rules/table, open-gap register mapped to AP/UX ids, real suites, manual a11y, the
  three separate statements. `research/plan/evidence/ux/matrix-e7a0961.json` — the prep run's verdict.

## Red before, green after

| Slice | Red | Green |
|---|---|---|
| Runner decisions | `node --test tests/ux/matrix/matrix.test.ts` before run-matrix.mjs existed: exit 1, `ERR_MODULE_NOT_FOUND .../run-matrix.mjs` | 9/9 pass |
| First CLI run | `node apps/app/tests/ux/matrix/run-matrix.mjs` at 6c6eadb1: exit 1, `TypeError ... join` on a blocked part (no app) | fixed d0c2ec0f; exit 3 |
| Mutants | UXM-X09 (a failed case with exit 0 not FAIL) survived; UXM-05 gained the exit-0 failure assertion. Full run at 7d4598b9: UXM-X04 STALE (the guard now appears twice) | find made unique; 42/42 killed |

## The matrix on the merged UX-00/01/02/04 (clean tree e7a0961e; exit 3 BLOCKED; 40 s)

PASS (6 parts): T01 Models/Docs wire-to-view 7/0/0 (UX-01) · T02 App drawer 6/0/0 (UX-02) · T02 Lab
dialog/drawer primitives 2/0/0 (UX-00 K01/K02) · T03 fixture guide/snippets/fold 25/0/0 (UX-04) · T04
fixture async/video examples 6/0/0 (UX-04) · T09 Lab service-state primitive 1/0/0 (UX-00 K05).
BLOCKED (21 parts; every one names its cause and the unblocking lane): unmerged suites — ux-app-2 UX-07
`tests/ux/usage` (T01 Settings row, T04 result states, T05); ux-lab-operate UX-03 `tests/ux/operate` (T02
shell, T07, T08, T09); ux-lab-requests UX-05 `tests/ux/requests` (T09); ux-lab-evaluations UX-08
`tests/ux/evaluations` (T09, T11); ux-lab-improve UX-06/09 `tests/ux/improve` (T10, T12). Declared real or
later: T03 real (AP-09 09a/09b + window), T04 real clip (AP-11 11e + window), T06 two-user (AP-09 +
window), T07 lab-e2e (batch 3), T08 readiness (AP-05 + AP-06), T11 catalog/experiments (SR-AP10-1/0066 +
AP-10 10c), T12 release fence (UX-10) and real teacher/training (AP-10 10c/10e + P-10), T13 usability
(batch 3 + operator). FAIL: 0. All 13 journeys BLOCKED. Fixtures 29 covered, 12 gaps, 0 dangling.
Identical tables at d0c2ec0f, 7d4598b9 and e7a0961e.

## Commands (exit, counts)

| Command | Exit | Result |
|---|---|---|
| `node apps/app/tests/ux/matrix/run-matrix.mjs --out …` (e7a0961e) | 3 | BLOCKED as above (expected until batch 2/3) |
| `cd apps/app && node tests/ux/run-mutants.mjs` (e7a0961e) | 0 | 24 cases all named; 42 mutants, 42 killed (UXA 2, UXR 9, UXN 15, UXM 16) |
| `make console-test` (e7a0961e) | 0 | 701 tests, 642 pass, 0 fail, 59 skipped (pre-existing DB/stack skips; +9 UXM cases) |
| `make console-lint` | 0 | 0 errors, 2 pre-existing warnings (fake-services.ts, unchanged) |
| `make console-typecheck` | 0 | clean (first run exit 2: the .mjs return union in matrix.test.ts; typed in 7d4598b9) |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS (918 links / 482 docs) |
| `make api-lint` / `make api-typecheck` | n/a | no Python touched (pyright baseline 458 unaffected) |
| `make lab-*` | n/a | no Lab file touched; the matrix ran the Lab's `tests/ux/foundations.test.ts` (UX00-K01/K02/K05 pass) |
| `pnpm install --frozen-lockfile` (apps/app, apps/lab) | 0 / 0 | lockfiles current |

## Wiring requests

- WR-UXV-1 (optional) `Makefile`: a target outside `check` (it exits 3 while batch-2/3 lanes are open):
  `ux-matrix:\n\tnode apps/app/tests/ux/matrix/run-matrix.mjs $(UX_MATRIX_ARGS)` and `ux-matrix` in `.PHONY`.
  Composed test: `make ux-matrix; test $$? -eq 3` at this head (BLOCKED table, 0 FAIL). Not required: the
  runner and its mutants already run through `console-test` / `console-mutants` (no new mutant runner).

## Open items

- Directory parts (`tests/ux/operate`, `improve`, `evaluations`) attribute every case in the directory to
  each journey mapped to it; the final acceptance narrows them with `match` once the lanes name cases.
- App-activity fixture gaps (filtered-empty, lifecycle states, unknown accounting) and the content-state
  fixtures are owned by UX-07 / UX-05; 200% zoom and reduced motion are batch-3 checks (no tooling today).
- Screenshots: none taken; the skeleton's index fixes the rules (synthetic harness only, never E3A).

## Estimate (remaining for UX-11 preparation: review + merge fixes)

optimistic 0.5 h, likely 1 h, pessimistic 3 h; confidence medium; basis: every slice done and green;
remaining is review findings and re-pointing `matrix.json` parts if batch-2 lanes merge their suites under
other directories. Final acceptance (batch 3) is separate: 4/6/10 h per the plan.
