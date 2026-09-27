-- D7 follow-up (wave-5 LW2, lane lab-sql): the D7 review's carried minors and the schema
-- requests filed against 0029 by other lanes. LOCAL-ONLY (R151): never applied hosted; the
-- number is the next free at merge. Strictly additive over 0029 (new columns, tables,
-- triggers and functions; three 0029 functions redefined, named below).
--
-- D7 review minors:
--   F4     a run whose every case failed ends `failed`, not `succeeded` (lab_finish_attempt).
--   F5     a dataset record whose sample names no source (or not as a string) is refused
--          before any row is written, instead of publishing without that sample
--          (trigger lab_records_shape).
--   F6     no case is leased once its sample's grant is no longer in force for
--          provider_sharing - a revocation mid-run stops leasing at once (trigger
--          lab_eval_attempts_rights, on new leases).
--   F7     sources and checkpoint receipts are keyed by (provider, id): another provider's
--          use of the same id is its own row, never a conflict that reveals or blocks the
--          first (lab_register_source, lab_receive_checkpoint).
--   RSI-3  an attempt's cost is in a unit one of the run's budgets names (lab_finish_attempt).
--   RSI-2  0029's rollback note says "except 0027's six": 0027 creates SEVEN functions
--          (lab_grant_json, lab_grant_version, lab_put_access_grant, lab_revoke_access_grant,
--          lab_access_grants, lab_provider_memberships, lab_deployment_aggregates); 0030's
--          `public.lab_provider_memberships()` is not in `infrx`. Read 0029's note that way.
--   F3     (the reaper's SKIP LOCKED) needed no SQL: it is now observed by a D7 drill.
-- Requests:
--   WR-B-2  (a) `lab_finish_attempt` stores a failed attempt's `error` code; (b) Lab
--           evaluators (R167's upgrade): `lab_evaluators` holds each spec as RFC 8785 bytes
--           addressed by its sha256 (B1's `evaluator_ref`); a run record, and every result,
--           must name one of the provider's registered evaluators; (c) `lab_run_results`, a
--           run's cases, attempts (cost, error) and results in one read; (d)
--           `lab_release_attempt`: a fenced lease given back WITHOUT consuming an attempt
--           (the attempt row goes, the case is pending with its count restored) - for a 402.
--   WR-B-7  `lab_eval_reports`: B2 reports stored write-once by `report_digest` (the sha256
--           of the stored RFC 8785 bytes), bound to both of the provider's run records.
--   H1      `lab_dataset_uses`: the `DatasetSources` port - every (grantor, model, category)
--           of the grant versions the dataset's samples were captured under (R172).
--
-- Named RPCs (SECURITY DEFINER; EXECUTE for service_role only through 0004's defaults):
--   lab_put_evaluator, lab_evaluator, lab_run_results, lab_release_attempt,
--   lab_put_eval_report, lab_eval_report, lab_dataset_uses; redefined: lab_register_source,
--   lab_receive_checkpoint, lab_finish_attempt.
--
-- ROLLBACK (this file alone): drop function infrx.lab_dataset_uses(jsonb),
--   infrx.lab_eval_report(jsonb), infrx.lab_put_eval_report(jsonb),
--   infrx.lab_release_attempt(jsonb), infrx.lab_run_results(jsonb), infrx.lab_evaluator(jsonb),
--   infrx.lab_put_evaluator(jsonb); drop trigger lab_records_shape on infrx.lab_records,
--   lab_eval_attempts_rights on infrx.lab_eval_attempts; drop function
--   infrx.lab_records_shape(), infrx.lab_lease_rights(); re-run 0029's definitions of
--   lab_register_source, lab_receive_checkpoint and lab_finish_attempt; restore the one-column
--   primary keys of lab_sources (source_id) and lab_checkpoint_receipts (checkpoint_id) - only
--   while no id is used by two providers; drop table infrx.lab_eval_reports,
--   infrx.lab_evaluators; alter table infrx.lab_eval_attempts drop column error_code.
--
-- Re-runnable: `if not exists`, `create or replace`, guarded key changes.

-- ================================================================ F7: keys ===
do $$
begin
  if (select array_length(conkey, 1) from pg_constraint
       where conname = 'lab_sources_pkey' and conrelid = 'infrx.lab_sources'::regclass) = 1 then
    alter table infrx.lab_sources drop constraint lab_sources_pkey,
      add constraint lab_sources_pkey primary key (provider_org_id, source_id);
  end if;
  if (select array_length(conkey, 1) from pg_constraint
       where conname = 'lab_checkpoint_receipts_pkey'
         and conrelid = 'infrx.lab_checkpoint_receipts'::regclass) = 1 then
    alter table infrx.lab_checkpoint_receipts drop constraint lab_checkpoint_receipts_pkey,
      add constraint lab_checkpoint_receipts_pkey primary key (provider_org_id, checkpoint_id);
  end if;
end $$;

-- ========================================================== tables and columns ===
alter table infrx.lab_eval_attempts add column if not exists error_code text
  check (error_code ~ '^[a-z][a-z0-9_:.-]{0,99}$');

create table if not exists infrx.lab_evaluators (
  ref text primary key,
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  evaluator_id uuid not null,
  body text not null check (length(body) <= 65536),
  published_by text not null check (length(btrim(published_by)) between 1 and 200),
  published_at timestamptz not null default infrx.now(),
  constraint lab_evaluators_content_addressed check (ref = 'lab:evaluator:' || provider_org_id
    || ':' || evaluator_id || '@sha256:' || encode(sha256(convert_to(body, 'UTF8')), 'hex'))
);

create table if not exists infrx.lab_eval_reports (
  report_digest text primary key,
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  baseline_run_ref text not null references infrx.lab_records on delete restrict,
  candidate_run_ref text not null references infrx.lab_records on delete restrict,
  protocol_digest text not null check (protocol_digest ~ '^sha256:[0-9a-f]{64}$'),
  outcome text not null check (outcome in ('accept', 'reject', 'inconclusive')),
  body text not null check (length(body) <= 1048576),
  stored_by text not null check (length(btrim(stored_by)) between 1 and 200),
  stored_at timestamptz not null default infrx.now(),
  constraint lab_eval_reports_content_addressed
    check (report_digest = 'sha256:' || encode(sha256(convert_to(body, 'UTF8')), 'hex'))
);

do $$
declare
  t text;
begin
  foreach t in array array['lab_evaluators', 'lab_eval_reports'] loop
    execute format('create or replace trigger %I before update or delete on infrx.%I '
                   'for each row execute function infrx.forbid_update_delete()',
                   t || '_immutable', t);
    execute format('create or replace trigger %I before truncate on infrx.%I '
                   'for each statement execute function infrx.forbid_truncate()',
                   t || '_no_truncate', t);
    execute format('alter table infrx.%I enable row level security', t);
    execute format('revoke all on infrx.%I from public, anon, authenticated, service_role', t);
    execute format('grant select on infrx.%I to service_role', t);
  end loop;
end $$;

-- ================================================ F5 + R167: record shapes ===
create or replace function infrx.lab_records_shape() returns trigger
language plpgsql set search_path = infrx, public, pg_temp as $$
declare
  v_doc jsonb := new.body::jsonb;
begin
  if new.kind = 'dataset' and exists (
       select 1 from jsonb_array_elements(case jsonb_typeof(v_doc->'samples')
                                          when 'array' then v_doc->'samples' else '[{}]' end) s
        where jsonb_typeof(s->'source_ref') is distinct from 'string') then
    perform infrx.refuse('invalid_request', 'every sample names its source');
  end if;
  -- (the ref's provider segment is the publisher's - lab_publish's walk - and a stored
  -- evaluator's ref carries its own provider, so the ref alone is provider-scoped)
  if new.kind = 'run' and not exists (
       select 1 from infrx.lab_evaluators e where e.ref = v_doc->>'evaluator_ref') then
    perform infrx.refuse('not_found', 'no such evaluator for this provider');
  end if;
  return new;
end $$;
create or replace trigger lab_records_shape before insert on infrx.lab_records
  for each row execute function infrx.lab_records_shape();

-- ============================================ F6: leases re-read the grant ===
create or replace function infrx.lab_lease_rights() returns trigger
language plpgsql set search_path = infrx, public, pg_temp as $$
begin
  if not exists (select 1 from infrx.lab_eval_runs r
                   join infrx.lab_dataset_samples s
                     on s.dataset_ref = r.dataset_ref and s.sample_id = new.case_id
                  where r.run_id = new.run_id
                    and infrx.lab_grant_current(s.grant_id, 'provider_sharing')) then
    perform infrx.refuse('forbidden', 'lease: the case''s grant is not in force for '
                         'provider_sharing');
  end if;
  return new;
end $$;
create or replace trigger lab_eval_attempts_rights before insert on infrx.lab_eval_attempts
  for each row when (new.state = 'leased') execute function infrx.lab_lease_rights();

-- ===================================================== redefined 0029 RPCs ===
create or replace function infrx.lab_register_source(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_provider uuid := (p_args->>'provider_org_id')::uuid;
  v_source uuid := (p_args->>'source_id')::uuid;
  g infrx.lab_access_grants%rowtype;
  s infrx.lab_sources%rowtype;
begin
  g := infrx.lab_grant_of(p_args->>'grant_ref', v_provider);
  if g.grant_id is null then
    perform infrx.refuse('not_found', 'no such grant for this provider');
  end if;
  if not infrx.lab_grant_current(g.grant_id, null) then
    perform infrx.refuse('forbidden', 'the grant is revoked or expired');
  end if;
  begin
    insert into infrx.lab_sources (source_id, provider_org_id, content_digest, grant_id,
      grant_version, registered_by, ref)
    values (v_source, v_provider, p_args->>'content_digest', g.grant_id, g.version,
      p_args->>'actor', 'lab:source:' || v_provider || ':' || v_source || '@sha256:'
      || substr(p_args->>'content_digest', 8))
    on conflict (provider_org_id, source_id) do nothing;
  exception when check_violation or not_null_violation then
    perform infrx.refuse('invalid_request', 'a source is a sha256 content digest and an actor');
  end;
  select * into s from infrx.lab_sources where provider_org_id = v_provider
     and source_id = v_source;
  if (s.provider_org_id, s.content_digest, s.grant_id, s.grant_version)
     is distinct from (v_provider, p_args->>'content_digest', g.grant_id, g.version) then
    perform infrx.refuse('state_conflict', 'source ' || v_source || ' names other content');
  end if;
  return jsonb_build_object('ref', s.ref);
end $$;

create or replace function infrx.lab_receive_checkpoint(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_provider uuid := (p_args->>'provider_org_id')::uuid;
  c infrx.lab_checkpoint_receipts%rowtype;
begin
  if not exists (select 1 from infrx.lab_records where ref = p_args->>'external_run_ref'
                 and provider_org_id = v_provider and kind = 'external_run') then
    perform infrx.refuse('not_found', 'no such external run for this provider');
  end if;
  begin
    insert into infrx.lab_checkpoint_receipts (checkpoint_id, provider_org_id,
      external_run_ref, artifact_digest)
    values ((p_args->>'checkpoint_id')::uuid, v_provider, p_args->>'external_run_ref',
      p_args->>'artifact_digest')
    on conflict (provider_org_id, checkpoint_id) do nothing
    returning * into c;
  exception when check_violation or not_null_violation or invalid_text_representation then
    perform infrx.refuse('invalid_request', 'a checkpoint is an id and a sha256 digest');
  end;
  if c.checkpoint_id is null then
    -- a redelivery (or a concurrent duplicate that waited for the first)
    select * into c from infrx.lab_checkpoint_receipts where provider_org_id = v_provider
       and checkpoint_id = (p_args->>'checkpoint_id')::uuid;
    if (c.provider_org_id, c.external_run_ref, c.artifact_digest) is distinct from
       (v_provider, p_args->>'external_run_ref', p_args->>'artifact_digest') then
      perform infrx.refuse('idempotency_conflict', 'the checkpoint id names another artifact');
    end if;
    return infrx.lab_receipt_json(c);
  end if;
  insert into infrx.lab_outbox (provider_org_id, kind, payload)
  values (v_provider, 'checkpoint_received', jsonb_build_object('checkpoint_id',
                                                                 c.checkpoint_id));
  return infrx.lab_receipt_json(c);
end $$;

create or replace function infrx.lab_finish_attempt(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_lease jsonb := p_args->'lease';
  v_outcome text := p_args->>'outcome';
  v_results jsonb := coalesce(p_args->'results', '[]');
  v_digest text := encode(sha256(convert_to((jsonb_build_object('outcome', v_outcome,
    'results', v_results, 'cost', p_args->'cost')
    || jsonb_strip_nulls(jsonb_build_object('error', p_args->'error')))::text, 'UTF8')), 'hex');
  a infrx.lab_eval_attempts%rowtype;
begin
  -- A finish whose answer was lost (the kill after commit) replays to the stored attempt.
  select x.* into a from infrx.lab_eval_attempts x join infrx.lab_eval_runs r using (run_id)
   where x.run_id = (v_lease->>'run_id')::uuid and x.case_id = (v_lease->>'case_id')::uuid
     and x.attempt = (v_lease->>'attempt')::int and x.worker_id = v_lease->>'worker_id'
     and r.provider_org_id = (v_lease->>'provider_org_id')::uuid;
  if a.finish_digest is not null then
    if a.finish_digest <> v_digest then
      perform infrx.refuse('idempotency_conflict', 'the attempt finished with another outcome');
    end if;
    return infrx.lab_attempt_json(a.run_id, a);
  end if;
  if v_outcome is null or v_outcome not in ('succeeded', 'failed')
     or (v_outcome = 'failed' and jsonb_array_length(v_results) > 0)
     or (v_outcome = 'succeeded' and p_args->>'error' is not null) then
    perform infrx.refuse('invalid_request', 'an attempt succeeds with its results or fails '
                         'with none (and its error code)');
  end if;
  a := infrx.lab_fence(v_lease);
  -- R167: every result is under one of this provider's registered evaluators
  if exists (select 1 from jsonb_array_elements(v_results) x
              where not exists (select 1 from infrx.lab_evaluators e
                                 where e.ref = x->>'evaluator_ref'
                                   and e.provider_org_id = (v_lease->>'provider_org_id')::uuid))
  then
    perform infrx.refuse('not_found', 'no such evaluator for this provider');
  end if;
  -- RSI-3: a cost is in a unit one of the run's budgets names (never converted, R159)
  if jsonb_typeof(p_args->'cost') = 'object' and not exists (
       select 1 from infrx.lab_eval_runs r join infrx.lab_records x on x.ref = r.run_ref
            cross join jsonb_array_elements(x.body::jsonb->'budgets') b
        where r.run_id = a.run_id and b->'limit'->>'unit' = p_args->'cost'->>'unit') then
    perform infrx.refuse('invalid_request', 'mixed_units: the cost is not in a unit of the '
                         'run''s budgets');
  end if;
  begin
    insert into infrx.lab_eval_results (run_id, case_id, evaluator_ref, attempt, body)
    select a.run_id, a.case_id, x->>'evaluator_ref', a.attempt, x->>'body'
      from jsonb_array_elements(v_results) x;
    update infrx.lab_eval_attempts set state = v_outcome, finished_at = infrx.now(),
      cost_unit = p_args->'cost'->>'unit', cost_value = (p_args->'cost'->>'value')::numeric,
      finish_digest = v_digest, error_code = p_args->>'error'
     where (run_id, case_id, attempt) = (a.run_id, a.case_id, a.attempt)
    returning * into a;
  exception when unique_violation or check_violation or not_null_violation
            or invalid_text_representation or numeric_value_out_of_range then
    perform infrx.refuse('invalid_request', 'one result per evaluator, a bounded body, a '
                         'unit-tagged cost and a well-formed error code');
  end;
  update infrx.lab_eval_cases set state = case v_outcome when 'succeeded' then 'done'
                                                         else 'failed' end
   where (run_id, case_id) = (a.run_id, a.case_id);
  if not exists (select 1 from infrx.lab_eval_cases where run_id = a.run_id
                 and state in ('pending', 'leased')) then
    -- F4: succeeded if any case was evaluated; a run whose every case failed failed
    update infrx.lab_eval_runs set updated_at = infrx.now(),
      state = case when exists (select 1 from infrx.lab_eval_cases c
                                 where c.run_id = a.run_id and c.state = 'done')
                   then 'succeeded' else 'failed' end
     where run_id = a.run_id;
  end if;
  return infrx.lab_attempt_json(a.run_id, a);
end $$;

-- =========================================================== WR-B-2 (b)-(d) ===
create or replace function infrx.lab_put_evaluator(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_provider uuid := (p_args->>'provider_org_id')::uuid;
  v_ref text := 'lab:evaluator:' || (p_args->>'provider_org_id') || ':'
                || (p_args->>'evaluator_id') || '@sha256:'
                || encode(sha256(convert_to(p_args->>'body', 'UTF8')), 'hex');
begin
  begin
    perform (p_args->>'body')::jsonb;
    insert into infrx.lab_evaluators (ref, provider_org_id, evaluator_id, body, published_by)
    values (v_ref, v_provider, (p_args->>'evaluator_id')::uuid, p_args->>'body',
            p_args->>'actor')
    on conflict (ref) do nothing;
  exception when check_violation or not_null_violation or invalid_text_representation
            or foreign_key_violation then
    perform infrx.refuse('invalid_request', 'an evaluator is a JSON spec with an id and an '
                         'actor');
  end;
  return jsonb_build_object('ref', v_ref);
end $$;

create or replace function infrx.lab_evaluator(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  e infrx.lab_evaluators%rowtype;
begin
  select * into e from infrx.lab_evaluators where ref = p_args->>'ref'
     and provider_org_id = (p_args->>'provider_org_id')::uuid;
  if not found then
    perform infrx.refuse('not_found', 'no such evaluator for this provider');
  end if;
  return jsonb_build_object('ref', e.ref, 'body', e.body);
end $$;

create or replace function infrx.lab_run_results(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  v_run uuid := (p_args->>'run_id')::uuid;
begin
  if not exists (select 1 from infrx.lab_eval_runs where run_id = v_run
                 and provider_org_id = (p_args->>'provider_org_id')::uuid) then
    perform infrx.refuse('not_found', 'no such run for this provider');
  end if;
  return infrx.lab_run_json(v_run) || jsonb_build_object(
    'case_states', (select coalesce(jsonb_agg(jsonb_build_object('case_id', c.case_id,
        'state', c.state, 'attempts', c.attempts) order by c.case_id), '[]')
        from infrx.lab_eval_cases c where c.run_id = v_run),
    'attempt_rows', (select coalesce(jsonb_agg(jsonb_build_object('case_id', a.case_id,
        'attempt', a.attempt, 'state', a.state, 'worker_id', a.worker_id,
        'error', a.error_code, 'cost', case when a.cost_unit is not null then
          jsonb_build_object('unit', a.cost_unit, 'value', a.cost_value::text) end)
        order by a.case_id, a.attempt), '[]')
        from infrx.lab_eval_attempts a where a.run_id = v_run),
    'results', (select coalesce(jsonb_agg(jsonb_build_object('case_id', x.case_id,
        'evaluator_ref', x.evaluator_ref, 'attempt', x.attempt, 'body', x.body)
        order by x.case_id, x.evaluator_ref), '[]')
        from infrx.lab_eval_results x where x.run_id = v_run));
end $$;

-- A 402 (the provider_dev wallet is exhausted) is not the case's failure: the live lease is
-- given back and the attempt it took is not counted - the next lease is the same number.
create or replace function infrx.lab_release_attempt(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  a infrx.lab_eval_attempts%rowtype := infrx.lab_fence(p_args->'lease');
begin
  delete from infrx.lab_eval_attempts
   where (run_id, case_id, attempt) = (a.run_id, a.case_id, a.attempt);
  update infrx.lab_eval_cases set state = 'pending', attempts = attempts - 1
   where (run_id, case_id) = (a.run_id, a.case_id);
  return infrx.lab_attempt_json(a.run_id, a) || '{"state": "released"}';
end $$;

-- ================================================================== WR-B-7 ===
create or replace function infrx.lab_put_eval_report(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_provider uuid := (p_args->>'provider_org_id')::uuid;
  v_doc jsonb;
  v_digest text := 'sha256:' || encode(sha256(convert_to(p_args->>'body', 'UTF8')), 'hex');
begin
  begin
    v_doc := (p_args->>'body')::jsonb;
  exception when invalid_text_representation then
    perform infrx.refuse('invalid_request', 'the body is not JSON');
  end;
  if v_doc->>'schema' is distinct from 'infrx.eval_report.1' then
    perform infrx.refuse('invalid_request', 'not an infrx.eval_report.1');
  end if;
  if (select count(*) from infrx.lab_records r where r.kind = 'run'
       and r.provider_org_id = v_provider
       and r.ref in (v_doc->>'baseline_run', v_doc->>'candidate_run')) <> 2 then
    perform infrx.refuse('not_found', 'a report compares two of this provider''s runs');
  end if;
  begin
    insert into infrx.lab_eval_reports (report_digest, provider_org_id, baseline_run_ref,
      candidate_run_ref, protocol_digest, outcome, body, stored_by)
    values (v_digest, v_provider, v_doc->>'baseline_run', v_doc->>'candidate_run',
      v_doc->>'protocol_digest', v_doc->'decision'->>'outcome', p_args->>'body',
      p_args->>'actor')
    on conflict (report_digest) do nothing;
  exception when check_violation or not_null_violation then
    perform infrx.refuse('invalid_request', 'a report binds its protocol digest and decision');
  end;
  return jsonb_build_object('report_digest', v_digest);
end $$;

create or replace function infrx.lab_eval_report(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  e infrx.lab_eval_reports%rowtype;
begin
  select * into e from infrx.lab_eval_reports where report_digest = p_args->>'report_digest'
     and provider_org_id = (p_args->>'provider_org_id')::uuid;
  if not found then
    perform infrx.refuse('not_found', 'no such report for this provider');
  end if;
  return jsonb_build_object('report_digest', e.report_digest, 'body', e.body,
                            'stored_by', e.stored_by, 'stored_at', e.stored_at);
end $$;

-- ====================================================== H1: DatasetSources ===
-- ponytail: a source records its grant, not its own model and category, so a use is every
-- (model, category) of the grant version it was captured under - authorize then needs the
-- current grant to still cover all of them (narrowing a grant denies; fail-closed).
create or replace function infrx.lab_dataset_uses(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(u order by u->>'grantor_org_id', u->>'model_id',
                            u->>'category'), '[]')
    from (select distinct jsonb_build_object('grantor_org_id', g.grantor_org_id,
                   'model_id', m, 'category', c) u
            from infrx.lab_records r
            join infrx.lab_dataset_samples s on s.dataset_ref = r.ref
            join infrx.lab_access_grants g
              on (g.grant_id, g.version) = (s.grant_id, s.grant_version)
            cross join unnest(g.model_ids) m cross join unnest(g.categories) c
           where r.ref = p_args->>'dataset_ref' and r.kind = 'dataset'
             and r.provider_org_id = (p_args->>'provider_org_id')::uuid) x
$$;
