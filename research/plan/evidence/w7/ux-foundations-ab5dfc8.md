# ux-foundations (UX-00, UX-01, UX-02) — evidence at ab5dfc80

Lane ux-foundations of wave 7 (LW7). Branch `codex/w7-ux-foundations`, base `cd9f517c`, code head
`ab5dfc80` (commits 67c384de UX-00, a802d90c UX-00 follow-up, 6d317396 UX-01, f06f43ad UX-02,
ab5dfc80 UX-02 harness). Key: none (fixtures only; no Docker, no hosted service, no network beyond the
Next font fetch the existing builds already do). Node 22.23.1, pnpm 9.15.9, Next 16.3.5, Playwright
1.63.0 Chromium 1243 (headless), no screenshots/traces/videos anywhere.

## Changed paths (all owned)

- UX-00: `apps/lab/package.json` + `pnpm-lock.yaml` (+`@base-ui/react` 1.8.0, `lucide-react` 1.47.0,
  exact pins; lockfile diff is additions only — pnpm's unrelated eslint-plugin-import peer-key respelling
  was reverted and `pnpm install --frozen-lockfile` passes), `apps/lab/app/layout.tsx`,
  `apps/lab/app/lab.css` (new), `apps/lab/components/ui/{button,field,badge,page-header,service-state,dialog,copy-button}.tsx`
  + `README.md` (the frozen interface note), `apps/app/app/globals.css` (additive `--success`/`--warning`
  in `:root` and `.dark`, `--color-success`/`--color-warning`), `apps/lab/tests/ux/` and `apps/app/tests/ux/`
  (harness projects, `browser.ts`, tests, mutant runners).
- UX-01: `apps/app/lib/contracts/v2/published-model.ts` (type only: `physical_deletion_bound_s?: number | null`),
  `apps/app/app/(console)/docs/content.ts` (`NO_DELETION_DEADLINE`, retentionFacts), 
  `apps/app/app/(console)/usage/[requestId]/result-panel.tsx` (expired copy only), `apps/app/tests/ux/retention.test.ts`.
- UX-02: `apps/app/components/sidebar.tsx`, `apps/app/tests/ux/shell/navigation.test.ts`, harness shell.

## The synthetic harness (UX-00 slice 3)

