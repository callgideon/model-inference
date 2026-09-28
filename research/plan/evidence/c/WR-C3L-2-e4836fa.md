# WR-C3L-2 — Lab judge page (lane judge-lw4, wave LW4)

| Field | Value |
|---|---|
| Task | **WR-C3L-2** (C3L follow-up, tasks.json:1920); oracle CONSOLE-FLOWS (+ JUDGE-BUDGET's double-click property) |
| Base / head | `9a48300c` / `e4836faf` (page commit), branch `codex/w5-judge-lw4` |
| Status | **review**; the actions still call SR-C3L-1's RPCs, not on the base (a missing RPC reads `unavailable`) |

## Changed paths (all owned)

- `apps/lab/app/(provider)/judge/page.tsx` (new, server): guard first; a viewer gets one line and no form; `runId = crypto.randomUUID()` once per render, only into the run-request form; four forms post `configureJudge`, `setJudgeBudget` (administrator only), `requestJudgeRun`, `judgeCalibrationPage`; no provider/identity field; PROVIDER_USD named.
- `apps/lab/app/(provider)/judge/form.tsx` (new, client): one action per form through `useActionState`, hidden values from the server render, submit disabled while pending, fixed outcome copy in `role="status"`.
- `apps/lab/lib/services/judge/copy.ts` (new): `outcomeText` - fixed refusal copy, never server data; a calibration page reports its size and cursor only.
- `apps/lab/lib/services/judge/core.ts`: a blank optional page size (what the form posts) is the default page, not `invalid`.
- `apps/lab/tests/c/judge/page.test.ts` (4 cases C3P-01..04), `judge.test.ts` (C3L-P01 + blank fields), `run-mutants.mjs` (+14: C3P-X01..13, C3L-X36).

## Seam tests first

| cmd | exit | result |
|---|---|---|
| `node --test tests/c/judge/page.test.ts` before `copy.ts`/page | 1 | `ERR_MODULE_NOT_FOUND lib/services/judge/copy.ts` |
| `node --test tests/c/judge/judge.test.ts` with the blank-field assertion, before the core fix | 1 | C3L-P01 failed (blank limit refused as invalid) |

Failed-then-passed: C3P-03's regex expected the JSX on one line; `C3P-X05` was killed by a `deepEqual` whose elided diff the runner could not classify as an assertion -> the scalar provider-field assertion runs first; `lab-typecheck` refused the regex `s` flag (target < es2018) -> dropped (`[^>]` already spans lines).

## Commands (final tree `e4836faf`)

| cmd | exit | result |
|---|---|---|
| `node --test tests/c/judge/*.test.ts` | 0 | 20/20 |
| `node tests/c/judge/run-mutants.mjs` | 0 | 21 cases all named; 72/72 killed |
| `make lab-test` | 0 | tests 98, pass 94, fail 0, skipped 4 |
| `make lab-lint` / `make lab-typecheck` / `make lab-build` | 0 / 0 / 0 | clean; `ƒ /judge` built |
| `make lab-mutants` | 0 | every runner 0 survivors (69, 47, 20, 42, 36, 72) |

`git diff 9a48300c..e4836faf -- apps/app packages Makefile apps/lab/pnpm-lock.yaml 'apps/lab/app/(provider)/layout.tsx'` is empty: the App and the nav are untouched.

## Wiring requests

- **WR-C3L-2-NAV (coordinator; `apps/lab/app/(provider)/layout.tsx`):** in the `<nav>`, after `<Link href="/deployments">Deployments</Link> ·`, add `<Link href="/judge">Judge</Link> ·`. Proof: `make lab-test` (L1-B02/B05 still pass) + `make lab-build` lists `/judge`.
- WR-C3L-1 (the runner in `lab-mutants`) is already on the base; nothing new for the Makefile.

## Estimate (remaining)

optimistic 0.25 h / likely 0.5 h / pessimistic 1.5 h, confidence medium; basis: the nav wiring + a PostgREST rerun once SR-C3L-1 merges.

## Audit log

- 2026-09-28: written at e4836faf (lane judge-lw4, LW4).
