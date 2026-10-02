# UX-06 + UX-09 (lane ux-lab-improve, wave 7 batch 2): dataset workflow; review, teacher batches and external training

- Base `b05eb6f4`. Branch `codex/w7-ux-lab-improve`. Code head `07bf1b6d`. The evidence commit follows it.
- Slice commits: `74b36a24` (A: dataset view decisions), `3b92e40b` (B: dataset pages and guided import), `967d3383` (C: training gate and lookup), `d70c77db` (D: Review page), `07bf1b6d` (mutant runner).
- Key: none. Fixtures only: a synthetic Next harness (`apps/lab/tests/ux/improve/harness`, fake server actions) and the existing pipelines preview stand-in (`lib/services/pipelines/fake.ts`). There was no Docker, no hosted service, no box and no secret. No switch, migration, API route or backend code was touched. Nothing is enabled.
- Browser: Chromium via the App's `@playwright/test` 1.63 (the UX-00 setup). No screenshots, traces or videos. Node v22.23.1, pnpm 9.15.9.

## Changed paths (all owned)

- `apps/lab/lib/services/datasets/views.ts`: added `percentToBp`, `bpPercent`, `splitPlan`, `previewKey`, `importStep`/`IMPORT_STEPS` and `importTemplate`. The N4 helpers are unchanged.
- `apps/lab/app/(provider)/datasets/`:
  - `page.tsx`, `[ref]/page.tsx`, `imports/[id]/page.tsx`: PageHeader in every state; ServiceState for failures and empty states.
  - `forms.tsx`: the guided import, the derive form and the export form. Server actions are passed as props.
  - `datasets.module.css`: lane-local, scoped.
  - `options.tsx`: a dataset-ref datalist.
- `apps/lab/app/(provider)/training/page.tsx` + `training/view.ts` (new, type-only imports).
- `apps/lab/app/(provider)/annotations/page.tsx`.
- `apps/lab/tests/ux/improve/`: `datasets.test.ts`, `wizard.test.ts` (browser), `improve.test.ts`, `pages.test.ts`, `browser.ts`, `harness/`, `run-mutants.mjs`.
- Not touched: `lib/services/datasets/{port,flows,server}.ts`, `lib/services/pipelines/*` (one mutant edits `pipelines/view.ts` in a temporary copy), the provider layout/nav (UX-03), `components/ui/*` (UX-00) and the backend.

## What the user sees now

### UX-06 (L-07, UX-T10)

**Library.**
- It has a header and an "Import dataset" action, offered to writers only.
- A failed read is `unavailable` or `denied`, with Try again. It is never shown as an empty list. An answered empty list is the `empty` state.
- The table sits in a labelled scroll region.

**Guided import: Source → Mapping → Validate → Import.**
- The steps are a list with `aria-current="step"`.
- Source shows the chosen file's name and size.
- Mapping is the advanced JSON spec. It is pre-filled from the real `infrx.dataset_import.1` schema with the workspace's provider and an import id and dataset id minted at render. The import id keeps a lost-response retry on the same write-once import. Licence, grant, method version and content path are left empty, so the backend refuses them until the provider fills them in. No structured mapping form is offered yet (CX-04, per 03-lab L-07).
- Validate states that the preview maps the first 64 KiB. It is not a full-file pass and not proof of consent or leakage safety.
- The preview counts only for the exact file and mapping it checked (`previewKey` = mapping text + file name, size and lastModified). Editing either one hides the stale preview, says so in a status message and disables Import until the next preview.
- Reject policy: strict by default. "Import the valid rows and list the rejected ones" sends `accept_rejects=on`, which the existing flow reads.

**Defect fixed in passing.** React 19 resets uncontrolled fields after a form action. That cleared the chosen file after Preview, so the next Preview or Import was sent without a file. The forms now submit through `keep()`: an onSubmit plus a transition, with no automatic reset. K02 is red without the fix (mutant X43).

