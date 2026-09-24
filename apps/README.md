# Console and API — requirements and architecture

> **Current product scope (2026-09-21):** [Two-platform architecture](../research/platforms/README.md) supersedes conflicting product and launch assumptions below. apps/app is the free consumer product with 10,000 credits once per individual; apps/lab is the provider product. Historical research and measurements remain context, not current implementation instructions.


> **Implementation amendment — 2026-09-20.** Use [the unified handoff package](../research/plan/README.md) for current scope, dependencies and acceptance. First release is a free single-GPU pilot with promotional holds/settlement, existing organization roles, explicit async, opt-in trace content and separately consented evaluation. Payments, OpenRouter and commercial second-owner launch are deferred; do not treat old auth, payment, no-content-storage or ZDR assumptions as current implementation instructions.


Status: spec, 2026-09-20. Two applications:

| app | what | runs on |
|---|---|---|
| `apps/app` | customer console: sign in, browse models, copy a working request, manage API keys, see usage and balance, read docs | Vercel (Next.js), Supabase (Postgres + Auth) |
| `apps/infrx-api` | the OpenAI-compatible inference gateway in front of vLLM (`infrx.gateway.app:create_app`), its deployment files, and the OpenRouter provider document | AWS EC2 GPU instances (systemd + Caddy) |

Reference for the console's shape: Wafer's app (models catalog with per-model
input/output/cache prices and a Copy & Run snippet, Usage with request/latency
tiles and a per-key filter, API Keys with reveal-once, Billing with prepaid
credits and Stripe invoices, Teams, Dedicated, Docs). We copy the information
architecture, not the branding.

## 1. Problem statement

We serve specialist models (Marlin-2B first) behind a paid OpenAI-compatible
API. Today the endpoint exists (`https://marlin2b.callbill.ai`) with one
hand-made key in SSM. People who want to try it have no way to sign up, get a
key, see what it costs, or see what they used. The console closes that gap so
an invited developer can go from sign-in to a successful call without an
operator, and so we can see who is calling what. It is also the first slice
of the model-to-API platform described in
[`research/inference-platform/`](../research/inference-platform/README.md):
the P0 rows "API keys", "customer usage", "developer onboarding" and the
metadata half of "request observability".

Out of scope for this slice (P1 in the research): payments and hard spend
protection, dedicated deployments, model-owner onboarding, teams beyond a
single organization per user, traces with content. The console shows the
places for them so the product reads complete. Traces with content are now
specified separately in [`research/traces/`](../research/traces/README.md)
(per-key opt-in, ClickHouse + S3, a Traces page, feedback API, LLM judge);
the console changes it needs are in `research/traces/07-console-spec.md`,
and its Supabase migration `0003_traces.sql` in `04-data-model.md` §5.

## 2. Users and journeys

1. **Invited developer** signs in with email (magic link) or Google, lands on
   Models, copies the Marlin cURL/Python snippet with their key filled in,
   runs it, and sees the request in Usage within a minute.
2. **Operator (us)** sees every organization's usage and keys, adjusts
   credits, and disables a key. Operator role is a flag on the profile.
3. **Automation** (the gateway) validates keys and records usage without a
   user session, via the service-role key.

## 3. Functional requirements

| id | requirement | acceptance |
|---|---|---|
| F1 | Sign in with Supabase Auth: email magic link and Google OAuth; sign out; session persists | unauthenticated visit to any console route redirects to `/login`; after login the user lands where they were going |
| F2 | First sign-in creates a `profiles` row and a personal `organizations` row with the user as owner | one org per user at launch; the org id scopes every other table |
| F3 | Models page lists catalog rows from `models`: name, provider, description, input/output/cache $ per 1M tokens, context, modalities, status (`live`, `coming_soon`), and a Copy & Run block with cURL / Python / JavaScript tabs whose snippet uses the real base URL and the user's selected key | copying the cURL for Marlin and running it returns a 200 |
| F4 | API Keys: create (name), reveal the full key once, list (name, prefix, created, last used), revoke | a revoked key gets 401 from the gateway within 60 s |
| F5 | Usage: tiles for requests, avg req/s, TTFT p50, tok/s p50, latency p50, error rate, cache hit; time-range picker (5m…30d); per-key filter; table and daily graph of tokens | numbers reconcile with `usage_events` for the org |
| F6 | Billing: balance (sum of `credit_ledger`), loaded/spent, invoices list (empty), "Add credits" and "Add card" disabled with "coming soon" | balance equals ledger sum; no payment path exists yet |
| F7 | Docs: quickstart, the two Marlin prompts, limits, error codes, and the same snippets as the catalog | a new developer completes a call from docs alone |
| F8 | Teams and Dedicated: pages exist, show the single org and members, and a "request dedicated capacity" mailto | no functionality beyond display |
| F9 | Gateway validates `Authorization: Bearer <key>` against `api_keys` (SHA-256 of the key), caches results for 60 s, updates `last_used_at`, and writes one `usage_events` row per request with `Inference-Id`, tokens, video seconds, TTFT, latency, status and computed `cost_usd` from the model's current prices | usage appears in the console within 60 s of a call |
| F10 | Operator view: `/admin` lists orgs, keys and usage totals; operator can add a ledger entry with a reason | non-operators get 404 |

