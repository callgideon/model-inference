# api-frontends-app — AP-09 09a/09b/09e (App) + LR-02 form half — evidence at 164d67f

Lane api-frontends-app (wave 7, batch 2). Branch `codex/w7-api-frontends-app`, worktree
`.claude/worktrees/codex-w7-api-frontends-app`, base `b05eb6f4`. Commits: `70d85028` (WR-AP09A-1:
regenerated `packages/api-client`, AP-00's path), `ea0b50ef` (09a/09b), `322df8b3` (mutants, 09e
check), `164d67fc` (the request client, refresh middleware and client fake moved out of the
`lib/api` port dir - AP-00's package test bans environment reads and key-shaped literals there; the
C3F door-grant re-anchor), then this evidence commit.

## What changed (the App holds no product DB access any more)

- `apps/app/lib/api/` (the port; no env, no fixtures): `cookie.ts` (the facade `Session` as one
  httpOnly cookie; edge-safe), `result.ts` (API answers -> contract `Result`s; exact CREDIT/USD and
  UTC instants; `onceGets`). Beside it: `lib/request-api.ts` (the request's client: the session
  cookie as Bearer, one answer per GET per request, the client fake when INFRX_CONSOLE_PREVIEW opens),
  `lib/session-middleware.ts` (refresh via `POST /auth/v1/refresh` 60 s before expiry; only a 401
  ends a session; an outage keeps it), `lib/fake-api.ts` (typed client fake of the console routes,
  documents typed by the generated OpenAPI schemas).
