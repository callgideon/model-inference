-- D6J (wave-5 LW2, lane lab-sql; 09-amendment-workstreams §D6J; handoffs/D-durable-state D6;
-- F3 R159-R161): Lab consent snapshots, PROVIDER_USD budget reservations with a named payer,
-- and the external submission protocol reserve -> intent -> ack, with an ambiguous outcome
-- quarantined and reconciled by lookup, never resubmitted (JUDGE-BUDGET, LAB-ACCESS).
-- LOCAL-ONLY (R151): never applied hosted; the number is the next free at merge.
--
--   lab_budgets          one per (provider, payer ref): the limit, what outstanding
--                        submissions hold and what finished ones spent, in PROVIDER_USD only
--                        (R159; never a consumer CREDIT wallet). `reserved + settled <= limit`
--                        is a CHECK on the row every reservation updates, so concurrent
--                        reservations serialize on it and the one past the cap fails
--                        (`budget_exceeded`), outstanding submissions included.
--   lab_budget_limits    every limit ever set, with its actor and reason (append-only).
--   lab_submissions      one per published `lab.external_run.1` record: its purpose, payer,
--                        reservation, the ONE submit intent (`submit:<external_run_id>`, R161)
--                        and the contract's external_run states; `ambiguous` carries its
--                        quarantine reason.
--   lab_submission_consents  the consent snapshot: every grant of the run's dataset at the
--                        version current when it was prepared (append-only).
--
-- Protocol (RPCs; SECURITY DEFINER, EXECUTE service_role only through 0004's defaults):
--   lab_put_budget {provider_org_id, payer_ref, limit, actor, reason}: the payer is a
--       `lab:payer` ref of THIS provider; a limit below what is held and spent is refused.
--   lab_budget {provider_org_id, payer_ref}: the row.
--   lab_prepare_submission {provider_org_id, external_run_ref, actor}: the caller's own
--       external run record; every grant of its dataset must be current for the run's OWN
--       purpose (R160; else `consent_missing`) and is snapshotted; the run's budget limit is
--       reserved against its payer's budget. A replay answers the same submission.
--   lab_submission_transition {provider_org_id, external_run_id, state, ...}:
--       submitting  the intent, persisted BEFORE egress: only from `prepared`, and only if
--                   every snapshotted grant is still at its snapshot version and current for
--                   the purpose (a revocation, a re-grant or an expiry since preparation is
--                   `consent_missing`). A second attempt - a duplicate under contention, a
--                   retry after a lost answer, a retry of an ambiguous submit - is
--                   `ambiguous_submission`: nothing ever submits twice.
--       submitted   with the connector's `external_batch_id` (from submitting or, by lookup,
--                   from ambiguous).
--       ambiguous   with a `reason`: the quarantine of an unknown outcome.
--       failed | cancelled | completed   the reservation is released and `cost` (default 0;
--                   required for completed; PROVIDER_USD, at most the reservation) settled.
--       A replay of the move already made answers the same row; other fields are
--       `idempotency_conflict`. Any other move is `state_conflict` (a trigger enforces the
--       contract's table on every write).
--   lab_submission {provider_org_id, external_run_id}: the row and its consent snapshot.
-- Enablement is its own flag `lab_submission` (09 §D6J; separate from D6F's `feedback`): OFF,
-- and a missing row is as closed as a disabled one; prepare and every move then raise 55000
-- (`dependency_unavailable`). No row is added here.
--
-- ROLLBACK (this file alone; nothing earlier references it): drop function
--   infrx.lab_submission(jsonb), infrx.lab_submission_transition(jsonb),
--   infrx.lab_prepare_submission(jsonb), infrx.lab_budget(jsonb), infrx.lab_put_budget(jsonb),
--   infrx.lab_submission_json(infrx.lab_submissions), infrx.lab_budget_json(infrx.lab_budgets),
--   infrx.lab_submission_guard(), infrx.lab_submission_may(text, text); drop table
--   infrx.lab_submission_consents, infrx.lab_submissions, infrx.lab_budget_limits,
--   infrx.lab_budgets (in this order); restore feature_flags_name_check without
--   'lab_submission' (after deleting any such flag row).
--
-- Re-runnable: `if not exists`, `create or replace`, `drop ... if exists` before each re-add.

-- ================================================================ enablement ===
alter table infrx.feature_flags drop constraint if exists feature_flags_name_check;
alter table infrx.feature_flags add constraint feature_flags_name_check
  check (name in ('signup_grant', 'credit_admission', 'legacy_usd_admission', 'feedback',
                  'lab_submission'));

-- ==================================================================== tables ===
create table if not exists infrx.lab_budgets (
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  payer_ref text not null,
  unit text not null default 'PROVIDER_USD' check (unit = 'PROVIDER_USD'),
  limit_value numeric(20, 8) not null check (limit_value >= 0),
  reserved numeric(20, 8) not null default 0 check (reserved >= 0),
  settled numeric(20, 8) not null default 0 check (settled >= 0),
  updated_at timestamptz not null default infrx.now(),
  primary key (provider_org_id, payer_ref),
  constraint lab_budgets_within_limit check (reserved + settled <= limit_value)
);

create table if not exists infrx.lab_budget_limits (
  provider_org_id uuid not null,
  payer_ref text not null,
  version int not null check (version >= 1),
  limit_value numeric(20, 8) not null,
  actor text not null check (length(btrim(actor)) between 1 and 200),
  reason text not null check (length(btrim(reason)) between 1 and 500),
  set_at timestamptz not null default infrx.now(),
  primary key (provider_org_id, payer_ref, version),
  foreign key (provider_org_id, payer_ref) references infrx.lab_budgets on delete restrict
);

create table if not exists infrx.lab_submissions (
  external_run_id uuid primary key,
  provider_org_id uuid not null,
  external_run_ref text not null unique references infrx.lab_records on delete restrict,
  purpose text not null check (purpose in ('external_judging', 'training')),
  payer_ref text not null,
  reserved numeric(20, 8) not null check (reserved >= 0),
  settled numeric(20, 8) check (settled >= 0 and settled <= reserved),
  state text not null default 'prepared' check (state in ('prepared', 'submitting',
    'ambiguous', 'submitted', 'completed', 'failed', 'cancelled')),
  intent_id uuid unique,
  intent_at timestamptz,
  external_batch_id text check (length(btrim(external_batch_id)) between 1 and 200),
  quarantine_reason text check (length(btrim(quarantine_reason)) between 1 and 500),
  prepared_by text not null check (length(btrim(prepared_by)) between 1 and 200),
  created_at timestamptz not null default infrx.now(),
  updated_at timestamptz not null default infrx.now(),
  foreign key (provider_org_id, payer_ref) references infrx.lab_budgets on delete restrict,
  -- the intent is persisted before egress: every state past `prepared` but cancelling one
  constraint lab_submissions_intent_before_egress
    check (state in ('prepared', 'cancelled') or intent_id is not null),
  constraint lab_submissions_accepted_has_batch
    check (state not in ('submitted', 'completed') or external_batch_id is not null),
  constraint lab_submissions_quarantine_has_reason
    check (state <> 'ambiguous' or quarantine_reason is not null),
  constraint lab_submissions_settled_when_final
    check ((state in ('completed', 'failed', 'cancelled')) = (settled is not null))
);

create table if not exists infrx.lab_submission_consents (
  external_run_id uuid not null references infrx.lab_submissions on delete restrict,
  grant_id uuid not null,
  grant_version int not null check (grant_version >= 1),
  primary key (external_run_id, grant_id)
);

-- ================================================================ state guard ===
-- The contract's external_run table (states.TRANSITIONS["external_run"]); parity is asserted.
create or replace function infrx.lab_submission_may(p_from text, p_to text) returns boolean
language sql immutable set search_path = infrx, public, pg_temp as $$
  select (p_from, p_to) in (('prepared', 'submitting'), ('prepared', 'cancelled'),
    ('submitting', 'submitted'), ('submitting', 'ambiguous'), ('submitting', 'failed'),
    ('ambiguous', 'submitted'), ('ambiguous', 'failed'),
    ('submitted', 'completed'), ('submitted', 'failed'), ('submitted', 'cancelled'))
$$;

create or replace function infrx.lab_submission_guard() returns trigger
language plpgsql set search_path = infrx, public, pg_temp as $$
begin
  if new.state is distinct from old.state
     and not infrx.lab_submission_may(old.state, new.state) then
    perform infrx.refuse('state_conflict', 'external_run: ' || old.state || ' -> '
                         || new.state || ' is not a declared transition');
  end if;
  new.updated_at := infrx.now();
  return new;
end $$;

do $$
declare
  t text;
begin
  execute 'create or replace trigger lab_submissions_state before update on '
          'infrx.lab_submissions for each row execute function infrx.lab_submission_guard()';
  foreach t in array array['lab_budget_limits', 'lab_submission_consents'] loop
    execute format('create or replace trigger %I before update or delete on infrx.%I '
                   'for each row execute function infrx.forbid_update_delete()',
                   t || '_immutable', t);
  end loop;
  foreach t in array array['lab_budgets', 'lab_budget_limits', 'lab_submissions',
                           'lab_submission_consents'] loop
    execute format('create or replace trigger %I before truncate on infrx.%I '
                   'for each statement execute function infrx.forbid_truncate()',
                   t || '_no_truncate', t);
    execute format('alter table infrx.%I enable row level security', t);
    execute format('revoke all on infrx.%I from public, anon, authenticated, service_role', t);
    execute format('grant select on infrx.%I to service_role', t);
  end loop;
end $$;

-- ==================================================================== shapes ===
create or replace function infrx.lab_budget_json(b infrx.lab_budgets) returns jsonb
language sql stable set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('provider_org_id', b.provider_org_id, 'payer_ref', b.payer_ref,
    'unit', b.unit, 'limit', b.limit_value::text, 'reserved', b.reserved::text,
    'settled', b.settled::text)
$$;

create or replace function infrx.lab_submission_json(s infrx.lab_submissions) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('external_run_id', s.external_run_id,
    'external_run_ref', s.external_run_ref, 'purpose', s.purpose, 'payer_ref', s.payer_ref,
    'reserved', s.reserved::text, 'settled', s.settled::text, 'state', s.state,
    'submit_key', 'submit:' || s.external_run_id, 'intent_id', s.intent_id,
    'external_batch_id', s.external_batch_id, 'quarantine_reason', s.quarantine_reason,
    'consents', (select coalesce(jsonb_agg(jsonb_build_object('grant_id', c.grant_id,
                   'grant_version', c.grant_version) order by c.grant_id), '[]')
                   from infrx.lab_submission_consents c
                  where c.external_run_id = s.external_run_id))
$$;

-- ====================================================================== RPCs ===
create or replace function infrx.lab_put_budget(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_provider uuid := (p_args->>'provider_org_id')::uuid;
  v_payer text := p_args->>'payer_ref';
  v_parts text[] := infrx.lab_ref_parts(p_args->>'payer_ref');
  b infrx.lab_budgets%rowtype;
begin
  if v_parts is null or v_parts[1] <> 'payer' then
    perform infrx.refuse('invalid_request', 'a budget is a lab:payer ref');
  end if;
  if v_parts[2] <> v_provider::text then
    perform infrx.refuse('not_found', 'no such payer for this provider');
  end if;
  begin
    insert into infrx.lab_budgets (provider_org_id, payer_ref, limit_value)
    values (v_provider, v_payer, (p_args->>'limit')::numeric)
    on conflict (provider_org_id, payer_ref) do update
      set limit_value = excluded.limit_value, updated_at = infrx.now()
    returning * into b;
    insert into infrx.lab_budget_limits (provider_org_id, payer_ref, version, limit_value,
      actor, reason)
    select v_provider, v_payer, coalesce(max(l.version), 0) + 1, b.limit_value,
           p_args->>'actor', p_args->>'reason'
      from infrx.lab_budget_limits l where (l.provider_org_id, l.payer_ref) = (v_provider, v_payer);
  exception when check_violation then
    if sqlerrm like '%lab_budgets_within_limit%' then
      perform infrx.refuse('budget_exceeded', 'the limit is below what is held and spent');
    end if;
    perform infrx.refuse('invalid_request', 'a limit is a non-negative PROVIDER_USD amount '
                         'set by a named actor for a reason');
  when not_null_violation or invalid_text_representation or numeric_value_out_of_range
       or foreign_key_violation then
    perform infrx.refuse('invalid_request', 'a limit is a non-negative PROVIDER_USD amount '
                         'set by a named actor for a reason');
  end;
  return infrx.lab_budget_json(b);
end $$;

create or replace function infrx.lab_budget(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  b infrx.lab_budgets%rowtype;
begin
  select * into b from infrx.lab_budgets
   where provider_org_id = (p_args->>'provider_org_id')::uuid
     and payer_ref = p_args->>'payer_ref';
  if not found then
    perform infrx.refuse('not_found', 'no budget for this payer');
  end if;
  return infrx.lab_budget_json(b);
end $$;

create or replace function infrx.lab_prepare_submission(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_provider uuid := (p_args->>'provider_org_id')::uuid;
  r infrx.lab_records%rowtype;
  v_doc jsonb;
  v_amount numeric;
  s infrx.lab_submissions%rowtype;
begin
  perform infrx.require_feature('lab_submission');
  select * into r from infrx.lab_records where ref = p_args->>'external_run_ref'
     and provider_org_id = v_provider and kind = 'external_run';
  if not found then
    perform infrx.refuse('not_found', 'no such external run for this provider');
  end if;
  select * into s from infrx.lab_submissions where external_run_ref = r.ref;
  if found then
    return infrx.lab_submission_json(s);            -- a replay: the one submission
  end if;
  v_doc := r.body::jsonb;
  if v_doc->'budget'->'limit'->>'unit' is distinct from 'PROVIDER_USD' then
    perform infrx.refuse('invalid_request', 'external work is PROVIDER_USD with a named payer');
  end if;
  v_amount := (v_doc->'budget'->'limit'->>'value')::numeric;
  -- the consent gate (R160: the run's own purpose) over every grant of the dataset
  if exists (select 1 from infrx.lab_dataset_samples x
              where x.dataset_ref = v_doc->>'dataset_ref'
                and not infrx.lab_grant_current(x.grant_id, v_doc->>'purpose')) then
    perform infrx.refuse('consent_missing', 'a grant of the dataset is not in force for '
                         || (v_doc->>'purpose'));
  end if;
  begin
    insert into infrx.lab_submissions (external_run_id, provider_org_id, external_run_ref,
      purpose, payer_ref, reserved, prepared_by)
    values (r.object_id, v_provider, r.ref, v_doc->>'purpose', v_doc->'budget'->>'payer_ref',
      v_amount, p_args->>'actor')
    on conflict (external_run_id) do nothing
    returning * into s;
  exception when foreign_key_violation then
    perform infrx.refuse('not_found', 'no budget for this payer');
  when check_violation or not_null_violation then
    perform infrx.refuse('invalid_request', 'an external run names its purpose, an amount '
                         'and the actor preparing it');
  end;
  if s.external_run_id is null then
    -- a concurrent preparation of this run won (its answer is ours), or another provider's
    -- run holds this id (ids are global): then it is an unknown run to this caller
    select * into s from infrx.lab_submissions where external_run_ref = r.ref;
    if not found then
      perform infrx.refuse('not_found', 'no such external run for this provider');
    end if;
    return infrx.lab_submission_json(s);
  end if;
  begin
    update infrx.lab_budgets set reserved = reserved + v_amount, updated_at = infrx.now()
     where (provider_org_id, payer_ref) = (v_provider, s.payer_ref);
  exception when check_violation then
    perform infrx.refuse('budget_exceeded', 'the reservation exceeds the payer''s budget');
  end;
  insert into infrx.lab_submission_consents (external_run_id, grant_id, grant_version)
  select s.external_run_id, x.grant_id,
         (select max(g.version) from infrx.lab_access_grants g where g.grant_id = x.grant_id)
    from (select distinct d.grant_id from infrx.lab_dataset_samples d
           where d.dataset_ref = v_doc->>'dataset_ref') x;
  return infrx.lab_submission_json(s);
end $$;

create or replace function infrx.lab_submission_transition(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_to text := p_args->>'state';
  v_batch text := p_args->>'external_batch_id';
  v_cost numeric;
  s infrx.lab_submissions%rowtype;
begin
  perform infrx.require_feature('lab_submission');
  select * into s from infrx.lab_submissions
   where external_run_id = (p_args->>'external_run_id')::uuid
     and provider_org_id = (p_args->>'provider_org_id')::uuid for update;
  if not found then
    perform infrx.refuse('not_found', 'no such external run for this provider');
  end if;
  if v_to = 'submitting' and s.state in ('submitting', 'ambiguous') then
    perform infrx.refuse('ambiguous_submission', 'submit:' || s.external_run_id || ' is '
                         || s.state || ': reconcile by lookup, never resubmit');
  end if;
  if p_args ? 'cost' and p_args->'cost'->>'unit' is distinct from 'PROVIDER_USD' then
    perform infrx.refuse('invalid_request', 'a submission''s cost is PROVIDER_USD');
  end if;
  v_cost := coalesce((p_args->'cost'->>'value')::numeric, 0);
  if s.state = v_to then
    -- the move already made, retried after a lost answer: the same row, nothing moves
    if (v_to = 'submitted' and s.external_batch_id is distinct from v_batch)
       or (v_to in ('completed', 'failed', 'cancelled') and s.settled <> v_cost) then
      perform infrx.refuse('idempotency_conflict', 'the run is already ' || v_to
                           || ' with other details');
    end if;
    return infrx.lab_submission_json(s);
  end if;
  -- (the guard trigger refuses it too, but only after a settlement touched the budget)
  if not infrx.lab_submission_may(s.state, v_to) then
    perform infrx.refuse('state_conflict', 'external_run: ' || s.state || ' -> '
                         || coalesce(v_to, '?') || ' is not a declared transition');
  end if;
  if v_to = 'submitting' then
    -- consent snapshot AND current permission, immediately before the intent (egress)
    if exists (select 1 from infrx.lab_submission_consents c
                where c.external_run_id = s.external_run_id
                  and (c.grant_version <> (select max(g.version) from infrx.lab_access_grants g
                                            where g.grant_id = c.grant_id)
                       or not infrx.lab_grant_current(c.grant_id, s.purpose))) then
      perform infrx.refuse('consent_missing', 'a snapshotted grant changed or is no longer '
                           'in force for ' || s.purpose);
    end if;
    update infrx.lab_submissions set state = v_to, intent_id = gen_random_uuid(),
      intent_at = infrx.now() where external_run_id = s.external_run_id returning * into s;
  elsif v_to = 'submitted' then
    if v_batch is null then
      perform infrx.refuse('invalid_request', 'an accepted submission names its batch');
    end if;
    update infrx.lab_submissions set state = v_to, external_batch_id = v_batch
     where external_run_id = s.external_run_id returning * into s;
  elsif v_to = 'ambiguous' then
    if p_args->>'reason' is null then
      perform infrx.refuse('invalid_request', 'a quarantine names its reason');
    end if;
    update infrx.lab_submissions set state = v_to, quarantine_reason = p_args->>'reason'
     where external_run_id = s.external_run_id returning * into s;
  else
    if v_to = 'completed' and not p_args ? 'cost' then
      perform infrx.refuse('invalid_request', 'a completed submission settles its cost');
    end if;
    if v_cost > 0 and s.state <> 'submitted' then
      perform infrx.refuse('invalid_request', 'only accepted work has a cost');
    end if;
    if v_cost < 0 or v_cost > s.reserved then
      perform infrx.refuse('budget_exceeded', 'the cost is outside the reservation');
    end if;
    update infrx.lab_budgets set reserved = reserved - s.reserved, settled = settled + v_cost,
      updated_at = infrx.now()
     where (provider_org_id, payer_ref) = (s.provider_org_id, s.payer_ref);
    update infrx.lab_submissions set state = v_to, settled = v_cost
     where external_run_id = s.external_run_id returning * into s;
  end if;
  return infrx.lab_submission_json(s);
exception when invalid_text_representation or numeric_value_out_of_range then
  perform infrx.refuse('invalid_request', 'a cost is a PROVIDER_USD amount');
  return null;
end $$;

create or replace function infrx.lab_submission(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  s infrx.lab_submissions%rowtype;
begin
  select * into s from infrx.lab_submissions
   where external_run_id = (p_args->>'external_run_id')::uuid
     and provider_org_id = (p_args->>'provider_org_id')::uuid;
  if not found then
    perform infrx.refuse('not_found', 'no such external run for this provider');
  end if;
  return infrx.lab_submission_json(s);
end $$;
