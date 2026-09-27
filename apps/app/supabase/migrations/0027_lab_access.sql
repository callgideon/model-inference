-- L2-SQL (wave-5 LW1, lane lab-sql; 09-amendment-workstreams §L2, R150, R156): the Lab's
-- provider-membership read and versioned source-purpose access grants, in the frozen v2
-- shapes (`contracts/v2/records.py` ProviderMembership, AccessGrant, DataCategory,
-- DataPurpose). LOCAL-ONLY (R151): never applied hosted; the number is the next free at merge.
--
-- Memberships are 0007's `infrx.provider_memberships` (never `org_members.role` nor
-- `profiles.is_operator`); what a role may do is the contract's ROLE_CAPABILITIES, applied by
-- the L2 service at use time, so no second copy of it lives here.
--
--   lab_access_grants   one grant per (grantor organization, recipient provider), VERSIONED:
--                       every change - a new scope, a revocation, a re-grant - is a new row
--                       with the next version, and no row is ever edited or deleted. The
--                       latest version is the current grant (its `revoked_at`/`expires_at`
--                       decide currency at use time); every version is the history.
--
-- Named RPCs (SECURITY DEFINER; EXECUTE for service_role only through 0004's default
-- privileges; `{...}` in, jsonb out; refusals `<code>: detail`, jobstore.domain_error):
--   lab_provider_memberships   {user_id}: the user's membership rows, current or revoked, with
--                              the provider's display name - THE one membership read (R156).
--   lab_put_access_grant       {actor_user_id, grantor_org_id, recipient_provider_org_id,
--                              model_ids, categories, purposes, retention_days, expires_at?}:
--                              the grantor's OWNER only, not suspended (R33); every model is the
--                              recipient's own; the next version, effective now.
--   lab_revoke_access_grant    {actor_user_id, grantor_org_id, recipient_provider_org_id}: the
--                              next version with the same scope and `revoked_at` now; the
--                              grantor's owner only; a missing or already revoked grant is
--                              `state_conflict`.
--   lab_access_grants          {grantor_org_id, recipient_provider_org_id}: every version,
--                              oldest first (the L2 port checks membership before asking).
--   lab_deployment_aggregates  {provider_org_id, since?, until?}: one row per deployment
--                              revision of THAT provider with requests in the window (since, until],
--                              default the 24 h up to now: request and error counts, no organization, key,
--                              user or request identifier (LAB-05). p95 latency is null: no
--                              latency is stored per job yet.
--
-- Browser roles reach nothing (schema `infrx` is closed). The platform role reads the grants
-- and writes them only through the RPCs.
--
-- ROLLBACK (this file alone; nothing earlier references it):
--   drop function infrx.lab_deployment_aggregates(jsonb), infrx.lab_access_grants(jsonb),
--     infrx.lab_revoke_access_grant(jsonb), infrx.lab_put_access_grant(jsonb),
--     infrx.lab_provider_memberships(jsonb), infrx.lab_grant_version(jsonb),
--     infrx.lab_grant_json(infrx.lab_access_grants);
--   drop table infrx.lab_access_grants;
--
-- Re-runnable: `if not exists`, `create or replace`.

create table if not exists infrx.lab_access_grants (
  grant_id uuid not null,
  version int not null check (version >= 1),
  grantor_org_id uuid not null references public.organizations(id) on delete restrict,
  recipient_provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  model_ids text[] not null,
  categories text[] not null check (categories <@ array['request_content',
    'response_content', 'media', 'usage_metadata', 'feedback']),
  purposes text[] not null
    check (purposes <@ array['capture', 'provider_sharing', 'external_judging', 'training']),
  retention_days int not null check (retention_days between 1 and 90),
  effective_at timestamptz not null,
  expires_at timestamptz check (expires_at > effective_at),
  revoked_at timestamptz check (revoked_at >= effective_at),
  written_by uuid not null references public.profiles(id) on delete restrict,
  primary key (grant_id, version),
  constraint lab_access_grants_one_pair unique (grantor_org_id, recipient_provider_org_id,
                                                version)
);
create or replace trigger lab_access_grants_immutable before update or delete
  on infrx.lab_access_grants for each row execute function infrx.forbid_update_delete();
create or replace trigger lab_access_grants_no_truncate before truncate
  on infrx.lab_access_grants for each statement execute function infrx.forbid_truncate();

create or replace function infrx.lab_grant_json(g infrx.lab_access_grants) returns jsonb
language sql stable set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('grant_id', g.grant_id, 'version', g.version,
    'grantor_org_id', g.grantor_org_id, 'recipient_provider_org_id',
    g.recipient_provider_org_id, 'model_ids', to_jsonb(g.model_ids),
    'categories', to_jsonb(g.categories), 'purposes', to_jsonb(g.purposes),
    'retention_days', g.retention_days, 'effective_at', g.effective_at,
    'expires_at', g.expires_at, 'revoked_at', g.revoked_at)
$$;

-- The shared half of both writes: the grantor's owner, not suspended; the pair serialized and
-- its latest row returned (NULL when there is none), so two writers get consecutive versions.
create or replace function infrx.lab_grant_version(p_args jsonb)
returns infrx.lab_access_grants language plpgsql security definer
set search_path = infrx, public, pg_temp as $$
declare
  v_grantor uuid := (p_args->>'grantor_org_id')::uuid;
  g infrx.lab_access_grants%rowtype;
