-- The remaining LW3 requests to lab-sql (wave-5 LW3, lane lab-sql), one file:
--   WR-N4-2  (datasets N4, N4 evidence)   lab_list_datasets: the provider's dataset versions.
--   WR-B4-2  (eval-ui B4, B4-7683330.md)  experiments (write-once, with their B2 report read
--            beside them), the provider's checkpoint subscriptions with their decisions, and
--            created_at/updated_at in `lab_run_json`. (`lab_cancel_run` already refuses a
--            finished run: 0029's state guard allows succeeded|failed -> cancelled nowhere -
--            `state_conflict`; asserted, no change.)
--   WR-R4-2  (release-ui R4, R4-b9b84b7.md) release proposals: one pending per policy
--            revision (a unique partial index - a double click is one proposal), decided
--            only through D9's fence CAS (0039 `lab_release_transition`).
--   SR-J3-1  (judge-lw4 J3, J3-e4836fa.md) the Lab's `public.lab_judge_runs` door (V3's
--            `JudgeRun` shape) with J3's calibration of the run's configuration - computed by
--            J3's `QualityReport.calibration()` from operator labels only and stored here by
--            the platform (`lab_put_judge_calibration`), never re-derived in SQL.
--   SR-R1-1  (rollout-routing R1, R1-8d2e636.md) the router's port over D9: release_active,
--            release_eligible, record_rollout_assignment, for `infrx_runtime` only.
--   WR-I2L-4 (lab-operate I2L, I2L-d1b0f21.md) the control service's own login role
--            `infrx_lab_control` with a CONNECTION LIMIT, reaching the Lab RPCs only.
--   WR-COMP-1 needs nothing new: 0034's `lab_evaluators` + `lab_evaluator` is the evaluator
--            read (`PgLabDataStore.evaluator(ref, provider_org_id=<the ref's provider>)`).
--   WR-LAB-API-4 (optional) is not done: `infrx.serving_versions` stays readable by
--            service_role (0007), so PgServing needs no RPC until that read is closed.
-- LOCAL-ONLY (R151): never applied hosted; the number is the next free at merge.
--
-- Tables (append-only unless named): lab_experiments (write-once); lab_release_proposals
--   (state proposed -> approved | rejected once, by a guard); lab_judge_calibrations (the
--   latest row per configuration is in force). Column: lab_rollouts.shadow_limit (default 0:
--   no provider-funded duplicate runs until an operator raises it; P-12 gates public shadow).
--
-- RPCs (SECURITY DEFINER; EXECUTE service_role only through 0004's defaults, except the
-- three R1 functions - infrx_runtime only - and the door - authenticated only):
--   lab_list_datasets {provider_org_id} -> [{ref, dataset_id, version, derivation, samples}].
--   lab_put_experiment {provider_org_id, experiment_id, protocol, protocol_digest,
--       baseline_run_ref, candidate_run_ref, actor}: the provider's two run records; a replay
--       is the row, another body under the id `idempotency_conflict`.
--   lab_experiments {provider_org_id} -> [{experiment_id, created_at, protocol,
--       protocol_digest, baseline, candidate (lab_run_json or null), report ({report_digest,
--       body} of the latest stored B2 report of these two runs under this protocol, or
--       null)}], newest first.
--   lab_checkpoint_listing {provider_org_id} -> [{subscription (minus evaluator and owner),
--       decisions: [{checkpoint_id, step, receipt, state, reason, run_id}]}].
--   lab_propose_release {provider_org_id, proposal_id, policy_ref, kind, fence, proposed_by}:
--       the policy revision the provider's rollout is at (`not_found`), at its current
--       fence, not rolled back, `expand` only while running (`state_conflict`); one pending
--       per revision (`state_conflict`); a replay is the row.
--   lab_decide_release_proposal {proposal_id, approve, decision, reasons, decided_by}:
--       approve = 0039's `lab_release_transition` at the proposal's fence (a stale fence
--       refuses and leaves the proposal pending), then approved; reject = rejected; a
--       decided proposal is decided never again (`lab_proposal_guard`: state_conflict).
--   lab_release_proposals {provider_org_id} -> [proposal], newest first.
--   lab_put_judge_calibration {provider_org_id, grantor_org_id, judge_model, rubric_version,
--       calibration}: J3's V3 `Calibration`; `calibrated` only with labels >= required and an
--       agreement and interval (J3's rule).
--   public.lab_judge_runs(p_provider_org_id, p_request_id): a CURRENT developer+ of the
--       provider (flag `lab_submission`, else 55000; else 42501) -> that provider's
--       Lab-requested judge runs that SENT the request, while the run's grant is current for
--       external_judging, in V3's `JudgeRun` shape (scores: criterion names and scores only -
--       never a rationale or notes).
--   infrx.release_active(requested_model) -> table(record, policy_ref, revisions,
--       shadow_limit): the RUNNING rollout of the endpoint the alias's current catalog
--       listing serves; every candidate `lab:serving` ref names a serving version of that
--       model (its uuid), whose R62 pin is `<alias>@<revision_label>` (a candidate with none
--       is `state_conflict`: fail closed, never a silent baseline).
--   infrx.release_eligible(policy_id, org_id) -> boolean: the org is not suspended and holds
--       a CURRENT grant to the policy's provider for provider_sharing; default deny.
--   infrx.record_rollout_assignment(record): a `lab.rollout_assignment.1` of a rollout's
--       policy revision (serving ref its baseline or a candidate) -> once per request.
--
-- ROLLBACK (this file alone; nothing earlier references it): revoke all on schema infrx
--   from infrx_lab_control; revoke execute on every function from it; drop role
--   infrx_lab_control; drop function infrx.record_rollout_assignment(jsonb),
--   infrx.release_eligible(uuid, uuid), infrx.release_active(text),
--   public.lab_judge_runs(uuid, uuid), infrx.lab_put_judge_calibration(jsonb),
--   infrx.lab_release_proposals(jsonb), infrx.lab_decide_release_proposal(jsonb),
--   infrx.lab_propose_release(jsonb), infrx.lab_proposal_json(infrx.lab_release_proposals),
--   infrx.lab_proposal_guard(), infrx.lab_checkpoint_listing(jsonb),
--   infrx.lab_experiments(jsonb), infrx.lab_put_experiment(jsonb),
--   infrx.lab_list_datasets(jsonb); restore 0029's `infrx.lab_run_json` body; drop table
--   infrx.lab_judge_calibrations, infrx.lab_release_proposals, infrx.lab_experiments; alter
--   table infrx.lab_rollouts drop column shadow_limit.
--
-- Re-runnable: `if not exists`, `create or replace`, guarded role creation.

