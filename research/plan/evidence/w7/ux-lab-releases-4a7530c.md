# ux-lab-releases (UX-10): evidence at 4a7530c

Lane ux-lab-releases, wave 7 (LW7), batch 3. Branch `codex/w7-ux-lab-releases`, base `49114933`, code
head `4a7530c5`. Key: none. This lane used fixtures only: no Docker, no network, no hosted service. Node
22.23.1, pnpm 9.15.9, Next 16.3.5. Chromium 1243 ran headless through the App install's Playwright, as
UX-00 and UX-03 do. No screenshots, traces or videos were taken, and every record is synthetic.

## Changed paths (all owned)

- `apps/lab/lib/services/releases/port.ts` (new): `readPublication(actor, api = sessionApi())` reads
  three things through the generated Lab client (`lib/api`, `@infrx/api-client/lab`), as the session's
  workspace:
  - AP-06's `GET /lab/v1/control/proposals`
  - `GET /lab/v1/control/deployments`
  - AP-05's `GET /lab/v1/control/deployments/{id}/readiness`, once per distinct requested revision

  With no API configured, every read is `unavailable` and nothing is sent.
- `apps/lab/lib/services/releases/view.ts` (new). It builds on R4's `releaseRows` and `variantRows`;
  every R4 decision is unchanged.
  - **No progress.** A release with no progress reads "No measurement yet" (never "no traffic" or zero).
    A unit-refused release keeps its R255 refusal.
  - **Observed window.** The page shows "Observed through".
  - **Serving proof** comes only from the latest observation. An approved expansion is never proof.
    The badge carries a short label and the full sentence goes in the details list.
  - **Proposal summary.** It names the policy, the policy revision (the fence) the server loaded, and
    "An infrx operator decides".
  - **Role note.** Viewers and developers see "needs an administrator".
  - **Stale fence.** `?refused=conflict` shows R4's copy followed by "review the release again". Only
    fixed copy is shown.
  - **Failed reads.** `failure()` maps a failed read to `denied` only for 403/`denied`, and to
    `unavailable` otherwise.
  - **Publication rows.** `publicationRows` shows the operator's decision beside AP-05's readiness:
    - ready: the time it was checked
    - not ready: the reasons
    - 404 envelope: "No serving proof recorded"
    - unmounted route, LAB_HOSTING off, network failure or 403: "Serving proof can't be checked right now"

    An approval without current proof carries the caveat "An approval is not proof that this revision
    is serving now". The row also shows whether the deployment is listed publicly.
  - **Variants** (`variantViews`):
    - A missing identity reads "Not recorded".
    - A missing identity or different hardware is "Not comparable", with no ratio and no claim.
    - The variant is "a separate serving version; <base> is unchanged; it is replaced only through a
      release".
    - The report digest is shown.
- `apps/lab/app/(provider)/releases/page.tsx` is rewritten:
  - It uses the UX-00 primitives: PageHeader on every state, a ServiceState for each section, Badge.
  - Controlled releases and publication requests load and fail independently.
  - Each release is a summary followed by an `Evidence` disclosure (`<details>`), whose labels keep
    observed values apart from configured ones ("Candidate p99 (observed)" vs "Configured limits").
  - The proposal summary sits outside the form and describes its button (`aria-describedby`). The
    policy, fence and kind are hidden inputs from the server's record, so no editable field exists.
  - The page links to Optimizations. It has no launch or traffic control.
  - The markup stays lab-e2e-compatible:
    - exact `<section aria-label="{policy}">`
    - a form's text is its button
    - "Request —"
    - R4's refusal copy
- `apps/lab/app/(provider)/releases/loading.tsx` (new): the heading plus the loading ServiceState.
- `apps/lab/app/(provider)/releases/variants.tsx` (new): `Variants({ result })`, the optimization
  comparison list in every state. It keeps E2E-R01's copy "No optimized variants registered yet.".
  `/optimizations` mounts it through WR-UX10-1.
- `apps/lab/tests/ux/releases/{fixtures.ts,view.test.ts,pages.test.ts,run-mutants.mjs}` (new). The pages
  render through UX-03's renderer (`tests/ux/operate/render.ts`, imported, not edited). The two ports and
  the R4 action are stubbed by specifier with a later-registered hook.

## Red before, green after (TAP logs are in the lane scratchpad `ux-releases/`)

| Slice | Red run | Green |
|---|---|---|
| 1: view + port | `node --test tests/ux/releases/view.test.ts` failed with ERR_MODULE_NOT_FOUND (`lib/services/releases/port.ts`), `red-view.log` | 12/12 |
| 2: pages | `pages.test.ts` at first failed with ERR_MODULE_NOT_FOUND (`loading.tsx`). With the new loading/variants and the **base** `releases/page.tsx` swapped back in, 6 of 8 failed by assertion (P01–P04, P06, L01; the base page also scrolls sideways at 390px), `red-pages-basepage.log` | 8/8 |
| 2: found by L01 | The first green attempt had a long proof sentence inside a badge that did not wrap, so 390px scrolled sideways (probe: the badge ended at 628px). Fix: the badge shows a short label and the details list holds the sentence | UX10-X46 kills the regression |
| 2: lab-e2e contract | The summary first sat inside the form, which breaks E2E-R's `forms().text` deep-equal, and the class on the release `<section>` broke `harness.section()`'s exact tag. Both were fixed | X44, X58 |

## Commands at 4a7530c5 (exit, counts)

