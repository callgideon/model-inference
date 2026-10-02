-- SR-AP01-1 (lane api-schema-2, wave 7 batch 2; AP-01's schema request, evidence
-- research/plan/evidence/w7/api-identity-a5c856d.md): the six SECURITY DEFINER identity doors
-- `infrx.console.session.PgIdentity` needs on a login that executes named functions only - the
-- Lab control unit's `infrx_lab_control` (0043), where `lab_workspaces` mounts. Each body is
-- the statement PgIdentity runs today on the platform role (account summary, the email lookup,
-- a provider's current members, grant / revoke a membership, create a provider); the one
-- current membership per pair is 0007's partial unique index, the provider slug 0007's unique
-- key, so a retry finds the first attempt's row. DDL as the request gives it, plus
-- `#variable_conflict use_column` in the three PL/pgSQL bodies: their RETURNS TABLE names
-- (user_id, role, revoked_at, slug, ...) are also the table's columns, so every ON CONFLICT
-- target and WHERE on them is otherwise 42702 ambiguous (measured).
-- Privileges: EXECUTE for service_role (the gateway's broad login) and infrx_lab_control only;
-- never PUBLIC, the browser roles or infrx_runtime (whose set is pinned catalog-wide by
-- tests/d/checks_reads.RUNTIME_FUNCTIONS; it gains these with the first gateway composition
-- on that login that needs them).
-- LOCAL-ONLY (R151/R201/R271): never applied hosted; 0065 is allocated by the coordinator
-- (merge #87, SR-AP01-1) to api-schema-2.
--
-- ROLLBACK (this file alone; nothing earlier references it; tests/d/test_control_ops_upgrade.py runs these lines):
-- rollback: drop function if exists infrx.identity_account(uuid), infrx.identity_user_by_email(text), infrx.identity_members(uuid), infrx.identity_grant_member(uuid, uuid, text, text), infrx.identity_revoke_member(uuid, uuid), infrx.identity_create_provider(text, text, text);

create or replace function infrx.identity_account(p_user uuid)
returns table (is_operator boolean, verified boolean, org_id uuid, wallet boolean,
               suspended boolean, grant_amount text, granted_at timestamptz)
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select p.is_operator, v.verification_evidence_ref is not null,
         coalesce(w.personal_org_id, v.personal_org_id), w.wallet_id is not null,
         coalesce(o.suspended, false), e.amount::text, e.granted_at
    from public.profiles p cross join lateral infrx.verified_user(p.id) v
    left join infrx.credit_wallets w on w.owner_user_id = p.id and w.kind = 'consumer'
    left join public.organizations o on o.id = coalesce(w.personal_org_id, v.personal_org_id)
    left join infrx.signup_entitlements e
           on e.user_id = p.id and e.entitlement = 'initial_signup_grant'
   where p.id = p_user $$;

create or replace function infrx.identity_user_by_email(p_email text) returns uuid
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select case when count(*) = 1 then min(id::text)::uuid end
    from public.profiles where lower(email) = lower(p_email) $$;

create or replace function infrx.identity_members(p_provider uuid)
returns table (user_id uuid, email text, role text, granted_by text, granted_at timestamptz,
               revoked_at timestamptz)
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select m.user_id, p.email, m.role, m.granted_by, m.granted_at, m.revoked_at
    from infrx.provider_memberships m join public.profiles p on p.id = m.user_id
   where m.provider_org_id = p_provider and m.revoked_at is null
   order by m.granted_at, m.membership_id $$;

create or replace function infrx.identity_grant_member(p_provider uuid, p_user uuid,
  p_role text, p_by text)
returns table (user_id uuid, email text, role text, granted_by text, granted_at timestamptz,
               revoked_at timestamptz, created boolean)
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
#variable_conflict use_column
declare v_created boolean;
begin
  insert into infrx.provider_memberships (provider_org_id, user_id, role, granted_by)
  values (p_provider, p_user, p_role, p_by)
  on conflict (provider_org_id, user_id) where revoked_at is null do nothing;
  v_created := found;
  if exists (select 1 from infrx.provider_memberships m where m.provider_org_id = p_provider
             and m.user_id = p_user and m.revoked_at is null and m.role <> p_role) then
    raise exception 'state_conflict: another current role' using errcode = 'P0001';
  end if;
  return query select m.user_id, p.email, m.role, m.granted_by, m.granted_at, m.revoked_at,
                      v_created
    from infrx.provider_memberships m join public.profiles p on p.id = m.user_id
   where m.provider_org_id = p_provider and m.user_id = p_user and m.revoked_at is null;
end $$;

create or replace function infrx.identity_revoke_member(p_provider uuid, p_user uuid)
returns table (user_id uuid, email text, role text, granted_by text, granted_at timestamptz,
               revoked_at timestamptz)
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
#variable_conflict use_column
begin
  update infrx.provider_memberships set revoked_at = greatest(infrx.now(), granted_at)
   where provider_org_id = p_provider and user_id = p_user and revoked_at is null;
  return query select m.user_id, p.email, m.role, m.granted_by, m.granted_at, m.revoked_at
    from infrx.provider_memberships m join public.profiles p on p.id = m.user_id
   where m.provider_org_id = p_provider and m.user_id = p_user
   order by m.revoked_at is null desc, m.granted_at desc, m.revoked_at desc limit 1;
  if not found then raise exception 'not_found: member' using errcode = 'P0001'; end if;
end $$;

create or replace function infrx.identity_create_provider(p_slug text, p_name text, p_by text)
returns table (provider_org_id uuid, slug text, display_name text, created_by text,
               created_at timestamptz, created boolean)
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
#variable_conflict use_column
declare v_created boolean;
begin
  insert into infrx.provider_orgs (slug, display_name, created_by) values (p_slug, p_name, p_by)
  on conflict (slug) do nothing;
  v_created := found;
  if exists (select 1 from infrx.provider_orgs o where o.slug = p_slug
             and o.display_name <> p_name) then
    raise exception 'state_conflict: slug taken' using errcode = 'P0001';
  end if;
  return query select o.provider_org_id, o.slug, o.display_name, o.created_by, o.created_at,
                      v_created from infrx.provider_orgs o where o.slug = p_slug;
end $$;

-- ============================================================== privileges ===
revoke all on function infrx.identity_account(uuid), infrx.identity_user_by_email(text),
  infrx.identity_members(uuid), infrx.identity_grant_member(uuid, uuid, text, text),
  infrx.identity_revoke_member(uuid, uuid), infrx.identity_create_provider(text, text, text)
  from public, anon, authenticated;
grant execute on function infrx.identity_account(uuid), infrx.identity_user_by_email(text),
  infrx.identity_members(uuid), infrx.identity_grant_member(uuid, uuid, text, text),
  infrx.identity_revoke_member(uuid, uuid), infrx.identity_create_provider(text, text, text)
  to service_role, infrx_lab_control;
