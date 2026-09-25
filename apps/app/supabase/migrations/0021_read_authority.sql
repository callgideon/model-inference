-- D10.c: result/consumer reads, the resolved USD price identity, the reconcile race and the
-- dedicated runtime role (consumer-v1/01 §D10.c; RV-06, RV-09, RV-11; P-22 as F2C.c decided
-- it; F2C.b ReadOutcome; S3 F6/F11; 08 §10 R10, R45, R59, R64, R66, R69, R91).
--
--   job_admission (0018's)      the committed outcome carries its PERSISTED
--                               `result_expires_at`, so status, result and idempotent replay
--                               read one instant (F2C.b); nothing recomputes it.
--   resolve_usd_revision        P-22 "resolve, then price": a model string resolves through
--   admit_legacy_usd (0011's)   the catalog listing (0008's grammar and rule) to its canonical
--                               `<alias>@<label>`; `price_versions` is read by THAT, and the
--                               job records the caller's string verbatim in `requested_model`
--                               beside the canonical `model_revision`. A string no listing
--                               names keeps the literal (the pilot's pre-catalog models).
--                               Nothing is rewritten: prior jobs keep their snapshot, the
--                               W7c/W7e rows stay; no balance is converted.
--   reconcile (0018's)          the operation id is serialized FIRST (an advisory lock taken
--                               only here), so two connections replaying one operation id -
--                               for one request or two - answer the audited result or
--                               `idempotency_conflict`; a raw 23505 never escapes.
--   consumer_jobs /             C0/U4's narrow, cursor-paginated reads for the signed-in
--   consumer_job_result         individual (auth.uid() -> their consumer wallet -> its
--                               personal organization; never a caller-named tenant): own jobs,
--                               holds, usage, settled charge in its OWN unit, result
--                               availability by the persisted expiry, and the owned result
--                               while it is available. No ClickHouse, no trace dependency.
--   infrx_runtime               I8's dedicated runtime role: a NOLOGIN role the operator gives
--                               LOGIN and a password out of band, holding exactly the gateway
--                               and worker surface (below). Its statement timeout is a ROLE
--                               default, so it survives a transaction pooler (S3 F6): the
--                               runtime needs no per-session `set role` / `set`.
--
-- LOCK ORDER. `reconcile`: the operation-id lock (taken by nothing else), then 0018's order
-- (job row, hold, wallet). The legacy admission keeps 0011's order exactly. `require_feature`
-- adds a SHARE lock on one `feature_flags` row: taken first by the legacy body, after lock 1
-- by the CREDIT body; a freeze takes only that row (FOR UPDATE), so no cycle exists.
--
-- ROLLBACK (0021 alone). Re-run 0018's `job_admission` and `reconcile` and 0011's
-- `admit_legacy_usd`; restore 0006's `jobs_regime_fixes_provenance` only after no legacy job
-- carries `requested_model` (or leave the wider one - it admits no row the old one refused
-- except that column); drop `resolve_usd_revision`, the consumer functions and (after the
-- runtime logs in as another role) `infrx_runtime`. Jobs admitted meanwhile keep their
-- canonical `model_revision` and snapshot - money history is never rewritten.
--
-- Additive and re-runnable.

-- ================================================== the persisted result expiry ===
create or replace function infrx.job_admission(p_request_id uuid) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object(
    'request_id', j.request_id, 'job_handle', j.job_handle, 'org_id', j.org_id,
    'key_id', j.key_id, 'operation', j.operation, 'idempotency_key', j.idempotency_key,
    'payload_hash', j.idem_payload_hash, 'price_snapshot', j.price_snapshot,
    'maximum_hold', j.maximum_hold::text, 'state', j.state,
    'reservations', coalesce((select jsonb_agg(jsonb_build_object(
        'request_id', r.request_id, 'org_id', r.org_id, 'key_id', r.key_id,
        'kind', r.kind, 'amount', r.amount, 'active', r.active,
        'reserved_at', r.reserved_at) order by array_position(
          array['preparation', 'inference', 'journal_bytes'], r.kind), r.kind)
      from infrx.capacity_reservations r where r.request_id = j.request_id), '[]'),
    'outbox', coalesce((select jsonb_agg(jsonb_build_object(
        'event_id', o.event_id, 'aggregate_id', o.aggregate_id, 'kind', o.kind,
        'version', o.version, 'payload', o.payload, 'available_at', o.available_at)
        order by o.created_at, o.event_id)
      from infrx.outbox o where o.aggregate_id = j.request_id
        and o.kind in ('prepare_dispatch', 'inference_dispatch')), '[]'),
    'admitted_at', j.admitted_at, 'deadline_at', j.deadline_at,
    'budgets', jsonb_build_object('preparation_s', j.budget_preparation_s,
      'queue_wait_s', j.budget_queue_wait_s, 'generation_s', j.budget_generation_s,
      'first_token_s', j.budget_first_token_s, 'stall_s', j.budget_stall_s),
    'preparation_deadline_at', j.preparation_deadline_at,
    'prepared_prompt_tokens', j.prepared_prompt_tokens,
    'queue_deadline_at', j.queue_deadline_at, 'queue_wait_used_s', j.queue_wait_used_s,
    'accounting_regime', j.accounting_regime,
    'wallet_id', j.wallet_id,
    'requested_model', j.requested_model, 'model_revision', j.model_revision,
    'pins', case when j.accounting_regime = 'credit' then jsonb_build_object(
        'model_id', j.model_id, 'requested_model', j.requested_model,
        'deployment_revision_id', j.deployment_revision_id,
        'serving_version_id', j.serving_version_id,
        'rate_card_version', j.rate_card_version, 'policy_version', j.policy_version,
        'accounting_regime', 'credit') end,
    'rate_card', (select jsonb_build_object(
        'rate_card_version', c.rate_card_version, 'unit', c.unit, 'meter', c.meter,
        'model_id', c.model_id, 'deployment_revision_id', c.deployment_revision_id,
        'serving_version_id', c.serving_version_id,
        'input_rate_per_million', c.input_rate_per_million::text,
        'output_rate_per_million', c.output_rate_per_million::text,
        'effective_at', c.effective_at, 'approved_by', c.approved_by,
        'status', c.status, 'hold_rounding', c.hold_rounding,
        'debit_rounding', c.debit_rounding)
      from infrx.rate_card_versions c where c.rate_card_version = j.rate_card_version),
    'outcome', case when j.settled_at is not null then jsonb_build_object(
        'job_id', j.request_id, 'state', j.state, 'cause', j.outcome_cause,
        'usage', infrx.usage_doc(j.usage_prompt_tokens, j.usage_completion_tokens),
        'result_ref', j.result_ref, 'settlement_state', j.settlement_state,
        'debit', j.debit::text, 'settled_at', j.settled_at,
        'reconcile_after', j.reconcile_after,
        'result_expires_at', j.result_expires_at) end,
    'charged_credits', (select (-l.amount)::text from infrx.credit_ledger l
                         where l.request_id = j.request_id and l.kind = 'inference_debit'),
    'replayed', false)
  from infrx.jobs j where j.request_id = p_request_id;
$$;

-- F2C.b rollout (optional -> required): a success carries its persisted expiry. NOT VALID -
-- every new row is checked, no existing row is rewritten or backfilled; the operator runs
-- `alter table infrx.jobs validate constraint jobs_success_has_result_expiry` once
-- `select count(*) from infrx.jobs where state = 'succeeded' and result_expires_at is null`
-- is 0 (such a pre-0018 success reads `unavailable`, fail-closed, until then).
do $$
begin
  if not exists (select 1 from pg_constraint where conrelid = 'infrx.jobs'::regclass
                 and conname = 'jobs_success_has_result_expiry') then
    alter table infrx.jobs add constraint jobs_success_has_result_expiry
      check (state <> 'succeeded' or result_expires_at is not null) not valid;
  end if;
end $$;

-- ======================================================= the regime flags, locked ===
-- G8 (wiring request 1): an admission reads its regime's flag FOR SHARE, so a freeze (an
-- UPDATE of the flag row) waits for every admission that already passed the check, and a
-- drain that then measures zero in-flight jobs is not raced by one still committing.
-- VOLATILE, because a row lock is a write; its callers (0011's `admit_legacy_usd`, 0008's
-- `resolve_admission_pins`, 0006's `jobs_credit_admission_guard`) keep their own
-- volatility: the lock is taken inside this function's own execution.
create or replace function infrx.require_feature(p_name text) returns void
language plpgsql volatile security definer set search_path = infrx, public, pg_temp as $$
begin
  if not coalesce((select f.enabled from infrx.feature_flags f
                   where f.name = p_name for share), false) then
    raise exception 'maintenance: % is not enabled', p_name using errcode = '55000';
  end if;
end $$;

-- ============================================ P-22: resolve, then price (USD) ===
-- A legacy USD job may now record the caller's string (`requested_model`); it still carries
-- no CREDIT pin, and a CREDIT job still carries every pin and no USD price.
do $$
begin
  if not exists (select 1 from pg_constraint where conrelid = 'infrx.jobs'::regclass
                 and conname = 'jobs_regime_fixes_provenance_v2') then
    alter table infrx.jobs
      drop constraint jobs_regime_fixes_provenance,
      add constraint jobs_regime_fixes_provenance_v2 check (case accounting_regime
        when 'legacy_usd' then price_version is not null and price_snapshot is not null
          and num_nulls(wallet_id, model_id, deployment_revision_id, serving_version_id,
                        rate_card_version, policy_version) = 6
        else price_version is null and price_snapshot is null
          and num_nonnulls(wallet_id, model_id, requested_model, deployment_revision_id,
                           serving_version_id, rate_card_version, policy_version) = 7 end);
  end if;
end $$;

-- The canonical revision a model string names: 0008's rule without the CREDIT flag, card or
-- policy - the highest effective listing of the alias (of its label, for a pin), its
-- deployment public and active - as `<alias>@<label>`. A string no listing names, or whose
-- listing is not served publicly, is returned as it came (and prices only if a
-- `price_versions` row carries exactly it).
create or replace function infrx.resolve_usd_revision(p_model text) returns text
language plpgsql stable security definer set search_path = infrx, public, pg_temp as $$
declare
  v_alias text := split_part(p_model, '@', 1);
  v_label text := nullif(split_part(p_model, '@', 2), '');
  v_revision text;
  v_served boolean;
begin
  if p_model is null or p_model !~ '^[^@\s]{1,200}(@[^@\s]{1,64})?$' then
    return p_model;
  end if;
  select v_alias || '@' || s.revision_label, d.state = 'active' and d.visibility = 'public'
    into v_revision, v_served
    from infrx.catalog_listings l
    join infrx.serving_versions s on s.serving_version_id = l.serving_version_id
    join infrx.deployment_revisions d on d.deployment_revision_id = l.deployment_revision_id
   where l.public_model_id = v_alias and l.effective_at <= infrx.now()
     and (v_label is null or s.revision_label = v_label)
   order by l.version desc limit 1;
  return case when v_served then v_revision else p_model end;
end $$;

-- 0011's legacy body; the two changes are marked P-22.
create or replace function infrx.admit_legacy_usd(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  r jsonb := p_args->'request';
  v_idem jsonb := p_args->'idem';
  v_limits jsonb := p_args->'limits';
  v_budgets jsonb := p_args->'budgets';
  v_now timestamptz := infrx.now();
  v_replay jsonb;
  v_deadline timestamptz;
  v_revision text;
  p infrx.price_versions%rowtype;
  v_hold numeric(20,8);
  v_available numeric(20,8);
  v_audience text;
  j infrx.jobs;
begin
  perform infrx.require_feature('legacy_usd_admission');
  v_replay := infrx.admission_replay(v_idem, (v_limits->>'idempotency_ttl_s')::float8, v_now,
                                     'legacy_usd');
  if v_replay is not null then
    return v_replay;
  end if;
  if exists (select 1 from infrx.jobs where request_id = (r->>'request_id')::uuid) then
    perform infrx.refuse('state_conflict', 'request ' || (r->>'request_id')
                         || ' is already an admitted job');
  end if;
  v_deadline := infrx.admission_checks(r, v_limits, v_budgets, r->>'model_revision', v_now);
  select a.audience into v_audience from public.api_keys a where a.id = (r->>'key_id')::uuid;
  if v_audience = 'operator' then
    perform infrx.refuse('forbidden', 'an operator credential does not spend a wallet');
  end if;
  if v_audience <> 'consumer' then
    perform infrx.refuse('not_found', 'model');
  end if;
  -- P-22: the price is the RESOLVED revision's; the request's string never keys it.
  v_revision := infrx.resolve_usd_revision(r->>'model_revision');
  select * into p from infrx.price_versions pv
   where pv.model_revision = v_revision and pv.effective_from <= v_now
     and (pv.effective_to is null or pv.effective_to > v_now)
   order by pv.effective_from desc, pv.captured_at desc limit 1;
  if not found then
    perform infrx.refuse('invalid_request', 'no price snapshot for the requested model');
  end if;
  v_hold := infrx.hold_for((r->>'max_input_tokens')::int, (r->>'max_output_tokens')::int,
                           p.input_rate_per_million, p.output_rate_per_million);
  if v_hold <= 0 then
    perform infrx.refuse('invalid_request', 'a zero USD hold would meter nothing');
  end if;
  select w.available into v_available from infrx.wallets w
   where w.org_id = (r->>'org_id')::uuid for update;
  if v_hold > coalesce(v_available, 0) then
    perform infrx.refuse('insufficient_credit',
                         'maximum hold exceeds available balance for org ' || (r->>'org_id'));
  end if;
  -- P-22: the canonical revision on the job, the caller's string verbatim beside it.
  j := infrx.admission_insert_job(r, v_idem, v_budgets, v_limits, v_deadline, v_now,
    'legacy_usd', v_revision, p.price_version, jsonb_build_object(
      'price_version', p.price_version, 'currency', p.currency,
      'model_revision', p.model_revision,
      'input_rate_per_million', p.input_rate_per_million::text,
      'output_rate_per_million', p.output_rate_per_million::text,
      'token_rules_version', p.token_rules_version, 'captured_at', p.captured_at),
    jsonb_build_object('requested_model', r->>'model_revision'), v_hold);
  update infrx.wallets set reserved_total = reserved_total + v_hold, revision = revision + 1,
                           updated_at = v_now
   where org_id = j.org_id;
  insert into infrx.credit_holds (request_id, org_id, key_id, amount, state, created_at,
                                  updated_at)
  values (j.request_id, j.org_id, j.key_id, v_hold, 'held', v_now, v_now);
  perform infrx.admission_rows(r, v_idem, v_limits, j.job_handle, v_now);
  return infrx.job_admission(j.request_id);
end $$;

-- ================================================ reconcile: one operation id ===
create or replace function infrx.reconcile(p_args jsonb) returns jsonb
language plpgsql security definer set search_path = infrx, public, pg_temp as $$
declare
  v_now timestamptz := infrx.now();
  v_key text := 'reconcile:' || (p_args->>'operation_id');
  j infrx.jobs%rowtype;
  a infrx.audit_entries%rowtype;
  v_before text;
begin
  if coalesce(p_args->>'org_id', '') !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$'
     or coalesce(p_args->>'request_id', '') !~* '^[0-9a-f]{8}-([0-9a-f]{4}-){3}[0-9a-f]{12}$'
     or length(btrim(coalesce(p_args->>'operation_id', ''))) not between 1 and 200
     or length(btrim(coalesce(p_args->>'actor', ''))) not between 1 and 200 then
    perform infrx.refuse('invalid_request', 'reconcile takes {org_id, request_id, '
                         || 'operation_id, actor, at}');
  end if;
  -- D10: one operation id at a time, BEFORE the job row: a replay racing its original on
  -- another request waits here and then finds the audit entry, instead of losing the audit
  -- index to a raw 23505.
  perform pg_advisory_xact_lock(hashtextextended(v_key, 0));
  select * into j from infrx.jobs
   where request_id = (p_args->>'request_id')::uuid and org_id = (p_args->>'org_id')::uuid
   for update;
  if not found then
    perform infrx.refuse('not_found', 'no request ' || (p_args->>'request_id')
                         || ' in organization ' || (p_args->>'org_id'));
  end if;
  select * into a from infrx.audit_entries where idempotency_key = v_key;
  if found then
    if a.after->>'request_id' is distinct from j.request_id::text then
      perform infrx.refuse('idempotency_conflict', 'operation ' || (p_args->>'operation_id')
                           || ' reconciled another request');
    end if;
    return jsonb_build_object('settlement_state', j.settlement_state, 'replayed', true);
  end if;
  if j.usage_certainty is distinct from 'unknown' then
    perform infrx.refuse('state_conflict', 'request ' || j.request_id
                         || ' has no unknown usage to reconcile');
  end if;
  v_before := j.settlement_state;
  if j.settlement_state = 'held_unknown' then
    if v_now < j.reconcile_after then
      perform infrx.refuse('state_conflict', 'unknown usage is held until '
                           || j.reconcile_after::text);
    end if;
    perform infrx.release_aged_unknown(j.request_id, v_now);
  end if;
  insert into infrx.audit_entries (id, at, actor_principal, action, target_org_id, reason,
                                   before, after, idempotency_key)
  values (gen_random_uuid(), v_now, btrim(p_args->>'actor'), 'admin_reconcile', j.org_id,
          'unknown usage reconciled after the 24 h window',
          jsonb_build_object('request_id', j.request_id, 'settlement_state', v_before),
          jsonb_build_object('request_id', j.request_id,
                             'settlement_state', 'released_platform_absorbed',
                             'requested_at', p_args->>'at'), v_key);
  return jsonb_build_object('settlement_state', 'released_platform_absorbed',
                            'replayed', false);
end $$;

-- ===================================================== the consumer read surface ===
-- The signed-in individual's own organization: their consumer wallet's personal
-- organization (R66), from the verified JWT subject - never an argument.
create or replace function public.consumer_org() returns uuid
language sql stable security definer set search_path = public, infrx, pg_temp as $$
  select w.personal_org_id from infrx.credit_wallets w
   where w.owner_user_id = auth.uid() and w.kind = 'consumer';
$$;

-- C0/U4: the individual's jobs, newest first, keyset-paged on the index 0003 built for it
-- (`jobs_org_created_idx`). `p_after` is the previous page's last `cursor`; `p_limit` 1..100.
-- Money leaves as text in its own unit (R59-9, R64): a CREDIT job's hold and settled charge
-- in CREDIT, a legacy job's in USD - never summed, never converted. `result_available` is
-- F2C.b's `available`: a success with a result and authoritative usage, before its
-- PERSISTED expiry, not scrubbed.
create or replace function public.consumer_jobs(p_after text default null,
                                                p_limit int default 50,
                                                p_request_id uuid default null)
returns table (request_id uuid, job_handle text, created_at timestamptz, requested_model text,
               model_revision text, execution_mode text, state text, outcome_cause text,
               accounting_regime text, settlement_state text, usage_certainty text,
               prompt_tokens int, completion_tokens int, unit text, hold text,
               hold_state text, charged text, result_available boolean,
               result_expires_at timestamptz, settled_at timestamptz, cursor text)
language plpgsql stable security definer set search_path = public, infrx, pg_temp as $$
#variable_conflict use_column
declare
  v_org uuid;
  v_at timestamptz;
  v_id uuid;
  v_now timestamptz := infrx.now();
begin
  if auth.uid() is null then
    raise exception 'not signed in' using errcode = '42501';
  end if;
  if p_after is not null then
    begin
      v_at := split_part(p_after, '|', 1)::timestamptz;
      v_id := split_part(p_after, '|', 2)::uuid;
    exception when others then
      raise exception 'invalid_cursor: not a cursor this read issued' using errcode = 'P0001';
    end;
  end if;
  v_org := public.consumer_org();
  return query
  select j.request_id, j.job_handle, j.created_at, coalesce(j.requested_model, j.model_revision),
         j.model_revision, j.execution_mode, j.state, j.outcome_cause, j.accounting_regime,
         j.settlement_state, j.usage_certainty, j.usage_prompt_tokens,
         j.usage_completion_tokens,
         case j.accounting_regime when 'credit' then 'CREDIT' else 'USD' end,
         coalesce(ch.amount, uh.amount)::text, coalesce(ch.state, uh.state),
         case when j.settlement_state = 'settled' then case j.accounting_regime
           when 'credit' then (select (-l.amount)::text from infrx.credit_ledger l
                                where l.request_id = j.request_id
                                  and l.kind = 'inference_debit')
           else j.debit::text end end,
         j.state = 'succeeded' and j.result_ref is not null
           and j.usage_prompt_tokens is not null and j.result_expires_at is not null
           and v_now < j.result_expires_at
           and not exists (select 1 from infrx.job_results x where x.request_id = j.request_id
                            and x.scrubbed_at is not null),
         j.result_expires_at, j.settled_at, j.created_at::text || '|' || j.request_id::text
    from infrx.jobs j
    left join infrx.credit_wallet_holds ch on ch.request_id = j.request_id
    left join infrx.credit_holds uh on uh.request_id = j.request_id
   where v_org is not null and j.org_id = v_org
     and (p_request_id is null or j.request_id = p_request_id)
     and (v_at is null or j.created_at < v_at or (j.created_at = v_at and j.request_id > v_id))
   order by j.created_at desc, j.request_id
   limit greatest(1, least(coalesce(p_limit, 50), 100));
end $$;

-- U4: the owned result, while it is available - the persisted expiry decides (0020's
-- `read_result`: pending, expired, scrubbed and another tenant's are refused typed).
create or replace function public.consumer_job_result(p_request_id uuid) returns text
language plpgsql stable security definer set search_path = public, infrx, pg_temp as $$
declare
  v_org uuid;
begin
  if auth.uid() is null then
    raise exception 'not signed in' using errcode = '42501';
  end if;
  v_org := public.consumer_org();
  if v_org is null or not exists (select 1 from infrx.jobs j where j.request_id = p_request_id
                                   and j.org_id = v_org and j.result_ref is not null) then
    perform infrx.refuse('not_found', 'no result for request ' || coalesce(p_request_id::text,
                                                                            ''));
  end if;
  return infrx.read_result(v_org, 'infrx-result:' || p_request_id);
end $$;

-- R59-4: Supabase's default ACL hands anon/authenticated EXECUTE on a new public function;
-- revoke that first, then grant exactly the signed-in read.
revoke all on function public.consumer_org() from public, anon, authenticated, service_role;
revoke all on function public.consumer_jobs(text, integer, uuid)
  from public, anon, authenticated, service_role;
revoke all on function public.consumer_job_result(uuid)
  from public, anon, authenticated, service_role;
-- (service_role as for every console read; with no JWT subject it is refused 42501.)
grant execute on function public.consumer_jobs(text, integer, uuid)
  to authenticated, service_role;
grant execute on function public.consumer_job_result(uuid) to authenticated, service_role;
revoke all on function infrx.resolve_usd_revision(text)
  from public, anon, authenticated, service_role;

-- G7 WR-3a: the USD price discovery publishes - the row `admit_legacy_usd` would capture now
-- for this model string (P-22: resolve, then price; same predicate). Null when unpriced.
create or replace function infrx.usd_price(p_model text) returns jsonb
language sql stable security definer set search_path = infrx, public, pg_temp as $$
  select jsonb_build_object('price_version', pv.price_version, 'currency', pv.currency,
           'model_revision', pv.model_revision,
           'input_rate_per_million', pv.input_rate_per_million::text,
           'output_rate_per_million', pv.output_rate_per_million::text,
           'token_rules_version', pv.token_rules_version, 'captured_at', infrx.now())
  from infrx.price_versions pv
  where pv.model_revision = infrx.resolve_usd_revision(p_model)
    and pv.effective_from <= infrx.now()
    and (pv.effective_to is null or pv.effective_to > infrx.now())
  order by pv.effective_from desc, pv.captured_at desc limit 1;
$$;
revoke all on function infrx.usd_price(text) from public, anon, authenticated;
grant execute on function infrx.usd_price(text) to service_role;

-- The browser's key scope, re-asserted (06a AuthContextV2; RV-06): a browser session
-- names, renames and revokes its keys; it never writes a key's audience, individual,
-- provider or endpoint - those are 0009's service defaults. New columns do not widen it.
revoke insert (audience, user_id, provider_org_id, endpoint_id),
       update (audience, user_id, provider_org_id, endpoint_id)
  on public.api_keys from anon, authenticated;

-- ============================================================ the runtime roles ===
-- I8 (RV-09, WR-I8-6): the gateway and worker's own login instead of the project's broad
-- one, and a read-only monitor login. Both are NOLOGIN here; the operator runs
-- `alter role <role> login password '<secret>'` from the secret store (never a file) and
-- points DATABASE_URL / MONITOR_DATABASE_URL at them. Neither is a member of any role,
-- neither is superuser, BYPASSRLS, CREATEROLE, CREATEDB or REPLICATION (I8's
-- privilege_probe identity checks); their statement bounds are ROLE defaults, so they hold
-- on the transaction pooler with no session SET (S3 F6, R110 as I8 proposes it).
--
-- infrx_runtime reaches exactly: the boundary functions below, direct reads of the
-- relations its adapters read (with a row-level policy for it alone - RLS stays on, with
-- no browser policy), PgAttachments' two inserts, and the one column UPDATE a
-- `select ... for update` of a job row needs (0003's `jobs_guard` stamps `updated_at`). No
-- operator operation, no money writer outside the settlement boundary, no DDL.
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'infrx_runtime') then
    create role infrx_runtime nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'infrx_monitor') then
    create role infrx_monitor nologin noinherit;
  end if;
end $$;
alter role infrx_runtime set statement_timeout = '15s';       -- DATABASE_POOL_STATEMENT_TIMEOUT_MS
alter role infrx_runtime set idle_in_transaction_session_timeout = '30s';
alter role infrx_monitor set statement_timeout = '10s';
alter role infrx_monitor set default_transaction_read_only = on;
grant usage on schema infrx, public to infrx_runtime, infrx_monitor;
do $$
declare
  f text;
  t text;
begin
  foreach f in array array[
      -- admission, replay and readiness (gateway)
      'infrx.admit(jsonb)', 'infrx.admit_ready(jsonb)', 'infrx.idempotency_lookup(jsonb)',
      'infrx.job_admission(uuid)', 'infrx.readiness_doc(uuid)', 'infrx.now()',
      'infrx.usd_price(text)',
      -- uploads and the content lifecycle (gateway, collector)
      'infrx.upload_create(jsonb)', 'infrx.upload_acknowledge_put(jsonb)',
      'infrx.upload_complete(jsonb)', 'infrx.upload_abort(jsonb)',
      'infrx.upload_resolve(jsonb)', 'infrx.upload_expire(jsonb)',
      'infrx.content_register(jsonb)', 'infrx.content_references(jsonb)',
      'infrx.content_candidates(jsonb)', 'infrx.content_claim(jsonb)',
      'infrx.content_tombstone(jsonb)', 'infrx.content_acknowledge_delete(jsonb)',
      -- preparation, dispatch and execution (worker, relay)
      'infrx.claim_preparation(jsonb)', 'infrx.claim_preparation_ready(jsonb)',
      'infrx.prepare(jsonb)', 'infrx.dispatch_pending(jsonb)',
      'infrx.acknowledge_dispatch(jsonb)', 'infrx.dispatch_snapshot()',
      'infrx.reopen_dispatch(jsonb)', 'infrx.release_dispatch(jsonb)',
      'infrx.fail_dispatch(jsonb)', 'infrx.gc_outbox(jsonb)', 'infrx.claim(jsonb)',
      'infrx.heartbeat(jsonb)', 'infrx.load_work(jsonb)', 'infrx.load_work_credit(jsonb)',
      'infrx.cancel(jsonb)', 'infrx.terminalize(jsonb)', 'infrx.recover(jsonb)',
      -- results and the journal
      'infrx.put_result(jsonb)', 'infrx.read_result(uuid, text)', 'infrx.append(jsonb)',
      'infrx.read_journal(jsonb)', 'infrx.expire_journal(jsonb)', 'infrx.journal_usage()']
  loop
    execute format('grant execute on function %s to infrx_runtime', f);
  end loop;
  -- The runtime's direct reads (PgJobStore owned lookups and `is_live`, PgStreamStore,
  -- PgCatalogDirectory, PgAttachments, PgLifecycle's readiness read).
  foreach t in array array[
      'infrx.jobs', 'infrx.stream_chunks', 'infrx.staged_media', 'infrx.job_media',
      'infrx.job_readiness', 'infrx.content_objects', 'infrx.catalog_listings',
      'infrx.serving_versions', 'infrx.model_versions', 'infrx.deployment_revisions',
      'infrx.rate_card_versions', 'infrx.data_access_policies', 'infrx.endpoints',
      'infrx.provider_orgs', 'public.models']
  loop
    execute format('grant select on %s to infrx_runtime', t);
    execute format('drop policy if exists runtime_reads on %s', t);
    execute format('create policy runtime_reads on %s for select to infrx_runtime '
                   'using (true)', t);
  end loop;
  foreach t in array array['infrx.staged_media', 'infrx.job_media'] loop
    execute format('grant insert on %s to infrx_runtime', t);
    execute format('drop policy if exists runtime_attaches on %s', t);
    execute format('create policy runtime_attaches on %s for insert to infrx_runtime '
                   'with check (true)', t);
  end loop;
end $$;
grant update (updated_at) on infrx.jobs to infrx_runtime;
drop policy if exists runtime_locks on infrx.jobs;
create policy runtime_locks on infrx.jobs for update to infrx_runtime using (true);

-- infrx_monitor: I8's durable gauges (`infra/observe/durable.py`), exactly the columns its
-- queries name, read-only by role default, and the two reconciliation views.
grant select (request_id, state, admitted_at, queued_at, updated_at, deadline_at, settled_at,
              outcome_cause, result_expires_at) on infrx.jobs to infrx_monitor;
grant select (kind, acknowledged_at, claimed_at, available_at) on infrx.outbox
  to infrx_monitor;
grant select (request_id, state, reconcile_after) on infrx.credit_holds to infrx_monitor;
grant select (expires_at) on infrx.stream_chunks to infrx_monitor;
grant select (request_id) on infrx.job_results to infrx_monitor;
grant select on infrx.wallet_reconciliation, infrx.credit_wallet_reconciliation
  to infrx_monitor;
do $$
declare
  t text;
begin
  foreach t in array array['infrx.jobs', 'infrx.outbox', 'infrx.credit_holds',
                           'infrx.stream_chunks', 'infrx.job_results'] loop
    execute format('drop policy if exists monitor_reads on %s', t);
    execute format('create policy monitor_reads on %s for select to infrx_monitor '
                   'using (true)', t);
  end loop;
end $$;
comment on role infrx_runtime is
  'D10 (0021) for I8: the gateway/worker runtime login. LOGIN and password are set by the '
  'operator out of band; statement_timeout is a role default (transaction-pooler safe).';
comment on role infrx_monitor is
  'D10 (0021) for I8: the read-only durable-gauge login (infra/observe/durable.py).';
