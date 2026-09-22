# Consumer inference App (`apps/app`)

**2026-09-22 sequence:** [Marlin inference backend first](../../research/plan/18-marlin-backend-first.md), consumer App next, provider Lab afterward. The backend has independent headless deployment/recovery/performance gates; frontend feature work is subsequent.

**Current execution:** [Marlin App-first complete plan](../../research/plan/12-complete-build-plan.md) and [fresh-session prompt](../../research/plan/16-fresh-session-handoff.md). Preserve the audited wave-2 work; actual product-v2 and runtime integration remain pending.

The customer console: sign in, browse models, copy a working request, manage API
keys, see usage and balance, read the docs. Next.js (App Router) on Vercel,
Supabase for auth and Postgres. Current [requirements](../../research/platforms/03-app-spec.md)
and [roadmap](../../research/platforms/04-app-roadmap.md) supersede the historical
single-console spec. Provider models/endpoints/traces/evaluation belong in
[`apps/lab`](../lab/README.md).

Target onboarding is public verified signup with **10,000 credits once per
individual user**, only a free plan initially. This README's invite-only setup
below describes the existing baseline, not the target. The [implementation
audit](../../research/plan/10-wave2-platform-audit.md) reconciles wave 2 at `271add9`.
Usage/Balance/Traces currently have development-only fixture previews, enabled by
`INFRX_CONSOLE_PREVIEW=1` under `next dev`; production always shows an unavailable
state until C0 supplies real account reporting. Sidebar amounts remain exact legacy
USD, including holds — or fixed "Balance unavailable" copy when the wallet summary
cannot be read, never a substituted number. No public signup or CREDIT grant
implementation is claimed.

The preview gate is decided when the bundle is built, not from the environment the
server is started with, because Next inlines only the textual `process.env.NODE_ENV`.
After `pnpm build`, the production chunk must contain no fixture path at all:

```bash
pnpm build
grep -rl "INFRX_CONSOLE_PREVIEW" .next --include="*.js"; echo "exit=$?"   # exit=1, no file
grep -rl "createFakeConsoleServices" .next --include="*.js"; echo "exit=$?" # exit=1, no file
grep -rho 'consoleContext",0,function.\{0,40\}' .next/server --include="*.js" | sort -u
# consoleContext",0,function(a=process.env){return null}
```

## Run locally

```bash
pnpm install
cp .env.example .env.local     # fill in the Supabase keys
pnpm dev                       # http://localhost:3000
```

`pnpm lint` and `pnpm build` must pass before pushing. `pnpm format` runs Prettier.

## Environment

| variable                        | where            | what                                                         |
| ------------------------------- | ---------------- | ------------------------------------------------------------ |
| `NEXT_PUBLIC_SUPABASE_URL`      | browser + server | `https://fcbnscgsymzdykendbrc.supabase.co`                   |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | browser + server | publishable key; RLS is what protects the data               |
| `SUPABASE_SERVICE_ROLE_KEY`     | server only      | used by `/admin` ledger writes; never send it to the browser |
| `NEXT_PUBLIC_APP_URL`           | browser          | origin used to build the auth callback URL                   |

Never commit real values: `.env*` is gitignored except this repo's `.env.example`.

## Database

Migrations live in [`supabase/migrations`](supabase/migrations) and are applied
with the Supabase CLI against project `fcbnscgsymzdykendbrc`:

```bash
supabase link --project-ref fcbnscgsymzdykendbrc
supabase db push
```

The console reads with the user's JWT and relies on RLS; it calls three SQL
functions: `org_usage_summary(p_org, p_from, p_to, p_key)`,
`org_usage_daily(...)` and `org_balance(p_org)`.

## Deploy on Vercel

- Project `infrx-app`, Git-linked to `callgideon/model-inference`,
  **root directory `apps/app`**, production branch `main`.
- Framework preset Next.js; build and install commands are the defaults.
- Environment variables: the four above, with `NEXT_PUBLIC_APP_URL` set to
  `https://app.callbill.ai` in production. Preview deployments can leave it
  unset — the reset email's callback is built from `window.location.origin`.
- Domain `app.callbill.ai` via a Route 53 CNAME to `cname.vercel-dns.com`.

## Supabase auth settings

Sign-in is **email + password, invite only**. Public sign-up is disabled, so
there is no sign-up page: an operator creates the account, the user sets their
own password from "Forgot password". Google OAuth is gone.

In Authentication → Providers: **Email** enabled, "Allow new users to sign up"
**off**, minimum password length **10**.

In Authentication → URL Configuration:

- **Site URL**: `https://app.callbill.ai`
- **Redirect URLs**: `https://app.callbill.ai/**` and
  `http://localhost:3000/auth/callback`.

### Creating a user

Either in Authentication → Users → **Add user** (dashboard; set a throwaway
password and leave "Auto Confirm User" on), or with the admin API:

```bash
curl -X POST "$NEXT_PUBLIC_SUPABASE_URL/auth/v1/admin/users" \
  -H "apikey: $SUPABASE_SERVICE_ROLE_KEY" \
  -H "Authorization: Bearer $SUPABASE_SERVICE_ROLE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"email":"someone@example.com","password":"'"$(openssl rand -base64 18)"'","email_confirm":true}'
```

Then tell the user to go to `/forgot-password` and enter that address: the
email links to `/auth/callback?token_hash=…&type=recovery&next=/update-password`,
which verifies the link, signs them in and drops them on `/update-password` to
choose their first password. The `auth.users` trigger creates their profile,
organization and membership on first sign-in.

### The routes

- `/login` — `signInWithPassword`, then `router.push(?next=…)` (same-site paths only).
- `/forgot-password` — `resetPasswordForEmail`; always answers "if that address
  has an account, a reset link is on its way" (no account enumeration).
- `/update-password` — `updateUser({ password })`; needs a session, reached from
  the recovery link or from "Change password" in the sidebar user menu.
- `/auth/callback` — exchanges `?code=` (PKCE) or verifies `?token_hash=&type=`,
  forwards to `?next=`, and sends failures to `/login?error=…`.

## Structure

```
app/(auth)/…            login, forgot-password, update-password
app/auth/callback       code exchange / recovery-link verification
app/(console)/…         models, usage, api-keys, billing, teams, dedicated, docs, admin
components/             sidebar, snippet (Copy & Run), tiles, shadcn/ui in components/ui
lib/supabase/           client (browser), server (cookies), middleware (session), admin (service role)
lib/keys.ts             key generation + SHA-256
middleware.ts           session refresh + console route guard
```

API keys are `sk-infrx-` + 40 base62 characters from the CSPRNG. Only the
SHA-256 hex and the `sk-infrx-` + 8-character prefix are stored; the secret is
shown once, in the create dialog.

## Deployment (live)

Vercel project `infrx-app` (`prj_W8JNBx71exKW6iEPBaALx1R9IxKn`, account
gideon@callgideon.com), Git-linked to `callgideon/model-inference`, root
directory `apps/app`, production branch `main`, domain
`https://app.callbill.ai`. Environment variables are set in the project
(Supabase URL, publishable key, secret key as a sensitive var, app URL);
the same values live in AWS SSM under `/INFRX-SUPABASE-PROD/*`.
