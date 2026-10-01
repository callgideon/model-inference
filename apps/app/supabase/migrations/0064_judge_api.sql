-- AP-08 (wave 7, lane api-judge; R271 allocates 0064 to it): the session doors the FastAPI judge
-- and review families (`infrx/gateway/routes/lab_judge.py`, `lab_reviews.py`) need beyond
-- 0037/0038's, and EXECUTE on them for the Lab control login. LOCAL-ONLY (R151/R201): never
-- applied hosted; the number is the next free at merge.
--
-- Why a migration (R271: "only if a read the HTTP contract needs has no function"): the
-- contract (research/plan/api-lifecycle/contracts.md §8) reads a provider's configurations, its
-- runs with their ledger state, a run's results and its budgets - none has a door (0037 has
-- configure/set_budget/request_run/calibration, 0043 only the runs that sent ONE request);
-- `Idempotency-Key` identity for a configuration and a budget change (0037's doors mint a fresh
-- config id and a fresh limit version per call); a cancel (no judge door moves a run); and a
-- provider's human review of a trace (no store: 0038's door reads customer signals, D8's label
-- log needs a dataset). Each door below is 0037's pattern: SECURITY DEFINER, the identity is
-- auth.uid() (the API sets the PostgREST claims to the user its ActorSource verified, never a
-- body field), the `lab_submission` flag and a CURRENT developer+ membership first
-- (`infrx.lab_judge_door`: 55000 / 42501), another provider's id `P0002`.
--
--   lab_judge_cancellations   append-only: one cancel request per run (who, when).
--   lab_trace_reviews         append-only: a provider member's HUMAN review of a request -
--                             provenance 'human' by construction (a judge result lives in
--                             0036's lab_judge_results, a teacher label in D8's label log),
--                             one row per review id (the API derives it from the scoped
--                             Idempotency-Key) with the canonical input hash.
--
--   public.lab_judge_configure_keyed(provider, config_id, grantor, model, judge_model,
--       rubric_version, sample_size): 0037's configure with the caller's config id; the same
--       id again is the stored row (`replayed`), with other values `idempotency_conflict`.
--   public.lab_judge_config_list(provider, config_id?, after?, limit): the provider's
--       configurations (one when config_id is given), keyset on config_id, at most 101, each
--       with J3's latest calibration of its (grantor, judge model, rubric version) or
--       `uncalibrated` (0043's default).
--   public.lab_judge_set_budget_keyed(provider, payer_ref, limit {unit, value}, key_digest):
--       0037's set_budget (administrator, own payer, PROVIDER_USD) whose limit version records
--       the key digest in its reason; the same digest again answers that version
--       (`replayed`) or `idempotency_conflict` for another limit.
--   public.lab_judge_budget_list(provider): the provider's payers - limit, held, settled,
--       latest limit version (PROVIDER_USD, exact numeric text).
--   public.lab_judge_run_list(provider, run_id?, after?, limit): 0037's queued requests with
--       their config pins, the ledger state when the worker reserved one (0036; null =
--       queued), selected/sent/media counts, accepted/rejected result counts, reserved and
--       settled PROVIDER_USD, price version and the cancel request; keyset on run_id, <= 101.
--   public.lab_judge_run_results(provider, run_id, after?, limit): the run's stored results
--       (0036, accepted or a bounded rejection), keyset on label_id, <= 101, only while the
--       run's grant is still current for external_judging (else `forbidden`).
--   public.lab_judge_cancel(provider, run_id): records the cancel once; a reserved run
--       (`prepared`) is released `cancelled` through 0031's declared move; a run already
--       submitting/submitted/ambiguous is NOT moved (its provider batch is reconciled, never
--       resubmitted or blindly dropped); a queued request is never started (the start step
--       reads the cancellation). Answers the run row as run_list does.
--   public.lab_trace_review(p_args {provider_org_id, request_id, review_id, input_hash,
--       verdict, comment?, run_id?, rubric_version?}): developer+ and the request's org
--       CURRENTLY sharing it with the provider (0038's rule: feedback + provider_sharing over
--       the job's model); the same review id with the same hash is the stored row, another
--       hash `idempotency_conflict`.
--   public.lab_trace_reviews(p_args {provider_org_id, request_id}): those reviews, same rule.
--
-- EXECUTE: the Lab control login `infrx_lab_control` (R237: the box's /lab/v1/* server) on the
-- doors above plus 0037's request_run/calibration and 0038's lab_review_feedback; the platform
-- role keeps 0004's default; PUBLIC, anon and authenticated get nothing new (the web apps call
-- FastAPI, R271).
--
-- ROLLBACK (this file alone; nothing earlier references it): revoke execute on the functions
--   below from infrx_lab_control; drop function public.lab_trace_reviews(jsonb),
--   public.lab_trace_review(jsonb), public.lab_judge_cancel(uuid, uuid),
--   public.lab_judge_run_results(uuid, uuid, uuid, int),
--   public.lab_judge_run_list(uuid, uuid, uuid, int), public.lab_judge_budget_list(uuid),
--   public.lab_judge_set_budget_keyed(uuid, text, jsonb, text),
--   public.lab_judge_config_list(uuid, uuid, uuid, int),
--   public.lab_judge_configure_keyed(uuid, uuid, uuid, uuid, text, int, int),
--   infrx.lab_judge_run_doc(uuid), infrx.lab_review_request_org(uuid, uuid);
--   drop table infrx.lab_trace_reviews, infrx.lab_judge_cancellations.
--
-- Re-runnable: `if not exists`, `create or replace`.

create table if not exists infrx.lab_judge_cancellations (
  run_id uuid primary key references infrx.lab_judge_requests on delete restrict,
  requested_by uuid not null,
  requested_at timestamptz not null default infrx.now()
);

create table if not exists infrx.lab_trace_reviews (
  review_id uuid primary key,
  provider_org_id uuid not null references infrx.provider_orgs on delete restrict,
  request_id uuid not null,
  reviewer uuid not null,
  provenance text not null default 'human' check (provenance = 'human'),
  verdict text not null check (verdict in ('pass', 'fail', 'unsure')),
  comment text check (length(comment) between 1 and 2000),
  run_id uuid references infrx.lab_judge_requests on delete restrict,
  rubric_version int check (rubric_version between 1 and 1000),
  input_hash text not null check (input_hash ~ '^sha256:[0-9a-f]{64}$'),
  created_at timestamptz not null default infrx.now()
);

create index if not exists lab_trace_reviews_by_request
  on infrx.lab_trace_reviews (provider_org_id, request_id, created_at);

do $$
declare
  t text;
begin
  foreach t in array array['lab_judge_cancellations', 'lab_trace_reviews'] loop
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

-- ============================================================ configurations ===
create or replace function infrx.lab_judge_config_json(c infrx.lab_judge_configs) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('config_id', c.config_id, 'grantor_org_id', c.grantor_org_id,
    'model_id', c.model_id, 'judge_model', c.judge_model, 'rubric_version', c.rubric_version,
    'sample_size', c.sample_size, 'created_at', c.created_at,
    'calibration', coalesce((select k.calibration from infrx.lab_judge_calibrations k
                              where k.provider_org_id = c.provider_org_id
                                and k.grantor_org_id = c.grantor_org_id
                                and k.judge_model = c.judge_model
                                and k.rubric_version = c.rubric_version
                              order by k.calibration_id desc limit 1),
                            jsonb_build_object('state', 'uncalibrated', 'labels', 0,
                              'required', 30, 'agreement', null, 'interval', null)))
$$;

create or replace function public.lab_judge_configure_keyed(p_provider_org_id uuid,
  p_config_id uuid, p_grantor_org_id uuid, p_model_id uuid, p_judge_model text,
  p_rubric_version int, p_sample_size int)
returns jsonb language plpgsql security definer set search_path = public, infrx, pg_temp as $$
declare
  c infrx.lab_judge_configs%rowtype;
begin
  perform infrx.lab_judge_door(p_provider_org_id, 'developer');
  select * into c from infrx.lab_judge_configs where config_id = p_config_id;
  if not found then
    if not infrx.lab_judge_granted(p_provider_org_id, p_grantor_org_id, p_model_id) then
      raise exception 'no current external_judging grant covers this model'
        using errcode = '42501';
    end if;
    begin
      insert into infrx.lab_judge_configs (config_id, provider_org_id, grantor_org_id,
        model_id, judge_model, rubric_version, sample_size, created_by)
      values (p_config_id, p_provider_org_id, p_grantor_org_id, p_model_id, p_judge_model,
        p_rubric_version, p_sample_size, auth.uid())
      on conflict (config_id) do nothing
      returning * into c;
    exception when check_violation or not_null_violation then
      raise exception 'a judge model name, a rubric version 1..1000 and 1..200 samples'
        using errcode = '22023';
    end;
    if c.config_id is not null then
      return infrx.lab_judge_config_json(c) || jsonb_build_object('replayed', false);
    end if;
    select * into c from infrx.lab_judge_configs where config_id = p_config_id;
  end if;
  if c.provider_org_id <> p_provider_org_id then
    raise exception 'no such judge configuration' using errcode = 'P0002';
  end if;
  if (c.grantor_org_id, c.model_id, c.judge_model, c.rubric_version, c.sample_size)
     is distinct from (p_grantor_org_id, p_model_id, p_judge_model, p_rubric_version,
                       p_sample_size) then
    perform infrx.refuse('idempotency_conflict',
                         'this key already configured different values');
  end if;
  return infrx.lab_judge_config_json(c) || jsonb_build_object('replayed', true);
end $$;

create or replace function public.lab_judge_config_list(p_provider_org_id uuid,
  p_config_id uuid, p_after uuid, p_limit int)
returns jsonb language plpgsql stable security definer set search_path = public, infrx, pg_temp as $$
begin
  perform infrx.lab_judge_door(p_provider_org_id, 'developer');
  return (select coalesce(jsonb_agg(infrx.lab_judge_config_json(x) order by x.config_id), '[]')
    from (select * from infrx.lab_judge_configs c
           where c.provider_org_id = p_provider_org_id
             and (p_config_id is null or c.config_id = p_config_id)
             and (p_after is null or c.config_id > p_after)
           order by c.config_id
           limit least(greatest(coalesce(p_limit, 26), 1), 101)) x);
end $$;

-- =================================================================== budgets ===
create or replace function public.lab_judge_set_budget_keyed(p_provider_org_id uuid,
  p_payer_ref text, p_limit jsonb, p_key_digest text)
returns jsonb language plpgsql security definer set search_path = public, infrx, pg_temp as $$
declare
  v_reason text;
  l infrx.lab_budget_limits%rowtype;
  v_budget jsonb;
begin
  perform infrx.lab_judge_door(p_provider_org_id, 'administrator');
  if (infrx.lab_ref_parts(p_payer_ref))[1:2] is distinct from
     array['payer', p_provider_org_id::text] then
    raise exception 'a budget is this provider''s own lab:payer ref' using errcode = '42501';
  end if;
  if p_limit->>'unit' is distinct from 'PROVIDER_USD' then
    raise exception 'a judge budget is PROVIDER_USD' using errcode = '22023';
  end if;
  if coalesce(p_key_digest, '') !~ '^[0-9a-f]{64}$' then
    raise exception 'a budget change names its key digest' using errcode = '22023';
  end if;
  v_reason := 'set in the Lab API; key sha256:' || p_key_digest;
  select * into l from infrx.lab_budget_limits
   where provider_org_id = p_provider_org_id and payer_ref = p_payer_ref and reason = v_reason;
  if not found then
    v_budget := infrx.lab_put_budget(jsonb_build_object('provider_org_id', p_provider_org_id,
      'payer_ref', p_payer_ref, 'limit', p_limit->>'value', 'actor', auth.uid(),
      'reason', v_reason));
    select * into l from infrx.lab_budget_limits
     where provider_org_id = p_provider_org_id and payer_ref = p_payer_ref
     order by version desc limit 1;
    return v_budget || jsonb_build_object('version', l.version, 'replayed', false);
  end if;
  begin
    if l.limit_value <> (p_limit->>'value')::numeric then
      perform infrx.refuse('idempotency_conflict', 'this key already set another limit');
    end if;
  exception when invalid_text_representation or numeric_value_out_of_range then
    raise exception 'a limit is a non-negative PROVIDER_USD amount' using errcode = '22023';
  end;
  return (select jsonb_build_object('provider_org_id', b.provider_org_id,
            'payer_ref', b.payer_ref, 'unit', b.unit, 'limit', l.limit_value::text,
            'reserved', b.reserved::text, 'settled', b.settled::text,
            'version', l.version, 'replayed', true)
            from infrx.lab_budgets b
           where b.provider_org_id = p_provider_org_id and b.payer_ref = p_payer_ref);
end $$;

create or replace function public.lab_judge_budget_list(p_provider_org_id uuid)
returns jsonb language plpgsql stable security definer set search_path = public, infrx, pg_temp as $$
begin
  perform infrx.lab_judge_door(p_provider_org_id, 'developer');
  return (select coalesce(jsonb_agg(jsonb_build_object('payer_ref', b.payer_ref,
            'unit', b.unit, 'limit', b.limit_value::text, 'reserved', b.reserved::text,
            'settled', b.settled::text, 'updated_at', b.updated_at,
            'version', (select max(l.version) from infrx.lab_budget_limits l
                         where l.provider_org_id = b.provider_org_id
                           and l.payer_ref = b.payer_ref))
            order by b.payer_ref), '[]')
    from infrx.lab_budgets b where b.provider_org_id = p_provider_org_id);
end $$;

-- ====================================================================== runs ===
create or replace function infrx.lab_judge_run_doc(p_run uuid) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('run_id', q.run_id, 'config_id', q.config_id,
    'payer_ref', q.payer_ref, 'requested_at', q.requested_at,
    'grantor_org_id', c.grantor_org_id, 'model_id', c.model_id,
    'judge_model', c.judge_model, 'rubric_version', c.rubric_version,
    'sample_size', c.sample_size,
    'ledger_state', r.state, 'price_version', r.price_version,
    'reserved', r.reserved::text, 'actual', r.actual::text,
    'selected', coalesce(cardinality(r.sample_ids), 0),
    'sent', coalesce(cardinality(r.sent_sample_ids), 0),
    'media', coalesce(cardinality(r.media_ids), 0),
    'accepted', (select count(*) from infrx.lab_judge_results x
                  where x.run_id = q.run_id and x.accepted),
    'rejected', (select count(*) from infrx.lab_judge_results x
                  where x.run_id = q.run_id and not x.accepted),
    'updated_at', coalesce(r.updated_at, q.requested_at),
    'cancel_requested_at', k.requested_at)
    from infrx.lab_judge_requests q
    join infrx.lab_judge_configs c on c.config_id = q.config_id
    left join infrx.lab_judge_runs r on r.run_id = q.run_id
    left join infrx.lab_judge_cancellations k on k.run_id = q.run_id
   where q.run_id = p_run
$$;

create or replace function public.lab_judge_run_list(p_provider_org_id uuid, p_run_id uuid,
  p_after uuid, p_limit int)
returns jsonb language plpgsql stable security definer set search_path = public, infrx, pg_temp as $$
begin
  perform infrx.lab_judge_door(p_provider_org_id, 'developer');
  return (select coalesce(jsonb_agg(infrx.lab_judge_run_doc(x.run_id) order by x.run_id), '[]')
    from (select q.run_id from infrx.lab_judge_requests q
           where q.provider_org_id = p_provider_org_id
             and (p_run_id is null or q.run_id = p_run_id)
             and (p_after is null or q.run_id > p_after)
           order by q.run_id
           limit least(greatest(coalesce(p_limit, 26), 1), 101)) x);
end $$;

create or replace function public.lab_judge_run_results(p_provider_org_id uuid, p_run_id uuid,
  p_after uuid, p_limit int)
returns jsonb language plpgsql stable security definer set search_path = public, infrx, pg_temp as $$
declare
  r infrx.lab_judge_runs%rowtype;
begin
  perform infrx.lab_judge_door(p_provider_org_id, 'developer');
  if not exists (select 1 from infrx.lab_judge_requests q
                  where q.run_id = p_run_id and q.provider_org_id = p_provider_org_id) then
    raise exception 'no such judge run' using errcode = 'P0002';
  end if;
  select * into r from infrx.lab_judge_runs where run_id = p_run_id;
  if not found then
    return '[]';                                   -- queued: nothing reserved, nothing judged
  end if;
  if not infrx.lab_grant_current(r.grant_id, 'external_judging') then
    perform infrx.refuse('forbidden', 'the run''s judging grant is no longer current');
  end if;
  return (select coalesce(jsonb_agg(jsonb_build_object('label_id', x.label_id,
            'sample_id', x.sample_id, 'rubric_version', x.rubric_version,
            'accepted', x.accepted, 'result', x.result, 'recorded_at', x.recorded_at)
            order by x.label_id), '[]')
    from (select * from infrx.lab_judge_results l
           where l.run_id = p_run_id and (p_after is null or l.label_id > p_after)
           order by l.label_id
           limit least(greatest(coalesce(p_limit, 26), 1), 101)) x);
end $$;

create or replace function public.lab_judge_cancel(p_provider_org_id uuid, p_run_id uuid)
returns jsonb language plpgsql security definer set search_path = public, infrx, pg_temp as $$
begin
  perform infrx.lab_judge_door(p_provider_org_id, 'developer');
  if not exists (select 1 from infrx.lab_judge_requests q
                  where q.run_id = p_run_id and q.provider_org_id = p_provider_org_id) then
    raise exception 'no such judge run' using errcode = 'P0002';
  end if;
  insert into infrx.lab_judge_cancellations (run_id, requested_by)
  values (p_run_id, auth.uid()) on conflict (run_id) do nothing;
  if exists (select 1 from infrx.lab_judge_runs r
              where r.run_id = p_run_id and r.state = 'prepared') then
    perform infrx.lab_judge_release(jsonb_build_object('run_id', p_run_id,
      'state', 'cancelled', 'reason', 'cancelled in the Lab API before egress'));
  end if;
  return infrx.lab_judge_run_doc(p_run_id);
end $$;

-- ============================================================ human reviews ===
-- 0038's rule: a CURRENT developer+ member (else not_found / forbidden), and the request's
-- org CURRENTLY sharing `feedback` for `provider_sharing` of the job's model with the provider.
create or replace function infrx.lab_review_request_org(p_provider uuid, p_job uuid)
returns uuid language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  v_org uuid;
begin
  if not exists (select 1 from infrx.provider_memberships m
                  where m.provider_org_id = p_provider and m.user_id = auth.uid()
                    and m.granted_at <= infrx.now()
                    and (m.revoked_at is null or infrx.now() < m.revoked_at)) then
    perform infrx.refuse('not_found', 'no such provider workspace');
  end if;
  if not exists (select 1 from infrx.provider_memberships m
                  where m.provider_org_id = p_provider and m.user_id = auth.uid()
                    and m.granted_at <= infrx.now()
                    and (m.revoked_at is null or infrx.now() < m.revoked_at)
                    and m.role in ('developer', 'administrator')) then
    perform infrx.refuse('forbidden', 'this provider role does not review requests');
  end if;
  select j.org_id into v_org from infrx.jobs j
   where j.request_id = p_job
     and (select g.effective_at <= infrx.now()
                 and (g.revoked_at is null or infrx.now() < g.revoked_at)
                 and (g.expires_at is null or infrx.now() < g.expires_at)
                 and j.model_id::text = any(g.model_ids)
                 and 'feedback' = any(g.categories) and 'provider_sharing' = any(g.purposes)
            from infrx.lab_access_grants g
           where g.grantor_org_id = j.org_id and g.recipient_provider_org_id = p_provider
           order by g.version desc limit 1);
  if v_org is null then
    perform infrx.refuse('not_found', 'no such request shared with this provider');
  end if;
  return v_org;
end $$;

create or replace function infrx.lab_trace_review_json(v infrx.lab_trace_reviews) returns jsonb
language sql stable set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('review_id', v.review_id, 'request_id', v.request_id,
    'reviewer', v.reviewer, 'provenance', v.provenance, 'verdict', v.verdict,
    'comment', v.comment, 'run_id', v.run_id, 'rubric_version', v.rubric_version,
    'created_at', v.created_at)
$$;

create or replace function public.lab_trace_review(p_args jsonb)
returns jsonb language plpgsql security definer set search_path = public, infrx, pg_temp as $$
declare
  v_uuid constant text := '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
  v_provider uuid;
  v_job uuid;
  v infrx.lab_trace_reviews%rowtype;
begin
  if jsonb_typeof(p_args) is distinct from 'object'
     or exists (select 1 from jsonb_object_keys(p_args) k
                where k not in ('provider_org_id', 'request_id', 'review_id', 'input_hash',
                                'verdict', 'comment', 'run_id', 'rubric_version'))
     or coalesce(p_args->>'review_id', '') !~* v_uuid
     or (p_args ? 'run_id' and jsonb_typeof(p_args->'run_id') <> 'null'
         and coalesce(p_args->>'run_id', '') !~* v_uuid) then
    perform infrx.refuse('invalid_request', 'a review is {provider_org_id, request_id, '
                         'review_id, input_hash, verdict, comment?, run_id?, rubric_version?}');
  end if;
  if p_args->>'provider_org_id' ~* v_uuid then
    v_provider := (p_args->>'provider_org_id')::uuid;
  end if;
  if p_args->>'request_id' ~* v_uuid then
    v_job := (p_args->>'request_id')::uuid;
  end if;
  perform infrx.lab_review_request_org(v_provider, v_job);
  if p_args->>'run_id' is not null and not exists (
       select 1 from infrx.lab_judge_requests q
        where q.run_id = (p_args->>'run_id')::uuid and q.provider_org_id = v_provider) then
    perform infrx.refuse('not_found', 'no such judge run');
  end if;
  begin
    insert into infrx.lab_trace_reviews (review_id, provider_org_id, request_id, reviewer,
      verdict, comment, run_id, rubric_version, input_hash)
    values ((p_args->>'review_id')::uuid, v_provider, v_job, auth.uid(), p_args->>'verdict',
      p_args->>'comment', (p_args->>'run_id')::uuid, (p_args->>'rubric_version')::int,
      p_args->>'input_hash')
    on conflict (review_id) do nothing
    returning * into v;
  exception when check_violation or not_null_violation or invalid_text_representation
            or numeric_value_out_of_range then
    perform infrx.refuse('invalid_request', 'a verdict pass|fail|unsure, a comment of '
                         '1..2000 characters, a rubric version 1..1000 and an input hash');
  end;
  if v.review_id is not null then
    return infrx.lab_trace_review_json(v) || jsonb_build_object('replayed', false);
  end if;
  select * into v from infrx.lab_trace_reviews where review_id = (p_args->>'review_id')::uuid;
  if v.provider_org_id <> v_provider or v.reviewer <> auth.uid()
     or v.input_hash <> p_args->>'input_hash' then
    perform infrx.refuse('idempotency_conflict', 'this key already stored another review');
  end if;
  return infrx.lab_trace_review_json(v) || jsonb_build_object('replayed', true);
end $$;

create or replace function public.lab_trace_reviews(p_args jsonb)
returns jsonb language plpgsql stable security definer set search_path = public, infrx, pg_temp as $$
declare
  v_uuid constant text := '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
  v_provider uuid;
  v_job uuid;
begin
  if jsonb_typeof(p_args) is distinct from 'object'
     or exists (select 1 from jsonb_object_keys(p_args) k
                where k not in ('provider_org_id', 'request_id')) then
    perform infrx.refuse('invalid_request', 'a review read is {provider_org_id, request_id}');
  end if;
  if p_args->>'provider_org_id' ~* v_uuid then
    v_provider := (p_args->>'provider_org_id')::uuid;
  end if;
  if p_args->>'request_id' ~* v_uuid then
    v_job := (p_args->>'request_id')::uuid;
  end if;
  perform infrx.lab_review_request_org(v_provider, v_job);
  return (select coalesce(jsonb_agg(infrx.lab_trace_review_json(v)
                                    order by v.created_at, v.review_id), '[]')
            from infrx.lab_trace_reviews v
           where v.provider_org_id = v_provider and v.request_id = v_job);
end $$;

-- ================================================================== privileges ===
revoke all on function infrx.lab_judge_config_json(infrx.lab_judge_configs),
  infrx.lab_judge_run_doc(uuid), infrx.lab_review_request_org(uuid, uuid),
  infrx.lab_trace_review_json(infrx.lab_trace_reviews),
  public.lab_judge_configure_keyed(uuid, uuid, uuid, uuid, text, int, int),
  public.lab_judge_config_list(uuid, uuid, uuid, int),
  public.lab_judge_set_budget_keyed(uuid, text, jsonb, text),
  public.lab_judge_budget_list(uuid),
  public.lab_judge_run_list(uuid, uuid, uuid, int),
  public.lab_judge_run_results(uuid, uuid, uuid, int),
  public.lab_judge_cancel(uuid, uuid),
  public.lab_trace_review(jsonb), public.lab_trace_reviews(jsonb)
  from public, anon, authenticated;
grant execute on function
  public.lab_judge_configure_keyed(uuid, uuid, uuid, uuid, text, int, int),
  public.lab_judge_config_list(uuid, uuid, uuid, int),
  public.lab_judge_set_budget_keyed(uuid, text, jsonb, text),
  public.lab_judge_budget_list(uuid),
  public.lab_judge_run_list(uuid, uuid, uuid, int),
  public.lab_judge_run_results(uuid, uuid, uuid, int),
  public.lab_judge_cancel(uuid, uuid),
  public.lab_trace_review(jsonb), public.lab_trace_reviews(jsonb),
  public.lab_judge_request_run(uuid, uuid, uuid, text),
  public.lab_judge_calibration(uuid, uuid, int),
  public.lab_review_feedback(jsonb)
  to infrx_lab_control;
