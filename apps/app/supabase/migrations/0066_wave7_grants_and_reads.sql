-- AP-00 00d remainder (lane api-schema-2, wave 7 batch 2, R271): the batch's open schema
-- requests in one LOCAL-ONLY file - reads and grants only, no table.
--
--   SR-AP10-1 (api-improve, evidence/w7/api-improve-47ab6e9.md)
--     (a) infrx.lab_eval_catalog {provider_org_id} -> {datasets [{ref, label}], harnesses
--         [{ref, harness_id, version, adapter}], evaluators [{ref, label}], servings [{ref,
--         label}]}: the provider's own dataset/harness records (0029), its evaluators (0034)
--         and its READY PRIVATE DEV deployment revisions by serving ref (0045) - what
--         `/lab/v1/evaluations` checks a launch against. Labels: dataset `<id> v<version>`,
--         evaluator its metric, serving its revision label.
--     (b) infrx.lab_experiments (0043) re-created with `baseline_run_ref`/`candidate_run_ref`:
--         an experiment stored before `freeze` created its runs is otherwise unreadable.
--   SR-AP10-2 (register row 15, WR-LAB2-4): infrx.lab_external_runs_of {provider_org_id} ->
--     [{external_run_id, doc}] and infrx.lab_checkpoint_receipts_of {provider_org_id} ->
--     [{checkpoint_id, external_run_ref, artifact_digest, state, reason}] (P3's
--     `checkpoint:<id>` note's state and reason over the receipt's), oldest first.
--   SR-AP10-3: EXECUTE for the Lab control login on 0043/0034/0042's six route reads the
--     unit lacked and on the three reads above (R251: the pin in
--     tests/d/test_code_mutants_lw8.py moves with it - wiring request).
--   AP-07 (api-traces, evidence/w7/api-traces-c75f85b.md): 0027's `lab_grant_version`
--     refuses a suspended organization's every write, its withdrawal too. A revoke-only
--     door, infrx.lab_withdraw_access_grant {actor_user_id, grantor_org_id,
--     recipient_provider_org_id}: the grantor's owner, suspended or not, revokes the current
--     grant (the next version, revoked now; 0027's revoke otherwise). 0027 is unchanged:
--     a suspended organization still grants and re-scopes nothing. Platform role only.
--   api-artifacts (evidence/w7/api-artifacts-dc343a4.md) on 0060: `cancel` of a finished
--     operation answers it as it is (Google-LRO style; 0060 refused succeeded/failed with
--     state_conflict), so a cancel racing a worker's finish is never an error;
--     infrx.control_op_cancel is re-created without that refusal. Its other names are
--     0060's already: `get` returns the actor (audience, ids) and the resource; the worker
--     login is the control login (no per-role Lab login exists yet, WR-LDP-7), which 0060
--     grants all seven doors.
--   Not here, by the decision recorded in evidence/w7/api-actions-75b8d25.md and
--     WR-AP03-2's settings comment: AP-03's conditional `infrx_runtime` grants - the console
--     action and read pools log in as a role that may `set role` (never infrx_runtime).
-- Privileges: the new functions are SECURITY DEFINER with 0004's defaults (service_role
-- only); the control login gains EXECUTE on the nine reads; no browser role, infrx_runtime
-- or infrx_monitor gains anything.
-- LOCAL-ONLY (R151/R201/R271): never applied hosted; 0066 is allocated to api-schema-2.
--
-- ROLLBACK (this file alone; nothing earlier references it; tests/d/test_control_ops_upgrade.py runs these lines):
-- rollback: revoke execute on function infrx.lab_put_experiment(jsonb), infrx.lab_checkpoint_listing(jsonb), infrx.lab_list_datasets(jsonb), infrx.lab_evaluator(jsonb), infrx.lab_checkpoint_subscribe(jsonb), infrx.lab_checkpoint_decisions(jsonb) from infrx_lab_control;
-- rollback: drop function if exists infrx.lab_eval_catalog(jsonb), infrx.lab_external_runs_of(jsonb), infrx.lab_checkpoint_receipts_of(jsonb), infrx.lab_withdraw_access_grant(jsonb);
-- rollback: create or replace function infrx.lab_experiments(p_args jsonb) returns jsonb language sql stable security definer set search_path = infrx, public, pg_temp as $$ select coalesce(jsonb_agg(jsonb_build_object( 'experiment_id', e.experiment_id, 'created_at', e.created_at, 'protocol', e.protocol, 'protocol_digest', e.protocol_digest, 'baseline', (select infrx.lab_run_json(r.run_id) from infrx.lab_eval_runs r where r.run_ref = e.baseline_run_ref), 'candidate', (select infrx.lab_run_json(r.run_id) from infrx.lab_eval_runs r where r.run_ref = e.candidate_run_ref), 'report', (select jsonb_build_object('report_digest', p.report_digest, 'body', p.body) from infrx.lab_eval_reports p where p.provider_org_id = e.provider_org_id and p.baseline_run_ref = e.baseline_run_ref and p.candidate_run_ref = e.candidate_run_ref and p.protocol_digest = e.protocol_digest order by p.stored_at desc, p.report_digest limit 1)) order by e.created_at desc, e.experiment_id), '[]') from infrx.lab_experiments e where e.provider_org_id::text = p_args->>'provider_org_id' $$;
-- rollback: create or replace function infrx.control_op_cancel(p_args jsonb) returns jsonb language plpgsql security definer set search_path = infrx, public, pg_temp as $$ declare v_now timestamptz := infrx.now(); r infrx.control_operations := infrx.control_op_row(p_args); begin if not (coalesce((p_args->'actor'->>'operator')::boolean, false) or infrx.control_owner(r.actor) = infrx.control_owner(p_args->'actor')) then perform infrx.refuse('not_found', 'no such operation'); end if; if r.state in ('succeeded', 'failed') then perform infrx.refuse('state_conflict', 'the operation already finished'); end if; if r.state in ('queued', 'running') then update infrx.control_operations set state = case when state = 'queued' then 'cancelled' else 'cancel_requested' end, cancel_requested_at = v_now, updated_at = v_now where operation_id = r.operation_id returning * into r; end if; return infrx.control_op_doc(r); end $$;

