# UX-08 (lane ux-lab-evaluations, wave 7 batch 2): the Evaluations comparison interface

- Base `b05eb6f4`. Branch `codex/w7-ux-lab-evaluations`. Slice commits `548d807f` (hub, subscriptions, report component), `9b14cb04` (judge setup), `5f44c5f6` (mutant list + test fixes). Evidence commit follows.
- Key: none. No Docker, no hosted service, no box, no secret. No switch, no migration, no Python change.

## Changed paths (all owned)

- `apps/lab/app/(provider)/evaluations/`: `page.tsx` (rewritten hub), `checkpoints/page.tsx`, new `view.ts` (lane view model), `nav.tsx` (section nav), `refs.tsx` (label → immutable ref list), `report.tsx` (decision-first `ComparisonReport`, mounted by WR-UX08-1).
- `apps/lab/app/(provider)/judge/`: `page.tsx` (PageHeader under Evaluations + records section), new `view.ts` (AP-08 record view model), `records.tsx`.
- `apps/lab/tests/ux/evaluations/`: `evaluations.test.ts` (13 cases), `judge.test.ts` (5), `alias.ts` (the `@/` resolve hook, as tests/b/actions.test.ts), `run-mutants.mjs` (38 mutants).

## What changed (L-08 / UX-T11)