-- ==================================================================== tables ===
create table if not exists infrx.lab_experiments (
  experiment_id uuid primary key,
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  protocol jsonb not null check (jsonb_typeof(protocol) = 'object'),
  protocol_digest text not null check (protocol_digest ~ '^sha256:[0-9a-f]{64}$'),
  baseline_run_ref text not null references infrx.lab_records on delete restrict,
  candidate_run_ref text not null references infrx.lab_records on delete restrict,
  created_by text not null check (length(btrim(created_by)) between 1 and 200),
  created_at timestamptz not null default infrx.now(),
  constraint lab_experiments_two_runs check (baseline_run_ref <> candidate_run_ref)
);

create table if not exists infrx.lab_release_proposals (
  proposal_id uuid primary key,
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  policy_ref text not null references infrx.lab_records on delete restrict,
  kind text not null check (kind in ('expand', 'rollback')),
  fence bigint not null check (fence >= 1),
  state text not null default 'proposed' check (state in ('proposed', 'approved', 'rejected')),
  proposed_by uuid not null,
  proposed_at timestamptz not null default infrx.now(),
  decided_by uuid,
  decided_at timestamptz,
  constraint lab_release_proposals_decided check ((state = 'proposed')
                                                  = (decided_at is null and decided_by is null))
);
create unique index if not exists lab_release_proposals_one_pending
  on infrx.lab_release_proposals (policy_ref) where state = 'proposed';

create table if not exists infrx.lab_judge_calibrations (
  calibration_id bigint generated always as identity primary key,
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  grantor_org_id uuid not null references public.organizations(id) on delete restrict,
  judge_model text not null check (judge_model ~ '^[a-z0-9][a-z0-9.-]{0,63}$'),
  rubric_version int not null check (rubric_version between 1 and 1000),
  calibration jsonb not null check (jsonb_typeof(calibration) = 'object'),
  computed_at timestamptz not null default infrx.now()
);
create index if not exists lab_judge_calibrations_by_config
  on infrx.lab_judge_calibrations (provider_org_id, grantor_org_id, judge_model,
                                   rubric_version, calibration_id desc);

