-- D1R: the operator/headless seams the G6B handback asked of D (coordinator ruling
-- 2026-09-22, folded into D1R). Additive and re-runnable, like 0006-0008.
--
--   * AuditAction: the closed 0003 list is EXTENDED (its four values kept) with the six
--     headless operator actions; `idempotency_key` becomes unique where present, and
--     `infrx.audit_by_idempotency_key` answers a replay.
--   * `public.api_keys` gains the credential audience (07: consumer | provider_dev |
--     operator), the individual behind a consumer key, and the provider/endpoint scope of
--     a provider_dev key; `key_by_hash` and a one-way `revoke_key` for the gateway/G6B.
--   * one audited bootstrap for the single operator-audience key (a hash, never a
--     plaintext), `verified_user`, an audited suspension operation, and bounded
--     `usage_records` / `active_holds` reads in UsageRecordV2 shape.
--
-- Everything here is service_role only; browser roles gain nothing. The deployed
-- console's key creation (org_id, created_by, name, prefix, key_hash) keeps working:
-- the audience defaults to consumer and the individual is its `created_by`.
--
-- D2 amendment 2026-09-22 (in place: 0006-0009 are applied to no hosted project; D1R
-- review (e), R84): a comment only, on the `usage_records` keyset ("both or neither").
-- No object changes.

-- ================================================================ AuditAction ===
do $$
begin
  if exists (select 1 from pg_constraint where conrelid = 'infrx.audit_entries'::regclass
             and conname = 'audit_entries_action_check'
             and pg_get_constraintdef(oid) not like '%admin_adjust%') then
    alter table infrx.audit_entries drop constraint audit_entries_action_check;
    alter table infrx.audit_entries add constraint audit_entries_action_check
      check (action in ('admin_grant', 'admin_set_suspension', 'admin_set_entitlements',
                        'calibration_label', 'admin_key_issue', 'admin_key_revoke',
                        'admin_publish', 'admin_job_cancel', 'admin_reconcile',
                        'admin_adjust'));
  end if;
end $$;
create unique index if not exists audit_entries_idempotency_key_idx
  on infrx.audit_entries (idempotency_key) where idempotency_key is not null;

create or replace function infrx.audit_by_idempotency_key(p_key text)
returns setof infrx.audit_entries
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select * from infrx.audit_entries where idempotency_key = p_key;
$$;

-- ================================================================ API keys ===
alter table public.api_keys
  add column if not exists audience text not null default 'consumer',
  add column if not exists user_id uuid,
  add column if not exists provider_org_id uuid,
  add column if not exists endpoint_id uuid;
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'endpoints_owner_pair_key') then
    alter table infrx.endpoints add constraint endpoints_owner_pair_key
      unique (endpoint_id, provider_org_id);
  end if;
  if not exists (select 1 from pg_constraint where conrelid = 'public.api_keys'::regclass
                 and conname = 'api_keys_audience_check') then
    alter table public.api_keys
      add constraint api_keys_audience_check
        check (audience in ('consumer', 'provider_dev', 'operator')),
      add constraint api_keys_user_fk foreign key (user_id)
        references public.profiles(id) on delete restrict,
      add constraint api_keys_endpoint_fk foreign key (endpoint_id, provider_org_id)
        references infrx.endpoints (endpoint_id, provider_org_id) on delete restrict,
      -- The audience carries its own identity (AuthContextV2): a provider_dev key is
      -- scoped to one provider and one endpoint, the others to neither. A consumer key's
      -- individual is required for NEW keys by the insert guard below; legacy keys predate
      -- `user_id` and are neither rewritten nor made un-renamable.
      add constraint api_keys_audience_identity check (case audience
        when 'provider_dev' then provider_org_id is not null and endpoint_id is not null
                                 and user_id is null
        else provider_org_id is null and endpoint_id is null end);
  end if;
end $$;
-- ONE active operator key.
create unique index if not exists api_keys_one_active_operator
  on public.api_keys ((true)) where audience = 'operator' and revoked_at is null;

