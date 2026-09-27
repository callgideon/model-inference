-- L2-SQL (wave-5 LW1, lane lab-sql; 09-amendment-workstreams §L2, R150): provider capabilities
-- and purpose-specific data-access grants for the Lab. LOCAL-ONLY (R151): applied to task-local
-- and E-gate databases, never hosted (the hosted schema stays at 0026). The number is the next
-- free one at merge.
--
--   provider_role_capabilities   which named provider operation each provider role performs.
--                                Provider roles are 0007's `provider_memberships.role`, never
--                                `org_members.role` (consumer owner/member) nor
--                                `profiles.is_operator` (09 §L2 item 1).
--   lab_data_grants              one explicit grant by a consumer organization's OWNER to one
--                                provider (the recipient), for one of that provider's models,
--                                one data category and one purpose, until an expiry; a
--                                revocation is one-way. The rows are the grant history.
--
-- Named RPCs (SECURITY DEFINER, service_role only; `{...}` in, jsonb out; refusals are
-- `<code>: detail`, jobstore.domain_error):
--   lab_grant_data_access      {actor_user_id, source_org_id, provider_org_id, model_id,
--                               data_category, purpose, expires_at}; the actor owns the source
--                               organization, which is not suspended; the model is the
--                               recipient's; 0 < expiry - now <= 366 days.
--   lab_revoke_data_access     {actor_user_id, grant_id}; the source owner; once.
--   lab_data_access_allowed    {user_id, provider_org_id, source_org_id, model_id,
--                               data_category, purpose} -> boolean: a CURRENT membership whose
--                               role has `use_granted_data` AND a current grant (unrevoked,
--                               unexpired at infrx.now()). A role alone never authorizes reuse.
--   lab_data_grants            {user_id, provider_org_id} (a member with use_granted_data) or
--                               {user_id, source_org_id} (the source owner): the grant history.
--   lab_deployment_aggregates  {user_id, provider_org_id, since?, until?}: per deployment
--                               revision of THAT provider, request counts by state; no
--                               organization, key, user or request identifier (LAB-05).
--
-- Browser roles reach nothing (schema `infrx` stays closed, and 0004's default privileges give
-- EXECUTE on new `infrx` functions to service_role only); the platform role reads both
-- relations and writes only through the RPCs. No existing relation, function or grant changes.
--
-- ROLLBACK (this file alone; nothing earlier references it):
--   drop function infrx.lab_deployment_aggregates(jsonb), infrx.lab_data_grants(jsonb),
--     infrx.lab_data_access_allowed(jsonb), infrx.lab_revoke_data_access(jsonb),
--     infrx.lab_grant_data_access(jsonb), infrx.lab_provider_can(uuid, uuid, text),
--     infrx.lab_grant_json(infrx.lab_data_grants), infrx.lab_data_grants_guard();
--   drop table infrx.lab_data_grants, infrx.provider_role_capabilities;
--
-- Re-runnable: `if not exists`, `on conflict do nothing`, `create or replace`.

-- ================================================================ capabilities ===
create table if not exists infrx.provider_role_capabilities (
  role text not null check (role in ('viewer', 'developer', 'administrator')),
  capability text not null check (capability in ('read_aggregates', 'use_granted_data')),
  primary key (role, capability)
);
insert into infrx.provider_role_capabilities (role, capability) values
  ('viewer', 'read_aggregates'), ('developer', 'read_aggregates'),
  ('administrator', 'read_aggregates'), ('developer', 'use_granted_data'),
  ('administrator', 'use_granted_data')
on conflict do nothing;

-- ====================================================================== grants ===
create table if not exists infrx.lab_data_grants (
  grant_id uuid primary key default gen_random_uuid(),
  source_org_id uuid not null references public.organizations(id) on delete restrict,
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  model_id uuid not null,
  data_category text not null check (data_category in ('traces', 'content', 'feedback')),
  purpose text not null
    check (purpose in ('review', 'annotation', 'evaluation', 'training', 'export')),
  granted_by uuid not null references public.profiles(id) on delete restrict,
  granted_at timestamptz not null default infrx.now(),
  expires_at timestamptz not null,
  revoked_at timestamptz,
  revoked_by uuid references public.profiles(id) on delete restrict,
  -- The model is the recipient's own (0007's `models_owner_key`).
  constraint lab_data_grants_model_is_the_recipients foreign key (model_id, provider_org_id)
    references public.models (model_uuid, provider_org_id) on delete restrict,
  constraint lab_data_grants_expiry_after_grant check (expires_at > granted_at),
  constraint lab_data_grants_revocation_is_complete
    check ((revoked_at is null) = (revoked_by is null)
           and (revoked_at is null or revoked_at >= granted_at))
);
create index if not exists lab_data_grants_lookup_idx on infrx.lab_data_grants
  (provider_org_id, source_org_id, model_id, data_category, purpose) where revoked_at is null;
