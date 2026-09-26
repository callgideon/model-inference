# APP-E3A-FIX — F-1 (sign-in claim) and F-2 (provider routes), a coordinator support lane for E3A

- **Lane:** APP-E3A-FIX. It supports task E3A: the A2 path for F-1, and the shared routes for F-2. E3A's gate is **not** claimed, and the manifest status of E3A stays `planned`.
- **Branch / worktree:** `codex/app-e3a-fix`, `.claude/worktrees/codex-app-e3a-fix`.
  - The coordinator created this worktree at `infrx-impl/apps/codex-app-e3a-fix`, with a relative path from the wrong directory. It also showed up as an untracked directory in the coordinator's tree. The lane moved it to the assigned path with `git worktree move`. It made no commits there and changed no files there.
- **Base:** `6d55c5e0`, the `claude/consumer-v1` tip, with every App task and E3A-PREP merged.
- **Code head:** `820db37e`, made of two commits:
  - `3c816729`: the fixes, the tests and the journey assertions;
  - `820db37e`: /traces answers 404 instead of streaming the 404 body under a 200.

  The evidence commit comes after these.

## Changed paths (`git diff --stat 6d55c5e0..820db37e`: 14 files, +233 −34, all owned)

| Path | What |
|---|---|
| `apps/app/lib/supabase/middleware.ts` | F-1. The decision is now the exported `redirectFor(request, signedIn)`. The signed-in redirect of /login and /signup applies to GET/HEAD only. The import is `next/server.js` so that `node --test` can load the file, because the package has no exports map. |
| `apps/app/lib/services/console.ts` | F-2, an additive export only. `providerRoute(session, notFound)`: if the operator flag is anything but exactly `true`, it calls `notFound()`. |
| `apps/app/app/(console)/{traces,dedicated,teams}/page.tsx` | The guard is the first statement of each page. On /teams it comes after the React-cached `getSession()`. |
| `apps/app/app/(console)/traces/loading.tsx` | **Deleted.** See F-2, second cause. |
| `apps/app/tests/a/middleware.test.ts` (new) | A2-MW-01..03 run on real `NextRequest`s. |
| `apps/app/tests/a/public-routes.test.ts` | Removes the source-regex A2-ROUTE-02, which pinned the old `if` line. It is superseded by A2-MW-01. The `isPublic` table cases are unchanged and green. |
| `apps/app/tests/a/{mutants.json,run-mutants.mjs}` | Adds mutant MW-POST, and adds `middleware.test.ts` to the suite. |
| `apps/app/tests/c/provider-routes.test.ts` (new) | C-PROV-01 is the guard's behaviour over the fixture sessions. C-PROV-02 pins each page's wiring and checks that no provider route has a loading boundary. |
| `apps/app/tests/c/{mutants.json,run-mutants.mjs}` | Adds mutants PROV-01..07, and adds the new test to the suite. |
| `apps/app/tests/e2e/journey.e2e.ts` | +3 assertions, none weakened (see step 3). |

## F-1: the first-login grant claim never ran

- **Root cause:** the one described in E3A-PREP. After `signInWithPassword` the browser already carries the session cookie, and `updateSession()` redirected every signed-in request for `/login`. That included the `POST /login` of the login form's server action `claimOnboarding`, which got a 307 to /models, so the action never executed. `afterSignIn` caught the failure and sent the user to /welcome.
- **Fix:** only a navigation (GET/HEAD) is sent away. The comment in the code gives the reason.
  - GET/HEAD was chosen over checking for a `Next-Action` header because it is one rule that also describes the intent: redirects are for navigations. A signed-in non-action POST to /login does not exist in the App, and passing it through would only render the form.
- **Tests first** (`tests/a/middleware.test.ts`):

  | Case | Before the fix (`unit-mw-before.log`) | After |
  |---|---|---|
  | A2-MW-01: signed-in GET/HEAD /login, /signup → /models | pass (unchanged behaviour) | pass |
  | A2-MW-02: signed-in server-action POST /login, /login?next=…, /signup → passes through | **FAIL**: `/models` returned instead of `null` | pass |
  | A2-MW-03: signed-out GET /usage?range=7d → `/login?next=%2Fusage%3Frange%3D7d`, public routes served, signed-in /usage served | pass | pass |

  The extraction with no behaviour change came first, so the red is the decision failing and not an import error.
