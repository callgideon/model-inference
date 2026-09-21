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

-- =============================== protected columns on existing public tables ===
-- organizations: 0001 revoked insert and delete but left UPDATE, because an owner may
-- rename their workspace (`organizations_update_owner`). Suspension now lives on this
-- row, so the table-level privilege goes and exactly the two harmless columns come
-- back. Without this an owner clears their own suspension through the existing policy.
revoke update on public.organizations from anon, authenticated;
grant update (name, slug) on public.organizations to authenticated;

-- profiles: `is_operator` IS the platform operator role (06). 0001 already granted
-- only (full_name, avatar_url); this states the negative so a future widening of the
-- table grant cannot silently re-expose it.
revoke update (is_operator) on public.profiles from anon, authenticated;

-- Money and metering stay service-only. 0001 revoked these; repeated here because
-- this file is the one place the whole matrix can be read, and because the columns
-- 0003 added to both tables are protected by exactly these revokes.
revoke insert, update, delete on public.credit_ledger from anon, authenticated;
revoke insert, update, delete on public.usage_events from anon, authenticated;
revoke insert, update, delete on public.models from anon, authenticated;
revoke insert, update, delete on public.org_members from anon, authenticated;

-- api_keys keeps 0001's owner insert/update (creating and revoking a key is a
-- nonfinancial tenant action, and R33 keeps `keys.revoke` working while suspended).

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