create index if not exists lab_data_grants_source_idx
  on infrx.lab_data_grants (source_org_id, granted_at desc);

create or replace function infrx.lab_data_grants_guard() returns trigger
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  -- A DELETE has no NEW, so it is refused here too: a grant is history.
  if (to_jsonb(new) - 'revoked_at' - 'revoked_by')
       is distinct from (to_jsonb(old) - 'revoked_at' - 'revoked_by')
     or old.revoked_at is not null then
    raise exception 'grant % is history: only a one-way revocation is permitted', old.grant_id
      using errcode = '23514';
  end if;
  return new;
end $$;
create or replace trigger lab_data_grants_guard before update or delete
  on infrx.lab_data_grants for each row execute function infrx.lab_data_grants_guard();
create or replace trigger provider_role_capabilities_immutable before update or delete
  on infrx.provider_role_capabilities for each row execute function infrx.forbid_update_delete();
create or replace trigger lab_data_grants_no_truncate before truncate on infrx.lab_data_grants
  for each statement execute function infrx.forbid_truncate();
create or replace trigger provider_role_capabilities_no_truncate before truncate
  on infrx.provider_role_capabilities for each statement execute function infrx.forbid_truncate();

-- =================================================================== helpers ===
create or replace function infrx.lab_provider_can(p_user uuid, p_provider uuid,
                                                  p_capability text) returns boolean
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select exists (select 1 from infrx.provider_memberships m
                   join infrx.provider_role_capabilities c on c.role = m.role
                  where m.user_id = p_user and m.provider_org_id = p_provider
                    and m.revoked_at is null and c.capability = p_capability)
$$;

create or replace function infrx.lab_grant_json(g infrx.lab_data_grants) returns jsonb
language sql stable set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('grant_id', g.grant_id, 'source_org_id', g.source_org_id,
    'provider_org_id', g.provider_org_id, 'model_id', g.model_id,
    'data_category', g.data_category, 'purpose', g.purpose, 'granted_at', g.granted_at,
    'expires_at', g.expires_at, 'revoked_at', g.revoked_at)
$$;