- **Mutant:** MW-POST drops the method condition and is **killed** by A2-MW-02.
- **Oracle (browser):** `signin-claim`.
  - At the base it FAILS: "the sign-in claim reached the database", expected 3, received 2.
  - At the head it PASSES: the claim RPC count goes up by 1 on sign-in, and `[entitlements, grant_rows] = [1, 1]`, so the claim is a replay and there is no second grant.
- **Replayed user lands on `next`:** the impact line in the E3A-PREP evidence says a replayed (onboarded) user always landed on /welcome. `afterSignIn` routes a replayed claim (`credited`, `first: false`) to `next`, so this outcome is implied by the flow. `signin-claim` now also asserts `pathname == "/models"`, which is `safeNext`'s default. It passes at the head. At the base it could not have held, because the claim never ran and every sign-in landed on /welcome.

## F-2: provider routes were served to a consumer

- **Root cause:** /traces, /dedicated and /teams had no guard. The sidebar only hid their links. /admin already did `if (!session.isOperator) notFound()`.
- **Fix:** one decision, `providerRoute` in `lib/services/console.ts`, which is node-loadable and so testable with the fixtures. It gives a 404 to anything but `isOperator === true`. Each of the three pages calls it as its first statement, before any read.
  - The flag is `getSession().isOperator`. That is the same React-cached read the console layout uses to derive `isOperator` for `consoleShell`, so the call adds no round trip.
  - An operator keeps the preview until V1M moves it to Lab.
  - The guard stays in the page and is not moved into a segment layout. A layout can be skipped by an RSC request whose router-state tree says the client already has that segment, so a layout guard alone does not protect the page's reads.
- **Second cause, found by the journey (run 2 at `3c816729`):**
  - `/traces` still answered **200**. The page snapshot showed the 404 UI.
  - The reason is `traces/loading.tsx`: it wraps the page in Suspense, so the shell flushed with 200 before the page's `notFound()` ran.
  - /dedicated and /teams have no loading boundary and already answered 404.
  - The route is operator-only now, so the boundary was deleted. C-PROV-02 pins that no provider route has a `loading.tsx`. That assertion FAILS with the file restored (`unit-prov-loading-before.log`).
- **Tests first** (`tests/c/provider-routes.test.ts`, using the fixture sessions of `lib/contracts/fixtures/orgs.json`: 4 consumers and 2 operators):

  | Case | Before | After |
  |---|---|---|
  | C-PROV-01: every non-operator session is refused, with the error being `notFound()` itself; both operators are let through; `1`, `"true"`, `{}`, `undefined` and `null` are not authority | **FAIL**: no export `providerRoute` (`unit-prov-before-0.log`) | pass |
  | C-PROV-02: each page imports `notFound` and `providerRoute`, runs the guard first (only the cached session read may precede it), calls it once, and has no `loading.tsx` | **FAIL**: `traces: notFound from next/navigation` (`unit-prov-before-1.log`, guard present, pages unwired); `traces: no loading boundary above the guard` (`unit-prov-loading-before.log`) | pass |

- **What "no consumer data read is issued for the page" covers:** the pages are `.tsx` and cannot load under `node --test`, so the before-any-read order is pinned as source, the same way U3-S02 pins /admin. For /traces this means the guard runs before `consoleContext()`, `services.keys.list` and `services.traces`. For /teams it runs before `createClient()` and the `org_members` read. The behavioural proof is the browser check.
- **Mutants, all killed:**

  | Mutant | Change | Killed by |
  |---|---|---|
  | PROV-01 | drop the check | C-PROV-01 |
  | PROV-02 | a truthy flag is authority | C-PROV-01 |
  | PROV-03 | the operator is refused too | C-PROV-01 |
  | PROV-04 | /traces drops its `notFound` guard | C-PROV-02 |
  | PROV-05 | /dedicated drops its `notFound` guard | C-PROV-02 |
  | PROV-06 | /teams drops its `notFound` guard | C-PROV-02 |
  | PROV-07 | /traces reads before the guard | C-PROV-02 |

- **Oracle (browser):** `provider-route-denial`, which collects the served list.
  - At the base: `["/traces (200)", "/dedicated (200)", "/teams (200)"]`.
  - At `3c816729`: `["/traces (200)"]`, the streamed 404.
  - At the head: `[]`. **PASS**.

## Step 3: fitting the checks that the merged lanes un-gate

