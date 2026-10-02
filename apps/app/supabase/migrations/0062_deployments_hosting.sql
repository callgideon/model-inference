-- AP-05 (wave 7, lane api-hosting; R271 allocates 0062): durable private deployments
-- (research/plan/api-lifecycle/contracts.md §5). A private deployment IS 0007's
-- `deployment_revisions` dev row (draft -> validating -> ready_private -> retired, moved only
-- by 0032's `lab_control_transition`) on a dev endpoint (0007 `endpoints`, created by
-- 0032's `lab_control_endpoint`); its long work is a 0060 control operation
-- (`deployment.create` | `deployment.smoke` | `deployment.retire`, resource = the deployment).
-- This file adds only what those lack:
--
--   hosting_deployments   the request of one deployment revision: hosting profile, limits,
--                         warm/expiry policy; immutable. `hosting_request` writes it, the
--                         draft revision and the `deployment.create` operation in ONE
--                         transaction (0060's documented domain-function pattern), so a
--                         replayed `Idempotency-Key` names the first deployment and nothing else.
--   hosting_allocations   the candidate slot a deployment holds: reserved -> launched ->
--                         released (reserved -> released too). A slot holds at most ONE
--                         unreleased allocation (the capacity rule: a second request is
--                         `capacity_unavailable`, never an eviction) and a deployment at most
--                         one. `resource_tag` is the stable tag the launcher finds its engine by.
--   hosting_receipts      immutable observations of one allocation: `identity` (installed bytes
--                         and runtime against the requested revision), `smoke` (one bounded
--                         finite-video request), `health` (the running engine's runtime);
--                         each expires. A receipt passes iff it names no reason.
--
-- Fencing: the controller writes allocations, receipts and state moves inside a transaction
-- that first calls `hosting_fence` (the live lease of an unfinished operation on THIS
-- deployment, its 0060 row locked until commit), so a controller that lost its lease writes
-- nothing. `hosting_active` lists a deployment's unfinished operations (the control login
-- reads no 0060 row directly).
-- Writers: the Lab control login `infrx_lab_control` (where the /lab/v1 routes and the
-- hosting controller run); the browser roles reach nothing (R271).
-- LOCAL-ONLY (R151/R201/R271): never applied hosted; 0062 is api-hosting's allocated number.
--
-- ROLLBACK (this file alone; tests/d/test_upgrade_0062_mutants.py runs these lines):
-- rollback: drop function if exists infrx.hosting_request(jsonb), infrx.hosting_fence(jsonb), infrx.hosting_active(jsonb), infrx.hosting_allocations_guard() cascade;
-- rollback: drop table if exists infrx.hosting_receipts, infrx.hosting_allocations, infrx.hosting_deployments;
-- (draft revisions and endpoints written stay: they are 0007 history)

create table if not exists infrx.hosting_deployments (
  deployment_revision_id uuid primary key
    references infrx.deployment_revisions on delete restrict,
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  serving_version_id uuid not null,
  endpoint_id uuid not null,
  profile_id text not null check (profile_id ~ '^[a-z0-9][a-z0-9.-]{0,63}$'),
  request jsonb not null check (jsonb_typeof(request) = 'object'
    and octet_length(request::text) <= 4096),
  operation_id uuid not null,
  created_by text not null check (length(btrim(created_by)) between 1 and 200),
  created_at timestamptz not null,
  expires_at timestamptz not null,
  constraint hosting_deployments_expire_later check (expires_at > created_at)
);
create or replace trigger hosting_deployments_immutable before update or delete
  on infrx.hosting_deployments for each row execute function infrx.forbid_update_delete();

create table if not exists infrx.hosting_allocations (
  allocation_id uuid primary key,
  deployment_revision_id uuid not null
    references infrx.hosting_deployments on delete restrict,
  slot text not null check (slot ~ '^[a-z0-9][a-z0-9./-]{0,99}$'),
  port int not null check (port between 1024 and 65535),
  resource_tag text not null check (resource_tag ~ '^infrx-hosting-[0-9a-f-]{36}$'),
  state text not null default 'reserved' check (state in ('reserved', 'launched', 'released')),
  operation_id uuid not null,
  reserved_at timestamptz not null,
  launched_at timestamptz,
  released_at timestamptz,
  constraint hosting_allocations_released_at check ((state = 'released') = (released_at is not null)),
  constraint hosting_allocations_launched_at check (state <> 'launched' or launched_at is not null),
  constraint hosting_allocations_owner_key unique (allocation_id, deployment_revision_id)
);
-- the capacity rule: one unreleased allocation per slot, and per deployment
create unique index if not exists hosting_allocations_slot_held
  on infrx.hosting_allocations (slot) where state <> 'released';
create unique index if not exists hosting_allocations_one_live
  on infrx.hosting_allocations (deployment_revision_id) where state <> 'released';

-- An allocation moves forward only (reserved -> launched -> released, reserved -> released);
-- its identity never changes and nothing is deleted.
create or replace function infrx.hosting_allocations_guard() returns trigger
language plpgsql set search_path = infrx, public, pg_temp as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'allocation % is never deleted', old.allocation_id using errcode = '23514';
  end if;
  if (to_jsonb(new) - 'state' - 'launched_at' - 'released_at')
       is distinct from (to_jsonb(old) - 'state' - 'launched_at' - 'released_at')
     or (new.state is distinct from old.state and not (
           (old.state = 'reserved' and new.state in ('launched', 'released'))
        or (old.state = 'launched' and new.state = 'released'))) then
    raise exception 'allocation %: % -> % is not a move', old.allocation_id, old.state, new.state
      using errcode = '23514';
  end if;
  return new;
end $$;
create or replace trigger hosting_allocations_guard before update or delete
  on infrx.hosting_allocations for each row execute function infrx.hosting_allocations_guard();

create table if not exists infrx.hosting_receipts (
  receipt_id uuid primary key,
  deployment_revision_id uuid not null,
  allocation_id uuid not null,
  kind text not null check (kind in ('identity', 'smoke', 'health')),
  passed boolean not null,
  observed jsonb not null check (jsonb_typeof(observed) = 'object'
    and octet_length(observed::text) <= 16384),
  reasons jsonb not null check (jsonb_typeof(reasons) = 'array'),
  operation_id uuid,
  checked_at timestamptz not null,
  expires_at timestamptz not null,
  recorded bigint generated always as identity,      -- insertion order: newest first
  constraint hosting_receipts_allocation_fk foreign key (allocation_id, deployment_revision_id)
    references infrx.hosting_allocations (allocation_id, deployment_revision_id) on delete restrict,
  constraint hosting_receipts_pass_iff_no_reason check (passed = (jsonb_array_length(reasons) = 0)),
  -- only a health observation is made outside an operation
  constraint hosting_receipts_decided_in_an_operation check (kind = 'health' or operation_id is not null),
  constraint hosting_receipts_expire_later check (expires_at > checked_at)
);
create index if not exists hosting_receipts_latest
  on infrx.hosting_receipts (deployment_revision_id, recorded desc);
create or replace trigger hosting_receipts_immutable before update or delete
  on infrx.hosting_receipts for each row execute function infrx.forbid_update_delete();

-- ================================================================ functions ===
-- {operation_id, fence, deployment_revision_id} -> void. The caller's transaction holds the
-- live lease (this fence, not expired) of an unfinished operation on this deployment; the
-- 0060 row stays locked until the caller commits, so no other holder interleaves.
create or replace function infrx.hosting_fence(p_args jsonb) returns void
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  r infrx.control_operations := infrx.control_op_row(p_args);
begin
  if r.resource_kind is distinct from 'deployment'
     or r.resource_id is distinct from p_args->>'deployment_revision_id' then
    perform infrx.refuse('not_found', 'no such operation on this deployment');
  end if;
  if r.fence is distinct from (p_args->>'fence')::bigint or r.lease_until is null
     or r.lease_until <= infrx.now() or r.state in ('succeeded', 'failed', 'cancelled') then
    perform infrx.refuse('state_conflict', 'stale fence: the lease was lost');
  end if;
end $$;

-- {deployment: {deployment_revision_id, endpoint_id, provider_org_id, serving_version_id,
--  max_input_tokens, max_output_tokens}, profile_id, request, expire_after_s, actor,
--  idempotency_key, input_hash} -> {operation, replayed, outcome}. One transaction: the
-- `deployment.create` operation (0060, resource = the deployment) and - unless the key
-- replays - the private dev `draft` revision (0007) and its hosting row. A replay answers the
-- first operation, whose resource is the first deployment, whatever id this call proposed.
create or replace function infrx.hosting_request(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  d jsonb := p_args->'deployment';
  v_now timestamptz := infrx.now();
  v_started jsonb := infrx.control_op_start(jsonb_build_object(
    'kind', 'deployment.create', 'actor', p_args->'actor',
    'idempotency_key', p_args->>'idempotency_key', 'input_hash', p_args->>'input_hash',
    'resource_kind', 'deployment', 'resource_id', d->>'deployment_revision_id',
    'retention_s', 86400));
begin
  if (v_started->>'replayed')::boolean then
    return v_started;
  end if;
  begin
    insert into infrx.deployment_revisions (deployment_revision_id, endpoint_id, provider_org_id,
      environment, serving_version_id, visibility, state, max_input_tokens, max_output_tokens,
      created_by, created_at)
    values ((d->>'deployment_revision_id')::uuid, (d->>'endpoint_id')::uuid,
            (d->>'provider_org_id')::uuid, 'dev', (d->>'serving_version_id')::uuid, 'private',
            'draft', (d->>'max_input_tokens')::int, (d->>'max_output_tokens')::int,
            'lab-hosting', v_now);
    insert into infrx.hosting_deployments (deployment_revision_id, provider_org_id,
      serving_version_id, endpoint_id, profile_id, request, operation_id, created_by,
      created_at, expires_at)
    values ((d->>'deployment_revision_id')::uuid, (d->>'provider_org_id')::uuid,
            (d->>'serving_version_id')::uuid, (d->>'endpoint_id')::uuid, p_args->>'profile_id',
            p_args->'request', (v_started->'operation'->>'operation_id')::uuid,
            coalesce(p_args->'actor'->>'user_id', 'operator'), v_now,
            v_now + make_interval(secs => (p_args->>'expire_after_s')::int));
  exception when check_violation or not_null_violation or foreign_key_violation then
    perform infrx.refuse('invalid_request', 'not a deployable request: endpoint, serving '
                         'revision, limits, profile or expiry');
  end;
  return v_started;
end $$;

-- {deployment_revision_id} -> [operation_id, ...]: the deployment's unfinished operations,
-- oldest first.
create or replace function infrx.hosting_active(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(operation_id::text order by created_at, operation_id), '[]')
    from infrx.control_operations
   where resource_kind = 'deployment' and resource_id = p_args->>'deployment_revision_id'
     and state in ('queued', 'running', 'cancel_requested');
$$;

-- ============================================================== privileges ===
do $$
declare
  t text;
begin
  foreach t in array array['hosting_deployments', 'hosting_allocations', 'hosting_receipts'] loop
    execute format('alter table infrx.%I enable row level security', t);
    execute format('revoke all on infrx.%I from public, anon, authenticated', t);
    execute format('drop policy if exists api_hosting_control on infrx.%I', t);
    execute format('create policy api_hosting_control on infrx.%I to infrx_lab_control '
                   'using (true) with check (true)', t);
  end loop;
end $$;
grant select on infrx.hosting_deployments to infrx_lab_control;
grant select, insert on infrx.hosting_allocations, infrx.hosting_receipts to infrx_lab_control;
grant update (state, launched_at, released_at) on infrx.hosting_allocations to infrx_lab_control;
revoke all on function infrx.hosting_allocations_guard() from public, anon, authenticated;
revoke all on function infrx.hosting_request(jsonb), infrx.hosting_fence(jsonb),
  infrx.hosting_active(jsonb) from public, anon, authenticated;
grant execute on function infrx.hosting_request(jsonb), infrx.hosting_fence(jsonb),
  infrx.hosting_active(jsonb) to service_role, infrx_lab_control;