-- ======================================================================= RPCs ===
create or replace function infrx.lab_grant_data_access(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_actor uuid := (p_args->>'actor_user_id')::uuid;
  v_source uuid := (p_args->>'source_org_id')::uuid;
  v_expires timestamptz := (p_args->>'expires_at')::timestamptz;
  g infrx.lab_data_grants%rowtype;
begin
  if not exists (select 1 from public.org_members m where m.org_id = v_source
                 and m.user_id = v_actor and m.role = 'owner') then
    perform infrx.refuse('forbidden', 'only the owner of the source organization grants');
  end if;
  if (select o.suspended from public.organizations o where o.id = v_source) then
    perform infrx.refuse('org_suspended', 'organization ' || v_source || ' is suspended');
  end if;
  if v_expires is null or v_expires <= infrx.now()
     or v_expires > infrx.now() + interval '366 days' then
    perform infrx.refuse('invalid_request', 'expires_at is after now and within 366 days');
  end if;
  begin
    insert into infrx.lab_data_grants (source_org_id, provider_org_id, model_id,
      data_category, purpose, granted_by, expires_at)
    values (v_source, (p_args->>'provider_org_id')::uuid, (p_args->>'model_id')::uuid,
      p_args->>'data_category', p_args->>'purpose', v_actor, v_expires)
    returning * into g;
  exception when check_violation or foreign_key_violation or not_null_violation then
    perform infrx.refuse('invalid_request', 'not a grantable model, category or purpose');
  end;
  return infrx.lab_grant_json(g);
end $$;

create or replace function infrx.lab_revoke_data_access(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_actor uuid := (p_args->>'actor_user_id')::uuid;
  g infrx.lab_data_grants%rowtype;
begin
  select * into g from infrx.lab_data_grants where grant_id = (p_args->>'grant_id')::uuid
    for update;
  if not found or not exists (select 1 from public.org_members m
                              where m.org_id = g.source_org_id and m.user_id = v_actor
                                and m.role = 'owner') then
    perform infrx.refuse('forbidden', 'only the owner of the source organization revokes');
  end if;
  if g.revoked_at is not null then
    perform infrx.refuse('state_conflict', 'grant ' || g.grant_id || ' is already revoked');
  end if;
  update infrx.lab_data_grants set revoked_at = infrx.now(), revoked_by = v_actor
   where grant_id = g.grant_id returning * into g;
  return infrx.lab_grant_json(g);
end $$;

create or replace function infrx.lab_data_access_allowed(p_args jsonb) returns boolean
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select infrx.lab_provider_can((p_args->>'user_id')::uuid,
                                (p_args->>'provider_org_id')::uuid, 'use_granted_data')
     and exists (select 1 from infrx.lab_data_grants g
                  where g.provider_org_id = (p_args->>'provider_org_id')::uuid
                    and g.source_org_id = (p_args->>'source_org_id')::uuid
                    and g.model_id = (p_args->>'model_id')::uuid
                    and g.data_category = p_args->>'data_category'
                    and g.purpose = p_args->>'purpose'
                    and g.revoked_at is null and g.expires_at > infrx.now())
$$;

create or replace function infrx.lab_data_grants(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  v_user uuid := (p_args->>'user_id')::uuid;
  v_provider uuid := (p_args->>'provider_org_id')::uuid;
  v_source uuid := (p_args->>'source_org_id')::uuid;
begin
  if v_provider is not null and infrx.lab_provider_can(v_user, v_provider, 'use_granted_data')
  then
    return coalesce((select jsonb_agg(infrx.lab_grant_json(g) order by g.granted_at, g.grant_id)
                       from infrx.lab_data_grants g where g.provider_org_id = v_provider),
                    '[]');
  end if;
  if v_source is not null and exists (select 1 from public.org_members m where
       m.org_id = v_source and m.user_id = v_user and m.role = 'owner') then
    return coalesce((select jsonb_agg(infrx.lab_grant_json(g) order by g.granted_at, g.grant_id)
                       from infrx.lab_data_grants g where g.source_org_id = v_source), '[]');
  end if;
  perform infrx.refuse('forbidden', 'not a member of the grantee nor the source owner');
  return null;
end $$;

create or replace function infrx.lab_deployment_aggregates(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  v_provider uuid := (p_args->>'provider_org_id')::uuid;
begin
  if not infrx.lab_provider_can((p_args->>'user_id')::uuid, v_provider, 'read_aggregates') then
    perform infrx.refuse('forbidden', 'not a member of provider ' || coalesce(v_provider::text, 'null'));
  end if;
  return coalesce((select jsonb_agg(jsonb_build_object(
      'deployment_revision_id', a.deployment_revision_id, 'requests', a.requests,
      'active', a.active, 'succeeded', a.succeeded, 'failed', a.failed,
      'cancelled', a.cancelled, 'expired', a.expired) order by a.deployment_revision_id)
    from (select j.deployment_revision_id, count(*) as requests,
            count(*) filter (where j.state in ('preparing', 'queued', 'running')) as active,
            count(*) filter (where j.state = 'succeeded') as succeeded,
            count(*) filter (where j.state = 'failed') as failed,
            count(*) filter (where j.state = 'cancelled') as cancelled,
            count(*) filter (where j.state = 'expired') as expired
            from infrx.jobs j
            join infrx.deployment_revisions d
              on d.deployment_revision_id = j.deployment_revision_id
           where d.provider_org_id = v_provider
             and j.admitted_at >= coalesce((p_args->>'since')::timestamptz, '-infinity')
             and j.admitted_at < coalesce((p_args->>'until')::timestamptz, 'infinity')
           group by j.deployment_revision_id) a), '[]');
end $$;

-- ================================================================ privileges ===
do $$
declare
  r text;
begin
  foreach r in array array['infrx.provider_role_capabilities', 'infrx.lab_data_grants'] loop
    execute format('alter table %s enable row level security', r);
    execute format('revoke all on %s from public, anon, authenticated, service_role', r);
    execute format('grant select on %s to service_role', r);
  end loop;
end $$;