- `create-key`, `revoke-key` and `request-detail` already PASS at the base with the E3A-PREP selectors, against U2's `/api-keys` dialog and U4's `/usage/<id>`. **No selector or flow change was needed.**
- Two checks did not yet assert everything the brief names, so each got an added assertion. Neither is weaker than what was there:
  - **`request-detail` ("detail, charge and result"):** it asserted the id and the charge but not the result. It now asserts that U4's result panel (`main pre`) shows exactly the text-sync reply. `text-sync` keeps that reply in the journey state as `syncContent`.
  - **`isolation`:** it asserted "foreign id unavailable" only through the API. The second individual now opens the first individual's `/usage/<syncRequest>` and gets U4's "We could not find this request in your account.", and the page shows no part of that request.
- `operator-controls` stays `NOT RUN[operator-action]`, which belongs to E3A proper. `expired-result` still checks only the API half. The expired-content display on /usage/<id> is still E3A proper's item 3.

## Journey: per check, base vs head

The runs used the e4b block with a clean tree at start and end. After every run `docker ps -a | grep -c infrx-e4b` = 0, and ports 56860, 56861 and 56870 were free.

| Check | Base `6d55c5e0` | `3c816729` | Head `820db37e` |
|---|---|---|---|
| signup-verify-grant | PASS | PASS | PASS |
| **signin-claim** | **FAIL** (2 claims, expected 3) | PASS | **PASS** |
| create-key | PASS | PASS | PASS |
| text-sync | PASS | PASS | PASS |
| sse-stream | PASS | PASS | PASS |
| async-poll | PASS | PASS | PASS |
| video-upload | PASS | PASS | PASS |
| retry | PASS | PASS | PASS |
| usage-balance | PASS | PASS | PASS |
| refresh | PASS | PASS | PASS |
| request-detail | PASS | PASS (+result) | PASS (+result) |
| accounting-uncertainty | PASS | PASS | PASS |
| rate-rejection | PASS | PASS | PASS |
| low-funds | PASS | PASS | PASS |
| **provider-route-denial** | **FAIL** (/traces, /dedicated, /teams 200) | FAIL (/traces 200) | **PASS** |
| operator-controls | NOT RUN[operator-action] | NOT RUN | NOT RUN |
| isolation | PASS | PASS (+foreign detail) | PASS (+foreign detail) |
| revoke-key | PASS | PASS | PASS |
| expired-result | PASS | PASS | PASS |
| **Checks** | 16 PASS, 2 FAIL, 1 NOT RUN | 17 / 1 / 1 | **18 PASS, 0 FAIL, 1 NOT RUN** |
| **Cells** | 10 PASS, 4 NOT RUN, 3 FAIL (DUR-RLS, CONSOLE-FLOWS, APP-JOURNEY) | same | **11 PASS, 6 NOT RUN, 0 FAIL** |
| **Gate** | FAIL (exit 1) | FAIL (exit 1) | **NOT RUN (exit 3)** |
| Wall clock | 106.4 s | 93.5 s | 93.7 s |

At the head, the stage timings were:

| Stage | Seconds |
|---|---|
| build | 7.1 |
| stack | 8.1 |
| world | 5.2 |
| App | 1.2 |
| browser | 65.9 |
| teardown | 4.4 |

The six NOT RUN cells at the head:

- **DUR-CAP, DUR-FENCE, DUR-OUTBOX and CREDIT-RATE** are delegated by design. Their journey slice is PASS.
- **CONSOLE-FLOWS and APP-JOURNEY** are NOT RUN only because of `operator-controls`. Every other check under them passes.

The gate is therefore NOT RUN by the runner's ordering, with no FAIL left behind.

## Break seams at the head

| Seam | Result | Detected by | Wall |
|---|---|---|---|
| `fixture-port` | gate FAIL (exit 1), `seam:fixture-port` FAIL, "broken seam detected" | `usage-balance`: displayed `10000` vs ledger `9999.4592` | 38.9 s |
| `grant-guard` | gate FAIL (exit 1), `seam:grant-guard` FAIL, "broken seam detected" | `signup-verify-grant`: `[2, 2, "20000"]` | 27.4 s |

## Commands