| Command | Exit | Result |
|---|---|---|
| `node --test tests/ux/releases/*.test.ts` | 0 | 20 UX10 cases pass (view 12, pages 8) |
| `node tests/ux/releases/run-mutants.mjs` | 0 | 20 cases, all named; 66 mutants, 66 killed, 0 not killed |
| `make lab-test` | 2 | 385 tests: 374 pass, **1 fail**, 10 skipped. The failure is `R4-P01` (tests/r/pages.test.ts pins the replaced early-return markup), which WR-UX10-2 fixes |
| `make lab-test` with WR-UX10-1/2/3 applied, then reverted | 0 | 385: 375 pass, 0 fail, 10 skipped |
| `node tests/r/run-mutants.mjs` without the WR | 1 | R4-P01 is red, so the unmutated suite fails; R4-X75 and R4-X76 are STALE (their finds named `<h1>Releases</h1>` and `value={a}`) |
| `node tests/r/run-mutants.mjs` with the WR applied | 0 | 32 cases, all named; 128/128 killed |
| `pytest tests/integration/test_makefile_mutant_lists.py` with the WR applied | 0 | 7 passed. Without the WR it fails, because `tests/ux/releases/run-mutants.mjs` is not in lab-mutants |
| `make lab-lint` / `make lab-typecheck` / `make lab-build` (head; and with the WR) | 0 / 0 / 0 | Clean. The build lists ƒ /releases and ƒ /optimizations |
| `node --test tests/boundary/api.test.ts` (R271 boundary) | 0 | 3/3. The new `.call("get", …)` routes are all in `openapi/lab-control.json`; there is no RPC, table read or Supabase use |
| `node tests/l/shell/run-mutants.mjs` | 0 | 48 cases, all named; 134/134 killed (L1 shell and boundary guards, including L1-T01/T02 over the new calls) |
| `make api-lint` / `make api-typecheck` | not run | No Python was touched |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS (evidence files only) |

## Wiring requests (one patch: `ux-lab-releases-4a7530c-WR-UX10.patch`; apply all three together)

- **WR-UX10-1 `apps/lab/app/(provider)/optimizations/page.tsx`** (UX-10 owns `/optimizations` per
  06-implementation, but this batch did not allocate it). The page becomes:
  - PageHeader with the Releases breadcrumb
  - the same preview note
  - `<Variants result={variants} />`

  The URL, nav and E2E-R01's copy are unchanged. Composed test: `tests/ux/releases` UX10-P07, plus
  `tests/r/pages.test.ts`.
- **WR-UX10-2 `apps/lab/tests/r/{pages.test.ts,run-mutants.mjs}`.**
  - R4-P01's failure-state assertion becomes `/\{!records\.ok \? \(|<Variants result=\{variants\} \/>/`.
    The states themselves are UX10-P04/P07's.
  - R4-X74 find: `<Variants result={variants} />`.
  - R4-X75 find: the `{refused && …}` line.
  - R4-X76 find: `value={p.kind}`.

  Composed test: `node tests/r/run-mutants.mjs`, 128/128 with the patch.
- **WR-UX10-3 `Makefile` lab-mutants**: add `\tcd apps/lab && node tests/ux/releases/run-mutants.mjs`
  after the `tests/ux/evaluations` line. Composed test: `tests/integration/test_makefile_mutant_lists.py`
  (7 passed with the patch).

## Deviations and decisions

- **Rollout data stays on R4's committed typed port.** Rollout releases and variants still read
  through `rollouts/port.ts` (`lib/services/http.ts`). `GET /lab/v1/releases` and
  `GET /lab/v1/optimizations` carry no response schema in `lab-control.json` (`200: {}`), so the
  generated client would only give `unknown`, and R4's row checks are the fail-closed validation.
  AP-06 and AP-05 data go through the generated client.
- **No new Releases UI for some AP-06 actions:** dev keys, the dev wallet, operator
  approve/reject/listing-rollback and the `/v1/models` catalog.
  - Operator decisions are not the Lab's (L-11: "Operator approval remains separate"). The Lab shows
    their outcome as each request's state.
  - Dev keys belong to Deployments (L-04, UX-03's page), and their listing and revocation are 503
    until SR-AP06-1 (api-schema-3's 0068).
- **No Lab mutation for a listing rollback.** A provider's listing-rollback request already exists as a
  proposal kind; it is shown, not newly offered.

## Open items

- **lab-e2e not run.** `INFRX_D_TASK=l4 make lab-e2e` belongs to the l4 key (ux-verify-final). The
  markup contract E2E-R relies on is held by UX10-P01/P04/P06/P07 and mutants X44/X58/X61/X67.
  ux-verify-final should rerun E2E-R after merge.
- **Readiness on the real Lab unit.** The route reads the session actor. If WR-UX03-4 (the session
  actor's provider) is still open, the read answers 403, and the page then honestly says "Serving proof
  can't be checked right now".
- **Proposed ruling (unnumbered).** A release's serving proof is the latest observation or AP-05
  readiness receipt. A proposal's or approval's state is never rendered as serving evidence, and a
  service that is off is "can't be checked", never an empty or zero row.

## Estimate (remaining for UX-10)

optimistic 0.5 h / likely 1 h / pessimistic 3 h, confidence medium. Basis: the code and mutants are
green; what remains is applying WR-UX10-1/2/3 at merge, a review round, and ux-verify-final's lab-e2e
rerun of E2E-R on l4.