- Catalog is its own section. At the base the hub returned a page-wide alert when any of runs, experiments or catalog failed: `if (!runs.ok || !experiments.ok || !catalog.ok) return <p role="alert">…` (page.tsx:18 at b05eb6f4). As composed after AP-10a, the catalog answers 503 (SR-AP10-1 until 0066), so the hub hid authorized, readable 200-empty records. Now only a runs/experiments failure fails the page. A catalog failure renders `ServiceState state="unavailable"` in place of the launch form, never empty selects. A catalog that answers but lacks an input renders `empty` naming exactly what is missing (launch needs servings, a subscription does not). The checkpoints page does the same.
- Experiment rows show the experiment, its created time, both arms' states, the decision and the frozen dataset. With no report, the row says "Running: no result yet" while an arm is queued or running, and "Awaiting comparison" once both have ended. It never shows a score. With a report, it shows B2's own outcome copy in a Badge (accept info, reject warning, inconclusive neutral: never `success`), followed by "under this protocol only, not a general quality certification".
- The launch is now four steps: frozen suite, serving revisions, decision protocol and limit. Each protocol field has an explanation tied with `aria-describedby`. The help text contains no number, and no input carries a default, value or placeholder. The metric basis starts at "Choose the metric basis" (required).
- The limit copy was verified against `infrx/gateway/routes/lab_evaluations.py:163`: each arm's run budget = `run_limit`. The copy says each of the two runs may spend up to it, so the experiment up to twice it. The page does no arithmetic. The selects show labels, and `RefList` lists each label beside its immutable ref. There is no "latest" choice.
- `ComparisonReport`: decision and scope first, then the reasons, pairing/basis/confidence/paired counts, the estimates table (overall + every slice; B4's "too few cases to decide" stays distinct from "inferior"), per-run missing/errored/not-comparable, costs one line per unit, latency labelled "not a performance comparison" (the report carries no workload or hardware identity), the cost delta per unit, and the digests under Details. A teacher-judgment basis adds "a source of evidence, not ground truth". The rows reuse lib's `comparison()`, so the E2E-E04 text assertions still hold.
- Sections: Experiments / Runs (anchors) / Checkpoint subscriptions / Judge setup (`aria-current`).
- Judge setup has a PageHeader with the breadcrumb Evaluations, and its purpose line says teacher judgment is not ground truth. The C3L forms are unchanged (the C3P anchors are intact). The `JudgeRecords` section (configs with calibration, runs, budgets) is built on AP-08's documents.
  - "calibrated" appears only when the API reports `state=calibrated`. Otherwise the section shows "not calibrated: n of m reference labels".
  - `ambiguous` reads "outcome unknown: not resent automatically".
  - "cancel requested" is shown only before the run ends.
  - Money is shown per unit, and null is shown as "none".
  - Until the judge port lists records, the page passes `null`, which renders `unavailable`, never empty (WR-UX08-3).
- Unchanged and already covered by B4 (not duplicated): duplicate launch (one `experiment_id` per render, same body = same record), changed immutable refs and the cancel race (409, shown as fixed conflict copy), and cancel only while queued or running.

## Red run (before the implementation)

`node --test tests/ux/evaluations/*.test.ts` → exit 1, 0/2 files loaded: `ERR_MODULE_NOT_FOUND …/app/(provider)/evaluations/view.ts` (and judge/view.ts). The behavioural red is the page-wide catalog gate quoted above.

## Commands (exit, counts)

| Command | Exit | Result |
|---|---|---|
| `node --test tests/ux/evaluations/*.test.ts` | 0 | 18 pass, 0 fail |
| `node tests/ux/evaluations/run-mutants.mjs` | 0 | 18 cases, all named; 38 mutants, 38 killed |
| `make lab-test` (head) | 2 | 304 tests: 289 pass, 1 fail (B4-P02, fixed by WR-UX08-1), 14 skipped (Docker/real gates) |
| `node --test tests/b/*.test.ts` with WR-UX08-1 applied | 0 | 34 pass, 1 skipped |
| `node tests/b/run-mutants.mjs` with WR-UX08-1 (B4-P02 half) applied | 0 | 34 cases; 159 mutants, 159 killed |
| `node tests/b/run-mutants.mjs --only B4-X110,B4-X111,B4-X112` with the full WR-UX08-1 | 0 | 3 killed |
| `node tests/c/judge/run-mutants.mjs` (judge page anchors) | 0 | 21 cases; 72 mutants, 72 killed |
| `make lab-lint` | 0 | clean |
| `make lab-typecheck` (head; also with WR-UX08-1 applied) | 0 | clean |
| `make lab-build` | 0 | compiled; /evaluations, /evaluations/checkpoints, /judge, /experiments/[id] built |
| `pytest tests/integration/test_makefile_mutant_lists.py` | 1 | 7 failed: the new runner is not in lab-mutants. With WR-UX08-2 applied transiently: 7 passed |
| `make api-lint` / `api-typecheck` | — | not run: no Python touched |

- The first full lab-test run failed UX00-* because `@playwright/test` was missing (apps/app not installed in this worktree). That was environmental: after `cd apps/app && pnpm install --frozen-lockfile`, UX00 is green.
- L1-B07 (app code imports lib by `@/`) failed once on the first draft of view.ts. It was fixed with `@/` imports plus `alias.ts`.
- `make lab-e2e` (key l4) was not run, because this lane has no key. Under WR-UX08-1 the e2e LAUNCH must name `metric_source`. The E01–E05 text assertions were checked by reading them against the new markup: row order, "No experiments yet."/"No runs yet.", 2× `lab:run:`, c.outcome contiguous after the arm states.

## Wiring requests

- **WR-UX08-1** (`research/plan/evidence/w7/ux-lab-evaluations-5f44c5f-WR-UX08-1.patch`, `git apply` clean at 5f44c5f6). The files touched and why:
  - `tests/b/pages.test.ts`: B4-P02 reads the launch/subscribe forms behind the catalog-state branch and the harness select over `launch.catalog`; B4-P03 asserts the `<ComparisonReport report={e.report!} />` mount.
  - `tests/b/run-mutants.mjs`: B4-X105/X107 anchors move to `launch.kind`/`offered.kind`.
  - `tests/e2e/evaluate/stack.test.ts`: LAUNCH gains `metric_source: "deterministic_metric"`, because the basis is no longer preselected.
  - `app/(provider)/experiments/[id]/page.tsx`: the report branch renders `ComparisonReport`, decision first; the export link and the pending branch are unchanged.

  Composed: tests/b 34/34, B4 mutants 159/159 plus X110–X112 killed, lab-typecheck and lint clean. `make lab-e2e` on l4 is the coordinator's.
- **WR-UX08-2** (`…-WR-UX08-2.patch`): Makefile lab-mutants appends `cd apps/lab && node tests/ux/evaluations/run-mutants.mjs`. Composed: `test_makefile_mutant_lists.py` 7 passed.
- **WR-UX08-3** (after api-frontends-lab's judge port lands): in `app/(provider)/judge/page.tsx`, replace `<JudgeRecords records={null} />` with the port's listing, `(await judge.records(workspace))` mapped to `JudgeRecords | null` (null on any refusal). In `tests/ux/evaluations/judge.test.ts` UX08-J05 and mutant UX08-X37, assert the port call in place of `records={null}`.
  - Once `packages/api-client` is regenerated with the judge paths (the same regeneration as WR-AP09A-1), `judge/view.ts`'s copied types become `components["schemas"]["RunDoc" | "ConfigDoc" | "BudgetDoc" | "CalibrationDoc" | "Money"]`. UX08-J01 pins them to `openapi/lab-control.json` meanwhile.

## Open items

- The judge records stay `unavailable` until WR-UX08-3. Readable judge model/rubric selectors (`/lab/v1/judge/models`, `/rubrics`) need the same port. The config form still takes raw ids, and its fields belong to api-frontends-lab's actions.
- The protocol section keeps the advanced `name margin min-cases` slice textarea (parsed by B4's actions, which this lane does not own).
- No browser viewport pass (390/768/1440) was run for these pages. The lane has no authenticated harness for server pages; the layout uses only the frozen primitives plus plain tables. The candidate for that pass is UX-11.

## Estimate

Remaining after review: 1 / 2 / 4 h, medium confidence. Basis: every slice is green on its own suite. What remains is applying WR-UX08-1/2 at merge, the WR-UX08-3 port swap once AP-09 Lab lands, and review findings.