## 4. Non-functional requirements

- Row-Level Security on every table; the anon key can read only what the
  signed-in user's org owns; service role used only by the gateway and server
  actions.
- Keys are stored hashed (SHA-256, hex); the prefix (`sk-infrx-` + 8 chars)
  is stored in clear for display. Full key shown exactly once.
- Latency: console pages render from Supabase in <1 s; gateway auth adds
  <5 ms with a warm cache and <100 ms cold.
- The gateway must keep serving if Supabase is unreachable for a key already
  in its cache; new keys fail closed with 503.
- No prompt or video content is stored anywhere; usage rows are metadata only.
- Clean stack: Next.js (latest stable, App Router, Server Components, Server
  Actions), TypeScript strict, Tailwind v4, shadcn/ui, `@supabase/ssr`,
  pnpm, ESLint + Prettier, no ORM (Supabase client + SQL migrations).

## 5. Architecture

```
browser ──HTTPS──▶ Vercel (apps/app, Next.js)
                    │  server components / actions use Supabase (RLS, user JWT)
                    ▼
                Supabase project fcbnscgsymzdykendbrc
                    Auth (magic link, Google) · Postgres · RLS
                    ▲
                    │ service-role key, from EC2 only
developer ──HTTPS──▶ Caddy ─▶ create_app (apps/infrx-api) ─▶ vLLM (Marlin) on the GPU box
                                 auth cache 60 s · usage_events writes (batched, async)
```

- The console never talks to the gateway; the gateway never talks to the
  console. Postgres is the contract.
- Prices live in `models` and are copied into each `usage_events` row as
  `cost_usd` at request time, so price changes never rewrite history.
- Environments: Vercel production = `app.callbill.ai`; previews per PR.
  Gateway production = `marlin2b.callbill.ai` (later `api.callbill.ai`).

## 6. Data model (Supabase migrations in `apps/app/supabase/migrations/`)

```sql
create table profiles (
  id uuid primary key references auth.users on delete cascade,
  email text not null, full_name text, avatar_url text,
  is_operator boolean not null default false,
  created_at timestamptz not null default now());

create table organizations (
  id uuid primary key default gen_random_uuid(),
  name text not null, slug text unique not null,
  created_by uuid references profiles(id),
  created_at timestamptz not null default now());

create table org_members (
  org_id uuid references organizations(id) on delete cascade,
  user_id uuid references profiles(id) on delete cascade,
  role text not null check (role in ('owner','member')),
  primary key (org_id, user_id));

create table models (
  id text primary key,                       -- 'nemostation/marlin-2b'
  name text not null, provider text not null, description text not null,
  status text not null check (status in ('live','coming_soon','retired')),
  base_url text not null,                    -- https://marlin2b.callbill.ai/v1
  served_model text not null,                -- id to send in the request body
  input_usd_per_m numeric(12,6) not null, output_usd_per_m numeric(12,6) not null,
  cache_usd_per_m numeric(12,6),
  context_tokens int not null, max_output_tokens int,
  input_modalities text[] not null, output_modalities text[] not null,
  limits jsonb not null default '{}',        -- {max_video_seconds:120, max_video_mb:64}
  snippets jsonb not null default '{}',      -- {curl:'...', python:'...', javascript:'...'} with {{KEY}} {{BASE_URL}}
  sort int not null default 100);

create table api_keys (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  created_by uuid references profiles(id),
  name text not null, prefix text not null, key_hash text unique not null,
  created_at timestamptz not null default now(),
  last_used_at timestamptz, revoked_at timestamptz);

create table usage_events (
  id uuid primary key,                        -- the gateway's Inference-Id
  org_id uuid not null references organizations(id) on delete cascade,
  api_key_id uuid references api_keys(id) on delete set null,
  model_id text not null references models(id),
  status int not null, stream boolean not null default false,
  prompt_tokens int, completion_tokens int, video_seconds numeric(10,3),
  ttft_ms int, latency_ms int, cached boolean not null default false,
  cost_usd numeric(14,8) not null default 0,
  created_at timestamptz not null default now());
create index on usage_events (org_id, created_at desc);

create table credit_ledger (
  id uuid primary key default gen_random_uuid(),
  org_id uuid not null references organizations(id) on delete cascade,
  delta_usd numeric(14,6) not null,
  kind text not null check (kind in ('grant','purchase','usage','adjustment')),
  reason text, ref text, created_by uuid references profiles(id),
  created_at timestamptz not null default now());
```