begin
  if not exists (select 1 from public.org_members m where m.org_id = v_grantor
                 and m.user_id = (p_args->>'actor_user_id')::uuid and m.role = 'owner') then
    perform infrx.refuse('forbidden', 'only the owner of the grantor organization writes');
  end if;
  if (select o.suspended from public.organizations o where o.id = v_grantor) then
    perform infrx.refuse('org_suspended', 'organization ' || v_grantor || ' is suspended');
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    'lab_access_grants/' || v_grantor || '/' || (p_args->>'recipient_provider_org_id'), 0));
  select * into g from infrx.lab_access_grants
   where grantor_org_id = v_grantor
     and recipient_provider_org_id = (p_args->>'recipient_provider_org_id')::uuid
   order by version desc limit 1;
  return g;
end $$;

create or replace function infrx.lab_put_access_grant(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_provider uuid := (p_args->>'recipient_provider_org_id')::uuid;
  v_models text[] := array(select jsonb_array_elements_text(p_args->'model_ids'));
  prev infrx.lab_access_grants%rowtype;
  g infrx.lab_access_grants%rowtype;
begin
  prev := infrx.lab_grant_version(p_args);
  if exists (select 1 from unnest(v_models) m where not exists (
       select 1 from public.models pm where pm.model_uuid::text = m
          and pm.provider_org_id = v_provider)) then
    perform infrx.refuse('invalid_request', 'every model is one of the recipient''s own');
  end if;
  begin
    insert into infrx.lab_access_grants (grant_id, version, grantor_org_id,
      recipient_provider_org_id, model_ids, categories, purposes, retention_days,
      effective_at, expires_at, written_by)
    values (coalesce(prev.grant_id, gen_random_uuid()), coalesce(prev.version, 0) + 1,
      (p_args->>'grantor_org_id')::uuid, v_provider, v_models,
      array(select jsonb_array_elements_text(p_args->'categories')),
      array(select jsonb_array_elements_text(p_args->'purposes')),
      (p_args->>'retention_days')::int, infrx.now(), (p_args->>'expires_at')::timestamptz,
      (p_args->>'actor_user_id')::uuid)
    returning * into g;
  exception when check_violation or not_null_violation or foreign_key_violation then
    perform infrx.refuse('invalid_request', 'not a grantable recipient, scope, retention '
                         'or expiry');
  end;
  return infrx.lab_grant_json(g);
end $$;

create or replace function infrx.lab_revoke_access_grant(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  prev infrx.lab_access_grants%rowtype;
  g infrx.lab_access_grants%rowtype;
begin
  prev := infrx.lab_grant_version(p_args);
  if prev.grant_id is null or prev.revoked_at is not null then
    perform infrx.refuse('state_conflict', 'no current grant to revoke');
  end if;
  insert into infrx.lab_access_grants (grant_id, version, grantor_org_id,
    recipient_provider_org_id, model_ids, categories, purposes, retention_days, effective_at,
    expires_at, revoked_at, written_by)
  values (prev.grant_id, prev.version + 1, prev.grantor_org_id, prev.recipient_provider_org_id,
    prev.model_ids, prev.categories, prev.purposes, prev.retention_days, prev.effective_at,
    prev.expires_at, infrx.now(), (p_args->>'actor_user_id')::uuid)
  returning * into g;
  return infrx.lab_grant_json(g);
end $$;

create or replace function infrx.lab_access_grants(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(infrx.lab_grant_json(g) order by g.version), '[]')
    from infrx.lab_access_grants g
   where g.grantor_org_id = (p_args->>'grantor_org_id')::uuid
     and g.recipient_provider_org_id = (p_args->>'recipient_provider_org_id')::uuid
$$;

create or replace function infrx.lab_provider_memberships(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object('provider_org_id', m.provider_org_id,
      'provider_name', o.display_name, 'user_id', m.user_id, 'role', m.role,
      'granted_by', m.granted_by, 'granted_at', m.granted_at, 'revoked_at', m.revoked_at)
      order by m.granted_at, m.membership_id), '[]')
    from infrx.provider_memberships m join infrx.provider_orgs o using (provider_org_id)
   where m.user_id = (p_args->>'user_id')::uuid
$$;

create or replace function infrx.lab_deployment_aggregates(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  with win as (
    select coalesce((p_args->>'since')::timestamptz,
                    coalesce((p_args->>'until')::timestamptz, infrx.now()) - interval '1 day')
             as since_at,
           coalesce((p_args->>'until')::timestamptz, infrx.now()) as until_at),
  rows as (
    select jsonb_build_object('deployment_revision_id', d.deployment_revision_id,
             'window_start', win.since_at, 'window_end', win.until_at, 'requests', count(*),
             'errors', count(*) filter (where j.state in ('failed', 'expired')),
             'p95_latency_ms', null) as r
      from win cross join infrx.deployment_revisions d
      join infrx.jobs j on j.deployment_revision_id = d.deployment_revision_id
       and j.admitted_at > win.since_at and j.admitted_at <= win.until_at
     where d.provider_org_id = (p_args->>'provider_org_id')::uuid
     group by d.deployment_revision_id, win.since_at, win.until_at)
  select coalesce(jsonb_agg(r order by r->>'deployment_revision_id'), '[]') from rows
$$;

alter table infrx.lab_access_grants enable row level security;
revoke all on infrx.lab_access_grants from public, anon, authenticated, service_role;
grant select on infrx.lab_access_grants to service_role;