alter table infrx.lab_rollouts
  add column if not exists shadow_limit int not null default 0 check (shadow_limit >= 0);

create or replace function infrx.lab_proposal_guard() returns trigger
language plpgsql set search_path = infrx, public, pg_temp as $$
begin
  if old.state <> 'proposed' or new.state = 'proposed'
     or (new.proposal_id, new.provider_org_id, new.policy_ref, new.kind, new.fence,
         new.proposed_by, new.proposed_at)
        is distinct from (old.proposal_id, old.provider_org_id, old.policy_ref, old.kind,
                          old.fence, old.proposed_by, old.proposed_at) then
    perform infrx.refuse('state_conflict', 'a proposal is decided once');
  end if;
  return new;
end $$;

do $$
declare
  t text;
begin
  execute 'create or replace trigger lab_release_proposals_decided_once before update on '
          'infrx.lab_release_proposals for each row execute function infrx.lab_proposal_guard()';
  foreach t in array array['lab_experiments', 'lab_judge_calibrations'] loop
    execute format('create or replace trigger %I before update or delete on infrx.%I '
                   'for each row execute function infrx.forbid_update_delete()',
                   t || '_immutable', t);
  end loop;
  execute 'create or replace trigger lab_release_proposals_kept before delete on '
          'infrx.lab_release_proposals for each row execute function infrx.forbid_update_delete()';
  foreach t in array array['lab_experiments', 'lab_release_proposals',
                           'lab_judge_calibrations'] loop
    execute format('create or replace trigger %I before truncate on infrx.%I '
                   'for each statement execute function infrx.forbid_truncate()',
                   t || '_no_truncate', t);
    execute format('alter table infrx.%I enable row level security', t);
    execute format('revoke all on infrx.%I from public, anon, authenticated, service_role', t);
    execute format('grant select on infrx.%I to service_role', t);
  end loop;
end $$;

-- ================================================================== WR-N4-2 ===
create or replace function infrx.lab_list_datasets(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object('ref', r.ref, 'dataset_id', r.object_id,
           'version', r.version, 'derivation', r.body::jsonb->>'derivation',
           'samples', (select count(*) from infrx.lab_dataset_samples s
                        where s.dataset_ref = r.ref))
           order by r.object_id, r.version), '[]')
    from infrx.lab_records r
   where r.kind = 'dataset' and r.provider_org_id::text = p_args->>'provider_org_id'
$$;

-- ================================================================== WR-B4-2 ===
-- 0029's run row plus its timeline (created_at, updated_at).
create or replace function infrx.lab_run_json(p_run uuid) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('run_id', r.run_id, 'run_ref', r.run_ref,
    'dataset_ref', r.dataset_ref, 'state', r.state,
    'created_at', r.created_at, 'updated_at', r.updated_at,
    'cases', (select coalesce(jsonb_object_agg(state, n), '{}') from (
               select c.state, count(*) n from infrx.lab_eval_cases c
                where c.run_id = r.run_id group by c.state) x),
    'attempts', (select coalesce(jsonb_object_agg(state, n), '{}') from (
               select a.state, count(*) n from infrx.lab_eval_attempts a
                where a.run_id = r.run_id group by a.state) x),
    'costs', (select coalesce(jsonb_object_agg(cost_unit, total::text), '{}') from (
               select a.cost_unit, sum(a.cost_value) total from infrx.lab_eval_attempts a
                where a.run_id = r.run_id and a.cost_unit is not null group by a.cost_unit) x))
    from infrx.lab_eval_runs r where r.run_id = p_run
$$;

