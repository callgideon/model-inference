# App release runbook — `apps/app` on Vercel (I2A)

**Operator-run, preparation only.** The I2A-PREP lane wrote this runbook and the code it relies on
(`apps/app/lib/deploy/`, `instrumentation.ts`, `next.config.ts`, `app/api/version/route.ts`, tests
`apps/app/tests/i2a/`). It changed nothing on Vercel, hosted Supabase, DNS, AWS or the pilot box.
Every item marked **[OP]** is held by the operator; nothing here is a live-state claim.

The App reuses the independently deployed backend (I2B/I8, [rollout.md](../runbooks/rollout.md),
[infra/rollout/README.md](../rollout/README.md)). It adds hosting, auth callbacks/signup and the
browser journey; it never deploys or restarts runtime infrastructure.

## 1. Order relative to the backend window

1. The backend release is accepted (**BACKEND-READY**: E4C accepted; the edge's public origin
   answers `GET /v1/models`). The App release never shares the backend's window.
2. The App's hosted inputs (§3, §4) are set **before** the App release is deployed: from this
   release on, a server whose environment is incomplete refuses to serve (every request 500,
   log line `App environment refused (<env>): <variable names>`), by design.
3. The App release is deployed (§5), smoke-checked (§6) and recorded as the known-good App release.

## 2. Environments and domains

| Environment | `VERCEL_ENV` | Origin | Auth/DB project | Notes |
|---|---|---|---|---|
| production | `production` | `https://app.callbill.ai` (Route 53 CNAME → `cname.vercel-dns.com`) | production `fcbnscgsymzdykendbrc` | the only origin the production project accepts |
| preview | `preview` | `https://infrx-app-<hash\|git-branch>-<scope>.vercel.app` | a **non-production (staging) project** [OP] | refused at startup if pointed at the production project |
| development | `development` (or unset under `next dev`) | `http://localhost:3000` | staging or local Supabase | `next start` outside Vercel must set `INFRX_APP_ENVIRONMENT` |

`<scope>` is `PREVIEW_SCOPE` in `apps/app/lib/deploy/env.ts` (`humanbit`, from
`apps/app/supabase/README.md`). ⚠️ TO BE VERIFIED [OP]: HANDOFF.md places the project in a
personal Vercel scope; confirm the slug from a real preview URL and correct the constant if it differs.

## 3. Hosting settings (names only; values live in Vercel, never in the repo)

Vercel project `infrx-app` (root directory `apps/app`, framework Next.js, Node ≥ 22.18 per
`package.json` `engines`) [OP to confirm each]:

- **Environment Variables**, per scope. The matrix is `VARIABLES` in `apps/app/lib/deploy/env.ts`;
  the server checks it at startup (`instrumentation.ts`):

  | Name | Exposure | Production | Preview | Development |
  |---|---|---|---|---|
  | `NEXT_PUBLIC_SUPABASE_URL` | public (inlined at build) | required, **must be** the production project | required, **must not be** the production project | required |
  | `NEXT_PUBLIC_SUPABASE_ANON_KEY` | public | required | required (staging project's) | required |
  | `NEXT_PUBLIC_APP_URL` | public | required, exactly `https://app.callbill.ai` | optional; if set, a preview host of this project | optional; `http://localhost:3000` |
  | `SUPABASE_SERVICE_ROLE_KEY` | **server only**, mark Sensitive | required | optional (staging project's only) | optional |
  | `INFRX_API_BASE_URL` | **server only** | required, https origin with no path (the accepted edge origin) | optional | optional (loopback http allowed) |
  | `CONSOLE_CURSOR_SECRET` | **server only**, mark Sensitive | required, ≥ 16 characters | optional (usage/trace pages fail closed without it) | optional |
  | `INFRX_APP_ENVIRONMENT` | server | not needed on Vercel (`VERCEL_ENV` is read) | not needed | set for `next start` off Vercel |

  No DSN, Sentry or alert variable exists in the App; none is invented here.
- **System Environment Variables**: "Automatically expose System Environment Variables" ON, so
  `VERCEL_ENV`, `VERCEL_GIT_COMMIT_SHA` and `VERCEL_DEPLOYMENT_ID` reach build and runtime.
- **Deployment Protection**: Vercel Authentication (Standard Protection) on preview deployments;
  production stays public. Preview variables are **scoped to Preview only** — never "All
  Environments" for any server-only or production value.
- **Domains**: `app.callbill.ai` assigned to Production only.
- **Git**: production branch as recorded in `apps/app/README.md` (`main`); a push there is a
  production deploy, so the App release is the merge (§5).

## 4. Hosted auth settings (P-05) [OP]

Listed by name with the value the code expects (`P05_SETTINGS`, `REDIRECT_ALLOWLIST` in
`apps/app/lib/deploy/env.ts`; tests `I2A-AUTH-03/04`). Set in the Supabase dashboard of each
project; the lane set none of them.

| Setting | Production project | Staging project |
|---|---|---|
| Site URL | `https://app.callbill.ai` | the staging App origin |
| Redirect URLs | `https://app.callbill.ai/auth/callback**` **only** (remove `http://localhost:3000/...` and the preview pattern if present) | `https://infrx-app-*-<scope>.vercel.app/auth/callback**`, `http://localhost:3000/auth/callback**` |
| Confirm email | on (unverified sign-in off) | on |
| Allow new users to sign up | on | on |
| Minimum password length | ≥ 8 (client hint `MIN_PASSWORD_LENGTH` = 8) | same |
| Custom SMTP | configured (sender domain, credentials by name in the dashboard only) | configured or the default sender for testing |
| Rate limits | emails/hour, sign-ups, sign-ins, verifications per IP set for launch | any |
| Email templates | default `{{ .ConfirmationURL }}`, or cross-device: `{{ .SiteURL }}/auth/callback?token_hash={{ .TokenHash }}&type=email&next=/welcome` (`type=recovery&next=/update-password` for reset) | same |
| `infrx.feature_flags.signup_grant` | enabled by the audited operator action when the grant opens | as needed |

Glob semantics (`**` any characters) are the auth service's; the first staging signup (§6 S4)
is the check that the entries cover `/auth/callback?next=...`.

## 5. Release identity, deploy and rollback (I8 discipline)

- **Identity** of an App release = the full commit SHA + the Vercel deployment id. Both are
  served by `GET /api/version` (`{commit, builtAt, deployment, environment, apiOrigin}`,
  `Cache-Control: private, no-store`); anything absent reads `"unknown"`, never a guess. The commit
  is fixed at build (`INFRX_RELEASE_SHA`, else `VERCEL_GIT_COMMIT_SHA`).
- **Known-good App release**: a deployment whose §6 smoke passed, recorded in the session record
  as `{commit, deployment, UTC, smoke output}` — by record, not by the existence of a deployment.
  Like `known-good.py` for the backend, a rollback target must be compatible with the applied
  schema: its tree's newest migration ≤ the hosted applied migration (migrations are additive).

Deploy:

1. **[OP]** Log purpose, expected effect and rollback target (the current known-good App
   deployment id) in the session record; hold the deployment lock ([infra/README.md §1](../README.md)).
2. Gates at `RELEASE`, locally: `git rev-parse HEAD` = `RELEASE`, clean tree; `make console-test
   console-lint console-typecheck`; `cd apps/app && pnpm build && node --test tests/i2a/*.test.ts`
   (the `I2A-BUILT-*` cases then run instead of skipping: no server-only name in `.next/static`,
   no private page prerendered).
3. **[OP]** Confirm §3/§4 inputs for the target environment (names present; never print values).
4. **[OP]** Deploy `RELEASE` to Production (merge to the production branch, or promote the
   preview deployment built from `RELEASE`).
5. Smoke (§6). Pass → record the new known-good. Fail → rollback.

Rollback = redeploy the previous identity: **[OP]** Vercel Instant Rollback / promote the recorded
known-good deployment id (it carries its own build; no rebuild), then §6 against it and record.
Never "fix forward" on production without a new release identity. The backend is not touched.

## 6. Smoke checks after deploy

| # | Check | Pass |
|---|---|---|
| S1 | `curl -sS -D - https://app.callbill.ai/api/version` (anonymous once WR-I2A-1 is applied; until then signed in, in a browser) | 200; `commit` = `RELEASE`; `environment` = `production`; `apiOrigin` = the accepted edge origin; `Cache-Control: private, no-store, max-age=0` |
| S2 | Docs point at the right origin: `curl -sS "$apiOrigin/v1/models"` and, signed in, `/docs` examples' `BASE` | 200 list; `BASE` = `apiOrigin` |
| S3 | Private caching: `curl -sSI https://app.callbill.ai/usage` (anonymous: the login redirect) and, signed in, the response headers of `/usage`, `/billing`, `/api-keys`, `/traces` | every one `Cache-Control: private, no-store...`; no `s-maxage`, no `public` |
| S4 | Signup callback: a fresh email on the **staging** project first, then production: sign up → email link → `/auth/callback` → `/welcome` | lands on `/welcome`; the grant state matches the `signup_grant` flag; E4 records the full journey |
| S5 | Preview safety: open a preview URL anonymously; its `/api/version` once signed in | Vercel authentication wall; `environment` = `preview`; a preview pointed at the production project refuses to serve |
| S6 | Static assets: any `/_next/static/...` chunk | `Cache-Control: public, max-age=31536000, immutable` |

## 7. Operator inputs

1. **[OP]** A non-production (staging) Supabase project for previews/development, with its URL,
   publishable key and (optional) service-role key set **Preview-scoped** in Vercel.
2. **[OP]** Production-scoped `INFRX_API_BASE_URL` (accepted edge origin) and `CONSOLE_CURSOR_SECRET`
   before the first deploy carrying `instrumentation.ts`; confirm `SUPABASE_SERVICE_ROLE_KEY` is
   Production-scoped and Sensitive.
3. **[OP]** Remove any production value that is currently scoped to Preview or "All Environments".
4. **[OP]** Confirm the Vercel scope slug (`PREVIEW_SCOPE`) and system-variable exposure.
5. **[OP]** P-05 (§4) on both projects; custom SMTP credentials; `signup_grant` when the grant opens.
6. **[OP]** Preview Deployment Protection on.
7. **[OP]** The first known-good App release record after §6.

## Verification log

- 2026-09-26: Written by I2A-PREP with the code it describes; local checks only (`pnpm test`,
  lint, typecheck, `next build`, `next start` header probes). No hosted setting was read or changed.
