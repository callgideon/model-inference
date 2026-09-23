-- A1: verified individual signup eligibility, the frozen personal-org binding, and the
-- retention policy for an individual who owns a wallet (research/plan/09 §A1;
-- platforms/02-credits; rulings R59, R64-R66, R71, R72).
--
-- Additive and re-runnable, like 0006-0009. Nothing here mints on application: the grant
-- stays behind 0006's `signup_grant` flag, and existing users receive it only through the
-- same operation a first login calls (the backfill is that call, per user).
--
--   * `public.claim_signup_grant(user, campaign, op)` - THE eligibility operation. Auth
--     callback retry, first login and existing-user backfill all call it. It derives the
--     verification evidence itself (GoTrue's `email_confirmed_at`, through 0009's
--     `infrx.verified_user`); a caller cannot hand one in. Denials are answers, not
--     exceptions, so the reason is recorded (`infrx.signup_denials`) without a second
--     round trip and without telling anyone else whether the account exists.
--   * `infrx.signup_identity_claims` - one grant per verified email, not only per UUID:
--     deleting and re-creating an account with the same address is not a new individual.
--   * the personal organization that funds a consumer wallet has a frozen membership
--     (its owner, alone): nobody is added to or removed from a bound personal org.
--   * `infrx.retire_individual` - the deletion policy. A wallet owner is never hard-
--     deleted (money history is `on delete restrict`); they are retired: profile
--     anonymised, keys revoked, personal org suspended, wallet frozen (no new hold, no
--     signup grant), ledger and entitlement kept.
--
-- Service role only. No browser role gains anything (R59-4); PostgREST must keep
-- exposing `public` only, and `claim_signup_grant` is not executable by anon or
-- authenticated - the console calls it server-side after reading the session user.

-- ======================================================== identity claims ===
-- The digest is sha256(lower(btrim(verified email))). Claimed in the grant's own
-- transaction, so a refused grant claims nothing.
-- ponytail: exact-address match; provider-specific folding (dots, +tags) is an abuse
-- control to add here if signup abuse is observed.
create table if not exists infrx.signup_identity_claims (
  identity_digest text primary key check (identity_digest ~ '^[0-9a-f]{64}$'),
  user_id uuid not null unique references public.profiles(id) on delete restrict,
  claimed_at timestamptz not null default infrx.now()
);
create or replace trigger signup_identity_claims_immutable before update or delete
  on infrx.signup_identity_claims for each row execute function infrx.forbid_update_delete();

-- ========================================================= denied / held ===
-- Why a verified-or-not individual did not receive the grant. Operator-readable only.
create table if not exists infrx.signup_denials (
  user_id uuid not null references public.profiles(id) on delete restrict,
  reason text not null
    check (reason in ('unverified', 'identity_reused', 'rollout_hold', 'retired')),
  attempts bigint not null default 1 check (attempts >= 1),
  first_at timestamptz not null default infrx.now(),
  last_at timestamptz not null default infrx.now(),
  primary key (user_id, reason)
);

-- ==================================================== retired individuals ===
create table if not exists infrx.retired_individuals (
  user_id uuid primary key references public.profiles(id) on delete restrict,
  actor text not null check (length(btrim(actor)) between 1 and 200),
  reason text not null check (length(btrim(reason)) between 1 and 500),
  retired_at timestamptz not null default infrx.now()
);
create or replace trigger retired_individuals_immutable before update or delete
  on infrx.retired_individuals for each row execute function infrx.forbid_update_delete();

-- ================================================= frozen personal-org binding ===
-- 02: "Do not let multiple memberships implicitly spend or transfer an individual's
-- promotional balance." Once a personal org funds a consumer wallet its membership is
-- frozen - no member added, the owner neither removed nor demoted. Browser roles cannot
-- write memberships at all (0001/0004); this holds for the platform role too.
create or replace function infrx.personal_org_binding_guard() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  if exists (select 1 from infrx.credit_wallets w
             where w.kind = 'consumer'
               and w.personal_org_id = any (array[old.org_id, new.org_id])) then
    raise exception 'organization %: the membership of a personal organization that funds '
      'a wallet is frozen', coalesce(new.org_id, old.org_id) using errcode = '23514';
  end if;
  if tg_op = 'DELETE' then
    return old;
  end if;
  return new;
end $$;
create or replace trigger org_members_personal_binding before insert or update or delete
  on public.org_members for each row execute function infrx.personal_org_binding_guard();

-- ============================================================ frozen wallet ===
-- A retired individual's wallet reserves nothing and receives no signup grant. Debits of
-- holds admitted before retirement and D5 compensating entries still land, so in-flight
-- work settles and corrections stay possible.
create or replace function infrx.retired_wallet_guard() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  if exists (select 1 from infrx.credit_wallets w
             join infrx.retired_individuals r on r.user_id = w.owner_user_id
             where w.wallet_id = new.wallet_id) then
    raise exception 'CREDIT wallet % is frozen: its owner is retired', new.wallet_id
      using errcode = '23514';
  end if;
  return new;