`tests/ux/harness` in each app is a separate Next project (root layout re-exported from the app's own
`app/layout.tsx`, so fonts/CSS/theme are production's) served by `tests/ux/browser.ts` with `next dev` on
a free loopback port, driven by headless Chromium; build output under the app's ignored `.next/ux-harness`.
Fixtures are synthetic and query-selected; UXA-S02/UX00-S03 prove no module under app/lib/components
imports the harness. The Lab has no Playwright dependency (UX-00 may add Base UI + Lucide only), so its
harness resolves `@playwright/test` from the App install (`ponytail:` note in `apps/lab/tests/ux/browser.ts`);
`lab-test` therefore needs both installs, as `make check` already does. The E3A suite (`tests/e2e`) is untouched.

## Red before, green after

| Slice | Red run (before the implementation) | Green |
|---|---|---|
| UX-00 Lab | `node --test tests/ux/foundations.test.ts`: 9/9 fail (components and lab.css absent: `Module not found '@/components/ui/badge'`) | 9/9 pass |
| UX-00 App | n/a (tokens + isolation; written with the tokens) | UXA-S01/S02 pass |
| UX-01 | `node --test tests/ux/retention.test.ts`: UXR-01/02/06/07 fail; UXR-01's actual text contains the live claim "Expired content is physically deleted within 0 hours." from the live-shaped `null` | 7/7 pass |
| UX-02 | final `navigation.test.ts` against the base sidebar (`git show cd9f517c:apps/app/components/sidebar.tsx`): 6/6 fail by assertion; UXN-01: `Tab 2 focused "infrx", which is not drawn on screen (seen: Open menu > infrx)` (the audited x=-224 link); UXN-02: `Enter on the trigger opens the menu` (no dialog: no Escape, no containment) | 6/6 pass (twice) |

The UX-02 harness first rendered unstyled (Tailwind scanned only the harness dir), which hid C9; the
harness PostCSS config now scans the App (`INFRX_UX_APP_DIR`), after which the offscreen link reproduced
exactly as in the audit.

## Commands at ab5dfc80 (exit, counts)

| Command | Exit | Result |
|---|---|---|
| `make console-test` | 0 | 665 tests, 608 pass, 0 fail, 57 skipped (pre-existing DB/stack skips; 59 before `console-built` left a `.next`) |
| `make console-lint` | 0 | 0 errors, 2 pre-existing warnings (fake-services.ts) |
| `make console-typecheck` | 0 | clean |
| `make console-built` | 0 | build OK; i2a 22/22 |
| `make lab-test` | 0 | 285 tests, 271 pass, 0 fail, 14 skipped (pre-existing Docker/stack skips) |
| `make lab-lint` / `make lab-typecheck` / `make lab-build` | 0 / 0 / 0 | clean; build lists every route |
| `cd apps/lab && pnpm install --frozen-lockfile` | 0 | lockfile current |
| `cd apps/app && node tests/ux/run-mutants.mjs` | 0 | 15 cases all named; 26 mutants, 26 killed (UXA 2, UXR 9, UXN 15) |
| `cd apps/lab && node tests/ux/run-mutants.mjs` | 0 | 9 cases all named; 16 mutants, 16 killed |
| `cd apps/app && node tests/c/run-mutants.mjs --only CREDITS-13` | 1 | STALE (sidebar indentation moved): fixed by WR-UXF-3, proven killed with the patch applied then reverted |
| `pytest tests/integration/test_makefile_mutant_lists.py` | 1 | 7 failed: the new `apps/lab/tests/ux/run-mutants.mjs` is not yet in lab-mutants; with WR-UXF-1 applied (then reverted) 7 passed |
| `node --test tests/contracts/v2/published-model.test.ts` (App) / `pytest tests/contracts/v2/test_published_model.py` (API) | 0 / 0 | 54 / 98 pass (TS-Python parity unchanged) |
| `make api-lint` / `make api-typecheck` | 0 / 0 | no Python touched; pyright 458 (baseline 458) |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS |

Intermediate survivors fixed during the lane: UX00-X12/X13 (copy-failure checks were waits, not
assertions), UXN-X05 (the harness page remounted the sidebar on navigation; it now sits in a persistent
layout like production's).

## UX-01 notes

- Python parity: `ServingRetention.physical_deletion_bound_s: Count | None = None` already treats null and
  absent alike; the TS type now says so (`number | null`), so the compiler forces every reader to handle
  null. No API change, no wiring request to the API owner.
- Zero, negative, fractional, string, boolean and object bounds are refused by the parser (`count`, ≥1),
  making the whole catalog `unavailable/malformed` (fails safely, UXR-05).
- Copy audit (scope/time bounds): result readability (410 after `result_ttl_s`), stream replay,
  idempotency "after the job finishes" (`limits.py` idempotency_ttl_s "after terminal") and the cache
  wording are correct; the only false claims were the zero-hour deletion and "content was removed" on
  read expiry. Two more removal claims sit in files this lane does not own: WR-UXF-4 (request-view-model
  `resultNote`) and WR-UXF-5 (Settings privacy row "then delete it").

## Wiring requests

- **WR-UXF-1 Makefile** (coordinator): add `\tcd apps/lab && node tests/ux/run-mutants.mjs` after the
  `tests/l/shared` line of `lab-mutants`, and `\tcd apps/app && node tests/ux/run-mutants.mjs` after
  `tests/c/feedback` in `console-mutants`. Composed test: `tests/integration/test_makefile_mutant_lists.py`
  (7 passed with the patch).
- **WR-UXF-2 `apps/app/app/(console)/layout.tsx`** (skip-link target): `import { MAIN_ID, Sidebar } from "@/components/sidebar";`
  and `<main id={MAIN_ID} tabIndex={-1} className="min-w-0 flex-1 px-4 py-6 outline-none md:px-8 md:py-8">`.
  Composed test (add to `apps/app/tests/ux/shell/`): `assert.match(readFileSync("app/(console)/layout.tsx","utf8"), /<main id=\{MAIN_ID\} tabIndex=\{-1\}/)`;
  the harness layout already composes it this way and UXN-05 proves the skip link lands on it.
- **WR-UXF-3 `apps/app/tests/c/mutants.json` CREDITS-13**: `"find": "          {balance === null ? ("` (10
  spaces; the branch moved into the shared `Contents`). Composed test: `node tests/c/run-mutants.mjs --only CREDITS-13` → killed.
- **WR-UXF-4 (UX-07 owner) `app/(console)/usage/[requestId]/request-view-model.ts` resultNote**:
  `available` → "Readable until ${at}. After that the result is no longer available; this page keeps the request's details and charge.";
  `expired` → "The result stopped being available at ${at}. Request status and usage remain available." Update
  `tests/u/request-detail.test.ts` expectations with it.
- **WR-UXF-5 (UX-07 owner) `app/(console)/settings/view-model.ts` "Serving retention" detail**: replace
  "for limited periods, then delete it" with "for limited periods, after which it can no longer be read" (no
  physical-deletion timing is committed; Docs says so).

## Open items

- C9's Escape half reproduces on the base as "no dialog at all" (UXN-02 fails at opening), not as an
  Escape-specific assertion; UXN-X03 proves the fixed menu's Escape is load-bearing.
- No real-host browser verification: hosted App/Lab untouched; UX-11 owns cross-app acceptance.
- Proposed ruling (unnumbered): synthetic UX browser suites live under `apps/*/tests/ux`, serve a separate
  harness Next project and never take screenshots; production modules never import `tests/ux`.

## Estimate (remaining, all three tasks)

optimistic 0.5 h / likely 1.5 h / pessimistic 4 h, confidence medium — review round plus applying
WR-UXF-1..3 at merge; WR-UXF-4/5 belong to UX-07's estimate.
