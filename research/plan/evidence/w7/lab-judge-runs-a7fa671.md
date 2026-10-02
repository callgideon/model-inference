# lab-judge-runs — register row 98 (UX-08 follow-up, AP-09 09c follow-up) at a7fa671

- Lane: lab-judge-runs (wave 7 batch 3). Branch `codex/w7-lab-judge-runs`, worktree `.claude/worktrees/codex-w7-lab-judge-runs`.
- Base 49114933 (merges #94–#97). Code head a7fa671c (slice 1 d544039f, slice 2 a7fa671c). Key: none (unit suites only; no Docker, nothing hosted).
- Environment: Linux, Node via `pnpm --dir apps/lab install --frozen-lockfile` and `pnpm --dir apps/app install --frozen-lockfile` (the UX harness needs the App's Playwright; lockfiles unchanged).

## Done
- **Slice 1 — the request page's judge runs** (`apps/lab/lib/services/judge/runs.ts`). `judgeRunsPort(api = sessionApi())` reads
  `GET /lab/v1/traces/{request_id}/judge-runs?provider_org_id=` (api-judge-2, WR-AP09L-3) through the generated client and maps
  `ListPage[TraceJudgeRun]` to V3's `JudgeRun`: `mode` is `live` (0043's door lists only runs that SENT the request; a dry run sends
  nothing), typed integer scores (`max_score`→`max`, `requires_media`→`requiresMedia`), PROVIDER_USD amounts as their exact 8-decimal
  strings (any other unit or an inexact amount is unreadable), `overall_pass` absent→null, the stored calibration. A viewer is denied
  and a misconfigured Lab unavailable before any call; 401/403/404 denied (core's `reasonOf`), anything else unavailable; a further
  page (`next_cursor`), a non-list or any broken row is unavailable, never a partial history (`ponytail:` one-page ceiling noted).
  The request page (`requests/[id]/page.tsx`) already renders `judgePort().runs(workspace, id)` through `JudgePanel`: no page change.
- **Slice 2 — the judge page forms** (`apps/lab/app/(provider)/judge/{page.tsx,form.tsx,view.ts}`). Configure and budget carry a
  render-minted `idempotency_key` (`hidden` + `keyed="idempotency_key"`); `JudgeForm` holds it in state and replaces it after any
  definite answer, keeps it after `unavailable` (`retryKeepsKey` in view.ts), so a retry replays the write and the next submission is a
  new write. The run form keeps C3L's per-render `run_id` unchanged. Calibration posts `config_id` (the old `after`/`limit` fields were
  never read by `calibration()`, so the three actions answered `invalid`).

## Red runs (tests first)
- Slice 1: `node --test tests/c/judge/runs.test.ts` against the stub: 2 fail (J3L-R01, J3L-R03), 2 pass (R02/R04 hold for the stub too).
- Slice 2: `node --test tests/ux/evaluations/judge.test.ts`: fails to load (`view.ts` has no export `retryKeepsKey`); J07's composed
  check (the page's form fields through core) answered `invalid` for configure/budget/calibration at the base.

## Checks (exit codes; cwd repo root unless stated)
| command | exit | result |
|---|---|---|
| `make lab-test` | 0 | 370 tests: 360 pass, 0 fail, 10 skipped (all real-service gates: LAB_*_REAL / LAB_E2E_REAL, NOT RUN — key none) |
| `make lab-lint` | 0 | clean |
| `make lab-typecheck` | 0 | clean |
| `make lab-build` | 0 | built |
| `cd apps/lab && node tests/ux/evaluations/run-mutants.mjs` | 0 | 20 cases all named; 47 mutants, 47 killed |
| `cd apps/lab && node tests/c/judge/run-mutants.mjs` | 0 | 19 cases all named; 74 mutants, 74 killed |
| `cd apps/lab && node tests/v/judge/run-mutants.mjs` | 0 | 8 cases all named; 36 mutants, 36 killed |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS (916 links, 502 documents) |

First `make lab-test` run (before installing apps/app) was exit 2 with 17 UX00/UX06/ux-operate failures: `UX harness: @playwright/test
is missing` — environment, cleared by the App install; not a product result.

## Cases and mutants
- New/replaced: J3L-R01..R04 (`tests/c/judge/runs.test.ts`, replacing the "unavailable until the API serves it" J3L-R01) with
  J3L-X01..X19; UX08-J06/J07 (`tests/ux/evaluations/judge.test.ts`) with UX08-X39..X47.
- Re-anchored (api-frontends-lab's C3P cases, the forms' own rules): C3P-01 now allows exactly one `crypto.randomUUID()` in the form,
  the keyed field's replacement, and still pins the per-render run id to the run form only (page mints through one `mint()`);
  C3P-02's field list drops `after`/`limit` for the calibration `config_id`. C3P-X03 retargeted to `Object.entries(values)`.

## Deviations
- The brief names `app/(provider)/evaluations/judge/**`; the judge page lives at `app/(provider)/judge/**` (UX-08 placed it under
  Evaluations by breadcrumb/nav only). Edited there, forms + view helper only. `tests/c/judge/{page.test.ts,run-mutants.mjs}` are
  api-frontends-lab's runner files; edited for the cases these two slices change (runs.ts's own tests and the forms' C3P rules).

## Wiring / schema requests
- None required. Optional cleanup (not owned): `apps/lab/components/traces/judge/port.ts` lines 3-4 still say "unavailable until the
  API serves a request's runs (WR-AP09L-3)" — replace with "over GET /lab/v1/traces/{request_id}/judge-runs (lib/services/judge/runs.ts)".
- Schema: none.

## Open / remaining
- `JudgeRecords records={null}` on the judge page is unchanged (WR-UX08-3: list configs/runs/budgets) — not in this brief.
- Real-route proof of the judge-runs read is the lab-e2e/l4 or lab-observe gates' (not run: key none).
- Production: the Lab build needs `LAB_JUDGE_API` + the Lab unit's actors on and `LAB_API_URL` (api-frontends-lab's deploy order).

## Estimate
optimistic 0.5 h / likely 1 h / pessimistic 2 h remaining (review + merge), confidence high. Basis: both slices done and green
(~2.5 h spent of 2/3/6); remaining is review findings and the merge check.