end $$;
create or replace trigger credit_wallet_holds_frozen before insert
  on infrx.credit_wallet_holds for each row execute function infrx.retired_wallet_guard();
create or replace trigger credit_ledger_signup_frozen before insert on infrx.credit_ledger
  for each row when (new.kind = 'signup_grant')
  execute function infrx.retired_wallet_guard();

-- ================================================== the eligibility operation ===
create or replace function infrx.record_signup_denial(p_user uuid, p_reason text)
returns void
language sql security definer set search_path = infrx, public, pg_temp as $$
  insert into infrx.signup_denials (user_id, reason)
  select p_user, p_reason where exists (select 1 from public.profiles p where p.id = p_user)
  on conflict (user_id, reason)
  do update set attempts = signup_denials.attempts + 1, last_at = infrx.now();
$$;

-- R72 on the USD side alone: is this organization's legacy USD balance nonzero? A boolean,
-- never an amount, so no function reads both units (R64/R73; checks_credit's unit scan).
create or replace function infrx.legacy_usd_rollout_hold(p_org uuid) returns boolean
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(sum(l.delta_usd), 0) <> 0 from public.credit_ledger l where l.org_id = p_org;
$$;

-- status: granted | replayed | unverified | identity_reused | rollout_hold | retired.
-- The grant columns are set for granted/replayed only. An unknown user answers
-- `unverified`, exactly like a known unverified one (no enumeration).
create or replace function public.claim_signup_grant(
  p_user_id uuid, p_campaign_version text default '', p_operation_id uuid default null)
returns table (status text, user_id uuid, wallet_id uuid, ledger_operation_id uuid,
               amount text, granted_at timestamptz)
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
#variable_conflict use_column
declare
  v_evidence text;
  v_email text;
  v_digest text;
  v_claimant uuid;
  v_status text;
begin
  perform infrx.require_feature('signup_grant');
  if p_user_id is null then
    raise exception 'invalid_request: a user is required' using errcode = '22023';
  end if;
  if exists (select 1 from infrx.retired_individuals r where r.user_id = p_user_id) then
    perform infrx.record_signup_denial(p_user_id, 'retired');
    return query select 'retired'::text, p_user_id, null::uuid, null::uuid, null::text,
                        null::timestamptz;
    return;
  end if;
  -- R71: the key is the identity. An existing grant answers every later call, whatever
  -- the campaign, organization, operation id or current verification state.
  if exists (select 1 from infrx.signup_entitlements e
             where e.user_id = p_user_id and e.entitlement = 'initial_signup_grant') then
    return query select 'replayed'::text, e.user_id, e.wallet_id, e.ledger_operation_id,
                        e.amount::text, e.granted_at
                 from infrx.signup_entitlements e
                 where e.user_id = p_user_id and e.entitlement = 'initial_signup_grant';
    return;
  end if;

  select v.verification_evidence_ref into v_evidence from infrx.verified_user(p_user_id) v;
  select to_jsonb(u)->>'email' into v_email from auth.users u
   where u.id = p_user_id and to_jsonb(u)->>'deleted_at' is null;
  if v_evidence is null or v_email is null or length(btrim(v_email)) = 0 then
    perform infrx.record_signup_denial(p_user_id, 'unverified');
    return query select 'unverified'::text, p_user_id, null::uuid, null::uuid, null::text,
                        null::timestamptz;
    return;
  end if;

  -- R72: a nonzero legacy USD balance is a rollout hold - never converted, never dropped.
  -- Scope: EVERY organization this individual created (the personal one and any later
  -- one), not only the one the wallet would bind; a shared org's billing owner is 02's
  -- transition. Fail closed: the account waits for the P-02 runbook either way.
  if exists (select 1 from public.organizations o
             where o.created_by = p_user_id and infrx.legacy_usd_rollout_hold(o.id)) then
    v_status := 'rollout_hold';
  else
    v_digest := encode(sha256(convert_to(lower(btrim(v_email)), 'UTF8')), 'hex');
    begin
      -- No conflict target: a racing retry for the same individual can collide on the
      -- digest OR the per-user key first, and either means "already claimed".
      insert into infrx.signup_identity_claims (identity_digest, user_id)
      values (v_digest, p_user_id) on conflict do nothing;
      select c.user_id into v_claimant from infrx.signup_identity_claims c
       where c.identity_digest = v_digest;
      if v_claimant is distinct from p_user_id then
        v_status := 'identity_reused';
      else
        return query select case when g.replayed then 'replayed' else 'granted' end,
                            g.user_id, g.wallet_id, g.ledger_operation_id, g.amount,
                            g.granted_at
                     from infrx.grant_signup_credit(p_user_id, v_evidence,
                                                    coalesce(p_campaign_version, ''),
                                                    p_operation_id) g;
        return;
      end if;
    exception when sqlstate '55000' then
      -- 0006's shared-organization refusal; the claim above is rolled back with it.
      if sqlerrm not like 'rollout_hold%' then
        raise;
      end if;
      v_status := 'rollout_hold';
    end;
  end if;
  perform infrx.record_signup_denial(p_user_id, v_status);
  return query select v_status, p_user_id, null::uuid, null::uuid, null::text,
                      null::timestamptz;
end $$;

-- ============================================================ retention (A1) ===
-- Ruling proposal (A1 evidence): an individual who owns a CREDIT wallet is retired, never
-- deleted. The hosted deletion is GoTrue's soft delete (`auth.users` row kept, address
-- obfuscated) followed by this call; a hard delete stays refused by the foreign keys.
-- Idempotent: a second call returns the first retirement time and changes nothing.
create or replace function infrx.retire_individual(p_user uuid, p_actor text,
  p_reason text, p_idempotency_key text)