- Reads (09a): `lib/services/console.ts` (C0 = `GET /console/v1/me` + `/console/v1/*`),
  `billing/credit-reads.ts` (`apiCreditReads`; "spent" is the API's `Credits.spent`),
  `usage/[requestId]/request-reads.ts` (`apiRequestReads`), `admin/operator-reads.ts`
  (`/operator/v1/{accounts,unknown-usage,wallet-drift,audit}`), `teams/page.tsx`
  (`/console/v1/account/members`), `welcome` (`/console/v1/credits`), `lib/session.ts` (`/console/v1/me`).
- Actions (09b): `lib/services/actions.ts` (keys `POST/DELETE /console/v1/keys` with the dialog's
  Idempotency-Key - no minting, no hash, no per-process replay Map; feedback
  `POST /console/v1/requests/{id}/feedback`; operator `POST /operator/v1/*` via `admin/operator-port.ts`),
  grant `POST /console/v1/signup-grant/claim` (`flow.ts claimGrant`), auth forms through
  `/auth/v1/{sign-in,sign-up,recovery,password,sign-out}` server actions (`app/(auth)/auth-actions.ts`,
  `session.ts`; PKCE verifier in an httpOnly cookie, challenge to the facade), callback via
  `GET /auth/v1/callback` (`app/auth/callback/route.ts`), `/auth/expired` clears a refused session.
- Durable uncertain-outcome UX for key creation: the dialog's one idempotency key now replays across
  API instances; `secret_returned:false` (or `replayed`) -> "created by an earlier attempt, secret
  cannot be shown again; revoke and create another" (existing REPLAYED_COPY), never a secret.
- LR-02 form half: `GET /auth/v1/availability` `captcha_required` with no widget configured -> the
  signup form says so up front and both email actions refuse on the server ("captcha_unconfigured",
  honest unavailable); a `captcha_token` form field is forwarded to sign-up/recovery as-is.
- Deleted: `lib/supabase/{admin,server,client,middleware}.ts`, `lib/keys.ts(+test)`,
  `lib/services/{query,cursor,credits,feedback}.ts`, `lib/credits.ts`, `billing/credit-fixture.ts`,
  `app/(auth)/grant.ts`, the PostgREST twins and real-DB worlds of the deleted adapters
  (`tests/c/realdb/*`, `tests/u/{credit_world,request_world,operator_stack}.py`,
  `tests/{c,u,a}/*-postgrest|*-pg.test.ts`, `tests/a/pg_up.py`), the C1 conformance harness over the
  deleted `createConsoleServices`.
- Env (`lib/deploy/env.ts`): SUPABASE_SERVICE_ROLE_KEY and CONSOLE_CURSOR_SECRET leave the matrix;
  NEXT_PUBLIC_SUPABASE_URL/ANON_KEY are no longer read (required: [], still checked when set);
  INFRX_API_BASE_URL stays production-required.
- Outside the listed paths, necessary to consume the linked client in a bundle: `apps/app/next.config.ts`
  and `apps/app/tests/ux/harness/next.config.ts` (Turbopack root = the directory holding the App and
  `packages/api-client`), `apps/app/middleware.ts` (import path), `app/auth/{callback,expired}/route.ts`.
  Overlap with ux-app-2: `billing/credit-view-model.ts` (`creditCardState(wallet)`, spent from the
  wallet), `billing/page.tsx`, `usage/page.tsx` (two call sites) - minimal edits.

## API-BOUNDARY (the deliverable the gate reads)

`apps/app/tests/boundary/{inventory.ts,api-boundary.test.ts}`: rules supabase-sdk (the auth-transport
allowlist is EMPTY), table-read, rpc, service-role, sql-driver, sql-text, key-minting,
credit-arithmetic, cursor-signing, over every `.ts/.tsx/.mjs` of the App except tests and
`lib/contracts/`. One display allowance: `billing/credit-view-model.ts` `walletReconciles` (compares
the API's own three figures). Plus AP09-E01: every `api.call(method, path)` in the App is a documented
operation of `apps/infrx-api/openapi/consumer.json`.
- RED at base b05eb6f4: `node --test tests/boundary/api-boundary.test.ts` exit 1, 2 of 5 cases fail,
  **80 findings** (lib/supabase/* sdk + service role, grant.ts rpc, credit-reads/operator-reads/
  teams/session/console table reads + rpcs, query.ts SQL, keys.ts/actions.ts minting, credit-fixture/
  credit-reads/billing/usage/operator-reads/credits.ts arithmetic, env.ts + server.ts cursor secret).
- GREEN at 164d67f: 6/6 pass, **0 findings**.

## Commands (exit, counts)

| command | exit | result |
|---|---|---|
| `cd packages/api-client && pnpm generate && pnpm typecheck && pnpm test && pnpm test:mutants` | 0 | regenerated (consumer.ts +5190 lines: the console/operator/auth paths were missing); 6/0; 10/10 killed |
| `make console-test` (base) | 0 | 692 tests / 633 pass / 0 fail / 59 skip |
| `make console-test` (head 164d67f) | 0 | 564 / 564 / 0 / 0 (the PG-skipping twins of deleted adapters are gone) |
| `make api-client-test` (head) | 0 | typecheck + 6/0 (at 322df8b it was 5/1: the port dir read the environment - fixed by 164d67f) |
| `make console-lint` | 0 | 0 errors (2 pre-existing warnings in lib/contracts) |
| `make console-typecheck` | 0 | clean |
| `make console-built` | 0 | build ok; I2A built cases 22/22 (322df8b and 164d67f) |
| `node tests/c/run-mutants.mjs --self-test` / full | 0 / 0 | 3/3 self-tests; 66/66 killed |
| `node tests/u/run-mutants.mjs` | 1 -> 0 | first run 214/218 (M54, M10 survived; M16x runner-error; M20 stale) -> fixed (USD ledger case, R03 page-down case, TAP parser `...` fix, M20 re-anchored); rerun of the four 4/4 killed |
| `node tests/a/run-mutants.mjs` | 0 | 60/60 killed |
| `node tests/c/feedback/run-mutants.mjs` | 0 | 8 cases all named; 17/17 killed |
| `node tests/contracts/run-mutants.mjs --self-test` + `pnpm test:mutants` | 0 | 14/14; 212/212 |
| `node tests/a/run-catalog-mutants.mjs`, `tests/ux/run-mutants.mjs`, `tests/ux/first-call/run-mutants.mjs` | 0 | 46/46, 26/26, 36/36 |
| `make console-mutants` (322df8b, full chain) | 0 | contracts 14/14 + 212/212; U 218/218; C 3 self + 66/66; A 60/60; catalog 46/46; C3F 17/17; UX 26/26; first-call 36/36. After 164d67f's move: C 66/66, A 60/60, C3F 17/17, U (the moved-path mutants M30/M31, U4-M09, U3-M20) 4/4 |
| `make console-c3f-real` (key app-c3f) | 0 | 6 checks 0 failed; Lab adapters 2+2 pass; 22/22 SQL mutants killed. Before the re-anchor: exit 2 - `check_only_signed_in_sessions_reach_the_doors` failed on base because 0064 (AP-08) grants `lab_review_feedback` to `infrx_lab_control`; re-anchored (that login is not a browser role) |
| composed parity (WR-AP09-PARITY applied transiently, `INFRX_D_TASK=app-u1r` = console-pg's key, conftest key check transiently widened, then restored) `pytest tests/ap02/test_parity_pg.py` | 0 | **1 passed**: the App's shipped adapters (apiCreditReads/apiRequestReads/operatorReads) against infrx-api's console + operator routes over HTTP on the seeded PostgreSQL give identical wallet/spent/ledger/legacy/requests/results/keys/operator values. Negative control: `spent: null` in credit-reads -> 1 failed (`assert None == '0.88800000'`) |
| `uvx ruff@0.15.12 check` on the patched `test_parity_pg.py` (scratch copy) | 0 | clean |
| `python3 research/plan/scripts/validate_plan.py` | 1 -> 0 | 2 broken links to the deleted `lib/credits.ts` in historical handoffs; 0 with WR-AP09-LINKS applied |

No infrx-api source changed on the branch (api-lint/api-typecheck not touched; the parity patch is a
wiring request). Local acceptance per test: tests/{a,c,u,boundary} use the client fake / recorded
fetch (`lib/fake-api.ts`); the real-DB acceptance is the composed parity on PostgreSQL (above).

## Real-DB gates (brief: re-pointed or retired)

- `console-c0-real`, `console-c3a-real`, `console-u3-real`: **retired** (their scripts tested the
  App's PostgREST adapters, which are deleted; the SQL they reached is now called only by infrx-api and
  proven by tests/ap02 (reads) and tests/ap03 (actions) on PostgreSQL).
- `console-pg`: **re-pointed** at the composed parity (tests/ap02 on ap2) - WR-AP09-MAKE.
- `console-c3f-real`: **kept** (SQL door checks + the Lab's review adapters until api-frontends-lab
  moves them); the App adapter run removed from it.

## Wiring requests

- **WR-AP09A-1** `packages/api-client/src/{consumer,lab}.ts`: regenerated (commit 70d85028; the
  committed client lacked every console/operator/auth path). Composed test: `make api-client-test`
  (6/0) + `make api-client-mutants` (10/10); the App's `console-typecheck` depends on it.
- **WR-AP09-PARITY** `apps/infrx-api/tests/ap02/{parity_harness.ts,test_parity_pg.py}`: patch
  `api-frontends-app-164d67f-WR-AP09-PARITY.patch` (the harness imported the deleted
  `postgrestCreditReads`/`spentCredit`/`postgrestRequestReads` and `operatorReads(client)`; it now runs
  the shipped adapters against the routes served by uvicorn on loopback). Composed test:
  `INFRX_D_TASK=ap2 uv run --frozen pytest -q tests/ap02/test_parity_pg.py` -> 1 passed (run here on
  app-u1r as above). Without this patch tests/ap02's pg run FAILS at merge.
- **WR-AP09-MAKE** `Makefile`: patch `api-frontends-app-164d67f-WR-AP09-MAKE.patch` (remove
  console-c0-real/c3a-real/u3-real; console-pg -> the parity). CLAUDE.md's Commands list names the
  removed targets (coordinator's file).
- **WR-AP09-LINKS** `research/plan/handoffs/{C-console-services,D-durable-state}.md`: the historical
  handoffs link `apps/app/lib/credits.ts`, which this lane deletes (dead `.rpc` reader).
  `validate_plan.py` exits 1 on the two broken links until patch
  `api-frontends-app-164d67f-WR-AP09-LINKS.patch` (link -> plain text) is applied; with it, exit 0.
- **WR-AP09-LOCK** `apps/app/package.json` + lockfile: drop `@supabase/ssr` and `@supabase/supabase-js`
  (no importer left; API-BOUNDARY forbids them). Composed test: `make console-test console-built`.
- **WR-AP09-RESEND** (api-identity-2, `infrx/auth_facade` + `gateway/routes/auth.py`): `POST /auth/v1/resend`
  `{email, captcha_token?, redirect_to?, code_challenge?}` -> `Sent` (IdP `POST /auth/v1/resend`
  `type=signup`; enumeration-safe like recovery), then OpenAPI + client regeneration. Until then the
  App's resend form shows `RESEND_UNAVAILABLE` (no button that sends nothing). App follow-up: one
  `requestResend` in flow.ts + the form.
- **WR-AP09-ME-EMAIL** (api-identity-2, `gateway/routes/console_me.py`): `Me.email: str | None`; the
  App then drops `tokenEmail` (display-only decode of the access token's email claim, ponytail-marked).
- **WR-AP09-CAPTCHA** (coordinator, P-05): the chosen hosted challenge's widget pinned as a dependency
  (or its script + CSP), `NEXT_PUBLIC_CAPTCHA_SITE_KEY` in the env matrix and the P-05 checklist;
  the App then renders it into the forms (token field `captcha_token`) and `captchaGate` gains "ready".
- **WR-AP09-DEPLOY** (coordinator, batch 3 - production cutover): the App at this head needs the
  gateway at INFRX_API_BASE_URL with IDENTITY_API, AUTH_FACADE, CONSOLE_READS, CONSOLE_ACTIONS_API
  ON and WEB_ORIGINS containing the App origin (redirect_to and the Origin checks). Deployed before
  that, every page is honestly unavailable and sign-in fails. Existing `sb-*` cookies are ignored:
  every user signs in once after the cutover.

## Schema requests

None.

## Open items

- The request-detail feedback control (UI) is ux-app-2's (usage/ presentation); the action is live
  over the API (`submitFeedback`).
- `GET /console/v1/capabilities` is not consumed: key creation is gated by `/me` (ready, not
  suspended), equivalent to `actions.create_key` by construction; `/dedicated` is a mailto, no inert
  purchase control.
- Proposed ruling (unnumbered): "The App's real-DB acceptance is the composed parity (the shipped
  adapters against infrx-api on a seeded PostgreSQL); App-side PostgREST worlds are retired with the
  adapters they tested."

## Estimate (remaining, hours)

optimistic 2 / likely 4 / pessimistic 8, confidence medium. Basis: the code and gates are done
locally; remaining = merge reconciliation with ux-app-2's billing/usage presentation edits, the
resend/me-email follow-ups once api-identity-2 lands, the CAPTCHA widget once P-05 names the
provider, and the hosted cutover verification (batch 3, coordinator).
