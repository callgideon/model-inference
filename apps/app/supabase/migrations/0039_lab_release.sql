-- WR-R2-1 (wave-5 LW2 request from the rollout-control lane's R2; lane lab-sql, D9): the
-- `ReleaseStore` R2's controller consumes - a release IS D9's rollout row at its policy
-- revision (0033 `lab_rollouts`, one schema): R2 reads it by `policy_ref`, and moves it
-- running -> approved | rolled_back and approved -> rolled_back by a CAS on the same FENCE
-- D9's own transitions bump, each decision an append-only `lab.rollout_decision.1` with its
-- reasons in 0033's `lab_rollout_events` (ROLLOUT-RECOVER, ROLLOUT-PIN).
-- LOCAL-ONLY (R151): never applied hosted; the number is the next free at merge.
--
-- Mapping: R2's `approved` (an operator's approved expansion) is 0033's `approved` state -
-- live for the one-rollout-per-endpoint rule, routed to the baseline by `lab_rollout_assign`
-- (only `running` buckets, R2's WR-R2-3 rule), and left by D9's own `expand` (to the later
-- version: running), `stop` or `rollback`. R2's `rolled_back` is D9's. `started_at` is the
-- rollout's `start` event; the plan digest is frozen at start.
--
--   lab_rollouts.plan_digest          R2's plan digest (`sha256:<hex>`), written once at start.
--   lab_rollout_events.decision_doc   the stored `lab.rollout_decision.1` (R2's moves).
--   lab_rollout_events.reasons        the verdict's reasons (R2's moves).
--
-- RPCs (SECURITY DEFINER; EXECUTE service_role only through 0004's defaults):
--   lab_release_start {provider_org_id, policy_ref, plan_digest, decided_by, reason}: D9's
--       `lab_rollout_start` and the plan digest, in one transaction -> the release.
--   lab_release {policy_ref}: {state, fence, plan_digest, started_at} (`not_found` when no
--       rollout is at that revision).
--   lab_release_transition {policy_ref, fence, to, decision, reasons}: the fence must be the
--       current one and the move one of R2's three (`state_conflict`); the decision must be
--       a `lab.rollout_decision.1` of this provider and revision whose decision matches the
--       move (`expand` for approved, naming the provider's run records as evidence;
--       `rollback` for rolled_back) -> {fence}.
--
-- ROLLBACK (this file alone): drop function infrx.lab_release_transition(jsonb),
--   infrx.lab_release(jsonb), infrx.lab_release_start(jsonb),
--   infrx.lab_release_json(infrx.lab_rollouts); drop trigger lab_rollouts_plan_frozen on
--   infrx.lab_rollouts; drop function infrx.lab_release_plan_frozen(); alter table
--   infrx.lab_rollout_events drop column reasons, drop column decision_doc; alter table
--   infrx.lab_rollouts drop column plan_digest. (0033's `approved` state stays; a row in it
--   is moved by D9's expand/stop/rollback.)
--
-- Re-runnable: `if not exists`, `create or replace`.

alter table infrx.lab_rollouts
  add column if not exists plan_digest text check (plan_digest ~ '^sha256:[0-9a-f]{64}$');
alter table infrx.lab_rollout_events
  add column if not exists decision_doc jsonb,
  add column if not exists reasons text[] not null default '{}';

create or replace function infrx.lab_release_plan_frozen() returns trigger
language plpgsql set search_path = infrx, public, pg_temp as $$
begin
  if old.plan_digest is not null and new.plan_digest is distinct from old.plan_digest then
    perform infrx.refuse('state_conflict', 'the plan was frozen at launch');
  end if;
  return new;
end $$;
create or replace trigger lab_rollouts_plan_frozen before update of plan_digest
  on infrx.lab_rollouts for each row execute function infrx.lab_release_plan_frozen();

create or replace function infrx.lab_release_json(o infrx.lab_rollouts) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('state', o.state, 'fence', o.fence, 'plan_digest', o.plan_digest,
    'started_at', (select e.at from infrx.lab_rollout_events e
                    where e.policy_id = o.policy_id and e.action = 'start'))
$$;

create or replace function infrx.lab_release_start(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_id uuid;
  o infrx.lab_rollouts%rowtype;
begin
  if coalesce(p_args->>'plan_digest', '') !~ '^sha256:[0-9a-f]{64}$' then
    perform infrx.refuse('invalid_request', 'a release freezes its plan digest at launch');
  end if;
  v_id := (infrx.lab_rollout_start(p_args)->>'policy_id')::uuid;
  update infrx.lab_rollouts set plan_digest = p_args->>'plan_digest' where policy_id = v_id
  returning * into o;
  return infrx.lab_release_json(o);
end $$;

create or replace function infrx.lab_release(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  o infrx.lab_rollouts%rowtype;
begin
  select * into o from infrx.lab_rollouts where policy_ref = p_args->>'policy_ref';
  if not found then
    perform infrx.refuse('not_found', 'no release at this policy revision');
  end if;
  return infrx.lab_release_json(o);
end $$;

create or replace function infrx.lab_release_transition(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_to text := p_args->>'to';
  d jsonb := p_args->'decision';
  v_reasons text[] := array(select jsonb_array_elements_text(coalesce(p_args->'reasons',
                                                                        '[]')));
  v_evidence text[] := array(select jsonb_array_elements_text(coalesce(d->'evidence_refs',
                                                                         '[]')));
  v_from text;
  o infrx.lab_rollouts%rowtype;
begin
  select * into o from infrx.lab_rollouts where policy_ref = p_args->>'policy_ref' for update;
  if not found then
    perform infrx.refuse('not_found', 'no release at this policy revision');
  end if;
  if o.fence is distinct from (p_args->>'fence')::bigint then
    perform infrx.refuse('state_conflict', 'stale fence: the release is at ' || o.fence);
  end if;
  if (o.state, v_to) not in (('running', 'approved'), ('running', 'rolled_back'),
                             ('approved', 'rolled_back')) then
    perform infrx.refuse('state_conflict', 'release: ' || o.state || ' -> '
                         || coalesce(v_to, '?') || ' is not a declared move');
  end if;
  if d->>'schema' is distinct from 'lab.rollout_decision.1'
     or d->>'policy_ref' is distinct from o.policy_ref
     or d->>'provider_org_id' is distinct from o.provider_org_id::text
     or d->>'decision' is distinct from (case v_to when 'approved' then 'expand'
                                                    else 'rollback' end) then
    perform infrx.refuse('invalid_request', 'the decision is this revision''s '
                         || case v_to when 'approved' then 'expand' else 'rollback' end);
  end if;
  if v_to = 'approved' and (cardinality(v_evidence) = 0 or exists (
       select 1 from unnest(v_evidence) x where not exists (
         select 1 from infrx.lab_records r where r.ref = x and r.kind = 'run'
            and r.provider_org_id = o.provider_org_id))) then
    perform infrx.refuse('invalid_request', 'an expansion names the provider''s evaluation '
                         'runs as its evidence');
  end if;
  v_from := o.state;
  update infrx.lab_rollouts set state = v_to, fence = o.fence + 1, updated_at = infrx.now()
   where policy_id = o.policy_id returning * into o;
  begin
    insert into infrx.lab_rollout_events (policy_id, fence, action, from_state, to_state,
      policy_ref, decision, evidence_refs, decided_by, reason, decision_doc, reasons)
    values (o.policy_id, o.fence, case v_to when 'approved' then 'expand' else 'rollback' end,
      v_from, v_to, o.policy_ref, d->>'decision', v_evidence, (d->>'decided_by')::uuid,
      coalesce(nullif(left(array_to_string(v_reasons, '; '), 500), ''), d->>'decision'), d,
      v_reasons);
  exception when not_null_violation or check_violation or invalid_text_representation then
    perform infrx.refuse('invalid_request', 'a decision names its deciding user');
  end;
  return jsonb_build_object('fence', o.fence);
end $$;
