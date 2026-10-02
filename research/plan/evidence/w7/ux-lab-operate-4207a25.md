# ux-lab-operate (UX-03 + AP-09 09d) — evidence at 4207a251

Lane ux-lab-operate of wave 7 (LW7, batch 2). Branch `codex/w7-ux-lab-operate`, base `b05eb6f4`, code head
`4207a251`. Key: none (fixtures only: no Docker, no network, no hosted service). Node 22.23.1, pnpm 9.15.9,
Next 16.3.5, Chromium 1243 headless through the App install's Playwright 1.63 (as UX-00); no screenshots,
traces or videos anywhere; synthetic records only.

## Changed paths

Owned: `apps/lab/app/(provider)/layout.tsx`, `apps/lab/lib/auth/sign-in-form.tsx` (presentation only:
`useActionState(signIn, null)` and `SIGN_IN_COPY[state.error]` kept), `apps/lab/app/(provider)/{overview,models,deployments,settings}/`
(incl. `models/actions.ts`, `models/revision-form.tsx`, the wizard `models/new/{page.tsx,api.ts,wizard.ts,actions.ts,forms.tsx}`),
`apps/lab/lib/services/control/view.ts` (additive: the L4 functions and their mutant finds are untouched),
`apps/lab/tests/ux/operate/` (renderer, 6 suites, mutant runner). Lane-local beside the layout (deviation,
see handback): `apps/lab/app/(provider)/nav.tsx` (the client NavLink/MobileNav island) and
`apps/lab/app/(provider)/operate.module.css` (the lane's CSS module over UX-00 tokens; lab.css untouched).
Not touched: `components/ui/*` (consumed as frozen), `lib/api/`, `lib/auth/*` other than the form,
`lib/services/{judge,review,common}`, any test or runner outside `tests/ux/operate/`.

## What each slice delivers

| Slice | Spec | Delivered |
|---|---|---|
| 1 shell + L-01 | 02-foundations shell, 03-lab L-01 | Grouped sidebar (Operate; Improve collapsed in a native details; Judge under Evaluations, Optimizations under Releases), aria-current, workspace + role strip, skip link, mobile Drawer (open only for the route it opened on), workspace switcher rows (name, role, "Open workspace"); 400px access cards: sign-in (labels above, username/current-password, "Signing in…", no recovery link guessed, provider-membership explanation), no-workspace (ACCESS_COPY.denied kept for lab-e2e), choose-workspace, access unavailable (retry; never a credential verdict). |
| 2 L-02 | Overview | Setup stages from records (`done`/`todo`/`unknown`; a failed read is "Couldn't check", never "not started"; private deployment never completes from a record), counts with "Not available" on failure (never 0), measured traffic ("No measured requests", p95 "Not available"), Observed through (evidence) vs Last loaded (fetch time); control and aggregates load and fail independently; Add model for developer/admin, viewer told why. |
| 3 L-03 | Models | Record cards (name = last segment, revision, runtime, registered; Details: model id, copyable digest, schema); "New revision of an imported model" only when a model is imported here, as Identify / Revision / Review with Back; values kept and each refused field named (server rules restated per field); outcome = the returned record ("Registered private revision … serving readiness has not been verified", View deployments), fixed refusal, or "Outcome not confirmed … Check Models before trying again" (registration has no replay receipt). |
| 4 AP-09 09d | contracts §4 + Lab UX amendment | Add model wizard: model project (stable Idempotency-Key per rendered form) → pinned repository import (allowlisted host, 40-hex commit, secret *reference* never echoed, pasted manifest checked in place) or browser upload (hash per file, API-granted PUT per file, server completion) → verification read from `GET /lab/v1/operations/{id}` (actual state/phase, bounded auto re-read + Check again; "Artifact verified" only for a succeeded operation naming its artifact) → compatibility report → serving revision; done state says readiness cannot be verified here (AP-05). Unconfigured API or a viewer: the actual prerequisite, no form. |
| 5 L-04 | Deployments | "Registered · active record" / "Retired" (never healthy), readiness stages only as far as evidence exists ("Not verified here"), "Recorded smoke result … does not verify serving readiness", no Run dev smoke (withheld until AP-05; ponytail note), administrator-only "Request publication" opening an in-place confirmation summary (exact revision, environment, evidence, operator decides) over the existing proposals route; "Requesting publication needs an administrator" otherwise; a failed proposal read keeps the records and withholds the request; no Connect (copy says details follow verified serving setup). |
| 6 L-06 | Settings | Workspace (name, copyable id), role in words + capability yes/no, customer content "Not part of any role", service availability "Not yet verified here" (CX-01 not consumed yet), no invented controls. |
| 7 journeys | 07-handoff UX-T07/T08/T09 | OP-J01..J03 over the real layout/pages/actions at 390/768/1440 + keyboard (details opened with Enter, confirmation reached by Tab; Try again reached by Tab). |

## Red before, green after (TAP logs kept in the lane's scratchpad)

| Slice | Red run | Green |
|---|---|---|
| 1 | `node --test tests/ux/operate/shell.test.ts` on the base layout: 4/4 fail by assertion (no aria-current, no module CSS, base sign-in markup, base denied paragraph) | 4/4 |
| 2 | view + pages: OP-V01..V03 `v.setupStages is not a function`…, OP-P01/P02 fail by assertion | 5/5 |
| 3 | OP-V04 not a function, actions file fails to load (no `models/actions.ts`), OP-P03/P04 fail | 7/7 new |
| 4 | wizard.test.ts with `models/new/{api,wizard}.ts` moved aside: ERR_MODULE_NOT_FOUND; actions/pages wizard cases: file load failure + OP-P05..P08 fail | 17/17 new |
| 5 | OP-V05/V06 + OP-P09/P10 fail (and OP-P02 tightened to `[1-9]` once a second record existed) | 4/4 new |
| 6 | OP-P11 fails by assertion on the base settings page | 1/1 |
| 7 | journeys compose slices 1–6 (no new production code); their discriminating power is the mutant list (OP-J01..J03 each named and killing) | 3/3 |
| fix | OP-P07 asserting Try again's URL failed on `artifact=undefined&revision=undefined` | pass; OP-X64 kills the fix's removal |

Every OP case also passes alone (`--test-name-pattern="^OP-… "` per case; order dependence found and removed in OP-P07 and OP-J02).

## Commands at 4207a251 (exit, counts)

| Command | Exit | Result |
|---|---|---|
| `node --test tests/ux/operate/*.test.ts` | 0 | 37 OP cases pass (shell 4, view 6, pages 11, actions 7, wizard 6, journeys 3) |
| `make lab-test` | 0 | 323 tests, 309 pass, 0 fail, 14 skipped (pre-existing Docker/stack skips; base 285/271/14 per ux-foundations) |
| `make lab-lint` / `make lab-typecheck` / `make lab-build` | 0 / 0 / 0 | clean; build lists ƒ /overview /models /models/new /deployments /settings |
| `node tests/ux/operate/run-mutants.mjs` | 0 | 37 cases, all named; 64 mutants, 64 killed |
| `node tests/l/shell/run-mutants.mjs` | 0 | 42 cases, all named; 106 mutants, 106 killed (every L1 find on the layout/form kept) |
| `node tests/l/ui/run-mutants.mjs` | 1 | 86/88 killed; L4-X45, L4-X46 STALE (their finds named the old `<h1>`/`<Link>`): WR-UX03-1 |
| same, WR-UX03-1 applied then reverted | 0 | 88 mutants, 88 killed |
| `node tests/ux/run-mutants.mjs` (UX-00) | 0 | 16/16 killed |
| `pytest tests/integration/test_makefile_mutant_lists.py` | 1 | 7 failed: `tests/ux/operate/run-mutants.mjs` not in lab-mutants (WR-UX03-3); with the patch applied then reverted: 7 passed |
| `make api-lint` / `make api-typecheck` | 0 / 0 | no Python touched; ruff "All checks passed!"; pyright 458 (baseline 458) |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS (see the handback) |

## The fixture harness (tests/ux/operate/render.ts)

The real `app/(provider)/layout.tsx` and pages render to static HTML outside Next (TypeScript `transpileModule`
under Node's module hooks, `@/` mapped to the Lab root); the seams are stubbed by specifier: the guard reads
`globalThis.__operateAccess`, server actions on pages are inert, `./api` is a fixture AP-04 port, CSS modules
map class names to themselves and the page carries the raw module CSS. Control records come from the labelled
preview fake (`LAB_CONTROL_PREVIEW=1`, never production). Server actions are run for real through the same
seams. Chromium lays the pages out at 390/768/1440 (no sideways scroll; every control Tab reaches is drawn on
screen). Ceiling (ponytail note in the file): static markup, no hydration — client islands (Dialog/Drawer,
UploadForm, AutoRefresh) render their server HTML; their interactive behaviour is UX-00's (Drawer) or untested
here (the browser upload's hashing/PUT loop and the bounded auto re-read): UX-11's hydrated suite or the
lifecycle runner's stage 02 must exercise them.

## Wiring requests

- **WR-UX03-1 `apps/lab/tests/l/ui/run-mutants.mjs`** (two finds named markup this lane replaced):
  `L4-X45` find `'        title="Deployments"\n'` replace `'        title="Deployments"\n        actions={<p>Published successfully.</p>}\n'`;
  `L4-X46` find `'<NavLink href="/deployments">Deployments</NavLink>'` replace `"Deployments"`.
  Composed test: `cd apps/lab && node tests/l/ui/run-mutants.mjs` → 88 mutants, 88 killed (run with the patch).
- **WR-UX03-2 the Lab client carries AP-04** (after api-artifacts-2 mounts `lab_artifacts` + `lab_model_projects` on
  `lab/control/app.py` under LAB_ARTIFACTS): export the lab-control artifact with them, `pnpm generate` in
  `packages/api-client`, then in `apps/lab/app/(provider)/models/new/api.ts` replace the `ArtifactPaths` block with
  `import type { paths as ArtifactPaths } from "@infrx/api-client/lab";` (the method/path generics are unchanged).
  Composed test: `tests/ux/operate/wizard.test.ts` OP-W06 pointed at `lab-control.json` instead of `consumer.json`.
- **WR-UX03-3 `Makefile` lab-mutants**: append `\tcd apps/lab && node tests/ux/operate/run-mutants.mjs` after the
  `tests/ux/run-mutants.mjs` line. Composed test: `tests/integration/test_makefile_mutant_lists.py` (7 passed with the patch).
- **WR-UX03-4 (AP-04/AP-01 owners: api-artifacts-2, api-identity-2) the AP-04 routes need the workspace.**
  `SessionActors.actor` returns a session actor with `provider_org_id=None`, and every AP-04 route calls
  `_provider(actor)`, so a signed-in provider's call answers 403 "a provider workspace session is required" (the
  tests/ap04 world builds actors with the provider set). The Lab sends `?provider_org_id=<workspace>` on every call
  (as every Lab family). Request: take `provider_org_id: str = Query()` on the AP-04 routes and check current
  membership/capability with `a.projects.access.require(user_id, provider_org_id, …)` as `lab_judge` does (or derive
  it in the actor seam). Composed test (tests/ap04): a session Bearer + `?provider_org_id=<member org>` lists that
  org's projects; a non-member org is refused.

## Open items

- Real upload acceptance needs the artifact bucket's CORS to allow the Lab origin's PUT (presigned URLs from
  `grantPart`); not provisioned by any lane yet (api-artifacts-2 / coordinator).
- `registerModel` and `smokeDeployment` (lib/services/control/actions.ts) have no page caller now (the revision
  form has its own state action; smoke is withheld until AP-05). They stay under their L4 cases; retire them
  with AP-05's real smoke.
- The Lab has no App URL setting, so the "Use the infrx App" hint is text, not a link (no guessed URL).
- Settings' service availability waits for `/lab/v1/capabilities` (CX-01) through api-frontends-lab's port.
- Proposed ruling (unnumbered): a Lab lane's fixture journeys may render the real layout/pages statically with
  the guard, actions and API port stubbed by specifier (tests/ux/operate/render.ts), production modules never
  importing tests; hydrated behaviour stays with the UX-00/UX-11 harness.

## Estimate (remaining)

optimistic 1 h / likely 2 h / pessimistic 4 h, confidence medium — a review round plus applying WR-UX03-1/3 at
merge; WR-UX03-2/4 are the AP-04 owners' (the swap in api.ts is one line once the client is regenerated).