create or replace function infrx.lab_put_experiment(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_provider uuid;
  e infrx.lab_experiments%rowtype;
begin
  begin
    v_provider := (p_args->>'provider_org_id')::uuid;
  exception when invalid_text_representation then
    perform infrx.refuse('invalid_request', 'an experiment names its provider');
  end;
  if (select count(*) from infrx.lab_records r where r.kind = 'run'
       and r.provider_org_id = v_provider
       and r.ref in (p_args->>'baseline_run_ref', p_args->>'candidate_run_ref')) <> 2 then
    perform infrx.refuse('not_found', 'an experiment compares two of this provider''s runs');
  end if;
  begin
    insert into infrx.lab_experiments (experiment_id, provider_org_id, protocol,
      protocol_digest, baseline_run_ref, candidate_run_ref, created_by)
    values ((p_args->>'experiment_id')::uuid, v_provider, p_args->'protocol',
      p_args->>'protocol_digest', p_args->>'baseline_run_ref', p_args->>'candidate_run_ref',
      p_args->>'actor')
    on conflict (experiment_id) do nothing
    returning * into e;
  exception when check_violation or not_null_violation or invalid_text_representation then
    perform infrx.refuse('invalid_request', 'an experiment is an id, a protocol and its '
                         'digest, two runs and an actor');
  end;
  if e.experiment_id is null then
    select * into e from infrx.lab_experiments
     where experiment_id = (p_args->>'experiment_id')::uuid;
    if (e.provider_org_id, e.protocol, e.protocol_digest, e.baseline_run_ref,
        e.candidate_run_ref) is distinct from (v_provider, p_args->'protocol',
        p_args->>'protocol_digest', p_args->>'baseline_run_ref',
        p_args->>'candidate_run_ref') then
      perform infrx.refuse('idempotency_conflict', 'this experiment id holds another launch');
    end if;
  end if;
  return jsonb_build_object('experiment_id', e.experiment_id, 'created_at', e.created_at);
end $$;

create or replace function infrx.lab_experiments(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object(
      'experiment_id', e.experiment_id, 'created_at', e.created_at, 'protocol', e.protocol,
      'protocol_digest', e.protocol_digest,
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

create or replace function infrx.lab_checkpoint_listing(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object(
      'subscription', s.body - 'evaluator' - 'owner_user_id',
      'decisions', (select coalesce(jsonb_agg(jsonb_build_object(
                       'checkpoint_id', d.checkpoint_id, 'step', ev.step,
                       'receipt', rc.state, 'state', d.state, 'reason', d.reason,
                       'run_id', d.run_id) order by ev.step, d.checkpoint_id), '[]')
                      from infrx.lab_checkpoint_decisions d
                      left join infrx.lab_checkpoint_events ev
                        on ev.provider_org_id = s.provider_org_id
                       and ev.checkpoint_id = d.checkpoint_id
                      left join infrx.lab_checkpoint_receipts rc
                        on rc.provider_org_id = s.provider_org_id
                       and rc.checkpoint_id = d.checkpoint_id
                     where d.subscription_id = s.subscription_id))
    order by s.created_at, s.subscription_id), '[]')
    from infrx.lab_checkpoint_subscriptions s
   where s.provider_org_id::text = p_args->>'provider_org_id'
$$;

-- ================================================================== WR-R4-2 ===
create or replace function infrx.lab_proposal_json(p infrx.lab_release_proposals)
returns jsonb language sql stable set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('proposal_id', p.proposal_id, 'policy_ref', p.policy_ref,
    'kind', p.kind, 'fence', p.fence, 'state', p.state, 'proposed_by', p.proposed_by,
    'proposed_at', p.proposed_at, 'decided_by', p.decided_by, 'decided_at', p.decided_at)
$$;

create or replace function infrx.lab_propose_release(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  o infrx.lab_rollouts%rowtype;
  p infrx.lab_release_proposals%rowtype;
begin
  select * into p from infrx.lab_release_proposals
   where proposal_id::text = p_args->>'proposal_id';
  if found then
    if (p.provider_org_id::text, p.policy_ref, p.kind, p.fence::text)
       is distinct from (p_args->>'provider_org_id', p_args->>'policy_ref', p_args->>'kind',
                         p_args->>'fence') then
      perform infrx.refuse('idempotency_conflict', 'this proposal id holds another proposal');
    end if;
    return infrx.lab_proposal_json(p);                -- a replay (a resubmitted form)
  end if;
  select * into o from infrx.lab_rollouts
   where policy_ref = p_args->>'policy_ref'
     and provider_org_id::text = p_args->>'provider_org_id' for update;
  if not found then
    perform infrx.refuse('not_found', 'no release at this policy revision for this provider');
  end if;
  if o.fence::text is distinct from p_args->>'fence' then
    perform infrx.refuse('state_conflict', 'stale fence: the release is at ' || o.fence);
  end if;
  if o.state = 'rolled_back' or (p_args->>'kind' = 'expand' and o.state <> 'running') then
    perform infrx.refuse('state_conflict', 'a ' || o.state || ' release takes no '
                         || coalesce(p_args->>'kind', '?') || ' proposal');
  end if;
  begin
    insert into infrx.lab_release_proposals (proposal_id, provider_org_id, policy_ref, kind,
                                             fence, proposed_by)
    values ((p_args->>'proposal_id')::uuid, o.provider_org_id, o.policy_ref, p_args->>'kind',
            o.fence, (p_args->>'proposed_by')::uuid)
    returning * into p;
  exception when unique_violation then
    perform infrx.refuse('state_conflict', 'a proposal for this release is already pending');
  when check_violation or not_null_violation or invalid_text_representation then
    perform infrx.refuse('invalid_request', 'a proposal is an id, expand or rollback, and its '
                         'proposer');
  end;
  return infrx.lab_proposal_json(p);
end $$;

create or replace function infrx.lab_decide_release_proposal(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  p infrx.lab_release_proposals%rowtype;
begin
  select * into p from infrx.lab_release_proposals
   where proposal_id::text = p_args->>'proposal_id' for update;
  if not found then
    perform infrx.refuse('not_found', 'no such proposal');
  end if;
  if coalesce((p_args->>'approve')::boolean, false) then
    perform infrx.lab_release_transition(jsonb_build_object(
      'policy_ref', p.policy_ref, 'fence', p.fence,
      'to', case p.kind when 'expand' then 'approved' else 'rolled_back' end,
      'decision', p_args->'decision', 'reasons', coalesce(p_args->'reasons', '[]')));
  end if;
  begin
    update infrx.lab_release_proposals
       set state = case when coalesce((p_args->>'approve')::boolean, false) then 'approved'
                        else 'rejected' end,
           decided_by = (p_args->>'decided_by')::uuid, decided_at = infrx.now()
     where proposal_id = p.proposal_id
    returning * into p;
  exception when check_violation or not_null_violation or invalid_text_representation then
    perform infrx.refuse('invalid_request', 'a decision names its operator');
  end;
  return infrx.lab_proposal_json(p);
end $$;

create or replace function infrx.lab_release_proposals(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(infrx.lab_proposal_json(p)
                            order by p.proposed_at desc, p.proposal_id), '[]')
    from infrx.lab_release_proposals p where p.provider_org_id::text = p_args->>'provider_org_id'
$$;

-- ================================================================== SR-J3-1 ===
create or replace function infrx.lab_put_judge_calibration(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  c jsonb := p_args->'calibration';
  k infrx.lab_judge_calibrations%rowtype;
begin
  if jsonb_typeof(c) is distinct from 'object'
     or coalesce(c->>'state', '') not in ('uncalibrated', 'insufficient', 'calibrated')
     or jsonb_typeof(c->'labels') is distinct from 'number'
     or jsonb_typeof(c->'required') is distinct from 'number'
     or (c->>'labels')::numeric < 0 or (c->>'required')::numeric < 1
     or jsonb_typeof(c->'agreement') not in ('null', 'number')
     or jsonb_typeof(c->'interval') not in ('null', 'array')
     or (c->>'state' = 'calibrated'
         and ((c->>'labels')::numeric < (c->>'required')::numeric
              or jsonb_typeof(c->'agreement') <> 'number'
              or jsonb_typeof(c->'interval') <> 'array')) then
    perform infrx.refuse('invalid_request', 'a calibration is J3''s shape; calibrated only '
                         'with enough labels, an agreement and its interval');
  end if;
  begin
    insert into infrx.lab_judge_calibrations (provider_org_id, grantor_org_id, judge_model,
                                              rubric_version, calibration)
    values ((p_args->>'provider_org_id')::uuid, (p_args->>'grantor_org_id')::uuid,
            p_args->>'judge_model', (p_args->>'rubric_version')::int, c)
    returning * into k;
  exception when check_violation or not_null_violation or invalid_text_representation
            or foreign_key_violation then
    perform infrx.refuse('invalid_request', 'a calibration names its provider, grantor, '
                         'judge model and rubric version');
  end;
  return jsonb_build_object('calibration_id', k.calibration_id);
end $$;

create or replace function public.lab_judge_runs(p_provider_org_id uuid, p_request_id uuid)
returns jsonb language plpgsql security definer set search_path = public, infrx, pg_temp as $$
begin
  perform infrx.lab_judge_door(p_provider_org_id, 'developer');
  return (select coalesce(jsonb_agg(x.run order by x.created_at, x.run_id), '[]') from (
    select r.created_at, r.run_id, jsonb_build_object(
        'runId', r.run_id, 'mode', 'live',
        'state', case r.state when 'prepared' then 'reserved' when 'submitting' then 'reserved'
                   when 'submitted' then 'submitted' when 'ambiguous' then 'ambiguous'
                   when 'completed' then 'collected' else 'released' end,
        'judgeModel', c.judge_model, 'rubricVersion', c.rubric_version,
        'media', p_request_id = any(r.media_ids),
        'reservedUsd', r.reserved::text, 'actualUsd', r.actual::text,
        -- ponytail: a stored result has no rubric; J1 criteria score 1..5 and R56's
        -- media floor is the result's `media_required` - store the rubric when one differs
        'scores', coalesce((select jsonb_agg(jsonb_build_object(
                     'criterion', s->>'name', 'score', (s->>'score')::int, 'max', 5,
                     'requiresMedia', coalesce((res.result->>'media_required')::boolean,
                                               false)))
                     from jsonb_array_elements(coalesce(res.result->'scores', '[]')) s), '[]'),
        'overallPass', (res.result->>'overall_pass')::boolean,
        'calibration', coalesce((select k.calibration from infrx.lab_judge_calibrations k
                                  where k.provider_org_id = r.provider_org_id
                                    and k.grantor_org_id = c.grantor_org_id
                                    and k.judge_model = c.judge_model
                                    and k.rubric_version = c.rubric_version
                                  order by k.calibration_id desc limit 1),
                                jsonb_build_object('state', 'uncalibrated', 'labels', 0,
                                  'required', 30, 'agreement', null, 'interval', null))) run
      from infrx.lab_judge_runs r
      join infrx.lab_judge_requests q on q.run_id = r.run_id
      join infrx.lab_judge_configs c on c.config_id = q.config_id
      left join infrx.lab_judge_results res
        on res.run_id = r.run_id and res.sample_id = p_request_id
       and res.rubric_version = c.rubric_version and res.accepted
     where r.provider_org_id = p_provider_org_id and r.purpose = 'external_judging'
       and p_request_id = any(r.sent_sample_ids)
       and infrx.lab_grant_current(r.grant_id, 'external_judging')) x);
end $$;

revoke all on function public.lab_judge_runs(uuid, uuid) from public, anon;
grant execute on function public.lab_judge_runs(uuid, uuid) to authenticated;

-- ================================================================== SR-R1-1 ===
create or replace function infrx.release_active(requested_model text)
returns table (record jsonb, policy_ref text, revisions jsonb, shadow_limit int)
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  o infrx.lab_rollouts%rowtype;
  v_model uuid;
  v_endpoint uuid;
  v_doc jsonb;
  v_revisions jsonb := '{}';
  c jsonb;
  v_label text;
begin
  select l.model_id, d.endpoint_id into v_model, v_endpoint
    from infrx.catalog_listings l
    join infrx.deployment_revisions d on d.deployment_revision_id = l.deployment_revision_id
   where l.public_model_id = requested_model and l.effective_at <= infrx.now()
   order by l.version desc limit 1;
  select * into o from infrx.lab_rollouts x
   where x.endpoint_id = v_endpoint and x.state = 'running';
  if o.policy_id is null then
    return;
  end if;
  select r.body::jsonb into v_doc from infrx.lab_records r where r.ref = o.policy_ref;
  for c in select * from jsonb_array_elements(v_doc->'candidates') loop
    select sv.revision_label into v_label from infrx.serving_versions sv
     where sv.serving_version_id::text = (infrx.lab_ref_parts(c->>'serving_ref'))[3]
       and sv.provider_org_id = o.provider_org_id and sv.model_id = v_model;
    if v_label is null then
      perform infrx.refuse('state_conflict', 'candidate ' || (c->>'serving_ref')
                           || ' is not a published revision of ' || requested_model);
    end if;
    v_revisions := v_revisions || jsonb_build_object(c->>'serving_ref',
                                                     requested_model || '@' || v_label);
  end loop;
  return query select v_doc, o.policy_ref, v_revisions, o.shadow_limit;
end $$;

create or replace function infrx.release_eligible(policy_id uuid, org_id uuid)
returns boolean language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce((select not coalesce(org.suspended, false)
                          and g.effective_at <= infrx.now()
                          and (g.revoked_at is null or infrx.now() < g.revoked_at)
                          and (g.expires_at is null or infrx.now() < g.expires_at)
                          and 'provider_sharing' = any(g.purposes)
                     from infrx.lab_rollouts o
                     join public.organizations org on org.id = release_eligible.org_id
                     join infrx.lab_access_grants g
                       on g.grantor_org_id = release_eligible.org_id
                      and g.recipient_provider_org_id = o.provider_org_id
                    where o.policy_id = release_eligible.policy_id
                    order by g.version desc limit 1), false)
$$;

create or replace function infrx.record_rollout_assignment(record jsonb) returns void
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  o infrx.lab_rollouts%rowtype;
  v_doc jsonb;
begin
  select r.body::jsonb into v_doc from infrx.lab_records r
   where r.ref = record->>'policy_ref' and r.kind = 'policy'
     and r.provider_org_id::text = record->>'provider_org_id';
  select * into o from infrx.lab_rollouts x where x.policy_id::text = v_doc->>'policy_id';
  if o.policy_id is null then
    perform infrx.refuse('not_found', 'no rollout at this policy revision for this provider');
  end if;
  if record->>'schema' is distinct from 'lab.rollout_assignment.1'
     or not (record->>'serving_ref' = v_doc->>'baseline_ref' or exists (
       select 1 from jsonb_array_elements(v_doc->'candidates') c
        where c->>'serving_ref' = record->>'serving_ref')) then
    perform infrx.refuse('invalid_request', 'an assignment serves the revision''s baseline or '
                         'one of its candidates');
  end if;
  begin
    insert into infrx.lab_rollout_assignments (policy_id, request_id, policy_ref,
      cohort_digest, serving_ref, pinned_by)
    values (o.policy_id, (record->>'request_id')::uuid, record->>'policy_ref',
      record->>'cohort_digest', record->>'serving_ref', record->>'pinned_by')
    on conflict (policy_id, request_id) do nothing;
  exception when check_violation or not_null_violation or invalid_text_representation then
    perform infrx.refuse('invalid_request', 'an assignment is a request id, a cohort digest '
                         'and how it was pinned');
  end;
end $$;

revoke all on function infrx.release_active(text), infrx.release_eligible(uuid, uuid),
  infrx.record_rollout_assignment(jsonb) from public, anon, authenticated, service_role;
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'infrx_runtime') then
    create role infrx_runtime nologin noinherit;
  end if;
end $$;
grant usage on schema infrx to infrx_runtime;
grant execute on function infrx.release_active(text), infrx.release_eligible(uuid, uuid),
  infrx.record_rollout_assignment(jsonb) to infrx_runtime;

-- ================================================================= WR-I2L-4 ===
-- The Lab control service's own login (INFRX_LAB_DATABASE_URL): no password here (the
-- operator sets one out of band), at most 10 connections (the operator may retune it with
-- `alter role infrx_lab_control connection limit N`), and only the Lab's named RPCs - never
-- an App table or the App's RPCs. A later Lab migration re-runs the grant loop below.
do $$
declare
  f regprocedure;
begin
  if not exists (select 1 from pg_roles where rolname = 'infrx_lab_control') then
    create role infrx_lab_control login noinherit nobypassrls;
  end if;
  -- (a role is the cluster's: set its bounds on every run, not only at creation)
  execute 'alter role infrx_lab_control login noinherit nobypassrls connection limit 10';
  execute 'grant usage on schema infrx to infrx_lab_control';
  for f in select p.oid::regprocedure from pg_proc p
            where p.pronamespace = 'infrx'::regnamespace and p.proname like 'lab\_%'
              and p.prokind = 'f' and p.prosecdef loop
    execute format('grant execute on function %s to infrx_lab_control', f);
  end loop;
end $$;