-- The consumer key's individual is the member who created it (the console writes
-- `created_by = auth.uid()`, enforced by 0001's insert policy); a revocation is one-way.
create or replace function infrx.api_keys_identity_guard() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  if tg_op = 'INSERT' then
    if new.audience = 'consumer' and new.user_id is null then
      new.user_id := new.created_by;
    end if;
    if new.audience = 'consumer' and new.user_id is null then
      raise exception 'a consumer key names the individual it belongs to'
        using errcode = '23514';
    end if;
    return new;
  end if;
  if new.audience is distinct from old.audience or new.user_id is distinct from old.user_id
     or new.provider_org_id is distinct from old.provider_org_id
     or new.endpoint_id is distinct from old.endpoint_id
     or (old.revoked_at is not null and new.revoked_at is distinct from old.revoked_at) then
    raise exception 'api key %: audience and scope are immutable; revocation is one-way',
      old.id using errcode = '23514';
  end if;
  return new;
end $$;
create or replace trigger api_keys_identity_guard before insert or update on public.api_keys
  for each row execute function infrx.api_keys_identity_guard();

create or replace function infrx.key_by_hash(p_key_hash text)
returns table (id uuid, org_id uuid, audience text, user_id uuid, provider_org_id uuid,
               endpoint_id uuid, trace_mode text, revoked_at timestamptz,
               org_suspended boolean)
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select k.id, k.org_id, k.audience, k.user_id, k.provider_org_id, k.endpoint_id,
         k.trace_mode, k.revoked_at, o.suspended
  from public.api_keys k join public.organizations o on o.id = k.org_id
  where k.key_hash = p_key_hash;
$$;

create or replace function infrx.revoke_key(p_key_id uuid, p_actor text, p_reason text,
                                            p_idempotency_key text)
returns timestamptz
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_at timestamptz;
begin
  update public.api_keys set revoked_at = infrx.now()
   where id = p_key_id and revoked_at is null returning revoked_at into v_at;
  if v_at is null then
    select revoked_at into v_at from public.api_keys where id = p_key_id;
    if v_at is null then
      raise exception 'not_found: key' using errcode = 'P0002';
    end if;
    return v_at;                                  -- already revoked: the same answer
  end if;
  insert into infrx.audit_entries (id, actor_principal, action, target_org_id, reason,
                                   after, idempotency_key)
  select gen_random_uuid(), p_actor, 'admin_key_revoke', k.org_id, p_reason,
         jsonb_build_object('key_id', k.id, 'revoked_at', v_at), p_idempotency_key
  from public.api_keys k where k.id = p_key_id;
  return v_at;
end $$;

-- The single operator-audience key, bootstrapped by a documented operator step with the
-- key's sha256 only; audited; a replay with the same hash returns the same key.
create or replace function infrx.bootstrap_operator_key(p_org uuid, p_name text,
  p_prefix text, p_key_hash text, p_actor text, p_reason text)
returns uuid
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_id uuid;
begin
  if p_key_hash !~ '^[0-9a-f]{64}$' then
    raise exception 'invalid_request: a sha256 hex digest, never a key' using errcode = '22023';
  end if;
  select k.id into v_id from public.api_keys k
   where k.key_hash = p_key_hash and k.audience = 'operator';
  if v_id is not null then
    return v_id;
  end if;
  insert into public.api_keys (org_id, name, prefix, key_hash, audience)
  values (p_org, p_name, p_prefix, p_key_hash, 'operator') returning id into v_id;
  insert into infrx.audit_entries (id, actor_principal, action, target_org_id, reason, after)
  values (gen_random_uuid(), p_actor, 'admin_key_issue', p_org, p_reason,
          jsonb_build_object('key_id', v_id, 'audience', 'operator', 'prefix', p_prefix));
  return v_id;
end $$;

-- ============================================================ verified user (A1) ===
-- Supabase's own confirmation timestamp is the evidence; NULL evidence = not verified.
create or replace function infrx.verified_user(p_user uuid)
returns table (user_id uuid, personal_org_id uuid, verification_evidence_ref text)
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select u.id,
         (select o.id from public.organizations o
            join public.org_members m on m.org_id = o.id and m.user_id = u.id
                                      and m.role = 'owner'
           where o.created_by = u.id order by o.created_at, o.id limit 1),
         case when to_jsonb(u)->>'email_confirmed_at' is not null
              then 'email_confirmed_at/' || (to_jsonb(u)->>'email_confirmed_at') end
  from auth.users u where u.id = p_user;
$$;

