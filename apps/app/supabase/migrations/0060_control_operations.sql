-- AP-00 slice 00d (lane api-schema, wave 7, R270/R271): the durable control operation store.
-- A long control operation (artifact verification, deployment, ...) is one row of
-- `infrx.control_operations` in the R270 state enum, created through `control_op_start` under
-- an `Idempotency-Key` scoped to the actor's tenant and the operation kind
-- (`infrx.control_idempotency`: same key + same input hash replays, a different hash is 409
-- `idempotency_conflict`, an expired key is 410 `idempotency_expired` - never a second
-- operation). Work is done under a fenced lease (`control_op_lease` / `_advance` / `_finish`);
-- `control_op_cancel` stops new work; `control_op_get` reads; `control_op_pending` is the
-- controller's discovery. The Python port is `infrx/state/control_ops.py` (PgControlOps,
-- FakeControlOps).
-- No outbox table: `infrx.outbox` does not fit (its `kind` enum and `org_id` belong to the job
-- relay, and `gc_outbox` reaps by job state), and a separate `control_outbox` would duplicate
-- the operation row - a non-terminal, unleased operation IS the pending work, and a domain
-- function that must commit its own row and the operation together calls
-- `infrx.control_op_start` inside its own transaction.
-- Read authority: the browser roles reach nothing (no schema usage, RLS on, no policy; R271:
-- the web apps read through FastAPI). The owning tenant (the actor's provider workspace, else
-- its organization, else its user) or an operator actor reads, enforced in `control_op_get`.
-- Writers: service_role (the gateway's broad login, 0004's default) and the Lab control
-- unit's login infrx_lab_control (where the new /lab/v1 operation routes run). The gateway's
-- dedicated login infrx_runtime gains them with the first gateway route that needs one (its
-- grant set is pinned by tests/d/checks_reads.RUNTIME_FUNCTIONS).
-- LOCAL-ONLY (R151/R201/R271): never applied hosted; 0060 is api-schema's allocated number.
--
-- ROLLBACK (this file alone; tests/d/test_control_ops_upgrade.py runs these lines):
-- rollback: drop function if exists infrx.control_op_start(jsonb), infrx.control_op_lease(jsonb), infrx.control_op_advance(jsonb), infrx.control_op_finish(jsonb), infrx.control_op_cancel(jsonb), infrx.control_op_get(jsonb), infrx.control_op_pending(jsonb), infrx.control_op_doc(infrx.control_operations), infrx.control_op_row(jsonb), infrx.control_owner(jsonb);
-- rollback: drop table if exists infrx.control_idempotency, infrx.control_operations;

