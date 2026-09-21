-- D1: the role / RLS / column-grant matrix and the mutation boundary
-- (06 §"Mutation boundaries"; 02 §"Promotional credit migration").
--
-- The rule this file exists for: "RLS alone is insufficient when broad existing
-- update grants cover new protected fields: explicitly restrict column grants and
-- expose narrowly authorized RPCs/services. Browser roles must not write balances,
-- holds, entitlements, suspension, job ownership, author provenance or platform
-- roles." Supabase grants anon/authenticated ALL PRIVILEGES on everything in
-- `public`, including columns added to a table later, so every protection here is
-- explicit rather than inherited.

-- ======================================================= schema reachability ===
-- Nothing browser-facing may even see the pilot schema. The revokes are redundant
-- with PostgreSQL's default (a new schema grants nothing to PUBLIC) and stay anyway:
-- a control that rests on nobody having granted something is not a control.
revoke all on schema infrx from public;
revoke usage on schema infrx from anon, authenticated;
revoke all on all tables in schema infrx from anon, authenticated;
revoke all on all functions in schema infrx from public, anon, authenticated;

grant usage on schema infrx to service_role;
grant select, insert, update, delete on all tables in schema infrx to service_role;
grant select on infrx.wallet_reconciliation to service_role;
grant execute on function infrx.now() to service_role;

-- Every object a later migration adds here starts with nothing.
--
-- r3 (N4): `alter default privileges IN SCHEMA … revoke execute on functions from public`
-- is a **no-op**. PostgreSQL's built-in default of `EXECUTE to PUBLIC` for a new function
-- is not a per-schema default ACL entry, so there is nothing in the schema for a
-- per-schema statement to remove: a function created in `infrx` afterwards still carried
-- `=X/postgres`. The form that works is the GLOBAL one, for the role that creates the
-- object, and it is written with dynamic SQL because the migration owner differs between
-- a local container (`postgres`, superuser) and the deployed project (`postgres`, NOT a
-- superuser - measured on supabase/postgres 17.6).
--
-- What it covers: every function created by THIS role, in every schema, from here on.
-- What it cannot cover: a function created by a different role (on the real image
-- `supabase_admin` owns default ACLs of its own), which is why the checks assert the
-- actual privileges of every function rather than trusting the default.
do $$
begin
  execute format('alter default privileges for role %I '
                 'revoke execute on functions from public', current_user);
  execute format('alter default privileges for role %I in schema public '
                 'revoke execute on functions from anon, authenticated', current_user);
  execute format('alter default privileges for role %I in schema infrx '
                 'revoke execute on functions from anon, authenticated', current_user);
end $$;

alter default privileges in schema infrx
  grant select, insert, update, delete on tables to service_role;
alter default privileges in schema infrx grant execute on functions to service_role;

-- And the functions that already exist. `revoke all … from public` on a function does NOT
-- remove Supabase's default-ACL grant to `anon`/`authenticated` (a separate grantee), so
-- both roles held EXECUTE on all six functions 0005 creates and on the three RPCs, on
-- both images. Each is revoked from PUBLIC, anon and authenticated, then granted back to
-- exactly the callers that need it. The `infrx` RPC surface is handled at the end of this
-- file; 0005 does its own functions after it creates them.
revoke all on function public.is_operator() from public, anon;
revoke all on function public.is_org_member(uuid) from public, anon;
revoke all on function public.is_org_owner(uuid) from public, anon;
revoke all on function public.org_usage_summary(uuid, timestamptz, timestamptz, uuid)
  from public, anon;
revoke all on function public.org_usage_daily(uuid, timestamptz, timestamptz, uuid)
  from public, anon;
revoke all on function public.org_balance(uuid) from public, anon;
-- Trigger functions: a trigger fires with the table owner's rights, so nothing needs to
-- call these directly.
revoke all on function public.set_updated_at() from public, anon, authenticated;
revoke all on function public.handle_new_user() from public, anon, authenticated;