-- ================================================================ SR-AP10-1 ===
create or replace function infrx.lab_eval_catalog(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object(
    'datasets', (select coalesce(jsonb_agg(jsonb_build_object('ref', r.ref,
                   'label', r.object_id || coalesce(' v' || r.version, ''))
                   order by r.object_id, r.version), '[]')
                   from infrx.lab_records r
                  where r.kind = 'dataset' and r.provider_org_id::text = p_args->>'provider_org_id'),
    'harnesses', (select coalesce(jsonb_agg(jsonb_build_object('ref', r.ref,
                    'harness_id', r.object_id, 'version', r.version,
                    'adapter', r.body::jsonb->>'adapter') order by r.object_id, r.version), '[]')
                    from infrx.lab_records r
                   where r.kind = 'harness' and r.provider_org_id::text = p_args->>'provider_org_id'),
    'evaluators', (select coalesce(jsonb_agg(jsonb_build_object('ref', e.ref,
                     'label', coalesce(e.body::jsonb->>'metric', e.evaluator_id::text))
                     order by e.published_at, e.ref), '[]')
                     from infrx.lab_evaluators e
                    where e.provider_org_id::text = p_args->>'provider_org_id'),
    'servings', (select coalesce(jsonb_agg(jsonb_build_object(
                   'ref', infrx.lab_serving_ref(d.deployment_revision_id),
                   'label', s.revision_label) order by d.created_at, d.deployment_revision_id), '[]')
                   from infrx.deployment_revisions d
                   join infrx.serving_versions s on s.serving_version_id = d.serving_version_id
                  where d.provider_org_id::text = p_args->>'provider_org_id'
                    and d.environment = 'dev' and d.visibility = 'private'
                    and d.state = 'ready_private'))
$$;

create or replace function infrx.lab_experiments(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object(
      'experiment_id', e.experiment_id, 'created_at', e.created_at, 'protocol', e.protocol,
      'protocol_digest', e.protocol_digest,
      'baseline_run_ref', e.baseline_run_ref, 'candidate_run_ref', e.candidate_run_ref,
      'baseline', (select infrx.lab_run_json(r.run_id) from infrx.lab_eval_runs r
                    where r.run_ref = e.baseline_run_ref),
      'candidate', (select infrx.lab_run_json(r.run_id) from infrx.lab_eval_runs r
                     where r.run_ref = e.candidate_run_ref),
      'report', (select jsonb_build_object('report_digest', p.report_digest, 'body', p.body)
                   from infrx.lab_eval_reports p
                  where p.provider_org_id = e.provider_org_id
                    and p.baseline_run_ref = e.baseline_run_ref
                    and p.candidate_run_ref = e.candidate_run_ref
                    and p.protocol_digest = e.protocol_digest
                  order by p.stored_at desc, p.report_digest limit 1))
    order by e.created_at desc, e.experiment_id), '[]')
    from infrx.lab_experiments e where e.provider_org_id::text = p_args->>'provider_org_id'