returns timestamptz
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_at timestamptz;
  v_org uuid;
begin
  -- The profile row lock serialises concurrent retirements of one individual.
  perform 1 from public.profiles p where p.id = p_user for update;
  if not found then
    raise exception 'not_found: individual' using errcode = 'P0002';
  end if;
  select r.retired_at into v_at from infrx.retired_individuals r where r.user_id = p_user;
  if v_at is not null then
    return v_at;
  end if;
  insert into infrx.retired_individuals (user_id, actor, reason)
  values (p_user, p_actor, p_reason) returning retired_at into v_at;
  update public.profiles
     set email = 'retired+' || p_user || '@invalid', full_name = null, avatar_url = null
   where id = p_user;
  -- Legacy keys predate `api_keys.user_id`; their individual is `created_by`.
  update public.api_keys set revoked_at = infrx.now()
   where coalesce(user_id, created_by) = p_user and revoked_at is null;
  -- The personal organizations this individual alone owns: suspended, audited.
  for v_org in
    select o.id from public.organizations o
      join public.org_members m on m.org_id = o.id and m.user_id = p_user and m.role = 'owner'
     where o.created_by = p_user
       and not exists (select 1 from public.org_members x
                       where x.org_id = o.id and x.user_id <> p_user)
  loop
    perform infrx.set_suspension(v_org, true, 'operator_request', p_actor, p_reason,
                                 p_idempotency_key || ':' || v_org);
  end loop;
  return v_at;
end $$;

-- ================================================================ privileges ===
alter table infrx.signup_identity_claims enable row level security;
alter table infrx.signup_denials enable row level security;
alter table infrx.retired_individuals enable row level security;
revoke all on infrx.signup_identity_claims, infrx.signup_denials, infrx.retired_individuals
  from public, anon, authenticated, service_role;
grant select on infrx.signup_identity_claims, infrx.signup_denials,
                infrx.retired_individuals to service_role;

do $$
declare
  r text;
begin
  foreach r in array array['infrx.signup_identity_claims', 'infrx.retired_individuals']
  loop
    execute format('create or replace trigger %I before truncate on %s for each statement '
                   'execute function infrx.forbid_truncate()',
                   replace(r, '.', '_') || '_no_truncate', r);
  end loop;
end $$;

revoke all on function infrx.personal_org_binding_guard() from public, anon, authenticated;
revoke all on function infrx.retired_wallet_guard() from public, anon, authenticated;
revoke all on function infrx.legacy_usd_rollout_hold(uuid)
  from public, anon, authenticated, service_role;
revoke all on function infrx.record_signup_denial(uuid, text)
  from public, anon, authenticated, service_role;
revoke all on function public.claim_signup_grant(uuid, text, uuid)
  from public, anon, authenticated;
revoke all on function infrx.retire_individual(uuid, text, text, text)
  from public, anon, authenticated;
grant execute on function public.claim_signup_grant(uuid, text, uuid) to service_role;
grant execute on function infrx.retire_individual(uuid, text, text, text) to service_role;
comment on function public.claim_signup_grant(uuid, text, uuid) is
  'A1: the one eligibility operation (auth callback retry, first login, backfill). '
  'Derives verification itself; one +10000.00000000 CREDIT grant per individual and per '
  'verified email. service_role only.';
comment on function infrx.retire_individual(uuid, text, text, text) is
  'A1 retention: anonymise, revoke keys, suspend the personal org, freeze the wallet; '
  'money history kept. service_role only.';
