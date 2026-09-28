-- D9 (wave-5 LW2, lane lab-sql; 13-lab-improvement-handoffs §D9; F3 R157/R161/R162): release
-- policies and stable experiment assignment (ROLLOUT-PIN, ROLLOUT-RECOVER).
-- LOCAL-ONLY (R151): never applied hosted; the number is the next free at merge.
--
-- A policy REVISION is 0029's immutable `lab.rollout_policy.1` record (kind `policy`, one row
-- per version, content-addressed). This file adds:
--   lab_rollouts             one live control row per policy id: its endpoint, the policy
--                            version in force, state (running | paused | approved |
--                            stopped | rolled_back; `approved` is R2's operator-approved
--                            expansion, set only by 0039's release store, awaiting D9's
--                            expand) and a FENCE every transition compares and bumps
--                            (CAS: a stale publisher's move is `state_conflict`). At most
--                            one live (running, paused or approved) rollout per endpoint.
--   lab_rollout_events       append-only: every start and transition with its decision
--                            (expand | hold | rollback, F3 RolloutDecision), evidence run
--                            refs, deciding user and reason. A rollback is a new decision,
--                            never an edit.
--   lab_rollout_assignments  one per (policy, request): the policy version, cohort digest,
--                            serving ref and pin kind the request was given - immutable, so a
--                            retry is the same answer and nothing re-routes an admitted
--                            request. No raw subject is stored: the cohort digest is
--                            sha256(policy_id "\n" subject) (R162).
--
-- Named RPCs (SECURITY DEFINER; EXECUTE for service_role only through 0004's defaults):
--   lab_rollout_start {provider_org_id, policy_ref, decided_by, reason}: runs the caller's
--       own policy version (validated: weights within 10000 bp, only a canary carries
--       candidate weight); a second start of the policy, or a second live rollout of the
--       endpoint, is `state_conflict`.
--   lab_rollout_transition {provider_org_id, policy_id, fence, action, decided_by, reason,
--       policy_ref?, evidence_refs?}: pause (running -> paused, decision hold), resume
--       (paused -> running), expand (running, a LATER version of the same policy, decision
--       expand with at least one of the provider's run records as evidence), stop and
--       rollback (running | paused -> stopped | rolled_back, rollback a decision). The
--       fence must be the current one.
--   lab_rollout_assign {provider_org_id, policy_id, request_id, subject_key,
--       explicit_serving_ref?}: an explicit pin (a serving ref of the provider) is honoured
--       as given; otherwise, while running, the R162 bucket over the version's candidates in
--       order, else the baseline. Once assigned, the same answer for that request forever.
--   lab_rollout {provider_org_id, policy_id}: the row and its events.
--
-- ROLLBACK (this file alone; nothing earlier references it): drop function
--   infrx.lab_rollout(jsonb), infrx.lab_rollout_assign(jsonb),
--   infrx.lab_rollout_transition(jsonb), infrx.lab_rollout_start(jsonb),
--   infrx.lab_rollout_json(infrx.lab_rollouts), infrx.lab_rollout_policy(text, uuid); drop
--   table infrx.lab_rollout_assignments, infrx.lab_rollout_events, infrx.lab_rollouts.
--
-- Re-runnable: `if not exists`, `create or replace`.

create table if not exists infrx.lab_rollouts (
  policy_id uuid primary key,
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  endpoint_id uuid not null,
  policy_ref text not null references infrx.lab_records on delete restrict,
  state text not null check (state in ('running', 'paused', 'approved', 'stopped',
    'rolled_back')),
  fence bigint not null check (fence >= 1),
  updated_at timestamptz not null default infrx.now()
);
create unique index if not exists lab_rollouts_one_live_per_endpoint
  on infrx.lab_rollouts (endpoint_id) where state in ('running', 'paused', 'approved');

create table if not exists infrx.lab_rollout_events (
  policy_id uuid not null references infrx.lab_rollouts on delete restrict,
  fence bigint not null,
  action text not null
    check (action in ('start', 'pause', 'resume', 'expand', 'stop', 'rollback')),
  from_state text,
  to_state text not null,
  policy_ref text not null,
  decision text check (decision in ('expand', 'hold', 'rollback')),
  evidence_refs text[] not null default '{}',
  decided_by uuid not null,
  reason text not null check (length(btrim(reason)) between 1 and 500),
  at timestamptz not null default infrx.now(),
  primary key (policy_id, fence)
);

create table if not exists infrx.lab_rollout_assignments (
  policy_id uuid not null references infrx.lab_rollouts on delete restrict,
  request_id uuid not null,
  policy_ref text not null,
  cohort_digest text not null check (cohort_digest ~ '^sha256:[0-9a-f]{64}$'),
  serving_ref text not null,
  pinned_by text not null check (pinned_by in ('cohort', 'explicit')),
  assigned_at timestamptz not null default infrx.now(),
  primary key (policy_id, request_id)
);

do $$
declare
  t text;
begin
  foreach t in array array['lab_rollout_events', 'lab_rollout_assignments'] loop
    execute format('create or replace trigger %I before update or delete on infrx.%I '
                   'for each row execute function infrx.forbid_update_delete()',
                   t || '_immutable', t);
  end loop;
  foreach t in array array['lab_rollouts', 'lab_rollout_events', 'lab_rollout_assignments'] loop
    execute format('create or replace trigger %I before truncate on infrx.%I '
                   'for each statement execute function infrx.forbid_truncate()',
                   t || '_no_truncate', t);
    execute format('alter table infrx.%I enable row level security', t);
    execute format('revoke all on infrx.%I from public, anon, authenticated, service_role', t);
    execute format('grant select on infrx.%I to service_role', t);
  end loop;
end $$;

-- The caller's own, well-formed policy version, as its body (else a refusal).
create or replace function infrx.lab_rollout_policy(p_ref text, p_provider uuid)
returns jsonb language plpgsql stable security definer
set search_path = infrx, public, pg_temp as $$
declare
  v_doc jsonb;
begin
  select r.body::jsonb into v_doc from infrx.lab_records r
   where r.ref = p_ref and r.provider_org_id = p_provider and r.kind = 'policy';
  if v_doc is null then
    perform infrx.refuse('not_found', 'no such rollout policy for this provider');
  end if;
  if (select coalesce(sum((c->>'weight_bp')::int), 0)
        from jsonb_array_elements(v_doc->'candidates') c) > 10000
     or (v_doc->>'mode' <> 'canary' and exists (
           select 1 from jsonb_array_elements(v_doc->'candidates') c
            where (c->>'weight_bp')::int > 0)) then
    perform infrx.refuse('invalid_request', 'candidate weights exceed 10000 bp, or a mode '
                         'other than canary carries candidate traffic');
  end if;
  return v_doc;
end $$;

create or replace function infrx.lab_rollout_json(o infrx.lab_rollouts) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('policy_id', o.policy_id, 'endpoint_id', o.endpoint_id,
    'policy_ref', o.policy_ref, 'state', o.state, 'fence', o.fence,
    'events', (select coalesce(jsonb_agg(jsonb_build_object('fence', e.fence,
                 'action', e.action, 'from_state', e.from_state, 'to_state', e.to_state,
                 'policy_ref', e.policy_ref, 'decision', e.decision,
                 'evidence_refs', to_jsonb(e.evidence_refs), 'decided_by', e.decided_by,
                 'reason', e.reason, 'at', e.at) order by e.fence), '[]')
                 from infrx.lab_rollout_events e where e.policy_id = o.policy_id))
$$;

create or replace function infrx.lab_rollout_start(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_provider uuid := (p_args->>'provider_org_id')::uuid;
  v_doc jsonb := infrx.lab_rollout_policy(p_args->>'policy_ref', v_provider);
  o infrx.lab_rollouts%rowtype;
begin
  begin
    insert into infrx.lab_rollouts (policy_id, provider_org_id, endpoint_id, policy_ref, state,
      fence)
    values ((v_doc->>'policy_id')::uuid, v_provider, (v_doc->>'endpoint_id')::uuid,
      p_args->>'policy_ref', 'running', 1)
    returning * into o;
    insert into infrx.lab_rollout_events (policy_id, fence, action, to_state, policy_ref,
      decided_by, reason)
    values (o.policy_id, 1, 'start', 'running', o.policy_ref,
      (p_args->>'decided_by')::uuid, p_args->>'reason');
  exception when unique_violation then
    perform infrx.refuse('state_conflict', 'the policy already has a rollout, or the endpoint '
                         'a live one');
  when not_null_violation or check_violation or invalid_text_representation then
    perform infrx.refuse('invalid_request', 'a start names its deciding user and reason');
  end;
  return infrx.lab_rollout_json(o);
end $$;

create or replace function infrx.lab_rollout_transition(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_action text := p_args->>'action';
  v_evidence text[] := array(select jsonb_array_elements_text(
                               coalesce(p_args->'evidence_refs', '[]')));
  v_to text;
  v_from text;
  v_ref text;
  v_doc jsonb;
  o infrx.lab_rollouts%rowtype;
begin
  select * into o from infrx.lab_rollouts
   where policy_id = (p_args->>'policy_id')::uuid
     and provider_org_id = (p_args->>'provider_org_id')::uuid for update;
  if not found then
    perform infrx.refuse('not_found', 'no such rollout for this provider');
  end if;
  if o.fence is distinct from (p_args->>'fence')::bigint then
    perform infrx.refuse('state_conflict', 'stale fence: the rollout is at ' || o.fence);
  end if;
  v_to := case
    when v_action = 'pause' and o.state = 'running' then 'paused'
    when v_action = 'resume' and o.state = 'paused' then 'running'
    when v_action = 'expand' and o.state in ('running', 'approved') then 'running'
    when v_action = 'stop' and o.state in ('running', 'paused', 'approved') then 'stopped'
    when v_action = 'rollback' and o.state in ('running', 'paused', 'approved')
      then 'rolled_back' end;
  if v_to is null then
    perform infrx.refuse('state_conflict', 'rollout: ' || coalesce(v_action, '?')
                         || ' from ' || o.state || ' is not a declared transition');
  end if;
  v_ref := o.policy_ref;
  if v_action = 'expand' then
    v_doc := infrx.lab_rollout_policy(p_args->>'policy_ref', o.provider_org_id);
    if (v_doc->>'policy_id')::uuid <> o.policy_id
       or (v_doc->>'version')::int <= (select (r.body::jsonb->>'version')::int
                                          from infrx.lab_records r where r.ref = o.policy_ref)
    then
      perform infrx.refuse('invalid_request', 'an expansion is a later version of the same '
                           'policy');
    end if;
    if cardinality(v_evidence) = 0 or exists (
         select 1 from unnest(v_evidence) x where not exists (
           select 1 from infrx.lab_records r where r.ref = x and r.kind = 'run'
              and r.provider_org_id = o.provider_org_id)) then
      perform infrx.refuse('invalid_request', 'an expansion names the provider''s evaluation '
                           'runs as its evidence');
    end if;
    v_ref := p_args->>'policy_ref';
  end if;
  v_from := o.state;
  update infrx.lab_rollouts set state = v_to, policy_ref = v_ref, fence = o.fence + 1,
    updated_at = infrx.now()
   where policy_id = o.policy_id returning * into o;
  begin
    insert into infrx.lab_rollout_events (policy_id, fence, action, from_state, to_state,
      policy_ref, decision, evidence_refs, decided_by, reason)
    values (o.policy_id, o.fence, v_action, v_from, v_to, v_ref,
      case v_action when 'expand' then 'expand' when 'pause' then 'hold'
                    when 'rollback' then 'rollback' end,
      case when v_action = 'expand' then v_evidence else '{}' end,
      (p_args->>'decided_by')::uuid, p_args->>'reason');
  exception when not_null_violation or check_violation or invalid_text_representation then
    perform infrx.refuse('invalid_request', 'a transition names its deciding user and reason');
  end;
  return infrx.lab_rollout_json(o);
end $$;

create or replace function infrx.lab_rollout_assign(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_explicit text := p_args->>'explicit_serving_ref';
  v_parts text[] := infrx.lab_ref_parts(p_args->>'explicit_serving_ref');
  v_doc jsonb;
  v_digest text;
  v_bucket int;
  o infrx.lab_rollouts%rowtype;
  a infrx.lab_rollout_assignments%rowtype;
begin
  select * into o from infrx.lab_rollouts
   where policy_id = (p_args->>'policy_id')::uuid
     and provider_org_id = (p_args->>'provider_org_id')::uuid for share;
  if not found then
    perform infrx.refuse('not_found', 'no such rollout for this provider');
  end if;
  if v_explicit is not null and (v_parts[1] is distinct from 'serving'
                                 or v_parts[2] is distinct from o.provider_org_id::text) then
    perform infrx.refuse('not_found', 'no such serving revision for this provider');
  end if;
  v_doc := (select r.body::jsonb from infrx.lab_records r where r.ref = o.policy_ref);
  v_digest := encode(sha256(convert_to((v_doc->>'policy_id') || E'\n'
                                       || (p_args->>'subject_key'), 'UTF8')), 'hex');
  v_bucket := (('x' || substr(v_digest, 1, 8))::bit(32)::bigint % 10000)::int;
  insert into infrx.lab_rollout_assignments (policy_id, request_id, policy_ref, cohort_digest,
    serving_ref, pinned_by)
  values (o.policy_id, (p_args->>'request_id')::uuid, o.policy_ref, 'sha256:' || v_digest,
    coalesce(v_explicit, case when o.state = 'running' then (
      select c.value->>'serving_ref' from jsonb_array_elements(v_doc->'candidates')
             with ordinality c(value, n)
       where v_bucket < (select sum((w.value->>'weight_bp')::int)
                           from jsonb_array_elements(v_doc->'candidates')
                                with ordinality w(value, m) where w.m <= c.n)
       order by c.n limit 1) end, v_doc->>'baseline_ref'),
    case when v_explicit is null then 'cohort' else 'explicit' end)
  on conflict (policy_id, request_id) do nothing
  returning * into a;
  if a.request_id is null then                    -- a retry (or a racing one): its answer
    select * into a from infrx.lab_rollout_assignments
     where policy_id = o.policy_id and request_id = (p_args->>'request_id')::uuid;
  end if;
  return to_jsonb(a) - 'assigned_at';
exception when not_null_violation or invalid_text_representation then
  perform infrx.refuse('invalid_request', 'an assignment names its request and subject');
  return null;
end $$;

create or replace function infrx.lab_rollout(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  o infrx.lab_rollouts%rowtype;
begin
  select * into o from infrx.lab_rollouts where policy_id = (p_args->>'policy_id')::uuid
     and provider_org_id = (p_args->>'provider_org_id')::uuid;
  if not found then
    perform infrx.refuse('not_found', 'no such rollout for this provider');
  end if;
  return infrx.lab_rollout_json(o);
end $$;
