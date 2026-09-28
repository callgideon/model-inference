-- D8 (wave-5 LW3, lane lab-sql; 13-lab-improvement-handoffs §D8, 13:231-249): append-only
-- labels with ground truth and synthetic kept distinct, external-run state, checkpoints and
-- their lineage to D7 manifests, USD reused from D6J, and a resumed poll that never creates a
-- second paid job record - implemented AS the union of the requests filed against it:
--   SR-P1-1 (pipelines P1, P1-f3acfe2.md)   the label log behind P1's `LabelLog`
--   SR-P3-1 (pipelines P3, P3-c9d2948.md)   the run ledger behind P3's `RunLedger`
--   SR-P2-1 (judge-lw4 P2, P2-e4836fa.md, amended by its fix round 1-JLW4-1)
--                                           teacher reservations over 0036's judge ledger
--                                           with per-sample consent, + a failure log
--   WR-B3-1 (eval-ops B3, B3-582c7e4.md)    the ledger behind B3's `CheckpointLedger`
-- LOCAL-ONLY (R151): never applied hosted; the number is the next free at merge.
--
-- ONE money schema: every paid path here draws on D6J's `lab_budgets` (0031: one
-- PROVIDER_USD cap per provider and named payer, `reserved + settled <= limit` a CHECK on
-- the row each reservation updates) - P3's run reservations and P2's teacher runs as J2's
-- judge runs already do. ONE state machine: the frozen `external_run` table
-- (0031 `lab_submission_may` / `lab_submission_guard`) for P3's runs and P2's teacher runs.
-- ONE lineage: every row names D7 records (`lab_records`): a label event its dataset
-- manifest (and a label its annotation record), an external run its `lab.external_run.1`
-- record (which names its dataset manifest), a checkpoint event its external run record.
--
-- Label log (SR-P1-1)
--   lab_label_events   one event per (provider, key) for ever, in append order (`seq`); the
--                      same key and body replays the stored event, another body is
--                      `idempotency_conflict`; the dataset is the provider's manifest; a
--                      `label` event's annotation is the provider's annotation record of that
--                      dataset (`not_found` otherwise).
--   Ground truth vs synthetic (D8): an annotation record is refused at publication when it
--   is `synthetic` and claims ground truth, or `human` without a reviewer (the contract's
--   rule, now also the database's: `lab_annotation_truth` on lab_records).
-- Run ledger (SR-P3-1)
--   lab_external_runs  one per (provider, external run id): the published record's ref,
--                      connector, payer and limit (checked against the record), and the
--                      `external_run` state as a compare-and-set on the expected state (the
--                      machine is a DAG, so a state CAS is the fence); `lab_external_run_events`
--                      keeps every move (append-only).
--   lab_run_reservations  one PROVIDER_USD reservation per (provider, submit key) on the
--                      payer's D6J budget: held -> settled once (a cost, or unknown: then the
--                      whole reservation counts as spent - never an estimate of the cost) |
--                      released (never both). A resumed poll re-settles the same cost (the
--                      stored row) and can never reserve or settle a second time.
--   lab_pipeline_notes append-only, one body per (provider, key).
-- Teacher runs (SR-P2-1, amended)
--   lab_judge_runs gains `purpose` ('external_judging' | 'teacher_annotation') and
--   `dataset_ref`; a teacher run has no single grant: its consent snapshot is every sample
--   grant of the dataset version at its CURRENT version (`lab_judge_run_consents`), each in
--   force for external_judging AND training at reservation, and re-checked unchanged at
--   record_sent (a revocation, a re-grant or an expiry since reservation is
--   `consent_missing`); 0036's judge-only reserve/record_sent refuse a teacher run (no
--   grant). Its holds count on the payer's budget exactly like a judge run's, outstanding or
--   quarantined. `lab_teacher_failures` is the append-only per-item failure log.
-- Checkpoint ledger (WR-B3-1)
--   lab_checkpoint_events  one per (provider, checkpoint id): the signed event, whole, and
--                      its columns; the same id with another body is `idempotency_conflict`
--                      (per provider, as D7's receipts since 0034 F7 - another provider's id
--                      is its own).
--   lab_checkpoint_rejections  one per checkpoint: the first reason stands.
--   lab_checkpoint_subscriptions  B3's `Subscription`, whole; the first write stands.
--   lab_checkpoint_decisions  one per (subscription, checkpoint): queued with a run id, or
--                      skipped with a reason; the first write stands.
--
-- RPCs (SECURITY DEFINER; EXECUTE service_role only through 0004's defaults; jsonb in/out;
-- refusals `<code>: detail`):
--   lab_label_append {provider_org_id, event} -> the stored event.
--   lab_label_events {provider_org_id, dataset_ref} -> [event] in append order.
--   lab_external_run_get {provider_org_id, external_run_id} -> the row or null.
--   lab_external_run_move {provider_org_id, external_run_id, expected, target, fields}:
--       expected null creates `prepared` from the provider's published record (an identical
--       create replays; anything else `state_conflict`); otherwise a CAS on `expected` along
--       the machine (`state_conflict`), `fields` merged (never the record's own four).
--   lab_run_reserve {provider_org_id, key, payer_ref, limit} (flag `lab_submission`: nothing
--       new is paid while it is off) / lab_run_settle {provider_org_id, key, cost} /
--       lab_run_release {provider_org_id, key} -> {payer_ref, limit, state, cost}.
--   lab_pipeline_note {provider_org_id, key, body} -> body; lab_pipeline_noted {..., key}.
--   lab_teacher_reserve {run_id, provider_org_id, payer_ref, dataset_ref, sample_ids,
--       price_version, max_cost} / lab_teacher_record_sent {run_id, sample_ids} -> the run
--       (0036's shape plus purpose and dataset_ref); the rest of the run's life is 0036's.
--   lab_teacher_record_failures {run_id, failures: [{sample_id, reason}]} -> {inserted};
--   lab_teacher_failures {run_id} -> [{sample_id, reason, recorded_at}].
--   lab_checkpoint_record_event {event} / lab_checkpoint_event {provider_org_id,
--       checkpoint_id} / lab_checkpoint_events {provider_org_id, external_run_ref} ->
--       [{event, rejected}] / lab_checkpoint_reject {provider_org_id, checkpoint_id, reason}
--       / lab_checkpoint_subscribe {subscription} / lab_checkpoint_subscriptions
--       {provider_org_id, external_run_ref} / lab_checkpoint_decisions {subscription_id} ->
--       {checkpoint_id: decision} / lab_checkpoint_decide {subscription_id, checkpoint_id,
--       state, reason, run_id} -> the decision.
--
-- ROLLBACK (this file alone; nothing earlier references it): drop function
--   infrx.lab_checkpoint_decide(jsonb), infrx.lab_checkpoint_decisions(jsonb),
--   infrx.lab_checkpoint_subscriptions(jsonb), infrx.lab_checkpoint_subscribe(jsonb),
--   infrx.lab_checkpoint_decision_json(infrx.lab_checkpoint_decisions),
--   infrx.lab_checkpoint_reject(jsonb), infrx.lab_checkpoint_events(jsonb),
--   infrx.lab_checkpoint_event(jsonb), infrx.lab_checkpoint_record_event(jsonb),
--   infrx.lab_teacher_failures(jsonb), infrx.lab_teacher_record_failures(jsonb),
--   infrx.lab_teacher_record_sent(jsonb), infrx.lab_teacher_reserve(jsonb),
--   infrx.lab_teacher_consent_current(uuid), infrx.lab_pipeline_noted(jsonb),
--   infrx.lab_pipeline_note(jsonb), infrx.lab_run_release(jsonb), infrx.lab_run_settle(jsonb),
--   infrx.lab_run_reserve(jsonb), infrx.lab_run_reservation_json(infrx.lab_run_reservations),
--   infrx.lab_external_run_move(jsonb), infrx.lab_external_run_get(jsonb),
--   infrx.lab_label_events(jsonb), infrx.lab_label_append(jsonb); drop trigger
--   lab_annotation_truth on infrx.lab_records; drop function infrx.lab_annotation_truth();
--   drop table infrx.lab_checkpoint_decisions, infrx.lab_checkpoint_subscriptions,
--   infrx.lab_checkpoint_rejections, infrx.lab_checkpoint_events, infrx.lab_teacher_failures,
--   infrx.lab_judge_run_consents, infrx.lab_pipeline_notes, infrx.lab_run_reservations,
--   infrx.lab_external_run_events, infrx.lab_external_runs, infrx.lab_label_events (in this
--   order); restore 0036's `infrx.lab_judge_json` body; delete teacher rows from
--   lab_judge_runs (after their results), then alter table infrx.lab_judge_runs drop
--   constraint lab_judge_runs_consent_of_purpose, drop column dataset_ref, drop column
--   purpose, alter column grant_id set not null, alter column grant_version set not null.
--   Budgets keep what was settled (history).
--
-- Re-runnable: `if not exists`, `create or replace`, `drop ... if exists` before each re-add.

-- ================================================================ label log ===
create table if not exists infrx.lab_label_events (
  seq bigint generated always as identity primary key,
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  key text not null check (length(key) between 1 and 512),
  dataset_ref text not null references infrx.lab_records on delete restrict,
  kind text not null check (kind in ('label', 'assign', 'review', 'adjudicate')),
  body jsonb not null check (jsonb_typeof(body) = 'object'),
  recorded_at timestamptz not null default infrx.now(),
  constraint lab_label_events_one_per_key unique (provider_org_id, key)
);
create index if not exists lab_label_events_by_dataset
  on infrx.lab_label_events (provider_org_id, dataset_ref, seq);

-- ============================================================== run ledger ===
create table if not exists infrx.lab_external_runs (
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  external_run_id uuid not null,
  run_ref text not null unique references infrx.lab_records on delete restrict,
  state text not null check (state in ('prepared', 'submitting', 'ambiguous', 'submitted',
                                       'completed', 'failed', 'cancelled')),
  doc jsonb not null check (jsonb_typeof(doc) = 'object'),
  created_at timestamptz not null default infrx.now(),
  updated_at timestamptz not null default infrx.now(),
  primary key (provider_org_id, external_run_id),
  constraint lab_external_runs_doc_is_the_row check (doc->>'state' = state
                                                     and doc->>'run_ref' = run_ref)
);

create table if not exists infrx.lab_external_run_events (
  event_id bigint generated always as identity primary key,
  provider_org_id uuid not null,
  external_run_id uuid not null,
  from_state text,
  to_state text not null,
  fields jsonb not null,
  at timestamptz not null default infrx.now(),
  foreign key (provider_org_id, external_run_id) references infrx.lab_external_runs
    on delete restrict
);

create table if not exists infrx.lab_run_reservations (
  provider_org_id uuid not null,
  key text not null check (length(btrim(key)) between 1 and 200),
  payer_ref text not null,
  amount numeric(20, 8) not null check (amount >= 0),
  state text not null default 'held' check (state in ('held', 'settled', 'released')),
  cost numeric(20, 8) check (cost >= 0),
  charged numeric(20, 8) check (charged >= 0 and charged <= amount),
  created_at timestamptz not null default infrx.now(),
  updated_at timestamptz not null default infrx.now(),
  primary key (provider_org_id, key),
  foreign key (provider_org_id, payer_ref) references infrx.lab_budgets on delete restrict,
  constraint lab_run_reservations_charged_when_settled check ((state = 'settled')
                                                             = (charged is not null)),
  constraint lab_run_reservations_cost_when_settled check (cost is null or state = 'settled')
);

create table if not exists infrx.lab_pipeline_notes (
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  key text not null check (length(key) between 1 and 512),
  body jsonb not null check (jsonb_typeof(body) = 'object'),
  noted_at timestamptz not null default infrx.now(),
  primary key (provider_org_id, key)
);

-- ============================================================ teacher runs ===
alter table infrx.lab_judge_runs
  add column if not exists purpose text not null default 'external_judging'
    check (purpose in ('external_judging', 'teacher_annotation')),
  add column if not exists dataset_ref text references infrx.lab_records on delete restrict,
  alter column grant_id drop not null,
  alter column grant_version drop not null;
alter table infrx.lab_judge_runs drop constraint if exists lab_judge_runs_consent_of_purpose;
alter table infrx.lab_judge_runs add constraint lab_judge_runs_consent_of_purpose check (
  (purpose = 'external_judging' and grant_id is not null and grant_version is not null
   and dataset_ref is null)
  or (purpose = 'teacher_annotation' and grant_id is null and grant_version is null
      and dataset_ref is not null));

create table if not exists infrx.lab_judge_run_consents (
  run_id uuid not null references infrx.lab_judge_runs on delete restrict,
  grant_id uuid not null,
  grant_version int not null check (grant_version >= 1),
  primary key (run_id, grant_id)
);

create table if not exists infrx.lab_teacher_failures (
  run_id uuid not null references infrx.lab_judge_runs on delete restrict,
  sample_id uuid not null,
  reason text not null check (reason ~ '^[a-z][a-z_]{0,63}$'),
  recorded_at timestamptz not null default infrx.now(),
  primary key (run_id, sample_id, reason)
);

-- ======================================================= checkpoint ledger ===
create table if not exists infrx.lab_checkpoint_events (
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  checkpoint_id uuid not null,
  key_id text not null check (length(btrim(key_id)) between 1 and 200),
  external_run_ref text not null references infrx.lab_records on delete restrict,
  step int not null check (step >= 0),
  artifact_uri text not null check (length(btrim(artifact_uri)) between 1 and 2000),
  artifact_digest text not null check (artifact_digest ~ '^sha256:[0-9a-f]{64}$'),
  issued_at timestamptz not null,
  body jsonb not null check (jsonb_typeof(body) = 'object'),
  recorded_at timestamptz not null default infrx.now(),
  primary key (provider_org_id, checkpoint_id)
);
create index if not exists lab_checkpoint_events_by_run
  on infrx.lab_checkpoint_events (provider_org_id, external_run_ref, step);

create table if not exists infrx.lab_checkpoint_rejections (
  provider_org_id uuid not null,
  checkpoint_id uuid not null,
  reason text not null check (length(btrim(reason)) between 1 and 500),
  rejected_at timestamptz not null default infrx.now(),
  primary key (provider_org_id, checkpoint_id),
  foreign key (provider_org_id, checkpoint_id) references infrx.lab_checkpoint_events
    on delete restrict
);

create table if not exists infrx.lab_checkpoint_subscriptions (
  subscription_id uuid primary key,
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  owner_user_id uuid not null,         -- the subscriber (server-derived, as D9's decided_by)
  external_run_ref text not null references infrx.lab_records on delete restrict,
  body jsonb not null check (jsonb_typeof(body) = 'object'),
  created_at timestamptz not null default infrx.now()
);
create index if not exists lab_checkpoint_subscriptions_by_run
  on infrx.lab_checkpoint_subscriptions (provider_org_id, external_run_ref);

create table if not exists infrx.lab_checkpoint_decisions (
  subscription_id uuid not null references infrx.lab_checkpoint_subscriptions
    on delete restrict,
  checkpoint_id uuid not null,
  state text not null check (state in ('queued', 'skipped')),
  reason text check (length(btrim(reason)) between 1 and 200),
  run_id uuid,
  decided_at timestamptz not null default infrx.now(),
  primary key (subscription_id, checkpoint_id),
  constraint lab_checkpoint_decisions_shape check ((state = 'queued' and run_id is not null)
                                                   or (state = 'skipped' and reason is not null))
);

do $$
declare
  t text;
begin
  execute 'create or replace trigger lab_external_runs_state before update on '
          'infrx.lab_external_runs for each row execute function infrx.lab_submission_guard()';
  foreach t in array array['lab_label_events', 'lab_external_run_events', 'lab_pipeline_notes',
                           'lab_judge_run_consents', 'lab_teacher_failures',
                           'lab_checkpoint_events', 'lab_checkpoint_rejections',
                           'lab_checkpoint_subscriptions', 'lab_checkpoint_decisions'] loop
    execute format('create or replace trigger %I before update or delete on infrx.%I '
                   'for each row execute function infrx.forbid_update_delete()',
                   t || '_immutable', t);
  end loop;
  foreach t in array array['lab_label_events', 'lab_external_runs', 'lab_external_run_events',
                           'lab_run_reservations', 'lab_pipeline_notes',
                           'lab_judge_run_consents', 'lab_teacher_failures',
                           'lab_checkpoint_events', 'lab_checkpoint_rejections',
                           'lab_checkpoint_subscriptions', 'lab_checkpoint_decisions'] loop
    execute format('create or replace trigger %I before truncate on infrx.%I '
                   'for each statement execute function infrx.forbid_truncate()',
                   t || '_no_truncate', t);
    execute format('alter table infrx.%I enable row level security', t);
    execute format('revoke all on infrx.%I from public, anon, authenticated, service_role', t);
    execute format('grant select on infrx.%I to service_role', t);
  end loop;
end $$;

-- ===================================================== ground truth (D8) ===
create or replace function infrx.lab_annotation_truth() returns trigger
language plpgsql set search_path = infrx, public, pg_temp as $$
declare
  v_doc jsonb := new.body::jsonb;
begin
  if v_doc->>'method' = 'synthetic' and v_doc->'ground_truth' = 'true'::jsonb then
    perform infrx.refuse('invalid_request', 'a synthetic label is never ground truth');
  end if;
  if v_doc->>'method' = 'human' and coalesce(v_doc->>'reviewer_id', '') = '' then
    perform infrx.refuse('invalid_request', 'a human label names its reviewer');
  end if;
  return new;
end $$;
create or replace trigger lab_annotation_truth before insert on infrx.lab_records
  for each row when (new.kind = 'annotation') execute function infrx.lab_annotation_truth();

-- =============================================================== label RPCs ===
create or replace function infrx.lab_label_append(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_provider uuid;
  e jsonb := p_args->'event';
  s infrx.lab_label_events%rowtype;
begin
  begin
    v_provider := (p_args->>'provider_org_id')::uuid;
  exception when invalid_text_representation then
    perform infrx.refuse('invalid_request', 'a label event names its provider');
  end;
  if jsonb_typeof(e) is distinct from 'object'
     or jsonb_typeof(e->'key') is distinct from 'string'
     or jsonb_typeof(e->'dataset_ref') is distinct from 'string'
     or coalesce(e->>'kind', '') not in ('label', 'assign', 'review', 'adjudicate') then
    perform infrx.refuse('invalid_request', 'a label event is {key, kind, dataset_ref, ...}');
  end if;
  if not exists (select 1 from infrx.lab_records r where r.ref = e->>'dataset_ref'
                    and r.provider_org_id = v_provider and r.kind = 'dataset') then
    perform infrx.refuse('not_found', 'no such dataset for this provider');
  end if;
  if e->>'kind' = 'label' and not exists (
       select 1 from infrx.lab_records a where a.ref = e->>'annotation_ref'
          and a.kind = 'annotation' and a.provider_org_id = v_provider
          and a.body::jsonb->>'dataset_ref' = e->>'dataset_ref') then
    perform infrx.refuse('not_found', 'a label names the provider''s annotation of this '
                         'dataset');
  end if;
  begin
    insert into infrx.lab_label_events (provider_org_id, key, dataset_ref, kind, body)
    values (v_provider, e->>'key', e->>'dataset_ref', e->>'kind', e)
    on conflict (provider_org_id, key) do nothing
    returning * into s;
  exception when check_violation then
    perform infrx.refuse('invalid_request', 'a label event key is 1..512 characters');
  end;
  if s.seq is null then
    select * into s from infrx.lab_label_events
     where provider_org_id = v_provider and key = e->>'key';
    if s.body <> e then
      perform infrx.refuse('idempotency_conflict', e->>'key' || ' holds another event');
    end if;
  end if;
  return s.body;
end $$;

create or replace function infrx.lab_label_events(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(s.body order by s.seq), '[]') from infrx.lab_label_events s
   where s.provider_org_id::text = p_args->>'provider_org_id'
     and s.dataset_ref = p_args->>'dataset_ref'
$$;

-- ========================================================== run ledger RPCs ===
create or replace function infrx.lab_external_run_get(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select o.doc from infrx.lab_external_runs o
   where o.provider_org_id::text = p_args->>'provider_org_id'
     and o.external_run_id::text = p_args->>'external_run_id'
$$;

create or replace function infrx.lab_external_run_move(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_provider uuid;
  v_id uuid;
  v_fields jsonb := coalesce(p_args->'fields', '{}');
  v_target text := p_args->>'target';
  v_new jsonb;
  b jsonb;
  o infrx.lab_external_runs%rowtype;
  v_from text;
begin
  begin
    v_provider := (p_args->>'provider_org_id')::uuid;
    v_id := (p_args->>'external_run_id')::uuid;
  exception when invalid_text_representation then
    perform infrx.refuse('invalid_request', 'an external run is named by uuids');
  end;
  if jsonb_typeof(v_fields) <> 'object' or v_fields ? 'state' then
    perform infrx.refuse('invalid_request', 'fields are an object; the state is the move''s');
  end if;
  if coalesce(jsonb_typeof(p_args->'expected'), 'null') = 'null' then
    if v_target is distinct from 'prepared' then
      perform infrx.refuse('invalid_request', 'an external run is created prepared');
    end if;
    select r.body::jsonb into b from infrx.lab_records r
     where r.ref = v_fields->>'run_ref' and r.provider_org_id = v_provider
       and r.kind = 'external_run' and r.object_id = v_id;
    if b is null then
      perform infrx.refuse('not_found', 'no such external run record for this provider');
    end if;
    if v_fields->>'connector' is distinct from b->>'connector'
       or v_fields->>'payer_ref' is distinct from b#>>'{budget,payer_ref}'
       or v_fields->>'limit' is distinct from b#>>'{budget,limit,value}' then
      perform infrx.refuse('invalid_request', 'the ledger row is its published record''s '
                           'connector, payer and limit');
    end if;
    v_new := v_fields || jsonb_build_object('state', 'prepared');
    insert into infrx.lab_external_runs (provider_org_id, external_run_id, run_ref, state, doc)
    values (v_provider, v_id, v_fields->>'run_ref', 'prepared', v_new)
    on conflict do nothing
    returning * into o;
    if o.external_run_id is null then
      select * into o from infrx.lab_external_runs
       where provider_org_id = v_provider and external_run_id = v_id;
      if o.doc is distinct from v_new then
        perform infrx.refuse('state_conflict', 'this external run already exists otherwise');
      end if;
      return o.doc;
    end if;
    insert into infrx.lab_external_run_events (provider_org_id, external_run_id, to_state,
                                               fields)
    values (v_provider, v_id, 'prepared', v_fields);
    return o.doc;
  end if;
  select * into o from infrx.lab_external_runs
   where provider_org_id = v_provider and external_run_id = v_id for update;
  if not found or o.state is distinct from p_args->>'expected' then
    perform infrx.refuse('state_conflict', 'expected ' || (p_args->>'expected') || ', found '
                         || coalesce(o.state, 'no run'));
  end if;
  if v_fields ?| array['run_ref', 'connector', 'payer_ref', 'limit'] then
    perform infrx.refuse('invalid_request', 'a move never rewrites the record''s own fields');
  end if;
  v_from := o.state;
  begin
    update infrx.lab_external_runs set state = v_target,
           doc = doc || v_fields || jsonb_build_object('state', v_target)
     where provider_org_id = v_provider and external_run_id = v_id
    returning * into o;
  exception when check_violation or not_null_violation then
    perform infrx.refuse('state_conflict', 'external_run: ' || v_from || ' -> '
                         || coalesce(v_target, '?') || ' is not a declared transition');
  end;
  insert into infrx.lab_external_run_events (provider_org_id, external_run_id, from_state,
                                             to_state, fields)
  values (v_provider, v_id, v_from, v_target, v_fields);
  return o.doc;
end $$;

create or replace function infrx.lab_run_reservation_json(r infrx.lab_run_reservations)
returns jsonb language sql stable set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('payer_ref', r.payer_ref, 'limit', r.amount::text,
                            'state', r.state, 'cost', r.cost::text)
$$;

create or replace function infrx.lab_run_reserve(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_provider uuid;
  v_amount numeric;
  r infrx.lab_run_reservations%rowtype;
begin
  perform infrx.require_feature('lab_submission');
  begin
    v_provider := (p_args->>'provider_org_id')::uuid;
    v_amount := (p_args->>'limit')::numeric;
    insert into infrx.lab_run_reservations (provider_org_id, key, payer_ref, amount)
    values (v_provider, p_args->>'key', p_args->>'payer_ref', v_amount)
    on conflict (provider_org_id, key) do nothing
    returning * into r;
  exception when foreign_key_violation then
    perform infrx.refuse('budget_exceeded', 'no PROVIDER_USD budget for this payer');
  when check_violation or not_null_violation or invalid_text_representation
       or numeric_value_out_of_range then
    perform infrx.refuse('invalid_request', 'a reservation is a key, a payer and a '
                         'non-negative PROVIDER_USD limit');
  end;
  if r.key is null then
    select * into r from infrx.lab_run_reservations
     where provider_org_id = v_provider and key = p_args->>'key';
    if r.payer_ref is distinct from p_args->>'payer_ref' or r.amount <> v_amount then
      perform infrx.refuse('idempotency_conflict', 'this key reserved another budget');
    end if;
    return infrx.lab_run_reservation_json(r);
  end if;
  begin
    update infrx.lab_budgets set reserved = reserved + v_amount, updated_at = infrx.now()
     where (provider_org_id, payer_ref) = (v_provider, r.payer_ref);
  exception when check_violation then
    perform infrx.refuse('budget_exceeded', 'the reservation exceeds the payer''s '
                         'remaining budget');
  end;
  return infrx.lab_run_reservation_json(r);
end $$;

create or replace function infrx.lab_run_settle(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  r infrx.lab_run_reservations%rowtype;
  v_cost numeric;
begin
  begin
    v_cost := (p_args->>'cost')::numeric;
  exception when invalid_text_representation then
    perform infrx.refuse('invalid_request', 'a cost is a PROVIDER_USD amount or unknown');
  end;
  select * into r from infrx.lab_run_reservations
   where provider_org_id::text = p_args->>'provider_org_id' and key = p_args->>'key'
   for update;
  if not found then
    perform infrx.refuse('not_found', 'no reservation under this key');
  end if;
  if r.state = 'released' then
    perform infrx.refuse('state_conflict', 'a released reservation is not settled');
  end if;
  if r.state = 'settled' then
    if r.cost is distinct from v_cost then
      perform infrx.refuse('idempotency_conflict', 'already settled at another cost');
    end if;
    return infrx.lab_run_reservation_json(r);          -- a resumed poll: the one settlement
  end if;
  if v_cost < 0 or v_cost > r.amount then
    perform infrx.refuse('budget_exceeded', 'the reported cost is outside the reservation');
  end if;
  update infrx.lab_budgets
     set reserved = reserved - r.amount, settled = settled + coalesce(v_cost, r.amount),
         updated_at = infrx.now()
   where (provider_org_id, payer_ref) = (r.provider_org_id, r.payer_ref);
  update infrx.lab_run_reservations
     set state = 'settled', cost = v_cost, charged = coalesce(v_cost, r.amount),
         updated_at = infrx.now()
   where (provider_org_id, key) = (r.provider_org_id, r.key)
  returning * into r;
  return infrx.lab_run_reservation_json(r);
end $$;

create or replace function infrx.lab_run_release(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  r infrx.lab_run_reservations%rowtype;
begin
  select * into r from infrx.lab_run_reservations
   where provider_org_id::text = p_args->>'provider_org_id' and key = p_args->>'key'
   for update;
  if not found then
    perform infrx.refuse('not_found', 'no reservation under this key');
  end if;
  if r.state = 'settled' then
    perform infrx.refuse('state_conflict', 'a settled reservation is not released');
  end if;
  if r.state = 'held' then
    update infrx.lab_budgets set reserved = reserved - r.amount, updated_at = infrx.now()
     where (provider_org_id, payer_ref) = (r.provider_org_id, r.payer_ref);
    update infrx.lab_run_reservations set state = 'released', updated_at = infrx.now()
     where (provider_org_id, key) = (r.provider_org_id, r.key)
    returning * into r;
  end if;
  return infrx.lab_run_reservation_json(r);
end $$;

create or replace function infrx.lab_pipeline_note(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  n infrx.lab_pipeline_notes%rowtype;
begin
  begin
    insert into infrx.lab_pipeline_notes (provider_org_id, key, body)
    values ((p_args->>'provider_org_id')::uuid, p_args->>'key', p_args->'body')
    on conflict (provider_org_id, key) do nothing
    returning * into n;
  exception when check_violation or not_null_violation or invalid_text_representation
       or foreign_key_violation then
    perform infrx.refuse('invalid_request', 'a note is a key and an object');
  end;
  if n.key is null then
    select * into n from infrx.lab_pipeline_notes
     where provider_org_id = (p_args->>'provider_org_id')::uuid and key = p_args->>'key';
    if n.body <> p_args->'body' then
      perform infrx.refuse('idempotency_conflict', (p_args->>'key') || ' holds another note');
    end if;
  end if;
  return n.body;
end $$;

create or replace function infrx.lab_pipeline_noted(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select n.body from infrx.lab_pipeline_notes n
   where n.provider_org_id::text = p_args->>'provider_org_id' and n.key = p_args->>'key'
$$;

-- ============================================================ teacher RPCs ===
-- 0036's run row, plus what a teacher run is (purpose, dataset). Same keys as 0036 for a
-- judge run (purpose 'external_judging', dataset_ref null).
create or replace function infrx.lab_judge_json(r infrx.lab_judge_runs) returns jsonb
language sql stable set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('run_id', r.run_id, 'provider_org_id', r.provider_org_id,
    'payer_ref', r.payer_ref, 'grant_id', r.grant_id, 'grant_version', r.grant_version,
    'sample_ids', to_jsonb(r.sample_ids), 'media_ids', to_jsonb(r.media_ids),
    'sent_sample_ids', coalesce(to_jsonb(r.sent_sample_ids), '[]'),
    'price_version', r.price_version, 'reserved', r.reserved::text,
    'actual', r.actual::text, 'state', r.state, 'submit_key', r.submit_key,
    'external_id', r.external_id, 'purpose', r.purpose, 'dataset_ref', r.dataset_ref)
$$;

-- Every snapshotted grant of the teacher run is still at its snapshot version (so still
-- naming external judging AND training, as it did at reservation) and in force (not expired).
create or replace function infrx.lab_teacher_consent_current(p_run uuid) returns boolean
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select not exists (
    select 1 from infrx.lab_judge_run_consents c
     where c.run_id = p_run
       and not (coalesce((select g.version = c.grant_version and g.effective_at <= infrx.now()
                            from infrx.lab_access_grants g where g.grant_id = c.grant_id
                           order by g.version desc limit 1), false)
                and infrx.lab_grant_current(c.grant_id, null)))
$$;

create or replace function infrx.lab_teacher_reserve(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_run uuid;
  v_provider uuid;
  v_ids uuid[];
  v_cost numeric;
  r infrx.lab_judge_runs%rowtype;
begin
  perform infrx.require_feature('lab_submission');
  begin
    v_run := (p_args->>'run_id')::uuid;
    v_provider := (p_args->>'provider_org_id')::uuid;
    v_ids := array(select jsonb_array_elements_text(p_args->'sample_ids'))::uuid[];
    v_cost := (p_args->>'max_cost')::numeric;
  exception when invalid_text_representation or invalid_parameter_value then
    perform infrx.refuse('invalid_request', 'a teacher run is uuids and a PROVIDER_USD cost');
  end;
  select * into r from infrx.lab_judge_runs where run_id = v_run;
  if found then
    if r.provider_org_id <> v_provider or r.purpose <> 'teacher_annotation' then
      perform infrx.refuse('not_found', 'no such teacher run');
    end if;
    return infrx.lab_judge_json(r);                   -- a replay: the one run
  end if;
  if not exists (select 1 from infrx.lab_records d where d.ref = p_args->>'dataset_ref'
                    and d.provider_org_id = v_provider and d.kind = 'dataset') then
    perform infrx.refuse('not_found', 'no such dataset for this provider');
  end if;
  if cardinality(v_ids) = 0 or exists (
       select 1 from unnest(v_ids) x where not exists (
         select 1 from infrx.lab_dataset_samples s
          where s.dataset_ref = p_args->>'dataset_ref' and s.sample_id = x)) then
    perform infrx.refuse('invalid_request', 'a teacher run sends the dataset''s own samples');
  end if;
  if exists (select 1 from infrx.lab_dataset_samples s
              where s.dataset_ref = p_args->>'dataset_ref' and s.sample_id = any(v_ids)
                and not (infrx.lab_grant_current(s.grant_id, 'external_judging')
                         and infrx.lab_grant_current(s.grant_id, 'training'))) then
    perform infrx.refuse('consent_missing', 'a sample''s grant is not in force for '
                         'external_judging and training');
  end if;
  begin
    insert into infrx.lab_judge_runs (run_id, provider_org_id, payer_ref, purpose,
      dataset_ref, sample_ids, media_ids, price_version, reserved)
    values (v_run, v_provider, p_args->>'payer_ref', 'teacher_annotation',
      p_args->>'dataset_ref', v_ids, '{}', p_args->>'price_version', v_cost)
    on conflict (run_id) do nothing
    returning * into r;
  exception when foreign_key_violation then
    perform infrx.refuse('budget_exceeded', 'no PROVIDER_USD budget for this payer');
  when check_violation or not_null_violation or numeric_value_out_of_range then
    perform infrx.refuse('invalid_request', 'a teacher run is distinct samples, a price '
                         'version and a non-negative PROVIDER_USD reservation');
  end;
  if r.run_id is null then
    select * into r from infrx.lab_judge_runs where run_id = v_run;
    if r.provider_org_id <> v_provider or r.purpose <> 'teacher_annotation' then
      perform infrx.refuse('not_found', 'no such teacher run');
    end if;
    return infrx.lab_judge_json(r);
  end if;
  insert into infrx.lab_judge_run_consents (run_id, grant_id, grant_version)
  select v_run, s.grant_id, (select g.version from infrx.lab_access_grants g
                              where g.grant_id = s.grant_id order by g.version desc limit 1)
    from infrx.lab_dataset_samples s
   where s.dataset_ref = p_args->>'dataset_ref' and s.sample_id = any(v_ids)
   group by s.grant_id;
  begin
    update infrx.lab_budgets set reserved = reserved + v_cost, updated_at = infrx.now()
     where (provider_org_id, payer_ref) = (v_provider, r.payer_ref);
  exception when check_violation then
    perform infrx.refuse('budget_exceeded', 'the reservation exceeds the payer''s '
                         'remaining budget');
  end;
  return infrx.lab_judge_json(r);
end $$;

create or replace function infrx.lab_teacher_record_sent(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  r infrx.lab_judge_runs%rowtype := infrx.lab_judge_run_for((p_args->>'run_id')::uuid,
                                                             array['submitting']);
  v_ids uuid[];
begin
  if r.purpose <> 'teacher_annotation' then
    perform infrx.refuse('not_found', 'no such teacher run');
  end if;
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
  if not infrx.lab_teacher_consent_current(r.run_id) then
    perform infrx.refuse('consent_missing', 'a sample''s grant changed or is no longer in '
                         'force for external_judging and training');
  end if;
  update infrx.lab_judge_runs set sent_sample_ids = v_ids where run_id = r.run_id
  returning * into r;
  return infrx.lab_judge_json(r);
end $$;

create or replace function infrx.lab_teacher_record_failures(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  n int;
begin
  if not exists (select 1 from infrx.lab_judge_runs where run_id::text = p_args->>'run_id'
                    and purpose = 'teacher_annotation') then
    perform infrx.refuse('not_found', 'no such teacher run');
  end if;
  begin
    insert into infrx.lab_teacher_failures (run_id, sample_id, reason)
    select (p_args->>'run_id')::uuid, (f->>'sample_id')::uuid, f->>'reason'
      from jsonb_array_elements(coalesce(p_args->'failures', '[]')) f
    on conflict do nothing;
    get diagnostics n = row_count;
  exception when check_violation or not_null_violation or invalid_text_representation
       or invalid_parameter_value then
    perform infrx.refuse('invalid_request', 'a failure is a sample id and a reason word');
  end;
  return jsonb_build_object('inserted', n);
end $$;

create or replace function infrx.lab_teacher_failures(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object('sample_id', f.sample_id, 'reason', f.reason,
                                               'recorded_at', f.recorded_at)
                            order by f.recorded_at, f.sample_id, f.reason), '[]')
    from infrx.lab_teacher_failures f where f.run_id::text = p_args->>'run_id'
$$;

-- ====================================================== checkpoint RPCs ===
create or replace function infrx.lab_checkpoint_record_event(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  e jsonb := p_args->'event';
  v_provider uuid;
  c infrx.lab_checkpoint_events%rowtype;
begin
  begin
    v_provider := (e->>'provider_org_id')::uuid;
    if not exists (select 1 from infrx.lab_records r where r.ref = e->>'external_run_ref'
                      and r.provider_org_id = v_provider and r.kind = 'external_run') then
      perform infrx.refuse('not_found', 'no such external run for this provider');
    end if;
    insert into infrx.lab_checkpoint_events (provider_org_id, checkpoint_id, key_id,
      external_run_ref, step, artifact_uri, artifact_digest, issued_at, body)
    values (v_provider, (e->>'checkpoint_id')::uuid, e->>'key_id', e->>'external_run_ref',
      (e->>'step')::int, e#>>'{artifact,uri}', e#>>'{artifact,digest}',
      (e->>'issued_at')::timestamptz, e)
    on conflict (provider_org_id, checkpoint_id) do nothing
    returning * into c;
  exception when check_violation or not_null_violation or invalid_text_representation
       or invalid_datetime_format or numeric_value_out_of_range then
    perform infrx.refuse('invalid_request', 'a checkpoint event is its signed fields');
  end;
  if c.checkpoint_id is null then
    select * into c from infrx.lab_checkpoint_events
     where provider_org_id = v_provider and checkpoint_id = (e->>'checkpoint_id')::uuid;
    if c.body <> e then
      perform infrx.refuse('idempotency_conflict', 'the checkpoint id names another event');
    end if;
  end if;
  return c.body;
end $$;

create or replace function infrx.lab_checkpoint_event(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  v_body jsonb;
begin
  select c.body into v_body from infrx.lab_checkpoint_events c
   where c.provider_org_id::text = p_args->>'provider_org_id'
     and c.checkpoint_id::text = p_args->>'checkpoint_id';
  if v_body is null then
    perform infrx.refuse('not_found', 'no such checkpoint event for this provider');
  end if;
  return v_body;
end $$;

create or replace function infrx.lab_checkpoint_events(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(jsonb_build_object('event', c.body, 'rejected',
                                               x.checkpoint_id is not null)
                            order by c.step, c.recorded_at, c.checkpoint_id), '[]')
    from infrx.lab_checkpoint_events c
    left join infrx.lab_checkpoint_rejections x using (provider_org_id, checkpoint_id)
   where c.provider_org_id::text = p_args->>'provider_org_id'
     and c.external_run_ref = p_args->>'external_run_ref'
$$;

create or replace function infrx.lab_checkpoint_reject(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  begin
    insert into infrx.lab_checkpoint_rejections (provider_org_id, checkpoint_id, reason)
    values ((p_args->>'provider_org_id')::uuid, (p_args->>'checkpoint_id')::uuid,
            p_args->>'reason')
    on conflict do nothing;
  exception when foreign_key_violation then
    perform infrx.refuse('not_found', 'no such checkpoint event for this provider');
  when check_violation or not_null_violation or invalid_text_representation then
    perform infrx.refuse('invalid_request', 'a rejection names its reason');
  end;
  return jsonb_build_object('reason', (select x.reason from infrx.lab_checkpoint_rejections x
    where x.provider_org_id::text = p_args->>'provider_org_id'
      and x.checkpoint_id::text = p_args->>'checkpoint_id'));
end $$;

create or replace function infrx.lab_checkpoint_subscribe(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  s jsonb := p_args->'subscription';
  v_provider uuid;
  v_body jsonb;
begin
  begin
    v_provider := (s->>'provider_org_id')::uuid;
    if not exists (select 1 from infrx.lab_records r where r.ref = s->>'external_run_ref'
                      and r.provider_org_id = v_provider and r.kind = 'external_run') then
      perform infrx.refuse('not_found', 'no such external run for this provider');
    end if;
    insert into infrx.lab_checkpoint_subscriptions (subscription_id, provider_org_id,
      owner_user_id, external_run_ref, body)
    values ((s->>'subscription_id')::uuid, v_provider, (s->>'owner_user_id')::uuid,
      s->>'external_run_ref', s)
    on conflict (subscription_id) do nothing;
  exception when check_violation or not_null_violation or invalid_text_representation
       or foreign_key_violation then
    perform infrx.refuse('invalid_request', 'a subscription names its provider, owner and run');
  end;
  select x.body into v_body from infrx.lab_checkpoint_subscriptions x
   where x.subscription_id = (s->>'subscription_id')::uuid and x.provider_org_id = v_provider;
  if v_body is null then
    perform infrx.refuse('not_found', 'no such subscription for this provider');
  end if;
  return v_body;
end $$;

create or replace function infrx.lab_checkpoint_subscriptions(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(x.body order by x.created_at, x.subscription_id), '[]')
    from infrx.lab_checkpoint_subscriptions x
   where x.provider_org_id::text = p_args->>'provider_org_id'
     and x.external_run_ref = p_args->>'external_run_ref'
$$;

create or replace function infrx.lab_checkpoint_decision_json(d infrx.lab_checkpoint_decisions)
returns jsonb language sql stable set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('state', d.state, 'reason', d.reason, 'run_id', d.run_id)
$$;

create or replace function infrx.lab_checkpoint_decisions(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_object_agg(d.checkpoint_id, infrx.lab_checkpoint_decision_json(d)),
                  '{}')
    from infrx.lab_checkpoint_decisions d where d.subscription_id::text = p_args->>'subscription_id'
$$;

create or replace function infrx.lab_checkpoint_decide(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  d infrx.lab_checkpoint_decisions%rowtype;
begin
  begin
    insert into infrx.lab_checkpoint_decisions (subscription_id, checkpoint_id, state, reason,
                                                run_id)
    values ((p_args->>'subscription_id')::uuid, (p_args->>'checkpoint_id')::uuid,
            p_args->>'state', p_args->>'reason', (p_args->>'run_id')::uuid)
    on conflict (subscription_id, checkpoint_id) do nothing;
  exception when foreign_key_violation then
    perform infrx.refuse('not_found', 'no such subscription');
  when check_violation or not_null_violation or invalid_text_representation then
    perform infrx.refuse('invalid_request', 'a decision is queued with a run or skipped with '
                         'a reason');
  end;
  select * into d from infrx.lab_checkpoint_decisions
   where subscription_id = (p_args->>'subscription_id')::uuid
     and checkpoint_id = (p_args->>'checkpoint_id')::uuid;
  return infrx.lab_checkpoint_decision_json(d);
end $$;