create table if not exists infrx.control_operations (
  operation_id uuid primary key,
  kind text not null check (kind ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$'),
  state text not null default 'queued'
    check (state in ('queued', 'running', 'succeeded', 'failed', 'cancel_requested',
                     'cancelled')),
  phase text,
  resource_kind text,
  resource_id text,
  -- R270 `Actor` as the server established it (audience + ids), never a request field
  actor jsonb not null
    check (actor->>'audience' in ('consumer', 'operator', 'provider_dev', 'session')),
  requested_input_hash text not null,      -- checked on its control_idempotency row
  lease_owner text,
  lease_until timestamptz,
  fence bigint not null default 0,
  retry_after_s int check (retry_after_s >= 0),
  error jsonb,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  cancel_requested_at timestamptz,
  constraint control_operations_error_iff_failed check ((state = 'failed') = (error is not null))
);
create index if not exists control_operations_pending_idx on infrx.control_operations (created_at)
  where state in ('queued', 'running', 'cancel_requested');

create table if not exists infrx.control_idempotency (
  scope text not null,                      -- infrx.control_owner(actor) || '/' || kind
  key text not null check (length(key) between 1 and 255),
  input_hash text not null check (input_hash ~ '^sha256:[0-9a-f]{64}$'),
  operation_id uuid not null references infrx.control_operations (operation_id)
    deferrable initially deferred,
  outcome jsonb,
  created_at timestamptz not null,
  expires_at timestamptz not null,
  primary key (scope, key),
  -- contracts.md §2: a control-operation receipt stays answerable for at least 24 h
  constraint control_idempotency_retained_a_day check (expires_at >= created_at + interval '24 hours')
);

-- ================================================================== helpers ===
-- The tenant that owns an actor's operations: its provider workspace, else its organization,
-- else its user. Null: an actor that names no tenant (start refuses it).
create or replace function infrx.control_owner(p_actor jsonb) returns text
language sql immutable set search_path = infrx, public, pg_temp as $$
  select case when p_actor->>'provider_org_id' is not null then 'provider:' || (p_actor->>'provider_org_id')
              when p_actor->>'org_id' is not null then 'org:' || (p_actor->>'org_id')
              when p_actor->>'user_id' is not null then 'user:' || (p_actor->>'user_id') end;
$$;

-- The stored row as the port reads it: R270's OperationDoc fields plus the lease, the actor
-- and the cancellation instant; instants as RFC 3339 UTC whatever the session time zone.
create or replace function infrx.control_op_doc(r infrx.control_operations) returns jsonb
language sql stable set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object(
    'operation_id', r.operation_id, 'kind', r.kind, 'state', r.state, 'phase', r.phase,
    'resource_kind', r.resource_kind, 'resource_id', r.resource_id, 'actor', r.actor,
    'created_at', to_char(r.created_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    'updated_at', to_char(r.updated_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    'retry_after_s', r.retry_after_s, 'error', r.error, 'fence', r.fence,
    'lease_owner', r.lease_owner,
    'lease_until', to_char(r.lease_until at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    'cancel_requested_at',
      to_char(r.cancel_requested_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
$$;

-- The row `p_args->>'operation_id'` names, locked for the caller's update; not_found when the
-- id is absent or not a uuid (a path parameter is the caller's text, never a 500).
create or replace function infrx.control_op_row(p_args jsonb) returns infrx.control_operations
language plpgsql set search_path = infrx, public, pg_temp as $$
declare
  r infrx.control_operations;
begin
  begin
    select * into r from infrx.control_operations
     where operation_id = (p_args->>'operation_id')::uuid for update;
  exception when invalid_text_representation then
    r := null;
  end;
  if r.operation_id is null then
    perform infrx.refuse('not_found', 'no such operation');
  end if;
  return r;
end $$;

-- ================================================================ boundary ===
-- {kind, actor, idempotency_key, input_hash, resource_kind?, resource_id?, outcome?,
--  retention_s (>= 86400)} -> {operation, replayed, outcome}. An actor that names no tenant
-- has no scope (null): refused with the other unstorable starts.
create or replace function infrx.control_op_start(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_now timestamptz := infrx.now();
  v_owner text := infrx.control_owner(p_args->'actor');
  v_scope text := v_owner || '/' || (p_args->>'kind');
  v_id uuid := gen_random_uuid();
  i infrx.control_idempotency;
  r infrx.control_operations;
begin
  begin
    insert into infrx.control_idempotency (scope, key, input_hash, operation_id, outcome,
                                           created_at, expires_at)
    values (v_scope, p_args->>'idempotency_key', p_args->>'input_hash', v_id,
            nullif(p_args->'outcome', 'null'),
            v_now, v_now + make_interval(secs => (p_args->>'retention_s')::int))
    on conflict (scope, key) do nothing;
    if found then
      insert into infrx.control_operations (operation_id, kind, resource_kind, resource_id, actor,
                                            requested_input_hash, created_at, updated_at)
      values (v_id, p_args->>'kind', p_args->>'resource_kind', p_args->>'resource_id',
              p_args->'actor', p_args->>'input_hash', v_now, v_now)
      returning * into r;
      return jsonb_build_object('operation', infrx.control_op_doc(r), 'replayed', false,
                                'outcome', nullif(p_args->'outcome', 'null'));
    end if;
  exception when check_violation or not_null_violation then
    perform infrx.refuse('invalid_request', 'not a startable operation: kind, actor (with a '
                         'tenant), key, input hash or retention');
  end;
  select * into i from infrx.control_idempotency where scope = v_scope
     and key = p_args->>'idempotency_key';
  if i.expires_at <= v_now then
    perform infrx.refuse('idempotency_expired', 'this Idempotency-Key has expired');
  end if;
  if i.input_hash <> p_args->>'input_hash' then
    perform infrx.refuse('idempotency_conflict',
                         'this Idempotency-Key was used with a different request');
  end if;
  select * into r from infrx.control_operations where operation_id = i.operation_id;
  return jsonb_build_object('operation', infrx.control_op_doc(r), 'replayed', true,
                            'outcome', i.outcome);
end $$;

-- {operation_id, owner, ttl_s (1..3600)} -> operation. A free or expired lease is granted
-- with the next fence (queued -> running; cancel_requested stays, so the worker reconciles);
-- the holder renews its own live lease under the same fence; anyone else is refused.
create or replace function infrx.control_op_lease(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_now timestamptz := infrx.now();
  v_owner text := p_args->>'owner';
  v_ttl int := (p_args->>'ttl_s')::int;
  r infrx.control_operations := infrx.control_op_row(p_args);
begin
  if v_owner is null or length(v_owner) not between 1 and 128 or v_ttl is null
     or v_ttl not between 1 and 3600 then
    perform infrx.refuse('invalid_request', 'a lease needs an owner and a ttl of 1-3600 s');
  end if;
  if r.state in ('succeeded', 'failed', 'cancelled') then
    perform infrx.refuse('state_conflict', 'the operation is finished');
  end if;
  if r.lease_until > v_now and r.lease_owner = v_owner then
    update infrx.control_operations set lease_until = v_now + make_interval(secs => v_ttl),
           updated_at = v_now
     where operation_id = r.operation_id returning * into r;
  elsif r.lease_until > v_now then
    perform infrx.refuse('state_conflict', 'the operation is leased by another worker');
  else
    update infrx.control_operations set lease_owner = v_owner, fence = fence + 1,
           lease_until = v_now + make_interval(secs => v_ttl), updated_at = v_now,
           state = case when state = 'queued' then 'running' else state end
     where operation_id = r.operation_id returning * into r;
  end if;
  return infrx.control_op_doc(r);
end $$;

-- {operation_id, fence, phase, retry_after_s?} -> operation. Only the live lease's fence.
create or replace function infrx.control_op_advance(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_now timestamptz := infrx.now();
  r infrx.control_operations := infrx.control_op_row(p_args);
begin
  if r.fence is distinct from (p_args->>'fence')::bigint or r.lease_until is null
     or r.lease_until <= v_now then
    perform infrx.refuse('state_conflict', 'stale fence: the lease was lost');
  end if;
  begin
    update infrx.control_operations set phase = p_args->>'phase', updated_at = v_now,
           retry_after_s = (p_args->>'retry_after_s')::int
     where operation_id = r.operation_id returning * into r;
  exception when check_violation then
    perform infrx.refuse('invalid_request', 'not a phase or retry interval');
  end;
  return infrx.control_op_doc(r);
end $$;

-- {operation_id, fence, state (succeeded|failed|cancelled), error? (iff failed)} -> operation
create or replace function infrx.control_op_finish(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_now timestamptz := infrx.now();
  r infrx.control_operations := infrx.control_op_row(p_args);
begin
  if coalesce(p_args->>'state', '') not in ('succeeded', 'failed', 'cancelled') then
    perform infrx.refuse('invalid_request', 'an operation finishes succeeded, failed or cancelled');
  end if;
  if r.fence is distinct from (p_args->>'fence')::bigint or r.lease_until is null
     or r.lease_until <= v_now then
    perform infrx.refuse('state_conflict', 'stale fence: the lease was lost before finishing');
  end if;
  begin
    update infrx.control_operations set state = p_args->>'state', error = nullif(p_args->'error', 'null'),
           lease_owner = null, lease_until = null, updated_at = v_now
     where operation_id = r.operation_id returning * into r;
  exception when check_violation then
    perform infrx.refuse('invalid_request', 'a failed operation carries an error, no other does');
  end;
  return infrx.control_op_doc(r);
end $$;

-- {operation_id, actor} -> operation. queued -> cancelled (no worker holds it); running ->
-- cancel_requested (the lease holder reconciles and finishes); repeated cancels answer the
-- row; a succeeded or failed operation is a conflict. Another tenant's operation: not_found.
create or replace function infrx.control_op_cancel(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_now timestamptz := infrx.now();
  r infrx.control_operations := infrx.control_op_row(p_args);
begin
  if not (coalesce((p_args->'actor'->>'operator')::boolean, false)
          or infrx.control_owner(r.actor) = infrx.control_owner(p_args->'actor')) then
    perform infrx.refuse('not_found', 'no such operation');
  end if;
  if r.state in ('succeeded', 'failed') then
    perform infrx.refuse('state_conflict', 'the operation already finished');
  end if;
  if r.state in ('queued', 'running') then
    update infrx.control_operations
       set state = case when state = 'queued' then 'cancelled' else 'cancel_requested' end,
           cancel_requested_at = v_now, updated_at = v_now
     where operation_id = r.operation_id returning * into r;
  end if;
  return infrx.control_op_doc(r);
end $$;

-- {operation_id, actor} -> operation, for its owning tenant or an operator; else not_found.
create or replace function infrx.control_op_get(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  r infrx.control_operations;
begin
  begin
    select * into r from infrx.control_operations
     where operation_id = (p_args->>'operation_id')::uuid;
  exception when invalid_text_representation then
    r := null;
  end;
  if r.operation_id is null
     or not (coalesce((p_args->'actor'->>'operator')::boolean, false)
             or infrx.control_owner(r.actor) = infrx.control_owner(p_args->'actor')) then
    perform infrx.refuse('not_found', 'no such operation');
  end if;
  return infrx.control_op_doc(r);
end $$;

-- {kinds: [..], limit (clamped to 1..100)} -> [operation_id, ...]: the oldest
-- unfinished operations of those kinds that no live lease holds (queued, a dead worker's,
-- or cancel_requested awaiting reconciliation).
create or replace function infrx.control_op_pending(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(o.operation_id order by o.created_at, o.operation_id), '[]')
    from (select operation_id, created_at from infrx.control_operations
           where state in ('queued', 'running', 'cancel_requested')
             and kind in (select jsonb_array_elements_text(p_args->'kinds'))
             and (lease_until is null or lease_until <= infrx.now())
           order by created_at, operation_id
           limit least(greatest((p_args->>'limit')::int, 1), 100)) o;
$$;

-- ============================================================== privileges ===
do $$
declare
  t text;
begin
  foreach t in array array['control_operations', 'control_idempotency'] loop
    execute format('alter table infrx.%I enable row level security', t);
    execute format('revoke all on infrx.%I from public, anon, authenticated, service_role', t);
    execute format('grant select on infrx.%I to service_role', t);
  end loop;
end $$;
revoke all on function infrx.control_owner(jsonb), infrx.control_op_doc(infrx.control_operations),
  infrx.control_op_row(jsonb) from public, anon, authenticated, service_role;
-- (the boundary functions start with no browser grant: 0004's default privileges)
grant execute on function infrx.control_op_start(jsonb), infrx.control_op_lease(jsonb),
  infrx.control_op_advance(jsonb), infrx.control_op_finish(jsonb), infrx.control_op_cancel(jsonb),
  infrx.control_op_get(jsonb), infrx.control_op_pending(jsonb)
  to service_role, infrx_lab_control;
