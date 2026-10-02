-- LOCAL-ONLY (R151/R201/R271): never applied hosted; 0068 is allocated to api-schema-3 (AP-00,
-- wave 7 batch 3; register rows 91 and 94). The batch's late schema requests and the E4C
-- run-2 database findings, in one file:
--
--   SR-AP06-1 (api-publication, evidence/w7/api-publication-5be6dbb.md): the control login's
--     dev-credential reads and revocation (SECURITY DEFINER, 0004's service_role default +
--     EXECUTE to infrx_lab_control only):
--     infrx.lab_control_dev_keys {provider_org_id, endpoint_id} -> [{key_id, endpoint_id, name,
--       prefix, created_at, revoked_at}]: the keys of the provider's own DEV endpoint, oldest
--       first (never the hash; 0009's check makes every key naming a provider and endpoint a
--       provider_dev key); another provider's or a prod endpoint is `not_found` (0032's
--       lab_control_dev_key refusal).
--     infrx.lab_control_revoke_dev_key {provider_org_id, endpoint_id, key_id, actor,
--       idempotency_key} -> the key as listed: one-way through 0009's revoke_key (its
--       admin_key_revoke audit entry) and a `lab_dev_key_revoke` control event, both once; a
--       revoked key answers its first revoked_at again; a key outside that endpoint is
--       `not_found`. The audit entry's idempotency key is scoped to the key
--       (`lab_dev_key_revoke:<key id>:<key>`): 0009's unique index is global, and two tenants
--       may send the same Idempotency-Key. 0032's action check admits `lab_dev_key_revoke`.
--     infrx.lab_control_dev_wallet {provider_org_id} -> {opened, balance}: the provider_dev
--       wallet's available CREDIT (ledger - reserved) as exact text (`0.00000000` and
--       opened=false before the first allocation; the request said numeric - text keeps it
--       exact across JSON, as job_admission's money does).
--   SR-AP10C-1 (api-improve-2, evidence/w7/api-improve-2-837cbb2.md): the datasets worker's own
--     role, `infrx_lab_datasets` (WR-LDP-7's per-role shape; 0021's dedicated logins: NOLOGIN
--     here, the operator runs `alter role infrx_lab_datasets login password '<secret>'` from
--     the secret store and points the role's LAB_DATABASE_URL at it on the transaction pooler;
--     a member of no role). Not the control login: R251 keeps the worker-only sample bounds,
--     tombstones and import claim off it, and the pass also needs D6F's cross-org feedback
--     read. EXECUTE on exactly what the role's three passes call (measured with
--     track_functions over the from-traces, import and lineage passes): 0060's worker doors
--     (pending/lease/advance/finish), L2's memberships and grants, D7's source/publish/
--     resolve/accessible samples, 0051's claim side, 0041's sample restrictions and content
--     refs (issue, redeem), D6F's request_feedback and infrx.now(); SELECT on
--     serving_versions(serving_version_id, model_id) under a policy of its own (WR-N3-4's
--     serving -> model read; pg_ports.model_of unchanged).
--   Register row 94 (E4C run 2 on e6a8b40a, evidence/e/E4C-e6a8b40/README.md "The soak's
--     503s"): the two per-tick statements that read every `infrx.jobs` row -
--     jobs_content_unscrubbed_idx: 0020's content-scrub sweep (register_existing_database_
--       content's job branch, `request_record is not null and content_scrubbed_at is null`)
--       planned as a full scan of jobs (134 ms idle on the hosted instance); this partial index
--       holds exactly the rows it selects (a job leaves it when scrubbed).
--     infrx.journal_bytes_charged(): 0011's body OR-joins jobs with capacity_reservations
--       (`r.request_id is not null or j.journal_stored_bytes > 0`), which no index serves, on
--       every admission and readiness probe. Same answer as two aggregates, each on an index:
--       greatest(reserved, stored) = stored + greatest(reserved - stored, 0), so the stored
--       bytes of every job (0011's jobs_journal_stored_idx) plus each live journal
--       reservation's excess over its job's stored bytes (0003's capacity_reservations_active_
--       idx; (request_id, kind) is the key, so at most one per job). A reservation with no job
--       still counts nothing. `create or replace` keeps 0011's grants (nobody: a definer body
--       calls it). The index is a plain `create index` (a migration runs in a transaction): it
--       holds jobs' SHARE lock while it builds - milliseconds at the hosted 13k rows.
--
-- ROLLBACK (this file alone; nothing earlier references it; tests/d/test_control_ops_upgrade.py runs these lines):
-- rollback: drop function if exists infrx.lab_control_dev_keys(jsonb), infrx.lab_control_revoke_dev_key(jsonb), infrx.lab_control_dev_wallet(jsonb);
-- rollback: alter table infrx.lab_control_events drop constraint if exists lab_control_events_action_check, add constraint lab_control_events_action_check check (action in ('lab_transition', 'lab_propose', 'lab_dev_key', 'lab_publish', 'lab_rollback', 'lab_fund'));
-- rollback: drop policy if exists lab_datasets_serving_models on infrx.serving_versions;
-- rollback: revoke select (serving_version_id, model_id) on infrx.serving_versions from infrx_lab_datasets;
-- rollback: revoke execute on function infrx.now(), infrx.control_op_pending(jsonb), infrx.control_op_lease(jsonb), infrx.control_op_advance(jsonb), infrx.control_op_finish(jsonb), infrx.lab_provider_memberships(jsonb), infrx.lab_access_grants(jsonb), infrx.lab_register_source(jsonb), infrx.lab_publish(jsonb), infrx.lab_resolve(jsonb), infrx.lab_accessible_samples(jsonb), infrx.lab_import_job_claim(jsonb), infrx.lab_import_job_heartbeat(jsonb), infrx.lab_import_job_finish(jsonb), infrx.lab_import_job(jsonb), infrx.lab_tombstone_samples(jsonb), infrx.lab_bound_samples(jsonb), infrx.lab_blocked_samples(jsonb), infrx.lab_permitted_samples(jsonb), infrx.lab_content_ref_issue(jsonb), infrx.lab_content_ref_redeem(jsonb), infrx.request_feedback(jsonb) from infrx_lab_datasets;
-- rollback: revoke usage on schema infrx from infrx_lab_datasets;
-- rollback: drop index if exists infrx.jobs_content_unscrubbed_idx;
-- rollback: create or replace function infrx.journal_bytes_charged() returns bigint language sql stable security definer set search_path = infrx, public, pg_temp as $$ select coalesce(sum(greatest(coalesce(r.amount, 0), j.journal_stored_bytes)), 0)::bigint from infrx.jobs j left join infrx.capacity_reservations r on r.request_id = j.request_id and r.kind = 'journal_bytes' and r.active where r.request_id is not null or j.journal_stored_bytes > 0; $$;
-- (The role itself stays: a role is the cluster's, and with no grant it reaches nothing. The
-- restored action check refuses while a `lab_dev_key_revoke` event exists - control events are
-- history, never deleted - so roll back only before the first dev-key revocation.)

-- ================================================================ SR-AP06-1 ===
alter table infrx.lab_control_events drop constraint if exists lab_control_events_action_check,
  add constraint lab_control_events_action_check check (action in ('lab_transition',
    'lab_propose', 'lab_dev_key', 'lab_publish', 'lab_rollback', 'lab_fund', 'lab_dev_key_revoke'));

create or replace function infrx.lab_control_dev_keys(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
begin
  if not exists (select 1 from infrx.endpoints where endpoint_id = (p_args->>'endpoint_id')::uuid
     and provider_org_id = (p_args->>'provider_org_id')::uuid and environment = 'dev') then
    perform infrx.refuse('not_found', 'no such dev endpoint for this provider');
  end if;
  return (select coalesce(jsonb_agg(jsonb_build_object('key_id', k.id,
      'endpoint_id', k.endpoint_id, 'name', k.name, 'prefix', k.prefix, 'created_at',
      k.created_at, 'revoked_at', k.revoked_at) order by k.created_at, k.id), '[]')
    from public.api_keys k
   where k.provider_org_id = (p_args->>'provider_org_id')::uuid
     and k.endpoint_id = (p_args->>'endpoint_id')::uuid);
end $$;

create or replace function infrx.lab_control_revoke_dev_key(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  k public.api_keys%rowtype;
  v_at timestamptz;
begin
  select * into k from public.api_keys a where a.id = (p_args->>'key_id')::uuid
     and a.provider_org_id = (p_args->>'provider_org_id')::uuid
     and a.endpoint_id = (p_args->>'endpoint_id')::uuid
   for update;
  if not found then
    perform infrx.refuse('not_found', 'no such dev credential on this endpoint');
  end if;
  v_at := infrx.revoke_key(k.id, p_args->>'actor', 'lab_dev_key_revoke',
    'lab_dev_key_revoke:' || k.id || ':' || (p_args->>'idempotency_key'));
  if k.revoked_at is null then
    perform infrx.lab_control_audit(k.provider_org_id, 'lab_dev_key_revoke', p_args->>'actor',
      k.id::text, jsonb_build_object('endpoint_id', k.endpoint_id, 'revoked_at', v_at));
  end if;
  return jsonb_build_object('key_id', k.id, 'endpoint_id', k.endpoint_id, 'name', k.name,
    'prefix', k.prefix, 'created_at', k.created_at, 'revoked_at', v_at);
end $$;

create or replace function infrx.lab_control_dev_wallet(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('opened', w.wallet_id is not null,
                            'balance', coalesce(w.available, 0)::numeric(20,8)::text)
  from (select 1) one
  left join infrx.credit_wallets w
    on w.kind = 'provider_dev' and w.owner_provider_org_id = (p_args->>'provider_org_id')::uuid
$$;

revoke all on function infrx.lab_control_dev_keys(jsonb), infrx.lab_control_revoke_dev_key(jsonb),
  infrx.lab_control_dev_wallet(jsonb) from public, anon, authenticated;
grant execute on function infrx.lab_control_dev_keys(jsonb), infrx.lab_control_revoke_dev_key(jsonb),
  infrx.lab_control_dev_wallet(jsonb) to infrx_lab_control;

-- =============================================================== SR-AP10C-1 ===
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'infrx_lab_datasets') then
    create role infrx_lab_datasets nologin noinherit;
  end if;
end $$;
grant usage on schema infrx to infrx_lab_datasets;
grant execute on function infrx.now(),
  -- 0060's worker doors (the trace -> dataset operation)
  infrx.control_op_pending(jsonb), infrx.control_op_lease(jsonb),
  infrx.control_op_advance(jsonb), infrx.control_op_finish(jsonb),
  -- L2 (LabAccess) and D7 (PgLabDataStore)
  infrx.lab_provider_memberships(jsonb), infrx.lab_access_grants(jsonb),
  infrx.lab_register_source(jsonb), infrx.lab_publish(jsonb), infrx.lab_resolve(jsonb),
  infrx.lab_accessible_samples(jsonb),
  -- 0051's import queue, the worker's side
  infrx.lab_import_job_claim(jsonb), infrx.lab_import_job_heartbeat(jsonb),
  infrx.lab_import_job_finish(jsonb), infrx.lab_import_job(jsonb),
  -- 0041: sample restrictions and C2's content refs
  infrx.lab_tombstone_samples(jsonb), infrx.lab_bound_samples(jsonb),
  infrx.lab_blocked_samples(jsonb), infrx.lab_permitted_samples(jsonb),
  infrx.lab_content_ref_issue(jsonb), infrx.lab_content_ref_redeem(jsonb),
  -- D6F: the grantor's own feedback rows of a selected request
  infrx.request_feedback(jsonb)
  to infrx_lab_datasets;
grant select (serving_version_id, model_id) on infrx.serving_versions to infrx_lab_datasets;
drop policy if exists lab_datasets_serving_models on infrx.serving_versions;
create policy lab_datasets_serving_models on infrx.serving_versions for select
  to infrx_lab_datasets using (true);

-- ================================================== E4C run 2 (register row 94) ===
create index if not exists jobs_content_unscrubbed_idx on infrx.jobs (request_id)
  where request_record is not null and content_scrubbed_at is null;

create or replace function infrx.journal_bytes_charged() returns bigint
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select ((select coalesce(sum(j.journal_stored_bytes), 0) from infrx.jobs j
            where j.journal_stored_bytes > 0)
        + (select coalesce(sum(greatest(r.amount - j.journal_stored_bytes, 0)), 0)
             from infrx.capacity_reservations r
             join infrx.jobs j on j.request_id = r.request_id
            where r.kind = 'journal_bytes' and r.active))::bigint;
$$;
