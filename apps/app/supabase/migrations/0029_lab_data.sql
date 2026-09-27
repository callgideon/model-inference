-- D7 (wave-5 LW1, lane lab-sql; 13-lab-improvement-handoffs §D7; F3 R157-R161): the Lab
-- catalog (sources, grant refs, immutable content-addressed records, dataset membership and
-- splits, harness revisions) and evaluation coordination (runs, cases, fenced attempt leases,
-- one result per run/case/evaluator, checkpoint receipts, a Lab outbox).
-- LOCAL-ONLY (R151): never applied hosted; the number is the next free at merge.
--
-- Refs (R157): `lab:<kind>:<provider_org_id>:<object_id>@sha256:<64 hex>`, unique on the full
-- ref and resolved PROVIDER-SCOPED: another provider's ref is `not_found`, the same answer as
-- an unknown one (F3's `FakeLabCatalog`).
--   lab_records    one row per published record: `body` is its RFC 8785 bytes and a CHECK
--                  re-derives the digest from them, so changed bytes are a different ref and
--                  never redefine one; one row per (kind, provider, object, version).
--   a grant ref    one VERSION of an 0027 grant: digest = sha256 of `{"grant_id":"…","version":n}`
--                  (JCS; `lab_data.grant_ref` in Python). It pins the version as evidence;
--                  rights are always read from the grant's CURRENT version (DATA-RIGHTS).
--   lab_sources    a source ref = the provider, the source id and its content digest,
--                  registered under a current grant of that provider (the grant version is
--                  resolved by the RPC; 0027's rows are immutable, so no FK re-checks it and
--                  0027's TRUNCATE refusal stays its own trigger's).
--   lab_dataset_samples  a dataset's membership and split, written with the manifest in ONE
--                  transaction (a partial upload is never visible); each sample is bound
--                  to its source's grant.
-- Evaluation (EVAL-DURABLE; 0016's fence, 0012's outbox):
--   lab_eval_runs/cases/attempts  one case per sample (up to max_cases); a lease is one
--                  attempt (run, case, attempt, worker, expiry on the DB clock); every write
--                  is fenced; expiry puts the case back (`lab_recover`, no caller time).
--   lab_eval_results  one logical result per (run, case, evaluator).
--   lab_checkpoint_receipts  one per checkpoint id; a redelivery is the same receipt.
--   lab_outbox     `eval_run` / `checkpoint_received`, written in the same transaction;
--                  claim / ack-by-claimant / release / error, as 0012 for `outbox.OutboxRelay`.
-- State moves are the contract's (states.py) through `lab_may_transition`, enforced by a
-- trigger on every state column.
--
-- Named RPCs (SECURITY DEFINER; EXECUTE for service_role only through 0004's default
-- privileges; `{...}` in, jsonb out; refusals `<code>: detail`): lab_register_source,
-- lab_publish, lab_resolve, lab_accessible_samples, lab_create_run, lab_run_status,
-- lab_cancel_run, lab_lease_case, lab_heartbeat, lab_finish_attempt, lab_recover,
-- lab_receive_checkpoint, lab_checkpoint_transition, lab_outbox_pending, lab_outbox_ack,
-- lab_outbox_release, lab_outbox_error. Browser roles reach nothing; the platform role reads
-- the tables and writes them only through the RPCs.
--
-- ROLLBACK (this file alone; nothing earlier references it): drop every function this file
--   creates (`infrx.lab_*` except 0027's six), then drop table infrx.lab_outbox,
--   infrx.lab_checkpoint_receipts, infrx.lab_eval_results, infrx.lab_eval_attempts,
--   infrx.lab_eval_cases, infrx.lab_eval_runs, infrx.lab_dataset_samples, infrx.lab_records,
--   infrx.lab_sources (in this order).
--
-- Re-runnable: `if not exists`, `create or replace`.

-- ==================================================================== helpers ===
-- [kind, provider, object id, digest] of a well-formed ref, else NULL (a mutable ref).
create or replace function infrx.lab_ref_parts(p_ref text) returns text[]
language sql immutable set search_path = infrx, public, pg_temp as $$
  select regexp_match(p_ref, '^lab:(source|grant|dataset|harness|serving|evaluator|run|'
    'external_run|checkpoint|annotation|rubric|payer|policy|variant):(' || u || '):(' || u
    || ')@sha256:([0-9a-f]{64})$')
    from (select '[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}' u) x
$$;

create or replace function infrx.lab_may_transition(p_kind text, p_from text, p_to text)
returns boolean language sql immutable set search_path = infrx, public, pg_temp as $$
  select exists (select 1 from (values
    ('run', 'queued', 'running'), ('run', 'queued', 'cancelled'),
    ('run', 'running', 'succeeded'), ('run', 'running', 'failed'),
    ('run', 'running', 'cancelled'),
    ('case', 'pending', 'leased'), ('case', 'pending', 'skipped'),
    ('case', 'leased', 'pending'), ('case', 'leased', 'done'), ('case', 'leased', 'failed'),
    ('attempt', 'leased', 'succeeded'), ('attempt', 'leased', 'failed'),
    ('attempt', 'leased', 'expired'),
    ('checkpoint', 'received', 'validated'), ('checkpoint', 'received', 'rejected'),
    ('checkpoint', 'validated', 'evaluated'), ('checkpoint', 'validated', 'rejected')
  ) t(kind, from_state, to_state)
   where (t.kind, t.from_state, t.to_state) = (p_kind, p_from, p_to))
$$;

create or replace function infrx.lab_guard_state() returns trigger
language plpgsql set search_path = infrx, public, pg_temp as $$
begin
  if new.state is distinct from old.state
     and not infrx.lab_may_transition(tg_argv[0], old.state, new.state) then
    perform infrx.refuse('state_conflict', tg_argv[0] || ': ' || old.state || ' -> '
                         || new.state || ' is not a declared transition');
  end if;
  return new;
end $$;

create or replace function infrx.lab_grant_ref(g infrx.lab_access_grants) returns text
language sql immutable set search_path = infrx, public, pg_temp as $$
  select 'lab:grant:' || g.recipient_provider_org_id || ':' || g.grant_id || '@sha256:'
    || encode(sha256(convert_to('{"grant_id":"' || g.grant_id || '","version":' || g.version
                                || '}', 'UTF8')), 'hex')
$$;

-- The grant version a ref names, when it is a grant TO this provider (else all NULL).
create or replace function infrx.lab_grant_of(p_ref text, p_provider uuid)
returns infrx.lab_access_grants language sql stable security definer
set search_path = infrx, public, pg_temp as $$
  select g.* from infrx.lab_access_grants g
   where g.grant_id = (infrx.lab_ref_parts(p_ref))[3]::uuid
     and g.recipient_provider_org_id = p_provider and infrx.lab_grant_ref(g) = p_ref
$$;

-- The grant's CURRENT (latest) version is in force, and names the purpose (NULL: any).
create or replace function infrx.lab_grant_current(p_grant uuid, p_purpose text)
returns boolean language sql stable security definer
set search_path = infrx, public, pg_temp as $$
  select coalesce((select g.revoked_at is null
                          and (g.expires_at is null or g.expires_at > infrx.now())
                          and (p_purpose is null or p_purpose = any(g.purposes))
                     from infrx.lab_access_grants g where g.grant_id = p_grant
                    order by g.version desc limit 1), false)
$$;

-- ===================================================================== catalog ===
create table if not exists infrx.lab_sources (
  source_id uuid primary key,
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  content_digest text not null check (content_digest ~ '^sha256:[0-9a-f]{64}$'),
  grant_id uuid not null,
  grant_version int not null,
  registered_by text not null check (length(btrim(registered_by)) between 1 and 200),
  registered_at timestamptz not null default infrx.now(),
  ref text not null unique,
  constraint lab_sources_bound_to_grant unique (source_id, grant_id)
);

create table if not exists infrx.lab_records (
  ref text primary key,
  kind text not null check (kind in ('dataset', 'harness', 'run', 'checkpoint', 'annotation',
                                     'external_run', 'policy', 'variant')),
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  object_id uuid not null,
  version int check (version >= 1),
  schema_id text not null check (schema_id ~ '^lab\.[a-z_]+\.[0-9]+$'),
  body text not null,
  published_by text not null check (length(btrim(published_by)) between 1 and 200),
  published_at timestamptz not null default infrx.now(),
  constraint lab_records_content_addressed check (ref = 'lab:' || kind || ':' || provider_org_id
    || ':' || object_id || '@sha256:' || encode(sha256(convert_to(body, 'UTF8')), 'hex'))
);
create unique index if not exists lab_records_one_version
  on infrx.lab_records (kind, provider_org_id, object_id, coalesce(version, 0));

create table if not exists infrx.lab_dataset_samples (
  dataset_ref text not null references infrx.lab_records on delete restrict,
  sample_id uuid not null,
  split text not null check (split in ('train', 'validation', 'holdout')),
  modality text not null check (modality in ('text', 'finite_video', 'structured')),
  source_id uuid not null,
  grant_id uuid not null,
  grant_version int not null,
  content_digest text not null check (content_digest ~ '^sha256:[0-9a-f]{64}$'),
  group_key text not null,
  primary key (dataset_ref, sample_id),
  constraint lab_dataset_samples_source_grant foreign key (source_id, grant_id)
    references infrx.lab_sources (source_id, grant_id) on delete restrict
);

-- ================================================================== evaluation ===
create table if not exists infrx.lab_eval_runs (
  run_id uuid primary key,
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  run_ref text not null unique references infrx.lab_records on delete restrict,
  dataset_ref text not null references infrx.lab_records on delete restrict,
  state text not null default 'queued'
    check (state in ('queued', 'running', 'succeeded', 'failed', 'cancelled')),
  created_at timestamptz not null default infrx.now(),
  updated_at timestamptz not null default infrx.now()
);

create table if not exists infrx.lab_eval_cases (
  run_id uuid not null references infrx.lab_eval_runs on delete restrict,
  case_id uuid not null,                  -- the sample id: one case per sample of the run
  state text not null default 'pending'
    check (state in ('pending', 'leased', 'skipped', 'done', 'failed')),
  attempts int not null default 0 check (attempts >= 0),
  primary key (run_id, case_id)
);
create index if not exists lab_eval_cases_pending
  on infrx.lab_eval_cases (run_id, case_id) where state = 'pending';

create table if not exists infrx.lab_eval_attempts (
  run_id uuid not null,
  case_id uuid not null,
  attempt int not null check (attempt >= 1),
  worker_id text not null check (length(btrim(worker_id)) between 1 and 200),
  state text not null default 'leased'
    check (state in ('leased', 'succeeded', 'failed', 'expired')),
  acquired_at timestamptz not null,
  expires_at timestamptz not null,
  finished_at timestamptz,
  cost_unit text check (cost_unit in ('CREDIT', 'PROVIDER_USD')),
  cost_value numeric(20, 8) check (cost_value >= 0),
  finish_digest text,
  primary key (run_id, case_id, attempt),
  foreign key (run_id, case_id) references infrx.lab_eval_cases on delete restrict,
  check ((cost_unit is null) = (cost_value is null))
);
create index if not exists lab_eval_attempts_expiry
  on infrx.lab_eval_attempts (expires_at) where state = 'leased';

create table if not exists infrx.lab_eval_results (
  run_id uuid not null,
  case_id uuid not null,
  evaluator_ref text not null,
  attempt int not null,
  body text not null check (length(body) <= 65536),
  recorded_at timestamptz not null default infrx.now(),
  primary key (run_id, case_id, evaluator_ref),
  foreign key (run_id, case_id, attempt) references infrx.lab_eval_attempts on delete restrict
);

create table if not exists infrx.lab_checkpoint_receipts (
  checkpoint_id uuid primary key,
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  external_run_ref text not null references infrx.lab_records on delete restrict,
  artifact_digest text not null check (artifact_digest ~ '^sha256:[0-9a-f]{64}$'),
  state text not null default 'received'
    check (state in ('received', 'validated', 'rejected', 'evaluated')),
  received_at timestamptz not null default infrx.now(),
  updated_at timestamptz not null default infrx.now()
);

create table if not exists infrx.lab_outbox (
  event_id uuid primary key default gen_random_uuid(),
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  kind text not null check (kind in ('eval_run', 'checkpoint_received')),
  payload jsonb not null check (length(payload::text) <= 4096),
  available_at timestamptz not null default infrx.now(),
  claimed_at timestamptz,
  claimed_by text,
  acknowledged_at timestamptz,
  attempts int not null default 0 check (attempts >= 0),
  last_error text,
  created_at timestamptz not null default infrx.now()
);
create index if not exists lab_outbox_pending
  on infrx.lab_outbox (available_at) where acknowledged_at is null;

-- ================================================================ guards, RLS ===
do $$
declare
  t text;
begin
  foreach t in array array['lab_sources', 'lab_records', 'lab_dataset_samples',
                           'lab_eval_results'] loop
    execute format('create or replace trigger %I before update or delete on infrx.%I '
                   'for each row execute function infrx.forbid_update_delete()',
                   t || '_immutable', t);
    execute format('create or replace trigger %I before truncate on infrx.%I '
                   'for each statement execute function infrx.forbid_truncate()',
                   t || '_no_truncate', t);
  end loop;
  foreach t in array array['lab_eval_runs:run', 'lab_eval_cases:case',
                           'lab_eval_attempts:attempt', 'lab_checkpoint_receipts:checkpoint'] loop
    execute format('create or replace trigger %I before update of state on infrx.%I '
                   'for each row execute function infrx.lab_guard_state(%L)',
                   split_part(t, ':', 1) || '_state', split_part(t, ':', 1),
                   split_part(t, ':', 2));
  end loop;
  foreach t in array array['lab_sources', 'lab_records', 'lab_dataset_samples',
                           'lab_eval_runs', 'lab_eval_cases', 'lab_eval_attempts',
                           'lab_eval_results', 'lab_checkpoint_receipts', 'lab_outbox'] loop
    execute format('alter table infrx.%I enable row level security', t);
    execute format('revoke all on infrx.%I from public, anon, authenticated, service_role', t);
    execute format('grant select on infrx.%I to service_role', t);
  end loop;
end $$;

-- ======================================================================= shapes ===
create or replace function infrx.lab_run_json(p_run uuid) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('run_id', r.run_id, 'run_ref', r.run_ref,
    'dataset_ref', r.dataset_ref, 'state', r.state,
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

create or replace function infrx.lab_attempt_json(p_run uuid, a infrx.lab_eval_attempts)
returns jsonb language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('provider_org_id', r.provider_org_id, 'run_id', a.run_id,
    'case_id', a.case_id, 'attempt', a.attempt, 'worker_id', a.worker_id, 'state', a.state,
    'expires_at', a.expires_at,
    'idempotency_key', 'attempt:' || a.run_id || ':' || a.case_id || ':' || a.attempt)
    from infrx.lab_eval_runs r where r.run_id = p_run
$$;

create or replace function infrx.lab_receipt_json(c infrx.lab_checkpoint_receipts) returns jsonb
language sql stable set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('checkpoint_id', c.checkpoint_id, 'external_run_ref',
    c.external_run_ref, 'artifact_digest', c.artifact_digest, 'state', c.state,
    'received_at', c.received_at)
$$;

-- ===================================================================== catalog RPCs ===
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
    on conflict (source_id) do nothing;
  exception when check_violation or not_null_violation then
    perform infrx.refuse('invalid_request', 'a source is a sha256 content digest and an actor');
  end;
  select * into s from infrx.lab_sources where source_id = v_source;
  if (s.provider_org_id, s.content_digest, s.grant_id, s.grant_version)
     is distinct from (v_provider, p_args->>'content_digest', g.grant_id, g.version) then
    perform infrx.refuse('state_conflict', 'source ' || v_source || ' names other content');
  end if;
  return jsonb_build_object('ref', s.ref);
end $$;

create or replace function infrx.lab_publish(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_provider uuid := (p_args->>'provider_org_id')::uuid;
  v_body text := p_args->>'body';
  v_doc jsonb;
  v_kind text;
  v_id text;
  v_ref text;
  v_parts text[];
begin
  begin
    v_doc := v_body::jsonb;
  exception when invalid_text_representation then
    perform infrx.refuse('invalid_request', 'the body is not JSON');
  end;
  select s.kind, v_doc->>s.id_field into v_kind, v_id from (values
    ('lab.dataset_manifest.1', 'dataset', 'dataset_id'),
    ('lab.harness_revision.1', 'harness', 'harness_id'),
    ('lab.eval_run.1', 'run', 'run_id'), ('lab.checkpoint.1', 'checkpoint', 'checkpoint_id'),
    ('lab.annotation.1', 'annotation', 'annotation_id'),
    ('lab.external_run.1', 'external_run', 'external_run_id'),
    ('lab.rollout_policy.1', 'policy', 'policy_id'),
    ('lab.optimization_variant.1', 'variant', 'variant_id')) s(schema_id, kind, id_field)
   where s.schema_id = v_doc->>'schema';
  if v_doc->>'provider_org_id' is distinct from v_provider::text then
    perform infrx.refuse('forbidden', 'a provider publishes only its own records');
  end if;
  -- Every declared ref (a key ending _ref/_refs outside the opaque content) is immutable and
  -- resolves for THIS provider: Lab records, sources and grants by their rows; the kinds no
  -- Lab table holds yet (serving, evaluator, rubric, payer) by their provider segment.
  for v_ref in
    with recursive walk(key, val) as (
      select null::text, v_doc
      union all
      select c.key, c.val from walk w cross join lateral (
        select e.key, e.value from jsonb_each(case jsonb_typeof(w.val) when 'object'
                                              then w.val else '{}' end) e
        union all
        select w.key, a.value from jsonb_array_elements(case jsonb_typeof(w.val) when 'array'
                                                        then w.val else '[]' end) a) c(key, val)
       where c.key is null
          or c.key not in ('input_schema', 'input_mapping', 'reference_output', 'label'))
    select val #>> '{}' from walk
     where jsonb_typeof(val) = 'string' and (key like '%\_ref' or key like '%\_refs')
  loop
    v_parts := infrx.lab_ref_parts(v_ref);
    if v_parts is null then
      perform infrx.refuse('invalid_request', 'mutable_ref: ' || v_ref);
    end if;
    if not (case
         when v_parts[1] = 'grant' then (infrx.lab_grant_of(v_ref, v_provider)).grant_id
                                        is not null
         when v_parts[1] = 'source' then exists (select 1 from infrx.lab_sources s
                                        where s.ref = v_ref and s.provider_org_id = v_provider)
         when v_parts[1] in ('serving', 'evaluator', 'rubric', 'payer')
           then v_parts[2] = v_provider::text
         else exists (select 1 from infrx.lab_records r
                       where r.ref = v_ref and r.provider_org_id = v_provider) end) then
      perform infrx.refuse('not_found', 'no such Lab record for this provider: ' || v_ref);
    end if;
  end loop;
  v_ref := 'lab:' || v_kind || ':' || v_provider || ':' || v_id || '@sha256:'
           || encode(sha256(convert_to(v_body, 'UTF8')), 'hex');
  begin
    insert into infrx.lab_records (ref, kind, provider_org_id, object_id, version, schema_id,
      body, published_by)
    values (v_ref, v_kind, v_provider, v_id::uuid, (v_doc->>'version')::int, v_doc->>'schema',
      v_body, p_args->>'actor')
    on conflict do nothing;
  exception when check_violation or not_null_violation or invalid_text_representation then
    perform infrx.refuse('invalid_request', 'not a referable Lab record of a valid shape');
  end;
  if not found then
    -- a concurrent or earlier publisher holds this version: the same bytes are a replay
    if not exists (select 1 from infrx.lab_records where ref = v_ref) then
      perform infrx.refuse('state_conflict', v_kind || ' ' || v_id || ' version '
                           || coalesce(v_doc->>'version', '-') || ' is already other bytes');
    end if;
    return jsonb_build_object('ref', v_ref);
  end if;
  if v_kind = 'dataset' then
    begin
      insert into infrx.lab_dataset_samples (dataset_ref, sample_id, split, modality,
        source_id, grant_id, grant_version, content_digest, group_key)
      select v_ref, (s->>'sample_id')::uuid,
             (select n from unnest(array['train', 'validation', 'holdout']) n
               where v_doc->'splits'->n ? (s->>'sample_id')),
             s->>'modality', src.source_id, g.grant_id, g.version, s->>'content_digest',
             s->>'group_key'
        from jsonb_array_elements(v_doc->'samples') s
        join infrx.lab_sources src on src.ref = s->>'source_ref'
        cross join lateral infrx.lab_grant_of(s->>'grant_ref', v_provider) g;
    exception when foreign_key_violation or not_null_violation or check_violation
              or unique_violation then
      perform infrx.refuse('invalid_request', 'every sample is in one split and bound to its '
                           'source''s grant');
    end;
  end if;
  return jsonb_build_object('ref', v_ref);
end $$;

create or replace function infrx.lab_resolve(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  r infrx.lab_records%rowtype;
begin
  select * into r from infrx.lab_records
   where ref = p_args->>'ref' and provider_org_id = (p_args->>'provider_org_id')::uuid;
  if not found then
    perform infrx.refuse('not_found', 'no such Lab record for this provider');
  end if;
  return jsonb_build_object('ref', r.ref, 'body', r.body, 'published_by', r.published_by,
                            'published_at', r.published_at);
end $$;

-- DATA-RIGHTS: the samples whose grant is in force NOW for the purpose (default the access
-- gate's, provider_sharing, R160). A published manifest confers nothing by itself.
create or replace function infrx.lab_accessible_samples(p_args jsonb) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select coalesce(jsonb_agg(s.sample_id order by s.sample_id), '[]')
    from infrx.lab_records r join infrx.lab_dataset_samples s on s.dataset_ref = r.ref
   where r.ref = p_args->>'dataset_ref'
     and r.provider_org_id = (p_args->>'provider_org_id')::uuid
     and infrx.lab_grant_current(s.grant_id,
                                 coalesce(p_args->>'purpose', 'provider_sharing'))
$$;

-- ================================================================== evaluation RPCs ===
create or replace function infrx.lab_create_run(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_provider uuid := (p_args->>'provider_org_id')::uuid;
  r infrx.lab_records%rowtype;
  v_doc jsonb;
begin
  select * into r from infrx.lab_records
   where ref = p_args->>'run_ref' and provider_org_id = v_provider and kind = 'run';
  if not found then
    perform infrx.refuse('not_found', 'no such run record for this provider');
  end if;
  v_doc := r.body::jsonb;
  -- DATA-RIGHTS at scheduling: every sample's grant in force for provider_sharing (R160).
  if exists (select 1 from infrx.lab_dataset_samples s
              where s.dataset_ref = v_doc->>'dataset_ref'
                and not infrx.lab_grant_current(s.grant_id, 'provider_sharing')) then
    perform infrx.refuse('forbidden', 'schedule: a sample''s grant is not in force for '
                         'provider_sharing');
  end if;
  insert into infrx.lab_eval_runs (run_id, provider_org_id, run_ref, dataset_ref)
  values (r.object_id, v_provider, r.ref, v_doc->>'dataset_ref')
  on conflict (run_id) do nothing;
  if not found then
    -- run:<run_id> replays (R161) to the caller's own run only; the same run_id under
    -- another provider is an unknown run (R157). ponytail: run_id is global, so a provider
    -- that learns another's run_id before it is created takes it; key runs by
    -- (provider_org_id, run_id) if run ids ever stop being unguessable.
    if not exists (select 1 from infrx.lab_eval_runs where run_ref = r.ref) then
      perform infrx.refuse('not_found', 'no such run for this provider');
    end if;
    return infrx.lab_run_json(r.object_id);
  end if;
  -- ponytail: the first max_cases samples by id; seeded sampling is B1's when it needs one.
  insert into infrx.lab_eval_cases (run_id, case_id)
  select r.object_id, s.sample_id from infrx.lab_dataset_samples s
   where s.dataset_ref = v_doc->>'dataset_ref'
   order by s.sample_id limit (v_doc->>'max_cases')::int;
  insert into infrx.lab_outbox (provider_org_id, kind, payload)
  values (v_provider, 'eval_run', jsonb_build_object('run_id', r.object_id));
  return infrx.lab_run_json(r.object_id);
end $$;

create or replace function infrx.lab_run_status(p_args jsonb) returns jsonb
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
begin
  if not exists (select 1 from infrx.lab_eval_runs where run_id = (p_args->>'run_id')::uuid
                 and provider_org_id = (p_args->>'provider_org_id')::uuid) then
    perform infrx.refuse('not_found', 'no such run for this provider');
  end if;
  return infrx.lab_run_json((p_args->>'run_id')::uuid);
end $$;

create or replace function infrx.lab_cancel_run(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
begin
  update infrx.lab_eval_runs set state = 'cancelled', updated_at = infrx.now()
   where run_id = (p_args->>'run_id')::uuid
     and provider_org_id = (p_args->>'provider_org_id')::uuid;
  if not found then
    perform infrx.refuse('not_found', 'no such run for this provider');
  end if;
  return infrx.lab_run_json((p_args->>'run_id')::uuid);
end $$;

create or replace function infrx.lab_lease_s(p_args jsonb) returns interval
language plpgsql immutable set search_path = infrx, public, pg_temp as $$
begin
  if coalesce((p_args->>'lease_s')::float8, 0) <= 0 then
    perform infrx.refuse('invalid_request', 'lease_s is a positive number of seconds');
  end if;
  return make_interval(secs => (p_args->>'lease_s')::float8);
end $$;

-- LOCK ORDER: the run row, then a case, then its attempt (the lease and the fence alike).
create or replace function infrx.lab_lease_case(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_ttl interval := infrx.lab_lease_s(p_args);
  v_now timestamptz := infrx.now();
  r infrx.lab_eval_runs%rowtype;
  c infrx.lab_eval_cases%rowtype;
  a infrx.lab_eval_attempts%rowtype;
begin
  -- ponytail: the run row serializes its leases; per-case SKIP LOCKED if throughput matters.
  select * into r from infrx.lab_eval_runs where run_id = (p_args->>'run_id')::uuid
     and provider_org_id = (p_args->>'provider_org_id')::uuid for update;
  if not found then
    perform infrx.refuse('not_found', 'no such run for this provider');
  end if;
  if r.state not in ('queued', 'running') then
    perform infrx.refuse('already_terminal', 'run ' || r.run_id || ' is ' || r.state);
  end if;
  update infrx.lab_eval_cases set state = 'leased', attempts = attempts + 1
   where (run_id, case_id) = (select x.run_id, x.case_id from infrx.lab_eval_cases x
                               where x.run_id = r.run_id and x.state = 'pending'
                               order by x.case_id limit 1)
  returning * into c;
  if not found then
    return null;
  end if;
  insert into infrx.lab_eval_attempts (run_id, case_id, attempt, worker_id, acquired_at,
    expires_at)
  values (r.run_id, c.case_id, c.attempts, p_args->>'worker_id', v_now, v_now + v_ttl)
  returning * into a;
  if r.state = 'queued' then
    update infrx.lab_eval_runs set state = 'running', updated_at = v_now
     where run_id = r.run_id;
  end if;
  return infrx.lab_attempt_json(r.run_id, a);
end $$;

-- THE fence of every attempt write: the lease's run is the caller's provider's and still
-- live, and the attempt is the live one of that worker, unexpired on the database clock.
create or replace function infrx.lab_fence(p_lease jsonb) returns infrx.lab_eval_attempts
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  r infrx.lab_eval_runs%rowtype;
  a infrx.lab_eval_attempts%rowtype;
begin
  select * into r from infrx.lab_eval_runs where run_id = (p_lease->>'run_id')::uuid
     and provider_org_id = (p_lease->>'provider_org_id')::uuid for update;
  if not found then
    perform infrx.refuse('not_found', 'no such run for this provider');
  end if;
  select * into a from infrx.lab_eval_attempts where run_id = r.run_id
     and case_id = (p_lease->>'case_id')::uuid and attempt = (p_lease->>'attempt')::int
     for update;
  if not found or a.state <> 'leased' then
    perform infrx.refuse('stale_lease', 'the attempt is not live');
  end if;
  if a.worker_id is distinct from p_lease->>'worker_id' then
    perform infrx.refuse('stale_lease', 'the lease belongs to ' || a.worker_id);
  end if;
  if r.state not in ('queued', 'running') then
    perform infrx.refuse('stale_lease', 'run ' || r.run_id || ' is ' || r.state);
  end if;
  if infrx.now() >= a.expires_at then
    perform infrx.refuse('stale_lease', 'the lease expired at ' || a.expires_at);
  end if;
  return a;
end $$;

create or replace function infrx.lab_heartbeat(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_ttl interval := infrx.lab_lease_s(p_args);
  a infrx.lab_eval_attempts%rowtype;
begin
  a := infrx.lab_fence(p_args->'lease');
  update infrx.lab_eval_attempts set expires_at = infrx.now() + v_ttl
   where (run_id, case_id, attempt) = (a.run_id, a.case_id, a.attempt)
  returning * into a;
  return infrx.lab_attempt_json(a.run_id, a);
end $$;

create or replace function infrx.lab_finish_attempt(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_lease jsonb := p_args->'lease';
  v_outcome text := p_args->>'outcome';
  v_results jsonb := coalesce(p_args->'results', '[]');
  v_digest text := encode(sha256(convert_to(jsonb_build_object('outcome', v_outcome,
    'results', v_results, 'cost', p_args->'cost')::text, 'UTF8')), 'hex');
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
     or (v_outcome = 'failed' and jsonb_array_length(v_results) > 0) then
    perform infrx.refuse('invalid_request', 'an attempt succeeds with its results or fails '
                         'with none');
  end if;
  a := infrx.lab_fence(v_lease);
  if exists (select 1 from jsonb_array_elements(v_results) x
              where (infrx.lab_ref_parts(x->>'evaluator_ref'))[1] is distinct from 'evaluator'
                 or (infrx.lab_ref_parts(x->>'evaluator_ref'))[2]
                    is distinct from v_lease->>'provider_org_id') then
    perform infrx.refuse('not_found', 'no such evaluator for this provider');
  end if;
  begin
    insert into infrx.lab_eval_results (run_id, case_id, evaluator_ref, attempt, body)
    select a.run_id, a.case_id, x->>'evaluator_ref', a.attempt, x->>'body'
      from jsonb_array_elements(v_results) x;
    update infrx.lab_eval_attempts set state = v_outcome, finished_at = infrx.now(),
      cost_unit = p_args->'cost'->>'unit', cost_value = (p_args->'cost'->>'value')::numeric,
      finish_digest = v_digest
     where (run_id, case_id, attempt) = (a.run_id, a.case_id, a.attempt)
    returning * into a;
  exception when unique_violation or check_violation or not_null_violation
            or invalid_text_representation or numeric_value_out_of_range then
    perform infrx.refuse('invalid_request', 'one result per evaluator, a bounded body and a '
                         'unit-tagged cost');
  end;
  update infrx.lab_eval_cases set state = case v_outcome when 'succeeded' then 'done'
                                                         else 'failed' end
   where (run_id, case_id) = (a.run_id, a.case_id);
  if not exists (select 1 from infrx.lab_eval_cases where run_id = a.run_id
                 and state in ('pending', 'leased')) then
    update infrx.lab_eval_runs set state = 'succeeded', updated_at = infrx.now()
     where run_id = a.run_id;
  end if;
  return infrx.lab_attempt_json(a.run_id, a);
end $$;

-- The reaper (no caller time): expired live attempts are `expired` and their cases pending
-- again. SKIP LOCKED: an attempt whose finish or heartbeat is in flight is left to the next
-- sweep, so the reaper never waits while holding a case.
create or replace function infrx.lab_recover(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  n int;
begin
  with expired as (
    update infrx.lab_eval_attempts a set state = 'expired', finished_at = infrx.now()
     where (a.run_id, a.case_id, a.attempt) in (
             select x.run_id, x.case_id, x.attempt from infrx.lab_eval_attempts x
              where x.state = 'leased' and x.expires_at <= infrx.now()
              for update skip locked)
    returning a.run_id, a.case_id)
  update infrx.lab_eval_cases c set state = 'pending'
    from expired e where (c.run_id, c.case_id) = (e.run_id, e.case_id);
  get diagnostics n = row_count;
  return jsonb_build_object('expired', n);
end $$;

-- ================================================================ checkpoint RPCs ===
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
    on conflict (checkpoint_id) do nothing
    returning * into c;
  exception when check_violation or not_null_violation or invalid_text_representation then
    perform infrx.refuse('invalid_request', 'a checkpoint is an id and a sha256 digest');
  end;
  if c.checkpoint_id is null then
    -- a redelivery (or a concurrent duplicate that waited for the first)
    select * into c from infrx.lab_checkpoint_receipts
     where checkpoint_id = (p_args->>'checkpoint_id')::uuid;
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

create or replace function infrx.lab_checkpoint_transition(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  c infrx.lab_checkpoint_receipts%rowtype;
begin
  update infrx.lab_checkpoint_receipts set state = p_args->>'state', updated_at = infrx.now()
   where checkpoint_id = (p_args->>'checkpoint_id')::uuid
     and provider_org_id = (p_args->>'provider_org_id')::uuid
  returning * into c;
  if not found then
    perform infrx.refuse('not_found', 'no such checkpoint for this provider');
  end if;
  return infrx.lab_receipt_json(c);
end $$;

-- ==================================================================== outbox RPCs ===
create or replace function infrx.lab_outbox_pending(p_args jsonb) returns jsonb
language sql security definer set search_path = infrx, public, pg_temp as $$
  with picked as (
    select o.event_id from infrx.lab_outbox o
     where o.acknowledged_at is null and o.available_at <= infrx.now()
       and (o.claimed_at is null or o.claimed_at
            <= infrx.now() - make_interval(secs => (p_args->>'redelivery_s')::float8))
     order by o.available_at, o.event_id limit (p_args->>'limit')::int
     for update skip locked),
  claimed as (
    update infrx.lab_outbox o set claimed_at = infrx.now(), claimed_by = p_args->>'worker_id',
                                  attempts = o.attempts + 1
      from picked where o.event_id = picked.event_id
    returning o.*)
  select coalesce(jsonb_agg(jsonb_build_object('event_id', event_id, 'kind', kind,
    'provider_org_id', provider_org_id, 'payload', payload) order by available_at, event_id),
    '[]') from claimed
$$;

-- An acknowledgment lands only for the relay still holding the claim (0012's OB-1b).
create or replace function infrx.lab_outbox_ack(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  n int;
begin
  update infrx.lab_outbox set acknowledged_at = infrx.now()
   where event_id in (select jsonb_array_elements_text(p_args->'event_ids')::uuid)
     and claimed_by = p_args->>'worker_id' and acknowledged_at is null;
  get diagnostics n = row_count;
  return to_jsonb(n);
end $$;

create or replace function infrx.lab_outbox_release(p_args jsonb) returns jsonb
language sql security definer set search_path = infrx, public, pg_temp as $$
  update infrx.lab_outbox set claimed_at = null, claimed_by = null
   where event_id in (select jsonb_array_elements_text(p_args->'event_ids')::uuid)
     and acknowledged_at is null;
  select 'null'::jsonb
$$;

create or replace function infrx.lab_outbox_error(p_args jsonb) returns jsonb
language sql security definer set search_path = infrx, public, pg_temp as $$
  update infrx.lab_outbox set last_error = left(coalesce(p_args->>'error', 'unknown'), 500)
   where event_id = (p_args->>'event_id')::uuid;
  select 'null'::jsonb
$$;