| Command | Exit | Result |
|---|---|---|
| `git worktree move apps/codex-app-e3a-fix <assigned path>` (run from the coordinator's tree) | 0 | Moves the worktree only. |
| `make api-env`; `cd apps/app && pnpm install --frozen-lockfile` | 0 / 0 | |
| `PLAYWRIGHT_BROWSERS_PATH=<scratchpad>/ms-playwright pnpm exec playwright install chromium-headless-shell` | 0 | Installed chromium_headless_shell-1243 in the scratchpad only. |
| `node --test tests/a/middleware.test.ts` (extracted, before the fix) | 1 | 2 pass, 1 fail (A2-MW-02) |
| same, after | 0 | 3/3 |
| `node --test tests/c/provider-routes.test.ts` (no guard / guard with unwired pages / loading.tsx restored) | 1 / 1 / 1 | as in the F-2 table |
| same, at the head | 0 | 2/2 |
| `PLAYWRIGHT_BROWSERS_PATH=… apps/infrx-api/.venv/bin/python tests/integration/app/runner.py --out <sp>/before` at `6d55c5e0` | 1 | FAIL, 16/2/1 |
| `… --out <sp>/after` at `3c816729` | 1 | FAIL, 17/1/1 (/traces 200) |
| `… --out <sp>/after` at `820db37e` | 3 | NOT RUN, 18/0/1 |
| `… --break-seam fixture-port` / `--break-seam grant-guard` at `820db37e` | 1 / 1 | both detected |
| `make console-test` | 0 | 643 tests: 594 pass, 0 fail, 49 skipped (the pre-existing env-gated real-database cases) |
| `make console-lint` | 0 | 0 errors, 2 pre-existing warnings (`lib/contracts/`) |
| `make console-typecheck` | 0 | clean |
| `cd apps/app && pnpm build` | 0 | /traces, /dedicated and /teams are dynamic (ƒ) |
| `node --test tests/i2a/*.test.ts` | 0 | 22/22 |
| `node tests/a/run-mutants.mjs` | 0 | **46/46** killed, including MW-POST |
| `node tests/c/run-mutants.mjs --self-test && node tests/c/run-mutants.mjs` | 0 / 0 | 4/4 self-tests; **183/183** killed, including PROV-01..07 |
| `node tests/contracts/run-mutants.mjs --self-test && pnpm test:mutants` | 0 / 0 | 14/14; 212/212 |
| `node tests/v/run-mutants.mjs`; `node tests/u/run-mutants.mjs`; `node tests/a/run-catalog-mutants.mjs` | 0 / 0 / 0 | 40/40; 204/204; 46/46 |
| `python3 research/plan/scripts/validate_plan.py` | 0 | PASS |

Raw logs are in [`E3A-fix-820db37/`](E3A-fix-820db37/):

- one directory per runner run: `before-6d55c5e`, `after-3c81672`, `after-820db37`, `fixture-port-820db37` and `grant-guard-820db37`, each with `verdict.json`, `runner.log`, `browser.log`, `playwright.json` and `app.log`;
- the unit logs before and after (`unit-*.log`);
- the a/c mutant logs.

There is no media. Scratchpad and worktree paths are replaced with placeholders. The files were scanned for key prefixes, JWTs and DSNs with passwords, and none were found.

**Incident, harmless:** one early background `pnpm install --frozen-lockfile` and one early a/c mutant pass ran with the coordinator's tree as the working directory, because of a shell `cd … &` slip.

- The install only synced that tree's ignored `node_modules` to its own frozen lockfile. It added `@playwright/test`, which consumer-v1 already locks.
- The mutant runners copy the app to a temp directory, so nothing was written to that tree.
- `git status` there is unchanged: only the coordinator's own edits remain.
- Both mutant lists were rerun here, and those reruns are the numbers above.

## Wiring requests

None new. E3A-WR-1…4 from E3A-PREP are unchanged.

## What E3A proper still needs

1. **E3C BACKEND-LOCAL passing**, then a rerun of the runner on the merged SHA. That takes about 1.5 min.
2. **`operator-controls`:** an operator identity on the edge (`profiles.is_operator` through the control API), one U3 action with a reason, and one audit row after a repeat. This also exercises the operator side of F-2: /traces renders for an operator.
3. **The four delegated cells:** import E3C's same-SHA verdict and the E3B drills, or add journey injections. DUR-CAP still has no carrying case.
4. **`expired-result`:** the expired-content display on /usage/<id>.
5. **WR-1 and WR-2** applied (the shims removed), and WR-3 documented.

## Estimate (E3A proper, after its dependencies)

| Optimistic | Likely | Pessimistic | Confidence |
|---|---|---|---|
| 2 h | 5 h | 10 h | medium |

The basis:

- Both product findings are closed, and every non-operator check passes on the merged tree.
- What remains is the operator action, resolving the delegated cells (the widest spread), WR-1 and WR-2, and merged-SHA reruns.

## Audit log

- 2026-09-26: Created by the APP-E3A-FIX lane (Opus implementer) at code head `820db37e`. The manifest's E3A status is unchanged (`planned`).
