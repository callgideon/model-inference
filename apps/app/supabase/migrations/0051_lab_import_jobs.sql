-- WR-N4-3 (lane lab-sql-lw6; COMPOSITION-2-b790f17.md "requests filed", the N4/I5 request): a
-- durable Lab import-job queue, so `LAB_DATASETS` imports run in the datasets pool (I5)
-- instead of as `asyncio.create_task`s of the gateway process (a restart loses every
-- in-flight import; the gateway's own crash budget is not N1's).
-- LOCAL-ONLY (R151/R201): never applied hosted; the number is the next free at merge.
--
-- The bundle itself is already durable (N1's write-once chunks under
-- `lab/<provider>/imports/<import id>/`); what was missing is a durable WORK ITEM saying
-- "run this import spec against that bundle", claimable by a pool of I5 worker processes
-- instead of living only in one gateway request's memory. This is a lease queue (D2/D7's
-- outbox pattern: claim, timeout, redeliver), not a pub/sub outbox - exactly one worker owns
-- a job at a time, and its terminal state is the answer `GET imports/{id}` reads.
--
--   lab_import_jobs   one row per import id: the provider, N1's import spec (the same body
--                     `POST imports` validates before this - immutable once accepted), state
--                     (queued -> running -> succeeded | failed, `running` re-claimable after
--                     a lease timeout, exactly the outbox's `claimed_at`/`claimed_by`/
--                     `attempts`), the terminal result or error, created/updated.
--
-- Named RPCs (SECURITY DEFINER; EXECUTE service_role only through 0004's defaults):
--   lab_import_job_enqueue {provider_org_id, job_id, spec, actor}: insert `queued`; a replay
--       of the SAME job_id (the route's own idempotency key, N1's import id) answers the
--       existing row UNCHANGED - never a second job, whatever state it is in now; another
--       provider's job_id is `not_found`; a malformed spec is `invalid_request`.
--   lab_import_job_claim {worker_id, limit, redelivery_s}: up to `limit` jobs `queued`, or
--       `running` whose lease expired (`updated_at` older than `redelivery_s`), oldest
--       first -> `running`, leased to `worker_id`, `attempts` bumped. Mirrors
--       `lab_outbox_pending`'s claim exactly (D2's pattern), because a killed I5 worker's
--       job must be redelivered the same way a killed relay's event is.
--   lab_import_job_heartbeat {job_id, worker_id}: touches the lease (`updated_at`) so a long
--       import is not reclaimed out from under its own worker; refused (`state_conflict`)
--       for a job this worker does not hold.
--   lab_import_job_finish {job_id, worker_id, state: succeeded|failed, result?, error?}:
--       `running` (held by `worker_id`) -> the terminal state, once; a repeat of the SAME
--       terminal state (a retried ack) answers the stored row; held by another worker, or
--       already terminal at a DIFFERENT state, is `state_conflict`.
--   lab_import_job {provider_org_id, job_id}: the row, or `not_found` for another provider's
--       or an unknown id (`GET imports/{id}`'s read).
--
-- ROLLBACK (this file alone; nothing references it): drop function
--   infrx.lab_import_job(jsonb), infrx.lab_import_job_finish(jsonb),
--   infrx.lab_import_job_heartbeat(jsonb), infrx.lab_import_job_claim(jsonb),
--   infrx.lab_import_job_enqueue(jsonb), infrx.lab_import_job_json(infrx.lab_import_jobs);
--   drop table infrx.lab_import_jobs.
--
-- Re-runnable: `if not exists`, `create or replace`.

create table if not exists infrx.lab_import_jobs (
  job_id uuid primary key,
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  spec jsonb not null check (jsonb_typeof(spec) = 'object'),
  state text not null default 'queued' check (state in ('queued', 'running', 'succeeded',
    'failed')),
  claimed_at timestamptz,
  claimed_by text,
  attempts int not null default 0 check (attempts >= 0),
  result jsonb,
  error text check (length(error) <= 4000),
  created_by text not null check (length(btrim(created_by)) between 1 and 200),
  created_at timestamptz not null default infrx.now(),
  updated_at timestamptz not null default infrx.now(),
  constraint lab_import_jobs_succeeded_has_result
    check (state <> 'succeeded' or result is not null),
  constraint lab_import_jobs_failed_has_error check (state <> 'failed' or error is not null)
);
create index if not exists lab_import_jobs_claimable
  on infrx.lab_import_jobs (updated_at) where state in ('queued', 'running');

do $$
begin
  execute 'create or replace trigger lab_import_jobs_no_truncate before truncate on '
          'infrx.lab_import_jobs for each statement execute function infrx.forbid_truncate()';
  execute 'alter table infrx.lab_import_jobs enable row level security';
  execute 'revoke all on infrx.lab_import_jobs from public, anon, authenticated, service_role';
  execute 'grant select on infrx.lab_import_jobs to service_role';
end $$;

create or replace function infrx.lab_import_job_json(j infrx.lab_import_jobs) returns jsonb
language sql immutable set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('job_id', j.job_id, 'provider_org_id', j.provider_org_id,
    'spec', j.spec, 'state', j.state, 'attempts', j.attempts, 'result', j.result,
    'error', j.error, 'created_at', j.created_at, 'updated_at', j.updated_at)
$$;

create or replace function infrx.lab_import_job_enqueue(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_id uuid := (p_args->>'job_id')::uuid;
  v_provider uuid := (p_args->>'provider_org_id')::uuid;
  j infrx.lab_import_jobs%rowtype;
begin
  select * into j from infrx.lab_import_jobs where job_id = v_id;
  if found then
    if j.provider_org_id <> v_provider then
      perform infrx.refuse('not_found', 'no such import job for this provider');
    end if;
    return infrx.lab_import_job_json(j);                -- a replay: the one job, unchanged
  end if;
  begin
    insert into infrx.lab_import_jobs (job_id, provider_org_id, spec, created_by)
    values (v_id, v_provider, p_args->'spec', p_args->>'actor')
    returning * into j;
  exception when check_violation or not_null_violation or invalid_text_representation then
    perform infrx.refuse('invalid_request', 'an import job names its provider, an id, an '
                         'object spec and a named actor');
  end;
  return infrx.lab_import_job_json(j);
end $$;

create or replace function infrx.lab_import_job_claim(p_args jsonb) returns jsonb
language sql security definer set search_path = infrx, public, pg_temp as $$
  with picked as (
    select j.job_id from infrx.lab_import_jobs j
     where j.state = 'queued'
        or (j.state = 'running' and j.updated_at
            <= infrx.now() - make_interval(secs => (p_args->>'redelivery_s')::float8))
     order by j.updated_at, j.job_id limit (p_args->>'limit')::int
     for update skip locked),
  claimed as (
    update infrx.lab_import_jobs j set state = 'running', claimed_at = infrx.now(),
      claimed_by = p_args->>'worker_id', attempts = j.attempts + 1, updated_at = infrx.now()
      from picked where j.job_id = picked.job_id
    returning j.*)
  select coalesce(jsonb_agg(infrx.lab_import_job_json(claimed) order by claimed.updated_at,
                            claimed.job_id), '[]')
    from claimed
$$;

create or replace function infrx.lab_import_job_heartbeat(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  j infrx.lab_import_jobs%rowtype;
begin
  update infrx.lab_import_jobs set updated_at = infrx.now()
   where job_id = (p_args->>'job_id')::uuid and state = 'running'
     and claimed_by = p_args->>'worker_id'
  returning * into j;
  if not found then
    perform infrx.refuse('state_conflict', 'this worker does not hold that job');
  end if;
  return infrx.lab_import_job_json(j);
end $$;

create or replace function infrx.lab_import_job_finish(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_to text := p_args->>'state';
  j infrx.lab_import_jobs%rowtype;
begin
  if v_to not in ('succeeded', 'failed') then
    perform infrx.refuse('invalid_request', 'a finish is succeeded or failed');
  end if;
  select * into j from infrx.lab_import_jobs where job_id = (p_args->>'job_id')::uuid
  for update;
  if not found then
    perform infrx.refuse('not_found', 'no such import job');
  end if;
  if j.state = v_to then
    return infrx.lab_import_job_json(j);                 -- a retried ack: already there
  end if;
  if j.state <> 'running' or j.claimed_by <> p_args->>'worker_id' then
    perform infrx.refuse('state_conflict', 'this worker does not hold that job, or it is '
                         'already ' || j.state);
  end if;
  begin
    update infrx.lab_import_jobs set state = v_to, result = p_args->'result',
      error = p_args->>'error', updated_at = infrx.now()
     where job_id = j.job_id returning * into j;
  exception when check_violation then
    perform infrx.refuse('invalid_request', 'succeeded names a result, failed names an error');
  end;
  return infrx.lab_import_job_json(j);
end $$;

create or replace function infrx.lab_import_job(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  j infrx.lab_import_jobs%rowtype;
begin
  select * into j from infrx.lab_import_jobs where job_id = (p_args->>'job_id')::uuid
     and provider_org_id = (p_args->>'provider_org_id')::uuid;
  if not found then
    perform infrx.refuse('not_found', 'no such import job for this provider');
  end if;
  return infrx.lab_import_job_json(j);
end $$;