$$;

-- ================================================================ SR-AP10-2 ===
create or replace function infrx.lab_external_runs_of(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object('external_run_id', o.external_run_id,
           'doc', o.doc) order by o.created_at, o.external_run_id), '[]')
    from infrx.lab_external_runs o where o.provider_org_id::text = p_args->>'provider_org_id'
$$;

create or replace function infrx.lab_checkpoint_receipts_of(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object('checkpoint_id', rc.checkpoint_id,
           'external_run_ref', rc.external_run_ref, 'artifact_digest', rc.artifact_digest,
           'state', coalesce(n.body->>'state', rc.state), 'reason', n.body->>'reason')
           order by rc.received_at, rc.checkpoint_id), '[]')
    from infrx.lab_checkpoint_receipts rc
    left join infrx.lab_pipeline_notes n on n.provider_org_id = rc.provider_org_id
     and n.key = 'checkpoint:' || rc.checkpoint_id
   where rc.provider_org_id::text = p_args->>'provider_org_id'
$$;

-- ==================================================================== AP-07 ===
create or replace function infrx.lab_withdraw_access_grant(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_grantor uuid := (p_args->>'grantor_org_id')::uuid;
  prev infrx.lab_access_grants%rowtype;
  g infrx.lab_access_grants%rowtype;
begin
  -- 0027's lab_grant_version without its suspension refusal: the owner, the pair's lock
  if not exists (select 1 from public.org_members m where m.org_id = v_grantor
                 and m.user_id = (p_args->>'actor_user_id')::uuid and m.role = 'owner') then
    perform infrx.refuse('forbidden', 'only the owner of the grantor organization writes');
  end if;
  perform pg_advisory_xact_lock(hashtextextended(
    'lab_access_grants/' || v_grantor || '/' || (p_args->>'recipient_provider_org_id'), 0));
  select * into prev from infrx.lab_access_grants
   where grantor_org_id = v_grantor
     and recipient_provider_org_id = (p_args->>'recipient_provider_org_id')::uuid
   order by version desc limit 1;
  if prev.grant_id is null or prev.revoked_at is not null then
    perform infrx.refuse('state_conflict', 'no current grant to withdraw');
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

-- ==================================================== 0060 control_op_cancel ===
-- {operation_id, actor} -> operation. queued -> cancelled (no worker holds it); running ->
-- cancel_requested (the lease holder reconciles and finishes); any other state - a repeated
-- cancel, or one that lost the race to the worker's finish - answers the row as it is.
-- Another tenant's operation: not_found.
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
  if r.state in ('queued', 'running') then
    update infrx.control_operations
       set state = case when state = 'queued' then 'cancelled' else 'cancel_requested' end,
           cancel_requested_at = v_now, updated_at = v_now
     where operation_id = r.operation_id returning * into r;
  end if;
  return infrx.control_op_doc(r);
end $$;

-- ============================================================== privileges ===
-- SR-AP10-3: the control login's route reads.
grant execute on function infrx.lab_put_experiment(jsonb), infrx.lab_checkpoint_listing(jsonb),
  infrx.lab_list_datasets(jsonb), infrx.lab_evaluator(jsonb),
  infrx.lab_checkpoint_subscribe(jsonb), infrx.lab_checkpoint_decisions(jsonb)
  to infrx_lab_control;
grant execute on function infrx.lab_eval_catalog(jsonb), infrx.lab_external_runs_of(jsonb),
  infrx.lab_checkpoint_receipts_of(jsonb)
  to infrx_lab_control;
