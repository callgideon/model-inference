# infrx console (`apps/app`)

The customer console: sign in, browse models, copy a working request, manage API
keys, see usage and balance, read the docs. Next.js (App Router) on Vercel,
Supabase for auth and Postgres. Spec: [`../README.md`](../README.md).

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
  unset — the login page builds the callback from `window.location.origin`.
- Domain `app.callbill.ai` via a Route 53 CNAME to `cname.vercel-dns.com`.

## Supabase auth settings

In Authentication → URL Configuration:

- **Site URL**: `https://app.callbill.ai`
- **Redirect URLs**: `https://app.callbill.ai/auth/callback`,
  `http://localhost:3000/auth/callback`, and the preview pattern
  `https://*-humanbit.vercel.app/auth/callback`.

Google OAuth via Supabase is the only sign-in method — there is no email
sign-in. The Google provider is enabled in Authentication → Providers with the
callgideon Google OAuth client, and that client must list
`https://fcbnscgsymzdykendbrc.supabase.co/auth/v1/callback` as an authorized
redirect URI. The login page calls `signInWithOAuth`, and `/auth/callback`
exchanges the code for a session and forwards to `?next=`.

## Structure

```
app/(auth)/login        Google sign-in
app/auth/callback       code exchange
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