**Derive.**
- Train and validation are entered as percentages with up to two decimals. They are converted to integer basis points as text, never through floats. The holdout is shown as the remainder.
- More than 100% is an inline error (`aria-invalid`) and the Derive button is disabled.
- The dataset id is minted at render and stays editable. The version is an explicit field; nothing guesses "latest + 1".

**Export.**
- Lifetime is a 1 h / 1 d / 7 d select inside the existing 1..604800 bound.
- The result lists each omitted sample with its reason.

**Version page.** Breadcrumb, a copyable ref, lineage links, splits, samples and restrictions. A failed read keeps the header.

**Import job page.**
- A Badge whose tone comes from `importView`: only a published job is `success`.
- "Published means a new private dataset version … not a public release."
- Accepted and rejected counts, the source ref and requeue as before.

### UX-09 (L-09 / L-10, UX-T12)

**Training: gated records.** The runs and checkpoints read now sits behind its own gate, the pinned P4 early return inside `Records`. At the base, a failed run listing replaced the whole page with one alert. In today's real composition that listing is a 503 (SR-AP10-2 / AP-10e). Now:
- The heading and purpose stay.
- The separately read teacher section stays.
- A note says that preparing a bundle and importing a checkpoint are not offered until the run records can be read.
- The Prepare-bundle and Import-checkpoint forms are hidden.

**Training: ambiguous submissions.**
- An ambiguous or stuck `submitting` run is offered "Look up the outcome (never submits again)". It posts `op=submit`, which P3 (`pipelines/training.submit` → `reconcile`) treats as a lookup only.
- Viewers are offered nothing. Before this change no action existed for ambiguous runs.
- Unknown cost stays "Unknown" (P4 copy, unchanged).

**Dataset catalog on ref fields.** The workspace's dataset versions are offered as a datalist on the training dataset fields and on the Review open field. The datalist is read only for writers on Training. A failed read renders no list and the typed ref remains, still validated by the action.

**Review (`/annotations`).**
- PageHeader "Review", kept in every state.
- The provenance line is repeated in the purpose text.
- The reviewer id is labelled as a fallback: "No member list is read here yet … The service refuses anyone who is not a current member allowed to review" (verified: P1 `assign` → `_may_review(reviewer_id)`).

**Unchanged on purpose.** Every P4/N4/L1/E2E-pinned string and the order the lab-e2e text regexes rely on:
- the first `externalRunId` hidden input is a run's;
- "Bundle {json} Checkpoints" stays adjacent;
- the label row cell order;
- `/Review$/` forms;
- "No training runs yet.";
- the teacher block's indentation (P4-X191).

## Commands (exit codes, counts)

| Command | Exit | Result |
|---|---|---|
| `node --test tests/ux/improve/datasets.test.ts` before slice A | 1 | Red: the views exports are missing (`scratchpad red-A.txt`). |
| `wizard.test.ts` against the base `forms.tsx` (temporary copy) | 1 | 0/5. K01 fails with `'' !== '1. Source'`; K04 times out waiting for "Train (%)". |
| `improve.test.ts` before slice C | 1 | Red: `training/view.ts` is missing. |
| `pages.test.ts` against the base pages (temporary copy) | 1 | 0/6. |
| `make lab-test` | 0 | 306 tests: 292 pass, 0 fail, 14 skipped (pre-existing Docker-gated skips). The base had 286; this lane adds 20. |
| `make lab-lint` | 0 | Clean. |
| `make lab-typecheck` (`next typegen` + `tsc`) | 0 | Clean. |
| `make lab-build` | 0 | Builds. |
| `node tests/ux/improve/run-mutants.mjs` | 1 → 0 | Static list: 15 cases, all named, 38/38 killed. Browser list: 5 cases, all named, 11/12. X44 survived on a throwing mutant (React nulls `currentTarget` in a deferred updater). Re-written eagerly, `--only UXI-X44` was killed by K02. |
| `node tests/p/run-mutants.mjs` | 0 | 45 cases, 220/220 killed. No anchor went stale. |
| `node tests/n/run-mutants.mjs` | 0 | 27 cases, 45/45 killed. |
| `node tests/l/shell/run-mutants.mjs` | 0 | 42 cases, 106/106 killed. |
| `node tests/ux/run-mutants.mjs` (UX-00) | 0 | 9 cases, 16/16 killed |
| `pytest tests/integration/test_makefile_mutant_lists.py` | 1 | 7 failed: the new runner is not yet named by `lab-mutants`. With WR-UXI-1 applied in a temporary edit, then reverted: 7 passed. |

