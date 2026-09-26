# I2A-PREP — App deployment configuration as code and release runbook

Lane I2A-PREP (task I2A, product app), branch `codex/i2a-prep`, worktree
`.claude/worktrees/codex-i2a-prep`. Base `fd40748c` (claude/consumer-v1: C0/U1R/A2/A3 merged).
Code head `9fe94846c8cc6d4a10d3b76a0f50130cf0114c30`; this evidence is committed on top of it.
Preparation only: no Vercel, hosted Supabase, DNS, AWS, SSM or pilot-box access; no secret values
in any file, log or command line (placeholders in tests only).

## Changed paths

| Path | Change |
|---|---|
| `apps/app/lib/deploy/env.ts` | new: `VARIABLES` matrix (public/server, required per environment, checks), `environmentOf`, `assertDeployEnv` (fail closed, names only), `originAllowed`, `REDIRECT_ALLOWLIST` per auth project, `P05_SETTINGS`, `releaseIdentity`; reuses A3's `apiBaseUrl` |
| `apps/app/lib/deploy/cache.ts` | new: `PRIVATE_NO_STORE`, `PRIVATE_HEADERS`, `PUBLIC_PAGES`, `isPrivatePath`, `cacheHeaders()` |
| `apps/app/instrumentation.ts` | new: `register()` runs `assertDeployEnv(process.env)` once per Node server start |
| `apps/app/next.config.ts` | `headers()` = `cacheHeaders()`; `env` bakes `INFRX_RELEASE_SHA` (`INFRX_RELEASE_SHA \|\| VERCEL_GIT_COMMIT_SHA`) and `INFRX_BUILT_AT` |
| `apps/app/app/api/version/route.ts` | new: `GET` → `releaseIdentity` JSON with `PRIVATE_HEADERS` |
| `apps/app/tests/i2a/deploy-config.test.ts`, `surface.test.ts` | new: 20 cases |
| `apps/app/.env.example`, `apps/app/README.md` | the two new server-only names + `INFRX_APP_ENVIRONMENT`; one pointer paragraph to the matrix and runbook (closes A3's README line request) |
| `infra/app/README.md` | new runbook |

## Per item

1. **Environment matrix.** No env loader existed; `lib/deploy/env.ts` is it. Variables:
   `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `NEXT_PUBLIC_APP_URL` (public);
   `SUPABASE_SERVICE_ROLE_KEY`, `INFRX_API_BASE_URL`, `CONSOLE_CURSOR_SECRET` (server-only,
   required in production). No DSN, Sentry or alert variable exists in the App (I2A-ENV-08 scans
   every `process.env.X` and `*_ENV = "X"` in app/components/lib/middleware/instrumentation/
   next.config against the matrix + a named exempt list). Environment = `VERCEL_ENV` or
   `INFRX_APP_ENVIRONMENT` (must agree); `NODE_ENV=production` with neither is refused, never
   guessed. Production API origin: https, no path/credentials/query/fragment (loopback http only in
   development). Production is bound to the production project and `https://app.callbill.ai`.
   **Preview marker:** the loader owns `PRODUCTION_SUPABASE_URL`; a preview whose
   `NEXT_PUBLIC_SUPABASE_URL` is that project is refused (with or without the service-role key:
   the anon key alone can sign users up and send production mail). Keys are never inspected.
   **Bundle check: both.** Source walk (I2A-BUNDLE-01: no `"use client"` module reaches a file
   naming a server-only variable) always runs; built-output scan (I2A-BUILT-01 over
   `.next/static`) runs when `.next/BUILD_ID` exists and skips visibly otherwise (`next build`
   takes ~20-45 s here, too slow for every `pnpm test`).
2. **Auth callbacks/domains.** `originAllowed(env, origin)`: production = the production origin
   only; preview = `https://infrx-app-<[a-z0-9-]+>-${PREVIEW_SCOPE}.vercel.app`; development =
   `http://localhost:3000`. `REDIRECT_ALLOWLIST`: production project
   `https://app.callbill.ai/auth/callback**` only; staging project the preview pattern + localhost.
   I2A-AUTH-03 builds A2's real `verifyRedirect`/`resetRedirect` URLs and matches them with the
   auth service's glob semantics. `P05_SETTINGS` lists Site URL, Redirect URLs, Confirm email,
   Allow new users to sign up, Minimum password length (tied to `MIN_PASSWORD_LENGTH`), Custom
   SMTP, Rate limits, Email templates, `infrx.feature_flags.signup_grant` — none set.
3. **Private caching.** One `next.config.ts` rule: every path except `/_next/static/`,
   `/_next/image`, `/favicon.ico` gets `Cache-Control: private, no-store, max-age=0`. Next rewrites
   Cache-Control on rendered pages (dynamic → its own private no-store; static prerender →
   s-maxage), so the page guarantee is I2A-BUILT-02: no private path is in
   `.next/prerender-manifest.json`. **Test type: static + built-output**, plus a live `next start`
   probe recorded below. Decision: the console's Docs and Models pages stay **private** despite
   public content, because they render the console shell with the signed-in balance; only
   `/`, `/login`, `/signup`, `/verify-email`, `/forgot-password`, `/update-password` are public,
   and I2A-CACHE-02 proves none of them reaches the session, cookies or console services.
4. **Release identity.** `GET /api/version` → `{commit, builtAt, deployment, environment,
   apiOrigin}`; commit is a 40-hex SHA baked at build (else `VERCEL_GIT_COMMIT_SHA`), deployment
   `dpl_...`, builtAt ISO-8601 Z; malformed or absent → `"unknown"`. The route sits behind the
   middleware session guard until WR-I2A-1.
5. **Runbook** `infra/app/README.md`: order after BACKEND-READY, environments/domains, hosting
   settings by name, P-05 per project, identity = commit + Vercel deployment id, known-good by
   record with the schema-compatibility rule, rollback = promote the recorded deployment, six
   smoke checks, seven operator inputs, all marked [OP].

## Commands (apps/app unless stated)

| Command | Exit | Result |
|---|---|---|
| `pnpm install --frozen-lockfile` | 0 | lockfile unchanged |
| `pnpm test` (base fd40748c) | 0 | 508 tests: 485 pass, 0 fail, 23 skip |
| `pnpm build` (base) | 0 | 20.5 s; route table 18 routes |
| `node --test tests/i2a/*.test.ts` before `lib/deploy/` | 1 | 2 files fail: `ERR_MODULE_NOT_FOUND lib/deploy/{env,cache}.ts` |
| same, with `lib/deploy/` but before wiring | 1 | 20 tests: 17 pass, **3 fail**: I2A-START-01 (no instrumentation.ts), I2A-CACHE-01 (next.config sets no headers), I2A-REL-02 (no version route) |
| same, after wiring | 0 | 20/20 pass |
| 10 hand mutants (probe restored after each) | — | 10/10 killed: public `/usage` → CACHE-02; narrowed rule → CACHE-01; preview-on-production check removed → ENV-05; cursor secret optional in production → ENV-02; any Vercel scope → ENV-05 + AUTH-02; NODE_ENV=production guessed as development → ENV-06; any-string SHA → REL-01; a client component importing `lib/deploy/env` → BUNDLE-01; new `process.env.STRIPE_SECRET_KEY` → ENV-08; `try/catch` around the startup check → START-01 |
| `pnpm build` (no `VERCEL_ENV`) | 0 | build does not run `register()`; `/api/version` is `ƒ` (dynamic) |
| `next start -p 3917` with no environment stated | — | every request 500; log `An error occurred while loading instrumentation hook: a production build must state VERCEL_ENV or INFRX_APP_ENVIRONMENT` (fail closed) |
| build with `NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:9` (unreachable placeholder) + temporary WR-I2A-1 middleware patch (restored, not committed); `next start` with `INFRX_APP_ENVIRONMENT=development INFRX_API_BASE_URL=https://api.example.test` | — | `/api/version` 200 `private, no-store, max-age=0`, body `{"commit":"fd40748c…","builtAt":"2026-09-25T23:57:34.572Z","deployment":"unknown","environment":"development","apiOrigin":"https://api.example.test"}`; `/usage` `/docs` 307 → login, `/auth/callback` 307 → `/login?error=link_invalid`, `/login` `/signup` 200 — all `private, no-store, max-age=0`; a real `/_next/static/chunks/*.js` `public, max-age=31536000, immutable` |
| `grep -rlE 'SUPABASE_SERVICE_ROLE_KEY\|INFRX_API_BASE_URL\|CONSOLE_CURSOR_SECRET' .next/static \| wc -l` | — | 0 (18 files in `.next/server`) |
| `rm -rf .next && pnpm build` at head (committed middleware) | 0 | 19 routes |
| `pnpm test` at head (after that build) | 0 | 528 tests: 505 pass, 0 fail, 23 skip (the pre-existing 23); I2A 20/20 with both BUILT cases executed |
| `make console-lint` | 0 | 0 errors, 2 pre-existing warnings (`lib/contracts/conformance.ts`, `fake-services.ts`) |
| `make console-typecheck` | 0 | clean |
| `python3 research/plan/scripts/validate_plan.py` (repo root) | 0 | 4 PASS lines |

## Wiring requests

- **WR-I2A-1** `apps/app/lib/supabase/middleware.ts`: `const PUBLIC = ["/api/version", "/login", "/signup", "/verify-email", "/forgot-password", "/auth"];`
  so smoke S1 works with `curl` (it exposes the commit SHA, build time, deployment id and the
  already-public API origin; nothing per-person). Composed proof: the local `next start` probe
  above (200 JSON, private no-store); add to `tests/a/public-routes.test.ts` A2-ROUTE-01
  `assert.ok(isPublic("/api/version"))` and keep `/api/versionx`, `/api/other` private. If the
  coordinator prefers the route session-gated, drop the request; S1 then runs in a signed-in browser.
- **WR-I2A-2 (optional)** `Makefile`: `console-built: ; cd apps/app && pnpm build && node --test tests/i2a/*.test.ts`
  (and in `check`), so the I2A-BUILT cases run in CI rather than skipping when `.next` is absent.

## Operator inputs (all [OP], none performed)

1. A non-production (staging) Supabase project for previews/development; its URL/keys set **Preview-scoped** in Vercel.
2. Production-scoped `INFRX_API_BASE_URL` (the accepted edge origin) and `CONSOLE_CURSOR_SECRET`
   **before** the first production deploy carrying `instrumentation.ts` — otherwise that deploy
   serves 500 on every request by design; confirm `SUPABASE_SERVICE_ROLE_KEY` Production-scoped + Sensitive.
3. Remove production values scoped to Preview / All Environments. If previews today share the
   production project, they refuse to serve after this lands until item 1 is done.
4. Confirm the Vercel scope slug (`PREVIEW_SCOPE = "humanbit"` from apps/app/supabase/README.md;
   HANDOFF.md says a personal scope) and that system environment variables are exposed.
5. P-05 on both projects (runbook §4), custom SMTP, `signup_grant` when the grant opens; the
   production project's redirect list reduced to the production callback only.
6. Preview Deployment Protection on.
7. The first known-good App release record after the runbook §6 smoke.

## Open issues

- Live part of I2A (deploy, smoke, staging fresh-email journey) waits for BACKEND-READY (E4C) and the inputs above.
- `/update-password` is prerendered static (public client form, no per-person HTML); listed public.
- Development on the production project (the `.env.example` default) is not refused; only previews are.
- Next 16 warns that `middleware.ts` is deprecated in favour of `proxy.ts` (pre-existing, not owned).

## Proposed ruling text

"The App refuses to serve when its environment does not satisfy `apps/app/lib/deploy/env.ts`
(checked once per server start); a preview never targets the production auth/DB project; every
App response except Next static assets is `private, no-store`, and no private page may be
prerendered; an App release is identified by commit + deployment id from `/api/version`, and
rollback promotes the recorded known-good deployment."

## Remaining effort (I2A as a whole)

Optimistic 2 h / likely 5 h / pessimistic 12 h, confidence medium-low (2026-09-26T00:10Z).
Basis: code, tests and runbook done; remaining is WR-I2A-1, the operator inputs (staging project
and SMTP are the pessimistic case), one production deploy + smoke after BACKEND-READY, and E4's journey.

## Fix round (2026-09-26T00:22Z)

Review head `98882cfbe8bb33bc6997a51af1256616c370f730`; fix code head
`a8d9489d34c50c523db5b7dd77b965b15fbfd9f5` (this section and `updates/I2A-20260926T0022Z.json`
are committed on top of it). Local only; no hosted system touched.

| Finding | Resolution |
|---|---|
| 1-I2A-R2 (NEXT_PUBLIC_APP_URL required, unread) | `VARIABLES`: `required: []` (still checked when set: I2A-ENV-04/05). New I2A-ENV-09: a variable may be required only if the App's code reads it (same scan as ENV-08). Runbook §3 row → optional; §7 item 2 now lists the full production-required set (`NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `INFRX_API_BASE_URL`, `CONSOLE_CURSOR_SECRET`); §1 item 4 names **gate APP-MERGE**: the merge of `claude/consumer-v1` into `main` is the production App deploy (coordinator: add it to the tracker as a named gate). |
| 1-I2A-R3 (INFRX_RELEASE_SHA overrides the host commit) | `releaseCommit`: `VERCEL_GIT_COMMIT_SHA` wins when present; `INFRX_RELEASE_SHA` is the off-Vercel build input only; both present and different → `"unknown"`; a present but malformed host value is never replaced. `next.config.ts` bakes each source as itself (no fold); the route reads both textually. Runbook §3 row "never set on Vercel", §5 clean-tree rule, §7 item 2. **Correction to the commands table above:** the `next start` probe's `"commit":"fd40748c…"` was a build of a **dirty tree** (uncommitted I2A code + the temporary WR-I2A-1 patch); it is not a release identity of any commit. |
| 0-F1 / 1-I2A-R1 (paths outside the owned list) | `apps/app/README.md` restored to base (0 lines in `git diff fd40748c..HEAD`); its paragraph is now **WR-I2A-0** below. The other three stay, because the brief's fail-closed startup and release identity need them; the coordinator is asked to ratify them as I2A scope at merge: `apps/app/instrumentation.ts` (Next's only startup hook), `apps/app/app/api/version/route.ts` (the identity endpoint; Next fixes the path), `apps/app/.env.example` (+8, names only). No overlap with the in-flight app lanes (per both reviews). |