RLS: members read their org's rows; only owners create/revoke keys; `models`
readable by every signed-in user; `usage_events` and `credit_ledger` insert
only via service role; operators (`profiles.is_operator`) read everything.
A trigger on `auth.users` insert creates the profile, org and owner
membership. A SQL function `org_usage_summary(org_id, from, to, key_id)`
returns the tiles in one query.

## 7. Gateway changes (`apps/infrx-api/gateway.py`)

- Replace the single `GATEWAY_API_KEY` with Supabase lookup:
  `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` in `/etc/marlin2b-gateway.env`;
  `select id, org_id, revoked_at from api_keys where key_hash = sha256(key)`;
  in-memory cache 60 s; `last_used_at` updated at most once a minute per key.
- After each request, insert into `usage_events` (async, fire-and-forget with
  a bounded retry queue) with `cost_usd = prompt_tokens × input_usd_per_m /
  1e6 + completion_tokens × output_usd_per_m / 1e6` using the price cached
  from `models` (refreshed every 5 min).
- Keep the local `usage.jsonl` as the durable fallback; a small replay script
  re-inserts rows that failed.
- `/v1/models` continues to serve the OpenRouter provider document.

## 8. Console structure (`apps/app`)

```
app/
  (auth)/login/page.tsx            magic link + Google
  auth/callback/route.ts           Supabase code exchange
  (console)/layout.tsx             sidebar: Models, Usage, Dedicated, Billing, Teams, API Keys · Docs · Balance · user menu
  (console)/models/page.tsx        catalog cards + Copy & Run (client component, key selector, language tabs)
  (console)/usage/page.tsx         tiles + range + key filter + graph/table
  (console)/api-keys/page.tsx      list + create dialog (reveal once) + revoke
  (console)/billing/page.tsx       balance, ledger, invoices (empty), disabled actions
  (console)/teams/page.tsx, dedicated/page.tsx
  (console)/docs/page.tsx          MDX-free: TSX sections sharing the snippet component
  (console)/admin/page.tsx         operators only
lib/supabase/{client,server,middleware}.ts   @supabase/ssr pattern
lib/keys.ts                        generate + hash
supabase/migrations/*.sql, supabase/seed.sql (models rows)
```

Environment variables: `NEXT_PUBLIC_SUPABASE_URL`,
`NEXT_PUBLIC_SUPABASE_ANON_KEY` (publishable), `SUPABASE_SERVICE_ROLE_KEY`
(server only, admin actions), `NEXT_PUBLIC_APP_URL`.

## 9. Deployment

- Vercel project `infrx-app` in team CallSofia (`humanbit`), Git-linked to
  `callgideon/model-inference`, root directory `apps/app`, production branch
  `main`. Domain `app.callbill.ai` via a Route 53 CNAME to `cname.vercel-dns.com`.
- Supabase: run migrations with the CLI against project
  `fcbnscgsymzdykendbrc`; enable Google provider; set Site URL and redirect
  URLs to `https://app.callbill.ai/auth/callback` and the Vercel preview
  pattern.
- Gateway: `sudo ./apps/infrx-api/deploy/install.sh` after adding the
  Supabase variables to the env file it writes.

## 10. Delivery plan

1. Supabase schema + RLS + seed (Marlin live; DeepSeek-V4.1-Flash, Qwen3.8-27B,
   Kimi-K3 as coming soon with research prices as placeholders marked so).
2. Next.js app: auth, layout, all pages, admin.
3. Gateway: Supabase key validation + usage ingestion; deploy; verify a
   console-created key works end to end and shows in Usage.
4. Vercel project + domain; Supabase auth URLs; smoke test with a fresh account.

Blocked on inputs from the owner: Supabase anon (publishable) key and service
role key for `fcbnscgsymzdykendbrc` (the CLI login here belongs to another
account), and a Google OAuth client if Google sign-in is wanted at launch
(magic link works without it).

### Implementation-plan amendment log — 2026-09-20

Linked authoritative reviewed handoffs; no implementation or live-state change. Prior research remains historical context.