Not run:
- API checks (`api-lint`, `api-typecheck`, `api-test`): no Python was touched.
- Console targets: `apps/app` was not touched.
- lab-e2e (key l4, not this lane's). Its text regexes were checked by reading `tests/e2e/improve/stack.test.ts` against the new markup.

**Equivalent decision (not separately killable).** The preview key is computed from the submitted FormData rather than from render state. Under every tested flow the two agree. The FormData form stays correct when previews queue up while another is pending.

## Wiring requests

- **WR-UXI-1 (Makefile).** Change `lab-mutants` from `cd apps/lab && node tests/ux/run-mutants.mjs` to `cd apps/lab && node tests/ux/run-mutants.mjs && node tests/ux/improve/run-mutants.mjs`. Composed test: `tests/integration/test_makefile_mutant_lists.py` goes from 7 failed to 7 passed (shown above).
- **WR-UXI-2 (optional, ux-verify / UX-00 owner).** Give `tests/ux/browser.ts` `harness()` a `dir` parameter (default `resolve(import.meta.dirname, "harness")`). Then delete `tests/ux/improve/browser.ts`, a ponytail-marked copy, and import `harness` from `../browser.ts` with `resolve(import.meta.dirname, "harness")`. Composed test: `node --test tests/ux/foundations.test.ts tests/ux/improve/wizard.test.ts` green.

## Open items and dependencies

**Capability states.**
- Training actions stay gated on the run listing (SR-AP10-2, AP-10e).
- Dataset imports run on the existing `/lab/v1/providers/{p}/datasets/*` API; worker and objects enablement is AP-10c.
- No `/lab/v1/capabilities` consumer exists in the Lab yet (AP-09 09e, api-frontends-lab). So "disabled" and "service down" both read as unavailable with a retry, never as a guessed "setup required".

**Deferred.**
- Structured mapping controls (CX-04).
- Stable export ids across a lost response: the export id is still minted per action call in `datasets/actions.ts`; this is unchanged.
- Batch review and keyboard shortcuts (L-09: deferred until per-item idempotency exists).
- Synchronized media review (CX-05).
- A member picker for reviewer assignment, which needs the Lab to consume AP-01 `/lab/v1/workspaces/{id}/members`.
- Teacher-chunk ambiguous lookup: re-approving resumes, but no explicit "look up" control was added.

**Integration.**
- Pages render a `lab-stack` without their own `lab-page` padding; the shell (UX-03, provider layout) owns page padding.
- The nav still says "Annotations" while the page title is "Review" (02-foundations naming). The nav label belongs to UX-03.

**Pre-existing, not this lane's.** `tests/e2e/run-mutants.mjs` E2E-S24's find string (`const failed = [imports, exports, labels, disputes].find(...)`) has been stale since the base: the page uses `firstFailure`. It only runs in the stack list.

## Estimate (remaining effort for UX-06 + UX-09 to "service integrated")

- Optimistic 3 h, likely 5 h, pessimistic 9 h. Confidence medium.
- Basis: the UI is implemented and fixture-verified. What remains:
  - wire the Lab capability API (AP-09e) into the gates;
  - re-run lab-e2e improve on l4 against the new markup;
  - the structured mapping form once CX-04 lands;
  - the member picker (AP-01 members API through the Lab port).
- Real-workflow verification waits on AP-10c/10e.