### Fails-before (tests written first, run against 98882cfb's code)

`node --test tests/i2a/*.test.ts` → 22 tests, 18 pass, **4 fail**:
I2A-ENV-02 (`App environment refused (production): NEXT_PUBLIC_APP_URL is required in production`),
I2A-ENV-09 (`["NEXT_PUBLIC_APP_URL"]` required but unread), I2A-REL-03 (stale `a…a` reported
instead of `unknown`), I2A-REL-02 (`VERCEL_GIT_COMMIT_SHA` not read by the route / folded in
`next.config.ts`). After the fix: 22/22.

### Commands (apps/app unless stated)

| Command | Exit | Result |
|---|---|---|
| `node --test tests/i2a/*.test.ts` (fix applied) | 0 | 22/22 pass, 0 skip |
| `rm -rf .next && pnpm build` | 0 | 21.8 s; 19 routes; `/api/version` `ƒ` |
| `rm -rf .next && INFRX_RELEASE_SHA=a×40 VERCEL_GIT_COMMIT_SHA=b×40 pnpm build` (placeholders) | 0 | the version route's server chunk carries `INFRX_RELEASE_SHA:"a…"` and `VERCEL_GIT_COMMIT_SHA:"b…"` separately (so `releaseIdentity` reports `unknown`, I2A-REL-03); 0 hits in `.next/static` |
| `make console-test` (repo root, at `a8d9489d`, after that build) | 0 | 530 tests: 507 pass, 0 fail, 23 skip (the pre-existing 23); I2A 22/22 with both BUILT cases executed |
| `make console-lint` | 0 | 0 errors, 2 pre-existing warnings |
| `make console-typecheck` | 0 | clean |
| `python3 research/plan/scripts/validate_plan.py` (repo root) | 0 | 4 PASS lines |

### Wiring requests (added)

- **WR-I2A-0** `apps/app/README.md` (closes A3 WR-3's README line): apply exactly the 4-line hunk
  this fix round removed, i.e. `git diff fd40748c 98882cfb -- apps/app/README.md | git apply`
  (a paragraph after "Never commit real values", pointing at `lib/deploy/env.ts` for the matrix
  and at `infra/app/README.md` for deploy/rollback). Composed proof: documentation only;
  `make console-test` passed with it present at 98882cfb (528 tests, 0 fail).
- WR-I2A-1 and WR-I2A-2 unchanged.

### Remaining effort (I2A as a whole)

Unchanged: optimistic 2 h / likely 5 h / pessimistic 12 h, confidence medium-low
(2026-09-26T00:22Z). Basis as above; the fix round removed one production outage path
(an unset `NEXT_PUBLIC_APP_URL`) from the pessimistic case.
