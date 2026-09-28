-- SR-J2-1 (wave-5 LW2 request from the judge lane's J2; lane lab-sql, D6J): the judge ledger
-- J2's `submit.JudgeLedger` port consumes - consented, PROVIDER_USD-budgeted judge runs with
-- one submit intent, quarantine on an unknown outcome, results stored once per
-- (run, sample, rubric version) and settled once (JUDGE-BUDGET, JUDGE-SCORES, LAB-ACCESS).
-- LOCAL-ONLY (R151): never applied hosted; the number is the next free at merge.
--
-- ONE schema with D6J (0031): SR-J2-1's `lab_judge_budgets(payer_ref, provider_org_id,
-- limit_value, unit)` IS 0031's `lab_budgets` (one row per provider and `lab:payer` ref,
-- PROVIDER_USD only, `reserved + settled <= limit` as a CHECK on the row every reservation
-- updates), so a payer's judge runs and its external submissions draw on one cap. A run's
-- `reserved` moves into the budget's `reserved` at `reserve`, out at release or settle, and its
-- `actual` into `settled` - SR-J2-1's "committed = held reservations + completed actuals".
-- The states are the frozen `external_run` machine (0031's `lab_submission_may` and
-- `lab_submission_guard`, reused), and the enablement flag is D6J's `lab_submission` (OFF;
-- a missing row is as closed as a disabled one): every write raises 55000 while it is off.
--
--   lab_judge_runs     one per J2 run: the consent snapshot (grant id + version), the samples
--                      and media, the samples that actually left (set once, before egress),
--                      the price version, the reservation, the ONE submit key
--                      (`submit:<run_id>`), the provider's batch id and the settled actual.
--   lab_judge_results  one per (run, sample, rubric version) - accepted scores or a bounded
--                      rejection; `label_id` is the calibration read's keyset (0037).
--   lab_judge_audit    append-only: quarantines, releases, refused settlements, lease expiry.
--
-- RPCs (SECURITY DEFINER; EXECUTE service_role only through 0004's defaults; `{...}` in, the
-- run row out; refusals `<code>: detail`):
--   lab_judge_reserve {run_id, provider_org_id, payer_ref, grant_id, grant_version,
--       sample_ids, media_ids, price_version, max_cost}: the snapshot must be the grantor's
--       CURRENT grant version to this provider for external_judging over request_content AND
--       response_content (`consent_missing`); a payer without a budget of this provider, or a
--       hold past the cap, is `budget_exceeded`; idempotent per run id (another provider's
--       run id is `not_found`).
--   lab_judge_begin_submit {run_id}: prepared -> submitting with the submit key -> {run,
--       created}; any later call answers {run, created: false} (nothing submits twice).
--   lab_judge_record_sent {run_id, sample_ids}: while submitting, once (a replay of the same
--       ids answers the row); a subset of the run's samples (`state_conflict`); the snapshot
--       grant still current (`consent_missing`: a revocation since the reservation stops
--       egress here even if the caller's own recheck raced it).
--   lab_judge_record_submission {run_id, external_id}: submitting | ambiguous -> submitted;
--       the same id again is the row; another id is `idempotency_conflict`.
--   lab_judge_quarantine {run_id, reason}: submitting -> ambiguous, hold kept, audited.
--   lab_judge_release {run_id, state, reason}: -> failed | cancelled by a declared move; the
--       hold stops counting; audited.
--   lab_judge_record_results {run_id, results: [{sample_id, rubric_version, accepted,
--       result}]}: only samples that left; duplicates ignored -> {inserted}.
--   lab_judge_settle {run_id, actual}: submitted -> completed once (a completed run answers
--       its row); an actual above the reservation is refused as {refused: budget_exceeded}
--       AFTER an audit row is committed (the call returns, the adapter raises), so the
--       overrun is on record while the run stays submitted for an operator.
--   lab_judge_run {run_id}: the row or null.
--   lab_judge_sweep {older_than_s}: `submitting` runs untouched that long (a dead worker)
--       -> ambiguous, audited, for `reconcile` -> {expired}.
--
-- ROLLBACK (this file alone; nothing earlier references it): drop function
--   infrx.lab_judge_sweep(jsonb), infrx.lab_judge_run(jsonb), infrx.lab_judge_settle(jsonb),
--   infrx.lab_judge_record_results(jsonb), infrx.lab_judge_release(jsonb),
--   infrx.lab_judge_quarantine(jsonb), infrx.lab_judge_record_submission(jsonb),
--   infrx.lab_judge_record_sent(jsonb), infrx.lab_judge_begin_submit(jsonb),
--   infrx.lab_judge_reserve(jsonb), infrx.lab_judge_json(infrx.lab_judge_runs),
--   infrx.lab_judge_consent_current(uuid, uuid, int), infrx.lab_judge_run_for(uuid, text[]),
--   infrx.lab_distinct(uuid[]); drop table infrx.lab_judge_audit, infrx.lab_judge_results,
--   infrx.lab_judge_runs (in this order). 0031's budgets keep what was settled (history).
--
-- Re-runnable: `if not exists`, `create or replace`.

create or replace function infrx.lab_distinct(p uuid[]) returns boolean
language sql immutable set search_path = infrx, public, pg_temp as $$
  select cardinality(p) = (select count(distinct x) from unnest(p) x)
$$;

create table if not exists infrx.lab_judge_runs (
  run_id uuid primary key,
  provider_org_id uuid not null,
  payer_ref text not null,
  grant_id uuid not null,
  grant_version int not null check (grant_version >= 1),
  sample_ids uuid[] not null
    check (cardinality(sample_ids) >= 1 and infrx.lab_distinct(sample_ids)),
  media_ids uuid[] not null default '{}' check (media_ids <@ sample_ids),
  sent_sample_ids uuid[] check (sent_sample_ids <@ sample_ids),
  price_version text not null check (length(btrim(price_version)) between 1 and 200),
  reserved numeric(20, 8) not null check (reserved >= 0),
  actual numeric(20, 8) check (actual >= 0 and actual <= reserved),
  state text not null default 'prepared' check (state in ('prepared', 'submitting',
    'ambiguous', 'submitted', 'completed', 'failed', 'cancelled')),
  submit_key text unique check (submit_key = 'submit:' || run_id),
  external_id text check (length(btrim(external_id)) between 1 and 200),
  created_at timestamptz not null default infrx.now(),
  updated_at timestamptz not null default infrx.now(),
  foreign key (provider_org_id, payer_ref) references infrx.lab_budgets on delete restrict,
  constraint lab_judge_runs_intent_before_egress
    check (state in ('prepared', 'cancelled') or submit_key is not null),
  constraint lab_judge_runs_accepted_has_batch
    check (state not in ('submitted', 'completed') or external_id is not null),
  constraint lab_judge_runs_actual_when_completed check ((state = 'completed') = (actual is not null))
);

create table if not exists infrx.lab_judge_results (
  run_id uuid not null references infrx.lab_judge_runs on delete restrict,
  sample_id uuid not null,
  rubric_version int not null check (rubric_version between 1 and 1000),
  label_id uuid not null default gen_random_uuid() unique,
  accepted boolean not null,
  result jsonb not null check (jsonb_typeof(result) = 'object'),
  recorded_at timestamptz not null default infrx.now(),
  primary key (run_id, sample_id, rubric_version)
);

create table if not exists infrx.lab_judge_audit (
  event_id bigint generated always as identity primary key,
  run_id uuid not null references infrx.lab_judge_runs on delete restrict,
  event text not null check (event in ('quarantine', 'failed', 'cancelled', 'settle_refused',
                                       'lease_expired')),
  reason text not null check (length(btrim(reason)) between 1 and 500),
  at timestamptz not null default infrx.now()
);

do $$
declare
  t text;
begin
  execute 'create or replace trigger lab_judge_runs_state before update on '
          'infrx.lab_judge_runs for each row execute function infrx.lab_submission_guard()';
  foreach t in array array['lab_judge_results', 'lab_judge_audit'] loop
    execute format('create or replace trigger %I before update or delete on infrx.%I '
                   'for each row execute function infrx.forbid_update_delete()',
                   t || '_immutable', t);
  end loop;
  foreach t in array array['lab_judge_runs', 'lab_judge_results', 'lab_judge_audit'] loop
    execute format('create or replace trigger %I before truncate on infrx.%I '
                   'for each statement execute function infrx.forbid_truncate()',
                   t || '_no_truncate', t);
    execute format('alter table infrx.%I enable row level security', t);
    execute format('revoke all on infrx.%I from public, anon, authenticated, service_role', t);
    execute format('grant select on infrx.%I to service_role', t);
  end loop;
end $$;

-- ================================================================ helpers ===
create or replace function infrx.lab_judge_json(r infrx.lab_judge_runs) returns jsonb
language sql stable set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('run_id', r.run_id, 'provider_org_id', r.provider_org_id,
    'payer_ref', r.payer_ref, 'grant_id', r.grant_id, 'grant_version', r.grant_version,
    'sample_ids', to_jsonb(r.sample_ids), 'media_ids', to_jsonb(r.media_ids),
    'sent_sample_ids', coalesce(to_jsonb(r.sent_sample_ids), '[]'),
    'price_version', r.price_version, 'reserved', r.reserved::text,
    'actual', r.actual::text, 'state', r.state, 'submit_key', r.submit_key,
    'external_id', r.external_id)
$$;

-- The snapshot is the grantor's CURRENT grant version to this provider, in force now, for
-- external judging over the question AND the answer.
create or replace function infrx.lab_judge_consent_current(p_provider uuid, p_grant uuid,
                                                           p_version int)
returns boolean language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce((select g.version = p_version and g.recipient_provider_org_id = p_provider
                          and g.effective_at <= infrx.now()
                          and g.categories @> array['request_content', 'response_content']
                     from infrx.lab_access_grants g where g.grant_id = p_grant
                    order by g.version desc limit 1), false)
     and infrx.lab_grant_current(p_grant, 'external_judging')
$$;

-- The run, locked, whatever its provider (the port is the platform's), or `not_found`.
create or replace function infrx.lab_judge_run_for(p_run uuid, p_states text[])
returns infrx.lab_judge_runs language plpgsql security definer
set search_path = infrx, public, pg_temp as $$
declare
  r infrx.lab_judge_runs%rowtype;
begin
  perform infrx.require_feature('lab_submission');
  select * into r from infrx.lab_judge_runs where run_id = p_run for update;
  if not found then
    perform infrx.refuse('not_found', 'no such judge run');
  end if;
  if p_states is not null and not r.state = any(p_states) then
    perform infrx.refuse('state_conflict', 'judge run ' || p_run || ' is ' || r.state);
  end if;
  return r;
end $$;

-- ==================================================================== RPCs ===
create or replace function infrx.lab_judge_reserve(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_run uuid := (p_args->>'run_id')::uuid;
  v_provider uuid := (p_args->>'provider_org_id')::uuid;
  v_cost numeric;
  r infrx.lab_judge_runs%rowtype;
begin
  perform infrx.require_feature('lab_submission');
  select * into r from infrx.lab_judge_runs where run_id = v_run;
  if found then
    if r.provider_org_id <> v_provider then
      perform infrx.refuse('not_found', 'no such judge run');
    end if;
    return infrx.lab_judge_json(r);                   -- a replay: the one run
  end if;
  if not infrx.lab_judge_consent_current(v_provider, (p_args->>'grant_id')::uuid,
                                         (p_args->>'grant_version')::int) then
    perform infrx.refuse('consent_missing', 'the grant is not the grantor''s current '
                         'external_judging grant over request and response content');
  end if;
  begin
    v_cost := (p_args->>'max_cost')::numeric;
    insert into infrx.lab_judge_runs (run_id, provider_org_id, payer_ref, grant_id,
      grant_version, sample_ids, media_ids, price_version, reserved)
    values (v_run, v_provider, p_args->>'payer_ref', (p_args->>'grant_id')::uuid,
      (p_args->>'grant_version')::int,
      array(select jsonb_array_elements_text(p_args->'sample_ids'))::uuid[],
      array(select jsonb_array_elements_text(coalesce(p_args->'media_ids', '[]')))::uuid[],
      p_args->>'price_version', v_cost)
    on conflict (run_id) do nothing
    returning * into r;
  exception when foreign_key_violation then
    perform infrx.refuse('budget_exceeded', 'no PROVIDER_USD budget for this payer');
  when check_violation or not_null_violation or invalid_text_representation
       or numeric_value_out_of_range then
    perform infrx.refuse('invalid_request', 'a judge run is distinct samples (media among '
                         'them), a price version and a non-negative PROVIDER_USD reservation');
  end;
  if r.run_id is null then
    -- a concurrent reservation of this run won: its answer is ours (or another provider's)
    select * into r from infrx.lab_judge_runs where run_id = v_run;
    if r.provider_org_id <> v_provider then
      perform infrx.refuse('not_found', 'no such judge run');
    end if;
    return infrx.lab_judge_json(r);
  end if;
  begin
    update infrx.lab_budgets set reserved = reserved + v_cost, updated_at = infrx.now()
     where (provider_org_id, payer_ref) = (v_provider, r.payer_ref);
  exception when check_violation then
    perform infrx.refuse('budget_exceeded', 'the reservation exceeds the payer''s '
                         'remaining budget');
  end;
  return infrx.lab_judge_json(r);
end $$;

create or replace function infrx.lab_judge_begin_submit(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  r infrx.lab_judge_runs%rowtype := infrx.lab_judge_run_for((p_args->>'run_id')::uuid, null);
begin
  if r.state <> 'prepared' then
    return jsonb_build_object('run', infrx.lab_judge_json(r), 'created', false);
  end if;
  update infrx.lab_judge_runs set state = 'submitting', submit_key = 'submit:' || run_id
   where run_id = r.run_id returning * into r;
  return jsonb_build_object('run', infrx.lab_judge_json(r), 'created', true);
end $$;

create or replace function infrx.lab_judge_record_sent(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  r infrx.lab_judge_runs%rowtype := infrx.lab_judge_run_for((p_args->>'run_id')::uuid,
                                                             array['submitting']);
  v_ids uuid[];
begin
  begin
    v_ids := array(select jsonb_array_elements_text(p_args->'sample_ids'))::uuid[];
  exception when invalid_text_representation then
    perform infrx.refuse('invalid_request', 'sample ids are uuids');
  end;
  if r.sent_sample_ids is not null then
    if r.sent_sample_ids = v_ids then
      return infrx.lab_judge_json(r);                 -- the same record, replayed
    end if;
    perform infrx.refuse('state_conflict', 'the samples that left are already recorded');
  end if;
  if cardinality(v_ids) = 0 or not v_ids <@ r.sample_ids then
    perform infrx.refuse('state_conflict', 'only the run''s own samples leave');
  end if;
  if not infrx.lab_judge_consent_current(r.provider_org_id, r.grant_id, r.grant_version) then
    perform infrx.refuse('consent_missing', 'the grant changed or is no longer in force');
  end if;
  update infrx.lab_judge_runs set sent_sample_ids = v_ids where run_id = r.run_id
  returning * into r;
  return infrx.lab_judge_json(r);
end $$;

create or replace function infrx.lab_judge_record_submission(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  r infrx.lab_judge_runs%rowtype := infrx.lab_judge_run_for((p_args->>'run_id')::uuid, null);
begin
  if r.external_id is not null then
    if r.external_id = p_args->>'external_id' then
      return infrx.lab_judge_json(r);
    end if;
    perform infrx.refuse('idempotency_conflict', 'the run was accepted as another batch');
  end if;
  begin
    update infrx.lab_judge_runs set state = 'submitted', external_id = p_args->>'external_id'
     where run_id = r.run_id returning * into r;
  exception when check_violation or not_null_violation then
    perform infrx.refuse('invalid_request', 'an accepted run names its batch');
  end;
  return infrx.lab_judge_json(r);
end $$;

create or replace function infrx.lab_judge_quarantine(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  r infrx.lab_judge_runs%rowtype := infrx.lab_judge_run_for((p_args->>'run_id')::uuid,
                                                             array['submitting']);
begin
  update infrx.lab_judge_runs set state = 'ambiguous' where run_id = r.run_id
  returning * into r;
  insert into infrx.lab_judge_audit (run_id, event, reason)
  values (r.run_id, 'quarantine', p_args->>'reason');
  return infrx.lab_judge_json(r);
exception when check_violation or not_null_violation then
  perform infrx.refuse('invalid_request', 'a quarantine names its reason');
  return null;
end $$;

create or replace function infrx.lab_judge_release(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_to text := p_args->>'state';
  r infrx.lab_judge_runs%rowtype := infrx.lab_judge_run_for((p_args->>'run_id')::uuid, null);
begin
  -- a release ends the run; which ends are declared from here is the state guard's
  if v_to is null or v_to not in ('failed', 'cancelled') then
    perform infrx.refuse('state_conflict', 'a release ends a run failed or cancelled');
  end if;
  update infrx.lab_budgets set reserved = reserved - r.reserved, updated_at = infrx.now()
   where (provider_org_id, payer_ref) = (r.provider_org_id, r.payer_ref);
  update infrx.lab_judge_runs set state = v_to where run_id = r.run_id returning * into r;
  insert into infrx.lab_judge_audit (run_id, event, reason)
  values (r.run_id, v_to, p_args->>'reason');
  return infrx.lab_judge_json(r);
exception when check_violation or not_null_violation then
  perform infrx.refuse('invalid_request', 'a release names its reason');
  return null;
end $$;

create or replace function infrx.lab_judge_record_results(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  r infrx.lab_judge_runs%rowtype := infrx.lab_judge_run_for((p_args->>'run_id')::uuid,
                                                             array['submitted', 'completed']);
  v_n int;
begin
  if exists (select 1 from jsonb_array_elements(p_args->'results') x
              where not (x->>'sample_id')::uuid = any(coalesce(r.sent_sample_ids, '{}'))) then
    perform infrx.refuse('invalid_request', 'a result for a sample that never left');
  end if;
  insert into infrx.lab_judge_results (run_id, sample_id, rubric_version, accepted, result)
  select r.run_id, (x->>'sample_id')::uuid, (x->>'rubric_version')::int,
         (x->>'accepted')::boolean, x->'result'
    from jsonb_array_elements(p_args->'results') x
  on conflict (run_id, sample_id, rubric_version) do nothing;
  get diagnostics v_n = row_count;
  return jsonb_build_object('inserted', v_n);
exception when check_violation or not_null_violation or invalid_text_representation then
  perform infrx.refuse('invalid_request', 'a result is a sample, a rubric version 1..1000, '
                       'accepted or not, and an object');
  return null;
end $$;

create or replace function infrx.lab_judge_settle(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  r infrx.lab_judge_runs%rowtype := infrx.lab_judge_run_for((p_args->>'run_id')::uuid,
                                                             array['submitted', 'completed']);
  v_actual numeric;
begin
  if r.state = 'completed' then
    return infrx.lab_judge_json(r);                   -- settled once
  end if;
  begin
    v_actual := (p_args->>'actual')::numeric;
  exception when invalid_text_representation then
    perform infrx.refuse('invalid_request', 'an actual is a PROVIDER_USD amount');
  end;
  if v_actual is null or v_actual < 0 then
    perform infrx.refuse('invalid_request', 'an actual is a PROVIDER_USD amount');
  end if;
  if v_actual > r.reserved then
    insert into infrx.lab_judge_audit (run_id, event, reason)
    values (r.run_id, 'settle_refused', 'billed ' || v_actual || ' over the reservation '
            || r.reserved);
    return jsonb_build_object('refused', 'budget_exceeded', 'run', infrx.lab_judge_json(r));
  end if;
  update infrx.lab_budgets set reserved = reserved - r.reserved, settled = settled + v_actual,
    updated_at = infrx.now()
   where (provider_org_id, payer_ref) = (r.provider_org_id, r.payer_ref);
  update infrx.lab_judge_runs set state = 'completed', actual = v_actual
   where run_id = r.run_id returning * into r;
  return infrx.lab_judge_json(r);
end $$;

create or replace function infrx.lab_judge_run(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select infrx.lab_judge_json(r) from infrx.lab_judge_runs r
   where r.run_id = (p_args->>'run_id')::uuid
$$;

create or replace function infrx.lab_judge_sweep(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_n int;
begin
  perform infrx.require_feature('lab_submission');
  with expired as (
    update infrx.lab_judge_runs set state = 'ambiguous'
     where state = 'submitting'
       and updated_at < infrx.now() - make_interval(secs => (p_args->>'older_than_s')::int)
    returning run_id)
  insert into infrx.lab_judge_audit (run_id, event, reason)
  select run_id, 'lease_expired', 'the submitting worker went silent' from expired;
  get diagnostics v_n = row_count;
  return jsonb_build_object('expired', v_n);
end $$;