-- r3 (review's D2-D6 note, done now because it is two grants): `infrx.wallets.ledger_total`
-- is moved by ONE writer, the AFTER INSERT trigger on `credit_ledger`, which is SECURITY
-- DEFINER and therefore unaffected by this. So the platform role loses the two privileges
-- that would let it drift the summary: UPDATE of `ledger_total` (a settlement that both
-- inserted a ledger row and set the total would double-move it) and DELETE of a wallet
-- (the trigger would recreate it with the next delta as its whole total). `reserved_total`,
-- `revision` and `updated_at` stay writable: D2 and D5 move the reservation.
revoke update, delete, insert on infrx.wallets from service_role;
grant insert (org_id, reserved_total) on infrx.wallets to service_role;
grant update (reserved_total, revision, updated_at) on infrx.wallets to service_role;

-- Row-level security on every tenant-bearing relation, with no policy for a browser
-- role. `service_role` is BYPASSRLS (as in production), so this sits behind the
-- schema grant as defence in depth: a future PostgREST exposure of `infrx` would
-- still deny anon/authenticated rather than leak every tenant's jobs.
alter table infrx.wallets                enable row level security;
alter table infrx.credit_holds           enable row level security;
alter table infrx.capacity_reservations  enable row level security;
alter table infrx.jobs                   enable row level security;
alter table infrx.attempts               enable row level security;
alter table infrx.staged_media           enable row level security;
alter table infrx.job_media              enable row level security;
alter table infrx.idempotency            enable row level security;
alter table infrx.stream_chunks          enable row level security;
alter table infrx.outbox                 enable row level security;
alter table infrx.feedback               enable row level security;
alter table infrx.consent_history        enable row level security;
alter table infrx.judge_budgets          enable row level security;
alter table infrx.judge_runs             enable row level security;
alter table infrx.judge_reservations     enable row level security;
alter table infrx.judge_samples          enable row level security;
alter table infrx.callback_destinations  enable row level security;
alter table infrx.callback_deliveries    enable row level security;
alter table infrx.org_entitlements       enable row level security;
alter table infrx.audit_entries          enable row level security;
alter table infrx.price_versions         enable row level security;

-- ============================= privileges on the existing public relations (r2) ===
-- Ruling 4, and B3: enumerating verbs (`revoke insert, update, delete`) left TRUNCATE
-- behind, which Supabase's default ALL grant hands to `anon` and `authenticated` —
-- `truncate public.credit_ledger cascade` erased the ledger as an anonymous browser
-- session, and neither RLS (which TRUNCATE ignores) nor the row-level append-only
-- trigger (which never fires) said a word. So: **revoke ALL, then grant back exactly
-- what the deployed console needs**, verb by verb and column by column. The list below
-- is the whole browser-reachable privilege surface of this database; the evidence
-- repeats it as a table.
--
-- `anon` gets nothing at all: every policy in 0001 is `to authenticated`, so anon
-- already read zero rows through RLS, and the grants only ever amounted to the
-- TRUNCATE hole. A future public (pre-login) page that needs the model catalogue must
-- add `grant select on public.models to anon` and say so.
revoke all on public.profiles, public.organizations, public.org_members,
              public.models, public.api_keys, public.usage_events,
              public.credit_ledger
  from anon, authenticated;

-- profiles: read yourself and anyone sharing an organization (0001's policy), and write
-- exactly your own display fields. `is_operator` IS the platform operator role (06).
grant select on public.profiles to authenticated;
grant update (full_name, avatar_url) on public.profiles to authenticated;

-- organizations: an owner renames their workspace and nothing else. Suspension lives
-- here now, so a table-level UPDATE would let an owner lift their own suspension.
grant select on public.organizations to authenticated;
grant update (name, slug) on public.organizations to authenticated;

-- org_members, models: read-only for a signed-in user.
grant select on public.org_members to authenticated;
grant select on public.models to authenticated;

-- api_keys (ruling 5 / B4): INSERT is column-scoped too. A table-level INSERT covers
-- every column, so an owner could create a key with `trace_mode = 'full'` — a consent
-- decision with no `consent_history` row — or backdate `created_at`, or choose `id`.
-- The five columns below are exactly what `app/(console)/api-keys/actions.ts` writes;
-- `id`, `created_at`, `last_used_at` and `trace_mode` take their defaults.
grant select on public.api_keys to authenticated;
grant insert (org_id, created_by, name, prefix, key_hash) on public.api_keys
  to authenticated;
grant update (name, revoked_at) on public.api_keys to authenticated;

-- usage_events, credit_ledger: read-only. Every write is the service role's, and
-- TRUNCATE is refused for every role by the statement trigger in 0003.
grant select on public.usage_events to authenticated;

-- credit_ledger: SELECT is **column-scoped** (r3, N2). `created_by` is the one column on
-- this table that can hold a platform-side identity - the deployed console's `addCredit`
-- writes the operator's user id into it - and 0001 gives every member SELECT on the
-- table, so a table-wide grant hands the operator's uuid to any member who asks for that
-- column. Removing the *column* is not an option: it is 0001's, the operator grant's only
-- link to who made it, and `public.console_ledger` resolves it per viewer through
-- `visible_principal`. So the column stays and the grant does not include it.
--
-- These seven are exactly what the console reads with a user session
-- (`apps/app/lib/credits.ts`: id, delta_usd, kind, reason, ref, created_at; `org_id` for
-- its own filter). `reason` is deliberately included: it is the customer-facing
-- description in the ledger DTO. Operator-internal prose lives in `infrx.audit_entries`,
-- which no browser role can read - R59-2 is about the operator's identity and internal
-- notes, not about the description the customer is meant to see.
grant select (id, org_id, delta_usd, kind, reason, ref, created_at)
  on public.credit_ledger to authenticated;

-- ========================================================= mutation boundary ===
-- 06: "Expose narrow service/RPC operations for admit, prepare, claim, heartbeat,
-- append, terminalize, cancel, grant, accept_feedback, reserve_judge and
-- record_submission. A transaction may call internal helpers but no public caller can
-- independently manipulate holds or terminal rows."
--
-- D1 ships the surface: eleven SECURITY DEFINER functions with a fixed search_path,
-- EXECUTE revoked from PUBLIC/anon/authenticated and granted to `service_role` only.
-- Each body raises `feature_not_supported` naming the task that owns it, so the
-- boundary fails closed until that task fills it in:
--
--   admit                          D2      cancel             D3
--   prepare                        D2      terminalize        D5
--   claim                          D3      grant_credit       D5
--   heartbeat                      D3      accept_feedback    D6
--   append                         D4      reserve_judge      D6
--                                          record_submission  D6
--
-- The argument is one canonical-JSON object (`contracts/codec.compact_bytes`) and the
-- result is one too, so a body can be filled in without changing the signature the
-- grants are attached to. `grant` is spelled `grant_credit` because `grant` is a
-- reserved word and a quoted function name is one someone will forget to quote.
create or replace function infrx.unimplemented(p_name text, p_owner text) returns void
language plpgsql as $$
begin
  raise exception 'infrx.%() has no body yet; task % implements it', p_name, p_owner
    using errcode = '0A000';
end $$;

do $$
declare
  r record;
begin
  for r in select * from (values
      ('admit', 'D2'), ('prepare', 'D2'), ('claim', 'D3'), ('heartbeat', 'D3'),
      ('append', 'D4'), ('cancel', 'D3'), ('terminalize', 'D5'), ('grant_credit', 'D5'),
      ('accept_feedback', 'D6'), ('reserve_judge', 'D6'), ('record_submission', 'D6')
    ) as t(name, owner)
  loop
    execute format($fn$
      create or replace function infrx.%I(p_args jsonb) returns jsonb
      language plpgsql security definer set search_path = infrx, public, pg_temp as $body$
      begin
        perform infrx.unimplemented(%L, %L);
        return null;
      end $body$;
      revoke all on function infrx.%I(jsonb) from public, anon, authenticated;
      grant execute on function infrx.%I(jsonb) to service_role;
      comment on function infrx.%I(jsonb) is
        'Mutation boundary (06). Signature and grants owned by D1; body owned by %s.';
    $fn$, r.name, r.name, r.owner, r.name, r.name, r.name, r.owner);
  end loop;
end $$;

-- How the Python adapter must connect (measured, not assumed - see D1's evidence):
-- the pilot relations have RLS enabled with no policy, and BYPASSRLS is a role
-- ATTRIBUTE that is not inherited through membership. A login role created `inherit in
-- role service_role` therefore reads zero rows and cannot insert. The adapter's pool
-- must run `set role service_role` on each connection (psycopg's pool `configure`
-- hook), exactly as PostgREST does, or hold BYPASSRLS itself. D1 creates no login role:
-- that needs a password, which does not belong in a migration.

-- The internal helpers a body will call are not part of the boundary.
revoke all on function infrx.unimplemented(text, text) from public, anon, authenticated;
revoke all on function infrx.forbid_update_delete() from public, anon, authenticated;
revoke all on function infrx.jobs_guard() from public, anon, authenticated;
revoke all on function infrx.consent_guard() from public, anon, authenticated;
revoke all on function infrx.staged_media_guard() from public, anon, authenticated;
revoke all on function infrx.ensure_wallet() from public, anon, authenticated;
revoke all on function infrx.now() from public, anon, authenticated;
grant execute on function infrx.now() to service_role;
-- The one `infrx` helper a platform client is meant to call: a re-run of
-- `0002_seed_models.sql` wipes the pilot keys out of `models.limits`, and this
-- puts them back (see 0003).
grant execute on function infrx.extend_model_limits() to service_role;