-- ================================================================ suspension ===
-- 0003's columns, set through one audited operation (R33, R34).
create or replace function infrx.set_suspension(p_org uuid, p_suspended boolean,
  p_reason_code text, p_actor text, p_reason text, p_idempotency_key text)
returns boolean
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_before jsonb;
begin
  if exists (select 1 from infrx.audit_entries where idempotency_key = p_idempotency_key) then
    return (select suspended from public.organizations where id = p_org);
  end if;
  select jsonb_build_object('suspended', suspended, 'reason', suspension_reason)
    into v_before from public.organizations where id = p_org for update;
  if v_before is null then
    raise exception 'not_found: organization' using errcode = 'P0002';
  end if;
  update public.organizations
     set suspended = p_suspended,
         suspended_at = case when p_suspended then infrx.now() end,
         suspension_reason = case when p_suspended then p_reason_code end
   where id = p_org;
  insert into infrx.audit_entries (id, actor_principal, action, target_org_id, reason,
                                   before, after, idempotency_key)
  values (gen_random_uuid(), p_actor, 'admin_set_suspension', p_org, p_reason, v_before,
          jsonb_build_object('suspended', p_suspended, 'reason', p_reason_code),
          p_idempotency_key);
  return p_suspended;
end $$;

-- ====================================================== usage and holds (G6B) ===
-- UsageRecordV2-shaped rows, newest first, bounded; the unit follows the regime and the
-- amount is text. Keyset: pass the last row's (settled_at, request_id) - BOTH or NEITHER
-- (D1R review (e)): with only one of the two the row comparison is NULL and the page is
-- silently empty.
create or replace function infrx.usage_records(p_org uuid, p_before timestamptz default null,
  p_before_id uuid default null, p_limit int default 100)
returns table (request_id uuid, org_id uuid, accounting_regime text, unit text,
               charged_amount text, prompt_tokens int, completion_tokens int,
               usage_certainty text, outcome text, rate_card_version text,
               serving_version_id uuid, deployment_revision_id uuid, price_version text,
               settled_at timestamptz)
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select e.id, e.org_id, e.accounting_regime,
         case e.accounting_regime when 'credit' then 'CREDIT' else 'USD' end,
         case e.accounting_regime when 'credit' then e.charged_credits::text
              else e.cost_usd::text end,
         e.prompt_tokens, e.completion_tokens, e.usage_certainty, e.settlement_state,
         e.rate_card_version, e.serving_version_id, e.deployment_revision_id,
         e.price_version, e.created_at
  from public.usage_events e
  where e.org_id = p_org
    and (p_before is null or (e.created_at, e.id) < (p_before, p_before_id))
  order by e.created_at desc, e.id desc
  limit least(greatest(coalesce(p_limit, 100), 1), 500);
$$;

create or replace function infrx.active_holds(p_org uuid)
returns table (request_id uuid, org_id uuid, accounting_regime text, unit text,
               amount text, state text, reconcile_after timestamptz)
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select h.request_id, h.org_id, 'legacy_usd', 'USD', h.amount::text, h.state,
         h.reconcile_after
  from infrx.credit_holds h where h.org_id = p_org and h.state in ('held', 'unknown')
  union all
  select h.request_id, h.org_id, 'credit', 'CREDIT', h.amount::text, h.state,
         h.reconcile_after
  from infrx.credit_wallet_holds h where h.org_id = p_org and h.state in ('held', 'unknown')
  order by 1
  limit 1000;
$$;

-- ================================================================ privileges ===
do $$
declare
  f text;
begin
  foreach f in array array[
      'infrx.audit_by_idempotency_key(text)', 'infrx.key_by_hash(text)',
      'infrx.revoke_key(uuid,text,text,text)',
      'infrx.bootstrap_operator_key(uuid,text,text,text,text,text)',
      'infrx.verified_user(uuid)',
      'infrx.set_suspension(uuid,boolean,text,text,text,text)',
      'infrx.usage_records(uuid,timestamptz,uuid,integer)', 'infrx.active_holds(uuid)']
  loop
    execute format('revoke all on function %s from public, anon, authenticated', f);
    execute format('grant execute on function %s to service_role', f);
  end loop;
end $$;
revoke all on function infrx.api_keys_identity_guard() from public, anon, authenticated;
