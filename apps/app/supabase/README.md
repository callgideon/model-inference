# Supabase (project `fcbnscgsymzdykendbrc`)

Schema for the console: `migrations/0001_init.sql` (tables, RLS, signup
trigger, reporting functions) and `migrations/0002_seed_models.sql` (model
catalog; re-runnable, upserts by id). Migrations `0003`–`0005` add the original
USD pilot durable schema and RPCs. They are committed and tested in the wave-2
record; this audit has not applied them to any hosted project. The two-platform
CREDIT/user-wallet schema requires new additive migrations under D1R. Never edit
0001–0005 to erase their history.

## Apply

```bash
cd apps/app
supabase link --project-ref fcbnscgsymzdykendbrc   # asks for the db password
supabase db push
```

`db push` applies every file in `migrations/` in filename order and records
them, so it is safe to re-run. To reseed the catalog after editing prices
without a new migration file:

```bash
psql "$SUPABASE_DB_URL" -f supabase/migrations/0002_seed_models.sql
# After 0003 is installed, extend the new models' limit rows as well:
psql "$SUPABASE_DB_URL" -c "select infrx.extend_model_limits();"
```

## Auth setup (dashboard → Authentication)

- **URL Configuration** → Site URL `https://app.callbill.ai`; Redirect URLs:
  `https://app.callbill.ai/auth/callback`, `http://localhost:3000/auth/callback`
  and the preview pattern `https://*-humanbit.vercel.app/auth/callback`.
- **Sign In / Providers → Email**: magic link is on by default; no extra config.
- **Sign In / Providers → Google**: paste the Google OAuth client ID and secret,
  and add Supabase's callback
  `https://fcbnscgsymzdykendbrc.supabase.co/auth/v1/callback`
  to the client's authorised redirect URIs in Google Cloud Console.

Every new user gets a `profiles` row, a personal `organizations` row (name =
email local part, slug = 12 random hex chars) and an `owner` membership, from
the `on_auth_user_created` trigger on `auth.users`.

## Make someone an operator

Operators see every org's usage, keys and ledger (`/admin`). The flag cannot be
set from the console — `authenticated` has no update grant on that column — so
run it in the SQL editor:

```sql
update profiles set is_operator = true where email = 'sofia@callbill.co';
```

## Reporting functions

All three guard on `is_org_member(p_org) or is_operator()` and are granted to
`authenticated`, so the console calls them with the user's JWT:

```sql
select * from org_usage_summary('<org>', now() - interval '24 hours', now());
select * from org_usage_daily('<org>', now() - interval '30 days', now(), '<key id>');
select org_balance('<org>');   -- legacy USD sum; NOT the console's balance source
```

The console reads its balance from `org_wallet_summary` (0005) only; there is no
`org_balance` fallback any more (it was deleted in the console, S1-fix B1). The
comments in `0005_console_read_surface.sql` that still mention that fallback are
historical and, like every file 0001–0005, are not edited. The CREDIT balance is
`console_wallet_summary(user)` (0008); legacy USD is `console_legacy_usd_statement(org)`.

`org_usage_summary` returns one row: requests, error_requests, avg_rps,
ttft_p50_ms, tok_s_p50, latency_p50_ms, cache_hit_ratio, prompt_tokens,
completion_tokens, cost_usd. The fourth argument (api key id) is optional on
both usage functions; days are bucketed in UTC.

## Who writes what

| table | authenticated | service role (gateway, admin actions) |
|---|---|---|
| `profiles` | update own `full_name`/`avatar_url` | everything |
| `organizations` | owner renames own org | everything |
| `org_members`, `models` | read only | everything |
| `api_keys` | owner inserts and revokes own org's | everything |
| `usage_events`, `credit_ledger` | read own org only | insert |

## Applying from a machine without IPv6

`db.<ref>.supabase.co` resolves to IPv6 only. Use the session pooler
(IPv4) instead; this project is in **us-east-2**:

```bash
supabase db push --yes --db-url \
  "postgresql://postgres.fcbnscgsymzdykendbrc:<url-encoded password>@aws-0-us-east-2.pooler.supabase.com:5432/postgres"
```

The password is in AWS SSM as `/INFRX-SUPABASE-PROD/db_password`; the
publishable and secret API keys are `/INFRX-SUPABASE-PROD/publishable_key`
and `/INFRX-SUPABASE-PROD/secret_key`. Applied 2026-09-20 (0001, 0002).

## Durable pilot exposure rules

Keep PostgREST `db-schemas` set to `public`; never expose `infrx` directly.
Every future migration adding a public table or function must revoke inherited
PUBLIC/anon/authenticated privileges first, then grant only the intended verbs or
RPC entry points. Supabase default ACLs are broader than a plain PostgreSQL instance;
verify both grants and RLS using real anon, authenticated, operator and service roles.

The historical role table above describes 0001–0002. For 0003–0005 the authority is
`0004_pilot_roles_and_rpcs.sql` and `0005_console_read_surface.sql` (exact names in migrations/),
including restricted financial RPCs. App and Lab share this one migration history.

## CREDIT and the provider registry (D1R: 0006–0008)

`0006_credit_accounting.sql` (CREDIT wallets, ledger, holds, the individual signup
entitlement, the accounting regime on jobs and usage, feature flags, the grant
function), `0007_provider_registry.sql` (provider orgs/roles, model/serving versions,
endpoints, deployment revisions, rate cards, listings) and
`0008_credit_read_surface.sql` (admission-pin resolution, console CREDIT views/RPCs)
are additive and re-runnable. Historical USD is never copied, converted or relabelled.

Applying them enables nothing: `infrx.feature_flags` starts with `signup_grant` and
`credit_admission` **off** (those paths refuse with SQLSTATE 55000, maintenance) and
`legacy_usd_admission` on. Enabling is a separate, attributed operator action:

```sql
update infrx.feature_flags set enabled = true, updated_by = '<operator>', reason = '<why>'
 where name = 'credit_admission';
```

The Marlin registry rows and a **provisional** rate card (P-01 pending) are an operator
seed, not a migration: `apps/infrx-api/infrx/state/seed_marlin_provisional.sql`
(`psql "$SUPABASE_DB_URL" -v ON_ERROR_STOP=1 -f …`; idempotent).

Legacy-account transition is a rollout input, not a migration decision (02 §"Existing
USD records"; P-02 is the read-only inventory of real accounts and balances): an
organization with a nonzero legacy USD balance is reported with `rollout_hold = true`
by `console_legacy_usd_statement`; an existing organization with several members gets no
individual wallet binding until its billing owner is resolved (the grant refuses it as a
rollout hold); no exchange rate exists anywhere in the schema.

Hosted state: only 0001–0002 are applied to the hosted project. 0003–0008 are not;
the coordinator applies them only after the backup/restore rehearsal (I3B).
