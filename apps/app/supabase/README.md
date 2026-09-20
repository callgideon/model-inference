# Supabase (project `fcbnscgsymzdykendbrc`)

Schema for the console: `migrations/0001_init.sql` (tables, RLS, signup
trigger, reporting functions) and `migrations/0002_seed_models.sql` (model
catalog; re-runnable, upserts by id).

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
select org_balance('<org>');
```

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
